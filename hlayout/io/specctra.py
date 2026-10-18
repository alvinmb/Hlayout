"""Specctra DSN export / SES import -- the file interface to the
Freerouting autorouter (https://github.com/freerouting/freerouting).

The round trip:

    export_dsn()  -> board.dsn   (outline, two routing layers, every
                                  numbered pad as a component pin, the
                                  design's named nets, net classes,
                                  existing tracks and vias)
    Freerouting   -> board.ses   (run headless: see run_freerouting())
    parse_ses()   -> the routed wires and vias, in DBU (um)

Session.import_ses() turns a parsed session back into HLayout copper
(round-capped Paths on the two conductor layers, and vias stamped with
their dielectric window), then regenerates the dielectric fill.

Thick-film mapping (the parts a PCB router doesn't know about):

* The two conductor layers are Specctra signal layers named ``L<n>``
  (top = the 2nd conductor, ``layer_b``). The printed dielectric between
  them is not modelled -- every via Freerouting places is given its
  dielectric window on import, and the fill is regenerated.
* A pad exists only on the layer it's drawn on, so a part whose pads are
  on the 2nd conductor can only be reached on that layer (Freerouting
  drops a via to come from the 1st), exactly like an SMD pad on a PCB.
* Pads drawn directly in the routed cell (module I/O pads) become pins of
  a component named ``IO`` -- the same IO.<pin> naming Highlight Net uses.
* Net names come from the design's stored net names (NETNAMES /
  Session.set_net_names()); a pad with no name is still exported as an
  obstacle pin, just not connected to anything.
* Existing tracks and vias are exported as ``protect`` wiring (kept
  exactly as drawn) or, with ``keep_existing=False``, as ``route``
  wiring Freerouting is free to rip up and replace.

All coordinates are written in um at resolution 1 (HLayout's DBU), so
nothing is rounded on the way out. Requires shapely.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

#: Freerouting rules HLayout uses when the design has none saved (see
#: techfile.load_freerouting_rules): widths/clearance/vias in DBU (um).
DEFAULT_RULES = {
    "signal_width": 300,
    "power_width": 600,
    "clearance": 100,
    "signal_via": 400,
    "signal_via_window": 600,
    "power_via": 700,
    "power_via_window": 800,
    "power_nets": ["VCC", "GND"],
    "exclude_nets": [],
    "passes": 100,
    "snap_90": True,
    "keep_existing": True,
    "remove_existing": False,
    #: module I/O pads may only be connected within this distance (um) of
    #: their inner edge (0 = anywhere on the pad)
    "io_entry_depth": 300,
    #: "auto" routes a design with nothing on the 2nd conductor on the
    #: 1st alone (no dielectric, no vias); "single" / "two" force it
    "layer_mode": "auto",
    #: chip-and-wire: square bond finger (um) laid where a wire bond
    #: lands on the substrate; the tracks are routed to it
    "bond_finger": 400,
    #: keep tracks this far (um) clear of a bare die / its attach epoxy
    "die_keepout": 200,
    #: no track between a part's own terminations (under a chip
    #: capacitor, across a printed resistor's ink)
    "keepout_bodies": True,
}

VIA_SIG = "VIA_SIG"
VIA_PWR = "VIA_PWR"
EXCLUDED_CLASS = "HL_EXCLUDED"


def _q(name) -> str:
    """Quote a Specctra identifier when it needs it."""
    name = str(name)
    if name and re.fullmatch(r"[A-Za-z0-9_+\-./\[\]:#$%&*<>=?@^~!|]+", name):
        return name
    return '"' + name.replace('"', "'") + '"'


def _fmt(v) -> str:
    v = round(float(v), 3)
    return str(int(v)) if v == int(v) else f"{v:g}"


def layer_name(n: int) -> str:
    return f"L{int(n)}"


@dataclass
class DsnSummary:
    path: str
    components: int = 0
    pins: int = 0
    nets: int = 0
    wires: int = 0
    vias: int = 0
    unknown_pads: List[str] = field(default_factory=list)   # named but not exported


# ---------------------------------------------------------------------------
# geometry collection
# ---------------------------------------------------------------------------

def _collect_pins(library, cell, layers, transform=None, ref="", out=None, depth=0):
    """[(component_ref, pin, layer, world_points)] for every numbered
    Rectangle/Polygon on ``layers``, descending into placed instances;
    an instance's label names its component, shapes in ``cell`` itself
    belong to "IO"."""
    from ..core.netlist import _compose_transforms, _sref_point_transform
    from ..core.shapes import Polygon, Rectangle
    out = [] if out is None else out
    t = transform or (lambda p: p)
    if depth > 32:
        return out
    for sh in cell.shapes:
        pin = getattr(sh, "pin", "") or ""
        if pin and isinstance(sh, (Rectangle, Polygon)) and sh.layer in layers:
            out.append((ref or "IO", str(pin), sh.layer, [t(p) for p in sh.points]))
    for r in cell.srefs:
        child = library.cells.get(r.cell_name)
        if child is None:
            continue
        ct = _compose_transforms(transform, _sref_point_transform(r))
        _collect_pins(library, child, layers, ct, r.label or ref, out, depth + 1)
    return out


def find_vias(cell, layers):
    """[(x, y, size)] -- via sites drawn directly in ``cell``: concentric
    same-size square pads on both conductor layers (what drop_via()
    stamps)."""
    from ..core.shapes import Polygon, Rectangle
    la, lb = layers

    def squares(lay):
        out = {}
        for s in cell.shapes:
            if s.layer != lay or not isinstance(s, (Rectangle, Polygon)) or getattr(s, "pin", ""):
                continue
            x0, y0, x1, y1 = s.bbox()
            w, h = x1 - x0, y1 - y0
            if w > 0 and abs(w - h) <= max(2, 0.02 * w):
                out[(round((x0 + x1) / 2), round((y0 + y1) / 2))] = w
        return out

    a, b = squares(la), squares(lb)
    return [(x, y, max(a[(x, y)], b[(x, y)])) for (x, y) in a if (x, y) in b]


def via_cell_sites(library, cell, layers):
    """[(x, y, size, sref)] from placed via-cell instances (a cell whose
    only copper is one concentric pad pair on the two conductor layers)."""
    from ..core.netlist import _sref_point_transform
    out = []
    for r in cell.srefs:
        child = library.cells.get(r.cell_name)
        if child is None or child.srefs or any(getattr(s, "pin", "") for s in child.shapes):
            continue
        v = find_vias(child, layers)
        if len(v) == 1:
            x, y = _sref_point_transform(r)((v[0][0], v[0][1]))
            out.append((round(x), round(y), v[0][2] * r.magnification, r))
    return out


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

def export_dsn(library, cell_name: str, path: str, *, layers: Tuple[int, int],
               rules: Optional[dict] = None, net_names: Optional[Dict[str, str]] = None,
               boundary_layer: Optional[int] = None, dielectric_layer: Optional[int] = None,
               design_name: Optional[str] = None,
               via_keepin: Optional[Tuple[float, float, float, float]] = None,
               write_wiring: bool = True,
               boundary_bbox: Optional[Tuple[float, float, float, float]] = None,
               edge_band: Optional[Tuple[Tuple[float, float, float, float], int]] = None,
               bond_pins: Optional[List[Tuple[str, float, float, float]]] = None,
               keepouts: Optional[List[Tuple[int, List[Tuple[float, float]]]]] = None,
               single_layer: bool = False
               ) -> DsnSummary:
    """Write ``cell_name`` as a Specctra DSN at ``path`` for Freerouting.
    ``layers`` = (1st conductor, 2nd conductor). ``rules`` overrides
    DEFAULT_RULES. ``net_names`` = {"RefDes.Pin": net}. The board
    outline is the bounding box of ``boundary_layer``'s shapes (default:
    of all the routed cell's copper and pads, grown 1 mm).

    ``via_keepin`` (x0, y0, x1, y1) is where vias may go -- the printed
    dielectric's own boundary, since a via is a window in it; the band
    between it and the board outline becomes a via keep-out. With
    ``write_wiring=False`` the existing tracks and vias are left out
    entirely (route the board from scratch). ``boundary_bbox`` overrides
    the outline.

    ``edge_band`` = ((x0, y0, x1, y1), layer): the band between that inner
    box (the printed dielectric's boundary) and the outline is closed to
    ``layer`` -- off the dielectric the two conductors aren't insulated,
    so only one of them may run there, and no via can go there. It is
    written as netless pads of a component ``HL_EDGE`` rather than as
    keepouts, because the Freerouting CLI native build crashes on
    keepout areas (UnsupportedFeatureError serialising ObstacleArea).

    Chip-and-wire: ``bond_pins`` = [(id, x, y, size)] are the substrate
    ends of the wire bonds -- the bond fingers the tracks must reach --
    written as square pins ``BOND.<id>`` of a component ``BOND`` (give
    them nets in ``net_names`` as "BOND.<id>"). ``keepouts`` =
    [(layer, [(x, y), ...])] are areas no track may cross (a die and its
    attach epoxy, the gap between a part's terminations, a resistor's
    ink), written as netless pads of a component ``HL_KEEP`` for the
    same crash reason as ``edge_band``. ``single_layer=True`` routes on
    ``layers[0]`` only (a one-conductor hybrid: no dielectric, no vias)."""
    from shapely.geometry import LineString, box
    from ..core.nettrace import NetTracer
    from ..core.shapes import Path

    R = dict(DEFAULT_RULES)
    R.update(rules or {})
    net_names = dict(net_names or {})
    la, lb = int(layers[0]), int(layers[1])
    cell = library.cells[cell_name]
    summ = DsnSummary(path=path)
    name = design_name or f"{library.name}_{cell_name}"

    # ---- pins -> components / padstacks --------------------------------
    pins = _collect_pins(library, cell, {la, lb})
    comps: Dict[str, list] = {}
    padstacks: Dict[str, str] = {}
    entry = float(R.get("io_entry_depth") or 0)
    if entry > 0 and pins:
        # Module I/O pads (drawn in the routed cell itself) may only be
        # connected within their first ``entry`` um from the inner edge:
        # the router sees just that strip as the pin, and the rest of the
        # pad -- set back by the clearance -- as a netless obstacle.
        xs_all = [p[0] for _, _, _, pts in pins for p in pts]
        ys_all = [p[1] for _, _, _, pts in pins for p in pts]
        mx, my = (min(xs_all) + max(xs_all)) / 2.0, (min(ys_all) + max(ys_all)) / 2.0
        split = []
        for ref, pin, lay, pts in pins:
            if ref != "IO":
                split.append((ref, pin, lay, pts))
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            bx0, by0, bx1, by1 = min(xs), min(ys), max(xs), max(ys)
            cx, cy = (bx0 + bx1) / 2.0, (by0 + by1) / 2.0
            dx, dy = cx - mx, cy - my
            gap = float(R["clearance"])
            if abs(dy) >= abs(dx):          # top/bottom edge: inner edge faces the centre
                if dy > 0:
                    strip, rest = (bx0, by0, bx1, by0 + entry), (bx0, by0 + entry + gap, bx1, by1)
                else:
                    strip, rest = (bx0, by1 - entry, bx1, by1), (bx0, by0, bx1, by1 - entry - gap)
            else:
                if dx > 0:
                    strip, rest = (bx0, by0, bx0 + entry, by1), (bx0 + entry + gap, by0, bx1, by1)
                else:
                    strip, rest = (bx1 - entry, by0, bx1, by1), (bx0, by0, bx1 - entry - gap, by1)
            rect = lambda b: [(b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3])]
            split.append((ref, pin, lay, rect(strip)))
            if rest[2] > rest[0] and rest[3] > rest[1]:
                split.append(("HL_IOREST", f"{pin}", lay, rect(rest)))
        pins = split
    for ref, pin, lay, pts in pins:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
        rect = len(pts) == 4 and all(
            abs(pts[i][0] - pts[(i + 1) % 4][0]) < 1e-6 or abs(pts[i][1] - pts[(i + 1) % 4][1]) < 1e-6
            for i in range(4))
        if rect:
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            ps = f"PAD_{layer_name(lay)}_{_fmt(w)}x{_fmt(h)}"
            padstacks.setdefault(ps, f"(rect {layer_name(lay)} {_fmt(-w / 2)} {_fmt(-h / 2)} "
                                     f"{_fmt(w / 2)} {_fmt(h / 2)})")
        else:
            rel = " ".join(f"{_fmt(x - cx)} {_fmt(y - cy)}" for x, y in pts + [pts[0]])
            ps = f"POLY_{layer_name(lay)}_{len(padstacks)}"
            padstacks[ps] = f"(polygon {layer_name(lay)} 0 {rel})"
        comps.setdefault(ref, []).append((pin, ps, cx, cy))

    # wire-bond landing points: square bond-finger pins, BOND.<id>
    for bid, bx, by, bsize in (bond_pins or []):
        ps = f"PAD_{layer_name(la)}_{_fmt(bsize)}x{_fmt(bsize)}"
        h = bsize / 2.0
        padstacks.setdefault(ps, f"(rect {layer_name(la)} {_fmt(-h)} {_fmt(-h)} {_fmt(h)} {_fmt(h)})")
        comps.setdefault("BOND", []).append((str(bid), ps, float(bx), float(by)))

    # Each component's image: its pins relative to the component's own
    # centroid, placed unrotated at that centroid -- already in world
    # orientation, so no rotation bookkeeping can go wrong.
    images = {}
    places = {}
    for ref, plist in comps.items():
        if ref == "IO":
            ox = oy = 0.0
        else:
            ox = round(sum(p[2] for p in plist) / len(plist))
            oy = round(sum(p[3] for p in plist) / len(plist))
        seen = set()
        lines = []
        for pin, ps, cx, cy in plist:
            pid, k = pin, 2
            while pid in seen:              # duplicate pin numbers: keep them distinct
                pid = f"{pin}@{k}"
                k += 1
            seen.add(pid)
            lines.append(f"      (pin {_q(ps)} {_q(pid)} {_fmt(cx - ox)} {_fmt(cy - oy)})")
        images[ref] = lines
        places[ref] = (ox, oy)
        summ.pins += len(plist)
    summ.components = len(comps)
    exported_pads = {f"{ref}.{pin}" for ref, pl in comps.items() for pin, *_ in pl}

    # ---- nets -------------------------------------------------------------
    nets: Dict[str, List[str]] = {}
    for pad, net in sorted(net_names.items()):
        if pad not in exported_pads:
            summ.unknown_pads.append(pad)
            continue
        ref, _, pin = pad.partition(".")
        nets.setdefault(net, []).append(f"{ref}-{pin}")
    summ.nets = len(nets)
    power = {n.upper() for n in R.get("power_nets") or []}
    excluded = {n.upper() for n in R.get("exclude_nets") or []}

    # ---- existing copper -> wiring ---------------------------------------
    try:
        tracer = NetTracer(library, cell, [la, lb], dielectric_layer=dielectric_layer)
        root_net = {}
        for k in range(len(tracer.nodes)):
            root = tracer.dsu.find(k)
            for p in tracer.node_pins.get(k, []):
                if p in net_names:
                    root_net.setdefault(root, set()).add(net_names[p])
    except Exception:
        tracer = None

    def net_at(geom, lay):
        if tracer is None:
            return None
        for j in tracer._query(geom):
            if tracer.nodes[j][0] == lay and tracer._geoms[j].intersects(geom):
                names = root_net.get(tracer.dsu.find(j), set())
                return next(iter(names)) if len(names) == 1 else None
        return None

    wire_type = "protect" if R.get("keep_existing", True) else "route"
    wiring = []
    for sh in cell.shapes:
        if isinstance(sh, Path) and sh.layer in (la, lb) and len(sh.points) >= 2:
            g = LineString(sh.points).buffer(max(sh.width, 1) / 2.0)
            n = net_at(g, sh.layer)
            coords = "  ".join(f"{_fmt(x)} {_fmt(y)}" for x, y in sh.points)
            wiring.append(f"    (wire (path {layer_name(sh.layer)} {_fmt(sh.width)}  {coords})"
                          + (f"(net {_q(n)})" if n else "") + f"(type {wire_type}))")
            summ.wires += 1
    sites = find_vias(cell, (la, lb)) + [(x, y, w) for x, y, w, _r in
                                         via_cell_sites(library, cell, (la, lb))]
    for x, y, w in sites:
        n = net_at(box(x - w / 2, y - w / 2, x + w / 2, y + w / 2), la)
        ps = VIA_PWR if w >= (R["signal_via"] + R["power_via"]) / 2.0 else VIA_SIG
        wiring.append(f"    (via {ps} {_fmt(x)} {_fmt(y)}" + (f"(net {_q(n)})" if n else "")
                      + f"(type {wire_type}))")
        summ.vias += 1

    # ---- boundary ----------------------------------------------------------
    bb = tuple(boundary_bbox) if boundary_bbox is not None else None
    if bb is None and boundary_layer is not None and boundary_layer >= 0:
        bbs = [s.bbox() for s in cell.shapes if s.layer == boundary_layer and hasattr(s, "bbox")]
        if bbs:
            bb = (min(b[0] for b in bbs), min(b[1] for b in bbs),
                  max(b[2] for b in bbs), max(b[3] for b in bbs))
    if bb is None:
        xs = [p[0] for _, _, _, pts in pins for p in pts]
        ys = [p[1] for _, _, _, pts in pins for p in pts]
        for sh in cell.shapes:
            if sh.layer in (la, lb) and hasattr(sh, "bbox"):
                x0, y0, x1, y1 = sh.bbox()
                xs += [x0, x1]
                ys += [y0, y1]
        if not xs:
            raise ValueError("nothing to route: no pads or copper on the routing layers")
        bb = (min(xs) - 1000, min(ys) - 1000, max(xs) + 1000, max(ys) + 1000)
    x0, y0, x1, y1 = bb

    if edge_band is not None:
        (kx0, ky0, kx1, ky1), elay = edge_band
        bands = [(x0, y0, x1, ky0), (x0, ky1, x1, y1), (x0, ky0, kx0, ky1), (kx1, ky0, x1, ky1)]
        lines = []
        for k, (bx0, by0, bx1, by1) in enumerate(bands):
            if bx1 - bx0 <= 0 or by1 - by0 <= 0:
                continue
            ps = f"HL_EDGE_{k}"
            w2, h2 = (bx1 - bx0) / 2.0, (by1 - by0) / 2.0
            padstacks[ps] = (f"(rect {layer_name(elay)} {_fmt(-w2)} {_fmt(-h2)} "
                             f"{_fmt(w2)} {_fmt(h2)})")
            lines.append(f"      (pin {ps} E{k} {_fmt((bx0 + bx1) / 2)} {_fmt((by0 + by1) / 2)})")
        if lines:
            images["HL_EDGE"] = lines
            places["HL_EDGE"] = (0, 0)

    if keepouts:
        lines = []
        for k, (klay, kpts) in enumerate(keepouts):
            if len(kpts) < 3:
                continue
            kx = [p[0] for p in kpts]
            ky = [p[1] for p in kpts]
            kcx, kcy = (min(kx) + max(kx)) / 2.0, (min(ky) + max(ky)) / 2.0
            rel = " ".join(f"{_fmt(x - kcx)} {_fmt(y - kcy)}" for x, y in list(kpts) + [kpts[0]])
            ps = f"HL_KEEP_{k}"
            padstacks[ps] = f"(polygon {layer_name(klay)} 0 {rel})"
            lines.append(f"      (pin {ps} K{k} {_fmt(kcx)} {_fmt(kcy)})")
        if lines:
            images["HL_KEEP"] = lines
            places["HL_KEEP"] = (0, 0)
    route_layers = (la,) if single_layer else (lb, la)

    # ---- write -------------------------------------------------------------
    L = []
    w = L.append
    w(f"(pcb {_q(name)}")
    w("  (parser")
    w('    (string_quote ")')
    w("    (space_in_quoted_tokens on)")
    w('    (host_cad "HLayout")')
    w('    (host_version "1")')
    w("  )")
    w("  (resolution um 1)")
    w("  (unit um)")
    w("  (structure")
    for i, lay in enumerate(route_layers):           # top (2nd conductor) first
        w(f"    (layer {layer_name(lay)} (type signal) (property (index {i})))")
    w(f"    (boundary (path pcb 0  {_fmt(x0)} {_fmt(y0)}  {_fmt(x1)} {_fmt(y0)}  "
      f"{_fmt(x1)} {_fmt(y1)}  {_fmt(x0)} {_fmt(y1)}  {_fmt(x0)} {_fmt(y0)}))")
    w(f"    (via {VIA_SIG} {VIA_PWR})")
    w(f"    (rule (width {_fmt(R['signal_width'])}) (clearance {_fmt(R['clearance'])}))")
    if via_keepin is not None:
        kx0, ky0, kx1, ky1 = via_keepin
        kx0, ky0 = max(kx0, x0), max(ky0, y0)
        kx1, ky1 = min(kx1, x1), min(ky1, y1)
        bands = [(x0, y0, x1, ky0), (x0, ky1, x1, y1), (x0, ky0, kx0, ky1), (kx1, ky0, x1, ky1)]
        for bx0, by0, bx1, by1 in bands:
            if bx1 - bx0 > 0 and by1 - by0 > 0:
                for lay in route_layers:
                    w(f"    (via_keepout (rect {layer_name(lay)} {_fmt(bx0)} {_fmt(by0)} "
                      f"{_fmt(bx1)} {_fmt(by1)}))")
    if R.get("snap_90", True):
        w("    (snap_angle ninety_degree)")
    w("  )")
    w("  (placement")
    for ref, (ox, oy) in places.items():
        w(f"    (component {_q('IMG_' + ref)} (place {_q(ref)} {_fmt(ox)} {_fmt(oy)} front 0))")
    w("  )")
    w("  (library")
    for ref, lines in images.items():
        w(f"    (image {_q('IMG_' + ref)}")
        L.extend(lines)
        w("    )")
    for ps, shape in padstacks.items():
        w(f"    (padstack {_q(ps)} (shape {shape}) (attach off))")
    for ps, size in ((VIA_SIG, R["signal_via"]), (VIA_PWR, R["power_via"])):
        h = size / 2.0
        w(f"    (padstack {ps}")
        for lay in route_layers:
            w(f"      (shape (rect {layer_name(lay)} {_fmt(-h)} {_fmt(-h)} {_fmt(h)} {_fmt(h)}))")
        w("      (attach off))")
    w("  )")
    w("  (network")
    for net, plist in nets.items():
        w(f"    (net {_q(net)} (pins {' '.join(_q(p) for p in plist)}))")
    sig = [n for n in nets if n.upper() not in power and n.upper() not in excluded]
    pwr = [n for n in nets if n.upper() in power and n.upper() not in excluded]
    exc = [n for n in nets if n.upper() in excluded]
    for cname, members, wid, via in (("HL_SIGNAL", sig, R["signal_width"], VIA_SIG),
                                      ("HL_POWER", pwr, R["power_width"], VIA_PWR),
                                      (EXCLUDED_CLASS, exc, R["signal_width"], VIA_SIG)):
        if not members:
            continue
        w(f"    (class {cname} {' '.join(_q(n) for n in members)}")
        w(f"      (circuit (use_via {via}))")
        w(f"      (rule (width {_fmt(wid)}) (clearance {_fmt(R['clearance'])})))")
    w("  )")
    w("  (wiring")
    if write_wiring:
        L.extend(wiring)
    w("  )")
    w(")")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    return summ


# ---------------------------------------------------------------------------
# SES parsing
# ---------------------------------------------------------------------------

def _tokenize(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c in "()":
            yield c
            i += 1
        elif c == '"':
            j = text.find('"', i + 1)
            j = n if j < 0 else j
            yield ("Q", text[i + 1:j])
            i = j + 1
        else:
            j = i
            while j < n and text[j] not in ' \t\r\n()"':
                j += 1
            yield text[i:j]
            i = j


def parse_sexpr(text: str):
    stack = [[]]
    for tok in _tokenize(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            if len(stack) > 1:
                done = stack.pop()
                stack[-1].append(done)
        else:
            stack[-1].append(tok[1] if isinstance(tok, tuple) else tok)
    while len(stack) > 1:                         # tolerate a truncated file
        done = stack.pop()
        stack[-1].append(done)
    return stack[0]


def _find(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


@dataclass
class SesResult:
    wires: List[Tuple[str, str, float, List[Tuple[float, float]]]] = field(default_factory=list)
    vias: List[Tuple[str, str, float, float]] = field(default_factory=list)


def _scale(res_node) -> float:
    """DBU (um) per file unit for a (resolution <unit> <n>) node."""
    if not res_node or len(res_node) < 3:
        return 1.0
    unit, n = str(res_node[1]).lower(), float(res_node[2])
    per = {"um": 1.0, "mm": 1000.0, "mil": 25.4, "inch": 25400.0, "cm": 10000.0}.get(unit, 1.0)
    return per / n


def parse_ses(path: str) -> SesResult:
    """Wires and vias from a Specctra session file, in um:
    wires = [(net, layer_name, width, [(x, y), ...])],
    vias  = [(net, padstack, x, y)]."""
    with open(path, encoding="utf-8", errors="replace") as f:
        tree = parse_sexpr(f.read())
    top = tree[0] if tree and isinstance(tree[0], list) else tree
    out = SesResult()
    for routes in _find(top, "routes"):
        res = _find(routes, "resolution")
        k = _scale(res[0] if res else None)
        for no in _find(routes, "network_out"):
            for net in _find(no, "net"):
                nname = str(net[1])
                for wire in _find(net, "wire"):
                    for p in _find(wire, "path") + _find(wire, "polyline_path"):
                        lay, width = str(p[1]), float(p[2]) * k
                        nums = [float(v) for v in p[3:] if not isinstance(v, list)]
                        pts = [(nums[i] * k, nums[i + 1] * k) for i in range(0, len(nums) - 1, 2)]
                        if len(pts) >= 2:
                            out.wires.append((nname, lay, width, pts))
                for via in _find(net, "via"):
                    out.vias.append((nname, str(via[1]), float(via[2]) * k, float(via[3]) * k))
    return out


# ---------------------------------------------------------------------------
# running Freerouting
# ---------------------------------------------------------------------------

def find_freerouting(saved: Optional[str] = None) -> Optional[str]:
    """Path to a Freerouting launcher (.exe / app) or .jar: ``saved`` if
    it exists, else a standard install location, else None."""
    import glob
    if saved and os.path.exists(saved):
        return saved
    cands = []
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        for pat in ("Freerouting*", "freerouting*"):
            cands += glob.glob(os.path.join(base, pat, "*.exe"))
    home = os.path.expanduser("~")
    for d in (os.path.join(home, ".hlayout"), os.path.join(home, ".hlayout", "freerouting"),
              home, os.path.join(home, "Downloads")):
        # the native CLI build (no Java needed) first, then a .jar
        cands += sorted(glob.glob(os.path.join(d, "freerouting-cli*")))
        cands += sorted(glob.glob(os.path.join(d, "freerouting*", "freerouting-cli*")))
        cands += sorted(glob.glob(os.path.join(d, "freerouting*.jar")))
    cands += ["/Applications/Freerouting.app/Contents/MacOS/Freerouting",
              "/opt/freerouting/bin/Freerouting", "/usr/bin/freerouting"]
    for c in cands:
        b = os.path.basename(c).lower()
        if os.path.isfile(c) and "uninstall" not in b and not b.endswith((".zip", ".gz", ".txt")):
            return c
    return None


def find_java(saved: Optional[str] = None) -> Optional[str]:
    import shutil
    if saved and os.path.exists(saved):
        return saved
    jh = os.environ.get("JAVA_HOME")
    if jh:
        for exe in ("java.exe", "java"):
            p = os.path.join(jh, "bin", exe)
            if os.path.exists(p):
                return p
    return shutil.which("java")


def freerouting_command(router: str, dsn: str, ses: str, passes: int = 100,
                        exclude_classes: Iterable[str] = (), java: Optional[str] = None,
                        extra: Iterable[str] = ()) -> List[str]:
    """The headless command line. ``router`` is a .jar (run with java)
    or a native launcher."""
    if router.lower().endswith(".jar"):
        j = find_java(java)
        if not j:
            raise RuntimeError("Freerouting is a .jar but no Java runtime was found -- "
                               "install Java, or the Freerouting installer (it bundles one)")
        cmd = [j, "-jar", router]
    else:
        cmd = [router]
    if "freerouting-cli" in os.path.basename(router).lower():
        # The native CLI build (v2.5.0) crashes the moment it snapshots the
        # board between passes (UnsupportedFeatureError in BoardHistory), so
        # give it exactly one pass and no optimiser -- the step it reaches
        # safely. The Java build (.jar / installer) takes the full count.
        passes = 1
        extra = ["-mt", "0"] + list(extra)
    cmd += ["-de", dsn, "-do", ses, "-mp", str(int(passes)), "--gui.enabled=false", "-da"]
    exc = [c for c in exclude_classes if c]
    if exc:
        cmd += ["-inc", ",".join(exc)]
    cmd += list(extra)
    return cmd


def run_freerouting(cmd: List[str], ses: str, timeout: Optional[float] = None,
                    on_line=None, cancel=None) -> Tuple[int, List[str]]:
    """Run ``cmd``; feed each output line to ``on_line``; stop early if
    ``cancel()`` turns true. Returns (exit code, last output lines).
    The caller checks that ``ses`` was written."""
    import subprocess
    import threading
    import time
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        if os.path.exists(ses):
            os.remove(ses)
    except OSError:
        pass
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, errors="replace",
                            creationflags=flags)
    tail: List[str] = []
    crashed = []

    def pump():
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            if line.startswith('Exception in thread "') and not crashed:
                # a worker thread died (e.g. the native CLI build's
                # UnsupportedFeatureError): the process then idles forever
                crashed.append(time.time())
            tail.append(line)
            del tail[:-40]
            if on_line:
                try:
                    on_line(line)
                except Exception:
                    pass

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    start = time.time()
    while proc.poll() is None:
        if cancel and cancel():
            proc.kill()
            tail.append("(cancelled)")
            break
        if crashed and time.time() - crashed[0] > 15:
            proc.kill()
            tail.append("(Freerouting crashed -- stopped it; see the exception above)")
            break
        if timeout and time.time() - start > timeout:
            proc.kill()
            tail.append(f"(stopped after {int(timeout)} s)")
            break
        time.sleep(0.2)
    t.join(timeout=5)
    return proc.wait(), tail
