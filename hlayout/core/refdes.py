"""Ref-designator placement helpers.

Connectors and headers (ref designators J, P, X, CN, CON, HDR followed by
a number) conventionally carry their ref-des next to pin 1, so the
assembler can find pin 1 and the part at a glance. The floated ref-des
annotation of a placed instance is drawn at the centre of the instance's
bounding box plus SRef.label_offset (DBU, parent coordinates -- see
LayoutCanvas._sref_label_anchor). refdes_pin1_offset() computes the
label_offset that puts it right beside pin 1, on the outside of the
pin row.
"""

import math
import re

from .library import _transform_bbox

#: ref designators treated as connectors/headers
CONNECTOR_REFDES = re.compile(r"^(J|P|X|CN|CON|HDR)\d", re.IGNORECASE)

#: default gap (DBU) between pin 1's pad and the ref-des anchor
DEFAULT_PIN1_GAP = 800


def is_connector_refdes(label: str) -> bool:
    return bool(label) and bool(CONNECTOR_REFDES.match(label.strip()))


def _bbox_centre(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def cell_pin1_bbox(library, cell_name, pin="1"):
    """Local bbox (x1, y1, x2, y2) DBU of pin `pin` in `cell_name`: a
    pinned shape's bbox, or a pin-numbered placement's bbox (a lead
    built from a shared template). None if the cell has no such pin."""
    cell = library.cells.get(cell_name)
    if cell is None:
        return None
    for sh in cell.shapes:
        if getattr(sh, "pin", "") == pin:
            try:
                return tuple(sh.bbox())
            except Exception:
                continue
    for ref in cell.srefs:
        if getattr(ref, "pin", "") == pin:
            box = library.cell_bbox(ref.cell_name)
            if box is not None:
                return _transform_bbox(box, ref)
            return (ref.origin[0], ref.origin[1], ref.origin[0], ref.origin[1])
    return None


def cell_pin1(library, cell_name, pin="1"):
    """Local (x, y) DBU centre of pin `pin` in `cell_name`, or None."""
    b = cell_pin1_bbox(library, cell_name, pin)
    return None if b is None else _bbox_centre(b)


def _to_parent(ref, pt):
    """Map a point in ref's cell into the parent (GDSII SREF order:
    mirror, scale, rotate, translate)."""
    x, y = pt
    if ref.x_reflection:
        y = -y
    x *= ref.magnification
    y *= ref.magnification
    t = math.radians(ref.rotation)
    xr = x * math.cos(t) - y * math.sin(t)
    yr = x * math.sin(t) + y * math.cos(t)
    return (xr + ref.origin[0], yr + ref.origin[1])


def refdes_pin1_offset(library, ref, gap=DEFAULT_PIN1_GAP):
    """The SRef.label_offset that places `ref`'s ref-des right beside
    its pin 1: `gap` DBU beyond pin 1's pad, on the side facing the
    nearest edge of the part (left/right preferred over top/bottom on a
    tie, since header rows end there) -- i.e. on the outside of the pin
    row, not between pins. None if the part has no pin 1."""
    pb = cell_pin1_bbox(library, ref.cell_name)
    box = library.cell_bbox(ref.cell_name)
    if pb is None or box is None:
        return None
    pad = _transform_bbox(pb, ref)
    px, py = _bbox_centre(pad)
    half_w, half_h = (pad[2] - pad[0]) / 2.0, (pad[3] - pad[1]) / 2.0
    x1, y1, x2, y2 = _transform_bbox(box, ref)
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    d_left, d_right, d_bottom, d_top = px - x1, x2 - px, py - y1, y2 - py
    side = min((d_left, 0, "left"), (d_right, 1, "right"),
               (d_bottom, 2, "bottom"), (d_top, 3, "top"))[2]
    if side == "left":
        ax, ay = px - half_w - gap, py
    elif side == "right":
        ax, ay = px + half_w + gap, py
    elif side == "bottom":
        ax, ay = px, py - half_h - gap
    else:
        ax, ay = px, py + half_h + gap
    return (int(round(ax - cx)), int(round(ay - cy)))
