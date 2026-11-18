# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

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
import os
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
    "edge": 300,               # um kept clear inside the substrate / I/O ring
    "bond_reach": 1500,        # um from a die pad to its bond-finger landing
    "thermal": 1.0,            # strength of the thermal term (0 = off)
    #: um of wire one crossing of two nets' SLI lines is worth (0 = ignore):
    #: every crossing is a crossover or a jumper once routed
    "sli_crossing": 3000.0,
    #: weight of routing room (0 = off): um of wire one um of track with no
    #: room to run is worth -- see hlayout.core.autoplace (congestion)
    "congestion": 0.0,
    #: crossover room: a straight-line crossing near a printed resistor or
    #: a part body (no room for a crossover strip) counts 1 + this crossings
    "xo_room": 0.0,
    #: routing room (0 = off): um of wire one track^2 of overflow is worth --
    #: track demand vs the tracks the gaps between parts can hold, on a grid
    #: of 4 track pitches over the area inside the I/O ring (hlayout.core.routeroom)
    "routing_room": 0.0,
    #: pin access (um, 0 = off): extra room on the sides a part's pins face --
    #: two facing pin sides get this much more between them (one routing
    #: channel = track + 2 spacings, less the clearance)
    "pin_halo": 0.0,
    #: soft gap (um, 0 = off): parts nearer each other than this beyond the
    #: clearance cost soft_gap_weight * shortfall^2 / 1000 um of wire --
    #: evens the whitespace out instead of packing parts at the clearance
    "soft_gap": 0.0,
    "soft_gap_weight": 1.0,
    "effort": 1.0,
    "seed": 0,
    "resistor_layer": None,    # conductor for generated resistors (None: tech file)
    "footprint_map": {},       # KiCad "Lib:Name" -> cell
    #: False (the flow): printed resistors must be designed in the Resistor
    #: Designer first; True: Auto Place sizes undesigned ones itself
    "auto_size_resistors": False,
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


CELLMAP_SUFFIX = ".cellmap.json"


def cellmap_path(netlist_path: str) -> str:
    """The cell map kept next to a netlist: <netlist>.cellmap.json."""
    return str(netlist_path) + CELLMAP_SUFFIX


def load_cellmap(netlist_path) -> dict:
    """The footprint / ref -> cell map saved next to ``netlist_path``
    ({} when there is none or it can't be read)."""
    if not netlist_path:
        return {}
    try:
        with open(cellmap_path(netlist_path), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    m = data.get("footprint_map", data) if isinstance(data, dict) else {}
    return {str(k): str(v) for k, v in m.items() if v} if isinstance(m, dict) else {}


def save_cellmap(netlist_path, fmap: dict) -> Optional[str]:
    """Write the cell map next to the netlist, so the cells chosen for
    the parts survive closing the window, an undo, or quitting without
    saving the design. Returns the path, or None if it can't be written."""
    if not netlist_path:
        return None
    path = cellmap_path(netlist_path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"netlist": os.path.basename(str(netlist_path)),
                       "footprint_map": dict(sorted(fmap.items()))}, f, indent=2)
            f.write("\n")
    except OSError:
        return None
    return path


def merged_footprint_map(session, netlist_path) -> dict:
    """The design's footprint map with any entries only the netlist's
    cell map file has added back in (and saved into the design)."""
    S = load_settings(session.library)
    fmap = dict(S["footprint_map"])
    extra = {k: v for k, v in load_cellmap(netlist_path).items() if k not in fmap}
    if extra:
        fmap.update(extra)
        save_settings(session.library, {"footprint_map": fmap})
    return fmap


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


