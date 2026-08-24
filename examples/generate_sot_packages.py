"""Generate a small library of standard SOT (Small-Outline Transistor)
package footprint cells and save it as a named GDSII library, ready to
be attached to any design with File > Attach Library (or
``s.attach_library(path)``) and placed with Find & Place Cell (or
``s.place_cell(name, origin)``).

    python examples/generate_sot_packages.py
    # writes examples/sot_packages.gds, library name "SOT-PACKAGES"

IMPORTANT: the pad/pitch/body dimensions below are generic, commonly
used land-pattern sizes rounded to this tool's database grid (1 micron
/ 0.001 mm) — they are NOT pulled from any single manufacturer's
datasheet. Treat them as a reasonable starting point for a footprint
library and verify against the actual part's datasheet before using
them for fabrication.

Each package is built as one cell containing:
  - copper pads on the "Pads" layer
  - a body/courtyard outline on the "Outline" layer
  - a reference-designator text label on the "Reference" layer
plus one "INDEX" cell that places every package in a row, purely so
opening the file gives an at-a-glance picture of the whole library.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.units import mm_to_dbu as mm
from hlayout.scripting import Session

LAYER_PADS = 0
LAYER_OUTLINE = 1
LAYER_REFERENCE = 2


def _pad(s, cx_mm, cy_mm, w_mm, h_mm):
    x1, y1 = cx_mm - w_mm / 2, cy_mm - h_mm / 2
    x2, y2 = cx_mm + w_mm / 2, cy_mm + h_mm / 2
    s.rect(LAYER_PADS, mm(x1), mm(y1), mm(x2), mm(y2))


def _outline(s, w_mm, h_mm):
    s.rect(LAYER_OUTLINE, mm(-w_mm / 2), mm(-h_mm / 2), mm(w_mm / 2), mm(h_mm / 2))


def _label(s, name, y_mm):
    s.text(LAYER_REFERENCE, (0, mm(y_mm)), name, height=mm(0.8))


# --- package builders --------------------------------------------------
# Each builder creates (and switches to) its own cell, draws pads
# centered on the origin, an outline, and a name label, then returns
# the cell name so the INDEX cell can reference it.

def build_sot23(s):
    """SOT-23, 3-lead: small-signal transistor/diode. Pitch 1.9 mm,
    two pads on one side, one centered on the other."""
    name = "SOT-23"
    s.new_cell(name)
    pad_w, pad_h = 0.9, 1.1
    _pad(s, -0.95, -1.5, pad_w, pad_h)
    _pad(s, 0.95, -1.5, pad_w, pad_h)
    _pad(s, 0.0, 1.5, pad_w, pad_h)
    _outline(s, 2.9, 2.6)
    _label(s, name, 2.2)
    return name


def build_sot23_5(s):
    """SOT-23-5, 5-lead: 3 pads on one side (0.95 mm pitch), 2 on the other."""
    name = "SOT-23-5"
    s.new_cell(name)
    pad_w, pad_h = 0.4, 1.0
    for i, x in enumerate((-0.95, 0.0, 0.95)):
        _pad(s, x, -1.5, pad_w, pad_h)
    for x in (-0.475, 0.475):
        _pad(s, x, 1.5, pad_w, pad_h)
    _outline(s, 2.9, 2.6)
    _label(s, name, 2.2)
    return name


def build_sot23_6(s):
    """SOT-23-6, 6-lead: 3 pads per side, 0.95 mm pitch."""
    name = "SOT-23-6"
    s.new_cell(name)
    pad_w, pad_h = 0.4, 1.0
    for x in (-0.95, 0.0, 0.95):
        _pad(s, x, -1.5, pad_w, pad_h)
        _pad(s, x, 1.5, pad_w, pad_h)
    _outline(s, 2.9, 2.6)
    _label(s, name, 2.2)
    return name


def build_sot89(s):
    """SOT-89, 3-lead power package: 3 gull-wing pads plus a larger
    tab (collector/drain) pad on the opposite side for heat sinking."""
    name = "SOT-89"
    s.new_cell(name)
    for x in (-1.5, 0.0, 1.5):
        _pad(s, x, -2.0, 1.0, 1.5)
    _pad(s, 0.0, 1.6, 3.0, 2.0)  # tab pad
    _outline(s, 4.5, 4.5)
    _label(s, name, 3.4)
    return name


def build_sot143(s):
    """SOT-143, 4-lead (e.g. dual-gate FET): 2 pads per side, 1.9 mm
    pitch, generic symmetric layout (real parts sometimes offset pin 3)."""
    name = "SOT-143"
    s.new_cell(name)
    pad_w, pad_h = 0.9, 1.1
    for x in (-0.95, 0.95):
        _pad(s, x, -1.5, pad_w, pad_h)
        _pad(s, x, 1.5, pad_w, pad_h)
    _outline(s, 2.9, 2.6)
    _label(s, name, 2.2)
    return name


def build_sot223(s):
    """SOT-223, 4-lead power package: 3 small pins at 2.3 mm pitch
    plus a wide tab pad on the opposite side."""
    name = "SOT-223"
    s.new_cell(name)
    for x in (-2.3, 0.0, 2.3):
        _pad(s, x, -3.0, 1.3, 2.0)
    _pad(s, 0.0, 2.6, 3.5, 2.0)  # tab pad
    _outline(s, 6.7, 7.0)
    _label(s, name, 5.5)
    return name


def build_sot363(s):
    """SOT-363 / SC-70-6, 6-lead: fine pitch (0.65 mm), 3 pads per side."""
    name = "SOT-363"
    s.new_cell(name)
    pad_w, pad_h = 0.4, 0.6
    for x in (-0.65, 0.0, 0.65):
        _pad(s, x, -1.0, pad_w, pad_h)
        _pad(s, x, 1.0, pad_w, pad_h)
    _outline(s, 2.0, 2.1)
    _label(s, name, 1.7)
    return name


BUILDERS = [
    build_sot23,
    build_sot23_5,
    build_sot23_6,
    build_sot89,
    build_sot143,
    build_sot223,
    build_sot363,
]


def main():
    s = Session.new("SOT-PACKAGES")
    s.set_layer_style(LAYER_PADS, name="Pads", color="#f5c518")
    s.set_layer_style(LAYER_OUTLINE, name="Outline", color="#3cb44b", fill=False)
    s.set_layer_style(LAYER_REFERENCE, name="Reference", color="#ffffff")

    names = [build(s) for build in BUILDERS]

    # An INDEX cell laying every package out in a row, spaced well
    # clear of each other, purely so opening the file shows the whole
    # library at a glance.
    s.new_cell("INDEX")
    spacing_mm = 10.0
    for i, name in enumerate(names):
        s.sref(name, (mm(i * spacing_mm), 0))

    out_path = os.path.join(os.path.dirname(__file__), "sot_packages.gds")
    s.save(out_path, library_name="SOT-PACKAGES")
    print(f"Saved {out_path}  (library name: {s.library.name!r})")
    print(f"Packages: {', '.join(names)}")
    print(f"INDEX bbox (DBU): {s.bbox('INDEX')}")
    print()
    print("Attach it to a design with:")
    print(f"    s.attach_library({out_path!r})")
    print("or from the command bar:")
    print(f"    ATTACH {out_path}")


if __name__ == "__main__":
    main()
