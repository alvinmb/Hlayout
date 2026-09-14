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
    assert got["CHIP"]["rotation_deg"] == 0.0  # default when not given


def test_component_models_round_trips_rotation_deg():
    lib = Library("X")
    save_component_models(lib, {"Q1": {"mode": "model", "model_path": "sot23.step",
                                        "rotation_deg": 90.0}})
    got = load_component_models(lib)
    assert got["Q1"]["rotation_deg"] == 90.0


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


def test_export_freecad_forwards_component_models_override(tmp_path):
    """Session.export_freecad()'s component_models kwarg (added for the
    auto-match feature) must reach write_freecad_macro() -- a matched
    cell gets a model-mode block (Part.Shape().read(...)), not the
    default flat extrude."""
    lib = _design()
    s = Session(lib)
    s.use_cell("TOP")
    step_path = str(tmp_path / "CHIP.step")
    open(step_path, "w").close()
    path = str(tmp_path / "override.FCMacro")
    s.export_freecad(path, component_models={"CHIP": {"mode": "model", "model_path": step_path}})
    src = open(path).read()
    compile(src, path, "exec")
    assert "# --- component CHIP (model) ---" in src
    assert step_path.replace("\\", "\\\\") in src or step_path in src


def test_export_freecad_applies_rotation_deg_on_top_of_placement_rotation(tmp_path):
    """A component_models entry's rotation_deg (the auto-matcher's
    orientation correction for a footprint whose pitch runs along the
    "swapped" axis -- see hlayout.io.component_match) must ADD to the
    placement's own rotation, not replace it."""
    lib = _design()
    lib.cells["TOP"].srefs[0].rotation = 45.0   # the CHIP instance
    s = Session(lib)
    s.use_cell("TOP")
    step_path = str(tmp_path / "CHIP.step")
    open(step_path, "w").close()
    path = str(tmp_path / "rotated.FCMacro")
    s.export_freecad(path, component_models={
        "CHIP": {"mode": "model", "model_path": step_path, "rotation_deg": 90.0}})
    src = open(path).read()
    compile(src, path, "exec")
    assert "App.Rotation(V(0,0,1), 135.0000)" in src


def test_export_freecad_model_mode_defaults_to_zero_rotation_correction(tmp_path):
    lib = _design()
    s = Session(lib)
    s.use_cell("TOP")
    step_path = str(tmp_path / "CHIP.step")
    open(step_path, "w").close()
    path = str(tmp_path / "no_correction.FCMacro")
    s.export_freecad(path, component_models={"CHIP": {"mode": "model", "model_path": step_path}})
    src = open(path).read()
    assert "App.Rotation(V(0,0,1), 0.0000)" in src


def test_export_freecad_names_repeat_instances_by_ref_designator(tmp_path):
    """Multiple placements of the SAME component cell must not all get the
    bare cell name as their FreeCAD object name -- FreeCAD disambiguates a
    repeated Part::Feature name by bumping its own trailing digits, which
    for a name like CAP_1210 makes several placements of one part look
    like several different EIA sizes in the tree. Each placement's own ref
    designator (SRef.label) must be used instead."""
    lib = _design()
    top = lib.cells["TOP"]
    top.add_sref("CHIP", (3000, 3000), label="C1")
    top.add_sref("CHIP", (6000, 3000), label="C2")
    s = Session(lib)
    s.use_cell("TOP")
    step_path = str(tmp_path / "CHIP.step")
    open(step_path, "w").close()
    path = str(tmp_path / "labeled.FCMacro")
    s.export_freecad(path, component_models={"CHIP": {"mode": "model", "model_path": step_path}})
    src = open(path).read()
    compile(src, path, "exec")
    assert "addObject('Part::Feature', 'C1_CHIP')" in src
    assert "addObject('Part::Feature', 'C2_CHIP')" in src
    # the original fixture's un-labelled CHIP instance still gets a name,
    # just not one that collides with the labelled ones
    assert "addObject('Part::Feature', 'CHIP_1')" in src


def test_export_freecad_names_repeat_unlabelled_instances_with_a_counter(tmp_path):
    """No ref designator at all (e.g. a quick script placement) still must
    not produce the bare cell name twice -- fall back to a per-cell counter
    instead of leaving FreeCAD to auto-renumber a colliding name."""
    lib = _design()
    top = lib.cells["TOP"]
    top.add_sref("CHIP", (3000, 3000))
    s = Session(lib)
    s.use_cell("TOP")
    step_path = str(tmp_path / "CHIP.step")
    open(step_path, "w").close()
    path = str(tmp_path / "unlabelled.FCMacro")
    s.export_freecad(path, component_models={"CHIP": {"mode": "model", "model_path": step_path}})
    src = open(path).read()
    compile(src, path, "exec")
    assert "addObject('Part::Feature', 'CHIP_1')" in src
    assert "addObject('Part::Feature', 'CHIP_2')" in src


def test_match_freecad_components_requires_step_dir(tmp_path):
    s = Session(_design())
    s.use_cell("TOP")
    with pytest.raises(LayoutError, match="step_dir"):
        s.match_freecad_components()


def test_match_freecad_components_end_to_end(tmp_path):
    """CHIP (the _design() fixture's non-film-outline component) has no
    size code in its name and no numbered pads, so it's correctly left
    unmatched -- this just confirms the Session wrapper actually reaches
    hlayout.io.component_match.build_match_report()."""
    s = Session(_design())
    s.use_cell("TOP")
    report = s.match_freecad_components(step_dir=str(tmp_path))
    assert [u.cell_name for u in report.unmatched] == ["CHIP"]


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


def test_setup_layer_thickness_dialog_saves(tmp_path):
    tk = pytest.importorskip("tkinter")
    from hlayout.gui.app import App
    from hlayout.io.techfile import load_layer_thickness
    import tkinter.ttk as ttk

    lib = _design()
    try:
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display: {e}")
    try:
        app.update()
        app.show_layer_thickness_dialog()
        app.update()
        top = next(w for w in app.winfo_children() if isinstance(w, tk.Toplevel))

        entries = []
        def walk(w):
            for c in w.winfo_children():
                if isinstance(c, ttk.Entry):
                    entries.append(c)
                walk(c)
        walk(top)
        assert entries                       # one row per drawn/seeded layer
        # bump the first film to 33 um and OK
        entries[0].delete(0, "end"); entries[0].insert(0, "33")

        def find_btn(w, label):
            for c in w.winfo_children():
                if isinstance(c, ttk.Button) and c.cget("text") == label:
                    return c
                r = find_btn(c, label)
                if r:
                    return r
        find_btn(top, "OK").invoke()
        app.update()

        assert 33.0 in load_layer_thickness(app.session.library).values()
    finally:
        app.destroy()
