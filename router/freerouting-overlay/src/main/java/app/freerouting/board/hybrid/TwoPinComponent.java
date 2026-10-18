package app.freerouting.board.hybrid;

import java.io.Serializable;
import java.util.Arrays;
import java.util.List;
import java.util.Objects;

/**
 * A fixed two-pin part handed to the router by HLayout, typically a printed resistor.
 *
 * <p>HLayout owns resistor synthesis (ink, size, trim allowance) and resistor rules. The router
 * only needs the two terminal pads and the body footprint: it routes to the pads and treats the
 * body, grown by {@code keepoutUm}, as an obstacle on every layer in {@code obstacleLayers}.
 *
 * <p>Local frame: origin at the body centre; for a resistor, current flows along local x and {@code
 * bodyLengthUm} is measured along x.
 *
 * @param refdes unique reference, e.g. "R4"
 * @param kind printed resistor or chip
 * @param centre body centre on the substrate
 * @param rotation counter-clockwise rotation of the local frame
 * @param bodyLengthUm body size along local x
 * @param bodyWidthUm body size along local y
 * @param keepoutUm clearance HLayout requires around the body (e.g. trim kerf, print spacing)
 * @param obstacleLayers layers the body blocks; for a printed resistor, at least its termination
 *     conductor layer
 * @param pinA terminal A (local -x for a resistor)
 * @param pinB terminal B (local +x for a resistor)
 * @param value informational value string, e.g. "10k"; not used by the router
 */
public record TwoPinComponent(
    String refdes,
    TwoPinKind kind,
    PointUm centre,
    Rotation rotation,
    int bodyLengthUm,
    int bodyWidthUm,
    int keepoutUm,
    List<String> obstacleLayers,
    ComponentPin pinA,
    ComponentPin pinB,
    String value)
    implements Serializable {

  /** Fills missing kind, centre, rotation and obstacle layers with defaults. */
  public TwoPinComponent {
    kind = Objects.requireNonNullElse(kind, TwoPinKind.PRINTED_RESISTOR);
    centre = centre == null ? PointUm.ORIGIN : centre;
    rotation = Objects.requireNonNullElse(rotation, Rotation.R0);
    obstacleLayers = HybridLists.copy(obstacleLayers);
  }

  /** Substrate position of a component-local point. */
  public PointUm toSubstrate(PointUm local) {
    return centre.plus(rotation.apply(local));
  }

  /** Substrate position of the pin centre. */
  public PointUm pinPosition(ComponentPin pin) {
    return toSubstrate(pin.position());
  }

  /** Pin pad on the substrate (width and height swap on a quarter turn). */
  public RectUm pinBounds(ComponentPin pin) {
    boolean swap = rotation.isQuarterTurn();
    int w = swap ? pin.heightUm() : pin.widthUm();
    int h = swap ? pin.widthUm() : pin.heightUm();
    return RectUm.centred(pinPosition(pin), w, h);
  }

  /** Body footprint on the substrate, without the keep-out. */
  public RectUm bodyBounds() {
    return RectUm.bounding(bodyCorners());
  }

  /** Both pins, A then B, skipping a missing one. */
  public List<ComponentPin> pins() {
    return HybridLists.copy(Arrays.asList(pinA, pinB));
  }

  /** Body corners on the substrate, counter-clockwise from local (-L/2, -W/2). */
  public List<PointUm> bodyCorners() {
    int halfLength = bodyLengthUm / 2;
    int halfWidth = bodyWidthUm / 2;
    return List.of(
        toSubstrate(new PointUm(-halfLength, -halfWidth)),
        toSubstrate(new PointUm(bodyLengthUm - halfLength, -halfWidth)),
        toSubstrate(new PointUm(bodyLengthUm - halfLength, bodyWidthUm - halfWidth)),
        toSubstrate(new PointUm(-halfLength, bodyWidthUm - halfWidth)));
  }
}
