# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Wire-bond DRC: BOND_MIN_LENGTH, BOND_MAX_LENGTH, BOND_MAX_ANGLE."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.drc import (BOND_KINDS, DRC_RULE_KINDS, DrcRule, bond_departures,
                              bond_rules_from_settings, check_rule)
from hlayout.core.errors import GeometryError
from hlayout.io.techfile import load_drc_rules, save_bond_rules
from hlayout.scripting import Session

SUB, EPOXY, PAD, BOND, ERR = 1, 10, 58, 6, 60

#: DIE cell: 1 x 1 mm, pads 0.1 mm square centred on its left, right and top sides
LEFT, RIGHT, TOP = (100, 500), (900, 500), (500, 900)


def _die(s, name="DIE"):
    s.new_cell(name)
    s.rect(EPOXY, 0, 0, 1000, 1000)
    for cx, cy in (LEFT, RIGHT, TOP):
        s.rect(PAD, cx - 50, cy - 50, cx + 50, cy + 50)


def _design(rotation=0.0):
    """TOP: a 20 x 20 mm substrate with DIE placed as U1 at (5000, 5000)."""
    s = Session.new("BONDDRC")
    _die(s)
    s.new_cell("TOP")
    s.rect(SUB, 0, 0, 20000, 20000)
    s.sref("DIE", (5000, 5000), rotation=rotation, label="U1")
    save_bond_rules(s.library, {"layer": BOND, "width": 25, "pad": 100, "min_length": 300,
                                "max_length": 5000, "die_pad_layers": [PAD]})
    s.clear_undo_history()
    return s


def _wire(s, a, b):
    s.cell.add_path(BOND, [a, b], 25, cap="round")


def _at(p):
    return (5000 + p[0], 5000 + p[1])


def _kinds(viol):
    return [v.rule.kind for v in viol]


# --- rule objects ------------------------------------------------------

def test_bond_kinds_are_drc_kinds():
    assert set(BOND_KINDS) <= set(DRC_RULE_KINDS)


def test_angle_rule_needs_a_die_pad_layer_and_a_degree_value():
    with pytest.raises(GeometryError):
        DrcRule(kind="BOND_MAX_ANGLE", layer_a=BOND, value=45)
    with pytest.raises(GeometryError):
        DrcRule(kind="BOND_MAX_ANGLE", layer_a=BOND, layer_b=PAD, value=200)
    DrcRule(kind="BOND_MAX_ANGLE", layer_a=BOND, layer_b=PAD, value=45)


def test_labels_say_maximum_and_degrees():
    assert DrcRule(kind="BOND_MAX_LENGTH", layer_a=6, value=5000).label == \
        "BOND_MAX_LENGTH(layer 6) <= 5000"
    assert DrcRule(kind="BOND_MIN_LENGTH", layer_a=6, value=300).label == \
        "BOND_MIN_LENGTH(layer 6) >= 300"
    assert DrcRule(kind="BOND_MAX_ANGLE", layer_a=6, layer_b=58, value=45, name="A").label == \
        "A: BOND_MAX_ANGLE(layer 6, layer 58) <= 45 deg"


def test_rule_dict_round_trip():
    r = DrcRule(kind="BOND_MAX_ANGLE", layer_a=6, layer_b=58, value=30, name="ANG")
    assert DrcRule.from_dict(r.to_dict()) == r


# --- length -------------------------------------------------------------

def test_min_length_flags_a_short_bond_only():
    s = _design()
    _wire(s, (1000, 1000), (1150, 1000))          # 0.15 mm
    _wire(s, (1000, 2000), (2000, 2000))          # 1 mm
    v = check_rule(s.library, s.cell, DrcRule(kind="BOND_MIN_LENGTH", layer_a=BOND, value=300))
    assert len(v) == 1
    assert "0.150 mm < min 0.300 mm" in v[0].message
    x0, y0, x1, y1 = v[0].bbox()
    assert x0 < 1000 and x1 > 1150 and y0 < 1000 < y1   # marker lies along the wire


def test_max_length_flags_a_long_bond_only():
    s = _design()
    _wire(s, (1000, 1000), (8000, 1000))          # 7 mm
    _wire(s, (1000, 2000), (2000, 2000))
    v = check_rule(s.library, s.cell, DrcRule(kind="BOND_MAX_LENGTH", layer_a=BOND, value=5000))
    assert len(v) == 1
    assert "7.000 mm > max 5.000 mm" in v[0].message


