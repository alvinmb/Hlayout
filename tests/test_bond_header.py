# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples"))

from hlayout.core.shapes import Path, Rectangle
from hlayout.gui.commands import CommandInterpreter
from hlayout.io.techfile import load_bond_rules, save_bond_rules, save_route_rules, \
    load_route_rules
from hlayout.scripting import Session
from hlayout.scripting import iopads


def _design():
    import generate_kovar_dip_packages as K
    lib = K.build([20])
    top = lib.new_cell("TOP")
    lib.top_cell_name = "TOP"
    lib.layers.set_style(57, name="substrate", fill=True, visible=True)
    lib.layers.set_style(60, name="Header_wire_Bond", fill=True, visible=True)
    top.add_rectangle(57, 3000, 4500, 26400, 23500)               # inside the cavity
    top.add_sref("KOVAR_DIP20", (0, 0))
    rr = load_route_rules(lib)
    rr["layer_a"], rr["layer_b"], rr["boundary_layer"] = 3, 5, 57
    save_route_rules(lib, rr)
    br = load_bond_rules(lib)
    br.update(layer=59, max_length=5000)
    save_bond_rules(lib, br)
    s = Session(lib)
    s.use_cell("TOP")
    return s


def test_bond_header_draws_a_pad_and_bond_per_pin_at_the_nearest_edge():
    s = _design()
    s.set_net_names({"IO.1": "A", "IO.15": "B"})
    hp = iopads.header_pins(s, s.cell)
    assert sorted(int(p) for p in hp) == list(range(1, 21))
    r = s.bond_header()
    assert sorted(r["pins"], key=int) == ["1", "15"] and r["layer"] == 60
    pads = {sh.pin: sh.bbox() for sh in s.cell.shapes
            if isinstance(sh, Rectangle) and sh.layer == 3 and sh.pin}
    assert set(pads) == {"1", "15"}
    x1, y1 = hp["1"]
    b = pads["1"]                                       # pin 1: bottom row -> bottom edge
    assert b[1] == 4500 + 250 and b[3] == 4500 + 250 + 600 and b[2] - b[0] == 300
    assert abs((b[0] + b[2]) / 2 - x1) <= 1
    bonds = [sh for sh in s.cell.shapes if isinstance(sh, Path) and sh.layer == 60]
    assert len(bonds) == 2
    ends = {tuple(map(round, sh.points[0])): tuple(sh.points[1]) for sh in bonds}
    assert ends[tuple(map(round, hp["1"]))] == (round(x1), 4500 + 250 + 150)
    # top row pin lands on the top edge
    b15 = pads["15"]
    assert b15[3] == 23500 - 250
    # again: replaced, not doubled
    s.bond_header()
    assert len([sh for sh in s.cell.shapes if isinstance(sh, Path) and sh.layer == 60]) == 2
    # and Assign I/O Pads now sees the sites
    p = iopads.plan(s)
    assert {st["pin"] for st in p["sites"]} == {"1", "15"}


def test_iopads_bond_command_all_pins():
    s = _design()
    out = CommandInterpreter(s, console=None).run("IOPADS BOND all=1")
    assert "20 pads" in out, out
