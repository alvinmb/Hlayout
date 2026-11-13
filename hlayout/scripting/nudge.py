# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""The router's nudge pass: move unlocked parts a little to let the
connections a route left open through.

A connection that won't route is usually stopped by a gap between two parts
that is a hair narrower than a track needs (half the track plus the spacing
from each side). For every open connection:

  * an A* runs on the 1st conductor with the parts' clearance halos made
    *soft* -- a step inside a halo costs more the deeper it goes, and only
    the parts themselves (and the substrate edge) are hard. Routed copper is
    ignored: it is rerouted anyway. The cheapest path is the way through
    that needs the least moving;
  * each part whose halo that path enters is a blocker, by how many um
    (the intrusion) and from which side;
  * an unlocked blocker is moved straight away from the path, along one
    axis, by its intrusion plus ``margin``; where the blocker on one side
    can't move (locked, a header pad, a bond finger of a locked die) the
    part across the channel takes the whole shortfall.

A move is kept only if it is at most ``max_move`` um, the part stays on the
substrate, and every copper feature of it keeps the minimum spacing from
every other part's copper (bodies may not overlap anything). Moves for the
same part from several connections are merged (the largest each way). A bare
die moves with its bond fingers (and wire bonds, see fanout.carry_bonds).
Header I/O pads never move. Locked parts (Session.lock_parts) never move.

plan() works on the hybrid.json design dict (substrate coordinates);
apply() moves the instances in the cell (world coordinates: the same deltas).
"""

import heapq
import math
from typing import Dict, List, Optional

import numpy as np

try:
    from . import shape_router as SR
    from .pathfinder import _Grid, DIRS
except ImportError:                     # run from a scratch folder
    import shape_router as SR
    from pathfinder import _Grid, DIRS


def _rect(c, x, y, w, h):
    return (c[0] + x - w / 2.0, c[1] + y - h / 2.0, c[0] + x + w / 2.0, c[1] + y + h / 2.0)


def features(design) -> List[dict]:
    """Every fixed thing a track must keep clear of, with the part it belongs
    to: {"rect", "owner" (ref-des or None = never moves), "net" (copper) or
    None (a body / die), "kind": pad | body | die}."""
    out = []
    for c in design.get("components") or []:
        cx, cy = float(c["centre"]["x"]), float(c["centre"]["y"])
        out.append({"rect": SR._comp_body_rect(c), "owner": c["refdes"], "net": None,
                    "kind": "body", "chip": c.get("kind") == "CHIP"})
        for k in ("pinA", "pinB"):
            q = c.get(k)
            if q:
                out.append({"rect": _rect((cx, cy), float(q["position"]["x"]), float(q["position"]["y"]),
                                          float(q["widthUm"]), float(q["heightUm"])),
                            "owner": c["refdes"], "net": q.get("net") or "", "kind": "pad"})
    dies = {d["refdes"] for d in design.get("dies") or []}
    for d in design.get("dies") or []:
        ox, oy = float(d["origin"]["x"]), float(d["origin"]["y"])
        out.append({"rect": (ox, oy, ox + float(d["widthUm"]), oy + float(d["heightUm"])),
                    "owner": d["refdes"], "net": None, "kind": "die"})
    for p in design.get("substratePads") or []:
        pid = str(p.get("id") or "")
        owner = None
        if p.get("role") == "BOND_FINGER" and pid.startswith("F_"):
            ref = pid[2:].rsplit("_", 1)[0]          # F_U1_7 -> U1
            owner = ref if ref in dies else None
        else:
            ref = pid.split("_", 1)[0]               # D1_1~2 -> D1 (die attach)
            owner = ref if ref in dies and "_" in pid else None
        out.append({"rect": _rect((0, 0), float(p["position"]["x"]), float(p["position"]["y"]),
                                  float(p["widthUm"]), float(p["heightUm"])),
                    "owner": owner, "net": p.get("net") or "", "kind": "pad", "pid": pid})
    return out


def _dist_map(G, rect, reach):
    """(window slice, distances of its cell centres to ``rect``) for the
    cells within ``reach`` of it."""
    x0, y0, x1, y1 = rect
    i0 = int(np.searchsorted(G.xs, x0 - reach, "left"))
    i1 = int(np.searchsorted(G.xs, x1 + reach, "right"))
    j0 = int(np.searchsorted(G.ys, y0 - reach, "left"))
    j1 = int(np.searchsorted(G.ys, y1 + reach, "right"))
    if i0 >= i1 or j0 >= j1:
        return None, None
    X = G.X[j0:j1, i0:i1]
    Y = G.Y[j0:j1, i0:i1]
    dx = np.maximum(0.0, np.maximum(x0 - X, X - x1))
    dy = np.maximum(0.0, np.maximum(y0 - Y, Y - y1))
    return (slice(j0, j1), slice(i0, i1)), np.hypot(dx, dy)


def _need(f, w, sp):
    """How far a track's centre line keeps from feature ``f``."""
    if f["kind"] == "body" and f.get("chip"):
        return w / 2.0                                   # ceramic: not copper
    return w / 2.0 + sp


