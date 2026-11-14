# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate a range of SMD ceramic-capacitor footprint cells and add
them to hlayout/examples/chip_wire.gds.

One cell per standard EIA chip size (0201 .. 2512). Each cell has:

  * a body-outline Path on LAYER 61, traced round the nominal body
    envelope (L x W);
  * two printed conductor pads on LAYER 3 (the 1st routing conductor),
    pin-numbered "1" / "2" for netlist extraction: each end termination
    grown by PAD_OVERSIZE_MM past the body end and on both sides (the
    inner edge stays at the termination's), so the land shows round the
    part and gives the epoxy fillet somewhere to sit;
  * two conductive-epoxy prints on LAYER 20, one under each termination,
    inset EPOXY_INSET_MM inside it (the epoxy must not spread past the
    termination towards the other pad).

The termination length is the one the 3D models use
(hlayout.core.smdlib: TERMINAL_LENGTH_FRACTION of the body length, at
most TERMINAL_LENGTH_MAX_MM), so pad, epoxy and model always agree.

Component origin is the BOTTOM-LEFT corner of the body outline: the
body spans (0, 0) to (L, W); the pads poke PAD_OVERSIZE_MM past that.
This matches hlayout.io.component_library's 3D STEP models exactly (also
bottom-left origin in X/Y), so a cell built here and one built there
sharing the same name (e.g. CAP_1210) substitute correctly in File >
Export to FreeCAD.

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

PAD_LAYER = 3
EPOXY_LAYER = 20
OUTLINE_LAYER = 61
OUTLINE_WIDTH_MM = 0.05

#: The conductor pad extends this far past the body end and the body sides.
PAD_OVERSIZE_MM = 0.2
#: The epoxy print sits this far inside the termination on every edge.
EPOXY_INSET_MM = 0.05

from hlayout.core.smdlib import TERMINAL_LENGTH_FRACTION as TERM_FRACTION  # noqa: E402
from hlayout.core.smdlib import TERMINAL_LENGTH_MAX_MM as TERM_MAX_MM  # noqa: E402

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
    o = mm_to_dbu(PAD_OVERSIZE_MM)
    i = mm_to_dbu(EPOXY_INSET_MM)

    # body outline (layer 61) -- origin at the bottom-left corner
    cell.add_path(OUTLINE_LAYER,
                  [(0, 0), (L, 0), (L, W), (0, W), (0, 0)],
                  width=mm_to_dbu(OUTLINE_WIDTH_MM), cap="flush")

    # conductor pads (layer 3): each termination, grown past the body end
    # and both sides; the inner edge stays at the termination's
    cell.add_rectangle(PAD_LAYER, -o, -o, band, W + o, pin="1")
    cell.add_rectangle(PAD_LAYER, L - band, -o, L + o, W + o, pin="2")

    # conductive epoxy (layer 20): inside each termination
    cell.add_rectangle(EPOXY_LAYER, i, i, band - i, W - i)
    cell.add_rectangle(EPOXY_LAYER, L - band + i, i, L - i, W - i)


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
        print(f"  CAP_{size}: body {L:.2f} x {Wd:.2f} mm  pad {band + PAD_OVERSIZE_MM:.2f} x "
              f"{Wd + 2 * PAD_OVERSIZE_MM:.2f} mm  gap between pads {L - 2 * band:.2f} mm")


if __name__ == "__main__":
    main()
