# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Open Channels: after Auto Place, move only the parts that wall a pad in.

A greedy local search on the routability check (hlayout.scripting.
routability, ~0.5 s a look): for every walled-in connection it tries moving
each part on the connection's cheapest way out -- and the walled-in part
itself -- straight away from the wall (then sideways, then diagonally) by
the wall's length, half of it and twice it, on the placement grid, where
the part stays clear of every other footprint and on the substrate. Each
candidate is put on the board, checked and taken off again; the one that
leaves the fewest connections walled in (then the fewest crossings with no
room for a crossover, then the lowest track demand) is kept, and the search
goes on from there until nothing improves. Locked parts and the header
never move; wire bonds and fingers move with their dies. One undo step per
kept move.

``open_channels(session)`` -> {"passes" (kept moves), "moves", "walled_before",
"walled_after", "log", "report"}.
"""
import math
import time

from ..core.errors import LayoutError


def _score(rep):
    return (rep["walled"], rep["no_xo"], round(rep["util"], 3))


def _fits(placer, p, dx, dy):
    """Would part p, moved by (dx, dy), overlap nothing it didn't before and
    stay on the substrate?"""
    from .autoplace_report import _hits
    before = _hits(placer, p)
    x0, y0 = p.x, p.y
    p.x, p.y = x0 + dx, y0 + dy
    now = _hits(placer, p)
    p.x, p.y = x0, y0
    return not now or now < before


def _cands(p, wall, dist, grid):
    vx, vy = p.x - wall[0], p.y - wall[1]
    sx, sy = (1 if vx >= 0 else -1), (1 if vy >= 0 else -1)
    out = []
    for f in (1.0, 0.5, 2.0):
        d = grid * max(1, math.ceil(dist * f / grid))
        main = [(sx * d, 0), (0, sy * d)] if abs(vx) >= abs(vy) else [(0, sy * d), (sx * d, 0)]
        out += main + [(sx * d, sy * d)]
    seen, res = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            res.append(c)
    return res


def open_channels(session, netlist=None, passes: int = 30, margin: float = 200.0,
                  max_move: float = 2500.0, time_budget: float = 300.0, on_line=None,
                  cell_name=None) -> dict:
    from .autoplace_session import apply, build_job
    from .routability import check
    say = on_line or (lambda _m: None)
    nl = netlist or (session.autoplace_settings() or {}).get("last_netlist")
    if not nl:
        raise LayoutError("no netlist -- run Auto Place first (or pass netlist=)")
    try:
        locked = set(session.locked_parts(cell_name))
    except Exception:
        locked = set()
    t0 = time.time()
    rep = check(session, cell_name=cell_name)
    start = rep["walled"]
    say(f"start: {rep['walled']} of {rep['connections']} connection(s) walled in, "
        f"{rep['no_xo']} crossing(s) with no crossover room")
    log, kept = [], 0
    tried = set()
    for k in range(1, int(passes) + 1):
        if not rep["walled_rows"] or time.time() - t0 > time_budget:
            break
        placer, info = build_job(session, nl, cell_name=cell_name)
        by = {p.ref: p for p in placer.parts}
        g = float(placer.grid or 50.0)
        best = None
        for w in sorted(rep["walled_rows"], key=lambda w: w["short"]):
            if w["wall"] is None:
                continue
            dist = min(max_move, w["short"] + margin)
            for ref in list(w["blockers"]) + list(w["own"]):
                p = by.get(ref)
                if p is None or p.fixed or ref in locked:
                    continue
                for dx, dy in _cands(p, w["wall"], dist, g):
                    key = (ref, round(p.x + dx), round(p.y + dy))
                    if key in tried or not _fits(placer, p, dx, dy):
                        continue
                    tried.add(key)
                    p.x += dx
                    p.y += dy
                    with session._batch():
                        apply(session, placer, info)
                    r2 = check(session, cell_name=cell_name)
                    session.undo()
                    p.x -= dx
                    p.y -= dy
                    if _score(r2) < _score(rep) and (best is None or _score(r2) < best[0]):
                        best = (_score(r2), ref, dx, dy, w["net"])
                if time.time() - t0 > time_budget:
                    break
            if time.time() - t0 > time_budget:
                break
        if best is None:
            say(f"move {k}: nothing improves -- done")
            break
        _sc, ref, dx, dy, net = best
        by[ref].x += dx
        by[ref].y += dy
        with session._batch():
            apply(session, placer, info)
        session._notify()
        rep = check(session, cell_name=cell_name)
        kept += 1
        msg = (f"move {k}: {ref} {dx / 1000:+.2f},{dy / 1000:+.2f} mm (for {net}) -- "
               f"{rep['walled']} walled in, {rep['no_xo']} crossing(s) with no crossover room")
        log.append(msg)
        say(msg)
    session._notify()
    return {"passes": kept, "moves": kept, "walled_before": start, "walled_after": rep["walled"],
            "connections": rep["connections"], "log": log, "report": rep}
