# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fan Out Die Bonds (Session.fan_out_die_bonds / FANOUT)."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.errors import LayoutError
from hlayout.core.shapes import Path, Rectangle
from hlayout.io.techfile import load_bond_rules, load_route_rules, save_bond_rules, save_route_rules
from hlayout.scripting import Session


def _die_board(rotation=0):
    """An 8-pad dual-in-line die (like OP249: pads 1-4 down the left, 5-8
    up the right) on a 1st-conductor attach pad, placed as U1."""
    s = Session.new("FAN")
    rr = load_route_rules(s.library); rr.update(layer_a=3, layer_b=5, boundary_layer=57)
    save_route_rules(s.library, rr)
    br = load_bond_rules(s.library)
    br.update(layer=59, width=30, pad=100, min_length=300, max_length=5000, die_pad_layers=[58])
    save_bond_rules(s.library, br)
    s.new_cell("PAD")
    s.rect(58, -50, -50, 50, 50)
    s.new_cell("DIE")
    s.rect(3, -1300, -1800, 1300, 1800)                         # die attach pad
    for i, (x, y) in enumerate([(-765, 1000), (-765, 333), (-765, -333), (-765, -1000),
                                (765, -1000), (765, -333), (765, 333), (765, 1000)], 1):
        s.sref("PAD", (x, y)).pin = str(i)
    s.new_cell("TOP")
    s.rect(57, -20000, -20000, 20000, 20000)
    s.sref("DIE", (0, 0), rotation=rotation).label = "U1"
    s.set_net_names({f"U1.{p}": f"N{p}" for p in (1, 2, 3, 5, 6, 7, 8)}
                    | {"U1.4": "unconnected-(U1-Pad4)"})
    return s


def _bonds(s):
    return [sh for sh in s.cell.shapes if isinstance(sh, Path) and sh.layer == 59]


def _fingers(s):
    return [sh for sh in s.cell.shapes if isinstance(sh, Rectangle) and sh.layer == 3]


