# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""PathFinder (negotiated congestion): results judged by the same oracles
as the shape router -- find_shorts(), resistor_crossings() and a
result-level spacing / edge check."""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")
pytest.importorskip("scipy")

from hlayout.scripting import hybridroute as H
from hlayout.scripting import pathfinder as PF

import test_shape_router as T


def _pf(s, layers, **kw):
    d, m = H.export_design(s, layers=layers)
    kw.setdefault("local_dielectric", m["local_diel"] >= 0)
    kw.setdefault("cond2_keepouts", m.get("local_keepouts"))
    return d, m, PF.route(copy.deepcopy(d), **kw)


@pytest.mark.parametrize("orth", [True, False])
def test_crossing_nets_cross_on_a_printed_crossover(orth):
    s = T._cross_board(dielectric=True)
    d, m = H.export_design(s, layers=2)
    d["routing"]["orthogonal"] = orth
    r = PF.route(copy.deepcopy(d), cond2_keepouts=m["local_keepouts"])
    assert r["stats"]["routed"] == r["stats"]["connections"] == 3
    assert r["crossovers"]
    H.apply_routed(s, r, m)
    assert s.find_shorts() == [] and s.resistor_crossings() == []


def test_without_a_dielectric_layer_nothing_crosses_and_nothing_shorts():
    s = T._cross_board(dielectric=False)
    d, m, r = _pf(s, 2)
    assert r["crossovers"] == [] and r["stats"]["routed"] == 2
    H.apply_routed(s, r, m)
    assert s.find_shorts() == []


@pytest.mark.parametrize("seed", range(4))
def test_random_boards_route_clean(seed):
    s = T._random_board(seed)
    d, m, r = _pf(s, 2)
    assert T._result_drc(d, r) == [], seed
    H.apply_routed(s, r, m)
    assert s.find_shorts() == [] and s.resistor_crossings() == [], seed
    assert r["stats"]["routed"] >= 0.8 * r["stats"]["connections"]


def test_tracks_meet_pads_square_and_centred():
    s = T._cross_board(dielectric=True)
    d, m, r = _pf(s, 2)
    centres = {(round(t["position"]["x"]), round(t["position"]["y"]))
               for t in d["substratePads"]}
    for t in r["traces"]:
        p = [(q["x"], q["y"]) for q in t["points"]]
        for end, nxt in ((p[0], p[1]), (p[-1], p[-2])):
            if end in centres:                    # a leg into a pad: axis-aligned
                assert end[0] == nxt[0] or end[1] == nxt[1]


def test_stop_in_the_first_round_returns_what_there_is():
    s = T._random_board(1)
    d, m = H.export_design(s, layers=2)
    calls = [0]

    def cancel():
        calls[0] += 1
        return calls[0] > 2
    r = PF.route(copy.deepcopy(d), cond2_keepouts=m["local_keepouts"], cancel=cancel)
    assert r["stats"]["stopped"] and r["stats"]["routed"] <= r["stats"]["connections"]


def test_hybrid_route_defaults_to_pathfinder():
    s = T._cross_board(dielectric=True)
    st = H.hybrid_route(s, layers=2)
    assert st["engine"] == "pathfinder"
    assert st["stats"]["routed"] == st["stats"]["connections"]
    assert s.find_shorts() == []


# ---- full dielectric: COND2 a routing layer under one sheet, joined by vias

def _full(s, **settings):
    H.save_settings(s.library, dict(full_dielectric=True, **settings))
    d, m = H.export_design(s, layers=2)
    assert m["full_dielectric"]
    return d, m, H.pf_route(copy.deepcopy(d), m)


def test_full_dielectric_crossing_nets_route_on_cond2_with_vias():
    s = T._cross_board(dielectric=True)
    d, m, r = _full(s)
    assert r["stats"]["routed"] == r["stats"]["connections"] == 3
    assert r["crossovers"] == []                          # no local strips
    assert r["windows"] and r["stats"]["fullDielectric"]
    c2 = [t for t in r["traces"] if t["layer"] == "COND2"]
    assert c2                                             # real 2nd-conductor tracks
    # every COND2 run ends on a via of its own net
    vias = {(w["net"], w["position"]["x"], w["position"]["y"]) for w in r["windows"]}
    for t in c2:
        for q in (t["points"][0], t["points"][-1]):
            assert (t["net"], q["x"], q["y"]) in vias
    st = H.apply_routed(s, r, m)
    assert st["windows"] == len(r["windows"])
    assert s.find_shorts() == [] and s.resistor_crossings() == []


def test_full_dielectric_off_keeps_local_strips():
    s = T._cross_board(dielectric=True)
    H.save_settings(s.library, {"full_dielectric": False})
    d, m = H.export_design(s, layers=2)
    assert not m["full_dielectric"]
    r = H.pf_route(copy.deepcopy(d), m)
    assert r["crossovers"] and not r["windows"]


def test_full_dielectric_needs_two_conductors():
    s = T._cross_board(dielectric=True)
    H.save_settings(s.library, {"full_dielectric": True})
    d, m = H.export_design(s, layers=1)
    assert not m["full_dielectric"]


@pytest.mark.parametrize("seed", range(3))
def test_full_dielectric_random_boards_route_clean(seed):
    s = T._random_board(seed)
    d, m, r = _full(s)
    H.apply_routed(s, r, m)
    assert s.find_shorts() == [] and s.resistor_crossings() == [], seed
    assert r["stats"]["routed"] >= 0.8 * r["stats"]["connections"]


def test_full_dielectric_via_cost_follows_crossover_cost():
    s = T._cross_board(dielectric=True)
    _d, _m, cheap = _full(s, crossover_cost=0.5)
    _d, _m, dear = _full(s, crossover_cost=50.0)
    assert len(cheap["windows"]) >= len(dear["windows"])
