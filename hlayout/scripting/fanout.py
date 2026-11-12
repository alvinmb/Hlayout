# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fan out the bare dies: a bond finger on the substrate for each die pad
and a wire bond from the pad to it (Tools > Fan Out Die Bonds..., FANOUT,
Session.fan_out_die_bonds()).

Run it once Auto Place has fixed where the dies are and before routing.
For every placed die -- an instance with a ref-des whose cell has pads on
the Wire Bonds tab's die pad layers -- each pad that carries a design net
(every pad with ``all_pads``) gets:

  * a bond finger, ``finger_w`` wide along the die edge and ``finger_l``
    long pointing away from the die, on the finger conductor (the 1st, or
    the 2nd when the Freerouting rules put the fingers there), its inner
    end at least ``gap`` clear of the die (its attach pad included) --
    by default far enough out of the router's die keep-out (die_keepout
    plus the clearance) for the tracks to reach it;
  * a wire bond from the die pad to the finger.

Fingers are grouped by the die side their pad is nearest, kept in the
pads' order along that side (so no two bonds cross), spaced ``finger_w +
spacing`` apart at least and centred on their pads; a side's row moves
further out until every bond leaves its pad within the bond departure
angle (45 degrees unless the routed design sets another) and is no
shorter than the Wire Bonds tab's minimum. A bond longer than the maximum
is left out and reported. Running it again on a die replaces the bonds
and fingers it drew. One undo step.
"""
import math
from typing import Optional

from ..core.errors import LayoutError
from ..core.shapes import Path, Rectangle
from .autoplace_session import _body_bbox, _xf

DEFAULTS = {"finger_w": 250.0, "finger_l": 400.0, "spacing": 150.0, "gap": 250.0}


def _unconnected(net):
    n = (net or "").lower()
    return not n or n.startswith("unconnected")


def die_pads(library, cell_name, pad_layers, _depth=0, _xform=None):
    """{pin: (x, y)} of a cell's numbered pads on the die pad layers, in
    the cell's coordinates ({} when it has none -- not a die). A pin on
    another layer (a zener's backside contact on the conductor) is not a
    bond pad."""
    from ..core.shapes import Polygon
    out = {}
    cell = library.cells.get(cell_name)
    if cell is None or not pad_layers or _depth > 16:
        return out
    layers = set(pad_layers)
    xf = _xform or (lambda p: p)

    def on_pad_layers(cn, depth=0):
        c = library.cells.get(cn)
        if c is None or depth > 16:
            return False
        return any(sh.layer in layers for sh in c.shapes) or any(
            on_pad_layers(r.cell_name, depth + 1) for r in c.srefs)

    for sh in cell.shapes:
        pin = getattr(sh, "pin", "")
        if pin and sh.layer in layers and isinstance(sh, (Rectangle, Polygon)):
            x0, y0, x1, y1 = sh.bbox()
            out.setdefault(str(pin), xf(((x0 + x1) / 2.0, (y0 + y1) / 2.0)))
    for r in cell.srefs:
        comp = (lambda f, g: (lambda p: f(g(p))))(xf, _xf(r))
        if getattr(r, "pin", ""):
            if on_pad_layers(r.cell_name):
                b = library.cell_bbox(r.cell_name)
                c = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0) if b else (0.0, 0.0)
                out.setdefault(str(r.pin), comp(c))
        else:
            for k, v in die_pads(library, r.cell_name, pad_layers, _depth + 1, comp).items():
                out.setdefault(k, v)
    return out


def _place_row(items, pitch):
    """items: [(key, wanted)] sorted by wanted -> {key: position}, at least
    ``pitch`` apart, in order, centred on the wanted positions."""
    if not items:
        return {}
    pos = []
    for _k, want in items:
        pos.append(want if not pos else max(want, pos[-1] + pitch))
    shift = sum(w for _k, w in items) / len(items) - sum(pos) / len(pos)
    return {k: p + shift for (k, _w), p in zip(items, pos)}


def fan_out(session, cell_name: Optional[str] = None, refs=None, all_pads: bool = False,
            finger_w: Optional[float] = None, finger_l: Optional[float] = None,
            spacing: Optional[float] = None, gap: Optional[float] = None,
            layer=None) -> dict:
    from ..io.techfile import load_bond_rules, load_net_names
    lib = session.library
    name = cell_name or session.current_cell_name
    if name is None:
        raise LayoutError("no current cell")
    cell = lib.get_cell(name)
    br = load_bond_rules(lib)
    pad_layers = [int(l) for l in (br.get("die_pad_layers") or []) if int(l) >= 0]
    if not pad_layers:
        raise LayoutError("no die pad layers -- set them in Setup > Technology Properties, "
                          "Wire Bonds tab (the layer the bare dies' bond pads are on, e.g. 58)")
    blay = int(br["layer"])
    if blay < 0:
        raise LayoutError("no wire-bond layer -- set it in Setup > Technology Properties, "
                          "Wire Bonds tab")
    fw = float(DEFAULTS["finger_w"] if finger_w is None else finger_w)
    fl = float(DEFAULTS["finger_l"] if finger_l is None else finger_l)
    sp = float(DEFAULTS["spacing"] if spacing is None else spacing)
    if gap is None:
        # the router keeps every track clear of a die by die_keepout; a
        # finger inside that ring (plus the clearance) can't be reached
        try:
            R = session._rules_with_drc(session.freerouting_rules())
            gp = max(DEFAULTS["gap"], float(R.get("die_keepout", 200) or 0)
                     + float(R.get("clearance", 100) or 0) + 50.0)
        except Exception:
            gp = DEFAULTS["gap"]
    else:
        gp = float(gap)
    if fw <= 0 or fl <= 0 or sp < 0 or gp < 0:
        raise LayoutError("finger width and length must be positive, spacing and gap 0 or more")
    flay = (lib.layers.resolve(layer) if layer is not None
            else session._finger_layer(session.freerouting_rules()))
    hb = session._hybrid_bond_rules()
    tan_a = math.tan(math.radians(max(5.0, min(85.0, float(hb.get("angle", 45.0))))))
    lo, hi = float(br["min_length"]), float(br["max_length"])
    ball = float(br["pad"]) / 2.0
    names = load_net_names(lib)
    want = {str(r).strip() for r in refs} if refs else None

    dies = []
    for r in cell.srefs:
        if not r.label or (want is not None and r.label not in want):
            continue
        pads = die_pads(lib, r.cell_name, pad_layers)
        if pads:
            dies.append((r, pads))
    if want is not None:
        missing = want - {r.label for r, _p in dies}
        if missing:
            raise LayoutError(f"not a placed bare die: {', '.join(sorted(missing))}")
    if not dies:
        raise LayoutError(f"no bare dies placed in {name} (an instance with a ref-des whose "
                          f"cell has pads on the die pad layers {pad_layers})")

    report = {"dies": {}, "fingers": 0, "skipped": {}, "overlaps": [], "layer": flay,
              "cell": name}
    # other parts' footprints and copper the new fingers should keep clear of
    others = []
    for r in cell.srefs:
        if not r.label:                 # the header / substrate round everything
            continue
        b = lib.cell_bbox(r.cell_name)
        if b:
            f = _xf(r)
            pts = [f((b[0], b[1])), f((b[2], b[3])), f((b[0], b[3])), f((b[2], b[1]))]
            others.append((r, (min(p[0] for p in pts), min(p[1] for p in pts),
                               max(p[0] for p in pts), max(p[1] for p in pts))))

    with session._batch():
        for r, pads in dies:
            f = _xf(r)
            body = _body_bbox(lib, r.cell_name, skip_layers={blay}) or (-500, -500, 500, 500)
            bx0, by0, bx1, by1 = body
            cx, cy = (bx0 + bx1) / 2.0, (by0 + by1) / 2.0
            hx, hy = max((bx1 - bx0) / 2.0, 1.0), max((by1 - by0) / 2.0, 1.0)
            use = [p for p in pads
                   if all_pads or not _unconnected(names.get(f"{r.label}.{p}"))]
            # -- take this die's old fan-out off (its bonds and their fingers)
            for p in pads:
                wx, wy = f(pads[p])
                for _ in range(4):
                    found = session.find_wire_bond((wx, wy), layer=blay, cell_name=name,
                                                   tol=ball + 20)
                    if found is None:
                        break
                    path, _balls = found
                    a, b = tuple(path.points[0]), tuple(path.points[-1])
                    far = b if math.hypot(a[0] - wx, a[1] - wy) <= math.hypot(b[0] - wx, b[1] - wy) \
                        else a
                    try:
                        session.delete_wire_bond((wx, wy), layer=blay, cell_name=name,
                                                 tol=ball + 20)
                    except LayoutError:
                        break
                    cell.shapes[:] = [
                        sh for sh in cell.shapes
                        if not (isinstance(sh, Rectangle) and sh.layer == flay
                                and not getattr(sh, "pin", "")
                                and sh.bbox()[0] <= far[0] <= sh.bbox()[2]
                                and sh.bbox()[1] <= far[1] <= sh.bbox()[3]
                                and (sh.bbox()[2] - sh.bbox()[0]) * (sh.bbox()[3] - sh.bbox()[1])
                                <= 4 * fw * fl)]
            # -- group the pads by side (in the die's own coordinates)
            sides = {"L": [], "R": [], "B": [], "T": []}
            for p in use:
                x, y = pads[p]
                dx, dy = (x - cx) / hx, (y - cy) / hy
                if abs(dx) >= abs(dy):
                    sides["R" if dx > 0 else "L"].append(p)
                else:
                    sides["T" if dy > 0 else "B"].append(p)
            made = []
            for side, pins in sides.items():
                if not pins:
                    continue
                horiz = side in ("B", "T")          # fingers in a row along x
                along = (lambda p: pads[p][0]) if horiz else (lambda p: pads[p][1])
                out_sign = 1.0 if side in ("R", "T") else -1.0
                edge = {"L": bx0, "R": bx1, "B": by0, "T": by1}[side]
                perp = (lambda p: pads[p][1]) if horiz else (lambda p: pads[p][0])
                row = _place_row(sorted(((p, along(p)) for p in pins), key=lambda t: t[1]),
                                 fw + sp)
                # how far out the row's centre line must be
                d = abs(edge - (cy if horiz else cx)) + gp + fl / 2.0
                base = (cy if horiz else cx) + out_sign * d
                need = 0.0
                for p in pins:
                    off = abs(row[p] - along(p))
                    depth = max(off / tan_a, math.sqrt(max(0.0, lo * lo - off * off)))
                    need = max(need, out_sign * (perp(p) + out_sign * depth - base))
                base += out_sign * max(0.0, need)
                for p in pins:
                    if horiz:
                        fc = (row[p], base)
                        rect = (row[p] - fw / 2.0, base - fl / 2.0, row[p] + fw / 2.0,
                                base + fl / 2.0)
                    else:
                        fc = (base, row[p])
                        rect = (base - fl / 2.0, row[p] - fw / 2.0, base + fl / 2.0,
                                row[p] + fw / 2.0)
                    wpad, wland = f(pads[p]), f(fc)
                    span = math.hypot(wland[0] - wpad[0], wland[1] - wpad[1])
                    if span > hi:
                        report["skipped"][f"{r.label}.{p}"] = (
                            f"bond {span / 1000:.2f} mm longer than the maximum {hi / 1000:.2f} mm")
                        continue
                    c = [f((rect[0], rect[1])), f((rect[2], rect[3]))]
                    wr = (round(min(c[0][0], c[1][0])), round(min(c[0][1], c[1][1])),
                          round(max(c[0][0], c[1][0])), round(max(c[0][1], c[1][1])))
                    try:
                        session.wire_bond([wpad, wland], layer=blay, cell_name=name)
                    except LayoutError as e:
                        report["skipped"][f"{r.label}.{p}"] = str(e)
                        continue
                    cell.add_rectangle(flay, *wr)
                    made.append(p)
                    for o, ob in others:
                        if o is r:
                            continue
                        if wr[0] < ob[2] and wr[2] > ob[0] and wr[1] < ob[3] and wr[3] > ob[1]:
                            report["overlaps"].append(f"{r.label}.{p} on {o.label or o.cell_name}")
                            break
            report["dies"][r.label] = sorted(made, key=lambda t: (len(t), t))
            report["fingers"] += len(made)
    session._notify()
    return report
