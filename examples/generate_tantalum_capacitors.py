"""Generate a GDSII library of standard EIA-case surface-mount tantalum
capacitor footprints, one Cell per case code, plus an "INDEX" cell that
places every case in a labelled row for browsing.

Cases: EIA-3216 (A), EIA-3528 (B), EIA-6032 (C), EIA-7343 (D). (Case E,
EIA-7343-43, shares Case D's footprint -- it's only taller -- so it
isn't listed separately here.)

Tantalum capacitors are POLARIZED, unlike an ordinary ceramic chip cap:
pin 1 is the anode (+) end, pin 2 the cathode (-) end. Each package
cell has:

  * two end-termination pads on LAYER 20 -- pin "1" (anode, +) and
    pin "2" (cathode, -) -- each overlapping the body outline the way
    a real wraparound termination does;
  * a body-outline Path on LAYER 61 (0.1 mm) round the nominal body;
  * a polarity band on LAYER 62 next to the anode (+) end, the same
    convention a real datasheet uses to mark the positive terminal;
  * a reference-designator placeholder Text "C" on LAYER 62.

Component origin is the BOTTOM-LEFT corner of the body (matching
hlayout.core.smdlib's / hlayout.io.component_library's convention),
not its centre -- the body spans (0, 0) to (length, width) -- long
axis along X, pin 1 (+) on the -X (left) end.

IMPORTANT: these are round-number nominal dimensions for layout
practice, NOT per-manufacturer datasheet land patterns (AVX TAJ,
KEMET T491, etc. all differ slightly). Check a real datasheet /
IPC-7351 land pattern before committing a board.

Usage:
    python examples/generate_tantalum_capacitors.py
    python examples/generate_tantalum_capacitors.py --out my_tants.gds
    python examples/generate_tantalum_capacitors.py --step-dir step_out --run-freecad
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

PAD_LAYER = 20
OUTLINE_LAYER = 61
LABEL_LAYER = 62
OUTLINE_WIDTH_MM = 0.1
LABEL_HEIGHT_MM = 0.6
POLARITY_BAND_WIDTH_MM = 0.3

#: name -> nominal geometry, all mm.
#:   body      (length, width) of the moulded body outline
#:   pad       (length, width) of each end termination pad -- length
#:             is along the component's long axis, width transverse
#:   overhang  how far a pad's outer edge sits beyond the body outline
#:             (the visible solder toe past the moulded end)
CASES = {
    "EIA-3216 (Case A)": dict(body=(3.2, 1.6), pad=(1.1, 1.4), overhang=0.4),
    "EIA-3528 (Case B)": dict(body=(3.5, 2.8), pad=(1.2, 2.2), overhang=0.4),
    "EIA-6032 (Case C)": dict(body=(6.0, 3.2), pad=(1.5, 2.4), overhang=0.5),
    "EIA-7343 (Case D)": dict(body=(7.3, 4.3), pad=(1.8, 3.4), overhang=0.6),
}

INDEX_PITCH_MM = 12.0

#: typical case height, mm -- 3D export only (the 2D footprint has no
#: use for it). Real tantalum chip heights don't scale much beyond
#: this once the case is bigger than about a D -- these are round
#: numbers for a visualization model, not a datasheet max height.
HEIGHT_MM = {
    "EIA-3216 (Case A)": 1.6,
    "EIA-3528 (Case B)": 2.8,
    "EIA-6032 (Case C)": 2.6,
    "EIA-7343 (Case D)": 2.8,
}


def _pad(cell, cx_mm, cy_mm, l_mm, w_mm, pin):
    hx, hy = mm_to_dbu(l_mm) // 2, mm_to_dbu(w_mm) // 2
    cx, cy = mm_to_dbu(cx_mm), mm_to_dbu(cy_mm)
    cell.add_rectangle(PAD_LAYER, cx - hx, cy - hy, cx + hx, cy + hy, pin=str(pin))


def build_package(cell, spec):
    body_l, body_w = spec["body"]
    pad_l, pad_w = spec["pad"]
    overhang = spec["overhang"]

    # Everything below is computed relative-to-centre, then shifted by
    # (ox, oy) into bottom-left-origin space -- see the module
    # docstring on why the cell's own origin is bottom-left.
    half_body_l, half_body_w = body_l / 2.0, body_w / 2.0
    ox, oy = half_body_l, half_body_w
    pad_outer = half_body_l + overhang       # pad's outer edge, past the body end
    pad_cx = pad_outer - pad_l / 2.0          # pad centre, so it overlaps the body by (pad_l - overhang)

    _pad(cell, -pad_cx + ox, oy, pad_l, pad_w, 1)   # anode (+), -X end
    _pad(cell, pad_cx + ox, oy, pad_l, pad_w, 2)    # cathode (-), +X end

    bl, bw = mm_to_dbu(body_l), mm_to_dbu(body_w)
    cell.add_path(OUTLINE_LAYER,
                  [(0, 0), (bl, 0), (bl, bw), (0, bw), (0, 0)],
                  width=mm_to_dbu(OUTLINE_WIDTH_MM), cap="flush")

    # Polarity band on the anode (+) end, just inside the body outline.
    band_x = mm_to_dbu(-half_body_l + POLARITY_BAND_WIDTH_MM / 2.0 + 0.15 + ox)
    band_oy = mm_to_dbu(oy)
    band_hw = mm_to_dbu(body_w) // 2 - mm_to_dbu(0.15)
    band_hx = mm_to_dbu(POLARITY_BAND_WIDTH_MM) // 2
    cell.add_rectangle(LABEL_LAYER, band_x - band_hx, band_oy - band_hw, band_x + band_hx, band_oy + band_hw)

    cell.add_text(LABEL_LAYER, (mm_to_dbu(ox), mm_to_dbu(oy)), "C", height=mm_to_dbu(LABEL_HEIGHT_MM))


def build() -> Library:
    lib = Library("TANTALUM-CAPACITORS")
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
    own cells, so a STEP file here lines up with its GDS cell -- note
    the overhang= passthrough, or the 3D terminations end up flush
    with the body instead of extending past it like the 2D pads do."""
    parts = []
    for name, spec in CASES.items():
        body_l, body_w = spec["body"]
        pad_l, _pad_w = spec["pad"]
        parts.append((name, "make_end_cap", dict(
            L=body_l, W=body_w, H=HEIGHT_MM[name], term_len=pad_l, overhang=spec["overhang"],
        )))
    return parts


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default=os.path.join(_here, "tantalum_capacitors.gds"),
                    help="output .gds path (default: examples/tantalum_capacitors.gds)")
    ap.add_argument("--step-dir", default=None,
                    help="also write a FreeCAD macro building a matching 3D STEP model "
                         "for every case into this folder")
    ap.add_argument("--run-freecad", action="store_true",
                    help="with --step-dir, also run the macro with freecadcmd to produce "
                         "the .step files now (requires FreeCAD installed)")
    args = ap.parse_args(argv)

    lib = build()
    save_gds(lib, args.out)

    print(f"{len(CASES)} tantalum capacitor cells + INDEX -> {args.out}")
    for name, spec in CASES.items():
        print(f"  {name:20s} body {spec['body'][0]} x {spec['body'][1]} mm  "
              f"pads {spec['pad'][0]} x {spec['pad'][1]} mm")

    if args.step_dir:
        macro_path = write_step_macro(step_parts(), args.step_dir)
        print(f"wrote 3D STEP macro -> {macro_path}")
        if args.run_freecad:
            print(run_step_macro(macro_path))


if __name__ == "__main__":
    main()
