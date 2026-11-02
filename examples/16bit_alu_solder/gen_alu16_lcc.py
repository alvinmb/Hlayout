#!/usr/bin/env python3
"""Generate KiCad 8 schematic + bare-die pad map for a 16-bit 54LS ALU."""
import uuid, csv, os, sys, collections

OUT = sys.argv[1] if len(sys.argv) > 1 else "alu16"
os.makedirs(OUT, exist_ok=True)
G = 2.54
_c = [0]
def U():
    _c[0] += 1
    return f"00000000-0000-4000-8000-{_c[0]:012x}"
ROOT = U()

# ---------------------------------------------------------------- symbols
# pin: (num, name, etype) ; left = inputs side, right = outputs side
SYM = {
 "54LS181": dict(w=7.62, desc="4-bit ALU slice", left=[
    ("2","A0","input"),("23","A1","input"),("21","A2","input"),("19","A3","input"),
    ("1","B0","input"),("22","B1","input"),("20","B2","input"),("18","B3","input"),
    ("6","S0","input"),("5","S1","input"),("4","S2","input"),("3","S3","input"),
    ("8","M","input"),("7","Cn","input"),("24","VCC","power_in"),("12","GND","power_in")],
  right=[("9","F0","output"),("10","F1","output"),("11","F2","output"),("13","F3","output"),
    ("14","A=B","open_collector"),("15","~{P}","output"),("17","~{G}","output"),("16","Cn+4","output")]),
 "54LS182": dict(w=7.62, desc="Carry lookahead generator", left=[
    ("3","~{G0}","input"),("4","~{P0}","input"),("1","~{G1}","input"),("2","~{P1}","input"),
    ("14","~{G2}","input"),("15","~{P2}","input"),("5","~{G3}","input"),("6","~{P3}","input"),
    ("13","Cn","input"),("16","VCC","power_in"),("8","GND","power_in")],
  right=[("12","Cn+x","output"),("11","Cn+y","output"),("9","Cn+z","output"),
    ("10","~{G}","output"),("7","~{P}","output")]),
 "54LS27": dict(w=5.08, desc="Triple 3-input NOR", left=[
    ("1","1A","input"),("2","1B","input"),("13","1C","input"),
    ("3","2A","input"),("4","2B","input"),("5","2C","input"),
    ("9","3A","input"),("10","3B","input"),("11","3C","input"),
    ("14","VCC","power_in"),("7","GND","power_in")],
  right=[("12","1Y","output"),("6","2Y","output"),("8","3Y","output")]),
 "54LS30": dict(w=5.08, desc="8-input NAND", left=[
    ("1","A","input"),("2","B","input"),("3","C","input"),("4","D","input"),
    ("5","E","input"),("6","F","input"),("11","G","input"),("12","H","input"),
    ("14","VCC","power_in"),("7","GND","power_in")],
  right=[("8","Y","output"),("9","NC","no_connect"),("10","NC","no_connect"),("13","NC","no_connect")]),
 "54LS86": dict(w=5.08, desc="Quad 2-input XOR", left=[
    ("1","1A","input"),("2","1B","input"),("4","2A","input"),("5","2B","input"),
    ("9","3A","input"),("10","3B","input"),("12","4A","input"),("13","4B","input"),
    ("14","VCC","power_in"),("7","GND","power_in")],
  right=[("3","1Y","output"),("6","2Y","output"),("8","3Y","output"),("11","4Y","output")]),
 "54LS00": dict(w=5.08, desc="Quad 2-input NAND", left=[
    ("1","1A","input"),("2","1B","input"),("4","2A","input"),("5","2B","input"),
    ("9","3A","input"),("10","3B","input"),("12","4A","input"),("13","4B","input"),
    ("14","VCC","power_in"),("7","GND","power_in")],
  right=[("3","1Y","output"),("6","2Y","output"),("8","3Y","output"),("11","4Y","output")]),
}

IN_PADS = ([f"A{i}" for i in range(16)] + [f"B{i}" for i in range(16)] +
           ["S0","S1","S2","S3","M","CN","SUB","VCC","GND"])
OUT_PADS = [f"F{i}" for i in range(16)] + ["Z","C","N","V","~{GG}","~{PG}","VCC","GND"]

