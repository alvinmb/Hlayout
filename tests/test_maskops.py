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


# --- Session.clear_layer() / CLEAR -------------------------------------------

def test_clear_layer_deletes_every_shape_on_that_layer():
    s = _fresh_session()
    s.rect(63, 0, 0, 100, 100)
    s.rect(63, 200, 200, 300, 300)
    s.rect(0, 0, 0, 50, 50)
    written = s.clear_layer(63)
    assert written == {"TOP": 2}
    assert [sh.layer for sh in s.cell.shapes] == [0]


def test_clear_layer_on_an_already_clean_layer_returns_empty():
    s = _fresh_session()
    s.rect(0, 0, 0, 50, 50)
    assert s.clear_layer(63) == {}


def test_clear_layer_mixed_shape_types():
    s = _fresh_session()
    s.rect(63, 0, 0, 100, 100)
    s.path(63, [(0, 0), (100, 0)], width=20, cap="round")
    s.polygon(63, [(0, 0), (10, 0), (5, 10)])
    written = s.clear_layer(63)
    assert written == {"TOP": 3}
    assert s.cell.shapes == []


def test_clear_layer_targets_a_named_cell_directly_no_flatten():
    # No flatten option -- this always operates on the named cell's own
    # shapes only, matching mask_op_top_view()'s own scope (the layer
    # being cleared has to actually live in the cell being operated on).
    s = _fresh_session()
    s.new_cell("Other")
    s.rect(63, 0, 0, 100, 100)
    s.use_cell("TOP")
    s.rect(63, 0, 0, 50, 50)
    written = s.clear_layer(63, cell_names=["Other"])
    assert written == {"Other": 1}
    assert any(sh.layer == 63 for sh in s.cell.shapes)  # TOP untouched
    assert s.library.cells["Other"].shapes == []


def test_clear_layer_is_undoable():
    s = _fresh_session()
    s.rect(63, 0, 0, 100, 100)
    s.clear_undo_history()
    before = len(s.cell.shapes)
    s.clear_layer(63)
    assert len(s.cell.shapes) == before - 1
    assert s.undo() is True
    assert len(s.cell.shapes) == before


def test_clear_command_bar():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    s.rect(63, 0, 0, 100, 100)
    s.rect(63, 200, 200, 300, 300)
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("CLEAR 63")
    assert "deleted 2 shape" in out
    assert s.cell.shapes == []


def test_clear_command_bar_reports_nothing_to_delete():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("CLEAR 63")
    assert "nothing on layer 63" in out


def test_clear_command_bar_requires_a_layer():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("CLEAR")
    assert "error" in out.lower()


# --- Session.convert_rects_to_outline() / OUTLINE ---------------------------

def test_convert_rects_to_outline_replaces_a_rectangle_with_a_closed_path():
    s = _fresh_session()
    s.rect(0, 0, 0, 5000, 5000)
    written = s.convert_rects_to_outline(0, 100)
    assert written == {"TOP": 1}
    shapes = s.cell.shapes
    assert len(shapes) == 1
    result = shapes[0]
    assert isinstance(result, Path)
    assert result.layer == 0
    assert result.width == 100
    # closed ring: same 4 corners, first point repeated at the end.
    assert result.points == [(0, 0), (5000, 0), (5000, 5000), (0, 5000), (0, 0)]


def test_convert_rects_to_outline_also_converts_polygons():
    # A substrate/package body is often a Polygon, not a Rectangle
    # (e.g. after a rotation or a hand-edited outline) -- confirmed
    # live, a real substrate shape turned out to be a Polygon, which
    # an earlier Rectangle-only version of this silently skipped
    # entirely (reporting "no rectangles" despite real closed geometry
    # sitting right there on the named layer).
    s = _fresh_session()
    s.polygon(0, [(0, 0), (5000, 0), (6000, 3000), (5000, 5000), (0, 5000)])
    written = s.convert_rects_to_outline(0, 100)
    assert written == {"TOP": 1}
    result = s.cell.shapes[0]
    assert isinstance(result, Path)
    assert result.points == [
        (0, 0), (5000, 0), (6000, 3000), (5000, 5000), (0, 5000), (0, 0),
    ]


def test_convert_rects_to_outline_only_converts_the_named_layer():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    s.rect(1, 0, 0, 100, 100)
    written = s.convert_rects_to_outline(0, 50)
    assert written == {"TOP": 1}
    assert any(isinstance(sh, Rectangle) and sh.layer == 1 for sh in s.cell.shapes)
    assert not any(isinstance(sh, Rectangle) and sh.layer == 0 for sh in s.cell.shapes)


def test_convert_rects_to_outline_converts_every_matching_rectangle():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    s.rect(0, 500, 500, 600, 600)
    written = s.convert_rects_to_outline(0, 50)
    assert written == {"TOP": 2}
    assert all(isinstance(sh, Path) for sh in s.cell.shapes)


def test_convert_rects_to_outline_leaves_other_shape_types_untouched():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    s.path(0, [(200, 200), (300, 200)], width=20, cap="round")
    written = s.convert_rects_to_outline(0, 50)
    assert written == {"TOP": 1}
    assert sum(1 for sh in s.cell.shapes if isinstance(sh, Path)) == 2


def test_convert_rects_to_outline_targets_a_named_cell_directly_no_flatten():
    # No flatten option -- this always operates on the named cell's own
    # shapes only, since the rectangle being replaced has to actually
    # live in the cell being operated on (e.g. a substrate or package
    # cell's own name, not whatever cell places it via Sref).
    s = _fresh_session()
    s.new_cell("Substrate")
    s.rect(61, 0, 0, 5000, 5000)
    s.use_cell("TOP")
    s.sref("Substrate", (0, 0))
    written = s.convert_rects_to_outline(61, 100, cell_names=["Substrate"])
    assert written == {"Substrate": 1}
    assert isinstance(s.library.cells["Substrate"].shapes[0], Path)
    assert not any(isinstance(sh, Rectangle) for sh in s.cell.shapes)  # TOP untouched


