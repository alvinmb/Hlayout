# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")

from hlayout.core.library import Library
from hlayout.core.shapes import Path


def _make_app():
    from hlayout.gui.app import App
    lib = Library("ROUTETEST")
    lib.new_cell("TOP")
    try:
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    return app


def _use_via_cell(app, layer_a, layer_b, name="VIA"):
    """A minimal via cell (a pad on each route layer) set as the Route
    tool's via -- with none configured a via drop opens a modal error."""
    s = app.session
    s.new_cell(name)
    s.use_cell(name)
    s.rect(layer_a, -50, -50, 50, 50)
    s.rect(layer_b, -50, -50, 50, 50)
    s.use_cell("TOP")
    app.canvas.route_via_cell = name


def _mkevent(x, y):
    class _E:
        pass
    e = _E()
    e.x, e.y = x, y
    return e


def test_route_mode_starts_on_layer_a_and_via_switches_to_layer_b():
    app = _make_app()
    try:
        _use_via_cell(app, 0, 1)
        app.canvas.route_layer_a = 0
        app.canvas.route_layer_b = 1
        app.canvas.set_mode("route")
        assert app.canvas._route_active_layer == 0

        app.canvas.draw_mode = "route"
        p0 = app.canvas.to_px(0, 0)
        p1 = app.canvas.to_px(1000, 0)
        app.canvas._on_click(_mkevent(int(p0[0]), int(p0[1])))
        app.canvas._on_click(_mkevent(int(p1[0]), int(p1[1])))

        app.canvas._route_drop_via()

        assert app.canvas._route_active_layer == 1
        cell = app.session.cell
        # the first segment (layer 0) is committed, and the via cell is
        # placed at the segment's end
        paths = [s for s in cell.shapes if isinstance(s, Path)]
        assert any(p.layer == 0 for p in paths)
        vias = [r for r in cell.srefs if r.cell_name == "VIA"]
        assert len(vias) == 1
    finally:
        app.destroy()


def test_route_via_places_the_configured_via_cell():
    app = _make_app()
    try:
        _use_via_cell(app, 2, 5)
        app.canvas.route_layer_a = 2
        app.canvas.route_layer_b = 5
        app.canvas.set_mode("route")

        p0 = app.canvas.to_px(0, 0)
        app.canvas._on_click(_mkevent(int(p0[0]), int(p0[1])))
        app.canvas._route_drop_via()

        vias = [r for r in app.session.cell.srefs if r.cell_name == "VIA"]
        assert len(vias) == 1
        via_layers = {s.layer for s in app.session.library.cells["VIA"].shapes}
        assert {2, 5}.issubset(via_layers)
    finally:
        app.destroy()


def test_route_space_with_no_start_point_does_nothing_harmful():
    app = _make_app()
    try:
        app.canvas.set_mode("route")
        app.canvas._route_drop_via()
        assert app.session.cell.shapes == []
    finally:
        app.destroy()


def test_route_finish_commits_final_segment_without_a_via():
    app = _make_app()
    try:
        app.canvas.route_layer_a = 0
        app.canvas.route_layer_b = 1
        app.canvas.set_mode("route")

        p0 = app.canvas.to_px(0, 0)
        p1 = app.canvas.to_px(500, 0)
        app.canvas._on_click(_mkevent(int(p0[0]), int(p0[1])))
        app.canvas._on_click(_mkevent(int(p1[0]), int(p1[1])))
        app.canvas.finish_draw()

        cell = app.session.cell
        assert len(cell.shapes) == 1
        assert isinstance(cell.shapes[0], Path)
        assert cell.shapes[0].layer == 0
        # No via pads -- finishing doesn't drop one.
        assert app.canvas._route_active_layer == app.canvas.route_layer_a
    finally:
        app.destroy()


