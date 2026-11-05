# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""View > Unrouted Nets... -- the nets still to route, one at a time.

Lists every named net that copper doesn't yet join (from Session.sli,
the same straight-line interconnects as View > Straight-Line
Interconnects), longest gap first. Picking one -- click, Up/Down, or
Prev/Next -- shows its SLI thick, rings each loose end, and Zoom
Windows round it, so a short gap on a busy board can be found. Refresh
re-reads the list after routing; the window follows the design.
"""

import tkinter as tk
from tkinter import ttk


class UnroutedNetsWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Unrouted Nets")
        self.geometry("360x420")
        self.resizable(True, True)
        self.nets = []
        self.zoom_var = tk.BooleanVar(value=True)
        self.only_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="")
        self._build()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()

    def _build(self):
        frm = ttk.Frame(self, padding=6)
        frm.pack(fill="both", expand=True)
        cols = ("net", "gaps", "length")
        self.tree = ttk.Treeview(frm, columns=cols, show="headings", selectmode="browse", height=12)
        for c, w, txt in (("net", 150, "Net"), ("gaps", 60, "Gaps"), ("length", 90, "SLI (mm)")):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w" if c == "net" else "e")
        sb = ttk.Scrollbar(frm, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, columnspan=4, sticky="nsew")
        sb.grid(row=0, column=4, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree.bind("<Double-1>", lambda e: self._on_select(force_zoom=True))
        ttk.Checkbutton(frm, text="Zoom to net", variable=self.zoom_var).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Checkbutton(frm, text="Show this net only", variable=self.only_var,
                        command=self._on_only).grid(row=1, column=2, columnspan=3, sticky="w",
                                                    pady=(6, 0))
        ttk.Button(frm, text="< Prev", command=lambda: self.step(-1)).grid(row=2, column=0, sticky="ew", pady=4)
        ttk.Button(frm, text="Next >", command=lambda: self.step(1)).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Button(frm, text="Refresh", command=self.refresh).grid(row=2, column=2, sticky="ew", pady=4)
        ttk.Button(frm, text="Close", command=self.close).grid(row=2, column=3, columnspan=2, sticky="ew", pady=4)
        ttk.Label(frm, textvariable=self.status_var, foreground="#555").grid(
            row=3, column=0, columnspan=5, sticky="w")
        frm.rowconfigure(0, weight=1)
        for c in range(4):
            frm.columnconfigure(c, weight=1)

    # --- data ----------------------------------------------------------------
    def refresh(self):
        cv = self.app.canvas
        keep = cv.sli_focus
        try:
            d = cv.sli_unrouted()
        except Exception as exc:          # no net names, no cell ...
            d = {"nets": []}
            self.status_var.set(f"no SLI: {exc}")
        self.nets = list(d.get("nets", []))
        self.tree.delete(*self.tree.get_children())
        for n in self.nets:
            self.tree.insert("", "end", iid=n["net"],
                             values=(n["net"], len(n["segments"]), f"{n['length'] / 1000:.2f}"))
        done = d.get("complete", 0)
        if self.nets:
            self.status_var.set(f"{len(self.nets)} unrouted, {done} complete")
        elif "complete" in d:
            self.status_var.set(f"all {done} nets routed")
        if keep in [n["net"] for n in self.nets]:
            self.tree.selection_set(keep)
            self.tree.see(keep)
        elif keep is not None:
            cv.focus_sli_net(None)
        return self.nets

    def select(self, net, zoom=None):
        """Pick ``net`` (as from the list). Returns its SLI entry."""
        if net not in [n["net"] for n in self.nets]:
            return None
        if tuple(self.tree.selection()) != (net,):
            self._quiet = True
            self.tree.selection_set(net)
            self.tree.see(net)
        return self._show(net, self.zoom_var.get() if zoom is None else zoom)

    def step(self, delta):
        if not self.nets:
            return None
        names = [n["net"] for n in self.nets]
        cur = self.tree.selection()
        i = names.index(cur[0]) + delta if cur and cur[0] in names else (0 if delta > 0 else -1)
        return self.select(names[i % len(names)])

    def _on_select(self, force_zoom=False):
        if getattr(self, "_quiet", False):
            self._quiet = False
            return
        cur = self.tree.selection()
        if cur:
            self._show(cur[0], True if force_zoom else self.zoom_var.get())

    def _show(self, net, zoom):
        cv = self.app.canvas
        cv.sli_focus_only = bool(self.only_var.get())
        if hasattr(self.app, "show_sli_var"):
            self.app.show_sli_var.set(True)
        entry = cv.focus_sli_net(net, zoom=zoom)
        if entry is not None:
            msg = (f"{net}: {len(entry['segments'])} gap(s), "
                   f"{entry['length'] / 1000:.2f} mm unrouted")
            self.status_var.set(msg)
            try:
                self.app.status.set(msg)
            except Exception:
                pass
        return entry

    def _on_only(self):
        cv = self.app.canvas
        cv.sli_focus_only = bool(self.only_var.get())
        cv.redraw()

    def close(self):
        try:
            self.app.canvas.sli_focus = None
            self.app.canvas.sli_focus_only = False
            self.app.canvas.redraw()
        finally:
            self.app._unrouted_window = None
            self.destroy()


def open_unrouted_window(app):
    win = getattr(app, "_unrouted_window", None)
    if win is not None and win.winfo_exists():
        win.refresh()
        win.lift()
        win.focus_set()
        return win
    win = UnroutedNetsWindow(app)
    app._unrouted_window = win
    return win
