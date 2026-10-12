"""View panning: canvas.pan_by/pan_to/pan_step, the Pan tool (left-drag),
Esc out of it, the PAN command and the View menu entries. Needs a real
Tk display; skips otherwise."""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture
def app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    try:
        lib = Library("PAN")
        lib.new_cell("TOP")
        a = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    a.geometry("900x700")
    a.update()
    a.canvas.zoom_to_window(0, 0, 10000, 10000)
    a.update()
    yield a
    a.destroy()


def _ev(x, y):
    return types.SimpleNamespace(x=x, y=y)


def _close(p, q, tol=1.0):
    return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol


def test_pan_by_and_pan_to_keep_scale(app):
    c = app.canvas
    scale = c.scale
    cx, cy = c.view_center()
    c.pan_by(1000, -500)
    assert _close(c.view_center(), (cx + 1000, cy - 500))
    c.pan_to(42000, 7000)
    assert _close(c.view_center(), (42000, 7000))
    assert c.scale == scale


def test_pan_step_moves_a_quarter_screen(app):
    c = app.canvas
    w = c.winfo_width() / c.scale
    h = c.winfo_height() / c.scale
    x0, y0 = c.view_center()
    c.pan_step("right")
    assert _close(c.view_center(), (x0 + w * 0.25, y0))
    c.pan_step("up", 0.5)
    assert _close(c.view_center(), (x0 + w * 0.25, y0 + h * 0.5))
    with pytest.raises(ValueError):
        c.pan_step("sideways")


def test_pan_tool_drags_the_view_and_esc_returns_to_select(app):
    c = app.canvas
    app._pick_mode("pan")
    assert c.draw_mode == "pan" and app.mode_var.get() == "pan"
    assert str(c.cget("cursor")) == "fleur"
    before = c.view_center()
    c._on_click(_ev(400, 300))
    c._on_drag_motion(_ev(500, 250))      # drag right 100 px, up 50 px
    c._on_release(_ev(500, 250))
    after = c.view_center()
    # dragging the design right/up moves the view left/down
    assert _close(after, (before[0] - 100 / c.scale, before[1] - 50 / c.scale), tol=2)
    c.cancel_draw()
    assert c.draw_mode == "select" and app.mode_var.get() == "select"
    assert str(c.cget("cursor")) == "crosshair"


def test_pan_command(app):
    from hlayout.gui.commands import CommandInterpreter
    interp = CommandInterpreter(app.session, console=None, view=app.canvas)
    out = interp.run("PAN TO 5000,6000")
    assert not out.startswith("error"), out
    assert _close(app.canvas.view_center(), (5000, 6000))
    interp.run("PAN 100 -200")
    assert _close(app.canvas.view_center(), (5100, 5800))
    assert not interp.run("PAN LEFT").startswith("error")
    assert interp.run("PAN").startswith("error")
    assert interp.run("PAN SIDEWAYS 3").startswith("error")


def test_view_menu_has_pan_entries(app):
    view = dict(app._top_menus)["View"]
    items = [view.entrycget(i, "label") for i in range(view.index("end") + 1)
             if view.type(i) != "separator"]
    assert "Pan" in items and "Pan Step" in items and "Pan to Point..." in items
