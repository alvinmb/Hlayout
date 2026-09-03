import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

shapely = pytest.importorskip("shapely")

from hlayout.core.errors import GeometryError, LayoutError
from hlayout.core.maskops import (
    MASK_OPS,
    boolean_op,
    fracture_polygons,
    layer_to_shapely,
    layer_to_shapely_flattened,
    shapely_to_polygons,
    shape_to_shapely,
    size_geometry,
)
from hlayout.core.shapes import Path, Polygon, Rectangle
from hlayout.scripting import Session


def _fresh_session():
    s = Session.new("MASKOPTEST")
    s.new_cell("TOP")
    s.clear_undo_history()
    return s


# --- Session.mask_op() / MASKOP command-level tests ------------------------

def test_oversize_grows_a_rectangle_with_mitred_square_corners():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    written = s.mask_op("OVERSIZE", 0, 1, amount=10)
    assert written == {"TOP": 1}
    result = next(sh for sh in s.cell.shapes if sh.layer == 1)
    assert isinstance(result, Polygon)
    x0, y0, x1, y1 = result.bbox()
    # Mitred (sharp-cornered) growth of a 100x100 square by 10 on all sides.
    assert (x0, y0, x1, y1) == (-10, -10, 110, 110)
    # The source layer is untouched.
    assert len([sh for sh in s.cell.shapes if sh.layer == 0]) == 1


def test_undersize_shrinks_a_rectangle():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    s.mask_op("UNDERSIZE", 0, 1, amount=10)
    result = next(sh for sh in s.cell.shapes if sh.layer == 1)
    x0, y0, x1, y1 = result.bbox()
    assert (x0, y0, x1, y1) == (10, 10, 90, 90)


def test_undersize_past_extinction_writes_nothing():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    written = s.mask_op("UNDERSIZE", 0, 1, amount=100)
    assert written == {}
    assert not any(sh.layer == 1 for sh in s.cell.shapes)


def test_and_intersects_two_overlapping_layers():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    s.rect(1, 50, 50, 150, 150)
    s.mask_op("AND", 0, 2, layer_b=1)
    result = next(sh for sh in s.cell.shapes if sh.layer == 2)
    assert result.bbox() == (50, 50, 100, 100)


def test_and_with_no_overlap_writes_nothing():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    s.rect(1, 100, 100, 110, 110)
    written = s.mask_op("AND", 0, 2, layer_b=1)
    assert written == {}


def test_or_unions_two_layers_into_one_polygon_when_overlapping():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    s.rect(1, 50, 0, 150, 100)
    s.mask_op("OR", 0, 2, layer_b=1)
    results = [sh for sh in s.cell.shapes if sh.layer == 2]
    assert len(results) == 1
    assert results[0].bbox() == (0, 0, 150, 100)


def test_or_of_disjoint_shapes_writes_two_separate_polygons():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    s.rect(1, 100, 100, 110, 110)
    written = s.mask_op("OR", 0, 2, layer_b=1)
    assert written == {"TOP": 2}
    results = [sh for sh in s.cell.shapes if sh.layer == 2]
    boxes = sorted(sh.bbox() for sh in results)
    assert boxes == [(0, 0, 10, 10), (100, 100, 110, 110)]


def test_result_can_target_the_same_layer_as_input():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    s.mask_op("OVERSIZE", 0, 0, amount=5)
    # The original rectangle plus the new oversized polygon both now
    # live on layer 0.
    on_layer_0 = [sh for sh in s.cell.shapes if sh.layer == 0]
    assert len(on_layer_0) == 2


def test_path_on_input_layer_is_converted_to_its_outline_polygon_first():
    s = _fresh_session()
    s.path(0, [(0, 0), (100, 0)], width=20, cap="flush")
    s.mask_op("OVERSIZE", 0, 1, amount=0)
    result = next(sh for sh in s.cell.shapes if sh.layer == 1)
    # A flush-capped 20-wide path from (0,0) to (100,0): outline bbox is
    # the segment's own extent x [-10, 10] in the perpendicular direction.
    x0, y0, x1, y1 = result.bbox()
    assert (x0, y0, x1, y1) == (0, -10, 100, 10)


