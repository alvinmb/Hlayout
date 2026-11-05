# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.gui.commands import CommandInterpreter
from hlayout.scripting import Session, ScriptConsole


def _fresh_interp():
    s = Session.new("HISTTEST")
    s.new_cell("TOP")
    s.clear_undo_history()
    return CommandInterpreter(s, ScriptConsole(s))


# --- command bar Up/Down history recall --------------------------------------
# Per user direction: Up/Down in the command bar cycles through the last
# 10 commands (CommandInterpreter.history, shared with the Script Console).

def test_command_history_caps_at_ten_entries():
    interp = _fresh_interp()
    for i in range(15):
        interp.run(f"CELL TOP")
    assert len(interp.history) == 10


def test_command_history_drops_the_oldest_entries_first():
    interp = _fresh_interp()
    for i in range(12):
        interp.run(f"RECT 0 0,0 {i},{i}")
    # the 12 RECT commands (indices 0..11) get capped to the most recent 10
    # (indices 2..11) -- the two oldest are dropped.
    assert interp.history[0] == "RECT 0 0,0 2,2"
    assert interp.history[-1] == "RECT 0 0,0 11,11"


def test_command_up_recalls_the_most_recent_command_first():
    interp = _fresh_interp()
    interp.run("CELL TOP")
    interp.run("RECT 0 0,0 5,5")
    assert interp.prev_history("") == "RECT 0 0,0 5,5"


def test_command_up_up_down_returns_to_the_more_recent_command():
    interp = _fresh_interp()
    interp.run("CELL A")
    interp.run("CELL B")
    interp.run("CELL C")
    assert interp.prev_history("") == "CELL C"
    assert interp.prev_history("") == "CELL B"
    assert interp.next_history("") == "CELL C"


def test_command_down_past_the_newest_entry_clears_the_line():
    interp = _fresh_interp()
    interp.run("CELL A")
    interp.prev_history("")
    assert interp.next_history("") == ""


def test_command_up_beyond_the_oldest_entry_stays_on_the_oldest():
    interp = _fresh_interp()
    interp.run("CELL A")
    interp.run("CELL B")
    interp.prev_history("")
    interp.prev_history("")
    assert interp.prev_history("") == "CELL A"  # doesn't run off the start


def test_command_history_shared_between_console_and_command_bar():
    # Both the bottom Command Bar and the Script Console route through the
    # same CommandInterpreter -- a command run from either surfaces on Up
    # in the other, since there's only one history list.
    interp = _fresh_interp()
    interp.run("CELL TOP")
    assert interp.prev_history("") == "CELL TOP"
