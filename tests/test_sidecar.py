import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.units import mm_to_dbu
from hlayout.gui.commands import CommandInterpreter
from hlayout.io.sidecar import load_design, sidecar_path
from hlayout.io.techfile import load_resistor_design_rules, save_resistor_design_rules
from hlayout.scripting import Session
from hlayout.scripting.console import ScriptConsole


def _make_part_library(path, cell_name="PAD"):
    s = Session.new("PARTLIB")
    s.new_cell(cell_name)
    s.rect(0, 0, 0, 10, 10)
    s.save(str(path))
    return str(path)


def test_save_design_writes_a_sidecar_next_to_the_gds(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.rect(0, 0, 0, 10, 10)
    path = str(tmp_path / "design.gds")

    s.save_design(path)

    assert os.path.exists(path)
    assert os.path.exists(sidecar_path(path))


def test_open_design_restores_technology_layer_stack(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.library.layers.set_style(5, name="VIA1", color="#00ff00", visible=False)
    path = str(tmp_path / "design.gds")
    s.save_design(path)

    s2 = Session.open_design(path)

    assert s2.library.layers[5].name == "VIA1"
    assert s2.library.layers[5].color == "#00ff00"
    assert s2.library.layers[5].visible is False


def test_open_design_restores_resistor_rules_end_to_end(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    save_resistor_design_rules(s.library, {
        "aim_pct": 60.0, "ar_min": 0.5, "ar_max": 2.0,
        "available_inks": [10, 100, 1000],
    })
    path = str(tmp_path / "design.gds")
    s.save_design(path)

    s2 = Session.open_design(path)
    rules = load_resistor_design_rules(s2.library)
    assert rules["aim_pct"] == 60.0
    assert rules["available_inks"] == [10, 100, 1000]


def test_open_design_restores_attached_libraries_even_if_source_moved(tmp_path):
    part_path = _make_part_library(tmp_path / "part.gds")
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.attach_library(part_path)
    design_path = str(tmp_path / "design.gds")
    s.save_design(design_path)

    # Simulate the original attached library no longer being reachable --
    # the sidecar embeds a full copy, so this must not matter.
    os.remove(part_path)

    s2 = Session.open_design(design_path)

    assert not s2.attach_errors
    assert len(s2.attached_libraries) == 1
    assert "PAD" in s2.attached_libraries[0].cells


def test_open_design_picks_up_edits_to_the_live_attached_library_file(tmp_path):
    # The sidecar embeds a copy, but if the source .gds is still there
    # and has been edited since (e.g. the Chip Wizard wrote a new part
    # into it), the open should reflect the current file, not the stale
    # embedded snapshot.
    part_path = _make_part_library(tmp_path / "part.gds")
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.attach_library(part_path)
    design_path = str(tmp_path / "design.gds")
    s.save_design(design_path)

    # edit the attached library's file on disk, without touching the design
    plib = Session.open(part_path)
    plib.new_cell("NEWPART")
    plib.rect(0, 0, 0, 20, 20)
    plib.save(part_path)

    s2 = Session.open_design(design_path)
    assert not s2.attach_errors
    assert "NEWPART" in s2.attached_libraries[0].cells
    assert "PAD" in s2.attached_libraries[0].cells


def test_open_design_with_no_sidecar_behaves_like_plain_open(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.rect(0, 0, 0, 10, 10)
    path = str(tmp_path / "bare.gds")
    s.save(path)  # plain save() -- no sidecar written

    s2 = Session.open_design(path)

    assert s2.had_sidecar is False
    assert s2.attached_libraries == []
    assert len(s2.library.cells["TOP"].shapes) == 1


def test_gerber_style_gerber_untouched_by_sidecar_addition(tmp_path):
    # Sanity check that adding sidecar support didn't disturb the
    # separate Gerber export path.
    from hlayout.io import write_gerber_layers
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.rect(0, 0, 0, mm_to_dbu(1), mm_to_dbu(1))
    written, _ = write_gerber_layers(s.library, "TOP", str(tmp_path / "gb"))
    assert len(written) == 1


def test_save_command_writes_sidecar_when_no_library_name_given(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.library.layers.set_style(2, name="METAL2")
    interp = CommandInterpreter(s, ScriptConsole(s))
    path = str(tmp_path / "cmd_design.gds")

    out = interp.run(f"SAVE {path}")

    assert "sidecar" in out
    assert os.path.exists(sidecar_path(path))


def test_save_command_with_library_name_does_not_write_sidecar(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    interp = CommandInterpreter(s, ScriptConsole(s))
    path = str(tmp_path / "cmd_lib.gds")

    out = interp.run(f"SAVE {path} PARTLIB")

    assert "as library" in out
    assert not os.path.exists(sidecar_path(path))


def test_open_command_restores_technology_and_attached_libraries(tmp_path):
    part_path = _make_part_library(tmp_path / "part2.gds")
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.attach_library(part_path)
    s.library.layers.set_style(3, name="OXIDE")
    design_path = str(tmp_path / "cmd_open_design.gds")
    s.save_design(design_path)

    s2 = Session.new("OTHER")
    s2.new_cell("TOP")
    interp = CommandInterpreter(s2, ScriptConsole(s2))
    out = interp.run(f"OPEN {design_path}")

    assert "restored technology" in out
    assert s2.library.layers[3].name == "OXIDE"
    assert len(s2.attached_libraries) == 1


def test_load_design_missing_file_raises_clean_error():
    import pytest
    from hlayout.core.errors import LayoutError
    with pytest.raises(LayoutError):
        load_design("/nonexistent/path/does_not_exist.gds")


def _make_app():
    import pytest as _pytest
    import tkinter as _tk
    _pytest.importorskip("tkinter")
    from hlayout.core.library import Library
    from hlayout.gui.app import App
    lib = Library("APPTEST")
    lib.new_cell("TOP")
    try:
        app = App(lib)
    except _tk.TclError as e:
        _pytest.skip(f"no display available: {e}")
    app.update()
    return app


def test_app_save_gds_then_open_gds_round_trips_technology_and_attachments(tmp_path, monkeypatch):
    part_path = _make_part_library(tmp_path / "apppart.gds")
    app = _make_app()
    try:
        app.session.rect(0, 0, 0, 10, 10)
        app.session.attach_library(part_path)
        app.session.library.layers.set_style(7, name="PASSIVATION", color="#123456")
        design_path = str(tmp_path / "app_design.gds")

        from hlayout.gui import app as app_module
        monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kw: design_path)
        app.save_gds()
        assert os.path.exists(sidecar_path(design_path))

        # Open a blank document, then reopen the saved design and confirm
        # everything comes back -- technology, attached library, geometry.
        app.new_library()
        assert app.session.attached_libraries == []
        monkeypatch.setattr(app_module.filedialog, "askopenfilename", lambda **kw: design_path)
        app.open_gds()

        assert app.session.library.layers[7].name == "PASSIVATION"
        assert len(app.session.attached_libraries) == 1
        assert "PAD" in app.session.attached_libraries[0].cells
    finally:
        app.destroy()


def test_save_as_gds_moves_the_open_design_to_a_new_location(tmp_path, monkeypatch):
    app = _make_app()
    try:
        app.session.rect(0, 0, 0, 10, 10)
        first_path = str(tmp_path / "first" / "design.gds")
        os.makedirs(os.path.dirname(first_path))

        from hlayout.gui import app as app_module
        monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kw: first_path)
        app.save_gds()
        assert app.current_path == first_path
        assert os.path.exists(first_path)

        # Save As... prompts again even though current_path is already
        # set (Save GDS... would silently reuse it) and moves the design
        # forward to the new location -- pre-filled from the old one.
        captured_kwargs = {}
        second_path = str(tmp_path / "second" / "design_v2.gds")
        os.makedirs(os.path.dirname(second_path))

        def fake_save_as(**kw):
            captured_kwargs.update(kw)
            return second_path

        monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", fake_save_as)
        app.save_as_gds()

        assert captured_kwargs["initialdir"] == os.path.dirname(first_path)
        assert captured_kwargs["initialfile"] == os.path.basename(first_path)
        assert app.current_path == second_path
        assert os.path.exists(second_path)

        # Ctrl+S / Save GDS... from here on writes the NEW location, not
        # the old one -- and the old file is left untouched, not deleted.
        app.session.rect(0, 0, 0, 20, 20)
        monkeypatch.setattr(app_module.filedialog, "asksaveasfilename",
                             lambda **kw: (_ for _ in ()).throw(AssertionError("should not prompt")))
        app.save_gds()
        assert app.current_path == second_path
        assert os.path.exists(first_path)
    finally:
        app.destroy()


def test_save_as_gds_cancel_leaves_current_path_untouched(tmp_path, monkeypatch):
    app = _make_app()
    try:
        design_path = str(tmp_path / "design.gds")
        from hlayout.gui import app as app_module
        monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kw: design_path)
        app.save_gds()
        assert app.current_path == design_path

        monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kw: "")
        app.save_as_gds()
        assert app.current_path == design_path
    finally:
        app.destroy()


def test_archive_design_zips_gds_sidecar_and_attached_library(tmp_path, monkeypatch):
    import zipfile

    os.makedirs(tmp_path / "sub")
    part_path = _make_part_library(tmp_path / "sub" / "apart.gds")
    app = _make_app()
    try:
        app.session.rect(0, 0, 0, 10, 10)
        app.session.attach_library(part_path)
        design_path = str(tmp_path / "design.gds")

        from hlayout.gui import app as app_module
        monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kw: design_path)
        app.save_gds()
        assert os.path.exists(sidecar_path(design_path))

        zip_path = str(tmp_path / "out" / "bundle.zip")
        os.makedirs(os.path.dirname(zip_path))
        captured = {}

        def fake_zip_dialog(**kw):
            captured.update(kw)
            return zip_path

        monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", fake_zip_dialog)
        app.archive_design()

        assert captured["initialdir"] == os.path.dirname(design_path)
        assert captured["initialfile"] == "design_archive.zip"
        assert os.path.exists(zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        assert names == {"design.gds", "design.gds.hlaux", "apart.gds"}
    finally:
        app.destroy()


def test_archive_design_dedupes_same_basename_from_different_folders(tmp_path, monkeypatch):
    import zipfile

    os.makedirs(tmp_path / "a")
    os.makedirs(tmp_path / "b")
    part_a = _make_part_library(tmp_path / "a" / "part.gds", cell_name="PADA")
    part_b = _make_part_library(tmp_path / "b" / "part.gds", cell_name="PADB")
    app = _make_app()
    try:
        app.session.attach_library(part_a)
        app.session.attach_library(part_b)
        design_path = str(tmp_path / "design.gds")

        from hlayout.gui import app as app_module
        monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kw: design_path)
        app.save_gds()

        zip_path = str(tmp_path / "bundle.zip")
        monkeypatch.setattr(app_module.filedialog, "asksaveasfilename", lambda **kw: zip_path)
        app.archive_design()

        with zipfile.ZipFile(zip_path) as zf:
            names = set(zf.namelist())
        assert names == {"design.gds", "design.gds.hlaux", "part.gds", "part_2.gds"}
    finally:
        app.destroy()


def test_archive_design_refuses_when_the_design_has_never_been_saved(monkeypatch):
    app = _make_app()
    try:
        from hlayout.gui import app as app_module
        warned = []
        monkeypatch.setattr(app_module.dialogs, "show_warning",
                             lambda *a, **kw: warned.append((a, kw)))
        called = []
        monkeypatch.setattr(app_module.filedialog, "asksaveasfilename",
                             lambda **kw: called.append(1) or "")
        app.archive_design()
        assert called == []           # never even got to the zip-location prompt
        assert len(warned) == 1
    finally:
        app.destroy()


# --- Auto-save (top toolbar switch) -----------------------------------

def test_autosave_toggle_schedules_and_cancels_the_timer():
    app = _make_app()
    try:
        assert app.autosave_var.get() is False
        assert getattr(app, "_autosave_job", None) is None

        app.autosave_var.set(True)
        app._on_autosave_toggle()
        assert app._autosave_job is not None      # a self.after() timer is now pending

        app.autosave_var.set(False)
        app._on_autosave_toggle()
        assert app._autosave_job is None          # cancelled, nothing left pending
    finally:
        app.destroy()


def test_autosave_tick_writes_to_current_path_and_reschedules(tmp_path):
    app = _make_app()
    try:
        app.session.rect(0, 0, 0, 100, 100)
        design_path = str(tmp_path / "design.gds")
        app.current_path = design_path
        app.autosave_var.set(True)

        app._run_autosave()

        assert os.path.exists(design_path)
        assert os.path.exists(sidecar_path(design_path))   # same write as Save GDS...
        assert "auto-saved" in app.status.get()
        assert app._autosave_job is not None               # still armed -- ticks again
    finally:
        app._cancel_autosave()
        app.destroy()


def test_autosave_tick_skips_but_stays_armed_when_never_saved():
    app = _make_app()
    try:
        assert app.current_path is None
        app.autosave_var.set(True)

        app._run_autosave()

        assert "skipped" in app.status.get()
        assert app._autosave_job is not None   # doesn't disarm the switch, just skips this tick
    finally:
        app._cancel_autosave()
        app.destroy()


def test_autosave_does_not_fire_once_switched_off(tmp_path):
    app = _make_app()
    try:
        app.current_path = str(tmp_path / "design.gds")
        app.autosave_var.set(True)
        app._on_autosave_toggle()
        job = app._autosave_job
        assert job is not None

        app.autosave_var.set(False)
        app._on_autosave_toggle()
        # Cancelling the pending after() job means _run_autosave never
        # fires for it -- nothing left to assert on the job itself
        # (after_cancel doesn't leave a queryable "cancelled" state),
        # so the real assertion is just that a fresh toggle-off always
        # leaves _autosave_job cleared, checked in the timer test above.
        assert app._autosave_job is None
    finally:
        app.destroy()


def test_quitting_cancels_a_pending_autosave():
    app = _make_app()
    app.autosave_var.set(True)
    app._on_autosave_toggle()
    assert app._autosave_job is not None
    # _on_quit() destroys app itself; this just proves it doesn't raise
    # trying to cancel the pending timer on the way out.
    app._on_quit()


def test_app_new_library_clears_attached_libraries(tmp_path):
    part_path = _make_part_library(tmp_path / "clearpart.gds")
    app = _make_app()
    try:
        app.session.attach_library(part_path)
        assert len(app.session.attached_libraries) == 1
        app.new_library()
        assert app.session.attached_libraries == []
        assert app.session._attached_paths == []
    finally:
        app.destroy()
