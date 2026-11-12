# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.library import Library
from hlayout.scripting.autoplace_session import match_footprint


def test_a_part_with_no_footprint_maps_by_its_ref():
    lib = Library("t")
    lib.new_cell("OP249")
    lib.new_cell("CAP_0805")
    fmap = {"ref:U1": "OP249", "Capacitor_SMD:C_0805": "CAP_0805"}
    assert match_footprint(lib, "", fmap, ref="U1") == "OP249"
    assert match_footprint(lib, "", fmap, ref="U2") is None
    assert match_footprint(lib, "Capacitor_SMD:C_0805", fmap, ref="C1") == "CAP_0805"
    # a ref mapping wins over the footprint name; a missing cell is ignored
    assert match_footprint(lib, "Capacitor_SMD:C_0805", {"ref:C1": "OP249"}, ref="C1") == "OP249"
    assert match_footprint(lib, "", {"ref:U1": "GONE"}, ref="U1") is None


def test_cells_from_an_attached_library_are_offered_and_copied_in():
    from hlayout.scripting import Session
    from hlayout.scripting.autoplace_session import _Cells, library_cells, use_cell
    design = Library("d")
    design.new_cell("TOP")
    att = Library("chip_wire")
    pin = att.new_cell("chip_pin")
    pin.add_rectangle(58, -50, -50, 50, 50)
    die = att.new_cell("OP249")
    die.add_rectangle(20, -900, -1400, 900, 1400)
    die.add_sref("chip_pin", (-700, 1000), pin="1")
    s = Session(design)
    s.attached_libraries.append(att)
    assert "OP249" in library_cells(s) and "OP249" not in design.cells
    assert match_footprint(_Cells(s), "", {"ref:U1": "OP249"}, ref="U1") == "OP249"
    assert use_cell(s, "OP249") == "OP249"
    assert "OP249" in design.cells and "chip_pin" in design.cells   # with what it places


def test_the_cell_map_survives_undo_and_is_kept_next_to_the_netlist(tmp_path):
    from hlayout.scripting import Session
    from hlayout.scripting.autoplace_session import (cellmap_path, load_cellmap,
                                                     merged_footprint_map, save_cellmap)
    s = Session.new("MAP")
    s.new_cell("TOP")
    s.set_autoplace_settings(footprint_map={"ref:C1": "CAP_0805"})
    s.rect(1, 0, 0, 100, 100)
    s.undo()                                     # an edit undone: the map stays
    assert s.autoplace_settings()["footprint_map"] == {"ref:C1": "CAP_0805"}
    net = tmp_path / "a.net"
    net.write_text("")
    assert save_cellmap(str(net), {"ref:C1": "CAP_0805", "ref:U1": "OP249"}) == cellmap_path(str(net))
    assert load_cellmap(str(net))["ref:U1"] == "OP249"
    s2 = Session.new("FRESH")                    # a design that never saw the map
    assert merged_footprint_map(s2, str(net)) == {"ref:C1": "CAP_0805", "ref:U1": "OP249"}
    assert s2.autoplace_settings()["footprint_map"]["ref:U1"] == "OP249"


def test_the_header_package_is_not_placed_on_the_substrate():
    from hlayout.scripting.autoplace_session import _is_header

    class C:
        value, footprint = "Conn_01x30_Socket", ""
    assert _is_header("J30", C, "30_PIN_pkg")
    C.value = "10K"
    assert not _is_header("R1", C, "R1") and not _is_header("U1", C, "OP249")


def test_io_pads_inside_a_placed_substrate_cell_count():
    from hlayout.io.techfile import load_route_rules, save_route_rules
    from hlayout.scripting import Session
    s = Session.new("SUBPADS")
    rr = load_route_rules(s.library); rr.update(layer_a=3, layer_b=5, boundary_layer=57)
    save_route_rules(s.library, rr)
    s.new_cell("PART")
    s.rect(3, -100, -100, 100, 100, pin="1")
    s.new_cell("SUB")
    s.rect(57, 0, 0, 20000, 10000)
    s.rect(3, 1000, 250, 1300, 850, pin="1")
    s.new_cell("PKG")                                  # header pins: not I/O pads
    s.rect(5, 1000, -3000, 1300, -2700, pin="1")
    s.new_cell("TOP")
    s.sref("SUB", (5000, 0))
    s.sref("PKG", (5000, 0))
    s.sref("PART", (15000, 5000)).label = "U1"
    s.set_net_names({"IO.1": "A", "U1.1": "A"})
    d = s.sli()
    assert len(d["nets"]) == 1
    seg = d["nets"][0]["segments"][0]
    assert 6000 < min(seg[0], seg[2]) < 6400 and max(seg[0], seg[2]) > 14000
    assert s._io_ring_box(s.cell) is None            # one pad: no ring, the header ignored
