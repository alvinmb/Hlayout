# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""drop_redundant_tracks(): after remove_jogs slides a leg onto a track of
the same net, the doubled-up copy and its repeated points are removed."""
from hlayout.core import Library
from hlayout.scripting.api import Session
from hlayout.scripting.hybridroute import drop_redundant_tracks


def _cell():
    lib = Library("T")
    s = Session(lib)
    s.new_cell("TOP")
    return s, lib.cells["TOP"]


def test_a_track_lying_on_another_is_removed_and_points_collapsed():
    s, cell = _cell()
    keep = cell.add_path(3, [(22650, -15050), (33250, -15050), (33250, -16750)], width=300)
    # the U whose top leg was pulled down onto the track above
    copy = cell.add_path(3, [(33250, -15050), (33250, -15050), (23850, -15050),
                             (23850, -15050), (22650, -15050)], width=300)
    r = drop_redundant_tracks(s, "TOP")
    assert copy not in cell.shapes and keep in cell.shapes
    assert len(r["removed"]) == 1


def test_a_track_only_partly_on_another_stays():
    s, cell = _cell()
    a = cell.add_path(3, [(0, 0), (10000, 0)], width=300)
    b = cell.add_path(3, [(5000, 0), (15000, 0)], width=300)        # sticks out past a
    c = cell.add_path(5, [(0, 0), (10000, 0)], width=300)           # other layer
    d = cell.add_path(3, [(0, 2000), (0, 2000), (4000, 2000)], width=300)
    r = drop_redundant_tracks(s, "TOP")
    assert all(x in cell.shapes for x in (a, b, c, d))
    assert d.points == [(0, 2000), (4000, 2000)] and r["points"] == 1


def test_two_identical_copies_keep_one():
    s, cell = _cell()
    cell.add_path(3, [(0, 0), (5000, 0)], width=300)
    cell.add_path(3, [(0, 0), (5000, 0)], width=300)
    drop_redundant_tracks(s, "TOP")
    assert len([x for x in cell.shapes if x.layer == 3]) == 1


def test_a_squeezed_shut_u_is_collapsed():
    from hlayout.scripting.hybridroute import remove_hairpins
    s, cell = _cell()
    # the +V hairpin: up x=40.95, 0.3 mm jog, back down x=40.65 (two tracks)
    cell.add_path(3, [(40950, -18750), (40950, -9350), (40650, -9350)], width=300)
    cell.add_path(3, [(40650, -9350), (40650, -16950), (38550, -16950)], width=300)
    r = remove_hairpins(s, "TOP")
    paths = [x for x in cell.shapes if x.layer == 3]
    assert r["hairpins"] == 1 and len(paths) == 1
    assert paths[0].points == [(40950, -18750), (40950, -16950), (38550, -16950)]


def test_a_hairpin_with_a_via_on_the_loop_stays():
    from hlayout.scripting.hybridroute import remove_hairpins
    s, cell = _cell()
    cell.add_path(3, [(0, 0), (0, 10000), (300, 10000), (300, 2000)], width=300)
    cell.add_rectangle(3, -150, 9850, 450, 10150)          # a via pad at the apex
    assert remove_hairpins(s, "TOP")["hairpins"] == 0


def test_a_real_u_with_a_gap_stays():
    from hlayout.scripting.hybridroute import remove_hairpins
    s, cell = _cell()
    cell.add_path(3, [(0, 0), (0, 10000), (1000, 10000), (1000, 2000)], width=300)
    assert remove_hairpins(s, "TOP")["hairpins"] == 0
