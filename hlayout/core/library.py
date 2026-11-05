# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Library: the top-level database — a set of named cells plus the layer table."""

import copy
import math
from typing import Dict, Iterable, Optional

from .cell import Cell
from .errors import BoundsError, CellError
from .layers import LayerTable
from .shapes import ARef, SRef
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
        cell.stamp()
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
            if any(ref.cell_name == name for ref in cell.srefs) or \
                    any(ref.cell_name == name for ref in cell.arefs):
                raise CellError(f"cannot delete {name!r}: still referenced by {cell.name!r}")
        del self.cells[name]
        if self.top_cell_name == name:
            self.top_cell_name = next(iter(self.cells), None)

    def top_cells(self):
        """Cells that are not referenced by any other cell (candidate roots)."""
        referenced = {ref.cell_name for c in self.cells.values() for ref in c.srefs}
        referenced |= {ref.cell_name for c in self.cells.values() for ref in c.arefs}
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
            for ref in self.cells[name].arefs:
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
            stack.extend(ref.cell_name for ref in self.cells[name].arefs)
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

        for aref in cell.arefs:
            child_box = self.cell_bbox(aref.cell_name, _seen)
            if child_box is None:
                continue
            for origin in aref.instance_origins():
                instance = SRef(cell_name=aref.cell_name, origin=origin, rotation=aref.rotation,
                                 magnification=aref.magnification, x_reflection=aref.x_reflection)
                boxes.append(_transform_bbox(child_box, instance))

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

    def import_cell_closure(self, source: "Library", root_name: str,
                            reuse_names: Optional[Iterable[str]] = None) -> str:
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

        ``reuse_names`` is a set of *shared sub-cell* names that, when
        one already exists in this library, should be referenced in
        place rather than copied in under a fresh suffixed name — e.g.
        the Chip Wizard's ``chip_pin`` / ``chip_pin1`` primitives, so
        re-exporting a second chip into the same library doesn't pile up
        ``chip_pin_1``, ``chip_pin_2`` … duplicates. The root cell
        itself is never reused this way.
        """
        if root_name not in source.cells:
            raise CellError(f"no such cell {root_name!r} in source library")

        reuse = {n for n in (reuse_names or ()) if n in self.cells}
        name_map: Dict[str, str] = {}

        def visit(name: str) -> str:
            if name in name_map:
                return name_map[name]
            if name in reuse and name != root_name:
                # point at the copy already in this library
                name_map[name] = name
                return name
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
                                        x_reflection=ref.x_reflection,
                                        label=ref.label, label_height=ref.label_height,
                                        label_offset=ref.label_offset,
                                        pin=getattr(ref, "pin", "")))
            new_arefs = []
            for ref in src_cell.arefs:
                child_name = visit(ref.cell_name)
                new_arefs.append(ARef(cell_name=child_name, origin=ref.origin,
                                        columns=ref.columns, rows=ref.rows,
                                        col_spacing=ref.col_spacing, row_spacing=ref.row_spacing,
                                        rotation=ref.rotation, magnification=ref.magnification,
                                        x_reflection=ref.x_reflection))
            cell = self.cells[new_name]
            cell.shapes = new_shapes
            cell.srefs = new_srefs
            cell.arefs = new_arefs
            # Keep the library cell's date/time stamps on the copy: the
            # placed part is the same part, so it shows when it was made
            # and last changed in its library, until it is edited here.
            cell.created = src_cell.created
            cell.modified = src_cell.modified
            cell.mark_clean()
            return new_name

        return visit(root_name)

    def __repr__(self):
        return f"Library({self.name!r}, cells={len(self.cells)})"


def merge_libraries(lib_a: "Library", lib_b: "Library", name: Optional[str] = None):
    """Merge two libraries into a new Library containing every cell from
    both -- the whole cell set, not just what's reachable from a top
    cell (unlike Library.import_cell_closure, which is meant for
    pulling in one part and its dependencies from an attached
    reference library). Neither lib_a nor lib_b is modified.

    The merged library's layer table and tech params come from lib_a
    (both libraries share the same fixed 1-micron DBU grid and layer
    numbering scheme, so lib_b's shapes need no rescaling or layer
    remapping to combine cleanly). Cells keep their original names
    unless a name exists in both libraries, in which case lib_b's copy
    is renamed with a numeric suffix (``_2``, ``_3``, ...) until it's
    unique, with every Sref/ARef inside lib_b's own cells updated to
    point at the new name so its hierarchy stays intact.

    Returns (merged_library, renamed) where renamed is a
    {original_name_in_lib_b: new_name} dict for every cell that had to
    be renamed to avoid a collision (empty if there were none).
    """
    merged = Library(name=name or lib_a.name)
    merged.layers = copy.deepcopy(lib_a.layers)
    merged.tech_params = dict(lib_a.tech_params)

    for cell_name, cell in lib_a.cells.items():
        merged.cells[cell_name] = copy.deepcopy(cell)

    used_names = set(merged.cells) | set(lib_b.cells)
    renamed: Dict[str, str] = {}
    for cell_name in lib_b.cells:
        if cell_name not in merged.cells:
            continue
        i = 2
        candidate = f"{cell_name}_{i}"
        while candidate in used_names:
            i += 1
            candidate = f"{cell_name}_{i}"
        renamed[cell_name] = candidate
        used_names.add(candidate)

    for cell_name, cell in lib_b.cells.items():
        new_cell = copy.deepcopy(cell)
        new_cell.name = renamed.get(cell_name, cell_name)
        for ref in new_cell.srefs:
            if ref.cell_name in renamed:
                ref.cell_name = renamed[ref.cell_name]
        for ref in new_cell.arefs:
            if ref.cell_name in renamed:
                ref.cell_name = renamed[ref.cell_name]
        merged.cells[new_cell.name] = new_cell

    if lib_a.top_cell_name:
        merged.top_cell_name = lib_a.top_cell_name
    elif lib_b.top_cell_name:
        merged.top_cell_name = renamed.get(lib_b.top_cell_name, lib_b.top_cell_name)
    else:
        merged.top_cell_name = next(iter(merged.cells), None)

    merged.check_no_cycles()
    return merged, renamed


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
