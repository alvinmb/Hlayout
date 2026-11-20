# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Remove crossovers the finished routing no longer needs.

A printed-jumper crossover is a COND2 bridge over a local dielectric strip,
with an up-via contact (a pad on both conductors) at each end. The router can
leave one behind that the final routing does not need: something blocked the
way when it was placed and later moved (a nudge, a rip-up).

For each net with a crossover this pass uses the router itself:

  1. notes what the net's routing costs now -- COND1 track at face value,
     COND2 track x COND2_FACTOR, VIA_COST per crossover -- and the pads it
     reaches;
  2. rips up all of the net's routed copper (tracks, bridges, contacts and
     their dielectric strips; pads stay);
  3. re-routes only that net with PathFinder, every other net kept as it is
     (an obstacle), with crossovers made dear (DEAR_VIA, DEAR_COND2) so it
     uses one only where there is no other way through;
  4. keeps the result only if the net is routed in full with fewer
     crossovers, still reaches every pad it did in one piece, adds no short,
     and costs less than before -- otherwise everything is put back as it
     was. A net with one needed and one needless crossover loses just the
     needless one.

Only for local crossovers: a design with a full dielectric sheet is left
alone. Call inside one undo step (Session.remove_needless_crossovers).
"""
import copy
import math
from typing import Optional

from shapely.geometry import LineString, Point, Polygon as SPoly
from shapely.ops import unary_union

from ..core.shapes import Path, Rectangle, Polygon

COND2_FACTOR = 2.5         # COND2 per mm, as PathFinder prices it
VIA_COST = 3000.0          # um of track a crossover's via pair is worth
# the re-route's prices: a crossover only where there is no other way
DEAR_VIA = 100000.0        # um of track a via pair costs in the re-route
DEAR_COND2 = 20.0          # COND2 per mm in the re-route
TIE_UM = 200.0             # within this, fewer crossovers wins a tie


def _geom(sh):
    try:
        if isinstance(sh, Path):
            if len(sh.points) < 2:
                return None
            return LineString(sh.points).buffer(max(sh.width, 1) / 2.0, cap_style=2,
                                                join_style=2)
        pts = getattr(sh, "points", None)
        if pts and len(pts) >= 3:
            g = SPoly(pts)
            return g if g.is_valid else g.buffer(0)
    except Exception:
        return None
    return None


def _length(pts):
    return sum(math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
               for i in range(1, len(pts)))


def _remove(cell, sh):
    """Remove ``sh`` itself (shapes compare by value, so list.remove could
    take an identical twin)."""
    for k, o in enumerate(cell.shapes):
        if o is sh:
            del cell.shapes[k]
            return True
    return False


def _diel_union(cell, diel):
    if diel is None or diel < 0:
        return None
    gs = [g for g in (_geom(sh) for sh in cell.shapes if sh.layer == diel) if g is not None]
    return unary_union(gs) if gs else None


def _components(trace, la, lb, diel_union):
    """The copper of a traced net split into the pieces that really join:
    [{layer: geom}]. Same-layer pieces join where they touch; COND1 and
    COND2 join where they overlap outside the dielectric."""
    pcs = [(l, g) for l, g in (trace.pieces or []) if l in (la, lb) and g is not None
           and not g.is_empty]
    n = len(pcs)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        for j in range(i + 1, n):
            (li, gi), (lj, gj) = pcs[i], pcs[j]
            if not gi.intersects(gj):
                continue
            if li == lj:
                parent[find(i)] = find(j)
                continue
            ov = gi.intersection(gj)
            if diel_union is not None and not diel_union.is_empty:
                ov = ov.difference(diel_union)
            if ov.area > 1.0:
                parent[find(i)] = find(j)
    groups = {}
    for i, (l, g) in enumerate(pcs):
        groups.setdefault(find(i), {}).setdefault(l, []).append(g)
    return [{l: unary_union(gs) for l, gs in grp.items()} for grp in groups.values()]


def find_crossovers(session, cell_name, la, lb, diel):
    """[{"bridge", "contacts": [shapes], "strip": shape|None, "ends": (p, q)}]:
    every COND2 track on ``lb`` with an up-via contact (a pad on both
    conductors) under each end."""
    cell = session.library.get_cell(cell_name)
    rects = [(sh, _geom(sh)) for sh in cell.shapes if isinstance(sh, (Rectangle, Polygon))
             and sh.layer in (la, lb) and not getattr(sh, "pin", "")]
    rects = [(sh, g) for sh, g in rects if g is not None]
    diels = [(sh, _geom(sh)) for sh in cell.shapes if diel is not None and diel >= 0
             and sh.layer == diel]
    out = []
    for br in cell.shapes:
        if not (isinstance(br, Path) and br.layer == lb and len(br.points) >= 2):
            continue
        ends = (tuple(br.points[0]), tuple(br.points[-1]))
        contacts = []
        ok = True
        for e in ends:
            pt = Point(e).buffer(1)
            here = [sh for sh, g in rects if g.intersects(pt)]
            if not ({sh.layer for sh in here} >= {la, lb}):
                ok = False
                break
            contacts.extend(here)
        if not ok:
            continue
        bg = LineString(br.points)
        strips = [sh for sh, g in diels if g is not None and g.intersects(bg)]
        out.append({"bridge": br, "contacts": contacts,
                    "strip": strips[0] if len(strips) == 1 else None, "ends": ends})
    return out


def find_net_crossovers(session, cell_name, la, lb, diel, net):
    """The crossovers (find_crossovers) whose bridge is on ``net``."""
    out = []
    for x in find_crossovers(session, cell_name, la, lb, diel):
        t = _trace(session, cell_name, x["ends"][0])
        if t is not None and t.name == net:
            out.append(x)
    return out


def _trace(session, cell_name, pt):
    try:
        return session.highlight_net(pt, cell_name=cell_name, notify=False)
    except Exception:
        return None


def _net_copper(session, cell, trace, la, lb, diel):
    """The routed copper of a traced net: its tracks on either conductor,
    its contacts (pinless pads) and the dielectric strips under its COND2
    tracks. Pads (pinned shapes, part pads) are not included."""
    lay = {}
    for l, g in (trace.pieces or []):
        lay.setdefault(l, []).append(g)
    u = {l: unary_union(gs).buffer(1) for l, gs in lay.items()}
    track, contacts, strips = [], [], []
    for sh in cell.shapes:
        if sh.layer not in (la, lb) or getattr(sh, "pin", "") or sh.layer not in u:
            continue
        g = _geom(sh)
        if g is None or not u[sh.layer].contains(g):
            continue
        (track if isinstance(sh, Path) else contacts).append(sh)
    if diel is not None and diel >= 0:
        c2 = [LineString(t.points) for t in track if t.layer == lb and len(t.points) >= 2]
        for sh in cell.shapes:
            if sh.layer == diel:
                g = _geom(sh)
                if g is not None and any(g.intersects(b) for b in c2):
                    strips.append(sh)
    return track, contacts, strips


def _cost(tracks, la, lb):
    c1 = sum(_length(t.points) for t in tracks if t.layer == la)
    c2 = sum(_length(t.points) for t in tracks if t.layer == lb)
    return c1 + COND2_FACTOR * c2 + VIA_COST * sum(1 for t in tracks if t.layer == lb)


def remove_needless_crossovers(session, cell_name: Optional[str] = None,
                               time_budget: float = 60.0, on_line=None) -> dict:
    """See the module doc. Returns {"nets", "crossovers", "removed": [{"net",
    "crossovers", "saved_um"}], "kept": [{"net", "why"}]}."""
    from ..io.techfile import load_dielectric_rules, load_net_names
    from . import hybridroute as H
    from . import pathfinder as PF
    say = on_line or (lambda _m: None)
    name = cell_name or session.current_cell_name
    lib = session.library
    cell = lib.get_cell(name)
    la, lb = session._route_layers()
    try:
        diel = int(load_dielectric_rules(lib)["layer"])
    except Exception:
        diel = -1
    if diel >= 0 and session._has_full_dielectric(cell, diel):
        return {"nets": 0, "crossovers": 0, "removed": [], "kept": [],
                "skipped": "full dielectric sheet -- crossovers there are vias"}
    try:
        shorts_before = len(session.find_shorts(cell_name=name))
    except Exception:
        shorts_before = None
    all_nets = {str(v) for v in load_net_names(lib).values() if v}

    xs = find_crossovers(session, name, la, lb, diel)
    by_net, count = {}, {}
    for x in xs:
        t = _trace(session, name, x["ends"][0])
        if t is not None and t.name and "SHORT" not in t.name and t.name != "unnamed":
            by_net.setdefault(t.name, x["ends"][0])
            count[t.name] = count.get(t.name, 0) + 1
    removed, kept = [], []
    for net, seed in sorted(by_net.items()):
        before = _trace(session, name, seed)
        if before is None:
            kept.append({"net": net, "why": "could not be traced"})
            continue
        pins_before = set(before.pins)
        parts_before = len(_components(before, la, lb, _diel_union(cell, diel)))
        tracks, contacts, strips = _net_copper(session, cell, before, la, lb, diel)
        n_x = count.get(net, 0)              # crossovers, not COND2 track pieces
        if not n_x:
            continue
        cost_old = _cost(tracks, la, lb)
        snapshot = list(cell.shapes)
        saved_pts = {id(sh): copy.deepcopy(getattr(sh, "points", None)) for sh in cell.shapes}
        saved_added = lib.tech_params.get(H.ADDED_KEY)
        ok, why = False, ""
        try:
            for sh in tracks + contacts + strips:
                _remove(cell, sh)
            others = sorted(all_nets - {net})
            design, mapping = H.export_design(session, name, layers=2, keep_existing=True,
                                              exclude_nets=others)
            full = bool(mapping.get("full_dielectric"))
            routed = PF.route(design, local_dielectric=True,
                              cond2_keepouts=None if full else mapping.get("local_keepouts"),
                              full_dielectric=full, via_cost=DEAR_VIA,
                              cond2_factor=DEAR_COND2, time_budget=time_budget)
            if routed.get("unrouted"):
                why = "the re-route left a connection open"
            elif len(routed.get("crossovers") or []) >= n_x:
                why = "every crossover is needed"
            else:
                H.apply_routed(session, routed, mapping, remove_existing=False)
                after = _trace(session, name, seed)
                if after is None or not set(after.pins) >= pins_before:
                    why = "the re-route lost a pad"
                elif len(_components(after, la, lb, _diel_union(cell, diel))) > parts_before:
                    why = "the re-route left the net in pieces"
                else:
                    new_tracks, _c, _s = _net_copper(session, cell, after, la, lb, diel)
                    cost_new = _cost(new_tracks, la, lb)
                    n_new = len(find_net_crossovers(session, name, la, lb, diel, net))
                    if n_new >= n_x:
                        why = "every crossover is needed"
                    elif shorts_before is not None and \
                            len(session.find_shorts(cell_name=name)) > shorts_before:
                        why = "the re-route made a short"
                    elif cost_new > cost_old + TIE_UM:
                        why = (f"the route with fewer crossovers ({cost_new / 1000:.1f} mm) is "
                               f"no better than now ({cost_old / 1000:.1f} mm)")
                    else:
                        ok = True
                        removed.append({"net": net, "crossovers": n_x - n_new,
                                        "saved_um": round(cost_old - cost_new)})
                        say(f"{net}: {n_x - n_new} of {n_x} crossover(s) removed, "
                            f"{(cost_old - cost_new) / 1000:.1f} mm saved")
        except Exception as e:
            why = f"router error: {e}"
        if not ok:
            cell.shapes[:] = snapshot
            for sh in cell.shapes:
                if id(sh) in saved_pts and saved_pts[id(sh)] is not None:
                    sh.points = saved_pts[id(sh)]
            if saved_added is None:
                lib.tech_params.pop(H.ADDED_KEY, None)
            else:
                lib.tech_params[H.ADDED_KEY] = saved_added
            kept.append({"net": net, "crossovers": n_x, "why": why})
            say(f"{net}: kept ({why})")
    return {"nets": len(by_net), "crossovers": len(xs), "removed": removed, "kept": kept}
