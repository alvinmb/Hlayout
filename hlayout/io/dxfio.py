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
nothing here is silently skipped: every Polygon/Rectangle/Path shape on a
visible layer is exported, along with any Text and every ref designator
(baked or floating -- see "Reference designators" below).

Every layer's Polygon/Rectangle/Path shapes are UNIONED together with
the same shapely pipeline Tools > Mask Operations... uses
(hlayout.core.maskops.layer_to_shapely_flattened()) before being
written out, rather than exported as separate, possibly-overlapping
entities:
  * A Path has no filled area of its own (it's a centreline + width),
    so it's first stroked into its outline polygon (buffered by half
    its width, honouring its end-cap style) -- meaning its width is
    baked directly into real, unambiguous polygon geometry instead of
    a DXF POLYLINE "constant width" attribute that not every reader
    honours.
  * Two overlapping/touching shapes on the same layer merge into one
    clean region, matching the as-fired reality of a screen-printed
    film -- not two overlapping outlines a downstream tool has to
    reconcile itself.
  * A resulting hole (e.g. a dielectric fill with a via window cut in
    it) is closed with the same "bridge to a zero-width slit" trick
    hlayout.core.maskops uses for GDSII output -- DXF's own POLYLINE
    has no native hole concept either.
Requires the shapely package (also needed for Mask Operations/DRC).

Reference designators: a ref-des that's baked into its own cell as a
real Text shape (rare -- see hlayout.gui.canvas._refdes_baked_shape())
is already picked up by the ordinary Text export above. The much more
common case -- SRef.label floating over the instance, drawn only as a
canvas overlay with no shape of its own -- is synthesized into a real
TEXT entity here too, at the same instance-bbox-centre-plus-offset
position the canvas computes, on Setup > Technology Properties...'s
Ref-Des layer, using REFDES_EXPORT_HEIGHT_DBU (SRef.label_height is a
fixed on-screen PIXEL size, independent of zoom, so there's no real
physical height to carry over for it).

Format conventions:
  * Coordinates: millimetres, converted from hlayout's 1-DBU-per-micron
    grid. (R12 predates the $INSUNITS header variable, so the unit is a
    convention rather than something the file states -- worth knowing on
    import into a tool that assumes a different default unit.)
  * Polygon / Rectangle / Path: a closed POLYLINE per merged region (see
    above) outlining its point ring.
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

from ..core.errors import CellError, GeometryError
from ..core.layers import NUM_LAYERS
from ..core.library import Library, _transform_bbox
from ..core.maskops import _close_holes, _require_shapely, layer_to_shapely_flattened
from ..core.shapes import Text, expand_arefs_to_srefs
from ..core.units import DBU_MM
from .techfile import load_refdes_annotation_layer

_MAX_DEPTH = 64

#: Text height (DBU) used for a synthesized (floating, un-baked) ref-des
#: TEXT entity -- see module docstring's "Reference designators" note.
REFDES_EXPORT_HEIGHT_DBU = 500  # 0.5 mm

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


def _merged_layer_rings(library: Library, cell, layer_n: int) -> List[List[Tuple[float, float]]]:
    """Every Polygon/Rectangle/Path on ``layer_n`` anywhere in ``cell``'s
    Sref/Aref hierarchy, stroked (for a Path) and unioned into one clean
    set of point rings via the same shapely pipeline Mask Operations
    uses -- see module docstring. Each returned ring is a closed point
    list (float DBU), holes already bridged in. Empty if the layer has
    no such geometry."""
    geom = layer_to_shapely_flattened(library, cell, layer_n)
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        candidates = [geom]
    elif geom.geom_type == "MultiPolygon":
        candidates = list(geom.geoms)
    elif geom.geom_type == "GeometryCollection":
        candidates = []
        for g in geom.geoms:
            if g.geom_type == "Polygon":
                candidates.append(g)
            elif g.geom_type == "MultiPolygon":
                candidates.extend(g.geoms)
            # Points/LineStrings in the collection (degenerate touches/
            # slivers) carry no area -- skip them, same as
            # maskops.shapely_to_polygons() does for a GDS result.
    else:
        candidates = []

    rings = []
    for poly in candidates:
        if poly.is_empty or poly.area == 0:
            continue
        ring = _close_holes(poly)
        if len(ring) >= 3:
            rings.append([(float(x), float(y)) for x, y in ring])
    return rings


