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


def _mkevent(x, y):
    class _E:
        pass
    e = _E()
    e.x, e.y = x, y
    return e


def test_route_mode_starts_on_layer_a_and_via_switches_to_layer_b():
    app = _make_app()
    try:
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
        # The first segment (layer 0) plus the via's two pads (layer 0
        # and layer 1) should now be committed.
        paths = [s for s in cell.shapes if isinstance(s, Path)]
        assert any(p.layer == 0 for p in paths)
        layers_present = {s.layer for s in cell.shapes}
        assert 0 in layers_present and 1 in layers_present
    finally:
        app.destroy()


def test_route_via_places_pads_on_both_configured_layers():
    app = _make_app()
    try:
        app.canvas.route_layer_a = 2
        app.canvas.route_layer_b = 5
        app.canvas.route_via_size = 80
        app.canvas.set_mode("route")

        p0 = app.canvas.to_px(0, 0)
        app.canvas._on_click(_mkevent(int(p0[0]), int(p0[1])))
        app.canvas._route_drop_via()

        cell = app.session.cell
        layers = {s.layer for s in cell.shapes}
        assert {2, 5}.issubset(layers)
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
        app.canvas.route_layer_a = 0
        app.canvas.route_layer_b = 1
        app.canvas.set_mode("route")
        p0 = app.canvas.to_px(0, 0)
        p1 = app.canvas.to_px(500, 0)
        app.canvas._on_click(_mkevent(int(p0[0]), int(p0[1])))
        app.canvas._on_click(_mkevent(int(p1[0]), int(p1[1])))
        app.canvas._route_drop_via()  # commits segment 1 + via, switches to layer B
        before_count = len(app.session.cell.shapes)

        p2 = app.canvas.to_px(500, 500)
        app.canvas._on_click(_mkevent(int(p2[0]), int(p2[1])))
        app.canvas.cancel_draw()

        # The pending (uncommitted) second segment is gone, but the
        # already-committed first segment + via pads remain.
        assert len(app.session.cell.shapes) == before_count
        assert app.canvas._pending_points == []
        assert app.canvas._route_active_layer == app.canvas.route_layer_a
    finally:
        app.destroy()


def test_route_via_drop_is_undoable_as_one_step():
    app = _make_app()
    try:
        app.canvas.route_layer_a = 0
        app.canvas.route_layer_b = 1
        app.canvas.set_mode("route")
        p0 = app.canvas.to_px(0, 0)
        p1 = app.canvas.to_px(500, 0)
        app.canvas._on_click(_mkevent(int(p0[0]), int(p0[1])))
        app.canvas._on_click(_mkevent(int(p1[0]), int(p1[1])))
        app.canvas._route_drop_via()

        assert len(app.session.cell.shapes) > 0
        assert app.session.undo() is True
        assert len(app.session.cell.shapes) == 0
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
        app.route_via_size_var.set(250)
        app.route_via_shape_var.set("round")
        app._on_route_opts_change()
        assert app.canvas.route_layer_a == 10
        assert app.canvas.route_layer_b == 20
        assert app.canvas.route_via_size == 250
        assert app.canvas.route_via_shape == "round"
    finally:
        app.destroy()


# --- dielectric via window (Setup > Route Layers...) ------------------------
# Reported request (verbatim): "route layer dialogue menu should include
# another parameter the size of the window in the dielectric layer that
# sets between the ist and 2 routing layers" -- a same-centred opening
# cut in the dielectric between Layer A/B, normally smaller than the
# via pads themselves, which actually defines the electrical contact
# area in a real process.

def test_on_route_opts_change_applies_dielectric_window_to_canvas():
    app = _make_app()
    try:
        app.session.library.layers.set_style(9, name="DIEL")
        app.route_dielectric_layer_var.set("9: DIEL")
        app.route_dielectric_window_var.set(20)
        app._on_route_opts_change()
        assert app.canvas.route_dielectric_layer == 9
        assert app.canvas.route_dielectric_window == 20
    finally:
        app.destroy()


def test_on_route_opts_change_none_dielectric_disables_window():
    app = _make_app()
    try:
        app.route_dielectric_layer_var.set("(none)")
        app.route_dielectric_window_var.set(0)
        app._on_route_opts_change()
        assert app.canvas.route_dielectric_layer is None
        assert app.canvas.route_dielectric_window is None
    finally:
        app.destroy()


