"""Steady-state thermal analysis of a thick-film hybrid.

Models the hybrid as a stack of flat SLICES on one regular X/Y grid --
an optional mounting layer, the substrate (several slices), each
printed film in print order, then component attach and body slices --
and solves steady-state heat conduction

    div(k grad T) + q = 0

by the cell-centred finite-volume method: every grid cell of every
slice that actually contains material is one unknown temperature,
joined to its neighbours by a thermal conductance, with each part's
power injected into the cells it occupies. It is the same kind of
sparse network solve hlayout.core.trim_sim does in 2D for current,
done in 3D for heat.

Two things make it fit a printed hybrid rather than a generic block:

  * A slice need not exist everywhere. A film is only present where
    its layer has geometry, so each slice carries a per-cell THICKNESS
    map and a cell of zero thickness is simply not there. Vertically, a
    cell is joined to the next cell that IS present below it in the
    same column -- so a resistor printed straight onto the substrate
    is in contact with the substrate, not insulated from it by an
    empty cell of a dielectric slice that only exists elsewhere on the
    board.

  * Conductances between cells of different material use the series
    (harmonic) combination of the two half-cells, so a thin, poorly
    conducting layer (dielectric under a resistor, epoxy under a die)
    keeps its full thermal resistance instead of being averaged away.

Heat leaves through the base of the stack (a fixed temperature, or a
heat-transfer coefficient to ambient) and, optionally and weakly,
through the top surface.

This module knows nothing about layout geometry or the technology
file: hlayout.core.thermal_build turns a library + cell into a
ThermalModel, and tests build one by hand. Like the trim simulator it
is a design-stage tool -- conductivity is constant with temperature,
and the answer is only as good as the material values fed to it.

Requires numpy and scipy (see requirements.txt), imported lazily by
callers exactly as hlayout.core.trim_sim is.
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.sparse import coo_matrix, diags
from scipy.sparse.linalg import cg, splu

from .errors import LayoutError


class ThermalError(LayoutError):
    """Raised for an unsolvable or badly set-up thermal model."""


#: Named materials offered in the Materials list. Conductivity ``k`` is
#: in W/(m.K). These are APPROXIMATE general-reference values meant as
#: sensible starting points -- thick-film paste conductivities in
#: particular vary widely by paste and firing profile -- and every one
#: can be overridden per layer with a measured figure.
MATERIALS: Dict[str, dict] = {
    "alumina_96":   {"label": "96% alumina",                 "k": 24.0},
    "alumina_99":   {"label": "99.6% alumina",               "k": 30.0},
    "aln":          {"label": "Aluminium nitride",           "k": 170.0},
    "beo":          {"label": "Beryllia",                    "k": 250.0},
    "conductor":    {"label": "Fired thick-film conductor",  "k": 80.0},
    "dielectric":   {"label": "Thick-film dielectric",       "k": 3.0},
    "resistor":     {"label": "Resistor ink",                "k": 2.0},
    "glaze":        {"label": "Glaze / overglaze",           "k": 1.0},
    "silicon":      {"label": "Silicon die",                 "k": 130.0},
    "gaas":         {"label": "Gallium arsenide die",        "k": 46.0},
    "ceramic_chip": {"label": "Ceramic chip component",      "k": 5.0},
    "epoxy_ag":     {"label": "Silver-filled epoxy",         "k": 2.5},
    "epoxy":        {"label": "Non-conductive epoxy",        "k": 0.5},
    "solder":       {"label": "Solder",                      "k": 50.0},
    "grease":       {"label": "Thermal grease",              "k": 1.0},
    "kovar":        {"label": "Kovar package base",          "k": 17.0},
    "copper":       {"label": "Copper",                      "k": 390.0},
    "aluminium":    {"label": "Aluminium",                   "k": 200.0},
}


def material_k(name_or_k) -> float:
    """Conductivity (W/m.K) for a MATERIALS name, or a number given
    directly. Raises ThermalError for an unknown name or a value that
    isn't positive."""
    if isinstance(name_or_k, str):
        key = name_or_k.strip().lower()
        if key in MATERIALS:
            return float(MATERIALS[key]["k"])
        try:
            k = float(key)
        except ValueError:
            raise ThermalError(f"unknown thermal material {name_or_k!r}")
    else:
        k = float(name_or_k)
    if not k > 0:
        raise ThermalError(f"thermal conductivity must be positive, got {k}")
    return k


