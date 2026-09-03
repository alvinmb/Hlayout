"""LayerPanel: scrollable list of all 64 layers with visibility,
colour swatch, and stipple selection."""

import tkinter as tk
from tkinter import colorchooser, ttk

from .fonts import section_header_font
from ..core.layers import NUM_LAYERS, STIPPLE_NAMES


class LayerPanel(ttk.Frame):
    def __init__(self, master, library, on_change=None):
        super().__init__(master)
        self.library = library
        self.on_change = on_change or (lambda: None)
        self._rows = []
        self._row_state = {}  # layer number -> dict of widgets/vars for sync()
        self._syncing = False  # re-entrancy guard for sync()
        self._build()

    def _build(self):
        canvas = tk.Canvas(self, highlightthickness=0)
        scroll = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._inner = inner
        self._canvas = canvas
        self._populate()

    def set_library(self, library):
        self.library = library
        self._populate()

    @staticmethod
    def _force_checkbox(cb, var, value: bool):
        """Set a Checkbutton's BooleanVar AND explicitly force the
        widget's ttk selected/!selected state flag to match.

        A ttk.Checkbutton normally repaints its indicator (and decides
        which way a click flips it -- ttk toggles from `instate
        selected`, not from the variable) purely off a trace on its
        -variable. That trace can silently miss an update when the
        variable is set programmatically rather than by a real click --
        All On/All Off, sync(), undo -- leaving the box showing one
        thing while the model and variable say another ("switch all
        layers off, then can't switch one back on"; more recently the
        same on the Fill box, "can't switch off colours"). Forcing the
        state flag here keeps the indicator, the variable, and the
        model in lock-step for both the On and Fill columns."""
        var.set(value)
        cb.state(["selected"] if value else ["!selected"])

    def set_all_visible(self, value: bool):
        """Turn every layer's visibility on (True) or off (False) at
        once — the "All On"/"All Off" buttons above the layer table."""
        for n in range(NUM_LAYERS):
            self.library.layers[n].visible = value
        for state in self._row_state.values():
            self._force_checkbox(state["visible_cb"], state["visible_var"], value)
        self.on_change()

    def sync(self):
        """Refresh displayed values from the library without rebuilding
        widgets (so external changes — command bar, scripting, undo —
        show up without losing scroll position or in-progress edits
        elsewhere). Guarded against re-entrancy: a toggle made in the
        panel repaints the canvas via on_change, and if that path ever
        loops back here mid-click it would stomp the checkbox being
        clicked."""
        if self._syncing:
            return
        self._syncing = True
        try:
            for n, state in self._row_state.items():
                layer = self.library.layers[n]
                if not state["name_entry"].winfo_ismapped() or not state["name_focused"][0]:
                    state["name_var"].set(layer.name)
                self._force_checkbox(state["visible_cb"], state["visible_var"], layer.visible)
                state["swatch"].configure(bg=layer.color)
                self._force_checkbox(state["fill_cb"], state["fill_var"], layer.fill)
                state["stipple_var"].set(STIPPLE_NAMES[layer.stipple])
                state["stipple_combo"].configure(state="readonly" if layer.fill else "disabled")
        finally:
            self._syncing = False

    def _populate(self):
        for w in self._inner.winfo_children():
            w.destroy()
        self._rows = []
        self._row_state = {}

        toolbar = ttk.Frame(self._inner)
        toolbar.pack(fill="x", pady=(0, 4))
        ttk.Button(toolbar, text="All On", command=lambda: self.set_all_visible(True)).pack(
            side="left", padx=(0, 4))
        ttk.Button(toolbar, text="All Off", command=lambda: self.set_all_visible(False)).pack(
            side="left")

        header = ttk.Frame(self._inner)
        header.pack(fill="x")
        # Bold, but otherwise tracking whatever size TkDefaultFont is
        # currently set to (see App._apply_global_font_size and
        # hlayout.gui.fonts.section_header_font) rather than a
        # hardcoded point size that would drift out of sync with the
        # rest of the app's font size.
        # Widths/padding here must mirror each row's actual widgets
        # (Checkbutton On, Label #, Entry Name, swatch Color, Checkbutton
        # Fill, Combobox Stipple in _make_row below) exactly, or the
        # header labels drift out of alignment with the columns beneath
        # them -- e.g. "Color" used to claim more width than the swatch
        # actually takes, which visually shifted "Fill" to the right of
        # its own checkbox, making it easy to click Fill (which only
        # controls solid-vs-outline) while aiming for On (visibility).
        header_font = section_header_font(self)
        for text, w, padx in [("On", 3, 0), ("#", 3, 0), ("Name", 12, 0), ("Color", 3, 2),
                               ("Fill", 4, 0), ("Stipple", 13, 2)]:
            ttk.Label(header, text=text, width=w, font=header_font).pack(side="left", padx=padx)

        for n in range(NUM_LAYERS):
            self._make_row(n)

    def _make_row(self, n):
        # Every row handler re-fetches the Layer through self.library
        # rather than closing over the object captured here: undo/redo
        # (Session._restore) can rebuild library.layers, and a callback
        # bound to the old Layer would then silently write to a dead
        # object -- "after an undo, changing fill/colour/visibility does
        # nothing on the canvas". self.library itself keeps its identity,
        # so `self.library.layers[n]` is always the live layer.
        def L():
            return self.library.layers[n]

        layer = L()
        row = ttk.Frame(self._inner)
        row.pack(fill="x")

        visible_var = tk.BooleanVar(value=layer.visible)

        def toggle_visible():
            L().visible = visible_var.get()
            self.on_change()

        visible_cb = ttk.Checkbutton(row, variable=visible_var, command=toggle_visible, width=3)
        visible_cb.pack(side="left")
        ttk.Label(row, text=str(n), width=3).pack(side="left")

        name_var = tk.StringVar(value=layer.name)
        name_focused = [False]

        def commit_name(event=None):
            L().name = name_var.get() or L().name
            name_focused[0] = False
            self.on_change()

        name_entry = ttk.Entry(row, textvariable=name_var, width=12)
        name_entry.pack(side="left")
        name_entry.bind("<FocusIn>", lambda e: name_focused.__setitem__(0, True))
        name_entry.bind("<Return>", commit_name)
        name_entry.bind("<FocusOut>", commit_name)

        swatch = tk.Label(row, bg=layer.color, width=3, relief="sunken")
        swatch.pack(side="left", padx=2)

        def pick_color():
            rgb, hexcolor = colorchooser.askcolor(color=L().color, title=f"Layer {n} colour")
            if hexcolor:
                L().color = hexcolor
                swatch.configure(bg=hexcolor)
                self.on_change()

        swatch.bind("<Button-1>", lambda e: pick_color())

        fill_var = tk.BooleanVar(value=layer.fill)

        stipple_var = tk.StringVar(value=STIPPLE_NAMES[layer.stipple])
        combo = ttk.Combobox(row, textvariable=stipple_var, values=STIPPLE_NAMES,
                              width=13, state="readonly")

        def _update_stipple_enabled():
            combo.configure(state="readonly" if fill_var.get() else "disabled")

        def toggle_fill():
            L().fill = fill_var.get()
            _update_stipple_enabled()
            self.on_change()

        fill_cb = ttk.Checkbutton(row, variable=fill_var, command=toggle_fill, width=4)
        fill_cb.pack(side="left")

        combo.pack(side="left", padx=2)
        _update_stipple_enabled()

        def on_stipple(event=None):
            L().stipple = STIPPLE_NAMES.index(stipple_var.get())
            self.on_change()

        combo.bind("<<ComboboxSelected>>", on_stipple)

        self._rows.append(row)
        self._row_state[n] = {
            "name_var": name_var,
            "name_entry": name_entry,
            "name_focused": name_focused,
            "visible_var": visible_var,
            "visible_cb": visible_cb,
            "swatch": swatch,
            "fill_var": fill_var,
            "fill_cb": fill_cb,
            "stipple_var": stipple_var,
            "stipple_combo": combo,
        }
