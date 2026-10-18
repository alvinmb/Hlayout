package app.freerouting.board.hybrid;

import java.io.Serializable;

/**
 * A connection the router could not make.
 *
 * @param net net name
 * @param from a terminal id on the routed side, e.g. "F1" or "R2.A"
 * @param to a terminal id it could not reach
 */
public record UnroutedConnection(String net, String from, String to) implements Serializable {}
