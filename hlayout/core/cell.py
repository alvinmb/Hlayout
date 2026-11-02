"""Cell: a named collection of shapes and Sref instances."""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Union

from .errors import GeometryError
from .shapes import ARef, Path, Polygon, Rectangle, SRef, Text, circle_points

Shape = Union[Polygon, Path, Rectangle, Text]


@dataclass
class Cell:
    name: str
    shapes: List[Shape] = field(default_factory=list)
    srefs: List[SRef] = field(default_factory=list)
    arefs: List[ARef] = field(default_factory=list)
    # Date/time stamps, kept in the GDSII BGNSTR record of every cell so
    # they travel with the cell into any library or design it is placed
    # in. ``created`` is set once; ``modified`` moves on save when the
    # cell's content has changed since it was loaded or last saved (see
    # stamp()). Neither takes part in equality.
    created: Optional[datetime] = field(default=None, compare=False)
    modified: Optional[datetime] = field(default=None, compare=False)
    _saved_fingerprint: Optional[str] = field(default=None, compare=False, repr=False)

    # --- date/time stamps ----------------------------------------------
    def fingerprint(self) -> str:
        """Hash of this cell's content (shapes, srefs, arefs) -- used to
        tell whether it changed since it was loaded or last saved."""
        h = hashlib.sha1()
        for group in (self.shapes, self.srefs, self.arefs):
            for item in group:
                h.update(repr(item).encode("utf-8", "replace"))
            h.update(b"|")
        return h.hexdigest()

    def mark_clean(self) -> None:
        """Record the current content as the saved state."""
        self._saved_fingerprint = self.fingerprint()

    def stamp(self, now: Optional[datetime] = None) -> None:
        """Bring the stamps up to date before a save: a new cell gets
        created = modified = now; a cell whose content changed since it
        was loaded/saved gets modified = now. Unchanged cells keep the
        stamps they were loaded with."""
        now = (now or datetime.now()).replace(microsecond=0)
        fp = self.fingerprint()
        if self.created is None:
            self.created = now
        if self.modified is None:
            self.modified = now
        elif self._saved_fingerprint is not None and fp != self._saved_fingerprint:
            self.modified = now
        if self.modified < self.created:
            self.modified = self.created
        self._saved_fingerprint = fp

    def stamp_text(self) -> str:
        """'created 2026-11-02 13:05  modified 2026-11-02 13:42', or
        'not saved yet' for a cell with no stamps."""
        fmt = "%Y-%m-%d %H:%M:%S"
        if self.created is None and self.modified is None:
            return "not saved yet"
        c = self.created.strftime(fmt) if self.created else "?"
        m = self.modified.strftime(fmt) if self.modified else "?"
        return f"created {c}  modified {m}"

    # --- adders -----------------------------------------------------
    def add_polygon(self, layer: int, points, datatype: int = 0, pin: str = "") -> Polygon:
        shape = Polygon(layer=layer, points=points, datatype=datatype, pin=pin)
        self.shapes.append(shape)
        return shape

    def add_path(self, layer: int, points, width: int, cap="flush", datatype: int = 0) -> Path:
        shape = Path(layer=layer, points=points, width=width, cap=cap, datatype=datatype)
        self.shapes.append(shape)
        return shape

    def add_rectangle(self, layer: int, x1, y1, x2, y2, datatype: int = 0, pin: str = "") -> Rectangle:
        shape = Rectangle.from_corners(layer, x1, y1, x2, y2, datatype=datatype, pin=pin)
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
                 magnification: float = 1.0, x_reflection: bool = False,
                 label: str = "", pin: str = "") -> SRef:
        ref = SRef(cell_name=cell_name, origin=origin, rotation=rotation,
                   magnification=magnification, x_reflection=x_reflection,
                   label=label, pin=pin)
        self.srefs.append(ref)
        return ref

    def add_aref(self, cell_name: str, origin, columns: int = 1, rows: int = 1,
                 col_spacing: float = 0.0, row_spacing: float = 0.0, rotation: float = 0.0,
                 magnification: float = 1.0, x_reflection: bool = False) -> ARef:
        ref = ARef(cell_name=cell_name, origin=origin, columns=columns, rows=rows,
                   col_spacing=col_spacing, row_spacing=row_spacing, rotation=rotation,
                   magnification=magnification, x_reflection=x_reflection)
        self.arefs.append(ref)
        return ref

    def smash_aref(self, ref: ARef) -> List[SRef]:
        """Explode an ARef in this cell back into individual SRefs, one
        per array position, removing the ARef and appending the new
        SRefs in its place. Returns the list of SRefs created."""
        if ref not in self.arefs:
            raise GeometryError("aref not found in this cell")
        new_refs = []
        for origin in ref.instance_origins():
            new_refs.append(self.add_sref(
                ref.cell_name, origin, rotation=ref.rotation,
                magnification=ref.magnification, x_reflection=ref.x_reflection,
            ))
        self.arefs.remove(ref)
        return new_refs

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
        elif shape in self.arefs:
            self.arefs.remove(shape)
        else:
            raise GeometryError("shape not found in cell")
