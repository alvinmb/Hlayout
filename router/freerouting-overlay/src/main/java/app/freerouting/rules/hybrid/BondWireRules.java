package app.freerouting.rules.hybrid;

import java.io.Serializable;
import java.util.Objects;

/**
 * Rules for die bonds and die-to-die wires. Lengths are plan-view, pad centre to pad centre, in
 * micrometres; a value missing from JSON takes its default.
 *
 * @param minLengthUm shortest wire the bonder can loop; default 500
 * @param maxLengthUm longest unsupported wire (25 µm gold); default 3000
 * @param maxAngleDeg largest angle between the wire and the outward normal of the die edge its die
 *     pad sits on; default 45
 * @param spacingDiameters minimum plan-view gap between wires of different nets, in multiples of
 *     the larger wire diameter; default 2.0
 * @param crossingsAllowed whether wires of different nets may cross in plan view; default false
 * @param minLoopSeparationUm when crossings are allowed, the minimum loop-height difference at a
 *     crossing; default 100
 * @param fingerPitchUm minimum centre-to-centre pitch between bond fingers; default 150
 * @param capillaryClearanceUm minimum gap from a second-bond pad centre to any other object, so the
 *     capillary does not strike it; default 250
 * @param dieToFingerMinUm minimum distance from a bond finger centre to its die outline; default
 *     250
 * @param dieToFingerMaxUm maximum distance from a bond finger centre to its die outline; default
 *     1500
 */
public record BondWireRules(
    Integer minLengthUm,
    Integer maxLengthUm,
    Double maxAngleDeg,
    Double spacingDiameters,
    Boolean crossingsAllowed,
    Integer minLoopSeparationUm,
    Integer fingerPitchUm,
    Integer capillaryClearanceUm,
    Integer dieToFingerMinUm,
    Integer dieToFingerMaxUm)
    implements Serializable {

  public static final BondWireRules DEFAULT =
      new BondWireRules(null, null, null, null, null, null, null, null, null, null);

  /** Fills values missing from JSON with their defaults. */
  public BondWireRules {
    minLengthUm = Objects.requireNonNullElse(minLengthUm, 500);
    maxLengthUm = Objects.requireNonNullElse(maxLengthUm, 3000);
    maxAngleDeg = Objects.requireNonNullElse(maxAngleDeg, 45.0);
    spacingDiameters = Objects.requireNonNullElse(spacingDiameters, 2.0);
    crossingsAllowed = Objects.requireNonNullElse(crossingsAllowed, Boolean.FALSE);
    minLoopSeparationUm = Objects.requireNonNullElse(minLoopSeparationUm, 100);
    fingerPitchUm = Objects.requireNonNullElse(fingerPitchUm, 150);
    capillaryClearanceUm = Objects.requireNonNullElse(capillaryClearanceUm, 250);
    dieToFingerMinUm = Objects.requireNonNullElse(dieToFingerMinUm, 250);
    dieToFingerMaxUm = Objects.requireNonNullElse(dieToFingerMaxUm, 1500);
  }
}
