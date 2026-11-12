package app.freerouting.autoroute.hybrid;

import app.freerouting.board.hybrid.BondEndpoint;
import app.freerouting.board.hybrid.BondType;
import app.freerouting.board.hybrid.BondWire;
import app.freerouting.board.hybrid.ComponentPin;
import app.freerouting.board.hybrid.CrossoverWindow;
import app.freerouting.board.hybrid.Die;
import app.freerouting.board.hybrid.HybridDesign;
import app.freerouting.board.hybrid.HybridRoutingOptions;
import app.freerouting.board.hybrid.PointUm;
import app.freerouting.board.hybrid.RectUm;
import app.freerouting.board.hybrid.RouteStats;
import app.freerouting.board.hybrid.RoutedHybrid;
import app.freerouting.board.hybrid.RoutedTrace;
import app.freerouting.board.hybrid.Segments;
import app.freerouting.board.hybrid.SubstratePad;
import app.freerouting.board.hybrid.SubstratePadRole;
import app.freerouting.board.hybrid.TwoPinComponent;
import app.freerouting.board.hybrid.TwoPinKind;
import app.freerouting.board.hybrid.UnroutedConnection;
import app.freerouting.board.hybrid.WireMaterial;
import app.freerouting.board.hybrid.WireSpec;
import app.freerouting.rules.hybrid.HybridRules;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.TreeMap;

/**
 * Grid router for chip-and-wire hybrids.
 *
 * <p>Pipeline: bond assignment ({@link BondAssigner}), then each net is grown as a tree from its
 * first group of terminals, one A* search per remaining group. Terminals already joined by bond
 * wires or jumpers count as one group. The search is octilinear on a uniform grid with a bend
 * penalty, so traces come out as 0°, 45° and 90° segments.
 *
 * <p>Interconnect, cheapest first: trace length; a dielectric crossover between adjacent routable
 * conductor layers ({@code crossoverCost} mm); a wire-bond jumper that hops foreign copper ({@code
 * jumperCost} mm plus its length). Jumpers are only searched for after a printed route has failed,
 * and only while the jumper budget lasts. With one routable layer there are no crossovers, so
 * jumpers are the only way across.
 *
 * <p>If any connection fails, the whole design is routed again with the failed nets first, and the
 * best of the runs is kept (fewest unrouted, then fewest jumpers, then shortest). Everything
 * is deterministic: same input, same output, independent of time budget or threads.
 */
public final class HybridRouter {

  private static final int FREE = -1;
  private static final int CONFLICT = -3;
  private static final int NO_DIR = 8;
  private static final int[] DX = {1, 1, 0, -1, -1, -1, 0, 1};
  private static final int[] DY = {0, 1, 1, 1, 0, -1, -1, -1};
  private static final byte MOVE_START = 0;
  private static final byte MOVE_STEP = 1;
  private static final byte MOVE_WINDOW = 2;
  private static final byte MOVE_HOP = 3;

  private HybridRouter() {}

  /** Routes the design. Run the validator and pre-route DRC first. */
  public static RoutedHybrid route(HybridDesign input) {
    BondAssigner.Result bonds = BondAssigner.assign(input);
    Problem problem = new Problem(bonds.design());
    Attempt first = new Attempt(problem, problem.defaultOrder());
    first.run();
    Attempt best = first;
    Attempt last = first;
    int passes = problem.options.ripupPasses();
    java.util.Random shuffle = new java.util.Random(1);
    for (int pass = 1; pass <= passes && !last.failedNets.isEmpty(); pass++) {
      // rip up: the nets that failed go first, the rest after (shuffled after the first pass,
      // so each pass tries a different order)
      List<String> rest = new ArrayList<>();
      for (String net : problem.defaultOrder()) {
        if (!last.failedNets.contains(net)) {
          rest.add(net);
        }
      }
      if (pass > 1) {
        java.util.Collections.shuffle(rest, shuffle);
      }
      List<String> order = new ArrayList<>(last.failedNets);
      order.addAll(rest);
      Attempt next = new Attempt(problem, order);
      next.run();
      if (next.isBetterThan(best)) {
        best = next;
      }
      last = next;
    }
    return best.result(bonds);
  }

  // --- static problem description ------------------------------------------------------------

  /** A fixed trace segment run: existing copper of a net, joined to a terminal group or loose. */
  private record Fixed(String net, int layer, List<PointUm> points, int width, String joinId) {}

  /** A copper terminal the router must connect: substrate pad or component pin. */
  private record Terminal(String id, String net, int layer, RectUm rect, String key) {}

  /** A resolved wire segment, for crossing and spacing checks against new jumpers. */
  private record WireSeg(String net, PointUm a, PointUm b, int diameterUm) {}

  /** Everything about the design that does not change while routing. */
  private static final class Problem {
    final HybridDesign design;
    final HybridRules rules;
    final HybridRoutingOptions options;
    final RoutingGrid grid;
    final List<String> layers;
    final boolean[] windowUp;
    final int traceWidth;
    final int space;
    final int windowOpening;
    final int windowPad;
    final int landSize;
    final double traceClearance;
    final double traceHardClearance;
    final double windowClearance;
    final double windowHardClearance;
    final double landClearance;
    final double landHardClearance;
    final boolean[][] rawHard;
    final float[][] hardDist;
    final int[][] zone;
    final List<Set<String>> zoneNets = new ArrayList<>();
    final List<Terminal> terminals = new ArrayList<>();
    final Map<String, List<List<Terminal>>> groups = new TreeMap<>();
    final List<String> nets = new ArrayList<>();
    final Map<String, Integer> netIds = new HashMap<>();
    final List<RectUm> obstacles = new ArrayList<>();
    /**
     * Where no jumper wire may fly: every die, a chip part's whole outline (body and terminations
     * -- it stands proud of the substrate), a printed resistor's body.
     */
    final List<RectUm> noFly = new ArrayList<>();
    final List<WireSeg> wires = new ArrayList<>();
    final List<RoutedTrace> escapes = new ArrayList<>();
    final List<Fixed> fixed = new ArrayList<>();

