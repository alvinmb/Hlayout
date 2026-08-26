"""Thin-film resistor sizing and geometry: serpentine (meander) and
top-hat (dog-bone) styles, both laser-trimmable.

Unlike the thick-film ink-print resistor (hlayout.gui.resistor_designer /
Session.generate_resistor(), a plain rectangle sized by squares), a
thin-film resistor's *shape* is the resistive element itself -- the
sheet resistivity is deposited uniformly across the whole design (one
Rs value for everything, not a per-ink map), so hitting a target
resistance means shaping a path of the right number of squares, not
picking a bigger rectangle.

Both styles here follow a widely-used two-zone laser-trim convention
(the kind shown on thin-film resistor design-guide app notes: a coarse
"link area" of removable jumper legs, plus a fine "serpentine"/"bar"
area trimmed with parallel laser kerf cuts):

- Serpentine: a boustrophedon (back-and-forth) meander. A small number
  of wide "link" legs at one end can be cut out entirely for coarse
  resistance steps; the main meander is fine-trimmed afterward by
  cutting shallow parallel kerfs partway across it, lengthening the
  current path a little at a time.
- Top-hat (dog-bone): a narrow resistive bar between two wide contact
  pads. All the resistance lives in the bar (the pads are wide enough
  that their own contribution is negligible); the bar is fine-trimmed
  the same way as the serpentine's main area.

Modeling assumptions / limitations (see also hlayout.core.trim_sim's
docstring for the same spirit of caveat):

- The classic "number of squares" model is used throughout:
  resistance = sheet_rho_ohms_per_sq * (length / width) for a straight
  run. This ignores current crowding at contacts and corners except
  for the CORNER_SQUARE_FACTOR correction below -- it is a standard
  first-order design estimate, not a field-solved value (for that,
  see hlayout.core.trim_sim, which *does* solve the resistor network
  once actual cuts are being simulated).
- CORNER_SQUARE_FACTOR approximates a plain (unmitered) 90-degree
  meander turn as contributing this many squares instead of a full
  square, since current crowds toward the inside corner. 0.5 is this
  module's default; override it per design if your process has
  measured a different value (see hlayout.io.techfile's thin-film
  rules -- "corner_factor").

Laser trim design rules (see Figure 4 "Serpentine Resistor With Two
Areas For Laser Trim" and Figure 5 "Probe Pad And Artwork Overlap
Requirements" in the design guides this module follows) are enforced
in two places:

- generate_top_hat_geometry()'s ``overlap`` parameter and
  Session.generate_serpentine_resistor()'s probe pad placement
  (hlayout/scripting/api.py) make the pad geometry actually *overlap*
  the resistive element by the design's "min_artwork_overlap" rule,
  rather than merely abutting it (Figure 5's "Artwork Overlap, .002in"
  callout exists specifically because two mask layers that only touch,
  with zero overlap margin, can lose contact entirely under normal
  layer-to-layer alignment tolerance).
- check_thin_film_rules() validates a solved candidate against the
  rest of Figure 5's minimums (resistor length, probe pad size) and
  Figure 4's kerf-vs-spacing requirement (a laser trim cut needs at
  least one kerf's width of clearance between adjacent legs, or the
  cut risks severing the neighboring leg instead of just lengthening
  the current path through the one being trimmed) -- see its own
  docstring for exactly what's checked. It's advisory, not enforced by
  solve_serpentine()/solve_top_hat() themselves (a design might
  deliberately push past a guideline for a one-off part), but Tools >
  Thin-Film Resistor Designer... surfaces any violation it finds.

What this module does *not* model: the actual resistance-vs-cut-depth
curve of trimming into the serpentine/bar area (how far a kerf can
plunge before hitting the "compensated for laser kerf" limit implied
by Figure 4's grid of pre-sized squares) -- that's a resistor-network
simulation, and hlayout.core.trim_sim already does that once a
placement exists (wired up as Tools > Thin-Film Resistor Designer...'s
"Trim Simulator..." button). This module's job stops at producing a
DRC-clean *nominal*, untrimmed shape for that simulator to start from.
"""

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .errors import GeometryError

Point = Tuple[float, float]

#: Default approximation for an unmitered 90-degree meander corner's
#: contribution to squares count -- see module docstring. Overridable
#: per design (Tools > Thin-Film Resistor Designer..., persisted via
#: hlayout.io.techfile.load_thin_film_rules()'s "corner_factor").
CORNER_SQUARE_FACTOR = 0.5

#: Fraction of a serpentine's total squares reserved for the coarse
#: link area by default (the rest goes to the fine-trim serpentine
#: area) -- see SerpentineSolution/solve_serpentine().
DEFAULT_LINK_FRACTION = 0.15


