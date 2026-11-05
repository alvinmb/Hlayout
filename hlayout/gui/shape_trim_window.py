"""Laser-trim simulator for shaped thick-film resistors (top-hat, U,
angle, ...) -- reached from the Thick-Film Resistor Designer's "Trim
Simulator..." button when the selected resistor is not a plain
rectangle (rectangles keep the full plunge/L-cut simulator in
trim_sim_window).

Runs hlayout.core.tf_shapes.simulate_trim(): a plunge along the shape's
suggested cut, with resistance and sensitivity at every step and the
power map of the last step, so you can see how far the cut can go and
where the current crowds. numpy/scipy are imported lazily.
"""
import math
import tkinter as tk
from tkinter import ttk

from . import dialogs
from .fonts import section_header_font
from ..core import tf_shapes
from ..core.units import dbu_to_mm

ACCENT = "#58a6ff"
MUTED = "#8b949e"
BG = "#0d1117"
PANEL = "#161b22"
TEXT = "#e6edf3"
GOLD = "#d29922"
INK = "#3b4a63"
GOOD = "#3fb950"
WARN = "#f78166"


def open_shape_trim_simulator(master, resistor, solution, rules):
    try:
        import numpy  # noqa: F401
        import scipy  # noqa: F401
    except ImportError:
        dialogs.show_error("Trim Simulator",
                           "The Trim Simulator needs numpy and scipy:  pip install numpy scipy",
                           parent=master)
        return None
    return ShapeTrimWindow(master, resistor, solution, rules)


def _heat(frac):
    """0..1 -> dark blue .. yellow .. white."""
    stops = [(0.0, (13, 17, 23)), (0.25, (40, 40, 120)), (0.5, (190, 40, 60)),
             (0.8, (240, 170, 40)), (1.0, (255, 255, 230))]
    frac = min(1.0, max(0.0, frac))
    for (f0, c0), (f1, c1) in zip(stops, stops[1:]):
        if frac <= f1:
            t = (frac - f0) / (f1 - f0) if f1 > f0 else 0
            return tuple(int(c0[i] + (c1[i] - c0[i]) * t) for i in range(3))
    return stops[-1][1]


