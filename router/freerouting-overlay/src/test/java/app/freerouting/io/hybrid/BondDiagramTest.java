package app.freerouting.io.hybrid;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import app.freerouting.board.hybrid.BondDiagram;
import app.freerouting.board.hybrid.BondDiagramEntry;
import app.freerouting.board.hybrid.BondWire;
import app.freerouting.board.hybrid.HybridDesign;
import app.freerouting.board.hybrid.WirePurpose;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.Reader;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.util.List;
import org.junit.jupiter.api.Test;

class BondDiagramTest {

  @Test
  void listsEveryWireInBondingOrder() throws IOException {
    HybridDesign design = load("single-die-op-amp");
    List<BondDiagramEntry> rows = BondDiagram.of(design);

    assertEquals(design.wires().size(), rows.size());
    for (int i = 0; i < rows.size(); i++) {
      assertEquals(i + 1, rows.get(i).sequence());
    }
    // die bonds first, jumpers last
    boolean seenJumper = false;
    for (BondDiagramEntry r : rows) {
      if (r.purpose() == WirePurpose.JUMPER) {
        seenJumper = true;
      } else {
        assertFalse(seenJumper, "a die bond follows a jumper");
      }
    }
  }

  @Test
  void rowsCarryPositionsLengthSpecAndAngles() throws IOException {
    HybridDesign design = load("single-die-op-amp");
    for (BondDiagramEntry r : BondDiagram.of(design)) {
      BondWire w = design.wires().stream().filter(x -> x.id().equals(r.wireId())).findFirst().get();
      assertEquals(w.net(), r.net());
      assertEquals(w.from().toString(), r.firstBond());
      assertEquals(w.to().toString(), r.secondBond());
      assertEquals(design.position(w.from()).get(), r.first());
      assertEquals(design.position(w.to()).get(), r.second());
      assertEquals(Math.round(design.wireLengthUm(w).get()), r.lengthUm());
      assertEquals(w.spec().diameterUm(), r.diameterUm());
      assertEquals(w.spec().loopHeightUm(), r.loopHeightUm());
      if (w.from().isDiePad()) {
        assertNotNull(r.firstAngleDeg());
        assertTrue(r.firstAngleDeg() >= 0 && r.firstAngleDeg() <= 180);
      } else {
        assertNull(r.firstAngleDeg());
      }
      if (!w.to().isDiePad()) {
        assertNull(r.secondAngleDeg());
      }
    }
  }

  @Test
  void dieBondsFollowTheDiePadOrder() throws IOException {
    HybridDesign design = load("single-die-op-amp");
    List<String> padOrder = design.dies().get(0).pads().stream().map(p -> "U1." + p.id()).toList();
    List<String> bonded =
        BondDiagram.of(design).stream()
            .filter(r -> r.purpose() == WirePurpose.DIE_BOND)
            .map(r -> r.firstBond().startsWith("U1.") ? r.firstBond() : r.secondBond())
            .toList();
    int last = -1;
    for (String pad : bonded) {
      int at = padOrder.indexOf(pad);
      assertTrue(at > last, "out of pad order at " + pad);
      last = at;
    }
  }

  @Test
  void csvHasHeaderAndOneLinePerWire() throws IOException {
    HybridDesign design = load("single-die-op-amp");
    List<BondDiagramEntry> rows = BondDiagram.of(design);
    StringWriter out = new StringWriter();
    BondDiagramCsv.write(rows, out);
    String[] lines = out.toString().split("\n");

    assertEquals(BondDiagramCsv.HEADER, lines[0]);
    assertEquals(rows.size() + 1, lines.length);
    assertEquals(18, BondDiagramCsv.HEADER.split(",").length);
    assertTrue(lines[1].startsWith("1," + rows.get(0).wireId() + ","));
  }

  @Test
  void csvQuotesAwkwardFields() {
    assertEquals("GND", BondDiagramCsv.field("GND"));
    assertEquals("\"A,B\"", BondDiagramCsv.field("A,B"));
    assertEquals("\"say \"\"hi\"\"\"", BondDiagramCsv.field("say \"hi\""));
    assertEquals("", BondDiagramCsv.field(null));
  }

  private static HybridDesign load(String name) throws IOException {
    try (InputStream in =
            BondDiagramTest.class.getResourceAsStream("/hybrid/" + name + ".hybrid.json");
        Reader r = new InputStreamReader(in, StandardCharsets.UTF_8)) {
      return HybridJson.read(r);
    }
  }
}
