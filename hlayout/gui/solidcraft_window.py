# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""File > 3D View in SolidCraft... -- build the current cell as a 3D model
in SolidCraft (github.com/bherbruck/solidcraft) through its MCP server.

SolidCraft opens in its own window and the model builds in front of you:
substrate, every printed film (its Setup thickness), the dielectric,
each part as a body sitting on the film under it, and every wire bond
as a raised arc of wire -- the same stack the FreeCAD export makes. It
can also write a STEP / 3MF / STL / OBJ file of the result.
"""
import copy
import os
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk

from ..io import app_settings
from ..io.solidcraft_mcp import find_solidcraft

SOLIDCRAFT_PATH_KEY = "solidcraft_path"


class SolidCraftWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.session = app.session
        self.title("3D View in SolidCraft")
        self.transient(master)
        self.resizable(True, False)
        self._running = False
        self._cancel = False
        self.vars = {"path": tk.StringVar(), "window": tk.BooleanVar(value=True),
                     "export": tk.BooleanVar(value=False), "export_path": tk.StringVar()}
        self._build()
        self.reload()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())

    def _build(self):
        f = ttk.Frame(self, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="SolidCraft folder:").grid(row=0, column=0, sticky="e", padx=4, pady=3)
        ttk.Entry(f, textvariable=self.vars["path"], width=58).grid(row=0, column=1, sticky="we")
        ttk.Button(f, text="Browse...", command=self._browse).grid(row=0, column=2, padx=4)
        self.found = ttk.Label(f, text="", foreground="#666")
        self.found.grid(row=1, column=1, sticky="w")
        ttk.Checkbutton(f, text="Open SolidCraft and build the model in its window",
                        variable=self.vars["window"]).grid(row=2, column=0, columnspan=3,
                                                           sticky="w", pady=(8, 0))
        ttk.Checkbutton(f, text="Also write a 3D file (STEP / 3MF / STL / OBJ):",
                        variable=self.vars["export"]).grid(row=3, column=0, columnspan=2,
                                                           sticky="w")
        ttk.Entry(f, textvariable=self.vars["export_path"], width=58).grid(row=4, column=1, sticky="we")
        ttk.Button(f, text="Browse...", command=self._browse_export).grid(row=4, column=2, padx=4)
        self.banner = tk.Label(f, text="", font=("Segoe UI", 14, "bold"), anchor="w",
                               padx=8, pady=4, fg="white", bg="#1f5fbf")
        self.banner.grid(row=5, column=0, columnspan=3, sticky="we", pady=(10, 2))
        self.banner.grid_remove()
        style = ttk.Style(self)
        style.configure("SC.Horizontal.TProgressbar", thickness=20)
        self.bar = ttk.Progressbar(f, maximum=100, style="SC.Horizontal.TProgressbar")
        self.bar.grid(row=6, column=0, columnspan=3, sticky="we")
        self.bar.grid_remove()
        bf = ttk.Frame(f)
        bf.grid(row=7, column=0, columnspan=3, sticky="we", pady=(10, 0))
        self.go = ttk.Button(bf, text="Build 3D", command=self.build)
        self.go.pack(side="left")
        self.stop = ttk.Button(bf, text="Stop", command=self._stop, state="disabled")
        self.stop.pack(side="left", padx=4)
        ttk.Button(bf, text="Close", command=self.close).pack(side="right")
        f.columnconfigure(1, weight=1)
        self.vars["path"].trace_add("write", lambda *_: self._update_found())

    def reload(self):
        saved = app_settings.get_setting(SOLIDCRAFT_PATH_KEY) or ""
        if not saved:
            app, cli = find_solidcraft()
            saved = os.path.dirname(cli) if cli else ""
        self.vars["path"].set(saved)
        cp = getattr(self.app, "current_path", None)
        home = os.path.join(os.path.dirname(cp), "3d") if cp else None
        lib = self.session.library.name
        cell = self.session.current_cell_name or "TOP"
        if home and not self.vars["export_path"].get():
            self.vars["export_path"].set(os.path.join(home, f"{lib}_{cell}.step"))
        self._update_found()

    def _update_found(self):
        app, cli = find_solidcraft(self.vars["path"].get().strip() or None)
        if cli and app:
            self.found.config(text="Found: solidcraft.exe and solidcraft-cli.exe")
        elif cli:
            self.found.config(text="Found solidcraft-cli only -- can export, not open the window")
        else:
            self.found.config(text="Not found -- install SolidCraft (github.com/bherbruck/solidcraft)")

    def _browse(self):
        d = filedialog.askdirectory(parent=self, title="Folder holding solidcraft-cli.exe")
        if d:
            self.vars["path"].set(d)

    def _browse_export(self):
        p = filedialog.asksaveasfilename(parent=self, title="3D file",
                                         defaultextension=".step",
                                         filetypes=[("STEP", "*.step"), ("3MF", "*.3mf"),
                                                    ("STL", "*.stl"), ("OBJ", "*.obj")])
        if p:
            self.vars["export_path"].set(p)
            self.vars["export"].set(True)

    def _say(self, text, kind="run", pct=None):
        if not self._alive():
            return
        colours = {"run": "#1f5fbf", "ok": "#1e8e3e", "err": "#c5221f"}
        self.banner.config(text=text, bg=colours.get(kind, "#1f5fbf"))
        self.banner.grid()
        if pct is None:
            self.bar.grid_remove()
        else:
            self.bar.grid()
            self.bar["value"] = pct
        self.app.status.set("SolidCraft: " + text)

    def _alive(self):
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def build(self):
        if self._running:
            return
        folder = self.vars["path"].get().strip()
        app_settings.set_setting(SOLIDCRAFT_PATH_KEY, folder)
        window = bool(self.vars["window"].get())
        export = self.vars["export_path"].get().strip() if self.vars["export"].get() else None
        if not window and not export:
            self._say("Tick the window, a 3D file, or both", "err")
            return
        if export:
            os.makedirs(os.path.dirname(os.path.abspath(export)), exist_ok=True)
        lib = copy.deepcopy(self.session.library)        # the job reads a snapshot
        cell = self.session.current_cell_name
        name = os.path.splitext(os.path.basename(getattr(self.app, "current_path", "") or ""))[0] \
            or lib.name
        self._running, self._cancel = True, False
        self.go.config(state="disabled")
        self.stop.config(state="normal")
        self._say("Starting SolidCraft...", "run", 0)
        t0 = time.monotonic()

        def progress(i, n, label):
            self.after(0, self._say, f"Building {int(100 * i / n)}% -- {label}", "run",
                       100.0 * i / n)

        def work():
            from ..io.solidcraftio import send_to_solidcraft
            try:
                path = getattr(self.app, "current_path", None) or \
                    getattr(self.app.session, "current_path", None)
                r = send_to_solidcraft(lib, cell, app=folder or None, window=window, export=export,
                                       on_progress=progress, cancel=lambda: self._cancel,
                                       design_name=f"{name} {cell}",
                                       model_dir=os.path.dirname(os.path.abspath(path))
                                       if path else None)
                self.after(0, self._done, r, None, time.monotonic() - t0)
            except Exception as e:
                self.after(0, self._done, None, e, time.monotonic() - t0)

        threading.Thread(target=work, daemon=True).start()

    def _done(self, r, err, secs):
        self._running = False
        if not self._alive():
            return
        self.go.config(state="normal")
        self.stop.config(state="disabled")
        took = f"{int(secs) // 60}:{int(secs) % 60:02d}"
        if err is not None:
            self._say(f"FAILED -- {err}", "err")
        elif r["errors"]:
            self._say(f"FINISHED in {took} with {len(r['errors'])} problem(s): "
                      + "; ".join(r["errors"])[:300], "err")
        else:
            msg = f"FINISHED in {took} -- {r['bodies']} bodies"
            if r.get("export"):
                msg += f"; wrote {os.path.basename(r['export'])}"
            self._say(msg, "ok", 100)
        try:
            self.bell()
            self.lift()
        except tk.TclError:
            pass

    def _stop(self):
        self._cancel = True

    def close(self):
        if self._running:
            self._cancel = True
        self.destroy()


def open_solidcraft_window(app):
    win = getattr(app, "_solidcraft_window", None)
    if win is not None and win.winfo_exists():
        win.deiconify()
        win.lift()
        return win
    win = SolidCraftWindow(app, app)
    app._solidcraft_window = win
    return win
