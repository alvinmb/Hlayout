"""Thick-film resistor shapes (hlayout.core.tf_shapes): geometry, sheet
solve, sizing, trim, drawing, DRC and the Resistor Designer."""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip("scipy")

from hlayout.core import tf_shapes as T
from hlayout.core.resistor_drc import check_all_resistors
from hlayout.io.techfile import (load_resistor_geometry_rules, load_resistor_list,
                                 save_resistor_geometry_rules, save_resistor_list)
from hlayout.scripting import Session

W, A, B, C = 1400.0, 300.0, 500.0, 300.0
SHAPED = [k for k in T.SHAPE_KEYS if k != "rect"]


@pytest.mark.parametrize("length", [333, 700, 1120, 2800])
def test_rectangle_is_exactly_L_over_W(length):
    sq = T.squares(T.build("rect", W, length, A, B, C))
    assert sq == pytest.approx(length / W, rel=2e-3)


def test_a_right_angle_corner_counts_about_half_a_square():
    sq = T.squares(T.build("angle", W, 2800, A, B, C), grid_per_w=40)
    corner = sq - 2 * 2800 / W
    assert 0.53 < corner < 0.60          # the classic ~0.56 squares


@pytest.mark.parametrize("shape", T.SHAPE_KEYS)
def test_sizing_hits_the_target(shape):
    p = T.default_params(shape, W, C)
    lo = T.squares(T.build(shape, W, T.min_length(shape, W, p), A, B, C))
    target = lo * 1.6
    g = T.size(shape, W, target, A, B, C)
    assert g is not None
    assert T.squares(g) == pytest.approx(target, rel=2e-3)
    assert {pin for _r, pin in g.pads} == {"1", "2"}


def test_target_outside_the_range_gives_none():
    assert T.size("u", W, 0.5, A, B, C) is None            # a U can't be that short
    assert T.size("angle", W, 500.0, A, B, C, max_length=4 * W) is None


@pytest.mark.parametrize("shape", ["tophat_roof", "u_roof"])
def test_a_roof_keeps_the_trim_from_opening(shape):
    g = T.size(shape, W, 4.0, A, B, C)
    steps = T.simulate_trim(g, 1000.0, kerf=40, steps=12)
    assert not steps[-1].is_open
    assert steps[-1].resistance > steps[0].resistance * 1.2


def test_a_top_hat_without_roof_opens_if_cut_through_the_crown():
    g = T.size("tophat", W, 3.0, A, B, C)
    deep = W + g.params["crown_h"] + 50
    steps = T.simulate_trim(g, 1000.0, depth=deep, kerf=40, steps=10)
    assert steps[-1].is_open


def test_rectangle_trim_rises_like_the_rectangle_simulator():
    g = T.build("rect", W, 1120, A, B, C)
    steps = T.simulate_trim(g, 1000.0, depth=350, kerf=40, steps=10)
    assert steps[0].resistance == pytest.approx(800, rel=0.01)
    assert 890 < steps[-1].resistance < 960          # trim_sim gives ~913


def _session():
    s = Session.new("SHAPES")
    save_resistor_geometry_rules(s.library, {
        "overlap_a": A, "pad_b": B, "space_c": C, "glaze_oversize": 300.0,
        "resistor_layer": 10, "conductor_layer": 3, "glaze_layer": 19,
        "keepout_layer": 4, "keepout_oversize": 500.0,
    })
    s.new_cell("TOP")
    return s


@pytest.mark.parametrize("shape", SHAPED)
def test_generate_draws_pins_roof_glaze_keepout_and_passes_drc(shape):
    s = _session()
    g = T.size(shape, W, 4.0 if shape != "angle3" else 7.0, A, B, C)
    name = s.generate_resistor(0, 0, W, g.length, ref_des="R1", shape=shape,
                               shape_params=g.params)
    cell = s.library.cells[name]
    cond = [sh for sh in cell.shapes if sh.layer == 3]
    assert sorted(sh.pin for sh in cond if sh.pin) == ["1", "2"]
    assert len([sh for sh in cond if not sh.pin]) == (1 if shape.endswith("_roof") else 0)
    assert any(sh.layer == 19 for sh in cell.shapes)
    assert any(sh.layer == 4 for sh in cell.shapes)
    rules = load_resistor_geometry_rules(s.library)
    res = check_all_resistors(s.library, rules)
    assert name in res.checked_cells
    assert res.violations == []


def test_drc_catches_a_short_pad_on_a_shaped_resistor():
    s = _session()
    g = T.size("angle", W, 4.0, A, B, C)
    name = s.generate_resistor(0, 0, W, g.length, shape="angle", pad_b=200.0)
    rules = load_resistor_geometry_rules(s.library)
    kinds = {v.kind for v in check_all_resistors(s.library, rules).violations}
    assert "PAD_LENGTH" in kinds


def test_resgen_command_shape_option():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _session()
    out = CommandInterpreter(s, ScriptConsole(s)).run(
        "RESGEN 0,0 1400 3000 ref=R9 shape=u_roof gap=1400 skirt_h=1400")
    assert "generated resistor" in out
    cell = s.library.cells[s.cell.srefs[-1].cell_name]
    assert len([sh for sh in cell.shapes if sh.layer == 3]) == 3


def test_designer_sizes_a_shape_and_remembers_it():
    from hlayout.gui.resistor_designer import DesignRules, Resistor
    s = _session()
    rules = DesignRules.from_library(s.library)
    assert rules.overlap_a == A and rules.space_c == C
    r = Resistor("R2", 8200, "5%", 50.0, 1400.0, shape="u")
    r.compute_solutions(rules)
    sol = r.selected
    assert sol is not None and sol.shape == "u" and sol.geom_code == "U"
    aim = r.aim_value(rules)
    assert sol.squares == pytest.approx(aim / sol.ink_rsh)
    assert T.squares(sol.geom) == pytest.approx(sol.squares, rel=3e-3)
    save_resistor_list(s.library, [{"ref_des": "R2", "value": 8200, "tolerance": "5%",
                                    "power_mw": 50.0, "body_width": 1400.0, "shape": "u",
                                    "selected_ink": sol.ink_rsh}])
    assert load_resistor_list(s.library)[0]["shape"] == "u"


def test_unknown_shape_is_an_error():
    with pytest.raises(T.ShapeError):
        T.build("serpentine", W, 1000, A, B, C)