def test_mask_op_runs_on_current_cell_by_default():
    s = _fresh_session()
    s.new_cell("OTHER")
    s.rect(0, 0, 0, 50, 50)
    s.use_cell("TOP")
    s.rect(0, 0, 0, 10, 10)
    written = s.mask_op("OVERSIZE", 0, 1, amount=1)
    assert list(written.keys()) == ["TOP"]


def test_mask_op_cell_names_restricts_scope():
    s = _fresh_session()
    s.new_cell("OTHER")
    s.rect(0, 0, 0, 50, 50)
    s.use_cell("TOP")
    s.rect(0, 0, 0, 10, 10)
    written = s.mask_op("OVERSIZE", 0, 1, amount=1, cell_names=["OTHER"])
    assert list(written.keys()) == ["OTHER"]
    assert not any(sh.layer == 1 for sh in s.library.cells["TOP"].shapes)


# --- flatten: mask ops must see geometry placed via Sref (Round 36's --
# cell+Sref resistor architecture put nearly all real design content
# behind an Sref, which an earlier version of mask_op couldn't see at
# all -- "mask operations are not producing output") ------------------

def test_mask_op_by_default_sees_geometry_placed_via_sref():
    s = _fresh_session()
    s.new_cell("DEVICE")
    s.rect(0, 0, 0, 100, 100)
    s.use_cell("TOP")
    s.sref("DEVICE", (500, 500))
    written = s.mask_op("OVERSIZE", 0, 1, amount=10)
    assert written == {"TOP": 1}
    result = next(sh for sh in s.cell.shapes if sh.layer == 1)
    # DEVICE's 0,0-100,100 rectangle placed at (500,500) -> 500..600
    # before growing by 10 on all sides.
    assert result.bbox() == (490, 490, 610, 610)


def test_mask_op_flattens_rotation_and_mirror_of_the_placing_sref():
    s = _fresh_session()
    s.new_cell("DEVICE")
    s.rect(0, 0, 0, 100, 20)  # a wide, short rectangle
    s.use_cell("TOP")
    s.sref("DEVICE", (0, 0), rotation=90.0)
    written = s.mask_op("OVERSIZE", 0, 1, amount=0)
    assert written == {"TOP": 1}
    result = next(sh for sh in s.cell.shapes if sh.layer == 1)
    # Rotated 90 degrees: the 100-long axis is now along Y, the
    # 20-long axis along X.
    x0, y0, x1, y1 = result.bbox()
    assert (x1 - x0, y1 - y0) == (20, 100)


def test_mask_op_flattens_nested_srefs_two_levels_deep():
    s = _fresh_session()
    s.new_cell("LEAF")
    s.rect(0, 0, 0, 10, 10)
    s.new_cell("MID")
    s.sref("LEAF", (100, 0))
    s.use_cell("TOP")
    s.sref("MID", (0, 100))
    written = s.mask_op("OVERSIZE", 0, 1, amount=0)
    assert written == {"TOP": 1}
    result = next(sh for sh in s.cell.shapes if sh.layer == 1)
    # LEAF's rect at local (0,0)-(10,10), placed at (100,0) inside MID,
    # MID itself placed at (0,100) inside TOP -> (100,100)-(110,110).
    assert result.bbox() == (100, 100, 110, 110)


def test_mask_op_and_across_two_layers_each_from_a_different_placed_cell():
    s = _fresh_session()
    s.new_cell("A_DEV")
    s.rect(0, 0, 0, 100, 100)
    s.new_cell("B_DEV")
    s.rect(1, 0, 0, 100, 100)
    s.use_cell("TOP")
    s.sref("A_DEV", (0, 0))
    s.sref("B_DEV", (50, 50))
    written = s.mask_op("AND", 0, 2, layer_b=1)
    assert written == {"TOP": 1}
    result = next(sh for sh in s.cell.shapes if sh.layer == 2)
    assert result.bbox() == (50, 50, 100, 100)


