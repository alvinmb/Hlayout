package app.freerouting.rules.hybrid;

import java.io.Serializable;
import java.util.Objects;

/**
 * Rules for wire-bond jumpers (substrate pad to substrate pad, hopping printed conductors). Jumpers
 * also obey the spacing, crossing and capillary rules in {@link BondWireRules}.
 *
 * @param minLengthUm shortest jumper; default 500
 * @param maxLengthUm longest jumper; default 3000
 * @param minLoopHeightUm minimum loop height, so the wire clears the conductors it hops; default
 *     150
 */
public record JumperRules(Integer minLengthUm, Integer maxLengthUm, Integer minLoopHeightUm)
    implements Serializable {

  public static final JumperRules DEFAULT = new JumperRules(null, null, null);

  /** Fills values missing from JSON with their defaults. */
  public JumperRules {
    minLengthUm = Objects.requireNonNullElse(minLengthUm, 500);
    maxLengthUm = Objects.requireNonNullElse(maxLengthUm, 3000);
    minLoopHeightUm = Objects.requireNonNullElse(minLoopHeightUm, 150);
  }
}
