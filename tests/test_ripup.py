"""Rip up the routing between two points of a net (Session.rip_up_between,
RIPUP, Edit > Rip Up and Reroute)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.errors import LayoutError
from hlayout.core.shapes import Path
from hlayout.io.specctra import find_vias
from hlayout.io.techfile import (load_dielectric_rules, load_route_rules, save_dielectric_rules,
                                 save_route_rules)
from hlayout.scripting import Session


def _board(via_cell=False):
    """IO.1 -> L3 track -> via at (0,0) -> L5 dog-leg -> via at (0,3000)
    -> L3 track -> IO.2; plus an unrelated L3 track (net B)."""
    s = Session.new("RU")
    if via_cell:
        s.new_cell("VIA35")
        s.rect(3, -200, -200, 200, 200)
        s.rect(5, -200, -200, 200, 200)
        s.rect(4, -300, -300, 300, 300)
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr["layer_a"] = 3; rr["layer_b"] = 5
    save_route_rules(s.library, rr)
    dr = load_dielectric_rules(s.library); dr["layer"] = 4; dr["clearance"] = 0
    save_dielectric_rules(s.library, dr)
    s.rect(4, -10000, -10000, 10000, 10000)
    s.rect(3, -8400, -400, -7600, 400, pin="1")
    s.rect(3, 7600, 2600, 8400, 3400, pin="2")
    s.path(3, [(-7600, 0), (0, 0)], 300)
    for y in (0, 3000):
        if via_cell:
            s.sref("VIA35", (0, y))
        else:
            s.drop_via(0, y, 3, 5, size=400, dielectric_layer=4, dielectric_window=600)
    s.path(5, [(0, 0), (0, 3000)], 300)
    s.path(3, [(0, 3000), (7600, 3000)], 300)
    s.path(3, [(-5000, -5000), (5000, -5000)], 300)          # another net
    s.generate_dielectric()
    s.set_net_names({"IO.1": "A", "IO.2": "A"})
    s.clear_undo_history()
    return s


def _tracks(s, layer):
    return [list(map(tuple, p.points)) for p in s.library.cells["TOP"].shapes
            if isinstance(p, Path) and p.layer == layer]


def test_rip_up_through_two_vias_leaves_the_ends():
    s = _board()
    r = s.rip_up_between((-4000, 0), (4000, 3000))
    assert r["a"][:2] == (-4000, 0) and r["b"][:2] == (4000, 3000)
    assert r["net"] == "A" and len(r["vias"]) == 2
    assert _tracks(s, 5) == []
    l3 = sorted(_tracks(s, 3))
    assert [(-7600, 0), (-4000, 0)] in l3 and [(4000, 3000), (7600, 3000)] in l3
    assert [(-5000, -5000), (5000, -5000)] in l3                      # other net untouched
    assert find_vias(s.library.cells["TOP"], (3, 5)) == []
    assert s.highlight_net("IO.1").fragments == 2                     # now two loose ends
    s.undo()
    assert len(find_vias(s.library.cells["TOP"], (3, 5))) == 2
    assert s.highlight_net("IO.1").fragments == 1


def test_rip_up_within_one_layer_keeps_the_vias():
    s = _board()
    r = s.rip_up_between((0, 1000), (0, 2000))
    assert r["vias"] == [] and len(find_vias(s.library.cells["TOP"], (3, 5))) == 2
    assert sorted(_tracks(s, 5)) == [[(0, 0), (0, 1000)], [(0, 2000), (0, 3000)]]


def test_rip_up_through_placed_via_cells():
    s = _board(via_cell=True)
    r = s.rip_up_between((-4000, 0), (4000, 3000))
    assert len(r["vias"]) == 2
    assert not [x for x in s.library.cells["TOP"].srefs if x.cell_name == "VIA35"]


def test_points_on_different_nets_are_refused():
    s = _board()
    with pytest.raises(LayoutError):
        s.rip_up_between((-4000, 0), (0, -5000))
    with pytest.raises(LayoutError):
        s.rip_up_between((-4000, 0), (9000, 9000))


def test_ripup_command():
    pytest.importorskip("tkinter")
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _board()
    out = CommandInterpreter(s, ScriptConsole(s)).run("RIPUP -4000,0 4000,3000")
    assert out.startswith("ripped up net A:") and "2 via(s)" in out


def test_gui_rip_up_then_route_from_the_first_point():
    tk = pytest.importorskip("tkinter")
    import types
    from hlayout.gui.app import App
    s = _board()
    try:
        app = App(s.library)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    try:
        app.update()
        app.snap_var.set(False)
        app._on_snap_change()
        cv = app.canvas
        app.session.use_cell("TOP")
        cv.cell_name = "TOP"
        cv.route_layer_a, cv.route_layer_b = 3, 5
        cv.fit_view()
        cv.set_mode("ripup")
        for x, y in ((-4000, 0), (4000, 3000)):
            px, py = cv.to_px(x, y)
            cv._on_click(types.SimpleNamespace(x=int(px), y=int(py)))
        assert cv.draw_mode == "route"
        assert cv._route_active_layer == 3
        (sx, sy), = cv._pending_points
        assert abs(sx + 4000) < 300 and abs(sy) < 300
        assert cv._reroute_target is not None and cv.sli_focus == "A"
        assert cv.find_withtag("reroute_target")
        assert not [p for p in app.session.library.cells["TOP"].shapes
                    if isinstance(p, Path) and p.layer == 5]
        cv.set_mode("select")                       # leaving the reroute drops the pick
        assert cv.sli_focus is None and cv._reroute_target is None
    finally:
        app.destroy()
