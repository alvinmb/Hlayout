"""Generate a GDSII library of dual-row ceramic hybrid package
substrates -- DIP-style, pins along the two long edges only (not all
four, unlike an LCC) -- for pin counts --start to --stop in steps of
--step (default: 20, 30, 40), each on a --pitch pitch (default
2.54 mm / 0.1"). Every pin count is built TWICE: a plain metallised
pad, and a version with a --hole-diameter (default 0.8 mm) hole
through the middle of every pad, for a package whose Kovar/Alloy-42
leads are brazed straight through the ceramic. Also writes an "INDEX"
cell placing every substrate in a labelled row for browsing.

Each substrate cell has:

  * round land pads on LAYER 20, pin-numbered "1"..N for netlist
    extraction;
  * for the "_HOLE" variant only, a concentric drilled-hole circle on
    LAYER 21 in the middle of every pad (the plated-through braze
    hole; the pad above it is the annular ring around it);
  * a body-outline Path on LAYER 61 (0.1 mm) round the substrate;
  * a reference-designator placeholder Text "PKG" on LAYER 62.

Pin 1 is the bottom-left pad; numbering runs left-to-right along the
bottom edge (pins 1..N/2), then right-to-left along the top edge
(N/2+1..N) -- the usual DIP counterclockwise-from-below convention.
Renumber with PIN SET / Session.set_pin() if your fab uses the
opposite hand. Component origin is the centre of the body.

IMPORTANT: PAD_DIAMETER_MM/DEPTH_MM/MARGIN_MM below are round-number
parametric estimates for layout practice, NOT pulled from a specific
manufacturer's package drawing -- adjust them (or the matching CLI
flags) to match the real substrate you're targeting.

Usage:
    python examples/generate_ceramic_substrates.py
    python examples/generate_ceramic_substrates.py --start 20 --stop 50 --step 10
    python examples/generate_ceramic_substrates.py --hole-diameter 1.0 --out my_subs.gds
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
from hlayout.core.shapes import circle_points
from hlayout.io.gdsio import save_gds
from hlayout.core.units import mm_to_dbu

PAD_LAYER = 20
HOLE_LAYER = 21
OUTLINE_LAYER = 61
LABEL_LAYER = 62
OUTLINE_WIDTH_MM = 0.1
LABEL_HEIGHT_MM = 0.6
CIRCLE_SEGMENTS = 32

#: defaults -- all mm; see the module docstring for what each means.
PITCH_MM = 2.54
PAD_DIAMETER_MM = 1.5
HOLE_DIAMETER_MM = 0.8
DEPTH_MM = 10.0     # substrate's short dimension, fixed across pin counts
MARGIN_MM = 2.0     # body edge to the nearest pad's centre, along the row

INDEX_PITCH_MM = 20.0


def _pad(cell, cx_mm, cy_mm, pad_dia, hole_dia, pin):
    center = (mm_to_dbu(cx_mm), mm_to_dbu(cy_mm))
    pts = circle_points(center, mm_to_dbu(pad_dia) // 2, CIRCLE_SEGMENTS)
    cell.add_polygon(PAD_LAYER, pts, pin=str(pin))
    if hole_dia:
        hpts = circle_points(center, mm_to_dbu(hole_dia) // 2, CIRCLE_SEGMENTS)
        cell.add_polygon(HOLE_LAYER, hpts)


def build_package(cell, pins, pitch, pad_dia, hole_dia, depth, margin):
    if pins <= 0 or pins % 2 != 0:
        raise ValueError(f"pins ({pins}) must be a positive even number for a dual-row substrate")
    per_side = pins // 2
    span = (per_side - 1) * pitch
    xs = [(-span / 2.0) + k * pitch for k in range(per_side)]
    half_depth = depth / 2.0
    half_length = span / 2.0 + margin

    n = 1
    for x in xs:                       # bottom row, left -> right
        _pad(cell, x, -half_depth, pad_dia, hole_dia, n)
        n += 1
    for x in reversed(xs):             # top row, right -> left
        _pad(cell, x, half_depth, pad_dia, hole_dia, n)
        n += 1

    hl, hw = mm_to_dbu(half_length), mm_to_dbu(half_depth)
    cell.add_path(OUTLINE_LAYER,
                  [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw), (-hl, -hw)],
                  width=mm_to_dbu(OUTLINE_WIDTH_MM), cap="flush")

    cell.add_text(LABEL_LAYER, (0, 0), "PKG", height=mm_to_dbu(LABEL_HEIGHT_MM))
    return half_length * 2, depth


def build(pin_counts, pitch, pad_dia, hole_dia, depth, margin) -> Library:
    lib = Library("CERAMIC-SUBSTRATES")
    sizes = {}
    names = []
    for pins in pin_counts:
        plain_name = f"SUBSTRATE-{pins}"
        hole_name = f"SUBSTRATE-{pins}-HOLE"
        sizes[plain_name] = build_package(lib.new_cell(plain_name), pins, pitch, pad_dia, 0.0, depth, margin)
        sizes[hole_name] = build_package(lib.new_cell(hole_name), pins, pitch, pad_dia, hole_dia, depth, margin)
        names += [plain_name, hole_name]

    index = lib.new_cell("INDEX")
    for i, name in enumerate(names):
        index.add_sref(name, (i * mm_to_dbu(INDEX_PITCH_MM), 0))
        index.add_text(LABEL_LAYER,
                       (i * mm_to_dbu(INDEX_PITCH_MM), mm_to_dbu(-sizes[name][1] / 2.0 - 2.0)),
                       name, height=mm_to_dbu(LABEL_HEIGHT_MM))

    lib.check_bounds()
    return lib, sizes, names


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=20, help="lowest pin count (default: 20)")
    ap.add_argument("--stop", type=int, default=40, help="highest pin count, inclusive (default: 40)")
    ap.add_argument("--step", type=int, default=10, help="step between pin counts (default: 10)")
    ap.add_argument("--pitch", type=float, default=PITCH_MM, help=f"pin pitch, mm (default: {PITCH_MM})")
    ap.add_argument("--pad-diameter", type=float, default=PAD_DIAMETER_MM,
                    help=f"pad diameter, mm (default: {PAD_DIAMETER_MM})")
    ap.add_argument("--hole-diameter", type=float, default=HOLE_DIAMETER_MM,
                    help=f"centre hole diameter for the _HOLE variant, mm (default: {HOLE_DIAMETER_MM})")
    ap.add_argument("--depth", type=float, default=DEPTH_MM,
                    help=f"substrate short dimension, mm (default: {DEPTH_MM})")
    ap.add_argument("--margin", type=float, default=MARGIN_MM,
                    help=f"body edge to nearest pad centre, mm (default: {MARGIN_MM})")
    ap.add_argument("-o", "--out", default=os.path.join(_here, "ceramic_substrates.gds"),
                    help="output .gds path (default: examples/ceramic_substrates.gds)")
    args = ap.parse_args(argv)

    if args.step <= 0 or args.start <= 0 or args.stop < args.start:
        raise SystemExit("invalid --start/--stop/--step")
    if args.pad_diameter <= args.hole_diameter:
        raise SystemExit("--pad-diameter must be bigger than --hole-diameter (need an annular ring)")
    pin_counts = list(range(args.start, args.stop + 1, args.step))
    bad = [p for p in pin_counts if p % 2 != 0]
    if bad:
        raise SystemExit(f"pin counts must be even for a dual-row substrate; got {bad}")

    lib, sizes, names = build(pin_counts, args.pitch, args.pad_diameter,
                              args.hole_diameter, args.depth, args.margin)
    save_gds(lib, args.out)

    print(f"{len(names)} ceramic substrate cells + INDEX -> {args.out}")
    for name in names:
        length, depth = sizes[name]
        print(f"  {name:22s} body {length:.2f} x {depth:.2f} mm")


if __name__ == "__main__":
    main()
