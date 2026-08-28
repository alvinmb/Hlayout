import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")

from hlayout.core.errors import CellError
from hlayout.core.shapes import Polygon, Rectangle, SRef


def _make_app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    try:
        lib = Library("MAKECELL")
        top = lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.canvas.fit_view()
    app.update()
    app.mode_var.set("select")
    app._on_mode_change()
    return app, top


def test_create_cell_from_two_shapes_rebases_geometry_to_the_origin():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 5000, 5000, 5100, 5100)
        b = top.add_rectangle(0, 5100, 5000, 5300, 5200)
        app.canvas.selected_multi = [("shape", a), ("shape", b)]

        assert app.canvas.create_cell_from_selection("PAD", (5000, 5000)) is True

        assert "PAD" in app.canvas.library.cells
        pad = app.canvas.library.cells["PAD"]
        assert len(pad.shapes) == 2
        assert a in pad.shapes
        assert b in pad.shapes
        assert a not in top.shapes
        assert b not in top.shapes
        # geometry re-based so (5000, 5000) becomes local (0, 0)
        xs = [p[0] for p in a.points] + [p[0] for p in b.points]
        ys = [p[1] for p in a.points] + [p[1] for p in b.points]
        assert min(xs) == 0
        assert min(ys) == 0
    finally:
        app.destroy()


def test_original_cell_gets_an_instance_in_place_of_the_selection():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 5000, 5000, 5100, 5100)
        app.canvas.selected_multi = [("shape", a)]

        app.canvas.create_cell_from_selection("PAD", (5000, 5000))

        assert len(top.srefs) == 1
        ref = top.srefs[0]
        assert ref.cell_name == "PAD"
        assert ref.origin == (5000, 5000)
        assert app.canvas.selected == ("sref", ref)
        assert app.canvas.selected_multi == []
    finally:
        app.destroy()


def test_visual_position_is_unchanged_after_wrapping_into_a_cell():
    """The point of MAKECELL/Create Cell from Selection is that nothing
    visually moves -- the new SRef placed at `origin` should reproduce
    the exact same absolute geometry the original shape had."""
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 5000, 5000, 5100, 5100)
        original_points = list(a.points)
        app.canvas.selected_multi = [("shape", a)]

        app.canvas.create_cell_from_selection("PAD", (5000, 5000))

        ref = top.srefs[0]
        ox, oy = ref.origin
        local_points = [p for p in app.canvas.library.cells["PAD"].shapes[0].points]
        reconstructed = [(px + ox, py + oy) for px, py in local_points]
        assert sorted(reconstructed) == sorted(original_points)
    finally:
        app.destroy()


def test_create_cell_undoes_cleanly():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 0, 0, 100, 100)
        app.canvas.selected_multi = [("shape", a)]

        app.canvas.create_cell_from_selection("PAD", (0, 0))
        assert "PAD" in app.canvas.library.cells
        assert len(top.shapes) == 0
        assert len(top.srefs) == 1

        app.session.undo()
        assert len(app.session.cell.shapes) == 1
        assert len(app.session.cell.srefs) == 0
        # the new cell itself is not retroactively un-created by undo
        # (Session.undo() restores the *current* cell's contents from
        # a Library snapshot; PAD still exists in the library) -- but
        # the design no longer references it, matching a real undo of
        # "the selection became a cell instance"
    finally:
        app.destroy()


def test_name_collision_raises_cell_error_and_leaves_selection_alone():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 0, 0, 100, 100)
        app.canvas.selected_multi = [("shape", a)]

        with pytest.raises(CellError):
            app.canvas.create_cell_from_selection("TOP", (0, 0))  # TOP already exists

        assert a in top.shapes  # untouched
    finally:
        app.destroy()


def test_nothing_selected_returns_false():
    app, top = _make_app()
    try:
        app.canvas.selected = None
        app.canvas.selected_multi = []
        assert app.canvas.create_cell_from_selection("PAD", (0, 0)) is False
    finally:
        app.destroy()


def test_vertex_selection_is_not_eligible():
    app, top = _make_app()
    try:
        poly = top.add_polygon(0, [(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
        app.canvas.selected = ("vertex", poly, 0)
        app.canvas.selected_multi = []
        assert app.canvas.create_cell_from_selection("PAD", (0, 0)) is False
    finally:
        app.destroy()


def test_command_bar_makecell():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 0, 0, 100, 100)
        app.canvas.selected_multi = [("shape", a)]
        out = app.interp.run("MAKECELL PAD 0,0")
        assert "created" in out.lower()
        assert "PAD" in app.canvas.library.cells

        app.canvas.selected = None
        app.canvas.selected_multi = []
        out2 = app.interp.run("MAKECELL PAD2 0,0")
        assert out2.startswith("error:")
    finally:
        app.destroy()


def test_sref_selection_can_also_become_a_cell():
    """Nesting a placed instance into a new cell -- e.g. wrapping a
    couple of placed SMD parts into a bigger reusable module cell."""
    app, top = _make_app()
    try:
        app.canvas.library.new_cell("PART")
        ref = top.add_sref("PART", (2000, 3000))
        app.canvas.selected_multi = [("sref", ref)]

        assert app.canvas.create_cell_from_selection("MODULE", (2000, 3000)) is True
        assert ref in app.canvas.library.cells["MODULE"].srefs
        assert ref not in top.srefs
        assert ref.origin == (0, 0)
    finally:
        app.destroy()
