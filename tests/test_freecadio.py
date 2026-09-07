import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.errors import LayoutError
from hlayout.core.library import Library
from hlayout.io.freecadio import write_freecad_macro
from hlayout.io.techfile import (
    default_layer_thickness,
    load_component_models,
    load_layer_thickness,
    save_component_models,
    save_layer_thickness,
)
from hlayout.scripting import Session


def _design():
    """A tiny hybrid: a substrate slab, two conductor pads on L5, a
    resistor ink bar on L6, and a placed component cell (outline on the
    non-film L20)."""
    lib = Library("HY")
    lib.layers.set_style(4, name="Substrate", fill=True, visible=True)
    lib.layers.set_style(5, name="Conductor", fill=True, visible=True)
    lib.layers.set_style(6, name="ResInk", fill=True, visible=True)
    lib.layers.set_style(20, name="Outline", fill=False, visible=True)

    part = lib.new_cell("CHIP")
    part.add_path(20, [(-500, -500), (500, -500), (500, 500), (-500, 500), (-500, -500)], width=20)
    part.add_rectangle(5, -400, -400, -200, 400, pin="1")   # its pads are real film

    top = lib.new_cell("TOP")
    top.add_rectangle(4, -5000, -5000, 5000, 5000)          # substrate
    top.add_rectangle(5, -2000, -200, -1000, 200, pin="1")
    top.add_rectangle(5, 1000, -200, 2000, 200, pin="2")
    top.add_rectangle(6, -1000, -150, 1000, 150)            # resistor ink
    top.add_sref("CHIP", (0, 3000))
    lib.top_cell_name = "TOP"
    return lib


# --- techfile: layer_thickness / component_models ------------------------

def test_default_layer_thickness_covers_drawn_filled_layers():
    lib = _design()
    t = default_layer_thickness(lib)
    assert t["4"] == 635          # named "Substrate"
    assert set(("5", "6")) <= set(t)
    assert "20" not in t          # outline layer: not filled -> non-physical


def test_layer_thickness_round_trips():
    lib = Library("X")
    save_layer_thickness(lib, {"5": 12.5, "6": 15, "9": 0, "x": "bad"})
    got = load_layer_thickness(lib)
    assert got == {"5": 12.5, "6": 15.0}   # 0 and malformed dropped


def test_component_models_round_trip():
    lib = Library("X")
    save_component_models(lib, {
        "CHIP": {"mode": "model", "model_path": "p/chip.step", "height_um": 300},
        "CAP": {"mode": "bogus"},
    })
    got = load_component_models(lib)
    assert got["CHIP"]["mode"] == "model" and got["CHIP"]["model_path"] == "p/chip.step"
    assert got["CAP"]["mode"] == "extrude"     # bad mode normalised


# --- the macro ---------------------------------------------------------------

def test_write_freecad_macro_is_valid_python_with_stacked_solids(tmp_path):
    lib = _design()
    path = str(tmp_path / "hy.FCMacro")
    write_freecad_macro(lib, "TOP", path)
    src = open(path).read()
    compile(src, path, "exec")               # valid Python

    # one Part.Face(...).extrude(...) per merged film polygon, at least
    # one per drawn film layer
    assert src.count(".extrude(") >= 3
    assert "# --- layer 4 (Substrate)" in src
    assert "# --- layer 5 (Conductor)" in src
    assert "# --- layer 6 (ResInk)" in src
    # the CHIP instance is a component (draws on non-film L20) -> a body
    assert "# --- component CHIP" in src
    # colours passed to _add
    assert "_add(" in src and "ViewObject.ShapeColor" in src


def test_macro_stacks_layers_in_number_order_by_thickness(tmp_path):
    lib = _design()
    save_layer_thickness(lib, {"4": 100, "5": 10, "6": 20})
    path = str(tmp_path / "z.FCMacro")
    write_freecad_macro(lib, "TOP", path)
    src = open(path).read()
    # z ranges appear in the layer header comments, cumulative & ordered
    import re
    zs = re.findall(r"# --- layer \d+ \([^)]*\) : z ([\d.]+)\.\.([\d.]+) mm", src)
    assert zs[0] == ("0.0000", "0.1000")     # L4: 100 um
    assert zs[1] == ("0.1000", "0.1100")     # L5: +10 um
    assert zs[2] == ("0.1100", "0.1300")     # L6: +20 um


def test_macro_emits_a_wire_bond_loop(tmp_path):
    pytest.importorskip("shapely")
    from hlayout.io.techfile import save_bond_rules
    s = Session.new("B")
    s.new_cell("TOP")
    s.rect(5, 0, 0, 200, 200, pin="A")
    s.rect(5, 3000, 0, 3200, 200, pin="B")
    s.library.layers.set_style(5, name="Cond", fill=True, visible=True)
    save_bond_rules(s.library, {"layer": 6, "width": 30, "pad": 100,
                                "min_length": 200, "max_length": 5000})
    s.library.layers.set_style(6, name="Bond", fill=True, visible=True)
    s.wire_bond([(100, 100), (3100, 100)])

    path = str(tmp_path / "wb.FCMacro")
    s.export_freecad(path)
    src = open(path).read()
    compile(src, path, "exec")
    assert "_bondwire(" in src
    assert "wire_bonds" in src


def test_write_freecad_macro_rejects_a_bad_cell(tmp_path):
    lib = _design()
    with pytest.raises(LayoutError, match="no cell named"):
        write_freecad_macro(lib, "NOPE", str(tmp_path / "x.FCMacro"))


def test_freecad_command_and_session_method(tmp_path):
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = Session(_design())
    s.use_cell("TOP")
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run(f"FREECAD {tmp_path / 'via_cmd.FCMacro'}")
    assert "written to" in out
    p2 = s.export_freecad(str(tmp_path / "via_api.FCMacro"))
    compile(open(p2).read(), p2, "exec")
