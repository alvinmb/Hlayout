"""Main application window for the Thick & Thin Film Hybrid Layout CAD tool."""

import os
import webbrowser
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, simpledialog, ttk

from . import dialogs

from .. import __version__
from ..core.errors import CellError, GeometryError, LayoutError
from ..core.library import Library
from ..core.maskops import MASK_OPS
from ..core.shapes import CIRCLE_SEGMENT_CHOICES
from ..core.units import dbu_to_mm, mm_to_dbu
from ..io.sidecar import load_design
from ..io.techfile import (
    load_resistor_geometry_rules,
    load_route_rules,
    save_resistor_geometry_rules,
    save_route_rules,
)
from ..scripting.api import Session
from ..scripting.console import ScriptConsole
from .canvas import LayoutCanvas, describe_selection
from .commands import COMMAND_REFERENCE, CommandError, CommandInterpreter, _parse_point
from .layer_panel import LayerPanel
from .line_editing import bind_console_style_editing
from .drc_window import open_drc_window
from .dxf_batch_window import open_dxf_batch_window
from .gerber_batch_window import open_gerber_batch_window
from .maskop_window import open_maskop_batch_window
from .resistor_designer import open_resistor_designer
from .thinfilm_designer import open_thin_film_designer


class ViewWindow(tk.Toplevel):
    """An additional, independent view window onto the same library."""

    def __init__(self, master, library, title="View", checkpoint=None):
        super().__init__(master)
        self.title(title)
        self.geometry("900x650")
        self.status = tk.StringVar(value="ready")
        self.canvas = LayoutCanvas(self, library, on_status=self.status.set)
        if checkpoint is not None:
            # Selecting/dragging a shape in a secondary view window edits
            # the same underlying library as the main window -- route it
            # through the same undo history rather than leaving it
            # unrecorded (see Session.checkpoint / App._undo/_redo).
            self.canvas.checkpoint = checkpoint
        self.canvas.pack(fill="both", expand=True)
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Label(bar, textvariable=self.status).pack(side="left", padx=4)
        self.after(50, self.canvas.fit_view)


