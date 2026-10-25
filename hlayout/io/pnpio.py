"""Pick-and-place (centroid) file: where each placed part's centre is and
how it is turned, for the assembly machine.

One row per placed part -- a placed cell instance carrying a reference
designator (its Label) -- at any depth below the cell, found the same way
the thermal model finds parts: an instance without a ref-des is a group
(a sub-module, an array) and is looked into; the substrate itself is
never a part.

Centroid
    The centre of the part's pad pattern (the bounding box of its
    pin-numbered shapes and pin placements, IPC-7351 style), which is
    where a pick-and-place nozzle is aimed. A bare die, or a part with no
    numbered pads, uses the centre of its body (the bounding box of its
    geometry, text left out).

Rotation
    The part's placement angle in degrees, counter-clockwise, 0-360, as
    seen from above -- the instance's own rotation composed with every
    instance it sits inside. A mirrored placement is flagged.

Origin
    "substrate" (default): the lower-left corner of the substrate, the
    datum an assembler fixtures to; falls back to the design origin when
    no substrate is found. "design": the layout's own (0, 0). Or an
    explicit (x, y) in DBU.

Kinds
    "smd" (packaged parts), "die" (bare die: places chip_pin cells or has
    two or more shapes on a Wire Bond Rules die-pad layer), "connector"
    (J/P/X/CN/HDR ref-des) and "printed" (a thick-film resistor -- ink,
    not a part to pick; left out unless asked for).

Formats
    "csv": Designator, Comment, Footprint, Kind, Mid X, Mid Y, Rotation,
    Side, Mirrored (mm, degrees) -- the common centroid layout most
    assembly houses accept. "kicad": the KiCad footprint-position (.pos)
    text layout (Ref Val Package PosX PosY Rot Side).
"""

import csv
import datetime
import math
import re
from typing import List, Optional

from ..core.maskops import _compose_transforms, _sref_point_transform
from ..core.refdes import is_connector_refdes
from ..core.shapes import Text, expand_arefs_to_srefs
from ..core.units import DBU_MM

CSV_COLUMNS = ["Designator", "Comment", "Footprint", "Kind", "Mid X (mm)", "Mid Y (mm)",
               "Rotation", "Side", "Mirrored"]

_SUBSTRATE_WORDS = ("substrate", "alumina", "ceramic")


def _natural(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s or "")]


def _bbox_union(boxes):
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _box_of_points(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _ref_box(library, ref):
    """Bbox of a placed instance in its parent's coordinates."""
    box = library.cell_bbox(ref.cell_name)
    if box is None:
        return None
    tf = _sref_point_transform(ref)
    x0, y0, x1, y1 = box
    return _box_of_points([tf(p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))])


def _pad_box(library, cell):
    """Local bbox of a cell's pad pattern: pin-numbered shapes and pin
    placements. None when it has no numbered pads."""
    boxes = []
    for sh in cell.shapes:
        if getattr(sh, "pin", ""):
            try:
                boxes.append(tuple(sh.bbox()))
            except Exception:
                pass
    for ref in cell.srefs:
        if getattr(ref, "pin", ""):
            boxes.append(_ref_box(library, ref) or (ref.origin[0], ref.origin[1],
                                                    ref.origin[0], ref.origin[1]))
    return _bbox_union(boxes)


def _body_box(library, cell, depth=0):
    """Local bbox of a cell's geometry with text left out."""
    if depth > 64:
        return None
    boxes = []
    for sh in cell.shapes:
        if isinstance(sh, Text):
            continue
        try:
            boxes.append(tuple(sh.bbox()))
        except Exception:
            pass
    for ref in cell.srefs + expand_arefs_to_srefs(cell.arefs):
        child = library.cells.get(ref.cell_name)
        if child is None:
            continue
        b = _body_box(library, child, depth + 1)
        if b is None:
            continue
        tf = _sref_point_transform(ref)
        boxes.append(_box_of_points([tf(p) for p in ((b[0], b[1]), (b[2], b[1]),
                                                       (b[2], b[3]), (b[0], b[3]))]))
    return _bbox_union(boxes)


