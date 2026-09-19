import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "examples"))

from hlayout.core.shapes import Path, Polygon
from hlayout.io.gdsio import load_gds
from hlayout.scripting import Session

SCRIPT = os.path.join(ROOT, "examples", "generate_ceramic_substrates.py")
OUT_GDS = os.path.join(ROOT, "examples", "ceramic_substrates.gds")

PIN_COUNTS = [20, 30, 40]
EXPECTED_CELLS = [f"SUBSTRATE-{p}{suffix}" for p in PIN_COUNTS for suffix in ("", "-HOLE")]


def test_generate_ceramic_substrates_script_runs_and_produces_valid_gds():
    result = subprocess.run(
        [sys.executable, SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert os.path.exists(OUT_GDS)

    lib = load_gds(OUT_GDS)
    assert lib.name == "CERAMIC-SUBSTRATES"
    for name in EXPECTED_CELLS:
        assert name in lib.cells
        assert lib.cells[name].shapes, f"{name} has no pads/outline/label shapes"
    assert "INDEX" in lib.cells
    assert len(lib.cells["INDEX"].srefs) == len(EXPECTED_CELLS)


def test_ceramic_substrate_library_can_be_attached_found_and_placed():
    assert os.path.exists(OUT_GDS), "run the script-runs test first, or generate the file manually"
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.attach_library(OUT_GDS)

    matches = s.find_cells("SUBSTRATE-20")
    names = {m["name"] for m in matches}
    assert {"SUBSTRATE-20", "SUBSTRATE-20-HOLE"} <= names

    ref = s.place_cell("SUBSTRATE-30-HOLE", (0, 0))
    assert ref.cell_name == "SUBSTRATE-30-HOLE"
    assert len(s.library.cells["SUBSTRATE-30-HOLE"].shapes) > 0


def test_each_pin_count_has_that_many_pads_numbered_1_to_n_on_both_variants():
    lib = load_gds(OUT_GDS)
    for pins in PIN_COUNTS:
        for suffix in ("", "-HOLE"):
            name = f"SUBSTRATE-{pins}{suffix}"
            pads = [sh for sh in lib.cells[name].shapes if isinstance(sh, Polygon) and sh.layer == 20]
            assert len(pads) == pins, name
            assert sorted(int(p.pin) for p in pads) == list(range(1, pins + 1)), name


def test_plain_variant_has_no_holes_and_hole_variant_has_one_hole_per_pad():
    lib = load_gds(OUT_GDS)
    for pins in PIN_COUNTS:
        plain = lib.cells[f"SUBSTRATE-{pins}"]
        holed = lib.cells[f"SUBSTRATE-{pins}-HOLE"]
        assert not [sh for sh in plain.shapes if sh.layer == 21]
        holes = [sh for sh in holed.shapes if isinstance(sh, Polygon) and sh.layer == 21]
        assert len(holes) == pins


def test_holes_are_concentric_with_their_pad_and_smaller():
    lib = load_gds(OUT_GDS)

    def centroid(poly):
        xs = [p[0] for p in poly.points]
        ys = [p[1] for p in poly.points]
        return sum(xs) / len(xs), sum(ys) / len(ys)

    def radius(poly):
        cx, cy = centroid(poly)
        return max(((p[0] - cx) ** 2 + (p[1] - cy) ** 2) ** 0.5 for p in poly.points)

    cell = lib.cells["SUBSTRATE-20-HOLE"]
    pads = {centroid(sh): sh for sh in cell.shapes if isinstance(sh, Polygon) and sh.layer == 20}
    holes = {centroid(sh): sh for sh in cell.shapes if isinstance(sh, Polygon) and sh.layer == 21}
    assert set(pads) == set(holes)
    for center, pad in pads.items():
        assert radius(holes[center]) < radius(pad)


def test_pads_split_evenly_between_the_top_and_bottom_edges():
    lib = load_gds(OUT_GDS)
    for pins in PIN_COUNTS:
        cell = lib.cells[f"SUBSTRATE-{pins}"]
        pads = [sh for sh in cell.shapes if isinstance(sh, Polygon) and sh.layer == 20]
        x0, y0, x1, y1 = cell.bbox()
        mid_y = (y0 + y1) / 2.0
        top = sum(1 for p in pads if sum(pt[1] for pt in p.points) / len(p.points) > mid_y)
        bottom = len(pads) - top
        assert top == bottom == pins // 2, pins


def test_rejects_an_odd_pin_count():
    result = subprocess.run(
        [sys.executable, SCRIPT, "--start", "21", "--stop", "21"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "must be even" in result.stderr


def test_rejects_a_pad_diameter_not_larger_than_the_hole_diameter():
    result = subprocess.run(
        [sys.executable, SCRIPT, "--pad-diameter", "0.5", "--hole-diameter", "0.8"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0
    assert "annular ring" in result.stderr


def test_step_parts_covers_every_cell_and_writes_a_valid_macro(tmp_path):
    import generate_ceramic_substrates as gen
    from hlayout.io.example_step3d import write_step_macro

    lib, sizes, names = gen.build(PIN_COUNTS, gen.PITCH_MM, gen.PAD_DIAMETER_MM,
                                  gen.HOLE_DIAMETER_MM, gen.DEPTH_MM, gen.MARGIN_MM)
    parts = gen.step_parts(PIN_COUNTS, sizes, gen.PITCH_MM, gen.PAD_DIAMETER_MM, gen.HOLE_DIAMETER_MM)
    assert {p[0] for p in parts} == set(EXPECTED_CELLS)
    assert all(func == "make_substrate_pins" for _name, func, _kwargs in parts)
    # only the -HOLE variant gets a nonzero pin_length (the braze feedthrough)
    for name, _func, kwargs in parts:
        expected = gen.PIN_LENGTH_MM if name.endswith("-HOLE") else 0.0
        assert kwargs["pin_length"] == expected, name

    path = write_step_macro(parts, str(tmp_path / "step_out"))
    text = open(path).read()
    for name in EXPECTED_CELLS:
        assert repr(name) in text