@dataclass
class Slice:
    """One horizontal slice of the stack, bottom to top.

    ``k`` (W/m.K) and ``t`` (thickness, micron) are (ny, nx) arrays;
    a cell with ``t == 0`` holds no material. ``q`` (watts per cell) is
    the heat generated in each cell."""
    name: str
    k: np.ndarray
    t: np.ndarray
    q: np.ndarray = None
    layer: Optional[int] = None   # layout layer this film came from, if any
    role: str = ""                # "mount" | "substrate" | "film" | "attach" | "body"

    def __post_init__(self):
        self.k = np.asarray(self.k, dtype=float)
        self.t = np.asarray(self.t, dtype=float)
        if self.q is None:
            self.q = np.zeros_like(self.k)
        self.q = np.asarray(self.q, dtype=float)


@dataclass
class Part:
    """A heat source / temperature-of-interest: one resistor, die or
    component. ``mask`` is the (ny, nx) set of cells it occupies in
    slice ``slice_index`` (where its power is injected and where its
    temperature is read)."""
    ref_des: str
    kind: str                 # "resistor" | "die" | "component"
    power_w: float
    slice_index: int
    mask: np.ndarray
    area_mm2: float = 0.0
    limit_c: Optional[float] = None
    cell_name: str = ""
    #: extra (slice_index, mask) pairs the power is ALSO spread over
    #: (a component body is more than one slice thick).
    extra: List[Tuple[int, np.ndarray]] = field(default_factory=list)

    def cells(self):
        return [(self.slice_index, self.mask)] + list(self.extra)


