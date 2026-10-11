"""Thermal Analysis tool -- Tools > Thermal Analysis...

Setup on the left (what each part dissipates, what each film is made
of, how the substrate is cooled, what counts as too hot), results on
the right (a temperature map and a parts table, hottest first, with
anything over its limit tinted red). Every setup edit is persisted
straight into the design's technology parameters
(hlayout.io.techfile.load_thermal_rules() / save_thermal_rules()), the
same no-separate-Save convention the DRC window uses, so the setup
travels with the design and its technology file.

The analysis itself is hlayout.core.thermal (the solver) driven by
hlayout.core.thermal_build (layout -> model) through
Session.run_thermal(); this window only edits rules and shows results.
numpy, scipy and shapely are imported lazily, on Run, so opening the
window never fails for want of them.
"""
import base64
import time
import tkinter as tk
from tkinter import filedialog, simpledialog, ttk

from . import dialogs
from .fonts import section_header_font
from ..core.errors import LayoutError
from ..io.techfile import DEFAULT_THERMAL, load_thermal_rules, save_thermal_rules

_BOUNDARY_FIELDS = [
    ("substrate_material", "Substrate material", "material"),
    ("substrate_um", "Substrate thickness (um)", "float"),
    ("substrate_layer", "Substrate outline layer (-1 = find by name)", "int"),
    ("base_mode", "Base: fixed temperature or htc", "mode"),
    ("base_temp_c", "Base temperature (C)", "float"),
    ("base_htc", "Base heat-transfer coefficient (W/m2.K)", "float"),
    ("ambient_c", "Ambient temperature (C)", "float"),
    ("top_htc", "Top-surface coefficient (W/m2.K, 0 = sealed)", "float"),
    ("mount_material", "Mounting layer material (blank = none)", "material_or_blank"),
    ("mount_um", "Mounting layer thickness (um)", "float"),
    ("grid_um", "Grid pitch (um, 0 = automatic)", "float"),
]
_LIMIT_FIELDS = [
    ("resistor_max_c", "Resistor film maximum (C)"),
    ("resistor_max_w_mm2", "Resistor power density maximum (W/mm2)"),
    ("die_max_c", "Die junction maximum (C)"),
    ("component_max_c", "Component maximum (C)"),
]
_MAP_PIXELS = 560
_TOP_SURFACE = "Top surface"


def _material_names():
    try:
        from ..core.thermal import MATERIALS
        return list(MATERIALS)
    except ImportError:
        return []


