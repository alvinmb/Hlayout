"""Net-aware router for the ALU16 module on the design open in HLayout (TOP).

Every pad and every piece of routed copper carries its net; a trace may only
touch copper of its own net (the stock auto_route treats any copper connected
to an endpoint as passable, which let one short cascade along the edge pins).

Process rules:
  * LCC and capacitor pads are reached on layer 5 (2nd conductor) only.
  * Module edge pins are reached on layer 3 (1st conductor) only; layer 5
    never touches an edge pin.
  * Layer 3 is otherwise a buried cross-under: it may pass beneath LCC pads.
  * Every layer change is a via (pads on 3 and 5, window on dielectric 4).
  * Everything stays inside the substrate outline (layer 57).
Power (VCC/GND incl. the 0805 decoupling caps): 0.6 mm, 0.7 mm via, 0.8 mm window.
Signals: 0.3 mm, 0.4 mm via, 0.6 mm window. 0.1 mm grid and clearance.

Use: setup(s); clear_routing(); then run('P'), run('B'), run('C') repeatedly
(each call does ~limit_s seconds of work as one undo step) until nothing is left.
"""
import csv, math, os, time
from collections import defaultdict, Counter
from shapely.geometry import LineString, box, Point
from shapely.ops import unary_union
from hlayout.core.errors import LayoutError
from hlayout.core.shapes import Rectangle, Path as HPath
from hlayout.core.library import _transform_bbox
from hlayout.core import autoroute as AR

NETLIST = r"C:\dev\HLayout\example2\alu16_netlist\alu16_netlist.csv"
PINOUT = os.path.expanduser(r"~\Downloads\alu16_module_pinout.csv")
L3, L4, L5, SUBSTRATE = 3, 4, 5, 57
GRID, CLR = 100, 100
POWER = {"VCC", "GND"}
PWR = dict(w=600, via=700, win=800)
SIG = dict(w=300, via=400, win=600)

S = None; TOP = None
ST = {"conns": [], "res": {}, "routed": [], "pads5": [], "pins": [], "sub": None}


def setup(session):
    global S, TOP
    S = session; TOP = S.library.cells["TOP"]
    nets = defaultdict(list)
    for row in csv.DictReader(open(NETLIST, encoding="utf-8")):
        ref = row["ref"]
        pin = row["lcc_pin"] if ref.startswith("U") else row["dip_die_pad"]
        nets[row["net"]].append((ref, str(pin)))
    for r in TOP.srefs:
        if r.label.startswith("C"):
            nets["VCC"].append((r.label, "1")); nets["GND"].append((r.label, "2"))
    netof = {m: n for n, mem in nets.items() for m in mem}
    was2mod = {r["was"]: r["module_pin"] for r in csv.DictReader(open(PINOUT)) if r["was"]}
    mod2net = {}
    for (ref, pin), n in netof.items():
        if ref.startswith("J"):
            mod2net[was2mod[f"{ref}.{pin}"]] = n
    pads = {}
    for r in TOP.srefs:
        for sh in S.library.cells[r.cell_name].shapes:
            if sh.layer == 1 and getattr(sh, "pin", ""):
                b = _transform_bbox(sh.bbox(), r)
                pads[(r.label, sh.pin)] = b
                ST["pads5"].append((box(*b), netof.get((r.label, sh.pin), "NC:%s.%s" % (r.label, sh.pin))))
    modpin = {sh.pin: sh for sh in TOP.shapes if isinstance(sh, Rectangle) and sh.layer == 2}
    for k, sh in modpin.items():
        ST["pins"].append((box(*sh.bbox()), mod2net.get(k, "SPARE:" + k)))
    sub = [sh for sh in TOP.shapes if sh.layer == SUBSTRATE][0].bbox()
    ST["sub"] = box(*sub)

    def ep(ref, pin):
        if ref.startswith("J"):
            b = modpin[was2mod[f"{ref}.{pin}"]].bbox(); kind = "J"
        else:
            b = pads[(ref, pin)]; kind = "U"
        return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0), b, kind

    conns = []
    for net, mem in nets.items():
        if len(mem) < 2:
            continue
        pts = [(m,) + ep(*m) for m in mem]
        inn = {0}
        while len(inn) < len(pts):
            i, j = min(((i, j) for i in inn for j in range(len(pts)) if j not in inn),
                       key=lambda ij: abs(pts[ij[0]][1][0] - pts[ij[1]][1][0]) + abs(pts[ij[0]][1][1] - pts[ij[1]][1][1]))
            inn.add(j)
            a, b = pts[i], pts[j]
            if a[3] == "J" and b[3] == "U":
                a, b = b, a
            L = abs(a[1][0] - b[1][0]) + abs(a[1][1] - b[1][1])
            conns.append(dict(L=L, net=net, a=a[0], b=b[0], pa=a[1], pb=b[1], kind=a[3] + b[3],
                              power=net in POWER, **(PWR if net in POWER else SIG)))
    conns.sort(key=lambda c: (not c["power"], c["L"]))
    ST["conns"] = conns
    return Counter(c["kind"] for c in conns)