def test_mask_op_flatten_false_restricts_to_the_cells_own_shapes():
    s = _fresh_session()
    s.new_cell("DEVICE")
    s.rect(0, 0, 0, 100, 100)
    s.use_cell("TOP")
    s.sref("DEVICE", (500, 500))
    s.rect(0, 0, 0, 5, 5)  # TOP's own directly-owned shape on layer 0
    written = s.mask_op("OVERSIZE", 0, 1, amount=0, flatten=False)
    assert written == {"TOP": 1}
    result = next(sh for sh in s.cell.shapes if sh.layer == 1)
    # Only TOP's own 0,0-5,5 rectangle counts -- not DEVICE's, placed
    # via Sref.
    assert result.bbox() == (0, 0, 5, 5)


def test_layer_to_shapely_flattened_matches_non_flattened_with_no_srefs():
    s = _fresh_session()
    s.rect(0, 0, 0, 30, 30)
    flat = layer_to_shapely_flattened(s.library, s.cell, 0)
    plain = layer_to_shapely(s.cell.shapes_on_layer(0))
    assert flat.equals(plain)


def test_maskop_command_flatten_option():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    s.new_cell("DEVICE")
    s.rect(0, 0, 0, 100, 100)
    s.use_cell("TOP")
    s.sref("DEVICE", (0, 0))
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("MASKOP OVERSIZE 0 result=1 amount=0 flatten=0")
    assert "no output" in out
    out = interp.run("MASKOP OVERSIZE 0 result=1 amount=0 flatten=1")
    assert "wrote 1 polygon" in out


def test_mask_op_rejects_unknown_op():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    with pytest.raises(LayoutError):
        s.mask_op("NOR", 0, 1, amount=1)


def test_and_or_not_xor_require_layer_b():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    for op in ("AND", "OR", "NOT", "XOR"):
        with pytest.raises(LayoutError):
            s.mask_op(op, 0, 1)


def test_not_subtracts_layer_b_from_layer_a():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    s.rect(1, 50, 0, 150, 100)
    s.mask_op("NOT", 0, 2, layer_b=1)
    result = next(sh for sh in s.cell.shapes if sh.layer == 2)
    assert result.bbox() == (0, 0, 50, 100)


def test_not_of_fully_covered_shape_writes_nothing():
    s = _fresh_session()
    s.rect(0, 25, 25, 75, 75)
    s.rect(1, 0, 0, 100, 100)
    written = s.mask_op("NOT", 0, 2, layer_b=1)
    assert written == {}


def test_xor_of_overlapping_layers_writes_the_non_overlapping_parts():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    s.rect(1, 50, 0, 150, 100)
    s.mask_op("XOR", 0, 2, layer_b=1)
    results = [sh for sh in s.cell.shapes if sh.layer == 2]
    boxes = sorted(sh.bbox() for sh in results)
    assert boxes == [(0, 0, 50, 100), (100, 0, 150, 100)]


def test_xor_of_identical_layers_writes_nothing():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    s.rect(1, 0, 0, 100, 100)
    written = s.mask_op("XOR", 0, 2, layer_b=1)
    assert written == {}


def test_simplify_reduces_vertex_count_of_an_oversized_result():
    from shapely.geometry import Point as _Pt
    s = _fresh_session()
    s.rect(0, 0, 0, 20, 20)
    # A large OVERSIZE with mitred joins on a single rectangle stays a
    # simple 4-point box (mitre joins don't round corners), so use a
    # rounded shape instead by growing a small square heavily -- the
    # buffer's own corner handling is mitred, so to actually exercise
    # simplify() meaningfully we compare a simplified vs. unsimplified
    # write of the same AND result built from two overlapping many-sided
    # approximations of circles.
    s.new_cell("CIRCLES")
    s.use_cell("CIRCLES")
    from hlayout.core.maskops import shapely_to_polygons
    a = _Pt(0, 0).buffer(50, quad_segs=32)
    b = _Pt(30, 0).buffer(50, quad_segs=32)
    for poly in shapely_to_polygons(a, layer=0):
        s.cell.shapes.append(poly)
    for poly in shapely_to_polygons(b, layer=1):
        s.cell.shapes.append(poly)
    written_plain = s.mask_op("OR", 0, 2, layer_b=1)
    plain_points = sum(len(sh.points) for sh in s.cell.shapes if sh.layer == 2)
    written_simplified = s.mask_op("OR", 0, 3, layer_b=1, simplify=5.0)
    simplified_points = sum(len(sh.points) for sh in s.cell.shapes if sh.layer == 3)
    assert written_plain and written_simplified
    assert simplified_points < plain_points


