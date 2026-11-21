# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Wire bonds: draw, find, move and delete bonds, their DRC rules and the
bond departure check.

Part of hlayout.scripting.api.Session (a mixin): these methods are
Session's own -- call them on a Session as always."""

import math
from typing import Optional

from ..core.errors import LayoutError
from ..io.techfile import load_bond_rules, load_drc_rules


def _point_seg_dist(p, a, b) -> float:
    """Shortest distance from point ``p`` to the segment ``a``-``b``
    (all (x, y) in DBU)."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    if seg2 < 1e-9:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _segments_cross(p1, p2, p3, p4) -> bool:
    """True only when segment p1-p2 and segment p3-p4 PROPERLY cross --
    i.e. each segment strictly straddles the other's line. Segments that
    merely share an endpoint, touch at a T, or lie collinear are NOT a
    crossing (a wire bond is allowed to share a landing pad with
    another). Integer/float DBU coordinates."""
    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    d1 = orient(p3, p4, p1)
    d2 = orient(p3, p4, p2)
    d3 = orient(p1, p2, p3)
    d4 = orient(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) \
        and d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0


class BondsMixin:
    def add_bond_drc_rules(self, angle: float = 45.0) -> list:
        """Save the wire-bond DRC rules made from Setup > Wire Bond
        Rules... into the technology file: BOND_MIN_LENGTH and
        BOND_MAX_LENGTH on the bond layer, and BOND_MAX_ANGLE (``angle``
        degrees) for each die-pad layer. Rules already saved are not
        added twice. Returns the DrcRule list added (Tools > DRC... >
        Add Bond Rules; DRCRULE BONDS)."""
        from ..core.drc import DrcRule, bond_rules_from_settings
        from ..io.techfile import load_bond_rules, save_drc_rules
        saved = load_drc_rules(self.library)
        new = bond_rules_from_settings(load_bond_rules(self.library),
                                       [DrcRule.from_dict(r) for r in saved], angle=angle)
        save_drc_rules(self.library, saved + [r.to_dict() for r in new])
        return new

    def bond_departures(self, cell_name: Optional[str] = None, pad_layer=None) -> list:
        """Every wire-bond end that lands on a bare-die pad, with the angle
        it leaves at: [{"a", "b", "end", "die", "angle_deg"}], ``a``/``b``
        the wire's ends and ``end`` (0 or 1) the one on the die.
        ``pad_layer`` defaults to each Setup > Wire Bond Rules die-pad
        layer. The angle is measured from the outward normal of the die
        edge nearest the pad (see BOND_MAX_ANGLE in hlayout.core.drc)."""
        from ..core.drc import bond_departures
        from ..io.techfile import load_bond_rules
        br = load_bond_rules(self.library)
        cell = self.library.get_cell(cell_name or self.current_cell_name)
        layers = ([self.library.layers.resolve(pad_layer)] if pad_layer is not None
                  else list(br.get("die_pad_layers") or []))
        out = []
        for pl in layers:
            for (a, b), k, die, ang in bond_departures(self.library, cell, int(br["layer"]), pl):
                out.append({"a": a, "b": b, "end": k, "die": die, "angle_deg": round(ang, 1)})
        return out

    def wire_bond(self, points, layer=None, width=None, pad=None,
                  min_length=None, max_length=None,
                  cell_name: Optional[str] = None):
        """Draw a chip-and-wire assembly WIRE BOND between two points --
        the jumper that ties a bare-die pad to a thick-film
        conductor pad, a conductor pad to a package pin, or one
        conductor pad to another.

        `points` is exactly two (x, y) DBU coordinates: the ball end and
        the stitch end. The bond is drawn on the bond layer as a round
        ball of diameter `pad` at each end joined by a `width`-wide
        round-capped Path -- an ``O------O`` figure -- so it visibly
        reads as a bond wire and, because that layer is folded into
        extract_netlist()/NETLIST RUN automatically, it electrically
        bridges whatever pads its two ends overlap. Connectivity is then
        reported straight through the bond to the die pin.

        The span (centre-to-centre distance) must lie between the bond
        rules' ``min_length`` and ``max_length`` -- a bonder can neither
        loop an arbitrarily short wire nor throw an unsupported one so
        long it sags or shorts. The new bond also may not CROSS any wire
        bond already in the cell (bond wires are bare gold in one plane
        -- crossed wires short); sharing a pad/endpoint is fine, only a
        proper X crossing is rejected.

        `layer`/`width`/`pad`/`min_length`/`max_length` default to
        Setup > Technology Properties (Wire Bonds tab) (load_bond_rules). Raises LayoutError
        on a bad layer, wrong point count, out-of-range length, or a
        crossing. One undo step.
        """
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")

        rules = load_bond_rules(self.library)
        resolve = self.library.layers.resolve
        r_layer = resolve(layer) if layer is not None else int(rules["layer"])
        if r_layer is None or r_layer < 0:
            raise LayoutError(
                "no wire-bond layer set -- pass layer=... or configure "
                "Setup > Technology Properties (Wire Bonds tab)"
            )
        w = int(width) if width is not None else int(rules["width"])
        p = int(pad) if pad is not None else int(rules["pad"])
        lo = float(min_length) if min_length is not None else float(rules["min_length"])
        hi = float(max_length) if max_length is not None else float(rules["max_length"])

        pts = [(int(round(x)), int(round(y))) for x, y in points]
        if len(pts) != 2:
            raise LayoutError(
                f"a wire bond needs exactly two endpoints, got {len(pts)}"
            )
        (x1, y1), (x2, y2) = pts
        span = math.hypot(x2 - x1, y2 - y1)
        if span < lo:
            raise LayoutError(
                f"wire bond span {span:.0f} DBU is shorter than the "
                f"minimum {lo:.0f} (Setup > Technology Properties (Wire Bonds tab))"
            )
        if span > hi:
            raise LayoutError(
                f"wire bond span {span:.0f} DBU is longer than the "
                f"maximum {hi:.0f} (Setup > Technology Properties (Wire Bonds tab))"
            )

        cell = self.library.get_cell(name)
        for a, b in self._existing_bond_segments(cell, r_layer):
            if _segments_cross((x1, y1), (x2, y2), a, b):
                raise LayoutError(
                    "that wire bond would cross an existing bond wire "
                    "-- bond wires share one plane and can't cross"
                )

        with self._batch():
            r = max(1, p // 2)
            cell.add_circle(r_layer, (x1, y1), r, segments=16)
            cell.add_circle(r_layer, (x2, y2), r, segments=16)
            cell.add_path(r_layer, [(x1, y1), (x2, y2)], max(1, w), cap="round")
        self._notify()
        return cell.shapes[-1]

    @staticmethod
    def _existing_bond_segments(cell, layer, exclude=None):
        """Yield (p0, p1) endpoint pairs for every wire-bond Path already
        on `layer` in `cell` -- the two-point round-capped Paths
        wire_bond() draws. `exclude` is one Path object to skip (used by
        move_wire_bond so a bond isn't tested against its own old
        position)."""
        from ..core.shapes import Path

        for s in cell.shapes:
            if s is exclude:
                continue
            if isinstance(s, Path) and s.layer == layer and len(s.points) >= 2:
                for i in range(len(s.points) - 1):
                    yield (tuple(s.points[i]), tuple(s.points[i + 1]))

    def _resolve_bond_layer(self, layer):
        rules = load_bond_rules(self.library)
        r_layer = self.library.layers.resolve(layer) if layer is not None else int(rules["layer"])
        if r_layer is None or r_layer < 0:
            raise LayoutError(
                "no wire-bond layer set -- pass layer=... or configure "
                "Setup > Technology Properties (Wire Bonds tab)"
            )
        return r_layer, rules

    def find_wire_bond(self, point, layer=None, cell_name: Optional[str] = None,
                       tol: Optional[float] = None):
        """Locate the wire bond nearest ``point`` (an (x, y) DBU
        coordinate) in the cell and return its component shapes as
        ``(connector_path, [ball, ball])`` -- the two-point round-capped
        Path plus the endpoint discs wire_bond() drew with it (the ball
        list may be shorter if a disc is missing). Returns ``None`` when
        nothing on the bond layer lies within ``tol`` (default: the bond
        rules' pad diameter, min the width) of ``point``.

        The match is by the connector Path: ``point`` need only be within
        ``tol`` of the O------O line, so clicking anywhere along a bond
        finds it.
        """
        from ..core.shapes import Path, Polygon

        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        r_layer, rules = self._resolve_bond_layer(layer)
        reach = float(tol) if tol is not None else float(max(int(rules["pad"]),
                                                             int(rules["width"]), 1))
        cell = self.library.get_cell(name)

        best = None
        best_d = reach
        for s in cell.shapes:
            if not (isinstance(s, Path) and s.layer == r_layer and len(s.points) == 2):
                continue
            d = _point_seg_dist(tuple(point), tuple(s.points[0]), tuple(s.points[1]))
            if d <= best_d:
                best_d = d
                best = s
        if best is None:
            return None

        ends = [tuple(best.points[0]), tuple(best.points[1])]
        # Exactly one ball per end: two bonds may share a pad, in which
        # case two identical discs sit on that end and each bond owns one
        # -- taking every disc found there would strip the neighbour's.
        balls = []
        for e in ends:
            for s in cell.shapes:
                if not (isinstance(s, Polygon) and s.layer == r_layer):
                    continue
                if any(s is b for b in balls):
                    continue
                x1, y1, x2, y2 = s.bbox()
                c = ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
                if math.hypot(c[0] - e[0], c[1] - e[1]) <= 2.0:
                    balls.append(s)
                    break
        return best, balls

    def move_wire_bond(self, near, to, layer=None,
                       cell_name: Optional[str] = None, tol: Optional[float] = None):
        """Move ONE end of the wire bond nearest ``near`` to the point
        ``to``. ``near`` both picks the bond (by distance to its
        O------O line) and chooses which end to move -- whichever
        endpoint it is closer to; the other end stays put. The moved
        end's disc follows.

        The new span (fixed end to ``to``) must still lie within the
        bond rules' ``min_length``/``max_length``, and the repositioned
        bond still may not CROSS another. Raises LayoutError if no bond
        is near ``near``, the new span is out of range, or the move would
        cross a bond. One undo step; returns ``(fixed_end, new_end)``."""
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        r_layer, rules = self._resolve_bond_layer(layer)
        found = self.find_wire_bond(near, layer=r_layer, cell_name=name, tol=tol)
        if found is None:
            raise LayoutError(f"no wire bond near {tuple(near)} on layer {r_layer}")
        path, balls = found

        p0, p1 = tuple(path.points[0]), tuple(path.points[1])
        nx, ny = float(near[0]), float(near[1])
        grab_first = (math.hypot(nx - p0[0], ny - p0[1])
                      <= math.hypot(nx - p1[0], ny - p1[1]))
        moved_old = p0 if grab_first else p1
        fixed = p1 if grab_first else p0
        new_end = (int(round(to[0])), int(round(to[1])))

        span = math.hypot(new_end[0] - fixed[0], new_end[1] - fixed[1])
        lo, hi = float(rules["min_length"]), float(rules["max_length"])
        if span < lo:
            raise LayoutError(
                f"the moved bond would span {span:.0f} DBU, shorter than the "
                f"minimum {lo:.0f} (Setup > Technology Properties (Wire Bonds tab))"
            )
        if span > hi:
            raise LayoutError(
                f"the moved bond would span {span:.0f} DBU, longer than the "
                f"maximum {hi:.0f} (Setup > Technology Properties (Wire Bonds tab))"
            )

        cell = self.library.get_cell(name)
        for a, b in self._existing_bond_segments(cell, r_layer, exclude=path):
            if _segments_cross(fixed, new_end, a, b):
                raise LayoutError(
                    "moving that end there would cross an existing bond wire "
                    "-- bond wires share one plane and can't cross"
                )

        dx = new_end[0] - moved_old[0]
        dy = new_end[1] - moved_old[1]
        with self._batch():
            path.points = [new_end, fixed] if grab_first else [fixed, new_end]
            for ball in balls:
                bx0, by0, bx1, by1 = ball.bbox()
                c = ((bx0 + bx1) / 2.0, (by0 + by1) / 2.0)
                if math.hypot(c[0] - moved_old[0], c[1] - moved_old[1]) <= 2.0:
                    ball.points = [(px + dx, py + dy) for px, py in ball.points]
        self._notify()
        return fixed, new_end

    def delete_wire_bond(self, point, layer=None,
                         cell_name: Optional[str] = None, tol: Optional[float] = None):
        """Remove the wire bond nearest ``point`` -- its connector Path
        and both endpoint discs. Raises LayoutError if no bond is near
        ``point``. One undo step; returns the removed bond's endpoints."""
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell; call new_cell()/use_cell() first")
        r_layer, _ = self._resolve_bond_layer(layer)
        found = self.find_wire_bond(point, layer=r_layer, cell_name=name, tol=tol)
        if found is None:
            raise LayoutError(f"no wire bond near {tuple(point)} on layer {r_layer}")
        path, balls = found
        ends = (tuple(path.points[0]), tuple(path.points[1]))
        doomed = {id(path), *(id(b) for b in balls)}
        cell = self.library.get_cell(name)
        with self._batch():
            cell.shapes[:] = [s for s in cell.shapes if id(s) not in doomed]
        self._notify()
        return ends
