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
