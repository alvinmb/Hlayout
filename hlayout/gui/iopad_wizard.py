# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Create > I/O Pad Wizard...: create and place numbered I/O pads along the
bottom and / or top edge of the selected substrate, a set distance in from
the edge (Session.edge_io_pads, the EDGEPADS command)."""

import tkinter as tk
from tkinter import ttk

from ..core.shapes import Polygon, Rectangle, SRef
from ..io.techfile import load_io_pad_rules
from ..scripting import iopads
from . import dialogs

_open = {}


def _mm(v):
    return f"{v / 1000.0:g}"


class IOPadWizard(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.session = app.session
        self.title("I/O Pad Wizard")
        self.resizable(False, False)
        self.shape = None
        pr = load_io_pad_rules(self.session.library)
        self.v = {
            "bottom": tk.BooleanVar(value=True),
            "top": tk.BooleanVar(value=True),
            "align": tk.StringVar(value="auto"),
            "n": tk.StringVar(value=""),
            "pitch": tk.StringVar(value="2.54"),
            "margin": tk.StringVar(value=_mm(pr["edge_margin"])),
            "w": tk.StringVar(value=_mm(pr["width"])),
            "l": tk.StringVar(value=_mm(pr["length"])),
            "start": tk.StringVar(value="1"),
            "layer": tk.StringVar(value=""),
        }
        f = ttk.Frame(self, padding=10)
        f.grid(sticky="nsew")
        r = 0
        ttk.Label(f, text="Substrate", font=("TkDefaultFont", 9, "bold")).grid(
            row=r, column=0, sticky="w")
        r += 1
        self.sub_lbl = ttk.Label(f, text="", width=52, wraplength=380, justify="left")
        self.sub_lbl.grid(row=r, column=0, columnspan=3, sticky="w")
        ttk.Button(f, text="Use Selection", command=self._use_selection).grid(
            row=r, column=3, sticky="e")
        r += 1
        ttk.Separator(f).grid(row=r, column=0, columnspan=4, sticky="ew", pady=6)
        r += 1
        ttk.Label(f, text="Edges").grid(row=r, column=0, sticky="w")
        ttk.Checkbutton(f, text="Bottom", variable=self.v["bottom"],
                        command=self._preview).grid(row=r, column=1, sticky="w")
        ttk.Checkbutton(f, text="Top", variable=self.v["top"],
                        command=self._preview).grid(row=r, column=2, sticky="w")
        r += 1
        ttk.Label(f, text="Position pads").grid(row=r, column=0, sticky="w")
        for i, (val, txt) in enumerate((("auto", "Auto"), ("header", "At header pins"),
                                        ("pitch", "At a pitch"))):
            ttk.Radiobutton(f, text=txt, value=val, variable=self.v["align"],
                            command=self._preview).grid(row=r, column=1 + i, sticky="w")
        r += 1

        def field(label, key, unit=""):
            nonlocal r
            ttk.Label(f, text=label).grid(row=r, column=0, sticky="w", pady=1)
            e = ttk.Entry(f, textvariable=self.v[key], width=10)
            e.grid(row=r, column=1, sticky="w")
            ttk.Label(f, text=unit, foreground="#555").grid(row=r, column=2, columnspan=2,
                                                           sticky="w")
            e.bind("<KeyRelease>", lambda _e: self._preview())
            r += 1

        field("Distance from edge", "margin", "mm  (pad outer end in from the substrate edge)")
        field("Pad width", "w", "mm  (along the edge)")
        field("Pad length", "l", "mm  (pointing in)")
        field("Pads per edge", "n", "blank = as many as fit  (pitch mode)")
        field("Pitch", "pitch", "mm  (pitch mode; 2.54 = 0.1 in)")
        field("First pin number", "start", "pin 1 bottom left, bottom L->R, top R->L")
        field("Layer", "layer", "blank = 1st conductor (Route layer A)")
        ttk.Separator(f).grid(row=r, column=0, columnspan=4, sticky="ew", pady=6)
        r += 1
        self.info = ttk.Label(f, text="", width=60, wraplength=430, justify="left")
        self.info.grid(row=r, column=0, columnspan=4, sticky="w")
        r += 1
        b = ttk.Frame(f)
        b.grid(row=r, column=0, columnspan=4, sticky="e", pady=(8, 0))
        ttk.Button(b, text="Place Pads", command=self._apply).pack(side="left", padx=4)
        ttk.Button(b, text="Close", command=self._close).pack(side="left")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._use_selection()

    # -- helpers ----------------------------------------------------------
    def _cell(self):
        return self.session.library.get_cell(self.app.canvas.cell_name)

    def _use_selection(self):
        sel = getattr(self.app.canvas, "selected", None)
        shp = sel[1] if sel and sel[0] in ("shape", "sref") else None
        self.shape = shp if isinstance(shp, (Rectangle, Polygon, SRef)) else None
        self._preview()

    def _num(self, key, conv=float, blank=None):
        t = self.v[key].get().strip()
        if not t:
            return blank
        return conv(t)

    def _kwargs(self):
        sides = ("B" if self.v["bottom"].get() else "") + ("T" if self.v["top"].get() else "")
        if not sides:
            raise ValueError("tick Bottom and / or Top")
        layer = self.v["layer"].get().strip()
        kw = dict(cell_name=self.app.canvas.cell_name, shape=self.shape, sides=sides,
                  align=self.v["align"].get(),
                  pitch=self._num("pitch") * 1000.0,
                  edge_margin=self._num("margin") * 1000.0,
                  pad_w=self._num("w") * 1000.0, pad_l=self._num("l") * 1000.0,
                  start=self._num("start", int, 1), n=self._num("n", int))
        if layer:
            kw["layer"] = int(layer) if layer.lstrip("-").isdigit() else layer
        return kw

    def _preview(self):
        cell = self._cell()
        try:
            outline = iopads.substrate_outline(self.session, cell, self.shape)
        except Exception as e:
            self.sub_lbl.config(text=str(e), foreground="#b00")
            self.info.config(text="")
            return
        x0, y0, x1, y1 = outline.bounds
        src = ("selected " + (f"instance of {self.shape.cell_name}" if isinstance(self.shape, SRef)
                              else type(self.shape).__name__.lower())
               if self.shape is not None else "boundary layer of " + cell.name)
        self.sub_lbl.config(text=f"{src}: {(x1 - x0) / 1000:.2f} x {(y1 - y0) / 1000:.2f} mm"
                            + ("" if outline.area >= (x1 - x0) * (y1 - y0) - 1 else ", shaped"),
                            foreground="")
        try:
            kw = self._kwargs()
        except Exception as e:
            self.info.config(text=str(e), foreground="#b00")
            return
        hp = {}
        try:
            hp = iopads.header_pins(self.session, cell, outline)
        except Exception:
            pass
        below = sum(1 for (px, py) in hp.values() if x0 <= px <= x1 and py < y0)
        above = sum(1 for (px, py) in hp.values() if x0 <= px <= x1 and py > y1)
        span = (x1 - x0) - 2 * kw["edge_margin"] - kw["pad_w"]
        fit = int(span // kw["pitch"]) + 1 if span >= 0 and kw["pitch"] > 0 else 0
        mode = kw["align"]
        if mode == "auto":
            mode = "header" if (below or above) else "pitch"
        if mode == "header":
            txt = (f"Header pins: {below} below, {above} above the substrate -- a pad at each, "
                   "numbered as its pin.")
        else:
            n = kw["n"] or fit
            nsides = len(kw["sides"])
            txt = (f"{n} pads per edge ({n * nsides} in all) at {kw['pitch'] / 1000:g} mm, "
                   f"centred; at most {fit} fit.")
            if kw["n"] and kw["n"] > fit:
                self.info.config(text=txt + "  Too many -- reduce the count or the pitch.",
                                 foreground="#b00")
                return
        users = [c.name for c in self.session.library.cells.values()
                 if any(r.cell_name == cell.name for r in c.srefs)]
        if users:
            self.info.config(text=txt + f"  WARNING: {cell.name} is placed inside "
                             f"{', '.join(users[:3])} -- I/O pads only count (SLI, Auto Place, "
                             f"routing) when drawn in the top cell: open {users[0]} and run "
                             "the wizard there, with the substrate instance selected.",
                             foreground="#b00")
            return
        self.info.config(text=txt + "  Pads with the same numbers are replaced.", foreground="")

    def _apply(self):
        try:
            kw = self._kwargs()
            r = self.session.edge_io_pads(**kw)
        except Exception as e:
            dialogs.show_error("I/O Pad Wizard", str(e), parent=self)
            return
        msg = (f"I/O pads: {r['pads']} placed on layer {r['layer']} "
               + ("at the header pins" if r["align"] == "header"
                  else f"at {r['pitch'] / 1000:g} mm pitch"))
        if r.get("io_named"):
            msg += f"; {r['io_named']} given the header's nets (IO.<pin>)"
        if r["skipped"]:
            msg += "; skipped " + ", ".join(f"pin {p} ({w})" for p, w in
                                            list(r["skipped"].items())[:4])
        try:
            self.app.status.set(msg)
            self.app.refresh_all()
        except Exception:
            pass
        self.info.config(text=msg, foreground="")

    def _close(self):
        _open.pop(id(self.app), None)
        self.destroy()


def open_iopad_wizard(app):
    w = _open.get(id(app))
    if w is not None and w.winfo_exists():
        w._use_selection()
        w.deiconify()
        w.lift()
        return w
    w = IOPadWizard(app)
    _open[id(app)] = w
    return w
