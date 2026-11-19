# Routability-driven Auto Place (spec)

Status: spec, 2026-11-15. Nothing here is implemented yet.
Owner module: `hlayout/core/autoplace.py` (Placer), `hlayout/scripting/autoplace_session.py`,
`hlayout/scripting/route_improve.py` (experimental, router-in-the-loop refine).

## 1. Why

PRE+AMP bench, 2026-11-15 (quick = 15-round PathFinder, rescue on, no nudge):

| placement            | routed | crossovers | SLI crossings | "too close" cells after round 60 |
|----------------------|--------|------------|---------------|----------------------------------|
| hand                 | 51-52/56 | 9        | 79            | 44 (full run)                     |
| Auto Place, 3 seeds  | 27-37/56 | 1-7      | 65-88         | 4 755-5 854                       |
| + congestion 2.0     | 38-41/56 | 4-5      | 54-87         | 3 163                             |
| route_improve (hand) | 54/56  | 7          | -             | -                                 |

Auto Place minimises straight-line length (MST) and, optionally, SLI crossings.
Neither predicts routing. Fewer crossings did not help: the auto placements
fail because parts are packed so tightly that the gaps between them hold
fewer tracks than the nets need (PathFinder ends with ~20 nets fighting over
thousands of cells; the hand placement ends with 4 over 44). The spacing fix
(fc721f3) made the problem visible: with legal gaps most seeds no longer fit
the substrate.

Standard-cell and FPGA placers solved this decades ago for channel-routed
fabrics. This spec maps their four techniques onto a chip-and-wire hybrid.

## 2. What the established placers do

1. **Cost per net divided by free routing capacity** (VPR, FPGA).
   `cost_n = q(n) * (bb_x / C_av,x + bb_y / C_av,y)`; C_av = average channel
   capacity (tracks) over the net's bounding box; q(n) corrects HPWL for
   nets with more than 3 pins (1.0 up to 3 pins, 2.79 at 50).
2. **Channels sized to the tracks they must carry** (TimberWolf, standard cell).
   Rows are spaced by estimated channel density; feed-throughs are inserted
   where nets must cross a row. Annealing in stages with a shrinking move
   window (range limiter).
3. **Fast congestion map + cell inflation** (Ripple, NTUplace4h, SimPLR -
   ISPD 2011/12 winners). RUDY spreads each net's wire over its bounding box;
   cells in overflowing bins are inflated (history-based: inflation only
   accumulates until global placement ends) and the placer re-spreads them.
4. **Router in the loop** (CRISP, SRP, Ropt). The real router's congestion
   replaces the estimate for final refinement.

## 3. Mapping onto a hybrid substrate

| standard cell / FPGA   | HLayout                                                          |
|------------------------|------------------------------------------------------------------|
| channel / routing bin  | gcell on a coarse grid (existing `cong_cell`, default 2.5 x pitch) |
| track                  | `pitch` = track width + conductor spacing (from Autoroute rules)  |
| channel capacity       | free width across a gcell edge / pitch, H and V separately        |
| blockage               | part footprints (pads + body), die + bond-finger halo, resistor ink keep-out (RESKEEPOUT), fixed copper |
| feed-through           | crossover (COND2 + dielectric) or track under a chip part (when "Allow tracks under chip parts" is on) |
| row spacing            | gap between neighbouring parts (horizontal / vertical constraint graph) |

Differences that matter:

* One routing layer (COND1). A crossing costs a crossover, so demand in a
  gcell is not relieved by a second layer. Crossovers need room
  (dielectric = crossover width + 2 x 0.3 mm, clear of resistor ink): keep the
  existing `xo_room` idea, but as capacity, not as a separate cost.
* Under-chip tracks add capacity inside chip-part footprints (between
  terminals, spacing to terminals respected).
* Parts are few (30-60) and large relative to the grid, so the per-move cost
  can afford an L-shape route estimate per MST edge instead of RUDY boxes.

## 4. Design

### 4.1 Capacity map (built once per run, updated for moved parts)

Grid: gcell size `G = cong_cell` (default 2.5 x pitch, ~1 mm at 400 um pitch).
For every gcell edge, H and V:

    cap_H(e) = free length of the vertical cut line through e / pitch
    cap_V(e) = free length of the horizontal cut line through e / pitch

