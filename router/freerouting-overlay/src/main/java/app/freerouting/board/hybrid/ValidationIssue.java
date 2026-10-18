package app.freerouting.board.hybrid;

import java.io.Serializable;

/**
 * One problem found by {@link HybridValidator}.
 *
 * @param severity ERROR blocks routing; WARNING is reported only
 * @param code stable machine-readable code, e.g. "WIRE_NET_MISMATCH"
 * @param ref the object it concerns, e.g. "U1.7", "W3", "R2"
 * @param message human-readable explanation
 */
public record ValidationIssue(Severity severity, String code, String ref, String message)
    implements Serializable {

  /** Issue severity. */
  public enum Severity {
    ERROR,
    WARNING
  }

  @Override
  public String toString() {
    return severity + " " + code + " [" + ref + "] " + message;
  }
}
