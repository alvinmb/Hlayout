import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")

from hlayout.core.library import Library
from hlayout.gui.canvas import describe_selection
from hlayout.gui.commands import CommandInterpreter
from hlayout.scripting import Session
from hlayout.scripting.console import ScriptConsole


def _make_app():
    from hlayout.gui.app import App
    lib = Library("PROPSTEST")
    lib.new_cell("TOP")
    try:
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    return app


def test_describe_selection_reports_nothing_selected():
    app = _make_app()
    try:
        assert describe_selection(app.canvas) == "Nothing selected."
    finally:
        app.destroy()


def test_describe_selection_reports_path_layer_and_width():
    app = _make_app()
    try:
        s = app.session
        s.path(2, [(0, 0), (100, 0)], width=15, cap="round")
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", shape)
        desc = describe_selection(app.canvas)
        assert "Path" in desc
        assert "layer=2" in desc
        assert "width=15" in desc
        assert "cap=round" in desc
    finally:
        app.destroy()


def test_describe_selection_reports_polygon_layer():
    app = _make_app()
    try:
        s = app.session
        s.polygon(3, [(0, 0), (10, 0), (5, 10)])
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", shape)
        desc = describe_selection(app.canvas)
        assert "Polygon" in desc
        assert "layer=3" in desc
        assert "points=3" in desc
    finally:
        app.destroy()


def test_describe_selection_reports_rectangle_layer():
    app = _make_app()
    try:
        s = app.session
        s.rect(1, 0, 0, 100, 50)
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", shape)
        desc = describe_selection(app.canvas)
        assert "Rectangle" in desc
        assert "layer=1" in desc
    finally:
        app.destroy()


def test_describe_selection_reports_sref_cell_name_and_transform():
    app = _make_app()
    try:
        s = app.session
        s.new_cell("SUB")
        s.rect(0, 0, 0, 10, 10)
        s.use_cell("TOP")
        s.sref("SUB", (50, 60), rotation=90, magnification=2.0, x_reflection=True)
        ref = s.cell.srefs[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("sref", ref)
        desc = describe_selection(app.canvas)
        assert "Cell Instance" in desc
        assert "cell='SUB'" in desc
        assert "50,60" in desc
        assert "rotation=90" in desc
        assert "magnification=2" in desc
        assert "mirror=yes" in desc
    finally:
        app.destroy()


def test_describe_selection_reports_layer_name_when_named():
    app = _make_app()
    try:
        s = app.session
        s.library.layers.set_style(4, name="METAL2")
        s.rect(4, 0, 0, 10, 10)
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", shape)
        desc = describe_selection(app.canvas)
        assert "METAL2" in desc
    finally:
        app.destroy()


def test_describe_selection_reports_vertex_and_edge():
    app = _make_app()
    try:
        s = app.session
        s.polygon(0, [(0, 0), (10, 0), (5, 10)])
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("vertex", shape, 1)
        desc = describe_selection(app.canvas)
        assert "vertex #1" in desc
        assert "Polygon" in desc

        app.canvas.selected = ("edge", shape, (0, 1))
        desc = describe_selection(app.canvas)
        assert "edge #0-1" in desc
    finally:
        app.destroy()


def test_describe_selection_reports_window_selected_group():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 10, 10)
        s.polygon(1, [(0, 0), (10, 0), (5, 10)])
        cell = s.cell
        app.canvas.draw_mode = "select"
        app.canvas.selected_multi = [("shape", cell.shapes[0]), ("shape", cell.shapes[1])]
        desc = describe_selection(app.canvas)
        assert "2 object(s) selected" in desc
        assert "Rectangle" in desc
        assert "Polygon" in desc
    finally:
        app.destroy()


def test_selecting_a_shape_refreshes_the_properties_panel_text():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 100, 50)
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", shape)
        app.canvas.on_select()
        text = app.props_text.get("1.0", "end").strip()
        assert "Rectangle" in text
    finally:
        app.destroy()


def test_props_command_reports_selection():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.rect(0, 0, 0, 10, 10)

    class FakeView:
        library = s.library
        selected = ("shape", s.cell.shapes[0])
        selected_multi = []

    interp = CommandInterpreter(s, ScriptConsole(s), view=FakeView())
    out = interp.run("PROPS")
    assert "Rectangle" in out


def test_props_command_requires_a_view():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("PROPS")
    assert "error" in out.lower()
