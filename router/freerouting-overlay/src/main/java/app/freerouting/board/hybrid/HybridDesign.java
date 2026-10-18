package app.freerouting.board.hybrid;

import app.freerouting.board.model.structure.LayerStructure;
import app.freerouting.rules.hybrid.HybridRules;
import java.io.Serializable;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.TreeSet;

/**
 * A complete chip-and-wire hybrid design as handed to the router: substrate, layer stack, dies,
 * printed pads, bond wires (including jumpers), two-pin components, routing options and design
 * rules. All lengths are integer micrometres.
 *
 * <p>Printed resistors arrive already sized by HLayout, as {@link TwoPinComponent}s. The model is
 * immutable; later phases produce new instances.
 *
 * @param schemaVersion version of the hybrid JSON schema this design follows
 * @param name design name
 * @param substrate ceramic substrate
 * @param stack printed layer stack
 * @param dies bare dies
 * @param substratePads bond fingers, jumper lands and external pads
 * @param wires bond wires, including jumpers
 * @param components two-pin components (printed resistors, chips)
 * @param routing routable layers and interconnect costs; null means {@link
 *     HybridRoutingOptions#DEFAULT}
 * @param rules router-side design rules; null means {@link HybridRules#DEFAULT}
 */
public record HybridDesign(
    int schemaVersion,
    String name,
    Substrate substrate,
    HybridLayerStack stack,
    List<Die> dies,
    List<SubstratePad> substratePads,
    List<BondWire> wires,
    List<TwoPinComponent> components,
    HybridRoutingOptions routing,
    HybridRules rules)
    implements Serializable {

  public static final int CURRENT_SCHEMA_VERSION = 1;

  /** Fills sections missing from JSON with empty lists or defaults. */
  public HybridDesign {
    name = Objects.requireNonNullElse(name, "");
    stack = stack == null ? new HybridLayerStack(List.of()) : stack;
    dies = HybridLists.copy(dies);
    substratePads = HybridLists.copy(substratePads);
    wires = HybridLists.copy(wires);
    components = HybridLists.copy(components);
    routing = Objects.requireNonNullElse(routing, HybridRoutingOptions.DEFAULT);
    rules = Objects.requireNonNullElse(rules, HybridRules.DEFAULT);
  }

  /** The die with this refdes, if any. */
  public Optional<Die> die(String refdes) {
    return dies.stream().filter(d -> Objects.equals(d.refdes(), refdes)).findFirst();
  }

  /** The substrate pad with this id, if any. */
  public Optional<SubstratePad> substratePad(String id) {
    return substratePads.stream().filter(p -> Objects.equals(p.id(), id)).findFirst();
  }

  /** The two-pin component with this refdes, if any. */
  public Optional<TwoPinComponent> component(String refdes) {
    return components.stream().filter(c -> Objects.equals(c.refdes(), refdes)).findFirst();
  }

  /** Substrate position of a wire endpoint, empty if it does not resolve. */
  public Optional<PointUm> position(BondEndpoint endpoint) {
    if (endpoint == null) {
      return Optional.empty();
    }
    if (endpoint.isDiePad()) {
      return die(endpoint.die()).flatMap(d -> d.pad(endpoint.pad()).map(d::padPosition));
    }
    return substratePad(endpoint.pad()).map(SubstratePad::position);
  }

  /** Net of the pad at a wire endpoint, empty if it does not resolve or is unconnected. */
  public Optional<String> net(BondEndpoint endpoint) {
    if (endpoint == null) {
      return Optional.empty();
    }
    if (endpoint.isDiePad()) {
      return die(endpoint.die()).flatMap(d -> d.pad(endpoint.pad())).map(BondPad::net);
    }
    return substratePad(endpoint.pad()).map(SubstratePad::net);
  }

  /** Plan-view wire length in µm (pad centre to pad centre); empty if an end does not resolve. */
  public Optional<Double> wireLengthUm(BondWire wire) {
    Optional<PointUm> a = position(wire.from());
    Optional<PointUm> b = position(wire.to());
    return a.isPresent() && b.isPresent()
        ? Optional.of(a.get().distanceTo(b.get()))
        : Optional.empty();
  }

  /** Conductor layers the router may place traces on. */
  public List<String> routingLayers() {
    return routing.effectiveRoutingLayers(stack);
  }

  /** True if exactly one conductor layer is routable, so jumpers are the only crossing. */
  public boolean isSingleLayer() {
    return routing.isSingleLayer(stack);
  }

  /** The router's layer structure, with only the routable conductor layers as signal layers. */
  public LayerStructure toLayerStructure() {
    return stack.toLayerStructure(routingLayers());
  }

  /** Wires that join two substrate pads. */
  public List<BondWire> jumpers() {
    return wires.stream().filter(BondWire::isJumper).toList();
  }

  /** All net names referenced anywhere in the design, sorted. */
  public Set<String> netNames() {
    Set<String> nets = new TreeSet<>();
    dies.forEach(d -> d.pads().forEach(p -> addNet(nets, p.net())));
    substratePads.forEach(p -> addNet(nets, p.net()));
    wires.forEach(w -> addNet(nets, w.net()));
    components.forEach(c -> c.pins().forEach(p -> addNet(nets, p.net())));
    return nets;
  }

  private static void addNet(Set<String> nets, String net) {
    if (net != null && !net.isBlank()) {
      nets.add(net);
    }
  }
}
