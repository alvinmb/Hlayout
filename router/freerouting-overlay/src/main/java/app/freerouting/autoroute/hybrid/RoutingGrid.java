package app.freerouting.autoroute.hybrid;

import app.freerouting.board.hybrid.PointUm;
import app.freerouting.board.hybrid.RectUm;
import app.freerouting.board.hybrid.Segments;
import java.util.function.IntConsumer;
import java.util.function.IntPredicate;

/**
 * Uniform routing grid over the substrate. Cell (i, j) covers [i·p, (i+1)·p) × [j·p, (j+1)·p) and
 * routes through its centre. Cells are numbered {@code j * nx + i}.
 *
 * <p>Clearances are checked on cell centres with a distance transform. The transform measures the
 * octile (8-connected) distance, which is never shorter than the true distance and at most {@link
 * #OCTILE_FACTOR} times longer; callers scale required radii by that factor so a passing cell is
 * always clear in exact geometry.
 */
final class RoutingGrid {

  /** Largest ratio of octile to Euclidean distance (at 22.5°), rounded up. */
  static final double OCTILE_FACTOR = 1.0825;

  final int pitch;
  final int nx;
  final int ny;
  final int cells;

  /** Half the cell diagonal: how far a cell's copper can sit from its centre. */
  final double slack;

  RoutingGrid(int widthUm, int heightUm, int pitch) {
    if (pitch <= 0) {
      throw new IllegalArgumentException("grid pitch must be positive");
    }
    this.pitch = pitch;
    this.nx = Math.max(1, (widthUm + pitch - 1) / pitch);
    this.ny = Math.max(1, (heightUm + pitch - 1) / pitch);
    this.cells = nx * ny;
    this.slack = pitch * Math.sqrt(2) / 2;
  }

  int col(int cell) {
    return cell % nx;
  }

  int row(int cell) {
    return cell / nx;
  }

  int cell(int i, int j) {
    return j * nx + i;
  }

  boolean inGrid(int i, int j) {
    return i >= 0 && j >= 0 && i < nx && j < ny;
  }

  PointUm centre(int cell) {
    return new PointUm(col(cell) * pitch + pitch / 2, row(cell) * pitch + pitch / 2);
  }

  /** Cell containing the point, clamped to the grid. */
  int cellAt(PointUm p) {
    int i = Math.max(0, Math.min(nx - 1, Math.floorDiv(p.x(), pitch)));
    int j = Math.max(0, Math.min(ny - 1, Math.floorDiv(p.y(), pitch)));
    return cell(i, j);
  }

  /** Visits every cell whose square overlaps or touches the rectangle. */
  void forOverlapping(RectUm r, IntConsumer action) {
    int i0 = Math.max(0, Math.floorDiv(r.minX(), pitch));
    int i1 = Math.min(nx - 1, Math.floorDiv(r.maxX(), pitch));
    int j0 = Math.max(0, Math.floorDiv(r.minY(), pitch));
    int j1 = Math.min(ny - 1, Math.floorDiv(r.maxY(), pitch));
    for (int j = j0; j <= j1; j++) {
      for (int i = i0; i <= i1; i++) {
        action.accept(cell(i, j));
      }
    }
  }

  /**
   * Visits every cell whose centre lies inside the rectangle; if none does, the cell containing its
   * centre.
   */
  void forInterior(RectUm r, IntConsumer action) {
    boolean any = false;
    int i0 = Math.max(0, Math.floorDiv(r.minX(), pitch));
    int i1 = Math.min(nx - 1, Math.floorDiv(r.maxX(), pitch));
    int j0 = Math.max(0, Math.floorDiv(r.minY(), pitch));
    int j1 = Math.min(ny - 1, Math.floorDiv(r.maxY(), pitch));
    for (int j = j0; j <= j1; j++) {
      for (int i = i0; i <= i1; i++) {
        int c = cell(i, j);
        if (r.contains(centre(c))) {
          action.accept(c);
          any = true;
        }
      }
    }
    if (!any) {
      action.accept(cellAt(new PointUm((r.minX() + r.maxX()) / 2, (r.minY() + r.maxY()) / 2)));
    }
  }

  /** Visits every cell whose centre is within {@code radius} of segment ab. */
  void forNearSegment(PointUm a, PointUm b, double radius, IntConsumer action) {
    int r = (int) Math.ceil(radius);
    RectUm box =
        new RectUm(
            Math.min(a.x(), b.x()) - r,
            Math.min(a.y(), b.y()) - r,
            Math.max(a.x(), b.x()) + r,
            Math.max(a.y(), b.y()) + r);
    int i0 = Math.max(0, Math.floorDiv(box.minX(), pitch));
    int i1 = Math.min(nx - 1, Math.floorDiv(box.maxX(), pitch));
    int j0 = Math.max(0, Math.floorDiv(box.minY(), pitch));
    int j1 = Math.min(ny - 1, Math.floorDiv(box.maxY(), pitch));
    for (int j = j0; j <= j1; j++) {
      for (int i = i0; i <= i1; i++) {
        int c = cell(i, j);
        if (Segments.pointToSegment(centre(c), a, b) <= radius) {
          action.accept(c);
        }
      }
    }
  }

  /**
   * Octile distance in micrometres from every cell centre to the nearest seed cell centre; {@link
   * Float#MAX_VALUE} when there is no seed. Two passes of a 3×3 chamfer mask with exact axis and
   * diagonal weights.
   */
  float[] distanceTransform(IntPredicate seed) {
    float far = Float.MAX_VALUE / 4;
    float a = pitch;
    float b = (float) (pitch * Math.sqrt(2));
    float[] d = new float[cells];
    for (int c = 0; c < cells; c++) {
      d[c] = seed.test(c) ? 0f : far;
    }
    for (int j = 0; j < ny; j++) {
      for (int i = 0; i < nx; i++) {
        int c = cell(i, j);
        float v = d[c];
        if (i > 0) {
          v = Math.min(v, d[c - 1] + a);
        }
        if (j > 0) {
          v = Math.min(v, d[c - nx] + a);
          if (i > 0) {
            v = Math.min(v, d[c - nx - 1] + b);
          }
          if (i < nx - 1) {
            v = Math.min(v, d[c - nx + 1] + b);
          }
        }
        d[c] = v;
      }
    }
    for (int j = ny - 1; j >= 0; j--) {
      for (int i = nx - 1; i >= 0; i--) {
        int c = cell(i, j);
        float v = d[c];
        if (i < nx - 1) {
          v = Math.min(v, d[c + 1] + a);
        }
        if (j < ny - 1) {
          v = Math.min(v, d[c + nx] + a);
          if (i < nx - 1) {
            v = Math.min(v, d[c + nx + 1] + b);
          }
          if (i > 0) {
            v = Math.min(v, d[c + nx - 1] + b);
          }
        }
        d[c] = v;
      }
    }
    for (int c = 0; c < cells; c++) {
      if (d[c] >= far) {
        d[c] = Float.MAX_VALUE;
      }
    }
    return d;
  }
}
