# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core.errors import GeometryError, LayoutError
from hlayout.core.library import Library
from hlayout.core.thinfilm import (
    CORNER_SQUARE_FACTOR,
    SerpentineSolution,
    TopHatSolution,
    check_thin_film_rules,
    generate_serpentine_geometry,
    generate_top_hat_geometry,
    solve_serpentine,
    solve_top_hat,
)
from hlayout.io.techfile import DEFAULT_THIN_FILM, load_thin_film_rules, save_thin_film_rules
from hlayout.scripting import Session


# --- solve_serpentine / generate_serpentine_geometry ------------------------

def test_corner_square_factor_is_half():
    # The user's explicit call: a meander corner contributes half a
    # square, not some other fudge factor -- this is a hard requirement,
    # not a tunable default that can silently drift.
    assert CORNER_SQUARE_FACTOR == 0.5


def test_solve_serpentine_returns_candidates_matching_target():
    sols = solve_serpentine(target_r_ohm=10000.0, sheet_rho_ohm_per_sq=100.0, trace_width=50)
    assert sols
    assert sols[0].recommended
    assert all(not s.recommended for s in sols[1:])
    for s in sols:
        assert s.resistance_ohm == pytest.approx(s.squares * 100.0)
        # total squares (serpentine + link) should reproduce the target
        # resistance to within rounding of whole legs.
        assert s.resistance_ohm == pytest.approx(10000.0, rel=0.05)


def test_solve_serpentine_rejects_bad_input():
    with pytest.raises(GeometryError):
        solve_serpentine(target_r_ohm=0.0, sheet_rho_ohm_per_sq=100.0, trace_width=50)
    with pytest.raises(GeometryError):
        solve_serpentine(target_r_ohm=10000.0, sheet_rho_ohm_per_sq=-1.0, trace_width=50)
    with pytest.raises(GeometryError):
        solve_serpentine(target_r_ohm=10000.0, sheet_rho_ohm_per_sq=100.0, trace_width=0)


def test_generate_serpentine_geometry_footprint_bounds_actual_polys():
    for target_r, rho, trace_width in (
        (10000.0, 100.0, 50), (500.0, 50.0, 100), (2000.0, 250.0, 25),
    ):
        for sol in solve_serpentine(target_r_ohm=target_r, sheet_rho_ohm_per_sq=rho, trace_width=trace_width):
            geo = generate_serpentine_geometry(sol)
            xs, ys = [], []
            for key in ("link_polys", "serpentine_polys"):
                for poly in geo[key]:
                    xs += [p[0] for p in poly]
                    ys += [p[1] for p in poly]
            for p in geo["bridge_poly"]:
                xs.append(p[0])
                ys.append(p[1])
            bbox_w, bbox_l = max(xs) - min(xs), max(ys) - min(ys)
            # the claimed footprint must never underclaim the actual
            # geometry (auto-placement spacing relies on this)
            assert sol.footprint_w >= bbox_w - 1
            assert sol.footprint_l >= bbox_l - 1
            # every polygon point is an integer DBU coordinate pair
            for key in ("link_polys", "serpentine_polys"):
                for poly in geo[key]:
                    for pt in poly:
                        assert isinstance(pt[0], int) and isinstance(pt[1], int)


def test_generate_serpentine_geometry_single_corner_case():
    # num_legs == 2 is the edge case that originally broke the footprint
    # formula (end_margin canceled out for the single-corner geometry).
    sols = solve_serpentine(target_r_ohm=300.0, sheet_rho_ohm_per_sq=100.0, trace_width=100, max_legs=2)
    two_leg = [s for s in sols if s.num_legs == 2]
    assert two_leg
    geo = generate_serpentine_geometry(two_leg[0])
    assert geo["serpentine_polys"]


# --- solve_top_hat / generate_top_hat_geometry -------------------------------

def test_solve_top_hat_matches_target_resistance():
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100)
    assert sol.resistance_ohm == pytest.approx(5000.0)
    assert sol.bar_length == pytest.approx(sol.squares * sol.bar_width)
    assert sol.pad_width == 4 * sol.bar_width
    assert sol.pad_length == 2 * sol.bar_width


def test_solve_top_hat_rejects_bad_input():
    with pytest.raises(GeometryError):
        solve_top_hat(target_r_ohm=0.0, sheet_rho_ohm_per_sq=100.0, bar_width=100)
    with pytest.raises(GeometryError):
        solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100, min_bar_length=1e9)


def test_generate_top_hat_geometry_is_three_rectangles():
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100)
    geo = generate_top_hat_geometry(sol)
    assert len(geo["pad_a_points"]) == 4
    assert len(geo["bar_points"]) == 4
    assert len(geo["pad_b_points"]) == 4


# --- top-hat crown (the actual hat shape -- wide brim + tall crown) ---------

