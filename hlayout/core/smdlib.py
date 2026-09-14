"""Generate a library of standard SMD component footprint cells.

Every cell gets:
  * a body-outline Path on layer 60 (BODY_OUTLINE_LAYER), traced along the
    package's maximum/worst-case rectangular body envelope, at a path
    width of 0.1 mm (100 DBU) -- per explicit user spec.
  * a reference-designator placeholder Text on layer 61 (REFDES_LAYER),
    using the standard prefix letter(s) for that component family
    (C=capacitor, R=resistor, T=transistor, D=diode, IC=integrated
    circuit), centred on the body.
  * leg/lead geometry, so every component shows how it actually contacts
    the board, not just a bare outline:
      - chip R/C: two filled terminal/end-cap Rectangles on layer 62
        (TERMINAL_LAYER) -- the wraparound metallized terminations a real
        chip resistor/capacitor has at each end. See _add_chip_terminals().
      - SOT-23 / SOD-123 / SOIC: filled gull-wing lead Rectangles on layer
        63 (LEAD_LAYER), one per pin, projecting outward from the body
        edge. See _add_gullwing_leads() and _SOT23_PINS/_SOD123_PINS.

Every cell's own origin (0, 0) is the BOTTOM-LEFT corner of its body
outline -- the body spans (0, 0) to (length_mm, width_mm) -- matching
hlayout.io.component_library's 3D STEP models exactly, so a 2D cell and
a 3D model sharing the same name substitute correctly in File > Export
to FreeCAD instead of landing offset by the body size.

Package set covered:
  * Standard EIA chip resistor/capacitor body sizes (0201..2512) -- both
    R_<size> and C_<size> cells are generated, since a given EIA chip
    size is a single physical body shared by resistors and capacitors.
  * T_SOT23   -- SOT-23 (3-terminal transistor/small-signal package),
    JEDEC TO-236: 2 leads on one long side (0.95 mm pitch), 1 centred
    lead on the opposite side.
  * D_SOD123  -- SOD-123 (2-terminal diode package): 1 gull-wing lead
    at each end.
  * IC_SOIC14, IC_SOIC16 -- narrow-body (3.9 mm) SOIC, JEDEC MS-012,
    N/2 gull-wing leads per long side at 1.27 mm pitch.
  * IC_SOIC24 -- wide-body (7.5 mm) SOIC, JEDEC MS-013, same pitch.

IMPORTANT: every dimension below -- body envelopes AND lead pitch/width/
extension -- is a generic, commonly-published JEDEC/EIA/TO-236 *nominal*
value, not pulled from any one manufacturer's datasheet. They're a
reasonable starting point for a component library but should be checked
against the real datasheet for any part before this geometry is used for
fabrication or DRC.
"""
from .library import Library
from .units import mm_to_dbu

BODY_OUTLINE_LAYER = 60
REFDES_LAYER = 61
TERMINAL_LAYER = 62
LEAD_LAYER = 63
BODY_OUTLINE_WIDTH_MM = 0.1  # path width for the body-outline trace

# Chip terminal (end-cap) length as a fraction of the body length, capped
# at an absolute maximum -- a generic representative proportion (typical
# real terminals run roughly a quarter to a third of body length on small
# chips), not per-size datasheet values.
TERMINAL_LENGTH_FRACTION = 0.25
TERMINAL_LENGTH_MAX_MM = 0.6

# Gull-wing lead nominal dimensions, generic per package family (not per
# manufacturer). "pitch" is pin-to-pin spacing; "width" is the lead's
# footprint along the pitch direction; "extension" is how far the lead
# projects outward past the body edge.
SOT23_PIN_PITCH_MM = 0.95     # TO-236 pitch between the 2 leads on one side
SOT23_LEAD_WIDTH_MM = 0.4
SOT23_LEAD_EXTENSION_MM = 0.4

SOD123_LEAD_WIDTH_MM = 1.0
SOD123_LEAD_EXTENSION_MM = 0.4

