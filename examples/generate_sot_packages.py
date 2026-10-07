"""Generate a GDSII library of common SOT (Small-Outline Transistor)
package footprints, one Cell per package, plus an "INDEX" cell that
places every package in a labelled row for browsing.

Packages: SOT-23, SOT-23-5, SOT-23-6, SOT-89, SOT-143, SOT-223, SOT-363,
SOT-8, SOT-16, SOT-18, SOT-20, SOT-24, SOT-28.

NOTE ON NAMING: SOT-8/16/18/20/24/28 are gull-wing multi-lead IC
packages, not transistor outlines -- JEDEC properly calls this shape
"SOIC"/"SO" (e.g. real-world SOIC-8, SOIC-20W), not "SOT", which is
otherwise reserved for 3-6 lead parts. They're named SOT-<n> here to
match how they were asked for and to sit alongside the rest of this
file's SOT family; rename the cells if your library wants the
JEDEC-correct SOIC-<n> naming instead.

Each package cell has:

  * copper land pads on LAYER 3, pin-numbered "1".."N" for netlist
    extraction (hlayout's own canvas already renders this pin number as
    a chip over the pad -- see _draw_pin_label() in hlayout.gui.canvas --
    so it doesn't also need to be baked into the layer's own geometry);
  * a body-outline Path on LAYER 61 (0.1 mm) round the nominal body;
  * a reference-designator placeholder Text "U" on LAYER 62.

Pads sit in two rows (left/right of the body) on a regular pitch, the
generic gull-wing arrangement. SOT-89 and SOT-223 carry an oversized
tab pad in place of one ordinary pad. Component origin is the
BOTTOM-LEFT corner of the body (matching hlayout.core.smdlib's /
hlayout.io.component_library's convention), not its centre -- the
body spans (0, 0) to (body[0], body[1]).

IMPORTANT: these are round-number nominal dimensions for layout
practice, NOT per-manufacturer datasheet land patterns. Check a real
datasheet / IPC-7351 land pattern before committing a board.

Usage:
    python examples/generate_sot_packages.py
    python examples/generate_sot_packages.py --out my_sot.gds
    python examples/generate_sot_packages.py --step-dir step_out --run-freecad
"""
import argparse
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_root = _here
for _ in range(4):
    if os.path.isdir(os.path.join(_root, "hlayout")):
        break
    _root = os.path.dirname(_root)
sys.path.insert(0, _root)

from hlayout.core.library import Library
from hlayout.io.gdsio import save_gds
from hlayout.core.units import mm_to_dbu
from hlayout.io.example_step3d import run_step_macro, write_step_macro

PAD_LAYER = 3
PAD_OVERSIZE_LAYER = 2
OUTLINE_LAYER = 61
LABEL_LAYER = 62
#: how much bigger the layer-2 oversized pad is than the layer-3 copper
#: pad, per side (so each dimension grows by 2x this amount) -- a
#: solder-mask-opening-style clearance pad.
PAD_OVERSIZE_MM = 0.1
OUTLINE_WIDTH_MM = 0.1
LABEL_HEIGHT_MM = 0.6

