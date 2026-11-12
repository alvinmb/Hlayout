# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Hybrid Route: export to hybrid.json, apply a routed result, and (when the
router is built here) a real single-layer route with jumpers."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.errors import LayoutError
from hlayout.core.shapes import Path, Rectangle
from hlayout.io.techfile import (load_bond_rules, load_resistor_geometry_rules, load_route_rules,
                                 save_bond_rules, save_resistor_geometry_rules, save_route_rules)
from hlayout.scripting import Session
from hlayout.scripting import hybridroute as H


def _board():
    """A 12 x 8 mm substrate: four I/O pads in the corners, nets A (bottom
    left to top right) and B (top left to bottom right) that must cross,
    and a printed resistor R1 in the middle on net C."""
    s = Session.new("HR")
    rr = load_route_rules(s.library); rr.update(layer_a=3, layer_b=5, boundary_layer=57)
    save_route_rules(s.library, rr)
    g = load_resistor_geometry_rules(s.library); g["conductor_layer"] = 3
    save_resistor_geometry_rules(s.library, g)
    br = load_bond_rules(s.library); br.update(layer=59, die_pad_layers=[58])
    save_bond_rules(s.library, br)
    s.new_cell("TOP")
    s.rect(57, 0, 0, 12000, 8000)
    # the pads hard by the edge: no track can go round them
    for pin, (x, y) in {"1": (550, 550), "2": (11450, 7450), "3": (550, 7450),
                        "4": (11450, 550)}.items():
        s.rect(3, x - 300, y - 300, x + 300, y + 300, pin=pin)
    s.generate_resistor(6000, 4000, 600, 1200, ref_des="R1", ink_rsh=1000)
    s.set_net_names({"IO.1": "A", "IO.2": "A", "IO.3": "B", "IO.4": "B",
                     "R1.1": "C", "R1.2": "C"})
    return s


def test_export_describes_the_board():
    s = _board()
    d, m = H.export_design(s, layers=1)
    assert d["substrate"]["widthUm"] == 12000 and d["routing"]["routingLayers"] == ["COND1"]
    ids = {p["id"]: p for p in d["substratePads"]}
    assert ids["IO1"]["net"] == "A" and ids["IO1"]["position"] == {"x": 550, "y": 550}
    (r1,) = d["components"]
    assert r1["refdes"] == "R1" and r1["kind"] == "PRINTED_RESISTOR"
    assert r1["obstacleLayers"] == ["COND1"] and r1["pinA"]["net"] == "C"
    d2, _ = H.export_design(s, layers=2)
    assert d2["routing"]["routingLayers"] == ["COND1", "COND2"]


def test_apply_draws_the_result_and_a_rerun_replaces_it():
    s = _board()
    s.path(3, [(500, 4000), (2000, 4000)], 300)                 # an old track
    d, m = H.export_design(s, layers=1)
    routed = {"design": dict(d, substratePads=d["substratePads"] + [
        {"id": "JL_1A", "net": "A", "role": "JUMPER_LAND", "position": {"x": 5000, "y": 2000},
         "widthUm": 300, "heightUm": 300, "layer": "COND1"},
        {"id": "JL_1B", "net": "A", "role": "JUMPER_LAND", "position": {"x": 5000, "y": 3000},
         "widthUm": 300, "heightUm": 300, "layer": "COND1"}],
        wires=d["wires"] + [{"id": "WJ_1", "net": "A",
                             "from": {"kind": "SUBSTRATE_PAD", "pad": "JL_1A"},
                             "to": {"kind": "SUBSTRATE_PAD", "pad": "JL_1B"}}]),
        "traces": [{"net": "A", "layer": "COND1", "widthUm": 300,
                    "points": [{"x": 550, "y": 550}, {"x": 5000, "y": 550}, {"x": 5000, "y": 2000}]}],
        "windows": [], "unrouted": [], "stats": {"connections": 3, "routed": 3}, "issues": []}
    st = H.apply_routed(s, routed, m)
    assert st["traces"] == 1 and st["jumpers"] == 1 and st["jumper_lands"] == 2
    assert st["removed_tracks"] == 1
    lands = [sh for sh in s.cell.shapes if isinstance(sh, Rectangle) and sh.layer == 3
             and sh.bbox() == (4850, 1850, 5150, 2150)]
    assert len(lands) == 1
    assert any(isinstance(sh, Path) and sh.layer == 59 for sh in s.cell.shapes)
    H.apply_routed(s, routed, m)                                # again: no duplicates
    assert len([sh for sh in s.cell.shapes if isinstance(sh, Rectangle) and sh.layer == 3
                and sh.bbox() == (4850, 1850, 5150, 2150)]) == 1
    s.undo()
    s.undo()
    assert any(isinstance(sh, Path) and sh.layer == 3 and sh.points[0] == (500, 4000)
               for sh in s.cell.shapes)


