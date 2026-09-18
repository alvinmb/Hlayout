"""Generate a GDSII library of common SOT (Small-Outline Transistor)
package footprints, one Cell per package, plus an "INDEX" cell that
places every package in a labelled row for browsing.

Packages: SOT-23, SOT-23-5, SOT-23-6, SOT-89, SOT-143, SOT-223, SOT-363,
SOT-8, SOT-16, SOT-18, SOT-20, SOT-24, SOT-28.

NOTE ON NAMING: SOT-8/16/18/20/24/28 are gull-wing multi-lead IC
packages, not transistor outlines -- JEDEC properly calls this shape
"SOIC"/"SO" (e.g. real-world SOIC-8, SOIC-20W), not "SOT", which is
otherwise reserved for 3-6 lead parts. They're named SOT-<n> here to
match how they were asked for and to sit alongside the rest of this
file's SOT family; rename the cells if your library wants the
JEDEC-correct SOIC-<n> naming instead.

Each package cell has:

  * copper land pads on LAYER 20, pin-numbered "1".."N" for netlist
    extraction;
  * a body-outline Path on LAYER 61 (0.1 mm) round the nominal body;
  * a reference-designator placeholder Text "U" on LAYER 62.

Pads sit in two rows (left/right of the body) on a regular pitch, the
generic gull-wing arrangement. SOT-89 and SOT-223 carry an oversized
tab pad in place of one ordinary pad. Component origin is the centre
of the body.

IMPORTANT: these are round-number nominal dimensions for layout
practice, NOT per-manufacturer datasheet land patterns. Check a real
datasheet / IPC-7351 land pattern before committing a board.

Usage:
    python examples/generate_sot_packages.py
    python examples/generate_sot_packages.py --out my_sot.gds
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
from hlayout.core.units import mm_to_dbu

PAD_LAYER = 20
OUTLINE_LAYER = 61
LABEL_LAYER = 62
OUTLINE_WIDTH_MM = 0.1
LABEL_HEIGHT_MM = 0.6

#: name -> nominal geometry, all mm.
#:   pins       total pin count (pads are numbered 1..pins)
#:   body       (length, width) of the moulded body outline
#:   pitch      centre-to-centre spacing of pads along each row
#:   row_gap    centre-to-centre spacing between the two pad rows
#:   pad        (length, width) of an ordinary land pad
#:   tab_pin    pin number drawn as an oversized tab, or None
#:   tab        (length, width) of that tab pad, when tab_pin is set
PACKAGES = {
    "SOT-23":   dict(pins=3, body=(2.9, 1.3), pitch=0.95, row_gap=2.3, pad=(1.0, 0.55)),
    "SOT-23-5": dict(pins=5, body=(2.9, 1.6), pitch=0.95, row_gap=2.6, pad=(1.1, 0.55)),
    "SOT-23-6": dict(pins=6, body=(2.9, 1.6), pitch=0.95, row_gap=2.6, pad=(1.1, 0.55)),
    "SOT-89":   dict(pins=4, body=(4.5, 2.5), pitch=1.5, row_gap=4.6, pad=(1.2, 0.6),
                     tab_pin=4, tab=(1.8, 2.0)),
    "SOT-143":  dict(pins=4, body=(2.9, 1.3), pitch=0.95, row_gap=2.3, pad=(1.0, 0.55),
                     tab_pin=4, tab=(1.0, 1.1)),
    "SOT-223":  dict(pins=4, body=(6.5, 3.5), pitch=2.3, row_gap=6.3, pad=(1.6, 0.9),
                     tab_pin=4, tab=(3.8, 2.0)),
    "SOT-363":  dict(pins=6, body=(2.0, 1.25), pitch=0.65, row_gap=2.0, pad=(0.7, 0.4)),
    # Gull-wing multi-lead ICs at the standard 1.27 mm (0.05") SOIC pitch --
    # see the "NOTE ON NAMING" in the module docstring. body is (across
    # the two rows, along a row) here -- the SECOND number is the one
    # that has to grow with pin count, since that's the axis pitch runs
    # along; getting these backwards is exactly the bug fixed 2026-09 --
    # the outline drew rotated 90 degrees for every one of these.
    "SOT-8":    dict(pins=8,  body=(3.9, 4.9),  pitch=1.27, row_gap=6.0, pad=(1.5, 0.6)),
    "SOT-16":   dict(pins=16, body=(3.9, 9.9),  pitch=1.27, row_gap=6.0, pad=(1.5, 0.6)),
    "SOT-18":   dict(pins=18, body=(7.5, 11.5), pitch=1.27, row_gap=9.8, pad=(1.5, 0.6)),
    "SOT-20":   dict(pins=20, body=(7.5, 12.8), pitch=1.27, row_gap=9.8, pad=(1.5, 0.6)),
    "SOT-24":   dict(pins=24, body=(7.5, 15.4), pitch=1.27, row_gap=9.8, pad=(1.5, 0.6)),
    "SOT-28":   dict(pins=28, body=(7.5, 17.9), pitch=1.27, row_gap=9.8, pad=(1.5, 0.6)),
}

INDEX_PITCH_MM = 12.0


def _pad(cell, cx_mm, cy_mm, l_mm, w_mm, pin):
    hx, hy = mm_to_dbu(l_mm) // 2, mm_to_dbu(w_mm) // 2
    cx, cy = mm_to_dbu(cx_mm), mm_to_dbu(cy_mm)
    cell.add_rectangle(PAD_LAYER, cx - hx, cy - hy, cx + hx, cy + hy, pin=str(pin))


def build_package(cell, spec):
    pins = spec["pins"]
    body_l, body_w = spec["body"]
    pitch = spec["pitch"]
    row_gap = spec["row_gap"]
    pad_l, pad_w = spec["pad"]
    tab_pin = spec.get("tab_pin")
    tab = spec.get("tab")

    per_row = (pins + 1) // 2          # row 1 (pins 1..per_row) is the left/lower row
    # Pads run along the y axis; the two rows are offset in x by row_gap.
    def _row_positions(count):
        span = (count - 1) * pitch
        return [(-span / 2.0) + k * pitch for k in range(count)]

    left_x = -row_gap / 2.0
    right_x = row_gap / 2.0

    left_ys = _row_positions(per_row)
    right_ys = list(reversed(_row_positions(pins - per_row)))

    n = 1
    for y in left_ys:
        if tab_pin == n and tab:
            _pad(cell, left_x, y, tab[0], tab[1], n)
        else:
            _pad(cell, left_x, y, pad_l, pad_w, n)
        n += 1
    for y in right_ys:
        if tab_pin == n and tab:
            _pad(cell, right_x, y, tab[0], tab[1], n)
        else:
            _pad(cell, right_x, y, pad_l, pad_w, n)
        n += 1

    hl, hw = mm_to_dbu(body_l) // 2, mm_to_dbu(body_w) // 2
    cell.add_path(OUTLINE_LAYER,
                  [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw), (-hl, -hw)],
                  width=mm_to_dbu(OUTLINE_WIDTH_MM), cap="flush")

    cell.add_text(LABEL_LAYER, (0, 0), "U", height=mm_to_dbu(LABEL_HEIGHT_MM))


def build() -> Library:
    lib = Library("SOT-PACKAGES")
    for name, spec in PACKAGES.items():
        build_package(lib.new_cell(name), spec)

    index = lib.new_cell("INDEX")
    for i, name in enumerate(PACKAGES):
        index.add_sref(name, (i * mm_to_dbu(INDEX_PITCH_MM), 0))
        index.add_text(LABEL_LAYER,
                       (i * mm_to_dbu(INDEX_PITCH_MM), mm_to_dbu(-4.0)),
                       name, height=mm_to_dbu(LABEL_HEIGHT_MM))

    lib.check_bounds()
    return lib


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default=os.path.join(_here, "sot_packages.gds"),
                    help="output .gds path (default: examples/sot_packages.gds)")
    args = ap.parse_args(argv)

    lib = build()
    save_gds(lib, args.out)

    print(f"{len(PACKAGES)} SOT package cells + INDEX -> {args.out}")
    for name, spec in PACKAGES.items():
        print(f"  {name:9s} {spec['pins']} pins  body {spec['body'][0]} x {spec['body'][1]} mm")


if __name__ == "__main__":
    main()