@dataclass
class PartResult:
    ref_des: str
    kind: str
    cell_name: str
    power_mw: float
    area_mm2: float
    t_max: float
    t_avg: float
    rise: float               # t_max above the base/reference temperature
    limit: Optional[float]
    ok: bool
    density_w_mm2: float = 0.0
    density_limit: Optional[float] = None

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class ThermalModel:
    """A stack of Slices on one X/Y grid, plus boundary conditions.

    ``pitch_um`` is the X and Y cell size (micron); ``origin`` the DBU
    coordinate of the grid's lower-left corner, so cell (j, i) covers
    x in [origin_x + i*pitch, +pitch), y likewise.

    Base boundary: ``base_mode`` "fixed" holds the underside of the
    lowest slice at ``base_temp_c``; "htc" joins it to ``ambient_c``
    through ``base_htc`` (W/m^2.K). Top boundary: ``top_htc`` (W/m^2.K)
    to ``ambient_c``; 0 means no heat leaves through the top."""

    def __init__(self, nx: int, ny: int, pitch_um: float, origin=(0.0, 0.0),
                 base_mode: str = "fixed", base_temp_c: float = 25.0,
                 ambient_c: float = 25.0, base_htc: float = 0.0,
                 top_htc: float = 0.0):
        if nx < 1 or ny < 1:
            raise ThermalError("thermal grid must be at least 1 x 1 cell")
        if not pitch_um > 0:
            raise ThermalError("thermal grid pitch must be positive")
        if base_mode not in ("fixed", "htc"):
            raise ThermalError("base_mode must be 'fixed' or 'htc'")
        self.nx, self.ny = int(nx), int(ny)
        self.pitch_um = float(pitch_um)
        self.origin = (float(origin[0]), float(origin[1]))
        self.base_mode = base_mode
        self.base_temp_c = float(base_temp_c)
        self.ambient_c = float(ambient_c)
        self.base_htc = float(base_htc)
        self.top_htc = float(top_htc)
        self.slices: List[Slice] = []
        self.parts: List[Part] = []
        self.warnings: List[str] = []
        self._system = None

    # --- building -------------------------------------------------------
    def add_slice(self, name: str, k, thickness_um, layer=None, role: str = "") -> int:
        """Append a slice on top of the stack. ``k`` and ``thickness_um``
        may each be a scalar (uniform everywhere) or an (ny, nx) array;
        zero thickness marks a cell as absent. Returns the slice index."""
        shape = (self.ny, self.nx)
        k_arr = np.broadcast_to(np.asarray(k, dtype=float), shape).copy()
        t_arr = np.broadcast_to(np.asarray(thickness_um, dtype=float), shape).copy()
        if np.any(t_arr < 0):
            raise ThermalError(f"slice {name!r} has a negative thickness")
        if np.any((t_arr > 0) & ~(k_arr > 0)):
            raise ThermalError(f"slice {name!r} has material with no conductivity")
        self.slices.append(Slice(name, k_arr, t_arr, layer=layer, role=role))
        self._system = None
        return len(self.slices) - 1

    def add_part(self, part: Part) -> Part:
        self.parts.append(part)
        return part

    def reference_temp(self) -> float:
        return self.base_temp_c if self.base_mode == "fixed" else self.ambient_c

    def cell_center(self, j: int, i: int) -> Tuple[float, float]:
        return (self.origin[0] + (i + 0.5) * self.pitch_um,
                self.origin[1] + (j + 0.5) * self.pitch_um)

    def cell_at(self, x: float, y: float) -> Optional[Tuple[int, int]]:
        i = int(math.floor((x - self.origin[0]) / self.pitch_um))
        j = int(math.floor((y - self.origin[1]) / self.pitch_um))
        if 0 <= i < self.nx and 0 <= j < self.ny:
            return j, i
        return None

    # --- assembly -------------------------------------------------------
    def _assemble(self):
        """Build the conductance matrix once (it doesn't depend on the
        powers): returns a dict with the CSR matrix ``A``, the constant
        boundary right-hand side ``b0``, the node index of every cell
        (``index``, -1 where absent) and the boundary conductances used
        afterwards for the energy balance."""
        if self._system is not None:
            return self._system
        if not self.slices:
            raise ThermalError("thermal model has no slices")
        nz, ny, nx = len(self.slices), self.ny, self.nx
        dx = self.pitch_um * 1e-6
        area = dx * dx

        present = np.stack([s.t > 0 for s in self.slices])
        index = np.full((nz, ny, nx), -1, dtype=np.int64)
        n = int(present.sum())
        if n == 0:
            raise ThermalError("thermal model contains no material")
        index[present] = np.arange(n)

        rows, cols, vals = [], [], []
        diag = np.zeros(n)
        b0 = np.zeros(n)

        def couple(a, b, g):
            rows.append(a); cols.append(b); vals.append(-g)
            rows.append(b); cols.append(a); vals.append(-g)
            np.add.at(diag, a, g)
            np.add.at(diag, b, g)

        # in-plane: sheet conductance k*t of each cell, two half-cells in series
        for z, s in enumerate(self.slices):
            kt = s.k * (s.t * 1e-6)
            for axis in (0, 1):
                a_sl = [slice(None), slice(None)]
                b_sl = [slice(None), slice(None)]
                a_sl[axis] = slice(0, -1)
                b_sl[axis] = slice(1, None)
                a_sl, b_sl = tuple(a_sl), tuple(b_sl)
                both = present[z][a_sl] & present[z][b_sl]
                if not both.any():
                    continue
                ka, kb = kt[a_sl][both], kt[b_sl][both]
                g = 2.0 * ka * kb / (ka + kb)
                couple(index[z][a_sl][both], index[z][b_sl][both], g)

        # vertical: each present cell to the next present cell below it
        below_node = np.full((ny, nx), -1, dtype=np.int64)
        below_r = np.zeros((ny, nx))          # half-cell resistance * area
        base_g = np.zeros(n)
        base_t = self.base_temp_c if self.base_mode == "fixed" else self.ambient_c
        for z, s in enumerate(self.slices):
            here = present[z]
            if not here.any():
                continue
            half = np.zeros((ny, nx))
            half[here] = (s.t[here] * 1e-6) / (2.0 * s.k[here])   # m^2.K/W
            joined = here & (below_node >= 0)
            if joined.any():
                g = area / (below_r[joined] + half[joined])
                couple(below_node[joined], index[z][joined], g)
            bottom = here & (below_node < 0)
            if bottom.any():
                r = half[bottom].copy()
                if self.base_mode == "htc":
                    if self.base_htc > 0:
                        r = r + 1.0 / self.base_htc
                    else:
                        r = None
                if r is not None:
                    g = area / r
                    nodes = index[z][bottom]
                    base_g[nodes] = g
                    diag[nodes] += g
                    b0[nodes] += g * base_t
            below_node[here] = index[z][here]
            below_r[here] = half[here]

        top_g = np.zeros(n)
        if self.top_htc > 0:
            topmost = below_node >= 0
            nodes = below_node[topmost]
            g = area / (below_r[topmost] + 1.0 / self.top_htc)
            top_g[nodes] = g
            diag[nodes] += g
            b0[nodes] += g * self.ambient_c

        if not (base_g.any() or top_g.any()):
            raise ThermalError(
                "thermal model has no path for heat to leave: set a fixed base "
                "temperature or a base/top heat-transfer coefficient")

        everyone = np.arange(n)
        A = coo_matrix((np.concatenate(vals + [diag]),
                        (np.concatenate(rows + [everyone]),
                         np.concatenate(cols + [everyone]))), shape=(n, n)).tocsr()
        self._system = {"A": A, "b0": b0, "index": index, "present": present,
                        "base_g": base_g, "top_g": top_g, "base_t": base_t,
                        "diag": diag, "n": n, "lu": None}
        return self._system

    #: Below this many unknowns a direct factorisation is used (fast, and
    #: reusable for every later solve on the same model); above it the
    #: fill-in of a 3D direct solve gets expensive, so conjugate gradient.
    DIRECT_LIMIT = 20000

    def _solve_vector(self, q_vec: np.ndarray, with_boundary: bool = True) -> np.ndarray:
        sysm = self._assemble()
        b = q_vec + (sysm["b0"] if with_boundary else 0.0)
        A, n = sysm["A"], sysm["n"]
        if n <= self.DIRECT_LIMIT:
            if sysm["lu"] is None:
                sysm["lu"] = splu(A.tocsc())
            return sysm["lu"].solve(b)
        inv_d = 1.0 / sysm["diag"]
        precond = diags(inv_d)
        x0 = np.full(n, self.reference_temp()) if with_boundary else np.zeros(n)
        try:
            x, info = cg(A, b, x0=x0, M=precond, rtol=1e-9, atol=0.0, maxiter=20000)
        except TypeError:      # scipy < 1.12 spells the tolerance "tol"
            x, info = cg(A, b, x0=x0, M=precond, tol=1e-9, atol=0.0, maxiter=20000)
        if info != 0:
            raise ThermalError(
                "thermal solve did not converge -- try a coarser grid, or check "
                "for a part with no thermal path to the base")
        return x

    def _q_vector(self, powers: Optional[Dict[str, float]] = None) -> np.ndarray:
        """Per-node heat (W): every slice's own ``q`` plus each part's
        power spread evenly over its cells. ``powers`` ({ref_des: watts})
        overrides individual parts' ``power_w``."""
        sysm = self._assemble()
        index = sysm["index"]
        q = np.zeros(sysm["n"])
        for z, s in enumerate(self.slices):
            m = sysm["present"][z] & (s.q != 0)
            if m.any():
                q[index[z][m]] += s.q[m]
        for part in self.parts:
            p = part.power_w if powers is None else powers.get(part.ref_des, part.power_w)
            if not p:
                continue
            nodes = self._part_nodes(part)
            if nodes.size == 0:
                msg = f"{part.ref_des}: no grid cell holds this part, its power was ignored"
                if msg not in self.warnings:
                    self.warnings.append(msg)
                continue
            np.add.at(q, nodes, p / nodes.size)
        return q

    def _part_nodes(self, part: Part) -> np.ndarray:
        index = self._assemble()["index"]
        out = []
        for z, mask in part.cells():
            nodes = index[z][mask]
            out.append(nodes[nodes >= 0])
        return np.concatenate(out) if out else np.zeros(0, dtype=np.int64)

    def _read_nodes(self, part: Part) -> np.ndarray:
        """Nodes a part's temperature is read from: its primary slice."""
        nodes = self._assemble()["index"][part.slice_index][part.mask]
        return nodes[nodes >= 0]

    # --- solving --------------------------------------------------------
    def solve(self, limits: Optional[dict] = None) -> "ThermalResult":
        """Solve for the temperature field at the parts' current powers
        and return a ThermalResult."""
        self._assemble()
        q = self._q_vector()
        t = self._solve_vector(q)
        return ThermalResult(self, t, q, limits or {})

    def coupling(self) -> "Coupling":
        """One solve per part at unit power. Because the problem is
        linear, the result gives every part's temperature for ANY set of
        powers with no further solve -- see Coupling.temperatures()."""
        sysm = self._assemble()
        n = sysm["n"]
        zero_q = np.zeros(n)
        for z, s in enumerate(self.slices):
            m = sysm["present"][z] & (s.q != 0)
            if m.any():
                zero_q[sysm["index"][z][m]] += s.q[m]
        t0 = self._solve_vector(zero_q)                 # field with every part off
        read = [self._read_nodes(p) for p in self.parts]
        base = [t0[r] if r.size else np.zeros(0) for r in read]
        unit = []
        for src in self.parts:
            nodes = self._part_nodes(src)
            if nodes.size == 0:
                unit.append([np.zeros(r.size) for r in read])
                continue
            q = np.zeros(n)
            np.add.at(q, nodes, 1.0 / nodes.size)
            rise = self._solve_vector(q, with_boundary=False)
            unit.append([rise[r] for r in read])
        return Coupling([p.ref_des for p in self.parts], base, unit,
                        self.reference_temp())