def pads_sym(names, desc):
    pins = []
    for i, n in enumerate(names, 1):
        t = "power_out" if n in ("VCC","GND") else "passive"
        pins.append((str(i), n, t))
    return dict(w=5.08, desc=desc, left=[], right=pins)

SYM["PADS_IN"] = pads_sym(IN_PADS, "Substrate input bond pads")
SYM["PADS_OUT"] = pads_sym(OUT_PADS, "Substrate output bond pads")

# ---- DIP -> LCC (TI FK) terminal mapping
def lcc_map(dip_pins):
    if dip_pins == 14:   # verified vs SN54LS00 FK
        m = {1:2,2:3,3:4,4:6,5:8,6:9,7:10,8:12,9:13,10:14,11:16,12:18,13:19,14:20}; n = 20
    elif dip_pins == 16: # JEDEC 16-in-20 (verify SN54S182 FK vs SDLS206)
        m = {d: d + 1 + (d - 1)//4 for d in range(1, 17)}; n = 20
    elif dip_pins == 24: # matches SN54AS181B FK (SDAS209B)
        m = {d: d + 1 + (d - 1)//6 for d in range(1, 25)}; n = 28
    return m, [str(x) for x in range(1, n + 1) if x not in m.values()]

DIPN = {"54LS181":24,"54LS182":16,"54LS27":14,"54LS30":14,"54LS86":14,"54LS00":14}
PART = {"54LS181":"SNJ54AS181BFK","54LS182":"SNJ54S182FK","54LS27":"SNJ54LS27FK",
        "54LS30":"SNJ54LS30FK","54LS86":"SNJ54LS86AFK","54LS00":"SNJ54LS00FK"}
FP = {14:"ALU16_LCC:LCC-20_FK",16:"ALU16_LCC:LCC-20_FK",24:"ALU16_LCC:LCC-28_FK"}
MAP, LCCNC = {}, {}
for k, dn in DIPN.items():
    m, nc = lcc_map(dn); MAP[k] = m; LCCNC[k] = nc
    SYM[k]["left"]  = [(str(m[int(a)]), b, c) for a, b, c in SYM[k]["left"]]
    SYM[k]["right"] = [(str(m[int(a)]), b, c) for a, b, c in SYM[k]["right"]] + \
                      [(x, "NC", "no_connect") for x in nc]

def pin_xy(sym, num):
    s = SYM[sym]
    for i, p in enumerate(s["left"]):
        if p[0] == num: return (-(s["w"] + G), -i * G, "L")
    for i, p in enumerate(s["right"]):
        if p[0] == num: return (s["w"] + G, -i * G, "R")
    raise KeyError((sym, num))

def f(x): return f"{x:.2f}".rstrip("0").rstrip(".")
EFF = '(effects (font (size 1.27 1.27)))'

def lib_symbol(name):
    s = SYM[name]; n = max(len(s["left"]), len(s["right"]))
    w = s["w"]; bot = -(n - 1) * G - G
    out = [f'(symbol "ALU16:{name}" (pin_names (offset 1.016)) (exclude_from_sim no) (in_bom yes) (on_board yes)',
           f'(property "Reference" "{"J" if name.startswith("PADS") else "U"}" (at 0 {f(G*2)} 0) {EFF})',
           f'(property "Value" "{name}" (at 0 {f(bot - G)} 0) {EFF})',
           f'(property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))',
           f'(property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))',
           f'(property "Description" "{s["desc"]}" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))',
           f'(symbol "{name}_0_1" (rectangle (start {f(-w)} {f(G)}) (end {f(w)} {f(bot)}) '
           f'(stroke (width 0.254) (type default)) (fill (type background))))',
           f'(symbol "{name}_1_1"']
    for side, ang in (("left", 0), ("right", 180)):
        for i, (num, pn, t) in enumerate(s[side]):
            x = -(w + G) if side == "left" else (w + G)
            out.append(f'(pin {t} line (at {f(x)} {f(-i*G)} {ang}) (length 2.54) '
                       f'(name "{pn}" {EFF}) (number "{num}" {EFF}))')
    out.append("))")
    return "\n".join(out)

# ---------------------------------------------------------------- netlist
# inst: ref, sym, (x,y), {pin: net or None(=NC)}
inst = []
for k in range(4):
    b = 4 * k
    cin = ["CN", "C1", "C2", "C3"][k]
    m = {"2":f"A{b}","23":f"A{b+1}","21":f"A{b+2}","19":f"A{b+3}",
         "1":f"B{b}","22":f"B{b+1}","20":f"B{b+2}","18":f"B{b+3}",
         "6":"S0","5":"S1","4":"S2","3":"S3","8":"M","7":cin,"24":"VCC","12":"GND",
         "9":f"F{b}","10":f"F{b+1}","11":f"F{b+2}","13":f"F{b+3}",
         "14":None,"15":f"P{k}N","17":f"G{k}N","16":"CN4" if k == 3 else None}
    inst.append((f"U{k+1}", "54LS181", (40 + 80*k, 30), m))
inst.append(("U5","54LS182",(40,110),{"3":"G0N","4":"P0N","1":"G1N","2":"P1N","14":"G2N","15":"P2N",
    "5":"G3N","6":"P3N","13":"CN","16":"VCC","8":"GND","12":"C1","11":"C2","9":"C3","10":"GG","7":"PG"}))
inst.append(("U6","54LS27",(120,110),{"1":"F0","2":"F1","13":"F2","3":"F3","4":"F4","5":"F5",
    "9":"F6","10":"F7","11":"F8","14":"VCC","7":"GND","12":"N0","6":"N1","8":"N2"}))
inst.append(("U7","54LS27",(190,110),{"1":"F9","2":"F10","13":"F11","3":"F12","4":"F13","5":"F14",
    "9":"F15","10":"GND","11":"GND","14":"VCC","7":"GND","12":"N3","6":"N4","8":"N5"}))
inst.append(("U8","54LS30",(260,110),{"1":"N0","2":"N1","3":"N2","4":"N3","5":"N4","6":"N5",
    "11":"VCC","12":"VCC","14":"VCC","7":"GND","8":"ZN","9":None,"10":None,"13":None}))
inst.append(("U9","54LS86",(120,175),{"1":"B15","2":"SUB","4":"A15","5":"F15","9":"B15X","10":"F15",
    "12":"ZN","13":"VCC","14":"VCC","7":"GND","3":"B15X","6":"VA","8":"VB","11":"Z"}))
inst.append(("U10","54LS00",(190,175),{"1":"VA","2":"VB","4":"VN","5":"VN","9":"CN4","10":"CN4",
    "12":"GND","13":"GND","14":"VCC","7":"GND","3":"VN","6":"V","8":"C","11":None}))
netname = lambda n: {"GG":"~{GG}","PG":"~{PG}"}.get(n, n)
inst.append(("J1","PADS_IN",(330,25),{str(i):n for i,n in enumerate(IN_PADS,1)}))
outmap = {str(i):n for i,n in enumerate(OUT_PADS,1)}
outmap = {k:("GG" if v=="~{GG}" else "PG" if v=="~{PG}" else v) for k,v in outmap.items()}
outmap[str(OUT_PADS.index("N")+1)] = "F15"   # N flag pad = F15
inst.append(("J2","PADS_OUT",(330,145),outmap))

inst2 = []
for ref, sym, xy, m in inst:
    if sym in MAP:
        m = {str(MAP[sym][int(k)]): v for k, v in m.items()}
        m.update({x: None for x in LCCNC[sym]})
    inst2.append((ref, sym, xy, m))
inst = inst2
# sanity: every net on >=2 pins, every symbol pin assigned
nets = collections.defaultdict(list)
for ref, sym, _, m in inst:
    allp = {p[0] for p in SYM[sym]["left"] + SYM[sym]["right"]}
    missing = allp - set(m)
    assert not missing, (ref, missing)
    for p, n in m.items():
        if n: nets[n].append(f"{ref}.{p}")
bad = {n: v for n, v in nets.items() if len(v) < 2}
assert not bad, bad

# ---------------------------------------------------------------- schematic
items, symbols = [], []
for ref, sym, (X, Y), m in inst:
    pins = "\n".join(f'(pin "{p}" (uuid "{U()}"))' for p in m)
    n = max(len(SYM[sym]["left"]), len(SYM[sym]["right"]))
    val = PART.get(sym) or ("Input pads" if sym=="PADS_IN" else "Output pads")
    fpn = FP[DIPN[sym]] if sym in DIPN else ""
    symbols.append(f'''(symbol (lib_id "ALU16:{sym}") (at {f(X)} {f(Y)} 0) (unit 1)
(exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (uuid "{U()}")
(property "Reference" "{ref}" (at {f(X)} {f(Y - 2*G)} 0) {EFF})
(property "Value" "{val}" (at {f(X)} {f(Y + n*G + G)} 0) {EFF})
(property "Footprint" "{fpn}" (at {f(X)} {f(Y)} 0) (effects (font (size 1.27 1.27)) hide))
(property "Datasheet" "" (at {f(X)} {f(Y)} 0) (effects (font (size 1.27 1.27)) hide))
{pins}
(instances (project "alu16" (path "/{ROOT}" (reference "{ref}") (unit 1)))))''')
    for p, net in m.items():
        px, py, side = pin_xy(sym, p)
        x, y = X + px, Y - py
        if net is None:
            items.append(f'(no_connect (at {f(x)} {f(y)}) (uuid "{U()}"))')
        else:
            ang, just = (180, "right") if side == "L" else (0, "left")
            items.append(f'(label "{netname(net)}" (at {f(x)} {f(y)} {ang}) '
                         f'(effects (font (size 1.27 1.27)) (justify {just} bottom)) (uuid "{U()}"))')

NOTE = ("16-bit ALU, 54LS bare-die MCM\\n"
        "Active-high data. ADD: S=1001 M=0 CN=1 SUB=0 | SUB: S=0110 M=0 CN=0 SUB=1\\n"
        "AND: S=1011 M=1 | OR: S=1110 M=1 | XOR: S=0110 M=1 | NOT A: S=0000 M=1\\n"
        "Flags: Z=NOR(F15..F0)  N=F15  C=NOT Cn+4 (1=carry / no borrow)  V=(A15^F15)&(B15^SUB^F15)\\n"
        "Packages: TI FK ceramic LCC. Pin numbers on symbols = LCC terminal numbers.\\n"
        "U1-U4 54AS181B (no 54LS181 in LCC). U5 54S182 (no 54LS182 in LCC). Bare-die pads = DIP numbers, see CSV.")
text = f'(text "{NOTE}" (exclude_from_sim no) (at 20 230 0) (effects (font (size 1.778 1.778)) (justify left bottom)) (uuid "{U()}"))'

sch = f'''(kicad_sch (version 20231120) (generator "eeschema") (generator_version "8.0")
(uuid "{ROOT}")
(paper "A3")
(title_block (title "ALU16 - 16-bit ALU, 54-series LCC (FK)") (date "2026-10-14") (rev "A") (comment 1 "4x 54LS181 + 54LS182 lookahead + flag logic"))
(lib_symbols
{chr(10).join(lib_symbol(s) for s in SYM)}
)
{text}
{chr(10).join(items)}
{chr(10).join(symbols)}
(sheet_instances (path "/" (page "1")))
)
'''
open(f"{OUT}/alu16.kicad_sch", "w").write(sch)

# local symbol library so the parts can be reused
lib = "(kicad_symbol_lib (version 20231120) (generator \"alu16_gen\")\n" + \
      "\n".join(lib_symbol(s).replace('"ALU16:', '"', 1) for s in SYM) + "\n)\n"
open(f"{OUT}/ALU16.kicad_sym", "w").write(lib)
open(f"{OUT}/sym-lib-table", "w").write(
    '(sym_lib_table (version 7)\n (lib (name "ALU16")(type "KiCad")(uri "${KIPRJMOD}/ALU16.kicad_sym")(options "")(descr "ALU16 parts")))\n')
open(f"{OUT}/alu16.kicad_pro", "w").write('{"meta": {"filename": "alu16.kicad_pro", "version": 1}}\n')

# ---------------------------------------------------------------- die pad map
DIE = {  # sym: (die W x H mm, source)
 "54LS181": ("TBD", "Not published by TI - request die drawing from supplier"),
 "54LS182": ("TBD", "Not published by TI - request die drawing from supplier"),
 "54LS27":  ("TBD", "Not published by TI - request die drawing from supplier"),
 "54LS30":  ("TBD", "Not published by TI - request die drawing from supplier"),
 "54LS86":  ("TBD", "Not published by TI - request die drawing from supplier"),
 "54LS00":  ("TBD", "Not published by TI - request die drawing from supplier"),
}
with open(f"{OUT}/alu16_die_pads.csv", "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["ref","part_lcc","lcc_pin","die_pad(=DIP)","signal","net","type","pad_x_um","pad_y_um","die_size_mm","source"])
    for ref, sym, _, m in inst:
        if sym.startswith("PADS"): continue
        inv = {str(v): str(k) for k, v in MAP[sym].items()}
        for num, pn, t in sorted(SYM[sym]["left"] + SYM[sym]["right"], key=lambda p: int(p[0])):
            net = m[num]
            w.writerow([ref, PART[sym], num, inv.get(num, "-"), pn.replace("~{","/").replace("}",""),
                        net if net else "NC", t, "", "", DIE[sym][0], DIE[sym][1]])

print("nets:", len(nets), "| symbols:", len(inst), "| labels:", sum(i.startswith("(label") for i in items),
      "| NC:", sum(i.startswith("(no_connect") for i in items))

# ---------------------------------------------------------------- LCC footprints
# TI FK: 20-pin 8.89 sq, 28-pin 11.43 sq, 1.27 pitch. Pin 1 = centre of top side,
# numbering counter-clockwise viewed from top (JEDEC). Generic land pattern - check vs TI FK drawing.
def lcc_fp(n, body):
    per = n // 4; h = body / 2; pl, pw = 2.0, 0.8; c = h - 0.35   # pads straddle body edge (0.65mm outside)
    pads = []
    # top side: pin1 at x=0, then pins 2.. go left (CCW)
    order = []
    mid = per // 2
    top = [(-(i) * 1.27, -c) for i in range(0, mid + 1)]          # pins 1..mid+1 moving left
    left = [(-c, -(mid - 0.0) * 1.27 + i * 1.27) for i in range(per)]  # top->bottom
    bot = [(-(mid) * 1.27 + i * 1.27, c) for i in range(per)]     # left->right
    right = [(c, (mid) * 1.27 - i * 1.27) for i in range(per)]     # bottom->top
    topr = [((mid - i) * 1.27, -c) for i in range(mid)]            # right->centre
    pts = top + left + bot + right + topr
    assert len(pts) == n, (len(pts), n)
    for i, (x, y) in enumerate(pts, 1):
        vert = abs(y) == c and abs(x) != c
        sx, sy = (pw, pl) if vert else (pl, pw)
        pads.append(f'(pad "{i}" smd rect (at {f(x)} {f(y)}) (size {f(sx)} {f(sy)}) (layers "F.Cu" "F.Paste" "F.Mask"))')
    nm = f"LCC-{n}_FK"
    return f'''(footprint "{nm}" (version 20240108) (generator "alu16_gen") (layer "F.Cu")
(descr "TI FK ceramic LCC, {n} terminals, {body}x{body}mm, 1.27mm pitch. Generic land pattern - verify vs TI FK drawing")
(attr smd)
(property "Reference" "REF**" (at 0 {f(-h-2.5)} 0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))
(property "Value" "{nm}" (at 0 {f(h+2.5)} 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))
(fp_rect (start {f(-h)} {f(-h)}) (end {f(h)} {f(h)}) (stroke (width 0.1) (type solid)) (fill none) (layer "F.Fab"))
(fp_rect (start {f(-h-1.5)} {f(-h-1.5)}) (end {f(h+1.5)} {f(h+1.5)}) (stroke (width 0.05) (type solid)) (fill none) (layer "F.CrtYd"))
(fp_circle (center 0 {f(-h-1.6)}) (end 0.3 {f(-h-1.6)}) (stroke (width 0.15) (type solid)) (fill solid) (layer "F.SilkS"))
{chr(10).join(pads)}
)
'''
os.makedirs(f"{OUT}/ALU16_LCC.pretty", exist_ok=True)
for n, body in ((20, 8.89), (28, 11.43)):
    open(f"{OUT}/ALU16_LCC.pretty/LCC-{n}_FK.kicad_mod", "w").write(lcc_fp(n, body))
open(f"{OUT}/fp-lib-table", "w").write(
    '(fp_lib_table (version 7)\n (lib (name "ALU16_LCC")(type "KiCad")(uri "${KIPRJMOD}/ALU16_LCC.pretty")(options "")(descr "TI FK LCC")))\n')
print("footprints ok")
