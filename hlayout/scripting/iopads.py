# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Assign I/O pads: which net goes on which module I/O pad.

A chip-and-wire module's I/O pads sit where its header (package) bonds
land on the substrate, one per used header pin. Which NET each pad
carries is free when the module's pinout is the designer's to choose, and
it decides how long -- and how tangled -- the routing from the pads to
the dice is.

``plan()`` works out the sites and the best assignment: each site's cost
for a net is the distance from the site to the nearest copper that net
must reach (a bond finger / die pad, a part's pad); the assignment
minimising the total is found with the Hungarian method. A minimum-total
matching of points in the plane has no crossing pairs, so the nets fan
out from the pad row without crossing one another. ``apply()`` draws the
pads, renames the nets (IO.<pin> = net) and writes the pinout CSV.

Header pin numbers come from the pads already there (the nearest pinned
pad to each site), else from the header's geometry: DIP numbering, pin 1
bottom left, the bottom row left to right, the top row back right to left.
"""

import csv
import math
import os
from typing import Dict, List, Optional, Tuple

from ..core.errors import LayoutError
from ..core.shapes import Path, Polygon, Rectangle
from .autoplace_session import _xf


def _pinned_points(library, cell_name, depth=0, xform=None):
    """[(pin, (x, y))] for EVERY numbered pad of a cell (a pin numbered
    twice -- a die pad and its bond finger -- gives both)."""
    out = []
    cell = library.cells.get(cell_name)
    if cell is None or depth > 16:
        return out
    xf = xform or (lambda p: p)
    for sh in cell.shapes:
        pin = getattr(sh, "pin", "")
        if pin and isinstance(sh, (Rectangle, Polygon)):
            x0, y0, x1, y1 = sh.bbox()
            out.append((str(pin), xf(((x0 + x1) / 2.0, (y0 + y1) / 2.0))))
    for r in cell.srefs:
        child = _xf(r)
        comp = (lambda f, g: (lambda p: f(g(p))))(xf, child)
        out.extend(_pinned_points(library, r.cell_name, depth + 1, comp))
    return out


def header_sites(session, cell, bond_layers=None) -> List[dict]:
    """The I/O pad sites: the substrate end of every bond that runs from
    off the substrate (a header / package pin) onto it. Returns
    [{"xy": (x, y), "pin_xy": (x, y), "inward": (dx, dy)}], ``inward`` the
    unit direction from the header pin towards the board."""
    sub = session._substrate_box(cell)
    if sub is None:
        raise LayoutError("no substrate outline -- the I/O pad sites are where the header "
                          "bonds land on the substrate")
    from shapely.geometry import Point
    sites = []
    for sh in cell.shapes:
        if not isinstance(sh, Path) or len(sh.points) < 2:
            continue
        if bond_layers is not None and sh.layer not in bond_layers:
            continue
        a, b = tuple(sh.points[0]), tuple(sh.points[-1])
        ia, ib = sub.contains(Point(a)), sub.contains(Point(b))
        if ia == ib:
            continue
        site, pin = (a, b) if ia else (b, a)
        L = math.hypot(site[0] - pin[0], site[1] - pin[1]) or 1.0
        if L > 10000:            # not a bond
            continue
        sites.append({"xy": (float(site[0]), float(site[1])), "pin_xy": (float(pin[0]), float(pin[1])),
                      "inward": ((site[0] - pin[0]) / L, (site[1] - pin[1]) / L)})
    return sites


def _number_sites(session, cell, sites, layer):
    """Header pin number of each site: an existing pinned pad there, else
    DIP numbering from the header's geometry."""
    pads = [(str(sh.pin), sh.bbox()) for sh in cell.shapes
            if isinstance(sh, Rectangle) and getattr(sh, "pin", "") and sh.layer == layer]
    if pads and len(pads) >= len(sites):
        for st in sites:
            x, y = st["xy"]
            best = min(pads, key=lambda p: (abs((p[1][0] + p[1][2]) / 2 - x)
                                            + abs((p[1][1] + p[1][3]) / 2 - y)))
            st["pin"] = best[0]
        if len({st["pin"] for st in sites}) == len(sites):
            return
    # DIP: rows by the header-pin y, pitch from the closest neighbours
    ys = sorted({round(st["pin_xy"][1]) for st in sites})
    cy = (ys[0] + ys[-1]) / 2.0
    xs = sorted(st["pin_xy"][0] for st in sites)
    gaps = [b - a for a, b in zip(xs, xs[1:]) if b - a > 100]
    pitch = min(gaps) if gaps else 2540.0
    x0 = min(xs)
    idx = [int(round((st["pin_xy"][0] - x0) / pitch)) for st in sites]
    half = max(idx) + 1
    one_row = ys[-1] - ys[0] < 100
    for st, i in zip(sites, idx):
        st["pin"] = str(i + 1 if (one_row or st["pin_xy"][1] < cy) else 2 * half - i)


def plan(session, cell_name: Optional[str] = None, bond_layers=None) -> dict:
    """Work out the best net for every I/O pad site (see the module doc);
    changes nothing. Returns {"sites": [{"pin", "xy", "inward", "net",
    "was"}], "before": um, "after": um} -- before/after the total
    distance from each pad to the nearest copper its net must reach."""
    import numpy as np
    from scipy.optimize import linear_sum_assignment
    from ..io.techfile import load_net_names
    lib = session.library
    name = cell_name or session.current_cell_name
    cell = lib.get_cell(name)
    names = load_net_names(lib)
    if not names:
        raise LayoutError("the design has no net names -- import them first (NETNAMES IMPORT, "
                          "or Auto Place)")
    la, _lb = session._route_layers()
    sites = header_sites(session, cell, bond_layers)
    if not sites:
        raise LayoutError("no header bonds found -- bond each used header pin to the substrate "
                          "first; the bond's substrate end is where its I/O pad goes")
    _number_sites(session, cell, sites, la)
    io = {k[3:]: v for k, v in names.items() if k.startswith("IO.")}
    # one entry per pad: a net on two pads (a power net) keeps two
    nets = sorted(io.values())
    if len(nets) > len(sites):
        raise LayoutError(f"{len(nets)} I/O nets but only {len(sites)} header bonds")
    # every point each net must reach: placed parts' pins (die pads, bond
    # fingers, passives' pads) -- the module's own I/O pads aside
    targets: Dict[str, List[Tuple[float, float]]] = {}
    for r in cell.srefs:
        if not r.label:
            continue
        for pin, xy in _pinned_points(lib, r.cell_name, 1, _xf(r)):
            n = names.get(f"{r.label}.{pin}")
            if n in io.values():
                targets.setdefault(n, []).append(xy)

    def cost(xy, n):
        pts = targets.get(n)
        if not pts:
            return 0.0
        return min(math.hypot(xy[0] - x, xy[1] - y) for x, y in pts)

    C = np.array([[cost(st["xy"], n) for n in nets] for st in sites])
    ri, ci = linear_sum_assignment(C)
    by_pin = {st["pin"]: st for st in sites}
    for st in sites:
        st["was"] = io.get(st["pin"])
        st["net"] = None
    for r, c in zip(ri, ci):
        sites[r]["net"] = nets[c]
    before = sum(cost(by_pin[p]["xy"], n) for p, n in io.items() if p in by_pin)
    after = float(C[ri, ci].sum())
    unreached = [n for n in nets if n not in targets]
    return {"sites": sorted(sites, key=lambda s: int(s["pin"]) if s["pin"].isdigit() else 0),
            "before": before, "after": after, "unreached": unreached, "cell": name}


def apply(session, p: dict, pad_w: float = 300.0, pad_l: float = 600.0,
          csv_path: Optional[str] = None, bond_inset: float = 150.0) -> dict:
    """Draw the planned pads and rename the nets. Every pinned pad on the
    1st conductor in the cell is replaced: a pad_w x pad_l pad per site,
    its long side pointing in from the header, the bond landing
    ``bond_inset`` um in from its outer end, pin = the header pin number. The net names'
    IO.<pin> entries are rewritten. One undo step."""
    from ..io.techfile import load_net_names
    lib = session.library
    cell = lib.get_cell(p["cell"])
    la, _lb = session._route_layers()
    names = load_net_names(lib)
    new_names = {k: v for k, v in names.items() if not k.startswith("IO.")}
    with session._batch():
        cell.shapes[:] = [sh for sh in cell.shapes
                          if not (isinstance(sh, Rectangle) and getattr(sh, "pin", "")
                                  and sh.layer == la)]
        for st in p["sites"]:
            x, y = st["xy"]
            dx, dy = st["inward"]
            # snap the pad's axis to the nearer of horizontal / vertical
            if abs(dx) >= abs(dy):
                ux, uy = (1.0 if dx > 0 else -1.0), 0.0
                hw, hl = pad_l / 2.0, pad_w / 2.0
            else:
                ux, uy = 0.0, (1.0 if dy > 0 else -1.0)
                hw, hl = pad_w / 2.0, pad_l / 2.0
            off = pad_l / 2.0 - min(float(bond_inset), pad_l / 2.0)
            cx, cy = x + ux * off, y + uy * off
            cell.add_rectangle(la, round(cx - hw), round(cy - hl), round(cx + hw), round(cy + hl),
                               pin=st["pin"])
            if st["net"]:
                new_names[f"IO.{st['pin']}"] = st["net"]
        session.set_net_names(new_names)
    if csv_path:
        os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["header_pin", "net", "previous_net"])
            for st in p["sites"]:
                w.writerow([st["pin"], st["net"] or "", st.get("was") or ""])
    session._notify()
    return {"pads": len(p["sites"]), "csv": csv_path}


