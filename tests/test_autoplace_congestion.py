# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Auto Place routing-room (congestion) term: the incremental grid stays
equal to a rebuild, and weighting it opens room between parts."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

np = pytest.importorskip("numpy")

from hlayout.core.autoplace import Part, Placer, PNet


def _board(seed=0, n=10, cong=0.0):
    rnd = random.Random(seed)
    parts = [Part(ref=f"P{i}", cell="C", family="smd", bbox=(-600, -300, 600, 300),
                  pins={"1": (-500, 0), "2": (500, 0)}) for i in range(n)]
    nets = []
    for k in range(n):
        a, b = rnd.randrange(n), rnd.randrange(n)
        if a != b:
            nets.append(PNet(name=f"N{k}", weight=1.0, members=[(a, "1"), (b, "2")]))
    # an edge anchor pulls everything to one corner
    nets.append(PNet(name="E", weight=1.0, members=[(0, "1")], anchors=[(0.0, 0.0)]))
    return Placer(parts, nets, (0, 0, 12000, 8000), grid=50, seed=seed,
                  cong_weight=cong, pitch=400.0)


def test_incremental_congestion_matches_a_rebuild():
    pl = _board(cong=5.0)
    pl.run(effort=0.2)
    inc = pl._overflow()
    pl._cong_build()
    assert pl._overflow() == pytest.approx(inc, rel=1e-6, abs=1e-3)


def test_congestion_off_changes_nothing():
    a, b = _board(seed=3), _board(seed=3)
    a.run(effort=0.2)
    b.run(effort=0.2)
    assert [(p.x, p.y, p.rot) for p in a.parts] == [(p.x, p.y, p.rot) for p in b.parts]
    assert a.congestion() == 0.0


def test_weighting_congestion_lowers_it():
    off, on = _board(seed=1), _board(seed=1, cong=20.0)
    off.run(effort=0.3)
    on.run(effort=0.3)
    probe = _board(seed=1, cong=1.0)
    def overflow(pl):
        for q, p in zip(probe.parts, pl.parts):
            q.x, q.y, q.rot = p.x, p.y, p.rot
        probe._refresh()
        return probe.congestion()
    assert overflow(on) <= overflow(off) + 1e-6


def test_crossing_with_no_room_for_a_crossover_costs_more():
    """Two nets cross at (5000, 4000). With a resistor right there the
    crossing counts 1 + xo_room; moved away, it counts 1."""
    def board(rx, xo):
        parts = [Part(ref="A", cell="C", family="smd", bbox=(-100, -100, 100, 100),
                      pins={"1": (0, 0)}, x=1000, y=1000, placed=True),
                 Part(ref="B", cell="C", family="smd", bbox=(-100, -100, 100, 100),
                      pins={"1": (0, 0)}, x=9000, y=7000, placed=True),
                 Part(ref="C", cell="C", family="smd", bbox=(-100, -100, 100, 100),
                      pins={"1": (0, 0)}, x=1000, y=7000, placed=True),
                 Part(ref="D", cell="C", family="smd", bbox=(-100, -100, 100, 100),
                      pins={"1": (0, 0)}, x=9000, y=1000, placed=True),
                 Part(ref="R", cell="R", family="resistor", bbox=(-300, -150, 300, 150),
                      pins={}, x=rx, y=4000, placed=True)]
        nets = [PNet("N1", 1.0, [(0, "1"), (1, "1")]), PNet("N2", 1.0, [(2, "1"), (3, "1")])]
        return Placer(parts, nets, (0, 0, 10000, 8000), cross_weight=1.0, xo_room=xo)
    assert board(5000, 2.0).crossings() == 3.0
    assert board(9500, 2.0).crossings() == 1.0
    assert board(5000, 0.0).crossings() == 1


def test_footprints_keep_the_copper_spacing_whatever_the_clearance():
    """Auto Place's clearance 0 let two parts' pads touch (a short): the
    footprint gap is never below the router's copper spacing."""
    from hlayout.scripting import Session
    from hlayout.scripting.autoplace_session import _copper_space
    s = Session.new("SP")
    s.set_freerouting_rules(clearance=150)
    assert _copper_space(s) >= 150
