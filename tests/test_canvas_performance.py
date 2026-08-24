"""Performance-focused regressions for LayoutCanvas.

These guard against the specific bottlenecks fixed in this round: full
delete-and-recreate redraw() on every pan/drag mouse-motion event, and
an uncapped number of individual grid-dot canvas items. All of this
requires a real Tk canvas (real or Xvfb), so it skips automatically
when no display is available.
"""
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
        lib = Library("PERF")
        lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.canvas.fit_view()
    app.update()
    return app


def _ev(x, y):
    return types.SimpleNamespace(x=x, y=y)


def test_grid_is_one_rasterized_image_even_at_dense_zoom():
    # At a scale just above the "too dense" cutoff, the (cols x rows)
    # dot grid used to grow unbounded -- tens of thousands of
    # create_oval() calls in a single redraw -- which an earlier round
    # capped by thinning the grid out past MAX_GRID_DOTS (an adaptive
    # stride), at the cost of visibly skipped grid points once you
    # zoomed out far enough. The grid is now rasterized into a single
    # PhotoImage/create_image() canvas item instead (see
    # LayoutCanvas._draw_grid_raster()), so every point still gets a
    # dot but the canvas item count stays flat regardless of zoom.
    app = _make_app()
    try:
        canvas = app.canvas
        canvas.scale = 3.5 / canvas.GRID_MINOR_DBU
        canvas.redraw()
        dots = [
            i for i in canvas.find_all()
            if canvas.type(i) == "oval" and "selection" not in canvas.gettags(i)
        ]
        assert dots == []
        grid_items = [i for i in canvas.find_all() if "grid" in canvas.gettags(i)]
        assert len(grid_items) == 1
        assert canvas.type(grid_items[0]) == "image"
    finally:
        app.destroy()


def test_pan_translates_existing_items_instead_of_recreating_them():
    app = _make_app()
    try:
        top = app.session.library.cells["TOP"]
        top.add_rectangle(0, 0, 0, 1000, 1000)
        app.canvas.redraw()
        before_ids = set(app.canvas.find_all())
        assert before_ids

        app.canvas._pan_start_evt(_ev(100, 100))
        app.canvas._pan_move_evt(_ev(110, 105))
        app.canvas._pan_move_evt(_ev(122, 111))
        # mid-pan: same canvas items, just translated -- no redraw ran
        assert set(app.canvas.find_all()) == before_ids
        assert app.canvas._pan_start is not None

        app.canvas._pan_end_evt(_ev(122, 111))
        assert app.canvas._pan_start is None
    finally:
        app.destroy()


def test_shape_drag_coalesces_redraws_into_one_idle_callback():
    app = _make_app()
    try:
        from hlayout.gui.canvas import _geom_snapshot

        top = app.session.library.cells["TOP"]
        rect = top.add_rectangle(0, 0, 0, 1000, 1000)
        app.canvas.redraw()
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", rect)
        app.canvas._drag_start_mouse = app.canvas.to_dbu(0, 0)
        app.canvas._drag_snapshot = _geom_snapshot("shape", rect)

        redraw_calls = []
        orig_redraw = app.canvas.redraw

        def counting_redraw():
            redraw_calls.append(1)
            orig_redraw()

        app.canvas.redraw = counting_redraw
        try:
            for i in range(1, 21):
                app.canvas._on_drag_motion(_ev(i, i))
            # 20 raw motion events: none should have triggered a
            # synchronous redraw, only a scheduled one.
            assert redraw_calls == []
            assert app.canvas._redraw_scheduled is True

            app.update()  # pump the Tk idle queue
            assert len(redraw_calls) == 1
            assert app.canvas._redraw_scheduled is False
        finally:
            app.canvas.redraw = orig_redraw
    finally:
        app.destroy()


def test_shape_drag_still_updates_geometry_synchronously():
    # The coalesced *visual* redraw must not delay the *data* update --
    # shape.points changes immediately on every motion event, only the
    # canvas redraw is deferred.
    app = _make_app()
    try:
        from hlayout.gui.canvas import _geom_snapshot

        top = app.session.library.cells["TOP"]
        rect = top.add_rectangle(0, 0, 0, 1000, 1000)
        app.canvas.redraw()
        orig_points = list(rect.points)
        app.canvas.draw_mode = "select"
        app.canvas.selected = ("shape", rect)
        app.canvas._drag_start_mouse = app.canvas.to_dbu(0, 0)
        app.canvas._drag_snapshot = _geom_snapshot("shape", rect)

        target_px = app.canvas.to_px(200, -200)
        app.canvas._on_drag_motion(_ev(*target_px))
        assert rect.points != orig_points  # no app.update() call needed
    finally:
        app.destroy()
