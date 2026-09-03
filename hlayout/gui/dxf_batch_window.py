"""DXF Export Sets tool -- Tools > DXF Export Sets...

A job table (name, layers, output file) persisted into the current
design's technology file (hlayout.io.techfile.load_dxf_export_jobs()/
save_dxf_export_jobs(), the same tech_params-blob pattern DRC rules,
mask-op batch steps, and Gerber export jobs use -- see
hlayout.gui.drc_window/hlayout.gui.maskop_window/
hlayout.gui.gerber_batch_window), plus an Export All button that runs
every job against the current cell via Session.run_dxf_export_batch()
and lists what each job wrote.

Nearly identical to hlayout.gui.gerber_batch_window -- the only real
difference is DXF writes ONE file per job (with every layer inside it,
via DXF's own layer table) rather than one file per layer, so a job has
a single output "file" rather than a "prefix". Like the Gerber batch
window (and unlike the mask-op batch window), job order doesn't matter
-- a DXF export never modifies the design, only writes a file, so
there's no Move Up/Move Down here.

There's no separate single-shot File/Tools > Export DXF... command any
more -- "Quick Export (All Layers)..." on this window's toolbar covers
that same one-off case (prompts for an output file, exports every
visible layer with data, no saved job needed) so this one window
covers both the quick one-off export and a repeatable saved set you can
re-run together with Run All Jobs.
"""

import os
import tkinter as tk
from tkinter import filedialog, ttk

from . import dialogs
from .fonts import section_header_font
from ..core.errors import LayoutError
from ..io.dxfio import DxfExportJob


def _layer_label(library, number):
    return f"{number} ({library.layers[number].name})"


