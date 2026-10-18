package app.freerouting.board.hybrid;

/** Placement rotation of a die or printed resistor, counter-clockwise, in 90 degree steps. */
public enum Rotation {
  R0,
  R90,
  R180,
  R270;

  /** Rotates a local offset about the local origin. */
  public PointUm apply(PointUm local) {
    return switch (this) {
      case R0 -> local;
      case R90 -> new PointUm(-local.y(), local.x());
      case R180 -> new PointUm(-local.x(), -local.y());
      case R270 -> new PointUm(local.y(), -local.x());
    };
  }

  /** True when width and height swap on the substrate. */
  public boolean isQuarterTurn() {
    return this == R90 || this == R270;
  }
}
