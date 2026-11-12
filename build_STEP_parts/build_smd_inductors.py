# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""SMD inductors as STEP models, in three families:

  * multilayer / wire-wound chip inductors, EIA 0201 to 1812
    (L_<EIA>_<metric>): a ferrite body with a tinned termination
    wrapping each end;
  * shielded moulded power inductors (L_SHLD_<size>, e.g. L_SHLD_5030 =
    5.0 x 5.0 x 3.0 mm): a square moulded body with two tinned terminals
    under it, bent up the two end faces;
  * unshielded drum-core power inductors (L_DRUM_<size>, the CD54 /
    CD75 / CD105 style): a square ferrite base, a copper winding round the
    core and a round ferrite top flange, with a tinned terminal on each
    end of the base.

Every model's origin is the BOTTOM-LEFT corner of its footprint (the
body spanning (0, 0) to (L, W), long axis -- the terminals -- along X),
with Z = 0 the seating plane on the substrate, like the other chip-part
models (ceramic_chip_caps, tantalum). Each STEP is an assembly of
coloured parts: body (ferrite), terminations (tin) and, for the drum
cores, the winding (copper). Sizes are typical catalogue outlines, not
any one maker's drawing.

Run:  python build_STEP_parts/build_smd_inductors.py [out_dir]     (needs cadquery)
      -- writes to examples/step_models/smd_inductors by default;
      build_STEP_parts/build_all.py runs it with the other model scripts.
