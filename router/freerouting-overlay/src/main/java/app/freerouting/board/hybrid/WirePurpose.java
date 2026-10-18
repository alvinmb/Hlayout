package app.freerouting.board.hybrid;

/** What a bond wire does, derived from its two endpoints. */
public enum WirePurpose {
  /** Die pad to substrate bond finger: the normal chip-and-wire connection. */
  DIE_BOND,
  /** Die pad to die pad. */
  DIE_TO_DIE,
  /** Substrate pad to substrate pad, hopping over printed conductors. Allowed, but high cost. */
  JUMPER;

  /** Purpose of a wire with these two ends. */
  public static WirePurpose of(BondEndpoint from, BondEndpoint to) {
    if (from.isDiePad() && to.isDiePad()) {
      return DIE_TO_DIE;
    }
    if (from.isDiePad() || to.isDiePad()) {
      return DIE_BOND;
    }
    return JUMPER;
  }
}
