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


def test_delete_vertex_of_a_two_point_path_deletes_the_whole_path():
    # A 2-point path can't give up a point, so Delete on a selected
    # endpoint of one removes the whole path rather than doing nothing
    # (which read as "Delete is broken").
    app, top = _make_app()
    try:
        path = top.add_path(1, [(0, 0), (1000, 1000)], width=200, cap="flush")
        app.canvas.redraw()
        v_px = app.canvas.to_px(0, 0)
        app.canvas._select_click(_ev(*v_px))
        assert app.canvas.selected[0] == "vertex"
        app.canvas.delete_selected()
        assert path not in top.shapes
        assert app.canvas.selected is None
    finally:
        app.destroy()


def test_delete_vertex_of_a_triangle_polygon_deletes_the_whole_polygon():
    app, top = _make_app()
    try:
        poly = top.add_polygon(0, [(0, 0), (10000, 0), (5000, 8000)])
        app.canvas.redraw()
        v_px = app.canvas.to_px(0, 0)
        app.canvas._select_click(_ev(*v_px))
        assert app.canvas.selected[0] == "vertex"
        app.canvas.delete_selected()
        assert poly not in top.shapes       # 3 points -> can't drop one
    finally:
        app.destroy()


# --- Edit > Insert Vertex on Edge -------------------------------------------

def test_insert_vertex_on_path_edge_adds_a_point_there():
    app, top = _make_app()
    try:
        path = top.add_path(1, [(0, 0), (10000, 0)], width=500, cap="round")
        app.canvas.redraw()

        mid_px = app.canvas.to_px(5000, 0)
        app.canvas._select_click(_ev(*mid_px))
        assert app.canvas.selected == ("edge", path, (0, 1))

        assert app.canvas.insert_vertex_on_selected_edge() is True
        assert len(path.points) == 3
        assert path.points == [(0, 0), (5000, 0), (10000, 0)]
        assert app.canvas.selected == ("vertex", path, 1)
    finally:
        app.destroy()


def test_insert_vertex_on_polygon_edge_adds_a_point_there():
    app, top = _make_app()
    try:
        poly = top.add_polygon(0, [(0, 0), (10000, 0), (10000, 10000), (0, 10000)])
        app.canvas.redraw()

        mid_px = app.canvas.to_px(5000, 0)
        app.canvas._select_click(_ev(*mid_px))
        assert app.canvas.selected[0] == "edge"

        assert app.canvas.insert_vertex_on_selected_edge() is True
        assert len(poly.points) == 5
        assert poly.points[1] == (5000, 0)
    finally:
        app.destroy()


def test_insert_vertex_requires_an_edge_selected():
    app, top = _make_app()
    try:
        top.add_polygon(0, [(0, 0), (10000, 0), (10000, 10000), (0, 10000)])
        app.canvas.redraw()
        assert app.canvas.selected is None
        assert app.canvas.insert_vertex_on_selected_edge() is False
    finally:
        app.destroy()


# --- Edit > Delete Selected on a path edge (segment) ------------------------

def test_delete_middle_segment_splits_path_into_two_pieces():
    app, top = _make_app()
    try:
        path = top.add_path(1, [(0, 0), (10000, 0), (20000, 0), (30000, 0)],
                             width=300, cap="round")
        app.canvas.redraw()

        mid_px = app.canvas.to_px(15000, 0)  # the (10000,0)-(20000,0) edge
        app.canvas._select_click(_ev(*mid_px))
        assert app.canvas.selected == ("edge", path, (1, 2))

        app.canvas.delete_selected()

        assert path not in top.shapes
        assert len(top.shapes) == 2
        piece_points = sorted(tuple(s.points) for s in top.shapes)
        assert piece_points == [((0, 0), (10000, 0)), ((20000, 0), (30000, 0))]
        for s in top.shapes:
            assert s.width == 300
            assert s.layer == 1
        assert app.canvas.selected is None
        assert sorted(id(s) for _, s in app.canvas.selected_multi) == sorted(id(s) for s in top.shapes)
    finally:
        app.destroy()


