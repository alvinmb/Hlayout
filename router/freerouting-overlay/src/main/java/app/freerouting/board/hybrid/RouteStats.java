package app.freerouting.board.hybrid;

import java.io.Serializable;

/**
 * Summary of a routing run.
 *
 * @param connections connections the router had to make
 * @param routed connections made
 * @param traceLengthUm total printed trace length
 * @param windows dielectric crossovers placed
 * @param jumpersAdded wire-bond jumpers the router added
 * @param fingersAdded bond fingers placed by bond assignment
 * @param gridUm routing grid pitch used
 */
public record RouteStats(
    int connections,
    int routed,
    long traceLengthUm,
    int windows,
    int jumpersAdded,
    int fingersAdded,
    int gridUm)
    implements Serializable {

  /** True if every connection was made. */
  public boolean isComplete() {
    return routed == connections;
  }
}
