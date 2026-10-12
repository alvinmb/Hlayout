"""Netlist extraction: turn copper connectivity plus pin/ref-designator
identity into a list of electrical nets.

Built on the same shapely flattening machinery as hlayout.core.drc and
hlayout.core.maskops: every shape on one of the given conductor layers,
anywhere in the design's hierarchy, is unioned together and split back
into connected components, and each component is a piece of substrate
copper. In this process the only way two different conductor layers'
copper physically overlaps is a real via, so plain geometric touching
is a sound proxy for "electrically the same net" -- FOR SUBSTRATE
COPPER.

That proxy is wrong for two things that live ABOVE the substrate, and
they are handled separately:

* WIRE BONDS (`bond_layer`, Setup > Wire Bond Rules...). A bond wire is
  in the air; it only touches anything at its two ends (ball and
  stitch). Unioning the drawn O------O figure with the copper would
  short it to every trace it flies over, and -- worse -- short every
  bond on a die to each other through the die-attach pad underneath
  the die. So a bond contributes nothing to the copper union: each
  bond-layer Path is one link whose two ENDPOINTS (a disc of the wire's
  half-width, at least) are resolved to whatever they land on.
  The end-ball circles wire_bond() draws are pictorial and ignored.

* DIE PADS (`die_pad_layers`, Wire Bond Rules... "Die pad layer").
  Bond pads on top of a bare die are isolated from the substrate copper
  the die sits on. Each die-pad shape is its own node, never merged
  with substrate copper or with other die-pad shapes even when they
  overlap in XY (a die outline drawn on the pad layer would otherwise
  swallow every pad). A bond end that lands on die-pad shapes connects
  to the SMALLEST one under it. A die-pad shape that encloses other
  die-pad shapes is taken to be a die outline, never a landing: a bond
  end that hits only an outline has missed its pad and is reported in
  Net.open_bond_ends. A bond end that lands on no die-pad shape at all
  connects to the substrate copper under it.

Connectivity between substrate components, die pads and bonds is then
a plain union-find; each resulting group is one Net.

Pins: a shape with a non-empty `.pin` (Rectangle.pin/Polygon.pin) is
attributed to the label of the *immediate* parent SRef. A placed SRef
can also be a pin itself (SRef.pin, a lead built from a shared
via/pad-cell template); it is attributed to the *enclosing* part's ref
designator and covers whatever copper/die-pad shapes that instance
draws. A die pad with no pin number at all that has a bond landing on
it is still reported -- as "<RefDes>.@x,y" (pad centre, DBU), counted
as unresolved -- so a bonded-but-unnumbered pad is visible instead of
silently vanishing.

With `bond_layer=None` (legacy callers) every layer in
`conductor_layers` is plain copper, exactly as before.
"""

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

from .errors import GeometryError  # noqa: F401  (re-exported for callers)
from .maskops import (_compose_transforms, _require_shapely, _sref_point_transform,
                      _transform_geom, shape_to_shapely, unary_union)
from .shapes import Path, expand_arefs_to_srefs

try:
    from shapely.geometry import Point as _ShapelyPoint
    from shapely.geometry import Polygon as _ShapelyPolygon
except ImportError:  # pragma: no cover - exercised only without shapely installed
    _ShapelyPoint = None
    _ShapelyPolygon = None


@dataclass
class NetPin:
    """One pin's identity within a net: `ref_des` is the label of the
    Sref that placed the part this pin belongs to (empty if that
    instance was never given one -- see Net.unresolved_count), `pin`
    is the pin number/name itself (Rectangle.pin/Polygon.pin/SRef.pin),
    or "@x,y" for a bonded die pad that has no pin number."""

    ref_des: str
    pin: str
    #: (x, y) DBU in the extracted cell's coordinates -- where this pin
    #: is, for zooming to it. Not part of equality.
    xy: Optional[Tuple[int, int]] = field(default=None, compare=False)

    def label(self) -> str:
        """"R1.2", or "?.2" if the owning instance has no ref designator."""
        return f"{self.ref_des or '?'}.{self.pin}"


