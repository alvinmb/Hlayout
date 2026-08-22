"""Design Rule Check (DRC) tool -- Tools > DRC...

A rule table (kind, layer(s), value, name) persisted into the current
design's technology file (hlayout.io.techfile.load_drc_rules() /
save_drc_rules(), the same tech_params-blob pattern as every other
process rule set in this tool -- see the Thick-Film Resistor
Designer's design/geometry rules), plus a Run DRC button that checks
those rules against the current cell (descending into every placed
cell instance the same way Mask Operations does -- see
hlayout.core.maskops/hlayout.core.drc) and lists what it finds.

Each rule is persisted the moment it's added/edited/deleted (there's
no separate "Save Rules" step to forget), since a DRC rule set is
process configuration, not per-session scratch state -- the same
reasoning Setup > Resistor Geometry Rules... uses.
"""

import tkinter as tk
from tkinter import ttk

from . import dialogs
from .fonts import section_header_font
from ..core.drc import DRC_RULE_KINDS, TWO_LAYER_KINDS, DrcRule
from ..core.errors import LayoutError
from ..io.techfile import load_drc_rules, save_drc_rules
from ..core.units import DBU_MM, dbu_to_mm, mm_to_dbu


def _value_to_display(kind, value):
    """DRC rule storage stays in DBU (DBU^2 for MIN_AREA) -- convert to
    the mm-based units shown in the dialog/table. Area scales as the
    square of the linear DBU->mm factor, so MIN_AREA needs its own
    conversion rather than dbu_to_mm()."""
    if kind == "MIN_AREA":
        return value * DBU_MM * DBU_MM
    return dbu_to_mm(value)


def _display_to_value(kind, display_value):
    if kind == "MIN_AREA":
        return display_value / (DBU_MM * DBU_MM)
    return float(mm_to_dbu(display_value))


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


