import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core import Library, LayerError
from hlayout.scripting import Session, ScriptConsole
from hlayout.gui.commands import CommandInterpreter


def test_layer_table_resolve_by_number_and_name():
    lib = Library()
    lib.layers.set_style(5, name="Thick-Film Conductor")
    assert lib.layers.resolve(5) == 5
    assert lib.layers.resolve("5") == 5
    assert lib.layers.resolve("Thick-Film Conductor") == 5
    assert lib.layers.resolve("thick-film conductor") == 5  # case-insensitive fallback
    with pytest.raises(LayerError):
        lib.layers.resolve("NoSuchLayer")
    with pytest.raises(LayerError):
        lib.layers.resolve(999)


def test_session_shapes_accept_layer_name():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.set_layer_style(0, name="Conductor")
    s.set_layer_style(1, name="Resistor")

    s.rect("Conductor", 0, 0, 10, 10)
    s.polygon("Resistor", [(0, 0), (5, 0), (2, 5)])
    assert s.cell.shapes[0].layer == 0
    assert s.cell.shapes[1].layer == 1
    assert len(s.shapes_on_layer("Conductor")) == 1


def test_command_bar_uses_layer_names():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    assert "layer" in interp.run("LAYER 3 name=Silkscreen")
    assert interp.run("RECT Silkscreen 0,0 10,10") == "rectangle added"
    assert s.cell.shapes[0].layer == 3

    result = interp.run("RECT NoSuchLayer 0,0 1,1")
    assert result.startswith("error")
