"""Generate a library of standard SMD component footprint cells.

Each cell gets:
  * a body-outline Path on layer 60 (BODY_OUTLINE_LAYER), traced along the
    package's maximum/worst-case rectangular body envelope, at a path
    width of 0.1 mm (100 DBU) -- per explicit user spec.
  * a reference-designator placeholder Text on layer 61 (REFDES_LAYER),
    using the standard prefix letter(s) for that component family
    (C=capacitor, R=resistor, T=transistor, D=diode, IC=integrated
    circuit), centred on the cell origin.

Package set covered:
  * Standard EIA chip resistor/capacitor body sizes (0201..2512) -- both
    R_<size> and C_<size> cells are generated, since a given EIA chip
    size is a single physical body shared by resistors and capacitors.
  * T_SOT23   -- SOT-23 (3-terminal transistor/small-signal package)
  * D_SOD123  -- SOD-123 (2-terminal diode package)
  * IC_SOIC14, IC_SOIC16 -- narrow-body (3.9 mm) SOIC, JEDEC MS-012
  * IC_SOIC24 -- wide-body (7.5 mm) SOIC, JEDEC MS-013

IMPORTANT: the body dimensions below are generic, commonly-published
JEDEC/EIA *nominal maximum* envelope values, not pulled from any one
manufacturer's datasheet. They are a reasonable starting point for a
component library but should be checked against the actual datasheet
for any part before this geometry is used for fabrication or DRC.
"""
from .library import Library
from .units import mm_to_dbu

BODY_OUTLINE_LAYER = 60
REFDES_LAYER = 61
BODY_OUTLINE_WIDTH_MM = 0.1  # path width for the body-outline trace

# EIA chip size -> (length_mm, width_mm), nominal maximum body envelope.
CHIP_SIZES_MM = {
    "0201": (0.60, 0.30),
    "0402": (1.00, 0.50),
    "0603": (1.60, 0.80),
    "0805": (2.00, 1.25),
    "1206": (3.20, 1.60),
    "1210": (3.20, 2.50),
    "1812": (4.50, 3.20),
    "2010": (5.00, 2.50),
    "2512": (6.40, 3.20),
}

# Discrete/IC packages -> (prefix, length_mm, width_mm, refdes_text).
OTHER_PACKAGES_MM = {
    "T_SOT23": ("T", 2.90, 1.30),
    "D_SOD123": ("D", 3.70, 1.70),
    "IC_SOIC14": ("IC", 8.65, 3.90),
    "IC_SOIC16": ("IC", 9.90, 3.90),
    "IC_SOIC24": ("IC", 15.40, 7.50),
}


def _add_body_and_refdes(cell, prefix, length_mm, width_mm):
    """Draw a centred rectangular body-outline Path (layer 60) and a
    refdes-prefix Text (layer 61) into `cell`."""
    half_l = mm_to_dbu(length_mm) / 2
    half_w = mm_to_dbu(width_mm) / 2
    x0, x1 = -half_l, half_l
    y0, y1 = -half_w, half_w
    points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    width = mm_to_dbu(BODY_OUTLINE_WIDTH_MM)
    cell.add_path(BODY_OUTLINE_LAYER, points, width, cap="flush")

    text_height = max(mm_to_dbu(0.3), min(mm_to_dbu(width_mm * 0.4), mm_to_dbu(1.0)))
    cell.add_text(REFDES_LAYER, (0, 0), prefix, height=int(text_height))


def generate_smd_library(name="SMD_LIBRARY") -> Library:
    """Build and return a Library of standard SMD component cells.

    Body outlines: Path, layer 60, width 0.1 mm.
    Ref-des placeholders: Text, layer 61, prefix only (C/R/T/D/IC).
    """
    lib = Library(name)

    for size, (length_mm, width_mm) in CHIP_SIZES_MM.items():
        r_cell = lib.new_cell(f"R_{size}")
        _add_body_and_refdes(r_cell, "R", length_mm, width_mm)

        c_cell = lib.new_cell(f"C_{size}")
        _add_body_and_refdes(c_cell, "C", length_mm, width_mm)

    for cell_name, (prefix, length_mm, width_mm) in OTHER_PACKAGES_MM.items():
        cell = lib.new_cell(cell_name)
        _add_body_and_refdes(cell, prefix, length_mm, width_mm)

    return lib
