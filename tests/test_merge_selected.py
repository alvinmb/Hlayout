# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")
shapely = pytest.importorskip("shapely")

from hlayout.core.errors import GeometryError
from hlayout.core.shapes import Polygon, Rectangle


def _make_app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    try:
        lib = Library("MERGE")
        top = lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.canvas.fit_view()
    app.update()
    app.mode_var.set("select")
    app._on_mode_change()
    return app, top


def test_merge_two_touching_rectangles_same_layer():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 0, 0, 100, 100)
        b = top.add_rectangle(0, 100, 0, 200, 100)  # shares the x=100 edge with `a`
        app.canvas.selected_multi = [("shape", a), ("shape", b)]
        before = len(top.shapes)

        assert app.canvas.merge_selected() is True

        assert len(top.shapes) == before - 1  # two shapes replaced by one
        merged = top.shapes[-1]
        assert isinstance(merged, Polygon)
        assert merged.layer == 0
        # the merged polygon's bounding box covers both rectangles
        xs = [p[0] for p in merged.points]
        ys = [p[1] for p in merged.points]
        assert min(xs) == 0 and max(xs) == 200
        assert min(ys) == 0 and max(ys) == 100
        # the new merged shape is what ends up selected
        assert app.canvas.selected_multi == [("shape", merged)]
    finally:
        app.destroy()


def test_merge_undoes_cleanly():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 0, 0, 100, 100)
        b = top.add_rectangle(0, 100, 0, 200, 100)
        app.canvas.selected_multi = [("shape", a), ("shape", b)]
        before = sorted((s.layer, tuple(s.points)) for s in top.shapes)

        app.canvas.merge_selected()
        assert len(app.session.cell.shapes) == 1  # merged down to one
        app.session.undo()

        # Undo restores via a deep-copied Library snapshot (see
        # Session.checkpoint()/undo()), so the two original shapes come
        # back with new object identity but the same layer/geometry --
        # compare by value, not by "is".
        after = sorted((s.layer, tuple(s.points)) for s in app.session.cell.shapes)
        assert after == before
    finally:
        app.destroy()


def test_merge_non_touching_shapes_raises_and_leaves_selection_alone():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 0, 0, 100, 100)
        b = top.add_rectangle(0, 1000, 0, 1100, 100)  # far away, doesn't touch
        app.canvas.selected_multi = [("shape", a), ("shape", b)]
        before = list(top.shapes)

        with pytest.raises(GeometryError, match="don't touch or overlap"):
            app.canvas.merge_selected()

        assert list(top.shapes) == before  # untouched
    finally:
        app.destroy()


def test_merge_different_layers_raises():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 0, 0, 100, 100)
        b = top.add_rectangle(1, 100, 0, 200, 100)  # touches, but layer 1 not 0
        app.canvas.selected_multi = [("shape", a), ("shape", b)]

        with pytest.raises(GeometryError, match="different layers"):
            app.canvas.merge_selected()
    finally:
        app.destroy()


def test_merge_requires_at_least_two_shapes():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 0, 0, 100, 100)
        app.canvas.selected_multi = [("shape", a)]
        assert app.canvas.merge_selected() is False
        app.canvas.selected_multi = []
        assert app.canvas.merge_selected() is False
    finally:
        app.destroy()


def test_merge_rejects_non_polygon_selection():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 0, 0, 100, 100)
        sref = top.add_sref("TOP", (0, 0))  # not a Polygon/Rectangle
        app.canvas.selected_multi = [("shape", a), ("sref", sref)]
        assert app.canvas.merge_selected() is False
    finally:
        app.destroy()


def test_merge_command_bar_union():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 0, 0, 100, 100)
        b = top.add_rectangle(0, 100, 0, 200, 100)
        app.canvas.selected_multi = [("shape", a), ("shape", b)]
        out = app.interp.run("UNION")
        assert "merged" in out.lower()
        assert len(top.shapes) == 1

        # nothing selected -> a clear error, not a crash
        app.canvas.selected_multi = []
        out2 = app.interp.run("UNION")
        assert out2.startswith("error:")
    finally:
        app.destroy()