class Coupling:
    """Per-part temperature response to each part's power.

    ``unit[j][i]`` is the temperature RISE (deg C per watt) at every
    read cell of part i caused by one watt in part j; ``base[i]`` is
    part i's temperature with every part switched off."""

    def __init__(self, names, base, unit, reference_temp):
        self.names = list(names)
        self._base = base
        self._unit = unit
        self.reference_temp = reference_temp

    def matrix(self) -> List[List[float]]:
        """[i][j] = peak rise of part i (deg C) per watt in part j."""
        n = len(self.names)
        return [[float(self._unit[j][i].max()) if self._unit[j][i].size else 0.0
                 for j in range(n)] for i in range(n)]

    def temperatures(self, powers_w: Dict[str, float]) -> Dict[str, Tuple[float, float]]:
        """{ref_des: (t_max, t_avg)} for the given {ref_des: watts}
        (a part left out dissipates nothing)."""
        out = {}
        for i, name in enumerate(self.names):
            field_ = self._base[i].copy()
            if field_.size == 0:
                out[name] = (float("nan"), float("nan"))
                continue
            for j, src in enumerate(self.names):
                p = powers_w.get(src, 0.0)
                if p:
                    field_ = field_ + p * self._unit[j][i]
            out[name] = (float(field_.max()), float(field_.mean()))
        return out


