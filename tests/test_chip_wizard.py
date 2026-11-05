# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core.errors import LayoutError
from hlayout.core.library import Library
from hlayout.core.shapes import Path
from hlayout.io.gdsio import load_gds, save_gds
from hlayout.scripting import Session

PIN = Session.CHIP_PIN_CELL       # "chip_pin"
PIN1 = Session.CHIP_PIN1_CELL     # "chip_pin1"


def _parts_library(path):
    """A parts library with the two die-pin cells the wizard needs:
    chip_pin -- one 1.8 mm epoxy pad on layer 58; chip_pin1 -- the same
    plus a pin-1 mark on layer 60."""
    lib = Library("PARTS")
    pc = lib.new_cell(PIN)
    pc.add_rectangle(58, -900, -900, 900, 900, pin="1")
    p1 = lib.new_cell(PIN1)
    p1.add_rectangle(58, -900, -900, 900, 900, pin="1")
    p1.add_rectangle(60, -900, 900, -600, 1200)
    save_gds(lib, path)


def _session_with_parts(tmp_path):
    apath = str(tmp_path / "parts.gds")
    _parts_library(apath)
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.attach_library(apath)
    return s, apath


# --- Session.create_chip_cell ------------------------------------------------

def test_create_chip_cell_lays_a_white_outline_and_an_epoxy_pad():
    from hlayout.core.shapes import Rectangle
    s = Session.new("D")
    s.new_cell("TOP")
    name = s.create_chip_cell("CHIP", 2000, 1000, graphics_layer=60,
                              epoxy_length=1800, epoxy_width=1200, epoxy_layer=58)
    assert name == "CHIP"
    assert s.current_cell_name == "CHIP"

    body = s.library.cells["CHIP"].shapes
    outline = next(sh for sh in body if isinstance(sh, Path))
    assert outline.layer == 60
    xs = [p[0] for p in outline.points]
    ys = [p[1] for p in outline.points]
    assert (min(xs), max(xs)) == (-1000, 1000)
    assert (min(ys), max(ys)) == (-500, 500)
    assert outline.points[0] == outline.points[-1]

    epoxy = next(sh for sh in body if isinstance(sh, Rectangle))
    assert epoxy.layer == 58
    assert epoxy.bbox() == (-900, -600, 900, 600)     # rectangular, 1800 x 1200

    # outline layer is white and visible; epoxy layer visible
    assert s.library.layers[60].color.lower() == "#ffffff"
    assert s.library.layers[60].visible and s.library.layers[58].visible

    # the wizard does NOT build the pin cells -- they come from the library
    assert PIN not in s.library.cells and PIN1 not in s.library.cells


def test_create_chip_cell_rejects_bad_size_and_name_clash():
    s = Session.new("D")
    s.new_cell("TOP")
    with pytest.raises(LayoutError, match="positive"):
        s.create_chip_cell("X", 0, 100, 1, 10, 10, 2)
    with pytest.raises(LayoutError, match="epoxy"):
        s.create_chip_cell("X", 100, 100, 1, 0, 10, 2)
    s.create_chip_cell("CHIP", 100, 100, 1, 10, 10, 2)
    with pytest.raises(LayoutError, match="already exists"):
        s.create_chip_cell("CHIP", 200, 200, 1, 10, 10, 2)
    s.create_chip_cell("CHIP", 200, 200, 1, 10, 10, 2, overwrite=True)
    outline = next(sh for sh in s.library.cells["CHIP"].shapes if isinstance(sh, Path))
    assert max(p[0] for p in outline.points) == 100


def test_create_chip_cell_is_one_undo_step():
    s = Session.new("D")
    s.new_cell("TOP")
    s.clear_undo_history()
    s.create_chip_cell("CHIP", 100, 100, 1, 10, 10, 2)
    assert "CHIP" in s.library.cells
    assert s.undo() is True
    assert "CHIP" not in s.library.cells


def test_show_layers_of_cell_unhides_every_layer_it_draws_on():
    s = Session.new("D")
    s.new_cell(PIN1)
    s.rect(58, -10, -10, 10, 10)
    s.rect(60, -12, 10, -8, 14)
    s.set_layer_style(58, visible=False)
    s.set_layer_style(60, visible=False)
    s.show_layers_of_cell(PIN1)
    assert s.library.layers[58].visible and s.library.layers[60].visible


# --- Session.export_cell_to_library ---------------------------------------------

def test_export_carries_the_placed_pin_cells_and_keeps_their_labels(tmp_path):
    s, apath = _session_with_parts(tmp_path)
    s.create_chip_cell("CHIP", 1000, 1000, 60, 500, 500, 58)
    s.use_cell("CHIP")
    s.place_cell(PIN1, (-300, -300), label="1")
    s.place_cell(PIN, (300, -300), label="2")

    name, path = s.export_cell_to_library("CHIP", index=0)
    assert (name, path) == ("CHIP", apath)

    reloaded = load_gds(apath)
    assert {"CHIP", PIN, PIN1} <= set(reloaded.cells)
    refs = reloaded.cells["CHIP"].srefs
    assert [r.cell_name for r in refs] == [PIN1, PIN]
    assert [r.label for r in refs] == ["1", "2"]
    # the library still has exactly one chip_pin / chip_pin1 -- not copies
    assert "chip_pin_1" not in reloaded.cells and "chip_pin1_1" not in reloaded.cells


