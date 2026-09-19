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
    lib = load_gds(OUT_GDS)
    for name in EXPECTED_PACKAGES:
        cell = lib.cells[name]
        outlines = [sh for sh in cell.shapes if isinstance(sh, Path) and sh.layer == 61]
        assert len(outlines) == 1, name
        ox0 = min(p[0] for p in outlines[0].points)
        oy0 = min(p[1] for p in outlines[0].points)
        ox1 = max(p[0] for p in outlines[0].points)
        oy1 = max(p[1] for p in outlines[0].points)
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
