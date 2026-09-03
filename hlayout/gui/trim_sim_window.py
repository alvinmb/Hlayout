"""Resistor Laser-Trim Simulator -- reached from the Thick-Film
Resistor Designer's "Trim Simulator..." toolbar button.

This is the GUI front end for hlayout.core.trim_sim.TrimSimulator: it
runs a plunge cut (optionally continued as an L-cut) against a chosen
Resistor Designer Solution's body_width/body_length/ink_rsh, and
displays the resulting resistance/sensitivity curve alongside the
current-density/power heat map as the cut advances -- see that
module's docstring for the physics and the modeling assumptions/
limitations.

numpy and scipy (see requirements.txt) are only imported lazily, in
open_trim_simulator() and the run handlers below -- not at module load
time -- so the rest of the application doesn't depend on them being
installed; a user who hasn't installed them yet gets a plain error
dialog telling them what to `pip install`, not an import crash.
"""
import math
import tkinter as tk
from tkinter import ttk

from . import dialogs
from .fonts import section_header_font

ACCENT = "#58a6ff"
MUTED = "#8b949e"
BG = "#0d1117"


def open_trim_simulator(master, resistor, solution):
    """Entry point called by Resistor Designer's "Trim Simulator..."
    button with the currently selected Resistor and its chosen
    Solution."""
    try:
        import numpy  # noqa: F401
        import scipy  # noqa: F401
    except ImportError as e:
        dialogs.show_error(
            "Trim Simulator",
            "The Trim Simulator needs numpy and scipy, which aren't "
            f"installed in this Python environment ({e}).\n\n"
            "Install them with:\n\n    pip install numpy scipy\n\n"
            "then try again.",
            parent=master,
        )
        return
    TrimSimulatorWindow(master, resistor, solution)


def _heat_color(t: float) -> str:
    """A simple black -> red -> yellow -> white ramp (t in 0..1)."""
    t = max(0.0, min(1.0, t))
    if t < 1 / 3:
        u = t * 3
        r, g, b = int(255 * u), 0, 0
    elif t < 2 / 3:
        u = (t - 1 / 3) * 3
        r, g, b = 255, int(255 * u), 0
    else:
        u = (t - 2 / 3) * 3
        r, g, b = 255, 255, int(255 * u)
    return f"#{r:02x}{g:02x}{b:02x}"


class HeatmapCanvas(tk.Canvas):
    """Renders a (ny, nx) power-density grid as a coarse heat map, with
    the cut path overlaid. Built from a plain Tk PhotoImage (nearest-
    neighbour zoomed to fill the canvas) -- no matplotlib/PIL
    dependency."""

    def __init__(self, master, **kwargs):
        super().__init__(master, bg=BG, highlightthickness=0, **kwargs)
        self._photo = None
        self._grid = None
        self._length_um = 1.0
        self._width_um = 1.0
        self._path = []
        self.bind("<Configure>", lambda e: self._redraw())

    def show(self, grid, length_um, width_um, path_points):
        self._grid = grid
        self._length_um = max(length_um, 1e-9)
        self._width_um = max(width_um, 1e-9)
        self._path = path_points
        self._redraw()

    def _redraw(self):
        self.delete("all")
        cw = max(self.winfo_width(), 50)
        ch = max(self.winfo_height(), 50)
        if self._grid is None:
            self.create_text(cw / 2, ch / 2, text="Run a plunge cut to see the heat map",
                              fill=MUTED)
            return

        grid = self._grid
        ny, nx = grid.shape
        vmax = float(grid.max())
        # master=self pins the image to this widget's Tk interpreter --
        # otherwise it binds to tk._default_root and create_image() can
        # raise `image "pyimageN" doesn't exist` when a second Tk root
        # exists (see LayoutCanvas._raster_grid_pure for the same fix).
        img = tk.PhotoImage(master=self, width=nx, height=ny)
        if vmax > 0:
            for j in range(ny):
                row = grid[j]
                colors = " ".join(_heat_color(float(v) / vmax) for v in row)
                img.put("{" + colors + "}", to=(0, j))

        margin = 30
        avail_w = max(cw - 2 * margin, 10)
        avail_h = max(ch - 2 * margin, 10)
        zoom = max(1, min(int(avail_w / nx) or 1, int(avail_h / ny) or 1))
        img_big = img.zoom(zoom, zoom)
        self._photo = img_big  # keep a reference -- Tk drops unreferenced PhotoImages
        disp_w, disp_h = nx * zoom, ny * zoom
        ox = (cw - disp_w) / 2
        oy = (ch - disp_h) / 2
        self.create_image(ox, oy, anchor="nw", image=self._photo)
        self.create_rectangle(ox, oy, ox + disp_w, oy + disp_h, outline=MUTED)

        if len(self._path) >= 2:
            pts = []
            for x_um, y_um in self._path:
                px = ox + (x_um / self._length_um) * disp_w
                py = oy + (y_um / self._width_um) * disp_h
                pts.extend([px, py])
            self.create_line(*pts, fill="#00e5ff", width=2)


