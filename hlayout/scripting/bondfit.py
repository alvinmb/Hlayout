# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fit the bond fingers of the placed bare dies to their nets
(Session.fit_bonds, FITBONDS, and the last step of Auto Place).

Fan Out Die Bonds puts every finger in a neat row at the shortest legal
distance from its die. Once the parts are placed, a finger is usually
better further out or swung to one side, towards the part its net goes
to. For each wire bond from a die pad to a bond finger this pass tries
the finger at every legal spot:

  * the bond between the Wire Bonds tab's minimum and maximum length,
  * leaving its pad within the departure angle of the die edge's
    outward normal (45 degrees unless the routed design sets another),
  * the finger clear of its die by the fan-out gap (the router's die
    keep-out plus the clearance), clear of every other finger and pad on
    the finger conductor and of every other part by the copper spacing,
    and inside the placement area,
  * the bond crossing no other bond,

and keeps the spot that makes the net's straight-line interconnect (its
minimum spanning tree, the bond's landing as the die pad's point)
shortest, a short bond winning a tie. The finger keeps its size and
stays square to the board; the bond and its landing ball move with it.
Passes repeat until nothing improves. One undo step.
"""
import math
from typing import Optional

from ..core.autoplace import mst
from ..core.errors import LayoutError
from ..core.shapes import Path, Polygon, Rectangle
from .autoplace_session import _body_bbox, _xf, cell_pins, io_pads
from .fanout import die_pads, fan_params

#: a bond's length counts this much against the net length it saves, so
#: of two spots equally good for the net the shorter bond wins
LENGTH_WEIGHT = 0.05


def _seg_cross(a, b, c, d):
    """True if segments ab and cd cross (touching ends don't count)."""
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2, d3, d4 = o(a, b, c), o(a, b, d), o(c, d, a), o(c, d, b)
    return d1 * d2 < 0 and d3 * d4 < 0


def _hit(r, boxes, g=0.0):
    x0, y0, x1, y1 = r
    for b in boxes:
        if x0 < b[2] + g and x1 > b[0] - g and y0 < b[3] + g and y1 > b[1] - g:
            return True
    return False


def _world_box(f, b):
    pts = [f((b[0], b[1])), f((b[2], b[3])), f((b[0], b[3])), f((b[2], b[1]))]
    return (min(p[0] for p in pts), min(p[1] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts))


def fit_bonds(session, cell_name: Optional[str] = None, refs=None, passes: int = 4,
              length_step: float = 50.0, angle_step: float = 5.0) -> dict:
    """Move each bonded die's fingers within the bond length and angle
    rules to shorten their nets (see the module docstring). ``refs``: just
    these dies. Returns {"fingers": moved, "dies": {ref: moved},
    "saved_um": net length saved, "checked": fingers looked at}."""
    from ..io.techfile import load_bond_rules, load_net_names
    lib = session.library
    name = cell_name or session.current_cell_name
    if name is None:
        raise LayoutError("no current cell")
    cell = lib.get_cell(name)
    br = load_bond_rules(lib)
    pad_layers = sorted(int(l) for l in (br.get("die_pad_layers") or []) if int(l) >= 0)
    blay = int(br.get("layer", -1))
    out = {"fingers": 0, "dies": {}, "saved_um": 0.0, "checked": 0}
    if not pad_layers or blay < 0:
        return out
    _fw, _fl, _sp, gp, _lo, _hi, _tan = fan_params(session)
    hb = session._hybrid_bond_rules()
    lo, hi = float(hb["min"]), float(hb["max"])
    lo, hi = max(lo, float(br.get("min_length", lo))), min(hi, float(br.get("max_length", hi)))
    amax = max(0.0, min(85.0, float(hb.get("angle", 45.0))))
    try:
        R = session._rules_with_drc(session.freerouting_rules())
        space = float(R.get("clearance", 100) or 100)
    except Exception:
        space = 100.0
    try:
        route = set(session._route_layers())
    except LayoutError:
        route = set()
    names = load_net_names(lib)
    want = {str(r).strip() for r in refs} if refs else None
    tol = 2.0

    # -- the dies, their pads (world) and their own bodies
    dies = {}
    for r in cell.srefs:
        if not r.label:
            continue
        pads = die_pads(lib, r.cell_name, pad_layers)
        if not pads:
            continue
        f = _xf(r)
        core = _body_bbox(lib, r.cell_name, skip_layers=set(range(256)) - set(pad_layers))
        body = _body_bbox(lib, r.cell_name, skip_layers={blay})
        if not core or not body:
            continue
        dies[r.label] = {"ref": r, "pads": {p: f(xy) for p, xy in pads.items()},
                         "core": _world_box(f, core), "body": _world_box(f, body)}
    pad_owner = [(lab, p, xy) for lab, d in dies.items() for p, xy in d["pads"].items()]

    def owner(pt):
        for lab, p, xy in pad_owner:
            if abs(pt[0] - xy[0]) <= tol and abs(pt[1] - xy[1]) <= tol:
                return lab, p
        return None

    bonds = [sh for sh in cell.shapes if isinstance(sh, Path) and sh.layer == blay
             and len(sh.points) == 2]
    balls = [sh for sh in cell.shapes if isinstance(sh, Polygon) and sh.layer == blay]
    fingers = [sh for sh in cell.shapes if isinstance(sh, Rectangle) and sh.layer in route
               and not getattr(sh, "pin", "")]

    # -- every die-pad-to-finger bond: (die, pin, bond, end on pad, finger, ball)
    items = []
    for sh in bonds:
        a, b = tuple(sh.points[0]), tuple(sh.points[1])
        oa, ob = owner(a), owner(b)
        if (oa is None) == (ob is None):
            continue                         # die to die, or no die at all
        (lab, pin), k = (oa, 0) if oa else (ob, 1)
        if want is not None and lab not in want:
            continue
        land = sh.points[1 - k]
        fg = next((g for g in fingers if g.bbox()[0] - tol <= land[0] <= g.bbox()[2] + tol
                   and g.bbox()[1] - tol <= land[1] <= g.bbox()[3] + tol), None)
        if fg is None:
            continue
        ball = None
        for bl in balls:
            x0, y0, x1, y1 = bl.bbox()
            if abs((x0 + x1) / 2.0 - land[0]) <= tol and abs((y0 + y1) / 2.0 - land[1]) <= tol:
                ball = bl
                break
        items.append({"die": lab, "pin": pin, "bond": sh, "k": k, "finger": fg, "ball": ball})
    if not items:
        return out

    # -- the nets' points: I/O pads, every part's pins (a bonded die pad at
    #    its landing), keyed so one bond's point can be swapped for a candidate
    landing_of = {f"{it['die']}.{it['pin']}": it for it in items}
    netpts = {}
    for pin, b in io_pads(lib, cell, session._boundary_layer()):
        net = names.get(f"IO.{pin}")
        if net:
            netpts.setdefault(net, {})[f"IO.{pin}"] = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)
    for r in cell.srefs:
        if not r.label:
            continue
        f = _xf(r)
        for pin, xy in cell_pins(lib, r.cell_name).items():
            key = f"{r.label}.{pin}"
            net = names.get(key)
            if not net:
                continue
            it = landing_of.get(key)
            netpts.setdefault(net, {})[key] = (tuple(it["bond"].points[1 - it["k"]]) if it
                                               else f(xy))

    def net_len(net, key, pt):
        pts = dict(netpts.get(net) or {})
        pts[key] = pt
        return mst(list(pts.values()))[0] if len(pts) > 1 else 0.0

    # -- what a finger must keep clear of
    region = None
    try:
        rb = session._io_ring_box(cell) or session._substrate_box(cell)
        region = rb.bounds if rb is not None else None
    except Exception:
        region = None
    try:
        from ..io.techfile import load_resistor_geometry_rules
        ko = load_resistor_geometry_rules(lib).get("keepout_layer")
        skip = {blay} | ({int(ko)} if isinstance(ko, int) and ko >= 0 else set())
    except Exception:
        skip = {blay}
    parts = []                                # (label, box) of every other placed part
    for r in cell.srefs:
        if not r.label:
            continue
        b = _body_bbox(lib, r.cell_name, skip_layers=skip) or lib.cell_bbox(r.cell_name)
        if not b:
            continue
        wb = _world_box(_xf(r), b)
        if region and wb[0] <= region[0] and wb[1] <= region[1] and \
                wb[2] >= region[2] and wb[3] >= region[3]:
            continue                          # the package round the substrate
        parts.append((r.label, wb))
    finger_set = {id(it["finger"]) for it in items}
    copper = [sh for sh in cell.shapes if sh.layer in route and id(sh) not in finger_set
              and isinstance(sh, (Rectangle, Polygon, Path))]

    def copper_boxes():
        bx = []
        for sh in copper:
            x0, y0, x1, y1 = sh.bbox()
            w = float(getattr(sh, "width", 0) or 0) / 2.0
            bx.append((x0 - w, y0 - w, x1 + w, y1 + w))
        return bx
    fixed_copper = copper_boxes()

    def normal_of(d, pad):
        x0, y0, x1, y1 = d["core"]
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        hx, hy = max((x1 - x0) / 2.0, 1.0), max((y1 - y0) / 2.0, 1.0)
        dx, dy = (pad[0] - cx) / hx, (pad[1] - cy) / hy
        if abs(dx) >= abs(dy):
            return (1.0, 0.0) if dx > 0 else (-1.0, 0.0)
        return (0.0, 1.0) if dy > 0 else (0.0, -1.0)

    def move(it, c):
        fg = it["finger"]
        x0, y0, x1, y1 = fg.bbox()
        ox, oy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        dx, dy = int(round(c[0] - ox)), int(round(c[1] - oy))
        if not dx and not dy:
            return False
        fg.points = [(px + dx, py + dy) for px, py in fg.points]
        k = it["k"]
        pts = list(it["bond"].points)
        lx, ly = pts[1 - k]
        pts[1 - k] = (lx + dx, ly + dy)
        it["bond"].points = pts
        if it["ball"] is not None:
            it["ball"].points = [(px + dx, py + dy) for px, py in it["ball"].points]
        return True

    steps_a = max(1, int(amax // max(angle_step, 0.5)))
    moved = {}                                # id(finger) -> die
    with session._batch():
        for _pass in range(max(1, passes)):
            improved = False
            for it in items:
                d = dies[it["die"]]
                key = f"{it['die']}.{it['pin']}"
                net = names.get(key)
                if not net:
                    continue
                if _pass == 0:
                    out["checked"] += 1
                pad = tuple(it["bond"].points[it["k"]])
                land = tuple(it["bond"].points[1 - it["k"]])
                x0, y0, x1, y1 = it["finger"].bbox()
                hw, hh = (x1 - x0) / 2.0, (y1 - y0) / 2.0
                # where the finger now is, and what it costs
                cur_len = math.hypot(land[0] - pad[0], land[1] - pad[1])
                best_c = None
                best = net_len(net, key, land) + LENGTH_WEIGHT * cur_len
                nx, ny = normal_of(d, pad)
                others_f = [g.bbox() for g in (o["finger"] for o in items) if g is not it["finger"]]
                others_b = [(tuple(o["bond"].points[0]), tuple(o["bond"].points[1]))
                            for o in items if o is not it]
                others_b += [(tuple(b.points[0]), tuple(b.points[1])) for b in bonds
                             if not any(b is o["bond"] for o in items)]
                part_boxes = [b for lab, b in parts if lab != it["die"]]
                die_keep = d["body"]
                length = lo
                while length <= hi + 1e-6:
                    for s in range(-steps_a, steps_a + 1):
                        th = math.radians(s * angle_step)
                        if abs(s * angle_step) > amax + 1e-6:
                            continue
                        vx = nx * math.cos(th) - ny * math.sin(th)
                        vy = nx * math.sin(th) + ny * math.cos(th)
                        c = (round(pad[0] + vx * length), round(pad[1] + vy * length))
                        rect = (c[0] - hw, c[1] - hh, c[0] + hw, c[1] + hh)
                        if region and not (rect[0] >= region[0] and rect[1] >= region[1]
                                           and rect[2] <= region[2] and rect[3] <= region[3]):
                            continue
                        if _hit(rect, [die_keep], gp):
                            continue
                        if _hit(rect, others_f, space) or _hit(rect, fixed_copper, space) or \
                                _hit(rect, part_boxes, space):
                            continue
                        if any(_seg_cross(pad, c, a, b) for a, b in others_b):
                            continue
                        cost = net_len(net, key, c) + LENGTH_WEIGHT * length
                        if cost < best - 1.0:
                            best, best_c = cost, c
                    length += length_step
                if best_c is not None:
                    before = net_len(net, key, land)
                    if move(it, best_c):
                        netpts[net][key] = (float(best_c[0]), float(best_c[1]))
                        moved[id(it["finger"])] = it["die"]
                        out["saved_um"] += before - net_len(net, key, best_c)
                        improved = True
            if not improved:
                break
    out["fingers"] = len(moved)
    for lab in moved.values():
        out["dies"][lab] = out["dies"].get(lab, 0) + 1
    out["saved_um"] = round(out["saved_um"], 1)
    session._notify()
    return out
