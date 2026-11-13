# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Back-annotate the header pinout to a KiCad schematic.

IOPINS (and Tools > Header Pinout) move I/O nets between header pins on the
board: the module's pin N now carries a different net. This writes that
pinout back into the KiCad schematic (.kicad_sch) so the schematic, its
netlist and the PCB agree with the layout.

How a header pin gets its net in the schematic: a wire (or a short chain of
wires) from the pin's end to a label -- global, local or hierarchical. For
every pin of the header symbol (``ref``, e.g. J30):

  * the net it should carry is the same as now: nothing changes;
  * it carries a net and should carry another: the label(s) on its wire are
    renamed in place;
  * it is unconnected and should carry a net: a wire is drawn from the pin
    end straight out (the way the pin points) by the same length the other
    pins' wires use, with a copy of a neighbouring label at its end;
  * it carries a net and should be free: its wire(s) and label(s) are
    deleted (with ``no_connect`` a no-connect flag is put on the pin).

A pin whose wiring also reaches something else (a junction, another wire
branch, another symbol's pin) is never edited: it is reported as "shared"
and left for the user. Everything the edit doesn't touch stays byte for byte
as it was; the original is copied to <file>.bak first. Only the top-level
sheet holding the header symbol is edited.

Pin numbers are the header's own: HLayout header pin N = the symbol's pin N.
"""

import os
import re
import shutil
import uuid as _uuid
from typing import Dict, List, Optional, Tuple

_NUM = r"(-?\d+(?:\.\d+)?)"


# --- S-expression spans ------------------------------------------------------------

def _block_end(s: str, i: int) -> int:
    """Index just past the S-expression that opens at s[i] == '('."""
    depth, j, n = 0, i, len(s)
    while j < n:
        ch = s[j]
        if ch == '"':
            j += 1
            while j < n and s[j] != '"':
                j += 2 if s[j] == "\\" else 1
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise ValueError("unbalanced S-expression")


def _top_blocks(s: str, head: str) -> List[Tuple[int, int]]:
    """Spans of the schematic's top-level ``(head ...`` blocks (children of
    the root kicad_sch form, one level in)."""
    out = []
    root = s.find("(kicad_sch")
    if root < 0:
        raise ValueError("not a KiCad schematic")
    j = root + 1
    end = _block_end(s, root)
    depth = 0
    pat = re.compile(r"\(" + re.escape(head) + r"[\s)]")
    while j < end - 1:
        ch = s[j]
        if ch == '"':
            j += 1
            while j < end and s[j] != '"':
                j += 2 if s[j] == "\\" else 1
            j += 1
            continue
        if ch == "(":
            if depth == 0 and pat.match(s, j):
                e = _block_end(s, j)
                out.append((j, e))
                j = e
                continue
            depth += 1
        elif ch == ")":
            depth -= 1
        j += 1
    return out


def _r(v: float) -> float:
    return round(v, 2)


def _fmt(v: float) -> str:
    t = f"{_r(v):.2f}".rstrip("0").rstrip(".")
    return "0" if t in ("-0", "") else t


# --- the header symbol and its pins -------------------------------------------------

def _lib_pins(s: str, lib_id: str) -> Dict[str, Tuple[float, float, float, float]]:
    """{pin number: (x, y, angle, length)} of the library symbol ``lib_id``
    from the schematic's embedded lib_symbols (symbol coordinates, y up)."""
    i = s.find(f'(symbol "{lib_id}"')
    if i < 0:
        raise ValueError(f"library symbol {lib_id} not in the schematic's lib_symbols")
    seg = s[i:_block_end(s, i)]
    pins = {}
    for m in re.finditer(r"\(pin\s", seg):
        blk = seg[m.start():_block_end(seg, m.start())]
        at = re.search(r"\(at " + _NUM + " " + _NUM + r"(?: " + _NUM + r")?\)", blk)
        ln = re.search(r"\(length " + _NUM + r"\)", blk)
        num = re.search(r'\(number "([^"]*)"', blk)
        if at and num:
            pins[num.group(1)] = (float(at.group(1)), float(at.group(2)),
                                  float(at.group(3) or 0), float(ln.group(1)) if ln else 0.0)
    return pins


