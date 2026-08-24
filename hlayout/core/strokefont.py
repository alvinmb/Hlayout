"""A single-stroke ("stick"/plotter-style) vector font, used to convert
a Text shape into line-segment geometry for output formats that have no
native text primitive -- currently Gerber RS-274X (see
hlayout.io.gerberio).

User request (verbatim): "at preset we cat gerber text,,hwever most
systems will stroke text to lines" -- i.e. Gerber export used to skip
Text shapes outright (Gerber has no text primitive), but the expected
behaviour of a real Gerber-consuming system is to receive text as
stroked line geometry instead, the way silkscreen text is universally
handled in PCB/hybrid-microelectronics fabrication.

Each glyph is defined on a fixed-size grid (0 <= x <= GLYPH_WIDTH_UNITS,
0 <= y <= GLYPH_HEIGHT_UNITS, y=0 is the baseline) as one or more
polylines ("strokes") -- exactly what a pen plotter or a pen-on-paper
single-line font draws, no fill. This is deliberately simple geometry
(straight segments, closed loops approximated by a handful of points
for round letters) rather than a typographically faithful font: the
goal is legible reference-designator/label text on a fabrication
output, not print-quality type.

Coverage: uppercase A-Z, digits 0-9, space, and a small set of
punctuation common in labels (- _ . , / : + ( )). A lowercase letter is
folded to uppercase before lookup (silkscreen/plotter fonts are
conventionally caps-only) so ``ref_des`` labels like "r1" still render.
A character with no glyph (some other symbol or a non-ASCII character)
has no stroke data -- callers should count and report these rather than
silently dropping them; text_to_strokes() below returns that count.
"""

import math

# The grid every glyph is authored on. GLYPH_HEIGHT_UNITS maps directly
# to a Text shape's `height` (its cap height) when scaling: scale =
# height / GLYPH_HEIGHT_UNITS.
GLYPH_HEIGHT_UNITS = 7.0

# Default per-glyph advance width (grid units) used by any glyph that
# doesn't specify its own `advance` below -- most letters and digits.
_DEFAULT_ADVANCE = 5.0

# {char: (advance_units, [stroke, ...])}; stroke = [(x, y), ...] in grid
# units. `advance` defaults to _DEFAULT_ADVANCE when omitted (None).
_GLYPHS = {
    "0": (None, [[(1, 0), (3, 0), (4, 1.5), (4, 5.5), (3, 7), (1, 7),
                  (0, 5.5), (0, 1.5), (1, 0)]]),
    "1": (None, [[(1, 5), (2, 7), (2, 0)], [(1, 0), (3, 0)]]),
    "2": (None, [[(0, 5.5), (0, 6), (1, 7), (3, 7), (4, 6), (4, 5),
                  (0, 0), (4, 0)]]),
    "3": (None, [[(0, 6.5), (1, 7), (3, 7), (4, 6), (4, 4.5), (2, 4),
                  (4, 3.5), (4, 1), (3, 0), (1, 0), (0, 0.5)]]),
    "4": (None, [[(3, 0), (3, 7)], [(3, 7), (0, 2), (4, 2)]]),
    "5": (None, [[(4, 7), (0, 7), (0, 4), (2, 4), (3.5, 3.5), (4, 2),
                  (3.5, 0.5), (2, 0), (0, 0.5)]]),
    "6": (None, [[(3.5, 7), (1.5, 5.5), (0, 3), (0, 1.5), (1.2, 0),
                  (2.8, 0), (4, 1.3), (4, 2.8), (2.8, 4), (1.2, 4),
                  (0.3, 3.2)]]),
    "7": (None, [[(0, 7), (4, 7), (1, 0)]]),
    "8": (None, [[(1, 4), (0, 5), (0, 6), (1, 7), (3, 7), (4, 6),
                  (4, 5), (3, 4), (1, 4)],
                 [(1, 4), (0, 3), (0, 1), (1, 0), (3, 0), (4, 1),
                  (4, 3), (3, 4), (1, 4)]]),
    "9": (None, [[(2.8, 3), (4, 4.2), (4, 5.7), (2.8, 7), (1.2, 7),
                  (0, 5.7), (0, 4.2), (1.2, 3), (2.5, 3), (0.5, 0)]]),
    "A": (None, [[(0, 0), (2, 7), (4, 0)], [(1, 2.5), (3, 2.5)]]),
    "B": (None, [[(0, 0), (0, 7), (2.5, 7), (3.5, 6.2), (3.5, 4.3),
                  (2.5, 3.5), (0, 3.5)],
                 [(0, 3.5), (2.7, 3.5), (3.7, 2.7), (3.7, 1), (2.5, 0),
                  (0, 0)]]),
    "C": (None, [[(4, 5.5), (3, 7), (1, 7), (0, 5.5), (0, 1.5), (1, 0),
                  (3, 0), (4, 1.5)]]),
    "D": (None, [[(0, 0), (0, 7), (2.2, 7), (4, 5.3), (4, 1.7),
                  (2.2, 0), (0, 0)]]),
    "E": (None, [[(4, 7), (0, 7), (0, 0), (4, 0)], [(0, 3.5), (3, 3.5)]]),
    "F": (None, [[(0, 0), (0, 7), (4, 7)], [(0, 3.5), (3, 3.5)]]),
    "G": (None, [[(4, 5.5), (3, 7), (1, 7), (0, 5.5), (0, 1.5), (1, 0),
                  (3, 0), (4, 1.5), (4, 3.2), (2.3, 3.2)]]),
    "H": (None, [[(0, 0), (0, 7)], [(4, 0), (4, 7)], [(0, 3.5), (4, 3.5)]]),
    "I": (3.0, [[(1.5, 0), (1.5, 7)]]),
    "J": (None, [[(3, 7), (3, 1.5), (2, 0), (1, 0), (0, 1.5)]]),
    "K": (None, [[(0, 0), (0, 7)], [(4, 7), (0, 3.5), (4, 0)]]),
    "L": (None, [[(0, 7), (0, 0), (4, 0)]]),
    "M": (None, [[(0, 0), (0, 7), (2, 3.5), (4, 7), (4, 0)]]),
    "N": (None, [[(0, 0), (0, 7), (4, 0), (4, 7)]]),
    "O": (None, [[(1, 0), (3, 0), (4, 1.5), (4, 5.5), (3, 7), (1, 7),
                  (0, 5.5), (0, 1.5), (1, 0)]]),
    "P": (None, [[(0, 0), (0, 7), (3, 7), (4, 6), (4, 4.5), (3, 3.5),
                  (0, 3.5)]]),
    "Q": (None, [[(1, 0), (3, 0), (4, 1.5), (4, 5.5), (3, 7), (1, 7),
                  (0, 5.5), (0, 1.5), (1, 0)], [(2, 2), (4.3, -0.8)]]),
    "R": (None, [[(0, 0), (0, 7), (3, 7), (4, 6), (4, 4.5), (3, 3.5),
                  (0, 3.5)], [(1.8, 3.5), (4, 0)]]),
    "S": (None, [[(4, 6), (3, 7), (1, 7), (0, 6), (0, 4.7), (1, 4),
                  (3, 3.5), (4, 2.7), (4, 1), (3, 0), (1, 0), (0, 1)]]),
    "T": (None, [[(0, 7), (4, 7)], [(2, 7), (2, 0)]]),
    "U": (None, [[(0, 7), (0, 2), (1.3, 0), (2.7, 0), (4, 2), (4, 7)]]),
    "V": (None, [[(0, 7), (2, 0), (4, 7)]]),
    "W": (None, [[(0, 7), (1, 0), (2, 4), (3, 0), (4, 7)]]),
    "X": (None, [[(0, 0), (4, 7)], [(0, 7), (4, 0)]]),
    "Y": (None, [[(0, 7), (2, 3.5), (4, 7)], [(2, 3.5), (2, 0)]]),
    "Z": (None, [[(0, 7), (4, 7), (0, 0), (4, 0)]]),
    " ": (3.0, []),
    "-": (4.0, [[(0.5, 3.5), (3.5, 3.5)]]),
    "_": (4.0, [[(0, 0), (4, 0)]]),
    ".": (3.0, [[(1.7, 0), (2.1, 0.4)]]),
    ",": (3.0, [[(2, 0.4), (1.5, -1)]]),
    "/": (4.0, [[(0, 0), (4, 7)]]),
    ":": (3.0, [[(1.8, 4.4), (2.2, 4.8)], [(1.8, 1.6), (2.2, 2)]]),
    "+": (4.0, [[(0.5, 3.5), (3.5, 3.5)], [(2, 2), (2, 5)]]),
    "(": (4.0, [[(2.5, 7), (1, 5.5), (1, 1.5), (2.5, 0)]]),
    ")": (4.0, [[(1.5, 7), (3, 5.5), (3, 1.5), (1.5, 0)]]),
}


