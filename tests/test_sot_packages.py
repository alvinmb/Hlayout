import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from hlayout.io.gdsio import load_gds
from hlayout.scripting import Session

SCRIPT = os.path.join(ROOT, "examples", "generate_sot_packages.py")
OUT_GDS = os.path.join(ROOT, "examples", "sot_packages.gds")

EXPECTED_PACKAGES = [
    "SOT-23", "SOT-23-5", "SOT-23-6", "SOT-89", "SOT-143", "SOT-223", "SOT-363",
]


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
