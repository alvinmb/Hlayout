# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Thick-film resistor shapes beyond the plain rectangle.

The Thick-Film Resistor Designer sizes a rectangle from squares alone:
R = Rsh x L / W. A shaped resistor -- a top-hat, a U, an angle -- has
corners, a crown or a conductor roof, and its resistance is no longer
L / W. This module draws each shape from a handful of dimensions and
finds its resistance by solving the ink as a resistive sheet, the same
way hlayout.core.trim_sim does for a rectangle being trimmed, so the
corners, the crown and the roof are all counted without rule-of-thumb
factors.

Shapes (all in the resistor's own cell frame, units DBU = 1 um, ink
width W everywhere):

``rect``          Rectangle. Terminations on both ends; gap L.
``tophat``        Top-hat. A bar of gap L with a crown Wc wide and H
                  high on its middle. The trim plunge runs up from the
                  bar's lower edge through the bar into the crown, so the
                  crown adds trim range without widening the whole bar.
``tophat_roof``   Top-hat with roof. As the top-hat, with a conductor
                  "roof" over the crown. Once the plunge has cut the bar,
                  current goes up one side of the crown, across the roof
                  and down the other, so the plunge can run almost to the
                  roof without the resistor going open: a large, safe
                  trim range with a bounded maximum.
``u``             U-shape. Two arms L long joined by a base bar, both
                  terminations on the arm tops (pads side by side). Trim
                  plunge into the base bar from inside the U.
``u_roof``        U-shape with roof. A U whose base bar carries a skirt H
                  deep under it, capped by a conductor roof; the plunge
                  runs from inside the U down through the base into the
                  skirt -- the U-shaped equivalent of the top-hat with roof.
``angle``         Angle. An L: a horizontal arm and a vertical arm, each
                  L long, one corner; terminations on two adjacent sides.
``double_angle``  Double angle. A Z: two horizontal arms L long joined by
                  a riser, two corners; terminations facing opposite
                  ways, offset vertically.
``angle3``        Angle three-sided. Two leads L long on one baseline
                  joined by a raised three-sided loop (up, across,
                  down), four corners; terminations facing outward.

Terminations follow the technology's resistor geometry rules (see
hlayout.io.techfile.load_resistor_geometry_rules): the ink runs A past
each end of the resistive length under the conductor, the pad is A + B
long and W + 2C wide. A roof is drawn the same way across the crown or
skirt it caps, but carries no pin -- it is a floating conductor.

Every non-length dimension (crown width/height, gaps, riser) is snapped
to a fraction of W so the sheet grid lines up with the ink edges and the
solve is accurate; lengths along the current flow end under a pad or a
roof, where the solver measures the distance to the conductor edge
exactly.