def test_a_real_single_layer_route_uses_a_jumper(tmp_path):
    try:
        H.router_command()
    except LayoutError as e:
        pytest.skip(str(e))
    s = _board()
    st = s.hybrid_route(layers=1, grid=100, out_dir=str(tmp_path), timeout=180)
    assert st["stats"].get("routed") == st["stats"].get("connections")
    assert st["jumpers"] >= 1 and not s.resistor_crossings()


def test_a_jumper_over_a_part_is_refused_with_its_lands_and_stubs():
    s = _board()
    d, m = H.export_design(s, layers=1)
    routed = {"design": dict(d, substratePads=d["substratePads"] + [
        {"id": "JL_9A", "net": "A", "role": "JUMPER_LAND", "position": {"x": 6000, "y": 2500},
         "widthUm": 300, "heightUm": 300, "layer": "COND1"},
        {"id": "JL_9B", "net": "A", "role": "JUMPER_LAND", "position": {"x": 6000, "y": 5500},
         "widthUm": 300, "heightUm": 300, "layer": "COND1"}],
        wires=d["wires"] + [{"id": "WJ_9", "net": "A",                 # straight over R1
                             "from": {"kind": "SUBSTRATE_PAD", "pad": "JL_9A"},
                             "to": {"kind": "SUBSTRATE_PAD", "pad": "JL_9B"}}]),
        "traces": [{"net": "A", "layer": "COND1", "widthUm": 300,
                    "points": [{"x": 550, "y": 550}, {"x": 6000, "y": 550}, {"x": 6000, "y": 2500}]}],
        "windows": [], "unrouted": [], "stats": {}, "issues": []}
    st = H.apply_routed(s, routed, m)
    assert st["refused_jumpers"] == [{"wire": "WJ_9", "net": "A", "over": "R1"}]
    assert st["jumpers"] == 0 and st["jumper_lands"] == 0 and st["traces"] == 0


def test_parts_are_obstacles_whatever_keepout_bodies_says():
    s = _board()
    s.new_cell("CAP")
    s.rect(61, -1600, -800, 1600, 800)                          # body outline
    s.rect(3, -1600, -700, -1000, 700, pin="1")
    s.rect(3, 1000, -700, 1600, 700, pin="2")
    s.use_cell("TOP")
    s.sref("CAP", (3000, 4000)).label = "C1"
    d, _m = H.export_design(s, layers=1)
    c1 = next(c for c in d["components"] if c["refdes"] == "C1")
    assert c1["kind"] == "CHIP" and c1["obstacleLayers"] == ["COND1"]
    assert c1["bodyWidthUm"] == 1600                            # the outline, not the pads


def test_export_power_width_unconnected_pads_and_existing_copper():
    s = _board()
    s.rect(3, 5700, 250, 6300, 850, pin="9")                      # an I/O pad with no net
    s.set_freerouting_rules(power_nets=["A"], signal_width=300, power_width=600)
    s.path(3, [(550, 550), (3000, 550)], 300)                     # an A track off pad 1
    d, m = H.export_design(s, layers=1, keep_existing=True)
    R = d["routing"]
    assert R["netWidths"] == {"A": 600}
    nc = next(p for p in d["substratePads"] if p["id"] == "IO9")
    assert nc["net"].startswith("~NC_")                           # nothing may touch it
    (fx,) = R["fixedTraces"]
    assert fx["net"] == "A" and R["fixedJoins"] == ["IO1"] and fx["widthUm"] == 300
    d0, _ = H.export_design(s, layers=1)                          # from scratch: nothing kept
    assert d0["routing"]["fixedTraces"] == []


def test_two_conductors_keep_the_second_off_open_dielectric_and_use_no_jumpers():
    from hlayout.io.techfile import load_dielectric_rules, save_dielectric_rules
    s = _board()
    dr = load_dielectric_rules(s.library); dr["layer"] = 4
    save_dielectric_rules(s.library, dr)
    d, _ = H.export_design(s, layers=2)
    R = d["routing"]
    assert R["jumpersAllowed"] is False and R["routingLayers"] == ["COND1", "COND2"]
    assert R["keepouts"]["COND2"]                                 # the band where the I/O pads are
    assert d["rules"]["conductor"]["windowEnclosureUm"] == 0


def test_keeping_the_existing_route_adds_only_what_is_open(tmp_path):
    try:
        H.router_command()
    except LayoutError as e:
        pytest.skip(str(e))
    s = _board()
    st = s.hybrid_route(layers=1, grid=100, remove_existing=True, out_dir=str(tmp_path),
                        timeout=180, shorten=False, dejog=False)
    assert st["stats"]["routed"] == st["stats"]["connections"] and not s.find_shorts()
    tracks = [sh for sh in s.cell.shapes if isinstance(sh, Path) and sh.layer == 3]
    st2 = s.autoroute(layers=1, grid=100, remove_existing=False, out_dir=str(tmp_path),
                      timeout=180, shorten=False, dejog=False)
    assert st2["stats"]["connections"] == 0 and st2["removed_tracks"] == 0
    assert all(t in s.cell.shapes for t in tracks) and not s.find_shorts()
