# Gridless Shape Router for the HLayout Hybrid — Specification & Implementation Plan

Status: draft for review · Target package: `app.freerouting.autoroute.hybrid.shape`
Supersedes (eventually): the grid A\* engine in `HybridRouter` (`autoroute.hybrid`)
Audience: whoever implements the next router phase

---

## 1. Purpose

The current chip‑and‑wire router (`HybridRouter`) routes on a uniform integer grid with
octile A\*. It works and it is DRC‑clean, but the grid is the source of its two real
limits:

- **Clearance inflation.** Every obstacle is rasterised and grown by a conservative
  half‑cell before the distance transform, so effective spacing runs ~20–30 % above the
  rule. On a tight thick‑film substrate that is lost capacity: nets that *fit* geometrically
  are reported unrouted.
- **Quantised features.** Trace corners, crossover windows and jumper lands land on grid
  cells. A finer grid recovers accuracy but multiplies `cells × layers × 9` search states
  and memory; on the stress designs a 50 µm grid already costs seconds.

A **gridless shape router** works directly on polygon geometry: traces are poly‑lines with
width, clearance is exact, and features go exactly where they fit. This spec defines that
router and — the point of this document — pins down **how every hybrid‑specific rule is
enforced without a grid**, because a generic PCB shape router does not know about wire
bonds, dielectric crossovers, bond fingers, die attach keep‑outs or printed resistors.

It reuses the existing model (`HybridDesign`, `HybridRules`, `HybridRoutingOptions`,
`RoutedHybrid`) and the existing acceptance oracle (`HybridRouteDrc`). HLayout's export /
import, the CLI and the GUI do not change.

---

## 2. What stays the same

The gridless engine is a drop‑in alternative behind the same seams, so none of this moves:

| Kept as‑is | Where |
| --- | --- |
| Input / output model | `board.hybrid.*` — `HybridDesign`, `RoutedHybrid`, `RoutedTrace`, `CrossoverWindow`, `SubstratePad`, `BondWire`, `UnroutedConnection`, `RouteStats` |
| Rules | `rules.hybrid.*` — `ConductorRules`, `BondWireRules`, `JumperRules`, `PadRules` |
| Options | `HybridRoutingOptions` — `netWidths`, `directions`, `againstDirectionCost`, `fixedTraces`/`fixedJoins`/`connected`, `keepouts`, `netWindows`, `crossoverCost`, `jumperCost`, `maxJumpers`, `jumpersAllowed`, `ripupPasses`, `orthogonal`, `routingLayers` |
| Pre‑route checks | `drc.hybrid.HybridDrc` |
| Acceptance oracle | `drc.hybrid.HybridRouteDrc` (every `*` code below) |
| Bond assignment / fan‑out of unbonded die pads | `BondAssigner` (reused unchanged for v1) |
| CLI, JSON, schema | `cli.hybrid.HybridRouterCli`, `io.hybrid.HybridJson`, `docs/hybrid/hybrid.schema.json` |
| HLayout side | `hlayout/scripting/hybridroute.py` export/import — unchanged |

The router is selected by a new option field (`engine: "grid" | "shape"`, default `grid`
until the shape engine passes the full suite) so both can ship side by side and be compared
on the same design.

---

## 3. Goals / non‑goals

**Goals**
1. Produce a `RoutedHybrid` that `HybridRouteDrc.check()` returns **empty** for, on every
   design the grid router already clears, plus a measurable fraction it does not.
2. Honour **exact** clearances — no grid inflation.
3. Enforce all hybrid rules of §6 as hard constraints (never produced), not post‑filters.
4. Same or better completion on the 40‑design stress set; comparable wall‑time (≤ ~2× the
   grid router at its default grid is acceptable for the accuracy gain).
5. Deterministic for a given seed (reproducible routes, like the grid engine's two‑attempt
   ordering).

**Non‑goals (v1)**
- Not a push‑and‑shove / rip‑up‑everything topological router. One net at a time with
  rip‑up‑and‑reroute passes, as today.
- Not changing bond assignment or fan‑out escape generation (keep `BondAssigner`).
- No curved traces; poly‑lines only (optionally 45°‑limited via the `orthogonal`/octilinear
  setting).
- Not multi‑threaded in v1.

---

## 4. Geometry model

- **Units.** Integer micrometres throughout, as the model already is (`PointUm`, `RectUm`).
  Internal maths in `double`; emit rounded ints.
- **Primitives.** `PointUm`, segment, `RectUm`, and a new `Poly` (CCW ring, integer verts)
  for substrate outline, resistor bodies and trimmed keep‑outs. Reuse `Segments` for
  point/segment/rect distance and proper‑intersection tests.
