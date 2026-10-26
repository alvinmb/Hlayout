"""Saving keeps the versions it overwrites: NAME.bak1.gds (newest) ..
NAME.bak3.gds, each with its sidecar."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.io.sidecar import backup_path, design_is_empty, load_design, sidecar_path
from hlayout.scripting import Session


def _design(n):
    s = Session.new("BAK")
    s.new_cell("TOP")
    for k in range(n):
        s.rect(3, k * 1000, 0, k * 1000 + 500, 500)
    return s


def _count(path):
    return len(load_design(path).library.cells["TOP"].shapes)


def test_each_save_keeps_the_version_it_overwrites(tmp_path):
    p = str(tmp_path / "TOP.gds")
    for n in (1, 2, 3, 4, 5):
        _design(n).save_design(p)
    assert _count(p) == 5
    assert [_count(backup_path(p, k)) for k in (1, 2, 3)] == [4, 3, 2]
    assert not os.path.exists(backup_path(p, 4))                 # only three kept
    assert os.path.exists(sidecar_path(backup_path(p, 1)))
    assert backup_path(p, 1).endswith("TOP.bak1.gds")


def test_first_save_makes_no_backup(tmp_path):
    p = str(tmp_path / "NEW.gds")
    _design(1).save_design(p)
    assert not os.path.exists(backup_path(p, 1))


def test_an_empty_design_is_recognised():
    assert design_is_empty(Session.new("E").library)
    assert not design_is_empty(_design(1).library)


def test_auto_save_never_puts_an_empty_design_over_a_real_one(tmp_path):
    import pytest
    tk = pytest.importorskip("tkinter")
    from hlayout.gui.app import App
    from hlayout.core import Library
    p = str(tmp_path / "TOP.gds")
    _design(5).save_design(p)
    try:
        app = App(Library("EMPTY"))
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    try:
        app.current_path = p
        app._run_autosave()
        assert _count(p) == 5 and "skipped" in app.status.get()
        app._cancel_autosave()
    finally:
        app.destroy()
