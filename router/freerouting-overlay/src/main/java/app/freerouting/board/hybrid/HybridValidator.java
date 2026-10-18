package app.freerouting.board.hybrid;

import app.freerouting.board.hybrid.ValidationIssue.Severity;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;

/**
 * Structural validation of a {@link HybridDesign}: references resolve, ids are unique, layers have
 * the right kind, nets agree at both ends of every wire, and geometry sits on the substrate.
 *
 * <p>This is not DRC: spacing, wire-length and power-density limits belong to the Phase 2 rule set.
 * It answers "can the router load this design?".
 */
public final class HybridValidator {

  private final HybridDesign design;
  private final List<ValidationIssue> issues = new ArrayList<>();

  private HybridValidator(HybridDesign design) {
    this.design = design;
  }

  /** Validates the design and returns every issue found, errors and warnings alike. */
  public static List<ValidationIssue> validate(HybridDesign design) {
    HybridValidator validator = new HybridValidator(Objects.requireNonNull(design, "design"));
    validator.run();
    return List.copyOf(validator.issues);
  }

  /** True if the issue list contains no errors. */
  public static boolean isLoadable(List<ValidationIssue> issues) {
    return issues.stream().noneMatch(i -> i.severity() == Severity.ERROR);
  }

  private void run() {
    checkSchemaVersion();
    checkSubstrate();
    checkStack();
    checkDies();
    checkSubstratePads();
    checkWires();
    checkComponents();
    checkRouting();
    checkRules();
  }

  private void checkSchemaVersion() {
    if (design.schemaVersion() != HybridDesign.CURRENT_SCHEMA_VERSION) {
      error(
          "SCHEMA_VERSION",
          design.name(),
          "schemaVersion "
              + design.schemaVersion()
              + " not supported; expected "
              + HybridDesign.CURRENT_SCHEMA_VERSION);
    }
  }

  private void checkSubstrate() {
    Substrate s = design.substrate();
    if (s == null) {
      error("SUBSTRATE_MISSING", design.name(), "design has no substrate");
      return;
    }
    if (s.widthUm() <= 0 || s.heightUm() <= 0) {
      error("SUBSTRATE_SIZE", "substrate", "width and height must be positive");
    }
    if (s.thicknessUm() <= 0) {
      warning("SUBSTRATE_THICKNESS", "substrate", "thickness not set");
    }
  }

  private void checkStack() {
    List<HybridLayer> layers = design.stack().layers();
    if (layers.isEmpty()) {
      error("STACK_EMPTY", "stack", "layer stack is empty");
      return;
    }
    Set<String> names = new HashSet<>();
    for (HybridLayer layer : layers) {
      String ref = layer.name() == null ? "?" : layer.name();
      if (isBlank(layer.name())) {
        error("LAYER_NAME", ref, "layer has no name");
      } else if (!names.add(layer.name())) {
        error("LAYER_DUPLICATE", ref, "duplicate layer name");
      }
      if (layer.kind() == null) {
        error("LAYER_KIND", ref, "layer has no kind");
      }
      if (layer.thicknessUm() <= 0) {
        warning("LAYER_THICKNESS", ref, "thickness not set");
      }
    }
    if (design.stack().conductorLayers().isEmpty()) {
      error("STACK_NO_CONDUCTOR", "stack", "stack has no CONDUCTOR layer");
    }
    boolean hasPrintedResistor =
        design.components().stream().anyMatch(c -> c.kind() == TwoPinKind.PRINTED_RESISTOR);
    if (hasPrintedResistor
        && layers.stream().noneMatch(l -> l.kind() == HybridLayerKind.RESISTOR)) {
      warning("STACK_NO_RESISTOR_LAYER", "stack", "printed resistors but no RESISTOR layer");
    }
    checkConductorSeparation();
  }

