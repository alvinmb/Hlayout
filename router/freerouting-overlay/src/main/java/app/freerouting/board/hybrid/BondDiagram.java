package app.freerouting.board.hybrid;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Optional;

/**
 * Builds the bond diagram of a design: every wire with both ends resolved to substrate positions,
 * in the order a bonder would place them.
 *
 * <p>Order: die bonds die by die (dies in design order), each die's wires in the order of its pads;
 * then die-to-die wires; then jumpers by id. A wire whose ends do not both resolve is left out, as
 * the validator already reports it.
 */
public final class BondDiagram {

  private BondDiagram() {}

  /** The bond diagram of {@code design}. */
  public static List<BondDiagramEntry> of(HybridDesign design) {
    List<BondWire> ordered = new ArrayList<>(design.wires());
    ordered.sort(Comparator.comparingLong(w -> orderKey(design, w)));
    List<BondDiagramEntry> rows = new ArrayList<>();
    for (BondWire w : ordered) {
      Optional<PointUm> a = design.position(w.from());
      Optional<PointUm> b = design.position(w.to());
      if (a.isEmpty() || b.isEmpty()) {
        continue;
      }
      WireSpec spec = w.spec();
      rows.add(
          new BondDiagramEntry(
              rows.size() + 1,
              w.id(),
              w.net(),
              w.purpose(),
              w.from().toString(),
              a.get(),
              w.to().toString(),
              b.get(),
              spec.material(),
              spec.diameterUm(),
              spec.bondType(),
              spec.loopHeightUm(),
              (int) Math.round(a.get().distanceTo(b.get())),
              departureAngleDeg(design, w.from(), a.get(), b.get()),
              departureAngleDeg(design, w.to(), b.get(), a.get()),
              w.fixed()));
    }
    return rows;
  }

  /**
   * Angle in degrees between the wire leaving {@code at} toward {@code toward} and the outward
   * normal of the die edge at that pad; null when the end is not on a die or the wire has no
   * length.
   */
  static Double departureAngleDeg(
      HybridDesign design, BondEndpoint end, PointUm at, PointUm toward) {
    if (end == null || !end.isDiePad()) {
      return null;
    }
    Optional<Die> die = design.die(end.die());
    Optional<BondPad> pad = die.flatMap(d -> d.pad(end.pad()));
    if (pad.isEmpty()) {
      return null;
    }
    PointUm n = die.get().padOutwardNormal(pad.get());
    double dx = (double) toward.x() - at.x();
    double dy = (double) toward.y() - at.y();
    double len = Math.hypot(dx, dy);
    if (len == 0) {
      return null;
    }
    double cos = (dx * n.x() + dy * n.y()) / len;
    double deg = Math.toDegrees(Math.acos(Math.max(-1.0, Math.min(1.0, cos))));
    return Math.round(deg * 10.0) / 10.0;
  }

  /** Sort key: purpose group, then die index, then pad index, then wire index. */
  private static long orderKey(HybridDesign design, BondWire w) {
    long wireIndex = design.wires().indexOf(w);
    WirePurpose purpose = w.purpose();
    if (purpose == WirePurpose.DIE_BOND || purpose == WirePurpose.DIE_TO_DIE) {
      BondEndpoint dieEnd = w.from().isDiePad() ? w.from() : w.to();
      int dieIndex = indexOfDie(design, dieEnd.die());
      int padIndex = design.die(dieEnd.die()).map(d -> indexOfPad(d, dieEnd.pad())).orElse(0xFFFFF);
      long group = purpose == WirePurpose.DIE_BOND ? 0 : 1;
      return (group << 60) | ((long) dieIndex << 40) | ((long) padIndex << 20) | wireIndex;
    }
    return (2L << 60) | wireIndex;
  }

  private static int indexOfDie(HybridDesign design, String refdes) {
    for (int i = 0; i < design.dies().size(); i++) {
      if (design.dies().get(i).refdes().equals(refdes)) {
        return i;
      }
    }
    return 0xFFFFF;
  }

  private static int indexOfPad(Die die, String padId) {
    for (int i = 0; i < die.pads().size(); i++) {
      if (die.pads().get(i).id().equals(padId)) {
        return i;
      }
    }
    return 0xFFFFF;
  }
}