def test_convert_rects_to_outline_zero_rectangles_returns_empty():
    s = _fresh_session()
    s.rect(1, 0, 0, 100, 100)  # a different layer
    assert s.convert_rects_to_outline(0, 50) == {}


def test_convert_rects_to_outline_rejects_non_positive_width():
    s = _fresh_session()
    s.rect(0, 0, 0, 100, 100)
    with pytest.raises(LayoutError):
        s.convert_rects_to_outline(0, 0)


def test_outline_command_bar():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    s.rect(0, 0, 0, 5000, 5000)
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("OUTLINE 0 width=100")
    assert "converted 1 shape" in out
    assert isinstance(s.cell.shapes[0], Path)


def test_outline_command_bar_reports_no_conversions():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("OUTLINE 0 width=100")
    assert "converted no rectangles/polygons" in out


def test_outline_command_bar_requires_width():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("OUTLINE 0")
    assert "error" in out.lower()


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


def _tv(s, name="TOPVIEW"):
    """The top view's own output cell (it never writes into the viewed cell)."""
    return s.library.get_cell(name)


# --- Session.mask_op_top_view() / TOPVIEW: hidden-line-removal top view ----

def test_top_view_keeps_the_full_outline_of_the_topmost_layer():
    # layer 20 (higher number = printed later = physically on top) has
    # nothing above it, so its whole outline must survive untouched.
    s = _fresh_session()
    s.rect(5, 0, 0, 600, 600)
    s.rect(20, 400, 400, 1000, 1000)
    written = s.mask_op_top_view(63, layers=[5, 20])
    assert "TOP" in written and written["TOP"] > 0
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    assert paths and all(isinstance(p, Path) for p in paths)
    from hlayout.core.maskops import shape_to_shapely
    from shapely.ops import unary_union
    drawn = unary_union([shape_to_shapely(p).buffer(0) for p in paths])
    # layer 20's own full perimeter (as a thin buffered line) must be
    # entirely covered by what got drawn -- none of it was removed.
    top_outline = shapely.geometry.box(400, 400, 1000, 1000).boundary.buffer(1)
    assert top_outline.difference(drawn.buffer(5)).area < top_outline.area * 0.05


def test_top_view_removes_the_occluded_part_of_a_lower_layers_outline():
    # The part of layer 5's (bottom) outline that falls inside layer 20's
    # (top) filled footprint must be genuinely absent from the result --
    # not just present-but-hidden the way a plain union would leave it.
    s = _fresh_session()
    s.rect(5, 0, 0, 600, 600)
    s.rect(20, 400, 400, 1000, 1000)
    s.mask_op_top_view(63, layers=[5, 20])
    from hlayout.core.maskops import shape_to_shapely
    from shapely.ops import unary_union
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    drawn = unary_union([shape_to_shapely(p).buffer(0) for p in paths])
    # A point on layer 5's own boundary that lies STRICTLY inside layer
    # 20's filled area (e.g. (500, 600), the middle of the occluded top
    # edge) must not be part of the drawn result.
    hidden_point = shapely.geometry.Point(500, 600)
    assert not drawn.buffer(1).contains(hidden_point)
    # ...but a point on layer 5's boundary OUTSIDE layer 20's footprint
    # (e.g. (0, 300), the middle of the untouched left edge) must be.
    visible_point = shapely.geometry.Point(0, 300)
    assert drawn.buffer(5).contains(visible_point)


def test_top_view_default_ignores_fired_film_thickness_entirely():
    # A top-down occlusion view has no use for a layer's THICKNESS
    # (unlike export_freecad()'s 3D stack, which needs a Z-height) --
    # a layer with real drawn geometry is considered regardless of
    # whether it has a fired-film thickness saved in Setup at all.
    s = _fresh_session()
    s.rect(5, 0, 0, 100, 100)
    s.rect(6, 200, 200, 300, 300)  # no thickness saved anywhere -- still included
    written = s.mask_op_top_view(63)
    assert written == {"TOP": 2}  # both layer 5's and layer 6's own outlines
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    xs = [p[0] for path in paths for p in path.points]
    assert max(xs) == 300  # layer 6's box (up to x=300) is present


def test_top_view_default_excludes_a_hidden_or_unfilled_layer():
    s = _fresh_session()
    s.rect(5, 0, 0, 100, 100)
    s.rect(6, 200, 200, 300, 300)
    s.library.layers[6].visible = False
    written = s.mask_op_top_view(63)
    assert written == {"TOP": 1}  # only layer 5 -- layer 6 is hidden
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    xs = [p[0] for path in paths for p in path.points]
    assert max(xs) == 100


def test_top_view_raises_without_any_layers_to_composite():
    s = _fresh_session()
    with pytest.raises(LayoutError, match="no layers to composite"):
        s.mask_op_top_view(63, layers=[])


def test_top_view_writes_paths_not_polygons():
    s = _fresh_session()
    s.rect(5, 0, 0, 100, 100)
    s.mask_op_top_view(63, layers=[5])
    result = [sh for sh in _tv(s).shapes if sh.layer == 63]
    assert result and all(isinstance(sh, Path) for sh in result)


def test_top_view_line_width_sets_the_drawn_path_width():
    s = _fresh_session()
    s.rect(5, 0, 0, 100, 100)
    s.mask_op_top_view(63, layers=[5], line_width=250)
    result = [sh for sh in _tv(s).shapes if sh.layer == 63]
    assert all(sh.width == 250 for sh in result)