    Problem(HybridDesign design) {
      this.design = design;
      this.rules = design.rules();
      this.options = design.routing();
      this.layers = design.routingLayers();
      int substrateW = design.substrate() == null ? 1000 : design.substrate().widthUm();
      int substrateH = design.substrate() == null ? 1000 : design.substrate().heightUm();
      this.traceWidth = rules.conductor().minLineUm();
      this.space = rules.conductor().minSpaceUm();
      int pitch =
          options.gridUm() > 0 ? options.gridUm() : Math.max(25, Math.min(traceWidth, space) / 2);
      this.grid = new RoutingGrid(substrateW, substrateH, pitch);
      this.windowOpening = rules.conductor().windowMinUm();
      this.windowPad = windowOpening + 2 * rules.conductor().windowEnclosureUm();
      this.landSize = rules.pads().jumperLandUm();
      double k = RoutingGrid.OCTILE_FACTOR;
      double slack = grid.slack;
      this.traceClearance = (traceWidth / 2.0 + space + slack) * k;
      this.traceHardClearance = (traceWidth / 2.0 + slack) * k;
      // Window pads and jumper lands are squares: their corners reach half the diagonal.
      double windowReach = windowPad * Math.sqrt(2) / 2;
      double landReach = landSize * Math.sqrt(2) / 2;
      this.windowClearance = (windowReach + space + slack) * k;
      this.windowHardClearance = (windowReach + slack) * k;
      this.landClearance = (landReach + space + slack) * k;
      this.landHardClearance = (landReach + slack) * k;

      int count = layers.size();
      windowUp = new boolean[count];
      for (int l = 0; l + 1 < count; l++) {
        windowUp[l] = design.stack().hasDielectricBetween(layers.get(l), layers.get(l + 1));
      }
      rawHard = new boolean[count][grid.cells];
      zone = new int[count][grid.cells];
      for (int[] z : zone) {
        Arrays.fill(z, -1);
      }
      markEdgeBand(substrateW, substrateH);
      for (Die die : design.dies()) {
        RectUm r = die.bounds().grow(die.attachKeepoutUm());
        for (int l = 0; l < count; l++) {
          markHard(l, r);
        }
        obstacles.add(die.bounds());
        noFly.add(die.bounds());
      }
      List<TwoPinComponent> comps = design.components();
      for (int ci = 0; ci < comps.size(); ci++) {
        TwoPinComponent comp = comps.get(ci);
        Set<String> pinNets = new HashSet<>();
        for (ComponentPin pin : comp.pins()) {
          if (pin.net() != null) {
            pinNets.add(pin.net());
          }
          obstacles.add(comp.pinBounds(pin));
        }
        zoneNets.add(pinNets);
        obstacles.add(comp.bodyBounds());
        RectUm fly = comp.bodyBounds();
        if (comp.kind() == TwoPinKind.CHIP) {
          for (ComponentPin pin : comp.pins()) {
            RectUm pr = comp.pinBounds(pin);
            fly =
                new RectUm(
                    Math.min(fly.minX(), pr.minX()),
                    Math.min(fly.minY(), pr.minY()),
                    Math.max(fly.maxX(), pr.maxX()),
                    Math.max(fly.maxY(), pr.maxY()));
          }
        }
        noFly.add(fly);
        RectUm zoneRect = comp.bodyBounds().grow(comp.keepoutUm());
        double reach = traceWidth / 2.0 + slack;
        for (String layer : comp.obstacleLayers()) {
          int l = layers.indexOf(layer);
          if (l < 0) {
            continue;
          }
          markHard(l, comp.bodyBounds());
          final int index = ci;
          grid.forOverlapping(
              zoneRect.grow((int) Math.ceil(reach)),
              c -> {
                if (zoneRect.distanceTo(grid.centre(c)) <= reach) {
                  zone[l][c] = index;
                }
              });
        }
      }
      for (Map.Entry<String, List<List<Integer>>> e : options.keepouts().entrySet()) {
        int l = layers.indexOf(e.getKey());
        if (l < 0 || e.getValue() == null) {
          continue;
        }
        for (List<Integer> r : e.getValue()) {
          if (r != null && r.size() == 4) {
            markHard(l, new RectUm(r.get(0), r.get(1), r.get(2), r.get(3)));
          }
        }
      }
      collectFixed();
      hardDist = new float[count][];
      for (int l = 0; l < count; l++) {
        final boolean[] hard = rawHard[l];
        hardDist[l] = grid.distanceTransform(c -> hard[c]);
      }
      collectTerminals();
      collectWires();
    }

    /**
     * Existing traces that stay: those of a net with terminals become that net's copper; the rest
     * (no net, or a net nothing else is on) block every net.
     */
    private void collectFixed() {
      Set<String> terminalNets = new HashSet<>();
      for (SubstratePad pad : design.substratePads()) {
        if (pad.net() != null && !pad.net().isBlank()) {
          terminalNets.add(pad.net());
        }
      }
      for (TwoPinComponent comp : design.components()) {
        for (ComponentPin pin : comp.pins()) {
          if (pin.net() != null && !pin.net().isBlank()) {
            terminalNets.add(pin.net());
          }
        }
      }
      List<RoutedTrace> traces = options.fixedTraces();
      List<String> joins = options.fixedJoins();
      for (int i = 0; i < traces.size(); i++) {
        RoutedTrace t = traces.get(i);
        int l = layers.indexOf(t.layer());
        if (l < 0 || t.points().size() < 2) {
          continue;
        }
        int width = Math.max(1, t.widthUm());
        if (t.net() == null || !terminalNets.contains(t.net())) {
          double reach = width / 2.0;
          for (int k = 1; k < t.points().size(); k++) {
            grid.forNearSegment(
                t.points().get(k - 1), t.points().get(k), reach, c -> rawHard[l][c] = true);
          }
          continue;
        }
        String join = i < joins.size() ? joins.get(i) : "";
        fixed.add(new Fixed(t.net(), l, t.points(), width, join));
      }
    }

    int widthOf(String net) {
      return options.widthOf(net, traceWidth);
    }

    private void markEdgeBand(int w, int h) {
      int e = rules.conductor().edgeClearanceUm();
      List<RectUm> band =
          List.of(
              new RectUm(0, 0, w, e),
              new RectUm(0, h - e, w, h),
              new RectUm(0, 0, e, h),
              new RectUm(w - e, 0, w, h));
      for (int l = 0; l < layers.size(); l++) {
        for (RectUm r : band) {
          markHard(l, r);
        }
      }
      // Cells past the far substrate edge, when the size is not a multiple of the pitch.
      for (int c = 0; c < grid.cells; c++) {
        PointUm p = grid.centre(c);
        if (p.x() > w || p.y() > h) {
          for (int l = 0; l < layers.size(); l++) {
            rawHard[l][c] = true;
          }
        }
      }
    }

    private void markHard(int layer, RectUm r) {
      grid.forOverlapping(r, c -> rawHard[layer][c] = true);
    }

