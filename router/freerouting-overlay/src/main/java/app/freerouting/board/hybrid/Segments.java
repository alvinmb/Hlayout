package app.freerouting.board.hybrid;

/** Plan-view segment geometry for wires and traces, exact on integer micrometre coordinates. */
public final class Segments {

  private Segments() {}

  /**
   * True if segments ab and cd cross at a single interior point. Touching at an end, or running
   * collinear, is not a crossing; spacing catches those.
   */
  public static boolean properlyIntersect(PointUm a, PointUm b, PointUm c, PointUm d) {
    long d1 = cross(c, d, a);
    long d2 = cross(c, d, b);
    long d3 = cross(a, b, c);
    long d4 = cross(a, b, d);
    return Long.signum(d1) * Long.signum(d2) < 0 && Long.signum(d3) * Long.signum(d4) < 0;
  }

  /** Shortest distance between segments ab and cd; 0 if they intersect. */
  public static double distance(PointUm a, PointUm b, PointUm c, PointUm d) {
    if (properlyIntersect(a, b, c, d)) {
      return 0.0;
    }
    return Math.min(
        Math.min(pointToSegment(a, c, d), pointToSegment(b, c, d)),
        Math.min(pointToSegment(c, a, b), pointToSegment(d, a, b)));
  }

  /** Distance from p to segment ab. */
  public static double pointToSegment(PointUm p, PointUm a, PointUm b) {
    double vx = (double) b.x() - a.x();
    double vy = (double) b.y() - a.y();
    double wx = (double) p.x() - a.x();
    double wy = (double) p.y() - a.y();
    double lengthSquared = vx * vx + vy * vy;
    double t =
        lengthSquared == 0 ? 0 : Math.max(0, Math.min(1, (wx * vx + wy * vy) / lengthSquared));
    return Math.hypot(wx - t * vx, wy - t * vy);
  }

  /** Shortest distance from segment ab to a rectangle; 0 if the segment touches or enters it. */
  public static double toRect(PointUm a, PointUm b, RectUm r) {
    if (r.contains(a) || r.contains(b)) {
      return 0.0;
    }
    PointUm c0 = new PointUm(r.minX(), r.minY());
    PointUm c1 = new PointUm(r.maxX(), r.minY());
    PointUm c2 = new PointUm(r.maxX(), r.maxY());
    PointUm c3 = new PointUm(r.minX(), r.maxY());
    return Math.min(
        Math.min(distance(a, b, c0, c1), distance(a, b, c1, c2)),
        Math.min(distance(a, b, c2, c3), distance(a, b, c3, c0)));
  }

  /** Z of (b - a) x (p - a): positive if p is left of ab. */
  private static long cross(PointUm a, PointUm b, PointUm p) {
    return ((long) b.x() - a.x()) * ((long) p.y() - a.y())
        - ((long) b.y() - a.y()) * ((long) p.x() - a.x());
  }
}
