# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")


def _make_app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    try:
        lib = Library("VIS")
        top = lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.canvas.fit_view()
    app.update()
    app.snap_var.set(False)
    app._on_snap_change()
    app.mode_var.set("select")
    app._on_mode_change()
    return app, top


def _ev(x, y):
    return types.SimpleNamespace(x=x, y=y)


def test_click_does_not_select_a_shape_on_a_hidden_layer():
    app, top = _make_app()
    try:
        rect = top.add_rectangle(0, -1000, -1000, 1000, 1000)
        app.canvas.redraw()
        app.update()
        px, py = app.canvas.to_px(0, 0)

        app.canvas._select_click_impl(_ev(px, py))
        assert app.canvas.selected == ("shape", rect)

        app.canvas.selected = None
        app.canvas.library.layers[0].visible = False
        app.canvas.redraw()
        app.update()

        app.canvas._select_click_impl(_ev(px, py))
        assert app.canvas.selected is None
    finally:
        app.destroy()


def test_vertex_and_edge_hit_test_skip_hidden_layers():
    app, top = _make_app()
    try:
        poly = top.add_polygon(0, [(0, 0), (2000, 0), (2000, 2000), (0, 2000)])
        app.canvas.redraw()
        app.update()
        vx, vy = app.canvas.to_px(0, 0)

        assert app.canvas._hit_test_vertex(vx, vy) == (poly, 0)

        app.canvas.library.layers[0].visible = False
        app.canvas.redraw()
        app.update()

        assert app.canvas._hit_test_vertex(vx, vy) is None
        ex, ey = app.canvas.to_px(1000, 0)
        assert app.canvas._hit_test_edge(ex, ey) is None
    finally:
        app.destroy()


def test_marquee_window_select_skips_hidden_layer_shapes():
    app, top = _make_app()
    try:
        visible_rect = top.add_rectangle(0, -1000, -1000, 1000, 1000)
        hidden_rect = top.add_rectangle(1, -1000, -1000, 1000, 1000)
        app.canvas.library.layers[1].visible = False
        app.canvas.redraw()
        app.update()

        p0 = app.canvas.to_px(-2000, -2000)
        p1 = app.canvas.to_px(2000, 2000)
        app.canvas._marquee_start_mouse = p0
        app.canvas._finish_marquee_impl(_ev(*p1))

        assert ("shape", visible_rect) in app.canvas.selected_multi
        assert ("shape", hidden_rect) not in app.canvas.selected_multi
    finally:
        app.destroy()


def test_shape_on_visible_layer_still_selects_normally():
    app, top = _make_app()
    try:
        rect = top.add_rectangle(0, -1000, -1000, 1000, 1000)
        app.canvas.redraw()
        app.update()
        px, py = app.canvas.to_px(0, 0)

        app.canvas._select_click_impl(_ev(px, py))
        assert app.canvas.selected == ("shape", rect)
    finally:
        app.destroy()