- **Clearance algebra.** A trace of width `w` keeping space `s` from an obstacle polygon `O`
  is legal iff `dist(centreline, O) ≥ w/2 + s`. Equivalent to routing a *zero‑width* path
  through `O` **grown** (Minkowski sum) by `w/2 + s`. The router grows obstacles **per net**
  (because `w` is per net via `netWidths`) rather than once.
- **Layers.** Each `CONDUCTOR` layer is its own 2‑D routing plane. Planes couple only at
  dielectric **crossover windows** (vertical transitions) and at **pads**, which are opaque
  through the whole stack (see §6.4).

---

## 5. Free‑space model (the core decision)

The router needs a fast "what is reachable and how far" structure that is exact, cheap to
rebuild per net (clearances are per‑net), and supports both printed traces and the
hybrid interconnects.

**Recommendation: per‑net grown‑obstacle set + visibility/A\* over obstacle corners,
with a rubber‑band refinement.** Concretely:

1. **Per‑net obstacle set.** For net `N` with width `w`, build the union of all blockers
   grown by `w/2 + minSpace` (foreign copper, pads, fixed traces of other nets, keep‑out
   rings, edge band, `keepouts[layer]`). Own‑net copper is *not* a blocker — it is a target
   (any point on the growing tree closes the connection). Grown polygons come from
   rectangle/ segment offsetting (round or mitred corners; mitred is fine and cheaper).
2. **Search graph.** Nodes = the convex **corners** of the grown obstacles plus the
   connection's start/target shapes. An edge joins two nodes if the open segment between
   them crosses no grown obstacle interior (a *visibility* edge). This is the classic
   shortest‑path‑in‑the‑plane‑with‑polygonal‑obstacles graph; the Euclidean shortest path
   is a poly‑line whose interior vertices are obstacle corners.
3. **A\*** over that graph with a Euclidean heuristic, biased by the cost model of §7.
4. **Rubber‑band pull‑tight** on the found poly‑line (replace the existing grid `simplify`):
   walk the line, and wherever it bends around a corner it does not need, straighten it to
   the tightest legal path. This is where the shortening/`dejog` clean‑ups that HLayout runs
   afterwards get most of their current job done for free.

Why this over the two usual alternatives:

- vs **corner‑stitching / tile planes (Magic‑style):** tiles are elegant for incremental
  insert/delete and give O(1) neighbour walks, but building the rubber‑band sketch and the
  wire‑bond geometry on top is more machinery than a small hybrid board needs. Visibility +
  A\* is less code and directly yields the exact shortest legal poly‑line.
- vs **a much finer grid:** rejected — that is the thing we are replacing.

Performance notes:
- Candidate‑edge explosion is the risk (corners²). Mitigate with (a) a reduced visibility
  graph — only *tangent* edges between convex corners matter for shortest paths; (b) an
  R‑tree (`STRtree`, already a dependency) over grown obstacles for the segment‑clear test;
  (c) a per‑net *bounding box* (start/target bbox grown by a slack) so only obstacles near
  the connection enter the graph. These keep it to the obstacles that actually matter for
  one connection, the same locality the grid's windowed search has today.

---

## 6. Hybrid‑specific rules — how each is enforced gridlessly

This is the section that makes it a *hybrid* router. Each rule maps to a hard constraint in
either the search (never generated) or a legality predicate (candidate rejected), and to the
`HybridRouteDrc` code that must stay clean. "Grown by X" means Minkowski‑grown as in §4.

### 6.1 Printed conductor rules (`ConductorRules`)

| Rule | Field | Enforcement | DRC |
| --- | --- | --- | --- |
| Trace width | per‑net `netWidths`, else `minLineUm` | the width carried on every `RoutedTrace`; search grows obstacles by `w/2` | `TRACE_WIDTH` |
| Spacing to other nets | `minSpaceUm` | obstacle grown by `w/2 + minSpace`; visibility edges cannot cross grown interiors | `TRACE_SPACING` |
| Substrate edge | `edgeClearanceUm` | the substrate outline, *inset* by `edgeClearanceUm + w/2`, is the routing boundary; nodes/edges clipped to it | `TRACE_EDGE_CLEARANCE` |

### 6.2 Printed resistors and chip bodies (two‑pin components)

- A `TwoPinComponent` body (plus `keepoutUm`) is an obstacle on each of its `obstacleLayers`
  — grown like any blocker. No trace of a foreign net may enter it → `TRACE_COMPONENT_KEEPOUT`.
