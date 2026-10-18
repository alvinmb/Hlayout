package app.freerouting.board.hybrid;

import java.io.Serializable;

/**
 * An opening in the dielectric that joins a trace on one conductor layer to the next. Each
 * conductor prints a square pad of {@code padUm} over it; the dielectric opening is {@code
 * openingUm}.
 *
 * @param net net carried
 * @param position window centre on the substrate
 * @param openingUm side of the square dielectric opening
 * @param padUm side of the square conductor pad printed over the opening on both layers
 * @param fromLayer lower conductor layer
 * @param toLayer upper conductor layer
 */
public record CrossoverWindow(
    String net, PointUm position, int openingUm, int padUm, String fromLayer, String toLayer)
    implements Serializable {

  /** Conductor pad outline on the substrate. */
  public RectUm padBounds() {
    return RectUm.centred(position, padUm, padUm);
  }
}
