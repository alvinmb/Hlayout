# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tools > Autoroute...: THE autorouter -- the chip-and-wire hybrid router
(router/, HybridRouterCli; see hlayout.scripting.hybridroute).

One window for every case: one conductor with wire-bond jumpers as the only
crossings, or two conductors with dielectric crossovers (vias); power nets
at their own width; 90-degree routing; a preferred direction per conductor;
existing tracks kept and joined on to, or ripped up; rip-up-and-reroute
passes; then crossover optimisation, shorten tracks and remove jogs.

The rules are the design's Autoroute rules (Session.freerouting_rules() --
the name is historical) plus the router's own settings
(hybridroute.load_settings()); both are saved with the design."""

import threading
import tkinter as tk
from tkinter import filedialog, ttk

from ..core.errors import LayoutError
from . import dialogs

_NUM_FIELDS = (
    ("Signal width (um)", "signal_width"),
    ("Power width (um)", "power_width"),
    ("Clearance (um)", "clearance"),
    ("Die keep-out (um)", "die_keepout"),
    ("Signal via pad (um)", "signal_via"),
    ("Power via pad (um)", "power_via"),
    ("Bond finger (um)", "bond_finger"),
    ("Against-direction cost", "against_direction_cost"),
)
_OWN_FIELDS = (
    ("Rip-up passes", "ripup_passes", "reroute with the failed nets first; best attempt kept"),
    ("Grid (um)", "grid", "0 = automatic; another grid can route what one leaves"),
    ("Max jumpers", "max_jumpers", "0 = no limit"),
    ("Jumper cost (mm)", "jumper_cost", "track length a jumper is worth"),
    ("Crossover cost (mm)", "crossover_cost", "track length a crossover (via) is worth"),
    ("Crossover dielectric side (um)", "xo_diel_side",
     "dielectric past the COND2 bridge, each side"),
    ("Crossover dielectric gap (um)", "xo_diel_gap",
     "dielectric end stops this short of the contact"),
    ("Crossover contact (um)", "xo_contact", "COND1 contact square; 0 = track width"),
    ("Nudge passes", "nudge_passes",
     "PathFinder: move unlocked parts to finish what is left open; 0 = off"),
    ("Nudge max move (um)", "nudge_max", "the most any one part moves in total"),
)
_LAYER_MODES = (("Auto (one conductor if nothing is on the 2nd)", "auto"),
                ("One conductor -- wire-bond jumpers are the only crossings", "single"),
                ("Two conductors -- dielectric crossovers (vias)", "two"))
_DIRECTIONS = (("1st conductor horizontal, 2nd vertical", "hv"),
               ("1st conductor vertical, 2nd horizontal", "vh"),
               ("No preferred direction", "off"))
_TRACK_CAPS = (("Round (smooth -- thick-film default)", "round"),
               ("Square (butt ends squared off)", "square"),
               ("Flush (stops at the end point)", "flush"))
_ENGINES = (("Auto -- picks the engine for the board (recommended)", "auto"),
            ("PathFinder -- negotiated congestion; local crossovers, jumpers", "pathfinder"),
            ("Freerouting -- gridless push-and-shove; full dielectric sheet", "freerouting"),
            ("Shape -- gridless Python (planar + printed-jumper crossovers)", "shape"),
            ("Hybrid grid router -- Java (HybridRouterCli)", "java"))


class AutorouteWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Autoroute")
        self.transient(app)
        self.minsize(640, 420)
        self._cancel = False
        self._result = None
        self._lines = []
        self.v = {}
        f = ttk.Frame(self, padding=8)
        f.pack(fill="both", expand=True)
        self.tabs = ttk.Notebook(f)
        self.tabs.pack(fill="both", expand=True)

        def tab(title):
            t = ttk.Frame(self.tabs, padding=8)
            self.tabs.add(t, text=title)
            return t

        def own_rows(parent, keys, start=0):
            by = {k: (lab, note) for lab, k, note in _OWN_FIELDS}
            for r, key in enumerate(keys, start):
                lab, note = by[key]
                self.v[key] = tk.StringVar()
                ttk.Label(parent, text=lab + ":").grid(row=r, column=0, sticky="e", padx=4, pady=2)
                ttk.Entry(parent, textvariable=self.v[key], width=9).grid(row=r, column=1, sticky="w")
                ttk.Label(parent, text=note, foreground="#666", wraplength=330,
                          justify="left").grid(row=r, column=2, sticky="w", padx=6)
            return start + len(keys)

        # ---- Rules
        gf = tab("Rules")
        ttk.Label(gf, text="Saved with the design; shared with the Freerouting engine.",
                  foreground="#666").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        self.v["layer_mode"] = tk.StringVar()
        ttk.Label(gf, text="Conductors:").grid(row=1, column=0, sticky="e", padx=4, pady=2)
        ttk.Combobox(gf, textvariable=self.v["layer_mode"], state="readonly", width=48,
                     values=[t for t, _v in _LAYER_MODES]).grid(row=1, column=1, columnspan=3,
                                                                 sticky="w")
        for i, (label, key) in enumerate(_NUM_FIELDS):
            self.v[key] = tk.StringVar()
            r, c = divmod(i, 2)
            ttk.Label(gf, text=label + ":").grid(row=r + 2, column=c * 2, sticky="e", padx=4, pady=2)
            ttk.Entry(gf, textvariable=self.v[key], width=9).grid(row=r + 2, column=c * 2 + 1,
                                                                 sticky="w")
        row = 2 + (len(_NUM_FIELDS) + 1) // 2
        for key, label in (("power_nets", "Power nets:"), ("exclude_nets", "Leave unrouted:")):
            self.v[key] = tk.StringVar()
            ttk.Label(gf, text=label).grid(row=row, column=0, sticky="e", padx=4, pady=2)
            ttk.Entry(gf, textvariable=self.v[key], width=50).grid(row=row, column=1,
                                                                  columnspan=3, sticky="we")
            row += 1
        self.v["layer_directions"] = tk.StringVar()
        ttk.Label(gf, text="Track directions:").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Combobox(gf, textvariable=self.v["layer_directions"], state="readonly", width=48,
                     values=[t for t, _v in _DIRECTIONS]).grid(row=row, column=1, columnspan=3,
                                                                sticky="w")
        row += 1
        self.v["track_cap"] = tk.StringVar()
        ttk.Label(gf, text="Track ends:").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Combobox(gf, textvariable=self.v["track_cap"], state="readonly", width=48,
                     values=[t for t, _v in _TRACK_CAPS]).grid(row=row, column=1, columnspan=3,
                                                                sticky="w")
        row += 1
        self.v["snap_90"] = tk.BooleanVar()
        ttk.Checkbutton(gf, text="90-degree routing only (horizontal and vertical tracks)",
                        variable=self.v["snap_90"]).grid(row=row, column=0, columnspan=4,
                                                        sticky="w", padx=4, pady=(4, 0))
        row += 1
        self.v["keep_existing"] = tk.BooleanVar()
        ttk.Checkbutton(gf, text="Keep what is already drawn and route only what is open\n"
                        "(else rip everything up and route from scratch)",
                        variable=self.v["keep_existing"]).grid(row=row, column=0, columnspan=4,
                                                              sticky="w", padx=4)

        # ---- Router
        of = tab("Router")
        self.v["engine"] = tk.StringVar()
        ttk.Label(of, text="Engine:").grid(row=0, column=0, sticky="e", padx=4, pady=(0, 6))
        ttk.Combobox(of, textvariable=self.v["engine"], state="readonly", width=52,
                     values=[t for t, _v in _ENGINES]).grid(row=0, column=1, columnspan=2,
                                                            sticky="w", pady=(0, 6))
        n_ = own_rows(of, ("ripup_passes", "grid", "max_jumpers", "jumper_cost", "crossover_cost"), 1)
        self.v["under_chips"] = tk.BooleanVar()
        ttk.Checkbutton(of, text="Allow tracks under chip parts, between their terminals "
                        "(1st conductor only; spacing to the terminals still applies)",
                        variable=self.v["under_chips"]).grid(row=n_, column=0, columnspan=3,
                                                            sticky="w", padx=4, pady=(6, 0))
        self.v["full_dielectric"] = tk.BooleanVar()
        ttk.Checkbutton(of, text="Full dielectric (PathFinder, two conductors): the 2nd conductor "
                        "is a routing layer like the 1st,\nunder one dielectric sheet, joined by "
                        "vias; Crossover cost is what a via is worth",
                        variable=self.v["full_dielectric"]).grid(row=n_ + 1, column=0, columnspan=3,
                                                                sticky="w", padx=4, pady=(4, 0))

        # ---- Crossovers
        xf = tab("Crossovers")
        ttk.Label(xf, text="A crossover is a 2nd-conductor bridge on its own printed dielectric "
                  "strip; these set the strip's size.", foreground="#666", wraplength=560,
                  justify="left").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        own_rows(xf, ("xo_diel_side", "xo_diel_gap", "xo_contact"), 1)

        # ---- Nudge
        uf = tab("Nudge")
        ttk.Label(uf, text="PathFinder only. When connections are left open, unlocked parts are "
                  "moved apart a little and the board routed again; a pass that routes no more "
                  "is undone. LOCK <refs> (command bar) keeps parts where they are.",
                  foreground="#666", wraplength=560, justify="left").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        own_rows(uf, ("nudge_passes", "nudge_max"), 1)
        self.locked_label = ttk.Label(uf, text="", foreground="#555")
        self.locked_label.grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # ---- Clean-up
        cf = tab("Clean-up")
        for key, label in (("via_optimize", "Take out every crossover (via) that can go "
                                             "(two conductors, Freerouting engine)"),
                           ("shorten_tracks", "Shorten tracks (straighten detours, pull corners "
                                              "tight; Freerouting engine)"),
                           ("remove_jogs", "Remove jogs (line up short sideways steps)")):
            self.v[key] = tk.BooleanVar()
            ttk.Checkbutton(cf, text=label, variable=self.v[key]).pack(anchor="w", pady=2)

        # ---- Log
        lf = tab("Log")
        self.log_tab = lf
        logf = ttk.Frame(lf)
        logf.pack(fill="both", expand=True)
        self.log = tk.Text(logf, width=70, height=16, wrap="none", font=("Consolas", 12))
        logsb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=logsb.set)
        logsb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)

        # always in view: the outcome, the net names, the buttons
        self.result = ttk.Label(f, text="", wraplength=600, justify="left")
        self.result.pack(anchor="w", pady=(6, 0))
        nf = ttk.Frame(f)
        nf.pack(fill="x", pady=(4, 0))
        self.nets_label = ttk.Label(nf, text="", foreground="#555")
        self.nets_label.pack(side="left")
        ttk.Button(nf, text="Import Net Names...", command=self._import_names).pack(side="right")
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(6, 0))
        self.run_btn = ttk.Button(b, text="Route", command=self.run)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(b, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        ttk.Button(b, text="Close", command=self.close).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.reload()

    # ---------------------------------------------------------------- state
    @property
    def session(self):
        return self.app.session

    def reload(self):
        from ..io.techfile import load_net_names
        from ..scripting.hybridroute import load_settings
        R = self.session.freerouting_rules()
        H = load_settings(self.session.library)
        for _l, key in _NUM_FIELDS:
            self.v[key].set(f"{float(R[key]):g}")
        for _l, key, _n in _OWN_FIELDS:
            self.v[key].set(f"{float(H[key]):g}")
        self.v["under_chips"].set(bool(H.get("under_chips", False)))
        self.v["full_dielectric"].set(bool(H.get("full_dielectric", False)))
        eng = str(H.get("engine", "auto"))
        self.v["engine"].set(next((t for t, v in _ENGINES if v == eng), _ENGINES[0][0]))
        self.v["power_nets"].set(", ".join(R.get("power_nets") or []))
        self.v["exclude_nets"].set(", ".join(R.get("exclude_nets") or []))
        mode = str(R.get("layer_mode", "auto"))
        self.v["layer_mode"].set(next((t for t, v in _LAYER_MODES if v == mode), _LAYER_MODES[0][0]))
        d = str(R.get("layer_directions", "hv"))
        self.v["layer_directions"].set(next((t for t, v in _DIRECTIONS if v == d),
                                            _DIRECTIONS[0][0]))
        cap = str(R.get("track_cap", "round"))
        self.v["track_cap"].set(next((t for t, v in _TRACK_CAPS if v == cap), _TRACK_CAPS[0][0]))
        for key, default in (("snap_90", True), ("keep_existing", True), ("via_optimize", True),
                             ("shorten_tracks", True), ("remove_jogs", True)):
            self.v[key].set(bool(R.get(key, default)))
        try:
            lk = sorted(self.session.locked_parts(), key=lambda r: (len(r), r))
            self.locked_label.config(text="Locked: " + (", ".join(lk) if lk else "none"))
        except Exception:
            pass
        names = load_net_names(self.session.library)
        nets = {n for n in names.values() if n and not n.lower().startswith("unconnected")}
        self.nets_label.config(text=f"{len(names)} named pads, {len(nets)} nets"
                               if names else "No net names -- read the netlist first "
                                             "(Tools > Read Netlist...)")

    def _collect(self):
        """(rules, own settings) from the fields; ValueError on a bad number."""
        rules, own = {}, {}
        for _l, key in _NUM_FIELDS:
            rules[key] = float(self.v[key].get())
            if rules[key] <= 0:
                raise ValueError(key)
        for _l, key, _n in _OWN_FIELDS:
            own[key] = float(self.v[key].get() or 0)
            if own[key] < 0:
                raise ValueError(key)
        for key in ("ripup_passes", "grid", "max_jumpers", "nudge_passes"):
            own[key] = int(own[key])
        if own["jumper_cost"] <= 1 or own["crossover_cost"] <= 1:
            raise ValueError("cost")
        own["engine"] = next((v for t, v in _ENGINES if t == self.v["engine"].get()), "auto")
        own["under_chips"] = bool(self.v["under_chips"].get())
        own["full_dielectric"] = bool(self.v["full_dielectric"].get())
        for key in ("power_nets", "exclude_nets"):
            rules[key] = [x.strip() for x in self.v[key].get().split(",") if x.strip()]
        rules["layer_mode"] = next((v for t, v in _LAYER_MODES if t == self.v["layer_mode"].get()),
                                   "auto")
        rules["layer_directions"] = next(
            (v for t, v in _DIRECTIONS if t == self.v["layer_directions"].get()), "hv")
        rules["track_cap"] = next(
            (v for t, v in _TRACK_CAPS if t == self.v["track_cap"].get()), "round")
        for key in ("snap_90", "keep_existing", "via_optimize", "shorten_tracks", "remove_jogs"):
            rules[key] = bool(self.v[key].get())
        rules["remove_existing"] = not rules["keep_existing"]
        return rules, own

    def _say(self, text):
        self.log.insert("end", text + "\n")
        self.log.see("end")

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

    # ------------------------------------------------------------------ run
    def run(self):
        from ..scripting import hybridroute as H
        if not getattr(self, "_nudging", False):
            self._nudge = {"pass": 0, "best": None, "last": {}, "total": {}}
        try:
            rules, own = self._collect()
        except ValueError:
            dialogs.show_error("Autoroute", "Widths, clearance and via sizes must be positive "
                               "numbers; passes, grid and max jumpers 0 or more; the jumper and "
                               "crossover costs more than 1.", parent=self)
            return
        s = self.session
        try:
            s.set_freerouting_rules(**rules)
            H.save_settings(s.library, own)
            layers = H.layers_for(s)
            design, mapping = H.export_design(
                s, self.app.canvas.cell_name, layers=layers, max_jumpers=own["max_jumpers"],
                jumper_cost=own["jumper_cost"], crossover_cost=own["crossover_cost"],
                grid=own["grid"], ripup_passes=own["ripup_passes"],
                keep_existing=rules["keep_existing"])
        except LayoutError as e:
            dialogs.show_error("Autoroute", str(e), parent=self)
            return
        if not getattr(self, "_nudging", False):
            self.log.delete("1.0", "end")
        try:
            self.tabs.select(self.log_tab)
        except Exception:
            pass
        self.result.config(text="")
        self._say(f"{len(design['dies'])} die(s), {len(design['components'])} two-pin part(s), "
                  f"{len(design['substratePads'])} pad(s), {len(design['wires'])} fixed bond(s); "
                  f"{'one conductor + jumpers' if layers == 1 else 'two conductors'}"
                  f"{', 90-degree' if rules['snap_90'] else ''}, "
                  f"{own['ripup_passes']} rip-up pass(es)")
        k = mapping.get("kept") or {}
        if rules["keep_existing"]:
            self._say(f"kept: {k.get('tracks', 0)} piece(s) of existing copper, "
                      f"{k.get('jumpers', 0)} jumper(s); {k.get('joined', 0)} group(s) of pads "
                      "already joined")
        if mapping["unbonded"]:
            self._say("die pads the router will bond: " + ", ".join(mapping["unbonded"]))
        self._cancel, self._result, self._lines = False, None, []
        engine = own.get("engine", "auto")
        if engine == "auto":
            engine, why = H.choose_engine(s, design, mapping, layers)
            self._say(f"Auto engine: {engine} -- {why}")
        fr_job = None
        if engine == "freerouting":
            # Freerouting proper: the DSN written here, the router on the
            # worker thread, the session read back in _poll (main thread)
            import os
            import tempfile
            from ..io import app_settings
            from ..io.specctra import find_freerouting
            exe = find_freerouting(app_settings.get_setting(app_settings.FREEROUTING_PATH_KEY))
            if not exe:
                dialogs.show_error("Autoroute", "Freerouting not found -- install it "
                                   "(freerouting.app) and set its path in Setup > "
                                   "Freerouting..., or pick another engine.", parent=self)
                return
            wd = os.path.join(tempfile.gettempdir(), "hlayout_freerouting")
            os.makedirs(wd, exist_ok=True)
            name = mapping["cell"]
            dsn, ses = os.path.join(wd, f"{name}.dsn"), os.path.join(wd, f"{name}.ses")
            if os.path.exists(ses):
                os.remove(ses)
            FR = s.freerouting_rules()
            FR.update(layer_mode="two", remove_existing=not rules["keep_existing"],
                      keep_existing=rules["keep_existing"])
            s.export_dsn(dsn, cell_name=name, **FR)
            java = app_settings.get_setting(app_settings.JAVA_PATH_KEY)
            cmd = s.freeroute_command(exe, dsn, ses, FR, java=java)
            # stage 1: route only (no optimiser) to its own file, so Stop
            # during the long optimisation still has a routing to read back
            ses0 = os.path.join(wd, f"{name}_routed.ses")
            if os.path.exists(ses0):
                os.remove(ses0)
            cmd0 = s.freeroute_command(exe, dsn, ses0, dict(FR, optimize_passes=0), java=java)
            fr_job = (cmd, ses, FR, name, cmd0, ses0)
            self._say(f"Freerouting: {FR.get('passes')} pass(es), "
                      f"{FR.get('optimize_passes')} optimise pass(es)")
        self._opts = (rules, layers, engine)
        self._fr_job = fr_job
        self._min_space = design["rules"]["conductor"]["minSpaceUm"]
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

        engine = self._opts[2]

        def work():
            try:
                if engine == "freerouting":
                    from ..io.specctra import run_freerouting
                    cmd, ses, FR_, _n, cmd0, ses0 = fr_job
                    self._lines.append("Freerouting stage 1: routing (no optimiser)")
                    code, tail = run_freerouting(cmd0, ses0, timeout=None,
                                                 on_line=self._lines.append,
                                                 cancel=lambda: self._cancel)
                    if not self._cancel and int(FR_.get("optimize_passes") or 0) > 0 \
                            and os.path.exists(ses0):
                        self._lines.append("Freerouting stage 2: routing + optimiser -- Stop "
                                           "keeps the stage 1 routing")
                        code, tail = run_freerouting(cmd, ses, timeout=None,
                                                     on_line=self._lines.append,
                                                     cancel=lambda: self._cancel)
                    self._result = ("fr", (code, tail), mapping)
                elif engine == "pathfinder":
                    routed = H.pf_route(design, mapping, on_line=self._lines.append,
                                        cancel=lambda: self._cancel)
                    self._result = ("ok", (0, routed, [], {}), mapping)
                elif engine == "shape":
                    from ..scripting import shape_router as SR
                    self._lines.append("shape router: gridless planar + local-dielectric crossovers")
                    routed = SR.route(design, max_jumpers=own["max_jumpers"],
                                      local_dielectric=mapping.get("local_diel", -1) >= 0,
                                      cond2_keepouts=mapping.get("local_keepouts"),
                                      passes=1 + max(0, int(own["ripup_passes"])),
                                      on_line=self._lines.append,
                                      cancel=lambda: self._cancel)
                    self._result = ("ok", (0, routed, [], {}), mapping)
                else:
                    self._result = ("ok", H.run_router(design, timeout=1800,
                                                       on_line=self._lines.append,
                                                       cancel=lambda: self._cancel), mapping)
            except BaseException as e:
                self._result = ("err", e, None)

        threading.Thread(target=work, daemon=True).start()
        self._poll()

    def _poll(self):
        while self._lines:
            line = self._lines.pop(0)
            if "WARNING COMPONENT_NO_OBSTACLE" not in line:
                self._say(line)
        if self._result is None:
            self.after(200, self._poll)
            return
        self.run_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        kind, res, mapping = self._result
        if kind == "err":
            self.result.config(text=f"Failed: {res}", foreground="#c5221f")
            return
        if kind == "fr":
            self._finish_freerouting(res)
            return
        code, routed, tail, paths = res
        if routed is None:
            self.result.config(text="The router wrote no result" + (" (stopped)" if self._cancel
                                                                    else f" (exit {code})"),
                               foreground="#c5221f")
            return
        from ..scripting import hybridroute as H
        rules, layers, engine = self._opts
        st = H.apply_routed(self.session, routed, mapping,
                            remove_existing=not rules["keep_existing"])
        if engine in ("shape", "pathfinder"):
            self._say("removing jogs ...")
            self.update_idletasks()
            cl = H.tidy(self.session, mapping["cell"], rules["remove_jogs"], rules["snap_90"],
                        self._min_space)
            d = cl.get("dejog") or {}
            if "error" in d:
                self._say(f"jog removal failed: {d['error']}")
            elif d:
                self._say(f"jogs: {d.get('found', 0)} found, {d.get('jogs', 0)} removed "
                          f"{d.get('why') or ''}")
        else:
            self._say("cleaning up ...")
            self.update_idletasks()
            cl = H.finish(self.session, mapping["cell"], rules["shorten_tracks"],
                          rules["remove_jogs"], rules["snap_90"], rules["via_optimize"], layers)
        self.app.refresh_all()
        rs = st["stats"]
        xovers = st.get("crossovers", 0) + st.get("windows", 0)
        txt = ("Stopped -- best so far applied. " if rs.get("stopped") else "") + (
               f"Routed {rs.get('routed', '?')} of {rs.get('connections', '?')} connections: "
               f"{st['traces']} tracks, {xovers} crossover(s), {st['jumpers']} jumper(s), "
               f"{st['bonds']} new bond(s)")
        txt += (f"; {st['removed_tracks']} old track(s) taken out." if st["removed_tracks"]
                else ".")
        v = cl.get("vias")
        if isinstance(v, dict) and "before" in v and v["before"] != v.get("after"):
            txt += f" Crossovers {v['before']} -> {v['after']}."
        for key, field, label in (("shorten", "tracks", "track(s) shortened"),
                                  ("dejog", "jogs", "jog(s) removed")):
            r = cl.get(key)
            if isinstance(r, dict) and r.get(field):
                txt += f" {r[field]} {label}."
        if st.get("refused_jumpers"):
            txt += ("\nJumpers refused (over a part): " + ", ".join(
                f"{x['net']} over {x['over']}" for x in st["refused_jumpers"]))
        if st["unrouted"]:
            txt += f"\nStill unrouted ({len(st['unrouted'])}): " + "; ".join(
                H.unrouted_text(u) for u in st["unrouted"][:10]) + (
                " ..." if len(st["unrouted"]) > 10 else "")
        try:
            shorts = self.session.find_shorts(cell_name=mapping["cell"])
        except Exception:
            shorts = []
        for x in shorts:
            self._say("SHORT CIRCUIT: " + " + ".join(x["nets"]))
        if shorts:
            txt += f"\n{len(shorts)} SHORT CIRCUIT(S) -- see the log (SHORTS lists them)."
        self._say("short-circuit check: " + (f"{len(shorts)} found" if shorts else "no nets joined"))
        self.result.config(text=txt, foreground="#c5221f" if shorts else
                           ("#1e8e3e" if not st["unrouted"] else "#d56e00"))
        try:
            self.app.status.set(txt.splitlines()[0])
        except Exception:
            pass
        if engine == "pathfinder":
            self._nudge_after(st, routed, mapping)

    def _finish_freerouting(self, res):
        """Read Freerouting's session back (main thread) and run the
        clean-ups, as FREEROUTE RUN does; then the short-circuit check."""
        import os
        code, tail = res
        cmd, ses, FR, name, _cmd0, ses0 = self._fr_job
        s = self.session
        if not os.path.exists(ses):
            if os.path.exists(ses0):
                self._say("using the stage 1 routing (Freerouting's optimiser "
                          + ("stopped)" if self._cancel else "wrote nothing)"))
                ses = ses0
            else:
                self.result.config(text="Freerouting wrote no result" + (
                    " (stopped)" if self._cancel else f" (exit {code})"), foreground="#c5221f")
                return
        self._say("reading the routing back ...")
        self.update_idletasks()
        try:
            out = s.import_ses(ses, cell_name=name, **FR)
            if FR.get("via_optimize"):
                vo = s.optimize_vias(cell_name=name, **FR)
                self._say(f"via optimisation: {vo['before']} -> {vo['after']} via(s)")
            if FR.get("shorten_tracks", True):
                sh = s.shorten_tracks(cell_name=name, **FR)
                self._say(f"shortened: {sh['tracks']} track(s)")
            if FR.get("remove_jogs", True):
                dj = s.remove_jogs(cell_name=name, **FR)
                self._say(f"jogs removed: {dj['jogs']}")
        except LayoutError as e:
            self.result.config(text=f"Failed: {e}", foreground="#c5221f")
            return
        self.app.canvas.recalc_sli()
        self.app.refresh_all()
        nets = out.get("nets", 0)
        txt = (("Stopped -- " if self._cancel else "") +
               f"Freerouting: {out.get('complete', 0)} of {nets} nets complete, "
               f"{out.get('wires', 0)} "
               f"track(s), {out.get('vias', 0)} via(s).")
        un = out.get("unrouted") or []
        if un:
            txt += f"\nStill unrouted ({len(un)}): " + ", ".join(un[:12]) + (
                " ..." if len(un) > 12 else "")
        shorts = s.find_shorts(cell_name=name)
        for x in shorts:
            self._say("SHORT CIRCUIT: " + " + ".join(x["nets"]))
        if shorts:
            txt += f"\n{len(shorts)} SHORT CIRCUIT(S) -- see the log (SHORTS lists them)."
        self._say("short-circuit check: " + (f"{len(shorts)} found" if shorts else
                                             "no nets joined"))
        self.result.config(text=txt, foreground="#c5221f" if shorts else
                           ("#1e8e3e" if not un else "#d56e00"))
        try:
            self.app.status.set(txt.splitlines()[0])
        except Exception:
            pass

    # ------------------------------------------------------------- nudge
    def _nudge_after(self, st, routed, mapping):
        """After a PathFinder route: keep the best result; if connections are
        still open and nudge passes are left, move unlocked parts out of their
        way (scripting.nudge) and route again. A pass that routes no more than
        the best has its moves undone and the best routing put back."""
        from ..scripting import hybridroute as H
        from ..scripting import nudge as NG
        N = getattr(self, "_nudge", None) or {"pass": 0, "best": None, "last": {}, "total": {}}
        self._nudge = N
        s, cell = self.session, mapping["cell"]
        own = H.load_settings(s.library)
        passes, cap = int(own.get("nudge_passes", 0) or 0), float(own.get("nudge_max", 500) or 0)
        n = int(st["stats"].get("routed", 0))
        best = N["best"]
        if best is not None and n <= best["n"]:
            self._say(f"nudge pass {N['pass']}: {n} routed, no better than {best['n']} -- "
                      "moves undone, best routing put back")
            H.unroute(s, cell)
            NG.apply(s, cell, {r: (-dx, -dy) for r, (dx, dy) in N["last"].items()})
            H.apply_routed(s, best["routed"], best["mapping"], remove_existing=True)
            H.tidy(s, cell, True, best["routed"]["design"]["routing"]["orthogonal"],
                   best["routed"]["design"]["rules"]["conductor"]["minSpaceUm"])
            self._nudge_report(best["n"], best["conns"])
            self.app.refresh_all()
            return
        if best is not None:
            for r, (dx, dy) in N["last"].items():
                tx, ty = N["total"].get(r, (0.0, 0.0))
                N["total"][r] = (tx + dx, ty + dy)
        N["best"] = {"n": n, "conns": int(st["stats"].get("connections", 0)),
                     "routed": routed, "mapping": mapping}
        N["last"] = {}
        if not st.get("unrouted") or N["pass"] >= passes or passes <= 0:
            if N["pass"]:
                self._nudge_report(n, N["best"]["conns"])
            return
        traces = [(t["net"], float(t["widthUm"]), [(q["x"], q["y"]) for q in t["points"]])
                  for t in routed.get("traces") or []]
        try:
            pl = NG.plan(routed["design"], routed.get("unrouted") or [],
                         locked=s.locked_parts(cell), max_move=cap, traces=traces)
        except Exception as e:
            self._say(f"nudge: {e}")
            return
        moves = {}
        for r, (dx, dy) in pl["moves"].items():
            tx, ty = N["total"].get(r, (0.0, 0.0))
            if (tx + dx) ** 2 + (ty + dy) ** 2 <= cap * cap + 1e-6:
                moves[r] = (dx, dy)
        for w in pl["why"]:
            self._say("  " + w)
        if not moves:
            self._say("nudge: no unlocked part can move to help -- stopping")
            if N["pass"]:
                self._nudge_report(n, N["best"]["conns"])
            return
        N["pass"] += 1
        N["last"] = moves
        self._say(f"nudge pass {N['pass']}: " + ", ".join(
            f"{r} {dx / 1000:+.2f},{dy / 1000:+.2f} mm" for r, (dx, dy) in moves.items())
            + " -- routing again")
        H.unroute(s, cell)
        NG.apply(s, cell, moves)
        self.app.refresh_all()
        self._nudging = True
        try:
            self.run()
        finally:
            self._nudging = False

    def _nudge_report(self, n, conns):
        tot = getattr(self, "_nudge", {}).get("total") or {}
        moved = ", ".join(f"{r} {dx / 1000:+.2f},{dy / 1000:+.2f} mm"
                          for r, (dx, dy) in sorted(tot.items()) if dx or dy)
        txt = (f"\nNudge: {n} of {conns} routed; parts moved: {moved}" if moved
               else f"\nNudge: {n} of {conns} routed; no part moved")
        self._say(txt.strip())
        try:
            self.result.config(text=self.result.cget("text") + txt)
        except Exception:
            pass

    def stop(self):
        self._cancel = True

    def close(self):
        self._cancel = True
        self.destroy()


def open_autoroute_window(app):
    w = getattr(app, "_autoroute_window", None)
    if w is not None and w.winfo_exists():
        w.reload()
        w.deiconify()
        w.lift()
        return w
    w = AutorouteWindow(app)
    app._autoroute_window = w
    return w