#: name -> nominal geometry, all mm.
#:   pins       total pin count (pads are numbered 1..pins)
#:   body       (length, width) of the moulded body outline -- for
#:              "lr" (default) packages this is (across-the-rows,
#:              along-a-row); for "tb" packages it's (along-a-row,
#:              across-the-rows) instead, i.e. the two axes are
#:              transposed relative to "lr" -- see `rows` below.
#:   pitch      centre-to-centre spacing of pads along each row
#:   row_gap    centre-to-centre spacing between the two pad rows
#:   pad        (length, width) of an ordinary land pad, same axis
#:              convention as body: transposed for "tb" packages.
#:   tab_pin    pin number drawn as an oversized tab, or None
#:   tab        (length, width) of that tab pad, when tab_pin is set
#:   rows       "lr" (default, omitted) -- rows on the LEFT/RIGHT
#:              walls, pins spread along Y, pin 1 at the bottom of the
#:              left row. "tb" -- rows on the TOP/BOTTOM walls, pins
#:              spread along X, pin 1 at the LEFT of the bottom row
#:              (row 1 = bottom, left-to-right; row 2 = top,
#:              right-to-left, continuing the same winding direction)
#:              -- see build_package() and _spec_step_leads().
PACKAGES = {
    # 2026-09-23: SOT-23/23-5/23-6/143/363 switched "lr" -> "tb". Real
    # parts in this family have leads exiting the TOP/BOTTOM long
    # edges (spread along X, same-row pads share a Y and differ in X),
    # not the left/right short ends -- same class of bug as the SOT-8
    # gull-wing family below. `pad`/`tab` tuples are swapped to match
    # "tb"'s (along-row, across-row) axis convention -- see PACKAGES's
    # own docstring above -- otherwise the along-row pad width would
    # exceed `pitch` and adjacent pads would overlap. SOT-89 and
    # SOT-223 stay "lr": their row_gap sits close to body_l (their
    # long axis), not body_w, confirming their tab/leads really do
    # exit the SHORT ends, unlike this family.
    "SOT-23":   dict(pins=3, body=(2.9, 1.3), pitch=0.95, row_gap=2.3, pad=(0.55, 1.0), rows="tb"),
    "SOT-23-5": dict(pins=5, body=(2.9, 1.6), pitch=0.95, row_gap=2.6, pad=(0.55, 1.1), rows="tb"),
    "SOT-23-6": dict(pins=6, body=(2.9, 1.6), pitch=0.95, row_gap=2.6, pad=(0.55, 1.1), rows="tb"),
    "SOT-89":   dict(pins=4, body=(4.5, 2.5), pitch=1.5, row_gap=4.6, pad=(1.2, 0.6),
                     tab_pin=4, tab=(1.8, 2.0)),
    "SOT-143":  dict(pins=4, body=(2.9, 1.3), pitch=0.95, row_gap=2.3, pad=(0.55, 1.0),
                     tab_pin=4, tab=(1.1, 1.0), rows="tb"),
    "SOT-223":  dict(pins=4, body=(6.5, 3.5), pitch=2.3, row_gap=6.3, pad=(1.6, 0.9),
                     tab_pin=4, tab=(3.8, 2.0)),
    "SOT-363":  dict(pins=6, body=(2.0, 1.25), pitch=0.65, row_gap=2.0, pad=(0.4, 0.7), rows="tb"),
    # Gull-wing multi-lead ICs at the standard 1.27 mm (0.05") SOIC pitch --
    # see the "NOTE ON NAMING" in the module docstring. These are "tb"
    # packages: rows on the TOP/BOTTOM walls (real SOIC leads exit the
    # two LONG sides, and here that's the top/bottom pair), pins spread
    # along X, matching a real datasheet pad-layout diagram directly
    # (2026-09 bug report, round 4 -- pin 1 is bottom-LEFT of the
    # BOTTOM row, row 1 = pins 1..per_row left-to-right along the
    # bottom, row 2 = pins per_row+1..pins right-to-left along the top;
    # confirmed against the user's own reference image: BL=1, BR=4,
    # TR=5, TL=8 for an 8-pin part). body/pad here are therefore
    # (along-a-row, across-the-rows) -- the OPPOSITE axis order from the
    # "lr" packages above -- so body_l is the dimension that grows with
    # pin count. SOT-8's body/row_gap/pitch match a real 8-pin SOIC
    # datasheet drawing (5.1 x 3.95, row_gap/lead-span 6.0, pitch 1.27,
    # all confirmed against it directly) -- SOT-16 shares its narrow-
    # body width (3.95) for the same reason, but keeps its own already-
    # reasonable along-row length (9.9) since no 16-pin reference was
    # checked against.
    "SOT-8":    dict(pins=8,  body=(5.1, 3.95), pitch=1.27, row_gap=6.0, pad=(0.6, 1.5), rows="tb"),
    "SOT-16":   dict(pins=16, body=(9.9, 3.95), pitch=1.27, row_gap=6.0, pad=(0.6, 1.5), rows="tb"),
    "SOT-18":   dict(pins=18, body=(11.5, 7.5), pitch=1.27, row_gap=9.8, pad=(0.6, 1.5), rows="tb"),
    "SOT-20":   dict(pins=20, body=(12.8, 7.5), pitch=1.27, row_gap=9.8, pad=(0.6, 1.5), rows="tb"),
    "SOT-24":   dict(pins=24, body=(15.4, 7.5), pitch=1.27, row_gap=9.8, pad=(0.6, 1.5), rows="tb"),
    "SOT-28":   dict(pins=28, body=(17.9, 7.5), pitch=1.27, row_gap=9.8, pad=(0.6, 1.5), rows="tb"),
}

