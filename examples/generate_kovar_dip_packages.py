# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate a GDSII library of Kovar dual-in-line hybrid packages
(20, 30 and 40 pins by default): 0.1 in (2.54 mm) pin pitch, 0.9 in
(22.86 mm) row to row, each pin in a glass-to-metal seal through the
package floor. Matching 3D STEP models come from
examples/step_models/kovar_dip/build_step_models.py (same dimensions,
same origin).

Each package cell KOVAR_DIP<n> has:

  * the outside of the Kovar body and the inside of the cavity as two
    closed outline Paths on LAYER 61 (0.1 mm), every corner chamfered
    0.6 mm -- the walls are 1 mm thick;
  * a numbered pad per pin: the glass seal's footprint (1.6 mm round)
    on LAYER 49, pin "1".."n" (pin 1 bottom left, counter-clockwise from
    above) -- the bond target, and what shows the pin number on screen
    and feeds the netlist / I/O pad numbering;
  * one placed KOVAR_PIN_SEAL per pin (its pin number set too), drawing
    the pin (0.46 mm) and seal circles on LAYER 63;
  * a pin-1 dot and a "J" ref-des placeholder on LAYER 62.

These are the same layers as the Header_Pin_Round pins already used in
the example designs. Put layer 49 in the Wire Bond rules' pad layers if
your design does not already.

Origin: the BOTTOM-LEFT corner of the body, the body spanning (0, 0) to
(L, W) -- this family's convention; the STEP models use the same
origin, underside at Z = 0.

Usage:
    python examples/generate_kovar_dip_packages.py
    python examples/generate_kovar_dip_packages.py --pins 20 24 28 --out my_kovar.gds
