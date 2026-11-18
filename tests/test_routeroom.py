# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""RouteRoom: capacity and demand on the gcell grid, and incremental
updates equal to a rebuild."""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core.autoplace import Part, Placer, PNet
from hlayout.core.routeroom import RouteRoom

P = 400.0          # track pitch
G = 1600.0         # gcell = 4 pitches


def _part(ref, x, y, w=1200, h=600, fam="smd"):
    p = Part(ref=ref, cell="C", family=fam, bbox=(-w / 2, -h / 2, w / 2, h / 2),
             pins={"1": (-w / 2 + 100, 0), "2": (w / 2 - 100, 0)})
    p.x, p.y, p.placed = float(x), float(y), True
    return p


def _placer(parts, nets, region=(0, 0, 8000, 8000)):
    return Placer(parts, nets, region, grid=50.0)


def _room(pl, area=(0, 0, 8000, 8000), **kw):
    return RouteRoom(area, P, G, **kw).build(pl)


def test_empty_area_capacity_is_cut_over_pitch():
    pl = _placer([_part("A", 7000, 7000)], [])
    r = _room(pl)
    assert (r.nx, r.ny) == (5, 5)
    # an edge far from the part: 1600 / 400 = 4 tracks
    assert r.capH[r._hi(0, 0)] == 4
    assert r.capV[r._vi(0, 0)] == 4
    assert r.overflow == 0


def test_part_on_a_cut_takes_its_width_out():
    # a 1200 tall part straddling the vertical cut x = 1600, in gcell row 0
    pl = _placer([_part("A", 1600, 800, w=600, h=1200)], [])
    r = _room(pl)
    assert r.capH[r._hi(0, 0)] == 1            # (1600 - 1200) / 400


def test_area_edge_clips_capacity():
    # area 7000 tall: the top row of gcells is 600 tall -> 1 track
    pl = _placer([], [])
    r = RouteRoom((0, 0, 8000, 7000), P, G).build(pl)
    assert r.capH[r._hi(0, r.ny - 1)] == 1


def test_straight_net_demand_one_track_per_cut():
    a, b = _part("A", 500, 800), _part("B", 7500, 800)
    pl = _placer([a, b], [PNet("N", 1.0, [(0, "2"), (1, "1")])])
    r = _room(pl)
    row = [r.dH[r._hi(i, 0)] for i in range(r.nx - 1)]
    assert row == [1.0, 1.0, 1.0, 1.0]
    assert sum(r.dV) == 0


def test_l_route_splits_half_and_half():
    a, b = _part("A", 500, 500), _part("B", 7300, 7300)
    pl = _placer([a, b], [PNet("N", 1.0, [(0, "2"), (1, "1")])])
    r = _room(pl)
    # two Ls, each crossing 4 H cuts and 4 V cuts at weight 0.5
    assert sum(r.dH) == pytest.approx(4.0)
    assert sum(r.dV) == pytest.approx(4.0)
    assert max(r.dH) == pytest.approx(0.5)


def test_own_escape_inside_footprint_not_counted():
    # a wide part straddling the cut x = 1600: its pin 1 is left of the cut,
    # pin 2 right; the SLI between its own pins crosses the cut inside it
    a = _part("A", 1600, 800, w=2400, h=600)
    pl = _placer([a], [PNet("N", 1.0, [(0, "1"), (0, "2")])])
    r = _room(pl)
    assert r.dH[r._hi(0, 0)] == 0.0


def test_three_nets_through_a_one_track_gap_overflow_two():
    # two tall blockers leave a 400 um (1 track) gap on the cut x = 3200
    blk1 = _part("B1", 3200, 400, w=600, h=800)        # y 0..800
    blk2 = _part("B2", 3200, 1400, w=600, h=400)       # y 1200..1600
    parts = [blk1, blk2]
    nets = []
    for k, y in enumerate((900, 1000, 1100)):
        parts += [_part(f"L{k}", 600, y, w=200, h=50), _part(f"R{k}", 5800, y, w=200, h=50)]
        nets.append(PNet(f"N{k}", 1.0, [(2 + 2 * k, "2"), (3 + 2 * k, "1")]))
    pl = _placer(parts, nets)
    r = _room(pl)
    e = r._hi(1, 0)                                    # cut x = 3200, row 0
    assert r.capH[e] == 1
    assert r.dH[e] == 3.0
    assert r.overflow >= 2.0


def test_under_chip_opens_the_middle():
    pl = _placer([_part("A", 1600, 800, w=600, h=1600)], [])
    shut = _room(pl).capH[0]
    open_ = _room(pl, under_chip=True).capH[0]
    assert open_ > shut


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_incremental_equals_rebuild(seed):
    rnd = random.Random(seed)
    n = 12
    parts = [_part(f"P{i}", rnd.uniform(500, 7500), rnd.uniform(500, 7500)) for i in range(n)]
    nets = []
    for k in range(16):
        m = rnd.sample(range(n), rnd.choice((2, 2, 3, 4)))
        nets.append(PNet(f"N{k}", 1.0, [(i, rnd.choice("12")) for i in m]))
    nets.append(PNet("IO", 1.0, [(0, "1")], anchors=[(0.0, 4000.0)]))
    pl = _placer(parts, nets)
    r = _room(pl)
    for _ in range(200):
        moved = rnd.sample(range(n), rnd.choice((1, 2)))
        for i in moved:
            parts[i].x = min(7800, max(200, parts[i].x + rnd.uniform(-1500, 1500)))
            parts[i].y = min(7800, max(200, parts[i].y + rnd.uniform(-1500, 1500)))
            if rnd.random() < 0.3:
                parts[i].rot = (parts[i].rot + 90) % 360
        r.update(moved)
    fresh = _room(pl)
    assert r.capH == fresh.capH and r.capV == fresh.capV
    assert r.dH == pytest.approx(fresh.dH) and r.dV == pytest.approx(fresh.dV)
    assert r.overflow == pytest.approx(fresh.overflow)
    assert r.overflow2 == pytest.approx(fresh.overflow2)


def test_update_then_undo_restores():
    rnd = random.Random(7)
    parts = [_part(f"P{i}", rnd.uniform(500, 7500), rnd.uniform(500, 7500)) for i in range(8)]
    nets = [PNet(f"N{k}", 1.0, [(k, "1"), ((k + 3) % 8, "2")]) for k in range(8)]
    pl = _placer(parts, nets)
    r = _room(pl)
    before = (r.overflow, list(r.capH), list(r.dV))
    x0 = parts[2].x
    parts[2].x = 4000
    r.update([2])
    parts[2].x = x0
    r.update([2])
    assert r.overflow == pytest.approx(before[0])
    assert r.capH == before[1] and r.dV == pytest.approx(before[2])


# ---------------------------------------------------------------- in the Placer
def _crowd(seed, room):
    rnd = random.Random(seed)
    n = 14
    parts = [_part(f"P{i}", 0, 0) for i in range(n)]
    for p in parts:
        p.placed = False
    nets = []
    for k in range(22):
        m = rnd.sample(range(n), 2)
        nets.append(PNet(f"N{k}", 1.0, [(m[0], "1"), (m[1], "2")]))
    for k, y in enumerate((1000, 3000, 5000, 7000)):
        nets.append(PNet(f"IO{k}", 1.0, [(k, "1")], anchors=[(0.0, float(y))]))
    return Placer(parts, nets, (0, 0, 8000, 8000), grid=50.0, seed=seed,
                  cross_weight=0.0, room_weight=room)


def test_placer_room_stays_equal_to_a_rebuild():
    pl = _crowd(3, room=2000.0)
    pl.run(effort=0.3)
    inc = pl.room.overflow2
    fresh = RouteRoom(pl.region, pl.pitch, 4.0 * pl.pitch).build(pl)
    assert inc == pytest.approx(fresh.overflow2)


def test_routing_room_weight_lowers_overflow():
    def ov(room, seed):
        pl = _crowd(seed, room)
        pl.run(effort=0.5)
        return RouteRoom(pl.region, pl.pitch, 4.0 * pl.pitch).build(pl).overflow2
    off = sum(ov(0.0, s) for s in (1, 2, 3))
    on = sum(ov(20000.0, s) for s in (1, 2, 3))
    assert on < off
