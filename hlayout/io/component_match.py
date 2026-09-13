"""Automatic FreeCAD component-model matching.

Guesses which placed cells are surface-mounted parts matching a known
package already built into the FreeCAD component library
(hlayout.io.component_library) from geometry/name alone, so
write_freecad_macro()'s ``component_models`` override can be filled in
without hand-mapping every cell in Setup > Technology Properties --
see build_match_report(). A manual entry for a cell always wins; this
module only ever proposes filling GAPS, never overrides an existing
entry, and never writes anything to the technology file itself -- the
caller decides how to use the result, typically as
write_freecad_macro()'s own ``component_models=`` override (see that
parameter's own doc: passing a dict there replaces, rather than saves
into, the design's saved tech-param).

Two independent strategies, chosen by category:

  * Passives (chip caps/resistors/etc): the placed cell's own NAME is
    searched for a known EIA case-size token (e.g. "0805"), derived
    from component_library.DEFAULT_CERAMIC_CAPS's own names so a case
    size added there becomes matchable with no change here.

  * Multi-lead gull-wing packages (the SOT family, and PKG20 -- both
    share the same "N leads per side at a pitch" shape): the placed
    cell's own footprint is inspected -- every own-shape Rectangle/
    Polygon carrying a non-empty ``pin`` is grouped by pin string, and
    the resulting pin count + span is compared against a table built
    from DEFAULT_SOT_PACKAGES'/DEFAULT_PKG20's own lead-position
    kwargs (not a hand-duplicated table). A real footprint may lay its
    two pad rows out along either local axis (this repo's own
    examples/generate_sot_packages.py puts the pitch along Y where
    component_library.py's 3D generator puts it along X, for the same
    physical SOT-23) -- so both axes are measured and either one
    matching the table's value counts, making the match orientation-
    independent instead of assuming one convention.

Once a package is matched (either way), its body height is whatever
component_library.py already assumes for that package -- there's no
attempt to infer height from the 2D footprint, since a footprint alone
never tells you how tall the part sitting on it is.

This module only emits data (dataclasses/dicts) -- its only filesystem
access is an os.path.isfile() probe against an already-built STEP
library folder (see find_step_file()). No FreeCAD, Tk, or shapely
import -- shapely is only needed by the geometry-merging side of this
package (maskops/dxfio/freecadio's film-layer extrusion), never here.
"""

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..core.errors import LayoutError
from ..core.library import Library
from ..core.shapes import Polygon, Rectangle
from ..core.units import DBU_MM
from .component_library import (
    DEFAULT_CERAMIC_CAPS,
    DEFAULT_PKG20,
    DEFAULT_SOT_PACKAGES,
    _soic_leads,
)
from .freecadio import _walk_instances, is_component_cell
from .techfile import default_layer_thickness, load_layer_thickness

#: How far a candidate's measured pin span may miss the package
#: table's own value and still count as a match -- generous, since a
#: PCB land pattern's pad pitch is laid out independently of (and
#: usually a little wider than) the lead pitch baked into the 3D body;
#: pad size/shape isn't modeled here at all, only pin count + spacing.
X_SPAN_TOLERANCE = 0.30


@dataclass
class Match:
    """One auto-matched placed cell, ready to use as a
    write_freecad_macro() ``component_models`` entry."""
    cell_name: str
    package: str          # e.g. "CAP_0805" or "SOT_23"
    category: str          # "passive" | "gullwing"
    reason: str            # human-readable, for the report
    entry: dict             # {"mode": "model", "model_path": ...}


@dataclass
class Unmatched:
    """A placed component cell nothing could be confidently matched
    to -- always carries a reason, so a bad or missing match is never
    silently invisible."""
    cell_name: str
    reason: str


