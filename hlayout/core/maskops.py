"""Layer-based mask operations: OVERSIZE, UNDERSIZE, AND, OR.

Similar to the "resize"/boolean layer operations found in GDSII layout
and CAM systems (e.g. Cadence's DRC/LVS "Size"/"AND"/"OR" deck
operators): grow or shrink everything on one layer by a fixed amount,
or combine two layers with a boolean intersection/union, writing the
result as new polygon shapes on a (usually different) target layer.

Geometry is done with the ``shapely`` package -- pip install shapely
(also listed in requirements.txt). This module raises a clear
``GeometryError`` if shapely isn't installed, rather than failing to
import at all, so the rest of the app still works without it.

Paths have no filled area of their own (they're a centreline + width),
so per the tool's own spec ("paths would be converted to polygons")
every Path on an input layer is first converted to its stroked-outline
polygon (a shapely buffer of the centreline by half the path's width,
honouring its end-cap style) before any sizing or boolean op runs.
Rectangle and Polygon shapes are already filled areas and convert
directly.

Boolean-op/oversize results have no notion of a "hole" in this tool's
own Polygon/Rectangle shapes (nor does GDSII's boundary record), so a
shapely polygon with interior rings (e.g. ANDing a large fill against
a layer with a hole cut in it) is closed up into a single point list
per the standard layout-tool technique: bridge each hole to the outer
boundary with a zero-width slit.

A layer's geometry is gathered with layer_to_shapely_flattened(),
which descends into every placed cell instance (Sref), recursively,
folding in each instance's own rotation/magnification/mirror/origin --
not just the calling cell's own directly-owned shapes. This matters in
practice: most real content in a design ends up placed via Sref rather
than drawn straight into the top cell (e.g. every resistor built by
the Thick-Film Resistor Designer / Session.generate_resistor() is its
own device cell placed by a single Sref) -- a mask op that only looked
at the calling cell's own shapes would silently see nothing to
operate on for a design built that way.
"""

import math
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from .errors import GeometryError
from .shapes import (
    MAX_PATH_POINTS,
    MAX_POLY_POINTS,
    Path,
    PathCap,
    Polygon,
    Rectangle,
    expand_arefs_to_srefs,
)

MASK_OPS = ("OVERSIZE", "UNDERSIZE", "AND", "OR", "NOT", "XOR")

#: Which mask ops take a second layer (layer_b) instead of an amount --
#: the same OVERSIZE/UNDERSIZE-vs-boolean split Tools > Mask
#: Operations... and MaskOpStep both key off of.
BOOLEAN_MASK_OPS = ("AND", "OR", "NOT", "XOR")

try:
    from shapely.geometry import LineString
    from shapely.geometry import Polygon as _ShapelyPolygon
    from shapely.geometry import box as _box
    from shapely.ops import transform as _shapely_transform
    from shapely.ops import unary_union
    _SHAPELY_IMPORT_ERROR = None
except ImportError as _exc:  # pragma: no cover - exercised only without shapely installed
    LineString = None
    _ShapelyPolygon = None
    _box = None
    unary_union = None
    _shapely_transform = None
    _SHAPELY_IMPORT_ERROR = _exc

# Path end cap -> shapely LineString.buffer() cap_style. ROUND is
# shapely's own "round" cap; FLUSH (no extension past the endpoint) is
# shapely's "flat"; SQUARE (extends half the width past the endpoint)
# is shapely's "square".
_CAP_STYLE = {PathCap.ROUND: "round", PathCap.FLUSH: "flat", PathCap.SQUARE: "square"}


def _require_shapely():
    if _ShapelyPolygon is None:
        raise GeometryError(
            "layer-based mask operations (OVERSIZE/UNDERSIZE/AND/OR) require "
            "the 'shapely' package, which isn't installed -- run: "
            "pip install shapely"
        ) from _SHAPELY_IMPORT_ERROR


def shape_to_shapely(shape):
    """Convert one layout shape to a shapely (multi)polygon, or None for
    a shape with no maskable filled area (Text, SRef). A Path is
    stroked into its outline polygon first -- see module docstring."""
    _require_shapely()
    if isinstance(shape, Path):
        if len(shape.points) < 2:
            return None
        line = LineString(shape.points)
        cap_style = _CAP_STYLE.get(shape.cap, "flat")
        if shape.width <= 0:
            return None
        return line.buffer(shape.width / 2.0, cap_style=cap_style, join_style="mitre")
    if isinstance(shape, (Polygon, Rectangle)):
        return _ShapelyPolygon(shape.points)
    return None