def header_pins(session, cell, sub=None) -> Dict[str, Tuple[float, float]]:
    """{pin: (x, y)} of the module's header (package) pins: the numbered
    pads of the placed part with the most numbered pads off the substrate
    -- the package the substrate sits in."""
    from shapely.geometry import Point
    sub = sub if sub is not None else session._substrate_box(cell)
    best = {}
    for r in cell.srefs:
        pins = {}
        for pin, xy in _pinned_points(session.library, r.cell_name, 1, _xf(r)):
            if sub is None or not sub.contains(Point(xy)):
                pins.setdefault(pin, xy)
        if len(pins) > len(best):
            best = pins
    return best


def _header_bond_layer(session):
    """The layer header bonds go on: a drawn layer named for header bonds,
    else the Wire Bond rules' layer."""
    from ..io.techfile import load_bond_rules
    lib = session.library
    for n in range(len(lib.layers)):
        nm = (lib.layers[n].name or "").lower()
        if "bond" in nm and ("header" in nm or "package" in nm or "pin" in nm):
            return n
    return int(load_bond_rules(lib)["layer"])


def bond_header(session, cell_name: Optional[str] = None, pins=None, all_pins: bool = False,
                layer=None, pad_w: float = 300.0, pad_l: float = 600.0,
                bond_inset: float = 150.0, edge_margin: float = 250.0,
                width=None) -> dict:
    """Bond the module's header pins to the substrate: for each header pin
    (see header_pins()) a numbered I/O pad (pin = the header pin) on the
    1st conductor at the nearest substrate edge -- its outer end
    ``edge_margin`` in from the edge, ``pad_l`` long pointing in, ``pad_w``
    wide along the edge -- and a header bond from the pin to the pad,
    landing ``bond_inset`` in from the pad's outer end.

    Which pins: ``pins`` (a list of pin numbers), else ``all_pins``, else
    the pins with an IO.<pin> net name (every pin when there are none).
    A pin's old I/O pad and its old header bond are replaced. One undo
    step. Returns {"pads": n, "pins": [...], "layer": bond layer,
    "skipped": {pin: reason}}."""
    from shapely.geometry import Point
    from shapely.ops import nearest_points
    from ..io.techfile import load_net_names
    lib = session.library
    name = cell_name or session.current_cell_name
    cell = lib.get_cell(name)
    sub = session._substrate_box(cell)
    if sub is None:
        raise LayoutError("no substrate outline -- the I/O pads go at the substrate edge")
    hp = header_pins(session, cell, sub)
    if not hp:
        raise LayoutError("no header pins found -- place the package (a part with numbered "
                          "pads off the substrate) first")
    if pins:
        want = [str(p) for p in pins]
    elif all_pins:
        want = list(hp)
    else:
        io = {k[3:] for k in load_net_names(lib) if k.startswith("IO.")}
        want = [p for p in hp if p in io] or list(hp)
    la, _lb = session._route_layers()
    bl = session.library.layers.resolve(layer) if layer is not None else _header_bond_layer(session)
    if bl is None or bl < 0:
        raise LayoutError("no bond layer -- pass layer= or set the Wire Bond rules' layer")
    skipped = {}
    done = []
    ring = sub.exterior
    with session._batch():
        for pin in sorted(want, key=lambda p: (len(p), p)):
            if pin not in hp:
                skipped[pin] = "no such header pin"
                continue
            px, py = hp[pin]
            q = nearest_points(ring, Point(px, py))[0]
            dx, dy = q.x - px, q.y - py
            if abs(dx) >= abs(dy):                 # pin left / right of the substrate
                ux, uy = (1.0 if dx > 0 else -1.0), 0.0
            else:                                  # pin below / above
                ux, uy = 0.0, (1.0 if dy > 0 else -1.0)
            # pad on the edge line straight in from the pin
            ex, ey = (q.x, py) if uy == 0 else (px, q.y)
            ox, oy = ex + ux * edge_margin, ey + uy * edge_margin      # outer end
            cx, cy = ox + ux * pad_l / 2.0, oy + uy * pad_l / 2.0
            hw, hh = (pad_l / 2.0, pad_w / 2.0) if uy == 0 else (pad_w / 2.0, pad_l / 2.0)
            lx, ly = ox + ux * bond_inset, oy + uy * bond_inset         # bond landing
            # replace this pin's old pad and its old header bond
            cell.shapes[:] = [sh for sh in cell.shapes
                              if not (isinstance(sh, Rectangle) and sh.layer == la
                                      and str(getattr(sh, "pin", "")) == pin)]
            for _ in range(4):
                try:
                    session.delete_wire_bond((px, py), layer=bl, cell_name=name)
                except Exception:
                    break
            cell.add_rectangle(la, round(cx - hw), round(cy - hh), round(cx + hw),
                               round(cy + hh), pin=pin)
            try:
                session.wire_bond([(px, py), (lx, ly)], layer=bl, width=width, cell_name=name)
            except LayoutError as e:
                skipped[pin] = str(e)
                continue
            done.append(pin)
    session._notify()
    return {"pads": len(done), "pins": done, "layer": bl, "skipped": skipped, "cell": name}


