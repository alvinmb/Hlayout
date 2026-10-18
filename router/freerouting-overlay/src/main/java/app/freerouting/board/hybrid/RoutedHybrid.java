package app.freerouting.board.hybrid;

import java.io.Serializable;
import java.util.List;
import java.util.Objects;

/**
 * Router output, written back to HLayout as {@code *.routed.json}.
 *
 * <p>{@code design} is the input design with the router's additions merged in: bond fingers and
 * die-bond wires from bond assignment, jumper lands and jumper wires. Traces and windows are listed
 * separately because the input model has no place for them.
 *
 * @param design input design plus added pads and wires
 * @param traces printed traces
 * @param windows dielectric crossovers
 * @param unrouted connections that could not be made
 * @param stats run summary
 * @param issues post-route DRC findings; empty straight out of the router
 */
public record RoutedHybrid(
    HybridDesign design,
    List<RoutedTrace> traces,
    List<CrossoverWindow> windows,
    List<UnroutedConnection> unrouted,
    RouteStats stats,
    List<ValidationIssue> issues)
    implements Serializable {

  /** Copies the lists; null becomes empty. */
  public RoutedHybrid {
    Objects.requireNonNull(design, "design");
    traces = HybridLists.copy(traces);
    windows = HybridLists.copy(windows);
    unrouted = HybridLists.copy(unrouted);
    issues = HybridLists.copy(issues);
  }

  /** The same result with these DRC findings attached. */
  public RoutedHybrid withIssues(List<ValidationIssue> found) {
    return new RoutedHybrid(design, traces, windows, unrouted, stats, found);
  }
}
