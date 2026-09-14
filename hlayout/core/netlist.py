"""Netlist extraction: turn copper connectivity plus pin/ref-designator
identity into a list of electrical nets.

Built on the same shapely flattening machinery as hlayout.core.drc and
hlayout.core.maskops (see maskops's module docstring for why shapely,
and layer_geoms_flattened() for the general "descend into every placed
Sref/Aref, folding in its transform" pattern reused here): every shape
on one of the given conductor layers, anywhere in the design's
hierarchy, is unioned together and split back into connected
components -- exactly hlayout.core.drc._connected_components()'s
technique -- and each component IS an electrical net, since in this
process the only way two different conductor layers' copper ever
physically overlaps is a real via (see hlayout.gui's Via Wizard/Route
tool), so plain geometric touching is a sound proxy for "electrically
the same net."

What layer_geoms_flattened() alone can't give us is which PIN each
piece of copper belongs to: it returns bare merged geometry with no
memory of which original shape, in which placed instance, it came
from. So this module doesn't reuse it directly -- _flatten() below is
a provenance-carrying variant that, for every shape carrying a
non-empty `.pin` (see Rectangle.pin/Polygon.pin), also records its own
untransformed-to-transformed geometry plus the ref designator of the
SRef that placed it -- specifically the *immediate* parent SRef's
`.label`, not any label further up the hierarchy, since that's the
placement that actually corresponds to "this physical part" (a
resistor's own Sref, a footprint's own Sref -- not some outer
assembly-level Sref wrapping several parts together).

A placed SRef can ALSO be a pin in its own right (SRef.pin), for a
round lead built from a shared via-cell template (Session.
create_via_cell()/the Via Wizard): the template's own shapes are
reused by every placement, so a per-shape pin field would be shared
by all of them -- SRef.pin instead lives on the placement, one number
per instance. Such a pin is attributed to the *enclosing* part's ref
designator (not the via instance's own -- it doesn't have one), since
it's one lead of the part the level above identifies.
"""

from dataclasses import dataclass, field
from typing import List, Optional

from .errors import GeometryError
from .maskops import (_compose_transforms, _require_shapely, _sref_point_transform,
                       _transform_geom, shape_to_shapely, unary_union)
from .shapes import expand_arefs_to_srefs

try:
    from shapely.geometry import Polygon as _ShapelyPolygon
except ImportError:  # pragma: no cover - exercised only without shapely installed
    _ShapelyPolygon = None


@dataclass
class NetPin:
    """One pin's identity within a net: `ref_des` is the label of the
    Sref that placed the part this pin belongs to (empty if that
    instance was never given one -- see Net.unresolved_count), `pin`
    is the pin number/name itself (Rectangle.pin/Polygon.pin)."""

    ref_des: str
    pin: str

    def label(self) -> str:
        """"R1.2", or "?.2" if the owning instance has no ref designator."""
        return f"{self.ref_des or '?'}.{self.pin}"


@dataclass
class Net:
    """One electrical net: a connected blob of copper (id is just a
    sequential "NET1", "NET2", ... label, or "UNASSIGNED" -- see
    below) and the pins found on it. `unresolved_count` counts members
    whose `ref_des` is empty -- the pin number is known (someone
    numbered that pad) but the placed instance carrying it has no ref
    designator yet (SRef.label wasn't set), so the pin can't yet be
    tied to a named component in a BOM/schematic sense. A net with
    `id == "UNASSIGNED"` is not a real net at all -- see extract_netlist()."""

    id: str
    pins: List[NetPin] = field(default_factory=list)
    unresolved_count: int = 0


