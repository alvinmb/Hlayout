# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Constructive placement: parts one at a time, the most connected first,
each where it is closest to what is already placed WITHOUT boxing in any
pad already placed.

Order: by number of connections (the pins that carry a net shared with
another part or an I/O pad), highest first -- the dense parts (a die, an
op-amp) go down first and the rest gather round them; ties go to the part
with more connections to parts already placed.

For each part, every grid position (``step``) and orientation is scored by
its net length to the parts already placed and the I/O pads (MST of each
net over the placed pins). The cheapest legal spots (``keep``) then get a
pocket check on a routing grid: copper and part bodies grown by half a
track and a spacing are walls; a pin is open if the free space round its
side of the part reaches the main free area. A spot that closes any pin --
the new part's own or one already placed -- pays ``pocket_cost`` um of
wire per pin closed. Fixed and locked parts stay where they are.

Spots are judged by the routability check itself (hlayout.scripting.
routability) on the parts placed so far. Repair: when even the best spot
for a part walls a connection in, each part walling it in is lifted and
placed again round the new one, and kept there if fewer connections are
walled in (up to ``repair`` tries a part).

``constructive_place(session, netlist)`` -> {"order", "closed", "placed",
"length", "legal"} and the design is updated (one undo step)."""
import math

import numpy as np

from ..core.autoplace import mst, rot_pt, seg_crossings
from ..core.errors import LayoutError


class _Pocket:
    """Free-space grid for the pocket check."""

    def __init__(self, region, cell, grow):
        self.x0, self.y0 = region[0], region[1]
        self.c = float(cell)
        self.nx = max(1, int(math.ceil((region[2] - region[0]) / cell)))
        self.ny = max(1, int(math.ceil((region[3] - region[1]) / cell)))
        self.grow = float(grow)

    def mask(self, boxes):
        m = np.zeros((self.ny, self.nx), bool)
        for b in boxes:
            i0 = max(0, int((b[0] - self.grow - self.x0) // self.c))
            i1 = min(self.nx - 1, int((b[2] + self.grow - self.x0) // self.c))
            j0 = max(0, int((b[1] - self.grow - self.y0) // self.c))
            j1 = min(self.ny - 1, int((b[3] + self.grow - self.y0) // self.c))
            if i1 >= i0 and j1 >= j0:
                m[j0:j1 + 1, i0:i1 + 1] = True
        return m

    def cell(self, x, y):
        return (min(self.ny - 1, max(0, int((y - self.y0) // self.c))),
                min(self.nx - 1, max(0, int((x - self.x0) // self.c))))


def constructive_place(session, netlist, cell_name=None, step: float = 0.0, keep: int = 8,
                       pocket_cost: float = 20000.0, repair: int = 8, on_line=None, cancel=None,
                       **settings) -> dict:
    from scipy import ndimage

    from .autoplace_session import _copper_space, _track_width, apply, build_job
    say = on_line or (lambda _m: None)
    placer, info = build_job(session, netlist, cell_name=cell_name, settings=settings)
    # one undo step: every trial position below is written straight into the
    # cell, so the whole run sits inside the batch
    with session._batch():
        apply(session, placer, info)          # net names from the netlist, first
        r = _run(session, placer, info, step, keep, pocket_cost, say, cancel, repair)
    session._notify()
    return r


def _run(session, placer, info, step, keep, pocket_cost, say, cancel, repair=8):
    from scipy import ndimage

    from .autoplace_session import _copper_space, _track_width, apply
    parts, nets, region = placer.parts, placer.nets, placer.region
    g = float(placer.grid or 50.0)
    step = float(step) or max(g, 4 * g)
    clr = max(float(info["settings"].get("clearance", 0) or 0), _copper_space(session))
    track, space = _track_width(session), _copper_space(session)
    # a routing channel is 3 track widths (track + a track's width either
    # side): walls grow by half of it, so a gap narrower than a channel shuts
    pk = _Pocket(region, 100.0, 1.5 * track)

    # connections per part: pins on nets that reach another part or an I/O pad
    n_conn = [0] * len(parts)
    for nt in nets:
        if len(nt.members) + len(nt.anchors) < 2:
            continue
        for i, _p in nt.members:
            n_conn[i] += 1
    placed = [p.fixed for p in parts]
    order = []
    todo = [i for i in range(len(parts)) if not parts[i].fixed]
    part_nets = placer.part_nets

    def links(i):
        return sum(1 for k in part_nets[i] for j, _p in nets[k].members if placed[j] and j != i)
    while todo:
        todo.sort(key=lambda i: (-n_conn[i], -links(i), parts[i].ref))
        order.append(todo.pop(0))
        placed[order[-1]] = True             # for the tie-break of the next pick only
    placed = [p.fixed for p in parts]

    def body(p, x=None, y=None, rot=None):
        b = p.world_bbox(x, y, rot)          # clearance included: shrink back to the part
        return (b[0] + clr / 2, b[1] + clr / 2, b[2] - clr / 2, b[3] - clr / 2)

    def overlaps(i, x, y, rot):
        b = parts[i].world_bbox(x, y, rot)
        if b[0] < region[0] or b[1] < region[1] or b[2] > region[2] or b[3] > region[3]:
            return True
        for o in placer.obstacles:
            if b[0] < o[2] and b[2] > o[0] and b[1] < o[3] and b[3] > o[1]:
                return True
        for j, q in enumerate(parts):
            if j == i or not placed[j]:
                continue
            c = q.world_bbox()
            if b[0] < c[2] and b[2] > c[0] and b[1] < c[3] and b[3] > c[1]:
                return True
        return False

    def net_cost(i, x, y, rot):
        p = parts[i]
        x0, y0, r0 = p.x, p.y, p.rot
        p.x, p.y, p.rot = x, y, rot
        c = 0.0
        for k in part_nets[i]:
            nt = nets[k]
            pts = [parts[j].pin_xy(pp) for j, pp in nt.members if placed[j] or j == i]
            pts += list(nt.anchors)
            if len(pts) >= 2:
                c += nt.weight * mst(pts)[0]
        p.x, p.y, p.rot = x0, y0, r0
        return c

    def closed_pins(extra=None):
        """Placed pins (with a net) whose way out is shut."""
        ids = [j for j in range(len(parts)) if placed[j]]
        if extra is not None:
            ids.append(extra)
        boxes = [body(parts[j]) for j in ids]
        wall = pk.mask(boxes)
        free = ~wall
        lab, n = ndimage.label(free)
        if n == 0:
            return 10 ** 6
        sizes = np.bincount(lab.ravel())
        sizes[0] = 0
        main = int(np.argmax(sizes))
        r = int(math.ceil(3.0 * track / pk.c)) + 1
        near = {}

        def labels(j, pin):
            p = parts[j]
            b = body(p)
            px, py = p.pin_xy(pin)
            # step out of the body towards the pin's nearest edge
            d = [px - b[0], b[2] - px, py - b[1], b[3] - py]
            e = int(np.argmin(d))
            ox, oy = [(-1, 0), (1, 0), (0, -1), (0, 1)][e]
            sx = b[0] if e == 0 else (b[2] if e == 1 else px)
            sy = b[1] if e == 2 else (b[3] if e == 3 else py)
            jy, ix = pk.cell(sx + ox * (pk.grow + pk.c), sy + oy * (pk.grow + pk.c))
            sub = lab[max(0, jy - r):jy + r + 1, max(0, ix - r):ix + r + 1]
            return set(int(v) for v in np.unique(sub) if v > 0)
        for j in ids:
            for k in part_nets[j]:
                for jj, pin in nets[k].members:
                    if jj == j:
                        near[(j, pin)] = labels(j, pin)
        bad = 0
        for (j, pin), ls in near.items():
            if main in ls:
                continue
            # a pocket is fine if the whole net (placed so far) is in it
            ok = False
            for k in part_nets[j]:
                nt = nets[k]
                if not any(jj == j and pp == pin for jj, pp in nt.members):
                    continue
                if nt.anchors:
                    break
                others = [near.get((jj, pp), set()) for jj, pp in nt.members
                          if (jj, pp) != (j, pin) and (jj in ids)]
                ok = bool(ls) and all(ls & o for o in others)
                if len(others) < sum(1 for _ in nt.members) - 1:
                    ok = False                # members still to come need a way in
            if not ok:
                bad += 1
        return bad

    xs = np.arange(region[0], region[2] + 1, step)
    ys = np.arange(region[1], region[3] + 1, step)
    log = []
    cw = float(info["settings"].get("sli_crossing", 3000.0) or 0.0)
    cell = session.library.cells[info["cell"]]
    by_ref = {r.label: r for r in cell.srefs if r.label}
    from .routability import check

    def segs_of(k, extra=None):
        nt = nets[k]
        pts = [parts[j].pin_xy(pp) for j, pp in nt.members if placed[j] or j == extra]
        pts += list(nt.anchors)
        if len(pts) < 2:
            return []
        _l, ed = mst(pts)
        return [(pts[a_], pts[b_]) for a_, b_ in ed]

    from .autoplace_session import _xf
    from .fanout import carry_bonds

    def put(j):
        """Move part j's instance to its trial spot -- its wire bonds and bond
        fingers with it, as Auto Place's apply() does."""
        r = by_ref.get(parts[j].ref)
        if r is None:
            return
        o = (int(round(parts[j].x)), int(round(parts[j].y)))
        if tuple(r.origin) == o and float(r.rotation) == float(parts[j].rot):
            return
        f_old = _xf(r)
        r.origin = o
        r.rotation = float(parts[j].rot)
        try:
            carry_bonds(session, info["cell"], {r.label: (f_old, _xf(r), r.cell_name)})
        except Exception:
            pass

    def real_check(extra):
        """The routability check on the parts placed so far (+ ``extra``):
        the others are taken off the board while it looks."""
        keep_refs = {parts[j].ref for j in range(len(parts)) if placed[j] or j == extra}
        saved = list(cell.srefs)
        cell.srefs[:] = [r for r in saved if not r.label or r.label in keep_refs
                         or r.label not in {p.ref for p in parts}]
        try:
            return check(session, cell_name=info["cell"])
        finally:
            cell.srefs[:] = saved

    for j in range(len(parts)):
        if placed[j]:
            put(j)
    ref_ix = {p.ref: j for j, p in enumerate(parts)}
    def place_one(i, prev):
        """Best spot for part i given the parts placed now: (score, x, y, rot,
        report) -- the part is left there, on the board."""
        p = parts[i]
        cands = []
        mine = set(part_nets[i])
        other_segs = {k: segs_of(k) for k in range(len(nets)) if k not in mine}
        for rot in (0, 90, 180, 270):
            for y in ys:
                for x in xs:
                    x_, y_ = round(x / g) * g, round(y / g) * g
                    if overlaps(i, x_, y_, rot):
                        continue
                    cands.append((net_cost(i, x_, y_, rot), x_, y_, rot))
        if not cands:
            raise LayoutError(f"no room left for {p.ref} -- a bigger substrate or a smaller "
                              "clearance")
        cands.sort()
        limit = max(1, int(keep))
        short = []
        for c, x_, y_, rot in cands[:limit * 8]:
            p.x, p.y, p.rot = x_, y_, rot
            xs_ = 0
            if cw:
                new = {k: segs_of(k, extra=i) for k in mine}
                ks = sorted(new)
                for a_ in range(len(ks)):
                    for o in other_segs.values():
                        xs_ += seg_crossings(new[ks[a_]], o)
                    for b_ in range(a_ + 1, len(ks)):
                        xs_ += seg_crossings(new[ks[a_]], new[ks[b_]])
            short.append((c + cw * xs_, x_, y_, rot))
        short.sort()
        best = None
        k_ = 0
        # the cheapest spots first; look further down (up to five times as
        # far) while every spot so far walls something new in
        while k_ < min(len(short), limit * 5):
            sc, x_, y_, rot = short[k_]
            k_ += 1
            p.x, p.y, p.rot = x_, y_, rot
            put(i)
            rep = real_check(i)
            dw = rep["walled"] - prev[0]
            dx = rep["no_xo"] - prev[1]
            score = sc + pocket_cost * max(0, dw) + cw * 2 * max(0, dx)
            if best is None or score < best[0]:
                best = (score, x_, y_, rot, rep)
            if k_ >= limit and best[4]["walled"] <= prev[0] and best[4]["no_xo"] <= prev[1]:
                break
        _s, p.x, p.y, p.rot, rep = best
        put(i)
        return best

    def placed_check():
        return real_check(None)

    prev = (0, 0)
    for n_done, i in enumerate(order, 1):
        if cancel and cancel():
            break
        p = parts[i]
        placed[i] = False
        best = place_one(i, prev)
        rep = best[4]
        p.placed = True
        placed[i] = True
        # repair: the best spot still walls something in -- lift each part
        # walling it in and place it again round the new one; keep it if
        # fewer connections are walled in
        tries = 0
        while rep["walled"] > prev[0] and tries < int(repair):
            blockers = []
            for w in rep.get("walled_rows") or []:
                for b in list(w["blockers"]) + list(w["own"]):
                    jb = ref_ix.get(b)
                    if jb is not None and jb != i and placed[jb] and not parts[jb].fixed \
                            and jb not in blockers:
                        blockers.append(jb)
            improved = False
            for jb in blockers[:4]:
                tries += 1
                q = parts[jb]
                old = (q.x, q.y, q.rot)
                placed[jb] = False
                nb = place_one(jb, (rep["walled"], rep["no_xo"]))
                placed[jb] = True
                r2 = placed_check()
                if (r2["walled"], r2["no_xo"]) < (rep["walled"], rep["no_xo"]):
                    msg = (f"   repair: {q.ref} moved to {q.x / 1000:.2f}, {q.y / 1000:.2f} mm "
                           f"-- walled in {rep['walled']} -> {r2['walled']}")
                    log.append(msg)
                    say(msg)
                    rep = r2
                    improved = True
                    break
                q.x, q.y, q.rot = old
                put(jb)
            if not improved:
                break
        prev = (rep["walled"], rep["no_xo"])
        msg = (f"{n_done}/{len(order)} {p.ref} ({n_conn[i]} connections) at "
               f"{p.x / 1000:.2f}, {p.y / 1000:.2f} mm, {p.rot} deg -- "
               f"{rep['walled']} walled in, {rep['no_xo']} crossing(s) with no crossover room so far")
        log.append(msg)
        say(msg)
    bad, out = placer.overlaps()
    closed = closed_pins()
    apply(session, placer, info)
    return {"order": [parts[i].ref for i in order], "closed": closed, "placed": len(order),
            "length": placer.wirelength(), "legal": not bad and not out, "log": log,
            "overlaps": bad, "outside": out}
