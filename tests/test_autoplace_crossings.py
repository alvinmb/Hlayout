# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Auto Place's SLI-crossing cost: crossings of two nets' straight-line
interconnects are counted and the placer avoids them."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.autoplace import Part, PNet, Placer, seg_crossings


def test_crossing_segments_are_counted_touching_ones_are_not():
    assert seg_crossings([((0, 0), (10, 10))], [((0, 10), (10, 0))]) == 1
    assert seg_crossings([((0, 0), (10, 0))], [((0, 5), (10, 5))]) == 0     # parallel
    assert seg_crossings([((0, 0), (10, 10))], [((10, 10), (20, 0))]) == 0  # share an end


def _placer(cross_weight):
    """Two fixed anchors left (A at the top, B at the bottom) and two movable
    one-pin parts that start swapped on the right, so nets A and B cross."""
    box = (-200.0, -200.0, 200.0, 200.0)
    parts = [Part("PA", "C", "smd", box, {"1": (0.0, 0.0)}, x=8000.0, y=1000.0, placed=True),
             Part("PB", "C", "smd", box, {"1": (0.0, 0.0)}, x=8000.0, y=9000.0, placed=True)]
    nets = [PNet("A", 1.0, [(0, "1")], [(1000.0, 9000.0)]),
            PNet("B", 1.0, [(1, "1")], [(1000.0, 1000.0)])]
    return Placer(parts, nets, (0, 0, 10000, 10000), grid=100.0, seed=1,
                  cross_weight=cross_weight)


def test_the_placer_uncrosses_two_nets_when_crossings_cost():
    p = _placer(50000.0)
    assert p.crossings() == 1
    p.run(effort=0.5, improve=True)
    assert p.crossings() == 0


def test_crossings_are_tracked_through_moves():
    p = _placer(3000.0)
    p.run(effort=0.3, improve=True)
    # the incrementally kept segments agree with a fresh count
    kept = p.crossings()
    p._refresh()
    assert p.crossings() == kept
