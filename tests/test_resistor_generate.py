import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.errors import LayoutError
from hlayout.core.shapes import Path, Rectangle, Text
from hlayout.io.techfile import (
    load_resistor_geometry_rules,
    resistor_ink_layer,
    save_resistor_geometry_rules,
)
from hlayout.scripting import Session


def _fresh_session():
    s = Session.new("RESGENTEST")
    s.new_cell("TOP")
    s.clear_undo_history()
    return s


def _device_shapes(s, parent=None):
    """Resolve the most recently placed resistor's Sref back to its
    device cell and return that cell's shapes. generate_resistor() now
    builds each resistor as its own cell + a single Sref placed in the
    calling cell, instead of drawing raw shapes directly into it."""
    parent = parent if parent is not None else s.cell
    sref = parent.srefs[-1]
    return s.library.cells[sref.cell_name].shapes


def test_generate_resistor_draws_ink_glaze_and_two_pads_axis_aligned():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 100.0, "pad_b": 300.0, "space_c": 50.0, "glaze_oversize": 0.0,
        "resistor_layer": 10, "conductor_layer": 11, "glaze_layer": 12,
    })
    s.generate_resistor(0, 0, body_width=200.0, body_length=1000.0)

    assert len(s.cell.srefs) == 1
    shapes = _device_shapes(s)
    assert len(shapes) == 4
    rects = [sh for sh in shapes if isinstance(sh, Rectangle)]
    assert len(rects) == 4

    ink = [r for r in rects if r.layer == 10]
    glaze = [r for r in rects if r.layer == 12]
    pads = [r for r in rects if r.layer == 11]
    assert len(ink) == 1
    assert len(glaze) == 1
    assert len(pads) == 2

    # Ink: length = body_length + 2*A = 1000 + 200 = 1200; width = 200.
    x0, y0, x1, y1 = ink[0].bbox()
    assert (x1 - x0) == 1200.0
    assert (y1 - y0) == 200.0

    # With glaze_oversize (D) = 0, the glaze exactly matches the ink's footprint.
    assert glaze[0].bbox() == ink[0].bbox()

    # Pads: length = A + B = 100 + 300 = 400; width = body_width + 2*C = 300.
    for pad in pads:
        px0, py0, px1, py1 = pad.bbox()
        assert (px1 - px0) == 400.0
        assert (py1 - py0) == 300.0

    # One pad on each side of centre: pad spans from the contact edge
    # (+-half_l = +-500) outward by A+B=400, i.e. to +-900.
    pad_x_ranges = sorted((p.bbox()[0], p.bbox()[2]) for p in pads)
    assert pad_x_ranges[0] == (-900.0, -500.0)   # left pad
    assert pad_x_ranges[1] == (500.0, 900.0)     # right pad
    # Ink extends A=100 beyond the contact edge into each pad's overlap zone.
    assert ink[0].bbox()[0] == -600.0
    assert ink[0].bbox()[2] == 600.0


def test_glaze_overprint_is_oversized_on_all_four_sides():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 100.0, "pad_b": 300.0, "space_c": 50.0, "glaze_oversize": 25.0,
        "resistor_layer": 10, "conductor_layer": 11, "glaze_layer": 12,
    })
    s.generate_resistor(0, 0, body_width=200.0, body_length=1000.0)

    shapes = _device_shapes(s)
    ink = next(sh for sh in shapes if isinstance(sh, Rectangle) and sh.layer == 10)
    glaze = next(sh for sh in shapes if isinstance(sh, Rectangle) and sh.layer == 12)
    ix0, iy0, ix1, iy1 = ink.bbox()
    gx0, gy0, gx1, gy1 = glaze.bbox()
    # Grown by D=25 on every side: both ends AND both long edges.
    assert gx0 == ix0 - 25.0
    assert gx1 == ix1 + 25.0
    assert gy0 == iy0 - 25.0
    assert gy1 == iy1 + 25.0


