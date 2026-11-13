# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""PathFinder: a negotiated-congestion router for the chip-and-wire hybrid.

Completion first. A sequential router (route a net, then the next) lets the
early nets take whatever they like and wall the later ones in; patching
that with rip-up only goes so far. PathFinder (McMurchie & Ebeling, the
algorithm FPGA routers use) routes EVERY net EVERY round and lets nets
share space at first, at a price:

  * present cost -- grows each round, for space another net is using now;
  * history cost -- builds up wherever nets keep fighting.

Nets with somewhere else to go move off contested space; the net that
needs it keeps it. Rounds repeat until nothing overlaps (or the round /
time cap is hit: then the overlapping connections are reported unrouted,
never drawn as shorts).

The board is a grid (``cell`` um, default 100) on both conductors, plus a
grid line through every pad centre so tracks meet pads on their axis. The
obstacles are the shape router's (ShapeProblem): other nets' pads, part
bodies, resistor ink keep-outs, the edge; inside its own pad a track may
run anywhere -- but not turn, so it leaves a pin straight out along its
centre line, through the edge away from the part body. Each net's copper
claims a clearance halo -- (width + spacing) / 2 round its centre line on
COND1, (strip width + spacing) / 2 on COND2 (the local dielectric strip,
the bridge width + diel_side each side) -- and two nets overlap when their halos do.

