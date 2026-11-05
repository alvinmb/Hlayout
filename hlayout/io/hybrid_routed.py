# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Hybrid router results (Phase 5): read a ``*.routed.json`` written by the
chip-and-wire router (``app.freerouting.cli.hybrid.HybridRouterCli``) and
work out its bond diagram.

The router's JSON is integer micrometres with the origin at the substrate's
lower-left corner -- the same unit as HLayout's DBU, so coordinates carry
over unchanged (plus any placement offset). Session.import_routed_hybrid()
turns a result into layout geometry; this module is the file side and has
no HLayout dependencies beyond the standard library.
"""

import csv
import json
import math
from typing import Dict, List, Optional

#: Bond diagram columns, the same as the router's BondDiagramCsv.HEADER
BOND_COLUMNS = (
    "seq", "wire", "net", "purpose", "first_bond", "first_x_um", "first_y_um",
    "second_bond", "second_x_um", "second_y_um", "material", "diameter_um",
    "bond_type", "loop_height_um", "length_um", "first_angle_deg",
    "second_angle_deg", "fixed",
)

_QUARTER = {"R0": 0, "R90": 90, "R180": 180, "R270": 270}


def load_routed(path: str) -> dict:
    """Parse a ``*.routed.json``. Raises ValueError if it isn't one."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "design" not in data:
        raise ValueError(f"{path} is not a routed hybrid result (no 'design')")
    data.setdefault("traces", [])
    data.setdefault("windows", [])
    data.setdefault("unrouted", [])
    data.setdefault("issues", [])
    return data


def rotate(rotation: str, x: float, y: float):
    """A local offset turned by a hybrid Rotation (R0/R90/R180/R270, CCW)."""
    r = _QUARTER.get(str(rotation or "R0").upper(), 0)
    if r == 90:
        return -y, x
    if r == 180:
        return -x, -y
    if r == 270:
        return y, -x
    return x, y


def rotation_degrees(rotation: str) -> int:
    return _QUARTER.get(str(rotation or "R0").upper(), 0)


def _pt(p) -> tuple:
    return (int((p or {}).get("x", 0)), int((p or {}).get("y", 0)))


def die_pad_position(die: dict, pad: dict) -> tuple:
    ox, oy = _pt(die.get("origin"))
    lx, ly = _pt(pad.get("position"))
    dx, dy = rotate(die.get("rotation"), lx, ly)
    return (ox + dx, oy + dy)


def endpoint_label(end: dict) -> str:
    """``die.pad`` for a die pad, the pad id for a substrate pad (as the router prints it)."""
    if (end or {}).get("kind") == "DIE_PAD":
        return f"{end.get('die')}.{end.get('pad')}"
    return str((end or {}).get("pad"))


def endpoint_position(design: dict, end: dict) -> Optional[tuple]:
    if not end:
        return None
    if end.get("kind") == "DIE_PAD":
        for d in design.get("dies", []):
            if d.get("refdes") == end.get("die"):
                for p in d.get("pads", []):
                    if str(p.get("id")) == str(end.get("pad")):
                        return die_pad_position(d, p)
        return None
    for p in design.get("substratePads", []):
        if p.get("id") == end.get("pad"):
            return _pt(p.get("position"))
    return None


def wire_purpose(w: dict) -> str:
    a = (w.get("from") or {}).get("kind") == "DIE_PAD"
    b = (w.get("to") or {}).get("kind") == "DIE_PAD"
    if a and b:
        return "DIE_TO_DIE"
    if a or b:
        return "DIE_BOND"
    return "JUMPER"


def _outward_normal(die: dict, pad: dict) -> tuple:
    px, py = _pt(pad.get("position"))
    w, h = int(die.get("widthUm", 0)), int(die.get("heightUm", 0))
    left, right, bottom, top = px, w - px, py, h - py
    m = min(left, right, bottom, top)
    if m == bottom:
        n = (0, -1)
    elif m == top:
        n = (0, 1)
    elif m == left:
        n = (-1, 0)
    else:
        n = (1, 0)
    return rotate(die.get("rotation"), *n)


def _departure(design: dict, end: dict, at: tuple, toward: tuple) -> Optional[float]:
    if (end or {}).get("kind") != "DIE_PAD":
        return None
    for d in design.get("dies", []):
        if d.get("refdes") != end.get("die"):
            continue
        for p in d.get("pads", []):
            if str(p.get("id")) == str(end.get("pad")):
                nx, ny = _outward_normal(d, p)
                dx, dy = toward[0] - at[0], toward[1] - at[1]
                ln = math.hypot(dx, dy)
                if ln == 0:
                    return None
                c = max(-1.0, min(1.0, (dx * nx + dy * ny) / ln))
                return round(math.degrees(math.acos(c)), 1)
    return None


