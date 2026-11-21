# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "2D_Part_Generator_scripts"))

from hlayout.core.shapes import Rectangle
from hlayout.io.gdsio import load_gds
from hlayout.scripting import Session

SCRIPT = os.path.join(ROOT, "2D_Part_Generator_scripts", "generate_sot_packages.py")
OUT_GDS = os.path.join(ROOT, "examples", "sot_packages.gds")

EXPECTED_PACKAGES = [
    "SOT-23", "SOT-23-5", "SOT-23-6", "SOT-89", "SOT-143", "SOT-223", "SOT-363",
    "SOT-8", "SOT-16", "SOT-18", "SOT-20", "SOT-24", "SOT-28",
]

GULLWING_IC_PACKAGES = {"SOT-8": 8, "SOT-16": 16, "SOT-18": 18, "SOT-20": 20, "SOT-24": 24, "SOT-28": 28}


def test_generate_sot_packages_script_runs_and_produces_valid_gds():
    result = subprocess.run(
        [sys.executable, SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert os.path.exists(OUT_GDS)

    lib = load_gds(OUT_GDS)
    assert lib.name == "SOT-PACKAGES"
    for name in EXPECTED_PACKAGES:
        assert name in lib.cells
        cell = lib.cells[name]
        assert cell.shapes, f"{name} has no pads/outline/label shapes"
    assert "INDEX" in lib.cells
    assert len(lib.cells["INDEX"].srefs) == len(EXPECTED_PACKAGES)


def test_sot_package_library_can_be_attached_found_and_placed():
    assert os.path.exists(OUT_GDS), "run the script-runs test first, or generate the file manually"
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.attach_library(OUT_GDS)

    matches = s.find_cells("SOT-23")
    names = {m["name"] for m in matches}
    assert {"SOT-23", "SOT-23-5", "SOT-23-6"} <= names

    ref = s.place_cell("SOT-89", (0, 0))
    assert ref.cell_name == "SOT-89"
    assert "SOT-89" in s.library.cells
    assert len(s.library.cells["SOT-89"].shapes) > 0


def _pin_pads(cell):
    """The package's numbered land pads (on the conductor layer)."""
    return [sh for sh in cell.shapes if isinstance(sh, Rectangle) and getattr(sh, "pin", "")]


def test_gullwing_ic_packages_have_the_right_pin_count_numbered_1_to_n():
    lib = load_gds(OUT_GDS)
    for name, pins in GULLWING_IC_PACKAGES.items():
        pads = _pin_pads(lib.cells[name])
        assert len(pads) == pins, name
        assert sorted(int(p.pin) for p in pads) == list(range(1, pins + 1)), name


def test_gullwing_ic_packages_use_the_standard_1_27mm_soic_pitch():
    lib = load_gds(OUT_GDS)
    pitch_dbu = round(1.27 * 1000)  # 1 DBU = 1 micron
    for name in GULLWING_IC_PACKAGES:
        cell = lib.cells[name]
        pads = [sh for sh in cell.shapes if isinstance(sh, Rectangle) and sh.layer == 20]
        left = sorted(
            round(sum(p[1] for p in sh.points) / len(sh.points))
            for sh in pads if sum(p[0] for p in sh.points) / len(sh.points) < 0
        )
        deltas = [b - a for a, b in zip(left, left[1:])]
        assert all(abs(d - pitch_dbu) < 10 for d in deltas), name


def test_gullwing_ic_body_outline_is_not_rotated_90_degrees_from_the_pins():
    # Regression test: the body's own PACKAGES dict entry once had (across-rows,
    # along-row) backwards for every package here, drawing an outline rotated
    # 90 degrees from the actual pin span -- narrower than the pins along the
    # row, and needlessly long across the rows. Confirmed live 2026-09.
    from hlayout.core.shapes import Path
    lib = load_gds(OUT_GDS)
    for name in GULLWING_IC_PACKAGES:
        cell = lib.cells[name]
        outline = next(sh for sh in cell.shapes if isinstance(sh, Path) and sh.layer == 61)
        ox0 = min(p[0] for p in outline.points)
        ox1 = max(p[0] for p in outline.points)
        oy0 = min(p[1] for p in outline.points)
        oy1 = max(p[1] for p in outline.points)
        pads = _pin_pads(cell)
        pin_xs = [sum(p[0] for p in sh.points) / len(sh.points) for sh in pads]
        pin_span_x = max(pin_xs) - min(pin_xs)
        # rows run along X (top/bottom, "tb"): the body must be at least as
        # long, along the row, as the pins actually spread, and narrower
        # across the two rows (Y) than along X.
        assert (ox1 - ox0) >= pin_span_x, name
        assert (oy1 - oy0) < (ox1 - ox0), name


def test_power_packages_are_three_leads_plus_a_tab_on_lead_2():
    # SOT-89 / SOT-223: leads 1-2-3 in one row at the datasheet pitch,
    # the tab alone on the opposite side, centred on lead 2 and
    # numbered 2 (it is lead 2's metal)
    lib = load_gds(OUT_GDS)
    for name, pitch_mm in (("SOT-89", 1.5), ("SOT-223", 2.3)):
        pads = _pin_pads(lib.cells[name])
        centre = {}
        for p in pads:
            x0, y0, x1, y1 = p.bbox()
            centre.setdefault(p.pin, []).append(((x0 + x1) / 2.0, (y0 + y1) / 2.0, x1 - x0))
        assert sorted(centre) == ["1", "2", "3"], name
        row = [centre[k][0] for k in ("1", "2", "3")]
        assert len({round(y) for _x, y, _w in row}) == 1, name          # one row
        xs = [x for x, _y, _w in row]
        assert [round(b - a) for a, b in zip(xs, xs[1:])] == [round(pitch_mm * 1000)] * 2, name
        assert len(centre["2"]) == 2, name                               # lead 2 + tab
        tab = max(centre["2"], key=lambda t: t[2])
        assert round(tab[0]) == round(xs[1]), name                       # centred on lead 2
        assert (tab[1] > row[0][1]) and all(t[1] < tab[1] for t in row), name   # opposite side


def test_sot143_pin1_is_the_wide_lead_bottom_left():
    # TO-253: pin 1 wide, bottom-left, 1.7 from pin 2; pins 3 / 4 opposite
    # 1.9 apart; pin 1's outer (left) edge lines up with pin 4's
    lib = load_gds(OUT_GDS)
    pads = {p.pin: p.bbox() for p in _pin_pads(lib.cells["SOT-143"])}
    assert sorted(pads) == ["1", "2", "3", "4"]
    cx = {k: (b[0] + b[2]) / 2.0 for k, b in pads.items()}
    cy = {k: (b[1] + b[3]) / 2.0 for k, b in pads.items()}
    width = {k: b[2] - b[0] for k, b in pads.items()}
    assert width["1"] == max(width.values()) and width["1"] > width["2"]
    assert cy["1"] == cy["2"] < cy["3"] == cy["4"]                  # 1-2 bottom, 3-4 top
    assert cx["1"] < cx["2"] and cx["4"] < cx["3"]                  # 1 left, 4 above it
    assert round(cx["2"] - cx["1"]) == 1700 and round(cx["3"] - cx["4"]) == 1900
    assert round(pads["1"][0]) == round(pads["4"][0])               # outer edges line up
    assert round(cy["3"] - cy["1"]) == 2000                         # rows 2.0 apart


def test_step_parts_covers_every_package_and_writes_a_valid_macro(tmp_path):
    import generate_sot_packages as gen
    from hlayout.io.example_step3d import write_step_macro

    parts = gen.step_parts()
    assert {p[0] for p in parts} == set(EXPECTED_PACKAGES)
    # the 3D builder follows each package's leads: flat -> make_power_flat,
    # else by row axis ("tb" -> make_dip)
    for name, func, _kwargs in parts:
        spec = gen.PACKAGES[name]
        want = ("make_power_flat" if spec.get("flat")
                else "make_dip" if spec.get("rows") == "tb" else "make_dip_x")
        assert func == want, name
    # a tab_pin package's tab lead width made it into the 3D lead list too
    for name, spec in gen.PACKAGES.items():
        if spec.get("tab_pin") and not spec.get("flat"):
            kwargs = next(k for n, _f, k in parts if n == name)
            axis = "y" if spec.get("rows") == "tb" else "x"
            all_leads = kwargs[f"leads_neg_{axis}"] + kwargs[f"leads_pos_{axis}"]
            widths = sorted({w for _pos, w in all_leads})
            along_row = 0 if spec.get("rows") == "tb" else 1   # tb specs are (along, across)
            assert spec.get("tab_lead_w", spec["tab"][along_row]) in widths, name

    path = write_step_macro(parts, str(tmp_path / "step_out"))
    text = open(path).read()
    for name in EXPECTED_PACKAGES:
        assert repr(name) in text
