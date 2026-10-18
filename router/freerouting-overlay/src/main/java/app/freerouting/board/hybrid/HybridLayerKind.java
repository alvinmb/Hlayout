package app.freerouting.board.hybrid;

/** The role of one printed (or tape) layer in a hybrid stack. */
public enum HybridLayerKind {
  /** Printed conductor; the only kind the router places traces on. */
  CONDUCTOR,
  /** Printed dielectric; openings in it ("windows") connect the conductors above and below. */
  DIELECTRIC,
  /** Printed resistor ink. */
  RESISTOR,
  /** Protective glass overglaze; openings expose bond fingers and trim areas. */
  OVERGLAZE;

  public boolean isRoutable() {
    return this == CONDUCTOR;
  }
}
