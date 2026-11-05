# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core.autoroute import _collapse, astar, route

pytest.importorskip("shapely")

from shapely.geometry import LineString, Polygon as ShapelyPolygon

from hlayout.core.errors import LayoutError
from hlayout.core.maskops import shape_to_shapely
from hlayout.scripting import Session


# --- core A* ---------------------------------------------------------

def test_astar_straight_line_when_unobstructed():
    blocked = [[False] * 5 for _ in range(5)]
    p = astar(blocked, (0, 0), (4, 0))
    assert p[0] == (0, 0) and p[-1] == (4, 0)
    assert len(p) == 5


def test_astar_routes_around_a_wall():
    blocked = [[False] * 5 for _ in range(5)]
    for j in range(1, 5):       # wall up column 2, gap only at row 0
        blocked[2][j] = True
    p = astar(blocked, (0, 2), (4, 2))
    assert p is not None
    assert all(not blocked[i][j] for i, j in p)
    assert (2, 0) in p          # had to detour through the one gap


def test_astar_returns_none_when_sealed_off():
    blocked = [[False] * 3 for _ in range(3)]
    for j in range(3):
        blocked[1][j] = True
    assert astar(blocked, (0, 0), (2, 0)) is None


def test_astar_blocked_endpoint_is_none():
    blocked = [[False] * 3 for _ in range(3)]
    blocked[2][2] = True
    assert astar(blocked, (0, 0), (2, 2)) is None


def test_turn_penalty_prefers_straight_runs():
    blocked = [[False] * 5 for _ in range(5)]
    p = astar(blocked, (0, 0), (4, 4), turn_penalty=5)
    # exactly one corner (an L), not a staircase
    turns = sum(
        1 for k in range(1, len(p) - 1)
        if (p[k][0] - p[k - 1][0], p[k][1] - p[k - 1][1])
        != (p[k + 1][0] - p[k][0], p[k + 1][1] - p[k][1])
    )
    assert turns == 1


def test_collapse_drops_collinear_interior_points():
    assert _collapse([(0, 0), (0, 1), (0, 2), (1, 2), (2, 2)]) == [(0, 0), (0, 2), (2, 2)]


# --- route() over DBU coords ----------------------------------------

def test_route_dodges_an_obstacle_polygon():
    # a bar across the direct path, open above y=400
    obstacle = ShapelyPolygon([(1500, -1000), (1700, -1000), (1700, 400), (1500, 400)])
    pts = route((100, 100), (4100, 100), obstacle, grid=100,
                x0=-200, y0=-1200, nx=60, ny=40)
    assert pts is not None
    assert pts[0] == (100, 100) and pts[-1] == (4100, 100)
    assert not LineString(pts).buffer(1).intersects(obstacle)


def test_route_none_when_boxed_in():
    box = ShapelyPolygon([(-500, -500), (500, -500), (500, 500), (-500, 500)]).exterior.buffer(60)
    pts = route((0, 0), (2000, 0), box, grid=50, x0=-800, y0=-800, nx=80, ny=40)
    assert pts is None


def test_route_rejects_an_oversized_grid():
    with pytest.raises(ValueError):
        route((0, 0), (10, 10), ShapelyPolygon(), grid=1,
              x0=0, y0=0, nx=1000, ny=1000)


# --- Session.auto_route -------------------------------------------

def _design():
    s = Session.new("AR")
    s.new_cell("TOP")
    pa = s.rect(3, 0, 0, 200, 200, pin="1")
    pb = s.rect(3, 4000, 0, 4200, 200, pin="2")
    s.clear_undo_history()
    return s, pa, pb


def test_auto_route_straight_trace_when_clear():
    s, pa, pb = _design()
    path = s.auto_route(pa, pb, layer=3, grid=100, width=40)
    assert path.layer == 3 and path.width == 40
    assert path.points[0] == (100, 100) and path.points[-1] == (4100, 100)
    assert len(path.points) == 2       # nothing in the way -> one segment