def io_pads(library, cell, boundary=None, _depth=0, _xform=None) -> list:
    """[(pin, (x0, y0, x1, y1))] of the module's I/O pads in ``cell``: the
    numbered pads drawn in the cell itself and those inside placed
    instances with no ref-des label -- a substrate cell carrying its pad
    row (SUBSTRATE_40): one with geometry on the ``boundary`` (substrate)
    layer. Pads of labelled parts (R1, U1) are the parts' own, and an
    unlabelled package (30_PIN_pkg) holds header pins, not I/O pads."""
    out = []
    if cell is None or _depth > 16:
        return out
    f = _xform or (lambda p: p)
    for sh in cell.shapes:
        pin = getattr(sh, "pin", "")
        if pin and isinstance(sh, (Rectangle, Polygon)):
            pts = [f(p) for p in sh.points]
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            out.append((str(pin), (min(xs), min(ys), max(xs), max(ys))))
    if boundary is None or int(boundary) < 0:
        return out
    for r in cell.srefs:
        child = library.cells.get(r.cell_name)
        if r.label or child is None or not any(sh.layer == boundary for sh in child.shapes):
            continue
        g = _xf(r)
        out.extend(io_pads(library, child, boundary, _depth + 1,
                           (lambda f, g: (lambda p: f(g(p))))(f, g)))
    return out


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


def library_cells(session) -> dict:
    """{cell name: source library} of every cell Auto Place can use: the
    design's own, then the attached libraries' (a name the design already
    has is the design's)."""
    out = {n: session.library for n in session.library.cells}
    for att in getattr(session, "attached_libraries", None) or []:
        for n in att.cells:
            out.setdefault(n, att)
    return out


def use_cell(session, name: str) -> str:
    """Make sure cell ``name`` is in the design: copy it -- with the cells
    it places -- from the attached library that has it. Returns its name
    in the design (the same unless that name was taken)."""
    lib = session.library
    if name in lib.cells:
        return name
    for att in getattr(session, "attached_libraries", None) or []:
        if name in att.cells:
            return lib.import_cell_closure(att, name)
    raise LayoutError(f"no cell {name!r} in the design or its attached libraries")


class _Cells:
    """A library-like view (``.cells``: name -> Cell) over the design's and
    the attached libraries' cells together."""

    def __init__(self, session):
        self.cells = {n: src.cells[n] for n, src in library_cells(session).items()}


