# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Net tracing for Highlight Net: starting from one pad, trace or point,
find everything electrically connected to it -- every piece of copper on
every conductor layer, and every numbered pad (RefDes.Pin) it reaches.

Unlike hlayout.core.netlist.extract_netlist() -- which unions all the
conductor layers together and so treats any XY overlap between two
layers as a connection -- this is stack-aware. In a thick-film
multilayer the 1st and 2nd conductors are separated by a printed
dielectric, and a buried layer-1 trace running underneath a layer-2 pad
is NOT connected to it. Two conductor layers only connect where their
copper overlaps AND the dielectric has an opening there (a via window).
If the dielectric fill hasn't been generated (no datatype
DIELECTRIC_FILL_DATATYPE shapes), the layers connect only inside the
via windows drawn on the dielectric layer (its datatype-0 shapes, less
the enclosing boundary); only a design with no dielectric at all treats
every overlap as a connection, as extract_netlist() does.

Same-layer connectivity is plain geometric touching. Coordinates are
the traced cell's own DBU. Pads drawn directly in the traced cell (not
inside a placed part) are named ``IO.<pin>``. Requires shapely.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .netlist import _DSU, _Flat, _components, _flatten
from .maskops import layer_geoms_flattened

#: An overlap smaller than this (DBU^2) between two layers' copper
#: through a dielectric opening is treated as numerical noise.
MIN_CONTACT_AREA = 1.0


@dataclass
class NetTrace:
    """One traced net: ``pieces`` is [(layer, shapely Polygon)], ``pins``
    the sorted "RefDes.Pin" names of every numbered pad on it."""
    pieces: List[Tuple[int, object]] = field(default_factory=list)
    pins: List[str] = field(default_factory=list)
    seed: str = ""
    #: the design net name(s) of the pads on this copper (see
    #: name_from()); empty until named
    net_names: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        """The net's name: its one design net name, "A + B (SHORT)" if
        pads of more than one named net are connected, or "unnamed" when
        no pad on it has a name."""
        if not self.net_names:
            return "unnamed"
        if len(self.net_names) == 1:
            return self.net_names[0]
        return " + ".join(self.net_names) + " (SHORT)"

    def name_from(self, pad_nets: Dict[str, str]) -> "NetTrace":
        """Fill net_names from a {"RefDes.Pin": net} map; returns self."""
        self.net_names = sorted({pad_nets[p] for p in self.pins if p in pad_nets})
        return self

    def missing_pads(self, pad_nets: Dict[str, str]) -> List[str]:
        """Pads the design says belong to this (single) net but that are
        NOT on this copper -- what's still left to route."""
        if len(self.net_names) != 1:
            return []
        on = set(self.pins)
        return sorted((p for p, n in pad_nets.items() if n == self.net_names[0] and p not in on),
                      key=_pin_sort_key)

    @property
    def layers(self):
        return sorted({lay for lay, _ in self.pieces})


