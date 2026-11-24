# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Hybrid (chip-and-wire) commands and canvas tools: fix vias, bond header,
unroute, die-bond fan-out, I/O pads, unused bonds, the dielectric refill
after a via move, auto-route / bond / finger / resistor / via / net tools,
and holding locked pads while a die moves.

Part of hlayout.gui.app.App (a mixin): these methods are App's own --
call them as always."""

import os

from . import dialogs
from ..core.errors import LayoutError
from ..core.units import dbu_to_mm


class HybridCommandsMixin:
    def _on_route_via(self, x, y, layer_a, layer_b, via_cell, segment=None):
        """Wired as canvas.on_route_via -- fired when Space drops a via
        mid-route (see LayoutCanvas._route_drop_via()). `segment`, if
        given, is (layer, points, width, cap) for the path drawn so far
        on the layer being left -- committed in the same Session.batch()
        as the via so the whole space-press is one undo step. The via
        itself is an instance of `via_cell` (Setup > Route Layers...,
        built via Create > Via Wizard...) placed at (x, y) with
        Session.place_cell() -- not raw pad/dielectric-window shapes --
        so its footprint is whatever that cell's own geometry says.
        `layer_a`/`layer_b` are only used for the error message; the
        placement itself doesn't need them, since the via cell already
        carries its own layers."""
        if not via_cell:
            dialogs.show_error(
                "Route", "No via cell configured -- set one in Setup > Route Layers... "
                "(build one first with Create > Via Wizard... if none exists yet).",
                parent=self,
            )
            return
        try:
            with self.session.batch():
                if segment is not None:
                    seg_layer, points, width, cap = segment
                    self.session.path(seg_layer, points, width, cap=cap)
                self.session.place_cell(via_cell, (x, y))
        except LayoutError as e:
            dialogs.show_error("Route", f"Could not place via: {e}", parent=self)
            return
        # open the dielectric fill at the new via once routing pauses
        vm = getattr(self.canvas, "_vias_moved", None)
        if vm is not None:
            vm()

    def remove_dielectric_fill(self):
        """Tools > Remove Dielectric Fill: clear every shape on the
        dielectric layer (from Dielectric Fill Rules) in the current
        cell, so it can be regenerated."""
        try:
            removed = self.session.clear_dielectric()
        except LayoutError as exc:
            dialogs.show_error("Remove Dielectric Fill", str(exc), parent=self)
            return
        self.refresh_all()
        self.status.set(f"removed {removed} dielectric polygon(s)")

    def _auto_route_two_pins(self):
        """Edit > Auto-Route Two Pins: enter the two-click auto-router --
        click the first pin, click the second, and a clear orthogonal
        trace is routed between them on the 1st routing conductor (Route
        Layers layer_a), around every other shape on that layer, snapped
        to the current grid and drawn at the current path width. Stays
        armed for the next pair; Esc exits. See _on_auto_route() /
        Session.auto_route()."""
        self.canvas.start_auto_route()

    def _on_auto_route(self, a, b):
        """Wired as canvas.on_auto_route -- fired by the two-click
        Auto-Route tool once the second pin is clicked. `a` / `b` are
        each a picked pad shape or a snapped (x, y) point."""
        try:
            path = self.session.auto_route(
                a, b,
                grid=int(self.canvas.grid_dbu), width=int(self.canvas.path_width),
            )
        except LayoutError as e:
            dialogs.show_error("Auto-Route", str(e), parent=self)
            return
        self.refresh_all()
        self.status.set(
            f"auto-routed: {len(path.points)}-point trace on "
            f"{self._layer_label(path.layer)} "
            f"(grid {dbu_to_mm(self.canvas.grid_dbu):g} mm, "
            f"width {dbu_to_mm(self.canvas.path_width):g} mm) -- "
            f"click two more pins, or Esc to stop"
        )

    def _move_wire_bond_tool(self):
        """Edit > Move Wire Bond: click near the END of a bond you want
        to move (the closer end is picked), then click its new position
        -- that end and its disc follow, the other end stays. The new
        span must stay within min/max and the bond still may not cross
        another. Stays armed; Esc exits. See _on_canvas_commit() /
        Session.move_wire_bond()."""
        self.canvas.start_move_bond()

    def _delete_wire_bond_tool(self):
        """Edit > Delete Wire Bond: click on (or near) a bond wire to
        remove it -- connector and both end discs. Stays armed; Esc
        exits. See _on_canvas_commit()/Session.delete_wire_bond()."""
        self.canvas.start_delete_bond()

    def _highlight_net_tool(self):
        """Edit > Highlight Net: click a pad or trace and every pad and
        track on its net lights up (see Session.highlight_net()). Stays
        armed; Esc stops; Edit > Clear Net Highlight removes it."""
        self.canvas.start_highlight_net()

    def _clear_net_highlight(self):
        self.session.clear_net_highlight()
        self.status.set("net highlight cleared")

    def _net_summary(self, tr, limit=12):
        from .commands import net_summary
        return net_summary(tr, limit=limit).replace("\n  ", " -- ")

    def _move_finger_tool(self):
        """Edit > Move Bond Finger: click a bond finger or jumper land of an
        imported routed hybrid, then its new spot -- its wires follow and
        are checked against the bond rules live. See
        Session.move_bond_finger()."""
        self.canvas.start_move_finger()

    def _resize_resistor_tool(self):
        """Edit > Resize Resistor: click an imported printed resistor, then
        move along its length with the value read out live; click to apply.
        See Session.resize_resistor()."""
        self.canvas.start_resize_resistor()

    def _delete_via_tool(self):
        """Edit > Delete Via: click a via to remove its pads and
        dielectric window. Stays armed; Esc exits. See
        _on_canvas_commit()/Session.delete_via()."""
        self.canvas.start_delete_via()

    def fix_vias(self):
        """Tools > Fix Vias...: redraw every via to the design's one via
        definition (Session.fix_vias)."""
        R = self.session.freerouting_rules()
        sig, pwr = self.session.via_spec(False, R), self.session.via_spec(True, R)
        if not dialogs.ask_yes_no(
                "Fix Vias",
                f"Redraw every via to the design's via definition?\n\n"
                f"  signal via: pad {sig['pad'] / 1000:.2f} mm, window {sig['window'] / 1000:.2f} mm\n"
                f"  power via:  pad {pwr['pad'] / 1000:.2f} mm, window {pwr['window'] / 1000:.2f} mm\n\n"
                "(pads from the Autoroute rules, windows = pad + 2 x the Dielectric tab's via "
                "window margin). The Route tool's via cell is rebuilt the same way and the "
                "dielectric fill is redone.", parent=self):
            return
        r = self.session.fix_vias(cell_name=self.canvas.cell_name)
        self.status.set(f"vias redrawn: {r['vias']} via(s), {r['via_cells']} via cell(s)")
        self.refresh_all()

    def bond_header(self):
        """Tools > Bond Header Pins...: an I/O pad at the substrate edge and a
        header bond for each header pin with an I/O net (see
        Session.bond_header)."""
        from .. import scripting as _s  # noqa: F401
        from ..scripting import iopads
        from ..io.techfile import load_net_names
        cell = self.session.library.get_cell(self.canvas.cell_name)
        try:
            hp = iopads.header_pins(self.session, cell)
        except Exception as e:
            dialogs.show_error("Bond Header Pins", str(e), parent=self)
            return
        if not hp:
            dialogs.show_error("Bond Header Pins", "No header pins found -- place the package "
                               "(a part with numbered pads off the substrate) first.", parent=self)
            return
        io = {k[3:] for k in load_net_names(self.session.library) if k.startswith("IO.")}
        used = [p for p in hp if p in io]
        if not dialogs.ask_yes_no(
                "Bond Header Pins",
                f"{len(hp)} header pins found, {len(used)} with an I/O net.\n\n"
                "Draw an I/O pad at the substrate edge and a header bond for "
                + (f"the {len(used)} pins with an I/O net?" if used else "every pin?")
                + "\n\nPad size and edge margin: Setup > Technology Properties, I/O Pads "
                "tab. A pin's old pad and bond are replaced. (IOPADS BOND all=1 bonds "
                "every pin.)",
                parent=self):
            return
        try:
            r = self.session.bond_header(cell_name=self.canvas.cell_name)
        except Exception as e:
            dialogs.show_error("Bond Header Pins", str(e), parent=self)
            return
        msg = f"Header bonded: {r['pads']} pads and bonds on layer {r['layer']}"
        if r["skipped"]:
            msg += "; skipped " + ", ".join(f"pin {p} ({why})" for p, why in
                                            list(r["skipped"].items())[:4])
        self.status.set(msg)
        self.refresh_all()

    def unroute_all(self):
        """Tools > Unroute All: delete the routing, back to the SLI (Ctrl+Z undoes)."""
        try:
            r = self.session.unroute(cell_name=self.canvas.cell_name)
        except LayoutError as e:
            dialogs.show_error("Unroute All", str(e), parent=self)
            return
        self.canvas.recalc_sli()
        self.refresh_all()
        self.status.set(f"Unrouted: {r['tracks']} track(s), {r.get('vias', 0)} via(s) and "
                        f"{r['added']} router-added "
                        "shape(s) deleted -- Ctrl+Z to undo")

    def fan_out_die_bonds(self):
        """Tools > Fan Out Die Bonds...: a bond finger and a wire bond for
        every die pad with a net, round each placed bare die (see
        Session.fan_out_die_bonds / FANOUT)."""
        from ..scripting import fanout
        from ..io.techfile import load_bond_rules, load_net_names
        lib = self.session.library
        cell = lib.get_cell(self.canvas.cell_name)
        pad_layers = [int(l) for l in (load_bond_rules(lib).get("die_pad_layers") or [])
                      if int(l) >= 0]
        if not pad_layers:
            dialogs.show_error(
                "Fan Out Die Bonds",
                "No die pad layers are set. Set the layer the bare dies' bond pads are on "
                "(e.g. 58) in Setup > Technology Properties, Wire Bonds tab.", parent=self)
            return
        names = load_net_names(lib)
        lines = []
        for r in cell.srefs:
            if not r.label:
                continue
            pads = fanout.die_pads(lib, r.cell_name, pad_layers)
            if pads:
                n = sum(1 for p in pads if not fanout._unconnected(names.get(f"{r.label}.{p}")))
                lines.append(f"  {r.label} ({r.cell_name}): {n} of {len(pads)} pads have a net")
        if not lines:
            dialogs.show_error("Fan Out Die Bonds",
                               "No bare dies are placed in this cell -- place the parts "
                               "(Tools > Auto Place...) first.", parent=self)
            return
        if not dialogs.ask_yes_no(
                "Fan Out Die Bonds",
                "Draw a bond finger and a wire bond for every die pad with a net:\n\n"
                + "\n".join(lines)
                + "\n\nFingers 250 x 400 um, 150 um apart, 250 um clear of the die, then each "
                "moved within the bond length and angle rules towards its net; a die's "
                "earlier fan-out is replaced. One undo step. (FANOUT sets other sizes; "
                "FANOUT all=1 bonds every pad; FANOUT fit=0 keeps neat rows.)", parent=self):
            return
        try:
            r = self.session.fan_out_die_bonds(cell_name=self.canvas.cell_name)
        except LayoutError as e:
            dialogs.show_error("Fan Out Die Bonds", str(e), parent=self)
            return
        msg = (f"Fanned out: {r['fingers']} fingers + bonds ("
               + ", ".join(f"{d} {len(p)}" for d, p in r["dies"].items()) + ")")
        fb = r.get("fitted") or {}
        if fb.get("fingers"):
            msg += (f"; {fb['fingers']} fitted towards their nets, "
                    f"{fb['saved_um'] / 1000:.1f} mm of net length saved")
        warn = []
        if r["skipped"]:
            warn.append("Not bonded:\n" + "\n".join(f"  {p}: {w}" for p, w in r["skipped"].items()))
        if r["overlaps"]:
            warn.append("Fingers over another part -- move the part or the finger (Edit > Move "
                        "Bond Finger):\n  " + ", ".join(r["overlaps"]))
        self.status.set(msg)
        self.refresh_all()
        if warn:
            dialogs.show_warning("Fan Out Die Bonds", msg + "\n\n" + "\n\n".join(warn),
                                 parent=self)

    def fit_bonds(self):
        """Tools > Fit Bonds to Nets: move the bond fingers within the bond
        length and angle rules towards their nets (Session.fit_bonds /
        FITBONDS). One undo step."""
        try:
            r = self.session.fit_bonds(cell_name=self.canvas.cell_name)
        except LayoutError as e:
            dialogs.show_error("Fit Bonds to Nets", str(e), parent=self)
            return
        self.refresh_all()
        if r["fingers"]:
            self.status.set(f"{r['fingers']} of {r['checked']} bond finger(s) moved towards their "
                            f"nets, {r['saved_um'] / 1000:.1f} mm of net length saved")
        else:
            self.status.set(f"{r['checked']} bond finger(s) checked: none could move closer "
                            "to its net")

    def assign_io_pads(self):
        """Tools > Assign I/O Pads...: optimise the module pinout -- each
        I/O net onto the header pin / I/O pad giving the shortest,
        uncrossed connection to its dice -- then (on Yes) draw the pads at
        the header-bond landings, rename the nets and write io_pinout.csv
        next to the design (see Session.assign_io_pads)."""
        try:
            p = self.session.assign_io_pads(cell_name=self.canvas.cell_name, apply=False)
        except Exception as e:
            dialogs.show_error("Assign I/O Pads", str(e), parent=self)
            return
        moved = [st for st in p["sites"] if st["net"] != st.get("was")]
        lines = "\n".join(f"  pin {st['pin']}: {st.get('was') or '-'} -> {st['net'] or '-'}"
                          for st in moved[:24])
        more = f"\n  ... and {len(moved) - 24} more" if len(moved) > 24 else ""
        if not dialogs.ask_yes_no(
                "Assign I/O Pads",
                f"Pad-to-die distance: {p['before'] / 1000:.0f} mm now, "
                f"{p['after'] / 1000:.0f} mm optimised.\n\n{len(moved)} pin(s) change net:\n"
                f"{lines}{more}\n\nThis changes the module pinout (update the schematic's "
                "header pin numbers from io_pinout.csv). Draw the pads and apply it?",
                parent=self):
            return
        path = getattr(self, "current_path", None)
        csv_path = os.path.join(os.path.dirname(path), "io_pinout.csv") if path else None
        r = self.session.assign_io_pads(cell_name=self.canvas.cell_name, csv_path=csv_path)
        self.status.set(f"I/O pads assigned: {len(r['sites'])} pads, pad-to-die "
                        f"{r['before'] / 1000:.0f} -> {r['after'] / 1000:.0f} mm"
                        + (f"; pinout written to {r['csv']}" if r.get("csv") else ""))
        self.refresh_all()

    def remove_unused_bonds(self):
        """Tools > Remove Unused Bonds...: list the die pads the design
        doesn't use, then (on OK) take their wire bonds and bond fingers
        off -- each affected die gets its own copy of its part (see
        Session.remove_unused_bonds)."""
        try:
            plan = self.session.remove_unused_bonds(cell_name=self.canvas.cell_name, dry_run=True)
        except LayoutError as e:
            dialogs.show_error("Remove Unused Bonds", str(e), parent=self)
            return
        if not plan["dies"]:
            dialogs.show_info("Remove Unused Bonds", "Every bonded die pad is on a net -- "
                              "nothing to remove.", parent=self)
            return
        lines = "\n".join(f"  {ref}: pad(s) {', '.join(p)}" for ref, p in sorted(plan["dies"].items()))
        if not dialogs.ask_yes_no(
                "Remove Unused Bonds",
                f"{plan['bonds']} bond(s) and finger(s) on pads no net uses:\n\n{lines}\n\n"
                "Each of these dies gets its own copy of its part without them (the library "
                "part and other dies are untouched). Remove them?", parent=self):
            return
        r = self.session.remove_unused_bonds(cell_name=self.canvas.cell_name)
        self.status.set(f"removed {r['bonds']} unused bond(s) and finger(s) from "
                        f"{len(r['dies'])} die(s)")
        self.refresh_all() if hasattr(self, "refresh_all") else self.canvas.redraw()

    def _refill_dielectric_after_via_move(self):
        """A via moved with its track (segment drag / arrow nudge): redo the
        dielectric fill, if the cell has one, so the via's window is open
        where the via now is."""
        from ..io.techfile import dielectric_layer_number
        name = self.canvas.cell_name
        cell = self.session.library.cells.get(name)
        diel = dielectric_layer_number(self.session.library)
        if cell is None or diel < 0:
            return
        if not self.session._has_full_dielectric(cell, diel):
            # no fill, or the router's local crossover strips: regenerating
            # would flood the substrate with a full sheet -- leave them
            if any(s.layer == diel for s in cell.shapes):
                self.status.set("via moved -- local dielectric strips left as they are "
                                "(re-route, or check the strip at that via)")
            return
        self.status.set("via moved -- regenerating the dielectric...")
        self.update_idletasks()
        try:
            self.session.generate_dielectric(cell_name=name)
            self.status.set("via moved -- dielectric regenerated round its new window")
        except Exception as e:
            self.status.set(f"via moved -- dielectric NOT regenerated: {e}")

    # --- a die's wire bonds and fingers follow it ---------------------------
    def _die_placements(self):
        """(cell, {id(sref): (label, cell_name, frozen transform, placement)})
        of every labelled instance in the current cell, taken at a canvas
        checkpoint so _after_canvas_edit can tell what moved or turned."""
        try:
            import types
            from ..scripting.autoplace_session import _xf
            name = self.session.current_cell_name
            cell = self.session.library.cells.get(name) if name else None
            if cell is None:
                return None
            out = {}
            for r in cell.srefs:
                if not r.label:
                    continue
                pl = (tuple(r.origin), float(r.rotation), bool(r.x_reflection),
                      float(r.magnification or 1.0))
                frozen = types.SimpleNamespace(origin=pl[0], rotation=pl[1],
                                               x_reflection=pl[2], magnification=pl[3])
                out[id(r)] = (r.label, r.cell_name, _xf(frozen), pl)
            return name, out
        except Exception:
            return None

    def _locked_pad_shapes(self):
        """(cell name, [(shape, points)]) of the locked I/O pads ("IO.<pin>"
        in Session.locked_parts) in the current cell, taken at a canvas
        checkpoint so _after_canvas_edit can put them back."""
        try:
            name = self.session.current_cell_name
            cell = self.session.library.cells.get(name) if name else None
            if cell is None:
                return None
            pins = {r[3:] for r in self.session.locked_parts(name) if r.startswith("IO.")}
            if not pins:
                return None
            return name, [(sh, [tuple(p) for p in sh.points]) for sh in cell.shapes
                          if str(getattr(sh, "pin", "")) in pins and hasattr(sh, "points")]
        except Exception:
            return None

    def _hold_locked_pads(self):
        """Put back any locked I/O pad a canvas edit moved, reshaped or
        deleted. Returns the pad numbers held."""
        snap, self._pad_place = getattr(self, "_pad_place", None), None
        if not snap:
            return []
        name, pads = snap
        cell = self.session.library.cells.get(name)
        if cell is None:
            return []
        try:        # a pad unlocked in this edit (Edit Selected) may change
            still = {r[3:] for r in self.session.locked_parts(name) if r.startswith("IO.")}
        except Exception:
            still = set()
        held = []
        for sh, pts in pads:
            pin = str(getattr(sh, "pin", ""))
            if pin not in still:
                continue
            if sh in cell.shapes:
                if [tuple(p) for p in sh.points] != pts:
                    sh.points = list(pts)
                    held.append(pin)
                continue
            # gone -- deleted, or swapped for a polygon by a vertex edit
            twin = [x for x in cell.shapes if x is not sh and str(getattr(x, "pin", "")) == pin
                    and x.layer == sh.layer and type(x) is not type(sh)]
            sh.points = list(pts)
            if twin:
                cell.shapes[cell.shapes.index(twin[0])] = sh
            else:
                cell.shapes.append(sh)
            held.append(pin)
        return held