def test_auto_route_goes_around_other_metal_on_the_layer():
    s, pa, pb = _design()
    blocker = s.rect(3, 1900, -1200, 2100, 400)   # straddles the direct path
    s.clear_undo_history()
    path = s.auto_route(pa, pb, layer=3, grid=100, width=40, clearance=50)
    assert len(path.points) > 2
    trace = LineString(path.points).buffer(path.width / 2.0 + 1)
    assert not trace.intersects(shape_to_shapely(blocker))


def test_auto_route_ignores_metal_on_other_layers():
    s, pa, pb = _design()
    s.rect(5, 1900, -1200, 2100, 400)   # a blocker, but on layer 5 -- irrelevant
    s.clear_undo_history()
    path = s.auto_route(pa, pb, layer=3, grid=100, width=40)
    assert len(path.points) == 2        # straight through -- layer 5 is not an obstacle


def test_auto_route_is_one_undo_step():
    s, pa, pb = _design()
    s.auto_route(pa, pb, layer=3, grid=100, width=40)
    assert any(getattr(sh, "points", None) and len(sh.points) >= 2
               for sh in s.cell.shapes if sh.layer == 3 and hasattr(sh, "width"))
    assert s.undo() is True
    assert not any(hasattr(sh, "width") and sh.layer == 3 for sh in s.cell.shapes)


def test_auto_route_raises_when_no_clear_path_exists():
    s = Session.new("AR2")
    s.new_cell("TOP")
    pa = s.rect(3, 0, 0, 100, 100, pin="1")
    pb = s.rect(3, 3000, 0, 3100, 100, pin="2")
    # wall pa off completely on layer 3
    s.rect(3, -300, -300, 400, -200)
    s.rect(3, -300, 300, 400, 400)
    s.rect(3, -300, -300, -200, 400)
    s.rect(3, 400, -300, 500, 400)
    s.clear_undo_history()
    with pytest.raises(LayoutError):
        s.auto_route(pa, pb, layer=3, grid=50, width=20)


def test_auto_route_rejects_two_endpoints_at_the_same_point():
    # Picking the same pin twice (or two points closer than one grid
    # step) used to write a zero-length 2-point path -- which passes
    # Path's own validation but then can't round-trip through GDS,
    # breaking the file on the next open. It must fail cleanly instead.
    s, pa, pb = _design()
    with pytest.raises(LayoutError, match="same point"):
        s.auto_route((100, 100), (100, 100), layer=3, grid=100, width=40)
    with pytest.raises(LayoutError, match="same point"):
        s.auto_route((100, 100), (150, 100), layer=3, grid=100, width=40)
    assert not any(type(sh).__name__ == "Path" for sh in s.cell.shapes)


def test_route_returns_none_for_a_degenerate_result():
    # start and goal snap to the same node -> no trace, not a 1-point path
    assert route((0, 0), (0, 0), None, 100, 0, 0, 5, 5) is None
    assert route((10, 10), (40, 10), None, 100, 0, 0, 5, 5) is None


def test_auto_route_accepts_bare_points():
    s = Session.new("AR3")
    s.new_cell("TOP")
    s.clear_undo_history()
    path = s.auto_route((0, 0), (2000, 0), layer=3, grid=100, width=30)
    assert path.points[0] == (0, 0) and path.points[-1] == (2000, 0)


# --- route boundary (keep-in) --------------------------------------

def test_auto_route_stays_inside_the_boundary_layer():
    s = Session.new("ARB")
    s.new_cell("TOP")
    s.rect(57, 0, -500, 5000, 700)                 # keep-in region on L57
    pa = s.rect(3, 100, 0, 300, 200, pin="1")
    pb = s.rect(3, 4600, 0, 4800, 200, pin="2")
    s.rect(3, 1500, -600, 1700, 300)               # blocker; the gap is at the top
    s.clear_undo_history()

    path = s.auto_route(pa, pb, layer=3, grid=100, width=40,
                        boundary_layer=57, clearance=20)
    ys = [y for _x, y in path.points]
    assert max(ys) <= 700 - 20 + 1                 # inside the width/2-inset top
    assert min(ys) >= -500 + 20 - 1               # inside the inset bottom
    assert len(path.points) > 2                    # detoured to the gap