def layer_to_shapely(shapes):
    """Union every shape in `shapes` (the caller has already filtered
    these to one layer) into a single shapely geometry. Returns an
    empty shapely Polygon if there's nothing to union."""
    _require_shapely()
    geoms = []
    for s in shapes:
        g = shape_to_shapely(s)
        if g is not None and not g.is_empty:
            geoms.append(g)
    if not geoms:
        return _ShapelyPolygon()
    return unary_union(geoms)


def _sref_point_transform(ref) -> Callable[[Tuple[float, float]], Tuple[float, float]]:
    """The same point transform hlayout.gui.canvas uses to draw a
    placed cell instance -- mirror, then scale by magnification, then
    rotate, then translate to the instance's origin."""
    theta = math.radians(ref.rotation)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    mag = ref.magnification
    ox, oy = ref.origin

    def transform(pt):
        x, y = pt
        if ref.x_reflection:
            y = -y
        x *= mag
        y *= mag
        return (x * cos_t - y * sin_t + ox, x * sin_t + y * cos_t + oy)

    return transform


def _compose_transforms(outer, inner):
    if outer is None:
        return inner
    return lambda pt: outer(inner(pt))


def _transform_geom(geom, point_transform):
    """Apply a plain (x, y) -> (x, y) point transform to every
    coordinate of a shapely geometry -- used to fold an Sref's
    placement (rotation/magnification/mirror/origin) into a child
    cell's geometry when flattening for a mask op. shapely.ops.transform
    accepts exactly this signature via a small (x, y) wrapper, so a
    pure Python transform composes correctly regardless of how deep the
    Sref nesting is, with no matrix bookkeeping needed here."""
    _require_shapely()
    return _shapely_transform(lambda x, y, z=None: point_transform((x, y)), geom)


def layer_geoms_flattened(library, cell, layer, point_transform=None, _depth=0,
                          datatype=None) -> list:
    """Return the individual (NOT unioned) shapely geometries for every
    shape on `layer` in `cell`, descending into every placed cell
    instance (Sref), recursively, and applying each instance's own
    rotation/magnification/mirror/origin -- so a layer's geometry that
    lives inside a placed sub-cell (e.g. a resistor built by
    Session.generate_resistor(), which places its ink/pads/glaze in
    their own device cell referenced by a single Sref rather than
    directly in the calling cell -- see the Thick-Film Resistor
    Designer) is still picked up by a mask op or DRC check run against
    the cell it was placed into. Guards against runaway/cyclic
    hierarchies the same way hlayout.gui.canvas's own recursive draw
    does.

    Kept separate (not unioned) rather than folded straight into
    layer_to_shapely_flattened() below because DRC spacing/area checks
    need to reason about each individual (post-union, per-connected-
    component -- see hlayout.core.drc) shape rather than one merged
    blob."""
    _require_shapely()
    geoms = []
    if _depth > 64:
        return geoms
    for shape in cell.shapes:
        if shape.layer != layer:
            continue
        if datatype is not None and getattr(shape, "datatype", 0) != datatype:
            continue
        g = shape_to_shapely(shape)
        if g is None or g.is_empty:
            continue
        if point_transform is not None:
            g = _transform_geom(g, point_transform)
        geoms.append(g)
    for ref in cell.srefs + expand_arefs_to_srefs(cell.arefs):
        if ref.cell_name not in library.cells:
            continue
        child = library.cells[ref.cell_name]
        combined = _compose_transforms(point_transform, _sref_point_transform(ref))
        geoms.extend(layer_geoms_flattened(library, child, layer, combined,
                                            _depth + 1, datatype=datatype))
    return geoms


def layer_to_shapely_flattened(library, cell, layer, point_transform=None, datatype=None):
    """Union of layer_geoms_flattened() -- see that function for what
    "flattened" means here. Returns an empty shapely Polygon if there's
    nothing on this layer anywhere in the cell's hierarchy.
    ``datatype``, if given, restricts to shapes with that GDS datatype."""
    _require_shapely()
    geoms = layer_geoms_flattened(library, cell, layer, point_transform, datatype=datatype)
    if not geoms:
        return _ShapelyPolygon()
    return unary_union(geoms)