#: body height, mm -- 3D export only, no use in the 2D footprint.
#: Matches hlayout.io.component_library.DEFAULT_SOT_PACKAGES's own
#: values where a name overlaps (SOT_23/323/89/223 there), so the two
#: independent 3D conventions at least agree on this one number.
#: SOT-8/16/18/20/24/28's own 2.5 mm matches the same 8-pin SOIC
#: datasheet drawing body=(3.95,5.1) above (overall height "A" = 2.5),
#: applied to the whole gull-wing sub-family since package height
#: doesn't vary much with pin count/body width the way length does.
HEIGHT_MM = {
    "SOT-23": 1.1, "SOT-23-5": 1.1, "SOT-23-6": 1.1,
    "SOT-89": 1.5, "SOT-143": 1.1, "SOT-223": 1.6, "SOT-363": 0.95,
    "SOT-8": 2.5, "SOT-16": 2.5, "SOT-18": 2.5,
    "SOT-20": 2.5, "SOT-24": 2.5, "SOT-28": 2.5,
}

#: real lead width ("b" on a datasheet), mm -- 3D export only for the
#: gull-wing sub-family. The 2D land pad is deliberately wider (see
#: PACKAGES's own pad=) for a real solderable copper pad, but the
#: actual metal lead itself -- what the 3D model should show -- is
#: this much narrower, matching the same 8-pin SOIC reference drawing.
LEAD_WIDTH_MM = 0.38

INDEX_PITCH_MM = 12.0


def _pad(cell, cx_mm, cy_mm, l_mm, w_mm, pin):
    hx, hy = mm_to_dbu(l_mm) // 2, mm_to_dbu(w_mm) // 2
    cx, cy = mm_to_dbu(cx_mm), mm_to_dbu(cy_mm)
    # `pin=` is netlist-extraction metadata, not a drawn shape -- but it
    # doesn't need to be: hlayout's own canvas already renders every
    # Rectangle/Polygon's pin string as a number chip centred on the pad
    # (see hlayout.gui.canvas._draw_pin_label(), on by default via
    # canvas.show_pin_numbers), reading this same attribute directly. A
    # 2026-09 round baked the number a second time as actual stroke-font
    # geometry on this layer, which was redundant with that built-in
    # display and risked showing a second, independently-computed (and
    # in practice wrong) label right on top of the correct one -- removed
    # again; this attribute alone is both correct and sufficient.
    cell.add_rectangle(PAD_LAYER, cx - hx, cy - hy, cx + hx, cy + hy, pin=str(pin))


def _oversized_pad(cell, cx_mm, cy_mm, l_mm, w_mm):
    # A layer-2 clearance pad mirroring _pad()'s own layer-3 copper
    # pad at the same centre, but PAD_OVERSIZE_MM bigger on every side
    # (so l_mm/w_mm each grow by 2*PAD_OVERSIZE_MM total). No `pin=`
    # attribute -- that's the layer-3 pad's netlist identity, not this
    # one's.
    l2 = l_mm + 2 * PAD_OVERSIZE_MM
    w2 = w_mm + 2 * PAD_OVERSIZE_MM
    hx, hy = mm_to_dbu(l2) // 2, mm_to_dbu(w2) // 2
    cx, cy = mm_to_dbu(cx_mm), mm_to_dbu(cy_mm)
    cell.add_rectangle(PAD_OVERSIZE_LAYER, cx - hx, cy - hy, cx + hx, cy + hy)


#: height of the baked pin-number label text, mm. Kept well under the
#: smallest pad dimension in use (SOT-363's 0.4 mm pad) so the digits
#: stay inside the copper for every package.
PIN_LABEL_HEIGHT_MM = 0.3


def _pin_label(cell, cx_mm, cy_mm, pin):
    # A visible pin number baked directly into the pad geometry (on
    # PAD_LAYER, same layer as the copper pad itself), in addition to
    # the `pin=` netlist-extraction attribute _pad() already sets.
    # HLayout's own canvas already renders that attribute as a number
    # chip (see _pad()'s own comment), but this bakes the digits into
    # the GDS geometry itself so they're visible in any GDS viewer, not
    # just hlayout's canvas. Text origin is bottom-left (see
    # hlayout.core.strokefont.text_to_strokes()'s docstring), and
    # bbox() estimates width as height*0.6*len(text), so both offsets
    # below centre the label on (cx_mm, cy_mm).
    s = str(pin)
    h_mm = PIN_LABEL_HEIGHT_MM
    w_mm = h_mm * 0.6 * len(s)
    x0 = mm_to_dbu(cx_mm - w_mm / 2.0)
    y0 = mm_to_dbu(cy_mm - h_mm / 2.0)
    cell.add_text(PAD_LAYER, (x0, y0), s, height=mm_to_dbu(h_mm))