def test_solve_top_hat_crown_defaults():
    # crown_width defaults to 2x bar_width (the standard top-hat
    # proportion -- the center "hat" is twice the width of the
    # terminal legs it rises from) and crown_height to 4x bar_width --
    # see solve_top_hat()'s docstring; 4x bar_width extends the old
    # bar-only trim runway 5x.
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100)
    assert sol.crown_width == 200
    assert sol.crown_height == 400


def test_solve_top_hat_crown_explicit_and_rejects_bad_input():
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100,
                         crown_width=60, crown_height=1000)
    assert sol.crown_width == 60
    assert sol.crown_height == 1000
    with pytest.raises(GeometryError):
        solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100, crown_width=0)
    with pytest.raises(GeometryError):
        solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100, crown_height=-1)


def test_generate_top_hat_geometry_crown_sits_on_bar_top_edge():
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100,
                         crown_width=60, crown_height=300)
    geo = generate_top_hat_geometry(sol)
    crown = geo["crown_points"]
    assert len(crown) == 4
    half_bar_w = sol.bar_width / 2
    # the crown's bottom edge sits exactly on the bar's top edge (they
    # share the resistor_layer, no overlap margin needed -- see the
    # module docstring), and its top edge is crown_height above that.
    assert min(p[1] for p in crown) == pytest.approx(half_bar_w, abs=1)
    assert max(p[1] for p in crown) == pytest.approx(half_bar_w + 300, abs=1)
    # narrower than the bar, and centered on it
    assert max(p[0] for p in crown) == pytest.approx(30, abs=1)
    assert min(p[0] for p in crown) == pytest.approx(-30, abs=1)


def test_generate_top_hat_geometry_zero_crown_height_omits_crown():
    # explicit crown_height=0 is the escape hatch back to the old
    # plain-bar shape -- generate_top_hat_geometry must not emit a
    # degenerate zero-height rectangle for it.
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100,
                         crown_height=0)
    geo = generate_top_hat_geometry(sol)
    assert geo["crown_points"] is None


def test_solve_top_hat_footprint_accounts_for_crown_height():
    # a tall crown makes the true footprint taller than the pads alone
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100,
                         pad_width=200, crown_height=1000)
    assert sol.footprint_w == pytest.approx(2 * (50 + 1000))  # 2 * (bar_width/2 + crown_height)
    # a short/no crown falls back to the pad width setting the footprint
    sol2 = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100,
                          pad_width=200, crown_height=0)
    assert sol2.footprint_w == pytest.approx(200)


def test_session_generate_top_hat_resistor_includes_crown_shape():
    s = _session()
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100)
    name = s.generate_top_hat_resistor(0, 0, sol, ref_des="R9")
    cell = s.library.cells[name]
    rules = load_thin_film_rules(s.library)
    r_layer = rules["resistor_layer"]
    r_shapes = [shp for shp in cell.shapes if shp.layer == r_layer]
    # bar + crown, both on the resistor layer, both 4-point rectangles
    assert len(r_shapes) == 2
    ys = [p[1] for shp in r_shapes for p in shp.points]
    # the crown reaches well above the bar's own half-width
    assert max(ys) > sol.bar_width / 2 + sol.crown_height / 2


# --- techfile persistence ----------------------------------------------------

def test_thin_film_rules_round_trip():
    lib = Library("TF")
    rules = load_thin_film_rules(lib)
    assert rules == DEFAULT_THIN_FILM
    assert rules["corner_factor"] == 0.5

    rules["sheet_rho"] = 250.0
    rules["corner_factor"] = 0.6
    save_thin_film_rules(lib, rules)

    reloaded = load_thin_film_rules(lib)
    assert reloaded["sheet_rho"] == 250.0
    assert reloaded["corner_factor"] == 0.6


# --- Session integration ------------------------------------------------------

def _session():
    lib = Library("TFSESSION")
    lib.new_cell("TOP")
    s = Session(lib)
    s.use_cell("TOP")
    return s


def test_generate_serpentine_resistor_places_sref_and_undoes():
    s = _session()
    sol = solve_serpentine(target_r_ohm=10000.0, sheet_rho_ohm_per_sq=100.0, trace_width=50)[0]
    before = len(s.cell.srefs)
    s.generate_serpentine_resistor(0, 0, sol, ref_des="R1")
    assert len(s.cell.srefs) == before + 1
    s.undo()
    assert len(s.cell.srefs) == before


def test_generate_top_hat_resistor_places_sref_and_undoes():
    s = _session()
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100)
    before = len(s.cell.srefs)
    s.generate_top_hat_resistor(0, 0, sol, ref_des="R2")
    assert len(s.cell.srefs) == before + 1
    s.undo()
    assert len(s.cell.srefs) == before


def test_generate_resistor_requires_current_cell():
    lib = Library("NOCELL")
    s = Session(lib)
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100)
    with pytest.raises(LayoutError):
        s.generate_top_hat_resistor(0, 0, sol, ref_des="R1")


# --- check_thin_film_rules (Figure 4/5 design rules) --------------------------