"""
import os
import sys

# cadquery is imported only to build the solids, so the dimension tables
# and footprint() below can be read without it -- the 2D cells
# (examples/generate_smd_inductors.py) come from the same numbers
cq = None

FERRITE = (0.22, 0.22, 0.24)
MOULD = (0.16, 0.16, 0.17)
TIN = (0.80, 0.80, 0.82)
COPPER = (0.78, 0.45, 0.20)

#: shielded: terminal width / foot length as fractions of W / L
SHLD_TERM_W, SHLD_FOOT_L = 0.60, 0.22
#: drum core: terminal width / length as fractions of W / L
DRUM_TERM_W, DRUM_TERM_L = 0.45, 0.18

#: chip inductors: EIA code -> (metric code, L, W, H, termination length), mm
CHIP = {
    "0201": ("0603Metric", 0.60, 0.30, 0.30, 0.15),
    "0402": ("1005Metric", 1.00, 0.50, 0.50, 0.25),
    "0603": ("1608Metric", 1.60, 0.80, 0.80, 0.30),
    "0805": ("2012Metric", 2.00, 1.25, 0.90, 0.40),
    "1008": ("2520Metric", 2.50, 2.00, 1.60, 0.50),
    "1206": ("3216Metric", 3.20, 1.60, 1.10, 0.50),
    "1210": ("3225Metric", 3.20, 2.50, 1.60, 0.50),
    "1812": ("4532Metric", 4.50, 3.20, 2.20, 0.60),
}

#: shielded moulded power inductors: name -> (L, W, H), mm
SHIELDED = {
    "2016": (2.0, 1.6, 1.0),
    "2520": (2.5, 2.0, 1.2),
    "3015": (3.0, 3.0, 1.5),
    "4020": (4.0, 4.0, 2.0),
    "4030": (4.0, 4.0, 3.0),
    "5030": (5.0, 5.0, 3.0),
    "6030": (6.0, 6.0, 3.0),
    "7040": (7.0, 7.0, 4.0),
    "1040": (10.0, 10.0, 4.0),
    "1265": (12.5, 12.5, 6.5),
}

#: unshielded drum cores: name -> (L, W, H), mm (base footprint, height)
DRUM = {
    "CD32": (3.2, 2.5, 2.0),
    "CD43": (4.5, 4.0, 3.2),
    "CD54": (5.8, 5.2, 4.5),
    "CD75": (7.8, 7.0, 5.0),
    "CD105": (10.0, 9.0, 5.4),
}


def _cq():
    global cq
    if cq is None:
        import cadquery
        cq = cadquery
    return cq


def footprint(name):
    """(L, W, H, [terminal rects]) in mm for a model name -- each terminal
    (x0, y0, x1, y1) is where the part's metal meets the substrate, pin
    "1" first (the -X end)."""
    fam, _, size = name[2:].partition("_")
    if fam == "SHLD":
        L, W, H = SHIELDED[size]
        tw, fl = W * SHLD_TERM_W, L * SHLD_FOOT_L
        y0 = (W - tw) / 2.0
        return L, W, H, [(0.0, y0, fl, y0 + tw), (L - fl, y0, L, y0 + tw)]
    if fam == "DRUM":
        L, W, H = DRUM[size]
        tw, tl = W * DRUM_TERM_W, L * DRUM_TERM_L
        y0 = (W - tw) / 2.0
        return L, W, H, [(0.0, y0, tl, y0 + tw), (L - tl, y0, L, y0 + tw)]
    _metric, L, W, H, t = CHIP[fam]
    return L, W, H, [(0.0, 0.0, t, W), (L - t, 0.0, L, W)]


def names():
    """Every model in the series, chip / shielded / drum."""
    return ([f"L_{eia}_{m[0]}" for eia, m in CHIP.items()]
            + [f"L_SHLD_{s}" for s in SHIELDED] + [f"L_DRUM_{s}" for s in DRUM])


def _box(x0, y0, z0, x1, y1, z1):
    cq = _cq()
    return (cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0, centered=False)
            .translate((x0, y0, z0)))


def chip(L, W, H, t):
    """Body and wrap-round end terminations (0.02 mm proud of the body)."""
    cq = _cq()
    o = 0.02
    body = _box(t, 0, 0, L - t, W, H)
    term = _box(0, -o, 0, t, W + o, H + o).union(_box(L - t, -o, 0, L, W + o, H + o))
    return [("body", body, FERRITE), ("terminations", term, TIN)]


def shielded(L, W, H):
    """A moulded square body (edges softened) with two terminals under it,
    each bent up its end face to a third of the height."""
    cq = _cq()
    th = max(0.08, min(0.15, H * 0.05))             # terminal strip thickness
    tw = W * SHLD_TERM_W                            # terminal width
    fl = L * SHLD_FOOT_L                            # foot length under the body
    up = H * 0.35                                   # how far it climbs the end
    y0 = (W - tw) / 2.0
    body = _box(th, 0, th, L - th, W, H)
    r = min(0.3, H * 0.1, W * 0.08)
    try:
        body = body.edges("|Z").fillet(r)
    except Exception:
        pass
    term = None
    for x_foot, x_up in ((0.0, 0.0), (L - fl, L - th)):
        foot = _box(x_foot, y0, 0, x_foot + fl, y0 + tw, th)
        riser = _box(x_up, y0, 0, x_up + th, y0 + tw, up)
        t = foot.union(riser)
        term = t if term is None else term.union(t)
    return [("body", body, MOULD), ("terminations", term, TIN)]


def drum(L, W, H):
    """A drum core: square base plate, core column, copper winding, round
    top flange, and a tinned terminal on each end of the base."""
    cq = _cq()
    base_h = max(0.4, H * 0.12)
    top_h = max(0.4, H * 0.12)
    d_top = min(L, W) * 0.95
    d_wind = min(L, W) * 0.85
    d_core = min(L, W) * 0.45
    cx, cy = L / 2.0, W / 2.0
    th = 0.12
    tw = W * DRUM_TERM_W
    base = _box(th, 0, 0, L - th, W, base_h)
    core = (cq.Workplane("XY").workplane(offset=base_h).center(cx, cy)
            .circle(d_core / 2.0).extrude(H - base_h - top_h))
    top = (cq.Workplane("XY").workplane(offset=H - top_h).center(cx, cy)
           .circle(d_top / 2.0).extrude(top_h))
    ferrite = base.union(core).union(top)
    winding = (cq.Workplane("XY").workplane(offset=base_h + 0.05).center(cx, cy)
               .circle(d_wind / 2.0).circle(d_core / 2.0)
               .extrude(H - base_h - top_h - 0.1))
    y0 = (W - tw) / 2.0
    tl = L * DRUM_TERM_L
    term = _box(0, y0, 0, tl, y0 + tw, base_h + 0.05).union(
        _box(L - tl, y0, 0, L, y0 + tw, base_h + 0.05))
    return [("body", ferrite, FERRITE), ("winding", winding, COPPER),
            ("terminations", term, TIN)]


def models():
    """[(name, parts)] for every model in the series."""
    out = []
    for eia, (metric, L, W, H, t) in CHIP.items():
        out.append((f"L_{eia}_{metric}", chip(L, W, H, t)))
    for size, (L, W, H) in SHIELDED.items():
        out.append((f"L_SHLD_{size}", shielded(L, W, H)))
    for size, (L, W, H) in DRUM.items():
        out.append((f"L_DRUM_{size}", drum(L, W, H)))
    return out


def build(out_dir):
    cq = _cq()
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for name, parts in models():
        a = cq.Assembly(name=name)
        for part, shape, colour in parts:
            a.add(shape, name=part, color=cq.Color(*colour))
        path = os.path.join(out_dir, name + ".step")
        a.export(path)
        made.append(path)
    return made


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, "examples", "step_models",
                                                             "smd_inductors")
    for p in build(out):
        print(p)