"Free" = not inside a part footprint, die halo, resistor ink keep-out or fixed
copper, and inside the region. Chip parts with under-chip routing allowed
contribute their between-terminal width minus 2 x spacing. Integer floor:
a 1.5-track gap is 1 track.

Incremental: a move changes capacity only in gcells under the old and new
footprints (store per-gcell blocked length per part; subtract / add).

### 4.2 Demand map (probabilistic L-routes on MST edges)

Nets are already MST edges (= the SLIs the user sees). For each edge a-b:

* Straight (same gcell row or column): demand 1 track on every gcell edge
  along it.
* Otherwise two L-routes, each weight 0.5 (Lou/Sapatnekar-style
  probabilistic routing), adding 0.5 track per gcell edge on each L.

Multi-pin correction: multiply a net's edge demand by `q(n)` (VPR
`cross_count` table) only when the net is estimated by bounding box; with
MST edges q(n) = 1 (the MST already accounts for the Steiner shape within
~10 %).

Pins inside a footprint: the escape from the pad to the footprint edge is
free (not counted) - the part's own pads are not a blockage for its nets.

### 4.3 Cost (replaces the `cong * overflow / pitch` term)

    C = sum_edges w_n * len_e * (1 + alpha * mean_{gcells on e} util)   VPR-style
      + beta  * sum_{gcell edges} max(0, demand - cap)^2 * G              overflow
      + cross_weight * crossings                                          as now
      + overlap / outside / thermal terms                                as now

    util = demand / max(cap, 0.5)

* The first term is VPR's "length divided by capacity" in a form that stays
  finite where cap = 0: a wire through crowded gcells costs more than the same
  length through empty ones.
* Squared overflow (not linear) so two half-full channels beat one full and one
  empty - this is what spreads tracks.
* Defaults to start the sweep: alpha = 1, beta = 4 (um of wire per
  track-overflow^2 per um of gcell). Calibrate in step 0.

### 4.4 Inflation loop (Ripple style)

After the explore anneal:

    for it in 1..K (K = 4):
        build demand / capacity maps
        for each movable part p:
            ov_x = max over gcell edges touching p's left/right sides of overflow_H
            ov_y = max over gcell edges touching p's top/bottom sides of overflow_V
            pad_x[p] += ceil(ov_x / 2) * pitch       (half the shortfall per side)
            pad_y[p] += ceil(ov_y / 2) * pitch
            pad_x[p] = min(pad_x[p], PAD_MAX)        (PAD_MAX = 4 x pitch)
        if sum(inflated areas) > TARGET_DENSITY x free region area:
            scale every pad down proportionally; record "board too small"
        refine anneal (hot = 0.05) with footprints = body + pad
    keep pads through legalisation; drop them only when writing the result

* History: pads only grow (accumulate) until the loop ends - stops parts
  oscillating between congested spots.
* TARGET_DENSITY default 0.85. If the parts plus the needed channels cannot
  fit, Auto Place says so (the existing red NOT LEGAL banner plus
  "needs ~N mm^2 more routing room") instead of producing a placement that
  cannot route.

### 4.5 Channel sizing (TimberWolf style), after legalisation

1. Build horizontal and vertical constraint graphs: an edge i -> j when part j
   is right of (above) part i and their projections overlap.
2. For each edge, channel density d_ij = number of demand tracks crossing the
   gap between them (from the demand map, rounded up).
3. Required gap g_ij = max(copper spacing, d_ij x pitch + spacing).
4. Longest-path compaction (symbolic-layout style) along x then y with these
   minimum gaps; fixed / locked parts are anchors.
5. If the result leaves the region: report the shortfall, keep the
   legalised placement.

This is the deterministic guarantee the annealing does not give: every gap
holds the tracks the estimate says go through it.

### 4.6 Router-in-the-loop refine (`route_improve.py`)

Last step, optional (setting "Refine with router", default on for boards
under ~80 connections). Uses quick PathFinder (8-15 rounds). Moves:
nudge plan, shift by 1-4 grid steps, flip/rotate; accept only if routed
count rises (ties: fewer crossovers, then shorter length). Also feed back
PathFinder's per-cell history cost into the pads of 4.4 (SRP / Ropt idea):
cells PathFinder fought over add to pad_x/pad_y of the parts beside them,
then one more refine anneal + 4.5.

## 5. Step 0 - calibrate before building (cheap, decides the weights)

The congestion term in fc721f3 gave +3 on one seed; before adding more
machinery, check the estimate predicts the router:

1. For every routed bench layout already on disk (`_bench/q_*_s*.gds`,
   `imp_*.gds`, hand placement) compute the demand / capacity maps of 4.1-4.2.
2. Compare with PathFinder's result on the same layout: unrouted count and the
   round-60 overuse cells.
3. Report the rank correlation (Spearman) of total overflow vs unrouted, and a
   per-gcell overlay (overflow map vs PathFinder overuse map) as PNG.

Go / no-go: rank correlation >= 0.7 across the ~15 layouts. If lower, fix the
estimate (capacity rules, under-chip, die halos) before tuning the placer.

## 6. Implementation plan

| step | file(s)                                   | content                                    |
|------|-------------------------------------------|--------------------------------------------|
| 0    | `_bench/calibrate_cong.py`                | step 0 above                               |
| 1    | `autoplace.py`: `CapMap`, `DemandMap`     | 4.1, 4.2, incremental update, unit tests   |
| 2    | `autoplace.py`: `Placer.cost`, `_try`     | 4.3; old `cong` term removed (setting kept as alias for beta) |
| 3    | `autoplace.py`: `Placer.inflate_loop`     | 4.4, in `run()` between explore and refine |
| 4    | `autoplace.py`: `Placer.size_channels`    | 4.5, after `legalise()`, before `orient()` |
| 5    | `route_improve.py` + `autoplace_session`  | 4.6, history feedback                       |
| 6    | `gui/autoplace_window.py`, commands, manual ch. | settings: Routing room (alpha/beta), Inflate passes, Target density, Size channels, Refine with router |

Settings exposed (Auto Place window, "Routing" group): Track pitch (from
Autoroute rules, read-only), Routing room weight, Inflate passes (0 = off),
Target density, Size channels (on/off), Refine with router (on/off, passes).

Tests: capacity of a known gap; L-route demand on a 2-part board; inflation
grows pads only where overflow is; channel sizing opens a 1-track gap to 3
tracks for 3 crossing nets; refine never lowers the routed count.

## 7. Acceptance

Benches (quick PathFinder, seeds 1-3, no hand edits):

| board                         | target                                  |
|-------------------------------|-----------------------------------------|
| PRE+AMP (33 parts, 56 conns)  | >= hand placement (52/56) on 2 of 3 seeds, 0 shorts, legal |
| op_amp (18 Q, 1 C, 11 R), SOT-23 | 100 % or within 1 of the best of 3 hand tries |
| op_amp, bare-die transistors  | 100 % or within 1 of the best of 3 hand tries |
| ALU8 (die + fingers)          | no worse than today's Auto Place         |

Runtime: Auto Place + inflate + sizing <= 2x today's (~7 min on PRE+AMP);
router refine is extra and stoppable.

## 8. Open questions

* Transistors on op_amp can be SOT-23 or bare die, so the blockage model
  handles both per part: SOT-23 = pads + body (tracks under the body
  between pads when "Allow tracks under chip parts" is on); bare die = die +
  bond-finger halo (finger ring + max bond length), no under-die routing.
  Bench op_amp in both variants (all SOT-23, all die).
* Crossover room as capacity (4.3) or keep `xo_room` as a separate cost?
  Proposal: capacity - a gcell within RES_CLEAR of resistor ink gets no
  crossover capacity.
* Should channel sizing (4.5) be allowed to move locked parts' neighbours
  only, or also grow the region when the substrate size is not fixed?

## References

* V. Betz, J. Rose, "VPR: A New Packing, Placement and Routing Tool for FPGA
  Research", FPL 1997 (linear congestion cost, q(n)).
* C. Sechen, A. Sangiovanni-Vincentelli, "TimberWolf3.2: A New Standard Cell
  Placement and Global Routing Package", DAC 1986.
* X. He et al., "Ripple: An Effective Routability-Driven Placer by Iterative
  Cell Movement", ICCAD 2011 (RUDY extension, cell inflation).
* P. Spindler, F. Johannes, "Fast and Accurate Routing Demand Estimation for
  Efficient Routability-driven Placement" (RUDY), DATE 2007.
* Z. Qi et al., CRISP, ICCAD 2009 (global router invoked within placement).
* J. Lou, S. Thakur, S. Krishnamoorthy, H. Sheng, "Estimating routing
  congestion using probabilistic analysis", IEEE TCAD 2002 (L-shape model).