def test_generate_resistor_is_centred_at_the_given_point():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 50.0, "pad_b": 100.0, "space_c": 20.0,
        "resistor_layer": 0, "conductor_layer": 1, "glaze_layer": 2,
    })
    s.generate_resistor(500, -250, body_width=100.0, body_length=400.0)
    # The device cell is drawn at local origin; it's the Sref placement
    # in the parent cell that carries the requested (x, y).
    sref = s.cell.srefs[-1]
    assert sref.origin == (500, -250)
    shapes = _device_shapes(s)
    ink = next(sh for sh in shapes if sh.layer == 0)
    x0, y0, x1, y1 = ink.bbox()
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    assert abs(cx - 0) < 1e-9
    assert abs(cy - 0) < 1e-9


def test_generate_resistor_uses_explicit_overrides_over_saved_rules():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 999.0, "pad_b": 999.0, "space_c": 999.0, "glaze_oversize": 999.0,
        "resistor_layer": 5, "conductor_layer": 6, "glaze_layer": 9,
    })
    s.generate_resistor(0, 0, body_width=200.0, body_length=1000.0,
                         overlap_a=10.0, pad_b=20.0, space_c=5.0, glaze_oversize=0.0,
                         resistor_layer=40, conductor_layer=41, glaze_layer=42)
    shapes = _device_shapes(s)
    ink = next(sh for sh in shapes if isinstance(sh, Rectangle) and sh.layer == 40)
    x0, y0, x1, y1 = ink.bbox()
    assert (x1 - x0) == 1020.0  # 1000 + 2*10
    glaze = next(sh for sh in shapes if isinstance(sh, Rectangle) and sh.layer == 42)
    assert glaze.bbox() == ink.bbox()  # D overridden to 0 -- matches exactly
    pads = [sh for sh in shapes if isinstance(sh, Rectangle) and sh.layer == 41]
    assert len(pads) == 2
    for p in pads:
        px0, py0, px1, py1 = p.bbox()
        assert (px1 - px0) == 30.0  # 10 + 20


def test_generate_resistor_no_dielectric_keepout_by_default():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 100.0, "pad_b": 300.0, "space_c": 50.0, "glaze_oversize": 0.0,
        "resistor_layer": 10, "conductor_layer": 11, "glaze_layer": 12,
    })
    s.generate_resistor(0, 0, body_width=200.0, body_length=1000.0)
    # ink + glaze + 2 pads, and nothing else -- keepout_layer defaults to -1.
    assert len(_device_shapes(s)) == 4


def test_generate_resistor_draws_dielectric_keepout_between_terminals():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 100.0, "pad_b": 300.0, "space_c": 50.0, "glaze_oversize": 0.0,
        "keepout_oversize": 25.0,
        "resistor_layer": 10, "conductor_layer": 11, "glaze_layer": 12,
        "keepout_layer": 20,
    })
    s.generate_resistor(0, 0, body_width=200.0, body_length=1000.0)
    shapes = _device_shapes(s)
    assert len(shapes) == 5
    ko = next(sh for sh in shapes if isinstance(sh, Rectangle) and sh.layer == 20)
    x0, y0, x1, y1 = ko.bbox()
    # length = body_length + 2*E = 1000 + 50 ; the gap between the two
    # terminals (inner pad edges are at +-half_l = +-500) plus E each end.
    assert (x1 - x0) == 1050.0
    # width = body_width + 2*E = 200 + 50 (the ink width, NOT the wider
    # pad width -- space_c does not widen the keep-out)
    assert (y1 - y0) == 250.0
    # centred on the resistor, i.e. strictly inside the terminal gap +/- E
    assert x0 == -525.0 and x1 == 525.0
    assert y0 == -125.0 and y1 == 125.0


def test_generate_resistor_keepout_layer_arg_overrides_saved_rule():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 100.0, "pad_b": 300.0, "space_c": 50.0,
        "resistor_layer": 10, "conductor_layer": 11, "glaze_layer": 12,
        "keepout_layer": -1,
    })
    s.generate_resistor(0, 0, body_width=200.0, body_length=1000.0,
                        keepout_layer=21, keepout_oversize=0.0)
    shapes = _device_shapes(s)
    ko = [sh for sh in shapes if isinstance(sh, Rectangle) and sh.layer == 21]
    assert len(ko) == 1
    x0, _, x1, _ = ko[0].bbox()
    assert (x1 - x0) == 1000.0  # E=0 -> exactly the body_length gap


