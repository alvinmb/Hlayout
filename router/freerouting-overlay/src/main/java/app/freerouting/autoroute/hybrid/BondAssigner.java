package app.freerouting.autoroute.hybrid;

import app.freerouting.board.hybrid.BondEndpoint;
import app.freerouting.board.hybrid.BondPad;
import app.freerouting.board.hybrid.BondWire;
import app.freerouting.board.hybrid.ComponentPin;
import app.freerouting.board.hybrid.Die;
import app.freerouting.board.hybrid.HybridDesign;
import app.freerouting.board.hybrid.PointUm;
import app.freerouting.board.hybrid.RectUm;
import app.freerouting.board.hybrid.SubstratePad;
import app.freerouting.board.hybrid.SubstratePadRole;
import app.freerouting.board.hybrid.TwoPinComponent;
import app.freerouting.rules.hybrid.BondWireRules;
import app.freerouting.rules.hybrid.ConductorRules;
import app.freerouting.rules.hybrid.PadRules;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;

/**
 * Bond assignment: gives every connected die pad that has no bond wire a bond finger and a wire.
 *
 * <p>The finger goes straight out from the die edge the pad sits on, so the wire leaves at 0°. Its
 * distance from the die is the smallest that satisfies the die-to-finger minimum, the attach
 * keep-out and the minimum wire length. If it would clash with another pad (pitch or spacing), it
 * moves out one row (finger length plus min space) at a time, up to the die-to-finger maximum and
 * the maximum wire length. A pad that cannot be placed is reported, not forced.
 *
 * <p>Deterministic: dies and pads are processed in a fixed order.
 */
public final class BondAssigner {

  private BondAssigner() {}

  /**
   * Result of bond assignment.
   *
   * @param design input design with the new fingers and wires added
   * @param fingersAdded number of fingers placed
   * @param unplaced die pads ("U1.8") that needed a finger but had no legal place for one
   */
  public record Result(HybridDesign design, int fingersAdded, List<String> unplaced) {}

  /** Adds fingers and wires for unbonded connected die pads. */
  public static Result assign(HybridDesign design) {
    Set<String> bonded = new HashSet<>();
    for (BondWire w : design.wires()) {
      mark(bonded, w.from());
      mark(bonded, w.to());
    }
    List<SubstratePad> pads = new ArrayList<>(design.substratePads());
    List<BondWire> wires = new ArrayList<>(design.wires());
    List<String> unplaced = new ArrayList<>();
    String layer = fingerLayer(design);
    int added = 0;
    for (Die die : design.dies()) {
      List<BondPad> todo =
          die.pads().stream()
              .filter(p -> p.isConnected() && !bonded.contains(die.refdes() + "." + p.id()))
              .sorted(alongEdge(die))
              .toList();
      for (BondPad pad : todo) {
        SubstratePad finger = place(design, die, pad, layer, pads);
        if (finger == null) {
          unplaced.add(die.refdes() + "." + pad.id());
          continue;
        }
        pads.add(finger);
        wires.add(
            new BondWire(
                uniqueId("WB_" + die.refdes() + "_" + pad.id(), wireIds(wires)),
                pad.net(),
                BondEndpoint.diePad(die.refdes(), pad.id()),
                BondEndpoint.substratePad(finger.id()),
                null,
                false));
        added++;
      }
    }
    if (added == 0) {
      return new Result(design, 0, unplaced);
    }
    HybridDesign updated =
        new HybridDesign(
            design.schemaVersion(),
            design.name(),
            design.substrate(),
            design.stack(),
            design.dies(),
            pads,
            wires,
            design.components(),
            design.routing(),
            design.rules());
    return new Result(updated, added, unplaced);
  }

  private static SubstratePad place(
      HybridDesign design, Die die, BondPad pad, String layer, List<SubstratePad> pads) {
    BondWireRules bond = design.rules().bondWire();
    PadRules sizes = design.rules().pads();
    ConductorRules conductor = design.rules().conductor();
    PointUm at = die.padPosition(pad);
    PointUm normal = die.padOutwardNormal(pad);
    RectUm dieBounds = die.bounds();
    double inset = distanceToEdgeAlong(dieBounds, at, normal);
    int halfLength = sizes.fingerLengthUm() / 2;
    double first =
        Math.max(
            bond.dieToFingerMinUm(),
            Math.max(die.attachKeepoutUm() + halfLength, bond.minLengthUm() - inset));
    double step = sizes.fingerLengthUm() + conductor.minSpaceUm();
    boolean vertical = normal.x() == 0;
    int w = vertical ? sizes.fingerWidthUm() : sizes.fingerLengthUm();
    int h = vertical ? sizes.fingerLengthUm() : sizes.fingerWidthUm();
    String id = uniqueId("BF_" + die.refdes() + "_" + pad.id(), padIds(pads));
    for (double out = first;
        out <= bond.dieToFingerMaxUm() && inset + out <= bond.maxLengthUm();
        out += step) {
      long d = Math.round(inset + out);
      PointUm centre =
          new PointUm(at.x() + (int) (normal.x() * d), at.y() + (int) (normal.y() * d));
      SubstratePad finger =
          new SubstratePad(id, pad.net(), SubstratePadRole.BOND_FINGER, centre, w, h, layer);
      if (fits(design, finger, pads)) {
        return finger;
      }
    }
    return null;
  }

