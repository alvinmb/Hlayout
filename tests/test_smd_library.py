import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.shapes import Path, Text
from hlayout.core.smdlib import (BODY_OUTLINE_LAYER, CHIP_SIZES_MM, OTHER_PACKAGES_MM,
                                  REFDES_LAYER, generate_smd_library)
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
