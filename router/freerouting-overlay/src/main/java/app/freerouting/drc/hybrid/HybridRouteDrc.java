package app.freerouting.drc.hybrid;

import app.freerouting.board.hybrid.BondEndpoint;
import app.freerouting.board.hybrid.BondWire;
import app.freerouting.board.hybrid.ComponentPin;
import app.freerouting.board.hybrid.CrossoverWindow;
import app.freerouting.board.hybrid.Die;
import app.freerouting.board.hybrid.HybridDesign;
import app.freerouting.board.hybrid.HybridLayerKind;
import app.freerouting.board.hybrid.PointUm;
import app.freerouting.board.hybrid.RectUm;
import app.freerouting.board.hybrid.RoutedHybrid;
import app.freerouting.board.hybrid.RoutedTrace;
import app.freerouting.board.hybrid.Segments;
import app.freerouting.board.hybrid.Substrate;
import app.freerouting.board.hybrid.SubstratePad;
import app.freerouting.board.hybrid.TwoPinComponent;
import app.freerouting.board.hybrid.UnroutedConnection;
import app.freerouting.board.hybrid.ValidationIssue;
import app.freerouting.board.hybrid.ValidationIssue.Severity;
import app.freerouting.rules.hybrid.ConductorRules;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.TreeMap;

/**
 * Post-route design-rule check of a {@link RoutedHybrid}, in exact geometry.
 *
 * <p>Runs the pre-route {@link HybridDrc} on the routed design (so added fingers, jumper lands and
 * jumper wires are checked too), then checks every trace and crossover window: width, spacing to
 * other nets, substrate edge, die and component keep-outs, window size, enclosure and layers. It
 * finishes with an independent connectivity check: every net must form one piece of copper (wires
 * included) unless the router reported it unrouted.
 */
public final class HybridRouteDrc {

  private static final double EPS = 1e-6;

  private final RoutedHybrid routed;
  private final HybridDesign design;
  private final ConductorRules conductor;
  private final List<ValidationIssue> issues = new ArrayList<>();
  private final List<Rect> rects = new ArrayList<>();
  private final List<Seg> segs = new ArrayList<>();
  private final Map<Integer, Integer> fixedSegOf = new HashMap<>();
  private static final String FIXED = "fixed#";

  private HybridRouteDrc(RoutedHybrid routed) {
    this.routed = routed;
    this.design = routed.design();
    this.conductor = design.rules().conductor();
  }

  /** Checks the routed result and returns every violation found. */
  public static List<ValidationIssue> check(RoutedHybrid routed) {
    HybridRouteDrc drc = new HybridRouteDrc(Objects.requireNonNull(routed, "routed"));
    drc.issues.addAll(HybridDrc.check(routed.design()));
    drc.collect();
    drc.checkTraces();
    drc.checkWindows();
    drc.checkConnectivity();
    for (UnroutedConnection u : routed.unrouted()) {
      drc.error("UNROUTED", u.net(), u.from() + " to " + u.to());
    }
    return List.copyOf(drc.issues);
  }

  /** A rectangular piece of copper on one layer. */
  private record Rect(String ref, String net, String layer, RectUm rect) {}

  /** One straight piece of a trace. */
  private record Seg(String ref, String net, String layer, PointUm a, PointUm b, int width) {}

