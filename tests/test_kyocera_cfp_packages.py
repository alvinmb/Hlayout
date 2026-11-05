# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "examples"))

from hlayout.core.shapes import Path, Rectangle
from hlayout.io.gdsio import load_gds
from hlayout.scripting import Session

SCRIPT = os.path.join(ROOT, "examples", "generate_kyocera_cfp_packages.py")
OUT_GDS = os.path.join(ROOT, "examples", "kyocera_cfp_packages.gds")

PIN_COUNTS = [20, 30, 40]
EXPECTED_CELLS = [f"KYOCERA-CFP-{p}" for p in PIN_COUNTS]

INCH_MM = 25.4


def test_generate_kyocera_cfp_packages_script_runs_and_produces_valid_gds():
    result = subprocess.run(
        [sys.executable, SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert os.path.exists(OUT_GDS)

    lib = load_gds(OUT_GDS)
    assert lib.name == "KYOCERA-CFP-PACKAGES"
    for name in EXPECTED_CELLS:
        assert name in lib.cells
        assert lib.cells[name].shapes, f"{name} has no pads/outline/lid/label shapes"
    assert "INDEX" in lib.cells
    assert len(lib.cells["INDEX"].srefs) == len(EXPECTED_CELLS)


def test_kyocera_cfp_library_can_be_attached_found_and_placed():
    assert os.path.exists(OUT_GDS), "run the script-runs test first, or generate the file manually"
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.attach_library(OUT_GDS)

    matches = s.find_cells("KYOCERA-CFP")
    names = {m["name"] for m in matches}
    assert set(EXPECTED_CELLS) <= names

    ref = s.place_cell("KYOCERA-CFP-30", (0, 0))
    assert ref.cell_name == "KYOCERA-CFP-30"
    assert len(s.library.cells["KYOCERA-CFP-30"].shapes) > 0


def test_each_pin_count_has_that_many_pads_numbered_1_to_n():
    lib = load_gds(OUT_GDS)
    for pins in PIN_COUNTS:
        pads = [sh for sh in lib.cells[f"KYOCERA-CFP-{pins}"].shapes
                if isinstance(sh, Rectangle) and sh.layer == 20]
        assert len(pads) == pins
        assert sorted(int(p.pin) for p in pads) == list(range(1, pins + 1))


def test_pads_split_evenly_between_top_and_bottom_rows_at_the_row_pitch():
    lib = load_gds(OUT_GDS)
    row_pitch_dbu = round(0.9 * INCH_MM * 1000)  # mm_to_dbu(0.9") -- 1 DBU = 1 micron
    for pins in PIN_COUNTS:
        cell = lib.cells[f"KYOCERA-CFP-{pins}"]
        pads = [sh for sh in cell.shapes if isinstance(sh, Rectangle) and sh.layer == 20]
        ys = sorted({round(sum(p[1] for p in sh.points) / len(sh.points)) for sh in pads})
        assert len(ys) == 2, pins
        assert abs((ys[1] - ys[0]) - row_pitch_dbu) < 10
        top = sum(1 for sh in pads if round(sum(p[1] for p in sh.points) / len(sh.points)) == ys[1])
        assert top == pins // 2 == len(pads) - top


def test_pin_to_pin_pitch_along_a_row_is_0_1_inch():
    lib = load_gds(OUT_GDS)
    pin_pitch_dbu = round(0.1 * INCH_MM * 1000)
    cell = lib.cells["KYOCERA-CFP-20"]
    pads = [sh for sh in cell.shapes if isinstance(sh, Rectangle) and sh.layer == 20]
    bottom = sorted(
        (round(sum(p[0] for p in sh.points) / len(sh.points)) for sh in pads if int(sh.pin) <= 10)
    )
    deltas = [b - a for a, b in zip(bottom, bottom[1:])]
    assert all(abs(d - pin_pitch_dbu) < 10 for d in deltas)


def test_metal_lid_is_a_single_rectangle_inset_from_the_body_outline():
    lib = load_gds(OUT_GDS)
    for pins in PIN_COUNTS:
        cell = lib.cells[f"KYOCERA-CFP-{pins}"]
        outlines = [sh for sh in cell.shapes if isinstance(sh, Path) and sh.layer == 61]
        lids = [sh for sh in cell.shapes if isinstance(sh, Rectangle) and sh.layer == 63]
        assert len(outlines) == 1, pins
        assert len(lids) == 1, pins
        ox0 = min(p[0] for p in outlines[0].points)
        ox1 = max(p[0] for p in outlines[0].points)
        oy0 = min(p[1] for p in outlines[0].points)
        oy1 = max(p[1] for p in outlines[0].points)
        lx0 = min(p[0] for p in lids[0].points)
        lx1 = max(p[0] for p in lids[0].points)
        ly0 = min(p[1] for p in lids[0].points)
        ly1 = max(p[1] for p in lids[0].points)
        assert ox0 < lx0 < lx1 < ox1, pins
        assert oy0 < ly0 < ly1 < oy1, pins


def test_rejects_an_odd_pin_count():
    result = subprocess.run(
        [sys.executable, SCRIPT, "--start", "21", "--stop", "21"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "must be even" in result.stderr


def test_rejects_a_lid_inset_too_large_for_the_body():
    result = subprocess.run(
        [sys.executable, SCRIPT, "--start", "20", "--stop", "20", "--lid-inset", "50"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0


def test_step_parts_covers_every_pin_count_and_writes_a_valid_macro(tmp_path):
    import generate_kyocera_cfp_packages as gen
    from hlayout.io.example_step3d import write_step_macro

    lib, sizes = gen.build(PIN_COUNTS, gen.ROW_PITCH_MM, gen.PIN_PITCH_MM,
                           gen.PAD_LENGTH_MM, gen.PAD_WIDTH_MM, gen.MARGIN_MM, gen.LID_INSET_MM)
    parts = gen.step_parts(PIN_COUNTS, sizes, gen.PIN_PITCH_MM, gen.PAD_LENGTH_MM,
                           gen.PAD_WIDTH_MM, gen.LID_INSET_MM)
    assert {p[0] for p in parts} == set(EXPECTED_CELLS)
    assert all(func == "make_dip" for _name, func, _kwargs in parts)
    assert all(kwargs["lid"] is not None for _name, _func, kwargs in parts)  # every CFP has a lid

    path = write_step_macro(parts, str(tmp_path / "step_out"))
    text = open(path).read()
    for name in EXPECTED_CELLS:
        assert repr(name) in text
