"""Component Library Manager -- File > Component Library Manager...

Lists every placed component cell in the current cell's hierarchy
alongside the STEP file it's tied to (a manual override, or whatever
hlayout.io.component_match auto-matched), and lets you set or clear a
manual override directly. Before this window, there was no way at all
to set one: hlayout.io.techfile.save_component_models() existed but had
no caller anywhere in the codebase -- the only visibility into cell-to-
3D-package assignment was the read-only auto-match preview shown once,
in passing, during File > Export to FreeCAD.

Deliberately simple, per explicit direction -- just the cell name and
the STEP file name side by side, not a rendered preview of either --
plus two small additions:
  - a Source column (manual/auto/none) so it's never ambiguous whether
    a row is a deliberate pin or a guess;
  - a Rotation field (0/90/180/270), since the footprint matcher is
    deliberately orientation-independent when DETECTING a package (see
    hlayout.io.component_match's module docstring), so an auto-matched
    substitution can come out rotated 90 degrees from the real
    footprint's actual lead direction -- this is the fallback fix once
    you've looked at the result in FreeCAD and it's rotated wrong."""

import os
import tkinter as tk
from tkinter import filedialog, ttk

from . import dialogs
from .fonts import section_header_font
from ..core.errors import LayoutError
from ..io.techfile import load_component_models, save_component_models

ROTATION_CHOICES = ("0", "90", "180", "270")


