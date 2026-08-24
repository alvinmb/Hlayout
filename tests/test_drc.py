import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

shapely = pytest.importorskip("shapely")

from hlayout.core.drc import DRC_RULE_KINDS, DrcRule, DrcViolation, check_rule, run_drc
from hlayout.core.errors import GeometryError, LayoutError
from hlayout.io.techfile import load_drc_rules, save_drc_rules
from hlayout.scripting import Session


def _fresh_session(name="DRCTEST"):
    s = Session.new(name)
    s.new_cell("TOP")
    s.clear_undo_history()
    return s


# --- DrcRule validation ------------------------------------------------

def test_drc_rule_rejects_unknown_kind():
    with pytest.raises(GeometryError):
        DrcRule(kind="MIN_FOO", layer_a=0, value=10)


def test_drc_rule_two_layer_kinds_require_layer_b():
    with pytest.raises(GeometryError):
        DrcRule(kind="MIN_SPACE_BETWEEN", layer_a=0, value=10)
    with pytest.raises(GeometryError):
        DrcRule(kind="MIN_ENCLOSURE", layer_a=0, value=10)
    # Doesn't raise with layer_b given.
    DrcRule(kind="MIN_SPACE_BETWEEN", layer_a=0, layer_b=1, value=10)


def test_drc_rule_rejects_non_positive_value():
    with pytest.raises(GeometryError):
        DrcRule(kind="MIN_WIDTH", layer_a=0, value=0)
    with pytest.raises(GeometryError):
        DrcRule(kind="MIN_WIDTH", layer_a=0, value=-5)


def test_drc_rule_to_dict_and_from_dict_round_trip():
    rule = DrcRule(kind="MIN_ENCLOSURE", layer_a=0, layer_b=1, value=25.0, name="ENC1")
    data = rule.to_dict()
    restored = DrcRule.from_dict(data)
    assert restored == rule


def test_drc_rule_label_formats_one_and_two_layer_rules():
    r1 = DrcRule(kind="MIN_WIDTH", layer_a=3, value=50)
    assert r1.label == "MIN_WIDTH(layer 3) >= 50"
    r2 = DrcRule(kind="MIN_ENCLOSURE", layer_a=3, layer_b=5, value=10, name="ENC")
    assert r2.label == "ENC: MIN_ENCLOSURE(layer 3, layer 5) >= 10"


def test_all_kinds_are_constructible_with_appropriate_layers():
    for kind in DRC_RULE_KINDS:
        if kind in ("MIN_SPACE_BETWEEN", "MIN_ENCLOSURE"):
            DrcRule(kind=kind, layer_a=0, layer_b=1, value=1)
        else:
            DrcRule(kind=kind, layer_a=0, value=1)


# --- MIN_WIDTH -----------------------------------------------------------

def test_min_width_flags_a_narrow_neck():
    s = _fresh_session()
    # A dumbbell: two 40-wide lobes joined by a 5-wide neck.
    s.polygon(0, [(0, 0), (40, 0), (40, 17.5), (60, 17.5), (60, 0), (100, 0),
                  (100, 40), (60, 40), (60, 22.5), (40, 22.5), (40, 40), (0, 40)])
    violations = check_rule(s.library, s.cell, DrcRule("MIN_WIDTH", layer_a=0, value=20))
    assert len(violations) == 1
    x0, y0, x1, y1 = violations[0].bbox()
    assert x0 == pytest.approx(40, abs=1) and x1 == pytest.approx(60, abs=1)


def test_min_width_no_violation_for_a_uniformly_wide_shape():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    violations = check_rule(s.library, s.cell, DrcRule("MIN_WIDTH", layer_a=0, value=20))
    assert violations == []


def test_min_width_flags_the_whole_shape_when_entirely_too_narrow():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 5)
    violations = check_rule(s.library, s.cell, DrcRule("MIN_WIDTH", layer_a=0, value=20))
    assert len(violations) == 1


def test_min_width_empty_layer_has_no_violations():
    s = _fresh_session()
    violations = check_rule(s.library, s.cell, DrcRule("MIN_WIDTH", layer_a=0, value=20))
    assert violations == []


# --- MIN_AREA --------------------------------------------------------------

def test_min_area_flags_a_too_small_shape():
    s = _fresh_session()
    s.rect(0, 0, 0, 5, 5)
    violations = check_rule(s.library, s.cell, DrcRule("MIN_AREA", layer_a=0, value=100))
    assert len(violations) == 1
    assert violations[0].bbox() == (0, 0, 5, 5)


def test_min_area_no_violation_for_a_large_enough_shape():
    s = _fresh_session()
    s.rect(0, 0, 0, 20, 20)
    violations = check_rule(s.library, s.cell, DrcRule("MIN_AREA", layer_a=0, value=100))
    assert violations == []


def test_min_area_treats_touching_shapes_as_one_connected_component():
    s = _fresh_session()
    s.rect(0, 0, 0, 5, 10)
    s.rect(0, 5, 0, 10, 10)  # touches the first -- combined area 100 >= 100
    violations = check_rule(s.library, s.cell, DrcRule("MIN_AREA", layer_a=0, value=100))
    assert violations == []


