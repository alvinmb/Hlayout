package app.freerouting.cli.hybrid;

import app.freerouting.autoroute.hybrid.HybridRouter;
import app.freerouting.board.hybrid.BondDiagram;
import app.freerouting.board.hybrid.BondDiagramEntry;
import app.freerouting.board.hybrid.HybridDesign;
import app.freerouting.board.hybrid.HybridValidator;
import app.freerouting.board.hybrid.RouteStats;
import app.freerouting.board.hybrid.RoutedHybrid;
import app.freerouting.board.hybrid.ValidationIssue;
import app.freerouting.board.hybrid.ValidationIssue.Severity;
import app.freerouting.drc.hybrid.HybridDrc;
import app.freerouting.drc.hybrid.HybridRouteDrc;
import app.freerouting.io.hybrid.BondDiagramCsv;
import app.freerouting.io.hybrid.HybridJson;
import java.io.IOException;
import java.io.PrintStream;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Command-line entry point that HLayout runs as a separate process.
 *
 * <pre>
 * java -cp freerouting-executable.jar app.freerouting.cli.hybrid.HybridRouterCli \
 *     design.hybrid.json [design.routed.json] [--force] [--bond-diagram wires.csv]
 * </pre>
 *
 * <p>Validates the design, runs the pre-route DRC, routes, runs the post-route DRC and writes the
 * routed result (with its DRC findings) as JSON. The output path defaults to the input path with
 * {@code .hybrid.json} (or {@code .json}) replaced by {@code .routed.json}. {@code --bond-diagram}
 * also writes the routed design's bond diagram (one line per wire, in bonding order) as CSV.
 *
 * <p>Exit codes: 0 routed completely and DRC-clean; 1 input invalid or pre-route DRC failed (no
 * output written; {@code --force} routes despite pre-route DRC errors); 2 routed but some
 * connections unrouted; 3 post-route DRC errors other than unrouted connections; 64 bad arguments;
 * 74 file error.
 */
public final class HybridRouterCli {

  static final int OK = 0;
  static final int INVALID = 1;
  static final int INCOMPLETE = 2;
  static final int DRC_ERRORS = 3;
  static final int USAGE = 64;
  static final int IO_ERROR = 74;

  private HybridRouterCli() {}

  /** Runs the router and exits with its status code. */
  public static void main(String[] args) {
    Charset console = consoleCharset();
    PrintStream out = new PrintStream(System.out, true, console);
    PrintStream err = new PrintStream(System.err, true, console);
    System.exit(run(args, out, err));
  }

  /** Runs the router; returns the exit code instead of exiting. */
  static int run(String[] args, PrintStream out, PrintStream err) {
    boolean force = false;
    Path bondDiagram = null;
    List<String> paths = new ArrayList<>();
    for (int i = 0; i < args.length; i++) {
      String a = args[i];
      if (a.equals("--force")) {
        force = true;
      } else if (a.equals("--bond-diagram")) {
        if (i + 1 >= args.length) {
          err.println("--bond-diagram needs a file name");
          return usage(err);
        }
        bondDiagram = Path.of(args[++i]);
      } else if (a.startsWith("--")) {
        err.println("unknown option " + a);
        return usage(err);
      } else {
        paths.add(a);
      }
    }
    if (paths.isEmpty() || paths.size() > 2) {
      return usage(err);
    }
    final Path input = Path.of(paths.get(0));
    final Path output = paths.size() == 2 ? Path.of(paths.get(1)) : defaultOutput(input);

    HybridDesign design;
    try {
      design = HybridJson.read(input);
    } catch (IOException e) {
      err.println("cannot read " + input + ": " + e.getMessage());
      return IO_ERROR;
    }
    List<ValidationIssue> validation = HybridValidator.validate(design);
    print(out, "validation", validation);
    if (!HybridValidator.isLoadable(validation)) {
      err.println("design is not valid; nothing routed");
      return INVALID;
    }
    List<ValidationIssue> preRoute = HybridDrc.check(design);
    print(out, "pre-route DRC", preRoute);
    if (!preRoute.isEmpty() && !force) {
      err.println("pre-route DRC failed; fix the design or use --force");
      return INVALID;
    }

    RoutedHybrid routed = HybridRouter.route(design);
    List<ValidationIssue> postRoute = HybridRouteDrc.check(routed);
    routed = routed.withIssues(postRoute);
    try {
      HybridJson.writeRouted(routed, output);
    } catch (IOException e) {
      err.println("cannot write " + output + ": " + e.getMessage());
      return IO_ERROR;
    }
    List<BondDiagramEntry> wires = BondDiagram.of(routed.design());
    if (bondDiagram != null) {
      try {
        BondDiagramCsv.write(wires, bondDiagram);
      } catch (IOException e) {
        err.println("cannot write " + bondDiagram + ": " + e.getMessage());
        return IO_ERROR;
      }
    }

    RouteStats s = routed.stats();
    out.printf(
        "routed %d/%d connections, %.1f mm of trace, %d windows, %d jumpers added, %d fingers"
            + " added (grid %d µm)%n",
        s.routed(),
        s.connections(),
        s.traceLengthUm() / 1000.0,
        s.windows(),
        s.jumpersAdded(),
        s.fingersAdded(),
        s.gridUm());
    print(out, "post-route DRC", postRoute);
    out.println("wrote " + output);
    if (bondDiagram != null) {
      out.println("wrote bond diagram (" + wires.size() + " wires) " + bondDiagram);
    }

    boolean otherErrors =
        postRoute.stream()
            .anyMatch(i -> i.severity() == Severity.ERROR && !i.code().equals("UNROUTED"));
    if (otherErrors) {
      return DRC_ERRORS;
    }
    return s.isComplete() ? OK : INCOMPLETE;
  }

  static Path defaultOutput(Path input) {
    String name = input.getFileName().toString();
    String base;
    if (name.endsWith(".hybrid.json")) {
      base = name.substring(0, name.length() - ".hybrid.json".length());
    } else if (name.endsWith(".json")) {
      base = name.substring(0, name.length() - ".json".length());
    } else {
      base = name;
    }
    return input.resolveSibling(base + ".routed.json");
  }

  private static void print(PrintStream out, String stage, List<ValidationIssue> issues) {
    if (issues.isEmpty()) {
      out.println(stage + ": clean");
      return;
    }
    out.println(stage + ": " + issues.size() + " issue(s)");
    for (ValidationIssue i : issues) {
      out.println("  " + i);
    }
  }

  private static int usage(PrintStream err) {
    err.println(
        "usage: HybridRouterCli <design.hybrid.json> [<design.routed.json>] [--force]"
            + " [--bond-diagram <wires.csv>]");
    return USAGE;
  }

  /**
   * The console's own encoding (Java 18+ {@code stdout.encoding}), so "µm" prints correctly in a
   * Windows console; UTF-8 when it is not set or not supported.
   */
  static Charset consoleCharset() {
    String name = System.getProperty("stdout.encoding");
    try {
      return name == null ? StandardCharsets.UTF_8 : Charset.forName(name);
    } catch (IllegalArgumentException e) {
      return StandardCharsets.UTF_8;
    }
  }
}
