# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Print output -- renders a cell as a to-scale SVG drawing embedded in
a printable HTML page, since Tkinter has no native print dialog of its
own (the same constraint hlayout.gui.resistor_designer's
"Print / Preview Report..." already works around, there for a resistor
BOM rather than layout geometry -- see that module's docstring).

Only **visible** layers (Setup > Layers..., ``LAYER <n> visible=0|1``)
are drawn -- "print what's on screen", the same rule the Gerber/DXF
exporters follow. Every SRef/ARef is flattened (rotation/magnification/
mirror/offset applied), so nested instances print as real geometry, not
placeholders -- the same walk hlayout.io.gerberio._flatten_layer_data
does, with a third bucket added for Text shapes (Gerber has no text
primitive to hand a fab tool; a printed sheet benefits from ref-des
labels the way a photoplotter output never would).

The SVG is sized in real millimetres from the design's own DBU
geometry (1 DBU = 1 micron), so a browser printing it "at 100%" comes
out to true physical scale for anything already a reasonable size on
paper. A design smaller than PRINT_TARGET_MM in its larger dimension
(hybrid parts are routinely a few mm across) is auto-scaled UP to
around that size instead -- at "100%" that would otherwise print as a
barely-visible speck -- using the nearest of a fixed set of clean
factors (2x, 5x, 10x, ...) rather than whatever number happens to hit
the target exactly, so the scale printed on the page is one you could
actually measure back against. Pass an explicit ``scale`` to override
the automatic choice, or 1.0 to force true size regardless. "Fit to
page"/"shrink to fit" in the print dialog is still there for anything
too large for one sheet even after this.

Fidelity gaps (disclosed, not silently approximated):
  * Path end caps are always drawn round, regardless of the path's own
    cap style -- SVG's stroke-linecap could distinguish butt/round/
    square, but round is the closest single choice to "looks right at
    a glance" for the common case, same tradeoff Gerber output makes.
  * Text is placed at its transformed origin but drawn UNROTATED,
    ignoring both the shape's own .rotation and any ancestor SRef
    rotation -- ref-des labels are near-universally unrotated in
    practice, and getting a compounded rotation's sign right after the
    DBU-to-SVG y-flip is easy to get subtly backwards, which would be
    worse than a plain but correct label.
  * A polygon outline (drawn even when Fill is on, and the ONLY thing
    drawn when it's off -- an outline-only "Fill" layer is exactly how
    a component body/package outline is usually represented) is a
    plotting convention, not real geometry -- so it's held to a fixed
    physical HAIRLINE_MM on the page, not the design's actual DBU
    coordinates. Do not read a printed outline's thickness as a
    dimension.
  * A layer coloured white or near-white (routinely true for a layer
    meant to stand out on this tool's own dark canvas, e.g.
    CHIP_OUTLINE_COLOR) prints in BLACK instead (see
    _print_safe_color()/PRINT_SAFE_LUMINANCE_CUTOFF) -- on paper, white
    ink on a white page is genuinely invisible, not merely faint,
    which otherwise silently dropped that layer's entire drawn content
    (and its own legend swatch) with no visible sign anything was
    missing. Do not read a printed layer's colour as its on-screen one.
"""

import html
import math
import os
import tempfile
from typing import Dict, List, Optional, Tuple

from ..core.errors import CellError
from ..core.library import Library, _transform_bbox
from ..core.shapes import Path, Polygon, Rectangle, Text, expand_arefs_to_srefs
from ..core.units import DBU_MM
from .techfile import load_refdes_annotation_layer

_MAX_DEPTH = 64

#: Auto-scale-up target (mm): a design whose larger dimension is
#: smaller than this at true (1x) scale gets scaled up toward it
#: instead -- see render_cell_svg()'s "scale" parameter.
PRINT_TARGET_MM = 150.0

#: Clean scale factors auto-scaling picks from, smallest sufficient
#: one first -- printed engineering-drawing style (2x, 5x, 10x, ...),
#: not an arbitrary decimal that happens to hit PRINT_TARGET_MM exactly.
_NICE_SCALES = (1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0, 5000.0)

#: Thinnest line width (mm, on the physical printed page) any stroke is
#: ever allowed to shrink below -- a real drafting hairline. The
#: viewBox is in raw DBU, so a stroke needs a DIFFERENT DBU width at
#: every scale to come out at this same physical thickness; see
#: _hairline_dbu(). Without this, an outline-only layer (Fill
#: unchecked -- a component body/package outline is drawn this way
#: routinely) rendered with a literal "1 DBU" (1 micron) stroke is
#: geometrically present but printed at a small fraction of a pixel --
#: invisible, not merely faint, on any design bigger than a few dozen
#: microns across.
HAIRLINE_MM = 0.15


def _hairline_dbu(scale_used: float) -> float:
    return HAIRLINE_MM / (DBU_MM * scale_used)


#: Perceived-luminance (0-255, ITU-R BT.601) cutoff above which a layer's
#: own colour is swapped for black on the printed page -- catches pure
#: white and near-white (e.g. this tool's own CHIP_OUTLINE_COLOR="#ffffff",
#: chosen for contrast against the app's dark canvas) without touching a
#: merely light-but-visible colour like pale yellow.
PRINT_SAFE_LUMINANCE_CUTOFF = 250.0


def _print_safe_color(hex_color: str) -> str:
    """``hex_color`` (e.g. "#ffffff"), or black if it's too light to
    show up against PRINT's white page background. A layer coloured
    for maximum contrast on this tool's own dark canvas is routinely
    white or near-white -- on paper, white ink on a white page is
    genuinely invisible, not merely faint, which silently dropped a
    layer's entire drawn content from both the SVG and its own legend
    swatch with no indication anything was wrong."""
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return hex_color or "#000000"
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return hex_color
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#000000" if luminance > PRINT_SAFE_LUMINANCE_CUTOFF else hex_color


#: Brightest perceived luminance (0-255, same BT.601 measure as above) a
#: LINE is allowed to print at. A filled area in a pale colour is still
#: plainly visible on paper, but a thin stroke in that same colour is
#: not: a wire bond is a 25-50 micron Path on a layer coloured pale
#: lilac or yellow for contrast on the dark canvas, which printed as a
#: hairline too faint to see -- present in the SVG, absent to the eye.
PRINT_STROKE_MAX_LUMINANCE = 140.0


def _print_stroke_color(hex_color: str) -> str:
    """The colour to draw a LINE in (a Path, an unfilled outline, text):
    ``_print_safe_color(hex_color)``, darkened -- same hue, every
    channel scaled down by one factor -- until its luminance is no more
    than PRINT_STROKE_MAX_LUMINANCE. A colour that's already dark enough
    comes back unchanged; filled areas keep using _print_safe_color()."""
    color = _print_safe_color(hex_color)
    h = (color or "").lstrip("#")
    if len(h) != 6:
        return color
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return color
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    if luminance <= PRINT_STROKE_MAX_LUMINANCE:
        return color
    k = PRINT_STROKE_MAX_LUMINANCE / luminance
    return "#%02x%02x%02x" % (int(r * k), int(g * k), int(b * k))


def _auto_scale(width_mm: float, height_mm: float) -> float:
    largest = max(width_mm, height_mm, 1e-9)
    if largest >= PRINT_TARGET_MM:
        return 1.0
    needed = PRINT_TARGET_MM / largest
    for s in _NICE_SCALES:
        if s >= needed:
            return s
    return _NICE_SCALES[-1]


def _text_angle(shape_rotation, frame_rot, mirrored):
    """World reading angle of a Text drawn at ``shape_rotation`` inside a
    frame rotated ``frame_rot`` and possibly mirrored. Text is never drawn
    mirror-imaged (no plotter/print font does that); a mirrored frame
    just flips which way its baseline runs."""
    a = (-shape_rotation if mirrored else shape_rotation) + frame_rot
    return a % 360.0


#: Size of a synthesized label (floating ref-des, pin number) on paper --
#: matches DXF export's own (hlayout.io.dxfio.REFDES_EXPORT_HEIGHT_DBU).
LABEL_TEXT_HEIGHT_DBU = 500.0

#: Printed text: cap height of the SVG font as a fraction of font-size
#: (Arial/Helvetica ~0.716), so a Text shape's `height` -- its CAP height,
#: like hlayout.core.strokefont -- comes out that tall on paper.
_FONT_CAP_RATIO = 0.716
#: Printed text is squeezed/stretched to this width per character (x the
#: cap height) -- the same estimate Text.bbox() and every table/title
#: generator centres with, so printed text lands exactly where it was laid
#: out and never runs over a cell border.
_CHAR_WIDTH_RATIO = 0.6
_PRINT_FONT = "'Arial Narrow', 'Liberation Sans Narrow', Arial, Helvetica, sans-serif"


class _PrintText(tuple):
    """One flattened text entry: still compares/unpacks as the plain
    ``((x, y), string)`` pair it always was, and also carries its
    world-space cap ``height`` (DBU) and ``rotation`` (degrees CCW)."""

    def __new__(cls, pos, text, height=LABEL_TEXT_HEIGHT_DBU, rotation=0.0):
        obj = super().__new__(cls, (pos, text))
        obj.height = float(height)
        obj.rotation = float(rotation)
        return obj


def _flatten_for_print(library: Library, cell_name: str, pin_label_layers=None):
    """Walk the Sref/Aref hierarchy starting at ``cell_name``, applying
    each instance's transform, and bucket the resulting *world-space*
    geometry by layer number. Returns (polys, paths, texts):
      polys: {layer_number: [[(x, y), ...], ...]}        (DBU, float)
      paths: {layer_number: [([(x, y), ...], width_dbu), ...]}
      texts: {layer_number: [((x, y), string), ...]}
    Collects EVERY layer's geometry regardless of Setup > Layers...
    visibility -- callers filter to visible layers themselves (see
    render_cell_svg()) so hidden_layers_with_data() can also see what
    a hidden layer would have drawn, to explain a shape's absence from
    print rather than just silently reproducing it.

    A placed instance's SRef.label (the draggable ref-des annotation
    every placed part gets -- see Session.sref()'s own docstring) is
    NOT itself real geometry, so it's invisible to this walk's own
    ``cell.shapes`` loop above -- exactly the gap that made a printed
    page come out with no ref-des text at all even though the canvas
    clearly shows one floating over every part. Mirroring
    hlayout.io.dxfio._flatten_texts_and_refdes()'s own convention: a
    label already baked as a real Text shape inside the placed cell
    (``sh.text == ref.label``) is left alone (the ordinary shapes loop
    above already picks it up); otherwise a Text entry is synthesized
    here at the child's own bbox centre plus its label_offset, on
    load_refdes_annotation_layer()'s layer -- so PRINT and DXF export
    agree on where a ref-des ends up without either one baking
    anything into the design itself.

    ``pin_label_layers`` (optional, e.g. a bare die's own pad layer) is
    a wire-bonding aid: a numbered pin (Rectangle/Polygon.pin, or an
    SRef.pin -- a round lead built from a shared via-cell template, see
    hlayout.core.shapes.SRef.pin's own docstring) whose OWN layer (the
    shape's own, or the target cell's, for an SRef) is in this set gets
    its pin number drawn as a Text label at its own position, on that
    same layer. Deliberately opt-in and scoped by LAYER, not drawn for
    every numbered pin everywhere -- an ordinary passive/resistor's own
    2-terminal pins are ALSO pin-tagged (their "1"/"2" identify which
    end is which), and labelling those too would be unwanted clutter on
    every single part; a bare die's own pad layer is normally distinct
    from a passive's terminal layer, so naming just that layer reaches
    exactly the pins that matter for locating a bond."""
    polys: Dict[int, List[List[Tuple[float, float]]]] = {}
    paths: Dict[int, List[Tuple[List[Tuple[float, float]], float]]] = {}
    texts: Dict[int, List[Tuple[Tuple[float, float], str]]] = {}
    refdes_layer = load_refdes_annotation_layer(library)
    pin_layers = {int(n) for n in pin_label_layers} if pin_label_layers else set()

    def walk(name, transform, scale, depth, rot=0.0, mirrored=False):
        if depth > _MAX_DEPTH or name not in library.cells:
            return
        cell = library.cells[name]
        for shape in cell.shapes:
            if isinstance(shape, (Rectangle, Polygon)):
                polys.setdefault(shape.layer, []).append([transform(p) for p in shape.points])
                pin = getattr(shape, "pin", "")
                if pin and shape.layer in pin_layers:
                    x0, y0, x1, y1 = shape.bbox()
                    texts.setdefault(shape.layer, []).append(
                        _PrintText(transform(((x0 + x1) / 2.0, (y0 + y1) / 2.0)), pin)
                    )
            elif isinstance(shape, Path):
                paths.setdefault(shape.layer, []).append(
                    ([transform(p) for p in shape.points], shape.width * scale))
            elif isinstance(shape, Text):
                texts.setdefault(shape.layer, []).append(_PrintText(
                    transform(shape.origin), shape.text, shape.height * scale,
                    _text_angle(shape.rotation, rot, mirrored)))
        for ref in cell.srefs + expand_arefs_to_srefs(cell.arefs):
            theta = math.radians(ref.rotation)
            cos_t, sin_t = math.cos(theta), math.sin(theta)
            mag = ref.magnification
            ox, oy = ref.origin
            xr = ref.x_reflection
            parent = transform

            def child_transform(pt, cos_t=cos_t, sin_t=sin_t, mag=mag, ox=ox, oy=oy,
                                 xr=xr, parent=parent):
                x, y = pt
                if xr:
                    y = -y
                x *= mag
                y *= mag
                rx = x * cos_t - y * sin_t
                ry = x * sin_t + y * cos_t
                return parent((rx + ox, ry + oy))

            if ref.label and refdes_layer is not None and refdes_layer >= 0:
                child = library.cells.get(ref.cell_name)
                baked = None
                if child is not None:
                    for sh in child.shapes:
                        if isinstance(sh, Text) and sh.text == ref.label:
                            baked = sh
                            break
                if baked is None:
                    try:
                        box = library.cell_bbox(ref.cell_name)
                    except CellError:
                        box = None
                    if box is not None:
                        x1, y1, x2, y2 = _transform_bbox(box, ref)
                        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                    else:
                        cx, cy = ref.origin
                    lox, loy = ref.label_offset
                    # (cx+lox, cy+loy) is in `cell`'s own local space --
                    # the same space shape.origin is in for cell.shapes
                    # above -- so it goes through the accumulated
                    # ancestor `transform`, not `child_transform`.
                    texts.setdefault(refdes_layer, []).append(
                        _PrintText(transform((cx + lox, cy + loy)), ref.label)
                    )

            ref_pin = getattr(ref, "pin", "")
            if ref_pin and pin_layers:
                child = library.cells.get(ref.cell_name)
                if child is not None:
                    pin_layer = next((sh.layer for sh in child.shapes
                                       if sh.layer in pin_layers), None)
                    if pin_layer is not None:
                        # ref.origin is in `cell`'s own local space, same
                        # as ref.label's -- the accumulated ancestor
                        # `transform`, not `child_transform`.
                        texts.setdefault(pin_layer, []).append(
                            _PrintText(transform(ref.origin), ref_pin)
                        )

            # world angle of the child's +x axis, and whether its frame is
            # mirrored (an odd number of x_reflections on the way down)
            child_rot = rot + (-ref.rotation if mirrored else ref.rotation)
            walk(ref.cell_name, child_transform, scale * mag, depth + 1,
                 child_rot, mirrored != bool(xr))

    walk(cell_name, lambda p: (float(p[0]), float(p[1])), 1.0, 0)
    return polys, paths, texts


def render_cell_svg(library: Library, cell_name: str, margin_dbu: float = 2000.0,
                     scale: Optional[float] = None, pin_label_layers=None) -> Tuple[str, List[int], float]:
    """Build an SVG document (as a string) of ``cell_name``'s visible,
    flattened geometry. ``scale`` sets the printed size relative to
    true physical size (2.0 = twice actual size on paper); left as
    None, it's chosen automatically (see the module docstring) --
    1.0 unless the design is smaller than PRINT_TARGET_MM, in which
    case it's scaled up to about that size using the nearest clean
    factor. ``pin_label_layers`` draws a numbered-pin label at every
    pin on those layers -- see _flatten_for_print()'s own docstring for
    why this is opt-in and layer-scoped rather than automatic. Returns
    (svg_markup, layer_numbers_drawn, scale_used) -- the layer list is
    used by write_print_html() to build a legend of only the layers
    that actually appear, in the order first encountered; the scale is
    shown on the printed page so it's never ambiguous what's been done
    to the drawing's size."""
    polys, paths, texts = _flatten_for_print(library, cell_name, pin_label_layers)

    # "Print what's on screen": _flatten_for_print() collects every
    # layer's geometry regardless of visibility, but only a VISIBLE
    # layer is actually drawn (or counted toward the bbox/auto-scale) --
    # same rule the Gerber/DXF exporters follow. A layer with data that
    # got left out here because it's hidden is exactly what
    # hidden_layers_with_data() reports, so a shape's absence from the
    # page has an answer instead of looking like a print bug.
    layer_order: List[int] = []
    for n in list(polys) + list(paths) + list(texts):
        if n not in layer_order and library.layers[n].visible:
            layer_order.append(n)
    layer_order.sort()

    xs: List[float] = []
    ys: List[float] = []
    for n in layer_order:
        for pts in polys.get(n, []):
            xs += [p[0] for p in pts]
            ys += [p[1] for p in pts]
        for pts, _w in paths.get(n, []):
            xs += [p[0] for p in pts]
            ys += [p[1] for p in pts]
        for t in texts.get(n, []):
            (x, y), _s = t
            h = getattr(t, "height", LABEL_TEXT_HEIGHT_DBU)
            w = h * _CHAR_WIDTH_RATIO * len(_s)
            a = math.radians(getattr(t, "rotation", 0.0))
            for dx, dy in ((0, 0), (w, 0), (0, h), (w, h)):
                xs.append(x + dx * math.cos(a) - dy * math.sin(a))
                ys.append(y + dx * math.sin(a) + dy * math.cos(a))

    if not xs:
        # An empty (or fully-hidden) cell still prints -- as a blank
        # page of a token size -- rather than raising.
        x0, x1, y0, y1 = 0.0, 1.0, 0.0, 1.0
    else:
        x0, x1 = min(xs) - margin_dbu, max(xs) + margin_dbu
        y0, y1 = min(ys) - margin_dbu, max(ys) + margin_dbu
    width_dbu, height_dbu = x1 - x0, y1 - y0
    width_mm, height_mm = width_dbu * DBU_MM, height_dbu * DBU_MM
    scale_used = float(scale) if scale is not None else _auto_scale(width_mm, height_mm)
    # The viewBox stays in raw DBU -- unscaled -- so scaling up is just
    # a bigger physical width/height for the SAME viewBox; the browser
    # stretches the drawing to fill it, which is exactly "print
    # bigger" without touching a single coordinate below.
    printed_width_mm, printed_height_mm = width_mm * scale_used, height_mm * scale_used
    hairline = _hairline_dbu(scale_used)

    def sx(x):
        return x - x0

    def sy(y):
        # SVG y grows downward; DBU y grows upward -- flip about the
        # viewBox's own height rather than the page, so this stays
        # correct regardless of margin_dbu.
        return y1 - y

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{printed_width_mm:.3f}mm" '
        f'height="{printed_height_mm:.3f}mm" viewBox="0 0 {width_dbu:.0f} {height_dbu:.0f}">',
        f'<rect x="0" y="0" width="{width_dbu:.0f}" height="{height_dbu:.0f}" fill="white"/>',
    ]
    for n in layer_order:
        layer = library.layers[n]
        color = _print_safe_color(layer.color)
        # Lines get a darkened version of a pale layer colour so a thin
        # Path (a wire bond, say) or an unfilled outline still shows on
        # white paper; filled areas keep the layer's own colour.
        line_color = _print_stroke_color(layer.color)
        parts.append(f'<g id="layer-{n}">')
        for pts in polys.get(n, []):
            pts_attr = " ".join(f"{sx(x):.2f},{sy(y):.2f}" for x, y in pts)
            fill = color if layer.fill else "none"
            outline = color if layer.fill else line_color
            parts.append(f'<polygon points="{pts_attr}" fill="{fill}" '
                          f'stroke="{outline}" stroke-width="{hairline:.4f}"/>')
        for pts, width_dbu_path in paths.get(n, []):
            pts_attr = " ".join(f"{sx(x):.2f},{sy(y):.2f}" for x, y in pts)
            w = max(hairline, width_dbu_path)
            parts.append(f'<polyline points="{pts_attr}" fill="none" stroke="{line_color}" '
                          f'stroke-width="{w:.4f}" stroke-linecap="round" stroke-linejoin="round"/>')
        for t in texts.get(n, []):
            (x, y), text = t
            if not text.strip():
                continue
            h = getattr(t, "height", LABEL_TEXT_HEIGHT_DBU)
            rot = getattr(t, "rotation", 0.0)
            length = h * _CHAR_WIDTH_RATIO * len(text)
            turn = f' transform="rotate({-rot:.3f} {sx(x):.2f} {sy(y):.2f})"' if rot else ""
            parts.append(
                f'<text x="{sx(x):.2f}" y="{sy(y):.2f}" font-size="{h / _FONT_CAP_RATIO:.2f}" '
                f'font-family="{_PRINT_FONT}" textLength="{length:.2f}" '
                f'lengthAdjust="spacingAndGlyphs" xml:space="preserve" '
                f'fill="{line_color}"{turn}>{html.escape(text)}</text>')
        parts.append('</g>')
    parts.append('</svg>')
    return "\n".join(parts), layer_order, scale_used


def hidden_layers_with_data(library: Library, cell_name: str) -> List[int]:
    """Layer numbers that have geometry somewhere in ``cell_name`` (any
    depth of SRef/ARef) but are currently NOT visible (Setup >
    Layers..., ``LAYER <n> visible=0``). Print/PRINT silently leaves
    these off the page -- "print what's on screen", the same rule the
    Gerber/DXF exporters follow -- which is easy to mistake for a bug
    ("my package didn't print") when the shape was never going to show
    up on the canvas either. write_print_html() surfaces this list
    directly on the page instead of leaving it to guesswork. Returns
    the layer numbers sorted, empty if nothing's affected."""
    polys, paths, texts = _flatten_for_print(library, cell_name)
    have_data = set(polys) | set(paths) | set(texts)
    return sorted(n for n in have_data if not library.layers[n].visible)


def write_print_html(library: Library, cell_name: str, out_path: str = None,
                      scale: Optional[float] = None, pin_label_layers=None) -> str:
    """Render ``cell_name`` (see render_cell_svg()) into a standalone,
    printable HTML page -- a title, a scale note, a small color-swatch
    legend of the layers actually drawn, and the SVG itself -- and
    write it to ``out_path`` (a fresh temp .html file if not given).
    ``scale`` is passed straight through to render_cell_svg() -- left
    as None, small designs are auto-scaled up so "print at 100%"
    produces something bigger than a speck; see that function and the
    module docstring. ``pin_label_layers`` is also passed straight
    through -- see _flatten_for_print()'s own docstring. Returns the
    path written. Opening the result in a browser and printing it
    (Ctrl+P, or handed to the OS's own print flow -- see
    App.print_current_view()) is the actual "print" step; this
    function only produces the page."""
    svg, layer_order, scale_used = render_cell_svg(
        library, cell_name, scale=scale, pin_label_layers=pin_label_layers)
    hidden = hidden_layers_with_data(library, cell_name)

    legend_rows = []
    for n in layer_order:
        layer = library.layers[n]
        swatch_color = _print_safe_color(layer.color)
        legend_rows.append(
            f'<span style="display:inline-flex;align-items:center;margin-right:16px;">'
            f'<span style="width:12px;height:12px;background:{swatch_color};'
            f'border:1px solid #333;display:inline-block;margin-right:4px;"></span>'
            f'{html.escape(f"{n}: {layer.name}")}</span>'
        )

    if scale_used == 1.0:
        scale_note = "Scale: 1:1 (true physical size)."
    else:
        scale_note = f"Scale: {scale_used:g}x actual size."

    hidden_note = ""
    if hidden:
        names = ", ".join(f"{n} ({library.layers[n].name})" for n in hidden)
        hidden_note = (
            f'<p class="no-print" style="color:#9a6700;background:#fff8e6;'
            f'border:1px solid #e0c67a;border-radius:4px;padding:8px 10px;">'
            f'<strong>Not shown:</strong> {html.escape(names)} '
            f"{'has' if len(hidden) == 1 else 'have'} geometry here but "
            f"{'is' if len(hidden) == 1 else 'are'} currently hidden "
            f"(Setup &gt; Layers... to turn {'it' if len(hidden) == 1 else 'them'} "
            f"back on) -- Print skips a hidden layer the same way the canvas does."
            f"</p>"
        )

    page = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>HLayout - {html.escape(cell_name)}</title>
<style>
  body {{ font-family: sans-serif; margin: 16px; }}
  .legend {{ margin: 8px 0 16px 0; }}
  .drawing {{ border: 1px solid #ccc; }}
  @media print {{ .no-print {{ display: none; }} .drawing {{ border: none; }} }}
</style>
</head>
<body>
<h2>{html.escape(cell_name)}</h2>
<p style="color:#555;"><strong>{html.escape(scale_note)}</strong></p>
{hidden_note}
<p class="no-print" style="color:#555;">
  Use your browser's Print (Ctrl+P) if it didn't open automatically --
  "Fit to page" if the drawing is larger than one sheet. Pass
  scale= to PRINT/print_cell() to override the scale shown above.
</p>
<div class="legend">{"".join(legend_rows)}</div>
<div class="drawing">{svg}</div>
</body>
</html>
"""
    if out_path is None:
        fd, out_path = tempfile.mkstemp(prefix="hlayout_print_", suffix=".html")
        os.close(fd)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    return out_path


def send_to_system_printer(path: str) -> bool:
    """Hand ``path`` to the OS's own print flow -- on Windows,
    ``os.startfile(path, "print")`` (a Win32 ShellExecute "print" verb):
    whatever's registered to handle the file's extension normally
    prints it, typically the default browser for a .html page. Returns
    True if the request was accepted, False if this platform/setup
    can't do it (no ``os.startfile`` at all off Windows, or no "print"
    verb registered for the extension) -- the caller should fall back
    to just opening the file and letting the user print manually
    (Ctrl+P) in that case. True is not a guarantee the job actually
    printed -- once handed off, that's between the OS/driver and the
    user, same as double-clicking "Print" in any other application."""
    try:
        os.startfile(path, "print")
        return True
    except (AttributeError, OSError):
        return False