def _round_pt(p) -> Point:
    return (int(round(p[0])), int(round(p[1])))


# ─────────────────────────────────────────────────────────────────────────
# Serpentine (meander)
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class SerpentineSolution:
    """One candidate serpentine sizing for a target resistance.

    All lengths in DBU (1 DBU = 1 micron). ``squares`` is the total
    (link + serpentine) squares count the candidate actually hits;
    ``resistance_ohm`` is that squares count times the design's sheet
    rho, i.e. what the resistor will measure fresh off the process,
    before any trim.
    """

    trace_width: float
    num_legs: int
    leg_length: float          # each serpentine-area leg, end to end
    serpentine_squares: float  # fine-trim area's own squares
    num_link_legs: int
    link_leg_length: float     # each link-area leg (coarse trim, cut whole)
    link_squares: float
    spacing: float             # gap between adjacent leg centerlines' edges
    footprint_w: float
    footprint_l: float
    squares: float
    resistance_ohm: float
    recommended: bool = False

    @property
    def aspect_ratio(self) -> float:
        return self.footprint_w / self.footprint_l if self.footprint_l else 0.0


def _corner_extension(num_legs, trace_width):
    """How far a meander's corner connectors push the Y extent beyond
    the legs' own [-leg_length/2, leg_length/2] span, on the top and
    bottom respectively -- corners alternate top/bottom (see
    _meander_polys), so 0 corners extends neither side, 1 corner (the
    num_legs == 2 case) extends only the top (that corner is always
    the i=1 "connect_top" one), and >=2 corners extend both."""
    corners = max(num_legs - 1, 0)
    if corners == 0:
        return 0, 0
    if corners == 1:
        return trace_width, 0
    return trace_width, trace_width


def _serpentine_footprint(num_legs, leg_length, trace_width, spacing, end_margin):
    w = num_legs * trace_width + max(num_legs - 1, 0) * spacing
    top_ext, bot_ext = _corner_extension(num_legs, trace_width)
    # The actual drawn (conductive) extent, plus end_margin as clear
    # placement padding on each end beyond that -- not part of the
    # conductor itself, just reserved footprint so this zone doesn't
    # visually/physically crowd whatever sits next to it.
    l = (leg_length + top_ext + bot_ext) + 2 * end_margin
    return w, l


