import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core.errors import LayoutError
from hlayout.core.library import Library
from hlayout.core.shapes import Path, Rectangle
from hlayout.io.gdsio import load_gds, save_gds
from hlayout.scripting import Session


def _session_with_attached_lib(tmp_path):
    att = Library("PARTS")
    att.new_cell("DUMMY")
    apath = str(tmp_path / "parts.gds")
    save_gds(att, apath)
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.attach_library(apath)
    return s, apath


# --- Session.create_chip_cell ------------------------------------------------

def test_create_chip_cell_lays_a_centred_body_outline():
    s = Session.new("D")
    s.new_cell("TOP")
    name = s.create_chip_cell("CHIP", 2000, 1000, 60)
    assert name == "CHIP"
    assert s.current_cell_name == "CHIP"
    body = s.library.cells["CHIP"].shapes
    assert len(body) == 1
    outline = body[0]
    assert isinstance(outline, Path) and outline.layer == 60
    # closed rectangle centred on the origin: -1000..1000 x -500..500
    xs = [p[0] for p in outline.points]
    ys = [p[1] for p in outline.points]
    assert (min(xs), max(xs)) == (-1000, 1000)
    assert (min(ys), max(ys)) == (-500, 500)
    assert outline.points[0] == outline.points[-1]


def test_create_chip_cell_rejects_bad_size_and_name_clash():
    s = Session.new("D")
    s.new_cell("TOP")
    with pytest.raises(LayoutError, match="positive"):
        s.create_chip_cell("X", 0, 100, 1)
    s.create_chip_cell("CHIP", 100, 100, 1)
    with pytest.raises(LayoutError, match="already exists"):
        s.create_chip_cell("CHIP", 200, 200, 1)
    # overwrite replaces it
    s.create_chip_cell("CHIP", 200, 200, 1, overwrite=True)
    outline = s.library.cells["CHIP"].shapes[0]
    assert max(p[0] for p in outline.points) == 100


def test_create_chip_cell_is_one_undo_step():
    s = Session.new("D")
    s.new_cell("TOP")
    s.clear_undo_history()
    s.create_chip_cell("CHIP", 100, 100, 1)
    assert "CHIP" in s.library.cells
    assert s.undo() is True
    assert "CHIP" not in s.library.cells


# --- Session.export_cell_to_library ---------------------------------------------

def test_export_cell_to_library_copies_and_saves(tmp_path):
    s, apath = _session_with_attached_lib(tmp_path)
    s.create_chip_cell("CHIP", 1000, 1000, 60)
    s.use_cell("CHIP")
    s.rect(58, -100, -100, 100, 100, pin="1")

    name, path = s.export_cell_to_library("CHIP", index=0)
    assert name == "CHIP"
    assert path == apath

    reloaded = load_gds(apath)
    assert "CHIP" in reloaded.cells
    assert len(reloaded.cells["CHIP"].shapes) == 2       # outline + one pad


def test_export_cell_to_library_needs_overwrite_for_a_name_clash(tmp_path):
    s, apath = _session_with_attached_lib(tmp_path)
    s.create_chip_cell("CHIP", 1000, 1000, 60)
    s.export_cell_to_library("CHIP", index=0)
    with pytest.raises(LayoutError, match="already exists"):
        s.export_cell_to_library("CHIP", index=0)
    s.export_cell_to_library("CHIP", index=0, overwrite=True)


def test_export_cell_to_library_errors_without_an_attached_lib():
    s = Session.new("D")
    s.new_cell("TOP")
    s.create_chip_cell("CHIP", 100, 100, 1)
    with pytest.raises(LayoutError, match="no attached library"):
        s.export_cell_to_library("CHIP", index=0)


# --- the Tools > Chip Wizard GUI flow -----------------------------------------

tk = pytest.importorskip("tkinter")


def _app(tmp_path):
    from hlayout.gui.app import App
    att = Library("PARTS")
    att.new_cell("DUMMY")
    apath = str(tmp_path / "parts.gds")
    save_gds(att, apath)
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


def test_chip_wizard_places_pins_by_click_then_saves_to_the_first_attached_lib(tmp_path):
    app, apath = _app(tmp_path)
    try:
        # stand in for the dialog: build the body + arm the pin tool
        app.session.create_chip_cell("CHIP4", 3000, 2000, 62)
        app._chip_wizard = {"name": "CHIP4", "epoxy_size": 150, "epoxy_layer": 58,
                            "total": 4, "placed": 0}
        app.session.use_cell("CHIP4")
        app.canvas.set_cell("CHIP4")
        app.canvas.fit_view()
        app.canvas.start_chip_pins(4)
        assert app.canvas.draw_mode == "chip_pins"

        def _click(wx, wy):
            px, py = app.canvas.to_px(wx, wy)
            app.canvas._on_click(types.SimpleNamespace(x=int(px), y=int(py)))

        for wx, wy in [(-1200, -800), (1200, -800), (1200, 800), (-1200, 800)]:
            _click(wx, wy)

        # tool auto-exited after the 4th pin
        assert app.canvas.draw_mode == "select"
        assert app._chip_wizard is None

        reloaded = load_gds(apath)
        assert "CHIP4" in reloaded.cells
        pads = [s for s in reloaded.cells["CHIP4"].shapes if isinstance(s, Rectangle)]
        assert sorted(p.pin for p in pads) == ["1", "2", "3", "4"]
        assert "saved to" in app.status.get()
    finally:
        app.destroy()


def test_chip_wizard_esc_leaves_the_half_built_cell_and_exports_nothing(tmp_path):
    app, apath = _app(tmp_path)
    try:
        app.session.create_chip_cell("CHIP2", 1000, 1000, 62)
        app._chip_wizard = {"name": "CHIP2", "epoxy_size": 100, "epoxy_layer": 58,
                            "total": 4, "placed": 0}
        app.session.use_cell("CHIP2")
        app.canvas.set_cell("CHIP2")
        app.canvas.fit_view()
        app.canvas.start_chip_pins(4)

        px, py = app.canvas.to_px(-300, -300)
        app.canvas._on_click(types.SimpleNamespace(x=int(px), y=int(py)))
        app.canvas.cancel_draw()          # Esc

        assert app.canvas.draw_mode == "select"
        assert app._chip_wizard is None
        assert "cancelled" in app.status.get().lower()
        # the cell is still in the design, but nothing was exported
        assert "CHIP2" in app.session.library.cells
        assert "CHIP2" not in load_gds(apath).cells
    finally:
        app.destroy()