def test_route_via_with_dielectric_configured_draws_the_window():
    app = _make_app()
    try:
        app.canvas.route_layer_a = 2
        app.canvas.route_layer_b = 5
        app.canvas.route_via_size = 80
        app.canvas.route_dielectric_layer = 9
        app.canvas.route_dielectric_window = 20
        app.canvas.set_mode("route")

        p0 = app.canvas.to_px(0, 0)
        app.canvas._on_click(_mkevent(int(p0[0]), int(p0[1])))
        app.canvas._route_drop_via()

        cell = app.session.cell
        layers = {s.layer for s in cell.shapes}
        assert {2, 5, 9}.issubset(layers)
    finally:
        app.destroy()


def test_route_via_with_no_dielectric_configured_draws_no_window():
    # A real via always has a dielectric window (Setup > Route Layers...
    # requires one), but the underlying canvas/Session.drop_via() plumbing
    # still tolerates None/off for scripted geometry that isn't modelling
    # a real dielectric stack -- explicitly opt out here to exercise that.
    app = _make_app()
    try:
        app.canvas.route_layer_a = 2
        app.canvas.route_layer_b = 5
        app.canvas.route_via_size = 80
        app.canvas.route_dielectric_layer = None
        app.canvas.route_dielectric_window = None
        app.canvas.set_mode("route")
        assert app.canvas.route_dielectric_layer is None

        p0 = app.canvas.to_px(0, 0)
        app.canvas._on_click(_mkevent(int(p0[0]), int(p0[1])))
        app.canvas._route_drop_via()

        cell = app.session.cell
        assert len(cell.shapes) == 2
    finally:
        app.destroy()


def test_route_layers_dialog_saves_and_reloads_settings_from_technology_file():
    # The core of the request: configure it once, and a later session
    # (a fresh App over the same library, simulating a save/reopen)
    # sees it again with no re-entry.
    from hlayout.io.techfile import load_route_rules, save_route_rules

    app = _make_app()
    try:
        app.session.library.layers.set_style(9, name="DIEL")
        save_route_rules(app.session.library, {
            "layer_a": 2, "layer_b": 5, "via_size": 80, "via_shape": "round",
            "dielectric_layer": 9, "dielectric_window": 120,
        })

        # A fresh App instance over the SAME library, standing in for
        # "close and reopen the design" -- the point being nothing here
        # is re-entered by hand.
        from hlayout.gui.app import App
        app2 = App(app.session.library)
        try:
            assert app2.route_layer_a_var.get() == app2._layer_label(2)
            assert app2.route_layer_b_var.get() == app2._layer_label(5)
            assert app2.route_via_size_var.get() == 80
            assert app2.route_via_shape_var.get() == "round"
            assert app2.route_dielectric_layer_var.get() == "9: DIEL"
            assert app2.route_dielectric_window_var.get() == 120
            assert app2.canvas.route_dielectric_layer == 9
            assert app2.canvas.route_dielectric_window == 120
        finally:
            app2.destroy()
    finally:
        app.destroy()


def test_loading_an_old_pre_dielectric_technology_file_still_gets_a_real_window():
    # An old saved route_rules blob (from before this feature required a
    # window, or one with an invalid/undersized window) should still end
    # up with a real dielectric layer and a window at least as large as
    # the via size when reloaded -- never silently back to "no window".
    from hlayout.io.techfile import save_route_rules

    app = _make_app()
    try:
        save_route_rules(app.session.library, {
            "layer_a": 0, "layer_b": 1, "via_size": 100, "via_shape": "square",
            "dielectric_layer": None, "dielectric_window": None,
        })
        app._reload_route_settings_from_library()
        assert app.canvas.route_dielectric_layer is not None
        assert app.canvas.route_dielectric_layer not in (0, 1)
        assert app.canvas.route_dielectric_window >= app.canvas.route_via_size
    finally:
        app.destroy()


def test_route_layers_dialog_persists_dielectric_settings_on_ok():
    from hlayout.io.techfile import load_route_rules

    app = _make_app()
    try:
        app.session.library.layers.set_style(9, name="DIEL")
        app.show_route_layers_dialog()
        win = next(w for w in app.winfo_children() if isinstance(w, tk.Toplevel))

        from tkinter import ttk
        combos = [w for w in win.winfo_children()[0].winfo_children()
                  if isinstance(w, ttk.Combobox)]
        # grid order: Layer A, Layer B, Via shape, Dielectric layer
        dielectric_combo = combos[-1]
        dielectric_combo.set("9: DIEL")

        spins = [w for w in win.winfo_children()[0].winfo_children()
                 if isinstance(w, ttk.Spinbox)]
        # grid order: Via size, Dielectric window
        window_spin = spins[-1]
        window_spin.delete(0, "end")
        window_spin.insert(0, "0.12")  # mm -- 120 DBU, >= the default via size (100 DBU)

        def _all_buttons(w):
            found = []
            for c in w.winfo_children():
                if isinstance(c, ttk.Button):
                    found.append(c)
                found.extend(_all_buttons(c))
            return found

        ok_btn = next(b for b in _all_buttons(win) if b.cget("text") == "OK")
        ok_btn.invoke()

        rules = load_route_rules(app.session.library)
        assert rules["dielectric_layer"] == 9
        assert rules["dielectric_window"] == 120
    finally:
        app.destroy()


