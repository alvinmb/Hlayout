import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk

EXPECTED_TOP_LEVEL = ["File", "Setup", "View", "Create", "Edit", "Tools", "Help"]


def _make_app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    try:
        lib = Library("MENU")
        lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    return app


def _top_level_labels(app):
    # The menu bar is a plain row of ttk.Menubutton widgets (not a
    # native tk.Menu installed via app["menu"]) -- see App._build_menu
    # for why. app._top_menus is the ordered (label, submenu-or-None)
    # list built alongside it, kept around for exactly this kind of
    # introspection.
    return [label for label, _ in app._top_menus]


def test_top_level_menu_structure_matches_required_headings():
    app = _make_app()
    try:
        assert _top_level_labels(app) == EXPECTED_TOP_LEVEL
    finally:
        app.destroy()


def test_every_top_level_heading_present_exactly_once():
    app = _make_app()
    try:
        labels = _top_level_labels(app)
        assert len(labels) == len(EXPECTED_TOP_LEVEL)
        assert len(set(labels)) == len(labels)
    finally:
        app.destroy()


def _submenu_labels(app, top_label):
    for label, sub in app._top_menus:
        if label == top_label:
            if sub is None:
                raise AssertionError(f"{top_label!r} is a direct button, not a submenu")
            out = []
            for j in range(sub.index("end") + 1):
                try:
                    out.append(sub.entrycget(j, "label"))
                except tk.TclError:
                    out.append(None)  # separator
            return out
    raise AssertionError(f"no top-level menu named {top_label!r}")


def test_create_menu_has_all_shape_tools_and_find_place():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Create")
        for expected in ("Rectangle", "Polygon", "Path", "Circle", "Text",
                          "Cell Instance", "Find && Place Cell..."):
            assert expected in labels
    finally:
        app.destroy()


def test_editorial_menu_has_select_edit_delete():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Edit")
        for expected in ("Select", "Edit Selected...", "Delete Selected"):
            assert expected in labels
    finally:
        app.destroy()


def test_setup_menu_has_library_and_layer_and_snap_items():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Setup")
        for expected in ("Attach Library...", "Attached Libraries...", "Layers...", "Snap to Grid"):
            assert expected in labels
    finally:
        app.destroy()


def test_tools_menu_has_console_and_script_and_command_bar():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Tools")
        for expected in ("Script Console", "Run Script File...", "Command Bar"):
            assert expected in labels
    finally:
        app.destroy()


def test_file_menu_no_longer_has_library_items():
    # File should be pure file I/O now that Attach/Attached/Find&Place
    # moved to Setup/Create.
    app = _make_app()
    try:
        labels = _submenu_labels(app, "File")
        assert "Attach Library..." not in labels
        assert "Find && Place Cell..." not in labels
        for expected in ("New", "Open GDS...", "Save GDS...", "Save As Library...",
                          "Gerber Export Sets...", "DXF Export Sets...", "Exit"):
            assert expected in labels
    finally:
        app.destroy()


def test_view_menu_has_new_view_window():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "View")
        for expected in ("Fit", "Zoom In", "Zoom Out", "Toggle Grid", "New View Window"):
            assert expected in labels
    finally:
        app.destroy()


def test_help_menu_has_command_reference():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Help")
        assert "Command Reference" in labels
    finally:
        app.destroy()


def test_help_menu_has_user_manual():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Help")
        assert "User Manual" in labels
        # ahead of Command Reference: a walkthrough before the
        # exhaustive syntax reference.
        assert labels.index("User Manual") < labels.index("Command Reference")
    finally:
        app.destroy()


def test_show_user_manual_opens_the_pdf_with_the_os_default_handler(monkeypatch):
    app = _make_app()
    try:
        opened = []
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
        app.show_user_manual()
        assert len(opened) == 1
        assert opened[0].endswith("HLayout_User_Manual.pdf")
    finally:
        app.destroy()


def test_show_user_manual_falls_back_to_the_docx_without_a_pdf(monkeypatch):
    app = _make_app()
    try:
        real_isfile = os.path.isfile
        monkeypatch.setattr(os.path, "isfile",
                            lambda p: False if p.endswith(".pdf") else real_isfile(p))
        opened = []
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
        app.show_user_manual()
        assert len(opened) == 1 and opened[0].endswith("HLayout_User_Manual.docx")
    finally:
        app.destroy()


def test_show_user_manual_reports_a_missing_file(monkeypatch):
    from hlayout.gui import app as app_module
    app = _make_app()
    try:
        monkeypatch.setattr(os.path, "isfile", lambda p: False)
        started = []
        monkeypatch.setattr(os, "startfile", lambda p: started.append(p), raising=False)
        warned = []
        monkeypatch.setattr(app_module.dialogs, "show_info",
                             lambda *a, **kw: warned.append((a, kw)))
        app.show_user_manual()
        assert started == []
        assert len(warned) == 1
    finally:
        app.destroy()


def test_show_user_manual_reports_when_nothing_can_open_it(monkeypatch):
    from hlayout.gui import app as app_module
    app = _make_app()
    try:
        def _fail(p):
            raise OSError("no application is associated")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(os, "startfile", _fail, raising=False)
        errors = []
        monkeypatch.setattr(app_module.dialogs, "show_error",
                             lambda *a, **kw: errors.append((a, kw)))
        app.show_user_manual()
        assert len(errors) == 1
    finally:
        app.destroy()


def test_about_is_on_the_help_menu():
    app = _make_app()
    try:
        assert "About" not in [label for label, _ in app._top_menus]
        assert "About HLayout" in _submenu_labels(app, "Help")
    finally:
        app.destroy()


def test_create_menu_radiobutton_sets_mode():
    app = _make_app()
    try:
        app.mode_var.set("select")
        # simulate what the Create > Circle radiobutton does
        app.mode_var.set("circle")
        app._on_mode_change()
        assert app.canvas.draw_mode == "circle"
    finally:
        app.destroy()


def test_setup_menu_layers_switches_notebook_tab():
    app = _make_app()
    try:
        app._show_console_tab()
        assert app.right_notebook.select() == str(app.console_frame)
        app._show_layers_tab()
        assert app.right_notebook.select() == str(app.layer_panel)
    finally:
        app.destroy()


def _find_toplevels(widget):
    # _open_edit_dialog() creates the Toplevel with the canvas (not the
    # app) as its master, so it only shows up under canvas.winfo_children(),
    # not app.winfo_children() — walk the whole widget tree to find it.
    found = []
    for w in widget.winfo_children():
        if isinstance(w, tk.Toplevel):
            found.append(w)
        found.extend(_find_toplevels(w))
    return found


def test_editorial_edit_selected_opens_dialog_for_selection():
    app = _make_app()
    try:
        top = app.session.library.cells["TOP"]
        top.add_rectangle(0, 0, 0, 100, 100)
        app.canvas.redraw()
        app.canvas.selected = ("shape", top.shapes[0])

        seen = {"opened": False}

        def close_dialog_when_it_appears():
            # edit_selected() opens the dialog via _open_edit_dialog(), which
            # calls parent.wait_window(win) — a *nested* Tk event loop, not a
            # thread block, so scheduled .after() callbacks like this one
            # still fire while it's waiting. We poll briefly for the
            # Toplevel to appear, record that it did, then close it so
            # wait_window() returns and edit_selected() can return to us.
            tops = _find_toplevels(app)
            if tops:
                seen["opened"] = True
                tops[0].destroy()
                return
            app.after(20, close_dialog_when_it_appears)

        app.after(20, close_dialog_when_it_appears)
        opened = app.canvas.edit_selected()
        assert opened is True
        assert seen["opened"] is True
    finally:
        app.destroy()