def test_cross_hatch_lines_stays_within_the_region_and_is_non_empty():
    from hlayout.core.maskops import cross_hatch_lines
    region = shapely.geometry.box(0, 0, 1000, 500)
    result = cross_hatch_lines(region, 200)
    assert not result.is_empty
    minx, miny, maxx, maxy = result.bounds
    assert minx >= 0 and miny >= 0 and maxx <= 1000 and maxy <= 500


def test_cross_hatch_lines_empty_region_or_pitch_gives_empty_result():
    from hlayout.core.maskops import cross_hatch_lines
    region = shapely.geometry.box(0, 0, 1000, 500)
    assert cross_hatch_lines(shapely.geometry.Polygon(), 200).is_empty
    assert cross_hatch_lines(region, 0).is_empty
    assert cross_hatch_lines(region, 200, angles=()).is_empty


def test_cross_hatch_lines_supports_arbitrary_angles():
    from hlayout.core.maskops import cross_hatch_lines
    region = shapely.geometry.box(0, 0, 1000, 1000)
    result_60 = cross_hatch_lines(region, 100, angles=(60, -60))
    assert not result_60.is_empty
    minx, miny, maxx, maxy = result_60.bounds
    assert minx >= 0 and miny >= 0 and maxx <= 1000 and maxy <= 1000
    # a single-angle hatch (one direction only) is also supported
    result_single = cross_hatch_lines(region, 100, angles=(30,))
    assert not result_single.is_empty
    # different angle sets produce genuinely different line geometry
    result_45 = cross_hatch_lines(region, 100, angles=(45, -45))
    assert not result_45.equals(result_60)


def test_top_view_hatch_conductor_angles_produces_a_different_pattern_than_default():
    from hlayout.io.techfile import save_resistor_geometry_rules

    def _make(**hatch_kwargs):
        s = _fresh_session()
        save_resistor_geometry_rules(s.library, _resistor_rules_conductor_only())
        s.rect(3, 0, 0, 1000, 1000)
        s.mask_op_top_view(63, hatch_conductor_pitch=100, **hatch_kwargs)
        return [sh for sh in _tv(s).shapes if sh.layer == 63]

    default_paths = _make()
    angled_paths = _make(hatch_conductor_angles=(60, -60))
    # both produce a real hatch fill, but at different angles the exact
    # set of drawn segments differs
    assert len(default_paths) > 4 and len(angled_paths) > 4
    assert {tuple(p.points) for p in default_paths} != {tuple(p.points) for p in angled_paths}


def _resistor_rules_conductor_only(conductor_layer=3):
    return {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": -1, "conductor_layer": conductor_layer,
        "glaze_layer": -1, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    }


def test_top_view_hatch_conductor_pitch_fills_conductor_with_a_cross_hatch():
    from hlayout.io.techfile import save_resistor_geometry_rules

    s1 = _fresh_session()
    save_resistor_geometry_rules(s1.library, _resistor_rules_conductor_only())
    s1.rect(3, 0, 0, 1000, 800)
    without_hatch = s1.mask_op_top_view(63)

    s2 = _fresh_session()
    save_resistor_geometry_rules(s2.library, _resistor_rules_conductor_only())
    s2.rect(3, 0, 0, 1000, 800)
    with_hatch = s2.mask_op_top_view(63, hatch_conductor_pitch=100)

    # hatching adds a lot of extra line segments beyond the plain outline
    assert with_hatch["TOP"] > without_hatch["TOP"] * 5
    # every hatch/outline point must stay within the conductor's own bounds
    paths = [sh for sh in _tv(s2).shapes if sh.layer == 63]
    xs = [p[0] for path in paths for p in path.points]
    ys = [p[1] for path in paths for p in path.points]
    assert min(xs) >= 0 and max(xs) <= 1000 and min(ys) >= 0 and max(ys) <= 800


def test_top_view_hatch_conductor_pitch_ignored_with_explicit_layers():
    from hlayout.io.techfile import save_resistor_geometry_rules

    s1 = _fresh_session()
    save_resistor_geometry_rules(s1.library, _resistor_rules_conductor_only())
    s1.rect(3, 0, 0, 1000, 800)
    without = s1.mask_op_top_view(63, layers=[3])

    s2 = _fresh_session()
    save_resistor_geometry_rules(s2.library, _resistor_rules_conductor_only())
    s2.rect(3, 0, 0, 1000, 800)
    with_ignored = s2.mask_op_top_view(63, layers=[3], hatch_conductor_pitch=100)

    assert without == with_ignored  # hatch_conductor_pitch has no effect here


def test_top_view_command_bar():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    s.rect(5, 0, 0, 600, 600)
    s.rect(20, 400, 400, 1000, 1000)
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("TOPVIEW result=63 layers=5,20")
    assert "wrote" in out and "path(s) to layer 63" in out
    assert any(sh.layer == 63 for sh in _tv(s).shapes)


def test_top_view_command_bar_reports_no_output():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("TOPVIEW result=63 layers=5")
    assert "no output" in out


def test_top_view_command_bar_requires_result():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("TOPVIEW layers=5")
    assert "error" in out.lower()


def _min_dist_to_paths(point, paths):
    return min(shapely.geometry.Point(point).distance(shapely.geometry.LineString(p.points))
               for p in paths)


def _rings_from_lines(lines):
    """Polygonize a set of (possibly fragmented) LineStrings back into
    closed rings, for asserting an occluded entry's own outline forms a
    genuinely closed shape rather than a dangling open arc."""
    from shapely.ops import polygonize
    return list(polygonize(lines))


# --- hidden_line_top_view(): occluded outlines must be CLOSED rings --------

