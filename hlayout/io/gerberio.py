# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Gerber RS-274X export -- one file per visible layer.

GDSII (hlayout/io/gdsio.py) is the tool's native on-disk database
format; Gerber is a manufacturing *output* format for photoplotters/PCB
and thick-film fabrication, so this writes flattened, per-layer
files rather than round-tripping the hierarchical design. Every Sref is
flattened (its shapes copied out with the placement's rotation/
magnification/mirror/offset applied), so a Gerber file never contains
an AB block or nested reference of its own -- exactly what fab tools
expect to receive.

By default (no explicit ``layers`` argument), only **visible** layers
(Setup > Layers..., ``LAYER <n> visible=0|1``) that actually have
shapes in them (after flattening) get a file -- an empty or hidden
layer produces nothing, matching "export what's on screen" rather than
every layer number that merely exists in the 64-entry layer table.
Naming a layer explicitly via ``layers=`` (e.g. a Gerber Export Sets
job's hand-picked list) overrides that visibility gate for the layers
named -- a deliberate, by-number request shouldn't be silently
dropped by an unrelated display toggle.

Format conventions:
  * Coordinates: absolute, in millimetres, format ``%FSLAX46Y46*%``
    (4 integer + 6 fractional digits, leading zeros omitted) -- plenty
    of headroom under the 500 x 500 mm database limit, and exact for
    hlayout's 1-DBU-per-micron grid (no rounding beyond float error
    from rotation).
  * Polygon / Rectangle: drawn as a filled region (``G36``/``G37``)
    outlining the shape's own points, closed back to the start point.
  * Path: drawn as a stroke with a circular aperture sized to the
    path's width -- the closest RS-274X equivalent to a path's round
    cap; square/flush caps are not distinguished (Gerber's standard
    aperture set has no projecting/flush line cap concept), which is
    noted here since it's a real, if usually cosmetic, fidelity gap at
    the very ends of a trace.
  * Text: **not exported** -- Gerber has no native text primitive, and
    approximating letterforms as flashed geometry is out of scope; text
    layers meant for fabrication should be redrawn as a path/polygon
    layer instead. Callers can detect this via the ``skipped_text``
    count `write_gerber_layers` returns.
"""

import math
import ntpath
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..core.errors import GeometryError
from ..core.library import Library
from ..core.shapes import Path, Polygon, Rectangle, SRef, Text, expand_arefs_to_srefs
from ..core.units import DBU_MM

# Gerber coordinate unit: 1e-6 mm (i.e. the FS format's 6 fractional
# digits), so a DBU (1 micron = 1e-3 mm) is exactly 1000 of these --
# integer, no rounding.
_GERBER_UNITS_PER_DBU = 1000

_MAX_DEPTH = 64


def _flatten_layer_data(library: Library, cell_name: str,
                         layers: Optional[List[int]] = None):
    """Walk the Sref hierarchy starting at ``cell_name``, applying each
    instance's transform, and bucket the resulting *world-space*
    geometry by layer number. Returns (polys, paths, skipped_text):
      polys: {layer_number: [[(x, y), ...], ...]}   (DBU, float)
      paths: {layer_number: [([(x, y), ...], width_dbu), ...]}
      skipped_text: count of Text shapes on an exported layer, not
        exported (Gerber has no text primitive -- see module docstring)

    An invisible layer is skipped, same as the on-screen view -- UNLESS
    it's named in ``layers``: an explicit, by-number request (a Gerber
    Export Sets job's hand-picked layer list) is a deliberate ask for
    that data and shouldn't be silently vetoed by an unrelated Setup >
    Layers display toggle the way the *implicit* "export everything
    visible" default should be."""
    polys: Dict[int, List[List[Tuple[float, float]]]] = {}
    paths: Dict[int, List[Tuple[List[Tuple[float, float]], float]]] = {}
    skipped_text = 0
    explicit = set(layers) if layers is not None else None

    def walk(name, transform, scale, depth):
        nonlocal skipped_text
        if depth > _MAX_DEPTH or name not in library.cells:
            return
        cell = library.cells[name]
        for shape in cell.shapes:
            layer = library.layers[shape.layer]
            if not (layer.visible or (explicit is not None and shape.layer in explicit)):
                continue
            if isinstance(shape, (Rectangle, Polygon)):
                pts = [transform(p) for p in shape.points]
                polys.setdefault(shape.layer, []).append(pts)
            elif isinstance(shape, Path):
                pts = [transform(p) for p in shape.points]
                paths.setdefault(shape.layer, []).append((pts, shape.width * scale))
            elif isinstance(shape, Text):
                skipped_text += 1
        for ref in cell.srefs + expand_arefs_to_srefs(cell.arefs):
            theta = math.radians(ref.rotation)
            cos_t, sin_t = math.cos(theta), math.sin(theta)
            mag = ref.magnification
            ox, oy = ref.origin
            xr = ref.x_reflection
            parent = transform

            def child_transform(pt, cos_t=cos_t, sin_t=sin_t, mag=mag, ox=ox, oy=oy,
                                 xr=xr, parent=parent):
                x, y = pt
                if xr:
                    y = -y
                x *= mag
                y *= mag
                rx = x * cos_t - y * sin_t
                ry = x * sin_t + y * cos_t
                return parent((rx + ox, ry + oy))

            walk(ref.cell_name, child_transform, scale * mag, depth + 1)

    walk(cell_name, lambda p: p, 1.0, 0)
    return polys, paths, skipped_text


