"""LayoutCanvas: a Tkinter Canvas that draws one cell of a Library,
with pan/zoom, a major/minor dot grid, and per-layer colour + stipple.
"""

import copy
import math
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, simpledialog

from ..core.errors import CellError, GeometryError
from ..core.maskops import layer_to_shapely, shapely_to_polygons
from ..core.shapes import (ARef, CIRCLE_SEGMENT_CHOICES, MAX_PATH_POINTS, MAX_POLY_POINTS, Path,
                           PathCap, Polygon, Rectangle, SRef, Text)
from ..core.units import DBU_MM, dbu_to_mm, mm_to_dbu
from ..io.techfile import load_refdes_annotation_layer

# Bitmaps used to approximate the 9 stipple/fill patterns on a Tk canvas.
# Index 0 ("solid") is drawn as a plain filled polygon (no stipple).
_STIPPLE_BITMAPS = {
    1: "gray12",
    2: "gray25",
    3: "gray50",
    4: "gray50",
    5: "gray75",
    6: "gray25",
    7: "gray12",
    8: "gray25",
}


class LayoutCanvas(tk.Canvas):
    """Renders a single Cell from a Library. Coordinates are DBU; the
    canvas keeps its own pixel-per-DBU scale and pan offset."""

    GRID_MINOR_DBU = mm_to_dbu(1.0)   # 1 mm, in DBU (DBU-size independent)
    GRID_MAJOR_EVERY = 10  # every 10 minor lines -> 10 mm

    # Grid dot size, in screen pixels of radius around the exact grid
    # point (a (2r+1) x (2r+1) square blob) -- see _draw_grid_raster().
    # Bumped up from 1px/single-pixel so dots actually read as dots at
    # normal zoom, not a barely-visible speck.
    GRID_MINOR_DOT_R = 1  # -> 3x3 px
    GRID_MAJOR_DOT_R = 2  # -> 5x5 px

    # Default view scale, expressed as pixels-per-mm rather than a raw
    # pixels-per-DBU number, so it stays sensible regardless of how
    # fine DBU itself is.
    PX_PER_MM_DEFAULT = 20.0

    # A selected shape/cell-instance is redrawn in this colour (instead
    # of its layer's own colour, and without stipple) so it's
    # unambiguous which object is about to be moved or rotated -- see
    # move_selected()/rotate_selected() and _draw_shape().
    SELECTED_COLOR = "#ffffff"

    def __init__(self, master, library, on_status=None, **kwargs):
        kwargs.setdefault("bg", "#1e1e1e")
        kwargs.setdefault("cursor", "crosshair")
        super().__init__(master, **kwargs)
        self.library = library
        self.cell_name = library.top_cell_name
        self.on_status = on_status or (lambda msg: None)

        self.scale = self.PX_PER_MM_DEFAULT * DBU_MM  # pixels per DBU
        self.offset_x = 0.0  # pixels
        self.offset_y = 0.0
        self.show_grid = True
        # Draw each numbered pad's pin string (Rectangle/Polygon .pin) at
        # its centre while editing the current cell's own shapes -- a
        # visual aid for the Edit > Number Pins... click-to-increment
        # tool and for checking an imported footprint. View > Show Pin
        # Numbers toggles it; see _draw_cell() and toggle_pin_numbers().
        self.show_pin_numbers = True
        # Current minor-grid spacing, in DBU -- an instance attribute (not
        # just the GRID_MINOR_DBU class default) so it can be changed at
        # runtime via set_grid_spacing() (see Setup > Grid Spacing... and
        # the GRID command). Also doubles as the default snap-to-grid
        # pitch (self.snap_dbu below) -- the drawn grid and the snap grid
        # are the same grid, matching how "grid" is understood in other
        # CAD tools, so changing one keeps the other in step unless the
        # user separately overrides self.snap_dbu.
        self.grid_dbu = self.GRID_MINOR_DBU

        self._pan_start = None
        self._font_cache = {}

        # --- interactive drawing state ---------------------------------
        self.draw_mode = "select"  # select | rect | poly | path | text | sref | aref | circle | zoomwin | route | measure | pin_number | cell_origin | auto_route
        # Create Cell from Selection: after the name is entered the tool
        # enters "cell_origin" mode -- the next canvas click is the point
        # that becomes the new cell's local (0, 0). The selection is
        # stashed here because switching draw mode clears it.
        self._pending_cell_name = None
        self._pending_cell_items = []
        self._pending_cell_replace = False
        self.current_layer = 0
        # --- Pin numbering tool (Edit > Number Pins...) -------------------
        # The pin number the NEXT click will stamp onto a Rectangle/Polygon
        # in the current cell; increments (numerically) after each click.
        # See increment_pin_str() and _pin_number_click().
        self.pin_number_next = "1"
        self.path_width = 2
        self.path_cap = "round"
        self.circle_segments = 32  # one of CIRCLE_SEGMENT_CHOICES
        # --- two-layer Route tool (Create > Route (2-Layer)) -------------
        # Configured via Setup > Route Layers...; route_layer_a is where a
        # route starts, route_layer_b is what Space switches to (and back).
        self.route_layer_a = 0
        self.route_layer_b = 1
        # Which reusable via cell (see Tools > Via Wizard.../scripting's
        # create_via_cell()) Space places at the switch point -- an SRef
        # to this cell, not raw pad/dielectric-window shapes, so the via's
        # actual footprint (size, shape, dielectric window, anything else
        # the cell's own geometry carries) lives in one place: the cell
        # itself. Empty string means "not configured yet" -- see
        # App._on_route_via(), which refuses to drop a via and asks the
        # user to set one via Setup > Route Layers... first.
        self.route_via_cell = ""
        self._route_active_layer = self.route_layer_a
        # Optional callback(x, y, layer_a, layer_b, via_cell, segment=None),
        # fired when Space drops a via mid-route. When given, `segment`
        # is (layer, points, width, cap) for the path segment drawn so
        # far that should be committed *atomically with* the via as one
        # undo step -- wired by the app to wrap Session.path() +
        # Session.place_cell() in one session.batch(). Left as a no-op so
        # a bare LayoutCanvas (e.g. in a test) works without an
        # app/session wiring it up.
        self.on_route_via = lambda x, y, a, b, via_cell, segment=None: None
        # callback(a, b) -- fired by the Auto-Route two-click tool with
        # each endpoint being either a picked pad shape or a snapped
        # (x, y) DBU point. No-op so a bare LayoutCanvas still works.
        self.on_auto_route = lambda a, b: None
        self._auto_route_a = None
        self.snap_enabled = True
        self.snap_dbu = self.grid_dbu  # snap to the current grid spacing by default
        # Orthogonal/45-degree routing constraint -- Path and Route
        # (2-Layer) drawing (Setup > Orthogonal/45 Routing / the "Ortho
        # 45" toolbar checkbox). On by default: every new segment is
        # forced to horizontal, vertical, or a clean 45-degree diagonal
        # from the last point already placed, the way real traces are
        # actually routed (arbitrary angles are unusual on any process,
        # and clean 45s route/mitre corners better than a hard 90).
        # Only constrains NEW points going forward -- doesn't touch
        # points already placed. See _constrain_to_ortho()/
        # _next_draw_point().
        self.ortho_enabled = True
        self.on_commit = None  # callback(mode, layer, points, extra_dict)
        self.on_mode_status = lambda msg: None  # optional extra status callback
        # Optional callback(mode), fired by set_mode() every time
        # draw_mode changes -- including when set_mode() changes it
        # itself rather than being called from outside (e.g.
        # _finish_zoomwin() switching back to "select" once its
        # one-shot Zoom Window drag is done). Wired by the app to keep
        # the View > Zoom Window radiobutton/toolbar button in sync.
        self.on_draw_mode_changed = lambda mode: None
        # Optional callback() -> list[str]: names offered by the "Cell
        # Instance" draw tool's picker (_prompt_sref). Wired by the app to
        # include attached-library cells, not just ones already in the
        # current design -- see App._placeable_cell_names(). Falls back to
        # the current library only when unset (e.g. a bare LayoutCanvas in
        # a test, with no app/session wiring it up).
        self.on_list_cells = None
        self.on_edit = None  # optional callback(), fired after a select-mode move/edit/delete
        # Optional callback(), fired whenever the current selection
        # itself changes (a click, a window-select drag, Delete clearing
        # it, switching cell/mode) -- wired by the app to refresh a
        # live Properties panel. Distinct from on_edit, which only fires
        # after the *data* is mutated; on_select fires on every pick/
        # deselect too, even ones that don't change any geometry.
        self.on_select = lambda: None
        # Optional callback(), fired right before any select-mode edit
        # (drag-to-move, MOVE/ROTATE, Edit Selected..., Delete Selected)
        # actually mutates the data model -- wired by the app to
        # Session._checkpoint() so undo/redo cover mouse-driven edits
        # too, not just commands that go through Session's own methods.
        # Defaults to a no-op so a bare LayoutCanvas (e.g. in a test)
        # works without an app/session wiring it up.
        self.checkpoint = lambda: None
        self._pending_points = []
        self._last_mouse_dbu = (0, 0)
        # Set by continue_path() while extending an existing Path: the
        # shape _commit_path() should replace (rather than adding a
        # brand new one alongside it) once the extended point list is
        # finished. None for an ordinary from-scratch path draw.
        self._continue_target = None

        # --- selection / move / edit state -------------------------------
        self._item_to_shape = {}  # tk item id -> ("shape"|"sref", object), current cell only
        self.selected = None  # ("shape"|"sref", object) or ("vertex"|"edge", shape, ...) or None
        # Window/marquee (rubber-band) multi-selection: a list of
        # ("shape"|"sref", object) tuples, built by dragging a selection
        # rectangle over empty canvas -- see _select_click/_finish_marquee.
        # Mutually exclusive with self.selected: exactly one of the two
        # is ever populated at a time.
        self.selected_multi = []
        self._drag_start_mouse = None
        self._drag_snapshot = None
        # A drag can fire many _on_drag_motion mutations per mouse
        # gesture; only the first one should push an undo checkpoint
        # (the "before the drag started" state), not every intermediate
        # mutation. Reset whenever a new drag begins (_select_click).
        self._drag_checkpointed = False
        # Marquee-select state: set while dragging a selection rectangle
        # over empty canvas in Select mode.
        self._marquee_start_mouse = None
        self.MARQUEE_MIN_PX = 3
        # Zoom Window state: set (to a pixel coordinate) while dragging
        # a zoom-to-this-area box in "zoomwin" mode -- see _on_click/
        # _on_drag_motion/_on_release's "zoomwin" branches and
        # _finish_zoomwin()/zoom_to_window().
        self._zoomwin_start = None
        # Group-drag state: set while dragging a shape/sref that is
        # already part of self.selected_multi, so the whole group moves
        # together instead of just the one clicked.
        self._group_drag = False
        self._group_snapshot = None

        # Coalesces rapid successive redraw requests (many mouse-motion
        # events during a pan or a shape/vertex/edge drag) into a single
        # full redraw per Tk idle cycle -- see _schedule_redraw().
        self._redraw_scheduled = False

        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<ButtonPress-2>", self._pan_start_evt)
        self.bind("<B2-Motion>", self._pan_move_evt)
        self.bind("<ButtonRelease-2>", self._pan_end_evt)
        self.bind("<ButtonPress-3>", self._pan_start_evt)
        self.bind("<B3-Motion>", self._pan_move_evt)
        self.bind("<ButtonRelease-3>", self._pan_end_evt)
        self.bind("<MouseWheel>", self._on_wheel)      # Windows / macOS
        self.bind("<Button-4>", lambda e: self.zoom(1.1, e.x, e.y))   # Linux
        self.bind("<Button-5>", lambda e: self.zoom(1 / 1.1, e.x, e.y))
        self.bind("<Motion>", self._on_motion)
        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag_motion)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Double-Button-1>", self._on_double_click)
        self.bind("<Escape>", lambda e: self.cancel_draw())
        self.bind("<Return>", lambda e: self.finish_draw())
        self.bind("<BackSpace>", lambda e: self._undo_point())
        self.bind("<space>", self._route_drop_via)
        self.bind("<Delete>", lambda e: self.delete_selected())
        self.focus_set()

    # --- coordinate transforms -----------------------------------------
    def to_px(self, x_dbu, y_dbu):
        px = x_dbu * self.scale + self.offset_x
        py = -y_dbu * self.scale + self.offset_y  # y flips: DBU up = screen up
        return px, py

    def to_dbu(self, px, py):
        x = (px - self.offset_x) / self.scale
        y = -(py - self.offset_y) / self.scale
        return x, y

    # --- view commands ----------------------------------------------------
    def set_cell(self, name):
        self.cell_name = name
        self.selected = None
        self.selected_multi = []
        self.redraw()
        self.on_select()

    def zoom(self, factor, anchor_px=None, anchor_py=None):
        if anchor_px is None:
            anchor_px = self.winfo_width() / 2
            anchor_py = self.winfo_height() / 2
        before = self.to_dbu(anchor_px, anchor_py)
        self.scale *= factor
        self.scale = max(0.001, min(self.scale, 5000))
        after_px, after_py = self.to_px(*before)
        self.offset_x += anchor_px - after_px
        self.offset_y += anchor_py - after_py
        self.redraw()

    def zoom_in(self):
        self.zoom(1.25)

    def zoom_out(self):
        self.zoom(0.8)

    def _cell_bbox_safe(self, name):
        """library.cell_bbox() but never raises: a design that somehow
        contains a cyclic cell reference must still open and redraw so
        the user can find and break it, rather than crashing every
        redraw with 'Unexpected Error'. Returns None on a cycle."""
        try:
            return self.library.cell_bbox(name)
        except CellError:
            return None

    def fit_view(self, padding=0.08):
        box = self._cell_bbox_safe(self.cell_name) if self.cell_name else None
        w = max(self.winfo_width(), 1)
        h = max(self.winfo_height(), 1)
        if box is None:
            self.scale = self.PX_PER_MM_DEFAULT * DBU_MM
            self.offset_x, self.offset_y = w / 2, h / 2
            self.redraw()
            return
        self._fit_to_box(box, padding=padding)

    def zoom_to_window(self, x1, y1, x2, y2, padding=0.04):
        """Zoom/pan so the DBU-space rectangle (x1,y1)-(x2,y2) fills the
        view -- the Zoom Window tool's operation (drag a box around the
        area you want to see; see "zoomwin" mode / _finish_zoomwin()),
        also usable directly for an exact/scripted view via the ZOOM
        WINDOW command or Session-level code. A smaller default padding
        than fit_view()'s: the box was drawn deliberately around the
        area of interest, so it should fill the view closely rather
        than leaving fit_view()'s more generous margin."""
        self._fit_to_box((x1, y1, x2, y2), padding=padding)

    def _fit_to_box(self, box, padding):
        """Shared math behind fit_view()/zoom_to_window(): set scale/
        offset so the DBU-space box (x1,y1,x2,y2) fills the current
        viewport, with `padding` as the fraction of the viewport left
        as margin on each axis."""
        w = max(self.winfo_width(), 1)
        h = max(self.winfo_height(), 1)
        x1, y1, x2, y2 = box
        bw, bh = max(x2 - x1, 1), max(y2 - y1, 1)
        sx = (w * (1 - padding)) / bw
        sy = (h * (1 - padding)) / bh
        self.scale = max(0.001, min(sx, sy))
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        self.offset_x = w / 2 - cx * self.scale
        self.offset_y = h / 2 + cy * self.scale
        self.redraw()

    def toggle_grid(self):
        self.show_grid = not self.show_grid
        self.redraw()

    def toggle_pin_numbers(self):
        self.show_pin_numbers = not self.show_pin_numbers
        self.redraw()

    def set_grid_spacing(self, dbu, sync_snap=True):
        """Change the minor grid line spacing (in DBU) -- see Setup >
        Grid Spacing... and the GRID <spacing> command. Raises
        GeometryError if dbu isn't positive. By default also updates
        snap_dbu to match (see the grid_dbu docstring in __init__ for
        why the drawn grid and the snap grid default to being the same
        setting); pass sync_snap=False to change only the drawn grid
        and leave an independently-set snap spacing alone."""
        dbu = int(round(dbu))
        if dbu <= 0:
            raise GeometryError("grid spacing must be positive")
        self.grid_dbu = dbu
        if sync_snap:
            self.snap_dbu = dbu
        self.redraw()

    # --- interactive drawing: mode control -------------------------------
    def set_mode(self, mode):
        """Switch the active drawing tool: select, rect, poly, path, text, sref, aref, zoomwin, route, wire_bond."""
        self.cancel_draw()
        if mode != "select":
            self.selected = None
            self.selected_multi = []
        if mode == "route":
            self._route_active_layer = self.route_layer_a
        self.draw_mode = mode
        self.redraw()
        self.on_mode_status(self._mode_hint())
        self.on_select()
        # Lets an external mode-picker (View > Zoom Window's radiobutton
        # in the toolbar/menu) stay in sync when THIS method changes the
        # mode on its own -- specifically _finish_zoomwin() switching
        # back to "select" once the drawn window has been zoomed to, a
        # one-shot tool like the marquee-select drag it's modelled on.
        self.on_draw_mode_changed(mode)

    def continue_path(self):
        """Edit > Continue Path: re-enter Path drawing pre-seeded with an
        existing Path shape's own points, so you can click to add more
        points onto its end instead of only ever starting a brand new
        path. Requires a single whole Path shape to currently be
        selected (not a vertex/edge of one, not a window-selected
        group); returns False and does nothing otherwise.

        The original shape is left untouched until the extended path
        is actually finished (double-click/Enter, see _commit_path()),
        so Esc still cleanly cancels back to nothing having changed --
        cancel_draw() just drops the pending points and the
        _continue_target reference below."""
        if self.selected is None or self.selected[0] != "shape":
            return False
        _, shape = self.selected
        if not isinstance(shape, Path):
            return False
        self.set_mode("path")
        self._pending_points = list(shape.points)
        self._continue_target = shape
        self._update_preview(*self._last_mouse_dbu)
        self.on_mode_status(
            f"Continue Path: click to add more points, double-click/Enter to finish "
            f"({len(self._pending_points)} existing point(s) loaded, layer {shape.layer})"
        )
        return True

    def _mode_hint(self):
        hints = {
            "select": "Select: click a shape to select it, drag to move, "
                      "double-click to edit, Delete to remove -- or drag "
                      "a box over empty space to window-select multiple",
            "zoomwin": "Zoom Window: drag a box around the area you want to see",
            "rect": "Rectangle: click first corner, then the opposite corner",
            "poly": "Polygon: click each vertex, double-click/Enter to finish (Esc cancels, Backspace undoes a point)",
            "path": "Path: click each point, double-click/Enter to finish (Esc cancels, Backspace undoes a point)",
            "text": "Text: click the insertion point, then fill in the text",
            "sref": "Cell instance: click the placement point, then choose the cell "
                    "(placed unrotated -- rotate afterwards with R / Rotate)",
            "aref": "Array instance: click the placement point, then choose the cell "
                    "and array parameters",
            "circle": "Circle: click the center, then click a point on the edge to set the radius",
            "route": self._route_hint(),
            "measure": "Measure: click a start point, then click a second point -- "
                       "the distance between them is shown on the canvas and in the status bar",
            "pin_number": self._pin_number_hint(),
            "cell_origin": self._cell_origin_hint(),
            "wire_bond": self._wire_bond_hint(),
            "auto_route": self._auto_route_hint(),
        }
        return hints.get(self.draw_mode, "")

    def _auto_route_hint(self):
        if self._auto_route_a is None:
            return ("Auto-Route: click the FIRST pin (a pad, or any point). "
                    "Esc stops.")
        return ("Auto-Route: first pin set -- now click the SECOND pin. A clear "
                "trace is routed between them on the 1st routing conductor, "
                "around other metal on that layer, at the current grid/path "
                "width. Esc stops.")

    def _wire_bond_hint(self):
        return ("Wire Bond: click the first pad, then the second -- an O------O "
                "bond wire is drawn on the bond layer (Setup > Wire Bond "
                "Rules...). Bonds can't cross and must be within min/max "
                "length; Esc cancels the current one")

    def _cell_origin_hint(self):
        return (f"Create Cell {self._pending_cell_name!r}: click the point to use as "
                f"the cell's origin -- it becomes local (0, 0); Esc cancels")

    def _pin_number_hint(self):
        return (f"Number Pins: click a pad to stamp pin {self.pin_number_next!r} "
                f"(numbers auto-increment); Esc stops")

    def _route_hint(self):
        layer = self._route_active_layer
        return (f"Route (2-layer): routing on layer {layer}. Click points; "
                f"Space drops a via and switches conductor layer; "
                f"double-click/Enter finishes; Esc cancels the current segment "
                f"(configure layers/via in Setup > Route Layers...)")

    def cancel_draw(self):
        self._pending_points = []
        self._continue_target = None
        self.delete("preview")
        self.delete("zoomwin_box")
        self.delete("measure_result")
        self._zoomwin_start = None
        if self.draw_mode == "route":
            self._route_active_layer = self.route_layer_a
        if self.draw_mode == "pin_number":
            # Esc stops the pin-numbering tool and drops back to Select,
            # same as any other one-shot/modal tool (Zoom Window, ...).
            # Done directly here rather than via set_mode() (which itself
            # calls cancel_draw()) to avoid recursing.
            self.draw_mode = "select"
            self.selected = None
            self.selected_multi = []
            self.redraw()
            self.on_mode_status(self._mode_hint())
            self.on_draw_mode_changed("select")
        if self.draw_mode == "cell_origin":
            # Esc aborts Create Cell from Selection without making a cell,
            # and restores the selection it was going to wrap so the user
            # doesn't have to re-select. Same direct-exit trick as above.
            self.draw_mode = "select"
            self.selected = None
            self.selected_multi = list(self._pending_cell_items)
            self._pending_cell_name = None
            self._pending_cell_items = []
            self._pending_cell_replace = False
            self.redraw()
            self.on_mode_status(self._mode_hint())
            self.on_draw_mode_changed("select")
        if self.draw_mode == "auto_route":
            # Esc stops the two-click Auto-Route tool and returns to
            # Select, same as pin_number above.
            self._auto_route_a = None
            self.draw_mode = "select"
            self.selected = None
            self.selected_multi = []
            self.redraw()
            self.on_mode_status(self._mode_hint())
            self.on_draw_mode_changed("select")

    def start_pin_numbering(self, start: str = "1"):
        """Edit > Number Pins...: enter the click-to-increment pin-
        numbering tool. Each click on a Rectangle/Polygon in the
        current cell stamps `start` on it, then increments (see
        increment_pin_str()) for the next click. Re-clicking an
        already-numbered pad asks for confirmation before overwriting.
        Esc (cancel_draw()) exits back to Select."""
        self.pin_number_next = (start or "1").strip() or "1"
        self.set_mode("pin_number")

    def _pin_number_click(self, e):
        item = self._pick_item_at(e.x, e.y)
        if item is None:
            return
        kind, shape = self._item_to_shape[item]
        if kind != "shape" or not isinstance(shape, (Rectangle, Polygon)):
            return
        if shape.pin and shape.pin != self.pin_number_next:
            if not messagebox.askyesno(
                "Renumber pad?",
                f"This pad is already pin {shape.pin!r}. Reassign it to "
                f"{self.pin_number_next!r}?",
            ):
                return
        self.checkpoint()
        shape.pin = self.pin_number_next
        self.pin_number_next = increment_pin_str(self.pin_number_next)
        self.on_mode_status(self._pin_number_hint())
        self._after_edit()

    def start_auto_route(self):
        """Edit > Auto-Route Two Pins: enter the two-click auto-router.
        Click the first pin, click the second, and a clear orthogonal
        trace is routed between them on the 1st routing conductor,
        around other metal on that layer, at the current grid and path
        width (via on_auto_route -> Session.auto_route()). Stays armed
        for the next pair; Esc (cancel_draw()) exits to Select."""
        self._auto_route_a = None
        self.set_mode("auto_route")

    def _auto_route_endpoint(self, e):
        """A click in Auto-Route mode resolves to the pad shape under
        the cursor (so the router lands on its centre and excludes its
        own copper), or, over empty space, the snapped (x, y) point."""
        item = self._pick_item_at(e.x, e.y)
        if item is not None:
            kind, shape = self._item_to_shape[item]
            if kind == "shape" and isinstance(shape, (Rectangle, Polygon)):
                return shape
        x, y = self.to_dbu(e.x, e.y)
        return self._snap(x, y)

    def _auto_route_click(self, e):
        target = self._auto_route_endpoint(e)
        if self._auto_route_a is None:
            self._auto_route_a = target
            self._draw_auto_route_marker(target)
            self.on_mode_status(self._auto_route_hint())
            return
        a, self._auto_route_a = self._auto_route_a, None
        self.delete("preview")
        self.on_auto_route(a, target)
        self.on_mode_status(self._auto_route_hint())

    def _draw_auto_route_marker(self, target):
        """Ring the first picked endpoint while waiting for the second."""
        self.delete("preview")
        if isinstance(target, (Rectangle, Polygon)):
            x0, y0, x1, y1 = target.bbox()
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        else:
            cx, cy = target
        px, py = self.to_px(cx, cy)
        r = 7
        self.create_oval(px - r, py - r, px + r, py + r, outline="#00d0ff",
                          width=2, tags="preview")
        self.create_line(px - r - 3, py, px + r + 3, py, fill="#00d0ff", tags="preview")
        self.create_line(px, py - r - 3, px, py + r + 3, fill="#00d0ff", tags="preview")

    def _cell_origin_click(self, e):
        """A click in "cell_origin" mode: the snapped point becomes the
        new cell's local (0, 0). Restores the stashed selection, drops
        back to Select, and wraps it into the cell via
        create_cell_from_selection()."""
        x, y = self.to_dbu(e.x, e.y)
        ox, oy = self._next_draw_point(x, y)
        name = self._pending_cell_name
        items = self._pending_cell_items
        replace = self._pending_cell_replace
        self._pending_cell_name = None
        self._pending_cell_items = []
        self._pending_cell_replace = False
        self.draw_mode = "select"
        self.selected = None
        self.selected_multi = list(items)
        self.on_draw_mode_changed("select")
        try:
            self.create_cell_from_selection(name, (int(round(ox)), int(round(oy))),
                                             replace=replace)
        except CellError as err:
            messagebox.showerror("Create Cell from Selection", str(err))
            self.redraw()
            self.on_mode_status(self._mode_hint())
            return
        self.on_mode_status(self._mode_hint())

    def _undo_point(self):
        if self._pending_points:
            self._pending_points.pop()
            self._update_preview(*self._last_mouse_dbu)

    def finish_draw(self):
        if self.draw_mode == "poly":
            self._commit_poly()
        elif self.draw_mode == "path":
            self._commit_path()
        elif self.draw_mode == "route":
            self._commit_route()

    # --- snapping -----------------------------------------------------
    def _snap(self, x, y):
        if not self.snap_enabled or self.snap_dbu <= 0:
            return (int(round(x)), int(round(y)))
        s = self.snap_dbu
        return (int(round(x / s)) * s, int(round(y / s)) * s)

    def _constrain_to_ortho(self, x0, y0, x, y):
        """Snap raw candidate point (x, y) onto the nearest of the 8
        compass/diagonal directions (0/45/90/.../315 degrees) from
        (x0, y0) -- the last point already placed -- so the segment
        from (x0, y0) is always horizontal, vertical, or a clean
        45-degree diagonal. Used by Path/Route drawing when
        self.ortho_enabled is on; see _next_draw_point().

        The distance travelled along that direction is preserved (and
        snapped to the current grid, same as _snap(), if snapping is
        on) rather than the raw (x, y) itself -- so an ortho segment's
        LENGTH still lands on-grid even though a 45-degree diagonal's
        endpoint generally won't (cos/sin 45 degrees isn't a grid
        multiple of the perpendicular axis; this matches how real
        45-degree routing works in any EDA tool)."""
        dx, dy = x - x0, y - y0
        if dx == 0 and dy == 0:
            return (x0, y0)
        length = math.hypot(dx, dy)
        if self.snap_enabled and self.snap_dbu > 0:
            length = round(length / self.snap_dbu) * self.snap_dbu
        angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
        nx = x0 + length * math.cos(angle)
        ny = y0 + length * math.sin(angle)
        return (int(round(nx)), int(round(ny)))

    def _next_draw_point(self, x, y):
        """Turn a raw DBU cursor position into the point a click would
        actually place: grid-snapped via _snap(), and -- for Path/Route
        drawing, once at least one point is already pending, with
        self.ortho_enabled on -- further constrained to a clean
        horizontal/vertical/45-degree segment from the last point via
        _constrain_to_ortho(). Shared by _on_click() and _on_motion()
        so the rubber-band preview always shows exactly the point a
        click would commit, never a raw diagonal the click wouldn't
        actually land on."""
        if (self.ortho_enabled and self.draw_mode in ("path", "route")
                and self._pending_points):
            x0, y0 = self._pending_points[-1]
            return self._constrain_to_ortho(x0, y0, x, y)
        return self._snap(x, y)

    # --- mouse handlers -----------------------------------------------
    def _pan_start_evt(self, e):
        self._pan_start = (e.x, e.y, self.offset_x, self.offset_y)

    def _pan_move_evt(self, e):
        if not self._pan_start:
            return
        sx, sy, ox, oy = self._pan_start
        new_offset_x = ox + (e.x - sx)
        new_offset_y = oy + (e.y - sy)
        dx = new_offset_x - self.offset_x
        dy = new_offset_y - self.offset_y
        self.offset_x = new_offset_x
        self.offset_y = new_offset_y
        # Translate the existing canvas items instead of a full
        # delete-and-recreate redraw() on every pixel of mouse movement --
        # move() is a native, near-free canvas operation, whereas redraw()
        # rebuilds the grid (thousands of dots) and every shape from
        # scratch. The grid was pre-drawn with a pan buffer (see
        # _draw_grid) so short pans don't reveal gaps; _pan_end_evt() does
        # one real redraw once the pan finishes to fix up the view exactly.
        self.move("all", dx, dy)

    def _pan_end_evt(self, e):
        self._pan_start = None
        self.redraw()

    def _on_wheel(self, e):
        factor = 1.1 if e.delta > 0 else 1 / 1.1
        self.zoom(factor, e.x, e.y)

    def _on_motion(self, e):
        x, y = self.to_dbu(e.x, e.y)
        sx, sy = self._next_draw_point(x, y)
        self._last_mouse_dbu = (sx, sy)
        self.on_status(f"x={dbu_to_mm(sx):.3f} mm  y={dbu_to_mm(sy):.3f} mm")
        self._update_preview(sx, sy)
        self._draw_snap_cursor(sx, sy)

    def _draw_snap_cursor(self, x, y):
        """A crosshair marker at the snapped grid point the next click
        will land on. The real OS pointer can't be warped onto the grid
        without janky side effects, so this stands in as the visual
        'snapped position' indicator, the way most CAD tools do it."""
        self.delete("snapcursor")
        if not self.snap_enabled:
            return
        px, py = self.to_px(x, y)
        r = 6
        self.create_line(px - r, py, px + r, py, fill="#00ffcc", width=1, tags="snapcursor")
        self.create_line(px, py - r, px, py + r, fill="#00ffcc", width=1, tags="snapcursor")
        self.create_oval(px - 2, py - 2, px + 2, py + 2, outline="#00ffcc", tags="snapcursor")

    def _on_click(self, e):
        self.focus_set()
        if self.draw_mode == "select":
            self._select_click(e)
            return
        if self.draw_mode == "pin_number":
            self._pin_number_click(e)
            return
        if self.draw_mode == "auto_route":
            self._auto_route_click(e)
            return
        if self.draw_mode == "cell_origin":
            self._cell_origin_click(e)
            return
        x, y = self.to_dbu(e.x, e.y)
        pt = self._next_draw_point(x, y)

        if self.draw_mode == "rect":
            self._pending_points.append(pt)
            if len(self._pending_points) == 2:
                self._commit_rect()
        elif self.draw_mode == "circle":
            self._pending_points.append(pt)
            if len(self._pending_points) == 2:
                self._commit_circle()
        elif self.draw_mode in ("poly", "path", "route"):
            self._pending_points.append(pt)
            self._update_preview(*pt)
        elif self.draw_mode == "text":
            self._pending_points = [pt]
            self._prompt_text()
        elif self.draw_mode == "sref":
            self._pending_points = [pt]
            self._prompt_sref()
        elif self.draw_mode == "aref":
            self._pending_points = [pt]
            self._prompt_aref()
        elif self.draw_mode == "zoomwin":
            # Pixel coordinates, not the snapped DBU `pt` computed above
            # -- zooming to a view is a screen-space drag, not geometry.
            self._zoomwin_start = (e.x, e.y)
        elif self.draw_mode == "measure":
            # A fresh first click starts a new measurement -- drop
            # whatever the previous one left on screen rather than
            # letting it linger once a new one is started.
            if not self._pending_points:
                self.delete("measure_result")
            self._pending_points.append(pt)
            if len(self._pending_points) == 2:
                self._commit_measure()
        elif self.draw_mode == "wire_bond":
            self._pending_points.append(pt)
            if len(self._pending_points) == 2:
                self._commit_wire_bond()
            else:
                self._update_preview(*pt)

    def _on_double_click(self, e):
        if self.draw_mode == "select":
            item = self._pick_item_at(e.x, e.y)
            if item is not None:
                kind, shape = self._item_to_shape[item]
                if kind in ("sref_label", "sref_reftext"):
                    kind = "sref"   # edit the instance (Label text lives there)
                _open_edit_dialog(self, kind, shape, on_done=self._after_edit,
                                   checkpoint=self.checkpoint)
            return
        if self.draw_mode == "poly":
            self._commit_poly()
        elif self.draw_mode == "path":
            self._commit_path()
        elif self.draw_mode == "route":
            self._commit_route()

    # --- selection / move / edit / delete --------------------------------
    VERTEX_HIT_PX = 6
    EDGE_HIT_PX = 5

    #: Radius (screen px) of the endpoint markers _draw_measure_overlay()
    #: draws at each end of a Measure tool line -- bumped up from the
    #: original 3px so the endpoints read clearly at a glance instead of
    #: needing to zoom in to see exactly where the line lands.
    MEASURE_POINT_RADIUS = 6

    def _pick_item_at(self, ex, ey):
        items = self.find_overlapping(ex - 3, ey - 3, ex + 3, ey + 3)
        for item in reversed(items):
            if item in self._item_to_shape:
                return item
        return None

    def _editable_point_shapes(self):
        """Polygon/Path shapes in the current cell — the ones whose
        individual vertices/edges can be grabbed and edited directly.
        (Rectangle is excluded: reshaping one corner would break its
        4-point-rectangle invariant; use the edit dialog for that.)
        Shapes on a hidden layer are excluded too -- selection should
        only ever pick up what's actually visible on screen, the same
        as whole-shape/sref selection (_pick_item_at, backed by
        _item_to_shape, which redraw() only populates for visible
        layers) already does."""
        cell = self.library.cells.get(self.cell_name)
        if cell is None:
            return []
        return [s for s in cell.shapes if isinstance(s, (Polygon, Path))
                and self.library.layers[s.layer].visible]

    def _hit_test_vertex(self, ex, ey):
        best = None
        best_d2 = self.VERTEX_HIT_PX ** 2
        for shape in self._editable_point_shapes():
            for i, p in enumerate(shape.points):
                px, py = self.to_px(*p)
                d2 = (px - ex) ** 2 + (py - ey) ** 2
                if d2 <= best_d2:
                    best_d2 = d2
                    best = (shape, i)
        return best

    def _hit_test_edge(self, ex, ey):
        best = None
        best_d = self.EDGE_HIT_PX
        for shape in self._editable_point_shapes():
            pts = shape.points
            n = len(pts)
            edge_count = n if isinstance(shape, Polygon) else n - 1
            for i in range(edge_count):
                j = (i + 1) % n
                pa = self.to_px(*pts[i])
                pb = self.to_px(*pts[j])
                d = _dist_point_to_segment(ex, ey, pa[0], pa[1], pb[0], pb[1])
                if d <= best_d:
                    best_d = d
                    best = (shape, i, j)
        return best

    def _select_click(self, e):
        self._select_click_impl(e)
        self.on_select()

    def _select_click_impl(self, e):
        # A fresh click starts a possible new drag -- reset so the next
        # _on_drag_motion (if any) pushes exactly one undo checkpoint.
        self._drag_checkpointed = False
        # Priority: a vertex under the cursor wins over an edge, which
        # wins over the whole-shape/sref pick — matches "click the edge
        # moves the edge, click the vertex moves the vertex" behaviour.
        vhit = self._hit_test_vertex(e.x, e.y)
        if vhit:
            shape, idx = vhit
            self.selected = ("vertex", shape, idx)
            self.selected_multi = []
            self._marquee_start_mouse = None
            self._group_drag = False
            self._drag_start_mouse = self.to_dbu(e.x, e.y)
            self._drag_snapshot = list(shape.points)
            self.redraw()
            return

        ehit = self._hit_test_edge(e.x, e.y)
        if ehit:
            shape, i, j = ehit
            self.selected = ("edge", shape, (i, j))
            self.selected_multi = []
            self._marquee_start_mouse = None
            self._group_drag = False
            self._drag_start_mouse = self.to_dbu(e.x, e.y)
            self._drag_snapshot = list(shape.points)
            self.redraw()
            return

        item = self._pick_item_at(e.x, e.y)
        if item is None:
            # Empty space: clear the current selection and start tracking
            # a possible window-select (marquee) drag -- see
            # _on_drag_motion/_finish_marquee. If the mouse never moves
            # past the drag threshold, this ends up as a plain
            # click-to-deselect (nothing further happens on release).
            self.selected = None
            self.selected_multi = []
            self._drag_start_mouse = None
            self._group_drag = False
            self._marquee_start_mouse = (e.x, e.y)
            self.redraw()
            return

        kind, shape = self._item_to_shape[item]
        self._marquee_start_mouse = None

        if kind == "sref_label":
            # Dragging the floated ref-des moves it relative to the part
            # (updates ref.label_offset), not the part itself.
            self.selected_multi = []
            self._group_drag = False
            self.selected = ("sref_label", shape)
            self._drag_start_mouse = self.to_dbu(e.x, e.y)
            self._drag_snapshot = tuple(shape.label_offset)
            self.redraw()
            return
        if kind == "sref_reftext":
            # Dragging the baked ref-des Text handle translates that Text
            # inside the instance's own cell.
            baked = self._refdes_baked_shape(shape)
            if baked is None:
                return
            self.selected_multi = []
            self._group_drag = False
            self.selected = ("sref_reftext", shape)
            self._drag_start_mouse = self.to_dbu(e.x, e.y)
            self._drag_snapshot = tuple(baked.origin)
            self.redraw()
            return

        if self.selected_multi and (kind, shape) in self.selected_multi:
            # Clicked on a member of the current window-selection: keep
            # the whole group selected and drag it together.
            self._group_drag = True
            self._drag_start_mouse = self.to_dbu(e.x, e.y)
            self._group_snapshot = [(k, s, _geom_snapshot(k, s)) for k, s in self.selected_multi]
            self.redraw()
            return

        self.selected_multi = []
        self._group_drag = False
        self.selected = (kind, shape)
        self._drag_start_mouse = self.to_dbu(e.x, e.y)
        self._drag_snapshot = _geom_snapshot(kind, shape)
        self.redraw()

    def _on_drag_motion(self, e):
        if self.draw_mode == "zoomwin":
            if self._zoomwin_start is not None:
                self._update_zoomwin_preview(e.x, e.y)
            return
        if self.draw_mode != "select":
            return
        if self._marquee_start_mouse is not None:
            self._update_marquee_preview(e.x, e.y)
            return
        if self._group_drag:
            self._drag_group_motion(e)
            return
        if self.selected is None or self._drag_start_mouse is None:
            return
        x, y = self.to_dbu(e.x, e.y)
        dx = x - self._drag_start_mouse[0]
        dy = y - self._drag_start_mouse[1]
        if self.snap_enabled:
            dx, dy = self._snap(dx, dy)

        if not self._drag_checkpointed:
            self.checkpoint()
            self._drag_checkpointed = True

        kind = self.selected[0]
        if kind == "sref_label":
            _, ref = self.selected
            ox, oy = self._drag_snapshot
            ref.label_offset = (int(round(ox + dx)), int(round(oy + dy)))
        elif kind == "sref_reftext":
            _, ref = self.selected
            baked = self._refdes_baked_shape(ref)
            if baked is not None:
                ldx, ldy = self._parent_delta_to_local(ref, dx, dy)
                ox, oy = self._drag_snapshot
                baked.origin = (int(round(ox + ldx)), int(round(oy + ldy)))
        elif kind == "vertex":
            _, shape, idx = self.selected
            ox, oy = self._drag_snapshot[idx]
            pts = list(shape.points)
            pts[idx] = (int(round(ox + dx)), int(round(oy + dy)))
            shape.points = pts
        elif kind == "edge":
            _, shape, (i, j) = self.selected
            oxi, oyi = self._drag_snapshot[i]
            oxj, oyj = self._drag_snapshot[j]
            pts = list(shape.points)
            pts[i] = (int(round(oxi + dx)), int(round(oyi + dy)))
            pts[j] = (int(round(oxj + dx)), int(round(oyj + dy)))
            shape.points = pts
        else:
            _, shape = self.selected
            _apply_translate(shape, self._drag_snapshot, dx, dy)
        # The data model update above is immediate and synchronous; only
        # the (expensive) visual redraw is coalesced -- see
        # _schedule_redraw(). _on_release() below still forces one last
        # synchronous redraw via _after_edit(), so the final frame is
        # always exact even if a scheduled redraw was still pending.
        self._schedule_redraw()

    def _update_marquee_preview(self, ex, ey):
        self.delete("marquee")
        sx, sy = self._marquee_start_mouse
        if abs(ex - sx) < self.MARQUEE_MIN_PX and abs(ey - sy) < self.MARQUEE_MIN_PX:
            return
        self.create_rectangle(sx, sy, ex, ey, outline="#00d0ff", dash=(3, 2), tags="marquee")

    def _update_zoomwin_preview(self, ex, ey):
        self.delete("zoomwin_box")
        sx, sy = self._zoomwin_start
        if abs(ex - sx) < self.MARQUEE_MIN_PX and abs(ey - sy) < self.MARQUEE_MIN_PX:
            return
        self.create_rectangle(sx, sy, ex, ey, outline="#ffcc00", dash=(4, 2), width=1.5,
                               tags="zoomwin_box")

    def _drag_group_motion(self, e):
        x, y = self.to_dbu(e.x, e.y)
        dx = x - self._drag_start_mouse[0]
        dy = y - self._drag_start_mouse[1]
        if self.snap_enabled:
            dx, dy = self._snap(dx, dy)
        if not self._drag_checkpointed:
            self.checkpoint()
            self._drag_checkpointed = True
        for kind, shape, snapshot in self._group_snapshot:
            _apply_translate(shape, snapshot, dx, dy)
        self._schedule_redraw()

    def _schedule_redraw(self):
        """Coalesce rapid successive redraw requests into a single full
        redraw() per Tk idle cycle. Mouse-motion events (dragging a shape,
        panning) can arrive far faster than a full delete-and-recreate
        redraw can keep up with; without this, a drag on anything but a
        tiny design visibly lags behind the cursor."""
        if self._redraw_scheduled:
            return
        self._redraw_scheduled = True
        self.after_idle(self._run_scheduled_redraw)

    def _run_scheduled_redraw(self):
        self._redraw_scheduled = False
        self.redraw()

    def _on_release(self, e):
        if self.draw_mode == "zoomwin":
            self._finish_zoomwin(e)
            return
        if self._marquee_start_mouse is not None:
            self._finish_marquee(e)
            return
        if self._group_drag:
            self._group_drag = False
            self._drag_start_mouse = None
            self._group_snapshot = None
            self._after_edit()
            return
        moved = self._drag_start_mouse is not None
        self._drag_start_mouse = None
        self._drag_snapshot = None
        if moved and self.draw_mode == "select" and self.selected is not None:
            self._after_edit()

    def _finish_marquee(self, e):
        self._finish_marquee_impl(e)
        self.on_select()

    def _finish_marquee_impl(self, e):
        self.delete("marquee")
        sx, sy = self._marquee_start_mouse
        self._marquee_start_mouse = None
        if abs(e.x - sx) < self.MARQUEE_MIN_PX and abs(e.y - sy) < self.MARQUEE_MIN_PX:
            # Never actually dragged -- treat as a plain click-to-deselect
            # (selection was already cleared in _select_click).
            self.redraw()
            return
        cell = self.library.cells.get(self.cell_name)
        if cell is None:
            self.redraw()
            return
        x0, y0 = self.to_dbu(sx, sy)
        x1, y1 = self.to_dbu(e.x, e.y)
        rx1, rx2 = sorted((x0, x1))
        ry1, ry2 = sorted((y0, y1))

        picked = []
        for shape in cell.shapes:
            if not self.library.layers[shape.layer].visible:
                # Window-select should only pick up what's actually
                # visible on screen, same as a plain click (_pick_item_at
                # / _editable_point_shapes both already skip hidden
                # layers).
                continue
            bx1, by1, bx2, by2 = shape.bbox()
            if bx1 >= rx1 and bx2 <= rx2 and by1 >= ry1 and by2 <= ry2:
                picked.append(("shape", shape))
        for ref in cell.srefs:
            box = self._cell_bbox_safe(ref.cell_name)
            if box is not None:
                from ..core.library import _transform_bbox
                bx1, by1, bx2, by2 = _transform_bbox(box, ref)
            else:
                ox, oy = ref.origin
                bx1 = bx2 = ox
                by1 = by2 = oy
            if bx1 >= rx1 and bx2 <= rx2 and by1 >= ry1 and by2 <= ry2:
                picked.append(("sref", ref))
        for aref in cell.arefs:
            bx1, by1, bx2, by2 = self._aref_bbox(aref)
            if bx1 >= rx1 and bx2 <= rx2 and by1 >= ry1 and by2 <= ry2:
                picked.append(("aref", aref))

        self.selected = None
        self.selected_multi = picked
        if picked:
            self.on_mode_status(f"{len(picked)} object(s) window-selected")
        self.redraw()

    def _finish_zoomwin(self, e):
        """Complete a Zoom Window drag (draw_mode == "zoomwin"): zoom/
        pan so the dragged box fills the view, then switch back to
        Select -- Zoom Window is a one-shot tool, same convention as
        the marquee-select drag it's modelled on. A drag too small to
        be deliberate (a stray click, same MARQUEE_MIN_PX threshold as
        marquee-select) is treated as a no-op cancel instead of zooming
        to a near-zero-size box."""
        self.delete("zoomwin_box")
        start = self._zoomwin_start
        self._zoomwin_start = None
        if start is None:
            return
        sx, sy = start
        if abs(e.x - sx) >= self.MARQUEE_MIN_PX or abs(e.y - sy) >= self.MARQUEE_MIN_PX:
            x0, y0 = self.to_dbu(sx, sy)
            x1, y1 = self.to_dbu(e.x, e.y)
            self.zoom_to_window(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self.set_mode("select")

    def _aref_bbox(self, ref):
        """Bounding box (DBU) of an ARef, unioning every array position's
        transformed instance -- same idea as Library.cell_bbox's own
        aref handling, but for a single ARef in isolation (used by
        selection/window-select/highlight, which don't need the whole
        cell's box)."""
        box = self._cell_bbox_safe(ref.cell_name)
        if box is None:
            ox, oy = ref.origin
            return (ox, oy, ox, oy)
        from ..core.library import _transform_bbox
        boxes = []
        for origin in ref.instance_origins():
            instance = SRef(cell_name=ref.cell_name, origin=origin, rotation=ref.rotation,
                             magnification=ref.magnification, x_reflection=ref.x_reflection)
            boxes.append(_transform_bbox(box, instance))
        x1 = min(b[0] for b in boxes)
        y1 = min(b[1] for b in boxes)
        x2 = max(b[2] for b in boxes)
        y2 = max(b[3] for b in boxes)
        return (x1, y1, x2, y2)

    def selection_bbox(self):
        """Combined DBU bounding box (x0, y0, x1, y1) of everything
        currently selected -- a window group or a single shape/instance
        -- or None if nothing selectable is selected (a bare vertex or
        edge doesn't count). Used to give Create Cell from Selection a
        sensible default origin at the selection's lower-left corner
        rather than the design's (0, 0)."""
        from ..core.library import _transform_bbox
        items = list(self.selected_multi)
        if not items and self.selected is not None and self.selected[0] in ("shape", "sref", "aref"):
            items = [self.selected]
        boxes = []
        for kind, obj in items:
            if kind == "shape":
                boxes.append(obj.bbox())
            elif kind == "sref":
                box = self._cell_bbox_safe(obj.cell_name)
                boxes.append(_transform_bbox(box, obj) if box is not None else None)
            elif kind == "aref":
                boxes.append(self._aref_bbox(obj))
        boxes = [b for b in boxes if b is not None]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def edit_selected(self):
        """Open the property-edit dialog for the current selection —
        the same dialog a double-click on the shape opens. Used by the
        Edit > Edit Selected... menu item as a keyboard/menu-driven
        alternative to double-clicking. Returns True if a dialog was
        opened, False if nothing is selected (vertex/edge selections
        open the dialog for their underlying shape). A window-selection
        of more than one object can't be edited at once -- edit them one
        at a time -- so this only opens the dialog when exactly one
        object is window-selected."""
        sel = self.selected
        if sel is None and len(self.selected_multi) == 1:
            sel = self.selected_multi[0]
        if not sel:
            return False
        kind = sel[0]
        if kind in ("vertex", "edge"):
            shape = sel[1]
            edit_kind = "sref" if isinstance(shape, SRef) else "shape"
            _open_edit_dialog(self, edit_kind, shape, on_done=self._after_edit,
                               checkpoint=self.checkpoint)
        else:
            _, shape = sel
            _open_edit_dialog(self, kind, shape, on_done=self._after_edit,
                               checkpoint=self.checkpoint)
        return True

    def insert_vertex_on_selected_edge(self):
        """Edit > Insert Vertex: add a new point on the currently
        selected edge (Polygon or Path), at the spot on that edge you
        clicked to select it -- click an edge, then call this, rather
        than needing a whole separate "click precisely on the edge to
        add a point" gesture layered onto plain selection. Returns
        False (does nothing) if the selection isn't an edge, or the
        shape is already at its point-count limit."""
        if self.draw_mode != "select" or self.selected is None or self.selected[0] != "edge":
            return False
        _, shape, (i, j) = self.selected
        limit = MAX_POLY_POINTS if isinstance(shape, Polygon) else MAX_PATH_POINTS
        if len(shape.points) >= limit:
            self.on_mode_status(f"can't insert a vertex: shape already has the max {limit} points")
            return False
        # The point on the edge that was actually clicked to select it
        # (see _select_click_impl) -- inserting exactly there, rather
        # than e.g. always the edge's midpoint, is what makes this feel
        # like "add a point here" instead of "add a point somewhere on
        # this edge".
        pt = self._drag_start_mouse
        if pt is None:
            return False
        if self.snap_enabled:
            pt = self._snap(*pt)
        pt = (int(round(pt[0])), int(round(pt[1])))
        self.checkpoint()
        pts = list(shape.points)
        pts.insert(i + 1, pt)
        shape.points = pts
        self.selected = ("vertex", shape, i + 1)
        self.redraw()
        self._after_edit()
        return True

    def delete_selected(self):
        if self.draw_mode != "select":
            return
        cell = self.library.cells.get(self.cell_name)
        if cell is None:
            return

        if self.selected_multi:
            self.checkpoint()
            for kind, shape in self.selected_multi:
                if kind == "shape" and shape in cell.shapes:
                    cell.shapes.remove(shape)
                elif kind == "sref" and shape in cell.srefs:
                    cell.srefs.remove(shape)
                elif kind == "aref" and shape in cell.arefs:
                    cell.arefs.remove(shape)
            self.selected_multi = []
            self.redraw()
            self._after_edit()
            return

        if self.selected is None:
            return
        kind = self.selected[0]

        if kind == "vertex":
            _, shape, idx = self.selected
            min_pts = 3 if isinstance(shape, Polygon) else 2
            if len(shape.points) <= min_pts:
                self.on_mode_status(f"can't delete vertex: needs at least {min_pts} points")
                return
            self.checkpoint()
            pts = list(shape.points)
            del pts[idx]
            shape.points = pts
            self.selected = None
        elif kind == "edge":
            _, shape, (i, j) = self.selected
            if not isinstance(shape, Path):
                # A polygon's edges are implied by consecutive vertices,
                # not a segment that can be removed independently
                # without either breaking the closed loop or requiring
                # it to become an open shape -- select a vertex to
                # delete a point instead.
                self.on_mode_status("select a vertex (not an edge) to delete a point "
                                     "-- deleting a polygon edge isn't supported")
                return
            # Deleting a segment out of the middle of a Path can't leave
            # a single, still-connected Path behind -- it splits into up
            # to two independent pieces (dropping either piece if it
            # would be left with under 2 points, e.g. deleting the very
            # first or last segment just trims that end instead of
            # leaving a stray 1-point sliver).
            pts = shape.points
            left = pts[:i + 1]
            right = pts[j:]
            pieces = [p for p in (left, right) if len(p) >= 2]
            if not pieces:
                self.on_mode_status("can't delete the only segment of a 2-point path "
                                     "-- delete the whole path instead")
                return
            self.checkpoint()
            new_shapes = [Path(layer=shape.layer, points=p, width=shape.width,
                                cap=shape.cap, datatype=shape.datatype) for p in pieces]
            if shape in cell.shapes:
                cell.shapes.remove(shape)
            cell.shapes.extend(new_shapes)
            if len(new_shapes) == 1:
                self.selected = ("shape", new_shapes[0])
            else:
                self.selected = None
                self.selected_multi = [("shape", s) for s in new_shapes]
            self.on_mode_status(f"deleted segment -- path split into {len(new_shapes)} piece(s)")
        elif kind == "shape":
            _, shape = self.selected
            if shape in cell.shapes:
                self.checkpoint()
                cell.shapes.remove(shape)
            self.selected = None
        elif kind == "sref":
            _, shape = self.selected
            if shape in cell.srefs:
                self.checkpoint()
                cell.srefs.remove(shape)
            self.selected = None
        elif kind == "aref":
            _, shape = self.selected
            if shape in cell.arefs:
                self.checkpoint()
                cell.arefs.remove(shape)
            self.selected = None
        elif kind in ("sref_label", "sref_reftext"):
            # Delete on a ref-des selection clears the ref designator
            # (and any baked Text for it), not the instance.
            _, ref = self.selected
            self.checkpoint()
            if kind == "sref_reftext":
                child = self.library.cells.get(ref.cell_name)
                if child is not None:
                    child.shapes = [sh for sh in child.shapes
                                    if not (isinstance(sh, Text) and sh.text == ref.label)]
            ref.label = ""
            ref.label_offset = (0, 0)
            self.selected = None
        self.redraw()
        self._after_edit()

    def smash_selected(self):
        """Explode the currently selected ARef back into individual
        SRefs, one per array position, and select the new SRefs as a
        window-selected group -- used by the SMASH command and Edit >
        Smash Selected to ARef. Returns True if something was smashed,
        False if the current selection isn't a single ARef (a
        window-selected group, a plain SRef/shape, or nothing)."""
        if self.draw_mode != "select":
            return False
        if self.selected is None or self.selected[0] != "aref":
            return False
        cell = self.library.cells.get(self.cell_name)
        if cell is None:
            return False
        _, ref = self.selected
        self.checkpoint()
        new_srefs = cell.smash_aref(ref)
        self.selected = None
        self.selected_multi = [("sref", s) for s in new_srefs]
        self._after_edit()
        return True

    def merge_selected(self):
        """Merge the window-selected group of same-layer Polygon/
        Rectangle shapes into a single Polygon on that layer, wherever
        they touch or overlap -- the MERGE command / Edit > Merge
        Selected.... Uses hlayout.core.maskops' shapely-backed union,
        the same machinery Tools > Mask Operations...' OR runs, just
        scoped to a hand-picked set of shapes instead of a whole
        layer.

        Returns True if the selection merged into one polygon. Returns
        False (no error, selection left alone) if the selection isn't
        at least two Polygon/Rectangle shapes -- a single shape, a
        vertex/edge, an SRef/ARef, or nothing at all. Raises
        GeometryError (left for the caller to show) if the shapes
        selected: are not all on the same layer; don't actually touch
        or overlap, so there's nothing to merge; or shapely isn't
        installed.

        A merge that would leave a hole (e.g. an annular selection)
        collapses that hole the same way every other mask-op result
        in this tool does -- see hlayout.core.maskops.shapely_to_polygons
        and its _close_holes() helper for why (this tool's Polygon
        shape, like GDSII's own boundary record, has no native
        "hole" concept)."""
        if self.draw_mode != "select":
            return False
        if len(self.selected_multi) < 2:
            return False
        cell = self.library.cells.get(self.cell_name)
        if cell is None:
            return False
        shapes = []
        for kind, shape in self.selected_multi:
            if kind != "shape" or not isinstance(shape, (Polygon, Rectangle)):
                return False
            shapes.append(shape)
        layers = {s.layer for s in shapes}
        if len(layers) != 1:
            raise GeometryError(
                "selected shapes are on different layers -- MERGE only "
                "combines shapes that are already on the same layer"
            )
        layer = shapes[0].layer
        datatype = shapes[0].datatype
        union = layer_to_shapely(shapes)
        if union.is_empty:
            return False
        if union.geom_type != "Polygon":
            raise GeometryError(
                "selected shapes don't touch or overlap -- nothing to merge "
                "(MERGE only combines shapes that share at least an edge or area)"
            )
        new_polys = shapely_to_polygons(union, layer, datatype=datatype)
        if not new_polys:
            return False
        self.checkpoint()
        for s in shapes:
            if s in cell.shapes:
                cell.shapes.remove(s)
        cell.shapes.extend(new_polys)
        self.selected = None
        # Exactly one polygon in practice (union.geom_type == "Polygon"
        # was just checked above), but select via the general list form
        # -- same convention smash_selected()/delete_selected() use for
        # a result that replaces one selection with a new group.
        self.selected_multi = [("shape", p) for p in new_polys]
        self._after_edit()
        return True

    def _selected_groupable_items(self):
        """The current selection as a list of ("shape"/"sref"/"aref",
        obj) tuples -- window group or single object, vertex/edge
        selections dropped. Shared by create_cell_from_selection() and
        start_cell_from_selection()."""
        items = list(self.selected_multi)
        if not items and self.selected is not None and self.selected[0] in ("shape", "sref", "aref"):
            items = [self.selected]
        return [it for it in items if it[0] in ("shape", "sref", "aref")]

    def start_cell_from_selection(self, name, replace=False):
        """Enter the click-to-set-origin step of Create Cell from
        Selection: stash the current selection, `name`, and whether an
        existing same-named cell should be replaced, then switch to
        "cell_origin" mode so the next canvas click picks the point that
        becomes the new cell's local (0, 0) -- rather than typing
        coordinates. Returns True if a valid selection was captured and
        the mode entered, False (nothing changed) if the selection can't
        become a cell. Esc (cancel_draw()) aborts and restores the
        selection; the click itself is handled in _on_click()."""
        if self.draw_mode != "select":
            return False
        items = self._selected_groupable_items()
        if not items:
            return False
        self._pending_cell_name = name
        self._pending_cell_items = items
        self._pending_cell_replace = replace
        self.set_mode("cell_origin")
        return True

    def create_cell_from_selection(self, name, origin, replace=False):
        """Make a new named cell out of the current selection -- draw or
        place geometry, window-select it, and turn it into a reusable
        library cell -- the classic "draw it, window it, name it, pick
        an origin, and it's a cell" Calma GDSII workflow (a bondpad or
        SMD land pattern is the typical use).

        Every selected shape/SRef/ARef is moved (not copied) out of the
        current cell and into a brand-new cell called `name`, with its
        geometry re-based so that `origin` (DBU, in the *current* cell's
        coordinates) becomes the new cell's local (0, 0) -- the same
        "pick the origin" step Calma's version has. To leave the design
        looking the same as before, the current cell then gets a fresh
        SRef instance of the new cell placed at `origin`, which becomes
        the new selection -- so this reads as "wrap the selection into
        a cell", not "delete what I had and make an empty cell".

        If a cell called `name` already exists: with replace=False this
        raises CellError (the caller offers a Replace prompt); with
        replace=True the existing cell's contents are cleared and
        rebuilt from the selection *in place* -- the Cell object is
        kept, so every SRef/ARef already pointing at `name` stays valid
        and just switches to the new geometry (updating a footprint
        definition everywhere it's used).

        Returns True if a cell was created/replaced. Returns False
        (selection left alone) if nothing suitable is selected -- a
        single vertex/edge, or nothing at all."""
        if self.draw_mode != "select":
            return False
        items = self._selected_groupable_items()
        if not items:
            return False
        cell = self.library.cells.get(self.cell_name)
        if cell is None:
            return False

        existing = self.library.cells.get(name)
        if existing is not None:
            if not replace:
                raise CellError(f"cell {name!r} already exists")
            if name == self.cell_name:
                raise CellError(
                    f"can't replace {name!r} -- it's the cell you're editing")

        # Cycle guard -- for BOTH new and replaced cells, checked before
        # any mutation so a rejected attempt leaves the design intact.
        # The wrapper cell `name` ends up placed inside the current cell
        # and containing every selected instance; neither hop may close
        # a loop in the cell hierarchy (which would later crash redraw /
        # save via cell_bbox()'s cycle detection).
        for kind, obj in items:
            if kind not in ("sref", "aref"):
                continue
            t = obj.cell_name
            if (t == name or t == self.cell_name
                    or self.library.would_create_cycle(self.cell_name, t)
                    or (name in self.library.cells
                        and self.library.would_create_cycle(name, t))):
                raise CellError(
                    f"that selection can't be wrapped into {name!r} -- the "
                    f"instance of {t!r} it contains would make the cell "
                    "hierarchy cyclic")

        self.checkpoint()
        if existing is not None:
            target = existing
            target.shapes.clear()
            target.srefs.clear()
            target.arefs.clear()
        else:
            target = self.library.new_cell(name)
        ox, oy = origin
        for kind, obj in items:
            source_list = {"shape": cell.shapes, "sref": cell.srefs, "aref": cell.arefs}[kind]
            dest_list = {"shape": target.shapes, "sref": target.srefs, "aref": target.arefs}[kind]
            if obj in source_list:
                source_list.remove(obj)
            snapshot = _geom_snapshot(kind, obj)
            _apply_translate(obj, snapshot, -ox, -oy)
            dest_list.append(obj)

        new_ref = cell.add_sref(name, origin)
        self.selected = ("sref", new_ref)
        self.selected_multi = []
        self._after_edit()
        return True

    def move_selected(self, dx, dy):
        """Move the current selection by (dx, dy) DBU. Works for a whole
        shape or cell instance ("shape"/"sref" selections), a single
        vertex or edge, or an entire window-selected group -- used by
        the MOVE command. Returns True if something was moved, False if
        nothing is selected."""
        if self.draw_mode != "select":
            return False
        if self.selected_multi:
            self.checkpoint()
            for kind, shape in self.selected_multi:
                snapshot = _geom_snapshot(kind, shape)
                _apply_translate(shape, snapshot, dx, dy)
            self._after_edit()
            return True
        if self.selected is None:
            return False
        self.checkpoint()
        kind = self.selected[0]
        if kind == "sref_label":
            _, ref = self.selected
            ox, oy = ref.label_offset
            ref.label_offset = (int(round(ox + dx)), int(round(oy + dy)))
        elif kind == "sref_reftext":
            _, ref = self.selected
            baked = self._refdes_baked_shape(ref)
            if baked is not None:
                ldx, ldy = self._parent_delta_to_local(ref, dx, dy)
                ox, oy = baked.origin
                baked.origin = (int(round(ox + ldx)), int(round(oy + ldy)))
        elif kind == "vertex":
            _, shape, idx = self.selected
            px, py = shape.points[idx]
            pts = list(shape.points)
            pts[idx] = (int(round(px + dx)), int(round(py + dy)))
            shape.points = pts
        elif kind == "edge":
            _, shape, (i, j) = self.selected
            pts = list(shape.points)
            pxi, pyi = pts[i]
            pxj, pyj = pts[j]
            pts[i] = (int(round(pxi + dx)), int(round(pyi + dy)))
            pts[j] = (int(round(pxj + dx)), int(round(pyj + dy)))
            shape.points = pts
        else:
            _, shape = self.selected
            snapshot = _geom_snapshot(kind, shape)
            _apply_translate(shape, snapshot, dx, dy)
        self._after_edit()
        return True

    def rotate_selected(self, angle_deg):
        """Rotate the current selection by angle_deg degrees (counter-
        clockwise, in DBU/world space) about its own origin/centroid.
        A "shape" selection (Polygon/Rectangle/Path) rotates its points
        about their centroid; a Text shape rotates about its origin by
        adjusting its .rotation field; an "sref" (cell instance) rotates
        about its origin by adjusting its .rotation field. Vertex/edge
        selections can't be meaningfully rotated on their own and are
        rejected. A window-selected group rotates each member about its
        own centroid/origin, not the group's combined centroid. Returns
        True if something was rotated, False otherwise."""
        if self.draw_mode != "select":
            return False
        if self.selected_multi:
            self.checkpoint()
            for kind, shape in self.selected_multi:
                if kind in ("sref", "aref") or isinstance(shape, (SRef, ARef)):
                    shape.rotation = (shape.rotation + angle_deg) % 360.0
                elif isinstance(shape, Text):
                    shape.rotation = (shape.rotation + angle_deg) % 360.0
                else:
                    shape.points = _apply_rotate(shape.points, angle_deg)
            self._after_edit()
            return True
        if self.selected is None:
            return False
        kind = self.selected[0]
        if kind in ("vertex", "edge", "sref_label", "sref_reftext"):
            self.on_mode_status("select a whole shape or cell instance to rotate")
            return False
        self.checkpoint()
        _, shape = self.selected
        if kind in ("sref", "aref") or isinstance(shape, (SRef, ARef)):
            shape.rotation = (shape.rotation + angle_deg) % 360.0
        elif isinstance(shape, Text):
            shape.rotation = (shape.rotation + angle_deg) % 360.0
        else:
            shape.points = _apply_rotate(shape.points, angle_deg)
        self._after_edit()
        return True

    def copy_selected(self, dx, dy):
        """Duplicate the current selection into the same cell, offsetting
        the new copy by (dx, dy) DBU from the original, and select the
        copy -- used by both the COPY command and the Edit > Copy...
        dialog. A single vertex/edge can't be meaningfully copied on its
        own and is rejected, same as rotate_selected(). A window-selected
        group is copied as a whole, offsetting and reselecting every
        copy. Returns True if something was copied, False if nothing is
        selected."""
        if self.draw_mode != "select":
            return False
        cell = self.library.cells.get(self.cell_name)
        if cell is None:
            return False
        if self.selected_multi:
            self.checkpoint()
            new_selection = []
            for kind, shape in self.selected_multi:
                new_shape = copy.deepcopy(shape)
                snapshot = _geom_snapshot(kind, new_shape)
                _apply_translate(new_shape, snapshot, dx, dy)
                if kind == "sref" or isinstance(new_shape, SRef):
                    cell.srefs.append(new_shape)
                    new_selection.append(("sref", new_shape))
                elif kind == "aref" or isinstance(new_shape, ARef):
                    cell.arefs.append(new_shape)
                    new_selection.append(("aref", new_shape))
                else:
                    cell.shapes.append(new_shape)
                    new_selection.append(("shape", new_shape))
            self.selected_multi = new_selection
            self._after_edit()
            return True
        if self.selected is None:
            return False
        kind = self.selected[0]
        if kind in ("vertex", "edge", "sref_label", "sref_reftext"):
            self.on_mode_status("select a whole shape or cell instance to copy")
            return False
        self.checkpoint()
        _, shape = self.selected
        new_shape = copy.deepcopy(shape)
        snapshot = _geom_snapshot(kind, new_shape)
        _apply_translate(new_shape, snapshot, dx, dy)
        if kind == "sref" or isinstance(new_shape, SRef):
            cell.srefs.append(new_shape)
            self.selected = ("sref", new_shape)
        elif kind == "aref" or isinstance(new_shape, ARef):
            cell.arefs.append(new_shape)
            self.selected = ("aref", new_shape)
        else:
            cell.shapes.append(new_shape)
            self.selected = ("shape", new_shape)
        self._after_edit()
        return True

    def _after_edit(self):
        self.redraw()
        if self.on_edit:
            self.on_edit()
        # Properties (layer/width/etc.) can change even when the
        # selection object itself doesn't (Edit Selected... changing a
        # path's width, a drag moving a shape's coordinates, ...), so
        # the Properties panel needs a refresh here too, not just when
        # the selection changes.
        self.on_select()

    # --- rubber-band preview ---------------------------------------------
    def _update_preview(self, mouse_x, mouse_y):
        self.delete("preview")
        if not self._pending_points:
            return
        if self.draw_mode == "rect" and len(self._pending_points) == 1:
            x0, y0 = self._pending_points[0]
            p0 = self.to_px(x0, y0)
            p1 = self.to_px(mouse_x, mouse_y)
            self.create_rectangle(*p0, *p1, outline="#00d0ff", dash=(3, 2), tags="preview")
        elif self.draw_mode == "circle" and len(self._pending_points) == 1:
            cx, cy = self._pending_points[0]
            p0 = self.to_px(cx, cy)
            dx, dy = mouse_x - cx, mouse_y - cy
            r_px = ((dx * dx + dy * dy) ** 0.5) * self.scale
            self.create_oval(p0[0] - r_px, p0[1] - r_px, p0[0] + r_px, p0[1] + r_px,
                              outline="#00d0ff", dash=(3, 2), tags="preview")
            self.create_oval(p0[0] - 2, p0[1] - 2, p0[0] + 2, p0[1] + 2, fill="#00d0ff",
                              outline="", tags="preview")
        elif self.draw_mode == "measure" and len(self._pending_points) == 1:
            x0, y0 = self._pending_points[0]
            self._draw_measure_overlay(x0, y0, mouse_x, mouse_y, tag="preview")
        elif self.draw_mode == "wire_bond" and len(self._pending_points) == 1:
            x0, y0 = self._pending_points[0]
            self._draw_wire_bond_overlay(x0, y0, mouse_x, mouse_y, tag="preview")
        elif self.draw_mode in ("poly", "path", "route"):
            coords = []
            for p in self._pending_points:
                coords += list(self.to_px(*p))
            coords += list(self.to_px(mouse_x, mouse_y))

            if self.draw_mode in ("path", "route"):
                # A "ghost" of the actual path width — a faint thick line
                # under the thin centreline — so you can see how wide the
                # path will really be before you commit it. Route uses
                # its own currently-active conductor layer, not the
                # regular draw tools' current_layer.
                width_px = max(1, self.path_width * self.scale)
                cap = tk.ROUND if self.path_cap == "round" else (
                    tk.PROJECTING if self.path_cap == "square" else tk.BUTT)
                layer_n = self._route_active_layer if self.draw_mode == "route" else self.current_layer
                layer = self.library.layers[layer_n]
                ghost_stipple = "gray50" if width_px >= 3 else ""
                self.create_line(*coords, fill=layer.color, width=width_px,
                                  capstyle=cap, joinstyle=tk.ROUND,
                                  stipple=ghost_stipple, tags="preview")

            self.create_line(*coords, fill="#00d0ff", dash=(3, 2), tags="preview")
            for p in self._pending_points:
                px, py = self.to_px(*p)
                self.create_oval(px - 2, py - 2, px + 2, py + 2, fill="#00d0ff",
                                  outline="", tags="preview")

    def _format_distance(self, x0, y0, x1, y1):
        dx, dy = x1 - x0, y1 - y0
        dist = (dx * dx + dy * dy) ** 0.5
        return (f"{dbu_to_mm(dist):.3f} mm  (dx={dbu_to_mm(dx):.3f}, dy={dbu_to_mm(dy):.3f})")

    def _draw_measure_overlay(self, x0, y0, x1, y1, tag):
        """Draw a Measure tool line from (x0, y0) to (x1, y1) DBU plus a
        distance label at its midpoint, tagged `tag` -- "preview" for
        the live line that follows the mouse after the first click (so
        it's cleared/redrawn like every other rubber-band preview), or
        "measure_result" for the final line left on screen once the
        second click lands (cleared by cancel_draw()/the next
        measurement's first click, or any redraw() -- see
        _on_click()'s "measure" branch and cancel_draw())."""
        p0 = self.to_px(x0, y0)
        p1 = self.to_px(x1, y1)
        self.create_line(*p0, *p1, fill="#00d0ff", dash=(3, 2), width=1.5, tags=tag)
        r = self.MEASURE_POINT_RADIUS
        for px, py in (p0, p1):
            self.create_oval(px - r, py - r, px + r, py + r, outline="#00d0ff",
                              width=2, fill="#0d1117", tags=tag)
        label = self._format_distance(x0, y0, x1, y1)
        mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2 - 12
        # A small backing rectangle behind the text so the label stays
        # legible over the design/grid underneath it -- text_bbox needs
        # the text item to already exist to measure it, so create the
        # text first, size the rectangle to its bbox, then raise the
        # text back above the rectangle.
        text_id = self.create_text(mx, my, text=label, fill="white",
                                    font=("TkDefaultFont", 9), tags=tag)
        bbox = self.bbox(text_id)
        if bbox:
            x0b, y0b, x1b, y1b = bbox
            self.create_rectangle(x0b - 3, y0b - 1, x1b + 3, y1b + 1,
                                   fill="#0d1117", outline="#00d0ff", tags=tag)
            self.tag_raise(text_id)

    def _draw_wire_bond_overlay(self, x0, y0, x1, y1, tag):
        """Rubber-band for the Wire Bond tool after the first click: an
        ``O------O`` figure -- a dashed line with a ring at each end --
        plus the span length, so you can see it against the bond rules'
        min/max before the second click commits it."""
        p0 = self.to_px(x0, y0)
        p1 = self.to_px(x1, y1)
        self.create_line(*p0, *p1, fill="#ffb020", dash=(4, 3), width=1.5, tags=tag)
        r = 5
        for px, py in (p0, p1):
            self.create_oval(px - r, py - r, px + r, py + r, outline="#ffb020",
                              width=2, fill="#0d1117", tags=tag)
        label = self._format_distance(x0, y0, x1, y1)
        mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2 - 12
        text_id = self.create_text(mx, my, text=label, fill="white",
                                    font=("TkDefaultFont", 9), tags=tag)
        bbox = self.bbox(text_id)
        if bbox:
            x0b, y0b, x1b, y1b = bbox
            self.create_rectangle(x0b - 3, y0b - 1, x1b + 3, y1b + 1,
                                   fill="#0d1117", outline="#ffb020", tags=tag)
            self.tag_raise(text_id)

    def _commit_wire_bond(self):
        p0, p1 = self._pending_points
        self._pending_points = []
        self.delete("preview")
        if self.on_commit:
            self.on_commit("wire_bond", self.current_layer, [p0, p1], {})
        self.on_mode_status(self._wire_bond_hint())

    def _commit_measure(self):
        (x0, y0), (x1, y1) = self._pending_points
        self._pending_points = []
        self.delete("preview")
        self.delete("measure_result")
        self._draw_measure_overlay(x0, y0, x1, y1, tag="measure_result")
        self.on_mode_status(f"Measure: distance = {self._format_distance(x0, y0, x1, y1)} "
                             f"-- click a new start point to measure again")

    # --- commits: turn pending points into a shape via on_commit ---------
    def _commit_rect(self):
        points = self._pending_points
        self._pending_points = []
        self.delete("preview")
        if self.on_commit:
            self.on_commit("rect", self.current_layer, points, {})

    def _commit_circle(self):
        center, edge = self._pending_points
        self._pending_points = []
        self.delete("preview")
        dx, dy = edge[0] - center[0], edge[1] - center[1]
        radius = max(1, int(round((dx * dx + dy * dy) ** 0.5)))
        if self.on_commit:
            self.on_commit("circle", self.current_layer, [center],
                            {"radius": radius, "segments": self.circle_segments})

    def _commit_poly(self):
        points = self._pending_points
        if len(points) < 3:
            self.on_mode_status("polygon needs at least 3 points")
            return
        self._pending_points = []
        self.delete("preview")
        if self.on_commit:
            self.on_commit("poly", self.current_layer, points, {})

    def _commit_path(self):
        points = self._pending_points
        if len(points) < 2:
            self.on_mode_status("path needs at least 2 points")
            return
        self._pending_points = []
        self.delete("preview")
        target = self._continue_target
        self._continue_target = None
        if target is not None:
            # continue_path() in progress: reuse the original shape's own
            # layer/width/cap (not whatever the toolbar currently shows —
            # this may not be the active draw layer at all) and tell
            # on_commit which shape this new one replaces.
            layer = target.layer
            extra = {"width": target.width,
                     "cap": target.cap.value if hasattr(target.cap, "value") else target.cap,
                     "replace": target}
        else:
            layer = self.current_layer
            extra = {"width": self.path_width, "cap": self.path_cap, "replace": None}
        if self.on_commit:
            self.on_commit("path", layer, points, extra)

    def _commit_route(self):
        """Finish the whole in-progress route (Enter/double-click): commit
        whatever segment is still pending on the active conductor layer
        (if it has at least 2 points -- a lone start point with no
        second click yet commits nothing) and reset back to a fresh
        route starting on route_layer_a next time. Unlike a Space-drop,
        no via is placed here -- this simply ends the route."""
        points = self._pending_points
        self._pending_points = []
        self.delete("preview")
        if len(points) >= 2 and self.on_commit:
            self.on_commit("path", self._route_active_layer, points,
                            {"width": self.path_width, "cap": self.path_cap})
        self._route_active_layer = self.route_layer_a
        self.on_mode_status(self._route_hint())

    def _route_drop_via(self, event=None):
        """Space, while routing: commit the segment drawn so far on the
        active conductor layer (if any) *and* drop a via at its last
        point connecting route_layer_a/route_layer_b as one atomic step
        (see on_route_via -- the app wraps both in a single
        Session.batch(), so one Ctrl+Z undoes the whole space-press),
        then continue routing from that same point on the *other*
        conductor layer. Returns "break" so Tk doesn't also treat Space
        as e.g. scrolling the canvas."""
        if self.draw_mode != "route":
            return None
        if not self._pending_points:
            self.on_mode_status("Route: click a start point before dropping a via (Space)")
            return "break"
        last = self._pending_points[-1]
        segment = None
        if len(self._pending_points) >= 2:
            segment = (self._route_active_layer, list(self._pending_points),
                       self.path_width, self.path_cap)
        self.on_route_via(last[0], last[1], self.route_layer_a, self.route_layer_b,
                           self.route_via_cell, segment=segment)
        self._route_active_layer = (
            self.route_layer_b if self._route_active_layer == self.route_layer_a
            else self.route_layer_a
        )
        self._pending_points = [last]
        self.delete("preview")
        self._update_preview(*last)
        self.on_mode_status(self._route_hint())
        return "break"

    def _prompt_text(self):
        pt = self._pending_points[0]
        self._pending_points = []
        text = simpledialog.askstring("Add Text", "Text string:", parent=self)
        if not text:
            return
        height_mm = simpledialog.askfloat("Add Text", "Height (mm):", parent=self,
                                           initialvalue=dbu_to_mm(10), minvalue=dbu_to_mm(1))
        height = mm_to_dbu(height_mm) if height_mm is not None else 10
        if self.on_commit:
            self.on_commit("text", self.current_layer, [pt], {"text": text, "height": height, "font": 0})

    def _prompt_sref(self):
        pt = self._pending_points[0]
        self._pending_points = []
        # Include attached-library cells, not just ones already in the
        # current design -- picking one imports its dependency closure
        # first (see App._on_canvas_commit's "sref" branch, which places
        # via session.place_cell() rather than the lower-level
        # session.sref()).
        if self.on_list_cells is not None:
            names = sorted(set(self.on_list_cells()))
        else:
            names = sorted(self.library.cells.keys())
        if not names:
            self.on_mode_status("no cells to reference yet")
            return
        cell_name = _choose_from_list(self, "Add Cell Instance", "Cell to place:", names)
        if not cell_name:
            return
        # Placed unrotated -- no rotation prompt. Rotate afterwards with
        # the Rotate command / R key, or Edit Selected... for an exact
        # angle/magnification/mirror.
        if self.on_commit:
            self.on_commit("sref", self.current_layer, [pt],
                            {"cell_name": cell_name, "rotation": 0.0,
                             "magnification": 1.0, "x_reflection": False})

    def _prompt_aref(self):
        """Create > Array Instance: click the placement point, choose the
        cell to repeat (same picker as Cell Instance), then fill in the
        array's columns/rows/spacing/rotation/mirror in one dialog (see
        _prompt_aref_params). Mirrors _prompt_sref, but places an ARef
        (see hlayout.core.shapes.ARef) instead of a plain SRef -- use
        SMASH (Edit > Smash Selected to SRefs, or the SMASH command) to
        explode the placed array back into individual cell instances."""
        pt = self._pending_points[0]
        self._pending_points = []
        if self.on_list_cells is not None:
            names = sorted(set(self.on_list_cells()))
        else:
            names = sorted(self.library.cells.keys())
        if not names:
            self.on_mode_status("no cells to reference yet")
            return
        cell_name = _choose_from_list(self, "Add Array Instance", "Cell to place:", names)
        if not cell_name:
            return
        params = _prompt_aref_params(self)
        if params is None:
            return
        if self.on_commit:
            self.on_commit("aref", self.current_layer, [pt],
                            {"cell_name": cell_name, **params})

    # --- drawing --------------------------------------------------------
    def redraw(self):
        self.delete("all")
        self._item_to_shape = {}
        # Whether the per-instance ref-des annotation (SRef.label float)
        # should be drawn this pass -- it's tied to the technology's
        # ref-des annotation layer's visibility (see
        # _refdes_annotation_layer_visible). Computed once per redraw.
        self._refdes_anno_visible = self._refdes_annotation_layer_visible()
        if self.show_grid:
            self._draw_grid()
        if self.cell_name and self.cell_name in self.library.cells:
            self._draw_cell(self.cell_name, ox=0, oy=0, rot=0, mag=1, mirror=False, depth=0)
        self._draw_selection_highlight()

    def _draw_grid(self):
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            return
        minor_px = self.grid_dbu * self.scale
        if minor_px < 3:
            return  # too dense; skip to avoid melting the canvas
        x0, y0 = self.to_dbu(0, 0)
        x1, y1 = self.to_dbu(w, h)

        # Pad the drawn area beyond the viewport so a short pan
        # (translated in place via move(), see _pan_move_evt) doesn't
        # reveal a blank gap at the edge before the next full redraw
        # regenerates the grid around the new position.
        pad_x = (x1 - x0) * 0.25
        pad_y = (y0 - y1) * 0.25
        gx0 = int((x0 - pad_x) // self.grid_dbu) - 1
        gx1 = int((x1 + pad_x) // self.grid_dbu) + 1
        gy0 = int((y1 - pad_y) // self.grid_dbu) - 1
        gy1 = int((y0 + pad_y) // self.grid_dbu) + 1

        # Every minor-grid intersection in the (padded) viewport gets a
        # dot -- no thinning/skipping. At zoom levels far enough out,
        # that can be tens of thousands of points (bounded by the
        # minor_px < 3 cutoff above, so at most roughly one dot per
        # 3x3 screen pixels), which is far too many individual
        # create_oval() canvas items to create every redraw without
        # stalling the UI. So the whole grid is rasterized into a
        # single PhotoImage instead -- one canvas item, however many
        # dots it contains. See _draw_grid_raster().
        self._draw_grid_raster(w, h, gx0, gx1, gy0, gy1)

    def _draw_grid_raster(self, w, h, gx0, gx1, gy0, gy1):
        """Render every grid dot in [gx0, gx1] x [gy0, gy1] (grid-index
        space) into one PhotoImage covering the viewport, then place it
        as a single create_image() canvas item. Uses numpy when it's
        installed, to fill the whole dot set with a few vectorized
        array ops (effectively O(1) canvas/Tcl calls regardless of dot
        count); falls back to a pure-Tk PhotoImage.put() path -- still
        one canvas item, just built with a Python loop -- when numpy
        isn't available, matching the lazy numpy import used by the
        Trim Simulator elsewhere in this GUI (see requirements.txt)."""
        bg = self.cget("bg") or "#1e1e1e"
        minor_color = "#3a3a3a"
        major_color = "#666666"

        try:
            import numpy as np
        except ImportError:
            np = None

        if np is not None:
            img = self._raster_grid_numpy(np, w, h, gx0, gx1, gy0, gy1,
                                           bg, minor_color, major_color)
        else:
            img = self._raster_grid_pure(w, h, gx0, gx1, gy0, gy1,
                                          bg, minor_color, major_color)
        if img is None:
            return
        # Keep a reference -- Tk drops/blank the image if the Python
        # PhotoImage object is garbage-collected, even while it's still
        # showing on the canvas.
        self._grid_image = img
        self.create_image(0, 0, anchor="nw", image=img, tags=("grid",))

    def _raster_grid_numpy(self, np, w, h, gx0, gx1, gy0, gy1,
                            bg, minor_color, major_color):
        bg_rgb = self._hex_to_rgb(bg)
        minor_rgb = self._hex_to_rgb(minor_color)
        major_rgb = self._hex_to_rgb(major_color)

        arr = np.empty((h, w, 3), dtype=np.uint8)
        arr[:, :] = bg_rgb

        cols = np.arange(gx0, gx1 + 1)
        rows = np.arange(gy0, gy1 + 1)
        px = np.round(cols * self.grid_dbu * self.scale + self.offset_x).astype(np.int64)
        py = np.round(-rows * self.grid_dbu * self.scale + self.offset_y).astype(np.int64)

        col_ok = (px >= 0) & (px < w)
        row_ok = (py >= 0) & (py < h)
        px, cols = px[col_ok], cols[col_ok]
        py, rows = py[row_ok], rows[row_ok]
        if px.size == 0 or py.size == 0:
            return self._to_photoimage(arr)

        # A dot is "major" only at a true major-grid intersection --
        # column AND row both on a major line -- not just whichever
        # column it's in. Conditioning on the column alone (as an
        # earlier version of this did) made every dot the whole way
        # down a major column render big, so the vertical axis showed
        # nothing but major-looking dots.
        col_major = (cols % self.GRID_MAJOR_EVERY) == 0
        row_major = (rows % self.GRID_MAJOR_EVERY) == 0
        Y, X = np.meshgrid(py, px, indexing="ij")  # both shape (rows, cols)
        is_major = row_major[:, None] & col_major[None, :]

        # Stamp each dot as a (2r+1) x (2r+1) square blob around its
        # exact pixel, minor dots first, then major dots on top (so a
        # major dot's larger blob always wins where the two overlap).
        min_y, min_x = Y[~is_major], X[~is_major]
        r = self.GRID_MINOR_DOT_R
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                yy = np.clip(min_y + dy, 0, h - 1)
                xx = np.clip(min_x + dx, 0, w - 1)
                arr[yy, xx] = minor_rgb

        maj_y, maj_x = Y[is_major], X[is_major]
        r = self.GRID_MAJOR_DOT_R
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                yy = np.clip(maj_y + dy, 0, h - 1)
                xx = np.clip(maj_x + dx, 0, w - 1)
                arr[yy, xx] = major_rgb

        return self._to_photoimage(arr)

    def _raster_grid_pure(self, w, h, gx0, gx1, gy0, gy1,
                           bg, minor_color, major_color):
        # master=self pins the image to THIS widget's Tk interpreter --
        # without it a PhotoImage binds to tk._default_root, which throws
        # `image "pyimageN" doesn't exist` on create_image() if anything
        # ever created a second Tk root (e.g. a mis-set-up test harness).
        img = tk.PhotoImage(master=self, width=w, height=h)
        img.put(bg, to=(0, 0, w, h))
        for i in range(gx0, gx1 + 1):
            px, _ = self.to_px(i * self.grid_dbu, 0)
            px = int(round(px))
            if px < 0 or px >= w:
                continue
            col_major = (i % self.GRID_MAJOR_EVERY) == 0
            for j in range(gy0, gy1 + 1):
                _, py = self.to_px(0, j * self.grid_dbu)
                py = int(round(py))
                if py < 0 or py >= h:
                    continue
                # Major only at a true intersection -- both the column
                # and the row on a major line -- see the numpy path's
                # comment above for why the row also has to match.
                major = col_major and (j % self.GRID_MAJOR_EVERY) == 0
                color = major_color if major else minor_color
                r = self.GRID_MAJOR_DOT_R if major else self.GRID_MINOR_DOT_R
                img.put(color, to=(px - r, py - r, px + r + 1, py + r + 1))
        return img

    @staticmethod
    def _hex_to_rgb(hex_color):
        hex_color = hex_color.lstrip("#")
        return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))

    def _to_photoimage(self, arr):
        h, w = arr.shape[:2]
        header = ("P6 %d %d 255 " % (w, h)).encode("ascii")
        # master=self -- see _raster_grid_pure() for why this matters.
        return tk.PhotoImage(master=self, width=w, height=h,
                             data=header + arr.tobytes(), format="PPM")

    def _draw_cell(self, name, ox, oy, rot, mag, mirror, depth):
        if depth > 64:
            return  # guard against runaway/cyclic hierarchies
        cell = self.library.cells[name]

        # The shape or cell instance currently selected (kind "shape",
        # "vertex", or "edge" all point at a single Polygon/Path/
        # Rectangle/Text object; "sref" points at an SRef) is redrawn in
        # SELECTED_COLOR instead of its layer's colour, so it's visually
        # unambiguous what MOVE/ROTATE (and dragging) would act on.
        selected_shape = None
        selected_ref = None
        selected_aref = None
        if self.selected:
            kind = self.selected[0]
            if kind in ("shape", "vertex", "edge"):
                selected_shape = self.selected[1]
            elif kind == "sref":
                selected_ref = self.selected[1]
            elif kind == "aref":
                selected_aref = self.selected[1]
        multi_shape_ids = {id(s) for k, s in self.selected_multi if k == "shape"}
        multi_ref_ids = {id(s) for k, s in self.selected_multi if k == "sref"}
        multi_aref_ids = {id(s) for k, s in self.selected_multi if k == "aref"}

        # Draw in ascending layer order (0-63) regardless of the order
        # shapes were created/loaded in, so layer stacking on screen is
        # always consistent -- e.g. a layer-0 ground plane never
        # visually covers a layer-5 trace just because it happened to
        # be added to the cell more recently. sorted() is stable, so
        # shapes sharing a layer keep their original relative order.
        for shape in sorted(cell.shapes, key=lambda s: s.layer):
            layer = self.library.layers[shape.layer]
            if not layer.visible:
                continue
            is_selected = (shape is selected_shape) or (id(shape) in multi_shape_ids)
            item = self._draw_shape(shape, layer, selected=is_selected)
            if item is not None:
                self._item_to_shape[item] = ("shape", shape)
            if (self.show_pin_numbers and isinstance(shape, (Polygon, Rectangle))
                    and getattr(shape, "pin", "")):
                self._draw_pin_label(shape)
        for ref in cell.srefs:
            # Recursive draw with the ref's transform folded in (flattened
            # rendering; good enough for the visual editor).
            highlight = (ref is selected_ref) or (id(ref) in multi_ref_ids)
            self._draw_sref(ref, depth, highlight=highlight)
            # A small selectable handle at the instance origin, since the
            # instance itself has no single canvas item of its own.
            hx, hy = self.to_px(*ref.origin)
            handle_color = self.SELECTED_COLOR if highlight else "#ff66ff"
            handle = self.create_rectangle(hx - 4, hy - 4, hx + 4, hy + 4,
                                            outline=handle_color, fill="",
                                            width=2 if highlight else 1)
            self._item_to_shape[handle] = ("sref", ref)
            if ref.label and getattr(self, "_refdes_anno_visible", True):
                baked = self._refdes_baked_shape(ref)
                if baked is None:
                    # Per-instance floating ref-des (SRef.label) -- drawn
                    # every redraw at the instance's bbox CENTRE plus
                    # ref.label_offset, so it tracks the part and can be
                    # dragged clear of it without moving the part
                    # (kind "sref_label"). Fixed on-screen px size
                    # (ref.label_height, not zoom-scaled) with a backing
                    # rectangle, same as the Measure tool's label.
                    ax, ay = self._sref_label_anchor(ref)
                    lx, ly = self.to_px(ax, ay)
                    label_color = self.SELECTED_COLOR if highlight else "#ffdd55"
                    label_item = self.create_text(lx, ly, text=ref.label, fill=label_color,
                                                   font=self._get_font(ref.label_height),
                                                   anchor="center")
                    bbox = self.bbox(label_item)
                    if bbox:
                        x0b, y0b, x1b, y1b = bbox
                        backing = self.create_rectangle(x0b - 2, y0b - 1, x1b + 2, y1b + 1,
                                                         fill="#0d1117", outline=label_color)
                        self.tag_raise(label_item)
                        self._item_to_shape[backing] = ("sref_label", ref)
                    self._item_to_shape[label_item] = ("sref_label", ref)
                else:
                    # The ref-des is a real Text shape baked on its
                    # assigned layer inside the instance's own cell
                    # (Session.generate_resistor()). Draw a drag handle
                    # over it (kind "sref_reftext") so it can be
                    # repositioned for this one instance without entering
                    # the cell -- dragging translates the Text's origin
                    # in that cell.
                    tf = self._sref_transform(ref)
                    tx0, ty0, tx1, ty1 = baked.bbox()
                    corners = [tf((tx0, ty0)), tf((tx1, ty0)), tf((tx1, ty1)), tf((tx0, ty1))]
                    xs = [self.to_px(*c)[0] for c in corners]
                    ys = [self.to_px(*c)[1] for c in corners]
                    hx0, hy0, hx1, hy1 = min(xs), min(ys), max(xs), max(ys)
                    hcol = self.SELECTED_COLOR if highlight else "#ffdd55"
                    hitem = self.create_rectangle(hx0 - 2, hy0 - 2, hx1 + 2, hy1 + 2,
                                                   outline=hcol, dash=(2, 2),
                                                   width=2 if highlight else 1)
                    self._item_to_shape[hitem] = ("sref_reftext", ref)
        for aref in cell.arefs:
            highlight = (aref is selected_aref) or (id(aref) in multi_aref_ids)
            self._draw_aref(aref, depth, highlight=highlight)
            # One selectable handle at the array's own origin (position
            # 0,0 of the grid) -- the whole ARef is one selection/move/
            # rotate/delete/smash unit, not per-instance.
            hx, hy = self.to_px(*aref.origin)
            handle_color = self.SELECTED_COLOR if highlight else "#ff66ff"
            handle = self.create_rectangle(hx - 5, hy - 5, hx + 5, hy + 5,
                                            outline=handle_color, fill="",
                                            width=2 if highlight else 1)
            self._item_to_shape[handle] = ("aref", aref)

    def _refdes_annotation_layer_visible(self) -> bool:
        """Whether the per-instance ref-des annotation (SRef.label,
        floated at the instance origin) should be drawn this pass.

        If the technology names a ref-des annotation layer, the
        annotation follows THAT layer's visibility, so every
        ref-designator hides together with the rest of the ref-des
        documentation. If none is named (-1, the default) the
        annotation is still shown -- EXCEPT when every layer is hidden
        ("All Off"), where the expectation is a blank canvas rather
        than these labels floating on nothing."""
        n = load_refdes_annotation_layer(self.library)
        if n is not None and n >= 0:
            try:
                return bool(self.library.layers[n].visible)
            except (KeyError, IndexError):
                pass
        return any(layer.visible for layer in self.library.layers)

    def _refdes_baked_shape(self, ref: SRef):
        """The real Text shape inside `ref`'s cell that carries this
        instance's exact label (its ref-des baked on the assigned layer,
        as Session.generate_resistor() does), or None. When it exists the
        per-instance floating annotation is suppressed -- the ref-des
        shows only on its assigned layer -- and the baked Text gets a
        drag handle instead (kind "sref_reftext"). A cell that only bakes
        a prefix *placeholder* ("C" for "C1") has no exact-match Text, so
        its floating "C1" still shows."""
        if not ref.label:
            return None
        child = self.library.cells.get(ref.cell_name)
        if child is None:
            return None
        for sh in child.shapes:
            if isinstance(sh, Text) and sh.text == ref.label:
                return sh
        return None

    def _refdes_baked_into_cell(self, ref: SRef) -> bool:
        return self._refdes_baked_shape(ref) is not None

    def _sref_label_anchor(self, ref: SRef):
        """Default DBU position of `ref`'s floated label: the centre of
        the instance's bounding box, shifted by ref.label_offset. Falls
        back to the instance origin if the cell's bbox can't be taken."""
        box = self._cell_bbox_safe(ref.cell_name)
        if box is not None:
            from ..core.library import _transform_bbox
            x1, y1, x2, y2 = _transform_bbox(box, ref)
            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
        else:
            cx, cy = ref.origin
        ox, oy = ref.label_offset
        return (cx + ox, cy + oy)

    def _draw_aref(self, ref: "ARef", depth, highlight=False):
        """Draw every array position by delegating to _draw_sref() with a
        synthetic per-instance SRef -- reuses the exact same recursive
        flatten/transform logic an ordinary cell instance uses, just
        called once per array position."""
        for origin in ref.instance_origins():
            instance = SRef(cell_name=ref.cell_name, origin=origin, rotation=ref.rotation,
                             magnification=ref.magnification, x_reflection=ref.x_reflection)
            self._draw_sref(instance, depth, highlight=highlight)

    @staticmethod
    def _sref_transform(ref: SRef):
        """Return a function mapping a point in `ref`'s cell to the
        parent cell's coordinates (mirror, then scale, then rotate, then
        translate -- GDSII SREF order)."""
        theta = math.radians(ref.rotation)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        mag = ref.magnification
        ox, oy = ref.origin

        def transform(pt):
            x, y = pt
            if ref.x_reflection:
                y = -y
            x *= mag
            y *= mag
            return (x * cos_t - y * sin_t + ox, x * sin_t + y * cos_t + oy)

        return transform

    @staticmethod
    def _parent_delta_to_local(ref: SRef, dx, dy):
        """Convert a translation in the parent cell to the equivalent
        translation inside `ref`'s cell (inverse of the SREF linear
        part: un-rotate, un-scale, un-mirror)."""
        theta = math.radians(ref.rotation)
        c, s = math.cos(theta), math.sin(theta)
        ux = (dx * c + dy * s) / ref.magnification
        uy = (-dx * s + dy * c) / ref.magnification
        if ref.x_reflection:
            uy = -uy
        return ux, uy

    def _draw_sref(self, ref: SRef, depth, highlight=False):
        transform = self._sref_transform(ref)

        if ref.cell_name not in self.library.cells:
            return
        child = self.library.cells[ref.cell_name]
        for shape in sorted(child.shapes, key=lambda s: s.layer):
            layer = self.library.layers[shape.layer]
            if not layer.visible:
                continue
            item = self._draw_shape(shape, layer, transform=transform, selected=highlight)
            # Register every bit of the instance's own drawn geometry
            # (not just the small origin handle) as a click target for
            # the instance itself -- so clicking anywhere on a placed
            # part's visible body (e.g. a resistor's ink/pads) selects
            # and can drag the whole instance, the way clicking a
            # directly-drawn shape already does.
            if item is not None:
                self._item_to_shape[item] = ("sref", ref)
        for sub in child.srefs:
            # compose transforms by drawing the nested sref through a
            # temporary shim shape list (simple recursive flatten);
            # top_ref is the OUTER placed instance -- clicking anything
            # inside a nested sub-instance still selects/drags that
            # outer instance, since the nested one isn't independently
            # placed in the current cell.
            self._draw_nested_sref(sub, transform, depth + 1, highlight=highlight, top_ref=ref)

    def _draw_nested_sref(self, ref: SRef, parent_transform, depth, highlight=False, top_ref=None):
        if depth > 64:
            return
        theta = math.radians(ref.rotation)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        mag = ref.magnification
        ox, oy = ref.origin

        def transform(pt):
            x, y = pt
            if ref.x_reflection:
                y = -y
            x *= mag
            y *= mag
            xr = x * cos_t - y * sin_t + ox
            yr = x * sin_t + y * cos_t + oy
            return parent_transform((xr, yr))

        if ref.cell_name not in self.library.cells:
            return
        click_ref = top_ref if top_ref is not None else ref
        child = self.library.cells[ref.cell_name]
        for shape in sorted(child.shapes, key=lambda s: s.layer):
            layer = self.library.layers[shape.layer]
            if not layer.visible:
                continue
            item = self._draw_shape(shape, layer, transform=transform, selected=highlight)
            if item is not None:
                self._item_to_shape[item] = ("sref", click_ref)
        for sub in child.srefs:
            self._draw_nested_sref(sub, transform, depth + 1, highlight=highlight, top_ref=click_ref)

    def _draw_shape(self, shape, layer, transform=None, selected=False):
        t = transform or (lambda p: p)
        # A selected shape is drawn in flat white (SELECTED_COLOR) instead
        # of its layer's own colour, so it's unambiguous which object
        # MOVE/ROTATE (or a mouse drag) would act on. For a Polygon/
        # Rectangle this is an outline box only -- never a solid white
        # fill, even on a filled/stippled layer, so a large selected shape
        # doesn't blot out everything underneath it.
        color = self.SELECTED_COLOR if selected else layer.color
        stipple = None if selected else _STIPPLE_BITMAPS.get(layer.stipple)
        item = None

        if isinstance(shape, (Polygon, Rectangle)):
            coords = []
            for p in shape.points:
                px, py = self.to_px(*t(p))
                coords += [px, py]
            if selected:
                item = self.create_polygon(coords, fill="", outline=color, width=2)
            elif layer.fill:
                item = self.create_polygon(coords, fill=color, outline=color,
                                            stipple=stipple if stipple else "", width=1)
            else:
                # No fill: outline only, no interior stipple/fill colour.
                item = self.create_polygon(coords, fill="", outline=color, width=2)
        elif isinstance(shape, Path):
            coords = []
            for p in shape.points:
                px, py = self.to_px(*t(p))
                coords += [px, py]
            width_px = max(1, shape.width * self.scale)
            cap = tk.ROUND if shape.cap == PathCap.ROUND else (
                tk.PROJECTING if shape.cap == PathCap.SQUARE else tk.BUTT)
            item = self.create_line(*coords, fill=color, width=width_px,
                                     capstyle=cap, joinstyle=tk.ROUND)
        elif isinstance(shape, Text):
            px, py = self.to_px(*t(shape.origin))
            size = max(6, int(shape.height * self.scale))
            fnt = self._get_font(size)
            # Tk's create_text angle= is measured counter-clockwise in
            # screen space; to_px() flips Y relative to DBU space, so a
            # positive shape.rotation (CCW in DBU/world space, matching the
            # convention used for SRef rotation) needs its sign flipped
            # here to still look CCW on screen.
            item = self.create_text(px, py, text=shape.text, fill=color,
                                     font=fnt, anchor="sw", angle=-shape.rotation)
        return item

    def _get_font(self, size):
        key = size
        if key not in self._font_cache:
            self._font_cache[key] = tkfont.Font(family="Courier", size=size)
        return self._font_cache[key]

    def _draw_pin_label(self, shape):
        """Stamp a Rectangle/Polygon's pin string at its centre, on a
        small dark chip so it reads over any layer colour -- the visual
        aid for the Number Pins tool. Skipped when the pad is smaller on
        screen than the label, so a tiny pad is never buried; the label
        and its chip both pick the same pad when clicked (so you can
        click the number itself to renumber it)."""
        xs = [p[0] for p in shape.points]
        ys = [p[1] for p in shape.points]
        x0, y0 = self.to_px(min(xs), min(ys))
        x1, y1 = self.to_px(max(xs), max(ys))
        w, h = abs(x1 - x0), abs(y1 - y0)
        if w < 15 or h < 13:
            return
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        size = max(7, min(12, int(min(w, h) * 0.45)))
        label = self.create_text(cx, cy, text=shape.pin, fill="#7fdfff",
                                  font=self._get_font(size), anchor="c")
        bbox = self.bbox(label)
        if bbox:
            bx0, by0, bx1, by1 = bbox
            chip = self.create_rectangle(bx0 - 3, by0 - 1, bx1 + 3, by1 + 1,
                                          fill="#0d1117", outline="#7fdfff")
            self.tag_raise(label)
            self._item_to_shape[chip] = ("shape", shape)
        self._item_to_shape[label] = ("shape", shape)

    def _draw_selection_highlight(self):
        cell = self.library.cells.get(self.cell_name)
        if cell is None:
            self.selected = None
            self.selected_multi = []
            return

        if self.selected:
            kind = self.selected[0]
            if kind == "vertex":
                _, shape, idx = self.selected
                if shape not in cell.shapes or idx >= len(shape.points):
                    self.selected = None
                else:
                    px, py = self.to_px(*shape.points[idx])
                    self.create_oval(px - 5, py - 5, px + 5, py + 5, outline="#ffcc00",
                                      width=2, tags="selection")
            elif kind == "edge":
                _, shape, (i, j) = self.selected
                if shape not in cell.shapes or i >= len(shape.points) or j >= len(shape.points):
                    self.selected = None
                else:
                    pa = self.to_px(*shape.points[i])
                    pb = self.to_px(*shape.points[j])
                    self.create_line(*pa, *pb, fill="#ffcc00", width=4, tags="selection")
                    for p in (pa, pb):
                        self.create_oval(p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4, fill="#ffcc00",
                                          outline="", tags="selection")
            else:
                _, shape = self.selected
                if kind == "shape" and shape not in cell.shapes:
                    self.selected = None
                elif kind in ("sref", "sref_label", "sref_reftext") and shape not in cell.srefs:
                    self.selected = None
                elif kind == "aref" and shape not in cell.arefs:
                    self.selected = None
                else:
                    self._draw_item_highlight(kind, shape)

        if self.selected_multi:
            still = []
            for kind, shape in self.selected_multi:
                if kind == "shape" and shape not in cell.shapes:
                    continue
                if kind == "sref" and shape not in cell.srefs:
                    continue
                if kind == "aref" and shape not in cell.arefs:
                    continue
                still.append((kind, shape))
                self._draw_item_highlight(kind, shape)
            self.selected_multi = still

    def _draw_item_highlight(self, kind, shape):
        """Dashed bounding-box highlight for a whole shape or sref
        selection -- shared by the single-selection and window-selected
        (multi) cases in _draw_selection_highlight()."""
        if kind in ("sref_label", "sref_reftext"):
            # highlight just the ref-des label, not the whole instance
            if kind == "sref_label":
                ax, ay = self._sref_label_anchor(shape)
            else:
                baked = self._refdes_baked_shape(shape)
                if baked is None:
                    return
                tf = self._sref_transform(shape)
                bx0, by0, bx1, by1 = baked.bbox()
                ax, ay = tf(((bx0 + bx1) / 2.0, (by0 + by1) / 2.0))
            px, py = self.to_px(ax, ay)
            self.create_rectangle(px - 16, py - 10, px + 16, py + 10,
                                   outline="#ffcc00", dash=(3, 2), width=2, tags="selection")
            return
        if kind == "sref":
            box = self._cell_bbox_safe(shape.cell_name)
            if box is not None:
                from ..core.library import _transform_bbox
                x1, y1, x2, y2 = _transform_bbox(box, shape)
            else:
                ox, oy = shape.origin
                x1 = y1 = ox - 5
                x2 = y2 = oy + 5
        elif kind == "aref":
            x1, y1, x2, y2 = self._aref_bbox(shape)
        else:
            x1, y1, x2, y2 = shape.bbox()

        p0 = self.to_px(x1, y1)
        p1 = self.to_px(x2, y2)
        self.create_rectangle(*p0, *p1, outline="#ffcc00", dash=(4, 2), width=2, tags="selection")
        for corner in [(p0[0], p0[1]), (p1[0], p0[1]), (p1[0], p1[1]), (p0[0], p1[1])]:
            cx, cy = corner
            self.create_rectangle(cx - 3, cy - 3, cx + 3, cy + 3, fill="#ffcc00",
                                   outline="", tags="selection")


def _layer_label(library, layer_number):
    layer = library.layers[layer_number]
    return f"{layer_number} ({layer.name})"


def _describe_one(kind, shape, library):
    """One-line (or short multi-line) human-readable summary of a single
    selected shape/sref -- shared by describe_selection() below for both
    a lone selection and each member of a window-selected group."""
    if kind == "sref" or isinstance(shape, SRef):
        mirror = "yes" if shape.x_reflection else "no"
        label_part = f"  label={shape.label!r}" if shape.label else ""
        return (f"Cell Instance  cell={shape.cell_name!r}  "
                f"origin=({shape.origin[0]},{shape.origin[1]})  "
                f"rotation={shape.rotation:g}°  magnification={shape.magnification:g}  "
                f"mirror={mirror}{label_part}")
    if kind == "aref" or isinstance(shape, ARef):
        mirror = "yes" if shape.x_reflection else "no"
        return (f"Array Instance  cell={shape.cell_name!r}  "
                f"origin=({shape.origin[0]},{shape.origin[1]})  "
                f"{shape.columns}x{shape.rows}  "
                f"col_spacing={shape.col_spacing:g}  row_spacing={shape.row_spacing:g}  "
                f"rotation={shape.rotation:g}°  magnification={shape.magnification:g}  "
                f"mirror={mirror}  ({shape.count} instances)")
    if isinstance(shape, Path):
        return (f"Path  layer={_layer_label(library, shape.layer)}  "
                f"width={shape.width}  cap={shape.cap.value}  "
                f"points={len(shape.points)}")
    if isinstance(shape, Rectangle):
        x1, y1, x2, y2 = shape.bbox()
        pin_part = f"  pin={shape.pin!r}" if shape.pin else ""
        return (f"Rectangle  layer={_layer_label(library, shape.layer)}  "
                f"{x1},{y1} to {x2},{y2}  "
                f"size={x2 - x1}x{y2 - y1}{pin_part}")
    if isinstance(shape, Polygon):
        pin_part = f"  pin={shape.pin!r}" if shape.pin else ""
        return (f"Polygon  layer={_layer_label(library, shape.layer)}  "
                f"points={len(shape.points)}{pin_part}")
    if isinstance(shape, Text):
        return (f"Text  layer={_layer_label(library, shape.layer)}  "
                f"text={shape.text!r}  height={shape.height}  font={shape.font}  "
                f"rotation={shape.rotation:g}°")
    return f"{type(shape).__name__}  layer={_layer_label(library, shape.layer)}"


def describe_selection(canvas) -> str:
    """Human-readable properties of whatever is currently selected on
    ``canvas`` -- a single shape/sref, a single vertex/edge, a
    window-selected group, or nothing. Used by the GUI's Properties
    panel (see App._update_properties_panel) and the PROPS command-bar
    command, so both stay in sync with exactly one implementation."""
    library = canvas.library
    if canvas.selected_multi:
        n = len(canvas.selected_multi)
        lines = [f"{n} object(s) selected:"]
        cap = 25
        for kind, shape in canvas.selected_multi[:cap]:
            lines.append("  - " + _describe_one(kind, shape, library))
        if n > cap:
            lines.append(f"  ... and {n - cap} more")
        return "\n".join(lines)

    sel = canvas.selected
    if not sel:
        return "Nothing selected."

    kind = sel[0]
    if kind == "vertex":
        _, shape, idx = sel
        x, y = shape.points[idx]
        return (f"{type(shape).__name__} vertex #{idx}  point=({x},{y})\n"
                f"  (of) {_describe_one('shape', shape, library)}")
    if kind == "edge":
        _, shape, (i, j) = sel
        return (f"{type(shape).__name__} edge #{i}-{j}\n"
                f"  (of) {_describe_one('shape', shape, library)}")
    if kind in ("sref_label", "sref_reftext"):
        _, ref = sel
        where = ("baked Text (moves inside the cell)" if kind == "sref_reftext"
                 else f"floated at centre, offset={ref.label_offset}")
        return (f"Ref-des {ref.label!r} -- {where}\n"
                f"  (of) {_describe_one('sref', ref, library)}")
    _, shape = sel
    return _describe_one(kind, shape, library)


def _choose_from_list(parent, title, prompt, options):
    """A tiny modal listbox picker (simpledialog has no built-in list chooser)."""
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.grab_set()
    tk.Label(win, text=prompt).pack(padx=8, pady=(8, 2))
    listbox = tk.Listbox(win, height=min(10, len(options)), exportselection=False)
    for opt in options:
        listbox.insert("end", opt)
    listbox.selection_set(0)
    listbox.pack(padx=8, pady=2, fill="both", expand=True)

    result = {"value": None}

    def on_ok(event=None):
        sel = listbox.curselection()
        if sel:
            result["value"] = listbox.get(sel[0])
        win.destroy()

    def on_cancel(event=None):
        win.destroy()

    listbox.bind("<Double-Button-1>", on_ok)
    btns = tk.Frame(win)
    btns.pack(pady=(0, 8))
    tk.Button(btns, text="OK", command=on_ok).pack(side="left", padx=4)
    tk.Button(btns, text="Cancel", command=on_cancel).pack(side="left", padx=4)
    win.bind("<Return>", on_ok)
    win.bind("<Escape>", on_cancel)

    win.update_idletasks()
    px = parent.winfo_rootx() + 40
    py = parent.winfo_rooty() + 40
    win.geometry(f"+{px}+{py}")
    parent.wait_window(win)
    return result["value"]


def _prompt_aref_params(parent):
    """Modal form for the array's columns/rows/spacing/rotation/
    magnification/mirror, used by LayoutCanvas._prompt_aref() (Create >
    Array Instance) once a cell has already been picked. Spacing is
    entered in mm (like every other on-screen length in this app) and
    converted to DBU here; returns a kwargs dict ready to splice into
    on_commit's extra dict, or None if cancelled."""
    win = tk.Toplevel(parent)
    win.title("Array Instance")
    win.transient(parent)
    win.grab_set()
    win.resizable(False, False)

    form = tk.Frame(win)
    form.pack(padx=10, pady=10)

    fields = [
        ("Columns:", tk.IntVar(value=2)),
        ("Rows:", tk.IntVar(value=1)),
        ("Column spacing (mm):", tk.DoubleVar(value=1.0)),
        ("Row spacing (mm):", tk.DoubleVar(value=1.0)),
        ("Rotation (degrees):", tk.DoubleVar(value=0.0)),
        ("Magnification:", tk.DoubleVar(value=1.0)),
    ]
    for row, (label, var) in enumerate(fields):
        tk.Label(form, text=label).grid(row=row, column=0, sticky="e", padx=4, pady=3)
        tk.Entry(form, textvariable=var, width=10).grid(row=row, column=1, sticky="w", padx=4, pady=3)
    (columns_var, rows_var, col_spacing_var, row_spacing_var,
     rotation_var, magnification_var) = (var for _, var in fields)

    mirror_var = tk.BooleanVar(value=False)
    tk.Checkbutton(form, text="Mirror (X reflection)", variable=mirror_var).grid(
        row=len(fields), column=0, columnspan=2, sticky="w", padx=4, pady=3)

    error_var = tk.StringVar(value="")
    tk.Label(form, textvariable=error_var, fg="red").grid(
        row=len(fields) + 1, column=0, columnspan=2, sticky="w", padx=4)

    result = {"value": None}

    def on_ok(event=None):
        try:
            columns = int(columns_var.get())
            rows = int(rows_var.get())
            col_spacing = mm_to_dbu(float(col_spacing_var.get()))
            row_spacing = mm_to_dbu(float(row_spacing_var.get()))
            rotation = float(rotation_var.get())
            magnification = float(magnification_var.get())
        except (tk.TclError, ValueError):
            error_var.set("All fields must be numbers.")
            return
        if columns < 1 or rows < 1:
            error_var.set("Columns and rows must be at least 1.")
            return
        if columns > 1 and col_spacing == 0:
            error_var.set("Column spacing must be nonzero when columns > 1.")
            return
        if rows > 1 and row_spacing == 0:
            error_var.set("Row spacing must be nonzero when rows > 1.")
            return
        if magnification <= 0:
            error_var.set("Magnification must be positive.")
            return
        result["value"] = {
            "columns": columns, "rows": rows,
            "col_spacing": col_spacing, "row_spacing": row_spacing,
            "rotation": rotation, "magnification": magnification,
            "x_reflection": mirror_var.get(),
        }
        win.destroy()

    def on_cancel(event=None):
        win.destroy()

    btns = tk.Frame(win)
    btns.pack(pady=(0, 10))
    tk.Button(btns, text="OK", command=on_ok).pack(side="left", padx=4)
    tk.Button(btns, text="Cancel", command=on_cancel).pack(side="left", padx=4)
    win.bind("<Return>", on_ok)
    win.bind("<Escape>", on_cancel)

    win.update_idletasks()
    px = parent.winfo_rootx() + 40
    py = parent.winfo_rooty() + 40
    win.geometry(f"+{px}+{py}")
    parent.wait_window(win)
    return result["value"]


# --- move/translate helpers (module-level, used by LayoutCanvas) ---------

def _dist_point_to_segment(px, py, ax, ay, bx, by):
    """Distance in pixel space from (px,py) to segment (ax,ay)-(bx,by)."""
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    if length2 == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
    cx, cy = ax + t * dx, ay + t * dy
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def _geom_snapshot(kind, shape):
    """Capture the geometry a drag/move should be computed relative to."""
    if kind in ("sref", "aref") or isinstance(shape, (SRef, ARef, Text)):
        return ("origin", shape.origin)
    return ("points", list(shape.points))


def _apply_translate(shape, snapshot, dx, dy):
    """Move a shape by (dx, dy) DBU, relative to its snapshot geometry
    (not cumulative — safe to call repeatedly during a drag)."""
    kind, value = snapshot
    if kind == "origin":
        ox, oy = value
        shape.origin = (int(round(ox + dx)), int(round(oy + dy)))
    else:
        shape.points = [(int(round(px + dx)), int(round(py + dy))) for px, py in value]


def _apply_rotate(points, angle_deg):
    """Rotate a list of (x, y) DBU points by angle_deg degrees
    counter-clockwise about their own centroid, rounding back to
    integer DBU. Used by rotate_selected() for Polygon/Rectangle/Path
    shapes -- Rectangle stores its 4 corners as plain points with no
    axis-aligned invariant enforced (see is_axis_aligned()), so an
    arbitrary-angle rotation here is safe."""
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    out = []
    for px, py in points:
        ox, oy = px - cx, py - cy
        rx = ox * cos_a - oy * sin_a
        ry = ox * sin_a + oy * cos_a
        out.append((int(round(cx + rx)), int(round(cy + ry))))
    return out


def _parse_points_str(text):
    pts = []
    for tok in text.replace(";", " ").split():
        tok = tok.replace(",", " ")
        parts = tok.split()
        if len(parts) != 2:
            raise ValueError(f"bad point {tok!r}, expected x,y")
        pts.append((int(round(float(parts[0]))), int(round(float(parts[1])))))
    return pts


def _points_to_str(points):
    return " ".join(f"{x},{y}" for x, y in points)


def increment_pin_str(pin: str) -> str:
    """The "next" pin after `pin`, for the click-to-increment pin-
    numbering tool (Edit > Number Pins...). A purely-numeric pin counts
    up ("1" -> "2", "9" -> "10"); anything else (a letter pin like "K",
    or blank) is left as-is, since there's no obvious next value to
    guess -- the operator just types the next one in that case."""
    if pin.isdigit():
        return str(int(pin) + 1)
    return pin


def _open_edit_dialog(parent, kind, shape, on_done=None, checkpoint=None):
    """A small property-edit form for the selected shape or sref.
    `checkpoint`, if given, is called once -- right before the shape's
    fields are actually mutated -- so this edit can be undone."""
    win = tk.Toplevel(parent)
    win.title("Edit " + {"sref": "Cell Instance", "aref": "Array Instance"}.get(
        kind, type(shape).__name__))
    win.transient(parent)
    win.grab_set()

    entries = {}
    row = 0

    def add_field(label, value):
        nonlocal row
        tk.Label(win, text=label).grid(row=row, column=0, sticky="e", padx=6, pady=3)
        var = tk.StringVar(value=str(value))
        tk.Entry(win, textvariable=var, width=28).grid(row=row, column=1, padx=6, pady=3)
        entries[label] = var
        row += 1

    error_var = tk.StringVar(value="")

    if kind == "shape":
        add_field("Layer", shape.layer)
        if isinstance(shape, Rectangle):
            (x1, y1), _, (x2, y2), _ = shape.points
            add_field("X1", x1)
            add_field("Y1", y1)
            add_field("X2", x2)
            add_field("Y2", y2)
            add_field("Pin", shape.pin)
        elif isinstance(shape, Polygon):
            add_field("Points (x,y x,y ...)", _points_to_str(shape.points))
            add_field("Pin", shape.pin)
        elif isinstance(shape, Path):
            add_field("Points (x,y x,y ...)", _points_to_str(shape.points))
            add_field("Width", shape.width)
            add_field("Cap (round/square/flush)", shape.cap.value)
        elif isinstance(shape, Text):
            add_field("Text", shape.text)
            add_field("X", shape.origin[0])
            add_field("Y", shape.origin[1])
            add_field("Height", shape.height)
            add_field("Font (0-3)", shape.font)
    elif kind == "aref":
        tk.Label(win, text="Cell").grid(row=row, column=0, sticky="e", padx=6, pady=3)
        tk.Label(win, text=shape.cell_name).grid(row=row, column=1, sticky="w", padx=6, pady=3)
        row += 1
        add_field("X", shape.origin[0])
        add_field("Y", shape.origin[1])
        add_field("Columns", shape.columns)
        add_field("Rows", shape.rows)
        add_field("Column spacing (DBU)", shape.col_spacing)
        add_field("Row spacing (DBU)", shape.row_spacing)
        add_field("Rotation (deg)", shape.rotation)
        add_field("Magnification", shape.magnification)
        add_field("Mirror (0/1)", int(shape.x_reflection))
    else:  # sref
        tk.Label(win, text="Cell").grid(row=row, column=0, sticky="e", padx=6, pady=3)
        tk.Label(win, text=shape.cell_name).grid(row=row, column=1, sticky="w", padx=6, pady=3)
        row += 1
        # Per-instance annotation (e.g. a reference designator) -- unlike
        # editing a Text shape inside the referenced cell, this belongs
        # to just this one placement and follows it when moved. It's a
        # separate annotation next to the instance, not a replacement
        # for any ref-des Text baked into the referenced cell itself.
        add_field("Label", shape.label)
        add_field("Label Size (px)", shape.label_height)
        add_field("Label Offset X (DBU)", shape.label_offset[0])
        add_field("Label Offset Y (DBU)", shape.label_offset[1])
        add_field("X", shape.origin[0])
        add_field("Y", shape.origin[1])
        add_field("Rotation (deg)", shape.rotation)
        add_field("Magnification", shape.magnification)
        add_field("Mirror (0/1)", int(shape.x_reflection))

    def on_ok(event=None):
        try:
            if checkpoint:
                checkpoint()
            if kind == "shape":
                shape.layer = int(entries["Layer"].get())
                if isinstance(shape, Rectangle):
                    x1 = float(entries["X1"].get())
                    y1 = float(entries["Y1"].get())
                    x2 = float(entries["X2"].get())
                    y2 = float(entries["Y2"].get())
                    x1, x2 = sorted((x1, x2))
                    y1, y2 = sorted((y1, y2))
                    x1, x2, y1, y2 = int(round(x1)), int(round(x2)), int(round(y1)), int(round(y2))
                    shape.points = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
                    shape.pin = entries["Pin"].get().strip()
                elif isinstance(shape, Polygon):
                    pts = _parse_points_str(entries["Points (x,y x,y ...)"].get())
                    if len(pts) < 3:
                        raise ValueError("polygon needs at least 3 points")
                    if len(pts) > 200:
                        raise ValueError("polygon max is 200 points")
                    shape.points = pts
                    shape.pin = entries["Pin"].get().strip()
                elif isinstance(shape, Path):
                    pts = _parse_points_str(entries["Points (x,y x,y ...)"].get())
                    if len(pts) < 2:
                        raise ValueError("path needs at least 2 points")
                    if len(pts) > 200:
                        raise ValueError("path max is 200 points")
                    shape.points = pts
                    shape.width = int(round(float(entries["Width"].get())))
                    shape.cap = PathCap(entries["Cap (round/square/flush)"].get().strip().lower())
                elif isinstance(shape, Text):
                    shape.text = entries["Text"].get()
                    shape.origin = (int(round(float(entries["X"].get()))),
                                     int(round(float(entries["Y"].get()))))
                    shape.height = int(round(float(entries["Height"].get())))
                    font = int(entries["Font (0-3)"].get())
                    if not (0 <= font <= 3):
                        raise ValueError("font must be 0-3")
                    shape.font = font
            elif kind == "aref":
                shape.origin = (int(round(float(entries["X"].get()))),
                                 int(round(float(entries["Y"].get()))))
                columns = int(entries["Columns"].get())
                rows = int(entries["Rows"].get())
                col_spacing = float(entries["Column spacing (DBU)"].get())
                row_spacing = float(entries["Row spacing (DBU)"].get())
                if columns < 1 or rows < 1:
                    raise ValueError("columns/rows must be at least 1")
                if columns > 1 and col_spacing == 0:
                    raise ValueError("column spacing must be nonzero when columns > 1")
                if rows > 1 and row_spacing == 0:
                    raise ValueError("row spacing must be nonzero when rows > 1")
                shape.columns = columns
                shape.rows = rows
                shape.col_spacing = col_spacing
                shape.row_spacing = row_spacing
                shape.rotation = float(entries["Rotation (deg)"].get())
                mag = float(entries["Magnification"].get())
                if mag <= 0:
                    raise ValueError("magnification must be positive")
                shape.magnification = mag
                shape.x_reflection = bool(int(entries["Mirror (0/1)"].get()))
            else:  # sref
                shape.label = entries["Label"].get()
                label_height = int(round(float(entries["Label Size (px)"].get())))
                if label_height <= 0:
                    raise ValueError("label size must be positive")
                shape.label_height = label_height
                shape.label_offset = (
                    int(round(float(entries["Label Offset X (DBU)"].get()))),
                    int(round(float(entries["Label Offset Y (DBU)"].get()))),
                )
                shape.origin = (int(round(float(entries["X"].get()))),
                                 int(round(float(entries["Y"].get()))))
                shape.rotation = float(entries["Rotation (deg)"].get())
                mag = float(entries["Magnification"].get())
                if mag <= 0:
                    raise ValueError("magnification must be positive")
                shape.magnification = mag
                shape.x_reflection = bool(int(entries["Mirror (0/1)"].get()))
        except Exception as exc:
            error_var.set(str(exc))
            return
        win.destroy()
        if on_done:
            on_done()

    def on_cancel(event=None):
        win.destroy()

    tk.Label(win, textvariable=error_var, fg="#c0392b").grid(
        row=row, column=0, columnspan=2, padx=6, pady=(0, 3))
    row += 1
    btns = tk.Frame(win)
    btns.grid(row=row, column=0, columnspan=2, pady=(2, 8))
    tk.Button(btns, text="OK", command=on_ok).pack(side="left", padx=4)
    tk.Button(btns, text="Cancel", command=on_cancel).pack(side="left", padx=4)
    win.bind("<Return>", on_ok)
    win.bind("<Escape>", on_cancel)

    win.update_idletasks()
    px = parent.winfo_rootx() + 40
    py = parent.winfo_rooty() + 40
    win.geometry(f"+{px}+{py}")
    parent.wait_window(win)