class App(tk.Tk):
    # The Layers/Console panel keeps this fixed pixel width, anchored to
    # the right edge of the window (see _build_body). The canvas gets
    # everything else. 600px was much wider than the panel's own content
    # (the Layers table's columns, at UI_FONT_SIZE, need well under 500px)
    # and ate into canvas space especially on laptop-sized screens, where
    # the window is clamped well below CANVAS_WIDTH + RIGHT_PANEL_WIDTH and
    # the two ended up nearly equal width instead of the canvas dominating.
    # Trimmed to fit the panel's actual content with modest headroom, so
    # the freed-up width goes to the canvas at every screen size.
    RIGHT_PANEL_WIDTH = 520
    CANVAS_WIDTH = 1450

    # Smallest window size we'll ever request, regardless of screen size —
    # small enough to fit on a laptop display, still usable.
    MIN_WIDTH = 900
    MIN_HEIGHT = 600

    # Every named Tk font is resized to this (see _apply_global_font_size)
    # so every widget in the app -- menus, buttons, labels, entries, the
    # layers table, dialogs -- renders at a consistent, readable size.
    # Canvas-drawn design text (the Text shape primitive) is unaffected:
    # its size is a DBU height set per-shape by the user, not app chrome.
    UI_FONT_SIZE = 14

    def __init__(self, library: Library = None, attached_libraries=None, attached_paths=None):
        # attached_libraries/attached_paths let a caller (currently just
        # hlayout_app.py's CLI startup, via hlayout.io.sidecar.load_design())
        # hand in reference libraries restored from a design's sidecar
        # alongside its initial library -- so opening a file from the
        # command line restores attached libraries the same way File >
        # Open GDS... does, not just the geometry.
        super().__init__()
        # Tkinter's default report_callback_exception just prints the
        # traceback to stderr. In the PyInstaller windowed build
        # (console=False, the default for hlayout.exe -- see
        # packaging/hlayout.spec) there's no console for that to reach
        # on Windows, so a bug inside any button/menu callback used to
        # fail completely silently: the click just appeared to do
        # nothing, with no error and no clue why -- exactly the
        # failure mode behind "Trim Simulator's Run Plunge does
        # nothing" reports. Route it through a dialog instead.
        self.report_callback_exception = self._report_callback_exception
        self._apply_global_font_size()
        self.title("HLayout — Thick & Thin Film Hybrid Layout CAD")

        # Clamp the requested window size to the actual screen, so on a
        # display smaller than the "ideal" 2050x820 the window (and the
        # canvas/right-panel split below) still fits instead of being
        # silently shrunk by the window manager after the sash position
        # was already computed for the larger size.
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        win_w = min(self.CANVAS_WIDTH + self.RIGHT_PANEL_WIDTH, max(self.MIN_WIDTH, screen_w - 80))
        win_h = min(820, max(self.MIN_HEIGHT, screen_h - 80))
        self.minsize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.geometry(f"{win_w}x{win_h}")
        self._win_w = win_w

        self.session = Session(library or self._default_library())
        if attached_libraries:
            self.session.attached_libraries = list(attached_libraries)
            self.session._attached_paths = list(attached_paths or [""] * len(attached_libraries))
        self.console = ScriptConsole(self.session)
        self.current_path = None
        self.status = tk.StringVar(value="ready")

        self._build_body()
        self._build_bottom_toolbar()
        self._build_command_bar()
        # Menu bar is built last: several of its items (Create/Edit
        # draw-mode entries, Setup > Snap to Grid, Tools > Script
        # Console/Command Bar) reference widgets/variables created by the
        # calls above.
        self._build_menu()
        # After _build_menu(): needs self._menu_bar_anchor (set in
        # _build_body()) and packs with before=self._menu_bar_anchor
        # the same way the menu bar does, so it lands directly under
        # the menu bar regardless of build order -- see
        # _build_top_toolbar()'s own docstring.
        self._build_top_toolbar()
        self._build_statusbar()

        self.canvas.on_commit = self._on_canvas_commit
        self.canvas.on_edit = self.refresh_all
        self.canvas.on_mode_status = self.status.set
        self.canvas.on_list_cells = self._placeable_cell_names
        self.canvas.checkpoint = self.session.checkpoint
        self.canvas.on_select = self._update_properties_panel
        self._update_properties_panel()
        self.canvas.on_route_via = self._on_route_via
        self.canvas.on_draw_mode_changed = lambda mode: self.mode_var.set(mode)
        self._on_route_opts_change()

        self.interp = CommandInterpreter(
            self.session, self.console, on_change=self.refresh_all, view=self.canvas
        )
        self.session.on_change(self.refresh_all)
        self.session.on_change(self._update_undo_redo_menu_state)
        self._update_undo_redo_menu_state()

        # Force the initial sash position explicitly — the Layers table's
        # many columns otherwise drive the panel wider than intended
        # before ttk finishes laying out the panes. Use the actual
        # (possibly screen-clamped) window width computed above, not the
        # "ideal" CANVAS_WIDTH, so the right panel stays fully visible
        # (and the canvas doesn't collapse to nothing) on smaller screens.
        self._anchor_sash()
        self.after(50, self._anchor_sash)
        self.after(100, self.canvas.fit_view)
        self.body_pane.bind("<Configure>", self._on_body_configure)
        self.protocol("WM_DELETE_WINDOW", self._on_quit)

    def _report_callback_exception(self, exc, val, tb):
        """Installed as self.report_callback_exception in __init__ --
        see the comment there for why this exists. Shows the exception
        in a dialog (falling back to stderr if even that fails) instead
        of letting Tkinter's default handler silently swallow it."""
        import traceback
        summary = "".join(traceback.format_exception_only(exc, val)).strip()
        # This dialog has no scrollbar and is fixed-size (see
        # dialogs._show), so an unbounded traceback could render taller
        # than the screen -- the last few frames are what matters for
        # a bug report anyway; the full one is always on stderr too.
        frames = "".join(traceback.format_tb(tb)[-6:])
        try:
            traceback.print_exception(exc, val, tb)  # full traceback, for a console run
        except Exception:
            pass  # no console attached (windowed build) -- nothing to print to
        try:
            dialogs.show_error("Unexpected Error", f"{summary}\n\n{frames}", parent=self)
        except Exception:
            pass  # dialog itself failed -- we already tried stderr above

    def _anchor_sash(self):
        try:
            width = self.body_pane.winfo_width()
            if width <= 1:
                width = self._win_w
            sash = max(self.MIN_WIDTH - self.RIGHT_PANEL_WIDTH, width - self.RIGHT_PANEL_WIDTH)
            self.body_pane.sashpos(0, sash)
        except tk.TclError:
            pass

    def _on_body_configure(self, event):
        # Keep the right panel pinned to a fixed width as the window is
        # resized (dragged, maximized, etc.) instead of drifting.
        target = max(self.MIN_WIDTH - self.RIGHT_PANEL_WIDTH, event.width - self.RIGHT_PANEL_WIDTH)
        try:
            if abs(self.body_pane.sashpos(0) - target) > 2:
                self.body_pane.sashpos(0, target)
        except tk.TclError:
            pass

    # --- setup helpers -------------------------------------------------
    def _apply_global_font_size(self):
        """Resize every named Tk font to UI_FONT_SIZE, before any widget
        is built. Tk/ttk widgets that don't get an explicit font=...
        default to one of these named fonts (TkDefaultFont, TkTextFont,
        TkMenuFont, TkHeadingFont, TkFixedFont, TkCaptionFont,
        TkSmallCaptionFont, TkIconFont, TkTooltipFont, ...), so resizing
        the named fonts themselves cascades to menus, buttons, labels,
        entries, the layers table, and stock dialogs (simpledialog,
        filedialog's own widgets aside) alike, without having to touch
        each widget.

        Two places this isn't reliable, both because the OS -- not Tk
        -- draws the widget and ignores Tk's font entirely, no matter
        how the named fonts here are resized:

        - The top-level menu BAR (File, Setup, View, ...): a tk.Menu
          installed as the window's native system menu
          (self.config(menu=...)) is drawn by the OS itself. Because of
          that, _build_menu() doesn't use a native menu bar at all: it
          builds an ordinary row of ttk.Menubutton widgets whose
          dropdowns are plain Tk-drawn popup menus, which DO respect
          -font on every platform. self._menu_font is computed here
          and passed explicitly to those Menubuttons and their
          dropdown tk.Menus.
        - tkinter.messagebox (showinfo/showerror/showwarning/askyesno):
          on Windows this calls the native Win32 MessageBox() dialog,
          confirmed still rendering at a small system font size even
          with every named font here resized. hlayout.gui.dialogs
          provides Tk-drawn replacements (show_info/show_error/
          show_warning/ask_yes_no) used everywhere in this app instead
          -- ordinary ttk widgets, so they pick up TkDefaultFont (and
          therefore UI_FONT_SIZE) the normal way."""
        for name in tkfont.names(self):
            f = tkfont.nametofont(name, self)
            f.configure(size=self.UI_FONT_SIZE)
        default_family = tkfont.nametofont("TkDefaultFont", self).cget("family")
        self._menu_font = (default_family, self.UI_FONT_SIZE)

    def _default_library(self) -> Library:
        lib = Library("UNTITLED")
        lib.new_cell("TOP")
        return lib

    def _build_menu(self):
        """Top-level menu bar: File, Setup, View, Create, Edit,
        Tools, Help. Every command elsewhere in the app (draw toolbar
        buttons, command bar, etc.) has a home under one of these --
        this is the canonical, discoverable command structure. About
        lives under Help (Help > About HLayout) rather than as its own
        top-level entry -- it's a single info dialog, not a set of
        commands, so it doesn't need a menu of its own.

        This is deliberately NOT a native tk.Menu installed via
        self.config(menu=...). On Windows, a tk.Menu used as a
        window's system menu bar is drawn by the OS itself, and the
        OS ignores Tk's -font option for that bar entirely -- an
        explicit font=self._menu_font (added in an earlier round) does
        nothing there, only on the dropdown submenus. So instead this
        builds an ordinary row of ttk.Menubutton widgets (packed in a
        plain Frame, placed above everything else via before=), each
        opening a normal tearoff=0 tk.Menu as its dropdown. A dropdown
        posted from a Menubutton is Tk-drawn on every platform
        (including Windows), so -font on it actually takes effect --
        this is what makes the whole bar, not just the dropdowns,
        respect UI_FONT_SIZE reliably cross-platform.
        """
        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", before=self._menu_bar_anchor)
        self._menu_bar_frame = bar
        # Ordered (label, submenu) pairs -- kept around so tests/other
        # code can introspect the menu structure without needing a
        # native tk.Menu / app["menu"] to walk.
        self._top_menus = []
        self._menu_buttons = {}

        # ttk widgets (Menubutton, Button, ...) have no -font resource
        # of their own -- unlike a plain tk widget, font is only
        # settable through a named ttk style. This style is what
        # UI_FONT_SIZE actually reaches the menu bar buttons through
        # (self._menu_font on the dropdown tk.Menus below is the
        # equivalent for those, which DO have a real -font option).
        self._menu_button_style = "HLayoutMenuBar.TMenubutton"
        style = ttk.Style(self)
        style.configure(self._menu_button_style, font=self._menu_font)

        def add_menu(label):
            mb = ttk.Menubutton(bar, text=label, style=self._menu_button_style)
            mb.pack(side="left")
            m = tk.Menu(mb, tearoff=0, font=self._menu_font)
            mb.configure(menu=m)
            self._top_menus.append((label, m))
            self._menu_buttons[label] = mb
            return m

        # --- File: opening/saving the design itself -----------------------
        filem = add_menu("File")
        filem.add_command(label="New", command=self.new_library, accelerator="Ctrl+N")
        filem.add_command(label="Open GDS...", command=self.open_gds, accelerator="Ctrl+O")
        filem.add_command(label="Save GDS...", command=self.save_gds, accelerator="Ctrl+S")
        filem.add_command(label="Save As Library...", command=self.save_as_library)
        filem.add_separator()
        filem.add_command(label="Merge GDS Libraries...", command=self.show_merge_gds_dialog)
        filem.add_separator()
        filem.add_command(label="Gerber Export Sets...", command=lambda: open_gerber_batch_window(self))
        filem.add_command(label="DXF Export Sets...", command=lambda: open_dxf_batch_window(self))
        filem.add_separator()
        filem.add_command(label="Exit", command=self._on_quit)

        # --- Setup: configuring the environment before/while you draw ----
        setupm = add_menu("Setup")
        setupm.add_command(label="Attach Library...", command=self.attach_library)
        setupm.add_command(label="Attached Libraries...", command=self.show_attached_libraries)
        setupm.add_separator()
        setupm.add_command(label="Layers...", command=self._show_layers_tab)
        setupm.add_checkbutton(label="Snap to Grid", variable=self.snap_var,
                                command=self._on_snap_change)
        setupm.add_checkbutton(label="Orthogonal/45° Routing", variable=self.ortho_var,
                                command=self._on_ortho_change)
        setupm.add_command(label="Grid Spacing...", command=self.show_grid_spacing_dialog)
        setupm.add_separator()
        setupm.add_command(label="Path Style...", command=self.show_path_style_dialog)
        setupm.add_command(label="Circle Segments...", command=self.show_circle_segments_dialog)
        setupm.add_command(label="Route Layers...", command=self.show_route_layers_dialog)
        setupm.add_command(label="Resistor Geometry Rules...", command=self.show_resistor_geometry_dialog)
        setupm.add_separator()
        setupm.add_command(label="Remap Layers...", command=self.show_remap_layers_dialog)
        setupm.add_separator()
        setupm.add_command(label="Load Technology File...", command=self.load_technology_file)
        setupm.add_command(label="Save Technology File...", command=self.save_technology_file)

        # --- View: how the design is displayed ----------------------------
        viewm = add_menu("View")
        viewm.add_command(label="Fit", command=lambda: self.canvas.fit_view(), accelerator="F")
        viewm.add_command(label="Zoom In", command=lambda: self.canvas.zoom_in(), accelerator="+")
        viewm.add_command(label="Zoom Out", command=lambda: self.canvas.zoom_out(), accelerator="-")
        viewm.add_command(label="Toggle Grid", command=lambda: self.canvas.toggle_grid(), accelerator="G")
        viewm.add_separator()
        viewm.add_radiobutton(label="Zoom Window", value="zoomwin", variable=self.mode_var,
                               command=self._on_mode_change, accelerator="W")
        viewm.add_radiobutton(label="Measure", value="measure", variable=self.mode_var,
                               command=self._on_mode_change, accelerator="M")
        viewm.add_separator()
        viewm.add_command(label="New View Window", command=self.new_view_window)

        # --- Create: adding new geometry/instances to the design ---------
        createm = add_menu("Create")
        for label, mode in [
            ("Rectangle", "rect"), ("Polygon", "poly"), ("Path", "path"),
            ("Circle", "circle"), ("Text", "text"), ("Cell Instance", "sref"),
            ("Array Instance", "aref"),
        ]:
            createm.add_radiobutton(label=label, value=mode, variable=self.mode_var,
                                     command=self._on_mode_change)
        createm.add_separator()
        createm.add_radiobutton(label="Route (2-Layer)", value="route", variable=self.mode_var,
                                 command=self._on_mode_change)
        createm.add_separator()
        createm.add_command(label="Find && Place Cell...", command=self.find_and_place_cell)

        # --- Edit: selecting/editing/removing existing geometry -----------
        editm = add_menu("Edit")
        editm.add_command(label="Undo", command=self._undo, accelerator="Ctrl+Z")
        editm.add_command(label="Redo", command=self._redo, accelerator="Ctrl+Y")
        self._editm = editm
        editm.add_separator()
        editm.add_radiobutton(label="Select", value="select", variable=self.mode_var,
                               command=self._on_mode_change)
        editm.add_separator()
        editm.add_command(label="Edit Selected...", command=self._edit_selected)
        editm.add_command(label="Delete Selected", command=self._delete_selected)
        editm.add_separator()
        editm.add_command(label="Insert Vertex on Edge", command=self._insert_vertex_on_edge)
        editm.add_command(label="Continue Path...", command=self._continue_path)
        editm.add_command(label="Number Pins...", command=self._number_pins)
        editm.add_separator()
        editm.add_command(label="Rotate...", command=self._rotate_selected)
        editm.add_command(label="Copy...", command=self._copy_selected)
        editm.add_command(label="Smash Selected to SRefs", command=self._smash_selected)
        editm.add_command(label="Merge Selected...", command=self._merge_selected)
        editm.add_command(label="Create Cell from Selection...", command=self._create_cell_from_selection)
        editm.add_separator()
        editm.add_command(label="Properties", command=self._show_properties_tab)

        # --- Tools: scripting utilities -----------------------------------
        toolsm = add_menu("Tools")
        toolsm.add_command(label="Script Console", command=self._show_console_tab)
        toolsm.add_command(label="Run Script File...", command=self.run_script_file)
        toolsm.add_separator()
        toolsm.add_command(label="Command Bar", command=lambda: self.cmd_entry.focus_set())
        toolsm.add_separator()
        toolsm.add_command(
            label="Thick-Film Resistor Designer...",
            command=lambda: open_resistor_designer(self),
        )
        toolsm.add_command(
            label="Thin-Film Resistor Designer...",
            command=lambda: open_thin_film_designer(self),
        )
        toolsm.add_command(label="Via Wizard...", command=self.show_via_wizard)
        toolsm.add_command(label="Pad Wizard...", command=self.show_pad_wizard)
        toolsm.add_command(label="Mask Operations...", command=self.show_mask_op_dialog)
        toolsm.add_command(label="Mask Operation Batch...", command=lambda: open_maskop_batch_window(self))
        toolsm.add_command(label="DRC...", command=lambda: open_drc_window(self))
        toolsm.add_command(label="Extract Netlist...", command=self.show_extract_netlist_dialog)
        toolsm.add_separator()
        toolsm.add_command(label="Gerber Export Sets...", command=lambda: open_gerber_batch_window(self))
        toolsm.add_command(label="DXF Export Sets...", command=lambda: open_dxf_batch_window(self))

        # --- Help ----------------------------------------------------------
        helpm = add_menu("Help")
        helpm.add_command(label="Command Reference", command=self.show_command_reference, accelerator="F1")
        helpm.add_separator()
        helpm.add_command(label="About HLayout", command=self.show_about)

        self.bind("<Control-n>", lambda e: self.new_library())
        self.bind("<Control-o>", lambda e: self.open_gds())
        self.bind("<Control-s>", lambda e: self.save_gds())
        self.bind("<Control-z>", lambda e: self._undo())
        self.bind("<Control-y>", lambda e: self._redo())
        self.bind("<Control-Shift-Z>", lambda e: self._redo())
        self.bind("f", lambda e: self.canvas.fit_view())
        self.bind("g", lambda e: self.canvas.toggle_grid())
        self.bind("<F1>", lambda e: self.show_command_reference())

    def _show_layers_tab(self):
        self.right_notebook.select(self.layer_panel)

    def _show_console_tab(self):
        self.right_notebook.select(self.console_frame)

    def _show_properties_tab(self):
        self.right_notebook.select(self.props_frame)

    # --- undo/redo ---------------------------------------------------------
    def _undo(self):
        if not self.session.undo():
            self.status.set("nothing to undo")
            return
        self.canvas.selected = None
        self.canvas.set_cell(self.session.current_cell_name)
        self.refresh_all()
        self.status.set("undo")

    def _redo(self):
        if not self.session.redo():
            self.status.set("nothing to redo")
            return
        self.canvas.selected = None
        self.canvas.set_cell(self.session.current_cell_name)
        self.refresh_all()
        self.status.set("redo")

    def _update_undo_redo_menu_state(self):
        # Wired as a Session on_change callback (in addition to being
        # called directly after undo()/redo() themselves) so the Edit
        # menu's Undo/Redo items stay accurate after *any* edit, not
        # just ones made through this method.
        if not hasattr(self, "_editm"):
            return
        self._editm.entryconfigure(0, state="normal" if self.session.can_undo() else "disabled")
        self._editm.entryconfigure(1, state="normal" if self.session.can_redo() else "disabled")

    def _edit_selected(self):
        if not self.canvas.edit_selected():
            if len(self.canvas.selected_multi) > 1:
                dialogs.show_info("Edit Selected", "More than one object is window-selected -- "
                                   "edit properties one object at a time (click just that one).",
                                   parent=self)
            else:
                dialogs.show_info("Edit Selected", "Nothing is selected. Switch to Select mode and click a shape first.",
                                   parent=self)

    def _rotate_selected(self):
        if self.canvas.selected is None and not self.canvas.selected_multi:
            dialogs.show_info("Rotate", "Nothing is selected. Switch to Select mode, click the "
                               "shape or cell instance to rotate (it turns white), or drag a "
                               "box over empty space to window-select several, then try again.",
                               parent=self)
            return
        text = simpledialog.askstring("Rotate", "Rotate by degrees (counter-clockwise):", parent=self)
        if not text:
            return
        try:
            angle = float(text)
        except ValueError:
            dialogs.show_error("Rotate", f"expected a number of degrees, got {text!r}", parent=self)
            return
        if not self.canvas.rotate_selected(angle):
            dialogs.show_info("Rotate", "That selection can't be rotated directly -- "
                               "select a whole shape or cell instance (not a single vertex/edge).",
                               parent=self)

    def _copy_selected(self):
        if self.canvas.selected is None and not self.canvas.selected_multi:
            dialogs.show_info("Copy", "Nothing is selected. Switch to Select mode, click the "
                               "shape or cell instance to copy (it turns white), or drag a box "
                               "over empty space to window-select several, then try again.",
                               parent=self)
            return
        text = simpledialog.askstring(
            "Copy", "Offset the copy by dx,dy (design units):", parent=self, initialvalue="0,0"
        )
        if not text:
            return
        try:
            dx, dy = _parse_point(text)
        except CommandError as e:
            dialogs.show_error("Copy", str(e), parent=self)
            return
        if not self.canvas.copy_selected(dx, dy):
            dialogs.show_info("Copy", "That selection can't be copied directly -- "
                               "select a whole shape or cell instance (not a single vertex/edge).",
                               parent=self)

    def _smash_selected(self):
        """Edit > Smash Selected to SRefs: explode the selected array
        instance (AREF, see Tools > Command Reference's AREF command)
        back into individual cell instances (SREFs), one per array
        position, selecting the new SREFs as a window-selected group.
        Backed by LayoutCanvas.smash_selected(); same operation as the
        command bar's SMASH command."""
        if self.canvas.selected is None or self.canvas.selected[0] != "aref":
            dialogs.show_info(
                "Smash Selected to SRefs",
                "Nothing suitable is selected. Switch to Select mode and click an "
                "array instance (AREF) first -- SMASH only applies to arrays, not "
                "a single shape or cell instance.",
                parent=self,
            )
            return
        self.canvas.smash_selected()
        self.status.set("array instance smashed into individual cell instances")

    def _merge_selected(self):
        """Edit > Merge Selected...: union a window-selected group of
        same-layer Polygon/Rectangle shapes that touch or overlap into
        a single polygon. Backed by LayoutCanvas.merge_selected(); same
        operation as the command bar's UNION command. Unlike most
        Select-mode actions this needs a *group* selection (drag a box
        over the shapes, not a single click) -- a lone shape has
        nothing to merge with."""
        if len(self.canvas.selected_multi) < 2:
            dialogs.show_info(
                "Merge Selected", "Nothing suitable is selected. Switch to Select mode "
                "and drag a box over at least two Polygon/Rectangle shapes on the same "
                "layer first -- MERGE needs a window-selected group, not a single click.",
                parent=self,
            )
            return
        try:
            merged = self.canvas.merge_selected()
        except GeometryError as e:
            dialogs.show_error("Merge Selected", str(e), parent=self)
            return
        if not merged:
            dialogs.show_info(
                "Merge Selected", "That selection can't be merged -- it must be all "
                "Polygon/Rectangle shapes (not paths, text, or cell instances).",
                parent=self,
            )
            return
        self.status.set("merged selected shapes into one polygon")

    def _create_cell_from_selection(self):
        """Edit > Create Cell from Selection...: the classic Calma GDSII
        workflow -- draw or place geometry, window-select it, "make
        cell", give it a name, pick an origin, and the graphics become
        a new reusable library cell (a bondpad or SMD land pattern is
        the typical use). Backed by LayoutCanvas.create_cell_from_selection();
        the current cell ends up with a fresh instance of the new cell
        sitting exactly where the original geometry was, so nothing
        visually moves."""
        if self.canvas.selected is None and not self.canvas.selected_multi:
            dialogs.show_info(
                "Create Cell from Selection", "Nothing is selected. Switch to Select mode, "
                "click a shape or cell instance, or drag a box over several, then try again.",
                parent=self,
            )
            return
        name = simpledialog.askstring("Create Cell from Selection", "New cell name:", parent=self)
        if not name:
            return
        origin_text = simpledialog.askstring(
            "Create Cell from Selection",
            "Cell origin, in this cell's current x,y coordinates\n"
            "(becomes (0, 0) inside the new cell):",
            parent=self, initialvalue="0,0",
        )
        if origin_text is None:
            return
        try:
            origin = _parse_point(origin_text)
        except CommandError as e:
            dialogs.show_error("Create Cell from Selection", str(e), parent=self)
            return
        try:
            created = self.canvas.create_cell_from_selection(name, origin)
        except CellError as e:
            dialogs.show_error("Create Cell from Selection", str(e), parent=self)
            return
        if not created:
            dialogs.show_info(
                "Create Cell from Selection", "That selection can't become a cell -- "
                "select a whole shape or cell instance (not a single vertex/edge).",
                parent=self,
            )
            return
        self.status.set(f"created cell {name!r} from the selection")

    def run_script_file(self):
        path = filedialog.askopenfilename(filetypes=[("Python scripts", "*.py"), ("All files", "*.*")])
        if not path:
            return
        out = self.console.run_file(path)
        self._show_console_tab()
        if out:
            self._append_console_output(out)
        self.refresh_all()
        self.status.set(f"ran {path}")

    def show_about(self):
        dialogs.show_info(
            "About HLayout",
            "HLayout\n"
            "Thick & Thin Film Hybrid Layout CAD Tool\n\n"
            "A layout editor for thick-and-thin-film hybrid microelectronics: "
            "64 layers, GDSII I/O, mouse and command-bar driven drawing/editing, "
            "attached reference libraries, and a full Python scripting API.\n\n"
            f"Version {__version__}",
            parent=self,
        )

    def _build_body(self):
        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)
        self.body_pane = body
        # _build_menu() (called later, once the widgets/vars it
        # references exist) packs the menu bar with before=self so it
        # still ends up visually above the canvas despite being built
        # after it -- body is the first thing packed below the menu
        # bar now that the old top toolbar was merged into the bottom
        # one (see _build_bottom_toolbar).
        self._menu_bar_anchor = body

        self.canvas = LayoutCanvas(body, self.session.library, on_status=self.status.set)
        # weight=1 on the canvas, weight=0 on the right panel: when the
        # window is resized, all the extra/lost space goes to the canvas
        # and the Layers/Console panel stays anchored at a fixed width on
        # the right edge instead of stretching with it.
        body.add(self.canvas, weight=1)

        right = ttk.Notebook(body, width=self.RIGHT_PANEL_WIDTH)
        body.add(right, weight=0)
        self.right_notebook = right  # so menu items (Setup > Layers..., Tools > Script Console) can switch tabs

        self.layer_panel = LayerPanel(right, self.session.library, on_change=self.refresh_all)
        right.add(self.layer_panel, text="Layers")

        props_frame = ttk.Frame(right)
        self.props_frame = props_frame  # so Edit > Properties can select this tab
        right.add(props_frame, text="Properties")
        ttk.Label(
            props_frame,
            text="Select a shape or cell instance (Select tool) to see "
                 "its properties here.",
            foreground="#666",
            font=("TkSmallCaptionFont",),
            wraplength=self.RIGHT_PANEL_WIDTH - 16,
        ).pack(fill="x", padx=4, pady=(4, 2))
        self.props_text = tk.Text(props_frame, height=20, bg="#111", fg="#ddd",
                                   state="disabled", wrap="word")
        self.props_text.pack(fill="both", expand=True, padx=2, pady=(0, 2))

        console_frame = ttk.Frame(right)
        self.console_frame = console_frame  # so Tools > Script Console can select this tab
        right.add(console_frame, text="Script Console")
        hint = ttk.Frame(console_frame)
        hint.pack(fill="x")
        ttk.Label(
            hint,
            text="Python or a command (RECT, CELL, ...) — same as Command Bar. "
                 "F1 for full list.",
            foreground="#666",
            font=("TkSmallCaptionFont",),
        ).pack(side="left", fill="x", padx=4, pady=(2, 0))
        self.console_output = tk.Text(console_frame, height=20, bg="#111", fg="#ddd",
                                       insertbackground="#ddd", state="disabled", wrap="word")
        self.console_output.pack(fill="both", expand=True)
        entry_row = ttk.Frame(console_frame)
        entry_row.pack(fill="x")
        self.console_entry = ttk.Entry(entry_row)
        self.console_entry.pack(side="left", fill="x", expand=True)
        self.console_entry.bind("<Return>", self._on_console_enter)
        # self.interp (CommandInterpreter) is built later in __init__, after
        # all the _build_* calls, so these are deferred lookups (only
        # called once the user actually presses Up/Down) rather than
        # direct references — self.interp doesn't exist yet at this point.
        bind_console_style_editing(
            self.console_entry,
            prev_history=lambda current: self.interp.prev_history(current),
            next_history=lambda current: self.interp.next_history(current),
        )
        ttk.Button(entry_row, text="Run", command=self._on_console_enter).pack(side="left")

    def _build_top_toolbar(self):
        """The Cell/Layer selector row, pinned right under the menu bar
        -- called from __init__ right after _build_menu(), packed with
        before=self._menu_bar_anchor the same way the menu bar itself
        is (see that method's docstring for why "before=" is needed at
        all here: this is built after the canvas/body, but still needs
        to land above it visually). Split out from _build_bottom_toolbar()
        because Cell and Layer are the two controls reached for before
        anything else on a given part of the design -- worth keeping
        visible at the top instead of grouped in with view/draw-mode
        options at the bottom."""
        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x", before=self._menu_bar_anchor)
        self._top_toolbar_frame = bar

        ttk.Label(bar, text="Cell:").pack(side="left", padx=(4, 2), pady=2)
        self.cell_var = tk.StringVar(value=self.session.current_cell_name or "")
        self.cell_combo = ttk.Combobox(bar, textvariable=self.cell_var, width=18, state="readonly")
        self.cell_combo.pack(side="left", pady=2)
        self.cell_combo.bind("<<ComboboxSelected>>", self._on_cell_selected)
        self._refresh_cell_list()

        ttk.Label(bar, text="Layer:").pack(side="left", padx=(12, 2), pady=2)
        self.layer_var = tk.StringVar(value=self._layer_label(0))
        self.layer_combo = ttk.Combobox(bar, textvariable=self.layer_var, width=22,
                                         values=self._layer_choices())
        self.layer_combo.pack(side="left", pady=2)
        self.layer_combo.bind("<<ComboboxSelected>>", lambda e: self._on_layer_change())
        self.layer_combo.bind("<Return>", lambda e: self._on_layer_change())
        self.layer_combo.bind("<FocusOut>", lambda e: self._on_layer_change())

    def _build_bottom_toolbar(self):
        """The toolbar row at the bottom of the window (above the status
        bar and Command bar). Used to be split across two rows: a view/
        navigation toolbar (Fit, Zoom In/Out, Grid, New View, the Cell
        selector) right under the menu bar, and a separate draw-mode
        toolbar (Select/Rect/Polygon/.../Cell Instance radio buttons,
        Layer, Snap to grid, Delete Selected) further down. The
        draw-mode buttons were dropped -- they just duplicated the
        Create menu (and Edit > Select), which is the only place to
        pick a draw tool now -- and everything else was merged into
        this single row and moved down here. Cell and Layer have since
        moved back out to their own row at the top (_build_top_toolbar()),
        pinned under the menu bar -- they're reached for more often than
        the view controls here, so they stay visible without competing
        with the context bar for space at the bottom."""
        bar = ttk.Frame(self)
        bar.pack(side="top", fill="x")

        for label, cmd in [
            ("Fit", lambda: self.canvas.fit_view()),
            ("Zoom In", lambda: self.canvas.zoom_in()),
            ("Zoom Out", lambda: self.canvas.zoom_out()),
            ("Grid", lambda: self.canvas.toggle_grid()),
            ("New View", self.new_view_window),
        ]:
            self._toolbar_button(bar, label, cmd).pack(side="left", padx=2, pady=2)

        # mode_var still exists and still drives the canvas's current
        # draw tool -- it's just set from the Create/Edit menu radio
        # buttons now instead of a duplicate set of buttons here.
        self.mode_var = tk.StringVar(value="select")

        # Cell/Layer selectors live in the top toolbar now (see
        # _build_top_toolbar(), called right after _build_menu() in
        # __init__) -- they're the two controls you reach for before
        # anything else on a given cell/layer, so they sit right under
        # the menu bar instead of down here with view/draw-mode
        # options. cell_var/cell_combo/layer_var/layer_combo are all
        # still created there; nothing else in this file needs to
        # change since it only ever refers to those by name, not by
        # which frame they're packed into.

        # Path Width/Cap moved to Setup > Path Style... (see _show_path_style_dialog);
        # width_var/cap_var still back the canvas's path drawing options, just
        # no longer edited from a toolbar field.
        self.width_var = tk.IntVar(value=2)
        self.cap_var = tk.StringVar(value="round")

        # Circle Segments moved to Setup > Circle Segments... (see
        # show_circle_segments_dialog) -- it only applies to the Circle
        # draw tool, so it doesn't belong on the always-visible toolbar
        # alongside options (Layer, Snap to grid) that apply to every
        # tool. segments_var still backs the canvas's circle drawing
        # option, just no longer edited from a toolbar field.
        self.segments_var = tk.StringVar(value="32")

        # Route (2-Layer) tool options -- Setup > Route Layers... (see
        # show_route_layers_dialog); these back canvas.route_layer_a/b/
        # route_via_cell, same pattern as width_var/cap_var above. Seeded
        # from the technology file (hlayout.io.techfile's
        # load_route_rules()) rather than hardcoded, so a route setup
        # configured once survives a save/reopen instead of needing to
        # be re-entered -- see _reload_route_settings_from_library().
        self.route_layer_a_var = tk.StringVar()
        self.route_layer_b_var = tk.StringVar()
        self.route_via_cell_var = tk.StringVar()
        self._reload_route_settings_from_library()

        self.snap_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Snap to grid", variable=self.snap_var,
                         command=self._on_snap_change).pack(side="left", padx=(12, 2))

        # Constrains Path/Route (2-Layer) drawing to horizontal/vertical/
        # 45-degree segments (LayoutCanvas.ortho_enabled -- see
        # _constrain_to_ortho()/_next_draw_point()) -- on by default,
        # matching how real traces are actually routed. Alongside Snap
        # to grid since both change how a click's point gets placed.
        self.ortho_var = tk.BooleanVar(value=self.canvas.ortho_enabled)
        ttk.Checkbutton(bar, text="Ortho/45° routing", variable=self.ortho_var,
                         command=self._on_ortho_change).pack(side="left", padx=(2, 2))

        # Always-visible current grid spacing readout + a quick way to
        # change it, alongside Snap to grid since they're the same
        # setting (see LayoutCanvas.set_grid_spacing()). Setup > Grid
        # Spacing... opens the same dialog for anyone who goes looking
        # in the menu instead.
        self.grid_spacing_var = tk.StringVar()
        self._refresh_grid_spacing_var()
        ttk.Label(bar, textvariable=self.grid_spacing_var).pack(side="left", padx=(8, 2))
        self._toolbar_button(bar, "Grid...", self.show_grid_spacing_dialog).pack(
            side="left", padx=(0, 2))

        self.mode_hint_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.mode_hint_var, foreground="#0066cc").pack(
            side="left", padx=(12, 2))

        # A second row, directly below the always-visible one above,
        # whose contents change with the active Create/Edit tool (see
        # _refresh_context_bar()) -- e.g. Delete Selected/Insert Vertex
        # on Edge/Continue Path... while in Select, Width/Cap while
        # drawing a Path, Finish/Cancel while mid-poly or mid-route.
        # Its own controls (Width, Cap, Segments, route Layer A/B) are
        # the same variables the Setup > Path Style.../Circle
        # Segments.../Route Layers... dialogs edit -- either place
        # keeps them in sync, this is just quicker access to the ones
        # relevant to what you're doing right now.
        self.context_bar = ttk.Frame(self)
        self.context_bar.pack(side="top", fill="x")
        self._refresh_context_bar()

    #: Fixed character width every button on the bottom toolbar and its
    #: context row is drawn at, so the row reads as one consistent set
    #: of controls instead of each button being sized to its own label
    #: (ttk's default). Sized to comfortably fit the longest label
    #: currently used there ("Insert Vertex on Edge").
    TOOLBAR_BUTTON_WIDTH = 22

    def _toolbar_button(self, parent, text, command):
        return ttk.Button(parent, text=text, command=command, width=self.TOOLBAR_BUTTON_WIDTH)

    def _on_mode_change(self):
        self.canvas.set_mode(self.mode_var.get())
        self.mode_hint_var.set(self.canvas._mode_hint())
        self._refresh_context_bar()

    def _refresh_context_bar(self):
        """Rebuild the context bar's contents for the currently active
        draw/edit mode -- see the comment where self.context_bar is
        created in _build_bottom_toolbar(). Every Create-menu draw mode
        gets the same baseline behaviour here: a Cancel button, since
        canvas.cancel_draw() cleanly drops whatever's pending no matter
        which tool is active (mid-rectangle, mid-circle, before an
        sref's cell is even picked, ...) -- plus Finish for the
        multi-click tools (poly/path/route) where there's a
        click-to-add-another-point loop for it to actually end early."""
        bar = self.context_bar
        for w in bar.winfo_children():
            w.destroy()
        mode = self.mode_var.get()

        def cancel_btn():
            self._toolbar_button(bar, "Cancel", self.canvas.cancel_draw).pack(
                side="left", padx=2, pady=2)

        def finish_cancel():
            self._toolbar_button(bar, "Finish", self.canvas.finish_draw).pack(
                side="left", padx=2, pady=2)
            cancel_btn()

        if mode == "select":
            for label, cmd in [
                ("Delete Selected", self._delete_selected),
                ("Edit Selected...", self._edit_selected),
                ("Insert Vertex on Edge", self._insert_vertex_on_edge),
                ("Continue Path...", self._continue_path),
                ("Number Pins...", self._number_pins),
                ("Rotate...", self._rotate_selected),
                ("Copy...", self._copy_selected),
                ("Smash to SRefs", self._smash_selected),
                ("Merge Selected...", self._merge_selected),
                ("Create Cell...", self._create_cell_from_selection),
            ]:
                self._toolbar_button(bar, label, cmd).pack(side="left", padx=2, pady=2)
            return  # nothing pending to Cancel in Select -- it's not a draw tool

        if mode == "path":
            ttk.Label(bar, text="Width (DBU):").pack(side="left", padx=(2, 2))
            width_spin = ttk.Spinbox(bar, from_=1, to=100000, width=8, textvariable=self.width_var,
                                      command=self._on_path_opts_change)
            width_spin.pack(side="left", padx=(0, 8))
            width_spin.bind("<Return>", lambda e: self._on_path_opts_change())
            width_spin.bind("<FocusOut>", lambda e: self._on_path_opts_change())
            ttk.Label(bar, text="Cap:").pack(side="left", padx=(0, 2))
            cap_combo = ttk.Combobox(bar, textvariable=self.cap_var, width=8, state="readonly",
                                      values=["round", "square", "flush"])
            cap_combo.pack(side="left", padx=(0, 12))
            cap_combo.bind("<<ComboboxSelected>>", lambda e: self._on_path_opts_change())
            finish_cancel()

        elif mode == "poly":
            finish_cancel()

        elif mode == "circle":
            ttk.Label(bar, text="Segments:").pack(side="left", padx=(2, 2))
            seg_combo = ttk.Combobox(bar, textvariable=self.segments_var, width=6, state="readonly",
                                      values=[str(n) for n in CIRCLE_SEGMENT_CHOICES])
            seg_combo.pack(side="left", padx=(0, 12))
            seg_combo.bind("<<ComboboxSelected>>", lambda e: self._on_circle_opts_change())
            cancel_btn()

        elif mode == "route":
            ttk.Label(bar, text="Layer A:").pack(side="left", padx=(2, 2))
            a_combo = ttk.Combobox(bar, textvariable=self.route_layer_a_var, width=16,
                                    values=self._layer_choices())
            a_combo.pack(side="left", padx=(0, 8))
            a_combo.bind("<<ComboboxSelected>>", lambda e: self._on_route_opts_change())
            ttk.Label(bar, text="Layer B:").pack(side="left", padx=(0, 2))
            b_combo = ttk.Combobox(bar, textvariable=self.route_layer_b_var, width=16,
                                    values=self._layer_choices())
            b_combo.pack(side="left", padx=(0, 8))
            b_combo.bind("<<ComboboxSelected>>", lambda e: self._on_route_opts_change())
            self._toolbar_button(bar, "Route Settings...", self.show_route_layers_dialog).pack(
                side="left", padx=(0, 12))
            finish_cancel()

        elif mode in ("sref", "aref"):
            self._toolbar_button(bar, "Find & Place Cell...", self.find_and_place_cell).pack(
                side="left", padx=2, pady=2)
            cancel_btn()

        else:  # rect, text, zoomwin, measure -- single-click-then-click-again tools
            cancel_btn()

    def _layer_label(self, number):
        return f"{number}: {self.session.library.layers[number].name}"

    def _layer_choices(self):
        return [self._layer_label(n) for n in range(64)]

    def _refresh_layer_choices(self):
        if not hasattr(self, "layer_combo"):
            return
        self.layer_combo["values"] = self._layer_choices()
        self.layer_var.set(self._layer_label(self.canvas.current_layer))
        # The Route toolbar's layer fields are displayed as cached "N:
        # Name" text (see _reload_route_settings_from_library()/
        # _on_route_opts_change()), so a layer rename (Layers tab,
        # Load Technology File..., etc.) has to explicitly re-resolve
        # them here too, or Setup > Route Layers... would keep showing
        # whatever name was current the last time those fields were
        # set -- the same "dialogs should always show the technology
        # file's current layer names" requirement as everywhere else.
        if hasattr(self, "route_layer_a_var"):
            self.route_layer_a_var.set(self._layer_label(self.canvas.route_layer_a))
            self.route_layer_b_var.set(self._layer_label(self.canvas.route_layer_b))

    def _on_layer_change(self):
        text = self.layer_var.get().strip()
        if ":" in text:
            text = text.split(":", 1)[0].strip()
        try:
            number = self.session.library.layers.resolve(text)
        except LayoutError as e:
            self.status.set(str(e))
            return
        self.canvas.current_layer = number
        self.layer_var.set(self._layer_label(number))

    def _on_path_opts_change(self):
        try:
            self.canvas.path_width = int(self.width_var.get())
        except (tk.TclError, ValueError):
            pass
        self.canvas.path_cap = self.cap_var.get()

    def show_path_style_dialog(self):
        """Setup > Path Style...: set the width and end-cap style used
        when drawing new paths. Replaces the Width/Cap fields that used
        to live directly on the draw toolbar."""
        win = tk.Toplevel(self)
        win.title("Path Style")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        form = ttk.Frame(win)
        form.pack(padx=12, pady=12)

        ttk.Label(form, text="Width (mm):").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        width_var = tk.DoubleVar(value=dbu_to_mm(self.width_var.get()))
        width_spin = ttk.Spinbox(form, from_=0, to=100.0, increment=0.001, width=8, textvariable=width_var)
        width_spin.grid(row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="End cap:").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        cap_var = tk.StringVar(value=self.cap_var.get())
        cap_combo = ttk.Combobox(form, textvariable=cap_var, width=10, state="readonly",
                                  values=["round", "square", "flush"])
        cap_combo.grid(row=1, column=1, sticky="w", padx=4, pady=4)

        error_var = tk.StringVar(value="")
        ttk.Label(form, textvariable=error_var, foreground="red").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=4
        )

        def apply_and_close():
            try:
                width = mm_to_dbu(float(width_var.get()))
            except (tk.TclError, ValueError):
                error_var.set("Width must be a number.")
                return
            if width < 0:
                error_var.set("Width must not be negative.")
                return
            self.width_var.set(width)
            self.cap_var.set(cap_var.get())
            self._on_path_opts_change()
            self.status.set(f"path style: width={dbu_to_mm(width):g} mm cap={cap_var.get()}")
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(0, 12))
        ttk.Button(btn_row, text="OK", command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        width_spin.focus_set()
        win.bind("<Return>", lambda e: apply_and_close())
        win.bind("<Escape>", lambda e: win.destroy())

    def _on_circle_opts_change(self):
        try:
            self.canvas.circle_segments = int(self.segments_var.get())
        except (tk.TclError, ValueError):
            pass

    def show_circle_segments_dialog(self):
        """Setup > Circle Segments...: choose how many segments the
        Circle draw tool uses to approximate a circle. Moved here from
        the draw toolbar since it's the one draw-tool option that only
        applies to a single tool (Circle), unlike Layer or Snap to
        grid which apply no matter what's being drawn."""
        win = tk.Toplevel(self)
        win.title("Circle Segments")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        form = ttk.Frame(win)
        form.pack(padx=12, pady=12)

        ttk.Label(form, text="Segments:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        segments_var = tk.StringVar(value=self.segments_var.get())
        segments_combo = ttk.Combobox(form, textvariable=segments_var, width=6, state="readonly",
                                       values=[str(n) for n in CIRCLE_SEGMENT_CHOICES])
        segments_combo.grid(row=0, column=1, sticky="w", padx=4, pady=4)

        def apply_and_close():
            self.segments_var.set(segments_var.get())
            self._on_circle_opts_change()
            self.status.set(f"circle segments: {segments_var.get()}")
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(0, 12))
        ttk.Button(btn_row, text="OK", command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        segments_combo.focus_set()
        win.bind("<Return>", lambda e: apply_and_close())
        win.bind("<Escape>", lambda e: win.destroy())

    def _resolve_layer_field(self, text):
        """Turn a "N: name" combobox value (or a bare number/name) into a
        layer number, raising LayoutError via the library's own resolve()
        for a bad entry -- shared by show_route_layers_dialog()."""
        text = text.strip()
        if ":" in text:
            text = text.split(":", 1)[0].strip()
        return self.session.library.layers.resolve(text)

    def _reload_route_settings_from_library(self):
        """Seed the Route (2-Layer) toolbar options from the current
        library's technology file (hlayout.io.techfile.load_route_rules())
        instead of a hardcoded default -- called once at startup and
        again whenever the library itself is swapped out (New, Open
        GDS..., Load Technology File...) so a route setup saved with a
        design (or a technology file) is picked up automatically rather
        than needing to be re-entered every session."""
        rules = load_route_rules(self.session.library)
        a, b = rules["layer_a"], rules["layer_b"]
        self.route_layer_a_var.set(self._layer_label(a))
        self.route_layer_b_var.set(self._layer_label(b))
        self.route_via_cell_var.set(rules["via_cell"])
        self._on_route_opts_change()

    def _on_route_opts_change(self):
        try:
            self.canvas.route_layer_a = self._resolve_layer_field(self.route_layer_a_var.get())
            self.canvas.route_layer_b = self._resolve_layer_field(self.route_layer_b_var.get())
        except LayoutError:
            pass  # dialog's own apply_and_close() already validated and reported this
        self.canvas.route_via_cell = self.route_via_cell_var.get().strip()
        if self.canvas.draw_mode == "route":
            self.canvas._route_active_layer = self.canvas.route_layer_a

    def show_route_layers_dialog(self):
        """Setup > Route Layers...: configure the two conductor layers
        the Create > Route (2-Layer) tool uses, and which reusable via
        cell it places between them. Route starts on Layer A; each Space
        press during a route places an instance (Sref) of the via cell
        at the current point and switches which of the two layers you're
        routing on -- see LayoutCanvas._route_drop_via()/App._on_route_via().

        The via cell itself -- its pad size/shape, any dielectric window,
        anything else its own geometry carries -- is built once via
        Tools > Via Wizard... (or scripting's create_via_cell()) and just
        named here, the same "build the reusable part once, place
        instances of it" pattern as any other cell (Create > Cell
        Instance, Find && Place Cell...) -- this dialog used to also ask
        for via size/shape and a separate dielectric layer/window, which
        only duplicated what the via cell's own geometry already
        describes.

        Everything here is saved into the technology file
        (hlayout.io.techfile.save_route_rules()) on OK, the same
        tech_params-blob pattern as the Resistor Designer's and DRC's
        own saved rules, so it travels with Setup > Save Technology
        File... / TECH SAVE and the per-design .hlaux sidecar -- a route
        setup configured once doesn't need to be re-entered next
        session."""
        win = tk.Toplevel(self)
        win.title("Route Layers")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        form = ttk.Frame(win)
        form.pack(padx=12, pady=12)

        ttk.Label(form, text="Layer A (route starts here):").grid(
            row=0, column=0, sticky="e", padx=4, pady=4)
        layer_a_var = tk.StringVar(value=self.route_layer_a_var.get())
        ttk.Combobox(form, textvariable=layer_a_var, width=22,
                     values=self._layer_choices()).grid(row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Layer B (Space switches to/from):").grid(
            row=1, column=0, sticky="e", padx=4, pady=4)
        layer_b_var = tk.StringVar(value=self.route_layer_b_var.get())
        ttk.Combobox(form, textvariable=layer_b_var, width=22,
                     values=self._layer_choices()).grid(row=1, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Via cell (Space places an instance):").grid(
            row=2, column=0, sticky="e", padx=4, pady=4)
        via_cell_choices = self._placeable_cell_names()
        via_cell_var = tk.StringVar(value=self.route_via_cell_var.get())
        ttk.Combobox(form, textvariable=via_cell_var, width=22,
                     values=via_cell_choices).grid(row=2, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(
            form,
            text="Built once via Tools > Via Wizard... (or scripting's\n"
                 "create_via_cell()); this just names which one to place.",
            foreground="#555", justify="left",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=4)

        error_var = tk.StringVar(value="")
        ttk.Label(form, textvariable=error_var, foreground="red", wraplength=340,
                  justify="left").grid(row=4, column=0, columnspan=2, sticky="w", padx=4)

        def apply_and_close():
            try:
                a = self._resolve_layer_field(layer_a_var.get())
                b = self._resolve_layer_field(layer_b_var.get())
            except LayoutError as e:
                error_var.set(str(e))
                return
            if a == b:
                error_var.set("Layer A and Layer B must be different.")
                return
            via_cell = via_cell_var.get().strip()
            if not via_cell:
                error_var.set(
                    "Pick a via cell -- build one first with Tools > Via Wizard... "
                    "if none exists yet."
                )
                return
            if via_cell not in via_cell_choices:
                error_var.set(
                    f"No cell named {via_cell!r} in this design or any attached library."
                )
                return

            self.route_layer_a_var.set(self._layer_label(a))
            self.route_layer_b_var.set(self._layer_label(b))
            self.route_via_cell_var.set(via_cell)
            self._on_route_opts_change()
            save_route_rules(self.session.library, {
                "layer_a": a, "layer_b": b, "via_cell": via_cell,
            })
            self.status.set(
                f"route layers: A={self._layer_label(a)}  B={self._layer_label(b)}  "
                f"via cell={via_cell!r}"
            )
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(0, 12))
        ttk.Button(btn_row, text="OK", command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        win.bind("<Return>", lambda e: apply_and_close())
        win.bind("<Escape>", lambda e: win.destroy())

    def _on_route_via(self, x, y, layer_a, layer_b, via_cell, segment=None):
        """Wired as canvas.on_route_via -- fired when Space drops a via
        mid-route (see LayoutCanvas._route_drop_via()). `segment`, if
        given, is (layer, points, width, cap) for the path drawn so far
        on the layer being left -- committed in the same Session.batch()
        as the via so the whole space-press is one undo step. The via
        itself is an instance of `via_cell` (Setup > Route Layers...,
        built via Tools > Via Wizard...) placed at (x, y) with
        Session.place_cell() -- not raw pad/dielectric-window shapes --
        so its footprint is whatever that cell's own geometry says.
        `layer_a`/`layer_b` are only used for the error message; the
        placement itself doesn't need them, since the via cell already
        carries its own layers."""
        if not via_cell:
            dialogs.show_error(
                "Route", "No via cell configured -- set one in Setup > Route Layers... "
                "(build one first with Tools > Via Wizard... if none exists yet).",
                parent=self,
            )
            return
        try:
            with self.session.batch():
                if segment is not None:
                    seg_layer, points, width, cap = segment
                    self.session.path(seg_layer, points, width, cap=cap)
                self.session.place_cell(via_cell, (x, y))
        except LayoutError as e:
            dialogs.show_error("Route", f"Could not place via: {e}", parent=self)

    def show_resistor_geometry_dialog(self):
        """Setup > Resistor Geometry Rules...: configure the A/B/C/D
        dimensions and the layers used when a resistor is actually
        drawn into the layout -- Session.generate_resistor() (the RESGEN
        command bar command, and Tools > Thick-Film Resistor
        Designer.../"Place in Layout..."). Saved into this design's
        technology file (Library.tech_params) via
        hlayout.io.techfile.save_resistor_geometry_rules(), same pattern
        as the Resistor Designer's own sizing "Design Rules...".

        A = overlap: resistor ink extends this far beyond the electrical
            body_length at BOTH ends, so it overlaps (contacts) the
            conductor pad.
        B = pad length: how far each conductor pad extends beyond that
            overlap, at each end. Each pad's own footprint is A+B long.
        C = side space: how far each pad extends beyond the resistor
            ink's width on either side (pad width = body_width + 2*C).
        D = glaze oversize: the glaze overprint is the resistor ink
            body's own shape, grown by D on ALL FOUR sides (not just
            matching the ink's footprint).

        The resistor ink layer set here is only the *fallback* used when
        a resistor's ink has no per-Rsh entry in Tools > Thick-Film
        Resistor Designer... > Design Rules... 's ink layer map (each
        available ink sheet resistance is normally matched to its own
        layer there -- see hlayout.io.techfile.resistor_ink_layer()).
        Conductor pad layer, glaze layer, and documentation layer (for
        the reference-designator label -- see "Place in Layout...") are
        single, process-wide layers (the conductor layer can still be
        overridden per placement from "Place in Layout...").
        """
        rules = load_resistor_geometry_rules(self.session.library)
        win = tk.Toplevel(self)
        win.title("Resistor Geometry Rules")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(
            win,
            text="A = resistor-to-conductor overlap (added to body_length at\n"
                 "     both ends)\n"
                 "B = minimum conductor pad length beyond the overlap\n"
                 "     (each pad's footprint is A+B long)\n"
                 "C = minimum conductor space on the side (pad width =\n"
                 "     body_width + 2×C)\n"
                 "D = glaze oversize -- the glaze overprint is the resistor's\n"
                 "     own shape, grown by D on all 4 sides (not just matching\n"
                 "     the ink body), for post-trim protection.",
            justify="left", foreground="#555",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        form = ttk.Frame(win)
        form.pack(padx=12, fill="x")

        ttk.Label(form, text="A - Overlap (mm):").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        a_var = tk.DoubleVar(value=dbu_to_mm(rules["overlap_a"]))
        ttk.Spinbox(form, from_=0, to=1000.0, increment=0.001, width=10, textvariable=a_var).grid(
            row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="B - Pad length (mm):").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        b_var = tk.DoubleVar(value=dbu_to_mm(rules["pad_b"]))
        ttk.Spinbox(form, from_=0, to=1000.0, increment=0.001, width=10, textvariable=b_var).grid(
            row=1, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="C - Side space (mm):").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        c_var = tk.DoubleVar(value=dbu_to_mm(rules["space_c"]))
        ttk.Spinbox(form, from_=0, to=1000.0, increment=0.001, width=10, textvariable=c_var).grid(
            row=2, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="D - Glaze oversize (mm):").grid(row=3, column=0, sticky="e", padx=4, pady=4)
        d_var = tk.DoubleVar(value=dbu_to_mm(rules["glaze_oversize"]))
        ttk.Spinbox(form, from_=0, to=1000.0, increment=0.001, width=10, textvariable=d_var).grid(
            row=3, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Resistor ink layer (fallback):").grid(
            row=4, column=0, sticky="e", padx=4, pady=4)
        r_layer_var = tk.StringVar(value=self._layer_label(rules["resistor_layer"]))
        ttk.Combobox(form, textvariable=r_layer_var, width=22,
                     values=self._layer_choices()).grid(row=4, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Conductor pad layer:").grid(row=5, column=0, sticky="e", padx=4, pady=4)
        c_layer_var = tk.StringVar(value=self._layer_label(rules["conductor_layer"]))
        ttk.Combobox(form, textvariable=c_layer_var, width=22,
                     values=self._layer_choices()).grid(row=5, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Glaze overprint layer:").grid(row=6, column=0, sticky="e", padx=4, pady=4)
        g_layer_var = tk.StringVar(value=self._layer_label(rules["glaze_layer"]))
        ttk.Combobox(form, textvariable=g_layer_var, width=22,
                     values=self._layer_choices()).grid(row=6, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Documentation (ref-des) layer:").grid(
            row=7, column=0, sticky="e", padx=4, pady=4)
        doc_layer_var = tk.StringVar(value=self._layer_label(rules["doc_layer"]))
        ttk.Combobox(form, textvariable=doc_layer_var, width=22,
                     values=self._layer_choices()).grid(row=7, column=1, sticky="w", padx=4, pady=4)

        error_var = tk.StringVar(value="")
        ttk.Label(form, textvariable=error_var, foreground="red").grid(
            row=8, column=0, columnspan=2, sticky="w", padx=4
        )

        def apply_and_close():
            try:
                a, b, c, d = (mm_to_dbu(a_var.get()), mm_to_dbu(b_var.get()),
                              mm_to_dbu(c_var.get()), mm_to_dbu(d_var.get()))
            except tk.TclError:
                error_var.set("A, B, C, and D must be numbers.")
                return
            if a < 0 or b < 0 or c < 0 or d < 0:
                error_var.set("A, B, C, and D must not be negative.")
                return
            try:
                r_layer = self._resolve_layer_field(r_layer_var.get())
                c_layer = self._resolve_layer_field(c_layer_var.get())
                g_layer = self._resolve_layer_field(g_layer_var.get())
                doc_layer = self._resolve_layer_field(doc_layer_var.get())
            except LayoutError as e:
                error_var.set(str(e))
                return
            if len({r_layer, c_layer, g_layer, doc_layer}) != 4:
                error_var.set(
                    "Resistor ink, conductor pad, glaze, and documentation layers "
                    "must all be different.")
                return
            save_resistor_geometry_rules(self.session.library, {
                "overlap_a": a, "pad_b": b, "space_c": c, "glaze_oversize": d,
                "resistor_layer": r_layer, "conductor_layer": c_layer,
                "glaze_layer": g_layer, "doc_layer": doc_layer,
                "ink_layers": rules["ink_layers"],
            })
            self.status.set(
                f"resistor geometry rules saved: A={dbu_to_mm(a):g} B={dbu_to_mm(b):g} "
                f"C={dbu_to_mm(c):g} D={dbu_to_mm(d):g} mm  "
                f"ink={self._layer_label(r_layer)}  pad={self._layer_label(c_layer)}  "
                f"glaze={self._layer_label(g_layer)}  doc={self._layer_label(doc_layer)}")
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(8, 12))
        ttk.Button(btn_row, text="OK", command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        win.bind("<Return>", lambda e: apply_and_close())
        win.bind("<Escape>", lambda e: win.destroy())

    def _on_snap_change(self):
        self.canvas.snap_enabled = self.snap_var.get()

    def _on_ortho_change(self):
        self.canvas.ortho_enabled = self.ortho_var.get()

    def _refresh_grid_spacing_var(self):
        """Keep the toolbar's "Grid: N mm" readout in sync with
        self.canvas.grid_dbu -- called on startup and after Grid
        Spacing... changes it."""
        self.grid_spacing_var.set(f"Grid: {dbu_to_mm(self.canvas.grid_dbu):g} mm")

    def show_grid_spacing_dialog(self):
        """Setup > Grid Spacing... (also reachable via the toolbar's
        Grid... button): change the minor grid line spacing, shown as
        the always-visible "Grid: N mm" toolbar readout. Also updates
        the snap-to-grid pitch to match, since the drawn grid and the
        snap grid are the same setting -- see
        LayoutCanvas.set_grid_spacing()."""
        win = tk.Toplevel(self)
        win.title("Grid Spacing")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        form = ttk.Frame(win)
        form.pack(padx=12, pady=12)

        ttk.Label(form, text="Grid spacing (mm):").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        spacing_var = tk.DoubleVar(value=dbu_to_mm(self.canvas.grid_dbu))
        spacing_spin = ttk.Spinbox(form, from_=0.001, to=1000.0, increment=0.001, width=10,
                                    textvariable=spacing_var)
        spacing_spin.grid(row=0, column=1, sticky="w", padx=4, pady=4)

        error_var = tk.StringVar(value="")
        ttk.Label(form, textvariable=error_var, foreground="red").grid(
            row=1, column=0, columnspan=2, sticky="w", padx=4
        )

        def apply_and_close():
            try:
                spacing_dbu = mm_to_dbu(float(spacing_var.get()))
            except (tk.TclError, ValueError):
                error_var.set("Grid spacing must be a number.")
                return
            try:
                self.canvas.set_grid_spacing(spacing_dbu)
            except GeometryError as e:
                error_var.set(str(e))
                return
            self._refresh_grid_spacing_var()
            self.status.set(f"grid spacing set to {dbu_to_mm(self.canvas.grid_dbu):g} mm")
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(0, 12))
        ttk.Button(btn_row, text="OK", command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        spacing_spin.focus_set()
        win.bind("<Return>", lambda e: apply_and_close())
        win.bind("<Escape>", lambda e: win.destroy())

    def _delete_selected(self):
        self.canvas.delete_selected()

    def _insert_vertex_on_edge(self):
        """Edit > Insert Vertex on Edge: add a new point on the
        currently selected edge, at the spot on that edge you clicked
        to select it. Works on both Path and Polygon edges. Deleting
        the vertex right back out afterward (select it, Delete
        Selected) gives an easy way to reshape a segment into two."""
        if self.canvas.selected is None or self.canvas.selected[0] != "edge":
            dialogs.show_info(
                "Insert Vertex on Edge",
                "Nothing suitable is selected. Switch to Select mode and click "
                "an edge (a segment between two points, not a vertex itself) "
                "of a path or polygon first.",
                parent=self,
            )
            return
        self.canvas.insert_vertex_on_selected_edge()

    def _number_pins(self):
        """Edit > Number Pins...: enter the click-to-increment pin-
        numbering tool (LayoutCanvas.start_pin_numbering()) for the
        current cell -- click each pad (Rectangle/Polygon) in the
        physical pinout order you want; each click stamps the current
        pin number and moves to the next one automatically. Prompts
        for a starting number (default "1"); Esc stops and returns to
        Select. See Session.list_pins()/set_pin() and the PIN command
        for reviewing/correcting assignments afterward, and
        hlayout.core.netlist for what pin numbers are ultimately for."""
        start = simpledialog.askstring(
            "Number Pins", "Starting pin number:", initialvalue="1", parent=self,
        )
        if start is None:
            return
        self.canvas.start_pin_numbering(start)

    def _continue_path(self):
        """Edit > Continue Path...: re-enter Path drawing pre-seeded
        with the selected Path's own points, so more points can be
        clicked onto its end instead of only ever starting a new path
        from scratch. Finish the same way as any other path
        (double-click/Enter); Esc cancels back to the unchanged
        original. See LayoutCanvas.continue_path()/_commit_path()."""
        if self.canvas.selected is None or self.canvas.selected[0] != "shape":
            dialogs.show_info(
                "Continue Path", "Nothing suitable is selected. Switch to Select "
                "mode and click a whole path first (not a vertex/edge of one).",
                parent=self,
            )
            return
        if not self.canvas.continue_path():
            dialogs.show_info("Continue Path", "The current selection isn't a path.",
                               parent=self)

    # --- turning mouse-drawn shapes into real database edits -------------
    def _on_canvas_commit(self, mode, layer, points, extra):
        try:
            if mode == "rect":
                (x1, y1), (x2, y2) = points
                self.session.rect(layer, x1, y1, x2, y2)
            elif mode == "poly":
                self.session.polygon(layer, points)
            elif mode == "path":
                replace = extra.get("replace")
                if replace is not None:
                    # Edit > Continue Path finishing: the extended point
                    # list replaces the original shape rather than
                    # sitting alongside it, as one undo step.
                    with self.session.batch():
                        self.session.remove(replace)
                        self.session.path(layer, points, extra["width"], cap=extra["cap"])
                else:
                    self.session.path(layer, points, extra["width"], cap=extra["cap"])
            elif mode == "circle":
                self.session.circle(layer, points[0], extra["radius"], segments=extra["segments"])
            elif mode == "text":
                self.session.text(layer, points[0], extra["text"], height=extra["height"],
                                   font=extra["font"])
            elif mode == "sref":
                # place_cell() (not the lower-level session.sref()) so
                # picking a cell that only lives in an attached library
                # -- offered by the picker via _placeable_cell_names()
                # below -- imports its dependency closure first instead
                # of creating a dangling reference to a cell that isn't
                # actually defined in this design.
                self.session.place_cell(
                    extra["cell_name"], points[0], rotation=extra["rotation"],
                    magnification=extra["magnification"], x_reflection=extra["x_reflection"],
                )
            elif mode == "aref":
                # place_cell_array() (not the lower-level session.aref())
                # for the same reason "sref" uses place_cell() above --
                # picking a cell that only lives in an attached library
                # imports its dependency closure first.
                self.session.place_cell_array(
                    extra["cell_name"], points[0],
                    columns=extra["columns"], rows=extra["rows"],
                    col_spacing=extra["col_spacing"], row_spacing=extra["row_spacing"],
                    rotation=extra["rotation"], magnification=extra["magnification"],
                    x_reflection=extra["x_reflection"],
                )
        except LayoutError as e:
            dialogs.show_error("Could not add shape", str(e), parent=self)

    def _placeable_cell_names(self):
        """Cell names offered by the Create > Cell Instance draw tool's
        picker: the current design's own cells plus every cell in every
        attached library (see Setup > Attach Library...). Mirrors what
        Create > Find && Place Cell... already searches, so both ways of
        placing a cell instance see the same set of cells."""
        return [m["name"] for m in self.session.find_cells("")]

    def _build_command_bar(self):
        frame = ttk.Frame(self)
        frame.pack(side="bottom", fill="x")
        ttk.Label(frame, text="Command:").pack(side="left", padx=(4, 2))
        self.cmd_var = tk.StringVar()
        self.cmd_entry = ttk.Entry(frame, textvariable=self.cmd_var)
        self.cmd_entry.pack(side="left", fill="x", expand=True, padx=2, pady=2)
        self.cmd_entry.bind("<Return>", self._on_command_enter)
        self.cmd_entry.bind("<Up>", self._on_command_up)
        self.cmd_entry.bind("<Down>", self._on_command_down)
        bind_console_style_editing(self.cmd_entry)  # Up/Down already bound above
        self.cmd_entry.focus_set()
        ttk.Button(frame, text="Run", command=self._on_command_enter).pack(side="left", padx=2)

    def _build_statusbar(self):
        bar = ttk.Frame(self)
        bar.pack(side="bottom", fill="x")
        ttk.Label(bar, textvariable=self.status, anchor="w").pack(fill="x", padx=4)

    # --- command bar / console handlers ---------------------------------
    def _on_command_enter(self, event=None):
        line = self.cmd_var.get()
        if not line.strip():
            return
        result = self.interp.run(line)
        self.status.set(result if result else "ok")
        self.cmd_var.set("")
        self._refresh_cell_list()

    def _on_command_up(self, event):
        self.cmd_var.set(self.interp.prev_history(self.cmd_var.get()))
        self.cmd_entry.icursor("end")

    def _on_command_down(self, event):
        self.cmd_var.set(self.interp.next_history(self.cmd_var.get()))
        self.cmd_entry.icursor("end")

    def _on_console_enter(self, event=None):
        src = self.console_entry.get()
        if not src.strip():
            return
        # Route through the same CommandInterpreter as the Command Bar, so
        # the Script Console accepts both short commands (RECT, CELL,
        # LAYER, HELP, ...) and raw Python, exactly like the bottom
        # Command Bar — no need to switch widgets to use one or the other.
        out = self.interp.run(src)
        self.console_output.configure(state="normal")
        self.console_output.insert("end", f">>> {src}\n")
        if out:
            self.console_output.insert("end", out if out.endswith("\n") else out + "\n")
        self.console_output.see("end")
        self.console_output.configure(state="disabled")
        self.console_entry.delete(0, "end")
        self._refresh_cell_list()
        self.refresh_all()

    def _append_console_output(self, text):
        self.console_output.configure(state="normal")
        self.console_output.insert("end", text if text.endswith("\n") else text + "\n")
        self.console_output.see("end")
        self.console_output.configure(state="disabled")

    # --- cell selector ----------------------------------------------------
    def _refresh_cell_list(self):
        names = sorted(self.session.library.cells.keys())
        self.cell_combo["values"] = names
        if self.session.current_cell_name:
            self.cell_var.set(self.session.current_cell_name)

    def _on_cell_selected(self, event):
        name = self.cell_var.get()
        self.session.use_cell(name)
        self.canvas.set_cell(name)
        # Show the whole of the newly selected cell, not whatever
        # region the view happened to be scrolled/zoomed to before.
        self.canvas.fit_view()

    # --- file operations --------------------------------------------------
    def new_library(self):
        self.session.library = self._default_library()
        self.session.current_cell_name = self.session.library.top_cell_name
        # A brand new document -- any libraries attached to the design
        # being replaced don't carry over, same as a document opened
        # fresh from disk with no sidecar.
        self.session.attached_libraries = []
        self.session._attached_paths = []
        self.session.attach_errors = []
        self.session.had_sidecar = False
        # A brand new document, not an edit -- nothing to undo into it.
        self.session.clear_undo_history()
        self._update_undo_redo_menu_state()
        self.current_path = None
        self.canvas.library = self.session.library
        self.canvas.set_cell(self.session.current_cell_name)
        self.layer_panel.set_library(self.session.library)
        self._refresh_cell_list()
        self._refresh_layer_choices()
        self._reload_route_settings_from_library()

    def open_gds(self):
        path = filedialog.askopenfilename(filetypes=[("GDSII", "*.gds"), ("All files", "*.*")])
        if not path:
            return
        try:
            loaded = load_design(path)
        except Exception as e:
            dialogs.show_error("Open failed", str(e), parent=self)
            return
        lib = loaded.library
        self.session.library = lib
        self.session.attached_libraries = loaded.attached_libraries
        self.session._attached_paths = loaded.attached_paths
        self.session.attach_errors = loaded.attach_errors
        self.session.had_sidecar = loaded.had_sidecar
        self.session.current_cell_name = lib.top_cell_name
        # A different document was just loaded -- nothing to undo into it.
        self.session.clear_undo_history()
        self._update_undo_redo_menu_state()
        self.current_path = path
        self.canvas.library = lib
        self.canvas.set_cell(lib.top_cell_name)
        self.layer_panel.set_library(lib)
        self._refresh_cell_list()
        self._refresh_layer_choices()
        self._reload_route_settings_from_library()
        self.canvas.fit_view()
        if loaded.attach_errors:
            dialogs.show_warning(
                "Open GDS",
                "Opened, but some attached libraries in the sidecar didn't restore:\n"
                + "\n".join(loaded.attach_errors),
                parent=self,
            )
        if loaded.had_sidecar:
            self.status.set(
                f"opened {path} (restored technology + "
                f"{len(loaded.attached_libraries)} attached library/ies)"
            )
        else:
            self.status.set(f"opened {path}")

    def save_gds(self):
        """Save the current design as a .gds file plus its sidecar (see
        hlayout.io.sidecar) -- the technology (layer stack/tech params,
        including any saved Resistor Designer rules) and a full embedded
        copy of every attached library, so File > Open GDS... later
        restores all of it, not just the geometry."""
        path = self.current_path or filedialog.asksaveasfilename(
            defaultextension=".gds", filetypes=[("GDSII", "*.gds")])
        if not path:
            return
        try:
            self.session.save_design(path)
        except LayoutError as e:
            dialogs.show_error("Save failed", str(e), parent=self)
            return
        self.current_path = path
        self.status.set(f"saved {path} (+ technology/attached-libraries sidecar)")

    def new_view_window(self):
        ViewWindow(self, self.session.library, title=f"View - {self.session.current_cell_name}",
                   checkpoint=self.session.checkpoint)

    def save_as_library(self):
        """Save the current design to a GDSII file under a chosen library
        name — the way to publish it as a reusable reference library
        that can later be picked up with Attach Library."""
        name = simpledialog.askstring(
            "Save As Library", "Library name:", initialvalue=self.session.library.name, parent=self
        )
        if not name:
            return
        path = filedialog.asksaveasfilename(defaultextension=".gds", filetypes=[("GDSII", "*.gds")])
        if not path:
            return
        try:
            self.session.save(path, library_name=name)
        except LayoutError as e:
            dialogs.show_error("Save As Library failed", str(e), parent=self)
            return
        self.status.set(f"saved library {name!r} to {path}")

    def show_merge_gds_dialog(self):
        """File > Merge GDS Libraries...: combine two GDSII files into a
        third, cell for cell -- a plain file-to-file operation, doesn't
        touch the currently open design. Prompts for the two source
        files and where to write the merged result, then hands off to
        Session.merge_gds(); same operation as the command bar's
        MERGE <lib1.gds> <lib2.gds> <lib3.gds> command."""
        path_a = filedialog.askopenfilename(
            title="Merge GDS Libraries: first file",
            filetypes=[("GDSII", "*.gds"), ("All files", "*.*")])
        if not path_a:
            return
        path_b = filedialog.askopenfilename(
            title="Merge GDS Libraries: second file",
            filetypes=[("GDSII", "*.gds"), ("All files", "*.*")])
        if not path_b:
            return
        out_path = filedialog.asksaveasfilename(
            title="Merge GDS Libraries: save merged file as",
            defaultextension=".gds", filetypes=[("GDSII", "*.gds")])
        if not out_path:
            return
        try:
            result = self.session.merge_gds(path_a, path_b, out_path)
        except LayoutError as e:
            dialogs.show_error("Merge GDS Libraries failed", str(e), parent=self)
            return
        except OSError as e:
            dialogs.show_error("Merge GDS Libraries failed", str(e), parent=self)
            return
        msg = f"merged into {out_path} ({result['cells']} cells)"
        if result["renamed"]:
            n = len(result["renamed"])
            msg += f", {n} cell name(s) renamed to avoid collisions"
        self.status.set(msg)
        dialogs.show_info("Merge GDS Libraries", msg, parent=self)

    def attach_library(self):
        path = filedialog.askopenfilename(filetypes=[("GDSII", "*.gds"), ("All files", "*.*")])
        if not path:
            return
        try:
            lib = self.session.attach_library(path)
        except LayoutError as e:
            dialogs.show_error("Attach Library failed", str(e), parent=self)
            return
        idx = len(self.session.attached_libraries) - 1
        self.status.set(f"attached library {idx}: {lib.name!r} ({len(lib.cells)} cells)")

    def load_technology_file(self):
        """Load a technology file (layer stack + global parameters,
        see hlayout/io/techfile.py) and apply it to the current
        design's layer stack, in place. Meant to be done once at the
        start of a design, but can be re-run any time -- e.g. to pick
        up an updated process technology mid-design."""
        path = filedialog.askopenfilename(
            filetypes=[("Technology files", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            data = self.session.load_technology(path)
        except LayoutError as e:
            dialogs.show_error("Load Technology File failed", str(e), parent=self)
            return
        self.refresh_all()
        self._reload_route_settings_from_library()
        n_layers = len(data.get("layers", []))
        n_params = len(data.get("params", {}))
        self.status.set(f"loaded technology from {path} ({n_layers} layers, {n_params} params)")

    def save_technology_file(self):
        """Save the current design's layer stack + global parameters
        out as a technology file, independent of saving the design
        itself -- e.g. to capture layer edits made in this design as
        the starting point for the next one."""
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("Technology files", "*.json")])
        if not path:
            return
        try:
            self.session.save_technology(path)
        except LayoutError as e:
            dialogs.show_error("Save Technology File failed", str(e), parent=self)
            return
        self.status.set(f"saved technology to {path}")

    def show_remap_layers_dialog(self):
        """Setup > Remap Layers...: bulk-reassign shapes from one layer
        to another, across every cell in the library by default. The
        usual reason to do this is bringing an existing design's
        geometry into alignment with a layer stack just loaded from a
        technology file (Setup > Load Technology File...) -- e.g. this
        design's old layer 3 should really be layer 7 in the target
        process. Backed by Session.remap_layers(); same operation as
        the LAYER MOVE <from> <to> command."""
        win = tk.Toplevel(self)
        win.title("Remap Layers")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(
            win,
            text="Move every shape on one layer to another, across the whole "
                 "library. Add one row per layer you want to reassign, then "
                 "click Apply.",
            wraplength=420, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        rows_frame = ttk.Frame(win)
        rows_frame.pack(padx=12, fill="x")

        header = ttk.Frame(rows_frame)
        header.pack(fill="x")
        ttk.Label(header, text="From layer", width=18).pack(side="left")
        ttk.Label(header, text="To layer", width=18).pack(side="left")

        rows = []

        def layer_choices():
            return [f"{n} ({self.session.library.layers[n].name})"
                    for n in range(len(self.session.library.layers))]

        def add_row(from_default="", to_default=""):
            row = ttk.Frame(rows_frame)
            row.pack(fill="x", pady=2)
            from_var = tk.StringVar(value=from_default)
            to_var = tk.StringVar(value=to_default)
            choices = layer_choices()
            from_combo = ttk.Combobox(row, textvariable=from_var, width=18,
                                       state="readonly", values=choices)
            from_combo.pack(side="left", padx=(0, 4))
            to_combo = ttk.Combobox(row, textvariable=to_var, width=18,
                                     state="readonly", values=choices)
            to_combo.pack(side="left", padx=(0, 4))

            def remove_row():
                row.destroy()
                rows.remove(entry)

            remove_btn = ttk.Button(row, text="Remove", command=remove_row)
            remove_btn.pack(side="left")
            entry = (from_var, to_var)
            rows.append(entry)

        add_row()

        ttk.Button(win, text="+ Add Row", command=lambda: add_row()).pack(
            anchor="w", padx=12, pady=(4, 8))

        scope_row = ttk.Frame(win)
        scope_row.pack(fill="x", padx=12)
        ttk.Label(scope_row, text="Apply to:").pack(side="left")
        scope_var = tk.StringVar(value="Whole library (every cell)")
        cell_names = list(self.session.library.cells.keys())
        scope_combo = ttk.Combobox(
            scope_row, textvariable=scope_var, state="readonly", width=28,
            values=["Whole library (every cell)"] + cell_names,
        )
        scope_combo.pack(side="left", padx=6)

        error_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=error_var, foreground="red", wraplength=420,
                  justify="left").pack(anchor="w", padx=12)

        def apply_and_close():
            mapping = {}
            for from_var, to_var in rows:
                fv, tv = from_var.get().strip(), to_var.get().strip()
                if not fv or not tv:
                    continue
                from_num = int(fv.split(" ", 1)[0])
                to_num = int(tv.split(" ", 1)[0])
                mapping[from_num] = to_num
            if not mapping:
                error_var.set("Add at least one From/To layer pair.")
                return
            scope = scope_var.get()
            cell_names_arg = None if scope.startswith("Whole library") else [scope]
            try:
                counts = self.session.remap_layers(mapping, cell_names=cell_names_arg)
            except LayoutError as e:
                error_var.set(str(e))
                return
            self.refresh_all()
            moved = sum(counts.values())
            self.status.set(f"remap layers: moved {moved} shape(s)")
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(6, 12))
        ttk.Button(btn_row, text="Apply", command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        win.bind("<Escape>", lambda e: win.destroy())

    def show_mask_op_dialog(self):
        """Tools > Mask Operations...: layer-based OVERSIZE/UNDERSIZE/
        AND/OR/NOT/XOR -- similar to a GDSII CAM system's resize/boolean
        deck operators. Grows or shrinks a layer by a fixed amount, or
        combines two layers with a boolean intersection/union/
        difference/symmetric-difference, writing the result as new
        polygons on a target layer (which may be the same layer or a
        fresh one). Any Path on an input layer is converted to its
        stroked-outline polygon first. Backed by Session.mask_op(); same
        operation as the MASKOP command-bar command. Requires the
        shapely package -- Session.mask_op() raises a clear error if it
        isn't installed."""
        win = tk.Toplevel(self)
        win.title("Mask Operations")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(
            win,
            text="Grow/shrink a layer (OVERSIZE/UNDERSIZE), or combine two "
                 "layers (AND=intersection, OR=union, NOT=A minus B, "
                 "XOR=symmetric difference), writing the result as new "
                 "polygons on a target layer. Paths on the input layer(s) "
                 "are converted to their stroked outline first.",
            wraplength=420, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        def layer_choices():
            return [f"{n} ({self.session.library.layers[n].name})"
                    for n in range(len(self.session.library.layers))]

        form = ttk.Frame(win)
        form.pack(padx=12, fill="x")

        ttk.Label(form, text="Operation:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        op_var = tk.StringVar(value=MASK_OPS[0])
        op_combo = ttk.Combobox(form, textvariable=op_var, width=16, state="readonly",
                                 values=list(MASK_OPS))
        op_combo.grid(row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Layer A:").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        a_var = tk.StringVar(value="")
        a_combo = ttk.Combobox(form, textvariable=a_var, width=22, state="readonly",
                                values=layer_choices())
        a_combo.grid(row=1, column=1, sticky="w", padx=4, pady=4)

        b_label = ttk.Label(form, text="Layer B:")
        b_label.grid(row=2, column=0, sticky="e", padx=4, pady=4)
        b_var = tk.StringVar(value="")
        b_combo = ttk.Combobox(form, textvariable=b_var, width=22, state="readonly",
                                values=layer_choices())
        b_combo.grid(row=2, column=1, sticky="w", padx=4, pady=4)

        amount_label = ttk.Label(form, text="Amount (mm):")
        amount_label.grid(row=3, column=0, sticky="e", padx=4, pady=4)
        amount_var = tk.DoubleVar(value=0.0)
        amount_spin = ttk.Spinbox(form, from_=0.0, to=1e6, increment=0.001, textvariable=amount_var, width=14)
        amount_spin.grid(row=3, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Result layer:").grid(row=4, column=0, sticky="e", padx=4, pady=4)
        result_var = tk.StringVar(value="")
        result_combo = ttk.Combobox(form, textvariable=result_var, width=22, state="readonly",
                                     values=layer_choices())
        result_combo.grid(row=4, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Apply to:").grid(row=5, column=0, sticky="e", padx=4, pady=4)
        scope_var = tk.StringVar(value="Current cell")
        cell_names = list(self.session.library.cells.keys())
        scope_combo = ttk.Combobox(form, textvariable=scope_var, state="readonly", width=22,
                                    values=["Current cell"] + cell_names)
        scope_combo.grid(row=5, column=1, sticky="w", padx=4, pady=4)

        # On by default -- most real content in a design ends up placed
        # via Sref rather than drawn straight into the cell being
        # operated on (every resistor the Resistor Designer places is
        # its own device cell + a single Sref), so a mask op that only
        # looked at the operated-on cell's own shapes would silently
        # find nothing to work with for a design built that way.
        flatten_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            form, text="Include placed cells (Sref) when gathering input geometry",
            variable=flatten_var,
        ).grid(row=6, column=0, columnspan=2, sticky="w", padx=4, pady=(0, 4))

        ttk.Label(form, text="Simplify (mm, 0=off):").grid(
            row=7, column=0, sticky="e", padx=4, pady=4)
        simplify_var = tk.DoubleVar(value=0.0)
        ttk.Spinbox(form, from_=0.0, to=1e6, increment=0.001, textvariable=simplify_var, width=14).grid(
            row=7, column=1, sticky="w", padx=4, pady=4)

        BOOLEAN_OPS = ("AND", "OR", "NOT", "XOR")

        def sync_fields(event=None):
            op = op_var.get()
            is_boolean = op in BOOLEAN_OPS
            b_combo.configure(state="readonly" if is_boolean else "disabled")
            amount_spin.configure(state="disabled" if is_boolean else "normal")

        op_combo.bind("<<ComboboxSelected>>", sync_fields)
        sync_fields()

        error_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=error_var, foreground="red", wraplength=420,
                  justify="left").pack(anchor="w", padx=12)

        def layer_num(text):
            return int(text.split(" ", 1)[0]) if text.strip() else None

        def apply_and_close():
            op = op_var.get()
            a_layer = layer_num(a_var.get())
            result_layer = layer_num(result_var.get())
            if a_layer is None or result_layer is None:
                error_var.set("Pick Layer A and a Result layer.")
                return
            kwargs = {"flatten": flatten_var.get()}
            scope = scope_var.get()
            if scope != "Current cell":
                kwargs["cell_names"] = [scope]
            try:
                simplify = mm_to_dbu(simplify_var.get())
            except tk.TclError:
                simplify = 0
            if simplify > 0:
                kwargs["simplify"] = simplify
            if op in BOOLEAN_OPS:
                b_layer = layer_num(b_var.get())
                if b_layer is None:
                    error_var.set(f"Pick Layer B for {op}.")
                    return
                kwargs["layer_b"] = b_layer
            else:
                try:
                    amount = mm_to_dbu(amount_var.get())
                except tk.TclError:
                    error_var.set("Amount must be a number.")
                    return
                if amount <= 0:
                    error_var.set("Amount must be greater than zero.")
                    return
                kwargs["amount"] = amount
            try:
                written = self.session.mask_op(op, a_layer, result_layer, **kwargs)
            except LayoutError as e:
                error_var.set(str(e))
                return
            total = sum(written.values())
            made_visible = False
            if total > 0:
                # A mask op writing real output straight to a layer whose
                # visibility happens to be off is otherwise
                # indistinguishable from writing nothing at all -- the
                # canvas looks identical either way. Since the whole point
                # of running the operation was to see the result, turn the
                # result layer on rather than leaving it silently hidden.
                result_layer_obj = self.session.library.layers[result_layer]
                if not result_layer_obj.visible:
                    result_layer_obj.visible = True
                    made_visible = True
            self.refresh_all()
            if total == 0:
                self.status.set(
                    f"{op}: no output -- layer {a_layer} had nothing on it"
                    f"{'' if flatten_var.get() else ' (try Include placed cells)'}"
                )
            else:
                msg = f"{op}: wrote {total} polygon(s) to layer {result_layer}"
                if made_visible:
                    msg += " (layer visibility turned on)"
                self.status.set(msg)
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(6, 12))
        ttk.Button(btn_row, text="Apply", command=apply_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        win.bind("<Escape>", lambda e: win.destroy())

    def show_extract_netlist_dialog(self):
        """Tools > Extract Netlist...: pick the conductor layer(s) to
        treat as electrically connected copper, run
        Session.extract_netlist() against the current cell (or another
        cell), and show the resulting nets -- each as a comma-separated
        list of RefDes.Pin members (Rectangle.pin/Polygon.pin plus the
        placing Sref's Label). See hlayout.core.netlist for exactly how
        a net is determined; see Edit > Number Pins... and PAD/RESGEN's
        auto-numbering for how pins get set in the first place. Same
        operation as the command bar's NETLIST RUN command. Requires
        the shapely package."""
        win = tk.Toplevel(self)
        win.title("Extract Netlist")
        win.transient(self)
        win.grab_set()

        ttk.Label(
            win,
            text="Pick every conductor layer that's electrically connected copper "
                 "in this design (Ctrl/Shift-click for more than one). Touching or "
                 "overlapping shapes on those layers become one net; each net's "
                 "members are read from Rectangle/Polygon pin numbers (Edit > "
                 "Number Pins...) and the ref designator (Label) of the cell "
                 "instance that placed them.",
            wraplength=460, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        form = ttk.Frame(win)
        form.pack(padx=12, fill="x")

        ttk.Label(form, text="Conductor layers:").grid(row=0, column=0, sticky="ne", padx=4, pady=4)
        layers_list = tk.Listbox(form, selectmode="extended", height=8, exportselection=False)
        for n in range(len(self.session.library.layers)):
            layers_list.insert("end", f"{n} ({self.session.library.layers[n].name})")
        layers_list.grid(row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Cell:").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        cell_var = tk.StringVar(value=self.session.current_cell_name or "")
        cell_names = list(self.session.library.cells.keys())
        ttk.Combobox(form, textvariable=cell_var, state="readonly", width=22,
                     values=cell_names).grid(row=1, column=1, sticky="w", padx=4, pady=4)

        error_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=error_var, foreground="red", wraplength=460,
                  justify="left").pack(anchor="w", padx=12)

        columns = ("net", "members", "unresolved")
        results = ttk.Treeview(win, columns=columns, show="headings", height=10)
        results.heading("net", text="Net")
        results.heading("members", text="Members")
        results.heading("unresolved", text="Unresolved")
        results.column("net", width=90, anchor="w")
        results.column("members", width=340, anchor="w")
        results.column("unresolved", width=80, anchor="center")
        results.pack(padx=12, pady=(6, 6), fill="both", expand=True)

        state = {"nets": []}

        def run_extract():
            error_var.set("")
            sel = [layers_list.get(i).split(" ", 1)[0] for i in layers_list.curselection()]
            if not sel:
                error_var.set("Pick at least one conductor layer.")
                return
            layers = [int(n) for n in sel]
            for row in results.get_children():
                results.delete(row)
            try:
                nets = self.session.extract_netlist(layers, cell_name=cell_var.get() or None)
            except LayoutError as e:
                error_var.set(str(e))
                return
            state["nets"] = nets
            for net in nets:
                members = ", ".join(p.label() for p in net.pins) or "(no pins)"
                results.insert("", "end", values=(net.id, members, net.unresolved_count))
            self.status.set(f"Extract Netlist: {len(nets)} net(s) found")

        def export_csv():
            if not state["nets"]:
                error_var.set("Run extraction first.")
                return
            path = filedialog.asksaveasfilename(
                parent=win, title="Export Netlist CSV", initialfile="netlist.csv",
                defaultextension=".csv", filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")])
            if not path:
                return
            self.session.export_netlist_csv(state["nets"], path)
            self.status.set(f"Netlist exported to {path}")

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(0, 12))
        ttk.Button(btn_row, text="Run", command=run_extract).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Export CSV...", command=export_csv).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Close", command=win.destroy).pack(side="left", padx=6)

        win.bind("<Escape>", lambda e: win.destroy())

    def show_via_wizard(self):
        """Tools > Via Wizard...: create a via cell -- a square or round
        pad drawn on two layers, representing the contact area where a
        via connects them, optionally with a dielectric via window cut
        between them -- and save it directly into an attached reference
        library's own file, so it becomes a reusable part (rather than
        something that only lives in this design). Backed by
        Session.create_via_cell(); same operation as the command bar's
        VIA <layer1> <layer2> ... command.

        Cell name defaults to VIA_Layer<n1>_to_Layer<n2> (shown as a
        placeholder, updated live as Layer 1/2 change) but can be
        overridden -- e.g. to give a process-specific via more than one
        library part could otherwise collide on a distinct name.

        The dielectric via window (off by default -- a bare two-pad via
        has no dielectric) is a same-shape opening cut in a third layer
        between the two conductor layers, representing the actual
        electrical contact area in a real process; normally sized AT OR
        ABOVE the pad size, since the pad lands within/over the opening.
        """
        if not self.session.attached_libraries:
            dialogs.show_info(
                "Via Wizard",
                "No library is attached yet. Use Setup > Attach Library... to "
                "attach the GDSII file the via should be saved into, then try "
                "the Via Wizard again.",
                parent=self,
            )
            return

        win = tk.Toplevel(self)
        win.title("Via Wizard")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(
            win,
            text="Create a via cell -- a square or round pad drawn on both "
                 "layers, optionally with a dielectric via window -- and "
                 "save it into an attached library.",
            wraplength=380, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        form = ttk.Frame(win)
        form.pack(padx=12, fill="x")

        def layer_choices():
            return [f"{n} ({self.session.library.layers[n].name})"
                    for n in range(len(self.session.library.layers))]

        row = 0
        ttk.Label(form, text="Shape:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        shape_var = tk.StringVar(value="square")
        shape_row = ttk.Frame(form)
        shape_row.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        ttk.Radiobutton(shape_row, text="Square", value="square", variable=shape_var).pack(side="left")
        ttk.Radiobutton(shape_row, text="Round", value="round", variable=shape_var).pack(side="left", padx=(8, 0))
        row += 1

        ttk.Label(form, text="Size (mm):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        size_var = tk.DoubleVar(value=dbu_to_mm(100))
        ttk.Spinbox(form, from_=0.001, to=100.0, increment=0.001, width=10, textvariable=size_var).grid(
            row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        choices = layer_choices()
        ttk.Label(form, text="Layer 1:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        layer1_var = tk.StringVar(value=choices[0])
        ttk.Combobox(form, textvariable=layer1_var, width=20, state="readonly",
                     values=choices).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Layer 2:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        layer2_var = tk.StringVar(value=choices[1] if len(choices) > 1 else choices[0])
        ttk.Combobox(form, textvariable=layer2_var, width=20, state="readonly",
                     values=choices).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        # Cell name -- pre-filled with the default (VIA_Layer<n1>_to_
        # Layer<n2>), editable to give the via a different name. Keeps
        # tracking the default as Layer 1/2 change, but only until the
        # user actually types something of their own -- _name_edited
        # below flips permanently on the first manual edit, same
        # "auto-fill until the user overrides it" pattern used for
        # generated names elsewhere in this file (e.g. the resistor
        # designer's ref-des-derived cell name).
        def default_via_name():
            n1 = int(layer1_var.get().split(" ", 1)[0])
            n2 = int(layer2_var.get().split(" ", 1)[0])
            return f"VIA_Layer{n1}_to_Layer{n2}"

        ttk.Label(form, text="Cell name:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        name_var = tk.StringVar(value=default_via_name())
        name_entry = ttk.Entry(form, textvariable=name_var, width=22)
        name_entry.grid(row=row, column=1, sticky="w", padx=4, pady=4)

        _name_edited = [False]

        def _on_name_key(event=None):
            _name_edited[0] = True

        def _on_layer_choice_change(*_):
            if not _name_edited[0]:
                name_var.set(default_via_name())

        name_entry.bind("<Key>", _on_name_key)
        layer1_combo = form.grid_slaves(row=2, column=1)[0]
        layer2_combo = form.grid_slaves(row=3, column=1)[0]
        layer1_combo.bind("<<ComboboxSelected>>", _on_layer_choice_change)
        layer2_combo.bind("<<ComboboxSelected>>", _on_layer_choice_change)
        row += 1

        lib_choices = [f"[{l['index']}] {l['name']} ({l['path']})"
                       for l in self.session.list_attached_libraries()]
        ttk.Label(form, text="Save to library:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        lib_var = tk.StringVar(value=lib_choices[0])
        lib_combo = ttk.Combobox(form, textvariable=lib_var, width=28, state="readonly",
                                  values=lib_choices)
        lib_combo.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        if len(lib_choices) == 1:
            lib_combo.configure(state="disabled")
        row += 1

        ttk.Separator(form, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", padx=4, pady=(8, 4))
        row += 1

        dielectric_var = tk.BooleanVar(value=False)
        dielectric_check = ttk.Checkbutton(form, text="Add dielectric via window",
                                            variable=dielectric_var)
        dielectric_check.grid(row=row, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Dielectric layer:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        dielectric_layer_var = tk.StringVar(value=choices[2] if len(choices) > 2 else choices[0])
        dielectric_layer_combo = ttk.Combobox(form, textvariable=dielectric_layer_var, width=20,
                                               state="disabled", values=choices)
        dielectric_layer_combo.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Dielectric window size (mm):").grid(
            row=row, column=0, sticky="e", padx=4, pady=4)
        dielectric_window_var = tk.DoubleVar(value=dbu_to_mm(140))
        dielectric_window_spin = ttk.Spinbox(form, from_=0.001, to=100.0, increment=0.001,
                                              width=10, textvariable=dielectric_window_var,
                                              state="disabled")
        dielectric_window_spin.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1
        ttk.Label(
            form,
            text="Opening cut in the dielectric between Layer 1 and Layer 2,\n"
                 "normally sized AT OR ABOVE the pad size (the pad lands\n"
                 "within/over the opening).",
            foreground="#555", justify="left",
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=4)
        row += 1

        def _on_dielectric_toggle():
            state = "readonly" if dielectric_var.get() else "disabled"
            dielectric_layer_combo.configure(state=state)
            dielectric_window_spin.configure(state="normal" if dielectric_var.get() else "disabled")

        dielectric_check.configure(command=_on_dielectric_toggle)

        error_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=error_var, foreground="red", wraplength=380,
                  justify="left").pack(anchor="w", padx=12)

        def create_and_close():
            n1 = int(layer1_var.get().split(" ", 1)[0])
            n2 = int(layer2_var.get().split(" ", 1)[0])
            index = int(lib_var.get().split("]", 1)[0].lstrip("["))
            try:
                size = mm_to_dbu(float(size_var.get()))
            except (tk.TclError, ValueError):
                error_var.set("Size must be a number.")
                return
            if size <= 0:
                error_var.set("Size must be a positive number.")
                return

            name = name_var.get().strip()
            if not name:
                error_var.set("Cell name can't be blank.")
                return

            dielectric_layer = None
            dielectric_window = None
            if dielectric_var.get():
                n3 = int(dielectric_layer_var.get().split(" ", 1)[0])
                if n3 in (n1, n2):
                    error_var.set("Dielectric layer must be different from Layer 1 and Layer 2.")
                    return
                try:
                    dielectric_window = mm_to_dbu(float(dielectric_window_var.get()))
                except (tk.TclError, ValueError):
                    error_var.set("Dielectric window size must be a number.")
                    return
                if dielectric_window <= 0:
                    error_var.set("Dielectric window size must be positive.")
                    return
                dielectric_layer = n3

            def do_create(overwrite):
                return self.session.create_via_cell(
                    n1, n2, shape=shape_var.get(), size=size, index=index, overwrite=overwrite,
                    name=name, dielectric_layer=dielectric_layer, dielectric_window=dielectric_window,
                )

            try:
                created_name, path = do_create(overwrite=False)
            except LayoutError as e:
                if "already exists" in str(e):
                    if dialogs.ask_yes_no(
                        "Via Wizard",
                        f"A via cell named {name!r} already "
                        "exists in that library. Replace it?",
                        parent=self,
                    ):
                        try:
                            created_name, path = do_create(overwrite=True)
                        except LayoutError as e2:
                            error_var.set(str(e2))
                            return
                    else:
                        return
                else:
                    error_var.set(str(e))
                    return
            self.refresh_all()
            self.status.set(f"created via cell {created_name!r}, saved to {path}")
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(6, 12))
        ttk.Button(btn_row, text="Create", command=create_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        win.bind("<Escape>", lambda e: win.destroy())

    def show_pad_wizard(self):
        """Tools > Pad Wizard...: create a rectangular pad cell -- a
        conductor rectangle sized by length x width, plus a solder
        paste rectangle centred on it and undersized by a margin on
        every side -- and save it directly into an attached reference
        library's own file, so it becomes a reusable part. Optionally
        lays out a centred rows x cols grid of identical pads (e.g. a
        connector footprint) instead of just one. Backed by
        Session.create_pad_cell(); same operation as the command bar's
        PAD <name> <conductor_layer> <paste_layer> ... command."""
        if not self.session.attached_libraries:
            dialogs.show_info(
                "Pad Wizard",
                "No library is attached yet. Use Setup > Attach Library... to "
                "attach the GDSII file the pad should be saved into, then try "
                "the Pad Wizard again.",
                parent=self,
            )
            return

        win = tk.Toplevel(self)
        win.title("Pad Wizard")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(
            win,
            text="Create a pad cell -- a conductor rectangle with a solder "
                 "paste rectangle undersized on every side -- and save it "
                 "into an attached library. Set rows/cols > 1 for a grid of "
                 "identical pads (e.g. a connector footprint).",
            wraplength=380, justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        form = ttk.Frame(win)
        form.pack(padx=12, fill="x")

        def layer_choices():
            return [f"{n} ({self.session.library.layers[n].name})"
                    for n in range(len(self.session.library.layers))]

        row = 0
        ttk.Label(form, text="Cell name:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        name_var = tk.StringVar(value="")
        ttk.Entry(form, textvariable=name_var, width=22).grid(
            row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Length (mm, X):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        length_var = tk.DoubleVar(value=dbu_to_mm(mm_to_dbu(1.0)))
        ttk.Spinbox(form, from_=0.001, to=500.0, increment=0.001, width=10, textvariable=length_var).grid(
            row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Width (mm, Y):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        width_var = tk.DoubleVar(value=dbu_to_mm(mm_to_dbu(1.0)))
        ttk.Spinbox(form, from_=0.001, to=500.0, increment=0.001, width=10, textvariable=width_var).grid(
            row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Paste undersize (mm/side):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        margin_var = tk.DoubleVar(value=dbu_to_mm(mm_to_dbu(0.05)))
        ttk.Spinbox(form, from_=0.0, to=250.0, increment=0.001, width=10, textvariable=margin_var).grid(
            row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        choices = layer_choices()
        ttk.Label(form, text="Conductor layer:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        conductor_var = tk.StringVar(value=choices[0])
        ttk.Combobox(form, textvariable=conductor_var, width=20, state="readonly",
                     values=choices).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Solder paste layer:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        paste_var = tk.StringVar(value=choices[1] if len(choices) > 1 else choices[0])
        ttk.Combobox(form, textvariable=paste_var, width=20, state="readonly",
                     values=choices).grid(row=row, column=1, sticky="w", padx=4, pady=4)
        row += 1

        ttk.Label(form, text="Rows x Cols:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        grid_row = ttk.Frame(form)
        grid_row.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        rows_var = tk.IntVar(value=1)
        cols_var = tk.IntVar(value=1)
        ttk.Spinbox(grid_row, from_=1, to=100, width=5, textvariable=rows_var).pack(side="left")
        ttk.Label(grid_row, text=" x ").pack(side="left")
        ttk.Spinbox(grid_row, from_=1, to=100, width=5, textvariable=cols_var).pack(side="left")
        row += 1

        ttk.Label(form, text="Pitch Y / X (mm):").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        pitch_row = ttk.Frame(form)
        pitch_row.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        pitch_y_var = tk.DoubleVar(value=0.0)
        pitch_x_var = tk.DoubleVar(value=0.0)
        ttk.Spinbox(pitch_row, from_=0.0, to=500.0, increment=0.001, width=8, textvariable=pitch_y_var).pack(side="left")
        ttk.Label(pitch_row, text=" x ").pack(side="left")
        ttk.Spinbox(pitch_row, from_=0.0, to=500.0, increment=0.001, width=8, textvariable=pitch_x_var).pack(side="left")
        row += 1
        ttk.Label(
            form, text="(pitch only needed when rows/cols > 1)",
            foreground="gray",
        ).grid(row=row, column=1, sticky="w", padx=4)
        row += 1

        lib_choices = [f"[{l['index']}] {l['name']} ({l['path']})"
                       for l in self.session.list_attached_libraries()]
        ttk.Label(form, text="Save to library:").grid(row=row, column=0, sticky="e", padx=4, pady=4)
        lib_var = tk.StringVar(value=lib_choices[0])
        lib_combo = ttk.Combobox(form, textvariable=lib_var, width=28, state="readonly",
                                  values=lib_choices)
        lib_combo.grid(row=row, column=1, sticky="w", padx=4, pady=4)
        if len(lib_choices) == 1:
            lib_combo.configure(state="disabled")
        row += 1

        error_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=error_var, foreground="red", wraplength=380,
                  justify="left").pack(anchor="w", padx=12)

        def create_and_close():
            pad_name = name_var.get().strip()
            if not pad_name:
                error_var.set("Cell name is required.")
                return
            n_cond = int(conductor_var.get().split(" ", 1)[0])
            n_paste = int(paste_var.get().split(" ", 1)[0])
            index = int(lib_var.get().split("]", 1)[0].lstrip("["))
            try:
                length = mm_to_dbu(float(length_var.get()))
                width = mm_to_dbu(float(width_var.get()))
                margin = mm_to_dbu(float(margin_var.get()))
            except (tk.TclError, ValueError):
                error_var.set("Length, width, and paste undersize must be numbers.")
                return
            rows = rows_var.get()
            cols = cols_var.get()
            pitch_y = mm_to_dbu(float(pitch_y_var.get())) if pitch_y_var.get() else None
            pitch_x = mm_to_dbu(float(pitch_x_var.get())) if pitch_x_var.get() else None

            def do_create(overwrite):
                return self.session.create_pad_cell(
                    pad_name, n_cond, n_paste, length, width, margin,
                    rows=rows, cols=cols, pitch_x=pitch_x, pitch_y=pitch_y,
                    index=index, overwrite=overwrite,
                )

            try:
                name, path, pad_count = do_create(overwrite=False)
            except LayoutError as e:
                if "already exists" in str(e):
                    if dialogs.ask_yes_no(
                        "Pad Wizard",
                        f"A cell named {pad_name!r} already exists in that "
                        "library. Replace it?",
                        parent=self,
                    ):
                        try:
                            name, path, pad_count = do_create(overwrite=True)
                        except LayoutError as e2:
                            error_var.set(str(e2))
                            return
                    else:
                        return
                else:
                    error_var.set(str(e))
                    return
            self.refresh_all()
            self.status.set(f"created pad cell {name!r} ({pad_count} pad(s)), saved to {path}")
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(6, 12))
        ttk.Button(btn_row, text="Create", command=create_and_close).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="left", padx=6)

        win.bind("<Escape>", lambda e: win.destroy())

    def show_attached_libraries(self):
        """Setup > Attached Libraries...: view every reference library
        currently attached (Session.attached_libraries -- up to
        MAX_ATTACHED_LIBRARIES, 2) and detach one to free its slot for
        Attach Library... . Detaching only removes it from the search/
        attach list -- any cells already copied into this design via
        Find & Place Cell (Session.import_cell_closure(), a real,
        independent deep copy) are completely unaffected, so detaching
        never touches anything already placed."""
        win = tk.Toplevel(self)
        win.title("Attached Libraries")
        win.transient(self)
        win.grab_set()
        win.resizable(False, False)

        ttk.Label(
            win,
            text=f"Up to {self.session.MAX_ATTACHED_LIBRARIES} reference libraries may be attached at once.",
            foreground="#8b949e",
        ).pack(anchor="w", padx=10, pady=(10, 4))

        columns = ("index", "name", "cells", "path")
        tree = ttk.Treeview(win, columns=columns, show="headings", height=4, selectmode="browse")
        for col, label, width in (
            ("index", "#", 30), ("name", "Name", 140), ("cells", "Cells", 60), ("path", "Path", 380),
        ):
            tree.heading(col, text=label)
            tree.column(col, width=width, anchor="w" if col in ("name", "path") else "center")
        tree.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        def refresh():
            for row in tree.get_children():
                tree.delete(row)
            libs = self.session.list_attached_libraries()
            for l in libs:
                tree.insert("", "end", iid=str(l["index"]),
                             values=(l["index"], l["name"], l["cells"], l["path"]))
            empty_var.set("No libraries attached." if not libs else "")
            detach_btn.config(state="disabled" if not libs else "normal")

        empty_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=empty_var, foreground="#8b949e").pack(anchor="w", padx=10)

        def detach_selected():
            sel = tree.selection()
            if not sel:
                return
            idx = int(sel[0])
            libs = self.session.list_attached_libraries()
            name = next((l["name"] for l in libs if l["index"] == idx), "?")
            if not dialogs.ask_yes_no(
                "Detach Library",
                f"Detach {name!r}? Anything already placed from it stays in the "
                "design untouched -- this only removes it from the attach list.",
                parent=win,
            ):
                return
            self.session.detach_library(idx)
            self.status.set(f"detached library {idx}: {name!r}")
            refresh()

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=10, pady=(0, 10))
        detach_btn = ttk.Button(btn_row, text="Detach", command=detach_selected)
        detach_btn.pack(side="left")
        ttk.Button(btn_row, text="Close", command=win.destroy).pack(side="right")

        refresh()
        win.bind("<Escape>", lambda e: win.destroy())

    def find_and_place_cell(self):
        win = tk.Toplevel(self)
        win.title("Find & Place Cell")
        win.geometry("420x440")

        ttk.Label(win, text="Search cell/Sref name:").pack(anchor="w", padx=8, pady=(8, 0))
        query_var = tk.StringVar()
        entry = ttk.Entry(win, textvariable=query_var)
        entry.pack(fill="x", padx=8)

        listbox = tk.Listbox(win)
        listbox.pack(fill="both", expand=True, padx=8, pady=8)
        matches = []

        def do_search(event=None):
            matches[:] = self.session.find_cells(query_var.get())
            listbox.delete(0, tk.END)
            for m in matches:
                src = "current" if m["source"] == "current" else f"lib {m['source']}"
                listbox.insert(tk.END, f"{m['name']}  ({src})")

        entry.bind("<Return>", do_search)
        entry.bind("<KeyRelease>", do_search)
        entry.focus_set()
        do_search()

        coord_row = ttk.Frame(win)
        coord_row.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(coord_row, text="Place at x,y:").pack(side="left")
        coord_var = tk.StringVar(value="0,0")
        ttk.Entry(coord_row, textvariable=coord_var, width=16).pack(side="left", padx=4)

        def do_place(event=None):
            sel = listbox.curselection()
            if not sel:
                dialogs.show_error("Find & Place Cell", "Select a cell from the list first.", parent=self)
                return
            m = matches[sel[0]]
            try:
                origin = _parse_point(coord_var.get())
            except CommandError as e:
                dialogs.show_error("Find & Place Cell", str(e), parent=self)
                return
            try:
                self.session.place_cell(m["name"], origin, source=m["source"])
            except LayoutError as e:
                dialogs.show_error("Find & Place Cell", str(e), parent=self)
                return
            # Placing a cell should make everything in it visible right
            # away, not leave it off-screen until the user separately
            # hits Fit — so refresh the panels, then fit the view to
            # include the newly placed geometry.
            self.refresh_all()
            self.canvas.fit_view()
            win.destroy()

        # Selecting a match and confirming with double-click or Enter
        # places it directly, without needing to also click the button.
        listbox.bind("<Double-Button-1>", do_place)
        listbox.bind("<Return>", do_place)

        ttk.Button(win, text="Place", command=do_place).pack(padx=8, pady=(0, 8))

    def show_command_reference(self):
        """Open the full HTML command bar + Python API reference
        (docs/help.html — every command and every Session method, each
        with a runnable example, in a searchable page) in the system's
        default web browser. Falls back to the old scrollable
        plain-text dialog (_show_command_reference_fallback) if that
        file can't be found -- e.g. a copy of the app distributed
        without its docs/ folder -- so Help > Command Reference always
        shows *something* even then."""
        # hlayout/gui/app.py -> hlayout/gui -> hlayout -> repo root -> docs/help.html
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        help_path = os.path.join(repo_root, "docs", "help.html")
        if os.path.isfile(help_path):
            webbrowser.open(f"file://{help_path}")
            return
        self._show_command_reference_fallback()

    def _show_command_reference_fallback(self):
        """The old scrollable plain-text command reference dialog --
        kept only as a fallback for show_command_reference() above when
        docs/help.html isn't present alongside the app."""
        win = tk.Toplevel(self)
        win.title("Command Reference")
        win.geometry("760x520")

        text_frame = ttk.Frame(win)
        text_frame.pack(fill="both", expand=True, padx=8, pady=8)
        hbar = ttk.Scrollbar(text_frame, orient="horizontal")
        text = tk.Text(text_frame, wrap="none", bg="#111", fg="#ddd", insertbackground="#ddd",
                        font=("TkFixedFont",), xscrollcommand=hbar.set)
        hbar.config(command=text.xview)
        hbar.pack(side="bottom", fill="x")
        text.pack(fill="both", expand=True)
        text.insert("1.0", COMMAND_REFERENCE)
        text.configure(state="disabled")

        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 8))

    def refresh_all(self):
        self.canvas.redraw()
        self._refresh_cell_list()
        self._refresh_layer_choices()
        if hasattr(self, "layer_panel"):
            self.layer_panel.sync()
        self._update_properties_panel()

    def _update_properties_panel(self):
        """Refresh the Properties tab with a description of whatever is
        currently selected on the canvas -- wired as canvas.on_select
        (fires on every click/deselect/window-select) and called from
        refresh_all() so it also stays current after undo/redo, opening
        a file, switching cells, etc. See canvas.describe_selection()
        for the actual formatting, shared with the PROPS command."""
        if not hasattr(self, "props_text"):
            return
        text = describe_selection(self.canvas)
        self.props_text.config(state="normal")
        self.props_text.delete("1.0", "end")
        self.props_text.insert("1.0", text)
        self.props_text.config(state="disabled")

    def _on_quit(self):
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