def solve_serpentine(
    target_r_ohm: float,
    sheet_rho_ohm_per_sq: float,
    trace_width: float,
    spacing: Optional[float] = None,
    link_fraction: float = DEFAULT_LINK_FRACTION,
    corner_factor: float = CORNER_SQUARE_FACTOR,
    max_legs: int = 30,
    end_margin: Optional[float] = None,
    num_candidates: int = 5,
) -> List[SerpentineSolution]:
    """Return up to ``num_candidates`` SerpentineSolutions for
    ``target_r_ohm``, sorted best-first (closest-to-square footprint,
    among candidates with a reasonable per-leg squares count).

    ``spacing`` (gap between leg edges) and ``end_margin`` (extra run
    at each end of a leg beyond the last corner, so the meander
    doesn't visually touch its own bounding box) both default to
    ``trace_width`` if not given -- the classic meander proportion
    where legs, gaps, and margins are all one trace-width.

    Raises GeometryError if target_r_ohm/sheet_rho_ohm_per_sq/
    trace_width aren't positive, or if no candidate in 2..max_legs
    legs can hit the target with at least 1 square per leg (e.g. the
    target resistance is too small for this sheet rho/trace width
    combination -- widen the trace or pick a lower-rho sheet).
    """
    if target_r_ohm <= 0:
        raise GeometryError("target resistance must be positive")
    if sheet_rho_ohm_per_sq <= 0:
        raise GeometryError("sheet resistivity must be positive")
    if trace_width <= 0:
        raise GeometryError("trace width must be positive")
    spacing = trace_width if spacing is None else spacing
    end_margin = trace_width if end_margin is None else end_margin

    total_squares = target_r_ohm / sheet_rho_ohm_per_sq
    link_squares_budget = total_squares * link_fraction
    serpentine_target = total_squares - link_squares_budget

    candidates = []
    for num_legs in range(2, max_legs + 1):
        corners = num_legs - 1
        leg_squares = (serpentine_target - corners * corner_factor) / num_legs
        if leg_squares < 1.0:
            continue  # legs would be shorter than they are wide -- not a meander
        leg_length = leg_squares * trace_width

        # Coarse link legs: a handful of short, wide (1-square) jumper
        # legs at one end, each individually cuttable, summing to
        # roughly link_squares_budget. At least 2 so there's something
        # to actually call a "link area"; capped at 8 so it doesn't
        # dominate the part for a small link_fraction.
        num_link_legs = max(2, min(8, round(link_squares_budget)))
        link_leg_length = trace_width * max(link_squares_budget / num_link_legs, 1.0)
        link_corners = max(num_link_legs - 1, 0)
        link_squares = (
            num_link_legs * (link_leg_length / trace_width) + link_corners * corner_factor
        )

        squares = (
            num_legs * leg_squares + corners * corner_factor + link_squares
        )
        w, l = _serpentine_footprint(num_legs, leg_length, trace_width, spacing, end_margin)
        link_w, link_l = _serpentine_footprint(num_link_legs, link_leg_length, trace_width, spacing, end_margin)
        # + spacing: generate_serpentine_geometry() leaves exactly one
        # spacing-wide gap between the link zone and the serpentine
        # zone for the bridge connector -- match that here so this
        # estimate is what auto-placement actually needs, not an
        # approximation of it.
        footprint_w = link_w + spacing + w
        footprint_l = max(l, link_l)

        sol = SerpentineSolution(
            trace_width=trace_width,
            num_legs=num_legs,
            leg_length=leg_length,
            serpentine_squares=num_legs * leg_squares + corners * corner_factor,
            num_link_legs=num_link_legs,
            link_leg_length=link_leg_length,
            link_squares=link_squares,
            spacing=spacing,
            footprint_w=footprint_w,
            footprint_l=footprint_l,
            squares=squares,
            resistance_ohm=squares * sheet_rho_ohm_per_sq,
        )
        candidates.append(sol)

    if not candidates:
        raise GeometryError(
            f"can't fit a serpentine for {target_r_ohm:g} Ω at "
            f"{sheet_rho_ohm_per_sq:g} Ω/sq with a {trace_width:g} DBU trace "
            "-- try a narrower trace or a higher-rho sheet"
        )

    # Prefer a close-to-square footprint (aspect ratio near 1); among
    # near-ties, prefer fewer legs (simpler, more robust part).
    def score(c: SerpentineSolution):
        ar = c.aspect_ratio
        ar_penalty = abs(math.log(ar)) if ar > 0 else float("inf")
        return (ar_penalty, c.num_legs)

    candidates.sort(key=score)
    best = candidates[: max(num_candidates, 1)]
    best[0].recommended = True
    return best


def generate_serpentine_geometry(sol: SerpentineSolution) -> dict:
    """Build the serpentine's polygon outlines from a solved
    SerpentineSolution, centered on the origin (0, 0) with the link
    area to the left and the serpentine (fine-trim) area to the right,
    legs running vertically -- matching the classic link-area /
    serpentine-area convention this module's docstring describes.

    Returns {"link_polys": [...], "serpentine_polys": [...],
    "outline_points": [...]} -- each "*_polys" entry is a list of
    point-lists (one rectangle per leg plus one per corner-connector,
    all axis-aligned so they tile into one continuous conductive
    shape), and "outline_points" is the combined bounding footprint
    (for a preview or a single merged silhouette) as one point list.
    All points are (x, y) DBU pairs, rounded to integers.
    """
    def _meander_polys(num_legs, leg_length, trace_width, spacing, end_margin, x0):
        """One rectangle per leg (running in Y) plus one per corner
        connector (running in X), left edge starting at x0. Returns
        (polys, next_x) where next_x is where a following zone should
        start (x0 + this zone's own footprint width)."""
        polys = []
        pitch = trace_width + spacing
        top_y = leg_length / 2 + end_margin
        bot_y = -leg_length / 2 - end_margin
        for i in range(num_legs):
            lx = x0 + i * pitch
            # Odd legs are the "up" run, even legs the "down" run, so
            # consecutive legs' corner connectors alternate top/bottom.
            polys.append([
                (lx, -leg_length / 2), (lx + trace_width, -leg_length / 2),
                (lx + trace_width, leg_length / 2), (lx, leg_length / 2),
            ])
            if i > 0:
                connect_top = (i % 2 == 1)
                cy = top_y - end_margin if connect_top else bot_y + end_margin
                y0, y1 = (cy, cy + trace_width) if connect_top else (cy - trace_width, cy)
                prev_lx = x0 + (i - 1) * pitch
                polys.append([
                    (prev_lx, y0), (lx + trace_width, y0),
                    (lx + trace_width, y1), (prev_lx, y1),
                ])
        width = num_legs * trace_width + max(num_legs - 1, 0) * spacing
        return polys, x0 + width

    end_margin = sol.spacing  # same one-trace-width convention as solve_serpentine
    link_polys, next_x = _meander_polys(
        sol.num_link_legs, sol.link_leg_length, sol.trace_width, sol.spacing, end_margin, x0=0.0
    )
    # A short horizontal jumper bridges the link area's last leg into
    # the serpentine area's first leg so the whole thing is one
    # connected conductor.
    bridge_y = -sol.link_leg_length / 2 - end_margin / 2
    bridge = [
        (next_x - sol.trace_width, bridge_y - sol.trace_width / 2),
        (next_x + sol.spacing + sol.trace_width, bridge_y - sol.trace_width / 2),
        (next_x + sol.spacing + sol.trace_width, bridge_y + sol.trace_width / 2),
        (next_x - sol.trace_width, bridge_y + sol.trace_width / 2),
    ]
    serp_polys, end_x = _meander_polys(
        sol.num_legs, sol.leg_length, sol.trace_width, sol.spacing, end_margin,
        x0=next_x + sol.spacing,
    )

    all_polys = link_polys + [bridge] + serp_polys
    xs = [p[0] for poly in all_polys for p in poly]
    ys = [p[1] for poly in all_polys for p in poly]
    cx = (min(xs) + max(xs)) / 2
    cy = (min(ys) + max(ys)) / 2

    def _center(poly):
        return [_round_pt((x - cx, y - cy)) for x, y in poly]

    outline = [
        (min(xs) - cx, min(ys) - cy), (max(xs) - cx, min(ys) - cy),
        (max(xs) - cx, max(ys) - cy), (min(xs) - cx, max(ys) - cy),
    ]
    return {
        "link_polys": [_center(p) for p in link_polys],
        "bridge_poly": _center(bridge),
        "serpentine_polys": [_center(p) for p in serp_polys],
        "outline_points": [_round_pt(p) for p in outline],
    }