class ThermalWindow(tk.Toplevel):
    """Tools > Thermal Analysis... . ``library`` is the current design's
    Library (rules load from and save to it); ``app`` is the main App,
    whose live session the analysis runs against."""

    def __init__(self, master, library, app=None):
        super().__init__(master)
        self.title("Thermal Analysis")
        self.geometry("1280x820")
        self.library = library
        self.app = app
        self.rules = load_thermal_rules(library)
        self.result = None
        self._photo = None
        self._map_grid = None
        self._map_scale = 1
        self._build()
        self._refresh_powers()
        self._refresh_stack()
        self.bind("<Escape>", lambda e: self.destroy())

    # -- layout ----------------------------------------------------------
    def _build(self):
        toolbar = ttk.Frame(self, padding=6)
        toolbar.pack(fill="x")
        self.run_btn = ttk.Button(toolbar, text="Run Analysis", command=self._run)
        self.run_btn.pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="Report...", command=self._report).pack(side="left", padx=2)
        ttk.Button(toolbar, text="CSV...", command=self._csv).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Draw Contours...",
                   command=self._contours).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Close", command=self.destroy).pack(side="right", padx=2)
        ttk.Button(toolbar, text="About", command=self._about).pack(side="right", padx=2)

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=4, pady=4)
        section_font = section_header_font(self)

        left = ttk.Frame(body)
        ttk.Label(left, text="SETUP", foreground="#58a6ff",
                  font=section_font).pack(anchor="w", padx=4, pady=(4, 0))
        tabs = ttk.Notebook(left)
        tabs.pack(fill="both", expand=True, padx=4, pady=4)
        self._build_powers_tab(tabs)
        self._build_stack_tab(tabs)
        self._build_form_tab(tabs, "Boundary", "boundary", _BOUNDARY_FIELDS)
        self._build_form_tab(tabs, "Limits", "limits",
                             [(k, label, "float") for k, label in _LIMIT_FIELDS])
        body.add(left, weight=2)

        right = ttk.Panedwindow(body, orient="vertical")
        map_frame = ttk.Frame(right)
        head = ttk.Frame(map_frame)
        head.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(head, text="TEMPERATURE MAP", foreground="#f78166",
                  font=section_font).pack(side="left")
        self.slice_var = tk.StringVar(value=_TOP_SURFACE)
        self.slice_box = ttk.Combobox(head, textvariable=self.slice_var, width=28,
                                      state="readonly", values=[_TOP_SURFACE])
        self.slice_box.pack(side="right")
        self.slice_box.bind("<<ComboboxSelected>>", lambda e: self._draw_map())
        ttk.Label(head, text="Slice:").pack(side="right", padx=(0, 4))
        self.map_canvas = tk.Canvas(map_frame, background="#0d1117", highlightthickness=0,
                                    width=_MAP_PIXELS, height=360)
        self.map_canvas.pack(fill="both", expand=True, padx=4, pady=4)
        self.map_canvas.bind("<Motion>", self._on_probe)
        self.map_canvas.bind("<Leave>", lambda e: self.probe_var.set(""))
        foot = ttk.Frame(map_frame)
        foot.pack(fill="x", padx=4)
        self.scale_var = tk.StringVar(value="Run the analysis to see the map.")
        ttk.Label(foot, textvariable=self.scale_var).pack(side="left")
        self.probe_var = tk.StringVar(value="")
        ttk.Label(foot, textvariable=self.probe_var).pack(side="right")
        right.add(map_frame, weight=3)

        table_frame = ttk.Frame(right)
        ttk.Label(table_frame, text="PARTS", foreground="#3fb950",
                  font=section_font).pack(anchor="w", padx=4, pady=(4, 0))
        cols = ("ref", "kind", "power", "density", "tmax", "tavg", "rise", "limit", "status")
        self.part_table = ttk.Treeview(table_frame, columns=cols, show="headings", height=9)
        for col, heading, width, anchor in [
            ("ref", "Ref Des", 90, "w"), ("kind", "Kind", 80, "w"),
            ("power", "Power (mW)", 85, "e"), ("density", "W/mm2", 70, "e"),
            ("tmax", "Max (C)", 70, "e"), ("tavg", "Avg (C)", 70, "e"),
            ("rise", "Rise (C)", 70, "e"), ("limit", "Limit (C)", 70, "e"),
            ("status", "Status", 150, "w"),
        ]:
            self.part_table.heading(col, text=heading)
            self.part_table.column(col, width=width, anchor=anchor)
        self.part_table.tag_configure("bad", background="#e6194b", foreground="#ffffff")
        self.part_table.pack(fill="both", expand=True, padx=4, pady=4)
        self.part_table.bind("<<TreeviewSelect>>", lambda e: self._mark_selected_part())
        right.add(table_frame, weight=2)
        body.add(right, weight=3)

        status = ttk.Frame(self, padding=(6, 3))
        status.pack(fill="x")
        self.status_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.status_var, foreground="#8b949e").pack(side="left")

    def _build_powers_tab(self, tabs):
        frame = ttk.Frame(tabs)
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=4)
        ttk.Button(bar, text="Set Power...", command=self._set_power).pack(side="left", padx=2)
        ttk.Button(bar, text="Clear", command=self._clear_power).pack(side="left", padx=2)
        ttk.Button(bar, text="Refresh", command=self._refresh_powers).pack(side="left", padx=2)
        cols = ("ref", "kind", "power", "source", "cell")
        self.power_table = ttk.Treeview(frame, columns=cols, show="headings", height=14)
        for col, heading, width, anchor in [
            ("ref", "Ref Des", 90, "w"), ("kind", "Kind", 80, "w"),
            ("power", "Power (mW)", 85, "e"), ("source", "From", 80, "w"),
            ("cell", "Cell", 110, "w"),
        ]:
            self.power_table.heading(col, text=heading)
            self.power_table.column(col, width=width, anchor=anchor)
        self.power_table.pack(fill="both", expand=True)
        self.power_table.bind("<Double-1>", lambda e: self._set_power())
        ttk.Label(frame, wraplength=420, justify="left", foreground="#8b949e", text=(
            "A resistor with no power set here uses its Resistor Designer power, "
            "matched by reference designator; 'cell' means no row had that name, so "
            "the row its cell was generated from was used. "
            "A die or component is only built into the model once it has a power."
        )).pack(anchor="w", pady=4)
        tabs.add(frame, text="Powers")

    def _build_stack_tab(self, tabs):
        frame = ttk.Frame(tabs)
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=4)
        ttk.Button(bar, text="Set Material...",
                   command=self._set_material).pack(side="left", padx=2)
        ttk.Button(bar, text="Role Default",
                   command=self._clear_material).pack(side="left", padx=2)
        cols = ("layer", "role", "material", "k", "thick")
        self.stack_table = ttk.Treeview(frame, columns=cols, show="headings", height=14)
        for col, heading, width, anchor in [
            ("layer", "Layer", 170, "w"), ("role", "Role", 80, "w"),
            ("material", "Material", 100, "w"), ("k", "k (W/m.K)", 75, "e"),
            ("thick", "Thick (um)", 75, "e"),
        ]:
            self.stack_table.heading(col, text=heading)
            self.stack_table.column(col, width=width, anchor=anchor)
        self.stack_table.pack(fill="both", expand=True)
        self.stack_table.bind("<Double-1>", lambda e: self._set_material())
        ttk.Label(frame, wraplength=420, justify="left", foreground="#8b949e", text=(
            "Films are stacked in layer-number order, the same print order FreeCAD export "
            "uses. Thicknesses come from Setup > Technology Properties > Thicknesses. "
            "Conductivities are approximate starting values: replace them with your "
            "paste and substrate data."
        )).pack(anchor="w", pady=4)
        tabs.add(frame, text="Stack")

    def _build_form_tab(self, tabs, title, section, fields):
        frame = ttk.Frame(tabs, padding=8)
        if not hasattr(self, "_form_vars"):
            self._form_vars = {}
        for row, (key, label, kind) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=str(self.rules[section][key]))
            if kind in ("material", "material_or_blank"):
                values = ([""] if kind == "material_or_blank" else []) + _material_names()
                w = ttk.Combobox(frame, textvariable=var, values=values, width=16)
            elif kind == "mode":
                w = ttk.Combobox(frame, textvariable=var, values=["fixed", "htc"], width=16,
                                 state="readonly")
            else:
                w = ttk.Entry(frame, textvariable=var, width=18)
            w.grid(row=row, column=1, sticky="w", padx=8, pady=3)
            self._form_vars[(section, key)] = var
        ttk.Button(frame, text="Apply",
                   command=lambda: self._apply_form(section)).grid(
            row=len(fields), column=1, sticky="w", padx=8, pady=10)
        tabs.add(frame, text=title)

    # -- setup edits -----------------------------------------------------
    def _persist(self):
        save_thermal_rules(self.library, self.rules)
        self.rules = load_thermal_rules(self.library)

    def _apply_form(self, section):
        for (sec, key), var in self._form_vars.items():
            if sec != section:
                continue
            text = var.get().strip()
            default = DEFAULT_THERMAL[section][key]
            if isinstance(default, str):
                if key in ("substrate_material", "mount_material") and text:
                    try:
                        from ..core.thermal import material_k
                        material_k(text)
                    except LayoutError as e:
                        dialogs.show_error("Thermal Analysis", str(e), parent=self)
                        return
                    except ImportError:
                        pass
                self.rules[section][key] = text
            else:
                try:
                    self.rules[section][key] = type(default)(float(text))
                except ValueError:
                    dialogs.show_error("Thermal Analysis",
                                       f"{key}: {text!r} is not a number.", parent=self)
                    return
        self._persist()
        for (sec, key), var in self._form_vars.items():
            if sec == section:
                var.set(str(self.rules[section][key]))
        self.status_var.set(f"{section.capitalize()} settings saved to the technology file")
        if section == "boundary":
            self._refresh_stack()

    def _session(self):
        return self.app.session if self.app is not None else None

    def _refresh_powers(self):
        self.power_table.delete(*self.power_table.get_children())
        session = self._session()
        if session is None or session.current_cell_name is None:
            return
        try:
            parts = session.thermal_parts()
        except (LayoutError, ImportError) as e:
            self.status_var.set(str(e))
            return
        for p in parts:
            self.power_table.insert("", "end", iid=p["ref_des"], values=(
                p["ref_des"], p["kind"], f"{p['power_mw']:.1f}", p["source"], p["cell"]))

    def _selected_ref(self):
        sel = self.power_table.selection()
        return sel[0] if sel else None

    def _set_power(self):
        ref = self._selected_ref()
        if ref is None:
            dialogs.show_info("Set Power", "Select a part first.", parent=self)
            return
        current = self.power_table.set(ref, "power")
        value = simpledialog.askfloat("Set Power", f"Power dissipated by {ref} (mW):",
                                      initialvalue=float(current), minvalue=0.0, parent=self)
        if value is None:
            return
        self.rules["powers"][ref] = value
        self._persist()
        self._refresh_powers()

    def _clear_power(self):
        ref = self._selected_ref()
        if ref is None:
            return
        self.rules["powers"].pop(ref, None)
        self._persist()
        self._refresh_powers()

    def _refresh_stack(self):
        self.stack_table.delete(*self.stack_table.get_children())
        try:
            from ..core.thermal import material_k
            from ..core.thermal_build import layer_roles
            from ..io.techfile import (_DEFAULT_FILM_UM, default_layer_thickness,
                                       load_layer_thickness)
        except ImportError as e:
            self.status_var.set(f"Thermal analysis needs numpy, scipy and shapely ({e})")
            return
        bc = self.rules["boundary"]
        try:
            sub_k = f"{material_k(bc['substrate_material']):g}"
        except LayoutError:
            sub_k = "?"
        self.stack_table.insert("", "end", iid="substrate", values=(
            "Substrate", "substrate", bc["substrate_material"], sub_k,
            f"{bc['substrate_um']:g}"))
        roles = layer_roles(self.library)
        thick = load_layer_thickness(self.library) or default_layer_thickness(self.library)
        used = {sh.layer for cell in self.library.cells.values() for sh in cell.shapes}
        for n in sorted(roles):
            if n not in used:
                continue
            role = roles[n]
            mat = self.rules["materials"].get(str(n), role)
            try:
                k = f"{material_k(mat):g}"
            except LayoutError:
                k = "?"
            t = thick.get(str(n), 0) or _DEFAULT_FILM_UM.get(role, 10)
            self.stack_table.insert("", "end", iid=str(n), values=(
                f"{n}: {self.library.layers[n].name}", role, mat, k, f"{float(t):g}"))

    def _set_material(self):
        sel = self.stack_table.selection()
        if not sel:
            dialogs.show_info("Set Material", "Select a layer first.", parent=self)
            return
        if sel[0] == "substrate":
            dialogs.show_info("Set Material",
                              "Set the substrate material on the Boundary tab.", parent=self)
            return
        names = _material_names()
        current = self.stack_table.set(sel[0], "material")
        text = simpledialog.askstring(
            "Set Material",
            "Material name, or a conductivity in W/m.K.\n\nNames: " + ", ".join(names),
            initialvalue=current, parent=self)
        if not text:
            return
        try:
            from ..core.thermal import material_k
            material_k(text.strip())
        except LayoutError as e:
            dialogs.show_error("Set Material", str(e), parent=self)
            return
        self.rules["materials"][sel[0]] = text.strip().lower()
        self._persist()
        self._refresh_stack()

    def _clear_material(self):
        sel = self.stack_table.selection()
        if sel and sel[0] != "substrate":
            self.rules["materials"].pop(sel[0], None)
            self._persist()
            self._refresh_stack()

    # -- run and results ---------------------------------------------------
    def _run(self):
        session = self._session()
        if session is None:
            dialogs.show_info("Thermal Analysis",
                              "This window isn't attached to a layout window.", parent=self)
            return
        self.status_var.set("Solving...")
        self.run_btn.state(["disabled"])
        self.update_idletasks()
        started = time.time()
        try:
            result = session.run_thermal()
        except ImportError as e:
            dialogs.show_error(
                "Thermal Analysis",
                "Thermal analysis needs numpy, scipy and shapely, which aren't all "
                f"installed in this Python environment ({e}).\n\nInstall them with:\n\n"
                "    pip install numpy scipy shapely\n\nthen try again.", parent=self)
            self.status_var.set("")
            return
        except LayoutError as e:
            dialogs.show_error("Thermal Analysis", str(e), parent=self)
            self.status_var.set("")
            return
        finally:
            self.run_btn.state(["!disabled"])
        self.result = result
        elapsed = time.time() - started
        self.slice_box.configure(values=[_TOP_SURFACE] + result.slice_names())
        if self.slice_var.get() not in self.slice_box.cget("values"):
            self.slice_var.set(_TOP_SURFACE)
        self._fill_parts()
        self._draw_map()
        m = result.model
        bad = len(result.failures)
        self.status_var.set(
            f"{'All parts within limits' if not bad else str(bad) + ' part(s) OVER LIMIT'}"
            f"  |  hottest {result.max_temp:.1f} C  |  {result.power_in * 1000:.1f} mW in, "
            f"balance error {result.energy_error * 100:.3f}%  |  {m.nx} x {m.ny} at "
            f"{m.pitch_um:g} um, {result.unknowns} unknowns, {elapsed:.1f} s")
        if result.warnings:
            dialogs.show_scrollable_text("Thermal Analysis: notes", result.warnings,
                                         parent=self)

    def _fill_parts(self):
        self.part_table.delete(*self.part_table.get_children())
        rows = sorted(self.result.parts,
                      key=lambda p: (p.ok, -(p.t_max if p.t_max == p.t_max else -1e9)))
        for p in rows:
            status = "OK"
            if not p.ok:
                status = ("over temperature" if p.limit is not None and p.t_max > p.limit
                          else "over power density")
            self.part_table.insert("", "end", iid=p.ref_des, tags=() if p.ok else ("bad",),
                                   values=(p.ref_des, p.kind, f"{p.power_mw:.1f}",
                                           f"{p.density_w_mm2:.3f}", f"{p.t_max:.1f}",
                                           f"{p.t_avg:.1f}", f"{p.rise:.1f}",
                                           "" if p.limit is None else f"{p.limit:.0f}",
                                           status))

    def _current_grid(self):
        which = self.slice_var.get()
        if which == _TOP_SURFACE:
            return self.result.surface_temps()
        return self.result.slice_temps(which)

    def _draw_map(self):
        if self.result is None:
            return
        import numpy as np
        from ..core.thermal import rgb_to_png, temps_to_rgb
        grid = self._current_grid()
        self._map_grid = grid
        finite = np.isfinite(grid)
        if not finite.any():
            self.map_canvas.delete("all")
            self.scale_var.set("This slice has no material.")
            return
        t_min, t_max = float(np.nanmin(grid)), float(np.nanmax(grid))
        rgb = temps_to_rgb(grid, t_min, t_max, background=(13, 17, 23))
        self.update_idletasks()
        avail_w = max(self.map_canvas.winfo_width(), 200)
        avail_h = max(self.map_canvas.winfo_height(), 150)
        self._map_scale = max(1, min(avail_w // rgb.shape[1], avail_h // rgb.shape[0]))
        png = rgb_to_png(rgb, self._map_scale)
        self._photo = tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
        self.map_canvas.delete("all")
        self.map_canvas.create_image(0, 0, image=self._photo, anchor="nw", tags="map")
        self.scale_var.set(f"{t_min:.1f} C (dark blue) to {t_max:.1f} C (red)")
        self._mark_selected_part()

    def _on_probe(self, event):
        if self._map_grid is None:
            return
        ny, nx = self._map_grid.shape
        i = event.x // self._map_scale
        j = ny - 1 - event.y // self._map_scale       # image row 0 is the top
        if 0 <= i < nx and 0 <= j < ny:
            t = self._map_grid[j, i]
            x, y = self.result.model.cell_center(j, i)
            if t == t:
                self.probe_var.set(f"({x / 1000:.2f}, {y / 1000:.2f}) mm   {t:.1f} C")
            else:
                self.probe_var.set(f"({x / 1000:.2f}, {y / 1000:.2f}) mm   no material")
        else:
            self.probe_var.set("")

    def _mark_selected_part(self):
        self.map_canvas.delete("mark")
        sel = self.part_table.selection()
        if not sel or self.result is None or self._map_grid is None:
            return
        import numpy as np
        part = next((p for p in self.result.model.parts if p.ref_des == sel[0]), None)
        if part is None or not part.mask.any():
            return
        js, is_ = np.nonzero(part.mask)
        ny = part.mask.shape[0]
        s = self._map_scale
        self.map_canvas.create_rectangle(
            int(is_.min()) * s - 2, (ny - 1 - int(js.max())) * s - 2,
            (int(is_.max()) + 1) * s + 2, (ny - int(js.min())) * s + 2,
            outline="#ffffff", width=2, tags="mark")

    # -- outputs -----------------------------------------------------------
    def _need_result(self):
        if self.result is None:
            dialogs.show_info("Thermal Analysis", "Run the analysis first.", parent=self)
            return False
        return True

    def _report(self):
        if not self._need_result():
            return
        path = filedialog.asksaveasfilename(parent=self, title="Save Thermal Report",
                                            defaultextension=".html",
                                            filetypes=[("HTML report", "*.html")])
        if path:
            self._session().export_thermal_report(self.result, path)
            self.status_var.set(f"Report written to {path}")

    def _csv(self):
        if not self._need_result():
            return
        path = filedialog.asksaveasfilename(parent=self, title="Save Parts Table",
                                            defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")])
        if path:
            self._session().export_thermal_csv(self.result, path)
            self.status_var.set(f"Parts table written to {path}")

    def _contours(self):
        if not self._need_result():
            return
        layer = simpledialog.askstring(
            "Draw Contours", "Layer (number or name) to draw the isotherms on:", parent=self)
        if not layer:
            return
        import numpy as np
        from ..core.thermal import nice_contour_step
        grid = self._current_grid()
        if not np.isfinite(grid).any():
            dialogs.show_info("Draw Contours", "The displayed slice has no material.",
                              parent=self)
            return
        lo, hi = float(np.nanmin(grid)), float(np.nanmax(grid))
        # Suggest a step that fits THIS result: a fixed 5 C default draws
        # nothing on a board whose whole range is a couple of degrees.
        step = simpledialog.askfloat(
            "Draw Contours",
            f"Temperatures span {lo:.2f} to {hi:.2f} C.\nContour step (C):",
            initialvalue=nice_contour_step(lo, hi), minvalue=0.0001, parent=self)
        if not step:
            return
        which = None if self.slice_var.get() == _TOP_SURFACE else self.slice_var.get()
        session = self._session()
        try:
            n = session.draw_thermal_contours(self.result, layer.strip(), step=step,
                                              which=which)
            if n:
                # contours on a hidden layer look exactly like no output
                session.set_layer_style(layer.strip(), visible=True)
        except LayoutError as e:
            dialogs.show_error("Draw Contours", str(e), parent=self)
            return
        if n == 0:
            dialogs.show_info(
                "Draw Contours",
                f"No contours were drawn: the temperatures only span {lo:.2f} to {hi:.2f} C, "
                f"which is less than one {step:g} C step.\n\nTry a step of "
                f"{nice_contour_step(lo, hi):g} C.", parent=self)
            return
        if self.app is not None:
            self.app.refresh_all()
        self.status_var.set(f"Drew {n} contour path(s) every {step:g} C on layer "
                            f"{layer.strip()} (Ctrl+Z undoes them)")

    def _about(self):
        dialogs.show_info("About Thermal Analysis", (
            "Steady-state heat conduction through the substrate and printed films, "
            "solved on a regular grid with each part's power injected where it sits.\n\n"
            "Heat leaves through the base of the substrate (and weakly through the top "
            "surface). Conductivity is constant with temperature, components are simple "
            "blocks, and conductor self-heating is not modelled.\n\n"
            "Results are only as good as the material values on the Stack tab: treat "
            "them as a design-stage estimate and confirm marginal parts by measurement."
        ), parent=self)


def open_thermal_window(app):
    """Tools > Thermal Analysis... . One window per App instance --
    calling this again while it's open just brings it to the front."""
    win = getattr(app, "_thermal_window", None)
    if win is not None and win.winfo_exists():
        win.lift()
        win.focus_set()
        return win
    win = ThermalWindow(app, app.session.library, app=app)
    app._thermal_window = win
    return win
