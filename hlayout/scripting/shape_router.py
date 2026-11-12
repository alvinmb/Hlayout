# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Gridless shape router for the chip-and-wire hybrid (native Python).

Routes on exact polygon geometry instead of a grid: per-net clearance is true
Euclidean distance, a connection is a shortest legal poly-line found by A* over
a tangent visibility graph of obstacle corners, then pulled tight (rubber-band).
It consumes and produces the same ``hybrid.json`` design/result dicts the Java
engine does (hlayout.scripting.hybridroute), so it drops in behind apply_routed.

Geometry is analytic (no shapely in the hot path) and every query is scoped to
the current connection's bounding box, so it stays tractable on a real board.

Milestones (router/docs/hybrid/gridless-shape-router.md):
  M1 geometry/free-space kit (ShapeSpace)   done
  M2 single-layer planar router (route)     done
  M3 wire-bond jumpers                      done
  M4 local-dielectric crossovers (printed jumper)  done
  2026-11-12: exact segment/rect distance, scope-box-exact clearance, copper
  (not centreline) inside the edge clearance, crossover lands land-clear,
  jumper landing search, max_jumpers 0 = no limit, no crossovers without a
  dielectric layer -- tests/test_shape_router.py

Coordinates are the design's own (substrate-local); the caller maps back.
"""
import heapq
import math
from typing import Dict, List, Optional, Tuple

EPS = 0.5
MAX_NODES = 80
_PEN = None  # optional congestion penalty(a,b)->cost, set by route()
_ORTHO = False  # 90-degree routing: _plan searches the orthogonal grid (set by _route_core)
BEND_FACTOR = 2.0  # a bend costs this many track widths of extra length (fewer jogs)
ORTHO_EXPAND = 40000  # node expansions per orthogonal search (bounds a failed one)
_EXPAND_CAP = None  # a smaller per-call cap (short stubs), set around the call
_DIRS = {}   # layer -> (axis 'H'/'V', againstDirectionCost); preferred-direction bias  # corner-node cap per connection (bounds failed-search cost)
Rect = Tuple[float, float, float, float]
Pt = Tuple[float, float]


# --------------------------------------------------------------------------- analytic geometry

def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _pt_seg_dist(px, py, ax, ay, bx, by) -> float:
    """Distance from point P to segment AB."""
    ux, uy = bx - ax, by - ay
    L2 = ux * ux + uy * uy
    if L2 <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = _clamp(((px - ax) * ux + (py - ay) * uy) / L2, 0.0, 1.0)
    return math.hypot(px - (ax + t * ux), py - (ay + t * uy))


def _seg_seg_dist(ax, ay, bx, by, cx, cy, dx, dy) -> float:
    """Shortest distance between segments AB and CD: 0 when they cross or
    touch, otherwise the least of the four endpoint-to-segment distances
    (exact in 2-D, and stable for near-parallel segments)."""
    def orient(px, py, qx, qy, rx, ry):
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)
    d1 = orient(cx, cy, dx, dy, ax, ay)
    d2 = orient(cx, cy, dx, dy, bx, by)
    d3 = orient(ax, ay, bx, by, cx, cy)
    d4 = orient(ax, ay, bx, by, dx, dy)
    if ((d1 > 0 > d2) or (d1 < 0 < d2)) and ((d3 > 0 > d4) or (d3 < 0 < d4)):
        return 0.0
    return min(_pt_seg_dist(ax, ay, cx, cy, dx, dy), _pt_seg_dist(bx, by, cx, cy, dx, dy),
               _pt_seg_dist(cx, cy, ax, ay, bx, by), _pt_seg_dist(dx, dy, ax, ay, bx, by))


def _pt_in_rect(px, py, r: Rect) -> bool:
    return r[0] - EPS <= px <= r[2] + EPS and r[1] - EPS <= py <= r[3] + EPS


def _seg_rect_dist(ax, ay, bx, by, r: Rect) -> float:
    """Shortest distance between segment AB and axis-aligned rect r (0 if they
    touch or an endpoint is inside). Exact: a Liang-Barsky clip says whether
    they meet; if not, the two are disjoint convex sets, so the minimum is at a
    vertex -- an endpoint to the rect, or a rect corner to the segment."""
    x0, y0, x1, y1 = r
    if x0 - EPS <= ax <= x1 + EPS and y0 - EPS <= ay <= y1 + EPS:
        return 0.0
    if x0 - EPS <= bx <= x1 + EPS and y0 - EPS <= by <= y1 + EPS:
        return 0.0
    ux, uy = bx - ax, by - ay
    t0, t1 = 0.0, 1.0
    hit = True
    for pk, qk in ((-ux, ax - x0), (ux, x1 - ax), (-uy, ay - y0), (uy, y1 - ay)):
        if pk == 0.0:
            if qk < 0.0:
                hit = False
                break
        else:
            t = qk / pk
            if pk < 0.0:
                if t > t1:
                    hit = False
                    break
                if t > t0:
                    t0 = t
            else:
                if t < t0:
                    hit = False
                    break
                if t < t1:
                    t1 = t
    if hit:
        return 0.0
    # endpoints to the rect
    dx = x0 - ax if ax < x0 else (ax - x1 if ax > x1 else 0.0)
    dy = y0 - ay if ay < y0 else (ay - y1 if ay > y1 else 0.0)
    best = dx * dx + dy * dy
    dx = x0 - bx if bx < x0 else (bx - x1 if bx > x1 else 0.0)
    dy = y0 - by if by < y0 else (by - y1 if by > y1 else 0.0)
    d2 = dx * dx + dy * dy
    if d2 < best:
        best = d2
    # rect corners to the segment
    L2 = ux * ux + uy * uy
    if L2 > 1e-12:
        for cx, cy in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            t = ((cx - ax) * ux + (cy - ay) * uy) / L2
            if t <= 0.0 or t >= 1.0:
                continue                    # nearest is an endpoint: done above
            px, py = ax + t * ux - cx, ay + t * uy - cy
            d2 = px * px + py * py
            if d2 < best:
                best = d2
    return math.sqrt(best)


# --------------------------------------------------------------------------- free space

class ShapeSpace:
    """Rectangular blockers per conductor layer plus an optional routing
    boundary (already inset for edge clearance). Clearance is exact; a net's
    half-width-plus-spacing is the ``margin`` passed at query time."""

    def __init__(self, boundary: Optional[Rect] = None, min_space: float = 0.0):
        # layer -> list of (net, rect)
        self._by_layer: Dict[str, List[Tuple[str, Rect]]] = {}
        self.boundary = boundary
        # a query's margin is half-width + min_space; the copper (half-width),
        # not the centreline, must stay inside the edge-clearance boundary
        self.min_space = float(min_space)
        # terminal centres: a pad sits where the designer put it, so a track
        # may start/end there even when the pad is nearer the edge than a track
        self.exempt = set()
        # terminal centre -> its pad rect: a track leaving (or reaching) a pin
        # adds no copper inside that pin, so only the part outside is checked
        # (a pin that abuts its part's body would otherwise be unreachable)
        self.term_rects = {}
        self.term_nets = {}
        self._tgrid = {}           # bucket -> [(net, rect)]: the pad a point lies in

    _TB = 2000.0

    def index_terms(self):
        """Bucket the pads so pad_at() is quick (call after term_rects/nets)."""
        g = {}
        for k, r in self.term_rects.items():
            net = self.term_nets.get(k, "")
            for bx in range(int(r[0] // self._TB), int(r[2] // self._TB) + 1):
                for by in range(int(r[1] // self._TB), int(r[3] // self._TB) + 1):
                    g.setdefault((bx, by), []).append((net, r))
        self._tgrid = g

    def pad_at(self, x, y, net):
        """The pad of ``net`` that (x, y) lies in, or None."""
        for n, r in self._tgrid.get((int(x // self._TB), int(y // self._TB)), ()):
            if n == net and r[0] - EPS <= x <= r[2] + EPS and r[1] - EPS <= y <= r[3] + EPS:
                return r
        return None

    def add(self, layer: str, net: Optional[str], rect: Rect):
        self._by_layer.setdefault(layer, []).append((net or "", rect))
        return self

    def remove_net(self, layer: str, net: str):
        bl = self._by_layer.get(layer)
        if bl:
            self._by_layer[layer] = [(n, r) for (n, r) in bl if n != (net or "")]

    def layer_blockers(self, layer: str):
        return self._by_layer.get(layer, ())

    def in_bounds(self, p: Pt, hw: float = 0.0) -> bool:
        """``p`` with copper of half-width ``hw`` round it inside the boundary."""
        b = self.boundary
        if b is None or (round(p[0]), round(p[1])) in self.exempt:
            return True
        if self._tgrid:            # anywhere in a pad: the pad sits where it was put
            for _n, r in self._tgrid.get((int(p[0] // self._TB), int(p[1] // self._TB)), ()):
                if r[0] - EPS <= p[0] <= r[2] + EPS and r[1] - EPS <= p[1] <= r[3] + EPS:
                    return True
        return (b[0] + hw - EPS <= p[0] <= b[2] - hw + EPS and
                b[1] + hw - EPS <= p[1] <= b[3] - hw + EPS)

    def segment_clear(self, a: Pt, b: Pt, net: str, margin: float, blockers) -> bool:
        """True when segment a-b keeps >= margin from every foreign blocker in
        ``blockers`` (a pre-scoped (net, rect) list) and stays in the boundary."""
        hw = max(0.0, margin - self.min_space)
        if not self.in_bounds(a, hw) or not self.in_bounds(b, hw):
            return False
        if not _inside(blockers, a, margin) or not _inside(blockers, b, margin):
            return False
        net = net or ""
        ax, ay = a
        bx, by = b
        if self._tgrid:
            # inside a pad of its own net a track adds no copper: check only
            # the part outside (anywhere in the pad, not just its centre -- the
            # 90-degree planner steps out of a pin a grid line at a time)
            ra = self.pad_at(ax, ay, net)
            rb = self.pad_at(bx, by, net)
            if ra is not None or rb is not None:
                t0 = _exit_t(ax, ay, bx, by, ra, hw) if ra is not None else 0.0
                t1 = 1.0 - _exit_t(bx, by, ax, ay, rb, hw) if rb is not None else 1.0
                if t0 >= t1:
                    return True          # all of it inside the pins it joins
                ux, uy = bx - ax, by - ay
                ax, ay, bx, by = ax + ux * t0, ay + uy * t0, ax + ux * t1, ay + uy * t1
        lox, hix = (ax, bx) if ax <= bx else (bx, ax)
        loy, hiy = (ay, by) if ay <= by else (by, ay)
        lox -= margin; loy -= margin; hix += margin; hiy += margin
        near = blockers.near(lox, loy, hix, hiy) if isinstance(blockers, _Scoped) else blockers
        for n, r in near:
            if n == net:
                continue
            if r[2] < lox or r[0] > hix or r[3] < loy or r[1] > hiy:
                continue  # rect can't be within margin of this segment
            if _seg_rect_dist(ax, ay, bx, by, r) < margin - EPS:
                return False
        return True

    def tighten(self, path, net, margin, blockers):
        pts = list(path)
        if len(pts) <= 2:
            return pts
        if _ORTHO and all(abs(pts[i][0] - pts[i - 1][0]) < EPS or abs(pts[i][1] - pts[i - 1][1]) < EPS
                          for i in range(1, len(pts))):
            return _tighten_ortho(self, pts, net, margin, blockers)
        changed = True
        while changed and len(pts) > 2:
            changed = False
            i = 1
            while i < len(pts) - 1:
                if self.segment_clear(pts[i - 1], pts[i + 1], net, margin, blockers):
                    del pts[i]
                    changed = True
                else:
                    i += 1
        return pts


# --------------------------------------------------------------------------- design -> problem

def _pt(q) -> Pt:
    return (float(q["x"]), float(q["y"])) if isinstance(q, dict) else (float(q[0]), float(q[1]))


def _rect_of_pad(p) -> Rect:
    x, y = _pt(p["position"])
    w, h = float(p["widthUm"]), float(p["heightUm"])
    return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)


def _comp_pin_rect(comp, pin) -> Rect:
    cx, cy = _pt(comp["centre"])
    px, py = _pt(pin["position"])
    w, h = float(pin["widthUm"]), float(pin["heightUm"])
    return (cx + px - w / 2, cy + py - h / 2, cx + px + w / 2, cy + py + h / 2)


def _comp_body_rect(comp) -> Rect:
    cx, cy = _pt(comp["centre"])
    bl, bw = float(comp["bodyLengthUm"]), float(comp["bodyWidthUm"])
    return (cx - bl / 2, cy - bw / 2, cx + bl / 2, cy + bw / 2)


def _die_rect(die) -> Rect:
    ox, oy = _pt(die["origin"])
    return (ox, oy, ox + float(die["widthUm"]), oy + float(die["heightUm"]))


class Terminal:
    __slots__ = ("tid", "net", "layer", "rect", "centre")

    def __init__(self, tid, net, layer, rect):
        self.tid = tid
        self.net = net or ""
        self.layer = layer
        self.rect = rect
        self.centre = ((rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0)


class ShapeProblem:
    def __init__(self, design: dict):
        self.design = design
        rt = design.get("routing") or {}
        stack = [l["name"] for l in (design.get("stack") or {}).get("layers", [])
                 if l.get("kind") == "CONDUCTOR"]
        self.layers = [l for l in (rt.get("routingLayers") or stack) if l in stack] or stack[:1]
        rules = (design.get("rules") or {}).get("conductor") or {}
        self.min_line = float(rules.get("minLineUm", 150))
        self.min_space = float(rules.get("minSpaceUm", 150))
        self.edge = float(rules.get("edgeClearanceUm", 250))
        self.net_widths = {k: float(v) for k, v in (rt.get("netWidths") or {}).items()}
        sub = design.get("substrate") or {}
        self.w = float(sub.get("widthUm", 0))
        self.h = float(sub.get("heightUm", 0))
        self.space = ShapeSpace(boundary=(self.edge, self.edge, self.w - self.edge,
                                          self.h - self.edge) if self.w and self.h else None,
                                min_space=self.min_space)
        self.terminals: List[Terminal] = []
        self._build()
        self.space.exempt = {(round(t.centre[0]), round(t.centre[1])) for t in self.terminals}
        self.space.term_rects = {(round(t.centre[0]), round(t.centre[1])): t.rect
                                 for t in self.terminals}
        self.space.term_nets = {(round(t.centre[0]), round(t.centre[1])): (t.net or "")
                                for t in self.terminals}
        self.space.index_terms()
        self.parts = [_die_rect(x) for x in (design.get("dies") or [])]
        for c in design.get("components") or []:
            self.parts.append(_comp_body_rect(c))
        jr = (design.get("rules") or {}).get("jumper") or {}
        self.jmin = float(jr.get("minLengthUm", 500))
        self.jmax = float(jr.get("maxLengthUm", 3000))
        pr = (design.get("rules") or {}).get("pads") or {}
        self.land = float(pr.get("jumperLandUm", max(200.0, self.min_line)))
        self.jumpers_allowed = bool(rt.get("jumpersAllowed", True))
        self.max_jumpers = int(rt.get("maxJumpers", 0))
        # printed-resistor regions a crossover's local dielectric must avoid
        self.resistors = [_comp_body_rect(c) for c in (design.get("components") or [])
                          if str(c.get("kind", "")).upper() == "PRINTED_RESISTOR"]
        self.diel_factor = 3.0            # local dielectric width = factor x trace width
        self.cross_max = float(rt.get("crossoverMaxUm", 8000))
        self.cross_short = float(rt.get("crossoverShortUm", 5000))  # a crossover must be a short hop
        self.res_clear = float(rt.get("resistorClearanceUm", self.min_space))
        # M5: preferred directions (per conductor), 90-degree mode, fixed copper
        dirs = rt.get("directions") or []
        self.dir_axis = {}
        for i, L in enumerate(self.layers):
            if i < len(dirs) and dirs[i] in ("H", "V"):
                self.dir_axis[L] = dirs[i]
        self.against = max(1.0, float(rt.get("againstDirectionCost", 1.0) or 1.0))
        self.orthogonal = bool(rt.get("orthogonal", False))
        self._add_fixed()
        self.groups = self._group()

    def _add_fixed(self):
        # keep-existing fixed copper: foreign (and blank-net) copper is an
        # obstacle; own-net copper the tree may pass through freely
        for ft in (self.design.get("routing") or {}).get("fixedTraces") or []:
            L = ft.get("layer")
            if L not in self.layers:
                continue
            net = ft.get("net") or ""
            hw = float(ft.get("widthUm", self.min_line)) / 2.0
            pts = [_pt(q) for q in ft.get("points") or []]
            for i in range(1, len(pts)):
                (ax, ay), (bx, by) = pts[i - 1], pts[i]
                self.space.add(L, net, (min(ax, bx) - hw, min(ay, by) - hw,
                                        max(ax, bx) + hw, max(ay, by) + hw))

    def width_of(self, net: str) -> float:
        return self.net_widths.get(net, self.min_line)

    def margin(self, net: str) -> float:
        return self.width_of(net) / 2.0 + self.min_space

    def _pad_all(self, net, rect):
        # pads are 1st-conductor features; a 2nd-conductor track runs over them,
        # isolated by the local dielectric, so they block COND1 only
        self.space.add(self.layers[0], net, rect)

    def _build(self):
        d = self.design
        pads = d.get("substratePads") or []
        for die in d.get("dies") or []:
            x0, y0, x1, y1 = _die_rect(die)
            # the attach keep-out is the copper-to-die distance; the query
            # margin already carries min_space, so grow only by the rest
            k = max(0.0, float(die.get("attachKeepoutUm", 0) or 0) - self.min_space)
            # a die sitting on a pad (its back contact / attach pad) does not
            # wall that pad's own net off from it
            under = {p.get("net") for p in pads if p.get("net") and _overlap(_rect_of_pad(p), (x0, y0, x1, y1))}
            own = under.pop() if len(under) == 1 else None
            for L in self.layers:
                self.space.add(L, own, (x0 - k, y0 - k, x1 + k, y1 + k))
        for p in d.get("substratePads") or []:
            rect = _rect_of_pad(p)
            self._pad_all(p.get("net"), rect)
            if p.get("layer") in self.layers:
                self.terminals.append(Terminal(p["id"], p.get("net"), p["layer"], rect))
        for comp in d.get("components") or []:
            body = _comp_body_rect(comp)
            for L in [L for L in (comp.get("obstacleLayers") or []) if L in self.layers]:
                self.space.add(L, None, body)
            for key, nm in (("pinA", "A"), ("pinB", "B")):
                pin = comp.get(key)
                if not pin:
                    continue
                rect = _comp_pin_rect(comp, pin)
                self._pad_all(pin.get("net"), rect)
                layer = pin.get("layer") if pin.get("layer") in self.layers else self.layers[0]
                self.terminals.append(Terminal(f"{comp['refdes']}.{nm}", pin.get("net"), layer, rect))
        for L, rects in ((d.get("routing") or {}).get("keepouts", {}) or {}).items():
            if L in self.layers or L == "COND2":    # COND2: crossover bridges, any mode
                for r in rects or []:
                    if len(r) == 4:
                        self.space.add(L, None, tuple(float(v) for v in r))

    def _group(self) -> Dict[str, List[List[Terminal]]]:
        d = self.design
        parent = {t.tid: t.tid for t in self.terminals}

        def find(a):
            while parent.get(a, a) != a:
                parent[a] = parent.get(parent[a], parent[a])
                a = parent[a]
            return a

        def union(a, b):
            if a in parent and b in parent:
                parent[find(a)] = find(b)

        def eid(e):
            if not e or e.get("kind") == "DIE_PAD":
                return None
            return e.get("pad")
        for wsrc in d.get("wires") or []:
            a, b = eid(wsrc.get("from")), eid(wsrc.get("to"))
            if a and b:
                union(a, b)
        for grp in ((d.get("routing") or {}).get("connected") or []):
            ids = [g for g in grp if g in parent]
            for other in ids[1:]:
                union(ids[0], other)
        nets: Dict[str, Dict[str, List[Terminal]]] = {}
        for t in self.terminals:
            if not t.net or t.net.startswith("~") or t.net.lower().startswith("unconnected"):
                continue
            nets.setdefault(t.net, {}).setdefault(find(t.tid), []).append(t)
        return {net: list(g.values()) for net, g in nets.items()}


# --------------------------------------------------------------------------- planar search

def _plan(space: ShapeSpace, layer: str, net: str, margin: float,
          starts: List[Pt], targets: List[Pt], target_rects: List[Rect],
          blockers, max_nodes: Optional[int] = None, frontier: bool = False):
    """A* over a visibility graph: start at any ``starts`` point, reach any
    ``target_rects``. Nodes = starts + targets + scoped foreign-blocker corners.
    ``blockers`` is the pre-scoped (net, rect) list. Returns the poly-line."""
    if _ORTHO:
        return _plan_ortho(space, layer, net, margin, starts, targets, target_rects,
                           blockers, frontier=frontier)
    m = math.ceil(margin)
    nodes: List[Pt] = []
    seen = {}

    def nid(p):
        k = (round(p[0]), round(p[1]))
        i = seen.get(k)
        if i is None:
            i = len(nodes)
            seen[k] = i
            nodes.append((float(p[0]), float(p[1])))
        return i
    start_ids = [nid(p) for p in starts]
    for p in targets:
        nid(p)
    tx = sum(t[0] for t in targets) / len(targets)
    ty = sum(t[1] for t in targets) / len(targets)
    sx = sum(s[0] for s in starts) / len(starts)
    sy = sum(s[1] for s in starts) / len(starts)
    # candidate corners, ranked by distance to the start->target segment; keep the
    # nearest MAX_NODES so a failed search bounds its work (far corners can't help a
    # single-layer detour here -- jumpers/2nd layer handle what this can't).
    cands = []
    for n, r in blockers:
        if n == net:
            continue
        for c in ((r[0] - m, r[1] - m), (r[2] + m, r[1] - m),
                  (r[2] + m, r[3] + m), (r[0] - m, r[3] + m)):
            if space.in_bounds(c, max(0.0, margin - space.min_space)) and _inside(blockers, c, margin):
                cands.append((_seg_rect_dist(sx, sy, tx, ty, (c[0], c[1], c[0], c[1])), c))
    cands.sort(key=lambda z: z[0])
    cap = MAX_NODES if max_nodes is None else max_nodes
    for _dz, c in cands[:cap]:
        nid(c)

    def in_target(p):
        return any(_pt_in_rect(p[0], p[1], r) for r in target_rects)

    n = len(nodes)
    INF = float("inf")
    g = [INF] * n
    prev = [-1] * n
    pq = []
    for sid in start_ids:
        g[sid] = 0.0
        heapq.heappush(pq, (math.hypot(nodes[sid][0] - tx, nodes[sid][1] - ty), sid))
    closed = [False] * n
    best_u, best_d = -1, INF

    def trace(u):
        path = [nodes[u]]
        while prev[u] != -1:
            u = prev[u]
            path.append(nodes[u])
        path.reverse()
        return path
    while pq:
        _f, u = heapq.heappop(pq)
        if closed[u]:
            continue
        closed[u] = True
        pu = nodes[u]
        if in_target(pu) and u not in start_ids:
            path = trace(u)
            return (path, True) if frontier else path
        du = math.hypot(pu[0] - tx, pu[1] - ty)
        if u not in start_ids and du < best_d:
            best_d, best_u = du, u
        gu = g[u]
        for v in range(n):
            if v == u or closed[v]:
                continue
            pv = nodes[v]
            d = math.hypot(pu[0] - pv[0], pu[1] - pv[1])
            _dir = _DIRS.get(layer)
            if _dir is not None and _dir[1] > 1.0:
                across = abs(pu[1] - pv[1]) if _dir[0] == "H" else abs(pu[0] - pv[0])
                d += (_dir[1] - 1.0) * across          # cost to route against the grain
            if _PEN is not None:
                d += _PEN(pu, pv)
            if gu + d >= g[v]:
                continue
            if not space.segment_clear(pu, pv, net, margin, blockers):
                continue
            g[v] = gu + d
            prev[v] = u
            heapq.heappush(pq, (g[v] + math.hypot(pv[0] - tx, pv[1] - ty), v))
    if frontier:
        return (trace(best_u), False) if best_u != -1 else (None, False)
    return None


def _in_any_pad(space, p) -> bool:
    """``p`` lies inside some terminal pad (a bend there would land a track
    on the pad off its centre line)."""
    for r in space.term_rects.values():
        if r[0] + EPS < p[0] < r[2] - EPS and r[1] + EPS < p[1] < r[3] - EPS:
            return True
    return False


def _plan_ortho(space, layer, net, margin, starts, targets, target_rects, blockers,
                frontier=False):
    """90-degree A*: the nodes are the grid of every x and y a track can
    usefully turn at (each foreign blocker's edges offset by ``margin``, plus
    the start and target centres), the moves horizontal or vertical only, and
    each bend costs BEND_FACTOR track widths -- so the path has few jogs. A
    track ends at a pad's centre and may not bend inside any pad, so it always
    enters and leaves a pad straight along its centre line (no landing on a
    corner). Same returns as _plan."""
    hw = max(0.0, margin - space.min_space)
    bend = BEND_FACTOR * max(2.0 * hw, 1.0)
    bb = getattr(blockers, "bb", None)
    xs, ys = set(), set()

    def addx(v):
        xs.add(round(v, 1))

    def addy(v):
        ys.add(round(v, 1))
    for p in list(starts) + list(targets):
        addx(p[0]); addy(p[1])
    for n, r in blockers:
        if n == (net or ""):
            continue
        for v in (r[0] - margin, r[2] + margin):
            addx(v)
        for v in (r[1] - margin, r[3] + margin):
            addy(v)
    # every pad, own net included: a track leaves a pin through the edge away
    # from its part's body and turns just outside it -- it needs lines there
    for r in space.term_rects.values():
        if bb is not None and (r[2] < bb[0] or r[0] > bb[2] or r[3] < bb[1] or r[1] > bb[3]):
            continue
        for v in (r[0] - margin, r[2] + margin, (r[0] + r[2]) / 2.0):
            addx(v)
        for v in (r[1] - margin, r[3] + margin, (r[1] + r[3]) / 2.0):
            addy(v)
    if bb is not None:
        xs = {v for v in xs if bb[0] + margin - EPS <= v <= bb[2] - margin + EPS}
        ys = {v for v in ys if bb[1] + margin - EPS <= v <= bb[3] - margin + EPS}
        for p in list(starts) + list(targets):
            xs.add(round(p[0], 1)); ys.add(round(p[1], 1))
    X = sorted(xs)
    Y = sorted(ys)
    xi = {v: i for i, v in enumerate(X)}
    yi = {v: i for i, v in enumerate(Y)}
    tset = {(xi[round(t[0], 1)], yi[round(t[1], 1)]) for t in targets}
    # grid nodes strictly inside a pad (no bend there), worked out once
    import bisect
    padcells = set()
    for r in space.term_rects.values():
        a0, a1 = bisect.bisect_right(X, r[0] + EPS), bisect.bisect_left(X, r[2] - EPS)
        b0, b1 = bisect.bisect_right(Y, r[1] + EPS), bisect.bisect_left(Y, r[3] - EPS)
        for ix in range(a0, a1):
            for iy in range(b0, b1):
                padcells.add((ix, iy))
    tpts = list(targets)
    tx = sum(t[0] for t in targets) / len(targets)
    ty = sum(t[1] for t in targets) / len(targets)

    def h(ix, iy):
        x, y = X[ix], Y[iy]
        return min(abs(x - a) + abs(y - b) for a, b in tpts)
    D = ((1, 0), (-1, 0), (0, 1), (0, -1))
    edge_ok = {}
    _dir = _DIRS.get(layer)
    g = {}
    prev = {}
    pq = []
    sid = set()
    for p in starts:
        k = (xi[round(p[0], 1)], yi[round(p[1], 1)], -1)
        sid.add(k[:2])
        g[k] = 0.0
        prev[k] = None
        heapq.heappush(pq, (h(k[0], k[1]), 0.0, k))
    best, best_d = None, float("inf")
    closed = set()
    n_exp = 0

    def trace(k):
        out = []
        while k is not None:
            p = (X[k[0]], Y[k[1]])
            if not out or out[-1] != p:
                out.append(p)
            k = prev[k]
        out.reverse()
        # drop collinear points
        res = [out[0]]
        for i in range(1, len(out) - 1):
            a, b, c = res[-1], out[i], out[i + 1]
            if (abs(a[0] - b[0]) < EPS and abs(b[0] - c[0]) < EPS) or \
                    (abs(a[1] - b[1]) < EPS and abs(b[1] - c[1]) < EPS):
                continue
            res.append(b)
        if len(out) > 1:
            res.append(out[-1])
        return res
    while pq:
        _f, gk, k = heapq.heappop(pq)
        if k in closed:
            continue
        closed.add(k)
        n_exp += 1
        ix, iy, dk = k
        if (ix, iy) in tset and (ix, iy) not in sid:
            path = trace(k)
            return (path, True) if frontier else path
        if (ix, iy) not in sid:
            dd = math.hypot(X[ix] - tx, Y[iy] - ty)
            if dd < best_d:
                best_d, best = dd, k
        if n_exp > (_EXPAND_CAP or ORTHO_EXPAND):
            break
        p = (X[ix], Y[iy])
        turn_ok = None
        for di, (dx, dy) in enumerate(D):
            if dk != -1 and di != dk and (dx, dy) == (-D[dk][0], -D[dk][1]):
                continue                      # no U-turns
            jx, jy = ix + dx, iy + dy
            if not (0 <= jx < len(X) and 0 <= jy < len(Y)):
                continue
            bent = dk != -1 and di != dk
            if bent:
                if turn_ok is None:
                    turn_ok = (ix, iy) not in padcells
                if not turn_ok:
                    continue
            ek = (min(ix, jx), min(iy, jy), dx != 0)
            ok = edge_ok.get(ek)
            q = (X[jx], Y[jy])
            if ok is None:
                ok = space.segment_clear(p, q, net, margin, blockers)
                edge_ok[ek] = ok
            if not ok:
                continue
            L = abs(q[0] - p[0]) + abs(q[1] - p[1])
            c = L + (bend if bent else 0.0)
            if _dir is not None and _dir[1] > 1.0 and ((_dir[0] == "H") != (dx != 0)):
                c += (_dir[1] - 1.0) * L
            if _PEN is not None:
                c += _PEN(p, q)
            nk = (jx, jy, di)
            ng = gk + c
            if ng < g.get(nk, float("inf")):
                g[nk] = ng
                prev[nk] = k
                heapq.heappush(pq, (ng + h(jx, jy), ng, nk))
    if frontier:
        return (trace(best), False) if best is not None else (None, False)
    return None


def _tighten_ortho(space, path, net, margin, blockers):
    """Rubber-band for 90-degree tracks: replace a run of legs by one L where
    both legs are clear and the corner is not on a pad; then drop collinear
    points. Keeps every segment axis-aligned."""
    pts = list(path)
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts) - 3):
            for j in range(len(pts) - 1, i + 2, -1):
                a, b = pts[i], pts[j]
                for c in ((a[0], b[1]), (b[0], a[1])):
                    if _in_any_pad(space, c) and c not in (a, b):
                        continue
                    if space.segment_clear(a, c, net, margin, blockers) and \
                            space.segment_clear(c, b, net, margin, blockers):
                        pts = pts[:i + 1] + [c] + pts[j:]
                        changed = True
                        break
                if changed:
                    break
            if changed:
                break
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = out[-1], pts[i], pts[i + 1]
        if b == a or (abs(a[0] - b[0]) < EPS and abs(b[0] - c[0]) < EPS) or \
                (abs(a[1] - b[1]) < EPS and abs(b[1] - c[1]) < EPS):
            continue
        out.append(b)
    if len(pts) > 1:
        out.append(pts[-1])
    return out


def _overlap(a: Rect, b: Rect) -> bool:
    """Overlapping or touching (a ring of attach-pad strips abuts its die)."""
    return a[0] <= b[2] + EPS and b[0] <= a[2] + EPS and a[1] <= b[3] + EPS and b[1] <= a[3] + EPS


def _exit_t(ax, ay, bx, by, r, hw) -> float:
    """Fraction of segment a->b (a inside pad ``r``) that a track of
    half-width ``hw`` covers before its copper leaves the pad: the exit from
    ``r`` inset by hw. 0 when the pad is narrower than the track."""
    x0, y0, x1, y1 = r[0] + hw, r[1] + hw, r[2] - hw, r[3] - hw
    if x0 > x1:                       # pad narrower than the track: its centre line
        x0 = x1 = (r[0] + r[2]) / 2.0
    if y0 > y1:
        y0 = y1 = (r[1] + r[3]) / 2.0
    if not (x0 - EPS <= ax <= x1 + EPS and y0 - EPS <= ay <= y1 + EPS):
        return 0.0
    ux, uy = bx - ax, by - ay
    t = 1.0
    if ux > 0:
        t = min(t, (x1 - ax) / ux)
    elif ux < 0:
        t = min(t, (x0 - ax) / ux)
    if uy > 0:
        t = min(t, (y1 - ay) / uy)
    elif uy < 0:
        t = min(t, (y0 - ay) / uy)
    return max(0.0, t)


class _Scoped(list):
    """A scoped blocker list that knows its box: blockers outside ``bb`` were
    left out, so a query point must keep its clearance inside ``bb`` or the
    answer would be wrong (see _inside). Bucketed on first query (rebuilt if
    the list has grown) so a clearance check looks only at what is near."""
    bb = None
    _CELL = 1000.0
    _grid = None
    _glen = -1

    def near(self, lox, loy, hix, hiy):
        if self._glen != len(self):
            g = {}
            c = self._CELL
            for i, (_n, r) in enumerate(self):
                for gx in range(int(r[0] // c), int(r[2] // c) + 1):
                    for gy in range(int(r[1] // c), int(r[3] // c) + 1):
                        g.setdefault((gx, gy), []).append(i)
            self._grid, self._glen = g, len(self)
        c = self._CELL
        out = set()
        g = self._grid
        for gx in range(int(lox // c), int(hix // c) + 1):
            for gy in range(int(loy // c), int(hiy // c) + 1):
                v = g.get((gx, gy))
                if v:
                    out.update(v)
        return [self[i] for i in out]


def _inside(blockers, p, clr) -> bool:
    """``p`` with clearance ``clr`` round it lies inside the scope box (always
    True for an unscoped list). Points inside it can only come within ``clr``
    of blockers that overlap the box -- exactly the ones the scope kept."""
    bb = getattr(blockers, "bb", None)
    return bb is None or (bb[0] + clr - EPS <= p[0] <= bb[2] - clr + EPS and
                          bb[1] + clr - EPS <= p[1] <= bb[3] - clr + EPS)


def _scope(space, layer, start_rects, target_rects, pad):
    """Blockers whose rect lies within the connection's bounding box (grown by
    pad), so the search works locally rather than over the whole board."""
    xs = [r[0] for r in start_rects + target_rects] + [r[2] for r in start_rects + target_rects]
    ys = [r[1] for r in start_rects + target_rects] + [r[3] for r in start_rects + target_rects]
    bb = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    out = _Scoped()
    out.bb = bb
    for n, r in space.layer_blockers(layer):
        if r[2] >= bb[0] and r[0] <= bb[2] and r[3] >= bb[1] and r[1] <= bb[3]:
            out.append((n, r))
    return out


def _point_clear(space, x, y, clearance, net, blockers) -> bool:
    """True when point (x,y) keeps >= clearance from every foreign blocker."""
    if not space.in_bounds((x, y), max(0.0, clearance - space.min_space)):
        return False
    if not _inside(blockers, (x, y), clearance):
        return False
    net = net or ""
    near = blockers.near(x - clearance, y - clearance, x + clearance, y + clearance) \
        if isinstance(blockers, _Scoped) else blockers
    for n, r in near:
        if n == net:
            continue
        if _seg_rect_dist(x, y, x, y, r) < clearance - EPS:
            return False
    return True


def _seg_hits_box(a, b, box) -> bool:
    """True when segment a-b touches/crosses axis-aligned box."""
    return _seg_rect_dist(a[0], a[1], b[0], b[1], box) <= EPS


def _bridge_route(P, net, a, b, w):
    """Route the COND2 bridge a->b (the printed jumper) as a poly-line that may
    dog-leg around printed resistors. Resistor regions are inflated so the local
    dielectric strip (diel_factor x width) stays res_clear from them; other COND2
    copper is inflated for spacing. Returns the bridge poly-line or None."""
    dhw = P.diel_factor * w / 2.0
    mB = w / 2.0
    raw2 = _scope(P.space, "COND2", [(a[0], a[1], a[0], a[1])],
                  [(b[0], b[1], b[0], b[1])], 4000 + dhw)
    blk = _Scoped()
    # COND2 copper is grown by min_space below, so shrink the box to match
    blk.bb = (raw2.bb[0] + P.min_space, raw2.bb[1] + P.min_space,
              raw2.bb[2] - P.min_space, raw2.bb[3] - P.min_space)
    for nn, r in raw2:
        blk.append((nn, (r[0] - P.min_space, r[1] - P.min_space,
                         r[2] + P.min_space, r[3] + P.min_space)))
    inf = max(0.0, dhw + P.res_clear - mB)
    for R in P.resistors:
        blk.append(("~RES", (R[0] - inf, R[1] - inf, R[2] + inf, R[3] + inf)))
    # a COND2 track / its dielectric cannot sit on a proud part (die or component
    # body) -- avoid them in every mode (in 1-conductor they aren't on COND2)
    infp = max(0.0, dhw - mB)
    # pads stay bare (solder, bonds): the strip keeps min_space off every pad
    # of any net -- the bridge's own ends sit on its via lands, not on pads
    infpad = max(0.0, dhw + P.min_space - mB)
    # (own-net pads: the via may sit by one with the bridge heading away --
    # the exact strip check below judges those)
    for kk, r in P.space.term_rects.items():
        if P.space.term_nets.get(kk) == (net or ""):
            continue
        if r[2] >= raw2.bb[0] and r[0] <= raw2.bb[2] and r[3] >= raw2.bb[1] and r[1] <= raw2.bb[3]:
            blk.append(("~PAD", (r[0] - infpad, r[1] - infpad, r[2] + infpad, r[3] + infpad)))
    pbb = (min(a[0], b[0]) - 4000, min(a[1], b[1]) - 4000,
           max(a[0], b[0]) + 4000, max(a[1], b[1]) + 4000)
    for box in P.parts:
        if box[2] >= pbb[0] and box[0] <= pbb[2] and box[3] >= pbb[1] and box[1] <= pbb[3]:
            blk.append(("~PART", (box[0] - infp, box[1] - infp, box[2] + infp, box[3] + infp)))
    tgt = (b[0] - 1.0, b[1] - 1.0, b[0] + 1.0, b[1] + 1.0)
    path = _plan(P.space, "COND2", net, mB, [a], [b], [tgt], blk, max_nodes=60)
    if path is None:
        return None
    path = P.space.tighten(path, net, mB, blk)
    return path if _strip_leaves_pads_bare(P, path, w) else None


def _polylen(pts):
    return sum(math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
               for i in range(1, len(pts)))


def _polyline_points(pts, step):
    """Walk a poly-line, yielding (point, is_vertex) every ~step along it."""
    out = [(pts[0], True)]
    for i in range(1, len(pts)):
        (x0, y0), (x1, y1) = pts[i - 1], pts[i]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg < 1:
            continue
        k = max(1, int(seg // step))
        for j in range(1, k):
            f = j / k
            out.append(((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f), False))
        out.append(((x1, y1), True))
    return out


def _shorten_crossover(P, layer, net, margin, starts, grp, blockers, bridge, a0, b0):
    """Keep COND2 only across the foreign-copper wall the bridge crosses: route
    COND1 from the tree up to the wall and from the wall to the target, each side
    independently (falling back to an up-via on the terminal where a side is
    hemmed). Returns (pathA, pathB, short_bridge, A, B) or None."""
    hl = P.land / 2.0
    vclr = hl + P.min_space
    samp = _polyline_points(bridge, max(100.0, P.min_line / 2.0))
    clear = [_point_clear(P.space, pt[0], pt[1], vclr, net, blockers) and _via_site_ok(P, net, *pt)
             for pt, _v in samp]
    blocked = [i for i, c in enumerate(clear) if not c]
    if not blocked:
        return None                                   # crosses no COND1 copper
    ws, we = blocked[0], blocked[-1]

    # tree side: latest clear sample before the wall that the tree can reach
    A, ia, pathA = a0, 0, []
    tried = 0
    for i in range(ws - 1, -1, -1):
        if not clear[i]:
            continue
        tried += 1
        if tried > 5:
            break
        pt = samp[i][0]
        r = (pt[0] - hl, pt[1] - hl, pt[0] + hl, pt[1] + hl)
        pth = _plan(P.space, layer, net, margin, starts, [pt], [r], blockers)
        if pth is not None:
            A, ia, pathA = pt, i, P.space.tighten(pth, net, margin, blockers)
            break

    # target side: earliest clear sample after the wall that reaches the target
    B, ib, pathB = b0, len(samp) - 1, []
    tried = 0
    for i in range(we + 1, len(samp)):
        if not clear[i]:
            continue
        tried += 1
        if tried > 5:
            break
        pt = samp[i][0]
        pth = _plan(P.space, layer, net, margin, [pt], [t.centre for t in grp],
                    [t.rect for t in grp], blockers)
        if pth is not None:
            B, ib, pathB = pt, i, P.space.tighten(pth, net, margin, blockers)
            break

    if ib <= ia:
        return None
    short = [A] + [samp[k][0] for k in range(ia + 1, ib) if samp[k][1]] + [B]
    short = [q for i, q in enumerate(short) if i == 0 or q != short[i - 1]]
    if len(short) < 2:
        return None
    return pathA, pathB, short, A, B



def _via_escape(P, layer, net, margin, term, blockers, toward):
    """A via point near terminal ``term``, clear of foreign COND1 copper, parts
    and the COND2 edge band, reachable from ``term`` on COND1. Tries a fan of
    directions (not just toward the target) so a pin whose only clear side faces
    away still escapes. Returns (via_xy, cond1_path term->via) or None; a clear
    terminal vias in place."""
    hl = P.land / 2.0
    clr = hl + P.min_space
    bb = (term[0] - 6000, term[1] - 6000, term[0] + 6000, term[1] + 6000)
    c2 = [(n, r) for n, r in P.space.layer_blockers("COND2")
          if r[2] >= bb[0] and r[0] <= bb[2] and r[3] >= bb[1] and r[1] <= bb[3]]

    pads = [r for r in P.space.term_rects.values()
            if r[2] >= bb[0] and r[0] <= bb[2] and r[3] >= bb[1] and r[1] <= bb[3]]
    dclr = clr      # the via land off every pad; the strip's own pad clearance is _bridge_route's

    def via_ok(x, y):
        if not _point_clear(P.space, x, y, clr, net, blockers):
            return False
        if not _via_site_ok(P, net, x, y):
            return False
        for n, r in c2:
            if n == net:
                continue
            if _seg_rect_dist(x, y, x, y, r) < clr - EPS:
                return False
        return True
    if via_ok(term[0], term[1]):
        return term, [term]
    base = math.atan2(toward[1] - term[1], toward[0] - term[0])
    step = max(150.0, P.min_line / 2.0)
    best = None
    for k in range(0, 8):           # fan: toward target first, then round to the back
        off = ((k + 1) // 2) * (math.pi / 4.0) * (1 if k % 2 else -1)
        ux, uy = math.cos(base + off), math.sin(base + off)
        for i in range(1, 40):
            x, y = term[0] + ux * step * i, term[1] + uy * step * i
            if i * step > 2 * P.cross_short:
                break
            if not via_ok(x, y):
                continue
            rect = (x - hl, y - hl, x + hl, y + hl)
            global _EXPAND_CAP
            _EXPAND_CAP = 1500          # a stub to a nearby via: a small search
            # a short stub: plan it in a small local scope (cheap to set up)
            loc = _scope(P.space, layer, [(term[0], term[1], term[0], term[1])], [rect],
                         1500.0 + margin)
            try:
                path = _plan(P.space, layer, net, margin, [term], [(x, y)], [rect], loc)
            finally:
                _EXPAND_CAP = None
            if path is not None:
                path = P.space.tighten(path, net, margin, loc)
                L = _polylen(path) if len(path) >= 2 else 0.0
                if best is None or L < best[0]:
                    best = (L, (x, y), path)
                break                       # shortest landing on this ray
        if best is not None and best[0] <= step * 2:
            break                           # a via right next to the pin -- take it
    return (best[1], best[2]) if best else None


def _try_crossover(P, layer, net, margin, starts, grp, blockers, cap=None):
    """Route a connection onto the second conductor: COND1 carries it to a via,
    a COND2 track crosses only the congested middle (its local dielectric routed
    clear of resistors, dog-legging if need be), then COND1 down to the target.
    Vias escape resistor pins and the edge band. Returns a payload dict,
    'resistor' when the second conductor can't be reached, or None."""
    w = P.width_of(net)
    hl = P.land / 2.0
    pairs = sorted(((math.hypot(s[0] - t.centre[0], s[1] - t.centre[1]), s, t.centre)
                    for s in starts for t in grp), key=lambda z: z[0])
    res_block = False
    for _d, a0, b0 in pairs[:4]:
        ea = _via_escape(P, layer, net, margin, a0, blockers, b0)
        if ea is None:
            continue
        A, stubA = ea
        eb = _via_escape(P, layer, net, margin, b0, blockers, a0)
        if eb is None:
            continue
        B, stubB = eb
        track = _bridge_route(P, net, A, B, w)
        if track is None:
            res_block = True
            continue
        # shorten: COND1 from the tree up to the wall, COND2 only across it, COND1
        # down to the target -- so the second conductor (and its dielectric) is
        # used only where COND1 can't reach
        sh = _shorten_crossover(P, layer, net, margin, starts, grp, blockers, track, A, B)
        if sh is not None:
            pA, pB, sbridge, F1, F2 = sh
            return {"a": F1, "b": F2, "stubA": pA or stubA, "stubB": pB or stubB,
                    "bridge": sbridge,
                    "landA": (F1[0] - hl, F1[1] - hl, F1[0] + hl, F1[1] + hl),
                    "landB": (F2[0] - hl, F2[1] - hl, F2[0] + hl, F2[1] + hl)}
        return {"a": A, "b": B, "stubA": stubA, "stubB": stubB, "bridge": track,
                "landA": (A[0] - hl, A[1] - hl, A[0] + hl, A[1] + hl),
                "landB": (B[0] - hl, B[1] - hl, B[0] + hl, B[1] + hl)}
    return "resistor" if res_block else None