def blockers(design, net, a_tid, b_tid, cell=100.0, soft=20.0) -> Optional[dict]:
    """The parts in the way of connection a_tid -> b_tid of ``net``: the
    soft-halo A* path and {feature index: (intrusion um, (ux, uy) unit
    vector from the path towards the feature, path point)}. None when even
    the soft search finds no way (walled in by parts themselves)."""
    P = SR.ShapeProblem(design)
    G = _Grid(P, float(cell))
    feats = features(design)
    terms = {t.tid: t for t in P.terminals}
    ta, tb = terms.get(a_tid), terms.get(b_tid)
    if ta is None or tb is None:
        return None
    w = P.width_of(net)
    sp = P.min_space
    cost = np.ones((G.ny, G.nx))
    hard = np.zeros((G.ny, G.nx), bool)
    e = P.edge + w / 2.0
    hard |= (G.X < e) | (G.X > P.w - e) | (G.Y < e) | (G.Y > P.h - e)
    own = np.zeros((G.ny, G.nx), bool)
    for t in P.terminals:
        if t.net == net:
            own |= G.cover(t.rect, 0.5)[0]
    for f in feats:
        if f["net"] == net:
            continue
        need = _need(f, w, sp)
        win, d = _dist_map(G, f["rect"], need)
        if win is None:
            continue
        # the track's own copper may not overlap the feature: hard
        hard[win] |= d < w / 2.0 - 1e-6
        inside = d < need - 1e-6
        cost[win] += np.where(inside, soft * (need - d) / max(need, 1.0), 0.0)
    hard &= ~own
    okf = (~hard).ravel().tolist()
    cf = cost.ravel().tolist()
    nx, ny = G.nx, G.ny
    src = set(np.flatnonzero(G.cover(ta.rect, 0.5)[0].ravel()).tolist()) or {G.idx(*ta.centre)}
    tgt = set(np.flatnonzero(G.cover(tb.rect, 0.5)[0].ravel()).tolist()) or {G.idx(*tb.centre)}
    bend = 2.0 * P.min_line
    g, prev, pq = {}, {}, []
    for k in src:
        s0 = (k, 4)
        g[s0] = 0.0
        prev[s0] = None
        heapq.heappush(pq, (0.0, s0))
    found = None
    while pq:
        gs, s0 = heapq.heappop(pq)
        if gs > g.get(s0, 1e300):
            continue
        k, d0 = s0
        if k in tgt:
            found = s0
            break
        i, j = k % nx, k // nx
        for di, (dx, dy) in enumerate(DIRS):
            ii, jj = i + dx, j + dy
            if ii < 0 or jj < 0 or ii >= nx or jj >= ny:
                continue
            kk = jj * nx + ii
            if not okf[kk]:
                continue
            ng = gs + cell * cf[kk] + (bend if d0 != 4 and d0 != di else 0.0)
            ns = (kk, di)
            if ng < g.get(ns, 1e300):
                g[ns] = ng
                prev[ns] = s0
                heapq.heappush(pq, (ng, ns))
    if found is None:
        return None
    path = []
    s0 = found
    while s0 is not None:
        path.append(G.xy(s0[0]))
        s0 = prev[s0]
    path.reverse()
    hits = {}
    for (x, y) in path:
        for fi, f in enumerate(feats):
            if f["net"] == net:
                continue
            r = f["rect"]
            need = _need(f, w, sp)
            nxp = min(max(x, r[0]), r[2])
            nyp = min(max(y, r[1]), r[3])
            d = math.hypot(nxp - x, nyp - y)
            if d >= need - 1e-6 or d <= 0:
                continue
            intr = need - d
            if fi not in hits or intr > hits[fi][0]:
                hits[fi] = (intr, ((nxp - x) / d, (nyp - y) / d), (x, y))
    return {"path": path, "hits": hits, "feats": feats, "P": P}