def _find_symbol(s: str, ref: str):
    """(span, lib_id, (x, y, rot), mirror) of the placed symbol whose
    Reference is ``ref``."""
    for a, b in _top_blocks(s, "symbol"):
        blk = s[a:b]
        if not re.search(r'\(property "Reference" "' + re.escape(ref) + r'"', blk):
            continue
        lib = re.search(r'\(lib_id "([^"]+)"\)', blk).group(1)
        at = re.search(r"\(at " + _NUM + " " + _NUM + r"(?: " + _NUM + r")?\)", blk)
        mir = re.search(r"\(mirror (x|y)\)", blk)
        return (a, b), lib, (float(at.group(1)), float(at.group(2)), float(at.group(3) or 0)), \
            (mir.group(1) if mir else None)
    raise ValueError(f"no symbol {ref} in the schematic")


def _pin_ends(s: str, ref: str) -> Dict[str, Tuple[Tuple[float, float], Tuple[float, float]]]:
    """{pin: ((x, y) pin end in sheet coordinates, (dx, dy) unit direction
    pointing out of the symbol, away from the body)}."""
    _span, lib, (sx, sy, rot), mir = _find_symbol(s, ref)
    import math
    th = math.radians(rot)
    c, si = round(math.cos(th)), round(math.sin(th))
    out = {}
    for num, (px, py, ang, _ln) in _lib_pins(s, lib).items():
        # the pin's own direction points from its end into the body
        a = math.radians(ang)
        dx, dy = round(math.cos(a)), round(math.sin(a))
        x, y = px, py
        if mir == "x":
            y, dy = -y, -dy
        elif mir == "y":
            x, dx = -x, -dx
        # symbol space is y-up; the sheet is y-down; rotation is counter-clockwise
        rx, ry = x * c - y * si, x * si + y * c
        rdx, rdy = dx * c - dy * si, dx * si + dy * c
        out[num] = ((_r(sx + rx), _r(sy - ry)), (-rdx, rdy))     # outward = away from body
    return out


# --- wires, labels, junctions -------------------------------------------------------

def _wires(s):
    out = []
    for a, b in _top_blocks(s, "wire"):
        m = re.search(r"\(pts\s*\(xy " + _NUM + " " + _NUM + r"\)\s*\(xy " + _NUM + " " + _NUM + r"\)",
                      s[a:b])
        if m:
            x1, y1, x2, y2 = (float(v) for v in m.groups())
            out.append(((a, b), (_r(x1), _r(y1)), (_r(x2), _r(y2))))
    return out


def _labels(s):
    out = []
    for kind in ("global_label", "label", "hierarchical_label"):
        for a, b in _top_blocks(s, kind):
            blk = s[a:b]
            m = re.match(r"\(" + kind + r'\s+"((?:[^"\\]|\\.)*)"', blk)
            at = re.search(r"\(at " + _NUM + " " + _NUM + r"(?: " + _NUM + r")?\)", blk)
            if m and at:
                out.append({"span": (a, b), "kind": kind, "text": m.group(1),
                            "xy": (_r(float(at.group(1))), _r(float(at.group(2)))),
                            "rot": float(at.group(3) or 0)})
    return out


def _points(s, head):
    out = []
    for a, b in _top_blocks(s, head):
        at = re.search(r"\(at " + _NUM + " " + _NUM, s[a:b])
        if at:
            out.append(((a, b), (_r(float(at.group(1))), _r(float(at.group(2))))))
    return out


def _on_segment(p, a, b):
    (x, y), (x1, y1), (x2, y2) = p, a, b
    if abs((x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)) > 1e-3:
        return False
    return min(x1, x2) - 1e-3 <= x <= max(x1, x2) + 1e-3 and \
        min(y1, y2) - 1e-3 <= y <= max(y1, y2) + 1e-3


def _other_pin_ends(s, ref):
    """End points of every placed symbol pin except ``ref``'s -- so a pin
    wire that also reaches a part is seen as shared."""
    pts = set()
    for a, b in _top_blocks(s, "symbol"):
        blk = s[a:b]
        r = re.search(r'\(property "Reference" "([^"]+)"', blk)
        if not r or r.group(1) == ref:
            continue
        try:
            for (xy, _d) in _pin_ends(s, r.group(1)).values():
                pts.add(xy)
        except Exception:
            pass
    return pts


