package app.freerouting.autoroute.hybrid;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertTrue;

import app.freerouting.board.hybrid.BondPad;
import app.freerouting.board.hybrid.BondWire;
import app.freerouting.board.hybrid.Die;
import app.freerouting.board.hybrid.HybridDesign;
import app.freerouting.board.hybrid.PointUm;
import app.freerouting.board.hybrid.Rotation;
import app.freerouting.board.hybrid.SubstratePad;
import app.freerouting.board.hybrid.SubstratePadRole;
import app.freerouting.board.hybrid.WirePurpose;
import app.freerouting.drc.hybrid.HybridDrc;
import app.freerouting.rules.hybrid.BondWireRules;
import app.freerouting.rules.hybrid.HybridRules;
import java.io.IOException;
import java.util.List;
import org.junit.jupiter.api.Test;

class BondAssignerTest {

  @Test
  void fullyBondedDesignIsLeftAlone() throws IOException {
    HybridDesign design = Fixtures.load("single-layer-crossing");
    BondAssigner.Result result = BondAssigner.assign(design);
    assertSame(design, result.design());
    assertEquals(0, result.fingersAdded());
    assertTrue(result.unplaced().isEmpty());
  }

  @Test
  void unbondedPadGetsFingerStraightOutAndDieBond() throws IOException {
    HybridDesign design = withoutWireAndFinger(Fixtures.load("single-layer-crossing"), "W1", "F1");
    BondAssigner.Result result = BondAssigner.assign(design);

    assertEquals(1, result.fingersAdded());
    SubstratePad finger = result.design().substratePad("BF_U1_1").orElseThrow();
    assertEquals(SubstratePadRole.BOND_FINGER, finger.role());
    assertEquals("A", finger.net());
    assertEquals("COND1", finger.layer());
    // Pad 1 is at (2550, 1850) on the bottom edge: the finger sits directly below it.
    assertEquals(2550, finger.position().x());
    assertTrue(finger.position().y() < 1750 - 250 + 1);
    BondWire wire =
        result.design().wires().stream()
            .filter(w -> w.id().equals("WB_U1_1"))
            .findFirst()
            .orElseThrow();
    assertEquals(WirePurpose.DIE_BOND, wire.purpose());
    assertEquals(List.of(), HybridDrc.check(result.design()));
  }

  @Test
  void noConnectPadsGetNothing() throws IOException {
    HybridDesign op = Fixtures.load("single-die-op-amp");
    // Pad 8 has an empty net in the fixture and no wire.
    assertEquals(0, BondAssigner.assign(op).fingersAdded());
  }

  @Test
  void closePadsAreStaggeredToKeepPitchAndSpacing() {
    // Two pads 120 µm apart on the bottom edge: straight-out fingers would clash.
    Die die =
        new Die(
            "U1",
            "d",
            new PointUm(4000, 3000),
            Rotation.R0,
            2000,
            2000,
            250,
            150,
            List.of(
                new BondPad("1", "A", new PointUm(900, 150), 80, 80),
                new BondPad("2", "B", new PointUm(1020, 150), 80, 80)));
    HybridDesign design =
        new HybridDesign(
            1,
            "stagger",
            new app.freerouting.board.hybrid.Substrate(null, null, 10000, 8000, 635),
            app.freerouting.board.hybrid.HybridLayerStack.singleLayerThickFilm(),
            List.of(die),
            null,
            null,
            null,
            null,
            null);
    BondAssigner.Result result = BondAssigner.assign(design);

    assertEquals(2, result.fingersAdded());
    SubstratePad f1 = result.design().substratePad("BF_U1_1").orElseThrow();
    SubstratePad f2 = result.design().substratePad("BF_U1_2").orElseThrow();
    assertNotEquals(f1.position().y(), f2.position().y());
    assertEquals(List.of(), HybridDrc.check(result.design()));
  }

  @Test
  void padWithNoLegalPlaceIsReportedAndShowsUpUnrouted() throws IOException {
    HybridDesign base = withoutWireAndFinger(Fixtures.load("single-layer-crossing"), "W1", "F1");
    // Fingers must be within 100 µm of the die, but the attach keep-out alone is 150 µm.
    HybridRules tight =
        new HybridRules(
            null, new BondWireRules(null, null, null, null, null, null, null, null, 50, 100), null);
    HybridDesign design =
        new HybridDesign(
            base.schemaVersion(),
            base.name(),
            base.substrate(),
            base.stack(),
            base.dies(),
            base.substratePads(),
            base.wires(),
            base.components(),
            base.routing(),
            tight);

    BondAssigner.Result result = BondAssigner.assign(design);
    assertEquals(List.of("U1.1"), result.unplaced());
    assertTrue(
        HybridRouter.route(design).unrouted().stream().anyMatch(u -> u.from().equals("U1.1")));
  }

  static HybridDesign withoutWireAndFinger(HybridDesign d, String wireId, String fingerId) {
    List<BondWire> wires = d.wires().stream().filter(w -> !w.id().equals(wireId)).toList();
    List<SubstratePad> pads =
        d.substratePads().stream().filter(p -> !p.id().equals(fingerId)).toList();
    return Fixtures.withPadsAndWires(d, pads, wires);
  }
}
