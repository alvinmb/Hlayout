# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""EDGE_CLEARANCE: conductor to the edge of the substrate."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.drc import DRC_RULE_KINDS, TWO_LAYER_KINDS, DrcRule, check_rule, substrate_area
from hlayout.core.errors import GeometryError
from hlayout.scripting import Session

SUB, COND, ERR = 1, 3, 60


def _s():
    s = Session.new("EDGE")
    s.new_cell("TOP")
    s.clear_undo_history()
    return s


def _rule(value=250):
    return DrcRule(kind="EDGE_CLEARANCE", layer_a=COND, layer_b=SUB, value=value, name="EDGE")


def test_edge_clearance_is_a_two_layer_kind():
    assert "EDGE_CLEARANCE" in DRC_RULE_KINDS and "EDGE_CLEARANCE" in TWO_LAYER_KINDS
    with pytest.raises(GeometryError):
        DrcRule(kind="EDGE_CLEARANCE", layer_a=COND, value=250)
    assert _rule().label == "EDGE: EDGE_CLEARANCE(layer 3, layer 1) >= 250"


def test_conductor_well_inside_passes():
    s = _s()
    s.rect(SUB, 0, 0, 10000, 10000)
    s.rect(COND, 1000, 1000, 9000, 2000)
    assert check_rule(s.library, s.cell, _rule()) == []


def test_conductor_exactly_at_the_limit_passes():
    s = _s()
    s.rect(SUB, 0, 0, 10000, 10000)
    s.rect(COND, 250, 1000, 9750, 2000)
    assert check_rule(s.library, s.cell, _rule()) == []


def test_conductor_too_close_is_flagged_with_its_distance():
    s = _s()
    s.rect(SUB, 0, 0, 10000, 10000)
    s.rect(COND, 100, 1000, 3000, 2000)          # 0.1 mm from the left edge
    s.rect(COND, 5000, 5000, 6000, 6000)          # fine
    v = check_rule(s.library, s.cell, _rule())
    assert len(v) == 1
    assert "0.100 mm from the substrate edge < min 0.250 mm" in v[0].message
    x0, y0, x1, y1 = v[0].bbox()
    assert x0 == pytest.approx(100) and x1 == pytest.approx(250)   # only the strip that is too close


def test_conductor_off_the_substrate_is_flagged():
    s = _s()
    s.rect(SUB, 0, 0, 10000, 10000)
    s.rect(COND, 9000, 1000, 11000, 2000)
    v = check_rule(s.library, s.cell, _rule())
    assert len(v) == 1 and "runs off the substrate" in v[0].message


def test_a_closed_outline_path_counts_as_its_area():
    s = _s()
    s.path(SUB, [(0, 0), (10000, 0), (10000, 10000), (0, 10000), (0, 0)], 200)
    s.rect(COND, 1000, 1000, 9000, 2000)
    s.rect(COND, 200, 5000, 1000, 6000)            # 0.2 mm from the outline's centre line
    area = substrate_area(s.library, s.cell, SUB)
    assert area.area == pytest.approx(1e8)
    v = check_rule(s.library, s.cell, _rule())
    assert len(v) == 1 and "0.200 mm" in v[0].message


def test_substrate_and_conductor_in_placed_cells():
    s = _s()
    s.new_cell("SUBSTRATE")
    s.rect(SUB, 0, 0, 10000, 10000)
    s.new_cell("PART")
    s.rect(COND, 0, 0, 500, 500)
    s.use_cell("TOP")
    s.sref("SUBSTRATE", (0, 0))
    s.sref("PART", (9600, 5000))                   # right edge 0.1 mm past the substrate
    s.sref("PART", (5000, 5000))
    v = check_rule(s.library, s.cell, _rule())
    assert len(v) == 1 and "runs off the substrate" in v[0].message


def test_no_substrate_is_an_error():
    s = _s()
    s.rect(COND, 0, 0, 100, 100)
    with pytest.raises(GeometryError):
        check_rule(s.library, s.cell, _rule())


def test_session_run_drc_draws_the_marker():
    s = _s()
    s.rect(SUB, 0, 0, 10000, 10000)
    s.rect(COND, 100, 1000, 3000, 2000)
    v = s.run_drc([{"kind": "EDGE_CLEARANCE", "layer_a": COND, "layer_b": SUB, "value": 250}],
                  error_layer=ERR)
    assert len(v) == 1
    assert len([sh for sh in s.cell.shapes if sh.layer == ERR]) == 1
