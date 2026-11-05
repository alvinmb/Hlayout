# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate a range of SMD ceramic-capacitor footprint cells and add
them to hlayout/examples/chip_wire.gds.

One cell per standard EIA chip size (0201 .. 2512). Each cell has:

  * a body-outline Path on LAYER 61, traced round the nominal body
    envelope (L x W);
  * two epoxy-print pads on LAYER 20, one under each end termination --
    the conductive-epoxy die-attach print for chip-and-wire assembly.
    Each epoxy pad is the termination band grown by EPOXY_OVERSIZE_MM
    on every side (so it's EPOXY_OVERSIZE_MM larger than the contact
    it prints under, giving room for placement tolerance and a fillet).
    Pads are pin-numbered "1" / "2" for netlist extraction.

Component origin is the BOTTOM-LEFT corner of the body outline: the
body spans (0, 0) to (L, W); the epoxy pads poke EPOXY_OVERSIZE_MM
past that, as a real epoxy print does. This matches
hlayout.io.component_library's 3D STEP models exactly (also
bottom-left origin in X/Y), so a cell built here and one built there
sharing the same name (e.g. CAP_1210) substitute correctly in File >
Export to FreeCAD: the 3D body lands on the real footprint instead of
offset by the body size.

Run:  python hlayout/examples/generate_ceramic_caps.py
"""
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
from hlayout.io.gdsio import load_gds, save_gds
from hlayout.core.units import mm_to_dbu

TARGET_GDS = os.path.join(_root, "hlayout", "examples", "chip_wire.gds")

EPOXY_LAYER = 20
OUTLINE_LAYER = 61
OUTLINE_WIDTH_MM = 0.05

#: Epoxy print oversize -- the epoxy pad extends this far beyond the end
#: termination it prints under, on every edge.
EPOXY_OVERSIZE_MM = 0.2

#: End-termination band as a fraction of body length, capped -- a
#: generic nominal (real chip terminals run ~a quarter to a third of
#: body length on small sizes); not per-manufacturer datasheet values.
TERM_FRACTION = 0.25
TERM_MAX_MM = 0.6

#: EIA chip size -> (length_mm, width_mm), nominal body envelope.
CHIP_SIZES_MM = {
    "0201": (0.60, 0.30),
    "0402": (1.00, 0.50),
    "0603": (1.60, 0.80),
    "0805": (2.00, 1.25),
    "1206": (3.20, 1.60),
    "1210": (3.20, 2.50),
    "1812": (4.50, 3.20),
    "2010": (5.00, 2.50),
    "2512": (6.40, 3.20),
}


def _build_cap(cell, length_mm, width_mm):
    L = mm_to_dbu(length_mm)
    W = mm_to_dbu(width_mm)
    band = mm_to_dbu(min(length_mm * TERM_FRACTION, TERM_MAX_MM))
    e = mm_to_dbu(EPOXY_OVERSIZE_MM)

    # body outline (layer 61) -- origin at the bottom-left corner
    cell.add_path(OUTLINE_LAYER,
                  [(0, 0), (L, 0), (L, W), (0, W), (0, 0)],
                  width=mm_to_dbu(OUTLINE_WIDTH_MM), cap="flush")

    # epoxy print pads (layer 20): each end's termination band, grown by
    # e on all four sides.
    cell.add_rectangle(EPOXY_LAYER, -e, -e, band + e, W + e, pin="1")
    cell.add_rectangle(EPOXY_LAYER, L - band - e, -e, L + e, W + e, pin="2")


def main():
    if os.path.exists(TARGET_GDS):
        lib = load_gds(TARGET_GDS)
    else:
        lib = Library("chip_wire")

    made = []
    for size, (length_mm, width_mm) in CHIP_SIZES_MM.items():
        name = f"CAP_{size}"
        if name in lib.cells:
            lib.delete_cell(name)
        cell = lib.new_cell(name)
        _build_cap(cell, length_mm, width_mm)
        made.append(name)

    lib.check_bounds()
    save_gds(lib, TARGET_GDS)

    print(f"{len(made)} ceramic-cap cells -> {TARGET_GDS}")
    for size in CHIP_SIZES_MM:
        L, Wd = CHIP_SIZES_MM[size]
        band = min(L * TERM_FRACTION, TERM_MAX_MM)
        pad_l = band + 2 * EPOXY_OVERSIZE_MM
        pad_w = Wd + 2 * EPOXY_OVERSIZE_MM
        print(f"  CAP_{size}: body {L:.2f} x {Wd:.2f} mm  "
              f"epoxy pad {pad_l:.2f} x {pad_w:.2f} mm  (band {band:.2f} + {EPOXY_OVERSIZE_MM} each side)")


if __name__ == "__main__":
    main()
