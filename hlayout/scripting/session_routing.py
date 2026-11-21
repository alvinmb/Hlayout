# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Routing: the hybrid (chip-and-wire) router and its results, Freerouting
(Specctra DSN/SES), the older auto_route, track clean-up (shorten /
jogs), vias (spec, fix, optimise, find, delete) and rip-up / unroute.

Part of hlayout.scripting.api.Session (a mixin): these methods are
Session's own -- call them on a Session as always."""

import json
import math
import os
from typing import Optional

from ..core.errors import LayoutError
from ..core.maskops import layer_to_shapely_flattened
from ..core.shapes import Polygon, Rectangle
from ..io.techfile import (
    dielectric_layer_number,
    load_dielectric_rules,
    load_resistor_geometry_rules,
    load_route_rules,
)


class RoutingMixin:
    # --- Freerouting autorouter (Specctra DSN/SES) ------------------------
    def freerouting_rules(self) -> dict:
        """The design's saved Freerouting rules (techfile
        load_freerouting_rules; defaults: specctra.DEFAULT_RULES)."""
        from ..io.techfile import load_freerouting_rules
        return load_freerouting_rules(self.library)

    def set_freerouting_rules(self, **rules) -> dict:
        """Update and save some of the Freerouting rules (signal_width,
        power_width, clearance, signal_via, signal_via_window, power_via,
        power_via_window, power_nets, exclude_nets, passes, snap_90,
        keep_existing, remove_existing, io_entry_depth, layer_mode,
        bond_finger, finger_layer, die_keepout, keepout_bodies,
        optimize_passes, optimize_threshold, layer_directions,
        against_direction_cost, via_cost, via_optimize, via_optimize_max_flip).
        Returns
        the full set."""
        from ..io.techfile import load_freerouting_rules, save_freerouting_rules
        cur = load_freerouting_rules(self.library)
        cur.update(rules)
        save_freerouting_rules(self.library, cur)
        return load_freerouting_rules(self.library)

    def _route_layers(self):
        rr = load_route_rules(self.library)
        la, lb = rr.get("layer_a"), rr.get("layer_b")
        if not (isinstance(la, int) and la >= 0 and isinstance(lb, int) and lb >= 0):
            raise LayoutError("set the two routing layers first (Setup > Route Layers...)")
        return la, lb

    # --- hybrid router (chip-and-wire) results -----------------------------
    def _hybrid_layer_map(self, design: dict, layers: Optional[dict] = None) -> dict:
        """{hybrid layer name or role: HLayout layer} for importing a routed
        hybrid result. Conductors map in stack order onto Setup's two Route
        Layers; DIELECTRIC onto the dielectric layer; RESISTOR onto the
        resistor body layer. Roles: "substrate", "die", "die_pad", "bond",
        "doc". ``layers`` overrides any entry (numbers or layer names)."""
        from ..io.techfile import load_bond_rules, load_route_rules

        lib = self.library

        def named(*words, default):
            for n in range(len(lib.layers)):
                nm = (lib.layers[n].name or "").lower().replace("_", " ")
                if any(w in nm for w in words):
                    return n
            return default

        rr = load_route_rules(lib)
        la = rr.get("layer_a") if isinstance(rr.get("layer_a"), int) and rr["layer_a"] >= 0 else 3
        lb = rr.get("layer_b") if isinstance(rr.get("layer_b"), int) and rr["layer_b"] >= 0 else 5
        diel = dielectric_layer_number(lib)
        if diel < 0:
            diel = named("dielectric", default=4)
        try:
            rg = load_resistor_geometry_rules(lib)
            res = rg.get("resistor_layer")
            if not (isinstance(res, int) and res >= 0):
                inks = [v for v in (rg.get("ink_layers") or {}).values() if isinstance(v, int)]
                res = inks[0] if inks else 30
        except Exception:
            res = 30
        try:
            br = load_bond_rules(lib)
            bond = int(br.get("layer", -1))
            die_pad = next((int(x) for x in (br.get("die_pad_layers") or []) if int(x) >= 0), -1)
        except Exception:
            bond, die_pad = -1, -1
        if bond < 0:
            bond = named("wire bond", "bond", default=59)
        if die_pad < 0:
            die_pad = named("chip pad", "die pad", default=58)

        out = {
            "substrate": named("substrate", "alumina", "ceramic", default=57),
            "die": named("epoxy", "die attach", default=20),
            "die_pad": die_pad,
            "bond": bond,
            "doc": named("document", "doc", default=61),
        }
        conductors = [ly.get("name") for ly in (design.get("stack") or {}).get("layers", [])
                      if ly.get("kind") == "CONDUCTOR"]
        for i, name in enumerate(conductors):
            out[name] = la if i == 0 else lb
        for ly in (design.get("stack") or {}).get("layers", []):
            if ly.get("kind") == "DIELECTRIC":
                out[ly.get("name")] = diel
            elif ly.get("kind") == "RESISTOR":
                out[ly.get("name")] = res
        for k, v in (layers or {}).items():
            out[k] = lib.layers.resolve(v)
        return out

    def import_routed_hybrid(self, path: str, cell_name: Optional[str] = None, *,
                             layers: Optional[dict] = None, origin=(0, 0),
                             overwrite: bool = True, dielectric_fill: bool = True) -> dict:
        """Bring a chip-and-wire router result (``*.routed.json`` from
        HybridRouterCli) into the design as one undo step, in its own cell
        (default: the design's name). Router micrometres are DBU, offset by
        ``origin``:

        - the substrate outline;
        - each die as a placed cell (body on the die/epoxy layer, numbered
          pads on the die-pad layer), labelled with its refdes;
        - each two-pin component as a placed cell (pins 1 and 2 = the
          router's A and B on their conductor, body on the resistor layer
          for a printed resistor, the documentation layer for a chip);
        - every substrate pad (bond fingers, jumper lands, external pads) as
          a numbered pad;
        - routed traces as round-capped paths, each dielectric crossover as
          a pad on both conductors plus its window on the dielectric, then
          the dielectric fill (``dielectric_fill``);
        - every wire bond, jumpers included, as a path on the bond layer at
          the wire diameter.

        Pad nets become the design's net names; the bond diagram is kept
        with the design for generate_bond_table() and the assembly drawing.
        ``layers`` overrides the layer mapping (see _hybrid_layer_map()).
        Returns counts, the router's stats and its unrouted connections."""
        from ..io import hybrid_routed as HR
        from ..io.techfile import load_net_names

        data = HR.load_routed(path)
        design = data["design"]
        L = self._hybrid_layer_map(design, layers)
        lib = self.library
        ox, oy = int(origin[0]), int(origin[1])
        name = (cell_name or design.get("name") or "HYBRID").strip().replace(" ", "_")

        def lay(layer_name):
            if layer_name not in L:
                raise LayoutError(f"no HLayout layer for hybrid layer {layer_name!r} -- "
                                  f"pass layers={{'{layer_name}': <layer>}}")
            return L[layer_name]

        def rect(cell, layer, cx, cy, w, h, pin=""):
            cell.add_rectangle(layer, round(cx - w / 2), round(cy - h / 2),
                               round(cx + w / 2), round(cy + h / 2), pin=pin)

        def fresh(cname):
            if cname in lib.cells:
                if not overwrite:
                    raise LayoutError(f"cell {cname!r} already exists; pass overwrite=True")
                lib.delete_cell(cname)
            return lib.new_cell(cname)

        stats = {"dies": 0, "components": 0, "pads": 0, "fingers": 0, "jumper_lands": 0,
                 "traces": 0, "windows": 0, "wires": 0, "jumpers": 0}
        nets = {}
        with self._batch():
            if name in lib.cells:
                if not overwrite:
                    raise LayoutError(f"cell {name!r} already exists; pass overwrite=True")
                lib.delete_cell(name)
            sub = design.get("substrate") or {}
            sw, sh = int(sub.get("widthUm", 0)), int(sub.get("heightUm", 0))

            # parts first, so the design cell can place them
            die_cells = {}
            for d in design.get("dies", []):
                cname = "DIE_" + str(d.get("partName") or d.get("refdes")).replace(" ", "_")
                if cname not in die_cells.values():
                    c = fresh(cname)
                    w, h = int(d.get("widthUm", 0)), int(d.get("heightUm", 0))
                    c.add_rectangle(L["die"], 0, 0, w, h)
                    for p in d.get("pads", []):
                        px, py = HR._pt(p.get("position"))
                        rect(c, L["die_pad"], px, py, int(p.get("widthUm", 0)),
                             int(p.get("heightUm", 0)), pin=str(p.get("id")))
                die_cells[d.get("refdes")] = cname
            comp_cells = {}
            for comp in design.get("components", []):
                cname = "CMP_" + str(comp.get("refdes"))
                c = fresh(cname)
                bl, bw = int(comp.get("bodyLengthUm", 0)), int(comp.get("bodyWidthUm", 0))
                body = lay(next((ly.get("name") for ly in design["stack"]["layers"]
                                 if ly.get("kind") == "RESISTOR"), "doc")) \
                    if comp.get("kind", "PRINTED_RESISTOR") == "PRINTED_RESISTOR" else L["doc"]
                if bl > 0 and bw > 0:
                    rect(c, body, 0, 0, bl, bw)
                for pin_no, key in (("1", "pinA"), ("2", "pinB")):
                    p = comp.get(key) or {}
                    px, py = HR._pt(p.get("position"))
                    rect(c, lay(p.get("layer")), px, py, int(p.get("widthUm", 0)),
                         int(p.get("heightUm", 0)), pin=pin_no)
                    if p.get("net"):
                        nets[f"{comp.get('refdes')}.{pin_no}"] = p["net"]
                comp_cells[comp.get("refdes")] = cname

            cell = lib.new_cell(name)
            if sw > 0 and sh > 0:
                cell.add_rectangle(L["substrate"], ox, oy, ox + sw, oy + sh)
            for d in design.get("dies", []):
                dx, dy = HR._pt(d.get("origin"))
                self._place_in(cell, die_cells[d.get("refdes")], (ox + dx, oy + dy),
                               HR.rotation_degrees(d.get("rotation")), str(d.get("refdes")))
                for p in d.get("pads", []):
                    if p.get("net"):
                        nets[f"{d.get('refdes')}.{p.get('id')}"] = p["net"]
                stats["dies"] += 1
            for comp in design.get("components", []):
                cx, cy = HR._pt(comp.get("centre"))
                self._place_in(cell, comp_cells[comp.get("refdes")], (ox + cx, oy + cy),
                               HR.rotation_degrees(comp.get("rotation")), str(comp.get("refdes")))
                stats["components"] += 1
            for p in design.get("substratePads", []):
                px, py = HR._pt(p.get("position"))
                rect(cell, lay(p.get("layer")), ox + px, oy + py, int(p.get("widthUm", 0)),
                     int(p.get("heightUm", 0)), pin=str(p.get("id")))
                if p.get("net"):
                    nets[f"IO.{p.get('id')}"] = p["net"]
                stats["pads"] += 1
                role = p.get("role", "BOND_FINGER")
                if role == "BOND_FINGER":
                    stats["fingers"] += 1
                elif role == "JUMPER_LAND":
                    stats["jumper_lands"] += 1
            for t in data["traces"]:
                pts = [(ox + x, oy + y) for x, y in (HR._pt(q) for q in t.get("points", []))]
                pts = [q for i, q in enumerate(pts) if i == 0 or q != pts[i - 1]]
                if len(pts) >= 2:
                    cell.add_path(lay(t.get("layer")), pts, int(t.get("widthUm", 0)), cap="round")
                    stats["traces"] += 1
            diel_layers = {ly.get("name") for ly in (design.get("stack") or {}).get("layers", [])
                           if ly.get("kind") == "DIELECTRIC"}
            diel = L[next(iter(diel_layers))] if diel_layers else None
            for wdw in data["windows"]:
                px, py = HR._pt(wdw.get("position"))
                pad = int(wdw.get("padUm", 0))
                for key in ("fromLayer", "toLayer"):
                    rect(cell, lay(wdw.get(key)), ox + px, oy + py, pad, pad)
                if diel is not None:
                    o = int(wdw.get("openingUm", 0))
                    rect(cell, diel, ox + px, oy + py, o, o)
                stats["windows"] += 1
            if diel is not None and sw > 0 and sh > 0:
                edge = int(((design.get("rules") or {}).get("conductor") or {})
                           .get("edgeClearanceUm") or 250)
                cell.add_rectangle(diel, ox + edge, oy + edge, ox + sw - edge, oy + sh - edge)
            for w in design.get("wires", []):
                a = HR.endpoint_position(design, w.get("from"))
                b = HR.endpoint_position(design, w.get("to"))
                if a is None or b is None or a == b:
                    continue
                dia = int((w.get("spec") or {}).get("diameterUm", 25))
                cell.add_path(L["bond"], [(ox + a[0], oy + a[1]), (ox + b[0], oy + b[1])],
                              dia, cap="round")
                stats["wires"] += 1
                if HR.wire_purpose(w) == "JUMPER":
                    stats["jumpers"] += 1

            rows = HR.bond_diagram(design)
            lib.tech_params["bond_diagram"] = json.dumps(rows)
            lib.tech_params["hybrid_design"] = json.dumps(
                {"cell": name, "design": design, "layer_map": L, "origin": [ox, oy]})
            self.current_cell_name = name
            if nets:
                merged = dict(load_net_names(lib))
                merged.update(nets)
                self.set_net_names(merged)
            if diel is not None and dielectric_fill:
                try:
                    self.generate_dielectric(cell_name=name)
                except Exception:
                    pass
        self._notify()
        stats.update(cell=name, bond_rows=len(rows), router_stats=data.get("stats") or {},
                     unrouted=data.get("unrouted") or [],
                     issues=len(data.get("issues") or []), layer_map=L)
        return stats

    def _place_in(self, cell, child: str, origin, rotation: float, label: str):
        """Place ``child`` in ``cell`` (not necessarily the current cell)."""
        from ..core.shapes import SRef
        cell.srefs.append(SRef(cell_name=child, origin=(int(origin[0]), int(origin[1])),
                               rotation=float(rotation), label=label))

    def _single_layer_route(self, R) -> bool:
        """Route on the 1st conductor alone? rules layer_mode "single" /
        "two", or "auto": yes when nothing anywhere is drawn on the 2nd
        conductor (a one-conductor chip-and-wire hybrid)."""
        mode = str(R.get("layer_mode", "auto")).lower()
        if mode.startswith("s") or mode == "1":
            return True
        if mode.startswith("t") or mode == "2":
            return False
        if self._fingers_on_second(R):
            return False            # bond fingers on the 2nd conductor need both
        _la, lb = self._route_layers()
        return not any(sh.layer == lb for c in self.library.cells.values() for sh in c.shapes)

    @staticmethod
    def _fingers_on_second(R) -> bool:
        """Rules finger_layer asks for the bond fingers on the 2nd conductor?"""
        v = str(R.get("finger_layer", "first")).strip().lower()
        return v.startswith("s") or v in ("2", "b", "top", "layer_b")

    def _finger_layer(self, R) -> int:
        """The conductor the chip-and-wire bond fingers go on: the 2nd
        (top) conductor when rules finger_layer is "second" and the route
        uses both layers, else the 1st."""
        la, lb = self._route_layers()
        return lb if self._fingers_on_second(R) and not self._single_layer_route(R) else la

    def _route_keepouts(self, cell, layers, R) -> list:
        """[(layer, [(x, y), ...])] areas no track may cross: every bare
        die placed in ``cell`` (a part with shapes on a Wire Bond Rules
        die-pad layer, grown by die_keepout -- an enclosing header
        package spanning most of the board excepted) and, with
        keepout_bodies, the body of every part with two or more pads on
        the routing layers (the hull of its pads and any resistor ink /
        glaze, less the pads grown by the clearance). A printed resistor's
        ink and glaze are ALWAYS kept out -- keepout_bodies or not -- less
        its own terminations grown by the clearance: no track may run
        through or under a resistor, on either conductor."""
        from shapely.geometry import MultiPolygon
        from shapely.ops import unary_union
        from ..core.maskops import (_compose_transforms, _sref_point_transform,
                                    _transform_geom, shape_to_shapely)
        from ..io.techfile import load_bond_rules
        lib = self.library
        try:
            br = load_bond_rules(lib)
            pad_layers = {int(l) for l in (br.get("die_pad_layers") or [])}
            bond_layers = {int(br["layer"])}
        except Exception:
            pad_layers, bond_layers = set(), set()
        ink = set()
        try:
            rg = load_resistor_geometry_rules(lib)
            ink |= {int(v) for v in (rg.get("ink_layers") or {}).values()}
            for k in ("resistor_layer", "glaze_layer"):
                if isinstance(rg.get(k), int) and rg[k] >= 0:
                    ink.add(rg[k])
        except Exception:
            pass
        route = set(int(l) for l in layers)
        bx = lib.cell_bbox(cell.name)
        board = max((bx[2] - bx[0]) * (bx[3] - bx[1]), 1) if bx else 1
        gap = float(R.get("clearance", 100))
        die_m = float(R.get("die_keepout", 200))
        try:
            _sb = self._substrate_box(cell)
            sub_area = _sb.area if _sb is not None else 0.0
        except Exception:
            sub_area = 0.0
        out = []

        def collect(name, tr, acc, depth=0):
            c = lib.cells.get(name)
            if c is None or depth > 32:
                return
            for sh in c.shapes:
                g = shape_to_shapely(sh)
                if g is None or g.is_empty:
                    continue
                g = _transform_geom(g, tr) if tr is not None else g
                acc.append((sh.layer, bool(getattr(sh, "pin", "")), g))
            for r in c.srefs:
                collect(r.cell_name, _compose_transforms(tr, _sref_point_transform(r)), acc, depth + 1)

        # a die part may carry its own wire bonds: they're not its body
        # (its keep-out would swallow the bond landings the tracks must reach)

        def polys(g):
            if g.is_empty:
                return []
            return list(g.geoms) if isinstance(g, MultiPolygon) else \
                [p for p in getattr(g, "geoms", [g]) if p.geom_type == "Polygon"]

        own = [(sh.layer, bool(getattr(sh, "pin", "")), shape_to_shapely(sh))
               for sh in cell.shapes if sh.layer in ink or getattr(sh, "pin", "")]
        own_res = [g for l, _p, g in own if l in ink and g is not None and not g.is_empty]
        if own_res:
            region = unary_union(own_res)
            pins_c = [g for l, p, g in own if p and l in route and g is not None]
            if pins_c:
                region = region.difference(unary_union(pins_c).buffer(gap, join_style=2))
            for p in polys(region):
                if p.area > 1.0:
                    pts = [(round(x), round(y)) for x, y in list(p.exterior.coords)[:-1]]
                    for lay in layers:
                        out.append((lay, pts))
        for r in cell.srefs:
            acc = []
            collect(r.cell_name, _sref_point_transform(r), acc)
            acc = [a for a in acc if a[0] not in bond_layers]
            if not acc:
                continue
            allg = unary_union([g for _l, _p, g in acc])
            b = allg.bounds
            area = (b[2] - b[0]) * (b[3] - b[1])
            # the enclosing package / header / substrate: judged against the
            # substrate when there is one (a part left off the board must not
            # inflate the cell's extent and turn the substrate into a body)
            if area > 0.4 * board or (sub_area and area >= 0.8 * sub_area):
                continue
            shapes = []
            # a printed resistor (ink / glaze): never routed through or under
            res = [g for l, _p, g in acc if l in ink]
            if res and not any(l in pad_layers for l, _p, _g in acc):
                region = unary_union(res)
                pins_r = [g for l, p, g in acc if p and l in route]
                if pins_r:
                    region = region.difference(unary_union(pins_r).buffer(gap, join_style=2))
                shapes.extend(p for p in polys(region) if p.area > 1.0)
            # a bare die: pads on a die-pad layer and no PINS on the routing
            # layers (plain copper there -- a die-attach pad -- is part of it)
            if any(l in pad_layers for l, _p, _g in acc):
                # the die itself -- not the numbered bond fingers a die part
                # may carry round it, which are where the tracks must go
                body = [g for l, p, g in acc if not (p and l in route)]
                if body:
                    ko = unary_union(body).envelope.buffer(die_m, join_style=2)
                    # a backside contact (a diode's cathode, a transistor's
                    # collector: the numbered attach pad under the die) is
                    # where its net's track must reach -- keep out of the
                    # rest only
                    own = [g for l, p, g in acc if p and l in route]
                    if own:
                        ko = ko.difference(unary_union(own).buffer(gap, join_style=2))
                    shapes += [p for p in polys(ko) if p.area > 1.0]
            elif R.get("keepout_bodies", True):
                pins = [g for l, p, g in acc if p and l in route]
                if len(pins) >= 2:
                    body = unary_union(pins + [g for l, _p, g in acc if l in ink]).convex_hull
                    rest = body.difference(unary_union(pins).buffer(gap, join_style=2))
                    shapes += [p for p in polys(rest) if p.area > 0.02 * body.area]
            for g in shapes:
                for p in polys(g):
                    pts = [(round(x), round(y)) for x, y in list(p.exterior.coords)[:-1]]
                    for lay in layers:
                        out.append((lay, pts))
        return out

    def resistor_crossings(self, cell_name: Optional[str] = None) -> list:
        """Tracks that run through or under a printed resistor -- copper on
        a route layer crossing a resistor's ink / glaze, its own
        terminations excepted (the router keeps out of these areas; this
        finds tracks drawn by hand or routed before). Returns
        [{"ref", "layer", "at": (x, y)}], one per track and resistor."""
        from shapely.geometry import Polygon as SPoly
        from ..core.maskops import shape_to_shapely
        from ..core.shapes import Path as _P
        name = cell_name or self.current_cell_name
        cell = self.library.get_cell(name)
        la, lb = self._route_layers()
        R = self.freerouting_rules()
        R = dict(R, keepout_bodies=False, die_keepout=0)
        kos = [(lay, SPoly(pts)) for lay, pts in self._route_keepouts(cell, (la, lb), R)]
        kos = [(lay, g) for lay, g in kos if g.is_valid and g.area > 0]
        refs = []
        for r in cell.srefs:
            b = self.library.cell_bbox(r.cell_name)
            if b:
                from ..core.library import _transform_bbox
                if r.label:
                    refs.append((r.label, _transform_bbox(b, r)))
        out = []
        for sh in cell.shapes:
            if sh.layer not in (la, lb) or getattr(sh, "pin", "") or not isinstance(sh, _P):
                continue
            g = shape_to_shapely(sh)
            if g is None:
                continue
            for lay, k in kos:
                if lay != sh.layer or not g.intersects(k):
                    continue
                hit = g.intersection(k)
                if hit.area < 1.0:
                    continue
                c = hit.representative_point()
                inside = [(abs((b[2] - b[0]) * (b[3] - b[1])), n) for n, b in refs
                          if b[0] <= c.x <= b[2] and b[1] <= c.y <= b[3]]
                ref = min(inside)[1] if inside else "?"
                out.append({"ref": ref, "layer": sh.layer, "at": (round(c.x), round(c.y))})
        return out

    def _boundary_bbox(self, cell, layer):
        """Bounding box of ``layer``'s geometry in ``cell``, placed
        instances included (a substrate drawn as its own placed cell), or
        None."""
        from ..core.maskops import layer_to_shapely_flattened
        try:
            g = layer_to_shapely_flattened(self.library, cell, layer)
        except Exception:
            return None
        if g is None or g.is_empty:
            return None
        return tuple(float(v) for v in g.bounds)

    def export_dsn(self, path: str, cell_name: Optional[str] = None, **rules):
        """Write the current (or named) cell as a Specctra DSN for
        Freerouting: the two Route Layers, every numbered pad, the
        design's net names (set_net_names()/import_net_names()), the
        saved Freerouting rules (overridden by ``rules``) and the
        existing tracks/vias. Returns hlayout.io.specctra.DsnSummary."""
        from ..io.specctra import export_dsn
        from ..io.techfile import load_net_names
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        names = load_net_names(self.library)
        if not names:
            raise LayoutError("the design has no net names -- import them first "
                              "(NETNAMES IMPORT <file.csv> / Session.import_net_names())")
        R = self.freerouting_rules()
        R.update(rules)
        R = self._rules_with_drc(R)
        # the router keeps other nets clear of a via's whole dielectric
        # window, not just its pad
        R["signal_via_keep"] = max(self.via_spec(False, R)["window"], float(R["signal_via"]))
        R["power_via_keep"] = max(self.via_spec(True, R)["window"], float(R["power_via"]))
        diel = dielectric_layer_number(self.library)
        band = None
        if diel >= 0:
            # Off the printed dielectric the two conductors aren't insulated
            # (a crossing would short) and a via has nothing to be a window
            # in: close the band outside the dielectric's boundary (its
            # largest datatype-0 shape) to the 2nd conductor, leaving it to
            # the 1st -- the layer the edge I/O pads are reached on.
            ins = [sh for sh in self.library.get_cell(name).shapes
                   if sh.layer == diel and getattr(sh, "datatype", 0) == 0 and hasattr(sh, "bbox")]
            if ins:
                b = max(ins, key=lambda sh: (sh.bbox()[2] - sh.bbox()[0]) * (sh.bbox()[3] - sh.bbox()[1]))
                band = (b.bbox(), self._route_layers()[1])
            # no boundary drawn (the biggest datatype-0 shape is then just a
            # via window): use the one generate_dielectric() works out
            ring = self._io_ring_box(self.library.get_cell(name))
            if ring is not None:
                rb = ring.bounds
                if band is None or (band[0][2] - band[0][0]) * (band[0][3] - band[0][1]) < \
                        0.5 * (rb[2] - rb[0]) * (rb[3] - rb[1]):
                    band = (rb, self._route_layers()[1])
        la, lb = self._route_layers()
        single = self._single_layer_route(R)
        cell = self.library.get_cell(name)
        # chip-and-wire: the substrate end of every wire bond is a bond
        # finger the tracks must reach, on the net of the die pad /
        # header pin at the bond's other end
        names = dict(names)
        finger = float(R.get("bond_finger", 400) or 0)
        flayer = self._finger_layer(R)
        bond_pins = []
        if finger > 0:
            # a die part that carries its own numbered bond fingers already
            # gives the router a pad at those landings: no second pin there
            from shapely.geometry import Point as _Pt, Polygon as _Poly
            from ..io.specctra import _collect_pins
            own = [_Poly(pts) for _r, _p, _l, pts in
                   _collect_pins(self.library, cell, {la, lb}) if len(pts) >= 3]
            k = 0
            # a finger drawn as copper (Fan Out Die Bonds, or by hand) is
            # the pin as it is -- not a bond_finger square that may reach
            # its neighbour
            drawn = [sh.bbox() for sh in cell.shapes
                     if sh.layer == flayer and not getattr(sh, "pin", "")
                     and type(sh).__name__ in ("Rectangle", "Polygon")]
            for x, y, net in self._bond_nets(self.bond_landings(name), names):
                if any(g.covers(_Pt(x, y)) for g in own):
                    continue
                k += 1
                hit = [b for b in drawn if b[0] <= x <= b[2] and b[1] <= y <= b[3]
                       and (b[2] - b[0]) * (b[3] - b[1]) <= 4 * finger * finger]
                if hit:
                    b = min(hit, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
                    bond_pins.append((f"B{k}", (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0,
                                      finger, flayer, (b[2] - b[0], b[3] - b[1])))
                else:
                    bond_pins.append((f"B{k}", x, y, finger, flayer))
                names[f"BOND.B{k}"] = net
        keepouts = self._route_keepouts(cell, (la,) if single else (la, lb), R)
        return export_dsn(self.library, name, path, layers=(la, lb), rules=R,
                          net_names=names, boundary_layer=self._boundary_layer(),
                          boundary_bbox=(self._boundary_bbox(cell, self._boundary_layer())
                                         if self._boundary_layer() is not None else None),
                          dielectric_layer=diel if (diel >= 0 and not single) else None,
                          edge_band=None if single else band,
                          write_wiring=not R.get("remove_existing", False),
                          bond_pins=bond_pins, keepouts=keepouts, single_layer=single)

    def import_ses(self, path: str, cell_name: Optional[str] = None, **rules) -> dict:
        """Bring a Freerouting session file's routing into the cell as one
        undo step: each wire becomes a round-capped Path on its conductor
        layer; each via a drop_via() with its dielectric window (signal or
        power size from the rules); the dielectric fill is regenerated.

        With rules keep_existing=True (the default) wires/vias that are
        already in the design are skipped; with keep_existing=False every
        existing track and via on the two routing layers is replaced by
        the session's routing. Returns counts, plus the net-completion
        check: {"wires", "vias", "skipped", "nets", "complete",
        "unrouted": [net names]}."""
        from shapely.geometry import LineString, Point
        from shapely.ops import unary_union
        from ..core.shapes import Path as _Path
        from ..io.specctra import VIA_PWR, find_vias, layer_name, parse_ses
        from ..io.techfile import load_net_names

        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        cell = self.library.get_cell(name)
        la, lb = self._route_layers()
        R = self.freerouting_rules()
        R.update(rules)
        R = self._rules_with_drc(R)
        power = {n.upper() for n in R.get("power_nets") or []}
        ses = parse_ses(path)
        lmap = {layer_name(la): la, layer_name(lb): lb}
        diel = dielectric_layer_number(self.library)

        keep = bool(R.get("keep_existing", True)) and not R.get("remove_existing", False)
        stats = {"wires": 0, "vias": 0, "skipped": 0}
        with self._batch():
            if not keep:
                # replace: drop existing routing paths and drop_via() vias --
                # and the routing's leftovers: a via pad left on one layer
                # only (its other half gone), or a bond finger this import
                # lays again, which no longer pair up as a via
                sites = {(x, y) for x, y, _w in find_vias(cell, (la, lb))}
                route_sizes = {round(float(R.get(k) or 0)) for k in
                               ("signal_via", "power_via", "bond_finger")} - {0}
                def _is_via_part(s):
                    if s.layer not in (la, lb, diel) or getattr(s, "pin", "") or not hasattr(s, "bbox"):
                        return False
                    x0, y0, x1, y1 = s.bbox()
                    c = (round((x0 + x1) / 2), round((y0 + y1) / 2))
                    if c in sites and (x1 - x0) < 5000 and getattr(s, "datatype", 0) == 0:
                        return True
                    w, h = x1 - x0, y1 - y0
                    return (s.layer in (la, lb) and isinstance(s, Rectangle) and abs(w - h) <= 2
                            and round(w) in route_sizes)
                cell.shapes[:] = [s for s in cell.shapes
                                  if not ((isinstance(s, _Path) and s.layer in (la, lb))
                                          or _is_via_part(s))]
                # placed via-cell instances go too
                from ..io.specctra import via_cell_sites
                gone = {id(r) for _x, _y, _w, r in via_cell_sites(self.library, cell, (la, lb))}
                cell.srefs[:] = [r for r in cell.srefs if id(r) not in gone]
            existing = {}
            for lay in (la, lb):
                geoms = []
                for s in cell.shapes:
                    if s.layer != lay:
                        continue
                    if isinstance(s, _Path) and len(s.points) >= 2:
                        geoms.append(LineString(s.points).buffer(s.width / 2.0))
                existing[lay] = unary_union(geoms).buffer(2) if geoms else None
            have_vias = {(x, y) for x, y, _w in find_vias(cell, (la, lb))}

            for _net, lay_name, width, pts in ses.wires:
                lay = lmap.get(lay_name)
                if lay is None:
                    stats["skipped"] += 1
                    continue
                pts = [(int(round(x)), int(round(y))) for x, y in pts]
                pts = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
                if len(pts) < 2:
                    continue
                w = int(round(width))
                # Freerouting necks a track down where it squeezes into a
                # pad; the design's minimum width still applies, so put it
                # back (Tools > DRC then shows any clearance it costs)
                cls_w = int(round(R["power_width"] if str(_net).upper() in power
                                  else R["signal_width"]))
                if w < cls_w:
                    w = cls_w
                    stats["widened"] = stats.get("widened", 0) + 1
                if keep and existing[lay] is not None and \
                        existing[lay].contains(LineString(pts).buffer(max(w / 2.0 - 3, 1))):
                    stats["skipped"] += 1
                    continue
                cell.add_path(lay, pts, w, cap="round")
                stats["wires"] += 1
            for _net, ps, x, y in ses.vias:
                x, y = int(round(x)), int(round(y))
                if keep and any(abs(x - vx) <= 2 and abs(y - vy) <= 2 for vx, vy in have_vias):
                    stats["skipped"] += 1
                    continue
                pwr = ps == VIA_PWR
                spec = self.via_spec(pwr, R)
                size, window = spec["pad"], spec["window"]
                h = size / 2.0
                cell.add_rectangle(la, x - h, y - h, x + h, y + h)
                cell.add_rectangle(lb, x - h, y - h, x + h, y + h)
                if diel >= 0 and window:
                    hw = window / 2.0
                    cell.add_rectangle(diel, x - hw, y - hw, x + hw, y + hw)
                have_vias.add((x, y))
                stats["vias"] += 1
            # chip-and-wire: lay a bond finger wherever a wire bond lands
            # on the substrate and the copper doesn't already give it one
            finger = float(R.get("bond_finger", 400) or 0)
            stats["fingers"] = 0
            if finger > 0:
                from shapely.geometry import box as _box
                from ..core.maskops import layer_to_shapely_flattened
                flayer = self._finger_layer(R)
                cu = layer_to_shapely_flattened(self.library, cell, flayer)
                h = finger / 2.0
                # a die part carrying its own numbered bond fingers already
                # has one at its landings, whatever its size
                from shapely.geometry import Point as _Pt, Polygon as _Poly
                from ..io.specctra import _collect_pins
                own = [_Poly(pts) for _r, _p, _l, pts in
                       _collect_pins(self.library, cell, {la, lb}) if len(pts) >= 3]
                # a finger already drawn there (Fan Out Die Bonds, or by hand)
                drawn = [sh.bbox() for sh in cell.shapes
                         if sh.layer == flayer and not getattr(sh, "pin", "")
                         and type(sh).__name__ in ("Rectangle", "Polygon")
                         and (sh.bbox()[2] - sh.bbox()[0]) * (sh.bbox()[3] - sh.bbox()[1])
                         <= 4 * finger * finger]
                for x, y, _net in self._bond_nets(self.bond_landings(name), load_net_names(self.library)):
                    sq = _box(x - h, y - h, x + h, y + h)
                    if cu is not None and not cu.is_empty and cu.buffer(1).contains(sq):
                        continue
                    if any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in drawn):
                        continue
                    if any(g.covers(_Pt(x, y)) for g in own):
                        continue
                    cell.add_rectangle(flayer, round(x - h), round(y - h), round(x + h), round(y + h))
                    stats["fingers"] += 1
            if diel >= 0 and not self._single_layer_route(R) and \
                    any(s.layer == diel and getattr(s, "datatype", 0) == 0 for s in cell.shapes):
                try:
                    self.generate_dielectric(cell_name=name)
                except Exception:
                    pass
        self._notify()

        # completion check, stack-aware (vias join the layers, nothing else)
        names = load_net_names(self.library)
        tracer = self.net_tracer(cell_name=name)
        by_net, count = {}, {}
        for pad, net in names.items():
            if pad in tracer.pin_node:
                by_net.setdefault(net, set()).add(tracer.dsu.find(tracer.pin_node[pad]))
                count[net] = count.get(net, 0) + 1
        # wire-bond landings count as pins of their net: a die pad is only
        # reached through the copper under its bond's substrate end
        for x, y, net in self._bond_nets(self.bond_landings(name), names):
            node = tracer.node_at(x, y, prefer_layer=self._finger_layer(R),
                                  tol=float(R.get("bond_finger", 400)) / 2.0)
            by_net.setdefault(net, set()).add(tracer.dsu.find(node) if node is not None
                                              else ("open", x, y))
            count[net] = count.get(net, 0) + 1
        multi = {n: r for n, r in by_net.items() if count.get(n, 0) > 1}
        unrouted = sorted(n for n, r in multi.items() if len(r) > 1)
        stats.update(nets=len(multi), complete=len(multi) - len(unrouted), unrouted=unrouted)
        return stats

    def freeroute(self, work_dir: Optional[str] = None, cell_name: Optional[str] = None,
                  router: Optional[str] = None, timeout: Optional[float] = None,
                  on_line=None, cancel=None, **rules) -> dict:
        """Export a DSN, run Freerouting headless on it, import the
        result (import_ses()). ``router`` = the Freerouting launcher or
        .jar (default: Setup > Freerouting..., else a standard install
        location). Files go to ``work_dir`` (default: a ``freerouting``
        folder next to the design, else the temp folder) as
        <cell>.dsn/.ses. Blocks until done -- the GUI runs the router
        itself on a worker thread (see run_freerouting()).

        Two-layer routes follow the rules' ``layer_directions`` (each
        conductor kept to one track direction). With ``via_optimize`` the
        imported routing then gets optimize_vias() -- which takes out
        every via it safely can -- as part of the same Autoroute.
        Returns import_ses()'s dict plus "dsn", "ses" and "log" (last
        output), and with the via pass "vias_before" / "vias_after"."""
        import tempfile
        from ..io import app_settings
        from ..io.specctra import find_freerouting, run_freerouting
        name = cell_name or self.current_cell_name
        exe = find_freerouting(router or app_settings.get_setting(app_settings.FREEROUTING_PATH_KEY))
        if not exe:
            raise LayoutError("Freerouting not found -- install it (freerouting.app) and set "
                              "its path in Setup > Freerouting...")
        work_dir = work_dir or os.path.join(tempfile.gettempdir(), "hlayout_freerouting")
        os.makedirs(work_dir, exist_ok=True)
        dsn = os.path.join(work_dir, f"{name}.dsn")
        ses = os.path.join(work_dir, f"{name}.ses")
        R = self.freerouting_rules()
        R.update(rules)
        self.export_dsn(dsn, cell_name=name, **R)
        cmd = self.freeroute_command(exe, dsn, ses, R,
                                     java=app_settings.get_setting(app_settings.JAVA_PATH_KEY))
        code, tail = run_freerouting(cmd, ses, timeout=timeout, on_line=on_line, cancel=cancel)
        if not os.path.exists(ses):
            raise LayoutError("Freerouting produced no session file (exit code %s):\n%s"
                              % (code, "\n".join(tail[-12:])))
        out = self.import_ses(ses, cell_name=name, **R)
        out.update(dsn=dsn, ses=ses, log=tail)
        if R.get("via_optimize") and not self._single_layer_route(R):
            vo = self.optimize_vias(cell_name=name, **R)
            out.update(vias_before=vo["before"], vias_after=vo["after"])
        if R.get("shorten_tracks", True):
            sh = self.shorten_tracks(cell_name=name, **R)
            out.update(shortened=sh["tracks"], shortened_by=sh["before"] - sh["after"])
        if R.get("remove_jogs", True):
            dj = self.remove_jogs(cell_name=name, **R)
            out.update(jogs_removed=dj["jogs"])
        return out

    @staticmethod
    def freeroute_command(exe: str, dsn: str, ses: str, R: dict,
                          java: Optional[str] = None) -> list:
        """The Freerouting command line for the rules ``R``."""
        from ..io.specctra import EXCLUDED_CLASS, freerouting_command
        return freerouting_command(exe, dsn, ses, passes=int(R["passes"]),
                                   optimize_passes=int(R.get("optimize_passes", 100)),
                                   optimize_threshold=float(R.get("optimize_threshold", 2.5)),
                                   exclude_classes=[EXCLUDED_CLASS] if R.get("exclude_nets") else [],
                                   java=java)

    def _dielectric_area(self, cell, diel):
        """Where the dielectric is printed -- the area the 2nd conductor
        (and every via) must stay on: the generated fill with its via
        windows, as one region; without a fill, the fill boundary
        generate_dielectric() would use (an enclosing datatype-0 outline,
        else the I/O-ring area). None when there is no dielectric."""
        from shapely.geometry import Polygon as _SP
        from shapely.geometry import box as _box
        from shapely.ops import unary_union
        if diel is None or diel < 0:
            return None
        dt = self.DIELECTRIC_FILL_DATATYPE
        from shapely.geometry import LineString as _LS
        from ..core.shapes import Path as _Path

        def _outline(sh):
            if isinstance(sh, _Path):
                # an older crossover strip drawn as a path: its outline is the
                # centre line grown by half its width (never the centre line
                # read as a polygon -- that is a sliver, and GEOS rejects it)
                return _LS(sh.points).buffer(sh.width / 2.0, cap_style=2, join_style=2)
            return _SP(sh.points)
        fill = [_outline(sh) for sh in cell.shapes if sh.layer == diel
                and getattr(sh, "datatype", 0) == dt
                and len(getattr(sh, "points", ())) >= (2 if isinstance(sh, _Path) else 3)]
        ins = [_box(*sh.bbox()) for sh in cell.shapes if sh.layer == diel
               and getattr(sh, "datatype", 0) == 0 and hasattr(sh, "bbox")]
        # a crossover strip with a dog-leg can come out self-touching; GEOS
        # won't union an invalid polygon, so repair it first
        fill = [g if g.is_valid else g.buffer(0) for g in fill]
        fill = [g for g in fill if not g.is_empty]
        if fill:
            u = unary_union(fill + ins)
            # the windows are openings INSIDE the sheet: close them up
            from shapely.geometry import MultiPolygon
            polys = list(u.geoms) if isinstance(u, MultiPolygon) else [u]
            return unary_union([_SP(p.exterior.coords) for p in polys if p.geom_type == "Polygon"])
        if ins:
            big = max(ins, key=lambda g: g.area)
            rest = [g for g in ins if g is not big]
            if rest and big.buffer(1).contains(unary_union(rest)):
                return big
        ring = self._io_ring_box(cell)
        return ring if ring is not None else self._substrate_box(cell)

    def shorten_tracks(self, cell_name: Optional[str] = None, progress=None, **rules) -> dict:
        """Straighten detours and pull corners tight: every routed track
        keeps its two ends and is re-laid on its own layer the shortest
        clear way (straight, an L, or a maze route round the obstacles)
        when that is at least 0.1 mm / 3 % shorter and keeps the
        clearance (Freerouting rules tightened by the DRC) from every
        other net, stays out of the die / part-body keep-outs, on the
        substrate, and -- on the 2nd conductor -- on the printed
        dielectric. A track something else joins part-way along is left
        alone. Works with nets still unrouted. One undo step. Returns
        {"tracks": shortened, "before": um, "after": um}. See
        hlayout.core.shorten."""
        from shapely.geometry import Polygon as _SP
        from shapely.geometry import box as _box
        from ..core.shorten import shorten_tracks as _short
        from ..io.techfile import load_net_names
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        cell = self.library.get_cell(name)
        la, lb = self._route_layers()
        R = self.freerouting_rules()
        R.update(rules)
        R = self._rules_with_drc(R)
        keep = {}
        for lay, pts in self._route_keepouts(cell, (la, lb), R):
            if len(pts) >= 3:
                keep.setdefault(int(lay), []).append(_SP(pts))
        keepin = self._substrate_box(cell)
        diel = dielectric_layer_number(self.library)
        keepin_b = self._dielectric_area(cell, diel)
        vspec = self.via_spec(False, R)
        margin = (vspec["window"] - vspec["pad"]) / 2.0 if vspec["window"] else 0.0
        fill_dt = self.DIELECTRIC_FILL_DATATYPE

        def make_via(x, y, size):
            h = size / 2.0
            out = [cell.add_rectangle(la, x - h, y - h, x + h, y + h),
                   cell.add_rectangle(lb, x - h, y - h, x + h, y + h)]
            if diel >= 0 and vspec["window"]:
                hw = h + margin
                out.append(cell.add_rectangle(diel, x - hw, y - hw, x + hw, y + hw))
            return out

        def remove_via(site):
            x, y, _w, kind, obj = site
            if kind == "cell":
                if obj in cell.srefs:
                    cell.srefs.remove(obj)
                return
            lays = {la, lb} | ({diel} if diel >= 0 else set())
            doomed = set()
            for sh in cell.shapes:
                if sh.layer not in lays or getattr(sh, "pin", "") or isinstance(sh, _PathT) \
                        or not hasattr(sh, "bbox") or getattr(sh, "datatype", 0) != 0:
                    continue
                b = sh.bbox()
                bw, bh = b[2] - b[0], b[3] - b[1]
                if 0 < bw <= 5000 and abs(bw - bh) <= max(2, 0.02 * bw) and \
                        abs((b[0] + b[2]) / 2 - x) <= 1 and abs((b[1] + b[3]) / 2 - y) <= 1:
                    doomed.add(id(sh))
            cell.shapes[:] = [sh for sh in cell.shapes if id(sh) not in doomed]

        from ..core.shapes import Path as _PathT
        before = {frozenset(x["nets"]) for x in self.find_shorts(cell_name=name)}
        keep_shapes, keep_srefs = list(cell.shapes), list(cell.srefs)
        with self._batch():
            res = _short(self.library, cell, (la, lb), float(R.get("clearance", 100)),
                         keepouts=keep, keepin=keepin, keepin_b=keepin_b,
                         dielectric_layer=diel if diel >= 0 else None,
                         fill_datatype=fill_dt, via_size=vspec["pad"], via_margin=margin,
                         make_via=make_via, remove_via=remove_via, progress=progress,
                         pad_nets=load_net_names(self.library) or None)
            if res["tracks"] and diel >= 0 and self._has_full_dielectric(cell, diel):
                try:
                    self.generate_dielectric(cell_name=name)
                except Exception:
                    pass
            # safety net: if the re-laid routing joined two named nets
            # anywhere, put everything back as it was
            res["shorts"] = []
            if res["tracks"]:
                new = [x for x in self.find_shorts(cell_name=name)
                       if frozenset(x["nets"]) not in before]
                if new:
                    cell.shapes[:] = keep_shapes
                    cell.srefs[:] = keep_srefs
                    res.update(tracks=0, before=0.0, after=0.0, vias_before=0, vias_after=0,
                               shorts=new, undone=True)
        self._notify()
        return res

    def remove_jogs(self, cell_name: Optional[str] = None, progress=None, **rules) -> dict:
        """Take the jogs out of the routing: a short sideways step (up to
        the Autoroute rules' max_jog, default 1 mm) in an otherwise
        straight run is removed by sliding one side across so the two runs
        meet in line -- when nothing else joins the copper that moves, an
        end of the track only slides within its own pad, and the moved
        copper keeps the clearance (Freerouting rules tightened by the
        DRC) from every other net, stays out of the die / part-body
        keep-outs, on the substrate, and -- on the 2nd conductor -- on the
        printed dielectric. Tracks joined end to end with nothing else at
        the join count as one. Works with nets still unrouted. If the
        result would join two named nets anywhere it is all put back. One
        undo step. Returns {"jogs": removed, "tracks", "found", "before",
        "after" (um), "why"}. See hlayout.core.dejog."""
        from shapely.geometry import Polygon as _SP
        from ..core.dejog import remove_jogs as _dejog
        from ..io.techfile import load_net_names
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        cell = self.library.get_cell(name)
        la, lb = self._route_layers()
        R = self.freerouting_rules()
        R.update(rules)
        R = self._rules_with_drc(R)
        keep = {}
        for lay, pts in self._route_keepouts(cell, (la, lb), R):
            if len(pts) >= 3:
                keep.setdefault(int(lay), []).append(_SP(pts))
        keepin = self._substrate_box(cell)
        diel = dielectric_layer_number(self.library)
        keepin_b = self._dielectric_area(cell, diel)
        before = {frozenset(x["nets"]) for x in self.find_shorts(cell_name=name)}
        keep_shapes = list(cell.shapes)
        with self._batch():
            res = _dejog(self.library, cell, (la, lb), float(R.get("clearance", 100)),
                         max_jog=float(R.get("max_jog", 1000)), keepouts=keep,
                         keepin=keepin, keepin_b=keepin_b,
                         dielectric_layer=diel if diel >= 0 else None,
                         fill_datatype=self.DIELECTRIC_FILL_DATATYPE,
                         pad_nets=load_net_names(self.library) or None, progress=progress)
            res["shorts"] = []
            if res["jogs"]:
                new = [x for x in self.find_shorts(cell_name=name)
                       if frozenset(x["nets"]) not in before]
                if new:
                    cell.shapes[:] = keep_shapes
                    res.update(jogs=0, tracks=0, before=0.0, after=0.0, shorts=new, undone=True)
        self._notify()
        return res

    def via_spec(self, power: bool = False, rules: Optional[dict] = None) -> dict:
        """THE via of this design -- the one definition every via is made
        from (Freerouting import, rip-up / shortening, the Route tool's
        via cell, Fix Vias): {"pad": the conductor pad (signal_via /
        power_via of the Autoroute rules), "window": the dielectric
        window = pad + 2 x via_window_margin (Dielectric rules) -- larger
        than the pad, and drawn at its printed size (generate_dielectric()
        doesn't grow it) -- and "layer": the dielectric layer (-1 none)}."""
        R = rules if rules is not None else self.freerouting_rules()
        dr = load_dielectric_rules(self.library)
        pad = float(R["power_via"] if power else R["signal_via"])
        layer = int(dr.get("layer", -1))
        margin = float(dr.get("via_window_margin", 100))
        return {"pad": pad, "window": pad + 2.0 * margin if layer >= 0 else 0.0, "layer": layer}

    def fix_vias(self, cell_name: Optional[str] = None) -> dict:
        """Redraw every via of the cell to the design's one via
        definition (via_spec()): pads on both route layers and the
        dielectric window, power-net vias (Autoroute power nets) at the
        power size, the rest at the signal size; a via CELL the cell
        places (the Route tool's) is rebuilt to the signal via in place.
        The dielectric fill is redone. One undo step. Returns
        {"vias": flat vias redrawn, "via_cells": via cells rebuilt}."""
        from ..io.specctra import find_vias, via_cell_sites
        from ..io.techfile import load_net_names
        name = cell_name or self.current_cell_name
        cell = self.library.get_cell(name)
        la, lb = self._route_layers()
        R = self.freerouting_rules()
        sig, pwr = self.via_spec(False, R), self.via_spec(True, R)
        diel = sig["layer"]
        power = {n.upper() for n in R.get("power_nets", [])}
        names = load_net_names(self.library)
        tr = self.net_tracer(cell_name=name)
        from shapely.geometry import Point as _Pt

        def net_at(x, y):
            hit = [j for j in tr._query(_Pt(x, y)) if tr.nodes[j][0] == la
                   and tr._geoms[j].distance(_Pt(x, y)) <= 1]
            if not hit:
                return None
            root = tr.dsu.find(hit[0])
            for p, n in names.items():
                if p in tr.pin_node and tr.dsu.find(tr.pin_node[p]) == root:
                    return n
            return None

        flat = find_vias(cell, (la, lb))
        lays = {la, lb} | ({diel} if diel >= 0 else set())
        n_flat = 0
        with self._batch():
            for x, y, _w in flat:
                n = net_at(x, y)
                spec = pwr if (n and n.upper() in power) else sig
                cell.shapes[:] = [sh for sh in cell.shapes if not (
                    sh.layer in lays and not getattr(sh, "pin", "") and hasattr(sh, "bbox")
                    and not hasattr(sh, "width") and getattr(sh, "datatype", 0) == 0
                    and abs((sh.bbox()[0] + sh.bbox()[2]) / 2 - x) <= 1
                    and abs((sh.bbox()[1] + sh.bbox()[3]) / 2 - y) <= 1
                    and (sh.bbox()[2] - sh.bbox()[0]) <= 5000)]
                h = spec["pad"] / 2.0
                cell.add_rectangle(la, x - h, y - h, x + h, y + h)
                cell.add_rectangle(lb, x - h, y - h, x + h, y + h)
                if diel >= 0 and spec["window"]:
                    hw = spec["window"] / 2.0
                    cell.add_rectangle(diel, x - hw, y - hw, x + hw, y + hw)
                n_flat += 1
            rebuilt = set()
            # the via cells placed here, and the one the Route tool drops
            via_cells = [ref.cell_name for _x, _y, _w, ref in
                         via_cell_sites(self.library, cell, (la, lb))]
            rv = load_route_rules(self.library).get("via_cell") or ""
            if rv and rv in self.library.cells:
                via_cells.append(rv)
            for vname in via_cells:
                vc = self.library.cells.get(vname)
                if vc is None or vname in rebuilt:
                    continue
                vlays = {sh.layer for sh in vc.shapes} | {la, lb}
                h = sig["pad"] / 2.0
                vc.shapes[:] = [sh for sh in vc.shapes if sh.layer not in (la, lb, diel)]
                for l in sorted(vlays & {la, lb}):
                    vc.add_rectangle(l, -h, -h, h, h)
                if diel >= 0 and sig["window"]:
                    hw = sig["window"] / 2.0
                    vc.add_rectangle(diel, -hw, -hw, hw, hw)
                rebuilt.add(vname)
            if diel >= 0 and self._has_full_dielectric(cell, diel):
                try:
                    self.generate_dielectric(cell_name=name)
                except Exception:
                    pass
        self._notify()
        return {"vias": n_flat, "via_cells": len(rebuilt)}

    def hybrid_route(self, cell_name: Optional[str] = None, layers: Optional[int] = None,
                     max_jumpers: Optional[int] = None, jumper_cost: Optional[float] = None,
                     crossover_cost: Optional[float] = None, grid: Optional[int] = None,
                     remove_existing: Optional[bool] = None, timeout: Optional[float] = 600,
                     on_line=None, cancel=None, out_dir: Optional[str] = None,
                     orthogonal: Optional[bool] = None, ripup_passes: Optional[int] = None,
                     exclude_nets=None, shorten: Optional[bool] = None,
                     dejog: Optional[bool] = None,
                     via_optimize: Optional[bool] = None, engine: Optional[str] = None) -> dict:
        """Route the cell with the chip-and-wire hybrid router (router/,
        HybridRouterCli): ``layers`` 1 = one conductor, wire-bond jumpers
        the only crossings (``max_jumpers`` 0 = no limit, ``jumper_cost`` in
        mm of track); 2 = both conductors with dielectric crossovers.
        ``orthogonal`` 90-degree tracks only and ``exclude_nets`` default to
        the Autoroute rules (snap_90, exclude_nets); ``ripup_passes``
        rip-up-and-reroute passes; ``shorten`` / ``dejog`` (default: the
        Autoroute rules) shorten the tracks and remove jogs after. The
        tracks, crossover windows, the fingers and jumper lands it adds and
        Power nets are routed at the power width; each conductor takes its
        preferred direction; a crossover is the design's via (via_spec()).
        remove_existing=False keeps the tracks, vias and jumpers already
        drawn: the router joins on to them and routes only what is open.
        Anything left None comes from the Autoroute rules / saved settings.
        This is THE autorouter (Tools > Autoroute...); ``autoroute`` is the
        same method. Otherwise the tracks, crossovers, fingers, lands and
        its bonds and jumpers are drawn into the cell, the old tracks (and
        the last Hybrid Route's additions) taken out first -- one undo
        step. Returns counts, the router's stats, its unrouted connections
        and post-route DRC issues. See hlayout.scripting.hybridroute."""
        from . import hybridroute
        return hybridroute.hybrid_route(self, cell_name=cell_name, layers=layers,
                                        max_jumpers=max_jumpers, jumper_cost=jumper_cost,
                                        crossover_cost=crossover_cost, grid=grid,
                                        remove_existing=remove_existing, timeout=timeout,
                                        on_line=on_line, cancel=cancel, out_dir=out_dir,
                                        orthogonal=orthogonal, ripup_passes=ripup_passes,
                                        exclude_nets=exclude_nets, shorten=shorten, dejog=dejog,
                                        via_optimize=via_optimize, engine=engine)

    autoroute = hybrid_route

    def unroute(self, cell_name: Optional[str] = None) -> dict:
        """Delete the routing and go back to the SLI (one undo step): every
        track (unpinned path on either conductor) and what Hybrid Route added
        -- crossover / jumper lands, local dielectric strips, jumper bonds.
        Flat vias from any router (pads on both conductors and the window)
        go too. Pads, parts and die bonds stay. Returns {"tracks", "added",
        "vias"}."""
        from . import hybridroute
        return hybridroute.unroute(self, cell_name=cell_name)

    def optimize_vias(self, cell_name: Optional[str] = None, progress=None, **rules) -> dict:
        """Take out every via of the routed cell that can safely go: the
        track(s) on one side of it move onto the other conductor and the
        via -- both pads and its dielectric window -- is deleted, when the
        moved copper still reaches its own net at the far end, keeps the
        clearance (Freerouting rules, tightened by the design's DRC) from
        every other net, stays on the printed dielectric if it moves onto
        the 2nd conductor, and is no longer than ``via_optimize_max_flip``
        (um, 0 = any length). The dielectric fill is regenerated. One undo
        step. Returns {"before", "after", "removed": [(x, y)], "moved"}.
        See hlayout.core.viaopt."""
        from ..core.viaopt import optimize_vias as _opt
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        cell = self.library.get_cell(name)
        la, lb = self._route_layers()
        R = self.freerouting_rules()
        R.update(rules)
        R = self._rules_with_drc(R)
        diel = dielectric_layer_number(self.library)
        area = self._dielectric_area(cell, diel)
        with self._batch():
            res = _opt(self.library, cell, (la, lb), float(R.get("clearance", 100)),
                       dielectric_layer=diel if diel >= 0 else None, dielectric_area=area,
                       fill_datatype=self.DIELECTRIC_FILL_DATATYPE,
                       max_flip=float(R.get("via_optimize_max_flip", 0) or 0),
                       progress=progress)
            if res["removed"] and diel >= 0 and \
                    any(s.layer == diel and getattr(s, "datatype", 0) == 0 for s in cell.shapes):
                try:
                    self.generate_dielectric(cell_name=name)
                except Exception:
                    pass
        self._notify()
        return res

    def rip_up_between(self, a, b, cell_name: Optional[str] = None, tol: float = 0.0) -> dict:
        """Rip up the routing between two points on one net: the shortest
        run of track from ``a`` to ``b`` (DBU points on tracks of either
        route layer) -- along the tracks, through T-junctions and vias --
        is cut out, the tracks cut at the two points; a via on the run
        goes too once nothing is left on one of its sides. ``tol`` (DBU)
        lets a point just beside a thin track still pick it. The
        dielectric fill is regenerated if the design has one. One undo
        step. Returns {"a": (x, y, layer), "b": (x, y, layer), "length",
        "tracks", "vias": [(x, y)], "net"} -- ``a``/``b`` are where the
        cut ends are, ready to be routed again. See hlayout.core.ripup."""
        from ..core.ripup import rip_up as _rip
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        cell = self.library.get_cell(name)
        la, lb = self._route_layers()
        net = None
        try:
            from ..io.techfile import load_net_names
            tr = self.net_tracer(cell_name=name).trace_point(a[0], a[1], tol=tol)
            if tr is not None:
                tr.name_from(load_net_names(self.library))
                net = tr.name if tr.name not in (None, "unnamed") else None
        except Exception:
            net = None
        with self._batch():
            res = _rip(self.library, cell, (la, lb), a, b, tol=tol)
            if res is None:
                raise LayoutError(
                    f"no track joins ({a[0] / 1000:.3f}, {a[1] / 1000:.3f}) and "
                    f"({b[0] / 1000:.3f}, {b[1] / 1000:.3f}) mm -- pick two points on tracks "
                    f"of one net (layer {la} or {lb})")
            diel = dielectric_layer_number(self.library)
            if res["vias"] and diel >= 0 and self._has_full_dielectric(cell, diel):
                try:
                    self.generate_dielectric(cell_name=name)
                except Exception:
                    pass
        res["net"] = net
        self._notify()
        return res

    def find_via(self, point, cell_name: Optional[str] = None,
                 tol: Optional[float] = None, layers=None):
        """Locate the via under/nearest ``point`` (an (x, y) DBU
        coordinate) and return ``(centre, [shapes])``, or ``None``.

        A via is what drop_via() draws: two or more square/round pads on
        different layers sharing one centre, plus (optionally) the
        dielectric window on a third layer at that same centre. Matched
        here as every non-pin Rectangle/Polygon whose bbox is roughly
        square, whose centre coincides (within 1 DBU) with the others',
        and that spans at least two layers. ``point`` must lie inside
        one of the via's shapes, or within ``tol`` (default 0) of its
        centre. ``layers`` optionally restricts which layers count
        (e.g. the two conductor layers + the dielectric)."""
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        cell = self.library.get_cell(name)
        allowed = None
        if layers is not None:
            allowed = {self.library.layers.resolve(l) for l in layers}
        px, py = float(point[0]), float(point[1])
        reach = float(tol or 0)

        groups = {}
        for s in cell.shapes:
            if not isinstance(s, (Rectangle, Polygon)) or getattr(s, "pin", ""):
                continue
            if allowed is not None and s.layer not in allowed:
                continue
            x1, y1, x2, y2 = s.bbox()
            w, h = x2 - x1, y2 - y1
            if w <= 0 or h <= 0 or abs(w - h) > max(2, 0.02 * max(w, h)):
                continue
            key = (round((x1 + x2) / 2), round((y1 + y2) / 2))
            groups.setdefault(key, []).append(s)

        best, best_d = None, None
        for (cx, cy), shapes in groups.items():
            if len({s.layer for s in shapes}) < 2:
                continue
            d = math.hypot(px - cx, py - cy)
            inside = any(x1 <= px <= x2 and y1 <= py <= y2
                         for x1, y1, x2, y2 in (s.bbox() for s in shapes))
            if not inside and d > reach:
                continue
            if best is None or d < best_d:
                best, best_d = ((cx, cy), shapes), d
        return best

    def delete_via(self, point, cell_name: Optional[str] = None,
                   tol: Optional[float] = None, layers=None):
        """Remove the via at/nearest ``point`` -- both conductor pads and
        the dielectric window (see find_via()). Tracks that end on it are
        left alone. Raises LayoutError if no via is there. One undo step;
        returns ``(centre, [layers removed])``."""
        name = cell_name or self.current_cell_name
        found = self.find_via(point, cell_name=name, tol=tol, layers=layers)
        if found is None:
            raise LayoutError(f"no via at {tuple(point)}")
        centre, shapes = found
        doomed = {id(s) for s in shapes}
        cell = self.library.get_cell(name)
        with self._batch():
            cell.shapes[:] = [s for s in cell.shapes if id(s) not in doomed]
        self._notify()
        return centre, sorted(s.layer for s in shapes)

    def auto_route(self, a, b, layer=None, grid: int = 100, width: int = 20,
                   clearance: Optional[float] = None, margin: Optional[float] = None,
                   boundary_layer=None, cell_name: Optional[str] = None):
        """Automatically route a clear orthogonal trace between two
        points on one conductor layer, dodging every other piece of
        copper already on that layer.

        ``boundary_layer`` (default: the Route Layers rule's
        ``boundary_layer``, -1 = none): if set, the trace must stay
        entirely inside the geometry on that layer -- a route keep-in
        boundary. The boundary is shrunk by ``width / 2`` before the
        check, and the search area is clipped to it.

        ``a`` / ``b`` are each either an ``(x, y)`` DBU point or a
        pin-bearing shape (Rectangle/Polygon) -- for a shape, its bbox
        centre is the endpoint and its own copper is excluded from the
        obstacle set so a trace can land on it. ``layer`` defaults to
        the Route Layers rule's ``layer_a`` (the "1st routing
        conductor"). ``grid`` is the routing lattice pitch (pass the
        canvas's current grid), ``width`` the trace width (pass the
        canvas's current path width). Obstacles are grown by
        ``width / 2 + clearance`` (``clearance`` defaults to one grid
        step) so the finished trace keeps clear of its neighbours.
        ``margin`` (default: 12 grid steps) is how far past the two
        endpoints the search area extends.

        Draws the result as a round-capped Path on ``layer`` in one undo
        step and returns it. Raises ``LayoutError`` if the two points
        can't be connected with a clear trace at this grid/width, or if
        the search grid would be too large (use a coarser grid). Needs
        shapely."""
        from ..core import autoroute as _ar
        from ..core.maskops import shape_to_shapely

        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        cell = self.library.get_cell(name)
        resolve = self.library.layers.resolve
        rr = load_route_rules(self.library)
        if layer is not None:
            r_layer = resolve(layer)
        else:
            r_layer = int(rr["layer_a"])
        if boundary_layer is not None:
            b_layer = resolve(boundary_layer)
        else:
            b_layer = int(rr.get("boundary_layer", -1))
        grid = int(grid)
        width = int(width)
        if grid <= 0:
            raise LayoutError("routing grid must be positive")
        clr = float(clearance) if clearance is not None else float(grid)
        mrg = float(margin) if margin is not None else 12.0 * grid

        def _endpoint(v):
            if isinstance(v, (Rectangle, Polygon)):
                x0, y0, x1, y1 = v.bbox()
                return ((x0 + x1) / 2.0, (y0 + y1) / 2.0), v
            return (float(v[0]), float(v[1])), None

        (ax, ay), sa = _endpoint(a)
        (bx, by), sb = _endpoint(b)

        if math.hypot(bx - ax, by - ay) < grid:
            raise LayoutError(
                "the two route endpoints are the same point (or closer "
                f"than one grid step, {grid} DBU) -- nothing to route"
            )

        from shapely.geometry import Point as _spoint
        from shapely.ops import unary_union

        obstacles = layer_to_shapely_flattened(self.library, cell, r_layer)
        inflate = width / 2.0 + clr
        # The copper an endpoint lands on must not seal that endpoint
        # off: drop the whole connected piece of copper under (or within
        # half a trace width of) each endpoint -- the pad plus whatever
        # is already joined to it, i.e. the same net. Everything else
        # stays an obstacle however close to a pin it sits. (This used
        # to clear a square halo around each endpoint instead, which let
        # a trace run straight through unrelated copper next to a pin.)
        if not obstacles.is_empty:
            reach = max(width / 2.0, 1.0)
            ends = (_spoint(ax, ay), _spoint(bx, by))
            pieces = list(getattr(obstacles, "geoms", [obstacles]))
            obstacles = unary_union([g for g in pieces
                                     if not any(g.distance(e) <= reach for e in ends)])
        # Also drop the two selected pin shapes' own copper entirely.
        own = [shape_to_shapely(s) for s in (sa, sb) if s is not None]
        own = [g for g in own if g is not None and not g.is_empty]
        if own and not obstacles.is_empty:
            obstacles = obstacles.difference(unary_union(own))
        if not obstacles.is_empty and inflate > 0:
            obstacles = obstacles.buffer(inflate, join_style="mitre", mitre_limit=5.0)

        # Route keep-in boundary: the trace must stay inside the geometry
        # on b_layer, shrunk by the trace half-width so its edge stays in.
        keepin = None
        if b_layer is not None and b_layer >= 0:
            keepin = layer_to_shapely_flattened(self.library, cell, b_layer)
            if keepin.is_empty:
                keepin = None
            else:
                if width > 0:
                    keepin = keepin.buffer(-width / 2.0, join_style="mitre", mitre_limit=5.0)
                if keepin.is_empty:
                    raise LayoutError(
                        f"the route boundary on layer {b_layer} is narrower than the "
                        f"trace width {width} -- nothing left after the keep-in inset"
                    )
                for pn, (px, py) in (("first", (ax, ay)), ("second", (bx, by))):
                    if not keepin.buffer(1.0).contains(_spoint(px, py)):
                        raise LayoutError(
                            f"the {pn} pin ({int(px)}, {int(py)}) is outside the route "
                            f"boundary on layer {b_layer}"
                        )

        x0 = min(ax, bx) - mrg
        y0 = min(ay, by) - mrg
        x1 = max(ax, bx) + mrg
        y1 = max(ay, by) + mrg
        if keepin is not None:
            # clip the search area to the boundary's extent (+1 grid) --
            # the trace can't leave it, and this keeps the node count down
            kx0, ky0, kx1, ky1 = keepin.bounds
            x0, y0 = max(x0, kx0 - grid), max(y0, ky0 - grid)
            x1, y1 = min(x1, kx1 + grid), min(y1, ky1 + grid)
        nx = int((x1 - x0) // grid) + 1
        ny = int((y1 - y0) // grid) + 1

        try:
            pts = _ar.route((ax, ay), (bx, by), obstacles, grid,
                            int(round(x0)), int(round(y0)), nx, ny, keepin=keepin)
        except ValueError as exc:
            raise LayoutError(str(exc))
        if pts is None:
            within = (f" within the layer-{b_layer} route boundary"
                      if keepin is not None else "")
            raise LayoutError(
                f"no clear route on layer {r_layer} between {(int(ax), int(ay))} "
                f"and {(int(bx), int(by))}{within} at grid {grid} / width {width} "
                f"(try a coarser grid, a narrower trace, or a larger margin)"
            )
        return self.path(r_layer, pts, width, cap="round")