def _seg_cross(p, q, a, b):
    """Do the segments p-q and a-b cross (or touch)?"""
    def o(u, v, w):
        return (v[0] - u[0]) * (w[1] - u[1]) - (v[1] - u[1]) * (w[0] - u[0])
    d1, d2, d3, d4 = o(a, b, p), o(a, b, q), o(p, q, a), o(p, q, b)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    return False


def bottleneck(path, feats, net, w, sp, traces, reach=4000.0, step=3):
    """The worst channel along ``path`` (a clean, part-only route): at every
    ``step``-th point the cut across the path (perpendicular, 90-degree), the
    nearest part feature on each side, the other nets' 1st-conductor tracks
    already through it and the shortfall = what k + 1 tracks need minus the
    width there. Returns {"short", "left", "right" (feature indices),
    "axis" ("x" | "y": the cut's direction), "at", "tracks"} or None."""
    best = None
    n = len(path)
    for i in range(1, n - 1, step):
        (x0, y0), (x1, y1) = path[i - 1], path[i + 1]
        x, y = path[i]
        horiz = abs(x1 - x0) >= abs(y1 - y0)       # path runs along x: cut is along y
        lo = hi = None                             # (gap, fi) below/left and above/right
        for fi, f in enumerate(feats):
            if f["net"] == net:
                continue
            r = f["rect"]
            if horiz:
                if not (r[0] < x < r[2]):
                    continue
                if r[3] <= y:
                    g = y - r[3]
                    if g < reach and (lo is None or g < lo[0]):
                        lo = (g, fi)
                elif r[1] >= y:
                    g = r[1] - y
                    if g < reach and (hi is None or g < hi[0]):
                        hi = (g, fi)
            else:
                if not (r[1] < y < r[3]):
                    continue
                if r[2] <= x:
                    g = x - r[2]
                    if g < reach and (lo is None or g < lo[0]):
                        lo = (g, fi)
                elif r[0] >= x:
                    g = r[0] - x
                    if g < reach and (hi is None or g < hi[0]):
                        hi = (g, fi)
        if lo is None or hi is None:
            continue
        width = lo[0] + hi[0]
        if horiz:
            a, b = (x, y - lo[0]), (x, y + hi[0])
        else:
            a, b = (x - lo[0], y), (x + hi[0], y)
        k = 0
        for n2, w2, pts in traces:
            if n2 == net:
                continue
            for p, q in zip(pts, pts[1:]):
                if _seg_cross(p, q, a, b):
                    k += 1
        side = lambda f: 0.0 if (f["kind"] == "body" and f.get("chip")) else sp
        need = (k + 1) * w + k * sp + side(feats[lo[1]]) + side(feats[hi[1]])
        short = need - width
        if short > 0 and (best is None or short > best["short"]):
            best = {"short": short, "left": lo[1], "right": hi[1], "axis": "y" if horiz else "x",
                    "at": (x, y), "tracks": k}
    return best


def _owner_nets(feats, owner):
    return {f["net"] for f in feats if f["owner"] == owner and f["net"]}


def _shares_net(feats, a, b, net):
    """Do parts ``a`` and ``b`` share a net (other than ``net``)? Opening a
    channel between them for ``net`` would cut that link: it would have to
    cross the new track."""
    if a is None or b is None or a == b:
        return False
    return bool((_owner_nets(feats, a) & _owner_nets(feats, b)) - {net})


def _owner_rects(feats, owner):
    return [f for f in feats if f["owner"] == owner]


