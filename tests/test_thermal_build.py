# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Thermal analysis from a real layout: the model builder
(hlayout.core.thermal_build), the thermal rules in the technology
file, the Session methods and the THERMAL command family."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("shapely")

from hlayout.core.errors import LayoutError  # noqa: E402
from hlayout.core.thermal import ThermalError  # noqa: E402
from hlayout.core.thermal_build import (  # noqa: E402
    build_thermal_model,
    candidate_parts,
    find_substrate_layer,
    layer_roles,
)
from hlayout.gui.commands import CommandInterpreter  # noqa: E402
from hlayout.io import techfile  # noqa: E402
from hlayout.scripting import Session  # noqa: E402
from hlayout.scripting.console import ScriptConsole  # noqa: E402


def _board(with_die=False):
    """10 x 6 mm alumina substrate (outline on layer 10, named), two
    thick-film resistors on the default rule layers (ink 30, conductor
    31, glaze 32), and optionally a bare die."""
    s = Session.new("THERMALTEST")
    s.set_layer_style(10, name="Substrate")
    s.new_cell("TOP")
    s.rect(10, 0, 0, 10000, 6000)
    s.convert_rects_to_outline(10, width=100)
    s.generate_resistor(3000, 3000, body_width=1000, body_length=2000, ref_des="R1",
                        resistor_layer=30)
    s.generate_resistor(7000, 3000, body_width=1000, body_length=2000, ref_des="R2",
                        resistor_layer=30)
    s.use_cell("TOP")
    if with_die:
        s.new_cell("DIE")
        s.rect(50, -500, -500, 500, 500)
        s.use_cell("TOP")
        s.sref("DIE", (5000, 1200), label="U1")
    return s


def test_layer_roles_and_substrate_come_from_the_design_rules():
    s = _board()
    roles = layer_roles(s.library)
    assert roles[30] == "resistor" and roles[31] == "conductor" and roles[32] == "glaze"
    assert find_substrate_layer(s.library, s.cell) == 10


def test_thermal_rules_round_trip_and_fall_back_to_defaults():
    s = _board()
    rules = techfile.load_thermal_rules(s.library)
    assert rules["boundary"]["substrate_material"] == "alumina_96"
    rules["boundary"]["base_temp_c"] = "85"
    rules["boundary"]["top_htc"] = "not a number"
    rules["powers"]["R1"] = 250
    techfile.save_thermal_rules(s.library, rules)
    again = techfile.load_thermal_rules(s.library)
    assert again["boundary"]["base_temp_c"] == 85.0
    assert again["boundary"]["top_htc"] == techfile.DEFAULT_THERMAL["boundary"]["top_htc"]
    assert again["powers"] == {"R1": 250}
    s.library.tech_params[techfile.THERMAL_KEY] = "{broken"
    assert techfile.load_thermal_rules(s.library)["limits"]["die_max_c"] == 125.0


def test_candidate_parts_lists_resistors_then_components_with_power_source():
    s = _board(with_die=True)
    techfile.save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 1000, "tolerance": 5, "power_mw": 100,
         "body_width": 1000, "selected_ink": 1000}])
    s.set_thermal_power("U1", 300)
    parts = {p["ref_des"]: p for p in candidate_parts(s.library, "TOP")}
    assert parts["R1"]["power_mw"] == 100 and parts["R1"]["source"] == "designer"
    assert parts["R2"]["power_mw"] == 0 and parts["R2"]["source"] == ""
    assert parts["U1"]["kind"] == "component" and parts["U1"]["source"] == "thermal"
    assert [p["kind"] for p in s.thermal_parts()][:2] == ["resistor", "resistor"]


