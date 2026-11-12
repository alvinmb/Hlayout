# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Hybrid Route: route the open design with the chip-and-wire hybrid router
(the Freerouting fork in router/, HybridRouterCli) -- on one conductor with
wire-bond jumpers as the only crossings, or on two with dielectric
crossovers -- and bring the result back into the same cell.

  export_design()  the cell as hybrid.json: the substrate (boundary layer),
                   every bare die with its pads and its fixed bonds and
                   fingers (Fan Out Die Bonds), each two-pin part as a fixed
                   component (printed resistors as obstacles), the I/O pads
                   and any other part's pads as external pads, and the rules;
  run_router()     HybridRouterCli in a separate process;
  apply_routed()   the routed tracks, crossover windows, the fingers and
                   jumper lands the router added and its new bonds and
                   jumpers, drawn into the cell -- the old tracks and the
                   last Hybrid Route's additions taken out first. One undo
                   step.

Tools > Hybrid Route..., HYBRID ROUTE, Session.hybrid_route().
"""
import json
import math
import os
import tempfile
from typing import Optional

from ..core.errors import LayoutError
from ..core.shapes import Path, Polygon, Rectangle
from .autoplace_session import _HEADER_REF, _is_header, _xf, io_pads

MAIN_CLASS = "app.freerouting.cli.hybrid.HybridRouterCli"
ADDED_KEY = "hybrid_route_added"         # tech_params: what the last run drew
SETTINGS_KEY = "hybrid_route_settings"   # tech_params: the window's choices

#: Hybrid Route's own settings (saved with the design). 90-degree routing,
#: the nets to leave unrouted, shorten tracks and remove jogs are the
#: Autoroute (Freerouting) rules', shared by both routers.
DEFAULTS = {"max_jumpers": 0, "jumper_cost": 50.0, "crossover_cost": 10.0, "grid": 100,
            "ripup_passes": 3, "engine": "java"}


def layers_for(session, rules=None) -> int:
    """1 or 2 conductors, from the Autoroute rules' layer_mode ("single",
    "two", or "auto": one when nothing is drawn on the 2nd conductor)."""
    R = rules if rules is not None else session.freerouting_rules()
    return 1 if session._single_layer_route(R) else 2


def load_settings(library) -> dict:
    out = dict(DEFAULTS)
    try:
        out.update({k: v for k, v in json.loads(library.tech_params.get(SETTINGS_KEY) or "{}").items()
                    if k in DEFAULTS})
    except (ValueError, TypeError, AttributeError):
        pass
    return out


def save_settings(library, settings: dict) -> dict:
    cur = load_settings(library)
    cur.update({k: v for k, v in (settings or {}).items() if k in DEFAULTS})
    library.tech_params[SETTINGS_KEY] = json.dumps(cur)
    return cur


# --- finding the router ---------------------------------------------------------

def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def router_jar():
    """The fork's executable jar (router/freerouting/build/libs)."""
    try:
        from ..io.app_settings import get_setting
        p = get_setting("hybrid_router_jar")
        if p and os.path.isfile(p):
            return p
    except Exception:
        pass
    libs = os.path.join(_repo_root(), "router", "freerouting", "build", "libs")
    for name in ("freerouting-current-executable.jar", "freerouting-executable.jar"):
        p = os.path.join(libs, name)
        if os.path.isfile(p):
            return p
    return None


def router_command():
    """The command that runs HybridRouterCli: ``java -cp <jar>`` when a Java
    runtime is found, else a launcher made next to the installed Freerouting
    app (the Java runtime it bundles), else LayoutError."""
    jar = router_jar()
    if jar is None:
        raise LayoutError("the hybrid router isn't built -- build it in router\\freerouting "
                          "(gradlew executableJar, see router\\README.md)")
    java_setting = None
    fr = None
    try:
        from ..io.app_settings import get_setting
        java_setting = get_setting("java_path")
        fr = get_setting("freerouting_path")
    except Exception:
        pass
    from ..io.specctra import find_java
    j = find_java(java_setting)
    if not j:
        # a JDK HLayout set up for building the router (~/.hlayout/jdk-*)
        import glob
        for d in sorted(glob.glob(os.path.join(os.path.expanduser("~"), ".hlayout", "jdk*")),
                        reverse=True):
            for exe in ("java.exe", "java"):
                c = os.path.join(d, "bin", exe)
                if os.path.isfile(c):
                    j = c
                    break
            if j:
                break
    if j:
        return [j, "-cp", jar, MAIN_CLASS]
    # a jpackage Freerouting install: its launcher with a second .cfg runs
    # any main class on its bundled runtime
    if fr and os.path.isfile(fr) and fr.lower().endswith(".exe"):
        d = os.path.dirname(fr)
        if os.path.isdir(os.path.join(d, "runtime")) and os.path.isdir(os.path.join(d, "app")):
            exe = os.path.join(d, "hybridrouter.exe")
            cfg = os.path.join(d, "app", "hybridrouter.cfg")
            want = ("[Application]\napp.classpath=" + jar + "\napp.mainclass=" + MAIN_CLASS
                    + "\n\n[JavaOptions]\njava-options=-Dlog4j2.disableJndi=true\n")
            try:
                if not os.path.isfile(exe):
                    import shutil
                    shutil.copy(fr, exe)
                if not os.path.isfile(cfg) or open(cfg).read() != want:
                    with open(cfg, "w") as f:
                        f.write(want)
                return [exe]
            except OSError as e:
                raise LayoutError(f"can't set up the hybrid router launcher: {e}")
    raise LayoutError("no Java runtime for the hybrid router -- install Java 25, set it in "
                      "Setup > Freerouting..., or install Freerouting (it bundles one)")


# --- export -------------------------------------------------------------------------

