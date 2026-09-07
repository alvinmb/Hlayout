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

def test_create_chip_cell_lays_a_centred_body_outline_only():
    s = Session.new("D")
    s.new_cell("TOP")
    name = s.create_chip_cell("CHIP", 2000, 1000, graphics_layer=60)
    assert name == "CHIP"
    assert s.current_cell_name == "CHIP"

    body = s.library.cells["CHIP"].shapes
    assert len(body) == 1
    outline = body[0]
    assert isinstance(outline, Path) and outline.layer == 60
    xs = [p[0] for p in outline.points]
    ys = [p[1] for p in outline.points]
    assert (min(xs), max(xs)) == (-1000, 1000)
    assert (min(ys), max(ys)) == (-500, 500)
    assert outline.points[0] == outline.points[-1]

    # the wizard does NOT build the pin cells -- they come from the library
    assert PIN not in s.library.cells and PIN1 not in s.library.cells


def test_create_chip_cell_makes_the_outline_layer_visible():
    s = Session.new("D")
    s.new_cell("TOP")
    s.set_layer_style(60, visible=False)
    s.create_chip_cell("CHIP", 1000, 1000, graphics_layer=60)
    assert s.library.layers[60].visible is True


def test_create_chip_cell_rejects_bad_size_and_name_clash():
    s = Session.new("D")
    s.new_cell("TOP")
    with pytest.raises(LayoutError, match="positive"):
        s.create_chip_cell("X", 0, 100, 1)
    s.create_chip_cell("CHIP", 100, 100, 1)
    with pytest.raises(LayoutError, match="already exists"):
        s.create_chip_cell("CHIP", 200, 200, 1)
    s.create_chip_cell("CHIP", 200, 200, 1, overwrite=True)
    assert max(p[0] for p in s.library.cells["CHIP"].shapes[0].points) == 100


def test_create_chip_cell_is_one_undo_step():
    s = Session.new("D")
    s.new_cell("TOP")
    s.clear_undo_history()
    s.create_chip_cell("CHIP", 100, 100, 1)
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
    s.create_chip_cell("CHIP", 1000, 1000, 60)
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


def _start(app, name, pins, pin1_cell=PIN1, pin_cell=PIN):
    app.session.create_chip_cell(name, 3000, 2000, 62)
    app._chip_wizard = {"name": name, "total": pins, "placed": 0,
                        "prev_grid": int(app.canvas.grid_dbu),
                        "pin1_cell": pin1_cell, "pin_cell": pin_cell}
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
