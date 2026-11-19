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
                                               "seed", "clearance", "bond_reach", "res_layer",
                                               "sli_crossing", "edge")}
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
        self.parts = ttk.Treeview(pf, columns=("ref", "value", "footprint", "cell", "family", "lock"),
                                  show="headings", height=8, style="AP.Treeview")
        for col, w in (("ref", 60), ("value", 140), ("footprint", 200), ("cell", 150), ("family", 70),
                       ("lock", 60)):
            self.parts.heading(col, text="Locked" if col == "lock" else col.capitalize())
            self.parts.column(col, width=w, anchor="center" if col == "lock" else "w")
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
        ttk.Button(side, text="Clear mapping", command=self._clear_mapping).pack(anchor="w")
        lk = ttk.Frame(side)
        lk.pack(anchor="w", pady=(10, 0))
        ttk.Button(lk, text="Lock", command=lambda: self._lock_selected(True)).pack(side="left")
        ttk.Button(lk, text="Unlock", command=lambda: self._lock_selected(False)).pack(
            side="left", padx=(4, 0))
        ttk.Label(side, text="Choosing a cell maps it at once;\nthe map is saved with the design\n"
                             "and next to the netlist\n(<netlist>.cellmap.json).\n"
                             "Clear mapping takes the selected\nparts' cell off again.\n"
                             "Lock keeps placed parts where\nthey are: Run / Improve / Spread\n"
                             "and the router's nudge leave\nthem, and the canvas won't move them.",
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
        # report: what the last run did and what needs attention
        rf = ttk.Frame(nb, padding=4)
        nb.add(rf, text="Report")
        self.nb, self.report_tab = nb, rf
        rf.columnconfigure(0, weight=1)
        rf.rowconfigure(0, weight=1)
        self.rep = ttk.Treeview(rf, columns=("detail",), show="tree headings", height=8,
                                style="AP.Treeview")
        self.rep.heading("#0", text="Item")
        self.rep.heading("detail", text="Detail")
        self.rep.column("#0", width=230, stretch=False)
        self.rep.column("detail", width=560)
        self.rep.tag_configure("error", foreground="#c5221f")
        self.rep.tag_configure("warning", foreground="#b35c00")
        self.rep.tag_configure("section", font=("Segoe UI", 10, "bold"))
        self.rep.grid(row=0, column=0, sticky="nsew")
        sb3 = ttk.Scrollbar(rf, command=self.rep.yview)
        self.rep.configure(yscrollcommand=sb3.set)
        sb3.grid(row=0, column=1, sticky="ns")
        side3 = ttk.Frame(rf)
        side3.grid(row=0, column=2, sticky="n", padx=6)
        ttk.Button(side3, text="Show on board", command=self._rep_show).pack(fill="x")
        self.fix_btn = ttk.Button(side3, text="Apply fix", command=self._rep_fix, state="disabled")
        self.fix_btn.pack(fill="x", pady=4)
        self.fixall_btn = ttk.Button(side3, text="Apply all fixes", command=self._rep_fix_all,
                                     state="disabled")
        self.fixall_btn.pack(fill="x")
        ttk.Button(side3, text="Check Routability", command=self._rep_check).pack(fill="x", pady=(12, 0))
        ttk.Button(side3, text="Open Channels", command=self._rep_open).pack(fill="x", pady=(4, 0))
        ttk.Button(side3, text="Copy", command=self._rep_copy).pack(fill="x", pady=(12, 4))
        ttk.Button(side3, text="Save...", command=self._rep_save).pack(fill="x")
        ttk.Label(side3, text="Double-click a row to\nselect its parts and\nzoom to them.",
                  foreground="#666", justify="left").pack(anchor="w", pady=(10, 0))
        self.rep_text = tk.Text(rf, height=3, wrap="word", relief="flat", background="#f4f4f4")
        self.rep_text.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        self.rep_text.configure(state="disabled")
        self.rep.bind("<<TreeviewSelect>>", self._rep_selected, add="+")
        self.rep.bind("<Double-1>", lambda e: self._rep_show())
        self._rep_rows, self._report, self._last = {}, None, None
        # settings
        sf = ttk.LabelFrame(f, text="Settings (saved with the design)", padding=8)
        sf.pack(fill="x", pady=(8, 0))
        items = (("Grid (um)", "grid"), ("Clearance (um)", "clearance"), ("Bond reach (um)", "bond_reach"),
                 ("Thermal (0 = off)", "thermal"), ("Effort", "effort"), ("Seed", "seed"),
                 ("SLI crossing (um)", "sli_crossing"), ("Edge (um)", "edge"))
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
                        ("thermal", "thermal"), ("effort", "effort"), ("seed", "seed"),
                        ("sli_crossing", "sli_crossing"), ("edge", "edge")):
            self.v[key].set(f"{float(S.get(sk, 0) or 0):g}")
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
                             ("effort", "effort", float), ("seed", "seed", int),
                             ("sli_crossing", "sli_crossing", float), ("edge", "edge", float)):
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
        try:
            locked = self.session.locked_parts(self.session.current_cell_name)
        except Exception:
            locked = set()
        self.parts.delete(*self.parts.get_children())
        natural = lambda r: [int(t) if t.isdigit() else t for t in re.findall(r"\d+|\D+", r)]
        for ref, c in sorted(self.nl.components.items(), key=lambda kv: natural(kv[0])):
            cn = placed.get(ref) or match_footprint(avail, c.footprint, S["footprint_map"],
                                                    ref=ref) or ""
            fam = _family(avail, c, cn or None, set()) or ("no cell -- map one" if not cn else "")
            if fam != "die" and ref not in placed and _is_header(ref, c, cn or ""):
                fam = "header (not placed)"
            self.parts.insert("", "end", iid=ref, values=(ref, c.value, c.footprint, cn, fam,
                                                          "locked" if ref in locked else ""))
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

    def _clear_mapping(self):
        """Take the cell off the selected parts: drop their entries
        (footprint name, or ref:<ref> for a part with no footprint) from
        the footprint map, in the design and in <netlist>.cellmap.json --
        the file too, or loading the netlist would add them back. A part
        already placed keeps its cell until the placed part is deleted."""
        sel = self.parts.selection()
        if not sel:
            self.summary.config(text="Select the part(s) to clear first.")
            return
        S = self.session.autoplace_settings()
        fmap = dict(S["footprint_map"])
        cell = self.session.library.cells.get(self.session.current_cell_name)
        placed = {r.label for r in (cell.srefs if cell else []) if r.label}
        cleared, kept = [], []
        for ref in sel:
            if ref in placed:
                kept.append(ref)
                continue
            fp = self.parts.set(ref, "footprint")
            for key in ((fp,) if fp else ()) + (f"ref:{ref}",):
                if fmap.pop(key, None) is not None:
                    cleared.append(ref)
        self.session.set_autoplace_settings(footprint_map=fmap)
        self._save_cellmap(fmap)
        self._load_netlist()
        keep = [r for r in sel if self.parts.exists(r)]
        if keep:
            self.parts.selection_set(keep)
            self.parts.see(keep[-1])
        msg = []
        if cleared:
            msg.append(f"mapping cleared: {', '.join(sorted(set(cleared)))}")
        if kept:
            msg.append(f"{', '.join(kept)} already placed -- delete the placed part to clear it")
        if not msg:
            msg.append("nothing mapped to clear (the cell was matched by its footprint name)")
        self.summary.config(text="; ".join(msg))
        self._part_selected()

    def _lock_selected(self, lock=True):
        """Lock (or unlock) the selected parts where they are placed --
        Session.lock_parts, one undo step. Only placed parts can be locked."""
        sel = self.parts.selection()
        if not sel:
            self.summary.config(text="Select the part(s) to lock first.")
            return
        name = self.session.current_cell_name
        cell = self.session.library.cells.get(name)
        placed = {r.label for r in (cell.srefs if cell else []) if r.label}
        refs = [r for r in sel if r in placed]
        missing = [r for r in sel if r not in placed]
        if refs:
            try:
                self.session.lock_parts(refs, cell_name=name, lock=lock)
            except Exception as e:
                self.summary.config(text=str(e))
                return
        self._load_netlist()
        keep = [r for r in sel if self.parts.exists(r)]
        if keep:
            self.parts.selection_set(keep)
            self.parts.see(keep[-1])
        msg = []
        if refs:
            msg.append(f"{'locked' if lock else 'unlocked'}: {', '.join(refs)}")
        if missing:
            msg.append(f"{', '.join(missing)} not placed yet -- place first, then lock")
        self.summary.config(text="; ".join(msg))
        try:
            self.app.canvas.redraw()
        except Exception:
            pass

    def _save_cellmap(self, fmap=None):
        from ..scripting.autoplace_session import save_cellmap
        path = self.v["netlist"].get().strip()
        if fmap is None:
            fmap = self.session.autoplace_settings()["footprint_map"]
        # written even when empty, so a cleared mapping stays cleared
        return save_cellmap(path, fmap) if path and fmap is not None else None

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
                self._say(f"FAILED -- {str(err).splitlines()[0][:120]} (full text on the Report tab)", "err")
                self._show_error(err)
            return
        from ..scripting.autoplace_session import apply
        s = self.session
        with s._batch():
            apply(s, placer, info)
        s._notify()
        self.app.refresh_all() if hasattr(self.app, "refresh_all") else self.app.canvas.redraw()
        self.app.set_sli(True)
        self._last = (placer, info, secs)
        rep = self._show_report(placer, info, secs)
        if alive:
            self._say(rep["headline"], "err" if rep["errors"] else ("warn" if rep["warnings"] else "ok"),
                      100)
            try:
                if rep["errors"] or rep["warnings"]:
                    self.nb.select(self.report_tab)
                self.bell()
                self.lift()
            except tk.TclError:
                pass

    # ------------------------------------------------------------- report
    def _show_report(self, placer, info, secs):
        from ..scripting.autoplace_report import build_report
        rep = build_report(self.session, placer, info, secs)
        self._report = rep
        try:
            if not self.winfo_exists():
                return rep
        except tk.TclError:
            return rep
        self._fill_tree(rep)
        return rep

    def _fill_tree(self, rep):
        self.rep.delete(*self.rep.get_children())
        self._rep_rows = {}
        for sec in rep["sections"]:
            sid = self.rep.insert("", "end", text=sec["title"], open=True, tags=("section",))
            if not sec["rows"]:
                self.rep.insert(sid, "end", text="(none)")
            for row in sec["rows"]:
                iid = self.rep.insert(sid, "end", text=row["item"], values=(row["detail"],),
                                      tags=(row["sev"],))
                self._rep_rows[iid] = row
        n = sum(1 for r in self._rep_rows.values() if r.get("fix"))
        self.fixall_btn.config(state="normal" if n else "disabled",
                               text=f"Apply all fixes ({n})" if n else "Apply all fixes")
        self.fix_btn.config(state="disabled")
        self._rep_note("")

    def _rep_check(self):
        """Check Routability: the router's own view of the board, no routing
        (hlayout.scripting.routability) -- added to the report."""
        from ..scripting.routability import check
        try:
            rr = check(self.session)
        except Exception as e:
            self._say(f"Check Routability failed -- {e}", "err")
            return
        rep = self._report or {"headline": "", "sections": [], "errors": 0, "warnings": 0}
        rep["sections"] = [sc for sc in rep["sections"]
                           if not sc["title"].startswith("Routability")] + rr["sections"]
        self._report = rep
        self._fill_tree(rep)
        try:
            self.nb.select(self.report_tab)
            last = self.rep.get_children()[-1]
            self.rep.see(last)
        except (tk.TclError, IndexError):
            pass
        self._say(rr["headline"], "err" if rr["errors"] else ("warn" if rr["warnings"] else "ok"), None)

    def _rep_open(self):
        """Open Channels: move only the parts that wall pads in, re-checking
        after every pass (hlayout.scripting.open_channels)."""
        if self._running:
            return
        from ..scripting.open_channels import open_channels
        path = self.v["netlist"].get().strip() or None
        self._say("Opening channels...", "run", None)
        self.update_idletasks()
        try:
            r = open_channels(self.session, netlist=path,
                              on_line=lambda m: (self._say("Open Channels: " + m, "run", None),
                                                 self.update_idletasks()))
        except Exception as e:
            self._say(f"Open Channels failed -- {e}", "err")
            return
        self.app.refresh_all() if hasattr(self.app, "refresh_all") else self.app.canvas.redraw()
        self._last = None                     # the placement moved: old fixes no longer apply
        rows = [{"sev": "info", "item": "Open Channels",
                 "detail": f"{r['moves']} move(s) in {r['passes']} pass(es): walled-in connections "
                           f"{r['walled_before']} -> {r['walled_after']} of {r['connections']}",
                 "refs": [], "box": None, "fix": None}]
        rows += [{"sev": "info", "item": ln.split(":", 1)[0], "detail": ln.split(":", 1)[1].strip(),
                  "refs": [w for w in ln.split() if w[:1] in "QRCUDL" and w[1:2].isdigit()][:2],
                  "box": None, "fix": None} for ln in r["log"]]
        rep = self._report or {"headline": "", "sections": [], "errors": 0, "warnings": 0}
        rep["sections"] = [sc for sc in rep["sections"]
                           if not sc["title"].startswith(("Routability", "Open Channels"))]
        rep["sections"] += [{"title": "Open Channels", "rows": rows}] + r["report"]["sections"]
        self._report = rep
        self._fill_tree(rep)
        try:
            self.nb.select(self.report_tab)
        except tk.TclError:
            pass
        self._say(f"Open Channels: walled-in {r['walled_before']} -> {r['walled_after']} "
                  f"({r['moves']} move(s), one undo step per pass) -- "
                  + r["report"]["headline"], "ok" if not r["walled_after"] else "warn", None)

    def _show_error(self, err):
        self._report = {"headline": f"FAILED -- {err}", "sections": [
            {"title": "Problems (1)", "rows": [{"sev": "error", "item": "Auto Place failed",
                                                "detail": str(err), "refs": [], "box": None,
                                                "fix": None}]}]}
        self.rep.delete(*self.rep.get_children())
        self._rep_rows = {}
        sid = self.rep.insert("", "end", text="Problems (1)", open=True, tags=("section",))
        row = self._report["sections"][0]["rows"][0]
        iid = self.rep.insert(sid, "end", text=row["item"], values=(row["detail"],), tags=("error",))
        self._rep_rows[iid] = row
        self.fix_btn.config(state="disabled")
        self.fixall_btn.config(state="disabled", text="Apply all fixes")
        self.rep.selection_set(iid)
        self.nb.select(self.report_tab)

    def _rep_note(self, text):
        self.rep_text.configure(state="normal")
        self.rep_text.delete("1.0", "end")
        self.rep_text.insert("1.0", text)
        self.rep_text.configure(state="disabled")

    def _rep_row(self):
        sel = self.rep.selection()
        return self._rep_rows.get(sel[0]) if sel else None

    def _rep_selected(self, _e=None):
        row = self._rep_row()
        if row is None:
            self.fix_btn.config(state="disabled")
            self._rep_note("")
            return
        fix = row.get("fix")
        self.fix_btn.config(state="normal" if fix and self._last else "disabled")
        self._rep_note(f"{row['item']}: {row['detail']}"
                       + (f"\nParts: {', '.join(row['refs'])}" if row.get("refs") else ""))

    def _rep_show(self):
        row = self._rep_row()
        if row is None:
            return
        cv = self.app.canvas
        cell = self.session.library.cells.get(self.session.current_cell_name)
        refs = set(row.get("refs") or [])
        hits = [r for r in (cell.srefs if cell is not None else []) if r.label in refs]
        cv.selected = None
        cv.selected_multi = [("sref", r) for r in hits]
        box = row.get("box")
        if box is None and hits:
            from ..scripting.autoplace_session import _xf
            pts = []
            for r in hits:
                b = self.session.library.cell_bbox(r.cell_name)
                if b:
                    f = _xf(r)
                    pts += [f((b[0], b[1])), f((b[2], b[3]))]
            if pts:
                box = (min(x for x, _ in pts) - 1000, min(y for _, y in pts) - 1000,
                       max(x for x, _ in pts) + 1000, max(y for _, y in pts) + 1000)
        if box:
            cv.zoom_to_window(*box, padding=0.1)
        cv.redraw()

    def _apply_fixes(self, rows):
        if not self._last or self._running:
            return
        placer, info, secs = self._last
        by = {p.ref: p for p in placer.parts}
        done = []
        for row in rows:
            fix = row.get("fix")
            p = by.get(fix["ref"]) if fix else None
            if p is None:
                continue
            p.x += fix["dx"]
            p.y += fix["dy"]
            done.append(fix["ref"])
        if not done:
            return
        from ..scripting.autoplace_session import apply
        s = self.session
        with s._batch():
            apply(s, placer, info)
        s._notify()
        self.app.refresh_all() if hasattr(self.app, "refresh_all") else self.app.canvas.redraw()
        rep = self._show_report(placer, info, secs)
        self._say(f"Moved {', '.join(done)} (one undo step) -- " + rep["headline"],
                  "err" if rep["errors"] else ("warn" if rep["warnings"] else "ok"), None)

    def _rep_fix(self):
        row = self._rep_row()
        if row and row.get("fix"):
            self._apply_fixes([row])

    def _rep_fix_all(self):
        # one at a time: each fix was worked out against the placement as it
        # was, so re-check after every move and stop when none is left
        for _ in range(50):
            rows = [r for r in self._rep_rows.values() if r.get("fix")]
            if not rows or not self._last:
                break
            self._apply_fixes(rows[:1])

    def _rep_copy(self):
        if not self._report:
            return
        from ..scripting.autoplace_report import to_text
        self.clipboard_clear()
        self.clipboard_append(to_text(self._report))
        self._rep_note("Report copied to the clipboard.")

    def _rep_save(self):
        if not self._report:
            return
        from ..scripting.autoplace_report import to_text
        dp = getattr(self.session, "design_path", "") or ""
        dp = dp if isinstance(dp, str) else ""
        base = os.path.splitext(dp)[0] if dp else "autoplace"
        path = filedialog.asksaveasfilename(parent=self, title="Save Auto Place report",
                                            defaultextension=".txt",
                                            initialdir=os.path.dirname(dp) or None,
                                            initialfile=os.path.basename(base) + "_autoplace.txt",
                                            filetypes=(("Text", "*.txt"), ("All files", "*.*")))
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(to_text(self._report))
            self._rep_note(f"Saved {path}")

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
