# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core.errors import LayoutError

pytest.importorskip("shapely")

from hlayout.core.autoplace import PNet, Part, Placer, mst, rot_pt
from hlayout.io.kicad_netlist import parse_sexpr, read_kicad_netlist
from hlayout.scripting import Session

KICAD = """(export (version "E")
 (components
  (comp (ref "U1") (value "LM741") (footprint "Pkg:SO-8")
    (fields (field (name "HL_Family") "die") (field (name "HL_Power_mW") "120")))
  (comp (ref "C1") (value "100n") (footprint "Cap:C_0805")
    (property (name "HL_Weight") (value "2")))
  (comp (ref "R1") (value "4.7k")))
 (nets
  (net (code "1") (name "GND") (class "Power")
   (node (ref "U1") (pin "4") (pinfunction "V-") (pintype "power_in"))
   (node (ref "C1") (pin "2") (pintype "passive")))
  (net (code "2") (name "OUT")
   (node (ref "U1") (pin "6") (pintype "output"))
   (node (ref "R1") (pin "1") (pintype "passive")))))
"""


def test_kicad_netlist_reads_parts_fields_nets_and_classes():
    nl = read_kicad_netlist(KICAD)
    assert set(nl.components) == {"U1", "C1", "R1"}
    assert nl.components["U1"].footprint == "Pkg:SO-8"
    assert nl.components["U1"].fields == {"HL_Family": "die", "HL_Power_mW": "120"}
    assert nl.components["C1"].fields["HL_Weight"] == "2"            # KiCad 7+ (property ...)
    gnd = nl.nets[0]
    assert (gnd.name, gnd.cls) == ("GND", "Power")
    assert [(n.ref, n.pin, n.pintype) for n in gnd.nodes] == [("U1", "4", "power_in"), ("C1", "2", "passive")]
    assert nl.pad_nets() == {"U1.4": "GND", "C1.2": "GND", "U1.6": "OUT", "R1.1": "OUT"}


def test_sexpr_handles_quotes_and_nesting():
    assert parse_sexpr('(a "b c" (d "e(f)" g))') == [["a", "b c", ["d", "e(f)", "g"]]]


def test_mst_and_rotation():
    assert mst([(0, 0), (3, 4)])[0] == 5.0
    length, edges = mst([(0, 0), (10, 0), (10, 10), (0, 10)])
    assert length == 30.0 and len(edges) == 3
    assert rot_pt(1, 0, 90) == (0, 1) and rot_pt(1, 0, 180) == (-1, 0) and rot_pt(1, 0, 270) == (0, -1)


def _two_parts(fixed_b=False):
    a = Part("A", "X", "smd", (-500, -500, 500, 500), {"1": (500, 0)})
    b = Part("B", "X", "smd", (-500, -500, 500, 500), {"1": (-500, 0)},
             x=8000, y=0, placed=fixed_b, fixed=fixed_b)
    return [a, b]


def test_placer_pulls_connected_parts_together_without_overlap():
    parts = _two_parts()
    net = PNet("N", 1.0, members=[(0, "1"), (1, "1")], anchors=[(-9000, 0)])
    pl = Placer(parts, [net], region=(-10000, -10000, 10000, 10000), grid=50, seed=3)
    pl.run(effort=0.5)
    bad, out = pl.overlaps()
    assert not bad and not out
    assert pl.wirelength() < 4000                     # anchor -> A -> B, all close
    for p in parts:
        assert p.x % 50 == 0 and p.y % 50 == 0        # on the grid (region origin on it)


def test_fixed_parts_never_move_and_thermal_pushes_hot_parts_apart():
    parts = _two_parts(fixed_b=True)
    pl = Placer(parts, [PNet("N", 1.0, members=[(0, "1"), (1, "1")])],
                region=(-10000, -10000, 10000, 10000), seed=1)
    pl.run(effort=0.5)
    assert (parts[1].x, parts[1].y) == (8000, 0)
    near = abs(parts[0].x - 8000) + abs(parts[0].y)
    hot = _two_parts(fixed_b=True)
    for p in hot:
        p.power = 500.0
    pl2 = Placer(hot, [PNet("N", 1.0, members=[(0, "1"), (1, "1")])],
                 region=(-10000, -10000, 10000, 10000), seed=1, thermal=5.0)
    pl2.run(effort=0.5)
    assert abs(hot[0].x - 8000) + abs(hot[0].y) > near


