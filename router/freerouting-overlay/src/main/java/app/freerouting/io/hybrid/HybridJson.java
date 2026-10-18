package app.freerouting.io.hybrid;

import app.freerouting.board.hybrid.HybridDesign;
import app.freerouting.board.hybrid.RoutedHybrid;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonIOException;
import com.google.gson.JsonParseException;
import java.io.IOException;
import java.io.Reader;
import java.io.StringReader;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Reads and writes {@link HybridDesign} as {@code hybrid.json} (schema in {@code
 * docs/hybrid/hybrid.schema.json}). Lengths are integer micrometres; enums are written by name.
 *
 * <p>Reading does not validate: run {@code HybridValidator.validate} on the result.
 */
public final class HybridJson {

  private static final Gson GSON =
      new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();

  private HybridJson() {}

  /** Reads a design; malformed or empty JSON becomes an {@link IOException}. */
  public static HybridDesign read(Reader reader) throws IOException {
    try {
      HybridDesign design = GSON.fromJson(reader, HybridDesign.class);
      if (design == null) {
        throw new IOException("hybrid design file is empty");
      }
      return design;
    } catch (JsonParseException e) {
      throw new IOException("invalid hybrid design JSON: " + e.getMessage(), e);
    }
  }

  /** Reads a UTF-8 {@code hybrid.json} file. */
  public static HybridDesign read(Path path) throws IOException {
    try (Reader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
      return read(reader);
    }
  }

  /** Reads a design from a JSON string. */
  public static HybridDesign fromString(String json) throws IOException {
    return read(new StringReader(json));
  }

  /** Writes the design as pretty-printed JSON. */
  public static void write(HybridDesign design, Writer writer) throws IOException {
    try {
      GSON.toJson(design, HybridDesign.class, writer);
      writer.flush();
    } catch (JsonIOException e) {
      throw new IOException("failed to write hybrid design: " + e.getMessage(), e);
    }
  }

  /** Writes the design to a UTF-8 file. */
  public static void write(HybridDesign design, Path path) throws IOException {
    try (Writer writer = Files.newBufferedWriter(path, StandardCharsets.UTF_8)) {
      write(design, writer);
    }
  }

  /** The design as pretty-printed JSON. */
  public static String toString(HybridDesign design) {
    return GSON.toJson(design, HybridDesign.class);
  }

  /** Reads a routed result ({@code *.routed.json}). */
  public static RoutedHybrid readRouted(Reader reader) throws IOException {
    try {
      RoutedHybrid routed = GSON.fromJson(reader, RoutedHybrid.class);
      if (routed == null || routed.design() == null) {
        throw new IOException("routed hybrid file is empty or has no design");
      }
      return routed;
    } catch (JsonParseException | NullPointerException e) {
      throw new IOException("invalid routed hybrid JSON: " + e.getMessage(), e);
    }
  }

  /** Reads a UTF-8 routed result file. */
  public static RoutedHybrid readRouted(Path path) throws IOException {
    try (Reader reader = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
      return readRouted(reader);
    }
  }

  /** Writes a routed result as pretty-printed JSON. */
  public static void writeRouted(RoutedHybrid routed, Writer writer) throws IOException {
    try {
      GSON.toJson(routed, RoutedHybrid.class, writer);
      writer.flush();
    } catch (JsonIOException e) {
      throw new IOException("failed to write routed hybrid: " + e.getMessage(), e);
    }
  }

  /** Writes a routed result to a UTF-8 file. */
  public static void writeRouted(RoutedHybrid routed, Path path) throws IOException {
    try (Writer writer = Files.newBufferedWriter(path, StandardCharsets.UTF_8)) {
      writeRouted(routed, writer);
    }
  }

  /** The routed result as pretty-printed JSON. */
  public static String routedToString(RoutedHybrid routed) {
    return GSON.toJson(routed, RoutedHybrid.class);
  }
}