def match_footprint(library, footprint, fmap, ref: Optional[str] = None) -> Optional[str]:
    """KiCad footprint -> design cell: the map, an exact cell name, else the
    cell sharing all the footprint name's numbers and most of its words.
    A part whose netlist gives no footprint can still be mapped by its
    reference designator (footprint_map key "ref:<REF>", set by Auto
    Place's Map footprint)."""
    if ref and fmap.get(f"ref:{ref}") in library.cells:
        return fmap[f"ref:{ref}"]
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
    try:
        board_locked = session.locked_parts(name)        # LOCK on the board
    except Exception:
        board_locked = set()
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
    avail = _Cells(session)                      # the design's cells + the attached ones
    # the flow: netlist read, resistors designed (Resistor Designer), then
    # place -- a printed resistor not yet designed stops the job here
    from .netlist_flow import resistor_status
    todo = [r for r in resistor_status(session, nl, placed=set(placed))["undesigned"]
            if (only is None or r in only) and not (ov.get(r, {}).get("cell"))]
    if todo and not S.get("auto_size_resistors"):
        raise LayoutError(
            f"design the printed resistors first: {', '.join(todo)} -- Tools > Read "
            "Netlist... puts them in the Resistor Designer, where each needs an ink and "
            "a size (Use This Solution) before Auto Place can lay them out")
    clr = float(S["clearance"])
    # footprints include the parts' conductor pads: two that touch are a
    # short (a C11 pad against R12's at clearance 0), so never closer than
    # the copper spacing the router keeps
    clr = max(clr, _copper_space(session))
    # pin access halo (um, 0 = off): extra room on the sides a part's pins face
    halo = float(S.get("pin_halo", 0.0) or 0.0)
    reach = float(S["bond_reach"])
    try:
        from ..io.techfile import load_resistor_geometry_rules
        _rg = load_resistor_geometry_rules(lib)
        res_ink = {int(v) for v in (_rg.get("ink_layers") or {}).values()}
        res_ko = _rg.get("keepout_layer")
        res_ko = int(res_ko) if isinstance(res_ko, int) and res_ko >= 0 else None
    except Exception:
        res_ink, res_ko = set(), None
    parts, info = [], {"skipped": [], "generated": [], "unmapped": [], "missing_pins": []}
    idx = {}
    for ref, comp in nl.components.items():
        o = ov.get(ref, {})
        sref = placed.get(ref)
        forced = o.get("cell") or comp.fields.get("HL_Cell")
        if sref is not None:
            cellname = sref.cell_name
        elif forced and forced in avail.cells:
            cellname = use_cell(session, forced)
        else:
            cellname = match_footprint(avail, comp.footprint, S["footprint_map"], ref=ref)
            if cellname is not None:
                cellname = use_cell(session, cellname)  # from an attached library: copy it in
        fam = (o.get("family") or "").lower() or _family(lib, comp, cellname, die_layers)
        if cellname is None and fam == "resistor":
            cellname = _make_resistor(session, comp, o, S, info)
            sref = next((r for r in cell.srefs if r.cell_name == cellname), None)
        if cellname is None:
            (info["unmapped"] if comp.footprint else info["skipped"]).append(ref)
            continue
        # a placed package (J1 -> 30_PIN_pkg round the substrate) is no part
        # either: its pins act as anchors below; a placed connector stays a part
        if fam != "die" and _is_header(ref, comp, cellname) and (
                sref is None or any(w in cellname.lower() for w in ("pkg", "package", "kovar"))):
            # the module's header / package (J30 -> 30_PIN_pkg): the
            # substrate sits inside it, so it is no part to place on the
            # substrate -- its pins reach the circuit through the I/O pads
            info.setdefault("header", []).append(ref)
            continue
        pins = cell_pins(lib, cellname)
        # wire bonds a die part carries are not its body (bond_reach
        # already leaves room for them, below)
        body = _body_bbox(lib, cellname, skip_layers=bond_layers) or (-500, -500, 500, 500)
        if res_ko is not None and _has_layers(lib, cellname, res_ink):
            # a printed resistor's footprint is its copper, ink and glaze --
            # not its dielectric keep-out, which only keeps crossovers off
            # the ink (another part may sit in it; the router keeps the ink
            # itself clear of tracks)
            body = _body_bbox(lib, cellname, skip_layers=bond_layers | {res_ko}) or body
        if fam == "die":
            from .fanout import die_pads, fan_params, predict_fingers
            # numbered pads off the die pad layers -- the die's own bond
            # fingers, or its attach pad / backside contact (a transistor's
            # collector): those nets reach the substrate where they are
            lands = {}
            for pin, lay, b in _pinned_shapes(lib, cellname):
                if lay not in die_layers:
                    lands.setdefault(pin, b)
            # the other die pads are bonded out by Fan Out Die Bonds: the
            # footprint is the die and its attach pad plus the fingers that
            # will draw, exactly where it will draw them
            # an attach pad's rim narrower than a track is widened outward by the
            # router to take one (hybridroute: the backside contact's strips) --
            # that copper is part of the footprint too
            core_d = _body_bbox(lib, cellname, skip_layers=set(range(256)) - set(die_layers))
            if core_d:
                wmin = _track_width(session)
                for q, lb_ in list(lands.items()):
                    if lb_[0] <= core_d[0] and lb_[1] <= core_d[1] and \
                            lb_[2] >= core_d[2] and lb_[3] >= core_d[3]:
                        lands[q] = (min(lb_[0], core_d[0] - wmin), min(lb_[1], core_d[1] - wmin),
                                    max(lb_[2], core_d[2] + wmin), max(lb_[3], core_d[3] + wmin))
            dpads = die_pads(lib, cellname, sorted(die_layers))
            wire = [q for q in dpads if q not in lands]
            die_box = _body_bbox(lib, cellname, skip_layers=bond_layers) or body
            fw, fl, fsp, fgp, flo, _fhi, ftan = fan_params(session)
            pred = predict_fingers(die_box, dpads, wire, fw, fl, fsp, fgp, flo, ftan) if wire else {}
            out = {}
            for q, xy in pins.items():
                if q in pred:
                    out[q] = pred[q][0]
                elif q in lands:
                    b = lands[q]
                    out[q] = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)
                else:
                    out[q] = xy
            pins = out
            boxes = [die_box] + list(lands.values()) + [rect for _c, rect in pred.values()]
            body = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes))
        bb = (body[0] - clr / 2, body[1] - clr / 2, body[2] + clr / 2, body[3] + clr / 2)
        if halo > 0 and pins:
            # pin access: a side the part's pins face gets half a routing
            # channel more, so two facing pin sides leave a track's way out
            # between them (the escape a hand placement leaves); bare sides
            # keep just the clearance
            w_, h_ = max(body[2] - body[0], 1.0), max(body[3] - body[1], 1.0)
            near = lambda v, e, span: abs(v - e) <= span / 3.0
            sides = [any(near(x, body[0], w_) for x, _y in pins.values()),
                     any(near(y, body[1], h_) for _x, y in pins.values()),
                     any(near(x, body[2], w_) for x, _y in pins.values()),
                     any(near(y, body[3], h_) for _x, y in pins.values())]
            hh = halo / 2.0
            bb = (bb[0] - (hh if sides[0] else 0), bb[1] - (hh if sides[1] else 0),
                  bb[2] + (hh if sides[2] else 0), bb[3] + (hh if sides[3] else 0))
        power = _f(o.get("power_mw") or comp.fields.get("HL_Power_mW"),
                   S["family_power_mw"].get(fam, 0.0))
        locked = (ref in board_locked or
                  (o.get("locked") or comp.fields.get("HL_Fixed") or "").lower() in ("1", "yes", "true", "y"))
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
    for pin, b in io_pads(lib, cell, session._boundary_layer()):
        net = names.get(f"IO.{pin}")
        if net:
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
    if rw <= 0 or rh <= 0:
        raise LayoutError(f"the placement area is empty ({rw / 1000:.1f} x {rh / 1000:.1f} mm "
                          "inside the edge margin) -- check the substrate outline and the "
                          "Auto Place edge setting")
    # the parts must fit: their footprints (clearance included) against the area
    area = lambda p: (p.bbox[2] - p.bbox[0]) * (p.bbox[3] - p.bbox[1])
    need = sum(area(p) for p in parts)
    if need > rw * rh:
        big = sorted(parts, key=area, reverse=True)[:3]
        raise LayoutError(
            f"the parts need {need / 1e6:.0f} mm\u00b2 (clearance {clr:g} um included) but "
            f"the placement area is {rw / 1000:.1f} x {rh / 1000:.1f} mm = "
            f"{rw * rh / 1e6:.0f} mm\u00b2. Largest: "
            + ", ".join(f"{p.ref} ({p.cell}) {(p.bbox[2] - p.bbox[0]) / 1000:.1f} x "
                        f"{(p.bbox[3] - p.bbox[1]) / 1000:.1f} mm" for p in big)
            + " -- a bigger substrate, a smaller clearance, or fewer parts")
    obstacles = [o for o in obstacles if (o[2] - o[0]) * (o[3] - o[1]) < 0.4 * rw * rh]
    placer = Placer(parts, nets, region, grid=float(S["grid"]), thermal=float(S["thermal"]),
                    seed=int(S["seed"]), obstacles=obstacles,
                    cross_weight=float(S.get("sli_crossing", 3000.0) or 0.0),
                    cong_weight=float(S.get("congestion", 0.0) or 0.0),
                    pitch=_track_pitch(session),
                    xo_room=float(S.get("xo_room", 0.0) or 0.0),
                    room_weight=float(S.get("routing_room", 0.0) or 0.0),
                    room_area=_route_area(session, cell, region))
    placer.gap_soft = float(S.get("soft_gap", 0.0) or 0.0)
    placer.gap_weight = float(S.get("soft_gap_weight", 1.0) or 1.0)
    info.update(settings=S, netlist=nl, cell=name, region=region)
    return placer, info


