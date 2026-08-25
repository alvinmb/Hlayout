"""Thin-Film Resistor Designer -- Tools > Thin-Film Resistor Designer...

A thin-film design has ONE sheet resistivity for the whole design (see
hlayout.core.thinfilm's module docstring) -- set it once here, then
size as many serpentine or top-hat (dog-bone) resistors against it as
the design needs. This is a leaner, single-resistor-at-a-time tool
compared to Tools > Thick-Film Resistor Designer... (no CSV import, no
multi-resistor batch table) -- it covers sizing, previewing, placing,
and trim-simulating one resistor at a time; batch workflows can follow
later if this proves out.

Geometry (serpentine link/serpentine areas, top-hat bar/pads) comes
from hlayout.core.thinfilm; placement geometry and layer defaults are
Session.generate_serpentine_resistor()/generate_top_hat_resistor()
(hlayout/scripting/api.py), backed by this design's saved rules
(hlayout.io.techfile.load_thin_film_rules()/save_thin_film_rules()).
"""

import tkinter as tk
from tkinter import ttk

from . import dialogs
from .fonts import section_header_font
from .resistor_designer import PlaceResistorDialog, Resistor, Solution, _layer_choices, _layer_label
from .trim_sim_window import open_trim_simulator
from ..core.errors import GeometryError, LayoutError
from ..core.thinfilm import (
    SerpentineSolution,
    TopHatSolution,
    generate_serpentine_geometry,
    generate_top_hat_geometry,
    solve_serpentine,
    solve_top_hat,
)
from ..core.units import dbu_to_mm, mm_to_dbu
from ..io.techfile import load_thin_film_rules, save_thin_film_rules

ACCENT = "#58a6ff"
MUTED = "#8b949e"
BG = "#0d1117"


class PreviewCanvas(tk.Canvas):
    """Small schematic preview of the currently solved candidate --
    same hand-drawn-on-a-plain-Canvas convention as the Trim
    Simulator's HeatmapCanvas/HistoryPlotCanvas (no matplotlib/PIL
    dependency)."""

    def __init__(self, master, **kwargs):
        super().__init__(master, bg=BG, highlightthickness=0, **kwargs)
        self._kind = None
        self._geo = None
        self.bind("<Configure>", lambda e: self._redraw())

    def show_serpentine(self, geo):
        self._kind, self._geo = "serpentine", geo
        self._redraw()

    def show_top_hat(self, geo):
        self._kind, self._geo = "top_hat", geo
        self._redraw()

    def clear(self):
        self._kind = self._geo = None
        self._redraw()

    def _polys(self):
        if self._kind == "serpentine":
            return (
                [("#58a6ff", p) for p in self._geo["link_polys"]]
                + [("#58a6ff", self._geo["bridge_poly"])]
                + [("#3fb950", p) for p in self._geo["serpentine_polys"]]
            )
        return [
            ("#f5c518", self._geo["pad_a_points"]),
            ("#58a6ff", self._geo["bar_points"]),
            ("#f5c518", self._geo["pad_b_points"]),
        ]

    def _redraw(self):
        self.delete("all")
        cw, ch = max(self.winfo_width(), 50), max(self.winfo_height(), 50)
        if self._geo is None:
            self.create_text(cw / 2, ch / 2, text="Solve a resistor to preview it", fill=MUTED)
            return
        entries = self._polys()
        xs = [p[0] for _, poly in entries for p in poly]
        ys = [p[1] for _, poly in entries for p in poly]
        w, h = (max(xs) - min(xs)) or 1, (max(ys) - min(ys)) or 1
        margin = 20
        scale = min((cw - 2 * margin) / w, (ch - 2 * margin) / h)
        mx, my = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2

        def tx(pt):
            x, y = pt
            return cw / 2 + (x - mx) * scale, ch / 2 - (y - my) * scale

        for color, poly in entries:
            pts = [c for p in poly for c in tx(p)]
            self.create_polygon(*pts, fill=color, outline="")


