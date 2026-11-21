# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Attached reference libraries: attach / detach / merge GDS, build via /
pad / chip cells into a library, export cells, list and find cells.

Part of hlayout.scripting.api.Session (a mixin): these methods are
Session's own -- call them on a Session as always."""

from typing import Optional, Union

from ..core.errors import LayoutError, LibraryError
from ..core.library import Library, merge_libraries
from ..io.gdsio import load_gds, save_gds


def _cell_stamps(cell):
    """{'created', 'modified'} ISO strings (or None) for a Cell."""
    return {"created": cell.created.isoformat(sep="T") if cell.created else None,
            "modified": cell.modified.isoformat(sep="T") if cell.modified else None}


class LibrariesMixin:
    # --- attached libraries ----------------------------------------------
    def attach_library(self, path: str) -> Library:
        """Load a GDSII file as an attached reference library, alongside
        (not merged into) the current design. Up to MAX_ATTACHED_LIBRARIES
        (2) may be attached at once; detach one with detach_library()
        to make room for another."""
        if len(self.attached_libraries) >= self.MAX_ATTACHED_LIBRARIES:
            raise LibraryError(
                f"at most {self.MAX_ATTACHED_LIBRARIES} attached libraries are supported; "
                "call detach_library() first"
            )
        lib = load_gds(path)
        self.attached_libraries.append(lib)
        self._attached_paths.append(path)
        return lib

    def detach_library(self, index: int) -> None:
        if index < 0 or index >= len(self.attached_libraries):
            raise LibraryError(f"no attached library at index {index}")
        del self.attached_libraries[index]
        del self._attached_paths[index]

    def list_attached_libraries(self):
        """[{'index', 'name', 'path', 'cells'}, ...] for each attached library."""
        return [
            {"index": i, "name": lib.name, "path": path, "cells": len(lib.cells)}
            for i, (lib, path) in enumerate(zip(self.attached_libraries, self._attached_paths))
        ]

    def merge_gds(self, path_a: str, path_b: str, out_path: str,
                  library_name: Optional[str] = None) -> dict:
        """Merge two GDSII files into a third, combining every cell from
        both. This is a plain file-to-file operation -- it reads
        path_a and path_b fresh and writes the merged result straight
        to out_path, without touching the session's current design
        (self.library) or its attached libraries at all.

        library_name defaults to path_a's own library name. Cells keep
        their original names unless the same name exists in both
        files, in which case path_b's copy is renamed to avoid the
        collision -- see hlayout.core.library.merge_libraries for the
        exact rule.

        Returns {'cells': <total cell count written to out_path>,
                 'renamed': {<original name from path_b>: <new name>, ...}}
        -- 'renamed' lists every cell from path_b that had to be
        renamed to avoid clashing with a same-named cell from path_a
        (empty if there were no collisions).
        """
        lib_a = load_gds(path_a)
        lib_b = load_gds(path_b)
        merged, renamed = merge_libraries(lib_a, lib_b, name=library_name)
        merged.check_bounds()
        save_gds(merged, out_path)
        return {"cells": len(merged.cells), "renamed": renamed}

    def create_via_cell(self, layer1, layer2, shape: str = "square",
                         size: float = 100, index: Optional[int] = None,
                         overwrite: bool = False, name: Optional[str] = None,
                         dielectric_layer=None, dielectric_window: Optional[float] = None):
        """Create a via cell -- a same-size square or round pad drawn on
        both layer1 and layer2, representing the contact area where a
        via connects them -- directly inside an attached reference
        library, and save that library back to its own file on disk in
        place. This is how a via (or any other new part) gets added to
        a shared parts library, as opposed to session.sref()/place_cell()
        which only ever place instances of cells that already exist.

        layer1/layer2 accept a layer number or an existing layer name,
        resolved against the *current* library's layer table (an
        attached library's own cells are addressed by plain layer
        number in the file, same as any other GDSII data). shape is
        "square" or "round". size is the pad's full width/diameter, in
        DBU (1 DBU = 1 micron).

        `dielectric_layer`/`dielectric_window`, if both given, also draw
        a same-shape opening of size `dielectric_window` DBU on
        `dielectric_layer`, centred at the cell's origin same as the two
        pads -- the window cut in the dielectric between the two
        conductor layers that actually defines the electrical contact
        area in a real process (must differ from both layer1 and
        layer2). Leave both unset (the default) for a bare two-pad via
        with no dielectric window. They must be given together --
        passing only one raises.

        index picks which attached library (0 or 1, see
        list_attached_libraries()); if exactly one is attached it's
        used automatically. The new cell is named `name` if given, else
        f"VIA_Layer{n1}_to_Layer{n2}" -- raises if that name already
        exists in the target library, unless overwrite=True (which
        replaces it).

        Returns (cell_name, path) for the via cell just written. The
        attached library object in memory is updated in place too, so
        Create > Cell Instance / Find & Place Cell... see the new via
        immediately without needing to re-attach.
        """
        if not self.attached_libraries:
            raise LibraryError(
                "no attached library -- attach one first (Setup > Attach Library...)"
            )
        if index is None:
            if len(self.attached_libraries) > 1:
                raise LibraryError(
                    "more than one attached library; pass index=0 or index=1 to pick one"
                )
            index = 0
        if index < 0 or index >= len(self.attached_libraries):
            raise LibraryError(f"no attached library at index {index}")

        shape = shape.lower()
        if shape not in ("square", "round"):
            raise LibraryError("shape must be 'square' or 'round'")
        if size <= 0:
            raise LibraryError("size must be a positive number of DBU")

        n1 = self.library.layers.resolve(layer1)
        n2 = self.library.layers.resolve(layer2)

        n3 = None
        if (dielectric_layer is None) != (dielectric_window is None):
            raise LibraryError(
                "dielectric_layer and dielectric_window must be given together"
            )
        if dielectric_layer is not None:
            n3 = self.library.layers.resolve(dielectric_layer)
            if n3 in (n1, n2):
                raise LibraryError(
                    "dielectric_layer must be different from layer1 and layer2"
                )
            if dielectric_window <= 0:
                raise LibraryError("dielectric_window must be a positive number of DBU")

        name = name or f"VIA_Layer{n1}_to_Layer{n2}"

        lib = self.attached_libraries[index]
        if name in lib.cells:
            if not overwrite:
                raise LibraryError(
                    f"{name!r} already exists in the attached library; pass overwrite=True to replace it"
                )
            lib.delete_cell(name)

        cell = lib.new_cell(name)
        half = size / 2.0
        for n in (n1, n2):
            if shape == "square":
                cell.add_rectangle(n, -half, -half, half, half)
            else:
                cell.add_circle(n, (0, 0), half, segments=32)
        if n3 is not None:
            dhalf = dielectric_window / 2.0
            if shape == "square":
                cell.add_rectangle(n3, -dhalf, -dhalf, dhalf, dhalf)
            else:
                cell.add_circle(n3, (0, 0), dhalf, segments=32)

        path = self._attached_paths[index]
        save_gds(lib, path)
        self._notify()
        return name, path

    def create_pad_cell(self, name: str, conductor_layer, paste_layer,
                         length: float, width: float, margin: float, *,
                         rows: int = 1, cols: int = 1,
                         pitch_x: Optional[float] = None, pitch_y: Optional[float] = None,
                         index: Optional[int] = None, overwrite: bool = False):
        """Create a pad cell -- a rectangular conductor pad with a
        smaller solder paste rectangle centred on it (the paste layer
        undersized from the conductor by `margin` DBU on every side) --
        directly inside an attached reference library, and save that
        library back to its own file on disk in place. Same idea as
        create_via_cell(), but for a single- or multi-pad footprint
        (e.g. a connector) sized by length/width rather than a fixed
        square/round via.

        conductor_layer/paste_layer accept a layer number or an
        existing layer name, resolved against the *current* library's
        layer table. length is the pad's size along X, width along Y,
        both in DBU (1 DBU = 1 micron); margin is how much smaller the
        paste rectangle is than the conductor rectangle on each side,
        so margin must be less than half of both length and width.

        rows x cols lays out a grid of identical pads (rows along Y,
        cols along X), spaced pitch_y/pitch_x apart (pad-centre to
        pad-centre, in DBU) and centred as a whole on the cell origin
        -- e.g. for a 2-pin connector footprint. pitch_x is required
        when cols > 1, pitch_y when rows > 1; both are ignored for a
        single pad (rows == cols == 1).

        index picks which attached library (0 or 1, see
        list_attached_libraries()); if exactly one is attached it's
        used automatically. Raises if `name` already exists in the
        target library, unless overwrite=True (which replaces it).

        A genuine 2-pad footprint (rows*cols == 2, e.g. a simple
        connector or discrete-part land pattern) is unambiguous, so its
        two conductor pads are auto-numbered pin "1"/"2" (lower X, then
        lower Y, is pin "1") -- see Rectangle.pin. Any other grid size
        is left unnumbered (pin=""); number those by hand via Edit >
        Number Pins... or Session.set_pin(), since which physical pad is
        pin 1 on a 3+ pin footprint depends on the part's actual pinout,
        not just its geometry.

        Returns (cell_name, path, pad_count) for the pad cell just
        written. The attached library object in memory is updated in
        place too, so Create > Cell Instance / Find & Place Cell... see
        the new pad immediately without needing to re-attach.
        """
        if not self.attached_libraries:
            raise LibraryError(
                "no attached library -- attach one first (Setup > Attach Library...)"
            )
        if index is None:
            if len(self.attached_libraries) > 1:
                raise LibraryError(
                    "more than one attached library; pass index=0 or index=1 to pick one"
                )
            index = 0
        if index < 0 or index >= len(self.attached_libraries):
            raise LibraryError(f"no attached library at index {index}")

        name = (name or "").strip()
        if not name:
            raise LibraryError("a cell name is required")
        if length <= 0 or width <= 0:
            raise LibraryError("length and width must be positive numbers")
        if margin < 0:
            raise LibraryError("margin must not be negative")
        if margin * 2 >= length or margin * 2 >= width:
            raise LibraryError(
                "margin is too large -- the solder paste rectangle would have "
                "zero or negative size; margin must be less than half of both "
                "length and width"
            )
        rows = int(rows)
        cols = int(cols)
        if rows < 1 or cols < 1:
            raise LibraryError("rows and cols must be at least 1")
        if cols > 1:
            if not pitch_x or pitch_x <= 0:
                raise LibraryError("pitch_x must be a positive number when cols > 1")
        if rows > 1:
            if not pitch_y or pitch_y <= 0:
                raise LibraryError("pitch_y must be a positive number when rows > 1")

        n_cond = self.library.layers.resolve(conductor_layer)
        n_paste = self.library.layers.resolve(paste_layer)

        lib = self.attached_libraries[index]
        if name in lib.cells:
            if not overwrite:
                raise LibraryError(
                    f"{name!r} already exists in the attached library; pass overwrite=True to replace it"
                )
            lib.delete_cell(name)

        cell = lib.new_cell(name)
        half_l, half_w = length / 2.0, width / 2.0
        paste_half_l, paste_half_w = half_l - margin, half_w - margin

        centers = []
        for r in range(rows):
            cy = (r - (rows - 1) / 2.0) * (pitch_y or 0)
            for c in range(cols):
                cx = (c - (cols - 1) / 2.0) * (pitch_x or 0)
                centers.append((cx, cy))

        # A genuine 2-pad footprint is unambiguous -- auto-number it.
        # Any other pad count is left unpinned; see the docstring.
        pins_by_index = {}
        if len(centers) == 2:
            order = sorted(range(2), key=lambda i: centers[i])
            pins_by_index[order[0]] = "1"
            pins_by_index[order[1]] = "2"

        pad_count = 0
        for i, (cx, cy) in enumerate(centers):
            cell.add_rectangle(n_cond, cx - half_l, cy - half_w, cx + half_l, cy + half_w,
                                pin=pins_by_index.get(i, ""))
            cell.add_rectangle(n_paste, cx - paste_half_l, cy - paste_half_w,
                                cx + paste_half_l, cy + paste_half_w)
            pad_count += 1

        path = self._attached_paths[index]
        save_gds(lib, path)
        self._notify()
        return name, path, pad_count

    #: Names of the two die-pin device cells the Chip Wizard places --
    #: pin 1 uses CHIP_PIN1_CELL, every other pin uses CHIP_PIN_CELL.
    #: These are the user's own cells, taken from the attached parts
    #: library (or the current design); the wizard does not build them.
    CHIP_PIN_CELL = "chip_pin"

    CHIP_PIN1_CELL = "chip_pin1"

    #: The chip-graphics (body-outline) layer is forced to this colour
    #: by create_chip_cell() so the outline reads clearly over the
    #: copper underneath it.
    CHIP_OUTLINE_COLOR = "#ffffff"

    def create_chip_cell(self, name: str, length: float, width: float,
                         graphics_layer, epoxy_length: float, epoxy_width: float,
                         epoxy_layer, *, outline_width: Optional[float] = None,
                         overwrite: bool = False, attach_pad_layer=None,
                         attach_pad_margin: float = 200.0, attach_pad_pin: str = "",
                         attach_tab: float = 0.0, attach_tab_side: str = "bottom"):
        """Start a bare-die / chip footprint cell in the CURRENT design:

          * a body outline -- a closed rectangular Path of ``length`` x
            ``width`` DBU, centred on the origin, on ``graphics_layer``,
            which is forced white and visible;
          * an ``epoxy_length`` x ``epoxy_width`` DBU epoxy die-attach
            pad, centred on the origin, on ``epoxy_layer`` (forced
            visible) -- rectangular, since chips are.

        ``attach_pad_layer`` (optional, a number or layer name) adds a
        conductor die-attach pad under the chip on that layer -- e.g. the
        2nd conductor -- ``attach_pad_margin`` DBU (default 200) larger
        than the body on every side. The autorouter keeps tracks clear of
        it (and of the die) by the die keep-out.

        ``attach_pad_pin`` makes that pad a pin of the part -- the back
        contact of a die whose back is an electrode, e.g. the collector
        "C" of a bipolar transistor or the drain of a MOSFET, connected
        through conductive epoxy. A pinned pad is no longer part of the
        die's routing keep-out, but the keep-out round the die body still
        covers most of it, so ``attach_tab`` (DBU) extends the pad on
        ``attach_tab_side`` ("bottom", "top", "left" or "right") into a
        tab the router can reach: make it longer than die_keepout plus
        the track clearance (default 0, no tab). Requires
        ``attach_pad_layer``.

        The die *pins* are added afterwards as instances of the
        ``chip_pin`` / ``chip_pin1`` cells you provide (e.g. in the
        attached parts library); export_cell_to_library() then carries
        the chip and those pin cells into the library together.

        ``graphics_layer`` / ``epoxy_layer`` accept a number or an
        existing layer name. ``outline_width`` (DBU) defaults to 2% of
        the shorter body edge, min 1. Raises LayoutError on a
        non-positive size, or if ``name`` is already a cell here and
        ``overwrite`` is false. One undo step; the new cell becomes the
        current cell."""
        name = (name or "").strip()
        if not name:
            raise LayoutError("a cell name is required")
        if length <= 0 or width <= 0:
            raise LayoutError("chip length and width must be positive")
        if epoxy_length <= 0 or epoxy_width <= 0:
            raise LayoutError("epoxy pad length and width must be positive")
        g_layer = self.library.layers.resolve(graphics_layer)
        e_layer = self.library.layers.resolve(epoxy_layer)
        if name in self.library.cells:
            if not overwrite:
                raise LayoutError(
                    f"{name!r} already exists in this design; pass overwrite=True to replace it"
                )
            self.library.delete_cell(name)

        hl, hw = length / 2.0, width / 2.0
        ow = int(outline_width) if outline_width else max(1, int(round(min(length, width) * 0.02)))
        el, ew = epoxy_length / 2.0, epoxy_width / 2.0
        a_layer = self.library.layers.resolve(attach_pad_layer) \
            if attach_pad_layer is not None else None
        if attach_pad_layer is not None and (a_layer is None or a_layer < 0):
            raise LayoutError(f"no layer {attach_pad_layer!r} for the die-attach pad")
        am = float(attach_pad_margin or 0)
        a_pin = str(attach_pad_pin or "").strip()
        tab = float(attach_tab or 0)
        side = (attach_tab_side or "bottom").strip().lower()
        if a_pin and a_layer is None:
            raise LayoutError("attach_pad_pin needs attach_pad_layer (a conductor die-attach pad)")
        if side not in ("bottom", "top", "left", "right"):
            raise LayoutError(f"attach_tab_side must be bottom, top, left or right, not {attach_tab_side!r}")
        if tab < 0:
            raise LayoutError("attach_tab must not be negative")

        with self._batch():
            cell = self.library.new_cell(name)
            self.current_cell_name = name
            if a_layer is not None:
                # conductor die-attach pad, `am` larger than the chip all round,
                # optionally extended into a tab on one side and made a pin
                x1, y1, x2, y2 = -hl - am, -hw - am, hl + am, hw + am
                if tab:
                    if side == "bottom":
                        y1 -= tab
                    elif side == "top":
                        y2 += tab
                    elif side == "left":
                        x1 -= tab
                    else:
                        x2 += tab
                cell.add_rectangle(a_layer, int(round(x1)), int(round(y1)),
                                   int(round(x2)), int(round(y2)), pin=a_pin)
            cell.add_rectangle(e_layer, -el, -ew, el, ew)   # epoxy die-attach pad
            cell.add_path(
                g_layer,
                [(-hl, -hw), (hl, -hw), (hl, hw), (-hl, hw), (-hl, -hw)],
                max(1, ow), cap="flush",
            )
            try:
                self.library.layers.set_style(
                    g_layer, color=self.CHIP_OUTLINE_COLOR, visible=True)
            except (KeyError, IndexError):
                pass
            try:
                self.library.layers.set_style(e_layer, visible=True)
            except (KeyError, IndexError):
                pass
        self._notify()
        return name

    def show_layers_of_cell(self, cell_name: str) -> None:
        """Make every layer that ``cell_name`` (in the current design)
        draws on visible -- used by the Chip Wizard so a freshly placed
        ``chip_pin`` / ``chip_pin1`` instance is actually shown even if
        its layers were switched off."""
        c = self.library.cells.get(cell_name)
        if c is None:
            return
        for sh in c.shapes:
            try:
                self.library.layers.set_style(sh.layer, visible=True)
            except (KeyError, IndexError):
                pass
        self._notify()

    def export_cell_to_library(self, cell_name: str, index: int = 0,
                               overwrite: bool = False, reuse_shared: bool = True):
        """Copy ``cell_name`` -- and its Sref dependency closure -- from
        the current design into attached reference library ``index``
        (default 0, the first attached), then save that library's .gds
        file in place. The finished part is then available to every
        design that attaches this library. Returns
        ``(name_in_library, path)``.

        With ``reuse_shared`` (the default), any sub-cell of the closure
        whose name already exists in the target library is referenced in
        place instead of copied in under a fresh suffixed name -- so
        re-exporting a part that shares a primitive (the Chip Wizard's
        ``chip_pin``/``chip_pin1``, a common pad cell, ...) doesn't pile
        up ``name_1``, ``name_2`` duplicates. It assumes a same-named
        sub-cell already there is the same primitive; pass
        ``reuse_shared=False`` to force fresh copies of everything.

        Raises LayoutError if no library is attached at ``index``, the
        cell isn't in the current design, or a cell of that name already
        lives in the target library and ``overwrite`` is false."""
        if not self.attached_libraries:
            raise LayoutError(
                "no attached library -- attach one first (Setup > Attach Library...)"
            )
        if index < 0 or index >= len(self.attached_libraries):
            raise LayoutError(f"no attached library at index {index}")
        if cell_name not in self.library.cells:
            raise LayoutError(f"no cell named {cell_name!r} in the current design")

        lib = self.attached_libraries[index]
        if cell_name in lib.cells:
            if not overwrite:
                raise LayoutError(
                    f"{cell_name!r} already exists in the attached library; "
                    "pass overwrite=True to replace it"
                )
            lib.delete_cell(cell_name)

        reuse = None
        if reuse_shared:
            reuse = [n for n in self._cell_closure_names(cell_name) if n != cell_name]
        local_name = lib.import_cell_closure(self.library, cell_name, reuse_names=reuse)
        path = self._attached_paths[index]
        save_gds(lib, path)
        self._notify()
        return local_name, path

    def _cell_closure_names(self, root: str):
        """Every cell name reachable from ``root`` in the current design
        via Srefs/Arefs, ``root`` included."""
        seen = set()
        stack = [root]
        while stack:
            name = stack.pop()
            if name in seen or name not in self.library.cells:
                continue
            seen.add(name)
            c = self.library.cells[name]
            for ref in list(c.srefs) + list(c.arefs):
                stack.append(ref.cell_name)
        return seen

    def _resolve_source(self, source: Union[str, int, None]) -> Library:
        if source in ("current", None):
            return self.library
        idx = int(source)
        if idx < 0 or idx >= len(self.attached_libraries):
            raise LibraryError(f"no attached library at index {idx}")
        return self.attached_libraries[idx]

    def list_library(self, source: Union[str, int, None] = "current"):
        """List the cells of `source` ('current', or an attached library's
        0-based index): [{'name', 'shapes', 'srefs', 'bbox', 'created',
        'modified'}, ...]. 'created'/'modified' are the cell's date/time
        stamps as ISO strings ('YYYY-MM-DDTHH:MM:SS'), or None for a
        cell not saved yet."""
        lib = self._resolve_source(source)
        return [
            {"name": name, "shapes": len(cell.shapes), "srefs": len(cell.srefs),
             "arefs": len(cell.arefs), "bbox": cell.bbox(), **_cell_stamps(cell)}
            for name, cell in lib.cells.items()
        ]

    def cell_stamps(self, source: Union[str, int, None] = None):
        """Date/time stamps of every cell (Sref target) in the current
        design and every attached library: [{'source', 'name',
        'created', 'modified'}, ...], source 'current' or an attached
        library's index. Stamps live in each cell's GDSII BGNSTR record;
        'modified' moves when a changed cell is saved. Pass `source` to
        list one library only."""
        sources = ([("current", self.library)] + list(enumerate(self.attached_libraries))
                   if source is None else [(source, self._resolve_source(source))])
        return [{"source": src, "name": name, **_cell_stamps(cell)}
                for src, lib in sources for name, cell in lib.cells.items()]

    def find_cells(self, query: str):
        """Case-insensitive substring search for cell (Sref target) names
        across the current library and every attached library. Returns
        [{'source', 'name'}, ...] where source is 'current' or an
        attached library's index."""
        query_l = query.lower()
        matches = [{"source": "current", "name": n, **_cell_stamps(c)}
                   for n, c in self.library.cells.items() if query_l in n.lower()]
        for i, lib in enumerate(self.attached_libraries):
            matches.extend({"source": i, "name": n, **_cell_stamps(c)}
                           for n, c in lib.cells.items() if query_l in n.lower())
        return matches