class RuleDialog(tk.Toplevel):
    """Modal Add/Edit form for one DRC rule."""

    def __init__(self, parent, library, rule: DrcRule = None):
        super().__init__(parent)
        self.title("Add DRC Rule" if rule is None else "Edit DRC Rule")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.library = library
        self.result = None
        self._build(rule)
        self.bind("<Return>", lambda e: self._accept())
        self.bind("<Escape>", lambda e: self.destroy())

    def _build(self, rule):
        form = ttk.Frame(self, padding=16)
        form.pack(fill="both", expand=True)

        ttk.Label(form, text="Name:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.name_var = tk.StringVar(value=rule.name if rule else "")
        ttk.Entry(form, textvariable=self.name_var, width=22).grid(
            row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Kind:").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        self.kind_var = tk.StringVar(value=rule.kind if rule else DRC_RULE_KINDS[0])
        kind_combo = ttk.Combobox(form, textvariable=self.kind_var, width=20, state="readonly",
                                   values=list(DRC_RULE_KINDS))
        kind_combo.grid(row=1, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Layer A:").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        self.a_var = tk.StringVar(value=_layer_label(self.library, rule.layer_a) if rule else "")
        a_combo = ttk.Combobox(form, textvariable=self.a_var, width=22, state="readonly",
                                values=_layer_choices(self.library))
        a_combo.grid(row=2, column=1, sticky="w", padx=4, pady=4)

        self.b_label = ttk.Label(form, text="Layer B:")
        self.b_label.grid(row=3, column=0, sticky="e", padx=4, pady=4)
        self.b_var = tk.StringVar(
            value=_layer_label(self.library, rule.layer_b) if rule and rule.layer_b is not None else "")
        self.b_combo = ttk.Combobox(form, textvariable=self.b_var, width=22, state="readonly",
                                     values=_layer_choices(self.library))
        self.b_combo.grid(row=3, column=1, sticky="w", padx=4, pady=4)

        self.value_label_var = tk.StringVar(value="Value (mm):")
        ttk.Label(form, textvariable=self.value_label_var).grid(
            row=4, column=0, sticky="e", padx=4, pady=4)
        _initial_kind = rule.kind if rule else DRC_RULE_KINDS[0]
        self.value_var = tk.DoubleVar(
            value=_value_to_display(_initial_kind, rule.value) if rule else 0.0)
        ttk.Spinbox(form, from_=0.000001, to=1e6, increment=0.001, textvariable=self.value_var, width=14).grid(
            row=4, column=1, sticky="w", padx=4, pady=4)

        self.hint_var = tk.StringVar(value="")
        ttk.Label(form, textvariable=self.hint_var, foreground="#888888", wraplength=320,
                  justify="left").grid(row=5, column=0, columnspan=2, sticky="w", padx=4, pady=(6, 0))

        self.error_var = tk.StringVar(value="")
        ttk.Label(form, textvariable=self.error_var, foreground="red", wraplength=320,
                  justify="left").grid(row=6, column=0, columnspan=2, sticky="w", padx=4)

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=(0, 12))
        ttk.Button(btn_row, text="OK", command=self._accept).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left", padx=6)

        kind_combo.bind("<<ComboboxSelected>>", lambda e: self._sync())
        self._sync()

    _HINTS = {
        "MIN_WIDTH": "Every feature on Layer A must be at least Value mm wide everywhere.",
        "MIN_AREA": "Every connected shape on Layer A must have area >= Value mm².",
        "MIN_SPACE": "Every pair of distinct shapes on Layer A must be >= Value mm apart.",
        "MIN_SPACE_BETWEEN": "Every shape on Layer A must be >= Value mm from every "
                              "non-overlapping shape on Layer B.",
        "MIN_ENCLOSURE": "Every shape on Layer B must be enclosed by Layer A by >= Value "
                          "mm on all sides (e.g. metal enclosing a via).",
    }

    def _sync(self):
        kind = self.kind_var.get()
        two_layer = kind in TWO_LAYER_KINDS
        self.b_combo.configure(state="readonly" if two_layer else "disabled")
        self.hint_var.set(self._HINTS.get(kind, ""))
        # Re-express the current value in the new kind's units when the
        # kind changes (MIN_AREA is mm^2, everything else is linear mm)
        # rather than leaving a stale number that now means something
        # else.
        prev_kind = getattr(self, "_last_kind", kind)
        if prev_kind != kind:
            try:
                current_dbu = _display_to_value(prev_kind, self.value_var.get())
                self.value_var.set(_value_to_display(kind, current_dbu))
            except tk.TclError:
                pass
        self._last_kind = kind
        self.value_label_var.set("Value (mm²):" if kind == "MIN_AREA" else "Value (mm):")

    def _accept(self):
        kind = self.kind_var.get()
        layer_a = _parse_layer_choice(self.a_var.get())
        if layer_a is None:
            self.error_var.set("Pick Layer A.")
            return
        layer_b = None
        if kind in TWO_LAYER_KINDS:
            layer_b = _parse_layer_choice(self.b_var.get())
            if layer_b is None:
                self.error_var.set("Pick Layer B.")
                return
        try:
            value = _display_to_value(kind, self.value_var.get())
        except tk.TclError:
            self.error_var.set("Value must be a number.")
            return
        try:
            rule = DrcRule(kind=kind, layer_a=layer_a, layer_b=layer_b, value=value,
                            name=self.name_var.get().strip())
        except LayoutError as e:
            self.error_var.set(str(e))
            return
        self.result = rule
        self.destroy()


class DrcWindow(tk.Toplevel):
    """Tools > DRC... . `library` is the current design's Library;
    rules are loaded from it on open and persisted back into it
    immediately on every add/edit/delete. `app` is the main App (used
    to run DRC against the live session and, if requested, draw
    violation markers into the design and refresh the canvas)."""

    def __init__(self, master, library, app=None):
        super().__init__(master)
        self.title("Design Rule Check (DRC)")
        self.geometry("1000x700")
        self.library = library
        self.app = app
        self.rules = [DrcRule.from_dict(r) for r in load_drc_rules(library)]
        self.violations = []
        self._build()
        self._refresh_rules()

    def _build(self):
        toolbar = ttk.Frame(self, padding=6)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Add Rule...", command=self._add_rule).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Edit Rule...", command=self._edit_rule).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Delete Rule", command=self._delete_rule).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="Run DRC", command=self._run_drc).pack(side="left", padx=2)

        self.mark_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text="Mark violations on layer:",
                         variable=self.mark_var).pack(side="left", padx=(16, 2))
        self.error_layer_var = tk.StringVar(value="")
        ttk.Combobox(toolbar, textvariable=self.error_layer_var, width=16, state="readonly",
                     values=_layer_choices(self.library)).pack(side="left", padx=2)

        ttk.Button(toolbar, text="About", command=self._about).pack(side="right", padx=2)

        body = ttk.Panedwindow(self, orient="vertical")
        body.pack(fill="both", expand=True, padx=4, pady=4)

        # Bold, but otherwise tracking whatever size TkDefaultFont is
        # currently set to (see App._apply_global_font_size and
        # hlayout.gui.fonts.section_header_font) rather than a
        # hardcoded point size that would drift out of sync with the
        # rest of the app's font size -- same pattern as LayerPanel's
        # own header row.
        section_font = section_header_font(self)

        top = ttk.Frame(body)
        ttk.Label(top, text="RULES", foreground="#3fb950",
                  font=section_font).pack(anchor="w", padx=4, pady=(4, 0))
        rule_cols = ("name", "kind", "layer_a", "layer_b", "value")
        self.rule_table = ttk.Treeview(top, columns=rule_cols, show="headings", height=8)
        for col, heading, width in [
            ("name", "Name", 120), ("kind", "Kind", 140), ("layer_a", "Layer A", 100),
            ("layer_b", "Layer B", 100), ("value", "Value (mm)", 100),
        ]:
            self.rule_table.heading(col, text=heading)
            self.rule_table.column(col, width=width, anchor="center")
        self.rule_table.pack(fill="both", expand=True, padx=4, pady=4)
        body.add(top, weight=1)

        bottom = ttk.Frame(body)
        ttk.Label(bottom, text="VIOLATIONS", foreground="#f78166",
                  font=section_font).pack(anchor="w", padx=4, pady=(4, 0))
        viol_cols = ("rule", "cell", "message")
        self.viol_table = ttk.Treeview(bottom, columns=viol_cols, show="headings", height=12)
        for col, heading, width in [
            ("rule", "Rule", 160), ("cell", "Cell", 120), ("message", "Message", 400),
        ]:
            self.viol_table.heading(col, text=heading)
            self.viol_table.column(col, width=width, anchor="w")
        self.viol_table.pack(fill="both", expand=True, padx=4, pady=4)
        body.add(bottom, weight=2)

        status = ttk.Frame(self, padding=(6, 3))
        status.pack(fill="x")
        self.status_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.status_var, foreground="#8b949e").pack(side="left")

    # -- rules ---------------------------------------------------------
    def _refresh_rules(self):
        self.rule_table.delete(*self.rule_table.get_children())
        for i, rule in enumerate(self.rules):
            display_value = _value_to_display(rule.kind, rule.value)
            unit = "mm²" if rule.kind == "MIN_AREA" else "mm"
            self.rule_table.insert("", "end", iid=str(i), values=(
                rule.name, rule.kind, _layer_label(self.library, rule.layer_a),
                _layer_label(self.library, rule.layer_b) if rule.layer_b is not None else "",
                f"{display_value:g} {unit}",
            ))
        self.status_var.set(f"{len(self.rules)} rule(s) saved to the technology file")

    def _persist(self):
        save_drc_rules(self.library, self.rules)

    def _current_rule_row(self):
        sel = self.rule_table.selection()
        return int(sel[0]) if sel else -1

    def _add_rule(self):
        dlg = RuleDialog(self, self.library)
        self.wait_window(dlg)
        if dlg.result is not None:
            self.rules.append(dlg.result)
            self._persist()
            self._refresh_rules()

    def _edit_rule(self):
        row = self._current_rule_row()
        if row < 0:
            dialogs.show_info("Edit Rule", "Select a rule first.", parent=self)
            return
        dlg = RuleDialog(self, self.library, rule=self.rules[row])
        self.wait_window(dlg)
        if dlg.result is not None:
            self.rules[row] = dlg.result
            self._persist()
            self._refresh_rules()

    def _delete_rule(self):
        row = self._current_rule_row()
        if row < 0:
            dialogs.show_info("Delete Rule", "Select a rule first.", parent=self)
            return
        del self.rules[row]
        self._persist()
        self._refresh_rules()

    # -- run -------------------------------------------------------------
    def _run_drc(self):
        if self.app is None:
            dialogs.show_info("Run DRC", "This DRC window isn't attached to a layout window.",
                                 parent=self)
            return
        if not self.rules:
            dialogs.show_info("Run DRC", "Add at least one rule first.", parent=self)
            return
        error_layer = None
        if self.mark_var.get():
            error_layer = _parse_layer_choice(self.error_layer_var.get())
            if error_layer is None:
                dialogs.show_info(
                    "Run DRC",
                    "Pick a layer to mark violations on, or uncheck "
                    "'Mark violations on layer'.", parent=self)
                return
        try:
            self.violations = self.app.session.run_drc(self.rules, error_layer=error_layer)
        except LayoutError as e:
            dialogs.show_error("Run DRC", str(e), parent=self)
            return
        self.viol_table.delete(*self.viol_table.get_children())
        for i, v in enumerate(self.violations):
            self.viol_table.insert("", "end", iid=str(i), values=(
                v.rule.name or v.rule.kind, v.cell_name, self._violation_message(v)))
        if error_layer is not None:
            self.app.refresh_all()
        if self.violations:
            self.status_var.set(f"DRC: {len(self.violations)} violation(s) found")
        else:
            self.status_var.set("DRC: no violations found")

    def _violation_message(self, v):
        """GUI-facing version of DrcViolation.message: the underlying
        DrcRule.label (hlayout.core.drc) reports its value in DBU, which
        is correct for the scripting API's own text output, but the GUI
        shows every size in mm -- so rebuild the same message here with
        the value converted rather than changing the shared core label."""
        rule = v.rule
        display_value = _value_to_display(rule.kind, rule.value)
        unit = "mm²" if rule.kind == "MIN_AREA" else "mm"
        if rule.kind in TWO_LAYER_KINDS:
            base = f"{rule.kind}(layer {rule.layer_a}, layer {rule.layer_b}) >= {display_value:g} {unit}"
        else:
            base = f"{rule.kind}(layer {rule.layer_a}) >= {display_value:g} {unit}"
        return f"{rule.name}: {base}" if rule.name else base

    def _about(self):
        dialogs.show_info(
            "About",
            "Design Rule Check (DRC)\n\n"
            "MIN_WIDTH / MIN_AREA / MIN_SPACE -- single-layer checks.\n"
            "MIN_SPACE_BETWEEN / MIN_ENCLOSURE -- between two layers.\n\n"
            "Every check descends into placed cell instances (Srefs), so "
            "a design built out of placed cells (resistors, vias, ...) "
            "is checked, not just the current cell's own directly-drawn "
            "shapes.\n\n"
            "Rules are saved into this design's technology file "
            "(Setup > Save Technology File...).",
            parent=self,
        )


def open_drc_window(app):
    """Tools > DRC... . Reuses a single window per App instance --
    calling this again while one is already open just brings it to the
    front, instead of stacking up duplicates."""
    win = getattr(app, "_drc_window", None)
    if win is not None and win.winfo_exists():
        win.lift()
        win.focus_set()
        return win
    win = DrcWindow(app, app.session.library, app=app)
    app._drc_window = win
    return win
