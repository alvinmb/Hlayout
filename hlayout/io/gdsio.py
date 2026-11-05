# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""GDSII import/export — the default on-disk database format.

1 hlayout database unit (DBU) = 1 micron (0.001 mm) — matching the
database unit most external GDSII libraries use — so the gdstk library
is created with unit=1e-6 (metres) which makes gdstk "user units"
identical to our integer DBU coordinates.

Format conventions used by this module (documented since GDSII itself
does not natively distinguish these hlayout concepts):
  * Rectangle vs. Polygon: any 4-point boundary is read back as a
    Rectangle; boundaries with any other point count are read back as
    a Polygon. This tool always writes Rectangles as 4-point boundaries,
    so round-tripping files produced by hlayout is exact.
  * Path caps: PathCap.ROUND <-> GDS/gdstk "round", PathCap.SQUARE <->
    "extended" (half-width projecting cap), PathCap.FLUSH <-> "flush".
    Paths are written with simple_path=True so they stay true GDSII
    PATH elements (not decomposed into boundaries) on disk.
  * Text: GDSII has no explicit font-size field. Height is encoded as
    ``magnification * TEXT_BASE_HEIGHT`` and font (0-3) is encoded in
    the GDS texttype field, matching GDSII's native 4-font model.
  * Cell date/time stamps: Cell.created / Cell.modified are the
    creation and modification dates of the GDSII BGNSTR record. gdstk
    writes one timestamp for every structure, so save_gds() patches each
    BGNSTR afterwards and load_gds() reads them back by scanning the
    records itself (see _patch_cell_stamps / read_cell_stamps).
"""

import math
import os
import struct
import warnings
from datetime import datetime
from typing import Dict, Optional, Tuple

import gdstk

from ..core.cell import Cell
from ..core.errors import LayoutError
from ..core.library import Library
from ..core.shapes import ARef, Path, PathCap, Polygon, Rectangle, SRef, Text
from ..core.units import DBU_MM

UNIT = DBU_MM * 1e-3  # metres per DBU (DBU_MM is in mm; 1 mm = 1e-3 m)
PRECISION = 1e-9  # metres (1 nm internal precision)
TEXT_BASE_HEIGHT = 10  # DBU; magnification 1.0 <=> this text height

# GDSII element property numbers (PROPATTR, 1-127) used to round-trip
# SRef.label / SRef.label_height -- per-instance annotation and its
# on-screen text size -- through real GDSII files. GDSII's native SREF
# element has no text field of its own, so these ride along as standard
# element properties (PROPATTR/PROPVALUE records), the same mechanism
# CAD tools use for arbitrary per-element metadata. Chosen arbitrarily
# in the unreserved range; not used for anything else in this codebase.
SREF_LABEL_PROPATTR = 126
SREF_LABEL_HEIGHT_PROPATTR = 125
# "dx,dy" DBU shift of the floated label from the instance centre.
SREF_LABEL_OFFSET_PROPATTR = 122

# Same idea, for Rectangle.pin / Polygon.pin -- a pad's pin number/name.
# GDSII boundaries have no native "pin" field either, so this rides
# along the same way as a standard element property.
RECT_PIN_PROPATTR = 124
POLY_PIN_PROPATTR = 123

# Same idea again, for SRef.pin -- a placed instance's own pin number
# (e.g. a Via Wizard-built round lead), distinct from SREF_LABEL_PROPATTR
# (the ref-designator annotation) -- see SRef.pin's own docstring.
SREF_PIN_PROPATTR = 121

# A cell's shapes are one ordered list here, but GDSII (via gdstk) hands
# them back grouped by element type -- boundaries, then paths, then
# labels. Anything addressed by position (list_pins()/PIN SET indices,
# DELETE LAST) would shift after a save/open, so a cell whose order is
# NOT already that grouped order records each shape's index as an
# element property and load_gds() sorts by it. Cells already in grouped
# order (the common case: all rectangles, say) are written unchanged.
SHAPE_ORDER_PROPATTR = 120


def _kind_rank(shape) -> int:
    return 0 if isinstance(shape, (Polygon, Rectangle)) else 1 if isinstance(shape, Path) else 2


def _shape_order(element):
    value = element.get_gds_property(SHAPE_ORDER_PROPATTR)
    try:
        return int(str(value).rstrip("\x00"))
    except (TypeError, ValueError):
        return None


_CAP_TO_GDSTK = {
    PathCap.ROUND: "round",
    PathCap.SQUARE: "extended",
    PathCap.FLUSH: "flush",
}
_GDSTK_TO_CAP = {v: k for k, v in _CAP_TO_GDSTK.items()}


# --- cell date/time stamps (GDSII BGNSTR) --------------------------------
_REC_BGNSTR = 0x05
_REC_STRNAME = 0x06
_REC_ENDLIB = 0x04


def _decode_gds_time(vals) -> Optional[datetime]:
    year, month, day, hour, minute, second = (int(v) for v in vals)
    if year < 1000:  # GDSII years are usually stored as year - 1900
        year += 1900
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def _encode_gds_time(dt: datetime):
    return (dt.year - 1900, dt.month, dt.day, dt.hour, dt.minute, dt.second)


def _scan_structures(data: bytes):
    """Yield (bgnstr_offset, name) for every structure in GDSII bytes."""
    i, n = 0, len(data)
    bgnstr_at = None
    while i + 4 <= n:
        length, rectype, _dt = struct.unpack(">HBB", data[i:i + 4])
        if length < 4:
            break
        if rectype == _REC_BGNSTR and length == 28:
            bgnstr_at = i
        elif rectype == _REC_STRNAME and bgnstr_at is not None:
            name = data[i + 4:i + length].rstrip(b"\x00").decode("ascii", "replace")
            yield bgnstr_at, name
            bgnstr_at = None
        elif rectype == _REC_ENDLIB:
            break
        i += length


def read_cell_stamps(path: str) -> Dict[str, Tuple[Optional[datetime], Optional[datetime]]]:
    """{cell name: (created, modified)} from a GDSII file's BGNSTR records."""
    with open(path, "rb") as f:
        data = f.read()
    stamps = {}
    for at, name in _scan_structures(data):
        vals = struct.unpack(">12h", data[at + 4:at + 28])
        stamps[name] = (_decode_gds_time(vals[:6]), _decode_gds_time(vals[6:]))
    return stamps