def test_bond_exactly_at_the_limit_passes():
    s = _design()
    _wire(s, (1000, 1000), (1300, 1000))
    _wire(s, (1000, 2000), (6000, 2000))
    for kind, val in (("BOND_MIN_LENGTH", 300), ("BOND_MAX_LENGTH", 5000)):
        assert check_rule(s.library, s.cell, DrcRule(kind=kind, layer_a=BOND, value=val)) == []


def test_length_counts_bonds_inside_placed_cells():
    s = _design()
    s.use_cell("DIE")
    _wire(s, (0, 0), (100, 0))
    s.use_cell("TOP")
    v = check_rule(s.library, s.cell, DrcRule(kind="BOND_MIN_LENGTH", layer_a=BOND, value=300))
    assert len(v) == 1 and "(5.000, 5.000)" in v[0].message


def test_other_layers_are_not_bonds():
    s = _design()
    s.cell.add_path(3, [(0, 0), (50, 0)], 25)
    assert check_rule(s.library, s.cell, DrcRule(kind="BOND_MIN_LENGTH", layer_a=BOND, value=300)) == []


# --- departure angle ----------------------------------------------------

def _angle_rule(value):
    return DrcRule(kind="BOND_MAX_ANGLE", layer_a=BOND, layer_b=PAD, value=value)


def test_departure_angles_are_measured_from_the_die_edge_normal():
    s = _design()
    _wire(s, _at(LEFT), (3600, 5500))             # straight out of the left side: 0
    _wire(s, _at(RIGHT), (6900, 6500))            # 45 off the right side's normal
    _wire(s, _at(TOP), (6500, 6000))              # nearly along the top edge
    got = sorted((round(a, 1), die) for _w, _k, die, a in
                 bond_departures(s.library, s.cell, BOND, PAD))
    assert [g[1] for g in got] == ["U1"] * 3
    assert got[0][0] == 0.0 and got[1][0] == 45.0
    assert got[2][0] == pytest.approx(math.degrees(math.atan2(1000, 100)), abs=0.1)


def test_max_angle_flags_only_wires_beyond_the_limit():
    s = _design()
    _wire(s, _at(LEFT), (3600, 5500))
    _wire(s, _at(RIGHT), (6900, 6500))
    _wire(s, _at(TOP), (6500, 6000))
    v45 = check_rule(s.library, s.cell, _angle_rule(45))
    assert len(v45) == 1 and "84.3 deg" in v45[0].message and "U1" in v45[0].message
    assert len(check_rule(s.library, s.cell, _angle_rule(44))) == 2


def test_the_substrate_end_is_not_checked():
    s = _design()
    _wire(s, (1000, 1000), (1000, 3000))          # no die at either end
    assert bond_departures(s.library, s.cell, BOND, PAD) == []


def test_rotated_die_uses_its_rotated_edges():
    s = _design(rotation=90)                      # the left side now faces down
    cell = s.cell
    # LEFT (100, 500) rotated 90 about the origin -> (-500, 100), placed at (5000, 5000)
    _wire(s, (4500, 5100), (4500, 3600))          # straight down: 0 deg
    _wire(s, (4500, 5100), (5500, 3600))          # 33.7 deg
    got = sorted(round(a, 1) for *_r, a in bond_departures(s.library, cell, BOND, PAD))
    assert got == [0.0, pytest.approx(33.7, abs=0.1)]


def test_a_die_built_from_placed_pin_cells_is_one_die():
    s = Session.new("PINS")
    s.new_cell("PIN")
    s.rect(PAD, -50, -50, 50, 50)
    s.new_cell("DIE")
    s.rect(EPOXY, 0, 0, 1000, 1000)
    for c in (LEFT, RIGHT, TOP):
        s.sref("PIN", c)
    s.new_cell("TOP")
    s.rect(SUB, 0, 0, 20000, 20000)
    s.sref("DIE", (5000, 5000), label="U7")
    _wire(s, _at(LEFT), (3600, 5500))
    got = bond_departures(s.library, s.cell, BOND, PAD)
    assert [(die, round(a, 1)) for _w, _k, die, a in got] == [("U7", 0.0)]


def test_a_die_body_drawn_on_the_pad_layer_is_not_a_pad():
    s = _design()
    s.use_cell("DIE")
    s.rect(PAD, 0, 0, 1000, 1000)                 # die body on the pad layer (as LM741 does)
    s.use_cell("TOP")
    _wire(s, _at(RIGHT), (7000, 5500))
    got = bond_departures(s.library, s.cell, BOND, PAD)
    assert [round(a, 1) for *_r, a in got] == [0.0]