  /** True if the finger keeps every pitch, spacing, edge and keep-out rule against the design. */
  private static boolean fits(HybridDesign design, SubstratePad finger, List<SubstratePad> pads) {
    ConductorRules conductor = design.rules().conductor();
    BondWireRules bond = design.rules().bondWire();
    RectUm r = finger.bounds();
    if (design.substrate() != null) {
      int gap =
          Math.min(
              Math.min(r.minX(), r.minY()),
              Math.min(
                  design.substrate().widthUm() - r.maxX(),
                  design.substrate().heightUm() - r.maxY()));
      if (gap < conductor.edgeClearanceUm()) {
        return false;
      }
    }
    for (SubstratePad other : pads) {
      if (!Objects.equals(other.layer(), finger.layer())) {
        continue;
      }
      if (r.gap(other.bounds()) < conductor.minSpaceUm()) {
        return false;
      }
      if (other.role() == SubstratePadRole.BOND_FINGER
          && other.position().distanceTo(finger.position()) < bond.fingerPitchUm()) {
        return false;
      }
    }
    for (TwoPinComponent comp : design.components()) {
      if (overlaps(comp.bodyBounds().grow(comp.keepoutUm()), r)) {
        return false;
      }
      for (ComponentPin pin : comp.pins()) {
        if (Objects.equals(pin.layer(), finger.layer())
            && comp.pinBounds(pin).gap(r) < conductor.minSpaceUm()) {
          return false;
        }
      }
    }
    for (Die die : design.dies()) {
      if (overlaps(die.bounds().grow(die.attachKeepoutUm()), r)) {
        return false;
      }
    }
    return true;
  }

  private static boolean overlaps(RectUm a, RectUm b) {
    return a.minX() < b.maxX() && b.minX() < a.maxX() && a.minY() < b.maxY() && b.minY() < a.maxY();
  }

  /** Distance from an inside point to the die edge in the direction of the unit normal. */
  private static double distanceToEdgeAlong(RectUm r, PointUm p, PointUm normal) {
    if (normal.x() > 0) {
      return r.maxX() - p.x();
    }
    if (normal.x() < 0) {
      return p.x() - r.minX();
    }
    if (normal.y() > 0) {
      return r.maxY() - p.y();
    }
    return p.y() - r.minY();
  }

  /** Orders pads by the edge they sit on, then along it, so staggering is repeatable. */
  private static Comparator<BondPad> alongEdge(Die die) {
    return Comparator.<BondPad>comparingInt(p -> edgeIndex(die.padOutwardNormal(p)))
        .thenComparingInt(p -> die.padPosition(p).x() + die.padPosition(p).y())
        .thenComparing(BondPad::id, Comparator.nullsFirst(Comparator.naturalOrder()));
  }

  private static int edgeIndex(PointUm normal) {
    if (normal.y() < 0) {
      return 0;
    }
    if (normal.x() > 0) {
      return 1;
    }
    if (normal.y() > 0) {
      return 2;
    }
    return 3;
  }

  /**
   * Layer for new fingers: the layer most existing fingers use, else the top routable conductor.
   */
  private static String fingerLayer(HybridDesign design) {
    for (SubstratePad p : design.substratePads()) {
      if (p.role() == SubstratePadRole.BOND_FINGER && p.layer() != null) {
        return p.layer();
      }
    }
    List<String> layers = design.routingLayers();
    return layers.isEmpty() ? null : layers.get(layers.size() - 1);
  }

  private static void mark(Set<String> bonded, BondEndpoint end) {
    if (end != null && end.isDiePad()) {
      bonded.add(end.die() + "." + end.pad());
    }
  }

  private static Set<String> padIds(List<SubstratePad> pads) {
    Set<String> ids = new HashSet<>();
    pads.forEach(p -> ids.add(p.id()));
    return ids;
  }

  private static Set<String> wireIds(List<BondWire> wires) {
    Set<String> ids = new HashSet<>();
    wires.forEach(w -> ids.add(w.id()));
    return ids;
  }

  static String uniqueId(String base, Set<String> taken) {
    if (!taken.contains(base)) {
      return base;
    }
    for (int i = 2; ; i++) {
      String candidate = base + "_" + i;
      if (!taken.contains(candidate)) {
        return candidate;
      }
    }
  }
}
