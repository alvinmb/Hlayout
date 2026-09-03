import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.shapes import Rectangle, Text
from hlayout.io.techfile import (
    load_resistor_geometry_rules,
    resistor_ink_layer,
    save_refdes_annotation_layer,
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
    # width = body_width + 2*C + 2*E = 200 + 100 + 50
    assert (y1 - y0) == 350.0
    # centred on the resistor, i.e. strictly inside the terminal gap +/- E
    assert x0 == -525.0 and x1 == 525.0


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


# --- reference-designator label (ref_des / doc_layer) -----------------------

def test_generate_resistor_ref_des_is_a_text_shape_only_on_the_doc_layer():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 100.0, "pad_b": 300.0, "space_c": 50.0, "glaze_oversize": 0.0,
        "resistor_layer": 10, "conductor_layer": 11, "glaze_layer": 12, "doc_layer": 13,
    })
    s.generate_resistor(0, 0, body_width=200.0, body_length=1000.0, ref_des="R1")

    shapes = _device_shapes(s)
    assert len(shapes) == 5  # ink + glaze + 2 pads + ref-des text
    labels = [sh for sh in shapes if isinstance(sh, Text)]
    assert len(labels) == 1
    assert labels[0].layer == 13          # only on the assigned ref-des layer
    assert labels[0].text == "R1"
    # still set on the instance for netlist extraction (the canvas knows
    # not to also float it -- see Canvas._refdes_baked_into_cell).
    assert s.cell.srefs[-1].label == "R1"


def test_generate_resistor_ref_des_uses_the_refdes_annotation_layer_when_set():
    # The design's one Reference Designator layer (Setup > Technology
    # Properties > Ref-Des) wins over the legacy resistor_geometry_rules
    # doc_layer -- the two settings were merged into one.
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 100.0, "pad_b": 300.0, "space_c": 50.0, "glaze_oversize": 0.0,
        "resistor_layer": 10, "conductor_layer": 11, "glaze_layer": 12, "doc_layer": 13,
    })
    save_refdes_annotation_layer(s.library, 40)
    s.generate_resistor(0, 0, body_width=200.0, body_length=1000.0, ref_des="R1")
    label = next(sh for sh in _device_shapes(s) if isinstance(sh, Text))
    assert label.layer == 40


def test_generate_resistor_ref_des_falls_back_to_doc_layer_without_annotation_layer():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 100.0, "pad_b": 300.0, "space_c": 50.0, "glaze_oversize": 0.0,
        "resistor_layer": 10, "conductor_layer": 11, "glaze_layer": 12, "doc_layer": 13,
    })
    save_refdes_annotation_layer(s.library, -1)   # "(none)"
    s.generate_resistor(0, 0, body_width=200.0, body_length=1000.0, ref_des="R1")
    label = next(sh for sh in _device_shapes(s) if isinstance(sh, Text))
    assert label.layer == 13


def test_generate_resistor_without_ref_des_adds_no_label():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 100.0, "pad_b": 300.0, "space_c": 50.0,
        "resistor_layer": 10, "conductor_layer": 11, "glaze_layer": 12,
    })
    s.generate_resistor(0, 0, body_width=200.0, body_length=1000.0)
    assert not any(isinstance(sh, Text) for sh in _device_shapes(s))


def test_generate_resistor_label_is_roughly_centred_on_the_ink_body():
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 100.0, "pad_b": 300.0, "space_c": 50.0,
        "resistor_layer": 10, "conductor_layer": 11, "glaze_layer": 12, "doc_layer": 13,
    })
    s.generate_resistor(500, -200, body_width=200.0, body_length=1000.0,
                         ref_des="R7", label_height=100.0)
    shapes = _device_shapes(s)
    label = next(sh for sh in shapes if isinstance(sh, Text))
    lx0, ly0, lx1, ly1 = label.bbox()
    cx, cy = (lx0 + lx1) / 2, (ly0 + ly1) / 2
    # The label is drawn centred at the device cell's own local origin
    # (0, 0) -- the Sref placement carries the (500, -200) offset.
    assert abs(cx - 0) < 50
    assert abs(cy - 0) < 50


def test_resgen_ref_option_adds_a_label():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 50.0, "pad_b": 100.0, "space_c": 20.0,
        "resistor_layer": 0, "conductor_layer": 1, "glaze_layer": 2, "doc_layer": 3,
    })
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("RESGEN 0,0 100 400 ref=R5")
    assert "generated resistor" in out
    shapes = _device_shapes(s)
    labels = [sh for sh in shapes if isinstance(sh, Text)]
    assert len(labels) == 1
    assert labels[0].text == "R5"
    assert labels[0].layer == 3
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
