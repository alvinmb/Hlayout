"""Auto Place on a design: turn a netlist + the design into the engine's
parts and nets (hlayout.core.autoplace), run it, write the result back.

Used by Session.auto_place() / Session.sli() / Session.autoplace_settings();
see the Auto Place chapter of the user manual.

Parts. Every netlist component with a cell becomes a part:
  * a placed instance labelled with its ref designator -> that cell, at its
    current position (the start point; locked parts never move);
  * else the KiCad footprint mapped to a cell (settings footprint_map, else
    a cell whose name shares the footprint's package letters and pin count,
    LCC-28_FK -> LCC_LCC28);
  * a printed resistor (HL_Family resistor, or ref R* with a value and no
    footprint) is sized by the Resistor Designer and generated as a cell on
    the job's resistor conductor.
A component with no cell (an I/O connector such as J1 "Input pads") is not
placed: its nets reach the design's own I/O pads by net name instead.

Pins are the cell's numbered pads (Rectangle/Polygon .pin, SRef .pin). A
bare die's pins are pushed out by bond_reach to where its bond fingers will
be, and its footprint grows by the same ring, so wire-bond room is kept.
"""
import json
import math
import re
from typing import Dict, Optional

from ..core.autoplace import Part, PNet, Placer, mst, rot_pt
from ..core.errors import LayoutError
from ..core.shapes import Path, Polygon, Rectangle

SETTINGS_KEY = "autoplace_settings"

DEFAULTS = {
    "grid": 50,
    "clearance": 250,          # um between part footprints
    "edge": 1000,              # um kept clear inside the substrate / I/O ring
    "bond_reach": 1500,        # um from a die pad to its bond-finger landing
    "thermal": 1.0,            # strength of the thermal term (0 = off)
    "effort": 1.0,
    "seed": 0,
    "resistor_layer": None,    # conductor for generated resistors (None: tech file)
    "footprint_map": {},       # KiCad "Lib:Name" -> cell
    "weights": {},             # net name -> weight
    "class_weights": {"signal": 1.0, "power": 1.0, "ground": 1.0, "clock": 1.0, "analog": 1.0},
    "two_pin_weight": 1.2,
    "die_net_weight": 1.5,
    "family_power_mw": {"die": 100.0, "lcc": 100.0, "smd": 0.0, "resistor": 50.0},
    "last_netlist": "",
}


def resistor_conductor_default(library) -> Optional[int]:
    """The tech file's resistor termination conductor (Setup > Resistor
    Geometry "Conductor pad layer"), else the 1st route layer."""
    from ..io.techfile import load_resistor_geometry_rules, load_route_rules
    n = load_resistor_geometry_rules(library).get("conductor_layer")
    if isinstance(n, int) and not isinstance(n, bool) and n >= 0:
        return n
    n = load_route_rules(library).get("layer_a")
    return int(n) if n is not None else None


def conductor_layers(library) -> list:
    """Every layer the tech file gives the conductor role (resistor rules'
    conductor pad layer, the route layers, thermal materials), sorted."""
    from ..core.thermal_build import layer_roles
    return sorted(n for n, r in layer_roles(library).items() if r == "conductor")


def resistor_conductor(library, settings=None) -> Optional[int]:
    """The job's resistor conductor: the setting if given, else the tech file."""
    S = settings if settings is not None else load_settings(library)
    v = S.get("resistor_layer")
    if v not in (None, ""):
        return int(v)
    return resistor_conductor_default(library)


def load_settings(library) -> dict:
    out = json.loads(json.dumps(DEFAULTS))
    raw = library.tech_params.get(SETTINGS_KEY)
    if raw:
        try:
            data = json.loads(raw)
            for k, v in data.items():
                if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                    out[k].update(v)
                elif k in out:
                    out[k] = v
        except (ValueError, TypeError):
            pass
    return out


def save_settings(library, settings: dict) -> dict:
    cur = load_settings(library)
    for k, v in (settings or {}).items():
        if k in cur:
            if k == "resistor_layer":
                v = None if v in (None, "") else library.layers.resolve(v)
            cur[k] = v
    library.tech_params[SETTINGS_KEY] = json.dumps(cur)
    return cur


# --- cells and pins ----------------------------------------------------------------