def test_check_thin_film_rules_clean_design_has_no_violations():
    rules = dict(DEFAULT_THIN_FILM)
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=200)
    assert check_thin_film_rules(sol, rules) == []
    sol2 = solve_serpentine(target_r_ohm=10000.0, sheet_rho_ohm_per_sq=100.0, trace_width=100)[0]
    assert check_thin_film_rules(sol2, rules) == []


def test_check_thin_film_rules_flags_short_bar():
    rules = dict(DEFAULT_THIN_FILM)
    # a tiny target resistance on a wide bar solves to a bar shorter
    # than min_resistor_length
    sol = solve_top_hat(target_r_ohm=1.0, sheet_rho_ohm_per_sq=100.0, bar_width=1000)
    violations = check_thin_film_rules(sol, rules)
    assert any("bar length" in v for v in violations)


def test_check_thin_film_rules_flags_narrow_bar_vs_kerf():
    rules = dict(DEFAULT_THIN_FILM)
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=rules["kerf"])
    violations = check_thin_film_rules(sol, rules)
    assert any("bar width" in v for v in violations)


def test_check_thin_film_rules_flags_spacing_narrower_than_kerf():
    rules = dict(DEFAULT_THIN_FILM)
    sol = solve_serpentine(
        target_r_ohm=10000.0, sheet_rho_ohm_per_sq=100.0, trace_width=200,
        spacing=rules["kerf"] // 2,
    )[0]
    violations = check_thin_film_rules(sol, rules)
    assert any("spacing" in v for v in violations)


# --- Figure 5 artwork overlap: pads must overlap the element, not abut it ----

def test_top_hat_pads_overlap_bar_when_requested():
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100)
    plain = generate_top_hat_geometry(sol)  # overlap=0 -- old abutting behaviour
    overlapped = generate_top_hat_geometry(sol, overlap=20)

    half_bar_l = sol.bar_length / 2
    # abutting: pad_a's right edge sits exactly at -half_bar_l
    assert max(p[0] for p in plain["pad_a_points"]) == pytest.approx(-half_bar_l, abs=1)
    # overlapping: pad_a's right edge reaches 20 DBU past -half_bar_l, into the bar
    assert max(p[0] for p in overlapped["pad_a_points"]) == pytest.approx(-half_bar_l + 20, abs=1)
    assert min(p[0] for p in overlapped["pad_b_points"]) == pytest.approx(half_bar_l - 20, abs=1)


def test_top_hat_overlap_never_exceeds_bar_midpoint():
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100)
    geo = generate_top_hat_geometry(sol, overlap=sol.bar_length * 10)  # absurdly large
    # the two pads' overlapping edges must not cross past the bar's center
    assert max(p[0] for p in geo["pad_a_points"]) <= 1
    assert min(p[0] for p in geo["pad_b_points"]) >= -1


def test_session_generate_top_hat_resistor_pads_overlap_bar():
    s = _session()
    sol = solve_top_hat(target_r_ohm=5000.0, sheet_rho_ohm_per_sq=100.0, bar_width=100)
    name = s.generate_top_hat_resistor(0, 0, sol, ref_des="R3")
    cell = s.library.cells[name]
    rules = load_thin_film_rules(s.library)
    r_layer, p_layer = rules["resistor_layer"], rules["pad_layer"]
    bar_xs = [p[0] for shp in cell.shapes if shp.layer == r_layer for p in shp.points]
    pad_shapes = [shp for shp in cell.shapes if shp.layer == p_layer]
    assert len(pad_shapes) == 2
    half_bar_l = max(bar_xs)
    # each pad must extend past the bar's own edge by some positive
    # overlap, not merely reach up to it
    pad_a_max_x = max(p[0] for p in pad_shapes[0].points)
    pad_b_min_x = min(p[0] for p in pad_shapes[1].points)
    assert pad_a_max_x > -half_bar_l
    assert pad_b_min_x < half_bar_l


def test_session_generate_serpentine_resistor_pads_overlap_element():
    s = _session()
    sol = solve_serpentine(target_r_ohm=10000.0, sheet_rho_ohm_per_sq=100.0, trace_width=50)[0]
    name = s.generate_serpentine_resistor(0, 0, sol, ref_des="R4")
    cell = s.library.cells[name]
    rules = load_thin_film_rules(s.library)
    r_layer, p_layer = rules["resistor_layer"], rules["pad_layer"]
    element_xs = [p[0] for shp in cell.shapes if shp.layer == r_layer for p in shp.points]
    pad_shapes = [shp for shp in cell.shapes if shp.layer == p_layer]
    assert len(pad_shapes) == 2
    min_x, max_x = min(element_xs), max(element_xs)
    left_pad, right_pad = sorted(pad_shapes, key=lambda s: min(p[0] for p in s.points))
    # left pad's right edge must reach past the element's left edge
    assert max(p[0] for p in left_pad.points) > min_x
    # right pad's left edge must reach before the element's right edge
    assert min(p[0] for p in right_pad.points) < max_x