def _try_jumper_pair(P, layer, net, margin, starts, grp, blockers, a0, b0):
    """Hop one foreign-copper wall on the straight line a0->b0: land just before
    the wall (tree side) and just after (target side), bond across it. Cheap and
    bounded -- no reachability sweeps. Returns the jumper payload or None."""
    ux, uy = b0[0] - a0[0], b0[1] - a0[1]
    dd = math.hypot(ux, uy) or 1.0
    ux, uy = ux / dd, uy / dd
    landclr = P.land / 2.0 + P.min_space
    step = max(50.0, P.min_line / 2.0)
    n = int(dd // step)
    state = [_point_clear(P.space, a0[0] + ux * step * i, a0[1] + uy * step * i,
                          landclr, net, blockers) for i in range(n + 1)]
    try:
        ib = next(i for i, c in enumerate(state) if not c)      # wall enters
    except StopIteration:
        return None
    io = next((i for i in range(ib, len(state)) if state[i]), None)   # wall exits
    if io is None:
        return None
    ia = ib - 1
    while ia > 0 and not state[ia]:
        ia -= 1
    if ia < 0 or not state[ia]:
        return None                                             # source side hemmed (needs 2nd layer)
    ax, ay = a0[0] + ux * step * ia, a0[1] + uy * step * ia
    bx, by = a0[0] + ux * step * io, a0[1] + uy * step * io
    blen = math.hypot(bx - ax, by - ay)
    if blen < P.jmin or blen > P.jmax:
        return None
    if any(_seg_hits_box((ax, ay), (bx, by), box) for box in P.parts):
        return None
    hl = P.land / 2.0
    landA = (ax - hl, ay - hl, ax + hl, ay + hl)
    landB = (bx - hl, by - hl, bx + hl, by + hl)
    pathA = _plan(P.space, layer, net, margin, starts, [(ax, ay)], [landA], blockers)
    pathB = _plan(P.space, layer, net, margin, [(bx, by)], [t.centre for t in grp],
                  [t.rect for t in grp], blockers)
    if pathA is None or pathB is None:
        return None
    pathA = P.space.tighten(pathA, net, margin, blockers)
    pathB = P.space.tighten(pathB, net, margin, blockers)
    return landA, landB, pathA, pathB, (ax, ay), (bx, by)


def _try_jumper(P, layer, net, margin, starts, grp, blockers):
    """A one-wall hop on the straight line between the nearest tree/target
    terminals; failing that, the landing search (_jumper_search)."""
    pairs = sorted(((math.hypot(s[0] - t.centre[0], s[1] - t.centre[1]), s, t.centre)
                    for s in starts for t in grp), key=lambda z: z[0])
    for _d, a0, b0 in pairs[:4]:
        r = _try_jumper_pair(P, layer, net, margin, starts, grp, blockers, a0, b0)
        if r is not None and _bond_ok(P, r[4], r[5]):
            return r
    return _jumper_search(P, layer, net, margin, starts, grp, blockers)


def _bond_segments(P):
    """Every bond wire already in the design (die bonds and jumpers), as
    substrate-local segments -- a new jumper must not cross any of them."""
    d = P.design
    pads = {p["id"]: _pt(p["position"]) for p in d.get("substratePads") or []}
    dies = {x["refdes"]: x for x in d.get("dies") or []}

    def end(e):
        if not e:
            return None
        if e.get("kind") == "DIE_PAD":
            die = dies.get(e.get("die"))
            if die is None:
                return None
            q = next((q for q in die.get("pads") or [] if str(q.get("id")) == str(e.get("pad"))), None)
            if q is None:
                return None
            ox, oy = _pt(die["origin"])
            px, py = _pt(q["position"])
            return (ox + px, oy + py)
        return pads.get(e.get("pad"))
    out = []
    for w in d.get("wires") or []:
        a, b = end(w.get("from")), end(w.get("to"))
        if a is not None and b is not None and a != b:
            out.append((a, b))
    return out


def _bond_ok(P, a, b) -> bool:
    """Jumper a->b: within length, not over a part, crossing no other bond."""
    blen = math.hypot(b[0] - a[0], b[1] - a[1])
    if blen < P.jmin - EPS or blen > P.jmax + EPS:
        return False
    if any(_seg_hits_box(a, b, box) for box in P.parts):
        return False
    gap = max(P.min_space, 50.0)
    for c, e in _bond_segments(P):
        if _seg_seg_dist(a[0], a[1], b[0], b[1], c[0], c[1], e[0], e[1]) < gap:
            return False
    return True


def _jumper_search(P, layer, net, margin, starts, grp, blockers, tries: int = 24):
    """Landing search (spec 6.6): hop any foreign-copper wall in scope, not just
    the one on the straight line. At stations along each foreign rect, walk out
    across its short side to the first clear land on each side (so a bus of
    parallel tracks is hopped in one bond), stretch to the minimum length,
    keep the legal bonds, rank them by tree->land + land->target distance and
    verify the best few with the planner. Returns the jumper payload or None."""
    own = net or ""
    landclr = P.land / 2.0 + P.min_space
    step = max(P.land, 250.0)
    walk = max(50.0, P.min_line / 2.0)
    targets = [t.centre for t in grp]

    def near(p, pts):
        return min(math.hypot(p[0] - q[0], p[1] - q[1]) for q in pts)

    def clear(x, y):
        return _point_clear(P.space, x, y, landclr, net, blockers)

    def out_to_clear(x, y, ux, uy, start, limit):
        t = start
        while t <= limit:
            px, py = x + ux * t, y + uy * t
            if clear(px, py):
                return px, py, t
            t += walk
        return None

    cands = {}
    for n, r in blockers:
        if not n or n == own:
            continue
        x0, y0, x1, y1 = r
        horiz = (x1 - x0) >= (y1 - y0)
        lo, hi = (x0, x1) if horiz else (y0, y1)
        k = max(1, int((hi - lo) // step))
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        half = ((y1 - y0) if horiz else (x1 - x0)) / 2.0
        for i in range(k + 1):
            u = lo + (hi - lo) * i / k
            sx, sy = (u, cy) if horiz else (cx, u)
            ux, uy = (0.0, 1.0) if horiz else (1.0, 0.0)
            pa = out_to_clear(sx, sy, -ux, -uy, half, P.jmax)
            pb = out_to_clear(sx, sy, ux, uy, half, P.jmax)
            if pa is None or pb is None:
                continue
            ta, tb = pa[2], pb[2]
            if ta + tb < P.jmin:                       # stretch to the minimum length
                need = (P.jmin - ta - tb) / 2.0 + EPS
                ta, tb = ta + need, tb + need
                if not clear(sx - ux * ta, sy - uy * ta) or not clear(sx + ux * tb, sy + uy * tb):
                    continue
            A = (sx - ux * ta, sy - uy * ta)
            B = (sx + ux * tb, sy + uy * tb)
            for a, b in ((A, B), (B, A)):
                key = (round(a[0]), round(a[1]), round(b[0]), round(b[1]))
                if key in cands:
                    continue
                cands[key] = (near(a, starts) + near(b, targets) + math.hypot(b[0] - a[0], b[1] - a[1]),
                              a, b)
    ranked = sorted(cands.values(), key=lambda z: (z[0], z[1], z[2]))
    hl = P.land / 2.0
    failA, failB = set(), set()
    n_try = 0
    for _score, a, b in ranked:
        if n_try >= tries:
            break
        ka, kb = (round(a[0]), round(a[1])), (round(b[0]), round(b[1]))
        if ka in failA or kb in failB or not _bond_ok(P, a, b):
            continue
        n_try += 1
        landA = (a[0] - hl, a[1] - hl, a[0] + hl, a[1] + hl)
        landB = (b[0] - hl, b[1] - hl, b[0] + hl, b[1] + hl)
        pathA = _plan(P.space, layer, net, margin, starts, [a], [landA], blockers)
        if pathA is None:
            failA.add(ka)
            continue
        pathB = _plan(P.space, layer, net, margin, [b], targets, [t.rect for t in grp], blockers)
        if pathB is None:
            failB.add(kb)
            continue
        pathA = P.space.tighten(pathA, net, margin, blockers)
        pathB = P.space.tighten(pathB, net, margin, blockers)
        return landA, landB, pathA, pathB, a, b
    return None


def _short_hop(P, layer, net, margin, F, grp, blockers, tries: int = 12):
    """From frontier point F, a short COND2 crossover over the wall ahead to a
    clear COND1 landing G closer to the target. Landings are looked for on a
    fan of rays (straight at the target, then up to 90 degrees either side --
    the straight line can run into a pad), ranked by bridge length plus what
    is left to go, and the best few bridged. Dog-legs around resistors;
    bridge capped at cross_short. Returns (G, bridge) or None."""
    w = P.width_of(net)
    hl = P.land / 2.0
    vclr = hl + P.min_space
    b0 = min((t.centre for t in grp), key=lambda c: math.hypot(c[0] - F[0], c[1] - F[1]))
    base = math.atan2(b0[1] - F[1], b0[0] - F[0])
    step = max(150.0, P.min_line / 2.0)
    dF = math.hypot(b0[0] - F[0], b0[1] - F[1])
    cands = []
    for deg in (0, 30, -30, 60, -60, 90, -90):
        ux, uy = math.cos(base + math.radians(deg)), math.sin(base + math.radians(deg))
        i = 1
        while i * step <= P.cross_short:
            gx, gy = F[0] + ux * step * i, F[1] + uy * step * i
            i += 1
            rest = math.hypot(b0[0] - gx, b0[1] - gy)
            # across the wall is the progress: a landing may be a little
            # further off than F (beside the target pad, say); ranking prefers
            # nearer ones and multihop's hop cap stops any wandering
            if rest >= dF + 4 * step:
                continue
            if not _point_clear(P.space, gx, gy, vclr, net, blockers) or \
                    not _via_site_ok(P, net, gx, gy):
                continue
            cands.append((step * (i - 1) + rest, abs(deg), gx, gy))
            break                                  # the first landing on this ray
    cands.sort()
    for _c, _deg, gx, gy in cands[:tries]:
        bridge = _bridge_route(P, net, F, (gx, gy), w)
        if bridge is None or _polylen(bridge) > P.cross_short:
            continue
        return (gx, gy), bridge
    return None


def _via_site_ok(P, net, x, y) -> bool:
    """A crossover via at (x, y) leaves every pad (any net) bare: its local
    dielectric strip, diel_factor x the width wide, starts at the land's edge,
    so the via keeps that half-width plus spacing from every pad."""
    dclr = P.diel_factor * P.width_of(net) / 2.0 + P.min_space
    lclr = P.land / 2.0 + P.min_space
    own = net or ""
    for k, r in P.space.term_rects.items():
        c = lclr if P.space.term_nets.get(k) == own else dclr
        if r[0] - c < x < r[2] + c and r[1] - c < y < r[3] + c and \
                _seg_rect_dist(x, y, x, y, r) < c - EPS:
            return False
    return True


def _trim_ends(pts, d):
    """``pts`` cut back by ``d`` along its length at each end ([] if too short)."""
    def cut(seq):
        left = d
        for i in range(1, len(seq)):
            (x0, y0), (x1, y1) = seq[i - 1], seq[i]
            sl = math.hypot(x1 - x0, y1 - y0)
            if sl > left:
                f = left / sl
                return [(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)] + list(seq[i:])
            left -= sl
        return []
    total = sum(math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
                for i in range(1, len(pts)))
    if len(pts) < 2 or total <= 2 * d:
        return []
    return cut(cut(list(pts))[::-1])[::-1]


def _strip_leaves_pads_bare(P, bridge, w) -> bool:
    """The local dielectric strip apply_routed will print for ``bridge`` (flat
    ends at the via lands, diel_factor x w wide) keeps min_space off every pad."""
    try:
        from shapely.geometry import LineString, box
    except ImportError:
        return True
    pts = _trim_ends(bridge, P.land / 2.0)
    if len(pts) < 2:
        return True
    strip = LineString(pts).buffer(P.diel_factor * w / 2.0, cap_style=2, join_style=2)
    bx = strip.bounds
    for r in P.space.term_rects.values():
        if r[2] < bx[0] - P.min_space or r[0] > bx[2] + P.min_space or \
                r[3] < bx[1] - P.min_space or r[1] > bx[3] + P.min_space:
            continue
        if strip.distance(box(*r)) < P.min_space - EPS:
            return False
    return True


def _backoff_land(P, net, path, blockers):
    """``path`` cut back to its last point where a crossover land fits: the
    frontier is clear for a track end, but a land can be wider than the track.
    Returns the shortened path ([start] at worst) or None if even the start
    can't take one."""
    clr = P.land / 2.0 + P.min_space
    step = 50.0
    pts = list(path)
    while len(pts) >= 2:
        (ax, ay), (bx, by) = pts[-2], pts[-1]
        L = math.hypot(bx - ax, by - ay)
        t = L
        while t > EPS:
            x, y = ax + (bx - ax) * t / L, ay + (by - ay) * t / L
            if _point_clear(P.space, x, y, clr, net, blockers) and _via_site_ok(P, net, x, y):
                return pts[:-1] + [(x, y)] if t < L else pts
            t -= step
        pts.pop()
    if pts and _point_clear(P.space, pts[0][0], pts[0][1], clr, net, blockers) \
            and _via_site_ok(P, net, pts[0][0], pts[0][1]):
        return pts
    return None


def _route_multihop(P, layer, net, margin, tree, grp, blockers, maxhops=5):
    """COND1 to the wall, a short crossover over it, COND1 on -- repeated, so a
    crossover is always a short hop. Returns a list of emit ops
    [("trace", pts) | ("cross", F, G, bridge)] or None."""
    starts = [t.centre for t in tree]
    grp_pts = [t.centre for t in grp]
    grp_rects = [t.rect for t in grp]
    ops = []
    last = None
    for _hop in range(maxhops):
        path, hit = _plan(P.space, layer, net, margin, starts, grp_pts, grp_rects,
                          blockers, frontier=True)
        if hit:
            if len(path) >= 2:
                ops.append(("trace", path))
            return ops
        if path is None:
            # fully hemmed: no COND1 frontier -- anchor the up-via on the nearest
            # terminal and hop out on COND2
            F = min(starts, key=lambda c: min(math.hypot(c[0] - t.centre[0], c[1] - t.centre[1])
                                              for t in grp))
            path = [F]
        else:
            path = _backoff_land(P, net, path, blockers)
            if path is None:
                return None
            F = path[-1]
        if last is not None and math.hypot(F[0] - last[0], F[1] - last[1]) < 1:
            return None                                   # no progress
        last = F
        hop = _short_hop(P, layer, net, margin, F, grp, blockers)
        if hop is None:
            return None
        G, bridge = hop
        if len(path) >= 2:
            ops.append(("trace", path))
        ops.append(("cross", F, G, bridge))
        starts = [G]
    return None


def _orthogonalize(P, layer, net, margin, pts, pref):
    """Best-effort 90-degree routing: replace each slanted segment with an L
    (two axis-aligned legs) when both legs are clear, preferring the layer's
    grain for the longer leg. A segment that can't go orthogonal stays as is."""
    if len(pts) < 2:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts)):
        a, b = out[-1], pts[i]
        if abs(a[0] - b[0]) < 1 or abs(a[1] - b[1]) < 1:
            out.append(b)
            continue
        blk = _scope(P.space, layer,
                     [(min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))],
                     [(b[0], b[1], b[0], b[1])], margin + 2000)
        c_hv = (b[0], a[1])    # horizontal leg first
        c_vh = (a[0], b[1])    # vertical leg first
        order = [c_vh, c_hv] if pref == "V" else [c_hv, c_vh]
        for c in order:
            if P.space.segment_clear(a, c, net, margin, blk) \
                    and P.space.segment_clear(c, b, net, margin, blk):
                out.append(c)
                out.append(b)
                break
        else:
            out.append(b)
    return out


