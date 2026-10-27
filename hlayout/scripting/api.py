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
import json
import math
import os
from contextlib import contextmanager
from typing import Callable, List, Optional, Union

from ..core.cell import Cell
from ..core.errors import LayoutError, LibraryError
from ..core.library import Library, merge_libraries
from ..core.shapes import Polygon, Rectangle
from ..core.units import format_rkm, mm_to_dbu
from ..core.maskops import (
    MASK_OPS,
    boolean_op,
    fracture_polygons,
    hidden_line_top_view,
    layer_geoms_flattened,
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
    load_and_apply_techfile,
    load_bond_rules,
    load_component_models,
    load_dielectric_rules,
    load_drc_rules,
    load_netlist_settings,
    load_resistor_geometry_rules,
    load_route_rules,
    load_thin_film_rules,
    resistor_ink_layer,
    save_techfile,
)
from ..core.thinfilm import generate_serpentine_geometry, generate_top_hat_geometry
from ..core.refdes import DEFAULT_PIN1_GAP, is_connector_refdes, refdes_pin1_offset


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


def _matched_component_footprint(library, cell_name, component_models, role_layers=frozenset(),
                                  excluded_layers=frozenset(), split_packages=False,
                                  pad_layers=frozenset()):
    """The union, in world DBU coordinates, of every placed instance's
    OWN shapes (every layer -- pads included, not just an outline)
    whose cell is a recognized real component: an explicit
    component_models entry (manual or already auto-matched), a name
    carrying a known EIA size code (component_match.match_passive_by_name()),
    a footprint matching a known gull-wing package's pin pattern
    (component_match.match_gullwing_by_footprint()), or -- for a "bare"
    part identified by neither, e.g. a wire-bonded die built with
    Session.create_chip_cell() (Tools > Chip Wizard...) -- a cell that
    (a) has at least one child SRef/ARef of its own (a real chip/die
    cell always gets its pins placed AFTERWARDS as separate instances,
    per create_chip_cell()'s own convention) AND (b) whose own shapes
    are ALL on a layer that isn't one of the design's own configured
    resistor/conductor/glaze/route/bond/dielectric layers
    (``role_layers``, from Setup). A component's real body/package
    graphics (an outline path, an epoxy die-attach pad) are drawn this
    way precisely because they AREN'T a resistor ink, conductor, or
    glaze film -- and a part's actual physical size is irrelevant here
    (a transistor or bare die can be a fraction of a millimetre), so
    there's no size catalogue to match against at all.

    Both conditions are load-bearing, each guarding against a
    confirmed-live false positive: requiring ALL of a cell's own shapes
    to be non-role (not just ANY) is what tells a bare die apart from
    an ordinary DEVICE cell (e.g. a resistor built by
    generate_resistor(), whose own role-layer content -- ink, conductor
    pads, glaze -- is its primary geometry, with a keep-out margin
    merely incidental to it -- ``any`` non-role shape present wrongly
    caught that margin instead). Requiring at least one child instance
    is what tells a real chip/die apart from the design's own substrate
    cell -- structurally just as "all non-role" as a die's own outline,
    but with no children at all.

    Neither condition, though, can tell a small bare die apart from a
    sealed hybrid PACKAGE's own outer case/lid (e.g. a 20-pin flatpack
    built the same way -- outline + an ARef of external header pins) --
    both are, structurally, "graphics-only with children". Confirmed
    live: a real package case's own outline can legitimately span the
    ENTIRE board (it physically does sit above everything inside it),
    so recognizing it the same way as a small die is technically
    correct but useless for an inside/assembly view that wants to see
    THROUGH the case at the real circuit. ``excluded_layers`` (from
    mask_op_top_view()'s own ``exclude_layers=``) is the caller's way
    out: a graphics-only candidate whose own shapes are ALL on an
    excluded layer is walked into instead of recognized, the same
    escape hatch exclude_layers already gives every other tier.

    Deliberately NAME/FOOTPRINT/NON-ROLE-GRAPHICS ONLY, with no
    reference to layer thickness at all -- a top-down pictorial view is
    about stacking ORDER, not fired-film thickness, and a cell that
    isn't in component_models and matches none of the above is walked
    into instead, the same as any other non-component cell.

    A component's own body-outline shape is often a thin PATH (a
    stroked ring around the body, not a filled area -- see
    hlayout.core.smdlib/examples/generate_ceramic_caps.py), which
    shape_to_shapely() correctly turns into a "picture frame" polygon
    WITH A HOLE for real mask-op purposes (a printed outline trace
    really is just a thin ring of ink). For an occluding FOOTPRINT
    that's wrong: the component's physical body is solid, so its hole
    is filled in before use here -- otherwise the hidden-line result
    would show both the outer edge of the frame and the inner edge of
    its hole, looking like two concentric, disconnected outlines
    instead of one component silhouette.

    Recursing stops at the first recognized component along each
    branch (its own internals aren't decomposed further); an
    unrecognized cell is walked into looking for components placed
    deeper inside it. Requires shapely."""
    from ..core.maskops import (
        _compose_transforms,
        _require_shapely,
        _sref_point_transform,
        _transform_geom,
        shape_to_shapely,
    )
    from ..core.shapes import expand_arefs_to_srefs
    from ..io.component_match import (
        eia_size_tokens,
        footprint_pin_stats,
        gullwing_package_table,
        match_chip_carrier_by_name,
        match_gullwing_by_footprint,
        match_passive_by_name,
    )
    from shapely.geometry import MultiPolygon, Polygon
    from shapely.ops import unary_union

    def _fill_holes(geom):
        """Rebuild a (Multi)Polygon from just its exterior ring(s),
        dropping any interior holes -- a stroked outline PATH's own
        thin frame shape becomes the solid area it actually bounds."""
        if geom.geom_type == "Polygon":
            return Polygon(geom.exterior) if geom.interiors else geom
        if geom.geom_type == "MultiPolygon":
            return unary_union([_fill_holes(p) for p in geom.geoms])
        return geom

    _require_shapely()
    tokens = eia_size_tokens()
    table = gullwing_package_table()
    base_models = dict(component_models) if component_models else {}

    def is_recognized(cn):
        """True if ``cn`` is a recognized component -- see this
        function's own docstring for the four ways. A cell that isn't
        in component_models and matches none of them is walked into
        instead, contributing its own layers to the ordinary tiers."""
        if cn in base_models:
            return True
        if match_passive_by_name(cn, tokens) is not None:
            return True
        if match_chip_carrier_by_name(cn) is not None:
            # LCC/PLCC chip carriers (LCC20 only ever matched by luck,
            # its pad ring resembling a 20-pin gull-wing; LCC28 matched
            # nothing, so its body was never drawn in the top view)
            return True
        c = library.cells.get(cn)
        stats = footprint_pin_stats(c) if c is not None else None
        if stats is not None and match_gullwing_by_footprint(stats, table) is not None:
            return True
        if (c is not None and c.shapes and (c.srefs or c.arefs)
                and all(sh.layer not in role_layers for sh in c.shapes)
                and not all(sh.layer in excluded_layers for sh in c.shapes)):
            # Regression: the design's own substrate cell (a single
            # shape on its own outline/marking layer, no children at
            # all) is STRUCTURALLY identical to a die's outline from an
            # "all shapes non-role" test alone -- confirmed live, that
            # test alone wrongly re-caught it, resurrecting the exact
            # substrate-occludes-everything bug this whole recognition
            # scheme exists to avoid. A real chip/die cell always has
            # its pins placed AFTERWARDS as separate SRef instances
            # (create_chip_cell()'s own convention -- "die pins added
            # afterwards as instances of the chosen cell"); the
            # substrate never has any children at all. Requiring at
            # least one is what tells them apart.
            #
            # That still can't tell a small die apart from a sealed
            # package's own outer case/lid (confirmed live: a 20-pin
            # flatpack's outline legitimately spans the ENTIRE board,
            # swallowing every real component and all conductor/
            # resistor content inside it) -- excluded_layers is the
            # caller's explicit way out, the same escape hatch every
            # other tier already gets from exclude_layers=.
            return True
        return False

    geoms = []          # (component cell name, geometry) per placed shape
    detail = []         # (component cell name, geometry) -- every shape, nested too

    def deep(name, transform, owner, depth):
        """Every shape of `name` and of everything placed inside it --
        an enclosing package's pins/seals are placed instances."""
        if depth > 64 or name not in library.cells:
            return
        c = library.cells[name]
        for shape in c.shapes:
            g = shape_to_shapely(shape)
            if g is not None and not g.is_empty:
                detail.append((owner, _transform_geom(g, transform)))
        for r in list(c.srefs) + expand_arefs_to_srefs(c.arefs):
            deep(r.cell_name, _compose_transforms(transform, _sref_point_transform(r)),
                 owner, depth + 1)

    def walk(name, transform, depth=0):
        if depth > 64 or name not in library.cells:
            return
        cell = library.cells[name]
        for ref in list(cell.srefs) + expand_arefs_to_srefs(cell.arefs):
            child_transform = _compose_transforms(transform, _sref_point_transform(ref))
            if is_recognized(ref.cell_name):
                target = library.cells.get(ref.cell_name)
                if target is not None:
                    # pad_layers: the part's own pads on a conductor layer
                    # are NOT part of its occluding body -- they're left to
                    # the conductor tier (drawn, and cross-hatched, as
                    # conductor wherever the body doesn't cover them), and
                    # so is any outline shape that just traces one of them
                    pads = [shape_to_shapely(sh) for sh in target.shapes
                            if sh.layer in pad_layers] if pad_layers else []
                    pads = [g for g in pads if g is not None and not g.is_empty]
                    for shape in target.shapes:
                        if shape.layer in pad_layers:
                            continue
                        g = shape_to_shapely(shape)
                        if g is not None and not g.is_empty:
                            if pads and any(
                                    g.symmetric_difference(pg).area <= 1e-3 * max(pg.area, 1.0)
                                    for pg in pads if pg.envelope.intersects(g.envelope)):
                                continue
                            geoms.append((ref.cell_name, _transform_geom(g, child_transform)))
                    if split_packages:
                        deep(ref.cell_name, child_transform, ref.cell_name, 0)
            else:
                walk(ref.cell_name, child_transform, depth + 1)

    walk(cell_name, None)

    def _union(items):
        return _fill_holes(unary_union([g for _n, g in items])) if items else unary_union([])

    if not split_packages:
        return _union(geoms)
    # An ENCLOSING package -- a header/bathtub can the substrate sits
    # in -- is underneath the circuit, not on top of it: a component
    # model referenced to the substrate underside (z_ref
    # "substrate_bottom"), or a part whose footprint spans most of the
    # whole design. Returned separately so the top view can put it at
    # the bottom of the stack instead of letting it hide everything.
    pkg_names = {n for n, d in base_models.items()
                 if isinstance(d, dict) and d.get("z_ref") == "substrate_bottom"}
    try:
        bx = library.cell_bbox(cell_name)
    except Exception:
        bx = None
    if bx is not None:
        design_area = max((bx[2] - bx[0]) * (bx[3] - bx[1]), 1)
        per_cell = {}
        for n, g in geoms:
            per_cell.setdefault(n, []).append(g)
        for n, gs in per_cell.items():
            b = unary_union(gs).bounds
            if (b[2] - b[0]) * (b[3] - b[1]) >= 0.6 * design_area:
                pkg_names.add(n)
    top = [(n, g) for n, g in geoms if n not in pkg_names]
    # An enclosing package is drawn in DETAIL, not as one filled
    # silhouette: each of its shapes (outer case, well, pin seals, pins
    # -- nested placements included) as its own tier, smallest on top,
    # so the case edge, the well wall and every pin show wherever the
    # substrate doesn't cover them.
    pkg_parts = sorted((g for n, g in detail if n in pkg_names), key=lambda g: g.area)
    return _union(top), pkg_parts, pkg_names