def test_editorial_edit_selected_returns_false_when_nothing_selected():
    app = _make_app()
    try:
        app.canvas.selected = None
        assert app.canvas.edit_selected() is False
    finally:
        app.destroy()


def test_run_script_file_executes_and_updates_design(tmp_path):
    app = _make_app()
    try:
        script = tmp_path / "add_rect.py"
        script.write_text("s.rect(0, 0, 0, 500, 500)\n")
        app.console.run_file(str(script))
        assert len(app.session.cell.shapes) == 1
    finally:
        app.destroy()


def test_script_console_accepts_short_commands_like_the_command_bar():
    # The Script Console entry used to only accept raw Python
    # (ScriptConsole.run). It should accept the same short commands
    # (RECT, CELL, LAYER, HELP, ...) as the bottom Command Bar, by
    # routing through the shared CommandInterpreter.
    app = _make_app()
    try:
        app.console_entry.insert(0, "RECT 0 0,0 10,10")
        app._on_console_enter()
        assert len(app.session.cell.shapes) == 1
        assert app.console_entry.get() == ""

        out_text = app.console_output.get("1.0", "end")
        assert "RECT 0 0,0 10,10" in out_text
        assert "rectangle added" in out_text
    finally:
        app.destroy()


def test_script_console_still_accepts_raw_python():
    app = _make_app()
    try:
        app.console_entry.insert(0, "s.rect(0, 0, 0, 5, 5)")
        app._on_console_enter()
        assert len(app.session.cell.shapes) == 1
    finally:
        app.destroy()


def test_script_console_and_command_bar_share_history():
    app = _make_app()
    try:
        app.console_entry.insert(0, "CELL TOP")
        app._on_console_enter()
        # history recall (Up) on the *command bar* should surface the
        # command that was run from the *script console* entry, since
        # both route through the same CommandInterpreter history.
        recalled = app.interp.prev_history("")
        assert recalled == "CELL TOP"
    finally:
        app.destroy()


def test_tools_menu_has_resistor_designer_item():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Tools")
        assert "Thick-Film Resistor Designer..." in labels
    finally:
        app.destroy()


def test_tools_menu_has_via_wizard_item():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Tools")
        assert "Via Wizard..." in labels
    finally:
        app.destroy()


def test_export_sets_are_on_the_file_menu_only():
    # Gerber / DXF Export Sets are file output: File menu only (they were
    # dropped from Tools); About is the last Help item, not a menu button.
    app = _make_app()
    try:
        tools = _submenu_labels(app, "Tools")
        assert "Gerber Export Sets..." not in tools and "DXF Export Sets..." not in tools
        assert "Remove Unused Bonds..." in tools
        assert "Unrouted Nets..." in _submenu_labels(app, "View")
        assert _submenu_labels(app, "Help")[-1] == "About HLayout"
    finally:
        app.destroy()


def test_tools_menu_has_mask_operations_item():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Tools")
        assert "Mask Operations..." in labels
    finally:
        app.destroy()


def test_via_wizard_without_an_attached_library_shows_info_and_no_dialog(monkeypatch):
    from hlayout.gui import app as app_module

    app = _make_app()
    try:
        calls = []
        monkeypatch.setattr(app_module.dialogs, "show_info",
                             lambda title, msg, parent=None: calls.append((title, msg)))
        app.show_via_wizard()
        assert _find_toplevels(app) == []
        assert calls and calls[0][0] == "Via Wizard"
    finally:
        app.destroy()


def test_via_wizard_creates_via_cell_and_saves_to_attached_library(tmp_path):
    from hlayout.scripting import Session

    app = _make_app()
    try:
        part = Session.new("PARTS")
        part.new_cell("DUMMY")
        part.rect(0, 0, 0, 1, 1)
        path = str(tmp_path / "parts.gds")
        part.save(path)
        app.session.attach_library(path)

        app.show_via_wizard()
        win = _find_toplevels(app)[0]

        create_btn = None
        for w in win.winfo_children():
            for c in w.winfo_children():
                if isinstance(c, ttk.Button) and c.cget("text") == "Create":
                    create_btn = c
        assert create_btn is not None
        create_btn.invoke()

        lib = app.session.attached_libraries[0]
        assert "VIA_Layer0_to_Layer1" in lib.cells
        cell = lib.cells["VIA_Layer0_to_Layer1"]
        assert {shape.layer for shape in cell.shapes} == {0, 1}

        from hlayout.io.gdsio import load_gds
        reloaded = load_gds(path)
        assert "VIA_Layer0_to_Layer1" in reloaded.cells
    finally:
        app.destroy()


def test_open_resistor_designer_reuses_a_single_window():
    from hlayout.gui.resistor_designer import open_resistor_designer

    app = _make_app()
    try:
        win1 = open_resistor_designer(app)
        win2 = open_resistor_designer(app)
        assert win1 is win2
        assert win1.resistors == []  # opens empty -- no hard-coded demo set
    finally:
        app.destroy()


def test_resistor_designer_loads_design_rules_from_technology_file(tmp_path):
    from hlayout.gui.resistor_designer import open_resistor_designer
    from hlayout.io.techfile import save_resistor_design_rules

    app = _make_app()
    try:
        save_resistor_design_rules(app.session.library, {
            "aim_pct": 65.0, "ar_min": 0.3, "ar_max": 3.0,
            "available_inks": [100, 1000, 10000],
        })
        win = open_resistor_designer(app)
        assert win.rules.aim_pct == 65.0
        assert win.rules.ar_min == 0.3
        assert win.rules.ar_max == 3.0
        assert win.rules.available_inks == [100, 1000, 10000]
    finally:
        app.destroy()


def test_resistor_designer_design_rules_save_back_to_library():
    from hlayout.gui.resistor_designer import DesignRules
    from hlayout.io.techfile import load_resistor_design_rules

    app = _make_app()
    try:
        rules = DesignRules(aim_pct=70.0, ar_min=0.4, ar_max=2.5,
                             available_inks=[10, 100])
        rules.save_to_library(app.session.library)
        saved = load_resistor_design_rules(app.session.library)
        assert saved["aim_pct"] == 70.0
        assert saved["ar_min"] == 0.4
        assert saved["ar_max"] == 2.5
        assert saved["available_inks"] == [10, 100]
    finally:
        app.destroy()


def test_setup_menu_has_path_style_item():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Setup")
        assert "Path Style..." in labels
    finally:
        app.destroy()


def test_draw_toolbar_no_longer_has_width_cap_or_segments_fields():
    # Width/Cap moved to Setup > Path Style..., and Segments (circle-only)
    # moved to Setup > Circle Segments...; the draw (bottom) toolbar
    # should no longer show any of them as inline fields.
    app = _make_app()
    try:
        texts = []
        for w in app.winfo_children():
            texts.extend(_all_label_texts(w))
        assert "Width:" not in texts
        assert "Cap:" not in texts
        assert "Segments:" not in texts
    finally:
        app.destroy()


def test_setup_menu_has_circle_segments_item():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Setup")
        assert "Circle Segments..." in labels
    finally:
        app.destroy()


