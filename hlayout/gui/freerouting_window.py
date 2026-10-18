"""Freerouting autorouter -- Tools > Autoroute (Freerouting)...

Drives the Specctra round trip in hlayout.io.specctra / Session:
export a DSN (the two Route Layers, every numbered pad, the design's
net names and the existing routing), run Freerouting headless on a
worker thread with its output streamed into the log, then import the
session file back as one undo step -- tracks on the conductor layers,
vias with their dielectric windows, dielectric fill regenerated -- and
report which named nets are complete.

The rules (widths, clearance, vias, power/excluded nets, passes, 90
degree routing, keep existing) are saved with the design; the
Freerouting launcher/.jar and Java paths are saved per installation.
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, ttk

from ..core.errors import LayoutError
from ..io import app_settings
from ..io.specctra import (EXCLUDED_CLASS, find_freerouting, find_java, freerouting_command,
                           run_freerouting)

_NUM_FIELDS = (
    ("Signal width (um)", "signal_width"),
    ("Power width (um)", "power_width"),
    ("Clearance (um)", "clearance"),
    ("Signal via (um)", "signal_via"),
    ("Signal via window (um)", "signal_via_window"),
    ("Power via (um)", "power_via"),
    ("Power via window (um)", "power_via_window"),
    ("Max passes", "passes"),
    ("Bond finger (um)", "bond_finger"),
    ("Die keep-out (um)", "die_keepout"),
)

#: layer_mode choices: (shown text, rule value)
_LAYER_MODES = (("Auto (one layer if nothing is on the 2nd conductor)", "auto"),
                ("1st conductor only (no vias)", "single"),
                ("Both conductors", "two"))


class FreeroutingWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.session = app.session
        self.title("Autoroute (Freerouting)")
        self.transient(master)
        self.minsize(640, 560)
        self.vars = {}
        self._cancel = False
        self._running = False
        self._build()
        self.reload()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())

    # ------------------------------------------------------------------ UI
    def _build(self):
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        rf = ttk.LabelFrame(outer, text="Freerouting", padding=8)
        rf.pack(fill="x")
        self.vars["router"] = tk.StringVar()
        self.vars["java"] = tk.StringVar()
        ttk.Label(rf, text="Launcher / .jar:").grid(row=0, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(rf, textvariable=self.vars["router"], width=52).grid(row=0, column=1, sticky="we")
        ttk.Button(rf, text="Browse...", command=self._browse_router).grid(row=0, column=2, padx=4)
        ttk.Label(rf, text="Java (for a .jar):").grid(row=1, column=0, sticky="e", padx=4, pady=2)
        ttk.Entry(rf, textvariable=self.vars["java"], width=52).grid(row=1, column=1, sticky="we")
        ttk.Button(rf, text="Browse...", command=self._browse_java).grid(row=1, column=2, padx=4)
        self.router_status = ttk.Label(rf, text="", foreground="#666")
        self.router_status.grid(row=2, column=1, sticky="w")
        rf.columnconfigure(1, weight=1)

        gf = ttk.LabelFrame(outer, text="Rules (saved with the design)", padding=8)
        gf.pack(fill="x", pady=(8, 0))
        for i, (label, key) in enumerate(_NUM_FIELDS):
            self.vars[key] = tk.StringVar()
            r, c = divmod(i, 2)
            ttk.Label(gf, text=label + ":").grid(row=r, column=c * 2, sticky="e", padx=4, pady=2)
            ttk.Entry(gf, textvariable=self.vars[key], width=9).grid(row=r, column=c * 2 + 1,
                                                                    sticky="w")
        row = (len(_NUM_FIELDS) + 1) // 2
        for key, label in (("power_nets", "Power nets:"), ("exclude_nets", "Leave unrouted:")):
            self.vars[key] = tk.StringVar()
            ttk.Label(gf, text=label).grid(row=row, column=0, sticky="e", padx=4, pady=2)
            ttk.Entry(gf, textvariable=self.vars[key], width=46).grid(row=row, column=1,
                                                                     columnspan=3, sticky="we")
            row += 1
        self.vars["snap_90"] = tk.BooleanVar()
        self.vars["keep_existing"] = tk.BooleanVar()
        self.vars["remove_existing"] = tk.BooleanVar()
        ttk.Checkbutton(gf, text="90-degree routing only", variable=self.vars["snap_90"]).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=4)
        ttk.Checkbutton(gf, text="Keep existing tracks and vias (else rip up and reroute)",
                        variable=self.vars["keep_existing"]).grid(
            row=row, column=2, columnspan=2, sticky="w", padx=4)
        row += 1
        ttk.Checkbutton(gf, text="Remove ALL existing tracks and vias and route from scratch",
                        variable=self.vars["remove_existing"]).grid(
            row=row, column=0, columnspan=4, sticky="w", padx=4)
        row += 1
        self.vars["keepout_bodies"] = tk.BooleanVar()
        ttk.Checkbutton(gf, text="No tracks between a part's own pads (under a chip cap, "
                                 "across resistor ink)",
                        variable=self.vars["keepout_bodies"]).grid(
            row=row, column=0, columnspan=4, sticky="w", padx=4)
        row += 1
        self.vars["layer_mode"] = tk.StringVar()
        ttk.Label(gf, text="Layers:").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Combobox(gf, textvariable=self.vars["layer_mode"], state="readonly", width=48,
                     values=[t for t, _v in _LAYER_MODES]).grid(
            row=row, column=1, columnspan=3, sticky="w")

        nf = ttk.Frame(outer)
        nf.pack(fill="x", pady=(8, 0))
        self.nets_label = ttk.Label(nf, text="")
        self.nets_label.pack(side="left")
        ttk.Button(nf, text="Import Net Names...", command=self._import_names).pack(side="right")

        bf = ttk.Frame(outer)
        bf.pack(fill="x", pady=(8, 0))
        self.route_btn = ttk.Button(bf, text="Route", command=self.route)
        self.route_btn.pack(side="left")
        self.cancel_btn = ttk.Button(bf, text="Stop", command=self._stop, state="disabled")
        self.cancel_btn.pack(side="left", padx=4)
        ttk.Button(bf, text="Export DSN...", command=self._export).pack(side="left", padx=(16, 4))
        ttk.Button(bf, text="Import SES...", command=self._import).pack(side="left")
        ttk.Button(bf, text="Close", command=self.close).pack(side="right")

        lf = ttk.LabelFrame(outer, text="Log", padding=4)
        lf.pack(fill="both", expand=True, pady=(8, 0))
        self.log = tk.Text(lf, height=12, wrap="none", font=("Consolas", 9))
        sb = ttk.Scrollbar(lf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set, state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ------------------------------------------------------------- state
    def reload(self):
        from ..io.techfile import load_net_names
        rules = self.session.freerouting_rules()
        for _l, key in _NUM_FIELDS:
            self.vars[key].set(str(int(rules[key])))
        self.vars["power_nets"].set(", ".join(rules["power_nets"]))
        self.vars["exclude_nets"].set(", ".join(rules["exclude_nets"]))
        self.vars["snap_90"].set(bool(rules["snap_90"]))
        self.vars["keep_existing"].set(bool(rules["keep_existing"]))
        self.vars["remove_existing"].set(bool(rules.get("remove_existing", False)))
        self.vars["keepout_bodies"].set(bool(rules.get("keepout_bodies", True)))
        mode = str(rules.get("layer_mode", "auto"))
        self.vars["layer_mode"].set(next((t for t, v in _LAYER_MODES if v == mode),
                                         _LAYER_MODES[0][0]))
        self.vars["router"].set(app_settings.get_setting(app_settings.FREEROUTING_PATH_KEY)
                                or find_freerouting() or "")
        self.vars["java"].set(app_settings.get_setting(app_settings.JAVA_PATH_KEY) or "")
        names = load_net_names(self.session.library)
        self.nets_label.config(text=f"Net names: {len(set(names.values()))} net(s), "
                                    f"{len(names)} pad(s)" if names else
                               "Net names: none -- import them before routing")
        self._update_router_status()

    def _update_router_status(self):
        p = self.vars["router"].get().strip()
        if not p:
            msg = "Not found -- install Freerouting (freerouting.app) and pick its launcher"
        elif not os.path.exists(p):
            msg = "That file doesn't exist"
        elif p.lower().endswith(".jar") and not find_java(self.vars["java"].get().strip() or None):
            msg = "A .jar needs Java -- install Java 25+ or pick java.exe"
        elif "freerouting-cli" in os.path.basename(p).lower():
            msg = ("Ready -- native CLI build: one routing pass per run (it crashes on "
                   "multi-pass); the Java build routes the full pass count")
        else:
            msg = "Ready"
        self.router_status.config(text=msg)

    def _rules(self):
        out = {}
        for label, key in _NUM_FIELDS:
            try:
                v = float(self.vars[key].get())
            except ValueError:
                raise LayoutError(f"{label}: not a number")
            if v <= 0:
                raise LayoutError(f"{label}: must be > 0")
            out[key] = v
        out["power_nets"] = [x.strip() for x in self.vars["power_nets"].get().split(",") if x.strip()]
        out["exclude_nets"] = [x.strip() for x in self.vars["exclude_nets"].get().split(",")
                               if x.strip()]
        out["snap_90"] = bool(self.vars["snap_90"].get())
        out["keep_existing"] = bool(self.vars["keep_existing"].get())
        out["remove_existing"] = bool(self.vars["remove_existing"].get())
        out["keepout_bodies"] = bool(self.vars["keepout_bodies"].get())
        shown = self.vars["layer_mode"].get()
        out["layer_mode"] = next((v for t, v in _LAYER_MODES if t == shown), "auto")
        return out

    def _save(self):
        rules = self.session.set_freerouting_rules(**self._rules())
        app_settings.set_setting(app_settings.FREEROUTING_PATH_KEY,
                                 self.vars["router"].get().strip() or None)
        app_settings.set_setting(app_settings.JAVA_PATH_KEY, self.vars["java"].get().strip() or None)
        return rules

    def _say(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    #: Freerouting chatter that says nothing useful to a user (it can
    #: print hundreds of these per pass)
    _NOISE = ("BoxShapeSearchTree", "normalizeTraces", "Deprecated settings path",
              "is deprecated; use")

    def _say_router(self, line):
        if any(n in line for n in self._NOISE):
            return
        # drop the timestamp / job id prefix Freerouting puts on every line
        import re
        line = re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+\s+", "", line)
        line = re.sub(r"\[[0-9A-F]+\\[0-9A-F]+\]\s*", "", line)
        self._say(line)
        if "pass #" in line or "stage" in line:
            self.app.status.set("Freerouting: " + line[:160])

    # ------------------------------------------------------------ actions
    def _browse_router(self):
        p = filedialog.askopenfilename(parent=self, title="Freerouting launcher or .jar",
                                       filetypes=[("Freerouting", "*.exe *.jar"), ("All", "*.*")])
        if p:
            self.vars["router"].set(p)
            self._update_router_status()

    def _browse_java(self):
        p = filedialog.askopenfilename(parent=self, title="java executable",
                                       filetypes=[("Java", "java*"), ("All", "*.*")])
        if p:
            self.vars["java"].set(p)
            self._update_router_status()

    def _import_names(self):
        p = filedialog.askopenfilename(parent=self, title="Net names CSV",
                                       filetypes=[("CSV", "*.csv"), ("All", "*.*")])
        if not p:
            return
        try:
            n = self.session.import_net_names(p)
            self._say(f"{n} named pad(s) imported from {p}")
        except Exception as e:
            self._say(f"Net names: {e}")
        self.reload()

    def _work_dir(self):
        base = getattr(self.app, "current_path", None)
        d = os.path.join(os.path.dirname(base), "freerouting") if base else None
        if d is None:
            import tempfile
            d = os.path.join(tempfile.gettempdir(), "hlayout_freerouting")
        os.makedirs(d, exist_ok=True)
        return d

    def _export(self):
        try:
            self._save()
            name = self.session.current_cell_name
            p = filedialog.asksaveasfilename(parent=self, title="Export Specctra DSN",
                                             initialdir=self._work_dir(),
                                             initialfile=f"{name}.dsn", defaultextension=".dsn",
                                             filetypes=[("Specctra DSN", "*.dsn")])
            if not p:
                return
            sm = self.session.export_dsn(p)
            self._say(f"Wrote {p}: {sm.components} part(s), {sm.pins} pad(s), {sm.nets} net(s), "
                      f"{sm.wires} track(s), {sm.vias} via(s)")
        except Exception as e:
            self._say(f"Export failed: {e}")

    def _import(self):
        p = filedialog.askopenfilename(parent=self, title="Import Specctra session",
                                       initialdir=self._work_dir(),
                                       filetypes=[("Specctra session", "*.ses"), ("All", "*.*")])
        if p:
            self._do_import(p)

    def _do_import(self, ses):
        try:
            self._save()
            st = self.session.import_ses(ses)
        except Exception as e:
            self._say(f"Import failed: {e}")
            return
        self._say(f"Imported {st['wires']} track(s) and {st['vias']} via(s) "
                  f"({st['skipped']} already there).")
        if st.get("fingers"):
            self._say(f"Laid {st['fingers']} bond finger(s) where wire bonds land on the substrate.")
        if st.get("widened"):
            self._say(f"Widened {st['widened']} necked-down track(s) back to the minimum width "
                      "-- run Tools > DRC to check their clearance.")
        self._say(f"Nets complete: {st['complete']} of {st['nets']}")
        if st["unrouted"]:
            self._say("Still unrouted: " + ", ".join(st["unrouted"]))
        self.app.status.set(f"Freerouting: {st['complete']}/{st['nets']} nets complete, "
                            f"{st['wires']} tracks + {st['vias']} vias added")

    def route(self):
        if self._running:
            return
        try:
            rules = self._save()
            router = find_freerouting(self.vars["router"].get().strip() or None)
            if not router:
                raise LayoutError("Freerouting not found -- install it (freerouting.app) and "
                                  "pick its launcher or .jar above")
            name = self.session.current_cell_name
            wd = self._work_dir()
            dsn = os.path.join(wd, f"{name}.dsn")
            ses = os.path.join(wd, f"{name}.ses")
            sm = self.session.export_dsn(dsn)
            cmd = freerouting_command(router, dsn, ses, passes=int(rules["passes"]),
                                      exclude_classes=[EXCLUDED_CLASS] if rules["exclude_nets"] else [],
                                      java=self.vars["java"].get().strip() or None)
        except Exception as e:
            self._say(f"Can't start: {e}")
            return
        self._say(f"Exported {dsn}: {sm.nets} net(s), {sm.pins} pad(s), "
                  f"{sm.wires} track(s), {sm.vias} via(s)")
        self._say("Running: " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
        self._running, self._cancel = True, False
        self.route_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")

        def worker():
            try:
                code, tail = run_freerouting(
                    cmd, ses, on_line=lambda ln: self.after(0, self._say_router, ln),
                    cancel=lambda: self._cancel)
                err = None
            except Exception as e:     # e.g. the launcher couldn't start
                code, tail, err = -1, [], e
            self.after(0, self._finished, ses, code, tail, err)

        threading.Thread(target=worker, daemon=True).start()

    def _finished(self, ses, code, tail, err):
        self._running = False
        self.route_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        if err is not None:
            self._say(f"Freerouting failed to start: {err}")
            return
        if self._cancel:
            self._say("Stopped.")
        if not os.path.exists(ses):
            self._say(f"No session file was written (exit code {code}).")
            return
        self._do_import(ses)

    def _stop(self):
        self._cancel = True

    def close(self):
        if self._running:
            self._cancel = True
        try:
            self._save()
        except Exception:
            pass
        self.destroy()


def open_freerouting_window(app):
    """Tools > Autoroute (Freerouting)... . One window per App."""
    win = getattr(app, "_freerouting_window", None)
    if win is not None and win.winfo_exists():
        win.reload()
        win.lift()
        win.focus_set()
        return win
    win = FreeroutingWindow(app, app)
    app._freerouting_window = win
    return win