def test_relabelled_resistor_takes_power_from_the_row_its_cell_came_from():
    """The notch-filter case: Designer rows R1, R2, R3, R1, R5, R; the
    fourth resistor was generated from the second "R1" row (cell
    "R1_1") and labelled R4 on the layout, the sixth from row "R"."""
    s = Session.new("RELABEL")
    s.set_layer_style(10, name="Substrate")
    s.new_cell("TOP")
    s.rect(10, 0, 0, 12000, 6000)
    s.generate_resistor(2000, 3000, 1000, 2000, ref_des="R1", resistor_layer=30)
    s.generate_resistor(6000, 3000, 1000, 2000, ref_des="R1", resistor_layer=30)
    s.generate_resistor(10000, 3000, 1000, 2000, ref_des="R", resistor_layer=30)
    s.use_cell("TOP")
    assert [r.cell_name for r in s.cell.srefs] == ["R1", "R1_1", "R"]
    s.cell.srefs[1].label = "R4"
    s.cell.srefs[2].label = "R6"
    row = {"value": 1000, "tolerance": 5, "body_width": 1000, "selected_ink": 1000}
    techfile.save_resistor_list(s.library, [
        dict(row, ref_des="R1", power_mw=100), dict(row, ref_des="R1", power_mw=250),
        dict(row, ref_des="R", power_mw=60)])
    parts = {p["ref_des"]: p for p in candidate_parts(s.library, "TOP")}
    assert (parts["R1"]["power_mw"], parts["R1"]["source"]) == (100, "designer")
    assert (parts["R4"]["power_mw"], parts["R4"]["source"]) == (250, "cell")   # highest "R1"
    assert (parts["R6"]["power_mw"], parts["R6"]["source"]) == (60, "cell")
    s.set_thermal_power("R4", 10)                    # an explicit power still wins
    assert candidate_parts(s.library, "TOP")[1]["source"] == "thermal"
    # a resistor whose cell matches no row either still gets nothing
    s.cell.srefs[2].cell_name = "R"
    techfile.save_resistor_list(s.library, [dict(row, ref_des="R1", power_mw=100)])
    assert {p["ref_des"]: p for p in candidate_parts(s.library, "TOP")}["R6"]["source"] == ""


def test_build_makes_substrate_film_and_part_slices():
    s = _board(with_die=True)
    s.set_thermal_power("R1", 200)
    s.set_thermal_power("U1", 500)
    m = build_thermal_model(s.library, "TOP", grid_um=250)
    roles = [sl.role for sl in m.slices]
    assert roles.count("substrate") == 5
    assert "film" in roles and roles[-1] == "body" and "attach" in roles
    assert (m.nx, m.ny) == (40, 24) and m.origin == (0.0, 0.0)
    total = sum(sl.t for sl in m.slices if sl.role == "substrate")
    assert np.allclose(total[total > 0], 635.0)
    names = {p.ref_des: p for p in m.parts}
    assert set(names) == {"R1", "R2", "U1"}
    assert names["R1"].area_mm2 == pytest.approx(2.0, rel=0.02)   # ink between the pads
    assert names["U1"].area_mm2 == pytest.approx(1.0)


def test_run_thermal_balances_energy_and_ranks_the_powered_resistor_hottest():
    s = _board()
    s.set_thermal_power("R1", 100)
    r = s.run_thermal(grid=250)
    assert r is s.last_thermal_result
    assert r.energy_error < 1e-6
    assert r.power_in == pytest.approx(0.1)
    by = {p.ref_des: p for p in r.parts}
    assert by["R1"].t_max > by["R2"].t_max > 25.0
    # 0.1 W straight down through 2 mm^2 of 635 um alumina, with no
    # spreading at all, would be 1.32 C; spreading must bring the rise
    # under that, and it can't be less than the whole 60 mm^2 substrate
    # conducting uniformly (0.044 C)
    assert 0.044 < by["R1"].rise < 1.32
    assert by["R1"].ok and not r.failures


def test_base_temperature_override_shifts_the_whole_result():
    s = _board()
    s.set_thermal_power("R1", 100)
    rules = techfile.load_thermal_rules(s.library)
    rules["boundary"]["top_htc"] = 0.0          # sealed: the base is the only sink
    cold = s.run_thermal(grid=250, rules=rules).parts[0]
    hot = s.run_thermal(grid=250, base_temp=85.0, rules=rules).parts[0]
    assert hot.t_max == pytest.approx(cold.t_max + 60.0, abs=1e-6)
    assert hot.rise == pytest.approx(cold.rise, abs=1e-6)


