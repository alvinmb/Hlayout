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
        lib = Library("ORTHO")
        top = lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.canvas.fit_view()
    app.update()
    return app, top


def _ev(x, y):
    return types.SimpleNamespace(x=x, y=y)


def test_ortho_enabled_by_default():
    app, top = _make_app()
    try:
        assert app.canvas.ortho_enabled is True
        assert app.ortho_var.get() is True
    finally:
        app.destroy()


def test_constrain_to_ortho_snaps_a_near_horizontal_drag_to_flat():
    app, top = _make_app()
    try:
        # a mostly-horizontal, slightly-off-angle candidate should land
        # exactly on the horizontal (y unchanged) from the anchor point
        x, y = app.canvas._constrain_to_ortho(0, 0, 1000, 40)
        assert y == 0
        assert x > 0
    finally:
        app.destroy()


def test_constrain_to_ortho_snaps_a_near_diagonal_drag_to_45():
    app, top = _make_app()
    try:
        # close to a 45-degree diagonal (dx != dy) should snap so dx == dy
        x, y = app.canvas._constrain_to_ortho(0, 0, 1000, 950)
        assert x == y
        assert x > 0
    finally:
        app.destroy()


def test_constrain_to_ortho_preserves_distance_rounded_to_grid():
    app, top = _make_app()
    try:
        app.canvas.snap_dbu = 100
        app.canvas.snap_enabled = True
        x, y = app.canvas._constrain_to_ortho(0, 0, 950, 0)
        # length (950) rounds to the nearest 100 DBU grid step (900 or
        # 1000) along the constrained (horizontal) direction
        assert y == 0
        assert x % 100 == 0
    finally:
        app.destroy()


def test_path_drawing_constrains_second_point_to_ortho():
    app, top = _make_app()
    try:
        app.mode_var.set("path")
        app._on_mode_change()
        app.canvas.snap_enabled = False  # isolate the ortho constraint from grid snap
        app.canvas._on_click(_ev(*app.canvas.to_px(0, 0)))
        # a point that is NOT on any clean 0/45/90 line from the origin
        raw_x, raw_y = 1000, 130
        app.canvas._on_click(_ev(*app.canvas.to_px(raw_x, raw_y)))
        assert app.canvas._pending_points[0] == (0, 0)
        px, py = app.canvas._pending_points[1]
        assert py == 0  # snapped flat, not the raw 130
        assert px != raw_x or py != raw_y
    finally:
        app.destroy()


def test_path_drawing_free_angle_when_ortho_disabled():
    app, top = _make_app()
    try:
        app.mode_var.set("path")
        app._on_mode_change()
        app.canvas.snap_enabled = False
        app.canvas.ortho_enabled = False
        app.canvas._on_click(_ev(*app.canvas.to_px(0, 0)))
        app.canvas._on_click(_ev(*app.canvas.to_px(1000, 130)))
        assert app.canvas._pending_points[1] == (1000, 130)
    finally:
        app.destroy()


def test_poly_drawing_constrains_each_segment_to_ortho():
    # Polygon drawing honours the Ortho/45 constraint too -- each edge
    # you draw is forced to 0/45/90 from the previous point.
    app, top = _make_app()
    try:
        app.mode_var.set("poly")
        app._on_mode_change()
        app.canvas.snap_enabled = False
        app.canvas._on_click(_ev(*app.canvas.to_px(0, 0)))
        app.canvas._on_click(_ev(*app.canvas.to_px(1000, 130)))   # near-flat
        px, py = app.canvas._pending_points[1]
        assert py == 0 and (px, py) != (1000, 130)
        app.canvas._on_click(_ev(*app.canvas.to_px(px + 1000, py + 940)))  # near-45
        qx, qy = app.canvas._pending_points[2]
        assert abs((qx - px) - (qy - py)) <= 1        # a clean 45 from point 1
    finally:
        app.destroy()


def test_poly_drawing_free_angle_when_ortho_disabled():
    app, top = _make_app()
    try:
        app.mode_var.set("poly")
        app._on_mode_change()
        app.canvas.snap_enabled = False
        app.canvas.ortho_enabled = False
        app.canvas._on_click(_ev(*app.canvas.to_px(0, 0)))
        app.canvas._on_click(_ev(*app.canvas.to_px(1000, 130)))
        assert app.canvas._pending_points[1] == (1000, 130)
    finally:
        app.destroy()


def test_ortho_does_not_constrain_rect_drawing():
    # A rectangle is a 2-click corner-to-corner drag -- ortho doesn't
    # apply, the opposite corner lands exactly where clicked.
    app, top = _make_app()
    try:
        app.mode_var.set("rect")
        app._on_mode_change()
        app.canvas.snap_enabled = False
        app.canvas._on_click(_ev(*app.canvas.to_px(0, 0)))
        app.canvas._on_click(_ev(*app.canvas.to_px(1000, 130)))
        rect = top.shapes[-1]
        assert rect.bbox() == (0, 0, 1000, 130)
    finally:
        app.destroy()


def test_ortho_checkbox_toggles_canvas_flag():
    app, top = _make_app()
    try:
        app.ortho_var.set(False)
        app._on_ortho_change()
        assert app.canvas.ortho_enabled is False
        app.ortho_var.set(True)
        app._on_ortho_change()
        assert app.canvas.ortho_enabled is True
    finally:
        app.destroy()