def test_a_corner_pad_may_leave_by_either_side():
    s = _design()
    s.use_cell("DIE")
    s.rect(PAD, 850, 850, 950, 950)               # top-right corner pad
    s.use_cell("TOP")
    _wire(s, (5900, 5900), (5900, 7400))          # straight up
    _wire(s, (5900, 5900), (7400, 5900))          # straight right
    got = sorted(round(a, 1) for *_r, a in bond_departures(s.library, s.cell, BOND, PAD))
    assert got == [0.0, 0.0]


def test_a_header_bonded_inward_is_not_a_die():
    s = _design()
    s.new_cell("HDR")                             # 8 x 8 mm frame with two pins
    s.rect(EPOXY, 0, 0, 8000, 300)
    s.rect(EPOXY, 0, 7700, 8000, 8000)
    s.rect(PAD, 1000, 50, 1200, 250)
    s.rect(PAD, 3000, 50, 3200, 250)
    s.use_cell("TOP")
    s.sref("HDR", (11000, 11000), label="J1")
    _wire(s, (12100, 11150), (12100, 14000))      # pin bonded in toward the substrate
    assert [d for *_r, d, _a in bond_departures(s.library, s.cell, BOND, PAD)] == []


def test_an_enclosing_package_is_not_a_die():
    s = _design()
    s.new_cell("CAN")
    s.rect(EPOXY, 0, 0, 20000, 20000)
    s.rect(PAD, 100, 100, 300, 300)
    s.rect(PAD, 100, 1000, 300, 1200)
    s.use_cell("TOP")
    s.sref("CAN", (0, 0), label="J2")
    _wire(s, (200, 200), (-2000, 200))
    assert bond_departures(s.library, s.cell, BOND, PAD) == []


# --- rules from Setup > Wire Bond Rules, Session, command bar ------------

def test_rules_from_settings():
    rules = bond_rules_from_settings({"layer": BOND, "min_length": 300, "max_length": 5000,
                                      "die_pad_layers": [PAD, 49]})
    assert [(r.kind, r.layer_a, r.layer_b, r.value) for r in rules] == [
        ("BOND_MIN_LENGTH", BOND, None, 300.0), ("BOND_MAX_LENGTH", BOND, None, 5000.0),
        ("BOND_MAX_ANGLE", BOND, PAD, 45.0), ("BOND_MAX_ANGLE", BOND, 49, 45.0)]
    assert bond_rules_from_settings({"layer": BOND, "min_length": 300, "max_length": 5000,
                                     "die_pad_layers": [PAD]}, existing=rules) == []
    with pytest.raises(GeometryError):
        bond_rules_from_settings({"layer": -1})


def test_session_add_bond_rules_and_run_drc():
    s = _design()
    _wire(s, _at(TOP), (6500, 6000))
    _wire(s, (1000, 1000), (1150, 1000))
    added = s.add_bond_drc_rules(angle=40)
    assert len(added) == 3 and s.add_bond_drc_rules() == []
    assert len(load_drc_rules(s.library)) == 3
    v = s.run_drc(error_layer=ERR)
    assert sorted(_kinds(v)) == ["BOND_MAX_ANGLE", "BOND_MIN_LENGTH"]
    assert len([sh for sh in s.cell.shapes if sh.layer == ERR]) == 2
    assert [d["die"] for d in s.bond_departures()] == ["U1"]


def test_run_drc_accepts_bond_rules_as_dicts():
    s = _design()
    _wire(s, (1000, 1000), (8000, 1000))
    v = s.run_drc([{"kind": "BOND_MAX_LENGTH", "layer_a": BOND, "value": 5000}])
    assert _kinds(v) == ["BOND_MAX_LENGTH"]


def test_command_bar_bonds_and_run():
    pytest.importorskip("tkinter")
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _design()
    _wire(s, _at(TOP), (6500, 6000))
    ci = CommandInterpreter(s, ScriptConsole(s))
    out = ci.run("DRCRULE BONDS angle=30")
    assert out.count("added rule") == 3 and "<= 30 deg" in out
    assert "already saved" in ci.run("DRCRULE BONDS")
    out = ci.run("DRC RUN")
    assert "1 x BOND.ANGLE: BOND_MAX_ANGLE" in out and "wire bonds:" in out and "U1" in out
    out = ci.run(f"DRCRULE ADD BOND_MAX_ANGLE {BOND} {PAD} value=60 name=A60")
    assert "<= 60 deg" in out