def _flatten_texts_and_refdes(library: Library, cell_name: str):
    """Walk the Sref/Aref hierarchy starting at ``cell_name``, applying
    each instance's transform, and bucket by layer number:
      - every Text shape already drawn as real geometry (this also
        covers a part's own baked ref-des, if any);
      - a synthesized Text for every SRef.label that ISN'T baked -- see
        module docstring's "Reference designators" note.
    Returns {layer_number: [(x, y, text, height_dbu, rotation_deg), ...]}.
    Invisible layers are skipped entirely, same as the on-screen view.
    Text rotation under a mirrored ancestor is a best-effort composition
    (mirroring flips the sense of further rotation); plain nested
    rotation/translation/magnification is exact."""
    refdes_layer = load_refdes_annotation_layer(library)
    texts: Dict[int, List[Tuple[float, float, str, float, float]]] = {}

    def walk(name, transform, rotation_deg, mirrored, scale, depth):
        if depth > _MAX_DEPTH or name not in library.cells:
            return
        cell = library.cells[name]
        for shape in cell.shapes:
            if not isinstance(shape, Text):
                continue
            layer = library.layers[shape.layer]
            if not layer.visible:
                continue
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

            if (ref.label and refdes_layer is not None and refdes_layer >= 0
                    and library.layers[refdes_layer].visible):
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
                    ax, ay = transform((cx + lox, cy + loy))
                    texts.setdefault(refdes_layer, []).append(
                        (ax, ay, ref.label, REFDES_EXPORT_HEIGHT_DBU * scale, rotation_deg)
                    )

            child_rotation = rotation_deg + (-ref.rotation if mirrored else ref.rotation)
            walk(ref.cell_name, child_transform, child_rotation % 360,
                 mirrored ^ xr, scale * mag, depth + 1)

    walk(cell_name, lambda p: p, 0.0, False, 1.0, 0)
    return texts


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
    data. Every layer's Polygon/Rectangle/Path shapes are merged first
    (see module docstring) -- requires the shapely package.

    ``layers``, if given, restricts export to that subset of layer
    numbers (still only ones that are visible and have data); the
    default (None) exports every visible layer with data.

    Returns (written_layers, entity_count):
      written_layers: [layer_number, ...], in ascending order
      entity_count: total number of DXF entities written
    """
    if cell_name not in library.cells:
        raise ValueError(f"no such cell {cell_name!r}")
    try:
        _require_shapely()
    except GeometryError as e:
        raise GeometryError(
            "DXF export merges each layer's shapes with the shapely package, "
            "which isn't installed -- run: pip install shapely"
        ) from e

    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    cell = library.get_cell(cell_name)
    candidate_layers = (
        [n for n in layers if 0 <= n < NUM_LAYERS] if layers is not None
        else list(range(NUM_LAYERS))
    )
    candidate_layers = [n for n in candidate_layers if library.layers[n].visible]

    poly_rings: Dict[int, List[List[Tuple[float, float]]]] = {}
    for n in candidate_layers:
        rings = _merged_layer_rings(library, cell, n)
        if rings:
            poly_rings[n] = rings

    texts = _flatten_texts_and_refdes(library, cell_name)
    if layers is not None:
        texts = {n: t for n, t in texts.items() if n in layers}

    indices = sorted(set(poly_rings) | set(texts))

    written_layers = []
    entity_count = 0
    layer_lines = []
    entity_lines = []

    for n in indices:
        layer = library.layers[n]
        rings = poly_rings.get(n, [])
        t_list = texts.get(n, [])
        if not rings and not t_list:
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

        for pts in rings:
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
