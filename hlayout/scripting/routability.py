# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Routability check: will this placement route? -- in seconds, no routing.

Uses the router's own view of the board (PathFinder's per-net maps of where
a track, a crossover strip and a crossover contact may go) and reports:

  * walled-in connections -- a net's pads that no path joins even with the
    board otherwise empty (a track can't get out between the parts round
    it, and there is no room for a crossover either): the router can never
    route these, whatever it tries;
  * channel overflow -- RUDY (rectangular uniform wire density): each net's
    straight-line length spread over its bounding box, against the tracks
    the free space in each tile can hold; the hot spots are where parts
    need spreading;
  * crossings with no room for a crossover -- an SLI crossing of two nets
    where neither net has a crossover contact site near it.

``check(session)`` -> {"connections", "walled", "crossings", "no_xo",
"hot", "score", "sections"} -- ``sections`` in the Auto Place report's form
(see autoplace_report), so the Auto Place window can list them. Run it
after Fan Out Die Bonds and Bond Header (dies with no bonds can't be
judged).
"""
import math
import re

import numpy as np

from ..core.autoplace import _cross_pt, mst

ERR, WARN, INFO = "error", "warning", "info"


def _ref(tid):
    """Part ref of a router terminal id: F_Q1_2 -> Q1, Q17_3~1 -> Q17,
    R4.A -> R4; I/O pads -> None."""
    t = str(tid)
    if t.startswith("IO"):
        return None
    if t.startswith("F_"):
        t = t[2:]
    m = re.match(r"([A-Za-z]+\d+)", t)
    return m.group(1) if m else None


def _breach(src, dst, ok):
    """Cells of the cheapest path from ``src`` to ``dst`` (bool grids) that
    are not ``ok`` -- a 0-1 BFS, forbidden cells costing 1."""
    from collections import deque
    ny, nx = ok.shape
    okf = ok.ravel()
    INF = 1 << 30
    dist = np.full(ny * nx, INF, np.int64)
    prev = np.full(ny * nx, -1, np.int64)
    dq = deque()
    for k in np.flatnonzero(src.ravel()).tolist():
        dist[k] = 0
        dq.append(k)
    dstf = dst.ravel()
    hit = -1
    while dq:
        k = dq.popleft()
        if dstf[k]:
            hit = k
            break
        x, y = k % nx, k // nx
        for nk in ((k - 1) if x > 0 else -1, (k + 1) if x < nx - 1 else -1,
                   (k - nx) if y > 0 else -1, (k + nx) if y < ny - 1 else -1):
            if nk < 0:
                continue
            w = 0 if okf[nk] else 1
            if dist[k] + w < dist[nk]:
                dist[nk] = dist[k] + w
                prev[nk] = k
                (dq.appendleft if w == 0 else dq.append)(nk)
    out = np.zeros(ny * nx, bool)
    k = hit
    while k >= 0:
        if not okf[k]:
            out[k] = True
        k = prev[k]
    return out.reshape(ny, nx)


class _DSU:
    def __init__(self):
        self.p = {}

    def find(self, a):
        p = self.p
        p.setdefault(a, a)
        while p[a] != a:
            p[a] = p[p[a]]
            a = p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def check(session, cell_name=None, cell: float = 100.0, tile: float = 1000.0,
          xo_radius: float = 1500.0) -> dict:
    from scipy import ndimage

    from . import pathfinder as PF
    from .hybridroute import export_design, layers_for
    name = cell_name or session.current_cell_name
    layers = layers_for(session)
    design, mapping = export_design(session, name, layers=layers, keep_existing=False)
    full = bool(mapping.get("full_dielectric"))
    M = PF.route(design, cell=float(cell), _maps_only=True,
                 local_dielectric=full or mapping.get("local_diel", -1) >= 0,
                 cond2_keepouts=None if full else mapping.get("local_keepouts"),
                 full_dielectric=full)
    G, P, allow, width, sp = M["G"], M["P"], M["allow"], M["width"], M["sp"]
    ox, oy = mapping.get("origin") or (0.0, 0.0)
    W = lambda x, y: (x + ox, y + oy)                       # router -> design coords
    shape = (G.ny, G.nx)

    def wbox(rects, pad=0.0):
        rs = [r for r in rects if r]
        if not rs:
            return None
        x0, y0 = W(min(r[0] for r in rs) - pad, min(r[1] for r in rs) - pad)
        x1, y1 = W(max(r[2] for r in rs) + pad, max(r[3] for r in rs) + pad)
        return (x0, y0, x1, y1)

    # ---- 1. walled in: per net, the pads no path joins on an empty board
    walled, n_conn = [], 0
    term_cells = {}
    for n in sorted(P.groups):
        groups = P.groups[n]
        if len(groups) < 2:
            continue
        n_conn += len(groups) - 1
        a1, a2, av = (np.asarray(v, bool).reshape(shape) for v in allow[n])
        l1, _k1 = ndimage.label(a1)
        l2, _k2 = ndimage.label(a2) if M["two_cond"] else (np.zeros(shape, int), 0)
        d = _DSU()
        if M["two_cond"]:
            ys, xs = np.nonzero(av & (l1 > 0) & (l2 > 0))
            for c1, c2 in set(zip(l1[ys, xs].tolist(), l2[ys, xs].tolist())):
                d.union(("1", c1), ("2", c2))
        roots = []
        for gr in groups:
            r = None
            for t in gr:
                k = G.idx(*t.centre)
                c = l1.ravel()[k]
                if c > 0:
                    r = d.find(("1", int(c)))
                    break
            roots.append(r)
        isl = {}
        for gi, r in enumerate(roots):
            isl.setdefault(r if r is not None else ("x", gi), []).append(gi)
        if len(isl) < 2:
            continue
        main = max(isl.values(), key=len)
        for key, gis in isl.items():
            if gis is main:
                continue
            terms = [t for gi in gis for t in groups[gi]]
            # who walls it in: other nets' pads at the edge of its island
            if key[0] == "x":
                mask = np.zeros(shape, bool)
            else:
                mask = np.zeros(shape, bool)
                for gi in gis:
                    for t in groups[gi]:
                        c = l1.ravel()[G.idx(*t.centre)]
                        if c > 0:
                            mask |= l1 == c
            # the cheapest breach: from the island to the rest of the net through
            # the fewest forbidden cells -- those cells are the wall to open
            rest = np.zeros(shape, bool)
            for key2, gis2 in isl.items():
                if gis2 is gis:
                    continue
                for gi in gis2:
                    for t in groups[gi]:
                        c = l1.ravel()[G.idx(*t.centre)]
                        if c > 0:
                            rest |= l1 == c
                        else:
                            rest |= G.cover(t.rect, cell)[0]
            src = mask if mask.any() else np.logical_or.reduce(
                [G.cover(t.rect, cell)[0] for t in terms])
            wall = _breach(src, rest, a1)
            near = set()
            for t in P.terminals:
                if t.net == n or not wall.any():
                    continue
                if (G.cover(t.rect, width[n] / 2.0 + sp + cell)[0] & wall).any():
                    r_ = _ref(t.tid)
                    if r_:
                        near.add(r_)
            short = int(round(wall.sum() * cell)) if wall.any() else 0
            own = sorted({_ref(t.tid) for t in terms if _ref(t.tid)})
            if wall.any():
                wj, wi = np.nonzero(wall)
                wxy = W(float(G.X[wj, wi].mean()), float(G.Y[wj, wi].mean()))
            else:
                wxy = None
            walled.append({
                "net": n, "own": own, "blockers": sorted(near - set(own)), "wall": wxy,
                "short": short,
                "sev": ERR,
                "item": f"{n}: {', '.join(t.tid for t in terms[:3])} walled in",
                "detail": ("no track can get out and no crossover fits -- the router can never "
                           "join it to the rest of the net"
                           + (f"; the nearest way out is blocked by {', '.join(sorted(near - set(own)))}"
                              f" (about {short} um of wall) -- move them apart: a track needs "
                              f"{3 * width[n]:g} um (3 track widths) between their copper" if near else
                              " -- spread the parts round it")),
                "refs": own + sorted(near - set(own)),
                "box": (wbox([(G.X[jj, ii] - cell, G.Y[jj, ii] - cell, G.X[jj, ii] + cell,
                               G.Y[jj, ii] + cell) for jj, ii in zip(*np.nonzero(wall))]
                             + [t.rect for t in terms], 800) if wall.any()
                        else wbox([t.rect for t in terms], 1500)), "fix": None})

    # ---- 2. channel overflow (RUDY) on ``tile`` tiles
    pitch = max(width.values()) + sp if width else 600.0
    nx, ny = max(1, int(math.ceil(P.w / tile))), max(1, int(math.ceil(P.h / tile)))
    hard, padnet = M["maps1"][max(M["maps1"])]
    free = (~hard) & (padnet == -1)                       # room for any net's track
    fy, fx = np.nonzero(free)
    cap = np.zeros((ny, nx))
    cx = np.minimum((G.X[fy, fx] // tile).astype(int), nx - 1)
    cy = np.minimum((G.Y[fy, fx] // tile).astype(int), ny - 1)
    np.add.at(cap, (cy, cx), cell * cell)
    cap = cap / (tile * pitch)                            # tracks a tile can take
    dem = np.zeros((ny, nx))
    segs = {}
    for n in sorted(P.groups):
        pts = [t.centre for gr in P.groups[n] for t in gr[:1]]
        if len(pts) < 2:
            continue
        ln, edges = mst(pts)
        segs[n] = [(pts[i], pts[j]) for i, j in edges]
        x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
        y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        x0, x1 = x0 - pitch / 2, x1 + pitch / 2
        y0, y1 = y0 - pitch / 2, y1 + pitch / 2
        dens = ln / ((x1 - x0) * (y1 - y0))               # track length per um^2
        for j in range(max(0, int(y0 // tile)), min(ny, int(y1 // tile) + 1)):
            for i in range(max(0, int(x0 // tile)), min(nx, int(x1 // tile) + 1)):
                ov = max(0.0, min(x1, (i + 1) * tile) - max(x0, i * tile)) * \
                    max(0.0, min(y1, (j + 1) * tile) - max(y0, j * tile))
                dem[j, i] += dens * ov / tile             # tracks through the tile
    over = dem - cap
    util = float(dem.sum() / max(cap.sum(), 1e-9))
    hot = []
    for j, i in zip(*np.unravel_index(np.argsort(-over, axis=None), over.shape)):
        if over[j, i] <= 1.0 or len(hot) >= 8:
            break
        bx = (i * tile - tile / 2, j * tile - tile / 2, (i + 1.5) * tile, (j + 1.5) * tile)
        refs = sorted({_ref(t.tid) for t in P.terminals if _ref(t.tid)
                       and bx[0] <= t.centre[0] <= bx[2] and bx[1] <= t.centre[1] <= bx[3]})
        hot.append((float(over[j, i]), float(dem[j, i]), float(cap[j, i]), bx, refs))
    hot_rows = [{"sev": WARN,
                 "item": f"Congested at {W(bx[0], bx[1])[0] / 1000 + tile / 1000:.1f}, "
                         f"{W(bx[0], bx[1])[1] / 1000 + tile / 1000:.1f} mm",
                 "detail": f"about {d_:.1f} tracks want through a {tile / 1000:g} mm square that "
                           f"holds {c_:.1f} -- spread {', '.join(refs[:8]) or 'the parts here'}",
                 "refs": refs, "box": wbox([bx], 0), "fix": None}
                for ov, d_, c_, bx, refs in hot]
    hot_rows.insert(0, {"sev": WARN if util > 0.7 else INFO, "item": "Track demand",
                        "detail": f"the nets need about {100 * util:.0f}% of the room the free space "
                                  "holds (RUDY estimate)"
                                  + (" -- over 70%: expect unrouted nets; more space between parts"
                                     if util > 0.7 else ""),
                        "refs": [], "box": None, "fix": None})

    # ---- 3. SLI crossings with no room for a crossover
    xo_rows, n_x = [], 0
    rr = int(math.ceil(xo_radius / cell))
    names = sorted(segs)
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            for p, q in segs[names[a]]:
                for r_, t_ in segs[names[b]]:
                    c = _cross_pt(p, q, r_, t_)
                    if c is None:
                        continue
                    n_x += 1
                    i0, j0 = int(c[0] // cell), int(c[1] // cell)
                    sl = (slice(max(0, j0 - rr), j0 + rr + 1), slice(max(0, i0 - rr), i0 + rr + 1))
                    ok = False
                    if M["two_cond"]:
                        for n in (names[a], names[b]):
                            av = np.asarray(allow[n][2], bool).reshape(shape)
                            if av[sl].sum() >= 2:
                                ok = True
                                break
                    if not ok:
                        ends = sorted({_ref(t.tid) for n in (names[a], names[b])
                                       for gr in P.groups[n] for t in gr
                                       if _ref(t.tid) and (math.dist(t.centre, p) < 1 or
                                                           math.dist(t.centre, q) < 1 or
                                                           math.dist(t.centre, r_) < 1 or
                                                           math.dist(t.centre, t_) < 1)})
                        xo_rows.append({
                            "sev": ERR, "item": f"{names[a]} x {names[b]}",
                            "detail": "the lines cross where no crossover fits (pads, parts or "
                                      "resistor keep-outs all round) -- swap or turn "
                                      + (", ".join(ends) if ends else "the parts") +
                                      " to uncross them, or move them apart",
                            "refs": ends, "box": wbox([(c[0], c[1], c[0], c[1])], xo_radius),
                            "fix": None})
    nw = len(walled)
    score = max(0.0, 1.0 - (nw + len(xo_rows)) / max(1, n_conn))
    head = (f"Routability: {n_conn - nw}/{n_conn} connections reachable, "
            f"{n_x} SLI crossing(s), {len(xo_rows)} with no room for a crossover, "
            f"track demand {100 * util:.0f}% of capacity")
    unb = mapping.get("unbonded") or []
    notes = []
    if unb:
        notes.append({"sev": WARN, "item": f"{len(unb)} die pad(s) not bonded",
                      "detail": "run Fan Out Die Bonds first -- unbonded pads are left out: "
                                + ", ".join(unb[:10]), "refs": sorted({u.split('.')[0] for u in unb}),
                      "box": None, "fix": None})
    sections = [{"title": f"Routability -- {100 * score:.0f}% ({head[13:]})",
                 "rows": notes + walled + xo_rows + hot_rows}]
    return {"walled_rows": walled, "connections": n_conn, "walled": nw, "crossings": n_x, "no_xo": len(xo_rows),
            "hot": len(hot), "util": util, "score": score, "headline": head, "sections": sections,
            "errors": nw + len(xo_rows), "warnings": len(hot) + len(notes)}


def to_text(rep) -> str:
    lines = [rep["headline"], ""]
    for sec in rep["sections"]:
        lines.append(sec["title"])
        for r in sec["rows"] or []:
            mark = {"error": "!!", "warning": " !", "info": "  "}[r["sev"]]
            lines.append(f"{mark} {r['item']}: {r['detail']}")
        if not sec["rows"]:
            lines.append("  (no problems)")
    return "\n".join(lines)
