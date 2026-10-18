package app.freerouting.board.hybrid;

import java.io.Serializable;
import java.util.Objects;

/**
 * A printed pad on a conductor layer: a bond finger, a jumper land or an external pad.
 *
 * @param id unique pad id across the design, e.g. "F12" or "J3A"
 * @param net net name
 * @param role why the pad exists
 * @param position pad centre on the substrate
 * @param widthUm pad width (along the wire for a finger)
 * @param heightUm pad height
 * @param layer conductor layer the pad is printed on; must be exposed through the overglaze
 */
public record SubstratePad(
    String id,
    String net,
    SubstratePadRole role,
    PointUm position,
    int widthUm,
    int heightUm,
    String layer)
    implements Serializable {

  /** Fills a missing role with bond finger and a missing position with the origin. */
  public SubstratePad {
    role = Objects.requireNonNullElse(role, SubstratePadRole.BOND_FINGER);
    position = position == null ? PointUm.ORIGIN : position;
  }

  /** Printed outline on the substrate. */
  public RectUm bounds() {
    return RectUm.centred(position, widthUm, heightUm);
  }
}
