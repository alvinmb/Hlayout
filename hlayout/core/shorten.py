# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shorten routed tracks: straighten detours and pull corners tight.

Runs after the autorouter (and the via optimisation), and works even when
some nets are still unrouted -- unlike Freerouting's own optimiser, which
only runs on a completely routed board.

A router that keeps each conductor to one track direction hops between
the two layers at nearly every turn, so a detour is a CHAIN: tracks and
vias in series, from one terminal to another. A terminal is anything the
chain must keep reaching -- a pad, a bond finger, a part's copper, a
junction where the net branches, a via with more than the chain on it.
Each chain is re-laid between its two terminals the shortest clear way
over BOTH conductors, a via costing ``via_equiv`` um of track: a
two-layer grid maze route over a window round the chain, its result
checked on the exact geometry. The new route replaces the chain (its
tracks and its own vias) when it is cheaper by at least ``min_gain`` um
and ``min_gain_frac``, and when:

  * every new track and via pad keeps ``clearance`` from every other
    net's copper on its layer -- pads and fingers inside placed parts
    included, and routing already changed in this pass;
  * it stays out of the routing keep-outs (dice, part bodies) and inside
    the keep-in (the substrate; for the 2nd conductor and every via, the
    printed dielectric).

The rest of the net is untouched; nets stay exactly as joined as before.
The worst detours go first, and passes repeat while they still gain.
"""

import heapq
import math
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import shapely
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .drc import _collect_geoms, _sref_point_transform
from .nettrace import NetTracer
from .shapes import Path

#: hard ceiling on maze cells per chain (both layers together)
MAX_CELLS = 600_000


def _length(pts):
    return sum(math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
               for i in range(1, len(pts)))


def _dedupe(pts):
    out = [(int(round(pts[0][0])), int(round(pts[0][1])))]
    for p in pts[1:]:
        p = (int(round(p[0])), int(round(p[1])))
        if p != out[-1]:
            out.append(p)
    k = 1
    while k < len(out) - 1:
        a, b, c = out[k - 1], out[k], out[k + 1]
        if (b[0] - a[0]) * (c[1] - a[1]) == (b[1] - a[1]) * (c[0] - a[0]):
            out.pop(k)
        else:
            k += 1
    return out


# --------------------------------------------------------------------------
# chains

def _via_sites(library, cell, la, lb):
    """[(x, y, size, kind, obj)]: flat vias (pads on both layers sharing a
    centre) and placed via cells."""
    from ..io.specctra import find_vias
    out = [(float(x), float(y), float(w), "flat", None) for x, y, w in find_vias(cell, (la, lb))]
    for r in cell.srefs:
        c = library.cells.get(r.cell_name)
        if c is None or c.srefs:
            continue
        lays = {s.layer for s in c.shapes}
        if la in lays and lb in lays and not any(getattr(s, "pin", "") for s in c.shapes) \
                and len(c.shapes) <= 6:
            ws = [s.bbox()[2] - s.bbox()[0] for s in c.shapes if s.layer == la and hasattr(s, "bbox")]
            out.append((float(r.origin[0]), float(r.origin[1]), float(max(ws) if ws else 0),
                        "cell", r))
    return out


def _chains(library, cell, la, lb, tracks, vias, inst, inst_tree):
    """Split the routing into chains (see the module doc). Returns
    [{"tracks": [Path], "vias": [via site], "a": (layer, (x, y)),
    "b": (layer, (x, y)), "width": um}]."""
    # endpoint nodes
    ends: Dict[Tuple[int, int, int], List[Tuple[int, int]]] = {}
    for k, p in enumerate(tracks):
        for side, q in ((0, p.points[0]), (1, p.points[-1])):
            ends.setdefault((p.layer, int(round(q[0])), int(round(q[1]))), []).append((k, side))
    # which via (if any) each end sits on
    vtree = STRtree([Point(v[0], v[1]) for v in vias]) if vias else None
    node_via: Dict[Tuple[int, int, int], int] = {}
    for key in ends:
        if vtree is None:
            break
        lay, x, y = key
        for i in vtree.query(Point(x, y).buffer(400)):
            v = vias[int(i)]
            if abs(v[0] - x) <= max(1.0, v[2] / 2.0) and abs(v[1] - y) <= max(1.0, v[2] / 2.0):
                node_via[key] = int(i)
                break
    # anything else of the copper at a node makes it a terminal
    others = []
    via_pad_ids = set()
    for v in vias:
        if v[3] == "flat":
            for sh in cell.shapes:
                if not isinstance(sh, Path) and sh.layer in (la, lb) and hasattr(sh, "bbox"):
                    b = sh.bbox()
                    if abs((b[0] + b[2]) / 2 - v[0]) <= 1 and abs((b[1] + b[3]) / 2 - v[1]) <= 1 \
                            and not getattr(sh, "pin", ""):
                        via_pad_ids.add(id(sh))
    for sh in cell.shapes:
        if sh.layer in (la, lb) and not isinstance(sh, Path) and hasattr(sh, "bbox") \
                and id(sh) not in via_pad_ids:
            others.append((sh.layer, box(*sh.bbox())))
    o_tree = STRtree([g for _l, g in others]) if others else None
    t_lines = [LineString(p.points) for p in tracks]
    t_tree = STRtree(t_lines) if t_lines else None

    def terminal(key):
        lay, x, y = key
        pt = Point(x, y)
        here = ends[key]
        w = max(tracks[k].width for k, _s in here)
        probe = pt.buffer(w / 2.0)
        if o_tree is not None and any(others[int(i)][0] == lay and others[int(i)][1].intersects(probe)
                                      for i in o_tree.query(probe)):
            return True
        if inst_tree.get(lay) is not None and any(inst[lay][int(i)].intersects(probe)
                                                   for i in inst_tree[lay].query(probe)):
            return True
        # another track passing through (a T-junction on its middle)
        mine = {k for k, _s in here}
        for i in t_tree.query(probe):
            i = int(i)
            if i in mine or tracks[i].layer != lay:
                continue
            if t_lines[i].distance(pt) <= (tracks[i].width + w) / 2.0:
                q0, q1 = tracks[i].points[0], tracks[i].points[-1]
                if (int(round(q0[0])), int(round(q0[1]))) != (x, y) and \
                        (int(round(q1[0])), int(round(q1[1]))) != (x, y):
                    return True
        return False

    term = {key: terminal(key) for key in ends}
    # the via joints: a via whose two layers each carry exactly one track end
    # and nothing else
    via_ends: Dict[int, List[Tuple[int, int, int]]] = {}
    for key, vi in node_via.items():
        via_ends.setdefault(vi, []).append(key)

    def next_of(k, side):
        """Continue from track k's end ``side``: (next track, its side
        entering, via index or None) or None at a terminal."""
        p = tracks[k]
        q = p.points[0] if side == 0 else p.points[-1]
        key = (p.layer, int(round(q[0])), int(round(q[1])))
        if term[key]:
            return None
        vi = node_via.get(key)
        if vi is None:
            here = [h for h in ends[key] if h != (k, side)]
            if len(here) != 1:
                return None
            return here[0][0], here[0][1], None
        keys = via_ends.get(vi, [])
        if len(keys) != 2 or len({kk[0] for kk in keys}) != 2:
            return None
        if any(len(ends[kk]) != 1 or term[kk] for kk in keys):
            return None
        other = next(kk for kk in keys if kk != key)
        (k2, s2), = ends[other]
        return k2, s2, vi

    seen = set()
    out = []
    for k0 in range(len(tracks)):
        if k0 in seen:
            continue
        seen.add(k0)
        # walk back to one end of the chain, then forward collecting it
        k, side = k0, 0
        guard = 0
        while guard < 10000:
            guard += 1
            nx = next_of(k, side)
            if nx is None or nx[0] == k0:
                break
            k, side = nx[0], 1 - nx[1]
        if guard >= 10000:
            continue
        start_k, start_side = k, side
        chain_t, chain_v = [], []
        k, side = start_k, start_side       # ``side`` = the end we came in by
        ok = True
        guard = 0
        while True:
            guard += 1
            if guard > 10000:
                ok = False
                break
            chain_t.append((k, side))
            seen.add(k)
            nx = next_of(k, 1 - side)
            if nx is None:
                break
            if nx[0] == start_k or nx[0] in {c for c, _s in chain_t}:
                ok = False                  # a loop
                break
            if nx[2] is not None:
                chain_v.append(nx[2])
            k, side = nx[0], nx[1]
        if not ok:
            continue
        p0, s0 = tracks[chain_t[0][0]], chain_t[0][1]
        p1, s1 = tracks[chain_t[-1][0]], 1 - chain_t[-1][1]
        a = p0.points[0] if s0 == 0 else p0.points[-1]
        b = p1.points[0] if s1 == 0 else p1.points[-1]
        out.append({"tracks": [tracks[c] for c, _s in chain_t],
                    "vias": [vias[v] for v in chain_v],
                    "a": (p0.layer, (float(a[0]), float(a[1]))),
                    "b": (p1.layer, (float(b[0]), float(b[1]))),
                    "width": max(tracks[c].width for c, _s in chain_t)})
    return out


# --------------------------------------------------------------------------
# two-layer maze

def _mask(geom, xs, ys):
    """bool[nx, ny]: True where (xs[i], ys[j]) is inside ``geom``."""
    if geom is None or geom.is_empty:
        return np.zeros((len(xs), len(ys)), dtype=bool)
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    return shapely.intersects_xy(geom, X, Y)


def _maze(start, goal, blocked, via_ok, xs, ys, via_cells, turn=2):
    """A* over two layers. ``start``/``goal`` = (layer index 0/1, i, j);
    ``blocked`` = [bool[nx, ny]] per layer; ``via_ok`` = bool[nx, ny].
    Steps cost 1 per cell, a turn ``turn``, a via ``via_cells``. Returns
    [(l, i, j)] or None."""
    nx, ny = len(xs), len(ys)
    gl, gi, gj = goal
    dirs = ((1, 0), (-1, 0), (0, 1), (0, -1))

    def h(i, j):
        return abs(i - gi) + abs(j - gj)

    s = (start[0], start[1], start[2], -1)
    g = {s: 0}
    came = {}
    heap = [(h(start[1], start[2]), 0, s)]
    while heap:
        _f, gc, st = heapq.heappop(heap)
        l, i, j, d = st
        if (l, i, j) == goal:
            path = [(l, i, j)]
            while st in came:
                st = came[st]
                path.append((st[0], st[1], st[2]))
            path.reverse()
            return path
        if gc > g.get(st, gc):
            continue
        for di, (dx, dy) in enumerate(dirs):
            ni, nj = i + dx, j + dy
            if not (0 <= ni < nx and 0 <= nj < ny) or blocked[l][ni, nj]:
                continue
            ns = (l, ni, nj, di)
            ng = gc + 1 + (turn if d not in (-1, di) else 0)
            if ng < g.get(ns, 1 << 60):
                g[ns] = ng
                came[ns] = st
                heapq.heappush(heap, (ng + h(ni, nj), ng, ns))
        if via_ok[i, j]:
            ol = 1 - l
            if not blocked[ol][i, j]:
                ns = (ol, i, j, -1)
                ng = gc + via_cells
                if ng < g.get(ns, 1 << 60):
                    g[ns] = ng
                    came[ns] = st
                    heapq.heappush(heap, (ng + h(i, j), ng, ns))
    return None


def _clear(mask, i, j, r):
    nx, ny = mask.shape
    mask[max(0, i - r):min(nx, i + r + 1), max(0, j - r):min(ny, j + r + 1)] = False


def shorten_tracks(library, cell, layers: Tuple[int, int], clearance: float,
                   keepouts: Optional[Dict[int, list]] = None, keepin=None,
                   keepin_b=None, dielectric_layer: Optional[int] = None,
                   fill_datatype: int = 1, via_size: float = 300.0, via_margin: float = 0.0,
                   make_via: Optional[Callable] = None, remove_via: Optional[Callable] = None,
                   grid: float = 100.0, via_equiv: float = 1000.0,
                   min_gain: float = 200.0, min_gain_frac: float = 0.05,
                   max_passes: int = 3, window: float = 1500.0, slack: float = 0.0,
                   max_changes: Optional[int] = None, pad_nets: Optional[dict] = None,
                   progress=None) -> Dict:
    """Shorten the routing of ``cell`` (see the module doc); edits it in
    place. ``keepouts`` = {layer: [shapely geometry]}; ``keepin`` = the
    area tracks must stay in (substrate), ``keepin_b`` = the area the 2nd
    conductor and every via must stay in (printed dielectric); None for
    anywhere. ``make_via(x, y, size)`` draws a via (pads + window) and
    returns its shapes; ``remove_via(site)`` deletes one -- without them
    chains are only re-laid without changing their vias' count. Returns
    {"tracks": chains re-laid, "before": um, "after": um, "vias_before",
    "vias_after"} (lengths and vias of the re-laid chains)."""
    la, lb = int(layers[0]), int(layers[1])
    lays = (la, lb)
    keepouts = keepouts or {}
    ko_union = {l: (unary_union(gs) if gs else None) for l, gs in keepouts.items()}

    inst = {la: [], lb: []}
    for ref in cell.srefs:
        acc = []
        _collect_geoms(library, ref.cell_name, _sref_point_transform(ref), acc)
        for lay, g in acc:
            if lay in inst:
                inst[lay].append(g)
    inst_tree = {lay: (STRtree(gs) if gs else None) for lay, gs in inst.items()}

    res = {"tracks": 0, "before": 0.0, "after": 0.0, "vias_before": 0, "vias_after": 0,
           "why": {}}

    def why(k):
        res["why"][k] = res["why"].get(k, 0) + 1
    def shorted_sets(tr):
        g = {}
        for pin, net in (pad_nets or {}).items():
            if pin in tr.pin_node:
                g.setdefault(tr.dsu.find(tr.pin_node[pin]), set()).add(net)
        return g, {frozenset(v) for v in g.values() if len(v) > 1}

    for _pass in range(max_passes):
        tracer = NetTracer(library, cell, list(lays), dielectric_layer=dielectric_layer,
                           fill_datatype=fill_datatype)
        geoms, nodes = tracer._geoms, tracer.nodes
        # which design nets each piece of copper carries: with net names,
        # "the chain's own net" means copper carrying exactly that one net
        # -- never just "joined to it", so a short already in the copper
        # can't make another net's copper look like the chain's own
        root_names, _shorts = shorted_sets(tracer) if pad_nets else ({}, set())
        tracks = [p for p in cell.shapes
                  if isinstance(p, Path) and p.layer in lays and len(p.points) >= 2]
        vias = _via_sites(library, cell, la, lb)
        chains = _chains(library, cell, la, lb, tracks, vias, inst, inst_tree)

        def root_at(layer, xy):
            pt = Point(xy)
            hit = [j for j in tracer._query(pt.buffer(2)) if nodes[j][0] == layer
                   and geoms[j].distance(pt) <= 2]
            return tracer.dsu.find(hit[0]) if hit else None

        work = []
        for ch in chains:
            L = sum(_length(p.points) for p in ch["tracks"])
            cost = L + via_equiv * len(ch["vias"])
            (al, a), (bl, b) = ch["a"], ch["b"]
            ideal = abs(a[0] - b[0]) + abs(a[1] - b[1]) + (0 if al == bl else via_equiv)
            if cost - ideal > max(min_gain, min_gain_frac * cost):
                work.append((cost / max(ideal, 1.0), cost, L, ch))
        work.sort(key=lambda t: -t[0])
        res.setdefault("chains", len(chains))
        res.setdefault("candidates", len(work))
        added: List[Tuple[int, int, object]] = []      # (layer, root, geom)
        changed = 0
        for n_done, (_ratio, old_cost, old_len, ch) in enumerate(work):
            if max_changes is not None and res["tracks"] >= max_changes:
                break
            if progress is not None:
                progress(n_done, len(work), res["tracks"])
            (al, a), (bl, b) = ch["a"], ch["b"]
            root0 = root_at(ch["tracks"][0].layer, ch["tracks"][0].points[0])
            if root0 is None:
                why("no net")
                continue
            if pad_nets:
                mine = root_names.get(root0, set())
                if len(mine) != 1:
                    why("unnamed or shorted net")
                    continue
                root = next(iter(mine))

                def is_other(j, _mine=mine):
                    return root_names.get(tracer.dsu.find(j), set()) != _mine
            else:
                root = root0

                def is_other(j, _r=root0):
                    return tracer.dsu.find(j) != _r
            w = float(ch["width"])
            r = w / 2.0
            vsz = via_size                      # the design's one via
            vr = vsz / 2.0 + via_margin         # other nets keep clear of its window
            chain_ids = {id(p) for p in ch["tracks"]}
            if remove_via is None and ch["vias"]:
                continue                        # can't move its vias
            xs_all = [q[0] for p in ch["tracks"] for q in p.points]
            ys_all = [q[1] for p in ch["tracks"] for q in p.points]
            x0, x1 = min(xs_all) - window, max(xs_all) + window
            y0, y1 = min(ys_all) - window, max(ys_all) + window
            # lattice anchored on terminal a
            x0 = a[0] - math.ceil((a[0] - x0) / grid) * grid
            y0 = a[1] - math.ceil((a[1] - y0) / grid) * grid
            nx = int((x1 - x0) // grid) + 1
            ny = int((y1 - y0) // grid) + 1
            if 2 * nx * ny > MAX_CELLS:
                why("too big")
                continue
            xs = x0 + grid * np.arange(nx)
            ys = y0 + grid * np.arange(ny)
            win = box(x0, y0, x1, y1)
            obs = {la: [], lb: []}
            for j in tracer._query(win):
                if nodes[j][0] in obs and is_other(j):
                    obs[nodes[j][0]].append(geoms[j])
            for al_, ar_, ag_ in added:
                if ar_ != root and ag_.intersects(win):
                    obs[al_].append(ag_)
            other = {l: (unary_union(obs[l]) if obs[l] else None) for l in lays}
            blocked, via_block = [], None
            for l in lays:
                parts = []
                if other[l] is not None:
                    parts.append(other[l].buffer(r + clearance - slack))
                if ko_union.get(l) is not None:
                    parts.append(ko_union[l].buffer(r))
                m = _mask(unary_union(parts) if parts else None, xs, ys)
                kin = keepin_b if (l == lb and keepin_b is not None) else keepin
                if kin is not None:
                    m |= ~_mask(kin.buffer(-r), xs, ys)
                blocked.append(m)
            vparts = []
            for l in lays:
                if other[l] is not None:
                    vparts.append(other[l].buffer(vr * 1.42 + clearance - slack))
                if ko_union.get(l) is not None:
                    vparts.append(ko_union[l].buffer(vr * 1.42))
            via_block = _mask(unary_union(vparts) if vparts else None, xs, ys)
            vkin = keepin_b if keepin_b is not None else keepin
            if vkin is not None:
                via_block |= ~_mask(vkin.buffer(-vr * 1.42), xs, ys)
            via_ok = ~via_block
            li = {la: 0, lb: 1}

            def cell_of(xy):
                return (min(max(int(round((xy[0] - x0) / grid)), 0), nx - 1),
                        min(max(int(round((xy[1] - y0) / grid)), 0), ny - 1))

            si, sj = cell_of(a)
            gi, gj = cell_of(b)
            rc = max(1, int(math.ceil(w / grid)))
            _clear(blocked[li[al]], si, sj, rc)
            _clear(blocked[li[bl]], gi, gj, rc)
            path = _maze((li[al], si, sj), (li[bl], gi, gj), blocked, via_ok, xs, ys,
                         via_cells=via_equiv / grid)
            if path is None:
                why("no route")
                continue
            # cell path -> runs per layer + via points
            runs, vpts = [], []
            cur_l, cur = path[0][0], [(xs[path[0][1]], ys[path[0][2]])]
            for (l, i, j) in path[1:]:
                p_ = (xs[i], ys[j])
                if l != cur_l:
                    runs.append((lays[cur_l], cur))
                    vpts.append(p_)
                    cur_l, cur = l, [p_]
                else:
                    cur.append(p_)
            runs.append((lays[cur_l], cur))
            runs[0][1][0] = a
            runs[-1][1][-1] = b
            runs = [(l, _dedupe(pts)) for l, pts in runs]
            # the goal may sit off the lattice: square up its last segment
            l_last, pts_last = runs[-1]
            if len(pts_last) >= 3:
                p_, q_, e_ = list(pts_last[-3]), list(pts_last[-2]), pts_last[-1]
                if q_[0] != e_[0] and q_[1] != e_[1]:
                    if p_[0] == q_[0]:
                        q_[1] = e_[1]
                    else:
                        q_[0] = e_[0]
                    runs[-1] = (l_last, _dedupe(pts_last[:-2] + [tuple(q_), e_]))
            new_len = sum(_length(pts) for _l, pts in runs if len(pts) >= 2)
            new_cost = new_len + via_equiv * len(vpts)
            if new_cost > old_cost - max(min_gain, min_gain_frac * old_cost):
                why("not shorter")
                continue
            # exact checks
            near_ends = Point(a).buffer(w).union(Point(b).buffer(w))
            new_geo = []
            ok = True
            for l, pts in runs:
                if len(pts) < 2:
                    continue
                g = LineString(pts).buffer(r)
                new_geo.append((l, g))
            for vx, vy in vpts:
                pad = box(vx - vr, vy - vr, vx + vr, vy + vr)
                new_geo += [(la, pad), (lb, pad)]
                if vkin is not None and not vkin.buffer(1).contains(pad):
                    ok = False
            for l, g in new_geo:
                if not ok:
                    break
                kin = keepin_b if (l == lb and keepin_b is not None) else keepin
                if kin is not None and not kin.buffer(1).contains(g):
                    ok = False
                    break
                if ko_union.get(l) is not None and ko_union[l].intersects(g.difference(near_ends)):
                    ok = False
                    break
                for j in tracer._query(g.buffer(clearance)):
                    if nodes[j][0] == l and is_other(j) and \
                            geoms[j].distance(g) < clearance - 0.5:
                        ok = False
                        break
                if ok:
                    for al_, ar_, ag_ in added:
                        if al_ == l and ar_ != root and ag_.distance(g) < clearance - 0.5:
                            ok = False
                            break
            if not ok:
                why("failed exact check")
                continue
            if vpts and make_via is None:
                continue
            # replace the chain
            cap = ch["tracks"][0].cap
            for v in ch["vias"]:
                remove_via(v)
            cell.shapes[:] = [sh for sh in cell.shapes if id(sh) not in chain_ids]
            for l, pts in runs:
                if len(pts) >= 2:
                    cell.shapes.append(Path(layer=l, points=list(pts), width=int(w), cap=cap))
            for vx, vy in vpts:
                make_via(int(round(vx)), int(round(vy)), vsz)
            for l, g in new_geo:
                added.append((l, root, g))
            res["tracks"] += 1
            res["before"] += old_len
            res["after"] += new_len
            res["vias_before"] += len(ch["vias"])
            res["vias_after"] += len(vpts)
            changed += 1
        if not changed:
            break
    return res
