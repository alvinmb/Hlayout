# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Improve a placement for routing, judged by the router itself.

Auto Place scores a placement by straight-line wire length; that says little
about whether it will route. This pass starts from a placement (Auto Place's,
or a hand placement) and tries small changes, scoring each with a quick
PathFinder route (``iters`` negotiation rounds instead of 60, rescue on, no
nudge) and keeping only the ones that route more:

  * nudge -- the router's own nudge plan (hlayout.scripting.nudge) for the
    connections left open: parts in the way moved apart by the shortfall;
  * shift -- one part on an open connection moved a few grid steps along
    one axis;
  * flip -- a two-pin part turned 180 degrees (its pads change places).

Every change is checked first (nudge._move_ok: on the substrate, bodies off
everything, copper the spacing apart; a die takes its bond fingers) and
locked parts (Session.lock_parts) never move. Score: connections routed,
then fewer crossovers, then shorter straight-line length. A change that
scores no better is undone. The routing is taken out first (the quick
routes are only scores, nothing is drawn); route the result with
Autoroute.
"""
import math
import random
import time
from typing import Optional

from . import hybridroute as H
from . import nudge as NG
from . import pathfinder as PF


def _score(session, cell, layers, iters):
    d, m = H.export_design(session, cell, layers=layers, keep_existing=False)
    full = bool(m.get("full_dielectric"))
    r = PF.route(d, local_dielectric=full or m.get("local_diel", -1) >= 0,
                 cond2_keepouts=None if full else m.get("local_keepouts"),
                 full_dielectric=full, iterations=iters)
    st = r["stats"]
    try:
        sli = session.sli(cell_name=cell, unrouted_only=False)["total"]
    except Exception:
        sli = 0.0
    xo = int(st.get("crossoversAdded", 0)) + int(st.get("windows", 0))
    key = (int(st.get("routed", 0)), -xo, -round(sli))
    return key, d, r


def _refs_on_open(design, unrouted):
    """Part ref-des at either end of the open connections."""
    feats = NG.features(design)
    tid = {}
    for p in design.get("substratePads") or []:
        tid[str(p.get("id"))] = p
    out = []
    for u in unrouted or []:
        for end in (u.get("from"), u.get("to")):
            if not end:
                continue
            ref = str(end).split(".", 1)[0]
            if ref.startswith("F_"):
                ref = ref[2:].rsplit("_", 1)[0]
            if any(f["owner"] == ref for f in feats) and ref not in out:
                out.append(ref)
    return out


def improve_for_routing(session, cell_name: Optional[str] = None, passes: int = 20,
                        iters: int = 15, step: float = 100.0, max_move: float = 500.0,
                        seed: int = 0, layers: Optional[int] = None, on_line=None,
                        cancel=None) -> dict:
    """See the module doc. ``passes`` = changes tried; ``step`` = shift step
    (um); ``max_move`` = the most a nudge moves a part. Each kept change is
    its own undo step. Returns {"before", "after" (routed, connections),
    "kept": [descriptions], "tried", "seconds"}."""
    say = on_line or (lambda _m: None)
    rnd = random.Random(seed)
    cell = cell_name or session.current_cell_name
    layers = H.layers_for(session) if layers is None else int(layers)
    locked = set(session.locked_parts(cell))
    t0 = time.time()
    session.unroute(cell_name=cell)
    best, d, r = _score(session, cell, layers, iters)
    conns = int(r["stats"].get("connections", 0))
    first = best
    say(f"start: {best[0]}/{conns} routed, {-best[1]} crossover(s)")
    kept, tried = [], 0
    by = {x.label: x for x in session.library.get_cell(cell).srefs if x.label}
    for k in range(passes):
        if cancel and cancel():
            break
        if best[0] >= conns:
            say("everything routes -- done")
            break
        unrouted = r.get("unrouted") or []
        feats = NG.features(d)
        P = PF.SR.ShapeProblem(d)
        sp = P.min_space
        on_open = [x for x in _refs_on_open(d, unrouted) if x not in locked]
        kind = ("nudge", "shift", "flip")[k % 3]
        moves, flip = {}, None
        if kind == "nudge":
            traces = [(t["net"], float(t["widthUm"]), [(q["x"], q["y"]) for q in t["points"]])
                      for t in r.get("traces") or []]
            pl = NG.plan(d, unrouted, locked=locked, max_move=max_move, traces=traces)
            moves = dict(pl["moves"])
            if not moves:
                kind = "shift"
        if kind == "flip":
            two = [x for x in on_open if x in by and len([f for f in feats if f["owner"] == x
                                                          and f["kind"] == "pad"]) == 2]
            if two:
                flip = rnd.choice(two)
            else:
                kind = "shift"
        if kind == "shift":
            cand = on_open or [x for x in by if x not in locked]
            for _try in range(12):
                ref = rnd.choice(cand)
                n = rnd.choice((1, 2, 3, 4)) * rnd.choice((-1, 1)) * step
                dx, dy = (n, 0.0) if rnd.random() < 0.5 else (0.0, n)
                if NG._move_ok(feats, ref, dx, dy, {}, P, sp):
                    moves = {ref: (dx, dy)}
                    break
        if not moves and not flip:
            continue
        tried += 1
        what = (f"flip {flip}" if flip else
                f"{kind} " + ", ".join(f"{x} {dx / 1000:+.2f},{dy / 1000:+.2f}"
                                       for x, (dx, dy) in moves.items()))
        session.checkpoint()
        with session._batch():
            if flip:
                ref_ = by[flip]
                ref_.rotation = (float(ref_.rotation) + 180.0) % 360.0
            else:
                NG.apply(session, cell, moves)
        session._notify()
        key, d2, r2 = _score(session, cell, layers, iters)
        if key > best:
            best, d, r = key, d2, r2
            kept.append(what)
            say(f"pass {k + 1}: {what} -> {key[0]}/{conns} routed, {-key[1]} crossover(s) -- kept")
        else:
            session.undo()
            say(f"pass {k + 1}: {what} -> {key[0]}/{conns} -- no better, undone")
    return {"before": (first[0], conns), "after": (best[0], conns), "kept": kept,
            "tried": tried, "seconds": round(time.time() - t0)}
