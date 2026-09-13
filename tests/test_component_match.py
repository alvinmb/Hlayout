import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.errors import LayoutError
from hlayout.core.library import Library
from hlayout.core.units import mm_to_dbu
from hlayout.io.component_library import DEFAULT_PKG20, DEFAULT_SOT_PACKAGES
from hlayout.io.component_match import (
    Match,
    MatchReport,
    Unmatched,
    build_match_report,
    eia_size_tokens,
    find_step_file,
    footprint_pin_stats,
    gullwing_package_table,
    match_gullwing_by_footprint,
    match_passive_by_name,
)
from hlayout.io.techfile import save_component_models, save_layer_thickness


# --- name-based passive matching --------------------------------------------

def test_eia_size_tokens_derived_from_ceramic_caps():
    tokens = eia_size_tokens()
    assert tokens == {"0603": "CAP_0603", "0805": "CAP_0805", "1206": "CAP_1206"}


def test_match_passive_by_name_simple_cases():
    assert match_passive_by_name("R1_0805") == "CAP_0805"
    assert match_passive_by_name("C3-1206-X") == "CAP_1206"
    assert match_passive_by_name("no size here") is None


def test_match_passive_by_name_rejects_an_embedded_digit_run():
    # "1206" is embedded inside a longer digit run, not standalone
    assert match_passive_by_name("PART120605") is None


def test_match_passive_by_name_rejects_an_ambiguous_name():
    assert match_passive_by_name("has0603and0805somewhere") is None


# --- gullwing package table (derived, not hand-duplicated) -------------------

def test_gullwing_package_table_derives_sot23():
    table = gullwing_package_table()
    assert table["SOT_23"] == {"pin_count": 3, "x_span_mm": pytest.approx(0.95)}


def test_gullwing_package_table_derives_pkg20_from_soic_leads():
    table = gullwing_package_table()
    assert table["PKG20"]["pin_count"] == DEFAULT_PKG20["leads_per_side"] * 2
    expected_span = (DEFAULT_PKG20["leads_per_side"] - 1) * DEFAULT_PKG20["pitch_mm"]
    assert table["PKG20"]["x_span_mm"] == pytest.approx(expected_span)


def test_gullwing_package_table_excludes_tab_style_packages():
    table = gullwing_package_table()
    # SOT_89 (heat_tab) and SOT_223 (a lead far wider than the rest) are
    # deliberately excluded -- see _has_wide_tab_lead()'s docstring for
    # the confirmed false-positive this avoids.
    assert "SOT_89" not in table
    assert "SOT_223" not in table
    assert "SOT_23" in table and "SOT_14" in table


def test_gullwing_package_table_respects_overrides():
    custom_sot = {"MY_SOT": dict(L=1, W=1, H=1, lead_t=0.1, lead_out=0.1, lead_foot=0.1,
                                  leads_neg_y=[(-1.0, 0.4)], leads_pos_y=[(1.0, 0.4)])}
    table = gullwing_package_table(sot_packages=custom_sot)
    assert table["MY_SOT"] == {"pin_count": 2, "x_span_mm": pytest.approx(2.0)}
    assert "SOT_23" not in table


# --- footprint_pin_stats -----------------------------------------------------

def _add_sot23_like_shapes(cell):
    cell.add_rectangle(0, mm_to_dbu(-0.475) - mm_to_dbu(0.2), mm_to_dbu(-0.65),
                        mm_to_dbu(-0.475) + mm_to_dbu(0.2), mm_to_dbu(-0.45), pin="1")
    cell.add_rectangle(0, mm_to_dbu(0.475) - mm_to_dbu(0.2), mm_to_dbu(-0.65),
                        mm_to_dbu(0.475) + mm_to_dbu(0.2), mm_to_dbu(-0.45), pin="2")
    cell.add_rectangle(0, mm_to_dbu(-0.2), mm_to_dbu(0.45),
                        mm_to_dbu(0.2), mm_to_dbu(0.65), pin="3")
    cell.add_path(61, [(-100, -100), (100, -100)], width=1000)
    return cell


def _sot23_like_cell():
    lib = Library("T")
    cell = _add_sot23_like_shapes(lib.new_cell("Q1"))
    return lib, cell


def test_footprint_pin_stats_on_a_sot23_like_cell():
    _lib, cell = _sot23_like_cell()
    stats = footprint_pin_stats(cell)
    assert stats["pin_count"] == 3
    assert stats["x_span_mm"] == pytest.approx(0.95, abs=0.01)


