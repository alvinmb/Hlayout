package app.freerouting.drc.hybrid;

import app.freerouting.board.hybrid.BondEndpoint;
import app.freerouting.board.hybrid.BondPad;
import app.freerouting.board.hybrid.BondWire;
import app.freerouting.board.hybrid.ComponentPin;
import app.freerouting.board.hybrid.Die;
import app.freerouting.board.hybrid.HybridDesign;
import app.freerouting.board.hybrid.PointUm;
import app.freerouting.board.hybrid.RectUm;
import app.freerouting.board.hybrid.Segments;
import app.freerouting.board.hybrid.Substrate;
import app.freerouting.board.hybrid.SubstratePad;
import app.freerouting.board.hybrid.SubstratePadRole;
import app.freerouting.board.hybrid.TwoPinComponent;
import app.freerouting.board.hybrid.ValidationIssue;
import app.freerouting.board.hybrid.ValidationIssue.Severity;
import app.freerouting.board.hybrid.WirePurpose;
import app.freerouting.rules.hybrid.BondWireRules;
import app.freerouting.rules.hybrid.ConductorRules;
import app.freerouting.rules.hybrid.JumperRules;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import java.util.Optional;

/**
 * Pre-route design-rule check for a chip-and-wire hybrid, against {@link HybridDesign#rules()}.
 *
 * <p>Checks what exists before routing: printed pads and component pins (spacing, substrate edge,
 * die and component keep-outs), bond wires and jumpers (length, departure angle, spacing,
 * crossings, loop height), bond fingers (pitch, distance to die, capillary clearance). Traces and
 * windows are checked after routing, in Phase 4.
 *
 * <p>Run {@code HybridValidator} first; items that do not resolve are skipped here. Every finding
 * is an {@link Severity#ERROR}. Results are deterministic: same input, same issues, same order.
 */
public final class HybridDrc {

  private final HybridDesign design;
  private final ConductorRules conductor;
  private final BondWireRules bond;
  private final JumperRules jumper;
  private final List<Copper> copper;
  private final List<Obstacle> obstacles;
  private final List<ValidationIssue> issues = new ArrayList<>();

  private HybridDrc(HybridDesign design) {
    this.design = design;
    this.conductor = design.rules().conductor();
    this.bond = design.rules().bondWire();
    this.jumper = design.rules().jumper();
    this.copper = collectCopper(design);
    this.obstacles = collectObstacles(design);
  }

  /** Checks the design against its rules and returns every violation found. */
  public static List<ValidationIssue> check(HybridDesign design) {
    HybridDrc drc = new HybridDrc(Objects.requireNonNull(design, "design"));
    drc.run();
    return List.copyOf(drc.issues);
  }

  private void run() {
    checkEdgeClearance();
    checkConductorSpacing();
    checkKeepouts();
    List<Wire> wires = resolveWires();
    for (Wire wire : wires) {
      checkWire(wire);
    }
    checkWirePairs(wires);
    checkFingerPitch();
  }

  // --- printed copper ---------------------------------------------------------------------------

  private void checkEdgeClearance() {
    Substrate s = design.substrate();
    if (s == null) {
      return;
    }
    for (Copper c : copper) {
      RectUm r = c.rect();
      int gap =
          Math.min(
              Math.min(r.minX(), r.minY()),
              Math.min(s.widthUm() - r.maxX(), s.heightUm() - r.maxY()));
      if (gap < conductor.edgeClearanceUm()) {
        error(
            "EDGE_CLEARANCE",
            c.ref(),
            "gap to substrate edge " + gap + " µm < " + conductor.edgeClearanceUm() + " µm");
      }
    }
  }

  private void checkConductorSpacing() {
    for (int i = 0; i < copper.size(); i++) {
      Copper a = copper.get(i);
      for (int j = i + 1; j < copper.size(); j++) {
        Copper b = copper.get(j);
        if (!a.layer().equals(b.layer()) || !differentNets(a.net(), b.net())) {
          continue;
        }
        double gap = a.rect().gap(b.rect());
        if (gap < conductor.minSpaceUm()) {
          String what =
              gap == 0
                  ? "touch (short " + a.net() + "/" + b.net() + ")"
                  : "gap " + um(gap) + " < " + conductor.minSpaceUm() + " µm";
          error("CONDUCTOR_SPACING", a.ref() + "/" + b.ref(), what);
        }
      }
    }
  }

  private void checkKeepouts() {
    for (Die die : design.dies()) {
      RectUm zone = die.bounds().grow(die.attachKeepoutUm());
      for (Copper c : copper) {
        if (overlapsInside(zone, c.rect())) {
          error("DIE_KEEPOUT", c.ref(), "inside the attach keep-out of " + die.refdes());
        }
      }
    }
    for (TwoPinComponent comp : design.components()) {
      RectUm zone = comp.bodyBounds().grow(comp.keepoutUm());
      for (Copper c : copper) {
        if (Objects.equals(c.owner(), comp.refdes())
            || !comp.obstacleLayers().contains(c.layer())) {
          continue;
        }
        if (overlapsInside(zone, c.rect())) {
          error("COMPONENT_KEEPOUT", c.ref(), "inside the body keep-out of " + comp.refdes());
        }
      }
    }
  }

