import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.shapes import Path, Rectangle, Text
from hlayout.core.smdlib import (BODY_OUTLINE_LAYER, CHIP_SIZES_MM, LEAD_LAYER,
                                  OTHER_PACKAGES_MM, REFDES_LAYER, TERMINAL_LAYER,
                                  generate_smd_library)
from hlayout.core.units import mm_to_dbu


def test_generates_expected_cell_set():
    lib = generate_smd_library()
    names = set(lib.cells)
    for size in CHIP_SIZES_MM:
        assert f"R_{size}" in names
        assert f"C_{size}" in names
    for cell_name in OTHER_PACKAGES_MM:
        assert cell_name in names
    assert len(names) == 2 * len(CHIP_SIZES_MM) + len(OTHER_PACKAGES_MM)


def test_every_cell_has_a_body_outline_and_refdes():
    lib = generate_smd_library()
    for cell in lib.cells.values():
        paths = [s for s in cell.shapes if isinstance(s, Path)]
        texts = [s for s in cell.shapes if isinstance(s, Text)]
        assert len(paths) == 1
        assert len(texts) == 1
        assert paths[0].layer == BODY_OUTLINE_LAYER
        assert paths[0].width == mm_to_dbu(0.1)
        assert texts[0].layer == REFDES_LAYER


def test_refdes_prefixes_match_component_family():
    lib = generate_smd_library()
    assert lib.cells["R_0603"].shapes[1].text == "R"
    assert lib.cells["C_0603"].shapes[1].text == "C"
    assert lib.cells["T_SOT23"].shapes[1].text == "T"
    assert lib.cells["D_SOD123"].shapes[1].text == "D"
    assert lib.cells["IC_SOIC16"].shapes[1].text == "IC"


def test_body_outline_bbox_matches_datasheet_dimensions_mm():
    lib = generate_smd_library()
    length_mm, width_mm = CHIP_SIZES_MM["0805"]
    path = lib.cells["C_0805"].shapes[0]
    xs = [p[0] for p in path.points]
    ys = [p[1] for p in path.points]
    assert max(xs) - min(xs) == mm_to_dbu(length_mm)
    assert max(ys) - min(ys) == mm_to_dbu(width_mm)


def test_body_outline_is_a_closed_rectangle():
    lib = generate_smd_library()
    path = lib.cells["R_1206"].shapes[0]
    assert path.points[0] == path.points[-1]
    assert len(path.points) == 5


def test_soic_packages_use_correct_body_width():
    lib = generate_smd_library()
    narrow = lib.cells["IC_SOIC14"].shapes[0]
    wide = lib.cells["IC_SOIC24"].shapes[0]
    narrow_ys = [p[1] for p in narrow.points]
    wide_ys = [p[1] for p in wide.points]
    assert max(narrow_ys) - min(narrow_ys) == mm_to_dbu(3.90)
    assert max(wide_ys) - min(wide_ys) == mm_to_dbu(7.50)


def test_chip_rc_cells_get_two_terminals_on_layer_62():
    lib = generate_smd_library()
    for size in CHIP_SIZES_MM:
        for prefix in ("R", "C"):
            cell = lib.cells[f"{prefix}_{size}"]
            terminals = [s for s in cell.shapes if isinstance(s, Rectangle)]
            assert len(terminals) == 2
            assert all(t.layer == TERMINAL_LAYER for t in terminals)


def test_other_packages_get_no_terminal_layer_rectangles():
    # T/D/IC packages get gull-wing LEAD_LAYER rectangles, not the
    # TERMINAL_LAYER wraparound end-caps chip R/C uses.
    lib = generate_smd_library()
    for cell_name in OTHER_PACKAGES_MM:
        cell = lib.cells[cell_name]
        assert not any(isinstance(s, Rectangle) and s.layer == TERMINAL_LAYER
                        for s in cell.shapes)


def test_each_package_gets_one_lead_per_pin_on_layer_63():
    lib = generate_smd_library()
    expected_pins = {"T_SOT23": 3, "D_SOD123": 2, "IC_SOIC14": 14,
                      "IC_SOIC16": 16, "IC_SOIC24": 24}
    for cell_name, pins in expected_pins.items():
        cell = lib.cells[cell_name]
        leads = [s for s in cell.shapes if isinstance(s, Rectangle) and s.layer == LEAD_LAYER]
        assert len(leads) == pins, f"{cell_name}: expected {pins} leads, got {len(leads)}"


def test_soic_leads_sit_outside_the_body_on_both_long_sides():
    lib = generate_smd_library()
    length_mm, width_mm = 9.90, 3.90  # IC_SOIC16
    half_w = mm_to_dbu(width_mm) / 2
    cell = lib.cells["IC_SOIC16"]
    leads = [s for s in cell.shapes if isinstance(s, Rectangle) and s.layer == LEAD_LAYER]
    assert len(leads) == 16
    top = [l for l in leads if min(p[1] for p in l.points) >= half_w]
    bottom = [l for l in leads if max(p[1] for p in l.points) <= -half_w]
    assert len(top) == 8
    assert len(bottom) == 8


def test_sot23_leads_are_2_plus_1():
    lib = generate_smd_library()
    width_mm = 1.30
    half_w = mm_to_dbu(width_mm) / 2
    cell = lib.cells["T_SOT23"]
    leads = [s for s in cell.shapes if isinstance(s, Rectangle) and s.layer == LEAD_LAYER]
    assert len(leads) == 3
    one_side = [l for l in leads if min(p[1] for p in l.points) >= half_w]
    other_side = [l for l in leads if max(p[1] for p in l.points) <= -half_w]
    assert {len(one_side), len(other_side)} == {1, 2}


def test_sod123_leads_are_at_each_end():
    lib = generate_smd_library()
    length_mm = 3.70
    half_l = mm_to_dbu(length_mm) / 2
    cell = lib.cells["D_SOD123"]
    leads = [s for s in cell.shapes if isinstance(s, Rectangle) and s.layer == LEAD_LAYER]
    assert len(leads) == 2
    left = [l for l in leads if max(p[0] for p in l.points) <= -half_l]
    right = [l for l in leads if min(p[0] for p in l.points) >= half_l]
    assert len(left) == 1
    assert len(right) == 1


def test_terminals_span_full_body_width_and_sit_at_each_end():
    lib = generate_smd_library()
    length_mm, width_mm = CHIP_SIZES_MM["0805"]
    cell = lib.cells["C_0805"]
    left, right = [s for s in cell.shapes if isinstance(s, Rectangle)]
    if left.points[0][0] > right.points[0][0]:
        left, right = right, left

    half_l = mm_to_dbu(length_mm) / 2
    half_w = mm_to_dbu(width_mm) / 2
    xs_left = [p[0] for p in left.points]
    ys_left = [p[1] for p in left.points]
    xs_right = [p[0] for p in right.points]

    assert min(xs_left) == -half_l
    assert max(xs_right) == half_l
    assert min(ys_left) == -half_w and max(ys_left) == half_w
    # the two terminals don't overlap in the middle of the body
    assert max(xs_left) <= min(xs_right)