# --- MIN_SPACE (single layer) -----------------------------------------------

def test_min_space_flags_two_shapes_too_close_together():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    s.rect(0, 15, 0, 25, 10)  # 5 DBU gap
    violations = check_rule(s.library, s.cell, DrcRule("MIN_SPACE", layer_a=0, value=10))
    assert len(violations) == 1


def test_min_space_no_violation_when_far_enough_apart():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    s.rect(0, 30, 0, 40, 10)  # 20 DBU gap
    violations = check_rule(s.library, s.cell, DrcRule("MIN_SPACE", layer_a=0, value=10))
    assert violations == []


def test_min_space_touching_shapes_are_not_a_violation():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    s.rect(0, 10, 0, 20, 10)  # exactly touching, distance 0
    violations = check_rule(s.library, s.cell, DrcRule("MIN_SPACE", layer_a=0, value=10))
    assert violations == []


def test_min_space_with_only_one_shape_has_no_violation():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    violations = check_rule(s.library, s.cell, DrcRule("MIN_SPACE", layer_a=0, value=10))
    assert violations == []


# --- MIN_SPACE_BETWEEN (two layers) -----------------------------------------

def test_min_space_between_flags_close_shapes_on_different_layers():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    s.rect(1, 15, 0, 25, 10)  # 5 DBU gap, different layer
    violations = check_rule(s.library, s.cell, DrcRule("MIN_SPACE_BETWEEN", layer_a=0, layer_b=1, value=10))
    assert len(violations) == 1


def test_min_space_between_overlapping_shapes_is_not_flagged():
    s = _fresh_session()
    s.rect(0, 0, 0, 20, 20)
    s.rect(1, 10, 10, 30, 30)  # overlaps -- distance 0
    violations = check_rule(s.library, s.cell, DrcRule("MIN_SPACE_BETWEEN", layer_a=0, layer_b=1, value=10))
    assert violations == []


def test_min_space_between_ignores_same_layer_pairs():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    s.rect(0, 12, 0, 22, 10)  # both on layer 0 -- MIN_SPACE_BETWEEN(0, 1) shouldn't see these
    violations = check_rule(s.library, s.cell, DrcRule("MIN_SPACE_BETWEEN", layer_a=0, layer_b=1, value=10))
    assert violations == []


# --- MIN_ENCLOSURE -----------------------------------------------------------

def test_min_enclosure_flags_insufficient_margin():
    s = _fresh_session()
    s.rect(0, 0, 0, 60, 60)     # outer metal
    s.rect(1, 45, 45, 55, 55)  # via -- only 5 DBU margin to outer's top/right edge
    violations = check_rule(s.library, s.cell, DrcRule("MIN_ENCLOSURE", layer_a=0, layer_b=1, value=10))
    assert len(violations) == 1


def test_min_enclosure_no_violation_with_enough_margin():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    s.rect(1, 45, 45, 55, 55)  # 45 DBU margin on every side
    violations = check_rule(s.library, s.cell, DrcRule("MIN_ENCLOSURE", layer_a=0, layer_b=1, value=10))
    assert violations == []


def test_min_enclosure_with_no_outer_geometry_at_all_flags_the_whole_inner_shape():
    s = _fresh_session()
    s.rect(1, 0, 0, 10, 10)  # inner shape with nothing on the outer layer
    violations = check_rule(s.library, s.cell, DrcRule("MIN_ENCLOSURE", layer_a=0, layer_b=1, value=5))
    assert len(violations) == 1


# --- flattening through placed cells (Srefs) --------------------------------

def test_drc_flattens_geometry_placed_via_sref():
    s = _fresh_session()
    s.new_cell("DEV")
    s.rect(0, 0, 0, 5, 5)  # too small, once placed
    s.use_cell("TOP")
    s.sref("DEV", (0, 0))
    violations = check_rule(s.library, s.cell, DrcRule("MIN_AREA", layer_a=0, value=100))
    assert len(violations) == 1
    assert violations[0].cell_name == "TOP"


def test_drc_flattens_rotated_sref_geometry():
    s = _fresh_session()
    s.new_cell("DEV")
    s.rect(0, 0, 0, 100, 5)  # long, thin -- 5 wide either way it's rotated
    s.use_cell("TOP")
    s.sref("DEV", (0, 0), rotation=90.0)
    violations = check_rule(s.library, s.cell, DrcRule("MIN_WIDTH", layer_a=0, value=10))
    assert len(violations) == 1


def test_drc_flattens_min_space_between_across_two_different_placed_cells():
    s = _fresh_session()
    s.new_cell("A_DEV")
    s.rect(0, 0, 0, 10, 10)
    s.new_cell("B_DEV")
    s.rect(1, 0, 0, 10, 10)
    s.use_cell("TOP")
    s.sref("A_DEV", (0, 0))
    s.sref("B_DEV", (15, 0))  # 5 DBU gap between the two placed shapes
    violations = check_rule(s.library, s.cell,
                             DrcRule("MIN_SPACE_BETWEEN", layer_a=0, layer_b=1, value=10))
    assert len(violations) == 1