def test_hidden_line_top_view_occluded_entry_forms_a_closed_notched_ring():
    # Regression: subtracting a higher entry's covered AREA from a lower
    # entry's boundary LINE leaves an open, dangling arc -- correct as
    # far as total ink drawn (the higher entry's own outline happens to
    # trace the missing piece), but the lower entry's own contribution
    # isn't a closed shape by itself. It must be: subtracting from the
    # FILLED region first, then taking that region's boundary, produces
    # one closed ring with a notch cut where the higher entry covers it.
    from hlayout.core.maskops import hidden_line_top_view
    conductor = shapely.geometry.box(0, 0, 600, 600)
    resistor = shapely.geometry.box(400, 400, 1000, 1000)
    result = hidden_line_top_view([(resistor, True), (conductor, True)])
    rings = _rings_from_lines(list(result.geoms))
    # the L-shaped notched conductor area (600x600 minus the 200x200
    # corner overlap) must polygonize back into a closed ring of exactly
    # that area -- impossible if conductor's own contribution were an
    # open arc with no closing edge along the notch.
    expected_area = 600 * 600 - 200 * 200
    assert any(abs(r.area - expected_area) < 1 for r in rings)


def test_hidden_line_top_view_component_notch_in_conductor_is_closed():
    from hlayout.core.maskops import hidden_line_top_view
    conductor = shapely.geometry.box(0, 0, 600, 600)
    component = shapely.geometry.box(100, 100, 500, 650)
    result = hidden_line_top_view([(component, True), (conductor, True)])
    rings = _rings_from_lines(list(result.geoms))
    expected_area = conductor.difference(component).area
    assert any(abs(r.area - expected_area) < 1 for r in rings)


# --- TOPVIEW default (layers=None): fixed role-based stacking order --------

def test_top_view_role_order_resistor_occludes_conductor_regardless_of_layer_number():
    # conductor_layer (50) is a HIGHER number than resistor_layer (5) --
    # deliberately backwards from plain layer-number order, to prove the
    # default role-based stacking (components, then resistor, then
    # conductor, then substrate) wins over layer number.
    from hlayout.io.techfile import save_layer_thickness, save_resistor_geometry_rules
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": 5, "conductor_layer": 50,
        "glaze_layer": -1, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    })
    save_layer_thickness(s.library, {"50": 12.0, "5": 15.0})
    s.rect(50, 0, 0, 600, 600)      # conductor
    s.rect(5, 400, 400, 1000, 1000)  # resistor, overlapping conductor's corner
    s.mask_op_top_view(63)
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    # conductor's own top edge, at a point strictly inside the resistor's
    # filled footprint, must be genuinely absent (not just "drawn under").
    assert _min_dist_to_paths((500, 600), paths) > 10
    # resistor's own outline is never occluded by anything (nothing sits
    # above it), so its far corner must still be exactly on the result.
    assert _min_dist_to_paths((1000, 700), paths) < 1
    # conductor's edge OUTSIDE the resistor's footprint is untouched.
    assert _min_dist_to_paths((0, 300), paths) < 1


def test_top_view_role_order_glaze_is_a_non_occluding_outline():
    # Glaze fully covers both conductor and resistor here -- it must
    # still show its own full outline (nothing hides it), AND it must
    # not hide either layer underneath it (it's a non-occluding overlay).
    from hlayout.io.techfile import save_layer_thickness, save_resistor_geometry_rules
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": 10, "conductor_layer": 5,
        "glaze_layer": 20, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    })
    save_layer_thickness(s.library, {"5": 12.0, "10": 15.0, "20": 20.0})
    s.rect(5, 0, 0, 1000, 1000)     # conductor
    s.rect(10, 400, 400, 800, 800)  # resistor, inside conductor
    s.rect(20, 200, 200, 900, 900)  # glaze, covering both
    s.mask_op_top_view(63)
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    # glaze's own corners, deep inside conductor+resistor's filled area,
    # are still fully present -- glaze is never occluded.
    assert _min_dist_to_paths((200, 200), paths) < 1
    assert _min_dist_to_paths((900, 900), paths) < 1
    # resistor's outline, though fully covered by glaze, is NOT hidden by
    # it (glaze doesn't occlude what's underneath).
    assert _min_dist_to_paths((400, 600), paths) < 1


def test_top_view_role_order_unclassified_other_layer_cannot_occlude_conductor():
    # Regression: an "other" (unclassified) layer with a HIGHER layer
    # number than conductor must not occlude conductor -- lumping every
    # unclassified layer into the same occlusion tier as conductor
    # reproduced the exact number-ordering bug role-based stacking
    # exists to avoid, one tier down.
    from hlayout.io.techfile import save_resistor_geometry_rules
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": -1, "conductor_layer": 3,
        "glaze_layer": -1, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    })
    s.rect(3, 0, 0, 600, 600)        # conductor, an EXPLICITLY classified role
    s.rect(58, 400, 400, 1000, 1000)  # an unclassified "other" layer, HIGHER number
    s.mask_op_top_view(63)
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    # conductor's top edge, strictly inside the "other" layer's filled
    # footprint, must survive -- an unclassified layer cannot occlude it.
    assert _min_dist_to_paths((500, 600), paths) < 1
    # the "other" layer's own outline is also fully present (nothing
    # above IT in this design).
    assert _min_dist_to_paths((1000, 700), paths) < 1