def _fmt_coord(v_dbu: float) -> str:
    return str(int(round(v_dbu * _GERBER_UNITS_PER_DBU)))


def _safe_layer_slug(layer) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", layer.name).strip("_")
    return slug or f"layer{layer.number}"


def _write_layer_file(path, layer, polys, paths, scale: float = 1.0):
    lines = [
        f"G04 HLayout Gerber RS-274X export -- layer {layer.number} ({layer.name}) *",
        f"G04 Artwork scale {scale:g}X (1X = actual size) *",
        "%FSLAX46Y46*%",
        "%MOMM*%",
        # A minimal aperture is required to be "selected" (Dnn) before
        # region (G36/G37) fill commands even though the aperture's own
        # shape/size has no effect inside a region -- see the Gerber
        # Format Specification. D10 is used as that placeholder here.
        "%ADD10C,0.0100*%",
    ]

    widths_mm = sorted({round(w * DBU_MM, 4) for _, w in paths})
    aperture_id = {}
    next_id = 11
    for w in widths_mm:
        aperture_id[w] = next_id
        diam = max(w, 0.0254)  # floor at ~1 mil so a zero/near-zero width still plots
        lines.append(f"%ADD{next_id}C,{diam:.4f}*%")
        next_id += 1

    lines.append("%LPD*%")
    lines.append("G01*")

    if polys:
        lines.append("D10*")
        for pts in polys:
            if len(pts) < 3:
                continue
            x0, y0 = pts[0]
            lines.append("G36*")
            lines.append(f"X{_fmt_coord(x0)}Y{_fmt_coord(y0)}D02*")
            for x, y in pts[1:]:
                lines.append(f"X{_fmt_coord(x)}Y{_fmt_coord(y)}D01*")
            lines.append(f"X{_fmt_coord(x0)}Y{_fmt_coord(y0)}D01*")  # close the loop
            lines.append("G37*")

    for w in widths_mm:
        lines.append(f"D{aperture_id[w]}*")
        for pts, width_dbu in paths:
            if round(width_dbu * DBU_MM, 4) != w or len(pts) < 2:
                continue
            x0, y0 = pts[0]
            lines.append(f"X{_fmt_coord(x0)}Y{_fmt_coord(y0)}D02*")
            for x, y in pts[1:]:
                lines.append(f"X{_fmt_coord(x)}Y{_fmt_coord(y)}D01*")

    lines.append("M02*")

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


# The largest coordinate the %FSLAX46Y46*% format can hold: 4 integer
# digits of millimetres.
_GERBER_MAX_MM = 9999.0