  private void collect() {
    for (SubstratePad pad : design.substratePads()) {
      rects.add(new Rect(pad.id(), pad.net(), pad.layer(), pad.bounds()));
    }
    for (TwoPinComponent comp : design.components()) {
      addPin(comp, comp.pinA(), "A");
      addPin(comp, comp.pinB(), "B");
    }
    for (CrossoverWindow w : routed.windows()) {
      String ref = "window@" + w.position().x() + "," + w.position().y();
      rects.add(new Rect(ref, w.net(), w.fromLayer(), w.padBounds()));
      rects.add(new Rect(ref, w.net(), w.toLayer(), w.padBounds()));
    }
    List<RoutedTrace> traces = routed.traces();
    for (int t = 0; t < traces.size(); t++) {
      RoutedTrace trace = traces.get(t);
      List<PointUm> p = trace.points();
      for (int i = 1; i < p.size(); i++) {
        String ref = trace.net() + "/" + trace.layer() + "#" + t;
        segs.add(new Seg(ref, trace.net(), trace.layer(), p.get(i - 1), p.get(i), trace.widthUm()));
      }
    }
    // Existing traces the router left as they were: checked against what it added, and part of
    // the connectivity.
    List<RoutedTrace> fixed = design.routing().fixedTraces();
    for (int t = 0; t < fixed.size(); t++) {
      RoutedTrace trace = fixed.get(t);
      List<PointUm> p = trace.points();
      for (int i = 1; i < p.size(); i++) {
        fixedSegOf.put(segs.size(), t);
        segs.add(
            new Seg(
                FIXED + t, trace.net(), trace.layer(), p.get(i - 1), p.get(i), trace.widthUm()));
      }
    }
  }

  private void addPin(TwoPinComponent comp, ComponentPin pin, String name) {
    if (pin != null) {
      rects.add(new Rect(comp.refdes() + "." + name, pin.net(), pin.layer(), comp.pinBounds(pin)));
    }
  }

  // --- traces ---------------------------------------------------------------------------------

  private void checkTraces() {
    for (Seg s : segs) {
      if (s.ref().startsWith(FIXED)) {
        continue; // existing copper is HLayout's to check
      }
      if (s.width() < conductor.minLineUm()) {
        error("TRACE_WIDTH", s.ref(), "width " + s.width() + " < " + conductor.minLineUm() + " µm");
      }
      double edge = edgeGap(s.a(), s.b()) - s.width() / 2.0;
      if (edge < conductor.edgeClearanceUm() - EPS) {
        error("TRACE_EDGE_CLEARANCE", s.ref(), "edge gap " + um(edge) + " µm");
      }
      for (Rect r : rects) {
        if (r.layer().equals(s.layer()) && differentNets(r.net(), s.net())) {
          double gap = Segments.toRect(s.a(), s.b(), r.rect()) - s.width() / 2.0;
          if (gap < conductor.minSpaceUm() - EPS) {
            error("TRACE_SPACING", s.ref() + "/" + r.ref(), gapText(gap));
          }
        }
      }
      for (Die die : design.dies()) {
        RectUm zone = die.bounds().grow(die.attachKeepoutUm());
        if (Segments.toRect(s.a(), s.b(), zone) < s.width() / 2.0 - EPS) {
          error("TRACE_DIE_KEEPOUT", s.ref(), "enters the attach keep-out of " + die.refdes());
        }
      }
      for (TwoPinComponent comp : design.components()) {
        checkTraceAgainstComponent(s, comp);
      }
    }
    for (int i = 0; i < segs.size(); i++) {
      Seg a = segs.get(i);
      for (int j = i + 1; j < segs.size(); j++) {
        Seg b = segs.get(j);
        if (!a.layer().equals(b.layer()) || !differentNets(a.net(), b.net())) {
          continue;
        }
        if (a.ref().startsWith(FIXED) && b.ref().startsWith(FIXED)) {
          continue;
        }
        double gap = Segments.distance(a.a(), a.b(), b.a(), b.b()) - (a.width() + b.width()) / 2.0;
        if (gap < conductor.minSpaceUm() - EPS) {
          error("TRACE_SPACING", a.ref() + "/" + b.ref(), gapText(gap));
        }
      }
    }
  }

