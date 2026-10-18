package app.freerouting.board.hybrid;

import java.io.Serializable;
import java.util.List;
import java.util.Objects;

/**
 * How the router may connect things: which conductor layers it routes on, and the relative cost of
 * each interconnect option in multiples of one unit of trace length (1 mm).
 *
 * <p>Cost order, cheapest first: trace, dielectric crossover, wire-bond jumper. Jumpers are allowed
 * but expensive, so the router reaches for one only when no printed route exists.
 *
 * <p><b>Single-layer mode.</b> When only one conductor layer is routable, there are no crossovers:
 * a wire-bond jumper is the only way for one net to cross another. Single-layer mode applies when
 * {@code routingLayers} names exactly one layer, or when it is empty and the stack has exactly one
 * conductor layer.
 *
 * <p>Fields are boxed so that a value missing from JSON takes its default rather than zero or
 * false.
 *
 * @param crossoverCost cost of one dielectric window (layer change); default 10; unused in
 *     single-layer mode
 * @param jumperCost fixed cost of one wire-bond jumper, on top of its length; default 50
 * @param jumpersAllowed false forbids the router from adding jumpers; default true
 * @param maxJumpers upper bound on router-added jumpers per design; 0 = no limit (default)
 * @param routingLayers conductor layers the router may place traces on, in stack order; empty =
 *     every conductor layer (default)
 * @param gridUm routing grid pitch; null or 0 = half the smaller of min line and min space, at
 *     least 25
 */
public record HybridRoutingOptions(
    Double crossoverCost,
    Double jumperCost,
    Boolean jumpersAllowed,
    Integer maxJumpers,
    List<String> routingLayers,
    Integer gridUm)
    implements Serializable {

  public static final HybridRoutingOptions DEFAULT =
      new HybridRoutingOptions(null, null, null, null, null, null);

  /** Fills fields missing from JSON with their defaults. */
  public HybridRoutingOptions {
    crossoverCost = Objects.requireNonNullElse(crossoverCost, 10.0);
    jumperCost = Objects.requireNonNullElse(jumperCost, 50.0);
    jumpersAllowed = Objects.requireNonNullElse(jumpersAllowed, Boolean.TRUE);
    maxJumpers = Objects.requireNonNullElse(maxJumpers, 0);
    routingLayers = HybridLists.copy(routingLayers);
    gridUm = Objects.requireNonNullElse(gridUm, 0);
  }

  /** Options with the grid pitch derived from the conductor rules. */
  public HybridRoutingOptions(
      Double crossoverCost,
      Double jumperCost,
      Boolean jumpersAllowed,
      Integer maxJumpers,
      List<String> routingLayers) {
    this(crossoverCost, jumperCost, jumpersAllowed, maxJumpers, routingLayers, null);
  }

  /** Options for single-layer routing on {@code layer}, jumpers allowed at the default cost. */
  public static HybridRoutingOptions singleLayer(String layer) {
    return new HybridRoutingOptions(null, null, null, null, List.of(layer));
  }

  /**
   * The conductor layers the router may use: {@code routingLayers} if given, otherwise every
   * conductor layer of the stack. Names that are not conductor layers are dropped; the validator
   * reports them.
   */
  public List<String> effectiveRoutingLayers(HybridLayerStack stack) {
    List<String> conductors =
        stack.conductorLayers().stream().map(HybridLayer::name).filter(Objects::nonNull).toList();
    if (routingLayers.isEmpty()) {
      return conductors;
    }
    return conductors.stream().filter(routingLayers::contains).toList();
  }

  /** True if exactly one conductor layer is routable, so jumpers are the only crossing. */
  public boolean isSingleLayer(HybridLayerStack stack) {
    return effectiveRoutingLayers(stack).size() == 1;
  }

  /**
   * True if the cost order holds for this stack: trace (1) &lt; jumper, and in multi-layer mode
   * also trace &lt; crossover &lt; jumper.
   */
  public boolean isOrdered(HybridLayerStack stack) {
    if (isSingleLayer(stack)) {
      return jumperCost > 1.0;
    }
    return crossoverCost > 1.0 && jumperCost > crossoverCost;
  }
}