def _design():
    """A 30 x 30 mm substrate, two I/O pads with net names, and two 2-pin
    part cells; the netlist ties IN -> U1 -> U2 -> OUT."""
    from hlayout.io.techfile import load_route_rules, save_route_rules
    s = Session.new("AP")
    s.new_cell("PART")
    s.rect(61, -1000, -600, 1000, 600)
    s.cell.add_rectangle(5, -1000, -300, -600, 300).pin = "1"
    s.cell.add_rectangle(5, 600, -300, 1000, 300).pin = "2"
    s.new_cell("TOP")
    s.rect(57, -15000, -15000, 15000, 15000)
    rr = load_route_rules(s.library)
    rr.update(layer_a=3, layer_b=5, boundary_layer=57)
    save_route_rules(s.library, rr)
    s.cell.add_rectangle(3, -14500, -500, -13500, 500).pin = "1"
    s.cell.add_rectangle(3, 13500, -500, 14500, 500).pin = "2"
    s.set_net_names({"IO.1": "IN", "IO.2": "OUT"})
    return s


NET = """(export (version "E")
 (components (comp (ref "U1") (value "x") (footprint "Lib:PART"))
             (comp (ref "U2") (value "x") (footprint "Lib:PART")))
 (nets (net (code "1") (name "IN") (node (ref "U1") (pin "1")))
       (net (code "2") (name "MID") (node (ref "U1") (pin "2")) (node (ref "U2") (pin "1")))
       (net (code "3") (name "OUT") (node (ref "U2") (pin "2")))))
"""


def test_auto_place_on_a_design_places_names_and_is_one_undo_step(tmp_path):
    s = _design()
    p = tmp_path / "a.net"
    p.write_text(NET)
    s.clear_undo_history()
    r = s.auto_place(str(p), effort=0.5, seed=2)
    assert r["placed"] == 2 and not r["overlaps"] and not r["outside"]
    refs = {x.label: x for x in s.library.cells["TOP"].srefs}
    assert set(refs) == {"U1", "U2"} and all(x.cell_name == "PART" for x in refs.values())
    d = s.sli()
    assert {n["net"] for n in d["nets"]} == {"IN", "MID", "OUT"}
    assert d["total"] < 32000                       # the straight run is 27 mm of pads
    assert refs["U1"].origin[0] < refs["U2"].origin[0]   # IN side first
    s.undo()
    assert not [x for x in s.library.cells["TOP"].srefs if x.label]


def test_improve_never_makes_it_worse(tmp_path):
    s = _design()
    p = tmp_path / "a.net"
    p.write_text(NET)
    s.auto_place(str(p), effort=0.3, seed=5)
    before = s.sli()["total"]
    s.auto_place(str(p), improve=True, effort=0.5, seed=6)
    assert s.sli()["total"] <= before + 1


ALU = os.path.join(os.path.dirname(__file__), "..", "example2", "alu16_MCM.gds")


@pytest.mark.skipif(not os.path.exists(ALU), reason="example 2 not present")
def test_alu16_scrambled_places_within_ten_percent_of_hand_placement():
    from hlayout.io.sidecar import load_design
    lib = load_design(ALU).library
    s = Session(lib)
    s.use_cell("TOP")
    top = lib.cells["TOP"]
    top.shapes[:] = [sh for sh in top.shapes
                     if not (sh.layer == 4 or (sh.layer in (3, 5) and not getattr(sh, "pin", None)))]
    top.srefs[:] = [r for r in top.srefs if not r.cell_name.startswith("VIA_")]
    ex = os.path.dirname(ALU)
    s.import_net_names(os.path.join(ex, "alu16_net_names.csv"))
    hand = s.sli()["total"]
    top.srefs[:] = [r for r in top.srefs if not r.cell_name.startswith("LCC_")]
    r = s.auto_place(os.path.join(ex, "alu16_netlist", "alu16.net"), seed=1, thermal=0)
    assert r["placed"] == 10 and not r["overlaps"] and not r["outside"]
    assert s.sli()["total"] < 1.10 * hand


