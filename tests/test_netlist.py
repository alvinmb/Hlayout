import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

shapely = pytest.importorskip("shapely")

from hlayout.core.library import Library
from hlayout.core.netlist import Net, NetPin, extract_netlist
from hlayout.gui.commands import CommandInterpreter
from hlayout.scripting import Session


def _fresh_session(name="NETTEST"):
    s = Session.new(name)
    s.new_cell("TOP")
    s.clear_undo_history()
    return s


def _two_resistors_bridged(s):
    """Two resistors (R1, R2), each an auto-numbered 2-pin device on the
    default Resistor Geometry Rules conductor layer (31), with R1's pin
    2 bridged to R2's pin 1 by a trace on that same layer -- leaving
    R1.1 and R2.2 as their own isolated singleton nets."""
    s.generate_resistor(0, 0, body_width=1000, body_length=2000, ref_des="R1")
    s.generate_resistor(5000, 0, body_width=1000, body_length=2000, ref_des="R2")
    s.use_cell("TOP")
    s.rect(31, 1300, -300, 3700, 300)


def _nets_by_pins(nets):
    return {net.id: sorted((p.ref_des, p.pin) for p in net.pins) for net in nets}


# --- core hlayout.core.netlist.extract_netlist() ----------------------------

def test_extract_netlist_groups_bridged_pads_into_one_net():
    s = _fresh_session()
    _two_resistors_bridged(s)

    nets = extract_netlist(s.library, s.cell, [31])
    by_pins = _nets_by_pins(nets)
    net_ids = list(by_pins.values())
    assert [("R1", "2"), ("R2", "1")] in net_ids
    assert [("R1", "1")] in net_ids
    assert [("R2", "2")] in net_ids
    assert len(nets) == 3
    assert all(n.unresolved_count == 0 for n in nets)


def test_extract_netlist_does_not_cross_layers_that_merely_coincide_in_xy():
    # Regression: shapely intersection is purely 2D and knows nothing
    # about GDSII layers, so a pin on a layer NOT passed to
    # extract_netlist() must never be netted just because it happens to
    # sit at the same XY coordinates as copper on a requested layer.
    s = _fresh_session()
    _two_resistors_bridged(s)

    nets = extract_netlist(s.library, s.cell, [0])  # wrong layer -- pads are on 31
    assert len(nets) == 1
    assert nets[0].id == "UNASSIGNED"
    assert sorted((p.ref_des, p.pin) for p in nets[0].pins) == [
        ("R1", "1"), ("R1", "2"), ("R2", "1"), ("R2", "2"),
    ]


def test_extract_netlist_marks_unlabeled_instances_as_unresolved():
    s = _fresh_session()
    s.generate_resistor(0, 0, body_width=1000, body_length=2000)  # no ref_des
    nets = extract_netlist(s.library, s.cell, [31])
    assert len(nets) == 2  # the two pads are isolated (no bridging trace)
    for net in nets:
        assert net.pins[0].ref_des == ""
        assert net.unresolved_count == 1


def test_extract_netlist_uses_the_immediate_parent_srefs_label():
    # A resistor placed inside a placed sub-assembly cell: the pin's
    # ref designator must come from the resistor's OWN Sref (the
    # immediate parent), not the outer assembly Sref that placed the
    # sub-assembly cell containing it.
    s = _fresh_session()
    s.new_cell("ASSY")
    s.generate_resistor(0, 0, body_width=1000, body_length=2000, ref_des="R1")
    s.use_cell("TOP")
    s.sref("ASSY", (10000, 10000), label="ASSY1")

    nets = extract_netlist(s.library, s.cell, [31])
    ref_des_values = {p.ref_des for net in nets for p in net.pins}
    assert ref_des_values == {"R1"}
    assert "ASSY1" not in ref_des_values


