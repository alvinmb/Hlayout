"""Oversize LAYER 2 (the LCC pad/land geometry) by 0.1mm on all sides
and write the result onto LAYER 3, for every package cell in
lcc_packages.gds.

Uses the existing Session.mask_op() OVERSIZE primitive (mitred-corner
buffer via hlayout.core.maskops.size_geometry()) rather than hand-
rolled geometry: it grows LAYER 2's shapes in the cell by the given
amount and writes the result as new Polygon shapes on LAYER 3,
leaving LAYER 2 itself untouched. Run once, in place, against
examples/lcc_packages.gds.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hlayout.core.units import mm_to_dbu
from hlayout.io.gdsio import load_gds, save_gds
from hlayout.scripting.api import Session

GDS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lcc_packages.gds")

SRC_LAYER = 2
DST_LAYER = 3
OVERSIZE_MM = 0.1


def main():
    lib = load_gds(GDS_PATH)
    sess = Session(lib)

    written = {}
    for cell in list(lib.cells.values()):
        if not any(s.layer == SRC_LAYER for s in cell.shapes):
            continue  # e.g. INDEX, which has no layer-2 geometry
        sess.use_cell(cell.name)
        result = sess.mask_op(
            op="OVERSIZE",
            layer_a=SRC_LAYER,
            result_layer=DST_LAYER,
            amount=mm_to_dbu(OVERSIZE_MM),
            cell_names=[cell.name],
            flatten=False,  # these cells draw pads directly, no Srefs to descend into
        )
        written.update(result)

    save_gds(lib, GDS_PATH)

    print(f"Oversized layer {SRC_LAYER} by {OVERSIZE_MM}mm -> layer {DST_LAYER}, saved to {GDS_PATH}")
    for cell_name, n in sorted(written.items()):
        print(f"  {cell_name}: {n} polygon(s) written on layer {DST_LAYER}")
    if not written:
        print("  WARNING: no cells had layer-2 geometry; nothing written")


if __name__ == "__main__":
    main()
