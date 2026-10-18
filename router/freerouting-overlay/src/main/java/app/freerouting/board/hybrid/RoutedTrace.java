package app.freerouting.board.hybrid;

import java.io.Serializable;
import java.util.List;

/**
 * A printed trace placed by the router: a polyline of centre-line points on one conductor layer.
 *
 * @param net net carried
 * @param layer conductor layer
 * @param points centre-line vertices on the substrate, at least two
 * @param widthUm printed width
 */
public record RoutedTrace(String net, String layer, List<PointUm> points, int widthUm)
    implements Serializable {

  /** Copies the points; null becomes empty. */
  public RoutedTrace {
    points = HybridLists.copy(points);
  }

  /** Total centre-line length in micrometres. */
  public double lengthUm() {
    double length = 0;
    for (int i = 1; i < points.size(); i++) {
      length += points.get(i - 1).distanceTo(points.get(i));
    }
    return length;
  }
}
