package app.freerouting.board.hybrid;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import app.freerouting.board.model.structure.LayerStructure;
import app.freerouting.rules.hybrid.HybridRules;
import java.util.List;
import org.junit.jupiter.api.Test;

class HybridModelTest {

  @Test
  void rotationTurnsCounterClockwise() {
    PointUm p = new PointUm(100, 20);
    assertEquals(p, Rotation.R0.apply(p));
    assertEquals(new PointUm(-20, 100), Rotation.R90.apply(p));
    assertEquals(new PointUm(-100, -20), Rotation.R180.apply(p));
    assertEquals(new PointUm(20, -100), Rotation.R270.apply(p));
  }

  @Test
  void diePadPositionFollowsOriginAndRotation() {
    BondPad pad = new BondPad("1", "GND", new PointUm(500, 150), 100, 100);
    PointUm origin = new PointUm(4000, 3000);
    Die upright = new Die("U1", "die", origin, Rotation.R0, 2000, 2000, 250, 150, List.of(pad));
    Die turned = new Die("U2", "die", origin, Rotation.R90, 2000, 2000, 250, 150, List.of(pad));

    assertEquals(new PointUm(4500, 3150), upright.padPosition(pad));
    assertEquals(new PointUm(3850, 3500), turned.padPosition(pad));
    assertEquals(new PointUm(6000, 5000), upright.corners().get(2));
  }

  @Test
  void wirePurposeComesFromEndpoints() {
    BondEndpoint die = BondEndpoint.diePad("U1", "1");
    BondEndpoint finger = BondEndpoint.substratePad("F1");

    assertEquals(WirePurpose.DIE_BOND, WirePurpose.of(die, finger));
    assertEquals(WirePurpose.DIE_BOND, WirePurpose.of(finger, die));
    assertEquals(WirePurpose.DIE_TO_DIE, WirePurpose.of(die, BondEndpoint.diePad("U2", "3")));
    BondEndpoint land = BondEndpoint.substratePad("JA");
    assertEquals(WirePurpose.JUMPER, WirePurpose.of(finger, land));
    assertTrue(new BondWire("WJ", "GND", finger, land, null, false).isJumper());
  }

  @Test
  void bondWireDefaultsToGoldSpec() {
    BondEndpoint die = BondEndpoint.diePad("U1", "1");
    BondWire wire = new BondWire("W1", "GND", die, BondEndpoint.substratePad("F1"), null, false);
    assertEquals(WireSpec.DEFAULT_GOLD, wire.spec());
  }

  @Test
  void componentPinsFollowCentreAndRotation() {
    TwoPinComponent flat = resistor(Rotation.R0);
    TwoPinComponent upright = resistor(Rotation.R90);

    assertEquals(new PointUm(7625, 2500), flat.pinPosition(flat.pinA()));
    assertEquals(new PointUm(8375, 2500), flat.pinPosition(flat.pinB()));
    assertEquals(new PointUm(8000, 2125), upright.pinPosition(upright.pinA()));
    assertEquals(new PointUm(8000, 2875), upright.pinPosition(upright.pinB()));
  }

  @Test
  void componentBodyCornersSpanLengthAndWidth() {
    TwoPinComponent flat = resistor(Rotation.R0);
    TwoPinComponent upright = resistor(Rotation.R90);

    assertEquals(new PointUm(7625, 2000), flat.bodyCorners().get(0));
    assertEquals(new PointUm(8375, 3000), flat.bodyCorners().get(2));
    // Quarter turn: length now runs along y, width along x.
    assertEquals(new PointUm(8500, 2125), upright.bodyCorners().get(0));
    assertEquals(new PointUm(7500, 2875), upright.bodyCorners().get(2));
  }

  @Test
  void componentDefaultsAndMissingPin() {
    TwoPinComponent bare =
        new TwoPinComponent("C1", null, null, null, 500, 250, 0, null, null, null, null);
    assertEquals(TwoPinKind.PRINTED_RESISTOR, bare.kind());
    assertEquals(Rotation.R0, bare.rotation());
    assertTrue(bare.obstacleLayers().isEmpty());
    assertTrue(bare.pins().isEmpty());
  }

  @Test
  void onlyConductorLayersBecomeSignalLayers() {
    HybridLayerStack stack = HybridLayerStack.defaultThickFilm();
    LayerStructure structure = stack.toLayerStructure();

    assertEquals(5, structure.layers.length);
    assertEquals(2, structure.signalLayerCount());
    assertEquals("COND1", structure.getSignalLayer(0).name);
    assertEquals("COND2", structure.getSignalLayer(1).name);
    assertFalse(structure.layers[1].isSignal);
  }

