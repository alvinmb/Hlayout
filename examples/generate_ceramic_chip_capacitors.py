"""Generate a GDSII library of standard EIA-case ceramic (MLCC) chip
capacitor footprints, one Cell per case code, plus an "INDEX" cell that
places every case in a labelled row for browsing.

Ceramic chip capacitors are NON-polarized, two-terminal parts: unlike
a tantalum's wraparound solder fillet (generate_tantalum_capacitors.py),
their end metallization terminations sit FLUSH with the body ends and
span the full body width.

Layer layout for each package cell -- every growth/shrink below is
UNIFORM ("larger/smaller all around", i.e. isotropic on all 4 sides),
the same convention Tools > Mask Operations... OVERSIZE/UNDERSIZE uses:

  * body outline Path on LAYER 61 (0.1mm wide), round the nominal body.
  * conductor pad Rectangle on LAYER 3, pin-numbered "1"/"2" -- each
    pad is the case's nominal terminal footprint (full body width,
    TERMINAL_LEN_MM long, flush with the body end) grown PAD_GROW_MM
    (0.2mm) larger on every side.
  * solder mask Rectangle on LAYER 30 -- the conductor pad shrunk
    MASK_SHRINK_MM (0.1mm) smaller on every side.
  * a fourth Rectangle on LAYER 4 -- the conductor pad grown
    LAYER4_GROW_MM (0.1mm) larger on every side.
  * a reference-designator placeholder Text "C" on LAYER 62.

Component origin is the BOTTOM-LEFT corner of the body (matching
hlayout.core.smdlib's / hlayout.io.component_library's convention --
see hlayout.io.example_step3d's own docstring), not its centre -- the
body spans (0, 0) to (length, width) -- long axis along X, pin "1" on
the -X (left) end. step_parts() below builds each case's 3D model with
hlayout.io.example_step3d.make_end_cap(overhang=0) -- SAME bottom-left
origin, flush (non-overhanging) terminations -- so a STEP file here
lines up exactly with the GDS cell of the same name.

IMPORTANT: these are round-number nominal case dimensions for layout
practice, NOT per-manufacturer datasheet land patterns (case body size
and terminal length both vary between vendors/dielectrics/voltage
ratings) -- check a real datasheet / IPC-7351 land pattern before
committing a board (same caveat generate_tantalum_capacitors.py
documents).

Usage:
    python examples/generate_ceramic_chip_capacitors.py
    python examples/generate_ceramic_chip_capacitors.py --out my_caps.gds
    python examples/generate_ceramic_chip_capacitors.py --step-dir step_out --run-freecad
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
from hlayout.io.example_step3d import run_step_macro, write_step_macro

PAD_LAYER = 3        # "conductor"
OUTLINE_LAYER = 61
MASK_LAYER = 30       # solder mask
LAYER4 = 4            # pad grown 0.1mm larger on every side
LABEL_LAYER = 62
OUTLINE_WIDTH_MM = 0.1
LABEL_HEIGHT_MM = 0.6

PAD_GROW_MM = 0.2      # terminal -> conductor pad (LAYER 3)
MASK_SHRINK_MM = 0.1   # conductor pad -> solder mask (LAYER 30)
LAYER4_GROW_MM = 0.1   # conductor pad -> LAYER 4 shape

#: name -> nominal geometry, all mm.
#:   body  (length, width) of the moulded/ceramic body outline
#:   term  termination length along the long axis, flush with each
#:         body end -- termination width is the FULL body width (a
#:         real MLCC's end metallization wraps the whole end face)
CASES = {
    "EIA-0201": dict(body=(0.60, 0.30), term=0.10),
    "EIA-0402": dict(body=(1.00, 0.50), term=0.25),
    "EIA-0603": dict(body=(1.60, 0.80), term=0.30),
    "EIA-0805": dict(body=(2.00, 1.25), term=0.40),
    "EIA-1206": dict(body=(3.20, 1.60), term=0.50),
    "EIA-1210": dict(body=(3.20, 2.50), term=0.50),
    "EIA-1812": dict(body=(4.50, 3.20), term=0.60),
    "EIA-2220": dict(body=(5.70, 5.00), term=0.60),
    "EIA-2225": dict(body=(5.70, 6.30), term=0.60),
}

INDEX_PITCH_MM = 10.0

#: typical case height, mm -- 3D export only (the 2D footprint has no
#: use for it). Round numbers for a visualization model, NOT a
#: datasheet max height (real height depends on dielectric/voltage).
HEIGHT_MM = {
    "EIA-0201": 0.30,
    "EIA-0402": 0.50,
    "EIA-0603": 0.80,
    "EIA-0805": 0.85,
    "EIA-1206": 1.15,
    "EIA-1210": 1.15,
    "EIA-1812": 1.40,
    "EIA-2220": 1.50,
    "EIA-2225": 1.50,
}


def _grow(rect, amount_mm):
    """Grow (amount_mm > 0) or shrink (amount_mm < 0) a
    (x0, y0, x1, y1) mm rectangle by amount_mm on every side."""
    x0, y0, x1, y1 = rect
    return (x0 - amount_mm, y0 - amount_mm, x1 + amount_mm, y1 + amount_mm)


def _rect_dbu(cell, layer, rect_mm, pin=""):
    x0, y0, x1, y1 = rect_mm
    cell.add_rectangle(layer, mm_to_dbu(x0), mm_to_dbu(y0), mm_to_dbu(x1), mm_to_dbu(y1),
                        pin=str(pin) if pin else "")


def build_package(cell, spec):
    body_l, body_w = spec["body"]
    term_len = spec["term"]

    # Terminal footprints -- flush with each body end, full body width.
    term1 = (0.0, 0.0, term_len, body_w)                    # pin 1, -X end
    term2 = (body_l - term_len, 0.0, body_l, body_w)        # pin 2, +X end

    for term, pin in ((term1, 1), (term2, 2)):
        pad = _grow(term, PAD_GROW_MM)
        mask = _grow(pad, -MASK_SHRINK_MM)
        layer4_shape = _grow(pad, LAYER4_GROW_MM)

        _rect_dbu(cell, PAD_LAYER, pad, pin=pin)
        _rect_dbu(cell, MASK_LAYER, mask)
        _rect_dbu(cell, LAYER4, layer4_shape)

    bl, bw = mm_to_dbu(body_l), mm_to_dbu(body_w)
    cell.add_path(OUTLINE_LAYER,
                  [(0, 0), (bl, 0), (bl, bw), (0, bw), (0, 0)],
                  width=mm_to_dbu(OUTLINE_WIDTH_MM), cap="flush")

    cell.add_text(LABEL_LAYER, (mm_to_dbu(body_l / 2.0), mm_to_dbu(body_w / 2.0)), "C",
                  height=mm_to_dbu(LABEL_HEIGHT_MM))


def build() -> Library:
    lib = Library("CERAMIC-CHIP-CAPACITORS")
    for name, spec in CASES.items():
        build_package(lib.new_cell(name), spec)

    index = lib.new_cell("INDEX")
    for i, name in enumerate(CASES):
        index.add_sref(name, (i * mm_to_dbu(INDEX_PITCH_MM), 0))
        index.add_text(LABEL_LAYER,
                       (i * mm_to_dbu(INDEX_PITCH_MM), mm_to_dbu(-4.0)),
                       name, height=mm_to_dbu(LABEL_HEIGHT_MM))

    lib.check_bounds()
    return lib


def step_parts():
    """(name, "make_end_cap", kwargs) for every case -- see
    hlayout.io.example_step3d. Bottom-left origin, matching build()'s
    own cells, so a STEP file here lines up with its GDS cell.
    overhang=0 -- ceramic terminations are flush with the body, unlike
    a tantalum's wraparound solder fillet."""
    parts = []
    for name, spec in CASES.items():
        body_l, body_w = spec["body"]
        parts.append((name, "make_end_cap", dict(
            L=body_l, W=body_w, H=HEIGHT_MM[name], term_len=spec["term"], overhang=0.0,
        )))
    return parts


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default=os.path.join(_here, "ceramic_chip_capacitors.gds"),
                    help="output .gds path (default: examples/ceramic_chip_capacitors.gds)")
    ap.add_argument("--step-dir", default=None,
                    help="also write a FreeCAD macro building a matching 3D STEP model "
                         "for every case into this folder")
    ap.add_argument("--run-freecad", action="store_true",
                    help="with --step-dir, also run the macro with freecadcmd to produce "
                         "the .step files now (requires FreeCAD installed)")
    args = ap.parse_args(argv)

    lib = build()
    save_gds(lib, args.out)

    print(f"{len(CASES)} ceramic chip capacitor cells + INDEX -> {args.out}")
    for name, spec in CASES.items():
        body_l, body_w = spec["body"]
        term = spec["term"]
        pad_l = term + 2 * PAD_GROW_MM
        pad_w = body_w + 2 * PAD_GROW_MM
        print(f"  {name:10s} body {body_l:.2f} x {body_w:.2f} mm  "
              f"terminal {term:.2f} mm  pad {pad_l:.2f} x {pad_w:.2f} mm")

    if args.step_dir:
        macro_path = write_step_macro(step_parts(), args.step_dir)
        print(f"wrote 3D STEP macro -> {macro_path}")
        if args.run_freecad:
            print(run_step_macro(macro_path))


if __name__ == "__main__":
    main()