def size_geometry(geom, amount: float):
    """Grow (amount > 0) or shrink (amount < 0) geometry by `amount`
    DBU. Mitred joins keep rectilinear corners sharp -- the behaviour
    expected of an OVERSIZE/UNDERSIZE ("resize") operation on
    Manhattan mask geometry, matching e.g. Cadence's GDSII resize."""
    _require_shapely()
    if geom.is_empty or amount == 0:
        return geom
    return geom.buffer(amount, join_style="mitre", mitre_limit=5.0)


def boolean_op(geom_a, geom_b, op: str):
    """AND (intersection), OR (union), NOT (difference: layer_a minus
    layer_b), or XOR (symmetric difference) of two shapely geometries --
    the standard four-operator boolean deck found in a GDSII CAM
    system."""
    _require_shapely()
    op = op.upper()
    if op == "AND":
        return geom_a.intersection(geom_b)
    if op == "OR":
        return geom_a.union(geom_b)
    if op == "NOT":
        return geom_a.difference(geom_b)
    if op == "XOR":
        return geom_a.symmetric_difference(geom_b)
    raise GeometryError(f"unknown boolean mask op {op!r}; expected one of {', '.join(MASK_OPS[2:])}")


def hidden_line_top_view(library, cell, layer_numbers, flatten: bool = True):
    """A top-down, hidden-line-removal pictorial view of the stack --
    the classic CAD/drafting sense of the term (as in AutoCAD's HIDE):
    the visible OUTLINE edges of the design as seen from directly
    above, with the portion of a lower layer's edge that's buried under
    a higher layer's filled footprint actually removed, not just drawn
    underneath it.

    This is deliberately edge-based rather than solid-fill-based:
    compositing FILLED regions front-to-back and merging the visible
    remainders onto one output layer is a no-op -- their union is
    always exactly the union of the original layers' own footprints,
    regardless of stacking order (occlusion only changes which
    individual piece contributed a given point, information a single
    undifferentiated output layer throws away immediately). Working
    with each layer's boundary LINES instead preserves the one thing
    solid compositing loses: a lower layer's edge that would be
    invisible in a real top-down view (fully enclosed by something
    above it) is genuinely absent from the result, giving a true
    hidden-line drawing rather than a plain multi-layer union.

    ``layer_numbers`` are plain layer numbers, in the same "layer
    number order is print/fire order" convention
    hlayout.io.freecadio's 3D export uses -- the HIGHEST number is
    printed/fired LAST and so sits physically on TOP. Layers are
    processed from the top down: each layer's own boundary is clipped
    to the part not already covered by a higher layer's filled area
    before being added to the result.

    Returns a single shapely (Multi)LineString (empty if nothing is on
    any of the given layers), ready for shapely_lines_to_paths()."""
    _require_shapely()

    def gather(layer):
        if flatten:
            return layer_to_shapely_flattened(library, cell, layer)
        return layer_to_shapely(cell.shapes_on_layer(layer))

    covered = _ShapelyPolygon()  # nothing covered yet, working top-down
    visible_edges = []
    for n in sorted(set(layer_numbers), reverse=True):  # highest (top) first
        geom = gather(n)
        if geom.is_empty:
            continue
        boundary = geom.boundary
        visible_here = boundary.difference(covered) if not covered.is_empty else boundary
        if not visible_here.is_empty:
            visible_edges.append(visible_here)
        covered = geom if covered.is_empty else covered.union(geom)

    if not visible_edges:
        return LineString()
    return unary_union(visible_edges)