def test_auto_place_window_runs_and_shows_slis(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.geometry("1x1+-3000+-3000")
    from hlayout.gui.autoplace_window import AutoPlaceWindow

    class Canvas:
        selected, selected_multi, sli_show_power, cell_name = None, [], True, "TOP"

        def redraw(self):
            pass

    class Status:
        text = ""

        def set(self, t):
            self.text = t

    class App:
        def __init__(self, s):
            self.session, self.canvas, self.status, self.sli = s, Canvas(), Status(), None

        def set_sli(self, on):
            self.sli = on

        def refresh_all(self):
            pass

    s = _design()
    p = tmp_path / "a.net"
    p.write_text(NET)
    app = App(s)
    win = AutoPlaceWindow(root, app)
    win.v["netlist"].set(str(p))
    win._load_netlist()
    assert set(win.parts.get_children()) == {"U1", "U2"}
    assert win.parts.set("U1", "cell") == "PART"
    assert set(win.nets.get_children()) == {"IN", "MID", "OUT"}
    win.v["effort"].set("0.3")
    win.run("run")
    for _ in range(200):
        root.update()
        if not win._running:
            break
        root.after(50)
    root.update()
    assert not win._running
    assert win.banner.cget("text").startswith("PLACED")
    assert app.sli is True
    assert {x.label for x in s.library.cells["TOP"].srefs} == {"U1", "U2"}
    win.destroy()
    root.destroy()


def test_printed_resistor_is_generated_from_its_value(tmp_path):
    s = _design()
    net = NET.replace('(comp (ref "U2") (value "x") (footprint "Lib:PART")))',
                      '(comp (ref "U2") (value "x") (footprint "Lib:PART")) (comp (ref "R1") (value "4.7k")))')
    net = net.replace('(node (ref "U2") (pin "2")))))', '(node (ref "U2") (pin "2")) (node (ref "R1") (pin "1")))))')
    p = tmp_path / "r.net"
    p.write_text(net)
    # the flow: read the netlist, design the resistor, then place
    from hlayout.io.techfile import load_resistor_list, save_resistor_list
    from hlayout.scripting.netlist_flow import designed_solution
    s.read_netlist(str(p))
    with pytest.raises(LayoutError, match="design the printed resistors first: R1"):
        s.auto_place(str(p), effort=0.3, seed=1)
    rows = load_resistor_list(s.library)
    from hlayout.gui.resistor_designer import DesignRules, Resistor, width_for_power
    res = Resistor(ref_des="R1", value=rows[0]["value"], tolerance=rows[0]["tolerance"],
                   power_mw=rows[0]["power_mw"], body_width=width_for_power(rows[0]["power_mw"]))
    res.compute_solutions(DesignRules.from_library(s.library))
    rows[0]["selected_ink"] = res.solutions[0].ink_rsh
    save_resistor_list(s.library, rows)
    assert designed_solution(s.library, "R1") is not None
    r = s.auto_place(str(p), effort=0.3, seed=1)
    assert r["generated"] == ["R1"]
    refs = {x.label: x for x in s.library.cells["TOP"].srefs if x.label}
    assert "R1" in refs and not r["overlaps"]


def test_resistor_conductor_comes_from_the_tech_file(tmp_path):
    from hlayout.io.techfile import load_resistor_geometry_rules, save_resistor_geometry_rules
    from hlayout.scripting.autoplace_session import conductor_layers, resistor_conductor
    s = _design()
    rg = load_resistor_geometry_rules(s.library)
    rg["conductor_layer"] = 7
    save_resistor_geometry_rules(s.library, rg)
    assert resistor_conductor(s.library) == 7 and 7 in conductor_layers(s.library)
    net = NET.replace('(comp (ref "U2") (value "x") (footprint "Lib:PART")))',
                      '(comp (ref "U2") (value "x") (footprint "Lib:PART")) (comp (ref "R1") (value "4.7k")))')
    net = net.replace('(node (ref "U2") (pin "2")))))', '(node (ref "U2") (pin "2")) (node (ref "R1") (pin "1")))))')
    p = tmp_path / "r.net"
    p.write_text(net)
    s.set_autoplace_settings(auto_size_resistors=True)        # the old shortcut still works
    s.auto_place(str(p), effort=0.3, seed=1)
    rc = next(x.cell_name for x in s.library.cells["TOP"].srefs if x.label == "R1")
    assert {sh.layer for sh in s.library.cells[rc].shapes if getattr(sh, "pin", None)} == {7}
    s.set_autoplace_settings(resistor_layer="5")                # job override, by number or name
    assert resistor_conductor(s.library) == 5
    s.set_autoplace_settings(resistor_layer=None)
    assert resistor_conductor(s.library) == 7


def test_overrides_force_a_cell_and_lock_a_part(tmp_path):
    s = _design()
    s.new_cell("PART2")
    s.rect(61, -1200, -600, 1200, 600)
    s.cell.add_rectangle(5, -1200, -300, -800, 300).pin = "1"
    s.cell.add_rectangle(5, 800, -300, 1200, 300).pin = "2"
    s.use_cell("TOP")
    p = tmp_path / "a.net"
    p.write_text(NET)
    ov = tmp_path / "o.csv"
    ov.write_text("ref,cell,x,y,locked\nU2,PART2,5000,5000,1\n")
    s.auto_place(str(p), overrides=str(ov), effort=0.3)
    refs = {x.label: x for x in s.library.cells["TOP"].srefs if x.label}
    assert refs["U2"].cell_name == "PART2" and tuple(refs["U2"].origin) == (5000, 5000)


def test_sli_starts_at_the_bond_landing_of_a_bonded_die_pad():
    from hlayout.io import techfile as T
    s = Session.new("SLI")
    s.new_cell("DIE")
    s.rect(61, -500, -500, 500, 500)                 # die body
    s.rect(58, 350, -50, 450, 50, pin="1")           # die pad, right edge
    s.new_cell("TOP")
    s.rect(57, -10000, -10000, 10000, 10000)          # substrate
    s.rect(3, 8000, -500, 9000, 500, pin="1")         # I/O pad 1
    s.sref("DIE", (0, 0), label="U1")
    T.save_bond_rules(s.library, {"layer": 59, "width": 25, "pad": 75, "min_length": 300,
                                  "max_length": 5000, "die_pad_layers": [58]})
    s.set_net_names({"U1.1": "N1", "IO.1": "N1"})
    seg = s.sli()["nets"][0]["segments"][0]
    assert {(seg[0], seg[1]), (seg[2], seg[3])} == {(400.0, 0.0), (8500.0, 0.0)}   # from the die pad
    s.wire_bond([(400, 0), (1600, 0)], cell_name="TOP")
    seg = s.sli()["nets"][0]["segments"][0]
    assert {(seg[0], seg[1]), (seg[2], seg[3])} == {(1600.0, 0.0), (8500.0, 0.0)}  # from the landing


def test_sli_leaves_out_connections_the_copper_already_makes():
    from hlayout.io.techfile import load_route_rules, save_route_rules
    s = Session.new("RATS")
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr["layer_a"] = 3; rr["layer_b"] = 5
    save_route_rules(s.library, rr)
    for i, x in enumerate((0, 5000, 10000), start=1):
        s.rect(3, x - 300, -300, x + 300, 300, pin=str(i))
    s.set_net_names({"IO.1": "N", "IO.2": "N", "IO.3": "N"})
    assert len(s.sli()["nets"][0]["segments"]) == 2           # nothing routed: the full tree
    s.path(3, [(0, 0), (5000, 0)], 300)                       # IO.1 - IO.2 routed
    d = s.sli()                                               # copper edge to copper edge
    assert sorted(d["nets"][0]["segments"][0][::2]) == [5300.0, 9700.0]
    assert len(s.sli(unrouted_only=False)["nets"][0]["segments"]) == 2
    s.path(3, [(5000, 0), (10000, 0)], 300)                   # all routed: no SLI left
    d = s.sli()
    assert d["nets"] == [] and d["complete"] == 1


def test_auto_place_replaces_the_placed_parts_old_pad_names():
    from hlayout.io.techfile import load_route_rules, save_route_rules
    s = Session.new("NAMES")
    rr = load_route_rules(s.library)
    rr.update(boundary_layer=57)                              # the substrate is the area
    save_route_rules(s.library, rr)
    s.new_cell("SO4")
    for i, (x, y) in enumerate([(0, 0), (1270, 0), (1270, 4000), (0, 4000)], start=1):
        s.rect(3, x - 200, y - 300, x + 200, y + 300, pin=str(i))
    s.new_cell("TOP")
    s.rect(57, 0, 0, 20000, 20000)
    s.rect(3, 1000, 1000, 1600, 1600, pin="1")
    s.set_net_names({"U1.4": "OLD", "U1.9": "STALE", "IO.1": "A"})
    nl = read_kicad_netlist('(export (version "E") (components (comp (ref "U1") (footprint "X:SO4")))'
                            ' (nets (net (code "1") (name "A") (node (ref "U1") (pin "1")))))')
    s.auto_place(nl)
    from hlayout.io.techfile import load_net_names
    assert load_net_names(s.library) == {"IO.1": "A", "U1.1": "A"}


def test_sli_runs_from_the_end_of_a_partly_routed_trace():
    from hlayout.io.techfile import load_route_rules, save_route_rules
    s = Session.new("RATS2")
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr["layer_a"] = 3; rr["layer_b"] = 5
    save_route_rules(s.library, rr)
    s.rect(3, -300, -300, 300, 300, pin="1")
    s.rect(3, 9700, 4700, 10300, 5300, pin="2")
    s.set_net_names({"IO.1": "N", "IO.2": "N"})
    s.path(3, [(0, 0), (6000, 0), (6000, 3000)], 300)     # half way there
    (seg,) = s.sli()["nets"][0]["segments"]
    ends = {(round(seg[0]), round(seg[1])), (round(seg[2]), round(seg[3]))}
    assert (6000, 3150) in ends or any(abs(x - 6000) <= 150 and abs(y - 3150) <= 150 for x, y in ends)
    assert s.sli()["total"] < 6000                           # not the 11 mm pad-to-pad line


def test_no_substrate_or_too_small_an_area_fails_at_once(tmp_path):
    """Auto Place refuses at once when there is nothing to place inside, or
    the parts can't fit it -- it used to search for room for ever."""
    from hlayout.io.techfile import load_route_rules, save_route_rules
    s = Session.new("AP")
    s.new_cell("PART")
    s.rect(61, -1000, -600, 1000, 600)
    s.cell.add_rectangle(5, -1000, -300, -600, 300).pin = "1"
    s.cell.add_rectangle(5, 600, -300, 1000, 300).pin = "2"
    s.new_cell("TOP")
    rr = load_route_rules(s.library)
    rr.update(layer_a=3, layer_b=5, boundary_layer=57)
    save_route_rules(s.library, rr)
    s.cell.add_rectangle(61, 0, 0, 30000, 2000)                 # something drawn, no substrate
    p = tmp_path / "a.net"
    p.write_text(NET)
    with pytest.raises(LayoutError, match="no substrate outline"):
        s.auto_place(str(p))
    s.rect(57, 0, 0, 3000, 3000)                                 # a substrate far too small
    with pytest.raises(LayoutError, match="placement area is"):
        s.auto_place(str(p))


def test_stop_works_in_the_first_placement_pass():
    from hlayout.core.autoplace import Stopped
    pl = Placer(_two_parts(), [PNet("N", 1.0, members=[(0, "1"), (1, "1")])],
                region=(-10000, -10000, 10000, 10000), grid=50, seed=3)
    with pytest.raises(Stopped):
        pl.run(cancel=lambda: True)


def test_orient_turns_a_part_to_face_its_net():
    a = Part("A", "X", "smd", (-500, -200, 500, 200), {"1": (-500, 0), "2": (500, 0)})
    b = Part("B", "X", "smd", (-200, -200, 200, 200), {"1": (0, 0)},
             x=0, y=5000, placed=True, fixed=True)
    a.x, a.y, a.placed = 0, 0, True
    pl = Placer([a, b], [PNet("N", 1.0, members=[(0, "2"), (1, "1")])],
                region=(-10000, -10000, 10000, 10000), grid=50, seed=1)
    before = pl.wirelength()
    assert pl.orient() >= 1 and a.rot == 90 and pl.wirelength() < before
