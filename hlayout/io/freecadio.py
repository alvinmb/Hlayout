"""FreeCAD 3D export.

Writes a ``.FCMacro`` -- a plain Python script -- that, run inside
FreeCAD (Macro > Macros... > Execute, or ``freecadcmd file.FCMacro``),
rebuilds the design as a stack of coloured solids:

  * The layer table's number order is the print/fire order. Each layer
    that carries a fired-film thickness (Setup, ``layer_thickness``
    tech-param, or the role heuristic in
    hlayout.io.techfile.default_layer_thickness) is extruded from the
    running elevation up by that thickness. Layers with no thickness
    (documentation, ref-des annotation, keep-out markers, route
    boundary) contribute nothing.
  * Each layer's shapes are merged first through the same shapely
    pipeline the mask ops use (hierarchy flattened, overlaps unioned,
    Paths buffered to width, holes kept) -- so a layer becomes one
    clean solid, not a pile of coplanar faces.
  * Wire bonds (the bond layer's two-point Paths) are raised swept
    loops instead of a flat blob.
  * Placed component cells are shown as a fixed-height extrusion of
    their footprint OR by substituting an external STEP/IGES model that
    FreeCAD imports and positions (``component_models`` tech-param).

Once open in FreeCAD the model can be shaded, sectioned, exported to
STEP, or turned into a hidden-line drawing with the TechDraw workbench.

This module only emits text -- FreeCAD does all the solid modelling, so
nothing here adds a dependency beyond ``shapely`` (already required by
the mask ops / DRC / auto-router this shares code with).
"""
import math
import os
from typing import Optional

from ..core.errors import LayoutError
from ..core.library import Library
from ..core.maskops import (
    _require_shapely,
    _transform_geom,
    layer_to_shapely_flattened,
    shape_to_shapely,
)
from ..core.shapes import Path, expand_arefs_to_srefs
from ..core.units import DBU_MM
from .techfile import (
    DEFAULT_COMPONENT_HEIGHT_UM,
    default_component_models,
    default_layer_thickness,
    load_bond_rules,
    load_component_models,
    load_layer_thickness,
)

_MAX_DEPTH = 64
#: How far a wire bond's loop rises above the bond layer, in microns,
#: unless the bond rules carry their own ``loop_height``.
_DEFAULT_LOOP_HEIGHT_UM = 250.0
#: Colour for an extruded component body (RGB 0..1).
_COMPONENT_RGB = (0.15, 0.15, 0.17)


def _rgb01(hex_color: str):
    h = (hex_color or "").lstrip("#")
    if len(h) != 6:
        return (0.6, 0.6, 0.6)
    try:
        return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)
    except ValueError:
        return (0.6, 0.6, 0.6)


def _mm(v: float) -> float:
    return v * DBU_MM


def _ring_literal(coords, z_mm: float) -> str:
    pts = list(coords)
    if pts and pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    inner = ",".join(f"V({_mm(x):.5f},{_mm(y):.5f},{z_mm:.5f})" for x, y in pts)
    return f"_poly([{inner}])"


def _iter_polygons(geom):
    """Yield each shapely Polygon in a Polygon / MultiPolygon /
    GeometryCollection, skipping empties and non-areal parts."""
    if geom is None or geom.is_empty:
        return
    gt = geom.geom_type
    if gt == "Polygon":
        yield geom
    elif gt in ("MultiPolygon", "GeometryCollection"):
        for g in geom.geoms:
            if g.geom_type == "Polygon" and not g.is_empty:
                yield g


# --- hierarchy walk: component instances + wire-bond segments -------------

def is_component_cell(library, cell_name, film_layers, comp_models):
    """True if ``cell_name``, if placed, should be shown as its own
    component body/model rather than descended into: named in
    ``comp_models``, or drawing on any layer that isn't a printed film.
    Pulled out of _walk_instances() so hlayout.io.component_match's
    auto-matcher can apply the exact same "is this a component" test a
    prospective match would actually be subject to."""
    if cell_name in comp_models:
        return True
    c = library.cells.get(cell_name)
    if c is None:
        return False
    return any(sh.layer not in film_layers for sh in c.shapes)


