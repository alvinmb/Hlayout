"""Layout primitives: Polygon, Path, Rectangle, Text, SRef.

All coordinates are integer (x, y) pairs in database units (1 DBU = 1 micron = 0.001 mm).
"""

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple

from .errors import GeometryError, LayerError
from .layers import NUM_LAYERS

Point = Tuple[int, int]

MAX_POLY_POINTS = 200
MAX_PATH_POINTS = 200

#: A "circle" isn't its own GDSII/database primitive — it's stored as a
#: regular Polygon approximating the circle, so it round-trips through
#: GDSII like any other polygon. The segment count is restricted to this
#: set (rather than allowing arbitrary N) to keep circles visually
#: consistent across a design.
CIRCLE_SEGMENT_CHOICES = (8, 16, 32, 64)


def circle_points(center: Point, radius, segments: int) -> List[Point]:
    """Vertices of a regular polygon approximating a circle of the given
    radius (DBU) centred at `center`, with `segments` sides — segments
    must be one of CIRCLE_SEGMENT_CHOICES (8, 16, 32, or 64)."""
    if segments not in CIRCLE_SEGMENT_CHOICES:
        raise GeometryError(
            f"circle segments must be one of {CIRCLE_SEGMENT_CHOICES}, got {segments}"
        )
    if radius <= 0:
        raise GeometryError("circle radius must be positive")
    cx, cy = center
    points = []
    for i in range(segments):
        theta = 2 * math.pi * i / segments
        x = cx + radius * math.cos(theta)
        y = cy + radius * math.sin(theta)
        points.append((int(round(x)), int(round(y))))
    return points


def _check_layer(layer: int):
    if not (0 <= layer < NUM_LAYERS):
        raise LayerError(f"layer number {layer} out of range 0-{NUM_LAYERS - 1}")


def _as_points(points) -> List[Point]:
    pts = [(int(round(x)), int(round(y))) for x, y in points]
    return pts


class PathCap(Enum):
    ROUND = "round"
    SQUARE = "square"
    FLUSH = "flush"  # no extension (not in the original spec's two options,
    #                   but useful/expected as a third GDS-native style)


@dataclass
class Polygon:
    """A closed polygon on a single layer, up to 200 vertices.

    `pin` is an optional pin-number/name (e.g. "1", "2", "K") for a
    polygon that represents part of a component footprint's pad (e.g. a
    gull-wing lead) -- empty string means "not a pin" / "not yet
    numbered". See Rectangle.pin for the common case (most pads are
    plain rectangles); this exists on Polygon too so shaped leads can
    be numbered the same way. Persisted through GDSII round-trips as a
    GDS element property (see io/gdsio.py's POLY_PIN_PROPATTR), same
    mechanism as SRef.label."""

    layer: int
    points: List[Point]
    datatype: int = 0
    pin: str = ""

    def __post_init__(self):
        _check_layer(self.layer)
        self.points = _as_points(self.points)
        if len(self.points) < 3:
            raise GeometryError("polygon needs at least 3 points")
        if len(self.points) > MAX_POLY_POINTS:
            raise GeometryError(
                f"polygon has {len(self.points)} points, max is {MAX_POLY_POINTS}"
            )

    def bbox(self):
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass
class Path:
    """A multi-segment path on a single layer, up to 200 points, with a
    width and a round or square end cap."""

    layer: int
    points: List[Point]
    width: int
    cap: PathCap = PathCap.FLUSH
    datatype: int = 0

    def __post_init__(self):
        _check_layer(self.layer)
        self.points = _as_points(self.points)
        if len(self.points) < 2:
            raise GeometryError("path needs at least 2 points")
        if len(self.points) > MAX_PATH_POINTS:
            raise GeometryError(
                f"path has {len(self.points)} points, max is {MAX_PATH_POINTS}"
            )
        if self.width < 0:
            raise GeometryError("path width cannot be negative")
        if isinstance(self.cap, str):
            self.cap = PathCap(self.cap)

    def bbox(self):
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        half = self.width / 2
        if self.cap != PathCap.FLUSH or not half:
            return (min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half)
        # A flush cap stops dead at the end point: the stroke only
        # reaches half a width sideways there, not past the end. Interior
        # vertices keep the conservative +/- half box (covers the join).
        pts = self.points
        cx, cy = [], []
        for i, (x, y) in enumerate(pts):
            ox, oy = pts[1] if i == 0 else pts[-2]
            length = math.hypot(x - ox, y - oy)
            if 0 < i < len(pts) - 1 or not length:
                cx += [x - half, x + half]
                cy += [y - half, y + half]
                continue
            nx, ny = (y - oy) / length * half, (x - ox) / length * half
            cx += [x - nx, x + nx]
            cy += [y - ny, y + ny]
        return (min(cx), min(cy), max(cx), max(cy))


