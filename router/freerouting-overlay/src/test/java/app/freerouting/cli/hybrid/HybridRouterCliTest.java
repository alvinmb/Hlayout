package app.freerouting.cli.hybrid;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import app.freerouting.board.hybrid.RoutedHybrid;
import app.freerouting.io.hybrid.HybridJson;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;

class HybridRouterCliTest {

  @Test
  void routesFixtureAndWritesTheResult() throws IOException {
    Path dir = Files.createTempDirectory("hybrid-cli");
    Path input = copyFixture("single-layer-crossing", dir);
    ByteArrayOutputStream out = new ByteArrayOutputStream();
    int code = run(out, input.toString());

    assertEquals(HybridRouterCli.OK, code, out::toString);
    Path output = dir.resolve("single-layer-crossing.routed.json");
    assertTrue(Files.exists(output));
    RoutedHybrid routed = HybridJson.readRouted(output);
    assertTrue(routed.stats().isComplete());
    assertTrue(routed.issues().isEmpty());
    assertTrue(out.toString(StandardCharsets.UTF_8).contains("post-route DRC: clean"));
  }

  @Test
  void writesTheBondDiagramWhenAsked() throws IOException {
    Path dir = Files.createTempDirectory("hybrid-cli");
    Path input = copyFixture("single-layer-crossing", dir);
    Path output = dir.resolve("out.routed.json");
    Path csv = dir.resolve("wires.csv");
    ByteArrayOutputStream out = new ByteArrayOutputStream();

    assertEquals(
        HybridRouterCli.OK,
        run(out, input.toString(), output.toString(), "--bond-diagram", csv.toString()),
        out::toString);
    List<String> lines = Files.readAllLines(csv);
    RoutedHybrid routed = HybridJson.readRouted(output);
    assertEquals(routed.design().wires().size() + 1, lines.size());
    assertTrue(lines.get(0).startsWith("seq,wire,net,purpose"));
    assertTrue(lines.stream().anyMatch(l -> l.contains(",JUMPER,")), "jumpers are listed");
    assertTrue(out.toString(StandardCharsets.UTF_8).contains("wrote bond diagram"));
  }

  @Test
  void bondDiagramOptionNeedsFileName() {
    assertEquals(
        HybridRouterCli.USAGE, run(new ByteArrayOutputStream(), "a.json", "--bond-diagram"));
  }

  @Test
  void incompleteRoutingExitsWithTwo() throws IOException {
    Path dir = Files.createTempDirectory("hybrid-cli");
    Path input = copyFixture("single-layer-crossing", dir);
    String json =
        Files.readString(input).replace("\"jumpersAllowed\": true", "\"jumpersAllowed\": false");
    Files.writeString(input, json);
    Path output = dir.resolve("out.json");

    assertEquals(
        HybridRouterCli.INCOMPLETE,
        run(new ByteArrayOutputStream(), input.toString(), output.toString()));
    assertTrue(
        HybridJson.readRouted(output).issues().stream().allMatch(i -> i.code().equals("UNROUTED")));
  }

  @Test
  void invalidDesignIsRejectedWithoutOutput() throws IOException {
    Path dir = Files.createTempDirectory("hybrid-cli");
    Path input = dir.resolve("bad.hybrid.json");
    Files.writeString(input, "{\"schemaVersion\": 1, \"name\": \"empty\"}");

    assertEquals(HybridRouterCli.INVALID, run(new ByteArrayOutputStream(), input.toString()));
    assertTrue(Files.notExists(dir.resolve("bad.routed.json")));
  }

  @Test
  void badArgumentsAndMissingFiles() {
    ByteArrayOutputStream sink = new ByteArrayOutputStream();
    assertEquals(HybridRouterCli.USAGE, run(sink));
    assertEquals(HybridRouterCli.USAGE, run(sink, "a.json", "--bogus"));
    assertEquals(HybridRouterCli.IO_ERROR, run(sink, "/nonexistent/x.hybrid.json"));
  }

  @Test
  void defaultOutputNameReplacesTheSuffix() {
    assertEquals(
        Path.of("d/x.routed.json"), HybridRouterCli.defaultOutput(Path.of("d/x.hybrid.json")));
    assertEquals(Path.of("d/x.routed.json"), HybridRouterCli.defaultOutput(Path.of("d/x.json")));
    assertEquals(Path.of("d/x.routed.json"), HybridRouterCli.defaultOutput(Path.of("d/x")));
  }

  private static int run(ByteArrayOutputStream out, String... args) {
    PrintStream ps = new PrintStream(out, true, StandardCharsets.UTF_8);
    return HybridRouterCli.run(args, ps, ps);
  }

  private static Path copyFixture(String name, Path dir) throws IOException {
    Path target = dir.resolve(name + ".hybrid.json");
    try (InputStream in =
        HybridRouterCliTest.class.getResourceAsStream("/hybrid/" + name + ".hybrid.json")) {
      Files.copy(in, target);
    }
    return target;
  }
}
