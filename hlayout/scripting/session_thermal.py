# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Thermal analysis: part powers, the solve, reports / CSV and contours.

Part of hlayout.scripting.api.Session (a mixin): these methods are
Session's own -- call them on a Session as always."""

from typing import List, Optional

from ..core.errors import LayoutError, LibraryError
from ..core.maskops import shapely_lines_to_paths


class ThermalMixin:
    # --- thermal analysis ---------------------------------------------------
    def thermal_parts(self, cell_name: Optional[str] = None) -> List[dict]:
        """Every placed resistor and component in ``cell_name`` (default:
        the current cell) that can be given a power, as
        [{"ref_des", "kind", "cell", "power_mw", "source"}, ...].
        ``source`` is "thermal" (set by set_thermal_power()), "designer"
        (the Resistor Designer row with that reference designator),
        "cell" (no row has that reference designator, so the row the
        resistor's cell was generated from) or "" (none: 0 mW). This
        is the list Tools > Thermal Analysis...'s Powers tab shows.
        Requires shapely."""
        from ..core.thermal_build import candidate_parts
        name = cell_name or self.current_cell_name
        if name is None:
            raise LibraryError("no current cell")
        return candidate_parts(self.library, name)

    def set_thermal_power(self, ref_des: str, power_mw: Optional[float]) -> None:
        """Set the power (milliwatts) a part dissipates in a thermal
        analysis, by reference designator. A resistor left unset uses
        its Resistor Designer power; anything else left unset is 0.
        Pass None to clear a saved power. Saved with the technology
        (THERMAL POWER <ref_des> <mW>)."""
        from ..io.techfile import load_thermal_rules, save_thermal_rules
        rules = load_thermal_rules(self.library)
        key = str(ref_des)
        if power_mw is None:
            rules["powers"].pop(key, None)
        else:
            p = float(power_mw)
            if p < 0:
                raise LayoutError("power must not be negative")
            rules["powers"][key] = p
        self._checkpoint()
        save_thermal_rules(self.library, rules)
        self._notify()

    def run_thermal(self, cell_name: Optional[str] = None, grid: Optional[float] = None,
                    base_temp: Optional[float] = None, rules: Optional[dict] = None):
        """Steady-state thermal analysis of ``cell_name`` (default: the
        current cell): how hot each printed resistor, die and powered
        component runs.

        The layout is turned into a stack of slices on a grid -- the
        substrate, then each printed film where it is actually printed,
        then powered components -- and heat conduction is solved with
        each part's power injected where it sits (see
        hlayout.core.thermal / thermal_build). Film thicknesses and
        layer roles are the ones FreeCAD export uses; materials, powers,
        part build-ups, boundary conditions and limits come from the
        thermal rules (hlayout.io.techfile.load_thermal_rules(); Tools >
        Thermal Analysis... edits them).

        ``grid`` is the X/Y cell size in DBU (micron); left out, the
        saved value or an automatic one (100 um or coarser) is used.
        ``base_temp`` overrides the saved base temperature (deg C) for
        this run. ``rules`` substitutes a complete rules dict for the
        saved one without saving it.

        Returns a hlayout.core.thermal.ThermalResult: ``.parts`` (one
        PartResult per part: ref_des, kind, power_mw, t_max, t_avg,
        rise, limit, ok), ``.failures``, ``.max_temp``,
        ``.temperature_at((x, y))``, ``.slice_temps(which)``,
        ``.energy_error`` and ``.summary_lines()``. The result is also
        kept as ``session.last_thermal_result``. Never modifies the
        design. Requires numpy, scipy and shapely."""
        from ..core.thermal_build import build_thermal_model
        from ..io.techfile import load_thermal_rules
        name = cell_name or self.current_cell_name
        if name is None:
            raise LibraryError("no current cell")
        rules = rules or load_thermal_rules(self.library)
        model = build_thermal_model(self.library, name, grid_um=grid, base_temp=base_temp,
                                    rules=rules)
        result = model.solve(rules["limits"])
        self.last_thermal_result = result
        return result

    def export_thermal_report(self, result, path: str) -> str:
        """Write run_thermal()'s result as a self-contained HTML report
        (parts table with failures first, temperature maps, stack and
        materials, boundary conditions, energy balance). Returns
        ``path``."""
        from ..io.techfile import load_thermal_rules
        from ..io.thermalio import write_thermal_html
        return write_thermal_html(result, path,
                                  display=load_thermal_rules(self.library)["display"])

    def export_thermal_csv(self, result, path: str) -> str:
        """Write run_thermal()'s parts table to a CSV file, hottest part
        first. Returns ``path``."""
        from ..io.thermalio import write_thermal_csv
        return write_thermal_csv(result, path)

    def draw_thermal_contours(self, result, layer, step: float = 5.0, which=None,
                              width: Optional[float] = None,
                              cell_name: Optional[str] = None) -> int:
        """Draw run_thermal()'s isotherms as Path shapes on ``layer`` of
        ``cell_name`` (default: the current cell), one contour every
        ``step`` deg C, so the temperature picture can be viewed over
        the layout and printed with the assembly drawing. ``which``
        picks the slice (a name, an index, or "layer:<n>"); left out,
        the top surface is used. ``width`` (DBU) defaults to a fifth of
        the thermal grid pitch. Appends to the layer -- CLEAR it first
        for a clean redraw. One undo step; returns the number of paths
        drawn. Requires shapely."""
        import numpy as np
        from shapely.geometry import LineString
        from shapely.ops import linemerge
        from ..core.thermal import contour_segments
        if not step > 0:
            raise LayoutError("contour step must be positive")
        r_layer = self.library.layers.resolve(layer)
        name = cell_name or self.current_cell_name
        if name is None:
            raise LibraryError("no current cell")
        cell = self.library.get_cell(name)
        model = result.model
        grid = result.surface_temps() if which is None else result.slice_temps(which)
        if not np.isfinite(grid).any():
            return 0
        lo, hi = float(np.nanmin(grid)), float(np.nanmax(grid))
        ref = model.reference_temp()
        # Levels are the reference temperature plus whole steps, strictly
        # inside the field's range. The margin keeps solver round-off (a
        # cell at 24.99999998 C on a 25 C base) from producing a junk
        # "contour" at the reference temperature itself.
        tol = 1e-6 * max(hi - lo, 1e-9)
        k0 = int(np.floor((lo + tol - ref) / step)) + 1
        k1 = int(np.ceil((hi - tol - ref) / step)) - 1
        levels = [ref + k * step for k in range(k0, k1 + 1)]
        pitch, (ox, oy) = model.pitch_um, model.origin
        lines = []
        for level in levels:
            for (x0, y0), (x1, y1) in contour_segments(grid, float(level)):
                a = (ox + (x0 + 0.5) * pitch, oy + (y0 + 0.5) * pitch)
                b = (ox + (x1 + 0.5) * pitch, oy + (y1 + 0.5) * pitch)
                if a != b:
                    lines.append(LineString([a, b]))
        if not lines:
            return 0
        merged = linemerge(lines)
        w = int(round(width)) if width else max(10, int(round(pitch / 5.0)))
        paths = shapely_lines_to_paths(merged, r_layer, w)
        with self._batch():
            cell.shapes.extend(paths)
        self._notify()
        return len(paths)
