"""Extract Netlist tool -- Tools > Extract Netlist...

A layer table in the same style as the Layers panel: one row per layer
with a checkbox for each role a layer can play in extraction --

* Conductor  -- substrate copper. Touching/overlapping shapes on any
  ticked conductor layer are one net.
* Isolated   -- pads that sit ABOVE the substrate (bare-die bond pads,
  header/package pins). Each shape is kept apart from the copper
  underneath and joins a net only where a wire bond lands on it.
  This column edits the Wire Bond Rules' isolated pad layers
  (techfile die_pad_layers), so it is the same setting as
  Setup > Technology Properties... > Wire Bonds.

The wire-bond layer itself is not tickable: its row is marked
"wire bonds (auto)" and bonds are included whenever "Include wire
bonds" is on.

Every tick is saved immediately into the design's tech_params
(techfile.save_netlist_settings / save_bond_rules), so the selection
comes back the next time the dialog opens, after a save/reopen, and is
what NETLIST RUN / Session.extract_netlist() use when no layers are
given.

Results come in two lists. Nets: each net's members, bond count,
unresolved count and open bond ends; clicking a net zooms the canvas to
it and rings every member pin. Issues: one row per problem -- an open
bond end, a pin on an unticked layer, a member with no RefDes or a
bonded pad with no pin number, a net with no pins -- each with its
location; clicking one zooms to it and drops a labelled crosshair
marker there (hlayout.gui.canvas.set_issue_markers()), with the run's
other issues ringed around it. Markers stay until Clear markers, the
next Run, or the window closing.
"""

import tkinter as tk
from tkinter import filedialog, ttk

from .fonts import section_header_font
from ..core.errors import LayoutError
from ..core.layers import NUM_LAYERS
from ..core.units import dbu_to_mm
from ..io.techfile import (
    load_bond_rules,
    load_netlist_settings,
    load_route_rules,
    save_bond_rules,
    save_netlist_settings,
)


def _used_layers(library):
    """Layer numbers that carry at least one shape anywhere in the
    design (every cell, not just the current one)."""
    used = set()
    for cell in library.cells.values():
        for shape in cell.shapes:
            used.add(shape.layer)
    return used


def _first_time_conductors(library):
    """A sensible starting selection for a design that has never saved
    one: the Route (2-Layer) layers, if Route Layers... has been set up
    for this design. Empty otherwise -- better to ask than to guess."""
    if "route_rules" not in library.tech_params:
        return []
    rules = load_route_rules(library)
    return sorted({int(rules[k]) for k in ("layer_a", "layer_b")
                   if isinstance(rules.get(k), int) and rules[k] >= 0})


class NetlistWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.session = app.session
        self.title("Extract Netlist")
        self.transient(master)
        self.geometry("780x880")
        self.minsize(620, 560)

        lib = self.session.library
        settings = load_netlist_settings(lib)
        conductors = settings["conductor_layers"]
        if not settings["saved"]:
            conductors = _first_time_conductors(lib)
        self._conductors = set(conductors)
        self._isolated = set(load_bond_rules(lib).get("die_pad_layers", []))
        self._isolated -= self._conductors
        self._nets = []
        self._issues = []
        self._run_cell = None
        self._rows = {}

        self.include_bonds_var = tk.BooleanVar(value=settings["include_bonds"])
        self.used_only_var = tk.BooleanVar(value=settings["used_only"])

        self._build()
        self._populate_layers()
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<F5>", lambda e: self.run())
        if settings["saved"] is False and conductors:
            self._save()

    # ------------------------------------------------------------------ UI

    def _build(self):
        ttk.Label(
            self,
            text="Tick Conductor for substrate copper (routing layers, printed pads). "
                 "Tick Isolated for pads that sit above the substrate and are reached "
                 "only by wire bonds (die pads, header pins). Leave everything else "
                 "unticked. Selections are saved with the design.",
            wraplength=720, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        top = ttk.Frame(self)
        top.pack(fill="x", padx=12)
        ttk.Checkbutton(top, text="Show only layers used in the design",
                        variable=self.used_only_var,
                        command=self._on_used_only).pack(side="left")
        ttk.Button(top, text="Clear all", command=self._clear_all).pack(side="right")

        # --- layer table (scrollable, Layers-panel style) -------------
        table = ttk.Frame(self, relief="groove", borderwidth=1)
        table.pack(fill="both", expand=False, padx=12, pady=(6, 6))
        header = ttk.Frame(table)
        header.pack(fill="x", padx=4, pady=(4, 0))
        hf = section_header_font(self)
        for text, w in (("Conductor", 9), ("Isolated", 8), ("#", 4), ("Name", 22),
                        ("", 3), ("Role", 24)):
            ttk.Label(header, text=text, width=w, font=hf).pack(side="left")

        body = ttk.Frame(table)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, highlightthickness=0, height=250)
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
        self._layer_inner = inner
        self._layer_canvas = canvas

        # --- run options -----------------------------------------------
        opts = ttk.Frame(self)
        opts.pack(fill="x", padx=12, pady=(2, 4))
        ttk.Label(opts, text="Cell:").pack(side="left")
        self.cell_var = tk.StringVar(value=self.session.current_cell_name or "")
        self.cell_combo = ttk.Combobox(opts, textvariable=self.cell_var, state="readonly",
                                       width=24, values=list(self.session.library.cells.keys()))
        self.cell_combo.pack(side="left", padx=(4, 16))
        ttk.Checkbutton(opts, text="Include wire bonds", variable=self.include_bonds_var,
                        command=self._on_include_bonds).pack(side="left")
        ttk.Button(opts, text="Run  (F5)", command=self.run).pack(side="right")

        self.summary_var = tk.StringVar(value="")
        self.summary_label = ttk.Label(self, textvariable=self.summary_var, wraplength=720,
                                       justify="left")
        self.summary_label.pack(anchor="w", padx=12)

        # --- results: nets (top) and issues (bottom), resizable split ----
        panes = ttk.PanedWindow(self, orient="vertical")
        panes.pack(fill="both", expand=True, padx=12, pady=(4, 6))

        nets_frame = ttk.Labelframe(panes, text="Nets  (click a net to mark its pins)")
        self.tree = self._make_tree(nets_frame, (
            ("net", "Net", 80, "w", False), ("members", "Members", 360, "w", True),
            ("bonds", "Bonds", 50, "center", False),
            ("unresolved", "Unresolved", 75, "center", False),
            ("open", "Open bond ends", 150, "w", True)), height=8)
        self.tree.tag_configure("unassigned", background="#f8d7da", foreground="#000")
        self.tree.tag_configure("open", background="#fff3cd", foreground="#000")
        self.tree.bind("<<TreeviewSelect>>", self._on_net_select)
        panes.add(nets_frame, weight=3)

        issues_frame = ttk.Labelframe(panes, text="Issues  (click to show it on the layout)")
        self.issues = self._make_tree(issues_frame, (
            ("type", "Type", 110, "w", False), ("net", "Net", 80, "w", False),
            ("item", "Item", 300, "w", True), ("where", "Location (mm)", 140, "w", False)),
            height=6)
        for kind, bg in (("open", "#f8d7da"), ("unassigned", "#f8d7da"),
                         ("unresolved", "#fff3cd"), ("floating", "#ffe5cc")):
            self.issues.tag_configure(kind, background=bg, foreground="#000")
        self.issues.bind("<<TreeviewSelect>>", self._on_issue_select)
        panes.add(issues_frame, weight=2)

        btns = ttk.Frame(self)
        btns.pack(pady=(0, 12))
        ttk.Button(btns, text="Export CSV...", command=self.export_csv).pack(side="left", padx=6)
        ttk.Button(btns, text="Clear markers", command=self.clear_markers).pack(side="left", padx=6)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="left", padx=6)

    @staticmethod
    def _make_tree(parent, columns, height):
        tree = ttk.Treeview(parent, columns=[c[0] for c in columns], show="headings",
                            height=height, selectmode="browse")
        for c, text, w, anchor, stretch in columns:
            tree.heading(c, text=text)
            tree.column(c, width=w, anchor=anchor, stretch=stretch)
        ysb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        xsb = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)
        tree.grid(row=0, column=0, sticky="nsew", padx=(4, 0), pady=(4, 0))
        ysb.grid(row=0, column=1, sticky="ns", pady=(4, 0))
        xsb.grid(row=1, column=0, sticky="ew", padx=(4, 0))
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        return tree

    # ------------------------------------------------------------ layers

    @staticmethod
    def _force_checkbox(cb, var, value):
        # Same lock-step trick as LayerPanel._force_checkbox: keep the ttk
        # selected flag, the variable and the model in agreement when a
        # box is changed programmatically (mutual exclusion, Clear all).
        var.set(value)
        cb.state(["selected"] if value else ["!selected"])

    def _bond_layer(self):
        b = int(load_bond_rules(self.session.library).get("layer", -1))
        return b if b >= 0 else None

    def _populate_layers(self):
        for w in self._layer_inner.winfo_children():
            w.destroy()
        self._rows = {}
        lib = self.session.library
        used = _used_layers(lib)
        bond = self._bond_layer()
        show_all = not self.used_only_var.get()
        for n in range(NUM_LAYERS):
            ticked = n in self._conductors or n in self._isolated
            if not (show_all or n in used or ticked or n == bond):
                continue
            self._make_row(n, n in used, n == bond)
        self._layer_canvas.yview_moveto(0)

    def _make_row(self, n, used, is_bond):
        layer = self.session.library.layers[n]
        row = ttk.Frame(self._layer_inner)
        row.pack(fill="x")

        cond_var = tk.BooleanVar(value=n in self._conductors)
        iso_var = tk.BooleanVar(value=n in self._isolated)
        cond_cb = ttk.Checkbutton(row, variable=cond_var, width=9,
                                  command=lambda: self._on_toggle(n, "cond"))
        iso_cb = ttk.Checkbutton(row, variable=iso_var, width=8,
                                 command=lambda: self._on_toggle(n, "iso"))
        cond_cb.pack(side="left", padx=(14, 0))
        iso_cb.pack(side="left")
        ttk.Label(row, text=str(n), width=4).pack(side="left")
        ttk.Label(row, text=layer.name, width=22,
                  foreground=("" if used else "#999")).pack(side="left")
        tk.Label(row, bg=layer.color, width=2, relief="sunken").pack(side="left", padx=(0, 8))
        role_var = tk.StringVar()
        ttk.Label(row, textvariable=role_var, width=30, foreground="#555").pack(side="left")
        if is_bond:
            cond_cb.state(["disabled"])
            iso_cb.state(["disabled"])
        self._force_checkbox(cond_cb, cond_var, n in self._conductors)
        self._force_checkbox(iso_cb, iso_var, n in self._isolated)
        self._rows[n] = {"cond_var": cond_var, "cond_cb": cond_cb,
                         "iso_var": iso_var, "iso_cb": iso_cb,
                         "role_var": role_var, "used": used, "bond": is_bond}
        self._update_role(n)

    def _update_role(self, n):
        r = self._rows.get(n)
        if r is None:
            return
        if r["bond"]:
            text = "wire bonds (auto)" if self.include_bonds_var.get() else "wire bonds (off)"
        elif n in self._conductors:
            text = "conductor"
        elif n in self._isolated:
            text = "isolated pads (bond only)"
        else:
            text = "" if r["used"] else "no shapes"
        r["role_var"].set(text)

    def _on_toggle(self, n, which):
        r = self._rows[n]
        if which == "cond":
            on = r["cond_var"].get()
            self._conductors.discard(n)
            if on:
                self._conductors.add(n)
                self._isolated.discard(n)
                self._force_checkbox(r["iso_cb"], r["iso_var"], False)
        else:
            on = r["iso_var"].get()
            self._isolated.discard(n)
            if on:
                self._isolated.add(n)
                self._conductors.discard(n)
                self._force_checkbox(r["cond_cb"], r["cond_var"], False)
        self._update_role(n)
        self._save()

    def _clear_all(self):
        self._conductors.clear()
        self._isolated.clear()
        for n, r in self._rows.items():
            self._force_checkbox(r["cond_cb"], r["cond_var"], False)
            self._force_checkbox(r["iso_cb"], r["iso_var"], False)
            self._update_role(n)
        self._save()

    def _on_used_only(self):
        self._populate_layers()
        self._save()

    def _on_include_bonds(self):
        for n in self._rows:
            self._update_role(n)
        self._save()

    def _save(self):
        lib = self.session.library
        save_netlist_settings(lib, {
            "conductor_layers": sorted(self._conductors),
            "include_bonds": self.include_bonds_var.get(),
            "used_only": self.used_only_var.get(),
        })
        rules = load_bond_rules(lib)
        if sorted(rules.get("die_pad_layers", [])) != sorted(self._isolated):
            rules["die_pad_layers"] = sorted(self._isolated)
            save_bond_rules(lib, rules)

    def reload(self):
        """Re-read everything from the library (e.g. after the Wire Bond
        Rules were edited elsewhere, or the cell list changed)."""
        lib = self.session.library
        s = load_netlist_settings(lib)
        self._conductors = set(s["conductor_layers"])
        self._isolated = set(load_bond_rules(lib).get("die_pad_layers", [])) - self._conductors
        self.include_bonds_var.set(s["include_bonds"])
        self.used_only_var.set(s["used_only"])
        self.cell_combo["values"] = list(lib.cells.keys())
        self._populate_layers()

    # ------------------------------------------------------------ run

    def run(self):
        self.tree.delete(*self.tree.get_children())
        self.issues.delete(*self.issues.get_children())
        self._nets = []
        self._issues = []
        self.clear_markers()
        if not self._conductors:
            self._set_summary("Tick at least one Conductor layer.", error=True)
            return
        cell = self.cell_var.get() or self.session.current_cell_name
        try:
            nets = self.session.extract_netlist(
                sorted(self._conductors), cell_name=cell or None,
                include_bonds=self.include_bonds_var.get())
        except LayoutError as e:
            self._set_summary(str(e), error=True)
            return
        self._nets = nets
        self._run_cell = cell
        real = [n for n in nets if n.id != "UNASSIGNED"]
        for i, net in enumerate(nets):
            members = ", ".join(p.label() for p in net.pins) or "(no pins)"
            opens = getattr(net, "open_bond_ends", [])
            if net.id == "UNASSIGNED":
                tags = ("unassigned",)
            else:
                tags = ("open",) if opens else ()
            self.tree.insert("", "end", iid=str(i), tags=tags, values=(
                net.id, members, getattr(net, "bonds", 0) or "",
                net.unresolved_count or "",
                "; ".join(f"{x},{y}" for x, y in opens)))
            self._collect_issues(net)

        order = {"open": 0, "unassigned": 1, "unresolved": 2, "floating": 3}
        self._issues.sort(key=lambda it: order[it["kind"]])
        for k, it in enumerate(self._issues):
            x, y = it["xy"]
            self.issues.insert("", "end", iid=str(k), tags=(it["kind"],), values=(
                it["type"], it["net"], it["item"],
                f"{dbu_to_mm(x):.3f}, {dbu_to_mm(y):.3f}"))

        counts = {}
        for it in self._issues:
            counts[it["type"]] = counts.get(it["type"], 0) + 1
        parts = [f"{len(real)} net(s)"]
        parts += [f"{n} {t.lower()}" for t, n in counts.items()]
        if self._issues:
            parts.append("click an issue to show it on the layout")
        hard = any(it["kind"] in ("open", "unassigned") for it in self._issues)
        self._set_summary(";  ".join(parts), error=hard)
        try:
            self.app.status.set(f"Extract Netlist: {len(real)} net(s), "
                                f"{len(self._issues)} issue(s)")
        except Exception:
            pass

    def _collect_issues(self, net):
        """Turn one Net into Issues-list rows, each with a location."""
        add = self._issues.append
        if net.id == "UNASSIGNED":
            for p in net.pins:
                if p.xy is not None:
                    add({"kind": "unassigned", "type": "Unassigned pin", "net": "-",
                         "item": f"{p.label()}  -- pin is on a layer not ticked",
                         "xy": p.xy, "label": f"{p.label()} (unassigned)"})
            return
        for p in net.pins:
            if p.xy is None:
                continue
            if p.pin.startswith("@"):
                add({"kind": "unresolved", "type": "Unresolved", "net": net.id,
                     "item": f"{p.label()}  -- bonded pad has no pin number",
                     "xy": p.xy, "label": f"{p.ref_des or '?'} pad: no pin number"})
            elif not p.ref_des:
                add({"kind": "unresolved", "type": "Unresolved", "net": net.id,
                     "item": f"{p.label()}  -- part has no RefDes",
                     "xy": p.xy, "label": f"{p.label()}: no RefDes"})
        for xy in getattr(net, "open_bond_ends", []):
            add({"kind": "open", "type": "Open bond end", "net": net.id,
                 "item": "wire bond end lands on nothing", "xy": tuple(xy),
                 "label": f"{net.id}: open bond end"})
        if not net.pins and getattr(net, "location", None) is not None:
            add({"kind": "floating", "type": "No pins", "net": net.id,
                 "item": "copper with no pins on it"
                         + (f" ({net.bonds} bond(s))" if getattr(net, "bonds", 0) else ""),
                 "xy": net.location, "label": f"{net.id}: no pins"})

    def _set_summary(self, text, error=False):
        self.summary_var.set(text)
        self.summary_label.configure(foreground=("#b00020" if error else ""))

    # ------------------------------------------------------- markers

    def _show_cell(self):
        """Make the canvas show the cell the netlist was run on, so the
        marker coordinates line up."""
        cell = getattr(self, "_run_cell", None)
        canvas = self.app.canvas
        if cell and canvas.cell_name != cell:
            self.session.use_cell(cell)
            canvas.set_cell(cell)
            try:
                self.app.cell_var.set(cell)
            except Exception:
                pass
        return canvas

    def _all_issue_markers(self):
        return [{"x": it["xy"][0], "y": it["xy"][1], "kind": it["kind"]}
                for it in self._issues]

    def _on_issue_select(self, event=None):
        sel = self.issues.selection()
        if not sel:
            return
        it = self._issues[int(sel[0])]
        canvas = self._show_cell()
        x, y = it["xy"]
        r = 800
        canvas.zoom_to_window(x - r, y - r, x + r, y + r)
        markers = [m for m in self._all_issue_markers() if (m["x"], m["y"]) != (x, y)]
        markers.append({"x": x, "y": y, "kind": it["kind"], "label": it["label"],
                        "active": True})
        canvas.set_issue_markers(markers, cell_name=self._run_cell)

    def _on_net_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        net = self._nets[int(sel[0])]
        markers = []
        for p in net.pins:
            if p.xy is None:
                continue
            bad = net.id == "UNASSIGNED" or not p.ref_des or p.pin.startswith("@")
            kind = ("unassigned" if net.id == "UNASSIGNED"
                    else "unresolved" if bad else "pin")
            markers.append({"x": p.xy[0], "y": p.xy[1], "kind": kind})
        for x, y in getattr(net, "open_bond_ends", []):
            markers.append({"x": x, "y": y, "kind": "open"})
        if not markers and getattr(net, "location", None) is not None:
            markers.append({"x": net.location[0], "y": net.location[1],
                            "kind": "floating", "label": f"{net.id}: no pins",
                            "active": True})
        if not markers:
            return
        canvas = self._show_cell()
        xs = [m["x"] for m in markers]
        ys = [m["y"] for m in markers]
        pad = 600
        canvas.zoom_to_window(min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
        canvas.set_issue_markers(markers, cell_name=self._run_cell)

    def clear_markers(self):
        try:
            self.app.canvas.clear_issue_markers()
        except Exception:
            pass

    def destroy(self):
        self.clear_markers()
        super().destroy()

    def export_csv(self):
        if not self._nets:
            self._set_summary("Run extraction first.", error=True)
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Export Netlist CSV", initialfile="netlist.csv",
            defaultextension=".csv", filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")])
        if not path:
            return
        self.session.export_netlist_csv(self._nets, path)
        self._set_summary(f"Netlist exported to {path}")


def open_netlist_window(app):
    """Tools > Extract Netlist... . One window per App: calling again
    brings the existing one to the front (reloaded from the library)."""
    win = getattr(app, "_netlist_window", None)
    if win is not None and win.winfo_exists():
        win.reload()
        win.lift()
        win.focus_set()
        return win
    win = NetlistWindow(app, app)
    app._netlist_window = win
    return win