@dataclass
class MatchReport:
    matches: List[Match] = field(default_factory=list)
    unmatched: List[Unmatched] = field(default_factory=list)
    skipped_manual: List[str] = field(default_factory=list)
    base_component_models: dict = field(default_factory=dict)

    def component_models(self) -> dict:
        """``base_component_models`` with every match layered on top --
        safe to pass straight to write_freecad_macro()/
        Session.export_freecad()'s ``component_models=`` unconditionally,
        even when nothing matched: it degenerates to exactly
        ``base_component_models``, the same dict write_freecad_macro()
        would have used anyway via ``component_models=None``. Never
        mutates ``base_component_models`` itself."""
        out = dict(self.base_component_models)
        out.update({m.cell_name: m.entry for m in self.matches})
        return out

    def summary_lines(self) -> List[str]:
        """Human-readable report lines for a GUI dialog (or a script's
        own printout) -- never leaves an empty/partial result
        ambiguous about why."""
        lines: List[str] = []
        if self.matches:
            lines.append(f"Matched {len(self.matches)} component cell(s):")
            for m in sorted(self.matches, key=lambda m: m.cell_name):
                lines.append(f"  {m.cell_name} -> {m.package}  ({m.reason})")
        if self.skipped_manual:
            lines.append("")
            lines.append(f"Left as-is (already manually configured): "
                         f"{', '.join(sorted(self.skipped_manual))}")
        if self.unmatched:
            lines.append("")
            lines.append(f"Not matched -- left as a flat extruded box "
                         f"({len(self.unmatched)}):")
            for u in sorted(self.unmatched, key=lambda u: u.cell_name):
                lines.append(f"  {u.cell_name}: {u.reason}")
        if not lines:
            lines.append("No placed component cells found to match.")
        return lines


def eia_size_tokens(ceramic_caps=None) -> Dict[str, str]:
    """{"0805": "CAP_0805", ...} derived from DEFAULT_CERAMIC_CAPS's
    own names (or an override list of the same (name, L, W, H, term)
    shape) by pulling the trailing digit run out of each name -- a
    case size added to the library becomes matchable automatically,
    with no change needed here."""
    caps = DEFAULT_CERAMIC_CAPS if ceramic_caps is None else ceramic_caps
    out = {}
    for name, *_rest in caps:
        m = re.search(r"(\d+)$", name)
        if m:
            out[m.group(1)] = name
    return out


def match_passive_by_name(cell_name: str, tokens: Optional[dict] = None) -> Optional[str]:
    """Ceramic-cap package name whose EIA size token appears in
    ``cell_name`` as a standalone digit run (not embedded in a longer
    one, e.g. "PART120605" doesn't match "1206") -- e.g. "R1_0805" or
    "C3-1206-X" match; a name with zero or more than one distinct
    token found is left unmatched (an ambiguous name isn't guessed
    at) -- see match_gullwing_by_footprint() for the other family."""
    tokens = eia_size_tokens() if tokens is None else tokens
    found = {pkg for tok, pkg in tokens.items()
             if re.search(rf"(?<!\d){re.escape(tok)}(?!\d)", cell_name)}
    return next(iter(found)) if len(found) == 1 else None


def _lead_positions(entry: dict) -> List[float]:
    return [x for x, _w in entry.get("leads_neg_y", ())] + \
           [x for x, _w in entry.get("leads_pos_y", ())]


def _has_wide_tab_lead(entry: dict) -> bool:
    """True if ``entry`` has a heat_tab (SOT-89) or any lead more than
    2x wider than the narrowest one (SOT-223's wide gull-wing tab
    lead) -- these packages mix a normal pitch-defining row of leads
    with one disproportionate tab, so "combined span of every lead
    position" isn't a stable pitch signal for them the way it is for a
    uniform-width family: empirically, matching against them produced
    a confirmed false positive (a real SOT-89 footprint's body-width
    measurement coincided numerically with SOT-223's combined-lead
    span) when tested against examples/generate_sot_packages.py's real
    fixture data. Excluded from the auto-footprint-matching table
    entirely rather than risk a confidently wrong substitution --
    still perfectly buildable/usable via a manual component_models
    entry, just not auto-matched by footprint."""
    if entry.get("heat_tab"):
        return True
    widths = [w for _x, w in _lead_widths(entry)]
    return bool(widths) and max(widths) > 2 * min(widths)


def _lead_widths(entry: dict):
    return list(entry.get("leads_neg_y", ())) + list(entry.get("leads_pos_y", ()))


