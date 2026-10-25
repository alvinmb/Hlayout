"""Pick-and-place (centroid) output: hlayout.io.pnpio / Session.export_pick_and_place."""
import csv
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.io.pnpio import CSV_COLUMNS, pick_and_place_rows, write_pick_and_place
from hlayout.scripting import Session

SUB, COND, PAD = 1, 3, 58


def _design():
    """A 20 x 20 mm substrate whose lower-left corner is at (1, 1) mm, and
    a 4-pad part SO4 whose pad pattern is centred at (635, 2000) local,
    with a body that is not centred on the pads."""
    s = Session.new("PNP")
    s.set_layer_style(SUB, name="substrate")
    s.new_cell("SO4")
    for i, (x, y) in enumerate([(0, 0), (1270, 0), (1270, 4000), (0, 4000)], start=1):
        s.rect(COND, x - 200, y - 300, x + 200, y + 300, pin=str(i))
    s.rect(10, -600, -800, 3000, 4800)           # body, off-centre
    s.new_cell("TOP")
    s.rect(SUB, 1000, 1000, 21000, 21000)
    s.clear_undo_history()
    return s


def _rows(s, **kw):
    return {r["designator"]: r for r in s.pick_and_place(**kw)["rows"]}


def test_centroid_is_the_pad_pattern_centre_and_angle_the_placement():
    s = _design()
    s.sref("SO4", (5000, 6000), label="U1")
    s.sref("SO4", (12000, 6000), rotation=90, label="U2")
    r = _rows(s)
    assert (r["U1"]["x_mm"], r["U1"]["y_mm"], r["U1"]["rotation"]) == (4.635, 7.0, 0.0)
    # (635, 2000) turned 90 CCW is (-2000, 635); placed at (12, 6) mm, origin (1, 1) mm
    assert (r["U2"]["x_mm"], r["U2"]["y_mm"], r["U2"]["rotation"]) == (9.0, 5.635, 90.0)
    assert r["U1"]["footprint"] == "SO4" and r["U1"]["kind"] == "smd" and not r["U1"]["mirrored"]


def test_rotation_is_normalised_and_mirroring_flagged():
    s = _design()
    s.sref("SO4", (5000, 5000), rotation=-90, label="U1")
    s.sref("SO4", (9000, 5000), x_reflection=True, label="U2")
    r = _rows(s)
    assert r["U1"]["rotation"] == 270.0
    assert r["U2"]["mirrored"] and r["U2"]["rotation"] == 0.0


def test_parts_inside_an_unlabelled_group_compose_their_angle():
    s = _design()
    s.new_cell("GROUP")
    s.sref("SO4", (0, 0), rotation=90, label="U5")
    s.use_cell("TOP")
    s.sref("GROUP", (10000, 10000), rotation=180)
    r = _rows(s)
    assert list(r) == ["U5"] and r["U5"]["rotation"] == 270.0
    # (635, 2000) -> 90 -> (-2000, 635) -> 180 -> (2000, -635), at (10, 10) mm
    assert (r["U5"]["x_mm"], r["U5"]["y_mm"]) == (11.0, 8.365)


def test_unlabelled_instances_and_the_substrate_are_not_parts():
    s = _design()
    s.new_cell("VIA")
    s.rect(COND, -100, -100, 100, 100)
    s.new_cell("SUBSTRATE_CELL")
    s.rect(SUB, 0, 0, 100, 100)
    s.use_cell("TOP")
    s.sref("VIA", (3000, 3000))
    s.sref("SUBSTRATE_CELL", (0, 0), label="SUB1")
    assert s.pick_and_place()["rows"] == []


def test_origin_options():
    s = _design()
    s.sref("SO4", (5000, 6000), label="U1")
    assert s.pick_and_place()["origin_kind"] == "substrate"
    r = _rows(s, origin="design")["U1"]
    assert (r["x_mm"], r["y_mm"]) == (5.635, 8.0)
    r = _rows(s, origin=(5000, 6000))["U1"]
    assert (r["x_mm"], r["y_mm"]) == (0.635, 2.0)


