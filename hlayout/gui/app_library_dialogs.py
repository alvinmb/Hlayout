# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Library dialogs: attached libraries, browse / find-and-place library
cells, merge GDS, save as library.

Part of hlayout.gui.app.App (a mixin): these methods are App's own --
call them as always."""

import tkinter as tk
from tkinter import filedialog, simpledialog, ttk

from . import dialogs
from ..core.errors import LayoutError
from ..core.units import dbu_to_mm
from .commands import CommandError, _parse_point


class LibraryDialogsMixin:
    def save_as_library(self):
        """Save the current design to a GDSII file under a chosen library
        name — the way to publish it as a reusable reference library
        that can later be picked up with Attach Library."""
        name = simpledialog.askstring(
            "Save As Library", "Library name:", initialvalue=self.session.library.name, parent=self
        )
        if not name:
            return
        path = filedialog.asksaveasfilename(defaultextension=".gds", filetypes=[("GDSII", "*.gds")])
        if not path:
            return
        try:
            self.session.save(path, library_name=name)
        except LayoutError as e:
            dialogs.show_error("Save As Library failed", str(e), parent=self)
            return
        self.status.set(f"saved library {name!r} to {path}")

    def show_merge_gds_dialog(self):
        """File > Merge GDS Libraries...: combine two GDSII files into a
        third, cell for cell -- a plain file-to-file operation, doesn't
        touch the currently open design. Prompts for the two source
        files and where to write the merged result, then hands off to
        Session.merge_gds(); same operation as the command bar's
        MERGE <lib1.gds> <lib2.gds> <lib3.gds> command."""
        path_a = filedialog.askopenfilename(
            title="Merge GDS Libraries: first file",
            filetypes=[("GDSII", "*.gds"), ("All files", "*.*")])
        if not path_a:
            return
        path_b = filedialog.askopenfilename(
            title="Merge GDS Libraries: second file",
            filetypes=[("GDSII", "*.gds"), ("All files", "*.*")])
        if not path_b:
            return
        out_path = filedialog.asksaveasfilename(
            title="Merge GDS Libraries: save merged file as",
            defaultextension=".gds", filetypes=[("GDSII", "*.gds")])
        if not out_path:
            return
        try:
            result = self.session.merge_gds(path_a, path_b, out_path)
        except LayoutError as e:
            dialogs.show_error("Merge GDS Libraries failed", str(e), parent=self)
            return
        except OSError as e:
            dialogs.show_error("Merge GDS Libraries failed", str(e), parent=self)
            return
        msg = f"merged into {out_path} ({result['cells']} cells)"
        if result["renamed"]:
            n = len(result["renamed"])
            msg += f", {n} cell name(s) renamed to avoid collisions"
        self.status.set(msg)
        dialogs.show_info("Merge GDS Libraries", msg, parent=self)

    def attach_library(self):
        path = filedialog.askopenfilename(filetypes=[("GDSII", "*.gds"), ("All files", "*.*")])
        if not path:
            return
        try:
            lib = self.session.attach_library(path)
        except LayoutError as e:
            dialogs.show_error("Attach Library failed", str(e), parent=self)
            return
        idx = len(self.session.attached_libraries) - 1
        kept = self._keep_attachments()
        self.status.set(f"attached library {idx}: {lib.name!r} ({len(lib.cells)} cells)"
                        + ("; kept with the design" if kept else ""))
        self._refresh_cell_lists()

    def _keep_attachments(self):
        """Record the attached libraries in the open design's sidecar at
        once, so re-opening the design (or quitting) before the next save
        does not lose an attachment. Returns True when written."""
        path = getattr(self, "current_path", None)
        if not path:
            return False
        try:
            from ..io.sidecar import read_sidecar, write_sidecar
            from ..io.techfile import apply_techfile
            from ..core.library import Library
            # keep the saved technology as saved: only the attachments change
            old = read_sidecar(path)
            tech_lib = self.session.library
            if old and isinstance(old.get("technology"), dict):
                tech_lib = Library(self.session.library.name)
                apply_techfile(tech_lib, old["technology"])
            write_sidecar(tech_lib, list(self.session.attached_libraries),
                          list(self.session._attached_paths), path)
            return True
        except Exception:
            return False

    def show_attached_libraries(self):
        """Setup > Attached Libraries...: view every reference library
        currently attached (Session.attached_libraries -- up to
        MAX_ATTACHED_LIBRARIES, 2) and detach one to free its slot for
        Attach Library... . Detaching only removes it from the search/
        attach list -- any cells already copied into this design via
        Find & Place Cell (Session.import_cell_closure(), a real,
        independent deep copy) are completely unaffected, so detaching
        never touches anything already placed."""
        win = tk.Toplevel(self)
        win.title("Attached Libraries")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(
            win,
            text=f"Up to {self.session.MAX_ATTACHED_LIBRARIES} reference libraries may be attached at once.",
            foreground="#8b949e",
        ).pack(anchor="w", padx=10, pady=(10, 4))

        columns = ("index", "name", "cells", "path")
        tree = ttk.Treeview(win, columns=columns, show="headings", height=4, selectmode="browse")
        for col, label, width in (
            ("index", "#", 30), ("name", "Name", 140), ("cells", "Cells", 60), ("path", "Path", 380),
        ):
            tree.heading(col, text=label)
            tree.column(col, width=width, anchor="w" if col in ("name", "path") else "center")
        tree.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        def refresh():
            for row in tree.get_children():
                tree.delete(row)
            libs = self.session.list_attached_libraries()
            for l in libs:
                tree.insert("", "end", iid=str(l["index"]),
                             values=(l["index"], l["name"], l["cells"], l["path"]))
            empty_var.set("No libraries attached." if not libs else "")
            detach_btn.config(state="disabled" if not libs else "normal")

        empty_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=empty_var, foreground="#8b949e").pack(anchor="w", padx=10)

        def detach_selected():
            sel = tree.selection()
            if not sel:
                return
            idx = int(sel[0])
            libs = self.session.list_attached_libraries()
            name = next((l["name"] for l in libs if l["index"] == idx), "?")
            if not dialogs.ask_yes_no(
                "Detach Library",
                f"Detach {name!r}? Anything already placed from it stays in the "
                "design untouched -- this only removes it from the attach list.",
                parent=win,
            ):
                return
            self.session.detach_library(idx)
            self._keep_attachments()
            self._refresh_cell_lists()
            self.status.set(f"detached library {idx}: {name!r}")
            refresh()

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=10, pady=(0, 10))
        detach_btn = ttk.Button(btn_row, text="Detach", command=detach_selected)
        detach_btn.pack(side="left")
        ttk.Button(btn_row, text="Close", command=win.destroy).pack(side="right")

        refresh()
        win.bind("<Escape>", lambda e: win.destroy())

    def show_library_cells(self):
        """Setup > Library Cells...: the Library Cells & Place dialog
        (see _library_cells_dialog)."""
        self._library_cells_dialog()

    def find_and_place_cell(self):
        """Create > Find && Place Cell...: the same Library Cells & Place
        dialog as Setup > Library Cells... (see _library_cells_dialog)."""
        self._library_cells_dialog()

    def _library_cells_dialog(self):
        """One dialog to browse and place cells: every cell (Sref target)
        in the design and in each attached library, with its size, pin
        count and created / last-modified date (Session.cell_stamps --
        the stamps live in each cell's GDSII BGNSTR record, so a part
        placed from a library keeps its library dates until it is edited
        here). Filter by name, show one library or all, click a heading
        to sort. Pick a row and Place (or double-click / Enter) to put an
        instance in the current cell at the given point, rotation, mirror
        and label; the dialog stays open so several parts can be placed
        one after another."""
        old = getattr(self, "_library_cells_win", None)
        if old is not None and old.winfo_exists():
            old.deiconify()
            old.lift()
            old._refresh()
            return

        title = "Library Cells & Place"
        win = tk.Toplevel(self)
        self._library_cells_win = win
        win.title(title)
        win.transient(self)
        win.geometry("900x540")

        # --- filter row ---------------------------------------------------
        top = ttk.Frame(win)
        top.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Label(top, text="Filter:").pack(side="left")
        filt_var = tk.StringVar()
        filt = ttk.Entry(top, textvariable=filt_var, width=24)
        filt.pack(side="left", padx=4)
        ttk.Label(top, text="Library:").pack(side="left", padx=(12, 0))
        lib_var = tk.StringVar(value="All")
        lib_box = ttk.Combobox(top, textvariable=lib_var, state="readonly", width=30)
        lib_box.pack(side="left", padx=4)
        count_var = tk.StringVar()
        ttk.Label(top, textvariable=count_var, foreground="#8b949e").pack(side="right")

        # --- cell table ---------------------------------------------------
        cols = ("library", "cell", "size", "pins", "created", "modified")
        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=8)
        tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        vbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vbar.set)
        for col, label, width, anchor in (
                ("library", "Library", 170, "w"), ("cell", "Cell", 200, "w"),
                ("size", "Size (mm)", 110, "e"), ("pins", "Pins", 50, "e"),
                ("created", "Created", 140, "w"), ("modified", "Modified", 140, "w")):
            tree.heading(col, text=label, command=lambda c=col: sort_by(c))
            tree.column(col, width=width, anchor=anchor)
        tree.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        state = {"key": "cell", "reverse": False, "rows": {}, "libs": {}}

        def fmt(iso):
            return iso.replace("T", " ") if iso else "not saved yet"

        def lib_of(src):
            if src == "current":
                return self.session.library
            return self.session.attached_libraries[src]

        def detail(src, name):
            """(width, height) in mm and the pin count of one cell."""
            try:
                lib = lib_of(src)
                cell = lib.cells[name]
                box = lib.cell_bbox(name)
            except Exception:
                return None, None, 0
            pins = {getattr(sh, "pin", "") for sh in cell.shapes} | \
                   {getattr(r, "pin", "") for r in cell.srefs}
            pins.discard("")
            if box is None:
                return None, None, len(pins)
            return dbu_to_mm(box[2] - box[0]), dbu_to_mm(box[3] - box[1]), len(pins)

        def lib_choices():
            names = {"current": f"Design ({self.session.library.name})"}
            for l in self.session.list_attached_libraries():
                names[l["index"]] = f"[{l['index']}] {l['name']}"
            state["libs"] = names
            lib_box["values"] = ["All"] + list(names.values())
            if lib_var.get() not in lib_box["values"]:
                lib_var.set("All")

        def refresh(*_):
            lib_choices()
            q = filt_var.get().strip().lower()
            want = lib_var.get()
            rows = []
            for r in self.session.cell_stamps():
                if q not in r["name"].lower():
                    continue
                lib_label = state["libs"].get(r["source"], str(r["source"]))
                if want != "All" and lib_label != want:
                    continue
                w, h, pins = detail(r["source"], r["name"])
                rows.append({**r, "lib_label": lib_label, "w": w, "h": h, "pins": pins})
            key = state["key"]
            getter = {"library": lambda r: r["lib_label"], "cell": lambda r: r["name"].lower(),
                      "size": lambda r: (r["w"] or 0) * (r["h"] or 0), "pins": lambda r: r["pins"],
                      "created": lambda r: r["created"] or "",
                      "modified": lambda r: r["modified"] or ""}[key]
            rows.sort(key=getter, reverse=state["reverse"])
            keep = tree.selection()
            keep = state["rows"].get(keep[0]) if keep else None
            tree.delete(*tree.get_children())
            state["rows"] = {}
            for r in rows:
                size = f"{r['w']:.2f} x {r['h']:.2f}" if r["w"] is not None else "empty"
                iid = tree.insert("", "end", values=(r["lib_label"], r["name"], size, r["pins"] or "",
                                                     fmt(r["created"]), fmt(r["modified"])))
                state["rows"][iid] = (r["source"], r["name"])
                if keep == (r["source"], r["name"]):
                    tree.selection_set(iid)
                    tree.see(iid)
            count_var.set(f"{len(rows)} cell(s)")

        def sort_by(col):
            state["reverse"] = (not state["reverse"]) if state["key"] == col \
                else col in ("created", "modified", "size", "pins")
            state["key"] = col
            refresh()

        win._refresh = refresh
        filt_var.trace_add("write", refresh)
        lib_box.bind("<<ComboboxSelected>>", refresh)

        # --- place row ------------------------------------------------------
        place = ttk.LabelFrame(win, text="Place in the current cell")
        place.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(place, text="At x,y:").pack(side="left", padx=(6, 2), pady=6)
        coord_var = tk.StringVar(value="0,0")
        ttk.Entry(place, textvariable=coord_var, width=14).pack(side="left")
        ttk.Label(place, text="Rotation:").pack(side="left", padx=(10, 2))
        rot_var = tk.StringVar(value="0")
        ttk.Combobox(place, textvariable=rot_var, values=("0", "90", "180", "270"),
                     width=5).pack(side="left")
        mirror_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(place, text="Mirror", variable=mirror_var).pack(side="left", padx=(10, 0))
        ttk.Label(place, text="Label:").pack(side="left", padx=(10, 2))
        label_var = tk.StringVar()
        ttk.Entry(place, textvariable=label_var, width=10).pack(side="left")
        place_btn = ttk.Button(place, text="Place")
        place_btn.pack(side="right", padx=6)

        def do_place(event=None):
            sel = tree.selection()
            if not sel:
                dialogs.show_error(title, "Select a cell from the list first.", parent=win)
                return
            src, name = state["rows"][sel[0]]
            try:
                origin = _parse_point(coord_var.get())
                rotation = float(rot_var.get() or 0)
            except (CommandError, ValueError) as e:
                dialogs.show_error(title, str(e), parent=win)
                return
            try:
                self.session.place_cell(name, origin, source=src, rotation=rotation,
                                        x_reflection=mirror_var.get(),
                                        label=label_var.get().strip())
            except LayoutError as e:
                dialogs.show_error(title, str(e), parent=win)
                return
            # Placing a cell should make everything in it visible right
            # away, not leave it off-screen until the user separately hits
            # Fit -- so refresh the panels, then fit the view.
            self.refresh_all()
            self.canvas.fit_view()
            where = "" if src == "current" else f" from {state['libs'].get(src, src)}"
            self.status.set(f"Placed {name}{where} at {coord_var.get()}")
            label_var.set("")
            refresh()     # a library part is now also a design cell

        place_btn.configure(command=do_place)
        tree.bind("<Double-Button-1>", do_place)
        tree.bind("<Return>", do_place)

        ttk.Label(win, foreground="#8b949e",
                  text="Created/modified are stored with each cell in the GDS file; "
                       "modified updates when a changed cell is saved. Placing a library "
                       "part copies it into the design.").pack(anchor="w", padx=8, pady=(4, 0))
        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=8, pady=8)
        ttk.Button(btns, text="Refresh", command=refresh).pack(side="left")
        ttk.Button(btns, text="Close", command=win.destroy).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())
        refresh()
        filt.focus_set()
