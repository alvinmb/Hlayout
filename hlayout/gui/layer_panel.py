"""LayerPanel: scrollable list of all 64 layers with visibility,
colour swatch, and stipple selection."""

import tkinter as tk
from tkinter import colorchooser, ttk
from tkinter import font as tkfont

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
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        # Keep the table exactly as wide as the visible canvas, so the
        # rows can stretch their Name column into any spare width (and
        # never run off under the scrollbar).
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
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

        # The header and every layer row are separate frames, so their
        # columns are lined up by giving each frame the SAME per-column
        # pixel widths (see _column_widths) rather than character widths
        # -- character widths measure differently for a bold label, a
        # checkbox and an entry, which is what used to clip "Color" to
        # "Col" and push the Stipple box off the right edge of the panel.
        # Name is the one column that stretches to take any spare width.
        header_font = section_header_font(self)
        self._col_px = self._column_widths(header_font)
        header = ttk.Frame(self._inner)
        header.pack(fill="x", pady=(0, 2))
        self._configure_columns(header)
        for col, text in enumerate(("On", "#", "Name", "Color", "Fill", "Stipple")):
            ttk.Label(header, text=text, font=header_font).grid(
                row=0, column=col, sticky="w", padx=self.COL_PADX)
        ttk.Separator(self._inner, orient="horizontal").pack(fill="x", pady=(0, 2))

        for n in range(NUM_LAYERS):
            self._make_row(n)

    COL_PADX = 3
    NAME_COL = 2

    def _column_widths(self, header_font):
        """Pixel width of each column (On, #, Name, Color, Fill,
        Stipple), derived from the current fonts so they still fit when
        App.UI_FONT_SIZE changes."""
        body = tkfont.nametofont("TkDefaultFont", self)
        head = tkfont.Font(self, font=header_font)
        h = head.measure
        digit = body.measure("0")
        longest_stipple = max(body.measure(name) for name in STIPPLE_NAMES)
        return [
            max(h("On"), 22) + 4,              # checkbox
            max(h("#"), body.measure("00")) + 6,
            max(h("Name"), digit * 8),         # stretches (see NAME_COL)
            h("Color") + 4,
            max(h("Fill"), 22) + 4,            # checkbox
            longest_stipple + 34,              # text + the drop-down arrow
        ]

    def _configure_columns(self, frame):
        for col, px in enumerate(self._col_px):
            frame.grid_columnconfigure(col, minsize=px + 2 * self.COL_PADX,
                                       weight=1 if col == self.NAME_COL else 0)

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
        row.pack(fill="x", pady=1)
        self._configure_columns(row)
        padx = self.COL_PADX

        visible_var = tk.BooleanVar(value=layer.visible)

        def toggle_visible():
            L().visible = visible_var.get()
            self.on_change()

        visible_cb = ttk.Checkbutton(row, variable=visible_var, command=toggle_visible)
        visible_cb.grid(row=0, column=0, sticky="w", padx=padx)
        ttk.Label(row, text=str(n), foreground="#555").grid(row=0, column=1, sticky="w", padx=padx)

        name_var = tk.StringVar(value=layer.name)
        name_focused = [False]

        def commit_name(event=None):
            L().name = name_var.get() or L().name
            name_focused[0] = False
            self.on_change()

        name_entry = ttk.Entry(row, textvariable=name_var, width=1)
        name_entry.grid(row=0, column=2, sticky="ew", padx=padx)
        name_entry.bind("<FocusIn>", lambda e: name_focused.__setitem__(0, True))
        name_entry.bind("<Return>", commit_name)
        name_entry.bind("<FocusOut>", commit_name)

        swatch = tk.Label(row, bg=layer.color, width=1, relief="groove", bd=2,
                          cursor="hand2")
        swatch.grid(row=0, column=3, sticky="nsew", padx=padx + 2, pady=3)

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
                              width=1, state="readonly")

        def _update_stipple_enabled():
            combo.configure(state="readonly" if fill_var.get() else "disabled")

        def toggle_fill():
            L().fill = fill_var.get()
            _update_stipple_enabled()
            self.on_change()

        fill_cb = ttk.Checkbutton(row, variable=fill_var, command=toggle_fill)
        fill_cb.grid(row=0, column=4, sticky="w", padx=padx)

        combo.grid(row=0, column=5, sticky="ew", padx=padx)
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