class SetModelDialog(tk.Toplevel):
    """Modal Set STEP File... form for one cell: the STEP path plus its
    rotation correction, matching write_freecad_macro's own degree
    convention (added on TOP of the placement's own rotation)."""

    def __init__(self, parent, cell_name, initial_path="", initial_rotation=0.0,
                 initial_dir=None):
        super().__init__(parent)
        self.title(f"Set STEP File -- {cell_name}")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.initial_dir = initial_dir
        self.result = None
        self._build(cell_name, initial_path, initial_rotation)
        self.bind("<Escape>", lambda e: self.destroy())

    def _build(self, cell_name, initial_path, initial_rotation):
        form = ttk.Frame(self, padding=16)
        form.pack(fill="both", expand=True)

        ttk.Label(form, text="STEP file:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.path_var = tk.StringVar(value=initial_path)
        ttk.Entry(form, textvariable=self.path_var, width=40).grid(
            row=0, column=1, sticky="w", padx=4, pady=4)
        ttk.Button(form, text="Browse...", command=self._browse).grid(
            row=0, column=2, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Rotation (deg):").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        # Nearest of the 4 choices -- an auto-suggested value is always
        # exactly 0 or 90 (see _gullwing_rotation_correction()), but
        # guard against anything odd a hand-edited tech file might hold.
        nearest = min(ROTATION_CHOICES, key=lambda c: abs(float(c) - initial_rotation) % 360)
        self.rotation_var = tk.StringVar(value=nearest)
        ttk.Combobox(form, textvariable=self.rotation_var, values=ROTATION_CHOICES,
                     state="readonly", width=8).grid(row=1, column=1, sticky="w", padx=4, pady=4)

        self.error_var = tk.StringVar(value="")
        ttk.Label(form, textvariable=self.error_var, foreground="red", wraplength=340,
                  justify="left").grid(row=2, column=0, columnspan=3, sticky="w", padx=4, pady=(6, 0))

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=(0, 12))
        ttk.Button(btn_row, text="OK", command=self._accept).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left", padx=6)

    def _browse(self):
        path = filedialog.askopenfilename(
            title="STEP File", filetypes=[("STEP files", "*.step;*.stp"), ("All files", "*.*")],
            initialdir=self.initial_dir, parent=self,
        )
        if path:
            self.path_var.set(path)

    def _accept(self):
        path = self.path_var.get().strip()
        if not path:
            self.error_var.set("Pick a STEP file.")
            return
        self.result = {"mode": "model", "model_path": path,
                        "rotation_deg": float(self.rotation_var.get())}
        self.destroy()


class ComponentLibraryWindow(tk.Toplevel):
    """`library` is the current design's Library; `app` is the main App
    (used to run auto-matching against the live session and to resolve
    the STEP library folder -- same as Export to FreeCAD's own
    resolution: the remembered component_library_dir(), falling back to
    this design's own <home>/freecad)."""

    def __init__(self, master, library, app=None):
        super().__init__(master)
        self.title("Component Library Manager")
        self.geometry("820x420")
        self.library = library
        self.app = app
        self._matched_by_cell = {}
        self._build()
        self._refresh()
        self.bind("<Escape>", lambda e: self.destroy())

    def _build(self):
        toolbar = ttk.Frame(self, padding=6)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Set STEP File...", command=self._set_step_file).pack(
            side="left", padx=2)
        ttk.Button(toolbar, text="Clear Override", command=self._clear_override).pack(
            side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="Refresh", command=self._refresh).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Close", command=self.destroy).pack(side="right", padx=2)
        ttk.Button(toolbar, text="About", command=self._about).pack(side="right", padx=2)

        section_font = section_header_font(self)
        ttk.Label(self, text="COMPONENTS", foreground="#3fb950",
                  font=section_font).pack(anchor="w", padx=10, pady=(6, 0))

        cols = ("cell", "step", "rotation", "source")
        self.table = ttk.Treeview(self, columns=cols, show="headings", height=16)
        for col, heading, width in [
            ("cell", "Cell", 200), ("step", "STEP File", 340),
            ("rotation", "Rotation", 80), ("source", "Source", 100),
        ]:
            self.table.heading(col, text=heading)
            self.table.column(col, width=width, anchor="w")
        self.table.pack(fill="both", expand=True, padx=10, pady=4)
        self.table.bind("<Double-1>", lambda e: self._set_step_file())

        status = ttk.Frame(self, padding=(6, 3))
        status.pack(fill="x")
        self.status_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.status_var, foreground="#8b949e").pack(side="left")

    # -- helpers --------------------------------------------------------------
    def _step_dir(self):
        if self.app is None:
            return None
        return self.app.component_library_dir(ask_if_unset=False) or \
            self.app.default_export_dir("freecad")

    def _selected_cell(self):
        sel = self.table.selection()
        return sel[0] if sel else None

    # -- list ------------------------------------------------------------------
    def _refresh(self):
        self.table.delete(*self.table.get_children())
        self._matched_by_cell = {}
        if self.app is None:
            self.status_var.set("This window isn't attached to a layout window.")
            return
        cell_name = self.app.session.current_cell_name
        step_dir = self._step_dir()
        if cell_name is None:
            self.status_var.set("No current cell.")
            return
        if step_dir is None:
            self.status_var.set("Save the design (or set a component library folder) first.")
            return

        try:
            report = self.app.session.match_freecad_components(
                cell_name=cell_name, step_dir=step_dir)
        except LayoutError as e:
            self.status_var.set(f"Could not match components: {e}")
            return

        manual = load_component_models(self.library)
        self._matched_by_cell = {m.cell_name: m for m in report.matches}
        names = sorted(set(manual) | set(self._matched_by_cell)
                       | {u.cell_name for u in report.unmatched})

        for cn in names:
            entry = manual.get(cn)
            if entry and entry.get("mode") == "model" and entry.get("model_path"):
                step_desc = os.path.basename(entry["model_path"])
                rotation = float(entry.get("rotation_deg", 0.0))
                source = "manual"
            elif cn in self._matched_by_cell:
                match_entry = self._matched_by_cell[cn].entry
                step_desc = os.path.basename(match_entry["model_path"])
                rotation = float(match_entry.get("rotation_deg", 0.0))
                source = "auto"
            else:
                step_desc = "(none -- flat box)"
                rotation = 0.0
                source = "none"
            self.table.insert("", "end", iid=cn, values=(
                cn, step_desc, f"{rotation:g}", source))

        self.status_var.set(f"{len(names)} component cell(s) -- step folder: {step_dir}")

    # -- editing -----------------------------------------------------------------
    def _set_step_file(self):
        cn = self._selected_cell()
        if cn is None:
            dialogs.show_info("Set STEP File", "Select a cell first.", parent=self)
            return
        manual = load_component_models(self.library)
        existing = manual.get(cn, {})
        initial_path = existing.get("model_path", "")
        initial_rotation = float(existing.get("rotation_deg", 0.0))
        if not initial_path and cn in self._matched_by_cell:
            match_entry = self._matched_by_cell[cn].entry
            initial_path = match_entry.get("model_path", "")
            initial_rotation = float(match_entry.get("rotation_deg", 0.0))

        dlg = SetModelDialog(self, cn, initial_path=initial_path,
                              initial_rotation=initial_rotation, initial_dir=self._step_dir())
        self.wait_window(dlg)
        if dlg.result is None:
            return
        # keep a centred LCC/PLCC model's footprint-centre offset when the
        # path is pinned by hand (the dialog only edits path + rotation)
        origin = existing.get("origin_um")
        if origin is None and cn in self._matched_by_cell:
            origin = self._matched_by_cell[cn].entry.get("origin_um")
        if origin:
            dlg.result["origin_um"] = origin
        manual[cn] = dlg.result
        save_component_models(self.library, manual)
        self._refresh()
        self.status_var.set(f"{cn} -> {os.path.basename(dlg.result['model_path'])} (manual)")

    def _clear_override(self):
        cn = self._selected_cell()
        if cn is None:
            dialogs.show_info("Clear Override", "Select a cell first.", parent=self)
            return
        manual = load_component_models(self.library)
        if cn in manual:
            del manual[cn]
            save_component_models(self.library, manual)
        self._refresh()
        self.status_var.set(f"{cn}: manual override cleared")

    def _about(self):
        dialogs.show_info(
            "About",
            "Component Library Manager\n\n"
            "Lists every placed component cell in the current cell's "
            "hierarchy next to the STEP file it's tied to -- a manual "
            "override (Set STEP File...) always wins over whatever "
            "File > Export to FreeCAD's auto-matcher would otherwise "
            "pick, and Clear Override reverts a cell back to auto-"
            "matching (or a flat extruded box, if nothing matches).\n\n"
            "Rotation is an extra correction on top of the part's own "
            "placement rotation -- the footprint matcher recognizes a "
            "package regardless of which way its leads are drawn, so an "
            "auto-match can come out 90 degrees off; fix it here once "
            "you've checked the result in FreeCAD.\n\n"
            "Source: 'manual' (you set it here), 'auto' (matched by "
            "name or footprint), or 'none' (falls back to a flat box).",
            parent=self,
        )


def open_component_library_window(app):
    """File > Component Library Manager... . Reuses a single window per
    App instance -- calling this again while one is already open just
    brings it to the front, instead of stacking up duplicates."""
    win = getattr(app, "_component_library_window", None)
    if win is not None and win.winfo_exists():
        win.lift()
        win.focus_set()
        return win
    win = ComponentLibraryWindow(app, app.session.library, app=app)
    app._component_library_window = win
    return win
