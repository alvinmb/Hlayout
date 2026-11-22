# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Auto Place: placement settings, the place runs, routability checks and
the substrate / I/O-ring / boundary boxes placement works inside.

Part of hlayout.scripting.api.Session (a mixin): these methods are
Session's own -- call them on a Session as always."""

from typing import Optional

from ..io.techfile import load_route_rules


class AutoPlaceMixin:
    # --- Auto Place -----------------------------------------------------------
    def autoplace_settings(self) -> dict:
        """The design's saved Auto Place settings (grid, clearance, edge,
        bond_reach, thermal, effort, seed, resistor_layer, footprint_map,
        weights, class_weights, two_pin_weight, die_net_weight,
        family_power_mw). See hlayout.scripting.autoplace_session."""
        from .autoplace_session import load_settings
        return load_settings(self.library)

    def set_autoplace_settings(self, **settings) -> dict:
        """Update and save some Auto Place settings; returns them all."""
        from .autoplace_session import save_settings
        return save_settings(self.library, settings)

    def open_channels(self, netlist=None, passes: int = 6, cell_name: Optional[str] = None,
                      on_line=None) -> dict:
        """After Auto Place (and Fan Out Die Bonds): move only the parts that
        wall pads in -- each pass checks routability, moves a blocking part
        straight away from its wall by the wall's length where it fits, and
        is kept only if fewer connections are walled in (one undo step per
        kept pass). Locked parts never move. -> {"passes", "moves",
        "walled_before", "walled_after", "log", "report"}. See
        hlayout.scripting.open_channels."""
        from .open_channels import open_channels
        return open_channels(self, netlist=netlist, passes=passes, cell_name=cell_name,
                             on_line=on_line)

    def auto_place_tries(self, netlist, tries: int = 3, seeds=None,
                         cell_name: Optional[str] = None, on_line=None, **settings) -> dict:
        """Auto Place with ``tries`` seeds (or the given ``seeds``), keeping
        the placement the routability check rates best -- fewest walled-in
        connections, then crossings with no room for a crossover, then track
        demand, then net length. Bare dies are fanned out for the check and
        on the kept placement. -> {"seed", "tries", "legal", "fanned_out",
        ...}. See hlayout.scripting.autoplace_tries."""
        from .autoplace_tries import auto_place_tries
        r = auto_place_tries(self, netlist, tries=tries, seeds=seeds, cell_name=cell_name,
                             on_line=on_line, **settings)
        r.pop("placer", None)
        r.pop("info", None)
        return r

    def check_routability(self, cell_name: Optional[str] = None, text: bool = False):
        """Will the placement route? In seconds, without routing: the
        connections no path can ever reach (walled in by the parts round
        them, no room for a crossover) with the parts in the way, the SLI
        crossings with no room for a crossover, and the track demand
        against the free space (RUDY). Run it after Fan Out Die Bonds and
        Bond Header. -> the report dict (``text``: as text). See
        hlayout.scripting.routability."""
        from .routability import check, to_text
        rep = check(self, cell_name=cell_name)
        return to_text(rep) if text else rep

    def auto_place(self, netlist, overrides: Optional[str] = None, cell_name: Optional[str] = None,
                   improve: bool = False, only=None, on_progress=None, cancel=None,
                   **settings) -> dict:
        """Place the netlist's parts in the current (or named) cell for the
        shortest weighted straight-line net length, with hot parts kept
        apart and nothing overlapping, on the placement grid -- one undo
        step. ``netlist`` = a KiCad .net (or CSV / Tango) path or a parsed
        hlayout.io.kicad_netlist.Netlist; ``overrides`` = an optional CSV
        (ref, x, y, rotation, locked, weight, power_mw, layer, family, cell).
        Parts already placed (an instance labelled with the ref) start
        where they are; ``improve`` keeps that start for every part and
        only refines it; ``only`` = the refs allowed to move. Extra
        keyword ``settings`` override the saved ones for this run (grid,
        thermal, effort, seed, weights, ...). The netlist's pad nets are
        merged into the design's net names.
        Returns {"placed", "fixed", "generated", "skipped", "unmapped",
        "missing_pins", "length_before", "length_after", "cost_before",
        "cost_after", "overlaps", "outside", "legal", "report"} -- the
        lengths are the weighted SLI length; the cost is what the placer
        minimises: that length plus the SLI-crossing, congestion, spacing
        and thermal terms, so the length can rise where the crossings
        fall (``*_before`` None when not every part started placed).
        ``report`` the full text report
        (problems with a suggested move each, SLI crossings, longest nets). ``legal`` False: the parts did not fit --
        overlapping footprints (their pads touching: shorts) or parts off
        the substrate; the placement is applied so it can be fixed by hand.
        Footprints never come closer than the copper spacing (the Autoroute
        clearance, raised to the DRC spacing), whatever the clearance setting."""
        from .autoplace_session import apply, build_job
        placer, info = build_job(self, netlist, cell_name=cell_name, overrides=overrides,
                                 settings=settings, only=set(only) if only else None)
        started = all(p.placed for p in placer.parts)
        before = placer.wirelength() if started else None
        cost_before = placer.cost() if started else None
        placer.run(effort=float(info["settings"]["effort"]), improve=improve,
                   on_progress=on_progress, cancel=cancel)
        bad, out = placer.overlaps()
        with self._batch():
            apply(self, placer, info)
        self._notify()
        from .autoplace_report import build_report, to_text
        try:
            report = to_text(build_report(self, placer, info))
        except Exception as e:                   # the placement stands either way
            report = f"(no report: {e})"
        return {"placed": sum(1 for p in placer.parts if not p.fixed),
                "fixed": sum(1 for p in placer.parts if p.fixed),
                "generated": info["generated"], "skipped": info["skipped"],
                "unmapped": info["unmapped"], "missing_pins": info["missing_pins"],
                "length_before": before, "length_after": placer.wirelength(),
                "cost_before": cost_before, "cost_after": placer.cost(),
                "overlaps": bad, "outside": out,
                # overlapping footprints include their pads: a short circuit
                "legal": not bad and not out,
                # the full report (Auto Place window, Report tab) as text
                "report": report}

    def sli(self, cell_name: Optional[str] = None, nets=None, unrouted_only: bool = True) -> dict:
        """Straight-line interconnects (SLIs) of the design as placed: for
        every named net, the minimum spanning tree over its pads.
        -> {"nets": [{"net", "length", "segments"}], "total"} (um)."""
        from .autoplace_session import sli as _sli
        return _sli(self, cell_name=cell_name, nets=nets, unrouted_only=unrouted_only)

    def _substrate_box(self, cell):
        """The substrate outline (boundary layer) of ``cell`` as a shapely
        box, or None."""
        from shapely.geometry import box as _box
        lay = self._boundary_layer()
        bb = self._boundary_bbox(cell, lay) if lay is not None else None
        return _box(*bb) if bb else None

    def _io_ring_box(self, cell):
        """The area inside the module I/O pad ring -- pin-labelled
        rectangles drawn in ``cell`` itself -- bounded by the pads' inner
        edges and grown by the Freerouting io_entry_depth (the strip the
        tracks enter by, which stays under the dielectric). A side with no
        pads takes the substrate edge. None without I/O pads."""
        from shapely.geometry import box as _box
        from ..core.shapes import Rectangle
        from .autoplace_session import io_pads
        pads = [b for _pin, b in io_pads(self.library, cell, self._boundary_layer())]
        if len(pads) < 2:
            return None
        sub = self._substrate_box(cell)
        if sub is not None:
            sx0, sy0, sx1, sy1 = sub.bounds
        else:
            sx0 = min(b[0] for b in pads); sy0 = min(b[1] for b in pads)
            sx1 = max(b[2] for b in pads); sy1 = max(b[3] for b in pads)
        mx, my = (sx0 + sx1) / 2.0, (sy0 + sy1) / 2.0
        hx, hy = max(sx1 - sx0, 1.0) / 2.0, max(sy1 - sy0, 1.0) / 2.0
        left, right, bottom, top = [], [], [], []
        for x0, y0, x1, y1 in pads:
            dx, dy = ((x0 + x1) / 2.0 - mx) / hx, ((y0 + y1) / 2.0 - my) / hy
            if abs(dx) >= abs(dy):
                (right if dx > 0 else left).append((x0, x1))
            else:
                (top if dy > 0 else bottom).append((y0, y1))
        try:
            entry = float(self.freerouting_rules().get("io_entry_depth") or 0)
        except Exception:
            entry = 0.0
        # each side: the pads' inner edge, moved OUT by the entry strip
        x0 = max(b[1] for b in left) - entry if left else sx0
        x1 = min(b[0] for b in right) + entry if right else sx1
        y0 = max(b[1] for b in bottom) - entry if bottom else sy0
        y1 = min(b[0] for b in top) + entry if top else sy1
        if x1 <= x0 or y1 <= y0:
            return None
        return _box(x0, y0, x1, y1)

    def _boundary_layer(self):
        rr = load_route_rules(self.library)
        b = rr.get("boundary_layer")
        if isinstance(b, int) and b >= 0:
            return b
        # a layer named like a substrate outline
        for n, lay in enumerate(self.library.layers):
            nm = (getattr(lay, "name", "") or "").lower()
            if "substrate" in nm or "outline" in nm or "boundary" in nm:
                cell = self.library.cells.get(self.current_cell_name)
                if cell is not None and self._boundary_bbox(cell, n) is not None:
                    return n
        return None

    def _rules_with_drc(self, R) -> dict:
        """The Freerouting rules, tightened to the design's own DRC rules
        on the routing layers: the clearance is at least the largest
        MIN_SPACE / MIN_SPACE_BETWEEN, the track widths at least the
        largest MIN_WIDTH -- so the router can't produce a layout the
        design's own Tools > DRC then flags."""
        R = dict(R)
        try:
            from ..io.techfile import load_drc_rules
            drc = load_drc_rules(self.library)
        except Exception:
            return R
        lays = set(self._route_layers())
        space = width = 0.0
        for r in drc:
            d = r if isinstance(r, dict) else getattr(r, "__dict__", {})
            kind = str(d.get("kind", "")).upper()
            la_, lb_ = d.get("layer_a"), d.get("layer_b")
            try:
                v = float(d.get("value") or 0)
            except (TypeError, ValueError):
                continue
            if kind == "MIN_WIDTH" and la_ in lays:
                width = max(width, v)
            elif kind == "MIN_SPACE" and la_ in lays:
                space = max(space, v)
            elif kind == "MIN_SPACE_BETWEEN" and la_ in lays and (lb_ in lays or lb_ is None):
                space = max(space, v)
        if space > float(R.get("clearance", 0)):
            R["clearance"] = space
        for k in ("signal_width", "power_width"):
            if width > float(R.get(k, 0)):
                R[k] = width
        return R
