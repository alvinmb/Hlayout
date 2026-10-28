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
    load_dielectric_rules,
    load_resistor_geometry_rules,
    load_thermal_rules,
)

_MAX_DEPTH = 64
#: How far a wire bond's loop rises above the bond layer, in microns,
#: unless the bond rules carry their own ``loop_height``.
_DEFAULT_LOOP_HEIGHT_UM = 250.0
#: Colour for an extruded component body (RGB 0..1).
_COMPONENT_RGB = (0.15, 0.15, 0.17)
#: Fired alumina / ceramic substrate (RGB 0..1) -- white.
_SUBSTRATE_RGB = (0.97, 0.97, 0.95)
#: Substrate thickness when the thermal setup doesn't give one (microns).
_DEFAULT_SUBSTRATE_UM = 635.0


def substrate_layers(library):
    """Layers that ARE the substrate (not printed films): the thermal
    setup's substrate_layer if set, else every layer whose name says
    substrate / alumina / ceramic -- the same naming test Top View uses."""
    try:
        n = int(load_thermal_rules(library)["boundary"].get("substrate_layer", -1))
    except Exception:
        n = -1
    if n >= 0:
        return {n}
    return {i for i in range(len(library.layers))
            if any(w in (library.layers[i].name or "").lower()
                   for w in ("substrate", "alumina", "ceramic"))}


def substrate_thickness_um(library):
    try:
        t = float(load_thermal_rules(library)["boundary"].get("substrate_um", 0) or 0)
    except Exception:
        t = 0.0
    return t if t > 0 else _DEFAULT_SUBSTRATE_UM


#: Resistor glaze overprint: green, see-through (RGB 0..1, transparency %).
_GLAZE_RGB = (0.20, 0.72, 0.35)
_GLAZE_TRANSPARENCY = 55


def dielectric_layer(library):
    try:
        n = int(load_dielectric_rules(library).get("layer", -1))
    except Exception:
        n = -1
    return n if n >= 0 else None


def keepout_layers(library):
    """Layers that only mark keep-out areas (never printed): the
    dielectric rules' extra keep-out layers, the resistor keep-out layer,
    and any layer whose name says keep-out -- except the dielectric layer
    itself, whose keep-outs are its datatype-0 inputs (see
    write_freecad_macro)."""
    out = set()
    try:
        out |= {int(n) for n in load_dielectric_rules(library).get("keepout_layers", [])}
    except Exception:
        pass
    try:
        k = int(load_resistor_geometry_rules(library).get("keepout_layer", -1))
        if k >= 0:
            out.add(k)
    except Exception:
        pass
    for i in range(len(library.layers)):
        nm = (library.layers[i].name or "").lower().replace("-", "").replace(" ", "")
        if "keepout" in nm:
            out.add(i)
    out.discard(dielectric_layer(library))
    return {n for n in out if n >= 0}


def glaze_layers(library):
    out = set()
    try:
        g = int(load_resistor_geometry_rules(library).get("glaze_layer", -1))
        if g >= 0:
            out.add(g)
    except Exception:
        pass
    for i in range(len(library.layers)):
        if "glaze" in (library.layers[i].name or "").lower():
            out.add(i)
    return out


def isolated_pad_layers(library):
    """Wire-bond rules' isolated pad layers (die pads, header pins): part
    of a component's own body, never a printed film on the substrate."""
    try:
        return {int(n) for n in load_bond_rules(library).get("die_pad_layers", [])}
    except Exception:
        return set()


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
    # A cell drawing only the substrate (plus films) is the substrate
    # itself -- drawn as the white base slab, never as a component box.
    subs = substrate_layers(library)
    return any(sh.layer not in film_layers and sh.layer not in subs for sh in c.shapes)


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
_GUI = bool(getattr(App, "GuiUp", False))
if _GUI:
    import FreeCADGui

V = App.Vector
_doc = App.newDocument({docname!r})


def _poly(points):
    return Part.makePolygon(points)


def _fuse(solids):
    # A compound, not a boolean fuse: every layer's polygons were already
    # merged (shapely unary_union) before being written here, so the
    # solids are disjoint and fusing them is pure cost -- a sequential
    # BRep fuse of ~100 many-vertex traces took minutes and looked like
    # a hang. A compound renders, exports and section-probes the same.
    solids = [s for s in solids if s is not None and not s.isNull()]
    if not solids:
        return None
    return solids[0] if len(solids) == 1 else Part.makeCompound(solids)