def _route_area(session, cell, region):
    """The area tracks may use: inside the I/O ring (pads' inner edge plus
    the entry strip), else the substrate, else the placement region."""
    from ..core.routeroom import route_area
    try:
        ring = session._io_ring_box(cell)
    except Exception:
        ring = None
    try:
        sub = session._substrate_box(cell)
    except Exception:
        sub = None
    return route_area(region, ring, sub)




def _has_layers(library, cell_name, layers, _depth=0):
    """True if the cell (or anything it places) draws on one of ``layers``."""
    c = library.cells.get(cell_name)
    if c is None or not layers or _depth > 16:
        return False
    return any(sh.layer in layers for sh in c.shapes) or any(
        _has_layers(library, r.cell_name, layers, _depth + 1) for r in c.srefs)

def _pinned_shapes(library, cell_name, _depth=0, _xform=None):
    """[(pin, layer, bbox)] of a cell's numbered pads through its hierarchy
    -- numbered shapes, and numbered instances (a die's chip_pin pads) on
    each layer they draw -- in the cell's coordinates."""
    out = []
    cell = library.cells.get(cell_name)
    if cell is None or _depth > 16:
        return out
    xf = _xform or (lambda p: p)

    def box(b, f):
        pts = [f((b[0], b[1])), f((b[2], b[3])), f((b[0], b[3])), f((b[2], b[1]))]
        return (min(p[0] for p in pts), min(p[1] for p in pts),
                max(p[0] for p in pts), max(p[1] for p in pts))

    def layers_of(cn, depth=0):
        c = library.cells.get(cn)
        if c is None or depth > 16:
            return set()
        ls = {sh.layer for sh in c.shapes}
        for r in c.srefs:
            ls |= layers_of(r.cell_name, depth + 1)
        return ls

    for sh in cell.shapes:
        pin = getattr(sh, "pin", "")
        if pin and isinstance(sh, (Rectangle, Polygon)):
            out.append((str(pin), sh.layer, box(sh.bbox(), xf)))
    for r in cell.srefs:
        comp = (lambda f, g: (lambda p: f(g(p))))(xf, _xf(r))
        if getattr(r, "pin", ""):
            b = library.cell_bbox(r.cell_name)
            if b:
                for lay in sorted(layers_of(r.cell_name)):
                    out.append((str(r.pin), lay, box(b, comp)))
        else:
            out.extend(_pinned_shapes(library, r.cell_name, _depth + 1, comp))
    return out


