# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tools > Header Pinout...: change which net sits on which module header pin,
to steer the router. Each I/O pad (header pin) takes its net from the IO.<pin>
net name; this window edits those by hand -- set a pin's net, or swap two pins
-- and can also run Assign I/O Pads (the automatic shortest-uncrossed pinout).
Nothing moves on the board; only the net-to-pin assignment changes, so re-run
the router (or SLI) to see the new routing."""

import tkinter as tk
from tkinter import ttk

from ..core.errors import LayoutError
from . import dialogs


class HeaderPinoutWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Header Pinout")
        self.transient(app)
        self.minsize(420, 460)
        self.rows = []            # (pin, StringVar)
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="Net on each header pin -- edit a net, or swap two pins, "
                  "then Apply.", wraplength=400, foreground="#555").pack(anchor="w")

        # scrollable pin list
        body = ttk.Frame(f)
        body.pack(fill="both", expand=True, pady=(6, 6))
        self.canvas = tk.Canvas(body, highlightthickness=0, width=380)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        sw = ttk.LabelFrame(f, text="Swap two pins", padding=6)
        sw.pack(fill="x")
        ttk.Label(sw, text="Pin").grid(row=0, column=0, padx=2)
        self.swap_a = ttk.Combobox(sw, width=7, state="readonly")
        self.swap_a.grid(row=0, column=1, padx=2)
        ttk.Label(sw, text="with pin").grid(row=0, column=2, padx=2)
        self.swap_b = ttk.Combobox(sw, width=7, state="readonly")
        self.swap_b.grid(row=0, column=3, padx=2)
        ttk.Button(sw, text="Swap", command=self._swap).grid(row=0, column=4, padx=6)

        b = ttk.Frame(f)
        b.pack(fill="x", pady=(8, 0))
        ttk.Button(b, text="Apply", command=self._apply).pack(side="left")
        ttk.Button(b, text="Auto-optimize", command=self._optimize).pack(side="left", padx=4)
        ttk.Button(b, text="Reload", command=self.reload).pack(side="left")
        ttk.Button(b, text="Close", command=self.destroy).pack(side="right")
        self.status = ttk.Label(f, text="", foreground="#555", wraplength=400)
        self.status.pack(anchor="w", pady=(6, 0))
        self.reload()

    @property
    def session(self):
        return self.app.session

    def reload(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.rows = []
        try:
            pinout = self.session.header_pinout()
        except Exception as e:
            self.status.config(text=f"{e}")
            return
        if not pinout:
            ttk.Label(self.inner, text="No header pins found. Bond the header first "
                      "(Tools > Bond Header Pins...) and import net names.",
                      wraplength=360, foreground="#999").grid(row=0, column=0, sticky="w")
            self.swap_a["values"] = self.swap_b["values"] = []
            return
        ttk.Label(self.inner, text="Pin", width=8, font=("", 9, "bold")).grid(row=0, column=0,
                                                                              sticky="w")
        ttk.Label(self.inner, text="Net", font=("", 9, "bold")).grid(row=0, column=1, sticky="w")
        for i, ent in enumerate(pinout, start=1):
            pin = str(ent["pin"])
            ttk.Label(self.inner, text=pin, width=8).grid(row=i, column=0, sticky="w", pady=1)
            v = tk.StringVar(value=ent.get("net") or "")
            ttk.Entry(self.inner, textvariable=v, width=28).grid(row=i, column=1, sticky="w", pady=1)
            self.rows.append((pin, v))
        pins = [p for p, _v in self.rows]
        self.swap_a["values"] = pins
        self.swap_b["values"] = pins
        if len(pins) >= 2:
            self.swap_a.set(pins[0])
            self.swap_b.set(pins[1])
        self.status.config(text=f"{len(pins)} header pin(s). Changes take effect on the next "
                            "route / SLI.")

    def _after_change(self, msg):
        try:
            self.session.retrace_net_highlight()
        except Exception:
            pass
        self.app.refresh_all()
        self.reload()
        self.status.config(text=msg)

    def _apply(self):
        mapping = {pin: v.get().strip() for pin, v in self.rows}
        try:
            n = self.session.set_header_pins(mapping)
        except LayoutError as e:
            dialogs.show_error("Header Pinout", str(e), parent=self)
            return
        self._after_change(f"{n} pin(s) changed." if n else "No changes.")

    def _swap(self):
        a, b = self.swap_a.get(), self.swap_b.get()
        if not a or not b or a == b:
            self.status.config(text="Pick two different pins to swap.")
            return
        try:
            r = self.session.swap_header_pins(a, b)
        except LayoutError as e:
            dialogs.show_error("Header Pinout", str(e), parent=self)
            return
        self._after_change(f"Pin {a} <-> {b}: {r['net_b']} now on {a}, {r['net_a']} on {b}.")

    def _optimize(self):
        try:
            p = self.session.assign_io_pads(apply=True)
        except LayoutError as e:
            dialogs.show_error("Header Pinout", str(e), parent=self)
            return
        moved = sum(1 for s in (p.get("sites") or []) if s.get("net") != s.get("was"))
        self._after_change(f"Auto-optimized: {moved} pin(s) reassigned "
                           f"({p.get('before', '?')} -> {p.get('after', '?')} um total).")


def open_header_pinout_window(app):
    HeaderPinoutWindow(app)