def _lead(cell, cx_mm, cy_mm, l_mm, w_mm):
    # The metal lead's own outline on OUTLINE_LAYER (61), alongside the
    # body outline already drawn there. NOT the same size as the pad
    # footprint (2026-09 bug report -- an earlier round just duplicated
    # _pad()'s own l_mm/w_mm here, which drew the copper LAND size, not
    # the real metal lead): the land pad is deliberately oversized
    # beyond the actual lead for solderability (see LEAD_WIDTH_MM's own
    # comment), so callers now pass the real, narrower along-row lead
    # width here (LEAD_WIDTH_MM for an ordinary pin) while the
    # across-row (wall-to-tip reach) dimension is unchanged, since that
    # one IS the real physical reach, not land overhang. No `pin=`
    # netlist attribute here (that stays on the layer-3 copper pad --
    # see _pad()).
    hx, hy = mm_to_dbu(l_mm) // 2, mm_to_dbu(w_mm) // 2
    cx, cy = mm_to_dbu(cx_mm), mm_to_dbu(cy_mm)
    cell.add_rectangle(OUTLINE_LAYER, cx - hx, cy - hy, cx + hx, cy + hy)


def _lead_bend(cell, rows, row_coord, wall, direction, across_centre, across_mm, along_mm, stub_cap):
    """Draw one lead as two abutting rectangles on OUTLINE_LAYER instead
    of one -- the gull-wing bend (stub near the body, foot flat under
    the pad), projected to its top-view footprint (2026-09 bug report:
    leads need to (a) actually touch the body wall -- previously the
    near edge sat wherever the pad happened to be, sometimes short of
    the wall by a real gap -- and (b) show the bend, not just one solid
    block matching the pad). `wall`/`direction` locate the body wall on
    the across-row axis and which way the lead points away from it (-1
    toward 0, +1 toward the far wall); `across_centre`/`across_mm` are
    this pin's own pad-or-tab across-row centre and size (so the lead's
    far edge lines up exactly with that pin's own pad, tab or not);
    `row_coord` is the fixed along-row position; `stub_cap` is the
    short near-body segment's length cap (0.25 mm for "tb" packages,
    0.5 mm for "lr" -- the same split step_parts() uses for the 3D
    model, so the 2D knee lines up with the 3D one)."""
    reach_total = direction * (across_centre - wall) + across_mm / 2.0
    stub_len = min(stub_cap, max(0.0, reach_total))
    foot_len = max(0.0, reach_total - stub_len)
    stub_far = wall + direction * stub_len
    tip = wall + direction * reach_total
    stub_mid = (wall + stub_far) / 2.0
    foot_mid = (stub_far + tip) / 2.0
    for length, mid in ((stub_len, stub_mid), (foot_len, foot_mid)):
        if length <= 0:
            continue
        if rows == "tb":
            _lead(cell, row_coord, mid, along_mm, length)
        else:
            _lead(cell, mid, row_coord, length, along_mm)