def test_generate_resistor_with_90_degree_rotation_places_sref_rotated():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 50.0, "pad_b": 100.0, "space_c": 20.0,
        "resistor_layer": 0, "conductor_layer": 1, "glaze_layer": 2,
    })
    s.generate_resistor(0, 0, body_width=100.0, body_length=400.0, rotation=90.0)

    # Rotation is now carried by the Sref itself; the device cell's own
    # ink shape stays an axis-aligned Rectangle at local (unrotated) size.
    sref = s.cell.srefs[-1]
    assert sref.rotation == 90.0

    shapes = _device_shapes(s)
    ink = next(sh for sh in shapes if sh.layer == 0)
    assert isinstance(ink, Rectangle)
    x0, y0, x1, y1 = ink.bbox()
    # Un-rotated in the device cell: long axis (length + 2*A = 500) along
    # X, short axis (width = 100) along Y -- the Sref applies the rotation
    # when the instance is drawn/expanded.
    assert abs((x1 - x0) - 500.0) < 1e-6
    assert abs((y1 - y0) - 100.0) < 1e-6


def test_generate_resistor_draws_as_a_single_undo_step():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 50.0, "pad_b": 100.0, "space_c": 20.0,
        "resistor_layer": 0, "conductor_layer": 1, "glaze_layer": 2,
    })
    s.generate_resistor(0, 0, body_width=100.0, body_length=400.0)
    assert len(s.cell.srefs) == 1
    assert s.undo() is True
    assert len(s.cell.srefs) == 0


# --- per-ink layer matching (resistor_ink_layer / ink_rsh) -----------------

def test_resistor_ink_layer_uses_map_entry_when_present():
    rules = {"resistor_layer": 30, "ink_layers": {"1000": 21, "100": 20}}
    assert resistor_ink_layer(rules, 1000) == 21
    assert resistor_ink_layer(rules, 100) == 20


def test_resistor_ink_layer_falls_back_when_rsh_unmapped_or_absent():
    rules = {"resistor_layer": 30, "ink_layers": {"1000": 21}}
    assert resistor_ink_layer(rules, 500) == 30       # not in the map
    assert resistor_ink_layer(rules, None) == 30       # no ink given at all


def test_generate_resistor_looks_up_ink_layer_by_sheet_resistance():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 50.0, "pad_b": 100.0, "space_c": 20.0,
        "resistor_layer": 30, "conductor_layer": 31, "glaze_layer": 32,
        "ink_layers": {"1000": 21, "100": 20},
    })
    s.generate_resistor(0, 0, body_width=100.0, body_length=400.0, ink_rsh=1000)
    shapes = _device_shapes(s)
    ink_layers_used = {sh.layer for sh in shapes if isinstance(sh, Rectangle)
                        and sh.layer not in (31, 32)}
    assert ink_layers_used == {21}


def test_generate_resistor_falls_back_to_default_ink_layer_for_unmapped_rsh():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 50.0, "pad_b": 100.0, "space_c": 20.0,
        "resistor_layer": 30, "conductor_layer": 31, "glaze_layer": 32,
        "ink_layers": {"1000": 21},
    })
    s.generate_resistor(0, 0, body_width=100.0, body_length=400.0, ink_rsh=999999)
    shapes = _device_shapes(s)
    ink_layers_used = {sh.layer for sh in shapes if isinstance(sh, Rectangle)
                        and sh.layer not in (31, 32)}
    assert ink_layers_used == {30}


def test_generate_resistor_explicit_resistor_layer_bypasses_ink_lookup():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 50.0, "pad_b": 100.0, "space_c": 20.0,
        "resistor_layer": 30, "conductor_layer": 31, "glaze_layer": 32,
        "ink_layers": {"1000": 21},
    })
    s.generate_resistor(0, 0, body_width=100.0, body_length=400.0,
                         ink_rsh=1000, resistor_layer=55)
    shapes = _device_shapes(s)
    ink_layers_used = {sh.layer for sh in shapes if isinstance(sh, Rectangle)
                        and sh.layer not in (31, 32)}
    assert ink_layers_used == {55}


