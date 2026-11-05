# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Headless test of mouse-driven drawing/select/move/delete on the canvas.

Requires a display (real or Xvfb) since it drives an actual Tk canvas
widget. Skips automatically if Tk can't open a display.
"""
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
        lib = Library("MOUSE")
        lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.canvas.fit_view()
    app.update()
    return app


def _ev(x, y):
    return types.SimpleNamespace(x=x, y=y)


def test_click_draw_rect_poly_path():
    app = _make_app()
    try:
        app.mode_var.set("rect")
        app._on_mode_change()
        app.canvas._on_click(_ev(400, 300))
        app.canvas._on_click(_ev(500, 400))
        assert len(app.session.cell.shapes) == 1

        app.mode_var.set("poly")
        app._on_mode_change()
        app.canvas._on_click(_ev(200, 200))
        app.canvas._on_click(_ev(250, 200))
        app.canvas._on_click(_ev(225, 260))
        app.canvas.finish_draw()
        assert len(app.session.cell.shapes) == 2

        app.mode_var.set("path")
        app._on_mode_change()
        app.canvas._on_click(_ev(100, 100))
        app.canvas._on_click(_ev(150, 150))
        app.canvas.finish_draw()
        assert len(app.session.cell.shapes) == 3
    finally:
        app.destroy()


def test_undo_while_drawing_a_path_drops_the_last_point_not_a_design_edit():
    app = _make_app()
    try:
        app.session.rect(0, 0, 0, 10, 10)          # something already in the design
        app.session.clear_undo_history()

        app.mode_var.set("path")
        app._on_mode_change()
        app.canvas._on_click(_ev(100, 100))
        app.canvas._on_click(_ev(150, 150))
        app.canvas._on_click(_ev(200, 120))
        assert len(app.canvas._pending_points) == 3

        app._undo()                                 # Ctrl+Z / Edit > Undo
        assert len(app.canvas._pending_points) == 2
        assert len(app.session.cell.shapes) == 1    # the rect is untouched

        app._undo()
        app._undo()
        assert app.canvas._pending_points == []
        assert len(app.session.cell.shapes) == 1

        # nothing pending now -> a real session undo
        app._undo()
        assert app.status.get() == "nothing to undo"
    finally:
        app.destroy()


def test_select_drag_and_delete():
    app = _make_app()
    try:
        app.mode_var.set("rect")
        app._on_mode_change()
        app.canvas._on_click(_ev(400, 300))
        app.canvas._on_click(_ev(500, 400))
        rect_shape = app.session.cell.shapes[0]
        orig_points = list(rect_shape.points)

        app.mode_var.set("select")
        app._on_mode_change()
        app.canvas._select_click(_ev(450, 350))
        assert app.canvas.selected is not None
        assert app.canvas.selected[1] is rect_shape

        app.canvas._on_drag_motion(_ev(470, 370))
        app.canvas._on_release(_ev(470, 370))
        assert rect_shape.points != orig_points

        app.canvas.delete_selected()
        assert rect_shape not in app.session.cell.shapes
    finally:
        app.destroy()


def test_click_selects_a_small_part_inside_a_big_container_instance():
    # A big board/package cell placed as an SRef used to blanket every
    # small part inside its bounding box with an invisible full-bbox
    # hit-region, so clicking a resistor selected the board -- even worse
    # when the part's own geometry is unfilled outline (only its
    # hit-region sits under an interior click). _pick_item_at now takes
    # the smallest-bbox item under the cursor, so the small part wins.
    app = _make_app()
    try:
        s = app.session
        lib = s.library
        lib.layers.set_style(1, name="Outline", fill=False, visible=True)
        # resistor drawn OUTLINE-ONLY, like the screenshot that reported this
        lib.layers.set_style(2, name="Ink", fill=False, visible=True)

        s.new_cell("BOARD")
        s.path(1, [(-20000, -20000), (20000, -20000), (20000, 20000),
                   (-20000, 20000), (-20000, -20000)], 50)

        s.new_cell("R1")
        s.rect(2, -800, -300, 800, 300)

        s.use_cell("TOP")
        s.sref("BOARD", (0, 0))            # big, placed FIRST
        r1_ref = s.sref("R1", (5000, 0))   # small, sitting inside the board

        app.canvas.set_cell("TOP")
        app.canvas.fit_view()
        app.update()

        # click well inside R1's (unfilled) body, off its origin/label,
        # so only R1's and the board's hit-regions are under the cursor
        app.canvas._select_click(_ev(*app.canvas.to_px(5400, 150)))
        assert app.canvas.selected is not None
        assert app.canvas.selected[0] == "sref"
        assert app.canvas.selected[1] is r1_ref

        # bare board area (no part there) still selects the board
        app.canvas._select_click(_ev(*app.canvas.to_px(-12000, 12000)))
        assert app.canvas.selected[0] == "sref"
        assert app.canvas.selected[1].cell_name == "BOARD"
    finally:
        app.destroy()
