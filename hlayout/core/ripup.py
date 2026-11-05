# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Rip up the routing between two points of a net.

Given two points on routed copper of the same net (on either conductor),
find the shortest run of track between them -- along the tracks, through
T-junctions and through vias (flat drop_via() vias and placed via cells)
-- and take it out: the tracks are cut at the two points and every piece
in between removed, and a via the run went through is removed too once
nothing is left on one of its sides. The rest of the net is untouched, so
the two points become loose ends to route again.

Only the tracks (Paths on the two route layers) of the cell itself are
cut; pads, bond fingers and anything inside a placed part are never
touched -- the run is found along tracks only.
"""

import heapq
import math
from typing import Dict, List, Optional, Tuple

from .shapes import Path


def _seg_proj(p, a, b):
    """(t in 0..1, distance, (x, y)) of the point on segment a-b nearest p."""
    ax, ay = a
    dx, dy = b[0] - ax, b[1] - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    x, y = ax + t * dx, ay + t * dy
    return t, math.hypot(p[0] - x, p[1] - y), (x, y)


def _arclen(pts):
    s = [0.0]
    for i in range(1, len(pts)):
        s.append(s[-1] + math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
    return s


def _point_at(pts, S, s):
    for i in range(1, len(pts)):
        if s <= S[i] + 1e-9:
            seg = S[i] - S[i - 1]
            t = 0.0 if seg == 0 else (s - S[i - 1]) / seg
            return (pts[i - 1][0] + t * (pts[i][0] - pts[i - 1][0]),
                    pts[i - 1][1] + t * (pts[i][1] - pts[i - 1][1]))
    return pts[-1]


def _sub_polyline(pts, S, s0, s1):
    """The part of the polyline between arc lengths s0 < s1."""
    out = [_point_at(pts, S, s0)]
    for i in range(1, len(pts) - 1):
        if s0 < S[i] < s1:
            out.append(tuple(pts[i]))
    out.append(_point_at(pts, S, s1))
    clean = [out[0]]
    for p in out[1:]:
        if math.hypot(p[0] - clean[-1][0], p[1] - clean[-1][1]) > 0.5:
            clean.append(p)
    return [(int(round(x)), int(round(y))) for x, y in clean]


def _via_sites(library, cell, la, lb):
    """[(x, y, kind, obj)]: flat vias (find_vias) and placed via cells --
    an instance of a cell with copper on both route layers and no pins."""
    from ..io.specctra import find_vias
    out = [(float(x), float(y), "flat", None) for x, y, _w in find_vias(cell, (la, lb))]
    for r in cell.srefs:
        c = library.cells.get(r.cell_name)
        if c is None or c.srefs:
            continue
        lays = {s.layer for s in c.shapes}
        if la in lays and lb in lays and not any(getattr(s, "pin", "") for s in c.shapes) \
                and len(c.shapes) <= 6:
            out.append((float(r.origin[0]), float(r.origin[1]), "cell", r))
    return out


def find_run(library, cell, layers: Tuple[int, int], a, b, tol: float = 0.0):
    """The shortest run of track between points ``a`` and ``b``. Returns
    {"cuts": {track index: [(s0, s1)]}, "vias": [(x, y, kind, obj)],
    "a": (x, y, layer), "b": (x, y, layer), "length", "tracks"} or None
    when the two points aren't joined by tracks."""
    la, lb = int(layers[0]), int(layers[1])
    tracks = [sh for sh in cell.shapes
              if isinstance(sh, Path) and sh.layer in (la, lb) and len(sh.points) >= 2]
    if not tracks:
        return None
    vias = _via_sites(library, cell, la, lb)
    geo = []
    for p in tracks:
        pts = [tuple(map(float, q)) for q in p.points]
        geo.append((pts, _arclen(pts)))

    def pick(pt):
        best = None
        for k, p in enumerate(tracks):
            pts, S = geo[k]
            for i in range(1, len(pts)):
                t, d, xy = _seg_proj(pt, pts[i - 1], pts[i])
                if d <= p.width / 2.0 + tol and (best is None or d < best[0]):
                    best = (d, k, S[i - 1] + t * (S[i] - S[i - 1]), xy)
        return best

    pa, pb = pick(a), pick(b)
    if pa is None or pb is None:
        return None
    if pa[1] == pb[1] and abs(pa[2] - pb[2]) < 1:
        return None

    # stations along every track: its vertices, where another track's end,
    # a via or one of the two picked points lies on it
    stations: List[set] = [set(S) for _pts, S in geo]
    ends = []
    for k, p in enumerate(tracks):
        pts, _S = geo[k]
        ends.append((p.layer, pts[0]))
        ends.append((p.layer, pts[-1]))
    for k, p in enumerate(tracks):
        pts, S = geo[k]
        r = p.width / 2.0
        probes = [xy for lay, xy in ends if lay == p.layer] + [(v[0], v[1]) for v in vias]
        for xy in probes:
            for i in range(1, len(pts)):
                t, d, _q = _seg_proj(xy, pts[i - 1], pts[i])
                if d <= r:
                    stations[k].add(S[i - 1] + t * (S[i] - S[i - 1]))
    stations[pa[1]].add(pa[2])
    stations[pb[1]].add(pb[2])

    # nodes: station points clustered per layer (touching copper = one node)
    node_of: Dict[Tuple[int, float], int] = {}
    pos: List[Tuple[int, float, float]] = []
    grid: Dict[Tuple[int, int, int], List[int]] = {}
    G = 50.0

    def node(layer, xy, join):
        gx, gy = int(xy[0] // G), int(xy[1] // G)
        span = int(join // G) + 1
        best = None
        for ix in range(gx - span, gx + span + 1):
            for iy in range(gy - span, gy + span + 1):
                for n in grid.get((layer, ix, iy), ()):
                    d = math.hypot(pos[n][1] - xy[0], pos[n][2] - xy[1])
                    if d <= join and (best is None or d < best[0]):
                        best = (d, n)
        if best is not None:
            return best[1]
        pos.append((layer, xy[0], xy[1]))
        n = len(pos) - 1
        grid.setdefault((layer, gx, gy), []).append(n)
        return n

    adj: Dict[int, List[Tuple[int, float, tuple]]] = {}
    for k, p in enumerate(tracks):
        pts, S = geo[k]
        ss = sorted(stations[k])
        join = p.width / 2.0
        ids = []
        for s in ss:
            n = node(p.layer, _point_at(pts, S, s), join)
            node_of[(k, s)] = n
            ids.append(n)
        for i in range(1, len(ss)):
            w = ss[i] - ss[i - 1]
            if w <= 0:
                continue
            u, v = ids[i - 1], ids[i]
            adj.setdefault(u, []).append((v, w, ("t", k, ss[i - 1], ss[i])))
            adj.setdefault(v, []).append((u, w, ("t", k, ss[i - 1], ss[i])))
    vr = max(p.width for p in tracks) / 2.0
    for vi, v in enumerate(vias):
        na = node(la, (v[0], v[1]), vr)
        nb = node(lb, (v[0], v[1]), vr)
        # tie the via's pad nodes to every track station on the via
        for n in (na, nb):
            lay = pos[n][0]
            for m, (l2, x2, y2) in enumerate(pos):
                if m != n and l2 == lay and math.hypot(x2 - v[0], y2 - v[1]) <= vr:
                    adj.setdefault(n, []).append((m, 0.0, ("j",)))
                    adj.setdefault(m, []).append((n, 0.0, ("j",)))
        adj.setdefault(na, []).append((nb, 0.0, ("v", vi)))
        adj.setdefault(nb, []).append((na, 0.0, ("v", vi)))

    src, dst = node_of[(pa[1], pa[2])], node_of[(pb[1], pb[2])]
    dist = {src: 0.0}
    prev: Dict[int, Tuple[int, tuple]] = {}
    pq = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == dst:
            break
        if d > dist.get(u, math.inf):
            continue
        for v, w, tag in adj.get(u, ()):
            nd = d + w
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = (u, tag)
                heapq.heappush(pq, (nd, v))
    if dst not in dist:
        return None
    cuts: Dict[int, List[Tuple[float, float]]] = {}
    used_vias = []
    on_run = {src}
    n = dst
    while n != src:
        on_run.add(n)
        u, tag = prev[n]
        if tag[0] == "t":
            cuts.setdefault(tag[1], []).append((tag[2], tag[3]))
        elif tag[0] == "v":
            used_vias.append(vias[tag[1]])
        n = u
    # a short piece whose two ends fell into ONE node (a stub shorter than
    # the track is wide, at a corner or a via) goes with the run too
    for k in range(len(tracks)):
        ss = sorted(stations[k])
        for i in range(1, len(ss)):
            u, v = node_of[(k, ss[i - 1])], node_of[(k, ss[i])]
            if u == v and u in on_run:
                cuts.setdefault(k, []).append((ss[i - 1], ss[i]))
    return {"cuts": cuts, "vias": used_vias, "tracks": tracks, "geo": geo,
            "a": (pa[3][0], pa[3][1], tracks[pa[1]].layer),
            "b": (pb[3][0], pb[3][1], tracks[pb[1]].layer),
            "length": dist[dst]}


def rip_up(library, cell, layers: Tuple[int, int], a, b, tol: float = 0.0) -> Optional[dict]:
    """Cut the shortest run of track between ``a`` and ``b`` out of
    ``cell`` (edits it in place; see the module doc). Returns
    {"a": (x, y, layer), "b": (x, y, layer), "length", "tracks": tracks
    cut, "vias": vias removed [(x, y)]} or None when no run joins them."""
    run = find_run(library, cell, layers, a, b, tol=tol)
    if run is None:
        return None
    la, lb = int(layers[0]), int(layers[1])
    tracks, geo = run["tracks"], run["geo"]
    for k, iv in run["cuts"].items():
        p = tracks[k]
        pts, S = geo[k]
        iv = sorted(iv)
        merged = []
        for s0, s1 in iv:
            if merged and s0 <= merged[-1][1] + 1e-6:
                merged[-1] = (merged[-1][0], max(merged[-1][1], s1))
            else:
                merged.append((s0, s1))
        keep, cur = [], 0.0
        for s0, s1 in merged:
            if s0 - cur > 0.5:
                keep.append((cur, s0))
            cur = s1
        if S[-1] - cur > 0.5:
            keep.append((cur, S[-1]))
        idx = next(i for i, sh in enumerate(cell.shapes) if sh is p)
        new = []
        for s0, s1 in keep:
            q = _sub_polyline(pts, S, s0, s1)
            if len(q) >= 2:
                new.append(Path(layer=p.layer, points=q, width=p.width, cap=p.cap,
                                datatype=getattr(p, "datatype", 0)))
        cell.shapes[idx:idx + 1] = new
    # a via the run went through goes once one of its sides has no track
    removed = []
    left = [sh for sh in cell.shapes
            if isinstance(sh, Path) and sh.layer in (la, lb) and len(sh.points) >= 2]

    def touched(x, y, layer):
        for sh in left:
            if sh.layer != layer:
                continue
            pts = sh.points
            for i in range(1, len(pts)):
                if _seg_proj((x, y), pts[i - 1], pts[i])[1] <= sh.width / 2.0:
                    return True
        return False

    for x, y, kind, obj in run["vias"]:
        if touched(x, y, la) and touched(x, y, lb):
            continue
        if kind == "cell":
            if obj in cell.srefs:
                cell.srefs.remove(obj)
        else:
            from ..io.specctra import find_vias as _fv
            _ = _fv  # flat via shapes: both pads and the window share the centre
            doomed = []
            for sh in cell.shapes:
                if isinstance(sh, Path) or getattr(sh, "pin", "") or not hasattr(sh, "bbox"):
                    continue
                if getattr(sh, "datatype", 0) != 0:
                    continue
                x0, y0, x1, y1 = sh.bbox()
                w, h = x1 - x0, y1 - y0
                if 0 < w <= 5000 and abs(w - h) <= max(2, 0.02 * w) and \
                        abs((x0 + x1) / 2 - x) <= 1 and abs((y0 + y1) / 2 - y) <= 1:
                    doomed.append(id(sh))
            cell.shapes[:] = [sh for sh in cell.shapes if id(sh) not in set(doomed)]
        removed.append((x, y))
    return {"a": run["a"], "b": run["b"], "length": run["length"],
            "tracks": len(run["cuts"]), "vias": removed}