def test_delete_first_segment_trims_instead_of_leaving_a_sliver():
    app, top = _make_app()
    try:
        path = top.add_path(1, [(0, 0), (10000, 0), (20000, 0)], width=300, cap="round")
        app.canvas.redraw()

        mid_px = app.canvas.to_px(5000, 0)  # the (0,0)-(10000,0) edge
        app.canvas._select_click(_ev(*mid_px))
        assert app.canvas.selected == ("edge", path, (0, 1))

        app.canvas.delete_selected()

        assert path not in top.shapes
        assert len(top.shapes) == 1
        assert top.shapes[0].points == [(10000, 0), (20000, 0)]
        assert app.canvas.selected == ("shape", top.shapes[0])
    finally:
        app.destroy()


def test_delete_the_only_segment_of_a_two_point_path_deletes_the_path():
    # Nothing survives removing a 2-point path's single segment, so
    # Delete on that edge removes the whole path (it used to just print
    # a status line and leave the path in place).
    app, top = _make_app()
    try:
        path = top.add_path(1, [(0, 0), (10000, 0)], width=300, cap="round")
        app.canvas.redraw()

        mid_px = app.canvas.to_px(5000, 0)
        app.canvas._select_click(_ev(*mid_px))
        assert app.canvas.selected[0] == "edge"

        app.canvas.delete_selected()
        assert path not in top.shapes
        assert app.canvas.selected is None
    finally:
        app.destroy()


def test_delete_segment_still_refused_for_a_polygon_edge():
    app, top = _make_app()
    try:
        poly = top.add_polygon(0, [(0, 0), (10000, 0), (10000, 10000), (0, 10000)])
        app.canvas.redraw()

        mid_px = app.canvas.to_px(5000, 0)
        app.canvas._select_click(_ev(*mid_px))
        assert app.canvas.selected[0] == "edge"

        app.canvas.delete_selected()
        assert poly in top.shapes
        assert len(poly.points) == 4
    finally:
        app.destroy()


# --- Rectangle edge/vertex editing (promotes to Polygon) -------------------

def test_stretch_rectangle_edge_promotes_to_polygon_and_moves_that_edge():
    from hlayout.core.shapes import Polygon, Rectangle
    app, top = _make_app()
    try:
        rect = top.add_rectangle(0, 0, 0, 10000, 10000, pin="1")
        app.canvas.redraw()

        # grab the bottom edge (0,0)-(10000,0)
        app.canvas._select_click(_ev(*app.canvas.to_px(5000, 0)))
        assert app.canvas.selected[0] == "edge"
        assert app.canvas.selected[1] is rect          # still a Rectangle until dragged

        target = app.canvas.to_px(5000, -3000)
        app.canvas._on_drag_motion(_ev(*target))
        app.canvas._on_release(_ev(*target))

        assert rect not in top.shapes                   # promoted...
        poly = top.shapes[0]
        assert isinstance(poly, Polygon)
        assert poly.pin == "1"                          # ...carrying the pin
        # only the grabbed edge's two endpoints moved
        assert poly.points[0][1] == -3000 and poly.points[1][1] == -3000
        assert poly.points[2] == (10000, 10000) and poly.points[3] == (0, 10000)

        app.session.undo()                              # one undo step
        restored = app.session.library.cells[top.name]
        assert isinstance(restored.shapes[0], Rectangle)
    finally:
        app.destroy()


