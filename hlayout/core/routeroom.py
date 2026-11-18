# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Routing room for Auto Place: track capacity vs track demand on a coarse
grid (router/docs/hybrid/routability-driven-placement.md, sections 4.1-4.2).

Pure geometry, no Tk, no Library -- it reads a Placer's parts, nets and
obstacles. Lengths in DBU = um.

Grid: square gcells of side G (default 4 track pitches -- the size that
predicted PathFinder best in the step 0 calibration, Spearman 0.91 on
PRE+AMP). Two kinds of gcell edge:

  H edge (i, j): the vertical cut x = X0 + (i+1) G between gcell (i, j) and
                 (i+1, j). Horizontal tracks cross it.
  V edge (i, j): the horizontal cut y = Y0 + (j+1) G between gcell (i, j) and
                 (i, j+1). Vertical tracks cross it.

Capacity of an edge = floor(free length of its cut / pitch): how many tracks
fit through. "Free" = inside the routing area and not inside any part
footprint (its world bbox -- pads, body and clearance) or obstacle.

Demand: every MST edge of every net (the SLIs) as two L-shaped routes of
weight 0.5 each (one when the edge is straight in grid terms); each adds its
weight to every gcell edge it crosses. A crossing inside the footprint of a
part the SLI ends on is not counted -- that is the pin's own escape.