def test_top_view_role_order_components_and_epoxy_occlude_conductor():
    # Component recognition is by NAME (a known EIA size code, matching
    # Session.match_freecad_components()'s own matcher) or FOOTPRINT, not
    # by which layer the cell draws on -- so the cell must be named like
    # a real part (e.g. CAP_1210) for this test to exercise it honestly.
    from hlayout.io.techfile import save_layer_thickness, save_resistor_geometry_rules
    s = _fresh_session()
    s.new_cell("CAP_1210")
    s.rect(61, 0, 0, 400, 550)  # non-film outline/epoxy footprint
    s.use_cell("TOP")
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": -1, "conductor_layer": 50,
        "glaze_layer": -1, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    })
    save_layer_thickness(s.library, {"50": 12.0})
    s.rect(50, 0, 0, 600, 600)
    s.sref("CAP_1210", (100, 100))  # component footprint now (100,100)-(500,650)
    s.mask_op_top_view(63)
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    # the component's own full outline is present...
    assert _min_dist_to_paths((100, 100), paths) < 1
    assert _min_dist_to_paths((500, 650), paths) < 1
    # ...and conductor's top edge, strictly inside the component's
    # footprint, is genuinely gone.
    assert _min_dist_to_paths((300, 600), paths) > 10
    # conductor's edge outside the component is untouched.
    assert _min_dist_to_paths((0, 300), paths) < 1


def test_top_view_wire_bond_is_not_clipped_by_the_component_it_bonds_to():
    # A wire bond terminates ON a component's own pad, at the surface --
    # it doesn't run buried underneath the component the way a real
    # conductor trace does. Confirmed live: lumping the Wire Bond layer
    # into the occluding conductor tier left a bond wire visibly
    # stopping short of the pad it actually reaches, clipped by the very
    # component it's bonded to. It must be non-occluding, same as glaze.
    from hlayout.io.techfile import save_bond_rules, save_layer_thickness, \
        save_resistor_geometry_rules
    s = _fresh_session()
    s.new_cell("CAP_1210")
    s.rect(61, 0, 0, 400, 550)  # component footprint (non-film outline)
    s.use_cell("TOP")
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": -1, "conductor_layer": 50,
        "glaze_layer": -1, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    })
    save_layer_thickness(s.library, {"50": 12.0})
    save_bond_rules(s.library, {"layer": 59, "width": 30, "pad": 100,
                                 "min_length": 300, "max_length": 5000})
    s.sref("CAP_1210", (100, 100))  # component footprint now (100,100)-(500,650)
    # a bond wire running from outside the component to well INSIDE its
    # footprint -- as if landing on the die's own pad. Horizontal with a
    # flush cap, so its own stroke edge is a plain straight line at
    # y=315 (centreline y=300, +half the 30-wide stroke).
    s.path(59, [(0, 300), (300, 300)], width=30, cap="flush")
    written = s.mask_op_top_view(63)
    assert written
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    # the wire's own top edge, at x=250 -- deep inside the component's
    # footprint (which starts at x=100) -- is still fully present, not
    # clipped at the component's edge.
    assert _min_dist_to_paths((250, 315), paths) < 1


def test_top_view_component_recognition_needs_no_layer_thickness_at_all():
    # Component recognition (by EIA-size name here) works with NOTHING
    # saved in layer_thickness anywhere in the design -- it has no
    # bearing on this at all, unlike is_component_cell()'s film/non-film
    # split (used elsewhere, by the 3D FreeCAD export).
    from hlayout.io.techfile import save_resistor_geometry_rules
    s = _fresh_session()
    s.new_cell("CAP_1210")
    s.rect(61, 0, 0, 400, 550)
    s.use_cell("TOP")
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": -1, "conductor_layer": 50,
        "glaze_layer": -1, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    })
    s.rect(50, 0, 0, 600, 600)
    s.sref("CAP_1210", (100, 100))
    written = s.mask_op_top_view(63)
    assert written  # produced output despite no layer_thickness ever saved
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    assert _min_dist_to_paths((100, 100), paths) < 1
    assert _min_dist_to_paths((300, 600), paths) > 10  # conductor cut under the cap


def test_top_view_bare_die_recognized_by_outline_not_role_layers():
    # Per user direction: a bare semiconductor die (built the same way
    # Tools > Chip Wizard.../Session.create_chip_cell() does -- a body-
    # outline path plus an epoxy die-attach pad, neither one a resistor/
    # conductor/glaze film) is a real physical part regardless of its
    # actual size (could be 0.2 x 0.2mm, could be 1.8 x 1.8mm) -- so
    # there's no size catalogue to match against. It's recognized here
    # simply because ALL of its own shapes sit on layers the design
    # doesn't use for any resistor/conductor/glaze/route/bond/dielectric
    # role -- exactly what a bare outline/epoxy pad is. A real chip/die
    # cell also always has its pins placed afterwards as separate SRef
    # instances (create_chip_cell()'s own convention), which is what
    # tells it apart from the design's own childless substrate cell
    # (see test_top_view_substrate_cell_with_no_children_is_not_a_component).
    from hlayout.io.techfile import save_resistor_geometry_rules
    s = _fresh_session()
    s.new_cell("chip_pin")
    s.rect(58, -20, -20, 20, 20)
    s.new_cell("ML1")
    s.path(60, [(0, 0), (400, 0), (400, 400), (0, 400), (0, 0)], 20, cap="flush")  # outline
    s.rect(61, 100, 100, 300, 300)  # epoxy die-attach pad
    s.sref("chip_pin", (200, 400), pin="1")  # a die pin, placed separately
    s.use_cell("TOP")
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": -1, "conductor_layer": 3,
        "glaze_layer": -1, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    })
    s.rect(3, 0, 0, 5000, 5000)  # conductor under the whole die
    s.sref("ML1", (0, 0))
    written = s.mask_op_top_view(63)
    assert written
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    # the die's own outline notches a real hole into the conductor --
    # its own right edge (the stroked path's outer edge, half the 20-wide
    # stroke beyond the 400 nominal) is present, and conductor strictly
    # inside its footprint is genuinely gone.
    assert _min_dist_to_paths((410, 200), paths) < 1
    assert _min_dist_to_paths((200, 200), paths) > 10
    assert _min_dist_to_paths((5000, 5000), paths) < 1  # conductor's own corner present


