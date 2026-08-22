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
    """A closed polygon on a single layer, up to 200 vertices."""

    layer: int
    points: List[Point]
    datatype: int = 0

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
        return (min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half)


@dataclass
class Rectangle:
    """An axis-aligned or rotated rectangle, stored as its 4 corner points."""

    layer: int
    points: List[Point]
    datatype: int = 0

    def __post_init__(self):
        _check_layer(self.layer)
        self.points = _as_points(self.points)
        if len(self.points) != 4:
            raise GeometryError("rectangle must have exactly 4 points")

    @classmethod
    def from_corners(cls, layer: int, x1: int, y1: int, x2: int, y2: int, datatype: int = 0):
        """Convenience constructor for an axis-aligned rectangle."""
        x1, x2 = sorted((int(round(x1)), int(round(x2))))
        y1, y2 = sorted((int(round(y1)), int(round(y2))))
        return cls(layer=layer, points=[(x1, y1), (x2, y1), (x2, y2), (x1, y2)], datatype=datatype)

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
    referenced cell may itself contain data on any layer."""

    cell_name: str
    origin: Point
    rotation: float = 0.0  # degrees, counter-clockwise
    magnification: float = 1.0
    x_reflection: bool = False

    def __post_init__(self):
        x, y = self.origin
        self.origin = (int(round(x)), int(round(y)))
        if self.magnification <= 0:
            raise GeometryError("sref magnification must be positive")
