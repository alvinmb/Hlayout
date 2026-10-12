"""Resistor geometry DRC.

Checks every resistor CELL DEFINITION in a library against the tech
file's ``resistor_geometry_rules`` (see hlayout.io.techfile). Unlike the
generic hlayout.core.drc engine -- which works on flattened, placed
geometry -- this module checks each resistor's own local-frame CELL
DEFINITION directly. Session.generate_resistor() always draws a
resistor's ink/glaze/pad/keepout shapes axis-aligned in the resistor's
own cell, regardless of how that cell is later placed (rotated,
mirrored, arrayed) via Sref/Aref. Checking the cell definition is
therefore rotation- and placement-agnostic by construction, and avoids
oriented-bounding-box math that would otherwise be needed to check
flattened instances.

The five relationships checked (all derived directly from
Session.generate_resistor()'s drawing code, not from generic
thick-film-resistor convention):

  PAD_OVERLAP   -- each conductor pad must overlap the resistor ink
                   body along the long axis by at least ``overlap_a``.
  PAD_LENGTH    -- each pad's own length beyond that overlap must be
                   at least ``pad_b``.
  PAD_SPACE     -- each pad must extend past the ink body's width by
                   at least ``space_c`` on each side (pad is wider
                   than the ink to allow registration tolerance).
  GLAZE_COVER   -- the resistor glaze (overprint) must cover the ink
                   body by at least ``glaze_oversize`` on all 4 sides.
  KEEPOUT_COVER -- if a keepout layer is configured, the keepout
                   shape must cover the ink body's *un-extended*
                   footprint (the ink bbox shrunk by ``overlap_a`` on
                   each end along the long axis -- i.e. the resistive
                   element itself, not the ink body as drawn, which
                   already includes the overlap extension) by at
                   least ``keepout_oversize`` on all 4 sides.

Only simple (non-folded, n_folds<=1) resistors drawn by
Session.generate_resistor() are checked precisely by PAD_OVERLAP /
PAD_LENGTH / PAD_SPACE / KEEPOUT_COVER, which assume exactly two
conductor pads on the resistor cell forming a single long-axis pair.
A resistor cell found with a conductor-pad count other than 2 (e.g. a
folded/meander/top-hat resistor with more legs) is still checked for
GLAZE_COVER (which only depends on the ink bbox), but is skipped for
the pad-relationship checks and reported via ``skipped_cells`` on the
return value of check_all_resistors() rather than silently ignored.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .units import mm_to_dbu  # noqa: F401  (re-exported for convenience)

RESISTOR_CHECK_KINDS = ("PAD_OVERLAP", "PAD_LENGTH", "PAD_SPACE", "GLAZE_COVER", "KEEPOUT_COVER")


@dataclass
class ResistorViolation:
    kind: str
    cell_name: str
    required: float
    actual: float
    points: List[Tuple[float, float]]
    message: str = field(default="")

    def __post_init__(self):
        if self.kind not in RESISTOR_CHECK_KINDS:
            raise ValueError(f"unknown resistor DRC check kind: {self.kind!r}")
        if not self.message:
            self.message = (
                f"{self.kind}: required >= {self.required:.0f} DBU, "
                f"actual {self.actual:.0f} DBU (cell {self.cell_name!r})"
            )

    def bbox(self) -> Tuple[float, float, float, float]:
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return (min(xs), min(ys), max(xs), max(ys))


def _ink_layers(rules: dict) -> set:
    """All layers that can hold resistor ink: the default resistor_layer
    plus any per-sheet-resistance override in ink_layers."""
    layers = {int(rules["resistor_layer"])}
    for v in (rules.get("ink_layers") or {}).values():
        try:
            layers.add(int(v))
        except (TypeError, ValueError):
            continue
    return layers


def _marker(x0: float, y0: float, x1: float, y1: float) -> List[Tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _union_bbox(bboxes: List[Tuple[float, float, float, float]]) -> Optional[Tuple[float, float, float, float]]:
    if not bboxes:
        return None
    x0 = min(b[0] for b in bboxes)
    y0 = min(b[1] for b in bboxes)
    x1 = max(b[2] for b in bboxes)
    y1 = max(b[3] for b in bboxes)
    return (x0, y0, x1, y1)


def find_resistor_cells(library, rules: dict) -> List:
    """Return every cell in library that looks like a resistor cell:
    it has shapes of its own (not through Srefs) on both an ink layer
    and the conductor_layer."""
    ink_layers = _ink_layers(rules)
    conductor_layer = int(rules["conductor_layer"])
    out = []
    for cell in library.cells.values():
        has_ink = any(s.layer in ink_layers for s in cell.shapes)
        has_conductor = any(s.layer == conductor_layer for s in cell.shapes)
        if has_ink and has_conductor:
            out.append(cell)
    return out


def _pair_pads(pads: list):
    """Pair up exactly two conductor pads by pin label "1"/"2" when
    present, else by ascending x0 (bbox min-x). Returns (pad1, pad2) or
    None if there are not exactly two pads."""
    if len(pads) != 2:
        return None
    by_pin = {p.pin: p for p in pads if getattr(p, "pin", "") in ("1", "2")}
    if len(by_pin) == 2:
        return by_pin["1"], by_pin["2"]
    return tuple(sorted(pads, key=lambda p: p.bbox()[0]))


def check_resistor_cell(cell, rules: dict) -> List[ResistorViolation]:
    """Check a single resistor cell's own (non-instanced) shapes against
    rules. Returns a list of violations (empty if the cell passes every
    check that applies to it)."""
    violations: List[ResistorViolation] = []

    ink_layers = _ink_layers(rules)
    conductor_layer = int(rules["conductor_layer"])
    glaze_layer = int(rules["glaze_layer"])
    keepout_layer = rules.get("keepout_layer", -1)
    try:
        keepout_layer = int(keepout_layer)
    except (TypeError, ValueError):
        keepout_layer = -1

    a = float(rules["overlap_a"])
    b = float(rules["pad_b"])
    c = float(rules["space_c"])
    d = float(rules["glaze_oversize"])
    e = float(rules.get("keepout_oversize", 0) or 0)

    ink_shapes = [s for s in cell.shapes if s.layer in ink_layers]
    if not ink_shapes:
        return violations
    ink_bbox = _union_bbox([s.bbox() for s in ink_shapes])
    ix0, iy0, ix1, iy1 = ink_bbox

    # --- GLAZE_COVER: glaze must cover ink bbox by >= d on all sides ---
    glaze_shapes = [s for s in cell.shapes if s.layer == glaze_layer]
    if glaze_shapes:
        gx0, gy0, gx1, gy1 = _union_bbox([s.bbox() for s in glaze_shapes])
        margins = [ix0 - gx0, iy0 - gy0, gx1 - ix1, gy1 - iy1]
        actual = min(margins)
        if actual < d:
            violations.append(ResistorViolation(
                kind="GLAZE_COVER", cell_name=cell.name, required=d, actual=actual,
                points=_marker(gx0, gy0, gx1, gy1),
            ))

    # --- pad-relationship checks: require exactly 2 conductor pads ---
    pads = [s for s in cell.shapes if s.layer == conductor_layer]
    pair = _pair_pads(pads)
    if pair is None:
        return violations  # not a simple 2-pad resistor; caller records as skipped

    for pad in pair:
        px0, py0, px1, py1 = pad.bbox()
        # Determine which side of the ink body this pad sits on by
        # which edge it overlaps, so this works for either long-axis
        # orientation (generate_resistor always draws long-axis = X,
        # but be defensive rather than assuming).
        ink_w = ix1 - ix0
        ink_h = iy1 - iy0
        if ink_w >= ink_h:
            # long axis = X
            overlap = min(px1, ix1) - max(px0, ix0)
            pad_span = px1 - px0
            pad_len_beyond = pad_span - max(overlap, 0)
            space_lo = iy0 - py0
            space_hi = py1 - iy1
        else:
            # long axis = Y
            overlap = min(py1, iy1) - max(py0, iy0)
            pad_span = py1 - py0
            pad_len_beyond = pad_span - max(overlap, 0)
            space_lo = ix0 - px0
            space_hi = px1 - ix1

        if overlap < a:
            violations.append(ResistorViolation(
                kind="PAD_OVERLAP", cell_name=cell.name, required=a, actual=overlap,
                points=_marker(px0, py0, px1, py1),
            ))
        if pad_len_beyond < b:
            violations.append(ResistorViolation(
                kind="PAD_LENGTH", cell_name=cell.name, required=b, actual=pad_len_beyond,
                points=_marker(px0, py0, px1, py1),
            ))
        space_actual = min(space_lo, space_hi)
        if space_actual < c:
            violations.append(ResistorViolation(
                kind="PAD_SPACE", cell_name=cell.name, required=c, actual=space_actual,
                points=_marker(px0, py0, px1, py1),
            ))

    # --- KEEPOUT_COVER: keepout must cover the ink's un-extended
    # (by overlap_a) footprint by >= e on all sides, when configured ---
    if keepout_layer >= 0:
        keepout_shapes = [s for s in cell.shapes if s.layer == keepout_layer]
        if keepout_shapes:
            ink_w = ix1 - ix0
            ink_h = iy1 - iy0
            if ink_w >= ink_h:
                rx0, rx1 = ix0 + a, ix1 - a
                ry0, ry1 = iy0, iy1
            else:
                rx0, rx1 = ix0, ix1
                ry0, ry1 = iy0 + a, iy1 - a
            kx0, ky0, kx1, ky1 = _union_bbox([s.bbox() for s in keepout_shapes])
            margins = [rx0 - kx0, ry0 - ky0, kx1 - rx1, ky1 - ry1]
            actual = min(margins)
            if actual < e:
                violations.append(ResistorViolation(
                    kind="KEEPOUT_COVER", cell_name=cell.name, required=e, actual=actual,
                    points=_marker(kx0, ky0, kx1, ky1),
                ))

    return violations


@dataclass
class ResistorDrcResult:
    violations: List[ResistorViolation]
    checked_cells: List[str]
    skipped_cells: List[str]


def check_all_resistors(library, rules: dict, cell_names: Optional[List[str]] = None) -> ResistorDrcResult:
    """Run the resistor geometry DRC over every resistor cell in
    library (or just cell_names, if given). Returns a ResistorDrcResult
    with all violations found, the list of cells actually checked for
    the pad-relationship rules, and the list of resistor-like cells
    skipped for those rules (e.g. folded resistors with != 2 pads)."""
    conductor_layer = int(rules["conductor_layer"])
    candidates = find_resistor_cells(library, rules)
    if cell_names is not None:
        wanted = set(cell_names)
        candidates = [c for c in candidates if c.name in wanted]

    violations: List[ResistorViolation] = []
    checked: List[str] = []
    skipped: List[str] = []
    for cell in candidates:
        pads = [s for s in cell.shapes if s.layer == conductor_layer]
        if _pair_pads(pads) is None:
            skipped.append(cell.name)
        else:
            checked.append(cell.name)
        violations.extend(check_resistor_cell(cell, rules))

    return ResistorDrcResult(violations=violations, checked_cells=checked, skipped_cells=skipped)