def test_top_view_substrate_cell_with_no_children_is_not_a_component():
    # Regression: the design's own substrate cell -- a single shape on
    # its own outline/marking layer, same as a die's outline, but with
    # NO children of its own -- is structurally indistinguishable from
    # a bare die by "all shapes non-role" alone. Confirmed live: left
    # unguarded, that test alone wrongly re-caught the substrate,
    # resurrecting the exact substrate-occludes-everything regression
    # this whole recognition scheme exists to avoid (in a NEW form, via
    # the non-role-layer path this time rather than layer thickness).
    # Requiring at least one child SRef/ARef -- a real chip/die cell
    # always gets its pins placed afterwards as separate instances,
    # per create_chip_cell()'s own convention -- is what tells them
    # apart: the substrate never has any children at all.
    from hlayout.io.techfile import save_resistor_geometry_rules
    s = _fresh_session()
    s.new_cell("Substrate")
    s.rect(61, 0, 0, 5000, 5000)  # board-sized shape, on a non-role layer, no children
    s.use_cell("TOP")
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": -1, "conductor_layer": 3,
        "glaze_layer": -1, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    })
    s.rect(3, 500, 500, 900, 900)  # conductor that must remain visible
    s.sref("Substrate", (0, 0))
    written = s.mask_op_top_view(63)
    assert written
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    # the substrate is walked into like any other non-component cell,
    # so the conductor beneath it still shows up in the result.
    assert _min_dist_to_paths((700, 900), paths) < 1


def test_top_view_device_cell_mixing_role_and_non_role_layers_is_not_swallowed():
    # Regression: the earlier keep-out-layer bug (a resistor device cell
    # wrongly became "a component" because it ALSO happened to draw on
    # a non-role keep-out layer) must not resurface here in a new form.
    # A cell that draws BOTH role-layer content (its own conductor pad)
    # AND a non-role shape (an incidental keep-out margin) is walked
    # into, not recognized -- requiring ALL of a cell's own shapes to be
    # non-role (not just ANY) is what tells a real bare part (outline +
    # epoxy pad, nothing else) apart from an ordinary device cell that
    # merely has some non-role decoration alongside its real role-layer
    # content.
    from hlayout.io.techfile import save_resistor_geometry_rules
    s = _fresh_session()
    s.new_cell("R1")
    s.rect(3, 0, 0, 100, 100)        # conductor pad -- role-layer content
    s.rect(7, -300, -300, 400, 400)  # an incidental, non-role keep-out margin
    s.use_cell("TOP")
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 200, "resistor_layer": -1, "conductor_layer": 3,
        "glaze_layer": -1, "doc_layer": -1, "keepout_layer": 7, "ink_layers": {},
    })
    # a trace well outside R1's own body, but squarely inside its
    # oversized (would-be) keep-out rectangle.
    s.rect(3, 260, 260, 290, 290)
    s.sref("R1", (0, 0))
    written = s.mask_op_top_view(63)
    assert written
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    # the nearby trace is untouched -- R1 was walked into, not treated
    # as an occluding component with an oversized keep-out footprint.
    assert _min_dist_to_paths((275, 260), paths) < 1


def test_top_view_exclude_layers_stops_a_sealed_package_case_from_occluding_everything():
    # Confirmed live: a sealed hybrid package's own outer case/lid (a
    # 20-pin flatpack, outline + an ARef of external header pins) is
    # recognized the same way a small bare die is -- graphics-only with
    # children -- but its outline can legitimately span the ENTIRE
    # board (it physically does sit above everything inside it), so it
    # swallowed every real component and all conductor content inside
    # it. Real repro: exclude_layers=[<the case's own outline layer>]
    # is the way out -- a graphics-only candidate whose own shapes are
    # ALL on an excluded layer is walked into instead of recognized,
    # the same escape hatch exclude_layers already gives every other
    # tier.
    from hlayout.io.techfile import save_resistor_geometry_rules
    s = _fresh_session()
    s.new_cell("header_pin")
    s.rect(58, -20, -20, 20, 20)
    s.new_cell("PKG_20")
    s.rect(61, -3400, -3400, 26000, 24600)  # the case's own huge outline
    for i in range(4):  # a few header pins, enough to give it children
        s.sref("header_pin", (i * 2000, 0), pin=str(i + 1))
    s.use_cell("TOP")
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": -1, "conductor_layer": 3,
        "glaze_layer": -1, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    })
    s.rect(3, 5000, 5000, 15000, 15000)  # conductor, well inside the case's footprint
    s.sref("PKG_20", (0, 0))

    # A case spanning the whole design is now recognised as an ENCLOSING
    # package and drawn at the bottom of the stack (under everything),
    # so even without exclude= the conductor inside it shows, and the
    # case's own outer edge is still drawn.
    written = s.mask_op_top_view(63)
    assert written
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    assert _min_dist_to_paths((10000, 15000), paths) < 1
    assert _min_dist_to_paths((26000, 10000), paths) < 1

    # exclude=61 (the case's own outline layer): the conductor is back.
    written2 = s.mask_op_top_view(62, exclude_layers=[61])
    assert written2
    paths2 = [sh for sh in _tv(s).shapes if sh.layer == 62]
    assert _min_dist_to_paths((10000, 15000), paths2) < 1


