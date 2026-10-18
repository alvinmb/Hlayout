package app.freerouting.board.hybrid;

import app.freerouting.board.model.structure.Layer;
import app.freerouting.board.model.structure.LayerStructure;
import java.io.Serializable;
import java.util.Collection;
import java.util.List;
import java.util.Optional;
import java.util.stream.Collectors;

/**
 * The printed layer stack in print order, e.g. COND1, DIEL1, COND2, RES, GLAZE.
 *
 * <p>Only {@link HybridLayerKind#CONDUCTOR} layers are visible to the router; the rest are carried
 * for DRC, artwork and windows.
 */
public record HybridLayerStack(List<HybridLayer> layers) implements Serializable {

  /** Copies the layers; null becomes an empty stack. */
  public HybridLayerStack {
    layers = HybridLists.copy(layers);
  }

  /** The layer with this name, if any. */
  public Optional<HybridLayer> find(String name) {
    return layers.stream().filter(l -> l.name() != null && l.name().equals(name)).findFirst();
  }

  /** Print-order index of the named layer, or -1. */
  public int indexOf(String name) {
    for (int i = 0; i < layers.size(); i++) {
      if (name != null && name.equals(layers.get(i).name())) {
        return i;
      }
    }
    return -1;
  }

  /** Conductor layers in print order. */
  public List<HybridLayer> conductorLayers() {
    return layers.stream().filter(l -> l.kind() == HybridLayerKind.CONDUCTOR).toList();
  }

  /** True if the named layer exists and is of this kind. */
  public boolean isKind(String name, HybridLayerKind kind) {
    return find(name).map(l -> l.kind() == kind).orElse(false);
  }

  /**
   * True if a dielectric layer is printed between the two named conductor layers, so a window in it
   * can join them.
   */
  public boolean hasDielectricBetween(String conductorA, String conductorB) {
    int a = indexOf(conductorA);
    int b = indexOf(conductorB);
    if (a < 0 || b < 0 || a == b) {
      return false;
    }
    for (int i = Math.min(a, b) + 1; i < Math.max(a, b); i++) {
      if (layers.get(i).kind() == HybridLayerKind.DIELECTRIC) {
        return true;
      }
    }
    return false;
  }

  /**
   * Builds the router's layer structure: every hybrid layer becomes a board layer, and only
   * conductor layers are marked as signal layers.
   */
  public LayerStructure toLayerStructure() {
    return toLayerStructure(
        conductorLayers().stream().map(HybridLayer::name).collect(Collectors.toSet()));
  }

  /**
   * Builds the router's layer structure with only the named conductor layers marked as signal
   * layers. Other conductor layers stay in the structure (pads and obstacles may sit on them) but
   * the router will not place traces there. Passing one name gives single-layer routing.
   */
  public LayerStructure toLayerStructure(Collection<String> routingLayers) {
    Layer[] boardLayers = new Layer[layers.size()];
    for (int i = 0; i < layers.size(); i++) {
      HybridLayer layer = layers.get(i);
      boolean signal =
          layer.kind() == HybridLayerKind.CONDUCTOR && routingLayers.contains(layer.name());
      boardLayers[i] = new Layer(layer.name(), signal);
    }
    return new LayerStructure(boardLayers);
  }

  /** Single-layer thick-film stack: one conductor, resistor print, overglaze. No dielectric. */
  public static HybridLayerStack singleLayerThickFilm() {
    return new HybridLayerStack(
        List.of(
            new HybridLayer("COND1", HybridLayerKind.CONDUCTOR, 12),
            new HybridLayer("RES", HybridLayerKind.RESISTOR, 15),
            new HybridLayer("GLAZE", HybridLayerKind.OVERGLAZE, 10)));
  }

  /** Default thick-film stack: two conductors, one dielectric, one resistor print, overglaze. */
  public static HybridLayerStack defaultThickFilm() {
    return new HybridLayerStack(
        List.of(
            new HybridLayer("COND1", HybridLayerKind.CONDUCTOR, 12),
            new HybridLayer("DIEL1", HybridLayerKind.DIELECTRIC, 40),
            new HybridLayer("COND2", HybridLayerKind.CONDUCTOR, 12),
            new HybridLayer("RES", HybridLayerKind.RESISTOR, 15),
            new HybridLayer("GLAZE", HybridLayerKind.OVERGLAZE, 10)));
  }
}
