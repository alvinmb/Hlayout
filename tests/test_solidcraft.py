# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.scripting import Session
from hlayout.io import solidcraftio as S
from hlayout.io.solidcraft_mcp import SolidCraftClient, find_solidcraft


def _hybrid():
    """1st conductor (L3) with a hole, a die cell with a pad, one bond."""
    from hlayout.io.techfile import load_bond_rules, save_bond_rules, save_layer_thickness
    s = Session.new("SC")
    s.new_cell("TOP")
    s.rect(57, -5000, -5000, 5000, 5000)                       # substrate
    s.library.layers[57].name = "Substrate"
    s.cell.add_polygon(3, [(-4000, -4000), (4000, -4000), (4000, -3000), (-4000, -3000)])
    s.rect(3, 2000, -1000, 3000, 1000)
    save_layer_thickness(s.library, {"3": 12, "57": 635})
    br = load_bond_rules(s.library)
    br.update(layer=59, die_pad_layers=[58])
    save_bond_rules(s.library, br)
    s.create_chip_cell("DIE", 1500, 1500, graphics_layer=61, epoxy_length=1400,
                       epoxy_width=1400, epoxy_layer=20)
    s.library.cells["DIE"].add_rectangle(58, -700, -50, -600, 50, pin="1")
    s.use_cell("TOP")
    s.sref("DIE", (0, 0), label="U1")
    s.wire_bond([(-650, 0), (2500, 0)], layer=59, width=25, pad=75)
    return s


def test_build_batches_makes_substrate_films_parts_and_bonds():
    s = _hybrid()
    b = S.build_batches(s.library, "TOP")
    labels = [l for l, _c, _f in b]
    assert labels[0] == "Substrate"
    assert any(l.startswith("Layer 3") for l in labels)
    assert any(l.endswith("parts") for l in labels)
    assert labels[-1] == "1 wire bonds"
    sub = b[0][1]
    assert sub[0]["command"] == "component.create"
    assert sub[1]["params"]["offset"] == pytest.approx(-0.635)
    ext = [c for c in sub if c["command"] == "solid.extrude"][0]["params"]
    assert ext["distance"] == pytest.approx(0.635) and ext["operation"] == "new"
    l3 = [c for l, c, _f in b if l.startswith("Layer 3")][0]
    lines = [c for c in l3 if c["command"] == "sketch.line"]
    assert len(lines) == 2 and all(c["params"]["closed"] for c in lines)
    picks = [c for c in l3 if c["command"] == "solid.extrude"][0]["params"]["profiles"]
    assert len(picks) == 2 and all("point" in p for p in picks)
    parts = [c for l, c, _f in b if l.endswith("parts")][0]
    box = [c for c in parts if c["command"] == "solid.box"][0]["params"]
    assert box["body_name"] == "U1" and box["length"] == pytest.approx(1.5, abs=0.05)   # die outline
    bond = b[-1][1]
    arc = [c for c in bond if c["command"] == "sketch.arc.three_point"][0]["params"]
    assert arc["end"][0] == pytest.approx(3.15)                           # span in mm
    assert arc["through"][1] > max(0.0, arc["end"][1])                    # loops up
    assert json.dumps(b)                                                  # plain JSON


_APP, _CLI = find_solidcraft()


@pytest.mark.skipif(_CLI is None, reason="SolidCraft not installed")
def test_builds_headless_in_solidcraft(tmp_path):
    s = _hybrid()
    out = str(tmp_path / "h.stl")
    r = S.send_to_solidcraft(s.library, "TOP", window=False, export=out)
    assert r["errors"] == []
    assert r["bodies"] >= 5            # substrate, 2 copper, die, bond
    assert os.path.getsize(out) > 1000