def _pinned_boxes(library, cell_name, layers, xform=None, depth=0):
    """{pin: (x0, y0, x1, y1)} -- every numbered pad on ``layers`` of a cell
    (own shapes, numbered instances, nested cells), in parent coordinates."""
    out = {}
    cell = library.cells.get(cell_name)
    if cell is None or depth > 16:
        return out
    f = xform or (lambda p: p)

    def box(pts):
        q = [f(p) for p in pts]
        return (min(p[0] for p in q), min(p[1] for p in q), max(p[0] for p in q),
                max(p[1] for p in q))

    def has_layers(cn, d=0):
        c = library.cells.get(cn)
        return c is not None and d < 16 and (any(sh.layer in layers for sh in c.shapes) or
                                              any(has_layers(r.cell_name, d + 1) for r in c.srefs))

    for sh in cell.shapes:
        pin = getattr(sh, "pin", "")
        if pin and sh.layer in layers and isinstance(sh, (Rectangle, Polygon)):
            out.setdefault(str(pin), box(sh.points))
    for r in cell.srefs:
        g = _xf(r)
        comp = (lambda f, g: (lambda p: f(g(p))))(f, g)
        if getattr(r, "pin", ""):
            if has_layers(r.cell_name):
                b = library.cell_bbox(r.cell_name)
                if b:
                    q = [comp(p) for p in ((b[0], b[1]), (b[2], b[3]), (b[0], b[3]), (b[2], b[1]))]
                    out.setdefault(str(r.pin), (min(p[0] for p in q), min(p[1] for p in q),
                                                max(p[0] for p in q), max(p[1] for p in q)))
        else:
            for k, v in _pinned_boxes(library, r.cell_name, layers, comp, depth + 1).items():
                out.setdefault(k, v)
    return out


def _world_bbox(library, ref, layers=None):
    """World bbox of an instance's shapes (on ``layers`` only, if given)."""
    from ..core.maskops import layer_to_shapely_flattened
    child = library.cells.get(ref.cell_name)
    if child is None:
        return None
    f = _xf(ref)
    if layers is None:
        b = library.cell_bbox(ref.cell_name)
        if not b:
            return None
        pts = [f((b[0], b[1])), f((b[2], b[3])), f((b[0], b[3])), f((b[2], b[1]))]
    else:
        pts = []
        for L in layers:
            g = layer_to_shapely_flattened(library, child, L)
            if g is not None and not g.is_empty:
                b = g.bounds
                pts += [f((b[0], b[1])), f((b[2], b[3])), f((b[0], b[3])), f((b[2], b[1]))]
        if not pts:
            return None
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts),
            max(p[1] for p in pts))


def _part_layers(library, cell_name, depth=0):
    """Every layer a cell draws on (nested cells included)."""
    c = library.cells.get(cell_name)
    if c is None or depth > 16:
        return set()
    out = {sh.layer for sh in c.shapes}
    for r in c.srefs:
        out |= _part_layers(library, r.cell_name, depth + 1)
    return out


def part_boxes(session, cell, skip=()):
    """[(ref, (x0, y0, x1, y1))] -- the world outline of every placed part
    (an instance with a ref-des; the header and the substrate excepted)."""
    out = []
    for r in cell.srefs:
        if not r.label or r.label in skip or _HEADER_REF.match(r.label) \
                or _is_header(r.label, None, r.cell_name):
            continue
        b = _world_bbox(session.library, r)
        if b:
            out.append((r.label, b))
    return out


def _seg_hits_box(a, b, box, inset=1.0):
    """Does segment a-b pass over box (shrunk by ``inset``)?"""
    from shapely.geometry import LineString, box as _box
    x0, y0, x1, y1 = box
    if x1 - x0 <= 2 * inset or y1 - y0 <= 2 * inset:
        return False
    return LineString([a, b]).intersects(_box(x0 + inset, y0 + inset, x1 - inset, y1 - inset))


def _net(names, key):
    n = names.get(key) or ""
    return "" if n.lower().startswith("unconnected") else n