def _flatten(library, cell, conductor_layers, ref_des: str = "", point_transform=None, _depth=0):
    """Returns (geoms, pinned): `geoms` is the flat list of shapely
    geometries on any of `conductor_layers`, anywhere in `cell`'s
    hierarchy (for the union/connectivity pass); `pinned` is a list of
    (geom, ref_des, pin) for every shape with a non-empty `.pin`,
    REGARDLESS of which layer it's on -- a pin set on a shape whose
    layer isn't in `conductor_layers` (e.g. passed the wrong layer
    list, or numbered a pin on a documentation shape by mistake) still
    needs to be reported rather than silently vanishing; see
    extract_netlist()'s "UNASSIGNED" bucket.

    `ref_des` is the ref designator this recursion level's shapes
    should be attributed to -- the label of the immediate parent SRef,
    reassigned (not accumulated) at every level of Sref nesting, per
    this module's docstring."""
    _require_shapely()
    geoms = []
    pinned = []
    if _depth > 64:
        return geoms, pinned
    layer_set = set(conductor_layers)
    for shape in cell.shapes:
        pin = getattr(shape, "pin", "")
        on_conductor = shape.layer in layer_set
        if not on_conductor and not pin:
            continue
        g = shape_to_shapely(shape)
        if g is None or g.is_empty:
            continue
        if point_transform is not None:
            g = _transform_geom(g, point_transform)
        if on_conductor:
            geoms.append(g)
        if pin:
            # `on_conductor` rides along so extract_netlist() only ever
            # tests a pin's geometry against the SAME layers' connectivity
            # union -- shapely intersection is purely 2D and knows
            # nothing about layers, so a pin on a layer that isn't in
            # `conductor_layers` must never be tested against another
            # layer's copper just because they happen to overlap in XY.
            pinned.append((g, ref_des, pin, on_conductor))

    def _descend(ref, child_ref_des):
        if ref.cell_name not in library.cells:
            return
        child = library.cells[ref.cell_name]
        combined = _compose_transforms(point_transform, _sref_point_transform(ref))
        child_geoms, child_pinned = _flatten(
            library, child, conductor_layers, ref_des=child_ref_des,
            point_transform=combined, _depth=_depth + 1,
        )
        geoms.extend(child_geoms)
        pinned.extend(child_pinned)
        if ref.pin and child_geoms:
            # This SRef instance is itself a numbered pin -- e.g. a
            # round lead built from a shared via-cell template
            # (Session.create_via_cell()/the Via Wizard), where the pin
            # number lives on the PLACEMENT (SRef.pin), not on the
            # template's own shapes (which are shared by every
            # placement and so can't carry a distinct number). Attribute
            # the union of whatever conductor-layer copper this
            # instance's own cell draws to it, under the CURRENT
            # (enclosing-part) ref_des -- not child_ref_des -- since a
            # via instance doesn't carry its own separate ref
            # designator; it's one lead of the part the *enclosing*
            # SRef's label identifies.
            pinned.append((unary_union(child_geoms), ref_des, ref.pin, True))

    for ref in cell.srefs:
        _descend(ref, ref.label)
    for ref in expand_arefs_to_srefs(cell.arefs):
        # ARef has no per-instance label of its own (unlike SRef), so
        # every element of a placed array inherits whatever ref_des
        # this level already had (usually none) rather than getting a
        # distinct one -- placing an individually-netlisted pinned
        # part as an array isn't really supported; arrays are meant
        # for uniform pads/bond sites, not discrete numbered parts.
        _descend(ref, ref_des)
    return geoms, pinned


def extract_netlist(library, cell, conductor_layers) -> List[Net]:
    """Extract the netlist of `cell` (descending into every placed
    instance) as seen across `conductor_layers` -- an iterable of
    already-resolved layer numbers (see Library.layers.resolve(); the
    caller, not this function, resolves layer names since this module
    only knows about hlayout.core, not Session's convenience API).

    Every touching/overlapping piece of copper on any of those layers
    becomes one Net (see module docstring for why cross-layer overlap
    is trusted as "same net" without separate via detection). Each
    net's `pins` list is every pinned shape (Rectangle.pin/Polygon.pin
    set) whose geometry falls on that net's copper, identified as
    NetPin(ref_des, pin).

    A pinned shape whose net could not be determined -- its own layer
    wasn't in `conductor_layers`, so it was never part of the
    connectivity union at all -- is collected into one final
    Net(id="UNASSIGNED") instead of being silently dropped; a properly
    numbered design conductor-layer pad should never end up there, so
    a non-empty UNASSIGNED net is itself worth investigating (wrong
    layer list, or a pin set somewhere unexpected).

    Requires the shapely package (see hlayout.core.maskops)."""
    _require_shapely()
    resolved_layers = list(conductor_layers)
    geoms, pinned = _flatten(library, cell, resolved_layers)

    merged = unary_union(geoms) if geoms else _ShapelyPolygon()
    if merged.is_empty:
        components = []
    elif merged.geom_type == "Polygon":
        components = [merged]
    elif merged.geom_type == "MultiPolygon":
        components = list(merged.geoms)
    else:
        components = []

    nets = [Net(id=f"NET{i}") for i in range(1, len(components) + 1)]
    unassigned: List[NetPin] = []
    for geom, ref_des, pin, on_conductor in pinned:
        matched = False
        if on_conductor:
            for net, comp in zip(nets, components):
                if geom.intersects(comp):
                    net.pins.append(NetPin(ref_des=ref_des, pin=pin))
                    if not ref_des:
                        net.unresolved_count += 1
                    matched = True
        if not matched:
            unassigned.append(NetPin(ref_des=ref_des, pin=pin))

    if unassigned:
        nets.append(Net(
            id="UNASSIGNED",
            pins=unassigned,
            unresolved_count=sum(1 for p in unassigned if not p.ref_des),
        ))
    return nets