def test_ink_layers_round_trip_through_geometry_rules():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 200, "pad_b": 500, "space_c": 200,
        "resistor_layer": 30, "conductor_layer": 31, "glaze_layer": 32,
        "ink_layers": {"10": 15, "1000000": 16},
    })
    rules = load_resistor_geometry_rules(s.library)
    assert rules["ink_layers"] == {"10": 15, "1000000": 16}


# --- reference designator ------------------------------------------------
# The ref-des is the placed instance's SRef.label -- the same
# per-instance floating annotation every other placed part gets --
# NOT a Text shape baked into the device cell.

def test_generate_resistor_ref_des_is_the_instance_label_not_a_baked_shape():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 100.0, "pad_b": 300.0, "space_c": 50.0, "glaze_oversize": 0.0,
        "resistor_layer": 10, "conductor_layer": 11, "glaze_layer": 12,
    })
    s.generate_resistor(0, 0, body_width=200.0, body_length=1000.0, ref_des="R1")

    shapes = _device_shapes(s)
    assert len(shapes) == 4                       # ink + glaze + 2 pads, no text
    assert not any(isinstance(sh, Text) for sh in shapes)
    assert s.cell.srefs[-1].label == "R1"         # ref-des lives on the instance


def test_generate_resistor_ignores_legacy_doc_layer_and_label_height():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 100.0, "pad_b": 300.0, "space_c": 50.0, "glaze_oversize": 0.0,
        "resistor_layer": 10, "conductor_layer": 11, "glaze_layer": 12, "doc_layer": 13,
    })
    s.generate_resistor(0, 0, body_width=200.0, body_length=1000.0, ref_des="R1",
                         doc_layer=13, label_height=100.0)
    assert not any(isinstance(sh, Text) for sh in _device_shapes(s))
    assert s.cell.srefs[-1].label == "R1"


def test_generate_resistor_without_ref_des_leaves_the_instance_label_empty():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 100.0, "pad_b": 300.0, "space_c": 50.0,
        "resistor_layer": 10, "conductor_layer": 11, "glaze_layer": 12,
    })
    s.generate_resistor(0, 0, body_width=200.0, body_length=1000.0)
    assert not any(isinstance(sh, Text) for sh in _device_shapes(s))
    assert s.cell.srefs[-1].label == ""


def test_resgen_ref_option_sets_the_instance_label():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 50.0, "pad_b": 100.0, "space_c": 20.0,
        "resistor_layer": 0, "conductor_layer": 1, "glaze_layer": 2,
    })
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("RESGEN 0,0 100 400 ref=R5")
    assert "generated resistor" in out
    assert not any(isinstance(sh, Text) for sh in _device_shapes(s))
    assert s.cell.srefs[-1].label == "R5"


# --- each resistor becomes its own reusable/uniquely-named cell -----------

def test_generate_resistor_creates_a_uniquely_named_cell_per_call():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 50.0, "pad_b": 100.0, "space_c": 20.0,
        "resistor_layer": 0, "conductor_layer": 1, "glaze_layer": 2,
    })
    name1 = s.generate_resistor(0, 0, body_width=100.0, body_length=400.0, ref_des="R1")
    name2 = s.generate_resistor(2000, 0, body_width=100.0, body_length=400.0, ref_des="R1")
    assert name1 != name2  # de-duplicated even with the same ref_des
    assert len(s.cell.srefs) == 2
    assert {sref.cell_name for sref in s.cell.srefs} == {name1, name2}
    # Two independent device cells were created in the library.
    assert name1 in s.library.cells
    assert name2 in s.library.cells


# --- Session.generate_resistor_table() / RESTABLE ---------------------------
# A resistor BOM table (REF DES / VALUE (OHMS) / TOL% / AIM VALUE /
# INK (OHMS/SQ)) built from the saved Thick-Film Resistor Designer rows,
# per user direction: generic and re-runnable, always rebuilding fresh
# from whatever the resistor list currently holds, so it can be placed
# alongside the assembly drawing for PRINT.

def _table_cells(cell):
    """{(x, y): text} for every Text shape in `cell` -- easy lookup by
    grid position regardless of shape order."""
    return {sh.origin: sh.text for sh in cell.shapes if isinstance(sh, Text)}


