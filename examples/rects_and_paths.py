"""Example: add 5 rectangles, 5 round-ended paths, and 5 square-ended
paths, then save the result as GDSII. Run with:

    python examples/rects_and_paths.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.units import mm_to_dbu as mm
from hlayout.scripting import Session

s = Session.new("RECTS_AND_PATHS")
s.new_cell("TOP")

# Give the layers we're using meaningful names (optional, but lets you
# refer to them by name from here on, in the GUI command bar, etc.)
s.set_layer_style(0, name="Rectangles", color="#f5c518")
s.set_layer_style(1, name="Round-Cap Paths", color="#3cb44b")
s.set_layer_style(2, name="Square-Cap Paths", color="#4363d8")

# --- 5 rectangles, laid out left to right ---------------------------
for i in range(5):
    x = mm(i * 3)
    s.rect("Rectangles", x, 0, x + mm(2), mm(2))

# --- 5 paths with round end caps -------------------------------------
for i in range(5):
    y = mm(4 + i * 1.5)
    s.path("Round-Cap Paths", [(0, y), (mm(10), y)], width=mm(0.5), cap="round")

# --- 5 paths with square end caps -------------------------------------
for i in range(5):
    y = mm(13 + i * 1.5)
    s.path("Square-Cap Paths", [(0, y), (mm(10), y)], width=mm(0.5), cap="square")

out_path = os.path.join(os.path.dirname(__file__), "rects_and_paths.gds")
s.save(out_path)
print(f"Saved {out_path}")
print(f"rectangles: {len(s.shapes_on_layer('Rectangles'))}")
print(f"round-cap paths: {len(s.shapes_on_layer('Round-Cap Paths'))}")
print(f"square-cap paths: {len(s.shapes_on_layer('Square-Cap Paths'))}")