class ThermalResult:
    """The solved temperature field plus per-part figures.

    ``limits`` may carry ``resistor_max_c``, ``die_max_c``,
    ``component_max_c`` (deg C) and ``resistor_max_w_mm2`` (power
    density); a part's own ``limit_c`` wins over the per-kind value."""

    def __init__(self, model: ThermalModel, t: np.ndarray, q: np.ndarray, limits: dict):
        self.model = model
        self._t = t
        self.limits = dict(limits)
        sysm = model._assemble()
        self.unknowns = sysm["n"]
        self.power_in = float(q.sum())
        self.power_base = float((sysm["base_g"] * (t - sysm["base_t"])).sum())
        self.power_top = float((sysm["top_g"] * (t - model.ambient_c)).sum())
        self.power_out = self.power_base + self.power_top
        self.max_temp = float(t.max())
        self.min_temp = float(t.min())
        self.warnings = list(model.warnings)
        self.parts: List[PartResult] = [self._part_result(p) for p in model.parts]

    @property
    def energy_error(self) -> float:
        """|heat out - heat in| as a fraction of heat in (0 when no power)."""
        if abs(self.power_in) < 1e-15:
            return 0.0
        return abs(self.power_out - self.power_in) / abs(self.power_in)

    def _part_result(self, part: Part) -> PartResult:
        nodes = self.model._read_nodes(part)
        ref = self.model.reference_temp()
        if nodes.size:
            t_max, t_avg = float(self._t[nodes].max()), float(self._t[nodes].mean())
        else:
            t_max = t_avg = float("nan")
        limit = part.limit_c
        if limit is None:
            limit = self.limits.get(f"{part.kind}_max_c")
        density = part.power_w / part.area_mm2 if part.area_mm2 > 0 else 0.0
        d_limit = self.limits.get("resistor_max_w_mm2") if part.kind == "resistor" else None
        ok = True
        if limit is not None and t_max == t_max and t_max > float(limit):
            ok = False
        if d_limit is not None and density > float(d_limit):
            ok = False
        return PartResult(part.ref_des, part.kind, part.cell_name, part.power_w * 1000.0,
                          part.area_mm2, t_max, t_avg, t_max - ref,
                          None if limit is None else float(limit), ok,
                          density, None if d_limit is None else float(d_limit))

    @property
    def failures(self) -> List[PartResult]:
        return [p for p in self.parts if not p.ok]

    def slice_names(self) -> List[str]:
        return [s.name for s in self.model.slices]

    def slice_index(self, which) -> int:
        """Resolve a slice by index, by name, or by layout layer number
        given as ``"layer:<n>"``."""
        slices = self.model.slices
        if isinstance(which, int) and not isinstance(which, bool):
            if -len(slices) <= which < len(slices):
                return which % len(slices)
            raise ThermalError(f"no thermal slice {which}")
        text = str(which)
        if text.lower().startswith("layer:"):
            n = int(text.split(":", 1)[1])
            hits = [i for i, s in enumerate(slices) if s.layer == n]
            if hits:
                return hits[-1]
            raise ThermalError(f"no thermal slice for layer {n}")
        for i, s in enumerate(slices):
            if s.name.lower() == text.lower():
                return i
        raise ThermalError(f"no thermal slice named {which!r}")

    def slice_temps(self, which) -> np.ndarray:
        """(ny, nx) temperatures of one slice, NaN where it has no material."""
        z = self.slice_index(which)
        index = self.model._assemble()["index"][z]
        out = np.full(index.shape, np.nan)
        m = index >= 0
        out[m] = self._t[index[m]]
        return out

    def surface_temps(self) -> np.ndarray:
        """(ny, nx) temperature of the topmost material in every column
        -- what an infrared camera looking down would see."""
        index = self.model._assemble()["index"]
        out = np.full(index.shape[1:], np.nan)
        for z in range(index.shape[0]):
            m = index[z] >= 0
            out[m] = self._t[index[z][m]]
        return out

    def temperature_at(self, point, which=None) -> float:
        """Temperature at DBU ``point`` in slice ``which`` (default: the
        top surface). NaN if there is no material there."""
        cell = self.model.cell_at(float(point[0]), float(point[1]))
        if cell is None:
            return float("nan")
        grid = self.surface_temps() if which is None else self.slice_temps(which)
        return float(grid[cell])

    def summary_lines(self) -> List[str]:
        m = self.model
        lines = [
            f"thermal: {self.unknowns} unknowns, grid {m.nx} x {m.ny} at "
            f"{m.pitch_um:g} um, {len(m.slices)} slices",
            f"  power in {self.power_in * 1000:.1f} mW, out {self.power_out * 1000:.1f} mW "
            f"(base {self.power_base * 1000:.1f}, top {self.power_top * 1000:.1f}); "
            f"energy balance error {self.energy_error * 100:.3f}%",
            f"  hottest point {self.max_temp:.1f} C "
            f"({self.max_temp - m.reference_temp():.1f} C above reference)",
        ]
        for p in sorted(self.parts, key=lambda r: -(r.t_max if r.t_max == r.t_max else -1e9)):
            lim = "" if p.limit is None else f"  limit {p.limit:.0f} C"
            flag = "" if p.ok else "  ** OVER LIMIT **"
            lines.append(f"  {p.ref_des:<10} {p.kind:<9} {p.power_mw:8.1f} mW  "
                         f"max {p.t_max:6.1f} C  avg {p.t_avg:6.1f} C{lim}{flag}")
        for w in self.warnings:
            lines.append(f"  warning: {w}")
        return lines