  private void checkTraceAgainstComponent(Seg s, TwoPinComponent comp) {
    if (!comp.obstacleLayers().contains(s.layer())) {
      return;
    }
    boolean pinNet = false;
    RectUm body = comp.bodyBounds();
    for (ComponentPin pin : comp.pins()) {
      if (Objects.equals(pin.net(), s.net())) {
        pinNet = true;
        body = trim(body, comp.pinBounds(pin));
      }
    }
    if (pinNet) {
      if (body != null && Segments.toRect(s.a(), s.b(), body) < s.width() / 2.0 - EPS) {
        error("TRACE_COMPONENT_BODY", s.ref(), "crosses the body of " + comp.refdes());
      }
      return;
    }
    RectUm zone = comp.bodyBounds().grow(comp.keepoutUm());
    if (Segments.toRect(s.a(), s.b(), zone) < s.width() / 2.0 - EPS) {
      error("TRACE_COMPONENT_KEEPOUT", s.ref(), "enters the keep-out of " + comp.refdes());
    }
  }

  /**
   * The body with the part under one of its own pins cut away, so a pin's own trace may reach the
   * termination. Null if the pin covers the whole body.
   */
  private static RectUm trim(RectUm body, RectUm pin) {
    if (body == null || !overlaps(body, pin)) {
      return body;
    }
    int minX = body.minX();
    int maxX = body.maxX();
    int minY = body.minY();
    int maxY = body.maxY();
    boolean spansY = pin.minY() <= minY && pin.maxY() >= maxY;
    boolean spansX = pin.minX() <= minX && pin.maxX() >= maxX;
    if (spansY && pin.minX() <= minX) {
      minX = Math.min(maxX, pin.maxX());
    } else if (spansY && pin.maxX() >= maxX) {
      maxX = Math.max(minX, pin.minX());
    } else if (spansX && pin.minY() <= minY) {
      minY = Math.min(maxY, pin.maxY());
    } else if (spansX && pin.maxY() >= maxY) {
      maxY = Math.max(minY, pin.minY());
    } else {
      return body;
    }
    if (minX >= maxX || minY >= maxY) {
      return null;
    }
    return new RectUm(minX, minY, maxX, maxY);
  }

  // --- windows --------------------------------------------------------------------------------

  private void checkWindows() {
    List<CrossoverWindow> windows = routed.windows();
    for (int i = 0; i < windows.size(); i++) {
      CrossoverWindow w = windows.get(i);
      String ref = "window@" + w.position().x() + "," + w.position().y();
      if (w.openingUm() < conductor.windowMinUm()) {
        error("WINDOW_SIZE", ref, "opening " + w.openingUm() + " < " + conductor.windowMinUm());
      }
      if ((w.padUm() - w.openingUm()) / 2 < conductor.windowEnclosureUm()) {
        error("WINDOW_ENCLOSURE", ref, "enclosure below " + conductor.windowEnclosureUm() + " µm");
      }
      boolean conductors =
          design.stack().isKind(w.fromLayer(), HybridLayerKind.CONDUCTOR)
              && design.stack().isKind(w.toLayer(), HybridLayerKind.CONDUCTOR);
      if (!conductors || !design.stack().hasDielectricBetween(w.fromLayer(), w.toLayer())) {
        error(
            "WINDOW_LAYERS",
            ref,
            "needs two conductor layers with dielectric between, got "
                + w.fromLayer()
                + "/"
                + w.toLayer());
      }
      RectUm pad = w.padBounds();
      double edge = edgeGapRect(pad);
      if (edge < conductor.edgeClearanceUm() - EPS) {
        error("WINDOW_EDGE_CLEARANCE", ref, "edge gap " + um(edge) + " µm");
      }
      for (Rect r : rects) {
        boolean sameLayer = r.layer().equals(w.fromLayer()) || r.layer().equals(w.toLayer());
        if (sameLayer && differentNets(r.net(), w.net()) && !r.ref().equals(ref)) {
          double gap = pad.gap(r.rect());
          if (gap < conductor.minSpaceUm() - EPS) {
            error("WINDOW_SPACING", ref + "/" + r.ref(), gapText(gap));
          }
        }
      }
      for (Die die : design.dies()) {
        if (overlaps(die.bounds().grow(die.attachKeepoutUm()), pad)) {
          error("WINDOW_DIE_KEEPOUT", ref, "inside the attach keep-out of " + die.refdes());
        }
      }
      for (TwoPinComponent comp : design.components()) {
        boolean onLayer =
            comp.obstacleLayers().contains(w.fromLayer())
                || comp.obstacleLayers().contains(w.toLayer());
        if (!onLayer) {
          continue;
        }
        boolean pinNet = comp.pins().stream().anyMatch(p -> Objects.equals(p.net(), w.net()));
        if (overlaps(comp.bodyBounds(), pad)) {
          error("WINDOW_COMPONENT_BODY", ref, "on the body of " + comp.refdes());
        } else if (!pinNet && overlaps(comp.bodyBounds().grow(comp.keepoutUm()), pad)) {
          error("WINDOW_COMPONENT_KEEPOUT", ref, "inside the keep-out of " + comp.refdes());
        }
      }
    }
  }

