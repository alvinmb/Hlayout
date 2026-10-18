package app.freerouting.board.hybrid;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import app.freerouting.board.hybrid.ValidationIssue.Severity;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

class HybridValidatorTest {

  private static final Substrate SUBSTRATE =
      new Substrate(SubstrateTechnology.THICK_FILM, "96% alumina", 10000, 8000, 635);

  @Test
  void validDesignHasNoIssues() {
    List<ValidationIssue> issues = HybridValidator.validate(design(wires(), pads()));
    assertEquals(List.of(), issues);
    assertTrue(HybridValidator.isLoadable(issues));
  }

  @Test
  void wireJoiningTwoNetsIsAnError() {
    List<BondWire> wires = wires();
    wires.add(wire("W9", "IN", BondEndpoint.diePad("U1", "2"), BondEndpoint.substratePad("F1")));
    List<ValidationIssue> issues = HybridValidator.validate(design(wires, pads()));

    assertTrue(hasError(issues, "WIRE_NET_MISMATCH"));
    assertFalse(HybridValidator.isLoadable(issues));
  }

  @Test
  void unresolvedEndpointIsAnError() {
    List<BondWire> wires = wires();
    wires.add(wire("W9", "GND", BondEndpoint.diePad("U9", "1"), BondEndpoint.substratePad("F1")));
    List<ValidationIssue> issues = HybridValidator.validate(design(wires, pads()));
    assertTrue(hasError(issues, "WIRE_ENDPOINT_UNRESOLVED"));
  }

  @Test
  void jumperBetweenJumperLandsIsAccepted() {
    List<ValidationIssue> issues = HybridValidator.validate(design(wires(), pads()));
    assertTrue(issues.stream().noneMatch(i -> i.code().startsWith("JUMPER")));
  }

  @Test
  void jumperLandingOnBondFingerIsWarning() {
    List<BondWire> wires = wires();
    wires.add(wire("WJ2", "GND", BondEndpoint.substratePad("F1"), BondEndpoint.substratePad("JA")));
    List<ValidationIssue> issues = HybridValidator.validate(design(wires, pads()));

    assertTrue(has(issues, Severity.WARNING, "JUMPER_LAND_ROLE"));
    assertTrue(HybridValidator.isLoadable(issues));
  }

  @Test
  void thickFilmConductorsWithoutDielectricAreShorted() {
    HybridLayerStack shorted =
        new HybridLayerStack(
            List.of(
                new HybridLayer("COND1", HybridLayerKind.CONDUCTOR, 12),
                new HybridLayer("COND2", HybridLayerKind.CONDUCTOR, 12),
                new HybridLayer("RES", HybridLayerKind.RESISTOR, 15)));
    HybridDesign design =
        new HybridDesign(1, "t", SUBSTRATE, shorted, null, null, null, null, null, null);

    assertTrue(hasError(HybridValidator.validate(design), "STACK_CONDUCTORS_SHORTED"));
  }

  @Test
  void ltccSkipsConductorSeparationCheck() {
    Substrate ltcc = new Substrate(SubstrateTechnology.LTCC, "LTCC", 10000, 8000, 800);
    HybridLayerStack tape =
        new HybridLayerStack(
            List.of(
                new HybridLayer("L1", HybridLayerKind.CONDUCTOR, 10),
                new HybridLayer("L2", HybridLayerKind.CONDUCTOR, 10)));
    HybridDesign design = new HybridDesign(1, "t", ltcc, tape, null, null, null, null, null, null);

    assertFalse(hasError(HybridValidator.validate(design), "STACK_CONDUCTORS_SHORTED"));
  }

  @Test
  void componentPinOnNonConductorLayerIsAnError() {
    TwoPinComponent bad =
        new TwoPinComponent(
            "R9",
            TwoPinKind.PRINTED_RESISTOR,
            new PointUm(8000, 6000),
            Rotation.R0,
            750,
            1000,
            250,
            List.of("COND1"),
            new ComponentPin("IN", new PointUm(-375, 0), 400, 1200, "RES"),
            null,
            "1k");
    List<ValidationIssue> issues = HybridValidator.validate(design(wires(), pads(), bad));

    assertTrue(hasError(issues, "PIN_LAYER"));
    assertTrue(hasError(issues, "PIN_MISSING"));
  }