- A net that **owns a pin** of the component may reach that pin: the body is trimmed by the
  owned pin's footprint (the existing `HybridRouteDrc.trim` logic), and only the *trimmed*
  remainder blocks the owning net → `TRACE_COMPONENT_BODY`. In the gridless world this is a
  polygon subtraction done once per (component, owning‑net), cached.
- Printed resistors never carry a crossover (their ink is not a conductor layer); windows
  over a body are already rejected → `WINDOW_COMPONENT_BODY` / `WINDOW_COMPONENT_KEEPOUT`.

### 6.3 Bare dies

- Die bounds grown by `attachKeepoutUm` is a hard obstacle on **every** conductor layer; no
  trace and no crossover may enter → `TRACE_DIE_KEEPOUT` / `WINDOW_DIE_KEEPOUT`. Fan‑out
  escapes from `BondAssigner` start *outside* this ring, unchanged.

### 6.4 Pads are opaque through the stack

A substrate pad, a component pin and a bond finger are bare metal with no dielectric over
them, so the *other* conductor may not run across them. Gridless rule: every terminal
polygon is a blocker **on all conductor layers except as a target for its own net**, grown
per foreign net by that net's `w/2 + minSpace`. (This is exactly the bug fixed in the grid
engine where second‑layer traces shorted over pads; here it is intrinsic to the obstacle
set, no special case.) Jumper lands and crossover pads created during routing join the same
set the moment they are placed.

### 6.5 Dielectric crossover windows (the "via" of a hybrid)

A crossover is the only way to change conductor layer. It is a square window of side
`padUm = windowMinUm + 2·windowEnclosureUm` (or the per‑net `netWindows` size for power
nets), opening `windowMinUm`, on the dielectric between two adjacent conductors.

Gridless placement and rules:
- A window may be inserted at any point `p` on the current trace where a square pad of side
  `padUm` centred at `p` is legal on **both** conductors being joined: clear of foreign
  copper + `minSpace` (`WINDOW_SPACING`), inside the edge inset (`WINDOW_EDGE_CLEARANCE`),
  off every die (`WINDOW_DIE_KEEPOUT`) and component body/keep‑out, and enclosing its
  opening (`WINDOW_ENCLOSURE`, `WINDOW_SIZE`).
- **Where the dielectric is open** (the band outside the dielectric boundary, resistor
  keep‑outs, hand‑drawn holes) the two conductors are not insulated — the upper layer and
  any window there would short. HLayout already passes these regions as
  `keepouts["COND2"]`; the gridless router treats them as hard obstacles on the upper plane
  and forbids windows inside them. (Same information, no grid.)
- Cost: a layer change costs `crossoverCost` mm of trace, so the search prefers staying on
  one layer and only dips to the other to cross — exactly as now.

### 6.6 Wire‑bond jumpers (single‑conductor crossings)

On a one‑conductor design a jumper is the *only* way for one net to cross another: an air
bridge of gold wire between two **jumper lands** (square pads of side `jumperLandUm`). These
are not vias; they have their own rule family, and this is the richest hybrid‑specific part.

Enforced gridlessly:

- **When considered.** Only after the planar search for a connection fails, and only while
  `jumpersAllowed` and the `maxJumpers` budget hold (as today). Cost `jumperCost` mm.
- **Take‑off / landing.** A jumper hops *foreign* copper: from a point where the printed
  path is blocked straight ahead, over the blocker, to the first point beyond where a land
  fits. Gridless: shoot a ray in the travel direction from the blocked frontier; the first
  clear interval beyond the blocker whose start admits a legal `jumperLandUm` land is the
  landing. Both lands must satisfy §6.4 (opaque pads) and land‑to‑land length ∈
  `[jumper.minLengthUm, jumper.maxLengthUm]` → `JUMPER_*_TOO_SHORT/_TOO_LONG`.
- **No flying over parts.** A jumper wire may not pass over a die, a chip body (termination
  included) or a printed resistor body — the `noFly` set. Exact segment‑vs‑rect test against
  each `noFly` polygon (`Segments.toRect`), as the grid engine now does, but with the true
  landing points.
- **Capillary clearance.** The bonder capillary needs room at the *second* bond: each land
  centre must be ≥ `capillaryClearanceUm` from any other object and from other jumpers' land
  centres → `CAPILLARY_CLEARANCE`. Checked exactly against the obstacle R‑tree.
- **No crossing wires** (unless `crossingsAllowed`): a new jumper segment may not properly
  cross an existing bond/jumper of another net; if crossings are allowed, the two must
  differ in loop height by ≥ `minLoopSeparationUm` → `WIRE_CROSSING`, `WIRE_CROSSING_LOOP`.