def test_header_package_goes_under_the_substrate_and_header_bonds_are_wires():
    """A package enclosing the substrate (a header) is a base under the
    substrate, not a box burying the circuit; a die carrying its own
    bonds is die-thick; every layer named for bonds is piped as wire."""
    from hlayout.core.library import Library
    from hlayout.io.solidcraftio import build_batches
    from hlayout.io.techfile import save_layer_thickness
    lib = Library("t")
    top = lib.new_cell("TOP")
    lib.layers.set_style(1, name="Substrate", fill=True, visible=True)
    lib.layers.set_style(3, name="Conductor", fill=True, visible=True)
    lib.layers.set_style(60, name="Header_wire_Bond", fill=True, visible=True)
    lib.layers.set_style(59, name="Chip_wire_Bond", fill=True, visible=True)
    top.add_rectangle(1, 0, 0, 20000, 10000)
    top.add_rectangle(3, 1000, 1000, 3000, 3000)
    top.add_path(60, [(1500, -2000), (1500, 1500)], 50)
    pkg = lib.new_cell("40_PIN_pkg")
    pkg.add_path(61, [(-3000, -3000), (23000, -3000), (23000, 13000), (-3000, 13000),
                      (-3000, -3000)], 100)
    lib.new_cell("PIN").add_rectangle(49, -200, -200, 200, 200)
    pkg.add_sref("PIN", (1500, -2000))
    top.add_sref("40_PIN_pkg", (0, 0))
    die = lib.new_cell("DIE_X")
    die.add_rectangle(20, -700, -700, 700, 700)
    die.add_path(59, [(-500, 500), (-1500, 1500)], 25)
    top.add_sref("DIE_X", (10000, 5000), label="U1")
    save_layer_thickness(lib, {1: 635, 3: 12, 60: 25, 59: 25})
    batches = build_batches(lib, "TOP")
    labels = [b[0] for b in batches]
    assert not any("Header_wire_Bond" in l for l in labels)            # not a film
    assert any(l.endswith("wire bonds") and l.startswith("2 ") for l in labels)
    boxes = {c["params"]["body_name"]: c["params"] for b in batches for c in b[1]
             if c["command"] == "solid.box"}
    pk = boxes["40_PIN_pkg_1"]
    assert pk["corner"][2] + pk["height"] <= -0.635 + 1e-6             # under the substrate
    assert abs(boxes["U1"]["height"] - 0.38) < 1e-6


def test_freecad_macro_package_under_substrate_die_body_and_all_bonds(tmp_path):
    """The FreeCAD export: the header package is a base under the
    substrate, a die's box is its own body (not its fingers and bonds),
    die-thick, and every bond layer is wire, not film."""
    from hlayout.core.library import Library
    from hlayout.io.freecadio import write_freecad_macro
    from hlayout.io.techfile import save_layer_thickness
    lib = Library("t")
    top = lib.new_cell("TOP")
    lib.layers.set_style(1, name="Substrate", fill=True, visible=True)
    lib.layers.set_style(60, name="Header_wire_Bond", fill=True, visible=True)
    lib.layers.set_style(59, name="Chip_wire_Bond", fill=True, visible=True)
    top.add_rectangle(1, 0, 0, 20000, 10000)
    top.add_path(60, [(1500, -2000), (1500, 1500)], 50)
    pkg = lib.new_cell("40_PIN_pkg")
    pkg.add_path(61, [(-3000, -3000), (23000, -3000), (23000, 13000), (-3000, 13000),
                      (-3000, -3000)], 100)
    lib.new_cell("PIN").add_rectangle(49, -200, -200, 200, 200)
    pkg.add_sref("PIN", (1500, -2000))
    top.add_sref("40_PIN_pkg", (0, 0))
    die = lib.new_cell("DIE_X")
    die.add_rectangle(20, -700, -700, 700, 700)
    die.add_path(59, [(-500, 500), (-1500, 1500)], 25)
    die.add_rectangle(3, -1700, 1300, -1300, 1700)
    top.add_sref("DIE_X", (10000, 5000), label="U1")
    save_layer_thickness(lib, {1: 635, 3: 12, 60: 25, 59: 25})
    txt = open(write_freecad_macro(lib, "TOP", str(tmp_path / "m.FCMacro"))).read()
    assert "package 40_PIN_pkg (base under the substrate)" in txt
    assert "Header_wire_Bond" not in txt.split("# --- wire bonds")[0]   # not a film
    assert "extrude 0.3800 mm" in txt
    die_block = txt.split("# --- component DIE_X")[1].split("_add(")[0]
    assert "9.3" in die_block and "8.3" not in die_block                 # body only


