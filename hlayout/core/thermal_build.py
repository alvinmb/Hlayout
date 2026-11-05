# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Build a ThermalModel from a layout.

hlayout.core.thermal solves a stack of slices on a grid; this module is
the only part of the thermal package that knows about layout geometry.
Given a library and a cell it:

  1. finds the substrate outline and lays a grid over it;
  2. builds the stack -- optional mounting layer, the substrate (as
     several slices), then each printed film in layer-number order
     (HLayout's layer order is its print order) with a material taken
     from the layer's role or the saved thermal materials;
  3. rasterises each film's flattened geometry onto the grid, so a film
     is present only where it is actually printed;
  4. finds the heat sources: every placed resistor (a cell with ink on
     a resistor layer), and every placed component that has been given
     a power or a thermal part definition, built up as an attach layer
     and a body above the films.

Everything it needs beyond the geometry comes from the technology
parameters: layer thicknesses and roles (the same ones FreeCAD export
uses), the Resistor Designer's list for default resistor powers, and
the thermal rules (hlayout.io.techfile.load_thermal_rules).

Requires shapely (for the geometry) as well as numpy/scipy.
"""
from typing import Dict, List, Optional

import numpy as np

from ..io.techfile import (
    DEFAULT_THERMAL_PART,
    _DEFAULT_FILM_UM,
    default_layer_thickness,
    load_bond_rules,
    load_dielectric_rules,
    load_layer_thickness,
    load_resistor_geometry_rules,
    load_resistor_list,
    load_route_rules,
    load_thermal_rules,
)
from .maskops import (
    _compose_transforms,
    _require_shapely,
    _sref_point_transform,
    _transform_geom,
    layer_geoms_flattened,
    shape_to_shapely,
)
from .shapes import Path, expand_arefs_to_srefs
from .thermal import MATERIALS, Part, ThermalError, ThermalModel, material_k

#: GDS datatype Generate Dielectric Fill writes its result at (the
#: datatype-0 shapes on that layer are only its boundary/keep-out
#: inputs). Mirrors Session.DIELECTRIC_FILL_DATATYPE.
DIELECTRIC_FILL_DATATYPE = 1

#: The substrate is split into this many slices, thinnest at the top
#: where the heat spreads out from the films (fractions of its thickness,
#: bottom to top).
SUBSTRATE_FRACTIONS = (0.34, 0.26, 0.20, 0.12, 0.08)

#: Automatic grid: the finest of these pitches (micron) that keeps one
#: slice at or under MAX_AUTO_CELLS cells.
MAX_AUTO_CELLS = 40000
MIN_AUTO_PITCH = 100.0

_SUBSTRATE_WORDS = ("substrate", "alumina", "ceramic")


def layer_roles(library) -> Dict[int, str]:
    """{layer number: "resistor" | "conductor" | "dielectric" | "glaze"}
    from the design's saved rules, then the thermal materials list (a
    layer given a material there joins the stack under that material's
    own role, or as a generic "film")."""
    roles: Dict[int, str] = {}
    rg = load_resistor_geometry_rules(library)

    def put(n, role):
        if isinstance(n, int) and not isinstance(n, bool) and n >= 0:
            roles.setdefault(n, role)

    put(rg.get("resistor_layer"), "resistor")
    for n in (rg.get("ink_layers") or {}).values():
        put(n, "resistor")
    put(rg.get("conductor_layer"), "conductor")
    put(rg.get("glaze_layer"), "glaze")
    rr = load_route_rules(library)
    put(rr.get("layer_a"), "conductor")
    put(rr.get("layer_b"), "conductor")
    put(load_dielectric_rules(library).get("layer"), "dielectric")
    for key, mat in load_thermal_rules(library)["materials"].items():
        try:
            n = int(key)
        except ValueError:
            continue
        name = str(mat).strip().lower()
        roles[n] = name if name in ("resistor", "conductor", "dielectric", "glaze") else \
            roles.get(n, "film")
    return roles


def _polygon_of(shape):
    """A shape's filled area; a closed outline Path (how a substrate or
    package body is normally drawn) counts as the area it encloses."""
    from shapely.geometry import Polygon as _Poly
    if isinstance(shape, Path):
        pts = list(shape.points)
        if len(pts) >= 4 and pts[0] == pts[-1]:
            poly = _Poly(pts)
            if poly.is_valid and poly.area > 0:
                return poly
        return shape_to_shapely(shape)
    return shape_to_shapely(shape)


def _flat_shapes(library, cell, layers, transform=None, depth=0, out=None):
    """(shape, transform) for every shape on ``layers`` through the
    whole hierarchy."""
    if out is None:
        out = []
    if depth > 64:
        return out
    for sh in cell.shapes:
        if sh.layer in layers:
            out.append((sh, transform))
    for ref in cell.srefs + expand_arefs_to_srefs(cell.arefs):
        child = library.cells.get(ref.cell_name)
        if child is not None:
            _flat_shapes(library, child, layers,
                         _compose_transforms(transform, _sref_point_transform(ref)),
                         depth + 1, out)
    return out


def _union(geoms):
    from shapely.geometry import Polygon as _Poly
    from shapely.ops import unary_union
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return _Poly()
    return unary_union([g if g.is_valid else g.buffer(0) for g in geoms])


def find_substrate_layer(library, cell) -> Optional[int]:
    """The layer the substrate outline is drawn on: the saved thermal
    setting, else the lowest layer with geometry whose name mentions
    substrate / alumina / ceramic. None if there isn't one."""
    n = int(load_thermal_rules(library)["boundary"]["substrate_layer"])
    if n >= 0:
        return n
    for i, layer in enumerate(library.layers):
        if any(w in (layer.name or "").lower() for w in _SUBSTRATE_WORDS):
            if _flat_shapes(library, cell, {i}):
                return i
    return None