class JobDialog(tk.Toplevel):
    """Modal Add/Edit form for one DXF export job."""

    def __init__(self, parent, library, job: DxfExportJob = None):
        super().__init__(parent)
        self.title("Add DXF Export Job" if job is None else "Edit DXF Export Job")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.library = library
        self.result = None
        self._build(job)
        self.bind("<Escape>", lambda e: self.destroy())

    def _build(self, job):
        form = ttk.Frame(self, padding=16)
        form.pack(fill="both", expand=True)

        ttk.Label(form, text="Name:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.name_var = tk.StringVar(value=job.name if job else "")
        ttk.Entry(form, textvariable=self.name_var, width=28).grid(
            row=0, column=1, columnspan=2, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Output file:").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        self.path_var = tk.StringVar(value=job.path if job else "")
        ttk.Entry(form, textvariable=self.path_var, width=28).grid(
            row=1, column=1, sticky="w", padx=4, pady=4)
        ttk.Button(form, text="Browse...", command=self._browse).grid(
            row=1, column=2, sticky="w", padx=4, pady=4)

        self.all_layers_var = tk.BooleanVar(value=job is None or not job.layers)
        ttk.Checkbutton(
            form, text="All visible layers with data", variable=self.all_layers_var,
            command=self._sync,
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=4, pady=(10, 0))

        ttk.Label(form, text="Layers:").grid(row=3, column=0, sticky="ne", padx=4, pady=4)
        list_frame = ttk.Frame(form)
        list_frame.grid(row=3, column=1, columnspan=2, sticky="w", padx=4, pady=4)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical")
        self.layer_list = tk.Listbox(
            list_frame, selectmode="multiple", height=10, width=28,
            exportselection=False, yscrollcommand=scrollbar.set,
        )
        scrollbar.config(command=self.layer_list.yview)
        self.layer_list.pack(side="left", fill="y")
        scrollbar.pack(side="left", fill="y")
        selected_layers = set(job.layers) if job and job.layers else set()
        for n in range(len(self.library.layers)):
            self.layer_list.insert("end", _layer_label(self.library, n))
            if n in selected_layers:
                self.layer_list.selection_set(n)

        self.error_var = tk.StringVar(value="")
        ttk.Label(form, textvariable=self.error_var, foreground="red", wraplength=340,
                  justify="left").grid(row=4, column=0, columnspan=3, sticky="w", padx=4, pady=(6, 0))

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=(0, 12))
        ttk.Button(btn_row, text="OK", command=self._accept).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left", padx=6)

        self._sync()

    def _sync(self):
        self.layer_list.configure(state="disabled" if self.all_layers_var.get() else "normal")

    def _browse(self):
        path = filedialog.asksaveasfilename(
            title="DXF Output File", defaultextension=".dxf",
            filetypes=[("DXF", "*.dxf"), ("All files", "*.*")], parent=self,
        )
        if path:
            self.path_var.set(path)

    def _accept(self):
        name = self.name_var.get().strip()
        path = self.path_var.get().strip()
        if not path:
            self.error_var.set("Pick an output file.")
            return
        layers = None
        if not self.all_layers_var.get():
            layers = [int(i) for i in self.layer_list.curselection()]
            if not layers:
                self.error_var.set("Pick at least one layer, or check 'All visible layers with data'.")
                return
        try:
            job = DxfExportJob(name=name, path=path, layers=layers)
        except LayoutError as e:
            self.error_var.set(str(e))
            return
        self.result = job
        self.destroy()


class DxfBatchWindow(tk.Toplevel):
    """Tools > DXF Export Sets... . `library` is the current design's
    Library; jobs are loaded from it on open and persisted back into it
    immediately on every add/edit/delete. `app` is the main App (used
    to run the batch against the live session)."""

    def __init__(self, master, library, app=None):
        super().__init__(master)
        self.title("DXF Export Sets")
        self.geometry("1000x700")
        self.library = library
        self.app = app
        self.jobs = [DxfExportJob.from_dict(j) for j in _load_jobs(library)]
        self._build()
        self._refresh_jobs()
        self.bind("<Escape>", lambda e: self.destroy())

    def _build(self):
        toolbar = ttk.Frame(self, padding=6)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Add Job...", command=self._add_job).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Edit Job...", command=self._edit_job).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Delete Job", command=self._delete_job).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="Run All Jobs", command=self._run_batch).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Quick Export (All Layers)...",
                   command=self._quick_export).pack(side="left", padx=2)

        ttk.Label(toolbar, text="Apply to:").pack(side="left", padx=(16, 2))
        self.scope_var = tk.StringVar(value="Current cell")
        cell_names = list(self.library.cells.keys())
        ttk.Combobox(toolbar, textvariable=self.scope_var, state="readonly", width=18,
                     values=["Current cell"] + cell_names).pack(side="left", padx=2)

        ttk.Button(toolbar, text="Close", command=self.destroy).pack(side="right", padx=2)
        ttk.Button(toolbar, text="About", command=self._about).pack(side="right", padx=2)

        body = ttk.Panedwindow(self, orient="vertical")
        body.pack(fill="both", expand=True, padx=4, pady=4)

        section_font = section_header_font(self)

        top = ttk.Frame(body)
        ttk.Label(top, text="JOBS", foreground="#3fb950",
                  font=section_font).pack(anchor="w", padx=4, pady=(4, 0))
        job_cols = ("name", "layers", "file")
        self.job_table = ttk.Treeview(top, columns=job_cols, show="headings", height=8)
        for col, heading, width in [
            ("name", "Name", 140), ("layers", "Layers", 260), ("file", "Output file", 400),
        ]:
            self.job_table.heading(col, text=heading)
            self.job_table.column(col, width=width, anchor="w")
        self.job_table.pack(fill="both", expand=True, padx=4, pady=4)
        body.add(top, weight=1)

        bottom = ttk.Frame(body)
        ttk.Label(bottom, text="FILES WRITTEN", foreground="#f78166",
                  font=section_font).pack(anchor="w", padx=4, pady=(4, 0))
        result_cols = ("job", "layers", "entities", "file")
        self.result_table = ttk.Treeview(bottom, columns=result_cols, show="headings", height=12)
        for col, heading, width in [
            ("job", "Job", 140), ("layers", "Layers written", 200),
            ("entities", "Entities", 100), ("file", "File", 360),
        ]:
            self.result_table.heading(col, text=heading)
            self.result_table.column(col, width=width, anchor="w")
        self.result_table.pack(fill="both", expand=True, padx=4, pady=4)
        body.add(bottom, weight=2)

        status = ttk.Frame(self, padding=(6, 3))
        status.pack(fill="x")
        self.status_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.status_var, foreground="#8b949e").pack(side="left")

    # -- jobs --------------------------------------------------------------
    def _refresh_jobs(self):
        self.job_table.delete(*self.job_table.get_children())
        for i, job in enumerate(self.jobs):
            layer_desc = ("all visible layers" if not job.layers
                          else ", ".join(str(n) for n in job.layers))
            self.job_table.insert("", "end", iid=str(i), values=(
                job.name, layer_desc, job.path))
        self.status_var.set(f"{len(self.jobs)} job(s) saved to the technology file")

    def _persist(self):
        _save_jobs(self.library, self.jobs)

    def _current_job_row(self):
        sel = self.job_table.selection()
        return int(sel[0]) if sel else -1

    def _add_job(self):
        dlg = JobDialog(self, self.library)
        self.wait_window(dlg)
        if dlg.result is not None:
            self.jobs.append(dlg.result)
            self._persist()
            self._refresh_jobs()

    def _edit_job(self):
        row = self._current_job_row()
        if row < 0:
            dialogs.show_info("Edit Job", "Select a job first.", parent=self)
            return
        dlg = JobDialog(self, self.library, job=self.jobs[row])
        self.wait_window(dlg)
        if dlg.result is not None:
            self.jobs[row] = dlg.result
            self._persist()
            self._refresh_jobs()

    def _delete_job(self):
        row = self._current_job_row()
        if row < 0:
            dialogs.show_info("Delete Job", "Select a job first.", parent=self)
            return
        del self.jobs[row]
        self._persist()
        self._refresh_jobs()

    # -- run -----------------------------------------------------------------
    def _run_batch(self):
        if self.app is None:
            dialogs.show_info("Run All Jobs", "This window isn't attached to a layout window.",
                               parent=self)
            return
        if not self.jobs:
            dialogs.show_info("Run All Jobs", "Add at least one job first.", parent=self)
            return
        scope = self.scope_var.get()
        cell_name = None if scope == "Current cell" else scope
        try:
            results = self.app.session.run_dxf_export_batch(self.jobs, cell_name=cell_name)
        except LayoutError as e:
            dialogs.show_error("Run All Jobs", str(e), parent=self)
            return
        self.result_table.delete(*self.result_table.get_children())
        total_files = 0
        total_entities = 0
        row = 0
        for job, written_layers, entity_count in results:
            label = job.name or os.path.basename(job.path)
            layer_desc = ", ".join(str(n) for n in written_layers) if written_layers else "(no data)"
            self.result_table.insert("", "end", iid=str(row), values=(
                label, layer_desc, entity_count, os.path.basename(job.path)))
            row += 1
            if written_layers:
                total_files += 1
            total_entities += entity_count
        self.app.refresh_all() if hasattr(self.app, "refresh_all") else None
        self.status_var.set(
            f"Run All Jobs: {len(results)} job(s) run, {total_files} file(s) written, "
            f"{total_entities} entities total")

    def _quick_export(self):
        """One-off export of every visible layer with data, prompting
        for just an output file -- no saved job needed. Covers the
        same case the old single-shot File/Tools > Export DXF...
        dialog did, without leaving anything behind in the JOBS table
        (unless you separately choose Add Job...)."""
        if self.app is None:
            dialogs.show_info("Quick Export", "This window isn't attached to a layout window.",
                               parent=self)
            return
        path = filedialog.asksaveasfilename(
            title="Export DXF", defaultextension=".dxf",
            filetypes=[("DXF", "*.dxf"), ("All files", "*.*")],
            initialfile=self.library.name, parent=self,
        )
        if not path:
            return
        scope = self.scope_var.get()
        cell_name = None if scope == "Current cell" else scope
        try:
            written_layers, entity_count = self.app.session.export_dxf(path, cell_name=cell_name)
        except LayoutError as e:
            dialogs.show_error("Quick Export failed", str(e), parent=self)
            return
        if not written_layers:
            dialogs.show_info("Quick Export", "No visible layers had any data to export.", parent=self)
            return
        names = ", ".join(f"L{n}" for n in written_layers)
        dialogs.show_info(
            "Quick Export",
            f"Wrote {entity_count} entity(ies) across {len(written_layers)} layer(s) ({names})\n"
            f"to {os.path.basename(path)}",
            parent=self,
        )
        self.status_var.set(
            f"Quick Export: {entity_count} entities, {len(written_layers)} layers -> {path}")

    def _about(self):
        dialogs.show_info(
            "About",
            "DXF Export Sets\n\n"
            "A saved set of DXF export jobs -- each with its own layer "
            "subset and output file -- run together as one action.\n\n"
            "Unlike Tools > Mask Operation Batch..., job order doesn't "
            "matter: a DXF export never modifies the design, it only "
            "writes a file, so one job never depends on another's "
            "output.\n\n"
            "Unlike Gerber export, each job writes a SINGLE DXF file "
            "with every selected layer inside it (via DXF's own layer "
            "table), not one file per layer -- and text shapes ARE "
            "included, since DXF has a native text entity.\n\n"
            "Jobs are saved into this design's technology file "
            "(Setup > Save Technology File...).",
            parent=self,
        )


def _load_jobs(library):
    from ..io.techfile import load_dxf_export_jobs
    return load_dxf_export_jobs(library)


def _save_jobs(library, jobs):
    from ..io.techfile import save_dxf_export_jobs
    save_dxf_export_jobs(library, jobs)


def open_dxf_batch_window(app):
    """Tools > DXF Export Sets... . Reuses a single window per App
    instance -- calling this again while one is already open just
    brings it to the front, instead of stacking up duplicates."""
    win = getattr(app, "_dxf_batch_window", None)
    if win is not None and win.winfo_exists():
        win.lift()
        win.focus_set()
        return win
    win = DxfBatchWindow(app, app.session.library, app=app)
    app._dxf_batch_window = win
    return win
