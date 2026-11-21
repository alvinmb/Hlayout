# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Exports and output dialogs: FreeCAD macro and the 3D component library,
pick-and-place, design archive, export folders, layer remap, mask ops,
top view / assembly drawing, netlist read / extract.

Part of hlayout.gui.app.App (a mixin): these methods are App's own --
call them as always."""

import os
import tkinter as tk
import zipfile
from tkinter import filedialog, ttk

from . import dialogs
from ..core.errors import LayoutError
from ..core.maskops import MASK_OPS
from ..core.units import dbu_to_mm, mm_to_dbu
from ..io.sidecar import sidecar_path
from .assembly_window import open_assembly_window
from .netlist_window import open_netlist_window
from .topview_window import open_topview_window


def _unique_arcname(used: set, name: str) -> str:
    """A zip member name guaranteed distinct from everything already in
    `used` -- e.g. two attached libraries with the same filename in
    different folders -- by appending "_2", "_3", ... before the
    extension as needed. Records the chosen name in `used` before
    returning it. Used by App.archive_design()."""
    if name not in used:
        used.add(name)
        return name
    stem, ext = os.path.splitext(name)
    i = 2
    while f"{stem}_{i}{ext}" in used:
        i += 1
    new_name = f"{stem}_{i}{ext}"
    used.add(new_name)
    return new_name


class ExportsMixin:
    def archive_design(self):
        """File > Archive Design...: bundle every file that makes up the
        open design into one .zip -- the .gds itself, its .hlaux sidecar
        (technology + tech_params) if one exists, and every attached
        library .gds that's still reachable at its recorded path (an
        embedded copy already lives in the sidecar, so this is about
        also keeping the standalone originals together, not needed to
        reopen the design). Skips any attached-library path that isn't a
        real file on disk (e.g. one restored from the sidecar with no
        live source, or since moved/deleted) rather than failing the
        whole archive over it.

        The design must already be saved -- current_path is what
        "this design"'s files are defined relative to -- so this refuses
        with a message to save first if it's unset."""
        if not self.current_path:
            dialogs.show_warning(
                "Archive Design", "Save the design (File > Save GDS...) before archiving it.",
                parent=self)
            return
        gds_path = self.current_path
        if not os.path.exists(gds_path):
            dialogs.show_error(
                "Archive Design failed", f"{gds_path} no longer exists on disk.", parent=self)
            return

        files = [gds_path]
        sc = sidecar_path(gds_path)
        if os.path.exists(sc):
            files.append(sc)
        for p in self.session._attached_paths:
            if p and os.path.isfile(p) and p not in files:
                files.append(p)

        default_name = os.path.splitext(os.path.basename(gds_path))[0] + "_archive.zip"
        zip_path = filedialog.asksaveasfilename(
            title="Archive Design", defaultextension=".zip", filetypes=[("Zip archive", "*.zip")],
            initialdir=os.path.dirname(gds_path), initialfile=default_name)
        if not zip_path:
            return

        used_names = set()
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in files:
                    zf.write(f, arcname=_unique_arcname(used_names, os.path.basename(f)))
        except OSError as e:
            dialogs.show_error("Archive Design failed", str(e), parent=self)
            return
        self.status.set(
            f"archived {len(files)} file(s) ({', '.join(sorted(used_names))}) -> {zip_path}")

    def default_export_dir(self, subfolder: str):
        """<home>/<subfolder> (created if it doesn't exist yet), where
        <home> is the folder the currently open/saved GDS file lives in
        -- e.g. default_export_dir("gerber") for a design opened from
        C:/jobs/board1/board1.gds is C:/jobs/board1/gerber. Returns
        None if the design has never been saved (current_path unset),
        since there's no "home" to key off yet -- callers should fall
        back to asking the user in that case. Used to give Gerber/DXF/
        FreeCAD export their own conventional subfolder next to the
        design instead of wherever the OS file dialog last happened to
        be, or a folder picker prompt every time."""
        if not self.current_path:
            return None
        out_dir = os.path.join(os.path.dirname(self.current_path), subfolder)
        os.makedirs(out_dir, exist_ok=True)
        return out_dir

    def component_library_dir(self, ask_if_unset: bool = True):
        """The FreeCAD component STEP library folder -- a single
        location REMEMBERED across every design (see
        hlayout.io.app_settings), unlike default_export_dir(), since a
        parts library is meant to be built once and reused everywhere,
        not regenerated next to whichever design happens to be open.

        If none is configured yet: with ask_if_unset (the default),
        prompts to choose/create one and remembers the choice for
        every design from now on; otherwise just returns None so a
        caller can skip straight to "nothing configured" behavior
        without a forced prompt (e.g. during a plain export)."""
        from ..io.app_settings import get_component_library_dir, set_component_library_dir
        existing = get_component_library_dir()
        if existing:
            os.makedirs(existing, exist_ok=True)
            return existing
        if not ask_if_unset:
            return None
        chosen = filedialog.askdirectory(
            title="Choose a Folder for the 3D Component Library "
                  "(remembered for every design from now on)")
        if not chosen:
            return None
        os.makedirs(chosen, exist_ok=True)
        set_component_library_dir(chosen)
        return chosen

    def export_pick_and_place(self):
        """File > Export Pick and Place...: write the centroid file of the
        current cell -- every placed part with a ref-des, its centroid
        (pad-pattern centre; body centre for a bare die) in mm from the
        substrate's lower-left corner, and its placement angle. .pos
        writes KiCad footprint positions, anything else CSV. Printed
        thick-film resistors are left out (they're printed, not placed)."""
        name = self.session.current_cell_name
        if not name:
            dialogs.show_info("Export Pick and Place", "Open or create a cell first.", parent=self)
            return
        out_dir = self.default_export_dir("assembly")
        path = filedialog.asksaveasfilename(
            parent=self, title="Export Pick and Place",
            initialdir=out_dir or None,
            initialfile=f"{self.session.library.name}_{name}_pnp.csv",
            defaultextension=".csv",
            filetypes=[("Pick and place CSV", "*.csv"), ("KiCad footprint positions", "*.pos"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            t = self.session.export_pick_and_place(
                path, design_name=os.path.basename(self.current_path) if self.current_path else None)
        except (LayoutError, OSError, ValueError) as e:
            dialogs.show_error("Export Pick and Place", str(e), parent=self)
            return
        ox, oy = t["origin"]
        lines = [f"Wrote {len(t['rows'])} part(s) to {path} ({t['format']}), "
                 f"created {t['created']}.",
                 f"Origin: {t['origin_kind']} ({dbu_to_mm(ox):g}, {dbu_to_mm(oy):g}) mm",
                 "", f"{'Ref':<8}{'Footprint':<22}{'Kind':<10}{'X mm':>10}{'Y mm':>10}{'Rot':>8}"]
        for r in t["rows"]:
            lines.append(f"{r['designator']:<8}{r['footprint'][:21]:<22}{r['kind']:<10}"
                         f"{r['x_mm']:>10.3f}{r['y_mm']:>10.3f}{r['rotation']:>8.1f}"
                         f"{'  mirrored' if r['mirrored'] else ''}")
        dialogs.show_scrollable_text("Export Pick and Place", lines, parent=self, ok_label="OK")
        self.status.set(f"Pick and place: {len(t['rows'])} part(s) written to {path}")

    def export_freecad_macro(self):
        """File > Export to FreeCAD (3D Macro)...: write a .FCMacro that
        rebuilds the current cell as a 3D solid stack when run inside
        FreeCAD. Layer thicknesses / component 3D come from the design's
        tech-params, else the role heuristic. Needs shapely.

        Writes straight into <home>/freecad (see default_export_dir())
        with no prompt, where <home> is the open design's own folder;
        falls back to asking for a folder only when the design has
        never been saved yet (no home to key off).

        Before writing, placed component cells with no manual
        component_models entry are auto-matched against whatever STEP
        library already exists in the remembered component library
        folder (component_library_dir() -- falling back to this
        design's own <home>/freecad, for STEP files built there before
        that folder existed) -- see
        Session.match_freecad_components()/hlayout.io.component_match:
        a passive cell by its name's size code, a SOT/PKG20-style part
        by its pad footprint. If there's anything to report, a preview
        shows it first -- purely informational, it never blocks the
        export: a component nothing matched (or with no STEP file
        built yet) just renders as today's flat extruded box, same as
        it always has, so an incomplete match never means no output at
        all. A manual override always wins over an auto-match."""
        name = self.session.current_cell_name or ""
        out_dir = self.default_export_dir("freecad")
        if out_dir is None:
            out_dir = filedialog.askdirectory(title="Export to FreeCAD (3D Macro) -- Output Folder")
            if not out_dir:
                return
        filename = f"{self.session.library.name}_{name}.FCMacro" if name else "design.FCMacro"
        path = os.path.join(out_dir, filename)
        step_dir = self.component_library_dir(ask_if_unset=False) or out_dir

        self.status.set(f"Export to FreeCAD: matching components against {step_dir}...")
        self.update_idletasks()
        try:
            report = self.session.match_freecad_components(step_dir=step_dir)
        except LayoutError as e:
            dialogs.show_error("Export to FreeCAD", str(e), parent=self)
            self.status.set(f"Export to FreeCAD failed while matching components: {e}")
            return
        except Exception as e:
            # Never fail silently: a Tkinter button callback that raises
            # anything other than what's explicitly caught above prints a
            # traceback to the console (if anyone's watching one) and the
            # GUI itself does nothing else -- no dialog, no status change
            # -- which looks exactly like "the export just didn't happen"
            # with no way to tell why. Catch everything here as a last
            # resort so that can never occur again.
            import traceback
            traceback.print_exc()
            dialogs.show_error(
                "Export to FreeCAD",
                f"Unexpected error while matching components ({type(e).__name__}): {e}",
                parent=self)
            self.status.set(f"Export to FreeCAD failed while matching components: {e}")
            return

        if report.matches or report.unmatched:
            dialogs.show_scrollable_text(
                "Auto-Matched Components", report.summary_lines(), parent=self, ok_label="OK")

        self.status.set(f"Export to FreeCAD: writing {path}...")
        self.update_idletasks()
        try:
            written = self.session.export_freecad(path, component_models=report.component_models())
        except (LayoutError, OSError) as e:
            dialogs.show_error("Export to FreeCAD", str(e), parent=self)
            self.status.set(f"Export to FreeCAD failed while writing the macro: {e}")
            return
        except Exception as e:
            import traceback
            traceback.print_exc()
            dialogs.show_error(
                "Export to FreeCAD",
                f"Unexpected error while writing the macro ({type(e).__name__}): {e}",
                parent=self)
            self.status.set(f"Export to FreeCAD failed while writing the macro: {e}")
            return
        self.status.set(f"FreeCAD 3D macro written to {written} "
                        f"-- run it inside FreeCAD (Macro > Macros...)")
        self._show_freecad_output(
            "Export to FreeCAD",
            "The FreeCAD 3D macro was written to the file below. Run it inside "
            "FreeCAD (Macro > Macros... > Execute) to build the 3D model.",
            written)

    def build_component_library_macro(self):
        """File > Build 3D Component Library...: write a .FCMacro
        that, run inside FreeCAD, builds a small library of generic 3D
        component bodies (ceramic chip capacitors, a 20-pin metal
        package, the SOT-23/323/89/223/14/16/18 package family, and
        the LCC/PLCC 20-84 pin chip carriers)
        and exports each as its own STEP file -- meant to be referenced
        from a placed cell's component_models tech-param (Setup >
        Technology Properties...) so export_freecad() can substitute a
        real 3D body for that instance.

        Writes into component_library_dir() -- a single folder
        REMEMBERED across every design, not this design's own folder
        -- so the library only ever needs building once, in FreeCAD,
        no matter which design is open afterwards. The first time
        this runs (nothing remembered yet) it asks where to put the
        library and remembers the answer from then on; Setup > FreeCAD
        Component Library Folder... changes it later."""
        out_dir = self.component_library_dir()
        if out_dir is None:
            return
        self.status.set(f"Build 3D Component Library: writing to {out_dir}...")
        self.update_idletasks()
        try:
            written = self.session.generate_component_library(out_dir)
        except OSError as e:
            dialogs.show_error("Build Component Library", str(e), parent=self)
            self.status.set(f"Build Component Library failed: {e}")
            return
        except Exception as e:
            import traceback
            traceback.print_exc()
            dialogs.show_error(
                "Build Component Library",
                f"Unexpected error ({type(e).__name__}): {e}", parent=self)
            self.status.set(f"Build Component Library failed: {e}")
            return
        self.status.set(f"Component library macro written to {written} "
                        f"-- run it inside FreeCAD to generate the STEP files")
        self._show_freecad_output(
            "Build Component Library",
            "The component library macro was written to the file below. Run it "
            "inside FreeCAD to generate the STEP files in the same folder.",
            written)

    @staticmethod
    def _find_freecad_exe():
        """FreeCAD's GUI executable: on PATH, else the newest
        C:\\Program Files\\FreeCAD*\\bin\\freecad.exe. None if not found."""
        import glob
        import shutil
        exe = shutil.which("freecad") or shutil.which("FreeCAD")
        if exe:
            return exe
        found = sorted(glob.glob(os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                                              "FreeCAD*", "bin", "freecad.exe")))
        return found[-1] if found else None

    def _show_freecad_output(self, title, message, path):
        """Tell the user exactly where a FreeCAD macro went: full path,
        Open Folder, Copy Path, and Open in FreeCAD (runs the macro)
        when FreeCAD is installed."""
        extra = []
        exe = self._find_freecad_exe()
        if exe:
            def _run_in_freecad():
                import subprocess
                try:
                    subprocess.Popen([exe, os.path.abspath(path)])
                    self.status.set(f"Opening {os.path.basename(path)} in FreeCAD...")
                except OSError as e:
                    dialogs.show_error(title, f"Could not start FreeCAD: {e}", parent=self)
            extra.append(("Open in FreeCAD", _run_in_freecad))
        dialogs.show_file_written(title, message, path, parent=self, extra_buttons=extra)

    def change_component_library_dir(self):
        """Setup > 3D Component Library Folder...: view/change the
        remembered STEP library folder (component_library_dir()) --
        e.g. to point every design at a library already built
        elsewhere, without rebuilding it."""
        from ..io.app_settings import get_component_library_dir, set_component_library_dir
        current = get_component_library_dir()
        chosen = filedialog.askdirectory(
            title="3D Component Library Folder",
            initialdir=current or None)
        if not chosen:
            return
        set_component_library_dir(chosen)
        self.status.set(f"3D component library folder set to {chosen}")

    def show_remap_layers_dialog(self):
        """Setup > Remap Layers...: bulk-reassign shapes from one layer
        to another, across every cell in the library by default. The
        usual reason to do this is bringing an existing design's
        geometry into alignment with a layer stack just loaded from a
        technology file (Setup > Load Technology File...) -- e.g. this
        design's old layer 3 should really be layer 7 in the target
        process. Backed by Session.remap_layers(); same operation as
        the LAYER MOVE <from> <to> command."""
        win = tk.Toplevel(self)
        win.title("Remap Layers")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(
            win,
            text="Move every shape on one layer to another, across the whole "
                 "library. Add one row per layer you want to reassign, then "
                 "click Apply.",
            wraplength=420, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        rows_frame = ttk.Frame(win)
        rows_frame.pack(padx=12, fill="x")

        header = ttk.Frame(rows_frame)
        header.pack(fill="x")
        ttk.Label(header, text="From layer", width=18).pack(side="left")
        ttk.Label(header, text="To layer", width=18).pack(side="left")

        rows = []

        def layer_choices():
            return [f"{n} ({self.session.library.layers[n].name})"
                    for n in range(len(self.session.library.layers))]

        def add_row(from_default="", to_default=""):
            row = ttk.Frame(rows_frame)
            row.pack(fill="x", pady=2)
            from_var = tk.StringVar(value=from_default)
            to_var = tk.StringVar(value=to_default)
            choices = layer_choices()
            from_combo = ttk.Combobox(row, textvariable=from_var, width=18,
                                       state="readonly", values=choices)
            from_combo.pack(side="left", padx=(0, 4))
            to_combo = ttk.Combobox(row, textvariable=to_var, width=18,
                                     state="readonly", values=choices)
            to_combo.pack(side="left", padx=(0, 4))

            def remove_row():
                row.destroy()
                rows.remove(entry)

            remove_btn = ttk.Button(row, text="Remove", command=remove_row)
            remove_btn.pack(side="left")
            entry = (from_var, to_var)
            rows.append(entry)

        add_row()

        ttk.Button(win, text="+ Add Row", command=lambda: add_row()).pack(
            anchor="w", padx=12, pady=(4, 8))

        scope_row = ttk.Frame(win)
        scope_row.pack(fill="x", padx=12)
        ttk.Label(scope_row, text="Apply to:").pack(side="left")
        scope_var = tk.StringVar(value="Whole library (every cell)")
        cell_names = list(self.session.library.cells.keys())
        scope_combo = ttk.Combobox(
            scope_row, textvariable=scope_var, state="readonly", width=28,
            values=["Whole library (every cell)"] + cell_names,
        )
        scope_combo.pack(side="left", padx=6)

        error_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=error_var, foreground="red", wraplength=420,
                  justify="left").pack(anchor="w", padx=12)

        def apply_and_close():
            mapping = {}
            for from_var, to_var in rows:
                fv, tv = from_var.get().strip(), to_var.get().strip()
                if not fv or not tv:
                    continue
                from_num = int(fv.split(" ", 1)[0])
                to_num = int(tv.split(" ", 1)[0])
                mapping[from_num] = to_num
            if not mapping:
                error_var.set("Add at least one From/To layer pair.")
                return
            scope = scope_var.get()
            cell_names_arg = None if scope.startswith("Whole library") else [scope]
            try:
                counts = self.session.remap_layers(mapping, cell_names=cell_names_arg)
            except LayoutError as e:
                error_var.set(str(e))
                return
            self.refresh_all()
            moved = sum(counts.values())
            self.status.set(f"remap layers: moved {moved} shape(s)")
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(6, 12))
        ttk.Button(btn_row, text="Apply", command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        win.bind("<Escape>", lambda e: win.destroy())

    def show_mask_op_dialog(self):
        """Tools > Mask Operations...: layer-based OVERSIZE/UNDERSIZE/
        AND/OR/NOT/XOR -- similar to a GDSII CAM system's resize/boolean
        deck operators. Grows or shrinks a layer by a fixed amount, or
        combines two layers with a boolean intersection/union/
        difference/symmetric-difference, writing the result as new
        polygons on a target layer (which may be the same layer or a
        fresh one). Any Path on an input layer is converted to its
        stroked-outline polygon first. Backed by Session.mask_op(); same
        operation as the MASKOP command-bar command. Requires the
        shapely package -- Session.mask_op() raises a clear error if it
        isn't installed."""
        win = tk.Toplevel(self)
        win.title("Mask Operations")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(
            win,
            text="Grow/shrink a layer (OVERSIZE/UNDERSIZE), or combine two "
                 "layers (AND=intersection, OR=union, NOT=A minus B, "
                 "XOR=symmetric difference), writing the result as new "
                 "polygons on a target layer. Paths on the input layer(s) "
                 "are converted to their stroked outline first.",
            wraplength=420, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        def layer_choices():
            return [f"{n} ({self.session.library.layers[n].name})"
                    for n in range(len(self.session.library.layers))]

        form = ttk.Frame(win)
        form.pack(padx=12, fill="x")

        ttk.Label(form, text="Operation:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        op_var = tk.StringVar(value=MASK_OPS[0])
        op_combo = ttk.Combobox(form, textvariable=op_var, width=16, state="readonly",
                                 values=list(MASK_OPS))
        op_combo.grid(row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Layer A:").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        a_var = tk.StringVar(value="")
        a_combo = ttk.Combobox(form, textvariable=a_var, width=22, state="readonly",
                                values=layer_choices())
        a_combo.grid(row=1, column=1, sticky="w", padx=4, pady=4)

        b_label = ttk.Label(form, text="Layer B:")
        b_label.grid(row=2, column=0, sticky="e", padx=4, pady=4)
        b_var = tk.StringVar(value="")
        b_combo = ttk.Combobox(form, textvariable=b_var, width=22, state="readonly",
                                values=layer_choices())
        b_combo.grid(row=2, column=1, sticky="w", padx=4, pady=4)

        amount_label = ttk.Label(form, text="Amount (mm):")
        amount_label.grid(row=3, column=0, sticky="e", padx=4, pady=4)
        amount_var = tk.DoubleVar(value=0.0)
        amount_spin = ttk.Spinbox(form, from_=0.0, to=1e6, increment=0.001, textvariable=amount_var, width=14)
        amount_spin.grid(row=3, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Result layer:").grid(row=4, column=0, sticky="e", padx=4, pady=4)
        result_var = tk.StringVar(value="")
        result_combo = ttk.Combobox(form, textvariable=result_var, width=22, state="readonly",
                                     values=layer_choices())
        result_combo.grid(row=4, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Apply to:").grid(row=5, column=0, sticky="e", padx=4, pady=4)
        scope_var = tk.StringVar(value="Current cell")
        cell_names = list(self.session.library.cells.keys())
        scope_combo = ttk.Combobox(form, textvariable=scope_var, state="readonly", width=22,
                                    values=["Current cell"] + cell_names)
        scope_combo.grid(row=5, column=1, sticky="w", padx=4, pady=4)

        # On by default -- most real content in a design ends up placed
        # via Sref rather than drawn straight into the cell being
        # operated on (every resistor the Resistor Designer places is
        # its own device cell + a single Sref), so a mask op that only
        # looked at the operated-on cell's own shapes would silently
        # find nothing to work with for a design built that way.
        flatten_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            form, text="Include placed cells (Sref) when gathering input geometry",
            variable=flatten_var,
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=4, pady=(0, 4))

        ttk.Label(form, text="Simplify (mm, 0=off):").grid(
            row=7, column=0, sticky="e", padx=4, pady=4)
        simplify_var = tk.DoubleVar(value=0.0)
        ttk.Spinbox(form, from_=0.0, to=1e6, increment=0.001, textvariable=simplify_var, width=14).grid(
            row=7, column=1, sticky="w", padx=4, pady=4)

        BOOLEAN_OPS = ("AND", "OR", "NOT", "XOR")

        def sync_fields(event=None):
            op = op_var.get()
            is_boolean = op in BOOLEAN_OPS
            b_combo.configure(state="readonly" if is_boolean else "disabled")
            amount_spin.configure(state="disabled" if is_boolean else "normal")

        op_combo.bind("<<ComboboxSelected>>", sync_fields)
        sync_fields()

        error_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=error_var, foreground="red", wraplength=420,
                  justify="left").pack(anchor="w", padx=12)

        def layer_num(text):
            return int(text.split(" ", 1)[0]) if text.strip() else None

        def apply_and_close():
            op = op_var.get()
            a_layer = layer_num(a_var.get())
            result_layer = layer_num(result_var.get())
            if a_layer is None or result_layer is None:
                error_var.set("Pick Layer A and a Result layer.")
                return
            kwargs = {"flatten": flatten_var.get()}
            scope = scope_var.get()
            if scope != "Current cell":
                kwargs["cell_names"] = [scope]
            try:
                simplify = mm_to_dbu(simplify_var.get())
            except tk.TclError:
                simplify = 0
            if simplify > 0:
                kwargs["simplify"] = simplify
            if op in BOOLEAN_OPS:
                b_layer = layer_num(b_var.get())
                if b_layer is None:
                    error_var.set(f"Pick Layer B for {op}.")
                    return
                kwargs["layer_b"] = b_layer
            else:
                try:
                    amount = mm_to_dbu(amount_var.get())
                except tk.TclError:
                    error_var.set("Amount must be a number.")
                    return
                if amount <= 0:
                    error_var.set("Amount must be greater than zero.")
                    return
                kwargs["amount"] = amount
            try:
                written = self.session.mask_op(op, a_layer, result_layer, **kwargs)
            except LayoutError as e:
                error_var.set(str(e))
                return
            total = sum(written.values())
            made_visible = False
            if total > 0:
                # A mask op writing real output straight to a layer whose
                # visibility happens to be off is otherwise
                # indistinguishable from writing nothing at all -- the
                # canvas looks identical either way. Since the whole point
                # of running the operation was to see the result, turn the
                # result layer on rather than leaving it silently hidden.
                result_layer_obj = self.session.library.layers[result_layer]
                if not result_layer_obj.visible:
                    result_layer_obj.visible = True
                    made_visible = True
            self.refresh_all()
            if total == 0:
                self.status.set(
                    f"{op}: no output -- layer {a_layer} had nothing on it"
                    f"{'' if flatten_var.get() else ' (try Include placed cells)'}"
                )
            else:
                msg = f"{op}: wrote {total} polygon(s) to layer {result_layer}"
                if made_visible:
                    msg += " (layer visibility turned on)"
                self.status.set(msg)
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(6, 12))
        ttk.Button(btn_row, text="Apply", command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        win.bind("<Escape>", lambda e: win.destroy())

    def show_topview_dialog(self):
        """Tools > Top View...: opens the layer-table Top View window
        (hlayout.gui.topview_window). Kept by name for scripts/MCP."""
        return open_topview_window(self)

    def show_assembly_dialog(self):
        """Tools > Assembly Drawing...: opens the assembly drawing window
        (hlayout.gui.assembly_window) -- sheet, title block, scaled top
        view and pin/resistor tables in one cell. Kept by name for
        scripts/MCP."""
        return open_assembly_window(self)

    def read_netlist(self):
        """Tools > Read Netlist...: step 1 of the design flow -- read the
        netlist (net names + printed resistors), then design the resistors
        in the Resistor Designer, which opens with them; then Auto Place."""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self, title="Read Netlist",
            filetypes=[("Netlists", "*.net *.csv *.tng *.tan"), ("All files", "*.*")])
        if not path:
            return
        try:
            r = self.session.read_netlist(path)
        except Exception as e:
            dialogs.show_error("Read Netlist", str(e), parent=self)
            return
        self.refresh_all()
        lines = [f"{r['parts']} parts, {r['nets']} nets -- {r['names']} pad net names stored.",
                 "",
                 f"Printed resistors: {len(r['resistors'])}"
                 + (f" ({len(r['added'])} new, {len(r['changed'])} changed -- to design again, "
                    f"{len(r['kept'])} kept with their design)" if r["resistors"] else "")]
        if r["bad_values"]:
            lines.append(f"Values I couldn't read (fix in the schematic): "
                         f"{', '.join(r['bad_values'])}")
        if r["resistors"]:
            lines += ["", "Next: design each resistor in the Resistor Designer (pick an ink "
                          "and Use This Solution), then Tools > Auto Place..."]
        dialogs.show_info("Read Netlist", "\n".join(lines), parent=self)
        if r["resistors"]:
            from .resistor_designer import open_resistor_designer
            win = open_resistor_designer(self)
            try:                                    # an open designer: show the new list
                win.resistors.clear()
                win._load_resistors()
                win._refresh_all()
            except Exception:
                pass
        self.status.set(f"Netlist read: {r['parts']} parts, {len(r['resistors'])} resistor(s) "
                        "to design")

    def show_extract_netlist_dialog(self):
        """Tools > Extract Netlist...: opens the checkbox-table window
        (hlayout.gui.netlist_window). Kept by name for scripts/MCP."""
        return open_netlist_window(self)