def _orientation(transform):
    """(angle deg CCW 0-360, mirrored) of a composed point transform."""
    o = transform((0.0, 0.0))
    ex = transform((1.0, 0.0))
    ey = transform((0.0, 1.0))
    ux, uy = ex[0] - o[0], ex[1] - o[1]
    vx, vy = ey[0] - o[0], ey[1] - o[1]
    mirrored = (ux * vy - uy * vx) < 0
    ang = math.degrees(math.atan2(uy, ux)) % 360.0
    ang = round(ang, 3) % 360.0
    return (0.0 if abs(ang) < 1e-9 else ang), mirrored


def _is_substrate(name):
    return any(w in (name or "").lower() for w in _SUBSTRATE_WORDS)


def _resistor_layers(library) -> set:
    from .techfile import load_resistor_geometry_rules
    rg = load_resistor_geometry_rules(library)
    out = {int(n) for n in (rg.get("ink_layers") or {}).values() if isinstance(n, int) and n >= 0}
    if isinstance(rg.get("resistor_layer"), int) and rg["resistor_layer"] >= 0:
        out.add(rg["resistor_layer"])
    return out


def _die_pad_layers(library) -> set:
    from .techfile import load_bond_rules
    return {int(n) for n in (load_bond_rules(library).get("die_pad_layers") or [])}


def _deep_count(library, cell, layers, depth=0):
    if depth > 64:
        return 0
    n = sum(1 for sh in cell.shapes if sh.layer in layers)
    for ref in cell.srefs + expand_arefs_to_srefs(cell.arefs):
        child = library.cells.get(ref.cell_name)
        if child is not None:
            n += _deep_count(library, child, layers, depth + 1)
    return n


def _kind(library, cell, label, res_layers, pad_layers):
    if {sh.layer for sh in cell.shapes} & res_layers:
        return "printed"
    if is_connector_refdes(label):        # header pins may sit on a die-pad layer too
        return "connector"
    if any(r.cell_name.lower().startswith("chip_pin") for r in cell.srefs) or \
            (pad_layers and _deep_count(library, cell, pad_layers) >= 2):
        return "die"
    return "smd"


def substrate_box(library, cell):
    """Bounding box (DBU) of the substrate in ``cell``: the substrate
    layer's geometry (Thermal Analysis setting, else a layer named
    substrate/alumina/ceramic), else a placed cell so named. None if
    there is no substrate."""
    from ..core.maskops import layer_to_shapely_flattened
    try:
        from ..core.thermal_build import find_substrate_layer
        layer = find_substrate_layer(library, cell)
    except Exception:
        layer = None
    if layer is not None:
        try:
            g = layer_to_shapely_flattened(library, cell, layer)
            if g is not None and not g.is_empty:
                return tuple(float(v) for v in g.bounds)
        except Exception:
            pass

    def find(c, tf, depth):
        if depth > 64:
            return None
        for ref in c.srefs + expand_arefs_to_srefs(c.arefs):
            child = library.cells.get(ref.cell_name)
            if child is None:
                continue
            t = _compose_transforms(tf, _sref_point_transform(ref))
            if _is_substrate(child.name):
                b = _body_box(library, child)
                if b is not None:
                    return _box_of_points([t(p) for p in ((b[0], b[1]), (b[2], b[1]),
                                                            (b[2], b[3]), (b[0], b[3]))])
            hit = find(child, t, depth + 1)
            if hit is not None:
                return hit
        return None

    return find(cell, None, 0)


