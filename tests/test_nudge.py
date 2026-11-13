# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Part locks and the router's nudge pass (scripting.nudge): a gap a hair
too narrow for a track is opened by moving the unlocked parts either side;
locked parts never move; the move cap holds; the nudged board routes."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")
pytest.importorskip("scipy")

import test_shape_router as T


def test_lock_unlock_and_listing():
    s = T._random_board(1)
    refs = sorted(r.label for r in s.library.get_cell("TOP").srefs if r.label)
    assert refs
    assert s.lock_parts(refs[0]) == [refs[0]]
    assert s.locked_parts() == {refs[0]}
    assert set(s.lock_parts("*")) == set(refs)
    assert s.unlock_parts(refs[0]) == sorted(set(refs) - {refs[0]}, key=lambda r: (len(r), r))
    assert s.unlock_parts() == [] and s.locked_parts() == set()
    with pytest.raises(Exception):
        s.lock_parts("NOPE99")


def _design(gap=850.0):
    """Two pads of net N either side of a wall of two chip parts whose gap is
    ``gap`` um (a 300 um track needs 900 between foreign pads)."""
    def chip(ref, cy, h, na, nb):
        return {"refdes": ref, "kind": "CHIP", "centre": {"x": 5000, "y": cy}, "rotation": "R0",
                "bodyLengthUm": 400, "bodyWidthUm": h, "keepoutUm": 0, "obstacleLayers": ["COND1"],
                "pinA": {"net": na, "position": {"x": -500, "y": 0}, "widthUm": 600, "heightUm": h,
                         "layer": "COND1"},
                "pinB": {"net": nb, "position": {"x": 500, "y": 0}, "widthUm": 600, "heightUm": h,
                         "layer": "COND1"}}
    top = 6000.0
    end = 800.0                              # 550 um from the edge: no track round the end
    h1 = 3000.0 - gap / 2 - end
    return {
        "schemaVersion": 1, "name": "t",
        "substrate": {"technology": "THICK_FILM", "material": "96% alumina", "widthUm": 10000,
                      "heightUm": int(top)},
        "stack": {"layers": [{"name": "COND1", "kind": "CONDUCTOR"}]}, "dies": [], "wires": [],
        "rules": {"conductor": {"minLineUm": 300, "minSpaceUm": 300, "edgeClearanceUm": 250,
                                "windowMinUm": 600, "windowEnclosureUm": 0},
                  "bondWire": {"minLengthUm": 300, "maxLengthUm": 5000, "dieToFingerMinUm": 0,
                               "dieToFingerMaxUm": 5000, "maxAngleDeg": 45.0},
                  "jumper": {"minLengthUm": 500, "maxLengthUm": 3000},
                  "pads": {"fingerWidthUm": 300, "fingerLengthUm": 400, "jumperLandUm": 300}},
        "routing": {"routingLayers": ["COND1"], "jumpersAllowed": False, "maxJumpers": 0,
                    "jumperCost": 50.0, "crossoverCost": 10.0, "gridUm": 0, "orthogonal": True,
                    "ripupPasses": 0, "netWidths": {}, "fixedTraces": [], "fixedJoins": [],
                    "connected": [], "directions": [], "netWindows": {}, "keepouts": {}},
        "components": [chip("C1", end + h1 / 2, h1, "P", "Q"),
                       chip("C2", top - end - h1 / 2, h1, "R", "S")],
        "substratePads": [
            {"id": "A", "net": "N", "role": "EXTERNAL", "position": {"x": 1000, "y": 3000},
             "widthUm": 600, "heightUm": 600, "layer": "COND1"},
            {"id": "B", "net": "N", "role": "EXTERNAL", "position": {"x": 9000, "y": 3000},
             "widthUm": 600, "heightUm": 600, "layer": "COND1"}]}


def test_nudge_opens_a_gap_a_hair_too_narrow():
    from hlayout.scripting import nudge as NG
    d = _design(850.0)
    r = NG.blockers(d, "N", "A", "B")
    assert r is not None and r["hits"]                  # through the gap, into both halos
    pl = NG.plan(d, [{"net": "N", "from": "A", "to": "B"}])
    assert set(pl["moves"]) <= {"C1", "C2"} and pl["moves"]
    # moved apart (C1 down, C2 up) by at least the 50 um shortfall in total
    dy1 = pl["moves"].get("C1", (0, 0))[1]
    dy2 = pl["moves"].get("C2", (0, 0))[1]
    assert dy1 <= 0 <= dy2 and dy2 - dy1 >= 50


def test_nudge_never_moves_a_locked_part():
    from hlayout.scripting import nudge as NG
    d = _design(850.0)
    pl = NG.plan(d, [{"net": "N", "from": "A", "to": "B"}], locked={"C1"})
    assert "C1" not in pl["moves"] and pl["moves"].get("C2", (0, 0))[1] >= 50
    pl = NG.plan(d, [{"net": "N", "from": "A", "to": "B"}], locked={"C1", "C2"})
    assert not pl["moves"]


def test_nudge_respects_the_move_cap():
    from hlayout.scripting import nudge as NG
    d = _design(500.0)                                  # 400 um short
    pl = NG.plan(d, [{"net": "N", "from": "A", "to": "B"}], locked={"C1"}, max_move=200)
    assert not pl["moves"]


def test_nudged_design_routes():
    import copy
    from hlayout.scripting import nudge as NG
    from hlayout.scripting import pathfinder as PF
    d = _design(850.0)
    assert PF.route(copy.deepcopy(d), local_dielectric=False, iterations=5)["unrouted"]
    pl = NG.plan(d, [{"net": "N", "from": "A", "to": "B"}])
    for c in d["components"]:
        dx, dy = pl["moves"].get(c["refdes"], (0, 0))
        c["centre"]["x"] += dx
        c["centre"]["y"] += dy
    r = PF.route(d, local_dielectric=False, iterations=5)
    assert not r["unrouted"], r["unrouted"]
