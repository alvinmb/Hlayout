package app.freerouting.board.hybrid;

import java.io.Serializable;
import java.util.List;
import java.util.Objects;
import java.util.Optional;

/**
 * A bare semiconductor die attached face-up to the substrate.
 *
 * @param refdes unique reference, e.g. "U1"
 * @param partName die type, e.g. "LM324-die"
 * @param origin substrate position of the die-local origin
 * @param rotation counter-clockwise rotation about the origin
 * @param widthUm die width before rotation
 * @param heightUm die height before rotation
 * @param thicknessUm die thickness, for loop-height and 3D checks
 * @param attachKeepoutUm epoxy/eutectic fillet margin around the die where nothing may be printed
 * @param pads bond pads, die-local
 */
public record Die(
    String refdes,
    String partName,
    PointUm origin,
    Rotation rotation,
    int widthUm,
    int heightUm,
    int thicknessUm,
    int attachKeepoutUm,
    List<BondPad> pads)
    implements Serializable {

  /** Fills missing origin, rotation and pad list with defaults. */
  public Die {
    origin = origin == null ? PointUm.ORIGIN : origin;
    rotation = Objects.requireNonNullElse(rotation, Rotation.R0);
    pads = HybridLists.copy(pads);
  }

  /** The pad with this id, if the die has one. */
  public Optional<BondPad> pad(String padId) {
    return pads.stream().filter(p -> Objects.equals(p.id(), padId)).findFirst();
  }

  /** Substrate position of a die-local point. */
  public PointUm toSubstrate(PointUm local) {
    return origin.plus(rotation.apply(local));
  }

  /** Substrate position of the pad's centre. */
  public PointUm padPosition(BondPad pad) {
    return toSubstrate(pad.position());
  }

  /** Die outline on the substrate. */
  public RectUm bounds() {
    return RectUm.bounding(corners());
  }

  /** Pad opening on the substrate (width and height swap on a quarter turn). */
  public RectUm padBounds(BondPad pad) {
    boolean swap = rotation.isQuarterTurn();
    int w = swap ? pad.heightUm() : pad.widthUm();
    int h = swap ? pad.widthUm() : pad.heightUm();
    return RectUm.centred(padPosition(pad), w, h);
  }

  /**
   * Outward normal of the die edge nearest the pad, as a unit step on the substrate: one of (1, 0),
   * (-1, 0), (0, 1), (0, -1). A wire bonded to the pad should leave roughly this way.
   */
  public PointUm padOutwardNormal(BondPad pad) {
    PointUm p = pad.position();
    int left = p.x();
    int right = widthUm - p.x();
    int bottom = p.y();
    int top = heightUm - p.y();
    int nearest = Math.min(Math.min(left, right), Math.min(bottom, top));
    PointUm local;
    if (nearest == bottom) {
      local = new PointUm(0, -1);
    } else if (nearest == top) {
      local = new PointUm(0, 1);
    } else if (nearest == left) {
      local = new PointUm(-1, 0);
    } else {
      local = new PointUm(1, 0);
    }
    return rotation.apply(local);
  }

  /** The four die corners on the substrate, counter-clockwise from the origin. */
  public List<PointUm> corners() {
    return List.of(
        toSubstrate(PointUm.ORIGIN),
        toSubstrate(new PointUm(widthUm, 0)),
        toSubstrate(new PointUm(widthUm, heightUm)),
        toSubstrate(new PointUm(0, heightUm)));
  }
}
