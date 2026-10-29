"""Remove jogs from routed tracks.

A jog is a short sideways step in an otherwise straight run -- the track
goes along, steps across by a little, and carries on in the same
direction: three segments A, J, B with A and B parallel and pointing the
same way, J short. Routers leave them where a track had to dodge
something that has since moved, or at the join of two tracks laid
separately. They cost a bend each side, crowd the neighbours and read
badly on the artwork.

remove_jogs() takes each one out by sliding one side across: segment A
moves sideways by J (its far corner sliding along the segment before it,
which must run along J's direction so the track stays square), or B the
other way, so A and B meet in line and J vanishes. The move is only made
when:

  * the jog is no longer than ``max_jog``;
  * the segments that move or change have nothing else on them -- no
    other track, via, pad or bond finger joins there (it would come
    loose); an end of the track may only slide within the pad, finger or
    via pad it ends on, staying wholly inside it;
  * the moved copper keeps ``clearance`` from every other net's copper
    on its layer (pads and fingers inside placed parts included), stays
    out of the routing keep-outs, inside the keep-in (the substrate) and,
    on the 2nd conductor, inside the printed dielectric.

Of the two ways, the one that shortens the track is tried first. Tracks
joined end to end with nothing else at the join are treated as one, so a
jog straddling the join goes too. Nets stay exactly as joined as before.
"""

import math
from typing import Dict, List, Optional, Tuple

from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .drc import _collect_geoms, _sref_point_transform
from .nettrace import NetTracer
from .shapes import MAX_PATH_POINTS, Path


def _cross(u, v):
    return u[0] * v[1] - u[1] * v[0]


def _dot(u, v):
    return u[0] * v[0] + u[1] * v[1]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _add(a, v):
    return (a[0] + v[0], a[1] + v[1])


def _parallel(u, v):
    n = math.hypot(*u) * math.hypot(*v)
    return n > 0 and abs(_cross(u, v)) <= 1e-9 * n


def _clean(pts):
    """Drop repeated points and the middle of straight runs."""
    out = []
    for p in pts:
        p = (int(round(p[0])), int(round(p[1])))
        if not out or p != out[-1]:
            out.append(p)
    k = 1
    while k < len(out) - 1:
        u, v = _sub(out[k], out[k - 1]), _sub(out[k + 1], out[k])
        if _parallel(u, v) and _dot(u, v) > 0:
            del out[k]
        else:
            k += 1
    return out


def _polylines(cell, lays, via_xy, other_at):
    """[(layer, width, [paths], [points])] -- each routed track, and runs
    of tracks joined end to end where nothing else is at the join (same
    layer and width, no via, no pad, no third track)."""
    tracks = [p for p in cell.shapes if isinstance(p, Path) and p.layer in lays
              and len(p.points) >= 2]
    ends: Dict[tuple, list] = {}
    for t in tracks:
        for e in (0, -1):
            ends.setdefault((t.layer, tuple(map(round, t.points[e]))), []).append(t)

    def joinable(layer, xy, pair):
        a, b = pair
        return (a is not b and a.width == b.width and xy not in via_xy
                and not other_at(layer, xy, pair))

    seen, out = set(), []
    for t in tracks:
        if id(t) in seen:
            continue
        # walk back to the start of the run, then forward
        start, back = t, [tuple(map(round, t.points[0])), tuple(map(round, t.points[-1]))]
        cur, at_end = t, back[0]
        guard = 0
        while guard < 10000:
            guard += 1
            nb = ends.get((cur.layer, at_end), [])
            if len(nb) != 2 or not joinable(cur.layer, at_end, nb):
                break
            nxt = nb[0] if nb[1] is cur else nb[1]
            if id(nxt) in seen or nxt is start and guard > 1:
                break
            far = tuple(map(round, nxt.points[-1])) if tuple(map(round, nxt.points[0])) == at_end \
                else tuple(map(round, nxt.points[0]))
            cur, at_end = nxt, far
            if cur is t:
                break
        # cur is the first track of the run; at_end is its outer end
        paths, pts = [], []
        node, here = cur, at_end
        guard = 0
        while node is not None and id(node) not in seen and guard < 10000:
            guard += 1
            seen.add(id(node))
            p = [tuple(q) for q in node.points]
            if tuple(map(round, p[-1])) == here:
                p = p[::-1]
            pts = pts + (p if not pts else p[1:])
            paths.append(node)
            here = tuple(map(round, p[-1]))
            nb = ends.get((node.layer, here), [])
            if len(nb) != 2 or not joinable(node.layer, here, nb):
                break
            node = nb[0] if nb[1] is node else nb[1]
        out.append((cur.layer, cur.width, paths, pts))
    return out