def test_footprint_pin_stats_none_with_fewer_than_two_pins():
    lib = Library("T")
    cell = lib.new_cell("C")
    cell.add_rectangle(0, -100, -100, 100, 100, pin="1")
    assert footprint_pin_stats(cell) is None


def test_footprint_pin_stats_ignores_unpinned_and_non_pad_shapes():
    lib = Library("T")
    cell = lib.new_cell("C")
    cell.add_rectangle(0, -100, -100, 100, 100, pin="1")
    cell.add_rectangle(0, 900, -100, 1100, 100, pin="2")
    cell.add_rectangle(0, 5000, 5000, 5100, 5100)          # unpinned -- ignored
    cell.add_path(61, [(-50, -50), (50, -50)], width=10)   # not a pad -- ignored
    stats = footprint_pin_stats(cell)
    assert stats["pin_count"] == 2


def test_footprint_pin_stats_averages_a_pin_shared_by_two_shapes():
    lib = Library("T")
    cell = lib.new_cell("C")
    cell.add_rectangle(0, -100, -100, 100, 100, pin="1")
    cell.add_rectangle(0, 900, -100, 1100, 100, pin="1")   # same pin, second shape
    cell.add_rectangle(0, 2000, -100, 2200, 100, pin="2")
    stats = footprint_pin_stats(cell)
    assert stats["pin_count"] == 2   # not 3 -- doesn't inflate the count


# --- match_gullwing_by_footprint ---------------------------------------------

def test_match_gullwing_by_footprint_exact():
    table = gullwing_package_table()
    assert match_gullwing_by_footprint({"pin_count": 3, "x_span_mm": 0.95, "y_span_mm": 0.95},
                                        table) == "SOT_23"


def test_match_gullwing_by_footprint_within_tolerance():
    table = gullwing_package_table()
    assert match_gullwing_by_footprint({"pin_count": 3, "x_span_mm": 1.1, "y_span_mm": 1.1},
                                        table) == "SOT_23"


def test_match_gullwing_by_footprint_wrong_axis_still_matches():
    # x_span way off, but y_span hits the table value -- the real-world
    # case examples/generate_sot_packages.py's SOT-23 footprint hits
    # (its two pad rows run along the opposite local axis from
    # component_library.py's own SOT_23 model).
    table = gullwing_package_table()
    assert match_gullwing_by_footprint({"pin_count": 3, "x_span_mm": 2.3, "y_span_mm": 0.95},
                                        table) == "SOT_23"


def test_match_gullwing_by_footprint_too_far_off():
    table = gullwing_package_table()
    assert match_gullwing_by_footprint({"pin_count": 3, "x_span_mm": 5.0, "y_span_mm": 5.0},
                                        table) is None


def test_match_gullwing_by_footprint_wrong_pin_count():
    table = gullwing_package_table()
    assert match_gullwing_by_footprint({"pin_count": 7, "x_span_mm": 0.95, "y_span_mm": 0.95},
                                        table) is None


def test_match_gullwing_by_footprint_ambiguous_returns_none():
    table = {
        "PKG_A": {"pin_count": 4, "x_span_mm": 1.0},
        "PKG_B": {"pin_count": 4, "x_span_mm": 1.0},
    }
    assert match_gullwing_by_footprint({"pin_count": 4, "x_span_mm": 1.0, "y_span_mm": 1.0},
                                        table) is None


# --- against the real examples/generate_sot_packages.py fixture -------------

def test_matcher_hits_the_real_example_sot23_footprint():
    """examples/generate_sot_packages.py is a real, pre-existing footprint
    generator in this repo that happens to lay its two pad rows out
    along the opposite axis from component_library.py's 3D model --
    proving the either-axis check actually works on real data, not
    just synthetic numbers."""
    examples_dir = os.path.join(os.path.dirname(__file__), "..", "examples")
    sys.path.insert(0, examples_dir)
    from generate_sot_packages import build, PACKAGES

    lib = build()
    table = gullwing_package_table()
    results = {}
    for name in PACKAGES:
        stats = footprint_pin_stats(lib.cells[name])
        results[name] = match_gullwing_by_footprint(stats, table) if stats else None

    assert results["SOT-23"] == "SOT_23"
    # Tab-style / out-of-library packages must never produce a
    # confident wrong match -- safe to be unmatched, never wrong.
    for name in ("SOT-23-5", "SOT-23-6", "SOT-89", "SOT-143", "SOT-223", "SOT-363"):
        assert results[name] is None, f"{name} matched {results[name]!r}, expected no match"