def _net_at_pin(pin_xy, wires, labels, junctions, other_pins):
    """(wire spans, labels, shared) of the wiring hanging off a pin end."""
    used, frontier, pts = set(), [pin_xy], {pin_xy}
    while frontier:
        p = frontier.pop()
        for k, (_span, a, b) in enumerate(wires):
            if k in used:
                continue
            if p == a or p == b or _on_segment(p, a, b):
                used.add(k)
                for q in (a, b):
                    if q not in pts:
                        pts.add(q)
                        frontier.append(q)
    labs = [lb for lb in labels if any(lb["xy"] == q for q in pts)
            or any(_on_segment(lb["xy"], wires[k][1], wires[k][2]) for k in used)]
    shared = any(j in pts for j in junctions) or any(q in other_pins for q in pts if q != pin_xy)
    # a T: a wire end landing mid-way on another wire of the chain
    ends = [e for k in used for e in (wires[k][1], wires[k][2])]
    for e in ends:
        if e == pin_xy:
            continue
        if sum(1 for k in used if _on_segment(e, wires[k][1], wires[k][2])) > 2:
            shared = True
    # a label sitting right on the pin end with no wire
    if not used:
        labs = [lb for lb in labels if lb["xy"] == pin_xy]
    return [wires[k][0] for k in used], labs, shared


# --- the edit -----------------------------------------------------------------------

def _new_uuid(blk: str) -> str:
    return re.sub(r'\(uuid "[^"]+"\)', lambda _m: f'(uuid "{_uuid.uuid4()}")', blk)


def _shift(blk: str, dx: float, dy: float) -> str:
    """Every (at x y ...) and (xy x y) in a block moved by (dx, dy)."""
    def at(m):
        return f"({m.group(1)} {_fmt(float(m.group(2)) + dx)} {_fmt(float(m.group(3)) + dy)}"
    return re.sub(r"\((at|xy) " + _NUM + " " + _NUM, at, blk)


def _rename(blk: str, kind: str, new: str) -> str:
    esc = new.replace("\\", "\\\\").replace('"', '\\"')
    return re.sub(r"^\(" + kind + r'\s+"(?:[^"\\]|\\.)*"', f'({kind} "{esc}"', blk, count=1)