    private void collectTerminals() {
      for (SubstratePad pad : design.substratePads()) {
        int l = layers.indexOf(pad.layer());
        if (l >= 0 && pad.id() != null) {
          terminals.add(new Terminal(pad.id(), pad.net(), l, pad.bounds(), "P:" + pad.id()));
          obstacles.add(pad.bounds());
        }
      }
      for (TwoPinComponent comp : design.components()) {
        addPin(comp, comp.pinA(), "A");
        addPin(comp, comp.pinB(), "B");
      }
      addEscapes();
      terminals.sort(Comparator.comparing(Terminal::id));

      UnionFind uf = new UnionFind();
      for (BondWire w : design.wires()) {
        String a = key(w.from());
        String b = key(w.to());
        if (a != null && b != null) {
          uf.union(a, b);
        }
      }
      // Terminals existing copper already joins.
      Map<String, String> keyOfId = new HashMap<>();
      for (Terminal t : terminals) {
        keyOfId.putIfAbsent(t.id(), t.key());
      }
      for (List<String> group : options.connected()) {
        String first = null;
        for (String id : group) {
          String k = keyOfId.get(id);
          if (k == null) {
            continue;
          }
          if (first == null) {
            first = k;
          } else {
            uf.union(first, k);
          }
        }
      }
      Map<String, Map<String, List<Terminal>>> byNet = new TreeMap<>();
      for (Terminal t : terminals) {
        if (t.net() == null || t.net().isBlank()) {
          continue;
        }
        byNet
            .computeIfAbsent(t.net(), n -> new LinkedHashMap<>())
            .computeIfAbsent(uf.find(t.key()), r -> new ArrayList<>())
            .add(t);
      }
      for (Map.Entry<String, Map<String, List<Terminal>>> e : byNet.entrySet()) {
        groups.put(e.getKey(), new ArrayList<>(e.getValue().values()));
      }
      nets.addAll(groups.keySet());
      for (int i = 0; i < nets.size(); i++) {
        netIds.put(nets.get(i), i);
      }
    }

    private void addPin(TwoPinComponent comp, ComponentPin pin, String name) {
      if (pin == null) {
        return;
      }
      int l = layers.indexOf(pin.layer());
      if (l >= 0) {
        String id = comp.refdes() + "." + name;
        terminals.add(new Terminal(id, pin.net(), l, comp.pinBounds(pin), "C:" + id));
      }
    }

    /**
     * Fan-out: gives every bond finger that must be routed a short straight escape trace pointing
     * away from its die, before any net is routed. Without it, the first nets wrap tightly round
     * the die just outside the finger row and wall the remaining fingers in. The escape becomes
     * part of the finger's terminal, so later searches start and end on it. It is shortened, then
     * dropped, where the space is not free.
     */
    private void addEscapes() {
      Map<String, PointUm> diePadOfFinger = new HashMap<>();
      for (BondWire w : design.wires()) {
        if (w.from() == null || w.to() == null || w.from().isDiePad() == w.to().isDiePad()) {
          continue;
        }
        BondEndpoint die = w.from().isDiePad() ? w.from() : w.to();
        BondEndpoint finger = w.from().isDiePad() ? w.to() : w.from();
        design.position(die).ifPresent(p -> diePadOfFinger.put(finger.pad(), p));
      }
      Map<String, Integer> terminalsPerNet = new HashMap<>();
      for (Terminal t : terminals) {
        if (t.net() != null && !t.net().isBlank()) {
          terminalsPerNet.merge(t.net(), 1, Integer::sum);
        }
      }
      int halfFinger = rules.pads().fingerLengthUm() / 2;
      int longest = halfFinger + 2 * (traceWidth + space);
      int shortest = halfFinger + traceWidth;
      List<SubstratePad> fingers =
          design.substratePads().stream()
              .filter(p -> p.role() == SubstratePadRole.BOND_FINGER)
              .filter(p -> diePadOfFinger.containsKey(p.id()))
              .filter(p -> terminalsPerNet.getOrDefault(p.net(), 0) > 1)
              .sorted(Comparator.comparing(SubstratePad::id))
              .toList();
      for (SubstratePad finger : fingers) {
        int l = layers.indexOf(finger.layer());
        if (l < 0) {
          continue;
        }
        PointUm from = diePadOfFinger.get(finger.id());
        int dx = finger.position().x() - from.x();
        int dy = finger.position().y() - from.y();
        int ux = Math.abs(dx) >= Math.abs(dy) ? Integer.signum(dx) : 0;
        int uy = Math.abs(dx) >= Math.abs(dy) ? 0 : Integer.signum(dy);
        if (ux == 0 && uy == 0) {
          continue;
        }
        PointUm a = finger.position();
        for (int length = longest; length >= shortest; length -= grid.pitch) {
          PointUm b = new PointUm(a.x() + ux * length, a.y() + uy * length);
          if (escapeClear(finger, l, a, b)) {
            escapes.add(new RoutedTrace(finger.net(), finger.layer(), List.of(a, b), traceWidth));
            // Widened across the escape only: traces have round ends, so the box must not run
            // past the end of the centre-line.
            int half = traceWidth / 2;
            int hx = ux == 0 ? half : 0;
            int hy = uy == 0 ? half : 0;
            RectUm r =
                new RectUm(
                    Math.min(a.x(), b.x()) - hx,
                    Math.min(a.y(), b.y()) - hy,
                    Math.max(a.x(), b.x()) + hx,
                    Math.max(a.y(), b.y()) + hy);
            terminals.add(
                new Terminal(finger.id() + "~esc", finger.net(), l, r, "P:" + finger.id()));
            break;
          }
        }
      }
    }

    /** Exact clearance check for an escape trace from a to b. */
    private boolean escapeClear(SubstratePad finger, int l, PointUm a, PointUm b) {
      double half = traceWidth / 2.0;
      if (design.substrate() != null) {
        int minX = Math.min(a.x(), b.x());
        int minY = Math.min(a.y(), b.y());
        int maxX = Math.max(a.x(), b.x());
        int maxY = Math.max(a.y(), b.y());
        double edge =
            Math.min(
                Math.min(minX, minY),
                Math.min(
                    design.substrate().widthUm() - maxX, design.substrate().heightUm() - maxY));
        if (edge - half < rules.conductor().edgeClearanceUm()) {
          return false;
        }
      }
      for (Terminal t : terminals) {
        if (t.layer() == l
            && !Objects.equals(t.net(), finger.net())
            && Segments.toRect(a, b, t.rect()) - half < space) {
          return false;
        }
      }
      for (RoutedTrace e : escapes) {
        if (e.layer().equals(finger.layer())
            && !Objects.equals(e.net(), finger.net())
            && Segments.distance(a, b, e.points().get(0), e.points().get(1)) - traceWidth < space) {
          return false;
        }
      }
      for (Die die : design.dies()) {
        if (Segments.toRect(a, b, die.bounds().grow(die.attachKeepoutUm())) < half) {
          return false;
        }
      }
      for (TwoPinComponent comp : design.components()) {
        if (comp.obstacleLayers().contains(finger.layer())
            && Segments.toRect(a, b, comp.bodyBounds().grow(comp.keepoutUm())) < half) {
          return false;
        }
      }
      return true;
    }