def _signed_area(ring) -> float:
    """Shoelace signed area -- positive for one winding direction,
    negative for the other. Used only to compare windings, not for its
    absolute value."""
    total = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _close_holes(poly) -> List[Tuple[int, int]]:
    """Return one closed point ring for a shapely Polygon, bridging any
    interior rings (holes) into the exterior boundary with a
    zero-width slit to the nearest point on the ring being bridged --
    the standard trick for representing a holed polygon in a shape
    format (like this tool's, and GDSII's boundary record) that has no
    native hole concept. A hole ring only nets out of the enclosed area
    correctly (via the shoelace formula any consumer -- e.g. re-deriving
    area, or a downstream boolean op -- might apply) if it's wound
    opposite to the exterior; shapely doesn't guarantee that for a
    Polygon built directly from coordinates (only for GEOS operation
    results), so the winding is checked and flipped here if needed."""
    ring = [(x, y) for x, y in list(poly.exterior.coords)[:-1]]
    ext_positive = _signed_area(ring) >= 0
    for interior in poly.interiors:
        hole = [(x, y) for x, y in list(interior.coords)[:-1]]
        if not hole:
            continue
        if (_signed_area(hole) >= 0) == ext_positive:
            hole = hole[::-1]
        best = None
        for i, rp in enumerate(ring):
            for j, hp in enumerate(hole):
                d = (rp[0] - hp[0]) ** 2 + (rp[1] - hp[1]) ** 2
                if best is None or d < best[0]:
                    best = (d, i, j)
        _, ri, hj = best
        hole_reordered = hole[hj:] + hole[:hj]
        bridge = [ring[ri]] + hole_reordered + [hole_reordered[0], ring[ri]]
        ring = ring[:ri] + bridge + ring[ri + 1:]
    return [(int(round(x)), int(round(y))) for x, y in ring]


def fracture_polygons(geom, max_points: int = MAX_POLY_POINTS, _depth: int = 0):
    """Break a shapely Polygon/MultiPolygon (possibly with many holes,
    or a single very high-vertex boundary) into a list of HOLE-FREE
    shapely Polygons, each with at most ``max_points`` exterior
    vertices, by recursively quartering its bounding box and
    intersecting.

    This is the standard way to get a big/holed boolean result -- a
    ground pour, or a dielectric fill opened up around every via --
    past ``shapely_to_polygons()``'s hard vertex limit (and GDSII's
    lack of a hole primitive) without simplifying the geometry: every
    output edge lies exactly on an input edge or a cut line.
    """
    _require_shapely()
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type in ("MultiPolygon", "GeometryCollection"):
        out = []
        for g in geom.geoms:
            if g.geom_type in ("Polygon", "MultiPolygon", "GeometryCollection"):
                out.extend(fracture_polygons(g, max_points, _depth))
        return out
    if geom.geom_type != "Polygon" or geom.area == 0:
        return []
    # hole-free and small enough -> take it as-is
    if not geom.interiors and len(geom.exterior.coords) - 1 <= max_points:
        return [geom]
    if _depth >= 24:
        # pathological input -- fall back to a hole-bridged single ring
        return [geom]
    x0, y0, x1, y1 = geom.bounds
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    out = []
    for qx0, qy0, qx1, qy1 in ((x0, y0, mx, my), (mx, y0, x1, my),
                                (x0, my, mx, y1), (mx, my, x1, y1)):
        piece = geom.intersection(_box(qx0, qy0, qx1, qy1))
        if not piece.is_empty:
            out.extend(fracture_polygons(piece, max_points, _depth + 1))
    return out


def shapely_to_polygons(geom, layer: int, datatype: int = 0) -> List[Polygon]:
    """Flatten a (possibly multi-, possibly holed, possibly empty)
    shapely geometry into a list of this tool's own Polygon shapes on
    `layer`. Raises GeometryError if any single resulting polygon would
    exceed the 200-vertex limit shared by every Polygon/Path in this
    tool -- simplify the input geometry or shrink the operation's area
    rather than silently truncating or auto-simplifying a mask result."""
    _require_shapely()
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
            # slivers from a boolean op) carry no area -- skip them.
    else:
        candidates = []

    polygons = []
    for poly in candidates:
        if poly.is_empty or poly.area == 0:
            continue
        points = _close_holes(poly)
        if len(points) < 3:
            continue
        if len(points) > MAX_POLY_POINTS:
            raise GeometryError(
                f"mask op result has {len(points)} vertices in one polygon, "
                f"exceeding the {MAX_POLY_POINTS}-point limit -- simplify the "
                "input geometry or run the operation over a smaller area"
            )
        polygons.append(Polygon(layer=layer, points=points, datatype=datatype))
    return polygons