def _move_ok(feats, owner, dx, dy, moved, P, sp):
    """The part ``owner`` moved by (dx, dy) stays on the substrate and keeps
    its copper ``sp`` from every other part's copper, its body off everything
    (other parts already moved by ``moved`` {owner: (dx, dy)})."""
    mine = _owner_rects(feats, owner)
    if not mine:
        return False

    def shifted(f, o):
        ddx, ddy = (dx, dy) if o == owner else moved.get(o, (0.0, 0.0))
        r = f["rect"]
        return (r[0] + ddx, r[1] + ddy, r[2] + ddx, r[3] + ddy)
    e = P.edge
    for f in mine:
        r = shifted(f, owner)
        if r[0] < e or r[1] < e or r[2] > P.w - e or r[3] > P.h - e:
            return False
    for f in mine:
        r = shifted(f, owner)
        for g_ in feats:
            if g_["owner"] == owner:
                continue
            q = shifted(g_, g_["owner"])
            copper = f["net"] is not None and g_["net"] is not None
            if copper and f["net"] and f["net"] == g_["net"]:
                continue                                  # same net may touch
            ox = min(r[2], q[2]) - max(r[0], q[0])
            oy = min(r[3], q[3]) - max(r[1], q[1])
            if ox > 0 and oy > 0:
                return False                              # overlap: never
            if copper:
                gx = max(q[0] - r[2], r[0] - q[2], 0.0)
                gy = max(q[1] - r[3], r[1] - q[3], 0.0)
                if math.hypot(gx, gy) < sp - 1e-6:
                    return False                          # copper spacing
    return True