def header_nets(names: dict) -> Dict[str, str]:
    """{pin: net} of the module's header as the netlist names it -- the
    J30.<pin> / P1.<pin> entries of the design's net names (the header
    ref with the most pins) -- so the I/O pad numbered <pin> carries it."""
    from .autoplace_session import _HEADER_REF
    by_ref: Dict[str, Dict[str, str]] = {}
    for k, v in names.items():
        ref, dot, pin = k.partition(".")
        if dot and v and ref != "IO" and _HEADER_REF.match(ref):
            by_ref.setdefault(ref, {})[pin] = v
    return max(by_ref.values(), key=len) if by_ref else {}


def fill_io_names(session, pins=None) -> int:
    """Give each I/O pad number with no IO.<pin> net yet the header pin's
    net from the netlist (J30.<pin>). ``pins``: just these numbers.
    Names set by Assign I/O Pads (or by hand) are left alone. Returns how
    many were added."""
    from ..io.techfile import load_net_names
    names = load_net_names(session.library)
    hn = header_nets(names)
    add = {f"IO.{p}": n for p, n in hn.items()
           if f"IO.{p}" not in names and (pins is None or p in pins)}
    if add:
        names.update(add)
        session.set_net_names(names)
    return len(add)


def substrate_outline(session, cell, shape=None):
    """The substrate as a shapely polygon in ``cell``'s coordinates: the
    given ``shape`` (a selected Rectangle / Polygon, or an SRef of a
    substrate cell -- its boundary-layer geometry), else the cell's
    boundary layer (placed instances included). The largest piece wins
    when there are several."""
    from shapely.geometry import Polygon as SPoly
    from shapely.ops import transform
    from ..core.maskops import layer_to_shapely_flattened
    from ..core.shapes import SRef
    lib = session.library
    g = None
    if isinstance(shape, (Rectangle, Polygon)):
        g = SPoly(shape.points)
    elif isinstance(shape, SRef):
        lay = session._boundary_layer()
        child = lib.cells.get(shape.cell_name)
        if child is None or lay is None:
            raise LayoutError("the selected instance has no substrate geometry")
        cg = layer_to_shapely_flattened(lib, child, lay)
        if cg is None or cg.is_empty:
            raise LayoutError(f"{shape.cell_name} has nothing on the boundary layer {lay}")
        f = _xf(shape)
        g = transform(lambda x, y, z=None: f((x, y)), cg)
    elif shape is not None:
        raise LayoutError("select the substrate (a rectangle or polygon, or a placed substrate "
                          "cell) first")
    else:
        lay = session._boundary_layer()
        if lay is not None:
            g = layer_to_shapely_flattened(lib, cell, lay)
    if g is None or g.is_empty:
        raise LayoutError("no substrate -- select it, or draw it on the boundary layer "
                          "(Setup > Technology Properties, Route tab)")
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda p: p.area)
    if not g.is_valid:
        g = g.buffer(0)
    return g