def test_3d_stack_skips_empty_layers_and_header_pins_stand_on_the_base(tmp_path):
    """A layer with a thickness but nothing drawn takes no place in the
    stack, and a film off the substrate (a header pin) is a post from the
    substrate's underside up -- in both the SolidCraft and FreeCAD builds."""
    from hlayout.core.library import Library
    from hlayout.io.freecadio import write_freecad_macro
    from hlayout.io.solidcraftio import build_batches
    from hlayout.io.techfile import save_layer_thickness
    lib = Library("t")
    top = lib.new_cell("TOP")
    lib.layers.set_style(1, name="Substrate", fill=True, visible=True)
    top.add_rectangle(1, 0, 0, 20000, 10000)
    top.add_rectangle(3, 1000, 1000, 3000, 3000)
    top.add_rectangle(49, 1000, -3000, 1500, -2500)            # header pin, off the substrate
    save_layer_thickness(lib, {1: 635, 3: 12, 10: 200, 49: 10})   # 10: nothing drawn
    sk = {c["params"]["name"]: c["params"]["offset"] for b in build_batches(lib, "TOP")
          for c in b[1] if c["command"] == "sketch.create"}
    assert abs(sk["L49 L49 posts sketch"] + 0.635) < 1e-6
    txt = open(write_freecad_macro(lib, "TOP", str(tmp_path / "m.FCMacro"))).read()
    assert "layer 49 (L49) : z 0.0120..0.0220 mm" in txt
    assert "V(1.00000,-3.00000,-0.63500)" in txt


def test_freecad_enclosing_package_model_goes_under_the_substrate(tmp_path):
    """A header package given a STEP model is placed from the substrate's
    underside (less its floor, z_offset_um), not on top of the films."""
    from hlayout.core.library import Library
    from hlayout.io.freecadio import write_freecad_macro
    from hlayout.io.techfile import save_component_models, save_layer_thickness
    lib = Library("t")
    top = lib.new_cell("TOP")
    lib.layers.set_style(1, name="Substrate", fill=True, visible=True)
    top.add_rectangle(1, 0, 0, 20000, 10000)
    top.add_rectangle(3, 1000, 1000, 3000, 3000)
    pkg = lib.new_cell("PKG")
    pkg.add_path(61, [(-3000, -3000), (23000, -3000), (23000, 13000), (-3000, 13000),
                      (-3000, -3000)], 100)
    lib.new_cell("PIN").add_rectangle(49, -200, -200, 200, 200)
    pkg.add_sref("PIN", (1500, -2000))
    top.add_sref("PKG", (0, 0))
    save_layer_thickness(lib, {1: 635, 3: 12})
    save_component_models(lib, {"PKG": {"mode": "model", "model_path": "C:/m/K.step",
                                        "z_offset_um": -1000}})
    txt = open(write_freecad_macro(lib, "TOP", str(tmp_path / "m.FCMacro"))).read()
    line = [l for l in txt.splitlines() if "_place_model(" in l and "K.step" in l][0]
    assert "-1.63500" in line