def _walk_instances(library, cell_name, film_layers, comp_models):
    """Yield ``(kind, payload)`` for the flattened hierarchy of
    ``cell_name``:

      ("component", (target_cell_name, entry, transform, rot_deg, mirrored, label))
      ("bond",      ((x0, y0), (x1, y1), width_dbu))

    ``transform`` maps a point in the component cell's own space to
    world (DBU). A cell is a "component" per is_component_cell() above.
    The walk does not descend into a component (its own shapes are its
    guts, handled by the film pass / its own body). ``label`` is the
    placed SRef's own ref designator (may be ""), carried along so the
    macro can name each instance's FreeCAD object distinctly instead of
    reusing the same cell name for every placement (see write_freecad_macro:
    FreeCAD auto-disambiguates a repeated Part::Feature name by bumping its
    own trailing digits, which for a name like "CAP_1210" makes repeat
    instances of the very same part look like different EIA sizes)."""
    bond_layers = film_layers  # bonds are found by the caller's own layer test

    def walk(name, transform, rot_deg, mirrored, depth):
        if depth > _MAX_DEPTH or name not in library.cells:
            return
        cell = library.cells[name]
        for ref in list(cell.srefs) + expand_arefs_to_srefs(cell.arefs):
            theta = math.radians(ref.rotation)
            cos_t, sin_t = math.cos(theta), math.sin(theta)
            mag = ref.magnification
            ox, oy = ref.origin
            xr = ref.x_reflection
            parent = transform

            def child_transform(pt, cos_t=cos_t, sin_t=sin_t, mag=mag,
                                 ox=ox, oy=oy, xr=xr, parent=parent):
                x, y = pt
                if xr:
                    y = -y
                x *= mag
                y *= mag
                rx = x * cos_t - y * sin_t
                ry = x * sin_t + y * cos_t
                return parent((rx + ox, ry + oy))

            child_rot = (rot_deg + (-ref.rotation if mirrored else ref.rotation)) % 360
            child_mirror = mirrored ^ xr
            if is_component_cell(library, ref.cell_name, film_layers, comp_models):
                entry = comp_models.get(ref.cell_name, {"mode": "extrude"})
                yield ("component",
                       (ref.cell_name, entry, child_transform, child_rot, child_mirror,
                        ref.label))
            else:
                yield from walk(ref.cell_name, child_transform, child_rot,
                                child_mirror, depth + 1)

    yield from walk(cell_name, lambda p: p, 0.0, False, 0)


def component_outline_flattened(library, cell_name, film_layers, comp_models):
    """The union, in world DBU coordinates, of every placed component
    instance's own footprint -- everything drawn on a layer that isn't
    a printed film (its outline/keep-out shapes, per is_component_cell()'s
    own test), transformed by that instance's placement. A component's
    pads on an actual film/conductor layer are already covered by that
    layer's own flattened geometry elsewhere, so only the non-film
    shapes are gathered here -- this is purely the physical body/
    package footprint, for occluding what's underneath it in a
    top-down pictorial view (see hlayout.core.maskops's
    hidden_line_top_view() and Session.mask_op_top_view()).

    Returns a single shapely geometry (empty if no component is placed
    or none has any non-film geometry). Requires shapely."""
    _require_shapely()
    from shapely.ops import unary_union

    geoms = []
    for kind, payload in _walk_instances(library, cell_name, film_layers, comp_models):
        if kind != "component":
            continue
        cn, _entry, xf, _rot, _mirrored, _label = payload
        target = library.cells.get(cn)
        if target is None:
            continue
        for shape in target.shapes:
            if shape.layer in film_layers:
                continue  # already covered by that film layer's own pass
            g = shape_to_shapely(shape)
            if g is None or g.is_empty:
                continue
            geoms.append(_transform_geom(g, xf))
    return unary_union(geoms) if geoms else unary_union([])