def _track_width(session) -> float:
    """The widest routed track (signal / power width of the Autoroute rules)."""
    try:
        R = session.freerouting_rules()
        return float(max(float(R.get("signal_width", 300) or 300),
                         float(R.get("power_width", 0) or 0)))
    except Exception:
        return 300.0

def _copper_space(session) -> float:
    """The minimum copper-to-copper spacing (Autoroute clearance, raised to
    the DRC's spacing rules on the route layers)."""
    try:
        R = session._rules_with_drc(session.freerouting_rules())
        return max(0.0, float(R.get("clearance", 0) or 0))
    except Exception:
        return 0.0


def _track_pitch(session) -> float:
    """Track + spacing (um) from the Autoroute rules, for the congestion term."""
    try:
        R = session.freerouting_rules()
        return float(R.get("signal_width", 300)) + float(R.get("clearance", 150))
    except Exception:
        return 400.0


_HEADER_REF = re.compile(r"^(J|P|CN|CON|HDR)\d", re.I)
_HEADER_WORDS = ("pkg", "package", "header", "conn", "socket", "dip_kovar", "kovar")


def _is_header(ref, comp, cellname):
    """A part that is the module's header / package or a connector (J30,
    Conn_01x30_Socket, 30_PIN_pkg) -- not something placed on the substrate."""
    # a KiCad footprint's library nickname is no clue: Package_TO_SOT_THT:TO-39-3
    # is a transistor, not a package -- only the footprint's own name counts
    fp = str(getattr(comp, "footprint", "") or "").rsplit(":", 1)[-1]
    text = " ".join(str(t or "") for t in (cellname, getattr(comp, "value", ""), fp)).lower()
    return bool(_HEADER_REF.match(str(ref))) or any(w in text for w in _HEADER_WORDS)


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
    # no substrate and no I/O pads: there is nothing to place inside (the
    # outline of whatever is already drawn -- a row of resistors, say --
    # is no placement area, and a search for room in it never ends)
    raise LayoutError("no substrate outline or I/O pads to place inside -- draw the substrate "
                      "outline (its layer: Setup > Technology Properties, Route tab, boundary "
                      "layer) and the I/O pads first")