def _xf(ref):
    th = math.radians(ref.rotation)
    c, s = math.cos(th), math.sin(th)
    m = ref.magnification or 1.0
    ox, oy = ref.origin

    def f(p):
        x, y = p
        if ref.x_reflection:
            y = -y
        x, y = x * m, y * m
        return (x * c - y * s + ox, x * s + y * c + oy)
    return f


def cell_pins(library, cell_name, _depth=0, _xform=None) -> Dict[str, tuple]:
    """{pin: (x, y)} -- every numbered pad of a cell (its own shapes and
    nested instances), at the pad's centre, in the cell's coordinates.
    A pin numbered twice keeps its first pad."""
    out = {}
    cell = library.cells.get(cell_name)
    if cell is None or _depth > 16:
        return out
    xf = _xform or (lambda p: p)
    for sh in cell.shapes:
        pin = getattr(sh, "pin", "")
        if pin and isinstance(sh, (Rectangle, Polygon)):
            x0, y0, x1, y1 = sh.bbox()
            out.setdefault(str(pin), xf(((x0 + x1) / 2.0, (y0 + y1) / 2.0)))
    for r in cell.srefs:
        child = _xf(r)
        comp = (lambda f, g: (lambda p: f(g(p))))(xf, child)
        if getattr(r, "pin", ""):
            sub = library.cells.get(r.cell_name)
            b = library.cell_bbox(r.cell_name) if sub is not None else None
            c = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0) if b else (0.0, 0.0)
            out.setdefault(str(r.pin), comp(c))
        else:
            for k, v in cell_pins(library, r.cell_name, _depth + 1, comp).items():
                out.setdefault(k, v)
    return out


def _body_bbox(library, cell_name, skip_layers=(), skip_pinned_layers=()):
    cell = library.cells.get(cell_name)
    if cell is None:
        return None
    xs, ys = [], []
    for sh in cell.shapes:
        if sh.layer in skip_layers:
            continue
        if getattr(sh, "pin", "") and sh.layer in skip_pinned_layers:
            continue
        b = sh.bbox()
        xs += [b[0], b[2]]
        ys += [b[1], b[3]]
    for r in cell.srefs:
        b = library.cell_bbox(r.cell_name)
        if b:
            f = _xf(r)
            for p in (f((b[0], b[1])), f((b[2], b[3])), f((b[0], b[3])), f((b[2], b[1]))):
                xs.append(p[0])
                ys.append(p[1])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _tokens(name):
    return [t.lower() for t in re.findall(r"[A-Za-z]+|\d+", name or "")]


def match_footprint(library, footprint, fmap) -> Optional[str]:
    """KiCad footprint -> design cell: the map, an exact cell name, else the
    cell sharing all the footprint name's numbers and most of its words."""
    if not footprint:
        return None
    if footprint in fmap and fmap[footprint] in library.cells:
        return fmap[footprint]
    name = footprint.split(":")[-1]
    if name in library.cells:
        return name
    want = _tokens(name)
    nums = {t for t in want if t.isdigit()}
    words = {t for t in want if not t.isdigit() and len(t) > 1}
    best, score = None, 0
    for cn in library.cells:
        have = set(_tokens(cn))
        joined = "".join(_tokens(cn))
        if nums and not all(n in have or n in joined for n in nums):
            continue
        sc = sum(1 for w in words if w in have or w in joined)
        if sc > score:
            best, score = cn, sc
    return best


def _family(library, comp, cell, die_layers):
    f = (comp.fields.get("HL_Family") or "").strip().lower()
    if f in ("lcc", "die", "resistor", "smd"):
        return f
    if cell:
        c = library.cells.get(cell)
        if c is not None and die_layers:
            def has_die(cn, d=0):
                cc = library.cells.get(cn)
                if cc is None or d > 8:
                    return False
                return any(s.layer in die_layers for s in cc.shapes) or \
                    any(has_die(r.cell_name, d + 1) for r in cc.srefs)
            if has_die(cell):
                return "die"
        if "lcc" in cell.lower() or "lcc" in (comp.footprint or "").lower():
            return "lcc"
        return "smd"
    if comp.ref[:1].upper() == "R" and not comp.footprint:
        return "resistor"
    return ""


