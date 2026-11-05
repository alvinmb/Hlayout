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
        lib = Library("MEASURE")
        top = lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.canvas.fit_view()
    app.update()
    app.snap_var.set(False)
    app._on_snap_change()
    app.mode_var.set("measure")
    app._on_mode_change()
    return app, top


def _ev(x, y):
    return types.SimpleNamespace(x=x, y=y)


def test_measure_click_click_computes_correct_distance():
    app, top = _make_app()
    try:
        p0 = app.canvas.to_px(0, 0)
        p1 = app.canvas.to_px(3000, 4000)  # a 3-4-5 triangle: distance = 5000 DBU = 5mm

        app.canvas._on_click(_ev(*p0))
        assert app.canvas._pending_points == [(0, 0)]
        assert app.canvas.find_withtag("measure_result") == ()  # nothing committed yet

        app.canvas._on_click(_ev(*p1))
        assert app.canvas._pending_points == []  # reset after the 2nd click
        items = app.canvas.find_withtag("measure_result")
        assert items  # the result overlay was drawn

        label = app.canvas._format_distance(0, 0, 3000, 4000)
        assert "5.000 mm" in label
    finally:
        app.destroy()


def test_measure_does_not_modify_the_design():
    app, top = _make_app()
    try:
        before_shapes = len(top.shapes)
        before_srefs = len(top.srefs)
        p0 = app.canvas.to_px(0, 0)
        p1 = app.canvas.to_px(1000, 1000)
        app.canvas._on_click(_ev(*p0))
        app.canvas._on_click(_ev(*p1))
        assert len(top.shapes) == before_shapes
        assert len(top.srefs) == before_srefs
    finally:
        app.destroy()


def test_measure_second_measurement_replaces_first_overlay():
    app, top = _make_app()
    try:
        app.canvas._on_click(_ev(*app.canvas.to_px(0, 0)))
        app.canvas._on_click(_ev(*app.canvas.to_px(1000, 0)))
        first_items = set(app.canvas.find_withtag("measure_result"))
        assert first_items

        # starting a fresh measurement clears the previous overlay
        app.canvas._on_click(_ev(*app.canvas.to_px(0, 0)))
        assert app.canvas.find_withtag("measure_result") == ()
        app.canvas._on_click(_ev(*app.canvas.to_px(2000, 0)))
        second_items = set(app.canvas.find_withtag("measure_result"))
        assert second_items
        assert second_items.isdisjoint(first_items)
    finally:
        app.destroy()


def test_measure_cancel_clears_pending_and_overlay():
    app, top = _make_app()
    try:
        app.canvas._on_click(_ev(*app.canvas.to_px(0, 0)))
        app.canvas._on_click(_ev(*app.canvas.to_px(1000, 0)))
        assert app.canvas.find_withtag("measure_result")

        app.canvas.cancel_draw()
        assert app.canvas._pending_points == []
        assert app.canvas.find_withtag("measure_result") == ()
    finally:
        app.destroy()


def test_measure_mode_hint_and_menu_wired():
    app, top = _make_app()
    try:
        assert "Measure" in app.canvas._mode_hint()
        # View menu has a Measure radiobutton driving the same mode_var
        assert app.mode_var.get() == "measure"
    finally:
        app.destroy()
