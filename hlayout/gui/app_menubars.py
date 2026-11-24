# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""The menu bar and the toolbars: building the File / Setup / View /
Create / Edit / Tools / Help menus, the top and bottom toolbars and the
context bar that follows the current tool.

Part of hlayout.gui.app.App (a mixin): these methods are App's own --
call them as always."""

import sys
import tkinter as tk
from tkinter import ttk

from ..core.shapes import CIRCLE_SEGMENT_CHOICES
from .assembly_window import open_assembly_window
from .bug_report import open_bug_report_window
from .component_library_window import open_component_library_window
from .drc_window import open_drc_window
from .dxf_batch_window import open_dxf_batch_window
from .gerber_batch_window import open_gerber_batch_window
from .maskop_window import open_maskop_batch_window
from .netlist_window import open_netlist_window
from .resistor_designer import open_resistor_designer
from .thermal_window import open_thermal_window
from .topview_window import open_topview_window
from .unrouted_window import open_unrouted_window


class MenuBarsMixin:
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
        filem.add_command(label="Save As...", command=self.save_as_gds, accelerator="Ctrl+Shift+S")
        filem.add_command(label="Save As Library...", command=self.save_as_library)
        filem.add_command(label="Archive Design...", command=self.archive_design)
        filem.add_separator()
        filem.add_command(label="Merge GDS Libraries...", command=self.show_merge_gds_dialog)
        filem.add_separator()
        filem.add_command(label="Gerber Export Sets...", command=lambda: open_gerber_batch_window(self))
        filem.add_command(label="DXF Export Sets...", command=lambda: open_dxf_batch_window(self))
        filem.add_command(label="Export Pick and Place...", command=self.export_pick_and_place)
        filem.add_command(label="Export to FreeCAD (3D Macro)...", command=self.export_freecad_macro)
        filem.add_command(label="3D View in SolidCraft...",
                          command=lambda: __import__("hlayout.gui.solidcraft_window",
                                                     fromlist=["x"]).open_solidcraft_window(self))
        filem.add_command(label="Build 3D Component Library...",
                          command=self.build_component_library_macro)
        filem.add_command(label="Component Library Manager...",
                          command=lambda: open_component_library_window(self))
        filem.add_command(label="Print...", command=self.print_current_view, accelerator="Ctrl+P")
        filem.add_separator()
        filem.add_command(label="Exit", command=self._on_quit)

        # --- Setup: configuring the environment before/while you draw ----
        setupm = add_menu("Setup")
        setupm.add_command(label="Attach Library...", command=self.attach_library)
        setupm.add_command(label="Attached Libraries...", command=self.show_attached_libraries)
        setupm.add_command(label="Library Cells...", command=self.show_library_cells)
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
        setupm.add_separator()
        setupm.add_command(label="Technology Properties...",
                           command=self.show_technology_properties_dialog)
        setupm.add_separator()
        setupm.add_command(label="Remap Layers...", command=self.show_remap_layers_dialog)
        setupm.add_separator()
        setupm.add_command(label="3D Component Library Folder...",
                           command=self.change_component_library_dir)
        setupm.add_separator()
        setupm.add_command(label="Load Technology File...", command=self.load_technology_file)
        setupm.add_command(label="Save Technology File...", command=self.save_technology_file)

        # --- View: how the design is displayed ----------------------------
        viewm = add_menu("View")
        viewm.add_command(label="Fit", command=lambda: self.canvas.fit_view(), accelerator="F")
        viewm.add_command(label="Zoom In", command=lambda: self.canvas.zoom_in(), accelerator="+")
        viewm.add_command(label="Zoom Out", command=lambda: self.canvas.zoom_out(), accelerator="-")
        viewm.add_command(label="Toggle Grid", command=lambda: self.canvas.toggle_grid(), accelerator="G")
        self.show_pins_var = tk.BooleanVar(value=self.canvas.show_pin_numbers)
        viewm.add_checkbutton(label="Show Pin Numbers", variable=self.show_pins_var,
                               command=self._on_show_pins_change)
        try:
            from ..io.app_settings import get_setting as _gs
            _sf = _gs("solid_fills")
        except Exception:
            _sf = None
        self.canvas.solid_fills = (sys.platform == "win32") if _sf is None else bool(_sf)
        self.solid_fills_var = tk.BooleanVar(value=self.canvas.solid_fills)
        viewm.add_checkbutton(label="Solid Layer Fills", variable=self.solid_fills_var,
                              command=self._on_solid_fills_change)
        self.show_sli_var = tk.BooleanVar(value=False)
        viewm.add_checkbutton(label="Straight-Line Interconnects", variable=self.show_sli_var,
                               command=self._on_show_sli_change)
        viewm.add_command(label="Recalculate SLI", command=self.recalc_sli, accelerator="F5")
        viewm.add_command(label="Unrouted Nets...", command=lambda: open_unrouted_window(self))
        viewm.add_separator()
        viewm.add_radiobutton(label="Zoom Window", value="zoomwin", variable=self.mode_var,
                               command=self._on_mode_change, accelerator="W")
        viewm.add_radiobutton(label="Pan", value="pan", variable=self.mode_var,
                               command=self._on_mode_change, accelerator="P")
        panm = tk.Menu(viewm, tearoff=False)
        viewm.add_cascade(label="Pan Step", menu=panm)
        for label, d, acc in (("Left", "left", "Ctrl+Left"), ("Right", "right", "Ctrl+Right"),
                              ("Up", "up", "Ctrl+Up"), ("Down", "down", "Ctrl+Down")):
            panm.add_command(label=label, accelerator=acc,
                             command=lambda d=d: self.canvas.pan_step(d))
        viewm.add_command(label="Pan to Point...", command=self.show_pan_to_dialog)
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
        createm.add_radiobutton(label="Wire Bond", value="wire_bond", variable=self.mode_var,
                                 command=self._on_mode_change)
        createm.add_separator()
        createm.add_command(label="Create Cell from Selection...", command=self._create_cell_from_selection)
        createm.add_command(label="Find && Place Cell...", command=self.find_and_place_cell)
        # the wizards: each builds a new cell (a via, a pad, a chip footprint)
        # or new I/O pads, so they live with the other ways of creating things
        createm.add_separator()
        createm.add_command(label="Via Wizard...", command=self.show_via_wizard)
        createm.add_command(label="Pad Wizard...", command=self.show_pad_wizard)
        createm.add_command(label="Chip Wizard...", command=self.show_chip_wizard)
        createm.add_command(label="I/O Pad Wizard...",
                            command=lambda: __import__("hlayout.gui.iopad_wizard",
                                                       fromlist=["x"]).open_iopad_wizard(self))

        # --- Edit: selecting/editing/removing existing geometry -----------
        editm = add_menu("Edit")
        editm.add_command(label="Undo", command=self._undo, accelerator="Ctrl+Z")
        editm.add_command(label="Redo", command=self._redo, accelerator="Ctrl+Y")
        self._editm = editm
        editm.add_separator()
        editm.add_radiobutton(label="Select", value="select", variable=self.mode_var,
                               command=self._on_mode_change)
        editm.add_radiobutton(label="Measure", value="measure", variable=self.mode_var,
                               command=self._on_mode_change, accelerator="M")
        editm.add_separator()
        editm.add_command(label="Edit Selected...", command=self._edit_selected)
        editm.add_command(label="Delete Selected", command=self._delete_selected)
        editm.add_separator()
        editm.add_command(label="Insert Vertex on Edge", command=self._insert_vertex_on_edge)
        editm.add_command(label="Continue Path...", command=self._continue_path)
        editm.add_command(label="Number Pins...", command=self._number_pins)
        editm.add_command(label="Ref-Des to Pin 1", command=self._refdes_to_pin1)
        editm.add_command(label="Auto-Route Two Pins", command=self._auto_route_two_pins)
        editm.add_command(label="Move Wire Bond", command=self._move_wire_bond_tool)
        editm.add_command(label="Delete Wire Bond", command=self._delete_wire_bond_tool)
        editm.add_command(label="Delete Via", command=self._delete_via_tool)
        editm.add_command(label="Move Bond Finger", command=self._move_finger_tool)
        editm.add_command(label="Resize Resistor", command=self._resize_resistor_tool)
        editm.add_command(label="Rip Up and Reroute", command=lambda: self.canvas.set_mode("ripup"))
        editm.add_command(label="Highlight Net", command=self._highlight_net_tool)
        editm.add_command(label="Clear Net Highlight", command=self._clear_net_highlight)
        editm.add_separator()
        editm.add_command(label="Rotate 90°", command=self._rotate_selected, accelerator="R")
        editm.add_command(label="Copy...", command=self._copy_selected)
        editm.add_command(label="Smash Selected to SRefs", command=self._smash_selected)
        editm.add_command(label="Merge Selected...", command=self._merge_selected)
        editm.add_separator()
        editm.add_command(label="Properties", command=self._show_properties_tab)

        # --- Tools: scripting utilities -----------------------------------
        toolsm = add_menu("Tools")
        toolsm.add_command(label="Script Console", command=self._show_console_tab)
        toolsm.add_command(label="Run Script File...", command=self.run_script_file)
        toolsm.add_separator()
        toolsm.add_command(label="Command Bar", command=lambda: self.cmd_entry.focus_set())
        # Start/stop the MCP bridge (what lets Claude's MCP server drive
        # this window). A checkbutton so the menu also shows whether
        # it's on -- including when it was started by --mcp.
        self.mcp_var = tk.BooleanVar(value=getattr(self, "_mcp_bridge", None) is not None)
        toolsm.add_checkbutton(label="MCP Server (Claude)", variable=self.mcp_var,
                               command=self.toggle_mcp_bridge)
        toolsm.add_separator()
        toolsm.add_command(
            label="Thick-Film Resistor Designer...",
            command=lambda: open_resistor_designer(self),
        )
        toolsm.add_command(label="Mask Operations...", command=self.show_mask_op_dialog)
        toolsm.add_command(label="Mask Operation Batch...", command=lambda: open_maskop_batch_window(self))
        toolsm.add_command(label="Top View...", command=lambda: open_topview_window(self))
        toolsm.add_command(label="Assembly Drawing...", command=lambda: open_assembly_window(self))
        toolsm.add_command(label="DRC...", command=lambda: open_drc_window(self))
        toolsm.add_command(label="Extract Netlist...", command=lambda: open_netlist_window(self))
        toolsm.add_command(label="Read Netlist...", command=self.read_netlist)
        toolsm.add_command(label="Auto Place...",
                           command=lambda: __import__("hlayout.gui.autoplace_window",
                                                      fromlist=["x"]).open_autoplace_window(self))
        # one autorouter: the chip-and-wire hybrid router (the Freerouting
        # window is no longer on the menu; FREEROUTE still runs it)
        toolsm.add_command(label="Autoroute...",
                           command=lambda: __import__("hlayout.gui.autoroute_window",
                                                      fromlist=["x"]).open_autoroute_window(self))
        toolsm.add_command(label="Unroute All", command=self.unroute_all)
        toolsm.add_command(label="Thermal Analysis...", command=lambda: open_thermal_window(self))
        toolsm.add_command(label="Generate Dielectric Fill...", command=self.show_dielectric_dialog)
        toolsm.add_command(label="Remove Dielectric Fill", command=self.remove_dielectric_fill)
        toolsm.add_command(label="Remove Unused Bonds...", command=self.remove_unused_bonds)
        toolsm.add_command(label="Bond Header Pins...", command=self.bond_header)
        toolsm.add_command(label="Fan Out Die Bonds...", command=self.fan_out_die_bonds)
        toolsm.add_command(label="Fit Bonds to Nets", command=self.fit_bonds)
        toolsm.add_command(label="Assign I/O Pads...", command=self.assign_io_pads)
        toolsm.add_command(label="Header Pinout...",
                          command=lambda: __import__("hlayout.gui.header_pinout_window",
                          fromlist=["x"]).open_header_pinout_window(self))
        toolsm.add_command(label="Fix Vias...", command=self.fix_vias)

        # --- Help ----------------------------------------------------------
        helpm = add_menu("Help")
        helpm.add_command(label="User Manual", command=self.show_user_manual)
        helpm.add_command(label="Command Reference", command=self.show_command_reference, accelerator="F1")
        helpm.add_separator()
        helpm.add_command(label="Report Bug...", command=lambda: open_bug_report_window(self))
        helpm.add_separator()
        helpm.add_command(label="About HLayout", command=self.show_about)

        self.bind("<Control-n>", lambda e: self.new_library())
        self.bind("<Control-o>", lambda e: self.open_gds())
        self.bind("<Control-s>", lambda e: self.save_gds())
        self.bind("<Control-Shift-S>", lambda e: self.save_as_gds())
        self.bind("<Control-p>", lambda e: self.print_current_view())
        self.bind("<Control-z>", lambda e: self._undo())
        self.bind("<Control-y>", lambda e: self._redo())
        self.bind("<F5>", lambda e: self.recalc_sli())
        self.bind("<Control-Shift-Z>", lambda e: self._redo())
        self.bind("f", lambda e: self.canvas.fit_view())
        self.bind("g", lambda e: self.canvas.toggle_grid())
        self.bind("<F1>", lambda e: self.show_command_reference())

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
        options at the bottom. Also carries the Auto-save switch (see
        _on_autosave_toggle()) -- not a Cell/Layer control, but this is
        the only always-visible row above the canvas."""
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

        # Auto-save: a plain on/off switch, same convention as Snap to
        # grid/Ortho 45 degree routing elsewhere. It belongs to the
        # user, not the design, so it is not saved into the technology
        # file. See _on_autosave_toggle()/_run_autosave().
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=(16, 8), pady=2)
        # On by default; the user's own on/off choice is remembered
        # between sessions (app settings), so switching it off sticks.
        from ..io.app_settings import load_app_settings
        self.autosave_var = tk.BooleanVar(
            value=bool(load_app_settings().get(self.AUTOSAVE_SETTING_KEY, True)))
        ttk.Checkbutton(bar, text="Auto-save (15 min)", variable=self.autosave_var,
                         command=self._on_autosave_toggle).pack(side="left", pady=2)
        if self.autosave_var.get():
            self._schedule_autosave()

    def _build_bottom_toolbar(self):
        """The toolbar row at the bottom of the window (above the status
        bar and Command bar). Used to be split across two rows: a view/
        navigation toolbar (Fit, Select, Zoom Win, Grid, New View, the Cell
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

        # Select / Zoom Win here just set the draw mode (same as Edit >
        # Select and View > Zoom Window) -- they replaced the old
        # Zoom In / Zoom Out step buttons, which stay on the View menu
        # (+/-) and are covered better by the Zoom Window drag anyway.
        def _pick_mode(mode):
            self.mode_var.set(mode)
            self._on_mode_change()

        for label, cmd in [
            ("Fit", lambda: self.canvas.fit_view()),
            ("Select", lambda: _pick_mode("select")),
            ("Zoom Win", lambda: _pick_mode("zoomwin")),
            ("Pan", lambda: _pick_mode("pan")),
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
        self.width_var = tk.IntVar(value=300)  # DBU: 0.3 mm default path width
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
                ("Rotate 90°", self._rotate_selected),
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
