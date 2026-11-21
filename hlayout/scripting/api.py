# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

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
import os
from contextlib import contextmanager
from typing import Callable, List, Optional, Union

from ..core.cell import Cell
from ..core.errors import LayoutError, LibraryError
from ..core.library import Library
from ..core.shapes import Polygon, Rectangle
from ..core.maskops import (
    MASK_OPS,
    boolean_op,
    layer_to_shapely,
    layer_to_shapely_flattened,
    shapely_lines_to_paths,
    shapely_to_polygons,
    size_geometry,
)
from ..io.dxfio import write_dxf
from ..io.gdsio import load_gds, save_gds
from ..io.gerberio import write_gerber_layers
from ..io.sidecar import load_design, save_design
from ..io.techfile import (
    dielectric_layer_number,
    load_and_apply_techfile,
    load_bond_rules,
    load_component_models,
    load_drc_rules,
    load_netlist_settings,
    load_route_rules,
    save_techfile,
)
from ..core.refdes import DEFAULT_PIN1_GAP, is_connector_refdes, refdes_pin1_offset
from .session_libraries import LibrariesMixin
from .session_resistors import ResistorsMixin
from .session_drawings import SHEET_SIZES_MM, DrawingsMixin, _matched_component_footprint  # noqa: F401 (re-exported: assembly.py, tests)
from .session_bonds import BondsMixin
from .session_dielectric import DielectricMixin
from .session_autoplace import AutoPlaceMixin
from .session_routing import RoutingMixin
from .session_hybrid import HybridMixin
from .session_locks import LocksMixin