    private static String key(BondEndpoint end) {
      if (end == null || end.pad() == null) {
        return null;
      }
      return end.isDiePad() ? "D:" + end.die() + "." + end.pad() : "P:" + end.pad();
    }

    private void collectWires() {
      for (BondWire w : design.wires()) {
        Optional<PointUm> a = design.position(w.from());
        Optional<PointUm> b = design.position(w.to());
        if (a.isPresent() && b.isPresent()) {
          wires.add(new WireSeg(w.net(), a.get(), b.get(), w.spec().diameterUm()));
        }
      }
    }

    int connectionCount() {
      int n = 0;
      for (List<List<Terminal>> g : groups.values()) {
        n += Math.max(0, g.size() - 1);
      }
      return n;
    }

    /** Nets ordered by terminal spread, smallest first, then by name. */
    List<String> defaultOrder() {
      List<String> order = new ArrayList<>(nets);
      Map<String, Long> spread = new HashMap<>();
      for (String net : nets) {
        int minX = Integer.MAX_VALUE;
        int minY = Integer.MAX_VALUE;
        int maxX = Integer.MIN_VALUE;
        int maxY = Integer.MIN_VALUE;
        for (List<Terminal> g : groups.get(net)) {
          for (Terminal t : g) {
            minX = Math.min(minX, t.rect().minX());
            minY = Math.min(minY, t.rect().minY());
            maxX = Math.max(maxX, t.rect().maxX());
            maxY = Math.max(maxY, t.rect().maxY());
          }
        }
        spread.put(net, (long) (maxX - minX) + (maxY - minY));
      }
      order.sort(Comparator.comparingLong((String n) -> spread.get(n)).thenComparing(n -> n));
      return order;
    }
  }

  /** Minimal union-find over string keys. */
  private static final class UnionFind {
    private final Map<String, String> parent = new HashMap<>();

    String find(String k) {
      String p = parent.getOrDefault(k, k);
      if (p.equals(k)) {
        return k;
      }
      String root = find(p);
      parent.put(k, root);
      return root;
    }

    void union(String a, String b) {
      String ra = find(a);
      String rb = find(b);
      if (!ra.equals(rb)) {
        if (ra.compareTo(rb) < 0) {
          parent.put(rb, ra);
        } else {
          parent.put(ra, rb);
        }
      }
    }
  }

  // --- one routing attempt -------------------------------------------------------------------

  /** One pass over all nets in a given order. */
  private static final class Attempt {
    final Problem pb;
    final List<String> order;
    final RoutingGrid grid;
    final int layerCount;
    final int[][] owner;
    final List<RoutedTrace> traces = new ArrayList<>();
    final List<CrossoverWindow> windows = new ArrayList<>();
    final List<SubstratePad> addedPads = new ArrayList<>();
    final List<BondWire> addedWires = new ArrayList<>();
    final List<UnroutedConnection> unrouted = new ArrayList<>();
    final Set<String> failedNets = new LinkedHashSet<>();
    final List<RectUm> obstacles;
    final List<WireSeg> wires;
    final Set<String> usedIds = new HashSet<>();
    int routed;
    double traceLength;

    // Per-net state.
    int netId;
    String net;
    int width;
    double traceClearance;
    double traceHardClearance;
    int windowPad;
    double windowClearance;
    double windowHardClearance;
    float[][] foreign;
    byte[][] landCache;
    byte[][] windowCache;
    boolean[][] own;
    boolean[][] tree;
    int[][] target;

    // Search state, reused across searches.
    final int states;
    final float[] costSoFar;
    final int[] parent;
    final byte[] move;
    final int[] seen;
    final int[] closed;
    int generation;
    final Heap heap = new Heap();

    Attempt(Problem pb, List<String> order) {
      this.pb = pb;
      this.order = order;
      this.grid = pb.grid;
      this.layerCount = pb.layers.size();
      this.owner = new int[layerCount][grid.cells];
      for (int[] o : owner) {
        Arrays.fill(o, FREE);
      }
      for (Terminal t : pb.terminals) {
        Integer id = pb.netIds.get(t.net());
        if (id != null) {
          rasterRect(t.layer(), t.rect(), id);
          if (!t.id().endsWith("~esc")) {
            // A pad -- a part's termination, a bond finger, an I/O pad -- is bare through the
            // whole stack: no other net's trace may cross over it on another conductor.
            for (int l = 0; l < layerCount; l++) {
              if (l != t.layer()) {
                rasterRect(l, t.rect(), id);
              }
            }
          }
        }
      }
      // Escapes are traces with round ends: record their full copper, not just the terminal box.
      double reach = pb.traceWidth / 2.0 + grid.slack;
      for (RoutedTrace e : pb.escapes) {
        int l = pb.layers.indexOf(e.layer());
        int id = pb.netIds.get(e.net());
        grid.forNearSegment(e.points().get(0), e.points().get(1), reach, c -> claim(l, c, id));
      }
      for (RoutedTrace e : pb.escapes) {
        traces.add(e);
        traceLength += e.lengthUm();
      }
      for (Fixed f : pb.fixed) {
        Integer id = pb.netIds.get(f.net());
        if (id == null) {
          continue;
        }
        double fr = f.width() / 2.0 + grid.slack;
        for (int k = 1; k < f.points().size(); k++) {
          grid.forNearSegment(
              f.points().get(k - 1), f.points().get(k), fr, c -> claim(f.layer(), c, id));
        }
      }
      this.obstacles = new ArrayList<>(pb.obstacles);
      this.wires = new ArrayList<>(pb.wires);
      pb.design.substratePads().forEach(p -> usedIds.add(p.id()));
      pb.design.wires().forEach(w -> usedIds.add(w.id()));
      this.states = layerCount * grid.cells * 9;
      this.costSoFar = new float[states];
      this.parent = new int[states];
      this.move = new byte[states];
      this.seen = new int[states];
      this.closed = new int[states];
    }

    void run() {
      for (String n : order) {
        routeNet(n);
      }
    }

    boolean isBetterThan(Attempt other) {
      if (unrouted.size() != other.unrouted.size()) {
        return unrouted.size() < other.unrouted.size();
      }
      if (addedWires.size() != other.addedWires.size()) {
        return addedWires.size() < other.addedWires.size();
      }
      return traceLength < other.traceLength;
    }