class ShapeTrimWindow(tk.Toplevel):
    def __init__(self, master, resistor, solution, rules):
        super().__init__(master)
        self.res, self.sol, self.rules = resistor, solution, rules
        self.geom = solution.geom
        self.title(f"Trim Simulator - {resistor.ref_des} "
                   f"({tf_shapes.SHAPE_LABELS.get(solution.shape, solution.shape)})")
        self.configure(bg=BG)
        self.geometry("980x600")
        self.steps = []
        self.field = None
        self._img = None
        self._build()
        self.after(50, self._draw_shape)

    # -- UI -----------------------------------------------------------------
    def _build(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        cut = self.geom.cut
        ttk.Label(top, text="Kerf (µm):").pack(side="left")
        self.kerf_var = tk.StringVar(value="40")
        ttk.Entry(top, textvariable=self.kerf_var, width=6).pack(side="left", padx=(2, 10))
        ttk.Label(top, text="Plunge depth (µm):").pack(side="left")
        self.depth_var = tk.StringVar(value=f"{cut.max_depth:.0f}")
        ttk.Entry(top, textvariable=self.depth_var, width=8).pack(side="left", padx=(2, 4))
        ttk.Label(top, text=f"(max {cut.max_depth:.0f})", foreground=MUTED).pack(side="left", padx=(0, 10))
        ttk.Label(top, text="Steps:").pack(side="left")
        self.steps_var = tk.StringVar(value="30")
        ttk.Entry(top, textvariable=self.steps_var, width=5).pack(side="left", padx=(2, 10))
        ttk.Button(top, text="Run", command=self._run).pack(side="left")
        ttk.Button(top, text="Close", command=self.destroy).pack(side="right")

        info = (f"{self.res.ref_des}  {self.res.value_label}  aim {self.res.aim_value_label(self.rules)}  |  "
                f"ink {self.sol.ink_rsh:g} Ω/□  {self.sol.squares:.3f} sq  |  "
                f"W {dbu_to_mm(self.sol.body_width):.2f} mm  L {dbu_to_mm(self.sol.body_length):.3f} mm")
        ttk.Label(self, text=info, foreground=MUTED, padding=(8, 0)).pack(anchor="w")

        body = ttk.Frame(self, padding=8)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="SHAPE AND POWER MAP", foreground=ACCENT,
                  font=section_header_font(self)).pack(anchor="w")
        self.shape_cv = tk.Canvas(left, bg=PANEL, highlightthickness=0, width=440, height=420)
        self.shape_cv.pack(fill="both", expand=True)
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        ttk.Label(right, text="RESISTANCE AND SENSITIVITY", foreground=ACCENT,
                  font=section_header_font(self)).pack(anchor="w")
        self.plot_cv = tk.Canvas(right, bg=PANEL, highlightthickness=0, width=460, height=330)
        self.plot_cv.pack(fill="both", expand=True)
        self.result_var = tk.StringVar(value="Press Run to cut the plunge step by step.")
        ttk.Label(right, textvariable=self.result_var, wraplength=460, justify="left").pack(
            anchor="w", pady=(8, 0))
        self.shape_cv.bind("<Configure>", lambda e: self._draw_shape())
        self.plot_cv.bind("<Configure>", lambda e: self._draw_plot())

    # -- run ----------------------------------------------------------------
    def _run(self):
        try:
            kerf = float(self.kerf_var.get())
            depth = float(self.depth_var.get())
            steps = int(self.steps_var.get())
            if kerf <= 0 or depth <= 0 or steps < 1:
                raise ValueError
        except ValueError:
            dialogs.show_error("Trim Simulator", "Kerf, depth and steps must be positive numbers.",
                               parent=self)
            return
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            self.steps, self.field = tf_shapes.simulate_trim(
                self.geom, self.sol.ink_rsh, depth=depth, kerf=kerf, steps=steps, want_field=True)
        finally:
            self.config(cursor="")
        self._depth = depth
        self._draw_shape()
        self._draw_plot()
        r0, rn = self.steps[0].resistance, self.steps[-1].resistance
        target = self.res.value
        if self.steps[-1].is_open:
            msg = (f"OPEN at {self.steps[-1].depth:.0f} µm: the cut severs the resistor. "
                   f"Last good reading before that: {self.steps[-2].resistance:.1f} Ω.")
        else:
            sens = self.steps[-1].sensitivity
            msg = (f"Untrimmed {r0:.1f} Ω  ->  {rn:.1f} Ω after {self.steps[-1].depth:.0f} µm "
                   f"(+{100 * (rn / r0 - 1):.1f} %).  Sensitivity at the end "
                   f"{sens:.3f} Ω/µm." if sens is not None else "")
        reach = next((s for s in self.steps if math.isfinite(s.resistance) and s.resistance >= target), None)
        if reach is not None:
            msg += f"\nReaches the {target:g} Ω value at about {reach.depth:.0f} µm."
        elif not self.steps[-1].is_open:
            msg += (f"\nDoes not reach {target:g} Ω within this depth: cut deeper, or choose a "
                    "shape with more trim range (a roof or a crown).")
        self.result_var.set(msg)

    # -- drawing ------------------------------------------------------------
    def _xf(self, cv):
        g = self.geom
        x0, y0, x1, y1 = g.bbox()
        w = cv.winfo_width() or 440
        h = cv.winfo_height() or 420
        mg = 16
        sc = min((w - 2 * mg) / max(x1 - x0, 1), (h - 2 * mg) / max(y1 - y0, 1))
        ox = (w - (x1 - x0) * sc) / 2
        oy = (h - (y1 - y0) * sc) / 2
        return lambda x, y: (ox + (x - x0) * sc, oy + (y1 - y) * sc), sc

    def _draw_shape(self):
        cv = self.shape_cv
        cv.delete("all")
        tx, sc = self._xf(cv)
        g = self.geom
        for r in g.ink:
            a, b = tx(r[0], r[3]), tx(r[2], r[1])
            cv.create_rectangle(a[0], a[1], b[0], b[1], fill=INK, outline=INK)
        if self.field is not None and self.field[0] is not None:
            self._draw_field(cv, tx, sc)
        for r, pin in g.pads:
            a, b = tx(r[0], r[3]), tx(r[2], r[1])
            cv.create_rectangle(a[0], a[1], b[0], b[1], fill=GOLD, outline=GOLD)
            cv.create_text((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, text=pin, fill=BG)
        for r in g.roofs:
            a, b = tx(r[0], r[3]), tx(r[2], r[1])
            cv.create_rectangle(a[0], a[1], b[0], b[1], fill=GOLD, outline=TEXT, dash=(3, 2))
        cut = g.cut
        if cut is not None:
            d = getattr(self, "_depth", cut.max_depth)
            (sx, sy), (dx, dy) = cut.start, cut.dir
            a, b = tx(sx, sy), tx(sx + dx * d, sy + dy * d)
            cv.create_line(a[0], a[1], b[0], b[1], fill="#7fd3ff", width=max(2, int(40 * sc)))

    def _draw_field(self, cv, tx, sc):
        import numpy as np
        P, xs, ys = self.field
        if P is None or not np.isfinite(P).any() or P.max() <= 0:
            return
        f = np.sqrt(P / P.max())
        h = xs[1] - xs[0] if len(xs) > 1 else 1
        px = max(1, int(round(h * sc)))
        # one canvas rectangle per cell is too slow; paint a PhotoImage
        ny, nx = f.shape
        img = tk.PhotoImage(width=nx * px, height=ny * px)
        rows = []
        for j in range(ny - 1, -1, -1):
            cols = []
            for i in range(nx):
                if P[j, i] > 0:
                    c = "#%02x%02x%02x" % _heat(f[j, i])
                else:
                    c = PANEL if not self._is_ink(xs[i], ys[j]) else INK
                cols.extend([c] * px)
            row = "{" + " ".join(cols) + "}"
            rows.extend([row] * px)
        img.put(" ".join(rows))
        self._img = img
        x0, y1 = tx(xs[0] - h / 2, ys[-1] + h / 2)
        cv.create_image(x0, y1, image=img, anchor="nw")

    def _is_ink(self, x, y):
        return any(r[0] <= x < r[2] and r[1] <= y < r[3] for r in self.geom.ink)

    def _draw_plot(self):
        cv = self.plot_cv
        cv.delete("all")
        w = cv.winfo_width() or 460
        h = cv.winfo_height() or 330
        ml, mr, mt, mb = 60, 56, 16, 34
        cv.create_rectangle(ml, mt, w - mr, h - mb, outline="#30363d")
        good = [s for s in self.steps if math.isfinite(s.resistance)]
        if len(good) < 2:
            cv.create_text(w / 2, h / 2, text="No run yet", fill=MUTED)
            return
        dmax = max(s.depth for s in good) or 1
        rs = [s.resistance for s in good] + [self.res.value]
        rmin, rmax = min(rs), max(rs)
        if rmax <= rmin:
            rmax = rmin + 1
        pad = 0.05 * (rmax - rmin)
        rmin, rmax = rmin - pad, rmax + pad
        sens = [s.sensitivity for s in good if s.sensitivity is not None]
        smax = max(sens) if sens else 1
        smax = smax if smax > 0 else 1

        def X(d):
            return ml + (w - ml - mr) * d / dmax

        def Y(r):
            return h - mb - (h - mt - mb) * (r - rmin) / (rmax - rmin)

        def Ys(v):
            return h - mb - (h - mt - mb) * v / smax

        yt = Y(self.res.value)
        cv.create_line(ml, yt, w - mr, yt, fill=WARN, dash=(4, 3))
        cv.create_text(ml + 4, yt - 8, text=f"value {self.res.value:g} Ω", fill=WARN, anchor="w")
        pts = []
        for s in good:
            pts += [X(s.depth), Y(s.resistance)]
        cv.create_line(*pts, fill=GOLD, width=2)
        spts = []
        for s in good:
            if s.sensitivity is not None:
                spts += [X(s.depth), Ys(s.sensitivity)]
        if len(spts) >= 4:
            cv.create_line(*spts, fill=GOOD, width=1, dash=(5, 2))
        for k in range(5):
            r = rmin + (rmax - rmin) * k / 4
            cv.create_text(ml - 4, Y(r), text=f"{r:.0f}", fill=GOLD, anchor="e")
            v = smax * k / 4
            cv.create_text(w - mr + 4, Ys(v), text=f"{v:.2f}", fill=GOOD, anchor="w")
            d = dmax * k / 4
            cv.create_text(X(d), h - mb + 12, text=f"{d:.0f}", fill=MUTED)
        cv.create_text((ml + w - mr) / 2, h - 8, text="Cut depth (µm)", fill=MUTED)
        cv.create_text(ml, mt - 6, text="R (Ω)", fill=GOLD, anchor="w")
        cv.create_text(w - mr, mt - 6, text="Ω/µm", fill=GOOD, anchor="e")
