"""3D view of a design in SolidCraft, built live over its MCP server.

The same stack the FreeCAD export builds (hlayout.io.freecadio): a white
substrate slab below z = 0, every printed film with a thickness extruded
in print order (layer-number order) on top of it, each placed part as a
dark body resting on the film under it, and every wire bond as a raised
arc of round wire -- but sent as SolidCraft commands
(sketch / line / extrude / box / arc / pipe / appearance) through
hlayout.io.solidcraft_mcp instead of written as a macro.

build_batches() is pure (no SolidCraft needed): it returns the command
batches, one per body, so a progress bar can follow them.
send_to_solidcraft() opens SolidCraft (or uses a running one) and plays
them.
"""
import math
import os
from typing import Callable, List, Optional, Tuple

from ..core.errors import LayoutError
from ..core.maskops import _require_shapely, layer_to_shapely_flattened
from ..core.units import DBU_MM
from . import freecadio as F
from .techfile import (DEFAULT_COMPONENT_HEIGHT_UM, default_component_models,
                       default_layer_thickness, load_bond_rules, load_component_models,
                       load_layer_thickness)

#: shapely simplify tolerance for film outlines (um): drops the extra
#: points round caps and buffers make, far below anything visible
SIMPLIFY_UM = 1.0
COMPONENT_HEX = "#26262b"
PACKAGE_HEX = "#8a8f96"          # header / package base under the substrate
#: a bare chip-and-wire die (a part carrying its own wire bonds) with no
#: height of its own in the component models: a typical die thickness
DIE_HEIGHT_UM = 380.0
#: an enclosing package (header) the substrate sits on: its base depth
PACKAGE_BASE_UM = 500.0
SUBSTRATE_HEX = "#f7f7f2"
GLAZE_HEX = "#38b85a"


def _mm(v):
    return round(float(v) * DBU_MM, 5)


def _hex(rgb01):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(c * 255)))) for c in rgb01)


def _safe(name):
    out = "".join(ch if ch.isalnum() or ch in "_-. " else "_" for ch in str(name)).strip()
    return out or "body"


def _rings(poly):
    def pts(ring):
        c = [(_mm(x), _mm(y)) for x, y in list(ring.coords)[:-1]]
        # drop consecutive duplicates after rounding
        out = []
        for p in c:
            if not out or p != out[-1]:
                out.append(p)
        if len(out) > 1 and out[0] == out[-1]:
            out.pop()
        return out
    return pts(poly.exterior), [pts(r) for r in poly.interiors]


def _film_batch(name, polys, z_mm, t_mm, colour, opacity=1.0):
    """One sketch at height z with every ring of ``polys`` drawn as a
    closed polyline, then ONE extrude of each polygon's profile (picked by
    a point inside it, so its holes stay holes) into a body ``name``."""
    cmds = [{"command": "component.create", "params": {"name": name, "activate": True}},
            {"command": "sketch.create",
             "params": {"plane": "XY", "offset": z_mm, "name": f"{name} sketch"}}]
    picks = []
    for poly in polys:
        outer, holes = _rings(poly)
        if len(outer) < 3:
            continue
        for ring in [outer] + [h for h in holes if len(h) >= 3]:
            cmds.append({"command": "sketch.line",
                         "params": {"points": [list(p) for p in ring], "closed": True,
                                    "connect": False}})
        rp = poly.representative_point()
        picks.append({"point": [_mm(rp.x), _mm(rp.y)]})
    if not picks:
        return None
    cmds.append({"command": "sketch.finish", "params": {}})
    cmds.append({"command": "solid.extrude",
                 "params": {"distance": t_mm, "profiles": picks, "operation": "new",
                            "body_name": name}})
    cmds += _close_component(name, colour, opacity)
    return cmds, None


def _close_component(name, colour, opacity=1.0):
    """Colour component ``name`` (every body in it) and go back to the
    design root."""
    look = {"components": [name], "color": colour}
    if opacity < 1.0:
        look["opacity"] = opacity
    return [{"command": "appearance.assign", "params": look},
            {"command": "component.activate", "params": {"component": "root"}}]


