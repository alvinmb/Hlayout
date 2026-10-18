package app.freerouting.board.hybrid;

import java.io.Serializable;
import java.util.Objects;

/**
 * The ceramic substrate: a rectangle from (0, 0) to (widthUm, heightUm).
 *
 * @param technology process family
 * @param material free text, e.g. "96% alumina"
 * @param widthUm outline width
 * @param heightUm outline height
 * @param thicknessUm ceramic thickness, used for thermal and 3D export
 */
public record Substrate(
    SubstrateTechnology technology, String material, int widthUm, int heightUm, int thicknessUm)
    implements Serializable {

  /** Fills a missing technology with thick film and a missing material with "". */
  public Substrate {
    technology = Objects.requireNonNullElse(technology, SubstrateTechnology.THICK_FILM);
    material = Objects.requireNonNullElse(material, "");
  }

  /** True if the point lies on the substrate, edges included. */
  public boolean contains(PointUm p) {
    return p.x() >= 0 && p.y() >= 0 && p.x() <= widthUm && p.y() <= heightUm;
  }
}
