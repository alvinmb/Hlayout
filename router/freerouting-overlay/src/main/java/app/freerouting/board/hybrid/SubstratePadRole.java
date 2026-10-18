package app.freerouting.board.hybrid;

/** Why a printed pad exists on the substrate. */
public enum SubstratePadRole {
  /** Landing pad for a wire bonded from a die pad. */
  BOND_FINGER,
  /** One end of a wire-bond jumper that hops over printed conductors. */
  JUMPER_LAND,
  /** Pad for an external lead, pin or package feed-through. */
  EXTERNAL
}