def build_batches(library, cell_name: str, *, layer_thickness=None, component_models=None,
                  loop_height_um: Optional[float] = None) -> List[Tuple[str, list]]:
    """[(label, [SolidCraft command, ...], finish), ...] building
    ``cell_name`` in 3D, in mm with z up (substrate top at z = 0).
    Each film, the parts and the bonds are built inside their own
    component, coloured as a whole. ``finish`` (None here) is an optional
    follow-up for _finish(): {"merge": name} joins the bodies a batch made
    into one. Raises LayoutError when nothing has a thickness."""
    _require_shapely()
    if cell_name not in library.cells:
        raise LayoutError(f"no cell named {cell_name!r} in the library")
    cell = library.cells[cell_name]
    thick = dict(layer_thickness) if layer_thickness is not None else load_layer_thickness(library)
    if not thick:
        thick = default_layer_thickness(library)
    thick = {str(int(k)): float(v) for k, v in thick.items() if float(v) > 0}
    if not thick:
        raise LayoutError("no layer has a fired-film thickness -- set them in Setup first")
    comp_models = component_models if component_models is not None else load_component_models(library)
    if not comp_models:
        comp_models = default_component_models(library)
    try:
        bond_layer = int(load_bond_rules(library)["layer"])
    except Exception:
        bond_layer = -1
    # every wire-bond layer: the Wire Bond rules' one plus any layer named
    # for bonds (separate chip-bond and header-bond layers) -- drawn as
    # wires, never extruded as a flat film
    bond_layers = {bond_layer} if bond_layer >= 0 else set()
    drawn = {sh.layer for c in library.cells.values() for sh in c.shapes}
    bond_layers |= {n for n in drawn if 0 <= n < len(library.layers)
                    and "bond" in (library.layers[n].name or "").lower()}
    loop_h = _mm(loop_height_um if loop_height_um is not None else F._DEFAULT_LOOP_HEIGHT_UM)

    subs = F.substrate_layers(library)
    pad_only = F.isolated_pad_layers(library)
    film_layers = {int(k) for k in thick} - subs - pad_only - F.keepout_layers(library)
    diel = F.dielectric_layer(library)
    glazes = F.glaze_layers(library)

    batches = []
    # substrate: white slab under z = 0
    sub_given = [thick[str(n)] for n in sorted(subs) if str(n) in thick]
    sub_t = _mm(sub_given[0] if sub_given else F.substrate_thickness_um(library))
    sub_polys = []
    for n in sorted(subs):
        try:
            sub_polys += list(F._iter_polygons(layer_to_shapely_flattened(library, cell, n)))
        except Exception:
            pass
    if sub_polys:
        b = _film_batch("Substrate", sub_polys, -sub_t, sub_t, SUBSTRATE_HEX)
        if b:
            batches.append(("Substrate", b[0], b[1]))

    # films, in print order
    z = 0.0
    surf = []                                    # (geometry, top z mm)
    from shapely.ops import unary_union as _uu0
    sub_geom = _uu0(sub_polys) if sub_polys else None
    for n in sorted(film_layers):
        if n in bond_layers:
            continue
        try:
            geom = layer_to_shapely_flattened(library, cell, n,
                                              datatype=1 if n == diel else None)
        except Exception:
            continue
        if geom is None or geom.is_empty:
            continue
        geom = geom.simplify(SIMPLIFY_UM, preserve_topology=True)
        polys = [p for p in F._iter_polygons(geom) if p.area > 1.0]
        if not polys:
            continue
        # only layers actually printed here take a place in the stack
        t = _mm(thick[str(n)])
        zb, zt = z, z + t
        z = zt
        surf.append((geom, zt))
        lay = library.layers[n]
        colour = GLAZE_HEX if n in glazes else _hex(F._rgb01(lay.color))
        name = _safe(f"{lay.name} L{n}")
        # off the substrate (a header pin): a post from the substrate's
        # underside -- the package base -- up to this film's top
        on = [q for q in polys if sub_geom is None or q.intersects(sub_geom)]
        off = [q for q in polys if sub_geom is not None and not q.intersects(sub_geom)]
        op = 0.45 if n in glazes else 1.0
        if on:
            b = _film_batch(name, on, round(zb, 5), round(t, 5), colour, opacity=op)
            if b:
                batches.append((f"Layer {n} {lay.name} ({len(on)} shapes)", b[0], b[1]))
        if off:
            b = _film_batch(f"{name} posts", off, round(-sub_t, 5), round(zt + sub_t, 5),
                            colour, opacity=op)
            if b:
                batches.append((f"Layer {n} {lay.name} ({len(off)} off the substrate)",
                                b[0], b[1]))

    # parts: a dark body the size of the part's own outline (not its pads
    # or die-attach pad, which are printed films), resting on what's under it
    tops = []                                    # (x0, y0, x1, y1, z_top) DBU / mm
    seen = {}
    sub_box = None
    if sub_polys:
        from shapely.ops import unary_union as _uu
        sub_box = _uu(sub_polys).bounds         # DBU
    part_cmds = [{"command": "component.create", "params": {"name": "Parts", "activate": True}}]
    pkg_cmds = [{"command": "component.create", "params": {"name": "Package", "activate": True}}]
    for kind, payload in F._walk_instances(library, cell_name, film_layers, comp_models):
        if kind != "component":
            continue
        cn, entry, xf, _rot, _mir, label = payload
        c = library.cells.get(cn)
        if c is None:
            continue
        body = [sh.bbox() for sh in c.shapes if sh.layer not in film_layers
                and sh.layer not in pad_only and sh.layer not in bond_layers]
        if not body:
            body = [sh.bbox() for sh in c.shapes]
        if not body:
            continue
        x0 = min(b[0] for b in body); y0 = min(b[1] for b in body)
        x1 = max(b[2] for b in body); y1 = max(b[3] for b in body)
        cs = [xf(p) for p in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        fx0, fy0 = min(p[0] for p in cs), min(p[1] for p in cs)
        fx1, fy1 = max(p[0] for p in cs), max(p[1] for p in cs)
        seen[cn] = seen.get(cn, 0) + 1
        name = _safe(label or f"{cn}_{seen[cn]}")
        if sub_box is not None and fx0 <= sub_box[0] + 1 and fy0 <= sub_box[1] + 1 \
                and fx1 >= sub_box[2] - 1 and fy1 >= sub_box[3] - 1:
            # an enclosing package (a header the substrate sits in): its
            # base goes UNDER the substrate, showing round it -- a box over
            # everything would bury the whole circuit
            hb = _mm(PACKAGE_BASE_UM)
            pkg_cmds.append(
                {"command": "solid.box",
                 "params": {"length": _mm(fx1 - fx0), "width": _mm(fy1 - fy0), "height": hb,
                            "corner": [_mm(fx0), _mm(fy0), round(-sub_t - hb, 5)],
                            "operation": "new", "body_name": name}})
            continue
        base = F._rest_z(surf, fx0, fy0, fx1, fy1)
        if "height_um" in entry:
            h = _mm(float(entry["height_um"]))
        elif any(sh.layer in bond_layers for sh in c.shapes):
            h = _mm(DIE_HEIGHT_UM)               # a bare die with its own bonds
        else:
            h = _mm(DEFAULT_COMPONENT_HEIGHT_UM)
        part_cmds.append(
            {"command": "solid.box",
             "params": {"length": _mm(fx1 - fx0), "width": _mm(fy1 - fy0), "height": h,
                        "corner": [_mm(fx0), _mm(fy0), round(base, 5)],
                        "operation": "new", "body_name": name}})
        tops.append((fx0, fy0, fx1, fy1, base + h))
    if len(pkg_cmds) > 1:
        batches.append(("Package", pkg_cmds + _close_component("Package", PACKAGE_HEX), None))
    if len(part_cmds) > 1:
        batches.append((f"{len(part_cmds) - 1} parts",
                        part_cmds + _close_component("Parts", COMPONENT_HEX), None))

    # wire bonds: an arc in the vertical plane through both ends, piped
    if bond_layers:
        from shapely.geometry import Point
        segs = [s_ for n in sorted(bond_layers) for s_ in F._bond_segments(library, cell_name, n)]

        def zat(x, y):
            best = 0.0
            for g, zt in surf:
                if zt > best and g.intersects(Point(x, y)):
                    best = zt
            for x0, y0, x1, y1, zt in tops:
                if zt > best and x0 <= x <= x1 and y0 <= y <= y1:
                    best = zt
            return best

        colour = _hex(F._rgb01(library.layers[bond_layer if bond_layer >= 0
                                              else min(bond_layers)].color))
        names = []
        cmds = [{"command": "component.create", "params": {"name": "Wire bonds", "activate": True}}]
        for k, (p0, p1, w) in enumerate(segs, 1):
            z0, z1 = zat(*p0), zat(*p1)
            dx, dy = p1[0] - p0[0], p1[1] - p0[1]
            L = math.hypot(dx, dy)
            if L <= 0:
                continue
            ux, uy = dx / L, dy / L
            Lm = _mm(L)
            top = max(z0, z1) + loop_h
            sk = f"bond {k} path"
            nm = f"Bond {k}"
            cmds += [
                {"command": "sketch.create",
                 "params": {"plane": {"origin": [_mm(p0[0]), _mm(p0[1]), round(z0, 5)],
                                      "x_dir": [round(ux, 6), round(uy, 6), 0.0],
                                      "y_dir": [0.0, 0.0, 1.0]}, "name": sk}},
                {"command": "sketch.arc.three_point",
                 "params": {"start": [0.0, 0.0], "end": [Lm, round(z1 - z0, 5)],
                            "through": [round(Lm / 2.0, 5), round(top - z0, 5)],
                            "connect": False}},
                {"command": "sketch.finish", "params": {}},
                {"command": "solid.pipe",
                 "params": {"path_sketch": sk, "path": ["a1"],
                            "diameter": max(_mm(w), 0.01), "operation": "new", "body_name": nm}},
            ]
            names.append(nm)
        if names:
            cmds += _close_component("Wire bonds", colour)
            batches.append((f"{len(names)} wire bonds", cmds, None))
    if not batches:
        raise LayoutError(f"nothing to build from {cell_name!r}")
    return batches


def _finish(sc, result, fin):
    """Join the bodies a batch created into one and colour it (see
    build_batches())."""
    made = []
    for step in (result or {}).get("results", []) if isinstance(result, dict) else []:
        if isinstance(step, dict):
            made += [b for b in step.get("created_bodies", []) or [] if b not in made]
    bodies = list(fin.get("bodies") or [])
    cmds = []
    if fin.get("merge") and made:
        target = made[0]
        if len(made) > 1:
            cmds.append({"command": "solid.combine",
                         "params": {"target": target, "tools": made[1:], "operation": "join"}})
        if target != fin["merge"]:
            cmds.append({"command": "body.rename", "params": {"body": target, "name": fin["merge"]}})
        bodies = [fin["merge"]]
    if fin.get("color") and bodies:
        look = {"bodies": bodies, "color": fin["color"]}
        if float(fin.get("opacity", 1.0)) < 1.0:
            look["opacity"] = float(fin["opacity"])
        cmds.append({"command": "appearance.assign", "params": look})
    if cmds:
        sc.batch(cmds, rollback=True, timeout=600)


def send_to_solidcraft(library, cell_name: str, *, app: Optional[str] = None,
                       window: bool = True, export: Optional[str] = None,
                       on_progress: Optional[Callable[[int, int, str], None]] = None,
                       cancel: Optional[Callable[[], bool]] = None,
                       design_name: Optional[str] = None, **kw) -> dict:
    """Build ``cell_name`` in SolidCraft. ``window``: open the SolidCraft
    app and build in front of you (else headless, for ``export`` only).
    ``export`` = a .step/.3mf/.stl/.obj path to write when done.
    ``on_progress(done, total, label)`` after each batch. Returns
    {"bodies", "batches", "export", "errors"}."""
    from .solidcraft_mcp import SolidCraftClient, SolidCraftError, find_solidcraft, free_port, launch_window
    gui, cli = find_solidcraft(app)
    if not cli:
        raise LayoutError("SolidCraft not found -- install it (github.com/bherbruck/solidcraft) "
                          "and set its folder in File > 3D View in SolidCraft...")
    batches = build_batches(library, cell_name, **kw)
    port = None
    if window:
        if not gui:
            raise LayoutError("solidcraft.exe (the window) not found next to solidcraft-cli")
        port = free_port()
        launch_window(gui, port)
    allow = []
    if export:
        export = os.path.abspath(export)
        if os.name == "nt":
            try:
                import ctypes
                buf = ctypes.create_unicode_buffer(32768)
                d = os.path.dirname(export)
                if ctypes.windll.kernel32.GetLongPathNameW(d, buf, 32768):
                    export = os.path.join(buf.value, os.path.basename(export))
            except Exception:
                pass
        allow.append(os.path.dirname(export))
    errors = []
    total = len(batches)
    with SolidCraftClient(cli, connect=port, allow=allow, timeout=600) as sc:
        sc.call("new_design", name=design_name or f"{library.name} {cell_name}")
        for i, (label, cmds, fin) in enumerate(batches, 1):
            if cancel and cancel():
                errors.append("stopped")
                break
            try:
                r = sc.batch(cmds, rollback=True, timeout=600)
                if isinstance(r, dict) and r.get("ok") is False:
                    f = r.get("failed") or {}
                    errors.append(f"{label}: step {f.get('index')} {f.get('command')}: {f.get('error')}")
                elif fin:
                    _finish(sc, r, fin)
            except SolidCraftError as e:
                errors.append(f"{label}: {e}")
            if on_progress:
                on_progress(i, total, label)
        try:
            sc.execute("browser.visibility", visible=False, folder="sketches")
        except Exception:
            pass
        info = sc.call("inspect_design", measure=False, sketches=False) or {}
        if export:
            sc.call("export", path=export)
    return {"bodies": len(info.get("bodies", [])) if isinstance(info, dict) else None,
            "batches": total, "export": export, "errors": errors, "port": port}
