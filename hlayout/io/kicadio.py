"""KiCad footprint library import -- one GDS Cell per footprint.

Reads a KiCad `.pretty` directory (a folder of `.kicad_mod` files, each
one PCB footprint) and creates one Cell per footprint in an hlayout
Library, so the whole library becomes a set of placeable GDSII cells
(see hlayout/io/gdsio.py -- this module builds a Library the normal way
and leaves writing it to disk to save_gds()).

Format support: only the legacy `(module ...)` S-expression grammar is
understood -- this is what every footprint in KiCad's own
kicad-footprints repository used through the KiCad 5 era, and it's what
`load_kicad_footprint_library()` was built and verified against. KiCad
6 introduced a different (though similar) `(footprint ...)` grammar for
newer libraries; a `.kicad_mod` in that format is detected and rejected
with a clear error rather than silently mis-parsed.

Within a `(module ...)`, these elements are converted:
  * pad shapes rect/circle/oval/roundrect/trapezoid -- drawn as a filled
    Polygon, once per KiCad copper/mask/paste layer the pad lists.
    roundrect corners are tessellated (4 segments/corner); oval pads
    become a true stadium shape (rect + two semicircle caps), not just
    their bounding rectangle. A trapezoid pad's `delta` skew is applied
    using KiCad's own trapezoid definition, but only the delta=(0,0)
    case (an ordinary rectangle) exists anywhere in the libraries this
    was verified against -- a real nonzero delta is unverified.
  * pad shape "custom" -- only the `gr_poly` primitive is supported
    (the only one that appears in practice, for paste-layer thermal-via
    relief patterns); gr_line/gr_circle/gr_arc primitives inside a
    custom pad are not handled and are silently skipped.
  * a thru_hole/np_thru_hole pad's `drill` -- a round drill is drawn as
    a filled circle on the drill layer; a slotted (oval) drill is
    skipped (not supported).
  * fp_line / fp_circle / fp_arc -- reference art (silkscreen, F.Fab
    assembly outline, F.CrtYd courtyard) drawn as open Paths at the
    KiCad-given stroke width. fp_circle/fp_arc are tessellated to short
    line segments; there is no analytic arc shape here to draw them
    exactly.
  * fp_text (reference/value/user) -- drawn as an hlayout Text label on
    whatever layer it names, sized from its declared font height. This
    is a real text label, not KiCad's stroke-font glyph outlines.
  * fp_poly, 3D `model` references, and free-standing text tables are
    not handled -- fp_poly never appears in the libraries this was
    tested against; `model` has no 2D geometry to convert.

Only front-side layers matter in practice (footprints are single-sided
drawings); B.* layers map to the same GDS layer number as their F.*
counterpart so a back-side reference doesn't silently vanish.

Coordinates are millimetres in the source file, converted to hlayout's
1-micron DBU grid (hlayout.core.units.mm_to_dbu) -- sub-micron pad
geometry (this dataset has some, e.g. 0.038mm corner radii) rounds to
the nearest micron, same precision loss as importing into any other
micron-grid GDSII tool.
"""

import math
import os
import re
from typing import List, Optional

from ..core.errors import LayoutError
from ..core.library import Library
from ..core.shapes import circle_points
from ..core.units import mm_to_dbu

# --- GDS layer assignment -----------------------------------------------

COPPER_LAYER = 1
SILKSCREEN_LAYER = 2
FAB_LAYER = 3
COURTYARD_LAYER = 4
MASK_LAYER = 5
PASTE_LAYER = 6
DRILL_LAYER = 7

_KICAD_LAYER_MAP = {
    "F.Cu": COPPER_LAYER, "B.Cu": COPPER_LAYER, "*.Cu": COPPER_LAYER,
    "F.SilkS": SILKSCREEN_LAYER, "B.SilkS": SILKSCREEN_LAYER,
    "F.Fab": FAB_LAYER, "B.Fab": FAB_LAYER,
    "F.CrtYd": COURTYARD_LAYER, "B.CrtYd": COURTYARD_LAYER,
    "F.Mask": MASK_LAYER, "B.Mask": MASK_LAYER, "*.Mask": MASK_LAYER,
    "F.Paste": PASTE_LAYER, "B.Paste": PASTE_LAYER, "*.Paste": PASTE_LAYER,
}

