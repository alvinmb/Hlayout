package app.freerouting.board.hybrid;

/** What a two-pin component is. The router treats every kind the same way. */
public enum TwoPinKind {
  /** Screen-printed thick-film resistor, sized and checked by HLayout. */
  PRINTED_RESISTOR,
  /** Surface-mounted chip (capacitor, resistor, inductor) epoxied or soldered to the substrate. */
  CHIP
}