@dataclass
class Rectangle:
    """An axis-aligned or rotated rectangle, stored as its 4 corner points.

    `pin` is an optional pin-number/name (e.g. "1", "2", "K") identifying
    this rectangle as a specific pad of a component footprint -- empty
    string (the default) means "not a pin" or "not yet numbered". Set
    automatically for unambiguous 2-pad parts (Session.create_via_cell()
    does NOT set it -- vias aren't pins; Session.create_pad_cell()/
    generate_resistor() do, for a genuine 2-terminal footprint) or by
    hand/via the pin-numbering canvas tool (Edit > Number Pins...) for
    everything else -- see hlayout.core.netlist for how these feed a
    netlist extraction. Persisted through GDSII round-trips as a GDS
    element property (see io/gdsio.py's RECT_PIN_PROPATTR), same
    mechanism as SRef.label."""

    layer: int
    points: List[Point]
    datatype: int = 0
    pin: str = ""

    def __post_init__(self):
        _check_layer(self.layer)
        self.points = _as_points(self.points)
        if len(self.points) != 4:
            raise GeometryError("rectangle must have exactly 4 points")

    @classmethod
    def from_corners(cls, layer: int, x1: int, y1: int, x2: int, y2: int, datatype: int = 0,
                      pin: str = ""):
        """Convenience constructor for an axis-aligned rectangle."""
        x1, x2 = sorted((int(round(x1)), int(round(x2))))
        y1, y2 = sorted((int(round(y1)), int(round(y2))))
        return cls(layer=layer, points=[(x1, y1), (x2, y1), (x2, y2), (x1, y2)],
                    datatype=datatype, pin=pin)

    def bbox(self):
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (min(xs), min(ys), max(xs), max(ys))

    def is_axis_aligned(self) -> bool:
        xs = sorted({p[0] for p in self.points})
        ys = sorted({p[1] for p in self.points})
        return len(xs) == 2 and len(ys) == 2


#: The 4 fonts available for Text, matching GDSII's built-in font indices 0-3.
FONTS = ["Font 0", "Font 1", "Font 2", "Font 3"]


@dataclass
class Text:
    """A text label: origin, height (size), and font selection."""

    layer: int
    origin: Point
    text: str
    height: int = 10  # DBU
    font: int = 0  # index into FONTS, 0-3
    rotation: float = 0.0  # degrees
    datatype: int = 0

    def __post_init__(self):
        _check_layer(self.layer)
        x, y = self.origin
        self.origin = (int(round(x)), int(round(y)))
        if not (0 <= self.font < len(FONTS)):
            raise GeometryError(f"font index {self.font} out of range 0-{len(FONTS) - 1}")
        if self.height <= 0:
            raise GeometryError("text height must be positive")

    def bbox(self):
        # Rough bbox estimate: height * 0.6 per character, ignoring rotation.
        w = int(self.height * 0.6 * max(len(self.text), 1))
        x, y = self.origin
        return (x, y, x + w, y + self.height)


@dataclass
class SRef:
    """An instance ('structure reference') of another cell, placed at an
    origin with optional rotation, magnification and mirroring. The
    referenced cell may itself contain data on any layer.

    `label` is an optional per-instance annotation (e.g. a reference
    designator like "C1") that belongs to THIS placement only -- unlike
    editing a Text shape inside the referenced cell (which would change
    every instance of that cell), each SRef has its own independent
    label. It's a separate annotation drawn next to the instance, NOT a
    replacement for any Text shape inside the referenced cell -- so a
    cell's own built-in ref-des placeholder (e.g. "C" baked into a
    library part) keeps showing regardless of what label an instance
    is given. It's drawn at the instance's bounding-box centre (plus
    `label_offset`) and follows the instance when it's moved, since it's
    re-derived every redraw rather than being a separate freestanding
    shape; dragging it on the canvas updates `label_offset`, not the
    instance. `label_height` is its on-screen text size in pixels
    (independent of zoom, like the Measure tool's distance label), user-
    adjustable per instance. Persisted through GDSII round-trips as GDS
    element properties (see io/gdsio.py's SREF_LABEL_PROPATTR /
    SREF_LABEL_HEIGHT_PROPATTR / SREF_LABEL_OFFSET_PROPATTR) since GDSII
    SREF elements have no native text field of their own.

    `pin` identifies THIS placement as a specific numbered lead/pin of
    whatever larger footprint it's part of -- e.g. a round lead built
    via the Via Wizard (Session.create_via_cell()), where the round
    geometry lives once in a shared via-cell template and gets placed
    as an SRef wherever a lead is needed, so a per-shape
    Rectangle.pin/Polygon.pin (which would be shared by every instance
    of that template) can't tell one placed lead's number from
    another's the way each SRef's own `pin` can. Distinct from
    `label`: `label` is a ref-designator annotation drawn next to the
    instance (e.g. "R1"), `pin` is electrical pin identity feeding
    hlayout.core.netlist extraction and
    hlayout.io.component_match.footprint_pin_stats() the same way
    Rectangle.pin/Polygon.pin do -- empty string means "not a pin".
    Persisted through GDSII round-trips as a GDS element property (see
    io/gdsio.py's SREF_PIN_PROPATTR)."""

    cell_name: str
    origin: Point
    rotation: float = 0.0  # degrees, counter-clockwise
    magnification: float = 1.0
    x_reflection: bool = False
    label: str = ""
    label_height: int = 14  # on-screen px, independent of zoom
    #: DBU (dx, dy) shift of the floated label from its default anchor --
    #: the instance's bounding-box centre. Lets the ref-des be dragged
    #: clear of the part it names without moving the part. Persisted as a
    #: GDS element property (io/gdsio.py SREF_LABEL_OFFSET_PROPATTR).
    label_offset: Point = (0, 0)
    pin: str = ""

    def __post_init__(self):
        x, y = self.origin
        self.origin = (int(round(x)), int(round(y)))
        lx, ly = self.label_offset
        self.label_offset = (int(round(lx)), int(round(ly)))
        if self.magnification <= 0:
            raise GeometryError("sref magnification must be positive")
        if self.label_height <= 0:
            raise GeometryError("sref label_height must be positive")