@dataclass
class Net:
    """One electrical net (id "NET1", "NET2", ... or "UNASSIGNED").
    `unresolved_count` counts members with no ref designator or no pin
    number. `bonds` is how many wire bonds belong to the net;
    `open_bond_ends` lists (x, y) DBU bond endpoints on this net that
    landed on nothing -- a bond to a pad that isn't on any conductor/
    die-pad layer you extracted. A net with id "UNASSIGNED" is not a
    real net -- see extract_netlist()."""

    id: str
    pins: List[NetPin] = field(default_factory=list)
    unresolved_count: int = 0
    bonds: int = 0
    open_bond_ends: List[Tuple[int, int]] = field(default_factory=list)
    #: (x, y) DBU of a point on the net (copper, pad or bond), for
    #: zooming to a net that has no pins.
    location: Optional[Tuple[int, int]] = None


def _pt(geom) -> Tuple[int, int]:
    """A point inside `geom` (its centroid when that lies inside it)."""
    c = geom.centroid
    if not geom.contains(c):
        c = geom.representative_point()
    return (int(round(c.x)), int(round(c.y)))


class _Flat:
    """Everything _flatten() collects, in top-cell coordinates."""

    def __init__(self):
        self.copper = []      # shapely geoms on substrate conductor layers
        self.pads = []        # [geom, owner_ref, [NetPin...]] one per die-pad shape
        self.bonds = []       # [(landing_geom, (x, y)), (landing_geom, (x, y))]
        self.pinned = []      # (geom, ref_des, pin, on_copper) for substrate/other pins


def _flatten(library, cell, copper_layers, pad_layers, bond_layer,
             flat, ref_des="", owner_ref="", point_transform=None, _depth=0):
    """Walk `cell`'s hierarchy into `flat`. Returns (n_copper, n_pads),
    the index ranges' starts are recorded by the caller to know what an
    SRef instance itself drew (for SRef.pin).

    `ref_des` is the immediate parent SRef's label (reassigned at every
    level); `owner_ref` is the nearest NON-empty label up the chain,
    used only to name an unnumbered bonded die pad."""
    _require_shapely()
    if _depth > 64:
        return
    for shape in cell.shapes:
        layer = shape.layer
        pin = getattr(shape, "pin", "") or ""

        if bond_layer is not None and layer == bond_layer:
            if isinstance(shape, Path) and len(shape.points) >= 2:
                r = max(1.0, float(getattr(shape, "width", 0) or 0) / 2.0)
                ends = []
                for p in (shape.points[0], shape.points[-1]):
                    g = _ShapelyPoint(float(p[0]), float(p[1])).buffer(r, 8)
                    c = _ShapelyPoint(float(p[0]), float(p[1]))
                    if point_transform is not None:
                        g = _transform_geom(g, point_transform)
                        c = _transform_geom(c, point_transform)
                    ends.append((g, (int(round(c.x)), int(round(c.y)))))
                flat.bonds.append(ends)
            # end-ball circles and anything else on the bond layer are
            # pictorial -- connectivity comes from the Path endpoints
            continue

        on_copper = layer in copper_layers
        on_pad = layer in pad_layers
        if not (on_copper or on_pad or pin):
            continue
        g = shape_to_shapely(shape)
        if g is None or g.is_empty:
            continue
        if point_transform is not None:
            g = _transform_geom(g, point_transform)

        if on_pad:
            pins = [NetPin(ref_des=ref_des, pin=pin)] if pin else []
            flat.pads.append([g, owner_ref, pins])
            continue
        if on_copper:
            flat.copper.append(g)
        if pin:
            flat.pinned.append((g, ref_des, pin, on_copper))

    def _descend(ref, child_ref_des):
        if ref.cell_name not in library.cells:
            return
        child = library.cells[ref.cell_name]
        combined = _compose_transforms(point_transform, _sref_point_transform(ref))
        c0, p0 = len(flat.copper), len(flat.pads)
        _flatten(library, child, copper_layers, pad_layers, bond_layer, flat,
                 ref_des=child_ref_des, owner_ref=child_ref_des or owner_ref,
                 point_transform=combined, _depth=_depth + 1)
        if ref.pin:
            # This placement is itself a numbered pin (round lead from a
            # shared template): attribute it, under the ENCLOSING part's
            # ref_des, to whatever this instance drew.
            child_copper = flat.copper[c0:]
            if child_copper:
                flat.pinned.append((unary_union(child_copper), ref_des, ref.pin, True))
            for pad in flat.pads[p0:]:
                pad[2].append(NetPin(ref_des=ref_des, pin=ref.pin))

    for ref in cell.srefs:
        _descend(ref, ref.label)
    for ref in expand_arefs_to_srefs(cell.arefs):
        # ARef has no per-instance label; elements inherit this level's.
        _descend(ref, ref_des)