def test_generate_resistor_table_text_does_not_cross_the_grid_line_above_it():
    # Confirmed live: Text's own origin is its BOTTOM-left corner (see
    # hlayout.core.shapes.Text.bbox()), so a row's text glyphs extend
    # UPWARD from the row's nominal y by text_height. The row's own top
    # grid line must clear that -- it was instead placed using a
    # fraction of row_height with no relation to text_height at all,
    # so the default text_height (150) comfortably exceeded that
    # padding (row_height*0.3 = 75), and the grid line above every row
    # ran right through the text.
    from hlayout.io.techfile import save_resistor_list
    s = _fresh_session()
    save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 100.0, "tolerance": "5%", "power_mw": 100.0,
         "body_width": 1000.0, "selected_ink": 100.0},
    ])
    row_height, text_height = 250.0, 150.0
    name = s.generate_resistor_table("RESTABLE_TEST", 62, row_height=row_height,
                                      text_height=text_height, title=None)
    cell = s.library.cells[name]
    # the header row's own text origin is at y=0 (oy); its glyphs reach
    # up to y=text_height. The grid line directly above the header row
    # must sit AT OR ABOVE that, never below it.
    lines = [sh for sh in cell.shapes if isinstance(sh, Path)]
    header_top_line_y = max(ln.points[0][1] for ln in lines
                             if ln.points[0][1] == ln.points[1][1])
    assert header_top_line_y >= text_height


def test_generate_resistor_table_rejects_a_row_height_too_small_for_the_text():
    from hlayout.io.techfile import save_resistor_list
    s = _fresh_session()
    save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 100.0, "tolerance": "5%", "power_mw": 100.0,
         "body_width": 1000.0, "selected_ink": 100.0},
    ])
    try:
        s.generate_resistor_table("RESTABLE_TEST", 62, row_height=100.0, text_height=150.0)
        assert False, "expected LayoutError"
    except LayoutError as e:
        assert "row_height" in str(e)


def test_generate_resistor_table_aim_value_matches_the_designers_own_formula():
    # aim value = value x (aim_pct% + tolerance%) -- see
    # hlayout.gui.resistor_designer.Resistor.aim_value()'s own docstring
    # for the exact same formula; this must never drift from it.
    from hlayout.io.techfile import save_resistor_design_rules, save_resistor_list
    s = _fresh_session()
    save_resistor_design_rules(s.library, {
        "aim_pct": 75.0, "ar_min": 0.25, "ar_max": 4.0,
        "available_inks": [10, 30, 100], "select_mode": "aspect",
    })
    save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 680000.0, "tolerance": "5%", "power_mw": 125.0,
         "body_width": 2000.0, "selected_ink": 300000.0},
    ])
    name = s.generate_resistor_table("RESTABLE_TEST", 62)
    cells = _table_cells(s.library.cells[name])
    assert cells[(0, 0)] == "REF DES"
    assert cells[(0, -250)] == "R1"
    assert cells[(1200, -250)] == "680,000"
    assert cells[(3700, -250)] == "5%"
    assert cells[(4700, -250)] == "544,000"  # 680000 * (0.75 + 0.05)
    assert cells[(6700, -250)] == "300,000"


def test_generate_resistor_table_handles_a_row_with_no_selected_ink():
    from hlayout.io.techfile import save_resistor_list
    s = _fresh_session()
    save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 100.0, "tolerance": "5%", "power_mw": 100.0,
         "body_width": 1000.0, "selected_ink": None},
    ])
    name = s.generate_resistor_table("RESTABLE_TEST", 62)
    cells = _table_cells(s.library.cells[name])
    assert cells[(6700, -250)] == "—"


def test_generate_resistor_table_raises_without_a_saved_resistor_list():
    s = _fresh_session()
    try:
        s.generate_resistor_table("RESTABLE_TEST", 62)
        assert False, "expected LayoutError"
    except LayoutError as e:
        assert "resistor" in str(e).lower()


def test_generate_resistor_table_requires_a_cell_name():
    from hlayout.io.techfile import save_resistor_list
    s = _fresh_session()
    save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 100.0, "tolerance": "5%", "power_mw": 100.0,
         "body_width": 1000.0, "selected_ink": 100.0},
    ])
    try:
        s.generate_resistor_table("", 62)
        assert False, "expected LayoutError"
    except LayoutError:
        pass


