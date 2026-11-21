# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Drawings and tables: resistor / pin-function / bond tables, the drawing
sheet, the assembly drawing and the mask-op top views.

Part of hlayout.scripting.api.Session (a mixin): these methods are
Session's own -- call them on a Session as always."""

from typing import List, Optional

from ..core.errors import LayoutError
from ..core.maskops import (
    hidden_line_top_view,
    layer_to_shapely,
    layer_to_shapely_flattened,
    shapely_lines_to_paths,
)
from ..core.units import format_rkm, mm_to_dbu
from ..io.techfile import dielectric_layer_number, load_component_models


# ISO 216 sheet sizes in mm, stored as (short_edge, long_edge) --
# portrait uses (short, long) as (width, height), landscape swaps them.
# Limited to A4/A3: hlayout.core.units.MAX_SIZE_MM caps the database at
# 500 x 500 mm, and A2 (594 mm) and larger don't fit inside that.
SHEET_SIZES_MM = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
}


def _matched_component_footprint(library, cell_name, component_models, role_layers=frozenset(),
                                  excluded_layers=frozenset(), split_packages=False,
                                  pad_layers=frozenset(), bond_layers=frozenset(),
                                  body_skip_layers=frozenset()):
    """The union, in world DBU coordinates, of every placed instance's
    OWN shapes (every layer -- pads included, not just an outline)
    whose cell is a recognized real component: an explicit
    component_models entry (manual or already auto-matched), a name
    carrying a known EIA size code (component_match.match_passive_by_name()),
    a footprint matching a known gull-wing package's pin pattern
    (component_match.match_gullwing_by_footprint()), or -- for a "bare"
    part identified by neither, e.g. a wire-bonded die built with
    Session.create_chip_cell() (Create > Chip Wizard...) -- a cell that
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
        if (c is not None and bond_layers
                and any(sh.layer in bond_layers for sh in c.shapes)
                and any(sh.layer not in role_layers and sh.layer not in excluded_layers
                        for sh in c.shapes)):
            # a chip-and-wire die part (Auto Place's DIE_* cells): the die
            # body / pads / outline drawn flat beside its own wire bonds and
            # bond fingers -- so neither "all non-role" nor "has children"
            # holds, but it is a solid die sitting on the conductor. Its
            # bonds and fingers stay out of the body (pad_layers).
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
                        if shape.layer in pad_layers or shape.layer in body_skip_layers:
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


class DrawingsMixin:
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
        if float(st.get("bond_width_um", 0)) > 0:
            kwargs["bond_width"] = float(st["bond_width_um"])
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
        "bond", "epoxy", "substrate" or "other". Roles come from the Setup rules
        (resistor geometry, route layers, wire bonds, dielectric fill);
        "substrate"/"other" are layers with drawn, visible, filled
        geometry not otherwise claimed. Components (placed parts) are not
        a layer role -- they are always composited on top."""
        from ..io.techfile import load_bond_rules, load_resistor_geometry_rules, load_route_rules
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
        d = dielectric_layer_number(self.library)
        if d >= 0:
            conductor_layers.add(d)

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
        # every wire-bond layer, not just the Wire Bond setup's one -- e.g.
        # separate chip-bond and header-bond layers
        bond_layers |= {
            n for n in drawn
            if 0 <= n < len(self.library.layers)
            and "bond" in (self.library.layers[n].name or "").lower()
        } - resistor_layers - conductor_layers - glaze_layers
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
        # die-attach epoxy: any DRAWN layer named for it, shown or not --
        # it sits on the substrate under a part and can spread past the
        # part's edge, so it gets its own tier just under the components
        epoxy_layers = {
            n for n in drawn
            if 0 <= n < len(self.library.layers)
            and any(w in (self.library.layers[n].name or "").lower()
                    for w in ("epoxy", "die attach", "die-attach", "dieattach", "adhesive"))
        } - role_layers - substrate_layers
        other_layers = generic_layers - role_layers - substrate_layers - epoxy_layers

        roles = {}
        for role, group in (("other", other_layers), ("substrate", substrate_layers),
                            ("epoxy", epoxy_layers),
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
                          refdes_height: float = 600.0,
                          bond_width: Optional[float] = None) -> dict:
        """`bond_width` (DBU, role-based view only): draw every wire bond
        (a Path on a bond layer) as one line of this width along the wire,
        so the bonds stand out, instead of the outline of the real wire;
        the ball / stitch pads keep their outlines. None / 0 = outlines.

        `refdes=True` also writes every placed part's reference
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
        from ..io.techfile import load_bond_rules, load_resistor_geometry_rules, load_route_rules

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
            epoxy_layers = {n for n, r in roles.items() if r == "epoxy"}

            excluded = {resolve(n) for n in exclude_layers} if exclude_layers else set()
            if excluded:
                resistor_layers -= excluded
                conductor_layers -= excluded
                glaze_layers -= excluded
                bond_layers -= excluded
                substrate_layers -= excluded
                other_layers -= excluded
                epoxy_layers -= excluded

            all_considered_layers = resistor_layers | conductor_layers | glaze_layers \
                | bond_layers | substrate_layers | other_layers | epoxy_layers

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
                    split_packages=True, pad_layers=conductor_layers | bond_layers,
                    bond_layers=bond_layers, body_skip_layers=epoxy_layers)
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
                # die-attach epoxy: under the parts, over every film -- a
                # fillet wider than the part shows round it and hides the
                # conductor it covers
                for n in sorted(epoxy_layers, reverse=True):
                    layer_geoms.append((gather(n), True))
                for n in sorted(resistor_layers, reverse=True):
                    layer_geoms.append((gather(n), True))
                # the dielectric counts as a conductor-tier film for
                # stacking, but it is NOT hatched -- hatching it would
                # cross-hatch the whole board (and, chopped up by every
                # via window and track, run to hundreds of thousands of
                # paths -- confirmed live, it froze the GUI)
                _diel = dielectric_layer_number(self.library)
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
                bond_lines = []
                from ..core.shapes import Path as _BondPath
                for n in sorted(bond_layers):
                    if bond_width:
                        from ..core.maskops import layer_paths_flattened
                        c = src["cell"]
                        bond_lines.extend(layer_paths_flattened(self.library, c, n)
                                          if flatten else
                                          [list(sh.points) for sh in c.shapes_on_layer(n)
                                           if isinstance(sh, _BondPath) and len(sh.points) >= 2])
                        if flatten:
                            from ..core.maskops import layer_geoms_flattened
                            from shapely.ops import unary_union as _uu
                            gs = layer_geoms_flattened(self.library, c, n, skip_paths=True)
                            layer_geoms.append((_uu(gs) if gs else None, False))
                        else:
                            layer_geoms.append((layer_to_shapely(
                                [sh for sh in c.shapes_on_layer(n)
                                 if not isinstance(sh, _BondPath)]), False))
                        continue
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
                bond_lines = []
                layer_geoms = [(gather(n), True) for n in sorted(layer_set, reverse=True)]

            result = hidden_line_top_view(layer_geoms)
            if simplify:
                result = result.simplify(simplify, preserve_topology=True)
            new_paths = shapely_lines_to_paths(result, r_layer, width_dbu, datatype=datatype)
            if bond_lines:
                # the bonds over every outline, one bold line each
                bw = int(round(bond_width))
                from ..core.shapes import Path as _BondPath
                new_paths = list(new_paths) + [
                    _BondPath(layer=r_layer, points=[(int(round(x)), int(round(y))) for x, y in pts],
                         width=bw, cap="round", datatype=datatype)
                    for pts in bond_lines]
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
