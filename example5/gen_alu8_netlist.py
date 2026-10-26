#!/usr/bin/env python3
"""Generate the KiCad netlist (and CSV / text listings) for ALU8, an 8-bit
54-series ALU in TI FK ceramic LCCs -- the 8-bit sibling of example2's ALU16.

  U1-U2  54AS181B  4-bit ALU slices, ripple carry (U1 Cn+4 -> U2 Cn)
  U3     54LS27    triple 3-input NOR  \\
  U4     54LS30    8-input NAND         > zero flag  Z = NOR(F7..F0)
  U5     54LS86    quad XOR            -- overflow terms, Z inverter
  U6     54LS00    quad NAND           -- V, C (= NOT Cn+4)

Active-high data. ADD: S=1001 M=0 CN=1 SUB=0 | SUB: S=0110 M=0 CN=0 SUB=1
Flags: Z=NOR(F7..F0)  N=F7  C=NOT Cn+4 (1=carry / no borrow)
       V=(A7^F7)&(B7^SUB^F7)
Symbol pin numbers are LCC terminal numbers; the die pad / DIP number is
given alongside (dip_die_pad) for the bare-die chip-and-wire build.

Two KiCad netlists are written:
  alu8.net      packaged build: pins = LCC terminal numbers, LCC footprints
  alu8_die.net  bare-die build: pins = die pad (DIP) numbers, footprints =
                the die cells of build_alu8_dies.py (ALU8_DIE:DIE_54AS181 ...)
                -- use this one for Auto Place / netlist checks on
                alu8_dies.gds, whose die pads are numbered by DIP pin.

Usage: python gen_alu8_netlist.py [out_dir]   -> alu8.net, alu8_die.net,
alu8_netlist.csv, alu8_netlist.txt
"""
import collections, csv, datetime, os, sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "alu8_netlist"

# pin: (dip number, name, KiCad pin type)
SYM = {
 "54LS181": ("4-bit ALU slice", [
    ("2","A0","input"),("23","A1","input"),("21","A2","input"),("19","A3","input"),
    ("1","B0","input"),("22","B1","input"),("20","B2","input"),("18","B3","input"),
    ("6","S0","input"),("5","S1","input"),("4","S2","input"),("3","S3","input"),
    ("8","M","input"),("7","Cn","input"),("24","VCC","power_in"),("12","GND","power_in"),
    ("9","F0","output"),("10","F1","output"),("11","F2","output"),("13","F3","output"),
    ("14","A=B","open_collector"),("15","~P","output"),("17","~G","output"),("16","Cn+4","output")]),
 "54LS27": ("Triple 3-input NOR", [
    ("1","1A","input"),("2","1B","input"),("13","1C","input"),
    ("3","2A","input"),("4","2B","input"),("5","2C","input"),
    ("9","3A","input"),("10","3B","input"),("11","3C","input"),
    ("14","VCC","power_in"),("7","GND","power_in"),
    ("12","1Y","output"),("6","2Y","output"),("8","3Y","output")]),
 "54LS30": ("8-input NAND", [
    ("1","A","input"),("2","B","input"),("3","C","input"),("4","D","input"),
    ("5","E","input"),("6","F","input"),("11","G","input"),("12","H","input"),
    ("14","VCC","power_in"),("7","GND","power_in"),("8","Y","output"),
    ("9","NC","no_connect"),("10","NC","no_connect"),("13","NC","no_connect")]),
 "54LS86": ("Quad 2-input XOR", [
    ("1","1A","input"),("2","1B","input"),("4","2A","input"),("5","2B","input"),
    ("9","3A","input"),("10","3B","input"),("12","4A","input"),("13","4B","input"),
    ("14","VCC","power_in"),("7","GND","power_in"),
    ("3","1Y","output"),("6","2Y","output"),("8","3Y","output"),("11","4Y","output")]),
 "54LS00": ("Quad 2-input NAND", [
    ("1","1A","input"),("2","1B","input"),("4","2A","input"),("5","2B","input"),
    ("9","3A","input"),("10","3B","input"),("12","4A","input"),("13","4B","input"),
    ("14","VCC","power_in"),("7","GND","power_in"),
    ("3","1Y","output"),("6","2Y","output"),("8","3Y","output"),("11","4Y","output")]),
}
PART = {"54LS181":"SNJ54AS181BFK","54LS27":"SNJ54LS27FK","54LS30":"SNJ54LS30FK",
        "54LS86":"SNJ54LS86AFK","54LS00":"SNJ54LS00FK"}
