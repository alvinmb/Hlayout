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
    def _set_visible_checkbox(state, value: bool):
        """Set a row's visibility BooleanVar AND explicitly force the
        actual Checkbutton widget's ttk selected/!selected state to
        match. Normally a ttk.Checkbutton repaints its indicator purely
        from a trace on its -variable, but that trace can silently miss
        an update when the variable is set programmatically (as All On/
        All Off and sync() below both do, rather than a real click) --
        the box then shows only its focus outline, unchecked, no matter
        what the variable/model actually holds ("switch all layers off,
        then can't switch one back on" -- the checkbox LOOKED stuck off
        even though clicking it did flip the underlying layer). Calling
        .state() directly bypasses the trace entirely, so the indicator
        can never visually diverge from the model."""
        state["visible_var"].set(value)
        state["visible_cb"].state(["selected"] if value else ["!selected"])

    def set_all_visible(self, value: bool):
        """Turn every layer's visibility on (True) or off (False) at
        once — the "All On"/"All Off" buttons above the layer table."""
        for n in range(NUM_LAYERS):
            self.library.layers[n].visible = value
        for state in self._row_state.values():
            self._set_visible_checkbox(state, value)
        self.on_change()

    def sync(self):
        """Refresh displayed values from the library without rebuilding
        widgets (so external changes — command bar, scripting — show up
        without losing scroll position or in-progress edits elsewhere)."""
        for n, state in self._row_state.items():
            layer = self.library.layers[n]
            if not state["name_entry"].winfo_ismapped() or not state["name_focused"][0]:
                state["name_var"].set(layer.name)
            self._set_visible_checkbox(state, layer.visible)
            state["swatch"].configure(bg=layer.color)
            state["fill_var"].set(layer.fill)
            state["stipple_var"].set(STIPPLE_NAMES[layer.stipple])
            state["stipple_combo"].configure(state="readonly" if layer.fill else "disabled")

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
        layer = self.library.layers[n]
        row = ttk.Frame(self._inner)
        row.pack(fill="x")

        visible_var = tk.BooleanVar(value=layer.visible)

        def toggle_visible():
            layer.visible = visible_var.get()
            self.on_change()

        visible_cb = ttk.Checkbutton(row, variable=visible_var, command=toggle_visible, width=3)
        visible_cb.pack(side="left")
        ttk.Label(row, text=str(n), width=3).pack(side="left")

        name_var = tk.StringVar(value=layer.name)
        name_focused = [False]

        def commit_name(event=None):
            layer.name = name_var.get() or layer.name
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
            rgb, hexcolor = colorchooser.askcolor(color=layer.color, title=f"Layer {n} colour")
            if hexcolor:
                layer.color = hexcolor
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
            layer.fill = fill_var.get()
            _update_stipple_enabled()
            self.on_change()

        ttk.Checkbutton(row, variable=fill_var, command=toggle_fill, width=4).pack(side="left")

        combo.pack(side="left", padx=2)
        _update_stipple_enabled()

        def on_stipple(event=None):
            layer.stipple = STIPPLE_NAMES.index(stipple_var.get())
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
            "stipple_var": stipple_var,
            "stipple_combo": combo,
        }
