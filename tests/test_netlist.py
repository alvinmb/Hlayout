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
    assert f"exported to {path}" in out2
    assert os.path.exists(path)


def test_netlist_run_command_requires_a_layer():
    s = _fresh_session()
    interp = CommandInterpreter(s, console=None)
    out = interp.run("NETLIST RUN")
    assert out.startswith("error:")
