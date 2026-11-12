package app.freerouting.autoroute.hybrid;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import app.freerouting.board.hybrid.BondWire;
import app.freerouting.board.hybrid.HybridDesign;
import app.freerouting.board.hybrid.HybridLayerStack;
import app.freerouting.board.hybrid.HybridRoutingOptions;
import app.freerouting.board.hybrid.PointUm;
import app.freerouting.board.hybrid.RoutedHybrid;
import app.freerouting.board.hybrid.RoutedTrace;
import app.freerouting.board.hybrid.SubstratePad;
import app.freerouting.board.hybrid.SubstratePadRole;
import app.freerouting.board.hybrid.ValidationIssue;
import app.freerouting.board.hybrid.WirePurpose;
import app.freerouting.drc.hybrid.HybridRouteDrc;
import java.io.IOException;
import java.util.List;
import org.junit.jupiter.api.Test;

class HybridRouterTest {

  @Test
  void twoLayerOpAmpRoutesCompletelyAndCleanly() throws IOException {
    RoutedHybrid r = HybridRouter.route(Fixtures.load("single-die-op-amp"));

    assertTrue(r.stats().isComplete(), () -> "unrouted: " + r.unrouted());
    assertEquals(8, r.stats().connections());
    assertEquals(0, r.stats().jumpersAdded());
    assertEquals(List.of(), HybridRouteDrc.check(r));
  }

  @Test
  void singleLayerCrossingUsesJumpersAndIsClean() throws IOException {
    RoutedHybrid r = HybridRouter.route(Fixtures.load("single-layer-crossing"));

    assertTrue(r.stats().isComplete(), () -> "unrouted: " + r.unrouted());
    assertEquals(0, r.stats().windows());
    int jumpers = r.stats().jumpersAdded();
    assertTrue(jumpers >= 1 && jumpers <= 4, "jumpers: " + jumpers);
    assertEquals(jumpers, r.design().jumpers().size());
    for (BondWire w : r.design().jumpers()) {
      assertEquals(WirePurpose.JUMPER, w.purpose());
      assertTrue(w.spec().loopHeightUm() >= r.design().rules().jumper().minLoopHeightUm());
    }
    long lands =
        r.design().substratePads().stream()
            .filter(p -> p.role() == SubstratePadRole.JUMPER_LAND)
            .count();
    assertEquals(2L * jumpers, lands);
    assertEquals(List.of(), HybridRouteDrc.check(r));
  }

  @Test
  void singleLayerWithoutJumpersReportsWhatItCannotRoute() throws IOException {
    HybridDesign design =
        Fixtures.withRouting(
            Fixtures.load("single-layer-crossing"),
            new HybridRoutingOptions(null, null, false, null, List.of("COND1")));
    RoutedHybrid r = HybridRouter.route(design);

    assertFalse(r.stats().isComplete());
    assertEquals(0, r.stats().jumpersAdded());
    assertFalse(r.unrouted().isEmpty());
    List<ValidationIssue> issues = HybridRouteDrc.check(r);
    assertTrue(issues.stream().allMatch(i -> i.code().equals("UNROUTED")), issues::toString);
    assertEquals(r.unrouted().size(), issues.size());
  }

  @Test
  void orthogonalRoutingHasNoDiagonalTraces() throws IOException {
    HybridDesign op = Fixtures.load("single-die-op-amp");
    HybridDesign design =
        Fixtures.withRouting(
            op,
            new HybridRoutingOptions(null, null, null, null, null, null, true, 3));
    RoutedHybrid r = HybridRouter.route(design);

    for (RoutedTrace t : r.traces()) {
      List<PointUm> pts = t.points();
      for (int i = 1; i < pts.size(); i++) {
        PointUm a = pts.get(i - 1);
        PointUm b = pts.get(i);
        assertTrue(a.x() == b.x() || a.y() == b.y(), "diagonal segment " + a + " - " + b);
      }
    }
  }

  @Test
  void netWidthsAndExistingCopperAreHonoured() throws IOException {
    HybridDesign base = Fixtures.load("single-layer-crossing");
    int all = HybridRouter.route(base).stats().connections();
    HybridDesign design =
        Fixtures.withRouting(
            base,
            new HybridRoutingOptions(
                null,
                null,
                null,
                null,
                List.of("COND1"),
                null,
                null,
                null,
                java.util.Map.of("VCC", 300),
                null,
                null,
                List.of(List.of("F1", "X2")),
                null,
                null,
                null,
                null));
    RoutedHybrid r = HybridRouter.route(design);

    assertEquals(all - 1, r.stats().connections(), "F1 and X2 are already joined");
    for (RoutedTrace t : r.traces()) {
      if (t.net().equals("VCC") && t.points().size() > 2) {
        assertEquals(300, t.widthUm());
      }
    }
    assertTrue(r.traces().stream().noneMatch(t -> t.net().equals("A") && t.points().size() > 2));
  }

