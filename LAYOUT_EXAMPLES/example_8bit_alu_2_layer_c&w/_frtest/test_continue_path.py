# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Edit > Continue Path works from a clicked track (an edge or vertex
grab, not only a whole-path selection) and carries on from the end
nearer the click."""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")


def _app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    try:
        lib = Library("CP")
        top = lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.snap_var.set(False)
    app._on_snap_change()
    app.mode_var.set("select")
    app._on_mode_change()
    return app, top


def _click(app, x, y):
    c = app.canvas
    px, py = c.to_px(x, y)
    c._select_click(types.SimpleNamespace(x=px, y=py))


def test_continue_from_an_edge_grab_near_the_far_end():
    app, top = _app()
    try:
        p = top.add_path(3, [(0, 0), (10000, 0), (10000, 5000)], 300)
        app.canvas.fit_view()
        _click(app, 10000, 4000)                       # edge near the path's last point
        assert app.canvas.selected[0] == "edge"
        assert app.canvas.continue_path()
        assert app.canvas._pending_points == [(0, 0), (10000, 0), (10000, 5000)]
        assert app.canvas._continue_target is p
    finally:
        app.destroy()


def test_continue_from_the_start_reverses_the_points():
    app, top = _app()
    try:
        top.add_path(3, [(0, 0), (10000, 0), (10000, 5000)], 300)
        app.canvas.fit_view()
        _click(app, 1000, 0)                           # edge near the path's first point
        assert app.canvas.continue_path()
        assert app.canvas._pending_points == [(10000, 5000), (10000, 0), (0, 0)]
    finally:
        app.destroy()


def test_menu_item_accepts_an_edge_selection(monkeypatch):
    from hlayout.gui import dialogs
    shown = []
    monkeypatch.setattr(dialogs, "show_info", lambda *a, **k: shown.append(a))
    app, top = _app()
    try:
        top.add_path(3, [(0, 0), (10000, 0)], 300)
        app.canvas.fit_view()
        _click(app, 8000, 0)
        app._continue_path()
        assert not shown and app.canvas.draw_mode == "path"
    finally:
        app.destroy()
