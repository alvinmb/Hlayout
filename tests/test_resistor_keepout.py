# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""A printed resistor's glaze is a route keep-out, whatever keepout_bodies says."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from shapely.geometry import Point, Polygon

from hlayout.io.techfile import (load_resistor_geometry_rules, load_route_rules,
                                 save_resistor_geometry_rules, save_route_rules)
from hlayout.scripting import Session


def _board():
    s = Session.new("RK")
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr.update(layer_a=3, layer_b=5)
    save_route_rules(s.library, rr)
    g = load_resistor_geometry_rules(s.library); g["conductor_layer"] = 3
    save_resistor_geometry_rules(s.library, g)
    s.rect(57, -20000, -20000, 20000, 20000)                         # the substrate
    s.generate_resistor(0, 0, 1000, 2000, ref_des="R1", ink_rsh=1000)
    return s


@pytest.mark.parametrize("bodies", [False, True])
def test_glaze_is_kept_out_on_both_layers(bodies):
    s = _board()
    R = dict(s.freerouting_rules(), keepout_bodies=bodies)
    ko = s._route_keepouts(s.cell, (3, 5), R)
    for lay in (3, 5):
        polys = [Polygon(p) for l, p in ko if l == lay]
        assert any(p.contains(Point(0, 0)) for p in polys)          # the resistor body
        # its terminations stay reachable
        assert not any(p.contains(Point(-1450, 0)) for p in polys)


def test_rescross_finds_a_track_through_a_resistor():
    s = _board()
    s.path(3, [(0, -3000), (0, 3000)], 200)                          # straight through R1
    s.path(3, [(5000, -3000), (5000, 3000)], 200)                    # clear of it
    hits = s.resistor_crossings()
    assert len(hits) == 1 and hits[0]["ref"] == "R1"
