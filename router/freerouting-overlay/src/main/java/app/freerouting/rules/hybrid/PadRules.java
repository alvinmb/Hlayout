package app.freerouting.rules.hybrid;

import java.io.Serializable;
import java.util.Objects;

/**
 * Sizes of the printed pads the router creates itself: bond fingers placed by bond assignment and
 * lands at each end of a router-added wire-bond jumper. Micrometres; a value missing from JSON
 * takes its default.
 *
 * @param fingerWidthUm bond finger size across the wire; default 150
 * @param fingerLengthUm bond finger size along the wire; default 200
 * @param jumperLandUm side of the square land at each end of a jumper; default 200
 */
public record PadRules(Integer fingerWidthUm, Integer fingerLengthUm, Integer jumperLandUm)
    implements Serializable {

  public static final PadRules DEFAULT = new PadRules(null, null, null);

  /** Fills values missing from JSON with their defaults. */
  public PadRules {
    fingerWidthUm = Objects.requireNonNullElse(fingerWidthUm, 150);
    fingerLengthUm = Objects.requireNonNullElse(fingerLengthUm, 200);
    jumperLandUm = Objects.requireNonNullElse(jumperLandUm, 200);
  }
}