def test_circle_segments_dialog_updates_segments_var_and_canvas():
    app = _make_app()
    try:
        app.show_circle_segments_dialog()
        win = _find_toplevels(app)[0]
        # Locate the readonly Combobox inside the dialog and its backing var.
        combo = None
        for w in win.winfo_children():
            for c in w.winfo_children():
                if isinstance(c, ttk.Combobox):
                    combo = c
        assert combo is not None
        combo.set("64")
        # Simulate clicking OK: find it first, then invoke it -- invoking
        # destroys the dialog, so the button must be found before that.
        ok_button = None
        for w in win.winfo_children():
            for c in w.winfo_children():
                if isinstance(c, ttk.Button) and c.cget("text") == "OK":
                    ok_button = c
        assert ok_button is not None
        ok_button.invoke()
        assert app.segments_var.get() == "64"
        assert app.canvas.circle_segments == 64
    finally:
        app.destroy()


def _all_label_texts(widget):
    out = []
    if widget.winfo_class() in ("TLabel", "Label"):
        try:
            out.append(widget.cget("text"))
        except tk.TclError:
            pass
    for c in widget.winfo_children():
        out.extend(_all_label_texts(c))
    return out


def test_path_style_dialog_updates_canvas_path_options():
    app = _make_app()
    try:
        app.width_var.set(2)
        app.cap_var.set("round")
        app.show_path_style_dialog()

        dialog = None
        for w in app.winfo_children():
            if isinstance(w, tk.Toplevel) and w.title() == "Path Style":
                dialog = w
                break
        assert dialog is not None

        # find the width Spinbox / cap Combobox inside the dialog and set
        # new values, then trigger the OK button's command directly.
        widgets = {}

        def collect(w):
            widgets.setdefault(w.winfo_class(), []).append(w)
            for c in w.winfo_children():
                collect(c)

        collect(dialog)
        spin = widgets["TSpinbox"][0]
        combo = widgets["TCombobox"][0]
        spin.delete(0, "end")
        spin.insert(0, "0.007")  # mm -- 7 DBU
        combo.set("square")

        ok_button = [b for b in widgets["TButton"] if b.cget("text") == "OK"][0]
        ok_button.invoke()

        assert app.width_var.get() == 7
        assert app.cap_var.get() == "square"
        assert app.canvas.path_width == 7
        assert app.canvas.path_cap == "square"
    finally:
        app.destroy()


def _make_part_library(path, cell_name="PAD"):
    """A small standalone library with one placeable part cell, for the
    Cell Instance / attached-library tests below."""
    from hlayout.scripting import Session

    s = Session.new("PARTLIB")
    s.new_cell(cell_name)
    s.rect(0, 0, 0, 10, 10)
    s.save(path)
    return path


def test_cell_instance_picker_includes_attached_library_cells(tmp_path):
    # Bug: Create > Cell Instance (canvas._prompt_sref) only listed cells
    # already in the current design, not ones from an attached library --
    # unlike Create > Find && Place Cell..., which searches both. A user
    # who attached a library and used the Cell Instance tool (rather than
    # Find & Place) would only ever see their own design's cells (just
    # "TOP" on a blank design), even with a library attached.
    app = _make_app()
    try:
        part_path = _make_part_library(str(tmp_path / "parts.gds"), "RES100")
        app.session.attach_library(part_path)

        names = app._placeable_cell_names()
        assert "TOP" in names          # the current design's own cell
        assert "RES100" in names       # the attached library's cell
    finally:
        app.destroy()


def test_placing_cell_instance_from_attached_library_imports_it():
    # Placing a cell that only exists in an attached library must import
    # its definition into the current design (like Find & Place / PLACE
    # do), not just create an Sref pointing at a name that isn't actually
    # defined anywhere in this file -- that would be invalid GDSII.
    app = _make_app()
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            part_path = _make_part_library(os.path.join(d, "parts.gds"), "RES100")
            app.session.attach_library(part_path)

            app._on_canvas_commit(
                "sref", 0, [(0, 0)],
                {"cell_name": "RES100", "rotation": 0.0,
                 "magnification": 1.0, "x_reflection": False},
            )

            top = app.session.library.cells["TOP"]
            assert [r.cell_name for r in top.srefs] == ["RES100"]
            # RES100 must now be a real, locally-defined cell -- not a
            # dangling reference to the attached library's copy.
            assert "RES100" in app.session.library.cells
            assert app.session.library.cells["RES100"].shapes
    finally:
        app.destroy()


def test_cell_instance_tool_places_unrotated_with_no_rotation_prompt(monkeypatch):
    # The Cell Instance tool used to pop a "Rotation (degrees)" dialog
    # after the cell picker. It now places at 0 deg -- rotate afterwards
    # with R / Rotate -- and must not call askfloat at all.
    from hlayout.gui import canvas as canvas_module
    app = _make_app()
    try:
        app.session.new_cell("PAD")
        app.session.rect(0, 0, 0, 10, 10)
        app.session.current_cell_name = "TOP"
        app.canvas.set_cell("TOP")

        monkeypatch.setattr(canvas_module, "_choose_from_list",
                            lambda *a, **k: "PAD")

        def _boom(*a, **k):
            raise AssertionError("rotation prompt should be gone")
        monkeypatch.setattr(canvas_module.simpledialog, "askfloat", _boom)

        app.canvas._pending_points = [(4000, 5000)]
        app.canvas._prompt_sref()

        top = app.session.library.cells["TOP"]
        assert [r.cell_name for r in top.srefs] == ["PAD"]
        assert top.srefs[0].rotation == 0.0
        assert top.srefs[0].origin == (4000, 5000)
    finally:
        app.destroy()


def test_cell_instance_still_works_with_no_attached_libraries():
    # Regression guard: the picker/placement path must keep working for
    # the plain case (no attached libraries, referencing a cell already
    # in the current design), which is all it supported before this fix.
    app = _make_app()
    try:
        app.session.new_cell("PAD")  # also switches current_cell_name to "PAD"
        app.session.rect(0, 0, 0, 10, 10)
        app.session.current_cell_name = "TOP"  # switch back to the cell _make_app() created

        names = app._placeable_cell_names()
        assert set(names) == {"TOP", "PAD"}

        app._on_canvas_commit(
            "sref", 0, [(0, 0)],
            {"cell_name": "PAD", "rotation": 0.0,
             "magnification": 1.0, "x_reflection": False},
        )
        top = app.session.library.cells["TOP"]
        assert [r.cell_name for r in top.srefs] == ["PAD"]
    finally:
        app.destroy()


def test_all_named_fonts_are_resized_to_ui_font_size():
    # "make all fonts in app 14pt" -- every stock named Tk font (which
    # ttk/tk widgets, menus, and stock dialogs like simpledialog fall
    # back to when not given an explicit font=...) should be resized,
    # not just a couple of widgets picked by hand. tkinter.messagebox
    # is the one built-in dialog that does NOT reliably follow these
    # named fonts (native on Windows -- see hlayout.gui.dialogs), which
    # is why this app no longer uses it at all.
    import tkinter.font as tkfont

    app = _make_app()
    try:
        for name in tkfont.names(app):
            f = tkfont.nametofont(name, app)
            assert f.cget("size") == app.UI_FONT_SIZE, f"{name} was not resized"
    finally:
        app.destroy()