def test_oversize_undersize_reject_negative_amount():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    with pytest.raises(LayoutError):
        s.mask_op("OVERSIZE", 0, 1, amount=-5)


def test_mask_op_is_a_single_undo_step():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    s.clear_undo_history()
    before = len(s.cell.shapes)
    s.mask_op("OVERSIZE", 0, 1, amount=10)
    assert len(s.cell.shapes) == before + 1
    assert s.undo() is True
    assert len(s.cell.shapes) == before


# --- MASKOP command-bar tests -----------------------------------------------

def test_maskop_command_oversize():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("MASKOP OVERSIZE 0 result=1 amount=10")
    assert "wrote 1 polygon" in out
    result = next(sh for sh in s.cell.shapes if sh.layer == 1)
    assert result.bbox() == (-10, -10, 110, 110)


def test_maskop_command_and():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    s.rect(1, 50, 50, 150, 150)
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("MASKOP AND 0 1 result=2")
    assert "wrote 1 polygon" in out
    result = next(sh for sh in s.cell.shapes if sh.layer == 2)
    assert result.bbox() == (50, 50, 100, 100)


def test_maskop_command_requires_amount_for_oversize():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("MASKOP OVERSIZE 0 result=1")
    assert "error" in out.lower()


def test_maskop_command_rejects_unknown_op():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("MASKOP FOO 0 result=1 amount=1")
    assert "error" in out.lower()


def test_maskop_reports_no_output_distinctly():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("MASKOP OVERSIZE 0 result=1 amount=10")
    assert "no output" in out


def test_maskop_command_turns_on_a_hidden_result_layer():
    """Reported bug: running a boolean mask op (AND/OR/NOT/XOR) appeared
    to "not produce data" -- the operation was actually writing correct
    output, but to a result layer whose visibility happened to be off,
    which looks identical to writing nothing at all on the canvas. The
    MASKOP command (and the Mask Operations dialog, covered by the GUI
    end-to-end test below) now turns the result layer's visibility on
    whenever it actually wrote output, and says so."""
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    s.rect(1, 50, 50, 150, 150)
    s.library.layers[2].visible = False
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("MASKOP AND 0 1 result=2")
    assert "wrote 1 polygon" in out
    assert "visibility turned on" in out
    assert s.library.layers[2].visible is True


def test_maskop_command_does_not_mention_visibility_when_already_visible():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    interp = CommandInterpreter(s, ScriptConsole(s))
    assert s.library.layers[1].visible is True
    out = interp.run("MASKOP OVERSIZE 0 result=1 amount=10")
    assert "wrote 1 polygon" in out
    assert "visibility" not in out


# --- low-level hlayout.core.maskops unit tests ------------------------------

def test_shape_to_shapely_converts_rectangle_and_polygon():
    rect = Rectangle.from_corners(0, 0, 0, 10, 10)
    poly = Polygon(layer=0, points=[(0, 0), (10, 0), (5, 10)])
    g_rect = shape_to_shapely(rect)
    g_poly = shape_to_shapely(poly)
    assert g_rect.area == pytest.approx(100.0)
    assert g_poly.area == pytest.approx(50.0)


def test_shape_to_shapely_buffers_a_path_by_half_width():
    path = Path(layer=0, points=[(0, 0), (100, 0)], width=10, cap="flush")
    g = shape_to_shapely(path)
    minx, miny, maxx, maxy = g.bounds
    assert (minx, miny, maxx, maxy) == (0, -5, 100, 5)


def test_shape_to_shapely_returns_none_for_zero_width_path():
    path = Path(layer=0, points=[(0, 0), (100, 0)], width=0, cap="flush")
    assert shape_to_shapely(path) is None


