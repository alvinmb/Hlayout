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


# ---- a die part with its own bonds and fingers (the chip_wire DIE_* cells)

def _die_in_cell_board(finger_layer=5, attach=False):
    """12 x 8 mm: die U1 whose cell carries its two pads (58), bonds (59)
    and bond fingers numbered like the pads on ``finger_layer``; each finger
    is wired to an edge pad."""
    s = T.Session.new("DIE")
    T._setup(s, True)
    s.new_cell("DIE_X")
    for pin, x in (("1", -200), ("2", 200)):
        s.rect(58, x - 50, -50, x + 50, 50, pin=pin)
        fx = x * 8
        s.rect(finger_layer, fx - 100, 1500, fx + 100, 1700, pin=pin)
        s.path(59, [(x, 0), (fx, 1600)], 30)
    s.rect(20, -400, -400, 400, 400)
    if attach:                                     # an unnumbered die-attach pad on COND2
        s.rect(5, -900, -900, 900, 900)
    s.new_cell("TOP")
    s.rect(57, 0, 0, 12000, 8000)
    for pin, (x, y) in {"1": (550, 550), "2": (11450, 550)}.items():
        s.rect(3, x - 300, y - 300, x + 300, y + 300, pin=pin)
    s.sref("DIE_X", (6000, 3000), label="U1")
    s.set_net_names({"IO.1": "A", "U1.1": "A", "IO.2": "B", "U1.2": "B"})
    return s


def test_die_bonds_drawn_in_the_die_part_are_fixed_bonds():
    s = _die_in_cell_board()
    d, m = H.export_design(s, layers=2)
    assert m["unbonded"] == [] and m["cell_bonds"] == 2
    fingers = {p["id"]: p for p in d["substratePads"] if p["role"] == "BOND_FINGER"}
    assert set(fingers) == {"F_U1_1", "F_U1_2"}
    assert all(p["layer"] == "COND2" for p in fingers.values())
    assert all(w["fixed"] for w in d["wires"])
    # not taken for backside contacts (no attach-pad strips round the die)
    assert not any(p["id"].startswith("U1_") for p in d["substratePads"])
    d1, m1 = H.export_design(s, layers=1)                  # one conductor: on COND1
    assert all(p["layer"] == "COND1" for p in d1["substratePads"]
               if p["role"] == "BOND_FINGER")


def test_full_dielectric_routes_to_fingers_on_the_2nd_conductor():
    s = _die_in_cell_board()
    H.save_settings(s.library, {"full_dielectric": True})
    d, m = H.export_design(s, layers=2)
    r = H.pf_route(copy.deepcopy(d), m)
    assert r["stats"]["routed"] == r["stats"]["connections"] == 2
    st = H.apply_routed(s, r, m)
    assert st["bonds"] == 0                                 # the part's own bonds stand
    assert s.find_shorts() == []
    # no via on a finger: a COND2 run that ends on its COND2 finger needs none
    fpos = {(p["position"]["x"], p["position"]["y"]) for p in d["substratePads"]
            if p["role"] == "BOND_FINGER"}
    for w in r["windows"]:
        q = (w["position"]["x"], w["position"]["y"])
        assert all(abs(q[0] - x) > 150 or abs(q[1] - y) > 150 for x, y in fpos)


def test_a_connection_grown_from_a_dropped_one_is_dropped_too():
    """Net A: pads 1 (left), 2 (right) and 3 (top middle). Connection 0 runs
    1 -> 2; connection 1 grows from the middle of it up to 3. With
    connection 0 dropped, connection 1 would be a track from nowhere to pad
    3: it must be dropped (and reported unrouted), not drawn."""
    s = T.Session.new("ORPH")
    T._setup(s, True)
    s.new_cell("TOP")
    s.rect(57, 0, 0, 12000, 8000)
    for pin, (x, y) in {"1": (1050, 4050), "2": (10950, 4050), "3": (6050, 7050)}.items():
        s.rect(3, x - 300, y - 300, x + 300, y + 300, pin=pin)
    s.set_net_names({"IO.1": "A", "IO.2": "A", "IO.3": "A"})
    d, m = H.export_design(s, layers=1)
    P = PF.SR.ShapeProblem(d)
    G = PF._Grid(P, 100.0)
    ts = {t.tid: t for t in P.terminals}
    k = lambda x, y: G.idx(x, y)
    t1, t2, t3 = (ts[i].centre for i in ("IO1", "IO2", "IO3"))
    row = [(0, k(x, t1[1])) for x in range(int(t1[0]), int(t2[0]) + 1, 100)]
    mid = k(t3[0], t1[1])
    col = [(0, k(t3[0], y)) for y in range(int(t1[1]), int(t3[1]) + 1, 100)]
    assert col[0][1] == mid
    routes = {"A": {"cells": row + col, "conns": [row, col], "fail": []}}
    out = PF._emit(d, P, G, routes, {"A": {0}}, ["A"], False, 0.0, lambda _m: None)
    assert out["traces"] == []
    assert out["stats"]["routed"] == 0 and len(out["unrouted"]) == 2
    # nothing dropped: both drawn
    out = PF._emit(d, P, G, routes, {}, ["A"], False, 0.0, lambda _m: None)
    assert out["stats"]["routed"] == 2 and len(out["traces"]) == 2