def test_drag_rectangle_vertex_promotes_to_polygon():
    from hlayout.core.shapes import Polygon
    app, top = _make_app()
    try:
        rect = top.add_rectangle(0, 0, 0, 10000, 10000)
        app.canvas.redraw()

        app.canvas._select_click(_ev(*app.canvas.to_px(0, 0)))   # bottom-left corner
        assert app.canvas.selected[0] == "vertex"

        target = app.canvas.to_px(-2000, -2000)
        app.canvas._on_drag_motion(_ev(*target))
        app.canvas._on_release(_ev(*target))

        poly = top.shapes[0]
        assert isinstance(poly, Polygon)
        assert poly.points[0] == (-2000, -2000)
        assert poly.points[1] == (10000, 0)             # neighbours unmoved -> a real quad
    finally:
        app.destroy()


def test_insert_vertex_on_rectangle_edge_promotes_and_adds_a_point():
    from hlayout.core.shapes import Polygon
    app, top = _make_app()
    try:
        top.add_rectangle(0, 0, 0, 10000, 10000)
        app.canvas.redraw()

        app.canvas._select_click(_ev(*app.canvas.to_px(5000, 0)))
        assert app.canvas.selected[0] == "edge"

        assert app.canvas.insert_vertex_on_selected_edge() is True
        poly = top.shapes[0]
        assert isinstance(poly, Polygon)
        assert len(poly.points) == 5 and poly.points[1] == (5000, 0)
    finally:
        app.destroy()


# --- Edit > Continue Path... -------------------------------------------------

def test_continue_path_extends_existing_path_as_one_undo_step():
    app, top = _make_app()
    try:
        top.add_path(1, [(0, 0), (10000, 0)], width=400, cap="round")
        app.canvas.redraw()
        app.session.clear_undo_history()
        path = app.session.library.cells["TOP"].shapes[0]

        app.canvas.selected = ("shape", path)
        app.canvas.selected_multi = []

        assert app.canvas.continue_path() is True
        assert app.canvas.draw_mode == "path"
        assert app.canvas._pending_points == [(0, 0), (10000, 0)]
        assert app.canvas._continue_target is path
        assert app.mode_var.get() == "path"  # toolbar's mode picker stays in sync

        new_pt_px = app.canvas.to_px(20000, 0)
        app.canvas._on_click(_ev(*new_pt_px))
        app.canvas.finish_draw()

        cell = app.session.library.cells["TOP"]
        assert len(cell.shapes) == 1
        extended = cell.shapes[0]
        assert extended is not path  # a new shape, not the old one mutated in place
        assert extended.points == [(0, 0), (10000, 0), (20000, 0)]
        assert extended.width == 400
        assert extended.layer == 1

        # One undo restores the original, unextended path -- not some
        # halfway state with the original gone and nothing in its place.
        assert app.session.can_undo() is True
        assert app.session.undo() is True
        cell = app.session.library.cells["TOP"]
        assert len(cell.shapes) == 1
        assert cell.shapes[0].points == [(0, 0), (10000, 0)]
        assert cell.shapes[0].width == 400
    finally:
        app.destroy()


def test_continue_path_cancel_leaves_original_path_untouched():
    app, top = _make_app()
    try:
        path = top.add_path(1, [(0, 0), (10000, 0)], width=400, cap="round")
        app.canvas.redraw()
        app.canvas.selected = ("shape", path)
        app.canvas.selected_multi = []

        assert app.canvas.continue_path() is True
        new_pt_px = app.canvas.to_px(20000, 0)
        app.canvas._on_click(_ev(*new_pt_px))

        app.canvas.cancel_draw()  # Esc

        assert app.canvas._continue_target is None
        assert app.canvas._pending_points == []
        assert path in top.shapes
        assert path.points == [(0, 0), (10000, 0)]
        assert len(top.shapes) == 1
    finally:
        app.destroy()


def test_continue_path_requires_a_whole_path_selected():
    app, top = _make_app()
    try:
        poly = top.add_polygon(0, [(0, 0), (10000, 0), (10000, 10000), (0, 10000)])
        app.canvas.redraw()
        app.canvas.selected = ("shape", poly)
        assert app.canvas.continue_path() is False

        app.canvas.selected = None
        assert app.canvas.continue_path() is False
    finally:
        app.destroy()