- **Loop height** ≥ `jumper.minLoopHeightUm` on the emitted `BondWire.spec` →
  `JUMPER_LOOP_HEIGHT`.
- Two‑conductor designs: jumpers disabled (`jumpersAllowed=false`), because a land cannot be
  bonded under the dielectric — already set by the HLayout export.

### 6.7 Die bonds, bond fingers and fan‑out (pre‑placed, honoured not generated)

`BondAssigner` places a finger and a die→finger bond for every unbonded die pad before
routing; HLayout's own fan‑out does the same and arrives as fixed wires. The router treats
their **fingers as ordinary terminals** (targets of their net) and their **bond wires as
fixed geometry** that the above wire rules protect. The finger‑side rules —
`fingerPitchUm`, `dieToFingerMin/MaxUm`, `maxAngleDeg`, `spacingDiameters` — are owned by
`BondAssigner` / `HybridDrc` and are unchanged; the shape router must simply not *move* or
*cross* them illegally (covered by §6.4 and §6.6).

### 6.8 Fixed copper, net widths, directions (already in options)

- **Fixed traces** (`fixedTraces`/`fixedJoins`): own‑net fixed copper is a target the tree
  may attach to; foreign fixed copper is an obstacle. A blank‑net fixed trace blocks all.
  Pre‑joined terminal groups (`connected`) start unioned so they are not reconnected.
- **Per‑net width** (`netWidths`): obstacles grown by that net's `w/2`; power nets get wider
  traces and larger crossover pads (`netWindows`).
- **Preferred direction** (`directions`, `againstDirectionCost`): a soft cost. In the
  gridless graph, bias each visibility edge by the angle between it and the layer's
  preferred axis — an edge across the grain costs `length × againstDirectionCost`. With
  `orthogonal` set, restrict emitted segments to H/V (and the rubber‑band step to Manhattan
  corners), the gridless analogue of the current 90°‑only mode.

### 6.9 Connectivity (the final oracle)

After routing, every pad of a net must sit in one electrical piece — `HybridRouteDrc`'s
union‑find over touching copper, windows and wires. The gridless router maintains the same
per‑net tree during search (own copper is a target), so a completed net is connected by
construction; `NET_OPEN` / `UNROUTED` only appear for connections the search genuinely could
not make, which are reported as `UnroutedConnection`s exactly as now.

---

## 7. Routing algorithm

```
route(design):
    design = BondAssigner.assign(design)          # reused: fingers + die bonds
    P = ShapeProblem(design)                       # per-layer planes, nets, obstacles, noFly
    best = attempt(P, P.defaultOrder())
    for pass in 1..ripupPasses while best has failures:
        order = failures-first, rest reshuffled    # same policy as the grid engine
        a = attempt(P, order); if a better: best = a
    return best.toRoutedHybrid()

attempt(P, order):
    for net in order:
        routeNet(net)                              # one net, planar then crossover then jumper

routeNet(net):
    build per-net grown obstacle set (all layers), own copper as targets
    while a target group is unconnected:
        path = planarSearch(net)                   # visibility A*, any layer, crossovers allowed
        if path == null and jumpersLeft: path = jumperSearch(net)
        if path == null: record UnroutedConnection; break
        commit(path)                               # emit trace(s), windows, jumper lands+wire
                                                   # add new copper to this net's targets &
                                                   # to the global obstacle set for later nets
```

**Cost model** (unchanged in spirit): trace length `1` per µm‑mm; a crossover `+crossoverCost`;
a jumper `+jumperCost + wire length`; against‑direction `× againstDirectionCost`. Cost order
trace < crossover < jumper is validated up front (`HybridRoutingOptions.isOrdered`).

**Determinism.** Nets ordered by spread then name; ties in the visibility graph broken by a
fixed corner ordering; the rip‑up reshuffle uses a fixed seed. Same route every run.

**Incrementality.** Committing a net inserts its grown copper into the shared obstacle
R‑tree so later nets see it. Rip‑up removes a net's copper from the tree. No full rebuild per
pass — only the ripped net's geometry changes.

---

## 8. Integration & API

- New package `autoroute.hybrid.shape` with `ShapeRouter.route(HybridDesign) → RoutedHybrid`,
  mirroring `HybridRouter.route` so `HybridRouterCli` dispatches on
  `design.routing().engine()` (new nullable field, default `"grid"`).
- Reuse `BondAssigner`, `Segments`, `RectUm`, `STRtree`, and every `board.hybrid` / `rules`
  type. Add only geometry helpers: polygon offset (grow/shrink), polygon subtraction (for
  §6.2 body trimming), visibility‑edge clearance test, rubber‑band tighten.
