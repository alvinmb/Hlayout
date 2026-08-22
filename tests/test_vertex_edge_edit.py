import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")


def _make_app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    try:
        lib = Library("EDIT")
        top = lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.canvas.fit_view()
    app.update()
    app.snap_var.set(False)
    app._on_snap_change()
    app.mode_var.set("select")
    app._on_mode_change()
    return app, top


def _ev(x, y):
    return types.SimpleNamespace(x=x, y=y)


def test_click_polygon_edge_moves_only_that_edge():
    app, top = _make_app()
    try:
        poly = top.add_polygon(0, [(0, 0), (10000, 0), (10000, 10000), (0, 10000)])
        app.canvas.redraw()

        mid_px = app.canvas.to_px(5000, 0)  # midpoint of the (0,0)-(10000,0) edge
        app.canvas._select_click(_ev(*mid_px))
        assert app.canvas.selected[0] == "edge"

        orig = list(poly.points)
        target_px = app.canvas.to_px(5000, 2000)
        app.canvas._on_drag_motion(_ev(*target_px))
        app.canvas._on_release(_ev(*target_px))

        # only the edge's two endpoints moved; the other two are untouched
        assert poly.points[0] != orig[0]
        assert poly.points[1] != orig[1]
        assert poly.points[2] == orig[2]
        assert poly.points[3] == orig[3]
    finally:
        app.destroy()


def test_click_path_vertex_moves_only_that_vertex():
    app, top = _make_app()
    try:
        path = top.add_path(1, [(20000, 0), (30000, 0), (30000, 10000)], width=500, cap="round")
        app.canvas.redraw()

        v_px = app.canvas.to_px(30000, 0)  # the middle vertex
        app.canvas._select_click(_ev(*v_px))
        assert app.canvas.selected[0] == "vertex"

        orig = list(path.points)
        target_px = app.canvas.to_px(35000, 5000)
        app.canvas._on_drag_motion(_ev(*target_px))
        app.canvas._on_release(_ev(*target_px))

        assert path.points[0] == orig[0]
        assert path.points[1] != orig[1]
        assert path.points[2] == orig[2]
    finally:
        app.destroy()


def test_delete_vertex_removes_only_one_point():
    app, top = _make_app()
    try:
        poly = top.add_polygon(0, [(0, 0), (10000, 0), (10000, 10000), (0, 10000)])
        app.canvas.redraw()
        v_px = app.canvas.to_px(10000, 0)
        app.canvas._select_click(_ev(*v_px))
        assert app.canvas.selected[0] == "vertex"
        app.canvas.delete_selected()
        assert len(poly.points) == 3
        assert (10000, 0) not in poly.points
    finally:
        app.destroy()


def test_delete_vertex_refuses_below_minimum():
    app, top = _make_app()
    try:
        path = top.add_path(1, [(0, 0), (1000, 1000)], width=200, cap="flush")
        app.canvas.redraw()
        v_px = app.canvas.to_px(0, 0)
        app.canvas._select_click(_ev(*v_px))
        assert app.canvas.selected[0] == "vertex"
        app.canvas.delete_selected()
        assert len(path.points) == 2  # refused: a path needs >= 2 points
    finally:
        app.destroy()
