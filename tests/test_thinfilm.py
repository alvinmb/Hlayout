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
