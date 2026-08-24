"""DXF export -- a single ASCII DXF (AutoCAD R12 / AC1009) file, with one
DXF layer per hlayout layer.

R12 is deliberately chosen over a newer DXF version: it's the most
universally-read DXF dialect there is (every CAD tool, old or new,
imports it cleanly) and its file structure is simple enough to write by
hand correctly -- no CLASSES/BLOCKS/OBJECTS sections, no per-entity
subclass markers or handles required.

Like Gerber (hlayout/io/gerberio.py), DXF is a manufacturing/interchange
*output* format -- GDSII (hlayout/io/gdsio.py) remains this tool's native
on-disk database format. Every Sref is flattened (its shapes copied out
with the placement's rotation/magnification/mirror/offset applied), so
the exported file never contains a block/INSERT of its own.

Unlike Gerber, a single DXF file naturally holds every layer (DXF has its
own LAYER table, so there's no need for one file per layer), and DXF
*does* have a native TEXT entity -- so, unlike write_gerber_layers(),
nothing here is silently skipped: every Polygon/Rectangle/Path/Text shape
on a visible layer is exported.

Format conventions:
  * Coordinates: millimetres, converted from hlayout's 1-DBU-per-micron
    grid. (R12 predates the $INSUNITS header variable, so the unit is a
    convention rather than something the file states -- worth knowing on
    import into a tool that assumes a different default unit.)
  * Polygon / Rectangle: a closed POLYLINE outlining the shape's points.
  * Path: a POLYLINE (open, unless the path's own points already close
    back on themselves) with a constant width (groups 40/41) equal to the
    path's width. R12's constant-width polyline has no round/square/
    flush end-cap concept, so -- like Gerber's aperture stroke -- the
    path's `cap` style isn't distinguished in the export.
  * Text: a TEXT entity (origin, height, rotation) -- exported as-is,
    since DXF has a native text primitive (Gerber does not).
  * Colour: R12 has no true-colour support, only the fixed 256-entry
    AutoCAD Color Index (ACI) palette. Each hlayout layer's colour is
    approximated to the nearest of the 9 standard ACI colours (red,
    yellow, green, cyan, blue, magenta, white, and two greys) -- coarse,
    but always a real, correctly-numbered ACI colour rather than a guess
    at the full 256-colour table (which isn't a simple formula).
"""

import math
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..core.errors import GeometryError
from ..core.library import Library
from ..core.shapes import Path, Polygon, Rectangle, SRef, Text, expand_arefs_to_srefs
from ..core.units import DBU_MM

_MAX_DEPTH = 64

# The 9 standard AutoCAD Color Index (ACI) entries whose RGB values are
# fixed/well-known regardless of AutoCAD version -- see module docstring.
_ACI_BASIC = {
    1: (255, 0, 0),      # red
    2: (255, 255, 0),    # yellow
    3: (0, 255, 0),      # green
    4: (0, 255, 255),    # cyan
    5: (0, 0, 255),      # blue
    6: (255, 0, 255),    # magenta
    7: (255, 255, 255),  # white/black (foreground colour)
    8: (128, 128, 128),  # grey
    9: (192, 192, 192),  # light grey
}


def _flatten(library: Library, cell_name: str):
    """Walk the Sref hierarchy starting at ``cell_name``, applying each
    instance's transform, and bucket the resulting *world-space* geometry
    by layer number. Returns (polys, paths, texts):
      polys: {layer_number: [[(x, y), ...], ...]}              (DBU, float)
      paths: {layer_number: [([(x, y), ...], width_dbu), ...]}
      texts: {layer_number: [(x, y, text, height_dbu, rotation_deg), ...]}
    Invisible layers are skipped entirely, same as the on-screen view.
    Text rotation under a mirrored ancestor is a best-effort composition
    (mirroring flips the sense of further rotation); plain nested
    rotation/translation/magnification is exact."""
    polys: Dict[int, List[List[Tuple[float, float]]]] = {}
    paths: Dict[int, List[Tuple[List[Tuple[float, float]], float]]] = {}
    texts: Dict[int, List[Tuple[float, float, str, float, float]]] = {}

    def walk(name, transform, rotation_deg, mirrored, scale, depth):
        if depth > _MAX_DEPTH or name not in library.cells:
            return
        cell = library.cells[name]
        for shape in cell.shapes:
            layer = library.layers[shape.layer]
            if not layer.visible:
                continue
            if isinstance(shape, (Rectangle, Polygon)):
                pts = [transform(p) for p in shape.points]
                polys.setdefault(shape.layer, []).append(pts)
            elif isinstance(shape, Path):
                pts = [transform(p) for p in shape.points]
                paths.setdefault(shape.layer, []).append((pts, shape.width * scale))
            elif isinstance(shape, Text):
                x, y = transform(shape.origin)
                rot = (shape.rotation + rotation_deg) % 360
                texts.setdefault(shape.layer, []).append(
                    (x, y, shape.text, shape.height * scale, rot)
                )
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

            child_rotation = rotation_deg + (-ref.rotation if mirrored else ref.rotation)
            walk(ref.cell_name, child_transform, child_rotation % 360,
                 mirrored ^ xr, scale * mag, depth + 1)

    walk(cell_name, lambda p: p, 0.0, False, 1.0, 0)
    return polys, paths, texts


def _safe_dxf_layer_name(layer) -> str:
    # DXF layer names may not contain <>/\":;?*|,=` or be empty.
    slug = re.sub(r'[<>/\\":;?*|,=`]+', "_", layer.name).strip()
    return slug or f"L{layer.number}"


