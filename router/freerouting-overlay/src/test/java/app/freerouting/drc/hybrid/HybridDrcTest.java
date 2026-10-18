package app.freerouting.drc.hybrid;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import app.freerouting.board.hybrid.BondEndpoint;
import app.freerouting.board.hybrid.BondPad;
import app.freerouting.board.hybrid.BondType;
import app.freerouting.board.hybrid.BondWire;
import app.freerouting.board.hybrid.ComponentPin;
import app.freerouting.board.hybrid.Die;
import app.freerouting.board.hybrid.HybridDesign;
import app.freerouting.board.hybrid.HybridLayerStack;
import app.freerouting.board.hybrid.HybridValidator;
import app.freerouting.board.hybrid.PointUm;
import app.freerouting.board.hybrid.Rotation;
import app.freerouting.board.hybrid.Substrate;
import app.freerouting.board.hybrid.SubstratePad;
import app.freerouting.board.hybrid.SubstratePadRole;
import app.freerouting.board.hybrid.SubstrateTechnology;
import app.freerouting.board.hybrid.TwoPinComponent;
import app.freerouting.board.hybrid.TwoPinKind;
import app.freerouting.board.hybrid.ValidationIssue;
import app.freerouting.board.hybrid.WireMaterial;
import app.freerouting.board.hybrid.WireSpec;
import app.freerouting.io.hybrid.HybridJson;
import app.freerouting.rules.hybrid.BondWireRules;
import app.freerouting.rules.hybrid.HybridRules;
import app.freerouting.rules.hybrid.JumperRules;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

class HybridDrcTest {

  private static final Substrate SUBSTRATE =
      new Substrate(SubstrateTechnology.THICK_FILM, "96% alumina", 10000, 8000, 635);

  // Die U1 at (4000, 3000), 2000 x 2000. Pad 1 bottom edge -> (4500, 3150); pad 2 bottom ->
  // (5500, 3150); pad 3 right edge -> (5850, 3500).

  @Test
  void bothFixturesAreDrcClean() throws IOException {
    for (String name : List.of("single-die-op-amp", "single-layer-crossing")) {
      HybridDesign design = fixture(name);
      assertTrue(HybridValidator.isLoadable(HybridValidator.validate(design)), name);
      List<ValidationIssue> issues = HybridDrc.check(design);
      assertEquals(List.of(), issues, () -> name + ": " + issues);
    }
  }

  @Test
  void cleanBaseDesignHasNoViolations() {
    assertEquals(List.of(), HybridDrc.check(base().build()));
  }

  @Test
  void wireTooShortAndTooLong() {
    // F1 300 µm below pad 1: wire 300 < 500. F2 2800 µm below pad 2: wire > 2500.
    Builder b = base();
    b.pads.set(0, finger("F1", "GND", 4500, 2850));
    b.pads.set(1, finger("F2", "IN", 5500, 350));
    b.rules = rules(new BondWireRules(null, 2500, null, null, null, null, null, null, null, 3000));
    List<ValidationIssue> issues = HybridDrc.check(b.build());

    assertTrue(has(issues, "WIRE_TOO_SHORT", "W1"));
    assertTrue(has(issues, "WIRE_TOO_LONG", "W2"));
  }

  @Test
  void wireLeavingThePadAtTooSharpAnAngleIsFlagged() {
    // Pad 1 is on the bottom edge (normal points down); a finger 700 down and 900 left is ~52°.
    Builder b = base();
    b.pads.set(0, finger("F1", "GND", 3600, 2450));
    assertTrue(has(HybridDrc.check(b.build()), "WIRE_ANGLE", "W1"));

    // 45° exactly is allowed.
    b.pads.set(0, finger("F1", "GND", 3800, 2450));
    assertFalse(has(HybridDrc.check(b.build()), "WIRE_ANGLE", "W1"));
  }

  @Test
  void fingerTooCloseToOrTooFarFromTheDie() {
    Builder near = base();
    near.pads.set(0, finger("F1", "GND", 4500, 2850)); // 150 µm from the die edge
    near.rules = rules(new BondWireRules(100, null, null, null, null, null, null, 0, null, null));
    assertTrue(hasCode(HybridDrc.check(near.build()), "DIE_TO_FINGER_MIN"));

    Builder far = base();
    far.pads.set(0, finger("F1", "GND", 4500, 1000)); // 2000 µm from the die edge
    assertTrue(hasCode(HybridDrc.check(far.build()), "DIE_TO_FINGER_MAX"));
  }