def _route_core(design: dict, order=None, on_net=None, scope_pad: float = 6000.0,
                max_jumpers: Optional[int] = None, allow_crossovers: bool = True,
                max_crossovers: Optional[int] = None, local_dielectric: bool = True,
                on_line=None, cancel=None, tag: str = "", ripup_rounds: int = 0,
                time_budget: Optional[float] = None) -> dict:
    """Route ``design``: one pass over the nets in ``order`` (default: easiest
    first by bounding-box spread) -- per connection planar, then a local
    crossover (a COND2 bridge on a local dielectric strip, diel_factor x the
    width, kept off resistors and pads), then a wire-bond jumper -- and then up
    to ``ripup_rounds`` rounds of RIP-UP AND REROUTE: for each connection left
    open, the path it would take with only the fixed obstacles (pads, parts,
    keep-outs) on the board shows which nets' copper is in its way; those are
    ripped up, the failed net routes first, they route after it, and the
    change is kept only if more connections route in all (else rolled back).
    ``time_budget`` (s) caps the rip-up rounds. Returns a result dict shaped
    like the Java engine's. ``max_jumpers`` overrides routing.maxJumpers
    (0 = no limit); ``local_dielectric`` False (no dielectric layer) turns
    crossovers off."""
    global _DIRS, _ORTHO
    import time as _time
    P = ShapeProblem(design)
    _DIRS = {L: (ax, P.against) for L, ax in P.dir_axis.items()}
    _ORTHO = P.orthogonal
    layer = P.layers[0]
    two = len(P.layers) >= 2
    cond2 = P.layers[1] if two else "COND2"
    jb0 = P.max_jumpers if max_jumpers is None else int(max_jumpers)
    if jb0 <= 0:
        jb0 = 10 ** 9              # 0 = no limit, as the Java engine and the dialog say
    jumpers_on = P.jumpers_allowed
    cb0 = (10 ** 9 if max_crossovers is None else int(max_crossovers)) \
        if (allow_crossovers and local_dielectric) else 0
    issues: List[dict] = []
    if allow_crossovers and not local_dielectric:
        issues.append({"severity": "WARNING", "code": "NO_DIELECTRIC_LAYER", "ref": "",
                       "message": "no dielectric layer is set (Setup > Technology Properties), "
                                  "so no printed crossovers: crossings need wire-bond jumpers"})
    base_pads = list(design.get("substratePads") or [])
    base_wires = list(design.get("wires") or [])
    say = on_line or (lambda _m: None)
    st = {"jb": jb0, "cb": cb0, "n": 0}
    recs: Dict[str, dict] = {}       # net -> what routing it put on the board
    term_by_id = {t.tid: t for t in P.terminals}

    def spread(net):
        ts = [t for g in P.groups[net] for t in g]
        xs = [t.centre[0] for t in ts]
        ys = [t.centre[1] for t in ts]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    def add_rect(rec, lay, net, rect):
        e = (net or "", rect)
        P.space._by_layer.setdefault(lay, []).append(e)
        rec["space"].append((lay, e))

    def add_copper(rec, lay, net, pts, width):
        # an axis-aligned leg is its exact rectangle; a slanted one is covered
        # by a strip of small squares along it (its bounding box would wall
        # off the whole box it spans)
        hw = width / 2.0
        for i in range(1, len(pts)):
            (ax, ay), (bx, by) = pts[i - 1], pts[i]
            if abs(ax - bx) < EPS or abs(ay - by) < EPS:
                add_rect(rec, lay, net, (min(ax, bx) - hw, min(ay, by) - hw,
                                         max(ax, bx) + hw, max(ay, by) + hw))
                continue
            L = math.hypot(bx - ax, by - ay)
            k = max(1, int(math.ceil(L / max(hw, 25.0))))
            for j in range(k):
                t0, t1 = j / k, (j + 1) / k
                x0, x1 = ax + (bx - ax) * t0, ax + (bx - ax) * t1
                y0, y1 = ay + (by - ay) * t0, ay + (by - ay) * t1
                add_rect(rec, lay, net, (min(x0, x1) - hw, min(y0, y1) - hw,
                                         max(x0, x1) + hw, max(y0, y1) + hw))

    def emit_trace(rec, net, pts):
        pts = [q for i, q in enumerate(pts) if i == 0 or q != pts[i - 1]]
        if len(pts) < 2:
            return
        if P.orthogonal:
            pts = _orthogonalize(P, layer, net, P.margin(net), pts, P.dir_axis.get(layer))
        w = P.width_of(net)
        rec["traces"].append({"net": net, "layer": layer, "widthUm": int(round(w)),
                              "points": [{"x": int(round(x)), "y": int(round(y))} for x, y in pts]})
        add_copper(rec, layer, net, pts, w)

    def add_land(rec, net, rect, role, lid):
        cx = (rect[0] + rect[2]) / 2.0
        cy = (rect[1] + rect[3]) / 2.0
        rec["pads"].append({"id": lid, "net": net, "layer": layer,
                            "position": {"x": int(round(cx)), "y": int(round(cy))},
                            "widthUm": int(round(P.land)), "heightUm": int(round(P.land)),
                            "role": role})
        add_rect(rec, layer, net, rect)     # own-net copper others must clear

    def add_crossover(rec, net, A, B, bridge, w):
        rec["crossovers"].append({"net": net, "widthUm": int(round(w)),
                                  "dielWidthUm": int(round(P.diel_factor * w)),
                                  "landUm": int(round(P.land)),
                                  "a": {"x": int(round(A[0])), "y": int(round(A[1]))},
                                  "b": {"x": int(round(B[0])), "y": int(round(B[1]))},
                                  "bridge": [{"x": int(round(x)), "y": int(round(y))}
                                             for x, y in bridge]})
        add_copper(rec, cond2, net, bridge, w)
        st["cb"] -= 1

    def rip(net):
        """Take everything ``net`` routed off the board (and give back its
        crossover / jumper budget)."""
        rec = recs.pop(net, None)
        if not rec:
            return
        ids = {id(e) for _l, e in rec["space"]}
        for lay in {l for l, _e in rec["space"]}:
            P.space._by_layer[lay] = [e for e in P.space._by_layer[lay] if id(e) not in ids]
        st["jb"] += rec["jumpers"]
        st["cb"] += len(rec["crossovers"])

    def route_net(net, verbose=True):
        rec = {"traces": [], "crossovers": [], "pads": [], "wires": [], "space": [],
               "routed": 0, "unrouted": [], "jumpers": 0}
        recs[net] = rec
        groups = [list(g) for g in P.groups[net]]
        if len(groups) < 2:
            return rec
        margin = P.margin(net)
        w = P.width_of(net)
        tree = groups.pop(0)
        while groups:
            start_rects = [t.rect for t in tree]
            starts = [t.centre for t in tree]
            groups.sort(key=lambda grp: min(math.hypot(s[0] - t.centre[0], s[1] - t.centre[1])
                                            for s in starts for t in grp))
            made = None
            for gi, grp in enumerate(groups):
                blk = _scope(P.space, layer, start_rects, [t.rect for t in grp], scope_pad + margin)
                path = _plan(P.space, layer, net, margin, starts,
                             [t.centre for t in grp], [t.rect for t in grp], blk)
                if path is not None:
                    made = ("planar", gi, grp, P.space.tighten(path, net, margin, blk))
                    break
            if made is None and st["cb"] > 0:
                # short local crossovers (multi-hop) first -- robust in both modes
                for gi, grp in enumerate(groups):
                    blk = _scope(P.space, layer, start_rects, [t.rect for t in grp],
                                 scope_pad + margin)
                    ops = _route_multihop(P, layer, net, margin, tree, grp, blk)
                    if ops is not None:
                        made = ("multi", gi, grp, ops)
                        break
                if made is None and two:
                    # two conductors: a full COND2 track for runs the hops can't make
                    for gi, grp in enumerate(groups):
                        blk = _scope(P.space, layer, start_rects, [t.rect for t in grp],
                                     scope_pad + margin)
                        c = _try_crossover(P, layer, net, margin, starts, grp, blk, cap=None)
                        if c is not None and c != "resistor":
                            made = ("xover", gi, grp, c)
                            break
            if made is None and jumpers_on and st["jb"] > 0:
                for gi, grp in enumerate(groups):
                    blk = _scope(P.space, layer, start_rects, [t.rect for t in grp], scope_pad + margin)
                    j = _try_jumper(P, layer, net, margin, starts, grp, blk)
                    if j is not None:
                        made = ("jumper", gi, grp, j)
                        break
            if made is None:
                # nothing reaches the tree: leave this one gap, and grow a new
                # tree from the nearest pad left so the rest of the net still
                # routes among itself (one failure is one gap, not the net)
                grp = groups[0]
                rec["unrouted"].append({"net": net, "from": tree[0].tid, "to": grp[0].tid})
                if verbose:
                    say(f"{tag}   {net}: {tree[0].tid} -> {grp[0].tid} NOT routed")
                tree = groups.pop(0)
                continue
            kind, gi, grp, payload = made
            if kind != "planar" and verbose:
                say(f"{tag}   {net}: {tree[0].tid} -> {grp[0].tid} needs a "
                    + {"jumper": "wire-bond jumper", "xover": "COND2 crossover"}.get(
                        kind, "printed crossover"))
            if kind == "planar":
                emit_trace(rec, net, payload)
            elif kind == "jumper":
                landA, landB, pathA, pathB, a, b = payload
                st["n"] += 1
                la, lb = "JL_%s_%d" % (net, st["n"]), "JL2_%s_%d" % (net, st["n"])
                add_land(rec, net, landA, "JUMPER_LAND", la)
                add_land(rec, net, landB, "JUMPER_LAND", lb)
                emit_trace(rec, net, pathA + [a])
                emit_trace(rec, net, [b] + pathB)
                rec["wires"].append({"id": "JW_%s_%d" % (net, st["n"]), "net": net,
                                     "from": {"kind": "SUBSTRATE_PAD", "pad": la},
                                     "to": {"kind": "SUBSTRATE_PAD", "pad": lb}})
                rec["jumpers"] += 1
                st["jb"] -= 1
            elif kind == "xover":  # full second-conductor track with COND1 stubs
                emit_trace(rec, net, payload["stubA"])
                emit_trace(rec, net, payload["stubB"])
                add_rect(rec, layer, net, payload["landA"])
                add_rect(rec, layer, net, payload["landB"])
                add_crossover(rec, net, payload["a"], payload["b"], payload["bridge"], w)
            else:  # multi: COND1 traces + short printed-jumper crossovers
                hl = P.land / 2.0
                for op in payload:
                    if op[0] == "trace":
                        emit_trace(rec, net, op[1])
                    else:
                        _, A, B, bridge = op
                        add_rect(rec, layer, net, (A[0] - hl, A[1] - hl, A[0] + hl, A[1] + hl))
                        add_rect(rec, layer, net, (B[0] - hl, B[1] - hl, B[0] + hl, B[1] + hl))
                        add_crossover(rec, net, A, B, bridge, w)
            rec["routed"] += 1
            tree += grp
            del groups[gi]
        if on_net:
            on_net(net)
        return rec

    if order is None:
        net_seq = sorted(P.groups, key=lambda nm: (spread(nm), nm))
    else:
        seen = set()
        net_seq = [nm for nm in order if nm in P.groups and not (nm in seen or seen.add(nm))]
        net_seq += [nm for nm in sorted(P.groups, key=lambda nm: (spread(nm), nm)) if nm not in seen]
    connections = sum(max(0, len(P.groups[nm]) - 1) for nm in net_seq)   # all of them, up front
    fixed = {L: list(v) for L, v in P.space._by_layer.items()}        # pads, parts, keep-outs

    def routed_now():
        return sum(r["routed"] for r in recs.values())

    def stop_now():
        return cancel is not None and cancel()

    # ---- first pass ------------------------------------------------------------------
    stopped = False
    nets_n = len(net_seq)
    for net_i, net in enumerate(net_seq, 1):
        if len(P.groups[net]) < 2:
            continue
        if stopped or stop_now():
            if not stopped:
                say(f"{tag}stopped -- {routed_now()}/{connections} routed")
            stopped = True
            g = P.groups[net]
            recs[net] = {"traces": [], "crossovers": [], "pads": [], "wires": [], "space": [],
                         "routed": 0, "jumpers": 0,
                         "unrouted": [{"net": net, "from": g[0][0].tid, "to": x[0].tid}
                                      for x in g[1:]]}
            continue
        say(f"{tag}net {net_i}/{nets_n} {net} ({len(P.groups[net])} pads) -- "
            f"{routed_now()}/{connections} routed")
        route_net(net)
    say(f"{tag}first pass: {routed_now()}/{connections} routed")

    # ---- rip-up and reroute ------------------------------------------------------------
    def blockers_for(net, gap):
        """The nets whose routed copper lies across the path ``gap`` would take
        with only the fixed obstacles on the board."""
        a, b = term_by_id.get(gap["from"]), term_by_id.get(gap["to"])
        if a is None or b is None:
            return []
        margin = P.margin(net)
        live = P.space._by_layer
        P.space._by_layer = fixed
        try:
            blk = _scope(P.space, layer, [a.rect], [b.rect], scope_pad + margin)
            path = _plan(P.space, layer, net, margin, [a.centre], [b.centre], [b.rect], blk)
        finally:
            P.space._by_layer = live
        if not path:
            return []
        hits = {}
        fixed_ids = {id(e) for e in fixed.get(layer, ())}
        for n2, r in live.get(layer, ()):
            if not n2 or n2 == net or n2.startswith("~"):
                continue
            for i in range(1, len(path)):
                if _seg_rect_dist(path[i - 1][0], path[i - 1][1], path[i][0], path[i][1], r) \
                        < margin - EPS:
                    hits[n2] = hits.get(n2, 0) + 1
                    break
        # copper only (pads of other nets are fixed and not rippable)
        rippable = {n2 for n2, c in hits.items() if n2 in recs and recs[n2]["space"]}
        return sorted(rippable, key=lambda n2: -hits[n2])[:4]

    t0 = _time.time()
    rounds = 0
    for rnd in range(max(0, int(ripup_rounds))):
        if stopped:
            break
        failed = [nm for nm in net_seq if recs.get(nm) and recs[nm]["unrouted"]]
        if not failed:
            break
        rounds += 1
        say(f"{tag}rip-up round {rnd + 1}/{ripup_rounds}: {len(failed)} net(s) with gaps")
        improved = False
        for net in failed:
            if stop_now() or (time_budget is not None and _time.time() - t0 > time_budget):
                say(f"{tag}rip-up {'stopped' if stop_now() else 'out of time'} -- "
                    f"{routed_now()}/{connections} routed")
                stopped = stop_now()
                break
            rec = recs.get(net)
            if not rec or not rec["unrouted"]:
                continue
            blockers = blockers_for(net, rec["unrouted"][0])
            if not blockers:
                continue
            before = routed_now()
            snap_space = {L: list(v) for L, v in P.space._by_layer.items()}
            snap_recs = dict(recs)
            snap_st = dict(st)
            for b in blockers:
                rip(b)
            rip(net)
            route_net(net, verbose=False)
            for b in sorted(blockers, key=lambda nm: (spread(nm), nm)):
                route_net(b, verbose=False)
            after = routed_now()
            if after > before:
                improved = True
                say(f"{tag}   ripped up {', '.join(blockers)} for {net}: "
                    f"{before} -> {after} routed")
            else:
                P.space._by_layer = snap_space
                recs.clear()
                recs.update(snap_recs)
                st.clear()
                st.update(snap_st)
        else:
            if not improved:
                break
            continue
        break
    if ripup_rounds:
        say(f"{tag}after rip-up: {routed_now()}/{connections} routed")

    # ---- the result -------------------------------------------------------------------
    traces, crossovers, unrouted, pads, wires = [], [], [], [], []
    jumpers = 0
    for nm in net_seq:
        r = recs.get(nm)
        if not r:
            continue
        traces += r["traces"]
        crossovers += r["crossovers"]
        unrouted += r["unrouted"]
        pads += r["pads"]
        wires += r["wires"]
        jumpers += r["jumpers"]
    design["substratePads"] = base_pads + pads
    design["wires"] = base_wires + wires
    length = 0.0
    for t in traces:
        pts = t["points"]
        for i in range(1, len(pts)):
            length += math.hypot(pts[i]["x"] - pts[i - 1]["x"], pts[i]["y"] - pts[i - 1]["y"])
    stats = {"connections": connections, "routed": routed_now(), "jumpersAdded": jumpers,
             "stopped": stopped, "ripupRounds": rounds,
             "crossoversAdded": len(crossovers), "fingersAdded": 0, "windows": 0,
             "traceLengthUm": int(length), "gridUm": 0}
    return {"design": design, "traces": traces, "crossovers": crossovers, "windows": [],
            "unrouted": unrouted, "stats": stats, "issues": issues}