    RoutedHybrid result(BondAssigner.Result bonds) {
      HybridDesign d = pb.design;
      List<SubstratePad> pads = new ArrayList<>(d.substratePads());
      pads.addAll(addedPads);
      List<BondWire> allWires = new ArrayList<>(d.wires());
      allWires.addAll(addedWires);
      HybridDesign out =
          new HybridDesign(
              d.schemaVersion(),
              d.name(),
              d.substrate(),
              d.stack(),
              d.dies(),
              pads,
              allWires,
              d.components(),
              d.routing(),
              d.rules());
      List<UnroutedConnection> missing = new ArrayList<>(unrouted);
      for (String diePad : bonds.unplaced()) {
        String netName = netOfDiePad(d, diePad);
        missing.add(new UnroutedConnection(netName, diePad, "no legal bond finger position"));
      }
      int connections = pb.connectionCount() + bonds.unplaced().size();
      RouteStats stats =
          new RouteStats(
              connections,
              routed,
              Math.round(traceLength),
              windows.size(),
              addedWires.size(),
              bonds.fingersAdded(),
              grid.pitch);
      return new RoutedHybrid(out, traces, windows, missing, stats, List.of());
    }

    private static String netOfDiePad(HybridDesign d, String diePad) {
      int dot = diePad.indexOf('.');
      String die = diePad.substring(0, dot);
      String pad = diePad.substring(dot + 1);
      return d.die(die).flatMap(x -> x.pad(pad)).map(p -> p.net()).orElse("");
    }

    // --- per net ---------------------------------------------------------------------------

    void routeNet(String netName) {
      List<List<Terminal>> netGroups = pb.groups.get(netName);
      if (netGroups == null || netGroups.size() < 2) {
        return;
      }
      net = netName;
      netId = pb.netIds.get(netName);
      width = pb.widthOf(netName);
      traceClearance = (width / 2.0 + pb.space + grid.slack) * RoutingGrid.OCTILE_FACTOR;
      traceHardClearance = (width / 2.0 + grid.slack) * RoutingGrid.OCTILE_FACTOR;
      Integer ownWindow = pb.options.netWindows().get(netName);
      windowPad = ownWindow == null ? pb.windowPad : Math.max(pb.windowPad, ownWindow);
      double windowReach = windowPad * Math.sqrt(2) / 2;
      windowClearance = (windowReach + pb.space + grid.slack) * RoutingGrid.OCTILE_FACTOR;
      windowHardClearance = (windowReach + grid.slack) * RoutingGrid.OCTILE_FACTOR;
      foreign = new float[layerCount][];
      landCache = new byte[layerCount][grid.cells];
      windowCache = new byte[layerCount][grid.cells];
      own = new boolean[layerCount][grid.cells];
      tree = new boolean[layerCount][grid.cells];
      target = new int[layerCount][grid.cells];
      for (int l = 0; l < layerCount; l++) {
        final int[] o = owner[l];
        final int id = netId;
        foreign[l] = grid.distanceTransform(c -> o[c] == CONFLICT || (o[c] >= 0 && o[c] != id));
      }
      for (List<Terminal> group : netGroups) {
        for (Terminal t : group) {
          grid.forInterior(t.rect(), c -> own[t.layer()][c] = true);
        }
      }
      // This net's fixed copper: its own, and part of the group of the terminal it reaches.
      Map<List<Terminal>, List<Fixed>> fixedOf = new java.util.IdentityHashMap<>();
      for (Fixed f : pb.fixed) {
        if (!f.net().equals(netName)) {
          continue;
        }
        markFixed(f, own);
        for (List<Terminal> group : netGroups) {
          if (group.stream().anyMatch(t -> t.id().equals(f.joinId()))) {
            fixedOf.computeIfAbsent(group, g -> new ArrayList<>()).add(f);
            break;
          }
        }
      }
      for (Terminal t : netGroups.get(0)) {
        grid.forInterior(t.rect(), c -> tree[t.layer()][c] = true);
      }
      for (Fixed f : fixedOf.getOrDefault(netGroups.get(0), List.of())) {
        markFixed(f, tree);
      }
      String treeId = netGroups.get(0).get(0).id();
      List<List<Terminal>> remaining = new ArrayList<>(netGroups.subList(1, netGroups.size()));
      while (!remaining.isEmpty()) {
        for (int[] tg : target) {
          Arrays.fill(tg, -1);
        }
        for (int gi = 0; gi < remaining.size(); gi++) {
          final int index = gi;
          for (Terminal t : remaining.get(gi)) {
            grid.forInterior(t.rect(), c -> target[t.layer()][c] = index);
          }
          for (Fixed f : fixedOf.getOrDefault(remaining.get(gi), List.of())) {
            for (int k = 1; k < f.points().size(); k++) {
              grid.forNearSegment(
                  f.points().get(k - 1),
                  f.points().get(k),
                  grid.pitch * 0.75,
                  c -> target[f.layer()][c] = index);
            }
          }
        }
        int[] path = search(false);
        if (path == null && jumpersLeft() > 0) {
          path = search(true);
        }
        if (path == null) {
          for (List<Terminal> lost : remaining) {
            unrouted.add(new UnroutedConnection(netName, treeId, lost.get(0).id()));
          }
          failedNets.add(netName);
          return;
        }
        int end = path[path.length - 1];
        int reached = target[layer(end)][cell(end)];
        commit(path);
        for (Terminal t : remaining.get(reached)) {
          grid.forInterior(t.rect(), c -> tree[t.layer()][c] = true);
        }
        for (Fixed f : fixedOf.getOrDefault(remaining.get(reached), List.of())) {
          markFixed(f, tree);
        }
        remaining.remove(reached);
        routed++;
      }
    }

    /** Marks the cells along a fixed trace's centre-line. */
    private void markFixed(Fixed f, boolean[][] map) {
      for (int k = 1; k < f.points().size(); k++) {
        grid.forNearSegment(
            f.points().get(k - 1),
            f.points().get(k),
            grid.pitch * 0.75,
            c -> map[f.layer()][c] = true);
      }
    }

    int jumpersLeft() {
      HybridRoutingOptions o = pb.options;
      if (!o.jumpersAllowed()) {
        return 0;
      }
      if (o.maxJumpers() == 0) {
        return Integer.MAX_VALUE;
      }
      return Math.max(0, o.maxJumpers() - addedWires.size());
    }

    // --- search ----------------------------------------------------------------------------

    int state(int layer, int cell, int dir) {
      return (layer * grid.cells + cell) * 9 + dir;
    }

    int layer(int s) {
      return s / 9 / grid.cells;
    }

    int cell(int s) {
      return (s / 9) % grid.cells;
    }

    int dir(int s) {
      return s % 9;
    }

