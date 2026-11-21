# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Create > Via / Pad / Chip wizards: build a via cell, a pad cell or a
chip (die) cell with its pins, step by step.

Part of hlayout.gui.app.App (a mixin): these methods are App's own --
call them as always."""

import tkinter as tk
from tkinter import ttk

from . import dialogs
from ..core.errors import LayoutError
from ..core.units import dbu_to_mm, mm_to_dbu
from ..io.techfile import load_route_rules


def chip_pin_names(text: str):
    """Chip Wizard 'Pin names': 'E,B' / 'E B' / 'E; B' -> ['E', 'B'];
    blank -> [] (pins numbered 1..N)."""
    import re as _re
    return [t for t in _re.split(r"[,;\s]+", text or "") if t]


class WizardsMixin:
    def show_via_wizard(self, on_created=None):
        """Create > Via Wizard...: create a via cell -- a square or round
        pad drawn on two layers, representing the contact area where a
        via connects them, optionally with a dielectric via window cut
        between them -- and save it directly into an attached reference
        library's own file, so it becomes a reusable part (rather than
        something that only lives in this design). Backed by
        Session.create_via_cell(); same operation as the command bar's
        VIA <layer1> <layer2> ... command.

        `on_created`, if given, is called with the new cell's name once
        a via cell is successfully created -- used by the Route Layers
        form's "New Via Cell..." button to select it straight away.

        Cell name defaults to VIA_Layer<n1>_to_Layer<n2> (shown as a
        placeholder, updated live as Layer 1/2 change) but can be
        overridden -- e.g. to give a process-specific via more than one
        library part could otherwise collide on a distinct name.

        The dielectric via window (off by default -- a bare two-pad via
        has no dielectric) is a same-shape opening cut in a third layer
        between the two conductor layers, representing the actual
        electrical contact area in a real process; normally sized AT OR
        ABOVE the pad size, since the pad lands within/over the opening.
        """
        # The window to actually parent/transient this wizard to: normally
        # the main App, but Setup > Technology Properties...'s Route tab
        # also opens this (via its "New Via Cell..." button) while ITS OWN
        # Toplevel holds the modal grab. A Toplevel that's transient to the
        # App instead of to that currently-modal dialog is a sibling of it
        # in Windows' eyes, not a child -- so once this wizard closes,
        # Windows can activate/raise the App above the still-open dialog
        # instead of returning to it, leaving it buried and looking
        # "frozen" (nothing responds, because nothing you can see is
        # actually on top). Resolving the parent from whatever currently
        # has focus keeps the chain correct either way this is opened.
        parent = self.focus_get()
        parent = parent.winfo_toplevel() if parent is not None else self

        if not self.session.attached_libraries:
            dialogs.show_info(
                "Via Wizard",
                "No library is attached yet. Use Setup > Attach Library... to "
                "attach the GDSII file the via should be saved into, then try "
                "the Via Wizard again.",
                parent=parent,
            )
            return

        win = tk.Toplevel(parent)
        win.title("Via Wizard")
        win.transient(parent)
        win.grab_set()
        win.resizable(False, False)

        def _close():
            win.destroy()
            # Handing this wizard's grab back to `parent` -- destroying a
            # grab-holding Toplevel auto-releases the grab, but Tk has no
            # grab *stack*, so parent doesn't get it back on its own; left
            # ungrabbed it's still visible/responsive (see the transient
            # note above for what actually caused the "frozen" report),
            # just no longer exclusive -- this restores that.
            if parent is not self and parent.winfo_exists():
                parent.grab_set()

        ttk.Label(
            win,
            text="Create a via cell -- a square or round pad drawn on both "
                 "layers, optionally with a dielectric via window -- and "
                 "save it into an attached library.",
            wraplength=380, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        form = ttk.Frame(win)
        form.pack(padx=12, fill="x")

        def layer_choices():
            return [f"{n} ({self.session.library.layers[n].name})"
                    for n in range(len(self.session.library.layers))]

        row = 0
        ttk.Label(form, text="Shape:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        shape_var = tk.StringVar(value="square")
        shape_row = ttk.Frame(form)
        shape_row.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        ttk.Radiobutton(shape_row, text="Square", value="square", variable=shape_var).pack(side="left")
        ttk.Radiobutton(shape_row, text="Round", value="round", variable=shape_var).pack(side="left", padx=(8, 0))
        row += 1

        # start from the design's one via definition (Autoroute signal via,
        # Dielectric tab layer + window margin) so a cell built here matches
        # the routed vias
        try:
            _spec = self.session.via_spec(False)
            _rr = load_route_rules(self.session.library)
        except Exception:
            _spec, _rr = {"pad": 100.0, "window": 0.0, "layer": -1}, None
        ttk.Label(form, text="Size (mm):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        size_var = tk.DoubleVar(value=dbu_to_mm(_spec["pad"]))
        ttk.Spinbox(form, from_=0.001, to=100.0, increment=0.001, width=10, textvariable=size_var).grid(
            row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        choices = layer_choices()

        def _choice(n, fallback):
            return choices[n] if isinstance(n, int) and 0 <= n < len(choices) else fallback
        ttk.Label(form, text="Layer 1:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        layer1_var = tk.StringVar(value=_choice(_rr and _rr.get("layer_a"), choices[0]))
        ttk.Combobox(form, textvariable=layer1_var, width=20, state="readonly",
                     values=choices).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Layer 2:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        layer2_var = tk.StringVar(value=_choice(_rr and _rr.get("layer_b"),
                                                choices[1] if len(choices) > 1 else choices[0]))
        ttk.Combobox(form, textvariable=layer2_var, width=20, state="readonly",
                     values=choices).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        # Cell name -- pre-filled with the default (VIA_Layer<n1>_to_
        # Layer<n2>), editable to give the via a different name. Keeps
        # tracking the default as Layer 1/2 change, but only until the
        # user actually types something of their own -- _name_edited
        # below flips permanently on the first manual edit, same
        # "auto-fill until the user overrides it" pattern used for
        # generated names elsewhere in this file (e.g. the resistor
        # designer's ref-des-derived cell name).
        def default_via_name():
            n1 = int(layer1_var.get().split(" ", 1)[0])
            n2 = int(layer2_var.get().split(" ", 1)[0])
            return f"VIA_Layer{n1}_to_Layer{n2}"

        ttk.Label(form, text="Cell name:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        name_var = tk.StringVar(value=default_via_name())
        name_entry = ttk.Entry(form, textvariable=name_var, width=22)
        name_entry.grid(row=row, column=1, sticky="w", padx=4, pady=4)

        _name_edited = [False]

        def _on_name_key(event=None):
            _name_edited[0] = True

        def _on_layer_choice_change(*_):
            if not _name_edited[0]:
                name_var.set(default_via_name())

        name_entry.bind("<Key>", _on_name_key)
        layer1_combo = form.grid_slaves(row=2, column=1)[0]
        layer2_combo = form.grid_slaves(row=3, column=1)[0]
        layer1_combo.bind("<<ComboboxSelected>>", _on_layer_choice_change)
        layer2_combo.bind("<<ComboboxSelected>>", _on_layer_choice_change)
        row += 1

        lib_choices = [f"[{l['index']}] {l['name']} ({l['path']})"
                       for l in self.session.list_attached_libraries()]
        ttk.Label(form, text="Save to library:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        lib_var = tk.StringVar(value=lib_choices[0])
        lib_combo = ttk.Combobox(form, textvariable=lib_var, width=28, state="readonly",
                                  values=lib_choices)
        lib_combo.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        if len(lib_choices) == 1:
            lib_combo.configure(state="disabled")
        row += 1

        ttk.Separator(form, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", padx=4, pady=(8, 4))
        row += 1

        dielectric_var = tk.BooleanVar(value=_spec["layer"] >= 0)
        dielectric_check = ttk.Checkbutton(form, text="Add dielectric via window",
                                            variable=dielectric_var)
        dielectric_check.grid(row=row, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Dielectric layer:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        dielectric_layer_var = tk.StringVar(
            value=_choice(_spec["layer"], choices[2] if len(choices) > 2 else choices[0]))
        dielectric_layer_combo = ttk.Combobox(form, textvariable=dielectric_layer_var, width=20,
                                               state="readonly" if _spec["layer"] >= 0 else "disabled",
                                               values=choices)
        dielectric_layer_combo.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Dielectric window size (mm):").grid(
            row=row, column=0, sticky="e", padx=4, pady=4)
        _margin = (_spec["window"] - _spec["pad"]) / 2.0 if _spec["window"] else 20.0
        dielectric_window_var = tk.DoubleVar(
            value=dbu_to_mm(_spec["window"] if _spec["window"] else 140))
        dielectric_window_spin = ttk.Spinbox(form, from_=0.001, to=100.0, increment=0.001,
                                              width=10, textvariable=dielectric_window_var,
                                              state="normal" if _spec["layer"] >= 0 else "disabled")

        def _follow_size(*_):
            # the window tracks the pad: pad + 2 x the design's margin
            try:
                dielectric_window_var.set(round(float(size_var.get()) + 2 * dbu_to_mm(_margin), 4))
            except (tk.TclError, ValueError):
                pass
        size_var.trace_add("write", _follow_size)
        dielectric_window_spin.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1
        ttk.Label(
            form,
            text="Opening cut in the dielectric between Layer 1 and Layer 2,\n"
                 "larger than the pad: pad + 2 x the via window margin of\n"
                 "Setup > Technology Properties, Dielectric -- the same rule\n"
                 "every routed via uses.",
            foreground="#555", justify="left",
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=4)
        row += 1

        def _on_dielectric_toggle():
            state = "readonly" if dielectric_var.get() else "disabled"
            dielectric_layer_combo.configure(state=state)
            dielectric_window_spin.configure(state="normal" if dielectric_var.get() else "disabled")

        dielectric_check.configure(command=_on_dielectric_toggle)

        error_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=error_var, foreground="red", wraplength=380,
                  justify="left").pack(anchor="w", padx=12)

        def create_and_close():
            n1 = int(layer1_var.get().split(" ", 1)[0])
            n2 = int(layer2_var.get().split(" ", 1)[0])
            index = int(lib_var.get().split("]", 1)[0].lstrip("["))
            try:
                size = mm_to_dbu(float(size_var.get()))
            except (tk.TclError, ValueError):
                error_var.set("Size must be a number.")
                return
            if size <= 0:
                error_var.set("Size must be a positive number.")
                return

            name = name_var.get().strip()
            if not name:
                error_var.set("Cell name can't be blank.")
                return

            dielectric_layer = None
            dielectric_window = None
            if dielectric_var.get():
                n3 = int(dielectric_layer_var.get().split(" ", 1)[0])
                if n3 in (n1, n2):
                    error_var.set("Dielectric layer must be different from Layer 1 and Layer 2.")
                    return
                try:
                    dielectric_window = mm_to_dbu(float(dielectric_window_var.get()))
                except (tk.TclError, ValueError):
                    error_var.set("Dielectric window size must be a number.")
                    return
                if dielectric_window <= 0:
                    error_var.set("Dielectric window size must be positive.")
                    return
                dielectric_layer = n3

            def do_create(overwrite):
                return self.session.create_via_cell(
                    n1, n2, shape=shape_var.get(), size=size, index=index, overwrite=overwrite,
                    name=name, dielectric_layer=dielectric_layer, dielectric_window=dielectric_window,
                )

            try:
                created_name, path = do_create(overwrite=False)
            except LayoutError as e:
                if "already exists" in str(e):
                    if dialogs.ask_yes_no(
                        "Via Wizard",
                        f"A via cell named {name!r} already "
                        "exists in that library. Replace it?",
                        parent=win,
                    ):
                        try:
                            created_name, path = do_create(overwrite=True)
                        except LayoutError as e2:
                            error_var.set(str(e2))
                            return
                    else:
                        return
                else:
                    error_var.set(str(e))
                    return
            self.refresh_all()
            self.status.set(f"created via cell {created_name!r}, saved to {path}")
            if on_created is not None:
                on_created(created_name)
            _close()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(6, 12))
        ttk.Button(btn_row, text="Create", command=create_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=_close).pack(side="left", padx=6)

        win.bind("<Escape>", lambda e: _close())
        win.protocol("WM_DELETE_WINDOW", _close)

    def show_pad_wizard(self):
        """Create > Pad Wizard...: create a rectangular pad cell -- a
        conductor rectangle sized by length x width, plus a solder
        paste rectangle centred on it and undersized by a margin on
        every side -- and save it directly into an attached reference
        library's own file, so it becomes a reusable part. Optionally
        lays out a centred rows x cols grid of identical pads (e.g. a
        connector footprint) instead of just one. Backed by
        Session.create_pad_cell(); same operation as the command bar's
        PAD <name> <conductor_layer> <paste_layer> ... command."""
        if not self.session.attached_libraries:
            dialogs.show_info(
                "Pad Wizard",
                "No library is attached yet. Use Setup > Attach Library... to "
                "attach the GDSII file the pad should be saved into, then try "
                "the Pad Wizard again.",
                parent=self,
            )
            return

        win = tk.Toplevel(self)
        win.title("Pad Wizard")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(
            win,
            text="Create a pad cell -- a conductor rectangle with a solder "
                 "paste rectangle undersized on every side -- and save it "
                 "into an attached library. Set rows/cols > 1 for a grid of "
                 "identical pads (e.g. a connector footprint).",
            wraplength=380, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        form = ttk.Frame(win)
        form.pack(padx=12, fill="x")

        def layer_choices():
            return [f"{n} ({self.session.library.layers[n].name})"
                    for n in range(len(self.session.library.layers))]

        row = 0
        ttk.Label(form, text="Cell name:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        name_var = tk.StringVar(value="")
        ttk.Entry(form, textvariable=name_var, width=22).grid(
            row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Length (mm, X):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        length_var = tk.DoubleVar(value=dbu_to_mm(mm_to_dbu(1.0)))
        ttk.Spinbox(form, from_=0.001, to=500.0, increment=0.001, width=10, textvariable=length_var).grid(
            row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Width (mm, Y):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        width_var = tk.DoubleVar(value=dbu_to_mm(mm_to_dbu(1.0)))
        ttk.Spinbox(form, from_=0.001, to=500.0, increment=0.001, width=10, textvariable=width_var).grid(
            row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Paste undersize (mm/side):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        margin_var = tk.DoubleVar(value=dbu_to_mm(mm_to_dbu(0.05)))
        ttk.Spinbox(form, from_=0.0, to=250.0, increment=0.001, width=10, textvariable=margin_var).grid(
            row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        choices = layer_choices()
        ttk.Label(form, text="Conductor layer:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        conductor_var = tk.StringVar(value=choices[0])
        ttk.Combobox(form, textvariable=conductor_var, width=20, state="readonly",
                     values=choices).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Solder paste layer:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        paste_var = tk.StringVar(value=choices[1] if len(choices) > 1 else choices[0])
        ttk.Combobox(form, textvariable=paste_var, width=20, state="readonly",
                     values=choices).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Rows x Cols:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        grid_row = ttk.Frame(form)
        grid_row.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        rows_var = tk.IntVar(value=1)
        cols_var = tk.IntVar(value=1)
        ttk.Spinbox(grid_row, from_=1, to=100, width=5, textvariable=rows_var).pack(side="left")
        ttk.Label(grid_row, text=" x ").pack(side="left")
        ttk.Spinbox(grid_row, from_=1, to=100, width=5, textvariable=cols_var).pack(side="left")
        row += 1

        ttk.Label(form, text="Pitch Y / X (mm):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        pitch_row = ttk.Frame(form)
        pitch_row.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        pitch_y_var = tk.DoubleVar(value=0.0)
        pitch_x_var = tk.DoubleVar(value=0.0)
        ttk.Spinbox(pitch_row, from_=0.0, to=500.0, increment=0.001, width=8, textvariable=pitch_y_var).pack(side="left")
        ttk.Label(pitch_row, text=" x ").pack(side="left")
        ttk.Spinbox(pitch_row, from_=0.0, to=500.0, increment=0.001, width=8, textvariable=pitch_x_var).pack(side="left")
        row += 1
        ttk.Label(
            form, text="(pitch only needed when rows/cols > 1)",
            foreground="gray",
        ).grid(row=row, column=1, sticky="w", padx=4)
        row += 1

        lib_choices = [f"[{l['index']}] {l['name']} ({l['path']})"
                       for l in self.session.list_attached_libraries()]
        ttk.Label(form, text="Save to library:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        lib_var = tk.StringVar(value=lib_choices[0])
        lib_combo = ttk.Combobox(form, textvariable=lib_var, width=28, state="readonly",
                                  values=lib_choices)
        lib_combo.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        if len(lib_choices) == 1:
            lib_combo.configure(state="disabled")
        row += 1

        error_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=error_var, foreground="red", wraplength=380,
                  justify="left").pack(anchor="w", padx=12)

        def create_and_close():
            pad_name = name_var.get().strip()
            if not pad_name:
                error_var.set("Cell name is required.")
                return
            n_cond = int(conductor_var.get().split(" ", 1)[0])
            n_paste = int(paste_var.get().split(" ", 1)[0])
            index = int(lib_var.get().split("]", 1)[0].lstrip("["))
            try:
                length = mm_to_dbu(float(length_var.get()))
                width = mm_to_dbu(float(width_var.get()))
                margin = mm_to_dbu(float(margin_var.get()))
            except (tk.TclError, ValueError):
                error_var.set("Length, width, and paste undersize must be numbers.")
                return
            rows = rows_var.get()
            cols = cols_var.get()
            pitch_y = mm_to_dbu(float(pitch_y_var.get())) if pitch_y_var.get() else None
            pitch_x = mm_to_dbu(float(pitch_x_var.get())) if pitch_x_var.get() else None

            def do_create(overwrite):
                return self.session.create_pad_cell(
                    pad_name, n_cond, n_paste, length, width, margin,
                    rows=rows, cols=cols, pitch_x=pitch_x, pitch_y=pitch_y,
                    index=index, overwrite=overwrite,
                )

            try:
                name, path, pad_count = do_create(overwrite=False)
            except LayoutError as e:
                if "already exists" in str(e):
                    if dialogs.ask_yes_no(
                        "Pad Wizard",
                        f"A cell named {pad_name!r} already exists in that "
                        "library. Replace it?",
                        parent=self,
                    ):
                        try:
                            name, path, pad_count = do_create(overwrite=True)
                        except LayoutError as e2:
                            error_var.set(str(e2))
                            return
                    else:
                        return
                else:
                    error_var.set(str(e))
                    return
            self.refresh_all()
            self.status.set(f"created pad cell {name!r} ({pad_count} pad(s)), saved to {path}")
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(6, 12))
        ttk.Button(btn_row, text="Create", command=create_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        win.bind("<Escape>", lambda e: win.destroy())

    def _chip_pin_cell_source(self, cell_name):
        """Where a die-pin cell lives: the current design, else the first
        attached library that has it, else None."""
        if cell_name in self.session.library.cells:
            return "current"
        for i, lib in enumerate(self.session.attached_libraries):
            if cell_name in lib.cells:
                return i
        return None

    def _placeable_pin_cell_names(self):
        """Every cell name available to place as a die pin: the current
        design's cells plus every cell in every attached library."""
        names = set(self.session.library.cells)
        for lib in self.session.attached_libraries:
            names |= set(lib.cells)
        return sorted(names)

    def show_chip_wizard(self):
        """Create > Chip Wizard...: build a bare-die / chip footprint
        interactively. Enter the body size, the outline (chip-graphics)
        layer, the epoxy die-attach pad size + layer, the pin count, and
        which cells to use for pin 1 and for the rest (picked from the
        design / attached libraries). The wizard drops a white body
        outline and the epoxy pad, then you click once per die pin to
        place an instance of the chosen cell. When the last pin is
        placed, if "Save to attached library" is ticked the finished
        cell is copied into the first attached library (under the given
        name) and its .gds saved; otherwise it's just left in the
        design. Backed by Session.create_chip_cell() +
        Session.export_cell_to_library()."""
        if not self.session.attached_libraries:
            dialogs.show_info(
                "Chip Wizard",
                "No library is attached yet. Use Setup > Attach Library... to "
                "attach the parts library your die-pin cells live in (and the "
                "file the finished chip is saved into), then try the Chip "
                "Wizard again.",
                parent=self,
            )
            return

        pin_cells = self._placeable_pin_cell_names()
        if not pin_cells:
            dialogs.show_info(
                "Chip Wizard",
                "No cells are available to place as die pins. Add your die-pad "
                "cell(s) to the attached parts library, then try again.",
                parent=self,
            )
            return

        def _default(*prefs):
            for p in prefs:
                if p in pin_cells:
                    return p
            return pin_cells[0]

        win = tk.Toplevel(self)
        win.title("Chip Wizard")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        libs = self.session.list_attached_libraries()
        ttk.Label(
            win,
            text="Build a bare-die chip footprint. Enter the body size, outline "
                 "layer, epoxy die-attach pad size + layer, pin count and the "
                 "die-pad cells to use; then click once per die pin to place an "
                 "instance. If \"Save to attached library\" stays ticked, the "
                 f"finished cell is written into {libs[0]['name']!r} "
                 f"({libs[0]['path']}) when the last pin is placed.",
            wraplength=400, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        form = ttk.Frame(win)
        form.pack(padx=12, fill="x")

        def layer_choices():
            return [f"{n} ({self.session.library.layers[n].name})"
                    for n in range(len(self.session.library.layers))]
        choices = layer_choices()

        row = 0
        ttk.Label(form, text="Cell name:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        name_var = tk.StringVar(value="")
        ttk.Entry(form, textvariable=name_var, width=22).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Chip length (mm, X):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        length_var = tk.DoubleVar(value=dbu_to_mm(mm_to_dbu(2.0)))
        ttk.Spinbox(form, from_=0.001, to=500.0, increment=0.001, width=10,
                    textvariable=length_var).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Chip width (mm, Y):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        width_var = tk.DoubleVar(value=dbu_to_mm(mm_to_dbu(2.0)))
        ttk.Spinbox(form, from_=0.001, to=500.0, increment=0.001, width=10,
                    textvariable=width_var).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Chip graphics layer (outline):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        gfx_layer_var = tk.StringVar(value=choices[1] if len(choices) > 1 else choices[0])
        ttk.Combobox(form, textvariable=gfx_layer_var, width=20, state="readonly",
                     values=choices).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Epoxy pad length (mm, X):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        epoxy_l_var = tk.DoubleVar(value=dbu_to_mm(mm_to_dbu(1.8)))
        ttk.Spinbox(form, from_=0.001, to=100.0, increment=0.001, width=10,
                    textvariable=epoxy_l_var).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Epoxy pad width (mm, Y):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        epoxy_w_var = tk.DoubleVar(value=dbu_to_mm(mm_to_dbu(1.8)))
        ttk.Spinbox(form, from_=0.001, to=100.0, increment=0.001, width=10,
                    textvariable=epoxy_w_var).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Epoxy pad layer:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        epoxy_layer_var = tk.StringVar(value=choices[0])
        ttk.Combobox(form, textvariable=epoxy_layer_var, width=20, state="readonly",
                     values=choices).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        # conductor die-attach pad under the chip: on by default, on the 2nd
        # conductor (Setup > Route Layers layer_b), 0.2 mm larger all round
        try:
            from ..io.techfile import load_route_rules
            _lb = load_route_rules(self.session.library).get("layer_b")
        except Exception:
            _lb = None
        _lb_ok = isinstance(_lb, int) and 0 <= _lb < len(choices)
        attach_var = tk.BooleanVar(value=_lb_ok)
        ttk.Checkbutton(form, text="Conductor die-attach pad under the chip",
                        variable=attach_var).grid(row=row, column=0, columnspan=2,
                                                  sticky="w", padx=4, pady=(8, 2))
        row += 1
        ttk.Label(form, text="Die-attach pad layer:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        attach_layer_var = tk.StringVar(value=choices[_lb] if _lb_ok else choices[0])
        ttk.Combobox(form, textvariable=attach_layer_var, width=20, state="readonly",
                     values=choices).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1
        ttk.Label(form, text="Pad larger than chip by (mm, each side):").grid(
            row=row, column=0, sticky="e", padx=4, pady=4)
        attach_margin_var = tk.DoubleVar(value=0.2)
        ttk.Spinbox(form, from_=0.0, to=10.0, increment=0.05, width=10,
                    textvariable=attach_margin_var).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1
        # back contact: the die-attach pad is itself a pin (e.g. a bipolar
        # transistor's collector), with a tab the router can reach past
        # the die keep-out
        back_var = tk.BooleanVar(value=False)
        back_row = ttk.Frame(form)
        ttk.Checkbutton(form, text="Back of die is a contact: attach pad is pin",
                        variable=back_var).grid(row=row, column=0, sticky="w", padx=4, pady=2)
        back_name_var = tk.StringVar(value="C")
        ttk.Entry(back_row, textvariable=back_name_var, width=6).pack(side="left")
        back_row.grid(row=row, column=1, sticky="w", padx=4, pady=2)
        row += 1
        ttk.Label(form, text="Tab for the track (mm, side):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        tab_row = ttk.Frame(form)
        tab_var = tk.DoubleVar(value=0.6)
        ttk.Spinbox(tab_row, from_=0.0, to=20.0, increment=0.1, width=6,
                    textvariable=tab_var).pack(side="left")
        tab_side_var = tk.StringVar(value="bottom")
        ttk.Combobox(tab_row, textvariable=tab_side_var, width=8, state="readonly",
                     values=("bottom", "top", "left", "right")).pack(side="left", padx=(6, 0))
        tab_row.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Pin 1 cell:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        pin1_cell_var = tk.StringVar(value=_default("chip_pin1", "chip_pin_1"))
        ttk.Combobox(form, textvariable=pin1_cell_var, width=20, state="readonly",
                     values=pin_cells).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Pins 2..N cell:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        pin_cell_var = tk.StringVar(value=_default("chip_pin"))
        ttk.Combobox(form, textvariable=pin_cell_var, width=20, state="readonly",
                     values=pin_cells).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Number of pins:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        pins_var = tk.IntVar(value=4)
        ttk.Spinbox(form, from_=1, to=1000, width=10,
                    textvariable=pins_var).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1
        ttk.Label(form, text="Pin names (optional):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        pin_names_var = tk.StringVar(value="")
        ttk.Entry(form, textvariable=pin_names_var, width=22).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1
        ttk.Label(form, foreground="#8b949e",
                  text="Blank: pins are numbered 1..N. Or list them in click order,\n"
                       "e.g. E,B -- they must match the pin numbers in the netlist.").grid(
            row=row, column=0, columnspan=2, sticky="w", padx=4, pady=(0, 4))
        row += 1

        save_var = tk.BooleanVar(value=True)
        lib_name_var = tk.StringVar(value="")
        lib_name_entry = ttk.Entry(form, textvariable=lib_name_var, width=22)

        def _toggle_save(*_):
            lib_name_entry.configure(state="normal" if save_var.get() else "disabled")
        save_chk = ttk.Checkbutton(
            form, text="Save to attached library when finished",
            variable=save_var, command=_toggle_save)
        save_chk.grid(row=row, column=0, columnspan=2, sticky="w", padx=4, pady=(8, 2))
        row += 1
        ttk.Label(form, text="Save as (library cell name):").grid(
            row=row, column=0, sticky="e", padx=4, pady=4)
        lib_name_entry.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        # mirror the cell name into the library-name field until the user
        # edits it themselves
        _prev = {"name": name_var.get()}

        def _mirror_name(*_):
            if lib_name_var.get() in ("", _prev["name"]):
                lib_name_var.set(name_var.get())
            _prev["name"] = name_var.get()
        name_var.trace_add("write", _mirror_name)
        _toggle_save()

        error_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=error_var, foreground="red", wraplength=400,
                  justify="left").pack(anchor="w", padx=12)

        def start_and_close():
            name = name_var.get().strip()
            if not name:
                error_var.set("Cell name is required.")
                return
            try:
                length = mm_to_dbu(float(length_var.get()))
                width = mm_to_dbu(float(width_var.get()))
                epoxy_l = mm_to_dbu(float(epoxy_l_var.get()))
                epoxy_w = mm_to_dbu(float(epoxy_w_var.get()))
                a_margin = mm_to_dbu(float(attach_margin_var.get()))
            except (tk.TclError, ValueError):
                error_var.set("Chip and epoxy-pad length/width must be numbers.")
                return
            pin_names = chip_pin_names(pin_names_var.get())
            if pin_names:
                pins_var.set(len(pin_names))
            n_pins = int(pins_var.get())
            if n_pins < 1:
                error_var.set("Number of pins must be at least 1.")
                return
            if len(set(pin_names)) != len(pin_names):
                error_var.set("Pin names must all be different.")
                return
            back_pin = back_name_var.get().strip() if back_var.get() else ""
            if back_var.get() and not attach_var.get():
                error_var.set("A back contact needs the conductor die-attach pad ticked.")
                return
            if back_var.get() and not back_pin:
                error_var.set("Give the back-contact pin a name, e.g. C.")
                return
            if back_pin and back_pin in pin_names:
                error_var.set(f"Pin {back_pin!r} is both the back contact and a top pin.")
                return
            try:
                tab = mm_to_dbu(float(tab_var.get())) if back_pin else 0
            except (tk.TclError, ValueError):
                error_var.set("Tab length must be a number.")
                return
            g_layer = int(gfx_layer_var.get().split(" ", 1)[0])
            e_layer = int(epoxy_layer_var.get().split(" ", 1)[0])
            a_layer = int(attach_layer_var.get().split(" ", 1)[0]) if attach_var.get() else None
            pin1_cell = pin1_cell_var.get()
            pin_cell = pin_cell_var.get()
            if self._chip_pin_cell_source(pin1_cell) is None:
                error_var.set(f"Pin 1 cell {pin1_cell!r} isn't available.")
                return
            if self._chip_pin_cell_source(pin_cell) is None:
                error_var.set(f"Pins 2..N cell {pin_cell!r} isn't available.")
                return

            def do_create(overwrite):
                return self.session.create_chip_cell(
                    name, length, width, g_layer, epoxy_l, epoxy_w, e_layer,
                    overwrite=overwrite, attach_pad_layer=a_layer,
                    attach_pad_margin=a_margin, attach_pad_pin=back_pin,
                    attach_tab=tab, attach_tab_side=tab_side_var.get())

            try:
                do_create(overwrite=False)
            except LayoutError as e:
                if "already exists" in str(e):
                    if not dialogs.ask_yes_no(
                        "Chip Wizard",
                        f"A cell named {name!r} already exists in this design. "
                        "Replace it and start over?",
                        parent=self,
                    ):
                        return
                    try:
                        do_create(overwrite=True)
                    except LayoutError as e2:
                        error_var.set(str(e2))
                        return
                else:
                    error_var.set(str(e))
                    return

            self._chip_wizard = {
                "name": name, "total": n_pins, "placed": 0,
                "prev_grid": int(self.canvas.grid_dbu),
                "pin1_cell": pin1_cell, "pin_cell": pin_cell,
                "save_to_lib": bool(save_var.get()),
                "pin_names": pin_names,
                "lib_name": lib_name_var.get().strip() or name,
            }
            self.session.use_cell(name)
            self.canvas.set_cell(name)
            self.cell_var.set(name)
            # pin placement snaps to a 0.1 mm grid
            self.canvas.set_grid_spacing(mm_to_dbu(0.1))
            self._refresh_grid_spacing_var()
            self.canvas.fit_view()
            self.refresh_all()
            self.canvas.start_chip_pins(n_pins, pin_names)
            first = pin_names[0] if pin_names else "1"
            self.status.set(f"Chip Wizard: click where pin {first} of {n_pins} goes "
                            f"(grid 0.1 mm)")
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(6, 12))
        ttk.Button(btn_row, text="Start Placing Pins", command=start_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)
        win.bind("<Escape>", lambda e: win.destroy())

    def _on_chip_pin(self, point, pin_no):
        """canvas.on_chip_pin: place a die-pin instance for the chip
        being built, centred on the clicked point -- pin 1 places the
        wizard's chosen pin-1 cell, every other pin its chosen pin
        cell (from the design or an attached library). The instance's
        Label carries the pin number, and its layers are switched on so
        it's visible."""
        w = self._chip_wizard
        if w is None:
            return
        cell_name = w["pin1_cell"] if pin_no == 1 else w["pin_cell"]
        names = w.get("pin_names") or []
        pin = names[pin_no - 1] if pin_no <= len(names) else str(pin_no)
        try:
            self.session.use_cell(w["name"])
            # pin= makes this instance the part's pin (SRef.pin) for netlist
            # extraction, Auto Place and routing; label= shows it on screen
            self.session.place_cell(cell_name, point, label=pin, pin=pin)
            self.session.show_layers_of_cell(cell_name)
        except LayoutError as e:
            dialogs.show_error("Chip Wizard", str(e), parent=self)
            return
        w["placed"] = pin_no
        self.refresh_all()
        nxt = pin_no + 1
        if nxt <= w["total"]:
            nxt_name = names[nxt - 1] if nxt <= len(names) else str(nxt)
            self.status.set(f"Chip Wizard: placed pin {pin} -- click where pin "
                            f"{nxt_name} of {w['total']} goes")

    def _restore_chip_grid(self, w):
        prev = (w or {}).get("prev_grid")
        if prev:
            try:
                self.canvas.set_grid_spacing(prev)
                self._refresh_grid_spacing_var()
            except Exception:
                pass

    def _on_chip_pins_done(self):
        """canvas.on_chip_pins_done: the last pin is placed. If the
        wizard's "Save to attached library" box was ticked, copy the
        finished chip cell into the first attached library (under the
        library name given) and save it; otherwise just leave it in the
        design."""
        w = self._chip_wizard
        if w is None:
            return
        self._restore_chip_grid(w)
        name = w["name"]

        if not w.get("save_to_lib"):
            self._chip_wizard = None
            self.refresh_all()
            self.status.set(
                f"Chip Wizard: {name!r} ({w['total']} pins) built in the design "
                f"-- not saved to a library"
            )
            return

        # rename the design cell if the library name differs (the cell
        # isn't referenced by anything yet, so this is a plain re-key)
        lib_target = w.get("lib_name") or name
        if lib_target != name and lib_target not in self.session.library.cells:
            cells = self.session.library.cells
            c = cells.pop(name)
            c.name = lib_target
            cells[lib_target] = c
            if self.session.current_cell_name == name:
                self.session.current_cell_name = lib_target
            self.canvas.set_cell(lib_target)
            self.cell_var.set(lib_target)
            name = lib_target

        try:
            lib_name, path = self.session.export_cell_to_library(name, index=0, overwrite=False)
        except LayoutError as e:
            if "already exists" in str(e) and dialogs.ask_yes_no(
                "Chip Wizard",
                f"A cell named {name!r} already exists in the attached library. "
                "Replace it?",
                parent=self,
            ):
                try:
                    lib_name, path = self.session.export_cell_to_library(
                        name, index=0, overwrite=True)
                except LayoutError as e2:
                    dialogs.show_error("Chip Wizard", str(e2), parent=self)
                    self._chip_wizard = None
                    return
            else:
                dialogs.show_error("Chip Wizard", str(e), parent=self)
                self._chip_wizard = None
                return
        self._chip_wizard = None
        self.refresh_all()
        self.status.set(f"Chip Wizard: {name!r} ({w['total']} pins) saved to {path}")

    def _on_chip_pins_cancel(self):
        """canvas.on_chip_pins_cancel: Esc bailed out mid-placement."""
        w = self._chip_wizard
        self._chip_wizard = None
        self._restore_chip_grid(w)
        if w is not None:
            self.status.set(
                f"Chip Wizard cancelled -- {w['name']!r} left in the design "
                f"({w['placed']}/{w['total']} pins placed), not exported"
            )
