"""Mask Operation Batch tool -- Tools > Mask Operation Batch...

A step table (kind, layer(s), amount, result layer, name) persisted
into the current design's technology file
(hlayout.io.techfile.load_mask_op_steps()/save_mask_op_steps(), the
same tech_params-blob pattern DRC rules use -- see hlayout.gui.
drc_window), plus a Run Batch button that replays those steps in order
against the current cell via Session.run_mask_op_batch() and lists what
each step wrote.

Unlike a DRC rule set, step ORDER matters here: a later step's Layer A/
Layer B can be an earlier step's own Result layer, so the batch is a
multi-step derived-layer recipe (e.g. "oversize the glaze opening, AND
it with the resistor ink, OVERSIZE that by the trim clearance") rather
than an unordered set of independent checks -- so this window adds
Move Up/Move Down alongside DRC's own Add/Edit/Delete, and every
add/edit/delete/reorder is persisted immediately, same as DRC.

This is a second, batch-oriented entry point alongside the existing
single-shot Tools > Mask Operations... dialog (App.show_mask_op_dialog)
-- that one-off dialog is still the quickest way to run a single mask
op by hand; this window is for a repeatable multi-step recipe you want
to save and re-run.
"""

import tkinter as tk
from tkinter import ttk

from . import dialogs
from .fonts import section_header_font
from ..core.errors import LayoutError
from ..core.maskops import BOOLEAN_MASK_OPS, MASK_OPS, MaskOpStep
from ..core.units import DBU_MM, dbu_to_mm, mm_to_dbu
from ..io.techfile import load_mask_op_steps, save_mask_op_steps


def _layer_choices(library):
    return [f"{n} ({library.layers[n].name})" for n in range(len(library.layers))]


def _layer_label(library, number):
    if number is None:
        return ""
    return f"{number} ({library.layers[number].name})"


def _parse_layer_choice(text):
    text = text.strip()
    if not text:
        return None
    return int(text.split(" ", 1)[0].split(":", 1)[0])