  // --- connectivity ---------------------------------------------------------------------------

  private void checkConnectivity() {
    Map<String, String> parent = new HashMap<>();
    // Copper that touches on a layer is joined.
    List<String> nodes = new ArrayList<>();
    Map<String, Rect> rectNode = new TreeMap<>();
    for (Rect r : rects) {
      String key = "R:" + r.ref() + "@" + r.layer();
      rectNode.put(key, r);
      nodes.add(key);
    }
    for (int i = 0; i < segs.size(); i++) {
      nodes.add("S:" + i);
    }
    for (String n : nodes) {
      parent.put(n, n);
    }
    List<String> rectKeys = new ArrayList<>(rectNode.keySet());
    for (int i = 0; i < segs.size(); i++) {
      Seg s = segs.get(i);
      for (String key : rectKeys) {
        Rect r = rectNode.get(key);
        if (r.layer().equals(s.layer())
            && Objects.equals(r.net(), s.net())
            && Segments.toRect(s.a(), s.b(), r.rect()) <= EPS) {
          union(parent, "S:" + i, key);
        }
      }
      for (int j = i + 1; j < segs.size(); j++) {
        Seg t = segs.get(j);
        // Joined when one centre-line runs inside the other's copper.
        double overlap = Math.max(s.width(), t.width()) / 2.0;
        if (t.layer().equals(s.layer())
            && Objects.equals(t.net(), s.net())
            && Segments.distance(s.a(), s.b(), t.a(), t.b()) <= overlap + EPS) {
          union(parent, "S:" + i, "S:" + j);
        }
      }
    }
    for (int i = 0; i < rectKeys.size(); i++) {
      Rect a = rectNode.get(rectKeys.get(i));
      for (int j = i + 1; j < rectKeys.size(); j++) {
        Rect b = rectNode.get(rectKeys.get(j));
        boolean sameWindow = a.ref().equals(b.ref()) && a.ref().startsWith("window@");
        boolean touching =
            a.layer().equals(b.layer())
                && Objects.equals(a.net(), b.net())
                && a.rect().gap(b.rect()) <= EPS;
        if (sameWindow || touching) {
          union(parent, rectKeys.get(i), rectKeys.get(j));
        }
      }
    }
    // Existing copper: a fixed trace reaches its terminal, and listed terminals are joined.
    List<String> joins = design.routing().fixedJoins();
    for (Map.Entry<Integer, Integer> e : fixedSegOf.entrySet()) {
      String join = e.getValue() < joins.size() ? joins.get(e.getValue()) : "";
      if (join.isEmpty()) {
        continue;
      }
      for (String key : rectKeys) {
        if (key.startsWith("R:" + join + "@")) {
          union(parent, "S:" + e.getKey(), key);
        }
      }
    }
    for (List<String> group : design.routing().connected()) {
      String first = null;
      for (String id : group) {
        for (String key : rectKeys) {
          if (key.startsWith("R:" + id + "@")) {
            if (first == null) {
              first = key;
            } else {
              union(parent, first, key);
            }
          }
        }
      }
    }
    // Wires join their two ends; a die pad is a node of its own.
    for (BondWire w : design.wires()) {
      String a = wireNode(w.from(), rectKeys, rectNode);
      String b = wireNode(w.to(), rectKeys, rectNode);
      if (a != null && b != null) {
        parent.putIfAbsent(a, a);
        parent.putIfAbsent(b, b);
        union(parent, a, b);
      }
    }
    Set<String> reported = new HashSet<>();
    routed.unrouted().forEach(u -> reported.add(u.net()));
    Map<String, Set<String>> pieces = new TreeMap<>();
    for (String key : rectKeys) {
      Rect r = rectNode.get(key);
      if (r.net() == null || r.net().isBlank() || r.ref().startsWith("window@")) {
        continue;
      }
      if (!design.routingLayers().contains(r.layer())) {
        continue;
      }
      pieces.computeIfAbsent(r.net(), n -> new HashSet<>()).add(find(parent, key));
    }
    for (Map.Entry<String, Set<String>> e : pieces.entrySet()) {
      if (e.getValue().size() > 1 && !reported.contains(e.getKey())) {
        error("NET_OPEN", e.getKey(), e.getValue().size() + " unconnected pieces, none reported");
      }
    }
  }

