"""Build a 2-layer hybrid circuit into hlayout/examples/test.gds.

Stack (per the technology -- the layer NAMES in older tech files are
misleading, these are the real roles)
-------------------------------------------------------------------
    L2  Top Edge Pad            -- not used here
    L3  1st Routing Conductor   -- horizontal trunks, power rails, AND
                                  every component land (SMD footprint
                                  pads + printed-resistor terminations)
    L4  Routing Dielectric      -- solid field between the two routing
                                  conductors, with a hole at every via
    L5  2nd Routing Conductor   -- vertical stubs

L3 and L5 crossing in plan view is NOT a short -- the L4 dielectric
separates them; they join only where a via (a pad on L3 and L5 with a
window cut in the L4 dielectric) is dropped.  Routing discipline:

    * every horizontal run  -> L3   (same layer as the part lands, so a
                                     trunk meeting a pad just connects)
    * every vertical run    -> L5
    * a via L3<->L5 at every corner and at every part pad

Bill of materials
-----------------
* U1  IC_SOIC14   -- 14-pin SOIC (from attached SMD_LIBRARY, docs/smd_library.gds)
* Q1  T_SOT23     -- SOT-23 transistor
* C1,C2  C_0805   -- 0805 decoupling caps
* R1..R8          -- 8 printed thick-film resistors, each SIZED by the
                     Thick-Film Resistor Designer rule (aim = value x
                     (aim% + tol%); pick the ink whose square count is
                     closest to 1 within the aspect-ratio window), drawn
                     on its per-value ink layer (10..30k ohm/sq -> L8..L15).

Run:  python hlayout/examples/build_hybrid_2layer.py
"""
import os
import sys
import math

_here = os.path.dirname(os.path.abspath(__file__))
_root = _here
for _ in range(4):
    if os.path.isdir(os.path.join(_root, "hlayout")):
        break
    _root = os.path.dirname(_root)
sys.path.insert(0, _root)

from hlayout.scripting.api import Session

MM = 1000

TEST_GDS = os.path.join(_root, "hlayout", "examples", "test.gds")
TECH = os.path.join(_root, "hlayout", "examples", "ThickFilm_2Con.json")
SMD_LIB = os.path.join(_root, "docs", "smd_library.gds")

L_H = 3         # 1st Routing Conductor -- horizontal trunks + part lands
L_V = 5         # 2nd Routing Conductor -- vertical stubs
L_DIE = 4       # Routing Dielectric: via windows + resistor keep-outs are
                # drawn here at datatype 0; generate_dielectric writes the
                # filled sheet here at datatype 1
DIE_MARGIN = 1000
VIA = 300       # via side (DBU)
W_SIG = 200
W_PWR = 400

# --- Thick-Film Resistor Designer sizing ------------------------------
AIM_PCT = 75.0
AR_MIN, AR_MAX = 0.25, 4.00
INKS = [10, 30, 100, 300, 1000, 3000, 10000, 30000, 100000, 300000, 1000000]
BODY_W = 1000  # 1.0 mm body (62.5 mW class)


def size_resistor(value_ohms, tol_frac=0.01):
    aim = value_ohms * (AIM_PCT / 100.0 + tol_frac)
    cand = []
    for rsh in INKS:
        sq = aim / rsh
        if AR_MIN <= sq <= AR_MAX:
            cand.append((abs(math.log(sq)), rsh, sq))
    if not cand:
        raise ValueError(f"no ink for {value_ohms} ohm")
    cand.sort()
    _, rsh, sq = cand[0]
    return rsh, sq, int(round(sq * BODY_W))


R_TARGETS = [("R1", 13.0), ("R2", 40.0), ("R3", 130.0), ("R4", 400.0),
             ("R5", 1300.0), ("R6", 4000.0), ("R7", 13000.0), ("R8", 40000.0)]


def _rot(x, y, deg):
    r = math.radians(deg); c, s = math.cos(r), math.sin(r)
    return (x * c - y * s, x * s + y * c)


