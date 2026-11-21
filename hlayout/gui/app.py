# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Main application window for the Thick-Film Hybrid Layout CAD tool."""

import os
import time
import webbrowser
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, simpledialog, ttk

from . import dialogs

from ..core.errors import GeometryError, LayoutError
from ..core.library import Library
from ..core.units import dbu_to_mm, mm_to_dbu
from ..io.printio import hidden_layers_with_data, send_to_system_printer
from ..io.sidecar import backup_path as _backup_path, load_design
from ..io.techfile import load_route_rules
from ..scripting.api import Session
from ..scripting.console import ScriptConsole
from .canvas import LayoutCanvas, describe_selection
from .commands import CommandError, CommandInterpreter, _parse_point
from .layer_panel import LayerPanel
from .line_editing import bind_console_style_editing
from .layer_util import layer_choices, layer_label
from ..core.layers import NUM_LAYERS
from .bug_report import get_log as _bug_log, install as _install_bug_log, open_bug_report_window
from .app_wizards import WizardsMixin, chip_pin_names  # noqa: F401 (re-exported: tests)
from .app_setup_dialogs import SetupDialogsMixin
from .app_library_dialogs import LibraryDialogsMixin
from .app_exports import ExportsMixin
from .app_hybrid import HybridCommandsMixin
from .app_menubars import MenuBarsMixin
from .app_help import HelpMixin


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



