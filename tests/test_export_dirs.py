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


def test_export_freecad_macro_writes_even_with_unmatched_components(tmp_path):
    """File > Export to FreeCAD (3D Macro)...: an unmatched component
    (or one whose STEP file isn't built yet) must never block the
    export -- the match report is purely informational, and the file
    is always written (the unmatched cell just renders as a flat
    extruded box, same as always) regardless of how the report dialog
    is dismissed."""
    pytest.importorskip("shapely")
    from hlayout.gui import dialogs
    from hlayout.io.techfile import save_layer_thickness

    app = _make_app()
    try:
        app.current_path = str(tmp_path / "board1.gds")
        app.session.new_cell("MYSTERY")
        app.session.use_cell("MYSTERY")
        app.session.path(61, [(-50, -50), (50, -50)], width=500)
        app.session.use_cell("TOP")
        app.session.sref("MYSTERY", (0, 0))
        save_layer_thickness(app.session.library, {"0": 10})

        shown = []
        orig = dialogs.show_scrollable_text
        dialogs.show_scrollable_text = lambda title, lines, parent, **kw: shown.append(lines)
        try:
            app.export_freecad_macro()
        finally:
            dialogs.show_scrollable_text = orig

        assert shown and any("MYSTERY" in line for line in shown[0])   # still reported
        written = os.listdir(os.path.join(str(tmp_path), "freecad"))
        assert any(f.endswith(".FCMacro") for f in written)   # but still exported
    finally:
        app.destroy()