def test_generate_resistor_table_draws_a_ruled_grid():
    # Per user direction: a real bordered table, not just floating text
    # in a grid pattern -- one horizontal line above every row
    # (including the header) plus one below the last row, and one
    # vertical line at every column boundary (including both outer
    # edges).
    from hlayout.io.techfile import save_resistor_list
    s = _fresh_session()
    save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 680000.0, "tolerance": "5%", "power_mw": 125.0,
         "body_width": 2000.0, "selected_ink": 300000.0},
        {"ref_des": "R2", "value": 1000.0, "tolerance": "1%", "power_mw": 125.0,
         "body_width": 2000.0, "selected_ink": 1000.0},
    ])
    col_widths = (1200.0, 2500.0, 1000.0, 2000.0, 2000.0)
    # title=None isolates the base grid from the (separately tested)
    # title row.
    name = s.generate_resistor_table("RESTABLE_TEST", 62, col_widths=col_widths, title=None)
    cell = s.library.cells[name]
    lines = [sh for sh in cell.shapes if isinstance(sh, Path)]
    # 2 data rows + header + 1 = 4 horizontal lines; 5 columns + 1 = 6
    # vertical lines.
    horiz = [ln for ln in lines if ln.points[0][1] == ln.points[1][1]]
    vert = [ln for ln in lines if ln.points[0][0] == ln.points[1][0]]
    assert len(horiz) == 4
    assert len(vert) == 6
    # the grid spans the full table width (sum of col_widths) and every
    # column boundary is represented.
    total_width = sum(col_widths)
    x_positions = sorted({ln.points[0][0] for ln in vert})
    assert x_positions[0] == 0
    assert x_positions[-1] == total_width
    assert len(x_positions) == 6


def test_generate_resistor_table_title_row_defaults_on_and_spans_full_width():
    # Per user direction: a header/title row above the column headers,
    # e.g. "RESISTOR TABLE" -- on by default, spanning the table's full
    # width as ONE merged cell (no column dividers running through it,
    # unlike every row below it).
    from hlayout.io.techfile import save_resistor_list
    s = _fresh_session()
    save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 100.0, "tolerance": "5%", "power_mw": 100.0,
         "body_width": 1000.0, "selected_ink": 100.0},
    ])
    col_widths = (1200.0, 2500.0, 1000.0, 2000.0, 2000.0)
    row_height = 250.0
    name = s.generate_resistor_table("RESTABLE_TEST", 62, col_widths=col_widths,
                                      row_height=row_height)
    cell = s.library.cells[name]
    texts = _table_cells(cell)
    # centred by default: total_width=8700, "RESISTOR TABLE" (14 chars)
    # estimated at text_height*0.6 per char = 1260 -> x = (8700-1260)/2.
    assert texts[(3720.0, row_height)] == "RESISTOR TABLE"

    lines = [sh for sh in cell.shapes if isinstance(sh, Path)]
    horiz = [ln for ln in lines if ln.points[0][1] == ln.points[1][1]]
    vert = [ln for ln in lines if ln.points[0][0] == ln.points[1][0]]
    # 1 data row + header + title + 1 = 4 horizontal lines (one extra
    # vs. no title); still only 6 verticals -- the title doesn't add
    # column dividers of its own.
    assert len(horiz) == 4
    assert len(vert) == 6
    # the two OUTER verticals reach all the way up through the title
    # row; no inner vertical does.
    total_width = sum(col_widths)
    outer = [ln for ln in vert if ln.points[0][0] in (0.0, total_width)]
    inner = [ln for ln in vert if ln.points[0][0] not in (0.0, total_width)]
    default_text_height = 150.0  # generate_resistor_table()'s own default
    title_top = default_text_height * 1.2 + row_height
    assert all(max(ln.points[0][1], ln.points[1][1]) == title_top for ln in outer)
    assert all(max(ln.points[0][1], ln.points[1][1]) < title_top for ln in inner)


def test_generate_resistor_table_title_inset_overrides_centering():
    # Per user direction the title is centred by default; title_inset
    # is the explicit opt-out for a plain left-aligned inset instead.
    from hlayout.io.techfile import save_resistor_list
    s = _fresh_session()
    save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 100.0, "tolerance": "5%", "power_mw": 100.0,
         "body_width": 1000.0, "selected_ink": 100.0},
    ])
    name = s.generate_resistor_table("RESTABLE_TEST", 62, title_inset=250.0)
    texts = _table_cells(s.library.cells[name])
    assert texts[(250.0, 250.0)] == "RESISTOR TABLE"