def test_top_view_component_footprint_hole_from_a_stroked_outline_path_is_filled():
    # Regression: a component's body outline is often a thin PATH (a
    # stroked ring, not a filled area -- see
    # hlayout/examples/generate_ceramic_caps.py's OUTLINE_LAYER), which
    # shape_to_shapely() correctly turns into a "picture frame" polygon
    # WITH A HOLE for real mask-op purposes. Used directly as an
    # occluding footprint, that hole made the hidden-line result show
    # BOTH the frame's outer edge and its inner hole edge -- two
    # concentric, disconnected squares instead of one solid component
    # silhouette.
    from hlayout.io.techfile import save_resistor_geometry_rules
    s = _fresh_session()
    s.new_cell("CAP_1210")
    s.path(61, [(0, 0), (400, 0), (400, 400), (0, 400), (0, 0)], 50, cap="flush")
    s.rect(20, -50, 100, 50, 300)    # epoxy pad, end 1
    s.rect(20, 350, 100, 450, 300)   # epoxy pad, end 2
    s.use_cell("TOP")
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": -1, "conductor_layer": 5,
        "glaze_layer": -1, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    })
    s.sref("CAP_1210", (0, 0))
    from hlayout.io.techfile import load_component_models
    from hlayout.scripting.api import _matched_component_footprint
    geom = _matched_component_footprint(s.library, "TOP", load_component_models(s.library))
    assert geom.geom_type == "Polygon"
    assert len(geom.interiors) == 0, "component footprint must have no interior hole"


def test_top_view_resistor_device_cell_with_extra_layers_is_not_a_component():
    # Regression: a resistor DEVICE cell can legitimately draw on more
    # layers than just resistor_layer/conductor_layer/glaze_layer (e.g.
    # a doc or keep-out sub-layer). Component recognition is by NAME (a
    # known EIA size code) or FOOTPRINT (a known pin pattern), not by
    # which layer a cell draws on and NOT by layer thickness (confirmed
    # here by never saving any layer_thickness at all) -- so a resistor
    # device cell named "R1_DEVICE", matching neither, is correctly
    # walked into and contributes its own layers to the ordinary tiers
    # instead of being swallowed into a wrongly-topmost "component"
    # footprint.
    from hlayout.io.techfile import save_resistor_geometry_rules
    s = _fresh_session()
    s.new_cell("R1_DEVICE")
    s.rect(3, 0, 0, 100, 100)      # conductor pad
    s.rect(4, 150, 0, 250, 20)     # extra resistor sub-layer, off to the
                                    # side so it isn't simply covered by
                                    # the conductor pad above -- NOT a
                                    # Setup role layer
    s.rect(19, 0, 0, 100, 100)     # glaze
    s.use_cell("TOP")
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": 30, "conductor_layer": 3,
        "glaze_layer": 19, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    })
    s.rect(3, 500, 500, 900, 900)  # the board's own conductor, apart from R1
    s.sref("R1_DEVICE", (0, 0))
    s.mask_op_top_view(63)
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    # R1_DEVICE's own layer-4 shape (150,0)-(250,20) must show up as
    # part of the ordinary "conductor and other" tier's output, not be
    # swallowed into a wrongly-topmost "component" footprint that never
    # gets composited at all.
    assert _min_dist_to_paths((200, 20), paths) < 1


def test_top_view_exclude_layers_drops_a_layer_from_the_role_based_default():
    from hlayout.io.techfile import save_resistor_geometry_rules
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": 30, "conductor_layer": 3,
        "glaze_layer": 19, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    })
    s.rect(3, 0, 0, 500, 500)
    s.rect(7, 600, 600, 900, 900)  # an unrelated "other" layer, off to the side
    written = s.mask_op_top_view(63, exclude_layers=[7])
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    # layer 7 must not appear anywhere in the output...
    assert _min_dist_to_paths((750, 750), paths) > 10
    # ...but conductor (not excluded) is unaffected.
    assert _min_dist_to_paths((0, 0), paths) < 1


def test_top_view_exclude_layers_ignored_when_layers_given_explicitly():
    from hlayout.io.techfile import save_resistor_geometry_rules
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": 30, "conductor_layer": 3,
        "glaze_layer": 19, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    })
    s.rect(3, 0, 0, 500, 500)
    written = s.mask_op_top_view(63, layers=[3], exclude_layers=[3])
    assert written  # exclude_layers has no effect once layers= is given explicitly


def test_top_view_explicit_layers_bypasses_role_order():
    # Passing layers= explicitly reverts to the older, simpler behaviour:
    # every given layer occludes equally, ordered by plain layer number.
    from hlayout.io.techfile import save_resistor_geometry_rules
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200, "glaze_oversize": 50,
        "keepout_oversize": 0, "resistor_layer": 5, "conductor_layer": 50,
        "glaze_layer": -1, "doc_layer": -1, "keepout_layer": -1, "ink_layers": {},
    })
    s.rect(50, 0, 0, 600, 600)       # "conductor" -- but higher layer number
    s.rect(5, 400, 400, 1000, 1000)  # "resistor" -- but lower layer number
    s.mask_op_top_view(63, layers=[5, 50])
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    # With plain layer-number order, layer 50 (conductor) is on top, so
    # ITS outline survives in full and layer 5's edge under it is cut --
    # the opposite of the role-based default above.
    assert _min_dist_to_paths((600, 600), paths) < 1  # layer 50's own corner
    assert _min_dist_to_paths((400, 500), paths) > 10  # layer 5's left edge, under layer 50, gone


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


# --- top view writes to its own TOPVIEW cell, never the viewed cell --------

def test_top_view_writes_to_a_topview_cell_not_the_viewed_cell():
    s = _fresh_session()
    s.rect(5, 0, 0, 100, 100)
    before = list(s.cell.shapes)
    written = s.mask_op_top_view(63, layers=[5])
    assert written == {"TOP": 1}
    assert s.cell.name == "TOP"                 # current cell unchanged
    assert s.cell.shapes == before              # nothing added to it
    assert [sh.layer for sh in _tv(s).shapes] == [63]


