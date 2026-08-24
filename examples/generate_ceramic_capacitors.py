"""Generate a small library of standard EIA-size ceramic (MLCC) chip
capacitor footprint cells and save it as a named GDSII library, ready
to be attached to any design with File > Attach Library (or
``s.attach_library(path)``) and placed with Find & Place Cell (or
``s.place_cell(name, origin)``).

    python examples/generate_ceramic_capacitors.py
    # writes examples/ceramic_capacitors.gds, library name "CERAMIC-CAPS"

Each package is built as one cell containing:
  - two rectangular conductor (termination) pads on GDS layer 1,
    named "L1" -- this project's usual first conductor layer (see
    e.g. the "VIA_Layer1_to_Layer3" via cell used elsewhere)
  - a body/courtyard outline on the "Outline" layer, sized to the
    part's EIA body dimensions
  - a reference-designator text label on the "Reference" layer
plus one "INDEX" cell that places every package in a row, purely so
opening the file gives an at-a-glance picture of the whole library.

Body sizes (EIA case code -> length x width in mm) are the standard,
widely published nominal chip-capacitor body dimensions and are not
in dispute between manufacturers. Pad geometry, however, IS
manufacturer- and process-specific (solder process, reflow profile,
board finish, ...) -- rather than guess at a single vendor's
datasheet numbers and present them as authoritative, this script
derives pad length/width from each body size with one simple,
clearly stated formula (see _pad_geometry_for_body below). Treat the
result as a reasonable, size-proportional starting point, and verify
against the actual part's datasheet / your board house's process
capability before using it for fabrication.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.units import mm_to_dbu as mm
from hlayout.scripting import Session

LAYER_PADS = 1  # GDS layer 1 -- this project's usual first conductor layer, "L1"
LAYER_OUTLINE = 2
LAYER_REFERENCE = 3

# EIA case code -> body (length_mm, width_mm). Length is the dimension
# along the two-terminal axis (X, the axis the pads sit on); width is
# across it (Y). These are the standard nominal EIA/metric chip body
# sizes published identically across MLCC manufacturers' datasheets.
EIA_BODIES_MM = {
    "01005": (0.40, 0.20),
    "0201":  (0.60, 0.30),
    "0402":  (1.00, 0.50),
    "0603":  (1.60, 0.80),
    "0805":  (2.00, 1.25),
    "1206":  (3.20, 1.60),
    "1210":  (3.20, 2.50),
    "1808":  (4.50, 2.00),
    "1812":  (4.50, 3.20),
    "1825":  (4.50, 6.40),
    "2220":  (5.70, 5.00),
    "2225":  (5.70, 6.40),
    "2512":  (6.40, 3.20),
}


def _pad_geometry_for_body(length_mm, width_mm):
    """Derive (pad_length_x, pad_width_y, pad_pitch) from a body size.

    Each pad covers the outer 40% of the body length plus a small toe
    (overhang past the body end, capped at 0.3 mm) to catch a solder
    fillet; the middle 20% of the body length is left as a gap
    between the two pads. Pad width matches the body width. This
    scales sensibly across the whole EIA size range without hard-
    coding a table of per-size numbers pulled from any one vendor.
    """
    toe = min(0.3, 0.15 * length_mm)
    pad_length_x = 0.4 * length_mm + toe
    gap_center = 0.2 * length_mm
    pad_pitch = gap_center + pad_length_x
    pad_width_y = width_mm
    return pad_length_x, pad_width_y, pad_pitch


def _pad(s, cx_mm, cy_mm, w_mm, h_mm):
    x1, y1 = cx_mm - w_mm / 2, cy_mm - h_mm / 2
    x2, y2 = cx_mm + w_mm / 2, cy_mm + h_mm / 2
    s.rect(LAYER_PADS, mm(x1), mm(y1), mm(x2), mm(y2))


def _outline(s, w_mm, h_mm):
    s.rect(LAYER_OUTLINE, mm(-w_mm / 2), mm(-h_mm / 2), mm(w_mm / 2), mm(h_mm / 2))


def _label(s, name, y_mm):
    s.text(LAYER_REFERENCE, (0, mm(y_mm)), name, height=mm(0.4))


def build_capacitor(s, eia_code, body_length_mm, body_width_mm):
    """One EIA-size ceramic chip capacitor: two pads on L1, straddling
    the body with a small gap in the middle, plus outline and label."""
    name = f"CAP-{eia_code}"
    s.new_cell(name)
    pad_l, pad_w, pitch = _pad_geometry_for_body(body_length_mm, body_width_mm)
    _pad(s, -pitch / 2, 0.0, pad_l, pad_w)
    _pad(s, pitch / 2, 0.0, pad_l, pad_w)
    _outline(s, body_length_mm, body_width_mm)
    _label(s, name, body_width_mm / 2 + 0.6)
    return name


def main():
    s = Session.new("CERAMIC-CAPS")
    s.set_layer_style(LAYER_PADS, name="L1", color="#f5c518")
    s.set_layer_style(LAYER_OUTLINE, name="Outline", color="#3cb44b", fill=False)
    s.set_layer_style(LAYER_REFERENCE, name="Reference", color="#ffffff")

    names = []
    for eia_code, (length_mm, width_mm) in EIA_BODIES_MM.items():
        names.append(build_capacitor(s, eia_code, length_mm, width_mm))

    # An INDEX cell laying every package out in a row, spaced well
    # clear of each other (by the largest body's footprint), purely
    # so opening the file shows the whole library at a glance.
    s.new_cell("INDEX")
    spacing_mm = 10.0
    for i, name in enumerate(names):
        s.sref(name, (mm(i * spacing_mm), 0))

    out_path = os.path.join(os.path.dirname(__file__), "ceramic_capacitors.gds")
    s.save(out_path, library_name="CERAMIC-CAPS")
    print(f"Saved {out_path}  (library name: {s.library.name!r})")
    print(f"Packages: {', '.join(names)}")
    print(f"INDEX bbox (DBU): {s.bbox('INDEX')}")
    print()
    print("Attach it to a design with:")
    print(f"    s.attach_library({out_path!r})")
    print("or from the command bar:")
    print(f"    ATTACH {out_path}")


if __name__ == "__main__":
    main()
