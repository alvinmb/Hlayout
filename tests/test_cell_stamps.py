"""Cell date/time stamps: stored in each GDSII BGNSTR, kept through
attached-library placement, moved on save only when a cell changed."""

import time
from datetime import datetime

from hlayout.core.library import Library
from hlayout.io.gdsio import load_gds, read_cell_stamps, save_gds
from hlayout.scripting.api import Session


def _lib():
    lib = Library("T")
    top = lib.new_cell("TOP")
    pad = lib.new_cell("PAD")
    pad.add_rectangle(3, 0, 0, 100, 100)
    top.add_sref("PAD", (0, 0))
    return lib


def test_new_cells_are_stamped_on_save(tmp_path):
    path = tmp_path / "a.gds"
    save_gds(_lib(), str(path))
    stamps = read_cell_stamps(str(path))
    assert set(stamps) == {"TOP", "PAD"}
    for created, modified in stamps.values():
        assert isinstance(created, datetime) and created == modified


def test_round_trip_and_modified_moves_only_for_changed_cell(tmp_path):
    a, b = tmp_path / "a.gds", tmp_path / "b.gds"
    save_gds(_lib(), str(a))
    lib = load_gds(str(a))
    before = {n: (c.created, c.modified) for n, c in lib.cells.items()}
    assert before == read_cell_stamps(str(a))
    time.sleep(1.1)
    lib.cells["PAD"].add_rectangle(3, 200, 0, 300, 100)
    save_gds(lib, str(b))
    after = read_cell_stamps(str(b))
    assert after["TOP"] == before["TOP"]
    assert after["PAD"][0] == before["PAD"][0]
    assert after["PAD"][1] > before["PAD"][1]


def test_unchanged_resave_keeps_stamps(tmp_path):
    a, b = tmp_path / "a.gds", tmp_path / "b.gds"
    save_gds(_lib(), str(a))
    time.sleep(1.1)
    save_gds(load_gds(str(a)), str(b))
    assert read_cell_stamps(str(a)) == read_cell_stamps(str(b))


def test_placing_from_attached_library_keeps_library_stamps(tmp_path):
    libpath = tmp_path / "parts.gds"
    save_gds(_lib(), str(libpath))
    lib_stamps = read_cell_stamps(str(libpath))
    time.sleep(1.1)
    design_lib = Library("D")
    design_lib.new_cell("MAIN")
    s = Session(design_lib)
    s.attach_library(str(libpath))
    s.place_cell("TOP", (0, 0), source=0)
    out = tmp_path / "design.gds"
    s.save_design(str(out))
    design = read_cell_stamps(str(out))
    assert design["TOP"] == lib_stamps["TOP"]
    assert design["PAD"] == lib_stamps["PAD"]
    rows = {(r["source"], r["name"]): r for r in s.cell_stamps()}
    assert rows[(0, "PAD")]["created"] == lib_stamps["PAD"][0].isoformat()
    assert rows[("current", "PAD")]["modified"] == lib_stamps["PAD"][1].isoformat()
