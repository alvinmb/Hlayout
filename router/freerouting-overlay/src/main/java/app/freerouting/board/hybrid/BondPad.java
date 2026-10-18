package app.freerouting.board.hybrid;

import java.io.Serializable;

/**
 * A bond pad on a bare die, in die-local coordinates (die origin = lower-left corner of the die
 * before rotation).
 *
 * @param id pad name, unique within the die, e.g. "1" or "VDD_A"
 * @param net net name, or null/empty for a no-connect pad
 * @param position pad centre, die-local
 * @param widthUm pad opening width
 * @param heightUm pad opening height
 */
public record BondPad(String id, String net, PointUm position, int widthUm, int heightUm)
    implements Serializable {

  /** Fills a missing position with the die origin. */
  public BondPad {
    position = position == null ? PointUm.ORIGIN : position;
  }

  public boolean isConnected() {
    return net != null && !net.isBlank();
  }
}
