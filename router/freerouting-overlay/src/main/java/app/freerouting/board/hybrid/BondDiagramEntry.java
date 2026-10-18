package app.freerouting.board.hybrid;

import java.io.Serializable;

/**
 * One row of a bond diagram: a wire as the wire bonder needs it.
 *
 * @param sequence bonding order, from 1
 * @param wireId wire id
 * @param net net carried by the wire
 * @param purpose die bond, die-to-die or jumper
 * @param firstBond first bond (the ball for ball-wedge): {@code die.pad} or a substrate pad id
 * @param first substrate position of the first bond
 * @param secondBond second bond: {@code die.pad} or a substrate pad id
 * @param second substrate position of the second bond
 * @param material wire metal
 * @param diameterUm wire diameter
 * @param bondType ball-wedge or wedge-wedge
 * @param loopHeightUm loop apex above the higher bond surface
 * @param lengthUm plan-view length, pad centre to pad centre, rounded to the micrometre
 * @param firstAngleDeg angle between the wire and the die-edge normal at the first bond; null if
 *     the first bond is not on a die
 * @param secondAngleDeg the same at the second bond; null if it is not on a die
 * @param fixed true if the wire was fixed in the input design
 */
public record BondDiagramEntry(
    int sequence,
    String wireId,
    String net,
    WirePurpose purpose,
    String firstBond,
    PointUm first,
    String secondBond,
    PointUm second,
    WireMaterial material,
    int diameterUm,
    BondType bondType,
    int loopHeightUm,
    int lengthUm,
    Double firstAngleDeg,
    Double secondAngleDeg,
    boolean fixed)
    implements Serializable {}