def bond_diagram(design: dict) -> List[dict]:
    """The bond diagram of a hybrid design (a routed result's ``design``):
    one dict per wire with the BOND_COLUMNS keys, in bonding order -- die
    bonds die by die in pad order, then die-to-die wires, then jumpers. The
    same rows as the router's ``--bond-diagram`` CSV."""
    wires = list(design.get("wires", []))
    dies = design.get("dies", [])
    die_ix = {d.get("refdes"): i for i, d in enumerate(dies)}
    pad_ix = {d.get("refdes"): {str(p.get("id")): j for j, p in enumerate(d.get("pads", []))}
              for d in dies}
    big = 0xFFFFF

    def key(item):
        i, w = item
        purpose = wire_purpose(w)
        if purpose == "JUMPER":
            return (2, 0, 0, i)
        end = w["from"] if (w.get("from") or {}).get("kind") == "DIE_PAD" else w["to"]
        d = end.get("die")
        return (0 if purpose == "DIE_BOND" else 1, die_ix.get(d, big),
                pad_ix.get(d, {}).get(str(end.get("pad")), big), i)

    rows = []
    for _i, w in sorted(enumerate(wires), key=key):
        a = endpoint_position(design, w.get("from"))
        b = endpoint_position(design, w.get("to"))
        if a is None or b is None:
            continue
        spec = w.get("spec") or {}
        rows.append({
            "seq": len(rows) + 1,
            "wire": w.get("id"),
            "net": w.get("net"),
            "purpose": wire_purpose(w),
            "first_bond": endpoint_label(w.get("from")),
            "first_x_um": a[0], "first_y_um": a[1],
            "second_bond": endpoint_label(w.get("to")),
            "second_x_um": b[0], "second_y_um": b[1],
            "material": spec.get("material", "GOLD"),
            "diameter_um": int(spec.get("diameterUm", 25)),
            "bond_type": spec.get("bondType", "BALL_WEDGE"),
            "loop_height_um": int(spec.get("loopHeightUm", 150)),
            "length_um": int(round(math.hypot(b[0] - a[0], b[1] - a[1]))),
            "first_angle_deg": _departure(design, w.get("from"), a, b),
            "second_angle_deg": _departure(design, w.get("to"), b, a),
            "fixed": bool(w.get("fixed", False)),
        })
    return rows


def read_bond_diagram_csv(path: str) -> List[dict]:
    """Rows of a bond diagram CSV written by the router (``--bond-diagram``)
    or write_bond_diagram_csv(), with numbers converted back."""
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            row: Dict[str, object] = dict(r)
            for k in ("seq", "first_x_um", "first_y_um", "second_x_um", "second_y_um",
                      "diameter_um", "loop_height_um", "length_um"):
                row[k] = int(row[k]) if row.get(k) not in (None, "") else None
            for k in ("first_angle_deg", "second_angle_deg"):
                row[k] = float(row[k]) if row.get(k) not in (None, "") else None
            row["fixed"] = str(row.get("fixed", "")).lower() == "true"
            out.append(row)
    return out


def write_bond_diagram_csv(rows: List[dict], path: str) -> None:
    """Write bond diagram rows in the router's CSV layout."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(BOND_COLUMNS)
        for r in rows:
            vals = []
            for k in BOND_COLUMNS:
                v = r.get(k)
                if v is None:
                    vals.append("")
                elif isinstance(v, bool):
                    vals.append("true" if v else "false")
                elif isinstance(v, float):
                    vals.append(f"{v:.1f}")
                else:
                    vals.append(v)
            w.writerow(vals)


_SUFFIX = {"R": 1.0, "K": 1e3, "M": 1e6, "G": 1e9}


def parse_ohms(text) -> Optional[float]:
    """'1k' -> 1000, '4k7' -> 4700, '100R' -> 100, '2.2M' -> 2.2e6, 330 -> 330;
    None when it can't be read."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip().upper().replace("OHMS", "").replace("OHM", "").replace("\u03a9", "")
    s = s.replace(" ", "")
    if not s:
        return None
    for i, ch in enumerate(s):
        if ch in _SUFFIX:
            head, tail = s[:i], s[i + 1:]
            try:
                whole = float(head) if head else 0.0
                frac = float("0." + tail) if tail and tail.isdigit() and "." not in head else 0.0
            except ValueError:
                return None
            return (whole + frac) * _SUFFIX[ch]
    try:
        return float(s)
    except ValueError:
        return None


def format_ohms(value) -> str:
    """1200 -> '1.2 k', 47 -> '47', None -> '?'."""
    if value is None:
        return "?"
    v = float(value)
    for div, suf in ((1e9, " G"), (1e6, " M"), (1e3, " k")):
        if abs(v) >= div:
            return f"{v / div:.3g}{suf}"
    return f"{v:.3g}"
