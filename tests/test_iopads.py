"""Optimised I/O pinout (Session.assign_io_pads / IOPADS)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")
pytest.importorskip("scipy")

from hlayout.io.techfile import load_net_names, load_route_rules, save_route_rules
from hlayout.scripting import Session


def _board():
    """Substrate with two dice: U1 on the left, U2 on the right; a header
    below the substrate bonded up to four pad sites along the bottom edge.
    The pinout puts U2's net on the leftmost pins and U1's on the right."""
    s = Session.new("IO")
    s.new_cell("DIE")
    s.rect(5, -200, -200, 200, 200, pin="1")
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr["layer_a"] = 3; rr["layer_b"] = 5; rr["boundary_layer"] = 57
    save_route_rules(s.library, rr)
    s.rect(57, 0, 0, 40000, 20000)                                   # substrate
    s.sref("DIE", (8000, 10000)).label = "U1"
    s.sref("DIE", (32000, 10000)).label = "U2"
    xs = [5000, 15000, 25000, 35000]
    for x in xs:
        s.path(60, [(x, -2000), (x, 1000)], 75)                       # header bond
    names = {"IO.1": "B", "IO.2": "B", "IO.3": "A", "IO.4": "A", "U1.1": "A", "U2.1": "B"}
    s.set_net_names(names)
    return s


def test_plan_puts_each_net_next_to_its_die():
    s = _board()
    p = s.assign_io_pads(apply=False)
    assert p["after"] < p["before"]
    by_pin = {st["pin"]: st["net"] for st in p["sites"]}
    assert by_pin["1"] == "A" and by_pin["2"] == "A" and by_pin["3"] == "B" and by_pin["4"] == "B"


def test_apply_draws_the_pads_and_renames(tmp_path):
    s = _board()
    out = tmp_path / "io_pinout.csv"
    s.assign_io_pads(csv_path=str(out))
    pads = sorted((sh.pin, sh.bbox()) for sh in s.library.cells["TOP"].shapes if getattr(sh, "pin", ""))
    assert [p for p, _b in pads] == ["1", "2", "3", "4"]
    x0, y0, x1, y1 = pads[0][1]
    assert (x1 - x0, y1 - y0) == (300, 600) and y0 < 1000 < y1        # long side inward, bond on it
    names = load_net_names(s.library)
    assert names["IO.1"] == "A" and names["IO.4"] == "B"
    assert out.read_text().splitlines()[0] == "header_pin,net,previous_net"


def test_iopads_command():
    pytest.importorskip("tkinter")
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _board()
    out = CommandInterpreter(s, ScriptConsole(s)).run("IOPADS")
    assert out.startswith("I/O pinout plan:") and "4 pin(s) change" in out


def test_pad_size_comes_from_the_io_pad_rules():
    from hlayout.io.techfile import load_io_pad_rules, save_io_pad_rules
    s = _board()
    assert load_io_pad_rules(s.library) == {"width": 300, "length": 600, "bond_inset": 150,
                                            "edge_margin": 250}
    save_io_pad_rules(s.library, {"width": 400, "length": 1000, "bond_inset": 200})
    s.assign_io_pads()
    x0, y0, x1, y1 = next(sh.bbox() for sh in s.library.cells["TOP"].shapes if getattr(sh, "pin", "") == "1")
    assert (x1 - x0, y1 - y0) == (400, 1000)
    assert y0 == 1000 - 200                                          # bond lands 0.2 mm in
    s.assign_io_pads(pad_w=250, pad_l=500, bond_inset=100)           # per-call override
    x0, y0, x1, y1 = next(sh.bbox() for sh in s.library.cells["TOP"].shapes if getattr(sh, "pin", "") == "1")
    assert (x1 - x0, y1 - y0) == (250, 500) and y0 == 900


def test_io_pads_tab_saves_the_rules():
    tk = pytest.importorskip("tkinter")
    from hlayout.gui.app import App
    from hlayout.io.techfile import load_io_pad_rules
    s = _board()
    try:
        app = App(s.library)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    try:
        frame = tk.Frame(app)
        apply = app._build_io_pad_form(frame)
        spins = [w for w in frame.winfo_children()[1].winfo_children() if w.winfo_class() == "TSpinbox"]
        spins[0].delete(0, "end"); spins[0].insert(0, "0.5")
        assert apply() is None
        assert load_io_pad_rules(app.session.library)["width"] == 500
        spins[2].delete(0, "end"); spins[2].insert(0, "9")
        assert "inset" in apply()
    finally:
        app.destroy()