DIPN = {"54LS181":24,"54LS27":14,"54LS30":14,"54LS86":14,"54LS00":14}
FP = {14:"ALU16_LCC:LCC-20_FK", 24:"ALU16_LCC:LCC-28_FK"}   # same footprints as ALU16
DIE_FP = {"54LS181":"ALU8_DIE:DIE_54AS181", "54LS27":"ALU8_DIE:DIE_54LS27",
          "54LS30":"ALU8_DIE:DIE_54LS30", "54LS86":"ALU8_DIE:DIE_54LS86",
          "54LS00":"ALU8_DIE:DIE_54LS00"}               # build_alu8_dies.py cells

def lcc_map(dip_pins):
    """DIP pin -> TI FK LCC terminal (same mapping as gen_alu16_lcc.py)."""
    if dip_pins == 14:     # SN54LS00 FK
        m = {1:2,2:3,3:4,4:6,5:8,6:9,7:10,8:12,9:13,10:14,11:16,12:18,13:19,14:20}; n = 20
    elif dip_pins == 24:   # SN54AS181B FK
        m = {d: d + 1 + (d - 1)//6 for d in range(1, 25)}; n = 28
    return m, [x for x in range(1, n + 1) if x not in m.values()]

IN_PADS = ([f"A{i}" for i in range(8)] + [f"B{i}" for i in range(8)] +
           ["S0","S1","S2","S3","M","CN","SUB","VCC","GND"])
OUT_PADS = [f"F{i}" for i in range(8)] + ["Z","C","N","V","VCC","GND"]

# ---------------------------------------------------------------- connections
# ref, symbol, {dip pin: net or None (= no connect)}
inst = []
for k in range(2):
    b = 4 * k
    inst.append((f"U{k+1}", "54LS181", {
        "2":f"A{b}","23":f"A{b+1}","21":f"A{b+2}","19":f"A{b+3}",
        "1":f"B{b}","22":f"B{b+1}","20":f"B{b+2}","18":f"B{b+3}",
        "6":"S0","5":"S1","4":"S2","3":"S3","8":"M","7":["CN","C4"][k],
        "24":"VCC","12":"GND",
        "9":f"F{b}","10":f"F{b+1}","11":f"F{b+2}","13":f"F{b+3}",
        "14":None,"15":None,"17":None,"16":["C4","CN8"][k]}))
inst += [
 ("U3","54LS27",{"1":"F0","2":"F1","13":"F2","3":"F3","4":"F4","5":"F5",
                 "9":"F6","10":"F7","11":"GND","14":"VCC","7":"GND",
                 "12":"N0","6":"N1","8":"N2"}),
 ("U4","54LS30",{"1":"N0","2":"N1","3":"N2","4":"VCC","5":"VCC","6":"VCC",
                 "11":"VCC","12":"VCC","14":"VCC","7":"GND","8":"ZN",
                 "9":None,"10":None,"13":None}),
 ("U5","54LS86",{"1":"B7","2":"SUB","3":"B7X",      # B7 ^ SUB
                 "4":"A7","5":"F7","6":"VA",        # A7 ^ F7
                 "9":"B7X","10":"F7","8":"VB",      # B7 ^ SUB ^ F7
                 "12":"ZN","13":"VCC","11":"Z",     # Z = NOT ZN
                 "14":"VCC","7":"GND"}),
 ("U6","54LS00",{"1":"VA","2":"VB","3":"VN",
                 "4":"VN","5":"VN","6":"V",         # V = VA & VB
                 "9":"CN8","10":"CN8","8":"C",      # C = NOT Cn+4
                 "12":"GND","13":"GND","11":None,
                 "14":"VCC","7":"GND"}),
 ("J1","PADS_IN",{str(i): n for i, n in enumerate(IN_PADS, 1)}),
 ("J2","PADS_OUT",{str(i): ("F7" if n == "N" else n) for i, n in enumerate(OUT_PADS, 1)}),
]

# ---------------------------------------------------------------- build pin table
rows = []          # (net, ref, value, footprint, lcc_pin, dip, pin_name, pin_type)
ncs = collections.OrderedDict()
parts = []
for ref, sym, conn in inst:
    if sym.startswith("PADS"):
        names = IN_PADS if sym == "PADS_IN" else OUT_PADS
        value = "Input pads" if sym == "PADS_IN" else "Output pads"
        desc = "Substrate input bond pads" if sym == "PADS_IN" else "Substrate output bond pads"
        parts.append((ref, value, "", sym, desc))
        for i, n in enumerate(names, 1):
            t = "power_out" if n in ("VCC", "GND") else "passive"
            rows.append((conn[str(i)], ref, value, "", str(i), None, n, t))
        continue
    desc, pins = SYM[sym]
    m, lcc_nc = lcc_map(DIPN[sym])
    parts.append((ref, PART[sym], FP[DIPN[sym]], sym, desc))
    names = {p[0] for p in pins}
    assert names == set(conn), (ref, names ^ set(conn))
    nc = list(lcc_nc)
    for dip, name, t in pins:
        net = conn[dip]
        if net is None:
            nc.append(m[int(dip)])
        else:
            rows.append((net, ref, PART[sym], FP[DIPN[sym]], str(m[int(dip)]), dip, name, t))
    ncs[ref] = sorted(nc)

nets = collections.OrderedDict()
def net_key(n):
    return (0, ["GND", "VCC"].index(n)) if n in ("GND", "VCC") else (1, n.upper())
for r in sorted(rows, key=lambda r: net_key(r[0])):
    nets.setdefault(r[0], []).append(r)
ref_key = lambda r: (r[1][0], int(r[1][1:]), int(r[4]))
for n in nets:
    nets[n].sort(key=ref_key)
bad = {n: len(v) for n, v in nets.items() if len(v) < 2}
assert not bad, f"single-pin nets: {bad}"

# ---------------------------------------------------------------- write
os.makedirs(OUT, exist_ok=True)
q = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
today = datetime.date.today().isoformat()
def kicad_netlist(die):
    """die=False: LCC terminal numbers / LCC footprints; die=True: die pad
    (DIP) numbers / die-cell footprints."""
    lines = ['(export (version "E")',
             f' (design (source "alu8.kicad_sch") (tool "gen_alu8_netlist.py") (date {q(today)}))',
             ' (components']
    for ref, value, fp, sym, desc in parts:
        if die and sym in DIE_FP:
            fp = DIE_FP[sym]
        lines.append(f'  (comp (ref {q(ref)}) (value {q(value)}) (footprint {q(fp)}) '
                     f'(libsource (lib "ALU16") (part {q(sym)}) (description {q(desc)})))')
    lines += [' )', ' (nets']
    for code, (n, rs) in enumerate(nets.items(), 1):
        lines.append(f'  (net (code {q(str(code))}) (name {q(n)})')
        for net, ref, value, fp, pin, dip, name, t in sorted(
                rs, key=lambda r: (r[1][0], int(r[1][1:]), int(r[5] if die and r[5] else r[4]))):
            pn = dip if die and dip else pin
            lines.append(f'   (node (ref {q(ref)}) (pin {q(pn)}) (pinfunction {q(name)}) (pintype {q(t)}))')
        lines.append('  )')
    lines += [' )', ')']
    return "\n".join(lines) + "\n"

open(os.path.join(OUT, "alu8.net"), "w", newline="\n").write(kicad_netlist(False))
open(os.path.join(OUT, "alu8_die.net"), "w", newline="\n").write(kicad_netlist(True))

with open(os.path.join(OUT, "alu8_netlist.csv"), "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["net","ref","part","lcc_pin","dip_die_pad","pin_name","pin_type"])
    for n, rs in nets.items():
        for net, ref, value, fp, pin, dip, name, t in rs:
            if dip is None:      # substrate pad: pad number in the die-pad column, as ALU16
                w.writerow([net, ref, value, "", pin, name, t])
            else:
                w.writerow([net, ref, value, pin, dip, name, t])

txt = [f"ALU8 netlist  (pin numbers = LCC FK terminals; die pad = DIP number in [])",
       f"{len(nets)} nets, {len(parts)} parts", "", "PARTS"]
txt += [f"  {ref:<4} {value:<16} {fp}".rstrip() for ref, value, fp, _, _ in parts]
txt += ["", "NETS"]
for n, rs in nets.items():
    nodes = " ".join(f"{r[1]}.{r[4]}" + (f"[{r[5]}]" if r[5] else "") + f"({r[6]})" for r in rs)
    txt.append(f"  {n:<6} ({len(rs):>2}): {nodes}")
txt += ["", "NO-CONNECTS"] + [f"  {ref:<4} " + " ".join(map(str, p)) for ref, p in ncs.items()]
txt += ["", "OPERATION",
        "  Active-high data. ADD: S=1001 M=0 CN=1 SUB=0 | SUB: S=0110 M=0 CN=0 SUB=1",
        "  AND: S=1011 M=1 | OR: S=1110 M=1 | XOR: S=0110 M=1 | NOT A: S=0000 M=1",
        "  Flags: Z=NOR(F7..F0)  N=F7  C=NOT Cn+4 (1=carry / no borrow)  V=(A7^F7)&(B7^SUB^F7)",
        "  Carry ripples U1 Cn+4 -> U2 Cn (net C4); no 54S182 is needed for two slices."]
open(os.path.join(OUT, "alu8_netlist.txt"), "w", newline="\n").write("\n".join(txt) + "\n")
print(f"{len(nets)} nets, {len(parts)} parts, {len(rows)} pins -> {OUT}")
