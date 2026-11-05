# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate a deliberately large, boring GDSII library for performance
and stress testing.

The output has a single cell "TOP" holding:

  * ``--rects`` axis-aligned rectangles on LAYER 0 (default 1000), and
  * ``--paths`` multi-segment polylines on LAYER 1 (default 100),

laid out on a plain grid so the file is easy to eyeball but still
exercises redraw / hit-test / GDS round-trip at scale. Nothing here is
electrically meaningful -- it is filler geometry only.

Usage:
    python examples/generate_perf_test.py
    python examples/generate_perf_test.py --rects 5000 --paths 400 --out big.gds
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

RECT_LAYER = 0
PATH_LAYER = 1

#: Grid pitch (DBU) for the rectangle field, and the rectangle size
#: within each cell of that grid.
RECT_PITCH = 100
RECT_SIZE = 60
RECT_COLS = 40

#: Each path is a horizontal zig-zag of this many vertices, stacked
#: below the rectangle field.
PATH_POINTS = 8
PATH_PITCH = 120
PATH_WIDTH = 20
PATH_SEG = 200


def build(rects: int, paths: int) -> Library:
    if rects < 0 or paths < 0:
        raise ValueError("--rects and --paths must be >= 0")

    lib = Library("PERF_TEST")
    cell = lib.new_cell("TOP")

    for i in range(rects):
        col = i % RECT_COLS
        row = i // RECT_COLS
        x = col * RECT_PITCH
        y = row * RECT_PITCH
        cell.add_rectangle(RECT_LAYER, x, y, x + RECT_SIZE, y + RECT_SIZE)

    # Park the path field a comfortable margin below the rectangles so
    # the two never overlap regardless of the rect count.
    rect_rows = (rects + RECT_COLS - 1) // RECT_COLS
    base_y = -(PATH_PITCH * (paths + 2)) if paths else 0
    base_y = min(base_y, -PATH_PITCH)
    _ = rect_rows  # kept for readability; layout only grows upward

    for j in range(paths):
        y = base_y - j * PATH_PITCH
        pts = []
        for k in range(PATH_POINTS):
            x = k * PATH_SEG
            pts.append((x, y + (PATH_PITCH // 3 if k % 2 else 0)))
        cell.add_path(PATH_LAYER, pts, width=PATH_WIDTH, cap="round")

    lib.check_bounds()
    return lib


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rects", type=int, default=1000,
                    help="rectangles on layer 0 (default 1000)")
    ap.add_argument("--paths", type=int, default=100,
                    help="paths on layer 1 (default 100)")
    ap.add_argument("-o", "--out", default=os.path.join(_here, "perf_test.gds"),
                    help="output .gds path (default: examples/perf_test.gds)")
    args = ap.parse_args(argv)

    lib = build(args.rects, args.paths)
    save_gds(lib, args.out)

    cell = lib.cells["TOP"]
    print(f"{len(cell.shapes)} shapes "
          f"({args.rects} rect / {args.paths} path) -> {args.out}")


if __name__ == "__main__":
    main()