  @Test
  void dielectricBetweenConductorsIsDetected() {
    HybridLayerStack good = HybridLayerStack.defaultThickFilm();
    HybridLayerStack shorted =
        new HybridLayerStack(
            List.of(
                new HybridLayer("COND1", HybridLayerKind.CONDUCTOR, 12),
                new HybridLayer("COND2", HybridLayerKind.CONDUCTOR, 12)));

    assertTrue(good.hasDielectricBetween("COND1", "COND2"));
    assertTrue(good.hasDielectricBetween("COND2", "COND1"));
    assertFalse(shorted.hasDielectricBetween("COND1", "COND2"));
    assertFalse(good.hasDielectricBetween("COND1", "MISSING"));
  }

  @Test
  void defaultCostsMakeJumpersTheMostExpensiveOption() {
    HybridLayerStack twoLayer = HybridLayerStack.defaultThickFilm();
    HybridRoutingOptions routing = HybridRoutingOptions.DEFAULT;
    assertTrue(routing.jumpersAllowed());
    assertTrue(routing.isOrdered(twoLayer));
    assertTrue(routing.jumperCost() > routing.crossoverCost());
    assertFalse(new HybridRoutingOptions(10.0, 5.0, true, 0, null).isOrdered(twoLayer));
  }

  @Test
  void singleLayerComesFromTheStackOrFromRoutingLayers() {
    HybridLayerStack oneConductor = HybridLayerStack.singleLayerThickFilm();
    HybridLayerStack twoConductors = HybridLayerStack.defaultThickFilm();

    assertTrue(HybridRoutingOptions.DEFAULT.isSingleLayer(oneConductor));
    assertFalse(HybridRoutingOptions.DEFAULT.isSingleLayer(twoConductors));
    assertTrue(HybridRoutingOptions.singleLayer("COND1").isSingleLayer(twoConductors));
    assertEquals(
        List.of("COND1", "COND2"),
        HybridRoutingOptions.DEFAULT.effectiveRoutingLayers(twoConductors));
    assertEquals(
        List.of("COND2"),
        HybridRoutingOptions.singleLayer("COND2").effectiveRoutingLayers(twoConductors));
  }

  @Test
  void singleLayerCostOrderIgnoresCrossoverCost() {
    HybridLayerStack oneConductor = HybridLayerStack.singleLayerThickFilm();
    // Crossover dearer than jumper: wrong for two layers, irrelevant for one.
    HybridRoutingOptions routing = new HybridRoutingOptions(80.0, 50.0, true, 0, null);
    assertTrue(routing.isOrdered(oneConductor));
    assertFalse(routing.isOrdered(HybridLayerStack.defaultThickFilm()));
    assertFalse(new HybridRoutingOptions(null, 0.5, true, 0, null).isOrdered(oneConductor));
  }

  @Test
  void singleLayerStructureHasOneSignalLayer() {
    LayerStructure fromTwo = HybridLayerStack.defaultThickFilm().toLayerStructure(List.of("COND1"));
    assertEquals(1, fromTwo.signalLayerCount());
    assertEquals("COND1", fromTwo.getSignalLayer(0).name);
    assertFalse(fromTwo.layers[2].isSignal); // COND2 kept as a layer, not routed

    LayerStructure fromOne = HybridLayerStack.singleLayerThickFilm().toLayerStructure();
    assertEquals(3, fromOne.layers.length);
    assertEquals(1, fromOne.signalLayerCount());
  }

  @Test
  void nullCollectionsBecomeEmpty() {
    HybridDesign design = new HybridDesign(1, null, null, null, null, null, null, null, null, null);
    assertEquals("", design.name());
    assertTrue(design.dies().isEmpty());
    assertTrue(design.components().isEmpty());
    assertTrue(design.stack().layers().isEmpty());
    assertEquals(HybridRoutingOptions.DEFAULT, design.routing());
    assertTrue(design.routingLayers().isEmpty());
    assertEquals(HybridRules.DEFAULT, design.rules());
  }

  /** A 750 µm long, 1000 µm wide printed resistor at (8000, 2500), pins at the body ends. */
  static TwoPinComponent resistor(Rotation rotation) {
    return new TwoPinComponent(
        "R1",
        TwoPinKind.PRINTED_RESISTOR,
        new PointUm(8000, 2500),
        rotation,
        750,
        1000,
        250,
        List.of("COND1"),
        new ComponentPin("IN", new PointUm(-375, 0), 400, 1200, "COND1"),
        new ComponentPin("FB", new PointUm(375, 0), 400, 1200, "COND1"),
        "1k");
  }
}
