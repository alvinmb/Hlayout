"""Generate a GDSII library of Kyocera-style co-fired ceramic packages
(CFP) with a metal lid -- dual-row, side-braze style, pins along the
two long edges only -- for pin counts --start to --stop in steps of
--step (default: 20, 30, 40). Also writes an "INDEX" cell placing
every package in a labelled row for browsing.

Row spacing (the fixed cross-package dimension, the same for every
pin count -- only the package LENGTH grows with pin count) is
--row-pitch, default 0.9" (22.86 mm); pin-to-pin pitch along each row
is --pin-pitch, default 0.1" (2.54 mm) -- both real standard side-braze
ceramic DIP dimensions. Each package cell has:

  * rectangular land pads on LAYER 20, pin-numbered "1"..N for netlist
    extraction;
  * a body-outline Path on LAYER 61 (0.1 mm) round the ceramic body;
  * a metal lid -- a filled Rectangle on LAYER 63, inset from the body
    outline by --lid-inset (default 1.5 mm) on every side, the way a
    real seam-welded or solder-sealed lid sits inside the body's own
    sealing flange rather than covering it edge to edge;
  * a reference-designator placeholder Text "PKG" on LAYER 62.

Pin 1 is the bottom-left pad; numbering runs left-to-right along the
bottom edge (pins 1..N/2), then right-to-left along the top edge
(N/2+1..N) -- the usual DIP counterclockwise-from-below convention.
Renumber with PIN SET / Session.set_pin() if your fab uses the
opposite hand. Component origin is the centre of the body.

IMPORTANT: PAD_LENGTH_MM/PAD_WIDTH_MM/MARGIN_MM below are round-number
parametric estimates for layout practice, NOT pulled from a specific
Kyocera package drawing -- adjust them (or the matching CLI flags) to
match the real package you're targeting. --row-pitch/--pin-pitch
default to real 0.9"/0.1" JEDEC side-braze dimensions, though.

Usage:
    python examples/generate_kyocera_cfp_packages.py
    python examples/generate_kyocera_cfp_packages.py --start 20 --stop 64 --step 10
    python examples/generate_kyocera_cfp_packages.py --row-pitch 22.86 --lid-inset 2.0
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

INCH_MM = 25.4

PAD_LAYER = 20
OUTLINE_LAYER = 61
LABEL_LAYER = 62
LID_LAYER = 63
OUTLINE_WIDTH_MM = 0.1
LABEL_HEIGHT_MM = 0.6

#: defaults -- all mm unless noted; see the module docstring for what each means.
ROW_PITCH_MM = 0.9 * INCH_MM        # 22.86 mm -- fixed across every pin count
PIN_PITCH_MM = 0.1 * INCH_MM        # 2.54 mm -- pin-to-pin along a row
PAD_LENGTH_MM = 1.4                 # along the row (x)
PAD_WIDTH_MM = 3.0                  # straddling the body edge (y)
MARGIN_MM = 2.5                     # body edge to nearest pad centre, along the row
LID_INSET_MM = 1.5                  # body outline to lid edge, every side

INDEX_PITCH_MM = 30.0


def _pad(cell, cx_mm, cy_mm, l_mm, w_mm, pin):
    hx, hy = mm_to_dbu(l_mm) // 2, mm_to_dbu(w_mm) // 2
    cx, cy = mm_to_dbu(cx_mm), mm_to_dbu(cy_mm)
    cell.add_rectangle(PAD_LAYER, cx - hx, cy - hy, cx + hx, cy + hy, pin=str(pin))


def build_package(cell, pins, row_pitch, pin_pitch, pad_l, pad_w, margin, lid_inset):
    if pins <= 0 or pins % 2 != 0:
        raise ValueError(f"pins ({pins}) must be a positive even number for a dual-row package")
    per_side = pins // 2
    span = (per_side - 1) * pin_pitch
    xs = [(-span / 2.0) + k * pin_pitch for k in range(per_side)]
    half_row = row_pitch / 2.0
    half_length = span / 2.0 + margin
    half_depth = half_row + pad_w / 2.0

    n = 1
    for x in xs:                       # bottom row, left -> right
        _pad(cell, x, -half_row, pad_l, pad_w, n)
        n += 1
    for x in reversed(xs):             # top row, right -> left
        _pad(cell, x, half_row, pad_l, pad_w, n)
        n += 1

    hl, hw = mm_to_dbu(half_length), mm_to_dbu(half_depth)
    cell.add_path(OUTLINE_LAYER,
                  [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw), (-hl, -hw)],
                  width=mm_to_dbu(OUTLINE_WIDTH_MM), cap="flush")

    lid_hl, lid_hw = mm_to_dbu(half_length - lid_inset), mm_to_dbu(half_depth - lid_inset)
    if lid_hl <= 0 or lid_hw <= 0:
        raise ValueError(f"lid_inset ({lid_inset}) is too large for a {pins}-pin package body")
    cell.add_rectangle(LID_LAYER, -lid_hl, -lid_hw, lid_hl, lid_hw)

    cell.add_text(LABEL_LAYER, (0, 0), "PKG", height=mm_to_dbu(LABEL_HEIGHT_MM))
    return half_length * 2, half_depth * 2


def build(pin_counts, row_pitch, pin_pitch, pad_l, pad_w, margin, lid_inset) -> Library:
    lib = Library("KYOCERA-CFP-PACKAGES")
    sizes = {}
    for pins in pin_counts:
        name = f"KYOCERA-CFP-{pins}"
        sizes[name] = build_package(lib.new_cell(name), pins, row_pitch, pin_pitch,
                                     pad_l, pad_w, margin, lid_inset)

    index = lib.new_cell("INDEX")
    for i, name in enumerate(sizes):
        index.add_sref(name, (i * mm_to_dbu(INDEX_PITCH_MM), 0))
        index.add_text(LABEL_LAYER,
                       (i * mm_to_dbu(INDEX_PITCH_MM), mm_to_dbu(-sizes[name][1] / 2.0 - 2.0)),
                       name, height=mm_to_dbu(LABEL_HEIGHT_MM))

    lib.check_bounds()
    return lib, sizes


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=20, help="lowest pin count (default: 20)")
    ap.add_argument("--stop", type=int, default=40, help="highest pin count, inclusive (default: 40)")
    ap.add_argument("--step", type=int, default=10, help="step between pin counts (default: 10)")
    ap.add_argument("--row-pitch", type=float, default=ROW_PITCH_MM,
                    help=f"pin row spacing, mm (default: {ROW_PITCH_MM:.2f} = 0.9\")")
    ap.add_argument("--pin-pitch", type=float, default=PIN_PITCH_MM,
                    help=f"pin-to-pin pitch along a row, mm (default: {PIN_PITCH_MM:.2f} = 0.1\")")
    ap.add_argument("--pad-length", type=float, default=PAD_LENGTH_MM,
                    help=f"pad size along the row, mm (default: {PAD_LENGTH_MM})")
    ap.add_argument("--pad-width", type=float, default=PAD_WIDTH_MM,
                    help=f"pad size straddling the body edge, mm (default: {PAD_WIDTH_MM})")
    ap.add_argument("--margin", type=float, default=MARGIN_MM,
                    help=f"body edge to nearest pad centre, mm (default: {MARGIN_MM})")
    ap.add_argument("--lid-inset", type=float, default=LID_INSET_MM,
                    help=f"body outline to lid edge, mm (default: {LID_INSET_MM})")
    ap.add_argument("-o", "--out", default=os.path.join(_here, "kyocera_cfp_packages.gds"),
                    help="output .gds path (default: examples/kyocera_cfp_packages.gds)")
    args = ap.parse_args(argv)

    if args.step <= 0 or args.start <= 0 or args.stop < args.start:
        raise SystemExit("invalid --start/--stop/--step")
    pin_counts = list(range(args.start, args.stop + 1, args.step))
    bad = [p for p in pin_counts if p % 2 != 0]
    if bad:
        raise SystemExit(f"pin counts must be even for a dual-row package; got {bad}")

    lib, sizes = build(pin_counts, args.row_pitch, args.pin_pitch,
                       args.pad_length, args.pad_width, args.margin, args.lid_inset)
    save_gds(lib, args.out)

    print(f"{len(pin_counts)} Kyocera-style CFP package cells + INDEX -> {args.out}")
    for name, (length, depth) in sizes.items():
        print(f"  {name:18s} body {length:.2f} x {depth:.2f} mm")


if __name__ == "__main__":
    main()