- `HybridRouteDrc` is unchanged and is the gate: a shape route that does not pass it is a
  bug in the router, not an accepted result.
- HLayout: add `engine="shape"` passthrough in `hlayout/scripting/hybridroute.py`
  `export_design` (one line into `design["routing"]`) and a "Routing engine" dropdown in the
  Autoroute window once the shape engine is trusted. No other HLayout change — export,
  import, via drawing, clean‑ups all already operate on `RoutedHybrid`.

---

## 9. Milestones

1. **Geometry kit + tests.** `Poly`, offset, subtraction, visibility‑edge clear test,
   rubber‑band. Property tests against brute force. *(No routing yet.)*
2. **Single‑layer planar router.** One conductor, no crossovers, no jumpers. Passes
   `single-layer-crossing` minus the crossing net; beats the grid on a tight‑spacing fixture.
3. **Jumpers (§6.6).** Full single‑conductor parity with the grid engine on
   `single-layer-crossing` and the 1‑layer stress designs; `HybridRouteDrc` clean.
4. **Two‑layer crossovers (§6.5).** `single-die-op-amp` (2‑layer) complete, 0 jumpers,
   DRC clean; power‑net windows sized from `netWindows`.
5. **Fixed copper, widths, directions (§6.8).** Parity with the merged grid engine's new
   options; the `netWidthsAndExistingCopperAreHonoured` test passes on the shape engine too.
6. **Bench & switch.** Run both engines over the 40‑design stress set + PRE+AMP; compare
   completion, DRC, wall‑time. When shape ≥ grid on completion and DRC with acceptable time,
   flip the default `engine` and keep grid as a fallback.

Each milestone ships behind `engine="shape"`, so `main` stays releasable throughout.

---

## 10. Testing

- **Unit (geometry).** Offsetting, subtraction, visibility clearance, rubber‑band — property
  tests vs a slow exact reference.
- **Router fixtures.** The existing `Fixtures` designs, asserted via `HybridRouteDrc.check()
  == []` and the same `RouteStats` assertions the grid tests use — run the whole
  `HybridRouterTest` suite against *both* engines (parametrise on engine).
- **Differential.** For each stress design, assert the shape engine's result is DRC‑clean
  and routes ≥ as many connections as the grid engine (never a regression).
- **Determinism.** Same input twice → byte‑identical `RoutedHybrid`.
- **HLayout end‑to‑end.** `tests/test_hybridroute.py` re‑run with `engine="shape"`: no
  shorts (`find_shorts`), no `resistor_crossings`, tracks drawn at the chosen `track_cap`.

---

## 11. Risks & open questions

- **Visibility‑graph blow‑up** in dense regions. Mitigation in §5 (tangent‑only edges,
  R‑tree, per‑connection bbox). If still too slow, fall back to a *local* finer grid only
  inside the failing region — a hybrid of both engines.
- **Robust polygon off/subtract at integer µm.** Round‑off at sharp corners. Use `double`
  internally, snap on emit, and keep a guard band (round offsets *up*). Consider a vetted
  geometry lib (JTS) vs. hand‑rolled — JTS adds a dependency but removes a class of bugs;
  decide at milestone 1.
- **Jumper landing search** is the subtlest hybrid part; keep it exactly rule‑for‑rule with
  §6.6 and lean on `HybridRouteDrc` + `HybridDrc` as the oracle in tests.
- **Bond assignment coupling.** v1 keeps `BondAssigner` as a pre‑pass; a later phase could
  let the router move a finger when a net cannot otherwise reach it (angle/length permitting)
  — out of scope here, noted so the interface leaves room.

---

## 12. One‑paragraph summary

Replace the uniform‑grid A\* in `HybridRouter` with a gridless engine that routes on exact
polygon geometry: per‑net Minkowski‑grown obstacles, a tangent visibility graph searched
with A\*, and a rubber‑band tighten. Every hybrid rule — printed spacing and width, resistor
and chip bodies, die attach keep‑outs, pads opaque through the stack, dielectric crossover
windows (including the open‑dielectric keep‑outs), and the full wire‑bond jumper family
(length, capillary clearance, no‑fly‑over‑parts, no‑crossing, loop height) — is a hard
constraint in the search or a legality predicate, with `HybridRouteDrc` as the unchanged
acceptance oracle. It reuses the whole `board.hybrid` / `rules` / options model and ships
behind an `engine="shape"` switch so it can be benchmarked against the grid router before
becoming the default.