    /** A* from the tree to any target cell. Returns states from start to goal, or null. */
    int[] search(boolean jumperMode) {
      generation++;
      heap.clear();
      int minX = Integer.MAX_VALUE;
      int minY = Integer.MAX_VALUE;
      int maxX = Integer.MIN_VALUE;
      int maxY = Integer.MIN_VALUE;
      for (int l = 0; l < layerCount; l++) {
        for (int c = 0; c < grid.cells; c++) {
          if (target[l][c] >= 0) {
            PointUm p = grid.centre(c);
            minX = Math.min(minX, p.x());
            minY = Math.min(minY, p.y());
            maxX = Math.max(maxX, p.x());
            maxY = Math.max(maxY, p.y());
          }
        }
      }
      if (minX == Integer.MAX_VALUE) {
        return null;
      }
      RectUm goalBox = new RectUm(minX, minY, maxX, maxY);
      for (int l = 0; l < layerCount; l++) {
        for (int c = 0; c < grid.cells; c++) {
          if (tree[l][c]) {
            int s = state(l, c, NO_DIR);
            seen[s] = generation;
            costSoFar[s] = 0f;
            parent[s] = -1;
            move[s] = MOVE_START;
            heap.push((float) goalBox.distanceTo(grid.centre(c)), s);
          }
        }
      }
      double jumperCost = pb.options.jumperCost() * 1000.0;
      double windowCost = pb.options.crossoverCost() * 1000.0;
      int hopsAllowed = jumperMode ? jumpersLeft() : 0;
      while (!heap.isEmpty()) {
        int s = heap.pop();
        if (closed[s] == generation) {
          continue;
        }
        closed[s] = generation;
        int l = layer(s);
        int c = cell(s);
        int d = dir(s);
        if (target[l][c] >= 0) {
          int[] path = reconstruct(s);
          if (countHops(path) > hopsAllowed || !hopsCompatible(path)) {
            return null;
          }
          return path;
        }
        float gs = costSoFar[s];
        int ci = grid.col(c);
        int cj = grid.row(c);
        for (int nd = 0; nd < 8; nd++) {
          if (pb.options.orthogonal() && (nd & 1) == 1) {
            continue; // 90-degree routing: no diagonal steps
          }
          double bend = bendCost(d, nd);
          if (bend < 0) {
            continue;
          }
          int ni = ci + DX[nd];
          int nj = cj + DY[nd];
          if (!grid.inGrid(ni, nj)) {
            continue;
          }
          int n = grid.cell(ni, nj);
          if (!passable(l, n)) {
            continue;
          }
          if (DX[nd] != 0 && DY[nd] != 0) {
            if (!passable(l, grid.cell(ci + DX[nd], cj))
                || !passable(l, grid.cell(ci, cj + DY[nd]))) {
              continue;
            }
          }
          double step = (DX[nd] != 0 && DY[nd] != 0) ? grid.pitch * Math.sqrt(2) : grid.pitch;
          // A layer's preferred direction: a step across it costs more.
          List<String> prefer = pb.options.directions();
          String want = l < prefer.size() ? prefer.get(l) : "";
          if (("H".equalsIgnoreCase(want) && DY[nd] != 0)
              || ("V".equalsIgnoreCase(want) && DX[nd] != 0)) {
            step *= pb.options.againstDirectionCost();
          }
          relax(s, state(l, n, nd), gs + step + bend, MOVE_STEP, goalBox, n);
        }
        if (layerCount > 1) {
          if (l + 1 < layerCount && pb.windowUp[l] && windowFits(l, c) && windowFits(l + 1, c)) {
            relax(s, state(l + 1, c, d), gs + windowCost, MOVE_WINDOW, goalBox, c);
          }
          if (l > 0 && pb.windowUp[l - 1] && windowFits(l, c) && windowFits(l - 1, c)) {
            relax(s, state(l - 1, c, d), gs + windowCost, MOVE_WINDOW, goalBox, c);
          }
        }
        if (hopsAllowed > 0 && landFits(l, c) && !tooCloseToRecentLanding(s)) {
          for (int nd = 0; nd < 8; nd++) {
            double bend = bendCost(d, nd);
            if (bend < 0) {
              continue;
            }
            int landing = findLanding(l, c, nd);
            if (landing >= 0) {
              double length = grid.centre(c).distanceTo(grid.centre(landing));
              relax(
                  s,
                  state(l, landing, nd),
                  gs + jumperCost + length + bend,
                  MOVE_HOP,
                  goalBox,
                  landing);
            }
          }
        }
      }
      return null;
    }

    private void relax(int from, int to, double cost, byte how, RectUm goalBox, int cellTo) {
      if (closed[to] == generation) {
        return;
      }
      if (seen[to] == generation && costSoFar[to] <= cost) {
        return;
      }
      seen[to] = generation;
      costSoFar[to] = (float) cost;
      parent[to] = from;
      move[to] = how;
      heap.push((float) (cost + goalBox.distanceTo(grid.centre(cellTo))), to);
    }

    /**
     * Extra cost for changing direction: 0 straight, one pitch per 45°, sharper than 90° banned.
     */
    private double bendCost(int from, int to) {
      if (from == NO_DIR) {
        return 0;
      }
      int diff = Math.abs(from - to);
      int turn = Math.min(diff, 8 - diff);
      if (turn >= 3) {
        return -1;
      }
      return turn * (double) grid.pitch;
    }

    boolean passable(int l, int c) {
      if (own[l][c]) {
        return true;
      }
      if (pb.rawHard[l][c]
          || pb.hardDist[l][c] < traceHardClearance
          || foreign[l][c] < traceClearance) {
        return false;
      }
      return zoneAllows(l, c);
    }

    private boolean zoneAllows(int l, int c) {
      int z = pb.zone[l][c];
      return z < 0 || pb.zoneNets.get(z).contains(net);
    }

    /**
     * True if a square pad of {@code size} centred on the cell keeps every clearance. With {@code
     * strictZone} the pad may not touch any component keep-out ring, even of its own net.
     */
    private boolean padFits(
        int l, int c, int size, double hardClearance, double foreignClearance, boolean strictZone) {
      if (pb.rawHard[l][c]
          || pb.hardDist[l][c] < hardClearance
          || foreign[l][c] < foreignClearance) {
        return false;
      }
      int reach = (size / 2 + grid.pitch - 1) / grid.pitch;
      int ci = grid.col(c);
      int cj = grid.row(c);
      for (int dj = -reach; dj <= reach; dj++) {
        for (int di = -reach; di <= reach; di++) {
          if (!grid.inGrid(ci + di, cj + dj)) {
            continue;
          }
          int n = grid.cell(ci + di, cj + dj);
          if (strictZone ? pb.zone[l][n] >= 0 : !zoneAllows(l, n)) {
            return false;
          }
        }
      }
      return true;
    }

    boolean windowFits(int l, int c) {
      if (windowCache[l][c] == 0) {
        boolean fits =
            padFits(l, c, windowPad, windowHardClearance, windowClearance, false);
        windowCache[l][c] = (byte) (fits ? 1 : 2);
      }
      return windowCache[l][c] == 1;
    }

