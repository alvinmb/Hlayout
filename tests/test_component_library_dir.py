import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")


def _make_app(monkeypatch, tmp_path):
    """A fresh App with its own isolated app_settings.json (never the
    real ~/.hlayout one) -- component_library_dir()/
    change_component_library_dir() call app_settings functions with no
    explicit path, so this is the only way to keep a test from writing
    to the developer's real machine-wide settings file."""
    import hlayout.io.app_settings as app_settings
    from hlayout.gui.app import App
    from hlayout.core import Library
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", str(tmp_path / "settings.json"))
    try:
        lib = Library("COMPLIBDIR")
        lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    return app


def test_component_library_dir_none_and_no_prompt_when_ask_if_unset_false(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    try:
        from tkinter import filedialog
        monkeypatch.setattr(filedialog, "askdirectory",
                            lambda **kw: (_ for _ in ()).throw(AssertionError("must not prompt")))
        assert app.component_library_dir(ask_if_unset=False) is None
    finally:
        app.destroy()


def test_component_library_dir_prompts_once_and_remembers(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    try:
        from tkinter import filedialog
        chosen = str(tmp_path / "shared_parts")
        calls = []
        monkeypatch.setattr(filedialog, "askdirectory",
                            lambda **kw: (calls.append(1) or chosen))

        first = app.component_library_dir()
        assert first == chosen
        assert os.path.isdir(chosen)   # created

        # second call must NOT prompt again -- it's remembered now
        second = app.component_library_dir()
        assert second == chosen
        assert len(calls) == 1
    finally:
        app.destroy()


def test_component_library_dir_cancel_returns_none(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    try:
        from tkinter import filedialog
        monkeypatch.setattr(filedialog, "askdirectory", lambda **kw: "")
        assert app.component_library_dir() is None
    finally:
        app.destroy()


def test_change_component_library_dir_updates_the_remembered_folder(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    try:
        from tkinter import filedialog
        first = str(tmp_path / "first")
        second = str(tmp_path / "second")
        monkeypatch.setattr(filedialog, "askdirectory", lambda **kw: first)
        assert app.component_library_dir() == first

        monkeypatch.setattr(filedialog, "askdirectory", lambda **kw: second)
        app.change_component_library_dir()
        assert app.component_library_dir(ask_if_unset=False) == second
    finally:
        app.destroy()


def test_build_library_and_match_work_across_different_design_folders(monkeypatch, tmp_path):
    """The scenario this feature exists for: build the STEP library once
    while one design is open, then get a match from a COMPLETELY
    different design's own folder -- proving the library isn't tied to
    whichever design happens to be open."""
    pytest.importorskip("shapely")
    from hlayout.io.techfile import save_layer_thickness

    app = _make_app(monkeypatch, tmp_path)
    try:
        from tkinter import filedialog
        shared_dir = str(tmp_path / "shared_lib")
        monkeypatch.setattr(filedialog, "askdirectory", lambda **kw: shared_dir)

        folder_a = tmp_path / "designA"
        folder_a.mkdir()
        app.current_path = str(folder_a / "designA.gds")
        app.build_component_library_macro()
        assert os.path.isfile(os.path.join(shared_dir, "build_component_library.FCMacro"))

        # pretend the user ran the macro in FreeCAD
        open(os.path.join(shared_dir, "CAP_0805.step"), "w").close()

        folder_b = tmp_path / "designB"
        folder_b.mkdir()
        app.current_path = str(folder_b / "designB.gds")
        app.session.new_cell("C1_0805")
        app.session.use_cell("C1_0805")
        app.session.rect(0, -400, -625, 400, 625)
        app.session.path(61, [(-1000, -625), (1000, -625)], width=100)
        app.session.use_cell("TOP")
        app.session.sref("C1_0805", (0, 0))
        save_layer_thickness(app.session.library, {"0": 10})

        report = app.session.match_freecad_components(
            step_dir=app.component_library_dir(ask_if_unset=False))
        assert [m.package for m in report.matches] == ["CAP_0805"]
    finally:
        app.destroy()