class _DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            if rb < ra:
                ra, rb = rb, ra
            self.p[rb] = ra


def _components(geoms):
    merged = unary_union(geoms) if geoms else _ShapelyPolygon()
    if merged.is_empty:
        return []
    if merged.geom_type == "Polygon":
        return [merged]
    if merged.geom_type in ("MultiPolygon", "GeometryCollection"):
        return [g for g in merged.geoms if g.geom_type == "Polygon" and not g.is_empty]
    return []


def extract_netlist(library, cell, conductor_layers: Iterable[int],
                    bond_layer: Optional[int] = None,
                    die_pad_layers: Iterable[int] = ()) -> List[Net]:
    """Extract the netlist of `cell` (descending into every placed
    instance).

    `conductor_layers`: resolved layer numbers of substrate copper.
    `bond_layer`: resolved wire-bond layer, or None to treat every
    layer in conductor_layers as plain copper (legacy behaviour).
    `die_pad_layers`: resolved layer numbers of bare-die bond pads,
    kept isolated from substrate copper (see module docstring). A layer
    listed here or as bond_layer is removed from the copper union even
    if it also appears in conductor_layers.

    A pinned shape on none of those layers is collected into one final
    Net(id="UNASSIGNED") rather than dropped. Requires shapely."""
    _require_shapely()
    pad_layers = {int(l) for l in die_pad_layers if l is not None and int(l) >= 0}
    b_layer = int(bond_layer) if bond_layer is not None and int(bond_layer) >= 0 else None
    copper_layers = {int(l) for l in conductor_layers} - pad_layers
    if b_layer is not None:
        copper_layers.discard(b_layer)

    flat = _Flat()
    _flatten(library, cell, copper_layers, pad_layers, b_layer, flat)

    comps = _components(flat.copper)
    n_c, n_p, n_b = len(comps), len(flat.pads), len(flat.bonds)
    PAD0, BOND0 = n_c, n_c + n_p
    dsu = _DSU(n_c + n_p + n_b)

    try:
        from shapely.strtree import STRtree
        comp_tree = STRtree(comps) if comps else None
        pad_tree = STRtree([p[0] for p in flat.pads]) if flat.pads else None
    except Exception:  # pragma: no cover - very old shapely
        comp_tree = pad_tree = None

    def _hits(tree, geoms, probe):
        if not geoms:
            return []
        if tree is not None:
            idx = tree.query(probe)
            cand = [int(i) for i in idx] if len(idx) and not hasattr(idx[0], "geom_type") \
                else [geoms.index(g) for g in idx]
        else:
            cand = range(len(geoms))
        return [i for i in cand if geoms[i].intersects(probe)]

    pad_geoms = [p[0] for p in flat.pads]
    pad_bonded = [False] * n_p
    # A die-pad shape that encloses other die-pad shapes is a die
    # outline/body drawn on the pad layer, not a pad: a bond landing on
    # it and nothing smaller has MISSED its pad -- reported as an open
    # end rather than letting the outline short every such bond together.
    is_outline = [False] * n_p
    for i, g in enumerate(pad_geoms):
        for j in _hits(pad_tree, pad_geoms, g):
            if j != i and pad_geoms[j].area < g.area and g.contains(pad_geoms[j].centroid):
                is_outline[i] = True
                break
    open_ends = {}   # bond index -> [(x, y), ...]

    for bi, ends in enumerate(flat.bonds):
        node = BOND0 + bi
        for land, xy in ends:
            pads_hit = _hits(pad_tree, pad_geoms, land)
            if pads_hit:
                real = [i for i in pads_hit if not is_outline[i]]
                if real:
                    pi = min(real, key=lambda i: pad_geoms[i].area)
                    dsu.union(node, PAD0 + pi)
                    pad_bonded[pi] = True
                else:
                    open_ends.setdefault(bi, []).append(xy)
                continue
            cu_hit = _hits(comp_tree, comps, land)
            for ci in cu_hit:
                dsu.union(node, ci)
            if not cu_hit:
                open_ends.setdefault(bi, []).append(xy)
    # bonds stitched on top of one another (stacked stitch, no pad under)
    for i in range(n_b):
        for j in range(i + 1, n_b):
            if any(a[0].intersects(b[0]) for a in flat.bonds[i] for b in flat.bonds[j]):
                dsu.union(BOND0 + i, BOND0 + j)

    groups = {}   # root -> Net (created lazily, numbered in node order)
    order = []

    def _node_pt(node):
        if node < PAD0:
            return _pt(comps[node])
        if node < BOND0:
            return _pt(flat.pads[node - PAD0][0])
        (_, a), (_, b) = flat.bonds[node - BOND0]
        return ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)

    def _net_for(node):
        root = dsu.find(node)
        if root not in groups:
            groups[root] = Net(id="", location=_node_pt(root))
            order.append(root)
        return groups[root]

    for ci in range(n_c):
        _net_for(ci)

    unassigned: List[NetPin] = []
    for geom, ref_des, pin, on_copper in flat.pinned:
        matched = False
        xy = _pt(geom)
        if on_copper:
            for ci in _hits(comp_tree, comps, geom):
                net = _net_for(ci)
                net.pins.append(NetPin(ref_des=ref_des, pin=pin, xy=xy))
                if not ref_des:
                    net.unresolved_count += 1
                matched = True
        if not matched:
            unassigned.append(NetPin(ref_des=ref_des, pin=pin, xy=xy))

    for pi, (g, owner_ref, pins) in enumerate(flat.pads):
        if not pins and not pad_bonded[pi]:
            continue   # unnumbered, unbonded die-pad shape (outline, spare pad)
        net = _net_for(PAD0 + pi)
        if pins:
            for p in pins:
                p.xy = _pt(g)
                net.pins.append(p)
                if not p.ref_des:
                    net.unresolved_count += 1
        else:
            c = g.centroid
            net.pins.append(NetPin(ref_des=owner_ref,
                                   pin=f"@{int(round(c.x))},{int(round(c.y))}", xy=_pt(g)))
            net.unresolved_count += 1

    for bi in range(n_b):
        net = _net_for(BOND0 + bi)
        net.bonds += 1
        net.open_bond_ends.extend(open_ends.get(bi, []))

    nets = []
    for k, root in enumerate(sorted(order), start=1):
        net = groups[root]
        net.id = f"NET{k}"
        nets.append(net)

    if unassigned:
        nets.append(Net(
            id="UNASSIGNED",
            pins=unassigned,
            unresolved_count=sum(1 for p in unassigned if not p.ref_des),
        ))
    return nets