class StepDialog(tk.Toplevel):
    """Modal Add/Edit form for one mask-op batch step."""

    def __init__(self, parent, library, step: MaskOpStep = None):
        super().__init__(parent)
        self.title("Add Step" if step is None else "Edit Step")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.library = library
        self.result = None
        self._build(step)
        self.bind("<Return>", lambda e: self._accept())
        self.bind("<Escape>", lambda e: self.destroy())

    def _build(self, step):
        form = ttk.Frame(self, padding=16)
        form.pack(fill="both", expand=True)

        ttk.Label(form, text="Name:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.name_var = tk.StringVar(value=step.name if step else "")
        ttk.Entry(form, textvariable=self.name_var, width=22).grid(
            row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Operation:").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        self.kind_var = tk.StringVar(value=step.kind if step else MASK_OPS[0])
        kind_combo = ttk.Combobox(form, textvariable=self.kind_var, width=20, state="readonly",
                                   values=list(MASK_OPS))
        kind_combo.grid(row=1, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Layer A:").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        self.a_var = tk.StringVar(value=_layer_label(self.library, step.layer_a) if step else "")
        a_combo = ttk.Combobox(form, textvariable=self.a_var, width=22, state="readonly",
                                values=_layer_choices(self.library))
        a_combo.grid(row=2, column=1, sticky="w", padx=4, pady=4)

        self.b_label = ttk.Label(form, text="Layer B:")
        self.b_label.grid(row=3, column=0, sticky="e", padx=4, pady=4)
        self.b_var = tk.StringVar(
            value=_layer_label(self.library, step.layer_b) if step and step.layer_b is not None else "")
        self.b_combo = ttk.Combobox(form, textvariable=self.b_var, width=22, state="readonly",
                                     values=_layer_choices(self.library))
        self.b_combo.grid(row=3, column=1, sticky="w", padx=4, pady=4)

        self.amount_label = ttk.Label(form, text="Amount (mm):")
        self.amount_label.grid(row=4, column=0, sticky="e", padx=4, pady=4)
        self.amount_var = tk.DoubleVar(value=dbu_to_mm(step.amount) if step else 0.0)
        self.amount_spin = ttk.Spinbox(form, from_=0.0, to=1e6, increment=0.001,
                                        textvariable=self.amount_var, width=14)
        self.amount_spin.grid(row=4, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Result layer:").grid(row=5, column=0, sticky="e", padx=4, pady=4)
        self.result_var = tk.StringVar(
            value=_layer_label(self.library, step.result_layer) if step else "")
        ttk.Combobox(form, textvariable=self.result_var, width=22, state="readonly",
                      values=_layer_choices(self.library)).grid(
            row=5, column=1, sticky="w", padx=4, pady=4)

        self.flatten_var = tk.BooleanVar(value=step.flatten if step else True)
        ttk.Checkbutton(
            form, text="Include placed cells (Sref) when gathering input geometry",
            variable=self.flatten_var,
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 0))

        ttk.Label(form, text="Simplify (mm, 0=off):").grid(
            row=7, column=0, sticky="e", padx=4, pady=4)
        self.simplify_var = tk.DoubleVar(value=dbu_to_mm(step.simplify) if step and step.simplify else 0.0)
        ttk.Spinbox(form, from_=0.0, to=1e6, increment=0.001, textvariable=self.simplify_var, width=14).grid(
            row=7, column=1, sticky="w", padx=4, pady=4)

        self.hint_var = tk.StringVar(value="")
        ttk.Label(form, textvariable=self.hint_var, foreground="#888888", wraplength=320,
                  justify="left").grid(row=8, column=0, columnspan=2, sticky="w", padx=4, pady=(6, 0))

        self.error_var = tk.StringVar(value="")
        ttk.Label(form, textvariable=self.error_var, foreground="red", wraplength=320,
                  justify="left").grid(row=9, column=0, columnspan=2, sticky="w", padx=4)

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=(0, 12))
        ttk.Button(btn_row, text="OK", command=self._accept).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left", padx=6)

        kind_combo.bind("<<ComboboxSelected>>", lambda e: self._sync())
        self._sync()

    _HINTS = {
        "OVERSIZE": "Grows everything on Layer A by Amount, written to Result layer.",
        "UNDERSIZE": "Shrinks everything on Layer A by Amount, written to Result layer.",
        "AND": "Intersection of Layer A and Layer B, written to Result layer.",
        "OR": "Union of Layer A and Layer B, written to Result layer.",
        "NOT": "Layer A minus Layer B, written to Result layer.",
        "XOR": "Everything on exactly one of Layer A/B, written to Result layer.",
    }

    def _sync(self):
        kind = self.kind_var.get()
        is_boolean = kind in BOOLEAN_MASK_OPS
        self.b_combo.configure(state="readonly" if is_boolean else "disabled")
        self.amount_spin.configure(state="disabled" if is_boolean else "normal")
        self.hint_var.set(self._HINTS.get(kind, ""))

    def _accept(self):
        kind = self.kind_var.get()
        layer_a = _parse_layer_choice(self.a_var.get())
        result_layer = _parse_layer_choice(self.result_var.get())
        if layer_a is None or result_layer is None:
            self.error_var.set("Pick Layer A and a Result layer.")
            return
        layer_b = None
        amount = 0.0
        if kind in BOOLEAN_MASK_OPS:
            layer_b = _parse_layer_choice(self.b_var.get())
            if layer_b is None:
                self.error_var.set(f"Pick Layer B for {kind}.")
                return
        else:
            try:
                amount = mm_to_dbu(self.amount_var.get())
            except tk.TclError:
                self.error_var.set("Amount must be a number.")
                return
            if amount <= 0:
                self.error_var.set("Amount must be greater than zero.")
                return
        try:
            simplify = mm_to_dbu(self.simplify_var.get())
        except tk.TclError:
            simplify = 0
        try:
            step = MaskOpStep(
                kind=kind, layer_a=layer_a, result_layer=result_layer, layer_b=layer_b,
                amount=amount, name=self.name_var.get().strip(),
                flatten=self.flatten_var.get(), simplify=simplify,
            )
        except LayoutError as e:
            self.error_var.set(str(e))
            return
        self.result = step
        self.destroy()


class MaskOpBatchWindow(tk.Toplevel):
    """Tools > Mask Operation Batch... . `library` is the current
    design's Library; steps are loaded from it on open and persisted
    back into it immediately on every add/edit/delete/reorder. `app` is
    the main App (used to run the batch against the live session and
    refresh the canvas)."""

    def __init__(self, master, library, app=None):
        super().__init__(master)
        self.title("Mask Operation Batch")
        self.geometry("1000x700")
        self.library = library
        self.app = app
        self.steps = [MaskOpStep.from_dict(s) for s in load_mask_op_steps(library)]
        self._build()
        self._refresh_steps()
        self.bind("<Escape>", lambda e: self.destroy())

    def _build(self):
        toolbar = ttk.Frame(self, padding=6)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Add Step...", command=self._add_step).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Edit Step...", command=self._edit_step).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Delete Step", command=self._delete_step).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="Move Up", command=lambda: self._move_step(-1)).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Move Down", command=lambda: self._move_step(1)).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="Run Batch", command=self._run_batch).pack(side="left", padx=2)

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
        ttk.Label(top, text="STEPS (run top to bottom)", foreground="#3fb950",
                  font=section_font).pack(anchor="w", padx=4, pady=(4, 0))
        step_cols = ("name", "kind", "layer_a", "layer_b", "amount", "result")
        self.step_table = ttk.Treeview(top, columns=step_cols, show="headings", height=8)
        for col, heading, width in [
            ("name", "Name", 120), ("kind", "Kind", 100), ("layer_a", "Layer A", 100),
            ("layer_b", "Layer B", 100), ("amount", "Amount (mm)", 100),
            ("result", "Result layer", 100),
        ]:
            self.step_table.heading(col, text=heading)
            self.step_table.column(col, width=width, anchor="center")
        self.step_table.pack(fill="both", expand=True, padx=4, pady=4)
        body.add(top, weight=1)

        bottom = ttk.Frame(body)
        ttk.Label(bottom, text="RESULTS", foreground="#f78166",
                  font=section_font).pack(anchor="w", padx=4, pady=(4, 0))
        result_cols = ("step", "cell", "written")
        self.result_table = ttk.Treeview(bottom, columns=result_cols, show="headings", height=12)
        for col, heading, width in [
            ("step", "Step", 220), ("cell", "Cell", 160), ("written", "Polygons written", 160),
        ]:
            self.result_table.heading(col, text=heading)
            self.result_table.column(col, width=width, anchor="w")
        self.result_table.pack(fill="both", expand=True, padx=4, pady=4)
        body.add(bottom, weight=2)

        status = ttk.Frame(self, padding=(6, 3))
        status.pack(fill="x")
        self.status_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.status_var, foreground="#8b949e").pack(side="left")

    # -- steps -----------------------------------------------------------
    def _refresh_steps(self):
        self.step_table.delete(*self.step_table.get_children())
        for i, step in enumerate(self.steps):
            amount_mm = dbu_to_mm(step.amount) if step.kind not in BOOLEAN_MASK_OPS else None
            self.step_table.insert("", "end", iid=str(i), values=(
                step.name, step.kind, _layer_label(self.library, step.layer_a),
                _layer_label(self.library, step.layer_b) if step.layer_b is not None else "",
                f"{amount_mm:g}" if amount_mm is not None else "",
                _layer_label(self.library, step.result_layer),
            ))
        self.status_var.set(f"{len(self.steps)} step(s) saved to the technology file")

    def _persist(self):
        save_mask_op_steps(self.library, self.steps)

    def _current_step_row(self):
        sel = self.step_table.selection()
        return int(sel[0]) if sel else -1

    def _add_step(self):
        dlg = StepDialog(self, self.library)
        self.wait_window(dlg)
        if dlg.result is not None:
            self.steps.append(dlg.result)
            self._persist()
            self._refresh_steps()

    def _edit_step(self):
        row = self._current_step_row()
        if row < 0:
            dialogs.show_info("Edit Step", "Select a step first.", parent=self)
            return
        dlg = StepDialog(self, self.library, step=self.steps[row])
        self.wait_window(dlg)
        if dlg.result is not None:
            self.steps[row] = dlg.result
            self._persist()
            self._refresh_steps()

    def _delete_step(self):
        row = self._current_step_row()
        if row < 0:
            dialogs.show_info("Delete Step", "Select a step first.", parent=self)
            return
        del self.steps[row]
        self._persist()
        self._refresh_steps()

    def _move_step(self, delta):
        row = self._current_step_row()
        if row < 0:
            dialogs.show_info("Move Step", "Select a step first.", parent=self)
            return
        new_row = row + delta
        if not (0 <= new_row < len(self.steps)):
            return
        self.steps[row], self.steps[new_row] = self.steps[new_row], self.steps[row]
        self._persist()
        self._refresh_steps()
        self.step_table.selection_set(str(new_row))

    # -- run ---------------------------------------------------------------
    def _run_batch(self):
        if self.app is None:
            dialogs.show_info("Run Batch", "This window isn't attached to a layout window.",
                               parent=self)
            return
        if not self.steps:
            dialogs.show_info("Run Batch", "Add at least one step first.", parent=self)
            return
        scope = self.scope_var.get()
        cell_names = None if scope == "Current cell" else [scope]
        try:
            results = self.app.session.run_mask_op_batch(self.steps, cell_names=cell_names)
        except LayoutError as e:
            dialogs.show_error("Run Batch", str(e), parent=self)
            return
        self.result_table.delete(*self.result_table.get_children())
        total_written = 0
        row = 0
        for step, written in results:
            label = step.name or step.label
            if not written:
                self.result_table.insert("", "end", iid=str(row), values=(label, "", "0"))
                row += 1
                continue
            for cell_name, count in written.items():
                self.result_table.insert("", "end", iid=str(row), values=(label, cell_name, count))
                total_written += count
                row += 1
        self.app.refresh_all()
        self.status_var.set(
            f"Batch: {len(results)} step(s) run, {total_written} polygon(s) written total")

    def _about(self):
        dialogs.show_info(
            "About",
            "Mask Operation Batch\n\n"
            "A saved, ORDERED series of mask operations "
            "(OVERSIZE/UNDERSIZE/AND/OR/NOT/XOR), run together as one "
            "action -- like Tools > DRC..., but for building up a "
            "derived-layer recipe instead of checking rules.\n\n"
            "Order matters: a later step's Layer A/B can be an earlier "
            "step's own Result layer, so the batch replays as a "
            "multi-step pipeline (e.g. oversize an opening, AND it "
            "with another layer, oversize the result again) -- use "
            "Move Up/Move Down to reorder.\n\n"
            "Every step descends into placed cell instances (Srefs) by "
            "default, same as Tools > Mask Operations....\n\n"
            "Steps are saved into this design's technology file "
            "(Setup > Save Technology File...).",
            parent=self,
        )


def open_maskop_batch_window(app):
    """Tools > Mask Operation Batch... . Reuses a single window per App
    instance -- calling this again while one is already open just
    brings it to the front, instead of stacking up duplicates."""
    win = getattr(app, "_maskop_batch_window", None)
    if win is not None and win.winfo_exists():
        win.lift()
        win.focus_set()
        return win
    win = MaskOpBatchWindow(app, app.session.library, app=app)
    app._maskop_batch_window = win
    return win