  /** Thick film: two conductor prints must be separated by a dielectric print. */
  private void checkConductorSeparation() {
    if (design.substrate() == null
        || design.substrate().technology() != SubstrateTechnology.THICK_FILM) {
      return;
    }
    List<HybridLayer> conductors = design.stack().conductorLayers();
    for (int i = 1; i < conductors.size(); i++) {
      String lower = conductors.get(i - 1).name();
      String upper = conductors.get(i).name();
      if (!design.stack().hasDielectricBetween(lower, upper)) {
        error(
            "STACK_CONDUCTORS_SHORTED",
            lower + "/" + upper,
            "no DIELECTRIC layer between conductor layers");
      }
    }
  }

  private void checkDies() {
    Set<String> refdes = new HashSet<>();
    for (Die die : design.dies()) {
      String ref = die.refdes() == null ? "?" : die.refdes();
      if (isBlank(die.refdes())) {
        error("DIE_REFDES", ref, "die has no refdes");
      } else if (!refdes.add(die.refdes())) {
        error("REFDES_DUPLICATE", ref, "duplicate die refdes");
      }
      if (die.widthUm() <= 0 || die.heightUm() <= 0) {
        error("DIE_SIZE", ref, "die width and height must be positive");
      }
      if (die.pads().isEmpty()) {
        warning("DIE_NO_PADS", ref, "die has no bond pads");
      }
      for (PointUm corner : die.corners()) {
        if (!onSubstrate(corner)) {
          error("DIE_OFF_SUBSTRATE", ref, "die extends past the substrate outline");
          break;
        }
      }
      checkDiePads(die, ref);
    }
  }

  private void checkDiePads(Die die, String dieRef) {
    Set<String> ids = new HashSet<>();
    for (BondPad pad : die.pads()) {
      String ref = dieRef + "." + (pad.id() == null ? "?" : pad.id());
      if (isBlank(pad.id())) {
        error("PAD_ID", ref, "die pad has no id");
      } else if (!ids.add(pad.id())) {
        error("PAD_DUPLICATE", ref, "duplicate pad id on die");
      }
      if (pad.widthUm() <= 0 || pad.heightUm() <= 0) {
        error("PAD_SIZE", ref, "pad opening must be positive");
      }
      PointUm p = pad.position();
      if (p.x() < 0 || p.y() < 0 || p.x() > die.widthUm() || p.y() > die.heightUm()) {
        error("PAD_OFF_DIE", ref, "pad centre lies outside the die");
      }
    }
  }

  private void checkSubstratePads() {
    Set<String> ids = new HashSet<>();
    for (SubstratePad pad : design.substratePads()) {
      String ref = pad.id() == null ? "?" : pad.id();
      if (isBlank(pad.id())) {
        error("SUBSTRATE_PAD_ID", ref, "substrate pad has no id");
      } else if (!ids.add(pad.id())) {
        error("SUBSTRATE_PAD_DUPLICATE", ref, "duplicate substrate pad id");
      }
      if (pad.widthUm() <= 0 || pad.heightUm() <= 0) {
        error("SUBSTRATE_PAD_SIZE", ref, "pad width and height must be positive");
      }
      if (!design.stack().isKind(pad.layer(), HybridLayerKind.CONDUCTOR)) {
        error("SUBSTRATE_PAD_LAYER", ref, "layer '" + pad.layer() + "' is not a CONDUCTOR layer");
      } else if (!design.routingLayers().contains(pad.layer())) {
        error("SUBSTRATE_PAD_NOT_ROUTABLE", ref, "layer '" + pad.layer() + "' is not routable");
      }
      if (!onSubstrate(pad.position())) {
        error("SUBSTRATE_PAD_OFF_SUBSTRATE", ref, "pad centre lies outside the substrate");
      }
      if (isBlank(pad.net())) {
        warning("SUBSTRATE_PAD_NO_NET", ref, "pad has no net");
      }
    }
  }

