"""Cell: a named collection of shapes and Sref instances."""

from dataclasses import dataclass, field
from typing import List, Union

from .errors import GeometryError
from .shapes import Path, Polygon, Rectangle, SRef, Text, circle_points

Shape = Union[Polygon, Path, Rectangle, Text]


@dataclass
class Cell:
    name: str
    shapes: List[Shape] = field(default_factory=list)
    srefs: List[SRef] = field(default_factory=list)

    # --- adders -----------------------------------------------------
    def add_polygon(self, layer: int, points, datatype: int = 0) -> Polygon:
        shape = Polygon(layer=layer, points=points, datatype=datatype)
        self.shapes.append(shape)
        return shape

    def add_path(self, layer: int, points, width: int, cap="flush", datatype: int = 0) -> Path:
        shape = Path(layer=layer, points=points, width=width, cap=cap, datatype=datatype)
        self.shapes.append(shape)
        return shape

    def add_rectangle(self, layer: int, x1, y1, x2, y2, datatype: int = 0) -> Rectangle:
        shape = Rectangle.from_corners(layer, x1, y1, x2, y2, datatype=datatype)
        self.shapes.append(shape)
        return shape

    def add_circle(self, layer: int, center, radius, segments: int = 32,
                    datatype: int = 0) -> Polygon:
        """A circle is stored as a regular Polygon approximating it.
        `segments` must be one of 8, 16, 32, or 64."""
        points = circle_points(center, radius, segments)
        return self.add_polygon(layer, points, datatype=datatype)

    def add_text(self, layer: int, origin, text: str, height: int = 10, font: int = 0,
                 rotation: float = 0.0, datatype: int = 0) -> Text:
        shape = Text(layer=layer, origin=origin, text=text, height=height,
                     font=font, rotation=rotation, datatype=datatype)
        self.shapes.append(shape)
        return shape

    def add_sref(self, cell_name: str, origin, rotation: float = 0.0,
                 magnification: float = 1.0, x_reflection: bool = False) -> SRef:
        ref = SRef(cell_name=cell_name, origin=origin, rotation=rotation,
                   magnification=magnification, x_reflection=x_reflection)
        self.srefs.append(ref)
        return ref

    # --- queries ------------------------------------------------------
    def shapes_on_layer(self, layer: int):
        return [s for s in self.shapes if s.layer == layer]

    def bbox(self):
        """Bounding box of this cell's own shapes (does not descend into
        srefs; use Library.cell_bbox for the full hierarchical bbox)."""
        boxes = [s.bbox() for s in self.shapes]
        if not boxes:
            return None
        xs1 = min(b[0] for b in boxes)
        ys1 = min(b[1] for b in boxes)
        xs2 = max(b[2] for b in boxes)
        ys2 = max(b[3] for b in boxes)
        return (xs1, ys1, xs2, ys2)

    def remove(self, shape) -> None:
        if shape in self.shapes:
            self.shapes.remove(shape)
        elif shape in self.srefs:
            self.srefs.remove(shape)
        else:
            raise GeometryError("shape not found in cell")