def test_no_substrate_falls_back_to_the_design_origin():
    s = Session.new("NOSUB")
    s.new_cell("P")
    s.rect(COND, -100, -100, 100, 100, pin="1")
    s.rect(COND, 900, -100, 1100, 100, pin="2")
    s.new_cell("TOP")
    s.sref("P", (2000, 3000), label="C1")
    t = s.pick_and_place()
    assert t["origin_kind"] == "design"
    assert (t["rows"][0]["x_mm"], t["rows"][0]["y_mm"]) == (2.5, 3.0)


def test_bare_die_uses_its_body_centre_and_is_a_die():
    s = _design()
    s.new_cell("chip_pin")
    s.rect(PAD, -50, -50, 50, 50)
    s.new_cell("DIE")
    s.rect(20, 0, 0, 1000, 1000)
    s.sref("chip_pin", (100, 900), pin="1")      # pads all in one corner
    s.sref("chip_pin", (300, 900), pin="2")
    s.use_cell("TOP")
    s.sref("DIE", (8000, 8000), label="U9")
    r = _rows(s)["U9"]
    assert r["kind"] == "die" and (r["x_mm"], r["y_mm"]) == (7.5, 7.5)
    assert "U9" not in _rows(s, include_dies=False)


def test_connector_kind_and_ref_des_order():
    s = _design()
    for i, lab in enumerate(["U10", "J1", "U2", "C3"]):
        s.sref("SO4", (3000 + 3000 * i, 12000), label=lab)
    t = s.pick_and_place()
    assert [r["designator"] for r in t["rows"]] == ["C3", "J1", "U2", "U10"]
    assert {r["designator"]: r["kind"] for r in t["rows"]}["J1"] == "connector"


def test_printed_resistors_are_left_out_unless_asked_for():
    s = _design()
    s.generate_resistor(6000, 15000, body_width=300, body_length=3000, ref_des="R1")
    assert "R1" not in _rows(s)
    r = _rows(s, include_printed=True)
    assert r["R1"]["kind"] == "printed"


def test_csv_file(tmp_path):
    s = _design()
    s.sref("SO4", (12000, 6000), rotation=90, label="U2")
    out = s.export_pick_and_place(str(tmp_path / "board_pnp.csv"))
    assert out["format"] == "csv"
    with open(out["path"], newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == CSV_COLUMNS
    assert rows[1] == ["U2", "SO4", "SO4", "smd", "9", "5.635", "90", "Top", "No"]


def test_kicad_pos_file(tmp_path):
    s = _design()
    s.sref("SO4", (12000, 6000), rotation=90, label="U2")
    path = str(tmp_path / "board.pos")
    assert s.export_pick_and_place(path)["format"] == "kicad"
    text = open(path, encoding="utf-8").read().splitlines()
    assert text[2] == "## Unit = mm, Angle = deg."
    row = [ln for ln in text if ln.startswith("U2")][0].split()
    assert row == ["U2", "SO4", "SO4", "9.0000", "5.6350", "90.0000", "top"]
    assert text[-1] == "## End"


def test_unknown_format_is_refused(tmp_path):
    s = _design()
    with pytest.raises(ValueError):
        write_pick_and_place(s.pick_and_place(), str(tmp_path / "x.txt"), fmt="xml")


def test_pnp_command():
    pytest.importorskip("tkinter")
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _design()
    s.sref("SO4", (12000, 6000), rotation=90, label="U2")
    ci = CommandInterpreter(s, ScriptConsole(s))
    out = ci.run("PNP LIST")
    assert "1 part(s); origin substrate (1, 1) mm" in out and "rot   90.00" in out
    out = ci.run("PNP LIST origin=design")
    assert "x   10.0000" in out