# --- run_drc() (multi-rule, multi-cell) -------------------------------------

def test_run_drc_runs_every_rule_against_every_named_cell():
    s = _fresh_session()
    s.rect(0, 0, 0, 5, 5)     # MIN_AREA violation
    s.rect(1, 0, 0, 10, 10)
    s.rect(1, 15, 0, 25, 10)  # MIN_SPACE violation
    rules = [
        DrcRule("MIN_AREA", layer_a=0, value=100),
        DrcRule("MIN_SPACE", layer_a=1, value=10),
    ]
    violations = run_drc(s.library, rules, cell_names=["TOP"])
    assert len(violations) == 2
    kinds = {v.rule.kind for v in violations}
    assert kinds == {"MIN_AREA", "MIN_SPACE"}


def test_run_drc_defaults_to_every_cell_when_cell_names_omitted():
    s = _fresh_session()
    s.new_cell("OTHER")
    s.rect(0, 0, 0, 5, 5)
    violations = run_drc(s.library, [DrcRule("MIN_AREA", layer_a=0, value=100)])
    assert {v.cell_name for v in violations} == {"OTHER"}


# --- Session.run_drc() -------------------------------------------------------

def test_session_run_drc_defaults_to_the_current_cell():
    s = _fresh_session()
    s.new_cell("OTHER")
    s.rect(0, 0, 0, 5, 5)
    s.use_cell("TOP")
    violations = s.run_drc([DrcRule("MIN_AREA", layer_a=0, value=100)])
    assert violations == []  # TOP has nothing on layer 0; OTHER isn't checked


def test_session_run_drc_avoids_double_reporting_placed_cells_by_default():
    # Checking only the current cell (which flattens through its own
    # Srefs) means a resistor-style placed sub-cell isn't ALSO reported
    # separately just because its own device cell technically exists
    # in the library too.
    s = _fresh_session()
    s.new_cell("DEV")
    s.rect(0, 0, 0, 5, 5)
    s.use_cell("TOP")
    s.sref("DEV", (0, 0))
    violations = s.run_drc([DrcRule("MIN_AREA", layer_a=0, value=100)])
    assert len(violations) == 1
    assert violations[0].cell_name == "TOP"


def test_session_run_drc_requires_a_current_cell():
    s = Session.new("NOCELL")
    with pytest.raises(LayoutError):
        s.run_drc([DrcRule("MIN_AREA", layer_a=0, value=100)])


def test_session_run_drc_defaults_to_saved_technology_file_rules():
    s = _fresh_session()
    s.rect(0, 0, 0, 5, 5)
    save_drc_rules(s.library, [DrcRule("MIN_AREA", layer_a=0, value=100).to_dict()])
    violations = s.run_drc()  # rules=None -> load from tech_params
    assert len(violations) == 1


def test_session_run_drc_accepts_plain_dicts():
    s = _fresh_session()
    s.rect(0, 0, 0, 5, 5)
    violations = s.run_drc([{"kind": "MIN_AREA", "layer_a": 0, "value": 100}])
    assert len(violations) == 1


def test_session_run_drc_error_layer_draws_markers_as_one_undo_step():
    s = _fresh_session()
    s.rect(0, 0, 0, 5, 5)
    s.clear_undo_history()
    before = len(s.cell.shapes)
    violations = s.run_drc([DrcRule("MIN_AREA", layer_a=0, value=100)], error_layer=9)
    assert len(violations) == 1
    markers = [sh for sh in s.cell.shapes if sh.layer == 9]
    assert len(markers) == 1
    assert s.undo() is True
    assert len(s.cell.shapes) == before


def test_session_run_drc_without_error_layer_does_not_modify_the_design():
    s = _fresh_session()
    s.rect(0, 0, 0, 5, 5)
    before = [sh for sh in s.cell.shapes]
    s.run_drc([DrcRule("MIN_AREA", layer_a=0, value=100)])
    assert s.cell.shapes == before


# --- DRC rules technology-file round trip -----------------------------------

def test_drc_rules_round_trip_through_tech_params():
    s = _fresh_session()
    rules = [
        DrcRule("MIN_WIDTH", layer_a=0, value=50, name="W1"),
        DrcRule("MIN_ENCLOSURE", layer_a=0, layer_b=1, value=10, name="ENC1"),
    ]
    save_drc_rules(s.library, rules)
    loaded = load_drc_rules(s.library)
    restored = [DrcRule.from_dict(r) for r in loaded]
    assert restored == rules


def test_load_drc_rules_defaults_to_empty_list_when_nothing_saved():
    s = _fresh_session()
    assert load_drc_rules(s.library) == []


def test_load_drc_rules_ignores_malformed_entries():
    s = _fresh_session()
    s.library.tech_params["drc_rules"] = '[{"kind": "MIN_WIDTH"}, "not a dict", 42]'
    assert load_drc_rules(s.library) == []