def _edge_y(outline, x, top):
    """Where a vertical line at ``x`` crosses the substrate's top (or
    bottom) edge, or None."""
    from shapely.geometry import LineString
    x0, y0, x1, y1 = outline.bounds
    hit = outline.intersection(LineString([(x, y0 - 1), (x, y1 + 1)]))
    if hit.is_empty:
        return None
    b = hit.bounds
    return b[3] if top else b[1]


def edge_pads(session, cell_name: Optional[str] = None, shape=None, n: Optional[int] = None,
              pitch: float = 2540.0, sides: str = "TB", pad_w: float = 300.0,
              pad_l: float = 600.0, edge_margin: float = 250.0, start: int = 1,
              layer=None, align: str = "auto") -> dict:
    """Draw a row of numbered I/O pads along the substrate's bottom and / or
    top edge, in ``cell_name`` (default: the current cell).

    The substrate: ``shape`` (the selected rectangle / polygon / placed
    substrate cell), else the boundary layer. Each pad is ``pad_w`` wide
    along the edge and ``pad_l`` long pointing in, its outer end
    ``edge_margin`` in from the edge -- for a shaped substrate, from the
    edge where it actually is at that pad.

    Where the pads go (``align``): "header" -- at the x of each header
    (package) pin below / above the substrate, numbered as that pin;
    "pitch" -- ``n`` pads a side (default: as many as fit) at ``pitch``,
    centred, numbered DIP style from ``start``: the bottom row left to
    right, then the top row right to left; "auto" -- "header" when a
    package is placed, else "pitch".

    Pads with the same pin numbers on the 1st conductor (or ``layer``) are
    replaced. One undo step. Returns {"pads", "pins", "align", "pitch",
    "layer", "cell", "skipped"}."""
    from shapely.geometry import box as _box
    lib = session.library
    name = cell_name or session.current_cell_name
    if name is None:
        raise LayoutError("no current cell")
    cell = lib.get_cell(name)
    sides = (sides or "TB").upper()
    if not sides or any(c not in "TB" for c in sides):
        raise LayoutError("sides must be T, B or TB")
    if pad_w <= 0 or pad_l <= 0 or edge_margin < 0 or pitch <= 0:
        raise LayoutError("pad width, length and pitch must be positive, the edge margin 0 or more")
    outline = substrate_outline(session, cell, shape)
    x0, y0, x1, y1 = outline.bounds
    la = session._route_layers()[0] if layer is None else lib.layers.resolve(layer)
    if la is None or la < 0:
        raise LayoutError("no conductor layer -- set the Route layers or pass layer=")

    rows = {"B": [], "T": []}                       # side -> [(pin, x)]
    mode = (align or "auto").lower()
    if mode not in ("auto", "header", "pitch"):
        raise LayoutError("align must be auto, header or pitch")
    hp = header_pins(session, cell, outline) if mode in ("auto", "header") else {}
    if hp:
        for pin, (px, py) in hp.items():
            if x0 <= px <= x1:
                side = "B" if py < y0 else ("T" if py > y1 else None)
                if side:
                    rows[side].append((str(pin), float(px)))
    if mode == "header" and not any(rows.values()):
        raise LayoutError("no header pins above or below the substrate -- place the package, "
                          "or use align=pitch")
    if mode == "pitch" or not any(rows[s] for s in sides):
        mode = "pitch"
        span = (x1 - x0) - 2.0 * edge_margin - pad_w
        fit = int(span // pitch) + 1 if span >= 0 else 0
        k = fit if n is None else int(n)
        if k < 1:
            raise LayoutError("no pads fit along the edge")
        if k > fit:
            raise LayoutError(f"{k} pads at {pitch / 1000:.3f} mm pitch need "
                              f"{((k - 1) * pitch + pad_w + 2 * edge_margin) / 1000:.2f} mm; "
                              f"the substrate is {(x1 - x0) / 1000:.2f} mm wide (at most {fit})")
        cx = (x0 + x1) / 2.0
        xs = [cx + (i - (k - 1) / 2.0) * pitch for i in range(k)]
        num = int(start)
        if "B" in sides:
            rows["B"] = [(str(num + i), x) for i, x in enumerate(xs)]
            num += k
        if "T" in sides:
            rows["T"] = [(str(num + i), x) for i, x in enumerate(reversed(xs))]
    else:
        mode = "header"

    made, skipped = [], {}
    with session._batch():
        want = {p for s in sides for p, _x in rows[s]}
        cell.shapes[:] = [sh for sh in cell.shapes
                          if not (isinstance(sh, Rectangle) and sh.layer == la
                                  and str(getattr(sh, "pin", "")) in want)]
        for side in sides:
            top = side == "T"
            for pin, x in rows[side]:
                ey = _edge_y(outline, x, top)
                if ey is None:
                    skipped[pin] = "off the substrate"
                    continue
                if top:
                    ya, yb = ey - edge_margin - pad_l, ey - edge_margin
                else:
                    ya, yb = ey + edge_margin, ey + edge_margin + pad_l
                r = (round(x - pad_w / 2.0), round(ya), round(x + pad_w / 2.0), round(yb))
                if not outline.buffer(0.5).contains(_box(*r)):
                    skipped[pin] = "pad would hang over the substrate edge"
                    continue
                cell.add_rectangle(la, *r, pin=pin)
                made.append(pin)
        io_named = fill_io_names(session, set(made)) if made else 0
    session._notify()
    return {"pads": len(made), "pins": made, "align": mode, "io_named": io_named,
            "pitch": None if mode == "header" else pitch, "layer": la, "cell": name,
            "skipped": skipped}