# --- find_step_file -----------------------------------------------------------

def test_find_step_file(tmp_path):
    assert find_step_file(str(tmp_path), "CAP_0805") is None
    (tmp_path / "CAP_0805.step").write_text("")
    found = find_step_file(str(tmp_path), "CAP_0805")
    assert found is not None and os.path.isfile(found)


# --- build_match_report end to end -------------------------------------------

def _match_design():
    """A passive matched by name, a semiconductor matched by footprint,
    and one genuinely unmatched cell -- all real "components" per
    is_component_cell() (each has a non-film outline shape)."""
    lib = Library("MATCHTEST")
    top = lib.new_cell("TOP")

    cap = lib.new_cell("C1_0805")
    cap.add_rectangle(0, -400, -625, 400, 625)
    cap.add_path(61, [(-1000, -625), (1000, -625)], width=100)
    top.add_sref("C1_0805", (0, 0))

    _add_sot23_like_shapes(lib.new_cell("Q1"))
    top.add_sref("Q1", (5000, 5000))

    mystery = lib.new_cell("MYSTERY")
    mystery.add_path(61, [(-50, -50), (50, -50)], width=500)
    top.add_sref("MYSTERY", (10000, 10000))

    save_layer_thickness(lib, {"0": 10})
    return lib


def _step_dir_with(tmp_path, *names):
    for n in names:
        (tmp_path / f"{n}.step").write_text("")
    return str(tmp_path)


def test_build_match_report_matches_by_name_and_footprint(tmp_path):
    lib = _match_design()
    step_dir = _step_dir_with(tmp_path, "CAP_0805", "SOT_23")
    report = build_match_report(lib, "TOP", step_dir)

    matched = {m.cell_name: m.package for m in report.matches}
    assert matched == {"C1_0805": "CAP_0805", "Q1": "SOT_23"}
    assert [u.cell_name for u in report.unmatched] == ["MYSTERY"]
    assert report.skipped_manual == []


def test_build_match_report_skips_a_manual_override(tmp_path):
    lib = _match_design()
    manual = {"C1_0805": {"mode": "extrude", "height_um": 999}}
    step_dir = _step_dir_with(tmp_path, "CAP_0805", "SOT_23")
    report = build_match_report(lib, "TOP", step_dir, component_models=manual)

    assert report.skipped_manual == ["C1_0805"]
    assert {m.cell_name for m in report.matches} == {"Q1"}
    assert report.component_models()["C1_0805"] == manual["C1_0805"]


def test_build_match_report_leaves_unmatched_when_step_file_missing(tmp_path):
    lib = _match_design()
    step_dir = str(tmp_path)  # no STEP files at all
    report = build_match_report(lib, "TOP", step_dir)

    assert report.matches == []
    reasons = {u.cell_name: u.reason for u in report.unmatched}
    assert "Build FreeCAD Component Library" in reasons["C1_0805"]
    assert "Build FreeCAD Component Library" in reasons["Q1"]


def test_build_match_report_raises_for_unknown_cell(tmp_path):
    lib = _match_design()
    with pytest.raises(LayoutError):
        build_match_report(lib, "NOPE", str(tmp_path))


def test_match_report_component_models_degenerates_to_base_when_no_matches():
    report = MatchReport(base_component_models={"X": {"mode": "extrude"}})
    assert report.component_models() == {"X": {"mode": "extrude"}}


def test_match_report_component_models_never_mutates_base():
    base = {"X": {"mode": "extrude"}}
    report = MatchReport(
        base_component_models=base,
        matches=[Match("Y", "CAP_0805", "passive", "matched by name",
                        {"mode": "model", "model_path": "y.step"})],
    )
    merged = report.component_models()
    assert merged == {"X": {"mode": "extrude"}, "Y": {"mode": "model", "model_path": "y.step"}}
    assert base == {"X": {"mode": "extrude"}}   # untouched


def test_match_report_summary_lines_cover_all_three_buckets():
    report = MatchReport(
        matches=[Match("C1", "CAP_0805", "passive", "matched by name",
                        {"mode": "model", "model_path": "c1.step"})],
        unmatched=[Unmatched("MYSTERY", "no size code in the name and no matching pad footprint")],
        skipped_manual=["Q1"],
    )
    lines = "\n".join(report.summary_lines())
    assert "C1" in lines and "CAP_0805" in lines
    assert "MYSTERY" in lines
    assert "Q1" in lines


def test_match_report_summary_lines_when_nothing_to_report():
    assert MatchReport().summary_lines() == ["No placed component cells found to match."]
