package app.freerouting.board.hybrid;

import java.io.Serializable;
import java.util.Objects;

/**
 * One end of a bond wire: either a pad on a die or a printed substrate pad.
 *
 * @param kind which kind of pad
 * @param die die refdes when {@code kind == DIE_PAD}, otherwise null
 * @param pad pad id: die-local for a die pad, design-wide for a substrate pad
 */
public record BondEndpoint(Kind kind, String die, String pad) implements Serializable {

  /** Endpoint kind. */
  public enum Kind {
    DIE_PAD,
    SUBSTRATE_PAD
  }

  /** Treats a missing kind as a substrate pad. */
  public BondEndpoint {
    kind = Objects.requireNonNullElse(kind, Kind.SUBSTRATE_PAD);
  }

  /** Endpoint on pad {@code pad} of die {@code die}. */
  public static BondEndpoint diePad(String die, String pad) {
    return new BondEndpoint(Kind.DIE_PAD, die, pad);
  }

  /** Endpoint on the substrate pad with this id. */
  public static BondEndpoint substratePad(String pad) {
    return new BondEndpoint(Kind.SUBSTRATE_PAD, null, pad);
  }

  public boolean isDiePad() {
    return kind == Kind.DIE_PAD;
  }

  @Override
  public String toString() {
    return isDiePad() ? die + "." + pad : pad;
  }
}