def check_gerber_scale(scale) -> float:
    """Validate an artwork scale factor (1 = actual size; hybrid screen
    artwork is commonly plotted at 5X or 10X) and return it as a float."""
    try:
        value = float(scale)
    except (TypeError, ValueError):
        raise GeometryError(f"Gerber scale must be a number, not {scale!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise GeometryError(f"Gerber scale must be greater than zero, not {scale!r}")
    return value


def write_gerber_layers(library: Library, cell_name: str, out_prefix: str,
                         layers: Optional[List[int]] = None, scale: float = 1.0):
    """Write one Gerber RS-274X file per layer that has data in
    ``cell_name`` (after flattening every Sref), named
    ``<out_prefix>_L<NN>_<layer name>.gbr``.

    ``layers``, if given, restricts export to exactly that subset of
    layer numbers -- including one that's currently hidden (Setup >
    Layers... / ``LAYER <n> visible=0``): naming a layer explicitly
    (e.g. a Gerber Export Sets job's hand-picked layer list) is a
    deliberate request for that data, so it isn't silently vetoed by
    an unrelated display toggle the way the implicit default is. The
    default (``layers=None``) exports every *visible* layer with data
    -- "export what's on screen".

    ``scale`` enlarges the whole artwork about the design origin (0,0):
    every coordinate AND every path width is multiplied by it, so 5 or
    10 gives the 5X / 10X artwork hybrid screens are normally plotted
    at. The design itself is not changed. 1 (the default) is actual size.

    Returns (written, skipped_text):
      written: [(layer_number, file_path), ...], in ascending layer order
      skipped_text: count of Text shapes that were not exported (see
        module docstring -- Gerber has no text primitive)
    """
    if cell_name not in library.cells:
        raise ValueError(f"no such cell {cell_name!r}")

    out_dir = os.path.dirname(out_prefix)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    scale = check_gerber_scale(scale)
    polys, paths, skipped_text = _flatten_layer_data(library, cell_name, layers=layers)
    if scale != 1.0:
        polys = {n: [[(x * scale, y * scale) for x, y in pts] for pts in items]
                 for n, items in polys.items()}
        paths = {n: [([(x * scale, y * scale) for x, y in pts], width * scale)
                     for pts, width in items]
                 for n, items in paths.items()}
    extent = max(
        [abs(c) for items in polys.values() for pts in items for p in pts for c in p]
        + [abs(c) for items in paths.values() for pts, _w in items for p in pts for c in p]
        + [0.0]) * DBU_MM
    if extent > _GERBER_MAX_MM:
        raise GeometryError(
            f"Gerber artwork at {scale:g}X reaches {extent:.0f} mm from the origin, "
            f"beyond the format's {_GERBER_MAX_MM:.0f} mm limit -- use a smaller scale")
    indices = sorted(set(polys.keys()) | set(paths.keys()))

    written = []
    for n in indices:
        if layers is not None and n not in layers:
            continue
        layer = library.layers[n]
        p_list = polys.get(n, [])
        w_list = paths.get(n, [])
        if not p_list and not w_list:
            continue
        fname = f"{out_prefix}_L{n:02d}_{_safe_layer_slug(layer)}.gbr"
        _write_layer_file(fname, layer, p_list, w_list, scale=scale)
        written.append((n, fname))

    return written, skipped_text


DEFAULT_GERBER_SUBFOLDER = "Gerber"


def default_gerber_output_dir(design_path: Optional[str] = None) -> str:
    """The default Gerber output folder: ``<current dir>/Gerber``, where
    the current dir is the folder the open design file lives in, or the
    process working directory for a design that has never been saved.
    An existing folder of the same name in different case (e.g. an older
    ``gerber``) is reused rather than creating a second one beside it.
    Nothing is created here -- the folder is made when files are written."""
    base = os.path.dirname(os.path.abspath(design_path)) if design_path else os.getcwd()
    try:
        for entry in os.listdir(base):
            if entry.lower() == DEFAULT_GERBER_SUBFOLDER.lower() and \
                    os.path.isdir(os.path.join(base, entry)):
                return os.path.join(base, entry)
    except OSError:
        pass
    return os.path.join(base, DEFAULT_GERBER_SUBFOLDER)


def _is_absolute(path: str) -> bool:
    # A Windows path (drive letter or UNC) counts as absolute on any OS,
    # so a job saved on one machine means the same thing on another.
    return os.path.isabs(path) or ntpath.isabs(path) or bool(ntpath.splitdrive(path)[0])


def resolve_out_prefix(out_prefix: str, out_dir: Optional[str] = None) -> str:
    """Where a job's files really go. A RELATIVE ``out_prefix`` (a plain
    name like ``board``, or ``fabA/board``) is placed inside ``out_dir``;
    an ABSOLUTE one is an explicit override and is used unchanged. With
    no ``out_dir`` the prefix is returned as given."""
    if not out_dir or _is_absolute(out_prefix):
        return out_prefix
    return os.path.join(out_dir, out_prefix)


@dataclass
class GerberExportJob:
    """One job in a Gerber export batch/set -- a saved, named
    write_gerber_layers() call. See hlayout.scripting.api.Session.
    run_gerber_export_batch(), which runs a list of these together in
    one action instead of using File > Export Gerber... once per job by
    hand (e.g. one job per fab house, each with its own layer subset
    and output prefix).

    Unlike hlayout.core.maskops.MaskOpStep, job order doesn't matter --
    each job's output is entirely independent of every other job's (a
    Gerber export never modifies the design, only writes files to
    disk) -- so, unlike the mask op batch window, the Gerber batch
    window has no Move Up/Move Down."""

    name: str
    out_prefix: str
    layers: Optional[List[int]] = None  # None = every visible layer with data

    def __post_init__(self):
        if not self.out_prefix:
            raise GeometryError("Gerber export job requires an output prefix")

    def to_dict(self) -> dict:
        return {"name": self.name, "out_prefix": self.out_prefix, "layers": self.layers}

    @classmethod
    def from_dict(cls, data: dict) -> "GerberExportJob":
        return cls(
            name=data.get("name", ""),
            out_prefix=data["out_prefix"],
            layers=data.get("layers"),
        )

    @property
    def label(self) -> str:
        layer_desc = ("all visible layers" if not self.layers
                       else f"layers {', '.join(map(str, self.layers))}")
        base = f"{layer_desc} -> {self.out_prefix}"
        return f"{self.name}: {base}" if self.name else base
