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

    body = ttk.Frame(win, padding=16)
    body.pack(fill="both", expand=True)

    text_frame = ttk.Frame(body)
    text_frame.pack(fill="both", expand=True, pady=(0, 12))
    scrollbar = ttk.Scrollbar(text_frame, orient="vertical")
    text = tk.Text(text_frame, wrap="word", yscrollcommand=scrollbar.set,
                    relief="flat", borderwidth=0)
    scrollbar.config(command=text.yview)
    text.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="left", fill="y")
    text.insert("1.0", "\n".join(lines))
    text.configure(state="disabled")

    default_result = False if cancel_label else True
    result = [default_result]

    btn_row = ttk.Frame(body)
    btn_row.pack(anchor="e")

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