def test_layer_panel_header_font_tracks_ui_font_size_not_hardcoded():
    # The Layers table's bold column headers used to hardcode size 9,
    # which would silently stop matching the rest of the app's font
    # size. It should track TkDefaultFont's actual (now-resized) size.
    import tkinter.font as tkfont

    app = _make_app()
    try:
        # Find the header row by content ("On" label) rather than
        # assuming a fixed child index, since a toolbar row (All On/All
        # Off) now sits above it.
        label = None
        for frame in app.layer_panel._inner.winfo_children():
            children = frame.winfo_children()
            if children and children[0].winfo_class() in ("TLabel", "Label") \
                    and children[0].cget("text") == "On":
                label = children[0]
                break
        assert label is not None, "couldn't find the layers table header row"
        actual = tkfont.Font(app, font=label.cget("font")).actual()
        assert actual["size"] == app.UI_FONT_SIZE
        assert actual["weight"] == "bold"
    finally:
        app.destroy()


def test_top_level_menu_bar_has_explicit_font_matching_ui_font_size():
    # The menu bar is a row of ttk.Menubutton widgets (not a native
    # tk.Menu installed via app["menu"]) precisely so this holds on
    # every platform, including Windows -- see App._build_menu for
    # why a native system menu bar can't be relied on for this. ttk
    # widgets have no -font resource of their own, so the size is set
    # via a named ttk.Style (self._menu_button_style /
    # self._menu_about_style) instead of widget.cget("font").
    import tkinter.font as tkfont

    app = _make_app()
    try:
        style = ttk.Style(app)
        button_actual = tkfont.Font(app, font=style.lookup(app._menu_button_style, "font")).actual()
        assert button_actual["size"] == app.UI_FONT_SIZE
        about_actual = tkfont.Font(app, font=style.lookup(app._menu_about_style, "font")).actual()
        assert about_actual["size"] == app.UI_FONT_SIZE

        for label, sub in app._top_menus:
            if sub is not None:
                sub_actual = tkfont.Font(app, font=sub.cget("font")).actual()
                assert sub_actual["size"] == app.UI_FONT_SIZE
    finally:
        app.destroy()


def test_layer_panel_all_on_off_buttons_toggle_every_layer():
    app = _make_app()
    try:
        panel = app.layer_panel
        panel.set_all_visible(False)
        assert all(not app.session.library.layers[n].visible for n in range(64))
        assert all(not state["visible_var"].get() for state in panel._row_state.values())

        panel.set_all_visible(True)
        assert all(app.session.library.layers[n].visible for n in range(64))
        assert all(state["visible_var"].get() for state in panel._row_state.values())
    finally:
        app.destroy()


def test_after_all_off_a_single_layer_checkbox_can_still_be_switched_on():
    # "when you switch all layers off you can switch on 1 or more
    # layers" -- All Off must not leave the per-layer checkboxes
    # inert; turning one back on individually should both flip that
    # layer's own visibility and make its shapes reappear on the
    # canvas, without needing All On to bring everything back.
    app = _make_app()
    try:
        top = app.session.library.cells["TOP"]
        shape_a = top.add_rectangle(2, 0, 0, 10, 10)
        shape_b = top.add_rectangle(5, 20, 20, 30, 30)

        panel = app.layer_panel
        panel.set_all_visible(False)
        app.canvas.redraw()
        assert not any(v == ("shape", shape_a) or v == ("shape", shape_b)
                        for v in app.canvas._item_to_shape.values())

        # Flip just layer 2's checkbox back on, the way a user would
        # click it in the Layers tab -- not via set_all_visible().
        row_checkbox = None
        for row in panel._rows:
            children = row.winfo_children()
            if children and children[1].cget("text") == "2":
                row_checkbox = children[0]
                break
        assert row_checkbox is not None, "couldn't find layer 2's row"
        row_checkbox.invoke()

        assert app.session.library.layers[2].visible is True
        assert app.session.library.layers[5].visible is False
        assert panel._row_state[2]["visible_var"].get() is True

        app.canvas.redraw()
        assert any(v == ("shape", shape_a) for v in app.canvas._item_to_shape.values())
        assert not any(v == ("shape", shape_b) for v in app.canvas._item_to_shape.values())
    finally:
        app.destroy()


def test_layer_panel_has_all_on_off_buttons():
    app = _make_app()
    try:
        texts = []

        def collect(w):
            if w.winfo_class() in ("TButton", "Button"):
                try:
                    texts.append(w.cget("text"))
                except tk.TclError:
                    pass
            for c in w.winfo_children():
                collect(c)

        collect(app.layer_panel)
        assert "All On" in texts
        assert "All Off" in texts
    finally:
        app.destroy()


def test_setup_menu_has_remap_layers_item():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Setup")
        assert "Remap Layers..." in labels
    finally:
        app.destroy()


def test_remap_layers_dialog_moves_shapes_across_whole_library():
    app = _make_app()
    try:
        top = app.session.library.cells["TOP"]
        top.add_rectangle(3, 0, 0, 10, 10)
        app.session.new_cell("OTHER")
        app.session.cell.add_rectangle(3, 0, 0, 5, 5)
        app.session.current_cell_name = "TOP"

        app.show_remap_layers_dialog()
        win = _find_toplevels(app)[0]

        # Find the two layer Comboboxes in the first (only) row and the
        # Apply button.
        combos = []

        def collect(w):
            if isinstance(w, ttk.Combobox):
                combos.append(w)
            for c in w.winfo_children():
                collect(c)

        collect(win)
        # combos[0] is the "Apply to" scope combobox is added after the
        # row combos in widget-creation order isn't guaranteed by walk
        # order, so just find From/To by their values containing "(".
        row_combos = [c for c in combos if any("(" in v for v in c.cget("values"))]
        assert len(row_combos) >= 2
        from_combo, to_combo = row_combos[0], row_combos[1]
        from_combo.set("3 (L3)")
        to_combo.set("7 (L7)")

        apply_btn = None
        for w in win.winfo_children():
            for c in w.winfo_children():
                if isinstance(c, ttk.Button) and c.cget("text") == "Apply":
                    apply_btn = c
        assert apply_btn is not None
        apply_btn.invoke()

        assert top.shapes[0].layer == 7
        assert app.session.library.cells["OTHER"].shapes[0].layer == 7
    finally:
        app.destroy()


def _collect_widgets(win, widget_type):
    found = []

    def walk(w):
        if isinstance(w, widget_type):
            found.append(w)
        for c in w.winfo_children():
            walk(c)

    walk(win)
    return found


def test_mask_op_dialog_oversize_writes_a_new_layer():
    app = _make_app()
    try:
        top = app.session.library.cells["TOP"]
        top.add_rectangle(0, 0, 0, 100, 100)

        app.show_mask_op_dialog()
        win = _find_toplevels(app)[0]

        combos = _collect_widgets(win, ttk.Combobox)
        # Order on the grid: Operation, Layer A, Layer B, Result layer,
        # Apply-to scope.
        op_combo, a_combo, b_combo, result_combo, scope_combo = combos
        op_combo.set("OVERSIZE")
        a_combo.set("0 (L0)")
        result_combo.set("1 (L1)")

        spins = _collect_widgets(win, ttk.Spinbox)
        amount_spin = spins[0]
        amount_spin.delete(0, "end")
        amount_spin.insert(0, "0.01")  # mm -- 10 DBU

        apply_btn = next(b for b in _collect_widgets(win, ttk.Button)
                          if b.cget("text") == "Apply")
        apply_btn.invoke()

        result = next(sh for sh in top.shapes if sh.layer == 1)
        assert result.bbox() == (-10, -10, 110, 110)
    finally:
        app.destroy()


