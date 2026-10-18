package app.freerouting.board.hybrid;

import java.io.Serializable;

/**
 * A point on the substrate in integer micrometres. The origin is the substrate's lower-left corner;
 * x grows right, y grows up.
 */
public record PointUm(int x, int y) implements Serializable {

  public static final PointUm ORIGIN = new PointUm(0, 0);

  /** Vector sum of the two points. */
  public PointUm plus(PointUm other) {
    return new PointUm(x + other.x, y + other.y);
  }

  /** Straight-line distance to another point, in micrometres. */
  public double distanceTo(PointUm other) {
    return Math.hypot((double) other.x - x, (double) other.y - y);
  }
}
