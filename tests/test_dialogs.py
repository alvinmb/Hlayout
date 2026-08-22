import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import font as tkfont
from tkinter import ttk


def _make_app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    try:
        lib = Library("DIALOGS")
        lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    return app


def _collect_widgets(root, cls):
    found = []
    for w in root.winfo_children():
        if isinstance(w, cls):
            found.append(w)
        found.extend(_collect_widgets(w, cls))
    return found


def _find_toplevels(app):
    return [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]


def _effective_font_size(widget):
    """The actual rendered point size of a widget's font, whether it
    was set explicitly (widget.cget("font") then returns a Tcl font
    object, not a plain str or tuple -- str() it first) or left at the
    ttk style's own default (cget("font") is "" in that case; fall
    back to the style lookup, which is how ttk resolves it at render
    time -- normally "TkDefaultFont")."""
    f = widget.cget("font")
    text = str(f) if f else ""
    if not text:
        style = ttk.Style(widget)
        text = style.lookup(widget.winfo_class(), "font") or "TkDefaultFont"
    return tkfont.Font(widget, font=text).cget("size")


# --- why hlayout.gui.dialogs exists at all ----------------------------------

def test_no_module_uses_tkinter_messagebox_directly():
    # tkinter.messagebox's showinfo/showerror/showwarning/askyesno call
    # Tcl's tk_messageBox, which on Windows delegates to the native
    # Win32 MessageBox() dialog -- confirmed live to still render at a
    # small system font even after every named Tk font in the app was
    # resized to UI_FONT_SIZE (see App._apply_global_font_size), most
    # visibly on the Export Gerber summary listing the files just
    # written. hlayout.gui.dialogs provides Tk-drawn replacements used
    # everywhere instead -- this asserts none of the GUI modules
    # regress back to importing the native one.
    import ast
    gui_dir = os.path.join(os.path.dirname(__file__), "..", "hlayout", "gui")
    for name in os.listdir(gui_dir):
        if not name.endswith(".py"):
            continue
        path = os.path.join(gui_dir, name)
        with open(path) as f:
            tree = ast.parse(f.read(), filename=name)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "tkinter":
                names = [alias.name for alias in node.names]
                assert "messagebox" not in names, f"{name} imports tkinter.messagebox"


# --- hlayout.gui.dialogs itself ----------------------------------------------

def test_show_info_builds_a_modal_toplevel_that_closes_on_ok():
    from hlayout.gui import dialogs
    app = _make_app()
    try:
        def click_ok():
            wins = _find_toplevels(app)
            assert wins
            win = wins[0]
            assert win.title() == "Info Title"
            btn = next(b for b in _collect_widgets(win, ttk.Button) if b.cget("text") == "OK")
            btn.invoke()

        app.after(10, click_ok)
        dialogs.show_info("Info Title", "an informational message", parent=app)
        assert _find_toplevels(app) == []
    finally:
        app.destroy()


def test_ask_yes_no_returns_true_on_yes_and_false_on_no():
    from hlayout.gui import dialogs
    app = _make_app()
    try:
        def click(label):
            win = _find_toplevels(app)[0]
            btn = next(b for b in _collect_widgets(win, ttk.Button) if b.cget("text") == label)
            btn.invoke()

        app.after(10, lambda: click("Yes"))
        assert dialogs.ask_yes_no("Confirm", "proceed?", parent=app) is True

        app.after(10, lambda: click("No"))
        assert dialogs.ask_yes_no("Confirm", "proceed?", parent=app) is False
    finally:
        app.destroy()


def test_dialog_labels_track_ui_font_size_not_a_hardcoded_size():
    # The whole point of replacing messagebox: these dialogs are built
    # from ordinary ttk widgets, so their text picks up TkDefaultFont
    # (and therefore App.UI_FONT_SIZE) exactly like every other widget
    # in the app, with no explicit font=... needed here.
    from hlayout.gui import dialogs
    app = _make_app()
    try:
        assert tkfont.nametofont("TkDefaultFont", app).cget("size") == app.UI_FONT_SIZE

        captured = {}

        def check_and_close():
            # Any exception raised in here is swallowed by Tk (reported
            # to stderr, not re-raised) and win.destroy() would never
            # run, hanging wait_window() forever -- so catch everything
            # and always destroy, checking the captured result after
            # wait_window() returns instead of asserting in here.
            try:
                win = _find_toplevels(app)[0]
                label = next(l for l in _collect_widgets(win, ttk.Label)
                             if l.cget("text") == "check my font size")
                captured["size"] = _effective_font_size(label)
                captured["window"] = win
            except Exception as e:  # pragma: no cover - diagnostic path
                captured["error"] = e
            finally:
                for w in _find_toplevels(app):
                    w.destroy()

        app.after(10, check_and_close)
        dialogs.show_info("Font Check", "check my font size", parent=app)

        assert "error" not in captured, captured.get("error")
        assert captured.get("size") == app.UI_FONT_SIZE
    finally:
        app.destroy()


# --- Export Gerber's message specifically (the reported example) -----------

def test_export_gerber_no_data_message_goes_through_dialogs_not_messagebox(monkeypatch, tmp_path):
    app = _make_app()
    try:
        from tkinter import filedialog

        out_path = str(tmp_path / "out.gbr")
        monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kw: out_path)

        calls = []
        from hlayout.gui import app as app_module
        monkeypatch.setattr(app_module.dialogs, "show_info",
                             lambda title, msg, parent=None: calls.append((title, msg)))

        app.export_gerber()

        assert calls
        title, msg = calls[0]
        assert title == "Export Gerber"
        assert "No visible layers" in msg
    finally:
        app.destroy()


# --- previously-hardcoded 10pt bold section headers -------------------------

def test_drc_window_section_headers_track_ui_font_size():
    from hlayout.gui.drc_window import open_drc_window
    app = _make_app()
    try:
        win = open_drc_window(app)
        labels = _collect_widgets(win, ttk.Label)
        rules_label = next(l for l in labels if l.cget("text") == "RULES")
        assert _effective_font_size(rules_label) == app.UI_FONT_SIZE
    finally:
        app.destroy()


def test_resistor_designer_section_headers_track_ui_font_size():
    from hlayout.gui.resistor_designer import open_resistor_designer
    app = _make_app()
    try:
        win = open_resistor_designer(app)
        labels = _collect_widgets(win, ttk.Label)
        header = next(l for l in labels if l.cget("text") == "RESISTOR LIST")
        assert _effective_font_size(header) == app.UI_FONT_SIZE
    finally:
        app.destroy()
