"""Scripting API: read/write the layout database from Python.

This is the single object exposed to user scripts, both when run
headless (``Session(...)`` in your own script) and inside the GUI's
built-in console (as the name ``hl``). It wraps a Library with the
current-cell/current-layer convenience state a script author expects,
and fires an on_change callback so a GUI can repaint after a script
edits the data.

Example
-------
    from hlayout.scripting.api import Session

    s = Session.new("MYLIB")
    s.new_cell("TOP")
    s.rect(layer=0, x1=0, y1=0, x2=100, y2=50)
    s.polygon(layer=1, points=[(0, 0), (10, 0), (5, 10)])
    s.path(layer=2, points=[(0, 0), (50, 50)], width=5, cap="round")
    s.text(layer=3, origin=(0, 0), text="HELLO")
    s.save("out.gds")
"""

import copy
import math
from contextlib import contextmanager
from typing import Callable, List, Optional, Union

from ..core.cell import Cell
from ..core.errors import LayoutError, LibraryError
from ..core.library import Library, merge_libraries
from ..core.shapes import Polygon, Rectangle
from ..core.maskops import (
    MASK_OPS,
    boolean_op,
    fracture_polygons,
    layer_geoms_flattened,
    layer_to_shapely,
    layer_to_shapely_flattened,
    shapely_to_polygons,
    size_geometry,
)
from ..io.dxfio import write_dxf
from ..io.gdsio import load_gds, save_gds
from ..io.gerberio import write_gerber_layers
from ..io.sidecar import load_design, save_design
from ..io.techfile import (
    load_and_apply_techfile,
    load_bond_rules,
    load_dielectric_rules,
    load_drc_rules,
    load_resistor_geometry_rules,
    load_route_rules,
    load_thin_film_rules,
    resistor_ink_layer,
    save_techfile,
)
from ..core.thinfilm import generate_serpentine_geometry, generate_top_hat_geometry


