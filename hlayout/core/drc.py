"""Design Rule Checking (DRC): single-layer and between-layer geometric
checks -- minimum width, minimum spacing, minimum area, and minimum
enclosure -- run against the actual layout geometry.

Built on the same shapely geometry engine as hlayout.core.maskops (see
that module's docstring for why shapely, and for the flattening
helpers reused here): a rule is checked by turning the relevant
layer(s)' geometry into shapely shapes -- descending into every placed
cell instance (Sref) the same way a mask op does, so a design built
mostly out of placed cells (resistors, vias, ...) is actually checked
rather than silently skipped -- then applying the geometric test for
that rule kind. A violation is reported with a small polygon marking
exactly where the problem is, so it can be drawn onto a "DRC error"
layer in the actual design for visual review, in addition to (or
instead of) a plain text report.

Rule kinds
----------
Single-layer:
    MIN_WIDTH(layer, value)      -- every feature on `layer` must be at
                                     least `value` DBU wide everywhere.
                                     Checked via a morphological opening
                                     (erode by value/2, then dilate back
                                     by value/2); whatever the opening
                                     removes and doesn't restore is
                                     narrower than `value` and reported
                                     as the violation region.
    MIN_AREA(layer, value)       -- every electrically-connected shape
                                     (touching same-layer shapes count
                                     as one) must have area >= `value`
                                     DBU^2.
    MIN_SPACE(layer, value)      -- every pair of DISTINCT (non-
                                     touching) same-layer shapes must be
                                     at least `value` DBU apart. This
                                     does not catch a spacing violation
                                     between two arms of the SAME
                                     connected shape (a narrow notch) --
                                     that is a separate, harder check
                                     (see module note below) not
                                     implemented here.

Between-layer:
    MIN_SPACE_BETWEEN(layer_a, layer_b, value)
                                  -- every pair of non-overlapping
                                     shapes, one on layer_a and one on
                                     layer_b, must be at least `value`
                                     DBU apart. Overlapping shapes
                                     (distance 0) are not reported --
                                     that's a different kind of rule
                                     (an overlap/no-overlap check),
                                     which this tool doesn't need for
                                     its supported process types.
    MIN_ENCLOSURE(layer_a, layer_b, value)
                                  -- every shape on layer_b (the
                                     "inner" layer, e.g. a via) must be
                                     enclosed by layer_a (the "outer"
                                     layer, e.g. a metal pour) by at
                                     least `value` DBU on all sides.
                                     Checked by growing each inner shape
                                     outward by `value` and confirming
                                     the grown region is fully covered
                                     by layer_a; whatever sticks out is
                                     reported as the violation region.

Substrate edge:
    EDGE_CLEARANCE(layer_a, substrate_layer, value)
                                  -- every shape on layer_a (a conductor)
                                     must lie on the substrate and at
                                     least `value` DBU in from its edge,
                                     so it survives scribing/snapping
                                     and the print registration. The
                                     substrate is the area of
                                     substrate_layer's shapes; a closed
                                     outline drawn as a path counts as
                                     the area it encloses (measured to
                                     the path's centre line). The part of
                                     a shape that is too close, or off
                                     the substrate, is the marker.

Wire bonds (chip and wire):
    BOND_MIN_LENGTH(bond_layer, value)
                                  -- every wire bond (a Path on the bond
                                     layer, Setup > Technology Properties (Wire Bonds tab))
                                     must span at least `value` DBU,
                                     measured end to end -- the shortest
                                     loop a bonder can form.
    BOND_MAX_LENGTH(bond_layer, value)
                                  -- every wire bond must span at most
                                     `value` DBU -- the longest
                                     unsupported span before the wire
                                     sags or sweeps into a neighbour.
    BOND_MAX_ANGLE(bond_layer, die_pad_layer, value)
                                  -- at every end that lands on a bare-
                                     die pad (a shape on die_pad_layer
                                     inside a placed die), the wire must
                                     leave within `value` DEGREES of the
                                     die-edge normal: the outward normal
                                     of the die side nearest that pad.
                                     A die is a placed cell instance
                                     with two or more shapes on
                                     die_pad_layer. A corner pad (as
                                     near one side as the other, within
                                     half its own size) may leave by
                                     either side: the smaller angle
                                     counts. An end whose other
                                     end lies inside the same part's
                                     outline (a header or package the
                                     substrate sits in, bonded inward)
                                     is not a die end and isn't checked.
                                     `value` is in degrees, not DBU.
    Each bond violation is reported with the measured value in its
    message and a marker along the whole wire.

Note on self-notch spacing: detecting a too-narrow notch WITHIN a
single connected shape (as opposed to the space BETWEEN two distinct
shapes, which MIN_SPACE covers) is a materially different and more
expensive check -- it requires reasoning about the background region
near the shape's own boundary rather than a simple pairwise distance --
and isn't implemented here. MIN_WIDTH, run on the same layer, already
catches many of the same design mistakes from the material side.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional

from .errors import GeometryError
from .maskops import (_ShapelyPolygon, _compose_transforms, _require_shapely, _sref_point_transform,
                      _transform_geom, layer_geoms_flattened, shape_to_shapely, shapely_to_polygons,
                      unary_union)
from .shapes import Path, expand_arefs_to_srefs
from .units import DBU_MM, mm_to_dbu

#: Wire-bond rule kinds (checked on the wires themselves, not on shapes).
BOND_KINDS = ("BOND_MIN_LENGTH", "BOND_MAX_LENGTH", "BOND_MAX_ANGLE")

DRC_RULE_KINDS = ("MIN_WIDTH", "MIN_AREA", "MIN_SPACE", "MIN_SPACE_BETWEEN", "MIN_ENCLOSURE",
                  "EDGE_CLEARANCE") + BOND_KINDS

#: Which rule kinds take a second layer.
TWO_LAYER_KINDS = ("MIN_SPACE_BETWEEN", "MIN_ENCLOSURE", "EDGE_CLEARANCE", "BOND_MAX_ANGLE")

#: Rule kinds whose value is an angle in degrees rather than a DBU length.
ANGLE_KINDS = ("BOND_MAX_ANGLE",)

#: Rule kinds that are upper limits ("<=") rather than minimums (">=").
MAX_KINDS = ("BOND_MAX_LENGTH", "BOND_MAX_ANGLE")

#: Half-width (DBU) of the marker drawn along a violating wire bond.
_BOND_MARKER_HALF_WIDTH = mm_to_dbu(0.05)

#: A part whose outline covers more than this fraction of the checked
#: cell's bounding box is an enclosing header/package, never a die --
#: the same test the Freerouting export uses for die keep-outs.
_ENCLOSING_FRACTION = 0.4

#: Half-width (DBU) used to turn a spacing violation's two nearest points
#: into a visible marker polygon (a thin rectangle along the gap, or a
#: dot when the two points coincide) rather than a zero-area line. The
#: marker is 0.1 mm wide overall, i.e. twice this half-width.
_SPACE_MARKER_HALF_WIDTH = mm_to_dbu(0.1) / 2.0


@dataclass
class DrcRule:
    kind: str
    layer_a: int
    value: float
    layer_b: Optional[int] = None
    name: str = ""
    #: Hex RGB (e.g. "#e6194b") the GUI uses to tell this rule's
    #: violations apart from other rules' in the DRC window's rule/
    #: violation tables -- purely a display preference, never read by
    #: check_rule()/run_drc(). "" means "no colour picked yet"; the GUI
    #: assigns one from its default palette the first time a rule is
    #: created (see hlayout.gui.drc_window._next_default_color) rather
    #: than this module opining on what that palette should be.
    color: str = ""

    def __post_init__(self):
        if self.kind not in DRC_RULE_KINDS:
            raise GeometryError(
                f"unknown DRC rule kind {self.kind!r}; expected one of {', '.join(DRC_RULE_KINDS)}"
            )
        if self.kind in TWO_LAYER_KINDS and self.layer_b is None:
            raise GeometryError(f"DRC rule {self.kind} requires layer_b")
        if self.value <= 0:
            raise GeometryError("DRC rule value must be greater than zero")
        if self.kind in ANGLE_KINDS and self.value > 180:
            raise GeometryError(f"DRC rule {self.kind} value is an angle in degrees (0-180)")

    def to_dict(self) -> dict:
        return {"kind": self.kind, "layer_a": self.layer_a, "layer_b": self.layer_b,
                "value": self.value, "name": self.name, "color": self.color}

    @classmethod
    def from_dict(cls, data: dict) -> "DrcRule":
        return cls(kind=data["kind"], layer_a=data["layer_a"], value=data["value"],
                    layer_b=data.get("layer_b"), name=data.get("name", ""),
                    color=data.get("color", ""))

    @property
    def label(self) -> str:
        op = "<=" if self.kind in MAX_KINDS else ">="
        unit = " deg" if self.kind in ANGLE_KINDS else ""
        if self.kind in TWO_LAYER_KINDS:
            base = f"{self.kind}(layer {self.layer_a}, layer {self.layer_b}) {op} {self.value:g}{unit}"
        else:
            base = f"{self.kind}(layer {self.layer_a}) {op} {self.value:g}{unit}"
        return f"{self.name}: {base}" if self.name else base


@dataclass
class DrcViolation:
    rule: DrcRule
    cell_name: str
    points: List[tuple]
    message: str = field(default="")

    def __post_init__(self):
        if not self.message:
            self.message = self.rule.label

    def bbox(self):
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (min(xs), min(ys), max(xs), max(ys))


def _connected_components(geoms: list):
    """Union a list of individual shapely geometries, then split the
    result back into its distinct connected components (touching/
    overlapping input shapes merge into one component; separate ones
    stay separate) -- the natural unit for MIN_AREA/MIN_SPACE, since
    two shapes that already touch have zero space between them by
    definition, not a violation."""
    _require_shapely()
    if not geoms:
        return []
    merged = unary_union(geoms)
    if merged.is_empty:
        return []
    if merged.geom_type == "Polygon":
        return [merged]
    if merged.geom_type == "MultiPolygon":
        return list(merged.geoms)
    return []


def _marker_from_points(a, b):
    """A thin rectangle along the segment from point a to point b,
    wide enough to be a visible marker regardless of how close a and b
    are (down to and including a == b, e.g. an enclosure check whose
    "sticking out" region happens to degenerate to a point)."""
    from shapely.geometry import LineString, Point
    if a == b:
        geom = Point(a).buffer(_SPACE_MARKER_HALF_WIDTH)
    else:
        geom = LineString([a, b]).buffer(_SPACE_MARKER_HALF_WIDTH, cap_style="round")
    return geom


#: DBU: a MIN_WIDTH leftover thinner than twice this is numerical noise
MIN_WIDTH_TOLERANCE = 1.0


def _check_min_width(rule: DrcRule, geoms: list) -> list:
    _require_shapely()
    if not geoms:
        return []
    merged = unary_union(geoms)
    if merged.is_empty:
        return []
    # a track drawn exactly AT the limit must pass: erode by a hair less
    # than half the width, or it erodes to nothing and is all "violation"
    half = max(rule.value / 2.0 - MIN_WIDTH_TOLERANCE, 0.0)
    opened = merged.buffer(-half, join_style="mitre", mitre_limit=5.0)
    if not opened.is_empty:
        opened = opened.buffer(half, join_style="mitre", mitre_limit=5.0)
    violation = merged.difference(opened) if not opened.is_empty else merged
    if not violation.is_empty:
        # drop numerical slivers: the curve approximation of a round cap
        # or arc, or a track drawn exactly at the limit, leaves hair-thin
        # leftovers that aren't a real narrowing
        tol = MIN_WIDTH_TOLERANCE
        violation = violation.buffer(-tol, join_style="mitre").buffer(tol, join_style="mitre")
    return [violation] if not violation.is_empty and violation.area > 0 else []


def _check_min_area(rule: DrcRule, geoms: list) -> list:
    violations = []
    for comp in _connected_components(geoms):
        if comp.area < rule.value:
            violations.append(comp)
    return violations


def _check_min_space(rule: DrcRule, geoms: list) -> list:
    from shapely.ops import nearest_points
    components = _connected_components(geoms)
    violations = []
    for i in range(len(components)):
        for j in range(i + 1, len(components)):
            d = components[i].distance(components[j])
            if 0 < d < rule.value:
                pa, pb = nearest_points(components[i], components[j])
                violations.append(_marker_from_points((pa.x, pa.y), (pb.x, pb.y)))
    return violations


def _check_min_space_between(rule: DrcRule, geoms_a: list, geoms_b: list) -> list:
    from shapely.ops import nearest_points
    comps_a = _connected_components(geoms_a)
    comps_b = _connected_components(geoms_b)
    violations = []
    for ca in comps_a:
        for cb in comps_b:
            d = ca.distance(cb)
            if 0 < d < rule.value:
                pa, pb = nearest_points(ca, cb)
                violations.append(_marker_from_points((pa.x, pa.y), (pb.x, pb.y)))
    return violations


def _check_min_enclosure(rule: DrcRule, geoms_outer: list, geoms_inner: list) -> list:
    _require_shapely()
    outer_union = unary_union(geoms_outer) if geoms_outer else _ShapelyPolygon()
    violations = []
    for inner in _connected_components(geoms_inner):
        required = inner.buffer(rule.value, join_style="mitre", mitre_limit=5.0)
        missing = required.difference(outer_union) if not outer_union.is_empty else required
        if not missing.is_empty and missing.area > 0:
            violations.append(missing)
    return violations


def _bond_wires(library, cell, layer, point_transform=None, _depth=0) -> list:
    """Every wire bond on ``layer`` in ``cell`` and its placed instances:
    [(end_a, end_b)] in the checked cell's coordinates. A wire bond is a
    Path with two or more points; its span is end to end."""
    out = []
    if _depth > 64:
        return out
    for sh in cell.shapes:
        if isinstance(sh, Path) and sh.layer == layer and len(sh.points) >= 2:
            a, b = tuple(sh.points[0]), tuple(sh.points[-1])
            if point_transform is not None:
                a, b = point_transform(a), point_transform(b)
            out.append((a, b))
    for ref in cell.srefs + expand_arefs_to_srefs(cell.arefs):
        child = library.cells.get(ref.cell_name)
        if child is None:
            continue
        out.extend(_bond_wires(library, child, layer,
                               _compose_transforms(point_transform, _sref_point_transform(ref)),
                               _depth + 1))
    return out


def _collect_geoms(library, name, transform, acc, depth=0):
    """(layer, shapely geometry) of every shape under placed cell ``name``."""
    c = library.cells.get(name)
    if c is None or depth > 64:
        return
    for sh in c.shapes:
        g = shape_to_shapely(sh)
        if g is None or g.is_empty:
            continue
        acc.append((sh.layer, _transform_geom(g, transform)))
    for ref in c.srefs + expand_arefs_to_srefs(c.arefs):
        _collect_geoms(library, ref.cell_name, _compose_transforms(transform, _sref_point_transform(ref)),
                       acc, depth + 1)


def _die_parts(library, cell, pad_layer, bond_layer=None) -> list:
    """The bare dice placed in ``cell``: [{"name", "bbox", "pads"}], one per
    placed instance (at any depth) with two or more shapes on ``pad_layer``,
    ``pads`` being those shapes. When instances nest (a die built from
    placed pin cells), the innermost instance that still holds two or
    more pads is the die; an instance holding dice further in (a sub-
    module) is not itself a die. A part covering most of the cell (an
    enclosing header) is left out. Wire bonds drawn inside the die part
    (on ``bond_layer``) and the numbered bond fingers it may carry (pin
    shapes off ``pad_layer``) aren't its body: they're left out of its
    bbox."""
    cb = library.cell_bbox(cell.name)
    cell_area = max((cb[2] - cb[0]) * (cb[3] - cb[1]), 1) if cb else 1
    found = []

    def walk(c, transform, depth):
        if depth > 32:
            return
        for ref in c.srefs + expand_arefs_to_srefs(c.arefs):
            child = library.cells.get(ref.cell_name)
            if child is None:
                continue
            tr = _compose_transforms(transform, _sref_point_transform(ref))
            before = len(found)
            walk(child, tr, depth + 1)
            acc = []
            _collect_geoms(library, ref.cell_name, tr, acc)
            pads = [g for lay, g in acc if lay == pad_layer]
            if len(pads) < 2:
                continue
            if len(found) > before:
                continue                       # the die (or dice) is a placed part further in
            fingers = []
            _finger_geoms(library, ref.cell_name, tr, pad_layer, fingers)

            body = [g for lay, g in acc if lay != bond_layer and not _same_as_any(g, fingers)]
            x0, y0, x1, y1 = unary_union(body or [g for _l, g in acc]).bounds
            if (x1 - x0) * (y1 - y0) > _ENCLOSING_FRACTION * cell_area:
                continue
            found.append({"name": getattr(ref, "label", "") or ref.cell_name,
                          "bbox": (x0, y0, x1, y1), "pads": pads})

    walk(cell, None, 0)
    return found


def _finger_geoms(library, name, transform, pad_layer, acc, depth=0):
    """Numbered (pin) shapes off ``pad_layer`` under placed cell ``name``
    -- the bond fingers a die part may carry."""
    c = library.cells.get(name)
    if c is None or depth > 64:
        return
    for sh in c.shapes:
        if getattr(sh, "pin", "") and sh.layer != pad_layer:
            g = shape_to_shapely(sh)
            if g is not None and not g.is_empty:
                acc.append(_transform_geom(g, transform))
    for ref in c.srefs + expand_arefs_to_srefs(c.arefs):
        _finger_geoms(library, ref.cell_name,
                      _compose_transforms(transform, _sref_point_transform(ref)), pad_layer, acc, depth + 1)


def _same_as_any(g, others) -> bool:
    return any(abs(g.area - o.area) < 1e-6 and g.equals_exact(o, 1e-6) for o in others)


def _outward_normals(bbox, pt, slack=0.0):
    """Unit outward normals of the side of ``bbox`` nearest ``pt`` and of
    any other side within ``slack`` of it -- both sides of a corner pad,
    which a wire may leave by either."""
    x0, y0, x1, y1 = bbox
    px, py = pt
    d = {(-1.0, 0.0): px - x0, (1.0, 0.0): x1 - px, (0.0, -1.0): py - y0, (0.0, 1.0): y1 - py}
    near = min(d.values())
    return [n for n, v in d.items() if v <= near + slack]


def _inside(bbox, pt):
    return bbox[0] <= pt[0] <= bbox[2] and bbox[1] <= pt[1] <= bbox[3]


def bond_departures(library, cell, bond_layer, pad_layer) -> list:
    """For every wire-bond end that lands on a bare-die pad: (wire, end,
    die name, angle in degrees from the die-edge normal). ``wire`` is the
    (end_a, end_b) pair, ``end`` 0 or 1."""
    from shapely.geometry import Point
    _require_shapely()
    dies = _die_parts(library, cell, pad_layer, bond_layer)
    out = []
    for wire in _bond_wires(library, cell, bond_layer):
        for k in (0, 1):
            at, other = wire[k], wire[1 - k]
            p = Point(at)
            for die in dies:
                if not _inside(die["bbox"], at) or _inside(die["bbox"], other):
                    continue
                # the smallest pad under the end: a die body drawn on the
                # pad layer too (it covers every pad) is never the pad
                x0, y0, x1, y1 = die["bbox"]
                body = 0.5 * (x1 - x0) * (y1 - y0)
                under = [g for g in die["pads"] if g.covers(p) and g.area < body]
                if not under:
                    continue
                pad = min(under, key=lambda g: g.area)
                c = pad.centroid
                px0, py0, px1, py1 = pad.bounds
                normals = _outward_normals(die["bbox"], (c.x, c.y),
                                           slack=max(px1 - px0, py1 - py0) / 2.0)
                dx, dy = other[0] - at[0], other[1] - at[1]
                ln = math.hypot(dx, dy)
                if ln == 0:
                    break
                ang = min(math.degrees(math.acos(max(-1.0, min(1.0, (dx * nx + dy * ny) / ln))))
                          for nx, ny in normals)
                out.append((wire, k, die["name"], ang))
                break
    return out


def _wire_marker(a, b):
    from shapely.geometry import LineString, Point
    if a == b:
        return Point(a).buffer(_BOND_MARKER_HALF_WIDTH)
    return LineString([a, b]).buffer(_BOND_MARKER_HALF_WIDTH, cap_style="round")


def _mm(v):
    return f"{v * DBU_MM:.3f} mm"


def _xy(p):
    return f"({p[0] * DBU_MM:.3f}, {p[1] * DBU_MM:.3f})"


def _check_bonds(library, cell, rule: DrcRule) -> list:
    """[(marker geometry, message)] for one wire-bond rule."""
    _require_shapely()
    out = []
    if rule.kind in ("BOND_MIN_LENGTH", "BOND_MAX_LENGTH"):
        for a, b in _bond_wires(library, cell, rule.layer_a):
            ln = math.hypot(b[0] - a[0], b[1] - a[1])
            if rule.kind == "BOND_MIN_LENGTH" and ln < rule.value - 0.5:
                msg = f"bond {_xy(a)}-{_xy(b)}: length {_mm(ln)} < min {_mm(rule.value)}"
            elif rule.kind == "BOND_MAX_LENGTH" and ln > rule.value + 0.5:
                msg = f"bond {_xy(a)}-{_xy(b)}: length {_mm(ln)} > max {_mm(rule.value)}"
            else:
                continue
            out.append((_wire_marker(a, b), msg))
    elif rule.kind == "BOND_MAX_ANGLE":
        for (a, b), k, die, ang in bond_departures(library, cell, rule.layer_a, rule.layer_b):
            if ang > rule.value + 1e-6:
                at = (a, b)[k]
                msg = (f"bond {_xy(a)}-{_xy(b)}: leaves {die} at {_xy(at)} {ang:.1f} deg "
                       f"off the die-edge normal > max {rule.value:g} deg")
                out.append((_wire_marker(a, b), msg))
    return out


def substrate_area(library, cell, layer, point_transform=None, _depth=0):
    """The substrate as one area: the union of ``layer``'s shapes in
    ``cell`` and its placed instances, where a closed outline path (first
    point = last, or three or more points) counts as the polygon its
    centre line encloses rather than as a thin stroked ring."""
    from shapely.geometry import Polygon as _P
    _require_shapely()
    geoms = []
    if _depth > 64:
        return geoms if _depth else _ShapelyPolygon()
    for sh in cell.shapes:
        if sh.layer != layer:
            continue
        if isinstance(sh, Path) and len(sh.points) >= 3:
            pts = [tuple(p) for p in sh.points]
            if point_transform is not None:
                pts = [point_transform(p) for p in pts]
            g = _P(pts).buffer(0)
        else:
            g = shape_to_shapely(sh)
            if g is None or g.is_empty:
                continue
            if point_transform is not None:
                g = _transform_geom(g, point_transform)
        if not g.is_empty:
            geoms.append(g)
    for ref in cell.srefs + expand_arefs_to_srefs(cell.arefs):
        child = library.cells.get(ref.cell_name)
        if child is not None:
            geoms.extend(substrate_area(library, child, layer,
                                        _compose_transforms(point_transform, _sref_point_transform(ref)),
                                        _depth + 1))
    if _depth:
        return geoms
    return unary_union(geoms) if geoms else _ShapelyPolygon()


def _check_edge_clearance(library, cell, rule: DrcRule) -> list:
    """[(marker geometry, message)] for EDGE_CLEARANCE."""
    _require_shapely()
    area = substrate_area(library, cell, rule.layer_b)
    if area.is_empty:
        raise GeometryError(f"EDGE_CLEARANCE: no substrate on layer {rule.layer_b} in {cell.name}")
    inner = area.buffer(-rule.value, join_style="mitre", mitre_limit=5.0)
    edge = area.boundary
    out = []
    for comp in _connected_components(layer_geoms_flattened(library, cell, rule.layer_a)):
        bad = comp.difference(inner) if not inner.is_empty else comp
        if bad.is_empty or bad.area <= 1.0:
            continue
        c = bad.representative_point()
        where = _xy((c.x, c.y))
        if not area.buffer(0.5).covers(comp):
            msg = f"conductor at {where} runs off the substrate (min {_mm(rule.value)} from the edge)"
        else:
            msg = (f"conductor at {where} {_mm(edge.distance(comp))} from the substrate edge "
                   f"< min {_mm(rule.value)}")
        out.append((bad, msg))
    return out


#: Max departure angle (deg) used when wire-bond rules are made from the
#: Wire Bond Rules settings -- the hybrid router's default.
DEFAULT_BOND_MAX_ANGLE = 45.0


def bond_rules_from_settings(bond_rules: dict, existing=(), angle: float = DEFAULT_BOND_MAX_ANGLE
                             ) -> List[DrcRule]:
    """DRC rules from the Wire Bond Rules settings (hlayout.io.techfile.
    load_bond_rules()): BOND_MIN_LENGTH and BOND_MAX_LENGTH on the bond
    layer, and a BOND_MAX_ANGLE of ``angle`` degrees per die-pad layer.
    A kind/layer pair already in ``existing`` (DrcRule list) is left out.
    Raises GeometryError when no bond layer is set."""
    layer = int(bond_rules.get("layer", -1))
    if layer < 0:
        raise GeometryError("no bond layer set: pick one in Setup > Technology Properties (Wire Bonds tab) first")
    have = {(r.kind, r.layer_a, r.layer_b) for r in existing}
    out = []
    for kind, value, name in (("BOND_MIN_LENGTH", bond_rules.get("min_length", 300), "BOND.L.MIN"),
                              ("BOND_MAX_LENGTH", bond_rules.get("max_length", 5000), "BOND.L.MAX")):
        if (kind, layer, None) not in have and float(value) > 0:
            out.append(DrcRule(kind=kind, layer_a=layer, value=float(value), name=name))
    for pad_layer in bond_rules.get("die_pad_layers") or []:
        if ("BOND_MAX_ANGLE", layer, int(pad_layer)) not in have:
            out.append(DrcRule(kind="BOND_MAX_ANGLE", layer_a=layer, layer_b=int(pad_layer),
                               value=float(angle), name="BOND.ANGLE"))
    return out


def check_rule(library, cell, rule: DrcRule) -> List[DrcViolation]:
    """Run one DrcRule against one cell (its own shapes plus everything
    reachable through its placed cell instances -- see
    hlayout.core.maskops.layer_geoms_flattened). Returns a DrcViolation
    per distinct violating region found."""
    _require_shapely()
    if rule.kind in BOND_KINDS or rule.kind == "EDGE_CLEARANCE":
        violations = []
        found = (_check_edge_clearance(library, cell, rule) if rule.kind == "EDGE_CLEARANCE"
                 else _check_bonds(library, cell, rule))
        for geom, msg in found:
            try:
                polys = shapely_to_polygons(geom, layer=0)
            except GeometryError:
                polys = shapely_to_polygons(geom.simplify(max(rule.value * 0.01, 1.0),
                                                          preserve_topology=True), layer=0)
            for poly in polys:
                violations.append(DrcViolation(rule=rule, cell_name=cell.name, points=poly.points,
                                               message=f"{rule.name}: {msg}" if rule.name else msg))
        return violations
    geoms_a = layer_geoms_flattened(library, cell, rule.layer_a)
    if rule.kind == "MIN_WIDTH":
        raw = _check_min_width(rule, geoms_a)
    elif rule.kind == "MIN_AREA":
        raw = _check_min_area(rule, geoms_a)
    elif rule.kind == "MIN_SPACE":
        raw = _check_min_space(rule, geoms_a)
    elif rule.kind == "MIN_SPACE_BETWEEN":
        geoms_b = layer_geoms_flattened(library, cell, rule.layer_b)
        raw = _check_min_space_between(rule, geoms_a, geoms_b)
    elif rule.kind == "MIN_ENCLOSURE":
        geoms_b = layer_geoms_flattened(library, cell, rule.layer_b)
        raw = _check_min_enclosure(rule, geoms_a, geoms_b)
    else:  # pragma: no cover - DrcRule.__post_init__ already validates kind
        raise GeometryError(f"unknown DRC rule kind {rule.kind!r}")

    violations = []
    for geom in raw:
        try:
            polys = shapely_to_polygons(geom, layer=0)
        except GeometryError:
            # An intricate violation region (e.g. a MIN_WIDTH opening
            # diff against a very jagged shape) can occasionally exceed
            # the 200-point polygon limit -- simplify it rather than
            # dropping the violation report entirely. A small positive
            # tolerance relative to the rule's own value keeps the
            # simplified marker recognisably in the same place.
            simplified = geom.simplify(max(rule.value * 0.01, 1.0), preserve_topology=True)
            polys = shapely_to_polygons(simplified, layer=0)
        for poly in polys:
            violations.append(DrcViolation(rule=rule, cell_name=cell.name, points=poly.points))
    return violations


def run_drc(library, rules: List[DrcRule], cell_names: Optional[List[str]] = None) -> List[DrcViolation]:
    """Run every rule in `rules` against every cell in `cell_names`
    (default: every cell in the library). Returns all violations found,
    in rule order then cell order."""
    _require_shapely()
    cells = ([library.get_cell(n) for n in cell_names] if cell_names
             else list(library.cells.values()))
    violations = []
    for rule in rules:
        for cell in cells:
            violations.extend(check_rule(library, cell, rule))
    return violations