_LAYER_STYLE = {
    COPPER_LAYER: ("Copper", "#c83232"),
    SILKSCREEN_LAYER: ("Silkscreen", "#f2f2f2"),
    FAB_LAYER: ("Fab", "#8080ff"),
    COURTYARD_LAYER: ("Courtyard", "#ffaa00"),
    MASK_LAYER: ("Soldermask", "#328232"),
    PASTE_LAYER: ("Paste", "#aaaaaa"),
    DRILL_LAYER: ("Drill", "#000000"),
}


def style_kicad_layers(library: Library) -> None:
    """Name/color the standard layers this importer draws on -- purely
    cosmetic, call once after importing so the layer panel is legible
    instead of showing "L1", "L2", ..."""
    for number, (name, color) in _LAYER_STYLE.items():
        library.layers.set_style(number, name=name, color=color)


# --- S-expression parsing -------------------------------------------------

_TOKEN_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()]+')


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text)


def _parse_sexp(text: str):
    """Parse one S-expression into nested Python lists; atoms stay as
    plain strings (quoted strings are unquoted, unescaped)."""
    tokens = _tokenize(text)
    pos = 0

    def parse_one():
        nonlocal pos
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            items = []
            while tokens[pos] != ")":
                items.append(parse_one())
            pos += 1  # consume ')'
            return items
        if tok == ")":
            raise LayoutError("unexpected ')' in KiCad S-expression")
        if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"':
            return tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        return tok

    result = parse_one()
    if pos != len(tokens):
        raise LayoutError("trailing content after top-level KiCad S-expression")
    return result


def _find(node, key) -> Optional[list]:
    for item in node:
        if isinstance(item, list) and item and item[0] == key:
            return item
    return None


def _find_all(node, key) -> List[list]:
    return [item for item in node if isinstance(item, list) and item and item[0] == key]


def _f(x) -> float:
    return float(x)


# --- local (pad-centered, un-rotated, mm) shape point generators ---------

def _rect_pts(w, h):
    return [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]