def test_top_view_rerun_replaces_the_previous_view():
    s = _fresh_session()
    s.rect(5, 0, 0, 100, 100)
    s.mask_op_top_view(63, layers=[5])
    first = len(_tv(s).shapes)
    s.mask_op_top_view(63, layers=[5])
    assert len(_tv(s).shapes) == first          # rebuilt, not appended


def test_top_view_placed_back_in_the_design_is_not_read_back_as_input():
    s = _fresh_session()
    s.rect(5, 0, 0, 100, 100)
    s.mask_op_top_view(63)
    first = len(_tv(s).shapes)
    s.library.layers[63].fill = True
    s.sref("TOPVIEW", (0, 0))                   # overlay it on the design
    s.mask_op_top_view(63)
    assert len(_tv(s).shapes) == first


def test_top_view_out_cell_name_and_one_cell_per_viewed_cell():
    s = _fresh_session()
    s.rect(5, 0, 0, 100, 100)
    s.mask_op_top_view(63, layers=[5], out_cell="ASSY_VIEW")
    assert "TOPVIEW" not in s.library.cells
    assert len(_tv(s, "ASSY_VIEW").shapes) == 1
    s.new_cell("B")
    s.rect(5, 0, 0, 50, 50)
    s.mask_op_top_view(63, layers=[5], cell_names=["TOP", "B"])
    assert len(_tv(s, "TOPVIEW_TOP").shapes) == 1
    assert len(_tv(s, "TOPVIEW_B").shapes) == 1


def test_top_view_of_its_own_output_cell_is_refused():
    s = _fresh_session()
    s.rect(5, 0, 0, 100, 100)
    s.mask_op_top_view(63, layers=[5])
    s.use_cell("TOPVIEW")
    with pytest.raises(LayoutError, match="own output cell"):
        s.mask_op_top_view(63, layers=[5])


def test_top_view_is_one_undo_step_and_removes_the_new_cell():
    s = _fresh_session()
    s.rect(5, 0, 0, 100, 100)
    s.mask_op_top_view(63, layers=[5])
    assert "TOPVIEW" in s.library.cells
    s.undo()
    assert "TOPVIEW" not in s.library.cells


def test_top_view_command_bar_out_option():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    s.rect(5, 0, 0, 100, 100)
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("TOPVIEW result=63 layers=5 out=VIEW1")
    assert "of cell VIEW1" in out
    assert len(_tv(s, "VIEW1").shapes) == 1


def test_top_view_substrate_on_a_hidden_layer_still_shows():
    s = _fresh_session()
    s.library.layers.set_style(57, name="substrate", visible=False, fill=False)
    s.rect(57, 0, 0, 20000, 10000)
    s.rect(3, 2000, 2000, 4000, 4000)
    assert s.top_view_layer_roles().get(57) == "substrate"
    s.mask_op_top_view(63)
    paths = [sh for sh in _tv(s).shapes if sh.layer == 63]
    assert _min_dist_to_paths((20000, 5000), paths) < 1      # substrate edge drawn


def test_top_view_enclosing_package_drawn_in_detail_with_pins_and_well():
    s = _fresh_session()
    s.new_cell("LEAD")
    s.circle(63, (0, 0), 300, segments=32)
    s.circle(49, (0, 0), 800, segments=32)
    s.new_cell("CAN")
    s.rect(61, -3400, -3400, 26000, 24600)       # case
    s.rect(61, -2100, -1700, 24900, 22900)       # well
    for i in range(3):
        s.sref("LEAD", (i * 2540, 0), pin=str(i + 1))
    s.use_cell("TOP")
    s.library.layers.set_style(57, name="substrate")
    s.rect(57, 0, 5000, 20000, 15000)
    s.sref("CAN", (0, 0))
    s.mask_op_top_view(62)
    paths = [sh for sh in _tv(s).shapes if sh.layer == 62]
    assert _min_dist_to_paths((300, 0), paths) < 5        # pin 1 itself
    assert _min_dist_to_paths((800, 0), paths) < 5        # its glass seal
    assert _min_dist_to_paths((-2100, 2000), paths) < 1   # the well wall
    assert _min_dist_to_paths((-3400, 2000), paths) < 1   # the case edge
    assert _min_dist_to_paths((10000, 5000), paths) < 1   # substrate edge on top


def test_fracture_keeps_the_fill_in_few_compact_hole_free_pieces():
    """A sheet with a grid of via windows fractures into hole-free
    pieces within the vertex limit that cover it exactly -- cut through
    the holes and joined back up, so far fewer pieces than holes and no
    thin slivers."""
    pytest.importorskip("shapely")
    from shapely.geometry import box
    from shapely.ops import unary_union
    from hlayout.core.maskops import fracture_polygons
    sheet = box(0, 0, 40000, 20000)
    holes = [box(x - 300, y - 300, x + 300, y + 300)
             for x in range(2000, 40000, 3000) for y in range(2000, 20000, 3000)]
    geom = sheet.difference(unary_union(holes))
    pieces = fracture_polygons(geom, max_points=200)
    assert all(p.geom_type == "Polygon" and not p.interiors for p in pieces)
    assert all(len(p.exterior.coords) - 1 <= 200 for p in pieces)
    assert abs(unary_union(pieces).area - geom.area) < 1
    assert abs(sum(p.area for p in pieces) - geom.area) < 1          # no overlaps
    assert len(pieces) < len(holes) / 2
    assert min(2 * p.area / p.length for p in pieces) > 500           # nothing thin