def _patch_cell_stamps(path: str, library: Library) -> None:
    with open(path, "r+b") as f:
        data = f.read()
        for at, name in list(_scan_structures(data)):
            cell = library.cells.get(name)
            if cell is None or cell.created is None or cell.modified is None:
                continue
            f.seek(at + 4)
            f.write(struct.pack(">12h", *_encode_gds_time(cell.created),
                                *_encode_gds_time(cell.modified)))


def save_gds(library: Library, path: str) -> None:
    """Write a Library to a GDSII file. Each cell's date/time stamps are
    brought up to date first (Cell.stamp) and written into its BGNSTR."""
    library.check_no_cycles()
    now = datetime.now().replace(microsecond=0)
    for cell in library.cells.values():
        cell.stamp(now)

    glib = gdstk.Library(name=library.name, unit=UNIT, precision=PRECISION)
    gcells = {name: gdstk.Cell(name) for name in library.cells}

    for name, cell in library.cells.items():
        gcell = gcells[name]
        ranks = [_kind_rank(shape) for shape in cell.shapes]
        keep_order = ranks != sorted(ranks)
        for index, shape in enumerate(cell.shapes):
            if isinstance(shape, (Polygon, Rectangle)):
                gelem = gdstk.Polygon(shape.points, layer=shape.layer, datatype=shape.datatype)
                if shape.pin:
                    propattr = RECT_PIN_PROPATTR if isinstance(shape, Rectangle) else POLY_PIN_PROPATTR
                    gelem.set_gds_property(propattr, shape.pin)
            elif isinstance(shape, Path):
                gelem = gdstk.FlexPath(
                    shape.points,
                    width=shape.width,
                    ends=_CAP_TO_GDSTK[shape.cap],
                    layer=shape.layer,
                    datatype=shape.datatype,
                    simple_path=True,
                )
            elif isinstance(shape, Text):
                gelem = gdstk.Label(
                    shape.text,
                    shape.origin,
                    rotation=math.radians(shape.rotation),
                    magnification=shape.height / TEXT_BASE_HEIGHT,
                    layer=shape.layer,
                    texttype=shape.font,
                )
            else:
                raise LayoutError(f"unsupported shape type: {type(shape)!r}")
            if keep_order:
                gelem.set_gds_property(SHAPE_ORDER_PROPATTR, str(index))
            gcell.add(gelem)

        for ref in cell.srefs:
            if ref.cell_name not in gcells:
                raise LayoutError(f"sref in {name!r} references unknown cell {ref.cell_name!r}")
            gref = gdstk.Reference(
                gcells[ref.cell_name],
                origin=ref.origin,
                rotation=math.radians(ref.rotation),
                magnification=ref.magnification,
                x_reflection=ref.x_reflection,
            )
            if ref.label:
                gref.set_gds_property(SREF_LABEL_PROPATTR, ref.label)
                gref.set_gds_property(SREF_LABEL_HEIGHT_PROPATTR, str(ref.label_height))
                lx, ly = ref.label_offset
                if lx or ly:
                    gref.set_gds_property(SREF_LABEL_OFFSET_PROPATTR, f"{int(lx)},{int(ly)}")
            if ref.pin:
                gref.set_gds_property(SREF_PIN_PROPATTR, ref.pin)
            gcell.add(gref)

        for ref in cell.arefs:
            if ref.cell_name not in gcells:
                raise LayoutError(f"aref in {name!r} references unknown cell {ref.cell_name!r}")
            gcell.add(gdstk.Reference(
                gcells[ref.cell_name],
                origin=ref.origin,
                rotation=math.radians(ref.rotation),
                magnification=ref.magnification,
                x_reflection=ref.x_reflection,
                columns=ref.columns,
                rows=ref.rows,
                spacing=(ref.col_spacing, ref.row_spacing),
            ))

    glib.add(*gcells.values())
    glib.write_gds(str(path), timestamp=now)
    _patch_cell_stamps(str(path), library)