def test_mask_op_dialog_flatten_checkbox_defaults_checked_and_sees_sref_content():
    # Reproduces the reported bug directly: geometry placed via Sref
    # (e.g. a resistor built by Session.generate_resistor(), which is
    # always its own device cell + a single Sref -- see "Thick-Film
    # Resistor Designer") must be visible to a mask op run against the
    # cell it was placed into, with no extra steps.
    app = _make_app()
    try:
        app.session.generate_resistor(0, 0, body_width=200.0, body_length=1000.0)

        app.show_mask_op_dialog()
        win = _find_toplevels(app)[0]

        checks = _collect_widgets(win, ttk.Checkbutton)
        flatten_check = next(c for c in checks if "placed cells" in c.cget("text"))
        assert flatten_check.instate(["selected"])

        combos = _collect_widgets(win, ttk.Combobox)
        op_combo, a_combo, b_combo, result_combo, scope_combo = combos
        op_combo.set("OVERSIZE")
        a_combo.set("30 (L30)")  # default resistor ink layer
        result_combo.set("40 (L40)")

        spins = _collect_widgets(win, ttk.Spinbox)
        amount_spin = spins[0]
        amount_spin.delete(0, "end")
        amount_spin.insert(0, "5")

        apply_btn = next(b for b in _collect_widgets(win, ttk.Button)
                          if b.cget("text") == "Apply")
        apply_btn.invoke()

        top = app.session.library.cells["TOP"]
        assert any(sh.layer == 40 for sh in top.shapes)
    finally:
        app.destroy()


def test_mask_op_dialog_boolean_op_turns_on_a_hidden_result_layer():
    # Reported bug (verbatim): "mask generator does not [produce] data for
    # the boolian oerators" -- AND/OR/NOT/XOR were, in fact, writing
    # correct output, but to a result layer whose visibility happened to
    # be off, which looks indistinguishable from writing nothing on the
    # canvas. Confirmed live on the user's own design (two real ink
    # layers, AND'd together, written to a layer toggled off in the
    # Layers panel) before this fix. The dialog now turns the result
    # layer's visibility on whenever the operation actually wrote output.
    app = _make_app()
    try:
        top = app.session.library.cells["TOP"]
        top.add_rectangle(0, 0, 0, 100, 100)
        top.add_rectangle(1, 50, 50, 150, 150)
        app.session.library.layers[2].visible = False

        app.show_mask_op_dialog()
        win = _find_toplevels(app)[0]

        combos = _collect_widgets(win, ttk.Combobox)
        op_combo, a_combo, b_combo, result_combo, scope_combo = combos
        op_combo.set("AND")
        a_combo.set("0 (L0)")
        b_combo.set("1 (L1)")
        result_combo.set("2 (L2)")

        apply_btn = next(b for b in _collect_widgets(win, ttk.Button)
                          if b.cget("text") == "Apply")
        apply_btn.invoke()

        assert any(sh.layer == 2 for sh in top.shapes)
        assert app.session.library.layers[2].visible is True
        assert "visibility turned on" in app.status.get()
    finally:
        app.destroy()



def _tv(app):
    from hlayout.gui.topview_window import TopViewWindow
    return next(w for w in _find_toplevels(app) if isinstance(w, TopViewWindow))


def _tv_button(win, text):
    return next(b for b in _collect_widgets(win, ttk.Button) if b.cget("text").startswith(text))


def _tv_build(win, timeout=30.0):
    """Press Build and wait for the background build to finish."""
    import time
    _tv_button(win, "Build").invoke()
    t0 = time.time()
    while getattr(win, "_building", False) and time.time() - t0 < timeout:
        win.update()
        time.sleep(0.02)
    win.update()


def test_topview_window_writes_a_hidden_line_result_and_saves_settings():
    from hlayout.io.techfile import load_topview_settings
    app = _make_app()
    try:
        top = app.session.library.cells["TOP"]
        top.add_rectangle(0, 0, 0, 1000, 1000)
        app.show_topview_dialog()
        win = _tv(app)
        assert 0 in win._rows                       # the drawn layer is listed
        win.result_combo.set(win._layer_label(1))
        win._on_result()
        _tv_build(win)
        assert not any(sh.layer == 1 for sh in top.shapes)
        view = app.session.library.cells["TOPVIEW"]
        assert any(sh.layer == 1 for sh in view.shapes)
        assert "wrote" in app.status.get() and "cell TOPVIEW" in app.status.get()
        st = load_topview_settings(app.session.library)
        assert st["saved"] and st["result_layer"] == 1 and st["out_cell"] == "TOPVIEW"
    finally:
        app.destroy()


def test_topview_window_output_cell_can_be_renamed():
    app = _make_app()
    try:
        app.session.library.cells["TOP"].add_rectangle(0, 0, 0, 1000, 1000)
        app.show_topview_dialog()
        win = _tv(app)
        win.result_combo.set(win._layer_label(1))
        win._on_result()
        win.out_cell_var.set("ASSY_VIEW")
        _tv_build(win)
        assert "ASSY_VIEW" in app.session.library.cells
        assert "TOPVIEW" not in app.session.library.cells
    finally:
        app.destroy()


def test_topview_window_always_views_the_main_design_cell():
    app = _make_app()
    try:
        lib = app.session.library
        lib.cells["TOP"].add_rectangle(0, 0, 0, 1000, 1000)
        other = lib.new_cell("OTHER")
        other.add_rectangle(0, 5000, 5000, 6000, 6000)
        app.session.use_cell("OTHER")
        app.show_topview_dialog()
        win = _tv(app)
        win.result_combo.set(win._layer_label(1))
        win._on_result()
        _tv_build(win)
        xs = [pt[0] for sh in lib.cells["TOPVIEW"].shapes for pt in getattr(sh, "points", [])]
        assert xs and max(xs) <= 1100              # TOP's square, not OTHER's
    finally:
        app.destroy()


def test_topview_window_hatch_fields_follow_the_hatch_box():
    app = _make_app()
    try:
        app.show_topview_dialog()
        win = _tv(app)
        assert "disabled" in win.hatch_pitch_entry.state()
        win.hatch_var.set(True)
        win._on_hatch()
        assert "disabled" not in win.hatch_pitch_entry.state()
        win.hatch_pitch_var.set("")                # cleared while typing: no crash
        assert "pitch" in win.msg_var.get().lower()
        win.hatch_pitch_var.set("0.25")
        win.hatch_angles_var.set("45, x")
        assert "angles" in win.msg_var.get().lower()
    finally:
        app.destroy()


def test_topview_window_requires_a_result_layer():
    app = _make_app()
    try:
        app.session.library.cells["TOP"].add_rectangle(0, 0, 0, 1000, 1000)
        app.show_topview_dialog()
        win = _tv(app)
        win.result_var.set("")
        _tv_build(win)
        assert "Result layer" in win.msg_var.get()
        assert "TOPVIEW" not in app.session.library.cells
    finally:
        app.destroy()


def test_topview_window_unticked_layer_is_remembered_and_left_out():
    from hlayout.io.techfile import load_topview_settings
    app = _make_app()
    try:
        app.session.library.cells["TOP"].add_rectangle(0, 0, 0, 1000, 1000)
        app.show_topview_dialog()
        win = _tv(app)
        cb, var, _lbl, _role = win._rows[0]
        var.set(False)
        win._on_toggle(0)
        assert load_topview_settings(app.session.library)["excluded"] == [0]
        win.destroy()
        app.show_topview_dialog()
        assert _tv(app)._rows[0][1].get() is False
    finally:
        app.destroy()

