# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""One via definition (Session.via_spec / fix_vias) and printed-size windows."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from shapely.geometry import Polygon as SP
from shapely.ops import unary_union

from hlayout.io.specctra import find_vias
from hlayout.io.techfile import (load_dielectric_rules, load_route_rules, save_dielectric_rules,
                                 save_route_rules)
from hlayout.scripting import Session


def _board():
    s = Session.new("VS")
    s.new_cell("VIA35")
    s.rect(3, -150, -150, 150, 150)
    s.rect(5, -150, -150, 150, 150)
    s.rect(4, -200, -200, 200, 200)
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr.update(layer_a=3, layer_b=5, via_cell="VIA35")
    save_route_rules(s.library, rr)
    dr = load_dielectric_rules(s.library); dr.update(layer=4, clearance=100, via_window_margin=100)
    save_dielectric_rules(s.library, dr)
    s.set_freerouting_rules(signal_via=200, power_via=400, power_nets=["VCC"])
    s.rect(4, -10000, -10000, 10000, 10000)                          # fill boundary
    s.rect(3, -5400, -400, -4600, 400, pin="1")
    s.path(3, [(-4600, 0), (0, 0)], 200)
    s.drop_via(0, 0, 3, 5, size=350, dielectric_layer=4, dielectric_window=900)   # odd one
    s.path(5, [(0, 0), (0, 3000)], 200)
    s.sref("VIA35", (3000, 3000))
    s.rect(4, 5000, -2000, 6000, -1000)                               # a keep-out
    s.set_net_names({"IO.1": "A"})
    s.generate_dielectric()
    return s


def test_via_spec_window_is_pad_plus_two_margins():
    s = _board()
    assert s.via_spec() == {"pad": 200.0, "window": 400.0, "layer": 4}
    assert s.via_spec(power=True)["window"] == 600.0


def test_fix_vias_redraws_flat_vias_and_the_via_cell():
    s = _board()
    r = s.fix_vias()
    assert r == {"vias": 1, "via_cells": 1}
    top = s.library.cells["TOP"]
    (x, y, w), = find_vias(top, (3, 5))
    assert (x, y, w) == (0, 0, 200)
    wins = [sh.bbox() for sh in top.shapes if sh.layer == 4 and getattr(sh, "datatype", 0) == 0
            and abs(sh.bbox()[0] + 200) < 1]
    assert wins and wins[0] == (-200, -200, 200, 200)
    vc = s.library.cells["VIA35"]
    assert sorted((sh.layer, sh.bbox()) for sh in vc.shapes) == [
        (3, (-100, -100, 100, 100)), (4, (-200, -200, 200, 200)), (5, (-100, -100, 100, 100))]


def test_a_via_window_is_printed_at_its_drawn_size_but_keep_outs_get_the_clearance():
    s = _board()
    s.fix_vias()
    top = s.library.cells["TOP"]
    fill = unary_union([SP(sh.points) for sh in top.shapes if sh.layer == 4
                        and getattr(sh, "datatype", 0) == 1])
    from shapely.geometry import Point, box
    # via window 400 square at (0,0): open exactly there, closed just outside
    assert not fill.intersects(box(-195, -195, 195, 195))
    assert fill.contains(Point(0, 260))
    # keep-out grown by 0.1 mm
    assert not fill.intersects(box(4910, -2090, 6090, -910))
    assert fill.contains(Point(4850, -1500))


def test_dsn_vias_keep_others_clear_of_the_window(tmp_path):
    s = _board()
    s.fix_vias()
    p = tmp_path / "b.dsn"
    s.export_dsn(str(p))
    txt = p.read_text()
    assert "(padstack HL_VIA_SIG" in txt or "VIA" in txt
    import re
    m = re.search(r"\(padstack (\S*SIG\S*)\s+\(shape \(rect \S+ (-?[\d.]+)", txt)
    assert m and abs(float(m.group(2)) + 200) < 1                      # half of the 400 window


def test_fix_vias_rebuilds_the_route_tools_via_cell_even_unplaced():
    s = _board()
    top = s.library.cells["TOP"]
    top.srefs[:] = []                                                  # nothing placed
    r = s.fix_vias()
    assert r["via_cells"] == 1
    assert (3, (-100, -100, 100, 100)) in [(sh.layer, sh.bbox()) for sh in s.library.cells["VIA35"].shapes]
