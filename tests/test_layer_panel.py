import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")

from hlayout.core.library import Library


def _make_app():
    from hlayout.gui.app import App
    try:
        app = App(Library("LP"))
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    return app


def _row(app, n):
    return app.layer_panel._row_state[n]


def _in_sync(app, n):
    """model, BooleanVars and the ttk widget-state flags all agree for row n."""
    layer = app.session.library.layers[n]
    st = _row(app, n)
    return (
        bool(st["visible_var"].get()) == layer.visible
        and st["visible_cb"].instate(["selected"]) == layer.visible
        and bool(st["fill_var"].get()) == layer.fill
        and st["fill_cb"].instate(["selected"]) == layer.fill
    )


def test_clicking_visibility_checkbox_keeps_model_var_and_widget_in_lockstep():
    app = _make_app()
    try:
        st = _row(app, 5)
        assert app.session.library.layers[5].visible is True
        st["visible_cb"].invoke()          # like a real click
        app.update()
        assert app.session.library.layers[5].visible is False
        assert _in_sync(app, 5)
        st["visible_cb"].invoke()
        app.update()
        assert app.session.library.layers[5].visible is True
        assert _in_sync(app, 5)
    finally:
        app.destroy()


def test_all_off_then_toggle_one_back_on_actually_works():
    app = _make_app()
    try:
        app.layer_panel.set_all_visible(False)
        app.update()
        assert all(not app.session.library.layers[n].visible for n in range(64))
        for n in range(64):
            assert _in_sync(app, n)

        _row(app, 3)["visible_cb"].invoke()
        app.update()
        assert app.session.library.layers[3].visible is True
        assert _in_sync(app, 3)
        assert not app.session.library.layers[4].visible   # nothing else moved
    finally:
        app.destroy()


def test_toggling_one_layers_visibility_does_not_disturb_another_rows_fill_box():
    app = _make_app()
    try:
        # turn Fill off on layer 10 via a click
        _row(app, 10)["fill_cb"].invoke()
        app.update()
        assert app.session.library.layers[10].fill is False
        assert _in_sync(app, 10)

        # now toggle visibility on a *different* layer several times
        for _ in range(6):
            _row(app, 2)["visible_cb"].invoke()
            app.update()

        # layer 10's Fill box must still read off, matching the model
        assert app.session.library.layers[10].fill is False
        assert _in_sync(app, 10)
    finally:
        app.destroy()


def test_clicking_fill_checkbox_keeps_everything_in_lockstep():
    app = _make_app()
    try:
        st = _row(app, 7)
        assert app.session.library.layers[7].fill is True
        st["fill_cb"].invoke()
        app.update()
        assert app.session.library.layers[7].fill is False
        assert _in_sync(app, 7)
        # stipple combo follows fill
        assert str(st["stipple_combo"].cget("state")) == "disabled"
        st["fill_cb"].invoke()
        app.update()
        assert app.session.library.layers[7].fill is True
        assert _in_sync(app, 7)
        assert str(st["stipple_combo"].cget("state")) == "readonly"
    finally:
        app.destroy()


def test_external_change_then_sync_updates_the_widget_state_flag():
    app = _make_app()
    try:
        app.session.library.layers[12].visible = False
        app.session.library.layers[12].fill = False
        app.layer_panel.sync()
        app.update()
        assert _in_sync(app, 12)
    finally:
        app.destroy()


def test_restore_keeps_layer_object_identity():
    # Session._restore must copy layer styles into the existing Layer
    # objects, not swap the LayerTable -- otherwise the layer panel's
    # per-row callbacks are left bound to dead objects.
    from hlayout.core.library import Library
    from hlayout.scripting import Session
    s = Session(Library("X"))
    before = [s.library.layers[n] for n in range(64)]
    s.checkpoint()
    s.library.layers[3].visible = False
    s.undo()
    after = [s.library.layers[n] for n in range(64)]
    assert all(a is b for a, b in zip(before, after))
    assert s.library.layers[3].visible is True     # value restored


def test_panel_still_drives_the_live_library_after_an_undo():
    app = _make_app()
    try:
        s = app.session
        s.checkpoint()
        s.library.layers[8].name = "scratch"        # any edit
        app.refresh_all()
        s.undo()                                     # _restore fires
        app.refresh_all()

        # clicking a checkbox in the panel must still move the LIVE layer
        _row(app, 8)["visible_cb"].invoke()
        app.update()
        assert s.library.layers[8].visible is False
        assert _in_sync(app, 8)

        _row(app, 8)["fill_cb"].invoke()
        app.update()
        assert s.library.layers[8].fill is False
        assert _in_sync(app, 8)
    finally:
        app.destroy()


def test_panel_reflects_and_drives_layers_after_loading_a_tech_file(tmp_path):
    import json
    app = _make_app()
    try:
        tech = tmp_path / "t.json"
        tech.write_text(json.dumps({
            "format": "hlayout-tech", "version": 1,
            "layers": [
                {"number": 2, "name": "Metal 1", "color": "#123456",
                 "stipple": 0, "fill": False, "visible": False},
            ],
            "params": {},
        }))
        app.session.load_technology(str(tech))
        app.refresh_all()
        app.update()

        assert _in_sync(app, 2)                       # panel shows the loaded style
        assert app.session.library.layers[2].fill is False
        assert _row(app, 2)["swatch"].cget("bg") == "#123456"

        # and toggling the row still works
        _row(app, 2)["visible_cb"].invoke()
        app.update()
        assert app.session.library.layers[2].visible is True
        assert _in_sync(app, 2)
    finally:
        app.destroy()


def test_sync_is_reentrancy_guarded():
    app = _make_app()
    try:
        lp = app.layer_panel
        lp._syncing = True
        app.session.library.layers[0].visible = False
        lp.sync()                                  # should be a no-op
        assert lp._row_state[0]["visible_var"].get() is True
        lp._syncing = False
    finally:
        app.destroy()