def build_package(cell, spec):
    pins = spec["pins"]
    body_l, body_w = spec["body"]
    pitch = spec["pitch"]
    row_gap = spec["row_gap"]
    pad_l, pad_w = spec["pad"]
    tab_pin = spec.get("tab_pin")
    tab = spec.get("tab")
    rows = spec.get("rows", "lr")

    # Everything below is computed relative-to-centre, then shifted by
    # (ox, oy) into bottom-left-origin space -- see the module
    # docstring on why the cell's own origin is bottom-left.
    ox, oy = body_l / 2.0, body_w / 2.0

    per_row = (pins + 1) // 2          # row 1 is pins 1..per_row

    def _row_positions(count, offset):
        span = (count - 1) * pitch
        return [(-span / 2.0) + k * pitch + offset for k in range(count)]

    # Real (narrower) ordinary-lead size for the layer-61 lead outline,
    # as opposed to the (wider, solderable) layer-3 land pad -- see
    # _lead()'s own comment. along-row shrinks to LEAD_WIDTH_MM;
    # across-row (the wall-to-tip reach) is unchanged since that's real
    # physical length, not land overhang. Axis matches PACKAGES's own
    # (along-row, across-row) convention: index 0 for "tb", 1 for "lr".
    along_idx = 0 if rows == "tb" else 1
    lead_l, lead_w_mm = (LEAD_WIDTH_MM, pad_w) if along_idx == 0 else (pad_l, LEAD_WIDTH_MM)
    # Stub-segment cap for _lead_bend() -- matches step_parts()'s own
    # split so the 2D knee lines up with the 3D one.
    stub_cap = 0.25 if rows == "tb" else 0.5

    n = 1
    if rows == "tb":
        # Rows on the TOP/BOTTOM walls, pins spread along X. Row 1
        # (pins 1..per_row) is the BOTTOM row, left-to-right; row 2
        # (per_row+1..pins) is the TOP row, right-to-left -- continuing
        # the same winding direction round the package, so pin 1 sits
        # at the bottom-left corner and the last pin at the top-left.
        bottom_y = -row_gap / 2.0 + oy
        top_y = row_gap / 2.0 + oy
        bottom_xs = _row_positions(per_row, ox)
        top_xs = list(reversed(_row_positions(pins - per_row, ox)))
        for x in bottom_xs:
            if tab_pin == n and tab:
                _pad(cell, x, bottom_y, tab[0], tab[1], n)
                _oversized_pad(cell, x, bottom_y, tab[0], tab[1])
                _lead_bend(cell, rows, x, 0.0, -1.0, bottom_y, tab[1], tab[0], stub_cap)
            else:
                _pad(cell, x, bottom_y, pad_l, pad_w, n)
                _oversized_pad(cell, x, bottom_y, pad_l, pad_w)
                _lead_bend(cell, rows, x, 0.0, -1.0, bottom_y, pad_w, lead_l, stub_cap)
            _pin_label(cell, x, bottom_y, n)
            n += 1
        for x in top_xs:
            if tab_pin == n and tab:
                _pad(cell, x, top_y, tab[0], tab[1], n)
                _oversized_pad(cell, x, top_y, tab[0], tab[1])
                _lead_bend(cell, rows, x, body_w, 1.0, top_y, tab[1], tab[0], stub_cap)
            else:
                _pad(cell, x, top_y, pad_l, pad_w, n)
                _oversized_pad(cell, x, top_y, pad_l, pad_w)
                _lead_bend(cell, rows, x, body_w, 1.0, top_y, pad_w, lead_l, stub_cap)
            _pin_label(cell, x, top_y, n)
            n += 1
    else:
        # Rows on the LEFT/RIGHT walls, pins spread along Y. Row 1
        # (pins 1..per_row) is the LEFT row, bottom-to-top; row 2
        # (per_row+1..pins) is the RIGHT row, top-to-bottom.
        left_x = -row_gap / 2.0 + ox
        right_x = row_gap / 2.0 + ox
        left_ys = _row_positions(per_row, oy)
        right_ys = list(reversed(_row_positions(pins - per_row, oy)))
        for y in left_ys:
            if tab_pin == n and tab:
                _pad(cell, left_x, y, tab[0], tab[1], n)
                _oversized_pad(cell, left_x, y, tab[0], tab[1])
                _lead_bend(cell, rows, y, 0.0, -1.0, left_x, tab[0], tab[1], stub_cap)
            else:
                _pad(cell, left_x, y, pad_l, pad_w, n)
                _oversized_pad(cell, left_x, y, pad_l, pad_w)
                _lead_bend(cell, rows, y, 0.0, -1.0, left_x, pad_l, lead_w_mm, stub_cap)
            _pin_label(cell, left_x, y, n)
            n += 1
        for y in right_ys:
            if tab_pin == n and tab:
                _pad(cell, right_x, y, tab[0], tab[1], n)
                _oversized_pad(cell, right_x, y, tab[0], tab[1])
                _lead_bend(cell, rows, y, body_l, 1.0, right_x, tab[0], tab[1], stub_cap)
            else:
                _pad(cell, right_x, y, pad_l, pad_w, n)
                _oversized_pad(cell, right_x, y, pad_l, pad_w)
                _lead_bend(cell, rows, y, body_l, 1.0, right_x, pad_l, lead_w_mm, stub_cap)
            _pin_label(cell, right_x, y, n)
            n += 1

    bl, bw = mm_to_dbu(body_l), mm_to_dbu(body_w)
    cell.add_path(OUTLINE_LAYER,
                  [(0, 0), (bl, 0), (bl, bw), (0, bw), (0, 0)],
                  width=mm_to_dbu(OUTLINE_WIDTH_MM), cap="flush")

    cell.add_text(LABEL_LAYER, (mm_to_dbu(ox), mm_to_dbu(oy)), "U", height=mm_to_dbu(LABEL_HEIGHT_MM))


