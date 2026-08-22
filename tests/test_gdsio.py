import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core import Library, PathCap
from hlayout.core.errors import LayoutError
from hlayout.io import save_gds, load_gds


def test_round_trip_all_primitives():
    lib = Library("RT")
    top = lib.new_cell("TOP")
    top.add_rectangle(0, 0, 0, 100, 50)
    top.add_polygon(1, [(0, 0), (10, 0), (15, 10), (5, 15), (0, 10)])
    top.add_path(2, [(0, 0), (50, 50), (100, 0)], width=5, cap="round")
    top.add_path(3, [(0, 0), (30, 0)], width=8, cap="square")
    top.add_text(4, (5, 5), "HELLO", height=8, font=1)
    sub = lib.new_cell("SUB")
    sub.add_rectangle(5, 0, 0, 20, 20)
    top.add_sref("SUB", origin=(200, 200), rotation=90, magnification=2, x_reflection=True)

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "rt.gds")
        save_gds(lib, path)
        lib2 = load_gds(path)

    assert set(lib2.cells.keys()) == {"TOP", "SUB"}
    t2 = lib2.get_cell("TOP")
    assert len(t2.shapes) == 5
    assert len(t2.srefs) == 1

    rect = [s for s in t2.shapes if s.layer == 0][0]
    assert rect.points == [(0, 0), (100, 0), (100, 50), (0, 50)]

    path2 = [s for s in t2.shapes if s.layer == 2][0]
    assert path2.width == 5
    assert path2.cap == PathCap.ROUND

    ref = t2.srefs[0]
    assert ref.cell_name == "SUB"
    assert ref.rotation == 90.0
    assert ref.magnification == 2.0
    assert ref.x_reflection is True


def test_load_gds_missing_file_raises_clean_layout_error(tmp_path):
    # A missing/wrong path used to surface as gdstk's own native-code
    # stderr print ("[GDSTK] Unable to open GDSII file for input.")
    # plus a bare OSError with no path in the message. It should now
    # fail fast with a clear, actionable LayoutError instead.
    missing = tmp_path / "does_not_exist.gds"
    with pytest.raises(LayoutError, match=r"GDSII file not found.*does_not_exist\.gds"):
        load_gds(str(missing))


def test_hlayout_app_cli_reports_missing_file_cleanly(tmp_path):
    import subprocess
    import sys as _sys

    root = os.path.join(os.path.dirname(__file__), "..")
    missing = tmp_path / "nope.gds"
    result = subprocess.run(
        [_sys.executable, os.path.join(root, "hlayout_app.py"), str(missing)],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1
    assert "GDSII file not found" in result.stderr
    assert "Traceback" not in result.stderr