def shapely_lines_to_paths(geom, layer: int, width: int, datatype: int = 0) -> List[Path]:
    """Flatten a (possibly multi-, possibly empty) shapely line
    geometry -- e.g. hidden_line_top_view()'s result -- into a list of
    this tool's own Path shapes on `layer`, each `width` DBU wide with
    a round cap. A line with more than the 200-point Path limit is
    split into consecutive pieces (sharing an endpoint, so they still
    read as one continuous line) rather than raising, since a hidden-
    line silhouette's outline length isn't under the caller's control
    the way a hand-drawn path's is."""
    _require_shapely()
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        candidates = [geom]
    elif geom.geom_type == "MultiLineString":
        candidates = list(geom.geoms)
    elif geom.geom_type == "GeometryCollection":
        candidates = []
        for g in geom.geoms:
            if g.geom_type == "LineString":
                candidates.append(g)
            elif g.geom_type == "MultiLineString":
                candidates.extend(g.geoms)
            # Points in the collection (degenerate touches) carry no
            # length -- skip them.
    else:
        candidates = []

    paths = []
    for line in candidates:
        if line.is_empty or line.length == 0:
            continue
        coords = [(round(x), round(y)) for x, y in line.coords]
        # dedupe consecutive duplicate points (can happen after a
        # boolean op) -- a Path needs at least 2 distinct points.
        pts = [coords[0]]
        for p in coords[1:]:
            if p != pts[-1]:
                pts.append(p)
        if len(pts) < 2:
            continue
        for i in range(0, len(pts) - 1, MAX_PATH_POINTS - 1):
            chunk = pts[i:i + MAX_PATH_POINTS]
            if len(chunk) < 2:
                continue
            paths.append(Path(layer=layer, points=chunk, width=width,
                               cap=PathCap.ROUND, datatype=datatype))
    return paths


@dataclass
class MaskOpStep:
    """One step in a mask-op batch/recipe -- a saved, ordered series of
    mask operations that can be run together in one action, the same
    way hlayout.core.drc.DrcRule is one saved rule in a DRC rule set.
    See hlayout.scripting.api.Session.run_mask_op_batch(), which runs a
    list of these in order, feeding one step's result_layer into a
    later step's layer_a/layer_b exactly as if each had been run by
    hand through Tools > Mask Operations... in sequence.

    Mirrors the arguments of Session.mask_op() (see that method for
    what each field means) rather than introducing new semantics of its
    own -- a MaskOpStep is just a saved, named mask_op() call."""

    kind: str
    layer_a: int
    result_layer: int
    layer_b: Optional[int] = None
    amount: float = 0.0  # DBU; OVERSIZE/UNDERSIZE only
    name: str = ""
    flatten: bool = True
    simplify: float = 0.0  # DBU; 0 = off

    def __post_init__(self):
        if self.kind not in MASK_OPS:
            raise GeometryError(
                f"unknown mask op {self.kind!r}; expected one of {', '.join(MASK_OPS)}"
            )
        if self.kind in BOOLEAN_MASK_OPS and self.layer_b is None:
            raise GeometryError(f"mask op step {self.kind} requires layer_b")
        if self.kind in ("OVERSIZE", "UNDERSIZE") and self.amount < 0:
            raise GeometryError(
                "amount must be non-negative -- OVERSIZE grows, UNDERSIZE "
                "shrinks by amount"
            )

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "layer_a": self.layer_a, "result_layer": self.result_layer,
            "layer_b": self.layer_b, "amount": self.amount, "name": self.name,
            "flatten": self.flatten, "simplify": self.simplify,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MaskOpStep":
        return cls(
            kind=data["kind"], layer_a=data["layer_a"], result_layer=data["result_layer"],
            layer_b=data.get("layer_b"), amount=data.get("amount", 0.0),
            name=data.get("name", ""), flatten=data.get("flatten", True),
            simplify=data.get("simplify", 0.0),
        )

    @property
    def label(self) -> str:
        if self.kind in BOOLEAN_MASK_OPS:
            base = f"{self.kind}(layer {self.layer_a}, layer {self.layer_b}) -> layer {self.result_layer}"
        else:
            base = f"{self.kind}(layer {self.layer_a}, {self.amount}) -> layer {self.result_layer}"
        return f"{self.name}: {base}" if self.name else base
