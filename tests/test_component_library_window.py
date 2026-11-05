# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")
pytest.importorskip("shapely")


def _make_app(monkeypatch, tmp_path):
    """A fresh App with its own isolated app_settings.json, and a
    design saved into tmp_path so default_export_dir()/
    component_library_dir() have somewhere real to resolve to."""
    import hlayout.io.app_settings as app_settings
    from hlayout.gui.app import App
    from hlayout.core import Library
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    try:
        lib = Library("COMPLIBWIN")
        lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.current_path = str(tmp_path / "design.gds")
    return app


def _step_dir_with(tmp_path, *names):
    step_dir = tmp_path / "freecad"
    step_dir.mkdir(exist_ok=True)
    for n in names:
        (step_dir / f"{n}.step").write_text("")
    return str(step_dir)


def _add_design(app):
    """A passive matched by name, and one genuinely unmatched cell."""
    from hlayout.io.techfile import save_layer_thickness

    s = app.session
    s.new_cell("C1_0805")
    s.use_cell("C1_0805")
    s.rect(0, -400, -625, 400, 625)
    s.path(61, [(-1000, -625), (1000, -625)], width=100)
    s.use_cell("TOP")
    s.sref("C1_0805", (0, 0))

    s.new_cell("MYSTERY")
    s.use_cell("MYSTERY")
    s.path(61, [(-50, -50), (50, -50)], width=500)
    s.use_cell("TOP")
    s.sref("MYSTERY", (5000, 5000))

    save_layer_thickness(s.library, {"0": 10})


def _rows(win):
    # Tkinter's Treeview stores values as Tcl objects and hands back a
    # purely-numeric one (e.g. the Rotation column) as an int/float, not
    # the str it was inserted as -- normalize to str for comparison.
    return {win.table.item(i)["values"][0]: tuple(str(v) for v in win.table.item(i)["values"][1:])
            for i in win.table.get_children()}


def test_lists_matched_and_unmatched_cells(monkeypatch, tmp_path):
    from hlayout.gui.component_library_window import open_component_library_window

    app = _make_app(monkeypatch, tmp_path)
    try:
        _add_design(app)
        _step_dir_with(tmp_path, "CAP_0805")
        win = open_component_library_window(app)
        rows = _rows(win)
        assert rows["C1_0805"] == ("CAP_0805.step", "0", "auto")
        assert rows["MYSTERY"] == ("(none -- flat box)", "0", "none")
    finally:
        app.destroy()


def test_set_step_file_writes_a_manual_override(monkeypatch, tmp_path):
    from hlayout.gui import component_library_window as clw
    from hlayout.io.techfile import load_component_models

    app = _make_app(monkeypatch, tmp_path)
    try:
        _add_design(app)
        step_dir = _step_dir_with(tmp_path, "CAP_0805")
        custom_step = str(tmp_path / "my_custom.step")
        open(custom_step, "w").close()

        win = clw.open_component_library_window(app)
        win.table.selection_set("MYSTERY")

        class FakeDialog:
            def __init__(self, *a, **kw):
                self.result = {"mode": "model", "model_path": custom_step, "rotation_deg": 90.0}
            def destroy(self):
                pass
        monkeypatch.setattr(clw, "SetModelDialog", FakeDialog)
        monkeypatch.setattr(win, "wait_window", lambda dlg: None)

        win._set_step_file()

        models = load_component_models(app.session.library)
        assert models["MYSTERY"]["mode"] == "model"
        assert models["MYSTERY"]["model_path"] == custom_step
        assert models["MYSTERY"]["rotation_deg"] == 90.0
        rows = _rows(win)
        assert rows["MYSTERY"] == ("my_custom.step", "90", "manual")
    finally:
        app.destroy()


def test_manual_override_wins_over_auto_match(monkeypatch, tmp_path):
    from hlayout.gui.component_library_window import open_component_library_window
    from hlayout.io.techfile import save_component_models

    app = _make_app(monkeypatch, tmp_path)
    try:
        _add_design(app)
        _step_dir_with(tmp_path, "CAP_0805")
        custom_step = str(tmp_path / "override.step")
        open(custom_step, "w").close()
        save_component_models(app.session.library, {
            "C1_0805": {"mode": "model", "model_path": custom_step}})

        win = open_component_library_window(app)
        rows = _rows(win)
        assert rows["C1_0805"] == ("override.step", "0", "manual")
    finally:
        app.destroy()


def test_clear_override_reverts_to_auto_match(monkeypatch, tmp_path):
    from hlayout.gui.component_library_window import open_component_library_window
    from hlayout.io.techfile import load_component_models, save_component_models

    app = _make_app(monkeypatch, tmp_path)
    try:
        _add_design(app)
        _step_dir_with(tmp_path, "CAP_0805")
        custom_step = str(tmp_path / "override.step")
        open(custom_step, "w").close()
        save_component_models(app.session.library, {
            "C1_0805": {"mode": "model", "model_path": custom_step}})

        win = open_component_library_window(app)
        win.table.selection_set("C1_0805")
        win._clear_override()

        assert "C1_0805" not in load_component_models(app.session.library)
        rows = _rows(win)
        assert rows["C1_0805"] == ("CAP_0805.step", "0", "auto")
    finally:
        app.destroy()


def test_refresh_picks_up_a_newly_placed_component(monkeypatch, tmp_path):
    from hlayout.gui.component_library_window import open_component_library_window

    app = _make_app(monkeypatch, tmp_path)
    try:
        _add_design(app)
        _step_dir_with(tmp_path, "CAP_0805")
        win = open_component_library_window(app)
        assert "C2_1206" not in _rows(win)

        s = app.session
        s.new_cell("C2_1206")
        s.use_cell("C2_1206")
        s.rect(0, -1600, -800, 1600, 800)
        s.path(61, [(-1600, -800), (1600, -800)], width=100)
        s.use_cell("TOP")
        s.sref("C2_1206", (10000, 10000))

        win._refresh()
        assert "C2_1206" in _rows(win)
    finally:
        app.destroy()


def test_open_component_library_window_reuses_the_singleton(monkeypatch, tmp_path):
    from hlayout.gui.component_library_window import open_component_library_window

    app = _make_app(monkeypatch, tmp_path)
    try:
        win1 = open_component_library_window(app)
        win2 = open_component_library_window(app)
        assert win1 is win2
    finally:
        app.destroy()