def test_setup_menu_has_technology_file_items():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Setup")
        assert "Load Technology File..." in labels
        assert "Save Technology File..." in labels
    finally:
        app.destroy()


def test_load_technology_file_via_app_updates_layer_panel(tmp_path):
    app = _make_app()
    try:
        path = str(tmp_path / "tech.json")
        app.session.library.layers.set_style(3, name="CONTACT", color="#ff00ff")
        app.session.save_technology(path)

        app.session.library.layers.set_style(3, name="RESET_NAME")
        app.session.load_technology(path)
        app.refresh_all()

        assert app.session.library.layers[3].name == "CONTACT"
        assert app.layer_panel._row_state[3]["name_var"].get() == "CONTACT"
    finally:
        app.destroy()


def test_editorial_menu_has_rotate_and_copy_but_no_move_dialog():
    # The Edit > Move... (x,y) dialog was removed in favour of purely
    # mouse-driven repositioning (drag to move, including a window/
    # marquee multi-selection) -- see LayoutCanvas's window-select
    # support. Rotate is now a no-dialog "Rotate 90 deg" (click again /
    # press R to keep turning); Copy... stays a dialog since "offset by
    # dx,dy" isn't a natural mouse-only gesture.
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Edit")
        assert "Move..." not in labels
        assert "Rotate..." not in labels
        assert "Rotate 90°" in labels
        assert "Copy..." in labels
        assert "Properties" in labels
        assert "Auto-Route Two Pins" in labels
    finally:
        app.destroy()


def test_rotate_menu_item_turns_the_selection_90_without_a_dialog(monkeypatch):
    from hlayout.gui import app as app_module
    app = _make_app()
    try:
        app.session.new_cell("PART")
        app.session.rect(0, 0, 0, 10, 10)
        app.session.current_cell_name = "TOP"
        app.canvas.set_cell("TOP")
        ref = app.session.sref("PART", (100, 100))
        app.canvas.selected = ("sref", ref)

        def _boom(*a, **k):
            raise AssertionError("Rotate must not open a dialog")
        monkeypatch.setattr(app_module.simpledialog, "askstring", _boom)

        app._rotate_selected()
        assert ref.rotation == 90.0
        app._rotate_selected()
        assert ref.rotation == 180.0
    finally:
        app.destroy()


def test_selected_shape_is_drawn_in_white():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 100, 50)
        app.canvas.redraw()
        shape = s.cell.shapes[0]
        app.canvas.selected = ("shape", shape)
        app.canvas.draw_mode = "select"
        app.canvas.redraw()
        item = next(i for i, v in app.canvas._item_to_shape.items() if v == ("shape", shape))
        assert app.canvas.itemcget(item, "outline") == app.canvas.SELECTED_COLOR
    finally:
        app.destroy()


def test_selected_shape_is_an_outline_not_a_solid_white_fill():
    # A selected shape on a filled/stippled layer must not be redrawn as
    # a solid white block -- only its outline turns white, so a large
    # selected shape doesn't hide everything underneath it.
    app = _make_app()
    try:
        s = app.session
        s.library.layers.set_style(0, fill=True, stipple=0)
        s.rect(0, 0, 0, 100, 50)
        shape = s.cell.shapes[0]
        app.canvas.selected = ("shape", shape)
        app.canvas.draw_mode = "select"
        app.canvas.redraw()
        item = next(i for i, v in app.canvas._item_to_shape.items() if v == ("shape", shape))
        assert app.canvas.itemcget(item, "outline") == app.canvas.SELECTED_COLOR
        assert app.canvas.itemcget(item, "fill") == ""
    finally:
        app.destroy()


def test_move_selected_translates_shape():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 100, 50)
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", shape)
        before = list(shape.points)
        assert app.canvas.move_selected(10, 20) is True
        after = list(shape.points)
        assert after == [(x + 10, y + 20) for x, y in before]
    finally:
        app.destroy()


def test_move_selected_returns_false_when_nothing_selected():
    app = _make_app()
    try:
        app.canvas.draw_mode = "select"
        app.canvas.selected = None
        assert app.canvas.move_selected(5, 5) is False
    finally:
        app.destroy()


def _mkevent(x, y):
    class _E:
        pass
    e = _E()
    e.x, e.y = x, y
    return e


def test_window_select_marquee_picks_up_fully_enclosed_shapes():
    app = _make_app()
    try:
        from hlayout.core.units import mm_to_dbu
        s = app.session
        # Sized in mm (not raw DBU) so the two rects are visibly separated
        # in pixel space at the canvas's default zoom level -- 10 DBU is
        # a fraction of a pixel at 1:1 default scale, too small to hit-test.
        d = mm_to_dbu(10)
        s.rect(0, 0, 0, d, d)                    # fully inside the marquee below
        s.rect(1, d * 20, d * 20, d * 21, d * 21)  # far outside the marquee
        cell = s.cell
        r_in, r_out = cell.shapes
        app.canvas.draw_mode = "select"
        app.canvas.update_idletasks()

        p0 = app.canvas.to_px(-d, d * 2)
        p1 = app.canvas.to_px(d * 2, -d)
        app.canvas._select_click(_mkevent(*[int(round(v)) for v in p0]))
        assert app.canvas._marquee_start_mouse is not None
        app.canvas._finish_marquee(_mkevent(*[int(round(v)) for v in p1]))

        assert app.canvas.selected is None
        assert ("shape", r_in) in app.canvas.selected_multi
        assert ("shape", r_out) not in app.canvas.selected_multi
    finally:
        app.destroy()


def test_window_select_then_move_translates_every_member():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 10, 10)
        s.rect(0, 20, 20, 30, 30)
        cell = s.cell
        a, b = cell.shapes
        app.canvas.draw_mode = "select"
        app.canvas.selected_multi = [("shape", a), ("shape", b)]
        before_a = list(a.points)
        before_b = list(b.points)
        assert app.canvas.move_selected(5, 5) is True
        assert a.points == [(x + 5, y + 5) for x, y in before_a]
        assert b.points == [(x + 5, y + 5) for x, y in before_b]
    finally:
        app.destroy()


def test_window_select_then_delete_removes_every_member():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 10, 10)
        s.rect(0, 20, 20, 30, 30)
        cell = s.cell
        a, b = cell.shapes
        app.canvas.draw_mode = "select"
        app.canvas.selected_multi = [("shape", a), ("shape", b)]
        app.canvas.delete_selected()
        assert a not in cell.shapes
        assert b not in cell.shapes
        assert app.canvas.selected_multi == []
    finally:
        app.destroy()


def test_a_trivial_click_with_no_drag_does_not_start_a_window_selection():
    app = _make_app()
    try:
        app.canvas.draw_mode = "select"
        app.canvas.update_idletasks()
        e = _mkevent(5, 5)
        app.canvas._select_click(e)
        assert app.canvas._marquee_start_mouse == (5, 5)
        app.canvas._finish_marquee(e)  # same point -- no real drag happened
        assert app.canvas.selected_multi == []
    finally:
        app.destroy()


def test_rotate_selected_rotates_shape_points_about_centroid():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 10, 10)
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", shape)
        assert app.canvas.rotate_selected(90) is True
        # A 10x10 square rotated 90 degrees about its own centroid still
        # covers the same bounding box, just with corners permuted.
        xs = sorted(p[0] for p in shape.points)
        ys = sorted(p[1] for p in shape.points)
        assert xs[0] == 0 and xs[-1] == 10
        assert ys[0] == 0 and ys[-1] == 10
    finally:
        app.destroy()


