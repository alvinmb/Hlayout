package app.freerouting.autoroute.hybrid;

import app.freerouting.board.hybrid.BondWire;
import app.freerouting.board.hybrid.HybridDesign;
import app.freerouting.board.hybrid.HybridLayerStack;
import app.freerouting.board.hybrid.HybridRoutingOptions;
import app.freerouting.board.hybrid.SubstratePad;
import app.freerouting.io.hybrid.HybridJson;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.util.List;

/** Loads the shared hybrid fixtures and builds variants of them. */
final class Fixtures {

  private Fixtures() {}

  static HybridDesign load(String name) throws IOException {
    String resource = "/hybrid/" + name + ".hybrid.json";
    try (InputStream in = Fixtures.class.getResourceAsStream(resource);
        Reader reader = new InputStreamReader(in, StandardCharsets.UTF_8)) {
      return HybridJson.read(reader);
    }
  }

  static HybridDesign withRouting(HybridDesign d, HybridRoutingOptions routing) {
    return new HybridDesign(
        d.schemaVersion(),
        d.name(),
        d.substrate(),
        d.stack(),
        d.dies(),
        d.substratePads(),
        d.wires(),
        d.components(),
        routing,
        d.rules());
  }

  static HybridDesign withStack(HybridDesign d, HybridLayerStack stack) {
    return new HybridDesign(
        d.schemaVersion(),
        d.name(),
        d.substrate(),
        stack,
        d.dies(),
        d.substratePads(),
        d.wires(),
        d.components(),
        d.routing(),
        d.rules());
  }

  static HybridDesign withPadsAndWires(
      HybridDesign d, List<SubstratePad> pads, List<BondWire> wires) {
    return new HybridDesign(
        d.schemaVersion(),
        d.name(),
        d.substrate(),
        d.stack(),
        d.dies(),
        pads,
        wires,
        d.components(),
        d.routing(),
        d.rules());
  }
}
