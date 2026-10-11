"""End-to-end regression suite for HLayout, driven entirely through its
MCP server.

Every test starts its own ``python -m hlayout.mcpserver --headless``
process and talks to it over stdio in JSON-RPC, exactly as Claude does,
so this exercises the protocol layer, the seven tools and the scripting
/ command-bar layers behind them in one go. ``--headless`` means a
running HLayout window is never touched.

    python tests/mcp_regression.py              # run everything
    python tests/mcp_regression.py -k mask      # only tests matching "mask"
    python tests/mcp_regression.py --list       # list tests, run nothing
    python tests/mcp_regression.py --json r.json  # also write a JSON report

Exit status is 0 when nothing FAILed. Outcomes:

    PASS   as expected
    FAIL   a regression
    SKIP   an optional dependency is missing (shapely; Tkinter for the
           command bar)
    XFAIL  a known defect, listed so it is not forgotten; turns into
           XPASS once fixed (then drop the ``known=`` marker)

Not covered, because they need the GUI's canvas and selection: MOVE,
ROTATE, COPY, PROPS, SMASH, UNION, MAKECELL, ZOOM, FIT, GRID. PRINT is
left out on purpose (it hands the page to the OS print flow);
Session.print_cell(), which only writes the file, is covered instead.
"""
import argparse
import glob
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPLY_TIMEOUT = 120
TOOL_NAMES = ["hlayout_status", "hlayout_open", "hlayout_save", "hlayout_command",
              "hlayout_python", "hlayout_api", "hlayout_describe_cell"]


class Skip(Exception):
    pass


class Server:
    """One headless MCP server process plus a minimal JSON-RPC client."""

    def __init__(self, *extra_args, handshake=True):
        env = dict(os.environ)
        env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
        env.pop("HLAYOUT_MCP", None)
        self._err = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "hlayout.mcpserver", "--headless", *extra_args],
            cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._err)
        self._lines = queue.Queue()
        threading.Thread(target=self._pump, daemon=True).start()
        self._id = 0
        if handshake:
            self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "mcp_regression", "version": "1"}})
            self.notify("notifications/initialized")
            # The first tool reply of a session is prefixed with a note
            # saying which mode it is in; take it here so tests see clean output.
            self.first_reply = self.ok("hlayout_status")

    def _pump(self):
        for line in iter(self.proc.stdout.readline, b""):
            self._lines.put(line)
        self._lines.put(None)

    def send_raw(self, data: bytes):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def read(self, timeout=REPLY_TIMEOUT):
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            raise AssertionError(f"no reply from the server within {timeout}s")
        if line is None:
            self._err.seek(0)
            tail = self._err.read().decode("utf-8", "replace")[-800:]
            raise AssertionError(f"server exited unexpectedly; stderr tail:\n{tail}")
        return json.loads(line)

    def notify(self, method, params=None):
        self.send_raw((json.dumps({"jsonrpc": "2.0", "method": method,
                                   "params": params or {}}) + "\n").encode())

    def rpc(self, method, params=None):
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw((json.dumps(msg) + "\n").encode())
        reply = self.read()
        assert reply.get("id") == self._id, f"reply id mismatch: {reply}"
        return reply

    # --- tools -----------------------------------------------------------
    def tool(self, _tool, **args):
        reply = self.rpc("tools/call", {"name": _tool, "arguments": args})
        assert "result" in reply, f"{_tool}: protocol error {reply.get('error')}"
        res = reply["result"]
        return res["content"][0]["text"], bool(res.get("isError"))

    def ok(self, _tool, **args):
        text, is_err = self.tool(_tool, **args)
        assert not is_err, f"{_tool} failed: {text.strip()[-600:]}"
        return text

    def err(self, _tool, _contains=None, **args):
        text, is_err = self.tool(_tool, **args)
        assert is_err, f"{_tool} should have failed but returned: {text.strip()[:300]}"
        if _contains:
            assert _contains.lower() in text.lower(), \
                f"{_tool}: expected {_contains!r} in error, got: {text.strip()[-400:]}"
        return text

    def py(self, code):
        return self.ok("hlayout_python", code=code)

    def py_err(self, code, contains=None):
        return self.err("hlayout_python", contains, code=code)

    def ev(self, expr):
        """Evaluate an expression in the server and bring its value back as JSON."""
        out = self.py(f"import json as _j\nprint('@@' + _j.dumps(({expr}), default=str))")
        return json.loads(out[out.rindex("@@") + 2:])

    @staticmethod
    def _json(text):
        return json.loads(text[text.index("{"):])

    def cmd(self, text):
        return self.ok("hlayout_command", text=text)

    def status(self):
        return self._json(self.ok("hlayout_status"))

    def describe(self, **args):
        return self._json(self.ok("hlayout_describe_cell", **args))

    def layer_counts(self, **args):
        return {l["layer"]: l["shapes"] for l in self.describe(**args)["layers"]}

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        finally:
            self._err.close()


class Ctx:
    """What a test gets: fresh servers on demand and a scratch folder."""

    def __init__(self, tmp, caps):
        self.tmp = tmp
        self.caps = caps
        self._servers = []

    def server(self, *args, **kw):
        srv = Server(*args, **kw)
        self._servers.append(srv)
        return srv

    def path(self, name):
        return os.path.join(self.tmp, name)

    def cleanup(self):
        for srv in self._servers:
            srv.close()


TESTS = []


def test(group, needs=(), known=None):
    def deco(fn):
        TESTS.append({"group": group, "name": fn.__name__, "fn": fn,
                      "needs": tuple(needs), "known": known,
                      "doc": (fn.__doc__ or "").strip()})
        return fn
    return deco


def approx(a, b, tol=1):
    return all(abs(x - y) <= tol for x, y in zip(a, b)) and len(a) == len(b)


# ======================================================================
# protocol
# ======================================================================
@test("protocol")
def initialize_reports_server_and_echoes_version(c):
    s = c.server(handshake=False)
    r = s.rpc("initialize", {"protocolVersion": "2024-11-05"})["result"]
    assert r["serverInfo"]["name"] == "hlayout" and r["serverInfo"]["version"]
    assert r["protocolVersion"] == "2024-11-05"
    assert "tools" in r["capabilities"] and "hlayout_status" in r["instructions"]


@test("protocol")
def initialize_falls_back_on_unknown_version(c):
    s = c.server(handshake=False)
    r = s.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert r["protocolVersion"] == "2025-06-18"


@test("protocol")
def tools_list_is_the_seven_tools_with_schemas(c):
    tools = c.server().rpc("tools/list")["result"]["tools"]
    assert [t["name"] for t in tools] == TOOL_NAMES
    for t in tools:
        assert t["description"] and t["inputSchema"]["type"] == "object"
    req = {t["name"]: t["inputSchema"]["required"] for t in tools}
    assert req["hlayout_open"] == ["path"] and req["hlayout_python"] == ["code"]
    assert req["hlayout_command"] == ["text"] and req["hlayout_status"] == []


@test("protocol")
def ping_unknown_method_and_unknown_tool(c):
    s = c.server()
    assert s.rpc("ping")["result"] == {}
    assert s.rpc("no/such/method")["error"]["code"] == -32601
    assert s.rpc("tools/call", {"name": "hlayout_nope", "arguments": {}})["error"]["code"] == -32602


@test("protocol")
def parse_error_does_not_kill_the_server(c):
    s = c.server()
    s.send_raw(b"this is not json\n")
    assert s.read()["error"]["code"] == -32700
    s.send_raw(b"\n   \n")  # blank lines are ignored
    assert s.rpc("ping")["result"] == {}


@test("protocol")
def notifications_get_no_reply(c):
    s = c.server()
    s.notify("notifications/cancelled", {"requestId": 99})
    assert s.rpc("ping")["result"] == {}  # the very next line is the ping's reply