  @Test
  void crossingWiresAreAnErrorByDefault() {
    Builder b = base();
    // Swap the fingers of pads 1 and 2 (different nets): the two wires now cross.
    b.pads.set(0, finger("F1", "GND", 5300, 2300));
    b.pads.set(1, finger("F2", "IN", 4700, 2300));
    b.rules = rules(new BondWireRules(null, null, 90.0, null, null, null, null, 0, null, null));
    assertTrue(has(HybridDrc.check(b.build()), "WIRE_CROSSING", "W1/W2"));
  }

  @Test
  void allowedCrossingNeedsLoopSeparation() {
    BondWireRules allowCrossings =
        new BondWireRules(null, null, 90.0, null, true, 100, null, 0, null, null);
    Builder b = base();
    b.pads.set(0, finger("F1", "GND", 5300, 2300));
    b.pads.set(1, finger("F2", "IN", 4700, 2300));
    b.rules = rules(allowCrossings);
    assertTrue(has(HybridDrc.check(b.build()), "WIRE_CROSSING_LOOP", "W1/W2"));

    WireSpec high = new WireSpec(WireMaterial.GOLD, 25, BondType.BALL_WEDGE, 300);
    b.wires.set(1, dieBond("W2", "IN", "2", "F2", high));
    List<ValidationIssue> issues = HybridDrc.check(b.build());
    assertFalse(hasCode(issues, "WIRE_CROSSING"));
    assertFalse(hasCode(issues, "WIRE_CROSSING_LOOP"));
  }

  @Test
  void parallelWiresTooCloseViolateSpacing() {
    // Pad 9 sits 60 µm right of pad 1; both wires run straight down. Centre-line gap 60, wire gap
    // 60 - 25 = 35 µm < required 2 x 25 = 50 µm.
    Builder b = base();
    b.diePads.add(new BondPad("9", "OUT", new PointUm(560, 150), 50, 50));
    b.pads.add(finger("F9", "OUT", 4560, 2500));
    b.wires.add(dieBond("W9", "OUT", "9", "F9", null));
    b.rules = rules(new BondWireRules(null, null, null, null, null, null, 10, 0, null, null));
    List<ValidationIssue> issues = HybridDrc.check(b.build());
    assertTrue(has(issues, "WIRE_SPACING", "W1/W9"));
  }

  @Test
  void bondFingersTooCloseTogetherViolatePitch() {
    Builder b = base();
    b.pads.set(1, finger("F2", "IN", 4650, 2500)); // 150 from F1, below the 175 pitch
    b.wires.set(1, dieBond("W2", "IN", "2", "F2", null));
    b.rules = rules(new BondWireRules(null, null, 90.0, null, null, null, 175, 0, null, null));
    List<ValidationIssue> issues = HybridDrc.check(b.build());
    assertTrue(has(issues, "FINGER_PITCH", "F1/F2"));
  }

  @Test
  void secondBondTooCloseToAnotherObjectViolatesCapillaryClearance() {
    // X1 spans y 2050..2250: its top edge is exactly 250 µm from F1's centre (4500, 2500). Allowed.
    Builder b = base();
    b.pads.add(external("X1", "NC", 4500, 2150, 200, 200));
    assertFalse(hasCode(HybridDrc.check(b.build()), "CAPILLARY_CLEARANCE"));

    // X1 spans y 2200..2300: 200 µm from F1's centre. Too close.
    b.pads.set(b.pads.size() - 1, external("X1", "NC", 4500, 2250, 200, 100));
    assertTrue(has(HybridDrc.check(b.build()), "CAPILLARY_CLEARANCE", "W1@F1"));
  }

  @Test
  void jumperRulesApplyToSubstrateToSubstrateWires() {
    Builder b = base();
    b.pads.add(land("JA", "GND", 2000, 6000));
    b.pads.add(land("JB", "GND", 2000, 6300)); // 300 µm: too short
    WireSpec low = new WireSpec(WireMaterial.GOLD, 25, BondType.BALL_WEDGE, 100);
    b.wires.add(jumper("WJ1", "GND", "JA", "JB", low));
    List<ValidationIssue> issues = HybridDrc.check(b.build());

    assertTrue(has(issues, "JUMPER_TOO_SHORT", "WJ1"));
    assertTrue(has(issues, "JUMPER_LOOP_HEIGHT", "WJ1"));
    assertFalse(hasCode(issues, "WIRE_TOO_SHORT"));
  }