  /** True if the rectangles share area, not merely an edge. */
  private static boolean overlapsInside(RectUm a, RectUm b) {
    return a.minX() < b.maxX() && b.minX() < a.maxX() && a.minY() < b.maxY() && b.minY() < a.maxY();
  }

  // --- single wires
  // -------------------------------------------------------------------------------

  private void checkWire(Wire w) {
    double length = w.a().distanceTo(w.b());
    if (w.purpose() == WirePurpose.JUMPER) {
      checkRange("JUMPER", w.id(), length, jumper.minLengthUm(), jumper.maxLengthUm());
      if (w.loopHeightUm() < jumper.minLoopHeightUm()) {
        error(
            "JUMPER_LOOP_HEIGHT",
            w.id(),
            "loop height " + w.loopHeightUm() + " µm < " + jumper.minLoopHeightUm() + " µm");
      }
    } else {
      checkRange("WIRE", w.id(), length, bond.minLengthUm(), bond.maxLengthUm());
    }
    checkDepartureAngle(w, w.source().from(), w.a(), w.b());
    checkDepartureAngle(w, w.source().to(), w.b(), w.a());
    if (w.purpose() == WirePurpose.DIE_BOND) {
      checkDieToFinger(w);
    }
    if (!w.source().to().isDiePad()) {
      checkCapillary(w, w.source().to().pad(), w.b());
    }
  }

  private void checkRange(String prefix, String ref, double length, int min, int max) {
    if (length < min) {
      error(prefix + "_TOO_SHORT", ref, "length " + um(length) + " < " + min + " µm");
    } else if (length > max) {
      error(prefix + "_TOO_LONG", ref, "length " + um(length) + " > " + max + " µm");
    }
  }

  private void checkDepartureAngle(Wire w, BondEndpoint end, PointUm at, PointUm toward) {
    if (!end.isDiePad()) {
      return;
    }
    Optional<Die> die = design.die(end.die());
    Optional<BondPad> pad = die.flatMap(d -> d.pad(end.pad()));
    if (pad.isEmpty()) {
      return;
    }
    PointUm normal = die.get().padOutwardNormal(pad.get());
    double dx = (double) toward.x() - at.x();
    double dy = (double) toward.y() - at.y();
    double len = Math.hypot(dx, dy);
    if (len == 0) {
      return;
    }
    double cos = (dx * normal.x() + dy * normal.y()) / len;
    double angle = Math.toDegrees(Math.acos(Math.max(-1.0, Math.min(1.0, cos))));
    if (angle > bond.maxAngleDeg() + 1e-9) {
      error(
          "WIRE_ANGLE",
          w.id(),
          "leaves "
              + end
              + " at "
              + um(angle)
              + "° off the pad axis (max "
              + um(bond.maxAngleDeg())
              + "°)");
    }
  }

  private void checkDieToFinger(Wire w) {
    BondEndpoint dieEnd = w.source().from().isDiePad() ? w.source().from() : w.source().to();
    PointUm finger = w.source().from().isDiePad() ? w.b() : w.a();
    Optional<Die> die = design.die(dieEnd.die());
    if (die.isEmpty()) {
      return;
    }
    double distance = die.get().bounds().distanceTo(finger);
    String ref = w.id() + " (" + die.get().refdes() + ")";
    if (distance < bond.dieToFingerMinUm()) {
      error(
          "DIE_TO_FINGER_MIN",
          ref,
          "finger " + um(distance) + " µm from die < " + bond.dieToFingerMinUm() + " µm");
    } else if (distance > bond.dieToFingerMaxUm()) {
      error(
          "DIE_TO_FINGER_MAX",
          ref,
          "finger " + um(distance) + " µm from die > " + bond.dieToFingerMaxUm() + " µm");
    }
  }

  private void checkCapillary(Wire w, String padId, PointUm at) {
    for (Obstacle o : obstacles) {
      if (o.excludes(padId)) {
        continue;
      }
      double distance = o.rect().distanceTo(at);
      if (distance < bond.capillaryClearanceUm()) {
        error(
            "CAPILLARY_CLEARANCE",
            w.id() + "@" + padId,
            "second bond "
                + um(distance)
                + " µm from "
                + o.ref()
                + " < "
                + bond.capillaryClearanceUm()
                + " µm");
      }
    }
  }

  // --- wire pairs -------------------------------------------------------------------------------

