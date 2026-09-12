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
out to true physical scale; "fit to page"/"shrink to fit" in the
print dialog is still there for anything too large for one sheet.

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
"""

import html
import math
import os
import tempfile
from typing import Dict, List, Tuple

from ..core.library import Library
from ..core.shapes import Path, Polygon, Rectangle, Text, expand_arefs_to_srefs
from ..core.units import DBU_MM

_MAX_DEPTH = 64


def _flatten_for_print(library: Library, cell_name: str):
    """Walk the Sref/Aref hierarchy starting at ``cell_name``, applying
    each instance's transform, and bucket the resulting *world-space*
    geometry by layer number. Returns (polys, paths, texts):
      polys: {layer_number: [[(x, y), ...], ...]}        (DBU, float)
      paths: {layer_number: [([(x, y), ...], width_dbu), ...]}
      texts: {layer_number: [((x, y), string), ...]}
    Invisible layers are skipped entirely, same as the on-screen view.
    """
    polys: Dict[int, List[List[Tuple[float, float]]]] = {}
    paths: Dict[int, List[Tuple[List[Tuple[float, float]], float]]] = {}
    texts: Dict[int, List[Tuple[Tuple[float, float], str]]] = {}

    def walk(name, transform, scale, depth):
        if depth > _MAX_DEPTH or name not in library.cells:
            return
        cell = library.cells[name]
        for shape in cell.shapes:
            layer = library.layers[shape.layer]
            if not layer.visible:
                continue
            if isinstance(shape, (Rectangle, Polygon)):
                polys.setdefault(shape.layer, []).append([transform(p) for p in shape.points])
            elif isinstance(shape, Path):
                paths.setdefault(shape.layer, []).append(
                    ([transform(p) for p in shape.points], shape.width * scale))
            elif isinstance(shape, Text):
                texts.setdefault(shape.layer, []).append((transform(shape.origin), shape.text))
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

            walk(ref.cell_name, child_transform, scale * mag, depth + 1)

    walk(cell_name, lambda p: (float(p[0]), float(p[1])), 1.0, 0)
    return polys, paths, texts


def render_cell_svg(library: Library, cell_name: str, margin_dbu: float = 2000.0) -> Tuple[str, List[int]]:
    """Build an SVG document (as a string) of ``cell_name``'s visible,
    flattened geometry, sized to true physical scale in millimetres.
    Returns (svg_markup, layer_numbers_drawn) -- the second is used by
    write_print_html() to build a legend of only the layers that
    actually appear, in the order first encountered."""
    polys, paths, texts = _flatten_for_print(library, cell_name)

    layer_order: List[int] = []
    for n in list(polys) + list(paths) + list(texts):
        if n not in layer_order:
            layer_order.append(n)
    layer_order.sort()

    xs: List[float] = []
    ys: List[float] = []
    for pts_list in polys.values():
        for pts in pts_list:
            xs += [p[0] for p in pts]
            ys += [p[1] for p in pts]
    for pts_list in paths.values():
        for pts, _w in pts_list:
            xs += [p[0] for p in pts]
            ys += [p[1] for p in pts]
    for pts_list in texts.values():
        for (x, y), _s in pts_list:
            xs.append(x)
            ys.append(y)

    if not xs:
        # An empty (or fully-hidden) cell still prints -- as a blank
        # page of a token size -- rather than raising.
        x0, x1, y0, y1 = 0.0, 1.0, 0.0, 1.0
    else:
        x0, x1 = min(xs) - margin_dbu, max(xs) + margin_dbu
        y0, y1 = min(ys) - margin_dbu, max(ys) + margin_dbu
    width_dbu, height_dbu = x1 - x0, y1 - y0
    width_mm, height_mm = width_dbu * DBU_MM, height_dbu * DBU_MM

    def sx(x):
        return x - x0

    def sy(y):
        # SVG y grows downward; DBU y grows upward -- flip about the
        # viewBox's own height rather than the page, so this stays
        # correct regardless of margin_dbu.
        return y1 - y

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm:.3f}mm" '
        f'height="{height_mm:.3f}mm" viewBox="0 0 {width_dbu:.0f} {height_dbu:.0f}">',
        f'<rect x="0" y="0" width="{width_dbu:.0f}" height="{height_dbu:.0f}" fill="white"/>',
    ]
    for n in layer_order:
        layer = library.layers[n]
        color = layer.color or "#000000"
        parts.append(f'<g id="layer-{n}">')
        for pts in polys.get(n, []):
            pts_attr = " ".join(f"{sx(x):.2f},{sy(y):.2f}" for x, y in pts)
            fill = color if layer.fill else "none"
            parts.append(f'<polygon points="{pts_attr}" fill="{fill}" '
                          f'stroke="{color}" stroke-width="1"/>')
        for pts, width_dbu_path in paths.get(n, []):
            pts_attr = " ".join(f"{sx(x):.2f},{sy(y):.2f}" for x, y in pts)
            w = max(1.0, width_dbu_path)
            parts.append(f'<polyline points="{pts_attr}" fill="none" stroke="{color}" '
                          f'stroke-width="{w:.2f}" stroke-linecap="round" stroke-linejoin="round"/>')
        for (x, y), text in texts.get(n, []):
            parts.append(f'<text x="{sx(x):.2f}" y="{sy(y):.2f}" font-size="{height_dbu * 0.02:.1f}" '
                          f'fill="{color}">{html.escape(text)}</text>')
        parts.append('</g>')
    parts.append('</svg>')
    return "\n".join(parts), layer_order


def write_print_html(library: Library, cell_name: str, out_path: str = None) -> str:
    """Render ``cell_name`` (see render_cell_svg()) into a standalone,
    printable HTML page -- a title, a small color-swatch legend of the
    layers actually drawn, and the SVG itself -- and write it to
    ``out_path`` (a fresh temp .html file if not given). Returns the
    path written. Opening the result in a browser and printing it
    (Ctrl+P, or handed to the OS's own print flow -- see
    App.print_current_view()) is the actual "print" step; this
    function only produces the page."""
    svg, layer_order = render_cell_svg(library, cell_name)

    legend_rows = []
    for n in layer_order:
        layer = library.layers[n]
        legend_rows.append(
            f'<span style="display:inline-flex;align-items:center;margin-right:16px;">'
            f'<span style="width:12px;height:12px;background:{layer.color};'
            f'border:1px solid #333;display:inline-block;margin-right:4px;"></span>'
            f'{html.escape(f"{n}: {layer.name}")}</span>'
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
<p class="no-print" style="color:#555;">
  Printed at true physical scale from the design's own units.
  Use your browser's Print (Ctrl+P) if it didn't open automatically --
  "Fit to page" if the drawing is larger than one sheet.
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
