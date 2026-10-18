package app.freerouting.board.hybrid;

import java.io.Serializable;

/**
 * Axis-aligned rectangle in integer micrometres. Hybrid parts rotate only in 90 degree steps, so
 * every pad, die and component body is exactly one of these on the substrate.
 */
public record RectUm(int minX, int minY, int maxX, int maxY) implements Serializable {

  /** Rejects a minimum corner above the maximum corner. */
  public RectUm {
    if (minX > maxX || minY > maxY) {
      throw new IllegalArgumentException("min corner above max corner");
    }
  }

  /** Rectangle of the given size centred on {@code centre}; sizes below zero count as zero. */
  public static RectUm centred(PointUm centre, int width, int height) {
    int w = Math.max(width, 0);
    int h = Math.max(height, 0);
    int x0 = centre.x() - w / 2;
    int y0 = centre.y() - h / 2;
    return new RectUm(x0, y0, x0 + w, y0 + h);
  }

  /** Smallest rectangle holding all the points. */
  public static RectUm bounding(Iterable<PointUm> points) {
    int minX = Integer.MAX_VALUE;
    int minY = Integer.MAX_VALUE;
    int maxX = Integer.MIN_VALUE;
    int maxY = Integer.MIN_VALUE;
    for (PointUm p : points) {
      minX = Math.min(minX, p.x());
      minY = Math.min(minY, p.y());
      maxX = Math.max(maxX, p.x());
      maxY = Math.max(maxY, p.y());
    }
    if (minX > maxX) {
      throw new IllegalArgumentException("no points");
    }
    return new RectUm(minX, minY, maxX, maxY);
  }

  /** Size along x. */
  public int width() {
    return maxX - minX;
  }

  /** Size along y. */
  public int height() {
    return maxY - minY;
  }

  /** Grown by {@code margin} on every side (shrunk if negative, never below a point). */
  public RectUm grow(int margin) {
    int cx = (minX + maxX) / 2;
    int cy = (minY + maxY) / 2;
    return new RectUm(
        Math.min(minX - margin, cx),
        Math.min(minY - margin, cy),
        Math.max(maxX + margin, cx),
        Math.max(maxY + margin, cy));
  }

  /** True if the point lies inside or on the edge. */
  public boolean contains(PointUm p) {
    return p.x() >= minX && p.x() <= maxX && p.y() >= minY && p.y() <= maxY;
  }

  /** True if the two rectangles share any area or edge. */
  public boolean intersects(RectUm other) {
    return minX <= other.maxX && other.minX <= maxX && minY <= other.maxY && other.minY <= maxY;
  }

  /** Edge-to-edge gap to another rectangle; 0 if they touch or overlap. */
  public double gap(RectUm other) {
    long dx = Math.max(0L, Math.max((long) other.minX - maxX, (long) minX - other.maxX));
    long dy = Math.max(0L, Math.max((long) other.minY - maxY, (long) minY - other.maxY));
    return Math.hypot(dx, dy);
  }

  /** Distance from a point to the rectangle; 0 if the point is inside or on the edge. */
  public double distanceTo(PointUm p) {
    long dx = Math.max(0L, Math.max((long) minX - p.x(), (long) p.x() - maxX));
    long dy = Math.max(0L, Math.max((long) minY - p.y(), (long) p.y() - maxY));
    return Math.hypot(dx, dy);
  }

  /** Distance from an inside point to the nearest edge; 0 on or outside the edge. */
  public double insetOf(PointUm p) {
    if (!contains(p)) {
      return 0.0;
    }
    return Math.min(Math.min(p.x() - minX, maxX - p.x()), Math.min(p.y() - minY, maxY - p.y()));
  }
}