def export_design(session, cell_name: Optional[str] = None, layers: int = 1,
                  max_jumpers: int = 0, jumper_cost: float = 50.0,
                  crossover_cost: float = 10.0, grid: int = 0,
                  orthogonal: Optional[bool] = None, ripup_passes: int = 3,
                  exclude_nets=None, keep_existing: bool = False) -> tuple:
    """(design dict, mapping) -- see the module doc. ``layers`` 1 = one
    conductor, jumpers the only crossings; 2 = both conductors.
    ``orthogonal`` (default: the Autoroute rules' 90-degree routing) keeps
    the tracks horizontal and vertical; ``ripup_passes`` rip-up-and-reroute
    passes; ``exclude_nets`` (default: the Autoroute rules' "leave
    unrouted") are left out. Power nets (the rules' power_nets) are routed
    at the power width; with two conductors each takes its preferred
    direction (layer_directions) and a crossover is the design's via
    (via_spec). ``keep_existing``: the tracks, vias and jumpers already
    drawn stay -- the router joins on to them and routes only what is
    still open."""
    from ..io.techfile import (load_bond_rules, load_dielectric_rules, load_net_names,
                               load_resistor_geometry_rules)
    from .fanout import die_pads
    lib = session.library
    name = cell_name or session.current_cell_name
    cell = lib.get_cell(name)
    names = load_net_names(lib)
    R0 = session.freerouting_rules()
    if orthogonal is None:
        orthogonal = bool(R0.get("snap_90", True))
    if exclude_nets is None:
        exclude_nets = R0.get("exclude_nets") or []
    skip = {str(n).strip().upper() for n in exclude_nets if str(n).strip()}
    if skip:
        # a net left unrouted reaches the router as no net (an obstacle)
        names = {k: ("" if str(v).upper() in skip else v) for k, v in names.items()}
    blay_b = session._boundary_layer()
    sb = session._boundary_bbox(cell, blay_b) if blay_b is not None else None
    if not sb:
        raise LayoutError("no substrate outline -- draw it on the boundary layer (Setup > "
                          "Technology Properties, Route tab)")
    ox, oy = sb[0], sb[1]
    W, H = int(round(sb[2] - sb[0])), int(round(sb[3] - sb[1]))
    la, lb = session._route_layers()
    two = int(layers) >= 2
    route = {la, lb} if two else {la}
    R = session._rules_with_drc(session.freerouting_rules())
    br = load_bond_rules(lib)
    pad_layers = [int(l) for l in (br.get("die_pad_layers") or []) if int(l) >= 0]
    blay = int(br["layer"])
    rg = load_resistor_geometry_rules(lib)
    ink = {int(v) for v in (rg.get("ink_layers") or {}).values()}
    for k in ("resistor_layer", "glaze_layer"):
        if isinstance(rg.get(k), int) and rg[k] >= 0:
            ink.add(rg[k])
    lname = {la: "COND1", lb: "COND2"}
    P = lambda x, y: {"x": int(round(x - ox)), "y": int(round(y - oy))}
    clr = int(round(float(R.get("clearance", 150))))
    width = int(round(float(R.get("signal_width", 300))))
    finger = float(R.get("bond_finger", 400) or 400)

    design = {"schemaVersion": 1, "name": name,
              "substrate": {"technology": "THICK_FILM", "material": "96% alumina",
                            "widthUm": W, "heightUm": H},
              "stack": {"layers": ([{"name": "COND1", "kind": "CONDUCTOR"}]
                                   + ([{"name": "DIEL1", "kind": "DIELECTRIC"},
                                       {"name": "COND2", "kind": "CONDUCTOR"}] if two else [])
                                   + [{"name": "RES", "kind": "RESISTOR"},
                                      {"name": "GLAZE", "kind": "OVERGLAZE"}])},
              "dies": [], "substratePads": [], "wires": [], "components": []}
    pads, wires = design["substratePads"], design["wires"]
    edge = W + H
    taken = set()
    terms = []                    # (terminal id, world x, y, layer, net)
    pre_connected = []            # groups of terminal ids that are one piece of copper
    used_boxes = []               # conductor rectangles exported as pads

    def add_pad(pid, net, box, role="EXTERNAL", layer=la):
        nonlocal edge
        pid = str(pid)
        while pid in taken:
            pid += "_"
        taken.add(pid)
        # a pad with no net (an unconnected header pin, a net left
        # unrouted) is still copper nothing may touch: a net of its own
        net = net or f"~NC_{pid}"
        x0, y0, x1, y1 = box
        pads.append({"id": pid, "net": net, "role": role, "position": P((x0 + x1) / 2, (y0 + y1) / 2),
                     "widthUm": max(1, int(round(x1 - x0))), "heightUm": max(1, int(round(y1 - y0))),
                     "layer": lname.get(layer, "COND1")})
        edge = min(edge, x0 - sb[0], y0 - sb[1], sb[2] - x1, sb[3] - y1)
        terms.append((pid, (x0 + x1) / 2, (y0 + y1) / 2, layer, net))
        used_boxes.append((layer, tuple(round(v) for v in box)))
        return pid

    die_refs = set()
    unbonded = []
    nofly = []                    # (ref, world box) no jumper may fly over
    for r in cell.srefs:
        if not r.label:
            continue
        dp = die_pads(lib, r.cell_name, pad_layers) if pad_layers else {}
        if not dp:
            continue
        die_refs.add(r.label)
        boxes = _pinned_boxes(lib, r.cell_name, set(pad_layers), _xf(r))
        db = _world_bbox(lib, r, pad_layers) or _world_bbox(lib, r)
        backside = _pinned_boxes(lib, r.cell_name, route, _xf(r))
        die = {"refdes": r.label, "partName": r.cell_name, "origin": P(db[0], db[1]),
               "rotation": "R0", "widthUm": max(1, int(round(db[2] - db[0]))),
               "heightUm": max(1, int(round(db[3] - db[1]))),
               "attachKeepoutUm": 0 if backside else int(round(float(R.get("die_keepout", 200)))),
               "pads": []}
        for pin, b in sorted(boxes.items(), key=lambda kv: (len(kv[0]), kv[0])):
            die["pads"].append({"id": pin, "net": _net(names, f"{r.label}.{pin}"),
                                "position": {"x": int(round((b[0] + b[2]) / 2 - db[0])),
                                             "y": int(round((b[1] + b[3]) / 2 - db[1]))},
                                "widthUm": max(1, int(round(b[2] - b[0]))),
                                "heightUm": max(1, int(round(b[3] - b[1])))})
            # its bond and finger, if it has them (Fan Out Die Bonds): fixed
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            found = session.find_wire_bond((cx, cy), layer=blay, cell_name=name,
                                           tol=max(b[2] - b[0], b[3] - b[1]) / 2 + 20)
            if found is None:
                if _net(names, f"{r.label}.{pin}"):
                    unbonded.append(f"{r.label}.{pin}")
                continue
            path, _balls = found
            a, e = tuple(path.points[0]), tuple(path.points[-1])
            far = e if math.hypot(a[0] - cx, a[1] - cy) <= math.hypot(e[0] - cx, e[1] - cy) else a
            fb = next((sh.bbox() for sh in cell.shapes
                       if sh.layer in route and isinstance(sh, (Rectangle, Polygon))
                       and not getattr(sh, "pin", "")
                       and sh.bbox()[0] <= far[0] <= sh.bbox()[2]
                       and sh.bbox()[1] <= far[1] <= sh.bbox()[3]
                       and (sh.bbox()[2] - sh.bbox()[0]) * (sh.bbox()[3] - sh.bbox()[1])
                       <= 4 * finger * finger), None)
            if fb is None:
                h = finger / 2
                fb = (far[0] - h, far[1] - h, far[0] + h, far[1] + h)
            fid = add_pad(f"F_{r.label}_{pin}", _net(names, f"{r.label}.{pin}"), fb, "BOND_FINGER")
            wires.append({"id": f"W_{r.label}_{pin}", "net": _net(names, f"{r.label}.{pin}"),
                          "from": {"kind": "DIE_PAD", "die": r.label, "pad": pin},
                          "to": {"kind": "SUBSTRATE_PAD", "pad": fid},
                          "spec": {"material": "GOLD",
                                   "diameterUm": max(1, int(round(float(br["width"]))))},
                          "fixed": True})
        for pin, b in backside.items():           # a diode's cathode, a collector
            # the attach pad round the die is what a track can reach: the
            # widest strip of it outside the die (the router keeps tracks
            # off the die itself)
            strips = [(b[0], b[1], db[0], b[3]), (db[2], b[1], b[2], b[3]),
                      (b[0], b[1], b[2], db[1]), (b[0], db[3], b[2], b[3])]
            strips = [t for t in strips if t[2] - t[0] > 0 and t[3] - t[1] > 0]
            strips.sort(key=lambda t: -(t[2] - t[0]) * (t[3] - t[1]))
            # every strip is the pad's copper (nothing else may touch it);
            # they are one piece, so the router need not join them
            ids = [add_pad(f"{r.label}_{pin}" + ("" if k == 0 else f"~{k}"),
                           _net(names, f"{r.label}.{pin}") or f"~NC_{r.label}_{pin}", t, layer=la)
                   for k, t in enumerate(strips or [b])]
            if len(ids) > 1:
                pre_connected.append(ids)
        design["dies"].append(die)
        nofly.append((r.label, tuple(db)))

    for pin, b in io_pads(lib, cell, blay_b):
        add_pad(f"IO{pin}", _net(names, f"IO.{pin}"), b)

    for r in cell.srefs:
        if not r.label or r.label in die_refs:
            continue
        pins = _pinned_boxes(lib, r.cell_name, route, _xf(r))
        if not pins:
            continue
        if _HEADER_REF.match(r.label) or _is_header(r.label, None, r.cell_name):
            continue
        res = _world_bbox(lib, r, ink) if ink else None
        if len(pins) == 2:
            (pa, ba), (pb, bb) = sorted(pins.items(), key=lambda kv: (len(kv[0]), kv[0]))
            ca = ((ba[0] + ba[2]) / 2, (ba[1] + ba[3]) / 2)
            cb = ((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2)
            horiz = abs(cb[0] - ca[0]) >= abs(cb[1] - ca[1])
            # the body across: a resistor's ink / glaze, else the part's whole
            # outline (an SMD inductor or capacitor is wider than its pads)
            acr = res or _world_bbox(lib, r, [l for l in _part_layers(lib, r.cell_name)
                                               if l not in route])
            if horiz:
                lo, hi = sorted([ba, bb], key=lambda b: b[0])
                bx0, bx1 = lo[2], hi[0]
                by0 = min([ba[1], bb[1]] + ([acr[1]] if acr else []))
                by1 = max([ba[3], bb[3]] + ([acr[3]] if acr else []))
            else:
                lo, hi = sorted([ba, bb], key=lambda b: b[1])
                by0, by1 = lo[3], hi[1]
                bx0 = min([ba[0], bb[0]] + ([acr[0]] if acr else []))
                bx1 = max([ba[2], bb[2]] + ([acr[2]] if acr else []))
            if bx1 - bx0 < 1:
                bx0, bx1 = bx0 - 0.5, bx0 + 0.5
            if by1 - by0 < 1:
                by0, by1 = by0 - 0.5, by0 + 0.5
            cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
            printed = res is not None
            # nothing -- no track, no jumper land -- goes under a part
            obstacle = ["COND1", "COND2"] if two else ["COND1"]

            def pin(b, p):
                return {"net": _net(names, f"{r.label}.{p}") or f"~NC_{r.label}.{p}",
                        "position": {"x": int(round((b[0] + b[2]) / 2 - cx)),
                                     "y": int(round((b[1] + b[3]) / 2 - cy))},
                        "widthUm": max(1, int(round(b[2] - b[0]))),
                        "heightUm": max(1, int(round(b[3] - b[1]))),
                        "layer": "COND1"}
            design["components"].append({
                "refdes": r.label, "kind": "PRINTED_RESISTOR" if printed else "CHIP",
                "centre": P(cx, cy), "rotation": "R0",
                "bodyLengthUm": max(1, int(round(bx1 - bx0))),
                "bodyWidthUm": max(1, int(round(by1 - by0))),
                "keepoutUm": 0, "obstacleLayers": obstacle,
                "pinA": pin(ba, pa), "pinB": pin(bb, pb)})
            for b, p, nm in ((ba, pa, "A"), (bb, pb, "B")):
                terms.append((f"{r.label}.{nm}", (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, la,
                              _net(names, f"{r.label}.{p}") or f"~NC_{r.label}.{p}"))
            for b in (ba, bb):
                edge = min(edge, b[0] - sb[0], b[1] - sb[1], sb[2] - b[2], sb[3] - b[3])
            # a chip stands proud of the substrate, terminations and all; a
            # printed resistor's terminations are flat print
            if printed:
                nofly.append((r.label, (bx0, by0, bx1, by1)))
            else:
                nofly.append((r.label, (min(bx0, ba[0], bb[0]), min(by0, ba[1], bb[1]),
                                        max(bx1, ba[2], bb[2]), max(by1, ba[3], bb[3]))))
        else:
            for p, b in pins.items():
                add_pad(f"{r.label}_{p}", _net(names, f"{r.label}.{p}"), b)

    # -- the tracks, vias and jumpers already there (keep_existing) ----------
    fixed, joins, connected, kept_wires = [], [], [], 0
    if keep_existing:
        tracer = session.net_tracer(cell_name=name)
        link = {}

        def find(a):
            while link.get(a, a) != a:
                a = link[a]
            return a

        def root(x, y, lay):
            n = tracer.node_at(x, y, prefer_layer=lay)
            return None if n is None else find(("n", tracer.dsu.find(n)))

        # a jumper already bonded joins the copper at its two ends
        die_boxes = [b for _r, b in nofly if _r in die_refs]
        for sh in cell.shapes:
            if isinstance(sh, Path) and sh.layer == blay and len(sh.points) == 2:
                a, b = sh.points
                if any(d[0] <= q[0] <= d[2] and d[1] <= q[1] <= d[3] for d in die_boxes
                       for q in (a, b)):
                    continue                              # a die bond, not a jumper
                ra, rb = root(a[0], a[1], la), root(b[0], b[1], la)
                if ra is not None and rb is not None and ra != rb:
                    link[ra] = rb
                    kept_wires += 1
        by_root = {}
        tnet = {}
        for tid, x, y, lay, net in terms:
            tnet[tid] = net
            r0 = root(x, y, lay)
            if r0 is not None:
                by_root.setdefault(r0, []).append(tid)
        connected = [ids for ids in by_root.values() if len(ids) > 1]

        def add_fixed(lay, pts, w):
            r0 = root(pts[0][0], pts[0][1], lay)
            ids = by_root.get(r0) or []
            net = next((tnet[i] for i in ids if tnet.get(i)), "")
            join = next((i for i in ids if tnet.get(i) == net), "") if net else ""
            fixed.append({"net": net, "layer": lname[lay],
                          "points": [P(x, y) for x, y in pts], "widthUm": max(1, int(round(w)))})
            joins.append(join)

        for sh in cell.shapes:
            if sh.layer not in route or getattr(sh, "pin", ""):
                continue
            if isinstance(sh, Path):
                pts = [tuple(q) for q in sh.points]
                pts = [q for i, q in enumerate(pts) if i == 0 or q != pts[i - 1]]
                if len(pts) >= 2:
                    add_fixed(sh.layer, pts, sh.width)
            elif isinstance(sh, (Rectangle, Polygon)):
                x0, y0, x1, y1 = sh.bbox()
                if (sh.layer, (round(x0), round(y0), round(x1), round(y1))) in used_boxes:
                    continue
                w, h = x1 - x0, y1 - y0
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                if w > h + 1:
                    add_fixed(sh.layer, [(x0 + h / 2, cy), (x1 - h / 2, cy)], h)
                elif h > w + 1:
                    add_fixed(sh.layer, [(cx, y0 + w / 2), (cx, y1 - w / 2)], w)
                else:
                    add_fixed(sh.layer, [(cx, cy), (cx + 1, cy)], min(w, h))

    power = {str(n).strip().upper() for n in (R.get("power_nets") or []) if str(n).strip()}
    pwidth = int(round(float(R.get("power_width", width))))
    all_nets = {t[4] for t in terms if t[4]}
    net_widths = {n: pwidth for n in all_nets if n.upper() in power and pwidth > width}
    dirs = str(R.get("layer_directions", "off")).lower()
    directions = ({"hv": ["H", "V"], "vh": ["V", "H"]}.get(dirs, []) if two else [])
    sig, pwr = session.via_spec(False, R), session.via_spec(True, R)
    via_keep = int(round(max(sig["pad"], sig["window"])))
    pwr_keep = int(round(max(pwr["pad"], pwr["window"])))
    net_windows = {n: pwr_keep for n in all_nets if n.upper() in power and pwr_keep > via_keep}
    keepouts = {}
    if two:
        # Where the dielectric will be open the two conductors aren't
        # insulated: the 2nd may not go there (the band outside the
        # dielectric's boundary, where the I/O pads are; a resistor's
        # keep-out; any other hole drawn on the dielectric layer). The
        # same boundary and holes as generate_dielectric().
        try:
            from shapely.ops import unary_union
            from ..core.maskops import layer_geoms_flattened, layer_to_shapely_flattened
            drules = load_dielectric_rules(lib)
            dlay = int(drules.get("layer", -1))
        except Exception:
            dlay = -1
        if dlay >= 0:
            dclr = float(drules.get("clearance", 0) or 0)
            vmax = max(sig["window"], pwr["window"]) + 2
            inputs = [g for g in layer_geoms_flattened(lib, cell, dlay, datatype=0)
                      if not (abs((g.bounds[2] - g.bounds[0]) - (g.bounds[3] - g.bounds[1])) <= 2
                              and g.bounds[2] - g.bounds[0] <= vmax)]       # not a via window
            boundary, holes = None, list(inputs)
            if inputs:
                biggest = max(inputs, key=lambda g: g.area)
                rest = [g for g in inputs if g is not biggest]
                if not rest:
                    boundary, holes = biggest, []
                else:
                    ru = unary_union(rest)
                    if ru.area == 0 or biggest.intersection(ru).area / ru.area >= 0.95:
                        boundary, holes = biggest, rest
            fill = boundary if boundary is not None else session._io_ring_box(cell)
            rects = []
            if fill is not None:
                fb = fill.bounds
                fb = (max(fb[0], sb[0]), max(fb[1], sb[1]), min(fb[2], sb[2]), min(fb[3], sb[3]))
                rects += [(sb[0], sb[1], sb[2], fb[1]), (sb[0], fb[3], sb[2], sb[3]),
                          (sb[0], fb[1], fb[0], fb[3]), (fb[2], fb[1], sb[2], fb[3])]
            for src in (drules.get("keepout_layers") or []):
                try:
                    g = layer_to_shapely_flattened(lib, cell, lib.layers.resolve(src))
                except Exception:
                    continue
                if g is not None and not g.is_empty:
                    holes += list(getattr(g, "geoms", [g]))
            for g in holes:
                b = g.bounds
                rects.append((b[0] - dclr, b[1] - dclr, b[2] + dclr, b[3] + dclr))
            keepouts["COND2"] = [[int(math.floor(r[0] - ox)), int(math.floor(r[1] - oy)),
                                  int(math.ceil(r[2] - ox)), int(math.ceil(r[3] - oy))]
                                 for r in rects if r[2] - r[0] > 0 and r[3] - r[1] > 0]
    edge_clr = int(max(0, min(clr, math.floor(edge)))) if edge < W + H else clr
    design["routing"] = {"routingLayers": ["COND1", "COND2"] if two else ["COND1"],
                         # jumpers are for one-conductor designs: under the
                         # dielectric of a two-conductor one a land can't be bonded
                         "jumpersAllowed": not two, "jumperCost": float(jumper_cost),
                         "crossoverCost": float(crossover_cost),
                         "maxJumpers": int(max_jumpers), "gridUm": int(grid),
                         "orthogonal": bool(orthogonal), "ripupPasses": int(max(0, ripup_passes)),
                         "netWidths": net_widths, "fixedTraces": fixed, "fixedJoins": joins,
                         "connected": pre_connected + connected, "directions": directions,
                         "netWindows": net_windows, "keepouts": keepouts,
                         "againstDirectionCost": max(1.0, float(R.get("against_direction_cost",
                                                                      2.5) or 2.5))}
    design["rules"] = {
        "conductor": {"minLineUm": width, "minSpaceUm": clr, "edgeClearanceUm": edge_clr,
                      # a crossover is the design's one via: keep its whole
                      # footprint (pad or dielectric window, the larger) clear
                      "windowMinUm": via_keep, "windowEnclosureUm": 0},
        "bondWire": {"minLengthUm": int(br["min_length"]), "maxLengthUm": int(br["max_length"]),
                     "dieToFingerMinUm": 0, "dieToFingerMaxUm": int(br["max_length"]),
                     "maxAngleDeg": float(session._hybrid_bond_rules().get("angle", 45.0))},
        "jumper": {"minLengthUm": max(int(br["min_length"]), 500),
                   "maxLengthUm": min(int(br["max_length"]), 3000)},
        "pads": {"fingerWidthUm": max(250, width), "fingerLengthUm": max(400, width),
                 "jumperLandUm": max(200, width)},
    }
    try:
        diel = int(load_dielectric_rules(lib)["layer"]) if two else -1
    except Exception:
        diel = -1
    try:
        local_diel = int(load_dielectric_rules(lib)["layer"])   # for local crossovers (printed jumpers), any mode
    except Exception:
        local_diel = diel
    mapping = {"cell": name, "origin": [ox, oy], "layers": {"COND1": la, "COND2": lb},
               "dielectric": diel, "local_diel": local_diel, "bond_layer": blay,
               "pads": [p["id"] for p in pads], "wires": [w["id"] for w in wires],
               "unbonded": unbonded, "bond_width": int(round(float(br["width"]))),
               "nofly": nofly, "keep_existing": bool(keep_existing),
               "kept": {"tracks": len(fixed), "joined": len(connected), "jumpers": kept_wires},
               "power_nets": sorted(n for n in all_nets if n.upper() in power),
               "via": {"signal": sig, "power": pwr},
               "ball": int(round(float(br["pad"])))}
    return design, mapping


# --- run ----------------------------------------------------------------------------

def run_router(design: dict, out_dir: Optional[str] = None, force: bool = True,
               timeout: Optional[float] = 600, on_line=None, cancel=None) -> tuple:
    """Write ``design`` and run HybridRouterCli. Returns (exit code, routed
    dict or None, output lines, paths)."""
    from ..io.specctra import run_freerouting
    d = out_dir or tempfile.mkdtemp(prefix="hlayout_hybrid_")
    os.makedirs(d, exist_ok=True)
    base = os.path.join(d, (design.get("name") or "design").replace(" ", "_"))
    inp, out = base + ".hybrid.json", base + ".routed.json"
    with open(inp, "w", encoding="utf-8") as f:
        json.dump(design, f, indent=1)
    cmd = router_command() + [inp, out] + (["--force"] if force else [])
    code, tail = run_freerouting(cmd, out, timeout=timeout, on_line=on_line, cancel=cancel)
    routed = None
    if os.path.isfile(out):
        with open(out, encoding="utf-8") as f:
            routed = json.load(f)
    return code, routed, tail, {"input": inp, "output": out}


# --- apply --------------------------------------------------------------------------

def _pt(q):
    return (float(q["x"]), float(q["y"])) if isinstance(q, dict) else (float(q[0]), float(q[1]))


def apply_routed(session, routed: dict, mapping: dict, remove_existing: bool = True) -> dict:
    """Draw the router's result into the cell (one undo step): see the
    module doc. Returns counts and the router's stats / unrouted list."""
    lib = session.library
    cell = lib.get_cell(mapping["cell"])
    ox, oy = mapping["origin"]
    L = dict(mapping["layers"])
    la, lb = L["COND1"], L["COND2"]
    diel = mapping.get("dielectric", -1)
    blay = mapping["bond_layer"]
    design = routed.get("design") or {}
    W = lambda q: (round(_pt(q)[0] + ox), round(_pt(q)[1] + oy))
    from ..core.shapes import PathCap
    try:
        cap = PathCap(str(session.freerouting_rules().get("track_cap", "round")).lower()).value
    except (ValueError, Exception):
        cap = "round"
    added = []
    st = {"traces": 0, "windows": 0, "pads": 0, "jumper_lands": 0, "fingers": 0,
          "bonds": 0, "jumpers": 0, "crossovers": 0, "removed_tracks": 0}
    with session._batch():
        # take out the old tracks and what the last Hybrid Route added
        try:
            old = json.loads(lib.tech_params.get(ADDED_KEY) or "{}")
        except ValueError:
            old = {}
        keeping = bool(mapping.get("keep_existing")) or not remove_existing
        remove_existing = not keeping
        old_sigs = set() if keeping else {tuple(x) for x in (old.get(mapping["cell"]) or [])}
        if keeping:
            added.extend(tuple(x) for x in (old.get(mapping["cell"]) or []))
        keep = []
        for sh in cell.shapes:
            sig = (sh.layer, *map(round, sh.bbox()))
            if sig in old_sigs:
                continue
            if remove_existing and isinstance(sh, Path) and sh.layer in (la, lb) \
                    and not getattr(sh, "pin", ""):
                st["removed_tracks"] += 1
                continue
            keep.append(sh)
        cell.shapes[:] = keep

        def rect(layer, cx, cy, w, h):
            s = cell.add_rectangle(layer, round(cx - w / 2), round(cy - h / 2),
                                   round(cx + w / 2), round(cy + h / 2))
            added.append((s.layer, *map(round, s.bbox())))

        known = set(mapping.get("pads") or [])
        knownw = set(mapping.get("wires") or [])
        pads = {p["id"]: p for p in design.get("substratePads") or []}
        # a jumper may not fly over a part (or a die): refuse it, its lands
        # and the track stubs to them -- its net is left unrouted
        parts = [(ref, tuple(b)) for ref, b in (mapping.get("nofly") or [])] \
            or part_boxes(session, cell)
        refused, refused_pads = [], set()
        for w in design.get("wires") or []:
            f, t = w.get("from") or {}, w.get("to") or {}
            if w.get("id") in knownw or f.get("kind") != "SUBSTRATE_PAD" \
                    or t.get("kind") != "SUBSTRATE_PAD":
                continue
            pa, pb = pads.get(f.get("pad")), pads.get(t.get("pad"))
            if not pa or not pb:
                continue
            a, b = W(pa["position"]), W(pb["position"])
            hit = next((ref for ref, box in parts if _seg_hits_box(a, b, box)), None)
            if hit:
                refused.append({"wire": w.get("id"), "net": w.get("net") or "", "over": hit})
                refused_pads |= {f.get("pad"), t.get("pad")}
        stub_ends = {W(pads[p]["position"]) for p in refused_pads if p in pads}

        for t in routed.get("traces") or []:
            pts = [W(q) for q in t.get("points") or []]
            pts = [q for i, q in enumerate(pts) if i == 0 or q != pts[i - 1]]
            if len(pts) < 2 or pts[0] in stub_ends or pts[-1] in stub_ends:
                continue
            cell.add_path(L.get(t.get("layer"), la), pts, int(t.get("widthUm") or 300),
                          cap=cap)
            st["traces"] += 1
        vias = mapping.get("via") or {}
        pnets = {n.upper() for n in (mapping.get("power_nets") or [])}
        for wdw in routed.get("windows") or []:
            # a crossover is drawn as the design's one via (via_spec): the
            # conductor pad on both layers and its dielectric window
            x, y = W(wdw.get("position"))
            spec = vias.get("power" if str(wdw.get("net") or "").upper() in pnets else "signal") or {}
            pad = int(round(spec.get("pad") or wdw.get("padUm") or 0))
            for key in ("fromLayer", "toLayer"):
                rect(L.get(wdw.get(key), la), x, y, pad, pad)
            if diel is not None and diel >= 0:
                o = int(round(spec.get("window") or wdw.get("openingUm") or 0))
                rect(diel, x, y, o, o)
            st["windows"] += 1
        # printed-jumper crossovers: a COND2 bridge with a local dielectric strip
        # (its ends inset so the up-vias contact COND1) -- see shape_router
        ld = mapping.get("local_diel", diel)

        def _inset(pts, d):
            """``pts`` trimmed by ``d`` along its length at each end (not just
            the end segments, so a short last leg can't fold the strip back);
            [] when the line is no longer than 2d."""
            import math as _m
            if len(pts) < 2 or d <= 0:
                return list(pts)

            def cut(seq):
                left = d
                for i in range(1, len(seq)):
                    (x0, y0), (x1, y1) = seq[i - 1], seq[i]
                    sl = _m.hypot(x1 - x0, y1 - y0)
                    if sl > left:
                        f = left / sl
                        return [(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)] + list(seq[i:])
                    left -= sl
                return []
            total = sum(_m.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
                        for i in range(1, len(pts)))
            if total <= 2 * d:
                return []
            out = cut(list(pts))
            out = cut(out[::-1])[::-1]
            return [(round(x), round(y)) for x, y in out]
        for c in routed.get("crossovers") or []:
            cw = max(1, int(c.get("widthUm") or 0))
            dw = max(cw, int(c.get("dielWidthUm") or cw * 3))
            bpts = [W(q) for q in c.get("bridge") or []]
            bpts = [q for i, q in enumerate(bpts) if i == 0 or q != bpts[i - 1]]
            if len(bpts) < 2:
                continue
            spec = vias.get("power" if str(c.get("net") or "").upper() in pnets else "signal") or {}
            # the land the router cleared (landUm); older results: the via pad
            vp = int(round(c.get("landUm") or spec.get("pad") or cw))
            for end in (bpts[0], bpts[-1]):
                rect(la, end[0], end[1], vp, vp)          # COND1 up-via pad
                rect(lb, end[0], end[1], vp, vp)          # COND2 up-via pad
            if ld is not None and ld >= 0:
                # flush ends at the land edges: the lands stay bare (the contact)
                # and everything the bridge crosses -- which the router kept at
                # least land/2 + spacing from each end -- is under dielectric
                dpts = _inset(bpts, vp / 2)
                if len(dpts) >= 2:
                    # the FILL datatype: datatype 0 on the dielectric layer means
                    # a via window (an opening), which would short the bridge to
                    # every COND1 track it crosses (see core.nettrace)
                    s = cell.add_path(ld, dpts, dw, cap="flush",
                                      datatype=session.DIELECTRIC_FILL_DATATYPE)
                    added.append((s.layer, *map(round, s.bbox())))
            s = cell.add_path(lb, bpts, cw, cap=cap)      # COND2 bridge
            added.append((s.layer, *map(round, s.bbox())))
            st["crossovers"] += 1
        for pid, p in pads.items():
            if pid in known or pid in refused_pads:
                continue
            x, y = W(p["position"])
            rect(L.get(p.get("layer"), la), x, y, int(p["widthUm"]), int(p["heightUm"]))
            st["pads"] += 1
            if p.get("role") == "JUMPER_LAND":
                st["jumper_lands"] += 1
            elif p.get("role", "BOND_FINGER") == "BOND_FINGER":
                st["fingers"] += 1
        dies = {d["refdes"]: d for d in design.get("dies") or []}

        def end(e):
            if e.get("kind") == "DIE_PAD":
                d = dies.get(e.get("die"))
                if d is None:
                    return None
                p = next((q for q in d.get("pads") or [] if str(q.get("id")) == str(e.get("pad"))), None)
                if p is None:
                    return None
                dx, dy = _pt(d["origin"])
                px, py = _pt(p["position"])
                return (round(dx + px + ox), round(dy + py + oy))
            p = pads.get(e.get("pad"))
            return W(p["position"]) if p else None

        r_ball = max(1, int(mapping.get("ball") or 100) // 2)
        refused_ids = {x["wire"] for x in refused}
        for w in design.get("wires") or []:
            if w.get("id") in knownw or w.get("id") in refused_ids:
                continue
            a, b = end(w.get("from") or {}), end(w.get("to") or {})
            if a is None or b is None or a == b:
                continue
            for c in (a, b):
                s = cell.add_circle(blay, c, r_ball, segments=16)
                added.append((s.layer, *map(round, s.bbox())))
            s = cell.add_path(blay, [a, b], max(1, int(mapping.get("bond_width") or 30)), cap="round")
            added.append((s.layer, *map(round, s.bbox())))
            jumper = (w.get("from") or {}).get("kind") == "SUBSTRATE_PAD" and \
                (w.get("to") or {}).get("kind") == "SUBSTRATE_PAD"
            st["jumpers" if jumper else "bonds"] += 1
        st["refused_jumpers"] = refused
        old[mapping["cell"]] = added
        lib.tech_params[ADDED_KEY] = json.dumps(old)
        if diel is not None and diel >= 0 and st["windows"]:
            try:
                session.generate_dielectric(cell_name=mapping["cell"])
            except Exception:
                pass
    session._notify()
    st["stats"] = routed.get("stats") or {}
    st["unrouted"] = routed.get("unrouted") or []
    st["issues"] = routed.get("issues") or []
    st["cell"] = mapping["cell"]
    return st


def unrouted_text(u) -> str:
    """One line for an unrouted connection, whatever shape the router gives."""
    if isinstance(u, dict):
        net = u.get("net") or "?"
        a, b = u.get("from"), u.get("to")
        f = lambda e: (f"{e.get('die') + '.' if e.get('die') else ''}{e.get('pad') or ''}"
                       if isinstance(e, dict) else str(e))
        return f"{net}: {f(a)} - {f(b)}" if a or b else net + (f" ({u.get('reason')})" if u.get("reason") else "")
    return str(u)


def finish(session, cell_name, shorten=None, dejog=None, orthogonal=None,
           via_optimize=None, layers: int = 1, progress=None) -> dict:
    """The clean-ups after a route: with two conductors take out every
    crossover that can go (optimize_vias), then shorten tracks and remove
    jogs (default: as the Autoroute rules say), keeping out from under
    every part and, with 90-degree routing, square. Returns their reports."""
    R = session.freerouting_rules()
    shorten = R.get("shorten_tracks", True) if shorten is None else shorten
    dejog = R.get("remove_jogs", True) if dejog is None else dejog
    via_optimize = R.get("via_optimize", True) if via_optimize is None else via_optimize
    over = {"keepout_bodies": True}
    if orthogonal is not None:
        over["snap_90"] = bool(orthogonal)
    out = {}
    if via_optimize and int(layers) >= 2:
        try:
            out["vias"] = session.optimize_vias(cell_name=cell_name, layer_mode="two", **over)
        except Exception as e:
            out["vias"] = {"error": str(e)}
    if shorten:
        try:
            out["shorten"] = session.shorten_tracks(cell_name=cell_name, **over)
        except Exception as e:
            out["shorten"] = {"error": str(e)}
    if dejog:
        try:
            out["dejog"] = session.remove_jogs(cell_name=cell_name, **over)
        except Exception as e:
            out["dejog"] = {"error": str(e)}
    return out


def hybrid_route(session, cell_name: Optional[str] = None, layers: Optional[int] = None,
                 max_jumpers: Optional[int] = None, jumper_cost: Optional[float] = None,
                 crossover_cost: Optional[float] = None, grid: Optional[int] = None,
                 remove_existing: Optional[bool] = None,
                 timeout: Optional[float] = 600, on_line=None, cancel=None,
                 out_dir: Optional[str] = None, orthogonal: Optional[bool] = None,
                 ripup_passes: Optional[int] = None, exclude_nets=None,
                 shorten: Optional[bool] = None, dejog: Optional[bool] = None,
                 via_optimize: Optional[bool] = None, engine: Optional[str] = None) -> dict:
    """Export, route and apply in one go (the apply is one undo step), then
    the clean-ups (crossover optimisation, shorten tracks, remove jogs).
    Anything left None comes from the design: the Autoroute rules
    (layer_mode, keep_existing, ...) and the saved autorouter settings."""
    H = load_settings(session.library)
    R = session.freerouting_rules()
    layers = layers_for(session, R) if layers is None else int(layers)
    max_jumpers = H["max_jumpers"] if max_jumpers is None else max_jumpers
    jumper_cost = H["jumper_cost"] if jumper_cost is None else jumper_cost
    crossover_cost = H["crossover_cost"] if crossover_cost is None else crossover_cost
    grid = H["grid"] if grid is None else grid
    ripup_passes = H["ripup_passes"] if ripup_passes is None else ripup_passes
    if remove_existing is None:
        remove_existing = not bool(R.get("keep_existing", True))
    engine = (H.get("engine") or "java") if engine is None else str(engine).lower()
    design, mapping = export_design(session, cell_name, layers=layers, max_jumpers=max_jumpers,
                                    jumper_cost=jumper_cost, crossover_cost=crossover_cost,
                                    grid=grid, orthogonal=orthogonal, ripup_passes=ripup_passes,
                                    exclude_nets=exclude_nets,
                                    keep_existing=not remove_existing)
    if engine == "shape":
        # native Python gridless shape router (planar + printed-jumper crossovers)
        from . import shape_router as _shape
        if on_line:
            on_line("shape router: gridless planar + local-dielectric crossovers")
        routed = _shape.route(design, max_jumpers=max_jumpers,
                              local_dielectric=mapping.get("local_diel", -1) >= 0,
                              passes=1 + max(0, int(ripup_passes)))
        code, tail, paths = 0, [], {}
    else:
        code, routed, tail, paths = run_router(design, out_dir=out_dir, timeout=timeout,
                                               on_line=on_line, cancel=cancel)
        if routed is None:
            raise LayoutError("the hybrid router wrote no result (exit code "
                              f"{code}):\n" + "\n".join(tail[-12:]))
    st = apply_routed(session, routed, mapping, remove_existing=remove_existing)
    st.update(exit_code=code, paths=paths, log=tail[-12:], layers=int(layers), engine=engine,
              unbonded=mapping.get("unbonded") or [],
              orthogonal=design["routing"]["orthogonal"])
    # the shape router's output is already clean; the Java via/track clean-ups
    # don't understand its printed-jumper crossovers, so skip them for it
    st["cleanup"] = {} if engine == "shape" else finish(
        session, mapping["cell"], shorten, dejog,
        design["routing"]["orthogonal"], via_optimize, layers)
    st["kept"] = mapping.get("kept") or {}
    return st