def gullwing_package_table(sot_packages=None, pkg20=None) -> Dict[str, dict]:
    """{package_name: {"pin_count", "x_span_mm"}} derived from
    DEFAULT_SOT_PACKAGES's own leads_neg_y/leads_pos_y kwargs (pin
    count = total lead count; x_span_mm = max-to-min of every lead's
    own X position, across BOTH sides -- the intra-row lead pitch, by
    construction of make_sot()) plus one synthetic "PKG20" entry built
    the same way from DEFAULT_PKG20's leads_per_side/pitch_mm (reusing
    component_library._soic_leads(), the same helper component_library
    itself uses for the SOT-14/16/18 entries), so PKG20 needs no
    separate hand-written table entry. Passing an override for either
    changes what's derived here automatically -- nothing in this
    module hand-duplicates the library's own geometry.

    A tab-style package (see _has_wide_tab_lead()) is left out of the
    table entirely -- match_gullwing_by_footprint() can never propose
    it, so those packages are only ever wired in manually."""
    sots = DEFAULT_SOT_PACKAGES if sot_packages is None else sot_packages
    pkg = DEFAULT_PKG20 if pkg20 is None else pkg20

    table: Dict[str, dict] = {}
    for name, entry in sots.items():
        if _has_wide_tab_lead(entry):
            continue
        xs = _lead_positions(entry)
        if not xs:
            continue
        table[name] = {
            "pin_count": len(entry.get("leads_neg_y", ())) + len(entry.get("leads_pos_y", ())),
            "x_span_mm": max(xs) - min(xs),
        }

    leads = _soic_leads(pkg["leads_per_side"], pkg["pitch_mm"])
    xs = [x for x, _w in leads]
    table["PKG20"] = {
        "pin_count": pkg["leads_per_side"] * 2,
        "x_span_mm": max(xs) - min(xs),
    }
    return table


def footprint_pin_stats(cell) -> Optional[dict]:
    """{"pin_count", "x_span_mm", "y_span_mm"} from ``cell``'s own
    (non-recursive) Rectangle/Polygon shapes carrying a non-empty
    ``pin`` -- one centroid per DISTINCT pin string (so more than one
    shape sharing a pin label, e.g. a wide tab lead drawn as two
    rectangles, still counts once), or None if fewer than 2 distinct
    pins (not enough signal to mean anything)."""
    centroids: Dict[str, List[Tuple[float, float]]] = {}
    for sh in cell.shapes:
        if not isinstance(sh, (Rectangle, Polygon)):
            continue
        pin = getattr(sh, "pin", "")
        if not pin:
            continue
        x0, y0, x1, y1 = sh.bbox()
        centroids.setdefault(pin, []).append(((x0 + x1) / 2.0, (y0 + y1) / 2.0))

    if len(centroids) < 2:
        return None

    pts = []
    for group in centroids.values():
        cx = sum(p[0] for p in group) / len(group)
        cy = sum(p[1] for p in group) / len(group)
        pts.append((cx, cy))

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return {
        "pin_count": len(centroids),
        "x_span_mm": (max(xs) - min(xs)) * DBU_MM,
        "y_span_mm": (max(ys) - min(ys)) * DBU_MM,
    }


def match_gullwing_by_footprint(stats: dict, table: Optional[dict] = None,
                                 tolerance: float = X_SPAN_TOLERANCE) -> Optional[str]:
    """Package name whose (pin_count, x_span_mm) matches ``stats`` --
    exact pin-count required, plus EITHER of the candidate's own
    x_span_mm or y_span_mm falling within +/-``tolerance`` of the
    table's x_span_mm. Checking both of the candidate's axes (not just
    its own X) is what makes this orientation-independent: a real
    footprint may run its lead pitch along either local axis (see this
    module's own docstring), and the table's x_span_mm is always "the
    intra-row pitch" regardless of which axis a given footprint
    happens to use for it. None if zero or more than one package
    matches (ambiguous -> left unmatched)."""
    table = gullwing_package_table() if table is None else table
    pin_count = stats["pin_count"]
    x_span = stats["x_span_mm"]
    y_span = stats.get("y_span_mm", x_span)

    found = set()
    for name, spec in table.items():
        if spec["pin_count"] != pin_count:
            continue
        expected = spec["x_span_mm"]
        if expected <= 0:
            continue
        lo, hi = expected * (1 - tolerance), expected * (1 + tolerance)
        if lo <= x_span <= hi or lo <= y_span <= hi:
            found.add(name)
    return next(iter(found)) if len(found) == 1 else None