def _two_vias_bridged(s, pin1="1", pin2="2"):
    """A round via-cell template (the shape a Via Wizard-built round
    lead actually is -- see SRef.pin's own docstring) placed twice
    inside a labelled part-cell "U1DEV", each instance given its own
    SRef.pin (the template's own circle carries no pin of its own,
    since both placements share it), bridged by a trace on the same
    conductor layer -- mirrors _two_resistors_bridged() above but for
    SRef-based pins instead of Rectangle-based ones."""
    s.new_cell("VIA")
    s.use_cell("VIA")
    s.circle(31, (0, 0), 300, segments=16)
    s.new_cell("U1DEV")
    s.use_cell("U1DEV")
    ref1 = s.sref("VIA", (0, 0), pin=pin1)
    ref2 = s.sref("VIA", (3000, 0), pin=pin2)
    s.rect(31, -300, -150, 3300, 150)
    s.use_cell("TOP")
    s.sref("U1DEV", (0, 0), label="U1")
    return ref1, ref2


def test_extract_netlist_recognizes_an_srefs_own_pin():
    s = _fresh_session()
    _two_vias_bridged(s)

    nets = extract_netlist(s.library, s.cell, [31])
    by_pins = _nets_by_pins(nets)
    assert [("U1", "1"), ("U1", "2")] in list(by_pins.values())
    assert all(n.unresolved_count == 0 for n in nets)


def test_extract_netlist_keeps_two_unbridged_via_instances_as_separate_nets():
    s = _fresh_session()
    s.new_cell("VIA")
    s.use_cell("VIA")
    s.circle(31, (0, 0), 300, segments=16)
    s.new_cell("U1DEV")
    s.use_cell("U1DEV")
    s.sref("VIA", (0, 0), pin="1")
    s.sref("VIA", (30000, 0), pin="2")   # far apart -- no bridging trace
    s.use_cell("TOP")
    s.sref("U1DEV", (0, 0), label="U1")

    nets = extract_netlist(s.library, s.cell, [31])
    by_pins = _nets_by_pins(nets)
    net_ids = list(by_pins.values())
    assert [("U1", "1")] in net_ids
    assert [("U1", "2")] in net_ids
    assert len(nets) == 2


def test_extract_netlist_with_no_conductor_geometry_returns_no_nets():
    s = _fresh_session()
    nets = extract_netlist(s.library, s.cell, [31])
    assert nets == []


# --- Session.extract_netlist() / export_netlist_csv() -----------------------

def test_session_extract_netlist_resolves_layer_names():
    s = _fresh_session()
    s.library.layers.set_style(31, name="CONDUCTOR")
    _two_resistors_bridged(s)
    nets = s.extract_netlist(["CONDUCTOR"])
    assert len(nets) == 3


def test_session_export_netlist_csv_writes_one_row_per_pin(tmp_path):
    s = _fresh_session()
    _two_resistors_bridged(s)
    nets = s.extract_netlist([31])
    path = str(tmp_path / "netlist.csv")
    s.export_netlist_csv(nets, path)

    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0].keys() == {"net", "ref_des", "pin"}
    net_of_r1_pin2 = [r["net"] for r in rows if r["ref_des"] == "R1" and r["pin"] == "2"][0]
    net_of_r2_pin1 = [r["net"] for r in rows if r["ref_des"] == "R2" and r["pin"] == "1"][0]
    assert net_of_r1_pin2 == net_of_r2_pin1


def test_export_netlist_csv_keeps_a_row_for_a_net_with_no_pins(tmp_path):
    s = _fresh_session()
    s.rect(31, 0, 0, 1000, 1000)  # ordinary unpinned copper, its own net
    nets = s.extract_netlist([31])
    assert len(nets) == 1
    assert nets[0].pins == []

    path = str(tmp_path / "netlist.csv")
    s.export_netlist_csv(nets, path)
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["ref_des"] == "" and rows[0]["pin"] == ""


# --- NETLIST RUN command bar -------------------------------------------------

def test_netlist_run_command_reports_nets_and_exports(tmp_path):
    s = _fresh_session()
    _two_resistors_bridged(s)
    interp = CommandInterpreter(s, console=None)

    out = interp.run("NETLIST RUN 31")
    assert "3 net(s)" in out
    assert "R1.2, R2.1" in out

    path = str(tmp_path / "cli_netlist.csv")
    out2 = interp.run(f"NETLIST RUN 31 export={path}")
    assert f"exported (csv) to {path}" in out2
    assert os.path.exists(path)


def test_netlist_run_command_requires_a_layer():
    s = _fresh_session()
    interp = CommandInterpreter(s, console=None)
    out = interp.run("NETLIST RUN")
    assert out.startswith("error:")