# --- isotherms ---------------------------------------------------------------

def contour_segments(grid: np.ndarray, level: float):
    """Marching-squares line segments of ``grid == level``, in grid
    coordinates where cell (j, i)'s centre is at (i, j). Cells touching
    a NaN are skipped. Returns [((x0, y0), (x1, y1)), ...]."""
    g = np.asarray(grid, dtype=float)
    ny, nx = g.shape
    segs = []
    if ny < 2 or nx < 2:
        return segs
    a, b = g[:-1, :-1], g[:-1, 1:]      # lower-left, lower-right
    c, d = g[1:, 1:], g[1:, :-1]        # upper-right, upper-left
    valid = ~(np.isnan(a) | np.isnan(b) | np.isnan(c) | np.isnan(d))
    code = ((a > level).astype(int) | ((b > level).astype(int) << 1)
            | ((c > level).astype(int) << 2) | ((d > level).astype(int) << 3))
    code = np.where(valid, code, 0)
    js, is_ = np.nonzero((code != 0) & (code != 15))

    def lerp(v0, v1):
        dv = v1 - v0
        return 0.5 if dv == 0 else min(1.0, max(0.0, (level - v0) / dv))

    # edges: 0 bottom (a-b), 1 right (b-c), 2 top (d-c), 3 left (a-d)
    table = {1: [(3, 0)], 2: [(0, 1)], 3: [(3, 1)], 4: [(1, 2)], 5: [(3, 2), (0, 1)],
             6: [(0, 2)], 7: [(3, 2)], 8: [(2, 3)], 9: [(0, 2)], 10: [(0, 3), (1, 2)],
             11: [(1, 2)], 12: [(1, 3)], 13: [(0, 1)], 14: [(3, 0)]}
    for j, i in zip(js.tolist(), is_.tolist()):
        va, vb, vc, vd = a[j, i], b[j, i], c[j, i], d[j, i]

        def pt(edge):
            if edge == 0:
                return (i + lerp(va, vb), j)
            if edge == 1:
                return (i + 1, j + lerp(vb, vc))
            if edge == 2:
                return (i + lerp(vd, vc), j + 1)
            return (i, j + lerp(va, vd))

        for e0, e1 in table[int(code[j, i])]:
            segs.append((pt(e0), pt(e1)))
    return segs


