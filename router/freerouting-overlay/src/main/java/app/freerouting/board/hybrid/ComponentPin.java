package app.freerouting.board.hybrid;

import java.io.Serializable;

/**
 * One terminal of a {@link TwoPinComponent}: a rectangular conductor pad in component-local
 * coordinates (component origin = body centre, before rotation).
 *
 * @param net net name
 * @param position pad centre, component-local
 * @param widthUm pad size along local x
 * @param heightUm pad size along local y
 * @param layer CONDUCTOR layer the pad is printed on
 */
public record ComponentPin(String net, PointUm position, int widthUm, int heightUm, String layer)
    implements Serializable {

  /** Fills a missing position with the component origin. */
  public ComponentPin {
    position = position == null ? PointUm.ORIGIN : position;
  }
}
