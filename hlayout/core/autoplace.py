# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Auto Place engine: put parts where their nets are shortest.

Pure geometry -- no Tk, no Library: hlayout.scripting.api builds the parts,
nets and placement region from a design and a netlist (Session.auto_place())
and writes the result back as placed cells.

Cost (all lengths in DBU = um):

    C = sum_nets w_n * MST_n                    weighted straight-line length
      + thermal * K_T * sum_{i<j} P_i P_j / d_ij   hot parts apart
      + LAM_OVERLAP * overlap_area / 1000       parts may not overlap
      + LAM_OUT * area_outside_region / 1000    ... or leave the region
      + cong * overflow / pitch                 room to route (off when 0)

The congestion term estimates routing room on a coarse grid: each SLI
segment spreads its track area (length x track pitch) evenly over the
cells of its bounding box (where an L-shaped route could run), and each
part body takes its area out of the cells it covers. Overflow is the
track area beyond UTIL of the free area, summed over cells; divided by the
pitch it is "um of track with nowhere to go", weighted by ``cong``.

MST_n = length of the minimum spanning tree over the net's pin positions --
exactly the straight-line interconnects (SLIs) drawn for it. Three passes:
a connectivity-ordered start, simulated annealing (shift / swap / rotate),
then legalisation onto the placement grid with no overlaps.
"""
import math
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

LAM_OVERLAP = 20.0
#: congestion: how much of a cell's free area tracks may fill before it overflows
UTIL = 0.6
LAM_OUT = 20.0
K_THERMAL = 1000.0     # um per (mW^2 / um): two 100 mW parts 5 mm apart ~ 2 mm of wire
MIN_THERMAL_D = 500.0


def rot_pt(x, y, rot):
    r = int(round(rot)) % 360
    if r == 90:
        return -y, x
    if r == 180:
        return -x, -y
    if r == 270:
        return y, -x
    return x, y


def rot_bbox(b, rot):
    pts = [rot_pt(b[0], b[1], rot), rot_pt(b[2], b[3], rot)]
    return (min(p[0] for p in pts), min(p[1] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts))


@dataclass
class Part:
    ref: str
    cell: str
    family: str
    bbox: Tuple[float, float, float, float]      # local footprint (clearance included)
    pins: Dict[str, Tuple[float, float]]         # local pin points used for wirelength
    x: float = 0.0
    y: float = 0.0
    rot: int = 0
    fixed: bool = False
    power: float = 0.0
    placed: bool = False                          # has a position already

    def world_bbox(self, x=None, y=None, rot=None):
        b = rot_bbox(self.bbox, self.rot if rot is None else rot)
        x = self.x if x is None else x
        y = self.y if y is None else y
        return (b[0] + x, b[1] + y, b[2] + x, b[3] + y)

    def pin_xy(self, pin):
        px, py = rot_pt(*self.pins[pin], self.rot)
        return px + self.x, py + self.y


@dataclass
class PNet:
    name: str
    weight: float
    members: List[Tuple[int, str]] = field(default_factory=list)   # (part index, pin)
    anchors: List[Tuple[float, float]] = field(default_factory=list)


def mst(points):
    """(length, [(i, j), ...]) of the Euclidean minimum spanning tree."""
    n = len(points)
    if n < 2:
        return 0.0, []
    inf = float("inf")
    dist = [inf] * n
    parent = [-1] * n
    used = [False] * n
    dist[0] = 0.0
    total = 0.0
    edges = []
    for _ in range(n):
        u = -1
        best = inf
        for v in range(n):
            if not used[v] and dist[v] < best:
                best, u = dist[v], v
        used[u] = True
        if parent[u] >= 0:
            total += best
            edges.append((parent[u], u))
        ux, uy = points[u]
        for v in range(n):
            if not used[v]:
                d = math.hypot(points[v][0] - ux, points[v][1] - uy)
                if d < dist[v]:
                    dist[v], parent[v] = d, u
    return total, edges


def _cross(p, q, r, t):
    """Segments p-q and r-t cross (properly: not just touching at an end)."""
    def o(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return (v > 1e-9) - (v < -1e-9)
    return o(p, q, r) * o(p, q, t) < 0 and o(r, t, p) * o(r, t, q) < 0


def seg_crossings(A, B) -> int:
    """How many times the segments of A cross those of B."""
    n = 0
    for p, q in A:
        ax0, ax1 = (p[0], q[0]) if p[0] <= q[0] else (q[0], p[0])
        ay0, ay1 = (p[1], q[1]) if p[1] <= q[1] else (q[1], p[1])
        for r, t in B:
            if max(r[0], t[0]) < ax0 or min(r[0], t[0]) > ax1 or \
                    max(r[1], t[1]) < ay0 or min(r[1], t[1]) > ay1:
                continue
            if _cross(p, q, r, t):
                n += 1
    return n


def _cross_pt(p, q, r, t):
    """Where segments p-q and r-t cross (properly), else None."""
    if not _cross(p, q, r, t):
        return None
    d = (q[0] - p[0]) * (t[1] - r[1]) - (q[1] - p[1]) * (t[0] - r[0])
    if abs(d) < 1e-12:
        return None
    u = ((r[0] - p[0]) * (t[1] - r[1]) - (r[1] - p[1]) * (t[0] - r[0])) / d
    return (p[0] + u * (q[0] - p[0]), p[1] + u * (q[1] - p[1]))


#: crossover room: a crossing this near a printed resistor (um, edge to
#: point) or another part has no room for a crossover's dielectric strip
XO_RES_CLEAR = 1000.0
XO_PART_CLEAR = 400.0


def _ov(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _outside(b, region):
    area = (b[2] - b[0]) * (b[3] - b[1])
    return area - _ov(b, region)


class Stopped(Exception):
    """Placer.run() was stopped before any placement was complete."""


class Placer:
    def __init__(self, parts: List[Part], nets: List[PNet], region, grid: float = 50.0,
                 thermal: float = 0.0, obstacles=(), seed: int = 0, cross_weight: float = 0.0,
                 cong_weight: float = 0.0, pitch: float = 400.0, cong_cell: float = 0.0,
                 xo_room: float = 0.0):
        self._stop = None
        #: what run() is doing now, for a progress display (read from another
        #: thread; only ever replaced, never mutated)
        self.phase = ""
        self.parts = parts
        self.nets = [n for n in nets if len(n.members) + len(n.anchors) >= 2 and n.weight > 0]
        self.region = tuple(region)
        self.grid = float(grid) or 50.0
        self.thermal = float(thermal or 0.0)
        self.obstacles = [tuple(o) for o in obstacles]
        self.rng = random.Random(seed)
        self.part_nets = [[] for _ in parts]
        for k, n in enumerate(self.nets):
            for i, _p in n.members:
                if k not in self.part_nets[i]:
                    self.part_nets[i].append(k)
        self.movable = [i for i, p in enumerate(parts) if not p.fixed]
        #: annealing only accepts moves that keep every part legal (no
        #: overlap, inside the region) once the start has made them so
        self.legal_only = False
        #: what one crossing of two nets' SLI lines costs, in um of wire: a
        #: crossing is a crossover or jumper later, so the placer avoids them
        self.cross_weight = float(cross_weight or 0.0)
        #: crossover room: a crossing with no room for a crossover (near a
        #: resistor or a part body) counts 1 + xo_room crossings
        self.xo_room = float(xo_room or 0.0)
        #: routing room (see the module doc): weight, track pitch (track +
        #: spacing, um) and the grid cell (default 2.5 pitches)
        self.cong = float(cong_weight or 0.0)
        self.pitch = max(1.0, float(pitch or 400.0))
        self.cong_cell = float(cong_cell or 0.0) or 2.5 * self.pitch
        self._refresh()

    def _net_geom(self, k):
        """(weighted-free length, SLI segments) of net k's spanning tree."""
        pts = self._net_points(k)
        L, edges = mst(pts)
        return L, [(pts[a], pts[b]) for a, b in edges]

    def _refresh(self, ks=None):
        """Recompute the lengths and SLI segments of nets ``ks`` (all if None)."""
        if ks is None or not hasattr(self, "net_segs"):
            geo = [self._net_geom(k) for k in range(len(self.nets))]
            self.net_len = [g[0] for g in geo]
            self.net_segs = [g[1] for g in geo]
            self._cong_build()
            return
        for k in ks:
            self.net_len[k], self.net_segs[k] = self._net_geom(k)

    def _xc(self, A, B):
        """Crossings of segment lists A and B, each with no room for a
        crossover (xo_room) counted 1 + xo_room."""
        if not self.xo_room:
            return seg_crossings(A, B)
        n = 0.0
        for p, q in A:
            for r, t in B:
                pt = _cross_pt(p, q, r, t)
                if pt is not None:
                    n += 1.0 + (self.xo_room if self._blocked(pt) else 0.0)
        return n

    def _blocked(self, pt):
        x, y = pt
        for p in self.parts:
            g = XO_RES_CLEAR if p.family == "resistor" else XO_PART_CLEAR
            b = p.world_bbox()
            if b[0] - g <= x <= b[2] + g and b[1] - g <= y <= b[3] + g:
                return True
        return False

    def crossings(self) -> int:
        """SLI crossings between different nets, in the placement as it is
        (with xo_room: weighted by the room for a crossover)."""
        n = 0
        for a in range(len(self.nets)):
            for b in range(a + 1, len(self.nets)):
                n += self._xc(self.net_segs[a], self.net_segs[b])
        return n

    def _cross_of(self, ks, segs):
        """Crossings involving nets ``ks`` (with segments ``segs``: k -> list)."""
        ks = sorted(ks)
        kset = set(ks)
        n = 0
        for i, a in enumerate(ks):
            for b in ks[i + 1:]:
                n += self._xc(segs[a], segs[b])
            for j in range(len(self.nets)):
                if j not in kset:
                    n += self._xc(segs[a], self.net_segs[j])
        return n

    # --- routing room ----------------------------------------------------
    def _cong_build(self):
        """Demand / blocked grids from scratch (congestion on only)."""
        if not self.cong:
            return
        import numpy as np
        x0, y0, x1, y1 = self.region
        c = self.cong_cell
        self._cg = (x0, y0, c, max(1, int(math.ceil((x1 - x0) / c))),
                    max(1, int(math.ceil((y1 - y0) / c))))
        nx, ny = self._cg[3], self._cg[4]
        self.D = np.zeros((ny, nx))
        self.B = np.zeros((ny, nx))
        for k in range(len(self.nets)):
            for a, b in self.net_segs[k]:
                self._seg(a, b, +1.0)
        for p in self.parts:
            self._body(p.world_bbox(), +1.0)

    def _cells(self, bx0, by0, bx1, by1):
        x0, y0, c, nx, ny = self._cg
        i0 = max(0, min(nx - 1, int((bx0 - x0) // c)))
        i1 = max(0, min(nx - 1, int((bx1 - x0) // c)))
        j0 = max(0, min(ny - 1, int((by0 - y0) // c)))
        j1 = max(0, min(ny - 1, int((by1 - y0) // c)))
        return i0, i1, j0, j1

    def _seg(self, a, b, sign):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L <= 0:
            return
        i0, i1, j0, j1 = self._cells(min(a[0], b[0]), min(a[1], b[1]),
                                     max(a[0], b[0]), max(a[1], b[1]))
        n = (i1 - i0 + 1) * (j1 - j0 + 1)
        self.D[j0:j1 + 1, i0:i1 + 1] += sign * L * self.pitch / n

    def _body(self, bb, sign):
        x0, y0, c, nx, ny = self._cg
        i0, i1, j0, j1 = self._cells(*bb)
        for j in range(j0, j1 + 1):
            cy0, cy1 = y0 + j * c, y0 + (j + 1) * c
            h = min(bb[3], cy1) - max(bb[1], cy0)
            if h <= 0:
                continue
            for i in range(i0, i1 + 1):
                cx0, cx1 = x0 + i * c, x0 + (i + 1) * c
                w = min(bb[2], cx1) - max(bb[0], cx0)
                if w > 0:
                    self.B[j, i] += sign * w * h

    def _overflow(self, win=None):
        import numpy as np
        if win is None:
            D, B = self.D, self.B
        else:
            i0, i1, j0, j1 = win
            D, B = self.D[j0:j1 + 1, i0:i1 + 1], self.B[j0:j1 + 1, i0:i1 + 1]
        cap = np.maximum(0.0, self.cong_cell ** 2 - B) * UTIL
        return float(np.maximum(0.0, D - cap).sum())

    def congestion(self) -> float:
        """Overflow of the placement as it is: um of track with no room."""
        if not self.cong:
            return 0.0
        return self._overflow() / self.pitch

    def _cong_window(self, segs_lists, boxes):
        xs, ys = [], []
        for segs in segs_lists:
            for a, b in segs:
                xs += [a[0], b[0]]
                ys += [a[1], b[1]]
        for bb in boxes:
            xs += [bb[0], bb[2]]
            ys += [bb[1], bb[3]]
        if not xs:
            return None
        return self._cells(min(xs), min(ys), max(xs), max(ys))

    # --- cost pieces ------------------------------------------------------
    def _net_points(self, k):
        n = self.nets[k]
        return [self.parts[i].pin_xy(p) for i, p in n.members] + list(n.anchors)

    def _net_length(self, k):
        return mst(self._net_points(k))[0]

    def wirelength(self):
        return sum(self.nets[k].weight * self.net_len[k] for k in range(len(self.nets)))

    def _pair(self, i, j):
        a, b = self.parts[i], self.parts[j]
        c = LAM_OVERLAP * _ov(a.world_bbox(), b.world_bbox()) / 1000.0
        if self.thermal and a.power and b.power:
            d = max(MIN_THERMAL_D, math.hypot(a.x - b.x, a.y - b.y))
            c += self.thermal * K_THERMAL * a.power * b.power / d
        return c

    def _single(self, i):
        b = self.parts[i].world_bbox()
        c = LAM_OUT * _outside(b, self.region) / 1000.0
        for o in self.obstacles:
            c += LAM_OVERLAP * _ov(b, o) / 1000.0
        return c

    def _local(self, moved):
        moved = list(dict.fromkeys(moved))
        ms = set(moved)
        c = 0.0
        for i in moved:
            c += self._single(i)
            for j in range(len(self.parts)):
                if j in ms and j <= i:
                    continue
                if j != i:
                    c += self._pair(i, j)
        nets = set()
        for i in moved:
            nets.update(self.part_nets[i])
        return c, nets

    def cost(self):
        c = self.wirelength() + self.cross_weight * (self.crossings() if self.cross_weight else 0)
        c += self.cong * self.congestion()
        for i in range(len(self.parts)):
            c += self._single(i)
            for j in range(i + 1, len(self.parts)):
                c += self._pair(i, j)
        return c

    def overlaps(self):
        """[(ref, ref)] of overlapping footprints, and [ref] outside the region."""
        bad, out = [], []
        for i, a in enumerate(self.parts):
            ba = a.world_bbox()
            if _outside(ba, self.region) > 1:
                out.append(a.ref)
            for o in self.obstacles:
                if _ov(ba, o) > 1:
                    bad.append((a.ref, "obstacle"))
                    break
            for j in range(i + 1, len(self.parts)):
                if _ov(ba, self.parts[j].world_bbox()) > 1:
                    bad.append((a.ref, self.parts[j].ref))
        return bad, out

    # --- moves ------------------------------------------------------------
    def _try(self, moved, apply, undo, T, legal_only=None):
        legal_only = self.legal_only if legal_only is None else legal_only
        if legal_only:
            apply()
            ok = all(self._legal(i, self.parts[i].x, self.parts[i].y) for i in moved)
            undo()
            if not ok:
                return 0.0, False
        old_boxes = [self.parts[i].world_bbox() for i in moved] if self.cong else []
        before, nets = self._local(moved)
        old_len = {k: self.net_len[k] for k in nets}
        before += sum(self.nets[k].weight * old_len[k] for k in nets)
        if self.cross_weight:
            before += self.cross_weight * self._cross_of(nets, {k: self.net_segs[k] for k in nets})
        apply()
        after, nets2 = self._local(moved)
        allk = nets2 | nets
        geo = {k: self._net_geom(k) for k in allk}
        after += sum(self.nets[k].weight * geo[k][0] for k in allk)
        if self.cross_weight:
            after += self.cross_weight * self._cross_of(allk, {k: geo[k][1] for k in allk})
        cong_undo = None
        if self.cong:
            new_boxes = [self.parts[i].world_bbox() for i in moved]
            old_segs = [self.net_segs[k] for k in allk]
            new_segs = [geo[k][1] for k in allk]
            win = self._cong_window(old_segs + new_segs, old_boxes + new_boxes)
            o1 = self._overflow(win) if win else 0.0

            def swap_in(sign):
                for sg in old_segs:
                    for a, b in sg:
                        self._seg(a, b, -sign)
                for sg in new_segs:
                    for a, b in sg:
                        self._seg(a, b, sign)
                for bb in old_boxes:
                    self._body(bb, -sign)
                for bb in new_boxes:
                    self._body(bb, sign)
            swap_in(+1.0)
            o2 = self._overflow(win) if win else 0.0
            after += self.cong * (o2 - o1) / self.pitch
            cong_undo = lambda: swap_in(-1.0)
        d = after - before
        if d <= 0 or (T > 0 and self.rng.random() < math.exp(-d / T)):
            for k, (L, sg) in geo.items():
                self.net_len[k] = L
                self.net_segs[k] = sg
            return d, True
        if cong_undo:
            cong_undo()
        undo()
        return d, False

    def _shift(self, i, sigma):
        p = self.parts[i]
        ox, oy = p.x, p.y
        nx = ox + self.rng.gauss(0, sigma)
        ny = oy + self.rng.gauss(0, sigma)

        def apply():
            p.x, p.y = nx, ny

        def undo():
            p.x, p.y = ox, oy
        return [i], apply, undo

    def _rotate(self, i):
        p = self.parts[i]
        o = p.rot
        n = (o + self.rng.choice((90, 180, 270))) % 360

        def apply():
            p.rot = n

        def undo():
            p.rot = o
        return [i], apply, undo

    def _swap(self, i, j):
        a, b = self.parts[i], self.parts[j]
        pa, pb = (a.x, a.y, a.rot), (b.x, b.y, b.rot)

        def apply():
            a.x, a.y, b.x, b.y = pb[0], pb[1], pa[0], pa[1]

        def undo():
            a.x, a.y, a.rot = pa
            b.x, b.y, b.rot = pb
        return [i, j], apply, undo

    def _random_move(self, sigma):
        i = self.rng.choice(self.movable)
        r = self.rng.random()
        if r < 0.15:
            return self._rotate(i)
        if r < 0.35 and len(self.movable) > 1:
            j = self.rng.choice(self.movable)
            if j != i:
                return self._swap(i, j)
        return self._shift(i, sigma)

    # --- passes -----------------------------------------------------------
    def _legal(self, i, x, y, rot=None):
        b = self.parts[i].world_bbox(x, y, rot)
        if _outside(b, self.region) > 1:
            return False
        for o in self.obstacles:
            if _ov(b, o) > 1:
                return False
        for j, q in enumerate(self.parts):
            if j != i and q.placed and _ov(b, q.world_bbox()) > 1:
                return False
        return True

    def _check_stop(self):
        if self._stop is not None and self._stop():
            raise Stopped("stopped")

    def _spiral(self, i, tx, ty, step=None, limit=400):
        """Nearest legal grid spot to (tx, ty), searched in square rings
        of ``step`` (default: an eighth of the part's size, on the grid)."""
        g = self.grid
        if step is None:
            b = self.parts[i].bbox
            size = max(b[2] - b[0], b[3] - b[1])
            step = max(g, g * math.ceil(size / 8.0 / g))
        snap = lambda v, o: o + round((v - o) / g) * g
        x0, y0 = self.region[0], self.region[1]
        R = self.region
        # where the part's origin can go and still sit inside the region:
        # spots outside that are skipped without the (costly) overlap test
        wb = rot_bbox(self.parts[i].bbox, self.parts[i].rot)
        xlo, xhi = R[0] - wb[0] - 1, R[2] - wb[2] + 1
        ylo, yhi = R[1] - wb[1] - 1, R[3] - wb[3] + 1
        if xlo > xhi or ylo > yhi:
            return None                               # bigger than the region this way round
        span = max(R[2] - R[0], R[3] - R[1])
        tx, ty = snap(tx, x0), snap(ty, y0)
        if self._legal(i, tx, ty):
            return tx, ty
        for ring in range(1, limit):
            if ring % 8 == 0:
                self._check_stop()
            pts = []
            for k in range(-ring, ring + 1):
                pts += [(k, -ring), (k, ring), (-ring, k), (ring, k)]
            self.rng.shuffle(pts)
            pts.sort(key=lambda d: math.hypot(*d))
            for dx, dy in pts:
                x, y = snap(tx + dx * step, x0), snap(ty + dy * step, y0)
                if x < xlo or x > xhi or y < ylo or y > yhi:
                    continue
                if self._legal(i, x, y):
                    return x, y
            if ring * step > span:                    # the whole region searched
                break
        return None

    def start(self, on_progress=None):
        """Fixed and already-placed parts stay; the rest go in order of how
        many connections they have to what is already placed, each at the
        centroid of those connections, at the nearest free grid spot."""
        cx = (self.region[0] + self.region[2]) / 2.0
        cy = (self.region[1] + self.region[3]) / 2.0
        todo = [i for i in self.movable if not self.parts[i].placed]
        n0 = max(1, len(todo))
        while todo:
            if on_progress:
                on_progress(1.0 - len(todo) / n0)
            def links(i):
                pts = []
                for k in self.part_nets[i]:
                    n = self.nets[k]
                    pts += list(n.anchors)
                    pts += [self.parts[j].pin_xy(p) for j, p in n.members
                            if j != i and self.parts[j].placed]
                return pts
            self._check_stop()
            best = max(todo, key=lambda i: (len(links(i)), len(self.part_nets[i])))
            pts = links(best)
            tx = sum(p[0] for p in pts) / len(pts) if pts else cx
            ty = sum(p[1] for p in pts) / len(pts) if pts else cy
            spot = self._spiral(best, tx, ty)
            p = self.parts[best]
            if spot is None:
                spot = (tx, ty)
            p.x, p.y = spot
            p.placed = True
            self.phase = f"First placement: {p.ref}"
            todo.remove(best)
        self._refresh()

    def anneal(self, effort=1.0, on_progress=None, cancel=None, hot=1.0):
        """Simulated annealing. ``hot`` scales the starting temperature:
        1 = from scratch, small (Improve) = refine without scrambling."""
        if not self.movable:
            return
        span = max(self.region[2] - self.region[0], self.region[3] - self.region[1])
        # starting temperature from the spread of random move deltas
        samples = []
        for _ in range(40):
            moved, apply, undo = self._random_move(span * 0.1)
            d, ok = self._try(moved, apply, undo, 0.0)
            samples.append(abs(d))
        T0 = max(1.0, sorted(samples)[len(samples) // 2] * 2.0) * hot
        n = max(500, int(effort * 2000 * len(self.movable)))
        T = T0
        alpha = (1e-3) ** (1.0 / n)
        snap = lambda: [(p.x, p.y, p.rot) for p in self.parts]
        cur = best = self.cost()
        best_state = snap()
        for step in range(n):
            if cancel and step % 200 == 0 and cancel():
                break
            sigma = max(4 * self.grid, span * 0.25 * math.sqrt(T / T0))
            moved, apply, undo = self._random_move(sigma)
            d, ok = self._try(moved, apply, undo, T)
            if ok:
                cur += d
                if cur < best - 1e-6:
                    best, best_state = cur, snap()
            T *= alpha
            if on_progress and step % max(1, n // 50) == 0:
                on_progress(step / n)
        for p, (x, y, r) in zip(self.parts, best_state):     # keep the best seen
            p.x, p.y, p.rot = x, y, r
        self._refresh()

    def legalise(self, on_progress=None):
        """Snap to the grid and move every overlapping / out-of-region part to
        the nearest legal spot. A part with no legal spot anywhere is left
        where it is (and not searched for again in a later pass)."""
        g = self.grid
        x0, y0 = self.region[0], self.region[1]
        for i in self.movable:
            p = self.parts[i]
            p.x = x0 + round((p.x - x0) / g) * g
            p.y = y0 + round((p.y - y0) / g) * g
        cx = (self.region[0] + self.region[2]) / 2.0
        cy = (self.region[1] + self.region[3]) / 2.0
        stuck = set()
        self.stuck = []
        for _pass in range(6):
            bad = [i for i in self.movable if i not in stuck and
                   not self._legal(i, self.parts[i].x, self.parts[i].y)]
            if not bad:
                break
            bad.sort(key=lambda i: -(self.parts[i].bbox[2] - self.parts[i].bbox[0]) *
                     (self.parts[i].bbox[3] - self.parts[i].bbox[1]))
            for k, i in enumerate(bad):
                p = self.parts[i]
                self.phase = f"Legalising {p.ref} ({k + 1}/{len(bad)}, pass {_pass + 1})"
                if on_progress:
                    on_progress((_pass + k / len(bad)) / 6.0)
                self._check_stop()
                if self._legal(i, p.x, p.y):
                    continue
                spot = self._spiral(i, p.x, p.y) or self._spiral(i, cx, cy)
                if spot is None:
                    for r in (90, 180, 270):
                        old = p.rot
                        p.rot = (p.rot + r) % 360
                        spot = self._spiral(i, p.x, p.y)
                        if spot:
                            break
                        p.rot = old
                if spot:
                    p.x, p.y = spot
                else:
                    stuck.add(i)                     # nowhere legal: don't search again
        self.stuck = [self.parts[i].ref for i in stuck]
        self._refresh()

    def orient(self, sweeps=3):
        """Final orientation pass: turn each movable part to whichever of
        0 / 90 / 180 / 270 degrees gives its nets the shortest weighted
        length -- its pads facing the parts they connect to -- where it
        still fits. Annealing tries turns at random; this makes sure no
        part is left a quarter turn from its best. Returns how many turns
        were kept."""
        kept = 0
        for _ in range(sweeps):
            better = 0
            for i in self.movable:
                p = self.parts[i]
                if not self.part_nets[i]:
                    continue
                self._check_stop()
                best = None
                for r in (90, 180, 270):
                    o, n = p.rot, (p.rot + r) % 360

                    def apply(p=p, n=n):
                        p.rot = n

                    def undo(p=p, o=o):
                        p.rot = o
                    d, ok = self._try([i], apply, undo, 0.0, legal_only=True)
                    if ok:                      # kept: undo and remember it
                        if d < -1e-6 and (best is None or d < best[0]):
                            best = (d, n)
                        undo()
                        self._refresh(self.part_nets[i])
                        if self.cong:
                            self._cong_build()
                if best is not None:
                    p.rot = best[1]
                    self._refresh(self.part_nets[i])
                    if self.cong:
                        self._cong_build()
                    better += 1
            kept += better
            if not better:
                break
        return kept

    def run(self, effort=1.0, improve=False, on_progress=None, cancel=None):
        """Place. ``cancel`` -- a callable -- is polled all through, so Stop
        works in the first placement and legalising passes too; a stop
        there raises Stopped (nothing placed), one in the annealing keeps
        the best placement so far. ``on_progress`` gets 0..1 across every
        phase; ``self.phase`` names the one running."""
        self._stop = cancel

        def span(a, b):
            return (lambda f: on_progress(a + (b - a) * max(0.0, min(1.0, f)))) if on_progress else None
        if not improve:
            self.phase = "First placement"
            self.start(on_progress=span(0.0, 0.05))
        else:
            for p in self.parts:
                p.placed = True
        bad, out = self.overlaps()
        if bad or out or not improve:
            self.phase = "Legalising"
            self.legalise(on_progress=span(0.05, 0.15))
            bad, out = self.overlaps()
        self.legal_only = not bad and not out
        if improve:
            self.phase = "Improving"
            self.anneal(effort, on_progress=span(0.15, 0.9), cancel=cancel, hot=0.05)
        else:
            # explore, then refine (the refine pass is what makes it repeatable)
            self.phase = "Annealing (explore)"
            self.anneal(effort, on_progress=span(0.15, 0.6), cancel=cancel, hot=1.0)
            self.phase = "Annealing (refine)"
            self.anneal(effort * 0.7, on_progress=span(0.6, 0.9), cancel=cancel, hot=0.05)
        self.phase = "Legalising"
        try:
            self.legalise(on_progress=span(0.9, 0.97))
            self.phase = "Turning parts to face their nets"
            self.orient()
        except Stopped:
            pass                          # stopped late: keep what the annealing found
        self.phase = "Done"
        if on_progress:
            on_progress(1.0)
