# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Auto Place with tries: place the netlist with several seeds and keep the
placement the routability check rates best (fewest walled-in connections,
then fewest crossings with no room for a crossover, then the lowest track
demand, then the shortest nets). Each try is placed, fanned out (bare dies)
and checked, then undone; the best is put back -- one undo step for the
placement and one for its fan-out. Placement with a seed is repeatable, so
the kept result is exactly the one that was scored."""
from ..core.errors import LayoutError


def _has_dies(session, cell_name=None):
    try:
        from ..io.techfile import load_bond_rules
        from .fanout import die_pads
        lay = [int(v) for v in (load_bond_rules(session.library).get("die_pad_layers") or [])]
        if not lay:
            return False
        cell = session.library.get_cell(cell_name or session.current_cell_name)
        return any(r.label and die_pads(session.library, r.cell_name, lay) for r in cell.srefs)
    except Exception:
        return False


def auto_place_tries(session, netlist, tries: int = 3, seeds=None, cell_name=None,
                     on_line=None, cancel=None, **settings) -> dict:
    from .autoplace_session import apply, build_job
    from .routability import check
    say = on_line or (lambda _m: None)
    seeds = list(seeds) if seeds else [int(settings.pop("seed", 0)) + k
                                       for k in range(max(1, int(tries)))]
    settings.pop("seed", None)
    results = []
    best = None
    for sd in seeds:
        if cancel and cancel():
            break
        S = dict(settings, seed=sd)
        placer, info = build_job(session, netlist, cell_name=cell_name, settings=S)
        placer.run(effort=float(info["settings"]["effort"]), cancel=cancel)
        bad, out = placer.overlaps()
        with session._batch():
            apply(session, placer, info)
        session._notify()
        fanned = False
        if _has_dies(session, cell_name):
            try:
                session.fan_out_die_bonds(cell_name=cell_name)
                fanned = True
            except LayoutError:
                pass
        try:
            rep = check(session, cell_name=cell_name)
        except Exception as e:
            rep = None
            say(f"seed {sd}: check failed ({e})")
        if fanned:
            session.undo()
        session.undo()
        session._notify()
        legal = not bad and not out
        key = ((0 if legal else 1),
               rep["walled"] if rep else 999, rep["no_xo"] if rep else 999,
               round(rep["util"], 2) if rep else 9.0, placer.wirelength())
        line = (f"seed {sd}: {'legal' if legal else 'NOT LEGAL'}, "
                + (f"{rep['connections'] - rep['walled']}/{rep['connections']} reachable, "
                   f"{rep['no_xo']} crossing(s) with no crossover room, "
                   f"demand {100 * rep['util']:.0f}%, " if rep else "")
                + f"nets {placer.wirelength() / 1000:.0f} mm")
        say(line)
        results.append({"seed": sd, "key": key, "line": line})
        if best is None or key < best[0]:
            best = (key, sd, placer, info)
    if best is None:
        raise LayoutError("no try finished")
    key, sd, placer, info = best
    with session._batch():
        apply(session, placer, info)
    session._notify()
    fanned = False
    if _has_dies(session, cell_name):
        try:
            session.fan_out_die_bonds(cell_name=cell_name)
            fanned = True
        except LayoutError:
            pass
    bad, out = placer.overlaps()
    say(f"kept seed {sd}")
    return {"seed": sd, "tries": results, "legal": not bad and not out, "fanned_out": fanned,
            "placer": placer, "info": info}