#: shapes bonds and parts land on: _SURF = films + substrate (what a
#: component sits on), _PROBE = everything solid so far (what a bond
#: end lands on -- die pads, header pins, traces).
_SURF = []
_PROBE = []


def _parts(shape):
    """A shape's individual solids (or the shape itself) -- what the
    probes below test one by one, so their bounding-box prefilter skips
    almost everything instead of sectioning a whole layer compound."""
    try:
        sol = list(shape.Solids)
    except Exception:
        sol = []
    return sol or [shape]


def _add(name, shape, rgb, surface=False, transparency=0):
    if shape is None:
        return None
    o = _doc.addObject("Part::Feature", name)
    o.Shape = shape
    if _GUI and o.ViewObject is not None:
        o.ViewObject.ShapeColor = rgb
        if transparency:
            o.ViewObject.Transparency = int(transparency)
    _PROBE.extend(_parts(o.Shape))
    if surface:
        _SURF.extend(_parts(o.Shape))
    return o


def _top_z(x, y, shapes=None, default=None):
    """Highest solid surface straight below/at (x, y), or `default`."""
    best = None
    for sh in (_PROBE if shapes is None else shapes):
        bb = sh.BoundBox
        if not (bb.XMin - 1e-6 <= x <= bb.XMax + 1e-6 and bb.YMin - 1e-6 <= y <= bb.YMax + 1e-6):
            continue
        try:
            sec = sh.section(Part.makeLine(V(x, y, bb.ZMax + 1.0), V(x, y, bb.ZMin - 1.0)))
        except Exception:
            continue
        for v in sec.Vertexes:
            if best is None or v.Point.z > best:
                best = v.Point.z
    return default if best is None else best


def _surface_z(x0, y0, x1, y1):
    """Where a part with footprint (x0,y0)-(x1,y1) rests: the highest
    film/substrate top under its centre or inset corners (0 if none)."""
    ix, iy = (x1 - x0) * 0.2, (y1 - y0) * 0.2
    pts = [((x0 + x1) / 2.0, (y0 + y1) / 2.0), (x0 + ix, y0 + iy), (x1 - ix, y0 + iy),
           (x1 - ix, y1 - iy), (x0 + ix, y1 - iy)]
    zs = [_top_z(x, y, _SURF) for x, y in pts]
    zs = [z for z in zs if z is not None]
    return max(zs) if zs else 0.0


_MODEL_SHAPES = {{}}     # path -> shape read once (probing + headless/mirrored use)
_MODEL_TEMPLATES = {{}}  # path -> first coloured GUI import, re-used via App::Link


def _progress(msg):
    """Report progress and keep the FreeCAD window painting while the
    macro runs -- otherwise a long GUI run looks frozen ("Not
    Responding") although it is still working."""
    App.Console.PrintMessage("HLayout: %s\\n" % msg)
    if _GUI:
        try:
            FreeCADGui.updateGui()
        except Exception:
            pass


def _model_shape(path):
    sh = _MODEL_SHAPES.get(path)
    if sh is None:
        sh = Part.Shape()
        sh.read(path)
        _MODEL_SHAPES[path] = sh
    return sh


def _place_model(path, name, x, y, z, rot_deg, mirrored):
    """Bring a STEP/IGES model in at (x, y, z) rotated rot_deg about Z.

    Each file is read ONCE. In the GUI the first instance is imported
    with ImportGui (so the model's own colours survive) into an
    App::Part named `name`; every further instance of the same file is
    an App::Link to that part -- re-importing the STEP per instance was
    what made a GUI run of a populated board take minutes. Headless (or
    mirrored) instances are plain Part::Features sharing one read."""
    _progress("placing %s" % name)
    P = App.Placement(V(x, y, z), App.Rotation(V(0, 0, 1), rot_deg))
    base = _model_shape(path)
    probe = base.copy()
    probe.Placement = P
    if _GUI and not mirrored:
        try:
            tpl = _MODEL_TEMPLATES.get(path)
            if tpl is not None:
                link = _doc.addObject("App::Link", name)
                link.setLink(tpl)
                link.Placement = P
                _PROBE.extend(_parts(probe))
                return link
            import ImportGui
            before = set(o.Name for o in _doc.Objects)
            ImportGui.insert(path, _doc.Name)
            new = [o for o in _doc.Objects if o.Name not in before]
            if new:
                part = _doc.addObject("App::Part", name)
                for o in [o for o in new if not any(p in new for p in o.InList)]:
                    part.addObject(o)
                part.Placement = P
                _MODEL_TEMPLATES[path] = part
                _PROBE.extend(_parts(probe))
                return part
        except Exception as e:
            App.Console.PrintWarning("HLayout: coloured import of %s failed (%s), "
                                     "reading plain shape\\n" % (path, e))
    sh = base.copy()
    if mirrored:
        sh = sh.mirror(V(0, 0, 0), V(1, 0, 0))
    o = _doc.addObject("Part::Feature", name)
    o.Shape = sh
    o.Placement = P
    _PROBE.extend(_parts(o.Shape))
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