def test_die_junction_runs_hotter_than_the_substrate_under_it():
    s = _board(with_die=True)
    s.set_thermal_power("U1", 1000)
    r = s.run_thermal(grid=250)
    u1 = next(p for p in r.parts if p.ref_des == "U1")
    under = r.temperature_at((5000, 1200), which="substrate 5")
    assert u1.t_max > under > 25.0
    # the 40 um epoxy alone is 40e-6 / (2.5 * 1e-6) = 16 C at 1 W over 1 mm^2
    assert u1.t_max - under > 12.0


def test_aluminium_nitride_runs_cooler_than_alumina():
    s = _board()
    s.set_thermal_power("R1", 500)
    alumina = s.run_thermal(grid=250).parts[0].rise
    rules = techfile.load_thermal_rules(s.library)
    rules["boundary"]["substrate_material"] = "aln"
    aln = s.run_thermal(grid=250, rules=rules).parts[0].rise
    assert aln < 0.5 * alumina


def test_limit_failure_is_reported_and_unpowered_design_warns():
    s = _board()
    r = s.run_thermal(grid=250)
    assert any("no part has any power" in w for w in r.warnings)
    s.set_thermal_power("R1", 5000)          # 2.5 W/mm^2: far over the density limit
    r = s.run_thermal(grid=250)
    assert [p.ref_des for p in r.failures] == ["R1"]
    assert any("OVER LIMIT" in line for line in r.summary_lines())


def test_missing_cell_and_empty_design_raise():
    s = Session.new("EMPTY")
    s.new_cell("TOP")
    with pytest.raises(LayoutError):
        s.run_thermal()
    with pytest.raises(ThermalError):
        build_thermal_model(s.library, "NOPE")


def test_report_csv_and_contours(tmp_path):
    s = _board()
    s.set_thermal_power("R1", 100)
    r = s.run_thermal(grid=250)
    html_path = s.export_thermal_report(r, str(tmp_path / "t.html"))
    text = open(html_path, encoding="utf-8").read()
    assert "data:image/png;base64," in text and "R1" in text
    assert "All parts are within their limits." in text
    csv_path = s.export_thermal_csv(r, str(tmp_path / "t.csv"))
    rows = open(csv_path, encoding="utf-8").read().splitlines()
    assert rows[0].startswith("ref_des,kind") and rows[1].startswith("R1,resistor")
    before = len(s.cell.shapes)
    n = s.draw_thermal_contours(r, 56, step=0.05)
    assert n > 0 and len(s.cell.shapes) == before + n
    assert all(sh.layer == 56 for sh in s.cell.shapes[before:])
    assert s.undo() and len(s.cell.shapes) == before


def test_thermal_commands():
    s = _board()
    ci = CommandInterpreter(s, ScriptConsole(s))
    assert "R1" in ci.run("THERMAL PARTS")
    assert ci.run("THERMAL POWER R1 100") == "thermal: R1 = 100 mW"
    assert ci.run("THERMAL SET base_temp_c 60").endswith("60.0")
    assert "2.5" in ci.run("THERMAL MATERIAL 31 epoxy_ag")
    assert ci.run("THERMAL MATERIAL 31 CLEAR").endswith("role default")
    assert "alumina_96" in ci.run("THERMAL MATERIALS")
    assert ci.run("THERMAL REPORT out=x.html").startswith("error: no thermal result")
    out = ci.run("THERMAL RUN grid=250")
    assert "energy balance error" in out and "R1" in out
    assert s.last_thermal_result.model.base_temp_c == 60.0
    assert ci.run("THERMAL CONTOURS 56 step=0.05").startswith("thermal: drew ")
    # a step bigger than the whole temperature range must say so, with a
    # step that would work, rather than silently drawing nothing
    ci.run("THERMAL SET top_htc 0")
    ci.run("THERMAL RUN grid=250")
    none = ci.run("THERMAL CONTOURS 56 step=500")
    assert "no contours drawn" in none and "try step=" in none
    assert ci.run("THERMAL POWER R1 CLEAR").endswith("cleared")
    assert ci.run("THERMAL SET nonsense 1").startswith("error: unknown thermal setting")
    assert ci.run("THERMAL").startswith("error: usage: THERMAL")
