import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")
from tkinter import ttk

from hlayout.core.library import Library
from hlayout.io.techfile import (
    load_bond_rules,
    load_refdes_annotation_layer,
    load_resistor_geometry_rules,
    save_bond_rules,
    save_resistor_geometry_rules,
)


def _make_app():
    from hlayout.gui.app import App
    lib = Library("SETUPPROPS")
    lib.new_cell("TOP")
    try:
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    return app


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


def test_technology_properties_has_the_five_rule_tabs_and_a_more_tab():
    app = _make_app()
    try:
        win = app.show_technology_properties_dialog()
        nb = next(w for w in _walk(win) if isinstance(w, ttk.Notebook))
        tabs = [nb.tab(t, "text") for t in nb.tabs()]
        assert tabs[:5] == ["Route", "Resistors", "Ref-Des", "Wire Bonds", "Dielectric"]
        assert len(tabs) == 6  # + "More..."
        win.destroy()
    finally:
        app.destroy()


def test_ok_on_a_configured_tech_saves_every_tab_and_closes(tmp_path):
    # With a real technology file loaded (every tab's rules valid), one
    # OK validates + saves all tabs and closes the window.
    from hlayout.io.techfile import load_and_apply_techfile
    app = _make_app()
    try:
        tech = os.path.join(os.path.dirname(__file__), "..",
                            "hlayout", "examples", "ThickFilm_2Con.json")
        load_and_apply_techfile(app.session.library, tech)
        app._on_route_opts_change() if hasattr(app, "_on_route_opts_change") else None
        # the ThickFilm tech names VIA_Layer3_to_Layer5 as the via cell;
        # make sure a cell of that name exists so the Route tab validates.
        if "VIA_Layer3_to_Layer5" not in app.session.library.cells:
            app.session.new_cell("VIA_Layer3_to_Layer5")
            app.session.use_cell("TOP")
        app.route_via_cell_var.set("VIA_Layer3_to_Layer5")

        win = app.show_technology_properties_dialog()
        ok = next(b for b in _walk(win)
                  if isinstance(b, ttk.Button) and b.cget("text") == "OK")
        ok.invoke()
        assert not win.winfo_exists()
        assert load_bond_rules(app.session.library)["layer"] == 59
        assert load_refdes_annotation_layer(app.session.library) == 62
    finally:
        app.destroy()


def test_a_failing_tab_keeps_the_dialog_open_and_selects_that_tab():
    app = _make_app()
    try:
        # A genuine validation error (Layer A == Layer B) keeps OK from
        # closing and selects the offending tab. (A merely *unset* via
        # cell is NOT an error -- it saves blank so you're never trapped.)
        app.route_layer_a_var.set(app._layer_label(0))
        app.route_layer_b_var.set(app._layer_label(0))
        win = app.show_technology_properties_dialog()
        nb = next(w for w in _walk(win) if isinstance(w, ttk.Notebook))
        ok = next(b for b in _walk(win)
                  if isinstance(b, ttk.Button) and b.cget("text") == "OK")
        ok.invoke()
        assert win.winfo_exists()
        assert nb.tab(nb.select(), "text") == "Route"
        win.destroy()
    finally:
        app.destroy()


def test_route_tab_with_no_via_cell_does_not_trap_the_dialog():
    from hlayout.io.techfile import load_route_rules
    app = _make_app()
    try:
        app.route_layer_a_var.set(app._layer_label(0))
        app.route_layer_b_var.set(app._layer_label(1))
        app.route_via_cell_var.set("")               # none exists on a blank lib
        win = app.show_route_layers_dialog()
        # "New Via Cell..." is offered right on the form
        assert any(isinstance(b, ttk.Button) and "Via Cell" in b.cget("text")
                   for b in _walk(win))
        ok = next(b for b in _walk(win)
                  if isinstance(b, ttk.Button) and b.cget("text") == "OK")
        ok.invoke()
        assert not win.winfo_exists()                # closed -- not trapped
        assert load_route_rules(app.session.library)["via_cell"] == ""
    finally:
        app.destroy()


def test_standalone_wire_bond_dialog_still_opens_and_saves():
    app = _make_app()
    try:
        save_bond_rules(app.session.library, {
            "layer": 6, "width": 30, "pad": 100,
            "min_length": 300, "max_length": 5000,
        })
        win = app.show_wire_bond_rules_dialog()
        assert isinstance(win, tk.Toplevel)
        ok = next(b for b in _walk(win)
                  if isinstance(b, ttk.Button) and b.cget("text") == "OK")
        ok.invoke()
        assert not win.winfo_exists()
        assert load_bond_rules(app.session.library)["layer"] == 6
    finally:
        app.destroy()


def test_resistor_geometry_tab_no_longer_has_a_ref_des_layer_field():
    app = _make_app()
    try:
        win = app.show_resistor_geometry_dialog()
        combos = [c for c in _walk(win) if isinstance(c, ttk.Combobox)]
        # ink (fallback), conductor pad, glaze, keep-out -- 4, not 5
        assert len(combos) == 4
        win.destroy()
    finally:
        app.destroy()


def test_ref_des_layer_is_read_back_by_the_ref_des_form():
    from hlayout.io.techfile import save_refdes_annotation_layer
    app = _make_app()
    try:
        save_refdes_annotation_layer(app.session.library, 40)
        win = app.show_refdes_annotation_layer_dialog()
        combo = next(c for c in _walk(win) if isinstance(c, ttk.Combobox))
        assert combo.get() == app._layer_label(40)
        win.destroy()
    finally:
        app.destroy()