def clear_routing():
    with S.batch():
        TOP.shapes[:] = [sh for sh in TOP.shapes if sh.layer not in (L3, L4, L5)]
    S._notify()
    ST["res"].clear(); ST["routed"].clear()


def _obstacles(layer, net):
    obs = [g for (lay, g, n, _) in ST["routed"] if lay == layer and n != net]
    if layer == L5:
        obs += [g for g, n in ST["pads5"] if n != net]
        obs += [g for g, n in ST["pins"]]                 # layer 5 never touches an edge pin
    else:
        obs += [g for g, n in ST["pins"] if n != net]
    return unary_union(obs) if obs else Point(0, 0).buffer(0)


def _route(a, b, layer, net, w, margin=None):
    O = _obstacles(layer, net)
    if not O.is_empty:
        O = O.buffer(w / 2.0 + CLR, join_style=2, mitre_limit=5.0)
    keepin = ST["sub"].buffer(-w / 2.0, join_style=2)
    mrg = margin if margin is not None else 12 * GRID
    kx0, ky0, kx1, ky1 = keepin.bounds
    x0 = max(min(a[0], b[0]) - mrg, kx0 - GRID); y0 = max(min(a[1], b[1]) - mrg, ky0 - GRID)
    x1 = min(max(a[0], b[0]) + mrg, kx1 + GRID); y1 = min(max(a[1], b[1]) + mrg, ky1 + GRID)
    nx = int((x1 - x0) // GRID) + 1; ny = int((y1 - y0) // GRID) + 1
    try:
        pts = AR.route(a, b, O, GRID, int(round(x0)), int(round(y0)), nx, ny, keepin=keepin)
    except ValueError as e:
        raise LayoutError(str(e))
    if not pts:
        raise LayoutError("no route")
    sh = TOP.add_path(layer, [(int(x), int(y)) for x, y in pts], int(w), cap="round")
    ST["routed"].append((layer, LineString(pts).buffer(w / 2.0), net, sh))
    return sh


def _via(x, y, net, size, win):
    h, hw = size / 2.0, win / 2.0
    p3 = TOP.add_rectangle(L3, int(x - h), int(y - h), int(x + h), int(y + h))
    p5 = TOP.add_rectangle(L5, int(x - h), int(y - h), int(x + h), int(y + h))
    p4 = TOP.add_rectangle(L4, int(x - hw), int(y - hw), int(x + hw), int(y + hw))
    g = box(x - h, y - h, x + h, y + h)
    ST["routed"] += [(L3, g, net, p3), (L5, g, net, p5), (L4, None, net, p4)]


def _via_sites(pt, net, w, size, rmin=900, rmax=7000, n=6):
    keep = CLR + w / 2.0
    g3 = _obstacles(L3, net)
    g5 = unary_union([_obstacles(L5, net)] + [g for g, nn in ST["pads5"]])   # never on a pad
    lim = ST["sub"].bounds[2] - 2000 - size / 2.0
    cx, cy = pt
    cands = sorted((math.hypot(dx, dy), cx + dx, cy + dy)
                   for dx in range(-rmax, rmax + 1, 2 * GRID) for dy in range(-rmax, rmax + 1, 2 * GRID)
                   if rmin <= math.hypot(dx, dy) <= rmax)
    out = []
    for d, x, y in cands:
        if abs(x) > lim or abs(y) > lim:
            continue
        sq = box(x - size / 2.0, y - size / 2.0, x + size / 2.0, y + size / 2.0)
        if g3.distance(sq) >= keep and g5.distance(sq) >= keep:
            out.append((x, y))
            if len(out) >= n:
                break
    return out


def _mark():
    return len(ST["routed"])


def _undo_to(m):
    gone = ST["routed"][m:]
    del ST["routed"][m:]
    ids = {id(x[3]) for x in gone}
    TOP.shapes[:] = [sh for sh in TOP.shapes if id(sh) not in ids]


def _stub_via(pt, c, stub_layer):
    for (vx, vy) in _via_sites(pt, c["net"], c["w"], c["via"]):
        m = _mark()
        try:
            _route(pt, (vx, vy), stub_layer, c["net"], c["w"], margin=3000)
        except LayoutError:
            _undo_to(m); continue
        _via(vx, vy, c["net"], c["via"], c["win"])
        return (vx, vy)
    return None


def _try_via(c):
    m0 = _mark()
    net, w = c["net"], c["w"]
    if c["kind"] == "UJ":
        v = _stub_via(c["pa"], c, L5)
        if v is not None:
            try:
                _route(v, c["pb"], L3, net, w, margin=20000); return "ok via"
            except LayoutError:
                _undo_to(m0)
        v = _stub_via(c["pb"], c, L3)
        if v is None:
            _undo_to(m0); return "no via site near %s.%s" % c["b"]
        try:
            _route(v, c["pa"], L5, net, w, margin=20000); return "ok via"
        except LayoutError:
            _undo_to(m0); return "no route"
    if c["kind"] == "UU":
        va = _stub_via(c["pa"], c, L5)
        if va is None:
            _undo_to(m0); return "no via site near %s.%s" % c["a"]
        vb = _stub_via(c["pb"], c, L5)
        if vb is None:
            _undo_to(m0); return "no via site near %s.%s" % c["b"]
        try:
            _route(va, vb, L3, net, w, margin=20000); return "ok via"
        except LayoutError:
            _undo_to(m0); return "no cross-under route"
    return "no route"


def run(phase, limit_s=35):
    """'P' power (direct, else via), 'B' signal direct (UU on 5, JJ on 3), 'C' signal via."""
    res = ST["res"]; t0 = time.time(); n = 0
    with S.batch():
        for i, c in enumerate(ST["conns"]):
            if time.time() - t0 > limit_s:
                break
            if (phase == "P") != c["power"]:
                continue
            done = res.get(i, "")
            if done.startswith("ok") or (phase != "C" and i in res) or done.startswith("C:"):
                continue
            if phase in ("P", "B"):
                lay = L5 if c["kind"] == "UU" else (L3 if c["kind"] == "JJ" else None)
                if lay is not None:
                    m = _mark()
                    try:
                        _route(c["pa"], c["pb"], lay, c["net"], c["w"]); res[i] = "ok L%d" % lay; n += 1; continue
                    except LayoutError:
                        _undo_to(m)
                if phase == "B":
                    res[i] = "direct failed"; n += 1; continue
            r = _try_via(c)
            res[i] = r if r.startswith("ok") else ("C: " if phase == "C" else "P: ") + r
            n += 1
    S._notify()
    return n, round(time.time() - t0, 1), Counter(v.split(" near")[0] for v in res.values())
