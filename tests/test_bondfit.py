# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fit Bonds to Nets (Session.fit_bonds / FITBONDS, hlayout.scripting.bondfit)."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.shapes import Path, Rectangle
from hlayout.scripting.autoplace_session import _xf
from hlayout.scripting.fanout import die_pads

from test_fanout import _bonds, _die_board, _fingers


def _board_with_target(rotation=0):
    """The 8-pad die U1, fanned out in neat rows, and a one-pad part X1 up
    and to the right on net N7 (U1 pad 7, on the die's right side)."""
    s = _die_board(rotation)
    s.fan_out_die_bonds(fit=False)
    cur = s.current_cell_name
    s.new_cell("TP")
    s.rect(3, -150, -150, 150, 150).pin = "1"
    s.use_cell(cur)
    s.sref("TP", (9000, 7000)).label = "X1"
    from hlayout.io.techfile import load_net_names
    names = load_net_names(s.library)
    names["X1.1"] = "N7"
    s.set_net_names(names, replace=True)
    return s


def _pad7_bond(s):
    r = next(x for x in s.cell.srefs if x.label == "U1")
    p7 = _xf(r)(die_pads(s.library, "DIE", [58])["7"])
    for b in _bonds(s):
        for k in (0, 1):
            if math.dist(b.points[k], p7) <= 2:
                return b, k, p7
    raise AssertionError("no bond on U1.7")


def test_a_finger_moves_towards_its_net_within_the_rules():
    s = _board_with_target()
    b, k, p7 = _pad7_bond(s)
    before = math.dist(b.points[1 - k], (9000, 7000))
    n_fingers = len(_fingers(s))
    r = s.fit_bonds()
    assert r["fingers"] >= 1 and r["saved_um"] > 0
    after = math.dist(b.points[1 - k], (9000, 7000))
    assert after < before - 500
    hb = s._hybrid_bond_rules()
    for bb in _bonds(s):
        ln = math.dist(bb.points[0], bb.points[1])
        assert hb["min"] - 1 <= ln <= hb["max"] + 1
    assert max(d["angle_deg"] for d in s.bond_departures()) <= hb["angle"] + 0.5
    # every bond still ends on a finger, the fingers kept their count and size
    fingers = _fingers(s)
    assert len(fingers) == n_fingers
    sizes = {(round(f.bbox()[2] - f.bbox()[0]), round(f.bbox()[3] - f.bbox()[1])) for f in fingers}
    assert len(sizes) <= 2
    for bb in _bonds(s):
        assert any(f.bbox()[0] - 2 <= q[0] <= f.bbox()[2] + 2 and f.bbox()[1] - 2 <= q[1] <= f.bbox()[3] + 2
                   for f in fingers for q in bb.points)


def test_no_bond_crosses_another_and_fingers_stay_apart():
    s = _board_with_target()
    s.fit_bonds()
    segs = [(tuple(b.points[0]), tuple(b.points[1])) for b in _bonds(s)]

    def cross(a, b, c, d):
        o = lambda p, q, r: (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            assert not cross(*segs[i], *segs[j])
    boxes = [f.bbox() for f in _fingers(s)]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            a, b = boxes[i], boxes[j]
            assert not (a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1])


def test_one_undo_puts_the_fingers_back():
    s = _board_with_target()
    before = sorted(tuple(f.bbox()) for f in _fingers(s))
    s.fit_bonds()
    assert sorted(tuple(f.bbox()) for f in _fingers(s)) != before
    s.undo()
    assert sorted(tuple(f.bbox()) for f in _fingers(s)) == before


def test_fan_out_fits_by_default_and_fit_0_keeps_rows():
    s = _board_with_target()                         # fanned out with fit=False
    rows = sorted(tuple(f.bbox()) for f in _fingers(s))
    r = s.fan_out_die_bonds()                        # again, fitted this time
    assert r["fitted"]["fingers"] >= 1
    assert sorted(tuple(f.bbox()) for f in _fingers(s)) != rows
    s.fan_out_die_bonds(fit=False)
    assert sorted(tuple(f.bbox()) for f in _fingers(s)) == rows


def test_fitbonds_command():
    from hlayout.gui.commands import CommandInterpreter
    s = _board_with_target()
    out = CommandInterpreter(s, console=None).run("FITBONDS refs=U1")
    assert out.startswith("FITBONDS:") and "moved" in out