def test_layer_to_shapely_of_empty_list_is_empty_geometry():
    g = layer_to_shapely([])
    assert g.is_empty


def test_boolean_op_rejects_unknown_op():
    a = shape_to_shapely(Rectangle.from_corners(0, 0, 0, 10, 10))
    b = shape_to_shapely(Rectangle.from_corners(0, 5, 5, 15, 15))
    with pytest.raises(GeometryError):
        boolean_op(a, b, "NOR")


def test_boolean_op_not_is_difference():
    a = shape_to_shapely(Rectangle.from_corners(0, 0, 0, 10, 10))
    b = shape_to_shapely(Rectangle.from_corners(0, 5, 5, 15, 15))
    result = boolean_op(a, b, "NOT")
    assert result.equals(a.difference(b))


def test_boolean_op_xor_is_symmetric_difference():
    a = shape_to_shapely(Rectangle.from_corners(0, 0, 0, 10, 10))
    b = shape_to_shapely(Rectangle.from_corners(0, 5, 5, 15, 15))
    result = boolean_op(a, b, "XOR")
    assert result.equals(a.symmetric_difference(b))


def test_shapely_to_polygons_bridges_a_hole_with_a_slit():
    from shapely.geometry import Polygon as ShPolygon
    # A square frame: outer 100x100, with a 40x40 hole in the middle.
    outer = [(0, 0), (100, 0), (100, 100), (0, 100)]
    hole = [(30, 30), (70, 30), (70, 70), (30, 70)]
    holed = ShPolygon(outer, [hole])
    assert len(holed.interiors) == 1
    polys = shapely_to_polygons(holed, layer=5)
    assert len(polys) == 1
    p = polys[0]
    assert p.layer == 5
    # The bridged ring still spans the full outer bbox, and area is
    # preserved (the polygon format has no holes, but the doubled-back
    # slit edges don't change the enclosed area).
    assert p.bbox() == (0, 0, 100, 100)
    from shapely.geometry import Polygon as ShPolygon2
    reconstructed = ShPolygon2(p.points)
    assert reconstructed.area == pytest.approx(holed.area, rel=1e-6)


def test_shapely_to_polygons_raises_over_the_vertex_limit():
    from shapely.geometry import Point
    # A circle with enough segments to blow well past MAX_POLY_POINTS (200).
    circle = Point(0, 0).buffer(1000, quad_segs=128)
    assert len(circle.exterior.coords) - 1 > 200
    with pytest.raises(GeometryError):
        shapely_to_polygons(circle, layer=0)


def test_mask_ops_tuple_matches_expected_six_operations():
    assert set(MASK_OPS) == {"OVERSIZE", "UNDERSIZE", "AND", "OR", "NOT", "XOR"}


# --- fracture_polygons ---------------------------------------------------

def test_fracture_polygons_splits_a_holed_sheet_into_holefree_pieces():
    from shapely.geometry import box
    from shapely.ops import unary_union
    sheet = box(0, 0, 10000, 10000)
    holes = unary_union([box(x, y, x + 300, y + 300)
                         for x in range(500, 10000, 900)
                         for y in range(500, 10000, 900)])
    holed = sheet.difference(holes)
    assert list(holed.interiors)  # sanity: it really has holes

    pieces = fracture_polygons(holed, max_points=200)
    assert len(pieces) > 1
    for p in pieces:
        assert p.geom_type == "Polygon"
        assert not p.interiors                       # hole-free
        assert len(p.exterior.coords) - 1 <= 200     # under the limit
    # area is preserved (no geometry lost to the fracture)
    assert unary_union(pieces).area == pytest.approx(holed.area, rel=1e-9)
    # and none of the pieces cover a hole centre
    assert not unary_union(pieces).covers(box(600, 600, 700, 700).centroid)


def test_fracture_polygons_passes_a_small_holefree_polygon_through_unchanged():
    from shapely.geometry import box
    b = box(0, 0, 100, 100)
    out = fracture_polygons(b)
    assert len(out) == 1 and out[0].equals(b)