def test_generate_resistor_table_title_can_be_omitted():
    from hlayout.io.techfile import save_resistor_list
    s = _fresh_session()
    save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 100.0, "tolerance": "5%", "power_mw": 100.0,
         "body_width": 1000.0, "selected_ink": 100.0},
    ])
    name = s.generate_resistor_table("RESTABLE_TEST", 62, title=None)
    texts = list(_table_cells(s.library.cells[name]).values())
    assert "RESISTOR TABLE" not in texts


def test_generate_resistor_table_custom_title_text():
    from hlayout.io.techfile import save_resistor_list
    s = _fresh_session()
    save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 100.0, "tolerance": "5%", "power_mw": 100.0,
         "body_width": 1000.0, "selected_ink": 100.0},
    ])
    name = s.generate_resistor_table("RESTABLE_TEST", 62, title="ASSEMBLY 1 RESISTORS")
    texts = list(_table_cells(s.library.cells[name]).values())
    assert "ASSEMBLY 1 RESISTORS" in texts
    assert "RESISTOR TABLE" not in texts


def test_generate_resistor_table_grid_uses_a_separate_line_layer():
    from hlayout.io.techfile import save_resistor_list
    s = _fresh_session()
    save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 100.0, "tolerance": "5%", "power_mw": 100.0,
         "body_width": 1000.0, "selected_ink": 100.0},
    ])
    name = s.generate_resistor_table("RESTABLE_TEST", 62, line_layer=10)
    cell = s.library.cells[name]
    assert all(sh.layer == 62 for sh in cell.shapes if isinstance(sh, Text))
    assert all(sh.layer == 10 for sh in cell.shapes if isinstance(sh, Path))


def test_generate_resistor_table_overwrite_defaults_true_and_rebuilds():
    # Generic and re-runnable, per user direction -- unlike every other
    # "cell already exists" guard in this module, overwrite defaults to
    # True here: running RESTABLE again after editing the resistor list
    # should just refresh the table, not require an explicit flag.
    from hlayout.io.techfile import save_resistor_list
    s = _fresh_session()
    save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 100.0, "tolerance": "5%", "power_mw": 100.0,
         "body_width": 1000.0, "selected_ink": 100.0},
    ])
    name = s.generate_resistor_table("RESTABLE_TEST", 62)
    save_resistor_list(s.library, [
        {"ref_des": "R2", "value": 200.0, "tolerance": "1%", "power_mw": 100.0,
         "body_width": 1000.0, "selected_ink": 200.0},
    ])
    s.generate_resistor_table("RESTABLE_TEST", 62)  # no overwrite= passed
    cells = _table_cells(s.library.cells[name])
    assert cells[(0, -250)] == "R2"
    assert (0, -500) not in cells  # only one data row now, not stale + new


def test_generate_resistor_table_overwrite_false_refuses_an_existing_cell():
    from hlayout.io.techfile import save_resistor_list
    s = _fresh_session()
    save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 100.0, "tolerance": "5%", "power_mw": 100.0,
         "body_width": 1000.0, "selected_ink": 100.0},
    ])
    s.generate_resistor_table("RESTABLE_TEST", 62)
    try:
        s.generate_resistor_table("RESTABLE_TEST", 62, overwrite=False)
        assert False, "expected LayoutError"
    except LayoutError as e:
        assert "already exists" in str(e)


def test_restable_command_bar():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    from hlayout.io.techfile import save_resistor_list
    s = _fresh_session()
    save_resistor_list(s.library, [
        {"ref_des": "R1", "value": 100.0, "tolerance": "5%", "power_mw": 100.0,
         "body_width": 1000.0, "selected_ink": 100.0},
    ])
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("RESTABLE RESTABLE_TEST 62")
    assert "generated resistor table" in out
    assert "RESTABLE_TEST" in s.library.cells


def test_restable_command_bar_requires_layer():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("RESTABLE RESTABLE_TEST")
    assert "error" in out.lower()