  @Test
  void jumperBudgetIsRespected() throws IOException {
    HybridDesign design =
        Fixtures.withRouting(
            Fixtures.load("single-layer-crossing"),
            new HybridRoutingOptions(null, null, true, 1, List.of("COND1")));
    RoutedHybrid r = HybridRouter.route(design);

    assertTrue(r.stats().jumpersAdded() <= 1);
    assertFalse(r.stats().isComplete(), "the crossing needs two jumpers");
    assertTrue(
        HybridRouteDrc.check(r).stream().allMatch(i -> i.code().equals("UNROUTED")),
        "only unrouted connections may be reported");
  }

  @Test
  void twoLayerStackCrossesWithWindowsInsteadOfJumpers() throws IOException {
    HybridDesign single = Fixtures.load("single-layer-crossing");
    HybridDesign design =
        Fixtures.withRouting(
            Fixtures.withStack(single, HybridLayerStack.defaultThickFilm()),
            HybridRoutingOptions.DEFAULT);
    RoutedHybrid r = HybridRouter.route(design);

    assertTrue(r.stats().isComplete(), () -> "unrouted: " + r.unrouted());
    assertTrue(r.stats().windows() >= 1);
    // Pads are bare through the whole stack, so the upper conductor cannot run over them: on
    // this small substrate one crossing may still need a jumper.
    assertTrue(r.stats().jumpersAdded() <= 1, "crossovers are cheaper than jumpers");
    assertEquals(List.of(), HybridRouteDrc.check(r));
  }

  @Test
  void unbondedDiePadsAreBondedThenRouted() throws IOException {
    HybridDesign full = Fixtures.load("single-layer-crossing");
    List<BondWire> noDieBonds =
        full.wires().stream().filter(w -> w.purpose() != WirePurpose.DIE_BOND).toList();
    List<SubstratePad> noFingers =
        full.substratePads().stream()
            .filter(p -> p.role() != SubstratePadRole.BOND_FINGER)
            .toList();
    RoutedHybrid r = HybridRouter.route(Fixtures.withPadsAndWires(full, noFingers, noDieBonds));

    assertEquals(4, r.stats().fingersAdded());
    assertTrue(r.stats().isComplete(), () -> "unrouted: " + r.unrouted());
    assertEquals(List.of(), HybridRouteDrc.check(r));
  }

  @Test
  void routingIsDeterministic() throws IOException {
    HybridDesign design = Fixtures.load("single-layer-crossing");
    assertEquals(HybridRouter.route(design), HybridRouter.route(design));
    HybridDesign op = Fixtures.load("single-die-op-amp");
    assertEquals(HybridRouter.route(op), HybridRouter.route(op));
  }

  @Test
  void tracesAreOctilinearAwayFromPads() throws IOException {
    RoutedHybrid r = HybridRouter.route(Fixtures.load("single-die-op-amp"));
    for (RoutedTrace t : r.traces()) {
      List<PointUm> p = t.points();
      for (int i = 2; i < p.size() - 1; i++) { // first and last legs may be pad stubs
        int dx = Math.abs(p.get(i).x() - p.get(i - 1).x());
        int dy = Math.abs(p.get(i).y() - p.get(i - 1).y());
        assertTrue(dx == 0 || dy == 0 || dx == dy, "not octilinear: " + t);
      }
      assertEquals(r.design().rules().conductor().minLineUm(), t.widthUm());
    }
  }

  @Test
  void simplifyDropsDuplicatesAndStraightRuns() {
    List<PointUm> raw =
        List.of(
            new PointUm(0, 0),
            new PointUm(0, 0),
            new PointUm(10, 0),
            new PointUm(20, 0),
            new PointUm(30, 10),
            new PointUm(40, 20),
            new PointUm(40, 30));
    assertEquals(
        List.of(new PointUm(0, 0), new PointUm(20, 0), new PointUm(40, 20), new PointUm(40, 30)),
        HybridRouter.simplify(raw));
  }
}
