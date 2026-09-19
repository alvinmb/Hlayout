import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "examples"))

from hlayout.core.shapes import Rectangle
from hlayout.io.gdsio import load_gds
from hlayout.scripting import Session

SCRIPT = os.path.join(ROOT, "examples", "generate_sot_packages.py")
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


def test_gullwing_ic_packages_have_the_right_pin_count_numbered_1_to_n():
    lib = load_gds(OUT_GDS)
    for name, pins in GULLWING_IC_PACKAGES.items():
        pads = [sh for sh in lib.cells[name].shapes if isinstance(sh, Rectangle) and sh.layer == 20]
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
        pads = [sh for sh in cell.shapes if isinstance(sh, Rectangle) and sh.layer == 20]
        pin_ys = [sum(p[1] for p in sh.points) / len(sh.points) for sh in pads]
        pin_span_y = max(pin_ys) - min(pin_ys)
        # the body must be at least as long, along the row (Y), as the pins
        # actually spread, and narrower across the two rows (X) than along Y.
        assert (oy1 - oy0) >= pin_span_y, name
        assert (ox1 - ox0) < (oy1 - oy0), name


def test_step_parts_covers_every_package_and_writes_a_valid_macro(tmp_path):
    import generate_sot_packages as gen
    from hlayout.io.example_step3d import write_step_macro

    parts = gen.step_parts()
    assert {p[0] for p in parts} == set(EXPECTED_PACKAGES)
    assert all(func == "make_dip_x" for _name, func, _kwargs in parts)
    # a tab_pin package's tab lead width made it into the 3D lead list too
    for name, spec in gen.PACKAGES.items():
        if spec.get("tab_pin"):
            kwargs = next(k for n, _f, k in parts if n == name)
            all_leads = kwargs["leads_neg_x"] + kwargs["leads_pos_x"]
            widths = sorted({w for _pos, w in all_leads})
            assert spec["tab"][1] in widths, name

    path = write_step_macro(parts, str(tmp_path / "step_out"))
    text = open(path).read()
    for name in EXPECTED_PACKAGES:
        assert repr(name) in text