# ISO 216 sheet sizes in mm, stored as (short_edge, long_edge) --
# portrait uses (short, long) as (width, height), landscape swaps them.
# Limited to A4/A3: hlayout.core.units.MAX_SIZE_MM caps the database at
# 500 x 500 mm, and A2 (594 mm) and larger don't fit inside that.
SHEET_SIZES_MM = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
}


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

    #: Names of the two die-pin device cells the Chip Wizard places --
    #: pin 1 uses CHIP_PIN1_CELL, every other pin uses CHIP_PIN_CELL.
    #: These are the user's own cells, taken from the attached parts
    #: library (or the current design); the wizard does not build them.
    CHIP_PIN_CELL = "chip_pin"
    CHIP_PIN1_CELL = "chip_pin1"

    #: The chip-graphics (body-outline) layer is forced to this colour
    #: by create_chip_cell() so the outline reads clearly over the
    #: copper underneath it.
    CHIP_OUTLINE_COLOR = "#ffffff"

    def create_chip_cell(self, name: str, length: float, width: float,
                         graphics_layer, epoxy_length: float, epoxy_width: float,
                         epoxy_layer, *, outline_width: Optional[float] = None,
                         overwrite: bool = False, attach_pad_layer=None,
                         attach_pad_margin: float = 200.0):
        """Start a bare-die / chip footprint cell in the CURRENT design:

          * a body outline -- a closed rectangular Path of ``length`` x
            ``width`` DBU, centred on the origin, on ``graphics_layer``,
            which is forced white and visible;
          * an ``epoxy_length`` x ``epoxy_width`` DBU epoxy die-attach
            pad, centred on the origin, on ``epoxy_layer`` (forced
            visible) -- rectangular, since chips are.

        ``attach_pad_layer`` (optional, a number or layer name) adds a
        conductor die-attach pad under the chip on that layer -- e.g. the
        2nd conductor -- ``attach_pad_margin`` DBU (default 200) larger
        than the body on every side. The autorouter keeps tracks clear of
        it (and of the die) by the die keep-out.

        The die *pins* are added afterwards as instances of the
        ``chip_pin`` / ``chip_pin1`` cells you provide (e.g. in the
        attached parts library); export_cell_to_library() then carries
        the chip and those pin cells into the library together.

        ``graphics_layer`` / ``epoxy_layer`` accept a number or an
        existing layer name. ``outline_width`` (DBU) defaults to 2% of
        the shorter body edge, min 1. Raises LayoutError on a
        non-positive size, or if ``name`` is already a cell here and
        ``overwrite`` is false. One undo step; the new cell becomes the
        current cell."""
        name = (name or "").strip()
        if not name:
            raise LayoutError("a cell name is required")
        if length <= 0 or width <= 0:
            raise LayoutError("chip length and width must be positive")
        if epoxy_length <= 0 or epoxy_width <= 0:
            raise LayoutError("epoxy pad length and width must be positive")
        g_layer = self.library.layers.resolve(graphics_layer)
        e_layer = self.library.layers.resolve(epoxy_layer)
        if name in self.library.cells:
            if not overwrite:
                raise LayoutError(
                    f"{name!r} already exists in this design; pass overwrite=True to replace it"
                )
            self.library.delete_cell(name)

        hl, hw = length / 2.0, width / 2.0
        ow = int(outline_width) if outline_width else max(1, int(round(min(length, width) * 0.02)))
        el, ew = epoxy_length / 2.0, epoxy_width / 2.0
        a_layer = self.library.layers.resolve(attach_pad_layer) \
            if attach_pad_layer is not None else None
        if attach_pad_layer is not None and (a_layer is None or a_layer < 0):
            raise LayoutError(f"no layer {attach_pad_layer!r} for the die-attach pad")
        am = float(attach_pad_margin or 0)

        with self._batch():
            cell = self.library.new_cell(name)
            self.current_cell_name = name
            if a_layer is not None:
                # conductor die-attach pad, `am` larger than the chip all round
                cell.add_rectangle(a_layer, -hl - am, -hw - am, hl + am, hw + am)
            cell.add_rectangle(e_layer, -el, -ew, el, ew)   # epoxy die-attach pad
            cell.add_path(
                g_layer,
                [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw), (-hl, -hw)],
                max(1, ow), cap="flush",
            )
            try:
                self.library.layers.set_style(
                    g_layer, color=self.CHIP_OUTLINE_COLOR, visible=True)
            except (KeyError, IndexError):
                pass
            try:
                self.library.layers.set_style(e_layer, visible=True)
            except (KeyError, IndexError):
                pass
        self._notify()
        return name

    def show_layers_of_cell(self, cell_name: str) -> None:
        """Make every layer that ``cell_name`` (in the current design)
        draws on visible -- used by the Chip Wizard so a freshly placed
        ``chip_pin`` / ``chip_pin1`` instance is actually shown even if
        its layers were switched off."""
        c = self.library.cells.get(cell_name)
        if c is None:
            return
        for sh in c.shapes:
            try:
                self.library.layers.set_style(sh.layer, visible=True)
            except (KeyError, IndexError):
                pass
        self._notify()

    def export_cell_to_library(self, cell_name: str, index: int = 0,
                               overwrite: bool = False, reuse_shared: bool = True):
        """Copy ``cell_name`` -- and its Sref dependency closure -- from
        the current design into attached reference library ``index``
        (default 0, the first attached), then save that library's .gds
        file in place. The finished part is then available to every
        design that attaches this library. Returns
        ``(name_in_library, path)``.

        With ``reuse_shared`` (the default), any sub-cell of the closure
        whose name already exists in the target library is referenced in
        place instead of copied in under a fresh suffixed name -- so
        re-exporting a part that shares a primitive (the Chip Wizard's
        ``chip_pin``/``chip_pin1``, a common pad cell, ...) doesn't pile
        up ``name_1``, ``name_2`` duplicates. It assumes a same-named
        sub-cell already there is the same primitive; pass
        ``reuse_shared=False`` to force fresh copies of everything.

        Raises LayoutError if no library is attached at ``index``, the
        cell isn't in the current design, or a cell of that name already
        lives in the target library and ``overwrite`` is false."""
        if not self.attached_libraries:
            raise LayoutError(
                "no attached library -- attach one first (Setup > Attach Library...)"
            )
        if index < 0 or index >= len(self.attached_libraries):
            raise LayoutError(f"no attached library at index {index}")
        if cell_name not in self.library.cells:
            raise LayoutError(f"no cell named {cell_name!r} in the current design")

        lib = self.attached_libraries[index]
        if cell_name in lib.cells:
            if not overwrite:
                raise LayoutError(
                    f"{cell_name!r} already exists in the attached library; "
                    "pass overwrite=True to replace it"
                )
            lib.delete_cell(cell_name)

        reuse = None
        if reuse_shared:
            reuse = [n for n in self._cell_closure_names(cell_name) if n != cell_name]
        local_name = lib.import_cell_closure(self.library, cell_name, reuse_names=reuse)
        path = self._attached_paths[index]
        save_gds(lib, path)
        self._notify()
        return local_name, path

    def _cell_closure_names(self, root: str):
        """Every cell name reachable from ``root`` in the current design
        via Srefs/Arefs, ``root`` included."""
        seen = set()
        stack = [root]
        while stack:
            name = stack.pop()
            if name in seen or name not in self.library.cells:
                continue
            seen.add(name)
            c = self.library.cells[name]
            for ref in list(c.srefs) + list(c.arefs):
                stack.append(ref.cell_name)
        return seen

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

    def generate_resistor(self, x, y, body_width, body_length, rotation: float = 0.0,
                           ink_rsh=None, ref_des=None, overlap_a=None, pad_b=None,
                           space_c=None, glaze_oversize=None, resistor_layer=None,
                           conductor_layer=None, glaze_layer=None, doc_layer=None,
                           keepout_layer=None, keepout_oversize=None,
                           label_height=None, cell_name=None, n_folds: int = 1) -> str:
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
        rectangle over the resistor ink footprint *between the two
        conductor terminals* -- ``body_length`` (the terminal gap) by
        ``body_width`` (the ink width, not the wider pad width) -- grown
        by ``keepout_oversize`` (E) on all four sides -- size =
        (body_width + 2*E) x (body_length + 2*E). It marks where the
        inter-layer dielectric must be cleared so the printed ink fires
        on a bare surface (subtract this layer from the dielectric). A
        keep-out layer of -1 (the default) draws nothing.

        Any of overlap_a/pad_b/space_c/glaze_oversize/keepout_oversize/
        conductor_layer/glaze_layer/keepout_layer left as None
        falls back to this design's saved Setup > Resistor Geometry
        Rules... (Library.tech_params, via
        hlayout.io.techfile.load_resistor_geometry_rules()).

        ``n_folds`` > 1 draws the ink body as a top-hat / meander of that
        many legs instead of a single rectangle. ``body_length`` stays
        the total (electrical) ink run; the drawn footprint is
        ``body_length / n_folds`` long. This is a low-level option for
        scripted/manual use -- the Thick-Film Resistor Designer is
        rectangle-only for now and always passes ``n_folds`` = 1 (native
        top-hat sizing is not yet implemented).

        Building the device cell and placing its Sref are both part of
        a single undo step."""
        parent_cell_name = self.current_cell_name
        if parent_cell_name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        n_folds = max(1, int(n_folds))

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
            if n_folds <= 1:
                self.rect(r_layer, -(half_l + a), -half_w, half_l + a, half_w)
                self.rect(g_layer, -(half_l + a + d), -(half_w + d), half_l + a + d, half_w + d)
                # A resistor is an inherently unambiguous 2-terminal part,
                # so its pads are auto-numbered the same way
                # create_pad_cell() numbers a 2-pad footprint: lower X is
                # pin "1".
                self.rect(c_layer, -(half_l + a + b), -pad_half_w, -half_l, pad_half_w, pin="1")
                self.rect(c_layer, half_l, -pad_half_w, half_l + a + b, pad_half_w, pin="2")
                if k_layer is not None and int(k_layer) >= 0:
                    # dielectric keep-out: the resistor ink footprint
                    # between the two conductor terminals -- body_length
                    # (the terminal gap) by body_width, NOT the wider pad
                    # width -- grown by E on all four sides.
                    self.rect(int(k_layer), -(half_l + e), -(half_w + e),
                              half_l + e, half_w + e)
            else:
                # Top-hat / meander: fold the ink run into n_folds legs
                # (each along Y, stepped along X) so the same electrical
                # length lands in a compact footprint. The centreline
                # path is body_width wide; total run stays ~body_length.
                pitch = body_width * 2.0
                connectors = (n_folds - 1) * pitch
                leg_len = max(body_width, (body_length - connectors) / n_folds)
                hl = leg_len / 2.0
                x0 = -((n_folds - 1) * pitch) / 2.0
                pts = []
                for i in range(n_folds):
                    xi = x0 + i * pitch
                    pts += ([(xi, -hl), (xi, hl)] if i % 2 == 0 else [(xi, hl), (xi, -hl)])
                self.path(r_layer, [(int(round(px)), int(round(py))) for px, py in pts],
                          max(1, int(round(body_width))), cap="flush")
                x_end = x0 + (n_folds - 1) * pitch
                self.rect(g_layer, x0 - half_w - d, -hl - d, x_end + half_w + d, hl + d)
                # pads at the two free ends (pts[0] and pts[-1])
                (ax, ay), (bx, by) = pts[0], pts[-1]
                self.rect(c_layer, ax - half_w - c, ay - a - b, ax + half_w + c, ay + a, pin="1")
                if by > 0:
                    self.rect(c_layer, bx - half_w - c, by - a, bx + half_w + c, by + a + b, pin="2")
                else:
                    self.rect(c_layer, bx - half_w - c, by - a - b, bx + half_w + c, by + a, pin="2")
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

    def generate_resistor_table(self, cell_name: str, layer, *, origin=(0, 0),
                                 row_height: float = 250.0,
                                 col_widths=(1200.0, 2500.0, 1000.0, 2000.0, 2000.0),
                                 text_height: float = 150.0, line_layer=None,
                                 line_width: float = 20.0,
                                 title: Optional[str] = "RESISTOR TABLE",
                                 title_inset: Optional[float] = None,
                                 overwrite: bool = True) -> str:
        """Build a resistor BOM table -- REF DES / VALUE (OHMS) / TOL% /
        AIM VALUE / INK (OHMS/SQ) -- as a grid of Text shapes plus ruled
        row/column border lines, all in its own cell, generated fresh
        from the saved Thick-Film Resistor Designer rows
        (hlayout.io.techfile.load_resistor_list(), the same data the
        Designer's own table shows). Generic and re-runnable: this
        ALWAYS rebuilds the cell from whatever the resistor list
        currently holds (``overwrite`` defaults to True, unlike every
        other ``cell already exists`` guard in this module) -- call it
        again after adding/editing resistors to refresh the table, then
        Session.sref() the cell wherever it's needed (e.g. alongside
        the assembly drawing, for PRINT).

        "Aim value" uses the SAME formula the Resistor Designer itself
        uses (value x (aim_pct% + tolerance%) -- see
        hlayout.gui.resistor_designer.Resistor.aim_value()), reading
        aim_pct from hlayout.io.techfile.load_resistor_design_rules()
        so the printed table always matches whatever the Designer
        actually used to pick each ink.

        `col_widths` (DBU, one per column: ref des, value, tolerance,
        aim value, ink) and `row_height`/`text_height` (DBU) control
        the table's layout; the defaults are sized for text_height's
        own default. Every cell's text (header row and data rows alike)
        is horizontally CENTRED within its own column, using the same
        character-width estimate as the title (below). The ruled grid
        -- one horizontal line above every row (including the header)
        plus one below the last row, and one vertical line at every
        column boundary (including both outer edges) -- is drawn on
        `line_layer` (defaults to the same layer as the text) at
        `line_width` DBU, giving every cell (header and data) its own
        bordered box the way a real printed table reads, not just
        floating text in a grid pattern.

        `title` (default "RESISTOR TABLE"; pass None or "" to omit it)
        adds one more row ABOVE the column headers -- a single cell
        spanning the table's full width (no internal column dividers
        through it, unlike every row below), horizontally CENTRED in
        that width by default (estimating its rendered width the same
        way hlayout.core.shapes.Text.bbox() does: height * 0.6 per
        character -- there's no real font-metrics table to measure
        against, but this is the same estimate already used elsewhere
        for a Text shape's own bbox, so it's at least self-consistent).
        Pass `title_inset` (DBU) to left-align it at that inset from
        the left edge instead of centring it.

        Raises LayoutError if there are no saved resistor rows to
        tabulate at all (Setup/the Resistor Designer hasn't saved a
        resistor_list yet), or if `cell_name` exists and isn't a table
        this method itself can safely rebuild (pass overwrite=False to
        refuse touching an existing cell instead)."""
        from ..io.techfile import load_resistor_design_rules, load_resistor_list

        cell_name = (cell_name or "").strip()
        if not cell_name:
            raise LayoutError("a cell name is required")
        rows = load_resistor_list(self.library)
        if not rows:
            raise LayoutError(
                "no resistor rows saved -- generate resistors with the "
                "Thick-Film Resistor Designer first"
            )
        HEADINGS = ("REF DES", "VALUE (OHMS)", "TOL%", "AIM VALUE", "INK (OHMS/SQ)")
        if len(col_widths) != len(HEADINGS):
            raise LayoutError(f"col_widths must have {len(HEADINGS)} entries")
        if cell_name in self.library.cells:
            if not overwrite:
                raise LayoutError(
                    f"{cell_name!r} already exists in this design; pass "
                    "overwrite=True to replace it"
                )
            self.library.delete_cell(cell_name)

        r_layer = self.library.layers.resolve(layer)
        grid_layer = self.library.layers.resolve(line_layer) if line_layer is not None else r_layer
        grid_width = int(round(line_width))
        aim_pct = load_resistor_design_rules(self.library)["aim_pct"]

        def tol_fraction(tol_str):
            try:
                return float(str(tol_str).strip().rstrip("%")) / 100.0
            except ValueError:
                return 0.0

        ox, oy = origin
        col_x = [ox]
        for w in col_widths[:-1]:
            col_x.append(col_x[-1] + w)
        h = int(round(text_height))
        # Headroom above each row's text baseline so the grid line
        # above a row clears the text instead of running through it --
        # Text's own origin is its BOTTOM-left corner (see
        # hlayout.core.shapes.Text.bbox()), so the glyphs extend UPWARD
        # from the row's nominal y by text_height; the line above must
        # clear that, not just leave a fraction of row_height that can
        # be smaller than the text itself (confirmed live: it was,
        # and the grid line cut right through the text).
        if text_height * 1.2 >= row_height:
            raise LayoutError(
                f"row_height ({row_height:g}) must be bigger than "
                f"text_height*1.2 ({text_height * 1.2:g}) to leave room for the grid lines"
            )
        # Grid line above a row's baseline: half the spare row height
        # above the text, so the text sits vertically CENTRED in its row
        # (equal gap above the cap height and below the baseline).
        top_pad = (row_height + text_height) / 2.0

        with self._batch():
            cell = self.library.new_cell(cell_name)
            self.current_cell_name = cell_name

            def put_row(y, cells_text):
                for x, w, text in zip(col_x, col_widths, cells_text):
                    if text:
                        # Centred within its own column, same character-
                        # width estimate as the title -- height * 0.6 per
                        # character (hlayout.core.shapes.Text.bbox()'s own
                        # convention, self-consistent even without a real
                        # font-metrics table to measure against).
                        est_width = text_height * 0.6 * len(text)
                        tx = x + max(0.0, (w - est_width) / 2.0)
                        cell.add_text(r_layer, (tx, y), text, height=h)

            put_row(oy, HEADINGS)
            for i, row in enumerate(rows, start=1):
                y = oy - i * row_height
                value = row["value"]
                tol = row["tolerance"]
                aim = value * (aim_pct / 100.0 + tol_fraction(tol))
                ink = row.get("selected_ink")
                put_row(y, (
                    row["ref_des"],
                    format_rkm(value),
                    tol,
                    format_rkm(aim),
                    format_rkm(ink) if ink is not None else "—",
                ))

            n_data_rows = len(rows)
            row_tops = [oy + top_pad - i * row_height for i in range(n_data_rows + 2)]
            col_bounds = col_x + [col_x[-1] + col_widths[-1]]
            left, right = col_bounds[0], col_bounds[-1]
            header_top, bottom = row_tops[0], row_tops[-1]

            if title:
                title_top = header_top + row_height
                if title_inset is None:
                    total_width = col_bounds[-1] - col_bounds[0]
                    est_title_width = text_height * 0.6 * len(title)
                    title_x = ox + max(0.0, (total_width - est_title_width) / 2.0)
                else:
                    title_x = ox + title_inset
                cell.add_text(r_layer, (title_x, oy + row_height), title, height=h)
                horiz_lines = [title_top] + row_tops
                top = title_top
            else:
                horiz_lines = row_tops
                top = header_top

            for y in horiz_lines:
                cell.add_path(grid_layer, [(left, y), (right, y)], grid_width, cap="flush")
            # outer edges span the FULL height (through the title row,
            # if any); inner column dividers stop at the header -- the
            # title is one merged cell, not split by column.
            cell.add_path(grid_layer, [(left, top), (left, bottom)], grid_width, cap="flush")
            cell.add_path(grid_layer, [(right, top), (right, bottom)], grid_width, cap="flush")
            for x in col_bounds[1:-1]:
                cell.add_path(grid_layer, [(x, header_top), (x, bottom)], grid_width, cap="flush")
        return cell_name

    def generate_pin_function_table(self, cell_name: str, layer, pin_count: int, *,
                                     pins_per_group: int = 10,
                                     functions: Optional[dict] = None,
                                     origin=(0, 0), row_height: float = 250.0,
                                     col_widths=(800.0, 2000.0), group_gap: float = 0.0,
                                     text_height: float = 150.0, line_layer=None,
                                     line_width: float = 20.0,
                                     title: Optional[str] = "PIN FUNCTION TABLE",
                                     title_inset: Optional[float] = None,
                                     overwrite: bool = True) -> str:
        """Build a pinout reference table -- one PIN # / PIN FUNCTION
        column-pair per group of `pins_per_group` pins (default 10),
        with groups placed side by side left to right -- as a grid of
        Text shapes plus ruled row/column border lines, in its own
        cell. `pin_count` must be a whole multiple of `pins_per_group`
        (e.g. 20/30/40/50 pins at the default 10 per group -> 2/3/4/5
        groups); pin numbers run 1..pin_count, group 1 covering
        1..pins_per_group, group 2 covering the next pins_per_group,
        and so on.

        `functions` (optional) is a {pin_number: "function name"} dict
        -- a pin not present (or `functions` left as None, the
        default) gets a BLANK function cell, a plain fill-in-by-hand
        template. Every cell's text (headers and data alike) is
        centred within its own column, the same convention
        generate_resistor_table() uses.

        `col_widths` (DBU, one per column: pin #, pin function) is
        shared by every group; `group_gap` (DBU, default 0 -- groups
        sit edge to edge with no space between them) is the empty
        space between one group's own bordered box and the next, if
        any -- each group is still its own separate mini-table (with
        its own outer border, drawn independently of its neighbours),
        it just isn't visually separated from them at the default 0.
        `title` (default "PIN FUNCTION TABLE";
        None/"" to omit) spans the FULL width of every group plus the
        gaps between them, centred by default (pass `title_inset` to
        left-align it instead) -- see generate_resistor_table()'s own
        docstring for the same title/grid/centring conventions, shared
        here.

        Generic and re-runnable, same as generate_resistor_table():
        `overwrite` defaults to True, so calling this again (e.g. with
        an updated `functions` mapping) just rebuilds the cell fresh.
        Then Session.sref() the cell wherever it's needed, e.g.
        alongside the assembly drawing, for PRINT.

        Raises LayoutError if `pin_count` isn't a positive multiple of
        `pins_per_group`, or if `cell_name` exists and isn't a table
        this method itself can safely rebuild (pass overwrite=False to
        refuse touching an existing cell instead)."""
        cell_name = (cell_name or "").strip()
        if not cell_name:
            raise LayoutError("a cell name is required")
        if pin_count <= 0 or pins_per_group <= 0 or pin_count % pins_per_group != 0:
            raise LayoutError(
                f"pin_count ({pin_count}) must be a positive whole multiple of "
                f"pins_per_group ({pins_per_group})"
            )
        HEADINGS = ("PIN #", "PIN FUNCTION")
        if len(col_widths) != len(HEADINGS):
            raise LayoutError(f"col_widths must have {len(HEADINGS)} entries")
        if cell_name in self.library.cells:
            if not overwrite:
                raise LayoutError(
                    f"{cell_name!r} already exists in this design; pass "
                    "overwrite=True to replace it"
                )
            self.library.delete_cell(cell_name)

        r_layer = self.library.layers.resolve(layer)
        grid_layer = self.library.layers.resolve(line_layer) if line_layer is not None else r_layer
        grid_width = int(round(line_width))
        functions = functions or {}
        h = int(round(text_height))
        if text_height * 1.2 >= row_height:
            raise LayoutError(
                f"row_height ({row_height:g}) must be bigger than "
                f"text_height*1.2 ({text_height * 1.2:g}) to leave room for the grid lines"
            )
        # Grid line above a row's baseline: half the spare row height
        # above the text, so the text sits vertically CENTRED in its row
        # (equal gap above the cap height and below the baseline).
        top_pad = (row_height + text_height) / 2.0

        n_groups = pin_count // pins_per_group
        group_width = sum(col_widths)
        ox, oy = origin

        with self._batch():
            cell = self.library.new_cell(cell_name)
            self.current_cell_name = cell_name

            def put_row(group_x, y, cells_text):
                x = group_x
                for w, text in zip(col_widths, cells_text):
                    if text:
                        est_width = text_height * 0.6 * len(text)
                        tx = x + max(0.0, (w - est_width) / 2.0)
                        cell.add_text(r_layer, (tx, y), text, height=h)
                    x += w

            row_tops = [oy + top_pad - i * row_height for i in range(pins_per_group + 2)]
            header_top, bottom = row_tops[0], row_tops[-1]

            for g in range(n_groups):
                group_x = ox + g * (group_width + group_gap)
                put_row(group_x, oy, HEADINGS)
                for i in range(pins_per_group):
                    pin_num = g * pins_per_group + i + 1
                    y = oy - (i + 1) * row_height
                    put_row(group_x, y, (str(pin_num), functions.get(pin_num, "")))

                group_left, group_right = group_x, group_x + group_width
                for yy in row_tops:
                    cell.add_path(grid_layer, [(group_left, yy), (group_right, yy)],
                                  grid_width, cap="flush")
                cell.add_path(grid_layer, [(group_left, header_top), (group_left, bottom)],
                              grid_width, cap="flush")
                cell.add_path(grid_layer, [(group_right, header_top), (group_right, bottom)],
                              grid_width, cap="flush")
                mid_x = group_x + col_widths[0]
                cell.add_path(grid_layer, [(mid_x, header_top), (mid_x, bottom)],
                              grid_width, cap="flush")

            if title:
                total_width = n_groups * group_width + (n_groups - 1) * group_gap
                title_top = header_top + row_height
                if title_inset is None:
                    est_title_width = text_height * 0.6 * len(title)
                    title_x = ox + max(0.0, (total_width - est_title_width) / 2.0)
                else:
                    title_x = ox + title_inset
                cell.add_text(r_layer, (title_x, oy + row_height), title, height=h)
                left, right = ox, ox + total_width
                cell.add_path(grid_layer, [(left, title_top), (right, title_top)],
                              grid_width, cap="flush")
                cell.add_path(grid_layer, [(left, title_top), (left, header_top)],
                              grid_width, cap="flush")
                cell.add_path(grid_layer, [(right, title_top), (right, header_top)],
                              grid_width, cap="flush")
        return cell_name

    def generate_drawing_sheet(self, cell_name: str, layer, *, size: str = "A3",
                                orientation: str = "portrait",
                                origin=(0, 0), margin_mm: float = 10.0,
                                title_block_height_mm: float = 45.0,
                                text_height_mm: float = 3.0,
                                line_width_mm: float = 0.35, line_layer=None,
                                title: str = "", dwg_no: str = "", issue: str = "",
                                drawn_by: str = "", drawn_date: str = "",
                                checked_by: str = "", checked_date: str = "",
                                approved_by: str = "", approved_date: str = "",
                                overwrite: bool = True) -> str:
        """Build a standard drawing sheet -- a ruled border frame sized
        to a real ISO paper size (`size`: "A4" or "A3", default "A3" --
        larger ISO sizes don't fit inside hlayout's 500 x 500 mm
        database cap, hlayout.core.units.MAX_SIZE_MM), `orientation`
        "portrait" (default) or "landscape", with a
        standard title block ruled across the FULL width of the
        bottom of that frame -- as a grid of Text shapes plus border/
        grid Path lines, in its own cell, the same generic
        grid-drawing convention generate_resistor_table() and
        generate_pin_function_table() use. Unlike those, every
        distance here is in real millimetres (`margin_mm`,
        `title_block_height_mm`, `text_height_mm`, `line_width_mm`),
        matching how a physical drawing sheet is actually specified,
        and is converted to DBU internally
        (hlayout.core.units.mm_to_dbu()).

        The title block has 5 rows, top to bottom:
          1. TITLE (full width)
          2. DWG NO | ISSUE
          3. DRAWN BY | DATE
          4. CHECKED BY | DATE
          5. APPROVED BY | DATE
        Every field is blank by default -- a plain fill-in-by-hand
        template, the same convention generate_pin_function_table()'s
        FUNCTION column uses -- pass any of `title`/`dwg_no`/`issue`/
        `drawn_by`/`drawn_date`/`checked_by`/`checked_date`/
        `approved_by`/`approved_date` to pre-fill it. Each cell reads
        "LABEL:" when blank or "LABEL: value" when filled, left-
        aligned (unlike RESTABLE/PINTABLE's centred cells -- a title
        block conventionally reads label-first, left to right).

        The rest of the sheet (above the title block, inside the
        margin) is left empty -- this cell is just the border and
        title block; place the actual design content, and/or
        Session.sref() this cell in, wherever it's needed for PRINT.

        Generic and re-runnable, same as generate_resistor_table():
        `overwrite` defaults to True, so calling this again (e.g.
        with updated title-block fields) just rebuilds the cell
        fresh.

        Raises LayoutError if `size`/`orientation` aren't recognised,
        if `margin_mm`/`title_block_height_mm` don't leave room for an
        actual drawing area and title-block text, or if `cell_name`
        exists and isn't a sheet this method itself can safely rebuild
        (pass overwrite=False to refuse touching an existing cell
        instead)."""
        cell_name = (cell_name or "").strip()
        if not cell_name:
            raise LayoutError("a cell name is required")
        size = (size or "").upper()
        if size not in SHEET_SIZES_MM:
            raise LayoutError(
                f"unknown size {size!r}; must be one of {sorted(SHEET_SIZES_MM)}"
            )
        orientation = (orientation or "").lower()
        if orientation not in ("portrait", "landscape"):
            raise LayoutError('orientation must be "portrait" or "landscape"')
        short_mm, long_mm = SHEET_SIZES_MM[size]
        width_mm, height_mm = (short_mm, long_mm) if orientation == "portrait" else (long_mm, short_mm)
        if cell_name in self.library.cells:
            if not overwrite:
                raise LayoutError(
                    f"{cell_name!r} already exists in this design; pass "
                    "overwrite=True to replace it"
                )
            self.library.delete_cell(cell_name)

        sheet_w, sheet_h = mm_to_dbu(width_mm), mm_to_dbu(height_mm)
        margin = mm_to_dbu(margin_mm)
        if 2 * margin >= min(sheet_w, sheet_h):
            raise LayoutError(
                f"margin_mm ({margin_mm:g}) is too large for a {size} "
                f"{orientation} sheet ({width_mm:g} x {height_mm:g} mm)"
            )
        title_block_h = mm_to_dbu(title_block_height_mm)
        text_h = mm_to_dbu(text_height_mm)
        row_h = title_block_h / 5.0
        if text_h * 1.2 >= row_h:
            raise LayoutError(
                f"title_block_height_mm ({title_block_height_mm:g}) must be "
                f"bigger than text_height_mm*1.2*5 ({text_height_mm * 1.2 * 5:g}) "
                "to leave room for 5 rows of text"
            )

        r_layer = self.library.layers.resolve(layer)
        grid_layer = self.library.layers.resolve(line_layer) if line_layer is not None else r_layer
        grid_width = mm_to_dbu(line_width_mm)
        pad_x = text_h * 0.5

        ox, oy = origin
        left, right = ox + margin, ox + sheet_w - margin
        frame_top, frame_bottom = oy + sheet_h - margin, oy + margin
        frame_width = right - left
        if title_block_h >= frame_top - frame_bottom:
            raise LayoutError(
                f"title_block_height_mm ({title_block_height_mm:g}) leaves no "
                f"room for a drawing area above it on a {size} {orientation} sheet"
            )
        title_block_top = frame_bottom + title_block_h
        row_tops = [title_block_top - i * row_h for i in range(6)]

        name_w = frame_width * 0.7
        date_w = frame_width - name_w
        divider_x = left + name_w

        ROWS = (
            (("TITLE", title),),
            (("DWG NO", dwg_no), ("ISSUE", issue)),
            (("DRAWN BY", drawn_by), ("DATE", drawn_date)),
            (("CHECKED BY", checked_by), ("DATE", checked_date)),
            (("APPROVED BY", approved_by), ("DATE", approved_date)),
        )

        with self._batch():
            cell = self.library.new_cell(cell_name)
            self.current_cell_name = cell_name

            for i, fields in enumerate(ROWS):
                row_top, row_bottom = row_tops[i], row_tops[i + 1]
                ty = row_bottom + (row_h - text_h) / 2.0
                cols = [(left, frame_width)] if len(fields) == 1 else [(left, name_w), (divider_x, date_w)]
                for (x, w), (label, value) in zip(cols, fields):
                    text = f"{label}:" + (f" {value}" if value else "")
                    cell.add_text(r_layer, (x + pad_x, ty), text, height=text_h)

            # outer border: the full frame, inset by margin from the sheet edge
            cell.add_path(grid_layer, [(left, frame_top), (left, frame_bottom)],
                          grid_width, cap="flush")
            cell.add_path(grid_layer, [(right, frame_top), (right, frame_bottom)],
                          grid_width, cap="flush")
            cell.add_path(grid_layer, [(left, frame_top), (right, frame_top)],
                          grid_width, cap="flush")
            for y in row_tops:
                cell.add_path(grid_layer, [(left, y), (right, y)], grid_width, cap="flush")
            # DWG NO/ISSUE and the three signature rows below it all share
            # the same name/date column divider, drawn as one continuous line.
            cell.add_path(grid_layer, [(divider_x, row_tops[1]), (divider_x, row_tops[5])],
                          grid_width, cap="flush")
        return cell_name

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

    def _top_view_refdes_texts(self, cell_name, layer, height, datatype=0):
        """One Text per labelled placement anywhere under `cell_name`,
        centred on the placed part's CENTROID (the centre of its placed
        bounding box), `height` DBU tall -- an assembly drawing labels
        each part in the middle of its body. Where the canvas floats the
        ref-des (dragged label_offset, ref-des moved to pin 1, a baked
        ref-des Text off to one side) is deliberately ignored here."""
        from ..core.library import _transform_bbox
        from ..core.maskops import _compose_transforms, _sref_point_transform
        from ..core.shapes import Text
        lib = self.library
        h = max(1, int(round(height)))
        out = []

        def centred(text, x, y):
            w = 0.6 * h * max(len(text), 1)
            return Text(layer, (x - w / 2.0, y - h / 2.0), text, height=h, datatype=datatype)

        body_cache = {}

        def body_bbox(name, depth=0):
            """Bounding box of a cell's real geometry, ignoring Text --
            so a baked ref-des or a note doesn't drag the centroid."""
            if name in body_cache:
                return body_cache[name]
            cell = lib.cells.get(name)
            boxes = []
            if cell is not None and depth <= 64:
                boxes = [sh.bbox() for sh in cell.shapes if not isinstance(sh, Text)]
                for r in cell.srefs:
                    b = body_bbox(r.cell_name, depth + 1)
                    if b is not None:
                        boxes.append(_transform_bbox(b, r))
                for ar in getattr(cell, "arefs", []):
                    try:
                        boxes.append(lib.cell_bbox(ar.cell_name) and
                                     _transform_bbox(lib.cell_bbox(ar.cell_name), ar))
                    except Exception:
                        pass
            boxes = [b for b in boxes if b]
            box = None
            if boxes:
                box = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                       max(b[2] for b in boxes), max(b[3] for b in boxes))
            elif cell is not None:
                try:
                    box = lib.cell_bbox(name)      # text-only cell: fall back
                except Exception:
                    box = None
            body_cache[name] = box
            return box

        def walk(name, xf, depth):
            if depth > 64 or name not in lib.cells:
                return
            for ref in lib.cells[name].srefs:
                child_xf = _compose_transforms(xf, _sref_point_transform(ref))
                if ref.label:
                    box = body_bbox(ref.cell_name)
                    if box is None:
                        x, y = child_xf((0.0, 0.0))
                    else:
                        x1, y1, x2, y2 = _transform_bbox(box, ref)
                        x, y = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                        if xf is not None:
                            x, y = xf((x, y))
                    out.append(centred(ref.label, x, y))
                walk(ref.cell_name, child_xf, depth + 1)

        walk(cell_name, None, 0)
        return out

    def top_view_from_settings(self, **overrides) -> dict:
        """Run the Top View with the design's saved settings (Tools > Top
        View..., techfile.load_topview_settings()) -- of the main design
        cell, into the saved output cell -- with any keyword overrides.
        Returns mask_op_top_view()'s {layer: paths written}."""
        from ..io.techfile import load_topview_settings
        st = load_topview_settings(self.library)
        st.update(overrides)
        if int(st.get("result_layer", -1)) < 0:
            raise LayoutError("no Top View result layer set -- pick one in Tools > Top View...")
        kwargs = {"out_cell": st["out_cell"], "flatten": bool(st["flatten"]),
                  "line_width": float(st["line_width_um"])}
        excluded = list(st.get("excluded") or [])
        if int(st["result_layer"]) not in excluded:
            excluded.append(int(st["result_layer"]))    # never read the output back in
        kwargs["exclude_layers"] = excluded
        if st.get("hatch") and float(st.get("hatch_pitch_um", 0)) > 0:
            kwargs["hatch_conductor_pitch"] = float(st["hatch_pitch_um"])
            kwargs["hatch_conductor_angles"] = tuple(st.get("hatch_angles") or (45.0, -45.0))
        if float(st.get("simplify_um", 0)) > 0:
            kwargs["simplify"] = float(st["simplify_um"])
        kwargs["refdes"] = bool(st.get("refdes", True))
        kwargs["refdes_height"] = float(st.get("refdes_height_um", 600.0))
        main = self.library.top_cell_name
        if main and main in self.library.cells and main != st["out_cell"]:
            kwargs["cell_names"] = [main]
        return self.mask_op_top_view(int(st["result_layer"]), **kwargs)

    def generate_assembly_drawing(self, save: bool = True, **settings) -> dict:
        """Build (or rebuild) the complete assembly drawing -- drawing sheet,
        a top view of the design at the largest standard scale that fits,
        a pin function table and a resistor table -- each as its own cell,
        all placed as Srefs into one assembly cell (default "ASSEMBLY").
        Sheet, text and line sizes are in real paper millimetres.

        Any key of hlayout.scripting.assembly.DEFAULT_ASSEMBLY_SETTINGS may
        be passed (title=, dwg_no=, size=, orientation=, scale=, layout=,
        pin_count=, pin_functions=, ...); anything not passed comes from the
        design's saved assembly settings, and with ``save`` (default True)
        the passed keys are saved back -- so a bare
        ``generate_assembly_drawing()`` rebuilds the last drawing, e.g.
        after editing resistors or moving parts. One undo step.

        Returns {"cell", "scale", "layout", "parts", "warnings"}."""
        from .assembly import generate_assembly_drawing
        return generate_assembly_drawing(self, save=save, **settings)

    def top_view_layer_roles(self) -> dict:
        """{layer: role} for the role-based Top View stacking (and the
        Top View window's layer table): "resistor", "conductor", "glaze",
        "bond", "substrate" or "other". Roles come from the Setup rules
        (resistor geometry, route layers, wire bonds, dielectric fill);
        "substrate"/"other" are layers with drawn, visible, filled
        geometry not otherwise claimed. Components (placed parts) are not
        a layer role -- they are always composited on top."""
        from ..io.techfile import load_bond_rules, load_dielectric_rules, \
            load_resistor_geometry_rules, load_route_rules
        resistor_layers, conductor_layers, glaze_layers, bond_layers = set(), set(), set(), set()
        try:
            rg = load_resistor_geometry_rules(self.library)
            if isinstance(rg.get("resistor_layer"), int) and rg["resistor_layer"] >= 0:
                resistor_layers.add(rg["resistor_layer"])
            for n in (rg.get("ink_layers") or {}).values():
                if isinstance(n, int) and n >= 0:
                    resistor_layers.add(n)
            if isinstance(rg.get("conductor_layer"), int) and rg["conductor_layer"] >= 0:
                conductor_layers.add(rg["conductor_layer"])
            if isinstance(rg.get("glaze_layer"), int) and rg["glaze_layer"] >= 0:
                glaze_layers.add(rg["glaze_layer"])
        except Exception:
            pass
        try:
            rr = load_route_rules(self.library)
            for n in (rr.get("layer_a"), rr.get("layer_b")):
                if isinstance(n, int) and n >= 0:
                    conductor_layers.add(n)
        except Exception:
            pass
        try:
            b = int(load_bond_rules(self.library)["layer"])
            if b >= 0:
                bond_layers.add(b)
        except Exception:
            pass
        try:
            d = int(load_dielectric_rules(self.library)["layer"])
            if d >= 0:
                conductor_layers.add(d)
        except Exception:
            pass

        # Every OTHER layer that actually has drawn, visible, filled
        # geometry anywhere in the library -- no thickness lookup,
        # just "is there really something here worth showing" (same
        # idea hlayout.io.techfile.default_layer_thickness() uses for
        # its own generic-film catch-all, minus the thickness
        # requirement -- see this method's own docstring for why).
        drawn = set()
        for c in self.library.cells.values():
            for sh in c.shapes:
                drawn.add(sh.layer)
        role_layers = resistor_layers | conductor_layers | glaze_layers | bond_layers
        generic_layers = {
            n for n in drawn
            if 0 <= n < len(self.library.layers)
            and self.library.layers[n].visible and self.library.layers[n].fill
        }
        # The substrate is the substrate whether or not its layer happens
        # to be shown/filled on screen (a design often hides it to see
        # the films): any DRAWN layer named substrate/alumina/ceramic,
        # or the Thermal setup's substrate layer.
        substrate_layers = {
            n for n in drawn
            if 0 <= n < len(self.library.layers)
            and any(w in (self.library.layers[n].name or "").lower()
                    for w in ("substrate", "alumina", "ceramic"))
        } - role_layers
        try:
            from ..io.techfile import load_thermal_rules
            t = int(load_thermal_rules(self.library)["boundary"].get("substrate_layer", -1))
            if t >= 0 and t in drawn:
                substrate_layers.add(t)
        except Exception:
            pass
        other_layers = generic_layers - role_layers - substrate_layers

        roles = {}
        for role, group in (("other", other_layers), ("substrate", substrate_layers),
                            ("bond", bond_layers), ("glaze", glaze_layers),
                            ("conductor", conductor_layers), ("resistor", resistor_layers)):
            for n in group:
                roles[n] = role
        return roles

    def mask_op_top_view(self, result_layer, layers: Optional[List] = None,
                          exclude_layers: Optional[List] = None,
                          hatch_conductor_pitch: Optional[float] = None,
                          hatch_conductor_angles=(45.0, -45.0),
                          line_width: float = 100.0,
                          cell_names: Optional[List[str]] = None,
                          datatype: int = 0, flatten: bool = True,
                          simplify: Optional[float] = None,
                          out_cell: str = "TOPVIEW",
                          refdes: bool = False,
                          refdes_height: float = 600.0) -> dict:
        """`refdes=True` also writes every placed part's reference
        designator as a Text on the result layer, AFTER all the outlines
        (the last thing in the view, never hidden), `refdes_height` DBU
        tall, centred where the canvas shows it (or at the baked ref-des
        Text inside the part, when it has one).

        Draw a top-down, hidden-line-removal pictorial view of the
        hybrid circuit onto a single output layer -- the classic CAD/
        drafting sense of the term (as in AutoCAD's HIDE): the visible
        OUTLINE edges of the design as seen from directly above, with
        the part of a lower entry's edge that's buried under a higher
        entry's filled footprint actually removed rather than drawn
        underneath it.

        This draws outlines, not filled silhouettes -- compositing
        FILLED regions and merging what's visible onto one
        undifferentiated output layer is a no-op (their union is always
        exactly the union of the inputs regardless of stacking order;
        occlusion only changes which entry contributed a given point,
        information a single output layer throws away immediately).
        Working with each entry's boundary line instead preserves the
        one thing that matters: an edge fully enclosed by something
        above it is genuinely absent from the result.

        With the default `layers=None`, stacking follows the real fab
        process order by ROLE, regardless of what layer numbers your
        technology setup happens to use for each: placed components
        and their epoxy/keep-out footprints sit topmost (cutting into
        everything under them) -- a placed cell counts as "a
        component" here by NAME (a known EIA size code) or FOOTPRINT
        (a known gull-wing pin pattern) or an explicit component_models
        entry, the same recognition Session.match_freecad_components()
        uses, with no reference to which layer it draws on at all (see
        _matched_component_footprint()) -- then the resistor ink
        layer(s), then
        every conductor layer (Setup's own conductor_layer plus the
        Route Layers), then any OTHER visible, filled layer not
        otherwise classified, then the substrate. An unclassified
        "other" layer is its own tier, strictly below conductor -- it
        never occludes conductor (or resistor, or a component) just
        because it happens to have a higher layer number, the exact
        number-ordering problem this role-based stacking exists to
        avoid in the first place; two layers within the SAME tier (e.g.
        two unclassified "other" layers) still settle ties between
        themselves by plain layer number, since nothing further
        distinguishes them. The glaze layer AND the Wire Bond layer are
        both drawn as non-occluding overlays -- always shown in full,
        never hidden by or hiding anything else: glaze is a thin,
        largely transparent overprint rather than an opaque film in
        this pictorial convention, and a wire bond terminates ON a
        component's own pad at the surface rather than running buried
        underneath it, so it must never be clipped short by the very
        component it's bonded to. Layer roles come from the same Setup
        accessors export_freecad()'s heuristic uses (hlayout.io.techfile's
        resistor/route/bond/dielectric rules).

        `exclude_layers` (numbers or names) drops specific layers out of
        the role-based default without having to rebuild the whole
        layer set by hand -- e.g. a documentation or debug layer that
        happens to have real geometry and would otherwise show up in
        the "other" tier. It also reaches into component recognition
        itself: a "bare" part recognized only by its own non-role
        graphics (see _matched_component_footprint()) is walked into
        instead of treated as an occluding component when ALL of its
        own shapes sit on an excluded layer -- the escape hatch for a
        sealed package's own outer case/lid, whose outline can
        legitimately span the entire board and would otherwise occlude
        every real component and all conductor/resistor content inside
        it (confirmed live). Ignored when `layers` is given explicitly
        (there, just leave the layer out of the list instead).

        `hatch_conductor_pitch` (DBU, default None = no hatching), if
        given, fills every conductor-tier layer's own VISIBLE region
        (after occlusion, same region its notched outline comes from)
        with a cross-hatch pattern at that pitch -- the standard
        technical-drawing convention for a solid material in a
        pictorial view (hlayout.core.maskops.cross_hatch_lines()).
        `hatch_conductor_angles` (degrees from the X axis, default
        (45, -45)) sets which angle(s) to hatch at -- pass a different
        pair (e.g. (60, -60)) or a single angle for one-directional
        hatching instead of a cross. Both are ignored when `layers` is
        given explicitly, and have no effect on any other tier
        (resistor, substrate, components, glaze, wire bonds).

        Pass an explicit `layers` list (numbers or names) to bypass all
        of that and get the older, simpler behaviour instead: every
        given layer treated as an equally-occluding film, ordered by
        plain layer number (highest = top) -- e.g. to restrict the view
        to a subset, or include a normally non-physical layer such as a
        documentation outline.

        `line_width` (DBU, default 100 = 0.1mm, matching this tool's
        own body-outline-path convention) is the drawn width of every
        outline segment.

        Writes the result as new Path shapes on `result_layer` of a
        SEPARATE cell named `out_cell` (default "TOPVIEW") -- never
        into the cell being viewed, so the drawing can't pollute the
        real design (or get picked up as geometry by DRC, netlist,
        Gerber or the next top view). The paths keep the viewed cell's
        own coordinates, so placing an instance of the output cell at
        0,0 overlays it exactly. The output cell is REBUILT on every
        run (created if missing, emptied first if it exists), so
        re-running replaces the previous view rather than piling up on
        it. With more than one cell in `cell_names`, each gets its own
        output cell named "<out_cell>_<cell name>". Viewing the output
        cell itself is an error.

        `cell_names`, `datatype`, and `flatten` match mask_op() exactly
        (see that method for what each means); `simplify` applies
        Douglas-Peucker vertex reduction to the line geometry at that
        DBU tolerance before it's drawn, same idea as mask_op()'s own
        `simplify` but operating on lines instead of polygon area.

        Returns {cell_name: paths_written}, keyed by the VIEWED cell's
        name, for cells that actually produced output (empty results
        are omitted). Requires shapely.

        Note this has nothing to do with fired-film THICKNESS (unlike
        export_freecad()'s 3D stack, which needs a Z-height to extrude
        each layer by) -- a 2D top-down occlusion view only needs to
        know a layer's stacking ROLE and whether it has geometry, so a
        layer with no thickness saved in Setup is still fully
        considered here."""
        from ..io.techfile import load_bond_rules, load_dielectric_rules, \
            load_resistor_geometry_rules, load_route_rules

        resolve = self.library.layers.resolve
        r_layer = resolve(result_layer)
        width_dbu = int(round(line_width))

        role_based = layers is None
        if not role_based:
            layer_set = sorted({resolve(n) for n in layers})
            if not layer_set:
                raise LayoutError("no layers to composite -- pass a non-empty layers=")
        else:
            roles = self.top_view_layer_roles()
            resistor_layers = {n for n, r in roles.items() if r == "resistor"}
            conductor_layers = {n for n, r in roles.items() if r == "conductor"}
            glaze_layers = {n for n, r in roles.items() if r == "glaze"}
            bond_layers = {n for n, r in roles.items() if r == "bond"}
            substrate_layers = {n for n, r in roles.items() if r == "substrate"}
            other_layers = {n for n, r in roles.items() if r == "other"}

            excluded = {resolve(n) for n in exclude_layers} if exclude_layers else set()
            if excluded:
                resistor_layers -= excluded
                conductor_layers -= excluded
                glaze_layers -= excluded
                bond_layers -= excluded
                substrate_layers -= excluded
                other_layers -= excluded

            all_considered_layers = resistor_layers | conductor_layers | glaze_layers \
                | bond_layers | substrate_layers | other_layers

            if not all_considered_layers:
                raise LayoutError(
                    "no layers to composite -- pass layers=, or draw something "
                    "on a visible, filled layer, or set resistor/conductor/"
                    "glaze/route/bond layers in Setup"
                )

        cells = ([self.library.get_cell(n) for n in cell_names] if cell_names
                 else [self.cell])
        out_cell = (out_cell or "").strip()
        if not out_cell:
            raise LayoutError("top view needs an output cell name")
        out_names = {
            cell.name: (out_cell if len(cells) == 1 else f"{out_cell}_{cell.name}")
            for cell in cells
        }
        for cell in cells:
            if cell.name in out_names.values():
                raise LayoutError(
                    f"cannot draw a top view of {cell.name!r}: it is the top "
                    f"view's own output cell -- switch to the design cell first"
                )

        self._checkpoint()
        # Empty every existing output cell BEFORE gathering anything, so
        # a previous view (e.g. one placed back into the design as an
        # instance) is never read back in as input geometry.
        for out_name in out_names.values():
            old = self.library.cells.get(out_name)
            if old is not None:
                del old.shapes[:]
                del old.srefs[:]
                del old.arefs[:]
        written = {}
        for cell in cells:
            src = {"cell": cell}

            def gather(layer):
                c = src["cell"]
                if flatten:
                    return layer_to_shapely_flattened(self.library, c, layer)
                return layer_to_shapely(c.shapes_on_layer(layer))

            if role_based:
                comp_geom, pkg_geom, pkg_names = _matched_component_footprint(
                    self.library, cell.name, load_component_models(self.library),
                    resistor_layers | conductor_layers | glaze_layers | bond_layers, excluded,
                    split_packages=True, pad_layers=conductor_layers | bond_layers)
                if pkg_names:
                    # the layer tiers must not pick up an enclosing
                    # package's own pads/outline again (they'd sit above
                    # the substrate and hide its pins) -- gather from a
                    # view of the cell without those placements
                    from ..core.cell import Cell as _Cell
                    view = _Cell(cell.name)
                    view.shapes = cell.shapes
                    view.srefs = [r for r in cell.srefs if r.cell_name not in pkg_names]
                    view.arefs = [a for a in cell.arefs if a.cell_name not in pkg_names]
                    src["cell"] = view
                layer_geoms = [(comp_geom, True)]
                for n in sorted(resistor_layers, reverse=True):
                    layer_geoms.append((gather(n), True))
                # the dielectric counts as a conductor-tier film for
                # stacking, but it is NOT hatched -- hatching it would
                # cross-hatch the whole board (and, chopped up by every
                # via window and track, run to hundreds of thousands of
                # paths -- confirmed live, it froze the GUI)
                try:
                    from ..io.techfile import load_dielectric_rules
                    _diel = int(load_dielectric_rules(self.library)["layer"])
                except Exception:
                    _diel = -1
                for n in sorted(conductor_layers, reverse=True):
                    layer_geoms.append(
                        (gather(n), True,
                         (hatch_conductor_pitch, hatch_conductor_angles)
                         if hatch_conductor_pitch and n != _diel else None))
                for n in sorted(other_layers, reverse=True):
                    layer_geoms.append((gather(n), True))
                for n in sorted(substrate_layers, reverse=True):
                    layer_geoms.append((gather(n), True))
                # an enclosing package (header can) is UNDER the substrate:
                # only the part of it outside the substrate shows -- its
                # pins, well and case edge each as their own tier
                for g in pkg_geom:
                    layer_geoms.append((g, True))
                for n in sorted(glaze_layers):
                    layer_geoms.append((gather(n), False))
                for n in sorted(bond_layers):
                    # A wire bond terminates ON a component's own pad,
                    # at the surface -- it doesn't run buried underneath
                    # one the way a real conductor trace does, so it
                    # must never be clipped short by the very component
                    # it's bonded to (confirmed live: occluding a bond
                    # wire the same as conductor left it visibly
                    # stopping short of the pad it actually reaches).
                    # Non-occluding, same as glaze.
                    layer_geoms.append((gather(n), False))
            else:
                layer_geoms = [(gather(n), True) for n in sorted(layer_set, reverse=True)]

            result = hidden_line_top_view(layer_geoms)
            if simplify:
                result = result.simplify(simplify, preserve_topology=True)
            new_paths = shapely_lines_to_paths(result, r_layer, width_dbu, datatype=datatype)
            if refdes:
                # ref-des LAST: drawn over the finished outlines, never hidden
                new_paths = list(new_paths) + self._top_view_refdes_texts(
                    cell.name, r_layer, refdes_height, datatype)
            if new_paths:
                out_name = out_names[cell.name]
                target = self.library.cells.get(out_name)
                if target is None:
                    target = self.library.new_cell(out_name)
                target.shapes.extend(new_paths)
                written[cell.name] = len(new_paths)
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
          With no such shape the boundary is worked out instead: the
          area inside the module's I/O pad ring (the pads' inner edges,
          overlapped by the Freerouting io_entry_depth so the tracks'
          entry strip stays insulated), else the design's bounding box
          grown by ``margin``. Either way the fill is clipped to the
          substrate outline (Setup > Route Layers boundary layer), so it
          never runs off the substrate or over the I/O pads.
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
            if not rest:
                boundary, holes = biggest, rest
            else:
                # Don't require *exact* full containment (a bare 1 DBU
                # tolerance) -- a hole/keep-out that pokes even slightly
                # past the intended boundary (a via near the board edge,
                # a keep-out sized with its own margin, ordinary DBU
                # rounding) would otherwise silently fail this check
                # entirely and fall back to a bbox+margin fill that
                # treats the boundary shape itself as just another hole
                # -- producing a near-empty/sliver result instead of an
                # error, which is worse than either succeeding or
                # failing loudly. Instead: treat `biggest` as the
                # boundary whenever it accounts for almost all of the
                # other shapes' combined area (>= 95% overlap) -- clearly
                # different from an unrelated large shape that doesn't
                # actually enclose anything (0% overlap, e.g. the
                # bbox+margin-fallback test's keep-out and via window,
                # which don't overlap each other at all) -- and CLIP
                # each hole to the boundary so any part outside it is
                # simply ignored rather than invalidating detection.
                rest_union = unary_union(rest)
                rest_area = rest_union.area
                overlap_area = biggest.intersection(rest_union).area
                if rest_area == 0 or overlap_area / rest_area >= 0.95:
                    boundary = biggest
                    holes = [g.intersection(biggest) for g in rest]
                    holes = [g for g in holes if not g.is_empty]

        if boundary is not None:
            fill = boundary
        else:
            fill = self._io_ring_box(cell)
            if fill is None:
                bbox = self.library.cell_bbox(name)
                if bbox is None:
                    raise LayoutError(f"cell {name!r} is empty -- nothing to cover")
                x0, y0, x1, y1 = bbox
                fill = _box(x0 - mrg, y0 - mrg, x1 + mrg, y1 + mrg)
        sub = self._substrate_box(cell)
        if sub is not None:
            fill = fill.intersection(sub)

        opening_geoms = list(holes)
        for src in keepout_nums:
            if src is None or src < 0 or src == r_layer:
                continue
            g = layer_to_shapely_flattened(self.library, cell, src)
            if not g.is_empty:
                opening_geoms.append(g)

        # a via window is drawn at its printed size (via_spec()); only
        # keep-outs get the clearance
        from ..io.specctra import find_vias as _fv, via_cell_sites as _vcs
        try:
            _la, _lb = self._route_layers()
            vsites = [(x, y) for x, y, _w in _fv(cell, (_la, _lb))] + \
                [(x, y) for x, y, _w, _r in _vcs(self.library, cell, (_la, _lb))]
        except Exception:
            vsites = []
        vtree = None
        if vsites:
            from shapely.geometry import Point as _Pt
            from shapely.strtree import STRtree as _STR
            vpts = [_Pt(x, y) for x, y in vsites]
            vtree = _STR(vpts)

        def _is_via_window(g):
            if vtree is None or g.geom_type != "Polygon":
                return False
            x0, y0, x1, y1 = g.bounds
            w, h = x1 - x0, y1 - y0
            if w <= 0 or w > 5000 or abs(w - h) > max(2, 0.02 * w):
                return False
            c = g.centroid
            return any(vpts[int(i)].distance(c) <= 2 for i in vtree.query(c.buffer(3)))

        windows = [g for g in holes if _is_via_window(g)]
        others = [g for g in opening_geoms if not any(g is w for w in windows)]
        result = fill
        if opening_geoms:
            merged = unary_union(others) if others else None
            if merged is not None and clr:
                merged = size_geometry(merged, clr)
            if windows:
                merged = unary_union(([merged] if merged is not None else []) + windows)
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

    def add_bond_drc_rules(self, angle: float = 45.0) -> list:
        """Save the wire-bond DRC rules made from Setup > Wire Bond
        Rules... into the technology file: BOND_MIN_LENGTH and
        BOND_MAX_LENGTH on the bond layer, and BOND_MAX_ANGLE (``angle``
        degrees) for each die-pad layer. Rules already saved are not
        added twice. Returns the DrcRule list added (Tools > DRC... >
        Add Bond Rules; DRCRULE BONDS)."""
        from ..core.drc import DrcRule, bond_rules_from_settings
        from ..io.techfile import load_bond_rules, save_drc_rules
        saved = load_drc_rules(self.library)
        new = bond_rules_from_settings(load_bond_rules(self.library),
                                       [DrcRule.from_dict(r) for r in saved], angle=angle)
        save_drc_rules(self.library, saved + [r.to_dict() for r in new])
        return new

    def bond_departures(self, cell_name: Optional[str] = None, pad_layer=None) -> list:
        """Every wire-bond end that lands on a bare-die pad, with the angle
        it leaves at: [{"a", "b", "end", "die", "angle_deg"}], ``a``/``b``
        the wire's ends and ``end`` (0 or 1) the one on the die.
        ``pad_layer`` defaults to each Setup > Wire Bond Rules die-pad
        layer. The angle is measured from the outward normal of the die
        edge nearest the pad (see BOND_MAX_ANGLE in hlayout.core.drc)."""
        from ..core.drc import bond_departures
        from ..io.techfile import load_bond_rules
        br = load_bond_rules(self.library)
        cell = self.library.get_cell(cell_name or self.current_cell_name)
        layers = ([self.library.layers.resolve(pad_layer)] if pad_layer is not None
                  else list(br.get("die_pad_layers") or []))
        out = []
        for pl in layers:
            for (a, b), k, die, ang in bond_departures(self.library, cell, int(br["layer"]), pl):
                out.append({"a": a, "b": b, "end": k, "die": die, "angle_deg": round(ang, 1)})
        return out

    def wire_bond(self, points, layer=None, width=None, pad=None,
                  min_length=None, max_length=None,
                  cell_name: Optional[str] = None):
        """Draw a chip-and-wire assembly WIRE BOND between two points --
        the jumper that ties a bare-die pad to a thick-film
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
        Setup > Technology Properties (Wire Bonds tab) (load_bond_rules). Raises LayoutError
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
                "Setup > Technology Properties (Wire Bonds tab)"
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
                f"minimum {lo:.0f} (Setup > Technology Properties (Wire Bonds tab))"
            )
        if span > hi:
            raise LayoutError(
                f"wire bond span {span:.0f} DBU is longer than the "
                f"maximum {hi:.0f} (Setup > Technology Properties (Wire Bonds tab))"
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
                "Setup > Technology Properties (Wire Bonds tab)"
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
        # Exactly one ball per end: two bonds may share a pad, in which
        # case two identical discs sit on that end and each bond owns one
        # -- taking every disc found there would strip the neighbour's.
        balls = []
        for e in ends:
            for s in cell.shapes:
                if not (isinstance(s, Polygon) and s.layer == r_layer):
                    continue
                if any(s is b for b in balls):
                    continue
                x1, y1, x2, y2 = s.bbox()
                c = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
                if math.hypot(c[0] - e[0], c[1] - e[1]) <= 2.0:
                    balls.append(s)
                    break
        return best, balls

    def move_wire_bond(self, near, to, layer=None,
                       cell_name: Optional[str] = None, tol: Optional[float] = None):
        """Move ONE end of the wire bond nearest ``near`` to the point
        ``to``. ``near`` both picks the bond (by distance to its
        O------O line) and chooses which end to move -- whichever
        endpoint it is closer to; the other end stays put. The moved
        end's disc follows.

        The new span (fixed end to ``to``) must still lie within the
        bond rules' ``min_length``/``max_length``, and the repositioned
        bond still may not CROSS another. Raises LayoutError if no bond
        is near ``near``, the new span is out of range, or the move would
        cross a bond. One undo step; returns ``(fixed_end, new_end)``."""
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        r_layer, rules = self._resolve_bond_layer(layer)
        found = self.find_wire_bond(near, layer=r_layer, cell_name=name, tol=tol)
        if found is None:
            raise LayoutError(f"no wire bond near {tuple(near)} on layer {r_layer}")
        path, balls = found

        p0, p1 = tuple(path.points[0]), tuple(path.points[1])
        nx, ny = float(near[0]), float(near[1])
        grab_first = (math.hypot(nx - p0[0], ny - p0[1])
                      <= math.hypot(nx - p1[0], ny - p1[1]))
        moved_old = p0 if grab_first else p1
        fixed = p1 if grab_first else p0
        new_end = (int(round(to[0])), int(round(to[1])))

        span = math.hypot(new_end[0] - fixed[0], new_end[1] - fixed[1])
        lo, hi = float(rules["min_length"]), float(rules["max_length"])
        if span < lo:
            raise LayoutError(
                f"the moved bond would span {span:.0f} DBU, shorter than the "
                f"minimum {lo:.0f} (Setup > Technology Properties (Wire Bonds tab))"
            )
        if span > hi:
            raise LayoutError(
                f"the moved bond would span {span:.0f} DBU, longer than the "
                f"maximum {hi:.0f} (Setup > Technology Properties (Wire Bonds tab))"
            )

        cell = self.library.get_cell(name)
        for a, b in self._existing_bond_segments(cell, r_layer, exclude=path):
            if _segments_cross(fixed, new_end, a, b):
                raise LayoutError(
                    "moving that end there would cross an existing bond wire "
                    "-- bond wires share one plane and can't cross"
                )

        dx = new_end[0] - moved_old[0]
        dy = new_end[1] - moved_old[1]
        with self._batch():
            path.points = [new_end, fixed] if grab_first else [fixed, new_end]
            for ball in balls:
                bx0, by0, bx1, by1 = ball.bbox()
                c = ((bx0 + bx1) / 2.0, (by0 + by1) / 2.0)
                if math.hypot(c[0] - moved_old[0], c[1] - moved_old[1]) <= 2.0:
                    ball.points = [(px + dx, py + dy) for px, py in ball.points]
        self._notify()
        return fixed, new_end

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

    # --- Freerouting autorouter (Specctra DSN/SES) ------------------------
    def freerouting_rules(self) -> dict:
        """The design's saved Freerouting rules (techfile
        load_freerouting_rules; defaults: specctra.DEFAULT_RULES)."""
        from ..io.techfile import load_freerouting_rules
        return load_freerouting_rules(self.library)

    def set_freerouting_rules(self, **rules) -> dict:
        """Update and save some of the Freerouting rules (signal_width,
        power_width, clearance, signal_via, signal_via_window, power_via,
        power_via_window, power_nets, exclude_nets, passes, snap_90,
        keep_existing, remove_existing, io_entry_depth, layer_mode,
        bond_finger, finger_layer, die_keepout, keepout_bodies,
        optimize_passes, optimize_threshold, layer_directions,
        against_direction_cost, via_cost, via_optimize, via_optimize_max_flip).
        Returns
        the full set."""
        from ..io.techfile import load_freerouting_rules, save_freerouting_rules
        cur = load_freerouting_rules(self.library)
        cur.update(rules)
        save_freerouting_rules(self.library, cur)
        return load_freerouting_rules(self.library)

    def _route_layers(self):
        rr = load_route_rules(self.library)
        la, lb = rr.get("layer_a"), rr.get("layer_b")
        if not (isinstance(la, int) and la >= 0 and isinstance(lb, int) and lb >= 0):
            raise LayoutError("set the two routing layers first (Setup > Route Layers...)")
        return la, lb

    # --- Auto Place -----------------------------------------------------------
    def autoplace_settings(self) -> dict:
        """The design's saved Auto Place settings (grid, clearance, edge,
        bond_reach, thermal, effort, seed, resistor_layer, footprint_map,
        weights, class_weights, two_pin_weight, die_net_weight,
        family_power_mw). See hlayout.scripting.autoplace_session."""
        from .autoplace_session import load_settings
        return load_settings(self.library)

    def set_autoplace_settings(self, **settings) -> dict:
        """Update and save some Auto Place settings; returns them all."""
        from .autoplace_session import save_settings
        return save_settings(self.library, settings)

    def auto_place(self, netlist, overrides: Optional[str] = None, cell_name: Optional[str] = None,
                   improve: bool = False, only=None, on_progress=None, cancel=None,
                   **settings) -> dict:
        """Place the netlist's parts in the current (or named) cell for the
        shortest weighted straight-line net length, with hot parts kept
        apart and nothing overlapping, on the placement grid -- one undo
        step. ``netlist`` = a KiCad .net (or CSV / Tango) path or a parsed
        hlayout.io.kicad_netlist.Netlist; ``overrides`` = an optional CSV
        (ref, x, y, rotation, locked, weight, power_mw, layer, family, cell).
        Parts already placed (an instance labelled with the ref) start
        where they are; ``improve`` keeps that start for every part and
        only refines it; ``only`` = the refs allowed to move. Extra
        keyword ``settings`` override the saved ones for this run (grid,
        thermal, effort, seed, weights, ...). The netlist's pad nets are
        merged into the design's net names.
        Returns {"placed", "fixed", "generated", "skipped", "unmapped",
        "missing_pins", "length_before", "length_after", "overlaps",
        "outside"}."""
        from .autoplace_session import apply, build_job
        placer, info = build_job(self, netlist, cell_name=cell_name, overrides=overrides,
                                 settings=settings, only=set(only) if only else None)
        before = placer.wirelength() if all(p.placed for p in placer.parts) else None
        placer.run(effort=float(info["settings"]["effort"]), improve=improve,
                   on_progress=on_progress, cancel=cancel)
        bad, out = placer.overlaps()
        with self._batch():
            apply(self, placer, info)
        self._notify()
        return {"placed": sum(1 for p in placer.parts if not p.fixed),
                "fixed": sum(1 for p in placer.parts if p.fixed),
                "generated": info["generated"], "skipped": info["skipped"],
                "unmapped": info["unmapped"], "missing_pins": info["missing_pins"],
                "length_before": before, "length_after": placer.wirelength(),
                "overlaps": bad, "outside": out}

    def sli(self, cell_name: Optional[str] = None, nets=None, unrouted_only: bool = True) -> dict:
        """Straight-line interconnects (SLIs) of the design as placed: for
        every named net, the minimum spanning tree over its pads.
        -> {"nets": [{"net", "length", "segments"}], "total"} (um)."""
        from .autoplace_session import sli as _sli
        return _sli(self, cell_name=cell_name, nets=nets, unrouted_only=unrouted_only)

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
        return _send(self.library, name, app=app, window=window, export=export,
                     on_progress=on_progress, cancel=cancel)

    def _substrate_box(self, cell):
        """The substrate outline (boundary layer) of ``cell`` as a shapely
        box, or None."""
        from shapely.geometry import box as _box
        lay = self._boundary_layer()
        bb = self._boundary_bbox(cell, lay) if lay is not None else None
        return _box(*bb) if bb else None

    def _io_ring_box(self, cell):
        """The area inside the module I/O pad ring -- pin-labelled
        rectangles drawn in ``cell`` itself -- bounded by the pads' inner
        edges and grown by the Freerouting io_entry_depth (the strip the
        tracks enter by, which stays under the dielectric). A side with no
        pads takes the substrate edge. None without I/O pads."""
        from shapely.geometry import box as _box
        from ..core.shapes import Rectangle
        pads = [sh.bbox() for sh in cell.shapes
                if isinstance(sh, Rectangle) and getattr(sh, "pin", "")]
        if len(pads) < 2:
            return None
        sub = self._substrate_box(cell)
        if sub is not None:
            sx0, sy0, sx1, sy1 = sub.bounds
        else:
            sx0 = min(b[0] for b in pads); sy0 = min(b[1] for b in pads)
            sx1 = max(b[2] for b in pads); sy1 = max(b[3] for b in pads)
        mx, my = (sx0 + sx1) / 2.0, (sy0 + sy1) / 2.0
        hx, hy = max(sx1 - sx0, 1.0) / 2.0, max(sy1 - sy0, 1.0) / 2.0
        left, right, bottom, top = [], [], [], []
        for x0, y0, x1, y1 in pads:
            dx, dy = ((x0 + x1) / 2.0 - mx) / hx, ((y0 + y1) / 2.0 - my) / hy
            if abs(dx) >= abs(dy):
                (right if dx > 0 else left).append((x0, x1))
            else:
                (top if dy > 0 else bottom).append((y0, y1))
        try:
            entry = float(self.freerouting_rules().get("io_entry_depth") or 0)
        except Exception:
            entry = 0.0
        # each side: the pads' inner edge, moved OUT by the entry strip
        x0 = max(b[1] for b in left) - entry if left else sx0
        x1 = min(b[0] for b in right) + entry if right else sx1
        y0 = max(b[1] for b in bottom) - entry if bottom else sy0
        y1 = min(b[0] for b in top) + entry if top else sy1
        if x1 <= x0 or y1 <= y0:
            return None
        return _box(x0, y0, x1, y1)

    def _boundary_layer(self):
        rr = load_route_rules(self.library)
        b = rr.get("boundary_layer")
        if isinstance(b, int) and b >= 0:
            return b
        # a layer named like a substrate outline
        for n, lay in enumerate(self.library.layers):
            nm = (getattr(lay, "name", "") or "").lower()
            if "substrate" in nm or "outline" in nm or "boundary" in nm:
                cell = self.library.cells.get(self.current_cell_name)
                if cell is not None and self._boundary_bbox(cell, n) is not None:
                    return n
        return None

    def _rules_with_drc(self, R) -> dict:
        """The Freerouting rules, tightened to the design's own DRC rules
        on the routing layers: the clearance is at least the largest
        MIN_SPACE / MIN_SPACE_BETWEEN, the track widths at least the
        largest MIN_WIDTH -- so the router can't produce a layout the
        design's own Tools > DRC then flags."""
        R = dict(R)
        try:
            from ..io.techfile import load_drc_rules
            drc = load_drc_rules(self.library)
        except Exception:
            return R
        lays = set(self._route_layers())
        space = width = 0.0
        for r in drc:
            d = r if isinstance(r, dict) else getattr(r, "__dict__", {})
            kind = str(d.get("kind", "")).upper()
            la_, lb_ = d.get("layer_a"), d.get("layer_b")
            try:
                v = float(d.get("value") or 0)
            except (TypeError, ValueError):
                continue
            if kind == "MIN_WIDTH" and la_ in lays:
                width = max(width, v)
            elif kind == "MIN_SPACE" and la_ in lays:
                space = max(space, v)
            elif kind == "MIN_SPACE_BETWEEN" and la_ in lays and (lb_ in lays or lb_ is None):
                space = max(space, v)
        if space > float(R.get("clearance", 0)):
            R["clearance"] = space
        for k in ("signal_width", "power_width"):
            if width > float(R.get(k, 0)):
                R[k] = width
        return R

    # --- hybrid router (chip-and-wire) results -----------------------------
    def _hybrid_layer_map(self, design: dict, layers: Optional[dict] = None) -> dict:
        """{hybrid layer name or role: HLayout layer} for importing a routed
        hybrid result. Conductors map in stack order onto Setup's two Route
        Layers; DIELECTRIC onto the dielectric layer; RESISTOR onto the
        resistor body layer. Roles: "substrate", "die", "die_pad", "bond",
        "doc". ``layers`` overrides any entry (numbers or layer names)."""
        from ..io.techfile import load_bond_rules, load_route_rules

        lib = self.library

        def named(*words, default):
            for n in range(len(lib.layers)):
                nm = (lib.layers[n].name or "").lower().replace("_", " ")
                if any(w in nm for w in words):
                    return n
            return default

        rr = load_route_rules(lib)
        la = rr.get("layer_a") if isinstance(rr.get("layer_a"), int) and rr["layer_a"] >= 0 else 3
        lb = rr.get("layer_b") if isinstance(rr.get("layer_b"), int) and rr["layer_b"] >= 0 else 5
        try:
            diel = int(load_dielectric_rules(lib)["layer"])
        except Exception:
            diel = -1
        if diel < 0:
            diel = named("dielectric", default=4)
        try:
            rg = load_resistor_geometry_rules(lib)
            res = rg.get("resistor_layer")
            if not (isinstance(res, int) and res >= 0):
                inks = [v for v in (rg.get("ink_layers") or {}).values() if isinstance(v, int)]
                res = inks[0] if inks else 30
        except Exception:
            res = 30
        try:
            br = load_bond_rules(lib)
            bond = int(br.get("layer", -1))
            die_pad = next((int(x) for x in (br.get("die_pad_layers") or []) if int(x) >= 0), -1)
        except Exception:
            bond, die_pad = -1, -1
        if bond < 0:
            bond = named("wire bond", "bond", default=59)
        if die_pad < 0:
            die_pad = named("chip pad", "die pad", default=58)

        out = {
            "substrate": named("substrate", "alumina", "ceramic", default=57),
            "die": named("epoxy", "die attach", default=20),
            "die_pad": die_pad,
            "bond": bond,
            "doc": named("document", "doc", default=61),
        }
        conductors = [ly.get("name") for ly in (design.get("stack") or {}).get("layers", [])
                      if ly.get("kind") == "CONDUCTOR"]
        for i, name in enumerate(conductors):
            out[name] = la if i == 0 else lb
        for ly in (design.get("stack") or {}).get("layers", []):
            if ly.get("kind") == "DIELECTRIC":
                out[ly.get("name")] = diel
            elif ly.get("kind") == "RESISTOR":
                out[ly.get("name")] = res
        for k, v in (layers or {}).items():
            out[k] = lib.layers.resolve(v)
        return out

    def import_routed_hybrid(self, path: str, cell_name: Optional[str] = None, *,
                             layers: Optional[dict] = None, origin=(0, 0),
                             overwrite: bool = True, dielectric_fill: bool = True) -> dict:
        """Bring a chip-and-wire router result (``*.routed.json`` from
        HybridRouterCli) into the design as one undo step, in its own cell
        (default: the design's name). Router micrometres are DBU, offset by
        ``origin``:

        - the substrate outline;
        - each die as a placed cell (body on the die/epoxy layer, numbered
          pads on the die-pad layer), labelled with its refdes;
        - each two-pin component as a placed cell (pins 1 and 2 = the
          router's A and B on their conductor, body on the resistor layer
          for a printed resistor, the documentation layer for a chip);
        - every substrate pad (bond fingers, jumper lands, external pads) as
          a numbered pad;
        - routed traces as round-capped paths, each dielectric crossover as
          a pad on both conductors plus its window on the dielectric, then
          the dielectric fill (``dielectric_fill``);
        - every wire bond, jumpers included, as a path on the bond layer at
          the wire diameter.

        Pad nets become the design's net names; the bond diagram is kept
        with the design for generate_bond_table() and the assembly drawing.
        ``layers`` overrides the layer mapping (see _hybrid_layer_map()).
        Returns counts, the router's stats and its unrouted connections."""
        from ..io import hybrid_routed as HR
        from ..io.techfile import load_net_names

        data = HR.load_routed(path)
        design = data["design"]
        L = self._hybrid_layer_map(design, layers)
        lib = self.library
        ox, oy = int(origin[0]), int(origin[1])
        name = (cell_name or design.get("name") or "HYBRID").strip().replace(" ", "_")

        def lay(layer_name):
            if layer_name not in L:
                raise LayoutError(f"no HLayout layer for hybrid layer {layer_name!r} -- "
                                  f"pass layers={{'{layer_name}': <layer>}}")
            return L[layer_name]

        def rect(cell, layer, cx, cy, w, h, pin=""):
            cell.add_rectangle(layer, round(cx - w / 2), round(cy - h / 2),
                               round(cx + w / 2), round(cy + h / 2), pin=pin)

        def fresh(cname):
            if cname in lib.cells:
                if not overwrite:
                    raise LayoutError(f"cell {cname!r} already exists; pass overwrite=True")
                lib.delete_cell(cname)
            return lib.new_cell(cname)

        stats = {"dies": 0, "components": 0, "pads": 0, "fingers": 0, "jumper_lands": 0,
                 "traces": 0, "windows": 0, "wires": 0, "jumpers": 0}
        nets = {}
        with self._batch():
            if name in lib.cells:
                if not overwrite:
                    raise LayoutError(f"cell {name!r} already exists; pass overwrite=True")
                lib.delete_cell(name)
            sub = design.get("substrate") or {}
            sw, sh = int(sub.get("widthUm", 0)), int(sub.get("heightUm", 0))

            # parts first, so the design cell can place them
            die_cells = {}
            for d in design.get("dies", []):
                cname = "DIE_" + str(d.get("partName") or d.get("refdes")).replace(" ", "_")
                if cname not in die_cells.values():
                    c = fresh(cname)
                    w, h = int(d.get("widthUm", 0)), int(d.get("heightUm", 0))
                    c.add_rectangle(L["die"], 0, 0, w, h)
                    for p in d.get("pads", []):
                        px, py = HR._pt(p.get("position"))
                        rect(c, L["die_pad"], px, py, int(p.get("widthUm", 0)),
                             int(p.get("heightUm", 0)), pin=str(p.get("id")))
                die_cells[d.get("refdes")] = cname
            comp_cells = {}
            for comp in design.get("components", []):
                cname = "CMP_" + str(comp.get("refdes"))
                c = fresh(cname)
                bl, bw = int(comp.get("bodyLengthUm", 0)), int(comp.get("bodyWidthUm", 0))
                body = lay(next((ly.get("name") for ly in design["stack"]["layers"]
                                 if ly.get("kind") == "RESISTOR"), "doc")) \
                    if comp.get("kind", "PRINTED_RESISTOR") == "PRINTED_RESISTOR" else L["doc"]
                if bl > 0 and bw > 0:
                    rect(c, body, 0, 0, bl, bw)
                for pin_no, key in (("1", "pinA"), ("2", "pinB")):
                    p = comp.get(key) or {}
                    px, py = HR._pt(p.get("position"))
                    rect(c, lay(p.get("layer")), px, py, int(p.get("widthUm", 0)),
                         int(p.get("heightUm", 0)), pin=pin_no)
                    if p.get("net"):
                        nets[f"{comp.get('refdes')}.{pin_no}"] = p["net"]
                comp_cells[comp.get("refdes")] = cname

            cell = lib.new_cell(name)
            if sw > 0 and sh > 0:
                cell.add_rectangle(L["substrate"], ox, oy, ox + sw, oy + sh)
            for d in design.get("dies", []):
                dx, dy = HR._pt(d.get("origin"))
                self._place_in(cell, die_cells[d.get("refdes")], (ox + dx, oy + dy),
                               HR.rotation_degrees(d.get("rotation")), str(d.get("refdes")))
                for p in d.get("pads", []):
                    if p.get("net"):
                        nets[f"{d.get('refdes')}.{p.get('id')}"] = p["net"]
                stats["dies"] += 1
            for comp in design.get("components", []):
                cx, cy = HR._pt(comp.get("centre"))
                self._place_in(cell, comp_cells[comp.get("refdes")], (ox + cx, oy + cy),
                               HR.rotation_degrees(comp.get("rotation")), str(comp.get("refdes")))
                stats["components"] += 1
            for p in design.get("substratePads", []):
                px, py = HR._pt(p.get("position"))
                rect(cell, lay(p.get("layer")), ox + px, oy + py, int(p.get("widthUm", 0)),
                     int(p.get("heightUm", 0)), pin=str(p.get("id")))
                if p.get("net"):
                    nets[f"IO.{p.get('id')}"] = p["net"]
                stats["pads"] += 1
                role = p.get("role", "BOND_FINGER")
                if role == "BOND_FINGER":
                    stats["fingers"] += 1
                elif role == "JUMPER_LAND":
                    stats["jumper_lands"] += 1
            for t in data["traces"]:
                pts = [(ox + x, oy + y) for x, y in (HR._pt(q) for q in t.get("points", []))]
                pts = [q for i, q in enumerate(pts) if i == 0 or q != pts[i - 1]]
                if len(pts) >= 2:
                    cell.add_path(lay(t.get("layer")), pts, int(t.get("widthUm", 0)), cap="round")
                    stats["traces"] += 1
            diel_layers = {ly.get("name") for ly in (design.get("stack") or {}).get("layers", [])
                           if ly.get("kind") == "DIELECTRIC"}
            diel = L[next(iter(diel_layers))] if diel_layers else None
            for wdw in data["windows"]:
                px, py = HR._pt(wdw.get("position"))
                pad = int(wdw.get("padUm", 0))
                for key in ("fromLayer", "toLayer"):
                    rect(cell, lay(wdw.get(key)), ox + px, oy + py, pad, pad)
                if diel is not None:
                    o = int(wdw.get("openingUm", 0))
                    rect(cell, diel, ox + px, oy + py, o, o)
                stats["windows"] += 1
            if diel is not None and sw > 0 and sh > 0:
                edge = int(((design.get("rules") or {}).get("conductor") or {})
                           .get("edgeClearanceUm") or 250)
                cell.add_rectangle(diel, ox + edge, oy + edge, ox + sw - edge, oy + sh - edge)
            for w in design.get("wires", []):
                a = HR.endpoint_position(design, w.get("from"))
                b = HR.endpoint_position(design, w.get("to"))
                if a is None or b is None or a == b:
                    continue
                dia = int((w.get("spec") or {}).get("diameterUm", 25))
                cell.add_path(L["bond"], [(ox + a[0], oy + a[1]), (ox + b[0], oy + b[1])],
                              dia, cap="round")
                stats["wires"] += 1
                if HR.wire_purpose(w) == "JUMPER":
                    stats["jumpers"] += 1

            rows = HR.bond_diagram(design)
            lib.tech_params["bond_diagram"] = json.dumps(rows)
            lib.tech_params["hybrid_design"] = json.dumps(
                {"cell": name, "design": design, "layer_map": L, "origin": [ox, oy]})
            self.current_cell_name = name
            if nets:
                merged = dict(load_net_names(lib))
                merged.update(nets)
                self.set_net_names(merged)
            if diel is not None and dielectric_fill:
                try:
                    self.generate_dielectric(cell_name=name)
                except Exception:
                    pass
        self._notify()
        stats.update(cell=name, bond_rows=len(rows), router_stats=data.get("stats") or {},
                     unrouted=data.get("unrouted") or [],
                     issues=len(data.get("issues") or []), layer_map=L)
        return stats

    def _place_in(self, cell, child: str, origin, rotation: float, label: str):
        """Place ``child`` in ``cell`` (not necessarily the current cell)."""
        from ..core.shapes import SRef
        cell.srefs.append(SRef(cell_name=child, origin=(int(origin[0]), int(origin[1])),
                               rotation=float(rotation), label=label))

    def bond_diagram(self) -> list:
        """The bond diagram kept with the design by import_routed_hybrid():
        one dict per wire (hybrid_routed.BOND_COLUMNS), in bonding order."""
        raw = self.library.tech_params.get("bond_diagram")
        if not raw:
            return []
        cached = getattr(self, "_bond_rows_cache", None)
        if cached is not None and cached[0] is raw:
            return cached[1]
        try:
            rows = json.loads(raw)
        except (TypeError, ValueError):
            return []
        rows = rows if isinstance(rows, list) else []
        self._bond_rows_cache = (raw, rows)
        return rows

    def export_bond_diagram(self, path: str) -> int:
        """Write the stored bond diagram as CSV (the router's column layout).
        Returns the number of wires."""
        from ..io.hybrid_routed import write_bond_diagram_csv
        rows = self.bond_diagram()
        if not rows:
            raise LayoutError("no bond diagram -- import a routed hybrid result first")
        write_bond_diagram_csv(rows, path)
        return len(rows)

    def generate_bond_table(self, cell_name: str, layer, *, rows: Optional[list] = None,
                            origin=(0, 0), row_height: float = 250.0,
                            col_widths=(600.0, 900.0, 1500.0, 1500.0, 1500.0, 1100.0,
                                        1000.0, 900.0),
                            text_height: float = 150.0, line_layer=None,
                            line_width: float = 20.0, title: Optional[str] = "BOND DIAGRAM",
                            overwrite: bool = True) -> str:
        """Draw the bond diagram (default: the stored one) as a table cell --
        SEQ, WIRE, NET, FIRST BOND, SECOND BOND, LENGTH (MM), WIRE (metal and
        diameter), LOOP (UM) -- laid out like generate_resistor_table()."""
        rows = self.bond_diagram() if rows is None else rows
        if not rows:
            raise LayoutError("no bond diagram -- import a routed hybrid result first")
        HEAD = ("SEQ", "WIRE", "NET", "FIRST BOND", "SECOND BOND", "LENGTH (MM)", "WIRE",
                "LOOP (UM)")
        if len(col_widths) != len(HEAD):
            raise LayoutError(f"col_widths must have {len(HEAD)} entries")
        if text_height * 1.2 >= row_height:
            raise LayoutError("row_height must be bigger than text_height*1.2")
        cell_name = (cell_name or "").strip()
        if not cell_name:
            raise LayoutError("a cell name is required")
        if cell_name in self.library.cells:
            if not overwrite:
                raise LayoutError(f"{cell_name!r} already exists; pass overwrite=True")
            self.library.delete_cell(cell_name)
        t_layer = self.library.layers.resolve(layer)
        g_layer = self.library.layers.resolve(line_layer) if line_layer is not None else t_layer
        gw = int(round(line_width))
        h = int(round(text_height))
        metal = {"GOLD": "AU", "ALUMINIUM": "AL", "ALUMINUM": "AL", "COPPER": "CU"}
        ox, oy = origin
        col_x = [ox]
        for w in col_widths[:-1]:
            col_x.append(col_x[-1] + w)
        top_pad = (row_height + text_height) / 2.0
        with self._batch():
            cell = self.library.new_cell(cell_name)

            def put_row(y, texts):
                for x, w, text in zip(col_x, col_widths, texts):
                    if text:
                        est = text_height * 0.6 * len(text)
                        cell.add_text(t_layer, (round(x + max(0.0, (w - est) / 2.0)), round(y)),
                                      text, height=h)

            put_row(oy, HEAD)
            for i, r in enumerate(rows, start=1):
                mat = metal.get(str(r.get("material", "")).upper(), str(r.get("material", ""))[:2])
                put_row(oy - i * row_height, (
                    str(r.get("seq", i)), str(r.get("wire", "")), str(r.get("net") or ""),
                    str(r.get("first_bond", "")), str(r.get("second_bond", "")),
                    f"{(r.get('length_um') or 0) / 1000.0:.2f}",
                    f"{mat} {r.get('diameter_um', '')}",
                    str(r.get("loop_height_um", "")),
                ))
            tops = [oy + top_pad - i * row_height for i in range(len(rows) + 2)]
            bounds = col_x + [col_x[-1] + col_widths[-1]]
            left, right = bounds[0], bounds[-1]
            header_top, bottom = tops[0], tops[-1]
            top = header_top
            lines = list(tops)
            if title:
                top = header_top + row_height
                est = text_height * 0.6 * len(title)
                cell.add_text(t_layer, (round(ox + max(0.0, (right - left - est) / 2.0)),
                                        round(oy + row_height)), title, height=h)
                lines = [top] + lines
            for y in lines:
                cell.add_path(g_layer, [(round(left), round(y)), (round(right), round(y))],
                              gw, cap="flush")
            for x in (left, right):
                cell.add_path(g_layer, [(round(x), round(top)), (round(x), round(bottom))],
                              gw, cap="flush")
            for x in bounds[1:-1]:
                cell.add_path(g_layer, [(round(x), round(header_top)), (round(x), round(bottom))],
                              gw, cap="flush")
        return cell_name

    # --- hybrid editing (Phase 6): bond info, finger moves, resistor resize ----
    def _hybrid_state(self) -> Optional[dict]:
        """The routed hybrid kept by import_routed_hybrid(): {"cell", "design",
        "layer_map", "origin"}; None when the design has none."""
        raw = self.library.tech_params.get("hybrid_design")
        if not raw:
            return None
        cached = getattr(self, "_hybrid_cache", None)
        if cached is not None and cached[0] is raw:
            return cached[1]
        try:
            st = json.loads(raw)
        except (TypeError, ValueError):
            return None
        st = st if isinstance(st, dict) and "design" in st else None
        self._hybrid_cache = (raw, st)
        return st

    def _save_hybrid_state(self, st: dict) -> None:
        from ..io import hybrid_routed as HR
        self.library.tech_params["hybrid_design"] = json.dumps(st)
        self.library.tech_params["bond_diagram"] = json.dumps(HR.bond_diagram(st["design"]))

    def _hybrid_bond_rules(self) -> dict:
        """min/max wire length and max departure angle: the routed design's
        own rules when there is one, else the Wire Bonds tab of Setup > Technology Properties."""
        from ..io.techfile import load_bond_rules
        st = self._hybrid_state()
        bw = (((st or {}).get("design") or {}).get("rules") or {}).get("bondWire") or {}
        if bw:
            return {"min": float(bw.get("minLengthUm") or 500),
                    "max": float(bw.get("maxLengthUm") or 3000),
                    "angle": float(bw.get("maxAngleDeg") or 45.0)}
        br = load_bond_rules(self.library)
        return {"min": float(br.get("min_length", 300)), "max": float(br.get("max_length", 5000)),
                "angle": 45.0}

    def _bond_layer(self) -> int:
        from ..io.techfile import load_bond_rules
        st = self._hybrid_state()
        if st and "bond" in (st.get("layer_map") or {}):
            return int(st["layer_map"]["bond"])
        return int(load_bond_rules(self.library).get("layer", -1))

    def bond_wire_info(self, point, tol: float = 50.0, cell_name: Optional[str] = None
                       ) -> Optional[dict]:
        """The wire bond nearest ``point`` (within ``tol`` DBU) as a dict --
        ends, length, and, when the bond diagram knows the wire, its id, net,
        material, diameter, bond type, loop height and departure angles --
        plus a one-line ``text`` for the status bar. None if no bond is near."""
        from ..core.shapes import Path as _Path
        cell = self.library.get_cell(cell_name or self.current_cell_name)
        layer = self._bond_layer()
        if layer < 0:
            return None
        px, py = float(point[0]), float(point[1])
        best, best_d = None, float(tol)
        for sh in cell.shapes:
            if not (isinstance(sh, _Path) and sh.layer == layer and len(sh.points) >= 2):
                continue
            (ax, ay), (bx, by) = sh.points[0], sh.points[-1]
            vx, vy = bx - ax, by - ay
            ll = vx * vx + vy * vy
            t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / ll))
            d = math.hypot(px - (ax + t * vx), py - (ay + t * vy))
            if d <= best_d:
                best, best_d = sh, d
        if best is None:
            return None
        a, b = tuple(best.points[0]), tuple(best.points[-1])
        info = {"a": a, "b": b, "length_um": round(math.hypot(b[0] - a[0], b[1] - a[1]))}
        st = self._hybrid_state()
        ox, oy = (st or {}).get("origin") or (0, 0)
        for r in self.bond_diagram():
            e1 = (r["first_x_um"] + ox, r["first_y_um"] + oy)
            e2 = (r["second_x_um"] + ox, r["second_y_um"] + oy)
            if {e1, e2} == {a, b}:
                info.update(r)
                break
        mat = {"GOLD": "Au", "ALUMINIUM": "Al", "ALUMINUM": "Al", "COPPER": "Cu"}
        parts = []
        if info.get("wire"):
            parts.append(f"{info['wire']} {info.get('net') or ''}".strip())
            parts.append(f"{info['first_bond']} -> {info['second_bond']}")
        parts.append(f"{info['length_um'] / 1000.0:.2f} mm")
        if info.get("material"):
            parts.append(f"{mat.get(str(info['material']).upper(), info['material'])} "
                         f"{info.get('diameter_um')} um {str(info.get('bond_type', '')).lower()}")
            parts.append(f"loop {info.get('loop_height_um')} um")
        angles = [x for x in (info.get("first_angle_deg"), info.get("second_angle_deg"))
                  if x is not None]
        if angles:
            parts.append("angle " + "/".join(f"{x:g}" for x in angles) + " deg")
        info["text"] = "Bond " + " · ".join(parts)
        return info

    def _hybrid_pad_at(self, cell, point, tol=0.0):
        """(shape, pad id) of the numbered substrate pad (a pin-labelled
        rectangle drawn in ``cell`` itself) under ``point``."""
        from ..core.shapes import Rectangle
        px, py = point
        best = None
        for sh in cell.shapes:
            if not (isinstance(sh, Rectangle) and getattr(sh, "pin", "")):
                continue
            x0, y0, x1, y1 = sh.bbox()
            if x0 - tol <= px <= x1 + tol and y0 - tol <= py <= y1 + tol:
                area = (x1 - x0) * (y1 - y0)
                if best is None or area < best[2]:
                    best = (sh, sh.pin, area)
        return (best[0], best[1]) if best else (None, None)

    def move_bond_finger(self, at, to, cell_name: Optional[str] = None,
                         dry_run: bool = False, tol: float = 0.0) -> dict:
        """Move the bond finger (or jumper land) under ``at`` so its centre
        lands on ``to``, and re-bond: every wire ending on it follows. One
        undo step. Each moved wire is checked against the bond rules (length
        min/max, departure angle at the die); ``warnings`` lists what fails,
        and ``on_trace`` says whether the finger still touches its own
        printed trace (if not, the net needs re-routing). ``dry_run`` only
        reports -- the live preview of Edit > Move Bond Finger."""
        from shapely.geometry import LineString, box
        from ..core.shapes import Path as _Path
        from ..io import hybrid_routed as HR

        name = cell_name or self.current_cell_name
        cell = self.library.get_cell(name)
        pad, pid = self._hybrid_pad_at(cell, at, tol)
        if pad is None:
            raise LayoutError(f"no bond finger at ({at[0]}, {at[1]})")
        x0, y0, x1, y1 = pad.bbox()
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        nx, ny = round(float(to[0])), round(float(to[1]))
        dx, dy = nx - cx, ny - cy
        layer = self._bond_layer()
        moved = []
        for sh in cell.shapes:
            if isinstance(sh, _Path) and sh.layer == layer and len(sh.points) == 2:
                for k in (0, 1):
                    ex, ey = sh.points[k]
                    if abs(ex - cx) <= 1 and abs(ey - cy) <= 1:
                        moved.append((sh, k))
        rules = self._hybrid_bond_rules()
        st = self._hybrid_state()
        design = (st or {}).get("design")
        ox, oy = (st or {}).get("origin") or (0, 0)
        rows = []
        if design is not None and st.get("cell") == name:
            d2 = json.loads(json.dumps(design))
            for p in d2.get("substratePads", []):
                if str(p.get("id")) == str(pid):
                    p["position"] = {"x": nx - ox, "y": ny - oy}
            rows = [r for r in HR.bond_diagram(d2) if pid in (r["first_bond"], r["second_bond"])]
        else:
            d2 = None
            for sh, k in moved:
                other = sh.points[1 - k]
                rows.append({"wire": None, "first_bond": "?", "second_bond": pid,
                             "length_um": round(math.hypot(other[0] - nx, other[1] - ny)),
                             "first_angle_deg": None, "second_angle_deg": None})
        warnings = []
        for r in rows:
            label = r.get("wire") or "wire"
            if r["length_um"] < rules["min"] or r["length_um"] > rules["max"]:
                warnings.append(f"{label}: length {r['length_um'] / 1000:.2f} mm outside "
                                f"{rules['min'] / 1000:g}-{rules['max'] / 1000:g} mm")
            for ang in (r.get("first_angle_deg"), r.get("second_angle_deg")):
                if ang is not None and ang > rules["angle"] + 1e-9:
                    warnings.append(f"{label}: {ang:g} deg off the die-edge normal "
                                    f"(max {rules['angle']:g})")
        w, h = x1 - x0, y1 - y0
        new_box = box(nx - w / 2.0, ny - h / 2.0, nx + w / 2.0, ny + h / 2.0)
        on_trace = any(isinstance(sh, _Path) and sh.layer == pad.layer and len(sh.points) >= 2
                       and LineString(sh.points).buffer(sh.width / 2.0).intersects(new_box)
                       for sh in cell.shapes)
        out = {"pad": pid, "from": (round(cx), round(cy)), "to": (nx, ny),
               "box": (nx - w / 2.0, ny - h / 2.0, nx + w / 2.0, ny + h / 2.0),
               "old_box": (x0, y0, x1, y1),
               "wires": [{"a": tuple(sh.points[1 - k]), "b": (nx, ny)} for sh, k in moved],
               "rows": rows, "warnings": warnings, "on_trace": on_trace}
        if dry_run:
            return out
        with self._batch():
            pad.points = [(p[0] + dx, p[1] + dy) for p in pad.points]
            for sh, k in moved:
                pts = list(sh.points)
                pts[k] = (nx, ny)
                sh.points = pts
            if d2 is not None:
                st = dict(st)              # never mutate the cached parse
                st["design"] = d2
                self._save_hybrid_state(st)
        self._notify()
        return out

    def resistor_at(self, point, cell_name: Optional[str] = None) -> Optional[dict]:
        """The imported printed resistor whose body or pins contain
        ``point``: {"refdes", "centre", "rotation"} or None."""
        st = self._hybrid_state()
        if not st:
            return None
        name = cell_name or self.current_cell_name
        if st.get("cell") != name:
            return None
        ox, oy = st.get("origin") or (0, 0)
        from ..io import hybrid_routed as HR
        px, py = point
        for c in st["design"].get("components", []):
            if c.get("kind", "PRINTED_RESISTOR") != "PRINTED_RESISTOR":
                continue
            cx, cy = HR._pt(c.get("centre"))
            cx, cy = cx + ox, cy + oy
            lx, ly = HR.rotate({"R90": "R270", "R270": "R90"}.get(c.get("rotation"), c.get("rotation")),
                               px - cx, py - cy)
            half_l = max(int(c.get("bodyLengthUm", 0)),
                         2 * max(abs(HR._pt((c.get(k) or {}).get("position"))[0])
                                 + int((c.get(k) or {}).get("widthUm", 0)) / 2.0
                                 for k in ("pinA", "pinB"))) / 2.0
            half_w = max(int(c.get("bodyWidthUm", 0)),
                         max(int((c.get(k) or {}).get("heightUm", 0)) for k in ("pinA", "pinB"))) / 2.0
            if abs(lx) <= half_l and abs(ly) <= half_w:
                return {"refdes": c.get("refdes"), "centre": (cx, cy),
                        "rotation": c.get("rotation", "R0")}
        return None

    def resistor_info(self, refdes: str) -> dict:
        """An imported printed resistor: body length and width (um), its
        squares (L/W), the value from the router file and the sheet
        resistance that implies (value / squares)."""
        from ..io.hybrid_routed import parse_ohms
        st = self._hybrid_state()
        comp = next((c for c in ((st or {}).get("design") or {}).get("components", [])
                     if c.get("refdes") == refdes), None)
        if comp is None:
            raise LayoutError(f"no imported resistor {refdes!r}")
        L, W = int(comp.get("bodyLengthUm", 0)), int(comp.get("bodyWidthUm", 0))
        if L <= 0 or W <= 0:
            raise LayoutError(f"{refdes} has no body size")
        sheet = comp.get("sheetOhms")
        value = parse_ohms(comp.get("value"))
        if sheet is None and value is not None:
            sheet = value / (L / W)
        return {"refdes": refdes, "length_um": L, "width_um": W, "squares": L / W,
                "value_ohms": value, "sheet_ohms": sheet}

    def resize_resistor(self, refdes: str, length: Optional[float] = None,
                        width: Optional[float] = None, dry_run: bool = False) -> dict:
        """Re-size an imported printed resistor's body (um). Its value
        follows the ink's sheet resistance: R = Rs x L / W, with Rs worked
        out from the value the router file gave it, so the readout stays
        consistent across repeated edits. The pins move with the body ends
        (a length change moves each by half the difference -- the traces on
        them then need re-routing; ``pins_moved_um`` says by how much). One
        undo step; ``dry_run`` only reports (the live readout of Edit >
        Resize Resistor)."""
        from ..core.shapes import Rectangle
        from ..io.hybrid_routed import format_ohms
        info = self.resistor_info(refdes)
        L0, W0 = info["length_um"], info["width_um"]
        L = int(round(length)) if length is not None else L0
        W = int(round(width)) if width is not None else W0
        if L <= 0 or W <= 0:
            raise LayoutError("length and width must be positive")
        rs = info["sheet_ohms"]
        r_new = rs * L / W if rs is not None else None
        shift = (L - L0) / 2.0
        out = {"refdes": refdes, "length_um": L, "width_um": W, "squares": L / W,
               "sheet_ohms": rs, "value_ohms": r_new, "was_ohms": info["value_ohms"],
               "pins_moved_um": abs(shift),
               "text": (f"{refdes}: L {L0 / 1000:.3f}->{L / 1000:.3f} mm, W {W / 1000:.3f} mm, "
                        f"{L / W:.2f} sq, R = {format_ohms(r_new)} "
                        f"(was {format_ohms(info['value_ohms'])}, "
                        f"Rs {format_ohms(rs)}/sq)")}
        if dry_run:
            return out
        st = json.loads(json.dumps(self._hybrid_state()))   # a copy: the parse is cached
        comp = next(c for c in st["design"]["components"] if c.get("refdes") == refdes)
        cell = self.library.cells.get("CMP_" + str(refdes))
        if cell is None:
            raise LayoutError(f"part cell CMP_{refdes} not found")
        res_layer = st["layer_map"].get(next((ly.get("name") for ly in
                                              st["design"]["stack"]["layers"]
                                              if ly.get("kind") == "RESISTOR"), ""), None)
        with self._batch():
            for sh in cell.shapes:
                if not isinstance(sh, Rectangle):
                    continue
                if not getattr(sh, "pin", "") and (res_layer is None or sh.layer == res_layer):
                    sh.points = [(-L / 2.0, -W / 2.0), (L / 2.0, -W / 2.0),
                                 (L / 2.0, W / 2.0), (-L / 2.0, W / 2.0)]
                    sh.points = [(round(x), round(y)) for x, y in sh.points]
                elif getattr(sh, "pin", "") in ("1", "2") and shift:
                    x0, _y0, x1, _y1 = sh.bbox()
                    sgn = -1 if (x0 + x1) / 2.0 < 0 else 1
                    sh.points = [(round(x + sgn * shift), y) for x, y in sh.points]
            comp["bodyLengthUm"], comp["bodyWidthUm"] = L, W
            comp["sheetOhms"] = rs
            if r_new is not None:
                comp["value"] = format_ohms(r_new).replace(" ", "")
            for k, sgn in (("pinA", -1), ("pinB", 1)):
                p = comp.get(k) or {}
                pos = p.get("position") or {"x": 0, "y": 0}
                if shift:
                    s = -1 if pos.get("x", 0) < 0 else 1
                    pos["x"] = round(pos.get("x", 0) + s * shift)
                p["position"] = pos
            self._save_hybrid_state(st)
        self._notify()
        return out

    def _single_layer_route(self, R) -> bool:
        """Route on the 1st conductor alone? rules layer_mode "single" /
        "two", or "auto": yes when nothing anywhere is drawn on the 2nd
        conductor (a one-conductor chip-and-wire hybrid)."""
        mode = str(R.get("layer_mode", "auto")).lower()
        if mode.startswith("s") or mode == "1":
            return True
        if mode.startswith("t") or mode == "2":
            return False
        if self._fingers_on_second(R):
            return False            # bond fingers on the 2nd conductor need both
        _la, lb = self._route_layers()
        return not any(sh.layer == lb for c in self.library.cells.values() for sh in c.shapes)

    @staticmethod
    def _fingers_on_second(R) -> bool:
        """Rules finger_layer asks for the bond fingers on the 2nd conductor?"""
        v = str(R.get("finger_layer", "first")).strip().lower()
        return v.startswith("s") or v in ("2", "b", "top", "layer_b")

    def _finger_layer(self, R) -> int:
        """The conductor the chip-and-wire bond fingers go on: the 2nd
        (top) conductor when rules finger_layer is "second" and the route
        uses both layers, else the 1st."""
        la, lb = self._route_layers()
        return lb if self._fingers_on_second(R) and not self._single_layer_route(R) else la

    def remove_unused_bonds(self, cell_name: Optional[str] = None, dry_run: bool = False) -> dict:
        """Take the wire bond and bond finger off every die pad the design
        doesn't use, to open up routing room round the die.

        For each labelled die placed in the cell whose part carries its own
        bonds (and fingers): a pad is unused when the design's net names
        give "REF.PIN" no net. That die gets its own copy of the part --
        "<part>_<REF>", e.g. DIE_54AS181_U1, reused if it is already this
        die's own copy -- without those bonds (wire and both balls) and
        fingers; the die pads themselves stay (they're on the chip). Other
        dies using the same part, and the library part, are untouched.
        One undo step. ``dry_run`` only reports. Returns {"dies": {REF:
        [pins removed]}, "cells": {REF: cell now used}, "bonds": n}."""
        import copy as _copy
        from ..core.shapes import Path
        from ..io.techfile import load_bond_rules, load_net_names
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        lib = self.library
        cell = lib.get_cell(name)
        names = load_net_names(lib)
        if not names:
            raise LayoutError("the design has no net names -- import them first: without them "
                              "every pad would look unused")
        br = load_bond_rules(lib)
        blay = int(br.get("layer", -1))
        pad_layers = {int(l) for l in (br.get("die_pad_layers") or [])}
        if blay < 0 or not pad_layers:
            raise LayoutError("set the wire-bond rules (bond layer, die-pad layers) first: Setup > Technology Properties, Wire Bonds tab")
        users = {}
        for r in cell.srefs:
            users.setdefault(r.cell_name, []).append(r)

        def plan(part, ref):
            pads = {}
            for sh in part.shapes:
                if sh.layer in pad_layers and getattr(sh, "pin", "") and hasattr(sh, "bbox"):
                    pads[str(sh.pin)] = sh.bbox()
            unused = {p for p in pads if f"{ref}.{p}" not in names}
            drop_paths, ends = [], []
            for sh in part.shapes:
                if sh.layer != blay or not isinstance(sh, Path) or len(sh.points) < 2:
                    continue
                for e in (sh.points[0], sh.points[-1]):
                    hit = [p for p, (x0, y0, x1, y1) in pads.items()
                           if x0 - 1 <= e[0] <= x1 + 1 and y0 - 1 <= e[1] <= y1 + 1]
                    if hit and hit[0] in unused:
                        drop_paths.append((sh, hit[0]))
                        ends.extend([sh.points[0], sh.points[-1]])
                        break
            pins = sorted({p for _sh, p in drop_paths}, key=lambda v: (len(v), v))
            balls = []
            for sh in part.shapes:
                if sh.layer == blay and not isinstance(sh, Path) and hasattr(sh, "bbox"):
                    x0, y0, x1, y1 = sh.bbox()
                    c = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
                    if any(abs(c[0] - e[0]) <= 2 and abs(c[1] - e[1]) <= 2 for e in ends):
                        balls.append(sh)
            fingers = [sh for sh in part.shapes
                       if getattr(sh, "pin", "") and str(sh.pin) in pins
                       and sh.layer not in pad_layers and sh.layer != blay]
            return pins, [sh for sh, _p in drop_paths] + balls + fingers

        out = {"dies": {}, "cells": {}, "bonds": 0}
        todo = []
        for r in cell.srefs:
            part = lib.cells.get(r.cell_name)
            if not r.label or part is None:
                continue
            if not any(sh.layer == blay and isinstance(sh, Path) for sh in part.shapes):
                continue                       # this part carries no bonds of its own
            pins, gone = plan(part, r.label)
            if not pins:
                continue
            out["dies"][r.label] = pins
            out["bonds"] += len(pins)
            todo.append((r, pins))
        if dry_run or not todo:
            return out
        with self._batch():
            for r, pins in todo:
                own = f"{r.cell_name}_{r.label}" if not r.cell_name.endswith(f"_{r.label}") \
                    else r.cell_name
                shared = len(users.get(r.cell_name, [])) > 1 or own != r.cell_name
                if shared:
                    if own in lib.cells:
                        lib.delete_cell(own) if not any(
                            x.cell_name == own for c in lib.cells.values() for x in c.srefs) else None
                    if own not in lib.cells:
                        copy_ = _copy.deepcopy(lib.cells[r.cell_name])
                        copy_.name = own
                        lib.add_cell(copy_)
                    r.cell_name = own
                part = lib.cells[r.cell_name]
                _pins, gone = plan(part, r.label)
                ids = {id(sh) for sh in gone}
                part.shapes[:] = [sh for sh in part.shapes if id(sh) not in ids]
                out["cells"][r.label] = r.cell_name
        self._notify()
        return out

    def bond_landings(self, cell_name: Optional[str] = None, tol: float = 50.0) -> list:
        """Where the design's wire bonds land on the SUBSTRATE: one
        [x, y, pads] per landing point (bonds stitched on the same spot
        share it), ``pads`` the "REF.PIN" names of the die pads / header
        pins at the bonds' other ends. A bond with both ends on pads
        (die to header pin) has no landing; one with neither is a
        substrate jumper and lands twice, unnamed. Uses Setup > Wire
        Bond Rules (bond layer, die-pad layers)."""
        from ..core.netlist import _Flat, _flatten
        from ..io.techfile import load_bond_rules
        name = cell_name or self.current_cell_name
        cell = self.library.get_cell(name)
        br = load_bond_rules(self.library)
        try:
            blay = int(br["layer"])
        except Exception:
            return []
        if blay < 0:
            return []
        pad_layers = {int(l) for l in (br.get("die_pad_layers") or []) if int(l) >= 0}
        flat = _Flat()
        _flatten(self.library, cell, set(), pad_layers, blay, flat)
        pads = flat.pads
        geoms = [p[0] for p in pads]
        # a pad-layer shape enclosing smaller ones is a die outline, not a pad
        outline = [any(j != i and geoms[j].area < g.area and g.contains(geoms[j].centroid)
                       for j in range(len(geoms))) for i, g in enumerate(geoms)]

        def pad_at(land):
            hit = [i for i, g in enumerate(geoms) if not outline[i] and g.intersects(land)]
            if not hit:
                return None
            i = min(hit, key=lambda k: geoms[k].area)
            names = [f"{p.ref_des}.{p.pin}" for p in pads[i][2] if p.ref_des and p.pin]
            return names[0] if names else ""

        out = []

        def add(xy, pad):
            for L in out:
                if abs(L[0] - xy[0]) <= tol and abs(L[1] - xy[1]) <= tol:
                    if pad and pad not in L[2]:
                        L[2].append(pad)
                    return
            out.append([xy[0], xy[1], [pad] if pad else []])

        for (ga, xa), (gb, xb) in flat.bonds:
            pa, pb = pad_at(ga), pad_at(gb)
            if pa is not None and pb is None:
                add(xb, pa)
            elif pb is not None and pa is None:
                add(xa, pb)
            elif pa is None and pb is None:
                add(xa, "")
                add(xb, "")
        return out

    def _bond_nets(self, landings, names) -> list:
        """[(x, y, net)] for each landing whose pads have exactly one net."""
        out = []
        for x, y, pads in landings:
            nets = {names[p] for p in pads if p in names}
            if len(nets) == 1:
                out.append((x, y, nets.pop()))
        return out

    def _route_keepouts(self, cell, layers, R) -> list:
        """[(layer, [(x, y), ...])] areas no track may cross: every bare
        die placed in ``cell`` (a part with shapes on a Wire Bond Rules
        die-pad layer, grown by die_keepout -- an enclosing header
        package spanning most of the board excepted) and, with
        keepout_bodies, the body of every part with two or more pads on
        the routing layers (the hull of its pads and any resistor ink /
        glaze, less the pads grown by the clearance)."""
        from shapely.geometry import MultiPolygon
        from shapely.ops import unary_union
        from ..core.maskops import (_compose_transforms, _sref_point_transform,
                                    _transform_geom, shape_to_shapely)
        from ..io.techfile import load_bond_rules
        lib = self.library
        try:
            br = load_bond_rules(lib)
            pad_layers = {int(l) for l in (br.get("die_pad_layers") or [])}
            bond_layers = {int(br["layer"])}
        except Exception:
            pad_layers, bond_layers = set(), set()
        ink = set()
        try:
            rg = load_resistor_geometry_rules(lib)
            ink |= {int(v) for v in (rg.get("ink_layers") or {}).values()}
            for k in ("resistor_layer", "glaze_layer"):
                if isinstance(rg.get(k), int) and rg[k] >= 0:
                    ink.add(rg[k])
        except Exception:
            pass
        route = set(int(l) for l in layers)
        bx = lib.cell_bbox(cell.name)
        board = max((bx[2] - bx[0]) * (bx[3] - bx[1]), 1) if bx else 1
        gap = float(R.get("clearance", 100))
        die_m = float(R.get("die_keepout", 200))
        out = []

        def collect(name, tr, acc, depth=0):
            c = lib.cells.get(name)
            if c is None or depth > 32:
                return
            for sh in c.shapes:
                g = shape_to_shapely(sh)
                if g is None or g.is_empty:
                    continue
                g = _transform_geom(g, tr) if tr is not None else g
                acc.append((sh.layer, bool(getattr(sh, "pin", "")), g))
            for r in c.srefs:
                collect(r.cell_name, _compose_transforms(tr, _sref_point_transform(r)), acc, depth + 1)

        # a die part may carry its own wire bonds: they're not its body
        # (its keep-out would swallow the bond landings the tracks must reach)

        def polys(g):
            if g.is_empty:
                return []
            return list(g.geoms) if isinstance(g, MultiPolygon) else \
                [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon"]

        for r in cell.srefs:
            acc = []
            collect(r.cell_name, _sref_point_transform(r), acc)
            acc = [a for a in acc if a[0] not in bond_layers]
            if not acc:
                continue
            allg = unary_union([g for _l, _p, g in acc])
            b = allg.bounds
            if (b[2] - b[0]) * (b[3] - b[1]) > 0.4 * board:
                continue                          # enclosing package / header
            shapes = []
            # a bare die: pads on a die-pad layer and no PINS on the routing
            # layers (plain copper there -- a die-attach pad -- is part of it)
            if any(l in pad_layers for l, _p, _g in acc):
                # the die itself -- not the numbered bond fingers a die part
                # may carry round it, which are where the tracks must go
                body = [g for l, p, g in acc if not (p and l in route)]
                if body:
                    shapes = [unary_union(body).envelope.buffer(die_m, join_style=2)]
            elif R.get("keepout_bodies", True):
                pins = [g for l, p, g in acc if p and l in route]
                if len(pins) >= 2:
                    body = unary_union(pins + [g for l, _p, g in acc if l in ink]).convex_hull
                    rest = body.difference(unary_union(pins).buffer(gap, join_style=2))
                    shapes = [p for p in polys(rest) if p.area > 0.02 * body.area]
            for g in shapes:
                for p in polys(g):
                    pts = [(round(x), round(y)) for x, y in list(p.exterior.coords)[:-1]]
                    for lay in layers:
                        out.append((lay, pts))
        return out

    def _boundary_bbox(self, cell, layer):
        """Bounding box of ``layer``'s geometry in ``cell``, placed
        instances included (a substrate drawn as its own placed cell), or
        None."""
        from ..core.maskops import layer_to_shapely_flattened
        try:
            g = layer_to_shapely_flattened(self.library, cell, layer)
        except Exception:
            return None
        if g is None or g.is_empty:
            return None
        return tuple(float(v) for v in g.bounds)

    def export_dsn(self, path: str, cell_name: Optional[str] = None, **rules):
        """Write the current (or named) cell as a Specctra DSN for
        Freerouting: the two Route Layers, every numbered pad, the
        design's net names (set_net_names()/import_net_names()), the
        saved Freerouting rules (overridden by ``rules``) and the
        existing tracks/vias. Returns hlayout.io.specctra.DsnSummary."""
        from ..io.specctra import export_dsn
        from ..io.techfile import load_net_names
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        names = load_net_names(self.library)
        if not names:
            raise LayoutError("the design has no net names -- import them first "
                              "(NETNAMES IMPORT <file.csv> / Session.import_net_names())")
        R = self.freerouting_rules()
        R.update(rules)
        R = self._rules_with_drc(R)
        # the router keeps other nets clear of a via's whole dielectric
        # window, not just its pad
        R["signal_via_keep"] = max(self.via_spec(False, R)["window"], float(R["signal_via"]))
        R["power_via_keep"] = max(self.via_spec(True, R)["window"], float(R["power_via"]))
        try:
            diel = int(load_dielectric_rules(self.library)["layer"])
        except Exception:
            diel = -1
        band = None
        if diel >= 0:
            # Off the printed dielectric the two conductors aren't insulated
            # (a crossing would short) and a via has nothing to be a window
            # in: close the band outside the dielectric's boundary (its
            # largest datatype-0 shape) to the 2nd conductor, leaving it to
            # the 1st -- the layer the edge I/O pads are reached on.
            ins = [sh for sh in self.library.get_cell(name).shapes
                   if sh.layer == diel and getattr(sh, "datatype", 0) == 0 and hasattr(sh, "bbox")]
            if ins:
                b = max(ins, key=lambda sh: (sh.bbox()[2] - sh.bbox()[0]) * (sh.bbox()[3] - sh.bbox()[1]))
                band = (b.bbox(), self._route_layers()[1])
            # no boundary drawn (the biggest datatype-0 shape is then just a
            # via window): use the one generate_dielectric() works out
            ring = self._io_ring_box(self.library.get_cell(name))
            if ring is not None:
                rb = ring.bounds
                if band is None or (band[0][2] - band[0][0]) * (band[0][3] - band[0][1]) < \
                        0.5 * (rb[2] - rb[0]) * (rb[3] - rb[1]):
                    band = (rb, self._route_layers()[1])
        la, lb = self._route_layers()
        single = self._single_layer_route(R)
        cell = self.library.get_cell(name)
        # chip-and-wire: the substrate end of every wire bond is a bond
        # finger the tracks must reach, on the net of the die pad /
        # header pin at the bond's other end
        names = dict(names)
        finger = float(R.get("bond_finger", 400) or 0)
        flayer = self._finger_layer(R)
        bond_pins = []
        if finger > 0:
            # a die part that carries its own numbered bond fingers already
            # gives the router a pad at those landings: no second pin there
            from shapely.geometry import Point as _Pt, Polygon as _Poly
            from ..io.specctra import _collect_pins
            own = [_Poly(pts) for _r, _p, _l, pts in
                   _collect_pins(self.library, cell, {la, lb}) if len(pts) >= 3]
            k = 0
            for x, y, net in self._bond_nets(self.bond_landings(name), names):
                if any(g.covers(_Pt(x, y)) for g in own):
                    continue
                k += 1
                bond_pins.append((f"B{k}", x, y, finger, flayer))
                names[f"BOND.B{k}"] = net
        keepouts = self._route_keepouts(cell, (la,) if single else (la, lb), R)
        return export_dsn(self.library, name, path, layers=(la, lb), rules=R,
                          net_names=names, boundary_layer=self._boundary_layer(),
                          boundary_bbox=(self._boundary_bbox(cell, self._boundary_layer())
                                         if self._boundary_layer() is not None else None),
                          dielectric_layer=diel if (diel >= 0 and not single) else None,
                          edge_band=None if single else band,
                          write_wiring=not R.get("remove_existing", False),
                          bond_pins=bond_pins, keepouts=keepouts, single_layer=single)

    def import_ses(self, path: str, cell_name: Optional[str] = None, **rules) -> dict:
        """Bring a Freerouting session file's routing into the cell as one
        undo step: each wire becomes a round-capped Path on its conductor
        layer; each via a drop_via() with its dielectric window (signal or
        power size from the rules); the dielectric fill is regenerated.

        With rules keep_existing=True (the default) wires/vias that are
        already in the design are skipped; with keep_existing=False every
        existing track and via on the two routing layers is replaced by
        the session's routing. Returns counts, plus the net-completion
        check: {"wires", "vias", "skipped", "nets", "complete",
        "unrouted": [net names]}."""
        from shapely.geometry import LineString, Point
        from shapely.ops import unary_union
        from ..core.shapes import Path as _Path
        from ..io.specctra import VIA_PWR, find_vias, layer_name, parse_ses
        from ..io.techfile import load_net_names

        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        cell = self.library.get_cell(name)
        la, lb = self._route_layers()
        R = self.freerouting_rules()
        R.update(rules)
        R = self._rules_with_drc(R)
        power = {n.upper() for n in R.get("power_nets") or []}
        ses = parse_ses(path)
        lmap = {layer_name(la): la, layer_name(lb): lb}
        try:
            diel = int(load_dielectric_rules(self.library)["layer"])
        except Exception:
            diel = -1

        keep = bool(R.get("keep_existing", True)) and not R.get("remove_existing", False)
        stats = {"wires": 0, "vias": 0, "skipped": 0}
        with self._batch():
            if not keep:
                # replace: drop existing routing paths and drop_via() vias --
                # and the routing's leftovers: a via pad left on one layer
                # only (its other half gone), or a bond finger this import
                # lays again, which no longer pair up as a via
                sites = {(x, y) for x, y, _w in find_vias(cell, (la, lb))}
                route_sizes = {round(float(R.get(k) or 0)) for k in
                               ("signal_via", "power_via", "bond_finger")} - {0}
                def _is_via_part(s):
                    if s.layer not in (la, lb, diel) or getattr(s, "pin", "") or not hasattr(s, "bbox"):
                        return False
                    x0, y0, x1, y1 = s.bbox()
                    c = (round((x0 + x1) / 2), round((y0 + y1) / 2))
                    if c in sites and (x1 - x0) < 5000 and getattr(s, "datatype", 0) == 0:
                        return True
                    w, h = x1 - x0, y1 - y0
                    return (s.layer in (la, lb) and isinstance(s, Rectangle) and abs(w - h) <= 2
                            and round(w) in route_sizes)
                cell.shapes[:] = [s for s in cell.shapes
                                  if not ((isinstance(s, _Path) and s.layer in (la, lb))
                                          or _is_via_part(s))]
                # placed via-cell instances go too
                from ..io.specctra import via_cell_sites
                gone = {id(r) for _x, _y, _w, r in via_cell_sites(self.library, cell, (la, lb))}
                cell.srefs[:] = [r for r in cell.srefs if id(r) not in gone]
            existing = {}
            for lay in (la, lb):
                geoms = []
                for s in cell.shapes:
                    if s.layer != lay:
                        continue
                    if isinstance(s, _Path) and len(s.points) >= 2:
                        geoms.append(LineString(s.points).buffer(s.width / 2.0))
                existing[lay] = unary_union(geoms).buffer(2) if geoms else None
            have_vias = {(x, y) for x, y, _w in find_vias(cell, (la, lb))}

            for _net, lay_name, width, pts in ses.wires:
                lay = lmap.get(lay_name)
                if lay is None:
                    stats["skipped"] += 1
                    continue
                pts = [(int(round(x)), int(round(y))) for x, y in pts]
                pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
                if len(pts) < 2:
                    continue
                w = int(round(width))
                # Freerouting necks a track down where it squeezes into a
                # pad; the design's minimum width still applies, so put it
                # back (Tools > DRC then shows any clearance it costs)
                cls_w = int(round(R["power_width"] if str(_net).upper() in power
                                  else R["signal_width"]))
                if w < cls_w:
                    w = cls_w
                    stats["widened"] = stats.get("widened", 0) + 1
                if keep and existing[lay] is not None and \
                        existing[lay].contains(LineString(pts).buffer(max(w / 2.0 - 3, 1))):
                    stats["skipped"] += 1
                    continue
                cell.add_path(lay, pts, w, cap="round")
                stats["wires"] += 1
            for _net, ps, x, y in ses.vias:
                x, y = int(round(x)), int(round(y))
                if keep and any(abs(x - vx) <= 2 and abs(y - vy) <= 2 for vx, vy in have_vias):
                    stats["skipped"] += 1
                    continue
                pwr = ps == VIA_PWR
                spec = self.via_spec(pwr, R)
                size, window = spec["pad"], spec["window"]
                h = size / 2.0
                cell.add_rectangle(la, x - h, y - h, x + h, y + h)
                cell.add_rectangle(lb, x - h, y - h, x + h, y + h)
                if diel >= 0 and window:
                    hw = window / 2.0
                    cell.add_rectangle(diel, x - hw, y - hw, x + hw, y + hw)
                have_vias.add((x, y))
                stats["vias"] += 1
            # chip-and-wire: lay a bond finger wherever a wire bond lands
            # on the substrate and the copper doesn't already give it one
            finger = float(R.get("bond_finger", 400) or 0)
            stats["fingers"] = 0
            if finger > 0:
                from shapely.geometry import box as _box
                from ..core.maskops import layer_to_shapely_flattened
                flayer = self._finger_layer(R)
                cu = layer_to_shapely_flattened(self.library, cell, flayer)
                h = finger / 2.0
                # a die part carrying its own numbered bond fingers already
                # has one at its landings, whatever its size
                from shapely.geometry import Point as _Pt, Polygon as _Poly
                from ..io.specctra import _collect_pins
                own = [_Poly(pts) for _r, _p, _l, pts in
                       _collect_pins(self.library, cell, {la, lb}) if len(pts) >= 3]
                for x, y, _net in self._bond_nets(self.bond_landings(name), load_net_names(self.library)):
                    sq = _box(x - h, y - h, x + h, y + h)
                    if cu is not None and not cu.is_empty and cu.buffer(1).contains(sq):
                        continue
                    if any(g.covers(_Pt(x, y)) for g in own):
                        continue
                    cell.add_rectangle(flayer, round(x - h), round(y - h), round(x + h), round(y + h))
                    stats["fingers"] += 1
            if diel >= 0 and not self._single_layer_route(R) and \
                    any(s.layer == diel and getattr(s, "datatype", 0) == 0 for s in cell.shapes):
                try:
                    self.generate_dielectric(cell_name=name)
                except Exception:
                    pass
        self._notify()

        # completion check, stack-aware (vias join the layers, nothing else)
        names = load_net_names(self.library)
        tracer = self.net_tracer(cell_name=name)
        by_net, count = {}, {}
        for pad, net in names.items():
            if pad in tracer.pin_node:
                by_net.setdefault(net, set()).add(tracer.dsu.find(tracer.pin_node[pad]))
                count[net] = count.get(net, 0) + 1
        # wire-bond landings count as pins of their net: a die pad is only
        # reached through the copper under its bond's substrate end
        for x, y, net in self._bond_nets(self.bond_landings(name), names):
            node = tracer.node_at(x, y, prefer_layer=self._finger_layer(R),
                                  tol=float(R.get("bond_finger", 400)) / 2.0)
            by_net.setdefault(net, set()).add(tracer.dsu.find(node) if node is not None
                                              else ("open", x, y))
            count[net] = count.get(net, 0) + 1
        multi = {n: r for n, r in by_net.items() if count.get(n, 0) > 1}
        unrouted = sorted(n for n, r in multi.items() if len(r) > 1)
        stats.update(nets=len(multi), complete=len(multi) - len(unrouted), unrouted=unrouted)
        return stats

    def freeroute(self, work_dir: Optional[str] = None, cell_name: Optional[str] = None,
                  router: Optional[str] = None, timeout: Optional[float] = None,
                  on_line=None, cancel=None, **rules) -> dict:
        """Export a DSN, run Freerouting headless on it, import the
        result (import_ses()). ``router`` = the Freerouting launcher or
        .jar (default: Setup > Freerouting..., else a standard install
        location). Files go to ``work_dir`` (default: a ``freerouting``
        folder next to the design, else the temp folder) as
        <cell>.dsn/.ses. Blocks until done -- the GUI runs the router
        itself on a worker thread (see run_freerouting()).

        Two-layer routes follow the rules' ``layer_directions`` (each
        conductor kept to one track direction). With ``via_optimize`` the
        imported routing then gets optimize_vias() -- which takes out
        every via it safely can -- as part of the same Autoroute.
        Returns import_ses()'s dict plus "dsn", "ses" and "log" (last
        output), and with the via pass "vias_before" / "vias_after"."""
        import tempfile
        from ..io import app_settings
        from ..io.specctra import find_freerouting, run_freerouting
        name = cell_name or self.current_cell_name
        exe = find_freerouting(router or app_settings.get_setting(app_settings.FREEROUTING_PATH_KEY))
        if not exe:
            raise LayoutError("Freerouting not found -- install it (freerouting.app) and set "
                              "its path in Setup > Freerouting...")
        work_dir = work_dir or os.path.join(tempfile.gettempdir(), "hlayout_freerouting")
        os.makedirs(work_dir, exist_ok=True)
        dsn = os.path.join(work_dir, f"{name}.dsn")
        ses = os.path.join(work_dir, f"{name}.ses")
        R = self.freerouting_rules()
        R.update(rules)
        self.export_dsn(dsn, cell_name=name, **R)
        cmd = self.freeroute_command(exe, dsn, ses, R,
                                     java=app_settings.get_setting(app_settings.JAVA_PATH_KEY))
        code, tail = run_freerouting(cmd, ses, timeout=timeout, on_line=on_line, cancel=cancel)
        if not os.path.exists(ses):
            raise LayoutError("Freerouting produced no session file (exit code %s):\n%s"
                              % (code, "\n".join(tail[-12:])))
        out = self.import_ses(ses, cell_name=name, **R)
        out.update(dsn=dsn, ses=ses, log=tail)
        if R.get("via_optimize") and not self._single_layer_route(R):
            vo = self.optimize_vias(cell_name=name, **R)
            out.update(vias_before=vo["before"], vias_after=vo["after"])
        if R.get("shorten_tracks", True):
            sh = self.shorten_tracks(cell_name=name, **R)
            out.update(shortened=sh["tracks"], shortened_by=sh["before"] - sh["after"])
        return out

    @staticmethod
    def freeroute_command(exe: str, dsn: str, ses: str, R: dict,
                          java: Optional[str] = None) -> list:
        """The Freerouting command line for the rules ``R``."""
        from ..io.specctra import EXCLUDED_CLASS, freerouting_command
        return freerouting_command(exe, dsn, ses, passes=int(R["passes"]),
                                   optimize_passes=int(R.get("optimize_passes", 100)),
                                   optimize_threshold=float(R.get("optimize_threshold", 2.5)),
                                   exclude_classes=[EXCLUDED_CLASS] if R.get("exclude_nets") else [],
                                   java=java)

    def _dielectric_area(self, cell, diel):
        """Where the dielectric is printed -- the area the 2nd conductor
        (and every via) must stay on: the generated fill with its via
        windows, as one region; without a fill, the fill boundary
        generate_dielectric() would use (an enclosing datatype-0 outline,
        else the I/O-ring area). None when there is no dielectric."""
        from shapely.geometry import Polygon as _SP
        from shapely.geometry import box as _box
        from shapely.ops import unary_union
        if diel is None or diel < 0:
            return None
        dt = self.DIELECTRIC_FILL_DATATYPE
        fill = [_SP(sh.points) for sh in cell.shapes if sh.layer == diel
                and getattr(sh, "datatype", 0) == dt and len(getattr(sh, "points", ())) >= 3]
        ins = [_box(*sh.bbox()) for sh in cell.shapes if sh.layer == diel
               and getattr(sh, "datatype", 0) == 0 and hasattr(sh, "bbox")]
        if fill:
            u = unary_union(fill + ins)
            # the windows are openings INSIDE the sheet: close them up
            from shapely.geometry import MultiPolygon
            polys = list(u.geoms) if isinstance(u, MultiPolygon) else [u]
            return unary_union([_SP(p.exterior.coords) for p in polys if p.geom_type == "Polygon"])
        if ins:
            big = max(ins, key=lambda g: g.area)
            rest = [g for g in ins if g is not big]
            if rest and big.buffer(1).contains(unary_union(rest)):
                return big
        ring = self._io_ring_box(cell)
        return ring if ring is not None else self._substrate_box(cell)

    def shorten_tracks(self, cell_name: Optional[str] = None, progress=None, **rules) -> dict:
        """Straighten detours and pull corners tight: every routed track
        keeps its two ends and is re-laid on its own layer the shortest
        clear way (straight, an L, or a maze route round the obstacles)
        when that is at least 0.1 mm / 3 % shorter and keeps the
        clearance (Freerouting rules tightened by the DRC) from every
        other net, stays out of the die / part-body keep-outs, on the
        substrate, and -- on the 2nd conductor -- on the printed
        dielectric. A track something else joins part-way along is left
        alone. Works with nets still unrouted. One undo step. Returns
        {"tracks": shortened, "before": um, "after": um}. See
        hlayout.core.shorten."""
        from shapely.geometry import Polygon as _SP
        from shapely.geometry import box as _box
        from ..core.shorten import shorten_tracks as _short
        from ..io.techfile import load_net_names
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        cell = self.library.get_cell(name)
        la, lb = self._route_layers()
        R = self.freerouting_rules()
        R.update(rules)
        R = self._rules_with_drc(R)
        keep = {}
        for lay, pts in self._route_keepouts(cell, (la, lb), R):
            if len(pts) >= 3:
                keep.setdefault(int(lay), []).append(_SP(pts))
        keepin = self._substrate_box(cell)
        try:
            diel = int(load_dielectric_rules(self.library)["layer"])
        except Exception:
            diel = -1
        keepin_b = self._dielectric_area(cell, diel)
        vspec = self.via_spec(False, R)
        margin = (vspec["window"] - vspec["pad"]) / 2.0 if vspec["window"] else 0.0
        fill_dt = self.DIELECTRIC_FILL_DATATYPE

        def make_via(x, y, size):
            h = size / 2.0
            out = [cell.add_rectangle(la, x - h, y - h, x + h, y + h),
                   cell.add_rectangle(lb, x - h, y - h, x + h, y + h)]
            if diel >= 0 and vspec["window"]:
                hw = h + margin
                out.append(cell.add_rectangle(diel, x - hw, y - hw, x + hw, y + hw))
            return out

        def remove_via(site):
            x, y, _w, kind, obj = site
            if kind == "cell":
                if obj in cell.srefs:
                    cell.srefs.remove(obj)
                return
            lays = {la, lb} | ({diel} if diel >= 0 else set())
            doomed = set()
            for sh in cell.shapes:
                if sh.layer not in lays or getattr(sh, "pin", "") or isinstance(sh, _PathT) \
                        or not hasattr(sh, "bbox") or getattr(sh, "datatype", 0) != 0:
                    continue
                b = sh.bbox()
                bw, bh = b[2] - b[0], b[3] - b[1]
                if 0 < bw <= 5000 and abs(bw - bh) <= max(2, 0.02 * bw) and \
                        abs((b[0] + b[2]) / 2 - x) <= 1 and abs((b[1] + b[3]) / 2 - y) <= 1:
                    doomed.add(id(sh))
            cell.shapes[:] = [sh for sh in cell.shapes if id(sh) not in doomed]

        from ..core.shapes import Path as _PathT
        before = {frozenset(x["nets"]) for x in self.find_shorts(cell_name=name)}
        keep_shapes, keep_srefs = list(cell.shapes), list(cell.srefs)
        with self._batch():
            res = _short(self.library, cell, (la, lb), float(R.get("clearance", 100)),
                         keepouts=keep, keepin=keepin, keepin_b=keepin_b,
                         dielectric_layer=diel if diel >= 0 else None,
                         fill_datatype=fill_dt, via_size=vspec["pad"], via_margin=margin,
                         make_via=make_via, remove_via=remove_via, progress=progress,
                         pad_nets=load_net_names(self.library) or None)
            if res["tracks"] and diel >= 0 and any(
                    s.layer == diel and getattr(s, "datatype", 0) == fill_dt for s in cell.shapes):
                try:
                    self.generate_dielectric(cell_name=name)
                except Exception:
                    pass
            # safety net: if the re-laid routing joined two named nets
            # anywhere, put everything back as it was
            res["shorts"] = []
            if res["tracks"]:
                new = [x for x in self.find_shorts(cell_name=name)
                       if frozenset(x["nets"]) not in before]
                if new:
                    cell.shapes[:] = keep_shapes
                    cell.srefs[:] = keep_srefs
                    res.update(tracks=0, before=0.0, after=0.0, vias_before=0, vias_after=0,
                               shorts=new, undone=True)
        self._notify()
        return res

    def via_spec(self, power: bool = False, rules: Optional[dict] = None) -> dict:
        """THE via of this design -- the one definition every via is made
        from (Freerouting import, rip-up / shortening, the Route tool's
        via cell, Fix Vias): {"pad": the conductor pad (signal_via /
        power_via of the Autoroute rules), "window": the dielectric
        window = pad + 2 x via_window_margin (Dielectric rules) -- larger
        than the pad, and drawn at its printed size (generate_dielectric()
        doesn't grow it) -- and "layer": the dielectric layer (-1 none)}."""
        R = rules if rules is not None else self.freerouting_rules()
        dr = load_dielectric_rules(self.library)
        pad = float(R["power_via"] if power else R["signal_via"])
        layer = int(dr.get("layer", -1))
        margin = float(dr.get("via_window_margin", 100))
        return {"pad": pad, "window": pad + 2.0 * margin if layer >= 0 else 0.0, "layer": layer}

    def fix_vias(self, cell_name: Optional[str] = None) -> dict:
        """Redraw every via of the cell to the design's one via
        definition (via_spec()): pads on both route layers and the
        dielectric window, power-net vias (Autoroute power nets) at the
        power size, the rest at the signal size; a via CELL the cell
        places (the Route tool's) is rebuilt to the signal via in place.
        The dielectric fill is redone. One undo step. Returns
        {"vias": flat vias redrawn, "via_cells": via cells rebuilt}."""
        from ..io.specctra import find_vias, via_cell_sites
        from ..io.techfile import load_net_names
        name = cell_name or self.current_cell_name
        cell = self.library.get_cell(name)
        la, lb = self._route_layers()
        R = self.freerouting_rules()
        sig, pwr = self.via_spec(False, R), self.via_spec(True, R)
        diel = sig["layer"]
        power = {n.upper() for n in R.get("power_nets", [])}
        names = load_net_names(self.library)
        tr = self.net_tracer(cell_name=name)
        from shapely.geometry import Point as _Pt

        def net_at(x, y):
            hit = [j for j in tr._query(_Pt(x, y)) if tr.nodes[j][0] == la
                   and tr._geoms[j].distance(_Pt(x, y)) <= 1]
            if not hit:
                return None
            root = tr.dsu.find(hit[0])
            for p, n in names.items():
                if p in tr.pin_node and tr.dsu.find(tr.pin_node[p]) == root:
                    return n
            return None

        flat = find_vias(cell, (la, lb))
        lays = {la, lb} | ({diel} if diel >= 0 else set())
        n_flat = 0
        with self._batch():
            for x, y, _w in flat:
                n = net_at(x, y)
                spec = pwr if (n and n.upper() in power) else sig
                cell.shapes[:] = [sh for sh in cell.shapes if not (
                    sh.layer in lays and not getattr(sh, "pin", "") and hasattr(sh, "bbox")
                    and not hasattr(sh, "width") and getattr(sh, "datatype", 0) == 0
                    and abs((sh.bbox()[0] + sh.bbox()[2]) / 2 - x) <= 1
                    and abs((sh.bbox()[1] + sh.bbox()[3]) / 2 - y) <= 1
                    and (sh.bbox()[2] - sh.bbox()[0]) <= 5000)]
                h = spec["pad"] / 2.0
                cell.add_rectangle(la, x - h, y - h, x + h, y + h)
                cell.add_rectangle(lb, x - h, y - h, x + h, y + h)
                if diel >= 0 and spec["window"]:
                    hw = spec["window"] / 2.0
                    cell.add_rectangle(diel, x - hw, y - hw, x + hw, y + hw)
                n_flat += 1
            rebuilt = set()
            # the via cells placed here, and the one the Route tool drops
            via_cells = [ref.cell_name for _x, _y, _w, ref in
                         via_cell_sites(self.library, cell, (la, lb))]
            rv = load_route_rules(self.library).get("via_cell") or ""
            if rv and rv in self.library.cells:
                via_cells.append(rv)
            for vname in via_cells:
                vc = self.library.cells.get(vname)
                if vc is None or vname in rebuilt:
                    continue
                vlays = {sh.layer for sh in vc.shapes} | {la, lb}
                h = sig["pad"] / 2.0
                vc.shapes[:] = [sh for sh in vc.shapes if sh.layer not in (la, lb, diel)]
                for l in sorted(vlays & {la, lb}):
                    vc.add_rectangle(l, -h, -h, h, h)
                if diel >= 0 and sig["window"]:
                    hw = sig["window"] / 2.0
                    vc.add_rectangle(diel, -hw, -hw, hw, hw)
                rebuilt.add(vname)
            if diel >= 0 and any(sh.layer == diel and getattr(sh, "datatype", 0) ==
                                 self.DIELECTRIC_FILL_DATATYPE for sh in cell.shapes):
                try:
                    self.generate_dielectric(cell_name=name)
                except Exception:
                    pass
        self._notify()
        return {"vias": n_flat, "via_cells": len(rebuilt)}

    def assign_io_pads(self, cell_name: Optional[str] = None, apply: bool = True,
                       csv_path: Optional[str] = None, pad_w: Optional[float] = None,
                       pad_l: Optional[float] = None,
                       bond_inset: Optional[float] = None) -> dict:
        """Optimise the module pinout for routing: put each I/O net on the
        I/O pad (header pin) that gives the shortest, uncrossed connection
        to the copper it must reach, and (``apply``) draw the pads at the
        header-bond landings and rename the nets IO.<pin> to match. The
        pinout is written to ``csv_path`` (default: io_pinout.csv next to
        the design). Pad size and the bond landing's inset from the pad's
        outer end default to the design's I/O pad rules (Setup > Technology
        Properties, I/O Pads tab; techfile.load_io_pad_rules). The
        dielectric fill is redone if there is one. One undo step. Returns the plan: {"sites": [{"pin", "net", "was", ...}],
        "before", "after" (um, pad-to-nearest-copper totals), "unreached",
        "csv"}. See hlayout.scripting.iopads."""
        from . import iopads
        from ..io.techfile import load_io_pad_rules
        pr = load_io_pad_rules(self.library)
        pad_w = float(pr["width"] if pad_w is None else pad_w)
        pad_l = float(pr["length"] if pad_l is None else pad_l)
        bond_inset = float(pr["bond_inset"] if bond_inset is None else bond_inset)
        if pad_w <= 0 or pad_l <= 0 or bond_inset < 0:
            raise LayoutError("I/O pad width and length must be positive, the bond inset 0 or more")
        p = iopads.plan(self, cell_name=cell_name)
        if not apply:
            return p
        if csv_path is None and getattr(self, "current_path", None):
            csv_path = os.path.join(os.path.dirname(self.current_path), "io_pinout.csv")
        with self._batch():
            r = iopads.apply(self, p, pad_w=pad_w, pad_l=pad_l, csv_path=csv_path,
                             bond_inset=bond_inset)
            cell = self.library.get_cell(p["cell"])
            try:
                diel = int(load_dielectric_rules(self.library)["layer"])
            except Exception:
                diel = -1
            if diel >= 0 and any(s.layer == diel and getattr(s, "datatype", 0) ==
                                 self.DIELECTRIC_FILL_DATATYPE for s in cell.shapes):
                try:
                    self.generate_dielectric(cell_name=p["cell"])
                except Exception:
                    pass
        p["csv"] = r["csv"]
        return p

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
        try:
            diel = int(load_dielectric_rules(self.library)["layer"])
        except Exception:
            diel = -1
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

    def optimize_vias(self, cell_name: Optional[str] = None, progress=None, **rules) -> dict:
        """Take out every via of the routed cell that can safely go: the
        track(s) on one side of it move onto the other conductor and the
        via -- both pads and its dielectric window -- is deleted, when the
        moved copper still reaches its own net at the far end, keeps the
        clearance (Freerouting rules, tightened by the design's DRC) from
        every other net, stays on the printed dielectric if it moves onto
        the 2nd conductor, and is no longer than ``via_optimize_max_flip``
        (um, 0 = any length). The dielectric fill is regenerated. One undo
        step. Returns {"before", "after", "removed": [(x, y)], "moved"}.
        See hlayout.core.viaopt."""
        from ..core.viaopt import optimize_vias as _opt
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        cell = self.library.get_cell(name)
        la, lb = self._route_layers()
        R = self.freerouting_rules()
        R.update(rules)
        R = self._rules_with_drc(R)
        try:
            diel = int(load_dielectric_rules(self.library)["layer"])
        except Exception:
            diel = -1
        area = self._dielectric_area(cell, diel)
        with self._batch():
            res = _opt(self.library, cell, (la, lb), float(R.get("clearance", 100)),
                       dielectric_layer=diel if diel >= 0 else None, dielectric_area=area,
                       fill_datatype=self.DIELECTRIC_FILL_DATATYPE,
                       max_flip=float(R.get("via_optimize_max_flip", 0) or 0),
                       progress=progress)
            if res["removed"] and diel >= 0 and \
                    any(s.layer == diel and getattr(s, "datatype", 0) == 0 for s in cell.shapes):
                try:
                    self.generate_dielectric(cell_name=name)
                except Exception:
                    pass
        self._notify()
        return res

    def rip_up_between(self, a, b, cell_name: Optional[str] = None, tol: float = 0.0) -> dict:
        """Rip up the routing between two points on one net: the shortest
        run of track from ``a`` to ``b`` (DBU points on tracks of either
        route layer) -- along the tracks, through T-junctions and vias --
        is cut out, the tracks cut at the two points; a via on the run
        goes too once nothing is left on one of its sides. ``tol`` (DBU)
        lets a point just beside a thin track still pick it. The
        dielectric fill is regenerated if the design has one. One undo
        step. Returns {"a": (x, y, layer), "b": (x, y, layer), "length",
        "tracks", "vias": [(x, y)], "net"} -- ``a``/``b`` are where the
        cut ends are, ready to be routed again. See hlayout.core.ripup."""
        from ..core.ripup import rip_up as _rip
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        cell = self.library.get_cell(name)
        la, lb = self._route_layers()
        net = None
        try:
            from ..io.techfile import load_net_names
            tr = self.net_tracer(cell_name=name).trace_point(a[0], a[1], tol=tol)
            if tr is not None:
                tr.name_from(load_net_names(self.library))
                net = tr.name if tr.name not in (None, "unnamed") else None
        except Exception:
            net = None
        with self._batch():
            res = _rip(self.library, cell, (la, lb), a, b, tol=tol)
            if res is None:
                raise LayoutError(
                    f"no track joins ({a[0] / 1000:.3f}, {a[1] / 1000:.3f}) and "
                    f"({b[0] / 1000:.3f}, {b[1] / 1000:.3f}) mm -- pick two points on tracks "
                    f"of one net (layer {la} or {lb})")
            try:
                diel = int(load_dielectric_rules(self.library)["layer"])
            except Exception:
                diel = -1
            if res["vias"] and diel >= 0 and any(
                    s.layer == diel and getattr(s, "datatype", 0) == self.DIELECTRIC_FILL_DATATYPE
                    for s in cell.shapes):
                try:
                    self.generate_dielectric(cell_name=name)
                except Exception:
                    pass
        res["net"] = net
        self._notify()
        return res

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
        try:
            diel = int(load_dielectric_rules(self.library)["layer"])
        except Exception:
            diel = -1
        if diel in layers:
            diel = -1
        return NetTracer(self.library, self.library.get_cell(name), layers,
                         dielectric_layer=diel if diel >= 0 else None,
                         fill_datatype=self.DIELECTRIC_FILL_DATATYPE)

    def highlight_net(self, target, layers=None, cell_name: Optional[str] = None,
                      prefer_layer=None, tol: float = 0.0):
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
        self.net_highlight = tr
        self._notify()
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

    def find_via(self, point, cell_name: Optional[str] = None,
                 tol: Optional[float] = None, layers=None):
        """Locate the via under/nearest ``point`` (an (x, y) DBU
        coordinate) and return ``(centre, [shapes])``, or ``None``.

        A via is what drop_via() draws: two or more square/round pads on
        different layers sharing one centre, plus (optionally) the
        dielectric window on a third layer at that same centre. Matched
        here as every non-pin Rectangle/Polygon whose bbox is roughly
        square, whose centre coincides (within 1 DBU) with the others',
        and that spans at least two layers. ``point`` must lie inside
        one of the via's shapes, or within ``tol`` (default 0) of its
        centre. ``layers`` optionally restricts which layers count
        (e.g. the two conductor layers + the dielectric)."""
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        cell = self.library.get_cell(name)
        allowed = None
        if layers is not None:
            allowed = {self.library.layers.resolve(l) for l in layers}
        px, py = float(point[0]), float(point[1])
        reach = float(tol or 0)

        groups = {}
        for s in cell.shapes:
            if not isinstance(s, (Rectangle, Polygon)) or getattr(s, "pin", ""):
                continue
            if allowed is not None and s.layer not in allowed:
                continue
            x1, y1, x2, y2 = s.bbox()
            w, h = x2 - x1, y2 - y1
            if w <= 0 or h <= 0 or abs(w - h) > max(2, 0.02 * max(w, h)):
                continue
            key = (round((x1 + x2) / 2), round((y1 + y2) / 2))
            groups.setdefault(key, []).append(s)

        best, best_d = None, None
        for (cx, cy), shapes in groups.items():
            if len({s.layer for s in shapes}) < 2:
                continue
            d = math.hypot(px - cx, py - cy)
            inside = any(x1 <= px <= x2 and y1 <= py <= y2
                         for x1, y1, x2, y2 in (s.bbox() for s in shapes))
            if not inside and d > reach:
                continue
            if best is None or d < best_d:
                best, best_d = ((cx, cy), shapes), d
        return best

    def delete_via(self, point, cell_name: Optional[str] = None,
                   tol: Optional[float] = None, layers=None):
        """Remove the via at/nearest ``point`` -- both conductor pads and
        the dielectric window (see find_via()). Tracks that end on it are
        left alone. Raises LayoutError if no via is there. One undo step;
        returns ``(centre, [layers removed])``."""
        name = cell_name or self.current_cell_name
        found = self.find_via(point, cell_name=name, tol=tol, layers=layers)
        if found is None:
            raise LayoutError(f"no via at {tuple(point)}")
        centre, shapes = found
        doomed = {id(s) for s in shapes}
        cell = self.library.get_cell(name)
        with self._batch():
            cell.shapes[:] = [s for s in cell.shapes if id(s) not in doomed]
        self._notify()
        return centre, sorted(s.layer for s in shapes)

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
        from shapely.ops import unary_union

        obstacles = layer_to_shapely_flattened(self.library, cell, r_layer)
        inflate = width / 2.0 + clr
        # The copper an endpoint lands on must not seal that endpoint
        # off: drop the whole connected piece of copper under (or within
        # half a trace width of) each endpoint -- the pad plus whatever
        # is already joined to it, i.e. the same net. Everything else
        # stays an obstacle however close to a pin it sits. (This used
        # to clear a square halo around each endpoint instead, which let
        # a trace run straight through unrelated copper next to a pin.)
        if not obstacles.is_empty:
            reach = max(width / 2.0, 1.0)
            ends = (_spoint(ax, ay), _spoint(bx, by))
            pieces = list(getattr(obstacles, "geoms", [obstacles]))
            obstacles = unary_union([g for g in pieces
                                     if not any(g.distance(e) <= reach for e in ends)])
        # Also drop the two selected pin shapes' own copper entirely.
        own = [shape_to_shapely(s) for s in (sa, sb) if s is not None]
        own = [g for g in own if g is not None and not g.is_empty]
        if own and not obstacles.is_empty:
            obstacles = obstacles.difference(unary_union(own))
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