def test_unnumbered_copper_in_a_die_part_is_kept_clear():
    """A die-attach pad on the 2nd conductor in the die part (no pin, no
    net) is copper: exported as a net-less pad, and no track touches it --
    on ALU8 a COND2 track crossed one and shorted B7X to F7 through it."""
    from shapely.geometry import LineString, box
    s = _die_in_cell_board(attach=True)
    s.cell.add_rectangle(3, 5450, 7000, 6550, 7600, pin="3")       # a pad above the die
    s.cell.add_rectangle(3, 5450, 400, 6550, 1000, pin="4")        # and one below
    s.set_net_names({"IO.1": "A", "U1.1": "A", "IO.2": "B", "U1.2": "B",
                     "IO.3": "C", "IO.4": "C"})
    H.save_settings(s.library, {"full_dielectric": True})
    d, m = H.export_design(s, layers=2)
    cu = [p for p in d["substratePads"] if p["id"].startswith("U1_COPPER")]
    assert len(cu) == 1 and cu[0]["layer"] == "COND2" and cu[0]["net"].startswith("~NC")
    r = H.pf_route(copy.deepcopy(d), m)
    ox, oy = m["origin"]
    att = box(6000 - 900 - ox, 3000 - 900 - oy, 6000 + 900 - ox, 3000 + 900 - oy)
    sp = d["rules"]["conductor"]["minSpaceUm"]
    for t in r["traces"]:
        if t["layer"] != "COND2":
            continue
        g = LineString([(q["x"], q["y"]) for q in t["points"]]).buffer(t["widthUm"] / 2)
        assert g.distance(att) >= sp - 1, t
    H.apply_routed(s, r, m)
    assert s.find_shorts() == []


# ---- Auto engine

def _no_fr(monkeypatch, path=None):
    import hlayout.io.specctra as SP
    monkeypatch.setattr(SP, "find_freerouting", lambda saved=None: path)


def test_auto_engine_one_conductor_is_pathfinder(monkeypatch):
    _no_fr(monkeypatch, "freerouting.exe")
    s = T._cross_board(dielectric=True)
    d, m = H.export_design(s, layers=1)
    assert H.choose_engine(s, d, m, 1)[0] == "pathfinder"


def test_auto_engine_printed_resistors_keep_local_crossovers(monkeypatch):
    _no_fr(monkeypatch, "freerouting.exe")
    s = T._cross_board(dielectric=True)               # R1 in the middle
    d, m = H.export_design(s, layers=2)
    eng, why = H.choose_engine(s, d, m, 2)
    assert eng == "pathfinder" and "resistor" in why


def test_auto_engine_fingers_on_cond2_want_a_full_sheet(monkeypatch):
    s = _die_in_cell_board()
    d, m = H.export_design(s, layers=2)
    _no_fr(monkeypatch, "freerouting.exe")
    assert H.choose_engine(s, d, m, 2)[0] == "freerouting"
    _no_fr(monkeypatch, None)                         # not installed: PathFinder, full
    m2 = dict(m)
    eng, why = H.choose_engine(s, d, m2, 2)
    assert eng == "pathfinder" and m2["full_dielectric"] and "not installed" in why


def test_auto_engine_by_crossing_density(monkeypatch):
    _no_fr(monkeypatch, "freerouting.exe")
    s = T.Session.new("DENSE")
    T._setup(s, True)
    s.new_cell("TOP")
    s.rect(57, 0, 0, 12000, 8000)
    names = {}
    # four nets, each from the left edge to the right edge in reverse order:
    # every pair crosses (6 crossings over 4 connections)
    for i in range(4):
        a, b = str(2 * i + 1), str(2 * i + 2)
        s.rect(3, 300, 1000 + 1500 * i, 900, 1600 + 1500 * i, pin=a)
        s.rect(3, 11100, 5500 - 1500 * i, 11700, 6100 - 1500 * i, pin=b)
        names[f"IO.{a}"] = names[f"IO.{b}"] = f"N{i}"
    s.set_net_names(names)
    d, m = H.export_design(s, layers=2)
    assert H.sli_crossings(d) == (6, 4)
    assert H.choose_engine(s, d, m, 2)[0] == "freerouting"
    # straight across instead: no crossings -> PathFinder
    s2 = T._random_board(0)
    d2, m2 = H.export_design(s2, layers=2)
    x, c = H.sli_crossings(d2)
    assert x >= 0 and c > 0