class HistoryPlotCanvas(tk.Canvas):
    """Resistance (solid) and sensitivity dR/dprogress (dashed) vs
    cumulative cut progress, hand-drawn on a plain Canvas -- same
    styling convention as ARDiagram elsewhere in this window."""

    def __init__(self, master, **kwargs):
        super().__init__(master, bg=BG, highlightthickness=0, **kwargs)
        self._history = []
        self._marker_index = None
        self.bind("<Configure>", lambda e: self._redraw())

    def show(self, history, marker_index=None):
        self._history = history
        self._marker_index = marker_index
        self._redraw()

    def _redraw(self):
        self.delete("all")
        cw = max(self.winfo_width(), 50)
        ch = max(self.winfo_height(), 50)
        hist = self._history
        if not hist:
            self.create_text(cw / 2, ch / 2, text="Run a plunge cut to see this plot", fill=MUTED)
            return

        # margin_t/margin_b leave room for the axis-label text drawn just
        # outside the plot rectangle below (a too-small margin here was
        # exactly the bug reported: text at the top of the plot got
        # clipped clean off by the canvas's own edge, since Tk draws
        # nothing outside a widget's allocated pixel area).
        margin_l, margin_r, margin_t, margin_b = 60, 20, 34, 40
        plot_w = max(cw - margin_l - margin_r, 10)
        plot_h = max(ch - margin_t - margin_b, 10)

        finite_r = [h.resistance_ohm for h in hist if math.isfinite(h.resistance_ohm)]
        r_max = max(finite_r) if finite_r else 1.0
        sens = [h.sensitivity_ohm_per_um for h in hist if h.sensitivity_ohm_per_um is not None]
        s_max = max(sens) if sens else 1.0
        p_max = max((h.cut_progress_um for h in hist), default=1.0) or 1.0

        def xpix(p):
            return margin_l + (p / p_max) * plot_w

        def ypix_r(r):
            r = min(r, r_max)
            return margin_t + plot_h - (r / r_max) * plot_h if r_max > 0 else margin_t + plot_h

        def ypix_s(s):
            return margin_t + plot_h - (s / s_max) * plot_h if s_max > 0 else margin_t + plot_h

        self.create_line(margin_l, margin_t, margin_l, margin_t + plot_h, fill=MUTED)
        self.create_line(margin_l, margin_t + plot_h, margin_l + plot_w, margin_t + plot_h, fill=MUTED)
        self.create_text(margin_l, margin_t - 10, text=f"R, max {r_max:.3g} Ω",
                          fill="#f5c518", anchor="sw")
        self.create_text(margin_l + plot_w, margin_t - 10,
                          text=f"dR/dx, max {s_max:.3g} Ω/µm", fill="#46f0f0", anchor="se")
        self.create_text(margin_l + plot_w / 2, margin_t + plot_h + 20,
                          text="cut progress (µm)", fill=MUTED, anchor="n")

        pts = []
        for h in hist:
            if not math.isfinite(h.resistance_ohm):
                if len(pts) >= 4:
                    self.create_line(*pts, fill="#f5c518", width=2)
                pts = []
                continue
            pts.extend([xpix(h.cut_progress_um), ypix_r(h.resistance_ohm)])
        if len(pts) >= 4:
            self.create_line(*pts, fill="#f5c518", width=2)

        pts = []
        for h in hist:
            if h.sensitivity_ohm_per_um is None:
                continue
            pts.extend([xpix(h.cut_progress_um), ypix_s(h.sensitivity_ohm_per_um)])
        if len(pts) >= 4:
            self.create_line(*pts, fill="#46f0f0", width=1, dash=(3, 2))

        if self._marker_index is not None and 0 <= self._marker_index < len(hist):
            mx = xpix(hist[self._marker_index].cut_progress_um)
            self.create_line(mx, margin_t, mx, margin_t + plot_h, fill="#ffffff", dash=(2, 2))


