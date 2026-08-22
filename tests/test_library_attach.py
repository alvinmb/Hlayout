import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core.errors import LibraryError
from hlayout.scripting import Session
from hlayout.gui.commands import CommandInterpreter, CommandError
from hlayout.scripting.console import ScriptConsole


def _make_part_library(path, cell_name="PAD"):
    """A small standalone library with one part cell, for attach tests."""
    s = Session.new("PARTLIB")
    s.new_cell(cell_name)
    s.rect(0, 0, 0, 10, 10)
    s.new_cell("TOP")
    s.sref(cell_name, (0, 0))
    s.save(path)
    return path


def test_attach_library_limit_is_two(tmp_path):
    lib1 = _make_part_library(str(tmp_path / "lib1.gds"), "PAD1")
    lib2 = _make_part_library(str(tmp_path / "lib2.gds"), "PAD2")
    lib3 = _make_part_library(str(tmp_path / "lib3.gds"), "PAD3")

    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.attach_library(lib1)
    s.attach_library(lib2)
    assert len(s.attached_libraries) == 2
    with pytest.raises(LibraryError):
        s.attach_library(lib3)


def test_list_attached_libraries_and_list_library(tmp_path):
    lib1 = _make_part_library(str(tmp_path / "lib1.gds"), "PAD1")
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.attach_library(lib1)

    libs = s.list_attached_libraries()
    assert len(libs) == 1
    assert libs[0]["index"] == 0
    assert libs[0]["cells"] == 2  # PAD1 + TOP

    cells = s.list_library(0)
    names = {c["name"] for c in cells}
    assert names == {"PAD1", "TOP"}

    current_cells = s.list_library("current")
    assert [c["name"] for c in current_cells] == ["TOP"]


def test_find_cells_across_current_and_attached(tmp_path):
    lib1 = _make_part_library(str(tmp_path / "lib1.gds"), "PAD_SMALL")
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.new_cell("PAD_LOCAL")
    s.attach_library(lib1)

    matches = s.find_cells("pad")
    names_sources = {(m["name"], m["source"]) for m in matches}
    assert ("PAD_LOCAL", "current") in names_sources
    assert ("PAD_SMALL", 0) in names_sources


def test_place_cell_from_attached_library_imports_closure(tmp_path):
    lib1 = _make_part_library(str(tmp_path / "lib1.gds"), "PAD")
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.attach_library(lib1)

    # PAD exists only in the attached library -> auto-resolves.
    ref = s.place_cell("PAD", (100, 100))
    assert ref.cell_name == "PAD"
    assert "PAD" in s.library.cells
    assert len(s.library.cells["PAD"].shapes) == 1


def test_place_cell_name_collision_gets_renamed(tmp_path):
    lib1 = _make_part_library(str(tmp_path / "lib1.gds"), "PAD")
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    # A different, pre-existing local cell also named "PAD".
    s.new_cell("PAD")
    s.rect(0, 0, 0, 1, 1)
    s.use_cell("TOP")
    s.attach_library(lib1)

    ref = s.place_cell("PAD", (0, 0), source=0)
    assert ref.cell_name == "PAD_1"
    # The pre-existing local PAD cell is untouched by the import.
    assert s.library.cells["PAD"].shapes[0].bbox() == (0, 0, 1, 1)
    assert s.library.cells["PAD_1"].shapes[0].bbox() == (0, 0, 10, 10)


def test_place_cell_ambiguous_across_attached_libraries_requires_source(tmp_path):
    lib1 = _make_part_library(str(tmp_path / "lib1.gds"), "SAME")
    lib2 = _make_part_library(str(tmp_path / "lib2.gds"), "SAME")
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.attach_library(lib1)
    s.attach_library(lib2)

    with pytest.raises(LibraryError):
        s.place_cell("SAME", (0, 0))

    # Disambiguated with source= it works.
    ref = s.place_cell("SAME", (0, 0), source=1)
    assert ref.cell_name == "SAME"


def test_save_as_named_library_and_reattach(tmp_path):
    s = Session.new("DESIGN")
    s.new_cell("PART")
    s.rect(0, 0, 0, 5, 5)
    out_path = str(tmp_path / "mylib.gds")
    s.save(out_path, library_name="MY-PARTS")
    assert s.library.name == "MY-PARTS"

    s2 = Session.new("OTHER")
    s2.new_cell("TOP")
    lib = s2.attach_library(out_path)
    assert lib.name == "MY-PARTS"
    assert "PART" in lib.cells


def test_command_interpreter_attach_liblist_find_place(tmp_path):
    lib1 = _make_part_library(str(tmp_path / "lib1.gds"), "RES100")
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    out = interp.run(f"ATTACH {lib1}")
    assert "attached library 0" in out

    out = interp.run("LIBS")
    assert "[0]" in out

    out = interp.run("LIBLIST 0")
    assert "RES100" in out

    out = interp.run("FIND res")
    assert "RES100" in out

    out = interp.run("PLACE RES100 10,10 source=0")
    assert "placed" in out
    assert "RES100" in s.library.cells


def test_command_interpreter_save_with_library_name(tmp_path):
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)
    out_path = str(tmp_path / "saved.gds")
    out = interp.run(f"SAVE {out_path} MYLIB")
    assert "MYLIB" in out
    assert s.library.name == "MYLIB"


class _FakeView:
    """Stands in for LayoutCanvas — just needs to record fit_view() calls."""

    def __init__(self):
        self.fit_calls = 0
        self.show_grid = True

    def fit_view(self):
        self.fit_calls += 1


def test_place_command_fits_view_so_placement_is_visible(tmp_path):
    lib1 = _make_part_library(str(tmp_path / "lib1.gds"), "RES100")
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    view = _FakeView()
    interp = CommandInterpreter(s, console, view=view)

    interp.run(f"ATTACH {lib1}")
    interp.run("PLACE RES100 500,500 source=0")

    assert view.fit_calls == 1
