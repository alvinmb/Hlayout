# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Dragging a routed track's segment carries the via at its end: the via's
pads on both conductors, its dielectric window, and the tracks meeting it."""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")


def _app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    from hlayout.io.techfile import (load_dielectric_rules, load_route_rules,
                                     save_dielectric_rules, save_route_rules)
    try:
        lib = Library("SEG")
        top = lib.new_cell("TOP")
        rr = load_route_rules(lib); rr["layer_a"] = 3; rr["layer_b"] = 5
        save_route_rules(lib, rr)
        dr = load_dielectric_rules(lib); dr["layer"] = 4
        save_dielectric_rules(lib, dr)
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.snap_var.set(False)
    app._on_snap_change()
    app.mode_var.set("select")
    app._on_mode_change()
    return app, top


def _ev(x, y):
    return types.SimpleNamespace(x=x, y=y)


def _drag(app, a, b):
    c = app.canvas
    c._select_click(_ev(*c.to_px(*a)))
    assert c.selected[0] == "edge"
    c._on_drag_motion(_ev(*c.to_px(*b)))
    c._on_release(_ev(*c.to_px(*b)))


def _centre(sh):
    x0, y0, x1, y1 = sh.bbox()
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def test_segment_drag_carries_the_via_its_window_and_the_other_layers_track():
    app, top = _app()
    try:
        t1 = top.add_path(3, [(0, 0), (10000, 0), (10000, 5000)], 300)
        pads = [top.add_rectangle(lay, 9800, 4800, 10200, 5200) for lay in (3, 5)]
        win = top.add_rectangle(4, 9700, 4700, 10300, 5300)
        t2 = top.add_path(5, [(10000, 5000), (20000, 5000)], 300)          # leaves along the drag
        t3 = top.add_path(5, [(10000, 5000), (10000, 9000), (15000, 9000)], 300)  # leaves across it
        other = top.add_rectangle(5, 30000, 30000, 30400, 30400)          # not at the via
        app.canvas.fit_view()
        app.canvas.redraw()
        _drag(app, (10000, 2500), (12000, 2500))                           # vertical segment, 2 mm right
        assert t1.points == [(0, 0), (12000, 0), (12000, 5000)]
        assert all(_centre(p) == (12000, 5000) for p in pads) and _centre(win) == (12000, 5000)
        assert t2.points == [(12000, 5000), (20000, 5000)]                # just shorter
        assert t3.points == [(12000, 5000), (12000, 9000), (15000, 9000)]  # its leg slid, still square
        assert _centre(other) == (30200, 30200)
        app._undo()                                                        # one undo puts it all back
        cell = app.canvas.library.cells["TOP"]
        tracks = sorted(tuple(map(tuple, sh.points)) for sh in cell.shapes if hasattr(sh, "width"))
        assert ((0, 0), (10000, 0), (10000, 5000)) in tracks
        assert ((10000, 5000), (10000, 9000), (15000, 9000)) in tracks
        vias = [sh for sh in cell.shapes if not hasattr(sh, "width") and sh.layer in (3, 4, 5)]
        assert sorted({_centre(v) for v in vias}) == [(10000.0, 5000.0), (30200.0, 30200.0)]
    finally:
        app.destroy()


def test_a_plain_bend_carries_nothing():
    app, top = _app()
    try:
        t1 = top.add_path(3, [(0, 0), (10000, 0), (10000, 5000)], 300)
        t2 = top.add_path(3, [(10000, 5000), (20000, 5000)], 300)          # same layer, no via
        app.canvas.fit_view()
        app.canvas.redraw()
        _drag(app, (10000, 2500), (12000, 2500))
        assert t1.points[-1] == (12000, 5000) and t2.points == [(10000, 5000), (20000, 5000)]
    finally:
        app.destroy()


def test_sli_is_not_worked_out_again_until_the_design_changes():
    app, top = _app()
    try:
        calls = []
        real = app.canvas.sli_provider
        app.canvas.sli_provider = lambda: (calls.append(1), real())[1]
        app.canvas.show_sli = True
        top.add_path(3, [(0, 0), (5000, 0)], 300)
        app.canvas.redraw(); app.canvas.redraw(); app.canvas.redraw()
        assert len(calls) == 1                                  # pan / zoom / reselect: cached
        top.add_path(3, [(0, 1000), (5000, 1000)], 300)
        app.canvas.redraw()
        assert len(calls) == 2                                  # the copper changed: redone
    finally:
        app.destroy()


def test_arrow_key_nudge_of_a_segment_carries_the_via_too():
    app, top = _app()
    try:
        t1 = top.add_path(3, [(0, 0), (10000, 0), (10000, 5000)], 300)
        pads = [top.add_rectangle(lay, 9800, 4800, 10200, 5200) for lay in (3, 5)]
        win = top.add_rectangle(4, 9700, 4700, 10300, 5300)
        t2 = top.add_path(5, [(10000, 5000), (20000, 5000)], 300)
        app.canvas.fit_view()
        app.canvas.redraw()
        c = app.canvas
        c._select_click(_ev(*c.to_px(10000, 2500)))
        assert c.selected[0] == "edge"
        step = c.grid_dbu
        assert c.nudge_selected(-step, 0)                                  # Left arrow
        assert t1.points[-1] == (10000 - step, 5000)
        assert all(_centre(p) == (10000 - step, 5000) for p in pads) and _centre(win) == (10000 - step, 5000)
        assert t2.points[0] == (10000 - step, 5000)
    finally:
        app.destroy()


def test_moving_a_via_asks_for_the_dielectric_to_be_redone_once():
    app, top = _app()
    try:
        calls = []
        app.canvas.on_vias_moved = lambda: calls.append(1)
        app.canvas.VIA_FILL_DELAY_MS = 1
        top.add_path(3, [(0, 0), (10000, 0), (10000, 5000)], 300)
        for lay in (3, 5):
            top.add_rectangle(lay, 9800, 4800, 10200, 5200)
        top.add_path(5, [(10000, 5000), (20000, 5000)], 300)
        app.canvas.fit_view(); app.canvas.redraw()
        c = app.canvas
        c._select_click(_ev(*c.to_px(10000, 2500)))
        for _ in range(3):
            c.nudge_selected(-c.grid_dbu, 0)                       # three Left presses
        app.after(50, app.quit); app.mainloop()
        assert calls == [1]                                        # one regeneration, after the last
    finally:
        app.destroy()
