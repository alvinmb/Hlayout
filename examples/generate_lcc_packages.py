"""Generate a GDSII library of square Leadless Chip Carrier (LCC)
package footprints, one Cell per pin count from --start to --stop in
steps of --step (default: 8, 12, 16, 20, 24, 28, 32), plus an "INDEX"
cell that places every package in a labelled row for browsing.

LCC packages are square, with an equal number of leadless (castellated)
terminals on all four edges -- so every pin count here must be a
multiple of 4. Each package cell has:

  * copper land pads on LAYER 20, pin-numbered "1"..N for netlist
    extraction, straddling the body edge (half in, half out) the way
    a real castellation wraps the body's fired edge;
  * a body-outline Path on LAYER 61 (0.1 mm) round the nominal body;
  * a small pin-1 orientation dot on LAYER 62, next to the first pad;
  * a reference-designator placeholder Text "U" on LAYER 62.

Pin 1 sits at the bottom-left corner of the bottom edge; numbering
runs counterclockwise (viewed from above, +x right/+y up) along the
bottom edge left-to-right, up the right edge, along the top edge
right-to-left, then down the left edge back to pin 1. Renumber with
PIN SET / Session.set_pin() if your fab uses the opposite hand.
Component origin is the BOTTOM-LEFT corner of the body (matching
hlayout.core.smdlib's / hlayout.io.component_library's convention),
not its centre -- the body spans (0, 0) to (side, side).

IMPORTANT: PITCH_MM/PAD_WIDTH_MM/PAD_DEPTH_MM/CORNER_MARGIN_MM below
are round-number parametric estimates for layout practice, NOT pulled
from a specific JEDEC MS-* spec sheet -- adjust them (or pass the
matching --pitch/--pad-width/--pad-depth/--corner-margin flags) to
match the real package you're targeting.

Usage:
    python examples/generate_lcc_packages.py
    python examples/generate_lcc_packages.py --start 8 --stop 44 --step 4
    python examples/generate_lcc_packages.py --pitch 1.0 --out my_lcc.gds
    python examples/generate_lcc_packages.py --step-dir step_out --run-freecad
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

PAD_LAYER = 20
OUTLINE_LAYER = 61
LABEL_LAYER = 62
OUTLINE_WIDTH_MM = 0.1
LABEL_HEIGHT_MM = 0.6

#: defaults -- all mm; see the module docstring for what each means.
PITCH_MM = 1.27
PAD_WIDTH_MM = 0.75
PAD_DEPTH_MM = 1.0
CORNER_MARGIN_MM = 1.5
PIN1_DOT_MM = 0.3

INDEX_PITCH_MM = 16.0

#: body height, mm -- 3D export only, no use in the 2D footprint.
#: Round number for a real ceramic LCC package, not size-dependent.
HEIGHT_MM = 2.3


def _pad_along_edge(cell, cx_mm, cy_mm, along_mm, depth_mm, horizontal, pin):
    """A pad centred at (cx_mm, cy_mm), `along_mm` long in the
    direction of the edge it sits on and `depth_mm` deep straddling
    the edge -- `horizontal=True` for the top/bottom edges (pad runs
    in x), `False` for the left/right edges (pad runs in y)."""
    if horizontal:
        hl, hw = mm_to_dbu(along_mm) // 2, mm_to_dbu(depth_mm) // 2
    else:
        hl, hw = mm_to_dbu(depth_mm) // 2, mm_to_dbu(along_mm) // 2
    cx, cy = mm_to_dbu(cx_mm), mm_to_dbu(cy_mm)
    cell.add_rectangle(PAD_LAYER, cx - hl, cy - hw, cx + hl, cy + hw, pin=str(pin))


def build_package(cell, pins, pitch, pad_width, pad_depth, corner_margin):
    if pins <= 0 or pins % 4 != 0:
        raise ValueError(f"pins ({pins}) must be a positive multiple of 4 for a square LCC")
    per_side = pins // 4
    side = 2 * corner_margin + (per_side - 1) * pitch
    half = side / 2.0

    def _positions():
        # relative-to-centre spacing, then shifted into bottom-left-
        # origin space (0..side) by adding half -- see the module
        # docstring on why the cell's own origin is bottom-left.
        span = (per_side - 1) * pitch
        start = -span / 2.0
        return [start + k * pitch + half for k in range(per_side)]

    pos = _positions()
    n = 1
    # Bottom edge, left -> right.
    for x in pos:
        _pad_along_edge(cell, x, 0.0, pad_width, pad_depth, horizontal=True, pin=n)
        n += 1
    # Right edge, bottom -> top.
    for y in pos:
        _pad_along_edge(cell, side, y, pad_width, pad_depth, horizontal=False, pin=n)
        n += 1
    # Top edge, right -> left.
    for x in reversed(pos):
        _pad_along_edge(cell, x, side, pad_width, pad_depth, horizontal=True, pin=n)
        n += 1
    # Left edge, top -> bottom.
    for y in reversed(pos):
        _pad_along_edge(cell, 0.0, y, pad_width, pad_depth, horizontal=False, pin=n)
        n += 1

    s = mm_to_dbu(side)
    cell.add_path(OUTLINE_LAYER,
                  [(0, 0), (s, 0), (s, s), (0, s), (0, 0)],
                  width=mm_to_dbu(OUTLINE_WIDTH_MM), cap="flush")

    # Pin-1 orientation dot, just inside the body near the first pad.
    dot_x = mm_to_dbu(pos[0])
    dot_y = mm_to_dbu(corner_margin * 0.5)
    hd = mm_to_dbu(PIN1_DOT_MM) // 2
    cell.add_rectangle(LABEL_LAYER, dot_x - hd, dot_y - hd, dot_x + hd, dot_y + hd)

    cell.add_text(LABEL_LAYER, (mm_to_dbu(half), mm_to_dbu(half)), "U", height=mm_to_dbu(LABEL_HEIGHT_MM))
    return side


def build(pin_counts, pitch, pad_width, pad_depth, corner_margin) -> Library:
    lib = Library("LCC-PACKAGES")
    sides = {}
    for pins in pin_counts:
        name = f"LCC-{pins}"
        sides[pins] = build_package(lib.new_cell(name), pins, pitch, pad_width, pad_depth, corner_margin)

    index = lib.new_cell("INDEX")
    for i, pins in enumerate(pin_counts):
        name = f"LCC-{pins}"
        index.add_sref(name, (i * mm_to_dbu(INDEX_PITCH_MM), 0))
        index.add_text(LABEL_LAYER,
                       (i * mm_to_dbu(INDEX_PITCH_MM), mm_to_dbu(-2.0)),
                       name, height=mm_to_dbu(LABEL_HEIGHT_MM))

    lib.check_bounds()
    return lib, sides


def step_parts(pin_counts, sides, pitch, pad_width, pad_depth):
    """(name, "make_lcc", kwargs) for every pin count -- see
    hlayout.io.example_step3d. Centred on the origin, matching build()'s
    own cells, so a STEP file here lines up with its GDS cell."""
    return [
        (f"LCC-{pins}", "make_lcc", dict(
            side=sides[pins], pitch=pitch, per_side=pins // 4,
            pad_w=pad_width, pad_depth=pad_depth, H=HEIGHT_MM,
        ))
        for pins in pin_counts
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=8, help="lowest pin count (default: 8)")
    ap.add_argument("--stop", type=int, default=32, help="highest pin count, inclusive (default: 32)")
    ap.add_argument("--step", type=int, default=4, help="step between pin counts (default: 4)")
    ap.add_argument("--pitch", type=float, default=PITCH_MM, help=f"terminal pitch, mm (default: {PITCH_MM})")
    ap.add_argument("--pad-width", type=float, default=PAD_WIDTH_MM,
                    help=f"pad size along the edge, mm (default: {PAD_WIDTH_MM})")
    ap.add_argument("--pad-depth", type=float, default=PAD_DEPTH_MM,
                    help=f"pad size straddling the edge, mm (default: {PAD_DEPTH_MM})")
    ap.add_argument("--corner-margin", type=float, default=CORNER_MARGIN_MM,
                    help=f"corner to nearest pad centre, mm (default: {CORNER_MARGIN_MM})")
    ap.add_argument("-o", "--out", default=os.path.join(_here, "lcc_packages.gds"),
                    help="output .gds path (default: examples/lcc_packages.gds)")
    ap.add_argument("--step-dir", default=None,
                    help="also write a FreeCAD macro building a matching 3D STEP model "
                         "for every pin count into this folder")
    ap.add_argument("--run-freecad", action="store_true",
                    help="with --step-dir, also run the macro with freecadcmd to produce "
                         "the .step files now (requires FreeCAD installed)")
    args = ap.parse_args(argv)

    if args.step <= 0 or args.start <= 0 or args.stop < args.start:
        raise SystemExit("invalid --start/--stop/--step")
    pin_counts = list(range(args.start, args.stop + 1, args.step))
    bad = [p for p in pin_counts if p % 4 != 0]
    if bad:
        raise SystemExit(f"pin counts must be multiples of 4 for a square LCC; got {bad}")

    lib, sides = build(pin_counts, args.pitch, args.pad_width, args.pad_depth, args.corner_margin)
    save_gds(lib, args.out)

    print(f"{len(pin_counts)} LCC package cells + INDEX -> {args.out}")
    for pins in pin_counts:
        print(f"  LCC-{pins:<3d} {pins} pins ({pins // 4}/side)  body {sides[pins]:.2f} x {sides[pins]:.2f} mm")

    if args.step_dir:
        parts = step_parts(pin_counts, sides, args.pitch, args.pad_width, args.pad_depth)
        macro_path = write_step_macro(parts, args.step_dir)
        print(f"wrote 3D STEP macro -> {macro_path}")
        if args.run_freecad:
            print(run_step_macro(macro_path))


if __name__ == "__main__":
    main()
