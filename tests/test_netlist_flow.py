# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.io.techfile import load_net_names, load_resistor_list, save_resistor_list
from hlayout.scripting import Session
from hlayout.scripting.netlist_flow import resistor_status
from hlayout.io.kicad_netlist import read_netlist

NET = """(export (version "E")
 (components (comp (ref "U1") (value "OP249") (footprint ""))
             (comp (ref "R1") (value "4.7k") (fields (field (name "Tolerance") "1%")))
             (comp (ref "R2") (value "100")))
 (nets (net (code "1") (name "/IN") (node (ref "U1") (pin "3")) (node (ref "R1") (pin "1")))
       (net (code "2") (name "GND") (node (ref "R1") (pin "2")) (node (ref "R2") (pin "2")))))
"""


def test_read_netlist_stores_names_and_queues_the_resistors(tmp_path):
    s = Session.new("F")
    s.new_cell("TOP")
    s.set_net_names({"IO.1": "IN", "U1.9": "OLD"})
    p = tmp_path / "a.net"
    p.write_text(NET)
    r = s.read_netlist(str(p))
    names = load_net_names(s.library)
    assert names["U1.3"] == "IN" and names["R1.2"] == "GND"
    assert names["IO.1"] == "IN" and "U1.9" not in names      # I/O kept, the part's old pins gone
    assert sorted(r["resistors"]) == ["R1", "R2"] and sorted(r["added"]) == ["R1", "R2"]
    rows = {x["ref_des"]: x for x in load_resistor_list(s.library)}
    assert rows["R1"]["value"] == 4700 and rows["R1"]["tolerance"] == "1%"
    assert rows["R1"]["selected_ink"] is None
    st = resistor_status(s, read_netlist(str(p)))
    assert sorted(st["undesigned"]) == ["R1", "R2"]
    # a design survives reading the same netlist again; a changed value doesn't
    rows["R1"]["selected_ink"] = 1000.0
    save_resistor_list(s.library, list(rows.values()))
    r2 = s.read_netlist(str(p))
    assert "R1" in r2["kept"]
    p.write_text(NET.replace('"4.7k"', '"10k"'))
    r3 = s.read_netlist(str(p))
    assert r3["changed"] == ["R1"]
    assert {x["ref_des"]: x for x in load_resistor_list(s.library)}["R1"]["selected_ink"] is None


def test_placed_resistors_come_out_in_one_step():
    from hlayout.scripting import Session
    s = Session.new("RM")
    s.new_cell("TOP")
    for i, ref in enumerate(("R1", "R2")):
        s.generate_resistor(i * 5000, 0, 500, 1000, ref_des=ref, ink_rsh=1000)
    s.rect(3, 0, 5000, 100, 5100)
    assert s.remove_placed_resistors(["R1", "R2"]) == ["R1", "R2"]
    assert not s.cell.srefs and "R1" not in s.library.cells and len(s.cell.shapes) == 1
    s.undo()
    assert sorted(r.label for r in s.cell.srefs) == ["R1", "R2"]
