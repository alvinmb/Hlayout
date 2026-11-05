# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""ALU16 placement plan -- run on the design open in HLayout.

Tools > Run Script File... (or exec() in the Script Console), with the
ALU16 parts U1..U10 already placed in the current cell as LCC_LCC28 /
LCC_LCC20 instances labelled by ref-des.

Reads example2/alu16_netlist/alu16_netlist.csv, takes each package's pin
positions from its own pin-number text, and moves/rotates U1..U10 onto a
slot grid so the total signal wirelength (half-perimeter, VCC/GND left
out as planes) is as small as possible. The substrate bond pads are
modelled as fixed strips: J1 (inputs, pads 1..41) along the top edge,
J2 (outputs, pads 1..24) along the bottom edge, in pad order left to
right. One undo step. Re-running starts from scratch (same seed).
"""
import csv
import math
import os
import random
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() \
    else r"C:\dev\HLayout\example2"
NETLIST = os.path.join(HERE, "alu16_netlist", "alu16_netlist.csv")

PITCH_X, PITCH_Y = 20000, 20000        # slot pitch, DBU (um)
COLS, ROWS = 5, 3                      # 15 slots for 10 parts
PAD_MARGIN = 12000                     # J1/J2 strips this far outside the slot grid
POWER = {"VCC", "GND"}
SEED, STEPS = 16, 20000


def session():
    g = globals()
    if "s" in g:
        return g["s"]
    raise SystemExit("run this from HLayout (Tools > Run Script File... or the Script Console)")


def read_netlist(path):
    nets = defaultdict(list)            # net -> [(ref, pin)]
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ref = row["ref"]
            pin = row["lcc_pin"] if ref.startswith("U") else row["dip_die_pad"]
            nets[row["net"]].append((ref, str(pin)))
    return nets


def pin_offsets(lib, cell_name):
    """{pin: (x, y)} from the package cell's own pin-number Text shapes."""
    out = {}
    for sh in lib.cells[cell_name].shapes:
        t = getattr(sh, "text", None)
        if t and t.isdigit():
            out[t] = tuple(float(v) for v in sh.origin)
    return out


def rot(p, deg):
    x, y = p
    a = math.radians(deg)
    return (x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a))


def main():
    s = session()
    lib = s.library
    cell = s.cell
    refs = {r.label: r for r in cell.srefs if r.label.startswith("U")}
    parts = sorted(refs, key=lambda r: int(r[1:]))
    nets = read_netlist(NETLIST)
    sig = {n: m for n, m in nets.items() if n not in POWER and len(m) > 1}
    pins = {c: pin_offsets(lib, c) for c in {refs[p].cell_name for p in parts}}

    # slot grid centred where the parts are now
    xs = [refs[p].origin[0] for p in parts]
    ys = [refs[p].origin[1] for p in parts]
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    slots = [(cx + (i - (COLS - 1) / 2.0) * PITCH_X, cy + ((ROWS - 1) / 2.0 - j) * PITCH_Y)
             for j in range(ROWS) for i in range(COLS)]
    left = cx - (COLS / 2.0) * PITCH_X
    width = COLS * PITCH_X
    top_y = cy + (ROWS / 2.0) * PITCH_Y + PAD_MARGIN
    bot_y = cy - (ROWS / 2.0) * PITCH_Y - PAD_MARGIN
    pads = {"J1": (41, top_y), "J2": (24, bot_y)}

    def pad_xy(ref, pin):
        n, y = pads[ref]
        return (left + (int(pin) - 0.5) / n * width, y)

    def pin_xy(state, ref, pin):
        if ref in pads:
            return pad_xy(ref, pin)
        slot, deg = state[ref]
        ox, oy = slots[slot]
        dx, dy = rot(pins[refs[ref].cell_name][pin], deg)
        return (ox + dx, oy + dy)

    def cost(state):
        total = 0.0
        for members in sig.values():
            pts = [pin_xy(state, r, p) for r, p in members]
            xs_ = [q[0] for q in pts]
            ys_ = [q[1] for q in pts]
            total += (max(xs_) - min(xs_)) + (max(ys_) - min(ys_))
        return total

    # starting point: the current placement mapped to its nearest free slot
    state, used = {}, set()
    for p in parts:
        ox, oy = refs[p].origin
        best = min((k for k in range(len(slots)) if k not in used),
                   key=lambda k: (slots[k][0] - ox) ** 2 + (slots[k][1] - oy) ** 2)
        used.add(best)
        state[p] = (best, int(round(refs[p].rotation)) % 360)

    def current_cost():
        # the layout exactly as it is now (not snapped to slots)
        total = 0.0
        for members in sig.values():
            pts = []
            for r, p in members:
                if r in pads:
                    pts.append(pad_xy(r, p))
                else:
                    ref = refs[r]
                    dx, dy = rot(pins[ref.cell_name][p], ref.rotation)
                    pts.append((ref.origin[0] + dx, ref.origin[1] + dy))
            total += (max(q[0] for q in pts) - min(q[0] for q in pts)) + \
                     (max(q[1] for q in pts) - min(q[1] for q in pts))
        return total

    before = current_cost()
    rng = random.Random(SEED)
    cur = dict(state)
    c_cur = cost(cur)
    best, c_best = dict(cur), c_cur
    t0 = c_cur * 0.05
    for step in range(STEPS):
        t = t0 * (1 - step / STEPS) + 1e-6
        new = dict(cur)
        p = rng.choice(parts)
        move = rng.random()
        if move < 0.25:
            new[p] = (new[p][0], (new[p][1] + rng.choice((90, 180, 270))) % 360)
        else:
            k = rng.randrange(len(slots))
            other = next((q for q in parts if new[q][0] == k), None)
            if other is not None:
                new[other] = (new[p][0], new[other][1])
            new[p] = (k, new[p][1])
        c_new = cost(new)
        if c_new < c_cur or rng.random() < math.exp((c_cur - c_new) / t):
            cur, c_cur = new, c_new
            if c_cur < c_best:
                best, c_best = dict(cur), c_cur

    s.checkpoint()
    for p in parts:
        k, deg = best[p]
        refs[p].origin = (int(round(slots[k][0])), int(round(slots[k][1])))
        refs[p].rotation = float(deg)
    s._notify()

    print(f"ALU16 placement: signal wirelength {before / 1000:.1f} mm -> {c_best / 1000:.1f} mm "
          f"({100 * (before - c_best) / before:.0f}% shorter)")
    print("J1 inputs modelled along the top edge, J2 outputs along the bottom; VCC/GND excluded.")
    grid = [["  .  "] * COLS for _ in range(ROWS)]
    for p in parts:
        k, deg = best[p]
        grid[k // COLS][k % COLS] = f"{p:>3}{'' if deg == 0 else '@' + str(deg)}".ljust(5)
    print("            " + "J1 (inputs)".center(COLS * 9))
    for row in grid:
        print("            " + "  ".join(c.center(7) for c in row))
    print("            " + "J2 (outputs)".center(COLS * 9))
    return {"before_mm": before / 1000, "after_mm": c_best / 1000, "placement": best,
            "slots": slots}


result = main()
