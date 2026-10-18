package app.freerouting.drc.hybrid;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import app.freerouting.autoroute.hybrid.HybridRouter;
import app.freerouting.board.hybrid.CrossoverWindow;
import app.freerouting.board.hybrid.HybridDesign;
import app.freerouting.board.hybrid.PointUm;
import app.freerouting.board.hybrid.RoutedHybrid;
import app.freerouting.board.hybrid.RoutedTrace;
import app.freerouting.board.hybrid.ValidationIssue;
import app.freerouting.io.hybrid.HybridJson;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.Reader;
import java.io.StringReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

class HybridRouteDrcTest {

  // Op-amp fixture: die U1 at (4000, 3000)-(6000, 5000), keep-out 150. F2 (IN) at (5500, 2500).
  // R1 body 750 x 1000 at (8000, 2500), keep-out 250. Substrate 10000 x 8000, edge clearance 250.

  @Test
  void routedFixtureIsClean() throws IOException {
    assertEquals(List.of(), HybridRouteDrc.check(routedOpAmp()));
  }

  @Test
  void traceTooCloseToForeignPad() throws IOException {
    // OUT trace passing 50 µm from F2 (IN): pad top edge y 2600, trace edge 2650.
    RoutedHybrid r = withTrace(routedOpAmp(), trace("OUT", 5000, 2725, 6000, 2725));
    assertTrue(has(HybridRouteDrc.check(r), "TRACE_SPACING"));
  }

  @Test
  void tracesOfDifferentNetsTooClose() throws IOException {
    RoutedHybrid base = routedOpAmp();
    RoutedHybrid r =
        withTrace(
            withTrace(base, trace("OUT", 1000, 7000, 3000, 7000)),
            trace("VCC", 1000, 7200, 3000, 7200)); // 200 centre to centre, 50 µm gap
    assertTrue(has(HybridRouteDrc.check(r), "TRACE_SPACING"));
  }

  @Test
  void traceThroughTheDieKeepout() throws IOException {
    RoutedHybrid r = withTrace(routedOpAmp(), trace("OUT", 3000, 4000, 3950, 4000));
    assertTrue(has(HybridRouteDrc.check(r), "TRACE_DIE_KEEPOUT"));
  }

  @Test
  void traceTooCloseToTheEdge() throws IOException {
    RoutedHybrid r = withTrace(routedOpAmp(), trace("OUT", 9700, 1000, 9700, 2000));
    assertTrue(has(HybridRouteDrc.check(r), "TRACE_EDGE_CLEARANCE"));
  }

  @Test
  void foreignTraceInComponentKeepoutButOwnTraceToItsPinIsFine() throws IOException {
    RoutedHybrid base = routedOpAmp();
    // OUT is not a pin net of R1: passing just above its body enters the keep-out.
    assertTrue(
        has(
            HybridRouteDrc.check(withTrace(base, trace("OUT", 7000, 3150, 9000, 3150))),
            "TRACE_COMPONENT_KEEPOUT"));
    // IN is R1's pin A net: a stub from outside onto pin A is allowed.
    List<ValidationIssue> own =
        HybridRouteDrc.check(withTrace(base, trace("IN", 7000, 2500, 7625, 2500)));
    assertTrue(own.stream().noneMatch(i -> i.code().startsWith("TRACE_COMPONENT")), own::toString);
  }

  @Test
  void ownTraceAcrossTheBodyIsCaught() throws IOException {
    RoutedHybrid r = withTrace(routedOpAmp(), trace("IN", 7400, 2500, 8600, 2500));
    assertTrue(has(HybridRouteDrc.check(r), "TRACE_COMPONENT_BODY"));
  }

  @Test
  void missingTraceIsAnOpenNet() throws IOException {
    RoutedHybrid base = routedOpAmp();
    List<RoutedTrace> fewer = new ArrayList<>(base.traces());
    RoutedTrace removed = fewer.remove(fewer.size() - 1); // a routed connection, not an escape
    RoutedHybrid r =
        new RoutedHybrid(
            base.design(), fewer, base.windows(), base.unrouted(), base.stats(), List.of());
    List<ValidationIssue> issues = HybridRouteDrc.check(r);
    assertTrue(
        issues.stream().anyMatch(i -> i.code().equals("NET_OPEN") && i.ref().equals(removed.net())),
        issues::toString);
  }

  @Test
  void badWindowsAreCaught() throws IOException {
    RoutedHybrid base = routedOpAmp();
    List<CrossoverWindow> windows = new ArrayList<>(base.windows());
    windows.add(new CrossoverWindow("OUT", new PointUm(1500, 7000), 100, 150, "COND1", "COND2"));
    windows.add(new CrossoverWindow("OUT", new PointUm(1500, 6000), 250, 450, "COND1", "RES"));
    RoutedHybrid r =
        new RoutedHybrid(
            base.design(), base.traces(), windows, base.unrouted(), base.stats(), List.of());
    List<ValidationIssue> issues = HybridRouteDrc.check(r);
    assertTrue(has(issues, "WINDOW_SIZE"));
    assertTrue(has(issues, "WINDOW_ENCLOSURE"));
    assertTrue(has(issues, "WINDOW_LAYERS"));
  }

  @Test
  void routedResultSurvivesJsonRoundTrip() throws IOException {
    RoutedHybrid r = routedOpAmp();
    RoutedHybrid back = HybridJson.readRouted(new StringReader(HybridJson.routedToString(r)));
    assertEquals(r, back);
    assertEquals(List.of(), HybridRouteDrc.check(back));
  }

  private static RoutedHybrid routedOpAmp() throws IOException {
    try (InputStream in =
            HybridRouteDrcTest.class.getResourceAsStream("/hybrid/single-die-op-amp.hybrid.json");
        Reader reader = new InputStreamReader(in, StandardCharsets.UTF_8)) {
      HybridDesign design = HybridJson.read(reader);
      return HybridRouter.route(design);
    }
  }

  private static RoutedTrace trace(String net, int x0, int y0, int x1, int y1) {
    return new RoutedTrace(net, "COND1", List.of(new PointUm(x0, y0), new PointUm(x1, y1)), 150);
  }

  private static RoutedHybrid withTrace(RoutedHybrid r, RoutedTrace extra) {
    List<RoutedTrace> traces = new ArrayList<>(r.traces());
    traces.add(extra);
    return new RoutedHybrid(r.design(), traces, r.windows(), r.unrouted(), r.stats(), List.of());
  }

  private static boolean has(List<ValidationIssue> issues, String code) {
    return issues.stream().anyMatch(i -> i.code().equals(code));
  }
}