def test_rotate_selected_rotates_sref_and_text_via_rotation_field():
    from hlayout.core.shapes import Text

    app = _make_app()
    try:
        s = app.session
        s.new_cell("SUB")
        s.current_cell_name = "TOP"
        ref = s.sref("SUB", (0, 0))
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("sref", ref)
        assert app.canvas.rotate_selected(45) is True
        assert ref.rotation == 45

        txt = Text(layer=0, origin=(0, 0), text="HI")
        s.cell.shapes.append(txt)
        app.canvas.selected = ("shape", txt)
        assert app.canvas.rotate_selected(30) is True
        assert txt.rotation == 30
    finally:
        app.destroy()


def test_rotate_selected_rejects_vertex_and_edge_selection():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 10, 10)
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("vertex", shape, 0)
        assert app.canvas.rotate_selected(10) is False
        app.canvas.selected = ("edge", shape, (0, 1))
        assert app.canvas.rotate_selected(10) is False
    finally:
        app.destroy()


def test_copy_selected_duplicates_shape_offset_and_selects_the_copy():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 100, 50)
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", shape)
        before = list(shape.points)

        assert app.canvas.copy_selected(200, 300) is True
        assert len(s.cell.shapes) == 2
        new_shape = s.cell.shapes[1]
        assert new_shape is not shape  # a real duplicate, not the same object
        assert new_shape.points == [(x + 200, y + 300) for x, y in before]
        assert shape.points == before  # original untouched
        assert app.canvas.selected == ("shape", new_shape)  # the copy is now selected
    finally:
        app.destroy()


def test_copy_selected_duplicates_sref_via_origin_offset():
    app = _make_app()
    try:
        s = app.session
        s.new_cell("SUB")
        s.current_cell_name = "TOP"
        ref = s.sref("SUB", (10, 20), rotation=45, magnification=2.0)
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("sref", ref)

        assert app.canvas.copy_selected(5, 5) is True
        assert len(s.cell.srefs) == 2
        new_ref = s.cell.srefs[1]
        assert new_ref is not ref
        assert new_ref.origin == (15, 25)
        assert new_ref.rotation == 45  # other fields carried over as-is
        assert new_ref.cell_name == "SUB"
        assert ref.origin == (10, 20)  # original untouched
    finally:
        app.destroy()


def test_copy_selected_returns_false_when_nothing_selected():
    app = _make_app()
    try:
        app.canvas.draw_mode = "select"
        app.canvas.selected = None
        assert app.canvas.copy_selected(5, 5) is False
    finally:
        app.destroy()


def test_copy_selected_rejects_vertex_and_edge_selection():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 10, 10)
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("vertex", shape, 0)
        assert app.canvas.copy_selected(10, 10) is False
        app.canvas.selected = ("edge", shape, (0, 1))
        assert app.canvas.copy_selected(10, 10) is False
        assert len(s.cell.shapes) == 1  # nothing was added
    finally:
        app.destroy()


def test_copy_selected_is_undoable():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 10, 10)
        s.clear_undo_history()
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", shape)

        assert app.canvas.copy_selected(5, 5) is True
        assert len(s.cell.shapes) == 2
        assert s.undo() is True
        assert len(s.cell.shapes) == 1
    finally:
        app.destroy()


def test_copy_command_operates_on_canvas_selection():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 10, 10)
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", shape)

        out = app.interp.run("COPY 5,5")
        assert "copied" in out
        assert len(s.cell.shapes) == 2

        app.canvas.selected = None
        out = app.interp.run("COPY 1,1")
        assert out.startswith("error:")
    finally:
        app.destroy()


def test_move_and_rotate_commands_operate_on_canvas_selection():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 10, 10)
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", shape)

        out = app.interp.run("MOVE 5,5")
        assert "moved" in out
        assert shape.points[0] == (5, 5)

        out = app.interp.run("ROTATE 90")
        assert "rotated" in out

        app.canvas.selected = None
        out = app.interp.run("MOVE 1,1")
        assert out.startswith("error:")
    finally:
        app.destroy()


def test_edit_menu_has_undo_redo_items():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Edit")
        assert "Undo" in labels
        assert "Redo" in labels
    finally:
        app.destroy()


def test_undo_redo_menu_items_disabled_with_nothing_to_do():
    app = _make_app()
    try:
        assert app._editm.entrycget(0, "state") == "disabled"
        assert app._editm.entrycget(1, "state") == "disabled"
    finally:
        app.destroy()


def test_undo_redo_menu_items_enable_after_an_edit():
    app = _make_app()
    try:
        app.session.rect(0, 0, 0, 10, 10)
        assert app._editm.entrycget(0, "state") == "normal"
        assert app._editm.entrycget(1, "state") == "disabled"

        app._undo()
        assert app._editm.entrycget(0, "state") == "disabled"
        assert app._editm.entrycget(1, "state") == "normal"
    finally:
        app.destroy()


def test_app_undo_redo_reverts_and_restores_a_shape():
    app = _make_app()
    try:
        app.session.rect(0, 0, 0, 10, 10)
        assert len(app.session.cell.shapes) == 1

        app._undo()
        assert len(app.session.cell.shapes) == 0

        app._redo()
        assert len(app.session.cell.shapes) == 1
    finally:
        app.destroy()


def test_app_undo_with_nothing_to_undo_sets_status():
    app = _make_app()
    try:
        app._undo()
        assert app.status.get() == "nothing to undo"
        app._redo()
        assert app.status.get() == "nothing to redo"
    finally:
        app.destroy()


def test_canvas_drag_move_is_undoable():
    # A mouse-driven drag-to-move (not the MOVE command/dialog) should
    # push exactly one undo checkpoint per drag gesture.
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 10, 10)
        s.clear_undo_history()  # isolate the drag from the rect's own checkpoint
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        before = list(shape.points)
        assert not s.can_undo()

        # simulate the drag: select the shape (button press), then two
        # motion events (only the first should checkpoint), then release
        from hlayout.gui.canvas import _geom_snapshot

        app.canvas.selected = ("shape", shape)
        app.canvas._drag_checkpointed = False
        app.canvas._drag_start_mouse = (0, 0)
        app.canvas._drag_snapshot = _geom_snapshot("shape", shape)

        class FakeEvent:
            def __init__(self, x, y):
                self.x, self.y = x, y

        # to_dbu(0,0) is (0,0) by default scale/offset in a fresh canvas,
        # so drive the drag via to_px of a target point instead.
        px, py = app.canvas.to_px(5, 5)
        app.canvas._on_drag_motion(FakeEvent(px, py))
        assert s.can_undo()
        stack_len_after_first = len(s._undo_stack)
        px2, py2 = app.canvas.to_px(6, 6)
        app.canvas._on_drag_motion(FakeEvent(px2, py2))
        assert len(s._undo_stack) == stack_len_after_first  # no 2nd checkpoint

        assert s.undo() is True
        # Undo restores a deep-copied snapshot -- the shapes in it are
        # new objects, not the same `shape` reference from before.
        assert s.cell.shapes[0].points == before
    finally:
        app.destroy()