def _make_resistor(session, comp, o, S, info):
    """A printed resistor: built from its design in the Resistor Designer
    (the flow: Read Netlist, design, place) -- or, with the job setting
    auto_size_resistors, sized here from its value -- generated as a cell
    + one instance (moved later)."""
    from ..gui.resistor_designer import DesignRules, Resistor, width_for_power
    from ..io.hybrid_routed import parse_ohms
    from .netlist_flow import designed_solution
    designed = designed_solution(session.library, comp.ref)
    if designed is not None:
        sol = designed[1]
        layer = o.get("layer") or comp.fields.get("HL_Layer")
        layer = session.library.layers.resolve(layer) if layer not in (None, "") else \
            resistor_conductor(session.library, S)
        if layer is None:
            raise LayoutError(f"{comp.ref}: no resistor conductor layer in the tech file or "
                              "the job settings")
        cn = session.generate_resistor(0, 0, sol.body_width, sol.body_length, ink_rsh=sol.ink_rsh,
                                       ref_des=comp.ref, conductor_layer=int(layer),
                                       n_folds=sol.n_folds, shape=sol.shape,
                                       shape_params=sol.shape_params)
        for ref in session.library.cells[session.current_cell_name].srefs:
            if ref.cell_name == cn and not ref.label:
                ref.label = comp.ref
        info["generated"].append(comp.ref)
        return cn
    try:
        value = parse_ohms(comp.value)
    except Exception:
        value = None
    if not value:
        info["skipped"].append(comp.ref)
        return None
    power = _f(o.get("power_mw") or comp.fields.get("HL_Power_mW"), 50.0)
    tol = comp.fields.get("Tolerance") or comp.fields.get("HL_Tolerance") or "5%"
    # optional resistor shape from the schematic (HL_Shape = tophat, u,
    # angle, ... or its label); default rectangle
    from ..core import tf_shapes
    shape = str(o.get("shape") or comp.fields.get("HL_Shape") or "rect").strip()
    shape = shape.lower() if shape.lower() in tf_shapes.SHAPE_KEYS else \
        tf_shapes.SHAPE_BY_LABEL.get(shape, "rect")
    r = Resistor(ref_des=comp.ref, value=value, tolerance=tol, power_mw=power,
                 body_width=width_for_power(power), shape=shape)
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
                                   ref_des=comp.ref, conductor_layer=int(layer),
                                   shape=sol.shape, shape_params=sol.shape_params)
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
    moved = {}
    for p in placer.parts:
        r = by.get(p.ref)
        if r is None:
            cell.add_sref(p.cell, (int(round(p.x)), int(round(p.y))), rotation=float(p.rot), label=p.ref)
        else:
            f_old = _xf(r)
            r.origin = (int(round(p.x)), int(round(p.y)))
            r.rotation = float(p.rot)
            moved[p.ref] = (f_old, _xf(r), r.cell_name)
    # a bonded die's wire bonds and bond fingers move with it
    from .fanout import carry_bonds
    info["carried"] = carry_bonds(session, info["cell"], moved)
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
    for pin, b in io_pads(lib, cell, session._boundary_layer()):
        net = names.get(f"IO.{pin}")
        if net:
            pts.setdefault(net, []).append(((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0))
    # "REF.PIN" -> where its wire bond lands on the substrate
    landing = {}
    try:
        for x, y, pads in session.bond_landings(name):
            for rp in pads:
                landing.setdefault(rp, (float(x), float(y)))
    except Exception:
        landing = {}
    # header bonds: a package pin bonded to its I/O pad reaches the circuit
    # through that pad (already a point of the net), so it is no SLI end
    hb_ends = []
    try:
        from .iopads import _header_bond_layer
        hbl = {_header_bond_layer(session)}
        try:
            from ..io.techfile import load_bond_rules
            hbl.add(int(load_bond_rules(lib)["layer"]))
        except Exception:
            pass
        for sh in cell.shapes:
            if sh.layer in hbl and isinstance(sh, Path) and len(sh.points) >= 2:
                hb_ends += [tuple(sh.points[0]), tuple(sh.points[-1])]
    except Exception:
        hb_ends = []
    cache = {}
    for r in cell.srefs:
        if not r.label:
            continue
        if r.cell_name not in cache:
            cache[r.cell_name] = cell_pins(lib, r.cell_name)
        f = _xf(r)
        header = bool(hb_ends) and _is_header(r.label, None, r.cell_name)
        for pin, xy in cache[r.cell_name].items():
            key = f"{r.label}.{pin}"
            net = names.get(key)
            if net:
                p_ = landing.get(key) or f(xy)
                if header and any(abs(e[0] - p_[0]) <= 900 and abs(e[1] - p_[1]) <= 900
                                  for e in hb_ends):
                    continue
                pts.setdefault(net, []).append(p_)
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