# ─────────────────────────────────────────────────────────────────────────
# Top-hat (dog-bone)
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class TopHatSolution:
    """A dog-bone/top-hat resistor: a narrow resistive bar between two
    wide contact pads. All lengths in DBU. The pads' own resistance
    contribution is treated as negligible (standard assumption when
    pad_width >> bar_width), so ``resistance_ohm`` comes entirely from
    the bar's squares count."""

    bar_width: float
    bar_length: float
    pad_width: float
    pad_length: float
    squares: float
    resistance_ohm: float
    footprint_w: float
    footprint_l: float


def solve_top_hat(
    target_r_ohm: float,
    sheet_rho_ohm_per_sq: float,
    bar_width: float,
    pad_width: Optional[float] = None,
    pad_length: Optional[float] = None,
    min_bar_length: float = 0.0,
) -> TopHatSolution:
    """Size a top-hat/dog-bone resistor's bar to hit target_r_ohm.

    ``pad_width`` defaults to 4x bar_width (wide enough that the
    negligible-pad-resistance assumption holds comfortably) and
    ``pad_length`` to 2x bar_width if not given. Raises GeometryError
    if the solved bar_length would be shorter than ``min_bar_length``
    (e.g. a fab's minimum trimmable resistor length -- see Figure 5's
    "Resistor Length 0.002in Min." in thin-film trim design guides) or
    if any input isn't positive.
    """
    if target_r_ohm <= 0:
        raise GeometryError("target resistance must be positive")
    if sheet_rho_ohm_per_sq <= 0:
        raise GeometryError("sheet resistivity must be positive")
    if bar_width <= 0:
        raise GeometryError("bar width must be positive")
    pad_width = bar_width * 4 if pad_width is None else pad_width
    pad_length = bar_width * 2 if pad_length is None else pad_length

    squares = target_r_ohm / sheet_rho_ohm_per_sq
    bar_length = squares * bar_width
    if bar_length < min_bar_length:
        raise GeometryError(
            f"solved bar length {bar_length:g} DBU is below the minimum "
            f"trimmable length {min_bar_length:g} DBU -- widen the bar or "
            "raise the sheet rho"
        )
    footprint_w = pad_width
    footprint_l = bar_length + 2 * pad_length
    return TopHatSolution(
        bar_width=bar_width, bar_length=bar_length,
        pad_width=pad_width, pad_length=pad_length,
        squares=squares, resistance_ohm=squares * sheet_rho_ohm_per_sq,
        footprint_w=footprint_w, footprint_l=footprint_l,
    )