def _is_substrate_cell(name: str) -> bool:
    return any(w in (name or "").lower() for w in _SUBSTRATE_WORDS)


def _substrate_cell_region(library, cell, transform=None, depth=0):
    """Fallback when no layer is named for the substrate: the outline of
    a placed cell whose NAME says it is the substrate."""
    if depth > 64:
        return None
    for ref in cell.srefs + expand_arefs_to_srefs(cell.arefs):
        child = library.cells.get(ref.cell_name)
        if child is None:
            continue
        tf = _compose_transforms(transform, _sref_point_transform(ref))
        if _is_substrate_cell(child.name):
            g = _union([_transform_geom(_polygon_of(sh), tf) for sh in child.shapes
                        if _polygon_of(sh) is not None])
            if not g.is_empty:
                return g
        deeper = _substrate_cell_region(library, child, tf, depth + 1)
        if deeper is not None:
            return deeper
    return None


def candidate_parts(library, cell_name: str) -> List[dict]:
    """Every placed resistor and component in ``cell_name`` that could
    be given a power: [{"ref_des", "kind", "cell", "power_mw",
    "source"}, ...]. ``source`` says where the power came from --
    "thermal" (saved here), "designer" (the Resistor Designer row with
    this reference designator), "cell" (no row has this reference
    designator, so the row the resistor's cell was generated from) or
    "" (none, 0 mW). Needs no solver; this is what fills the Powers
    table."""
    _require_shapely()
    if cell_name not in library.cells:
        raise ThermalError(f"no cell named {cell_name!r}")
    found = _find_parts(library, library.cells[cell_name], load_thermal_rules(library),
                        layer_roles(library), find_substrate_layer(library,
                                                                   library.cells[cell_name]))
    return [{"ref_des": p["ref_des"], "kind": p["kind"], "cell": p["cell"],
             "power_mw": p["power_mw"], "source": p["source"]} for p in found]