class NetTracer:
    """Build once for a cell, then trace any number of seeds."""

    def __init__(self, library, cell, conductor_layers: Iterable[int],
                 dielectric_layer: Optional[int] = None, fill_datatype: int = 1):
        from shapely.ops import unary_union
        from shapely.strtree import STRtree

        self.layers = [int(l) for l in conductor_layers]
        self.nodes: List[Tuple[int, object]] = []          # (layer, polygon)
        pinned = []                                          # (layer, geom, "REF.PIN")
        for lay in self.layers:
            flat = _Flat()
            _flatten(library, cell, {lay}, set(), None, flat)
            for g in _components(flat.copper):
                self.nodes.append((lay, g))
            for g, ref, pin, on_cu in flat.pinned:
                if on_cu:
                    # a pad drawn directly in the traced cell (a module I/O
                    # pad, say) has no owning instance -- named IO.<pin>
                    pinned.append((lay, g, f"{ref}.{pin}" if ref else f"IO.{pin}"))
        self._geoms = [g for _, g in self.nodes]
        self._tree = STRtree(self._geoms) if self._geoms else None
        self.dsu = _DSU(len(self.nodes))

        fill = None
        windows = None
        if dielectric_layer is not None and int(dielectric_layer) >= 0:
            # The via windows drawn as the dielectric's datatype-0 inputs
            # (a via cell placed by the Route tool carries its own). The one
            # shape enclosing the rest is the fill boundary, not a window.
            ins = layer_geoms_flattened(library, cell, int(dielectric_layer), datatype=0)
            if len(ins) > 1:
                big = max(ins, key=lambda g: g.area)
                rest = [g for g in ins if g is not big]
                if big.buffer(1).contains(unary_union(rest)):
                    ins = rest
            if ins:
                windows = unary_union(ins)
            fg = layer_geoms_flattened(library, cell, int(dielectric_layer),
                                       datatype=fill_datatype)
            if fg:
                # A window always opens the dielectric, even where a fill
                # made before the via was dropped still covers it.
                fill = unary_union(fg)
                if windows is not None:
                    fill = fill.difference(windows)

        # inter-layer contacts: overlap, minus wherever dielectric covers it
        for i, (li, gi) in enumerate(self.nodes):
            for j in self._query(gi):
                lj, gj = self.nodes[j]
                if j <= i or lj == li:
                    continue
                ov = gi.intersection(gj)
                if ov.is_empty:
                    continue
                if fill is not None:
                    ov = ov.difference(fill)
                elif windows is not None:
                    ov = ov.intersection(windows)
                if ov.area >= MIN_CONTACT_AREA:
                    self.dsu.union(i, j)

        # pin name -> node, node -> pins
        self.pin_node: Dict[str, int] = {}
        self._pin_box: Dict[str, Tuple[float, float, float, float]] = {}
        self.node_pins: Dict[int, List[str]] = {}
        for lay, g, name in pinned:
            for j in self._query(g):
                if self.nodes[j][0] == lay and self._geoms[j].intersects(g):
                    self.pin_node.setdefault(name, j)
                    self._pin_box.setdefault(name, g.bounds)
                    self.node_pins.setdefault(j, []).append(name)
                    break

    def pin_box(self, name):
        """(x0, y0, x1, y1) of the named pad, or None."""
        return self._pin_box.get(name)

    def _query(self, geom):
        if self._tree is None:
            return []
        idx = self._tree.query(geom)
        return [int(k) for k in idx]

    def node_at(self, x, y, prefer_layer=None, tol=0.0):
        """The copper piece under (x, y) -- on ``prefer_layer`` if
        several layers have copper there, else the highest-numbered
        conductor (the one drawn on top) -- or None."""
        from shapely.geometry import Point
        p = Point(float(x), float(y))
        hits = [j for j in self._query(p) if self._geoms[j].intersects(p)]
        if not hits and tol:
            # nothing exactly under the point: take the nearest piece in reach
            near = [(self._geoms[j].distance(p), j) for j in self._query(p.buffer(tol))]
            near = [(d, j) for d, j in near if d <= tol]
            if not near:
                return None
            dmin = min(d for d, _ in near)
            hits = [j for d, j in near if d <= dmin + 1e-6]
        if not hits:
            return None
        if prefer_layer is not None:
            on = [j for j in hits if self.nodes[j][0] == prefer_layer]
            if on:
                return on[0]
        # a numbered pad beats a plain trace, then the top layer
        return max(hits, key=lambda j: (j in self.node_pins, self.nodes[j][0]))

    def trace_node(self, node, seed="") -> NetTrace:
        root = self.dsu.find(node)
        members = [k for k in range(len(self.nodes)) if self.dsu.find(k) == root]
        pins = sorted({p for k in members for p in self.node_pins.get(k, [])},
                      key=_pin_sort_key)
        return NetTrace(pieces=[self.nodes[k] for k in members], pins=pins, seed=seed)

    def trace_point(self, x, y, prefer_layer=None, tol=0.0) -> Optional[NetTrace]:
        n = self.node_at(x, y, prefer_layer=prefer_layer, tol=tol)
        return None if n is None else self.trace_node(n, seed=f"{int(x)},{int(y)}")

    def trace_net_name(self, net: str, pad_nets: Dict[str, str]) -> Optional[NetTrace]:
        """Every piece of copper that carries a pad of design net ``net``
        (all its routed fragments, even when they aren't joined yet), as
        one NetTrace; None if no such pad exists on the conductors."""
        wanted = {p for p, n in pad_nets.items() if n.upper() == net.upper()}
        roots = {self.dsu.find(self.pin_node[p]) for p in wanted if p in self.pin_node}
        if not roots:
            return None
        members = [k for k in range(len(self.nodes)) if self.dsu.find(k) in roots]
        pins = sorted({p for k in members for p in self.node_pins.get(k, [])},
                      key=_pin_sort_key)
        return NetTrace(pieces=[self.nodes[k] for k in members], pins=pins, seed=net)

    def trace_pin(self, name: str) -> Optional[NetTrace]:
        n = self.pin_node.get(name)
        if n is None:
            low = {k.upper(): v for k, v in self.pin_node.items()}
            n = low.get(name.upper())
        return None if n is None else self.trace_node(n, seed=name)


def _pin_sort_key(name):
    import re
    ref, _, pin = name.partition(".")
    m = re.match(r"([A-Za-z]*)(\d*)", ref)
    pm = re.match(r"(\d+)", pin)
    return (m.group(1), int(m.group(2) or 0), int(pm.group(1)) if pm else 0, pin)