def build() -> Library:
    lib = Library("SOT-PACKAGES")
    for name, spec in PACKAGES.items():
        build_package(lib.new_cell(name), spec)

    index = lib.new_cell("INDEX")
    for i, name in enumerate(PACKAGES):
        index.add_sref(name, (i * mm_to_dbu(INDEX_PITCH_MM), 0))
        index.add_text(LABEL_LAYER,
                       (i * mm_to_dbu(INDEX_PITCH_MM), mm_to_dbu(-4.0)),
                       name, height=mm_to_dbu(LABEL_HEIGHT_MM))

    lib.check_bounds()
    return lib


def _spec_step_leads(spec, lead_w=None):
    """leads for make_dip()/make_dip_x() from a PACKAGES entry -- same
    (relative-to-centre) positions build_package() itself computes for
    its two rows, just returned as plain data instead of drawn, so the
    2D cell and the 3D model can never drift apart. Works for both
    `rows` conventions: for "lr" the along-row axis is Y and the
    across-row pad dimension is pad[1]; for "tb" the along-row axis is
    X and the across-row (protrusion) pad dimension is pad[1] too (pad
    is stored (along-row, across-row) for "tb" -- see PACKAGES's own
    docstring). `lead_w` overrides the ordinary (non-tab) lead's
    along-row width -- the 2D land pad is deliberately wider than the
    real metal lead (see LEAD_WIDTH_MM); defaults to the 2D pad's own
    along-row width when not given. A tab_pin's own tab width is never
    overridden."""
    pins = spec["pins"]
    pitch = spec["pitch"]
    rows = spec.get("rows", "lr")
    # For "lr", pad=(protrusion, along-row-width) -> along-row is [1].
    # For "tb", pad=(along-row-width, protrusion) -> along-row is [0].
    along_row_idx = 0 if rows == "tb" else 1
    pad_w = lead_w if lead_w is not None else spec["pad"][along_row_idx]
    tab_pin = spec.get("tab_pin")
    tab = spec.get("tab")
    per_row = (pins + 1) // 2

    def _positions(count):
        span = (count - 1) * pitch
        return [(-span / 2.0) + k * pitch for k in range(count)]

    def _leads(positions, first_pin):
        out = []
        for i, p in enumerate(positions):
            n = first_pin + i
            w = tab[along_row_idx] if tab_pin == n and tab else pad_w
            out.append((round(p, 4), w))
        return out

    row1 = _leads(_positions(per_row), 1)
    row2 = list(reversed(_leads(_positions(pins - per_row), per_row + 1)))
    return row1, row2