def _point_seg_dist(p, a, b) -> float:
    """Shortest distance from point ``p`` to the segment ``a``-``b``
    (all (x, y) in DBU)."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 < 1e-9:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _segments_cross(p1, p2, p3, p4) -> bool:
    """True only when segment p1-p2 and segment p3-p4 PROPERLY cross --
    i.e. each segment strictly straddles the other's line. Segments that
    merely share an endpoint, touch at a T, or lie collinear are NOT a
    crossing (a wire bond is allowed to share a landing pad with
    another). Integer/float DBU coordinates."""
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1 = orient(p3, p4, p1)
    d2 = orient(p3, p4, p2)
    d3 = orient(p1, p2, p3)
    d4 = orient(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) \
        and d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0


class Session:
    # An "attached" library is a second (read-mostly) GDSII file kept
    # alongside the current design purely as a source of cells to search
    # and place — like a symbol/part library. Capped at 2 by the spec.
    MAX_ATTACHED_LIBRARIES = 2

    #: How many undo steps to keep around (each is a full deep copy of
    #: the current design -- see the undo/redo section below).
    UNDO_LIMIT = 50

    def __init__(self, library: Library):
        self.library = library
        self.current_cell_name: Optional[str] = library.top_cell_name
        self._on_change: List[Callable[[], None]] = []
        self.attached_libraries: List[Library] = []
        self._attached_paths: List[str] = []
        # Set by open_design() when a sidecar (hlayout.io.sidecar) was
        # found/restored alongside a .gds file -- left at their defaults
        # for a plain Session.new()/Session.open().
        self.had_sidecar = False
        self.attach_errors: List[str] = []
        self._undo_stack: List[Library] = []
        self._redo_stack: List[Library] = []
        self._suspend_checkpoint = False

    # --- construction --------------------------------------------------
    @classmethod
    def new(cls, name: str = "HLIB") -> "Session":
        return cls(Library(name=name))

    @classmethod
    def open(cls, path: str) -> "Session":
        return cls(load_gds(path))

    @classmethod
    def open_design(cls, path: str) -> "Session":
        """Open a .gds file plus its sidecar (see hlayout.io.sidecar),
        restoring the technology (layer stack + tech_params, which is
        also where the Resistor Designer's rules live) and every
        attached reference library the design had when it was last
        saved with save_design() -- if a sidecar is present next to
        ``path``. A .gds with no sidecar (a file from another tool, or
        one saved before this feature existed) opens exactly as
        Session.open() would.

        The returned session has ``attach_errors`` set (a list of
        strings, empty if nothing went wrong) describing any attached
        library that failed to restore -- the rest of the design still
        opens even if one embedded library is corrupt."""
        loaded = load_design(path)
        session = cls(loaded.library)
        session.attached_libraries = loaded.attached_libraries
        session._attached_paths = loaded.attached_paths
        session.attach_errors = loaded.attach_errors
        session.had_sidecar = loaded.had_sidecar
        return session

    def save(self, path: str, library_name: Optional[str] = None) -> None:
        """Write the current design to a GDSII file. If `library_name`
        is given, the library is renamed to it first — the way to save
        the current design out as a distinct, named (part/symbol)
        library, e.g. for later use with attach_library()."""
        if library_name:
            self.library.name = library_name
        self.library.check_bounds()
        save_gds(self.library, path)

    def save_design(self, path: str) -> str:
        """Write the current design to a .gds file *plus* a sidecar
        (see hlayout.io.sidecar) capturing the technology (layer stack +
        tech_params, including any saved Resistor Designer rules) and a
        full embedded copy of every attached reference library, so
        open_design() can restore all of it later. This is what File >
        Save GDS... uses; plain save() (used by Save As Library... and
        scripts publishing a reusable library) still writes just the
        bare .gds, unchanged. Returns the sidecar's path."""
        return save_design(self.library, self.attached_libraries, self._attached_paths, path)

    def export_gerber(self, out_prefix: str, cell_name: Optional[str] = None,
                       layers: Optional[List[int]] = None):
        """Export the design (default: the current cell) as Gerber
        RS-274X -- one flattened file per *visible* layer that has data,
        named ``<out_prefix>_L<NN>_<layer name>.gbr``. Gerber is a
        manufacturing output format, not this tool's database format
        (that's still GDSII, via save()/File > Save As Library...), so
        every Sref is flattened rather than round-tripped, and Text
        shapes are skipped (Gerber has no text primitive).

        `layers`, if given, restricts export to that subset of layer
        numbers. Returns (written, skipped_text) -- see
        hlayout.io.gerberio.write_gerber_layers for details."""
        name = cell_name or self.current_cell_name
        if name is None:
            raise LibraryError("no current cell to export")
        return write_gerber_layers(self.library, name, out_prefix, layers=layers)

    def run_gerber_export_batch(self, jobs=None, cell_name: Optional[str] = None):
        """Run a saved series of Gerber export jobs -- e.g. one job per
        fab house or board revision, each with its own layer subset and
        output prefix -- in one action, instead of using File > Export
        Gerber... once per job by hand.

        `jobs` is a list of hlayout.io.gerberio.GerberExportJob
        instances or plain dicts (each with at least out_prefix, e.g.
        {"name": "FabHouseA", "out_prefix": "out/fabA/board",
        "layers": [10, 20, 30]}); defaults to whatever's saved in the
        technology file (Tools > Gerber Export Sets...) via
        load_gerber_export_jobs(). Unlike run_mask_op_batch(), job
        order doesn't matter -- each job's output is independent of
        every other job's.

        `cell_name` defaults to the current cell (same convention as
        export_gerber() itself) and is used for every job.

        Gerber export never modifies the design (it only writes files
        to disk), so unlike run_mask_op_batch() this needs no undo
        checkpoint.

        Returns [(job, written, skipped_text), ...] in the order run --
        see export_gerber()/hlayout.io.gerberio.write_gerber_layers for
        what `written`/`skipped_text` mean."""
        from ..io.gerberio import GerberExportJob
        from ..io.techfile import load_gerber_export_jobs

        if jobs is None:
            jobs = load_gerber_export_jobs(self.library)
        resolved = [j if isinstance(j, GerberExportJob) else GerberExportJob.from_dict(j) for j in jobs]

        results = []
        for job in resolved:
            written, skipped_text = self.export_gerber(
                job.out_prefix, cell_name=cell_name, layers=job.layers,
            )
            results.append((job, written, skipped_text))
        return results

    def export_dxf(self, path: str, cell_name: Optional[str] = None,
                    layers: Optional[List[int]] = None):
        """Export the design (default: the current cell) as a single DXF
        (AutoCAD R2000) file at ``path``, with one DXF layer per hlayout
        layer that's visible and has data. Like export_gerber(), every
        Sref is flattened rather than round-tripped -- but unlike Gerber,
        DXF has a native TEXT entity, so Text shapes are included, not
        skipped.

        `layers`, if given, restricts export to that subset of layer
        numbers. Returns (written_layers, entity_count) -- see
        hlayout.io.dxfio.write_dxf for details."""
        name = cell_name or self.current_cell_name
        if name is None:
            raise LibraryError("no current cell to export")
        return write_dxf(self.library, name, path, layers=layers)

    def run_dxf_export_batch(self, jobs=None, cell_name: Optional[str] = None):
        """Run a saved series of DXF export jobs -- e.g. one job per
        downstream tool/consumer, each with its own layer subset and
        output file -- in one action, instead of using File > Export
        DXF... once per job by hand.

        `jobs` is a list of hlayout.io.dxfio.DxfExportJob instances or
        plain dicts (each with at least path, e.g. {"name": "Fab",
        "path": "out/board.dxf", "layers": [10, 20, 30]}); defaults to
        whatever's saved in the technology file (Tools > DXF Export
        Sets...) via load_dxf_export_jobs(). Like
        run_gerber_export_batch(), job order doesn't matter -- each
        job's output is independent of every other job's.

        `cell_name` defaults to the current cell (same convention as
        export_dxf() itself) and is used for every job.

        DXF export never modifies the design (it only writes a file to
        disk), so unlike run_mask_op_batch() this needs no undo
        checkpoint.

        Returns [(job, written_layers, entity_count), ...] in the order
        run -- see export_dxf()/hlayout.io.dxfio.write_dxf for what
        `written_layers`/`entity_count` mean."""
        from ..io.dxfio import DxfExportJob
        from ..io.techfile import load_dxf_export_jobs

        if jobs is None:
            jobs = load_dxf_export_jobs(self.library)
        resolved = [j if isinstance(j, DxfExportJob) else DxfExportJob.from_dict(j) for j in jobs]

        results = []
        for job in resolved:
            written_layers, entity_count = self.export_dxf(
                job.path, cell_name=cell_name, layers=job.layers,
            )
            results.append((job, written_layers, entity_count))
        return results

    # --- technology file ---------------------------------------------------
    def load_technology(self, path: str) -> dict:
        """Load a technology file (JSON; see hlayout.io.techfile) and
        apply its layer stack + global parameters onto the current
        library, in place. The usual point to do this is right at the
        start of a design, before drawing anything, so the layer names/
        colours/etc. for the target process are in place from the
        start -- but it can be called at any time. Returns the parsed
        file contents."""
        self._checkpoint()
        return load_and_apply_techfile(self.library, path)

    def save_technology(self, path: str) -> None:
        """Write the current library's layer stack + tech_params out as
        a technology file, independent of saving the design itself --
        e.g. to capture layer edits made in this design as the starting
        point for the next one."""
        save_techfile(self.library, path)

    # --- attached libraries ----------------------------------------------
    def attach_library(self, path: str) -> Library:
        """Load a GDSII file as an attached reference library, alongside
        (not merged into) the current design. Up to MAX_ATTACHED_LIBRARIES
        (2) may be attached at once; detach one with detach_library()
        to make room for another."""
        if len(self.attached_libraries) >= self.MAX_ATTACHED_LIBRARIES:
            raise LibraryError(
                f"at most {self.MAX_ATTACHED_LIBRARIES} attached libraries are supported; "
                "call detach_library() first"
            )
        lib = load_gds(path)
        self.attached_libraries.append(lib)
        self._attached_paths.append(path)
        return lib

    def detach_library(self, index: int) -> None:
        if index < 0 or index >= len(self.attached_libraries):
            raise LibraryError(f"no attached library at index {index}")
        del self.attached_libraries[index]
        del self._attached_paths[index]

    def list_attached_libraries(self):
        """[{'index', 'name', 'path', 'cells'}, ...] for each attached library."""
        return [
            {"index": i, "name": lib.name, "path": path, "cells": len(lib.cells)}
            for i, (lib, path) in enumerate(zip(self.attached_libraries, self._attached_paths))
        ]

    def merge_gds(self, path_a: str, path_b: str, out_path: str,
                  library_name: Optional[str] = None) -> dict:
        """Merge two GDSII files into a third, combining every cell from
        both. This is a plain file-to-file operation -- it reads
        path_a and path_b fresh and writes the merged result straight
        to out_path, without touching the session's current design
        (self.library) or its attached libraries at all.

        library_name defaults to path_a's own library name. Cells keep
        their original names unless the same name exists in both
        files, in which case path_b's copy is renamed to avoid the
        collision -- see hlayout.core.library.merge_libraries for the
        exact rule.

        Returns {'cells': <total cell count written to out_path>,
                 'renamed': {<original name from path_b>: <new name>, ...}}
        -- 'renamed' lists every cell from path_b that had to be
        renamed to avoid clashing with a same-named cell from path_a
        (empty if there were no collisions).
        """
        lib_a = load_gds(path_a)
        lib_b = load_gds(path_b)
        merged, renamed = merge_libraries(lib_a, lib_b, name=library_name)
        merged.check_bounds()
        save_gds(merged, out_path)
        return {"cells": len(merged.cells), "renamed": renamed}

    def create_via_cell(self, layer1, layer2, shape: str = "square",
                         size: float = 100, index: Optional[int] = None,
                         overwrite: bool = False, name: Optional[str] = None,
                         dielectric_layer=None, dielectric_window: Optional[float] = None):
        """Create a via cell -- a same-size square or round pad drawn on
        both layer1 and layer2, representing the contact area where a
        via connects them -- directly inside an attached reference
        library, and save that library back to its own file on disk in
        place. This is how a via (or any other new part) gets added to
        a shared parts library, as opposed to session.sref()/place_cell()
        which only ever place instances of cells that already exist.

        layer1/layer2 accept a layer number or an existing layer name,
        resolved against the *current* library's layer table (an
        attached library's own cells are addressed by plain layer
        number in the file, same as any other GDSII data). shape is
        "square" or "round". size is the pad's full width/diameter, in
        DBU (1 DBU = 1 micron).

        `dielectric_layer`/`dielectric_window`, if both given, also draw
        a same-shape opening of size `dielectric_window` DBU on
        `dielectric_layer`, centred at the cell's origin same as the two
        pads -- the window cut in the dielectric between the two
        conductor layers that actually defines the electrical contact
        area in a real process (must differ from both layer1 and
        layer2). Leave both unset (the default) for a bare two-pad via
        with no dielectric window. They must be given together --
        passing only one raises.

        index picks which attached library (0 or 1, see
        list_attached_libraries()); if exactly one is attached it's
        used automatically. The new cell is named `name` if given, else
        f"VIA_Layer{n1}_to_Layer{n2}" -- raises if that name already
        exists in the target library, unless overwrite=True (which
        replaces it).

        Returns (cell_name, path) for the via cell just written. The
        attached library object in memory is updated in place too, so
        Create > Cell Instance / Find & Place Cell... see the new via
        immediately without needing to re-attach.
        """
        if not self.attached_libraries:
            raise LibraryError(
                "no attached library -- attach one first (Setup > Attach Library...)"
            )
        if index is None:
            if len(self.attached_libraries) > 1:
                raise LibraryError(
                    "more than one attached library; pass index=0 or index=1 to pick one"
                )
            index = 0
        if index < 0 or index >= len(self.attached_libraries):
            raise LibraryError(f"no attached library at index {index}")

        shape = shape.lower()
        if shape not in ("square", "round"):
            raise LibraryError("shape must be 'square' or 'round'")
        if size <= 0:
            raise LibraryError("size must be a positive number of DBU")

        n1 = self.library.layers.resolve(layer1)
        n2 = self.library.layers.resolve(layer2)

        n3 = None
        if (dielectric_layer is None) != (dielectric_window is None):
            raise LibraryError(
                "dielectric_layer and dielectric_window must be given together"
            )
        if dielectric_layer is not None:
            n3 = self.library.layers.resolve(dielectric_layer)
            if n3 in (n1, n2):
                raise LibraryError(
                    "dielectric_layer must be different from layer1 and layer2"
                )
            if dielectric_window <= 0:
                raise LibraryError("dielectric_window must be a positive number of DBU")

        name = name or f"VIA_Layer{n1}_to_Layer{n2}"

        lib = self.attached_libraries[index]
        if name in lib.cells:
            if not overwrite:
                raise LibraryError(
                    f"{name!r} already exists in the attached library; pass overwrite=True to replace it"
                )
            lib.delete_cell(name)

        cell = lib.new_cell(name)
        half = size / 2.0
        for n in (n1, n2):
            if shape == "square":
                cell.add_rectangle(n, -half, -half, half, half)
            else:
                cell.add_circle(n, (0, 0), half, segments=32)
        if n3 is not None:
            dhalf = dielectric_window / 2.0
            if shape == "square":
                cell.add_rectangle(n3, -dhalf, -dhalf, dhalf, dhalf)
            else:
                cell.add_circle(n3, (0, 0), dhalf, segments=32)

        path = self._attached_paths[index]
        save_gds(lib, path)
        self._notify()
        return name, path

    def create_pad_cell(self, name: str, conductor_layer, paste_layer,
                         length: float, width: float, margin: float, *,
                         rows: int = 1, cols: int = 1,
                         pitch_x: Optional[float] = None, pitch_y: Optional[float] = None,
                         index: Optional[int] = None, overwrite: bool = False):
        """Create a pad cell -- a rectangular conductor pad with a
        smaller solder paste rectangle centred on it (the paste layer
        undersized from the conductor by `margin` DBU on every side) --
        directly inside an attached reference library, and save that
        library back to its own file on disk in place. Same idea as
        create_via_cell(), but for a single- or multi-pad footprint
        (e.g. a connector) sized by length/width rather than a fixed
        square/round via.

        conductor_layer/paste_layer accept a layer number or an
        existing layer name, resolved against the *current* library's
        layer table. length is the pad's size along X, width along Y,
        both in DBU (1 DBU = 1 micron); margin is how much smaller the
        paste rectangle is than the conductor rectangle on each side,
        so margin must be less than half of both length and width.

        rows x cols lays out a grid of identical pads (rows along Y,
        cols along X), spaced pitch_y/pitch_x apart (pad-centre to
        pad-centre, in DBU) and centred as a whole on the cell origin
        -- e.g. for a 2-pin connector footprint. pitch_x is required
        when cols > 1, pitch_y when rows > 1; both are ignored for a
        single pad (rows == cols == 1).

        index picks which attached library (0 or 1, see
        list_attached_libraries()); if exactly one is attached it's
        used automatically. Raises if `name` already exists in the
        target library, unless overwrite=True (which replaces it).

        A genuine 2-pad footprint (rows*cols == 2, e.g. a simple
        connector or discrete-part land pattern) is unambiguous, so its
        two conductor pads are auto-numbered pin "1"/"2" (lower X, then
        lower Y, is pin "1") -- see Rectangle.pin. Any other grid size
        is left unnumbered (pin=""); number those by hand via Edit >
        Number Pins... or Session.set_pin(), since which physical pad is
        pin 1 on a 3+ pin footprint depends on the part's actual pinout,
        not just its geometry.

        Returns (cell_name, path, pad_count) for the pad cell just
        written. The attached library object in memory is updated in
        place too, so Create > Cell Instance / Find & Place Cell... see
        the new pad immediately without needing to re-attach.
        """
        if not self.attached_libraries:
            raise LibraryError(
                "no attached library -- attach one first (Setup > Attach Library...)"
            )
        if index is None:
            if len(self.attached_libraries) > 1:
                raise LibraryError(
                    "more than one attached library; pass index=0 or index=1 to pick one"
                )
            index = 0
        if index < 0 or index >= len(self.attached_libraries):
            raise LibraryError(f"no attached library at index {index}")

        name = (name or "").strip()
        if not name:
            raise LibraryError("a cell name is required")
        if length <= 0 or width <= 0:
            raise LibraryError("length and width must be positive numbers")
        if margin < 0:
            raise LibraryError("margin must not be negative")
        if margin * 2 >= length or margin * 2 >= width:
            raise LibraryError(
                "margin is too large -- the solder paste rectangle would have "
                "zero or negative size; margin must be less than half of both "
                "length and width"
            )
        rows = int(rows)
        cols = int(cols)
        if rows < 1 or cols < 1:
            raise LibraryError("rows and cols must be at least 1")
        if cols > 1:
            if not pitch_x or pitch_x <= 0:
                raise LibraryError("pitch_x must be a positive number when cols > 1")
        if rows > 1:
            if not pitch_y or pitch_y <= 0:
                raise LibraryError("pitch_y must be a positive number when rows > 1")

        n_cond = self.library.layers.resolve(conductor_layer)
        n_paste = self.library.layers.resolve(paste_layer)

        lib = self.attached_libraries[index]
        if name in lib.cells:
            if not overwrite:
                raise LibraryError(
                    f"{name!r} already exists in the attached library; pass overwrite=True to replace it"
                )
            lib.delete_cell(name)

        cell = lib.new_cell(name)
        half_l, half_w = length / 2.0, width / 2.0
        paste_half_l, paste_half_w = half_l - margin, half_w - margin

        centers = []
        for r in range(rows):
            cy = (r - (rows - 1) / 2.0) * (pitch_y or 0)
            for c in range(cols):
                cx = (c - (cols - 1) / 2.0) * (pitch_x or 0)
                centers.append((cx, cy))

        # A genuine 2-pad footprint is unambiguous -- auto-number it.
        # Any other pad count is left unpinned; see the docstring.
        pins_by_index = {}
        if len(centers) == 2:
            order = sorted(range(2), key=lambda i: centers[i])
            pins_by_index[order[0]] = "1"
            pins_by_index[order[1]] = "2"

        pad_count = 0
        for i, (cx, cy) in enumerate(centers):
            cell.add_rectangle(n_cond, cx - half_l, cy - half_w, cx + half_l, cy + half_w,
                                pin=pins_by_index.get(i, ""))
            cell.add_rectangle(n_paste, cx - paste_half_l, cy - paste_half_w,
                                cx + paste_half_l, cy + paste_half_w)
            pad_count += 1

        path = self._attached_paths[index]
        save_gds(lib, path)
        self._notify()
        return name, path, pad_count

    def _resolve_source(self, source: Union[str, int, None]) -> Library:
        if source in ("current", None):
            return self.library
        idx = int(source)
        if idx < 0 or idx >= len(self.attached_libraries):
            raise LibraryError(f"no attached library at index {idx}")
        return self.attached_libraries[idx]

    def list_library(self, source: Union[str, int, None] = "current"):
        """List the cells of `source` ('current', or an attached library's
        0-based index): [{'name', 'shapes', 'srefs', 'bbox'}, ...]."""
        lib = self._resolve_source(source)
        return [
            {"name": name, "shapes": len(cell.shapes), "srefs": len(cell.srefs),
             "arefs": len(cell.arefs), "bbox": cell.bbox()}
            for name, cell in lib.cells.items()
        ]

    def find_cells(self, query: str):
        """Case-insensitive substring search for cell (Sref target) names
        across the current library and every attached library. Returns
        [{'source', 'name'}, ...] where source is 'current' or an
        attached library's index."""
        query_l = query.lower()
        matches = [{"source": "current", "name": n} for n in self.library.cells if query_l in n.lower()]
        for i, lib in enumerate(self.attached_libraries):
            matches.extend({"source": i, "name": n} for n in lib.cells if query_l in n.lower())
        return matches

    # --- change notification (used by the GUI to repaint) ---------------
    def on_change(self, callback: Callable[[], None]) -> None:
        self._on_change.append(callback)

    def _notify(self) -> None:
        for cb in self._on_change:
            cb()

    # --- undo/redo --------------------------------------------------------
    # A whole-design snapshot stack: _checkpoint() is called once, right
    # before a mutating edit actually changes anything, by every method
    # below that edits the current design (add/remove/move/rotate a
    # shape or sref, bulk layer remap, loading a technology file's layer
    # stack) as well as the GUI's own mouse-driven edits (drag-to-move,
    # the Edit Selected... dialog -- see LayoutCanvas.checkpoint in
    # hlayout/gui/canvas.py). undo()/redo() just swap the current
    # design's contents for a stored snapshot; simple and always
    # correct, at the cost of a full deep copy per edit -- fine at the
    # design sizes this tool targets (see "Canvas performance" in the
    # README).
    #
    # Deliberately scoped to the CURRENT design only: attached libraries
    # (read-mostly reference data, see attach_library()) are never
    # snapshotted or touched by undo/redo, and New/Open GDS (which
    # replace self.library wholesale with an unrelated document) clear
    # the history instead of trying to undo into it -- see
    # clear_undo_history().
    def _checkpoint(self) -> None:
        if self._suspend_checkpoint:
            return
        self._undo_stack.append(copy.deepcopy(self.library))
        if len(self._undo_stack) > self.UNDO_LIMIT:
            del self._undo_stack[0]
        self._redo_stack.clear()

    @contextmanager
    def _batch(self):
        """Group several mutations (e.g. place_cell()'s cell-import
        followed by its sref) behind a single undo checkpoint, so one
        user-facing action is one undo step rather than several."""
        self._checkpoint()
        prev = self._suspend_checkpoint
        self._suspend_checkpoint = True
        try:
            yield
        finally:
            self._suspend_checkpoint = prev

    def checkpoint(self) -> None:
        """Public entry point for external callers -- specifically the
        GUI's own mouse-driven edits (drag-to-move, Edit Selected...,
        Delete Selected; see LayoutCanvas.checkpoint in
        hlayout/gui/canvas.py) -- that mutate self.library directly
        rather than through one of this class's own methods, and so
        need to push an undo checkpoint themselves before doing so."""
        self._checkpoint()

    def batch(self):
        """Public entry point for external callers that need to group
        several of this class's own mutating calls behind a single undo
        checkpoint -- e.g. the GUI's two-layer Route tool (Create >
        Route (2-Layer)) committing a path segment and then dropping a
        via, which should undo together as one step. Use as
        ``with session.batch(): ...``."""
        return self._batch()

    def drop_via(self, x, y, layer_a, layer_b, size: float = 100, shape: str = "square",
                 dielectric_layer=None, dielectric_window: float = None):
        """Draw a same-size via pad directly on both layer_a and
        layer_b, centred at (x, y) -- the inline equivalent of Tools >
        Via Wizard...'s reusable via *cell*, but drawn straight into the
        current cell with no attached-library/sref indirection. This is
        what the two-layer Route tool (Create > Route (2-Layer)) drops
        each time you hit Space to switch conductor layers mid-route.

        `dielectric_layer`/`dielectric_window`, if both given, also draw
        a same-shape opening of size `dielectric_window` on
        `dielectric_layer`, centred at the same point -- the window cut
        in the dielectric between the two conductor layers that actually
        defines the electrical contact area in a real process. A real
        via always has this window; it's normally sized AT OR ABOVE
        `size` (the conductor pads land within/over the opening), which
        is why it's a separate size rather than reusing `size`. Leave
        either one unset (the default) only for a bare two-pad via with
        no dielectric window at all -- e.g. quick scripted geometry that
        isn't modelling a real dielectric stack.

        Every shape dropped -- both pads, plus the window if requested --
        is added as a single undo step."""
        with self._batch():
            for layer in (layer_a, layer_b):
                if shape == "round":
                    self.circle(layer, (x, y), size / 2)
                else:
                    half = size / 2
                    self.rect(layer, x - half, y - half, x + half, y + half)
            if dielectric_layer is not None and dielectric_window:
                if shape == "round":
                    self.circle(dielectric_layer, (x, y), dielectric_window / 2)
                else:
                    half = dielectric_window / 2
                    self.rect(dielectric_layer, x - half, y - half, x + half, y + half)

    def generate_resistor(self, x, y, body_width, body_length, rotation: float = 0.0,
                           ink_rsh=None, ref_des=None, overlap_a=None, pad_b=None,
                           space_c=None, glaze_oversize=None, resistor_layer=None,
                           conductor_layer=None, glaze_layer=None, doc_layer=None,
                           keepout_layer=None, keepout_oversize=None,
                           label_height=None, cell_name=None) -> str:
        """Build a complete thick-film resistor structure -- the resistor
        ink body, its protective glaze overprint, its two end conductor
        pads, and (if ``ref_des`` is given) a reference-designator label
        -- as a brand-new CELL in this library, then place ONE instance
        of it (an Sref) in the current cell at (x, y), rotated
        ``rotation`` degrees. ``body_width``/``body_length`` are
        normally the ``body_width``/``selected.body_length`` of a sized
        Resistor from Tools > Thick-Film Resistor Designer... .

        Building each resistor as its own cell + Sref (instead of
        drawing its shapes straight into the current cell every call)
        means a placed resistor is one selectable/movable/rotatable
        object on the canvas -- click it once instead of marquee-
        selecting 4-5 separate shapes -- matching how the rest of the
        tool already treats reusable parts (Tools > Via Wizard...'s via
        cells, attached-library parts placed via PLACE/find-and-place).
        The new cell is named after ``cell_name`` if given, else
        ``ref_des`` (e.g. "R1"), else "RESISTOR" -- de-duplicated
        against any existing cell of that name via the library's normal
        unique-name suffixing ("R1_1", "R1_2", ...), so placing several
        resistors (even several with the same ref_des, e.g. from
        scripted iteration) never silently overwrites another
        resistor's cell. Returns the new cell's actual name.

        Geometry (see hlayout.io.techfile's resistor-geometry-rules
        docstring for the full diagram): the resistor ink extends
        ``overlap_a`` beyond body_length at each end (so it overlaps the
        conductor pad for a reliable contact); each pad is ``pad_b``
        long beyond that overlap, and ``space_c`` wider than the ink on
        each side. The glaze overprint is the same shape as the ink
        body, oversized by ``glaze_oversize`` on *all four* sides (both
        ends and both long edges) -- size = (body_width + 2*D) x
        (body_length + 2*A + 2*D) -- on its own layer, so it fully
        covers the resistor (and a manufacturing margin around it) after
        trim.

        ``ink_rsh`` -- the sheet resistance (Ohm/sq) of the ink actually
        used for this resistor, normally a solution's ``ink_rsh`` --
        picks which layer the ink is drawn on via this design's saved
        per-ink layer map (hlayout.io.techfile.resistor_ink_layer());
        pass ``resistor_layer`` explicitly to bypass that lookup
        entirely. ``ref_des``, if given (e.g. "R1"), is stored on the
        placed Sref's ``label`` -- the same per-instance ref designator
        every other placed part carries: the canvas floats it at the
        instance centre (draggable, gated by the ref-des annotation
        layer's visibility) and netlist extraction reads it. Nothing is
        baked into the device cell. ``doc_layer``/``label_height`` are
        accepted for backward compatibility but ignored.

        ``keepout_layer``/``keepout_oversize`` add a DIELECTRIC KEEP-OUT
        rectangle spanning the gap *between the two conductor terminals*
        (x within body_length, y at the full pad width), grown by
        ``keepout_oversize`` (E) on all four sides -- size = (body_width
        + 2*C + 2*E) x (body_length + 2*E). It marks where the
        inter-layer dielectric must be cleared so the printed ink fires
        on a bare surface (subtract this layer from the dielectric). A
        keep-out layer of -1 (the default) draws nothing.

        Any of overlap_a/pad_b/space_c/glaze_oversize/keepout_oversize/
        conductor_layer/glaze_layer/keepout_layer left as None
        falls back to this design's saved Setup > Resistor Geometry
        Rules... (Library.tech_params, via
        hlayout.io.techfile.load_resistor_geometry_rules()).

        Building the device cell and placing its Sref are both part of
        a single undo step."""
        parent_cell_name = self.current_cell_name
        if parent_cell_name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")

        rules = load_resistor_geometry_rules(self.library)
        a = float(overlap_a) if overlap_a is not None else rules["overlap_a"]
        b = float(pad_b) if pad_b is not None else rules["pad_b"]
        c = float(space_c) if space_c is not None else rules["space_c"]
        d = float(glaze_oversize) if glaze_oversize is not None else rules["glaze_oversize"]
        r_layer = resistor_layer if resistor_layer is not None else resistor_ink_layer(rules, ink_rsh)
        c_layer = conductor_layer if conductor_layer is not None else rules["conductor_layer"]
        g_layer = glaze_layer if glaze_layer is not None else rules["glaze_layer"]
        # `doc_layer`/`label_height` are accepted for backward
        # compatibility but no longer used: the ref-des is now the same
        # per-instance floating annotation every other placed part gets
        # (SRef.label, drawn at the instance centre, draggable), not a
        # baked Text shape on a layer.
        k_layer = keepout_layer if keepout_layer is not None else rules["keepout_layer"]
        e = float(keepout_oversize) if keepout_oversize is not None else float(rules["keepout_oversize"])

        half_l = body_length / 2.0
        half_w = body_width / 2.0
        pad_half_w = half_w + c

        base_name = cell_name or (str(ref_des) if ref_des else "RESISTOR")
        device_name = self.library._unique_cell_name(base_name)

        with self._batch():
            self.new_cell(device_name)  # also switches current_cell_name
            self.rect(r_layer, -(half_l + a), -half_w, half_l + a, half_w)
            self.rect(g_layer, -(half_l + a + d), -(half_w + d), half_l + a + d, half_w + d)
            # A resistor is an inherently unambiguous 2-terminal part, so
            # its pads are auto-numbered the same way create_pad_cell()
            # numbers a 2-pad footprint: lower X is pin "1".
            self.rect(c_layer, -(half_l + a + b), -pad_half_w, -half_l, pad_half_w, pin="1")
            self.rect(c_layer, half_l, -pad_half_w, half_l + a + b, pad_half_w, pin="2")
            if k_layer is not None and int(k_layer) >= 0:
                # dielectric keep-out: the gap between the two terminals
                # (body_length long, full pad width), grown by E.
                self.rect(int(k_layer), -(half_l + e), -(pad_half_w + e),
                          half_l + e, pad_half_w + e)
            self.use_cell(parent_cell_name)
            # The ref-des is the placed instance's own SRef.label -- the
            # same per-instance floating annotation every other placed
            # part gets: drawn at the instance centre, draggable, gated by
            # the ref-des annotation layer's visibility, and read by
            # netlist extraction. Nothing is baked into the device cell.
            self.sref(device_name, (x, y), rotation=rotation, label=str(ref_des) if ref_des else "")
        return device_name

    def generate_serpentine_resistor(self, x, y, sol, rotation: float = 0.0,
                                      ref_des=None, resistor_layer=None, pad_layer=None,
                                      doc_layer=None, label_height=None, cell_name=None) -> str:
        """Build a thin-film serpentine resistor -- link area (coarse
        trim), serpentine area (fine trim), a connecting bridge, and
        two probe pads -- as a new cell, then place one Sref of it at
        (x, y) rotated ``rotation`` degrees. ``sol`` is a
        hlayout.core.thinfilm.SerpentineSolution from
        hlayout.core.thinfilm.solve_serpentine() (normally via Tools >
        Thin-Film Resistor Designer...). Layers not given fall back to
        this design's saved Setup > Thin-Film Resistor Rules...
        (hlayout.io.techfile.load_thin_film_rules()). Building the
        device cell and placing its Sref are one undo step. Returns
        the new cell's name."""
        parent_cell_name = self.current_cell_name
        if parent_cell_name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")

        rules = load_thin_film_rules(self.library)
        r_layer = resistor_layer if resistor_layer is not None else rules["resistor_layer"]
        p_layer = pad_layer if pad_layer is not None else rules["pad_layer"]
        # doc_layer/label_height accepted for compatibility, unused: the
        # ref-des is the placed instance's SRef.label (see generate_resistor).

        geo = generate_serpentine_geometry(sol)
        all_polys = geo["link_polys"] + [geo["bridge_poly"]] + geo["serpentine_polys"]
        min_x, min_y, max_x, max_y = geo["outline_points"][0][0], geo["outline_points"][0][1], \
            geo["outline_points"][2][0], geo["outline_points"][2][1]

        pad_clear = rules["min_probe_pad_clearance"]
        pad_len = max(rules["probe_pad_min"], pad_clear)
        pad_width = max(sol.trace_width * 3, pad_clear * 2)
        # Figure 5's "Artwork Overlap" minimum -- a pad that only
        # abuts the resistor element (zero overlap) can lose contact
        # entirely under ordinary layer-to-layer alignment tolerance,
        # so each pad is drawn reaching past the resistor's own edge
        # by this much rather than stopping exactly at it. Capped so a
        # very short leg can't have its pads overlap each other.
        overlap = min(rules["min_artwork_overlap"], (max_x - min_x) / 2)

        base_name = cell_name or (str(ref_des) if ref_des else "SERPENTINE")
        device_name = self.library._unique_cell_name(base_name)

        with self._batch():
            self.new_cell(device_name)
            for poly in all_polys:
                self.polygon(r_layer, poly)
            # Probe pads at each end of the combined footprint (link
            # area's outer edge, serpentine area's outer edge),
            # reaching `overlap` past that edge into the resistor
            # element itself, centered on that zone's own vertical
            # middle.
            # Two-terminal part -- auto-number pads same as generate_resistor().
            self.rect(p_layer, min_x - pad_len, -pad_width / 2, min_x + overlap, pad_width / 2, pin="1")
            self.rect(p_layer, max_x - overlap, -pad_width / 2, max_x + pad_len, pad_width / 2, pin="2")
            self.use_cell(parent_cell_name)
            self.sref(device_name, (x, y), rotation=rotation, label=str(ref_des) if ref_des else "")
        return device_name

    def generate_top_hat_resistor(self, x, y, sol, rotation: float = 0.0,
                                   ref_des=None, resistor_layer=None, pad_layer=None,
                                   doc_layer=None, label_height=None, cell_name=None) -> str:
        """Build a thin-film top-hat (dog-bone) resistor -- a narrow
        trimmable bar between two wide contact pads -- as a new cell,
        then place one Sref of it at (x, y) rotated ``rotation``
        degrees. ``sol`` is a hlayout.core.thinfilm.TopHatSolution from
        hlayout.core.thinfilm.solve_top_hat() (normally via Tools >
        Thin-Film Resistor Designer...). Layers not given fall back to
        this design's saved Setup > Thin-Film Resistor Rules...
        (hlayout.io.techfile.load_thin_film_rules()). Building the
        device cell and placing its Sref are one undo step. Returns
        the new cell's name."""
        parent_cell_name = self.current_cell_name
        if parent_cell_name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")

        rules = load_thin_film_rules(self.library)
        r_layer = resistor_layer if resistor_layer is not None else rules["resistor_layer"]
        p_layer = pad_layer if pad_layer is not None else rules["pad_layer"]
        # doc_layer/label_height accepted for compatibility, unused (see
        # generate_resistor).

        # Figure 5's "Artwork Overlap" minimum -- see the matching
        # comment in generate_serpentine_resistor() above.
        overlap = min(rules["min_artwork_overlap"], sol.bar_length / 2)
        geo = generate_top_hat_geometry(sol, overlap=overlap)

        base_name = cell_name or (str(ref_des) if ref_des else "TOPHAT")
        device_name = self.library._unique_cell_name(base_name)

        with self._batch():
            self.new_cell(device_name)
            self.polygon(r_layer, geo["bar_points"])
            if geo["crown_points"] is not None:
                self.polygon(r_layer, geo["crown_points"])
            # Two-terminal part -- auto-number pads same as generate_resistor()
            # (pad_a is the lower-X pad, see generate_top_hat_geometry()).
            self.polygon(p_layer, geo["pad_a_points"], pin="1")
            self.polygon(p_layer, geo["pad_b_points"], pin="2")
            self.use_cell(parent_cell_name)
            self.sref(device_name, (x, y), rotation=rotation, label=str(ref_des) if ref_des else "")
        return device_name

    def clear_undo_history(self) -> None:
        """Called when self.library is replaced wholesale (New, Open
        GDS) rather than edited -- there's nothing meaningful to undo
        into a different document."""
        self._undo_stack.clear()
        self._redo_stack.clear()

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        self._redo_stack.append(copy.deepcopy(self.library))
        self._restore(self._undo_stack.pop())
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        self._undo_stack.append(copy.deepcopy(self.library))
        self._restore(self._redo_stack.pop())
        return True

    def _restore(self, snapshot: Library) -> None:
        # Copies the snapshot's contents into the *existing* self.library
        # object (rather than replacing self.library outright) so every
        # other reference to it -- the GUI canvas, the layer panel, any
        # open view windows -- sees the restored state immediately,
        # without needing to be individually re-pointed at a new object.
        self.library.name = snapshot.name
        # Restore each layer's style INTO the existing Layer objects
        # rather than swapping the whole LayerTable: the GUI layer panel
        # binds its per-row fill/colour/visibility callbacks to specific
        # Layer instances, and a wholesale swap would orphan them -- the
        # panel would still *show* the restored state (sync() re-reads by
        # number) but toggling a row afterwards would write to a dead
        # object and nothing on the canvas would change.
        for n in range(len(snapshot.layers)):
            src = snapshot.layers[n]
            dst = self.library.layers[n]
            dst.name = src.name
            dst.color = src.color
            dst.stipple = src.stipple
            dst.fill = src.fill
            dst.visible = src.visible
            dst.selectable = src.selectable
        self.library.cells = snapshot.cells
        self.library.top_cell_name = snapshot.top_cell_name
        self.library.tech_params = snapshot.tech_params
        if self.current_cell_name not in self.library.cells:
            self.current_cell_name = self.library.top_cell_name
        self._notify()

    # --- cell/layer context ---------------------------------------------
    def new_cell(self, name: str) -> Cell:
        self._checkpoint()
        cell = self.library.new_cell(name)
        self.current_cell_name = name
        self._notify()
        return cell

    def use_cell(self, name: str) -> Cell:
        cell = self.library.get_cell(name)
        self.current_cell_name = name
        return cell

    @property
    def cell(self) -> Cell:
        if self.current_cell_name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        return self.library.get_cell(self.current_cell_name)

    def set_layer_style(self, layer, **kwargs):
        """layer accepts a layer number or an existing layer name."""
        number = self.library.layers.resolve(layer)
        return self.library.layers.set_style(number, **kwargs)

    # --- shape shortcuts (operate on the current cell) -------------------
    # `layer` accepts either a layer number (0-63) or a layer name (e.g.
    # "Thick-Film Conductor"), resolved via the library's layer table.
    def rect(self, layer, x1, y1, x2, y2, datatype: int = 0, pin: str = ""):
        self._checkpoint()
        layer = self.library.layers.resolve(layer)
        shape = self.cell.add_rectangle(layer, x1, y1, x2, y2, datatype=datatype, pin=pin)
        self._notify()
        return shape

    def polygon(self, layer, points, datatype: int = 0, pin: str = ""):
        self._checkpoint()
        layer = self.library.layers.resolve(layer)
        shape = self.cell.add_polygon(layer, points, datatype=datatype, pin=pin)
        self._notify()
        return shape

    def circle(self, layer, center, radius, segments: int = 32, datatype: int = 0):
        """A circle stored as a regular Polygon; segments must be one of
        8, 16, 32, or 64."""
        self._checkpoint()
        layer = self.library.layers.resolve(layer)
        shape = self.cell.add_circle(layer, center, radius, segments=segments, datatype=datatype)
        self._notify()
        return shape

    def path(self, layer, points, width: int, cap: str = "flush", datatype: int = 0):
        self._checkpoint()
        layer = self.library.layers.resolve(layer)
        shape = self.cell.add_path(layer, points, width, cap=cap, datatype=datatype)
        self._notify()
        return shape

    def text(self, layer, origin, text: str, height: int = 10, font: int = 0,
              rotation: float = 0.0, datatype: int = 0):
        self._checkpoint()
        layer = self.library.layers.resolve(layer)
        shape = self.cell.add_text(layer, origin, text, height=height, font=font,
                                    rotation=rotation, datatype=datatype)
        self._notify()
        return shape

    def sref(self, cell_name: str, origin, rotation: float = 0.0,
             magnification: float = 1.0, x_reflection: bool = False, label: str = ""):
        # Checked before any mutation (and before the undo checkpoint,
        # so a rejected placement doesn't leave a spurious undo step):
        # a cell instancing itself, directly or by way of one of its
        # own descendants, is invalid GDSII and previously corrupted
        # the design silently -- it wouldn't fail until something later
        # walked the hierarchy (cell_bbox()/fit_view(), save, ...) and
        # hit the cycle, by which point the bad Sref was already saved.
        if self.library.would_create_cycle(self.current_cell_name, cell_name):
            raise LibraryError(
                f"cannot place {cell_name!r} inside {self.current_cell_name!r}: "
                "this would create a cyclic cell reference (a cell can't "
                "contain an instance of itself, directly or indirectly)"
            )
        self._checkpoint()
        ref = self.cell.add_sref(cell_name, origin, rotation=rotation,
                                  magnification=magnification, x_reflection=x_reflection,
                                  label=label)
        self._notify()
        return ref

    def place_cell(self, cell_name: str, origin, source: Union[str, int, None] = None,
                    rotation: float = 0.0, magnification: float = 1.0, x_reflection: bool = False,
                    label: str = ""):
        """Search for a cell by exact name and place an Sref to it in the
        current cell — the normal way to place a symbol/part found via
        find_cells() or attached from another library.

        `source`: 'current', an attached library's index, or None to
        search the current library then each attached library in turn
        (raising if the name is ambiguous across more than one attached
        library). If the cell lives in an attached library, it (and
        everything it references) is copied into the current library
        first, since GDSII Srefs require their target cell to be
        defined in the same file.

        `label`, if given, sets the placed instance's ref designator
        (SRef.label) directly -- e.g. for a netlist-ready placement --
        the same field Edit Selected... calls "Label".
        """
        with self._batch():
            if source is None:
                if cell_name in self.library.cells:
                    source = "current"
                else:
                    found = [i for i, lib in enumerate(self.attached_libraries) if cell_name in lib.cells]
                    if not found:
                        raise LibraryError(
                            f"cell {cell_name!r} not found in the current library or any attached library"
                        )
                    if len(found) > 1:
                        raise LibraryError(
                            f"cell {cell_name!r} exists in more than one attached library {found}; "
                            "pass source=<index> to disambiguate"
                        )
                    source = found[0]

            if source == "current":
                if cell_name not in self.library.cells:
                    raise LibraryError(f"no such cell {cell_name!r} in the current library")
                local_name = cell_name
            else:
                idx = int(source)
                if idx < 0 or idx >= len(self.attached_libraries):
                    raise LibraryError(f"no attached library at index {idx}")
                local_name = self.library.import_cell_closure(self.attached_libraries[idx], cell_name)

            return self.sref(local_name, origin, rotation=rotation,
                              magnification=magnification, x_reflection=x_reflection,
                              label=label)

    def place_cell_array(self, cell_name: str, origin, columns: int = 1, rows: int = 1,
                          col_spacing: float = 0.0, row_spacing: float = 0.0,
                          source: Union[str, int, None] = None,
                          rotation: float = 0.0, magnification: float = 1.0,
                          x_reflection: bool = False):
        """Same idea as place_cell(), but places an ARef (array of
        instances) instead of a single SRef -- the normal way to place
        an array from Create > Array Instance or a cell picked via
        find_cells() when it should be repeated in a grid. See
        place_cell() for the `source` search/import semantics (a cell
        that only lives in an attached library is copied into the
        current library first)."""
        with self._batch():
            if source is None:
                if cell_name in self.library.cells:
                    source = "current"
                else:
                    found = [i for i, lib in enumerate(self.attached_libraries) if cell_name in lib.cells]
                    if not found:
                        raise LibraryError(
                            f"cell {cell_name!r} not found in the current library or any attached library"
                        )
                    if len(found) > 1:
                        raise LibraryError(
                            f"cell {cell_name!r} exists in more than one attached library {found}; "
                            "pass source=<index> to disambiguate"
                        )
                    source = found[0]

            if source == "current":
                if cell_name not in self.library.cells:
                    raise LibraryError(f"no such cell {cell_name!r} in the current library")
                local_name = cell_name
            else:
                idx = int(source)
                if idx < 0 or idx >= len(self.attached_libraries):
                    raise LibraryError(f"no attached library at index {idx}")
                local_name = self.library.import_cell_closure(self.attached_libraries[idx], cell_name)

            return self.aref(local_name, origin, columns=columns, rows=rows,
                              col_spacing=col_spacing, row_spacing=row_spacing,
                              rotation=rotation, magnification=magnification,
                              x_reflection=x_reflection)

    def aref(self, cell_name: str, origin, columns: int = 1, rows: int = 1,
             col_spacing: float = 0.0, row_spacing: float = 0.0,
             rotation: float = 0.0, magnification: float = 1.0, x_reflection: bool = False):
        """Place an array ('AREF') of instances of another cell in the
        current cell -- columns x rows, spaced col_spacing/row_spacing
        DBU apart, sharing one rotation/magnification/mirror for the
        whole array -- GDSII's native array-reference element, same
        idea as sref() but repeated. col_spacing/row_spacing are
        defined before rotation/mirroring is applied to the array (see
        hlayout.core.shapes.ARef.instance_origins()); magnification
        scales each instance's own geometry, not the spacing between
        instances. Same cyclic-reference guard as sref(). Use
        smash_aref() to explode the result back into individual SRefs,
        one per array position -- the SMASH command's operation."""
        if self.library.would_create_cycle(self.current_cell_name, cell_name):
            raise LibraryError(
                f"cannot place {cell_name!r} inside {self.current_cell_name!r}: "
                "this would create a cyclic cell reference (a cell can't "
                "contain an instance of itself, directly or indirectly)"
            )
        self._checkpoint()
        ref = self.cell.add_aref(cell_name, origin, columns=columns, rows=rows,
                                  col_spacing=col_spacing, row_spacing=row_spacing,
                                  rotation=rotation, magnification=magnification,
                                  x_reflection=x_reflection)
        self._notify()
        return ref

    def smash_aref(self, ref):
        """Explode an ARef (as returned by aref(), or found in
        self.cell.arefs) back into individual SRefs, one per array
        position, in the same cell it's placed in -- the command bar's
        SMASH command. Returns the list of SRefs created."""
        self._checkpoint()
        new_refs = self.cell.smash_aref(ref)
        self._notify()
        return new_refs

    def remove(self, shape):
        self._checkpoint()
        self.cell.remove(shape)
        self._notify()

    def mask_op(self, op: str, layer_a, result_layer, layer_b=None,
                amount: float = 0.0, cell_names: Optional[List[str]] = None,
                datatype: int = 0, flatten: bool = True,
                simplify: Optional[float] = None) -> dict:
        """Layer-based mask operation -- OVERSIZE, UNDERSIZE, AND, OR,
        NOT, XOR -- similar to the "resize"/boolean layer operators in
        a GDSII CAM system: grows/shrinks a layer, or combines two
        layers, and writes the result as new Polygon shapes on
        `result_layer` (which may be the same as layer_a, or a fresh
        layer). Any Path on an input layer is converted to its
        stroked-outline polygon first (paths have no filled area of
        their own) -- see hlayout.core.maskops for the geometry engine
        (requires the shapely package).

        - OVERSIZE/UNDERSIZE grow/shrink everything on `layer_a` by
          `amount` DBU (a single non-negative amount; UNDERSIZE shrinks
          by it, OVERSIZE grows by it -- the sign comes from which op
          you asked for, not the amount itself).
        - AND/OR/NOT/XOR combine `layer_a` with `layer_b` (required for
          all four; ignored for OVERSIZE/UNDERSIZE): AND is
          intersection, OR is union, NOT is layer_a minus layer_b
          (everything on A that isn't also on B), XOR is symmetric
          difference (everything on exactly one of A/B, not both --
          handy as a quick "did these two layers change" diff check).

        Runs on the current cell by default; pass `cell_names` to run
        it, independently, over other/additional specific cells (each
        cell's own layer_a/layer_b input geometry is combined only with
        itself -- output from one cell is never combined with another
        cell's).

        `simplify` (default None, meaning off) applies shapely's
        Douglas-Peucker vertex reduction to the result before it's
        written, with the given DBU tolerance (how far a simplified
        edge may drift from the original) -- useful for keeping a
        heavily-sized or heavily-booleaned result (arcs from OVERSIZE's
        rounded intermediate geometry, or a jagged XOR/NOT diff) under
        this tool's 200-point-per-polygon limit. Topology is preserved
        (no self-intersections introduced), but a large tolerance can
        still visibly round off corners -- start small (e.g. 1-2 DBU)
        and increase only if shapely_to_polygons() still splits the
        result into more pieces than expected.

        `flatten` (default True) controls whether placed cell instances
        (Srefs) inside the cell being operated on are descended into --
        with their own rotation/magnification/mirror/origin folded in --
        when gathering layer_a/layer_b's input geometry. This defaults
        on because most real content in a design ends up placed via
        Sref rather than drawn straight into the cell being operated on
        (e.g. every resistor built by the Thick-Film Resistor Designer
        is its own device cell placed by a single Sref -- see
        Session.generate_resistor()): with flatten=False a mask op run
        against the cell those resistors were placed into would see no
        input geometry at all and silently write nothing. Pass
        flatten=False to restrict the operation to exactly the named
        cell's own directly-owned shapes instead. Either way, the
        result is written flat into the operated-on cell itself, never
        back down into a placed sub-cell.

        Returns {cell_name: polygons_written} for cells that actually
        produced output (empty results are omitted).
        """
        op = op.upper()
        if op not in MASK_OPS:
            raise LayoutError(
                f"unknown mask op {op!r}; expected one of {', '.join(MASK_OPS)}"
            )
        resolve = self.library.layers.resolve
        a_layer = resolve(layer_a)
        r_layer = resolve(result_layer)
        b_layer = resolve(layer_b) if layer_b is not None else None
        if op in ("AND", "OR", "NOT", "XOR") and b_layer is None:
            raise LayoutError(f"mask op {op} requires layer_b")
        if op in ("OVERSIZE", "UNDERSIZE") and amount < 0:
            raise LayoutError(
                "amount must be non-negative -- OVERSIZE grows, UNDERSIZE "
                "shrinks by amount"
            )

        def gather(cell, layer):
            if flatten:
                return layer_to_shapely_flattened(self.library, cell, layer)
            return layer_to_shapely(cell.shapes_on_layer(layer))

        self._checkpoint()
        cells = ([self.library.get_cell(n) for n in cell_names] if cell_names
                 else [self.cell])
        written = {}
        for cell in cells:
            geom_a = gather(cell, a_layer)
            if op == "OVERSIZE":
                result = size_geometry(geom_a, amount)
            elif op == "UNDERSIZE":
                result = size_geometry(geom_a, -amount)
            else:
                geom_b = gather(cell, b_layer)
                result = boolean_op(geom_a, geom_b, op)
            if simplify:
                result = result.simplify(simplify, preserve_topology=True)
            new_polys = shapely_to_polygons(result, r_layer, datatype=datatype)
            if new_polys:
                cell.shapes.extend(new_polys)
                written[cell.name] = len(new_polys)
        self._notify()
        return written

    def run_mask_op_batch(self, steps=None, cell_names: Optional[List[str]] = None):
        """Run a saved, ORDERED series of mask operations (a "mask op
        batch"/recipe) -- e.g. OVERSIZE layer 10 by 5um into layer 20,
        then AND layer 20 with layer 30 into layer 40 -- as one action,
        instead of driving Tools > Mask Operations... once per step by
        hand. Steps run in the order given via mask_op(), so a LATER
        step can read a layer an EARLIER step just wrote (e.g. step 2's
        layer_a is step 1's result_layer) -- that's the whole point of a
        multi-step recipe, and why order is preserved end to end (see
        hlayout.io.techfile.save_mask_op_steps).

        `steps` is a list of hlayout.core.maskops.MaskOpStep instances
        or plain dicts (each with at least kind/layer_a/result_layer,
        e.g. {"kind": "OVERSIZE", "layer_a": 10, "result_layer": 20,
        "amount": 5000}); defaults to whatever's saved in the technology
        file (Tools > Mask Operation Batch...) via load_mask_op_steps().

        `cell_names` is forwarded to every step's mask_op() call
        (defaults to the current cell, same convention as mask_op()
        itself -- and, like mask_op(), each named cell's geometry is
        combined only with itself, never across cells).

        The whole batch runs as ONE undo step (see Session._batch()),
        even though it's implemented as several mask_op() calls under
        the hood -- Ctrl+Z undoes the entire recipe at once, not one
        step of it.

        Returns [(step, written), ...] in the order run, where
        `written` is that step's own mask_op() return value
        ({cell_name: polygons_written}) -- so a caller (the GUI batch
        window) can report what each step actually did."""
        from ..core.maskops import MaskOpStep
        from ..io.techfile import load_mask_op_steps

        if steps is None:
            steps = load_mask_op_steps(self.library)
        resolved = [s if isinstance(s, MaskOpStep) else MaskOpStep.from_dict(s) for s in steps]

        results = []
        with self._batch():
            for step in resolved:
                written = self.mask_op(
                    step.kind, step.layer_a, step.result_layer,
                    layer_b=step.layer_b, amount=step.amount,
                    cell_names=cell_names, flatten=step.flatten,
                    simplify=step.simplify or None,
                )
                results.append((step, written))
        return results

    #: GDS datatype the generated dielectric FILL polygons are written on.
    #: Everything a user (or drop_via / generate_resistor) draws on the
    #: dielectric layer stays at datatype 0 and is treated as input --
    #: the outer boundary, plus every via window / keep-out rectangle
    #: inside it. generate_dielectric() reads datatype 0 and (re)writes
    #: datatype 1; clear_dielectric() removes only datatype 1.
    DIELECTRIC_FILL_DATATYPE = 1

    def generate_dielectric(self, layer=None, keepout_layers=None,
                             clearance=None, margin=None,
                             cell_name: Optional[str] = None) -> int:
        """Turn the dielectric layer's INPUT geometry into a filled sheet
        with holes -- the parametric equivalent of hand-running Tools >
        Mask Operations... ``boundary NOT holes``, auto-fractured so it
        never hits the 200-point-per-polygon limit.

        Everything already drawn on ``layer`` at GDS datatype 0 is the
        input, and needs no separate "keep-out layer":

        * the single LARGEST shape that encloses all the others is the
          fill BOUNDARY (draw one rectangle around your circuit). If no
          shape encloses the rest, the boundary falls back to the
          design's bounding box grown by ``margin``.
        * every OTHER datatype-0 shape inside it -- a via window stamped
          by drop_via(dielectric_layer=...), a resistor's
          between-terminals keep-out, a hand-drawn exclusion rectangle,
          anything -- is a hole, grown by ``clearance`` before it's cut.

        ``keepout_layers`` is an optional extra: other layers whose
        geometry should also be punched out (for cross-layer exclusion
        zones). The filled result is written at datatype
        ``DIELECTRIC_FILL_DATATYPE`` (1); any previous fill there is
        cleared first, so this re-runs cleanly after the layout changes
        and never eats its own inputs. Parameters fall back to Setup >
        Dielectric Fill Rules... Returns the polygon count. One undo step.
        """
        from shapely.geometry import box as _box
        from shapely.ops import unary_union

        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")

        rules = load_dielectric_rules(self.library)
        resolve = self.library.layers.resolve
        r_layer = resolve(layer) if layer is not None else int(rules["layer"])
        if r_layer is None or r_layer < 0:
            raise LayoutError(
                "no dielectric layer set -- pass layer=... or configure "
                "Setup > Dielectric Fill Rules..."
            )
        clr = float(clearance) if clearance is not None else float(rules["clearance"])
        mrg = float(margin) if margin is not None else float(rules["margin"])
        kl_src = keepout_layers if keepout_layers is not None else rules["keepout_layers"]
        keepout_nums = [resolve(k) for k in kl_src]

        cell = self.library.get_cell(name)

        # datatype-0 input geometry already on the dielectric layer
        inputs = layer_geoms_flattened(self.library, cell, r_layer, datatype=0)
        boundary = None
        holes = list(inputs)
        if inputs:
            biggest = max(inputs, key=lambda g: g.area)
            rest = [g for g in inputs if g is not biggest]
            if not rest or biggest.buffer(1.0).contains(unary_union(rest)):
                boundary, holes = biggest, rest

        if boundary is not None:
            fill = boundary
        else:
            bbox = self.library.cell_bbox(name)
            if bbox is None:
                raise LayoutError(f"cell {name!r} is empty -- nothing to cover")
            x0, y0, x1, y1 = bbox
            fill = _box(x0 - mrg, y0 - mrg, x1 + mrg, y1 + mrg)

        opening_geoms = list(holes)
        for src in keepout_nums:
            if src is None or src < 0 or src == r_layer:
                continue
            g = layer_to_shapely_flattened(self.library, cell, src)
            if not g.is_empty:
                opening_geoms.append(g)

        result = fill
        if opening_geoms:
            merged = unary_union(opening_geoms)
            if clr:
                merged = size_geometry(merged, clr)
            result = fill.difference(merged)

        dt = self.DIELECTRIC_FILL_DATATYPE
        with self._batch():
            self.clear_dielectric(layer=r_layer, cell_name=name)
            written = 0
            for poly in fracture_polygons(result):
                pts = list(poly.exterior.coords)[:-1]
                if len(pts) >= 3:
                    cell.add_polygon(r_layer, [(int(round(px)), int(round(py)))
                                               for px, py in pts], datatype=dt)
                    written += 1
        self._notify()
        return written

    def clear_dielectric(self, layer=None, cell_name: Optional[str] = None,
                          all_datatypes: bool = False) -> int:
        """Remove the generated dielectric FILL (shapes on ``layer`` at
        datatype ``DIELECTRIC_FILL_DATATYPE``) in the target cell, so
        Generate Dielectric Fill can be re-run -- the boundary and
        keep-out inputs it was built from (datatype 0) are left in
        place. Pass ``all_datatypes=True`` to wipe the layer entirely,
        inputs included. ``layer`` defaults to the saved Setup >
        Dielectric Fill Rules... layer. Returns how many shapes were
        removed. One undo step (a no-op checkpoint when called from
        generate_dielectric's own batch)."""
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        resolve = self.library.layers.resolve
        if layer is not None:
            r_layer = resolve(layer)
        else:
            r_layer = int(load_dielectric_rules(self.library)["layer"])
        if r_layer is None or r_layer < 0:
            raise LayoutError(
                "no dielectric layer set -- pass layer=... or configure "
                "Setup > Dielectric Fill Rules..."
            )
        dt = self.DIELECTRIC_FILL_DATATYPE
        cell = self.library.get_cell(name)

        def _is_fill(s):
            if s.layer != r_layer:
                return False
            return all_datatypes or getattr(s, "datatype", 0) == dt

        keep = [s for s in cell.shapes if not _is_fill(s)]
        removed = len(cell.shapes) - len(keep)
        if removed:
            self._checkpoint()   # a no-op when called inside generate_dielectric's batch
            cell.shapes = keep
            self._notify()
        return removed

    def run_drc(self, rules=None, cell_names: Optional[List[str]] = None,
                error_layer=None) -> List["DrcViolation"]:
        """Run Design Rule Check (DRC) rules -- MIN_WIDTH, MIN_AREA,
        MIN_SPACE (single-layer), MIN_SPACE_BETWEEN, MIN_ENCLOSURE
        (between two layers) -- against the design. See hlayout.core.drc
        for what each rule kind checks and how a violation's marker
        geometry is computed; like mask ops, every check descends into
        placed cell instances (Srefs) so a design built mostly out of
        placed cells (resistors, vias, ...) is actually checked.

        `rules` is a list of DrcRule instances or plain dicts (each
        with at least kind/layer_a/value, e.g. {"kind": "MIN_WIDTH",
        "layer_a": 30, "value": 100, "name": "M1.W.1"}); defaults to
        whatever's saved in the technology file (Tools > DRC
        Rules.../TECH SAVE) via load_drc_rules().

        `cell_names` restricts the check to specific cells; defaults to
        just the current cell (same convention as mask_op()) -- since
        every check already descends into that cell's own placed
        instances, checking the current cell alone is normally the
        whole design as seen from there. Pass explicit cell_names
        (e.g. every cell in the library) only when you actually want
        each named cell checked independently in its OWN right as well
        -- checking both a sub-cell and a parent that places it will
        report the same physical violation twice, once from each
        cell's own point of view, since nothing here tracks "already
        reported by a descendant."

        `error_layer`, if given, additionally draws every violation's
        marker polygon onto that layer in the cell it was found in (as
        a single undo step alongside everything else DRC touches -- DRC
        itself never modifies any other geometry). Every checked cell's
        EXISTING shapes on `error_layer` are removed first, so re-running
        DRC (after a fix, or with different rules) never leaves stale
        markers from a previous run mixed in with -- or standing in for --
        this run's results; a cell that had violations last time but is
        now clean is left with none, not last run's leftovers. Pass None
        (the default) for a dry-run report with no drawing (and no
        clearing either -- a dry run never touches the design).

        Returns the list of DrcViolation found (each has .rule,
        .cell_name, .points, .message, .bbox()).
        """
        from ..core.drc import DrcRule, run_drc

        if rules is None:
            rules = load_drc_rules(self.library)
        parsed = [r if isinstance(r, DrcRule) else DrcRule.from_dict(r) for r in rules]

        if cell_names is None:
            if self.current_cell_name is None:
                raise LayoutError("no current cell; call new_cell()/use_cell() first")
            cell_names = [self.current_cell_name]

        self._checkpoint()
        violations = run_drc(self.library, parsed, cell_names=cell_names)
        if error_layer is not None:
            e_layer = self.library.layers.resolve(error_layer)
            by_cell = {}
            for v in violations:
                by_cell.setdefault(v.cell_name, []).append(v)
            for cell_name in cell_names:
                cell = self.library.get_cell(cell_name)
                cell.shapes = [s for s in cell.shapes if s.layer != e_layer]
                for v in by_cell.get(cell_name, []):
                    cell.add_polygon(e_layer, v.points)
        self._notify()
        return violations

    def wire_bond(self, points, layer=None, width=None, pad=None,
                  min_length=None, max_length=None,
                  cell_name: Optional[str] = None):
        """Draw a chip-and-wire assembly WIRE BOND between two points --
        the jumper that ties a bare-die pad to a thick/thin-film
        conductor pad, a conductor pad to a package pin, or one
        conductor pad to another.

        `points` is exactly two (x, y) DBU coordinates: the ball end and
        the stitch end. The bond is drawn on the bond layer as a round
        ball of diameter `pad` at each end joined by a `width`-wide
        round-capped Path -- an ``O------O`` figure -- so it visibly
        reads as a bond wire and, because that layer is folded into
        extract_netlist()/NETLIST RUN automatically, it electrically
        bridges whatever pads its two ends overlap. Connectivity is then
        reported straight through the bond to the die pin.

        The span (centre-to-centre distance) must lie between the bond
        rules' ``min_length`` and ``max_length`` -- a bonder can neither
        loop an arbitrarily short wire nor throw an unsupported one so
        long it sags or shorts. The new bond also may not CROSS any wire
        bond already in the cell (bond wires are bare gold in one plane
        -- crossed wires short); sharing a pad/endpoint is fine, only a
        proper X crossing is rejected.

        `layer`/`width`/`pad`/`min_length`/`max_length` default to
        Setup > Wire Bond Rules... (load_bond_rules). Raises LayoutError
        on a bad layer, wrong point count, out-of-range length, or a
        crossing. One undo step.
        """
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")

        rules = load_bond_rules(self.library)
        resolve = self.library.layers.resolve
        r_layer = resolve(layer) if layer is not None else int(rules["layer"])
        if r_layer is None or r_layer < 0:
            raise LayoutError(
                "no wire-bond layer set -- pass layer=... or configure "
                "Setup > Wire Bond Rules..."
            )
        w = int(width) if width is not None else int(rules["width"])
        p = int(pad) if pad is not None else int(rules["pad"])
        lo = float(min_length) if min_length is not None else float(rules["min_length"])
        hi = float(max_length) if max_length is not None else float(rules["max_length"])

        pts = [(int(round(x)), int(round(y))) for x, y in points]
        if len(pts) != 2:
            raise LayoutError(
                f"a wire bond needs exactly two endpoints, got {len(pts)}"
            )
        (x1, y1), (x2, y2) = pts
        span = math.hypot(x2 - x1, y2 - y1)
        if span < lo:
            raise LayoutError(
                f"wire bond span {span:.0f} DBU is shorter than the "
                f"minimum {lo:.0f} (Setup > Wire Bond Rules...)"
            )
        if span > hi:
            raise LayoutError(
                f"wire bond span {span:.0f} DBU is longer than the "
                f"maximum {hi:.0f} (Setup > Wire Bond Rules...)"
            )

        cell = self.library.get_cell(name)
        for a, b in self._existing_bond_segments(cell, r_layer):
            if _segments_cross((x1, y1), (x2, y2), a, b):
                raise LayoutError(
                    "that wire bond would cross an existing bond wire "
                    "-- bond wires share one plane and can't cross"
                )

        with self._batch():
            r = max(1, p // 2)
            cell.add_circle(r_layer, (x1, y1), r, segments=16)
            cell.add_circle(r_layer, (x2, y2), r, segments=16)
            cell.add_path(r_layer, [(x1, y1), (x2, y2)], max(1, w), cap="round")
        self._notify()
        return cell.shapes[-1]

    @staticmethod
    def _existing_bond_segments(cell, layer, exclude=None):
        """Yield (p0, p1) endpoint pairs for every wire-bond Path already
        on `layer` in `cell` -- the two-point round-capped Paths
        wire_bond() draws. `exclude` is one Path object to skip (used by
        move_wire_bond so a bond isn't tested against its own old
        position)."""
        from ..core.shapes import Path

        for s in cell.shapes:
            if s is exclude:
                continue
            if isinstance(s, Path) and s.layer == layer and len(s.points) >= 2:
                for i in range(len(s.points) - 1):
                    yield (tuple(s.points[i]), tuple(s.points[i + 1]))

    def _resolve_bond_layer(self, layer):
        rules = load_bond_rules(self.library)
        r_layer = self.library.layers.resolve(layer) if layer is not None else int(rules["layer"])
        if r_layer is None or r_layer < 0:
            raise LayoutError(
                "no wire-bond layer set -- pass layer=... or configure "
                "Setup > Wire Bond Rules..."
            )
        return r_layer, rules

    def find_wire_bond(self, point, layer=None, cell_name: Optional[str] = None,
                       tol: Optional[float] = None):
        """Locate the wire bond nearest ``point`` (an (x, y) DBU
        coordinate) in the cell and return its component shapes as
        ``(connector_path, [ball, ball])`` -- the two-point round-capped
        Path plus the endpoint discs wire_bond() drew with it (the ball
        list may be shorter if a disc is missing). Returns ``None`` when
        nothing on the bond layer lies within ``tol`` (default: the bond
        rules' pad diameter, min the width) of ``point``.

        The match is by the connector Path: ``point`` need only be within
        ``tol`` of the O------O line, so clicking anywhere along a bond
        finds it.
        """
        from ..core.shapes import Path, Polygon

        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        r_layer, rules = self._resolve_bond_layer(layer)
        reach = float(tol) if tol is not None else float(max(int(rules["pad"]),
                                                             int(rules["width"]), 1))
        cell = self.library.get_cell(name)

        best = None
        best_d = reach
        for s in cell.shapes:
            if not (isinstance(s, Path) and s.layer == r_layer and len(s.points) == 2):
                continue
            d = _point_seg_dist(tuple(point), tuple(s.points[0]), tuple(s.points[1]))
            if d <= best_d:
                best_d = d
                best = s
        if best is None:
            return None

        ends = [tuple(best.points[0]), tuple(best.points[1])]
        balls = []
        for s in cell.shapes:
            if not (isinstance(s, Polygon) and s.layer == r_layer):
                continue
            x1, y1, x2, y2 = s.bbox()
            c = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
            for e in ends:
                if math.hypot(c[0] - e[0], c[1] - e[1]) <= 2.0:
                    balls.append(s)
                    break
        return best, balls

    def move_wire_bond(self, point, dx, dy, layer=None,
                       cell_name: Optional[str] = None, tol: Optional[float] = None):
        """Translate the wire bond nearest ``point`` by (``dx``, ``dy``)
        DBU -- both endpoint discs and the connector move together. The
        span is unchanged, so min/max length still hold; the new position
        still may not CROSS another bond. Raises LayoutError if no bond
        is near ``point`` or the move would cross one. One undo step."""
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        r_layer, _ = self._resolve_bond_layer(layer)
        found = self.find_wire_bond(point, layer=r_layer, cell_name=name, tol=tol)
        if found is None:
            raise LayoutError(f"no wire bond near {tuple(point)} on layer {r_layer}")
        path, balls = found
        dx, dy = int(round(dx)), int(round(dy))

        (x1, y1), (x2, y2) = path.points
        n1, n2 = (x1 + dx, y1 + dy), (x2 + dx, y2 + dy)
        cell = self.library.get_cell(name)
        for a, b in self._existing_bond_segments(cell, r_layer, exclude=path):
            if _segments_cross(n1, n2, a, b):
                raise LayoutError(
                    "moving that wire bond there would cross an existing "
                    "bond wire -- bond wires share one plane and can't cross"
                )

        with self._batch():
            path.points = [n1, n2]
            for ball in balls:
                ball.points = [(px + dx, py + dy) for px, py in ball.points]
        self._notify()
        return n1, n2

    def delete_wire_bond(self, point, layer=None,
                         cell_name: Optional[str] = None, tol: Optional[float] = None):
        """Remove the wire bond nearest ``point`` -- its connector Path
        and both endpoint discs. Raises LayoutError if no bond is near
        ``point``. One undo step; returns the removed bond's endpoints."""
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        r_layer, _ = self._resolve_bond_layer(layer)
        found = self.find_wire_bond(point, layer=r_layer, cell_name=name, tol=tol)
        if found is None:
            raise LayoutError(f"no wire bond near {tuple(point)} on layer {r_layer}")
        path, balls = found
        ends = (tuple(path.points[0]), tuple(path.points[1]))
        doomed = {id(path), *(id(b) for b in balls)}
        cell = self.library.get_cell(name)
        with self._batch():
            cell.shapes[:] = [s for s in cell.shapes if id(s) not in doomed]
        self._notify()
        return ends

    def auto_route(self, a, b, layer=None, grid: int = 100, width: int = 20,
                   clearance: Optional[float] = None, margin: Optional[float] = None,
                   boundary_layer=None, cell_name: Optional[str] = None):
        """Automatically route a clear orthogonal trace between two
        points on one conductor layer, dodging every other piece of
        copper already on that layer.

        ``boundary_layer`` (default: the Route Layers rule's
        ``boundary_layer``, -1 = none): if set, the trace must stay
        entirely inside the geometry on that layer -- a route keep-in
        boundary. The boundary is shrunk by ``width / 2`` before the
        check, and the search area is clipped to it.

        ``a`` / ``b`` are each either an ``(x, y)`` DBU point or a
        pin-bearing shape (Rectangle/Polygon) -- for a shape, its bbox
        centre is the endpoint and its own copper is excluded from the
        obstacle set so a trace can land on it. ``layer`` defaults to
        the Route Layers rule's ``layer_a`` (the "1st routing
        conductor"). ``grid`` is the routing lattice pitch (pass the
        canvas's current grid), ``width`` the trace width (pass the
        canvas's current path width). Obstacles are grown by
        ``width / 2 + clearance`` (``clearance`` defaults to one grid
        step) so the finished trace keeps clear of its neighbours.
        ``margin`` (default: 12 grid steps) is how far past the two
        endpoints the search area extends.

        Draws the result as a round-capped Path on ``layer`` in one undo
        step and returns it. Raises ``LayoutError`` if the two points
        can't be connected with a clear trace at this grid/width, or if
        the search grid would be too large (use a coarser grid). Needs
        shapely."""
        from ..core import autoroute as _ar
        from ..core.maskops import shape_to_shapely

        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        cell = self.library.get_cell(name)
        resolve = self.library.layers.resolve
        rr = load_route_rules(self.library)
        if layer is not None:
            r_layer = resolve(layer)
        else:
            r_layer = int(rr["layer_a"])
        if boundary_layer is not None:
            b_layer = resolve(boundary_layer)
        else:
            b_layer = int(rr.get("boundary_layer", -1))
        grid = int(grid)
        width = int(width)
        if grid <= 0:
            raise LayoutError("routing grid must be positive")
        clr = float(clearance) if clearance is not None else float(grid)
        mrg = float(margin) if margin is not None else 12.0 * grid

        def _endpoint(v):
            if isinstance(v, (Rectangle, Polygon)):
                x0, y0, x1, y1 = v.bbox()
                return ((x0 + x1) / 2.0, (y0 + y1) / 2.0), v
            return (float(v[0]), float(v[1])), None

        (ax, ay), sa = _endpoint(a)
        (bx, by), sb = _endpoint(b)

        if math.hypot(bx - ax, by - ay) < grid:
            raise LayoutError(
                "the two route endpoints are the same point (or closer "
                f"than one grid step, {grid} DBU) -- nothing to route"
            )

        from shapely.geometry import Point as _spoint
        from shapely.geometry import box as _sbox
        from shapely.ops import unary_union

        obstacles = layer_to_shapely_flattened(self.library, cell, r_layer)
        inflate = width / 2.0 + clr
        # Clear a landing zone around each endpoint so the routed
        # centre-line can actually start/finish there: the pad the trace
        # lands on is itself copper on this layer (and, for bare-point
        # endpoints, we've no shape to exclude by identity), so without
        # this the buffered obstacle set seals the endpoint off.
        halo = inflate + 2.0 * grid
        landing = [
            _sbox(ax - halo, ay - halo, ax + halo, ay + halo),
            _sbox(bx - halo, by - halo, bx + halo, by + halo),
        ]
        # Also drop the two selected pin shapes' own copper entirely.
        for s in (sa, sb):
            if s is not None:
                g = shape_to_shapely(s)
                if g is not None and not g.is_empty:
                    landing.append(g)
        if not obstacles.is_empty:
            obstacles = obstacles.difference(unary_union(landing))
        if not obstacles.is_empty and inflate > 0:
            obstacles = obstacles.buffer(inflate, join_style="mitre", mitre_limit=5.0)

        # Route keep-in boundary: the trace must stay inside the geometry
        # on b_layer, shrunk by the trace half-width so its edge stays in.
        keepin = None
        if b_layer is not None and b_layer >= 0:
            keepin = layer_to_shapely_flattened(self.library, cell, b_layer)
            if keepin.is_empty:
                keepin = None
            else:
                if width > 0:
                    keepin = keepin.buffer(-width / 2.0, join_style="mitre", mitre_limit=5.0)
                if keepin.is_empty:
                    raise LayoutError(
                        f"the route boundary on layer {b_layer} is narrower than the "
                        f"trace width {width} -- nothing left after the keep-in inset"
                    )
                for pn, (px, py) in (("first", (ax, ay)), ("second", (bx, by))):
                    if not keepin.buffer(1.0).contains(_spoint(px, py)):
                        raise LayoutError(
                            f"the {pn} pin ({int(px)}, {int(py)}) is outside the route "
                            f"boundary on layer {b_layer}"
                        )

        x0 = min(ax, bx) - mrg
        y0 = min(ay, by) - mrg
        x1 = max(ax, bx) + mrg
        y1 = max(ay, by) + mrg
        if keepin is not None:
            # clip the search area to the boundary's extent (+1 grid) --
            # the trace can't leave it, and this keeps the node count down
            kx0, ky0, kx1, ky1 = keepin.bounds
            x0, y0 = max(x0, kx0 - grid), max(y0, ky0 - grid)
            x1, y1 = min(x1, kx1 + grid), min(y1, ky1 + grid)
        nx = int((x1 - x0) // grid) + 1
        ny = int((y1 - y0) // grid) + 1

        try:
            pts = _ar.route((ax, ay), (bx, by), obstacles, grid,
                            int(round(x0)), int(round(y0)), nx, ny, keepin=keepin)
        except ValueError as exc:
            raise LayoutError(str(exc))
        if pts is None:
            within = (f" within the layer-{b_layer} route boundary"
                      if keepin is not None else "")
            raise LayoutError(
                f"no clear route on layer {r_layer} between {(int(ax), int(ay))} "
                f"and {(int(bx), int(by))}{within} at grid {grid} / width {width} "
                f"(try a coarser grid, a narrower trace, or a larger margin)"
            )
        return self.path(r_layer, pts, width, cap="round")

    def extract_netlist(self, conductor_layers, cell_name: Optional[str] = None,
                        include_bonds: bool = True) -> List["Net"]:
        """Extract the electrical netlist of `cell_name` (default: the
        current cell), as seen across `conductor_layers` -- an iterable
        of layer numbers or names (each resolved the same way every
        other layer argument in this API is). See hlayout.core.netlist
        for exactly how this works: every touching/overlapping piece of
        copper on those layers becomes one Net, and Rectangle.pin/
        Polygon.pin plus the owning SRef's .label identify each net's
        members as RefDes.Pin.

        There's no saved "which layers are conductors" setting in the
        technology file today, so `conductor_layers` must be passed
        explicitly every call -- e.g. `s.extract_netlist([31])` for a
        design whose Resistor Geometry Rules... conductor_layer is 31,
        or `s.extract_netlist([0, 1])` for a two-layer Route (2-Layer)
        design.

        Returns a list of hlayout.core.netlist.Net (each has .id,
        .pins -- a list of NetPin(ref_des, pin) -- and
        .unresolved_count for members whose owning instance has no ref
        designator yet). A pin whose own shape isn't on any of
        `conductor_layers` is reported in one final Net(id="UNASSIGNED")
        rather than silently dropped -- see extract_netlist()'s own
        docstring. Requires the shapely package (see mask_op()).

        `include_bonds` (default True): if Setup > Wire Bond Rules... has
        a bond layer configured, it's added to `conductor_layers`
        automatically so every WIRE BOND (wire_bond()/BOND) is treated
        as conductor and its net is reported straight through to the
        die pin it lands on. Pass include_bonds=False to see only the
        conductor layers you named."""
        from ..core.netlist import extract_netlist as _extract_netlist

        cell = self.library.get_cell(cell_name) if cell_name else self.cell
        resolved = [self.library.layers.resolve(l) for l in conductor_layers]
        if include_bonds:
            b_layer = int(load_bond_rules(self.library)["layer"])
            if b_layer >= 0 and b_layer not in resolved:
                resolved.append(b_layer)
        return _extract_netlist(self.library, cell, resolved)

    def export_netlist_csv(self, nets: List["Net"], path: str) -> None:
        """Write `nets` (as returned by extract_netlist()) to a CSV file,
        one row per (net, ref_des, pin) -- net, ref_des, pin columns,
        matching the row-per-item convention of the Resistor Designer's
        own CSV export. A net with no pins at all (rare -- copper with
        no numbered pad touching it anywhere) still gets one row with
        empty ref_des/pin columns, so the net's existence isn't lost."""
        import csv

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["net", "ref_des", "pin"])
            for net in nets:
                if not net.pins:
                    writer.writerow([net.id, "", ""])
                    continue
                for p in net.pins:
                    writer.writerow([net.id, p.ref_des, p.pin])

    def remap_layers(self, mapping, cell_names: Optional[List[str]] = None) -> dict:
        """Reassign shapes from one layer to another, in bulk, across the
        library. `mapping` is {old_layer: new_layer}; each side accepts
        either a layer number (0-63) or an existing layer name, resolved
        the same way every other layer argument in this API is.

        By default every cell in the library is scanned (not just the
        current cell) since the usual reason to do this is bringing an
        existing design's geometry into alignment with a layer stack
        loaded from a technology file (Setup > Load Technology File...
        / TECH LOAD) after the fact -- e.g. this design's old layer 3
        should really be layer 7 in the target process. Pass
        `cell_names` to restrict the remap to specific cells instead.

        Returns {old_layer_number: shapes_moved} for layers that
        actually had something to move (layers with nothing on them,
        or where old == new, are omitted).
        """
        self._checkpoint()
        resolve = self.library.layers.resolve
        resolved = {resolve(old): resolve(new) for old, new in mapping.items()}
        resolved = {old: new for old, new in resolved.items() if old != new}
        cells = ([self.library.get_cell(n) for n in cell_names] if cell_names
                 else list(self.library.cells.values()))
        counts = {}
        for cell in cells:
            for shape in cell.shapes:
                if shape.layer in resolved:
                    counts[shape.layer] = counts.get(shape.layer, 0) + 1
                    shape.layer = resolved[shape.layer]
        self._notify()
        return counts

    # --- queries -----------------------------------------------------
    def shapes_on_layer(self, layer):
        layer = self.library.layers.resolve(layer)
        return self.cell.shapes_on_layer(layer)

    def bbox(self, cell_name: Optional[str] = None):
        return self.library.cell_bbox(cell_name or self.current_cell_name)

    # --- pin numbering -------------------------------------------------
    def list_pins(self, cell_name: Optional[str] = None) -> List[dict]:
        """Every Rectangle/Polygon shape in `cell_name` (default: the
        current cell) that could carry a pin number -- Path/Text can't
        and are omitted. One dict per shape: {"index", "layer", "pin",
        "bbox"}. `index` is what set_pin()/the PIN SET command expect;
        `pin` is "" for a pad that hasn't been numbered yet. This is
        also how the pin-numbering canvas tool (Edit > Number Pins...)
        and netlist extraction find what's already assigned in a cell.
        The command-bar form is PIN LIST [cell=<name>]."""
        cell = self.library.get_cell(cell_name) if cell_name else self.cell
        return [
            {"index": i, "layer": shape.layer, "pin": shape.pin, "bbox": shape.bbox()}
            for i, shape in enumerate(cell.shapes)
            if isinstance(shape, (Rectangle, Polygon))
        ]

    def set_pin(self, index: int, pin: str, cell_name: Optional[str] = None):
        """Set the pin number/name of the shape at `index` within
        `cell_name`'s (default: current cell) shape list -- see
        list_pins() for indices. Only Rectangle/Polygon shapes carry a
        pin; raises if the shape at that index is some other type or
        the index is out of range. The command-bar form is
        PIN SET <index> <pin> [cell=<name>]."""
        cell = self.library.get_cell(cell_name) if cell_name else self.cell
        if index < 0 or index >= len(cell.shapes):
            raise LayoutError(f"no shape at index {index} in cell {cell.name!r}")
        shape = cell.shapes[index]
        if not isinstance(shape, (Rectangle, Polygon)):
            raise LayoutError(
                f"shape at index {index} is a {type(shape).__name__}, which has no pin"
            )
        self._checkpoint()
        shape.pin = pin
        self._notify()
        return shape