  private void checkWires() {
    Set<String> ids = new HashSet<>();
    for (BondWire wire : design.wires()) {
      String ref = wire.id() == null ? "?" : wire.id();
      if (isBlank(wire.id())) {
        error("WIRE_ID", ref, "wire has no id");
      } else if (!ids.add(wire.id())) {
        error("WIRE_DUPLICATE", ref, "duplicate wire id");
      }
      if (wire.spec().diameterUm() <= 0) {
        error("WIRE_DIAMETER", ref, "wire diameter must be positive");
      }
      boolean fromOk = checkEndpoint(wire.from(), ref, "from");
      boolean toOk = checkEndpoint(wire.to(), ref, "to");
      if (!fromOk || !toOk) {
        continue;
      }
      checkWireNets(wire, ref);
      checkWireRoles(wire, ref);
      design
          .wireLengthUm(wire)
          .filter(length -> length < 1.0)
          .ifPresent(length -> error("WIRE_ZERO_LENGTH", ref, "both ends are at the same point"));
    }
  }

  private boolean checkEndpoint(BondEndpoint end, String wireRef, String which) {
    if (end == null) {
      error("WIRE_ENDPOINT_MISSING", wireRef, "'" + which + "' endpoint missing");
      return false;
    }
    if (design.position(end).isEmpty()) {
      error("WIRE_ENDPOINT_UNRESOLVED", wireRef, "'" + which + "' pad " + end + " not found");
      return false;
    }
    return true;
  }

  private void checkWireNets(BondWire wire, String ref) {
    String fromNet = design.net(wire.from()).orElse(null);
    String toNet = design.net(wire.to()).orElse(null);
    if (!isBlank(fromNet) && !isBlank(toNet) && !fromNet.equals(toNet)) {
      error(
          "WIRE_NET_MISMATCH",
          ref,
          "shorts " + fromNet + " (" + wire.from() + ") to " + toNet + " (" + wire.to() + ")");
    }
    String padNet = !isBlank(fromNet) ? fromNet : toNet;
    if (!isBlank(wire.net()) && !isBlank(padNet) && !wire.net().equals(padNet)) {
      error("WIRE_NET_LABEL", ref, "wire net " + wire.net() + " differs from pad net " + padNet);
    }
  }

  private void checkWireRoles(BondWire wire, String ref) {
    WirePurpose purpose = wire.purpose();
    if (purpose == WirePurpose.JUMPER) {
      for (BondEndpoint end : List.of(wire.from(), wire.to())) {
        Optional<SubstratePad> pad = design.substratePad(end.pad());
        if (pad.isPresent() && pad.get().role() != SubstratePadRole.JUMPER_LAND) {
          warning("JUMPER_LAND_ROLE", ref, "jumper lands on " + pad.get().role() + " pad " + end);
        }
      }
    } else if (purpose == WirePurpose.DIE_BOND) {
      BondEndpoint substrateEnd = wire.from().isDiePad() ? wire.to() : wire.from();
      design
          .substratePad(substrateEnd.pad())
          .filter(p -> p.role() != SubstratePadRole.BOND_FINGER)
          .ifPresent(
              p -> warning("BOND_FINGER_ROLE", ref, "die bond lands on " + p.role() + " pad"));
    }
  }