def _rest_z(surf_geoms, x0, y0, x1, y1):
    """Seat height (mm) for a part whose footprint is the DBU box
    (x0,y0)-(x1,y1): the highest film top under its centre or its four
    20%-inset corners, else 0 (the substrate top) -- the same sample
    points the macro's own _surface_z() probe uses."""
    from shapely.geometry import Point
    ix, iy = (x1 - x0) * 0.2, (y1 - y0) * 0.2
    pts = [((x0 + x1) / 2.0, (y0 + y1) / 2.0), (x0 + ix, y0 + iy), (x1 - ix, y0 + iy),
           (x1 - ix, y1 - iy), (x0 + ix, y1 - iy)]
    best = 0.0
    for geom, zt in surf_geoms:
        if zt > best and any(geom.intersects(Point(x, y)) for x, y in pts):
            best = zt
    return best


#: a bare chip-and-wire die (a part carrying its own wire bonds) with no
#: height of its own in the component models: a typical die thickness
DIE_HEIGHT_UM = 380.0
#: an enclosing package (header) the substrate sits on: its base depth
PACKAGE_BASE_UM = 500.0
_PACKAGE_RGB = (0.54, 0.56, 0.59)


def all_bond_layers(library) -> set:
    """Every wire-bond layer: the Wire Bond rules' one plus any drawn layer
    named for bonds (separate chip-bond and header-bond layers)."""
    out = set()
    try:
        b = int(load_bond_rules(library)["layer"])
        if b >= 0:
            out.add(b)
    except Exception:
        pass
    drawn = {sh.layer for c in library.cells.values() for sh in c.shapes}
    out |= {n for n in drawn if 0 <= n < len(library.layers)
            and "bond" in (library.layers[n].name or "").lower()}
    return out


def _carries_bonds(library, cell_name, bond_layers) -> bool:
    c = library.cells.get(cell_name)
    return c is not None and any(sh.layer in bond_layers for sh in c.shapes)