def remove_jogs(library, cell, layers: Tuple[int, int], clearance: float,
                max_jog: float = 1000.0, keepouts: Optional[Dict[int, list]] = None,
                keepin=None, keepin_b=None, dielectric_layer: Optional[int] = None,
                fill_datatype: int = 1, pad_nets: Optional[dict] = None,
                max_passes: int = 4, progress=None) -> Dict:
    """Take the jogs out of ``cell``'s routing on ``layers`` (see the
    module doc); edits it in place. ``keepouts`` = {layer: [geometry]};
    ``keepin`` the area tracks stay in, ``keepin_b`` the area the 2nd
    conductor stays in. ``pad_nets`` ({"REF.PIN": net}) makes "another
    net" mean copper carrying a different design net. Returns {"jogs":
    removed, "tracks": tracks rewritten, "before": um, "after": um,
    "found": jogs seen, "why": {reason: count}}."""
    la, lb = int(layers[0]), int(layers[1])
    lays = (la, lb)
    keepouts = keepouts or {}
    ko = {l: (unary_union(g) if g else None) for l, g in keepouts.items()}
    res = {"jogs": 0, "tracks": 0, "before": 0.0, "after": 0.0, "found": 0, "why": {}}

    def why(k):
        res["why"][k] = res["why"].get(k, 0) + 1

    inst = {la: [], lb: []}
    for ref in cell.srefs:
        acc = []
        _collect_geoms(library, ref.cell_name, _sref_point_transform(ref), acc)
        for lay, g in acc:
            if lay in inst:
                inst[lay].append(g)

    for _pass in range(max_passes):
        tracer = NetTracer(library, cell, list(lays), dielectric_layer=dielectric_layer,
                           fill_datatype=fill_datatype)
        geoms, nodes = tracer._geoms, tracer.nodes
        names = {}
        if pad_nets:
            for pin, net in pad_nets.items():
                if pin in tracer.pin_node:
                    names.setdefault(tracer.dsu.find(tracer.pin_node[pin]), set()).add(net)

        # every piece of copper on a layer, with the Path it came from (if any)
        copper = {l: [] for l in lays}          # (geometry, path-or-None)
        for sh in cell.shapes:
            if sh.layer not in lays or not hasattr(sh, "points"):
                continue
            if isinstance(sh, Path):
                if len(sh.points) >= 2:
                    copper[sh.layer].append((LineString(sh.points).buffer(sh.width / 2.0), sh))
            elif len(sh.points) >= 3:
                from shapely.geometry import Polygon as _P
                g = _P(sh.points)
                if not g.is_valid:
                    g = g.buffer(0)
                copper[sh.layer].append((g, None))
        for l in lays:
            copper[l] += [(g, None) for g in inst[l]]
        trees = {l: STRtree([g for g, _p in copper[l]]) if copper[l] else None for l in lays}

        from ..io.specctra import find_vias, via_cell_sites
        via_xy = {(round(x), round(y)) for x, y, _w in find_vias(cell, lays)}
        via_xy |= {(round(x), round(y)) for x, y, _w, _r in via_cell_sites(library, cell, lays)}

        def touching(layer, geom, own_ids):
            """Copper on ``layer`` meeting ``geom`` that isn't the track's own."""
            t = trees.get(layer)
            if t is None:
                return []
            out = []
            for i in t.query(geom):
                g, p = copper[layer][int(i)]
                if p is not None and id(p) in own_ids:
                    continue
                if g.intersects(geom):
                    out.append(g)
            return out

        def other_at(layer, xy, pair):
            ids = {id(p) for p in pair}
            return bool(touching(layer, Point(xy).buffer(1), ids))

        def root_of(layer, xy):
            pt = Point(xy)
            hit = [j for j in tracer._query(pt.buffer(2)) if nodes[j][0] == layer
                   and geoms[j].distance(pt) <= 2]
            return tracer.dsu.find(hit[0]) if hit else None

        changed = False
        added = []                  # (layer, net key, geometry) moved this pass
        polys = _polylines(cell, lays, via_xy, other_at)
        for n_done, (layer, width, paths, pts) in enumerate(polys):
            if progress is not None:
                progress(n_done, len(polys), res["jogs"])
            if len(pts) < 4:
                continue
            r = width / 2.0
            own = {id(p) for p in paths}
            root = root_of(layer, pts[0])
            mine = names.get(root, set()) if pad_nets else None
            kin = keepin_b if (layer == lb and keepin_b is not None) else keepin

            def foreign_other_net(g):
                """Other nets' copper near ``g`` (within the clearance)."""
                probe = g.buffer(clearance)
                bad = []
                for j in tracer._query(probe):
                    if nodes[j][0] != layer:
                        continue
                    rj = tracer.dsu.find(j)
                    if pad_nets:
                        if names.get(rj, set()) == mine and mine:
                            continue
                    elif rj == root:
                        continue
                    if geoms[j].distance(g) < clearance - 1e-6:
                        bad.append(j)
                # copper moved earlier in this pass, at its new place
                key = frozenset(mine) if pad_nets else root
                for l_, k_, g_ in added:
                    if l_ == layer and k_ != key and g_.distance(g) < clearance - 1e-6:
                        bad.append(-1)
                return bad

            def ok_new(seg_pts):
                g = LineString(seg_pts).buffer(r)
                if foreign_other_net(g):
                    return "clearance"
                if ko.get(layer) is not None and ko[layer].intersects(g):
                    return "keep-out"
                if kin is not None and not kin.buffer(1).contains(g):
                    return "keep-in"
                return None

            def loose(seg_pts):
                """Something else attached along ``seg_pts`` (it would detach)."""
                if seg_pts[0] == seg_pts[1]:
                    return False
                g = LineString(seg_pts).buffer(max(r - 1, 1))
                return bool(touching(layer, g, own))

            before_len = sum(math.dist(pts[k - 1], pts[k]) for k in range(1, len(pts)))
            new = list(pts)
            fixed_any = False
            guard = 0
            i = 1
            while i < len(new) - 2 and guard < 500:
                guard += 1
                a, b, c, d = new[i - 1], new[i], new[i + 1], new[i + 2]
                v1, v2, v3 = _sub(b, a), _sub(c, b), _sub(d, c)
                if not (_parallel(v1, v3) and _dot(v1, v3) > 0 and math.hypot(*v2) > 0
                        and not _parallel(v1, v2)):
                    i += 1
                    continue
                if math.hypot(*v2) > max_jog:
                    i += 1
                    continue
                res["found"] += 1
                last = len(new) - 1
                options = []

                def end_slides(pt, to):
                    """The terminal copper an END of the track sits in (a pad,
                    a finger, a via pad) still holds it after the slide."""
                    holder = [g for g in touching(layer, Point(pt).buffer(1), own)
                              if g.contains(Point(pt))]
                    return [g for g in holder if g.buffer(-r).contains(Point(to))
                            or g.contains(LineString([pt, to]).buffer(r))]

                # A slides by +v2 (points i-1, i); B slides by -v2 (i+1, i+2)
                for side, k0, k1, mv in (("A", i - 1, i, v2), ("B", i + 1, i + 2, (-v2[0], -v2[1]))):
                    outer = k0 if side == "A" else k1          # the corner away from the jog
                    q_out = _add(new[outer], mv)
                    if outer in (0, last):
                        held = end_slides(new[outer], q_out)
                        if not held:
                            continue
                        nb = None
                        gain = math.hypot(*v2)                 # the jog goes, nothing grows
                    else:
                        nb = outer - 1 if side == "A" else outer + 1
                        if not _parallel(_sub(new[outer], new[nb]), v2):
                            continue
                        if _dot(_sub(q_out, new[nb]), _sub(new[outer], new[nb])) <= 0:
                            continue                           # the neighbour would reverse
                        held = []
                        gain = (math.dist(new[nb], new[outer]) + math.hypot(*v2)
                                - math.dist(new[nb], q_out))
                    options.append((gain, side, k0, k1, mv, nb, held))
                if not options:
                    why("an end of the track would move")
                    i += 1
                    continue
                options.sort(key=lambda o: -o[0])
                done = False
                for _gain, side, k0, k1, mv, nb, held in options:
                    p0, p1 = new[k0], new[k1]
                    q0, q1 = _add(p0, mv), _add(p1, mv)
                    outer = k0 if side == "A" else k1
                    moving = [(p0, p1), (b, c)]
                    new_parts = [(q0, q1)]
                    if nb is not None:
                        far = new[nb]
                        new_parts.append((far, q0 if side == "A" else q1))
                    # nothing else may hang off the copper that moves or goes:
                    # the moving segment, the jog, and the part of the
                    # neighbour that shrinks (its far end stays put); an end
                    # sliding inside its own pad may of course touch that pad
                    ok_attach = True
                    for seg in moving:
                        g = LineString(seg).buffer(max(r - 1, 1))
                        hits = touching(layer, g, own)
                        if held:
                            hits = [h for h in hits if not any(h.equals(x) for x in held)]
                        if hits:
                            ok_attach = False
                            break
                    if ok_attach and nb is not None:
                        # the neighbour keeps its far end; when it shrinks,
                        # the piece cut off (old corner back to the new one)
                        # must have nothing on it
                        far, old_c = new[nb], new[outer]
                        new_c = q0 if side == "A" else q1
                        if math.dist(far, new_c) < math.dist(far, old_c):
                            cut = LineString([new_c, old_c]).buffer(max(r - 1, 1))
                            cut = cut.difference(LineString([far, new_c]).buffer(r))
                            if not cut.is_empty and touching(layer, cut, own):
                                ok_attach = False
                    if not ok_attach:
                        why("something joins the moving copper")
                        continue
                    bad = None
                    for seg in new_parts:
                        if seg[0] != seg[1]:
                            bad = ok_new(seg)
                            if bad:
                                break
                    if bad:
                        why(bad)
                        continue
                    new[k0], new[k1] = q0, q1
                    new = _clean(new)
                    res["jogs"] += 1
                    fixed_any = True
                    done = True
                    break
                if done:
                    i = max(1, i - 2)
                else:
                    i += 1
            if not fixed_any:
                continue
            after_len = sum(math.dist(new[k - 1], new[k]) for k in range(1, len(new)))
            proto = paths[0]
            idx = min(cell.shapes.index(p) for p in paths)
            cell.shapes[:] = [s_ for s_ in cell.shapes if id(s_) not in own]
            chunks, start = [], 0
            while start < len(new) - 1:
                end = min(start + MAX_PATH_POINTS - 1, len(new) - 1)
                chunks.append(new[start:end + 1])
                start = end
            for k, ch in enumerate(chunks):
                cell.shapes.insert(idx + k, Path(layer=layer, points=ch, width=width,
                                                 cap=proto.cap, datatype=proto.datatype))
            res["tracks"] += 1
            res["before"] += before_len
            res["after"] += after_len
            added.append((layer, frozenset(mine) if pad_nets else root,
                          LineString(new).buffer(r)))
            changed = True
        if not changed:
            break
    return res