@test("protocol")
def batch_request_returns_a_batch(c):
    s = c.server()
    batch = [{"jsonrpc": "2.0", "id": "a", "method": "ping"},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": "b", "method": "tools/list"}]
    s.send_raw((json.dumps(batch) + "\n").encode())
    replies = s.read()
    assert isinstance(replies, list) and [r["id"] for r in replies] == ["a", "b"]


@test("protocol")
def non_object_request_is_invalid(c):
    s = c.server()
    s.send_raw(b'"hello"\n')
    assert s.read()["error"]["code"] == -32600


@test("protocol")
def design_flag_opens_a_file_at_start(c):
    a = c.server()
    a.py("s.new_cell('A'); s.rect(1, 0, 0, 10, 20)")
    a.ok("hlayout_save", path=c.path("start.gds"))
    b = c.server("--design", c.path("start.gds"))
    st = b.status()
    assert st["cells"] == ["A"] and st["file"] == c.path("start.gds")


# ======================================================================
# status / describe / api tools
# ======================================================================
@test("tools")
def status_of_a_fresh_headless_session(c):
    st = c.server().status()
    assert st["mode"] == "headless" and st["library"] == "HLIB"
    assert st["file"] is None and st["current_cell"] is None
    assert st["cell_count"] == 0 and st["cells"] == [] and st["top_cells"] == []
    assert st["can_undo"] is False and st["can_redo"] is False
    assert "1 DBU = 1 micron" in st["units"]
    assert isinstance(st["command_bar_available"], bool)


@test("tools")
def first_reply_says_which_mode_it_is_in_and_later_ones_do_not(c):
    s = c.server()
    assert s.first_reply.startswith("[headless session]\n{"), s.first_reply[:60]
    assert s.ok("hlayout_status").startswith("{")
    s2 = c.server(handshake=False)
    s2.rpc("initialize", {})
    assert s2.err("hlayout_python", code="1/0").startswith("[headless session]")  # errors carry it too


@test("tools")
def status_tracks_cells_top_cells_layers_and_undo(c):
    s = c.server()
    s.py("s.new_cell('LEAF'); s.rect(1, 0, 0, 10, 10)\n"
         "s.new_cell('TOP'); s.use_cell('TOP'); s.sref('LEAF', (0, 0))\n"
         "s.set_layer_style(7, name='Seven')")
    st = s.status()
    assert st["current_cell"] == "TOP" and st["cell_count"] == 2
    assert sorted(st["cells"]) == ["LEAF", "TOP"] and st["top_cells"] == ["TOP"]
    assert st["named_layers"].get("7") == "Seven" and st["can_undo"] is True


@test("tools")
def describe_cell_reports_layers_instances_and_size(c):
    s = c.server()
    s.py("s.new_cell('LEAF'); s.rect(1, 0, 0, 100, 50)\n"
         "s.new_cell('TOP'); s.use_cell('TOP')\n"
         "s.rect(2, 0, 0, 1000, 2000); s.path(2, [(0, 0), (500, 0)], 20)\n"
         "s.sref('LEAF', (5000, 0)); s.aref('LEAF', (0, 5000), 3, 2, 200, 100)")
    d = s.describe()
    assert d["cell"] == "TOP" and d["own_shape_count"] == 2
    assert d["layers"] == [{"layer": 2, "name": d["layers"][0]["name"],
                            "shapes": {"Rectangle": 1, "Path": 1}}]
    assert d["sref_count"] == 1 and d["aref_count"] == 1
    assert d["bbox_dbu"][2] == 5100, d["bbox_dbu"]  # hierarchical: reaches the sref
    assert approx(d["size_mm"], [d["bbox_dbu"][2] / 1000 - d["bbox_dbu"][0] / 1000,
                                 d["bbox_dbu"][3] / 1000 - d["bbox_dbu"][1] / 1000], 0.001)
    assert s.describe(name="LEAF")["bbox_dbu"] == [0, 0, 100, 50]


@test("tools")
def describe_cell_errors_and_max_items(c):
    s = c.server()
    s.err("hlayout_describe_cell", "no current cell")
    s.py("s.new_cell('L'); s.rect(1, 0, 0, 1, 1); s.new_cell('T'); s.use_cell('T')\n"
         "for i in range(12): s.sref('L', (i * 10, 0))")
    s.err("hlayout_describe_cell", name="MISSING")
    d = s.describe(max_items=5)
    assert d["sref_count"] == 12 and len(d["srefs"]) <= 6


@test("tools")
def api_listing_detail_and_commands(c):
    s = c.server()
    listing = s.ok("hlayout_api")
    for name in ("rect(", "mask_op(", "run_drc(", "place_cell(", "extract_netlist("):
        assert name in listing, name
    assert "_checkpoint" not in listing
    assert "Design Rule Check" in s.ok("hlayout_api", name="run_drc")
    s.err("hlayout_api", name="_checkpoint")
    s.err("hlayout_api", name="no_such_method")
    if c.caps["tk"]:
        assert "MASKOP" in s.ok("hlayout_api", name="commands")


# ======================================================================
# hlayout_python
# ======================================================================
@test("python")
def expression_value_print_and_persistent_variables(c):
    s = c.server()
    assert s.py("1 + 2").strip() == "3"
    assert "hello" in s.py("x = 41\nprint('hello')")
    assert s.py("x + 1").strip() == "42"
    assert s.py("s is hl").strip() == "True"
    assert s.py("library is s.library").strip() == "True"
    assert s.py("all(k in globals() for k in ('Cell', 'Polygon', 'Path', 'Rectangle', 'SRef', 'Text'))").strip() == "True"


@test("python")
def errors_are_reported_and_the_session_survives(c):
    s = c.server()
    s.py_err("1/0", "ZeroDivisionError")
    s.py_err("def (", "SyntaxError")
    s.err("hlayout_python", code="")
    s.err("hlayout_python")
    s.py_err("s.cell", "no current cell")
    assert s.py("2 * 21").strip() == "42"


@test("python")
def stdout_from_scripts_never_corrupts_the_protocol(c):
    s = c.server()
    s.py("import sys\nprint('to stdout')\nsys.stdout.write('{\"jsonrpc\": \"fake\"}\\n')")
    assert s.rpc("ping")["result"] == {}


# ======================================================================
# geometry
# ======================================================================
@test("geometry")
def every_shape_type_and_bbox(c):
    s = c.server()
    s.py("s.new_cell('T')\n"
         "s.rect(1, 0, 0, 100, 50)\n"
         "s.polygon(2, [(0, 0), (50, 0), (25, 40)])\n"
         "s.path(3, [(0, 0), (500, 0), (500, 500)], 20, cap='round')\n"
         "s.circle(4, (1000, 1000), 250)\n"
         "s.text(5, (0, 0), 'R1', height=100)")
    assert s.layer_counts() == {1: {"Rectangle": 1}, 2: {"Polygon": 1}, 3: {"Path": 1},
                                4: {"Polygon": 1}, 5: {"Text": 1}}
    assert s.ev("s.bbox('T')")[2:] == [1250, 1250]
    assert s.ev("[type(x).__name__ for x in s.cell.shapes]") == \
        ["Rectangle", "Polygon", "Path", "Polygon", "Text"]


@test("geometry")
def rectangle_corners_are_normalised(c):
    s = c.server()
    s.py("s.new_cell('T'); s.rect(1, 100, 50, 0, 0)")
    assert s.ev("s.bbox('T')") == [0, 0, 100, 50]


@test("geometry")
def circle_segment_counts(c):
    s = c.server()
    s.py("s.new_cell('T')\nfor n in (8, 16, 32, 64): s.circle(1, (0, 0), 100, segments=n)")
    assert s.ev("[len(x.points) for x in s.cell.shapes]") == [8, 16, 32, 64]
    assert s.ev("s.bbox('T')") == [-100, -100, 100, 100]
    s.py_err("s.circle(1, (0, 0), 100, segments=7)", "segments")


@test("geometry")
def path_caps(c):
    s = c.server()
    s.py("s.new_cell('T')\nfor cap in ('flush', 'round', 'square'): s.path(1, [(0, 0), (100, 0)], 10, cap=cap)")
    assert s.ev("[x.cap.value for x in s.cell.shapes]") == ["flush", "round", "square"]
    s.py_err("s.path(1, [(0, 0), (100, 0)], 10, cap='pointy')")


@test("geometry")
def flush_path_bbox_stops_at_its_ends(c):
    s = c.server()
    s.py("s.new_cell('T'); s.path(1, [(0, 0), (1000, 0)], 100, cap='flush')")
    assert s.ev("s.bbox('T')") == [0, -50, 1000, 50]


@test("geometry")
def square_path_bbox_includes_the_cap(c):
    s = c.server()
    s.py("s.new_cell('T'); s.path(1, [(0, 0), (1000, 0)], 100, cap='square')")
    assert approx(s.ev("s.bbox('T')"), [-50, -50, 1050, 50])


@test("geometry")
def invalid_geometry_is_rejected(c):
    s = c.server()
    s.py("s.new_cell('T')")
    s.py_err("s.polygon(1, [(0, 0), (1, 1)])", "at least 3")
    s.py_err("s.polygon(1, [(i, i * i) for i in range(201)])", "max is 200")
    s.py_err("s.rect(64, 0, 0, 1, 1)", "out of range")
    s.py_err("s.rect(-1, 0, 0, 1, 1)")
    s.py_err("s.rect('NoSuchLayer', 0, 0, 1, 1)", "no layer named")
    assert s.describe()["own_shape_count"] == 0


@test("geometry")
def polygon_at_the_200_point_limit(c):
    s = c.server()
    s.py("import math\ns.new_cell('T')\n"
         "s.polygon(1, [(round(1000 * math.cos(2 * math.pi * i / 200)), round(1000 * math.sin(2 * math.pi * i / 200))) for i in range(200)])")
    assert s.ev("len(s.cell.shapes[0].points)") == 200


@test("geometry")
def remove_and_shapes_on_layer(c):
    s = c.server()
    s.py("s.new_cell('T'); a = s.rect(1, 0, 0, 1, 1); b = s.rect(1, 5, 5, 6, 6); s.rect(2, 0, 0, 1, 1)")
    assert s.ev("len(s.shapes_on_layer(1))") == 2
    s.py("s.remove(a)")
    assert s.ev("len(s.shapes_on_layer(1))") == 1 and s.describe()["own_shape_count"] == 2


@test("geometry")
def bulk_2000_shapes_in_one_batch(c):
    s = c.server()
    t0 = time.time()
    s.py("s.new_cell('BULK')\nwith s.batch():\n"
         "    for i in range(1000):\n"
         "        s.rect(2, 0, i * 600, 100000, i * 600 + 300)\n"
         "        s.path(4, [(105000, i * 600 + 150), (205000, i * 600 + 150)], 300)")
    took = time.time() - t0
    assert s.layer_counts() == {2: {"Rectangle": 1000}, 4: {"Path": 1000}}
    s.py("s.undo()")  # the whole batch is one undo step
    assert s.describe()["own_shape_count"] == 0
    assert took < 30, f"2000 shapes took {took:.1f}s"


# ======================================================================
# cells and instances
# ======================================================================
@test("cells")
def new_cell_use_cell_and_errors(c):
    s = c.server()
    s.py("s.new_cell('A'); s.new_cell('B')")
    assert s.status()["current_cell"] in ("A", "B")
    s.py("s.use_cell('A')")
    assert s.status()["current_cell"] == "A"
    s.py_err("s.new_cell('A')", "already exists")
    s.py_err("s.use_cell('NOPE')", "no such cell")


@test("cells")
def sref_transforms_show_in_the_bbox(c):
    s = c.server()
    s.py("s.new_cell('L'); s.rect(1, 0, 0, 100, 50); s.new_cell('T'); s.use_cell('T')\n"
         "s.sref('L', (1000, 0))")
    assert approx(s.ev("s.bbox('T')"), [1000, 0, 1100, 50])
    s.py("s.undo(); s.sref('L', (0, 0), rotation=90)")
    assert approx(s.ev("s.bbox('T')"), [-50, 0, 0, 100])
    s.py("s.undo(); s.sref('L', (0, 0), magnification=2)")
    assert approx(s.ev("s.bbox('T')"), [0, 0, 200, 100])
    s.py("s.undo(); s.sref('L', (0, 0), x_reflection=True)")
    assert approx(s.ev("s.bbox('T')"), [0, -50, 100, 0])


@test("cells")
def aref_grid_and_smash(c):
    s = c.server()
    s.py("s.new_cell('L'); s.rect(1, 0, 0, 100, 50); s.new_cell('T'); s.use_cell('T')\n"
         "a = s.aref('L', (0, 0), 4, 3, 500, 300)")
    assert approx(s.ev("s.bbox('T')"), [0, 0, 1600, 650])
    s.py("s.smash_aref(a)")
    assert s.ev("[len(s.cell.arefs), len(s.cell.srefs)]") == [0, 12]
    assert approx(s.ev("s.bbox('T')"), [0, 0, 1600, 650])
    assert s.ev("sorted({tuple(r.origin) for r in s.cell.srefs})[-1]") == [1500, 600]


@test("cells")
def find_cells_list_library_and_place_cell(c):
    s = c.server()
    s.py("s.new_cell('PAD_0402'); s.rect(1, 0, 0, 10, 10); s.new_cell('PAD_0603'); s.new_cell('TOP'); s.use_cell('TOP')")
    assert s.ev("sorted(x['name'] for x in s.find_cells('pad'))") == ["PAD_0402", "PAD_0603"]
    assert s.ev("s.find_cells('zzz')") == []
    lib = {x["name"]: x for x in s.ev("s.list_library()")}
    assert lib["PAD_0402"]["shapes"] == 1 and lib["PAD_0402"]["bbox"] == [0, 0, 10, 10]
    s.py("s.place_cell('PAD_0402', (100, 100), label='U1', pin='3')")
    assert s.ev("[(r.cell_name, list(r.origin), r.label, r.pin) for r in s.cell.srefs]") == \
        [["PAD_0402", [100, 100], "U1", "3"]]
    s.py_err("s.place_cell('NOPE', (0, 0))")


@test("cells")
def save_refuses_a_dangling_sref(c):
    """sref() itself accepts a missing target; the check is at save."""
    s = c.server()
    s.py("s.new_cell('T'); s.sref('NOPE', (0, 0))")
    s.err("hlayout_save", "NOPE", path=c.path("dangling.gds"))
    assert not os.path.exists(c.path("dangling.gds"))


@test("cells")
def cyclic_references_are_refused(c):
    s = c.server()
    s.py("s.new_cell('A'); s.new_cell('B'); s.new_cell('C')\n"
         "s.use_cell('A'); s.sref('B', (0, 0)); s.use_cell('B'); s.sref('C', (0, 0)); s.use_cell('C')")
    s.py_err("s.sref('A', (0, 0))", "cyclic")            # A > B > C > A
    s.py_err("s.sref('C', (0, 0))", "cyclic")            # directly inside itself
    s.py_err("s.aref('A', (0, 0), 2, 2, 10, 10)", "cyclic")
    assert s.describe()["sref_count"] == 0 and s.describe()["aref_count"] == 0


# ======================================================================
# undo / redo
# ======================================================================
@test("undo")
def undo_redo_single_steps(c):
    s = c.server()
    s.py("s.new_cell('T'); s.rect(1, 0, 0, 1, 1); s.rect(1, 2, 2, 3, 3)")
    assert s.ev("[s.undo(), len(s.cell.shapes), s.can_redo()]") == [True, 1, True]
    assert s.ev("[s.redo(), len(s.cell.shapes), s.can_redo()]") == [True, 2, False]
    assert s.ev("s.redo()") is False


@test("undo")
def a_new_edit_clears_the_redo_stack(c):
    s = c.server()
    s.py("s.new_cell('T'); s.rect(1, 0, 0, 1, 1); s.undo(); s.rect(2, 0, 0, 1, 1)")
    st = s.status()
    assert st["can_redo"] is False and st["can_undo"] is True
    assert s.layer_counts() == {2: {"Rectangle": 1}}


@test("undo")
def undo_everything_back_to_empty(c):
    s = c.server()
    s.py("s.new_cell('T')\nfor i in range(5): s.rect(1, i, i, i + 1, i + 1)")
    s.py("while s.can_undo(): s.undo()")
    assert s.status()["cell_count"] == 0


# ======================================================================
# layers
# ======================================================================
@test("layers")
def named_layers_work_everywhere_a_layer_is_taken(c):
    s = c.server()
    s.py("s.new_cell('T'); s.set_layer_style(9, name='Nine', color='#ff0000', visible=False)\n"
         "s.rect('Nine', 0, 0, 5, 5); s.path('Nine', [(0, 0), (9, 0)], 2)")
    assert s.status()["named_layers"]["9"] == "Nine"
    d = s.describe()
    assert d["layers"] == [{"layer": 9, "name": "Nine", "shapes": {"Rectangle": 1, "Path": 1}}]
    assert s.ev("len(s.shapes_on_layer('Nine'))") == 2


@test("layers")
def remap_and_clear_layer(c):
    s = c.server()
    s.py("s.new_cell('A'); s.rect(3, 0, 0, 1, 1); s.rect(3, 2, 2, 3, 3); s.rect(4, 0, 0, 1, 1)\n"
         "s.new_cell('B'); s.use_cell('B'); s.rect(3, 0, 0, 1, 1)")
    assert s.ev("s.remap_layers({3: 7})") == {"3": 3}
    assert s.layer_counts(name="A") == {4: {"Rectangle": 1}, 7: {"Rectangle": 2}}
    assert s.ev("s.remap_layers({3: 7})") == {}
    assert s.ev("s.remap_layers({4: 8}, ['A'])") == {"4": 1}
    assert s.ev("s.clear_layer(7)") == {"B": 1}  # current cell only
    assert s.ev("s.clear_layer(7, ['A'])") == {"A": 2}
    assert s.layer_counts(name="A") == {8: {"Rectangle": 1}}


# ======================================================================
# pins
# ======================================================================
@test("pins")
def pin_list_and_set(c):
    s = c.server()
    s.py("s.new_cell('T'); s.rect(1, 0, 0, 10, 10, pin='1'); s.path(1, [(0, 0), (9, 0)], 2)\n"
         "s.polygon(1, [(20, 0), (30, 0), (25, 9)])")
    pins = s.ev("s.list_pins()")
    assert [(p["index"], p["pin"]) for p in pins] == [(0, "1"), (2, "")]
    s.py("s.set_pin(2, 'A2')")
    assert s.ev("[p['pin'] for p in s.list_pins()]") == ["1", "A2"]
    assert [p for p in s.describe()["pins"]], "describe_cell lists numbered pins"
    s.py_err("s.set_pin(1, 'X')")   # a Path can't carry a pin
    s.py_err("s.set_pin(99, 'X')")


# ======================================================================
# files
# ======================================================================
FIXTURE = (
    "s.new_cell('LEAF'); s.rect(1, 0, 0, 100, 50, pin='1'); s.polygon(2, [(0, 0), (50, 0), (25, 40)])\n"
    "s.new_cell('TOP'); s.use_cell('TOP')\n"
    "s.rect(1, -10, -20, 30, 40); s.path(3, [(0, 0), (500, 0), (500, 500)], 20, cap='round')\n"
    "s.path(3, [(0, 100), (500, 100)], 30, cap='flush'); s.circle(4, (1000, 1000), 250, segments=16)\n"
    "s.text(5, (10, 20), 'HELLO', height=120)\n"
    "s.sref('LEAF', (2000, 0), rotation=90, magnification=2, x_reflection=True)\n"
    "s.aref('LEAF', (0, 3000), 3, 2, 400, 300)\n"
)
DUMP = (
    "{n: {'shapes': sorted(repr(x) for x in library.cells[n].shapes),"
    " 'srefs': [(r.cell_name, tuple(r.origin), r.rotation, r.magnification, r.x_reflection) for r in library.cells[n].srefs],"
    " 'arefs': len(library.cells[n].arefs), 'bbox': s.bbox(n)} for n in sorted(library.cells)}"
)


@test("files")
def gds_round_trip_preserves_everything(c):
    a = c.server()
    a.py(FIXTURE)
    before = a.ev(DUMP)
    a.ok("hlayout_save", path=c.path("rt.gds"))
    assert os.path.getsize(c.path("rt.gds")) > 0
    b = c.server()
    b.ok("hlayout_open", path=c.path("rt.gds"))
    assert b.ev(DUMP) == before
    st = b.status()
    assert st["file"] == c.path("rt.gds") and st["top_cells"] == ["TOP"]


@test("files")
def gds_round_trip_keeps_shape_order(c):
    a = c.server()
    a.py("s.new_cell('T'); s.path(3, [(0, 0), (9, 0)], 2); s.rect(1, 0, 0, 5, 5, pin='1')\n"
         "s.text(5, (0, 0), 'X', height=10); s.rect(1, 9, 9, 12, 12, pin='2')")
    order = "[type(x).__name__ for x in s.cell.shapes]"
    before = a.ev(order)
    a.ok("hlayout_save", path=c.path("order.gds"))
    b = c.server("--design", c.path("order.gds"))
    b.py("s.use_cell('T')")
    assert b.ev(order) == before


@test("files")
def save_writes_a_sidecar_that_restores_layer_names(c):
    a = c.server()
    a.py("s.new_cell('T'); s.set_layer_style(12, name='100 Ohms'); s.rect(12, 0, 0, 1, 1)")
    a.ok("hlayout_save", path=c.path("side.gds"))
    assert glob.glob(c.path("side*hlaux*")), os.listdir(c.tmp)
    b = c.server()
    b.ok("hlayout_open", path=c.path("side.gds"))
    assert b.status()["named_layers"].get("12") == "100 Ohms"


@test("files")
def save_defaults_to_the_open_file_and_needs_a_path_otherwise(c):
    s = c.server()
    s.err("hlayout_save", "no file yet")
    s.py("s.new_cell('T'); s.rect(1, 0, 0, 1, 1)")
    s.ok("hlayout_save", path=c.path("d.gds"))
    s.py("s.rect(1, 5, 5, 6, 6)")
    s.ok("hlayout_save")
    b = c.server("--design", c.path("d.gds"))
    assert b.describe(name="T")["own_shape_count"] == 2


@test("files")
def open_errors_and_replaces_the_document(c):
    s = c.server()
    s.err("hlayout_open")
    s.err("hlayout_open", path=c.path("missing.gds"))
    with open(c.path("junk.gds"), "wb") as f:
        f.write(b"not a gds file at all")
    s.err("hlayout_open", path=c.path("junk.gds"))
    s.py("s.new_cell('KEEP')")
    assert s.status()["cells"] == ["KEEP"]  # failed opens left the document alone
    a = c.server()
    a.py("s.new_cell('OTHER'); s.rect(1, 0, 0, 1, 1)")
    a.ok("hlayout_save", path=c.path("other.gds"))
    s.ok("hlayout_open", path=c.path("other.gds"))
    st = s.status()
    assert st["cells"] == ["OTHER"] and st["can_undo"] is False
    assert s.py("library is s.library").strip() == "True"


@test("files")
def plain_save_with_a_library_name(c):
    s = c.server()
    s.py(f"s.new_cell('T'); s.rect(1, 0, 0, 1, 1); s.save({c.path('named.gds')!r}, 'PARTS')")
    b = c.server()
    b.ok("hlayout_open", path=c.path("named.gds"))
    assert b.status()["library"] == "PARTS"


@test("files")
def merge_gds_renames_clashing_cells(c):
    s = c.server()
    s.py(f"s.new_cell('A'); s.rect(1, 0, 0, 1, 1); s.new_cell('SHARED'); s.save({c.path('a.gds')!r})")
    t = c.server()
    t.py(f"s.new_cell('B'); s.new_cell('SHARED'); s.save({c.path('b.gds')!r})")
    r = s.ev(f"s.merge_gds({c.path('a.gds')!r}, {c.path('b.gds')!r}, {c.path('m.gds')!r})")
    assert r["cells"] == 4 and list(r["renamed"]) == ["SHARED"]
    assert s.status()["cell_count"] == 2  # the open design is untouched
    m = c.server("--design", c.path("m.gds"))
    assert set(m.status()["cells"]) == {"A", "B", "SHARED", r["renamed"]["SHARED"]}


@test("files")
def technology_file_round_trip(c):
    a = c.server()
    a.py(f"s.new_cell('T'); s.set_layer_style(21, name='Glaze', color='#00ff00'); s.save_technology({c.path('t.json')!r})")
    with open(c.path("t.json"), encoding="utf-8") as f:
        json.load(f)  # valid JSON
    b = c.server()
    b.py(f"s.new_cell('T'); s.load_technology({c.path('t.json')!r})")
    assert b.status()["named_layers"].get("21") == "Glaze"
    b.py("s.rect('Glaze', 0, 0, 1, 1)")
    b.py_err(f"s.load_technology({c.path('nope.json')!r})")


# ======================================================================
# attached libraries, via / pad / chip cells
# ======================================================================
def _parts_library(c, s):
    lib = c.server()
    lib.py(f"s.new_cell('SOT-23'); s.rect(1, 0, 0, 100, 50); s.new_cell('SUB'); s.rect(2, 0, 0, 5, 5)\n"
           f"s.new_cell('ASSY'); s.use_cell('ASSY'); s.sref('SUB', (0, 0)); s.save({c.path('parts.gds')!r})")
    s.py(f"s.new_cell('TOP'); s.attach_library({c.path('parts.gds')!r})")


@test("libraries")
def attach_list_place_and_detach(c):
    s = c.server()
    _parts_library(c, s)
    libs = s.ev("s.list_attached_libraries()")
    assert len(libs) == 1 and libs[0]["index"] == 0
    assert {x["name"] for x in s.ev("s.list_library(0)")} == {"SOT-23", "SUB", "ASSY"}
    assert len(s.status()["attached_libraries"]) == 1
    assert {"source": 0, "name": "SOT-23"} in s.ev("s.find_cells('sot')")
    assert s.status()["cells"] == ["TOP"]  # attached, not merged
    s.py("s.place_cell('ASSY', (0, 0))")
    assert set(s.status()["cells"]) == {"TOP", "ASSY", "SUB"}  # copied in with its dependency
    s.py("s.detach_library(0)")
    assert s.ev("s.list_attached_libraries()") == []
    s.py_err("s.detach_library(0)", "no attached library")
    assert set(s.status()["cells"]) == {"TOP", "ASSY", "SUB"}


@test("libraries")
def at_most_two_attached_libraries(c):
    s = c.server()
    _parts_library(c, s)
    shutil.copy(c.path("parts.gds"), c.path("parts2.gds"))
    shutil.copy(c.path("parts.gds"), c.path("parts3.gds"))
    s.py(f"s.attach_library({c.path('parts2.gds')!r})")
    s.py_err(f"s.attach_library({c.path('parts3.gds')!r})")
    s.py_err(f"s.attach_library({c.path('missing.gds')!r})")
    s.py_err("s.place_cell('SOT-23', (0, 0))")          # ambiguous across the two
    s.py("s.place_cell('SOT-23', (0, 0), source=1)")


@test("libraries")
def attached_libraries_survive_save_and_open(c):
    s = c.server()
    _parts_library(c, s)
    s.ok("hlayout_save", path=c.path("design.gds"))
    b = c.server()
    b.ok("hlayout_open", path=c.path("design.gds"))
    assert len(b.ev("s.list_attached_libraries()")) == 1


@test("libraries")
def via_and_pad_cells_are_written_into_the_attached_library(c):
    s = c.server()
    s.py_err("s.new_cell('X'); s.create_via_cell(2, 4)", "no attached library")
    _parts_library(c, s)
    s.py("s.create_via_cell(2, 4, size=200, index=0, name='VIA_A')\n"
         "s.create_pad_cell('PAD2', 2, 30, 500, 300, 50, cols=2, pitch_x=800, index=0)")
    names = {x["name"]: x for x in s.ev("s.list_library(0)")}
    assert "VIA_A" in names and "PAD2" in names, sorted(names)
    assert approx(names["VIA_A"]["bbox"], [-100, -100, 100, 100])
    assert approx(names["PAD2"]["bbox"], [-650, -150, 650, 150])
    s.py_err("s.create_via_cell(2, 4, index=0, name='VIA_A')")       # exists, no overwrite
    s.py("s.create_via_cell(2, 4, size=300, index=0, name='VIA_A', overwrite=True)")
    s.py_err("s.create_pad_cell('BAD', 2, 30, 500, 300, 400, index=0)")   # margin too big
    s.py_err("s.create_pad_cell('BAD', 2, 30, 500, 300, 50, cols=2, index=0)")  # pitch_x missing
    # ... and saved to the library's own file
    chk = c.server("--design", c.path("parts.gds"))
    assert {"VIA_A", "PAD2"} <= set(chk.status()["cells"])
    pins = sorted(p["pin"] for p in chk.ev("s.list_pins('PAD2')") if p["pin"])
    assert pins == ["1", "2"], pins   # a 2-pad grid is auto-numbered


@test("libraries")
def drop_via_and_chip_cell(c):
    s = c.server()
    s.py("s.new_cell('T'); s.drop_via(100, 100, 2, 4, size=100)")
    assert set(s.layer_counts()) == {2, 4} and s.describe()["own_shape_count"] == 2
    assert approx(s.ev("s.bbox('T')"), [50, 50, 150, 150])
    s.py("s.drop_via(1000, 0, 2, 4, size=100, shape='round', dielectric_layer=3, dielectric_window=150)")
    assert 3 in s.layer_counts()
    assert s.ev("s.create_chip_cell('DIE', 2000, 1500, 51, 1800, 1300, 22)") == "DIE"
    assert s.status()["current_cell"] == "DIE"
    assert set(s.layer_counts()) == {22, 51}
    s.py_err("s.create_chip_cell('DIE', 2000, 1500, 51, 1800, 1300, 22)")
    s.py_err("s.create_chip_cell('DIE2', 0, 1500, 51, 1800, 1300, 22)")


# ======================================================================
# generators
# ======================================================================
RES = ("s.generate_resistor(%d, 0, 500, 1000, resistor_layer=10, conductor_layer=2, glaze_layer=21,"
       " overlap_a=100, pad_b=300, space_c=100, glaze_oversize=100, ref_des='R1')")


@test("generators")
def thick_film_resistor(c):
    s = c.server()
    s.py("s.new_cell('T')")
    assert s.ev(RES % 0) == "R1"
    assert s.ev("[(r.cell_name, r.label) for r in s.cell.srefs]") == [["R1", "R1"]]
    layers = s.layer_counts(name="R1")
    assert {2, 10, 21} <= set(layers) and sum(layers[2].values()) == 2
    b = s.ev("s.bbox('R1')")
    w, h = b[2] - b[0], b[3] - b[1]
    assert max(w, h) >= 1000 and min(w, h) >= 500
    assert s.ev(RES % 5000) != "R1"          # second cell is de-duplicated
    assert len(s.ev("[r.cell_name for r in s.cell.srefs]")) == 2


@test("generators")
def pin_function_table(c):
    s = c.server()
    s.py("s.new_cell('T')")
    texts = "[x.text for x in library.cells['PT'].shapes if type(x).__name__ == 'Text']"
    assert s.ev("s.generate_pin_function_table('PT', 55, 20, functions={1: 'VCC', 20: 'GND'})") == "PT"
    assert {"VCC", "GND", "1", "20", "PIN FUNCTION TABLE"} <= set(s.ev(texts))
    s.py("s.generate_pin_function_table('PT', 55, 30)")            # overwrite defaults on
    assert "30" in s.ev(texts)
    s.py_err("s.generate_pin_function_table('PT', 55, 25)")        # not a multiple of 10
    s.py_err("s.generate_pin_function_table('PT', 55, 20, overwrite=False)")


@test("generators")
def drawing_sheet_sizes(c):
    s = c.server()
    s.py("s.new_cell('T')")
    for size, orient, w, h in (("A4", "portrait", 210, 297), ("A3", "landscape", 420, 297)):
        s.py(f"s.generate_drawing_sheet('SH', 60, size={size!r}, orientation={orient!r}, title='TEST BOARD')")
        b = s.ev("s.bbox('SH')")
        got = ((b[2] - b[0]) / 1000, (b[3] - b[1]) / 1000)
        assert abs(got[0] - (w - 20)) < 1 and abs(got[1] - (h - 20)) < 1, (size, got)
    assert any("TEST BOARD" in t for t in
               s.ev("[x.text for x in library.cells['SH'].shapes if type(x).__name__ == 'Text']"))
    s.py_err("s.generate_drawing_sheet('SH2', 60, size='A0')")


@test("generators")
def resistor_table_needs_saved_rows(c):
    s = c.server()
    s.py("s.new_cell('T')")
    s.py_err("s.generate_resistor_table('RT', 55)", "no resistor rows")
    assert s.status()["cells"] == ["T"]


# ======================================================================
# shapely-backed: mask ops, DRC, netlist, dielectric, routing
# ======================================================================
AREA = "sum(__import__('shapely.geometry', fromlist=['Polygon']).Polygon(x.points).area for x in s.shapes_on_layer(%d))"
TWO_SQUARES = "s.new_cell('T'); s.rect(1, 0, 0, 1000, 1000); s.rect(2, 500, 0, 1500, 1000)"


@test("maskops", needs=["shapely"])
def oversize_and_undersize(c):
    s = c.server()
    s.py("s.new_cell('T'); s.rect(1, 0, 0, 1000, 1000)")
    assert s.ev("s.mask_op('OVERSIZE', 1, 10, amount=100)") == {"T": 1}
    assert s.ev("s.mask_op('UNDERSIZE', 1, 11, amount=100)") == {"T": 1}
    assert s.ev(AREA % 10) == 1200 * 1200 and s.ev(AREA % 11) == 800 * 800
    assert s.ev("s.mask_op('UNDERSIZE', 1, 12, amount=600)") in ({}, {"T": 0})  # shrinks away
    assert s.ev("len(s.shapes_on_layer(1))") == 1  # input left alone


@test("maskops", needs=["shapely"])
def boolean_ops_areas(c):
    s = c.server()
    s.py(TWO_SQUARES)
    s.py("s.mask_op('AND', 1, 10, layer_b=2); s.mask_op('OR', 1, 11, layer_b=2)\n"
         "s.mask_op('NOT', 1, 12, layer_b=2); s.mask_op('XOR', 1, 13, layer_b=2)")
    assert [s.ev(AREA % n) for n in (10, 11, 12, 13)] == [500e3, 1500e3, 500e3, 1000e3]


@test("maskops", needs=["shapely"])
def mask_op_errors_paths_and_flatten(c):
    s = c.server()
    s.py("s.new_cell('L'); s.rect(1, 0, 0, 100, 100); s.new_cell('T'); s.use_cell('T')\n"
         "s.sref('L', (0, 0)); s.path(3, [(0, 0), (1000, 0)], 100, cap='flush')")
    s.py_err("s.mask_op('BOGUS', 1, 10)", "unknown mask op")
    s.py_err("s.mask_op('AND', 1, 10)")                      # layer_b required
    assert s.ev("s.mask_op('OVERSIZE', 1, 10, amount=10)") == {"T": 1}   # sees into the sref
    assert s.ev("s.mask_op('OVERSIZE', 1, 11, amount=10, flatten=False)") in ({}, {"T": 0})
    s.py("s.mask_op('OVERSIZE', 3, 12, amount=0)")           # a path becomes its outline
    assert s.ev(AREA % 12) == 100000
    s.py("s.undo()")
    assert 12 not in s.layer_counts()


@test("maskops", needs=["shapely"])
def outline_conversion_and_top_view(c):
    s = c.server()
    s.py("s.new_cell('T'); s.rect(50, 0, 0, 5000, 5000); s.rect(2, 1000, 1000, 2000, 2000)")
    assert s.ev("s.convert_rects_to_outline(50, 100)") == {"T": 1}
    assert s.layer_counts()[50] == {"Path": 1}
    s.py("s.mask_op_top_view(57)")
    assert 57 not in s.layer_counts()                      # not in the viewed cell
    assert set(s.layer_counts(name="TOPVIEW")[57]) == {"Path"}


@test("drc", needs=["shapely"])
def width_space_and_area_rules(c):
    s = c.server()
    s.py("s.new_cell('T'); s.rect(5, 0, 0, 50, 1000); s.rect(5, 70, 0, 500, 1000)")
    n = lambda rules: s.ev(f"len(s.run_drc({rules!r}))")
    assert n([{"kind": "MIN_WIDTH", "layer_a": 5, "value": 100}]) == 1
    assert n([{"kind": "MIN_WIDTH", "layer_a": 5, "value": 40}]) == 0
    assert n([{"kind": "MIN_SPACE", "layer_a": 5, "value": 50}]) == 1
    assert n([{"kind": "MIN_SPACE", "layer_a": 5, "value": 10}]) == 0
    assert n([{"kind": "MIN_AREA", "layer_a": 5, "value": 100000}]) == 1
    s.py_err("s.run_drc([{'kind': 'NOPE', 'layer_a': 5, 'value': 1}])")


@test("drc", needs=["shapely"])
def between_layer_rules_descend_into_instances_and_mark_errors(c):
    s = c.server()
    s.py("s.new_cell('PART'); s.rect(5, 0, 0, 50, 1000)\n"
         "s.new_cell('T'); s.use_cell('T'); s.sref('PART', (0, 0)); s.rect(6, 70, 0, 500, 1000)\n"
         "s.rect(7, -5, -5, 55, 1005)")
    n = lambda rules, extra="": s.ev(f"len(s.run_drc({rules!r}{extra}))")
    assert n([{"kind": "MIN_WIDTH", "layer_a": 5, "value": 100}]) == 1           # inside the sref
    assert n([{"kind": "MIN_SPACE_BETWEEN", "layer_a": 5, "layer_b": 6, "value": 50}]) == 1
    assert n([{"kind": "MIN_SPACE_BETWEEN", "layer_a": 5, "layer_b": 6, "value": 10}]) == 0
    assert n([{"kind": "MIN_ENCLOSURE", "layer_a": 7, "layer_b": 5, "value": 20}]) >= 1
    assert n([{"kind": "MIN_ENCLOSURE", "layer_a": 7, "layer_b": 5, "value": 5}]) == 0
    assert n([{"kind": "MIN_WIDTH", "layer_a": 5, "value": 100}], ", error_layer=57") == 1
    assert 57 in s.layer_counts()


NETLIST_FIXTURE = (
    "s.new_cell('PADC'); s.rect(2, -100, -100, 100, 100, pin='1')\n"
    "s.new_cell('TOP'); s.use_cell('TOP')\n"
    "s.place_cell('PADC', (0, 0), label='U1'); s.place_cell('PADC', (1000, 0), label='U2')\n"
    "s.place_cell('PADC', (0, 5000), label='U3')\n"
)
NETS = "sorted(sorted(q.ref_des + '.' + q.pin for q in n.pins) for n in s.extract_netlist([2]%s) if n.pins)"


@test("netlist", needs=["shapely"])
def nets_follow_touching_copper(c):
    s = c.server()
    s.py(NETLIST_FIXTURE)
    assert s.ev(NETS % "") == [["U1.1"], ["U2.1"], ["U3.1"]]
    s.py("p = s.path(2, [(0, 0), (1000, 0)], 40, cap='round')")
    assert s.ev(NETS % "") == [["U1.1", "U2.1"], ["U3.1"]]
    s.py(f"s.export_netlist_csv(s.extract_netlist([2]), {c.path('n.csv')!r})")
    with open(c.path("n.csv")) as f:
        rows = [r.split(",") for r in f.read().split()]
    assert rows[0] == ["net", "ref_des", "pin"] and len(rows) == 4
    assert len({r[0] for r in rows[1:] if r[1] in ("U1", "U2")}) == 1


@test("bonds")
def wire_bond_rules_find_move_delete(c):
    s = c.server()
    kw = "layer=40, width=25, pad=100, min_length=100, max_length=5000"
    s.py(f"s.new_cell('T'); s.wire_bond([(0, 0), (2000, 0)], {kw})")
    assert s.layer_counts() == {40: {"Path": 1, "Polygon": 2}}
    s.py_err(f"s.wire_bond([(1000, -500), (1000, 500)], {kw})", "cross")
    s.py_err(f"s.wire_bond([(0, 3000), (50, 3000)], {kw})", "shorter than the minimum")
    s.py_err(f"s.wire_bond([(0, 3000), (9000, 3000)], {kw})")
    s.py(f"s.wire_bond([(0, 1000), (2000, 1000)], {kw})")
    assert s.ev("[list(p) for p in s.find_wire_bond((1000, 0), layer=40)[0].points]") == [[0, 0], [2000, 0]]
    s.py("s.move_wire_bond((2000, 0), (2500, 300), layer=40)")
    assert s.ev("[list(p) for p in s.find_wire_bond((1200, 100), layer=40)[0].points]") == [[0, 0], [2500, 300]]
    s.py("s.delete_wire_bond((1200, 100), layer=40)")
    assert s.layer_counts() == {40: {"Path": 1, "Polygon": 2}}
    s.py_err("s.delete_wire_bond((90000, 90000), layer=40, tol=10)", "no wire bond near")
    s.py("s.undo()")
    assert s.layer_counts() == {40: {"Path": 2, "Polygon": 4}}


@test("bonds")
def two_bonds_may_share_an_end(c):
    s = c.server()
    kw = "layer=40, width=25, pad=100, min_length=100, max_length=5000"
    s.py(f"s.new_cell('T'); s.wire_bond([(0, 0), (2000, 0)], {kw}); s.wire_bond([(0, 0), (0, 2000)], {kw})")
    assert s.layer_counts()[40]["Path"] == 2


@test("bonds")
def deleting_a_bond_leaves_a_neighbour_sharing_its_end_intact(c):
    s = c.server()
    kw = "layer=40, width=25, pad=100, min_length=100, max_length=5000"
    s.py(f"s.new_cell('T'); s.wire_bond([(0, 0), (2000, 0)], {kw}); s.wire_bond([(0, 0), (0, 2000)], {kw})\n"
         "s.delete_wire_bond((1000, 0), layer=40)")
    assert s.ev("len(s.find_wire_bond((0, 1000), layer=40)[1])") == 2


@test("dielectric", needs=["shapely"])
def fill_with_holes_and_clear(c):
    s = c.server()
    s.py("s.new_cell('T'); s.rect(3, 0, 0, 5000, 5000); s.rect(3, 1000, 1000, 1500, 1500)")
    assert s.ev("s.generate_dielectric(layer=3, clearance=50)") >= 1
    fill = ("sum(__import__('shapely.geometry', fromlist=['Polygon']).Polygon(x.points).area"
            " for x in s.cell.shapes if x.layer == 3 and x.datatype == 1)")
    assert s.ev(fill) == 5000 * 5000 - 600 * 600
    assert s.ev("len([x for x in s.cell.shapes if x.datatype == 0])") == 2   # inputs kept
    assert s.ev("s.generate_dielectric(layer=3, clearance=0)") >= 1          # re-runs cleanly
    assert s.ev(fill) == 5000 * 5000 - 500 * 500
    assert s.ev("s.clear_dielectric(layer=3)") >= 1
    assert s.ev("len(s.cell.shapes)") == 2


@test("routing", needs=["shapely"])
def auto_route_dodges_an_obstacle(c):
    s = c.server()
    s.py("s.new_cell('T'); s.rect(2, 400, -500, 600, 500)\n"
         "r = s.auto_route((0, 0), (1000, 0), layer=2, grid=100, width=20)")
    pts = s.ev("[list(p) for p in r.points]")
    assert pts[0] == [0, 0] and pts[-1] == [1000, 0] and len(pts) > 2
    assert all(a[0] == b[0] or a[1] == b[1] for a, b in zip(pts, pts[1:])), "orthogonal"
    hit = ("__import__('shapely.geometry', fromlist=['LineString']).LineString(r.points).buffer(10)"
           ".intersects(__import__('shapely.geometry', fromlist=['box']).box(400, -500, 600, 500))")
    assert s.ev(hit) is False
    s.py("s.undo()")
    assert s.describe()["own_shape_count"] == 1


@test("routing", needs=["shapely"])
def auto_route_reports_when_boxed_in(c):
    s = c.server()
    s.py("s.new_cell('T'); s.rect(2, 2000, -9000, 2300, 9000)")     # a wall right across the search area
    s.py_err("s.auto_route((0, 0), (5000, 0), layer=2, grid=100, width=20)", "no clear route")
    s.py_err("s.auto_route((0, 0), (50, 0), layer=2, grid=100, width=20)", "nothing to route")
    s.py_err("s.auto_route((0, 0), (5000, 0), layer=2, grid=0, width=20)", "grid")
    assert s.describe()["own_shape_count"] == 1


@test("routing", needs=["shapely"])
def auto_route_does_not_cross_copper_next_to_an_endpoint(c):
    s = c.server()
    s.py("s.new_cell('T'); s.rect(2, 200, -3000, 300, 3000)\n"       # foreign trace 0.2 mm from the start
         "r = s.auto_route((0, 0), (2000, 0), layer=2, grid=100, width=20, margin=5000)")
    hit = ("__import__('shapely.geometry', fromlist=['LineString']).LineString(r.points).buffer(10)"
           ".intersects(__import__('shapely.geometry', fromlist=['box']).box(200, -3000, 300, 3000))")
    assert s.ev(hit) is False


# ======================================================================
# exports
# ======================================================================
EXPORT_FIXTURE = ("s.new_cell('L'); s.rect(2, 0, 0, 500, 300); s.new_cell('T'); s.use_cell('T')\n"
                  "s.sref('L', (0, 0)); s.rect(50, -1000, -1000, 2000, 2000); s.path(4, [(0, 0), (900, 0)], 50)\n"
                  "s.text(55, (0, 0), 'ASSY', height=200)\n")


@test("exports")
def gerber_one_file_per_layer(c):
    s = c.server()
    s.py(EXPORT_FIXTURE)
    written, skipped = s.ev(f"s.export_gerber({c.path('gb')!r})")
    assert [w[0] for w in written] == [2, 4, 50] and skipped == 1   # text skipped
    for _, path in written:
        with open(path) as f:
            body = f.read()
        assert "%FS" in body and "M02*" in body, path
    only, _ = s.ev(f"s.export_gerber({c.path('gb2')!r}, layers=[4])")
    assert [w[0] for w in only] == [4]


@test("exports", needs=["shapely"])
def dxf_freecad_and_print(c):
    s = c.server()
    s.py(EXPORT_FIXTURE)
    layers, entities = s.ev(f"s.export_dxf({c.path('o.dxf')!r})")
    assert set(layers) == {2, 4, 50, 55} and entities >= 4
    with open(c.path("o.dxf")) as f:
        dxf = f.read()
    assert "ENTITIES" in dxf and "ASSY" in dxf and dxf.rstrip().endswith("EOF")
    assert s.ev(f"s.export_freecad({c.path('o.FCMacro')!r})") == c.path("o.FCMacro")
    with open(c.path("o.FCMacro"), encoding="utf-8") as f:
        assert "FreeCAD" in f.read()
    assert s.ev(f"s.print_cell(out_path={c.path('p.html')!r})") == c.path("p.html")
    with open(c.path("p.html"), encoding="utf-8") as f:
        html = f.read()
    assert "<svg" in html and "</html>" in html.lower()


# ======================================================================
# command bar (hlayout_command)
# ======================================================================
@test("commands", needs=["tk"])
def drawing_commands_and_case_insensitivity(c):
    s = c.server()
    s.cmd('CELL TOP\nRECT 0 0,0 100,50\npoly 1 0,0 50,0 25,40\nPATH 2 5 round 0,0 50,0 50,50\n'
          'CIRCLE 3 0,0 25 16\nTEXT 4 0,0 "R 1" 10\nRect 0 200,200 300,300')
    assert s.layer_counts() == {0: {"Rectangle": 2}, 1: {"Polygon": 1}, 2: {"Path": 1},
                                3: {"Polygon": 1}, 4: {"Text": 1}}
    assert s.ev("s.cell.shapes[4].text") == "R 1"
    assert s.ev("s.bbox('TOP')")[2:] == [300, 300]


@test("commands", needs=["tk"])
def space_separated_points_are_accepted(c):
    s = c.server()
    s.cmd("CELL TOP\nRECT 0 200 200 300 300\nPOLY 1 0 0 50 0 25 40\nCIRCLE 3 0 0 25")
    assert s.describe()["own_shape_count"] == 3


@test("commands", needs=["tk"])
def instances_delete_last_undo_redo(c):
    s = c.server()
    s.cmd("CELL LEAF\nRECT 0 0,0 100,50\nCELL TOP\nSREF LEAF 200,0 90")
    assert s.describe()["sref_count"] == 1
    s.cmd("DELETE LAST")
    assert s.describe()["sref_count"] == 0
    s.cmd("SREF LEAF 200,0 90\nAREF LEAF 0,1000 4 2 500 300")
    d = s.describe()
    assert d["sref_count"] == 1 and d["aref_count"] == 1
    s.cmd("UNDO\nUNDO")
    d = s.describe()
    assert d["sref_count"] == 0 and d["aref_count"] == 0
    s.cmd("REDO")
    assert s.describe()["sref_count"] == 1
    s.err("hlayout_command", text="SREF")
    s.err("hlayout_command", text="SREF LEAF")


@test("commands", needs=["tk"])
def delete_last_can_be_undone(c):
    s = c.server()
    s.cmd("CELL TOP\nRECT 1 0,0 5,5\nDELETE LAST\nUNDO")
    assert s.describe()["own_shape_count"] == 1


@test("commands", needs=["tk"])
def delete_last_removes_the_most_recent_item(c):
    s = c.server()
    s.cmd("CELL LEAF\nRECT 0 0,0 100,50\nCELL TOP\nRECT 1 0,0 5,5\nSREF LEAF 200,0\nAREF LEAF 0,1000 4 2 500 300\nDELETE LAST")
    count = lambda: tuple(s.describe()[k] for k in ("own_shape_count", "sref_count", "aref_count"))
    assert count() == (1, 1, 0)
    s.cmd("DELETE LAST")
    assert count() == (1, 0, 0)
    s.cmd("DELETE LAST")
    assert count() == (0, 0, 0)
    assert "nothing" in s.cmd("DELETE LAST")
    s.cmd("UNDO\nUNDO\nUNDO")
    assert count() == (1, 1, 1)


@test("commands", needs=["tk"])
def a_failing_line_stops_the_rest(c):
    s = c.server()
    text = s.err("hlayout_command", text="CELL TOP\nRECT 0 0,0 10,10\nRECT 99 0,0 10,10\nRECT 1 0,0 10,10")
    assert "> RECT 99" in text and "> RECT 1 " not in text
    assert s.layer_counts() == {0: {"Rectangle": 1}}
    s.err("hlayout_command", text="")
    s.err("hlayout_command", text="   \n  ")


@test("commands", needs=["tk"])
def python_fallback_and_py_and_help(c):
    s = c.server()
    s.cmd("CELL TOP")
    s.cmd("PY s.rect(3, 0, 0, 7, 7)")
    s.cmd("s.rect(3, 10, 10, 17, 17)")       # unrecognised input falls through to Python
    assert s.layer_counts() == {3: {"Rectangle": 2}}
    assert "MASKOP" in s.cmd("HELP") and "NETLIST RUN" in s.cmd("help")
    assert s.py("len(s.cell.shapes)").strip() == "2"   # same session as hlayout_python


@test("commands", needs=["tk"])
def layer_commands(c):
    s = c.server()
    s.cmd('CELL TOP\nLAYER 1 name="Conductor 1" color=#3cb44b fill=1\nRECT "Conductor 1" 0,0 10,10\n'
          'RECT 3 0,0 5,5\nLAYER MOVE 3 7\nLAYER ALL visible=0\nLAYER ALL visible=1')
    assert s.status()["named_layers"]["1"] == "Conductor 1"
    assert s.layer_counts() == {1: {"Rectangle": 1}, 7: {"Rectangle": 1}}
    s.cmd("CLEAR 7")
    assert s.layer_counts() == {1: {"Rectangle": 1}}


@test("commands", needs=["tk"])
def file_and_library_commands(c):
    s = c.server()
    p = c.path
    s.cmd(f'CELL PART\nRECT 1 0,0 10,10\nSAVE "{p("lib.gds")}" PARTS')
    assert os.path.exists(p("lib.gds")) and not glob.glob(p("lib*hlaux*"))
    s.cmd(f'SAVE "{p("d.gds")}"')
    assert glob.glob(p("d*hlaux*"))
    t = c.server()
    t.cmd(f'CELL TOP\nATTACH "{p("lib.gds")}"')
    assert "PARTS" in t.cmd("LIBS") or "lib.gds" in t.cmd("LIBS")
    assert "PART" in t.cmd("LIBLIST 0") and "PART" in t.cmd("FIND par")
    t.cmd("PLACE PART 100,100")
    assert t.describe()["sref_count"] == 1
    t.cmd(f'MERGE "{p("lib.gds")}" "{p("d.gds")}" "{p("m.gds")}"')
    assert os.path.exists(p("m.gds"))
    t.cmd(f'OPEN "{p("d.gds")}"')
    assert t.status()["cells"] == ["PART"]
    t.err("hlayout_command", text=f'OPEN "{p("missing.gds")}"')


@test("commands", needs=["tk"])
def tech_commands(c):
    s = c.server()
    s.cmd('CELL TOP\nLAYER 21 name=Glaze\nTECH SET my_param 42')
    assert "42" in s.cmd("TECH GET my_param") and "my_param" in s.cmd("TECH PARAMS")
    s.cmd(f'TECH SAVE "{c.path("t.json")}"')
    t = c.server()
    t.cmd(f'CELL TOP\nTECH LOAD "{c.path("t.json")}"')
    assert t.status()["named_layers"].get("21") == "Glaze" and "42" in t.cmd("TECH GET my_param")


@test("commands", needs=["tk"])
def pin_commands(c):
    s = c.server()
    s.cmd("CELL TOP\nRECT 1 0,0 10,10\nRECT 1 20,0 30,10\nPIN SET 0 1\nPIN SET 1 A2")
    assert s.ev("[p['pin'] for p in s.list_pins()]") == ["1", "A2"]
    assert "A2" in s.cmd("PIN LIST")
    s.cmd('PIN SET 1 ""')
    assert s.ev("[p['pin'] for p in s.list_pins()]") == ["1", ""]
    s.err("hlayout_command", text="PIN SET 9 1")


@test("commands", needs=["tk", "shapely"])
def maskop_outline_and_clear_commands(c):
    s = c.server()
    s.cmd("CELL T\nRECT 1 0,0 1000,1000\nRECT 2 500,0 1500,1000\n"
          "MASKOP OVERSIZE 1 result=10 amount=100\nMASKOP UNDERSIZE 1 result=11 amount=100\n"
          "MASKOP AND 1 2 result=12\nMASKOP OR 1 2 result=13\nMASKOP NOT 1 2 result=14\nMASKOP XOR 1 2 result=15")
    assert [s.ev(AREA % n) for n in range(10, 16)] == [1440e3, 640e3, 500e3, 1500e3, 500e3, 1000e3]
    s.cmd("OUTLINE 1 width=20\nCLEAR 15")
    lc = s.layer_counts()
    assert lc[1] == {"Path": 1} and 15 not in lc
    s.err("hlayout_command", text="MASKOP AND 1 result=20")
    s.err("hlayout_command", text="MASKOP OVERSIZE 1 amount=5")


@test("commands", needs=["tk", "shapely"])
def drc_commands(c):
    s = c.server()
    s.cmd("CELL T\nRECT 5 0,0 50,1000\nRECT 5 70,0 500,1000\n"
          "DRCRULE ADD MIN_WIDTH 5 value=100 name=W1\nDRCRULE ADD MIN_SPACE 5 value=50 name=S1")
    listing = s.cmd("DRCRULE LIST")
    assert "W1" in listing and "S1" in listing
    assert "2" in s.cmd("DRC RUN error=57")
    assert 57 in s.layer_counts()
    assert s.ev("len(s.run_drc())") == 2          # the saved rules are the default
    s.cmd("DRCRULE REMOVE 1")
    assert "W1" not in s.cmd("DRCRULE LIST")
    s.cmd("DRCRULE CLEAR")
    assert s.ev("len(s.run_drc())") == 0
    s.err("hlayout_command", text="DRCRULE ADD MIN_SPACE_BETWEEN 5 value=10")   # needs layer_b
    s.err("hlayout_command", text="DRCRULE ADD NOPE 5 value=10")


@test("commands", needs=["tk", "shapely"])
def netlist_bond_autoroute_dielectric_commands(c):
    s = c.server()
    s.py(NETLIST_FIXTURE)
    out = s.cmd(f'PATH 2 40 round 0,0 1000,0\nNETLIST RUN 2 export="{c.path("n.csv")}"')
    assert "U1" in out and os.path.exists(c.path("n.csv"))
    s.cmd("CELL B\nBOND 0,0 2000,0 layer=40 width=25 pad=100 min=100 max=5000\n"
          "BOND MOVE 2000,0 2500,300 layer=40\nBOND DELETE 100,0 layer=40")
    assert s.describe()["own_shape_count"] == 0
    s.err("hlayout_command", text="BOND 0,0 20,0 layer=40 width=25 pad=100 min=100 max=5000")
    s.cmd("CELL R\nRECT 2 400,-500 600,500\nAUTOROUTE 0,0 1000,0 layer=2 grid=100 width=20")
    assert s.layer_counts() == {2: {"Rectangle": 1, "Path": 1}}
    s.cmd("CELL D\nRECT 3 0,0 5000,5000\nRECT 3 1000,1000 1500,1500\nDIELECTRIC layer=3 clearance=50")
    assert s.ev("len([x for x in s.cell.shapes if x.datatype == 1])") >= 1
    s.cmd("DIELECTRIC CLEAR layer=3")
    assert s.ev("len(s.cell.shapes)") == 2


@test("commands", needs=["tk"])
def table_sheet_resgen_and_export_commands(c):
    s = c.server()
    s.cmd('CELL TOP\nPINTABLE PT 55 20 title="MY PINS"\nSHEET SH 60 size=A4 title="BOARD A"\n'
          "RESGEN 0,0 500 1000 ref=R7 rlayer=10 clayer=2 glayer=21 a=100 b=300 c=100 d=100")
    assert {"TOP", "PT", "SH", "R7"} <= set(s.status()["cells"])
    assert s.ev("[r.label for r in s.cell.srefs]") == ["R7"]
    s.cmd(f'RECT 2 0,0 100,100\nGERBER "{c.path("cg")}"')
    assert glob.glob(c.path("cg_L*.gbr"))
    s.err("hlayout_command", text="PINTABLE PT 55 25")
    s.err("hlayout_command", text="RESTABLE RT 55")


# ======================================================================
# an end-to-end build, like a real session
# ======================================================================
@test("scenario")
def twenty_pin_package_build(c):
    """The 20-PIN-pkg build: substrate, two rows of holes, square pads front and back."""
    s = c.server()
    s.py("SIZE, N, PITCH, INSET, H, HOLE_R = 25400, 10, 2540, 1270, 750, 400\n"
         "X0 = (SIZE - (N - 1) * PITCH) // 2\n"
         "s.new_cell('20-PIN-pkg'); s.set_layer_style(52, name='Hole')\n"
         "with s.batch():\n"
         "    s.rect(50, 0, 0, SIZE, SIZE)\n"
         "    n = 0\n"
         "    for y in (INSET, SIZE - INSET):\n"
         "        for i in range(N):\n"
         "            x = X0 + i * PITCH; n += 1\n"
         "            s.rect(0, x - H, y - H, x + H, y + H, pin=str(n))\n"
         "            s.rect(1, x - H, y - H, x + H, y + H, pin=str(n))\n"
         "            s.circle('Hole', (x, y), HOLE_R)")
    d = s.describe()
    assert d["bbox_dbu"] == [0, 0, 25400, 25400] and d["size_mm"] == [25.4, 25.4]
    assert s.layer_counts() == {0: {"Rectangle": 20}, 1: {"Rectangle": 20},
                                50: {"Rectangle": 1}, 52: {"Polygon": 20}}
    assert s.ev("sorted({p['pin'] for p in s.list_pins() if p['pin']}, key=int)") == [str(i) for i in range(1, 21)]
    s.ok("hlayout_save", path=c.path("pkg.gds"))
    b = c.server("--design", c.path("pkg.gds"))
    assert b.describe(name="20-PIN-pkg")["own_shape_count"] == 61
    assert b.status()["named_layers"].get("52") == "Hole"
    if c.caps["shapely"]:
        drc = "len(s.run_drc([{'kind': 'MIN_SPACE', 'layer_a': 0, 'value': %d}], ['20-PIN-pkg']))"
        assert b.ev(drc % 1000) == 0 and b.ev(drc % 1100) > 0   # pads are 1.04 mm apart


# ======================================================================
# runner
# ======================================================================
def probe_capabilities():
    srv = Server()
    try:
        st = srv.status()
        _, no_shapely = srv.tool("hlayout_python", code="import shapely")
    finally:
        srv.close()
    return {"tk": st["command_bar_available"], "shapely": not no_shapely,
            "version": st["hlayout_version"]}


def run_one(t, caps, keep):
    missing = [n for n in t["needs"] if not caps[n]]
    if missing:
        return "SKIP", "needs " + ", ".join(missing), 0.0
    tmp = tempfile.mkdtemp(prefix="hlmcp_")
    ctx = Ctx(tmp, caps)
    t0 = time.time()
    try:
        t["fn"](ctx)
        outcome, msg = ("XPASS", "known defect now passes: " + t["known"]) if t["known"] else ("PASS", "")
    except Skip as e:
        outcome, msg = "SKIP", str(e)
    except AssertionError as e:
        import traceback
        here = [f for f in traceback.extract_tb(e.__traceback__) if f.name == t["name"]]
        where = f"line {here[-1].lineno}: {here[-1].line}" if here else ""
        msg = (str(e).strip() + ("\n" if str(e) else "") + where).strip()
        outcome = "FAIL"
        if t["known"]:
            outcome, msg = "XFAIL", t["known"]
    except Exception as e:
        outcome, msg = ("XFAIL", t["known"]) if t["known"] else ("FAIL", f"{type(e).__name__}: {e}")
    finally:
        ctx.cleanup()
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
    return outcome, msg, time.time() - t0


def main(argv=None):
    ap = argparse.ArgumentParser(description="HLayout MCP regression suite")
    ap.add_argument("-k", metavar="TEXT", help="only tests whose group.name contains TEXT")
    ap.add_argument("--list", action="store_true", help="list the tests and exit")
    ap.add_argument("--json", metavar="PATH", help="also write a JSON report")
    ap.add_argument("--keep", action="store_true", help="keep each test's scratch folder")
    ap.add_argument("-q", action="store_true", help="only print problems and the summary")
    opts = ap.parse_args(argv)

    tests = [t for t in TESTS if not opts.k or opts.k.lower() in (t["group"] + "." + t["name"]).lower()]
    if opts.list:
        for t in tests:
            print(f"{t['group']}.{t['name']}" + (f"  [needs {', '.join(t['needs'])}]" if t["needs"] else ""))
        print(f"{len(tests)} tests")
        return 0

    caps = probe_capabilities()
    print(f"HLayout {caps['version']} MCP regression  |  python {sys.version.split()[0]} on {sys.platform}"
          f"  |  command bar: {'yes' if caps['tk'] else 'NO (no Tkinter)'}"
          f"  |  shapely: {'yes' if caps['shapely'] else 'NO'}")
    results, t_start = [], time.time()
    for t in tests:
        outcome, msg, took = run_one(t, caps, opts.keep)
        results.append({"group": t["group"], "name": t["name"], "outcome": outcome,
                        "message": msg, "seconds": round(took, 2)})
        if not opts.q or outcome in ("FAIL", "XPASS", "XFAIL"):
            print(f"{outcome:5} {t['group']}.{t['name']}  ({took:.1f}s)")
            if msg and outcome != "PASS":
                for line in msg.splitlines():
                    print("        " + line)
    counts = {}
    for r in results:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    print("-" * 70)
    print(f"{len(results)} tests in {time.time() - t_start:.0f}s: "
          + ", ".join(f"{counts[k]} {k}" for k in ("PASS", "FAIL", "SKIP", "XFAIL", "XPASS") if k in counts))
    if opts.json:
        with open(opts.json, "w", encoding="utf-8") as f:
            json.dump({"capabilities": caps, "counts": counts, "results": results,
                       "python": sys.version, "platform": sys.platform}, f, indent=1)
    return 1 if counts.get("FAIL") else 0


if __name__ == "__main__":
    sys.exit(main())