def _net_class(net, settings):
    c = (net.cls or "").strip().lower()
    if c and c != "default":
        if "gnd" in c or "ground" in c:
            return "ground"
        if "pow" in c or "vcc" in c or "supply" in c:
            return "power"
        return c
    types = {n.pintype for n in net.nodes}
    nm = net.name.upper()
    if nm in ("GND", "AGND", "DGND", "VSS", "0V") or nm.endswith("GND"):
        return "ground"
    if types & {"power_in", "power_out"} or nm.startswith(("VCC", "VDD", "VEE", "+", "V+", "V-")):
        return "power"
    return "signal"


def _f(v, d=0.0):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return d


def _read_overrides(path):
    import csv
    if not path:
        return {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["ref"].strip(): {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
                for r in csv.DictReader(f) if r.get("ref")}


# --- building the job --------------------------------------------------------------

def build_job(session, netlist, cell_name=None, overrides=None, settings=None, only=None):
    """-> (placer, info). ``netlist`` = a path or a hlayout.io.kicad_netlist.Netlist.
    ``only`` = refs allowed to move (the rest fixed where they are)."""
    from ..io.kicad_netlist import Netlist, read_netlist
    from ..io.techfile import load_bond_rules, load_net_names, load_route_rules
    lib = session.library
    name = cell_name or session.current_cell_name
    if name not in lib.cells:
        raise LayoutError("no current cell")
    cell = lib.cells[name]
    S = load_settings(lib)
    S.update(settings or {})
    nl = netlist if isinstance(netlist, Netlist) else read_netlist(netlist)
    ov = overrides if isinstance(overrides, dict) else _read_overrides(overrides)
    try:
        die_layers = {int(x) for x in load_bond_rules(lib).get("die_pad_layers") or []}
    except Exception:
        die_layers = set()
    try:
        bond_layers = {int(load_bond_rules(lib)["layer"])}
    except Exception:
        bond_layers = set()
    placed = {r.label: r for r in cell.srefs if r.label}
    clr = float(S["clearance"])
    reach = float(S["bond_reach"])
    parts, info = [], {"skipped": [], "generated": [], "unmapped": [], "missing_pins": []}
    idx = {}
    for ref, comp in nl.components.items():
        o = ov.get(ref, {})
        sref = placed.get(ref)
        forced = o.get("cell") or comp.fields.get("HL_Cell")
        if sref is not None:
            cellname = sref.cell_name
        elif forced and forced in lib.cells:
            cellname = forced
        else:
            cellname = match_footprint(lib, comp.footprint, S["footprint_map"])
        fam = (o.get("family") or "").lower() or _family(lib, comp, cellname, die_layers)
        if cellname is None and fam == "resistor":
            cellname = _make_resistor(session, comp, o, S, info)
            sref = next((r for r in cell.srefs if r.cell_name == cellname), None)
        if cellname is None:
            (info["unmapped"] if comp.footprint else info["skipped"]).append(ref)
            continue
        pins = cell_pins(lib, cellname)
        # wire bonds a die part carries are not its body (bond_reach
        # already leaves room for them, below)
        body = _body_bbox(lib, cellname, skip_layers=bond_layers) or (-500, -500, 500, 500)
        if fam == "die":
            # a die part may carry its own numbered bond fingers: they ARE
            # where its nets reach the substrate
            fingers = {}
            dcell = lib.cells.get(cellname)
            for sh in (dcell.shapes if dcell is not None else []):
                if getattr(sh, "pin", "") and sh.layer not in die_layers and \
                        isinstance(sh, (Rectangle, Polygon)):
                    b = sh.bbox()
                    fingers.setdefault(str(sh.pin), ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0, b))
            core = _body_bbox(lib, cellname, skip_layers=die_layers | bond_layers,
                              skip_pinned_layers=set(range(256))) or body
            cx, cy = (core[0] + core[2]) / 2.0, (core[1] + core[3]) / 2.0
            hx, hy = (core[2] - core[0]) / 2.0, (core[3] - core[1]) / 2.0
            out = {}
            for p, (x, y) in pins.items():
                if p in fingers:
                    out[p] = fingers[p][:2]
                    continue
                dx, dy = x - cx, y - cy
                if abs(dx) / max(hx, 1) >= abs(dy) / max(hy, 1):
                    out[p] = (x + math.copysign(reach, dx or 1), y)
                else:
                    out[p] = (x, y + math.copysign(reach, dy or 1))
            pins = out
            if fingers:
                fb = [f[2] for f in fingers.values()]
                body = (min(core[0], *(b[0] for b in fb)) - 300, min(core[1], *(b[1] for b in fb)) - 300,
                        max(core[2], *(b[2] for b in fb)) + 300, max(core[3], *(b[3] for b in fb)) + 300)
            else:
                body = (body[0] - reach - 300, body[1] - reach - 300,
                        body[2] + reach + 300, body[3] + reach + 300)
        bb = (body[0] - clr / 2, body[1] - clr / 2, body[2] + clr / 2, body[3] + clr / 2)
        power = _f(o.get("power_mw") or comp.fields.get("HL_Power_mW"),
                   S["family_power_mw"].get(fam, 0.0))
        locked = (o.get("locked") or comp.fields.get("HL_Fixed") or "").lower() in ("1", "yes", "true", "y")
        p = Part(ref=ref, cell=cellname, family=fam or "smd", bbox=bb, pins=pins, power=power)
        if sref is not None:
            p.x, p.y, p.rot, p.placed = float(sref.origin[0]), float(sref.origin[1]), int(sref.rotation) % 360, True
        for key, attr in (("x", "x"), ("y", "y")):
            v = o.get(key) or comp.fields.get("HL_" + key.upper())
            if v:
                setattr(p, attr, _f(v))
                p.placed = True
        if o.get("rotation") or comp.fields.get("HL_Rot"):
            p.rot = int(_f(o.get("rotation") or comp.fields.get("HL_Rot"))) % 360
        p.fixed = locked or (only is not None and ref not in only)
        if p.fixed and not p.placed:
            p.fixed = False          # can't hold a part that has no place yet
        idx[ref] = len(parts)
        parts.append(p)

    # anchors: the design's own named pads that no placed part owns (I/O pads)
    names = load_net_names(lib)
    anchors = {}
    for sh in cell.shapes:
        pin = getattr(sh, "pin", "")
        if pin and isinstance(sh, (Rectangle, Polygon)):
            net = names.get(f"IO.{pin}")
            if net:
                b = sh.bbox()
                anchors.setdefault(net, []).append(((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0))
    for r in cell.srefs:
        if r.label and r.label not in idx:
            f = _xf(r)
            for pin, xy in cell_pins(lib, r.cell_name).items():
                net = names.get(f"{r.label}.{pin}")
                if net:
                    anchors.setdefault(net, []).append(f(xy))

    # obstacles: other placed instances (a decoupling cap the netlist
    # doesn't list, a header) -- the parts must keep clear of them
    obstacles = []
    rarea = None
    for r in cell.srefs:
        if r.label in idx:
            continue
        b = lib.cell_bbox(r.cell_name)
        if not b:
            continue
        f = _xf(r)
        cs = [f((b[0], b[1])), f((b[2], b[3])), f((b[0], b[3])), f((b[2], b[1]))]
        ob = (min(c[0] for c in cs), min(c[1] for c in cs),
              max(c[0] for c in cs), max(c[1] for c in cs))
        obstacles.append(ob)

    nets = []
    for net in nl.nets:
        cls = _net_class(net, S)
        mem = []
        for n in net.nodes:
            i = idx.get(n.ref)
            if i is None:
                continue
            if n.pin not in parts[i].pins:
                info["missing_pins"].append(f"{n.ref}.{n.pin}")
                continue
            mem.append((i, n.pin))
        anc = anchors.get(net.name, [])
        if len(mem) + len(anc) < 2 or not mem:
            continue
        if net.name in S["weights"]:
            w = float(S["weights"][net.name])
        else:
            w = float(S["class_weights"].get(cls, 1.0))
            if len(mem) + len(anc) == 2:
                w *= float(S["two_pin_weight"])
            if any(parts[i].family == "die" for i, _p in mem):
                w *= float(S["die_net_weight"])
        nets.append(PNet(name=net.name, weight=w, members=mem, anchors=anc))

    region = _region(session, cell, float(S["edge"]))
    rw, rh = region[2] - region[0], region[3] - region[1]
    obstacles = [o for o in obstacles if (o[2] - o[0]) * (o[3] - o[1]) < 0.4 * rw * rh]
    placer = Placer(parts, nets, region, grid=float(S["grid"]), thermal=float(S["thermal"]),
                    seed=int(S["seed"]), obstacles=obstacles)
    info.update(settings=S, netlist=nl, cell=name, region=region)
    return placer, info


def _region(session, cell, edge):
    ring = session._io_ring_box(cell)
    sub = session._substrate_box(cell)
    if ring is not None:
        b = ring.bounds
        entry = 300.0
        return (b[0] + entry + edge, b[1] + entry + edge, b[2] - entry - edge, b[3] - entry - edge)
    if sub is not None:
        b = sub.bounds
        return (b[0] + edge, b[1] + edge, b[2] - edge, b[3] - edge)
    bb = session.library.cell_bbox(cell.name)
    if bb is None:
        raise LayoutError("no substrate outline or I/O pads to place inside -- draw the substrate first")
    return bb


def _make_resistor(session, comp, o, S, info):
    """A printed resistor from its value: sized by the Resistor Designer,
    generated as a cell + one instance (moved later)."""
    from ..gui.resistor_designer import DesignRules, Resistor, width_for_power
    from ..io.hybrid_routed import parse_ohms
    try:
        value = parse_ohms(comp.value)
    except Exception:
        value = None
    if not value:
        info["skipped"].append(comp.ref)
        return None
    power = _f(o.get("power_mw") or comp.fields.get("HL_Power_mW"), 50.0)
    tol = comp.fields.get("Tolerance") or comp.fields.get("HL_Tolerance") or "5%"
    r = Resistor(ref_des=comp.ref, value=value, tolerance=tol, power_mw=power,
                 body_width=width_for_power(power))
    r.compute_solutions(DesignRules.from_library(session.library))
    sol = r.selected
    if sol is None:
        info["skipped"].append(comp.ref)
        return None
    layer = o.get("layer") or comp.fields.get("HL_Layer")
    if layer not in (None, ""):
        layer = session.library.layers.resolve(layer)
    else:
        layer = resistor_conductor(session.library, S)
    if layer is None:
        raise LayoutError(f"{comp.ref}: no resistor conductor layer in the tech file or the job settings")
    cn = session.generate_resistor(0, 0, sol.body_width, sol.body_length, ink_rsh=sol.ink_rsh,
                                   ref_des=comp.ref, conductor_layer=int(layer))
    for ref in session.library.cells[session.current_cell_name].srefs:
        if ref.cell_name == cn and not ref.label:
            ref.label = comp.ref
    info["generated"].append(comp.ref)
    return cn


def apply(session, placer, info):
    """Write the placement into the cell: move each part's labelled
    instance, or place a new one. Net names from the netlist are merged in."""
    lib = session.library
    cell = lib.cells[info["cell"]]
    by = {r.label: r for r in cell.srefs if r.label}
    for p in placer.parts:
        r = by.get(p.ref)
        if r is None:
            cell.add_sref(p.cell, (int(round(p.x)), int(round(p.y))), rotation=float(p.rot), label=p.ref)
        else:
            r.origin = (int(round(p.x)), int(round(p.y)))
            r.rotation = float(p.rot)
    refs = {p.ref for p in placer.parts}
    pads = {k: v for k, v in info["netlist"].pad_nets().items() if k.split(".")[0] in refs}
    # the netlist is the truth for the parts it places: their old pad names
    # go (a netlist numbered another way -- LCC terminals vs die pads --
    # would otherwise leave stale names on pads that now mean something
    # else), everything else (I/O pads, parts it doesn't list) stays
    from ..io.techfile import load_net_names
    keep = {k: v for k, v in load_net_names(session.library).items()
            if k.split(".")[0] not in refs}
    keep.update(pads)
    session.set_net_names(keep, replace=True)


# --- straight-line interconnects -----------------------------------------------

def sli(session, cell_name=None, nets=None, unrouted_only=True):
    """Straight-line interconnects of the design as it stands: for every
    named net, the minimum spanning tree over its pads.

    Once there's routing, pads already joined by copper (tracks, vias,
    a bond finger's own track) count as one: the tree only spans what is
    still to connect -- a ratsnest -- and a fully routed net has no SLI
    at all. ``unrouted_only=False`` gives the full pad-to-pad tree.

    Chip-and-wire: a die pad that is already wire-bonded connects to the
    substrate at its bond's LANDING (where the bond finger / track has to
    be), not at the die pad itself, so the SLI runs from the landing. An
    unbonded die pad (before bonding) still uses the pad.
    -> {"nets": [{"net", "length", "segments": [(x0, y0, x1, y1)], "class"}],
        "total": um, "complete": nets fully routed}"""
    from ..io.techfile import load_net_names
    lib = session.library
    name = cell_name or session.current_cell_name
    cell = lib.cells.get(name)
    if cell is None:
        return {"nets": [], "total": 0.0}
    names = load_net_names(lib)
    if not names:
        return {"nets": [], "total": 0.0}
    pts = {}
    for sh in cell.shapes:
        pin = getattr(sh, "pin", "")
        if pin and isinstance(sh, (Rectangle, Polygon)):
            net = names.get(f"IO.{pin}")
            if net:
                b = sh.bbox()
                pts.setdefault(net, []).append(((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0))
    # "REF.PIN" -> where its wire bond lands on the substrate
    landing = {}
    try:
        for x, y, pads in session.bond_landings(name):
            for rp in pads:
                landing.setdefault(rp, (float(x), float(y)))
    except Exception:
        landing = {}
    cache = {}
    for r in cell.srefs:
        if not r.label:
            continue
        if r.cell_name not in cache:
            cache[r.cell_name] = cell_pins(lib, r.cell_name)
        f = _xf(r)
        for pin, xy in cache[r.cell_name].items():
            key = f"{r.label}.{pin}"
            net = names.get(key)
            if net:
                pts.setdefault(net, []).append(landing.get(key) or f(xy))
    tracer = _routed_tracer(session, cell, name) if unrouted_only else None
    out, total, complete = [], 0.0, 0
    for net, p in pts.items():
        if nets and net not in nets:
            continue
        if len(p) < 2:
            continue
        if tracer is None:
            length, edges = mst(p)
            segs = [(p[a][0], p[a][1], p[b][0], p[b][1]) for a, b in edges]
        else:
            length, segs = _unrouted_tree(tracer, p)
            if not segs:
                complete += 1
                continue
        out.append({"net": net, "length": length, "segments": segs})
        total += length
    out.sort(key=lambda d: -d["length"])
    return {"nets": out, "total": total, "complete": complete}


def _routed_tracer(session, cell, name):
    """The design's net tracer when the cell has any routing (a track on a
    routing layer), else None -- before routing every pad is its own."""
    try:
        la, lb = session._route_layers()
    except Exception:
        return None
    if not any(isinstance(sh, Path) and sh.layer in (la, lb) for sh in cell.shapes):
        return None
    try:
        return session.net_tracer(cell_name=name)
    except Exception:
        return None


def _unrouted_tree(tracer, pts):
    """(length, segments) of the minimum tree joining the copper islands
    ``pts`` sit on: points on the same routed copper are one island, and
    each pair of islands is joined between the nearest points of their
    COPPER -- so once part of a net is routed, its SLI runs from the end
    of that trace, not from the pad it started at."""
    from shapely.geometry import Point
    from shapely.ops import nearest_points, unary_union
    groups, alone = {}, []
    for x, y in pts:
        node = tracer.node_at(x, y, tol=50.0)
        if node is None:
            alone.append(Point(x, y))
        else:
            groups.setdefault(tracer.dsu.find(node), None)
    roots = list(groups)
    if roots:
        members = {}
        for k in range(len(tracer.nodes)):
            r = tracer.dsu.find(k)
            if r in groups:
                members.setdefault(r, []).append(tracer._geoms[k])
        isl = [unary_union(members[r]) for r in roots]
    else:
        isl = []
    isl += alone
    n = len(isl)
    if n < 2:
        return 0.0, []
    cache = {}

    def link(i, j):
        lo, hi = min(i, j), max(i, j)
        if (lo, hi) not in cache:
            a, b = nearest_points(isl[lo], isl[hi])
            cache[(lo, hi)] = (a.distance(b), (a.x, a.y), (b.x, b.y))
        d, pa, pb = cache[(lo, hi)]
        return (d, pa, pb) if i == lo else (d, pb, pa)

    inf = float("inf")
    dist, via, used = [inf] * n, [None] * n, [False] * n
    dist[0] = 0.0
    length, segs = 0.0, []
    for _ in range(n):
        u = min((v for v in range(n) if not used[v]), key=lambda v: dist[v])
        used[u] = True
        if via[u] is not None:
            d, a, b = via[u]
            length += d
            segs.append((a[0], a[1], b[0], b[1]))
        for v in range(n):
            if not used[v]:
                lk = link(u, v)
                if lk[0] < dist[v]:
                    dist[v], via[v] = lk[0], lk
    return length, segs