def _body_bbox_dbu(library, cell_name, film_layers, pad_only, bond_layers):
    """The bbox of a part's BODY: its own shapes that aren't printed film,
    pads or wire bonds (a die cell carrying its fingers and bonds would
    otherwise get a box over all of them); the whole cell when it has
    nothing else."""
    c = library.cells.get(cell_name)
    if c is None:
        return None
    body = [sh.bbox() for sh in c.shapes if sh.layer not in film_layers
            and sh.layer not in pad_only and sh.layer not in bond_layers]
    if not body:
        return _cell_bbox_dbu(library, cell_name)
    return (min(b[0] for b in body), min(b[1] for b in body),
            max(b[2] for b in body), max(b[3] for b in body))


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
    bond_layers = all_bond_layers(library)
    loop_h_mm = _mm(float(loop_height_um) if loop_height_um is not None
                    else _DEFAULT_LOOP_HEIGHT_UM)

    # The substrate is the white base slab below z=0, and isolated pad
    # layers (die pads, header pins) belong to their part's own body --
    # neither is a printed film, so neither takes a place in the stack.
    subs = substrate_layers(library)
    # keep-out markers are never printed, so never exported
    film_layers = ({int(k) for k in thick} - subs - isolated_pad_layers(library)
                   - keepout_layers(library))
    diel = dielectric_layer(library)
    glazes = glaze_layers(library)
    # Pad layers (any layer carrying numbered pads -- package footprint
    # lands, module I/O pads) are drawn in the 2nd routing conductor's
    # colour: they're the same printed metal, so they should read as such
    # in 3D rather than in whatever on-screen colour the layer happens to
    # have. Falls back to each layer's own colour when no 2nd conductor
    # is configured.
    pad_rgb = None
    try:
        from .techfile import load_route_rules
        lb = load_route_rules(library).get("layer_b")
        if isinstance(lb, int) and 0 <= lb < len(library.layers):
            pad_rgb = _rgb01(library.layers[lb].color)
    except Exception:
        pad_rgb = None
    pad_layers = {sh.layer for c in library.cells.values() for sh in c.shapes
                  if getattr(sh, "pin", "")}
    # ...but never recolour a routing conductor itself just because some
    # pads were drawn on it (I/O pads on the 1st conductor, say)
    try:
        from .techfile import load_route_rules as _lrr
        _rr = _lrr(library)
        pad_layers -= {_rr.get("layer_a"), _rr.get("layer_b")}
    except Exception:
        pass
    # a thickness given to the substrate layer itself wins over the
    # thermal setup's substrate_um
    sub_given = [thick[str(n)] for n in sorted(subs) if str(n) in thick]
    sub_t_mm = _mm(sub_given[0] if sub_given else substrate_thickness_um(library))

    # the substrate outline, first: a film off the substrate (header pins)
    # stands on what's under it, not in mid-air
    sub_polys = []
    for n in sorted(subs):
        try:
            sub_polys += list(_iter_polygons(layer_to_shapely_flattened(library, cell, n)))
        except Exception:
            continue
    from shapely.ops import unary_union as _uu0
    sub_geom = _uu0(sub_polys) if sub_polys else None

    # running elevation, layer-number order == print/fire order -- only
    # layers this cell actually prints take a place in it (a resistor ink
    # with a thickness but nothing drawn would lift everything above it)
    film_geoms = {}
    for n in sorted(film_layers - bond_layers):
        try:
            # the dielectric layer's datatype-0 shapes are the fill's
            # INPUTS (its boundary and keep-out holes) -- only the
            # generated fill (datatype 1) is printed dielectric
            g = layer_to_shapely_flattened(library, cell, n,
                                           datatype=1 if n == diel else None)
        except Exception:
            continue
        if g is not None and not g.is_empty and list(_iter_polygons(g)):
            film_geoms[n] = g
    z = 0.0
    stack = []                       # (layer_n, z_bottom_mm, z_top_mm)
    for n in sorted(film_geoms):
        t_mm = _mm(thick[str(n)])
        stack.append((n, z, z + t_mm))
        z += t_mm
    stack_top = z

    lines = [_HEADER.format(design=library.name, cell=cell_name,
                            docname=f"{library.name}_{cell_name}")]

    n_solids = 0
    surf_geoms = []          # (merged film geometry, its top z) -- see _rest_z()
    for (n, zb, zt) in stack:
        geom = film_geoms[n]
        polys = list(_iter_polygons(geom))
        if not polys:
            continue
        surf_geoms.append((geom, zt))
        if n in glazes:
            rgb = _GLAZE_RGB
        elif pad_rgb is not None and n in pad_layers:
            rgb = pad_rgb
        else:
            rgb = _rgb01(library.layers[n].color)
        transp = _GLAZE_TRANSPARENCY if n in glazes else 0
        lines.append(f"# --- layer {n} ({library.layers[n].name}) : "
                     f"z {zb:.4f}..{zt:.4f} mm ---")
        lines.append("_s = []")
        for poly in polys:
            # off the substrate (a header pin): a post from the substrate's
            # underside -- the package base -- up to this film's top
            pb = zb
            if sub_geom is not None and not poly.intersects(sub_geom):
                pb = -sub_t_mm
            faces = [_ring_literal(poly.exterior.coords, pb)]
            faces += [_ring_literal(r.coords, pb) for r in poly.interiors]
            lines.append(f"_s.append(Part.Face([{', '.join(faces)}])"
                         f".extrude(V(0,0,{zt - pb:.5f})))")
            n_solids += 1
        lines.append(f"_add({library.layers[n].name!r} + '_L{n}', _fuse(_s), "
                     f"({rgb[0]:.4f},{rgb[1]:.4f},{rgb[2]:.4f}), surface=True"
                     + (f", transparency={transp})" if transp else ")"))

    # substrate -- white slab from -thickness up to z=0, under the films
    if sub_polys:
        lines.append(f"# --- substrate (layers {sorted(subs)}) : "
                     f"z {-sub_t_mm:.4f}..0 mm ---")
        lines.append("_s = []")
        for poly in sub_polys:
            faces = [_ring_literal(poly.exterior.coords, -sub_t_mm)]
            faces += [_ring_literal(r.coords, -sub_t_mm) for r in poly.interiors]
            lines.append(f"_s.append(Part.Face([{', '.join(faces)}])"
                         f".extrude(V(0,0,{sub_t_mm:.5f})))")
            n_solids += 1
        lines.append(f"_add('Substrate', _fuse(_s), {_SUBSTRATE_RGB!r}, surface=True)")

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
    sub_box = None
    if sub_polys:
        from shapely.ops import unary_union as _uu
        sub_box = _uu(sub_polys).bounds
    pad_only = isolated_pad_layers(library)
    for kind, payload in _walk_instances(library, cell_name, film_layers, comp_models):
        if kind != "component":
            continue
        cn, entry, xf, rot_deg, mirrored, label = payload
        seen_counts[cn] = seen_counts.get(cn, 0) + 1
        obj_name = f"{label}_{cn}" if label else f"{cn}_{seen_counts[cn]}"
        mode = entry.get("mode", "extrude")
        zoff = _mm(float(entry.get("z_offset_um", 0.0)))
        bb = _body_bbox_dbu(library, cn, film_layers, pad_only, bond_layers)
        if mode != "model" and bb is not None and sub_box is not None:
            cs = [xf(p_) for p_ in ((bb[0], bb[1]), (bb[2], bb[1]), (bb[2], bb[3]), (bb[0], bb[3]))]
            if (min(c[0] for c in cs) <= sub_box[0] + 1 and min(c[1] for c in cs) <= sub_box[1] + 1
                    and max(c[0] for c in cs) >= sub_box[2] - 1
                    and max(c[1] for c in cs) >= sub_box[3] - 1):
                # an enclosing package (a header the substrate sits in): a
                # base UNDER the substrate, not a box burying the circuit
                hb = _mm(PACKAGE_BASE_UM)
                lines.append(f"# --- package {cn} (base under the substrate) ---")
                lines.append(f"_sh = Part.Face({_ring_literal(cs, 0.0)}).extrude(V(0,0,{hb:.5f}))")
                lines.append(f"_sh.translate(V(0, 0, {-sub_t_mm - hb:.5f}))")
                lines.append(f"_add({obj_name!r}, _sh, {_PACKAGE_RGB!r})")
                n_comp += 1
                continue
        encloses = False
        if bb is not None and sub_box is not None:
            cs = [xf(p_) for p_ in ((bb[0], bb[1]), (bb[2], bb[1]), (bb[2], bb[3]), (bb[0], bb[3]))]
            encloses = (min(c[0] for c in cs) <= sub_box[0] + 1
                        and min(c[1] for c in cs) <= sub_box[1] + 1
                        and max(c[0] for c in cs) >= sub_box[2] - 1
                        and max(c[1] for c in cs) >= sub_box[3] - 1)
        if entry.get("z_ref") == "substrate_bottom" or encloses:
            # an enclosing package (header) model: the substrate sits IN it,
            # so the model is placed from the substrate's underside -- with
            # z_offset_um = minus the package floor thickness, the floor
            # ends up under the substrate (resting on the films would bury
            # the whole circuit in the floor)
            base_expr = f"{-sub_t_mm + zoff:.5f}"
        elif bb is not None:
            # rests on whatever film is under its footprint -- worked out
            # here from the same merged 2D film geometry the slabs were
            # built from (the films are flat, so a point-in-polygon test
            # gives the exact top), instead of BRep-sectioning every
            # solid in FreeCAD per part, which took seconds per part
            cs = [xf(p) for p in ((bb[0], bb[1]), (bb[2], bb[1]), (bb[2], bb[3]), (bb[0], bb[3]))]
            fx0, fy0 = min(c[0] for c in cs), min(c[1] for c in cs)
            fx1, fy1 = max(c[0] for c in cs), max(c[1] for c in cs)
            base_expr = f"{_rest_z(surf_geoms, fx0, fy0, fx1, fy1) + zoff:.5f}"
        else:
            base_expr = f"{zoff:.5f}"

        if mode == "model" and entry.get("model_path"):
            mp = entry["model_path"]
            if not os.path.isabs(mp):
                mp = os.path.join(comp_dir, mp)
            # entry["origin_um"] (cell-local, DBU = um) is where the
            # model's own (0, 0) belongs in the footprint -- e.g. the pad
            # ring centre for a centred LCC/PLCC model (see
            # hlayout.io.component_match.match_chip_carrier_by_name()).
            # Default: the cell origin, as before.
            org = entry.get("origin_um") or (0.0, 0.0)
            ox_dbu, oy_dbu = xf((float(org[0]), float(org[1])))
            # entry["rotation_deg"] is an extra correction ON TOP OF the
            # placement's own rot_deg -- e.g. from an auto-match that
            # identified this package via its footprint's Y axis instead
            # of the library model's own local X-axis lead-pitch
            # convention (see hlayout.io.component_match's
            # _gullwing_rotation_correction()) -- not a substitute for it.
            total_rot_deg = rot_deg + float(entry.get("rotation_deg", 0.0))
            lines.append(f"# --- component {cn} (model) ---")
            lines.append("try:")
            lines.append(f"    _place_model({mp!r}, {obj_name!r}, {_mm(ox_dbu):.5f}, "
                         f"{_mm(oy_dbu):.5f}, {base_expr}, {total_rot_deg:.4f}, {bool(mirrored)})")
            lines.append("except Exception as _e:")
            lines.append(f"    App.Console.PrintWarning('HLayout: could not read "
                         f"{os.path.basename(mp)} for {cn}: %s\\n' % _e)")
            n_comp += 1
            continue

        if bb is None:
            continue
        x0, y0, x1, y1 = bb
        corners = [xf((x0, y0)), xf((x1, y0)), xf((x1, y1)), xf((x0, y1))]
        if "height_um" in entry:
            h_mm = _mm(float(entry["height_um"]))
        elif _carries_bonds(library, cn, bond_layers):
            h_mm = _mm(DIE_HEIGHT_UM)            # a bare die with its own bonds
        else:
            h_mm = _mm(DEFAULT_COMPONENT_HEIGHT_UM)
        lines.append(f"# --- component {cn} (extrude {h_mm:.4f} mm) ---")
        lines.append(f"_sh = Part.Face({_ring_literal(corners, 0.0)}).extrude(V(0,0,{h_mm:.5f}))")
        lines.append(f"_sh.translate(V(0, 0, {base_expr}))")
        lines.append(f"_add({obj_name!r}, _sh, "
                     f"({_COMPONENT_RGB[0]},{_COMPONENT_RGB[1]},{_COMPONENT_RGB[2]}))")
        n_comp += 1

    # wire bonds -- raised loops, built LAST so each end lands on the
    # actual surface under it (die pad, header pin head, trace) found in
    # FreeCAD from the solids above, not on a guessed film height
    if bond_layers:
        segs = [s_ for n in sorted(bond_layers) for s_ in _bond_segments(library, cell_name, n)]
        if segs:
            lines.append(f"# --- wire bonds (layers {sorted(bond_layers)}) ---")
            lines.append("_BONDS = [")
            for (p0, p1, w) in segs:
                (x0, y0), (x1b, y1b) = p0, p1
                r_mm = max(_mm(w) / 2.0, 0.005)
                lines.append(f"    ({_mm(x0):.5f}, {_mm(y0):.5f}, {_mm(x1b):.5f}, "
                             f"{_mm(y1b):.5f}, {r_mm:.5f}),")
            lines.append("]")
            lines.append("_bw = []")
            lines.append("for _x0, _y0, _x1, _y1, _r in _BONDS:")
            lines.append("    _z0 = _top_z(_x0, _y0, default=0.0)")
            lines.append("    _z1 = _top_z(_x1, _y1, default=0.0)")
            lines.append(f"    _za = max(_z0, _z1) + {loop_h_mm:.5f}")
            lines.append("    _bw.append(_bondwire(V(_x0, _y0, _z0), "
                         "V((_x0 + _x1) / 2.0, (_y0 + _y1) / 2.0, _za), V(_x1, _y1, _z1), _r))")
            rgb = _rgb01(library.layers[bond_layer if bond_layer >= 0
                                        else min(bond_layers)].color)
            lines.append(f"_add('wire_bonds', _fuse(_bw), "
                         f"({rgb[0]:.4f},{rgb[1]:.4f},{rgb[2]:.4f}))")
            n_solids += len(segs)

    if n_solids == 0 and n_comp == 0:
        raise LayoutError(
            f"nothing to export from {cell_name!r}: no geometry on any layer "
            "with a fired-film thickness, and no components"
        )

    lines.append(_FOOTER)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path
