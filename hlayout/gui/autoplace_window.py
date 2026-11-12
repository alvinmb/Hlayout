# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

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
        # the flow: Read Netlist -> design the resistors -> place
        self.flow = ttk.Label(files, text="", foreground="#555")
        self.flow.grid(row=3, column=1, sticky="w")
        fb = ttk.Frame(files)
        fb.grid(row=3, column=2, sticky="e")
        ttk.Button(fb, text="Read Netlist...", command=self._read_netlist).pack(side="left", padx=2)
        ttk.Button(fb, text="Resistor Designer...", command=self._open_designer).pack(side="left")

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
        self.cell_box = ttk.Combobox(side, textvariable=self.cell_var, width=24, state="readonly",
                                     postcommand=self.refresh_cells)
        self.cell_box.pack(anchor="w")
        self.cell_box.bind("<<ComboboxSelected>>", lambda e: self._map_footprint())
        ttk.Button(side, text="Map footprint", command=self._map_footprint).pack(anchor="w", pady=4)
        ttk.Label(side, text="Choosing a cell maps it at once;\nthe map is saved with the design\n"
                             "and next to the netlist\n(<netlist>.cellmap.json).",
                  foreground="#666", justify="left").pack(anchor="w", pady=(6, 0))
        self.parts.bind("<<TreeviewSelect>>", self._part_selected, add="+")
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
                 ("Thermal (0 = off)", "thermal"), ("Effort", "effort"), ("Seed", "seed"))
        for k, (label, key) in enumerate(items):
            r, c = divmod(k, 4)
            ttk.Label(sf, text=label + ":").grid(row=r, column=c * 2, sticky="e", padx=4, pady=2)
            ttk.Entry(sf, textvariable=self.v[key], width=8).grid(row=r, column=c * 2 + 1, sticky="w")
        ttk.Label(sf, text="Resistor conductor:").grid(row=2, column=0, sticky="e", padx=4, pady=2)
        self.res_box = ttk.Combobox(sf, textvariable=self.v["res_layer"], width=40, state="readonly")
        self.res_box.grid(row=2, column=1, columnspan=5, sticky="w")

        bf = ttk.Frame(f)
        bf.pack(fill="x", pady=(8, 0))
        self.run_btn = ttk.Button(bf, text="Run", command=lambda: self.run("run"))
        self.run_btn.pack(side="left")
        self.imp_btn = ttk.Button(bf, text="Improve", command=lambda: self.run("improve"))
        self.imp_btn.pack(side="left", padx=4)
        self.sel_btn = ttk.Button(bf, text="Place Selection", command=lambda: self.run("selection"))
        self.sel_btn.pack(side="left")
        self.spread_btn = ttk.Button(bf, text="Spread", command=self._spread)
        self.spread_btn.pack(side="left", padx=(4, 0))
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
        self._fill_res_layers(S)
        for cls, var in self.cw.items():
            var.set(f"{float(S['class_weights'].get(cls, 1.0)):g}")
        from ..scripting.autoplace_session import library_cells
        self.cell_box["values"] = sorted(library_cells(self.session))
        last = S.get("last_netlist") if isinstance(S, dict) else None
        if last and not self.v["netlist"].get():
            self.v["netlist"].set(last)
        if self.v["netlist"].get():
            self._load_netlist()

    def _layer_name(self, n):
        try:
            return f"{n}: {self.session.library.layers[n].name}"
        except Exception:
            return str(n)

    def _fill_res_layers(self, S):
        """Resistor conductor choices from the tech file: its default (the
        Resistor Geometry conductor pad layer) first, then every layer the
        tech file gives the conductor role."""
        from ..scripting.autoplace_session import conductor_layers, resistor_conductor_default
        lib = self.session.library
        d = resistor_conductor_default(lib)
        first = "Tech file default" + (f" ({self._layer_name(d)})" if d is not None else " (none set)")
        self._res_map = {first: None}
        layers = conductor_layers(lib)
        cur = S.get("resistor_layer")
        if cur not in (None, "") and int(cur) not in layers:
            layers = sorted(set(layers) | {int(cur)})
        for n in layers:
            self._res_map[self._layer_name(n)] = n
        self.res_box["values"] = list(self._res_map)
        self.v["res_layer"].set(first if cur in (None, "") else self._layer_name(int(cur)))

    def refresh_cells(self):
        """The cells offered: the design's and every attached library's,
        re-read each time the list opens (a library attached since)."""
        from ..scripting.autoplace_session import library_cells
        self.cell_box["values"] = sorted(library_cells(self.session))

    def _settings(self):
        out = {}
        for key, sk, typ in (("grid", "grid", float), ("clearance", "clearance", float),
                             ("bond_reach", "bond_reach", float), ("thermal", "thermal", float),
                             ("effort", "effort", float), ("seed", "seed", int)):
            try:
                out[sk] = typ(float(self.v[key].get()))
            except ValueError:
                raise LayoutError(f"{key}: not a number")
        out["resistor_layer"] = self._res_map.get(self.v["res_layer"].get())
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
        from ..scripting.autoplace_session import (_Cells, _family, _is_header, _net_class,
                                                   library_cells, match_footprint)
        path = self.v["netlist"].get().strip()
        try:
            self.nl = read_netlist(path)
        except Exception as e:
            self.summary.config(text=f"Can't read the netlist: {e}")
            self.nl = None
            return
        from ..scripting.autoplace_session import merged_footprint_map
        merged_footprint_map(self.session, path)
        S = self.session.autoplace_settings()
        lib = self.session.library
        avail = _Cells(self.session)
        cell = lib.cells.get(self.session.current_cell_name)
        placed = {r.label: r.cell_name for r in (cell.srefs if cell else []) if r.label}
        self.parts.delete(*self.parts.get_children())
        natural = lambda r: [int(t) if t.isdigit() else t for t in re.findall(r"\d+|\D+", r)]
        for ref, c in sorted(self.nl.components.items(), key=lambda kv: natural(kv[0])):
            cn = placed.get(ref) or match_footprint(avail, c.footprint, S["footprint_map"],
                                                    ref=ref) or ""
            fam = _family(avail, c, cn or None, set()) or ("no cell -- map one" if not cn else "")
            if fam != "die" and ref not in placed and _is_header(ref, c, cn or ""):
                fam = "header (not placed)"
            self.parts.insert("", "end", iid=ref, values=(ref, c.value, c.footprint, cn, fam))
        self.nets.delete(*self.nets.get_children())
        for n in self.nl.nets:
            cls = _net_class(n, S)
            w = S["weights"].get(n.name, S["class_weights"].get(cls, 1.0))
            self.nets.insert("", "end", iid=n.name, values=(n.name, cls, len(n.nodes), f"{float(w):g}"))
        self.summary.config(text=f"{len(self.nl.components)} parts, {len(self.nl.nets)} nets")
        self._show_flow(placed)

    def _show_flow(self, placed=None):
        """Where the flow stands: resistors designed / still to design."""
        from ..scripting.netlist_flow import resistor_status
        if self.nl is None:
            self.flow.config(text="")
            return
        try:
            st = resistor_status(self.session, self.nl, placed=set(placed or {}))
        except Exception as e:
            self.flow.config(text=f"Resistors: can't check ({e})", foreground="#c5221f")
            return
        d, u = st["designed"], st["undesigned"]
        if not d and not u:
            self.flow.config(text="No printed resistors in this netlist.", foreground="#555")
        elif u:
            self.flow.config(text=f"Resistors: {len(d)} designed, {len(u)} to design before "
                                  f"placing ({', '.join(u[:8])}{'...' if len(u) > 8 else ''})",
                             foreground="#c5221f")
        else:
            self.flow.config(text=f"Resistors: all {len(d)} designed -- ready to place.",
                             foreground="#1e8e3e")

    def _read_netlist(self):
        self.app.read_netlist()
        S = self.session.autoplace_settings()
        if S.get("last_netlist"):
            self.v["netlist"].set(S["last_netlist"])
        self._load_netlist()

    def _open_designer(self):
        from .resistor_designer import open_resistor_designer
        win = open_resistor_designer(self.app)
        try:                          # re-check the resistors when it closes
            win.bind("<Destroy>", lambda e: self.after(100, self._load_netlist)
                     if e.widget is win else None, add="+")
        except Exception:
            pass

    def _map_footprint(self):
        cell = self.cell_var.get()
        sel = self.parts.selection()
        if not cell or not sel:
            return
        S = self.session.autoplace_settings()
        fmap = dict(S["footprint_map"])
        placed = []
        for ref in sel:
            fp = self.parts.set(ref, "footprint")
            # by footprint name -- every part with that footprint follows;
            # a part the netlist gives no footprint is mapped on its own
            fmap[fp if fp else f"ref:{ref}"] = cell
            if self.parts.set(ref, "cell") and self.parts.set(ref, "cell") != cell:
                placed.append(ref)
        self.session.set_autoplace_settings(footprint_map=fmap)
        self._save_cellmap(fmap)
        self._load_netlist()
        keep = [r for r in sel if self.parts.exists(r)]
        if keep:
            self.parts.selection_set(keep)
            self.parts.see(keep[-1])
        still = [r for r in placed if self.parts.exists(r) and self.parts.set(r, "cell") != cell]
        if still:
            self.summary.config(text=f"{', '.join(still)} already placed as another cell -- "
                                     "delete the placed part to remap it")

    def _save_cellmap(self, fmap=None):
        from ..scripting.autoplace_session import save_cellmap
        path = self.v["netlist"].get().strip()
        if fmap is None:
            fmap = self.session.autoplace_settings()["footprint_map"]
        return save_cellmap(path, fmap) if path and fmap else None

    def _part_selected(self, _e=None):
        """Show the selected part's cell in the cell box."""
        sel = self.parts.selection()
        if len(sel) == 1:
            self.cell_var.set(self.parts.set(sel[0], "cell") or "")

    def _save_all(self):
        """Save everything entered in the window: the settings, the
        netlist path and the cell map (design + next to the netlist)."""
        try:
            out = self._settings()
        except Exception:
            out = {}
        path = self.v["netlist"].get().strip()
        if path:
            out["last_netlist"] = path
        try:
            self.session.set_autoplace_settings(**out)
        except Exception:
            pass
        self._save_cellmap()

    SPREAD_FILL = 0.6        # Spread: the parts (with clearance) cover this much of the area

    def _spread(self):
        """Spread the parts apart: the largest clearance (50 um steps) at
        which the parts, clearance included, still fill only SPREAD_FILL
        of the placement area -- set in the Clearance box -- then Improve."""
        from ..scripting.autoplace_session import build_job
        path = self.v["netlist"].get().strip()
        if not path:
            self._say("Pick the netlist first", "err")
            return
        try:
            st = self._settings()
            st["clearance"] = 0.0
            self.session.set_autoplace_settings(**st)
            placer, info = build_job(self.session, path,
                                     overrides=self.v["overrides"].get().strip() or None)
        except Exception as e:
            self._say(str(e), "err")
            return
        r = info["region"]
        area = (r[2] - r[0]) * (r[3] - r[1])
        dims = [(p.bbox[2] - p.bbox[0], p.bbox[3] - p.bbox[1]) for p in placer.parts]
        fill = lambda c: sum((a + c) * (b + c) for a, b in dims) / area
        c = 0.0
        while c < 20000 and fill(c + 50) <= self.SPREAD_FILL:
            c += 50
        self.v["clearance"].set(f"{c:g}")
        self.session.set_autoplace_settings(clearance=c)
        self._say(f"Spreading: clearance {c:g} um (parts fill {fill(c) * 100:.0f}% of the area)",
                  "run")
        self.run("improve")

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
        for b in (self.run_btn, self.imp_btn, self.sel_btn, self.spread_btn):
            b.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._say("Placing 0%", "run", 0)
        t0 = time.monotonic()
        prog = {"f": 0.0}

        def progress(f):
            prog["f"] = f

        self._result = None
        job = {}

        def tick():
            # the worker never touches Tk: it leaves its result here and
            # this poll (on the Tk thread) picks it up
            if self._result is not None:
                self._done(*self._result)
                return
            if self._running:
                ph = getattr(job.get("placer"), "phase", "") or "Placing"
                self._say(f"{ph} -- {int(100 * prog['f'])}% -- {time.monotonic() - t0:.0f} s",
                          "run", 100 * prog["f"])
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
        job["placer"] = placer

        def work():
            from ..core.autoplace import Stopped
            try:
                placer.run(effort=float(info["settings"]["effort"]), improve=(mode != "run"),
                           on_progress=progress, cancel=lambda: self._cancel)
                self._result = (placer, info, None, time.monotonic() - t0)
            except Stopped:
                self._result = (None, None, LayoutError("stopped before anything was placed"),
                                time.monotonic() - t0)
            except BaseException as e:            # never leave the window stuck 'running'
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
            for b in (self.run_btn, self.imp_btn, self.sel_btn, self.spread_btn):
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
        if info.get("header"):
            notes.append("header / connector, not placed on the substrate: "
                         + ", ".join(info["header"]))
        if info["unmapped"]:
            notes.append("no cell for: " + ", ".join(info["unmapped"]))
        if bad or out:
            notes.append(f"{len(bad)} overlap(s), {len(out)} outside")
        cb = info.get("carried") or {}
        if cb.get("bonds"):
            notes.append(f"{cb['bonds']} wire bond(s) and {cb.get('fingers', 0)} bond finger(s) "
                         "moved with their dies")
        if getattr(placer, "stuck", None):
            notes.append("no legal spot (left where they were): " + ", ".join(placer.stuck))
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
        self._save_all()
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
