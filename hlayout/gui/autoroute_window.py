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
_ENGINES = (("Shape -- gridless Python (planar + printed-jumper crossovers)", "shape"),
            ("Freerouting -- grid, Java", "java"))


class AutorouteWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Autoroute")
        self.transient(app)
        self.minsize(1040, 540)
        self._cancel = False
        self._result = None
        self._lines = []
        self.v = {}
        f = ttk.Frame(self, padding=10)
        f.pack(fill="both", expand=True)
        cols = ttk.Frame(f)
        cols.pack(fill="both", expand=True)
        left = ttk.Frame(cols)
        left.pack(side="left", fill="y")
        right = ttk.Frame(cols)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        gf = ttk.LabelFrame(left, text="Rules (saved with the design)", padding=6)
        gf.pack(fill="x")
        self.v["layer_mode"] = tk.StringVar()
        ttk.Label(gf, text="Conductors:").grid(row=0, column=0, sticky="e", padx=4, pady=2)
        ttk.Combobox(gf, textvariable=self.v["layer_mode"], state="readonly", width=56,
                     values=[t for t, _v in _LAYER_MODES]).grid(row=0, column=1, columnspan=3,
                                                                 sticky="w")
        for i, (label, key) in enumerate(_NUM_FIELDS):
            self.v[key] = tk.StringVar()
            r, c = divmod(i, 2)
            ttk.Label(gf, text=label + ":").grid(row=r + 1, column=c * 2, sticky="e", padx=4, pady=2)
            ttk.Entry(gf, textvariable=self.v[key], width=9).grid(row=r + 1, column=c * 2 + 1,
                                                                 sticky="w")
        row = 1 + (len(_NUM_FIELDS) + 1) // 2
        for key, label in (("power_nets", "Power nets:"), ("exclude_nets", "Leave unrouted:")):
            self.v[key] = tk.StringVar()
            ttk.Label(gf, text=label).grid(row=row, column=0, sticky="e", padx=4, pady=2)
            ttk.Entry(gf, textvariable=self.v[key], width=58).grid(row=row, column=1,
                                                                  columnspan=3, sticky="we")
            row += 1
        self.v["layer_directions"] = tk.StringVar()
        ttk.Label(gf, text="Track directions:").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Combobox(gf, textvariable=self.v["layer_directions"], state="readonly", width=56,
                     values=[t for t, _v in _DIRECTIONS]).grid(row=row, column=1, columnspan=3,
                                                                sticky="w")
        row += 1
        self.v["track_cap"] = tk.StringVar()
        ttk.Label(gf, text="Track ends:").grid(row=row, column=0, sticky="e", padx=4, pady=2)
        ttk.Combobox(gf, textvariable=self.v["track_cap"], state="readonly", width=56,
                     values=[t for t, _v in _TRACK_CAPS]).grid(row=row, column=1, columnspan=3,
                                                                sticky="w")
        row += 1
        self.v["snap_90"] = tk.BooleanVar()
        ttk.Checkbutton(gf, text="90-degree routing only (horizontal and vertical tracks)",
                        variable=self.v["snap_90"]).grid(row=row, column=0, columnspan=4,
                                                        sticky="w", padx=4)
        row += 1
        self.v["keep_existing"] = tk.BooleanVar()
        ttk.Checkbutton(gf, text="Keep the tracks, vias and jumpers already drawn and route only "
                        "what is still open (else rip everything up and route from scratch)",
                        variable=self.v["keep_existing"]).grid(row=row, column=0, columnspan=4,
                                                              sticky="w", padx=4)

        of = ttk.LabelFrame(left, text="Router (saved with the design)", padding=6)
        of.pack(fill="x", pady=(6, 0))
        for r, (label, key, note) in enumerate(_OWN_FIELDS):
            self.v[key] = tk.StringVar()
            ttk.Label(of, text=label + ":").grid(row=r, column=0, sticky="e", padx=4, pady=1)
            ttk.Entry(of, textvariable=self.v[key], width=8).grid(row=r, column=1, sticky="w")
            ttk.Label(of, text=note, foreground="#666").grid(row=r, column=2, sticky="w", padx=6)
        erow = len(_OWN_FIELDS)
        self.v["engine"] = tk.StringVar()
        ttk.Label(of, text="Engine:").grid(row=erow, column=0, sticky="e", padx=4, pady=(4, 1))
        ttk.Combobox(of, textvariable=self.v["engine"], state="readonly", width=52,
                     values=[t for t, _v in _ENGINES]).grid(row=erow, column=1, columnspan=2,
                                                            sticky="w", pady=(4, 1))

        cf = ttk.LabelFrame(left, text="Clean-up at the end", padding=6)
        cf.pack(fill="x", pady=(6, 0))
        for key, label in (("via_optimize", "Take out every crossover (via) that can go "
                                             "(two conductors)"),
                           ("shorten_tracks", "Shorten tracks (straighten detours, pull corners "
                                              "tight)"),
                           ("remove_jogs", "Remove jogs (line up short sideways steps)")):
            self.v[key] = tk.BooleanVar()
            ttk.Checkbutton(cf, text=label, variable=self.v[key]).pack(anchor="w")

        nf = ttk.Frame(left)
        nf.pack(fill="x", pady=(6, 0))
        self.nets_label = ttk.Label(nf, text="", foreground="#555")
        self.nets_label.pack(side="left")
        ttk.Button(nf, text="Import Net Names...", command=self._import_names).pack(side="right")

        # the log fills the new width on the right, so the form stays short
        ttk.Label(right, text="Log").pack(anchor="w")
        logf = ttk.Frame(right)
        logf.pack(fill="both", expand=True)
        self.log = tk.Text(logf, width=46, wrap="none", font=("Consolas", 11))
        logsb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=logsb.set)
        logsb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.result = ttk.Label(right, text="", wraplength=440, justify="left")
        self.result.pack(anchor="w", pady=(6, 0))

        b = ttk.Frame(f)
        b.pack(fill="x")
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
        eng = str(H.get("engine", "java"))
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
        for key in ("ripup_passes", "grid", "max_jumpers"):
            own[key] = int(own[key])
        if own["jumper_cost"] <= 1 or own["crossover_cost"] <= 1:
            raise ValueError("cost")
        own["engine"] = next((v for t, v in _ENGINES if t == self.v["engine"].get()), "java")
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
        self.log.delete("1.0", "end")
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
        self._opts = (rules, layers, own.get("engine", "java"))
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

        engine = self._opts[2]

        def work():
            try:
                if engine == "shape":
                    from ..scripting import shape_router as SR
                    self._lines.append("shape router: gridless planar + local-dielectric crossovers")
                    routed = SR.route(design, max_jumpers=own["max_jumpers"],
                                      local_dielectric=mapping.get("local_diel", -1) >= 0,
                                      cond2_keepouts=mapping.get("local_keepouts"),
                                      passes=1 + max(0, int(own["ripup_passes"])))
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
        if engine == "shape":
            cl = {}
        else:
            self._say("cleaning up ...")
            self.update_idletasks()
            cl = H.finish(self.session, mapping["cell"], rules["shorten_tracks"],
                          rules["remove_jogs"], rules["snap_90"], rules["via_optimize"], layers)
        self.app.refresh_all()
        rs = st["stats"]
        xovers = st.get("crossovers", 0) + st.get("windows", 0)
        txt = (f"Routed {rs.get('routed', '?')} of {rs.get('connections', '?')} connections: "
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