def check_thin_film_rules(sol, rules: dict) -> List[str]:
    """Validate a solved SerpentineSolution or TopHatSolution against a
    design's thin-film rules (hlayout.io.techfile.load_thin_film_rules()
    -- "min_resistor_length", "kerf", "probe_pad_min") and return a list
    of human-readable violation descriptions (empty if clean). Advisory
    only -- solve_serpentine()/solve_top_hat() don't call this
    themselves, so a design can knowingly push past a guideline; Tools
    > Thin-Film Resistor Designer... calls it after solving to warn
    about the ones it finds.

    Checks, all traceable to Figure 5 ("Probe Pad And Artwork Overlap
    Requirements") and Figure 4 ("Serpentine Resistor With Two Areas
    For Laser Trim") in the design guides this module follows:

    - Each trimmable run (a TopHatSolution's bar, or a
      SerpentineSolution's fine-trim leg) must be at least
      rules["min_resistor_length"] long (Figure 5's "Resistor Length
      0.002in Min.") -- a run shorter than this can't be laser-trimmed
      with any useful precision.
    - A SerpentineSolution's leg-to-leg spacing must be at least
      rules["kerf"] (Figure 4's kerf lines run between legs to trim
      one at a time) -- narrower than that and a trim cut aimed at one
      leg risks clipping its neighbor instead of just lengthening the
      current path through the one being trimmed.
    - A TopHatSolution's bar (or a SerpentineSolution's trace) must be
      at least 2x rules["kerf"] wide -- narrower than that, a partial
      "plunge" trim cut has no controllable range: the very first cut
      deep enough to matter already reaches most of the way across.
    """
    violations = []
    min_len = rules.get("min_resistor_length", 0)
    kerf = rules.get("kerf", 0)

    if isinstance(sol, SerpentineSolution):
        if sol.leg_length < min_len:
            violations.append(
                f"fine-trim leg length ({sol.leg_length:g} DBU) is below the "
                f"minimum trimmable resistor length ({min_len:g} DBU)"
            )
        if sol.spacing < kerf:
            violations.append(
                f"leg spacing ({sol.spacing:g} DBU) is narrower than the laser "
                f"kerf ({kerf:g} DBU) -- a trim cut could sever the neighboring leg"
            )
        if sol.trace_width < 2 * kerf:
            violations.append(
                f"trace width ({sol.trace_width:g} DBU) is less than 2x the laser "
                f"kerf ({kerf:g} DBU) -- too narrow for a controlled partial trim cut"
            )
    elif isinstance(sol, TopHatSolution):
        if sol.bar_length < min_len:
            violations.append(
                f"bar length ({sol.bar_length:g} DBU) is below the minimum "
                f"trimmable resistor length ({min_len:g} DBU)"
            )
        if sol.bar_width < 2 * kerf:
            violations.append(
                f"bar width ({sol.bar_width:g} DBU) is less than 2x the laser "
                f"kerf ({kerf:g} DBU) -- too narrow for a controlled partial trim cut"
            )
    return violations


def generate_top_hat_geometry(sol: TopHatSolution, overlap: float = 0.0) -> dict:
    """Build the top-hat's polygon outline, centered on the origin
    with the bar running horizontally. Returns {"pad_a_points": [...],
    "bar_points": [...], "pad_b_points": [...]}, each a single point
    list (rectangles), all (x, y) DBU pairs rounded to integers.

    ``overlap`` (DBU) extends each pad *into* the bar by that much
    instead of just abutting it at the bar's own endpoint -- see this
    module's "Laser trim design rules" docstring section for why a
    zero-overlap abutment isn't enough (Figure 5's "Artwork Overlap"
    minimum). Pass hlayout.io.techfile's thin-film rules'
    "min_artwork_overlap" here (Session.generate_top_hat_resistor()
    does exactly that); defaults to 0 (the old abutting behaviour) so
    a direct call without a rules dict still gets *some* valid shape,
    just without the overlap margin.
    """
    half_bar_l = sol.bar_length / 2
    half_bar_w = sol.bar_width / 2
    half_pad_w = sol.pad_width / 2
    overlap = min(overlap, sol.bar_length / 2)  # never overlap past the bar's own middle
    pad_a_x0 = -half_bar_l - sol.pad_length
    pad_b_x1 = half_bar_l + sol.pad_length

    def rect(x0, y0, x1, y1):
        return [_round_pt(p) for p in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]

    return {
        "pad_a_points": rect(pad_a_x0, -half_pad_w, -half_bar_l + overlap, half_pad_w),
        "bar_points": rect(-half_bar_l, -half_bar_w, half_bar_l, half_bar_w),
        "pad_b_points": rect(half_bar_l - overlap, -half_pad_w, pad_b_x1, half_pad_w),
    }
