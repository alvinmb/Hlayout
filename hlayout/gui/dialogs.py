# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tk-drawn replacements for tkinter.messagebox's showinfo/showerror/
showwarning/askyesno.

tkinter.messagebox invokes Tcl's tk_messageBox, which on Windows
delegates straight to the native Win32 MessageBox() dialog. That
dialog is drawn by the OS itself, not by Tk, so it ignores every Tk
font setting entirely -- exactly the same problem this app already
hit, and already solved, for its top-level menu bar (see
App._build_menu's docstring: a native tk.Menu installed as a window's
system menu bar is drawn by Windows and ignores -font too, which is
why the menu bar is an ordinary row of buttons instead). The visible
result before this fix: every info/error/warning/confirmation popup
in the app rendered in a small system font while the rest of the UI
was a consistent 14pt (App.UI_FONT_SIZE) -- most noticeable on a
message someone actually reads closely, like the Export Gerber
summary listing the files just written, confirmed live to still be
rendering at the small native size even after every other dialog and
the main window had already been resized.

These functions build a plain ttk-based Toplevel instead: modal
(grab_set), centered over the parent window, Escape/Enter bound, with
a coloured glyph standing in for the usual info/warning/error icon.
Because every widget here is an ordinary ttk widget, it picks up
TkDefaultFont automatically -- and therefore App.UI_FONT_SIZE -- with
no explicit font=... needed, the same way the rest of the app's
widgets already do.
"""

import tkinter as tk
from tkinter import ttk

_INFO = ("ℹ", "#0969da")      # "i" in a circle, blue
_WARNING = ("⚠", "#9a6700")   # triangle, amber
_ERROR = ("✕", "#cf222e")     # heavy X, red


def _center_over(win, parent):
    win.update_idletasks()
    try:
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
    except tk.TclError:
        px = py = 0
        pw = ph = 0
    ww, wh = win.winfo_width(), win.winfo_height()
    x = px + max((pw - ww) // 2, 0)
    y = py + max((ph - wh) // 3, 0)
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")


def _build(parent, title, message, glyph_and_color):
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.resizable(False, False)

    body = ttk.Frame(win, padding=16)
    body.pack(fill="both", expand=True)

    row = ttk.Frame(body)
    row.pack(fill="x", pady=(0, 16))
    glyph, color = glyph_and_color
    ttk.Label(row, text=glyph, foreground=color,
              font=("TkDefaultFont", 20)).pack(side="left", padx=(0, 12), anchor="n")
    ttk.Label(row, text=message, wraplength=380, justify="left").pack(
        side="left", fill="x", expand=True)

    return win, body


def _show(parent, title, message, glyph_and_color, ok_label="OK"):
    win, body = _build(parent, title, message, glyph_and_color)

    btn = ttk.Button(body, text=ok_label, command=win.destroy)
    btn.pack(anchor="e")

    win.bind("<Return>", lambda e: win.destroy())
    win.bind("<Escape>", lambda e: win.destroy())
    win.protocol("WM_DELETE_WINDOW", win.destroy)

    _center_over(win, parent)
    win.grab_set()
    btn.focus_set()
    win.wait_window()


def show_info(title, message, parent):
    """Drop-in replacement for tkinter.messagebox.showinfo -- see
    module docstring for why this exists instead. `parent` is
    required (unlike messagebox, which falls back to a default root)
    since this dialog is centered over it and grabbed relative to it."""
    _show(parent, title, message, _INFO)


def reveal_in_folder(path):
    """Open the folder holding `path` in the system file manager, with
    the file selected where the platform supports it (Explorer, Finder).
    Returns False if nothing could be launched."""
    import os
    import subprocess
    import sys
    path = os.path.abspath(path)
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    try:
        if sys.platform.startswith("win"):
            if os.path.isfile(path):
                subprocess.Popen(["explorer", "/select,", path])
            else:
                os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path] if os.path.isfile(path) else ["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
        return True
    except Exception:
        return False


def show_file_written(title, message, path, parent, extra_buttons=()):
    """Success dialog for an export: `message`, then the full output
    path in a selectable field, with Open Folder (reveals the file in
    Explorer/Finder), Copy Path and OK. `extra_buttons` is a sequence of
    (label, callback) added before OK."""
    import os
    win, body = _build(parent, title, message, _INFO)

    path = os.path.abspath(path)
    path_var = tk.StringVar(value=path)
    entry = ttk.Entry(body, textvariable=path_var, width=max(48, min(len(path) + 2, 90)),
                      state="readonly")
    entry.pack(fill="x", pady=(0, 12))

    status = tk.StringVar(value="")
    ttk.Label(body, textvariable=status, foreground="#555").pack(anchor="w")

    def copy_path():
        win.clipboard_clear()
        win.clipboard_append(path)
        status.set("Path copied to the clipboard.")

    def open_folder():
        if not reveal_in_folder(path):
            status.set("Could not open the folder.")

    btn_row = ttk.Frame(body)
    btn_row.pack(anchor="e")
    ttk.Button(btn_row, text="Open Folder", command=open_folder).pack(side="left", padx=(6, 0))
    ttk.Button(btn_row, text="Copy Path", command=copy_path).pack(side="left", padx=(6, 0))
    for label, cb in extra_buttons:
        ttk.Button(btn_row, text=label, command=cb).pack(side="left", padx=(6, 0))
    ok = ttk.Button(btn_row, text="OK", command=win.destroy)
    ok.pack(side="left", padx=(6, 0))

    win.bind("<Return>", lambda e: win.destroy())
    win.bind("<Escape>", lambda e: win.destroy())
    win.protocol("WM_DELETE_WINDOW", win.destroy)
    _center_over(win, parent)
    win.grab_set()
    ok.focus_set()
    win.wait_window()


def show_warning(title, message, parent):
    """Drop-in replacement for tkinter.messagebox.showwarning."""
    _show(parent, title, message, _WARNING)


def show_error(title, message, parent):
    """Drop-in replacement for tkinter.messagebox.showerror."""
    _show(parent, title, message, _ERROR)


def ask_yes_no(title, message, parent) -> bool:
    """Drop-in replacement for tkinter.messagebox.askyesno. Returns
    True for Yes, False for No/Escape/closing the window."""
    win, body = _build(parent, title, message, _WARNING)
    result = [False]

    btn_row = ttk.Frame(body)
    btn_row.pack(anchor="e")

    def choose(value):
        result[0] = value
        win.destroy()

    ttk.Button(btn_row, text="No", command=lambda: choose(False)).pack(
        side="left", padx=(0, 6))
    yes_btn = ttk.Button(btn_row, text="Yes", command=lambda: choose(True))
    yes_btn.pack(side="left")

    win.bind("<Return>", lambda e: choose(True))
    win.bind("<Escape>", lambda e: choose(False))
    win.protocol("WM_DELETE_WINDOW", lambda: choose(False))

    _center_over(win, parent)
    win.grab_set()
    yes_btn.focus_set()
    win.wait_window()
    return result[0]


def show_scrollable_text(title, lines, parent, ok_label="OK", cancel_label=None) -> bool:
    """Modal report dialog for output too long/multi-line for
    show_info's fixed-width Label -- e.g. Export to FreeCAD's
    auto-matched-components report. A read-only, scrollable Text
    widget listing `lines` (one per line), with an `ok_label` button
    (returns True) and, if `cancel_label` is given, a Cancel button
    beside it (returns False) -- e.g. for a preview the user can still
    back out of before a file is actually written. Escape/closing the
    window always returns False when a cancel option exists, else
    True (there's nothing to cancel)."""
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.geometry("560x360")
    win.minsize(320, 200)

    # grid, not pack, for the body/button split -- a Tk Text widget's
    # default REQUESTED size is 24 lines tall regardless of how much
    # content it actually holds, which is taller than this whole
    # window; under pack() that request could consume the entire
    # cavity before the button row ever gets laid out, rendering with
    # no visible buttons at all (found live: a user saw exactly this,
    # a report dialog with no way to dismiss it via a button). grid's
    # row weights make the outcome explicit instead of order-dependent:
    # row 0 (the text) is the only one allowed to grow; row 1 (the
    # buttons) always gets its own natural size first, so it can never
    # be squeezed out no matter how tall the text wants to be.
    body = ttk.Frame(win, padding=16)
    body.pack(fill="both", expand=True)
    body.rowconfigure(0, weight=1)
    body.rowconfigure(1, weight=0)
    body.columnconfigure(0, weight=1)

    text_frame = ttk.Frame(body)
    text_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 12))
    text_frame.rowconfigure(0, weight=1)
    text_frame.columnconfigure(0, weight=1)
    scrollbar = ttk.Scrollbar(text_frame, orient="vertical")
    text = tk.Text(text_frame, wrap="word", yscrollcommand=scrollbar.set,
                    relief="flat", borderwidth=0, height=1)
    scrollbar.config(command=text.yview)
    text.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    text.insert("1.0", "\n".join(lines))
    text.configure(state="disabled")

    default_result = False if cancel_label else True
    result = [default_result]

    btn_row = ttk.Frame(body)
    btn_row.grid(row=1, column=0, sticky="e")

    def choose(value):
        result[0] = value
        win.destroy()

    if cancel_label:
        ttk.Button(btn_row, text=cancel_label, command=lambda: choose(False)).pack(
            side="left", padx=(0, 6))
    ok_btn = ttk.Button(btn_row, text=ok_label, command=lambda: choose(True))
    ok_btn.pack(side="left")

    win.bind("<Escape>", lambda e: choose(default_result))
    win.protocol("WM_DELETE_WINDOW", lambda: choose(default_result))

    _center_over(win, parent)
    win.grab_set()
    ok_btn.focus_set()
    win.wait_window()
    return result[0]


def ask_choice(title, message, parent, choices, default=0) -> str:
    """Modal dialog with one button per option. `choices` is a list of
    (key, label) pairs (or plain strings, used as both key and label).
    Returns the chosen key, or None if closed/Escape. `default` indexes
    which button gets focus."""
    win, body = _build(parent, title, message, _WARNING)
    result = [None]

    btn_row = ttk.Frame(body)
    btn_row.pack(anchor="e")

    def choose(value):
        result[0] = value
        win.destroy()

    norm = [(c, c) if isinstance(c, str) else c for c in choices]
    buttons = []
    for key, label in norm:
        b = ttk.Button(btn_row, text=label, command=lambda k=key: choose(k))
        b.pack(side="left", padx=(6, 0))
        buttons.append(b)

    win.bind("<Escape>", lambda e: choose(None))
    win.protocol("WM_DELETE_WINDOW", lambda: choose(None))

    _center_over(win, parent)
    win.grab_set()
    if buttons:
        buttons[max(0, min(default, len(buttons) - 1))].focus_set()
    win.wait_window()
    return result[0]
