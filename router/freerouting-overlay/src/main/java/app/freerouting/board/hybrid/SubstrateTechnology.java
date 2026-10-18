package app.freerouting.board.hybrid;

/** Substrate process family. Thin film is deliberately not supported. */
public enum SubstrateTechnology {
  /** Screen-printed conductors, dielectrics and resistors fired on a ceramic (usually alumina). */
  THICK_FILM,
  /** Low-temperature co-fired ceramic: buried conductor layers joined by filled tape vias. */
  LTCC
}