# --- wire bonds / die pads ---------------------------------------------------

def _die_with_two_bonds(s, die_pins=("1", "2")):
    """A die (cell DIE, pads on layer 58, pin-numbered) sitting on a
    layer-31 die-attach pad, each die pad bonded (layer 59) out to its
    own layer-31 substrate pad. The bond wires fly over the attach pad."""
    from hlayout.io.techfile import load_bond_rules, save_bond_rules
    rules = load_bond_rules(s.library)
    rules.update(layer=59, die_pad_layers=[58], width=25, pad=60)
    save_bond_rules(s.library, rules)
    s.new_cell("DIE")
    s.use_cell("DIE")
    s.rect(58, -500, -500, 500, 500)                          # die outline on pad layer
    s.cell.shapes[-1].pin = ""
    s.rect(58, -400, -50, -300, 50); s.cell.shapes[-1].pin = die_pins[0]
    s.rect(58, 300, -50, 400, 50);   s.cell.shapes[-1].pin = die_pins[1]
    s.use_cell("TOP")
    s.rect(31, -800, -800, 800, 800)                          # die-attach pad
    s.sref("DIE", (0, 0), label="U1")
    s.rect(31, -3000, -200, -2600, 200)                       # left landing
    s.rect(31, 2600, -200, 3000, 200)                         # right landing
    s.wire_bond([(-350, 0), (-2800, 0)], min_length=0, max_length=10000)
    s.wire_bond([(350, 0), (2800, 0)], min_length=0, max_length=10000)


def test_bonds_on_one_die_are_not_shorted_through_the_die_attach_pad():
    s = _fresh_session()
    _die_with_two_bonds(s)
    nets = s.extract_netlist([31])
    pins = [sorted(p.label() for p in n.pins) for n in nets]
    assert ["U1.1"] in pins and ["U1.2"] in pins
    bonded = [n for n in nets if n.bonds]
    assert len(bonded) == 2 and all(n.bonds == 1 for n in bonded)
    assert not any(n.open_bond_ends for n in nets)


def test_bond_wire_does_not_short_to_a_trace_it_flies_over():
    s = _fresh_session()
    _die_with_two_bonds(s)
    s.rect(31, -1800, -2000, -1600, 2000)        # trace under the left bond's span
    nets = s.extract_netlist([31])
    left = next(n for n in nets if any(p.label() == "U1.1" for p in n.pins))
    assert left.bonds == 1
    assert len([n for n in nets if not n.pins and not n.bonds]) >= 1   # trace stays alone


def test_unnumbered_bonded_die_pad_is_reported_and_off_pad_bond_is_open():
    s = _fresh_session()
    _die_with_two_bonds(s, die_pins=("", ""))
    s.wire_bond([(0, 300), (0, 2500)], min_length=0, max_length=10000)   # lands on outline only
    nets = s.extract_netlist([31])
    labels = [p.label() for n in nets for p in n.pins]
    assert sum(1 for l in labels if l.startswith("U1.@")) == 2
    assert any((0, 300) in n.open_bond_ends for n in nets)


def test_include_bonds_false_ignores_bonds():
    s = _fresh_session()
    _die_with_two_bonds(s)
    nets = s.extract_netlist([31], include_bonds=False)
    assert not any(n.bonds for n in nets)


def test_bond_rules_isolated_pad_layers_round_trip_and_legacy_form():
    import json
    from hlayout.io.techfile import BOND_RULES_KEY, load_bond_rules, save_bond_rules
    s = _fresh_session()
    save_bond_rules(s.library, {"layer": 59, "die_pad_layers": [58, 49, 58]})
    assert load_bond_rules(s.library)["die_pad_layers"] == [49, 58]
    s.library.tech_params[BOND_RULES_KEY] = json.dumps({"layer": 59, "die_pad_layer": 58})
    assert load_bond_rules(s.library)["die_pad_layers"] == [58]