  /**
   * Two-pin components arrive sized and rule-checked by HLayout; only check that the router can
   * load them: unique refdes, two pins on conductor layers, a body, known obstacle layers.
   */
  private void checkComponents() {
    Set<String> refdes = new HashSet<>();
    design.dies().forEach(d -> refdes.add(d.refdes()));
    for (TwoPinComponent c : design.components()) {
      String ref = c.refdes() == null ? "?" : c.refdes();
      if (isBlank(c.refdes())) {
        error("COMPONENT_REFDES", ref, "component has no refdes");
      } else if (!refdes.add(c.refdes())) {
        error("REFDES_DUPLICATE", ref, "duplicate refdes");
      }
      if (c.bodyLengthUm() <= 0 || c.bodyWidthUm() <= 0) {
        error("COMPONENT_BODY_SIZE", ref, "body length and width must be positive");
      }
      if (c.keepoutUm() < 0) {
        error("COMPONENT_KEEPOUT", ref, "keep-out must not be negative");
      }
      for (String layer : c.obstacleLayers()) {
        if (design.stack().find(layer).isEmpty()) {
          error("COMPONENT_OBSTACLE_LAYER", ref, "obstacle layer '" + layer + "' not in stack");
        }
      }
      if (c.obstacleLayers().isEmpty()) {
        warning("COMPONENT_NO_OBSTACLE", ref, "body blocks no layer; traces may route through it");
      }
      checkPin(c, c.pinA(), ref + ".A");
      checkPin(c, c.pinB(), ref + ".B");
      if (c.pinA() != null
          && c.pinB() != null
          && !isBlank(c.pinA().net())
          && c.pinA().net().equals(c.pinB().net())) {
        warning("COMPONENT_SHORTED", ref, "both pins on net " + c.pinA().net());
      }
      for (PointUm corner : c.bodyCorners()) {
        if (!onSubstrate(corner)) {
          error("COMPONENT_OFF_SUBSTRATE", ref, "body extends past the substrate outline");
          break;
        }
      }
    }
  }

  private void checkPin(TwoPinComponent component, ComponentPin pin, String ref) {
    if (pin == null) {
      error("PIN_MISSING", ref, "pin missing");
      return;
    }
    if (pin.widthUm() <= 0 || pin.heightUm() <= 0) {
      error("PIN_SIZE", ref, "pin width and height must be positive");
    }
    if (!design.stack().isKind(pin.layer(), HybridLayerKind.CONDUCTOR)) {
      error("PIN_LAYER", ref, "layer '" + pin.layer() + "' is not a CONDUCTOR layer");
    } else if (!design.routingLayers().contains(pin.layer())) {
      error("PIN_NOT_ROUTABLE", ref, "layer '" + pin.layer() + "' is not routable");
    }
    if (!onSubstrate(component.pinPosition(pin))) {
      error("PIN_OFF_SUBSTRATE", ref, "pin centre lies outside the substrate");
    }
    if (isBlank(pin.net())) {
      warning("PIN_NO_NET", ref, "pin has no net");
    }
  }

  private void checkRouting() {
    HybridRoutingOptions routing = design.routing();
    for (String layer : routing.routingLayers()) {
      if (!design.stack().isKind(layer, HybridLayerKind.CONDUCTOR)) {
        error("ROUTING_LAYER", layer, "routing layer is not a CONDUCTOR layer in the stack");
      }
    }
    if (!design.stack().conductorLayers().isEmpty() && design.routingLayers().isEmpty()) {
      error("ROUTING_NO_LAYER", "routing", "no routable conductor layer");
    }
    if (routing.maxJumpers() < 0) {
      error("ROUTING_MAX_JUMPERS", "routing", "maxJumpers must not be negative");
    }
    if (!routing.isOrdered(design.stack())) {
      String expected =
          design.isSingleLayer() ? "trace (1) < jumper" : "trace (1) < crossover < jumper";
      warning("COST_ORDER", "routing", "expected " + expected + "; jumpers may be over-used");
    }
    if (design.isSingleLayer() && !routing.jumpersAllowed()) {
      warning(
          "SINGLE_LAYER_NO_JUMPERS",
          "routing",
          "single-layer routing without jumpers completes only planar layouts");
    }
  }

  private void checkRules() {
    for (String problem : design.rules().problems()) {
      error("RULES_INVALID", "rules", problem);
    }
  }

  private boolean onSubstrate(PointUm p) {
    return design.substrate() == null || design.substrate().contains(p);
  }

  private static boolean isBlank(String s) {
    return s == null || s.isBlank();
  }

  private void error(String code, String ref, String message) {
    issues.add(new ValidationIssue(Severity.ERROR, code, ref, message));
  }

  private void warning(String code, String ref, String message) {
    issues.add(new ValidationIssue(Severity.WARNING, code, ref, message));
  }
}
