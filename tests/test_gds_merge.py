# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""save_gds_merged(): part generators run one after another into the
same file add to it instead of wiping the earlier generators' cells."""
from hlayout.core.library import Library
from hlayout.io.gdsio import load_gds, save_gds_merged


def _lib(name, parts):
    lib = Library(name)
    for p in parts:
        lib.new_cell(p).add_rectangle(3, 0, 0, 100, 100, pin="1")
    index = lib.new_cell("INDEX")
    for i, p in enumerate(parts):
        index.add_sref(p, (i * 1000, 0))
    return lib


def test_second_generator_keeps_the_first_ones_cells(tmp_path):
    out = str(tmp_path / "SOLDER.gds")
    assert save_gds_merged(_lib("CAPS", ["C1", "C2"]), out)["mode"] == "new"
    res = save_gds_merged(_lib("SOT", ["SOT-23"]), out)
    assert res["mode"] == "merged"
    cells = set(load_gds(out).cells)
    assert {"C1", "C2", "SOT-23", "INDEX", "INDEX_SOT"} <= cells


def test_rerun_updates_its_own_cells_without_duplicating_the_index(tmp_path):
    out = str(tmp_path / "SOLDER.gds")
    save_gds_merged(_lib("CAPS", ["C1"]), out)
    save_gds_merged(_lib("SOT", ["SOT-23"]), out)
    save_gds_merged(_lib("CAPS", ["C1"]), out)
    res = save_gds_merged(_lib("SOT", ["SOT-23"]), out)
    assert res["replaced"] == ["SOT-23", "INDEX_SOT"]
    assert sorted(load_gds(out).cells) == ["C1", "INDEX", "INDEX_SOT", "SOT-23"]


def test_overwrite_starts_a_fresh_file(tmp_path):
    out = str(tmp_path / "SOLDER.gds")
    save_gds_merged(_lib("CAPS", ["C1"]), out)
    save_gds_merged(_lib("SOT", ["SOT-23"]), out, overwrite=True)
    assert sorted(load_gds(out).cells) == ["INDEX", "SOT-23"]