COND2 is the printed crossover: costlier per mm than COND1 (so it is used
to cross, not to travel), reached by a via whose land is kept off every
pad; its runs become local-dielectric crossovers. The result is the same
dict the other engines return, so apply_routed draws it unchanged.
"""
import heapq
import math
import time
from typing import Dict, List, Optional

import numpy as np

try:
    from . import shape_router as SR
except ImportError:                     # run from a scratch folder
    import shape_router as SR

DEBUG = set()
#: negotiation: starting present-congestion factor, its growth per round, and
#: the history added per round for each extra net on a cell
PF0, PF_GROW, HIST_INC = 2.0, 1.8, 0.5
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
DIAG = ((1, 1), (1, -1), (-1, 1), (-1, -1))


class _Grid:
    """Cell centres xs (nx), ys (ny) on a uniform pitch (the clearance halos
    are disks in cells, so the pitch must be even); tracks are lined up on
    their pads' centres afterwards (_emit)."""

    def __init__(self, P, cell):
        e = P.edge
        self.xs = np.arange(e + cell / 2.0, P.w - e, cell)
        self.ys = np.arange(e + cell / 2.0, P.h - e, cell)
        self.nx, self.ny = len(self.xs), len(self.ys)
        self.n = self.nx * self.ny
        self.X, self.Y = np.meshgrid(self.xs, self.ys)        # (ny, nx)

    def idx(self, x, y):
        i = int(np.argmin(np.abs(self.xs - x)))
        j = int(np.argmin(np.abs(self.ys - y)))
        return j * self.nx + i

    def xy(self, k):
        return float(self.xs[k % self.nx]), float(self.ys[k // self.nx])

    def cover(self, rect, grow):
        """Bool (ny, nx): cell centres within ``grow`` of ``rect`` (exact,
        Euclidean -- rounded corners)."""
        x0, y0, x1, y1 = rect
        i0 = np.searchsorted(self.xs, x0 - grow, "left")
        i1 = np.searchsorted(self.xs, x1 + grow, "right")
        j0 = np.searchsorted(self.ys, y0 - grow, "left")
        j1 = np.searchsorted(self.ys, y1 + grow, "right")
        out = np.zeros((self.ny, self.nx), bool)
        if i0 >= i1 or j0 >= j1:
            return out, (j0, j1, i0, i1)
        X = self.X[j0:j1, i0:i1]
        Y = self.Y[j0:j1, i0:i1]
        dx = np.maximum(0.0, np.maximum(x0 - X, X - x1))
        dy = np.maximum(0.0, np.maximum(y0 - Y, Y - y1))
        out[j0:j1, i0:i1] = dx * dx + dy * dy < grow * grow - 1e-6
        return out, (j0, j1, i0, i1)


def _disk(r_cells):
    """Cells strictly within r_cells: two halos of radii ha, hb share a cell
    only when the centres are closer than about ha + hb -- the clearance."""
    r = max(0, int(math.ceil(r_cells)))
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    m = (xx * xx + yy * yy) < r_cells * r_cells - 1e-9
    m[r, r] = True
    return m


def route(design: dict, cell: float = 100.0, iterations: int = 60,
          on_line=None, cancel=None, local_dielectric: bool = True, cond2_keepouts=None,
          time_budget: Optional[float] = None, via_cost: float = 3000.0,
          cond2_factor: float = 2.5, bend_cost: Optional[float] = None, **_ignored) -> dict:
    """Route ``design`` (the hybrid.json design dict) with PathFinder. See the
    module doc. ``iterations`` caps the negotiation rounds, ``time_budget``
    (s) the run; ``via_cost`` (um of track) is what a crossover's via pair
    is worth, ``cond2_factor`` how much dearer COND2 is per mm. Returns the
    shape router's result dict (traces, crossovers, unrouted, stats)."""
    import copy
    from scipy import ndimage
    say = on_line or (lambda _m: None)
    t_start = time.time()
    design = copy.deepcopy(design)
    if cond2_keepouts is not None:
        design.setdefault("routing", {}).setdefault("keepouts", {})["COND2"] = \
            [list(r) for r in cond2_keepouts]
    P = SR.ShapeProblem(design)
    G = _Grid(P, float(cell))
    ortho = P.orthogonal
    two_cond = local_dielectric                 # COND2 crossovers (local strips) allowed
    sp = P.min_space
    nets = sorted(P.groups)
    net_id = {n: k for k, n in enumerate(nets)}
    width = {n: P.width_of(n) for n in nets}
    # crossover geometry (user-defined): contact square, dielectric margins
    hc_of = {n: P.contact(width[n]) / 2.0 for n in nets}
    hc_max = max(hc_of.values()) if hc_of else P.min_line / 2.0
    xc_of = {n: P.xclr(n) for n in nets}              # contact centre -> foreign COND1
    xc_max = max(xc_of.values()) if xc_of else hc_max + sp
    if bend_cost is None:
        bend_cost = 2.0 * P.min_line
    say(f"PathFinder: {G.nx} x {G.ny} grid ({cell:g} um), {len(nets)} nets, "
        f"{sum(len(P.groups[n]) - 1 for n in nets)} connections")

    # ---- what is where -------------------------------------------------------------
    # pad interiors: which net's pad a cell centre is in (-2: more than one)
    pad_in = np.full((G.ny, G.nx), -1, np.int32)
    for t in P.terminals:
        if t.net not in net_id:
            continue
        m, _b = G.cover(t.rect, 0.5)
        k = net_id[t.net]
        cur = pad_in[m]
        pad_in[m] = np.where((cur == -1) | (cur == k), k, -2)
    cond1 = P.layers[0]
    fixed1 = list(P.space.layer_blockers(cond1))
    fixed2 = list(P.space.layer_blockers("COND2"))
    part_rects = list(P.parts)
    res_rects = list(P.resistors)

    def forbid_map(blockers, grow_of, edge_hw):
        """(hard, padnet): ``hard`` -- cells no net may use (net-less
        blockers, the edge); ``padnet`` -- the net of the netted blocker a
        cell is too near (-1 none, -2 several nets)."""
        hard = np.zeros((G.ny, G.nx), bool)
        padnet = np.full((G.ny, G.nx), -1, np.int32)
        for n, r in blockers:
            g = grow_of(n)
            if n and n in net_id:
                # a pad narrower than its own net's track: that track sticks out
                # of it, so other nets keep clear of the track, not just the pad
                g += max(0.0, (width[n] - min(r[2] - r[0], r[3] - r[1])) / 2.0)
            m, _b = G.cover(r, g)
            if not n or n not in net_id:
                hard |= m
            else:
                k = net_id[n]
                cur = padnet[m]
                padnet[m] = np.where((cur == -1) | (cur == k), k, -2)
        e = P.edge + edge_hw
        hard |= (G.X < e) | (G.X > P.w - e) | (G.Y < e) | (G.Y > P.h - e)
        return hard, padnet

    def allowed_for(k, hard, padnet):
        own = pad_in == k
        return own | (~hard & ((padnet == -1) | (padnet == k)))

    # COND1: every margin in use; the corner bonus keeps a straight step between
    # two allowed cells clear too (distance to a box is convex along the step)
    margins = sorted({P.margin(n) for n in nets})
    maps1 = {}
    for m in margins:
        mg = math.sqrt(m * m + (cell / 2.0) ** 2)
        maps1[m] = forbid_map(fixed1, lambda n, mg=mg: mg, m - sp)
    # COND2 (the strip): off parts, resistor ink (+ clearance), keep-outs, other
    # nets' COND2, and every pad
    maps2 = {}
    if two_cond:
        for w in sorted(set(width.values())):
            dhw = P.strip_w(w) / 2.0
            hc = P.contact(w) / 2.0
            hard = np.zeros((G.ny, G.nx), bool)
            for r in part_rects:
                hard |= G.cover(r, dhw)[0]
            for r in res_rects:
                hard |= G.cover(r, dhw + P.res_clear)[0]
            for n, r in fixed2:
                hard |= G.cover(r, dhw + (sp if n else 0.0))[0]
            e = P.edge + dhw
            hard |= (G.X < e) | (G.X > P.w - e) | (G.Y < e) | (G.Y > P.h - e)
            # pads: a strip may not cover any; a via land keeps clear of its own
            padnet = np.full((G.ny, G.nx), -1, np.int32)
            for t in P.terminals:
                k = net_id.get(t.net, -2)
                m = G.cover(t.rect, dhw + sp)[0]
                cur = padnet[m]
                padnet[m] = np.where((cur == -1) | (cur == k), k, -2)
                hard |= G.cover(t.rect, hc + sp)[0]
            maps2[w] = (hard, padnet)
    # via sites: the contact, its bare gap and the dielectric's margin clear of
    # foreign copper/pads; the contact itself not on any pad
    vmap = {}
    if two_cond:
        lm = xc_max
        land_hard, land_pad = forbid_map(fixed1, lambda n: math.sqrt(lm * lm + (cell / 2) ** 2),
                                         hc_max)
        anypad = np.zeros((G.ny, G.nx), bool)
        for t in P.terminals:
            anypad |= G.cover(t.rect, hc_max + sp)[0]
        vmap = (land_hard | anypad, land_pad)

    # per net: allowed cells on each layer (flattened python lists, fast lookups)
    allow = {}
    for n in nets:
        k = net_id[n]
        a1 = allowed_for(k, *maps1[P.margin(n)])
        if two_cond:
            h2, p2 = maps2[width[n]]
            a2 = ~h2 & ((p2 == -1) | (p2 == k))
            av = ~vmap[0] & ((vmap[1] == -1) | (vmap[1] == k)) & a1 & a2
        else:
            a2 = np.zeros_like(a1)
            av = a2
        allow[n] = (a1.ravel().tolist(), a2.ravel().tolist(), av.ravel().tolist())
    padin = pad_in.ravel().tolist()

    # clearance between nets: two nets' centre lines must be at least ``need``
    # apart -- half of each one's copper plus the spacing (a via land counts
    # as copper of its size). Checked exactly at every grid point with a
    # distance transform to the nearest other net's centre line.
    wmax = max(width.values()) if width else P.min_line

    def need(n, L):
        if L == 0:
            return (max(width[n], 2 * hc_of[n]) + max(wmax, 2 * hc_max)) / 2.0 + sp
        return (P.strip_w(width[n]) + P.strip_w(wmax)) / 2.0 + sp

    # a crossover's contact: around it the bridge is bare COND2 (the gap to the
    # dielectric's end), so other nets' COND1 keeps xclr from its centre
    def via_mask(n, cells):
        m0 = np.zeros(G.n, bool)
        m1 = np.zeros(G.n, bool)
        for (l, c) in cells:
            (m1 if l else m0)[c] = True
        return (m0 & m1).reshape(G.ny, G.nx)

    def claim(n, cells, L):
        """The cells of ``n`` on layer L whose copper counts against others:
        inside its own pad its copper sits on the pad (an obstacle already)."""
        k = net_id[n]
        return [c for (l, c) in cells if l == L and (L == 1 or padin[c] != k)]

    occ = [np.zeros((G.ny, G.nx), np.int16), np.zeros((G.ny, G.nx), np.int16)]
    hist = [np.zeros((G.ny, G.nx), np.float32), np.zeros((G.ny, G.nx), np.float32)]
    routes: Dict[str, dict] = {}     # net -> {"cells": [(layer, k)], "conns": [...], "fail": [...]}
    own = {}                         # (net, layer) -> its centre-line cells (bool)

    def cells_mask(n, cells, L):
        m = np.zeros(G.n, bool)
        c = claim(n, cells, L)
        if c:
            m[c] = True
        return m.reshape(G.ny, G.nx)

    occv = np.zeros((G.ny, G.nx), np.int16)       # crossover contacts, all nets

    def mark(n, sign):
        r = routes.get(n)
        if not r:
            return
        for L in (0, 1):
            m = cells_mask(n, r["cells"], L)
            occ[L] += sign * m.astype(np.int16)
            own[(n, L)] = m if sign > 0 else np.zeros_like(m)
        v = via_mask(n, r["cells"])
        occv[...] += sign * v.astype(np.int16)
        own[(n, "v")] = v if sign > 0 else np.zeros_like(v)

    def _others(n, key, arr, like):
        return (arr - own.get((n, key), np.zeros_like(like)).astype(np.int16)) > 0

    def too_near(n, L, mask, occ_L, vmask=None, occ_v=None, occ_0=None):
        """Cells of ``mask`` (and, on COND1, of the net's contacts ``vmask``)
        closer to another net's copper than allowed."""
        bad = np.zeros_like(mask)
        other = _others(n, L, occ_L, mask)
        if other.any() and mask.any():
            d = ndimage.distance_transform_edt(~other) * cell
            bad |= mask & (d < need(n, L) - 1e-6)
        if L == 0 and occ_v is not None:
            ov = _others(n, "v", occ_v, mask)
            if ov.any() and mask.any():               # my COND1 by their contacts
                d = ndimage.distance_transform_edt(~ov) * cell
                bad |= mask & (d < xc_max + width[n] / 2.0 - 1e-6)
            if vmask is not None and vmask.any() and other.any():   # my contacts by their COND1
                d = ndimage.distance_transform_edt(~other) * cell
                bad |= vmask & (d < xc_of[n] + wmax / 2.0 - 1e-6)
        return bad if bad.any() else None

    nx = G.nx
    xs, ys = G.xs.tolist(), G.ys.tolist()

    def astar(n, sources, targets, cost1, cost2, vcost):
        """A* from any of ``sources`` [(layer, k)] to any of ``targets`` (set
        of (0, k)), on the per-net allowed cells, with the congestion costs.
        State: (layer, k, dir) -- a bend costs bend_cost, a turn inside a pad
        is not allowed. Returns [(layer, k)] or None."""
        a1, a2, av = allow[n]
        kn = net_id[n]
        tx = [xs[k % nx] for (_l, k) in targets]
        ty = [ys[k // nx] for (_l, k) in targets]
        tset = targets
        ny_ = G.ny

        def h(k):
            x, y = xs[k % nx], ys[k // nx]
            return min(abs(x - a) + abs(y - b) for a, b in zip(tx, ty))
        INF = float("inf")
        g = {}
        prev = {}
        pq = []
        for (l, k) in sources:
            s = (l, k, 4)
            g[s] = 0.0
            prev[s] = None
            heapq.heappush(pq, (h(k), 0.0, s))
        moves = DIRS if ortho else DIRS + DIAG
        expanded = 0
        while pq:
            f, gs, s = heapq.heappop(pq)
            if gs > g.get(s, INF):
                continue
            l, k, d = s
            if l == 0 and (0, k) in tset and prev[s] is not None:
                out = []
                while s is not None:
                    out.append((s[0], s[1]))
                    s = prev[s]
                return out[::-1]
            expanded += 1
            if expanded > 400000:
                return None
            i, j = k % nx, k // nx
            x, y = xs[i], ys[j]
            in_pad = padin[k] >= 0
            cost = cost1 if l == 0 else cost2
            allowed = a1 if l == 0 else a2
            lf = 1.0 if l == 0 else cond2_factor
            for di, (dx, dy) in enumerate(moves):
                ii, jj = i + dx, j + dy
                if ii < 0 or jj < 0 or ii >= nx or jj >= ny_:
                    continue
                kk = jj * nx + ii
                if not allowed[kk]:
                    continue
                bent = d != 4 and d != di
                if bent and in_pad and l == 0:
                    continue                      # no turning inside a pad
                if dx and dy:                     # diagonal: both side cells free too
                    if not (allowed[j * nx + ii] and allowed[jj * nx + i]):
                        continue
                step = math.hypot(xs[ii] - x, ys[jj] - y)
                cc_ = 1.0 if (l == 0 and padin[kk] == kn) else cost[kk]   # own pad: free
                # a bend is charged at the local congestion too: with a fixed
                # bend cost, late rounds (dear steps) staircase freely
                c = (step * lf + (bend_cost if bent else 0.0)) * cc_
                ns = (l, kk, di)
                ng = gs + c
                if ng < g.get(ns, INF):
                    g[ns] = ng
                    prev[ns] = s
                    heapq.heappush(pq, (ng + h(kk), ng, ns))
            if two_cond and av[k] and not in_pad:
                ns = (1 - l, k, 4)
                ng = gs + via_cost / 2.0 * vcost[k]
                if ng < g.get(ns, INF):
                    g[ns] = ng
                    prev[ns] = s
                    heapq.heappush(pq, (ng + h(k), ng, ns))
        return None

    def straight_run(n, ks, cost1):
        """One COND1 run (cells ``ks``, ends fixed) with its staircases taken
        out: from each corner jump to the farthest later corner reachable by
        a straight leg or an L whose cells are all allowed for ``n``, whose
        corner is not inside a pad, and which costs no more (at the present
        congestion) than the stretch it replaces."""
        a1 = allow[n][0]
        kn = net_id[n]

        def cc(k):
            return 1.0 if padin[k] == kn else cost1[k]

        def line(k1, k2):
            i1, j1, i2, j2 = k1 % nx, k1 // nx, k2 % nx, k2 // nx
            if j1 == j2:
                st = 1 if i2 > i1 else -1
                return [j1 * nx + i for i in range(i1, i2 + st, st)]
            st = 1 if j2 > j1 else -1
            return [j * nx + i1 for j in range(j1, j2 + st, st)]

        def dirn(k1, k2):
            return (k2 % nx - k1 % nx, k2 // nx - k1 // nx)
        corners = [0] + [i for i in range(1, len(ks) - 1)
                         if dirn(ks[i - 1], ks[i]) != dirn(ks[i], ks[i + 1])] + [len(ks) - 1]
        if len(corners) < 4:
            return ks
        out = [ks[0]]
        ci = 0
        while ci < len(corners) - 1:
            i = corners[ci]
            done = False
            for cj in range(len(corners) - 1, ci + 1, -1):
                j = corners[cj]
                old_cost = sum(cc(k) for k in ks[i + 1:j + 1]) + bend_cost * (cj - ci - 1)
                k1, k2 = ks[i], ks[j]
                i1, j1, i2, j2 = k1 % nx, k1 // nx, k2 % nx, k2 // nx
                if i1 == i2 or j1 == j2:
                    cands = [[k1, k2]]
                else:
                    cands = [[k1, j1 * nx + i2, k2], [k1, j2 * nx + i1, k2]]
                for cnd in cands:
                    if len(cnd) == 3 and padin[cnd[1]] >= 0:
                        continue                         # no turning inside a pad
                    cells_ = line(cnd[0], cnd[1])
                    if len(cnd) == 3:
                        cells_ += line(cnd[1], cnd[2])[1:]
                    if not all(a1[k] for k in cells_):
                        continue
                    new_cost = sum(cc(k) for k in cells_[1:]) + bend_cost * (len(cnd) - 2)
                    if new_cost <= old_cost + 1e-6:
                        out += cells_[1:]
                        ci = cj
                        done = True
                        break
                if done:
                    break
            if not done:
                out += ks[i + 1:corners[ci + 1] + 1]
                ci += 1
        return out

    def straighten(n, path, cost1):
        runs = []
        for l, k in path:
            if runs and runs[-1][0] == l:
                runs[-1][1].append(k)
            else:
                runs.append([l, [k]])
        res = []
        for l, ks in runs:
            if ortho and l == 0 and len(ks) >= 4:   # 90-degree grids only
                ks = straight_run(n, ks, cost1)
            res += [(l, k) for k in ks]
        return res

    def route_net(n, pf):
        k = net_id[n]
        # present-congestion cost: cells nearer another net's centre line than
        # the clearance allows (the net itself was ripped up first)
        c = []
        for L in (0, 1):
            other = occ[L] > 0
            if other.any():
                d = ndimage.distance_transform_edt(~other) * cell
                near = (d < need(n, L) - 1e-6).astype(np.float32)
                cc = (1.0 + hist[L]) * (1.0 + pf * near)
            else:
                cc = 1.0 + hist[L]
            c.append(cc.ravel().tolist())
        # COND1 by other nets' contacts, and a contact by other nets' COND1
        ov = occv > 0
        if ov.any():
            d = ndimage.distance_transform_edt(~ov) * cell
            c[0] = (np.asarray(c[0]).reshape(G.ny, G.nx) *
                    (1.0 + pf * (d < xc_max + width[n] / 2.0))).ravel().tolist()
        o1 = occ[0] > 0
        if o1.any():
            d = ndimage.distance_transform_edt(~o1) * cell
            vc = (1.0 + pf * (d < xc_of[n] + wmax / 2.0)).ravel().tolist()
        else:
            vc = [1.0] * G.n
        groups = [list(gr) for gr in P.groups[n]]
        tree_terms = groups.pop(0)
        tree = [(0, G.idx(*t.centre)) for t in tree_terms]
        cells = list(tree)
        conns, fail = [], []
        while groups:
            starts = [t.centre for t in tree_terms]
            groups.sort(key=lambda gr: min(math.hypot(s[0] - t.centre[0], s[1] - t.centre[1])
                                           for s in starts for t in gr))
            gr = groups.pop(0)
            targets = {(0, G.idx(*t.centre)) for t in gr}
            srcs = [c_ for c_ in cells if c_[0] == 0]
            path = astar(n, srcs, targets, c[0], c[1], vc)
            if path is None:
                fail.append({"net": n, "from": tree_terms[0].tid, "to": gr[0].tid})
                tree_terms = gr                              # grow on from here
                cells += [(0, G.idx(*t.centre)) for t in gr]
                continue
            path = straighten(n, path, c[0])
            conns.append(path)
            cells += path
            cells += [(0, G.idx(*t.centre)) for t in gr]
            tree_terms = tree_terms + gr
        routes[n] = {"cells": list(dict.fromkeys(cells)), "conns": conns, "fail": fail}

    def conflicts():
        """{net: [(layer, cells of its copper too near another net)]}."""
        out = {}
        for n in routes:
            for L in (0, 1):
                m = own.get((n, L))
                if m is None:
                    continue
                bad = too_near(n, L, m, occ[L], own.get((n, "v")), occv)
                if bad is not None:
                    out.setdefault(n, []).append((L, bad))
        return out

    # order: shortest nets first (they have the fewest options)
    def spread(n):
        ts = [t for gr in P.groups[n] for t in gr]
        return (max(t.centre[0] for t in ts) - min(t.centre[0] for t in ts)) + \
               (max(t.centre[1] for t in ts) - min(t.centre[1] for t in ts))
    order = sorted([n for n in nets if len(P.groups[n]) >= 2], key=lambda n: (spread(n), n))
    pf = PF0
    best = None
    stopped = False
    todo = list(order)
    for it in range(1, max(1, iterations) + 1):
        t0 = time.time()
        for i, n in enumerate(todo, 1):
            if cancel is not None and cancel():
                stopped = True
                break
            mark(n, -1)
            route_net(n, pf)
            mark(n, +1)
        conf = conflicts()
        bad = set(conf)
        n_fail = sum(len(r["fail"]) for r in routes.values())
        ncells = sum(int(m.sum()) for v in conf.values() for _L, m in v)
        say(f"round {it}: {len(bad)} net(s) too close to another ({ncells} cells), "
            f"{n_fail} connection(s) without a path ({time.time() - t0:.1f}s)")
        # remember the best legal state: overlapping nets count as unrouted
        legal_routed = sum(len(r["conns"]) for nm, r in routes.items() if nm not in bad)
        if best is None or legal_routed > best[0]:
            best = (legal_routed, {nm: dict(r) for nm, r in routes.items()}, set(bad))
        if not bad or stopped:
            break
        if time_budget is not None and time.time() - t_start > time_budget:
            say(f"out of time after round {it}")
            break
        for v in conf.values():
            for L, m in v:
                hist[L][m] += HIST_INC
        pf *= PF_GROW
        # reroute the nets in conflict (and whatever failed), worst first
        todo = [n for n in order if n in bad or routes.get(n, {}).get("fail")]

    if best is None:                       # stopped inside the first round
        best = (0, {nm: dict(r) for nm, r in routes.items()}, set(conflicts()))
    _n, final, bad = best
    # a net still too close to another: drop as few connections as will clear
    # it -- one at a time, the one in most trouble first, rechecking after each
    drop = {}
    if bad:
        conn_mask = {}
        for nm, r in final.items():
            for ci, path in enumerate(r["conns"]):
                conn_mask[(nm, ci)] = (cells_mask(nm, path, 0), cells_mask(nm, path, 1),
                                       via_mask(nm, path))
        kept = set(conn_mask)

        def net_masks():
            out = {}
            for (nm, ci) in kept:
                m0, m1, mv = conn_mask[(nm, ci)]
                a = out.setdefault(nm, [np.zeros_like(m0), np.zeros_like(m1), np.zeros_like(mv)])
                a[0] |= m0
                a[1] |= m1
                a[2] |= mv
            # the pads the net reaches count too (always kept)
            return out
        for _round in range(len(conn_mask)):
            nmask = net_masks()
            focc = [np.zeros((G.ny, G.nx), np.int16), np.zeros((G.ny, G.nx), np.int16)]
            foccv = np.zeros((G.ny, G.nx), np.int16)
            own.clear()
            for nm, (m0, m1, mv) in nmask.items():
                focc[0] += m0.astype(np.int16)
                focc[1] += m1.astype(np.int16)
                foccv += mv.astype(np.int16)
                own[(nm, 0)], own[(nm, 1)], own[(nm, "v")] = m0, m1, mv
            worst, worst_n = None, 0
            for key in kept:
                nm = key[0]
                nbad = 0
                for L in (0, 1):
                    b_ = too_near(nm, L, conn_mask[key][L], focc[L],
                                  conn_mask[key][2] if L == 0 else None, foccv)
                    if b_ is not None:
                        nbad += int(b_.sum())
                if nbad > worst_n:
                    worst, worst_n = key, nbad
            if worst is None:
                break
            kept.discard(worst)
            drop.setdefault(worst[0], set()).add(worst[1])
    return _emit(design, P, G, final, drop, order, stopped, t_start, say)


def _align_ends(pts, centre_of):
    """Line a run's ends up on the pads they reach: the end becomes the pad
    centre and its last straight leg moves onto the pad's axis (a shift of
    under half a cell), so the track meets the pad square and centred."""
    pts = [list(p) for p in pts]
    for end, nxt in ((0, 1), (-1, -2)):
        c = centre_of(pts[end])
        if c is None or len(pts) < 2:
            continue
        a, b = pts[end], pts[nxt]
        if abs(a[1] - b[1]) < 1e-6:                 # horizontal leg: onto y = cy
            a[1] = b[1] = c[1]
            a[0] = c[0]
        elif abs(a[0] - b[0]) < 1e-6:               # vertical leg: onto x = cx
            a[0] = b[0] = c[0]
            a[1] = c[1]
        else:
            a[0], a[1] = c
    return [tuple(p) for p in pts]


def _clear_of(P, net, w, pts, others, layer):
    """Polyline ``pts`` of ``net`` (width w) keeps its clearance from the
    other nets' copper ``others`` [(net, w, a, b)] and the fixed obstacles."""
    sp = P.min_space
    blk = SR._Scoped(P.space.layer_blockers(layer))
    blk.bb = (-1e18, -1e18, 1e18, 1e18)
    m = w / 2.0 + sp
    for i in range(1, len(pts)):
        a, b = pts[i - 1], pts[i]
        if not P.space.segment_clear(a, b, net, m, blk):
            return False
        for n2, w2, c, d in others:
            if n2 == net:
                continue
            if SR._seg_seg_dist(a[0], a[1], b[0], b[1], c[0], c[1], d[0], d[1]) < \
                    (w + w2) / 2.0 + sp - 0.5:
                return False
    return True


def _straighten(P, net, w, pts, others, layer, ortho=True):
    """Take the grid's staircases and jogs out of a run: from each corner,
    jump to the farthest later corner reachable by a straight leg or an L
    (either way round; with 45-degree routing a direct leg first) that keeps
    clear (_clear_of). The ends stay where they are. Congestion makes a grid
    step dear and a bend cheap by comparison, so A* staircases freely; this
    puts the corners back where they need to be."""
    if len(pts) < 4:
        return pts
    pts = [tuple(p) for p in pts]
    out = [pts[0]]
    i = 0
    while i < len(pts) - 1:
        best = None
        for j in range(len(pts) - 1, i + 1, -1):
            a, b = pts[i], pts[j]
            if abs(a[0] - b[0]) < 1e-6 or abs(a[1] - b[1]) < 1e-6:
                cands = [[a, b]]
            else:
                cands = [] if ortho else [[a, b]]
                cands += [[a, (b[0], a[1]), b], [a, (a[0], b[1]), b]]
            for cnd in cands:
                if _clear_of(P, net, w, cnd, others, layer):
                    best = (j, cnd)
                    break
            if best:
                break
        if best is None:
            out.append(pts[i + 1])
            i += 1
        else:
            out += best[1][1:]
            i = best[0]
    # drop collinear points
    res = [out[0]]
    for k in range(1, len(out) - 1):
        a, b, c = res[-1], out[k], out[k + 1]
        if b == a:
            continue
        if abs((b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])) < 1e-6:
            continue
        res.append(b)
    res.append(out[-1])
    return res


def _emit(design, P, G, routes, drop, order, stopped, t_start, say):
    """Turn the grid routes into the result dict: COND1 runs are traces,
    COND2 runs local crossovers (their via lands at the ends). A net still
    overlapping another is left out (unrouted), never drawn."""
    term_rect = {(round(t.centre[0]), round(t.centre[1])): t.rect for t in P.terminals}
    traces, crossovers, unrouted = [], [], []
    connections = sum(max(0, len(P.groups[n]) - 1) for n in order)
    routed = 0

    def simplify(pts):
        out = [pts[0]]
        for i in range(1, len(pts) - 1):
            a, b, c = out[-1], pts[i], pts[i + 1]
            if (b[0] - a[0]) * (c[1] - b[1]) == (b[1] - a[1]) * (c[0] - b[0]):
                continue
            out.append(b)
        if len(pts) > 1:
            out.append(pts[-1])
        return out

    cell_terms = {}
    for t in P.terminals:
        cell_terms.setdefault(G.idx(*t.centre), []).append(t)

    pending = []                          # (net, w, COND1 run, centre_of)
    for n in order:
        r = routes.get(n)
        if r is None:
            continue
        unrouted += r["fail"]
        w = P.width_of(n)

        def centre_of(p, n=n):
            for t in cell_terms.get(G.idx(*p), ()):
                if t.net == n:
                    return t.centre
            return None
        for ci, path in enumerate(r["conns"]):
            if ci in drop.get(n, ()):
                a = next((t for t in cell_terms.get(path[0][1], ()) if t.net == n), None)
                b = next((t for t in cell_terms.get(path[-1][1], ()) if t.net == n), None)
                unrouted.append({"net": n, "from": a.tid if a else "?", "to": b.tid if b else "?"})
                continue
            routed += 1
            runs = []
            for (l, k) in path:
                xy = G.xy(k)
                if not runs or runs[-1][0] != l:
                    runs.append([l, [xy]])
                else:
                    runs[-1][1].append(xy)
            for l, pts in runs:
                pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
                if l == 0:
                    if len(pts) >= 2:
                        pending.append((n, w, simplify(pts), centre_of))
                else:
                    br = simplify(pts)
                    if len(br) < 2:
                        continue
                    crossovers.append({"net": n, "widthUm": int(round(w)),
                                       "dielWidthUm": int(round(P.strip_w(w))),
                                       "landUm": int(round(P.contact(w))),
                                       "dielGapUm": int(round(P.diel_gap)),
                                       "a": {"x": int(round(br[0][0])), "y": int(round(br[0][1]))},
                                       "b": {"x": int(round(br[-1][0])), "y": int(round(br[-1][1]))},
                                       "bridge": [{"x": int(round(x)), "y": int(round(y))}
                                                  for x, y in br]})
    # line each run's ends up on its pads' centres -- where the shift (under
    # half a cell) keeps it clear of every other net's copper; else it stays
    cop = [(n, w, a, b) for n, w, pts, _c in pending for a, b in zip(pts, pts[1:])]
    for c in crossovers:
        # round a contact the COND2 runs bare (the dielectric stops short of
        # it): foreign COND1 keeps the contact clearance (xclr), as on the
        # grid -- _clear_of adds w/2 + min_space, so size the "land" to match
        land = max(float(c["landUm"]), 2.0 * (float(P.xclr(c["net"])) - P.min_space))
        for q in (c["a"], c["b"]):
            xy = (float(q["x"]), float(q["y"]))
            cop.append((c["net"], land, xy, xy))
    jogs_before = jogs_after = 0
    for idx, (n, w, pts, centre_of) in enumerate(pending):
        others = [x for x in cop if x[0] != n]
        st = _straighten(P, n, w, pts, others, P.layers[0], P.orthogonal)
        jogs_before += len(pts)
        jogs_after += len(st)
        if st != pts:
            old_segs = set(zip(pts, pts[1:]))
            cop = [x for x in cop if not (x[0] == n and (x[2], x[3]) in old_segs)]
            cop += [(n, w, a, b) for a, b in zip(st, st[1:])]
            pending[idx] = (n, w, st, centre_of)
    say(f"straightened: {jogs_before} -> {jogs_after} points")
    for n, w, pts, centre_of in pending:
        al = _align_ends(pts, centre_of)
        if al != pts:
            others = [x for x in cop if x[0] != n]
            if _clear_of(P, n, w, al, others, P.layers[0]):
                cop = [x for x in cop if not (x[0] == n and (x[2], x[3]) in set(zip(pts, pts[1:])))]
                cop += [(n, w, a, b) for a, b in zip(al, al[1:])]
                pts = al
        traces.append({"net": n, "layer": P.layers[0], "widthUm": int(round(w)),
                       "points": [{"x": int(round(x)), "y": int(round(y))} for x, y in pts]})
    length = 0.0
    for t in traces:
        p = t["points"]
        for i in range(1, len(p)):
            length += math.hypot(p[i]["x"] - p[i - 1]["x"], p[i]["y"] - p[i - 1]["y"])
    stats = {"connections": connections, "routed": routed, "jumpersAdded": 0,
             "stopped": stopped, "crossoversAdded": len(crossovers), "fingersAdded": 0,
             "windows": 0, "traceLengthUm": int(length), "gridUm": 0, "engine": "pathfinder"}
    say(f"done in {time.time() - t_start:.1f}s: {routed}/{connections} routed, "
        f"{len(crossovers)} crossover(s)")
    return {"design": design, "traces": traces, "crossovers": crossovers, "windows": [],
            "unrouted": unrouted, "stats": stats, "issues": []}
