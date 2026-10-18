package app.freerouting.board.hybrid;

import java.util.List;
import java.util.Objects;

/** Null-tolerant list handling for records filled from JSON. */
final class HybridLists {

  private HybridLists() {}

  /** Immutable copy; null becomes empty and null elements are dropped. */
  static <T> List<T> copy(List<T> source) {
    if (source == null) {
      return List.of();
    }
    return source.stream().filter(Objects::nonNull).toList();
  }
}