# App's methods by area: the window itself (layout, canvas commits, file
# open / save / autosave, undo, cell lists, properties) is below; the rest
# lives in the app_*.py mixins it inherits -- each one's methods are App's.
class App(
    HelpMixin,            # app_help: About, user manual, command reference
    MenuBarsMixin,        # app_menubars: menu bar, toolbars, context bar
    HybridCommandsMixin,  # app_hybrid: hybrid commands and canvas tools
    ExportsMixin,         # app_exports: FreeCAD, pick-and-place, archive, mask ops
    LibraryDialogsMixin,  # app_library_dialogs: attached libraries, library cells
    SetupDialogsMixin,    # app_setup_dialogs: Technology Properties and setup
    WizardsMixin,         # app_wizards: via / pad / chip wizards
    tk.Tk,
):
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
        self.title("HLayout — Thick-Film Hybrid Layout CAD")

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
        self.canvas.on_edit = self._after_canvas_edit
        self.canvas.on_mode_status = self.status.set
        self.canvas.on_list_cells = self._placeable_cell_names
        self.canvas.checkpoint = self._canvas_checkpoint
        self.canvas.on_select = self._update_properties_panel
        self._update_properties_panel()
        self.canvas.on_route_via = self._on_route_via
        self.canvas.on_auto_route = self._on_auto_route
        self.canvas.on_chip_pin = self._on_chip_pin
        self.canvas.on_chip_pins_done = self._on_chip_pins_done
        self.canvas.on_chip_pins_cancel = self._on_chip_pins_cancel
        self.canvas.on_draw_mode_changed = lambda mode: self.mode_var.set(mode)
        self.canvas.on_request_mode = self._pick_mode
        self._chip_wizard = None
        self._on_route_opts_change()

        self.interp = CommandInterpreter(
            self.session, self.console, on_change=self.refresh_all, view=self.canvas
        )
        self.session.on_change(self.refresh_all)
        self.canvas.retrace_net_highlight = self.session.retrace_net_highlight
        self.session.on_change(self._update_undo_redo_menu_state)
        self._update_undo_redo_menu_state()
        self.after_idle(self._restore_sli)
        # Help > Report Bug...: start recording commands, status
        # messages, uncaught exceptions and stderr for this session so a
        # report shows the lead-up to a problem (see gui/bug_report.py).
        _install_bug_log(self)

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
            _bug_log().exception(exc, val, tb, where="Tk callback")
        except Exception:
            pass
        try:
            if dialogs.ask_yes_no(
                    "Unexpected Error",
                    f"{summary}\n\n{frames}\n"
                    "Report this bug? The error details are added for you.",
                    parent=self):
                open_bug_report_window(self, summary=summary[:120])
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
        #: bold, UI_FONT_SIZE -- the bottom message/status line uses this
        self._status_font = (default_family, self.UI_FONT_SIZE, "bold")

    def _default_library(self) -> Library:
        lib = Library("UNTITLED")
        lib.new_cell("TOP")
        return lib


    def fit_view_when_shown(self):
        """Fit the whole design in the canvas as soon as the window is
        actually on screen -- for a design opened from the command line
        (hlayout_app.py mydesign.gds). Fitting earlier, while the canvas
        still has its 1x1 pre-layout size, computes a scale for a one-
        pixel view, so the design came up as a dot in the top-left
        corner. Waits for the canvas's first real <Configure> size (or
        retries briefly), fits once, then lets go."""
        state = {"done": False}

        def _try(_event=None):
            if state["done"]:
                return
            if self.canvas.winfo_width() > 50 and self.canvas.winfo_height() > 50:
                state["done"] = True      # later resizes: _try is a no-op
                self.canvas.fit_view()

        self.canvas.bind("<Configure>", _try, add="+")
        for delay in (50, 200, 500, 1000, 2000):
            self.after(delay, _try)

    def _show_layers_tab(self):
        self.right_notebook.select(self.layer_panel)

    def _show_console_tab(self):
        self.right_notebook.select(self.console_frame)

    def _show_properties_tab(self):
        self.right_notebook.select(self.props_frame)

    # --- undo/redo ---------------------------------------------------------
    def _undo(self):
        # While a path/polygon/route is being drawn, Undo backs it off
        # one point at a time (same as Backspace) rather than undoing
        # whatever was in the design before the draw started.
        if self.canvas.undo_draw_point():
            return
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
        """Edit > Rotate 90 deg: turn the selection 90 deg counter-
        clockwise about its centroid. No dialog -- click again (or press
        R) to keep turning; four clicks is a full turn. For an arbitrary
        angle, use the ROTATE <degrees> command."""
        if self.canvas.selected is None and not self.canvas.selected_multi:
            dialogs.show_info("Rotate", "Nothing is selected. Switch to Select mode, click the "
                               "shape or cell instance to rotate (it turns white), or drag a "
                               "box over empty space to window-select several, then try again.",
                               parent=self)
            return
        if not self.canvas.rotate_selected(90):
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
        """Create > Create Cell from Selection...: the classic Calma GDSII
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
        name = name.strip()
        replace = False
        if name in self.session.library.cells:
            if name == self.canvas.cell_name:
                dialogs.show_error(
                    "Create Cell from Selection",
                    f"{name!r} is the cell you're editing -- it can't be replaced "
                    "with a selection from inside itself.", parent=self)
                return
            rev = self.session.library._unique_cell_name(name)
            choice = dialogs.ask_choice(
                "Create Cell from Selection",
                f"A cell named {name!r} already exists.\n\n"
                f"New revision  -  save as {rev!r}; {name!r} and every instance of "
                f"it stay exactly as they are, so you can swap in the new version "
                f"where you want it.\n\n"
                f"Replace  -  rebuild {name!r} in place; every instance of it in "
                f"the design switches to the new geometry.",
                parent=self,
                choices=[("revision", f"New revision ({rev})"),
                         ("replace", "Replace"), (None, "Cancel")],
            )
            if choice == "revision":
                name = rev
            elif choice == "replace":
                replace = True
            else:
                return
        # Rather than typing an origin, pick it with a click: the tool
        # enters "cell_origin" mode and the next canvas click is the
        # point that becomes the new cell's local (0, 0).
        if not self.canvas.start_cell_from_selection(name, replace=replace):
            dialogs.show_info(
                "Create Cell from Selection", "That selection can't become a cell -- "
                "select a whole shape or cell instance (not a single vertex/edge).",
                parent=self,
            )
            return
        # The mode is otherwise silent apart from the status line -- tell
        # the user the next step is to click the cell's origin.
        dialogs.show_info(
            "Create Cell from Selection",
            f"Now click a point on the canvas to set the origin of {name!r}.\n\n"
            "That point becomes local (0, 0) inside the new cell -- pick a "
            "reference feature such as a pad centre or a corner. Press Esc to "
            "cancel.",
            parent=self,
        )
        self.status.set(
            f"Create Cell {name!r}: click the point to use as the cell's origin (Esc cancels)")

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
        self.canvas.sli_provider = lambda: self.session.sli(cell_name=self.canvas.cell_name)
        self.canvas.on_vias_moved = self._refill_dielectric_after_via_move
        from .hybrid_hooks import HybridHooks
        self.canvas.hybrid_hooks = HybridHooks(self)
        # weight=1 on the canvas, weight=0 on the right panel: when the
        # window is resized, all the extra/lost space goes to the canvas
        # and the Layers/Console panel stays anchored at a fixed width on
        # the right edge instead of stretching with it.
        body.add(self.canvas, weight=1)

        # The side-panel tabs get their own style so they read as proper
        # tabs at the app's UI font size rather than the small cramped
        # theme default: roomier padding, the selected tab lifted a
        # little (expand) and drawn in the accent colour, unselected
        # tabs a softer grey. Bold-on-select is avoided on purpose --
        # it changes the tab's width and makes the row jump on click.
        side_style = ttk.Style(self)
        side_style.configure("Side.TNotebook", tabmargins=(4, 6, 4, 0), padding=(0, 2, 0, 0))
        side_style.configure("Side.TNotebook.Tab", padding=(20, 7), font=self._menu_font)
        side_style.map("Side.TNotebook.Tab",
                       foreground=[("selected", "#1f5fbf"), ("active", "#1f1f1f"),
                                   ("!selected", "#555555")],
                       expand=[("selected", (3, 4, 3, 0))])
        right = ttk.Notebook(body, width=self.RIGHT_PANEL_WIDTH, style="Side.TNotebook")
        body.add(right, weight=0)
        self.right_notebook = right  # so menu items (Setup > Layers..., Tools > Script Console) can switch tabs

        self.layer_panel = LayerPanel(right, self.session.library,
                                       on_change=self._on_layer_panel_change)
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
        right.add(console_frame, text="Console")
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





    def _refdes_to_pin1(self):
        """Edit > Ref-Des to Pin 1: the selected instance (or the one
        whose ref-des is selected); with nothing selected, every
        connector/header in the cell (J, P, X, CN, CON, HDR)."""
        sel = self.canvas.selected
        target = None
        if sel is not None and sel[0] in ("sref", "sref_label", "sref_reftext"):
            target = sel[1]
        try:
            moved = self.session.refdes_to_pin1(target)
        except LayoutError as e:
            self.status.set(str(e))
            return
        self.canvas.redraw()
        self.status.set("Ref-des moved beside pin 1: " + ", ".join(moved) if moved
                        else "No part with a pin 1 to move a ref-des to"
                        + ("" if target is not None else " (no connector/header labels found)"))

    def _pick_mode(self, mode):
        """Switch the canvas tool and keep the menu radio buttons, mode
        hint and context bar in step (used by canvas keys such as P)."""
        self.mode_var.set(mode)
        self._on_mode_change()

    def show_pan_to_dialog(self):
        """View > Pan to Point...: centre the view on typed X/Y (mm)
        without changing the zoom. Same as the PAN TO command."""
        win = tk.Toplevel(self)
        win.title("Pan to Point")
        win.transient(self)
        win.resizable(False, False)
        cx, cy = self.canvas.view_center()
        x_var = tk.StringVar(value=f"{dbu_to_mm(cx):.3f}")
        y_var = tk.StringVar(value=f"{dbu_to_mm(cy):.3f}")
        form = ttk.Frame(win)
        form.pack(padx=12, pady=(12, 4))
        ttk.Label(form, text="X (mm):").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        x_entry = ttk.Entry(form, textvariable=x_var, width=12)
        x_entry.grid(row=0, column=1, padx=4, pady=4)
        ttk.Label(form, text="Y (mm):").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(form, textvariable=y_var, width=12).grid(row=1, column=1, padx=4, pady=4)
        err = tk.StringVar(value="")
        ttk.Label(win, textvariable=err, foreground="red").pack(padx=12)

        def go(event=None):
            try:
                x, y = mm_to_dbu(float(x_var.get())), mm_to_dbu(float(y_var.get()))
            except ValueError:
                err.set("X and Y must be numbers (mm).")
                return
            self.canvas.pan_to(x, y)
            self.status.set(f"View centred on {float(x_var.get()):.3f}, {float(y_var.get()):.3f} mm")
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(pady=(4, 12))
        ttk.Button(btns, text="Pan", command=go).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=6)
        win.bind("<Return>", go)
        win.bind("<Escape>", lambda e: win.destroy())
        x_entry.focus_set()
        x_entry.select_range(0, "end")

    def _on_mode_change(self):
        self.canvas.set_mode(self.mode_var.get())
        self.mode_hint_var.set(self.canvas._mode_hint())
        self._refresh_context_bar()


    def _layer_label(self, number):
        return layer_label(self.session.library, number, colon=True)

    def _layer_choices(self):
        return layer_choices(self.session.library, colon=True, count=NUM_LAYERS)

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


    def _on_circle_opts_change(self):
        try:
            self.canvas.circle_segments = int(self.segments_var.get())
        except (tk.TclError, ValueError):
            pass


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





    def _on_solid_fills_change(self):
        """View > Solid Layer Fills: solid blended colours instead of stipple
        patterns (Tk on Windows can leave a stippled pad looking empty after a
        partial repaint). Remembered for next time."""
        self.canvas.solid_fills = bool(self.solid_fills_var.get())
        try:
            from ..io.app_settings import set_setting
            set_setting("solid_fills", self.canvas.solid_fills)
        except Exception:
            pass
        self.canvas.redraw()

    def _on_show_pins_change(self):
        self.canvas.show_pin_numbers = self.show_pins_var.get()
        self.canvas.redraw()

    def _on_show_sli_change(self):
        """View > Straight-Line Interconnects: every named net drawn pad to
        pad along its shortest tree; total length on the status bar."""
        self.set_sli(self.show_sli_var.get())

    def recalc_sli(self):
        """View > Recalculate SLI (F5, SLI RECALC): work the straight-line
        interconnects out again and report the total; turns them on."""
        try:
            from ..io.app_settings import set_setting
            set_setting(self.SLI_SETTING_KEY, True)
        except Exception:
            pass
        self.canvas.show_sli = True
        if hasattr(self, "show_sli_var"):
            self.show_sli_var.set(True)
        self.canvas.recalc_sli()
        d = self.canvas.sli_last or self.session.sli(cell_name=self.canvas.cell_name)
        self.status.set(f"Straight-line interconnects recalculated: {len(d['nets'])} nets, "
                        f"total {d['total'] / 1000:.1f} mm")

    SLI_SETTING_KEY = "show_sli"

    def _restore_sli(self, report: bool = True):
        """Straight-line interconnects on or off as last chosen -- the
        choice is remembered across opening a design and restarting."""
        try:
            from ..io.app_settings import get_setting
            on = bool(get_setting(self.SLI_SETTING_KEY))
        except Exception:
            return
        self.canvas.sli_last = None
        self.canvas._sli_sig = None
        if report:
            self.set_sli(on)
        else:
            self.canvas.show_sli = on
            if hasattr(self, "show_sli_var"):
                self.show_sli_var.set(on)
            self.canvas.redraw()

    def set_sli(self, on: bool):
        try:
            from ..io.app_settings import set_setting
            set_setting(self.SLI_SETTING_KEY, bool(on))
        except Exception:
            pass
        self.canvas.show_sli = bool(on)
        if hasattr(self, "show_sli_var") and self.show_sli_var.get() != bool(on):
            self.show_sli_var.set(bool(on))
        self.canvas.redraw()
        if on:
            d = self.canvas.sli_last or self.session.sli(cell_name=self.canvas.cell_name)
            if not d["nets"]:
                self.status.set("Straight-line interconnects: no named nets -- import net "
                                "names or run Tools > Auto Place...")
            else:
                self.status.set(f"Straight-line interconnects: {len(d['nets'])} nets, "
                                f"total {d['total'] / 1000:.1f} mm")

    def _on_snap_change(self):
        self.canvas.snap_enabled = self.snap_var.get()

    def _on_ortho_change(self):
        self.canvas.ortho_enabled = self.ortho_var.get()

    def _refresh_grid_spacing_var(self):
        """Keep the toolbar's "Grid: N mm" readout in sync with
        self.canvas.grid_dbu -- called on startup and after Grid
        Spacing... changes it."""
        self.grid_spacing_var.set(f"Grid: {dbu_to_mm(self.canvas.grid_dbu):g} mm")




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
        if self.canvas.selected is None or \
                self.canvas.selected[0] not in ("shape", "edge", "vertex"):
            dialogs.show_info(
                "Continue Path", "Nothing suitable is selected. Switch to Select "
                "mode and click the path (near the end you want to carry on from).",
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
            elif mode == "wire_bond":
                self.session.wire_bond(points)
            elif mode == "del_bond":
                e1, e2 = self.session.delete_wire_bond(points[0])
                self.status.set(
                    f"wire bond {e1} - {e2} removed -- click another, or Esc to stop"
                )
            elif mode == "ripup":
                tol = 6.0 / max(self.canvas.scale, 1e-9)
                r = self.session.rip_up_between(points[0], points[1],
                                                cell_name=self.canvas.cell_name, tol=tol)
                self.canvas.start_reroute(r["a"][:2], r["b"][:2], r["a"][2], net=r.get("net"))
                self.status.set(
                    f"ripped up {r['length'] / 1000:.2f} mm of "
                    f"{('net ' + r['net']) if r.get('net') else 'track'}"
                    f"{', ' + str(len(r['vias'])) + ' via(s)' if r['vias'] else ''} -- "
                    f"route from here to the ringed end (Ctrl+Z puts it back)")
            elif mode == "hilite":
                # a click a few pixels off a pad or a thin track still picks it
                tol = 6.0 / max(self.canvas.scale, 1e-9)
                tr = self.session.highlight_net(points[0], prefer_layer=layer, tol=tol)
                self.status.set(self._net_summary(tr))
            elif mode == "del_via":
                centre, layers = self.session.delete_via(points[0])
                self.status.set(
                    f"via at {centre} removed (layers {', '.join(map(str, layers))}) "
                    f"-- click another, or Esc to stop"
                )
            elif mode == "move_bond":
                near, to = points
                fixed, moved = self.session.move_wire_bond(near, to)
                self.status.set(
                    f"wire bond end moved to {moved} (other end {fixed}) "
                    f"-- move another, or Esc to stop"
                )
            elif mode == "move_finger":
                grab, to = points
                tol = 6.0 / max(self.canvas.scale, 1e-9)
                r = self.session.move_bond_finger(grab, to, tol=tol)
                msg = f"finger {r['pad']} moved to {r['to']}"
                if r["warnings"]:
                    msg += " -- " + "; ".join(r["warnings"])
                if not r["on_trace"]:
                    msg += " -- now off its trace: reroute the net"
                self.status.set(msg + " -- move another, or Esc to stop")
            elif mode == "resize_res":
                from .hybrid_hooks import new_resistor_length
                grab, to = points
                pick = self.session.resistor_at(grab)
                if pick is None:
                    raise LayoutError("no printed resistor there")
                r = self.session.resize_resistor(pick["refdes"],
                                                 length=new_resistor_length(pick, to))
                msg = r["text"]
                if r["pins_moved_um"]:
                    msg += f" -- pins moved {r['pins_moved_um']:.0f} um: reroute their nets"
                self.status.set(msg)
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
            if mode == "hilite":
                # a missed click while highlighting isn't worth a modal box
                self.status.set(f"Highlight Net: {e}")
                return
            title = {"del_bond": "Delete Wire Bond",
                     "move_bond": "Move Wire Bond",
                     "del_via": "Delete Via",
                     "ripup": "Rip Up and Reroute",
                     "move_finger": "Move Bond Finger",
                     "resize_res": "Resize Resistor"}.get(mode, "Could not add shape")
            dialogs.show_error(title, str(e), parent=self)

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
        ttk.Label(bar, textvariable=self.status, anchor="w",
                  font=self._status_font).pack(fill="x", padx=4)

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
    def _close_library_bound_tool_windows(self):
        """Tear down the singleton tool windows that capture the current
        design's Library in their constructor (Resistor/Thin-Film
        Designer, DRC, Mask-Op Batch, Gerber/DXF Export Sets). Called
        when the session's library is replaced (New / Open GDS) so the
        next open rebuilds them against -- and, for the Resistor
        Designer, reloads its saved list from -- the new design rather
        than silently operating on the discarded one."""
        for attr in ("_resistor_designer_window", "_thin_film_designer_window",
                     "_drc_window", "_netlist_window", "_topview_window",
                     "_maskop_batch_window",
                     "_gerber_batch_window", "_dxf_batch_window",
                     "_thermal_window", "_unrouted_window"):
            win = getattr(self, attr, None)
            if win is not None:
                try:
                    if win.winfo_exists():
                        win.destroy()
                except Exception:
                    pass
                setattr(self, attr, None)

    def new_library(self):
        self._close_library_bound_tool_windows()
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
        self._close_library_bound_tool_windows()
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
        self._restore_sli(report=False)
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
        if not self._ok_to_overwrite(path):
            return
        try:
            self.session.save_design(path)
        except LayoutError as e:
            dialogs.show_error("Save failed", str(e), parent=self)
            return
        self.current_path = path
        self.status.set(f"saved {path} (+ technology/attached-libraries sidecar; "
                        f"the previous version kept as {os.path.basename(_backup_path(path, 1))})")








    def _ok_to_overwrite(self, path) -> bool:
        """Saving an EMPTY design over a file that has a design in it is
        almost always a mistake (a fresh window saved over yesterday's
        work): ask first."""
        from ..io.sidecar import design_is_empty
        try:
            if not design_is_empty(self.session.library) or not os.path.isfile(path) \
                    or os.path.getsize(path) < 300:
                return True
        except OSError:
            return True
        return dialogs.ask_yes_no(
            "Save an empty design?",
            f"This design is empty, and {os.path.basename(path)} already holds a design.\n\n"
            f"Overwrite it with the empty design? (The current file would still be kept as "
            f"{os.path.basename(_backup_path(path, 1))}.)",
            parent=self)

    #: Milliseconds between auto-saves while the top toolbar's
    #: Auto-save switch is on -- see _on_autosave_toggle().
    AUTOSAVE_INTERVAL_MS = 15 * 60 * 1000

    #: App-settings key the Auto-save switch's on/off state is kept
    #: under (absent = on, the default).
    AUTOSAVE_SETTING_KEY = "autosave"

    def _on_autosave_toggle(self):
        """Top toolbar > Auto-save (15 min): flips the recurring
        self.after() timer on/off. Turning it on schedules the first
        tick AUTOSAVE_INTERVAL_MS from now (not immediately -- flipping
        the switch isn't itself a reason to save right away); turning
        it off cancels whatever's pending. A per-session setting only,
        not saved into the technology file or restored on reopen."""
        self._cancel_autosave()
        try:
            from ..io.app_settings import load_app_settings, save_app_settings
            settings = load_app_settings()
            settings[self.AUTOSAVE_SETTING_KEY] = bool(self.autosave_var.get())
            save_app_settings(settings)
        except OSError:
            pass  # not being able to remember the choice must not block it
        if self.autosave_var.get():
            self._schedule_autosave()
            self.status.set("auto-save on -- every 15 minutes while a file is open")
        else:
            self.status.set("auto-save off")

    def _schedule_autosave(self):
        self._autosave_job = self.after(self.AUTOSAVE_INTERVAL_MS, self._run_autosave)

    def _cancel_autosave(self):
        job = getattr(self, "_autosave_job", None)
        if job is not None:
            self.after_cancel(job)
        self._autosave_job = None

    def _run_autosave(self):
        """Fires every AUTOSAVE_INTERVAL_MS while the switch is on.
        Silent by design -- no dialog, just a status-line note -- since
        it runs unattended and a save succeeding or being skipped is
        exactly the kind of thing that shouldn't interrupt whatever the
        user is doing. Skips (without disarming the switch) when the
        design has never been saved yet -- current_path is what "where"
        an auto-save writes to is defined from, same as Save GDS...,
        and there's nothing to silently prompt a location for here."""
        self._autosave_job = None
        from ..io.sidecar import design_is_empty
        if self.current_path and design_is_empty(self.session.library) and \
                os.path.isfile(self.current_path) and os.path.getsize(self.current_path) >= 300:
            self.status.set("auto-save skipped -- the design is empty and "
                            f"{os.path.basename(self.current_path)} isn't")
        elif self.current_path:
            try:
                self.session.save_design(self.current_path)
                self.status.set(f"auto-saved {self.current_path} "
                                f"at {time.strftime('%H:%M:%S')}")
            except LayoutError as e:
                self.status.set(f"auto-save failed: {e}")
        else:
            self.status.set("auto-save skipped -- save the design once "
                            "(File > Save GDS...) to give it a location first")
        if self.autosave_var.get():
            self._schedule_autosave()

    def save_as_gds(self):
        """File > Save As...: always prompt for a location -- pre-filled
        with the current file's folder/name, if any -- and save the
        design there (design + technology/attached-libraries sidecar,
        exactly like Save GDS...). Unlike Save GDS..., this never
        silently reuses current_path even when one is already set, so
        it's the way to save a copy under a new name or move the open
        design to a different folder. The new path becomes current_path,
        so Save/Ctrl+S from here on writes there, not the old location
        -- the old file on disk is left untouched."""
        initial_dir = os.path.dirname(self.current_path) if self.current_path else None
        initial_file = os.path.basename(self.current_path) if self.current_path else ""
        path = filedialog.asksaveasfilename(
            defaultextension=".gds", filetypes=[("GDSII", "*.gds")],
            initialdir=initial_dir, initialfile=initial_file)
        if not path:
            return
        if not self._ok_to_overwrite(path):
            return
        try:
            self.session.save_design(path)
        except LayoutError as e:
            dialogs.show_error("Save As failed", str(e), parent=self)
            return
        self.current_path = path
        self.status.set(f"saved {path} (+ technology/attached-libraries sidecar)")


    def new_view_window(self):
        ViewWindow(self, self.session.library, title=f"View - {self.session.current_cell_name}",
                   checkpoint=self.session.checkpoint)











    def print_current_view(self):
        """File > Print... (Ctrl+P): render the current cell as a
        printable HTML page (Session.print_cell() -- a legend plus an
        SVG drawing of its visible geometry at true physical scale)
        and hand it straight to the OS's own print flow
        (hlayout.io.printio.send_to_system_printer() --
        os.startfile(path, "print") on Windows), since Tkinter has no
        print dialog of its own. Falls back to just opening the page
        in the default browser if that handoff isn't available on this
        platform/setup -- Ctrl+P there still prints it."""
        try:
            path = self.session.print_cell()
        except LayoutError as e:
            dialogs.show_error("Print", str(e), parent=self)
            return
        if send_to_system_printer(path):
            msg = f"sent to the system printer: {path}"
        else:
            webbrowser.open(path)
            msg = (f"opened {path} for printing -- use Ctrl+P "
                   f"(no system print handoff available here)")
        hidden = hidden_layers_with_data(self.session.library, self.session.current_cell_name)
        if hidden:
            names = ", ".join(str(n) for n in hidden)
            msg += f" (layer(s) {names} have geometry here but are hidden -- not printed)"
        self.status.set(msg)



    def _refresh_cell_lists(self):
        """Open windows that list the usable cells pick up a library just
        attached or detached."""
        w = getattr(self, "_autoplace_window", None)
        try:
            if w is not None and w.winfo_exists():
                w.refresh_cells()
        except Exception:
            pass

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











    def _on_layer_panel_change(self):
        """Wired as LayerPanel.on_change -- a layer toggle/colour/stipple
        edit made IN the panel. Only repaint the things that depend on
        layer state; do NOT call the panel's own sync() back (it already
        updated its widgets and the model, and re-syncing mid-click is
        what made the On/Fill checkboxes stick or flicker). Full
        refresh_all() -- which does sync the panel -- is still used for
        changes that come from outside it (command bar, script, undo,
        file open, cell switch)."""
        self.canvas.redraw()
        self._refresh_layer_choices()
        self._update_properties_panel()


    def _canvas_checkpoint(self):
        self.session.checkpoint()
        self._die_place = self._die_placements()
        self._pad_place = self._locked_pad_shapes()



    def _after_canvas_edit(self):
        """After a canvas move / rotate / mirror / edit: any bare die that
        moved or turned takes its wire bonds and bond fingers with it
        (fanout.carry_bonds; same undo step as the edit), then refresh."""
        held_pads = self._hold_locked_pads()
        if held_pads:
            self.status.set(f"locked I/O pad(s) not changed: {', '.join(held_pads)} "
                            "(unlock IO.<pin> to edit)")
        snap, self._die_place = getattr(self, "_die_place", None), None
        if snap:
            name, before = snap
            cell = self.session.library.cells.get(name)
            if cell is not None:
                from ..scripting.autoplace_session import _xf
                try:
                    locked = self.session.locked_parts(name)
                except Exception:
                    locked = set()
                moved, held = {}, []
                for r in cell.srefs:
                    b = before.get(id(r))
                    if b is None:
                        continue
                    now = (tuple(r.origin), float(r.rotation), bool(r.x_reflection),
                           float(r.magnification or 1.0))
                    if now != b[3]:
                        if r.label in locked:
                            # a locked part stays put: undo the move / turn
                            (r.origin, r.rotation, r.x_reflection, r.magnification) = (
                                b[3][0], b[3][1], b[3][2], b[3][3])
                            held.append(r.label)
                            continue
                        moved[r.label] = (b[2], _xf(r), r.cell_name)
                if held:
                    self.status.set(f"locked, not moved: {', '.join(sorted(held))} "
                                    "(UNLOCK to move)")
                if moved:
                    try:
                        from ..scripting.fanout import carry_bonds
                        res = carry_bonds(self.session, name, moved)
                        if res.get("bonds"):
                            self.status.set(f"{res['bonds']} bond(s), {res['fingers']} finger(s) "
                                            f"moved with {', '.join(sorted(moved))}")
                    except Exception as e:
                        self.status.set(f"bonds not moved with the die: {e}")
        self.refresh_all()

    def refresh_all(self):
        self.canvas.net_highlight = getattr(self.session, "net_highlight", None)
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

    def start_mcp_bridge(self):
        """Start the loopback socket that lets the HLayout MCP server
        (python -m hlayout.mcpserver, i.e. Claude) drive THIS window --
        see hlayout/mcpserver/bridge.py. Off unless called: hlayout_app.py
        calls it for --mcp / HLAYOUT_MCP=1, and Tools > MCP Server (Claude)
        toggles it (toggle_mcp_bridge). Safe to call twice."""
        if getattr(self, "_mcp_bridge", None) is None:
            from ..mcpserver.bridge import McpBridge
            self._mcp_bridge = McpBridge(self).start()
            self.status.set(f"MCP bridge on (127.0.0.1:{self._mcp_bridge.port})")
        self._sync_mcp_menu()
        return self._mcp_bridge

    def stop_mcp_bridge(self):
        """Stop the MCP bridge (no-op if it's off). A stopped McpBridge
        can't be restarted, so it's dropped here and start_mcp_bridge()
        builds a fresh one -- new port, new token -- next time."""
        bridge = getattr(self, "_mcp_bridge", None)
        if bridge is not None:
            bridge.stop()
            self._mcp_bridge = None
            self.status.set("MCP bridge off")
        self._sync_mcp_menu()

    def toggle_mcp_bridge(self):
        """Tools > MCP Server (Claude): the checkbutton has already
        flipped self.mcp_var, so start or stop to match it."""
        if self.mcp_var.get():
            try:
                self.start_mcp_bridge()
            except Exception as e:
                self._sync_mcp_menu()
                dialogs.show_error("MCP Server", f"Could not start the MCP bridge:\n{e}", parent=self)
        else:
            self.stop_mcp_bridge()

    def _sync_mcp_menu(self):
        var = getattr(self, "mcp_var", None)
        if var is not None:
            var.set(getattr(self, "_mcp_bridge", None) is not None)

    def _on_quit(self):
        self._cancel_autosave()
        if getattr(self, "_mcp_bridge", None) is not None:
            self._mcp_bridge.stop()
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
