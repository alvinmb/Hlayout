# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Setup dialogs: Technology Properties and its tabs (route layers,
resistors and their shapes, ref-des, wire bonds, I/O pads, dielectric,
layer thicknesses), path style, circle segments and grid spacing.

Part of hlayout.gui.app.App (a mixin): these methods are App's own --
call them as always."""

import tkinter as tk
from tkinter import ttk

from ..core.errors import GeometryError, LayoutError
from ..core.shapes import CIRCLE_SEGMENT_CHOICES
from ..core.units import dbu_to_mm, mm_to_dbu
from ..io.techfile import (
    default_layer_thickness,
    load_allowed_resistor_shapes,
    load_bond_rules,
    load_dielectric_rules,
    load_layer_thickness,
    load_refdes_annotation_layer,
    load_resistor_geometry_rules,
    load_route_rules,
    save_allowed_resistor_shapes,
    save_bond_rules,
    save_dielectric_rules,
    save_layer_thickness,
    save_refdes_annotation_layer,
    save_resistor_geometry_rules,
    save_route_rules,
)
from .drc_window import open_drc_window
from .dxf_batch_window import open_dxf_batch_window
from .gerber_batch_window import open_gerber_batch_window
from .maskop_window import open_maskop_batch_window


class SetupDialogsMixin:
    def show_path_style_dialog(self):
        """Setup > Path Style...: set the width and end-cap style used
        when drawing new paths. Replaces the Width/Cap fields that used
        to live directly on the draw toolbar."""
        win = tk.Toplevel(self)
        win.title("Path Style")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        form = ttk.Frame(win)
        form.pack(padx=12, pady=12)

        ttk.Label(form, text="Width (mm):").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        width_var = tk.DoubleVar(value=dbu_to_mm(self.width_var.get()))
        width_spin = ttk.Spinbox(form, from_=0, to=100.0, increment=0.001, width=8, textvariable=width_var)
        width_spin.grid(row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="End cap:").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        cap_var = tk.StringVar(value=self.cap_var.get())
        cap_combo = ttk.Combobox(form, textvariable=cap_var, width=10, state="readonly",
                                  values=["round", "square", "flush"])
        cap_combo.grid(row=1, column=1, sticky="w", padx=4, pady=4)

        error_var = tk.StringVar(value="")
        ttk.Label(form, textvariable=error_var, foreground="red").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=4
        )

        def apply_and_close():
            try:
                width = mm_to_dbu(float(width_var.get()))
            except (tk.TclError, ValueError):
                error_var.set("Width must be a number.")
                return
            if width < 0:
                error_var.set("Width must not be negative.")
                return
            self.width_var.set(width)
            self.cap_var.set(cap_var.get())
            self._on_path_opts_change()
            self.status.set(f"path style: width={dbu_to_mm(width):g} mm cap={cap_var.get()}")
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(0, 12))
        ttk.Button(btn_row, text="OK", command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        width_spin.focus_set()
        win.bind("<Return>", lambda e: apply_and_close())
        win.bind("<Escape>", lambda e: win.destroy())

    def show_circle_segments_dialog(self):
        """Setup > Circle Segments...: choose how many segments the
        Circle draw tool uses to approximate a circle. Moved here from
        the draw toolbar since it's the one draw-tool option that only
        applies to a single tool (Circle), unlike Layer or Snap to
        grid which apply no matter what's being drawn."""
        win = tk.Toplevel(self)
        win.title("Circle Segments")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        form = ttk.Frame(win)
        form.pack(padx=12, pady=12)

        ttk.Label(form, text="Segments:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        segments_var = tk.StringVar(value=self.segments_var.get())
        segments_combo = ttk.Combobox(form, textvariable=segments_var, width=6, state="readonly",
                                       values=[str(n) for n in CIRCLE_SEGMENT_CHOICES])
        segments_combo.grid(row=0, column=1, sticky="w", padx=4, pady=4)

        def apply_and_close():
            self.segments_var.set(segments_var.get())
            self._on_circle_opts_change()
            self.status.set(f"circle segments: {segments_var.get()}")
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(0, 12))
        ttk.Button(btn_row, text="OK", command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        segments_combo.focus_set()
        win.bind("<Return>", lambda e: apply_and_close())
        win.bind("<Escape>", lambda e: win.destroy())

    def show_route_layers_dialog(self):
        """Setup > Route Layers...: configure the two conductor layers
        the Create > Route (2-Layer) tool uses, and which reusable via
        cell it places between them. Route starts on Layer A; each Space
        press during a route places an instance (Sref) of the via cell
        at the current point and switches which of the two layers you're
        routing on -- see LayoutCanvas._route_drop_via()/App._on_route_via().

        The via cell itself -- its pad size/shape, any dielectric window,
        anything else its own geometry carries -- is built once via
        Create > Via Wizard... (or scripting's create_via_cell()) and just
        named here, the same "build the reusable part once, place
        instances of it" pattern as any other cell (Create > Cell
        Instance, Find && Place Cell...) -- this dialog used to also ask
        for via size/shape and a separate dielectric layer/window, which
        only duplicated what the via cell's own geometry already
        describes.

        Everything here is saved into the technology file
        (hlayout.io.techfile.save_route_rules()) on OK, the same
        tech_params-blob pattern as the Resistor Designer's and DRC's
        own saved rules, so it travels with Setup > Save Technology
        File... / TECH SAVE and the per-design .hlaux sidecar -- a route
        setup configured once doesn't need to be re-entered next
        session. Also a tab of Setup > Technology Properties..."""
        return self._setting_window("Route Layers", self._build_route_form)

    def _build_route_form(self, parent):
        form = ttk.Frame(parent)
        form.pack(padx=12, pady=12)

        ttk.Label(form, text="Layer A (route starts here):").grid(
            row=0, column=0, sticky="e", padx=4, pady=4)
        layer_a_var = tk.StringVar(value=self.route_layer_a_var.get())
        ttk.Combobox(form, textvariable=layer_a_var, width=22,
                     values=self._layer_choices()).grid(row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Layer B (Space switches to/from):").grid(
            row=1, column=0, sticky="e", padx=4, pady=4)
        layer_b_var = tk.StringVar(value=self.route_layer_b_var.get())
        ttk.Combobox(form, textvariable=layer_b_var, width=22,
                     values=self._layer_choices()).grid(row=1, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Via cell (Space places an instance):").grid(
            row=2, column=0, sticky="e", padx=4, pady=4)
        via_cell_choices = list(self._placeable_cell_names())
        via_cell_var = tk.StringVar(value=self.route_via_cell_var.get())
        via_combo = ttk.Combobox(form, textvariable=via_cell_var, width=22,
                                 values=via_cell_choices)
        via_combo.grid(row=2, column=1, sticky="w", padx=4, pady=4)

        def _via_created(name):
            """Called back by the Via Wizard once it actually creates a
            cell -- refresh this combo's list and select the new one, so
            a missing via cell never traps you here with nothing to pick."""
            via_cell_choices[:] = list(self._placeable_cell_names())
            via_combo.configure(values=via_cell_choices)
            via_cell_var.set(name)

        ttk.Button(form, text="New Via Cell…",
                   command=lambda: self.show_via_wizard(on_created=_via_created)).grid(
            row=2, column=2, sticky="w", padx=4, pady=4)

        ttk.Label(
            form,
            text="Built once via Create > Via Wizard... (\"New Via Cell…\" above)\n"
                 "or scripting's create_via_cell(); this just names which one\n"
                 "to place. Leave blank for now -- routing works, but Space\n"
                 "can't drop a via until one is set.",
            foreground="#555", justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", padx=4)

        _BND_NONE = "(none - route anywhere)"
        rr = load_route_rules(self.session.library)
        bnd = int(rr.get("boundary_layer", -1))
        ttk.Label(form, text="Auto-Route boundary layer:").grid(
            row=4, column=0, sticky="e", padx=4, pady=(10, 4))
        boundary_var = tk.StringVar(
            value=self._layer_label(bnd) if bnd >= 0 else _BND_NONE)
        ttk.Combobox(form, textvariable=boundary_var, width=22, state="readonly",
                     values=[_BND_NONE] + self._layer_choices()).grid(
            row=4, column=1, sticky="w", padx=4, pady=(10, 4))
        ttk.Label(form,
                  text="AUTOROUTE / Edit > Auto-Route Two Pins keeps the trace\n"
                       "inside this layer's geometry.",
                  foreground="#555", justify="left").grid(
            row=5, column=0, columnspan=3, sticky="w", padx=4)

        def apply():
            try:
                a = self._resolve_layer_field(layer_a_var.get())
                b = self._resolve_layer_field(layer_b_var.get())
            except LayoutError as e:
                return str(e)
            if a == b:
                return "Layer A and Layer B must be different."
            via_cell = via_cell_var.get().strip()
            if via_cell and via_cell not in via_cell_choices:
                return (f"No cell named {via_cell!r} in this design or any "
                        f"attached library -- use \"New Via Cell…\" to build one.")
            btext = boundary_var.get().strip()
            try:
                boundary = (-1 if btext == _BND_NONE or not btext
                            else self._resolve_layer_field(btext))
            except LayoutError as e:
                return str(e)

            self.route_layer_a_var.set(self._layer_label(a))
            self.route_layer_b_var.set(self._layer_label(b))
            self.route_via_cell_var.set(via_cell)
            self._on_route_opts_change()
            save_route_rules(self.session.library, {
                "layer_a": a, "layer_b": b, "via_cell": via_cell,
                "boundary_layer": boundary,
            })
            if via_cell:
                self.status.set(
                    f"route layers: A={self._layer_label(a)}  B={self._layer_label(b)}  "
                    f"via cell={via_cell!r}"
                )
            else:
                self.status.set(
                    f"route layers: A={self._layer_label(a)}  B={self._layer_label(b)}  "
                    f"(no via cell set yet -- add one with \"New Via Cell…\" to drop vias)"
                )
            return None

        return apply

    def show_resistor_geometry_dialog(self):
        """Setup > Technology Properties... > Resistors tab (and still
        its own window): the A/B/C/D/E dimensions and the layers used
        when a resistor is drawn into the layout -- Session.generate_
        resistor() (RESGEN, and Tools > Thick-Film Resistor Designer.../
        "Place in Layout..."). The reference-designator layer is NOT
        set here any more -- it's the one on the Ref-Des tab
        (Setup > Reference Designator Layer). Saved via
        hlayout.io.techfile.save_resistor_geometry_rules().

        A = overlap: resistor ink extends this far beyond the electrical
            body_length at BOTH ends, so it overlaps (contacts) the
            conductor pad.
        B = pad length: how far each conductor pad extends beyond that
            overlap, at each end. Each pad's own footprint is A+B long.
        C = side space: how far each pad extends beyond the resistor
            ink's width on either side (pad width = body_width + 2*C).
        D = glaze oversize: the glaze overprint is the resistor ink
            body's own shape, grown by D on ALL FOUR sides (not just
            matching the ink's footprint).
        """
        return self._setting_window("Resistor Geometry Rules",
                                    self._build_resistor_geometry_form)

    #: picture lengths (x W) for each shape's thumbnail in the
    #: Resistor Shapes tab
    _SHAPE_THUMB_L = {"rect": 2.0, "tophat": 4.0, "tophat_roof": 4.5, "u": 1.5,
                      "u_roof": 1.5, "angle": 1.5, "double_angle": 1.5, "angle3": 1.2}

    def _build_resistor_shapes_form(self, parent):
        """Setup > Technology Properties > Resistor Shapes: a picture of
        each thick-film resistor shape (hlayout.core.tf_shapes) with a
        check box. Only ticked shapes are offered by the Resistor
        Designer and accepted by RESGEN / generate_resistor(). Saved via
        hlayout.io.techfile.save_allowed_resistor_shapes()."""
        from ..core import tf_shapes
        allowed = set(load_allowed_resistor_shapes(self.session.library))

        ttk.Label(
            parent,
            text="Tick the resistor shapes this process supports. Only ticked\n"
                 "shapes are offered by the Thick-Film Resistor Designer and\n"
                 "accepted by RESGEN. Click a picture to toggle it.",
            justify="left", foreground="#555",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        grid = ttk.Frame(parent)
        grid.pack(padx=12)
        cw, ch = 168, 118
        w, a, b, c = 1000.0, 250.0, 350.0, 150.0
        vars_ = {}

        def draw(canvas, key):
            on = vars_[key].get()
            canvas.delete("all")
            canvas.configure(background="#ffffff" if on else "#f0f0f0",
                             highlightbackground="#2e7d32" if on else "#c8c8c8")
            try:
                g = tf_shapes.build(key, w, self._SHAPE_THUMB_L[key] * w, a, b, c)
            except tf_shapes.ShapeError:
                return
            rects = ([(r, "ink") for r in g.ink] + [(r, "pad") for r, _pin in g.pads]
                     + [(r, "roof") for r in g.roofs])
            x0 = min(r[0] for r, _ in rects); y0 = min(r[1] for r, _ in rects)
            x1 = max(r[2] for r, _ in rects); y1 = max(r[3] for r, _ in rects)
            pad = 12
            k = min((cw - 2 * pad) / (x1 - x0), (ch - 2 * pad) / (y1 - y0))
            ox = (cw - (x1 - x0) * k) / 2
            oy = (ch - (y1 - y0) * k) / 2
            colours = ({"ink": "#34495e", "pad": "#d4a017", "roof": "#e0b84a"} if on else
                       {"ink": "#b8bec4", "pad": "#ddd3b4", "roof": "#e4dcc4"})
            # pads first, ink over them -- the ink runs A under each pad
            for kind in ("pad", "roof", "ink"):
                for r, kd in rects:
                    if kd != kind:
                        continue
                    canvas.create_rectangle(
                        ox + (r[0] - x0) * k, ch - oy - (r[3] - y0) * k,
                        ox + (r[2] - x0) * k, ch - oy - (r[1] - y0) * k,
                        fill=colours[kind], outline="")
            if not on:
                canvas.create_text(cw - 6, 6, text="not allowed", anchor="ne",
                                   fill="#999999", font=("TkDefaultFont", 8))

        canvases = {}
        for i, (key, label, code) in enumerate(tf_shapes.SHAPES):
            cell = ttk.Frame(grid)
            cell.grid(row=i // 4, column=i % 4, padx=6, pady=6)
            cv = tk.Canvas(cell, width=cw, height=ch, highlightthickness=2,
                           cursor="hand2")
            cv.pack()
            var = tk.BooleanVar(value=key in allowed)
            vars_[key] = var
            canvases[key] = cv
            ttk.Checkbutton(cell, text=f"{label} ({code})", variable=var,
                            command=lambda k_=key: draw(canvases[k_], k_)).pack(pady=(3, 0))

            def toggle(_e, k_=key):
                vars_[k_].set(not vars_[k_].get())
                draw(canvases[k_], k_)
            cv.bind("<Button-1>", toggle)
            draw(cv, key)

        def set_all(keys):
            for k_, v in vars_.items():
                v.set(k_ in keys)
                draw(canvases[k_], k_)

        row = ttk.Frame(parent)
        row.pack(anchor="w", padx=12, pady=(4, 8))
        ttk.Button(row, text="Allow all", command=lambda: set_all(set(vars_))).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Rectangle only", command=lambda: set_all({"rect"})).pack(side="left")

        def apply():
            keys = [k_ for k_, v in vars_.items() if v.get()]
            if not keys:
                return "Tick at least one resistor shape."
            save_allowed_resistor_shapes(self.session.library, keys)
            return None

        return apply

    def _build_resistor_geometry_form(self, parent):
        rules = load_resistor_geometry_rules(self.session.library)
        win = parent

        ttk.Label(
            win,
            text="A = resistor-to-conductor overlap (added to body_length at\n"
                 "     both ends)\n"
                 "B = minimum conductor pad length beyond the overlap\n"
                 "     (each pad's footprint is A+B long)\n"
                 "C = minimum conductor space on the side (pad width =\n"
                 "     body_width + 2×C)\n"
                 "D = glaze oversize -- the glaze overprint is the resistor's\n"
                 "     own shape, grown by D on all 4 sides (not just matching\n"
                 "     the ink body), for post-trim protection.\n"
                 "E = dielectric keep-out oversize -- a rectangle in the gap\n"
                 "     BETWEEN the two terminals (subtract from the dielectric),\n"
                 "     grown by E on all 4 sides. Needs a keep-out layer set.",
            justify="left", foreground="#555",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        form = ttk.Frame(win)
        form.pack(padx=12, fill="x")

        ttk.Label(form, text="A - Overlap (mm):").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        a_var = tk.DoubleVar(value=dbu_to_mm(rules["overlap_a"]))
        ttk.Spinbox(form, from_=0, to=1000.0, increment=0.001, width=10, textvariable=a_var).grid(
            row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="B - Pad length (mm):").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        b_var = tk.DoubleVar(value=dbu_to_mm(rules["pad_b"]))
        ttk.Spinbox(form, from_=0, to=1000.0, increment=0.001, width=10, textvariable=b_var).grid(
            row=1, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="C - Side space (mm):").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        c_var = tk.DoubleVar(value=dbu_to_mm(rules["space_c"]))
        ttk.Spinbox(form, from_=0, to=1000.0, increment=0.001, width=10, textvariable=c_var).grid(
            row=2, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="D - Glaze oversize (mm):").grid(row=3, column=0, sticky="e", padx=4, pady=4)
        d_var = tk.DoubleVar(value=dbu_to_mm(rules["glaze_oversize"]))
        ttk.Spinbox(form, from_=0, to=1000.0, increment=0.001, width=10, textvariable=d_var).grid(
            row=3, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="E - Dielectric keep-out oversize (mm):").grid(
            row=4, column=0, sticky="e", padx=4, pady=4)
        e_var = tk.DoubleVar(value=dbu_to_mm(rules["keepout_oversize"]))
        ttk.Spinbox(form, from_=0, to=1000.0, increment=0.001, width=10, textvariable=e_var).grid(
            row=4, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Resistor ink layer (fallback):").grid(
            row=5, column=0, sticky="e", padx=4, pady=4)
        r_layer_var = tk.StringVar(value=self._layer_label(rules["resistor_layer"]))
        ttk.Combobox(form, textvariable=r_layer_var, width=22,
                     values=self._layer_choices()).grid(row=5, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Conductor pad layer:").grid(row=6, column=0, sticky="e", padx=4, pady=4)
        c_layer_var = tk.StringVar(value=self._layer_label(rules["conductor_layer"]))
        ttk.Combobox(form, textvariable=c_layer_var, width=22,
                     values=self._layer_choices()).grid(row=6, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Glaze overprint layer:").grid(row=7, column=0, sticky="e", padx=4, pady=4)
        g_layer_var = tk.StringVar(value=self._layer_label(rules["glaze_layer"]))
        ttk.Combobox(form, textvariable=g_layer_var, width=22,
                     values=self._layer_choices()).grid(row=7, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Dielectric keep-out layer:").grid(
            row=8, column=0, sticky="e", padx=4, pady=4)
        _KO_NONE = "(none - disabled)"
        k_layer_var = tk.StringVar(
            value=(self._layer_label(rules["keepout_layer"])
                   if int(rules["keepout_layer"]) >= 0 else _KO_NONE))
        ttk.Combobox(form, textvariable=k_layer_var, width=22,
                     values=[_KO_NONE] + self._layer_choices()).grid(
            row=8, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form,
                  text="(the reference-designator layer is on the Ref-Des tab)",
                  foreground="#888").grid(row=9, column=0, columnspan=2,
                                          sticky="w", padx=4, pady=(2, 0))

        def apply():
            try:
                a, b, c, d, e = (mm_to_dbu(a_var.get()), mm_to_dbu(b_var.get()),
                                 mm_to_dbu(c_var.get()), mm_to_dbu(d_var.get()),
                                 mm_to_dbu(e_var.get()))
            except tk.TclError:
                return "A, B, C, D, and E must be numbers."
            if a < 0 or b < 0 or c < 0 or d < 0 or e < 0:
                return "A, B, C, D, and E must not be negative."
            try:
                r_layer = self._resolve_layer_field(r_layer_var.get())
                c_layer = self._resolve_layer_field(c_layer_var.get())
                g_layer = self._resolve_layer_field(g_layer_var.get())
                k_text = k_layer_var.get().strip()
                k_layer = (-1 if k_text == _KO_NONE or not k_text
                           else self._resolve_layer_field(k_text))
            except LayoutError as exc:
                return str(exc)
            distinct = [r_layer, c_layer, g_layer]
            if k_layer >= 0:
                distinct.append(k_layer)
            if len(set(distinct)) != len(distinct):
                return ("Resistor ink, conductor pad, glaze, and keep-out "
                        "layers must all be different.")
            save_resistor_geometry_rules(self.session.library, {
                "overlap_a": a, "pad_b": b, "space_c": c, "glaze_oversize": d,
                "keepout_oversize": e,
                "resistor_layer": r_layer, "conductor_layer": c_layer,
                "glaze_layer": g_layer,
                # doc_layer is no longer edited here -- carried forward
                # untouched as the legacy fallback for generate_resistor()
                # (the live ref-des layer is refdes_annotation_layer).
                "doc_layer": rules["doc_layer"],
                "keepout_layer": k_layer,
                "ink_layers": rules["ink_layers"],
            })
            ko = self._layer_label(k_layer) if k_layer >= 0 else "(none)"
            self.status.set(
                f"resistor geometry rules saved: A={dbu_to_mm(a):g} B={dbu_to_mm(b):g} "
                f"C={dbu_to_mm(c):g} D={dbu_to_mm(d):g} E={dbu_to_mm(e):g} mm  "
                f"ink={self._layer_label(r_layer)}  pad={self._layer_label(c_layer)}  "
                f"glaze={self._layer_label(g_layer)}  keepout={ko}")
            return None

        return apply

    def show_refdes_annotation_layer_dialog(self):
        """Setup > Technology Properties... > Ref-Des tab (and still its
        own window): the design's ONE reference-designator layer. It is
        where generate_resistor() bakes a resistor's ref-des Text, and
        the layer whose visibility gates the per-instance ref-des
        annotation the canvas floats at each placed part's origin, so
        every ref-des shows and hides together. "(none)" leaves the
        annotation always shown. Saved via
        hlayout.io.techfile.save_refdes_annotation_layer()."""
        return self._setting_window("Reference Designator Layer",
                                    self._build_refdes_form)

    def _build_refdes_form(self, parent):
        current = load_refdes_annotation_layer(self.session.library)

        ttk.Label(
            parent,
            text="The one layer this design uses for reference designators.\n"
                 "A resistor's ref-des Text is baked here, and the per-instance\n"
                 "ref-des annotation the canvas floats at each part's origin is\n"
                 "drawn only while this layer is visible, so every ref-des shows\n"
                 "and hides together. \"(none)\" = annotation always shown.",
            justify="left", foreground="#555",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        form = ttk.Frame(parent)
        form.pack(padx=12, fill="x")
        ttk.Label(form, text="Ref-des layer:").grid(row=0, column=0, sticky="e", padx=4, pady=6)
        _NONE = "(none - always shown)"
        var = tk.StringVar(value=self._layer_label(current) if current >= 0 else _NONE)
        ttk.Combobox(form, textvariable=var, width=24, state="readonly",
                     values=[_NONE] + self._layer_choices()).grid(
            row=0, column=1, sticky="w", padx=4, pady=6)

        def apply():
            text = var.get().strip()
            try:
                layer = -1 if text == _NONE or not text else self._resolve_layer_field(text)
            except LayoutError as exc:
                return str(exc)
            save_refdes_annotation_layer(self.session.library, layer)
            self.canvas.redraw()
            self.status.set(
                "ref-des layer: "
                + (self._layer_label(layer) if layer >= 0 else "(none - always shown)"))
            return None

        return apply

    def _setting_window(self, title, build_form):
        """Wrap one `_build_*_form(parent) -> apply()` in a standalone
        Toplevel with a shared OK / Cancel / error row -- so every
        technology-property form is available both on its own (menu /
        tests) and as a tab of Setup > Technology Properties...
        `apply()` returns an error string (shown, dialog stays open) or
        None (saved -- dialog closes)."""
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True)
        apply_fn = build_form(body)

        err = tk.StringVar(value="")
        ttk.Label(win, textvariable=err, foreground="red", wraplength=360,
                  justify="left").pack(anchor="w", padx=12)

        def ok():
            msg = apply_fn()
            if msg:
                err.set(msg)
            else:
                win.destroy()

        row = ttk.Frame(win)
        row.pack(pady=(8, 12))
        ttk.Button(row, text="OK", command=ok).pack(side="left", padx=6)
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="left", padx=6)
        win.bind("<Return>", lambda e: ok())
        win.bind("<Escape>", lambda e: win.destroy())
        return win

    def show_technology_properties_dialog(self):
        """Setup > Technology Properties...: every technology-file-backed
        setting in one tabbed window. Each tab is the same form as its
        old standalone dialog; one OK validates and saves all of them
        (a tab that fails validation is selected and its message shown).
        The DRC / mask-op batch / export-job editors keep their own
        windows -- the last tab just links to them."""
        win = tk.Toplevel(self)
        win.title("Technology Properties")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        # roomier tab headers -- the default padding packs the labels in
        # too tightly to scan at a glance.
        ttk.Style(win).configure("Tech.TNotebook.Tab", padding=(18, 10),
                                 font=self._menu_font)
        nb = ttk.Notebook(win, style="Tech.TNotebook")
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        tabs = []   # (label, frame, apply_fn)
        for label, builder in (
            ("Route", self._build_route_form),
            ("Resistors", self._build_resistor_geometry_form),
            ("Resistor Shapes", self._build_resistor_shapes_form),
            ("Ref-Des", self._build_refdes_form),
            ("Wire Bonds", self._build_wire_bond_form),
            ("I/O Pads", self._build_io_pad_form),
            ("Dielectric", self._build_dielectric_form),
            ("Thicknesses", self._build_layer_thickness_form),
        ):
            frame = ttk.Frame(nb)
            nb.add(frame, text=label)
            tabs.append((label, frame, builder(frame)))

        more = ttk.Frame(nb)
        nb.add(more, text="More…")
        ttk.Label(more, text="Editors with their own windows:",
                  foreground="#555").pack(anchor="w", padx=12, pady=(12, 6))

        def _open_more(opener):
            # These editors are their own independent, non-modal windows
            # (reused across calls, meant to stay open and be worked in
            # alongside everything else) -- but `win`'s grab_set() above
            # is a LOCAL grab, meaning it captures EVERY input event
            # app-wide, not just events aimed at `win`. Left in place,
            # the window `opener` just created would sit there entirely
            # inert -- not even its own comboboxes would respond -- since
            # every click still routes to `win` alone. Release it before
            # opening, then reclaim it once `win` is the active window
            # again (its own FocusIn), so Technology Properties itself is
            # still modal to the main app, but doesn't lock out the
            # editor window it just handed off to.
            win.grab_release()
            return opener(self)

        for text, opener in (
            ("DRC Rules…", open_drc_window),
            ("Mask Operation Batch…", open_maskop_batch_window),
            ("Gerber Export Sets…", open_gerber_batch_window),
            ("DXF Export Sets…", open_dxf_batch_window),
        ):
            ttk.Button(more, text=text, width=26,
                       command=lambda o=opener: _open_more(o)).pack(anchor="w", padx=16, pady=3)
        win.bind("<FocusIn>", lambda e: win.grab_set())

        err = tk.StringVar(value="")
        ttk.Label(win, textvariable=err, foreground="red", wraplength=420,
                  justify="left").pack(anchor="w", padx=12)

        def ok():
            for label, frame, apply_fn in tabs:
                msg = apply_fn()
                if msg:
                    nb.select(frame)
                    err.set(f"{label}: {msg}")
                    return
            win.destroy()

        row = ttk.Frame(win)
        row.pack(pady=(6, 12))
        ttk.Button(row, text="OK", command=ok).pack(side="left", padx=6)
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="left", padx=6)
        win.bind("<Escape>", lambda e: win.destroy())
        return win

    def show_wire_bond_rules_dialog(self):
        """Setup > Technology Properties (Wire Bonds tab): the layer and defaults the BOND
        command / Session.wire_bond() use to draw a chip-and-wire
        assembly wire bond (an O------O ball-to-ball figure), and the
        min/max span it enforces. This layer is also folded into
        Extract Netlist automatically, so a bond carries connectivity
        through to the die pin. Also a tab of Setup > Technology
        Properties... Saved via hlayout.io.techfile.save_bond_rules()."""
        return self._setting_window("Wire Bond Rules", self._build_wire_bond_form)

    def _build_wire_bond_form(self, parent):
        rules = load_bond_rules(self.session.library)

        ttk.Label(
            parent,
            text="A wire bond ties a bare-die pad to a conductor pad, a conductor\n"
                 "pad to a package pin, or two conductor pads (a jumper). It is\n"
                 "drawn on the bond layer as a ball of the given diameter at each\n"
                 "end joined by a wire of the given width. The span must fall\n"
                 "between min and max, and a new bond may not cross an existing\n"
                 "one. Extract Netlist connects a bond at its two ends only;\n"
                 "pads on the isolated pad layers (die pads, header pins)\n"
                 "are kept apart from the copper underneath and joined only\n"
                 "by bonds. List several layers separated by commas.",
            justify="left", foreground="#555",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        form = ttk.Frame(parent)
        form.pack(padx=12, fill="x")

        ttk.Label(form, text="Bond layer:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        layer_var = tk.StringVar(
            value=self._layer_label(rules["layer"]) if rules["layer"] >= 0 else "")
        ttk.Combobox(form, textvariable=layer_var, width=24, state="readonly",
                     values=self._layer_choices()).grid(row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Wire width (mm):").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        width_var = tk.DoubleVar(value=dbu_to_mm(rules["width"]))
        ttk.Spinbox(form, from_=0.001, to=10.0, increment=0.005, width=10,
                    textvariable=width_var).grid(row=1, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="End ball dia. (mm):").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        pad_var = tk.DoubleVar(value=dbu_to_mm(rules["pad"]))
        ttk.Spinbox(form, from_=0.001, to=10.0, increment=0.01, width=10,
                    textvariable=pad_var).grid(row=2, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Min length (mm):").grid(row=3, column=0, sticky="e", padx=4, pady=4)
        min_var = tk.DoubleVar(value=dbu_to_mm(rules["min_length"]))
        ttk.Spinbox(form, from_=0, to=100.0, increment=0.05, width=10,
                    textvariable=min_var).grid(row=3, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Max length (mm):").grid(row=4, column=0, sticky="e", padx=4, pady=4)
        max_var = tk.DoubleVar(value=dbu_to_mm(rules["max_length"]))
        ttk.Spinbox(form, from_=0, to=100.0, increment=0.1, width=10,
                    textvariable=max_var).grid(row=4, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Isolated pad layers:").grid(row=5, column=0, sticky="e", padx=4, pady=4)
        die_var = tk.StringVar(value=", ".join(str(n) for n in rules["die_pad_layers"]))
        ttk.Entry(form, textvariable=die_var, width=26).grid(
            row=5, column=1, sticky="w", padx=4, pady=4)

        def apply():
            text = layer_var.get().strip()
            try:
                layer = -1 if not text else self._resolve_layer_field(text)
                die_layers = [self._resolve_layer_field(t.strip())
                              for t in die_var.get().split(",") if t.strip()]
            except LayoutError as exc:
                return str(exc)
            try:
                width = mm_to_dbu(width_var.get())
                pad = mm_to_dbu(pad_var.get())
                min_len = mm_to_dbu(min_var.get())
                max_len = mm_to_dbu(max_var.get())
            except tk.TclError:
                return "Width, ball diameter and lengths must be numbers."
            if min(width, pad, min_len, max_len) < 0:
                return "Values must not be negative."
            if max_len and max_len < min_len:
                return "Max length must not be less than min length."
            save_bond_rules(self.session.library, {
                "layer": layer, "width": width, "pad": pad,
                "min_length": min_len, "max_length": max_len,
                "die_pad_layers": die_layers,
            })
            self.status.set(
                "wire bond layer: "
                + (self._layer_label(layer) if layer >= 0 else "(none)"))
            return None

        return apply

    def _build_io_pad_form(self, parent):
        """Technology Properties, I/O Pads tab: the size of the module I/O
        pads Tools > Assign I/O Pads... (IOPADS) draws where the header
        bonds land, and where on the pad the bond lands."""
        from ..io.techfile import load_io_pad_rules, save_io_pad_rules
        rules = load_io_pad_rules(self.session.library)
        ttk.Label(
            parent,
            text="Module I/O pads, drawn by Tools > Bond Header Pins... and\n"
                 "Tools > Assign I/O Pads... where each header bond lands on the\n"
                 "substrate. Width runs along the substrate edge, length points\n"
                 "in from the header; the bond lands the inset in from the pad's\n"
                 "outer end, so the track leaves from the inner end. Bond Header\n"
                 "Pins puts the pad's outer end the edge margin in from the edge.",
            justify="left", foreground="#555",
        ).pack(anchor="w", padx=12, pady=(12, 8))
        form = ttk.Frame(parent)
        form.pack(padx=12, fill="x")
        fields = []
        for row, (label, key) in enumerate((("Pad width (mm):", "width"),
                                            ("Pad length (mm):", "length"),
                                            ("Bond inset from outer end (mm):", "bond_inset"),
                                            ("Edge margin (mm, Bond Header Pins):",
                                             "edge_margin"))):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="e", padx=4, pady=4)
            var = tk.DoubleVar(value=dbu_to_mm(rules[key]))
            ttk.Spinbox(form, from_=0.0, to=20.0, increment=0.05, width=10,
                        textvariable=var).grid(row=row, column=1, sticky="w", padx=4, pady=4)
            fields.append((key, var))

        def apply():
            try:
                vals = {k: mm_to_dbu(v.get()) for k, v in fields}
            except tk.TclError:
                return "Pad width, length and bond inset must be numbers."
            if vals["width"] <= 0 or vals["length"] <= 0:
                return "Pad width and length must be more than 0."
            if vals["bond_inset"] < 0 or vals["bond_inset"] > vals["length"]:
                return "The bond inset must be between 0 and the pad length."
            if vals["edge_margin"] < 0:
                return "The edge margin must be 0 or more."
            save_io_pad_rules(self.session.library, vals)
            return None

        return apply

    def _build_dielectric_form(self, parent):
        """The Dielectric Fill *rules* form (layer / margin / clearance /
        extra keep-out layers). apply() only SAVES the rules -- the
        Tools > Generate Dielectric Fill... window adds a Generate
        button that runs generate_dielectric() after saving."""
        rules = load_dielectric_rules(self.session.library)

        ttk.Label(
            parent,
            text="The dielectric layer's own datatype-0 geometry is the input:\n"
                 "the shape that encloses all the others is the fill boundary\n"
                 "(or the design bbox + margin if none), and every other shape\n"
                 "inside it -- a via window, a resistor keep-out, a hand-drawn\n"
                 "exclusion rect -- becomes a hole. Keep-outs are grown by the\n"
                 "clearance; a via window is printed at its drawn size.\n"
                 "Every via's window is its pad grown by the via window margin\n"
                 "on each side -- one rule for routed vias, the Route tool's\n"
                 "via cell and the Via Wizard (Tools > Fix Vias redraws\n"
                 "existing ones). The fill is written at datatype 1;\n"
                 "re-running replaces just that, never the inputs.",
            justify="left", foreground="#555",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        form = ttk.Frame(parent)
        form.pack(padx=12, fill="x")

        ttk.Label(form, text="Dielectric layer:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        layer_var = tk.StringVar(
            value=self._layer_label(rules["layer"]) if rules["layer"] >= 0 else "")
        ttk.Combobox(form, textvariable=layer_var, width=24, state="readonly",
                     values=self._layer_choices()).grid(row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Margin (mm):").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        margin_var = tk.DoubleVar(value=dbu_to_mm(rules["margin"]))
        ttk.Spinbox(form, from_=0, to=1000.0, increment=0.05, width=10,
                    textvariable=margin_var).grid(row=1, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Keep-out clearance (mm):").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        clr_var = tk.DoubleVar(value=dbu_to_mm(rules["clearance"]))
        ttk.Spinbox(form, from_=0, to=1000.0, increment=0.01, width=10,
                    textvariable=clr_var).grid(row=2, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Extra keep-out layers:").grid(row=3, column=0, sticky="e", padx=4, pady=4)
        ko_var = tk.StringVar(value=", ".join(str(n) for n in rules["keepout_layers"]))
        ttk.Entry(form, textvariable=ko_var, width=26).grid(
            row=3, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(form, text="(optional, comma-separated layer numbers or names)",
                  foreground="#888").grid(row=4, column=1, sticky="w", padx=4)

        ttk.Label(form, text="Via window margin (mm):").grid(row=5, column=0, sticky="e", padx=4, pady=4)
        vwm_var = tk.DoubleVar(value=dbu_to_mm(rules.get("via_window_margin", 100)))
        ttk.Spinbox(form, from_=0, to=10.0, increment=0.01, width=10,
                    textvariable=vwm_var).grid(row=5, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(form, text="(window = via pad + 2 x margin)",
                  foreground="#888").grid(row=6, column=1, sticky="w", padx=4)

        def parse():
            """Return (rules_dict, None) or (None, error_message).
            A blank layer means "not configured yet" (layer = -1) rather
            than an error -- so this never traps the tabbed Technology
            Properties dialog; Generate Dielectric Fill raises its own
            clear error if you actually run it with no layer set."""
            if not layer_var.get().strip():
                r_layer = -1
            else:
                try:
                    r_layer = self._resolve_layer_field(layer_var.get())
                except LayoutError as exc:
                    return None, str(exc)
            try:
                margin = mm_to_dbu(margin_var.get())
                clearance = mm_to_dbu(clr_var.get())
                vwm = mm_to_dbu(vwm_var.get())
            except tk.TclError:
                return None, "Margin, clearance and via window margin must be numbers."
            if margin < 0 or clearance < 0 or vwm < 0:
                return None, "Margin, clearance and via window margin must not be negative."
            ko_nums = []
            for tok in ko_var.get().replace(",", " ").split():
                try:
                    ko_nums.append(self._resolve_layer_field(tok))
                except LayoutError as exc:
                    return None, str(exc)
            return {"layer": r_layer, "margin": margin, "clearance": clearance,
                    "keepout_layers": ko_nums, "via_window_margin": vwm}, None

        def apply():
            new_rules, msg = parse()
            if msg:
                return msg
            save_dielectric_rules(self.session.library, new_rules)
            self.status.set("dielectric fill rules saved")
            return None

        apply.parse = parse   # exposed for the Generate button below
        return apply

    def show_dielectric_dialog(self):
        """Tools > Generate Dielectric Fill...: the Dielectric tab's rules
        form plus a Generate button that saves the rules and runs
        Session.generate_dielectric() -- lay a solid sheet on the chosen
        layer, opening a clearance hole around every via window / keep-out
        it finds. Re-running replaces the previous fill. The rules alone
        are also editable from Setup > Technology Properties... >
        Dielectric. Saved via hlayout.io.techfile.save_dielectric_rules()."""
        win = tk.Toplevel(self)
        win.title("Generate Dielectric Fill")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True)
        apply_fn = self._build_dielectric_form(body)

        err = tk.StringVar(value="")
        ttk.Label(win, textvariable=err, foreground="red", wraplength=360,
                  justify="left").pack(anchor="w", padx=12)

        def generate():
            new_rules, msg = apply_fn.parse()
            if msg:
                err.set(msg)
                return
            save_dielectric_rules(self.session.library, new_rules)
            try:
                n = self.session.generate_dielectric()
            except LayoutError as exc:
                err.set(str(exc))
                return
            r_layer = new_rules["layer"]
            self.session.library.layers[r_layer].visible = True
            self.refresh_all()
            self.status.set(f"dielectric fill: {n} polygon(s) on "
                            f"{self._layer_label(r_layer)}")
            win.destroy()

        def save_only():
            msg = apply_fn()
            if msg:
                err.set(msg)
            else:
                win.destroy()

        row = ttk.Frame(win)
        row.pack(pady=(8, 12))
        ttk.Button(row, text="Generate", command=generate).pack(side="left", padx=6)
        ttk.Button(row, text="Save Rules", command=save_only).pack(side="left", padx=6)
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="left", padx=6)
        win.bind("<Return>", lambda e: generate())
        win.bind("<Escape>", lambda e: win.destroy())
        return win

    def show_grid_spacing_dialog(self):
        """Setup > Grid Spacing... (also reachable via the toolbar's
        Grid... button): change the minor grid line spacing, shown as
        the always-visible "Grid: N mm" toolbar readout. Also updates
        the snap-to-grid pitch to match, since the drawn grid and the
        snap grid are the same setting -- see
        LayoutCanvas.set_grid_spacing()."""
        win = tk.Toplevel(self)
        win.title("Grid Spacing")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        form = ttk.Frame(win)
        form.pack(padx=12, pady=12)

        ttk.Label(form, text="Grid spacing (mm):").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        spacing_var = tk.DoubleVar(value=dbu_to_mm(self.canvas.grid_dbu))
        spacing_spin = ttk.Spinbox(form, from_=0.001, to=1000.0, increment=0.001, width=10,
                                    textvariable=spacing_var)
        spacing_spin.grid(row=0, column=1, sticky="w", padx=4, pady=4)

        error_var = tk.StringVar(value="")
        ttk.Label(form, textvariable=error_var, foreground="red").grid(
            row=1, column=0, columnspan=2, sticky="w", padx=4
        )

        def apply_and_close():
            try:
                spacing_dbu = mm_to_dbu(float(spacing_var.get()))
            except (tk.TclError, ValueError):
                error_var.set("Grid spacing must be a number.")
                return
            try:
                self.canvas.set_grid_spacing(spacing_dbu)
            except GeometryError as e:
                error_var.set(str(e))
                return
            self._refresh_grid_spacing_var()
            self.status.set(f"grid spacing set to {dbu_to_mm(self.canvas.grid_dbu):g} mm")
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(0, 12))
        ttk.Button(btn_row, text="OK", command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        spacing_spin.focus_set()
        win.bind("<Return>", lambda e: apply_and_close())
        win.bind("<Escape>", lambda e: win.destroy())

    def show_layer_thickness_dialog(self):
        """Setup > Technology Properties... > Thicknesses (also here as a
        standalone window): the fired-film thickness (micron) of each
        printed layer, used by File > Export to FreeCAD (3D Macro).
        Layer-number order is the print/fire order; a blank row means
        "not a physical film" (skipped in 3D). Pre-filled from the saved
        `layer_thickness` tech-param, or the role heuristic
        (hlayout.io.techfile.default_layer_thickness) where unset. Saved
        into library.tech_params, so File > Save GDS / Setup > Save
        Technology File... carries it."""
        return self._setting_window("Layer Thicknesses (3D)",
                                    self._build_layer_thickness_form)

    def _build_layer_thickness_form(self, parent):
        lib = self.session.library
        saved = load_layer_thickness(lib)
        seeded = default_layer_thickness(lib)
        drawn = {sh.layer for c in lib.cells.values() for sh in c.shapes}
        rows_layers = sorted(
            set(drawn) | {int(k) for k in saved} | {int(k) for k in seeded})

        ttk.Label(
            parent,
            text="Fired-film thickness in microns for each printed layer, in "
                 "print order (top row is fired first). Blank = not a physical "
                 "film -- skipped in the 3D export.",
            wraplength=360, justify="left").pack(anchor="w", padx=12, pady=(12, 6))

        if not rows_layers:
            ttk.Label(
                parent,
                text="Nothing is drawn yet -- add some geometry, then set the "
                     "printed-film thicknesses here.",
                wraplength=360, justify="left", foreground="#555").pack(
                anchor="w", padx=12, pady=(0, 8))
            return lambda: None

        list_frame = ttk.Frame(parent)
        list_frame.pack(fill="both", expand=True, padx=(12, 0))
        canvas = tk.Canvas(list_frame, highlightthickness=0, width=320, height=280)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>",
                   lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        ttk.Label(inner, text="Layer", width=22).grid(row=0, column=0, sticky="w")
        ttk.Label(inner, text="µm").grid(row=0, column=1, sticky="w", padx=6)
        vars_by_layer = {}
        for i, n in enumerate(rows_layers, start=1):
            lname = lib.layers[n].name if n < len(lib.layers) else f"L{n}"
            ttk.Label(inner, text=f"L{n}  {lname}", width=22).grid(
                row=i, column=0, sticky="w", pady=1)
            cur = saved.get(str(n), seeded.get(str(n)))
            v = tk.StringVar(value=("" if not cur else f"{float(cur):g}"))
            ttk.Entry(inner, textvariable=v, width=8).grid(
                row=i, column=1, sticky="w", padx=6, pady=1)
            vars_by_layer[n] = v

        ttk.Button(
            parent, text="Reset to heuristic",
            command=lambda: [vars_by_layer[n].set(
                "" if str(n) not in seeded else f"{float(seeded[str(n)]):g}")
                for n in rows_layers]).pack(anchor="w", padx=12, pady=(6, 0))

        def apply_fn():
            out = {}
            for n, v in vars_by_layer.items():
                s = v.get().strip()
                if not s:
                    continue
                try:
                    t = float(s)
                except ValueError:
                    return f"L{n}: {s!r} isn't a number."
                if t < 0:
                    return f"L{n}: thickness can't be negative."
                if t > 0:
                    out[str(n)] = t
            save_layer_thickness(lib, out)
            self.status.set(
                f"layer thicknesses saved ({len(out)} printed film(s))")
            return None

        return apply_fn
