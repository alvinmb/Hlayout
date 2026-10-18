# HLayout hybrid router — Phases 4, 5 and 6

Chip-and-wire router for HLayout, built inside a fork of
[Freerouting](https://github.com/freerouting/freerouting) (GPLv3). Thick-film alumina, LTCC
optional, no thin film. HLayout writes `design.hybrid.json`, runs the router as a separate
process and reads back `design.routed.json`.

## Run it

```bat
java -cp freerouting-executable.jar app.freerouting.cli.hybrid.HybridRouterCli design.hybrid.json [design.routed.json] [--force] [--bond-diagram wires.csv]
```

| Exit | Meaning |
| --- | --- |
| 0 | Routed completely, post-route DRC clean |
| 1 | Design invalid or pre-route DRC failed — nothing written (`--force` routes anyway) |
| 2 | Routed, but some connections unrouted (listed in the output) |
| 3 | Post-route DRC errors other than unrouted connections |
| 64 / 74 | Bad arguments / file error |

The output holds the design with added fingers, jumper lands and jumper wires, plus `traces`,
`windows`, `unrouted`, `stats` and the post-route DRC `issues`. `examples/*.routed.json` are real
outputs for the two example designs.

## Set up (Windows)

`freerouting-overlay\` holds complete files laid out as in the Freerouting repo (Phases 1, 2 and 4).
Copy it over a clean checkout:

```bat
cd C:\dev\HLayout\router
git clone https://github.com/freerouting/freerouting.git freerouting
cd freerouting
git checkout 5aa1b24
git switch -c hlayout-hybrid
xcopy /E /Y ..\freerouting-overlay\* .
gradlew.bat test --tests "app.freerouting.*.hybrid.*"
gradlew.bat spotlessCheck checkstyleMain checkstyleTest
gradlew.bat executableJar
```

Needs JDK 25. Two upstream files are replaced: `docs/architecture.md`, and
`src/main/resources/META-INF/native-image/reachability-metadata.json` (the hybrid model and rule
classes registered as serializable, which the `NativeImageSerializationMetadataArchTest` ArchUnit
test requires).

If Gradle stops with `Unable to establish loopback connection`, Java can't create its
Unix-domain socket in the temp folder. Point it at a short writable folder first:

```bat
set JAVA_TOOL_OPTIONS=-Djdk.net.unixdomain.tmpdir=C:\dev\tmp_jdk
```

## What Phase 4 adds

| Package | Contents |
| --- | --- |
| `autoroute.hybrid` | `BondAssigner` (fingers for unbonded die pads), `HybridRouter` (fan-out escapes, octilinear grid A*, crossovers, jumpers, single-layer mode, one rip-up reorder pass), `RoutingGrid` |
| `drc.hybrid` | `HybridRouteDrc`: exact post-route check — trace and window spacing, edge, die/component keep-outs, window size/enclosure/layers, plus an independent connectivity check |
| `board.hybrid` | `RoutedHybrid`, `RoutedTrace`, `CrossoverWindow`, `UnroutedConnection`, `RouteStats`; `Segments` (shared geometry) |
| `cli.hybrid` | `HybridRouterCli` |
| `rules.hybrid` | `PadRules` (finger and jumper-land sizes) |

New JSON: `rules.pads { fingerWidthUm, fingerLengthUm, jumperLandUm }` and `routing.gridUm`.

Cost order: trace < crossover (10 mm) < jumper (50 mm). Jumpers are only searched for after a
printed route fails, and only take off where the printed path is blocked straight ahead.

## What Phase 5 adds (outputs)

| Where | Contents |
| --- | --- |
| `board.hybrid.BondDiagram` / `BondDiagramEntry` | The bond diagram of a design: every wire with both ends on the substrate, in bonding order (die bonds die by die in pad order, then die-to-die, then jumpers), with material, diameter, bond type, loop height, plan length and the departure angle off the die-edge normal at each die end |
| `io.hybrid.BondDiagramCsv` | Writes it as CSV (`seq,wire,net,purpose,first_bond,first_x_um,...,fixed`) |
| `cli.hybrid.HybridRouterCli` | `--bond-diagram <file.csv>` writes the routed design's bond diagram; console output now uses the console's own encoding, so "µm" prints correctly on Windows |
| HLayout `hlayout/io/hybrid_routed.py` | Reads `*.routed.json`; the same bond diagram in Python (identical rows to the router's CSV); CSV read/write |
| HLayout `Session.import_routed_hybrid()` | Draws a routed result in its own cell, one undo step: substrate, dies and two-pin parts as placed cells, fingers / jumper lands / external pads as numbered pads, traces, crossover pads + dielectric windows + dielectric fill, wire bonds and jumpers; sets net names; keeps the bond diagram with the design |
| HLayout `Session.generate_bond_table()`, `export_bond_diagram()` | Bond diagram as a drawing table and as CSV; the assembly drawing adds the table automatically when a bond diagram is present |
| HLayout commands | `HYBRID IMPORT <file.routed.json> [cell=] [fill=]`, `HYBRID BONDS [<file.csv>]`, `BONDTABLE <cell> <layer>` |

Print-layer Gerbers come from HLayout's existing Gerber export of the imported cell. Resistor trim
tables stay in HLayout (Resistor Designer); the router passes resistor placement through unchanged.
`examples/*.bonds.csv` are the router's bond diagrams for the two examples.

## What Phase 6 adds (GUI, in HLayout)

Phase 6 lives in HLayout, which draws the routed hybrid after `HYBRID IMPORT`; the router stays a
headless process.

| Tool | What it does |
| --- | --- |
| Hover a wire bond | Highlights it; the status bar shows wire id, net, both ends, length, metal and diameter, bond type, **loop height** and the departure angle at the die |
| Edit > Move Bond Finger | Click a finger (or jumper land), move, click: its wires follow (re-bond). Live preview is green while every wire meets the bond rules (length min/max, angle off the die-edge normal), red with the reason when not, and warns when the finger leaves its trace (net needs re-routing). One undo step; the bond diagram updates |
| Edit > Resize Resistor | Click a printed resistor, move along its length, click: the body (and its pins) follow, with a live readout R = Rs × L / W, Rs taken from the value in the router file |
| Commands | `BONDINFO <x,y>`, `MOVEFINGER <pad\|x,y> <x,y>`, `RESIZE <refdes> [length=] [width=] [preview=1]` |
| Python | `Session.bond_wire_info()`, `move_bond_finger(dry_run=)`, `resistor_at()`, `resistor_info()`, `resize_resistor(dry_run=)` |

A moved finger or a resized resistor changes geometry the router laid out; the tools say which
nets need re-routing rather than patching the traces.

## Results

| Design | Result |
| --- | --- |
| `single-die-op-amp` (2 layers) | 8/8 routed, 2 crossovers, 0 jumpers, DRC clean |
| `single-layer-crossing` (1 layer) | 6/6 routed, 3 jumpers, DRC clean |
| Same crossing on 2 layers | complete with crossovers, 0 jumpers, DRC clean |

Stress run on generated designs (two 12-pad dies, unbonded, 14 random nets, edge pads,
3 resistors, 16 × 12 mm), 20 seeds × 1 and 2 layers:

- about 4 s per design; 26–30 of 30 connections routed; 16 of 40 designs complete;
- **6 of 40 designs still had post-route DRC errors** — see Known issues.

## Verification

On Windows with Microsoft JDK 25.0.4.1 and Gradle 9.8.0 (2026-10-18): the 108 hybrid tests pass,
spotlessCheck passes, Checkstyle reports nothing in the hybrid packages, the ArchUnit suites pass
(after the metadata fix above), `executableJar` builds, and the CLI reproduces both example outputs
exactly.

Earlier, in a sandbox with JDK 21 (no Maven access): 108 tests pass; google-java-format 1.30.0 and
Checkstyle 13.9.0 (repo config) report nothing; both examples validate against the schema and
route DRC-clean. Not run: the Gradle build on JDK 25 and the ArchUnit suite.

## Known issues (next)

1. **Trace over a resistor body via its own pin.** When a net owns a pin that overlaps a resistor
   body, the router may run a trace inside that pin close enough that half its width spills onto
   the bare body (`TRACE_COMPONENT_BODY`). Seen on 6 of 40 generated designs, never on the
   examples. Fix in progress: check a net's own pad cells exactly instead of with the grid's
   conservative clearance (a first attempt made pins unreachable and was backed out).
2. Grid clearance is conservative (about +30% on spacing), which costs routing capacity in tight
   areas; a finer `routing.gridUm` reduces it.
3. Not yet connected to Freerouting's own shape-based engine; that needs a real build.
4. Single rip-up pass; fingers go straight out from the die (no angled fan-out).
