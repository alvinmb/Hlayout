"""Auto Place engine: put parts where their nets are shortest.

Pure geometry -- no Tk, no Library: hlayout.scripting.api builds the parts,
nets and placement region from a design and a netlist (Session.auto_place())
and writes the result back as placed cells.

Cost (all lengths in DBU = um):

    C = sum_nets w_n * MST_n                    weighted straight-line length
      + thermal * K_T * sum_{i<j} P_i P_j / d_ij   hot parts apart
      + LAM_OVERLAP * overlap_area / 1000       parts may not overlap
      + LAM_OUT * area_outside_region / 1000    ... or leave the region

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


def _ov(a, b):
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _outside(b, region):
    area = (b[2] - b[0]) * (b[3] - b[1])
    return area - _ov(b, region)


class Placer:
    def __init__(self, parts: List[Part], nets: List[PNet], region, grid: float = 50.0,
                 thermal: float = 0.0, obstacles=(), seed: int = 0):
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
        self.net_len = [self._net_length(k) for k in range(len(self.nets))]

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
        c = self.wirelength()
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
        before, nets = self._local(moved)
        old_len = {k: self.net_len[k] for k in nets}
        before += sum(self.nets[k].weight * old_len[k] for k in nets)
        apply()
        after, nets2 = self._local(moved)
        new_len = {k: self._net_length(k) for k in nets2 | nets}
        after += sum(self.nets[k].weight * new_len[k] for k in nets2 | nets)
        d = after - before
        if d <= 0 or (T > 0 and self.rng.random() < math.exp(-d / T)):
            for k, v in new_len.items():
                self.net_len[k] = v
            return d, True
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
        tx, ty = snap(tx, x0), snap(ty, y0)
        if self._legal(i, tx, ty):
            return tx, ty
        for ring in range(1, limit):
            pts = []
            for k in range(-ring, ring + 1):
                pts += [(k, -ring), (k, ring), (-ring, k), (ring, k)]
            self.rng.shuffle(pts)
            pts.sort(key=lambda d: math.hypot(*d))
            for dx, dy in pts:
                x, y = snap(tx + dx * step, x0), snap(ty + dy * step, y0)
                if self._legal(i, x, y):
                    return x, y
            if ring * step > 2 * max(self.region[2] - self.region[0], self.region[3] - self.region[1]):
                break
        return None

    def start(self):
        """Fixed and already-placed parts stay; the rest go in order of how
        many connections they have to what is already placed, each at the
        centroid of those connections, at the nearest free grid spot."""
        cx = (self.region[0] + self.region[2]) / 2.0
        cy = (self.region[1] + self.region[3]) / 2.0
        todo = [i for i in self.movable if not self.parts[i].placed]
        while todo:
            def links(i):
                pts = []
                for k in self.part_nets[i]:
                    n = self.nets[k]
                    pts += list(n.anchors)
                    pts += [self.parts[j].pin_xy(p) for j, p in n.members
                            if j != i and self.parts[j].placed]
                return pts
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
            todo.remove(best)
        self.net_len = [self._net_length(k) for k in range(len(self.nets))]

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
        self.net_len = [self._net_length(k) for k in range(len(self.nets))]

    def legalise(self):
        g = self.grid
        x0, y0 = self.region[0], self.region[1]
        for i in self.movable:
            p = self.parts[i]
            p.x = x0 + round((p.x - x0) / g) * g
            p.y = y0 + round((p.y - y0) / g) * g
        cx = (self.region[0] + self.region[2]) / 2.0
        cy = (self.region[1] + self.region[3]) / 2.0
        for _pass in range(6):
            bad = [i for i in self.movable if not self._legal(i, self.parts[i].x, self.parts[i].y)]
            if not bad:
                break
            bad.sort(key=lambda i: -(self.parts[i].bbox[2] - self.parts[i].bbox[0]) *
                     (self.parts[i].bbox[3] - self.parts[i].bbox[1]))
            for i in bad:
                p = self.parts[i]
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
        self.net_len = [self._net_length(k) for k in range(len(self.nets))]

    def run(self, effort=1.0, improve=False, on_progress=None, cancel=None):
        if not improve:
            self.start()
        else:
            for p in self.parts:
                p.placed = True
        bad, out = self.overlaps()
        if bad or out or not improve:
            self.legalise()
            bad, out = self.overlaps()
        self.legal_only = not bad and not out
        if improve:
            self.anneal(effort, on_progress=on_progress, cancel=cancel, hot=0.05)
        else:
            # explore, then refine (the refine pass is what makes it repeatable)
            half = (lambda f: on_progress(f * 0.6)) if on_progress else None
            rest = (lambda f: on_progress(0.6 + f * 0.4)) if on_progress else None
            self.anneal(effort, on_progress=half, cancel=cancel, hot=1.0)
            self.anneal(effort * 0.7, on_progress=rest, cancel=cancel, hot=0.05)
        self.legalise()
        if on_progress:
            on_progress(1.0)