class ThinFilmDesignerWindow(tk.Toplevel):
    def __init__(self, master, app=None):
        super().__init__(master)
        self.app = app
        self.library = app.session.library if app is not None else None
        self.title("Thin-Film Resistor Designer")
        self.geometry("980x640")
        self.minsize(820, 560)

        self.rules = load_thin_film_rules(self.library) if self.library is not None else {}
        self.candidates = []  # list of SerpentineSolution, or [TopHatSolution]
        self.selected_index = None
        self._next_place_y = 0

        self._build()

    # -- UI ---------------------------------------------------------------
    def _build(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Design sheet resistivity (Ω/sq):").pack(side="left")
        self.rho_var = tk.DoubleVar(value=self.rules.get("sheet_rho", 100.0))
        ttk.Spinbox(top, from_=0.001, to=1e7, increment=1, textvariable=self.rho_var, width=12).pack(
            side="left", padx=(4, 12))
        ttk.Button(top, text="Save as Design Default", command=self._save_rho).pack(side="left")
        ttk.Label(top, text="(one Rs for the whole design -- deposited film, not a per-ink choice)",
                  foreground=MUTED).pack(side="left", padx=(8, 0))

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8, pady=8)

        left = ttk.Frame(body, padding=(0, 0, 8, 0))
        body.add(left, weight=1)
        right = ttk.Frame(body)
        body.add(right, weight=1)

        # -- left: form -----------------------------------------------------
        self.type_var = tk.StringVar(value="serpentine")
        type_row = ttk.Frame(left)
        type_row.pack(fill="x", pady=(0, 8))
        ttk.Radiobutton(type_row, text="Serpentine", variable=self.type_var, value="serpentine",
                         command=self._on_type_change).pack(side="left")
        ttk.Radiobutton(type_row, text="Top-Hat (dog-bone)", variable=self.type_var, value="top_hat",
                         command=self._on_type_change).pack(side="left", padx=(12, 0))

        form = ttk.LabelFrame(left, text="Parameters", padding=8)
        form.pack(fill="x")
        ttk.Label(form, text="Ref des:").grid(row=0, column=0, sticky="e", padx=2, pady=2)
        self.ref_des_var = tk.StringVar(value="R1")
        ttk.Entry(form, textvariable=self.ref_des_var, width=10).grid(row=0, column=1, sticky="w", padx=2, pady=2)

        ttk.Label(form, text="Target resistance (Ω):").grid(row=1, column=0, sticky="e", padx=2, pady=2)
        self.target_r_var = tk.DoubleVar(value=10000.0)
        ttk.Spinbox(form, from_=0.001, to=1e12, increment=100, textvariable=self.target_r_var, width=14).grid(
            row=1, column=1, sticky="w", padx=2, pady=2)

        # Serpentine-only fields
        self.trace_width_var = tk.DoubleVar(value=dbu_to_mm(self.rules.get("trace_width", 25)) * 1000)
        self.link_fraction_var = tk.DoubleVar(value=self.rules.get("link_fraction", 0.15) * 100)
        self.corner_factor_var = tk.DoubleVar(value=self.rules.get("corner_factor", 0.5))
        # Top-hat-only fields
        self.bar_width_var = tk.DoubleVar(value=dbu_to_mm(self.rules.get("trace_width", 25)) * 1000)
        self.pad_width_var = tk.DoubleVar(value=0.0)  # 0 => auto (4x bar width)
        self.pad_length_var = tk.DoubleVar(value=0.0)  # 0 => auto (2x bar width)

        self._serp_row0 = 2
        self._serp_fields = []
        self._add_field(form, self._serp_row0, "Trace width (µm):", self.trace_width_var, self._serp_fields)
        self._add_field(form, self._serp_row0 + 1, "Link area (% of squares):", self.link_fraction_var, self._serp_fields)
        self._add_field(form, self._serp_row0 + 2, "Corner factor (0-1):", self.corner_factor_var, self._serp_fields)

        self._tophat_fields = []
        self._add_field(form, self._serp_row0, "Bar width (µm):", self.bar_width_var, self._tophat_fields)
        self._add_field(form, self._serp_row0 + 1, "Pad width (µm, 0=auto):", self.pad_width_var, self._tophat_fields)
        self._add_field(form, self._serp_row0 + 2, "Pad length (µm, 0=auto):", self.pad_length_var, self._tophat_fields)

        ttk.Button(left, text="Solve", command=self._solve).pack(fill="x", pady=(8, 0))

        self.error_var = tk.StringVar(value="")
        ttk.Label(left, textvariable=self.error_var, foreground="#f85149", wraplength=380, justify="left").pack(
            fill="x", pady=(4, 0))

        cand_frame = ttk.LabelFrame(left, text="Candidates", padding=4)
        cand_frame.pack(fill="both", expand=True, pady=(8, 0))
        columns = ("legs", "squares", "resistance", "footprint")
        self.cand_tree = ttk.Treeview(cand_frame, columns=columns, show="headings", height=6, selectmode="browse")
        for col, label, width in (
            ("legs", "Legs", 50), ("squares", "Squares", 70),
            ("resistance", "R (Ω)", 90), ("footprint", "Footprint (µm)", 140),
        ):
            self.cand_tree.heading(col, text=label)
            self.cand_tree.column(col, width=width, anchor="center")
        self.cand_tree.pack(fill="both", expand=True)
        self.cand_tree.bind("<<TreeviewSelect>>", self._on_candidate_select)

        btn_row = ttk.Frame(left)
        btn_row.pack(fill="x", pady=(8, 0))
        ttk.Button(btn_row, text="Place in Layout...", command=self._place_in_layout).pack(side="left")
        ttk.Button(btn_row, text="Trim Simulator...", command=self._open_trim_simulator).pack(side="left", padx=(6, 0))

        self.status_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.status_var, foreground=MUTED, padding=(8, 4)).pack(fill="x")

        # -- right: preview ---------------------------------------------------
        ttk.Label(right, text="PREVIEW", foreground=ACCENT, font=section_header_font(right)).pack(
            anchor="w", padx=4, pady=(4, 0))
        self.preview = PreviewCanvas(right)
        self.preview.pack(fill="both", expand=True, padx=4, pady=4)

        self._on_type_change()

    def _add_field(self, form, row, label, var, group):
        lbl = ttk.Label(form, text=label)
        lbl.grid(row=row, column=0, sticky="e", padx=2, pady=2)
        entry = ttk.Spinbox(form, from_=0.0, to=1e9, increment=1, textvariable=var, width=14)
        entry.grid(row=row, column=1, sticky="w", padx=2, pady=2)
        group.append((lbl, entry))

    def _on_type_change(self):
        is_serp = self.type_var.get() == "serpentine"
        for lbl, entry in self._serp_fields:
            (lbl.grid if is_serp else lbl.grid_remove)()
            (entry.grid if is_serp else entry.grid_remove)()
        for lbl, entry in self._tophat_fields:
            (lbl.grid if not is_serp else lbl.grid_remove)()
            (entry.grid if not is_serp else entry.grid_remove)()
        self.cand_tree.configure(height=6 if is_serp else 1)
        self._clear_candidates()

    # -- solving ------------------------------------------------------------
    def _save_rho(self):
        if self.library is None:
            return
        rules = load_thin_film_rules(self.library)
        rules["sheet_rho"] = self.rho_var.get()
        save_thin_film_rules(self.library, rules)
        self.status_var.set(f"Saved sheet resistivity {self.rho_var.get():g} Ω/sq as this design's default.")

    def _clear_candidates(self):
        self.candidates = []
        self.selected_index = None
        for row in self.cand_tree.get_children():
            self.cand_tree.delete(row)
        self.preview.clear()

    def _solve(self):
        self.error_var.set("")
        self._clear_candidates()
        try:
            rho = self.rho_var.get()
            target_r = self.target_r_var.get()
        except tk.TclError:
            self.error_var.set("Sheet resistivity and target resistance must be numbers.")
            return

        try:
            if self.type_var.get() == "serpentine":
                trace_width = mm_to_dbu(self.trace_width_var.get() / 1000)
                link_fraction = self.link_fraction_var.get() / 100.0
                corner_factor = self.corner_factor_var.get()
                sols = solve_serpentine(
                    target_r_ohm=target_r, sheet_rho_ohm_per_sq=rho, trace_width=trace_width,
                    link_fraction=link_fraction, corner_factor=corner_factor,
                )
                self.candidates = sols
                for i, sol in enumerate(sols):
                    tag = " (recommended)" if sol.recommended else ""
                    self.cand_tree.insert("", "end", iid=str(i), values=(
                        sol.num_legs, f"{sol.squares:.1f}{tag}", f"{sol.resistance_ohm:.1f}",
                        f"{dbu_to_mm(sol.footprint_w) * 1000:.0f} x {dbu_to_mm(sol.footprint_l) * 1000:.0f}",
                    ))
                if sols:
                    self.cand_tree.selection_set("0")
            else:
                bar_width = mm_to_dbu(self.bar_width_var.get() / 1000)
                pad_width = mm_to_dbu(self.pad_width_var.get() / 1000) or None
                pad_length = mm_to_dbu(self.pad_length_var.get() / 1000) or None
                sol = solve_top_hat(
                    target_r_ohm=target_r, sheet_rho_ohm_per_sq=rho, bar_width=bar_width,
                    pad_width=pad_width, pad_length=pad_length,
                )
                self.candidates = [sol]
                self.cand_tree.insert("", "end", iid="0", values=(
                    "-", f"{sol.squares:.1f}", f"{sol.resistance_ohm:.1f}",
                    f"{dbu_to_mm(sol.footprint_w) * 1000:.0f} x {dbu_to_mm(sol.footprint_l) * 1000:.0f}",
                ))
                self.cand_tree.selection_set("0")
        except GeometryError as e:
            self.error_var.set(str(e))

    def _on_candidate_select(self, event=None):
        sel = self.cand_tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        self.selected_index = idx
        sol = self.candidates[idx]
        if isinstance(sol, SerpentineSolution):
            self.preview.show_serpentine(generate_serpentine_geometry(sol))
        else:
            self.preview.show_top_hat(generate_top_hat_geometry(sol))

    def _current_solution(self):
        if self.selected_index is None or not self.candidates:
            return None
        return self.candidates[self.selected_index]

    # -- actions --------------------------------------------------------------
    def _place_in_layout(self):
        sol = self._current_solution()
        if sol is None:
            dialogs.show_info("Place in Layout", "Solve a resistor and pick a candidate first.", parent=self)
            return
        if self.app is None:
            dialogs.show_info("Place in Layout", "This designer isn't attached to a layout window.", parent=self)
            return
        rules = load_thin_film_rules(self.library)
        dlg = PlaceResistorDialog(
            self, self.ref_des_var.get(), library=self.library,
            default_conductor_layer=rules["pad_layer"], default_x=0.0, default_y=self._next_place_y,
        )
        self.wait_window(dlg)
        if dlg.result is None:
            return
        x, y, rotation, pad_layer = dlg.result
        try:
            if isinstance(sol, SerpentineSolution):
                self.app.session.generate_serpentine_resistor(
                    x, y, sol, rotation=rotation, ref_des=self.ref_des_var.get(), pad_layer=pad_layer)
                footprint_w = sol.footprint_w
            else:
                self.app.session.generate_top_hat_resistor(
                    x, y, sol, rotation=rotation, ref_des=self.ref_des_var.get(), pad_layer=pad_layer)
                footprint_w = sol.footprint_w
        except LayoutError as e:
            dialogs.show_error("Place in Layout", str(e), parent=self)
            return
        self.app.refresh_all()
        # Advance the placement cursor downward (more-negative Y) by this
        # resistor's footprint plus a small margin, so the next resistor
        # placed from this dialog doesn't land on top of it.
        self._next_place_y = y - sol.footprint_l - mm_to_dbu(0.5)
        self.status_var.set(f"{self.ref_des_var.get()} placed at ({dbu_to_mm(x):g}, {dbu_to_mm(y):g}) mm")

    def _open_trim_simulator(self):
        sol = self._current_solution()
        if sol is None:
            dialogs.show_info("Trim Simulator", "Solve a resistor and pick a candidate first.", parent=self)
            return
        ref_des = self.ref_des_var.get() or "R1"
        rho = self.rho_var.get()
        if isinstance(sol, SerpentineSolution):
            # The Trim Simulator's engine solves a straight rectangular
            # body (see hlayout.core.trim_sim); a serpentine meander
            # has no such single straight run, so it's approximated
            # here as one "unrolled" bar of the same total squares --
            # same total resistance and the same style of trim
            # response, not a literal model of trimming into a folded
            # meander. Good enough to explore trim behavior; not a
            # substitute for the real thing once actual squares/kerf
            # geometry matters.
            body_length = sol.squares * sol.trace_width
            body_width = sol.trace_width
        else:
            body_length = sol.bar_length
            body_width = sol.bar_width
        resistor = Resistor(ref_des=ref_des, value=sol.resistance_ohm, tolerance="", power_mw=0.0,
                             body_width=body_width)
        solution = Solution(ink_rsh=rho, squares=sol.squares, body_width=body_width,
                             body_length=body_length, aspect_ratio=body_width / body_length if body_length else 0,
                             score=1.0, recommended=True)
        open_trim_simulator(self, resistor, solution)


def open_thin_film_designer(app):
    """Tools > Thin-Film Resistor Designer... . Reuses a single window
    per App instance, mirroring open_resistor_designer() in
    resistor_designer.py -- calling this again while one is already
    open just brings it to the front instead of stacking up
    duplicates."""
    win = getattr(app, "_thin_film_designer_window", None)
    if win is not None and win.winfo_exists():
        win.lift()
        win.focus_set()
        return win
    win = ThinFilmDesignerWindow(app, app=app)
    app._thin_film_designer_window = win
    return win
