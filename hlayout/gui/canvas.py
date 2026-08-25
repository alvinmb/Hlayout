"""LayoutCanvas: a Tkinter Canvas that draws one cell of a Library,
with pan/zoom, a major/minor dot grid, and per-layer colour + stipple.
"""

import copy
import math
import tkinter as tk
from tkinter import font as tkfont
from tkinter import simpledialog

from ..core.errors import GeometryError
from ..core.shapes import (ARef, CIRCLE_SEGMENT_CHOICES, MAX_PATH_POINTS, MAX_POLY_POINTS, Path,
                           PathCap, Polygon, Rectangle, SRef, Text)
from ..core.units import DBU_MM, dbu_to_mm, mm_to_dbu

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
        self.draw_mode = "select"  # select | rect | poly | path | text | sref | aref | circle | zoomwin | route
        self.current_layer = 0
        self.path_width = 2
        self.path_cap = "round"
        self.circle_segments = 32  # one of CIRCLE_SEGMENT_CHOICES
        # --- two-layer Route tool (Create > Route (2-Layer)) -------------
        # Configured via Setup > Route Layers...; route_layer_a is where a
        # route starts, route_layer_b is what Space switches to (and back).
        self.route_layer_a = 0
        self.route_layer_b = 1
        self.route_via_size = 100  # DBU
        self.route_via_shape = "square"  # "square" | "round"
        # Dielectric via window -- a same-shape opening in the dielectric
        # layer sitting between route_layer_a/route_layer_b, normally
        # sized AT OR ABOVE route_via_size, representing the actual
        # electrical contact area (see Session.drop_via()). A real via
        # always has this window; App._reload_route_settings_from_library()
        # sets real values here immediately at startup. None/None is
        # just this attribute's pre-setup placeholder (and remains
        # supported at the API level for bare two-pad vias).
        self.route_dielectric_layer = None
        self.route_dielectric_window = None
        self._route_active_layer = self.route_layer_a
        # Optional callback(x, y, layer_a, layer_b, size, shape,
        # segment=None, dielectric_layer=None, dielectric_window=None),
        # fired when Space drops a via mid-route. When given, `segment`
        # is (layer, points, width, cap) for the path segment drawn so
        # far that should be committed *atomically with* the via as one
        # undo step -- wired by the app to wrap Session.path() +
        # Session.drop_via() in one session.batch(). Left as a no-op so
        # a bare LayoutCanvas (e.g. in a test) works without an
        # app/session wiring it up.
        self.on_route_via = (
            lambda x, y, a, b, size, shape, segment=None,
            dielectric_layer=None, dielectric_window=None: None
        )
        self.snap_enabled = True
        self.snap_dbu = self.grid_dbu  # snap to the current grid spacing by default
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

    def fit_view(self, padding=0.08):
        box = self.library.cell_bbox(self.cell_name) if self.cell_name else None
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
        """Switch the active drawing tool: select, rect, poly, path, text, sref, aref, zoomwin, route."""
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
            "sref": "Cell instance: click the placement point, then choose the cell",
            "aref": "Array instance: click the placement point, then choose the cell "
                    "and array parameters",
            "circle": "Circle: click the center, then click a point on the edge to set the radius",
            "route": self._route_hint(),
        }
        return hints.get(self.draw_mode, "")

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
        self._zoomwin_start = None
        if self.draw_mode == "route":
            self._route_active_layer = self.route_layer_a

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
        sx, sy = self._snap(x, y)
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
        x, y = self.to_dbu(e.x, e.y)
        pt = self._snap(x, y)

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

    def _on_double_click(self, e):
        if self.draw_mode == "select":
            item = self._pick_item_at(e.x, e.y)
            if item is not None:
                kind, shape = self._item_to_shape[item]
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
        4-point-rectangle invariant; use the edit dialog for that.)"""
        cell = self.library.cells.get(self.cell_name)
        if cell is None:
            return []
        return [s for s in cell.shapes if isinstance(s, (Polygon, Path))]

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
        if kind == "vertex":
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
            bx1, by1, bx2, by2 = shape.bbox()
            if bx1 >= rx1 and bx2 <= rx2 and by1 >= ry1 and by2 <= ry2:
                picked.append(("shape", shape))
        for ref in cell.srefs:
            box = self.library.cell_bbox(ref.cell_name)
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
        box = self.library.cell_bbox(ref.cell_name)
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
        if kind == "vertex":
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
        if kind in ("vertex", "edge"):
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
        if kind in ("vertex", "edge"):
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
                           self.route_via_size, self.route_via_shape, segment=segment,
                           dielectric_layer=self.route_dielectric_layer,
                           dielectric_window=self.route_dielectric_window)
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
        rotation = simpledialog.askfloat("Add Cell Instance", "Rotation (degrees):",
                                          parent=self, initialvalue=0.0) or 0.0
        if self.on_commit:
            self.on_commit("sref", self.current_layer, [pt],
                            {"cell_name": cell_name, "rotation": rotation,
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
        img = tk.PhotoImage(width=w, height=h)
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

    @staticmethod
    def _to_photoimage(arr):
        h, w = arr.shape[:2]
        header = ("P6 %d %d 255 " % (w, h)).encode("ascii")
        return tk.PhotoImage(width=w, height=h, data=header + arr.tobytes(), format="PPM")

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

    def _draw_aref(self, ref: "ARef", depth, highlight=False):
        """Draw every array position by delegating to _draw_sref() with a
        synthetic per-instance SRef -- reuses the exact same recursive
        flatten/transform logic an ordinary cell instance uses, just
        called once per array position."""
        for origin in ref.instance_origins():
            instance = SRef(cell_name=ref.cell_name, origin=origin, rotation=ref.rotation,
                             magnification=ref.magnification, x_reflection=ref.x_reflection)
            self._draw_sref(instance, depth, highlight=highlight)

    def _draw_sref(self, ref: SRef, depth, highlight=False):
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
            xr = x * cos_t - y * sin_t
            yr = x * sin_t + y * cos_t
            return (xr + ox, yr + oy)

        if ref.cell_name not in self.library.cells:
            return
        child = self.library.cells[ref.cell_name]
        for shape in sorted(child.shapes, key=lambda s: s.layer):
            layer = self.library.layers[shape.layer]
            if not layer.visible:
                continue
            self._draw_shape(shape, layer, transform=transform, selected=highlight)
        for sub in child.srefs:
            # compose transforms by drawing the nested sref through a
            # temporary shim shape list (simple recursive flatten)
            self._draw_nested_sref(sub, transform, depth + 1, highlight=highlight)

    def _draw_nested_sref(self, ref: SRef, parent_transform, depth, highlight=False):
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
        child = self.library.cells[ref.cell_name]
        for shape in sorted(child.shapes, key=lambda s: s.layer):
            layer = self.library.layers[shape.layer]
            if not layer.visible:
                continue
            self._draw_shape(shape, layer, transform=transform, selected=highlight)
        for sub in child.srefs:
            self._draw_nested_sref(sub, transform, depth + 1, highlight=highlight)

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
                elif kind == "sref" and shape not in cell.srefs:
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
        if kind == "sref":
            box = self.library.cell_bbox(shape.cell_name)
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
        return (f"Cell Instance  cell={shape.cell_name!r}  "
                f"origin=({shape.origin[0]},{shape.origin[1]})  "
                f"rotation={shape.rotation:g}°  magnification={shape.magnification:g}  "
                f"mirror={mirror}")
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
        return (f"Rectangle  layer={_layer_label(library, shape.layer)}  "
                f"{x1},{y1} to {x2},{y2}  "
                f"size={x2 - x1}x{y2 - y1}")
    if isinstance(shape, Polygon):
        return (f"Polygon  layer={_layer_label(library, shape.layer)}  "
                f"points={len(shape.points)}")
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
        elif isinstance(shape, Polygon):
            add_field("Points (x,y x,y ...)", _points_to_str(shape.points))
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
                elif isinstance(shape, Polygon):
                    pts = _parse_points_str(entries["Points (x,y x,y ...)"].get())
                    if len(pts) < 3:
                        raise ValueError("polygon needs at least 3 points")
                    if len(pts) > 200:
                        raise ValueError("polygon max is 200 points")
                    shape.points = pts
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
            else:
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