def test_route_escape_cancels_current_segment_only():
    app = _make_app()
    try:
        _use_via_cell(app, 0, 1)
        app.canvas.route_layer_a = 0
        app.canvas.route_layer_b = 1
        app.canvas.set_mode("route")
        p0 = app.canvas.to_px(0, 0)
        p1 = app.canvas.to_px(500, 0)
        app.canvas._on_click(_mkevent(int(p0[0]), int(p0[1])))
        app.canvas._on_click(_mkevent(int(p1[0]), int(p1[1])))
        app.canvas._route_drop_via()  # commits segment 1 + via, switches to layer B
        before = (len(app.session.cell.shapes), len(app.session.cell.srefs))

        p2 = app.canvas.to_px(500, 500)
        app.canvas._on_click(_mkevent(int(p2[0]), int(p2[1])))
        app.canvas.cancel_draw()

        # The pending (uncommitted) second segment is gone, but the
        # already-committed first segment + via remain.
        assert (len(app.session.cell.shapes), len(app.session.cell.srefs)) == before
        assert before == (1, 1)
        assert app.canvas._pending_points == []
        assert app.canvas._route_active_layer == app.canvas.route_layer_a
    finally:
        app.destroy()


def test_route_via_drop_is_undoable_as_one_step():
    app = _make_app()
    try:
        _use_via_cell(app, 0, 1)
        app.canvas.route_layer_a = 0
        app.canvas.route_layer_b = 1
        app.canvas.set_mode("route")
        p0 = app.canvas.to_px(0, 0)
        p1 = app.canvas.to_px(500, 0)
        app.canvas._on_click(_mkevent(int(p0[0]), int(p0[1])))
        app.canvas._on_click(_mkevent(int(p1[0]), int(p1[1])))
        app.canvas._route_drop_via()

        cell = app.session.cell
        assert cell.shapes and cell.srefs
        assert app.session.undo() is True
        cell = app.session.cell   # undo restores a snapshot: re-read it
        assert cell.shapes == [] and cell.srefs == []   # segment + via in one step
    finally:
        app.destroy()


def test_selecting_route_mode_via_the_menu_variable_switches_the_canvas():
    app = _make_app()
    try:
        app.mode_var.set("route")
        app._on_mode_change()
        assert app.canvas.draw_mode == "route"
    finally:
        app.destroy()


def test_route_layers_dialog_helper_resolves_by_name_and_number():
    app = _make_app()
    try:
        app.session.library.layers.set_style(9, name="GND")
        assert app._resolve_layer_field("9: GND") == 9
        assert app._resolve_layer_field("GND") == 9
        assert app._resolve_layer_field("3") == 3
    finally:
        app.destroy()


def test_on_route_opts_change_applies_vars_to_canvas():
    app = _make_app()
    try:
        app.session.library.layers.set_style(10, name="L10")
        app.session.library.layers.set_style(20, name="L20")
        app.route_layer_a_var.set("10: L10")
        app.route_layer_b_var.set("20: L20")
        app.route_via_cell_var.set(" VIA ")
        app._on_route_opts_change()
        assert app.canvas.route_layer_a == 10
        assert app.canvas.route_layer_b == 20
        assert app.canvas.route_via_cell == "VIA"
    finally:
        app.destroy()


# --- layer renames must be reflected everywhere a layer name is shown ------
# Reported request (verbatim): "any dialogue that shows layer should show
# layer names from the technology file" -- Route Layers' toolbar-cached
# labels (route_layer_a_var/route_layer_b_var/route_dielectric_layer_var)
# used to keep showing whatever name was current the last time they were
# set, even after the underlying layer was renamed via the Layers tab.

def test_renaming_a_layer_refreshes_the_route_toolbars_cached_labels():
    app = _make_app()
    try:
        app.route_layer_a_var.set(app._layer_label(0))
        app.route_layer_b_var.set(app._layer_label(1))
        app._on_route_opts_change()

        # Rename layer 0 the same way the Layers tab does: mutate the
        # layer, then fire the panel's on_change callback (wired to
        # App.refresh_all).
        app.session.library.layers.set_style(0, name="RENAMED")
        app.refresh_all()

        assert app.route_layer_a_var.get() == app._layer_label(0)
        assert "RENAMED" in app.route_layer_a_var.get()
        assert app.route_layer_b_var.get() == app._layer_label(1)
    finally:
        app.destroy()
