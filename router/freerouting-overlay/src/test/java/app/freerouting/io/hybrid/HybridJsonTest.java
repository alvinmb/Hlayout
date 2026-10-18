package app.freerouting.io.hybrid;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import app.freerouting.board.hybrid.HybridDesign;
import app.freerouting.board.hybrid.HybridLayerKind;
import app.freerouting.board.hybrid.HybridRoutingOptions;
import app.freerouting.board.hybrid.HybridValidator;
import app.freerouting.board.hybrid.PointUm;
import app.freerouting.board.hybrid.TwoPinComponent;
import app.freerouting.board.hybrid.TwoPinKind;
import app.freerouting.board.hybrid.ValidationIssue;
import app.freerouting.board.hybrid.WirePurpose;
import app.freerouting.rules.hybrid.HybridRules;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.Test;

class HybridJsonTest {

  private static final String FIXTURE = "/hybrid/single-die-op-amp.hybrid.json";
  private static final String SINGLE_LAYER = "/hybrid/single-layer-crossing.hybrid.json";

  @Test
  void fixtureLoadsAndValidatesCleanly() throws IOException {
    HybridDesign design = readFixture();

    assertEquals(1, design.schemaVersion());
    assertEquals(5, design.stack().layers().size());
    assertEquals(HybridLayerKind.DIELECTRIC, design.stack().layers().get(1).kind());
    assertEquals(1, design.dies().size());
    assertEquals(8, design.dies().get(0).pads().size());
    assertEquals(9, design.substratePads().size());
    assertEquals(8, design.wires().size());
    assertEquals(3, design.components().size());

    List<ValidationIssue> issues = HybridValidator.validate(design);
    assertEquals(List.of(), issues, () -> "unexpected issues: " + issues);
  }

  @Test
  void fixtureJumperAndWireGeometryResolve() throws IOException {
    HybridDesign design = readFixture();

    assertEquals(1, design.jumpers().size());
    assertEquals("WJ1", design.jumpers().get(0).id());
    assertEquals(WirePurpose.DIE_BOND, design.wires().get(0).purpose());
    // U1.1 at (4500, 3150) to F1 at (4500, 2500)
    assertEquals(650.0, design.wireLengthUm(design.wires().get(0)).orElseThrow(), 1e-9);
    assertEquals(800.0, design.wireLengthUm(design.jumpers().get(0)).orElseThrow(), 1e-9);
  }

  @Test
  void fixtureComponentsArriveAsTwoPinParts() throws IOException {
    HybridDesign design = readFixture();
    TwoPinComponent r3 = design.component("R3").orElseThrow();

    assertEquals(TwoPinKind.PRINTED_RESISTOR, r3.kind());
    assertEquals(List.of("COND1"), r3.obstacleLayers());
    assertEquals(new PointUm(8000, 5900), r3.pinPosition(r3.pinA()));
    assertEquals(new PointUm(8000, 7100), r3.pinPosition(r3.pinB()));
    assertEquals(List.of("BIAS", "FB", "GND", "IN", "OUT", "VCC"), List.copyOf(design.netNames()));
  }

  @Test
  void roundTripPreservesTheDesign() throws IOException {
    HybridDesign original = readFixture();
    HybridDesign copy = HybridJson.fromString(HybridJson.toString(original));
    assertEquals(original, copy);
  }

  @Test
  void missingSectionsDefaultSafely() throws IOException {
    HybridDesign design = HybridJson.fromString("{\"schemaVersion\": 1, \"name\": \"empty\"}");

    assertTrue(design.dies().isEmpty());
    assertTrue(design.wires().isEmpty());
    assertTrue(design.components().isEmpty());
    assertEquals(HybridRoutingOptions.DEFAULT, design.routing());
    assertTrue(
        HybridValidator.validate(design).stream()
            .anyMatch(i -> i.code().equals("SUBSTRATE_MISSING")));
  }

  @Test
  void partialCostsKeepDefaultsForMissingFields() throws IOException {
    HybridDesign design =
        HybridJson.fromString("{\"schemaVersion\": 1, \"routing\": {\"jumperCost\": 80}}");

    assertEquals(80.0, design.routing().jumperCost(), 1e-9);
    assertEquals(10.0, design.routing().crossoverCost(), 1e-9);
    assertTrue(design.routing().jumpersAllowed());
    assertEquals(0, design.routing().maxJumpers());
  }

  @Test
  void twoLayerFixtureRoutesOnBothConductors() throws IOException {
    HybridDesign design = readFixture();
    assertEquals(List.of("COND1", "COND2"), design.routingLayers());
    assertFalse(design.isSingleLayer());
    assertEquals(2, design.toLayerStructure().signalLayerCount());
  }

  @Test
  void singleLayerFixtureLoadsValidatesAndHasOneSignalLayer() throws IOException {
    HybridDesign design = read(SINGLE_LAYER);

    assertTrue(design.isSingleLayer());
    assertEquals(List.of("COND1"), design.routingLayers());
    assertEquals(1, design.toLayerStructure().signalLayerCount());
    assertEquals(4, design.routing().maxJumpers());
    assertTrue(design.routing().jumpersAllowed());
    assertTrue(design.jumpers().isEmpty(), "router adds the jumpers; none pre-placed");

    List<ValidationIssue> issues = HybridValidator.validate(design);
    assertEquals(List.of(), issues, () -> "unexpected issues: " + issues);
  }

  @Test
  void singleLayerFixtureRoundTrips() throws IOException {
    HybridDesign original = read(SINGLE_LAYER);
    assertEquals(original, HybridJson.fromString(HybridJson.toString(original)));
  }

  @Test
  void fixtureRulesOverrideOnlyWhatTheyName() throws IOException {
    HybridDesign design = readFixture();

    assertEquals(125, design.rules().conductor().minSpaceUm());
    assertEquals(150, design.rules().conductor().minLineUm());
    assertEquals(2500, design.rules().bondWire().maxLengthUm());
    assertEquals(500, design.rules().bondWire().minLengthUm());
    assertEquals(175, design.rules().bondWire().fingerPitchUm());
    assertEquals(175, design.rules().jumper().minLoopHeightUm());
    assertEquals(3000, design.rules().jumper().maxLengthUm());
  }

  @Test
  void missingRulesUseDefaults() throws IOException {
    assertEquals(HybridRules.DEFAULT, read(SINGLE_LAYER).rules());
  }

  @Test
  void malformedJsonIsAnIoException() {
    assertThrows(IOException.class, () -> HybridJson.fromString("{\"dies\": [ }"));
    assertThrows(IOException.class, () -> HybridJson.fromString(""));
  }

  private static HybridDesign readFixture() throws IOException {
    return read(FIXTURE);
  }

  private static HybridDesign read(String resource) throws IOException {
    try (InputStream in = HybridJsonTest.class.getResourceAsStream(resource);
        Reader reader = new InputStreamReader(in, StandardCharsets.UTF_8)) {
      return HybridJson.read(reader);
    }
  }
}
