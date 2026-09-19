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
tab pad in place of one ordinary pad. Component origin is the
BOTTOM-LEFT corner of the body (matching hlayout.core.smdlib's /
hlayout.io.component_library's convention), not its centre -- the
body spans (0, 0) to (body[0], body[1]).

IMPORTANT: these are round-number nominal dimensions for layout
practice, NOT per-manufacturer datasheet land patterns. Check a real
datasheet / IPC-7351 land pattern before committing a board.

Usage:
    python examples/generate_sot_packages.py
    python examples/generate_sot_packages.py --out my_sot.gds
    python examples/generate_sot_packages.py --step-dir step_out --run-freecad
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
    # SOT-8's body/row_gap/pitch match a real 8-pin SOIC datasheet
    # drawing (body 3.95 x 5.1, row_gap/lead-span 6.0, pitch 1.27, all
    # confirmed against it directly) -- SOT-16 shares its narrow-body
    # width (3.95) for the same reason, but keeps its own already-
    # reasonable along-row length (9.9) since no 16-pin reference was
    # checked against.
    "SOT-8":    dict(pins=8,  body=(3.95, 5.1), pitch=1.27, row_gap=6.0, pad=(1.5, 0.6)),
    "SOT-16":   dict(pins=16, body=(3.95, 9.9), pitch=1.27, row_gap=6.0, pad=(1.5, 0.6)),
    "SOT-18":   dict(pins=18, body=(7.5, 11.5), pitch=1.27, row_gap=9.8, pad=(1.5, 0.6)),
    "SOT-20":   dict(pins=20, body=(7.5, 12.8), pitch=1.27, row_gap=9.8, pad=(1.5, 0.6)),
    "SOT-24":   dict(pins=24, body=(7.5, 15.4), pitch=1.27, row_gap=9.8, pad=(1.5, 0.6)),
    "SOT-28":   dict(pins=28, body=(7.5, 17.9), pitch=1.27, row_gap=9.8, pad=(1.5, 0.6)),
}

#: body height, mm -- 3D export only, no use in the 2D footprint.
#: Matches hlayout.io.component_library.DEFAULT_SOT_PACKAGES's own
#: values where a name overlaps (SOT_23/323/89/223 there), so the two
#: independent 3D conventions at least agree on this one number.
#: SOT-8/16/18/20/24/28's own 2.5 mm matches the same 8-pin SOIC
#: datasheet drawing body=(3.95,5.1) above (overall height "A" = 2.5),
#: applied to the whole gull-wing sub-family since package height
#: doesn't vary much with pin count/body width the way length does.
HEIGHT_MM = {
    "SOT-23": 1.1, "SOT-23-5": 1.1, "SOT-23-6": 1.1,
    "SOT-89": 1.5, "SOT-143": 1.1, "SOT-223": 1.6, "SOT-363": 0.95,
    "SOT-8": 2.5, "SOT-16": 2.5, "SOT-18": 2.5,
    "SOT-20": 2.5, "SOT-24": 2.5, "SOT-28": 2.5,
}

#: real lead width ("b" on a datasheet), mm -- 3D export only for the
#: gull-wing sub-family. The 2D land pad is deliberately wider (see
#: PACKAGES's own pad=) for a real solderable copper pad, but the
#: actual metal lead itself -- what the 3D model should show -- is
#: this much narrower, matching the same 8-pin SOIC reference drawing.
LEAD_WIDTH_MM = 0.38

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

    # Everything below is computed relative-to-centre, then shifted by
    # (ox, oy) into bottom-left-origin space -- see the module
    # docstring on why the cell's own origin is bottom-left.
    ox, oy = body_l / 2.0, body_w / 2.0

    per_row = (pins + 1) // 2          # row 1 (pins 1..per_row) is the left/lower row
    # Pads run along the y axis; the two rows are offset in x by row_gap.
    def _row_positions(count):
        span = (count - 1) * pitch
        return [(-span / 2.0) + k * pitch + oy for k in range(count)]

    left_x = -row_gap / 2.0 + ox
    right_x = row_gap / 2.0 + ox

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

    bl, bw = mm_to_dbu(body_l), mm_to_dbu(body_w)
    cell.add_path(OUTLINE_LAYER,
                  [(0, 0), (bl, 0), (bl, bw), (0, bw), (0, 0)],
                  width=mm_to_dbu(OUTLINE_WIDTH_MM), cap="flush")

    cell.add_text(LABEL_LAYER, (mm_to_dbu(ox), mm_to_dbu(oy)), "U", height=mm_to_dbu(LABEL_HEIGHT_MM))


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


