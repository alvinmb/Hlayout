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

#: the run-length settings, in their own "Passes" box by the Route button
_PASS_FIELDS = (
    ("Route passes", "passes", "0 = until every net is routed"),
    ("Optimize passes", "optimize_passes", "0 = don't optimize"),
    ("Stop optimizing below (%)", "optimize_threshold", "gain per pass"),
)

_NUM_FIELDS = (
    ("Signal width (um)", "signal_width"),
    ("Power width (um)", "power_width"),
    ("Clearance (um)", "clearance"),
    ("Signal via (um)", "signal_via"),
    ("Signal via window (um)", "signal_via_window"),
    ("Power via (um)", "power_via"),
    ("Power via window (um)", "power_via_window"),
    ("Bond finger (um)", "bond_finger"),
    ("Die keep-out (um)", "die_keepout"),
    ("Via cost", "via_cost"),
    ("Against-direction cost", "against_direction_cost"),
)

#: layer_directions choices: (shown text, rule value)
_DIRECTIONS = (("1st conductor horizontal, 2nd conductor vertical", "hv"),
               ("1st conductor vertical, 2nd conductor horizontal", "vh"),
               ("No preferred direction", "off"))

#: layer_mode choices: (shown text, rule value)
_LAYER_MODES = (("Auto (one layer if nothing is on the 2nd conductor)", "auto"),
                ("1st conductor only (no vias)", "single"),
                ("Both conductors", "two"))

