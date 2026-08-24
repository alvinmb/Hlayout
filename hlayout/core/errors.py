"""Exceptions raised by the hlayout core data model."""


class LayoutError(Exception):
    """Base class for all hlayout data-model errors."""


class LayerError(LayoutError):
    """Invalid layer number or layer-related operation."""


class GeometryError(LayoutError):
    """A shape violates the point-count, width, or other geometric limits."""


class BoundsError(LayoutError):
    """Geometry falls outside the 500 x 500 mm database extent."""


class CellError(LayoutError):
    """Invalid cell reference or a cyclic Sref hierarchy."""


class LibraryError(LayoutError):
    """Invalid attached-library operation (attach limit, unknown source, etc)."""
