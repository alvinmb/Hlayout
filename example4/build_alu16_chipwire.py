"""Example 4 -- ALU16 MCM as a chip-and-wire hybrid.

Starts from example2/alu16_MCM.gds (LCC packaged parts) and replaces every
LCC20/LCC28 carrier with a BARE DIE (pads on layer 58, epoxy on layer 20,
outline on layer 61) placed at the same spot/rotation.  Each used die pad
gets a WIRE BOND (layer 59) out to a substrate landing; the old routing is
stripped and the substrate is re-routed by Freerouting in chip-and-wire mode
(it lays a bond finger under each landing).

Die pads are numbered by the DIP / die pad number (alu16_netlist.csv
column dip_die_pad), so pin U1.12 is 54AS181 die pad 12 (GND).

ASSUMPTION: TI does not publish die drawings for these parts, so the die
sizes below are representative, not real:
    54AS181  3.0 x 3.0 mm     54S182 2.0 x 2.0 mm     54LSxx 1.5 x 1.5 mm
The pad ORDER around each die follows the DIP pin order (same as inside
the LCC), so the bonds fan out without crossing.

Each die sits on a 2nd-conductor die-attach pad 200 um larger than the
chip all round; the chip capacitors' pads are on the 2nd conductor too;
the bond fingers are on the 2nd conductor (--fingers-1st for the 1st).

Run inside HLayout (Tools > Python console) or with the hlayout package on
the path:  python build_alu16_chipwire.py   [--no-route]
"""
import csv, os, sys
from hlayout.io.sidecar import load_design
from hlayout.io import techfile as T
import hlayout.scripting.api as A

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "example2", "alu16_MCM.gds")
OUT = os.path.join(HERE, "alu16_chipwire.gds")
if "--out" in sys.argv:                      # --out <file.gds>
    OUT = os.path.abspath(sys.argv[sys.argv.index("--out") + 1])
NETCSV = os.path.join(HERE, "alu16_netlist", "alu16_netlist.csv")

PAD_L, EPOXY_L, OUTLINE_L, BOND_L = 58, 20, 61, 59
DIE_PAD = 100          # die bond pad, um square
PAD_INSET = 150        # die edge to pad centre
LAND_K = 0.70          # landing spread (fraction of the LCC pad spread)

# part -> (die cell, die size um, DIP pins, LCC pins)
DIES = {
    "SNJ54AS181BFK": ("DIE_54AS181", 3000, 24, 28),
    "SNJ54S182FK":   ("DIE_54S182",  2000, 16, 20),
    "SNJ54LS27FK":   ("DIE_54LS27",  1500, 14, 20),
    "SNJ54LS30FK":   ("DIE_54LS30",  1500, 14, 20),
    "SNJ54LS86AFK":  ("DIE_54LS86",  1500, 14, 20),
    "SNJ54LS00FK":   ("DIE_54LS00",  1500, 14, 20),
}
LAND_R = {28: 3500, 20: 2600}   # landing ring half-size per carrier
ATTACH_L, ATTACH_MARGIN = 5, 200   # die-attach pad: 2nd conductor, 200 um larger all round
CAP_PAD_L = 5                      # chip-cap pads on the 2nd conductor
# Freerouting rules: power tracks 400 um (no wider than a signal via, so
# a power track never pokes out of one) and 500 um fingers to swallow them,
# laid on the 2nd (top) conductor so the bonds land on exposed copper.
# Pass --fingers-1st to put them on the 1st conductor instead.
ROUTE_RULES = {"power_width": 400, "bond_finger": 500, "passes": 200,
               "finger_layer": "second"}   # fingers on the top (2nd) conductor


