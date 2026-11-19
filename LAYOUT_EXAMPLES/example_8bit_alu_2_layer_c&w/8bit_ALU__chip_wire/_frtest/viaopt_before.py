# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Via optimisation for a routed two-conductor thick-film hybrid.

Runs after the autorouter. Every via costs a dielectric window, a print
registration and a reliability risk, and a router that keeps each
conductor to one track direction (Freerouting with ``layer_directions``)
puts one in at nearly every change of direction. This pass takes out
each via it safely can. For every via, the tracks on ONE side of it --
the side needing the shorter move first -- move onto the OTHER conductor
and the via (both pads and its dielectric window) is deleted, when:

  * every track on that side ENDS at the via (a track that only passes
    over it, or a pad / bond finger under it, still needs it);
  * each moved track's far end lands on copper of the same net on the
    new layer (a pad or finger there, or another via), and nothing else
    it touched on its old layer -- a pad, a T-junction -- is left behind
    (a via there is fine: it reaches both layers);
  * the moved tracks still touch whatever the via joined them to on the
    new layer (its pad goes, so the copper must meet);
  * the moved copper keeps ``clearance`` from every other net's copper
    on the new layer;
  * onto the 2nd conductor, it stays on the printed dielectric (off the
    dielectric only the 1st conductor may run); and
  * (optional) the moved tracks total no more than ``max_flip``.

A via with copper on one side only (left over once its other side has
moved) is simply deleted when the tracks it joined still touch.

Repeats until no via can go. Nets come from the copper itself
(hlayout.core.nettrace), so it needs no netlist and never joins two nets.
"""

from typing import Dict, List, Optional, Tuple

from shapely.geometry import LineString, Point, box
from shapely.geometry import Polygon as _SPoly
from shapely.ops import unary_union

from .drc import _collect_geoms, _sref_point_transform
from .nettrace import NetTracer, _DSU
from .shapes import Path, Polygon, Rectangle


def _ends(p: Path):
    return p.points[0], p.points[-1]


def _near(a, b, tol) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _geom(sh):
    if isinstance(sh, Path):
        return LineString(sh.points).buffer(sh.width / 2.0) if len(sh.points) >= 2 else None
    if isinstance(sh, Rectangle):
        return box(*sh.bbox())
    if isinstance(sh, Polygon):
        return _SPoly(sh.points)
    return None


def _via_shapes(cell, x, y, layers, dielectric_layer):
    """The via's own pads (both conductors) and its dielectric window."""
    out = []
    lays = set(layers) | ({dielectric_layer} if dielectric_layer is not None else set())
    for s in cell.shapes:
        if s.layer not in lays or getattr(s, "pin", "") or not isinstance(s, (Rectangle, Polygon)):
            continue
        if getattr(s, "datatype", 0) != 0:
            continue
        x0, y0, x1, y1 = s.bbox()
        w, h = x1 - x0, y1 - y0
        if w <= 0 or w > 5000 or abs(w - h) > max(2, 0.02 * w):
            continue
        if abs((x0 + x1) / 2 - x) <= 1 and abs((y0 + y1) / 2 - y) <= 1:
            out.append(s)
    return out


def _connected(geoms) -> bool:
    u = unary_union([g.buffer(0.5) for g in geoms])
    return u.geom_type == "Polygon"