class TrimSimulatorWindow(tk.Toplevel):
    def __init__(self, master, resistor, solution):
        super().__init__(master)
        self.resistor = resistor
        self.solution = solution
        self.title(f"Trim Simulator — {resistor.ref_des}")
        self.geometry("1300x900")
        # Below this, the control panels alone (Plunge Cut + L-Cut boxes,
        # status line, scrub bar) start crowding out the plot area --
        # see the "graph text is hidden by the GUI" report this fixes.
        self.minsize(1000, 720)
        self.sim = None  # core.trim_sim.TrimSimulator, created on first Run Plunge
        self._build()

    # -- UI ------------------------------------------------------------
    def _build(self):
        sol = self.solution
        header = ttk.Frame(self, padding=8)
        header.pack(fill="x")
        ttk.Label(
            header,
            text=f"{self.resistor.ref_des}   {self.resistor.value_label}   "
                 f"{sol.ink_rsh:g} Ω/□   {sol.squares:.3f}□   "
                 f"L={sol.body_length:g} µm   W={sol.body_width:g} µm",
            foreground=ACCENT, font=section_header_font(header),
        ).pack(anchor="w")

        controls = ttk.Frame(self, padding=(8, 0))
        controls.pack(fill="x")

        plunge_box = ttk.LabelFrame(controls, text="Plunge Cut", padding=6)
        plunge_box.pack(side="left", padx=(0, 8), fill="y")
        self.entry_edge_var = tk.StringVar(value="bottom")
        ttk.Label(plunge_box, text="Entry edge:").grid(row=0, column=0, sticky="e", padx=2, pady=2)
        ttk.Combobox(plunge_box, textvariable=self.entry_edge_var, state="readonly",
                     values=["bottom", "top"], width=8).grid(row=0, column=1, sticky="w", padx=2, pady=2)
        self.kerf_var = tk.DoubleVar(value=40.0)
        ttk.Label(plunge_box, text="Kerf (µm):").grid(row=1, column=0, sticky="e", padx=2, pady=2)
        ttk.Spinbox(plunge_box, from_=1, to=500, increment=1, textvariable=self.kerf_var,
                    width=8).grid(row=1, column=1, sticky="w", padx=2, pady=2)
        self.depth_pct_var = tk.DoubleVar(value=95.0)
        ttk.Label(plunge_box, text="Depth (% of width):").grid(row=2, column=0, sticky="e", padx=2, pady=2)
        ttk.Spinbox(plunge_box, from_=1, to=100, increment=1, textvariable=self.depth_pct_var,
                    width=8).grid(row=2, column=1, sticky="w", padx=2, pady=2)
        self.plunge_steps_var = tk.IntVar(value=40)
        ttk.Label(plunge_box, text="Steps:").grid(row=3, column=0, sticky="e", padx=2, pady=2)
        ttk.Spinbox(plunge_box, from_=2, to=200, increment=1, textvariable=self.plunge_steps_var,
                    width=8).grid(row=3, column=1, sticky="w", padx=2, pady=2)
        self.voltage_var = tk.DoubleVar(value=1.0)
        ttk.Label(plunge_box, text="Operating V:").grid(row=4, column=0, sticky="e", padx=2, pady=2)
        ttk.Spinbox(plunge_box, from_=0.01, to=1000, increment=0.5, textvariable=self.voltage_var,
                    width=8).grid(row=4, column=1, sticky="w", padx=2, pady=2)
        self.run_btn = ttk.Button(plunge_box, text="Run Plunge", command=self._run_plunge)
        self.run_btn.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        lcut_box = ttk.LabelFrame(controls, text="L-Cut (optional, after a plunge)", padding=6)
        lcut_box.pack(side="left", padx=(0, 8), fill="y")
        self.lcut_len_var = tk.DoubleVar(value=200.0)
        ttk.Label(lcut_box, text="Turn length (µm):").grid(row=0, column=0, sticky="e", padx=2, pady=2)
        ttk.Spinbox(lcut_box, from_=1, to=100000, increment=10, textvariable=self.lcut_len_var,
                    width=8).grid(row=0, column=1, sticky="w", padx=2, pady=2)
        self.lcut_dir_var = tk.StringVar(value="+X")
        ttk.Label(lcut_box, text="Direction:").grid(row=1, column=0, sticky="e", padx=2, pady=2)
        ttk.Combobox(lcut_box, textvariable=self.lcut_dir_var, state="readonly",
                     values=["+X", "-X"], width=8).grid(row=1, column=1, sticky="w", padx=2, pady=2)
        self.lcut_steps_var = tk.IntVar(value=20)
        ttk.Label(lcut_box, text="Steps:").grid(row=2, column=0, sticky="e", padx=2, pady=2)
        ttk.Spinbox(lcut_box, from_=2, to=200, increment=1, textvariable=self.lcut_steps_var,
                    width=8).grid(row=2, column=1, sticky="w", padx=2, pady=2)
        self.lcut_btn = ttk.Button(lcut_box, text="Add L-Cut", command=self._run_l_cut, state="disabled")
        self.lcut_btn.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        actions = ttk.Frame(controls)
        actions.pack(side="left", fill="y")
        ttk.Button(actions, text="Reset", command=self._reset).pack(fill="x", pady=2)

        self.status_var = tk.StringVar(value="Set your plunge parameters and click Run Plunge.")
        ttk.Label(self, textvariable=self.status_var, foreground=MUTED, padding=(8, 4)).pack(anchor="w")

        scrub_row = ttk.Frame(self, padding=(8, 0))
        scrub_row.pack(fill="x")
        ttk.Label(scrub_row, text="Scrub step:").pack(side="left")
        self.scrub_var = tk.IntVar(value=0)
        self.scrub_scale = ttk.Scale(scrub_row, from_=0, to=0, variable=self.scrub_var,
                                      orient="horizontal", command=self._on_scrub)
        self.scrub_scale.pack(side="left", fill="x", expand=True, padx=6)
        self.scrub_scale.state(["disabled"])

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=4, pady=4)

        left = ttk.Frame(body)
        ttk.Label(left, text="CURRENT-DENSITY / POWER HEAT MAP", foreground=ACCENT,
                  font=section_header_font(left)).pack(anchor="w", padx=4, pady=(4, 0))
        # Explicit minimum height (not just fill=True/expand=True) so
        # the Panedwindow reserves real room for these on first layout
        # instead of starting them near-zero-height and only growing
        # once the user manually drags the window/sash bigger.
        self.heatmap = HeatmapCanvas(left, height=340)
        self.heatmap.pack(fill="both", expand=True, padx=4, pady=4)
        body.add(left, weight=1)

        right = ttk.Frame(body)
        ttk.Label(right, text="RESISTANCE / SENSITIVITY vs CUT PROGRESS", foreground=ACCENT,
                  font=section_header_font(right)).pack(anchor="w", padx=4, pady=(4, 0))
        self.history_plot = HistoryPlotCanvas(right, height=340)
        self.history_plot.pack(fill="both", expand=True, padx=4, pady=4)
        body.add(right, weight=1)

    # -- actions ---------------------------------------------------------
    def _run_plunge(self):
        from ..core.trim_sim import TrimSimError, TrimSimulator
        length_um = float(self.solution.body_length)  # DBU == microns in this project
        width_um = float(self.solution.body_width)
        try:
            self.sim = TrimSimulator(
                length_um=length_um, width_um=width_um,
                sheet_ohms_per_sq=self.solution.ink_rsh,
                operating_voltage=self.voltage_var.get(),
            )
            depth_um = width_um * (self.depth_pct_var.get() / 100.0)
            self.sim.add_plunge(
                entry_edge=self.entry_edge_var.get(),
                kerf_um=self.kerf_var.get(),
                depth_um=depth_um,
                steps=int(self.plunge_steps_var.get()),
            )
        except TrimSimError as e:
            dialogs.show_error("Trim Simulator", str(e), parent=self)
            return
        self.lcut_btn.configure(state="disabled" if self.sim.is_open() else "normal")
        self._refresh_after_run()

    def _run_l_cut(self):
        from ..core.trim_sim import TrimSimError
        if self.sim is None:
            return
        direction = 1 if self.lcut_dir_var.get() == "+X" else -1
        try:
            self.sim.add_l_cut(
                turn_length_um=self.lcut_len_var.get(),
                direction=direction,
                steps=int(self.lcut_steps_var.get()),
            )
        except TrimSimError as e:
            dialogs.show_error("Trim Simulator", str(e), parent=self)
            return
        if self.sim.is_open():
            self.lcut_btn.configure(state="disabled")
        self._refresh_after_run()

    def _reset(self):
        self.sim = None
        self.lcut_btn.configure(state="disabled")
        self.scrub_scale.configure(to=0)
        self.scrub_scale.state(["disabled"])
        self.scrub_var.set(0)
        self.heatmap.show(None, 1.0, 1.0, [])
        self.history_plot.show([])
        self.status_var.set("Set your plunge parameters and click Run Plunge.")

    def _refresh_after_run(self):
        n = len(self.sim.history)
        self.scrub_scale.configure(to=max(n - 1, 0))
        self.scrub_scale.state(["!disabled"])
        self.scrub_var.set(n - 1)
        self._show_step(n - 1)

    def _on_scrub(self, _value=None):
        if self.sim is None or not self.sim.history:
            return
        self._show_step(int(round(self.scrub_var.get())))

    def _show_step(self, idx):
        if self.sim is None or not self.sim.history:
            return
        idx = max(0, min(idx, len(self.sim.history) - 1))
        grid = self.sim.power_density_at(idx)
        path = self.sim.cut_path_points()
        self.heatmap.show(grid, self.sim.length_um, self.sim.width_um, path)
        self.history_plot.show(self.sim.history, marker_index=idx)
        h = self.sim.history[idx]
        r_text = "OPEN (severed)" if h.is_open else f"{h.resistance_ohm:.3f} Ω"
        sens_text = (f"{h.sensitivity_ohm_per_um:.5f} Ω/µm"
                     if h.sensitivity_ohm_per_um is not None else "n/a")
        self.status_var.set(
            f"Step {idx + 1}/{len(self.sim.history)}   R = {r_text}   "
            f"Sensitivity = {sens_text}   I = {h.current_a * 1000:.3f} mA   "
            f"cut progress = {h.cut_progress_um:.1f} µm   ({h.segment_kind})"
        )