# --- the Tools > Chip Wizard GUI flow -----------------------------------------

tk = pytest.importorskip("tkinter")


def _app(tmp_path):
    from hlayout.gui.app import App
    apath = str(tmp_path / "parts.gds")
    _parts_library(apath)
    lib = Library("DESIGN")
    lib.new_cell("TOP")
    try:
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.session.attach_library(apath)
    app.canvas.set_cell("TOP")
    return app, apath


def _start(app, name, pins, pin1_cell=PIN1, pin_cell=PIN,
           save_to_lib=True, lib_name=None):
    app.session.create_chip_cell(name, 3000, 2000, 62, 1800, 1200, 58)
    app._chip_wizard = {"name": name, "total": pins, "placed": 0,
                        "prev_grid": int(app.canvas.grid_dbu),
                        "pin1_cell": pin1_cell, "pin_cell": pin_cell,
                        "save_to_lib": save_to_lib, "lib_name": lib_name or name}
    app.session.use_cell(name)
    app.canvas.set_cell(name)
    from hlayout.core.units import mm_to_dbu
    app.canvas.set_grid_spacing(mm_to_dbu(0.1))
    app.canvas.fit_view()
    app.canvas.start_chip_pins(pins)


def test_chip_wizard_no_attached_library_is_reported(tmp_path, monkeypatch):
    from hlayout.gui.app import App
    lib = Library("DESIGN")
    lib.new_cell("TOP")
    try:
        app = App(lib)
    except tk.TclError as ex:
        pytest.skip(f"no display available: {ex}")
    try:
        app.update()
        seen = {}
        monkeypatch.setattr("hlayout.gui.dialogs.show_info",
                            lambda title, msg, **k: seen.update(msg=msg))
        app.show_chip_wizard()
        assert "attach" in seen["msg"].lower()
    finally:
        app.destroy()


def test_chip_wizard_pin_cell_names_can_be_chosen_incl_underscore_variant(tmp_path):
    # The parts library names its pin-1 cell "chip_pin_1" (an underscore),
    # not "chip_pin1" -- the wizard lets you pick whichever cell you want
    # for pin 1 and for the rest.
    from hlayout.gui.app import App
    apath = str(tmp_path / "cw.gds")
    plib = Library("CW")
    pc = plib.new_cell("chip_pin")
    pc.add_rectangle(58, -900, -900, 900, 900, pin="1")
    p1 = plib.new_cell("chip_pin_1")
    p1.add_rectangle(58, -900, -900, 900, 900, pin="1")
    save_gds(plib, apath)

    lib = Library("DESIGN")
    lib.new_cell("TOP")
    try:
        app = App(lib)
    except tk.TclError as ex:
        pytest.skip(f"no display available: {ex}")
    try:
        app.update()
        app.session.attach_library(apath)
        app.canvas.set_cell("TOP")

        _start(app, "CHIPX", 3, pin1_cell="chip_pin_1", pin_cell="chip_pin")

        def _click(wx, wy):
            px, py = app.canvas.to_px(wx, wy)
            app.canvas._on_click(types.SimpleNamespace(x=int(px), y=int(py)))
        for wx, wy in [(-1000, -800), (1000, -800), (0, 800)]:
            _click(wx, wy)

        reloaded = load_gds(apath)
        refs = reloaded.cells["CHIPX"].srefs
        assert [r.cell_name for r in refs] == ["chip_pin_1", "chip_pin", "chip_pin"]
        assert [r.label for r in refs] == ["1", "2", "3"]
    finally:
        app.destroy()


def test_chip_wizard_places_pin1_from_chip_pin1_and_the_rest_from_chip_pin(tmp_path):
    app, apath = _app(tmp_path)
    try:
        _start(app, "CHIP4", 4)
        assert app.canvas.draw_mode == "chip_pins"

        def _click(wx, wy):
            px, py = app.canvas.to_px(wx, wy)
            app.canvas._on_click(types.SimpleNamespace(x=int(px), y=int(py)))

        for wx, wy in [(-1200, -800), (1200, -800), (1200, 800), (-1200, 800)]:
            _click(wx, wy)

        assert app.canvas.draw_mode == "select"
        assert app._chip_wizard is None

        reloaded = load_gds(apath)
        refs = reloaded.cells["CHIP4"].srefs
        assert len(refs) == 4
        assert refs[0].cell_name == PIN1 and refs[0].label == "1"
        assert [r.cell_name for r in refs[1:]] == [PIN, PIN, PIN]
        assert [r.label for r in refs] == ["1", "2", "3", "4"]
        # pin cells were reused from the library, not re-copied
        assert "chip_pin_1" not in reloaded.cells
        assert "saved to" in app.status.get()
        # the epoxy-pad layer (58) is now visible
        assert app.session.library.layers[58].visible is True
    finally:
        app.destroy()