def test_header_pin_on_isolated_layer_joins_only_through_its_bond():
    s = _fresh_session()
    _die_with_two_bonds(s)
    from hlayout.io.techfile import load_bond_rules, save_bond_rules
    r = load_bond_rules(s.library); r["die_pad_layers"] = [58, 49]; save_bond_rules(s.library, r)
    s.new_cell("HPIN"); s.use_cell("HPIN"); s.circle(49, (0, 0), 400, segments=16)
    s.new_cell("HDR"); s.use_cell("HDR")
    s.sref("HPIN", (0, 0), pin="1")
    s.sref("HPIN", (2540, 0), pin="2")
    s.use_cell("TOP")
    s.sref("HDR", (-2800, -3000), label="J1")
    s.rect(31, -4000, -3200, 1000, -2800)       # trace under both header pins
    s.wire_bond([(-2800, -200), (-2800, -3000)], min_length=0, max_length=10000)
    nets = s.extract_netlist([31])
    pins = [sorted(p.label() for p in n.pins) for n in nets]
    assert ["J1.1", "U1.1"] in pins
    assert ["J1.2"] in pins                      # not shorted via the trace under it


def test_netlist_settings_persist_and_drive_default_extraction():
    from hlayout.io.techfile import load_netlist_settings, save_netlist_settings
    s = _fresh_session()
    assert load_netlist_settings(s.library)["saved"] is False
    save_netlist_settings(s.library, {"conductor_layers": [31, 31, 5], "include_bonds": False,
                                      "used_only": False})
    st = load_netlist_settings(s.library)
    assert st["saved"] and st["conductor_layers"] == [5, 31]
    assert st["include_bonds"] is False and st["used_only"] is False
    _two_resistors_bridged(s)
    assert len(s.extract_netlist()) == 3          # uses the saved layers


def test_netlist_run_command_uses_saved_layers():
    from hlayout.io.techfile import save_netlist_settings
    s = _fresh_session()
    _two_resistors_bridged(s)
    save_netlist_settings(s.library, {"conductor_layers": [31]})
    out = CommandInterpreter(s, console=None).run("NETLIST RUN")
    assert "3 net(s)" in out


def test_pins_and_nets_carry_locations_for_zooming():
    s = _fresh_session()
    _die_with_two_bonds(s, die_pins=("", ""))
    s.rect(31, 5000, 5000, 5400, 5400)              # isolated copper, no pins
    nets = s.extract_netlist([31])
    bonded = [p for n in nets for p in n.pins if p.pin.startswith("@")]
    assert sorted(p.xy for p in bonded) == [(-350, 0), (350, 0)]
    lonely = [n for n in nets if not n.pins and not n.bonds and n.location
              and 5000 <= n.location[0] <= 5400]
    assert len(lonely) == 1
    assert all(p.xy is not None for n in nets for p in n.pins)



def test_export_netlist_all_formats(tmp_path):
    s = _fresh_session()
    _two_resistors_bridged(s)
    nets = s.extract_netlist([31])
    got = {}
    for ext in (".csv", ".txt", ".net", ".tng"):
        p = tmp_path / ("n" + ext)
        got[ext] = (s.export_netlist(nets, str(p)), p.read_text())
    assert got[".csv"][0] == "csv" and got[".csv"][1].splitlines()[0] == "net,ref_des,pin"
    assert got[".txt"][0] == "report" and "R1.2, R2.1" in got[".txt"][1] and "Issues: 0" in got[".txt"][1]
    kicad = got[".net"][1]
    assert got[".net"][0] == "kicad" and kicad.startswith("(export (version D)")
    assert '(comp (ref "R1")' in kicad and '(node (ref "R2") (pin "1"))' in kicad
    assert kicad.count("(") == kicad.count(")")
    tango = got[".tng"][1]
    assert got[".tng"][0] == "tango" and "R1-2" in tango and "[" in tango


def test_export_netlist_format_override_and_bad_format(tmp_path):
    s = _fresh_session()
    _two_resistors_bridged(s)
    nets = s.extract_netlist([31])
    p = tmp_path / "x.dat"
    assert s.export_netlist(nets, str(p), fmt="kicad") == "kicad"
    with pytest.raises(ValueError):
        s.export_netlist(nets, str(p), fmt="spice")


def test_netlist_run_command_export_picks_format_from_extension(tmp_path):
    s = _fresh_session()
    _two_resistors_bridged(s)
    out = CommandInterpreter(s, console=None).run(f"NETLIST RUN 31 export={tmp_path / 'b.net'}")
    assert "exported (kicad)" in out
    assert (tmp_path / "b.net").read_text().startswith("(export")
