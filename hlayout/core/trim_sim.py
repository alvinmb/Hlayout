"""Resistor laser-trim simulator.

Models a rectangular thick-film resistor body as a 2D resistive-sheet
network (a grid of small resistors, one classic finite-difference way
to approximate a continuous conductive sheet), applies a laser trim
cut to it incrementally -- a straight PLUNGE cut in from one edge,
optionally continued as an L-CUT (a 90-degree turn, run parallel to
that edge) -- and at every step solves the network for:

  - the terminal-to-terminal resistance (what the trim system's own
    feedback loop is watching)
  - sensitivity: dR / d(cut progress), the rate resistance is
    changing per unit the laser advances -- this is what makes plunge
    cuts risky as they approach the far edge (the remaining
    conduction path narrows to nothing and sensitivity blows up) and
    is exactly why L-cuts exist (turning the cut keeps the remaining
    path a fixed width instead of tapering to zero, keeping
    sensitivity bounded near the target value)
  - the current-density / power-dissipation field over the resistor
    body at that step, i.e. a heat map -- current crowds through
    whatever conduction path remains, and that's where a thick-film
    resistor is most likely to crack or burn out during an aggressive
    trim

This is a standalone analysis tool: it operates purely on the body
width/length/sheet-resistance numbers a Resistor Designer Solution
already carries (see hlayout.gui.resistor_designer.Solution) and does
not read or write any GDS geometry.

Like the pad-geometry formulas in examples/generate_*.py, the sheet
model here is a deliberately simple, uniform-weight finite-difference
approximation, not a precision continuum FEM solve -- treat its
numbers as a design-stage sensitivity/heat-map guide, not a
fabrication-grade prediction, and validate against an actual trim
system's behavior before relying on it for production trim profiles.

Requires numpy and scipy (see requirements.txt). Imported lazily by
the GUI (hlayout.gui.trim_sim_window) precisely so the rest of the
application doesn't depend on them being installed.
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import spsolve


class TrimSimError(Exception):
    """Raised for invalid trim-simulator setup (bad geometry, cut
    requested before a starting edge/plunge exists, etc)."""


# --- small union-find, used only to detect a fully severed (open
# circuit) resistor body cheaply, before/alongside the real solve -----
class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _dist_point_to_segment(px, py, x0, y0, x1, y1):
    """Vectorized point-to-segment distance; px/py are numpy arrays."""
    dx, dy = x1 - x0, y1 - y0
    seg_len2 = dx * dx + dy * dy
    if seg_len2 < 1e-9:
        return np.hypot(px - x0, py - y0)
    t = ((px - x0) * dx + (py - y0) * dy) / seg_len2
    t = np.clip(t, 0.0, 1.0)
    cx = x0 + t * dx
    cy = y0 + t * dy
    return np.hypot(px - cx, py - cy)


@dataclass
class CutSegment:
    """One straight leg of the overall cut path, in the same um-space
    coordinates as the resistor body (x: 0..length_um along the
    current-flow axis, y: 0..width_um across it). kerf_um is the full
    kerf width, centered on the x0,y0 -> x1,y1 line."""
    x0: float
    y0: float
    x1: float
    y1: float
    kerf_um: float
    kind: str  # "plunge" or "lcut", for reporting/plotting only


@dataclass
class TrimResult:
    """One recorded step of a trim run."""
    cut_progress_um: float  # cumulative distance advanced along the cut path
    resistance_ohm: float   # math.inf once the body is fully severed
    is_open: bool
    current_a: float        # at the simulator's operating_voltage
    sensitivity_ohm_per_um: Optional[float] = None  # dR/d(progress); None for the first point
    segment_kind: str = "plunge"


class TrimSimulator:
    """A rectangular resistor body of length_um (current-flow axis, x)
    by width_um (transverse axis, y), sheet resistance
    sheet_ohms_per_sq, discretized as an nx x ny node grid.

    Terminal contacts are the full x=0 and x=length_um edges (an ideal
    contact spanning the whole width, same assumption Resistor
    Designer's own sizing makes), held at operating_voltage and 0V
    respectively for the field solve. Grid resolution defaults to
    roughly 6000 nodes, scaled to the body's aspect ratio, which is
    fast enough (well under a second per solve) for an interactive
    step-by-step trim run; pass nx/ny explicitly for finer detail.
    """

    def __init__(self, length_um: float, width_um: float, sheet_ohms_per_sq: float,
                 nx: Optional[int] = None, ny: Optional[int] = None,
                 operating_voltage: float = 1.0):
        if length_um <= 0 or width_um <= 0:
            raise TrimSimError("length_um and width_um must be positive")
        if sheet_ohms_per_sq <= 0:
            raise TrimSimError("sheet_ohms_per_sq must be positive")
        self.length_um = float(length_um)
        self.width_um = float(width_um)
        self.sheet_ohms_per_sq = float(sheet_ohms_per_sq)
        self.operating_voltage = float(operating_voltage)

        if nx is None or ny is None:
            target_nodes = 6000
            aspect = self.length_um / self.width_um
            ny_auto = max(15, int(round(math.sqrt(target_nodes / max(aspect, 1e-6)))))
            nx_auto = max(15, int(round(ny_auto * aspect)))
            nx = min(nx or nx_auto, 320)
            ny = min(ny or ny_auto, 320)
        self.nx = int(nx)
        self.ny = int(ny)

        self.dx = self.length_um / (self.nx - 1)
        self.dy = self.width_um / (self.ny - 1)
        # Uniform per-edge control-volume weight (see module docstring:
        # a deliberate simplification, not a boundary-corrected FEM mesh).
        self._base_gx = self.dy / (self.dx * self.sheet_ohms_per_sq)
        self._base_gy = self.dx / (self.dy * self.sheet_ohms_per_sq)

        # Node index grid: idx_grid[j, i] = j*nx + i, y-row major.
        self.idx_grid = (np.arange(self.ny)[:, None] * self.nx
                          + np.arange(self.nx)[None, :])

        # Edge midpoint coordinates, precomputed once for cut rasterization.
        xs = np.arange(self.nx) * self.dx
        ys = np.arange(self.ny) * self.dy
        # Horizontal edges: (i,j)-(i+1,j), shape (ny, nx-1)
        self._hx_mid = (xs[:-1] + self.dx / 2)[None, :] * np.ones((self.ny, 1))
        self._hy_mid = ys[:, None] * np.ones((1, self.nx - 1))
        # Vertical edges: (i,j)-(i,j+1), shape (ny-1, nx)
        self._vx_mid = xs[None, :] * np.ones((self.ny - 1, 1))
        self._vy_mid = (ys[:-1] + self.dy / 2)[:, None] * np.ones((1, self.nx))

        self.segments: List[CutSegment] = []
        self.history: List[TrimResult] = []
        self.power_history: List[np.ndarray] = []  # one (ny, nx) grid per history[] entry, for scrubbing
        self._last_power_grid: Optional[np.ndarray] = None  # (ny, nx), watts
        self._last_is_open = False
        self._cut_progress_um = 0.0

        self._rebuild_conductances()

    # --- geometry / conductance -------------------------------------
    def reset(self) -> None:
        """Discard every cut segment and start over from an untrimmed body."""
        self.segments.clear()
        self.history.clear()
        self.power_history.clear()
        self._last_power_grid = None
        self._last_is_open = False
        self._cut_progress_um = 0.0
        self._rebuild_conductances()

    def _rebuild_conductances(self) -> None:
        # Horizontal edges: shape (ny, nx-1); vertical: (ny-1, nx).
        self._Gh = np.full((self.ny, self.nx - 1), self._base_gx)
        self._Gv = np.full((self.ny - 1, self.nx), self._base_gy)
        for seg in self.segments:
            self._apply_segment(seg)

    def _apply_segment(self, seg: CutSegment) -> None:
        half = seg.kerf_um / 2.0
        dh = _dist_point_to_segment(self._hx_mid, self._hy_mid, seg.x0, seg.y0, seg.x1, seg.y1)
        self._Gh[dh < half] = 0.0
        dv = _dist_point_to_segment(self._vx_mid, self._vy_mid, seg.x0, seg.y0, seg.x1, seg.y1)
        self._Gv[dv < half] = 0.0

    def body_center_x_um(self) -> float:
        return self.length_um / 2.0

    # --- trim runs ----------------------------------------------------
    def add_plunge(self, entry_edge: str = "bottom", x_center_um: Optional[float] = None,
                    kerf_um: float = 40.0, depth_um: Optional[float] = None,
                    steps: int = 40) -> List[TrimResult]:
        """Advance a straight plunge cut in from one edge, from 0 up to
        depth_um (default: 95% of the body width -- deep enough to
        show the sensitivity ramp without guaranteeing a full sever),
        recording a TrimResult at each of `steps` increments. Can only
        be called on a body with no cut yet (call reset() first to
        start a fresh plunge)."""
        if self.segments:
            raise TrimSimError(
                "add_plunge() starts a new cut -- call reset() first if you want "
                "to discard the current one and start over")
        entry_edge = entry_edge.lower()
        if entry_edge not in ("bottom", "top"):
            raise TrimSimError('entry_edge must be "bottom" or "top"')
        if x_center_um is None:
            x_center_um = self.body_center_x_um()
        if not (0 < x_center_um < self.length_um):
            raise TrimSimError("x_center_um must fall inside the body length")
        if depth_um is None:
            depth_um = self.width_um * 0.95
        depth_um = min(max(depth_um, 0.0), self.width_um)
        if steps < 1:
            raise TrimSimError("steps must be >= 1")

        y0 = 0.0 if entry_edge == "bottom" else self.width_um
        direction = 1.0 if entry_edge == "bottom" else -1.0

        seg = CutSegment(x0=x_center_um, y0=y0, x1=x_center_um, y1=y0, kerf_um=kerf_um, kind="plunge")
        self.segments.append(seg)

        results = []
        for step in range(1, steps + 1):
            d = depth_um * step / steps
            seg.x1, seg.y1 = x_center_um, y0 + direction * d
            self._rebuild_conductances()
            results.append(self._solve_and_record("plunge"))
        return results

    def add_l_cut(self, turn_length_um: float, direction: int = 1,
                   kerf_um: Optional[float] = None, steps: int = 20) -> List[TrimResult]:
        """Extend the current cut with an L-leg: a 90-degree turn from
        the tip of the last segment, running parallel to the entry
        edge for turn_length_um (direction=+1 towards +x, -1 towards
        -x). Must follow an add_plunge() call (or another add_l_cut()
        -- you can chain more than one turn if you want a Z-shaped
        profile, though a single L-leg is the usual case). kerf_um
        defaults to the same kerf as the previous segment."""
        if not self.segments:
            raise TrimSimError("add_l_cut() needs an existing cut to extend -- call add_plunge() first")
        if direction not in (1, -1):
            raise TrimSimError("direction must be 1 or -1")
        if turn_length_um <= 0:
            raise TrimSimError("turn_length_um must be positive")
        prev = self.segments[-1]
        if kerf_um is None:
            kerf_um = prev.kerf_um
        if steps < 1:
            raise TrimSimError("steps must be >= 1")

        tip_x, tip_y = prev.x1, prev.y1
        target_x = tip_x + direction * turn_length_um
        target_x = min(max(target_x, 0.0), self.length_um)

        seg = CutSegment(x0=tip_x, y0=tip_y, x1=tip_x, y1=tip_y, kerf_um=kerf_um, kind="lcut")
        self.segments.append(seg)

        results = []
        for step in range(1, steps + 1):
            x = tip_x + (target_x - tip_x) * step / steps
            seg.x1, seg.y1 = x, tip_y
            self._rebuild_conductances()
            results.append(self._solve_and_record("lcut"))
        return results

    # --- field solve ----------------------------------------------------
    def _solve_and_record(self, segment_kind: str) -> TrimResult:
        nx, ny = self.nx, self.ny
        idx = self.idx_grid
        V_left, V_right = self.operating_voltage, 0.0

        # --- cheap open-circuit check via union-find over active edges,
        # with all left-edge nodes unioned together and all right-edge
        # nodes unioned together (an ideal contact spanning the edge).
        uf = _UnionFind(nx * ny)
        hi, hj = np.nonzero(self._Gh > 0)
        for j, i in zip(hi, hj):
            uf.union(int(idx[j, i]), int(idx[j, i + 1]))
        vi, vj = np.nonzero(self._Gv > 0)
        for j, i in zip(vi, vj):
            uf.union(int(idx[j, i]), int(idx[j + 1, i]))
        left_nodes = idx[:, 0]
        right_nodes = idx[:, -1]
        for n in left_nodes[1:]:
            uf.union(int(left_nodes[0]), int(n))
        for n in right_nodes[1:]:
            uf.union(int(right_nodes[0]), int(n))
        is_open = uf.find(int(left_nodes[0])) != uf.find(int(right_nodes[0]))

        if is_open:
            self._last_power_grid = np.zeros((ny, nx))
            self._last_is_open = True
            resistance = math.inf
            current_a = 0.0
        else:
            V_full = self._solve_voltages(V_left, V_right)
            n1h = idx[:, :-1]
            n2h = idx[:, 1:]
            Ih = self._Gh * (V_full[n1h] - V_full[n2h])
            n1v = idx[:-1, :]
            n2v = idx[1:, :]
            Iv = self._Gv * (V_full[n1v] - V_full[n2v])

            current_a = float(np.sum(Ih[:, 0]))  # current leaving the left contact
            if abs(current_a) < 1e-15:
                resistance = math.inf
                self._last_is_open = True
            else:
                resistance = (V_left - V_right) / current_a
                self._last_is_open = False

            # Power density: split each edge's dissipation between its
            # two endpoint nodes, accumulate into a per-node grid.
            P = np.zeros(nx * ny)
            ph = np.abs(Ih * (V_full[n1h] - V_full[n2h])) / 2.0
            np.add.at(P, n1h.ravel(), ph.ravel())
            np.add.at(P, n2h.ravel(), ph.ravel())
            pv = np.abs(Iv * (V_full[n1v] - V_full[n2v])) / 2.0
            np.add.at(P, n1v.ravel(), pv.ravel())
            np.add.at(P, n2v.ravel(), pv.ravel())
            self._last_power_grid = P.reshape(ny, nx)

        self._cut_progress_um += self._last_segment_step_length(segment_kind)
        prev_r = self.history[-1].resistance_ohm if self.history else None
        prev_progress = self.history[-1].cut_progress_um if self.history else None
        sensitivity = None
        if prev_r is not None and math.isfinite(prev_r) and math.isfinite(resistance):
            dprogress = self._cut_progress_um - prev_progress
            if dprogress > 1e-9:
                sensitivity = (resistance - prev_r) / dprogress

        result = TrimResult(
            cut_progress_um=self._cut_progress_um,
            resistance_ohm=resistance,
            is_open=self._last_is_open,
            current_a=current_a,
            sensitivity_ohm_per_um=sensitivity,
            segment_kind=segment_kind,
        )
        self.history.append(result)
        self.power_history.append(self._last_power_grid.copy())
        return result

    def _last_segment_step_length(self, segment_kind: str) -> float:
        # Called once per solve step; the caller (add_plunge/add_l_cut)
        # advances the active segment's endpoint by depth/steps or
        # turn_length/steps just before calling us, so the increment is
        # simply the distance from the *previous* recorded progress to
        # the current segment tip -- recompute it from the segment
        # endpoints directly to avoid double-bookkeeping.
        seg = self.segments[-1]
        total_len = math.hypot(seg.x1 - seg.x0, seg.y1 - seg.y0)
        prior_len = sum(math.hypot(s.x1 - s.x0, s.y1 - s.y0) for s in self.segments[:-1])
        return (prior_len + total_len) - self._cut_progress_um

    def _solve_voltages(self, V_left: float, V_right: float) -> np.ndarray:
        nx, ny = self.nx, self.ny
        idx = self.idx_grid
        n_total = nx * ny

        fixed_value = np.full(n_total, np.nan)
        fixed_value[idx[:, 0]] = V_left
        fixed_value[idx[:, -1]] = V_right
        is_fixed = ~np.isnan(fixed_value)
        free_global = np.flatnonzero(~is_fixed)
        n_free = free_global.size
        g2l = -np.ones(n_total, dtype=np.int64)
        g2l[free_global] = np.arange(n_free)

        diag = np.zeros(n_free)
        b = np.zeros(n_free)
        rows: List[int] = []
        cols: List[int] = []
        vals: List[float] = []

        def add_edges(n1_grid, n2_grid, g_grid):
            n1 = n1_grid.ravel()
            n2 = n2_grid.ravel()
            g = g_grid.ravel()
            mask = g > 0
            n1, n2, g = n1[mask], n2[mask], g[mask]
            f1, f2 = is_fixed[n1], is_fixed[n2]

            both = (~f1) & (~f2)
            l1, l2, gg = g2l[n1[both]], g2l[n2[both]], g[both]
            np.add.at(diag, l1, gg)
            np.add.at(diag, l2, gg)
            rows.extend(l1.tolist()); cols.extend(l2.tolist()); vals.extend((-gg).tolist())
            rows.extend(l2.tolist()); cols.extend(l1.tolist()); vals.extend((-gg).tolist())

            only1 = (~f1) & f2
            l1, gg, vb = g2l[n1[only1]], g[only1], fixed_value[n2[only1]]
            np.add.at(diag, l1, gg)
            np.add.at(b, l1, gg * vb)

            only2 = f1 & (~f2)
            l2, gg, vb = g2l[n2[only2]], g[only2], fixed_value[n1[only2]]
            np.add.at(diag, l2, gg)
            np.add.at(b, l2, gg * vb)

        n1h, n2h = idx[:, :-1], idx[:, 1:]
        add_edges(n1h, n2h, self._Gh)
        n1v, n2v = idx[:-1, :], idx[1:, :]
        add_edges(n1v, n2v, self._Gv)

        # Regularization: a tiny leak-to-zero on every free node's
        # diagonal, so a subnetwork that ends up disconnected from both
        # terminals (an island the cut has pinched off) still yields a
        # solvable, non-singular system instead of crashing -- see the
        # module docstring. Negligible next to any real conductance.
        typical_g = max(self._base_gx, self._base_gy)
        diag = diag + typical_g * 1e-9

        rows.extend(range(n_free))
        cols.extend(range(n_free))
        vals.extend(diag.tolist())

        A = coo_matrix((vals, (rows, cols)), shape=(n_free, n_free)).tocsr()
        V_free = spsolve(A, b)

        V_full = np.empty(n_total)
        V_full[is_fixed] = fixed_value[is_fixed]
        V_full[free_global] = V_free
        return V_full

    # --- results --------------------------------------------------------
    def current_power_density_grid(self) -> Optional[np.ndarray]:
        """(ny, nx) grid of power dissipated at each node, in watts, at
        operating_voltage -- None if nothing has been solved yet."""
        return self._last_power_grid

    def power_density_at(self, index: int) -> np.ndarray:
        """The (ny, nx) power-density grid recorded at history[index] --
        for scrubbing back through a completed run's heat map."""
        return self.power_history[index]

    def is_open(self) -> bool:
        return self._last_is_open

    def cut_path_points(self):
        """[(x_um, y_um), ...] tracing the cut path so far, for
        overlaying on a plot/canvas of the resistor body."""
        if not self.segments:
            return []
        pts = [(self.segments[0].x0, self.segments[0].y0)]
        for seg in self.segments:
            pts.append((seg.x1, seg.y1))
        return pts
