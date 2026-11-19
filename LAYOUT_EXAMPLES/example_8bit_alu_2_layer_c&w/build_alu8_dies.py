# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Example 5 -- bare-die components for the ALU8 netlist.

Builds one die cell per part in alu8_netlist/alu8.net (everything except the
J1 / J2 substrate pads):

    DIE_54AS181  3.0 x 3.0 mm, 24 pads   (U1, U2)
    DIE_54LS27   1.5 x 1.5 mm, 14 pads   (U3)
    DIE_54LS30   1.5 x 1.5 mm, 14 pads   (U4)
    DIE_54LS86   1.5 x 1.5 mm, 14 pads   (U5)
    DIE_54LS00   1.5 x 1.5 mm, 14 pads   (U6)

Each die, the same way as example4's ALU16 dies:
  * body outline on layer 61, epoxy die attach on layer 20 (body - 100 um),
    a 2nd-conductor (layer 5) die-attach pad 200 um larger all round;
  * 100 um square bond pads on layer 58, 150 um in from the die edge, each
    tagged pin = die pad number (= DIP pin, the dip_die_pad column of
    alu8_netlist.csv), so U1.12 is 54AS181 die pad 12 (GND);
  * the end pads of adjacent sides keep a 50 um gap at each corner
    (example4's ALU16 dies have them touching corner to corner);
  * pads go round the die in the same order as the TI FK LCC terminals
    (DIP pin 1 near the top centre, counter-clockwise), so bonds fan out
    without crossing.

TOP holds a 30 x 30 mm substrate outline (layer 57, set as the route
boundary), the module I/O pads (layer 3: J1 inputs across the top and down
the left, J2 outputs up the right; pad n is net-named IO.n, listed in
alu8_io_pads.csv) and U1-U6 labelled with their reference designators. The
die-pad and I/O-pad net names come from the netlist (alu8_net_names.csv). For Auto Place use alu8_netlist/alu8_die.net: its pin
numbers are die pad numbers, like these cells (alu8.net uses LCC terminal
numbers).

ASSUMPTION: TI does not publish die drawings for these parts; the die sizes
are representative, not real -- swap in the supplier's die drawing values
(DIES below) when you have them.

Run with the hlayout package on the path:  python build_alu8_dies.py
"""
import csv, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import hlayout.scripting.api as A
from hlayout.io import techfile as T

OUT = os.path.join(HERE, "alu8_dies.gds")
NETCSV = os.path.join(HERE, "alu8_netlist", "alu8_netlist.csv")

PAD_L, EPOXY_L, OUTLINE_L, ATTACH_L = 58, 20, 61, 5
DIE_PAD = 100          # bond pad, um square
PAD_INSET = 150        # die edge to pad centre
PAD_GAP = 50           # min gap between the two corner pads (example4's dies touch)
EPOXY_INSET = 100      # epoxy is this much smaller than the die (total)
ATTACH_MARGIN = 200    # die-attach pad, larger than the die all round

# part -> (die cell, die size um, DIP pins, LCC terminals)
DIES = {
    "SNJ54AS181BFK": ("DIE_54AS181", 3000, 24, 28),
    "SNJ54LS27FK":   ("DIE_54LS27",  1500, 14, 20),
    "SNJ54LS30FK":   ("DIE_54LS30",  1500, 14, 20),
    "SNJ54LS86AFK":  ("DIE_54LS86",  1500, 14, 20),
    "SNJ54LS00FK":   ("DIE_54LS00",  1500, 14, 20),
}
SUB_L, IO_L = 57, 3     # substrate outline layer, I/O pad layer (1st conductor)
SUB_SIZE = 30000        # substrate, um square (30 x 30 mm)
IO_PAD, IO_PITCH, IO_EDGE = 1200, 1800, 600   # I/O pad size / pitch / inset from edge

# where U1-U6 sit in TOP (um); a starting point -- Auto Place moves them
PLACE = {"U1": (-2600, 2600), "U2": (2600, 2600),
         "U3": (-4200, -2400), "U4": (-1400, -2400), "U5": (1400, -2400), "U6": (4200, -2400)}


def lcc_map(dip_pins):
    """DIP / die pad -> LCC terminal (same table as gen_alu8_netlist.py)."""
    if dip_pins == 14:
        return {1:2,2:3,3:4,4:6,5:8,6:9,7:10,8:12,9:13,10:14,11:16,12:18,13:19,14:20}
    return {d: d + 1 + (d - 1)//6 for d in range(1, 25)}


def lcc_terminals(n):
    """LCC terminal -> (normal, along) on a unit-pitch grid: terminal 1 at
    the top centre, counter-clockwise viewed from the top (JEDEC). y up."""
    per = n // 4; mid = per // 2
    pts = ([((0, 1), -i) for i in range(mid + 1)] +            # top, centre -> left
           [((-1, 0), mid - i) for i in range(per)] +          # left, top -> bottom
           [((0, -1), -mid + i) for i in range(per)] +         # bottom, left -> right
           [((1, 0), -mid + i) for i in range(per)] +          # right, bottom -> top
           [((0, 1), mid - i) for i in range(mid)])            # top, right -> centre
    assert len(pts) == n
    return {i: p for i, p in enumerate(pts, 1)}


def pad_centres(size, ndip, nlcc):
    term = lcc_terminals(nlcc)
    m = lcc_map(ndip)
    amax = max(abs(term[t][1]) for t in m.values())
    half = size / 2.0
    r = half - PAD_INSET                             # centre -> pad row
    # along-edge pitch: the last pad on a side stops PAD_GAP short of the
    # first pad on the next side round the corner
    k = (r - DIE_PAD - PAD_GAP) / amax
    out = {}
    for d, t in m.items():
        (nx, ny), a = term[t]
        out[d] = (nx * r + (a * k if nx == 0 else 0), ny * r + (a * k if ny == 0 else 0))
    return out


def build():
    rows = list(csv.DictReader(open(NETCSV, newline="")))
    part_of = {r["ref"]: r["part"] for r in rows if r["ref"].startswith("U")}
    signal = {(r["ref"], int(r["dip_die_pad"])): (r["net"], r["pin_name"])
              for r in rows if r["ref"].startswith("U")}

    s = A.Session.new("ALU8_DIES")
    for n, name in ((5, "2nd Routing Conductor"), (20, "Epoxy"), (58, "Chip_Pads"),
                    (59, "Chip_wire_Bond"), (61, "Document 1")):
        s.set_layer_style(n, name=name)

    # Wire Bond Rules: die pads on layer 58 -- this is also what tells Auto
    # Place / DRC that these cells are dies (bond reach round them)
    T.save_bond_rules(s.library, {"layer": 59, "width": 25, "pad": 75,
                                  "min_length": 300, "max_length": 5000,
                                  "die_pad_layers": [PAD_L]})
    pads_csv = []
    for part, (cname, size, ndip, nlcc) in DIES.items():
        s.create_chip_cell(cname, size, size, OUTLINE_L, size - EPOXY_INSET, size - EPOXY_INSET,
                           EPOXY_L, overwrite=True, attach_pad_layer=ATTACH_L,
                           attach_pad_margin=ATTACH_MARGIN)
        cell = s.library.cells[cname]
        h = DIE_PAD / 2.0
        for d, (x, y) in sorted(pad_centres(size, ndip, nlcc).items()):
            r = cell.add_rectangle(PAD_L, x - h, y - h, x + h, y + h)
            r.pin = str(d)
            refs = [u for u, p in sorted(part_of.items()) if p == part]
            for u in refs:
                net, fn = signal.get((u, d), ("NC", "NC"))
                pads_csv.append([cname, u, d, fn, net, round(x), round(y)])

    s.new_cell("TOP")
    # substrate outline + route layers, so Auto Place / Freerouting know
    # the board area (Auto Place stops with "no substrate outline or I/O
    # pads to place inside" without them)
    s.set_layer_style(SUB_L, name="Substrate")
    h = SUB_SIZE / 2.0
    s.rect(SUB_L, -h, -h, h, h)
    rr = T.load_route_rules(s.library)
    rr.update(layer_a=3, layer_b=5, boundary_layer=SUB_L)
    T.save_route_rules(s.library, rr)

    # I/O pad ring: J1 (inputs) across the top and down the left side,
    # J2 (outputs) up the right side. Pad pin n is net-named "IO.n", which
    # is what Auto Place pulls the dies towards.
    j = {r["ref"]: {} for r in rows if r["ref"] in ("J1", "J2")}
    for r in rows:
        if r["ref"] in j:
            j[r["ref"]][int(r["dip_die_pad"])] = (r["net"], r["pin_name"])
    io_names, io_rows = {}, []
    c = h - IO_EDGE - IO_PAD / 2.0                      # pad-row centre line
    def row(n, side):
        span = (n - 1) * IO_PITCH
        for i in range(n):
            a = -span / 2.0 + i * IO_PITCH
            yield {"top": (a, c), "left": (-c, -a), "right": (c, a)}[side]
    j1 = sorted(j["J1"]); j2 = sorted(j["J2"])
    slots = list(row(13, "top")) + list(row(len(j1) - 13, "left")) + list(row(len(j2), "right"))
    pads = [("J1", p) for p in j1] + [("J2", p) for p in j2]
    q = IO_PAD / 2.0
    for n, ((ref, pin), (x, y)) in enumerate(zip(pads, slots), 1):
        s.rect(IO_L, x - q, y - q, x + q, y + q, pin=str(n))
        net, fn = j[ref][pin]
        io_names[f"IO.{n}"] = net
        io_rows.append([f"IO.{n}", ref, pin, fn, net, round(x), round(y)])

    for u, (x, y) in PLACE.items():
        s.sref(DIES[part_of[u]][0], (x, y), label=u)
    names = {f"{u}.{d}": net for (u, d), (net, _) in signal.items()}
    names.update(io_names)
    s.set_net_names(names)
    s.clear_undo_history()
    s.save_design(OUT)

    with open(os.path.join(HERE, "alu8_net_names.csv"), "w", newline="") as f:
        w = csv.writer(f); w.writerow(["pad", "net"])
        for k in sorted(names, key=lambda k: (k[:2] != "IO", k.split(".")[0], int(k.split(".")[1]))):
            w.writerow([k, names[k]])
    with open(os.path.join(HERE, "alu8_die_pads.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["die_cell", "ref", "die_pad", "signal", "net", "pad_x_um", "pad_y_um"])
        w.writerows(sorted(pads_csv, key=lambda r: (int(r[1][1:]), r[2])))
    with open(os.path.join(HERE, "alu8_io_pads.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["io_pad", "ref", "pin", "signal", "net", "x_um", "y_um"])
        w.writerows(io_rows)
    print(f"{len(DIES)} die cells, {len(PLACE)} placed, {len(names)} named pads -> {OUT}")


if __name__ == "__main__":
    build()
