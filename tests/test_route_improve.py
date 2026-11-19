# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Improve-for-routing: never makes the routed count worse, keeps locked
parts still, and every kept change is undoable."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")
pytest.importorskip("scipy")

from hlayout.scripting import route_improve as RI

import test_shape_router as T


def _board():
    """A die U1 whose two nets must leave between two resistors placed
    one track too close together."""
    s = T._cross_board(dielectric=True)
    return s


def test_never_worse_and_locks_hold():
    s = T._random_board(2)
    placed = {r.label: tuple(r.origin) for r in s.cell.srefs if r.label}
    if not placed:
        pytest.skip("no placed parts")
    lock = sorted(placed)[0]
    s.lock_parts(lock)
    res = RI.improve_for_routing(s, passes=4, iters=5, seed=1)
    assert res["after"][0] >= res["before"][0]
    assert dict((r.label, tuple(r.origin)) for r in s.cell.srefs if r.label)[lock] == placed[lock]
