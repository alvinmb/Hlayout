# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Apply the ALU16 module plan (alu16_placement.csv + alu16_module_pinout.csv)
to the design open in HLayout.

1. Renumbers the LCC_LCC20 / LCC_LCC28 cells' pins to TI FK / JEDEC order
   (pin 1 at the centre of the top edge, counter-clockwise viewed from the
   top) -- matching ALU16_LCC.pretty -- both the pin-number text and the
   pin attribute of every terminal pad.
2. Places U1..U10: KiCad mm (+y down, origin = substrate centre) ->
   HLayout DBU (+y up): x*1000, -y*1000; rotation unchanged (CCW).
3. Draws the 74 x 74 mm substrate (grown from the plan's 50 mm -- see SCALE), places the 0805 decoupling caps, the 2 mm edge keep-out and the 68
   module pins (17 per side), numbered and labelled with their signals.
One undo step.
"""
import csv, os

DOWN = os.path.expanduser(r"~\Downloads")
PLACEMENT = os.path.join(DOWN, "alu16_placement.csv")
PINOUT = os.path.join(DOWN, "alu16_module_pinout.csv")

SUB_MM, KEEPOUT_MM, PER_SIDE = 74.0, 2.0, 17
# The plan was made for the KiCad ALU16_LCC footprints (pads reach 6.37 /
# 5.10 mm from centre); HLayout's LCC_LCC28 / LCC_LCC20 pads reach 8.095 /
# 6.825 mm. Positions are spread about the substrate centre by SCALE -- the
# smallest factor giving every pair of packages >= 1 mm between pad extents
# -- and the substrate grown to keep them inside the 2 mm edge keep-out.
SCALE = 1.505            # also leaves room for each IC's 0805 decoupling cap (below)

# 0805 decoupling cap per IC (cell CAP_0805, pads on layer 1, reached on layer 5):
# placed in the IC's own frame just outside the pad ring level with the VCC
# pin (LCC28 pin 28 / LCC20 pin 20 at x=+1.27 mm on the top edge), pad 1
# next to the pin; it rotates with its IC. C<n> decouples U<n>.
CAP_CELL = "CAP_0805"
CAP_X = 1920
CAP_Y = {"LCC_LCC28": 8095 + 500 + 800, "LCC_LCC20": 6825 + 500 + 800}
L_SUBSTRATE, L_DOC, L_PIN, L_PAD = 57, 61, 2, 1       # layers
PIN_W, PIN_D = 1.6, 1.6                                 # module pin pad, mm
PIN_INSET = 0.2          # pin pad's outer edge this far in from the substrate edge
PIN_GAP = 0.5            # min gap between adjacent module pins (incl. round the corners)
# first/last pin centre on each side this far from the corner, so the end
# pins of neighbouring sides keep PIN_GAP between them
CORNER = PIN_INSET + PIN_D + PIN_GAP + PIN_W / 2.0
LABEL_H = 500            # pin label text height, DBU; labels go OUTSIDE the substrate


def renumber(lib, name):
    from hlayout.core.shapes import Text, Rectangle
    cell = lib.cells[name]
    texts = [sh for sh in cell.shapes if isinstance(sh, Text) and sh.text.isdigit()]
    per_side = len(texts) // 4
    pts = [(t, t.origin) for t in texts]
    # walk the perimeter CCW (y up) starting at top centre: sort by angle
    import math
    def ang(p):          # 0 at +y (top centre), increasing CCW
        a = math.atan2(-p[0], p[1])
        return a % (2 * math.pi)
    order = sorted(pts, key=lambda tp: ang(tp[1]))
    mapping = {}
    for i, (t, _) in enumerate(order):
        mapping[t.text] = str(i + 1)
    for t in texts:
        t.text = mapping[t.text]
    # pin attribute on the terminal pads (layer L_PAD): nearest text
    pads = [sh for sh in cell.shapes if isinstance(sh, Rectangle) and sh.layer == L_PAD]
    for pad in pads:
        x0, y0, x1, y1 = pad.bbox()
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        t = min(texts, key=lambda t: (t.origin[0] - cx) ** 2 + (t.origin[1] - cy) ** 2)
        pad.pin = t.text
    return mapping


def module_pin_xy(n):
    """Centre (mm, y up) of module pin n, 17 per side, in the plan's order,
    spread evenly between CORNER and side - CORNER so the corners stay clear."""
    half = SUB_MM / 2.0
    pitch = (SUB_MM - 2 * CORNER) / (PER_SIDE - 1)
    inset = PIN_INSET + PIN_D / 2.0
    k = (n - 1) % PER_SIDE
    off = -half + CORNER + k * pitch
    side = (n - 1) // PER_SIDE
    if side == 0:   return (off, half - inset, "top")           # top, L->R
    if side == 1:   return (half - inset, -off, "right")        # right, T->B
    if side == 2:   return (-off, -half + inset, "bottom")      # bottom, R->L
    return (-half + inset, off, "left")                         # left, B->T


def main(s):
    lib = s.library
    um = lambda v: int(round(v * 1000))
    with s.batch():
        maps = {c: renumber(lib, c) for c in ("LCC_LCC28", "LCC_LCC20")}
        refs = {r.label: r for r in s.cell.srefs if r.label}
        with open(PLACEMENT, newline="") as f:
            rows = list(csv.DictReader(f))
        # redraw: clear any previous module outline / pins / labels, routing
        # (layers 3/4/5) and decoupling caps
        cell = s.cell
        cell.shapes[:] = [sh for sh in cell.shapes
                          if sh.layer not in (L_SUBSTRATE, L_DOC, L_PIN, 3, 4, 5)]
        cell.srefs[:] = [r for r in cell.srefs if r.cell_name != CAP_CELL]
        for row in rows:
            r = refs[row["ref"]]
            r.origin = (um(float(row["x_mm"]) * SCALE), -um(float(row["y_mm"]) * SCALE))
            r.rotation = float(row["kicad_rot_deg"]) % 360
            if CAP_CELL in lib.cells:
                import math
                a = math.radians(r.rotation)
                cx, cy = CAP_X, CAP_Y[r.cell_name]
                ox = r.origin[0] + cx * math.cos(a) - cy * math.sin(a)
                oy = r.origin[1] + cx * math.sin(a) + cy * math.cos(a)
                cell.add_sref(CAP_CELL, (int(round(ox)), int(round(oy))), rotation=r.rotation,
                              label="C" + row["ref"][1:])
        h = SUB_MM / 2.0
        s.rect(L_SUBSTRATE, um(-h), um(-h), um(h), um(h))
        k = h - KEEPOUT_MM
        s.path(L_DOC, [(um(-k), um(-k)), (um(k), um(-k)), (um(k), um(k)),
                       (um(-k), um(k)), (um(-k), um(-k))], width=100)
        with open(PINOUT, newline="") as f:
            pins = list(csv.DictReader(f))
        for p in pins:
            n = int(p["module_pin"])
            x, y, side = module_pin_xy(n)
            w, d = (PIN_W, PIN_D) if side in ("top", "bottom") else (PIN_D, PIN_W)
            s.rect(L_PIN, um(x - w / 2), um(y - d / 2), um(x + w / 2), um(y + d / 2), pin=str(n))
            label = f"{n} {p['signal']}"
            th, half = LABEL_H, SUB_MM / 2.0
            w_lab = len(label) * th * 0.6
            out = 600                                   # gap outside the substrate edge
            if side == "top":      pos = (um(x) - w_lab / 2, um(half) + out)
            elif side == "bottom": pos = (um(x) - w_lab / 2, -um(half) - out - th)
            elif side == "left":   pos = (-um(half) - out - w_lab, um(y) - th / 2)
            else:                  pos = (um(half) + out, um(y) - th / 2)
            s.text(L_DOC, (int(pos[0]), int(pos[1])), label, height=th)
    s._notify()
    return maps, rows, pins


if "s" in globals():
    MAPS, ROWS, PINS = main(s)
    for c, m in MAPS.items():
        print(c, "old->new:", " ".join(f"{k}->{v}" for k, v in sorted(m.items(), key=lambda kv: int(kv[0]))))
    print(f"placed {len(ROWS)} parts, {len(PINS)} module pins")