def _congestion_penalty(h, cell, k):
    if not h:
        return None

    def pen(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        n = max(1, int(L // cell))
        s = 0.0
        for i in range(n + 1):
            x = a[0] + dx * i / n
            y = a[1] + dy * i / n
            s += h.get((int(x // cell), int(y // cell)), 0.0)
        return s * k * cell
    return pen


def route(design: dict, on_net=None, scope_pad: float = 6000.0, on_line=None, cancel=None,
          max_jumpers: Optional[int] = None, allow_crossovers: bool = True,
          max_crossovers: Optional[int] = None, passes: Optional[int] = None,
          local_dielectric: bool = True, cond2_keepouts=None,
          time_budget: Optional[float] = 180.0) -> dict:
    """Route ``design`` with the gridless shape router: one pass over the nets,
    then ``passes`` - 1 rounds of rip-up and reroute (each connection left open
    rips up the nets in its way, routes first, and the change is kept only if
    more connections route in all) -- see _route_core. ``time_budget`` (s) caps
    the rip-up; ``cancel`` stops after the current net with the best so far;
    ``on_line`` gets progress lines. Planar -> local crossover -> jumper."""
    global _PEN, _ORTHO
    import copy
    import time as _time
    if cond2_keepouts is not None:
        # the local-strip keep-outs (resistor ink + oversize, keep-out layers)
        # in place of the full-fill ones the design carries for the Java engine
        design = copy.deepcopy(design)
        design.setdefault("routing", {}).setdefault("keepouts", {})["COND2"] = \
            [list(r) for r in cond2_keepouts]
    rounds = max(0, (1 if passes is None else int(passes)) - 1)
    say = on_line or (lambda _m: None)
    t0 = _time.time()
    _PEN = None
    try:
        res = _route_core(copy.deepcopy(design), on_net=on_net, scope_pad=scope_pad,
                          max_jumpers=max_jumpers, allow_crossovers=allow_crossovers,
                          max_crossovers=max_crossovers, local_dielectric=local_dielectric,
                          on_line=on_line, cancel=cancel, ripup_rounds=rounds,
                          time_budget=time_budget)
    finally:
        _PEN = None
        _ORTHO = False
    st = res["stats"]
    say(f"done in {_time.time() - t0:.1f}s: {st['routed']}/{st['connections']} routed, "
        f"{st['crossoversAdded']} crossover(s), {st['jumpersAdded']} jumper(s)")
    st["passes"] = 1 + st.get("ripupRounds", 0)
    return res