def glyph(ch):
    """Return (advance_units, strokes) for one character, or None if
    there's no glyph for it. Folds lowercase to uppercase first."""
    entry = _GLYPHS.get(ch) or _GLYPHS.get(ch.upper())
    if entry is None:
        return None
    advance, strokes = entry
    return (advance if advance is not None else _DEFAULT_ADVANCE, strokes)


def text_to_strokes(text, origin, height, rotation=0.0):
    """Convert a text string into world-space stroke polylines, as if
    drawn by a single-line plotter font: ``origin`` is the bottom-left
    (south-west) corner of the first character's baseline, ``height``
    is the cap height (same units as ``origin``, e.g. DBU), and
    ``rotation`` is degrees counter-clockwise about ``origin`` -- the
    same anchor/rotation convention hlayout.gui.canvas uses to draw a
    Text shape on screen (anchor="sw", positive rotation = CCW).

    Returns (strokes, skipped_chars):
      strokes: [[(x, y), ...], ...] -- one polyline per stroke, in the
        same coordinate space as ``origin`` (still needs any further
        transform, e.g. a parent Sref's placement, applied by the
        caller -- text_to_strokes() itself never flattens hierarchy).
      skipped_chars: count of characters with no glyph data (space
        always has a glyph, so it's never counted here).
    """
    scale = height / GLYPH_HEIGHT_UNITS
    theta = math.radians(rotation)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    ox, oy = origin

    strokes = []
    skipped_chars = 0
    cursor = 0.0  # advance, in grid units, along the untransformed baseline
    for ch in text:
        g = glyph(ch)
        if g is None:
            skipped_chars += 1
            cursor += _DEFAULT_ADVANCE
            continue
        advance, char_strokes = g
        for stroke in char_strokes:
            world_stroke = []
            for gx, gy in stroke:
                lx = (cursor + gx) * scale
                ly = gy * scale
                # Rotate (lx, ly) by `rotation` about the origin, then
                # translate -- same formula used for SRef/Path transforms
                # elsewhere in this codebase.
                wx = lx * cos_t - ly * sin_t + ox
                wy = lx * sin_t + ly * cos_t + oy
                world_stroke.append((wx, wy))
            strokes.append(world_stroke)
        cursor += advance
    return strokes, skipped_chars