def _nearest_aci(hex_color: str) -> int:
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return 7
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return 7
    best_idx, best_dist = 7, None
    for idx, (rr, gg, bb) in _ACI_BASIC.items():
        dist = (r - rr) ** 2 + (g - gg) ** 2 + (b - bb) ** 2
        if best_dist is None or dist < best_dist:
            best_idx, best_dist = idx, dist
    return best_idx


def _dxf_point(v_dbu: float) -> str:
    return f"{v_dbu * DBU_MM:.6f}"


def write_dxf(library: Library, cell_name: str, path: str,
              layers: Optional[List[int]] = None):
    """Write ``cell_name`` out as a single ASCII DXF (AutoCAD R12) file at
    ``path``, with one DXF layer per hlayout layer that's visible and has
    data.

    ``layers``, if given, restricts export to that subset of layer
    numbers (still only ones that are visible and have data); the
    default (None) exports every visible layer with data.

    Returns (written_layers, entity_count):
      written_layers: [layer_number, ...], in ascending order
      entity_count: total number of DXF entities written
    """
    if cell_name not in library.cells:
        raise ValueError(f"no such cell {cell_name!r}")

    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    polys, paths, texts = _flatten(library, cell_name)
    indices = sorted(set(polys.keys()) | set(paths.keys()) | set(texts.keys()))
    if layers is not None:
        indices = [n for n in indices if n in layers]
    indices = [n for n in indices if library.layers[n].visible]

    written_layers = []
    entity_count = 0
    layer_lines = []
    entity_lines = []

    for n in indices:
        layer = library.layers[n]
        p_list = polys.get(n, [])
        w_list = paths.get(n, [])
        t_list = texts.get(n, [])
        if not p_list and not w_list and not t_list:
            continue
        written_layers.append(n)
        dxf_name = _safe_dxf_layer_name(layer)
        aci = _nearest_aci(layer.color)

        layer_lines += [
            "0", "LAYER",
            "2", dxf_name,
            "70", "0",
            "62", str(aci),
            "6", "CONTINUOUS",
        ]

        for pts in p_list:
            if len(pts) < 3:
                continue
            entity_lines += [
                "0", "POLYLINE",
                "8", dxf_name,
                "62", str(aci),
                "66", "1",
                "70", "1",  # closed
            ]
            for x, y in pts:
                entity_lines += ["0", "VERTEX", "8", dxf_name,
                                  "10", _dxf_point(x), "20", _dxf_point(y)]
            entity_lines += ["0", "SEQEND"]
            entity_count += 1

        for pts, width_dbu in w_list:
            if len(pts) < 2:
                continue
            closed = pts[0] == pts[-1] and len(pts) > 2
            verts = pts[:-1] if closed else pts
            w = _dxf_point(width_dbu)
            entity_lines += [
                "0", "POLYLINE",
                "8", dxf_name,
                "62", str(aci),
                "66", "1",
                "70", "1" if closed else "0",
                "40", w, "41", w,
            ]
            for x, y in verts:
                entity_lines += ["0", "VERTEX", "8", dxf_name,
                                  "10", _dxf_point(x), "20", _dxf_point(y)]
            entity_lines += ["0", "SEQEND"]
            entity_count += 1

        for x, y, text, height_dbu, rotation in t_list:
            entity_lines += [
                "0", "TEXT",
                "8", dxf_name,
                "62", str(aci),
                "10", _dxf_point(x), "20", _dxf_point(y),
                "40", _dxf_point(height_dbu),
                "1", text,
                "50", f"{rotation:.4f}",
            ]
            entity_count += 1

    lines = [
        "0", "SECTION", "2", "HEADER",
        "9", "$ACADVER", "1", "AC1009",
        "0", "ENDSEC",

        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LTYPE", "70", "1",
        "0", "LTYPE", "2", "CONTINUOUS", "70", "0",
        "3", "Solid line", "72", "65", "73", "0", "40", "0.0",
        "0", "ENDTAB",
        "0", "TABLE", "2", "LAYER", "70", str(len(written_layers) + 1),
        "0", "LAYER", "2", "0", "70", "0", "62", "7", "6", "CONTINUOUS",
        *layer_lines,
        "0", "ENDTAB",
        "0", "ENDSEC",

        "0", "SECTION", "2", "ENTITIES",
        *entity_lines,
        "0", "ENDSEC",

        "0", "EOF",
    ]

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")

    return written_layers, entity_count


@dataclass
class DxfExportJob:
    """One job in a DXF export batch/set -- a saved, named write_dxf()
    call. See hlayout.scripting.api.Session.run_dxf_export_batch(),
    which runs a list of these together in one action instead of using
    File > Export DXF... once per job by hand (e.g. one job per
    downstream tool/consumer, each with its own layer subset and output
    file).

    Like hlayout.io.gerberio.GerberExportJob (and unlike
    hlayout.core.maskops.MaskOpStep), job order doesn't matter -- each
    job's output is entirely independent of every other job's (a DXF
    export never modifies the design, only writes a file) -- so, like
    the Gerber batch window, the DXF batch window has no Move Up/Move
    Down."""

    name: str
    path: str
    layers: Optional[List[int]] = None  # None = every visible layer with data

    def __post_init__(self):
        if not self.path:
            raise GeometryError("DXF export job requires an output file path")

    def to_dict(self) -> dict:
        return {"name": self.name, "path": self.path, "layers": self.layers}

    @classmethod
    def from_dict(cls, data: dict) -> "DxfExportJob":
        return cls(
            name=data.get("name", ""),
            path=data["path"],
            layers=data.get("layers"),
        )

    @property
    def label(self) -> str:
        layer_desc = ("all visible layers" if not self.layers
                       else f"layers {', '.join(map(str, self.layers))}")
        base = f"{layer_desc} -> {self.path}"
        return f"{self.name}: {base}" if self.name else base
