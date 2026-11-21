# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Dielectric: generate / clear the dielectric fill, full-sheet detection
and the needless-crossover pass.

Part of hlayout.scripting.api.Session (a mixin): these methods are
Session's own -- call them on a Session as always."""

from typing import Optional

from ..core.errors import LayoutError
from ..core.maskops import (
    fracture_polygons,
    layer_geoms_flattened,
    layer_to_shapely_flattened,
    size_geometry,
)
from ..io.techfile import dielectric_layer_number, load_dielectric_rules


class DielectricMixin:
    #: GDS datatype the generated dielectric FILL polygons are written on.
    #: Everything a user (or drop_via / generate_resistor) draws on the
    #: dielectric layer stays at datatype 0 and is treated as input --
    #: the outer boundary, plus every via window / keep-out rectangle
    #: inside it. generate_dielectric() reads datatype 0 and (re)writes
    #: datatype 1; clear_dielectric() removes only datatype 1.
    DIELECTRIC_FILL_DATATYPE = 1

    def _has_full_dielectric(self, cell, diel) -> bool:
        """True when ``cell``'s dielectric fill on layer ``diel`` is a full
        sheet (generate_dielectric's), so an edit that moves a via should
        regenerate it. False for no fill, and for the router's local
        crossover strips: regenerating those would flood the substrate with
        a full sheet. Full = there is fill input (datatype 0) on the layer,
        or the fill covers at least half the substrate."""
        fill = [sh for sh in cell.shapes if sh.layer == diel
                and getattr(sh, "datatype", 0) == self.DIELECTRIC_FILL_DATATYPE]
        if not fill:
            return False
        if any(sh.layer == diel and getattr(sh, "datatype", 0) == 0 for sh in cell.shapes):
            return True
        from shapely.geometry import Polygon as _SP
        area = 0.0
        for sh in fill:
            try:
                area += abs(_SP(sh.points).area)
            except Exception:
                pass
        try:
            sub = self._substrate_box(cell)
            whole = sub.area if sub is not None else 0.0
        except Exception:
            whole = 0.0
        if whole <= 0:
            bb = self.library.cell_bbox(cell.name)
            whole = (bb[2] - bb[0]) * (bb[3] - bb[1]) if bb else 0.0
        return whole > 0 and area >= 0.5 * whole

    def remove_needless_crossovers(self, cell_name: Optional[str] = None, on_line=None,
                                   time_budget: float = 60.0) -> dict:
        """Take out the crossovers the routing no longer needs: each net with
        a local crossover is re-routed by PathFinder with crossovers priced
        out, every other net kept as it is, and the result is kept only if the
        net then routes on the 1st conductor alone, still reaches every pad in
        one piece, adds no short and costs less (COND2 track x2.5 and 3 mm a
        via pair). Autoroute runs this last (Router setting
        drop_needless_crossovers). One undo step. Returns {"nets",
        "crossovers", "removed": [{"net", "crossovers", "saved_um"}], "kept":
        [{"net", "why"}]}. See hlayout.scripting.xover_cleanup."""
        from .xover_cleanup import remove_needless_crossovers as _rnx
        name = cell_name or self.current_cell_name
        with self._batch():
            r = _rnx(self, name, time_budget=time_budget, on_line=on_line)
        self._notify()
        return r

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
            r_layer = dielectric_layer_number(self.library)
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