def load_gds(path: str, library_name: Optional[str] = None) -> Library:
    """Read a GDSII file into a new Library."""
    # Check existence ourselves rather than letting gdstk fail: gdstk's
    # own error for a missing/unreadable file is a native-code stderr
    # print ("[GDSTK] Unable to open GDSII file for input.") followed by
    # a bare OSError with no path in the message, which is confusing
    # from the CLI (hlayout_app.py mydesign.gds) or the OPEN/ATTACH
    # commands. Fail with a clear, actionable LayoutError instead.
    if not os.path.isfile(path):
        raise LayoutError(f"GDSII file not found: {path}")
    gdslib = gdstk.read_gds(str(path), unit=UNIT)
    library = Library(name=library_name or gdslib.name or "HLIB")

    #: (cell_name, layer, spine) for every path dropped on load because
    #: its spine had fewer than 2 points -- see the paths loop below.
    _skipped_degenerate_paths: list = []

    # Pass 1: create cells (empty) so srefs can resolve by name.
    for gcell in gdslib.cells:
        library.add_cell(Cell(name=gcell.name))

    # Pass 2: populate shapes and references.
    for gcell in gdslib.cells:
        cell = library.get_cell(gcell.name)
        orders = []  # one SHAPE_ORDER_PROPATTR value (or None) per loaded shape

        for poly in gcell.polygons:
            orders.append(_shape_order(poly))
            pts = [(int(round(x)), int(round(y))) for x, y in poly.points]
            if len(pts) == 4:
                pin = (poly.get_gds_property(RECT_PIN_PROPATTR) or "").rstrip("\x00")
                cell.shapes.append(Rectangle(layer=poly.layer, points=pts, datatype=poly.datatype, pin=pin))
            else:
                pin = (poly.get_gds_property(POLY_PIN_PROPATTR) or "").rstrip("\x00")
                cell.shapes.append(Polygon(layer=poly.layer, points=pts, datatype=poly.datatype, pin=pin))

        for fp in gcell.paths:
            spine = [(int(round(x)), int(round(y))) for x, y in fp.spine()]
            widths = list(fp.widths())
            width = int(round(widths[0][0])) if widths else 0
            ends = fp.ends[0] if isinstance(fp.ends, tuple) else fp.ends
            cap = _GDSTK_TO_CAP.get(ends, PathCap.FLUSH)
            layer = fp.layers[0] if fp.layers else 0
            datatype = fp.datatypes[0] if fp.datatypes else 0
            # A path whose spine collapses to a single point (or none) is
            # un-drawable junk -- gdstk drops coincident vertices, so a
            # zero-length path written by an older build reads back this
            # way. Skip it rather than failing the whole file to load.
            if len(spine) < 2:
                _skipped_degenerate_paths.append((gcell.name, layer, tuple(spine)))
                continue
            orders.append(_shape_order(fp))
            cell.shapes.append(Path(layer=layer, points=spine, width=width, cap=cap, datatype=datatype))

        for label in gcell.labels:
            orders.append(_shape_order(label))
            font = label.texttype if 0 <= label.texttype < 4 else 0
            height = max(1, int(round(label.magnification * TEXT_BASE_HEIGHT)))
            cell.shapes.append(Text(
                layer=label.layer,
                origin=label.origin,
                text=label.text,
                height=height,
                font=font,
                rotation=math.degrees(label.rotation),
                datatype=label.texttype,
            ))

        if orders and None not in orders and len(orders) == len(cell.shapes):
            ranked = sorted(zip(orders, range(len(orders))))
            cell.shapes[:] = [cell.shapes[i] for _, i in ranked]

        for ref in gcell.references:
            rep = ref.repetition
            if rep is not None and rep.size and rep.size > 1 and rep.columns and rep.rows:
                # An AREF: gdstk exposes it as a plain Reference plus a
                # Repetition (rectangular columns x rows grid). After a
                # round trip through an actual GDSII file (real GDSII
                # AREF stores the grid as 3 reference points, not a
                # columns/rows/spacing triple), gdstk always reports
                # rep.spacing as None and exposes the already-rotated/
                # mirrored column/row vectors as rep.v1/rep.v2 instead --
                # their magnitudes recover the pre-rotation spacing our
                # own ARef.instance_origins() expects (rotation/mirror
                # are re-applied separately via ref.rotation/
                # ref.x_reflection below, so only the magnitude is
                # needed here, not the vector's direction).
                if rep.spacing is not None:
                    col_spacing, row_spacing = rep.spacing
                else:
                    col_spacing = math.hypot(*rep.v1) if rep.v1 is not None else 0.0
                    row_spacing = math.hypot(*rep.v2) if rep.v2 is not None else 0.0
                if rep.columns <= 1:
                    col_spacing = 0.0
                if rep.rows <= 1:
                    row_spacing = 0.0
                cell.arefs.append(ARef(
                    cell_name=ref.cell_name,
                    origin=ref.origin,
                    columns=rep.columns,
                    rows=rep.rows,
                    col_spacing=col_spacing,
                    row_spacing=row_spacing,
                    rotation=math.degrees(ref.rotation),
                    magnification=ref.magnification,
                    x_reflection=ref.x_reflection,
                ))
            else:
                # gdstk pads GDS property strings to an even byte length
                # with a trailing NUL; strip it back off.
                label = (ref.get_gds_property(SREF_LABEL_PROPATTR) or "").rstrip("\x00")
                label_height_raw = (ref.get_gds_property(SREF_LABEL_HEIGHT_PROPATTR) or "").rstrip("\x00")
                try:
                    label_height = int(label_height_raw) if label_height_raw else 14
                except ValueError:
                    label_height = 14
                offset_raw = (ref.get_gds_property(SREF_LABEL_OFFSET_PROPATTR) or "").rstrip("\x00")
                label_offset = (0, 0)
                if offset_raw and "," in offset_raw:
                    try:
                        lx, ly = offset_raw.split(",", 1)
                        label_offset = (int(lx), int(ly))
                    except ValueError:
                        label_offset = (0, 0)
                pin = (ref.get_gds_property(SREF_PIN_PROPATTR) or "").rstrip("\x00")
                cell.srefs.append(SRef(
                    cell_name=ref.cell_name,
                    origin=ref.origin,
                    rotation=math.degrees(ref.rotation),
                    magnification=ref.magnification,
                    x_reflection=ref.x_reflection,
                    label=label,
                    label_height=label_height,
                    label_offset=label_offset,
                    pin=pin,
                ))

    top = gdslib.top_level()
    if top:
        library.top_cell_name = top[0].name

    try:
        stamps = read_cell_stamps(str(path))
    except (OSError, struct.error):
        stamps = {}
    for name, cell in library.cells.items():
        created, modified = stamps.get(name, (None, None))
        cell.created, cell.modified = created, modified
        cell.mark_clean()

    library.check_no_cycles()

    if _skipped_degenerate_paths:
        detail = ", ".join(f"{c} (layer {ly})" for c, ly, _ in _skipped_degenerate_paths)
        warnings.warn(
            f"{os.path.basename(path)}: skipped {len(_skipped_degenerate_paths)} "
            f"zero-length path(s) that could not be reconstructed: {detail}",
            stacklevel=2,
        )
    return library