def _find_parts(library, cell, rules, roles, substrate_layer) -> List[dict]:
    res_layers = {n for n, r in roles.items() if r == "resistor"}
    film_layers = set(roles)
    ignore = set(film_layers)
    if substrate_layer is not None:
        ignore.add(substrate_layer)
    bond = int(load_bond_rules(library).get("layer", -1))
    if bond >= 0:
        ignore.add(bond)
    # Resistor Designer powers by row name. Row names aren't forced to be
    # unique, so keep every power a name carries: a label match takes the
    # first row (the Designer's own order), a cell-name match the highest.
    designer: Dict[str, List[float]] = {}
    for row in load_resistor_list(library):
        designer.setdefault(str(row.get("ref_des", "")), []).append(
            float(row.get("power_mw", 0.0)))
    saved = rules["powers"]
    part_rules = rules["parts"]
    parts: List[dict] = []
    used = set()

    def unique(name):
        base, n, out = name, 2, name
        while out in used:
            out = f"{base}_{n}"
            n += 1
        used.add(out)
        return out

    def power_of(ref, is_resistor, cell_name=""):
        """(mW, source). A power saved for this reference designator
        wins; then the Resistor Designer row of the same name; then --
        for a resistor whose label matches no row -- the row its CELL
        was generated from. generate_resistor() names the device cell
        after the row ("R1", or "R1_8" when that name was taken), so a
        resistor placed from row "R1" and later relabelled "R4" on the
        layout still finds its power. Rows sharing that name may differ,
        so the highest is used: the safe side for a temperature check."""
        if ref in saved:
            try:
                return float(saved[ref]), "thermal"
            except (TypeError, ValueError):
                pass
        if is_resistor and ref in designer:
            return designer[ref][0], "designer"
        if is_resistor and cell_name:
            base = cell_name
            head, sep, tail = cell_name.rpartition("_")
            if sep and tail.isdigit() and head:
                base = head
            for key in (cell_name, base):
                if key in designer:
                    return max(designer[key]), "cell"
        return 0.0, ""

    def is_die(child):
        return any(r.cell_name.lower().startswith("chip_pin") for r in child.srefs)

    def walk(parent, transform, depth):
        if depth > 64:
            return
        for ref in parent.srefs + expand_arefs_to_srefs(parent.arefs):
            child = library.cells.get(ref.cell_name)
            if child is None:
                continue
            tf = _compose_transforms(transform, _sref_point_transform(ref))
            own = {sh.layer for sh in child.shapes}
            label = (ref.label or "").strip()
            if _is_substrate_cell(child.name):
                continue
            if own & res_layers:
                name = unique(label or child.name)
                p, src = power_of(name, True, child.name)
                ink = _union([_transform_geom(shape_to_shapely(sh), tf)
                              for sh in child.shapes if sh.layer in res_layers
                              and shape_to_shapely(sh) is not None])
                layer = min(own & res_layers)
                parts.append({"ref_des": name, "kind": "resistor", "cell": child.name,
                              "power_mw": p, "source": src, "geom": ink, "layer": layer,
                              "transform": tf})
            elif (own - ignore) or child.name in part_rules or (label and label in part_rules):
                name = unique(label or child.name)
                p, src = power_of(name, False)
                rule = part_rules.get(name) or part_rules.get(child.name) or {}
                kind = str(rule.get("kind") or ("die" if is_die(child) else "component"))
                body = _union([_transform_geom(_polygon_of(sh), tf) for sh in child.shapes
                               if _polygon_of(sh) is not None])
                parts.append({"ref_des": name, "kind": kind, "cell": child.name,
                              "power_mw": p, "source": src, "geom": body, "rule": rule,
                              "explicit": bool(rule)})
            else:
                walk(child, tf, depth + 1)

    walk(cell, None, 0)

    # resistor ink drawn straight into the cell itself, not as a placed part
    loose = _union([shape_to_shapely(sh) for sh in cell.shapes if sh.layer in res_layers])
    if not loose.is_empty:
        pieces = list(loose.geoms) if hasattr(loose, "geoms") else [loose]
        for i, piece in enumerate(pieces, start=1):
            name = unique(f"RES{i}")
            p, src = power_of(name, True)
            layer = min((sh.layer for sh in cell.shapes if sh.layer in res_layers
                         and shape_to_shapely(sh) is not None
                         and shape_to_shapely(sh).intersects(piece)), default=min(res_layers))
            parts.append({"ref_des": name, "kind": "resistor", "cell": cell.name,
                          "power_mw": p, "source": src, "geom": piece, "layer": layer})
    parts.sort(key=lambda d: (d["kind"] != "resistor", d["ref_des"]))
    return parts