def test_canvas_move_rotate_delete_are_undoable():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 10, 10)
        s.clear_undo_history()
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"

        app.canvas.selected = ("shape", shape)
        assert app.canvas.move_selected(5, 5) is True
        assert s.undo() is True
        shape = s.cell.shapes[0]  # a fresh object after the restore
        assert shape.points[0] == (0, 0)

        app.canvas.selected = ("shape", shape)
        assert app.canvas.rotate_selected(90) is True
        assert s.undo() is True
        shape = s.cell.shapes[0]

        app.canvas.selected = ("shape", shape)
        app.canvas.delete_selected()
        assert shape not in s.cell.shapes
        assert s.undo() is True
        assert len(s.cell.shapes) == 1
    finally:
        app.destroy()


def test_edit_selected_dialog_apply_is_undoable():
    app = _make_app()
    try:
        s = app.session
        s.rect(0, 0, 0, 10, 10)
        s.clear_undo_history()
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", shape)

        # _open_edit_dialog() calls parent.wait_window(win) -- a nested
        # Tk event loop, not a thread block -- so edit_selected() below
        # won't return until the dialog closes. Interact with it (and
        # invoke OK) via a scheduled callback instead, same pattern as
        # test_editorial_edit_selected_opens_dialog_for_selection above.
        seen = {"applied": False}

        def fill_in_and_apply():
            tops = _find_toplevels(app)
            if not tops:
                app.after(20, fill_in_and_apply)
                return
            win = tops[0]
            buttons = []
            layer_combo = []

            def walk(w):
                for c in w.winfo_children():
                    if c.winfo_class() == "Button":
                        buttons.append(c)
                    elif c.winfo_class() == "TCombobox":
                        layer_combo.append(c)
                    walk(c)

            walk(win)
            # the Layer field is a dropdown of the technology's layer stack
            layer_combo[0].set("5: " + app.session.library.layers[5].name)
            ok_button = next(b for b in buttons if b.cget("text") == "OK")
            ok_button.invoke()
            seen["applied"] = True

        assert not s.can_undo()
        app.after(20, fill_in_and_apply)
        opened = app.canvas.edit_selected()
        assert opened is True
        assert seen["applied"] is True

        assert s.cell.shapes[0].layer == 5
        assert s.undo() is True
        assert s.cell.shapes[0].layer == 0
    finally:
        app.destroy()


def test_edit_shape_dialog_layer_field_reads_the_layer_stack():
    from tkinter import ttk
    app = _make_app()
    try:
        s = app.session
        s.library.layers.set_style(7, name="Metal-7")
        s.polygon(7, [(0, 0), (100, 0), (100, 100)])
        shape = s.cell.shapes[0]
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", shape)

        found = {}

        def inspect():
            tops = _find_toplevels(app)
            if not tops:
                app.after(20, inspect)
                return
            combos = []

            def walk(w):
                for c in w.winfo_children():
                    if isinstance(c, ttk.Combobox):
                        combos.append(c)
                    walk(c)

            walk(tops[0])
            found["values"] = list(combos[0]["values"]) if combos else None
            found["current"] = combos[0].get() if combos else None
            tops[0].destroy()

        app.after(20, inspect)
        assert app.canvas.edit_selected() is True
        # the dropdown is the whole technology layer stack, and the
        # polygon's own layer (7: Metal-7) is preselected
        assert found["values"][7] == "7: Metal-7"
        assert found["values"][0] == "0: " + s.library.layers[0].name
        assert found["current"] == "7: Metal-7"
    finally:
        app.destroy()


def test_setup_menu_has_technology_properties_item():
    # The individual Route Layers / Resistor Geometry / Ref-Des / Wire
    # Bond dialogs were consolidated into one tabbed Setup > Technology
    # Properties... window.
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Setup")
        assert "Technology Properties..." in labels
        for gone in ("Route Layers...", "Resistor Geometry Rules...",
                     "Ref-Des Annotation Layer...", "Wire Bond Rules..."):
            assert gone not in labels
    finally:
        app.destroy()


def test_technology_properties_dialog_has_all_rule_tabs():
    from tkinter import ttk
    app = _make_app()
    try:
        win = app.show_technology_properties_dialog()
        nb = next(w for w in _collect_widgets(win, ttk.Notebook))
        tabs = [nb.tab(t, "text") for t in nb.tabs()]
        for expected in ("Route", "Resistors", "Ref-Des", "Wire Bonds", "Dielectric"):
            assert expected in tabs
        win.destroy()
    finally:
        app.destroy()


def test_tools_menu_has_drc_item():
    app = _make_app()
    try:
        labels = _submenu_labels(app, "Tools")
        assert "DRC..." in labels
    finally:
        app.destroy()


def test_drc_window_flags_a_resistor_lead_that_is_too_narrow():
    # Reproduces the same "geometry placed via Sref must be visible"
    # scenario as test_mask_op_dialog_flatten_checkbox_defaults_checked_
    # _and_sees_sref_content, but for DRC: a resistor built by
    # Session.generate_resistor() is always its own device cell + a
    # single Sref (see "Thick-Film Resistor Designer"), so a MIN_WIDTH
    # rule on the ink layer must catch a too-narrow lead by descending
    # into that placed cell, not just the current cell's own shapes.
    from hlayout.gui.drc_window import open_drc_window, DrcRule

    app = _make_app()
    try:
        app.session.generate_resistor(0, 0, body_width=200.0, body_length=1000.0)

        win = open_drc_window(app)
        # A MIN_WIDTH value bigger than the resistor's body width is
        # guaranteed to flag the whole ink shape as a violation.
        win.rules.append(DrcRule(kind="MIN_WIDTH", layer_a=30, value=300.0,
                                  name="ink min width"))
        win._persist()
        win._refresh_rules()
        assert len(win.rule_table.get_children()) == 1

        win.mark_var.set(False)
        win._run_drc()

        assert win.violations
        assert all(v.rule.kind == "MIN_WIDTH" for v in win.violations)
        assert len(win.viol_table.get_children()) == len(win.violations)
        assert "violation(s) found" in win.status_var.get()
    finally:
        app.destroy()


def test_drc_window_reports_no_violations_when_rules_are_satisfied():
    from hlayout.gui.drc_window import open_drc_window, DrcRule

    app = _make_app()
    try:
        top = app.session.library.cells["TOP"]
        top.add_rectangle(0, 0, 0, 100, 100)

        win = open_drc_window(app)
        win.rules.append(DrcRule(kind="MIN_WIDTH", layer_a=0, value=10.0))
        win._persist()
        win._refresh_rules()

        win.mark_var.set(False)
        win._run_drc()

        assert win.violations == []
        assert win.status_var.get() == "DRC: no violations found"
    finally:
        app.destroy()


def test_tools_menu_mcp_server_item_starts_and_stops_the_bridge(tmp_path, monkeypatch):
    from hlayout.mcpserver import bridge
    monkeypatch.setattr(bridge, "BRIDGE_INFO_PATH", str(tmp_path / "mcp_bridge.json"))
    app = _make_app()
    try:
        assert "MCP Server (Claude)" in _submenu_labels(app, "Tools")
        assert not app.mcp_var.get()
        app.mcp_var.set(True)       # what clicking the checkbutton does
        app.toggle_mcp_bridge()
        first = bridge.read_bridge_info()
        assert first and first["port"] == app._mcp_bridge.port
        app.mcp_var.set(False)
        app.toggle_mcp_bridge()
        assert app._mcp_bridge is None and bridge.read_bridge_info() is None
        app.start_mcp_bridge()      # the --mcp route ticks the menu item too
        assert app.mcp_var.get()
        assert bridge.read_bridge_info()["token"] != first["token"]
    finally:
        app._on_quit()
