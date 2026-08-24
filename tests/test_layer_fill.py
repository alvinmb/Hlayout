import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core import Library
from hlayout.scripting import Session, ScriptConsole
from hlayout.gui.commands import CommandInterpreter


def test_layer_fill_defaults_true_and_is_settable():
    lib = Library()
    assert lib.layers[0].fill is True
    lib.layers.set_style(0, fill=False)
    assert lib.layers[0].fill is False
    lib.layers.set_style(0, fill=True)
    assert lib.layers[0].fill is True


def test_command_bar_sets_fill():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)
    assert "layer" in interp.run("LAYER 0 fill=0")
    assert s.library.layers[0].fill is False
    assert "layer" in interp.run("LAYER 0 fill=1")
    assert s.library.layers[0].fill is True