def build_thermal_model(library, cell_name: str, grid_um: Optional[float] = None,
                        base_temp: Optional[float] = None,
                        rules: Optional[dict] = None) -> ThermalModel:
    """Turn ``cell_name`` into a ThermalModel ready to solve.

    ``grid_um`` overrides the saved/automatic X-Y pitch; ``base_temp``
    overrides the saved base (or, for a heat-transfer-coefficient base,
    ambient) temperature for this run. ``rules`` replaces the saved
    thermal rules wholesale (the GUI passes its unsaved edits this way).
    Anything the builder had to assume or skip is listed in the
    returned model's ``warnings``."""
    _require_shapely()
    import shapely
    if cell_name not in library.cells:
        raise ThermalError(f"no cell named {cell_name!r}")
    cell = library.cells[cell_name]
    rules = rules or load_thermal_rules(library)
    bc = rules["boundary"]
    roles = layer_roles(library)
    for key, mat in rules["materials"].items():     # rules may be unsaved GUI edits
        try:
            n = int(key)
        except ValueError:
            continue
        name = str(mat).strip().lower()
        roles[n] = name if name in ("resistor", "conductor", "dielectric", "glaze") else \
            roles.get(n, "film")
    warnings: List[str] = []

    # --- substrate region and grid -------------------------------------
    sub_layer = int(bc["substrate_layer"])
    if sub_layer < 0:
        found = find_substrate_layer(library, cell)
        sub_layer = -1 if found is None else found
    roles.pop(sub_layer, None)
    film_geom = {}
    diel_layer = int(load_dielectric_rules(library).get("layer", -1))
    for n in sorted(roles):
        geoms = []
        if n == diel_layer:
            geoms = layer_geoms_flattened(library, cell, n, datatype=DIELECTRIC_FILL_DATATYPE)
        if not geoms:
            geoms = layer_geoms_flattened(library, cell, n)
        g = _union(geoms)
        if not g.is_empty:
            film_geom[n] = g

    region = None
    if sub_layer >= 0:
        region = _union([_transform_geom(_polygon_of(sh), tf) if tf else _polygon_of(sh)
                         for sh, tf in _flat_shapes(library, cell, {sub_layer})])
        if region.is_empty:
            region = None
    if region is None:
        region = _substrate_cell_region(library, cell)
    if region is None:
        if not film_geom:
            raise ThermalError(
                f"cell {cell_name!r} has no substrate outline and no geometry on a "
                "conductor, dielectric, resistor or glaze layer to analyse")
        x0, y0, x1, y1 = _union(list(film_geom.values())).bounds
        from shapely.geometry import box
        region = box(x0 - 500, y0 - 500, x1 + 500, y1 + 500)
        warnings.append("no substrate outline found: used the films' bounding box plus "
                        "0.5 mm (set the substrate layer in the thermal Boundary settings)")
    x0, y0, x1, y1 = region.bounds
    width, height = x1 - x0, y1 - y0
    if width <= 0 or height <= 0:
        raise ThermalError("substrate outline has no area")

    pitch = float(grid_um) if grid_um else float(bc.get("grid_um") or 0.0)
    if pitch <= 0:
        pitch = MIN_AUTO_PITCH
        while (width / pitch) * (height / pitch) > MAX_AUTO_CELLS:
            pitch += 25.0
    nx = max(1, int(np.ceil(width / pitch - 1e-9)))
    ny = max(1, int(np.ceil(height / pitch - 1e-9)))
    if nx * ny > 4_000_000:
        raise ThermalError(f"thermal grid {nx} x {ny} is too fine -- use a larger grid pitch")

    base_mode = str(bc["base_mode"])
    base_t, amb = float(bc["base_temp_c"]), float(bc["ambient_c"])
    if base_temp is not None:
        if base_mode == "fixed":
            base_t = float(base_temp)
        else:
            amb = float(base_temp)
    model = ThermalModel(nx, ny, pitch, origin=(x0, y0), base_mode=base_mode,
                         base_temp_c=base_t, ambient_c=amb,
                         base_htc=float(bc["base_htc"]), top_htc=float(bc["top_htc"]))
    model.warnings = warnings
    xs = x0 + (np.arange(nx) + 0.5) * pitch
    ys = y0 + (np.arange(ny) + 0.5) * pitch
    X, Y = np.meshgrid(xs, ys)

    def raster(geom) -> np.ndarray:
        if geom is None or geom.is_empty:
            return np.zeros((ny, nx), dtype=bool)
        shapely.prepare(geom)
        gx0, gy0, gx1, gy1 = geom.bounds
        i0 = max(0, int((gx0 - x0) // pitch)); i1 = min(nx, int((gx1 - x0) // pitch) + 1)
        j0 = max(0, int((gy0 - y0) // pitch)); j1 = min(ny, int((gy1 - y0) // pitch) + 1)
        out = np.zeros((ny, nx), dtype=bool)
        if i1 > i0 and j1 > j0:
            out[j0:j1, i0:i1] = shapely.contains_xy(geom, X[j0:j1, i0:i1], Y[j0:j1, i0:i1])
        return out

    inside = raster(region)
    if not inside.any():
        raise ThermalError("thermal grid is too coarse to resolve the substrate")

    # --- stack ----------------------------------------------------------
    mount_um = float(bc.get("mount_um") or 0.0)
    if mount_um > 0 and str(bc.get("mount_material") or ""):
        model.add_slice("mounting", material_k(bc["mount_material"]),
                        np.where(inside, mount_um, 0.0), role="mount")
    sub_k = material_k(bc["substrate_material"])
    sub_um = float(bc["substrate_um"])
    if not sub_um > 0:
        raise ThermalError("substrate thickness must be positive")
    for i, frac in enumerate(SUBSTRATE_FRACTIONS, start=1):
        model.add_slice(f"substrate {i}", sub_k, np.where(inside, sub_um * frac, 0.0),
                        layer=sub_layer if sub_layer >= 0 else None, role="substrate")

    thick = load_layer_thickness(library) or default_layer_thickness(library)
    film_slice: Dict[int, int] = {}
    film_mask: Dict[int, np.ndarray] = {}
    for n in sorted(film_geom):
        role = roles[n]
        mat = rules["materials"].get(str(n), role if role in MATERIALS else "dielectric")
        t_um = float(thick.get(str(n), 0) or _DEFAULT_FILM_UM.get(role, 10))
        mask = raster(film_geom[n]) & inside
        if not mask.any():
            warnings.append(f"layer {n} ({library.layers[n].name}): too fine for the "
                            f"{pitch:g} um grid, left out")
            continue
        name = library.layers[n].name or f"L{n}"
        film_slice[n] = model.add_slice(f"{n}: {name}", material_k(mat),
                                        np.where(mask, t_um, 0.0), layer=n, role="film")
        film_mask[n] = mask

    # --- parts ----------------------------------------------------------
    found = _find_parts(library, cell, rules, roles, sub_layer if sub_layer >= 0 else None)
    cond = _union([g for n, g in film_geom.items() if roles[n] == "conductor"])
    limits = rules["limits"]

    def nearest_cell(geom):
        c = geom.centroid
        hit = model.cell_at(c.x, c.y)
        m = np.zeros((ny, nx), dtype=bool)
        if hit is not None:
            m[hit] = True
        return m

    blocks = [d for d in found if d["kind"] != "resistor"
              and (d["power_mw"] > 0 or d.get("explicit"))]
    attach_k = np.ones((ny, nx)); attach_t = np.zeros((ny, nx))
    body_k = np.ones((ny, nx)); body_t = np.zeros((ny, nx))
    block_masks = []
    for d in blocks:
        rule = dict(DEFAULT_THERMAL_PART.get(d["kind"], DEFAULT_THERMAL_PART["component"]))
        rule.update({k: v for k, v in (d.get("rule") or {}).items() if v not in (None, "")})
        geom = d["geom"]
        size = rule.get("size_um")
        if size and not geom.is_empty:
            from shapely.geometry import box
            c = geom.centroid
            geom = box(c.x - float(size[0]) / 2, c.y - float(size[1]) / 2,
                       c.x + float(size[0]) / 2, c.y + float(size[1]) / 2)
        else:
            geom = geom.envelope if not geom.is_empty else geom
        mask = raster(geom) & inside
        if not mask.any():
            mask = nearest_cell(geom) & inside
        if not mask.any():
            warnings.append(f"{d['ref_des']}: lies outside the substrate, left out")
            continue
        attach_um = float(rule.get("attach_um") or 0.0)
        if attach_um > 0:
            attach_k[mask] = material_k(rule["attach_material"])
            attach_t[mask] = attach_um
        body_k[mask] = material_k(rule["body_material"])
        body_t[mask] = float(rule["body_um"]) / 2.0
        block_masks.append((d, mask, geom))
    if block_masks:
        if attach_t.any():
            model.add_slice("part attach", attach_k, attach_t, role="attach")
        z_lower = model.add_slice("part body (lower)", body_k, body_t, role="body")
        z_upper = model.add_slice("part body (top)", body_k, body_t, role="body")
        for d, mask, geom in block_masks:
            extra = [] if d["kind"] == "die" else [(z_lower, mask)]
            model.add_part(Part(d["ref_des"], d["kind"], d["power_mw"] / 1000.0, z_upper,
                                mask, geom.area * 1e-6, limits.get(f"{d['kind']}_max_c"),
                                d["cell"], extra))

    for d in found:
        if d["kind"] != "resistor":
            continue
        n = d["layer"] if d["layer"] in film_slice else next(
            (m for m in sorted(film_slice) if roles[m] == "resistor"), None)
        if n is None:
            warnings.append(f"{d['ref_des']}: its ink layer is not in the thermal stack, "
                            "left out")
            continue
        body = d["geom"]
        active = body.difference(cond) if not cond.is_empty else body
        if active.is_empty or active.area < 0.05 * body.area:
            active = body
        mask = raster(active) & film_mask[n]
        if not mask.any():
            mask = nearest_cell(active) & film_mask[n]
        if not mask.any():
            warnings.append(f"{d['ref_des']}: too small for the {pitch:g} um grid, left out")
            continue
        model.add_part(Part(d["ref_des"], "resistor", d["power_mw"] / 1000.0, film_slice[n],
                            mask, active.area * 1e-6, limits.get("resistor_max_c"),
                            d["cell"]))
    model.parts.sort(key=lambda p: (p.kind != "resistor", p.ref_des))
    if not any(p.power_w > 0 for p in model.parts):
        warnings.append("no part has any power: set powers in the thermal Powers table "
                        "or the Resistor Designer")
    model.meta = {"cell": cell_name, "substrate_layer": sub_layer, "pitch_um": pitch,
                  "substrate_material": bc["substrate_material"], "substrate_um": sub_um,
                  "films": [(n, library.layers[n].name, roles[n],
                             rules["materials"].get(str(n), roles[n])) for n in film_slice]}
    return model