def optimize_vias(library, cell, layers: Tuple[int, int], clearance: float,
                  dielectric_layer: Optional[int] = None, dielectric_area=None,
                  fill_datatype: int = 1, max_flip: float = 0.0,
                  max_rounds: int = 1000) -> Dict:
    """Take out every via of ``cell`` that can go (see the module doc).
    ``layers`` = (1st conductor, 2nd conductor); ``dielectric_area`` = a
    shapely geometry the 2nd conductor must stay on (None = anywhere).
    Edits ``cell`` in place. Returns {"before", "after", "removed":
    [(x, y)], "moved": tracks moved to the other layer}."""
    from ..io.specctra import find_vias
    la, lb = int(layers[0]), int(layers[1])
    before = len(find_vias(cell, (la, lb)))
    removed: List[Tuple[float, float]] = []
    moved = 0
    for _round in range(max_rounds):
        tracer = NetTracer(library, cell, [la, lb], dielectric_layer=dielectric_layer,
                           fill_datatype=fill_datatype)
        geoms, nodes = tracer._geoms, tracer.nodes
        # Nets for this pass: copper on one layer is joined where it
        # touches, and the two layers ONLY at a via. (The tracer's own
        # rule -- any overlap not covered by dielectric -- would join
        # every crossing in a design with no dielectric layer, and then
        # nothing would look like another net.)
        dsu = _DSU(len(nodes))
        for vx, vy, vw in find_vias(cell, (la, lb)):
            vb = box(vx - vw / 2.0, vy - vw / 2.0, vx + vw / 2.0, vy + vw / 2.0)
            hit = [j for j in tracer._query(vb) if geoms[j].intersects(vb)]
            for j in hit[1:]:
                dsu.union(hit[0], j)
        tracer.dsu = dsu
        sites = {(vx, vy) for vx, vy, _vw in find_vias(cell, (la, lb))}
        # copper inside placed parts (a die part's own bond fingers, a
        # chip's pads): a moved track must not leave any of it behind either
        inst = {la: [], lb: []}
        for ref in cell.srefs:
            acc = []
            _collect_geoms(library, ref.cell_name, _sref_point_transform(ref), acc)
            for lay, g in acc:
                if lay in inst:
                    inst[lay].append(g)
        done = False
        for x, y, w in sorted(find_vias(cell, (la, lb))):
            centre = Point(x, y)
            under = [j for j in tracer._query(centre) if geoms[j].intersects(centre)]
            if not under:
                continue
            root = tracer.dsu.find(under[0])
            tol = w / 2.0 + 1
            pad = box(x - w / 2.0, y - w / 2.0, x + w / 2.0, y + w / 2.0)
            via = _via_shapes(cell, x, y, (la, lb), dielectric_layer)
            gone = {id(v) for v in via}
            # copper touching the via on each layer, the via's own pads aside
            touch = {la: [], lb: []}
            for sh in cell.shapes:
                if sh.layer in touch and id(sh) not in gone:
                    g = _geom(sh)
                    if g is not None and g.intersects(pad):
                        touch[sh.layer].append((sh, g))
            plans = []
            for src, dst in ((lb, la), (la, lb)):
                src_cu = touch[src]
                if not src_cu:
                    continue
                at = [sh for sh, _g in src_cu if isinstance(sh, Path)
                      and any(_near(e, (x, y), tol) for e in _ends(sh))]
                if len(at) != len(src_cu):
                    continue           # a pad, finger or passing track on this side needs it
                if not touch[dst]:
                    # nothing on the other side: the via only joins these
                    # tracks to each other -- drop it if they still meet
                    if _connected([g for _sh, g in src_cu]):
                        plans.append((0.0, src, dst, []))
                    continue
                length = sum(LineString(p.points).length for p in at)
                if max_flip and length > max_flip:
                    continue
                plans.append((length, src, dst, at))
            for length, src, dst, at in sorted(plans, key=lambda t: t[0]):
                if at:
                    new = unary_union([LineString(p.points).buffer(p.width / 2.0) for p in at])
                    if dst == lb and dielectric_area is not None and \
                            not dielectric_area.buffer(1).contains(new):
                        continue
                    ok = True
                    for p in at:       # far ends land on same-net copper on the new layer
                        a, b = _ends(p)
                        fp = Point(b if _near(a, (x, y), tol) else a)
                        r = p.width / 2.0
                        if not [j for j in tracer._query(fp.buffer(r))
                                if nodes[j][0] == dst and tracer.dsu.find(j) == root
                                and geoms[j].distance(fp) <= r]:
                            ok = False
                            break
                    if not ok:
                        continue
                    # whatever else the tracks touched on their old layer (a
                    # pad at the far end, a T-junction) loses them -- unless
                    # it's a via, which carries on to the new layer too
                    lost = False
                    ids = {id(p) for p in at}
                    for sh in cell.shapes:
                        if sh.layer != src or id(sh) in gone or id(sh) in ids:
                            continue
                        g = _geom(sh)
                        if g is None or not g.intersects(new):
                            continue
                        if isinstance(sh, (Rectangle, Polygon)) and not getattr(sh, "pin", ""):
                            b0, b1, b2, b3 = sh.bbox()
                            if (round((b0 + b2) / 2), round((b1 + b3) / 2)) in sites:
                                continue
                        lost = True
                        break
                    if not lost and any(g.intersects(new) for g in inst[src]):
                        lost = True        # e.g. the bond finger the track starts from
                    if lost:
                        continue
                    if not all(new.buffer(0.5).intersects(g) for _sh, g in touch[dst]):
                        continue       # would no longer meet what the via joined
                    if [j for j in tracer._query(new.buffer(clearance))
                            if nodes[j][0] == dst and tracer.dsu.find(j) != root
                            and geoms[j].distance(new) < clearance - 0.5]:
                        continue       # too close to another net
                    for p in at:
                        p.layer = dst
                    moved += len(at)
                cell.shapes[:] = [sh for sh in cell.shapes if id(sh) not in gone]
                removed.append((x, y))
                done = True
                break
            if done:
                break                  # the copper changed: re-trace, look at every via again
        if not done:
            break
    return {"before": before, "after": len(find_vias(cell, (la, lb))),
            "removed": removed, "moved": moved}