class Session(LibrariesMixin, ResistorsMixin, DrawingsMixin, BondsMixin, DielectricMixin, AutoPlaceMixin, RoutingMixin, HybridMixin, LocksMixin):
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
        #: The most recent run_thermal() result (None until one is run).
        self.last_thermal_result = None
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
        session.design_path = path
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
        out = save_design(self.library, self.attached_libraries, self._attached_paths, path)
        self.design_path = path
        return out

    def export_gerber(self, out_prefix: str, cell_name: Optional[str] = None,
                       layers: Optional[List[int]] = None, scale: float = 1.0):
        """Export the design (default: the current cell) as Gerber
        RS-274X -- one flattened file per *visible* layer that has data,
        named ``<out_prefix>_L<NN>_<layer name>.gbr``. Gerber is a
        manufacturing output format, not this tool's database format
        (that's still GDSII, via save()/File > Save As Library...), so
        every Sref is flattened rather than round-tripped, and Text
        shapes are skipped (Gerber has no text primitive).

        `layers`, if given, restricts export to that subset of layer
        numbers. `scale` enlarges the artwork about the origin -- 5 or
        10 for the 5X / 10X artwork hybrid screens are plotted at; path
        widths scale with it and the design itself is untouched.
        Returns (written, skipped_text) -- see
        hlayout.io.gerberio.write_gerber_layers for details."""
        name = cell_name or self.current_cell_name
        if name is None:
            raise LibraryError("no current cell to export")
        return write_gerber_layers(self.library, name, out_prefix, layers=layers,
                                   scale=scale)

    def run_gerber_export_batch(self, jobs=None, cell_name: Optional[str] = None,
                                out_dir: Optional[str] = None, scale: float = 1.0):
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

        `out_dir` is the output folder for the whole set (Tools > Gerber
        Export Sets... > Output folder): a job whose out_prefix is
        RELATIVE (a plain name, or sub/folder/name) is written inside
        it; a job with an ABSOLUTE out_prefix keeps its own location.
        The folder is created if needed. With out_dir=None every prefix
        is used exactly as given (relative to the working directory).

        `scale` is the artwork scale for every job in the set (1 =
        actual size, 5 / 10 = 5X / 10X artwork) -- see export_gerber().

        Gerber export never modifies the design (it only writes files
        to disk), so unlike run_mask_op_batch() this needs no undo
        checkpoint.

        Returns [(job, written, skipped_text), ...] in the order run --
        see export_gerber()/hlayout.io.gerberio.write_gerber_layers for
        what `written`/`skipped_text` mean."""
        from ..io.gerberio import GerberExportJob, resolve_out_prefix
        from ..io.techfile import load_gerber_export_jobs

        if jobs is None:
            jobs = load_gerber_export_jobs(self.library)
        resolved = [j if isinstance(j, GerberExportJob) else GerberExportJob.from_dict(j) for j in jobs]

        results = []
        for job in resolved:
            written, skipped_text = self.export_gerber(
                resolve_out_prefix(job.out_prefix, out_dir),
                cell_name=cell_name, layers=job.layers, scale=scale,
            )
            results.append((job, written, skipped_text))
        return results

    def export_dxf(self, path: str, cell_name: Optional[str] = None,
                    layers: Optional[List[int]] = None):
        """Export the design (default: the current cell) as a single DXF
        (AutoCAD R12/AC1009) file at ``path``, with one DXF layer per
        hlayout layer that's visible and has data. Like export_gerber(),
        every Sref is flattened rather than round-tripped -- but unlike
        Gerber, DXF has a native TEXT entity, so Text shapes (and every
        reference designator, baked or floating) are included, not
        skipped.

        Every layer's Polygon/Rectangle/Path shapes are merged with the
        same shapely pipeline Tools > Mask Operations... uses before
        being written -- a Path's width is baked into real, unambiguous
        polygon geometry (not a DXF attribute some readers ignore), and
        touching/overlapping shapes on one layer become a single clean
        region. Requires the shapely package.

        `layers`, if given, restricts export to that subset of layer
        numbers. Returns (written_layers, entity_count) -- see
        hlayout.io.dxfio.write_dxf for details."""
        name = cell_name or self.current_cell_name
        if name is None:
            raise LibraryError("no current cell to export")
        return write_dxf(self.library, name, path, layers=layers)

    def export_freecad(self, path: str, cell_name: Optional[str] = None, *,
                        layer_thickness: Optional[dict] = None,
                        component_models: Optional[dict] = None,
                        loop_height_um: Optional[float] = None):
        """Write a FreeCAD macro (``.FCMacro``) at ``path`` that, run
        inside FreeCAD, rebuilds the design (default: the current cell)
        as a 3D stack of coloured solids -- one per printed film, at the
        elevation its fired-film thickness puts it (layer number order =
        print order), with each layer's shapes merged through the same
        shapely pipeline the mask ops use, wire bonds as raised loops,
        and placed components as fixed-height bodies or substituted
        STEP/IGES models. Layer thicknesses and component models come
        from Setup (``layer_thickness`` / ``component_models``
        tech-params), falling back to the role heuristics in
        hlayout.io.techfile. Requires the ``shapely`` package. Returns
        ``path``.

        `layer_thickness`/`component_models`/`loop_height_um`, if given,
        override those tech-param defaults for this export only --
        nothing is written back to the saved technology file. Passing
        `component_models` REPLACES the tech-param lookup rather than
        merging with it, so build a full dict yourself if you want to
        keep any existing manual entries (see
        match_freecad_components()'s MatchReport.component_models(),
        which does exactly that -- it starts from the design's saved
        entries and layers auto-matches on top)."""
        from ..io.freecadio import write_freecad_macro
        name = cell_name or self.current_cell_name
        if name is None:
            raise LibraryError("no current cell to export")
        return write_freecad_macro(self.library, name, path,
                                    layer_thickness=layer_thickness,
                                    component_models=component_models,
                                    loop_height_um=loop_height_um)

    def match_freecad_components(self, cell_name: Optional[str] = None,
                                  step_dir: Optional[str] = None):
        """Guess which placed cells in `cell_name` (default: the
        current cell) are surface-mounted parts matching a known
        package in the FreeCAD component STEP library already built
        into `step_dir` (e.g. via GUI's App.default_export_dir("freecad")
        -- Session has no notion of "the design's folder" itself, so
        `step_dir` is required here) -- see hlayout.io.component_match
        for the two matching strategies (by name for sized passives, by
        pad footprint for SOT-family/PKG20-style parts) and why a
        manual component_models entry always wins.

        Returns a hlayout.io.component_match.MatchReport -- pass its
        ``.component_models()`` straight to export_freecad()'s own
        `component_models=` to apply the auto-match without touching
        the saved technology file; call ``.summary_lines()`` for a
        human-readable report of what matched/didn't and why."""
        from ..io.component_match import build_match_report
        name = cell_name or self.current_cell_name
        if name is None:
            raise LibraryError("no current cell")
        if not step_dir:
            raise LibraryError("step_dir is required, e.g. App.default_export_dir('freecad')")
        return build_match_report(self.library, name, step_dir,
                                   component_models=load_component_models(self.library))

    def generate_component_library(self, out_dir: str, path: Optional[str] = None,
                                     ceramic_caps=None, pkg20=None, sot_packages=None,
                                     lcc_packages=None, plcc_packages=None):
        """Write a FreeCAD macro (``.FCMacro``) at ``path`` (default:
        ``<out_dir>/build_component_library.FCMacro``) that, run inside
        FreeCAD, builds a small library of generic 3D component
        bodies -- standard EIA ceramic chip-capacitor case sizes
        (0603/0805/1206 by default), a 20-pin sealed metal/ceramic
        hybrid flatpack, and the common SOT (small-outline-transistor)
        gull-wing package family (SOT-23/323/89/223 3-lead transistor
        outlines plus SOT-14/16/18 narrow-body SOIC-style ICs, by
        default) -- and exports each as its own ``.step`` file into
        ``out_dir``.

        Those STEP files are meant to be referenced from a placed
        cell's ``component_models`` tech-param (Setup > Technology
        Properties...): set its mode to "model" and model_path to one
        of the generated files, and export_freecad() then substitutes
        this real 3D body for that instance instead of a flat extruded
        footprint. This function only writes the macro text -- the
        STEP files themselves only exist once the macro is actually
        run inside FreeCAD.

        `ceramic_caps`, if given, overrides the default case-size list
        (a list of (name, length_mm, width_mm, height_mm,
        end_termination_length_mm) tuples; pass [] to skip capacitors
        entirely). `pkg20` overrides any subset of the 20-pin
        package's dimensions as a dict (e.g. {"leads_per_side": 8} for
        a 16-pin version), or pass pkg20=False to skip it.
        `sot_packages` overrides the default {name: kwargs} dict of SOT
        variants (see hlayout.io.component_library.DEFAULT_SOT_PACKAGES
        for the make_sot() kwargs shape) to add/replace variants (e.g.
        a SOT-23-5), or pass {} to skip the family entirely.
        `lcc_packages`/`plcc_packages` likewise override (or, with {},
        skip) the ceramic LCCC and plastic PLCC chip-carrier families --
        by default LCC20..LCC84 and PLCC20..PLCC84 at 20/28/32/44/52/68/84
        pins. Returns ``path``. See hlayout.io.component_library for
        details."""
        from ..io.component_library import write_component_library_macro
        return write_component_library_macro(out_dir, path=path, ceramic_caps=ceramic_caps,
                                              pkg20=pkg20, sot_packages=sot_packages,
                                              lcc_packages=lcc_packages,
                                              plcc_packages=plcc_packages)

    def print_cell(self, cell_name: Optional[str] = None, out_path: Optional[str] = None,
                    scale: Optional[float] = None, pin_label_layers=None) -> str:
        """Render the design (default: the current cell) as a
        printable HTML page -- a color-swatch legend plus an SVG
        drawing of its visible, flattened geometry -- and write it to
        ``out_path`` (a fresh temp .html file if not given). Returns
        the path written; File > Print... (and the PRINT command) then
        hand that off to the OS's own print flow, since Tkinter has no
        print dialog of its own.

        ``scale`` sets the printed size relative to true physical size
        (2.0 = twice actual size); left as None, a design smaller than
        hlayout.io.printio.PRINT_TARGET_MM is auto-scaled up to about
        that size (using a clean factor -- 2x, 5x, 10x, ...) instead of
        printing "at 100%" as a barely-visible speck, which is exactly
        what a true-size hybrid part a few mm across would otherwise
        do. Pass 1.0 to force true size regardless.

        ``pin_label_layers`` (layer numbers or names) draws a numbered
        label at every pin on those layers -- e.g. a bare die's own pad
        layer, so an assembly operator can see which numbered pad to
        land a wire bond on. Deliberately opt-in and scoped by layer,
        not automatic: an ordinary passive/resistor's own 2-terminal
        pins are ALSO pin-tagged, and labelling those too would be
        unwanted clutter on every part -- see
        hlayout.io.printio._flatten_for_print()'s own docstring.

        See hlayout.io.printio for exactly what does/doesn't render."""
        from ..io.printio import write_print_html
        name = cell_name or self.current_cell_name
        if name is None:
            raise LibraryError("no current cell to print")
        resolved_pin_layers = ([self.library.layers.resolve(n) for n in pin_label_layers]
                                if pin_label_layers else None)
        return write_print_html(self.library, name, out_path, scale=scale,
                                 pin_label_layers=resolved_pin_layers)

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
    #: tech_params entries undo / redo leave as they are (see _restore)
    UNDO_KEEPS_PARAMS = ("autoplace_settings", "resistor_list")

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

    def delete_last(self):
        """Remove the most recently added shape, sref or aref from the
        current cell -- the DELETE LAST command. One undo step. Returns
        "shape", "sref" or "aref" for what went, or None if the cell is
        empty.

        A cell keeps shapes, srefs and arefs in three separate lists, so
        "most recent" is read off the undo history: the newest snapshot
        in which one of the three lists was shorter than it is now says
        which kind grew last. With no history to go on (a freshly opened
        file) it falls back to shape, then sref, then aref."""
        cell = self.cell
        lists = (("shape", cell.shapes), ("sref", cell.srefs), ("aref", cell.arefs))
        if not any(items for _, items in lists):
            return None
        pick = None
        for snap in reversed(self._undo_stack):
            old = snap.cells.get(cell.name)
            was = (len(old.shapes), len(old.srefs), len(old.arefs)) if old else (0, 0, 0)
            grown = [i for i in range(3) if was[i] < len(lists[i][1])]
            if grown:
                pick = grown[-1]
                break
        if pick is None:
            pick = next(i for i in range(3) if lists[i][1])
        self._checkpoint()
        lists[pick][1].pop()
        self._notify()
        return lists[pick][0]

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
        # what the user has entered in the design's tools -- the Auto
        # Place settings and cell map, the resistor list and designs -- is
        # not geometry: undo / redo of an edit must not throw it away
        keep = {k: self.library.tech_params[k] for k in self.UNDO_KEEPS_PARAMS
                if k in self.library.tech_params}
        self.library.tech_params = dict(snapshot.tech_params)
        self.library.tech_params.update(keep)
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
             magnification: float = 1.0, x_reflection: bool = False, label: str = "",
             pin: str = ""):
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
                                  label=label, pin=pin)
        # Connectors/headers (J1, P2, ...) get their ref-des beside pin 1.
        if is_connector_refdes(label):
            off = refdes_pin1_offset(self.library, ref)
            if off is not None:
                ref.label_offset = off
        self._notify()
        return ref

    def refdes_to_pin1(self, target=None, gap: Optional[int] = None,
                       cell_name: Optional[str] = None) -> List[str]:
        """Move a placed part's floated ref-des to beside its pin 1
        (hlayout.core.refdes.refdes_pin1_offset): `gap` DBU beyond pin
        1's pad, on the outside of the pin row. `target` is an SRef, a
        ref designator ("J1"), or None for every connector/header
        (J, P, X, CN, CON, HDR + number) in the cell. Parts without a
        pin 1 are skipped. Returns the ref designators moved. One undo
        step. sref()/place_cell() already do this for a connector label
        given at placement."""
        cell = self.library.get_cell(cell_name) if cell_name else self.cell
        if target is None:
            refs = [r for r in cell.srefs if is_connector_refdes(r.label)]
        elif isinstance(target, str):
            refs = [r for r in cell.srefs if r.label == target]
            if not refs:
                raise LayoutError(f"no placed instance labelled {target!r} in {cell.name!r}")
        else:
            refs = [target]
        g = DEFAULT_PIN1_GAP if gap is None else int(gap)
        moves = [(r, refdes_pin1_offset(self.library, r, g)) for r in refs]
        moves = [(r, off) for r, off in moves if off is not None]
        if moves:
            self._checkpoint()
            for r, off in moves:
                r.label_offset = off
            self._notify()
        return [r.label or r.cell_name for r, _ in moves]

    def place_cell(self, cell_name: str, origin, source: Union[str, int, None] = None,
                    rotation: float = 0.0, magnification: float = 1.0, x_reflection: bool = False,
                    label: str = "", pin: str = ""):
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
        the same field Edit Selected... calls "Label". `pin`, if given,
        sets this placement's own pin number (SRef.pin) -- for a round
        lead placed from a shared via-cell template (Session.
        create_via_cell()/the Via Wizard), where the pin number can't
        live on the template's own shapes since every placement of it
        would share that number.
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
                              label=label, pin=pin)

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
                simplify: Optional[float] = None,
                join_style: str = "mitre") -> dict:
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

        `join_style` (default "mitre") is passed straight through to
        OVERSIZE/UNDERSIZE's underlying buffer -- see
        hlayout.core.maskops.size_geometry() for why you'd ever want
        "round" or "bevel" instead: mainly, growing/shrinking geometry
        that already includes a many-facet curve (e.g. a castellation
        scallop unioned with a pad), where the default mitre join
        produces a small spurious spike/notch at the facet-to-straight
        seam. Ignored for AND/OR/NOT/XOR (those have no offset to
        join-style).

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
                result = size_geometry(geom_a, amount, join_style=join_style)
            elif op == "UNDERSIZE":
                result = size_geometry(geom_a, -amount, join_style=join_style)
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

    def convert_rects_to_outline(self, layer, width: float,
                                  cell_names: Optional[List[str]] = None) -> dict:
        """Replace every Rectangle or Polygon on `layer`, in the named
        cells' own shapes (never descending into placed sub-cells --
        this converts exactly the shape actually drawn there, in
        place), with an equivalent closed-ring Path tracing the same
        corners, `width` DBU wide -- e.g. so a substrate or package
        body is a stroked OUTLINE instead of a filled, closed shape (a
        solid fill is often the wrong thing for a silkscreen/reference
        marking, or for a downstream tool that treats "closed filled
        shape" and "open stroked outline" differently). A Rectangle
        always has 4 corners; a Polygon can have up to 200 -- either
        way, the SAME corner points become the outline path's own
        points, just closed into a ring and given a stroke width
        instead of a fill.

        Runs on the current cell by default; pass `cell_names` to
        target other/additional specific cells directly (e.g. a
        substrate or package cell's own name, not whatever cell places
        it via Sref -- there's nothing to flatten here, since the
        shape being replaced has to actually live in the cell being
        operated on).

        Returns {cell_name: shapes_converted} for cells that had at
        least one matching Rectangle/Polygon on `layer` (empty results
        are omitted)."""
        r_layer = self.library.layers.resolve(layer)
        w = int(round(width))
        if w <= 0:
            raise LayoutError("width must be positive")
        self._checkpoint()
        cells = ([self.library.get_cell(n) for n in cell_names] if cell_names
                 else [self.cell])
        written = {}
        for cell in cells:
            closed = [sh for sh in cell.shapes
                      if isinstance(sh, (Rectangle, Polygon)) and sh.layer == r_layer]
            for shape in closed:
                pts = [tuple(p) for p in shape.points]
                pts.append(pts[0])  # close the ring
                cell.remove(shape)
                cell.add_path(r_layer, pts, w, cap="flush", datatype=shape.datatype)
            if closed:
                written[cell.name] = len(closed)
        self._notify()
        return written

    def clear_layer(self, layer, cell_names: Optional[List[str]] = None) -> dict:
        """Delete every shape on `layer` from the named cells' own
        shapes (never descending into placed sub-cells -- this deletes
        exactly what's drawn directly in the cell being operated on,
        the same scope mask_op_top_view()/convert_rects_to_outline()
        use). The obvious use is cleaning up a scratch/result layer
        between runs of a mask operation. (mask_op_top_view() no longer
        needs this: it rebuilds its own TOPVIEW output cell each run.)

        Runs on the current cell by default; pass `cell_names` to
        target other/additional specific cells directly.

        Returns {cell_name: shapes_deleted} for cells that had at least
        one shape on `layer` (empty results are omitted)."""
        r_layer = self.library.layers.resolve(layer)
        self._checkpoint()
        cells = ([self.library.get_cell(n) for n in cell_names] if cell_names
                 else [self.cell])
        written = {}
        for cell in cells:
            doomed = [sh for sh in cell.shapes if sh.layer == r_layer]
            for shape in doomed:
                cell.remove(shape)
            if doomed:
                written[cell.name] = len(doomed)
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






    def run_drc(self, rules=None, cell_names: Optional[List[str]] = None,
                error_layer=None) -> List["DrcViolation"]:
        """Run Design Rule Check (DRC) rules -- MIN_WIDTH, MIN_AREA,
        MIN_SPACE (single-layer), MIN_SPACE_BETWEEN, MIN_ENCLOSURE
        (between two layers), EDGE_CLEARANCE (layer_a = conductor,
        layer_b = substrate: every conductor shape on the substrate and
        at least value DBU in from its edge), and the wire-bond rules BOND_MIN_LENGTH /
        BOND_MAX_LENGTH (layer_a = bond layer, value in DBU) and
        BOND_MAX_ANGLE (layer_a = bond layer, layer_b = die-pad layer,
        value in degrees off the die-edge normal) -- against the
        design. add_bond_drc_rules() saves the bond rules from Setup >
        Wire Bond Rules.... See hlayout.core.drc
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












    def send_to_solidcraft(self, cell_name: Optional[str] = None, window: bool = True,
                           export: Optional[str] = None, app: Optional[str] = None,
                           on_progress=None, cancel=None) -> dict:
        """Build the current (or named) cell as a 3D model in SolidCraft
        (github.com/bherbruck/solidcraft) through its MCP server -- the
        FreeCAD-export stack: substrate, printed films at their Setup
        thickness, parts, wire bonds. ``window`` opens SolidCraft and
        builds in front of you; ``export`` also writes a .step / .3mf /
        .stl / .obj. ``app`` = the folder holding solidcraft-cli (default:
        File > 3D View in SolidCraft's saved folder, PATH, ~/.hlayout).
        Returns {"bodies", "batches", "export", "errors", "port"}. See
        hlayout.io.solidcraftio."""
        from ..io.solidcraftio import send_to_solidcraft as _send
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell")
        if app is None:
            try:
                from ..io import app_settings as _as
                app = _as.get_setting("solidcraft_path") or None
            except Exception:
                app = None
        path = getattr(self, "current_path", None)
        return _send(self.library, name, app=app, window=window, export=export,
                     on_progress=on_progress, cancel=cancel,
                     model_dir=os.path.dirname(os.path.abspath(path)) if path else None)






































    def find_shorts(self, cell_name: Optional[str] = None) -> list:
        """Short circuits: copper that joins pads of two or more design
        nets (net names, see import_net_names()). Spacing DRC can't see
        these -- touching copper of two nets is one shape to it. Returns
        [{"nets": [names], "pins": {net: [pads]}, "at": [(x, y)]}] -- ``at``
        the via windows / crossings where the nets' copper meets."""
        from collections import deque
        from shapely.geometry import box as _box
        from shapely.ops import unary_union
        from ..io.techfile import load_net_names
        names = load_net_names(self.library)
        if not names:
            return []
        name = cell_name or self.current_cell_name
        tr = self.net_tracer(cell_name=name)
        groups = {}
        for p, n in names.items():
            if p in tr.pin_node:
                groups.setdefault(tr.dsu.find(tr.pin_node[p]), {}).setdefault(n, []).append(p)
        out = []
        cell = self.library.get_cell(name)
        diel = dielectric_layer_number(self.library)
        wins = unary_union([_box(*sh.bbox()) for sh in cell.shapes if sh.layer == diel
                            and getattr(sh, "datatype", 0) == 0 and hasattr(sh, "bbox")]) \
            if diel >= 0 else None
        nodes, geoms = tr.nodes, tr._geoms
        for root, by_net in groups.items():
            if len(by_net) < 2:
                continue
            # where do the nets meet? grow each net out from its own pads
            lab, q, at = {}, deque(), []
            for n, pins in by_net.items():
                for p in pins:
                    j = tr.pin_node[p]
                    if j not in lab:
                        lab[j] = n
                        q.append(j)
            while q and len(at) < 6:
                j = q.popleft()
                for k in tr._query(geoms[j]):
                    if k == j or not geoms[k].intersects(geoms[j]):
                        continue
                    ov = geoms[k].intersection(geoms[j])
                    if nodes[k][0] != nodes[j][0] and wins is not None and \
                            ov.intersection(wins).area < 1:
                        continue
                    if k not in lab:
                        lab[k] = lab[j]
                        q.append(k)
                    elif lab[k] != lab[j]:
                        c = ov.representative_point()
                        xy = (round(c.x), round(c.y))
                        if xy not in at:
                            at.append(xy)
            out.append({"nets": sorted(by_net), "pins": {n: sorted(v) for n, v in by_net.items()},
                        "at": at})
        return out



    def net_tracer(self, layers=None, cell_name: Optional[str] = None):
        """A hlayout.core.nettrace.NetTracer for ``cell_name`` (default:
        the current cell) over ``layers`` -- default: Setup > Route
        Layers' two conductors (layer_a/layer_b), falling back to the
        Tools > Extract Netlist... conductor selection -- with the
        dielectric fill from Setup > Dielectric Fill Rules... deciding
        where the two layers actually connect (via windows only)."""
        from ..core.nettrace import NetTracer
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        resolve = self.library.layers.resolve
        if layers is None:
            rr = load_route_rules(self.library)
            layers = [l for l in (rr.get("layer_a"), rr.get("layer_b"))
                      if isinstance(l, int) and l >= 0]
            if not layers:
                layers = list(load_netlist_settings(self.library).get("conductor_layers") or [])
        layers = [resolve(l) for l in layers]
        if not layers:
            raise LayoutError("no conductor layers -- pass layers=... or set Setup > Route Layers...")
        diel = dielectric_layer_number(self.library)
        if diel in layers:
            diel = -1
        return NetTracer(self.library, self.library.get_cell(name), layers,
                         dielectric_layer=diel if diel >= 0 else None,
                         fill_datatype=self.DIELECTRIC_FILL_DATATYPE)

    def highlight_net(self, target, layers=None, cell_name: Optional[str] = None,
                      prefer_layer=None, tol: float = 0.0, notify: bool = True):
        """Trace the net of ``target`` -- an (x, y) DBU point on a pad or
        trace, a pad name "RefDes.Pin" (a pad drawn directly in the cell
        is "IO.<pin>"), or a design net name (see set_net_names() /
        import_net_names(); every routed fragment of that net is
        highlighted, joined or not; a point or pad on a named net shows the
        whole net the same way) -- and make it the current highlight
        (``self.net_highlight``; the GUI draws every piece of copper and
        every pad on it). Returns the hlayout.core.nettrace.NetTrace,
        with ``.name`` the design net name ("unnamed" if none is known,
        "A + B (SHORT)" if pads of two named nets are connected),
        ``.fragments`` the number of separate copper groups and
        ``.missing`` the named net's pads not on this copper. Raises
        LayoutError if there's no pad/track there or no such pad or net.
        ``tol`` (DBU) lets a click just beside a pad or thin track still
        pick it (the nearest piece within ``tol`` wins).
        Highlighting never edits the design. See clear_net_highlight()."""
        from ..io.techfile import load_net_names
        tracer = self.net_tracer(layers=layers, cell_name=cell_name)
        names = load_net_names(self.library)
        tr = None
        if isinstance(target, str):
            key = target.strip()
            tr = tracer.trace_pin(key)
            if tr is None:
                tr = tracer.trace_net_name(key, names)
            if tr is None:
                raise LayoutError(f"There is no pad or net called {key!r}. Use a pad name "
                                  f"like U1.5 (I/O pads are IO.<pin>) or a net name.")
        else:
            x, y = target
            tr = tracer.trace_point(x, y, prefer_layer=prefer_layer, tol=tol)
            if tr is None:
                lay = " or ".join(f"{n} ({self.library.layers[n].name})" for n in tracer.layers)
                raise LayoutError(f"Nothing to highlight at ({x / 1000:.3f}, {y / 1000:.3f}) mm: "
                                  f"click on a pad or track on layer {lay}.")
        # A click (or a pad name) traces the copper joined to that spot;
        # when its pads all carry one design net name, show EVERY piece
        # of that net -- the fragments not yet joined to this one too.
        # ``missing`` stays what isn't joined to the piece picked.
        tr.name_from(names)
        missing = tr.missing_pads(names)
        nets = {names[p].upper(): names[p] for p in tr.pins if p in names}
        if len(nets) == 1:
            full = tracer.trace_net_name(next(iter(nets.values())), names)
            if full is not None and len(full.pieces) > len(tr.pieces):
                full.seed = tr.seed
                tr = full
                tr.name_from(names)
        tr.fragments = len({tracer.dsu.find(tracer.pin_node[p]) for p in tr.pins
                            if p in tracer.pin_node}) or 1
        tr.missing = missing
        tr.cell_name = cell_name or self.current_cell_name
        tr.pad_boxes = [tracer.pin_box(p) for p in tr.pins if tracer.pin_box(p)]
        tr.trace_layers = layers
        self.net_highlight = tr
        if notify:
            self._notify()
        return tr

    def retrace_net_highlight(self):
        """Trace the highlighted net again on the design as it is now -- a
        part moved, a track added -- so the highlight follows the copper
        and pads it shows: by its design net name when it has one, else by
        one of its pads (which moves with its part). Keeps the old
        highlight if the net is gone. Returns the current highlight."""
        tr = getattr(self, "net_highlight", None)
        if tr is None:
            return None
        name = tr.name
        if len(tr.net_names) == 1:
            target = name
        elif tr.pins:
            target = tr.pins[0]
        else:
            return tr
        try:
            return self.highlight_net(target, layers=getattr(tr, "trace_layers", None),
                                      cell_name=getattr(tr, "cell_name", None), notify=False)
        except Exception:
            return tr

    def set_net_names(self, pad_nets: dict, replace: bool = True) -> int:
        """Store the design's intended netlist as {"RefDes.Pin": net}
        (module I/O pads are "IO.<pin>") -- what Highlight Net names a
        traced net by. ``replace=False`` merges into the existing map.
        Returns the number of named pads."""
        from ..io.techfile import load_net_names, save_net_names
        cur = {} if replace else load_net_names(self.library)
        cur.update({str(k): str(v) for k, v in pad_nets.items()})
        save_net_names(self.library, cur)
        return len(cur)

    def read_netlist(self, path: str, resistors: bool = True) -> dict:
        """Step 1 of the design flow: read the design's netlist (KiCad .net,
        CSV or Tango) -- every pad's net name stored, and every printed
        resistor put in the Resistor Designer's list to be designed before
        Auto Place lays the parts out. See
        hlayout.scripting.netlist_flow.read_design_netlist()."""
        from .netlist_flow import read_design_netlist
        return read_design_netlist(self, path, resistors=resistors)

    def import_net_names(self, path: str, replace: bool = True) -> int:
        """Read pad -> net names from a CSV with a header row: either a
        ``pad`` column ("U1.5") or ``ref``/``refdes`` + ``pin`` columns,
        plus a ``net`` (or ``net_name``/``signal``) column. Column names
        are case-insensitive. Returns the number of named pads stored
        (see set_net_names())."""
        import csv
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            raise LayoutError(f"{path}: no rows")
        cols = {c.lower().strip(): c for c in rows[0].keys() if c}
        net_c = next((cols[k] for k in ("net", "net_name", "netname", "signal") if k in cols), None)
        pad_c = next((cols[k] for k in ("pad", "refdes.pin", "pin_name") if k in cols), None)
        ref_c = next((cols[k] for k in ("ref", "refdes", "ref_des", "designator") if k in cols), None)
        pin_c = next((cols[k] for k in ("pin", "pin_number", "pad_number") if k in cols), None)
        if net_c is None or (pad_c is None and not (ref_c and pin_c)):
            raise LayoutError(f"{path}: need a 'net' column and 'pad' or 'ref'+'pin' columns "
                              f"(found {sorted(cols)})")
        out = {}
        for r in rows:
            net = (r.get(net_c) or "").strip()
            pad = (r.get(pad_c) or "").strip() if pad_c else \
                f"{(r.get(ref_c) or '').strip()}.{(r.get(pin_c) or '').strip()}"
            if net and pad and not pad.startswith(".") and not pad.endswith("."):
                out[pad] = net
        return self.set_net_names(out, replace=replace)

    def clear_net_highlight(self) -> None:
        """Remove the Highlight Net overlay."""
        if getattr(self, "net_highlight", None) is not None:
            self.net_highlight = None
            self._notify()




    # --- thermal analysis ---------------------------------------------------
    def thermal_parts(self, cell_name: Optional[str] = None) -> List[dict]:
        """Every placed resistor and component in ``cell_name`` (default:
        the current cell) that can be given a power, as
        [{"ref_des", "kind", "cell", "power_mw", "source"}, ...].
        ``source`` is "thermal" (set by set_thermal_power()), "designer"
        (the Resistor Designer row with that reference designator),
        "cell" (no row has that reference designator, so the row the
        resistor's cell was generated from) or "" (none: 0 mW). This
        is the list Tools > Thermal Analysis...'s Powers tab shows.
        Requires shapely."""
        from ..core.thermal_build import candidate_parts
        name = cell_name or self.current_cell_name
        if name is None:
            raise LibraryError("no current cell")
        return candidate_parts(self.library, name)

    def set_thermal_power(self, ref_des: str, power_mw: Optional[float]) -> None:
        """Set the power (milliwatts) a part dissipates in a thermal
        analysis, by reference designator. A resistor left unset uses
        its Resistor Designer power; anything else left unset is 0.
        Pass None to clear a saved power. Saved with the technology
        (THERMAL POWER <ref_des> <mW>)."""
        from ..io.techfile import load_thermal_rules, save_thermal_rules
        rules = load_thermal_rules(self.library)
        key = str(ref_des)
        if power_mw is None:
            rules["powers"].pop(key, None)
        else:
            p = float(power_mw)
            if p < 0:
                raise LayoutError("power must not be negative")
            rules["powers"][key] = p
        self._checkpoint()
        save_thermal_rules(self.library, rules)
        self._notify()

    def run_thermal(self, cell_name: Optional[str] = None, grid: Optional[float] = None,
                    base_temp: Optional[float] = None, rules: Optional[dict] = None):
        """Steady-state thermal analysis of ``cell_name`` (default: the
        current cell): how hot each printed resistor, die and powered
        component runs.

        The layout is turned into a stack of slices on a grid -- the
        substrate, then each printed film where it is actually printed,
        then powered components -- and heat conduction is solved with
        each part's power injected where it sits (see
        hlayout.core.thermal / thermal_build). Film thicknesses and
        layer roles are the ones FreeCAD export uses; materials, powers,
        part build-ups, boundary conditions and limits come from the
        thermal rules (hlayout.io.techfile.load_thermal_rules(); Tools >
        Thermal Analysis... edits them).

        ``grid`` is the X/Y cell size in DBU (micron); left out, the
        saved value or an automatic one (100 um or coarser) is used.
        ``base_temp`` overrides the saved base temperature (deg C) for
        this run. ``rules`` substitutes a complete rules dict for the
        saved one without saving it.

        Returns a hlayout.core.thermal.ThermalResult: ``.parts`` (one
        PartResult per part: ref_des, kind, power_mw, t_max, t_avg,
        rise, limit, ok), ``.failures``, ``.max_temp``,
        ``.temperature_at((x, y))``, ``.slice_temps(which)``,
        ``.energy_error`` and ``.summary_lines()``. The result is also
        kept as ``session.last_thermal_result``. Never modifies the
        design. Requires numpy, scipy and shapely."""
        from ..core.thermal_build import build_thermal_model
        from ..io.techfile import load_thermal_rules
        name = cell_name or self.current_cell_name
        if name is None:
            raise LibraryError("no current cell")
        rules = rules or load_thermal_rules(self.library)
        model = build_thermal_model(self.library, name, grid_um=grid, base_temp=base_temp,
                                    rules=rules)
        result = model.solve(rules["limits"])
        self.last_thermal_result = result
        return result

    def export_thermal_report(self, result, path: str) -> str:
        """Write run_thermal()'s result as a self-contained HTML report
        (parts table with failures first, temperature maps, stack and
        materials, boundary conditions, energy balance). Returns
        ``path``."""
        from ..io.techfile import load_thermal_rules
        from ..io.thermalio import write_thermal_html
        return write_thermal_html(result, path,
                                  display=load_thermal_rules(self.library)["display"])

    def export_thermal_csv(self, result, path: str) -> str:
        """Write run_thermal()'s parts table to a CSV file, hottest part
        first. Returns ``path``."""
        from ..io.thermalio import write_thermal_csv
        return write_thermal_csv(result, path)

    def draw_thermal_contours(self, result, layer, step: float = 5.0, which=None,
                              width: Optional[float] = None,
                              cell_name: Optional[str] = None) -> int:
        """Draw run_thermal()'s isotherms as Path shapes on ``layer`` of
        ``cell_name`` (default: the current cell), one contour every
        ``step`` deg C, so the temperature picture can be viewed over
        the layout and printed with the assembly drawing. ``which``
        picks the slice (a name, an index, or "layer:<n>"); left out,
        the top surface is used. ``width`` (DBU) defaults to a fifth of
        the thermal grid pitch. Appends to the layer -- CLEAR it first
        for a clean redraw. One undo step; returns the number of paths
        drawn. Requires shapely."""
        import numpy as np
        from shapely.geometry import LineString
        from shapely.ops import linemerge
        from ..core.thermal import contour_segments
        if not step > 0:
            raise LayoutError("contour step must be positive")
        r_layer = self.library.layers.resolve(layer)
        name = cell_name or self.current_cell_name
        if name is None:
            raise LibraryError("no current cell")
        cell = self.library.get_cell(name)
        model = result.model
        grid = result.surface_temps() if which is None else result.slice_temps(which)
        if not np.isfinite(grid).any():
            return 0
        lo, hi = float(np.nanmin(grid)), float(np.nanmax(grid))
        ref = model.reference_temp()
        # Levels are the reference temperature plus whole steps, strictly
        # inside the field's range. The margin keeps solver round-off (a
        # cell at 24.99999998 C on a 25 C base) from producing a junk
        # "contour" at the reference temperature itself.
        tol = 1e-6 * max(hi - lo, 1e-9)
        k0 = int(np.floor((lo + tol - ref) / step)) + 1
        k1 = int(np.ceil((hi - tol - ref) / step)) - 1
        levels = [ref + k * step for k in range(k0, k1 + 1)]
        pitch, (ox, oy) = model.pitch_um, model.origin
        lines = []
        for level in levels:
            for (x0, y0), (x1, y1) in contour_segments(grid, float(level)):
                a = (ox + (x0 + 0.5) * pitch, oy + (y0 + 0.5) * pitch)
                b = (ox + (x1 + 0.5) * pitch, oy + (y1 + 0.5) * pitch)
                if a != b:
                    lines.append(LineString([a, b]))
        if not lines:
            return 0
        merged = linemerge(lines)
        w = int(round(width)) if width else max(10, int(round(pitch / 5.0)))
        paths = shapely_lines_to_paths(merged, r_layer, w)
        with self._batch():
            cell.shapes.extend(paths)
        self._notify()
        return len(paths)

    def extract_netlist(self, conductor_layers=None, cell_name: Optional[str] = None,
                        include_bonds: bool = True) -> List["Net"]:
        """Extract the electrical netlist of `cell_name` (default: the
        current cell), as seen across `conductor_layers` -- an iterable
        of layer numbers or names (each resolved the same way every
        other layer argument in this API is). See hlayout.core.netlist
        for exactly how this works: every touching/overlapping piece of
        copper on those layers becomes one Net, and Rectangle.pin/
        Polygon.pin plus the owning SRef's .label identify each net's
        members as RefDes.Pin.

        `conductor_layers=None` uses the design's saved selection (the
        Conductor checkboxes in Tools > Extract Netlist...,
        techfile.load_netlist_settings()); otherwise pass the layers
        explicitly -- e.g. `s.extract_netlist([31])` for a
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

        `include_bonds` (default True): if Setup > Technology Properties (Wire Bonds tab) has
        a bond layer configured, it's added to `conductor_layers`
        automatically so every WIRE BOND (wire_bond()/BOND) is treated
        as conductor and its net is reported straight through to the
        die pin it lands on. Only a bond's two ENDS connect (the wire
        flies over whatever lies between), and the rules' isolated pad
        layers (die pads, header pins), if set, are kept isolated from
        the substrate copper underneath -- see hlayout.core.netlist. Pass include_bonds=False to see
        only the conductor layers you named (bond layer then treated as
        plain copper only if you list it)."""
        from ..core.netlist import extract_netlist as _extract_netlist

        if conductor_layers is None:
            # The design's saved Tools > Extract Netlist... selection
            # (load_netlist_settings) -- the same layers the dialog's
            # Conductor checkboxes show.
            conductor_layers = load_netlist_settings(self.library)["conductor_layers"]
            if not conductor_layers:
                raise LayoutError(
                    "no conductor layers given and none saved -- pass a layer "
                    "list or tick them in Tools > Extract Netlist..."
                )
        cell = self.library.get_cell(cell_name) if cell_name else self.cell
        resolved = [self.library.layers.resolve(l) for l in conductor_layers]
        rules = load_bond_rules(self.library)
        die_pads = list(rules.get("die_pad_layers", []))
        b_layer = int(rules["layer"]) if include_bonds else -1
        return _extract_netlist(self.library, cell, resolved,
                                bond_layer=b_layer if b_layer >= 0 else None,
                                die_pad_layers=die_pads)

    def pick_and_place(self, cell_name: Optional[str] = None, origin="substrate",
                       include_printed: bool = False, include_dies: bool = True) -> dict:
        """The pick-and-place (centroid) table of the current (or named)
        cell: one row per placed part with a reference designator --
        {"designator", "comment", "footprint", "kind" (smd / die /
        connector / printed), "x_mm", "y_mm", "rotation" (deg CCW, 0-360),
        "side", "mirrored", "x_dbu", "y_dbu"} -- in ref-des order.

        The centroid is the centre of the part's pad pattern (its body for
        a bare die, or a part with no numbered pads); the rotation is the
        placement angle composed through every enclosing instance.
        ``origin`` is "substrate" (lower-left corner of the substrate --
        the default; the design origin when there is no substrate),
        "design" (0, 0) or an (x, y) DBU point. Printed thick-film
        resistors are left out unless ``include_printed``. Returns {"rows",
        "origin", "origin_kind"}. See hlayout.io.pnpio."""
        from ..io.pnpio import pick_and_place_rows
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        return pick_and_place_rows(self.library, name, origin=origin,
                                   include_printed=include_printed, include_dies=include_dies)

    def export_pick_and_place(self, path: str, cell_name: Optional[str] = None,
                              fmt: Optional[str] = None, origin="substrate",
                              include_printed: bool = False, include_dies: bool = True,
                              header: bool = True, design_name: Optional[str] = None) -> dict:
        """Write the pick-and-place file of the current (or named) cell
        (see pick_and_place()). ``fmt`` is "csv" (Designator, Comment,
        Footprint, Kind, Mid X, Mid Y, Rotation, Side, Mirrored) or
        "kicad" (.pos footprint positions); None picks it from the
        extension (.pos = KiCad, else CSV). Both are stamped with the
        date and time they were created; a CSV carries it in "#" comment
        lines above the column row, which ``header=False`` leaves out.
        ``design_name`` (the header's design) defaults to the file name
        of the design opened or saved with open_design()/save_design(),
        else the library name. Returns the table, with "format", "path"
        and "created" added."""
        from ..io.pnpio import write_pick_and_place
        table = self.pick_and_place(cell_name, origin=origin, include_printed=include_printed,
                                    include_dies=include_dies)
        if design_name is None:
            dp = getattr(self, "design_path", None)
            design_name = os.path.basename(dp) if dp else (self.library.name or "")
        table["format"] = write_pick_and_place(table, path, fmt=fmt, design_name=design_name,
                                               header=header)
        table["path"] = path
        return table

    def export_netlist(self, nets: List["Net"], path: str, fmt: Optional[str] = None,
                       cell_name: Optional[str] = None) -> str:
        """Write `nets` (from extract_netlist()) to a file another tool can
        read -- see hlayout.io.netlistio. `fmt` is "csv", "report",
        "kicad" or "tango"; None picks it from the extension (.csv, .txt,
        .net = KiCad, .tng = Tango/Protel). `cell_name` (default: the
        current cell) supplies the component list the EDA formats need.
        Returns the format written."""
        from ..io.netlistio import write_netlist
        from ..io.techfile import load_netlist_settings
        cell = cell_name or self.current_cell_name or ""
        layers = load_netlist_settings(self.library)["conductor_layers"]
        return write_netlist(nets, path, fmt=fmt, library=self.library, cell_name=cell,
                             design=self.library.name, layers=layers)

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