def _spec_step_leads(spec, lead_w=None):
    """leads_neg_x/leads_pos_x for make_dip_x() from a PACKAGES entry
    -- same (relative-to-centre) positions build_package() itself
    computes for the left/right rows, just returned as plain data
    instead of drawn, so the 2D cell and the 3D model can never drift
    apart. `lead_w` overrides the ordinary (non-tab) lead's width --
    the 2D land pad is deliberately wider than the real metal lead
    (see LEAD_WIDTH_MM); defaults to the 2D pad's own width when not
    given. A tab_pin's own tab width is never overridden."""
    pins = spec["pins"]
    pitch = spec["pitch"]
    pad_w = lead_w if lead_w is not None else spec["pad"][1]
    tab_pin = spec.get("tab_pin")
    tab = spec.get("tab")
    per_row = (pins + 1) // 2

    def _positions(count):
        span = (count - 1) * pitch
        return [(-span / 2.0) + k * pitch for k in range(count)]

    def _leads(ys, first_pin):
        out = []
        for i, y in enumerate(ys):
            n = first_pin + i
            w = tab[1] if tab_pin == n and tab else pad_w
            out.append((round(y, 4), w))
        return out

    neg = _leads(_positions(per_row), 1)
    pos = list(reversed(_leads(_positions(pins - per_row), per_row + 1)))
    return neg, pos


def step_parts():
    """(name, "make_dip_x", kwargs) for every PACKAGES entry -- see
    hlayout.io.example_step3d.make_dip_x() (the rows-along-X twin of
    make_dip(), matching this script's own axis convention directly,
    no swapping needed). Bottom-left origin, matching build()'s own
    cells, so a STEP file here lines up with its GDS cell.

    KNOWN GAP: reach (below) is computed from the spec's ordinary pad
    size only -- a tab_pin's own bigger tab size isn't factored in, so
    SOT-89/143/223's 3D tab lead falls a little short of where the 2D
    tab pad's own outer edge actually sits (confirmed ~0.3 mm on
    SOT-89 -- _gullwing_side_x() only takes one lead_out for a whole
    side, not a per-lead override, so fixing this needs a real API
    change, not just a number tweak)."""
    parts = []
    for name, spec in PACKAGES.items():
        body_l, body_w = spec["body"]
        row_gap, pad_l = spec["row_gap"], spec["pad"][0]
        # gull-wing ICs (SOT-8 and up) get their real, narrower metal
        # lead width in 3D; the transistor-family entries keep using
        # their pad width as before.
        lead_w = LEAD_WIDTH_MM if spec["pins"] >= 8 else None
        leads_neg_x, leads_pos_x = _spec_step_leads(spec, lead_w)
        # Reach the leads/feet need to cover, from the 3D body's own
        # wall (X=0/X=body_l) out to roughly where the 2D pad's own
        # outer edge sits -- see the module docstring's note that a
        # pad can extend a little past the outline. lead_t itself also
        # adds to the total reach (the "drop" segment has width
        # lead_t), so it's subtracted back out.
        lead_t = 0.15
        reach = max((row_gap / 2.0 + pad_l / 2.0) - body_l / 2.0, 0.05)
        lead_out = max(reach - lead_t, 0.02)
        parts.append((name, "make_dip_x", dict(
            L=body_l, W=body_w, H=HEIGHT_MM[name],
            leads_neg_x=leads_neg_x, leads_pos_x=leads_pos_x,
            lead_t=lead_t, lead_out=lead_out, lead_foot=0.05,
        )))
    return parts


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default=os.path.join(_here, "sot_packages.gds"),
                    help="output .gds path (default: examples/sot_packages.gds)")
    ap.add_argument("--step-dir", default=None,
                    help="also write a FreeCAD macro building a matching 3D STEP model "
                         "for every package into this folder")
    ap.add_argument("--run-freecad", action="store_true",
                    help="with --step-dir, also run the macro with freecadcmd to produce "
                         "the .step files now (requires FreeCAD installed)")
    args = ap.parse_args(argv)

    lib = build()
    save_gds(lib, args.out)

    print(f"{len(PACKAGES)} SOT package cells + INDEX -> {args.out}")
    for name, spec in PACKAGES.items():
        print(f"  {name:9s} {spec['pins']} pins  body {spec['body'][0]} x {spec['body'][1]} mm")

    if args.step_dir:
        macro_path = write_step_macro(step_parts(), args.step_dir)
        print(f"wrote 3D STEP macro -> {macro_path}")
        if args.run_freecad:
            print(run_step_macro(macro_path))


if __name__ == "__main__":
    main()