def test_route_layers_dialog_rejects_a_window_smaller_than_the_via_size():
    # Reported requirement (verbatim): "dielectric window is normally
    # larger or equal to the conductor via" -- the dialog should refuse
    # to save a window smaller than the via size rather than silently
    # accepting an unphysical setup.
    app = _make_app()
    try:
        app.show_route_layers_dialog()
        win = next(w for w in app.winfo_children() if isinstance(w, tk.Toplevel))
        from tkinter import ttk

        spins = [w for w in win.winfo_children()[0].winfo_children()
                 if isinstance(w, ttk.Spinbox)]
        via_size_spin, window_spin = spins[0], spins[-1]
        via_size_spin.delete(0, "end")
        via_size_spin.insert(0, "0.1")  # mm -- 100 DBU
        window_spin.delete(0, "end")
        window_spin.insert(0, "0.05")  # mm -- 50 DBU, smaller than the via size -- invalid

        def _all_buttons(w):
            found = []
            for c in w.winfo_children():
                if isinstance(c, ttk.Button):
                    found.append(c)
                found.extend(_all_buttons(c))
            return found

        ok_btn = next(b for b in _all_buttons(win) if b.cget("text") == "OK")
        ok_btn.invoke()

        # The dialog stays open (OK didn't close it) and reports an error.
        assert win.winfo_exists()
        win.destroy()
    finally:
        app.destroy()


def test_route_layers_dialog_has_no_none_option_for_the_dielectric_layer():
    # Reported requirement (verbatim): "You never have no window on the
    # dielectric layer" -- Setup > Route Layers... must always require a
    # real dielectric layer, with no way to opt out via the dialog.
    app = _make_app()
    try:
        app.show_route_layers_dialog()
        win = next(w for w in app.winfo_children() if isinstance(w, tk.Toplevel))
        from tkinter import ttk
        combos = [w for w in win.winfo_children()[0].winfo_children()
                  if isinstance(w, ttk.Combobox)]
        dielectric_combo = combos[-1]
        assert "(none)" not in dielectric_combo["values"]
        win.destroy()
    finally:
        app.destroy()


def test_fresh_library_already_has_a_real_dielectric_window_configured():
    # A real via always has a dielectric window, so even a brand-new
    # design (no technology file loaded yet) should start with a real
    # dielectric layer/window rather than "(none)"/0.
    app = _make_app()
    try:
        assert app.canvas.route_dielectric_layer is not None
        assert app.canvas.route_dielectric_window is not None
        assert app.canvas.route_dielectric_window >= app.canvas.route_via_size
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
        app.session.library.layers.set_style(9, name="DIEL")
        app.route_layer_a_var.set(app._layer_label(0))
        app.route_layer_b_var.set(app._layer_label(1))
        app.route_dielectric_layer_var.set("9: DIEL")
        app.route_dielectric_window_var.set(20)
        app._on_route_opts_change()
        assert app.route_dielectric_layer_var.get() == "9: DIEL"

        # Rename layer 9 the same way the Layers tab does: mutate the
        # layer, then fire the panel's on_change callback (wired to
        # App.refresh_all).
        app.session.library.layers.set_style(9, name="RENAMED")
        app.refresh_all()

        assert app.route_dielectric_layer_var.get() == "9: RENAMED"
        assert app.route_layer_a_var.get() == app._layer_label(0)
        assert app.route_layer_b_var.get() == app._layer_label(1)
    finally:
        app.destroy()


def test_renaming_a_layer_updates_what_a_freshly_opened_route_dialog_shows():
    app = _make_app()
    try:
        app.session.library.layers.set_style(9, name="DIEL")
        app.route_layer_a_var.set(app._layer_label(0))
        app.route_layer_b_var.set(app._layer_label(1))
        app.route_dielectric_layer_var.set("9: DIEL")
        app.route_dielectric_window_var.set(20)
        app._on_route_opts_change()

        app.session.library.layers.set_style(9, name="RENAMED")
        app.refresh_all()

        app.show_route_layers_dialog()
        win = next(w for w in app.winfo_children() if isinstance(w, tk.Toplevel))
        from tkinter import ttk
        combos = [w for w in win.winfo_children()[0].winfo_children()
                  if isinstance(w, ttk.Combobox)]
        dielectric_combo = combos[-1]
        assert dielectric_combo.get() == "9: RENAMED"
        win.destroy()
    finally:
        app.destroy()
