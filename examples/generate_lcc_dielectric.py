# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate the dielectric fill (with windows/cutouts around each
component pad) for every package cell in lcc_packages.gds.

This is the second half of the LCC dielectric-window workflow:
oversize_lcc_layer2.py already grew each pad (LAYER 2) by 0.1mm and
wrote the result onto LAYER 3, as datatype-0 "hole" shapes -- that is
the input Session.generate_dielectric() expects (see its docstring):
everything already drawn on the dielectric layer at datatype 0 is
either the fill boundary (the one shape enclosing all the others) or a
hole to cut out of it. Since none of LAYER 3's 8 pad shapes encloses
the rest, generate_dielectric() falls back to the cell's own bounding
box (grown by `margin`) as the fill boundary, and cuts a window around
each oversized pad shape (grown further by `clearance`) out of it --
producing a dielectric sheet with a clearance window around every
component pad, written at LAYER 3 datatype 1 (DIELECTRIC_FILL_DATATYPE),
alongside (not replacing) the datatype-0 pad-window shapes it was
built from.

margin/clearance below match this project's own 2-Layer_solder_Build
tech file's dielectric_rules (0.3mm / 0.2mm) for consistency, even
though this standalone example library has no tech file applied.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hlayout.core.units import mm_to_dbu
from hlayout.io.gdsio import load_gds, save_gds
from hlayout.scripting.api import Session

GDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lcc_packages.gds")

DIELECTRIC_LAYER = 3
MARGIN_MM = 0.3
CLEARANCE_MM = 0.2


def main():
    lib = load_gds(GDS_PATH)
    sess = Session(lib)

    written = {}
    for cell in list(lib.cells.values()):
        if not any(s.layer == DIELECTRIC_LAYER for s in cell.shapes):
            continue  # e.g. INDEX, which has no layer-3 window shapes
        n = sess.generate_dielectric(
            layer=DIELECTRIC_LAYER,
            margin=mm_to_dbu(MARGIN_MM),
            clearance=mm_to_dbu(CLEARANCE_MM),
            cell_name=cell.name,
        )
        written[cell.name] = n

    save_gds(lib, GDS_PATH)

    print(f"Generated dielectric fill on layer {DIELECTRIC_LAYER} "
          f"(margin {MARGIN_MM}mm, clearance {CLEARANCE_MM}mm), saved to {GDS_PATH}")
    for cell_name, n in sorted(written.items()):
        print(f"  {cell_name}: {n} fill polygon(s) written")
    if not written:
        print("  WARNING: no cells had layer-3 window geometry; nothing written")


if __name__ == "__main__":
    main()
