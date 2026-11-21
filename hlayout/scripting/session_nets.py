# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Nets: shorts, net tracing and highlight, design net names, netlist
import, extraction and export.

Part of hlayout.scripting.api.Session (a mixin): these methods are
Session's own -- call them on a Session as always."""

from typing import List, Optional

from ..core.errors import LayoutError
from ..io.techfile import (
    dielectric_layer_number,
    load_bond_rules,
    load_netlist_settings,
    load_route_rules,
)


class NetsMixin:
    def find_shorts(self, cell_name: Optional[str] = None) -> list:
        """Short circuits: copper that joins pads of two or more design
        nets (net names, see import_net_names()). Spacing DRC can't see
        these -- touching copper of two nets is one shape to it. Returns
        [{"nets": [names], "pins": {net: [pads]}, "at": [(x, y)]}] -- ``at``
        the via windows / crossings where the nets' copper meets."""
        from collections import deque
        from shapely.geometry import box as _box
        from shapely.ops import unary_union
        from ..io.techfile import load_net_names
        names = load_net_names(self.library)
        if not names:
            return []
        name = cell_name or self.current_cell_name
        tr = self.net_tracer(cell_name=name)
        groups = {}
        for p, n in names.items():
            if p in tr.pin_node:
                groups.setdefault(tr.dsu.find(tr.pin_node[p]), {}).setdefault(n, []).append(p)
        out = []
        cell = self.library.get_cell(name)
        diel = dielectric_layer_number(self.library)
        wins = unary_union([_box(*sh.bbox()) for sh in cell.shapes if sh.layer == diel
                            and getattr(sh, "datatype", 0) == 0 and hasattr(sh, "bbox")]) \
            if diel >= 0 else None
        nodes, geoms = tr.nodes, tr._geoms
        for root, by_net in groups.items():
            if len(by_net) < 2:
                continue
            # where do the nets meet? grow each net out from its own pads
            lab, q, at = {}, deque(), []
            for n, pins in by_net.items():
                for p in pins:
                    j = tr.pin_node[p]
                    if j not in lab:
                        lab[j] = n
                        q.append(j)
            while q and len(at) < 6:
                j = q.popleft()
                for k in tr._query(geoms[j]):
                    if k == j or not geoms[k].intersects(geoms[j]):
                        continue
                    ov = geoms[k].intersection(geoms[j])
                    if nodes[k][0] != nodes[j][0] and wins is not None and \
                            ov.intersection(wins).area < 1:
                        continue
                    if k not in lab:
                        lab[k] = lab[j]
                        q.append(k)
                    elif lab[k] != lab[j]:
                        c = ov.representative_point()
                        xy = (round(c.x), round(c.y))
                        if xy not in at:
                            at.append(xy)
            out.append({"nets": sorted(by_net), "pins": {n: sorted(v) for n, v in by_net.items()},
                        "at": at})
        return out

    def net_tracer(self, layers=None, cell_name: Optional[str] = None):
        """A hlayout.core.nettrace.NetTracer for ``cell_name`` (default:
        the current cell) over ``layers`` -- default: Setup > Route
        Layers' two conductors (layer_a/layer_b), falling back to the
        Tools > Extract Netlist... conductor selection -- with the
        dielectric fill from Setup > Dielectric Fill Rules... deciding
        where the two layers actually connect (via windows only)."""
        from ..core.nettrace import NetTracer
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        resolve = self.library.layers.resolve
        if layers is None:
            rr = load_route_rules(self.library)
            layers = [l for l in (rr.get("layer_a"), rr.get("layer_b"))
                      if isinstance(l, int) and l >= 0]
            if not layers:
                layers = list(load_netlist_settings(self.library).get("conductor_layers") or [])
        layers = [resolve(l) for l in layers]
        if not layers:
            raise LayoutError("no conductor layers -- pass layers=... or set Setup > Route Layers...")
        diel = dielectric_layer_number(self.library)
        if diel in layers:
            diel = -1
        return NetTracer(self.library, self.library.get_cell(name), layers,
                         dielectric_layer=diel if diel >= 0 else None,
                         fill_datatype=self.DIELECTRIC_FILL_DATATYPE)

    def highlight_net(self, target, layers=None, cell_name: Optional[str] = None,
                      prefer_layer=None, tol: float = 0.0, notify: bool = True):
        """Trace the net of ``target`` -- an (x, y) DBU point on a pad or
        trace, a pad name "RefDes.Pin" (a pad drawn directly in the cell
        is "IO.<pin>"), or a design net name (see set_net_names() /
        import_net_names(); every routed fragment of that net is
        highlighted, joined or not; a point or pad on a named net shows the
        whole net the same way) -- and make it the current highlight
        (``self.net_highlight``; the GUI draws every piece of copper and
        every pad on it). Returns the hlayout.core.nettrace.NetTrace,
        with ``.name`` the design net name ("unnamed" if none is known,
        "A + B (SHORT)" if pads of two named nets are connected),
        ``.fragments`` the number of separate copper groups and
        ``.missing`` the named net's pads not on this copper. Raises
        LayoutError if there's no pad/track there or no such pad or net.
        ``tol`` (DBU) lets a click just beside a pad or thin track still
        pick it (the nearest piece within ``tol`` wins).
        Highlighting never edits the design. See clear_net_highlight()."""
        from ..io.techfile import load_net_names
        tracer = self.net_tracer(layers=layers, cell_name=cell_name)
        names = load_net_names(self.library)
        tr = None
        if isinstance(target, str):
            key = target.strip()
            tr = tracer.trace_pin(key)
            if tr is None:
                tr = tracer.trace_net_name(key, names)
            if tr is None:
                raise LayoutError(f"There is no pad or net called {key!r}. Use a pad name "
                                  f"like U1.5 (I/O pads are IO.<pin>) or a net name.")
        else:
            x, y = target
            tr = tracer.trace_point(x, y, prefer_layer=prefer_layer, tol=tol)
            if tr is None:
                lay = " or ".join(f"{n} ({self.library.layers[n].name})" for n in tracer.layers)
                raise LayoutError(f"Nothing to highlight at ({x / 1000:.3f}, {y / 1000:.3f}) mm: "
                                  f"click on a pad or track on layer {lay}.")
        # A click (or a pad name) traces the copper joined to that spot;
        # when its pads all carry one design net name, show EVERY piece
        # of that net -- the fragments not yet joined to this one too.
        # ``missing`` stays what isn't joined to the piece picked.
        tr.name_from(names)
        missing = tr.missing_pads(names)
        nets = {names[p].upper(): names[p] for p in tr.pins if p in names}
        if len(nets) == 1:
            full = tracer.trace_net_name(next(iter(nets.values())), names)
            if full is not None and len(full.pieces) > len(tr.pieces):
                full.seed = tr.seed
                tr = full
                tr.name_from(names)
        tr.fragments = len({tracer.dsu.find(tracer.pin_node[p]) for p in tr.pins
                            if p in tracer.pin_node}) or 1
        tr.missing = missing
        tr.cell_name = cell_name or self.current_cell_name
        tr.pad_boxes = [tracer.pin_box(p) for p in tr.pins if tracer.pin_box(p)]
        tr.trace_layers = layers
        self.net_highlight = tr
        if notify:
            self._notify()
        return tr

    def retrace_net_highlight(self):
        """Trace the highlighted net again on the design as it is now -- a
        part moved, a track added -- so the highlight follows the copper
        and pads it shows: by its design net name when it has one, else by
        one of its pads (which moves with its part). Keeps the old
        highlight if the net is gone. Returns the current highlight."""
        tr = getattr(self, "net_highlight", None)
        if tr is None:
            return None
        name = tr.name
        if len(tr.net_names) == 1:
            target = name
        elif tr.pins:
            target = tr.pins[0]
        else:
            return tr
        try:
            return self.highlight_net(target, layers=getattr(tr, "trace_layers", None),
                                      cell_name=getattr(tr, "cell_name", None), notify=False)
        except Exception:
            return tr

    def set_net_names(self, pad_nets: dict, replace: bool = True) -> int:
        """Store the design's intended netlist as {"RefDes.Pin": net}
        (module I/O pads are "IO.<pin>") -- what Highlight Net names a
        traced net by. ``replace=False`` merges into the existing map.
        Returns the number of named pads."""
        from ..io.techfile import load_net_names, save_net_names
        cur = {} if replace else load_net_names(self.library)
        cur.update({str(k): str(v) for k, v in pad_nets.items()})
        save_net_names(self.library, cur)
        return len(cur)

    def read_netlist(self, path: str, resistors: bool = True) -> dict:
        """Step 1 of the design flow: read the design's netlist (KiCad .net,
        CSV or Tango) -- every pad's net name stored, and every printed
        resistor put in the Resistor Designer's list to be designed before
        Auto Place lays the parts out. See
        hlayout.scripting.netlist_flow.read_design_netlist()."""
        from .netlist_flow import read_design_netlist
        return read_design_netlist(self, path, resistors=resistors)

    def import_net_names(self, path: str, replace: bool = True) -> int:
        """Read pad -> net names from a CSV with a header row: either a
        ``pad`` column ("U1.5") or ``ref``/``refdes`` + ``pin`` columns,
        plus a ``net`` (or ``net_name``/``signal``) column. Column names
        are case-insensitive. Returns the number of named pads stored
        (see set_net_names())."""
        import csv
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            raise LayoutError(f"{path}: no rows")
        cols = {c.lower().strip(): c for c in rows[0].keys() if c}
        net_c = next((cols[k] for k in ("net", "net_name", "netname", "signal") if k in cols), None)
        pad_c = next((cols[k] for k in ("pad", "refdes.pin", "pin_name") if k in cols), None)
        ref_c = next((cols[k] for k in ("ref", "refdes", "ref_des", "designator") if k in cols), None)
        pin_c = next((cols[k] for k in ("pin", "pin_number", "pad_number") if k in cols), None)
        if net_c is None or (pad_c is None and not (ref_c and pin_c)):
            raise LayoutError(f"{path}: need a 'net' column and 'pad' or 'ref'+'pin' columns "
                              f"(found {sorted(cols)})")
        out = {}
        for r in rows:
            net = (r.get(net_c) or "").strip()
            pad = (r.get(pad_c) or "").strip() if pad_c else \
                f"{(r.get(ref_c) or '').strip()}.{(r.get(pin_c) or '').strip()}"
            if net and pad and not pad.startswith(".") and not pad.endswith("."):
                out[pad] = net
        return self.set_net_names(out, replace=replace)

    def clear_net_highlight(self) -> None:
        """Remove the Highlight Net overlay."""
        if getattr(self, "net_highlight", None) is not None:
            self.net_highlight = None
            self._notify()

    def extract_netlist(self, conductor_layers=None, cell_name: Optional[str] = None,
                        include_bonds: bool = True) -> List["Net"]:
        """Extract the electrical netlist of `cell_name` (default: the
        current cell), as seen across `conductor_layers` -- an iterable
        of layer numbers or names (each resolved the same way every
        other layer argument in this API is). See hlayout.core.netlist
        for exactly how this works: every touching/overlapping piece of
        copper on those layers becomes one Net, and Rectangle.pin/
        Polygon.pin plus the owning SRef's .label identify each net's
        members as RefDes.Pin.

        `conductor_layers=None` uses the design's saved selection (the
        Conductor checkboxes in Tools > Extract Netlist...,
        techfile.load_netlist_settings()); otherwise pass the layers
        explicitly -- e.g. `s.extract_netlist([31])` for a
        design whose Resistor Geometry Rules... conductor_layer is 31,
        or `s.extract_netlist([0, 1])` for a two-layer Route (2-Layer)
        design.

        Returns a list of hlayout.core.netlist.Net (each has .id,
        .pins -- a list of NetPin(ref_des, pin) -- and
        .unresolved_count for members whose owning instance has no ref
        designator yet). A pin whose own shape isn't on any of
        `conductor_layers` is reported in one final Net(id="UNASSIGNED")
        rather than silently dropped -- see extract_netlist()'s own
        docstring. Requires the shapely package (see mask_op()).

        `include_bonds` (default True): if Setup > Technology Properties (Wire Bonds tab) has
        a bond layer configured, it's added to `conductor_layers`
        automatically so every WIRE BOND (wire_bond()/BOND) is treated
        as conductor and its net is reported straight through to the
        die pin it lands on. Only a bond's two ENDS connect (the wire
        flies over whatever lies between), and the rules' isolated pad
        layers (die pads, header pins), if set, are kept isolated from
        the substrate copper underneath -- see hlayout.core.netlist. Pass include_bonds=False to see
        only the conductor layers you named (bond layer then treated as
        plain copper only if you list it)."""
        from ..core.netlist import extract_netlist as _extract_netlist

        if conductor_layers is None:
            # The design's saved Tools > Extract Netlist... selection
            # (load_netlist_settings) -- the same layers the dialog's
            # Conductor checkboxes show.
            conductor_layers = load_netlist_settings(self.library)["conductor_layers"]
            if not conductor_layers:
                raise LayoutError(
                    "no conductor layers given and none saved -- pass a layer "
                    "list or tick them in Tools > Extract Netlist..."
                )
        cell = self.library.get_cell(cell_name) if cell_name else self.cell
        resolved = [self.library.layers.resolve(l) for l in conductor_layers]
        rules = load_bond_rules(self.library)
        die_pads = list(rules.get("die_pad_layers", []))
        b_layer = int(rules["layer"]) if include_bonds else -1
        return _extract_netlist(self.library, cell, resolved,
                                bond_layer=b_layer if b_layer >= 0 else None,
                                die_pad_layers=die_pads)

    def export_netlist(self, nets: List["Net"], path: str, fmt: Optional[str] = None,
                       cell_name: Optional[str] = None) -> str:
        """Write `nets` (from extract_netlist()) to a file another tool can
        read -- see hlayout.io.netlistio. `fmt` is "csv", "report",
        "kicad" or "tango"; None picks it from the extension (.csv, .txt,
        .net = KiCad, .tng = Tango/Protel). `cell_name` (default: the
        current cell) supplies the component list the EDA formats need.
        Returns the format written."""
        from ..io.netlistio import write_netlist
        from ..io.techfile import load_netlist_settings
        cell = cell_name or self.current_cell_name or ""
        layers = load_netlist_settings(self.library)["conductor_layers"]
        return write_netlist(nets, path, fmt=fmt, library=self.library, cell_name=cell,
                             design=self.library.name, layers=layers)

    def export_netlist_csv(self, nets: List["Net"], path: str) -> None:
        """Write `nets` (as returned by extract_netlist()) to a CSV file,
        one row per (net, ref_des, pin) -- net, ref_des, pin columns,
        matching the row-per-item convention of the Resistor Designer's
        own CSV export. A net with no pins at all (rare -- copper with
        no numbered pad touching it anywhere) still gets one row with
        empty ref_des/pin columns, so the net's existence isn't lost."""
        import csv

        with open(path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["net", "ref_des", "pin"])
            for net in nets:
                if not net.pins:
                    writer.writerow([net.id, "", ""])
                    continue
                for p in net.pins:
                    writer.writerow([net.id, p.ref_des, p.pin])
