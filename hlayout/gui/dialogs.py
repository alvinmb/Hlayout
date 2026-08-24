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