@dataclass
class ARef:
    """An array ('array reference') of instances of another cell, laid
    out as columns x rows starting at an origin with a fixed column/
    row spacing (in DBU), sharing one rotation/magnification/mirror for
    the whole array -- GDSII's native AREF element, same idea as SRef
    but repeated. col_spacing/row_spacing are defined *before*
    rotation/mirroring is applied (see instance_origins()), matching
    real GDSII AREF semantics: the grid rotates/mirrors together with
    the array, but magnification only scales each instance's own
    geometry, not the spacing between instances.

    Use Cell.smash_aref()/Session.smash_aref() to explode an ARef back
    into individual SRefs, one per array position -- the command bar's
    SMASH command, or Session.aref() to place a new one (the AREF
    command)."""

    cell_name: str
    origin: Point
    columns: int = 1
    rows: int = 1
    col_spacing: float = 0.0
    row_spacing: float = 0.0
    rotation: float = 0.0  # degrees, counter-clockwise
    magnification: float = 1.0
    x_reflection: bool = False

    def __post_init__(self):
        x, y = self.origin
        self.origin = (int(round(x)), int(round(y)))
        if self.magnification <= 0:
            raise GeometryError("aref magnification must be positive")
        self.columns = int(self.columns)
        self.rows = int(self.rows)
        if self.columns < 1 or self.rows < 1:
            raise GeometryError("aref columns/rows must be at least 1")
        if self.columns > 1 and self.col_spacing == 0:
            raise GeometryError("aref col_spacing must be nonzero when columns > 1")
        if self.rows > 1 and self.row_spacing == 0:
            raise GeometryError("aref row_spacing must be nonzero when rows > 1")

    @property
    def count(self) -> int:
        return self.columns * self.rows

    def instance_origins(self) -> List[Point]:
        """World-space origin of every array position (row-major: column
        varies fastest). The column/row spacing vectors are rotated and
        mirrored together with the array's own rotation/x_reflection
        (matching GDSII AREF), but never scaled by magnification --
        magnification only scales each instance's own geometry, exactly
        like a plain SRef."""
        theta = math.radians(self.rotation)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        ox, oy = self.origin
        origins = []
        for r in range(self.rows):
            for c in range(self.columns):
                dx = c * self.col_spacing
                dy = r * self.row_spacing
                if self.x_reflection:
                    dy = -dy
                rx = dx * cos_t - dy * sin_t
                ry = dx * sin_t + dy * cos_t
                origins.append((int(round(ox + rx)), int(round(oy + ry))))
        return origins


def expand_arefs_to_srefs(arefs: List["ARef"]) -> List[SRef]:
    """One synthetic SRef per array position, for callers (flattening
    exporters, hierarchy walkers, the canvas) that only need to iterate
    'every cell instance here' without caring whether it came from a
    plain SRef or an ARef position."""
    out = []
    for ref in arefs:
        for origin in ref.instance_origins():
            out.append(SRef(cell_name=ref.cell_name, origin=origin, rotation=ref.rotation,
                             magnification=ref.magnification, x_reflection=ref.x_reflection))
    return out
