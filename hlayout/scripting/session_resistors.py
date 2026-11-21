# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Printed (thick-/thin-film) resistors: generate a resistor from its value
and the technology rules -- straight, serpentine or top-hat -- and remove
placed ones.

Part of hlayout.scripting.api.Session (a mixin): these methods are
Session's own -- call them on a Session as always."""

from typing import Optional

from ..core.errors import LayoutError
from ..core.thinfilm import generate_serpentine_geometry, generate_top_hat_geometry
from ..io.techfile import (
    load_resistor_geometry_rules,
    load_thin_film_rules,
    resistor_ink_layer,
)


class ResistorsMixin:
    def generate_resistor(self, x, y, body_width, body_length, rotation: float = 0.0,
                           ink_rsh=None, ref_des=None, overlap_a=None, pad_b=None,
                           space_c=None, glaze_oversize=None, resistor_layer=None,
                           conductor_layer=None, glaze_layer=None, doc_layer=None,
                           keepout_layer=None, keepout_oversize=None,
                           label_height=None, cell_name=None, n_folds: int = 1,
                           shape: str = "rect", shape_params=None) -> str:
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
        tool already treats reusable parts (Create > Via Wizard...'s via
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

        Only shapes ticked in Setup > Technology Properties > Resistor
        Shapes (techfile.load_allowed_resistor_shapes) are accepted.

        ``shape`` draws one of the thick-film shapes in
        hlayout.core.tf_shapes instead of the plain rectangle: "tophat",
        "tophat_roof", "u", "u_roof", "angle", "double_angle" or
        "angle3" ("rect", the default, is the rectangle above). For a
        shape, ``body_length`` is that shape's sized length L (the bar
        gap of a top-hat, the arm length of a U or an angle, the lead
        length of a three-sided angle -- normally a Resistor Designer
        solution's ``body_length``) and ``shape_params`` its secondary
        dimensions (crown_w/crown_h, gap, skirt_h, rise, loop_w/loop_h;
        defaults from the ink width). The pin "1"/"2" terminations
        follow A/B/C at each end of the ink, a roof is a pin-less
        conductor, the glaze is the ink outline grown by D and the
        keep-out the ink outside the conductors grown by E.

        Building the device cell and placing its Sref are both part of
        a single undo step."""
        parent_cell_name = self.current_cell_name
        if parent_cell_name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        n_folds = max(1, int(n_folds))
        shape = (shape or "rect").strip().lower()
        from ..io.techfile import load_allowed_resistor_shapes
        from ..core.tf_shapes import SHAPE_KEYS
        allowed = load_allowed_resistor_shapes(self.library)
        if shape in SHAPE_KEYS and shape not in allowed:
            raise LayoutError(f"resistor shape {shape!r} is not allowed by this technology; "
                              f"allowed: {', '.join(allowed)}")

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

        if shape != "rect":
            from ..core import tf_shapes
            try:
                geom = tf_shapes.build(shape, float(body_width), float(body_length), a, b, c,
                                       shape_params)
            except tf_shapes.ShapeError as ex:
                raise LayoutError(str(ex)) from None
            with self._batch():
                self.new_cell(device_name)
                self._draw_shaped_resistor(geom, r_layer, c_layer, g_layer, k_layer, d, e)
                self.use_cell(parent_cell_name)
                self.sref(device_name, (x, y), rotation=rotation,
                          label=str(ref_des) if ref_des else "")
            return device_name

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

    def remove_placed_resistors(self, refs=None, cell_name: Optional[str] = None) -> list:
        """Take the printed resistors placed by generate_resistor() (the
        Resistor Designer's Place / Place All) back out of the current (or
        named) cell: every instance labelled with one of ``refs`` (default:
        every ref-des in the resistor list) whose cell is that resistor's
        generated device cell (R5, R5_1, ...), and each device cell nothing
        uses any more. One undo step. Returns the ref-des removed."""
        from ..io.techfile import load_resistor_list
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell")
        cell = self.library.get_cell(name)
        if refs is None:
            refs = [str(r.get("ref_des", "")) for r in load_resistor_list(self.library)]
        want = {str(r).strip() for r in refs if str(r).strip()}

        def generated(ref):
            lab = str(ref.label or "")
            return lab in want and (ref.cell_name == lab or ref.cell_name.startswith(lab + "_"))

        hit = [r for r in cell.srefs if generated(r)]
        if not hit:
            return []
        with self._batch():
            for r in hit:
                cell.srefs.remove(r)
            for cn in {r.cell_name for r in hit}:
                if cn in self.library.cells and not any(
                        x.cell_name == cn for c in self.library.cells.values()
                        for x in list(c.srefs) + list(c.arefs)):
                    del self.library.cells[cn]
        self._notify()
        return sorted({str(r.label) for r in hit},
                      key=lambda t: [int(p) if p.isdigit() else p
                                     for p in __import__("re").findall(r"\d+|\D+", t)])

    def _draw_shaped_resistor(self, geom, r_layer, c_layer, g_layer, k_layer, d, e) -> None:
        """Draw a hlayout.core.tf_shapes.ShapeGeometry into the current
        cell (see generate_resistor)."""
        from shapely.geometry import MultiPolygon

        def pts(poly):
            return [(int(round(px)), int(round(py))) for px, py in list(poly.exterior.coords)[:-1]]

        def polys(g):
            if g.is_empty:
                return []
            return list(g.geoms) if isinstance(g, MultiPolygon) else [g]

        ink = geom.ink_polygons()
        for poly in ink:
            self.polygon(r_layer, pts(poly))
        from shapely.ops import unary_union
        ink_u = unary_union(ink)
        for poly in polys(ink_u.buffer(d, join_style=2)):
            self.polygon(g_layer, pts(poly))
        for (x1, y1, x2, y2), pin in geom.pads:
            self.rect(c_layer, int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2)), pin=pin)
        for (x1, y1, x2, y2) in geom.roofs:
            self.rect(c_layer, int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2)))
        if k_layer is not None and int(k_layer) >= 0:
            res = unary_union(geom.resistive_polygons())
            for poly in polys(res.buffer(e, join_style=2)):
                self.polygon(int(k_layer), pts(poly))

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