  @Test
  void jumperTooLongIsFlagged() {
    Builder b = base();
    b.pads.add(land("JA", "GND", 1000, 6000));
    b.pads.add(land("JB", "GND", 1000, 2500)); // 3500 > 3000
    b.wires.add(jumper("WJ1", "GND", "JA", "JB", null));
    b.rules = new HybridRules(null, null, new JumperRules(null, 3000, 100));
    assertTrue(has(HybridDrc.check(b.build()), "JUMPER_TOO_LONG", "WJ1"));
  }

  @Test
  void padsOfDifferentNetsTooCloseOrTouching() {
    Builder b = base();
    b.pads.add(external("X1", "VCC", 8000, 6000, 400, 400));
    b.pads.add(external("X2", "OUT", 8500, 6000, 400, 400)); // 100 µm gap < 150
    b.pads.add(external("X3", "IN", 8000, 6350, 400, 400)); // touches X1
    List<ValidationIssue> issues = HybridDrc.check(b.build());

    assertTrue(has(issues, "CONDUCTOR_SPACING", "X1/X2"));
    assertTrue(has(issues, "CONDUCTOR_SPACING", "X1/X3"));
  }

  @Test
  void sameNetPadsMayTouchAndOtherLayersDoNotInteract() {
    Builder b = base();
    b.pads.add(external("X1", "VCC", 8000, 6000, 400, 400));
    b.pads.add(external("X2", "VCC", 8300, 6000, 400, 400));
    b.pads.add(external2("X3", "OUT", 8000, 6000, 400, 400));
    assertFalse(hasCode(HybridDrc.check(b.build()), "CONDUCTOR_SPACING"));
  }

  @Test
  void conductorTooCloseToTheSubstrateEdge() {
    Builder b = base();
    b.pads.add(
        external(
            "X1", "VCC", 9700, 4000, 300, 300)); // right edge at 9850: 150 from the substrate edge
    assertTrue(has(HybridDrc.check(b.build()), "EDGE_CLEARANCE", "X1"));
  }

  @Test
  void foreignPadInsideComponentKeepoutButOwnPinsAreFine() {
    Builder b = base();
    b.components.add(resistor("R1", 8000, 5000));
    assertFalse(hasCode(HybridDrc.check(b.build()), "COMPONENT_KEEPOUT"));

    // Body 750 x 1000 at (8000, 5000), keep-out 250: zone x 7375..8625. X1 spans x 8600..9000.
    b.pads.add(external("X1", "OUT", 8800, 5000, 400, 200));
    assertTrue(has(HybridDrc.check(b.build()), "COMPONENT_KEEPOUT", "X1"));
  }

  @Test
  void padInsideDieAttachKeepout() {
    Builder b = base();
    // Die spans y 3000..5000; keep-out 150 reaches x 3850. Pad at x 3800, 200 wide, spans to 3900.
    b.pads.add(external("X1", "OUT", 3800, 4000, 200, 200));
    assertTrue(has(HybridDrc.check(b.build()), "DIE_KEEPOUT", "X1"));
  }

  @Test
  void inconsistentRulesAreRejectedByTheValidator() {
    Builder b = base();
    b.rules = new HybridRules(null, null, new JumperRules(4000, 3000, 150));
    List<ValidationIssue> issues = HybridValidator.validate(b.build());
    assertTrue(issues.stream().anyMatch(i -> i.code().equals("RULES_INVALID")));
    assertFalse(HybridValidator.isLoadable(issues));
  }

  @Test
  void resultsAreDeterministic() throws IOException {
    Builder b = base();
    b.pads.set(0, finger("F1", "GND", 5300, 2300));
    b.pads.set(1, finger("F2", "IN", 4700, 2300));
    HybridDesign design = b.build();
    assertEquals(HybridDrc.check(design), HybridDrc.check(design));
    HybridDesign copy = HybridJson.fromString(HybridJson.toString(design));
    assertEquals(HybridDrc.check(design), HybridDrc.check(copy));
  }

  // --- builders ---------------------------------------------------------------------------------