    /** Jumper lands keep out of every keep-out ring, like the pads HLayout supplies. */
    boolean landFits(int l, int c) {
      if (landCache[l][c] == 0) {
        // A land is bare through the whole stack: it must be clear on every conductor.
        boolean fits = true;
        for (int k = 0; k < layerCount && fits; k++) {
          fits = padFits(k, c, pb.landSize, pb.landHardClearance, pb.landClearance, true);
        }
        landCache[l][c] = (byte) (fits ? 1 : 2);
      }
      return landCache[l][c] == 1;
    }

    /**
     * True if a land at this cell would sit within capillary clearance of a jumper this path has
     * just landed: walks back along the path as far as that clearance can reach.
     */
    private boolean tooCloseToRecentLanding(int s) {
      int capillary = pb.rules.bondWire().capillaryClearanceUm();
      int maxBack = (capillary + pb.landSize) / grid.pitch + 2;
      RectUm land = RectUm.centred(grid.centre(cell(s)), pb.landSize, pb.landSize);
      int at = s;
      for (int k = 0; k <= maxBack && at >= 0; k++) {
        if (move[at] == MOVE_HOP) {
          return land.distanceTo(grid.centre(cell(at))) < capillary;
        }
        at = parent[at];
      }
      return false;
    }

    /**
     * Exact check of the hops within one path against each other: every jumper's second bond must
     * keep capillary clearance from the other jumpers' lands.
     */
    private boolean hopsCompatible(int[] path) {
      List<PointUm[]> hops = new ArrayList<>();
      for (int i = 1; i < path.length; i++) {
        if (move[path[i]] == MOVE_HOP) {
          hops.add(new PointUm[] {grid.centre(cell(path[i - 1])), grid.centre(cell(path[i]))});
        }
      }
      int capillary = pb.rules.bondWire().capillaryClearanceUm();
      for (int i = 0; i < hops.size(); i++) {
        for (int j = 0; j < hops.size(); j++) {
          if (i == j) {
            continue;
          }
          PointUm secondBond = hops.get(i)[1];
          for (PointUm end : hops.get(j)) {
            if (RectUm.centred(end, pb.landSize, pb.landSize).distanceTo(secondBond) < capillary) {
              return false;
            }
          }
        }
      }
      return true;
    }

    /**
     * Walks from a take-off cell in one direction over foreign copper and returns the first cell
     * past it where a jumper may land, or -1. The wire may fly over traces, pads and keep-out
     * rings, but not over a die, a component body or the substrate edge band.
     */
    int findLanding(int l, int from, int nd) {
      int min = pb.rules.jumper().minLengthUm();
      int max = pb.rules.jumper().maxLengthUm();
      double step = (DX[nd] != 0 && DY[nd] != 0) ? grid.pitch * Math.sqrt(2) : grid.pitch;
      int ci = grid.col(from);
      int cj = grid.row(from);
      boolean crossed = false;
      for (int k = 1; ; k++) {
        double length = k * step;
        if (length > max) {
          return -1;
        }
        int ni = ci + k * DX[nd];
        int nj = cj + k * DY[nd];
        if (!grid.inGrid(ni, nj)) {
          return -1;
        }
        int n = grid.cell(ni, nj);
        if (pb.rawHard[l][n] && !own[l][n]) {
          return -1;
        }
        if (!passable(l, n)) {
          crossed = true;
          continue;
        }
        if (!crossed) {
          // A jumper only takes off where the printed path is blocked straight ahead.
          return -1;
        }
        if (length >= min && landFits(l, n) && jumperClear(l, from, n)) {
          return n;
        }
      }
    }

    /** Exact checks for a jumper from take-off to landing: capillary room, wire crossings. */
    private boolean jumperClear(int l, int from, int to) {
      PointUm a = grid.centre(from);
      PointUm b = grid.centre(to);
      // A jumper never flies over a die or a part.
      for (RectUm r : pb.noFly) {
        if (Segments.toRect(a, b, r) <= 0) {
          return false;
        }
      }
      int capillary = pb.rules.bondWire().capillaryClearanceUm();
      for (RectUm r : obstacles) {
        if (r.distanceTo(b) < capillary) {
          return false;
        }
      }
      // Both new lands must also leave the capillary room at every existing second bond.
      RectUm landA = RectUm.centred(a, pb.landSize, pb.landSize);
      RectUm landB = RectUm.centred(b, pb.landSize, pb.landSize);
      for (WireSeg w : wires) {
        if (landA.distanceTo(w.b()) < capillary || landB.distanceTo(w.b()) < capillary) {
          return false;
        }
      }
      int diameter = WireSpec.DEFAULT_GOLD.diameterUm();
      for (WireSeg w : wires) {
        if (Objects.equals(w.net(), net)) {
          continue;
        }
        if (Segments.properlyIntersect(a, b, w.a(), w.b())) {
          return false;
        }
        double gap = Segments.distance(a, b, w.a(), w.b()) - (diameter + w.diameterUm()) / 2.0;
        double need = pb.rules.bondWire().spacingDiameters() * Math.max(diameter, w.diameterUm());
        if (gap < need) {
          return false;
        }
      }
      return true;
    }

    private int[] reconstruct(int goal) {
      int length = 0;
      for (int s = goal; s >= 0; s = parent[s]) {
        length++;
      }
      int[] path = new int[length];
      int i = length - 1;
      for (int s = goal; s >= 0; s = parent[s]) {
        path[i--] = s;
      }
      return path;
    }

    private int countHops(int[] path) {
      int hops = 0;
      for (int s : path) {
        if (move[s] == MOVE_HOP) {
          hops++;
        }
      }
      return hops;
    }

    // --- committing a path -----------------------------------------------------------------

    void commit(int[] path) {
      List<PointUm> points = new ArrayList<>();
      int first = path[0];
      Terminal startTerminal = terminalAt(layer(first), cell(first));
      if (startTerminal != null && !startTerminal.rect().contains(grid.centre(cell(first)))) {
        points.add(centreOf(startTerminal.rect()));
      }
      points.add(grid.centre(cell(first)));
      markPathCell(first);
      int currentLayer = layer(first);
      for (int i = 1; i < path.length; i++) {
        int s = path[i];
        int c = cell(s);
        PointUm p = grid.centre(c);
        switch (move[s]) {
          case MOVE_WINDOW -> {
            finishTrace(currentLayer, points);
            int lo = Math.min(currentLayer, layer(s));
            int hi = Math.max(currentLayer, layer(s));
            windows.add(
                new CrossoverWindow(
                    net,
                    p,
                    pb.windowOpening + (windowPad - pb.windowPad),
                    windowPad,
                    pb.layers.get(lo),
                    pb.layers.get(hi)));
            RectUm padRect = RectUm.centred(p, windowPad, windowPad);
            for (int l : new int[] {lo, hi}) {
              rasterRect(l, padRect, netId);
              markOwnInterior(l, padRect);
            }
            currentLayer = layer(s);
            points = new ArrayList<>();
            points.add(p);
          }
          case MOVE_HOP -> {
            finishTrace(currentLayer, points);
            addJumper(currentLayer, grid.centre(cell(path[i - 1])), p);
            points = new ArrayList<>();
            points.add(p);
          }
          default -> points.add(p);
        }
        markPathCell(s);
      }
      int last = path[path.length - 1];
      Terminal endTerminal = terminalAt(layer(last), cell(last));
      if (endTerminal != null && !endTerminal.rect().contains(grid.centre(cell(last)))) {
        points.add(centreOf(endTerminal.rect()));
      }
      finishTrace(currentLayer, points);
    }

