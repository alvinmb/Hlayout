package app.freerouting.rules.hybrid;

import java.io.Serializable;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/**
 * Router-side design rules for a chip-and-wire hybrid. Resistor rules are deliberately absent:
 * HLayout designs and checks printed resistors, and hands them over as two-pin components whose
 * body and keep-out the router only avoids.
 *
 * @param conductor printed-conductor rules
 * @param bondWire die-bond and die-to-die wire rules
 * @param jumper wire-bond jumper rules
 * @param pads sizes of the bond fingers and jumper lands the router creates
 */
public record HybridRules(
    ConductorRules conductor, BondWireRules bondWire, JumperRules jumper, PadRules pads)
    implements Serializable {

  public static final HybridRules DEFAULT = new HybridRules(null, null, null, null);

  /** Fills missing rule groups with their defaults. */
  public HybridRules {
    conductor = Objects.requireNonNullElse(conductor, ConductorRules.DEFAULT);
    bondWire = Objects.requireNonNullElse(bondWire, BondWireRules.DEFAULT);
    jumper = Objects.requireNonNullElse(jumper, JumperRules.DEFAULT);
    pads = Objects.requireNonNullElse(pads, PadRules.DEFAULT);
  }

  /** Rule set with default pad sizes. */
  public HybridRules(ConductorRules conductor, BondWireRules bondWire, JumperRules jumper) {
    this(conductor, bondWire, jumper, null);
  }

  /**
   * Self-consistency problems: non-positive sizes, min above max, angles out of range. Empty when
   * the rule set is usable.
   */
  public List<String> problems() {
    List<String> problems = new ArrayList<>();
    positive(problems, "conductor.minLineUm", conductor.minLineUm());
    positive(problems, "conductor.minSpaceUm", conductor.minSpaceUm());
    notNegative(problems, "conductor.edgeClearanceUm", conductor.edgeClearanceUm());
    positive(problems, "conductor.windowMinUm", conductor.windowMinUm());
    notNegative(problems, "conductor.windowEnclosureUm", conductor.windowEnclosureUm());

    positive(problems, "bondWire.minLengthUm", bondWire.minLengthUm());
    ordered(problems, "bondWire length", bondWire.minLengthUm(), bondWire.maxLengthUm());
    if (bondWire.maxAngleDeg() <= 0 || bondWire.maxAngleDeg() > 90) {
      problems.add("bondWire.maxAngleDeg must be in (0, 90]");
    }
    if (bondWire.spacingDiameters() < 0) {
      problems.add("bondWire.spacingDiameters must not be negative");
    }
    notNegative(problems, "bondWire.minLoopSeparationUm", bondWire.minLoopSeparationUm());
    positive(problems, "bondWire.fingerPitchUm", bondWire.fingerPitchUm());
    notNegative(problems, "bondWire.capillaryClearanceUm", bondWire.capillaryClearanceUm());
    notNegative(problems, "bondWire.dieToFingerMinUm", bondWire.dieToFingerMinUm());
    ordered(
        problems,
        "bondWire die-to-finger",
        bondWire.dieToFingerMinUm(),
        bondWire.dieToFingerMaxUm());

    positive(problems, "jumper.minLengthUm", jumper.minLengthUm());
    ordered(problems, "jumper length", jumper.minLengthUm(), jumper.maxLengthUm());
    notNegative(problems, "jumper.minLoopHeightUm", jumper.minLoopHeightUm());

    positive(problems, "pads.fingerWidthUm", pads.fingerWidthUm());
    positive(problems, "pads.fingerLengthUm", pads.fingerLengthUm());
    positive(problems, "pads.jumperLandUm", pads.jumperLandUm());
    if (pads.fingerWidthUm() < conductor.minLineUm()) {
      problems.add("pads.fingerWidthUm is below conductor.minLineUm");
    }
    return problems;
  }

  private static void positive(List<String> problems, String name, int value) {
    if (value <= 0) {
      problems.add(name + " must be positive");
    }
  }

  private static void notNegative(List<String> problems, String name, int value) {
    if (value < 0) {
      problems.add(name + " must not be negative");
    }
  }

  private static void ordered(List<String> problems, String name, int min, int max) {
    if (min > max) {
      problems.add(name + ": minimum " + min + " exceeds maximum " + max);
    }
  }
}
