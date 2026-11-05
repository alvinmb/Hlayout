# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""PowerShell/cmd.exe-style line editing for Tk Entry widgets.

Tk's default Entry bindings cover the basics (Home/End, Left/Right,
Backspace/Delete) but are missing several keys every Windows console
user expects: Ctrl+A to select the whole line, Ctrl+Left/Right to jump
by word, Ctrl+Backspace/Delete to delete a word, and Escape to clear
the line outright. This module adds those, and optionally wires
Up/Down to history recall (the same "recall a previous command" feel
as PowerShell's PSReadLine).
"""

import tkinter as tk


def _word_left(text, pos):
    i = pos
    while i > 0 and text[i - 1].isspace():
        i -= 1
    while i > 0 and not text[i - 1].isspace():
        i -= 1
    return i


def _word_right(text, pos):
    i = pos
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    while i < n and not text[i].isspace():
        i += 1
    return i


def _set_text(entry, text):
    entry.delete(0, tk.END)
    entry.insert(0, text)
    entry.icursor(tk.END)


def bind_console_style_editing(entry, prev_history=None, next_history=None):
    """Bind PowerShell/cmd.exe-style editing keys to a Tk Entry.

    prev_history/next_history, if given, are callables taking the
    entry's current text and returning the text to show — wired to
    Up/Down, matching the console history-recall convention.
    """

    def select_all(event=None):
        entry.select_range(0, tk.END)
        entry.icursor(tk.END)
        return "break"

    def home(event=None):
        entry.icursor(0)
        entry.selection_clear()
        return "break"

    def end(event=None):
        entry.icursor(tk.END)
        entry.selection_clear()
        return "break"

    def word_left(event=None):
        entry.icursor(_word_left(entry.get(), entry.index(tk.INSERT)))
        return "break"

    def word_right(event=None):
        entry.icursor(_word_right(entry.get(), entry.index(tk.INSERT)))
        return "break"

    def delete_word_back(event=None):
        pos = entry.index(tk.INSERT)
        text = entry.get()
        newpos = _word_left(text, pos)
        entry.delete(newpos, pos)
        entry.icursor(newpos)
        return "break"

    def delete_word_forward(event=None):
        pos = entry.index(tk.INSERT)
        text = entry.get()
        newpos = _word_right(text, pos)
        entry.delete(pos, newpos)
        return "break"

    def clear_line(event=None):
        entry.delete(0, tk.END)
        return "break"

    entry.bind("<Control-a>", select_all)
    entry.bind("<Control-A>", select_all)
    entry.bind("<Home>", home)
    entry.bind("<End>", end)
    entry.bind("<Control-Left>", word_left)
    entry.bind("<Control-Right>", word_right)
    entry.bind("<Control-BackSpace>", delete_word_back)
    entry.bind("<Control-Delete>", delete_word_forward)
    entry.bind("<Escape>", clear_line)

    if prev_history is not None and next_history is not None:
        entry.bind("<Up>", lambda e: (_set_text(entry, prev_history(entry.get())), "break")[1])
        entry.bind("<Down>", lambda e: (_set_text(entry, next_history(entry.get())), "break")[1])
