"""Example: generate a synthetic design with 1000 rectangles and 100
paths, purely for exercising canvas draw/pan/drag performance (see the
"Canvas performance" section of the README and
tests/test_canvas_performance.py). Run with:

    python examples/generate_perf_test.py [--rects N] [--paths N]

Then open the result in the GUI to try it out interactively:

    python hlayout_app.py examples/perf_test.gds

and pan (middle/right-drag), zoom, and drag individual shapes around to
feel the difference the redraw-coalescing/grid-cap changes make.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.units import mm_to_dbu as mm
from hlayout.scripting import Session

# Grid layout parameters for the rectangles, in mm.
RECT_SIZE = 1.0
RECT_PITCH = 1.5
RECT_COLS = 40  # 40 x 25 = 1000 rectangles

# Layout parameters for the paths, in mm.
PATH_WIDTH = 0.2
PATH_LENGTH = 20.0
PATH_ROW_SPACING = 0.6
PATH_ROWS_BELOW_RECTS = 3.0  # gap below the rectangle block, in mm


def generate(num_rects, num_paths, out_path):
    s = Session.new("PERF_TEST")
    s.new_cell("TOP")

    s.set_layer_style(0, name="Rectangles", color="#f5c518")
    s.set_layer_style(1, name="Paths", color="#3cb44b")

    # --- rectangles, laid out in a grid -----------------------------
    cols = RECT_COLS
    rows = (num_rects + cols - 1) // cols
    for i in range(num_rects):
        col = i % cols
        row = i // cols
        x = mm(col * RECT_PITCH)
        y = mm(row * RECT_PITCH)
        s.rect("Rectangles", x, y, x + mm(RECT_SIZE), y + mm(RECT_SIZE))

    # --- paths, stacked below the rectangle block, alternating cap --
    rect_block_height = rows * RECT_PITCH
    y0 = rect_block_height + PATH_ROWS_BELOW_RECTS
    for i in range(num_paths):
        y = mm(y0 + i * PATH_ROW_SPACING)
        cap = "round" if i % 2 == 0 else "square"
        s.path("Paths", [(0, y), (mm(PATH_LENGTH), y)], width=mm(PATH_WIDTH), cap=cap)

    s.save(out_path)
    return s


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rects", type=int, default=1000, help="number of rectangles (default: 1000)")
    parser.add_argument("--paths", type=int, default=100, help="number of paths (default: 100)")
    parser.add_argument(
        "--out",
        default=os.path.join(os.path.dirname(__file__), "perf_test.gds"),
        help="output GDS path (default: examples/perf_test.gds)",
    )
    args = parser.parse_args()

    s = generate(args.rects, args.paths, args.out)

    print(f"Saved {args.out}")
    print(f"rectangles: {len(s.shapes_on_layer('Rectangles'))}")
    print(f"paths: {len(s.shapes_on_layer('Paths'))}")
    print(f"total shapes: {len(s.cell.shapes)}")


if __name__ == "__main__":
    main()
