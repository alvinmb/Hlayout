# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Gridless shape router (hlayout.scripting.shape_router): its geometry
kernel against shapely, and whole routes judged by the same oracles the
GUI uses -- find_shorts(), resistor_crossings() -- plus a result-level
spacing / edge check that sees violations before they become shorts."""
import copy
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

shapely = pytest.importorskip("shapely")
from shapely.geometry import LineString, box

from hlayout.io.techfile import (load_bond_rules, load_dielectric_rules,
                                 load_resistor_geometry_rules, load_route_rules,
                                 save_bond_rules, save_dielectric_rules,
                                 save_resistor_geometry_rules, save_route_rules)
from hlayout.scripting import Session
from hlayout.scripting import hybridroute as H
from hlayout.scripting import shape_router as SR

DIEL = 4


# --------------------------------------------------------------------------- boards

def _setup(s, dielectric=True):
    rr = load_route_rules(s.library)
    rr.update(layer_a=3, layer_b=5, boundary_layer=57)
    save_route_rules(s.library, rr)
    g = load_resistor_geometry_rules(s.library)
    g["conductor_layer"] = 3
    save_resistor_geometry_rules(s.library, g)
    br = load_bond_rules(s.library)
    br.update(layer=59, die_pad_layers=[58])
    save_bond_rules(s.library, br)
    if dielectric:
        dr = load_dielectric_rules(s.library)
        dr["layer"] = DIEL
        save_dielectric_rules(s.library, dr)


def _cross_board(dielectric=True):
    """12 x 8 mm: nets A (bottom left -> top right) and B (top left -> bottom
    right) must cross; printed resistor R1 in the middle on net C."""
    s = Session.new("SR")
    _setup(s, dielectric)
    s.new_cell("TOP")
    s.rect(57, 0, 0, 12000, 8000)
    for pin, (x, y) in {"1": (550, 550), "2": (11450, 7450), "3": (550, 7450),
                        "4": (11450, 550)}.items():
        s.rect(3, x - 300, y - 300, x + 300, y + 300, pin=pin)
    s.generate_resistor(6000, 4000, 600, 1200, ref_des="R1", ink_rsh=1000)
    s.set_net_names({"IO.1": "A", "IO.2": "A", "IO.3": "B", "IO.4": "B",
                     "R1.1": "C", "R1.2": "C"})
    return s


def _random_board(seed):
    """15 x 10 mm, 8-16 edge pads on 3-6 nets, 1-4 printed resistors."""
    rnd = random.Random(seed)
    s = Session.new("S%d" % seed)
    _setup(s, True)
    s.new_cell("TOP")
    W, Hh = 15000, 10000
    s.rect(57, 0, 0, W, Hh)
    pos = [(x, 600) for x in range(1200, W - 600, 1800)] + \
          [(x, Hh - 600) for x in range(1200, W - 600, 1800)] + \
          [(600, y) for y in range(1800, Hh - 1200, 1800)] + \
          [(W - 600, y) for y in range(1800, Hh - 1200, 1800)]
    rnd.shuffle(pos)
    npads = rnd.randint(8, min(16, len(pos)))
    nnets = rnd.randint(3, 6)
    names = {}
    for i, (x, y) in enumerate(pos[:npads]):
        s.rect(3, x - 250, y - 250, x + 250, y + 250, pin=str(i + 1))
        names["IO.%d" % (i + 1)] = "N%d" % (i % nnets)
    placed = []
    for k in range(rnd.randint(1, 4)):
        for _ in range(30):
            cx, cy = rnd.randint(3000, W - 3000), rnd.randint(2500, Hh - 2500)
            if all(abs(cx - a) > 2500 or abs(cy - b) > 2500 for a, b in placed):
                break
        placed.append((cx, cy))
        s.generate_resistor(cx, cy, 600, 1200, ref_des="R%d" % (k + 1), ink_rsh=1000)
        names["R%d.1" % (k + 1)] = "N%d" % rnd.randrange(nnets)
        names["R%d.2" % (k + 1)] = "N%d" % rnd.randrange(nnets)
    s.set_net_names(names)
    return s


def _route(s, layers, **kw):
    d, m = H.export_design(s, layers=layers)
    kw.setdefault("local_dielectric", m["local_diel"] >= 0)
    kw.setdefault("cond2_keepouts", m.get("local_keepouts"))
    r = SR.route(copy.deepcopy(d), **kw)
    return d, m, r


def _result_drc(d, r):
    """Same-layer spacing between different nets, and copper inside the edge
    clearance, straight from the result dict (crossover lands included)."""
    sp = d["rules"]["conductor"]["minSpaceUm"]
    edge = d["rules"]["conductor"]["edgeClearanceUm"]
    W, Hh = d["substrate"]["widthUm"], d["substrate"]["heightUm"]
    segs = []
    for t in r["traces"]:
        p = [(q["x"], q["y"]) for q in t["points"]]
        segs += [(t["net"], "C1", t["widthUm"], p[i - 1], p[i]) for i in range(1, len(p))]
    for c in r["crossovers"]:
        p = [(q["x"], q["y"]) for q in c["bridge"]]
        segs += [(c["net"], "C2", c["widthUm"], p[i - 1], p[i]) for i in range(1, len(p))]
        segs += [(c["net"], "C1", c["landUm"], e, e) for e in (p[0], p[-1])]
    bad = []
    for i, (n1, l1, w1, a1, b1) in enumerate(segs):
        for x, y in (a1, b1):
            if min(x, y, W - x, Hh - y) < w1 / 2 + edge - 1:
                bad.append(("edge", n1, (x, y)))
        for n2, l2, w2, a2, b2 in segs[i + 1:]:
            if n1 != n2 and l1 == l2 and \
                    SR._seg_seg_dist(*a1, *b1, *a2, *b2) < (w1 + w2) / 2 + sp - 1:
                bad.append(("space", n1, a1, b1, n2, a2, b2))
    return bad


# --------------------------------------------------------------------------- geometry

def test_segment_distances_match_shapely_including_near_parallel():
    rnd = random.Random(1)
    for i in range(5000):
        p = [rnd.uniform(0, 1000) for _ in range(8)]
        if i % 3 == 0:                                   # near-parallel pairs
            p[3] = p[1] + rnd.uniform(-1, 1)
            p[7] = p[5] + rnd.uniform(-1, 1)
        ref = LineString([p[0:2], p[2:4]]).distance(LineString([p[4:6], p[6:8]]))
        assert SR._seg_seg_dist(*p) == pytest.approx(ref, abs=1e-6)
    # the case that let a planar track through foreign copper
    assert SR._seg_rect_dist(9538.4, 5020.1, 12638, 5020,
                             (8656, 5250, 14550, 5550)) == pytest.approx(229.9, abs=0.01)


def test_segment_to_rect_distance_matches_shapely():
    rnd = random.Random(2)
    for i in range(5000):
        x0, y0 = rnd.uniform(0, 800), rnd.uniform(0, 800)
        r = (x0, y0, x0 + rnd.uniform(0, 300), y0 + rnd.uniform(0, 300))
        a = [rnd.uniform(-200, 1200) for _ in range(4)]
        if i % 4 == 0:
            a[3] = a[1]
        elif i % 4 == 1:
            a[2] = a[0]
        ref = LineString([a[:2], a[2:]]).distance(box(*r))
        got = SR._seg_rect_dist(*a, r)
        if ref > 1.0:
            assert got == pytest.approx(ref, abs=1e-6)
        else:
            assert got <= 1.0


def test_a_scoped_query_refuses_points_outside_its_box():
    space = SR.ShapeSpace()
    space.add("COND1", "X", (5000, 0, 5300, 10000))       # outside the scope
    blk = SR._scope(space, "COND1", [(0, 0, 100, 100)], [(900, 0, 1000, 100)], 500)
    assert blk == [] and blk.bb is not None
    # inside the box: fine; reaching out of it is refused rather than
    # answered against a list that never saw what lies there
    assert space.segment_clear((100, 50), (900, 50), "N", 250, blk)
    assert not space.segment_clear((100, 50), (5150, 50), "N", 250, blk)


def test_copper_not_centreline_keeps_inside_the_edge():
    sp = SR.ShapeSpace(boundary=(100, 100, 9900, 9900), min_space=100)
    assert sp.in_bounds((150, 5000))                      # a bare point
    assert not sp.segment_clear((150, 5000), (150, 6000), "N", 250, [])   # 300 wide
    assert sp.segment_clear((250, 5000), (250, 6000), "N", 250, [])
    sp.exempt = {(150, 5000)}                             # a pad centre may sit there
    assert sp.in_bounds((150, 5000), 150)


# --------------------------------------------------------------------------- routing

def test_one_conductor_crossing_takes_a_jumper_with_max_jumpers_zero_unlimited():
    s = _cross_board(dielectric=False)
    d, m, r = _route(s, 1, max_jumpers=0)
    assert r["stats"]["routed"] == r["stats"]["connections"] == 3
    st = H.apply_routed(s, r, m)
    assert st["jumpers"] >= 1 and not st["refused_jumpers"]
    assert s.find_shorts() == [] and s.resistor_crossings() == []
    assert _result_drc(d, r) == []


def test_jumpers_off_leaves_the_crossing_unrouted_not_shorted():
    s = _cross_board(dielectric=False)
    d, m, r = _route(s, 1, max_jumpers=0)
    s2 = _cross_board(dielectric=False)
    d2, m2 = H.export_design(s2, layers=1)
    d2["routing"]["jumpersAllowed"] = False
    r2 = SR.route(copy.deepcopy(d2), local_dielectric=False)
    assert r2["stats"]["routed"] == 2 and len(r2["unrouted"]) == 1
    H.apply_routed(s2, r2, m2)
    assert s2.find_shorts() == []


def test_no_dielectric_layer_means_no_crossovers_and_a_warning():
    s = _cross_board(dielectric=False)
    d, m, r = _route(s, 2)
    assert m["local_diel"] < 0
    assert r["crossovers"] == []
    assert any(i["code"] == "NO_DIELECTRIC_LAYER" for i in r["issues"])
    H.apply_routed(s, r, m)
    assert s.find_shorts() == []


def test_a_crossover_is_insulated_by_a_fill_strip_with_bare_lands():
    s = _cross_board(dielectric=True)
    d, m, r = _route(s, 2)
    assert r["stats"]["routed"] == r["stats"]["connections"]
    assert r["crossovers"]
    st = H.apply_routed(s, r, m)
    assert st["crossovers"] == len(r["crossovers"])
    strips = [sh for sh in s.cell.shapes if sh.layer == DIEL]
    assert strips and all(sh.datatype == s.DIELECTRIC_FILL_DATATYPE for sh in strips)
    # each strip stops at its lands: the COND1/COND2 contact stays bare
    for c in r["crossovers"]:
        ends = [(q["x"], q["y"]) for q in (c["bridge"][0], c["bridge"][-1])]
        for sh in strips:
            for (x, y) in ends:
                assert min(math.hypot(x - px, y - py) for px, py in sh.points) >= c["landUm"] / 2 - 1
    assert s.find_shorts() == [] and s.resistor_crossings() == []


def test_the_strip_inset_never_folds_back_on_a_short_last_leg():
    s = _cross_board(dielectric=True)
    d, m = H.export_design(s, layers=2)
    routed = {"design": d, "traces": [], "windows": [], "unrouted": [], "stats": {}, "issues": [],
              "crossovers": [{"net": "B", "widthUm": 300, "dielWidthUm": 900, "landUm": 300,
                              "bridge": [{"x": 3000, "y": 3000}, {"x": 5000, "y": 3000},
                                         {"x": 5100, "y": 3000}]}]}
    H.apply_routed(s, routed, m)
    (strip,) = [sh for sh in s.cell.shapes if sh.layer == DIEL]
    xs = [p[0] for p in strip.points]
    assert xs == sorted(xs) and min(xs) >= 3150 and max(xs) <= 4950


@pytest.mark.parametrize("seed", range(8))
def test_random_boards_route_with_no_shorts_spacing_or_edge_faults(seed):
    for layers in (1, 2):
        s = _random_board(seed)
        d, m, r = _route(s, layers, max_jumpers=0, passes=2)
        assert _result_drc(d, r) == [], (seed, layers)
        st = H.apply_routed(s, r, m)
        assert not st["refused_jumpers"]
        assert s.find_shorts() == [], (seed, layers)
        assert s.resistor_crossings() == [], (seed, layers)
        assert r["stats"]["routed"] >= 0.8 * r["stats"]["connections"]


def test_the_route_is_deterministic():
    s = _random_board(3)
    d, _m = H.export_design(s, layers=2)
    import json
    a = SR.route(copy.deepcopy(d), passes=2)
    b = SR.route(copy.deepcopy(d), passes=2)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_hybrid_route_runs_the_shape_engine_end_to_end():
    s = _cross_board(dielectric=False)
    st = H.hybrid_route(s, layers=1, max_jumpers=0, engine="shape")
    assert st["engine"] == "shape" and st["jumpers"] >= 1
    assert st["stats"]["routed"] == st["stats"]["connections"]
    assert s.find_shorts() == [] and s.resistor_crossings() == []


def test_unroute_takes_the_routing_out_and_keeps_the_pads():
    from hlayout.core.shapes import Path, Rectangle
    s = _cross_board(dielectric=True)
    pads = sorted(sh.bbox() for sh in s.cell.shapes if isinstance(sh, Rectangle) and sh.pin)
    st = H.hybrid_route(s, layers=2, engine="shape")
    assert st["traces"] and st["crossovers"]
    n_routed = len(s.cell.shapes)
    r = s.unroute()
    assert r["tracks"] >= st["traces"] and r["added"] > 0
    assert not any(isinstance(sh, Path) and sh.layer in (3, 5) for sh in s.cell.shapes)
    assert not any(sh.layer == DIEL for sh in s.cell.shapes)
    assert sorted(sh.bbox() for sh in s.cell.shapes if isinstance(sh, Rectangle) and sh.pin) == pads
    s.undo()
    assert len(s.cell.shapes) == n_routed

