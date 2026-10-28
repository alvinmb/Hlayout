"""Kovar dual-in-line hybrid packages (20, 30 and 40 pins) as STEP models.

A machined Kovar tub -- floor and four 1 mm walls, every inside and
outside vertical wall corner chamfered 0.6 mm -- with two rows of Kovar
pins on 0.1 in (2.54 mm) pitch, 0.9 in (22.86 mm) row to row. Each pin
passes through the floor in its own glass-to-metal seal, stands 2 mm up
into the cavity above the floor and runs 5 mm out below the package.

Pin 1 bottom left (−X, −Y), numbered counter-clockwise from above (the
DIP convention), so pin n/2 is bottom right and pin n top left. The
origin is the BOTTOM-LEFT corner of the body (body spanning (0, 0) to
(L, W)) -- the same origin as the 2D cells from
examples/generate_kovar_dip_packages.py -- and Z = 0 is the top of the
package floor, where the substrate sits (the underside is at Z = -1 mm),
like the chip_wire header models: give the part's component model
z_ref = substrate_bottom, or just let HLayout's FreeCAD export place it
(a package enclosing the substrate is referenced to its underside
automatically). Each STEP is an assembly of three coloured parts: body (Kovar),
seals (glass) and pins.

Run:  python build_STEP_parts/build_kovar_dip.py [out_dir]     (needs cadquery)
      -- writes to examples/step_models/kovar_dip by default.
"""
import os
import sys

import cadquery as cq

INCH = 25.4
PITCH = 0.1 * INCH          # pin to pin
ROW = 0.9 * INCH            # row to row
WALL = 1.0                  # wall thickness
FLOOR = 1.0                 # floor thickness
HEIGHT = 5.08               # overall body height (0.2 in), underside to wall top
CHAMFER = 0.6               # inside and outside vertical wall corners
INNER_W = 26.0              # cavity width (Y): 1.57 mm from each pin row to the wall
END_MARGIN = 2.27           # cavity end wall to the end pin centre (X)
PIN_D = 0.46                # Kovar pin (0.018 in)
SEAL_D = 1.60               # glass bead through the floor
MENISCUS_H = 0.25           # glass meniscus climbing the pin inside the cavity
PIN_IN = 2.0                # pin above the floor, inside the package
PIN_OUT = 5.0               # pin below the package

KOVAR = cq.Color(0.62, 0.63, 0.66)
GLASS = cq.Color(0.55, 0.80, 0.78, 0.6)
PIN = cq.Color(0.83, 0.69, 0.22)          # gold-plated


def pin_sites(n):
    """[(pin, x, y)] -- pin 1 bottom left, counter-clockwise from above."""
    per = n // 2
    x0 = -(per - 1) * PITCH / 2.0
    out = []
    for k in range(per):                       # bottom row, left to right
        out.append((k + 1, x0 + k * PITCH, -ROW / 2.0))
    for k in range(per):                       # top row, right to left
        out.append((per + k + 1, x0 + (per - 1 - k) * PITCH, ROW / 2.0))
    return out


def body_size(n):
    per = n // 2
    inner_l = (per - 1) * PITCH + 2 * END_MARGIN
    return inner_l + 2 * WALL, INNER_W + 2 * WALL, inner_l, INNER_W


def make_body(n):
    L, W, il, iw = body_size(n)
    outer = (cq.Workplane("XY").rect(L, W).extrude(HEIGHT)
             .edges("|Z").chamfer(CHAMFER))
    cavity = (cq.Workplane("XY").workplane(offset=FLOOR).rect(il, iw)
              .extrude(HEIGHT)                  # through the top
              .edges("|Z").chamfer(CHAMFER))
    body = outer.cut(cavity)
    holes = cq.Workplane("XY").pushPoints([(x, y) for _p, x, y in pin_sites(n)]) \
        .circle(SEAL_D / 2.0).extrude(FLOOR)
    return body.cut(holes)


def make_seals(n):
    pts = [(x, y) for _p, x, y in pin_sites(n)]
    bead = cq.Workplane("XY").pushPoints(pts).circle(SEAL_D / 2.0) \
        .circle(PIN_D / 2.0).extrude(FLOOR)
    # a shallow glass meniscus wetting up the pin from the floor
    men = None
    for x, y in pts:
        m = (cq.Workplane("XY").workplane(offset=FLOOR).center(x, y)
             .circle(SEAL_D / 2.0).workplane(offset=MENISCUS_H)
             .circle(PIN_D / 2.0 + 0.05).loft()
             .cut(cq.Workplane("XY").workplane(offset=FLOOR).center(x, y)
                  .circle(PIN_D / 2.0).extrude(MENISCUS_H)))
        men = m if men is None else men.union(m)
    return bead.union(men)


def make_pins(n):
    pts = [(x, y) for _p, x, y in pin_sites(n)]
    return (cq.Workplane("XY").workplane(offset=-PIN_OUT).pushPoints(pts)
            .circle(PIN_D / 2.0).extrude(PIN_OUT + FLOOR + PIN_IN))


def build(n, out_dir):
    L, W, _il, _iw = body_size(n)
    shift = (L / 2.0, W / 2.0, -FLOOR)          # body bottom-left to the origin,
    #                                             floor top to Z = 0
    a = cq.Assembly(name=f"KOVAR_DIP{n}")
    a.add(make_body(n).translate(shift), name="body", color=KOVAR)
    a.add(make_seals(n).translate(shift), name="seals", color=GLASS)
    a.add(make_pins(n).translate(shift), name="pins", color=PIN)
    path = os.path.join(out_dir, f"KOVAR_DIP{n}_0.9in.step")
    a.export(path)
    return path


if __name__ == "__main__":
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(root, "examples", "step_models",
                                                             "kovar_dip")
    os.makedirs(out, exist_ok=True)
    for n in (20, 30, 40):
        L, W, _il, _iw = body_size(n)
        print(f"{build(n, out)}   body {L:.2f} x {W:.2f} x {HEIGHT:.2f} mm")