def test_chip_wizard_skips_the_library_save_when_the_box_is_unticked(tmp_path):
    app, apath = _app(tmp_path)
    try:
        _start(app, "NOSAVE", 2, save_to_lib=False)

        def _click(wx, wy):
            px, py = app.canvas.to_px(wx, wy)
            app.canvas._on_click(types.SimpleNamespace(x=int(px), y=int(py)))
        for wx, wy in [(-1000, -800), (1000, -800)]:
            _click(wx, wy)

        assert "NOSAVE" in app.session.library.cells      # built in the design
        assert "NOSAVE" not in load_gds(apath).cells       # not exported
        assert "not saved to a library" in app.status.get()
    finally:
        app.destroy()


def test_chip_wizard_saves_under_the_given_library_name(tmp_path):
    app, apath = _app(tmp_path)
    try:
        _start(app, "WORKNAME", 2, lib_name="DIE_2P")

        def _click(wx, wy):
            px, py = app.canvas.to_px(wx, wy)
            app.canvas._on_click(types.SimpleNamespace(x=int(px), y=int(py)))
        for wx, wy in [(-1000, -800), (1000, -800)]:
            _click(wx, wy)

        cells = load_gds(apath).cells
        assert "DIE_2P" in cells and "WORKNAME" not in cells
        assert "DIE_2P" in app.session.library.cells      # design cell renamed too
    finally:
        app.destroy()


def test_chip_wizard_uses_a_0p1mm_grid_for_pins_then_restores_it(tmp_path):
    from hlayout.core.units import mm_to_dbu
    app, apath = _app(tmp_path)
    try:
        app.canvas.set_grid_spacing(mm_to_dbu(0.5))
        grid_before = int(app.canvas.grid_dbu)
        _start(app, "CHIP3", 3)
        assert int(app.canvas.grid_dbu) == 100
        assert int(app.canvas.snap_dbu) == 100

        def _click(wx, wy):
            px, py = app.canvas.to_px(wx, wy)
            app.canvas._on_click(types.SimpleNamespace(x=int(px), y=int(py)))

        for wx, wy in [(-1000, -800), (1000, -800), (0, 800)]:
            _click(wx, wy)

        assert int(app.canvas.grid_dbu) == grid_before
    finally:
        app.destroy()


def test_chip_wizard_esc_leaves_the_half_built_cell_and_exports_nothing(tmp_path):
    app, apath = _app(tmp_path)
    try:
        grid_before = int(app.canvas.grid_dbu)
        _start(app, "CHIP2", 4)

        px, py = app.canvas.to_px(-300, -300)
        app.canvas._on_click(types.SimpleNamespace(x=int(px), y=int(py)))
        app.canvas.cancel_draw()          # Esc

        assert app.canvas.draw_mode == "select"
        assert app._chip_wizard is None
        assert "cancelled" in app.status.get().lower()
        assert int(app.canvas.grid_dbu) == grid_before
        assert "CHIP2" in app.session.library.cells
        assert "CHIP2" not in load_gds(apath).cells
    finally:
        app.destroy()


def test_create_chip_cell_can_lay_a_conductor_die_attach_pad_200_larger():
    from hlayout.core.shapes import Rectangle
    s = Session.new("D")
    s.new_cell("TOP")
    s.create_chip_cell("CHIP", 2000, 1000, graphics_layer=61, epoxy_length=1900,
                       epoxy_width=900, epoxy_layer=20, attach_pad_layer=5)
    pads = [sh for sh in s.library.cells["CHIP"].shapes
            if isinstance(sh, Rectangle) and sh.layer == 5]
    assert len(pads) == 1 and pads[0].bbox() == (-1200, -700, 1200, 700)   # 200 all round
    s.create_chip_cell("CHIP2", 2000, 1000, graphics_layer=61, epoxy_length=1900,
                       epoxy_width=900, epoxy_layer=20)
    assert not any(sh.layer == 5 for sh in s.library.cells["CHIP2"].shapes)


def test_a_die_with_an_attach_pad_is_still_a_routing_keepout():
    from hlayout.io.techfile import save_bond_rules, load_bond_rules
    s = Session.new("D")
    s.new_cell("TOP")
    s.create_chip_cell("CHIP", 2000, 2000, graphics_layer=61, epoxy_length=1900,
                       epoxy_width=1900, epoxy_layer=20, attach_pad_layer=5)
    s.library.cells["CHIP"].add_rectangle(58, -900, 800, -800, 900, pin="1")   # a die pad
    br = load_bond_rules(s.library)
    br["die_pad_layers"] = [58]
    save_bond_rules(s.library, br)
    s.use_cell("TOP")
    s.rect(57, -50000, -50000, 50000, 50000)
    s.sref("CHIP", (0, 0), label="U1")
    ko = s._route_keepouts(s.library.cells["TOP"], (3, 5), {"die_keepout": 200, "clearance": 100})
    assert ko, "the die (attach pad and all) must be kept out"
    xs = [x for _l, pts in ko for x, _y in pts]
    assert min(xs) <= -1400 and max(xs) >= 1400      # 1200 pad + 200 keep-out