def test_bonds_carried_inside_the_die_part_are_checked_and_dont_widen_its_keepout():
    """A library die can carry its own wire bonds (they move with it): the
    angle check still sees them, and the router's die keep-out stays the
    die's own size, clear of the bond landings."""
    from hlayout.io.techfile import load_route_rules, save_route_rules
    from shapely.geometry import Point, Polygon as SPoly
    s = Session.new("PARTBONDS")
    _die(s)
    save_bond_rules(s.library, {"layer": BOND, "width": 25, "pad": 75, "min_length": 300,
                                "max_length": 5000, "die_pad_layers": [PAD]})
    s.wire_bond([LEFT, (-900, 500)], cell_name="DIE")            # straight out: 0 degrees
    s.wire_bond([TOP, (900, 1900)], cell_name="DIE")             # 400 across, 1000 out
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr["layer_a"] = 3; rr["layer_b"] = 5
    save_route_rules(s.library, rr)
    s.rect(SUB, -10000, -10000, 10000, 10000)
    s.sref("DIE", (0, 0), label="U1")
    angles = sorted(round(d[3], 1) for d in bond_departures(s.library, s.library.cells["TOP"], BOND, PAD))
    assert angles == [0.0, round(math.degrees(math.atan2(400, 1000)), 1)]
    ko = [SPoly(p) for lay, p in s._route_keepouts(s.library.cells["TOP"], (3, 5),
                                                   s.freerouting_rules()) if lay == 3]
    assert ko and not any(k.contains(Point(-900, 500)) or k.contains(Point(900, 1900)) for k in ko)


def test_a_die_part_with_its_own_numbered_bond_fingers(tmp_path):
    """Bond fingers carried in the die part, numbered like the die pads:
    the router gets them as the die's pads (no extra BOND pins), the die
    keep-out leaves them clear, the angle check still runs, and Auto
    Place takes them as where the die's nets reach the substrate."""
    from hlayout.io.techfile import load_route_rules, save_route_rules
    from hlayout.scripting.autoplace_session import build_job
    from hlayout.io.kicad_netlist import read_kicad_netlist
    from shapely.geometry import Polygon as SPoly
    s = Session.new("FINGERS")
    _die(s)
    save_bond_rules(s.library, {"layer": BOND, "width": 25, "pad": 75, "min_length": 300,
                                "max_length": 5000, "die_pad_layers": [PAD]})
    die = s.library.cells["DIE"]
    for sh in die.shapes:
        if sh.layer == PAD:
            sh.pin = {LEFT: "1", RIGHT: "2", TOP: "3"}[((sh.bbox()[0] + sh.bbox()[2]) / 2,
                                                       (sh.bbox()[1] + sh.bbox()[3]) / 2)]
    for pin, a, b in (("1", LEFT, (-900, 500)), ("2", RIGHT, (1900, 500))):
        s.wire_bond([a, b], cell_name="DIE")
        s.rect(5, b[0] - 150, b[1] - 150, b[0] + 150, b[1] + 150, pin=pin)
    s.use_cell("DIE")
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr["layer_a"] = 3; rr["layer_b"] = 5; rr["boundary_layer"] = SUB
    save_route_rules(s.library, rr)
    s.rect(SUB, -10000, -10000, 10000, 10000)
    s.rect(3, -8000, 300, -7000, 700, pin="1")
    s.rect(3, 8000, 300, 9000, 700, pin="2")
    s.sref("DIE", (0, 0), label="U1")
    s.set_net_names({"U1.1": "A", "IO.1": "A", "U1.2": "B", "IO.2": "B"})
    txt_path = str(tmp_path / "fingers.dsn")
    sm = s.export_dsn(txt_path, layer_mode="two", finger_layer="second")
    txt = open(txt_path).read()
    assert "BOND-B" not in txt and sm.unknown_pads == []
    assert "(net A (pins IO-1 U1-1))" in txt and "(net B (pins IO-2 U1-2))" in txt
    ko = [SPoly(p) for lay, p in s._route_keepouts(s.library.cells["TOP"], (3, 5),
                                                   s.freerouting_rules()) if lay == 5]
    fingers = [SPoly([(-1050, 350), (-750, 350), (-750, 650), (-1050, 650)])]
    assert ko and not any(k.intersects(f) for k in ko for f in fingers)
    assert len(bond_departures(s.library, s.library.cells["TOP"], BOND, PAD)) == 2
    nl = read_kicad_netlist('(export (version "E") (components (comp (ref "U1") (footprint "X:DIE")))'
                            ' (nets (net (code "1") (name "A") (node (ref "U1") (pin "1")))'
                            ' (net (code "2") (name "B") (node (ref "U1") (pin "2")))))')
    placer, _info = build_job(s, nl, cell_name="TOP")
    u1 = placer.parts[0]
    assert u1.pins["1"] == (-900.0, 500.0) and u1.pins["2"] == (1900.0, 500.0)