    private void addJumper(int l, PointUm a, PointUm b) {
      int n = addedWires.size() + 1;
      String landA = BondAssigner.uniqueId("JL_" + n + "A", usedIds);
      usedIds.add(landA);
      String landB = BondAssigner.uniqueId("JL_" + n + "B", usedIds);
      usedIds.add(landB);
      String wireId = BondAssigner.uniqueId("WJ_" + n, usedIds);
      usedIds.add(wireId);
      String layerName = pb.layers.get(l);
      int size = pb.landSize;
      SubstratePad padA =
          new SubstratePad(landA, net, SubstratePadRole.JUMPER_LAND, a, size, size, layerName);
      SubstratePad padB =
          new SubstratePad(landB, net, SubstratePadRole.JUMPER_LAND, b, size, size, layerName);
      addedPads.add(padA);
      addedPads.add(padB);
      int loop =
          Math.max(WireSpec.DEFAULT_GOLD.loopHeightUm(), pb.rules.jumper().minLoopHeightUm());
      WireSpec spec = new WireSpec(WireMaterial.GOLD, 25, BondType.BALL_WEDGE, loop);
      addedWires.add(
          new BondWire(
              wireId,
              net,
              BondEndpoint.substratePad(landA),
              BondEndpoint.substratePad(landB),
              spec,
              false));
      for (SubstratePad pad : List.of(padA, padB)) {
        for (int k = 0; k < layerCount; k++) {
          rasterRect(k, pad.bounds(), netId);
        }
        markOwnInterior(l, pad.bounds());
        obstacles.add(pad.bounds());
      }
      wires.add(new WireSeg(net, a, b, 25));
    }

    private void finishTrace(int l, List<PointUm> raw) {
      List<PointUm> pts = simplify(raw);
      if (pts.size() < 2) {
        return;
      }
      RoutedTrace trace = new RoutedTrace(net, pb.layers.get(l), pts, width);
      traces.add(trace);
      traceLength += trace.lengthUm();
      double reach = width / 2.0 + grid.slack;
      for (int i = 1; i < pts.size(); i++) {
        grid.forNearSegment(pts.get(i - 1), pts.get(i), reach, c -> claim(l, c, netId));
      }
    }

    private void markPathCell(int s) {
      own[layer(s)][cell(s)] = true;
      tree[layer(s)][cell(s)] = true;
    }

    private void markOwnInterior(int l, RectUm r) {
      grid.forInterior(
          r,
          c -> {
            own[l][c] = true;
            tree[l][c] = true;
          });
    }

    private void rasterRect(int l, RectUm r, int id) {
      grid.forOverlapping(r, c -> claim(l, c, id));
    }

    private void claim(int l, int c, int id) {
      int o = owner[l][c];
      if (o == FREE) {
        owner[l][c] = id;
      } else if (o != id) {
        owner[l][c] = CONFLICT;
      }
    }

    private Terminal terminalAt(int l, int c) {
      PointUm p = grid.centre(c);
      for (List<Terminal> group : pb.groups.get(net)) {
        for (Terminal t : group) {
          if (t.layer() == l && (t.rect().contains(p) || grid.cellAt(centreOf(t.rect())) == c)) {
            return t;
          }
        }
      }
      return null;
    }
  }

  private static PointUm centreOf(RectUm r) {
    return new PointUm((r.minX() + r.maxX()) / 2, (r.minY() + r.maxY()) / 2);
  }

  /** Drops repeated points and the middle point of any straight run. */
  static List<PointUm> simplify(List<PointUm> raw) {
    List<PointUm> out = new ArrayList<>();
    for (PointUm p : raw) {
      if (!out.isEmpty() && out.get(out.size() - 1).equals(p)) {
        continue;
      }
      while (out.size() >= 2 && collinear(out.get(out.size() - 2), out.get(out.size() - 1), p)) {
        out.remove(out.size() - 1);
      }
      out.add(p);
    }
    return out;
  }

  private static boolean collinear(PointUm a, PointUm b, PointUm c) {
    long cross =
        ((long) b.x() - a.x()) * ((long) c.y() - a.y())
            - ((long) b.y() - a.y()) * ((long) c.x() - a.x());
    if (cross != 0) {
      return false;
    }
    long dot =
        ((long) b.x() - a.x()) * ((long) c.x() - b.x())
            + ((long) b.y() - a.y()) * ((long) c.y() - b.y());
    return dot >= 0;
  }

  /** Binary min-heap of states keyed by f-score; ties go to the lower state number. */
  private static final class Heap {
    private float[] keys = new float[1024];
    private int[] vals = new int[1024];
    private int size;

    void clear() {
      size = 0;
    }

    boolean isEmpty() {
      return size == 0;
    }

    void push(float key, int val) {
      if (size == keys.length) {
        keys = Arrays.copyOf(keys, size * 2);
        vals = Arrays.copyOf(vals, size * 2);
      }
      int i = size++;
      while (i > 0) {
        int p = (i - 1) / 2;
        if (!less(key, val, keys[p], vals[p])) {
          break;
        }
        keys[i] = keys[p];
        vals[i] = vals[p];
        i = p;
      }
      keys[i] = key;
      vals[i] = val;
    }

    int pop() {
      final int top = vals[0];
      float key = keys[--size];
      int val = vals[size];
      int i = 0;
      while (true) {
        int l = 2 * i + 1;
        if (l >= size) {
          break;
        }
        int r = l + 1;
        int m = (r < size && less(keys[r], vals[r], keys[l], vals[l])) ? r : l;
        if (!less(keys[m], vals[m], key, val)) {
          break;
        }
        keys[i] = keys[m];
        vals[i] = vals[m];
        i = m;
      }
      keys[i] = key;
      vals[i] = val;
      return top;
    }

    private static boolean less(float ka, int va, float kb, int vb) {
      return ka < kb || (ka == kb && va < vb);
    }
  }
}