  private void checkWirePairs(List<Wire> wires) {
    for (int i = 0; i < wires.size(); i++) {
      Wire p = wires.get(i);
      for (int j = i + 1; j < wires.size(); j++) {
        Wire q = wires.get(j);
        if (!differentNets(p.net(), q.net()) || sharesEnd(p, q)) {
          continue;
        }
        String ref = p.id() + "/" + q.id();
        if (Segments.properlyIntersect(p.a(), p.b(), q.a(), q.b())) {
          if (!bond.crossingsAllowed()) {
            error("WIRE_CROSSING", ref, p.net() + " crosses " + q.net());
          } else if (Math.abs(p.loopHeightUm() - q.loopHeightUm()) < bond.minLoopSeparationUm()) {
            error(
                "WIRE_CROSSING_LOOP",
                ref,
                "loop heights differ by "
                    + Math.abs(p.loopHeightUm() - q.loopHeightUm())
                    + " µm < "
                    + bond.minLoopSeparationUm()
                    + " µm");
          }
          continue;
        }
        double centre = Segments.distance(p.a(), p.b(), q.a(), q.b());
        double gap = centre - (p.diameterUm() + q.diameterUm()) / 2.0;
        double required = bond.spacingDiameters() * Math.max(p.diameterUm(), q.diameterUm());
        if (gap < required) {
          error("WIRE_SPACING", ref, "gap " + um(gap) + " µm < " + um(required) + " µm");
        }
      }
    }
  }

  private static boolean sharesEnd(Wire p, Wire q) {
    return p.a().equals(q.a()) || p.a().equals(q.b()) || p.b().equals(q.a()) || p.b().equals(q.b());
  }

  // --- fingers
  // ------------------------------------------------------------------------------------

  private void checkFingerPitch() {
    List<SubstratePad> fingers =
        design.substratePads().stream()
            .filter(p -> p.role() == SubstratePadRole.BOND_FINGER)
            .toList();
    for (int i = 0; i < fingers.size(); i++) {
      for (int j = i + 1; j < fingers.size(); j++) {
        SubstratePad a = fingers.get(i);
        SubstratePad b = fingers.get(j);
        double pitch = a.position().distanceTo(b.position());
        if (pitch < bond.fingerPitchUm()) {
          error(
              "FINGER_PITCH",
              a.id() + "/" + b.id(),
              "pitch " + um(pitch) + " µm < " + bond.fingerPitchUm() + " µm");
        }
      }
    }
  }

  // --- collection -------------------------------------------------------------------------------

  /** A printed conductor shape that must keep its distance from other nets. */
  private record Copper(String ref, String owner, String net, String layer, RectUm rect) {}

  /** Something a bonding capillary must not strike. {@code padId} is set for substrate pads. */
  private record Obstacle(String ref, String padId, RectUm rect) {
    boolean excludes(String id) {
      return id.equals(padId);
    }
  }

  /** A bond wire with both ends resolved to substrate positions. */
  private record Wire(BondWire source, PointUm a, PointUm b) {
    String id() {
      return source.id();
    }

    String net() {
      return source.net();
    }

    WirePurpose purpose() {
      return source.purpose();
    }

    int diameterUm() {
      return source.spec().diameterUm();
    }

    int loopHeightUm() {
      return source.spec().loopHeightUm();
    }
  }

  private static List<Copper> collectCopper(HybridDesign design) {
    List<Copper> list = new ArrayList<>();
    for (SubstratePad pad : design.substratePads()) {
      if (pad.id() != null && pad.layer() != null) {
        list.add(new Copper(pad.id(), pad.id(), pad.net(), pad.layer(), pad.bounds()));
      }
    }
    for (TwoPinComponent comp : design.components()) {
      addPin(list, comp, comp.pinA(), "A");
      addPin(list, comp, comp.pinB(), "B");
    }
    return list;
  }

  private static void addPin(List<Copper> list, TwoPinComponent comp, ComponentPin pin, String n) {
    if (pin != null && pin.layer() != null) {
      list.add(
          new Copper(
              comp.refdes() + "." + n, comp.refdes(), pin.net(), pin.layer(), comp.pinBounds(pin)));
    }
  }

  private static List<Obstacle> collectObstacles(HybridDesign design) {
    List<Obstacle> list = new ArrayList<>();
    for (SubstratePad pad : design.substratePads()) {
      if (pad.id() != null) {
        list.add(new Obstacle(pad.id(), pad.id(), pad.bounds()));
      }
    }
    for (TwoPinComponent comp : design.components()) {
      list.add(new Obstacle(comp.refdes(), null, comp.bodyBounds()));
      for (ComponentPin pin : comp.pins()) {
        list.add(new Obstacle(comp.refdes() + " pin", null, comp.pinBounds(pin)));
      }
    }
    for (Die die : design.dies()) {
      list.add(new Obstacle(die.refdes(), null, die.bounds()));
    }
    return list;
  }

  private List<Wire> resolveWires() {
    List<Wire> list = new ArrayList<>();
    for (BondWire w : design.wires()) {
      Optional<PointUm> a = design.position(w.from());
      Optional<PointUm> b = design.position(w.to());
      if (w.id() != null && a.isPresent() && b.isPresent() && w.purpose() != null) {
        list.add(new Wire(w, a.get(), b.get()));
      }
    }
    return list;
  }

  // --- helpers
  // ------------------------------------------------------------------------------------

  private static boolean differentNets(String a, String b) {
    return a != null && b != null && !a.isBlank() && !b.isBlank() && !a.equals(b);
  }

  private static String um(double value) {
    return String.format(Locale.ROOT, "%.0f", value);
  }

  private void error(String code, String ref, String message) {
    issues.add(new ValidationIssue(Severity.ERROR, code, ref, message));
  }
}