def lcc_map(dip_pins):
    """DIP / die pad -> LCC pin (same table as example2/gen_alu16_lcc.py)."""
    if dip_pins == 14:
        return {1:2,2:3,3:4,4:6,5:8,6:9,7:10,8:12,9:13,10:14,11:16,12:18,13:19,14:20}
    if dip_pins == 16:
        return {d: d + 1 + (d - 1)//4 for d in range(1, 17)}
    return {d: d + 1 + (d - 1)//6 for d in range(1, 25)}


def lcc_pad_centres(lib, cell):
    out = {}
    for sh in lib.cells[cell].shapes:
        if sh.layer == 5 and getattr(sh, "pin", None):
            x0, y0, x1, y1 = sh.bbox()
            out[int(sh.pin)] = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    return out


def side_along(x, y):
    """(unit normal, along coordinate) of an LCC pad centre."""
    if abs(y) > abs(x):
        return ((0, 1 if y > 0 else -1), x)
    return ((1 if x > 0 else -1, 0), y)


def place(normal, along, r):
    nx, ny = normal
    return (nx * r + (along if nx == 0 else 0), ny * r + (along if ny == 0 else 0))


def rot(p, deg, o):
    import math
    a = math.radians(deg); c, s = round(math.cos(a), 12), round(math.sin(a), 12)
    return (o[0] + p[0] * c - p[1] * s, o[1] + p[0] * s + p[1] * c)


def build(route=True):
    ld = load_design(SRC)
    lib = ld.library
    s = A.Session(lib)
    s.use_cell("TOP")
    top = lib.cells["TOP"]
    rows = list(csv.DictReader(open(NETCSV, newline="")))
    part_of = {r["ref"]: r["part"] for r in rows}
    used = {(r["ref"], int(r["dip_die_pad"])): r["net"] for r in rows if r["ref"].startswith("U")}

    # --- 1. strip LCCs, vias and all routing / dielectric -----------------
    # (keeping the dielectric's drawn BOUNDARY -- its largest datatype-0
    # shape -- so the fill stays on the substrate, inside the I/O pads)
    d0 = [sh for sh in top.shapes if sh.layer == 4 and getattr(sh, "datatype", 0) == 0]
    keep = max(d0, key=lambda sh: (sh.bbox()[2] - sh.bbox()[0]) * (sh.bbox()[3] - sh.bbox()[1])) \
        if d0 else None
    lccs = [r for r in top.srefs if r.cell_name.startswith("LCC_")]
    top.srefs[:] = [r for r in top.srefs
                    if not r.cell_name.startswith(("LCC_", "VIA_"))]
    top.shapes[:] = [sh for sh in top.shapes
                     if sh is keep or not (sh.layer == 4 or
                             (sh.layer in (3, 5) and not getattr(sh, "pin", None)))]

    lib.tech_params.pop("resistor_list", None)   # example2 carried a stale list

    # chip capacitors: pads move to the 2nd conductor
    for cn, c in lib.cells.items():
        if cn.startswith("CAP_"):
            for sh in c.shapes:
                if sh.layer == 3 and getattr(sh, "pin", None):
                    sh.layer = CAP_PAD_L

    # --- 2. die cells (pad geometry in LCC-cell coordinates) -------------
    made = {}
    for part, (cname, size, ndip, nlcc) in DIES.items():
        if not any(p == part for p in part_of.values()):
            continue
        carrier = "LCC_LCC%d" % nlcc
        centres = lcc_pad_centres(lib, carrier)
        m = lcc_map(ndip)
        amax = max(abs(side_along(*c)[1]) for c in centres.values())
        half = size / 2.0
        k = (half - PAD_INSET - DIE_PAD) / amax
        geo = {}
        s.create_chip_cell(cname, size, size, OUTLINE_L, size - 100, size - 100,
                           EPOXY_L, overwrite=True, attach_pad_layer=ATTACH_L,
                           attach_pad_margin=ATTACH_MARGIN)
        cell = lib.cells[cname]
        for d, lp in m.items():
            n, a = side_along(*centres[lp])
            px, py = place(n, a * k, half - PAD_INSET)
            h = DIE_PAD / 2.0
            r = cell.add_rectangle(PAD_L, px - h, py - h, px + h, py + h)
            r.pin = str(d)
            land = place(n, a * LAND_K, LAND_R[nlcc])
            geo[d] = ((px, py), land)
        made[part] = (cname, geo)
    s.use_cell("TOP")

    # --- 3. place dies where the LCCs were, bond every used pad -----------
    T.save_bond_rules(lib, {"layer": BOND_L, "width": 25, "pad": 75,
                            "min_length": 300, "max_length": 5000,
                            "die_pad_layers": [PAD_L]})
    nbond = 0
    for r in lccs:
        cname, geo = made[part_of[r.label]]
        s.sref(cname, r.origin, rotation=r.rotation, x_reflection=r.x_reflection,
               label=r.label)
        for d, (pad, land) in sorted(geo.items()):
            if (r.label, d) not in used:
                continue          # unconnected die pad: no bond
            s.wire_bond([rot(pad, r.rotation, r.origin), rot(land, r.rotation, r.origin)],
                        cell_name="TOP")
            nbond += 1

    # --- 4. net names keyed by die pad -----------------------------------
    names = {"%s.%s" % k: v for k, v in used.items()}
    old = os.path.join(HERE, "..", "example2", "alu16_net_names.csv")
    for row in csv.DictReader(open(old, newline="")):
        if not row["pad"].startswith("U"):
            names[row["pad"]] = row["net"]
    s.set_net_names(names)
    with open(os.path.join(HERE, "alu16_net_names.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["pad", "net"])
        for k in sorted(names):
            w.writerow([k, names[k]])
    print("dies placed: %d, bonds: %d, named pads: %d" % (len(lccs), nbond, len(names)))

    # --- 5. route the substrate (Freerouting, chip-and-wire) and save ------
    rr = dict(ROUTE_RULES)
    if "--fingers-1st" in sys.argv:
        rr["finger_layer"] = "first"
    s.set_freerouting_rules(**rr)
    if route:
        res = s.freeroute(work_dir=os.path.join(HERE, "freerouting"), cell_name="TOP",
                          timeout=1500, on_line=lambda l: None)
        print("routed: %d/%d nets, unrouted %s, fingers %d"
              % (res["complete"], res["nets"], res["unrouted"], res.get("fingers", 0)))
        for cn in ("LCC_LCC28", "LCC_LCC20"):
            if cn in lib.cells:
                lib.delete_cell(cn)   # carriers no longer used
        s.save_design(OUT)
        print("saved", OUT)
    return s, lib


if __name__ == "__main__":
    build(route="--no-route" not in sys.argv)
