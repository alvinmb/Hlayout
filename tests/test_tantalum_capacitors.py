import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "examples"))

from hlayout.core.shapes import Path, Rectangle
from hlayout.io.gdsio import load_gds
from hlayout.scripting import Session

SCRIPT = os.path.join(ROOT, "examples", "generate_tantalum_capacitors.py")
OUT_GDS = os.path.join(ROOT, "examples", "tantalum_capacitors.gds")

EXPECTED_CASES = [
    "EIA-3216 (Case A)", "EIA-3528 (Case B)", "EIA-6032 (Case C)", "EIA-7343 (Case D)",
]


def test_generate_tantalum_capacitors_script_runs_and_produces_valid_gds():
    result = subprocess.run(
        [sys.executable, SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert os.path.exists(OUT_GDS)

    lib = load_gds(OUT_GDS)
    assert lib.name == "TANTALUM-CAPACITORS"
    for name in EXPECTED_CASES:
        assert name in lib.cells
        cell = lib.cells[name]
        assert cell.shapes, f"{name} has no pads/outline/label shapes"
    assert "INDEX" in lib.cells
    assert len(lib.cells["INDEX"].srefs) == len(EXPECTED_CASES)


def test_tantalum_capacitor_library_can_be_attached_found_and_placed():
    assert os.path.exists(OUT_GDS), "run the script-runs test first, or generate the file manually"
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.attach_library(OUT_GDS)

    matches = s.find_cells("EIA-")
    names = {m["name"] for m in matches}
    assert set(EXPECTED_CASES) <= names

    ref = s.place_cell("EIA-6032 (Case C)", (0, 0))
    assert ref.cell_name == "EIA-6032 (Case C)"
    assert "EIA-6032 (Case C)" in s.library.cells
    assert len(s.library.cells["EIA-6032 (Case C)"].shapes) > 0


def test_each_case_has_exactly_two_pins_numbered_1_and_2():
    lib = load_gds(OUT_GDS)
    for name in EXPECTED_CASES:
        pads = [sh for sh in lib.cells[name].shapes if isinstance(sh, Rectangle) and sh.layer == 20]
        assert sorted(p.pin for p in pads) == ["1", "2"], name


def test_pin_1_is_the_anode_on_the_left_end_pin_2_the_cathode_on_the_right():
    # Component origin is the body's own BOTTOM-LEFT corner (see the
    # module docstring), so "left end" means nearer x=0 than the far
    # (length) edge, not literally negative.
    lib = load_gds(OUT_GDS)
    for name in EXPECTED_CASES:
        cell = lib.cells[name]
        x0, _y0, x1, _y1 = cell.bbox()
        mid_x = (x0 + x1) / 2.0
        pads = {sh.pin: sh for sh in cell.shapes if isinstance(sh, Rectangle) and sh.layer == 20}
        cx1 = sum(p[0] for p in pads["1"].points) / len(pads["1"].points)
        cx2 = sum(p[0] for p in pads["2"].points) / len(pads["2"].points)
        assert cx1 < mid_x < cx2, name


def test_each_case_has_a_polarity_band_near_the_anode_end():
    lib = load_gds(OUT_GDS)
    for name in EXPECTED_CASES:
        cell = lib.cells[name]
        x0, _y0, x1, _y1 = cell.bbox()
        mid_x = (x0 + x1) / 2.0
        bands = [sh for sh in cell.shapes if isinstance(sh, Rectangle) and sh.layer == 62]
        assert len(bands) == 1, name
        band_cx = sum(p[0] for p in bands[0].points) / len(bands[0].points)
        assert band_cx < mid_x, f"{name}: polarity band should sit on the anode (left) end"


def test_each_case_has_a_body_outline_and_the_pads_overlap_it():
    lib = load_gds(OUT_GDS)
    for name in EXPECTED_CASES:
        cell = lib.cells[name]
        outlines = [sh for sh in cell.shapes if isinstance(sh, Path) and sh.layer == 61]
        assert len(outlines) == 1, name
        ox0, oy0, ox1, oy1 = [min(p[0] for p in outlines[0].points), min(p[1] for p in outlines[0].points),
                               max(p[0] for p in outlines[0].points), max(p[1] for p in outlines[0].points)]
        pads = [sh for sh in cell.shapes if isinstance(sh, Rectangle) and sh.layer == 20]
        for pad in pads:
            px0 = min(p[0] for p in pad.points)
            px1 = max(p[0] for p in pad.points)
            # each pad must both overlap the body (not float free of it) and
            # extend past the body's own end (a real solder-toe overhang).
            overlaps_body = px0 < ox1 and px1 > ox0
            extends_past_end = px0 < ox0 or px1 > ox1
            assert overlaps_body and extends_past_end, f"{name} pin {pad.pin}"


def test_step_parts_covers_every_case_and_writes_a_valid_macro(tmp_path):
    import generate_tantalum_capacitors as gen
    from hlayout.io.example_step3d import write_step_macro

    parts = gen.step_parts()
    assert {p[0] for p in parts} == set(EXPECTED_CASES)
    assert all(func == "make_end_cap" for _name, func, _kwargs in parts)
    assert all(kwargs["overhang"] == gen.CASES[name]["overhang"] for name, _func, kwargs in parts)

    path = write_step_macro(parts, str(tmp_path / "step_out"))
    text = open(path).read()
    for name in EXPECTED_CASES:
        assert repr(name) in text