"""
import argparse
import math
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
from hlayout.core.units import mm_to_dbu
from hlayout.io.gdsio import save_gds

SEAL_LAYER = 49
OUTLINE_LAYER = 61
LABEL_LAYER = 62
DRAWING_LAYER = 63
OUTLINE_WIDTH_MM = 0.1
LABEL_HEIGHT_MM = 1.0

#: all mm -- keep in step with step_models/kovar_dip/build_step_models.py
PITCH_MM = 2.54          # 0.1 in, pin to pin
ROW_MM = 22.86           # 0.9 in, row to row
WALL_MM = 1.0
CHAMFER_MM = 0.6         # inside and outside wall corners
INNER_W_MM = 26.0        # cavity width
END_MARGIN_MM = 2.27     # cavity end wall to the end pin centre
PIN_D_MM = 0.46
SEAL_D_MM = 1.60
PIN1_DOT_MM = 0.5
CIRCLE_POINTS = 32
INDEX_GAP_MM = 8.0

PIN_CELL = "KOVAR_PIN_SEAL"


def body_size(n):
    """(outer L, outer W, cavity L, cavity W), mm."""
    per = n // 2
    il = (per - 1) * PITCH_MM + 2 * END_MARGIN_MM
    return il + 2 * WALL_MM, INNER_W_MM + 2 * WALL_MM, il, INNER_W_MM


def pin_sites(n):
    """[(pin, x, y)] mm, body bottom-left at (0, 0); pin 1 bottom left,
    counter-clockwise from above."""
    L, W, _il, _iw = body_size(n)
    per = n // 2
    x0 = L / 2.0 - (per - 1) * PITCH_MM / 2.0
    yb, yt = W / 2.0 - ROW_MM / 2.0, W / 2.0 + ROW_MM / 2.0
    out = [(k + 1, x0 + k * PITCH_MM, yb) for k in range(per)]
    out += [(per + k + 1, x0 + (per - 1 - k) * PITCH_MM, yt) for k in range(per)]
    return out


def _circle(d_mm):
    r = mm_to_dbu(d_mm) / 2.0
    return [(int(round(r * math.cos(2 * math.pi * i / CIRCLE_POINTS))),
             int(round(r * math.sin(2 * math.pi * i / CIRCLE_POINTS))))
            for i in range(CIRCLE_POINTS)]


def _chamfered_ring(x0, y0, x1, y1, c):
    """A closed point list (DBU) round the box, every corner cut by a
    45 degree chamfer of leg c."""
    return [(x0 + c, y0), (x1 - c, y0), (x1, y0 + c), (x1, y1 - c), (x1 - c, y1),
            (x0 + c, y1), (x0, y1 - c), (x0, y0 + c), (x0 + c, y0)]


def build_pin_cell(lib):
    if PIN_CELL in lib.cells:
        return lib.cells[PIN_CELL]
    c = lib.new_cell(PIN_CELL)
    c.add_polygon(DRAWING_LAYER, _circle(SEAL_D_MM))
    c.add_polygon(DRAWING_LAYER, _circle(PIN_D_MM))
    return c


def build_package(lib, n):
    if n % 2 or n < 4:
        raise ValueError(f"pin count must be even and >= 4, not {n}")
    build_pin_cell(lib)
    name = f"KOVAR_DIP{n}"
    cell = lib.new_cell(name)
    L, W, il, iw = body_size(n)
    w = mm_to_dbu(OUTLINE_WIDTH_MM)
    c = mm_to_dbu(CHAMFER_MM)
    cell.add_path(OUTLINE_LAYER, _chamfered_ring(0, 0, mm_to_dbu(L), mm_to_dbu(W), c), w)
    cell.add_path(OUTLINE_LAYER, _chamfered_ring(mm_to_dbu(WALL_MM), mm_to_dbu(WALL_MM),
                                                 mm_to_dbu(WALL_MM + il),
                                                 mm_to_dbu(WALL_MM + iw), c), w)
    seal = _circle(SEAL_D_MM)
    for pin, x, y in pin_sites(n):
        px, py = mm_to_dbu(x), mm_to_dbu(y)
        # the numbered seal pad, flat in the package cell
        cell.add_polygon(SEAL_LAYER, [(px + sx, py + sy) for sx, sy in seal], pin=str(pin))
        cell.add_sref(PIN_CELL, (px, py), pin=str(pin))
    # pin-1 dot beside pin 1, between its seal and the end wall
    _p, x1, y1 = pin_sites(n)[0]
    dot = [(px + mm_to_dbu(x1 - SEAL_D_MM / 2.0 - (x1 - WALL_MM - SEAL_D_MM / 2.0) / 2.0),
            py + mm_to_dbu(y1)) for px, py in _circle(PIN1_DOT_MM)]
    cell.add_polygon(LABEL_LAYER, dot)
    cell.add_text(LABEL_LAYER, (mm_to_dbu(L / 2.0), mm_to_dbu(W / 2.0)), "J",
                  height=mm_to_dbu(LABEL_HEIGHT_MM))
    return cell


def build(pin_counts):
    lib = Library("KOVAR_DIP")
    index = lib.new_cell("INDEX")
    x = 0
    for n in pin_counts:
        build_package(lib, n)
        index.add_sref(f"KOVAR_DIP{n}", (x, 0))
        index.add_text(LABEL_LAYER, (x, -mm_to_dbu(3.0)), f"KOVAR_DIP{n}",
                       height=mm_to_dbu(LABEL_HEIGHT_MM))
        x += mm_to_dbu(body_size(n)[0] + INDEX_GAP_MM)
    lib.top_cell_name = "INDEX"
    return lib


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--pins", type=int, nargs="+", default=[20, 30, 40])
    ap.add_argument("--out", default=os.path.join(_here, "kovar_dip_packages.gds"))
    args = ap.parse_args()
    lib = build(args.pins)
    save_gds(lib, args.out)
    for n in args.pins:
        L, W, il, iw = body_size(n)
        print(f"KOVAR_DIP{n}: body {L:.2f} x {W:.2f} mm, cavity {il:.2f} x {iw:.2f} mm")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
