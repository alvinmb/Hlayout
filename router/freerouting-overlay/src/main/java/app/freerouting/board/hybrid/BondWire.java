package app.freerouting.board.hybrid;

import java.io.Serializable;
import java.util.Objects;

/**
 * A bond wire between two pads. For ball-wedge wires {@code from} is the ball (first) bond.
 *
 * @param id unique wire id, e.g. "W7"
 * @param net net carried by the wire
 * @param from first bond
 * @param to second bond
 * @param spec wire specification; null means the design default
 * @param fixed true if the router must not move or remove the wire
 */
public record BondWire(
    String id, String net, BondEndpoint from, BondEndpoint to, WireSpec spec, boolean fixed)
    implements Serializable {

  /** Fills a missing wire spec with {@link WireSpec#DEFAULT_GOLD}. */
  public BondWire {
    spec = Objects.requireNonNullElse(spec, WireSpec.DEFAULT_GOLD);
  }

  /** Purpose derived from the endpoints; null if an endpoint is missing. */
  public WirePurpose purpose() {
    return from == null || to == null ? null : WirePurpose.of(from, to);
  }

  public boolean isJumper() {
    return purpose() == WirePurpose.JUMPER;
  }
}