# --- false-colour image --------------------------------------------------------

_RAMP = [(0.00, (20, 24, 82)), (0.25, (33, 102, 172)), (0.50, (60, 170, 120)),
         (0.75, (240, 200, 40)), (1.00, (200, 30, 30))]


def heat_color(frac: float) -> Tuple[int, int, int]:
    """Cold-to-hot colour for ``frac`` in 0..1."""
    f = min(1.0, max(0.0, float(frac)))
    for (f0, c0), (f1, c1) in zip(_RAMP, _RAMP[1:]):
        if f <= f1:
            u = 0.0 if f1 == f0 else (f - f0) / (f1 - f0)
            return tuple(int(round(c0[n] + u * (c1[n] - c0[n]))) for n in range(3))
    return _RAMP[-1][1]


def temps_to_rgb(grid: np.ndarray, t_min: float = None, t_max: float = None,
                 background=(255, 255, 255)) -> np.ndarray:
    """(ny, nx) temperatures -> (ny, nx, 3) uint8 image, row 0 at the
    TOP (so it's the right way up as an image: layout +y is up). NaN
    cells take ``background``."""
    g = np.asarray(grid, dtype=float)
    valid = ~np.isnan(g)
    if t_min is None:
        t_min = float(np.nanmin(g)) if valid.any() else 0.0
    if t_max is None:
        t_max = float(np.nanmax(g)) if valid.any() else 1.0
    span = (t_max - t_min) or 1.0
    frac = np.clip((np.where(valid, g, t_min) - t_min) / span, 0.0, 1.0)
    stops = np.array([s[0] for s in _RAMP])
    cols = np.array([s[1] for s in _RAMP], dtype=float)
    rgb = np.stack([np.interp(frac, stops, cols[:, n]) for n in range(3)], axis=-1)
    rgb = np.round(rgb).astype(np.uint8)
    rgb[~valid] = np.array(background, dtype=np.uint8)
    return rgb[::-1].copy()


def rgb_to_png(rgb: np.ndarray, scale: int = 1) -> bytes:
    """Encode an (h, w, 3) uint8 array as a PNG, each pixel repeated
    ``scale`` times in both directions. Pure standard library + numpy,
    so neither the report nor the GUI needs an imaging package."""
    import struct
    import zlib
    img = np.asarray(rgb, dtype=np.uint8)
    if scale > 1:
        img = np.repeat(np.repeat(img, scale, axis=0), scale, axis=1)
    h, w = img.shape[:2]
    raw = np.concatenate([np.zeros((h, 1), dtype=np.uint8), img.reshape(h, w * 3)], axis=1)

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw.tobytes(), 6))
            + chunk(b"IEND", b""))