def find_step_file(step_dir: str, package_name: str) -> Optional[str]:
    """Absolute path to ``<step_dir>/<package_name>.step`` if it
    already exists, else None -- the point at which a match is
    discarded rather than ever wired to a model_path that doesn't
    exist (falls back to today's flat-extrude default)."""
    p = os.path.abspath(os.path.join(step_dir, package_name + ".step"))
    return p if os.path.isfile(p) else None


def build_match_report(library: Library, cell_name: str, step_dir: str, *,
                        component_models: Optional[dict] = None,
                        ceramic_caps=None, sot_packages=None, pkg20=None,
                        tolerance: float = X_SPAN_TOLERANCE) -> MatchReport:
    """The dry run: walk ``cell_name``'s flattened hierarchy exactly
    like write_freecad_macro() will (via freecadio._walk_instances(),
    so the same is_component_cell() test applies), collect every
    DISTINCT placed component cell name, skip any that already have a
    manual ``component_models`` entry, and for the rest try
    match_passive_by_name() then match_gullwing_by_footprint() in
    turn -- accepting a match only if ``<package>.step`` already
    exists in ``step_dir``. ``component_models`` defaults to
    ``{}`` (the caller normally passes ``load_component_models(library)``
    -- see Session.match_freecad_components()).

    ``ceramic_caps``/``sot_packages``/``pkg20`` mirror
    generate_component_library()'s own overrides -- pass the SAME
    overrides you generated the library with, or a renamed/custom
    package won't have a name/footprint table entry to match against
    (it'll just report unmatched, never a broken model_path).

    Raises LayoutError -- the same message write_freecad_macro() uses
    -- if no layer has a fired-film thickness, since the film-layer
    heuristic used to distinguish "component" from "just more
    geometry" would be meaningless."""
    if cell_name not in library.cells:
        raise LayoutError(f"no cell named {cell_name!r} in the library")

    thick = load_layer_thickness(library)
    if not thick:
        thick = default_layer_thickness(library)
    thick = {str(int(k)): float(v) for k, v in thick.items() if float(v) > 0}
    if not thick:
        raise LayoutError(
            "no layer has a fired-film thickness -- set them in Setup, or "
            "the design's role config (resistor/route/bond/dielectric "
            "layers) so the heuristic can seed them"
        )
    film_layers = {int(k) for k in thick}

    base_models = dict(component_models) if component_models else {}

    names = set()
    for kind, payload in _walk_instances(library, cell_name, film_layers, base_models):
        if kind == "component":
            names.add(payload[0])

    tokens = eia_size_tokens(ceramic_caps)
    table = gullwing_package_table(sot_packages, pkg20)

    report = MatchReport(base_component_models=base_models)
    for cn in sorted(names):
        if cn in base_models:
            report.skipped_manual.append(cn)
            continue

        package = match_passive_by_name(cn, tokens)
        category = "passive"
        if package is None:
            c = library.cells.get(cn)
            stats = footprint_pin_stats(c) if c is not None else None
            if stats is not None:
                package = match_gullwing_by_footprint(stats, table, tolerance)
                category = "gullwing"

        if package is None:
            report.unmatched.append(Unmatched(
                cn, "no size code in the name and no matching pad footprint"))
            continue

        step_path = find_step_file(step_dir, package)
        if step_path is None:
            report.unmatched.append(Unmatched(
                cn, f"matched {package} but {package}.step isn't in {step_dir} yet -- "
                    f"run File > Build FreeCAD Component Library... first"))
            continue

        reason = "name" if category == "passive" else "footprint"
        report.matches.append(Match(
            cell_name=cn, package=package, category=category,
            reason=f"matched by {reason}",
            entry={"mode": "model", "model_path": step_path},
        ))

    return report
