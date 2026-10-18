package app.freerouting.board.hybrid;

import java.io.Serializable;
import java.util.Objects;

/**
 * Bond wire specification.
 *
 * @param material wire metal
 * @param diameterUm wire diameter, typically 25 (1 mil)
 * @param bondType ball-wedge or wedge-wedge
 * @param loopHeightUm loop apex above the higher bond surface
 */
public record WireSpec(WireMaterial material, int diameterUm, BondType bondType, int loopHeightUm)
    implements Serializable {

  /** 25 µm gold, ball-wedge, 150 µm loop. */
  public static final WireSpec DEFAULT_GOLD =
      new WireSpec(WireMaterial.GOLD, 25, BondType.BALL_WEDGE, 150);

  /** Fills a missing material or bond type with gold, ball-wedge. */
  public WireSpec {
    material = Objects.requireNonNullElse(material, WireMaterial.GOLD);
    bondType = Objects.requireNonNullElse(bondType, BondType.BALL_WEDGE);
  }
}
