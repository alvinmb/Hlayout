"""Top View tool -- Tools > Top View...

Same layout as the Extract Netlist window: a layer table (Layers-panel
style) with one Show checkbox per layer and its role in the stack, then
the options, then Build. Every choice is saved with the design the
moment it changes (techfile.save_topview_settings), so the window -- and
TOPVIEW with no arguments -- start from the same settings next time.

The view is role-based: placed components are always on top, then
resistor ink, conductor, any other drawn layer, and the substrate at
the bottom; glaze and wire bonds are overlays that never hide anything.
Unticking a layer leaves it out of the view. The result layer is never
read back in as input. The view is always of the main design cell and
is written into its own output cell, rebuilt on every Build.
"""

import tkinter as tk
from tkinter import ttk

from .fonts import section_header_font
from ..core.errors import LayoutError
from ..core.layers import NUM_LAYERS
from ..core.units import dbu_to_mm, mm_to_dbu
from ..io.techfile import load_topview_settings, save_topview_settings

#: stacking order shown in the table, top of the view first
ROLE_ORDER = ("resistor", "conductor", "other", "substrate", "glaze", "bond")
ROLE_TEXT = {
    "resistor": "resistor ink",
    "conductor": "conductor",
    "other": "other layer",
    "substrate": "substrate (bottom)",
    "glaze": "glaze (overlay)",
    "bond": "wire bonds (overlay)",
}


class TopViewWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.session = app.session
        self.title("Top View")
        self.transient(master)
        self.minsize(600, 480)

        st = load_topview_settings(self.session.library)
        if st["result_layer"] < 0:
            # first use: a layer named "top view" is the obvious target
            for i in range(NUM_LAYERS):
                nm = (self.session.library.layers[i].name or "").lower().replace("_", " ")
                if "top view" in nm or "topview" in nm.replace(" ", ""):
                    st["result_layer"] = i
                    break
        self._excluded = set(st["excluded"])
        self._rows = {}
        self._loading = True

        self.used_only_var = tk.BooleanVar(value=st["used_only"])
        self.result_var = tk.StringVar(value=self._layer_label(st["result_layer"])
                                       if st["result_layer"] >= 0 else "")
        self.out_cell_var = tk.StringVar(value=st["out_cell"])
        self.width_var = tk.StringVar(value=f"{dbu_to_mm(st['line_width_um']):g}")
        self.hatch_var = tk.BooleanVar(value=st["hatch"])
        self.hatch_pitch_var = tk.StringVar(value=f"{dbu_to_mm(st['hatch_pitch_um']):g}")
        self.hatch_angles_var = tk.StringVar(
            value=", ".join(f"{a:g}" for a in st["hatch_angles"]))
        self.flatten_var = tk.BooleanVar(value=st["flatten"])
        self.simplify_var = tk.StringVar(value=f"{dbu_to_mm(st['simplify_um']):g}")
        self.refdes_var = tk.BooleanVar(value=st["refdes"])
        self.refdes_h_var = tk.StringVar(value=f"{dbu_to_mm(st['refdes_height_um']):g}")

        self._build()
        self._populate_layers()
        for v in (self.out_cell_var, self.width_var, self.hatch_pitch_var,
                  self.hatch_angles_var, self.simplify_var, self.refdes_h_var):
            v.trace_add("write", lambda *_: self._save())
        self._sync_hatch()
        self.refdes_h_entry.configure(state="normal" if self.refdes_var.get() else "disabled")
        self._loading = False
        self._fit_to_screen()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<F5>", lambda e: self.build())

    # ------------------------------------------------------------- helpers

    def _fit_to_screen(self):
        """Natural size, but never taller than the screen (minus the
        taskbar) -- the layer table scrolls instead."""
        self.update_idletasks()
        w = max(self.winfo_reqwidth(), 640)
        h = min(self.winfo_reqheight(), self.winfo_screenheight() - 90)
        self.geometry(f"{w}x{h}")

    def _layer_label(self, n):
        return f"{n}: {self.session.library.layers[n].name}"

    def _layer_choices(self):
        return [self._layer_label(n) for n in range(NUM_LAYERS)]

    @staticmethod
    def _force_checkbox(cb, var, value):
        var.set(value)
        cb.state(["selected"] if value else ["!selected"])

    def _result_layer(self):
        t = self.result_var.get().strip()
        if not t:
            return None
        try:
            return int(t.split(":", 1)[0])
        except ValueError:
            return None

    @staticmethod
    def _mm(var, name, positive=False, allow_zero=True):
        try:
            v = float(var.get().strip() or 0)
        except ValueError:
            raise ValueError(f"{name} must be a number (mm).")
        if v < 0 or (positive and v == 0 and not allow_zero):
            raise ValueError(f"{name} must be greater than zero.")
        return v

    # ------------------------------------------------------------------ UI

    def _build(self):
        # Buttons and the message line are packed FIRST, at the bottom, so
        # they always keep their space -- the layer table is what shrinks
        # when the window is short (large UI font, small screen).
        btns = ttk.Frame(self)
        btns.pack(side="bottom", pady=(6, 12))
        ttk.Button(btns, text="Build  (F5)", command=self.build).pack(side="left", padx=6)
        ttk.Button(btns, text="Open Output Cell", command=self.open_output).pack(side="left", padx=6)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="left", padx=6)
        self.msg_var = tk.StringVar(value="")
        self.msg = ttk.Label(self, textvariable=self.msg_var, wraplength=680, justify="left")
        self.msg.pack(side="bottom", anchor="w", padx=12, pady=(4, 0))

        form = ttk.Labelframe(self, text="Output")
        ttk.Label(form, text="Result layer:").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        self.result_combo = ttk.Combobox(form, textvariable=self.result_var, width=26,
                                         state="readonly", values=self._layer_choices())
        self.result_combo.grid(row=0, column=1, sticky="w", padx=4, pady=3)
        self.result_combo.bind("<<ComboboxSelected>>", lambda e: self._on_result())
        ttk.Label(form, text="Output cell:").grid(row=0, column=2, sticky="e", padx=4, pady=3)
        ttk.Entry(form, textvariable=self.out_cell_var, width=16).grid(
            row=0, column=3, sticky="w", padx=4, pady=3)
        ttk.Label(form, text="Line width (mm):").grid(row=1, column=0, sticky="e", padx=4, pady=3)
        ttk.Entry(form, textvariable=self.width_var, width=10).grid(
            row=1, column=1, sticky="w", padx=4, pady=3)

        opts = ttk.Labelframe(self, text="Options")
        # bottom-up (pack order = bottom first): buttons, message, Options, Output
        opts.pack(side="bottom", fill="x", padx=12, pady=(4, 4))
        form.pack(side="bottom", fill="x", padx=12, pady=(8, 4))
        ttk.Checkbutton(opts, text="Hatch conductor", variable=self.hatch_var,
                        command=self._on_hatch).grid(row=0, column=0, sticky="w", padx=4, pady=3)
        ttk.Label(opts, text="pitch (mm):").grid(row=0, column=1, sticky="e", padx=4)
        self.hatch_pitch_entry = ttk.Entry(opts, textvariable=self.hatch_pitch_var, width=8)
        self.hatch_pitch_entry.grid(row=0, column=2, sticky="w", padx=4)
        ttk.Label(opts, text="angles (deg):").grid(row=0, column=3, sticky="e", padx=4)
        self.hatch_angles_entry = ttk.Entry(opts, textvariable=self.hatch_angles_var, width=12)
        self.hatch_angles_entry.grid(row=0, column=4, sticky="w", padx=4)
        ttk.Checkbutton(opts, text="Include placed cells", variable=self.flatten_var,
                        command=self._save).grid(row=1, column=0, sticky="w", padx=4, pady=3)
        ttk.Label(opts, text="Simplify (mm, 0 = off):").grid(row=1, column=1, columnspan=2,
                                                             sticky="e", padx=4)
        ttk.Entry(opts, textvariable=self.simplify_var, width=8).grid(
            row=1, column=3, sticky="w", padx=4)
        ttk.Checkbutton(opts, text="Include ref-des (drawn last, on top)",
                        variable=self.refdes_var, command=self._on_refdes).grid(
            row=2, column=0, sticky="w", padx=4, pady=3)
        ttk.Label(opts, text="text height (mm):").grid(row=2, column=1, sticky="e", padx=4)
        self.refdes_h_entry = ttk.Entry(opts, textvariable=self.refdes_h_var, width=8)
        self.refdes_h_entry.grid(row=2, column=2, sticky="w", padx=4)


        ttk.Label(
            self,
            text="Builds a top-down, hidden-line view of the design (components on top, "
                 "then resistor ink, conductor, other layers and the substrate; glaze and "
                 "wire bonds are drawn over everything). Untick a layer to leave it out. "
                 "Settings are saved with the design.",
            wraplength=680, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        top = ttk.Frame(self)
        top.pack(fill="x", padx=12)
        ttk.Checkbutton(top, text="Show only layers used in the design",
                        variable=self.used_only_var,
                        command=self._on_used_only).pack(side="left")
        ttk.Button(top, text="Show all", command=lambda: self._set_all(True)).pack(side="right")
        ttk.Button(top, text="Hide all", command=lambda: self._set_all(False)).pack(
            side="right", padx=(0, 6))

        table = ttk.Frame(self, relief="groove", borderwidth=1)
        table.pack(fill="both", expand=True, padx=12, pady=(6, 6))
        header = ttk.Frame(table)
        header.pack(fill="x", padx=4, pady=(4, 0))
        hf = section_header_font(self)
        for text, w in (("Show", 6), ("#", 4), ("Name", 22), ("", 3), ("Role in view", 26)):
            ttk.Label(header, text=text, width=w, font=hf).pack(side="left")
        body = ttk.Frame(table)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, highlightthickness=0, height=200)
        vbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        vbar.pack(side="right", fill="y")

        def _wheel(e):
            canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        self._inner, self._canvas = inner, canvas
        ttk.Label(self, text="Placed parts and dies are drawn on top; a package the substrate "
                             "sits in (a header can) is drawn underneath, so only its rim shows.",
                  foreground="#555", wraplength=680, justify="left").pack(anchor="w", padx=14)

    # --------------------------------------------------------------- layers

    def _populate_layers(self):
        for w in self._inner.winfo_children():
            w.destroy()
        self._rows = {}
        try:
            roles = self.session.top_view_layer_roles()
        except Exception:
            roles = {}
        used = set()
        for c in self.session.library.cells.values():
            for sh in c.shapes:
                used.add(sh.layer)
        res = self._result_layer()
        only_used = self.used_only_var.get()
        layers = [n for n in range(NUM_LAYERS) if n != res
                  and ((n in roles and (n in used or not only_used)) or not only_used)]
        rank = {r: i for i, r in enumerate(ROLE_ORDER)}
        layers.sort(key=lambda n: (rank.get(roles.get(n), 99), -n))
        if res is not None and res in used:
            layers.append(res)
        for n in layers:
            self._make_row(n, roles.get(n), n == res, n in used)
        self._canvas.yview_moveto(0)

    def _make_row(self, n, role, is_result, used):
        layer = self.session.library.layers[n]
        row = ttk.Frame(self._inner)
        row.pack(fill="x")
        var = tk.BooleanVar(value=(n not in self._excluded) and bool(role) and not is_result)
        cb = ttk.Checkbutton(row, variable=var, width=6, command=lambda: self._on_toggle(n))
        cb.pack(side="left", padx=(8, 0))
        ttk.Label(row, text=str(n), width=4).pack(side="left")
        ttk.Label(row, text=layer.name, width=22,
                  foreground=("" if used else "#999")).pack(side="left")
        tk.Label(row, bg=layer.color, width=2, relief="sunken").pack(side="left", padx=(0, 8))
        if is_result:
            text = "result layer (output)"
        elif role:
            text = ROLE_TEXT[role]
        else:
            text = "not used by the view" if used else "no shapes"
        role_lbl = ttk.Label(row, text=text, width=30, foreground="#555")
        role_lbl.pack(side="left")
        if is_result or not role:
            cb.state(["disabled"])
        self._force_checkbox(cb, var, var.get())
        self._rows[n] = (cb, var, role_lbl, role)

    def _on_toggle(self, n):
        cb, var, lbl, role = self._rows[n]
        if var.get():
            self._excluded.discard(n)
            lbl.configure(text=ROLE_TEXT.get(role, ""))
        else:
            self._excluded.add(n)
            lbl.configure(text="left out")
        self._save()

    def _set_all(self, on):
        for n, (cb, var, lbl, role) in self._rows.items():
            if "disabled" in cb.state():
                continue
            self._force_checkbox(cb, var, on)
            (self._excluded.discard if on else self._excluded.add)(n)
            lbl.configure(text=ROLE_TEXT.get(role, "") if on else "left out")
        self._save()

    def _on_used_only(self):
        self._populate_layers()
        self._save()

    def _on_result(self):
        self._populate_layers()
        self._save()

    def _on_hatch(self):
        self._sync_hatch()
        self._save()

    def _on_refdes(self):
        self.refdes_h_entry.configure(state="normal" if self.refdes_var.get() else "disabled")
        self._save()

    def _sync_hatch(self):
        state = "normal" if self.hatch_var.get() else "disabled"
        self.hatch_pitch_entry.configure(state=state)
        self.hatch_angles_entry.configure(state=state)

    # -------------------------------------------------------------- persist

    def _collect(self):
        """The current choices as a settings dict, or raise ValueError
        with a message for the first bad field."""
        st = {"excluded": sorted(self._excluded),
              "used_only": self.used_only_var.get(),
              "hatch": self.hatch_var.get(),
              "flatten": self.flatten_var.get(),
              "out_cell": self.out_cell_var.get().strip() or "TOPVIEW"}
        res = self._result_layer()
        st["result_layer"] = -1 if res is None else res
        width = self._mm(self.width_var, "Line width")
        if width <= 0:
            raise ValueError("Line width must be greater than zero.")
        st["line_width_um"] = float(mm_to_dbu(width))
        st["simplify_um"] = float(mm_to_dbu(self._mm(self.simplify_var, "Simplify")))
        st["refdes"] = self.refdes_var.get()
        rh = self._mm(self.refdes_h_var, "Ref-des text height")
        if st["refdes"] and rh <= 0:
            raise ValueError("Ref-des text height must be greater than zero.")
        st["refdes_height_um"] = float(mm_to_dbu(rh)) if rh > 0 else 600.0
        pitch = self._mm(self.hatch_pitch_var, "Hatch pitch")
        if st["hatch"] and pitch <= 0:
            raise ValueError("Hatch pitch must be greater than zero.")
        st["hatch_pitch_um"] = float(mm_to_dbu(pitch)) if pitch > 0 else 250.0
        try:
            ang = [float(t) for t in self.hatch_angles_var.get().replace(";", ",").split(",")
                   if t.strip()]
        except ValueError:
            raise ValueError("Hatch angles must be comma-separated numbers.")
        st["hatch_angles"] = ang or [45.0, -45.0]
        return st

    def _save(self):
        if self._loading:
            return True
        try:
            st = self._collect()
        except ValueError as e:
            self._say(str(e), error=True)
            return False
        save_topview_settings(self.session.library, st)
        self._say("")
        return True

    def _say(self, text, error=False):
        self.msg_var.set(text)
        self.msg.configure(foreground=("#b00020" if error else ""))

    # ---------------------------------------------------------------- run

    def build(self):
        if getattr(self, "_building", False):
            return
        if not self._save():
            return
        if self._result_layer() is None:
            self._say("Pick a Result layer.", error=True)
            return
        # a big routed board with hatching takes a while: build on a
        # worker thread behind a progress box so the window never freezes
        self._building = True
        self._say("Building the top view...")
        from .progress import run_with_progress
        run_with_progress(self, self.session.top_view_from_settings,
                          on_done=self._built, on_error=self._build_failed,
                          title="Top View", text="Building the top view...",
                          app=self.app)

    def _build_failed(self, e):
        self._building = False
        if isinstance(e, LayoutError):
            self._say(str(e), error=True)
        else:
            self._say(f"Top View failed: {type(e).__name__}: {e}", error=True)
        try:
            self.app.status.set("TOPVIEW failed")
        except Exception:
            pass

    def _built(self, written):
        self._building = False
        st = load_topview_settings(self.session.library)
        total = sum(written.values())
        res = st["result_layer"]
        if total:
            layer = self.session.library.layers[res]
            if not layer.visible:
                layer.visible = True
        self.app.refresh_all()
        if total:
            self._say(f"Wrote {total} path(s) to layer {res} of cell {st['out_cell']} -- "
                      "Open Output Cell to see it.")
            self.app.status.set(f"TOPVIEW: wrote {total} path(s) to layer {res} "
                                f"of cell {st['out_cell']}")
        else:
            self._say(f"Nothing was drawn on layer {res} -- check that some layers are "
                      "ticked and have shapes.", error=True)

    def open_output(self):
        name = self.out_cell_var.get().strip() or "TOPVIEW"
        if name not in self.session.library.cells:
            self._say(f"There is no {name} cell yet -- Build first.", error=True)
            return
        self.session.use_cell(name)
        self.app.canvas.set_cell(name)
        try:
            self.app.cell_var.set(name)
        except Exception:
            pass
        self.app.canvas.fit_view()

    def reload(self):
        st = load_topview_settings(self.session.library)
        self._loading = True
        self._excluded = set(st["excluded"])
        self.used_only_var.set(st["used_only"])
        self.result_var.set(self._layer_label(st["result_layer"]) if st["result_layer"] >= 0 else "")
        self.result_combo["values"] = self._layer_choices()
        self._populate_layers()
        self._loading = False


def open_topview_window(app):
    """Tools > Top View... . One window per App: calling again brings the
    existing one to the front, reloaded from the design."""
    win = getattr(app, "_topview_window", None)
    if win is not None and win.winfo_exists():
        win.reload()
        win.lift()
        win.focus_set()
        return win
    win = TopViewWindow(app, app)
    app._topview_window = win
    return win