def _roundrect_pts(w, h, rratio, segs_per_corner=4):
    r = rratio * min(w, h)
    if r <= 1e-9:
        return _rect_pts(w, h)
    hw, hh = w / 2, h / 2
    # corner arc centers and their angular span, going counter-clockwise
    # starting at the bottom-right corner
    corners = [
        (hw - r, -hh + r, -90, 0),
        (hw - r, hh - r, 0, 90),
        (-hw + r, hh - r, 90, 180),
        (-hw + r, -hh + r, 180, 270),
    ]
    pts = []
    for cx, cy, a0, a1 in corners:
        for i in range(segs_per_corner + 1):
            a = math.radians(a0 + (a1 - a0) * i / segs_per_corner)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _oval_pts(w, h, segs=16):
    if abs(w - h) < 1e-9:
        r = w / 2
        return [(r * math.cos(2 * math.pi * i / segs), r * math.sin(2 * math.pi * i / segs))
                for i in range(segs)]
    half_segs = max(2, segs // 2)
    pts = []
    if w > h:
        r = h / 2
        d = (w - h) / 2
        for cx, a0, a1 in ((d, -90, 90), (-d, 90, 270)):
            for i in range(half_segs + 1):
                a = math.radians(a0 + (a1 - a0) * i / half_segs)
                pts.append((cx + r * math.cos(a), r * math.sin(a)))
    else:
        r = w / 2
        d = (h - w) / 2
        for cy, a0, a1 in ((d, 0, 180), (-d, 180, 360)):
            for i in range(half_segs + 1):
                a = math.radians(a0 + (a1 - a0) * i / half_segs)
                pts.append((r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _trapezoid_pts(w, h, dx, dy):
    # KiCad's symmetric trapezoid: dx skews the top/bottom edges, dy
    # skews the left/right edges (normally only one is nonzero). Best
    # effort -- verified only for dx == dy == 0 (an ordinary rectangle),
    # which is the only case present in the libraries this was tested
    # against; ordering matches KiCad's own corner sequence.
    hw, hh = w / 2, h / 2
    return [
        (-hw - dy / 2, -hh - dx / 2),
        (hw + dy / 2, -hh + dx / 2),
        (hw - dy / 2, hh - dx / 2),
        (-hw + dy / 2, hh + dx / 2),
    ]


def _rotate(x, y, angle_deg):
    if not angle_deg:
        return x, y
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    return x * ca - y * sa, x * sa + y * ca


def _world_dbu(px, py, angle_deg, cx, cy):
    rx, ry = _rotate(px, py, angle_deg)
    return (mm_to_dbu(rx + cx), mm_to_dbu(ry + cy))


def _dedupe_consecutive(pts):
    """Drop consecutive duplicate points (can happen once coordinates
    are rounded to the 1-micron DBU grid) -- a Path with any coincident
    consecutive pair round-trips through GDSII as a shorter path (gdstk
    collapses them), so it's safer to never write one in the first
    place than to have it silently change point count on reload."""
    out = []
    for p in pts:
        if not out or out[-1] != p:
            out.append(p)
    return out


# --- element emitters ------------------------------------------------------

def _emit_pad(cell, node):
    # (pad <name> <smd|thru_hole|np_thru_hole> <shape> (at x y [angle])
    #   (size w h) (layers ...) ...)
    shape = node[3]
    at = _find(node, "at")
    x, y = _f(at[1]), _f(at[2])
    angle = _f(at[3]) if len(at) > 3 else 0.0
    layers_node = _find(node, "layers")
    layer_names = layers_node[1:] if layers_node else []
    gds_layers = sorted({_KICAD_LAYER_MAP[l] for l in layer_names if l in _KICAD_LAYER_MAP})

    if shape == "custom":
        prim = _find(node, "primitives")
        if prim is not None:
            for gp in _find_all(prim, "gr_poly"):
                pts_node = _find(gp, "pts")
                local = [(_f(xy[1]), _f(xy[2])) for xy in pts_node[1:]
                         if isinstance(xy, list) and xy and xy[0] == "xy"]
                world = _dedupe_consecutive([_world_dbu(px, py, angle, x, y) for px, py in local])
                if len(world) < 3:
                    continue
                for layer in gds_layers:
                    cell.add_polygon(layer, world)
        return

    size = _find(node, "size")
    sw, sh = _f(size[1]), _f(size[2])

    if shape == "rect":
        local = _rect_pts(sw, sh)
    elif shape == "roundrect":
        rr = _find(node, "roundrect_rratio")
        local = _roundrect_pts(sw, sh, _f(rr[1]) if rr else 0.0)
    elif shape in ("oval", "circle"):
        local = _oval_pts(sw, sh)
    elif shape == "trapezoid":
        delta = _find(node, "delta")
        dx, dy = (_f(delta[1]), _f(delta[2])) if delta else (0.0, 0.0)
        local = _trapezoid_pts(sw, sh, dx, dy)
    else:
        return  # unrecognized pad shape -- skip rather than guess

    world = _dedupe_consecutive([_world_dbu(px, py, angle, x, y) for px, py in local])
    if len(world) >= 3:
        for layer in gds_layers:
            cell.add_polygon(layer, world)

    drill = _find(node, "drill")
    if drill is not None:
        if drill[1] in ("oval",):
            return  # slotted holes not supported
        dia = _f(drill[1])
        r_dbu = mm_to_dbu(dia / 2)
        if r_dbu > 0:
            cell.add_circle(DRILL_LAYER, (mm_to_dbu(x), mm_to_dbu(y)), r_dbu, segments=32)


def _emit_fp_line(cell, node):
    layer_node = _find(node, "layer")
    layer = _KICAD_LAYER_MAP.get(layer_node[1]) if layer_node else None
    if layer is None:
        return
    start, end = _find(node, "start"), _find(node, "end")
    width_node = _find(node, "width")
    width_mm = _f(width_node[1]) if width_node else 0.1
    p0 = (mm_to_dbu(_f(start[1])), mm_to_dbu(_f(start[2])))
    p1 = (mm_to_dbu(_f(end[1])), mm_to_dbu(_f(end[2])))
    if p0 == p1:
        return  # zero-length line (a handful of these exist in the
        # source libraries, e.g. Package_TO_SOT_SMD.pretty/SOT-23 --
        # KiCad silently tolerates/ignores them; a Path needs >=2
        # distinct points so it can't be represented, and there's
        # nothing to draw anyway)
    cell.add_path(layer, [p0, p1], width=max(1, mm_to_dbu(width_mm)))


def _emit_fp_circle(cell, node):
    layer_node = _find(node, "layer")
    layer = _KICAD_LAYER_MAP.get(layer_node[1]) if layer_node else None
    if layer is None:
        return
    center, end = _find(node, "center"), _find(node, "end")
    cx, cy = _f(center[1]), _f(center[2])
    ex, ey = _f(end[1]), _f(end[2])
    radius_mm = math.hypot(ex - cx, ey - cy)
    r_dbu = mm_to_dbu(radius_mm)
    if r_dbu <= 0:
        return
    width_node = _find(node, "width")
    width_mm = _f(width_node[1]) if width_node else 0.1
    # KiCad's fp_circle is a stroked ring, not a filled disc -- drawn
    # here as a closed Path (not a Polygon) so it renders the same open
    # outline KiCad shows, not a solid dot.
    pts = circle_points((mm_to_dbu(cx), mm_to_dbu(cy)), r_dbu, 32)
    pts = _dedupe_consecutive(pts + [pts[0]])
    if len(pts) < 2:
        return
    cell.add_path(layer, pts, width=max(1, mm_to_dbu(width_mm)))


def _emit_fp_arc(cell, node):
    layer_node = _find(node, "layer")
    layer = _KICAD_LAYER_MAP.get(layer_node[1]) if layer_node else None
    if layer is None:
        return
    # Legacy (module ...) grammar: "start" is the arc CENTER, "end" is
    # the arc's starting point, "angle" the sweep in degrees.
    start, end = _find(node, "start"), _find(node, "end")
    angle_node = _find(node, "angle")
    angle_deg = _f(angle_node[1]) if angle_node else 0.0
    cx, cy = _f(start[1]), _f(start[2])
    sx, sy = _f(end[1]), _f(end[2])
    radius = math.hypot(sx - cx, sy - cy)
    if radius <= 0 or angle_deg == 0:
        return
    width_node = _find(node, "width")
    width_mm = _f(width_node[1]) if width_node else 0.1
    start_angle = math.atan2(sy - cy, sx - cx)
    segs = max(2, int(round(abs(angle_deg) / 10)))
    pts = []
    for i in range(segs + 1):
        a = start_angle + math.radians(angle_deg) * i / segs
        pts.append((mm_to_dbu(cx + radius * math.cos(a)), mm_to_dbu(cy + radius * math.sin(a))))
    pts = _dedupe_consecutive(pts)
    if len(pts) < 2:
        return
    cell.add_path(layer, pts, width=max(1, mm_to_dbu(width_mm)))


def _emit_fp_text(cell, node):
    # (fp_text reference|value|user <text> (at x y [angle]) (layer L) (effects ...))
    text = node[2]
    layer_node = _find(node, "layer")
    layer = _KICAD_LAYER_MAP.get(layer_node[1]) if layer_node else None
    if layer is None:
        return
    at = _find(node, "at")
    x, y = _f(at[1]), _f(at[2])
    angle = _f(at[3]) if len(at) > 3 else 0.0
    height_mm = 1.0
    effects = _find(node, "effects")
    if effects is not None:
        font = _find(effects, "font")
        size = _find(font, "size") if font is not None else None
        if size is not None:
            height_mm = _f(size[1])
    height_dbu = max(1, mm_to_dbu(height_mm))
    cell.add_text(layer, (mm_to_dbu(x), mm_to_dbu(y)), str(text), height=height_dbu, rotation=angle)


_EMITTERS = {
    "pad": _emit_pad,
    "fp_line": _emit_fp_line,
    "fp_circle": _emit_fp_circle,
    "fp_arc": _emit_fp_arc,
    "fp_text": _emit_fp_text,
}


def _convert_module(tree, library: Library):
    name = tree[1]
    cell = library.new_cell(name)
    for item in tree[2:]:
        if not (isinstance(item, list) and item):
            continue
        emitter = _EMITTERS.get(item[0])
        if emitter is not None:
            emitter(cell, item)
    return cell


# --- public API -------------------------------------------------------------

def load_kicad_footprint(path: str, library: Library):
    """Import a single .kicad_mod file as one new Cell in `library`.
    Returns the Cell. Raises LayoutError if the file isn't the legacy
    `(module ...)` format this importer supports (see module docstring)."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    tree = _parse_sexp(text)
    if not (isinstance(tree, list) and tree):
        raise LayoutError(f"{path}: empty or unparseable KiCad S-expression")
    if tree[0] == "footprint":
        raise LayoutError(
            f"{path}: KiCad 6+ '(footprint ...)' format is not supported by this importer "
            "(only the legacy '(module ...)' format is)"
        )
    if tree[0] != "module":
        raise LayoutError(f"{path}: not a recognized .kicad_mod file (top-level '{tree[0]}')")
    return _convert_module(tree, library)


def load_kicad_footprint_library(pretty_dir: str, library: Library) -> List[str]:
    """Import every .kicad_mod file in a KiCad `.pretty` footprint
    library directory as one Cell per footprint in `library`. Returns
    the list of cell names created, in filename-sorted order."""
    created = []
    for fname in sorted(os.listdir(pretty_dir)):
        if not fname.endswith(".kicad_mod"):
            continue
        cell = load_kicad_footprint(os.path.join(pretty_dir, fname), library)
        created.append(cell.name)
    return created