def _bond_segments(library, cell_name, bond_layer):
    """Every two-point Path on ``bond_layer`` in the flattened hierarchy
    of ``cell_name`` -> [((x0, y0), (x1, y1), width_dbu), ...] (DBU)."""
    out = []

    def walk(name, transform, scale, depth):
        if depth > _MAX_DEPTH or name not in library.cells:
            return
        cell = library.cells[name]
        for sh in cell.shapes:
            if isinstance(sh, Path) and sh.layer == bond_layer and len(sh.points) == 2:
                p0 = transform(tuple(sh.points[0]))
                p1 = transform(tuple(sh.points[1]))
                out.append((p0, p1, sh.width * scale))
        for ref in list(cell.srefs) + expand_arefs_to_srefs(cell.arefs):
            theta = math.radians(ref.rotation)
            cos_t, sin_t = math.cos(theta), math.sin(theta)
            mag = ref.magnification
            ox, oy = ref.origin
            xr = ref.x_reflection
            parent = transform

            def ct(pt, cos_t=cos_t, sin_t=sin_t, mag=mag, ox=ox, oy=oy, xr=xr, parent=parent):
                x, y = pt
                if xr:
                    y = -y
                x *= mag
                y *= mag
                return parent((x * cos_t - y * sin_t + ox, x * sin_t + y * cos_t + oy))

            walk(ref.cell_name, ct, scale * mag, depth + 1)

    walk(cell_name, lambda p: p, 1.0, 0)
    return out


def _cell_bbox_dbu(library, cell_name):
    """(x0, y0, x1, y1) DBU bounding box of every shape drawn directly
    in ``cell_name`` (no recursion), or None."""
    c = library.cells.get(cell_name)
    if c is None or not c.shapes:
        return None
    xs, ys = [], []
    for sh in c.shapes:
        bx0, by0, bx1, by1 = sh.bbox()
        xs += [bx0, bx1]
        ys += [by0, by1]
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


# --- macro emission -------------------------------------------------------

_HEADER = '''# FreeCAD 3D view of {design}  (cell {cell})
# Generated by HLayout -- run inside FreeCAD: Macro > Macros... > Execute,
# or  freecadcmd this_file.FCMacro
import FreeCAD as App
import Part
from functools import reduce
try:
    import FreeCADGui
    _GUI = True
except Exception:
    _GUI = False

V = App.Vector
_doc = App.newDocument({docname!r})


def _poly(points):
    return Part.makePolygon(points)


def _fuse(solids):
    solids = [s for s in solids if s is not None and not s.isNull()]
    if not solids:
        return None
    return reduce(lambda a, b: a.fuse(b), solids[1:], solids[0]) if len(solids) > 1 else solids[0]


def _add(name, shape, rgb):
    if shape is None:
        return None
    o = _doc.addObject("Part::Feature", name)
    o.Shape = shape
    if _GUI and o.ViewObject is not None:
        o.ViewObject.ShapeColor = rgb
    return o


def _bondwire(p0, apex, p1, radius):
    """A swept loop p0 -> apex -> p1; falls back to a thin box path."""
    try:
        c = Part.BSplineCurve()
        c.interpolate([p0, apex, p1])
        spine = Part.Wire(c.toShape())
        prof = Part.Wire(Part.makeCircle(radius, p0, apex.sub(p0)))
        return spine.makePipeShell([prof], True, True)
    except Exception:
        legs = []
        for a, b in ((p0, apex), (apex, p1)):
            d = b.sub(a)
            L = d.Length
            if L > 1e-6:
                legs.append(Part.makeCylinder(radius, L, a, d))
        legs.append(Part.makeSphere(radius, apex))
        return _fuse(legs)

'''

_FOOTER = '''
_doc.recompute()
if _GUI:
    try:
        FreeCADGui.activeDocument().activeView().viewIsometric()
        FreeCADGui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass
'''


