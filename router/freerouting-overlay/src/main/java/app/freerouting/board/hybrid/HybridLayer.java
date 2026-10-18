package app.freerouting.board.hybrid;

import java.io.Serializable;

/**
 * One layer of the hybrid stack, listed in print order (first printed = closest to the ceramic).
 * Missing fields are reported by {@link HybridValidator}, not rejected here, so a bad input file
 * yields a full issue list instead of the first exception.
 *
 * @param name unique layer name, e.g. "COND1"
 * @param kind layer role
 * @param thicknessUm fired thickness
 */
public record HybridLayer(String name, HybridLayerKind kind, int thicknessUm)
    implements Serializable {}
