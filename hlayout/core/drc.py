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

Note on self-notch spacing: detecting a too-narrow notch WITHIN a
single connected shape (as opposed to the space BETWEEN two distinct
shapes, which MIN_SPACE covers) is a materially different and more
expensive check -- it requires reasoning about the background region
near the shape's own boundary rather than a simple pairwise distance --
and isn't implemented here. MIN_WIDTH, run on the same layer, already
catches many of the same design mistakes from the material side.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from .errors import GeometryError
from .maskops import _ShapelyPolygon, _require_shapely, layer_geoms_flattened, shapely_to_polygons, unary_union

DRC_RULE_KINDS = ("MIN_WIDTH", "MIN_AREA", "MIN_SPACE", "MIN_SPACE_BETWEEN", "MIN_ENCLOSURE")

#: Which rule kinds take a second layer.
TWO_LAYER_KINDS = ("MIN_SPACE_BETWEEN", "MIN_ENCLOSURE")

#: A small fixed half-width (DBU) used to turn a spacing violation's two
#: nearest points into a visible marker polygon (a thin rectangle along
#: the gap) rather than a zero-area line.
_SPACE_MARKER_HALF_WIDTH = 1.0


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
        if self.kind in TWO_LAYER_KINDS:
            base = f"{self.kind}(layer {self.layer_a}, layer {self.layer_b}) >= {self.value}"
        else:
            base = f"{self.kind}(layer {self.layer_a}) >= {self.value}"
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


def _check_min_width(rule: DrcRule, geoms: list) -> list:
    _require_shapely()
    if not geoms:
        return []
    merged = unary_union(geoms)
    if merged.is_empty:
        return []
    half = rule.value / 2.0
    opened = merged.buffer(-half, join_style="mitre", mitre_limit=5.0)
    if not opened.is_empty:
        opened = opened.buffer(half, join_style="mitre", mitre_limit=5.0)
    violation = merged.difference(opened) if not opened.is_empty else merged
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


def check_rule(library, cell, rule: DrcRule) -> List[DrcViolation]:
    """Run one DrcRule against one cell (its own shapes plus everything
    reachable through its placed cell instances -- see
    hlayout.core.maskops.layer_geoms_flattened). Returns a DrcViolation
    per distinct violating region found."""
    _require_shapely()
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
