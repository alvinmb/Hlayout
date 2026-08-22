"""Library: the top-level database — a set of named cells plus the layer table."""

import copy
import math
from typing import Dict, Optional

from .cell import Cell
from .errors import BoundsError, CellError
from .layers import LayerTable
from .shapes import SRef
from .units import DBU_MM, MAX_SIZE_DBU, MAX_SIZE_MM


class Library:
    """The layout database. Default format on disk is GDSII (see
    hlayout.io.gdsio). Units are fixed at 1 micron (0.001 mm) per DBU;
    the database extent is capped at 500 x 500 mm as required by the
    spec."""

    def __init__(self, name: str = "HLIB"):
        self.name = name
        self.dbu_mm = DBU_MM
        self.max_size_mm = (MAX_SIZE_MM, MAX_SIZE_MM)
        self.layers = LayerTable()
        self.cells: Dict[str, Cell] = {}
        self.top_cell_name: Optional[str] = None
        # Open-ended global parameters loaded from/saved to a technology
        # file (see hlayout.io.techfile) alongside the layer stack --
        # e.g. process name, design rules, whatever a given process
        # technology needs to carry. Deliberately just str->str: new
        # keys can be introduced over time without a schema change here.
        self.tech_params: Dict[str, str] = {}

    # --- cell management -------------------------------------------------
    def new_cell(self, name: str) -> Cell:
        if name in self.cells:
            raise CellError(f"cell {name!r} already exists")
        cell = Cell(name=name)
        self.cells[name] = cell
        if self.top_cell_name is None:
            self.top_cell_name = name
        return cell

    def add_cell(self, cell: Cell) -> Cell:
        if cell.name in self.cells:
            raise CellError(f"cell {cell.name!r} already exists")
        self.cells[cell.name] = cell
        if self.top_cell_name is None:
            self.top_cell_name = cell.name
        return cell

    def get_cell(self, name: str) -> Cell:
        try:
            return self.cells[name]
        except KeyError:
            raise CellError(f"no such cell: {name!r}") from None

    def delete_cell(self, name: str) -> None:
        if name not in self.cells:
            raise CellError(f"no such cell: {name!r}")
        for cell in self.cells.values():
            if any(ref.cell_name == name for ref in cell.srefs):
                raise CellError(f"cannot delete {name!r}: still referenced by {cell.name!r}")
        del self.cells[name]
        if self.top_cell_name == name:
            self.top_cell_name = next(iter(self.cells), None)

    def top_cells(self):
        """Cells that are not referenced by any other cell (candidate roots)."""
        referenced = {ref.cell_name for c in self.cells.values() for ref in c.srefs}
        return [c for name, c in self.cells.items() if name not in referenced]

    # --- hierarchy validation ---------------------------------------------
    def check_no_cycles(self) -> None:
        WHITE, GRAY, BLACK = 0, 1, 2
        state = {name: WHITE for name in self.cells}

        def visit(name, stack):
            if name not in self.cells:
                raise CellError(f"sref references unknown cell {name!r} (from {stack[-1]!r})")
            if state[name] == GRAY:
                raise CellError("cyclic cell reference: " + " -> ".join(stack + [name]))
            if state[name] == BLACK:
                return
            state[name] = GRAY
            for ref in self.cells[name].srefs:
                visit(ref.cell_name, stack + [name])
            state[name] = BLACK

        for name in list(self.cells):
            if state[name] == WHITE:
                visit(name, [])

    def would_create_cycle(self, container_name: str, target_name: str) -> bool:
        """True if placing an Sref to `target_name` inside `container_name`
        would create a cyclic cell reference -- including the direct case
        of a cell instancing itself (container_name == target_name), and
        the indirect case of `target_name`'s own reference closure
        already reaching back to `container_name` (placing a cell inside
        one of its own descendants). Meant to be checked *before* the
        Sref is added -- see Session.sref() -- so an attempt to create a
        cycle is rejected with a clear error up front, rather than
        corrupting the design and only surfacing as a crash later (e.g.
        from cell_bbox()/check_no_cycles(), or on save)."""
        if target_name == container_name:
            return True
        seen = set()
        stack = [target_name]
        while stack:
            name = stack.pop()
            if name == container_name:
                return True
            if name in seen or name not in self.cells:
                continue
            seen.add(name)
            stack.extend(ref.cell_name for ref in self.cells[name].srefs)
        return False

    # --- hierarchical bbox / bounds checking -------------------------------
    def cell_bbox(self, name: str, _seen=None):
        """Bounding box of a cell including everything placed via srefs,
        in DBU, as (x1, y1, x2, y2), or None if empty."""
        if _seen is None:
            _seen = set()
        if name in _seen:
            raise CellError(f"cyclic cell reference detected at {name!r}")
        _seen = _seen | {name}

        cell = self.get_cell(name)
        boxes = []
        own = cell.bbox()
        if own is not None:
            boxes.append(own)

        for ref in cell.srefs:
            child_box = self.cell_bbox(ref.cell_name, _seen)
            if child_box is None:
                continue
            boxes.append(_transform_bbox(child_box, ref))

        if not boxes:
            return None
        x1 = min(b[0] for b in boxes)
        y1 = min(b[1] for b in boxes)
        x2 = max(b[2] for b in boxes)
        y2 = max(b[3] for b in boxes)
        return (x1, y1, x2, y2)

    def check_bounds(self, name: Optional[str] = None) -> None:
        """Raise BoundsError if the given cell (default: top cell) exceeds
        the 500 x 500 mm database extent."""
        name = name or self.top_cell_name
        if name is None:
            return
        box = self.cell_bbox(name)
        if box is None:
            return
        x1, y1, x2, y2 = box
        width_dbu = x2 - x1
        height_dbu = y2 - y1
        if width_dbu > MAX_SIZE_DBU or height_dbu > MAX_SIZE_DBU:
            raise BoundsError(
                f"cell {name!r} extent is "
                f"{width_dbu * DBU_MM:.2f} x {height_dbu * DBU_MM:.2f} mm, "
                f"exceeds the {MAX_SIZE_MM:.0f} x {MAX_SIZE_MM:.0f} mm database limit"
            )

    # --- importing cells from another library (attached libraries) --------
    def _unique_cell_name(self, base: str) -> str:
        if base not in self.cells:
            return base
        i = 1
        while f"{base}_{i}" in self.cells:
            i += 1
        return f"{base}_{i}"

    def import_cell_closure(self, source: "Library", root_name: str) -> str:
        """Copy ``root_name`` and everything it (transitively) references
        via Srefs from ``source`` into this library. GDSII Srefs need
        their target cell defined in the same file, so placing an
        instance of a cell that lives in an attached reference library
        first requires copying that cell (and its dependency closure)
        in.

        Returns the name the root cell now has in *this* library —
        identical to ``root_name`` unless that name was already taken by
        a different cell here, in which case the import uses a
        suffixed, non-colliding name (``root_name_1``, etc) instead of
        clobbering the existing cell.
        """
        if root_name not in source.cells:
            raise CellError(f"no such cell {root_name!r} in source library")

        name_map: Dict[str, str] = {}

        def visit(name: str) -> str:
            if name in name_map:
                return name_map[name]
            new_name = self._unique_cell_name(name)
            name_map[name] = new_name
            # Reserve the name before recursing into children so shared
            # sub-cells resolve to the same copy and cyclic references
            # (which shouldn't exist, but shouldn't infinite-loop either)
            # terminate.
            self.cells[new_name] = Cell(name=new_name)
            if self.top_cell_name is None:
                self.top_cell_name = new_name
            src_cell = source.cells[name]
            new_shapes = copy.deepcopy(src_cell.shapes)
            new_srefs = []
            for ref in src_cell.srefs:
                child_name = visit(ref.cell_name)
                new_srefs.append(SRef(cell_name=child_name, origin=ref.origin,
                                        rotation=ref.rotation, magnification=ref.magnification,
                                        x_reflection=ref.x_reflection))
            cell = self.cells[new_name]
            cell.shapes = new_shapes
            cell.srefs = new_srefs
            return new_name

        return visit(root_name)

    def __repr__(self):
        return f"Library({self.name!r}, cells={len(self.cells)})"


def _transform_bbox(box, ref):
    """Apply an SRef's rotation/magnification/reflection/origin to a
    child bounding box, returning the transformed axis-aligned bbox."""
    x1, y1, x2, y2 = box
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    theta = math.radians(ref.rotation)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    mag = ref.magnification
    ox, oy = ref.origin

    transformed = []
    for x, y in corners:
        if ref.x_reflection:
            y = -y
        x *= mag
        y *= mag
        xr = x * cos_t - y * sin_t
        yr = x * sin_t + y * cos_t
        transformed.append((xr + ox, yr + oy))

    xs = [p[0] for p in transformed]
    ys = [p[1] for p in transformed]
    return (min(xs), min(ys), max(xs), max(ys))
