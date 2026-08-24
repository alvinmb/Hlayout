"""Example: build a small layout purely from Python (no GUI) and save it
as GDSII. Run with:

    python examples/demo_script.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.units import mm_to_dbu as mm
from hlayout.scripting import Session

s = Session.new("DEMO_HYBRID")

# A resistor pad cell
pad = s.new_cell("PAD")
s.rect(layer=0, x1=0, y1=0, x2=mm(2), y2=mm(2))  # thick-film conductor pad, 2x2mm

# A resistor trace cell
res = s.new_cell("RESISTOR")
s.rect(layer=10, x1=0, y1=0, x2=mm(10), y2=mm(1))  # thick-film resistor paste, 10x1mm
s.text(layer=20, origin=(0, mm(1.2)), text="R1 10K", height=mm(0.6))

# Top-level cell places instances of both
top = s.new_cell("TOP")
s.sref("PAD", origin=(0, 0))
s.sref("RESISTOR", origin=(mm(3), mm(0.5)))
s.sref("PAD", origin=(mm(14), 0))
s.path(layer=30, points=[(mm(2), mm(1)), (mm(3), mm(1))], width=mm(0.2), cap="round")  # thin-film trace
s.path(layer=30, points=[(mm(13), mm(1)), (mm(14), mm(1))], width=mm(0.2), cap="round")

s.set_layer_style(0, name="Thick-Film Conductor", color="#f5c518")
s.set_layer_style(10, name="Thick-Film Resistor", color="#a0522d")
s.set_layer_style(20, name="Silkscreen", color="#ffffff")
s.set_layer_style(30, name="Thin-Film Trace", color="#3cb44b")

out_path = os.path.join(os.path.dirname(__file__), "demo_hybrid.gds")
s.save(out_path)
print(f"Saved {out_path}")
print(f"TOP bbox (DBU): {s.bbox('TOP')}")
