# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Interactive placement: a placed part hangs on the cursor until a click
drops it (LayoutCanvas.start_carry, IPLACE, Place with Mouse)."""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")

from hlayout.scripting import Session


def _board():
    """A 2 x 1 mm two-pad part P (origin at its bottom-left corner) and a
    TOP cell with R1 placed, R1.2 and R2.1 on net N."""
    s = Session.new("IPL")
    s.new_cell("P")
    a = s.rect(3, 0, 0, 400, 1000)
    a.pin = "1"
    b = s.rect(3, 1600, 0, 2000, 1000)
    b.pin = "2"
    s.new_cell("TOP")
    s.sref("P", (0, 0)).label = "R1"
    s.set_net_names({"R1.2": "N", "R2.1": "N"})
    return s


@pytest.fixture
def canvas():
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    from hlayout.gui.canvas import LayoutCanvas
    made = []

    def make(s):
        cv = LayoutCanvas(root, s.library)
        cv.set_cell("TOP")
        cv.checkpoint = s.checkpoint
        cv.snap_enabled, cv.snap_dbu = True, 50
        made.append(cv)
        return cv
    yield make
    root.destroy()


def _move(cv, x, y):
    cv.to_dbu = lambda px, py: (x, y)
    cv._on_motion(types.SimpleNamespace(x=0, y=0))


def test_the_part_follows_the_cursor_centred_and_drops_on_a_click(canvas):
    s = _board()
    cv = canvas(s)
    ref = s.place_cell("P", (0, 0), label="R2")
    done = []
    cv.start_carry(ref, on_done=done.append)
    _move(cv, 10000, 5000)
    assert ref.origin == (9000, 4500)                 # its 2 x 1 mm box centred on the cursor
    _move(cv, 10020, 5040)
    assert ref.origin == (9000, 4550)                 # on the 50 um grid
    cv.rotate_selected(90)                            # R: a quarter turn about the cursor
    assert ref.rotation == 90 and ref.origin == (10500, 4050)
    cv.carry_mirror()                                 # M
    assert ref.x_reflection
    cv._on_click(types.SimpleNamespace(x=0, y=0))
    assert done == [ref] and cv._carry is None
    _move(cv, 0, 0)                                   # dropped: no longer follows
    assert ref.origin != (0, 0)


def test_its_straight_line_interconnects_follow_it(canvas):
    s = _board()
    cv = canvas(s)
    ref = s.place_cell("P", (0, 0), label="R2")
    cv.start_carry(ref)
    _move(cv, 20000, 0)
    seg1 = s.sli()["nets"][0]["segments"]
    _move(cv, 30000, 0)
    seg2 = s.sli()["nets"][0]["segments"]
    assert seg1 != seg2
    # the net runs from R1 pad 2 to R2 pad 1 wherever R2 now is
    (x0, y0, x1, y1), = seg2
    assert {round(x0), round(x1)} == {1800, ref.origin[0] + 200}


def test_esc_calls_cancel_and_undo_takes_the_part_out(canvas):
    s = _board()
    cv = canvas(s)
    n = len(s.cell.srefs)
    ref = s.place_cell("P", (0, 0), label="R2")
    cancelled = []

    def cancel(r):
        cancelled.append(r)
        s.undo()
    cv.start_carry(ref, on_cancel=cancel)
    _move(cv, 5000, 5000)
    cv.cancel_draw()                                  # Esc
    assert cancelled == [ref] and cv._carry is None
    assert len(s.cell.srefs) == n


def test_iplace_command(canvas):
    from hlayout.gui.commands import CommandInterpreter
    s = _board()
    cv = canvas(s)
    ci = CommandInterpreter(s, console=None, view=cv)
    out = ci.run("IPLACE P R2 rotation=90")
    assert "click to drop" in out
    ref = cv._carry["ref"]
    assert ref.label == "R2" and ref.rotation == 90
    cv.cancel_draw()
    assert all(r.label != "R2" for r in s.cell.srefs)
