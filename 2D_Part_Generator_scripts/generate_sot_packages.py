# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

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

Pads sit in two rows on a regular pitch, the generic gull-wing
arrangement. SOT-89 and SOT-223 are 3 leads in one row with an
oversized tab pad opposite, centred on lead 2 and numbered 2 (the tab
is lead 2's metal). Component origin is the
BOTTOM-LEFT corner of the body (matching hlayout.core.smdlib's /
hlayout.io.component_library's convention), not its centre -- the
body spans (0, 0) to (body[0], body[1]).

IMPORTANT: these are round-number nominal dimensions for layout
practice, NOT per-manufacturer datasheet land patterns. Check a real
datasheet / IPC-7351 land pattern before committing a board.

Usage:
    python 2D_Part_Generator_scripts/generate_sot_packages.py
    python 2D_Part_Generator_scripts/generate_sot_packages.py --out my_sot.gds
    python 2D_Part_Generator_scripts/generate_sot_packages.py --step-dir step_out --run-freecad
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
from hlayout.io.gdsio import save_gds, save_gds_merged
from hlayout.core.units import mm_to_dbu
from hlayout.io.example_step3d import run_step_macro, write_step_macro
from hlayout.core.sot_packages import (  # noqa: F401  (re-exported for callers/tests)
    HEIGHT_MM, LEAD_WIDTH_MM, PACKAGES, _spec_step_leads, step_parts)

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


def _lead_bend(cell, rows, row_coord, wall, direction, across_centre, across_mm, along_mm, stub_cap,
               reach=None):
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
    if reach is not None:
        # spec gives the real lead tip-to-tip span -- the lead stops
        # there, short of the (deliberately longer) land pad's toe.
        reach_total = reach
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

    per_row = spec.get("row1", (pins + 1) // 2)   # row 1 is pins 1..per_row
    tab_reach = spec.get("tab_reach")

    def _label(n):
        return spec.get("tab_label", n) if (tab_pin == n and tab) else n

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
    lw = spec.get("lead_w", LEAD_WIDTH_MM)
    lead_l, lead_w_mm = (lw, pad_w) if along_idx == 0 else (pad_l, lw)
    # Stub-segment cap for _lead_bend() -- matches step_parts()'s own
    # split so the 2D knee lines up with the 3D one.
    stub_cap = spec.get("lead_out", 0.25 if rows == "tb" else 0.5)
    # Real lead reach from the body wall, when the spec gives the
    # overall lead span (tip to tip across the rows).
    span = spec.get("span")
    body_across = body_w if rows == "tb" else body_l
    reach = (span - body_across) / 2.0 if span else spec.get("lead_reach")
    if tab_reach is None:
        tab_reach = reach if span else None

    n = 1
    if rows == "tb":
        # Rows on the TOP/BOTTOM walls, pins spread along X. Row 1
        # (pins 1..per_row) is the BOTTOM row, left-to-right; row 2
        # (per_row+1..pins) is the TOP row, right-to-left -- continuing
        # the same winding direction round the package, so pin 1 sits
        # at the bottom-left corner and the last pin at the top-left.
        bottom_y = -row_gap / 2.0 + oy
        top_y = row_gap / 2.0 + oy
        if spec.get("row_y"):
            bottom_y, top_y = spec["row_y"][0] + oy, spec["row_y"][1] + oy
        bottom_xs = _row_positions(per_row, ox)
        top_xs = list(reversed(_row_positions(pins - per_row, ox)))
        if spec.get("row2"):
            top_xs = [ox + p for p in spec["row2"]]
        for x in bottom_xs:
            if tab_pin == n and tab:
                _pad(cell, x, bottom_y, tab[0], tab[1], _label(n))
                _oversized_pad(cell, x, bottom_y, tab[0], tab[1])
                _lead_bend(cell, rows, x, 0.0, -1.0, bottom_y, tab[1], tab[0], stub_cap, tab_reach)
            else:
                _pad(cell, x, bottom_y, pad_l, pad_w, n)
                _oversized_pad(cell, x, bottom_y, pad_l, pad_w)
                _lead_bend(cell, rows, x, 0.0, -1.0, bottom_y, pad_w, lead_l, stub_cap, reach)
            _pin_label(cell, x, bottom_y, _label(n))
            n += 1
        for x in top_xs:
            if tab_pin == n and tab:
                _pad(cell, x, top_y, tab[0], tab[1], _label(n))
                _oversized_pad(cell, x, top_y, tab[0], tab[1])
                _lead_bend(cell, rows, x, body_w, 1.0, top_y, tab[1], tab[0], stub_cap, tab_reach)
            else:
                _pad(cell, x, top_y, pad_l, pad_w, n)
                _oversized_pad(cell, x, top_y, pad_l, pad_w)
                _lead_bend(cell, rows, x, body_w, 1.0, top_y, pad_w, lead_l, stub_cap, reach)
            _pin_label(cell, x, top_y, _label(n))
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
                _lead_bend(cell, rows, y, 0.0, -1.0, left_x, pad_l, lead_w_mm, stub_cap, reach)
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
                _lead_bend(cell, rows, y, body_l, 1.0, right_x, pad_l, lead_w_mm, stub_cap, reach)
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


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default=os.path.join(_root, "examples", "sot_packages.gds"),
                    help="output .gds path (default: examples/sot_packages.gds)")
    ap.add_argument("--step-dir", default=None,
                    help="also write a FreeCAD macro building a matching 3D STEP model "
                         "for every package into this folder")
    ap.add_argument("--run-freecad", action="store_true",
                    help="with --step-dir, also run the macro with freecadcmd to produce "
                         "the .step files now (requires FreeCAD installed)")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace the output file instead of adding to it "
                         "(by default an existing file keeps its other cells)")
    args = ap.parse_args(argv)

    lib = build()
    res = save_gds_merged(lib, args.out, overwrite=args.overwrite)
    if res["mode"] == "merged":
        print(f"added to existing {args.out}: {len(res['added'])} new, "
              f"{len(res['replaced'])} updated, {len(res['kept'])} other cells kept")

    print(f"{len(PACKAGES)} SOT package cells + INDEX -> {args.out}")
    for name, spec in PACKAGES.items():
        print(f"  {name:9s} {spec['pins']} pins  body {spec['body'][0]} x {spec['body'][1]} mm")

    if args.step_dir:
        macro_path = write_step_macro(step_parts(), args.step_dir)
        print(f"wrote 3D STEP macro -> {macro_path}")
        if args.run_freecad:
            print(run_step_macro(macro_path))
            # moulded body black, leads silver (needs cadquery)
            from hlayout.io.step_colour import colour_sot_models
            col = colour_sot_models(args.step_dir, force=True)
            print(f"coloured {len(col['coloured'])} model(s): body black, leads silver")
            for p, why in col["skipped"].items():
                print(f"  not coloured: {p} -- {why}")


if __name__ == "__main__":
    main()
