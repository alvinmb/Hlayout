# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)

from hlayout.io.gdsio import load_gds

SCRIPT = os.path.join(ROOT, "examples", "generate_perf_test.py")
DEFAULT_OUT = os.path.join(ROOT, "examples", "perf_test.gds")


def test_default_run_produces_1000_rects_and_100_paths():
    result = subprocess.run(
        [sys.executable, SCRIPT], cwd=ROOT, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert os.path.exists(DEFAULT_OUT)

    lib = load_gds(DEFAULT_OUT)
    assert lib.name == "PERF_TEST"
    cell = lib.cells["TOP"]
    rects = [s for s in cell.shapes if s.layer == 0]
    paths = [s for s in cell.shapes if s.layer == 1]
    assert len(rects) == 1000
    assert len(paths) == 100
    assert len(cell.shapes) == 1100


def test_rects_and_paths_counts_are_configurable(tmp_path):
    out = tmp_path / "small.gds"
    result = subprocess.run(
        [sys.executable, SCRIPT, "--rects", "30", "--paths", "7", "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert out.exists()

    lib = load_gds(str(out))
    cell = lib.cells["TOP"]
    rects = [s for s in cell.shapes if s.layer == 0]
    paths = [s for s in cell.shapes if s.layer == 1]
    assert len(rects) == 30
    assert len(paths) == 7


def test_generated_design_attaches_and_places_from_a_session(tmp_path):
    # The generated library should be usable the same way any other
    # attached reference library is (see test_library_attach.py).
    from hlayout.scripting import Session

    out = tmp_path / "attach_check.gds"
    result = subprocess.run(
        [sys.executable, SCRIPT, "--rects", "10", "--paths", "2", "--out", str(out)],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr

    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.attach_library(str(out))
    matches = s.find_cells("TOP")
    assert any(m["name"] == "TOP" and m["source"] != "current" for m in matches)
