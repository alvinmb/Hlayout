package app.freerouting.board.hybrid;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;
import org.junit.jupiter.api.Test;

class RectUmTest {

  @Test
  void centredRectangleHasRequestedSize() {
    RectUm r = RectUm.centred(new PointUm(100, 200), 40, 20);
    assertEquals(new RectUm(80, 190, 120, 210), r);
    assertEquals(40, r.width());
    assertEquals(20, r.height());
  }

  @Test
  void gapIsEdgeToEdgeAndZeroWhenTouching() {
    RectUm a = new RectUm(0, 0, 10, 10);
    assertEquals(5.0, a.gap(new RectUm(15, 0, 20, 10)), 1e-9);
    assertEquals(5.0, a.gap(new RectUm(13, 14, 20, 20)), 1e-9); // diagonal 3-4-5
    assertEquals(0.0, a.gap(new RectUm(10, 0, 20, 10)), 1e-9);
    assertEquals(0.0, a.gap(new RectUm(5, 5, 20, 20)), 1e-9);
  }

  @Test
  void pointDistanceAndInset() {
    RectUm r = new RectUm(0, 0, 100, 50);
    assertEquals(0.0, r.distanceTo(new PointUm(50, 25)), 1e-9);
    assertEquals(10.0, r.distanceTo(new PointUm(110, 25)), 1e-9);
    assertEquals(25.0, r.insetOf(new PointUm(50, 25)), 1e-9);
    assertEquals(0.0, r.insetOf(new PointUm(150, 25)), 1e-9);
  }

  @Test
  void growAndIntersect() {
    RectUm r = new RectUm(0, 0, 10, 10);
    assertEquals(new RectUm(-5, -5, 15, 15), r.grow(5));
    assertEquals(new RectUm(5, 5, 5, 5), r.grow(-100)); // never smaller than its centre point
    assertTrue(r.intersects(new RectUm(10, 10, 20, 20)));
    assertFalse(r.intersects(new RectUm(11, 0, 20, 10)));
  }

  @Test
  void boundingBoxOfPoints() {
    RectUm r = RectUm.bounding(List.of(new PointUm(5, -3), new PointUm(-2, 7), new PointUm(1, 1)));
    assertEquals(new RectUm(-2, -3, 5, 7), r);
    assertThrows(IllegalArgumentException.class, () -> RectUm.bounding(List.of()));
  }

  @Test
  void quarterTurnSwapsPadAndPinSizes() {
    BondPad pad = new BondPad("1", "GND", new PointUm(500, 150), 200, 100);
    Die turned = new Die("U1", "d", PointUm.ORIGIN, Rotation.R90, 2000, 2000, 250, 0, List.of(pad));
    RectUm padBounds = turned.padBounds(pad);
    assertEquals(100, padBounds.width());
    assertEquals(200, padBounds.height());
  }

  @Test
  void padOutwardNormalFollowsNearestEdgeAndRotation() {
    BondPad bottom = new BondPad("1", "A", new PointUm(500, 150), 100, 100);
    BondPad right = new BondPad("2", "B", new PointUm(1850, 500), 100, 100);
    Die upright =
        new Die("U1", "d", PointUm.ORIGIN, Rotation.R0, 2000, 2000, 250, 0, List.of(bottom, right));
    Die turned =
        new Die(
            "U2", "d", PointUm.ORIGIN, Rotation.R90, 2000, 2000, 250, 0, List.of(bottom, right));

    assertEquals(new PointUm(0, -1), upright.padOutwardNormal(bottom));
    assertEquals(new PointUm(1, 0), upright.padOutwardNormal(right));
    assertEquals(new PointUm(1, 0), turned.padOutwardNormal(bottom)); // bottom edge now faces +x
    assertEquals(new PointUm(0, 1), turned.padOutwardNormal(right));
  }
}
