"""Layer table: 64 layers, each with a colour and one of 9 stipple patterns."""

from dataclasses import dataclass, field

from .errors import LayerError

NUM_LAYERS = 64
NUM_STIPPLES = 9

# A small default palette that cycles across the 64 layers so a freshly
# created library is immediately legible; users can rename/recolour freely.
_DEFAULT_COLORS = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#46f0f0", "#f032e6", "#bcf60c", "#fabebe",
    "#008080", "#e6beff", "#9a6324", "#fffac8", "#800000",
    "#aaffc3",
]

#: 9 stipple (fill) patterns, indexed 0-8. 0 = solid fill.
STIPPLE_NAMES = [
    "solid",
    "horizontal",
    "vertical",
    "diagonal-/",
    "diagonal-\\",
    "cross",
    "diagonal-cross",
    "dots-sparse",
    "dots-dense",
]


@dataclass
class Layer:
    number: int
    name: str = ""
    color: str = "#ffffff"  # hex RGB
    stipple: int = 0  # index into STIPPLE_NAMES, 0-8 (ignored when fill=False)
    fill: bool = True  # False = outline only, no interior fill/stipple
    visible: bool = True
    selectable: bool = True

    def __post_init__(self):
        if not (0 <= self.number < NUM_LAYERS):
            raise LayerError(f"layer number {self.number} out of range 0-{NUM_LAYERS - 1}")
        if not (0 <= self.stipple < NUM_STIPPLES):
            raise LayerError(f"stipple {self.stipple} out of range 0-{NUM_STIPPLES - 1}")
        if not self.name:
            self.name = f"L{self.number}"


class LayerTable:
    """Fixed-size table of the 64 layers available in a library."""

    def __init__(self):
        self._layers = {}
        for n in range(NUM_LAYERS):
            self._layers[n] = Layer(
                number=n,
                name=f"L{n}",
                color=_DEFAULT_COLORS[n % len(_DEFAULT_COLORS)],
                stipple=n % NUM_STIPPLES,
            )

    def __getitem__(self, number: int) -> Layer:
        try:
            return self._layers[number]
        except KeyError:
            raise LayerError(f"layer number {number} out of range 0-{NUM_LAYERS - 1}") from None

    def __iter__(self):
        return iter(self._layers.values())

    def __len__(self):
        return len(self._layers)

    def set_style(self, number: int, *, name=None, color=None, stipple=None,
                  fill=None, visible=None, selectable=None):
        layer = self[number]
        if name is not None:
            layer.name = name
        if color is not None:
            layer.color = color
        if stipple is not None:
            if not (0 <= stipple < NUM_STIPPLES):
                raise LayerError(f"stipple {stipple} out of range 0-{NUM_STIPPLES - 1}")
            layer.stipple = stipple
        if fill is not None:
            layer.fill = fill
        if visible is not None:
            layer.visible = visible
        if selectable is not None:
            layer.selectable = selectable
        return layer

    def by_name(self, name: str) -> Layer:
        for layer in self._layers.values():
            if layer.name == name:
                return layer
        # fall back to a case-insensitive match, for convenience when
        # typing layer names in the command bar/scripts
        lowered = name.lower()
        for layer in self._layers.values():
            if layer.name.lower() == lowered:
                return layer
        raise LayerError(f"no layer named {name!r}")

    def resolve(self, value) -> int:
        """Resolve a layer number OR layer name to a layer number.

        Accepts an int, a numeric string ("5"), or a layer name
        ("Thick-Film Conductor"). Raises LayerError if it matches
        neither a valid layer number nor a known layer name.
        """
        if isinstance(value, int):
            return self[value].number
        s = str(value).strip()
        if s.lstrip("-").isdigit():
            return self[int(s)].number
        return self.by_name(s).number