  @Test
  void componentRefdesClashingWithDieIsAnError() {
    TwoPinComponent clash =
        new TwoPinComponent(
            "U1",
            TwoPinKind.CHIP,
            new PointUm(8000, 6000),
            Rotation.R0,
            1000,
            500,
            100,
            List.of("COND1"),
            new ComponentPin("IN", new PointUm(-500, 0), 300, 500, "COND1"),
            new ComponentPin("FB", new PointUm(500, 0), 300, 500, "COND1"),
            "100n");
    assertTrue(
        hasError(HybridValidator.validate(design(wires(), pads(), clash)), "REFDES_DUPLICATE"));
  }

  @Test
  void componentOffSubstrateIsAnError() {
    TwoPinComponent off =
        new TwoPinComponent(
            "R9",
            TwoPinKind.PRINTED_RESISTOR,
            new PointUm(9900, 4000),
            Rotation.R0,
            750,
            1000,
            250,
            List.of("COND1"),
            new ComponentPin("IN", new PointUm(-375, 0), 400, 1200, "COND1"),
            new ComponentPin("FB", new PointUm(375, 0), 400, 1200, "COND1"),
            "1k");
    List<ValidationIssue> issues = HybridValidator.validate(design(wires(), pads(), off));
    assertTrue(hasError(issues, "COMPONENT_OFF_SUBSTRATE"));
  }

  @Test
  void jumperCheaperThanCrossoverIsWarning() {
    HybridDesign base = design(wires(), pads());
    HybridDesign cheapJumpers =
        new HybridDesign(
            base.schemaVersion(),
            base.name(),
            base.substrate(),
            base.stack(),
            base.dies(),
            base.substratePads(),
            base.wires(),
            base.components(),
            new HybridRoutingOptions(10.0, 2.0, true, 0, null),
            null);
    assertTrue(has(HybridValidator.validate(cheapJumpers), Severity.WARNING, "COST_ORDER"));
  }

  @Test
  void unsupportedSchemaVersionIsAnError() {
    HybridLayerStack stack = HybridLayerStack.defaultThickFilm();
    HybridDesign future =
        new HybridDesign(99, "t", SUBSTRATE, stack, null, null, null, null, null, null);
    assertTrue(hasError(HybridValidator.validate(future), "SCHEMA_VERSION"));
  }

  @Test
  void singleLayerStackDesignIsValid() {
    HybridDesign design =
        with(design(wires(), pads()), HybridLayerStack.singleLayerThickFilm(), null);

    assertTrue(design.isSingleLayer());
    assertEquals(List.of(), HybridValidator.validate(design));
  }

  @Test
  void singleLayerOnTwoConductorStackIsValid() {
    HybridDesign design =
        with(
            design(wires(), pads()),
            HybridLayerStack.defaultThickFilm(),
            HybridRoutingOptions.singleLayer("COND1"));

    assertTrue(design.isSingleLayer());
    assertEquals(List.of(), HybridValidator.validate(design));
  }

  @Test
  void padOnUnroutedConductorIsAnErrorInSingleLayerMode() {
    List<SubstratePad> pads = pads();
    pads.add(
        new SubstratePad(
            "F9", "OUT", SubstratePadRole.EXTERNAL, new PointUm(9000, 7000), 300, 300, "COND2"));
    HybridDesign design =
        with(
            design(wires(), pads),
            HybridLayerStack.defaultThickFilm(),
            HybridRoutingOptions.singleLayer("COND1"));

    assertTrue(hasError(HybridValidator.validate(design), "SUBSTRATE_PAD_NOT_ROUTABLE"));
  }

  @Test
  void componentPinOnUnroutedConductorIsAnError() {
    HybridDesign design =
        with(
            design(wires(), pads()),
            HybridLayerStack.defaultThickFilm(),
            HybridRoutingOptions.singleLayer("COND2"));

    // The fixture resistor's pins sit on COND1.
    assertTrue(hasError(HybridValidator.validate(design), "PIN_NOT_ROUTABLE"));
  }

