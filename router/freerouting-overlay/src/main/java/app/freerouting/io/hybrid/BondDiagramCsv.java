package app.freerouting.io.hybrid;

import app.freerouting.board.hybrid.BondDiagramEntry;
import java.io.IOException;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Locale;

/**
 * Writes a bond diagram as CSV for the wire bonder and the assembly drawing. Lengths and positions
 * are micrometres on the substrate, origin at its lower-left corner.
 */
public final class BondDiagramCsv {

  /** The CSV header line. */
  public static final String HEADER =
      "seq,wire,net,purpose,first_bond,first_x_um,first_y_um,second_bond,second_x_um,second_y_um,"
          + "material,diameter_um,bond_type,loop_height_um,length_um,first_angle_deg,"
          + "second_angle_deg,fixed";

  private BondDiagramCsv() {}

  /** Writes the header and one line per entry. */
  public static void write(List<BondDiagramEntry> rows, Writer out) throws IOException {
    out.write(HEADER);
    out.write("\n");
    for (BondDiagramEntry r : rows) {
      out.write(
          String.join(
              ",",
              Integer.toString(r.sequence()),
              field(r.wireId()),
              field(r.net()),
              String.valueOf(r.purpose()),
              field(r.firstBond()),
              Integer.toString(r.first().x()),
              Integer.toString(r.first().y()),
              field(r.secondBond()),
              Integer.toString(r.second().x()),
              Integer.toString(r.second().y()),
              String.valueOf(r.material()),
              Integer.toString(r.diameterUm()),
              String.valueOf(r.bondType()),
              Integer.toString(r.loopHeightUm()),
              Integer.toString(r.lengthUm()),
              angle(r.firstAngleDeg()),
              angle(r.secondAngleDeg()),
              Boolean.toString(r.fixed())));
      out.write("\n");
    }
  }

  /** Writes the CSV to a file as UTF-8. */
  public static void write(List<BondDiagramEntry> rows, Path path) throws IOException {
    try (Writer w = Files.newBufferedWriter(path, StandardCharsets.UTF_8)) {
      write(rows, w);
    }
  }

  private static String angle(Double deg) {
    return deg == null ? "" : String.format(Locale.ROOT, "%.1f", deg);
  }

  /** Quotes a field when it holds a comma, quote or line break. */
  static String field(String s) {
    if (s == null) {
      return "";
    }
    if (s.indexOf(',') < 0 && s.indexOf('"') < 0 && s.indexOf('\n') < 0 && s.indexOf('\r') < 0) {
      return s;
    }
    return '"' + s.replace("\"", "\"\"") + '"';
  }
}