def plan(design, unrouted, locked=(), max_move: float = 500.0, margin: float = 50.0,
         snap: float = 10.0, traces=(), say=None) -> dict:
    """Moves that let the ``unrouted`` connections ({"net", "from", "to"})
    through: parts in the way of each one's clean path, and -- the usual case
    once the board is routed -- the parts either side of the worst channel
    on that path, where the other nets' ``traces`` [(net, w, [(x, y)])] leave
    no room for one more track. Returns {"moves": {ref: (dx, dy)}, "why": [text], "stuck":
    [connections no move helps]}."""
    say = say or (lambda _m: None)
    locked = set(locked or ())
    want: Dict[str, List[float]] = {}            # ref -> [+x, -x, +y, -y] needs
    why, stuck = [], []
    P = None
    feats = None
    for u in unrouted:
        net, a, b = u.get("net"), u.get("from"), u.get("to")
        if not net or not a or not b or a == "?" or b == "?":
            continue
        r = blockers(design, net, a, b)
        if r is None:
            stuck.append(u)
            continue
        P, feats = r["P"], r["feats"]
        movable = lambda f: f["owner"] is not None and f["owner"] not in locked
        hits = dict(r["hits"])
        # the worst channel on the way, once the other nets' tracks are in it
        bn = bottleneck(r["path"], feats, net, P.width_of(net), P.min_space, traces)
        if bn is not None and _shares_net(feats, feats[bn["left"]]["owner"],
                                          feats[bn["right"]]["owner"], net):
            why.append(f"{net} {a}-{b}: the channel at ({bn['at'][0]:.0f}, {bn['at'][1]:.0f}) "
                       f"runs between {feats[bn['left']]['owner']} and "
                       f"{feats[bn['right']]['owner']}, which are connected -- not opened")
            bn = None
        if bn is not None:
            x, y = bn["at"]
            for fi, sgn in ((bn["left"], -1.0), (bn["right"], 1.0)):
                share = bn["short"] / 2.0
                other = bn["right"] if fi == bn["left"] else bn["left"]
                if not movable(feats[other]):
                    share = bn["short"]             # the far side can't give way
                if not movable(feats[fi]):
                    continue
                v = (0.0, sgn) if bn["axis"] == "y" else (sgn, 0.0)
                if fi not in hits or hits[fi][0] < share:
                    hits[fi] = (share, v, (x, y))
            why.append(f"{net} {a}-{b}: channel at ({x:.0f}, {y:.0f}) short by "
                       f"{bn['short']:.0f} um with {bn['tracks']} track(s) in it")
        # never open a channel between two connected parts (see _shares_net)
        drop = set()
        hl = list(hits.items())
        for i1, (f1, (_i, v1, _a)) in enumerate(hl):
            for f2, (_i2, v2, _a2) in hl[i1 + 1:]:
                if v1[0] * v2[0] + v1[1] * v2[1] < -0.3 and \
                        _shares_net(feats, feats[f1]["owner"], feats[f2]["owner"], net):
                    drop |= {f1, f2}
        if drop:
            why.append(f"{net} {a}-{b}: way between connected parts "
                       + ", ".join(sorted({str(feats[f]["owner"]) for f in drop})) + " -- not opened")
            for f in drop:
                hits.pop(f, None)
        if not hits:
            stuck.append(u)
            continue
        need_here = {}
        fixed_short = []
        for fi, (intr, (ux, uy), at) in hits.items():
            f = feats[fi]
            if movable(f):
                need_here.setdefault(f["owner"], []).append((intr, ux, uy, at))
            else:
                fixed_short.append((intr, ux, uy, at))
        # a blocker that can't move: the movable one across the channel (the
        # nearest hit on the other side) takes its share too
        for intr, ux, uy, at in fixed_short:
            best, bd = None, 1e300
            for owner, lst in need_here.items():
                for (i2, vx, vy, at2) in lst:
                    if vx * ux + vy * uy < -0.3:           # the other side
                        dd = math.hypot(at2[0] - at[0], at2[1] - at[1])
                        if dd < bd:
                            best, bd = (owner, vx, vy, at2), dd
            if best is None or bd > 1500:
                continue
            need_here[best[0]].append((intr, best[1], best[2], best[3]))
        if not need_here:
            stuck.append(u)
            continue
        for owner, lst in need_here.items():
            # the intrusions of one part sum if they come from both of its
            # neighbours across the same channel, else take the largest per way
            agg = want.setdefault(owner, [0.0, 0.0, 0.0, 0.0])
            tot = {}
            for intr, ux, uy, _at in lst:
                if abs(ux) >= abs(uy):
                    k, comp = (0 if ux > 0 else 1), abs(ux)
                else:
                    k, comp = (2 if uy > 0 else 3), abs(uy)
                tot[k] = max(tot.get(k, 0.0), intr / max(comp, 0.5))
            for k, v in tot.items():
                agg[k] = max(agg[k], v)
        why.append(f"{net} {a}-{b}: " + ", ".join(sorted(need_here)))
    moves = {}
    if P is None:
        return {"moves": moves, "why": why, "stuck": stuck}
    sp = P.min_space
    for owner, (px, nxm, py, nym) in sorted(want.items(), key=lambda kv: -max(kv[1])):
        dx = (px - nxm)
        dy = (py - nym)

        def up(v):
            if abs(v) < 1e-9:
                return 0.0
            v = math.copysign(abs(v) + margin, v)
            return math.copysign(math.ceil(abs(v) / snap) * snap, v)
        dx, dy = up(dx), up(dy)
        if not dx and not dy:
            continue
        if math.hypot(dx, dy) > max_move:
            why.append(f"{owner}: needs {math.hypot(dx, dy):.0f} um, more than {max_move:.0f}")
            continue
        if _move_ok(feats, owner, dx, dy, moves, P, sp):
            moves[owner] = (dx, dy)
        else:
            # one axis at a time
            done = False
            for ddx, ddy in ((dx, 0.0), (0.0, dy)):
                if (ddx or ddy) and _move_ok(feats, owner, ddx, ddy, moves, P, sp):
                    moves[owner] = (ddx, ddy)
                    done = True
                    break
            if not done:
                why.append(f"{owner}: no room to move {dx:+.0f}, {dy:+.0f} um")
    return {"moves": moves, "why": why, "stuck": stuck}


def apply(session, cell_name: str, moves: Dict[str, tuple]) -> dict:
    """Move the instances ``moves`` {ref: (dx, dy)} um; a die takes its wire
    bonds and bond fingers with it. Call inside the caller's undo step.
    Returns {"moved": [refs], "bonds"}."""
    import types
    from .autoplace_session import _xf
    from .fanout import carry_bonds
    lib = session.library
    cell = lib.get_cell(cell_name)
    by = {r.label: r for r in cell.srefs if r.label}
    carried = {}
    out = []
    for ref, (dx, dy) in moves.items():
        r = by.get(ref)
        if r is None or (not dx and not dy):
            continue
        old = types.SimpleNamespace(origin=tuple(r.origin), rotation=r.rotation,
                                    x_reflection=r.x_reflection, magnification=r.magnification)
        r.origin = (int(round(r.origin[0] + dx)), int(round(r.origin[1] + dy)))
        carried[ref] = (_xf(old), _xf(r), r.cell_name)
        out.append(ref)
    res = carry_bonds(session, cell_name, carried) if carried else {"bonds": 0}
    return {"moved": out, "bonds": res.get("bonds", 0)}
