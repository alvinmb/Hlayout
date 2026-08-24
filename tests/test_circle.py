import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core import GeometryError, Library, Polygon
from hlayout.scripting import Session, ScriptConsole
from hlayout.gui.commands import CommandInterpreter

tk = pytest.importorskip("tkinter")


def test_circle_points_segment_choices():
    from hlayout.core import circle_points
    for n in (8, 16, 32, 64):
        pts = circle_points((0, 0), 100, n)
        assert len(pts) == n

    with pytest.raises(GeometryError):
        circle_points((0, 0), 100, 10)  # not one of the allowed choices


def test_cell_add_circle_is_a_polygon():
    lib = Library()
    cell = lib.new_cell("TOP")
    shape = cell.add_circle(0, (0, 0), 50, segments=16)
    assert isinstance(shape, Polygon)
    assert len(shape.points) == 16


def test_session_circle_accepts_layer_name():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.set_layer_style(0, name="Pads")
    shape = s.circle("Pads", (10, 10), 5, segments=8)
    assert shape.layer == 0
    assert len(shape.points) == 8


def test_command_bar_circle():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)
    result = interp.run("CIRCLE 0 0,0 50 64")
    assert "64 segments" in result
    assert len(s.cell.shapes[0].points) == 64

    bad = interp.run("CIRCLE 0 0,0 50 10")
    assert bad.startswith("error")


def _make_app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    try:
        lib = Library("CIRCLE")
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


def test_click_draw_circle_two_clicks():
    app = _make_app()
    try:
        app.mode_var.set("circle")
        app._on_mode_change()
        app.segments_var.set("16")
        app._on_circle_opts_change()

        center_px = app.canvas.to_px(0, 0)
        edge_px = app.canvas.to_px(50, 0)
        app.canvas._on_click(_ev(*center_px))
        app.canvas._on_click(_ev(*edge_px))

        assert len(app.session.cell.shapes) == 1
        shape = app.session.cell.shapes[0]
        assert isinstance(shape, Polygon)
        assert len(shape.points) == 16
    finally:
        app.destroy()