SOIC_PIN_PITCH_MM = 1.27      # JEDEC MS-012/MS-013 standard pitch
SOIC_LEAD_WIDTH_MM = 0.4
# Chosen so body_width + 2*extension lands on the commonly-published
# overall lead-span: ~6.0 mm for narrow-body (3.9 mm) SOIC, ~10.3 mm for
# wide-body (7.5 mm) SOIC.
SOIC_LEAD_EXTENSION_NARROW_MM = 1.05
SOIC_LEAD_EXTENSION_WIDE_MM = 1.4

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

# Discrete/IC packages -> (prefix, length_mm, width_mm, kind, pins).
# "kind" selects which lead layout _add_leads() draws; "pins" is only
# used for the SOIC kind (leads-per-side = pins // 2).
OTHER_PACKAGES_MM = {
    "T_SOT23": ("T", 2.90, 1.30, "sot23", 3),
    "D_SOD123": ("D", 3.70, 1.70, "sod123", 2),
    "IC_SOIC14": ("IC", 8.65, 3.90, "soic", 14),
    "IC_SOIC16": ("IC", 9.90, 3.90, "soic", 16),
    "IC_SOIC24": ("IC", 15.40, 7.50, "soic", 24),
}


def _add_body_and_refdes(cell, prefix, length_mm, width_mm):
    """Draw a rectangular body-outline Path (layer 60) and a
    refdes-prefix Text (layer 61) into `cell`. Origin at the
    bottom-left corner: body spans (0, 0) to (length_mm, width_mm)."""
    L = mm_to_dbu(length_mm)
    W = mm_to_dbu(width_mm)
    points = [(0, 0), (L, 0), (L, W), (0, W), (0, 0)]
    width = mm_to_dbu(BODY_OUTLINE_WIDTH_MM)
    cell.add_path(BODY_OUTLINE_LAYER, points, width, cap="flush")

    text_height = max(mm_to_dbu(0.3), min(mm_to_dbu(width_mm * 0.4), mm_to_dbu(1.0)))
    cell.add_text(REFDES_LAYER, (L // 2, W // 2), prefix, height=int(text_height))


def _add_chip_terminals(cell, length_mm, width_mm):
    """Draw the two wraparound end-cap terminals (layer 62) a chip R/C
    has at each end of its body, full body width, extending inward from
    each end by TERMINAL_LENGTH_FRACTION of the body length (capped at
    TERMINAL_LENGTH_MAX_MM). Body spans (0, 0) to (length_mm, width_mm)."""
    term_len_mm = min(length_mm * TERMINAL_LENGTH_FRACTION, TERMINAL_LENGTH_MAX_MM)
    L = mm_to_dbu(length_mm)
    W = mm_to_dbu(width_mm)
    term_len = mm_to_dbu(term_len_mm)

    cell.add_rectangle(TERMINAL_LAYER, 0, 0, term_len, W)
    cell.add_rectangle(TERMINAL_LAYER, L - term_len, 0, L, W)


def _side_lead_positions(n, pitch_mm):
    """x-offsets (mm) for `n` leads centred on 0 at `pitch_mm` spacing --
    relative to the body's own centreline, added to half the body length
    by the caller to land in the cell's bottom-left-origin frame."""
    return [(k - (n - 1) / 2) * pitch_mm for k in range(n)]


def _add_side_leads(cell, xs_mm, y_edge, y_dir, extension, lead_width):
    """Add gull-wing lead Rectangles projecting outward from a body edge
    at `y_edge` (DBU) -- in the +y direction if y_dir > 0, else -y --
    one per absolute x-position in `xs_mm` (mm)."""
    half_w = mm_to_dbu(lead_width) / 2
    ext = mm_to_dbu(extension)
    for x_mm in xs_mm:
        x = mm_to_dbu(x_mm)
        if y_dir > 0:
            cell.add_rectangle(LEAD_LAYER, x - half_w, y_edge, x + half_w, y_edge + ext)
        else:
            cell.add_rectangle(LEAD_LAYER, x - half_w, y_edge - ext, x + half_w, y_edge)


def _add_end_leads(cell, ys_mm, x_edge, x_dir, extension, lead_width):
    """Add gull-wing lead Rectangles projecting outward from a body end
    at `x_edge` (DBU) -- in the +x direction if x_dir > 0, else -x --
    one per absolute y-position in `ys_mm` (mm)."""
    half_w = mm_to_dbu(lead_width) / 2
    ext = mm_to_dbu(extension)
    for y_mm in ys_mm:
        y = mm_to_dbu(y_mm)
        if x_dir > 0:
            cell.add_rectangle(LEAD_LAYER, x_edge, y - half_w, x_edge + ext, y + half_w)
        else:
            cell.add_rectangle(LEAD_LAYER, x_edge - ext, y - half_w, x_edge, y + half_w)


def _add_leads(cell, kind, length_mm, width_mm, pins):
    L = mm_to_dbu(length_mm)
    W = mm_to_dbu(width_mm)
    cx_mm = length_mm / 2.0
    cy_mm = width_mm / 2.0

    if kind == "sot23":
        # 2 leads on one long side (pitch SOT23_PIN_PITCH_MM), 1 lead
        # centred on the opposite side -- standard TO-236 pinout.
        xs = [cx_mm + dx for dx in _side_lead_positions(2, SOT23_PIN_PITCH_MM)]
        _add_side_leads(cell, xs, 0, -1, SOT23_LEAD_EXTENSION_MM, SOT23_LEAD_WIDTH_MM)
        _add_side_leads(cell, [cx_mm], W, 1, SOT23_LEAD_EXTENSION_MM, SOT23_LEAD_WIDTH_MM)

    elif kind == "sod123":
        # 1 gull-wing lead at each end.
        _add_end_leads(cell, [cy_mm], L, 1, SOD123_LEAD_EXTENSION_MM, SOD123_LEAD_WIDTH_MM)
        _add_end_leads(cell, [cy_mm], 0, -1, SOD123_LEAD_EXTENSION_MM, SOD123_LEAD_WIDTH_MM)

    elif kind == "soic":
        # N/2 leads per long side, 1.27 mm pitch, standard SOIC pinout.
        n_per_side = pins // 2
        xs = [cx_mm + dx for dx in _side_lead_positions(n_per_side, SOIC_PIN_PITCH_MM)]
        extension = SOIC_LEAD_EXTENSION_WIDE_MM if width_mm > 5.0 else SOIC_LEAD_EXTENSION_NARROW_MM
        _add_side_leads(cell, xs, 0, -1, extension, SOIC_LEAD_WIDTH_MM)
        _add_side_leads(cell, xs, W, 1, extension, SOIC_LEAD_WIDTH_MM)


def generate_smd_library(name="SMD_LIBRARY") -> Library:
    """Build and return a Library of standard SMD component cells.

    Body outlines: Path, layer 60, width 0.1 mm.
    Ref-des placeholders: Text, layer 61, prefix only (C/R/T/D/IC).
    Chip R/C terminals (end-caps): Rectangle, layer 62.
    SOT-23/SOD-123/SOIC gull-wing leads: Rectangle, layer 63.
    """
    lib = Library(name)

    for size, (length_mm, width_mm) in CHIP_SIZES_MM.items():
        r_cell = lib.new_cell(f"R_{size}")
        _add_body_and_refdes(r_cell, "R", length_mm, width_mm)
        _add_chip_terminals(r_cell, length_mm, width_mm)

        c_cell = lib.new_cell(f"C_{size}")
        _add_body_and_refdes(c_cell, "C", length_mm, width_mm)
        _add_chip_terminals(c_cell, length_mm, width_mm)

    for cell_name, (prefix, length_mm, width_mm, kind, pins) in OTHER_PACKAGES_MM.items():
        cell = lib.new_cell(cell_name)
        _add_body_and_refdes(cell, prefix, length_mm, width_mm)
        _add_leads(cell, kind, length_mm, width_mm, pins)

    return lib