  private static String wireNode(
      BondEndpoint end, List<String> rectKeys, Map<String, Rect> rectNode) {
    if (end == null || end.pad() == null) {
      return null;
    }
    if (end.isDiePad()) {
      return "D:" + end.die() + "." + end.pad();
    }
    for (String key : rectKeys) {
      if (rectNode.get(key).ref().equals(end.pad())) {
        return key;
      }
    }
    return null;
  }

  private static String find(Map<String, String> parent, String k) {
    String p = parent.getOrDefault(k, k);
    if (p.equals(k)) {
      return k;
    }
    String root = find(parent, p);
    parent.put(k, root);
    return root;
  }

  private static void union(Map<String, String> parent, String a, String b) {
    String ra = find(parent, a);
    String rb = find(parent, b);
    if (!ra.equals(rb)) {
      parent.put(ra.compareTo(rb) < 0 ? rb : ra, ra.compareTo(rb) < 0 ? ra : rb);
    }
  }

  // --- helpers --------------------------------------------------------------------------------

  private double edgeGap(PointUm a, PointUm b) {
    Substrate s = design.substrate();
    if (s == null) {
      return Double.MAX_VALUE;
    }
    int minX = Math.min(a.x(), b.x());
    int minY = Math.min(a.y(), b.y());
    int maxX = Math.max(a.x(), b.x());
    int maxY = Math.max(a.y(), b.y());
    return Math.min(Math.min(minX, minY), Math.min(s.widthUm() - maxX, s.heightUm() - maxY));
  }

  private double edgeGapRect(RectUm r) {
    Substrate s = design.substrate();
    if (s == null) {
      return Double.MAX_VALUE;
    }
    return Math.min(
        Math.min(r.minX(), r.minY()), Math.min(s.widthUm() - r.maxX(), s.heightUm() - r.maxY()));
  }

  private static boolean overlaps(RectUm a, RectUm b) {
    return a.minX() < b.maxX() && b.minX() < a.maxX() && a.minY() < b.maxY() && b.minY() < a.maxY();
  }

  private static boolean differentNets(String a, String b) {
    return a != null && b != null && !a.isBlank() && !b.isBlank() && !a.equals(b);
  }

  private String gapText(double gap) {
    return gap <= 0
        ? "copper touches (short)"
        : "gap " + um(gap) + " < " + conductor.minSpaceUm() + " µm";
  }

  private static String um(double v) {
    return String.format(Locale.ROOT, "%.0f", v);
  }

  private void error(String code, String ref, String message) {
    issues.add(new ValidationIssue(Severity.ERROR, code, ref, message));
  }
}