Overflow = sum over edges of max(0, demand - capacity) (tracks with nowhere
to go); ``overflow2`` the sum of squares. ``update(moved)`` keeps both equal
to a rebuild after parts move, touching only the edges and nets involved.
"""
import math
from typing import Dict, Iterable, List, Optional, Tuple

from .autoplace import mst

#: families whose footprint lets tracks pass between the terminals when
#: ``under_chip`` is on (a crude model -- the calibration found it hurt the
#: prediction, so it is off by default)
CHIP_FAMILIES = ("smd", "chip", "capacitor")
#: under_chip: the middle fraction of a chip footprint that stays open
CHIP_OPEN = 0.4


class RouteRoom:
    def __init__(self, area, pitch: float, cell: float = 0.0, under_chip: bool = False):
        self.area = tuple(float(v) for v in area)
        self.pitch = max(1.0, float(pitch))
        self.G = float(cell) if cell and cell > 0 else 4.0 * self.pitch
        X0, Y0, X1, Y1 = self.area
        self.nx = max(1, int(math.ceil((X1 - X0) / self.G - 1e-9)))
        self.ny = max(1, int(math.ceil((Y1 - Y0) / self.G - 1e-9)))
        self.under_chip = bool(under_chip)
        nH = max(0, self.nx - 1) * self.ny
        nV = self.nx * max(0, self.ny - 1)
        self.capH = [0] * nH
        self.capV = [0] * nV
        self.dH = [0.0] * nH
        self.dV = [0.0] * nV
        self.overflow = 0.0
        self.overflow2 = 0.0
        self._placer = None
        self._bb: List[Tuple[float, float, float, float]] = []
        self._contrib: Dict[int, List[Tuple[int, int, float]]] = {}   # net -> [(0=H/1=V, idx, w)]

    # ------------------------------------------------------------ indexing
    def _hi(self, i, j):
        return i * self.ny + j

    def _vi(self, i, j):
        return i * (self.ny - 1) + j

    def gx(self, x):
        return min(self.nx - 1, max(0, int((x - self.area[0]) // self.G)))

    def gy(self, y):
        return min(self.ny - 1, max(0, int((y - self.area[1]) // self.G)))

    # ------------------------------------------------------------ capacity
    def _blockers(self):
        p = self._placer
        out = [(b, (p.parts[i].family in CHIP_FAMILIES) and self.under_chip)
               for i, b in enumerate(self._bb)]
        out += [(tuple(o), False) for o in p.obstacles]
        return out

    def _free(self, lo, hi, at, cut_is_vertical, blk):
        """Free length of a cut: vertical (x = at, y in [lo, hi]) or
        horizontal (y = at, x in [lo, hi]), clipped to the area."""
        X0, Y0, X1, Y1 = self.area
        if cut_is_vertical:
            if not (X0 <= at <= X1):
                return 0.0
            lo, hi = max(lo, Y0), min(hi, Y1)
        else:
            if not (Y0 <= at <= Y1):
                return 0.0
            lo, hi = max(lo, X0), min(hi, X1)
        if hi <= lo:
            return 0.0
        cov = []
        for (bx0, by0, bx1, by1), chip in blk:
            if cut_is_vertical:
                if not (bx0 < at < bx1):
                    continue
                s, e, a, b = max(lo, by0), min(hi, by1), by0, by1
            else:
                if not (by0 < at < by1):
                    continue
                s, e, a, b = max(lo, bx0), min(hi, bx1), bx0, bx1
            if e <= s:
                continue
            if chip:
                m = (b - a) * (1.0 - CHIP_OPEN) / 2.0
                s2, e2 = max(lo, a + m), min(hi, b - m)
                if e2 > s2:
                    cov += [(s, s2), (e2, e)]
                    continue
            cov.append((s, e))
        cov.sort()
        used, cs, ce = 0.0, None, None
        for s, e in cov:
            if ce is None or s > ce:
                if ce is not None:
                    used += ce - cs
                cs, ce = s, e
            elif e > ce:
                ce = e
        if ce is not None:
            used += ce - cs
        return max(0.0, (hi - lo) - used)

    def _cap_H(self, i, j, blk):
        X0, Y0 = self.area[0], self.area[1]
        G = self.G
        return int(math.floor(self._free(Y0 + j * G, Y0 + (j + 1) * G, X0 + (i + 1) * G,
                                         True, blk) / self.pitch + 1e-9))

    def _cap_V(self, i, j, blk):
        X0, Y0 = self.area[0], self.area[1]
        G = self.G
        return int(math.floor(self._free(X0 + i * G, X0 + (i + 1) * G, Y0 + (j + 1) * G,
                                         False, blk) / self.pitch + 1e-9))

    def _edges_near(self, b):
        """(H indices, V indices) of the edges whose cut meets rect b."""
        X0, Y0 = self.area[0], self.area[1]
        G = self.G
        bx0, by0, bx1, by1 = b
        H, V = [], []
        i0 = max(0, int(math.floor((bx0 - X0) / G)) - 1)
        i1 = min(self.nx - 2, int(math.floor((bx1 - X0) / G)))
        j0, j1 = self.gy(by0), self.gy(by1)
        for i in range(i0, i1 + 1):
            x = X0 + (i + 1) * G
            if bx0 <= x <= bx1:
                H += [self._hi(i, j) for j in range(j0, j1 + 1)]
        j0 = max(0, int(math.floor((by0 - Y0) / G)) - 1)
        j1 = min(self.ny - 2, int(math.floor((by1 - Y0) / G)))
        i0, i1 = self.gx(bx0), self.gx(bx1)
        for j in range(j0, j1 + 1):
            y = Y0 + (j + 1) * G
            if by0 <= y <= by1:
                V += [self._vi(i, j) for i in range(i0, i1 + 1)]
        return H, V

    # ------------------------------------------------------------ demand
    def _net_contrib(self, k):
        p = self._placer
        n = p.nets[k]
        pts, own = [], []
        for i, pin in n.members:
            pts.append(p.parts[i].pin_xy(pin))
            own.append(i)
        for a in n.anchors:
            pts.append((float(a[0]), float(a[1])))
            own.append(None)
        if len(pts) < 2:
            return []
        out: List[Tuple[int, int, float]] = []
        _L, edges = mst(pts)
        for a, b in edges:
            (x1, y1), (x2, y2) = pts[a], pts[b]
            oa, ob = own[a], own[b]
            if self.gx(x1) == self.gx(x2) or self.gy(y1) == self.gy(y2):
                self._hseg(x1, x2, y1, 1.0, oa, ob, out)
                self._vseg(y1, y2, x1, 1.0, oa, ob, out)
            else:
                # L1: along y1 then down x2;  L2: along x1 then across y2
                self._hseg(x1, x2, y1, 0.5, oa, None, out)
                self._vseg(y1, y2, x2, 0.5, None, ob, out)
                self._vseg(y1, y2, x1, 0.5, oa, None, out)
                self._hseg(x1, x2, y2, 0.5, None, ob, out)
        return out

    def _inside(self, x, y, owner):
        if owner is None:
            return False
        b = self._bb[owner]
        return b[0] <= x <= b[2] and b[1] <= y <= b[3]

    def _hseg(self, xa, xb, y, w, oa, ob, out):
        if xa > xb:
            xa, xb, oa, ob = xb, xa, ob, oa
        j = self.gy(y)
        X0, G = self.area[0], self.G
        for i in range(self.gx(xa), self.gx(xb)):
            xc = X0 + (i + 1) * G
            if self._inside(xc, y, oa) or self._inside(xc, y, ob):
                continue
            if i < self.nx - 1:
                out.append((0, self._hi(i, j), w))

    def _vseg(self, ya, yb, x, w, oa, ob, out):
        if ya > yb:
            ya, yb, oa, ob = yb, ya, ob, oa
        i = self.gx(x)
        Y0, G = self.area[1], self.G
        for j in range(self.gy(ya), self.gy(yb)):
            yc = Y0 + (j + 1) * G
            if self._inside(x, yc, oa) or self._inside(x, yc, ob):
                continue
            if j < self.ny - 1:
                out.append((1, self._vi(i, j), w))

    # ------------------------------------------------------------ overflow bookkeeping
    @staticmethod
    def _ov(d, c):
        return d - c if d > c else 0.0

    def _set_edge(self, kind, idx, d=None, c=None):
        D, C = (self.dH, self.capH) if kind == 0 else (self.dV, self.capV)
        o = self._ov(D[idx], C[idx])
        self.overflow -= o
        self.overflow2 -= o * o
        if d is not None:
            D[idx] = d
        if c is not None:
            C[idx] = c
        o = self._ov(D[idx], C[idx])
        self.overflow += o
        self.overflow2 += o * o

    def _apply(self, contrib, sign):
        for kind, idx, w in contrib:
            D = self.dH if kind == 0 else self.dV
            self._set_edge(kind, idx, d=D[idx] + sign * w)

    # ------------------------------------------------------------ public
    def build(self, placer):
        """Everything from scratch for ``placer``'s current placement."""
        self._placer = placer
        self._bb = [p.world_bbox() for p in placer.parts]
        blk = self._blockers()
        for i in range(self.nx - 1):
            for j in range(self.ny):
                self.capH[self._hi(i, j)] = self._cap_H(i, j, blk)
        for i in range(self.nx):
            for j in range(self.ny - 1):
                self.capV[self._vi(i, j)] = self._cap_V(i, j, blk)
        self.dH = [0.0] * len(self.dH)
        self.dV = [0.0] * len(self.dV)
        self._contrib = {}
        for k in range(len(placer.nets)):
            c = self._net_contrib(k)
            self._contrib[k] = c
            for kind, idx, w in c:
                (self.dH if kind == 0 else self.dV)[idx] += w
        self._recount()
        return self

    def _recount(self):
        self.overflow = self.overflow2 = 0.0
        for D, C in ((self.dH, self.capH), (self.dV, self.capV)):
            for d, c in zip(D, C):
                o = self._ov(d, c)
                self.overflow += o
                self.overflow2 += o * o

    def update(self, moved: Iterable[int]):
        """Parts ``moved`` have new positions / rotations: bring capacity
        (edges near their old and new footprints) and demand (their nets) up
        to date. Call again after an undo -- it is symmetric."""
        p = self._placer
        moved = list(dict.fromkeys(moved))
        if not moved:
            return
        dirtyH, dirtyV = set(), set()
        for i in moved:
            old = self._bb[i]
            new = p.parts[i].world_bbox()
            for b in (old, new):
                H, V = self._edges_near(b)
                dirtyH.update(H)
                dirtyV.update(V)
            self._bb[i] = new
        if dirtyH or dirtyV:
            blk = self._blockers()
            for idx in dirtyH:
                i, j = divmod(idx, self.ny)
                self._set_edge(0, idx, c=self._cap_H(i, j, blk))
            for idx in dirtyV:
                i, j = divmod(idx, self.ny - 1)
                self._set_edge(1, idx, c=self._cap_V(i, j, blk))
        nets = set()
        for i in moved:
            nets.update(p.part_nets[i])
        # a net's own-escape test uses footprints, so a net touching a moved
        # part's old or new footprint changes even if its pins did not move
        for k in nets:
            self._apply(self._contrib.get(k, []), -1.0)
            c = self._net_contrib(k)
            self._contrib[k] = c
            self._apply(c, +1.0)
        # float drift: snap tiny residues
        if abs(self.overflow) < 1e-9:
            self.overflow = 0.0
        if abs(self.overflow2) < 1e-9:
            self.overflow2 = 0.0

    def edges_over(self) -> int:
        return sum(1 for D, C in ((self.dH, self.capH), (self.dV, self.capV))
                   for d, c in zip(D, C) if d > c)

    def util(self, kind: int, idx: int) -> float:
        D, C = (self.dH, self.capH) if kind == 0 else (self.dV, self.capV)
        return D[idx] / max(C[idx], 0.5)

    def edge_geom(self, kind: int, idx: int):
        """((x0, y0), (x1, y1)) of an edge's cut, for drawing a map."""
        X0, Y0 = self.area[0], self.area[1]
        G = self.G
        if kind == 0:
            i, j = divmod(idx, self.ny)
            x = X0 + (i + 1) * G
            return (x, Y0 + j * G), (x, Y0 + (j + 1) * G)
        i, j = divmod(idx, self.ny - 1)
        y = Y0 + (j + 1) * G
        return (X0 + i * G, y), (X0 + (i + 1) * G, y)

    def summary(self) -> dict:
        return {"overflow": round(self.overflow, 3), "overflow2": round(self.overflow2, 3),
                "edges_over": self.edges_over(), "gcell_um": self.G,
                "grid": (self.nx, self.ny)}


def route_area(region, ring=None, substrate=None):
    """The area tracks may use: the I/O ring box (pads' inner edge plus the
    entry strip), else the substrate, else the placement region. Wider than
    the Auto Place region, which also keeps the edge margin."""
    for b in (ring, substrate):
        if b is not None:
            return tuple(float(v) for v in (b.bounds if hasattr(b, "bounds") else b))
    return tuple(float(v) for v in region)
