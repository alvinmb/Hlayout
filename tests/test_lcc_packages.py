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

SCRIPT = os.path.join(ROOT, "examples", "generate_lcc_packages.py")
OUT_GDS = os.path.join(ROOT, "examples", "lcc_packages.gds")

EXPECTED_PACKAGES = ["LCC-8", "LCC-12", "LCC-16", "LCC-20", "LCC-24", "LCC-28", "LCC-32"]


def test_generate_lcc_packages_script_runs_and_produces_valid_gds():
    result = subprocess.run(
        [sys.executable, SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert os.path.exists(OUT_GDS)

    lib = load_gds(OUT_GDS)
    assert lib.name == "LCC-PACKAGES"
    for name in EXPECTED_PACKAGES:
        assert name in lib.cells
        cell = lib.cells[name]
        assert cell.shapes, f"{name} has no pads/outline/label shapes"
    assert "INDEX" in lib.cells
    assert len(lib.cells["INDEX"].srefs) == len(EXPECTED_PACKAGES)


def test_lcc_package_library_can_be_attached_found_and_placed():
    assert os.path.exists(OUT_GDS), "run the script-runs test first, or generate the file manually"
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.attach_library(OUT_GDS)

    matches = s.find_cells("LCC-2")
    names = {m["name"] for m in matches}
    assert {"LCC-20", "LCC-24", "LCC-28"} <= names

    ref = s.place_cell("LCC-20", (0, 0))
    assert ref.cell_name == "LCC-20"
    assert "LCC-20" in s.library.cells
    assert len(s.library.cells["LCC-20"].shapes) > 0


def test_each_lcc_package_has_exactly_its_pin_count_of_pads_numbered_1_to_n():
    lib = load_gds(OUT_GDS)
    for name in EXPECTED_PACKAGES:
        pins = int(name.split("-")[1])
        pads = [sh for sh in lib.cells[name].shapes if isinstance(sh, Rectangle) and sh.layer == 20]
        assert len(pads) == pins, name
        assert sorted(int(p.pin) for p in pads) == list(range(1, pins + 1)), name


def test_each_lcc_package_has_a_square_body_outline_at_the_bottom_left_origin():
    # The body outline has the castellation notch cut into it at every
    # terminal (2026-09 request: a real LCCC's castellation is a
    # physical scallop in the ceramic body, not just a copper add-on),
    # so a package with enough pins needs more than one Path element
    # to stay under MAX_PATH_POINTS -- one or more pieces sharing
    # endpoints so together they still trace one continuous outline.
    lib = load_gds(OUT_GDS)
    for name in EXPECTED_PACKAGES:
        cell = lib.cells[name]
        outlines = [sh for sh in cell.shapes if isinstance(sh, Path) and sh.layer == 61]
        assert len(outlines) >= 1, name
        ox0 = min(p[0] for sh in outlines for p in sh.points)
        oy0 = min(p[1] for sh in outlines for p in sh.points)
        ox1 = max(p[0] for sh in outlines for p in sh.points)
        oy1 = max(p[1] for sh in outlines for p in sh.points)
        # square, and its own bottom-left corner at the component origin
        # (matching hlayout.core.smdlib's / component_library's convention)
        assert abs((ox1 - ox0) - (oy1 - oy0)) < 5, name
        assert abs(ox0) < 5, name
        assert abs(oy0) < 5, name


def test_pads_are_evenly_split_across_the_four_edges():
    lib = load_gds(OUT_GDS)
    for name in EXPECTED_PACKAGES:
        pins = int(name.split("-")[1])
        per_side = pins // 4
        cell = lib.cells[name]
        pads = [sh for sh in cell.shapes if isinstance(sh, Rectangle) and sh.layer == 20]
        x0, y0, x1, y1 = cell.bbox()
        on_bottom = sum(1 for p in pads if abs(min(pt[1] for pt in p.points) - y0) < 200)
        on_top = sum(1 for p in pads if abs(max(pt[1] for pt in p.points) - y1) < 200)
        on_left = sum(1 for p in pads if abs(min(pt[0] for pt in p.points) - x0) < 200)
        on_right = sum(1 for p in pads if abs(max(pt[0] for pt in p.points) - x1) < 200)
        assert [on_bottom, on_top, on_left, on_right] == [per_side] * 4, name


def test_lcc_20_body_matches_the_real_jedec_l20_sq_datasheet():
    # Regression test: CORNER_MARGIN_MM is back-solved from a real
    # 20-terminal LCC drawing ("L20(SQ) 0204") so a fresh 20-pin part
    # reproduces its 0.350" (8.89 mm) body exactly.
    lib = load_gds(OUT_GDS)
    outline = next(sh for sh in lib.cells["LCC-20"].shapes if isinstance(sh, Path) and sh.layer == 61)
    x0 = min(p[0] for p in outline.points)
    x1 = max(p[0] for p in outline.points)
    side_mm = (x1 - x0) / 1000.0
    assert abs(side_mm - 8.89) < 0.05


def test_body_outline_is_chamfered_with_a_smaller_cut_at_the_pin_1_corner():
    lib = load_gds(OUT_GDS)
    for name in EXPECTED_PACKAGES:
        cell = lib.cells[name]
        outline = next(sh for sh in cell.shapes if isinstance(sh, Path) and sh.layer == 61)
        pts = outline.points
        x0 = min(p[0] for p in pts)
        y0 = min(p[1] for p in pts)
        # a plain (unchamfered) square outline would have a point AT
        # the (x0, y0) corner itself; a chamfered one never does.
        assert (x0, y0) not in [(p[0], p[1]) for p in pts], name
        # the two points nearest the pin-1 (bottom-left) corner must be
        # closer to it than the points cutting the other three corners.
        near_pin1 = [p for p in pts if abs(p[0] - x0) < 50 or abs(p[1] - y0) < 50]
        dists = sorted((p[0] - x0) ** 2 + (p[1] - y0) ** 2 for p in near_pin1)
        assert dists[1] < dists[-1], name


def test_top_surface_outline_is_inset_from_the_body_outline():
    # The body outline on layer 61 may be split across several Path
    # pieces (see the test above) -- gather all of them for the true
    # combined bounding box rather than next()-ing just the first one.
    lib = load_gds(OUT_GDS)
    for name in EXPECTED_PACKAGES:
        cell = lib.cells[name]
        outlines = [sh for sh in cell.shapes if isinstance(sh, Path) and sh.layer == 61]
        top = next(sh for sh in cell.shapes if isinstance(sh, Path) and sh.layer == 63)
        bx0 = min(p[0] for sh in outlines for p in sh.points)
        by0 = min(p[1] for sh in outlines for p in sh.points)
        bx1 = max(p[0] for sh in outlines for p in sh.points)
        by1 = max(p[1] for sh in outlines for p in sh.points)
        tx0, ty0 = min(p[0] for p in top.points), min(p[1] for p in top.points)
        tx1, ty1 = max(p[0] for p in top.points), max(p[1] for p in top.points)
        assert bx0 < tx0 < tx1 < bx1, name
        assert by0 < ty0 < ty1 < by1, name


def test_rejects_a_pin_count_not_a_multiple_of_four():
    result = subprocess.run(
        [sys.executable, SCRIPT, "--start", "10", "--stop", "10"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "multiples of 4" in result.stderr


def test_custom_start_stop_step_changes_the_generated_series():
    out_path = os.path.join(ROOT, "examples", "_test_lcc_custom.gds")
    try:
        result = subprocess.run(
            [sys.executable, SCRIPT, "--start", "8", "--stop", "16", "--step", "4", "-o", out_path],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        lib = load_gds(out_path)
        assert set(lib.cells) >= {"LCC-8", "LCC-12", "LCC-16"}
        assert "LCC-20" not in lib.cells
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)


def test_step_parts_covers_every_pin_count_and_writes_a_valid_macro(tmp_path):
    import generate_lcc_packages as gen
    from hlayout.io.example_step3d import write_step_macro

    pin_counts = [8, 12, 16, 20, 24, 28, 32]
    lib, sides = gen.build(pin_counts, gen.PITCH_MM, gen.PAD_WIDTH_MM,
                            gen.PAD_DEPTH_MM, gen.CORNER_MARGIN_MM)
    parts = gen.step_parts(pin_counts, sides, gen.PITCH_MM, gen.PAD_WIDTH_MM, gen.PAD_DEPTH_MM)
    assert {p[0] for p in parts} == set(EXPECTED_PACKAGES)
    assert all(func == "make_lcc" for _name, func, _kwargs in parts)

    path = write_step_macro(parts, str(tmp_path / "step_out"))
    text = open(path).read()
    for name in EXPECTED_PACKAGES:
        assert repr(name) in text