  /** Mutable test builder; {@link #build()} snapshots it into an immutable design. */
  private static final class Builder {
    final List<BondPad> diePads = new ArrayList<>();
    final List<SubstratePad> pads = new ArrayList<>();
    final List<BondWire> wires = new ArrayList<>();
    final List<TwoPinComponent> components = new ArrayList<>();
    HybridRules rules;

    HybridDesign build() {
      Die die =
          new Die("U1", "die", new PointUm(4000, 3000), Rotation.R0, 2000, 2000, 250, 150, diePads);
      return new HybridDesign(
          HybridDesign.CURRENT_SCHEMA_VERSION,
          "drc-test",
          SUBSTRATE,
          HybridLayerStack.defaultThickFilm(),
          List.of(die),
          pads,
          wires,
          components,
          null,
          rules);
    }
  }

  /** Die with three pads, each bonded straight out to its own finger 650 µm away. */
  private static Builder base() {
    Builder b = new Builder();
    b.diePads.add(new BondPad("1", "GND", new PointUm(500, 150), 100, 100));
    b.diePads.add(new BondPad("2", "IN", new PointUm(1500, 150), 100, 100));
    b.diePads.add(new BondPad("3", "FB", new PointUm(1850, 500), 100, 100));
    b.pads.add(finger("F1", "GND", 4500, 2500));
    b.pads.add(finger("F2", "IN", 5500, 2500));
    b.pads.add(finger("F3", "FB", 6500, 3500));
    b.wires.add(dieBond("W1", "GND", "1", "F1", null));
    b.wires.add(dieBond("W2", "IN", "2", "F2", null));
    b.wires.add(dieBond("W3", "FB", "3", "F3", null));
    return b;
  }

  private static HybridRules rules(BondWireRules bondWire) {
    return new HybridRules(null, bondWire, null);
  }

  private static SubstratePad finger(String id, String net, int x, int y) {
    return new SubstratePad(
        id, net, SubstratePadRole.BOND_FINGER, new PointUm(x, y), 150, 150, "COND1");
  }

  private static SubstratePad external(String id, String net, int x, int y, int w, int h) {
    return new SubstratePad(id, net, SubstratePadRole.EXTERNAL, new PointUm(x, y), w, h, "COND1");
  }

  private static SubstratePad external2(String id, String net, int x, int y, int w, int h) {
    return new SubstratePad(id, net, SubstratePadRole.EXTERNAL, new PointUm(x, y), w, h, "COND2");
  }

  private static SubstratePad land(String id, String net, int x, int y) {
    return new SubstratePad(
        id, net, SubstratePadRole.JUMPER_LAND, new PointUm(x, y), 200, 200, "COND1");
  }

  private static BondWire dieBond(String id, String net, String pad, String finger, WireSpec spec) {
    return new BondWire(
        id, net, BondEndpoint.diePad("U1", pad), BondEndpoint.substratePad(finger), spec, false);
  }

  private static BondWire jumper(String id, String net, String from, String to, WireSpec spec) {
    return new BondWire(
        id, net, BondEndpoint.substratePad(from), BondEndpoint.substratePad(to), spec, false);
  }

  private static TwoPinComponent resistor(String refdes, int x, int y) {
    return new TwoPinComponent(
        refdes,
        TwoPinKind.PRINTED_RESISTOR,
        new PointUm(x, y),
        Rotation.R0,
        750,
        1000,
        250,
        List.of("COND1"),
        new ComponentPin("IN", new PointUm(-375, 0), 400, 1200, "COND1"),
        new ComponentPin("FB", new PointUm(375, 0), 400, 1200, "COND1"),
        "1k");
  }

  private static boolean has(List<ValidationIssue> issues, String code, String ref) {
    return issues.stream().anyMatch(i -> i.code().equals(code) && i.ref().equals(ref));
  }

  private static boolean hasCode(List<ValidationIssue> issues, String code) {
    return issues.stream().anyMatch(i -> i.code().equals(code));
  }

  private static HybridDesign fixture(String name) throws IOException {
    String resource = "/hybrid/" + name + ".hybrid.json";
    try (InputStream in = HybridDrcTest.class.getResourceAsStream(resource);
        Reader reader = new InputStreamReader(in, StandardCharsets.UTF_8)) {
      return HybridJson.read(reader);
    }
  }
}
