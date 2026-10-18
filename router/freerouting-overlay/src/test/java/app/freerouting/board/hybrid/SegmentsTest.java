package app.freerouting.board.hybrid;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class SegmentsTest {

  private static PointUm pt(int x, int y) {
    return new PointUm(x, y);
  }

  @Test
  void crossingSegmentsIntersectProperly() {
    assertTrue(Segments.properlyIntersect(pt(0, 0), pt(10, 10), pt(0, 10), pt(10, 0)));
    assertEquals(0.0, Segments.distance(pt(0, 0), pt(10, 10), pt(0, 10), pt(10, 0)));
  }

  @Test
  void touchingAtEndOrCollinearIsNoCrossing() {
    assertFalse(Segments.properlyIntersect(pt(0, 0), pt(10, 0), pt(10, 0), pt(10, 10)));
    assertFalse(Segments.properlyIntersect(pt(0, 0), pt(10, 0), pt(5, 0), pt(20, 0)));
    assertFalse(Segments.properlyIntersect(pt(0, 0), pt(10, 0), pt(5, 0), pt(5, 10)));
  }

  @Test
  void distanceBetweenParallelAndSkewSegments() {
    assertEquals(60.0, Segments.distance(pt(0, 0), pt(0, 100), pt(60, 0), pt(60, 100)), 1e-9);
    // End of one segment to the interior of the other.
    assertEquals(30.0, Segments.distance(pt(0, 0), pt(100, 0), pt(50, 30), pt(50, 200)), 1e-9);
    // End to end.
    assertEquals(5.0, Segments.distance(pt(0, 0), pt(10, 0), pt(13, 4), pt(20, 20)), 1e-9);
  }

  @Test
  void pointToDegenerateSegmentIsPointDistance() {
    assertEquals(5.0, Segments.pointToSegment(pt(3, 4), pt(0, 0), pt(0, 0)), 1e-9);
  }

  @Test
  void segmentToRectangle() {
    RectUm r = new RectUm(0, 0, 100, 100);
    assertEquals(0.0, Segments.toRect(pt(50, 50), pt(500, 50), r), 1e-9); // starts inside
    assertEquals(0.0, Segments.toRect(pt(-50, 50), pt(150, 50), r), 1e-9); // passes through
    assertEquals(20.0, Segments.toRect(pt(120, -50), pt(120, 150), r), 1e-9);
    assertEquals(5.0, Segments.toRect(pt(103, 104), pt(200, 200), r), 1e-9);
  }

  @Test
  void largeCoordinatesDoNotOverflow() {
    int big = 2_000_000_000;
    assertTrue(
        Segments.properlyIntersect(pt(-big, -big), pt(big, big), pt(-big, big), pt(big, -big)));
  }
}
