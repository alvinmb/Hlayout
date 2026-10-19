"""Tools > Auto Place... -- place a netlist's parts for the shortest nets.

Pick the KiCad netlist (or CSV / Tango) and, optionally, an overrides CSV;
map any KiCad footprint HLayout couldn't match to a cell; set the net
weights (power and ground included), the thermal strength, the grid and
the effort; then Run (place from scratch, keeping parts that are already
placed as the start), Improve (refine the current placement) or Place
Selection (only the selected parts move). The straight-line interconnects
are switched on so every net shows pad to pad; dragging a part afterwards
updates them live. Each run is one undo step.
"""
import os
import re
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

from ..core.errors import LayoutError


class AutoPlaceWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.title("Auto Place")
        self.transient(master)
        self.minsize(720, 560)
        self._running = False
        self._cancel = False
        self.nl = None
        self.v = {k: tk.StringVar() for k in ("netlist", "overrides", "grid", "thermal", "effort",
                                               "seed", "clearance", "bond_reach", "res_layer")}
        self._build()
        self.reload()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())

    @property
    def session(self):
        return self.app.session

    # ------------------------------------------------------------------ UI
    def _build(self):
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        self.banner = tk.Label(f, text="", font=("Segoe UI", 14, "bold"), anchor="w", padx=8,
                               pady=4, fg="white", bg="#1f5fbf")
        style = ttk.Style(self)
        style.configure("AP.Horizontal.TProgressbar", thickness=20)
        try:   # rows tall enough for the UI font (scaled displays clip them otherwise)
            import tkinter.font as tkfont
            style.configure("AP.Treeview", rowheight=tkfont.nametofont("TkDefaultFont").metrics("linespace") + 8)
        except Exception:
            pass
        self.bar = ttk.Progressbar(f, maximum=100, style="AP.Horizontal.TProgressbar")

        files = ttk.LabelFrame(f, text="Inputs", padding=8)
        files.pack(fill="x")
        for r, (label, key, cmd) in enumerate((("Netlist (KiCad .net, CSV, Tango):", "netlist", self._browse_netlist),
                                               ("Overrides CSV (optional):", "overrides", self._browse_overrides))):
            ttk.Label(files, text=label).grid(row=r, column=0, sticky="e", padx=4, pady=2)
            ttk.Entry(files, textvariable=self.v[key], width=60).grid(row=r, column=1, sticky="we")
            ttk.Button(files, text="Browse...", command=cmd).grid(row=r, column=2, padx=4)
        files.columnconfigure(1, weight=1)
        self.summary = ttk.Label(files, text="", foreground="#555")
        self.summary.grid(row=2, column=1, sticky="w")

        nb = ttk.Notebook(f)
        nb.pack(fill="both", expand=True, pady=(8, 0))
        # parts
        pf = ttk.Frame(nb, padding=4)
        nb.add(pf, text="Parts")
        self.parts = ttk.Treeview(pf, columns=("ref", "value", "footprint", "cell", "family"),
                                  show="headings", height=8, style="AP.Treeview")
        for col, w in (("ref", 60), ("value", 140), ("footprint", 200), ("cell", 150), ("family", 70)):
            self.parts.heading(col, text=col.capitalize())
            self.parts.column(col, width=w, anchor="w")
        self.parts.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(pf, command=self.parts.yview)
        self.parts.configure(yscrollcommand=sb.set)
        sb.pack(side="left", fill="y")
        side = ttk.Frame(pf)
        side.pack(side="left", fill="y", padx=6)
        ttk.Label(side, text="Cell for the selected footprint:").pack(anchor="w")
        self.cell_var = tk.StringVar()
        self.cell_box = ttk.Combobox(side, textvariable=self.cell_var, width=24, state="readonly")
        self.cell_box.pack(anchor="w")
        ttk.Button(side, text="Map footprint", command=self._map_footprint).pack(anchor="w", pady=4)
        # nets
        nf = ttk.Frame(nb, padding=4)
        nb.add(nf, text="Net weights")
        self.nets = ttk.Treeview(nf, columns=("net", "class", "pins", "weight"), show="headings", height=8,
                                 style="AP.Treeview")
        for col, w in (("net", 160), ("class", 90), ("pins", 60), ("weight", 80)):
            self.nets.heading(col, text=col.capitalize())
            self.nets.column(col, width=w, anchor="w")
        self.nets.pack(side="left", fill="both", expand=True)
        sb2 = ttk.Scrollbar(nf, command=self.nets.yview)
        self.nets.configure(yscrollcommand=sb2.set)
        sb2.pack(side="left", fill="y")
        side2 = ttk.Frame(nf)
        side2.pack(side="left", fill="y", padx=6)
        ttk.Label(side2, text="Weight for the selected net(s):").pack(anchor="w")
        self.w_var = tk.StringVar(value="1")
        ttk.Spinbox(side2, textvariable=self.w_var, from_=0, to=20, increment=0.5, width=8).pack(anchor="w")
        ttk.Button(side2, text="Set weight", command=self._set_weight).pack(anchor="w", pady=4)
        ttk.Label(side2, text="Class weights:").pack(anchor="w", pady=(10, 0))
        self.cw = {}
        for cls in ("signal", "power", "ground"):
            row = ttk.Frame(side2)
            row.pack(anchor="w")
            ttk.Label(row, text=cls, width=8).pack(side="left")
            self.cw[cls] = tk.StringVar()
            ttk.Spinbox(row, textvariable=self.cw[cls], from_=0, to=20, increment=0.5, width=6).pack(side="left")
        ttk.Label(side2, text="0 = leave the net out", foreground="#666").pack(anchor="w", pady=(6, 0))
        # settings
        sf = ttk.LabelFrame(f, text="Settings (saved with the design)", padding=8)
        sf.pack(fill="x", pady=(8, 0))
        items = (("Grid (um)", "grid"), ("Clearance (um)", "clearance"), ("Bond reach (um)", "bond_reach"),
                 ("Thermal (0 = off)", "thermal"), ("Effort", "effort"), ("Seed", "seed"),
                 ("Resistor conductor layer", "res_layer"))
        for k, (label, key) in enumerate(items):
            r, c = divmod(k, 4)
            ttk.Label(sf, text=label + ":").grid(row=r, column=c * 2, sticky="e", padx=4, pady=2)
            ttk.Entry(sf, textvariable=self.v[key], width=8).grid(row=r, column=c * 2 + 1, sticky="w")

        bf = ttk.Frame(f)
        bf.pack(fill="x", pady=(8, 0))
        self.run_btn = ttk.Button(bf, text="Run", command=lambda: self.run("run"))
        self.run_btn.pack(side="left")
        self.imp_btn = ttk.Button(bf, text="Improve", command=lambda: self.run("improve"))
        self.imp_btn.pack(side="left", padx=4)
        self.sel_btn = ttk.Button(bf, text="Place Selection", command=lambda: self.run("selection"))
        self.sel_btn.pack(side="left")
        self.stop_btn = ttk.Button(bf, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        self.sli_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bf, text="Show straight-line interconnects", variable=self.sli_var,
                        command=lambda: self.app.set_sli(self.sli_var.get())).pack(side="left", padx=12)
        self.pwr_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bf, text="incl. power / ground", variable=self.pwr_var,
                        command=self._pwr).pack(side="left")
        ttk.Button(bf, text="Close", command=self.close).pack(side="right")

    # ------------------------------------------------------------- state
    def reload(self):
        S = self.session.autoplace_settings()
        for key, sk in (("grid", "grid"), ("clearance", "clearance"), ("bond_reach", "bond_reach"),
                        ("thermal", "thermal"), ("effort", "effort"), ("seed", "seed")):
            self.v[key].set(f"{float(S[sk]):g}")
        self.v["res_layer"].set("" if S.get("resistor_layer") in (None, "") else str(S["resistor_layer"]))
        for cls, var in self.cw.items():
            var.set(f"{float(S['class_weights'].get(cls, 1.0)):g}")
        self.cell_box["values"] = sorted(self.session.library.cells)
        last = S.get("last_netlist") if isinstance(S, dict) else None
        if last and not self.v["netlist"].get():
            self.v["netlist"].set(last)
        if self.v["netlist"].get():
            self._load_netlist()

    def _settings(self):
        out = {}
        for key, sk, typ in (("grid", "grid", float), ("clearance", "clearance", float),
                             ("bond_reach", "bond_reach", float), ("thermal", "thermal", float),
                             ("effort", "effort", float), ("seed", "seed", int)):
            try:
                out[sk] = typ(float(self.v[key].get()))
            except ValueError:
                raise LayoutError(f"{key}: not a number")
        rl = self.v["res_layer"].get().strip()
        out["resistor_layer"] = int(rl) if rl else None
        S = self.session.autoplace_settings()
        cw = dict(S["class_weights"])
        for cls, var in self.cw.items():
            try:
                cw[cls] = float(var.get())
            except ValueError:
                pass
        out["class_weights"] = cw
        return out

    def _browse_netlist(self):
        p = filedialog.askopenfilename(parent=self, title="Netlist",
                                       filetypes=[("KiCad netlist", "*.net"), ("CSV", "*.csv"),
                                                  ("Tango", "*.tng"), ("All", "*.*")])
        if p:
            self.v["netlist"].set(p)
            self._load_netlist()

    def _browse_overrides(self):
        p = filedialog.askopenfilename(parent=self, title="Overrides CSV",
                                       filetypes=[("CSV", "*.csv"), ("All", "*.*")])
        if p:
            self.v["overrides"].set(p)

    def _load_netlist(self):
        from ..io.kicad_netlist import read_netlist
        from ..scripting.autoplace_session import _family, _net_class, match_footprint
        path = self.v["netlist"].get().strip()
        try:
            self.nl = read_netlist(path)
        except Exception as e:
            self.summary.config(text=f"Can't read the netlist: {e}")
            self.nl = None
            return
        S = self.session.autoplace_settings()
        lib = self.session.library
        cell = lib.cells.get(self.session.current_cell_name)
        placed = {r.label: r.cell_name for r in (cell.srefs if cell else []) if r.label}
        self.parts.delete(*self.parts.get_children())
        natural = lambda r: [int(t) if t.isdigit() else t for t in re.findall(r"\d+|\D+", r)]
        for ref, c in sorted(self.nl.components.items(), key=lambda kv: natural(kv[0])):
            cn = placed.get(ref) or match_footprint(lib, c.footprint, S["footprint_map"]) or ""
            fam = _family(lib, c, cn or None, set()) or ("connector (not placed)" if not cn else "")
            self.parts.insert("", "end", iid=ref, values=(ref, c.value, c.footprint, cn, fam))
        self.nets.delete(*self.nets.get_children())
        for n in self.nl.nets:
            cls = _net_class(n, S)
            w = S["weights"].get(n.name, S["class_weights"].get(cls, 1.0))
            self.nets.insert("", "end", iid=n.name, values=(n.name, cls, len(n.nodes), f"{float(w):g}"))
        self.summary.config(text=f"{len(self.nl.components)} parts, {len(self.nl.nets)} nets")

    def _map_footprint(self):
        cell = self.cell_var.get()
        sel = self.parts.selection()
        if not cell or not sel:
            return
        S = self.session.autoplace_settings()
        fmap = dict(S["footprint_map"])
        for ref in sel:
            fp = self.parts.set(ref, "footprint")
            if fp:
                fmap[fp] = cell
        self.session.set_autoplace_settings(footprint_map=fmap)
        self._load_netlist()

    def _set_weight(self):
        try:
            w = float(self.w_var.get())
        except ValueError:
            return
        S = self.session.autoplace_settings()
        ws = dict(S["weights"])
        for net in self.nets.selection():
            ws[net] = w
            self.nets.set(net, "weight", f"{w:g}")
        self.session.set_autoplace_settings(weights=ws)

    def _pwr(self):
        self.app.canvas.sli_show_power = bool(self.pwr_var.get())
        self.app.canvas.redraw()

    def _say(self, text, kind="run", pct=None):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        self.banner.config(text=text, bg={"run": "#1f5fbf", "ok": "#1e8e3e", "warn": "#d56e00",
                                          "err": "#c5221f"}[kind])
        if not self.banner.winfo_ismapped():
            self.banner.pack(fill="x", before=self.winfo_children()[0].winfo_children()[2], pady=(0, 6))
        if pct is None:
            self.bar.pack_forget()
        else:
            if not self.bar.winfo_ismapped():
                self.bar.pack(fill="x", after=self.banner, pady=(0, 6))
            self.bar["value"] = pct
        self.app.status.set("Auto Place: " + text)

    # ------------------------------------------------------------ actions
    def run(self, mode):
        if self._running:
            return
        path = self.v["netlist"].get().strip()
        if not path or not os.path.exists(path):
            self._say("Pick a netlist first", "err")
            return
        try:
            st = self._settings()
        except LayoutError as e:
            self._say(str(e), "err")
            return
        st["last_netlist"] = path
        self.session.set_autoplace_settings(**st)
        only = None
        if mode == "selection":
            only = self._selected_refs()
            if not only:
                self._say("Select the parts to place first", "err")
                return
        ov = self.v["overrides"].get().strip() or None
        self._running, self._cancel = True, False
        for b in (self.run_btn, self.imp_btn, self.sel_btn):
            b.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._say("Placing 0%", "run", 0)
        t0 = time.monotonic()
        prog = {"f": 0.0}

        def progress(f):
            prog["f"] = f

        self._result = None

        def tick():
            # the worker never touches Tk: it leaves its result here and
            # this poll (on the Tk thread) picks it up
            if self._result is not None:
                self._done(*self._result)
                return
            if self._running:
                self._say(f"Placing {int(100 * prog['f'])}% -- {time.monotonic() - t0:.0f} s", "run",
                          100 * prog["f"])
                self.after(150, tick)
        self.after(150, tick)

        # the engine runs on a worker; the design is only written back on
        # the Tk thread (Session.auto_place does both, so split it here)
        from ..scripting.autoplace_session import apply, build_job
        try:
            placer, info = build_job(self.session, path, overrides=ov,
                                     only=set(only) if only else None)
        except Exception as e:
            self._result = (None, None, e, 0)
            return

        def work():
            try:
                placer.run(effort=float(info["settings"]["effort"]), improve=(mode != "run"),
                           on_progress=progress, cancel=lambda: self._cancel)
                self._result = (placer, info, None, time.monotonic() - t0)
            except Exception as e:
                self._result = (None, None, e, time.monotonic() - t0)
        threading.Thread(target=work, daemon=True).start()

    def _selected_refs(self):
        cv = self.app.canvas
        refs = []
        for sel in (cv.selected_multi or ([cv.selected] if cv.selected else [])):
            obj = sel[1] if isinstance(sel, tuple) and len(sel) > 1 else None
            lab = getattr(obj, "label", "")
            if lab:
                refs.append(lab)
        return refs

    def _done(self, placer, info, err, secs):
        self._running = False
        try:
            alive = self.winfo_exists()
        except tk.TclError:
            alive = False
        if alive:
            for b in (self.run_btn, self.imp_btn, self.sel_btn):
                b.config(state="normal")
            self.stop_btn.config(state="disabled")
        if err is not None:
            if alive:
                self._say(f"FAILED -- {err}", "err")
            return
        from ..scripting.autoplace_session import apply
        s = self.session
        with s._batch():
            apply(s, placer, info)
        s._notify()
        self.app.refresh_all() if hasattr(self.app, "refresh_all") else self.app.canvas.redraw()
        self.app.set_sli(True)
        d = s.sli()
        bad, out = placer.overlaps()
        msg = (f"PLACED in {secs:.0f} s -- {sum(1 for p in placer.parts if not p.fixed)} parts, "
               f"SLI total {d['total'] / 1000:.1f} mm")
        notes = []
        if info["skipped"]:
            notes.append("not placed: " + ", ".join(info["skipped"]))
        if info["unmapped"]:
            notes.append("no cell for: " + ", ".join(info["unmapped"]))
        if bad or out:
            notes.append(f"{len(bad)} overlap(s), {len(out)} outside")
        if info["missing_pins"]:
            notes.append(f"{len(info['missing_pins'])} netlist pin(s) not on the footprint")
        if alive:
            self._say(msg + ("; " + "; ".join(notes) if notes else ""), "warn" if (bad or out or info["unmapped"]) else "ok", 100)
            try:
                self.bell()
                self.lift()
            except tk.TclError:
                pass

    def _stop(self):
        self._cancel = True

    def close(self):
        if self._running:
            self._cancel = True
        self.destroy()


def open_autoplace_window(app):
    win = getattr(app, "_autoplace_window", None)
    if win is not None and win.winfo_exists():
        win.deiconify()
        win.lift()
        return win
    win = AutoPlaceWindow(app, app)
    app._autoplace_window = win
    return win