def test_auto_route_fails_when_the_only_detour_leaves_the_boundary():
    s = Session.new("ARB2")
    s.new_cell("TOP")
    s.rect(57, 0, -200, 5000, 400)                 # thin keep-in band
    qa = s.rect(3, 100, 0, 300, 200, pin="1")
    qb = s.rect(3, 4600, 0, 4800, 200, pin="2")
    s.rect(3, 2000, -300, 2200, 500)              # full-height blocker across the band
    s.clear_undo_history()
    with pytest.raises(LayoutError, match="boundary"):
        s.auto_route(qa, qb, layer=3, grid=50, width=20, boundary_layer=57)


def test_auto_route_boundary_comes_from_route_rules():
    from hlayout.io.techfile import save_route_rules
    s = Session.new("ARB3")
    s.new_cell("TOP")
    save_route_rules(s.library, {"layer_a": 3, "layer_b": 5, "via_cell": "",
                                 "boundary_layer": 57})
    s.rect(57, -100, -100, 3000, 300)             # boundary that does NOT cover (0,900)
    s.clear_undo_history()
    # second point is outside the boundary -> rejected, without passing
    # boundary_layer= explicitly
    with pytest.raises(LayoutError, match="outside the route boundary"):
        s.auto_route((100, 100), (100, 900), layer=3, grid=50, width=20)


def test_auto_route_no_boundary_is_unrestricted():
    s = Session.new("ARB4")
    s.new_cell("TOP")
    s.rect(3, 100, 0, 300, 200, pin="1")
    s.rect(3, 4600, 0, 4800, 200, pin="2")
    s.clear_undo_history()
    # no L57 geometry, boundary_layer not set -> routes freely
    path = s.auto_route((200, 100), (4700, 100), layer=3, grid=100, width=40)
    assert path.points[0] == (200, 100) and path.points[-1] == (4700, 100)


# --- command bar -------------------------------------------------

def test_autoroute_command_draws_a_trace():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s, pa, pb = _design()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("AUTOROUTE 100,100 4100,100 layer=3 grid=100 width=40")
    assert "auto-routed" in out
    assert any(hasattr(sh, "width") and sh.layer == 3 for sh in s.cell.shapes)


# --- two-click GUI tool ---------------------------------------------

def _ev(x, y):
    import types
    return types.SimpleNamespace(x=int(x), y=int(y))


def test_edit_auto_route_two_pins_is_a_two_click_tool():
    import tkinter as tk
    from hlayout.core.library import Library
    from hlayout.io.techfile import save_route_rules
    try:
        from hlayout.gui.app import App
        lib = Library("ART")
        lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        import pytest as _pt
        _pt.skip(f"no display: {e}")
    try:
        save_route_rules(app.session.library,
                         {"layer_a": 3, "layer_b": 5, "via_cell": ""})
        s = app.session
        pa = s.rect(3, 0, 0, 200, 200, pin="1")
        pb = s.rect(3, 4000, 0, 4200, 200, pin="2")
        s.rect(3, 1900, -1200, 2100, 400)          # a blocker on layer 3
        s.clear_undo_history()
        app.canvas.grid_dbu = 100
        app.canvas.path_width = 40
        app.canvas.redraw()
        app.update_idletasks()

        app._auto_route_two_pins()
        assert app.canvas.draw_mode == "auto_route"

        def click(shape):
            x0, y0, x1, y1 = shape.bbox()
            px, py = app.canvas.to_px((x0 + x1) / 2, (y0 + y1) / 2)
            app.canvas._on_click(_ev(px, py))

        click(pa)
        assert app.canvas._auto_route_a is not None    # armed after first pin
        click(pb)

        traces = [sh for sh in s.cell.shapes
                  if hasattr(sh, "width") and sh.layer == 3]
        assert len(traces) == 1
        assert len(traces[0].points) > 2               # jogged around the blocker
        assert app.canvas.draw_mode == "auto_route"    # stays armed for the next pair
        assert app.canvas._auto_route_a is None

        app.canvas.cancel_draw()                        # Esc
        assert app.canvas.draw_mode == "select"
    finally:
        app.destroy()