def _cross(a, b, c, d):
    o = lambda p, q, r: (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0


@pytest.mark.parametrize("rot", [0, 90])
def test_every_pad_with_a_net_gets_a_finger_and_a_bond(rot):
    s = _die_board(rot)
    r = s.fan_out_die_bonds()
    assert r["dies"]["U1"] == ["1", "2", "3", "5", "6", "7", "8"] and not r["skipped"]
    bonds, fingers = _bonds(s), _fingers(s)
    assert len(bonds) == 7 and len(fingers) == 7
    segs = [(tuple(b.points[0]), tuple(b.points[-1])) for b in bonds]
    assert not any(_cross(*a, *b) for i, a in enumerate(segs) for b in segs[i + 1:])
    for a, b in segs:
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        assert 300 <= L <= 5000
    die = (-1300, -1800, 1300, 1800) if rot == 0 else (-1800, -1300, 1800, 1300)
    for f in fingers:                                   # clear of the die attach pad
        x0, y0, x1, y1 = f.bbox()
        assert x1 <= die[0] - 249 or x0 >= die[2] + 249 or y1 <= die[1] - 249 or y0 >= die[3] + 249
    for i, f in enumerate(fingers):                     # 150 um apart at least
        for g in fingers[i + 1:]:
            a, b = f.bbox(), g.bbox()
            gx = max(a[0] - b[2], b[0] - a[2])
            gy = max(a[1] - b[3], b[1] - a[3])
            assert max(gx, gy) >= 149
    # the landings are named as the die pads
    names = {tuple(p[2]) for p in s.bond_landings()}
    assert ("U1.1",) in names and ("U1.8",) in names


def test_rerun_replaces_and_all_pads_bonds_every_pad():
    s = _die_board()
    s.fan_out_die_bonds()
    s.fan_out_die_bonds(all_pads=True, gap=400)
    assert len(_bonds(s)) == 8 and len(_fingers(s)) == 8
    s.undo()
    assert len(_bonds(s)) == 7


def test_fanout_needs_the_die_pad_layers():
    s = _die_board()
    br = load_bond_rules(s.library); br["die_pad_layers"] = []
    save_bond_rules(s.library, br)
    with pytest.raises(LayoutError, match="die pad layers"):
        s.fan_out_die_bonds()


def test_a_backside_contact_is_not_a_bond_pad():
    from hlayout.scripting.fanout import die_pads
    s = _die_board()
    s.new_cell("ZPAD")
    s.rect(58, -100, -100, 100, 100)
    s.new_cell("ZENER")
    s.rect(3, -500, -500, 500, 500, pin="1")          # cathode: the die attach pad
    s.sref("ZPAD", (0, 0)).pin = "2"                    # anode: the bond pad
    assert die_pads(s.library, "ZENER", [58]) == {"2": (0.0, 0.0)}


@pytest.mark.parametrize("top_pads, bonds", [(["2"], 1), (["1", "2"], 2)])
def test_diode_one_bond_transistor_two(top_pads, bonds):
    """A diode / zener: cathode on the backside (the attach pad), one bond
    to the anode. A transistor: collector on the backside, two bonds (base
    and emitter)."""
    s = _die_board()
    s.new_cell("BPAD")
    s.rect(58, -60, -60, 60, 60)
    s.new_cell("SMALLDIE")
    back = "3" if len(top_pads) == 2 else "1"
    s.rect(3, -500, -500, 500, 500, pin=back)            # backside contact
    for k, p in enumerate(top_pads):
        s.sref("BPAD", (-200 + 400 * k if len(top_pads) == 2 else 0, 0)).pin = p
    s.use_cell("TOP")
    s.sref("SMALLDIE", (8000, 0)).label = "Q1"
    s.set_net_names({f"Q1.{p}": f"Q{p}" for p in top_pads + [back]}, replace=False)
    r = s.fan_out_die_bonds(refs=["Q1"])
    assert r["dies"]["Q1"] == sorted(top_pads) and r["fingers"] == bonds


def test_a_backside_contact_stays_reachable_through_the_die_keepout():
    from shapely.geometry import Point, Polygon
    s = _die_board()
    s.new_cell("ZPAD")
    s.rect(58, -100, -100, 100, 100)
    s.new_cell("ZENER")
    s.rect(20, -250, -250, 250, 250)
    s.rect(3, -500, -500, 500, 500, pin="1")
    s.sref("ZPAD", (0, 0)).pin = "2"
    s.use_cell("TOP")
    s.sref("ZENER", (9000, 0)).label = "D1"
    ko = s._route_keepouts(s.cell, (3, 5), s.freerouting_rules())
    polys = [Polygon(p) for l, p in ko if l == 3]
    assert not any(p.contains(Point(9000 + 500, 0)) for p in polys)    # the cathode pad edge


def test_the_router_sees_the_fanned_out_fingers_as_drawn(tmp_path):
    s = _die_board()
    s.fan_out_die_bonds()
    p = tmp_path / "f.dsn"
    s.export_dsn(str(p))
    txt = p.read_text()
    assert "PAD_L3_250x400" in txt or "PAD_L3_400x250" in txt     # the fingers, not 400 squares
    assert "PAD_L3_400x400" not in txt


def test_move_bond_finger_moves_a_fanned_out_finger_and_its_bond():
    s = _die_board()
    s.fan_out_die_bonds()
    f = sorted(_fingers(s), key=lambda r: r.bbox()[0])[0]
    x0, y0, x1, y1 = f.bbox()
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r = s.move_bond_finger((cx, cy), (cx - 300, cy))
    assert r["wires"] and abs(f.bbox()[0] - (x0 - 300)) < 1
    ends = [tuple(b.points[-1]) for b in _bonds(s)] + [tuple(b.points[0]) for b in _bonds(s)]
    assert (round(cx - 300), round(cy)) in ends
    # the bond's end disc went with it
    discs = [sh for sh in s.cell.shapes if sh.layer == 59 and not isinstance(sh, Path)]
    assert any(abs((d.bbox()[0] + d.bbox()[2]) / 2 - (cx - 300)) <= 2 for d in discs)
    s.undo()
    assert abs(f.bbox()[0] - x0) < 1 or abs(sorted(_fingers(s), key=lambda r: r.bbox()[0])[0].bbox()[0] - x0) < 1


def test_a_finger_whose_bond_was_moved_off_it_still_moves_and_rebonds():
    s = _die_board()
    s.fan_out_die_bonds()
    f = sorted(_fingers(s), key=lambda r: r.bbox()[0])[0]
    x0, y0, x1, y1 = f.bbox()
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    s.move_wire_bond((cx, cy), (x0 - 150, cy))                   # the bond end now off the finger
    r = s.move_bond_finger((cx, cy), (cx - 400, cy))
    assert r["rebonded"] and r["wires"]
    ends = [tuple(b.points[k]) for b in _bonds(s) for k in (0, -1)]
    assert (round(cx - 400), round(cy)) in ends                  # back on the finger's new centre


def test_moving_a_bonded_die_takes_its_bonds_and_fingers_with_it():
    """Auto Place's apply(): a die that has been fanned out keeps its wire
    bonds and bond fingers -- they move and turn with it."""
    from types import SimpleNamespace
    from hlayout.scripting.autoplace_session import apply
    s = _die_board(0)
    s.fan_out_die_bonds()
    rel = lambda pt, o, rot: (lambda x, y: (round(x), round(y)))(
        *({0: (pt[0] - o[0], pt[1] - o[1]),
           90: (pt[1] - o[1], -(pt[0] - o[0]))}[rot]))
    before = sorted(tuple(rel(p, (0, 0), 0) for p in b.points) for b in _bonds(s))
    fb = sorted(tuple(sorted(rel(p, (0, 0), 0) for p in f.points)) for f in _fingers(s))
    ballc = len([sh for sh in s.cell.shapes if sh.layer == 59])
    placer = SimpleNamespace(parts=[SimpleNamespace(ref="U1", cell="DIE", x=5000, y=-3000, rot=90)])
    info = {"cell": "TOP", "netlist": SimpleNamespace(pad_nets=lambda: {})}
    with s._batch():
        apply(s, placer, info)
    assert info["carried"] == {"bonds": 7, "fingers": 7}
    after = sorted(tuple(rel(p, (5000, -3000), 90) for p in b.points) for b in _bonds(s))
    assert after == before
    fa = sorted(tuple(sorted(rel(p, (5000, -3000), 90) for p in f.points)) for f in _fingers(s))
    assert fa == fb
    assert len([sh for sh in s.cell.shapes if sh.layer == 59]) == ballc
    # every bond still starts on one of the die's pads where the die now is
    from hlayout.scripting.fanout import die_pads
    from hlayout.scripting.autoplace_session import _xf
    r = next(r for r in s.cell.srefs if r.label == "U1")
    pads = [_xf(r)(v) for v in die_pads(s.library, "DIE", [58]).values()]
    for b in _bonds(s):
        assert any(abs(b.points[0][0] - x) <= 1 and abs(b.points[0][1] - y) <= 1 for x, y in pads) or \
            any(abs(b.points[1][0] - x) <= 1 and abs(b.points[1][1] - y) <= 1 for x, y in pads)