def step_parts():
    """(name, "make_dip"/"make_dip_x", kwargs) for every PACKAGES entry
    -- see hlayout.io.example_step3d.make_dip()/make_dip_x(). Which one
    is used follows the spec's own `rows` ("tb" -> make_dip, rows along
    Y at the top/bottom walls; "lr"/default -> make_dip_x, rows along X
    at the left/right walls) -- matching this script's own 2D axis
    convention directly, no swapping needed. Bottom-left origin,
    matching build()'s own cells, so a STEP file here lines up with its
    GDS cell.

    KNOWN GAP: reach (below) is computed from the spec's ordinary pad
    size only -- a tab_pin's own bigger tab size isn't factored in, so
    SOT-89/143/223's 3D tab lead falls a little short of where the 2D
    tab pad's own outer edge actually sits (confirmed ~0.3 mm on
    SOT-89 -- _gullwing_side_x() only takes one lead_out for a whole
    side, not a per-lead override, so fixing this needs a real API
    change, not just a number tweak)."""
    parts = []
    for name, spec in PACKAGES.items():
        body_l, body_w = spec["body"]
        rows = spec.get("rows", "lr")
        row_gap = spec["row_gap"]
        # The across-row (protrusion) pad dimension, and the body
        # dimension the leads protrude from, both depend on `rows` --
        # see PACKAGES's own docstring on the axis transpose for "tb".
        across_idx = 1 if rows == "tb" else 0
        pad_across = spec["pad"][across_idx]
        body_across = body_w if rows == "tb" else body_l
        # gull-wing ICs (SOT-8 and up) get their real, narrower metal
        # lead width in 3D; the transistor-family entries keep using
        # their pad width as before.
        lead_w = LEAD_WIDTH_MM if spec["pins"] >= 8 else None
        row1, row2 = _spec_step_leads(spec, lead_w)
        # Reach the leads/feet need to cover, from the 3D body's own
        # wall out to roughly where the 2D pad's own outer edge sits --
        # see the module docstring's note that a pad can extend a
        # little past the outline. lead_t itself also adds to the
        # total reach (the "drop" segment has width lead_t), so it's
        # subtracted back out.
        lead_t = 0.15
        # A real gull-wing foot is the DOMINANT flat, solderable part of
        # the lead, and the stub where it exits the body is short -- a
        # flat 0.05/0.35 mm split (2026-09, first pass) still read as a
        # long stub with a barely-there foot (see reference photos:
        # 2026-09 bug report, round 2). Then: total tip reach was kept
        # close to the 2D land pad's own outer edge (reach - lead_t, as
        # before, but with 0.35 mm of extra budget folded in so there's
        # room for a real foot), split so the stub gets at most 0.5 mm
        # (floored at 0.15 mm so it's still visible) and the foot got
        # whatever was left over. For the larger SOIC-style packages
        # (SOT-8 and up) that leftover-budget approach handed the foot
        # 1.5-1.6 mm -- well past the real datasheet spec (LTC S-package
        # 16-lead SOIC, DWG 05-08-1610 Rev G: foot length .016-.050in =
        # 0.4-1.27mm) and nearly as long as half the body itself (2026-09
        # bug report, round 3). So the foot is now capped at 1.0 mm,
        # comfortably inside that spec range; any leftover budget beyond
        # stub+foot is simply unused rather than inflating the foot.
        reach = max((row_gap / 2.0 + pad_across / 2.0) - body_across / 2.0, 0.05)
        total_budget = max(reach - lead_t, 0.02) + lead_t + 0.35
        lead_out = min(0.5, max(0.15, total_budget * 0.25)) if rows != "tb" else min(0.25, max(0.15, total_budget * 0.25))
        lead_foot = min(1.0, max(0.15, total_budget - lead_out - lead_t))
        if rows == "tb":
            parts.append((name, "make_dip", dict(
                L=body_l, W=body_w, H=HEIGHT_MM[name],
                leads_neg_y=row1, leads_pos_y=row2,
                lead_t=lead_t, lead_out=lead_out, lead_foot=lead_foot,
            )))
        else:
            parts.append((name, "make_dip_x", dict(
                L=body_l, W=body_w, H=HEIGHT_MM[name],
                leads_neg_x=row1, leads_pos_x=row2,
                lead_t=lead_t, lead_out=lead_out, lead_foot=lead_foot,
            )))
    return parts


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default=os.path.join(_here, "sot_packages.gds"),
                    help="output .gds path (default: examples/sot_packages.gds)")
    ap.add_argument("--step-dir", default=None,
                    help="also write a FreeCAD macro building a matching 3D STEP model "
                         "for every package into this folder")
    ap.add_argument("--run-freecad", action="store_true",
                    help="with --step-dir, also run the macro with freecadcmd to produce "
                         "the .step files now (requires FreeCAD installed)")
    args = ap.parse_args(argv)

    lib = build()
    save_gds(lib, args.out)

    print(f"{len(PACKAGES)} SOT package cells + INDEX -> {args.out}")
    for name, spec in PACKAGES.items():
        print(f"  {name:9s} {spec['pins']} pins  body {spec['body'][0]} x {spec['body'][1]} mm")

    if args.step_dir:
        macro_path = write_step_macro(step_parts(), args.step_dir)
        print(f"wrote 3D STEP macro -> {macro_path}")
        if args.run_freecad:
            print(run_step_macro(macro_path))


if __name__ == "__main__":
    main()