def test_solidcraft_inserts_a_parts_step_model(tmp_path):
    """A part whose component model points at a STEP file is inserted with
    file.insert_step, placed at its origin (turned with the placement) --
    a header the substrate sits in from the substrate underside -- and its
    folder is reported so SolidCraft may read it."""
    from hlayout.core.library import Library
    from hlayout.io.solidcraftio import build_batches, model_files
    from hlayout.io.techfile import save_component_models, save_layer_thickness
    step = tmp_path / "K.step"
    step.write_text("ISO-10303-21;")
    cap = tmp_path / "C.step"
    cap.write_text("ISO-10303-21;")
    lib = Library("t")
    top = lib.new_cell("TOP")
    lib.layers.set_style(1, name="Substrate", fill=True, visible=True)
    top.add_rectangle(1, 0, 0, 20000, 10000)
    top.add_rectangle(3, 1000, 1000, 3000, 3000)
    pkg = lib.new_cell("PKG")
    pkg.add_path(61, [(-3000, -3000), (23000, -3000), (23000, 13000), (-3000, 13000),
                      (-3000, -3000)], 100)
    lib.new_cell("PIN").add_rectangle(49, -200, -200, 200, 200)
    pkg.add_sref("PIN", (1500, -2000))
    top.add_sref("PKG", (0, 0), label="J1")
    lib.new_cell("CAP").add_rectangle(62, -500, -250, 500, 250)
    top.add_sref("CAP", (2000, 2000), rotation=90.0, label="C1")
    save_layer_thickness(lib, {1: 635, 3: 12})
    save_component_models(lib, {
        "PKG": {"mode": "model", "model_path": str(step), "origin_um": [-3000, -3000]},
        "CAP": {"mode": "model", "model_path": str(cap)}})
    batches = build_batches(lib, "TOP")
    ins = {c["params"]["name"]: c["params"] for _l, cmds, _f in batches for c in cmds
           if c["command"] == "file.insert_step"}
    assert set(ins) == {"J1", "C1"} and ins["J1"]["path"] == str(step)
    assert "matrix" not in ins["J1"]
    place = {c["params"]["name"]: f["place"] for _l, cmds, f in batches for c in cmds
             if c["command"] == "file.insert_step"}
    m = place["J1"]
    assert m[0] == 1 and abs(m[3] + 3.0) < 1e-6 and abs(m[7] + 3.0) < 1e-6   # origin_um
    assert abs(m[11] + 0.635) < 1e-6                                          # underside
    c_ = place["C1"]
    assert abs(c_[0]) < 1e-9 and abs(c_[1] + 1) < 1e-9 and abs(c_[4] - 1) < 1e-9   # 90 deg
    assert abs(c_[3] - 2.0) < 1e-6 and abs(c_[11] - 0.012) < 1e-6           # on the film
    assert not any(c["command"] == "solid.box" for _l, cmds, _f in batches for c in cmds)
    assert sorted(model_files(batches)) == sorted([str(step), str(cap)])
    fins = [f for _l, cmds, f in batches if any(c["command"] == "file.insert_step" for c in cmds)]
    assert all(f and f["place"] for f in fins)          # placed again after the insert


def test_finish_places_the_inserted_occurrence():
    from hlayout.io.solidcraftio import _finish

    class SC:
        def __init__(self):
            self.calls = []

        def execute(self, command, **params):
            self.calls.append((command, params))

    sc = SC()
    m = [1, 0, 0, 10, 0, 1, 0, 20, 0, 0, 1, -0.635]
    _finish(sc, {"results": [{"active": 0}, {"component": 1, "occurrence": 2}, {}]}, {"place": m})
    assert sc.calls == [("occurrence.move", {"occurrence": 2, "matrix": m, "space": "world"})]