  @Test
  void routingLayerThatIsNotConductorIsError() {
    HybridDesign design =
        with(
            design(wires(), pads()),
            HybridLayerStack.defaultThickFilm(),
            HybridRoutingOptions.singleLayer("RES"));
    List<ValidationIssue> issues = HybridValidator.validate(design);

    assertTrue(hasError(issues, "ROUTING_LAYER"));
    assertTrue(hasError(issues, "ROUTING_NO_LAYER"));
  }

  @Test
  void singleLayerWithoutJumpersIsWarning() {
    HybridDesign design =
        with(
            design(wires(), pads()),
            HybridLayerStack.singleLayerThickFilm(),
            new HybridRoutingOptions(null, null, false, null, null));
    List<ValidationIssue> issues = HybridValidator.validate(design);

    assertTrue(has(issues, Severity.WARNING, "SINGLE_LAYER_NO_JUMPERS"));
    assertTrue(HybridValidator.isLoadable(issues));
  }

  // --- fixtures -------------------------------------------------------------------------------

  private static HybridDesign with(
      HybridDesign base, HybridLayerStack stack, HybridRoutingOptions routing) {
    return new HybridDesign(
        base.schemaVersion(),
        base.name(),
        base.substrate(),
        stack,
        base.dies(),
        base.substratePads(),
        base.wires(),
        base.components(),
        routing,
        base.rules());
  }

  private static HybridDesign design(
      List<BondWire> wires, List<SubstratePad> pads, TwoPinComponent... extra) {
    List<TwoPinComponent> components = new ArrayList<>();
    components.add(HybridModelTest.resistor(Rotation.R0));
    components.addAll(List.of(extra));
    return new HybridDesign(
        HybridDesign.CURRENT_SCHEMA_VERSION,
        "test",
        SUBSTRATE,
        HybridLayerStack.defaultThickFilm(),
        List.of(die()),
        pads,
        wires,
        components,
        null,
        null);
  }

  private static Die die() {
    return new Die(
        "U1",
        "opamp-die",
        new PointUm(4000, 3000),
        Rotation.R0,
        2000,
        2000,
        250,
        150,
        List.of(
            new BondPad("1", "GND", new PointUm(500, 150), 100, 100),
            new BondPad("2", "IN", new PointUm(1500, 150), 100, 100)));
  }

  private static List<SubstratePad> pads() {
    return new ArrayList<>(
        List.of(
            pad("F1", "GND", SubstratePadRole.BOND_FINGER, 4500, 2500),
            pad("F2", "IN", SubstratePadRole.BOND_FINGER, 5500, 2500),
            pad("JA", "GND", SubstratePadRole.JUMPER_LAND, 3500, 6500),
            pad("JB", "GND", SubstratePadRole.JUMPER_LAND, 3500, 7300)));
  }

  private static SubstratePad pad(String id, String net, SubstratePadRole role, int x, int y) {
    return new SubstratePad(id, net, role, new PointUm(x, y), 150, 200, "COND1");
  }

  private static List<BondWire> wires() {
    return new ArrayList<>(
        List.of(
            wire("W1", "GND", BondEndpoint.diePad("U1", "1"), BondEndpoint.substratePad("F1")),
            wire("W2", "IN", BondEndpoint.diePad("U1", "2"), BondEndpoint.substratePad("F2")),
            wire("WJ1", "GND", BondEndpoint.substratePad("JA"), BondEndpoint.substratePad("JB"))));
  }

  private static BondWire wire(String id, String net, BondEndpoint from, BondEndpoint to) {
    return new BondWire(id, net, from, to, null, false);
  }

  private static boolean hasError(List<ValidationIssue> issues, String code) {
    return has(issues, Severity.ERROR, code);
  }

  private static boolean has(List<ValidationIssue> issues, Severity severity, String code) {
    return issues.stream().anyMatch(i -> i.severity() == severity && i.code().equals(code));
  }
}