def backannotate_pins(sch_path: str, ref: str, pin_nets: Dict[str, Optional[str]],
                      apply: bool = True, no_connect: bool = False,
                      out_path: Optional[str] = None) -> dict:
    """Make header ``ref``'s pins in ``sch_path`` carry ``pin_nets`` ({pin:
    net or None}); pins not in the dict are left alone. Returns {"changes":
    [{"pin", "was", "net", "action"}], "shared": [...], "schematic", "backup"}.
    ``apply`` False only reports. See the module doc."""
    with open(sch_path, "r", encoding="utf-8", newline="") as f:
        s = f.read()
    ends = _pin_ends(s, ref)
    wires, labels = _wires(s), _labels(s)
    junctions = [xy for _sp, xy in _points(s, "junction")]
    ncs = _points(s, "no_connect")
    others = _other_pin_ends(s, ref)

    # the template for a new pin wire + label: the commonest wired pin
    template = None
    for num, (xy, d) in sorted(ends.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
        ws, labs, shared = _net_at_pin(xy, wires, labels, junctions, others)
        if len(ws) == 1 and len(labs) == 1 and not shared:
            (a, b) = ws[0]
            w = next(w for w in wires if w[0] == ws[0])
            far = w[2] if w[1] == xy else w[1]
            template = {"xy": xy, "wire": s[a:b], "label": s[labs[0]["span"][0]:labs[0]["span"][1]],
                        "kind": labs[0]["kind"], "far": far}
            break

    edits = []            # (start, end, replacement)
    changes, shared_pins = [], []
    inserts = []
    for num, want in sorted(pin_nets.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0):
        num = str(num)
        if num not in ends:
            changes.append({"pin": num, "was": None, "net": want, "action": "no such pin"})
            continue
        xy, d = ends[num]
        ws, labs, shared = _net_at_pin(xy, wires, labels, junctions, others)
        was = labs[0]["text"] if labs else None
        want = want or None
        if (was or None) == want:
            continue
        if shared or len({lb["text"] for lb in labs}) > 1:
            shared_pins.append({"pin": num, "was": was, "net": want})
            continue
        if was and want:
            for lb in labs:
                a, b = lb["span"]
                edits.append((a, b, _rename(s[a:b], lb["kind"], want)))
            changes.append({"pin": num, "was": was, "net": want, "action": "renamed"})
        elif want and not was:
            if template is None:
                shared_pins.append({"pin": num, "was": None, "net": want,
                                    "why": "no wired pin to copy a wire and label from"})
                continue
            dx, dy = xy[0] - template["xy"][0], xy[1] - template["xy"][1]
            wire = _new_uuid(_shift(template["wire"], dx, dy))
            lab = _new_uuid(_rename(_shift(template["label"], dx, dy), template["kind"], want))
            # the shape (input / output / bidirectional / passive) the net's
            # other labels already have, so ERC sees one signal direction
            same = next((lb for lb in labels if lb["text"] == want and lb["kind"] == template["kind"]),
                        None)
            if same is not None:
                sh = re.search(r"\(shape (\w+)\)", s[same["span"][0]:same["span"][1]])
                if sh:
                    lab = re.sub(r"\(shape \w+\)", f"(shape {sh.group(1)})", lab, count=1)
            inserts += [wire, lab]
            for sp, q in ncs:
                if q == xy:
                    edits.append((sp[0], sp[1], ""))
            changes.append({"pin": num, "was": None, "net": want, "action": "wired"})
        else:
            for a, b in ws:
                edits.append((a, b, ""))
            for lb in labs:
                edits.append((lb["span"][0], lb["span"][1], ""))
            if no_connect:
                inserts.append(f'(no_connect\n\t\t(at {_fmt(xy[0])} {_fmt(xy[1])})\n'
                               f'\t\t(uuid "{_uuid.uuid4()}")\n\t)')
            changes.append({"pin": num, "was": was, "net": None, "action": "freed"})

    res = {"changes": changes, "shared": shared_pins, "schematic": sch_path, "backup": None,
           "ref": ref}
    if not apply or not (edits or inserts):
        return res
    # apply from the end so earlier spans stay valid; a deleted block takes
    # its leading indentation and newline with it
    out = s
    for a, b, rep in sorted(edits, key=lambda e: -e[0]):
        if rep == "":
            k = a
            while k > 0 and out[k - 1] in " \t":
                k -= 1
            if k > 0 and out[k - 1] == "\n":
                k -= 1
                if k > 0 and out[k - 1] == "\r":
                    k -= 1
            a = k
        out = out[:a] + rep + out[b:]
    if inserts:
        nl = "\r\n" if "\r\n" in out else "\n"
        # before the sheet's sheet_instances / embedded_fonts, else before the
        # closing paren of kicad_sch
        m = None
        for head in ("sheet_instances", "embedded_fonts"):
            sp = _top_blocks(out, head)
            if sp:
                m = sp[0][0]
                break
        if m is None:
            m = out.rstrip().rfind(")")
        k = m
        while k > 0 and out[k - 1] in " \t":
            k -= 1
        ins = "".join("\t" + blk.replace("\r\n", "\n").replace("\n", nl) + nl for blk in inserts)
        out = out[:k] + ins + out[k:]
    dst = out_path or sch_path
    if dst == sch_path:
        bak = sch_path + ".bak"
        shutil.copy2(sch_path, bak)
        res["backup"] = bak
    with open(dst, "w", encoding="utf-8", newline="") as f:
        f.write(out)
    res["schematic"] = dst
    return res


def header_ref_from_cellmap(netlist_path: str, header_cell: Optional[str] = None) -> Optional[str]:
    """The netlist reference placed as the header package (the
    <netlist>.cellmap.json entry "ref:J30": "30_PIN_pkg"), if there is one."""
    import json
    p = netlist_path + ".cellmap.json"
    if not os.path.exists(p):
        return None
    try:
        m = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None
    if isinstance(m.get("footprint_map"), dict):
        m = m["footprint_map"]
    for k, v in m.items():
        if k.startswith("ref:") and (header_cell is None or v == header_cell):
            if header_cell is not None or re.match(r"ref:(J|P|CON|X)\d", k):
                return k[4:]
    return None