Like trim_sim, this is a design-stage model: uniform sheet resistance,
ideal conductors, no termination interaction. Requires numpy and scipy.
"""

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

Rect = Tuple[float, float, float, float]  # x1, y1, x2, y2 (x1 < x2, y1 < y2)

#: (key, label, short code) in menu order. No serpentine: thin film only.
SHAPES: List[Tuple[str, str, str]] = [
    ("rect", "Rectangle", "R"),
    ("tophat", "Top-hat", "TH"),
    ("tophat_roof", "Top-hat with roof", "THR"),
    ("u", "U-shape", "U"),
    ("u_roof", "U-shape with roof", "UR"),
    ("angle", "Angle", "A"),
    ("double_angle", "Double angle", "AA"),
    ("angle3", "Angle three-sided", "A3"),
]
SHAPE_KEYS = [k for k, _, _ in SHAPES]
SHAPE_LABELS = {k: lbl for k, lbl, _ in SHAPES}
SHAPE_CODES = {k: c for k, _, c in SHAPES}
SHAPE_BY_LABEL = {lbl: k for k, lbl, _ in SHAPES}

#: grid cells across the ink width for sizing; trim runs use a finer grid
GRID_PER_W = 10


class ShapeError(ValueError):
    """Bad shape name or dimensions."""


@dataclass
class Cut:
    """A suggested plunge cut: from ``start`` along unit vector ``dir``
    up to ``max_depth`` (the deepest it may go before the part opens,
    or before it reaches a roof)."""
    start: Tuple[float, float]
    dir: Tuple[float, float]
    max_depth: float


@dataclass
class ShapeGeometry:
    shape: str
    width: float              # ink width W
    length: float             # the sized length L (meaning depends on shape)
    params: Dict[str, float]  # secondary dimensions actually used
    ink: List[Rect]           # union of these is the ink
    pads: List[Tuple[Rect, str]]  # conductor terminations with pin "1"/"2"
    roofs: List[Rect] = field(default_factory=list)  # floating conductors
    cut: Optional[Cut] = None

    def bbox(self) -> Rect:
        rects = list(self.ink) + [r for r, _ in self.pads] + list(self.roofs)
        return (min(r[0] for r in rects), min(r[1] for r in rects),
                max(r[2] for r in rects), max(r[3] for r in rects))

    def ink_polygons(self):
        """The ink as shapely polygons (normally one)."""
        from shapely.geometry import box
        from shapely.ops import unary_union
        u = unary_union([box(*r) for r in self.ink])
        return list(getattr(u, "geoms", [u]))

    def resistive_polygons(self):
        """The ink not under a conductor -- what the dielectric keep-out
        covers."""
        from shapely.geometry import box
        from shapely.ops import unary_union
        ink = unary_union([box(*r) for r in self.ink])
        cond = unary_union([box(*r) for r, _ in self.pads] + [box(*r) for r in self.roofs])
        u = ink.difference(cond)
        return [g for g in getattr(u, "geoms", [u]) if not g.is_empty]


def _snap(v: float, step: float, minimum: float = 0.0) -> float:
    return max(minimum, round(v / step) * step)


def default_params(shape: str, w: float, c: float = 0.0) -> Dict[str, float]:
    """Secondary dimensions for ``shape`` when none are given, as
    multiples of the ink width (``c`` is the pad side clearance C, used
    to keep U pads apart)."""
    h = w / GRID_PER_W
    if shape == "tophat":
        return {"crown_w": w, "crown_h": w}
    if shape == "tophat_roof":
        return {"crown_w": 2 * w, "crown_h": w}
    if shape in ("u", "u_roof"):
        gap = _snap(max(w, 2 * c + 0.5 * w), 2 * h, 2 * h)
        p = {"gap": gap}
        if shape == "u_roof":
            p["skirt_h"] = w
        return p
    if shape == "double_angle":
        return {"rise": w}
    if shape == "angle3":
        return {"loop_w": w, "loop_h": w}
    return {}


def min_length(shape: str, w: float, params: Dict[str, float]) -> float:
    """Shortest sensible L for ``shape``."""
    if shape in ("tophat", "tophat_roof"):
        return params["crown_w"] + w        # at least W/2 of bar each side of the crown
    return 0.25 * w


def max_length(shape: str, w: float, ar_max: float,
               params: Optional[Dict[str, float]] = None) -> float:
    """Longest L allowed for ``shape`` under an aspect limit of
    ``ar_max`` widths per straight run: a top-hat's bar may be longer by
    its crown, since the crown splits it into two shoulders."""
    if shape in ("tophat", "tophat_roof"):
        p = default_params(shape, w)
        p.update(params or {})
        return ar_max * w + p["crown_w"]
    return ar_max * w


def _term(edge: Tuple[float, float], out: Tuple[int, int], w: float,
          a: float, b: float, c: float) -> Tuple[Rect, Rect]:
    """Ink extension (A long, W wide) and pad (A + B long, W + 2C wide)
    for a termination whose resistive ink ends at ``edge`` (centre of
    the end), pointing outward along ``out``."""
    x, y = edge
    dx, dy = out
    hw, pw = w / 2.0, w / 2.0 + c
    if dx:
        x2 = x + dx * a
        ink = (min(x, x2), y - hw, max(x, x2), y + hw)
        x3 = x + dx * (a + b)
        pad = (min(x, x3), y - pw, max(x, x3), y + pw)
    else:
        y2 = y + dy * a
        ink = (x - hw, min(y, y2), x + hw, max(y, y2))
        y3 = y + dy * (a + b)
        pad = (x - pw, min(y, y3), x + pw, max(y, y3))
    return ink, pad


def build(shape: str, w: float, length: float, a: float, b: float, c: float,
          params: Optional[Dict[str, float]] = None) -> ShapeGeometry:
    """Geometry of ``shape`` with ink width ``w`` and sized length
    ``length`` (see the module docstring for what L means per shape),
    with terminations per the A/B/C rules. Secondary dimensions come
    from ``params`` or default_params(); they are snapped to the sheet
    grid (W / GRID_PER_W)."""
    if shape not in SHAPE_KEYS:
        raise ShapeError(f"unknown resistor shape {shape!r}; one of {', '.join(SHAPE_KEYS)}")
    if w <= 0 or length <= 0:
        raise ShapeError("width and length must be positive")
    p = default_params(shape, w, c)
    p.update({k: float(v) for k, v in (params or {}).items() if v is not None})
    h = w / GRID_PER_W
    L = float(length)
    hw = w / 2.0
    ink: List[Rect] = []
    pads: List[Tuple[Rect, str]] = []
    roofs: List[Rect] = []
    cut = None

    def term(edge, out, pin):
        e, pd = _term(edge, out, w, a, b, c)
        ink.append(e)
        pads.append((pd, pin))

    if shape == "rect":
        ink.append((-L / 2, -hw, L / 2, hw))
        term((-L / 2, 0.0), (-1, 0), "1")
        term((L / 2, 0.0), (1, 0), "2")
        cut = Cut((0.0, -hw), (0.0, 1.0), 0.5 * w)

    elif shape in ("tophat", "tophat_roof"):
        cw = _snap(p["crown_w"], 2 * h, 2 * h)
        ch = _snap(p["crown_h"], h, h)
        p["crown_w"], p["crown_h"] = cw, ch
        if L < cw + w:
            raise ShapeError(f"top-hat bar ({L:g}) must be at least crown width + W ({cw + w:g})")
        ink.append((-L / 2, -hw, L / 2, hw))
        top = hw + ch
        if shape == "tophat_roof":
            ink.append((-cw / 2, hw, cw / 2, top + a))          # crown runs A under the roof
            roofs.append((-cw / 2 - c, top, cw / 2 + c, top + a + b))
            cut = Cut((0.0, -hw), (0.0, 1.0), w + ch - 0.1 * w)
        else:
            ink.append((-cw / 2, hw, cw / 2, top))
            cut = Cut((0.0, -hw), (0.0, 1.0), w + 0.7 * ch)
        term((-L / 2, 0.0), (-1, 0), "1")
        term((L / 2, 0.0), (1, 0), "2")

    elif shape in ("u", "u_roof"):
        g = _snap(p["gap"], 2 * h, 2 * h)
        p["gap"] = g
        xo = g / 2 + w                                      # outer edge of each arm
        ink.append((-xo, 0.0, xo, w))                       # base bar
        ink.append((-xo, w, -g / 2, w + L))                 # left arm
        ink.append((g / 2, w, xo, w + L))                   # right arm
        term((-(g / 2 + hw), w + L), (0, 1), "1")
        term((g / 2 + hw, w + L), (0, 1), "2")
        if shape == "u_roof":
            sh = _snap(p["skirt_h"], h, h)
            p["skirt_h"] = sh
            ink.append((-xo, -sh - a, xo, 0.0))              # skirt, A under the roof
            roofs.append((-xo - c, -sh - a - b, xo + c, -sh))
            cut = Cut((0.0, w), (0.0, -1.0), w + sh - 0.1 * w)
        else:
            cut = Cut((0.0, w), (0.0, -1.0), 0.5 * w)

    elif shape == "angle":
        ink.append((-L, 0.0, w, w))                         # horizontal arm + corner
        ink.append((0.0, w, w, w + L))                      # vertical arm
        term((-L, hw), (-1, 0), "1")
        term((hw, w + L), (0, 1), "2")
        cut = Cut((-L / 2, 0.0), (0.0, 1.0), 0.5 * w)

    elif shape == "double_angle":
        r = _snap(p["rise"], h, 0.0)
        p["rise"] = r
        ink.append((-L, 0.0, w, w))                         # lower arm + corner
        ink.append((0.0, w, w, 2 * w + r))                  # riser + upper corner
        ink.append((w, w + r, w + L, 2 * w + r))            # upper arm
        term((-L, hw), (-1, 0), "1")
        term((w + L, w + r + hw), (1, 0), "2")
        cut = Cut((-L / 2, 0.0), (0.0, 1.0), 0.5 * w)

    elif shape == "angle3":
        lw = _snap(p["loop_w"], 2 * h, 2 * h)
        lh = _snap(p["loop_h"], h, 0.0)
        p["loop_w"], p["loop_h"] = lw, lh
        xi, xo = lw / 2, lw / 2 + w
        top = 2 * w + lh
        ink.append((-xo - L, 0.0, -xi, w))                  # left lead + post foot
        ink.append((-xo, w, -xi, top))                      # left post
        ink.append((-xo, top - w, xo, top))                 # top
        ink.append((xi, w, xo, top))                        # right post
        ink.append((xi, 0.0, xo + L, w))                    # right lead + post foot
        term((-xo - L, hw), (-1, 0), "1")
        term((xo + L, hw), (1, 0), "2")
        cut = Cut((0.0, top), (0.0, -1.0), 0.5 * w)

    return ShapeGeometry(shape=shape, width=w, length=L, params=p, ink=ink, pads=pads,
                         roofs=roofs, cut=cut)


# ---------------------------------------------------------------------------
# Sheet solve
# ---------------------------------------------------------------------------

def _contains(rect: Rect, x, y):
    # half-open, so rectangles that share an edge tile without a gap even
    # when a grid centre lands exactly on that edge
    return (x >= rect[0]) & (x < rect[2]) & (y >= rect[1]) & (y < rect[3])


class _Sheet:
    """The ink rasterised on a square grid of pitch h, with each grid
    cell tagged free, terminal 1, terminal 2, or roof. Built once per
    geometry; cuts just knock cells out."""

    def __init__(self, geom: ShapeGeometry, h: float):
        import numpy as np
        self.geom, self.h = geom, h
        x0, y0, x1, y1 = geom.bbox()
        # grid lines on multiples of h from the origin: the shapes put
        # every width-direction edge on such a line
        i0, i1 = math.floor(x0 / h) - 1, math.ceil(x1 / h) + 1
        j0, j1 = math.floor(y0 / h) - 1, math.ceil(y1 / h) + 1
        self.xs = (np.arange(i0, i1) + 0.5) * h
        self.ys = (np.arange(j0, j1) + 0.5) * h
        X, Y = np.meshgrid(self.xs, self.ys)
        ink = np.zeros(X.shape, bool)
        for r in geom.ink:
            ink |= _contains(r, X, Y)
        self.X, self.Y, self.ink = X, Y, ink
        # 0 free, 1 pin 1, 2 pin 2, 3 roof, -1 no ink
        tag = np.where(ink, 0, -1)
        self.cond_rect = {}
        for r, pin in geom.pads:
            k = 1 if pin == "1" else 2
            m = ink & _contains(r, X, Y)
            tag[m] = k
        for r in geom.roofs:
            tag[ink & _contains(r, X, Y)] = 3
        self.tag = tag
        self.rects = {1: [r for r, pin in geom.pads if pin == "1"],
                      2: [r for r, pin in geom.pads if pin == "2"],
                      3: list(geom.roofs)}

    def _edge_dist(self, kind, x, y, dx, dy):
        """Distance from free-cell centre (x, y) moving along (dx, dy)
        to the edge of the conductor ``kind`` it runs into (<= h)."""
        best = self.h
        for r in self.rects[kind]:
            if dx > 0 and r[1] < y < r[3] and r[0] >= x:
                best = min(best, r[0] - x)
            elif dx < 0 and r[1] < y < r[3] and r[2] <= x:
                best = min(best, x - r[2])
            elif dy > 0 and r[0] < x < r[2] and r[1] >= y:
                best = min(best, r[1] - y)
            elif dy < 0 and r[0] < x < r[2] and r[3] <= y:
                best = min(best, y - r[3])
        return max(best, 1e-3 * self.h)

    def solve(self, removed=None, want_field=False):
        """Resistance in squares between pin 1 and pin 2 (multiply by
        Rsh for ohms); math.inf if the ink is cut through. With
        want_field, also the per-cell power share (for heat maps)."""
        import numpy as np
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        from scipy.sparse.linalg import spsolve
        tag = self.tag.copy()
        if removed is not None:
            tag[removed & (tag == 0)] = -1
        ny, nx = tag.shape
        free = np.argwhere(tag == 0)
        idx = -np.ones(tag.shape, int)
        idx[tag == 0] = np.arange(len(free))
        n = len(free)
        roof_node = n if (tag == 3).any() else None
        nn = n + (1 if roof_node is not None else 0)
        rows, cols, vals = [], [], []
        rhs = np.zeros(nn)
        g_to = {1: np.zeros(nn), 2: np.zeros(nn)}      # conductance of each node to each pin
        diag = np.zeros(nn)
        h = self.h

        def node(j, i):
            t = tag[j, i]
            if t == 0:
                return idx[j, i]
            if t == 3:
                return roof_node
            return None

        # free-free and free-conductor links
        for j in range(ny):
            for i in range(nx):
                t = tag[j, i]
                if t < 0:
                    continue
                for dj, di in ((0, 1), (1, 0)):
                    jj, ii = j + dj, i + di
                    if jj >= ny or ii >= nx:
                        continue
                    t2 = tag[jj, ii]
                    if t2 < 0 or (t != 0 and t2 != 0):
                        continue                       # nothing, or conductor-conductor
                    if t == 0 and t2 == 0:
                        a_, b_ = idx[j, i], idx[jj, ii]
                        g = 1.0
                        rows += [a_, b_]
                        cols += [b_, a_]
                        vals += [-g, -g]
                        diag[a_] += g
                        diag[b_] += g
                        continue
                    # one free, one conductor: the link runs from the free
                    # centre to the conductor's actual edge
                    if t == 0:
                        fj, fi, kind, sx, sy = j, i, t2, di, dj
                    else:
                        fj, fi, kind, sx, sy = jj, ii, t, -di, -dj
                    d = self._edge_dist(kind, self.X[fj, fi], self.Y[fj, fi], sx, sy)
                    g = h / d
                    f = idx[fj, fi]
                    diag[f] += g
                    if kind == 3:
                        rows += [f, roof_node]
                        cols += [roof_node, f]
                        vals += [-g, -g]
                        diag[roof_node] += g
                    else:
                        g_to[kind][f] += g
        # connectivity: pin 1 must reach pin 2 through the graph
        touch1 = g_to[1] > 0
        touch2 = g_to[2] > 0
        if not touch1.any() or not touch2.any():
            # pins directly adjacent (no free ink) is not a real case here
            return (math.inf, None) if want_field else math.inf
        A_adj = coo_matrix((np.ones(len(vals)), (rows, cols)), shape=(nn, nn)).tocsr()
        ncomp, lab = connected_components(A_adj, directed=False)
        if not set(lab[touch1]) & set(lab[touch2]):
            return (math.inf, None) if want_field else math.inf
        # V(pin1) = 1, V(pin2) = 0
        rhs += g_to[1]
        M = coo_matrix((list(vals) + list(diag), (list(rows) + list(range(nn)), list(cols) + list(range(nn)))),
                       shape=(nn, nn)).tocsr()
        # isolated islands (no path to either pin) would make M singular
        keep = np.isin(lab, list(set(lab[touch1]) | set(lab[touch2])))
        sub = np.where(keep)[0]
        V = np.zeros(nn)
        V[sub] = spsolve(M[sub][:, sub].tocsc(), rhs[sub])
        I = float(np.sum(g_to[1] * (1.0 - V)))
        squares = 1.0 / I if I > 0 else math.inf
        if not want_field:
            return squares
        # power share per free cell: sum over its links of g dV^2 / 2
        P = np.zeros(tag.shape)
        Vg = np.full(tag.shape, np.nan)
        Vg[tag == 0] = V[:n]
        Vg[tag == 1] = 1.0
        Vg[tag == 2] = 0.0
        if roof_node is not None:
            Vg[tag == 3] = V[roof_node]
        for dj, di in ((0, 1), (1, 0)):
            a_ = Vg[:ny - dj, :nx - di]
            b_ = Vg[dj:, di:]
            dv2 = np.nan_to_num((a_ - b_) ** 2)
            P[:ny - dj, :nx - di] += dv2 / 2
            P[dj:, di:] += dv2 / 2
        P[tag != 0] = 0
        return squares, P

    def cut_mask(self, cut: Cut, depth: float, kerf: float):
        import numpy as np
        (sx, sy), (dx, dy) = cut.start, cut.dir
        rx, ry = self.X - sx, self.Y - sy
        along = rx * dx + ry * dy
        across = np.abs(-rx * dy + ry * dx)
        half = max(kerf, self.h) / 2.0
        return (along >= -self.h) & (along <= depth) & (across <= half)


def squares(geom: ShapeGeometry, grid_per_w: int = GRID_PER_W) -> float:
    """Number of squares between the two terminations of ``geom``
    (resistance = Rsh x squares)."""
    return _Sheet(geom, geom.width / grid_per_w).solve()


def size(shape: str, w: float, target_squares: float, a: float, b: float, c: float,
         params: Optional[Dict[str, float]] = None, max_length: Optional[float] = None,
         tol: float = 1e-3) -> Optional[ShapeGeometry]:
    """Find L so that ``shape`` at width ``w`` is ``target_squares``
    squares (aim value / Rsh). Returns the geometry, or None when even
    the shortest legal L has more squares than the target or the
    longest allowed (``max_length``, default 20 W) has fewer."""
    p = default_params(shape, w, c)
    p.update(params or {})
    lo = min_length(shape, w, p)
    hi = max_length if max_length is not None else 20 * w
    if hi <= lo:
        return None

    def sq(L):
        return squares(build(shape, w, L, a, b, c, p))

    s_lo, s_hi = sq(lo), sq(hi)
    if not (s_lo <= target_squares <= s_hi):
        return None
    # resistance is close to linear in L: secant steps, bracketed
    for _ in range(40):
        L = lo + (target_squares - s_lo) * (hi - lo) / (s_hi - s_lo) if s_hi > s_lo else (lo + hi) / 2
        L = min(max(L, lo + 1e-6 * w), hi - 1e-6 * w)
        s = sq(L)
        if abs(s - target_squares) <= tol * target_squares:
            return build(shape, w, L, a, b, c, p)
        if s < target_squares:
            lo, s_lo = L, s
        else:
            hi, s_hi = L, s
    return build(shape, w, (lo + hi) / 2, a, b, c, p)


@dataclass
class TrimStep:
    depth: float                 # um along the cut
    resistance: float            # ohm (inf when open)
    sensitivity: Optional[float]  # ohm / um
    is_open: bool


def simulate_trim(geom: ShapeGeometry, rsh: float, depth: Optional[float] = None,
                  kerf: float = 40.0, steps: int = 30, grid: Optional[float] = None,
                  cut: Optional[Cut] = None, want_field: bool = False):
    """Plunge-trim ``geom`` along its suggested cut (or ``cut``) to
    ``depth`` (default the cut's max_depth) in ``steps`` steps with a
    ``kerf`` um wide laser. Returns a list of TrimStep (step 0 is the
    untrimmed part) and, with want_field, the power map of the last
    step (cells x cells, plus the grid x/y centres)."""
    cut = cut or geom.cut
    if cut is None:
        raise ShapeError("no cut defined for this shape")
    depth = cut.max_depth if depth is None else float(depth)
    h = grid or min(geom.width / 20.0, max(kerf, geom.width / 60.0))
    sheet = _Sheet(geom, h)
    # a step shorter than a grid cell would leave some steps cutting
    # nothing (zero sensitivity) and the next a whole cell: keep >= 1 cell
    steps = max(1, min(int(steps), int(depth / h) if depth > 0 else 1))
    out: List[TrimStep] = []
    prev = None
    last_field = None
    for k in range(steps + 1):
        d = depth * k / steps
        mask = sheet.cut_mask(cut, d, kerf) if k else None
        if want_field and k == steps:
            sq, last_field = sheet.solve(mask, want_field=True)
        else:
            sq = sheet.solve(mask)
        r = rsh * sq
        sens = None
        if prev is not None and math.isfinite(r) and math.isfinite(prev[1]) and d > prev[0]:
            sens = (r - prev[1]) / (d - prev[0])
        out.append(TrimStep(d, r, sens, not math.isfinite(r)))
        prev = (d, r)
        if not math.isfinite(r):
            break
    if want_field:
        return out, (last_field, sheet.xs, sheet.ys)
    return out