def pick_and_place_rows(library, cell_name: str, origin="substrate", include_printed: bool = False,
                        include_dies: bool = True) -> dict:
    """The pick-and-place table of ``cell_name``: {"rows": [...], "origin":
    (x, y) DBU, "origin_kind": "substrate" | "design" | "explicit"}. Each
    row: designator, comment, footprint (the placed cell's name), kind,
    x_mm, y_mm (centroid relative to the origin), rotation (deg CCW,
    0-360), side ("top"), mirrored, plus x_dbu/y_dbu in design
    coordinates. Rows are in ref-des order."""
    cell = library.get_cell(cell_name)
    res_layers = _resistor_layers(library)
    pad_layers = _die_pad_layers(library)
    rows = []

    def walk(c, tf, depth):
        if depth > 64:
            return
        for ref in c.srefs + expand_arefs_to_srefs(c.arefs):
            child = library.cells.get(ref.cell_name)
            if child is None or _is_substrate(child.name):
                continue
            t = _compose_transforms(tf, _sref_point_transform(ref))
            label = (ref.label or "").strip()
            if not label:
                walk(child, t, depth + 1)          # a group, not a part
                continue
            kind = _kind(library, child, label, res_layers, pad_layers)
            if (kind == "printed" and not include_printed) or (kind == "die" and not include_dies):
                continue
            box = None if kind == "die" else _pad_box(library, child)
            box = box or _body_box(library, child) or (0, 0, 0, 0)
            cx, cy = t(((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0))
            ang, mir = _orientation(t)
            rows.append({"designator": label, "comment": child.name, "footprint": child.name,
                         "kind": kind, "x_dbu": cx, "y_dbu": cy, "rotation": ang,
                         "side": "top", "mirrored": mir})

    walk(cell, None, 0)

    if isinstance(origin, (tuple, list)):
        ox, oy, okind = float(origin[0]), float(origin[1]), "explicit"
    elif origin == "design":
        ox, oy, okind = 0.0, 0.0, "design"
    else:
        sb = substrate_box(library, cell)
        ox, oy, okind = (sb[0], sb[1], "substrate") if sb else (0.0, 0.0, "design")
    for r in rows:
        r["x_mm"] = round((r["x_dbu"] - ox) * DBU_MM, 4)
        r["y_mm"] = round((r["y_dbu"] - oy) * DBU_MM, 4)
    rows.sort(key=lambda r: _natural(r["designator"]))
    return {"rows": rows, "origin": (ox, oy), "origin_kind": okind}


def _fmt(v, nd=4):
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def write_pick_and_place(table: dict, path: str, fmt: Optional[str] = None,
                         design_name: str = "") -> str:
    """Write pick_and_place_rows()'s table to ``path``. ``fmt`` is "csv" or
    "kicad"; by default .pos is KiCad and anything else CSV. Returns the
    format written."""
    fmt = (fmt or ("kicad" if path.lower().endswith(".pos") else "csv")).lower()
    rows = table["rows"]
    if fmt == "kicad":
        when = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ox, oy = table["origin"]
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"### Footprint positions - created on {when} ###\n")
            f.write(f"### Printed by HLayout{(' - ' + design_name) if design_name else ''}\n")
            f.write("## Unit = mm, Angle = deg.\n")
            f.write("## Side : top\n")
            f.write(f"## Origin : {table['origin_kind']} ({_fmt(ox * DBU_MM)}, {_fmt(oy * DBU_MM)}) mm\n")
            f.write(f"{'# Ref':<12}{'Val':<24}{'Package':<24}{'PosX':>12}{'PosY':>12}{'Rot':>10}  Side\n")
            for r in rows:
                f.write(f"{r['designator']:<12}{r['comment']:<24}{r['footprint']:<24}"
                        f"{r['x_mm']:>12.4f}{r['y_mm']:>12.4f}{r['rotation']:>10.4f}  "
                        f"{r['side']}\n")
            f.write("## End\n")
        return "kicad"
    if fmt != "csv":
        raise ValueError(f"unknown pick-and-place format {fmt!r} (csv or kicad)")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for r in rows:
            w.writerow([r["designator"], r["comment"], r["footprint"], r["kind"],
                        _fmt(r["x_mm"]), _fmt(r["y_mm"]), _fmt(r["rotation"], 3),
                        r["side"].capitalize(), "Yes" if r["mirrored"] else "No"])
    return "csv"
