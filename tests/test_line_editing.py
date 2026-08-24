import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")

from hlayout.gui.line_editing import _word_left, _word_right, bind_console_style_editing


def test_word_left_right_boundaries():
    text = "s.rect(0, 0, 0, 10, 10)"
    # from the end, word-left should land at the start of "10)"
    pos = len(text)
    pos1 = _word_left(text, pos)
    assert text[pos1:pos] == "10)"
    pos2 = _word_left(text, pos1)
    assert text[pos2:pos1] == "10, "

    # word-right from the start should land after "s.rect(0,"
    pos = 0
    npos = _word_right(text, pos)
    assert text[pos:npos] == "s.rect(0,"


def _make_root():
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    root.geometry("+0+0")
    return root


def _make_entry(root, text=""):
    entry = tk.Entry(root)
    entry.pack()
    if text:
        entry.insert(0, text)
    entry.focus_force()
    root.update()
    return entry


def test_ctrl_a_selects_all():
    root = _make_root()
    try:
        entry = _make_entry(root, "hello world")
        bind_console_style_editing(entry)
        entry.event_generate("<Control-a>")
        root.update()
        assert entry.selection_present()
        assert entry.selection_get() == "hello world"
    finally:
        root.destroy()


def test_escape_clears_line():
    root = _make_root()
    try:
        entry = _make_entry(root, "some text")
        bind_console_style_editing(entry)
        entry.event_generate("<Escape>")
        root.update()
        assert entry.get() == ""
    finally:
        root.destroy()


def test_ctrl_backspace_deletes_word():
    root = _make_root()
    try:
        entry = _make_entry(root, "hello world")
        entry.icursor("end")
        bind_console_style_editing(entry)
        entry.event_generate("<Control-BackSpace>")
        root.update()
        assert entry.get() == "hello "
    finally:
        root.destroy()


def test_history_recall_up_down():
    root = _make_root()
    try:
        entry = _make_entry(root)
        history = ["s.rect(0,0,0,1,1)", "s.text(0,(0,0),'hi')"]
        idx = [len(history)]

        def prev(current):
            idx[0] = max(0, idx[0] - 1)
            return history[idx[0]]

        def nxt(current):
            if idx[0] >= len(history) - 1:
                idx[0] = len(history)
                return ""
            idx[0] += 1
            return history[idx[0]]

        bind_console_style_editing(entry, prev_history=prev, next_history=nxt)
        entry.event_generate("<Up>")
        root.update()
        assert entry.get() == "s.text(0,(0,0),'hi')"
        entry.event_generate("<Up>")
        root.update()
        assert entry.get() == "s.rect(0,0,0,1,1)"
        entry.event_generate("<Down>")
        root.update()
        assert entry.get() == "s.text(0,(0,0),'hi')"
    finally:
        root.destroy()
