package app.freerouting.rules.hybrid;

import java.io.Serializable;
import java.util.Objects;

/**
 * Printed-conductor rules for thick film. Values are integer micrometres; a value missing from JSON
 * takes its default. Defaults are starting points: replace them with the substrate supplier's
 * design guide.
 *
 * @param minLineUm minimum printed line width; default 150
 * @param minSpaceUm minimum edge-to-edge gap between conductors of different nets on one layer;
 *     default 150
 * @param edgeClearanceUm minimum gap from any conductor to the substrate edge; default 250
 * @param windowMinUm minimum dielectric window (crossover via) size; default 250
 * @param windowEnclosureUm conductor overlap around a dielectric window; default 100
 */
public record ConductorRules(
    Integer minLineUm,
    Integer minSpaceUm,
    Integer edgeClearanceUm,
    Integer windowMinUm,
    Integer windowEnclosureUm)
    implements Serializable {

  public static final ConductorRules DEFAULT = new ConductorRules(null, null, null, null, null);

  /** Fills values missing from JSON with their defaults. */
  public ConductorRules {
    minLineUm = Objects.requireNonNullElse(minLineUm, 150);
    minSpaceUm = Objects.requireNonNullElse(minSpaceUm, 150);
    edgeClearanceUm = Objects.requireNonNullElse(edgeClearanceUm, 250);
    windowMinUm = Objects.requireNonNullElse(windowMinUm, 250);
    windowEnclosureUm = Objects.requireNonNullElse(windowEnclosureUm, 100);
  }
}
