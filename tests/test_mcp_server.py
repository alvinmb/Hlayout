"""MCP server (hlayout/mcpserver): the shared operations, the stdio
JSON-RPC server, and the GUI bridge (driven here by a stand-in app, so
no display is needed)."""

import json
import os
import subprocess
import sys
import threading

import pytest

from hlayout.mcpserver import bridge, ops, server
from hlayout.scripting import ScriptConsole, Session

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ctx():
    return ops.local_context()


def test_python_op_edits_and_returns_expression_value():
    ctx = _ctx()
    ops.call(ctx, "python", {"code": "s.new_cell('TOP')\ns.rect(3, 0, 0, 100, 50)"})
    assert ops.call(ctx, "python", {"code": "len(s.cell.shapes)"}).strip() == "1"
    info = json.loads(ops.call(ctx, "describe_cell", {}))
    assert info["cell"] == "TOP"
    assert tuple(info["bbox_dbu"]) == (0, 0, 100, 50)
    assert info["layers"][0]["layer"] == 3
    assert info["layers"][0]["shapes"] == {"Rectangle": 1}


def test_python_op_reports_exceptions_as_errors():
    with pytest.raises(ops.OpError, match="ZeroDivisionError"):
        ops.call(_ctx(), "python", {"code": "1/0"})


def test_save_then_open_round_trip(tmp_path):
    ctx = _ctx()
    path = str(tmp_path / "d.gds")
    with pytest.raises(ops.OpError):
        ops.call(ctx, "save", {})  # no file yet
    ops.call(ctx, "python", {"code": "s.new_cell('A'); s.rect(0, 0, 0, 10, 10)"})
    ops.call(ctx, "save", {"path": path})
    other = _ctx()
    ops.call(other, "open", {"path": path})
    status = json.loads(ops.call(other, "status", {}))
    assert status["file"] == path and status["cells"] == ["A"]
    assert status["mode"] == "headless"
    # `library` in the script namespace follows the newly opened document
    assert ops.call(other, "python", {"code": "library is s.library"}).strip() == "True"


def test_api_listing_and_detail():
    ctx = _ctx()
    listing = ops.call(ctx, "api", {})
    assert "run_drc(" in listing and "place_cell(" in listing and "_checkpoint" not in listing
    assert "Design Rule Check" in ops.call(ctx, "api", {"name": "run_drc"})
    with pytest.raises(ops.OpError):
        ops.call(ctx, "api", {"name": "_checkpoint"})


def test_unknown_op():
    with pytest.raises(ops.OpError):
        ops.call(_ctx(), "nope", {})


def _rpc(proc, msg):
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


def test_stdio_server_end_to_end():
    proc = subprocess.Popen(
        [sys.executable, "-m", "hlayout.mcpserver", "--headless"],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        init = _rpc(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                      "clientInfo": {"name": "t", "version": "0"}}})
        assert init["result"]["protocolVersion"] == "2025-03-26"
        assert init["result"]["serverInfo"]["name"] == "hlayout"
        proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        tools = _rpc(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in tools["result"]["tools"]}
        assert {"hlayout_status", "hlayout_python", "hlayout_command"} <= names
        # print() from user code must not corrupt the protocol stream
        r = _rpc(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "hlayout_python",
                                   "arguments": {"code": "s.new_cell('T'); print('hi')"}}})
        assert r["result"]["isError"] is False
        assert r["result"]["content"][0]["text"].endswith("hi\n")
        bad = _rpc(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                          "params": {"name": "hlayout_python", "arguments": {"code": "1/0"}}})
        assert bad["result"]["isError"] is True
        assert _rpc(proc, {"jsonrpc": "2.0", "id": 5, "method": "nope"})["error"]["code"] == -32601
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)
        proc.stdout.close()
        proc.stderr.close()


class _FakeApp:
    """Just enough of gui.app.App for McpBridge: a session, a console,
    and after() -- run on a timer thread instead of a Tk loop."""

    def __init__(self):
        self.session = Session.new("LIVE")
        self.console = ScriptConsole(self.session)
        self.current_path = None

    def after(self, ms, fn):
        t = threading.Timer(ms / 1000.0, fn)
        t.daemon = True
        t.start()


def test_bridge_runs_ops_in_the_app_and_server_prefers_it(tmp_path, monkeypatch):
    info_path = str(tmp_path / "mcp_bridge.json")
    monkeypatch.setattr(bridge, "BRIDGE_INFO_PATH", info_path)
    app = _FakeApp()
    b = bridge.McpBridge(app, poll_ms=5).start()
    try:
        backend = server.Backend()
        out = backend.call("python", {"code": "s.new_cell('TOP'); s.rect(1, 0, 0, 5, 5)"})
        assert out.startswith("[connected to the running HLayout window]")
        assert len(app.session.library.get_cell("TOP").shapes) == 1  # the APP's session
        status = json.loads(backend.call("status", {}))  # no note the second time
        assert status["mode"] == "live" and status["library"] == "LIVE"

        # a wrong token is refused and nothing runs
        with open(info_path) as f:
            info = json.load(f)
        info["token"] = "0" * 32
        with open(info_path, "w") as f:
            json.dump(info, f)
        with pytest.raises(ops.OpError, match="bad token"):
            backend.call("python", {"code": "s.new_cell('EVIL')"})
        assert "EVIL" not in app.session.library.cells
    finally:
        b.stop()

    # bridge gone: falls back to a private session and says so loudly
    out = server.Backend().call("status", {})
    assert out.startswith("[no running HLayout window")
    assert json.loads(out.split("]\n", 1)[1])["mode"] == "headless"


def test_bridge_stop_removes_its_info_file(tmp_path):
    info_path = str(tmp_path / "mcp_bridge.json")
    b = bridge.McpBridge(_FakeApp(), poll_ms=5, info_path=info_path).start()
    assert bridge.read_bridge_info(info_path)["port"] == b.port
    b.stop()
    assert not os.path.exists(info_path)