def pad_geom(session, ref, pin):
    """(center_x, center_y, (x0,y0,x1,y1), layer) of a part pad, in world DBU."""
    cell = session.library.cells[ref.cell_name]
    for sh in cell.shapes:
        if getattr(sh, "pin", "") == str(pin):
            x0, y0, x1, y1 = sh.bbox()
            corners = [_rot(x0, y0, ref.rotation), _rot(x1, y0, ref.rotation),
                       _rot(x1, y1, ref.rotation), _rot(x0, y1, ref.rotation)]
            ox, oy = ref.origin
            xs = [ox + cx for cx, _ in corners]; ys = [oy + cy for _, cy in corners]
            return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2,
                    (min(xs), min(ys), max(xs), max(ys)), sh.layer)
    raise KeyError(f"{ref.cell_name}: no pin {pin!r}")


def main():
    # build from scratch every run so re-running never stacks a second
    # copy of the circuit onto a previously-saved test.gds
    for stale in (TEST_GDS, TEST_GDS + ".hlaux"):
        if os.path.exists(stale):
            os.remove(stale)
    s = Session.new("HYBRID")
    top = "TOP"
    s.new_cell(top)
    s.use_cell(top)
    s.load_technology(TECH)
    s.attach_library(SMD_LIB)
    print("cell:", top, "| attached:", [a["name"] for a in s.list_attached_libraries()])

    ref = {}

    def place(name, cellname, x_mm, y_mm, rot=0.0):
        ref[name] = s.place_cell(cellname, (x_mm * MM, y_mm * MM),
                                 rotation=rot, label=name)

    # ---- components ------------------------------------------------
    place("U1", "IC_SOIC14", 0, 0, 0)
    place("Q1", "T_SOT23", 20, -18, 0)
    place("C1", "C_0805", -5, 16, 0)
    place("C2", "C_0805", 5, 16, 0)

    # 8 thick-film resistors, horizontal, 5 mm pitch below the IC
    r_x = [-17.5, -12.5, -7.5, -2.5, 2.5, 7.5, 12.5, 17.5]
    for (rd, val), xm in zip(R_TARGETS, r_x):
        rsh, sq, blen = size_resistor(val)
        s.generate_resistor(xm * MM, -18 * MM, BODY_W, blen, rotation=0.0,
                            ink_rsh=rsh, ref_des=rd)
        ref[rd] = s.library.cells[top].srefs[-1]
        print(f"  {rd}: {val:>8.1f} ohm  ink {rsh:>6} ohm/sq  {sq:.3f} sq  "
              f"L={blen/1000:.3f} mm")

    # ---- netlist (per net: channel Y in mm, trace width, pad refs) --
    def PC(rd, pin):
        cx, cy, _, _ = pad_geom(s, ref[rd], pin)
        return (cx, cy)

    nets = {
        "VCC":  (13,  W_PWR, [("U1", 14), ("C1", 2), ("C2", 2), ("Q1", 3), ("R1", 2), ("R2", 2)]),
        "GND":  (-15, W_PWR, [("U1", 7), ("C1", 1), ("C2", 1), ("Q1", 2), ("R4", 2), ("R5", 2), ("R8", 1)]),
        "BASE": (-13, W_SIG, [("Q1", 1), ("R3", 2), ("R7", 2)]),
        "N1":   (-5,  W_SIG, [("U1", 1), ("R1", 1)]),
        "N2":   (-6,  W_SIG, [("U1", 2), ("R2", 1)]),
        "N3":   (-7,  W_SIG, [("U1", 3), ("R3", 1)]),
        "N4":   (-8,  W_SIG, [("U1", 4), ("R4", 1)]),
        "N5":   (-9,  W_SIG, [("U1", 5), ("R5", 1)]),
        "N6":   (-10, W_SIG, [("U1", 6), ("R6", 1)]),
        "N10":  (5,   W_SIG, [("U1", 10), ("R7", 1)]),
        "N11":  (6,   W_SIG, [("U1", 11), ("R8", 2)]),
        "FB":   (8,   W_SIG, [("U1", 9), ("R6", 2)]),
    }

    from shapely.geometry import LineString, box
    from shapely.ops import unary_union

    geo = {}   # (net, layer) -> [poly]
    via_pts = []

    def g_add(net, layer, poly):
        geo.setdefault((net, layer), []).append(poly)

    def g_line(net, layer, pts, w):
        g_add(net, layer, LineString(pts).buffer(w / 2.0, cap_style=1))

    def g_box(net, layer, cx, cy, half):
        g_add(net, layer, box(cx - half, cy - half, cx + half, cy + half))

    with s.batch():
        for nm, (chy_mm, w, pads) in nets.items():
            chy = chy_mm * MM
            pts = [PC(rd, pin) for rd, pin in pads]
            xs = [p[0] for p in pts]
            xlo, xhi = min(xs), max(xs)
            s.path(L_H, [(xlo, chy), (xhi, chy)], width=w, cap="round")
            g_line(nm, L_H, [(xlo, chy), (xhi, chy)], w)
            for (px, py) in pts:
                s.path(L_V, [(px, py), (px, chy)], width=w, cap="round")
                g_line(nm, L_V, [(px, py), (px, chy)], w)
                for (vx, vy) in ((px, py), (px, chy)):
                    # L3<->L5 via; its dielectric window lands on L4 (the
                    # dielectric layer) at datatype 0, so it's just a hole.
                    s.drop_via(vx, vy, L_H, L_V, size=VIA, shape="square",
                               dielectric_layer=L_DIE, dielectric_window=VIA)
                    via_pts.append((vx, vy))
                    g_box(nm, L_H, vx, vy, VIA / 2)
                    g_box(nm, L_V, vx, vy, VIA / 2)
            print(f"  net {nm:5s} ch y={chy_mm:+3d} mm  x=[{xlo/1000:.2f},{xhi/1000:.2f}]  {len(pads)} pads")

    # ---- dielectric: draw the fill boundary on L4 (datatype 0), then one
    # command. generate_dielectric() takes the enclosing L4 shape as the
    # boundary and every other datatype-0 L4 shape -- the 68 via windows
    # above and the 8 resistor between-terminals keep-outs (also on L4
    # now) -- as a hole, grows each by the tech clearance, and writes the
    # fractured fill on L4 at datatype 1.
    bx0, by0, bx1, by1 = s.bbox()
    s.rect(L_DIE, bx0 - DIE_MARGIN, by0 - DIE_MARGIN, bx1 + DIE_MARGIN, by1 + DIE_MARGIN)
    written = s.generate_dielectric()
    print(f"\ndielectric L{L_DIE}: {written} fill polygon(s) "
          f"(boundary minus {len(via_pts)} via windows + 8 resistor keep-outs)")

    # also fold each part pad into its net's geometry (on the pad's own
    # layer) so the connectivity check sees the stub reaching the pad.
    pad_by_net = {}
    for nm, (_, _, pads) in nets.items():
        for rd, pin in pads:
            _, _, bb, lay = pad_geom(s, ref[rd], pin)
            g_add(nm, lay, box(*bb))
            pad_by_net[(rd, str(pin))] = nm

    # ---- verification (dielectric model) -------------------------
    names = list(nets)
    per = {}  # (net, layer) -> unioned geom
    for k, polys in geo.items():
        per[k] = unary_union(polys)

    shorts = []
    for L in (L_H, L_V):
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = per.get((names[i], L)), per.get((names[j], L))
                if a is not None and b is not None:
                    inter = a.intersection(b)
                    if not inter.is_empty and inter.area > 2.0:
                        shorts.append((L, names[i], names[j], round(inter.area)))

    opens = []
    for nm in names:
        parts = [per[(nm, L)] for L in (L_H, L_V, L_DIE) if (nm, L) in per]
        # a via bridges L3<->L5: each net's via boxes exist on both, so
        # the union of its trunk (L3), stubs (L5) and vias is one blob.
        blob = unary_union(parts)
        ncomp = len(blob.geoms) if blob.geom_type == "MultiPolygon" else 1
        if ncomp != 1:
            opens.append((nm, ncomp))

    print("\n--- dielectric-model check "
          "(same-layer overlap = short; L3-over-L5 crossings ignored) ---")
    print(f"same-layer shorts : {len(shorts)}")
    for L, a, b, ar in shorts:
        print(f"    L{L}: {a} <-> {b}  ({ar} DBU^2)")
    print(f"disconnected nets : {len(opens)}")
    for nm, n in opens:
        print(f"    {nm}: {n} pieces")

    drc = s.run_drc()
    print(f"\nDRC (tech rules): {len(drc)} violation(s)")
    for v in drc[:30]:
        print("   ", getattr(v.rule, "name", v.rule), "-", v.message)

    s.save_design(TEST_GDS)
    print(f"\nsaved -> {TEST_GDS}")


if __name__ == "__main__":
    main()