def write_freecad_macro(library: Library, cell_name: str, path: str, *,
                        layer_thickness: Optional[dict] = None,
                        component_models: Optional[dict] = None,
                        loop_height_um: Optional[float] = None) -> str:
    """Write a FreeCAD macro rebuilding ``cell_name`` as a 3D solid
    stack. ``layer_thickness`` ({str(layer): microns}) and
    ``component_models`` default to the design's saved tech-params, then
    to the role heuristics in hlayout.io.techfile. Returns ``path``.
    Raises LayoutError if the cell doesn't exist or nothing is
    printable. Requires the ``shapely`` package."""
    _require_shapely()
    if cell_name not in library.cells:
        raise LayoutError(f"no cell named {cell_name!r} in the library")
    cell = library.cells[cell_name]

    thick = dict(layer_thickness) if layer_thickness is not None else load_layer_thickness(library)
    if not thick:
        thick = default_layer_thickness(library)
    thick = {str(int(k)): float(v) for k, v in thick.items() if float(v) > 0}
    if not thick:
        raise LayoutError(
            "no layer has a fired-film thickness -- set them in Setup, or "
            "the design's role config (resistor/route/bond/dielectric "
            "layers) so the heuristic can seed them"
        )

    comp_models = (component_models if component_models is not None
                   else load_component_models(library))
    if not comp_models:
        comp_models = default_component_models(library)

    try:
        bond_layer = int(load_bond_rules(library)["layer"])
    except Exception:
        bond_layer = -1
    loop_h_mm = _mm(float(loop_height_um) if loop_height_um is not None
                    else _DEFAULT_LOOP_HEIGHT_UM)

    film_layers = {int(k) for k in thick}

    # running elevation, layer-number order == print/fire order
    z = 0.0
    stack = []                       # (layer_n, z_bottom_mm, z_top_mm)
    for n in sorted(film_layers):
        t_mm = _mm(thick[str(n)])
        stack.append((n, z, z + t_mm))
        z += t_mm
    stack_top = z

    lines = [_HEADER.format(design=library.name, cell=cell_name,
                            docname=f"{library.name}_{cell_name}")]

    n_solids = 0
    for (n, zb, zt) in stack:
        if n == bond_layer:
            continue                 # bonds get raised loops, below
        try:
            geom = layer_to_shapely_flattened(library, cell, n)
        except Exception:
            continue
        polys = list(_iter_polygons(geom))
        if not polys:
            continue
        rgb = _rgb01(library.layers[n].color)
        lines.append(f"# --- layer {n} ({library.layers[n].name}) : "
                     f"z {zb:.4f}..{zt:.4f} mm ---")
        lines.append("_s = []")
        for poly in polys:
            faces = [_ring_literal(poly.exterior.coords, zb)]
            faces += [_ring_literal(r.coords, zb) for r in poly.interiors]
            lines.append(f"_s.append(Part.Face([{', '.join(faces)}])"
                         f".extrude(V(0,0,{zt - zb:.5f})))")
            n_solids += 1
        lines.append(f"_add({library.layers[n].name!r} + '_L{n}', _fuse(_s), "
                     f"({rgb[0]:.4f},{rgb[1]:.4f},{rgb[2]:.4f}))")

    # wire bonds -- raised loops on the bond layer
    if bond_layer >= 0:
        bz = next((zb for (bn, zb, _zt) in stack if bn == bond_layer), stack_top)
        segs = _bond_segments(library, cell_name, bond_layer)
        if segs:
            lines.append(f"# --- wire bonds (layer {bond_layer}) ---")
            lines.append("_bw = []")
            for (p0, p1, w) in segs:
                (x0, y0), (x1b, y1b) = p0, p1
                mx, my = (x0 + x1b) / 2.0, (y0 + y1b) / 2.0
                r_mm = max(_mm(w) / 2.0, 0.005)
                lines.append(
                    f"_bw.append(_bondwire("
                    f"V({_mm(x0):.5f},{_mm(y0):.5f},{bz:.5f}), "
                    f"V({_mm(mx):.5f},{_mm(my):.5f},{bz + loop_h_mm:.5f}), "
                    f"V({_mm(x1b):.5f},{_mm(y1b):.5f},{bz:.5f}), {r_mm:.5f}))")
            rgb = _rgb01(library.layers[bond_layer].color)
            lines.append(f"_add('wire_bonds', _fuse(_bw), "
                         f"({rgb[0]:.4f},{rgb[1]:.4f},{rgb[2]:.4f}))")
            n_solids += len(segs)

    # components -- extrude a body or substitute a model
    comp_dir = os.path.dirname(os.path.abspath(path))
    n_comp = 0
    # FreeCAD disambiguates a repeated Part::Feature name by bumping its own
    # trailing digits, so naming every instance of the same cell "CAP_1210"
    # would turn a design with six 1210 caps into what LOOKS like six
    # different EIA sizes (CAP_1210, CAP_1211, CAP_1212, ...) in the tree.
    # Name each instance after its own ref designator instead (falling back
    # to an explicit per-cell counter when a placement has none) so repeats
    # of the same part stay recognizably the same part.
    seen_counts: dict = {}
    for kind, payload in _walk_instances(library, cell_name, film_layers, comp_models):
        if kind != "component":
            continue
        cn, entry, xf, rot_deg, mirrored, label = payload
        seen_counts[cn] = seen_counts.get(cn, 0) + 1
        obj_name = f"{label}_{cn}" if label else f"{cn}_{seen_counts[cn]}"
        mode = entry.get("mode", "extrude")
        zoff = _mm(float(entry.get("z_offset_um", 0.0)))
        base_z = stack_top + zoff

        if mode == "model" and entry.get("model_path"):
            mp = entry["model_path"]
            if not os.path.isabs(mp):
                mp = os.path.join(comp_dir, mp)
            ox_dbu, oy_dbu = xf((0.0, 0.0))
            # entry["rotation_deg"] is an extra correction ON TOP OF the
            # placement's own rot_deg -- e.g. from an auto-match that
            # identified this package via its footprint's Y axis instead
            # of the library model's own local X-axis lead-pitch
            # convention (see hlayout.io.component_match's
            # _gullwing_rotation_correction()) -- not a substitute for it.
            total_rot_deg = rot_deg + float(entry.get("rotation_deg", 0.0))
            lines.append(f"# --- component {cn} (model) ---")
            lines.append("try:")
            lines.append(f"    _sh = Part.Shape(); _sh.read({mp!r})")
            if mirrored:
                lines.append(f"    _sh = _sh.mirror(V(0,0,0), V(1,0,0))")
            lines.append(f"    _o = _doc.addObject('Part::Feature', {obj_name!r})")
            lines.append("    _o.Shape = _sh")
            lines.append(f"    _o.Placement = App.Placement("
                         f"V({_mm(ox_dbu):.5f},{_mm(oy_dbu):.5f},{base_z:.5f}), "
                         f"App.Rotation(V(0,0,1), {total_rot_deg:.4f}))")
            lines.append("except Exception as _e:")
            lines.append(f"    App.Console.PrintWarning('HLayout: could not read "
                         f"{os.path.basename(mp)} for {cn}: %s\\n' % _e)")
            n_comp += 1
            continue

        bb = _cell_bbox_dbu(library, cn)
        if bb is None:
            continue
        x0, y0, x1, y1 = bb
        corners = [xf((x0, y0)), xf((x1, y0)), xf((x1, y1)), xf((x0, y1))]
        h_mm = _mm(float(entry.get("height_um", DEFAULT_COMPONENT_HEIGHT_UM)))
        lines.append(f"# --- component {cn} (extrude {h_mm:.4f} mm) ---")
        lines.append(f"_add({obj_name!r}, Part.Face({_ring_literal(corners, base_z)})"
                     f".extrude(V(0,0,{h_mm:.5f})), "
                     f"({_COMPONENT_RGB[0]},{_COMPONENT_RGB[1]},{_COMPONENT_RGB[2]}))")
        n_comp += 1

    if n_solids == 0 and n_comp == 0:
        raise LayoutError(
            f"nothing to export from {cell_name!r}: no geometry on any layer "
            "with a fired-film thickness, and no components"
        )

    lines.append(_FOOTER)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path
