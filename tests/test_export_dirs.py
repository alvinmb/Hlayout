import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")


def _make_app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    try:
        lib = Library("EXPORTDIRS")
        lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    return app


def test_default_export_dir_none_when_design_never_saved():
    app = _make_app()
    try:
        assert app.current_path is None
        assert app.default_export_dir("gerber") is None
    finally:
        app.destroy()


def test_default_export_dir_is_a_subfolder_of_the_open_gds_files_folder(tmp_path):
    app = _make_app()
    try:
        app.current_path = str(tmp_path / "board1.gds")
        for sub, expect in (("gerber", "gerber"), ("dxf", "dxf"), ("freecad", "freecad")):
            out = app.default_export_dir(sub)
            assert out == str(tmp_path / expect)
            assert os.path.isdir(out)   # created, not just computed
    finally:
        app.destroy()


def test_default_export_dir_reuses_an_existing_folder(tmp_path):
    app = _make_app()
    try:
        app.current_path = str(tmp_path / "board1.gds")
        first = app.default_export_dir("gerber")
        (tmp_path / "gerber" / "marker.txt").write_text("x")
        second = app.default_export_dir("gerber")
        assert first == second
        assert (tmp_path / "gerber" / "marker.txt").exists()
    finally:
        app.destroy()