#: finger_layer choices: (shown text, rule value)
_FINGER_LAYERS = (("1st conductor", "first"),
                  ("2nd conductor (top, no dielectric window; routes both layers)", "second"))


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
        self._hide_when_done = False
        self._build()
        self.reload()
        self._fit_screen()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())

    # ------------------------------------------------------------------ UI
    def _build(self):
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        # big, coloured result line (what the router is doing / how it
        # ended) with a percent-complete bar under it while it runs
        self.banner_box = ttk.Frame(outer)
        self.banner = tk.Label(self.banner_box, text="", font=self._BANNER_FONT, anchor="w",
                               padx=10, pady=6, justify="left", wraplength=900)
        self.banner.pack(fill="x")
        style = ttk.Style(self)
        style.configure("Route.Horizontal.TProgressbar", thickness=24)
        self.progress = ttk.Progressbar(self.banner_box, maximum=100, mode="determinate",
                                        style="Route.Horizontal.TProgressbar")
        self._prog = None

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
        row += 1
        self.vars["finger_layer"] = tk.StringVar()
        ttk.Label(gf, text="Bond fingers on:").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Combobox(gf, textvariable=self.vars["finger_layer"], state="readonly", width=48,
                     values=[t for t, _v in _FINGER_LAYERS]).grid(
            row=row, column=1, columnspan=3, sticky="w")
        row += 1
        self.vars["layer_directions"] = tk.StringVar()
        ttk.Label(gf, text="Track directions:").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Combobox(gf, textvariable=self.vars["layer_directions"], state="readonly", width=48,
                     values=[t for t, _v in _DIRECTIONS]).grid(
            row=row, column=1, columnspan=3, sticky="w")
        row += 1
        self.vars["via_optimize"] = tk.BooleanVar()
        ttk.Checkbutton(gf, text="Via optimisation at the end (take out every via that can go)",
                        variable=self.vars["via_optimize"]).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=4)
        self.vars["via_optimize_max_flip"] = tk.StringVar()
        ttk.Label(gf, text="Max track moved per via (um, 0 = any):").grid(
            row=row, column=2, sticky="e", padx=4)
        ttk.Entry(gf, textvariable=self.vars["via_optimize_max_flip"], width=9).grid(
            row=row, column=3, sticky="w")

        nf = ttk.Frame(outer)
        nf.pack(fill="x", pady=(8, 0))
        self.nets_label = ttk.Label(nf, text="")
        self.nets_label.pack(side="left")
        ttk.Button(nf, text="Import Net Names...", command=self._import_names).pack(side="right")

        pf = ttk.LabelFrame(outer, text="Passes (saved with the design)", padding=8)
        pf.pack(fill="x", pady=(8, 0))
        for c, (label, key, hint) in enumerate(_PASS_FIELDS):
            self.vars[key] = tk.StringVar()
            ttk.Label(pf, text=label + ":").grid(row=0, column=c * 2, sticky="e", padx=(12 if c else 4, 4))
            ttk.Spinbox(pf, textvariable=self.vars[key], width=7,
                        from_=0, to=(100 if key == "optimize_threshold" else 9999),
                        increment=(0.5 if key == "optimize_threshold" else 1)).grid(
                row=0, column=c * 2 + 1, sticky="w")
            ttk.Label(pf, text=hint, foreground="#666").grid(row=1, column=c * 2 + 1,
                                                             sticky="w")

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
        self.log = tk.Text(lf, height=8, wrap="none", font=self._LOG_FONT)
        sb = ttk.Scrollbar(lf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set, state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ------------------------------------------------------------- state
    def reload(self):
        from ..io.techfile import load_net_names
        rules = self.session.freerouting_rules()
        for _l, key in _NUM_FIELDS:
            self.vars[key].set(f"{float(rules[key]):g}")
        for _l, key, _h in _PASS_FIELDS:
            self.vars[key].set(f"{float(rules[key]):g}")
        self.vars["power_nets"].set(", ".join(rules["power_nets"]))
        self.vars["exclude_nets"].set(", ".join(rules["exclude_nets"]))
        self.vars["snap_90"].set(bool(rules["snap_90"]))
        self.vars["keep_existing"].set(bool(rules["keep_existing"]))
        self.vars["remove_existing"].set(bool(rules.get("remove_existing", False)))
        self.vars["keepout_bodies"].set(bool(rules.get("keepout_bodies", True)))
        mode = str(rules.get("layer_mode", "auto"))
        self.vars["layer_mode"].set(next((t for t, v in _LAYER_MODES if v == mode),
                                         _LAYER_MODES[0][0]))
        fl = str(rules.get("finger_layer", "first"))
        self.vars["finger_layer"].set(next((t for t, v in _FINGER_LAYERS if v == fl),
                                           _FINGER_LAYERS[0][0]))
        ld = str(rules.get("layer_directions", "hv"))
        self.vars["layer_directions"].set(next((t for t, v in _DIRECTIONS if v == ld),
                                               _DIRECTIONS[0][0]))
        self.vars["via_optimize"].set(bool(rules.get("via_optimize", True)))
        self.vars["via_optimize_max_flip"].set(f"{float(rules.get('via_optimize_max_flip', 0)):g}")
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
        for label, key in list(_NUM_FIELDS) + [(l, k) for l, k, _h in _PASS_FIELDS]:
            try:
                v = float(self.vars[key].get())
            except ValueError:
                raise LayoutError(f"{label}: not a number")
            if key in self._ZERO_OK:
                if v < 0:
                    raise LayoutError(f"{label}: must be 0 or more")
            elif v <= 0:
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
        shown = self.vars["finger_layer"].get()
        out["finger_layer"] = next((v for t, v in _FINGER_LAYERS if t == shown), "first")
        shown = self.vars["layer_directions"].get()
        out["layer_directions"] = next((v for t, v in _DIRECTIONS if t == shown), "hv")
        out["via_optimize"] = bool(self.vars["via_optimize"].get())
        try:
            out["via_optimize_max_flip"] = float(self.vars["via_optimize_max_flip"].get() or 0)
        except ValueError:
            raise LayoutError("Max track moved per via: not a number")
        if out["via_optimize_max_flip"] < 0:
            raise LayoutError("Max track moved per via: must be 0 or more")
        if out["against_direction_cost"] < 1:
            raise LayoutError("Against-direction cost: must be 1 or more (1 = no penalty)")
        return out

    def _save(self):
        rules = self.session.set_freerouting_rules(**self._rules())
        app_settings.set_setting(app_settings.FREEROUTING_PATH_KEY,
                                 self.vars["router"].get().strip() or None)
        app_settings.set_setting(app_settings.JAVA_PATH_KEY, self.vars["java"].get().strip() or None)
        return rules

    #: number fields where 0 is meaningful (until done / off)
    _ZERO_OK = ("passes", "optimize_passes")

    _BANNER_FONT = ("Segoe UI", 16, "bold")
    _LOG_FONT = ("Consolas", 12)
    #: banner colours (background) per kind; white text on all of them
    _BANNER_COLOURS = {"run": "#1f5fbf", "ok": "#1e8e3e", "warn": "#d56e00", "err": "#c5221f"}

    def _banner(self, text, kind="run"):
        """Show ``text`` in the big coloured line at the top (kind run /
        ok / warn / err); empty text hides it."""
        if not self._alive():
            return
        box = self.banner_box
        if not text:
            box.pack_forget()
            return
        self.banner.config(text=text, bg=self._BANNER_COLOURS.get(kind, "#1f5fbf"), fg="white")
        self.banner.update_idletasks()
        if kind == "run":
            if not self.progress.winfo_ismapped():
                self.progress.pack(fill="x", pady=(4, 0))
        else:
            self.progress.pack_forget()
        if not box.winfo_ismapped():
            first = box.master.winfo_children()[1]   # above the Freerouting frame
            box.pack(fill="x", pady=(0, 8), before=first)

    def _announce(self, text, kind):
        """The run is over: say so loudly -- banner, title, status bar,
        beep, and bring the window back up even if it was closed."""
        self._banner(text, kind)
        self.title("Autoroute (Freerouting) -- " + text.split(" ")[0])
        self.app.status.set("Freerouting: " + text)
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(1500, lambda: self._alive() and self.attributes("-topmost", False))
            self.focus_force()
            self.bell()
        except tk.TclError:
            pass

    def _fit_screen(self):
        """Never open taller than the screen (less the taskbar): the log
        shrinks, the Passes box and the Route button stay in view."""
        try:
            self.update_idletasks()
            h = min(self.winfo_reqheight(), self.winfo_screenheight() - 100)
            self.geometry(f"{self.winfo_reqwidth()}x{h}")
        except tk.TclError:
            pass

    def _took(self) -> str:
        return self._prog.elapsed() if self._prog is not None else "?"

    def _alive(self) -> bool:
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def _say(self, text):
        if not self._alive():
            return
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    #: Freerouting chatter that says nothing useful to a user (it can
    #: print hundreds of these per pass)
    _NOISE = ("BoxShapeSearchTree", "normalizeTraces", "Deprecated settings path",
              "is deprecated; use")

    def _say_router(self, line):
        if not self._alive() or any(n in line for n in self._NOISE):
            return
        # drop the timestamp / job id prefix Freerouting puts on every line
        import re
        line = re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+\s+", "", line)
        line = re.sub(r"\[[0-9A-F]+\\[0-9A-F]+\]\s*", "", line)
        self._say(line)
        if self._prog is not None and self._prog.feed(line):
            self._show_progress()

    def _show_progress(self):
        """Percent complete on the bar, the banner, the title and the
        main window's status bar."""
        p = self._prog
        if p is None or not self._alive() or not self._running:
            return
        self.progress["value"] = p.percent
        self._banner(p.text(), "run")
        self.title(f"Autoroute (Freerouting) -- {int(round(p.percent))}%")
        self.app.status.set("Freerouting: " + p.text())

    def _tick(self):
        """Once a second while routing: keep the elapsed time moving."""
        if self._running and self._alive():
            self._show_progress()
            self.after(1000, self._tick)

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
            return None
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
        return st

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
            cmd = self.session.freeroute_command(router, dsn, ses, rules,
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
        from ..io.route_progress import RouteProgress
        self._prog = RouteProgress(max_passes=rules["passes"],
                                   opt_passes=rules["optimize_passes"])
        self.progress["value"] = 0
        self._banner(f"Routing 0% -- {sm.nets} nets  (you can close this window -- it comes "
                     "back when the route finishes)", "run")
        self.title("Autoroute (Freerouting) -- 0%")
        self.after(1000, self._tick)

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
        if not self._alive():
            return
        self.route_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self._hide_when_done = False
        if err is not None:
            self._say(f"Freerouting failed to start: {err}")
            self._announce(f"FAILED to start: {err}", "err")
        elif self._cancel:
            self._say("Stopped -- nothing imported.")
            self._announce("Stopped -- nothing imported", "err")
        elif not os.path.exists(ses):
            self._say(f"No session file was written (exit code {code}).")
            self._announce(f"FAILED -- no session file written (exit code {code})", "err")
        else:
            st = self._do_import(ses)
            vias = ""
            if st is not None:
                vo = self._optimize_vias()
                if vo is not None:
                    vias = f"; via optimisation {vo['before']} -> {vo['after']} vias"
            if st is None:
                self._announce("FAILED -- the result could not be imported (see the log)", "err")
            elif st["unrouted"]:
                self._announce(f"FINISHED in {self._took()} -- {st['complete']} of {st['nets']} "
                               f"nets complete ({100 * st['complete'] // max(st['nets'], 1)}%); "
                               f"unrouted: {', '.join(st['unrouted'])}{vias}", "warn")
            else:
                self._announce(f"FINISHED in {self._took()} -- all {st['nets']} nets complete (100%)  "
                               f"({st['wires']} tracks, {st['vias']} vias{vias})", "ok")

    def _optimize_vias(self):
        """With "Via optimisation at the end" on a two-layer route: take
        out the vias that can go (Session.optimize_vias). None if off."""
        try:
            rules = self.session.freerouting_rules()
            if not rules.get("via_optimize") or self.session._single_layer_route(rules):
                return None
            self._say("Via optimisation: looking for vias that can go...")
            import time as _time
            last = [0.0]

            def tick(i, n, gone):
                # keep the window painting and the status bar moving
                now = _time.monotonic()
                if now - last[0] > 0.25:
                    last[0] = now
                    self.app.status.set(f"Via optimisation: {gone} via(s) out so far "
                                        f"({i}/{n} looked at this pass)")
                    try:
                        self.update_idletasks()
                    except tk.TclError:
                        pass
            vo = self.session.optimize_vias(progress=tick)
        except Exception as e:
            self._say(f"Via optimisation failed: {e}")
            return None
        self._say(f"Via optimisation: {vo['before']} -> {vo['after']} vias "
                  f"({vo['moved']} track(s) moved to the other conductor)")
        return vo

    def _stop(self):
        self._cancel = True

    def close(self):
        try:
            self._save()
        except Exception:
            pass
        if self._running:
            # keep routing in the background: hide the window, import the
            # result when Freerouting finishes (progress on the status bar);
            # Stop cancels instead
            self._hide_when_done = True
            self.withdraw()
            self.app.status.set("Freerouting: still routing in the background -- "
                                "its window pops back up when it finishes")
            return
        self.destroy()


def open_freerouting_window(app):
    """Tools > Autoroute (Freerouting)... . One window per App."""
    win = getattr(app, "_freerouting_window", None)
    if win is not None and win.winfo_exists():
        try:
            win._hide_when_done = False
            win.deiconify()
            if not win._running:
                win.reload()
            win.lift()
            win.focus_set()
            return win
        except tk.TclError:
            # half torn down (closed while being reopened): start afresh
            if win._running:
                raise
            try:
                win.destroy()
            except tk.TclError:
                pass
    win = FreeroutingWindow(app, app)
    app._freerouting_window = win
    return win
