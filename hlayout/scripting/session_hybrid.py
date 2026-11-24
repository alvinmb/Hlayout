# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Hybrid editing: bond-wire info and finger moves, printed-resistor info /
resize / keep-outs, unused bonds, bond landings, I/O pads and the header
pinout (assign, optimise, swap, back-annotate), the bond diagram.

Part of hlayout.scripting.api.Session (a mixin): these methods are
Session's own -- call them on a Session as always."""

import json
import math
import os
from typing import Optional

from ..core.errors import LayoutError
from ..io.techfile import dielectric_layer_number


class HybridMixin:
    def bond_diagram(self) -> list:
        """The bond diagram kept with the design by import_routed_hybrid():
        one dict per wire (hybrid_routed.BOND_COLUMNS), in bonding order."""
        raw = self.library.tech_params.get("bond_diagram")
        if not raw:
            return []
        cached = getattr(self, "_bond_rows_cache", None)
        if cached is not None and cached[0] is raw:
            return cached[1]
        try:
            rows = json.loads(raw)
        except (TypeError, ValueError):
            return []
        rows = rows if isinstance(rows, list) else []
        self._bond_rows_cache = (raw, rows)
        return rows

    def export_bond_diagram(self, path: str) -> int:
        """Write the stored bond diagram as CSV (the router's column layout).
        Returns the number of wires."""
        from ..io.hybrid_routed import write_bond_diagram_csv
        rows = self.bond_diagram()
        if not rows:
            raise LayoutError("no bond diagram -- import a routed hybrid result first")
        write_bond_diagram_csv(rows, path)
        return len(rows)

    # --- hybrid editing (Phase 6): bond info, finger moves, resistor resize ----
    def _hybrid_state(self) -> Optional[dict]:
        """The routed hybrid kept by import_routed_hybrid(): {"cell", "design",
        "layer_map", "origin"}; None when the design has none."""
        raw = self.library.tech_params.get("hybrid_design")
        if not raw:
            return None
        cached = getattr(self, "_hybrid_cache", None)
        if cached is not None and cached[0] is raw:
            return cached[1]
        try:
            st = json.loads(raw)
        except (TypeError, ValueError):
            return None
        st = st if isinstance(st, dict) and "design" in st else None
        self._hybrid_cache = (raw, st)
        return st

    def _save_hybrid_state(self, st: dict) -> None:
        from ..io import hybrid_routed as HR
        self.library.tech_params["hybrid_design"] = json.dumps(st)
        self.library.tech_params["bond_diagram"] = json.dumps(HR.bond_diagram(st["design"]))

    def _hybrid_bond_rules(self) -> dict:
        """min/max wire length and max departure angle: the routed design's
        own rules when there is one, else the Wire Bonds tab of Setup > Technology Properties."""
        from ..io.techfile import load_bond_rules
        st = self._hybrid_state()
        bw = (((st or {}).get("design") or {}).get("rules") or {}).get("bondWire") or {}
        if bw:
            return {"min": float(bw.get("minLengthUm") or 500),
                    "max": float(bw.get("maxLengthUm") or 3000),
                    "angle": float(bw.get("maxAngleDeg") or 45.0)}
        br = load_bond_rules(self.library)
        return {"min": float(br.get("min_length", 300)), "max": float(br.get("max_length", 5000)),
                "angle": 45.0}

    def _bond_layer(self) -> int:
        from ..io.techfile import load_bond_rules
        st = self._hybrid_state()
        if st and "bond" in (st.get("layer_map") or {}):
            return int(st["layer_map"]["bond"])
        return int(load_bond_rules(self.library).get("layer", -1))

    def bond_wire_info(self, point, tol: float = 50.0, cell_name: Optional[str] = None
                       ) -> Optional[dict]:
        """The wire bond nearest ``point`` (within ``tol`` DBU) as a dict --
        ends, length, and, when the bond diagram knows the wire, its id, net,
        material, diameter, bond type, loop height and departure angles --
        plus a one-line ``text`` for the status bar. None if no bond is near."""
        from ..core.shapes import Path as _Path
        cell = self.library.get_cell(cell_name or self.current_cell_name)
        layer = self._bond_layer()
        if layer < 0:
            return None
        px, py = float(point[0]), float(point[1])
        best, best_d = None, float(tol)
        for sh in cell.shapes:
            if not (isinstance(sh, _Path) and sh.layer == layer and len(sh.points) >= 2):
                continue
            (ax, ay), (bx, by) = sh.points[0], sh.points[-1]
            vx, vy = bx - ax, by - ay
            ll = vx * vx + vy * vy
            t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / ll))
            d = math.hypot(px - (ax + t * vx), py - (ay + t * vy))
            if d <= best_d:
                best, best_d = sh, d
        if best is None:
            return None
        a, b = tuple(best.points[0]), tuple(best.points[-1])
        info = {"a": a, "b": b, "length_um": round(math.hypot(b[0] - a[0], b[1] - a[1]))}
        st = self._hybrid_state()
        ox, oy = (st or {}).get("origin") or (0, 0)
        for r in self.bond_diagram():
            e1 = (r["first_x_um"] + ox, r["first_y_um"] + oy)
            e2 = (r["second_x_um"] + ox, r["second_y_um"] + oy)
            if {e1, e2} == {a, b}:
                info.update(r)
                break
        mat = {"GOLD": "Au", "ALUMINIUM": "Al", "ALUMINUM": "Al", "COPPER": "Cu"}
        parts = []
        if info.get("wire"):
            parts.append(f"{info['wire']} {info.get('net') or ''}".strip())
            parts.append(f"{info['first_bond']} -> {info['second_bond']}")
        parts.append(f"{info['length_um'] / 1000.0:.2f} mm")
        if info.get("material"):
            parts.append(f"{mat.get(str(info['material']).upper(), info['material'])} "
                         f"{info.get('diameter_um')} um {str(info.get('bond_type', '')).lower()}")
            parts.append(f"loop {info.get('loop_height_um')} um")
        angles = [x for x in (info.get("first_angle_deg"), info.get("second_angle_deg"))
                  if x is not None]
        if angles:
            parts.append("angle " + "/".join(f"{x:g}" for x in angles) + " deg")
        info["text"] = "Bond " + " · ".join(parts)
        return info

    def _hybrid_pad_at(self, cell, point, tol=0.0):
        """(shape, pad id) of the bond finger / jumper land under ``point``:
        a numbered substrate pad drawn in ``cell`` itself (an imported
        hybrid route), else a plain conductor rectangle a wire bond lands
        on (Fan Out Die Bonds, Hybrid Route, one drawn by hand) -- id
        "x,y" of its centre."""
        from ..core.shapes import Path as _P, Rectangle
        px, py = point
        best = None
        for sh in cell.shapes:
            if not (isinstance(sh, Rectangle) and getattr(sh, "pin", "")):
                continue
            x0, y0, x1, y1 = sh.bbox()
            if x0 - tol <= px <= x1 + tol and y0 - tol <= py <= y1 + tol:
                area = (x1 - x0) * (y1 - y0)
                if best is None or area < best[2]:
                    best = (sh, sh.pin, area)
        if best:
            return best[0], best[1]
        try:
            blay = self._bond_layer()
        except Exception:
            blay = -1
        ends = [tuple(p) for sh in cell.shapes
                if isinstance(sh, _P) and sh.layer == blay and len(sh.points) == 2
                for p in sh.points]
        la, lb = self._route_layers()
        for sh in cell.shapes:
            if not isinstance(sh, Rectangle) or sh.layer not in (la, lb) or getattr(sh, "pin", ""):
                continue
            x0, y0, x1, y1 = sh.bbox()
            if not (x0 - tol <= px <= x1 + tol and y0 - tol <= py <= y1 + tol):
                continue
            # a bond lands on it -- or did, and was moved just off it
            # (Move Wire Bond): a bond end within the finger's own size
            reach = self._finger_reach(sh)
            if not any(x0 - reach <= ex <= x1 + reach and y0 - reach <= ey <= y1 + reach
                       for ex, ey in ends):
                continue
            area = (x1 - x0) * (y1 - y0)
            if best is None or area < best[2]:
                best = (sh, f"{round((x0 + x1) / 2)},{round((y0 + y1) / 2)}", area)
        return (best[0], best[1]) if best else (None, None)

    @staticmethod
    def _finger_reach(pad) -> float:
        """How far off a bond finger a bond end may lie and still count as
        its own (moved off it by hand): the finger's larger side, at least
        0.3 mm."""
        x0, y0, x1, y1 = pad.bbox()
        return max(300.0, x1 - x0, y1 - y0)

    def move_bond_finger(self, at, to, cell_name: Optional[str] = None,
                         dry_run: bool = False, tol: float = 0.0) -> dict:
        """Move the bond finger (or jumper land) under ``at`` so its centre
        lands on ``to``, and re-bond: every wire ending on it follows. One
        undo step. Each moved wire is checked against the bond rules (length
        min/max, departure angle at the die); ``warnings`` lists what fails,
        and ``on_trace`` says whether the finger still touches its own
        printed trace (if not, the net needs re-routing). ``dry_run`` only
        reports -- the live preview of Edit > Move Bond Finger."""
        from shapely.geometry import LineString, box
        from ..core.shapes import Path as _Path
        from ..io import hybrid_routed as HR

        name = cell_name or self.current_cell_name
        cell = self.library.get_cell(name)
        pad, pid = self._hybrid_pad_at(cell, at, tol)
        if pad is None:
            raise LayoutError(f"no bond finger at ({at[0]}, {at[1]})")
        x0, y0, x1, y1 = pad.bbox()
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        nx, ny = round(float(to[0])), round(float(to[1]))
        dx, dy = nx - cx, ny - cy
        layer = self._bond_layer()
        moved = []
        balls = []
        rebond = {}                 # (shape, end) -> new point: ends that were off the finger
        bonds = [sh for sh in cell.shapes
                 if isinstance(sh, _Path) and sh.layer == layer and len(sh.points) == 2]
        for sh in bonds:
            for k in (0, 1):
                ex, ey = sh.points[k]
                if x0 - 1 <= ex <= x1 + 1 and y0 - 1 <= ey <= y1 + 1:
                    moved.append((sh, k))
        if not moved:
            # its bond was moved just off it: the nearest end within reach
            # comes back onto the finger, at its new centre
            reach = self._finger_reach(pad)
            near = []
            for sh in bonds:
                for k in (0, 1):
                    ex, ey = sh.points[k]
                    d = max(x0 - ex, ex - x1, y0 - ey, ey - y1, 0)
                    if d <= reach:
                        near.append((d, sh, k))
            if near:
                _d, sh, k = min(near, key=lambda t: t[0])
                moved.append((sh, k))
                rebond[(id(sh), k)] = (nx, ny)
        ends = {tuple(sh.points[k]) for sh, k in moved}
        for sh in cell.shapes:
            # the bond's ball / stitch discs at those ends go with it
            if sh.layer == layer and not isinstance(sh, _Path) and hasattr(sh, "points"):
                bx0, by0, bx1, by1 = sh.bbox()
                c = ((bx0 + bx1) / 2.0, (by0 + by1) / 2.0)
                if any(abs(c[0] - e[0]) <= 2 and abs(c[1] - e[1]) <= 2 for e in ends):
                    balls.append(sh)
        rules = self._hybrid_bond_rules()
        st = self._hybrid_state()
        design = (st or {}).get("design")
        ox, oy = (st or {}).get("origin") or (0, 0)
        rows = []
        if design is not None and st.get("cell") == name:
            d2 = json.loads(json.dumps(design))
            for p in d2.get("substratePads", []):
                if str(p.get("id")) == str(pid):
                    p["position"] = {"x": nx - ox, "y": ny - oy}
            rows = [r for r in HR.bond_diagram(d2) if pid in (r["first_bond"], r["second_bond"])]
        else:
            d2 = None
            for sh, k in moved:
                other = sh.points[1 - k]
                end = rebond.get((id(sh), k), (sh.points[k][0] + dx, sh.points[k][1] + dy))
                rows.append({"wire": None, "first_bond": "?", "second_bond": pid,
                             "length_um": round(math.hypot(other[0] - end[0],
                                                           other[1] - end[1])),
                             "first_angle_deg": None, "second_angle_deg": None})
        warnings = []
        for r in rows:
            label = r.get("wire") or "wire"
            if r["length_um"] < rules["min"] or r["length_um"] > rules["max"]:
                warnings.append(f"{label}: length {r['length_um'] / 1000:.2f} mm outside "
                                f"{rules['min'] / 1000:g}-{rules['max'] / 1000:g} mm")
            for ang in (r.get("first_angle_deg"), r.get("second_angle_deg")):
                if ang is not None and ang > rules["angle"] + 1e-9:
                    warnings.append(f"{label}: {ang:g} deg off the die-edge normal "
                                    f"(max {rules['angle']:g})")
        w, h = x1 - x0, y1 - y0
        new_box = box(nx - w / 2.0, ny - h / 2.0, nx + w / 2.0, ny + h / 2.0)
        on_trace = any(isinstance(sh, _Path) and sh.layer == pad.layer and len(sh.points) >= 2
                       and LineString(sh.points).buffer(sh.width / 2.0).intersects(new_box)
                       for sh in cell.shapes)
        out = {"pad": pid, "from": (round(cx), round(cy)), "to": (nx, ny),
               "box": (nx - w / 2.0, ny - h / 2.0, nx + w / 2.0, ny + h / 2.0),
               "old_box": (x0, y0, x1, y1),
               "wires": [{"a": tuple(sh.points[1 - k]),
                          "b": rebond.get((id(sh), k),
                                          (round(sh.points[k][0] + dx), round(sh.points[k][1] + dy)))}
                         for sh, k in moved],
               "rebonded": bool(rebond),
               "rows": rows, "warnings": warnings, "on_trace": on_trace}
        if dry_run:
            return out
        with self._batch():
            pad.points = [(p[0] + dx, p[1] + dy) for p in pad.points]
            shift = {}
            for sh, k in moved:
                pts = list(sh.points)
                old = tuple(pts[k])
                new = rebond.get((id(sh), k), (round(pts[k][0] + dx), round(pts[k][1] + dy)))
                shift[old] = (new[0] - old[0], new[1] - old[1])
                pts[k] = new
                sh.points = pts
            for b in balls:
                bx0, by0, bx1, by1 = b.bbox()
                c = ((bx0 + bx1) / 2.0, (by0 + by1) / 2.0)
                sx, sy = next((v for e, v in shift.items()
                               if abs(c[0] - e[0]) <= 2 and abs(c[1] - e[1]) <= 2), (dx, dy))
                b.points = [(round(p[0] + sx), round(p[1] + sy)) for p in b.points]
            if d2 is not None:
                st = dict(st)              # never mutate the cached parse
                st["design"] = d2
                self._save_hybrid_state(st)
        self._notify()
        return out

    def resistor_at(self, point, cell_name: Optional[str] = None) -> Optional[dict]:
        """The imported printed resistor whose body or pins contain
        ``point``: {"refdes", "centre", "rotation"} or None."""
        st = self._hybrid_state()
        if not st:
            return None
        name = cell_name or self.current_cell_name
        if st.get("cell") != name:
            return None
        ox, oy = st.get("origin") or (0, 0)
        from ..io import hybrid_routed as HR
        px, py = point
        for c in st["design"].get("components", []):
            if c.get("kind", "PRINTED_RESISTOR") != "PRINTED_RESISTOR":
                continue
            cx, cy = HR._pt(c.get("centre"))
            cx, cy = cx + ox, cy + oy
            lx, ly = HR.rotate({"R90": "R270", "R270": "R90"}.get(c.get("rotation"), c.get("rotation")),
                               px - cx, py - cy)
            half_l = max(int(c.get("bodyLengthUm", 0)),
                         2 * max(abs(HR._pt((c.get(k) or {}).get("position"))[0])
                                 + int((c.get(k) or {}).get("widthUm", 0)) / 2.0
                                 for k in ("pinA", "pinB"))) / 2.0
            half_w = max(int(c.get("bodyWidthUm", 0)),
                         max(int((c.get(k) or {}).get("heightUm", 0)) for k in ("pinA", "pinB"))) / 2.0
            if abs(lx) <= half_l and abs(ly) <= half_w:
                return {"refdes": c.get("refdes"), "centre": (cx, cy),
                        "rotation": c.get("rotation", "R0")}
        return None

    def resistor_info(self, refdes: str) -> dict:
        """An imported printed resistor: body length and width (um), its
        squares (L/W), the value from the router file and the sheet
        resistance that implies (value / squares)."""
        from ..io.hybrid_routed import parse_ohms
        st = self._hybrid_state()
        comp = next((c for c in ((st or {}).get("design") or {}).get("components", [])
                     if c.get("refdes") == refdes), None)
        if comp is None:
            raise LayoutError(f"no imported resistor {refdes!r}")
        L, W = int(comp.get("bodyLengthUm", 0)), int(comp.get("bodyWidthUm", 0))
        if L <= 0 or W <= 0:
            raise LayoutError(f"{refdes} has no body size")
        sheet = comp.get("sheetOhms")
        value = parse_ohms(comp.get("value"))
        if sheet is None and value is not None:
            sheet = value / (L / W)
        return {"refdes": refdes, "length_um": L, "width_um": W, "squares": L / W,
                "value_ohms": value, "sheet_ohms": sheet}

    def resize_resistor(self, refdes: str, length: Optional[float] = None,
                        width: Optional[float] = None, dry_run: bool = False) -> dict:
        """Re-size an imported printed resistor's body (um). Its value
        follows the ink's sheet resistance: R = Rs x L / W, with Rs worked
        out from the value the router file gave it, so the readout stays
        consistent across repeated edits. The pins move with the body ends
        (a length change moves each by half the difference -- the traces on
        them then need re-routing; ``pins_moved_um`` says by how much). One
        undo step; ``dry_run`` only reports (the live readout of Edit >
        Resize Resistor)."""
        from ..core.shapes import Rectangle
        from ..io.hybrid_routed import format_ohms
        info = self.resistor_info(refdes)
        L0, W0 = info["length_um"], info["width_um"]
        L = int(round(length)) if length is not None else L0
        W = int(round(width)) if width is not None else W0
        if L <= 0 or W <= 0:
            raise LayoutError("length and width must be positive")
        rs = info["sheet_ohms"]
        r_new = rs * L / W if rs is not None else None
        shift = (L - L0) / 2.0
        out = {"refdes": refdes, "length_um": L, "width_um": W, "squares": L / W,
               "sheet_ohms": rs, "value_ohms": r_new, "was_ohms": info["value_ohms"],
               "pins_moved_um": abs(shift),
               "text": (f"{refdes}: L {L0 / 1000:.3f}->{L / 1000:.3f} mm, W {W / 1000:.3f} mm, "
                        f"{L / W:.2f} sq, R = {format_ohms(r_new)} "
                        f"(was {format_ohms(info['value_ohms'])}, "
                        f"Rs {format_ohms(rs)}/sq)")}
        if dry_run:
            return out
        st = json.loads(json.dumps(self._hybrid_state()))   # a copy: the parse is cached
        comp = next(c for c in st["design"]["components"] if c.get("refdes") == refdes)
        cell = self.library.cells.get("CMP_" + str(refdes))
        if cell is None:
            raise LayoutError(f"part cell CMP_{refdes} not found")
        res_layer = st["layer_map"].get(next((ly.get("name") for ly in
                                              st["design"]["stack"]["layers"]
                                              if ly.get("kind") == "RESISTOR"), ""), None)
        with self._batch():
            for sh in cell.shapes:
                if not isinstance(sh, Rectangle):
                    continue
                if not getattr(sh, "pin", "") and (res_layer is None or sh.layer == res_layer):
                    sh.points = [(-L / 2.0, -W / 2.0), (L / 2.0, -W / 2.0),
                                 (L / 2.0, W / 2.0), (-L / 2.0, W / 2.0)]
                    sh.points = [(round(x), round(y)) for x, y in sh.points]
                elif getattr(sh, "pin", "") in ("1", "2") and shift:
                    x0, _y0, x1, _y1 = sh.bbox()
                    sgn = -1 if (x0 + x1) / 2.0 < 0 else 1
                    sh.points = [(round(x + sgn * shift), y) for x, y in sh.points]
            comp["bodyLengthUm"], comp["bodyWidthUm"] = L, W
            comp["sheetOhms"] = rs
            if r_new is not None:
                comp["value"] = format_ohms(r_new).replace(" ", "")
            for k, sgn in (("pinA", -1), ("pinB", 1)):
                p = comp.get(k) or {}
                pos = p.get("position") or {"x": 0, "y": 0}
                if shift:
                    s = -1 if pos.get("x", 0) < 0 else 1
                    pos["x"] = round(pos.get("x", 0) + s * shift)
                p["position"] = pos
            self._save_hybrid_state(st)
        self._notify()
        return out

    def remove_unused_bonds(self, cell_name: Optional[str] = None, dry_run: bool = False) -> dict:
        """Take the wire bond and bond finger off every die pad the design
        doesn't use, to open up routing room round the die.

        For each labelled die placed in the cell whose part carries its own
        bonds (and fingers): a pad is unused when the design's net names
        give "REF.PIN" no net. That die gets its own copy of the part --
        "<part>_<REF>", e.g. DIE_54AS181_U1, reused if it is already this
        die's own copy -- without those bonds (wire and both balls) and
        fingers; the die pads themselves stay (they're on the chip). Other
        dies using the same part, and the library part, are untouched.
        One undo step. ``dry_run`` only reports. Returns {"dies": {REF:
        [pins removed]}, "cells": {REF: cell now used}, "bonds": n}."""
        import copy as _copy
        from ..core.shapes import Path
        from ..io.techfile import load_bond_rules, load_net_names
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        lib = self.library
        cell = lib.get_cell(name)
        names = load_net_names(lib)
        if not names:
            raise LayoutError("the design has no net names -- import them first: without them "
                              "every pad would look unused")
        br = load_bond_rules(lib)
        blay = int(br.get("layer", -1))
        pad_layers = {int(l) for l in (br.get("die_pad_layers") or [])}
        if blay < 0 or not pad_layers:
            raise LayoutError("set the wire-bond rules (bond layer, die-pad layers) first: Setup > Technology Properties, Wire Bonds tab")
        users = {}
        for r in cell.srefs:
            users.setdefault(r.cell_name, []).append(r)

        def plan(part, ref):
            pads = {}
            for sh in part.shapes:
                if sh.layer in pad_layers and getattr(sh, "pin", "") and hasattr(sh, "bbox"):
                    pads[str(sh.pin)] = sh.bbox()
            unused = {p for p in pads if f"{ref}.{p}" not in names}
            drop_paths, ends = [], []
            for sh in part.shapes:
                if sh.layer != blay or not isinstance(sh, Path) or len(sh.points) < 2:
                    continue
                for e in (sh.points[0], sh.points[-1]):
                    hit = [p for p, (x0, y0, x1, y1) in pads.items()
                           if x0 - 1 <= e[0] <= x1 + 1 and y0 - 1 <= e[1] <= y1 + 1]
                    if hit and hit[0] in unused:
                        drop_paths.append((sh, hit[0]))
                        ends.extend([sh.points[0], sh.points[-1]])
                        break
            pins = sorted({p for _sh, p in drop_paths}, key=lambda v: (len(v), v))
            balls = []
            for sh in part.shapes:
                if sh.layer == blay and not isinstance(sh, Path) and hasattr(sh, "bbox"):
                    x0, y0, x1, y1 = sh.bbox()
                    c = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
                    if any(abs(c[0] - e[0]) <= 2 and abs(c[1] - e[1]) <= 2 for e in ends):
                        balls.append(sh)
            fingers = [sh for sh in part.shapes
                       if getattr(sh, "pin", "") and str(sh.pin) in pins
                       and sh.layer not in pad_layers and sh.layer != blay]
            return pins, [sh for sh, _p in drop_paths] + balls + fingers

        out = {"dies": {}, "cells": {}, "bonds": 0}
        todo = []
        for r in cell.srefs:
            part = lib.cells.get(r.cell_name)
            if not r.label or part is None:
                continue
            if not any(sh.layer == blay and isinstance(sh, Path) for sh in part.shapes):
                continue                       # this part carries no bonds of its own
            pins, gone = plan(part, r.label)
            if not pins:
                continue
            out["dies"][r.label] = pins
            out["bonds"] += len(pins)
            todo.append((r, pins))
        if dry_run or not todo:
            return out
        with self._batch():
            for r, pins in todo:
                own = f"{r.cell_name}_{r.label}" if not r.cell_name.endswith(f"_{r.label}") \
                    else r.cell_name
                shared = len(users.get(r.cell_name, [])) > 1 or own != r.cell_name
                if shared:
                    if own in lib.cells:
                        lib.delete_cell(own) if not any(
                            x.cell_name == own for c in lib.cells.values() for x in c.srefs) else None
                    if own not in lib.cells:
                        copy_ = _copy.deepcopy(lib.cells[r.cell_name])
                        copy_.name = own
                        lib.add_cell(copy_)
                    r.cell_name = own
                part = lib.cells[r.cell_name]
                _pins, gone = plan(part, r.label)
                ids = {id(sh) for sh in gone}
                part.shapes[:] = [sh for sh in part.shapes if id(sh) not in ids]
                out["cells"][r.label] = r.cell_name
        self._notify()
        return out

    def bond_landings(self, cell_name: Optional[str] = None, tol: float = 50.0) -> list:
        """Where the design's wire bonds land on the SUBSTRATE: one
        [x, y, pads] per landing point (bonds stitched on the same spot
        share it), ``pads`` the "REF.PIN" names of the die pads / header
        pins at the bonds' other ends. A bond with both ends on pads
        (die to header pin) has no landing; one with neither is a
        substrate jumper and lands twice, unnamed. Uses Setup > Wire
        Bond Rules (bond layer, die-pad layers)."""
        from ..core.netlist import _Flat, _flatten
        from ..io.techfile import load_bond_rules
        name = cell_name or self.current_cell_name
        cell = self.library.get_cell(name)
        br = load_bond_rules(self.library)
        try:
            blay = int(br["layer"])
        except Exception:
            return []
        if blay < 0:
            return []
        pad_layers = {int(l) for l in (br.get("die_pad_layers") or []) if int(l) >= 0}
        flat = _Flat()
        _flatten(self.library, cell, set(), pad_layers, blay, flat)
        pads = flat.pads
        geoms = [p[0] for p in pads]
        # a pad-layer shape enclosing smaller ones is a die outline, not a pad
        outline = [any(j != i and geoms[j].area < g.area and g.contains(geoms[j].centroid)
                       for j in range(len(geoms))) for i, g in enumerate(geoms)]

        def pad_at(land):
            hit = [i for i, g in enumerate(geoms) if not outline[i] and g.intersects(land)]
            if not hit:
                return None
            i = min(hit, key=lambda k: geoms[k].area)
            names = [f"{p.ref_des}.{p.pin}" for p in pads[i][2] if p.ref_des and p.pin]
            return names[0] if names else ""

        out = []

        def add(xy, pad):
            for L in out:
                if abs(L[0] - xy[0]) <= tol and abs(L[1] - xy[1]) <= tol:
                    if pad and pad not in L[2]:
                        L[2].append(pad)
                    return
            out.append([xy[0], xy[1], [pad] if pad else []])

        for (ga, xa), (gb, xb) in flat.bonds:
            pa, pb = pad_at(ga), pad_at(gb)
            if pa is not None and pb is None:
                add(xb, pa)
            elif pb is not None and pa is None:
                add(xa, pb)
            elif pa is None and pb is None:
                add(xa, "")
                add(xb, "")
        return out

    def _bond_nets(self, landings, names) -> list:
        """[(x, y, net)] for each landing whose pads have exactly one net."""
        out = []
        for x, y, pads in landings:
            nets = {names[p] for p in pads if p in names}
            if len(nets) == 1:
                out.append((x, y, nets.pop()))
        return out

    def bond_header(self, cell_name: Optional[str] = None, pins=None, all_pins: bool = False,
                    layer=None, pad_w: Optional[float] = None, pad_l: Optional[float] = None,
                    bond_inset: Optional[float] = None, edge_margin: Optional[float] = None,
                    width=None) -> dict:
        """Bond the module's header (package) pins to the substrate: for
        each pin a numbered I/O pad on the 1st conductor at the nearest
        substrate edge and a header bond from the pin to it -- e.g. after
        placing or replacing the package. Then assign_io_pads() can
        optimise which net goes on which pin. Pins: ``pins``, else
        ``all_pins``, else those with an IO.<pin> net name. Sizes default
        to the I/O pad rules (I/O Pads tab); the bond layer to a layer
        named for header bonds, else the Wire Bond layer. The dielectric
        fill is redone if there is one. One undo step. Returns
        {"pads", "pins", "layer", "skipped"}. See iopads.bond_header()."""
        from . import iopads
        from ..io.techfile import load_io_pad_rules
        pr = load_io_pad_rules(self.library)
        kw = dict(pad_w=float(pr["width"] if pad_w is None else pad_w),
                  pad_l=float(pr["length"] if pad_l is None else pad_l),
                  bond_inset=float(pr["bond_inset"] if bond_inset is None else bond_inset),
                  edge_margin=float(pr["edge_margin"] if edge_margin is None else edge_margin))
        if kw["pad_w"] <= 0 or kw["pad_l"] <= 0 or kw["bond_inset"] < 0 or kw["edge_margin"] < 0:
            raise LayoutError("I/O pad width and length must be positive, the bond inset and "
                              "edge margin 0 or more")
        with self._batch():
            r = iopads.bond_header(self, cell_name=cell_name, pins=pins, all_pins=all_pins,
                                   layer=layer, width=width, **kw)
            cell = self.library.get_cell(r["cell"])
            diel = dielectric_layer_number(self.library)
            if r["pads"] and diel >= 0 and self._has_full_dielectric(cell, diel):
                try:
                    self.generate_dielectric(cell_name=r["cell"])
                except Exception:
                    pass
        self._notify()
        return r

    def edge_io_pads(self, cell_name: Optional[str] = None, shape=None, n: Optional[int] = None,
                     pitch: float = 2540.0, sides: str = "TB", pad_w: Optional[float] = None,
                     pad_l: Optional[float] = None, edge_margin: Optional[float] = None,
                     start: int = 1, layer=None, align: str = "auto") -> dict:
        """Create and place numbered I/O pads along the substrate's bottom
        and / or top edge (``sides`` "B", "T" or "TB"). The substrate is
        ``shape`` (a selected rectangle / polygon / placed substrate cell)
        or the boundary layer. ``align`` "header" puts a pad at each header
        pin above / below the substrate, numbered as that pin; "pitch" puts
        ``n`` a side (default: as many as fit) at ``pitch`` um, centred,
        numbered DIP style from ``start``; "auto" (default) = header when a
        package is placed. Size and edge margin default to the I/O pad rules
        (I/O Pads tab). Pads with the same numbers are replaced. One undo
        step. See iopads.edge_pads()."""
        from . import iopads
        from ..io.techfile import load_io_pad_rules
        pr = load_io_pad_rules(self.library)
        r = iopads.edge_pads(self, cell_name=cell_name, shape=shape, n=n, pitch=float(pitch),
                             sides=sides,
                             pad_w=float(pr["width"] if pad_w is None else pad_w),
                             pad_l=float(pr["length"] if pad_l is None else pad_l),
                             edge_margin=float(pr["edge_margin"] if edge_margin is None
                                               else edge_margin),
                             start=int(start), layer=layer, align=align)
        self._notify()
        return r

    def fit_bonds(self, cell_name: Optional[str] = None, refs=None, passes: int = 4,
                  length_step: float = 50.0, angle_step: float = 5.0) -> dict:
        """Move each bonded die's bond fingers -- the bond longer or
        shorter between the Wire Bonds tab's minimum and maximum, swung
        within the departure angle -- to where their nets are shortest,
        clear of the die, other fingers, pads and parts, no bond crossing
        another. Auto Place and Fan Out Die Bonds run it themselves.
        ``refs``: just these dies. One undo step. Returns {"fingers",
        "dies": {ref: moved}, "saved_um", "checked"}. See
        hlayout.scripting.bondfit."""
        from .bondfit import fit_bonds
        return fit_bonds(self, cell_name=cell_name, refs=refs, passes=passes,
                         length_step=length_step, angle_step=angle_step)

    def fan_out_die_bonds(self, cell_name: Optional[str] = None, refs=None,
                          all_pads: bool = False, finger_w: Optional[float] = None,
                          finger_l: Optional[float] = None, spacing: Optional[float] = None,
                          gap: Optional[float] = None, layer=None, fit: bool = True) -> dict:
        """Fan out the placed bare dies: a bond finger on the substrate and
        a wire bond to it for every die pad with a design net (all_pads:
        every pad), in the pads' order round the die so no bonds cross,
        within the bond length and departure-angle rules. ``refs``: just
        these dies. Re-running replaces a die's fan-out. Run it after Auto
        Place, before routing. ``fit`` (default): then move the fingers
        within the bond length and angle rules towards their nets
        (fit_bonds); fit=False keeps them in neat rows. One undo step.
        Returns {"dies": {ref: [pins]}, "fingers", "skipped": {pad: why},
        "overlaps", "layer", "fitted"}.
        The dielectric fill is redone if there is one. See
        hlayout.scripting.fanout."""
        from . import fanout
        with self._batch():
            r = fanout.fan_out(self, cell_name=cell_name, refs=refs, all_pads=all_pads,
                               finger_w=finger_w, finger_l=finger_l, spacing=spacing,
                               gap=gap, layer=layer)
            if fit and r["fingers"]:
                from .bondfit import fit_bonds
                r["fitted"] = fit_bonds(self, cell_name=r["cell"], refs=list(r["dies"]))
            diel = dielectric_layer_number(self.library)
            cell = self.library.get_cell(r["cell"])
            if r["fingers"] and diel >= 0 and self._has_full_dielectric(cell, diel):
                try:
                    self.generate_dielectric(cell_name=r["cell"])
                except Exception:
                    pass
        self._notify()
        return r

    def assign_io_pads(self, cell_name: Optional[str] = None, apply: bool = True,
                       csv_path: Optional[str] = None, pad_w: Optional[float] = None,
                       pad_l: Optional[float] = None,
                       bond_inset: Optional[float] = None) -> dict:
        """Optimise the module pinout for routing: put each I/O net on the
        I/O pad (header pin) that gives the shortest, uncrossed connection
        to the copper it must reach, and (``apply``) draw the pads at the
        header-bond landings and rename the nets IO.<pin> to match. The
        pinout is written to ``csv_path`` (default: io_pinout.csv next to
        the design). Pad size and the bond landing's inset from the pad's
        outer end default to the design's I/O pad rules (Setup > Technology
        Properties, I/O Pads tab; techfile.load_io_pad_rules). The
        dielectric fill is redone if there is one. One undo step. Returns the plan: {"sites": [{"pin", "net", "was", ...}],
        "before", "after" (um, pad-to-nearest-copper totals), "unreached",
        "csv"}. See hlayout.scripting.iopads."""
        from . import iopads
        from ..io.techfile import load_io_pad_rules
        pr = load_io_pad_rules(self.library)
        pad_w = float(pr["width"] if pad_w is None else pad_w)
        pad_l = float(pr["length"] if pad_l is None else pad_l)
        bond_inset = float(pr["bond_inset"] if bond_inset is None else bond_inset)
        if pad_w <= 0 or pad_l <= 0 or bond_inset < 0:
            raise LayoutError("I/O pad width and length must be positive, the bond inset 0 or more")
        p = iopads.plan(self, cell_name=cell_name)
        if not apply:
            return p
        if csv_path is None and getattr(self, "current_path", None):
            csv_path = os.path.join(os.path.dirname(self.current_path), "io_pinout.csv")
        with self._batch():
            r = iopads.apply(self, p, pad_w=pad_w, pad_l=pad_l, csv_path=csv_path,
                             bond_inset=bond_inset)
            cell = self.library.get_cell(p["cell"])
            diel = dielectric_layer_number(self.library)
            if diel >= 0 and self._has_full_dielectric(cell, diel):
                try:
                    self.generate_dielectric(cell_name=p["cell"])
                except Exception:
                    pass
        p["csv"] = r["csv"]
        return p

    def header_pinout(self, cell_name: Optional[str] = None) -> list:
        """The module's current header pinout: [{"pin", "net"}] for every
        I/O pad (header pin), in pin order, from the IO.<pin> net names.
        These are the pins assign_io_pads() optimises; swap or set them by
        hand with swap_header_pins() / set_header_pins() to steer routing."""
        from ..io.techfile import load_net_names
        names = load_net_names(self.library)

        def order(pn):
            try:
                return (0, int(pn), "")
            except ValueError:
                return (1, 0, pn)
        pins = set()
        try:
            from .autoplace_session import io_pads
            cell = self.library.get_cell(cell_name or self.current_cell_name)
            pins = {str(pin) for pin, _b in io_pads(self.library, cell, self._boundary_layer())}
        except Exception:
            pass
        pins |= {k[3:] for k in names if k.startswith("IO.")}   # include any assigned pin
        return [{"pin": pn, "net": names.get(f"IO.{pn}", "")}
                for pn in sorted(pins, key=order)]

    def set_header_pins(self, mapping: dict, cell_name: Optional[str] = None) -> int:
        """Put a net on header pins: ``mapping`` {pin: net} (net "" clears the
        pin). Rewrites the IO.<pin> net names so that net routes to that pin's
        I/O pad; the geometry is untouched. Re-run SLI / the router to see the
        new routing. Returns how many pins changed."""
        from ..io.techfile import load_net_names, save_net_names
        names = load_net_names(self.library)
        changed = 0
        for pin, net in (mapping or {}).items():
            k = f"IO.{pin}"
            new = str(net or "").strip()
            if (names.get(k) or "") == new:
                continue
            if new:
                names[k] = new
            else:
                names.pop(k, None)
            changed += 1
        if changed:
            save_net_names(self.library, names)
            self._notify()
        return changed

    def set_resistor_keepout(self, oversize: float, redraw: bool = True) -> dict:
        """Set the printed resistors' dielectric keep-out oversize (Resistor
        Geometry rules, E: how far the dielectric stays off the resistor
        ink) and, with ``redraw``, redraw the keep-out of every resistor
        already in the design to match: its resistive area (the ink less
        where the terminations overlap it) grown by ``oversize``, square
        corners. One undo step. Returns {"oversize", "cells"}."""
        from shapely.geometry import Polygon as SPoly
        from shapely.ops import unary_union
        from ..core.maskops import shape_to_shapely
        from ..core.shapes import Polygon, Rectangle
        from ..io.techfile import load_resistor_geometry_rules, save_resistor_geometry_rules
        e = float(oversize)
        if e < 0:
            raise LayoutError("the keep-out oversize can't be negative")
        rules = load_resistor_geometry_rules(self.library)
        rules["keepout_oversize"] = e
        k_layer = rules.get("keepout_layer")
        inks = {int(v) for v in (rules.get("ink_layers") or {}).values() if isinstance(v, int)}
        if isinstance(rules.get("resistor_layer"), int) and rules["resistor_layer"] >= 0:
            inks.add(rules["resistor_layer"])
        cond = rules.get("conductor_layer")
        done = []
        with self._batch():
            save_resistor_geometry_rules(self.library, rules)
            if redraw and isinstance(k_layer, int) and k_layer >= 0 and inks:
                for name, cell in self.library.cells.items():
                    if not any(sh.layer == k_layer for sh in cell.shapes):
                        continue
                    ink = [shape_to_shapely(sh) for sh in cell.shapes if sh.layer in inks]
                    ink = [g for g in ink if g is not None and not g.is_empty]
                    if not ink:
                        continue                  # a keep-out on its own: not a resistor
                    res = unary_union(ink)
                    pads = [shape_to_shapely(sh) for sh in cell.shapes
                            if sh.layer == cond and getattr(sh, "pin", "")]
                    pads = [g for g in pads if g is not None and not g.is_empty]
                    if pads:
                        res = res.difference(unary_union(pads))
                    if res.is_empty:
                        continue
                    grown = res.buffer(e, join_style=2) if e > 0 else res
                    cell.shapes[:] = [sh for sh in cell.shapes if sh.layer != k_layer]
                    for poly in getattr(grown, "geoms", [grown]):
                        pts = [(int(round(x)), int(round(y))) for x, y in poly.exterior.coords[:-1]]
                        xs = sorted({p[0] for p in pts})
                        ys = sorted({p[1] for p in pts})
                        if len(pts) == 4 and len(xs) == 2 and len(ys) == 2:
                            cell.shapes.append(Rectangle.from_corners(k_layer, xs[0], ys[0],
                                                                      xs[1], ys[1]))
                        else:
                            cell.shapes.append(Polygon(layer=k_layer, points=pts))
                    done.append(name)
        self._notify()
        return {"oversize": e, "cells": done}

    def optimize_io_pins(self, cell_name: Optional[str] = None, locked=(),
                         apply: bool = True) -> dict:
        """Before routing: move the I/O nets to the header pins that shorten
        the SLI -- each net onto the pin nearest the copper it must reach,
        free pins included, no crossing lines (Hungarian method). ``locked``
        pins stay put; ``apply`` renames IO.<pin>, nothing moves on the board.
        Needs no header bonds. Returns {"changes": [{"pin", "was", "net"}],
        "before", "after"} (um). See hlayout.scripting.iopads.optimize_pins."""
        from . import iopads
        return iopads.optimize_pins(self, cell_name=cell_name, locked=locked, apply=apply)

    def backannotate_header(self, schematic: Optional[str] = None, ref: Optional[str] = None,
                            apply: bool = True, no_connect: bool = False) -> dict:
        """Write the header pinout (the IO.<pin> net names IOPINS / Header
        Pinout set) back to the KiCad schematic, so schematic, netlist and
        PCB agree with the layout: header pin N here = pin N of the header
        symbol ``ref``. A pin that changes net has its label renamed; one
        that gains a net gets a wire and label like its neighbours'; one
        that is freed loses them (``no_connect``: and gets a no-connect
        flag). Pins whose wiring is shared with anything else are reported,
        not edited. ``schematic`` defaults to the .kicad_sch beside the last
        imported netlist, ``ref`` to the header in that netlist's cell map.
        ``apply`` False only reports. Close the schematic in KiCad first.
        Returns {"changes", "shared", "schematic", "backup", "ref"}. See
        hlayout.io.kicad_backannotate."""
        from ..io import kicad_backannotate as KB
        from ..io.techfile import load_net_names
        net = (self.autoplace_settings() or {}).get("last_netlist") or ""
        if schematic is None:
            if not net:
                raise LayoutError("no schematic given and no netlist imported to find one by")
            schematic = os.path.splitext(net)[0] + ".kicad_sch"
        if not os.path.exists(schematic):
            raise LayoutError(f"schematic not found: {schematic}")
        if ref is None and net:
            # the header: the unlabelled package placed in the current cell
            cell = self.library.cells.get(self.current_cell_name or "")
            for hc in [r.cell_name for r in (cell.srefs if cell else []) if not r.label]:
                ref = KB.header_ref_from_cellmap(net, hc)
                if ref:
                    break
            if ref is None:
                ref = KB.header_ref_from_cellmap(net)
        if ref is None:
            raise LayoutError("can't tell which symbol is the header -- give ref= (e.g. J30)")
        with open(schematic, encoding="utf-8") as f:
            pins = KB._pin_ends(f.read(), ref)
        names = load_net_names(self.library)

        def real(v):
            return v if v and not v.lower().startswith("unconnected") and not v.startswith("~") \
                else None
        want = {p: real(names.get(f"IO.{p}")) for p in pins}
        try:
            return KB.backannotate_pins(schematic, ref, want, apply=apply, no_connect=no_connect)
        except ValueError as e:
            raise LayoutError(str(e))

    def io_pad_pins(self, cell_name: Optional[str] = None) -> list:
        """Pin numbers of the I/O pads drawn in the cell itself (pinned
        shapes on the cell's own geometry, as the I/O Pad Wizard makes
        them) -- the ones that can be locked as "IO.<pin>"."""
        from .autoplace_session import io_pads
        name = cell_name or self.current_cell_name
        cell = self.library.get_cell(name)
        own = {str(getattr(sh, "pin", "")) for sh in cell.shapes if getattr(sh, "pin", "")}
        try:
            pins = {str(p) for p, _b in io_pads(self.library, cell, self._boundary_layer())}
        except Exception:
            pins = set()
        return sorted(pins & own, key=lambda p: (len(p), p))

    def swap_header_pins(self, a, b, cell_name: Optional[str] = None) -> dict:
        """Swap the nets on two header pins: the signal that entered at pin
        ``a`` now enters at pin ``b`` and vice versa (the IO.<pin> net names
        are exchanged; no geometry moves). Re-run SLI / the router to see the
        new routing. Returns {"a", "b", "net_a", "net_b"} after the swap."""
        from ..io.techfile import load_net_names, save_net_names
        names = load_net_names(self.library)
        ka, kb = f"IO.{a}", f"IO.{b}"
        va, vb = names.get(ka), names.get(kb)
        for k, v in ((ka, vb), (kb, va)):
            if v is None:
                names.pop(k, None)
            else:
                names[k] = v
        save_net_names(self.library, names)
        self._notify()
        return {"a": str(a), "b": str(b), "net_a": vb or "", "net_b": va or ""}