def test_header_model_pins_carry_the_bonds():
    """With a header STEP model the stand-in pin posts go, and the bond
    batch can be rebuilt once the model's pins are measured, each bond
    landing on its pin's top."""
    from hlayout.core.library import Library
    from hlayout.io.solidcraftio import _model_tops, build_batches
    from hlayout.io.techfile import save_component_models, save_layer_thickness
    import tempfile, os
    d = tempfile.mkdtemp()
    step = os.path.join(d, "K.step")
    open(step, "w").write("x")
    lib = Library("t")
    top = lib.new_cell("TOP")
    lib.layers.set_style(1, name="Substrate", fill=True, visible=True)
    lib.layers.set_style(60, name="Header_wire_Bond", fill=True, visible=True)
    top.add_rectangle(1, 0, 0, 20000, 10000)
    top.add_rectangle(3, 1000, 1000, 3000, 3000)
    top.add_path(60, [(1500, -2000), (1500, 1500)], 50)
    pkg = lib.new_cell("PKG")
    pkg.add_path(61, [(-3000, -3000), (23000, -3000), (23000, 13000), (-3000, 13000),
                      (-3000, -3000)], 100)
    pkg.add_polygon(49, [(1300, -2200), (1700, -2200), (1700, -1800), (1300, -1800)], pin="1")
    lib.new_cell("PIN").add_rectangle(63, -200, -200, 200, 200)
    pkg.add_sref("PIN", (1500, -2000), pin="1")
    top.add_sref("PKG", (0, 0), label="J1")
    save_layer_thickness(lib, {1: 635, 3: 12, 49: 10})
    save_component_models(lib, {"PKG": {"mode": "model", "model_path": step,
                                        "origin_um": [-3000, -3000]}})
    batches = build_batches(lib, "TOP")
    assert not any("off the substrate" in b[0] for b in batches)
    bond = [b for b in batches if b[0].endswith("wire bonds")][0]
    assert bond[2] and callable(bond[2]["rebond"])
    origin = [c["params"]["plane"]["origin"] for c in bond[1] if c["command"] == "sketch.create"]
    assert origin[0][2] < 0.1                             # first guess: no pin height known

    class SC:
        def call(self, tool, **kw):
            return {"bodies": [{"name": "pins (3)", "bbox": {"min": [4.27, 0.77, -6.0],
                                                             "max": [4.73, 1.23, 2.0]}},
                               {"name": "KOVAR", "bbox": {"min": [0, 0, -1], "max": [26, 16, 4]}}]}
    m = [1, 0, 0, -3.0, 0, 1, 0, -3.0, 0, 0, 1, -0.635]
    tops = _model_tops(SC(), [(["pins (3)", "KOVAR"], m)])
    assert len(tops) == 1 and abs(tops[0][4] - 1.365) < 1e-9          # pin top, placed
    cmds, n = bond[2]["rebond"](tops)
    origin = [c["params"]["plane"]["origin"] for c in cmds if c["command"] == "sketch.create"]
    assert n == 1 and abs(origin[0][2] - 1.365) < 1e-6    # the bond starts on the pin top


def test_bonds_are_gold_and_thick_enough_to_see(tmp_path):
    from hlayout.core.library import Library
    from hlayout.io.freecadio import write_freecad_macro
    from hlayout.io.solidcraftio import BOND_HEX, build_batches
    from hlayout.io.techfile import save_layer_thickness
    lib = Library("t")
    top = lib.new_cell("TOP")
    lib.layers.set_style(1, name="Substrate", fill=True, visible=True)
    lib.layers.set_style(59, name="Chip_wire_Bond", fill=True, visible=True)
    top.add_rectangle(1, 0, 0, 20000, 10000)
    top.add_rectangle(3, 1000, 1000, 3000, 3000)
    top.add_path(59, [(1500, 1500), (5000, 1500)], 25)
    save_layer_thickness(lib, {1: 635, 3: 12})
    cmds = [c for b in build_batches(lib, "TOP") if b[0].endswith("wire bonds") for c in b[1]]
    assert [c["params"]["diameter"] for c in cmds if c["command"] == "solid.pipe"] == [0.1]
    assert any(c["command"] == "appearance.assign" and c["params"]["color"] == BOND_HEX
               for c in cmds)
    thin = [c for b in build_batches(lib, "TOP", bond_diameter_um=0)
            if b[0].endswith("wire bonds") for c in b[1] if c["command"] == "solid.pipe"]
    assert thin[0]["params"]["diameter"] == 0.025
    txt = open(write_freecad_macro(lib, "TOP", str(tmp_path / "m.FCMacro"))).read()
    assert "0.05000)," in txt and "(0.8310,0.6860,0.2160)" in txt
