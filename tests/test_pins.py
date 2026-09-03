import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core.errors import LayoutError
from hlayout.core.library import Library
from hlayout.io.gdsio import load_gds, save_gds
from hlayout.scripting import Session

tk = pytest.importorskip("tkinter")


def _session_with_attached_library(tmp_path):
    design = Library("DESIGN")
    design.new_cell("TOP")
    s = Session(design)
    s.use_cell("TOP")

    parts = Library("PARTS")
    parts.new_cell("DUMMY")
    parts_path = str(tmp_path / "parts.gds")
    save_gds(parts, parts_path)
    s.attach_library(parts_path)
    return s


# --- Phase A: pin field + GDS round-trip -----------------------------------

def test_rectangle_and_polygon_pin_default_to_empty():
    lib = Library("L")
    cell = lib.new_cell("TOP")
    rect = cell.add_rectangle(0, 0, 0, 10, 10)
    poly = cell.add_polygon(1, [(0, 0), (10, 0), (5, 10)])
    assert rect.pin == ""
    assert poly.pin == ""


def test_pin_round_trips_through_gds(tmp_path):
    lib = Library("L")
    cell = lib.new_cell("TOP")
    cell.add_rectangle(0, 0, 0, 10, 10, pin="1")
    cell.add_polygon(1, [(0, 0), (10, 0), (5, 10)], pin="K")
    path = str(tmp_path / "pins.gds")
    save_gds(lib, path)

    lib2 = load_gds(path)
    shapes = lib2.cells["TOP"].shapes
    rect2 = next(s for s in shapes if s.layer == 0)
    poly2 = next(s for s in shapes if s.layer == 1)
    assert rect2.pin == "1"
    assert poly2.pin == "K"


def test_unpinned_shape_round_trips_as_empty_pin(tmp_path):
    lib = Library("L")
    cell = lib.new_cell("TOP")
    cell.add_rectangle(0, 0, 0, 10, 10)
    path = str(tmp_path / "no_pin.gds")
    save_gds(lib, path)
    lib2 = load_gds(path)
    assert lib2.cells["TOP"].shapes[0].pin == ""


# --- Phase B: automatic 2-pad numbering ------------------------------------

def test_create_pad_cell_auto_numbers_a_genuine_2_pad_footprint(tmp_path):
    s = _session_with_attached_library(tmp_path)
    name, _, count = s.create_pad_cell(
        "PAD2", 0, 1, length=1000, width=500, margin=50, cols=2, pitch_x=2000, index=0,
    )
    assert count == 2
    cell = s.attached_libraries[0].cells[name]
    conductor_pads = [sh for sh in cell.shapes if sh.layer == 0]
    by_pin = {p.pin: p for p in conductor_pads}
    assert set(by_pin) == {"1", "2"}
    # pin "1" is the lower-X pad
    assert by_pin["1"].points[0][0] < by_pin["2"].points[0][0]


def test_create_pad_cell_leaves_other_grid_sizes_unnumbered(tmp_path):
    s = _session_with_attached_library(tmp_path)
    name, _, count = s.create_pad_cell(
        "PAD3", 0, 1, length=1000, width=500, margin=50, cols=3, pitch_x=2000, index=0,
    )
    assert count == 3
    cell = s.attached_libraries[0].cells[name]
    conductor_pads = [sh for sh in cell.shapes if sh.layer == 0]
    assert all(p.pin == "" for p in conductor_pads)

    name1, _, count1 = s.create_pad_cell(
        "PAD1", 0, 1, length=1000, width=500, margin=50, index=0, overwrite=True,
    )
    assert count1 == 1
    cell1 = s.attached_libraries[0].cells[name1]
    assert [sh.pin for sh in cell1.shapes if sh.layer == 0] == [""]


def test_generate_resistor_auto_numbers_pads_and_sets_instance_label():
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    device_name = s.generate_resistor(0, 0, body_width=1000, body_length=2000, ref_des="R1")
    device_cell = s.library.cells[device_name]
    pinned = {sh.pin: sh for sh in device_cell.shapes if getattr(sh, "pin", "")}
    assert set(pinned) == {"1", "2"}
    assert pinned["1"].points[0][0] < pinned["2"].points[0][0]

    placed = s.cell.srefs[-1]
    assert placed.cell_name == device_name
    assert placed.label == "R1"


def test_generate_resistor_without_ref_des_leaves_label_empty():
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.generate_resistor(0, 0, body_width=1000, body_length=2000)
    placed = s.cell.srefs[-1]
    assert placed.label == ""


def test_place_cell_and_sref_accept_a_label_kwarg(tmp_path):
    s = _session_with_attached_library(tmp_path)
    ref = s.sref("DUMMY", (0, 0), label="U1")
    assert ref.label == "U1"

    ref2 = s.place_cell("DUMMY", (5000, 0), label="U2")
    assert ref2.label == "U2"


# --- Phase C: Session.list_pins()/set_pin() + PIN command -------------------

def test_list_pins_returns_only_rectangle_and_polygon_shapes():
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.rect(0, 0, 0, 1000, 1000, pin="1")
    s.polygon(1, [(0, 0), (10, 0), (5, 10)])
    s.path(2, [(0, 0), (100, 100)], width=50)
    s.text(3, (0, 0), "hi")

    pins = s.list_pins()
    assert len(pins) == 2
    assert pins[0]["pin"] == "1"
    assert pins[1]["pin"] == ""


def test_set_pin_updates_the_shape_and_is_undoable():
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.rect(0, 0, 0, 1000, 1000)
    s.clear_undo_history()

    shape = s.set_pin(0, "7")
    assert shape.pin == "7"
    assert s.list_pins()[0]["pin"] == "7"

    assert s.undo() is True
    assert s.list_pins()[0]["pin"] == ""


def test_set_pin_rejects_out_of_range_index_and_non_pad_shapes():
    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.text(0, (0, 0), "hi")
    with pytest.raises(LayoutError):
        s.set_pin(5, "1")
    with pytest.raises(LayoutError):
        s.set_pin(0, "1")  # index 0 is the Text shape, not pin-capable


def test_pin_command_bar_list_and_set():
    from hlayout.gui.commands import CommandInterpreter

    s = Session.new("DESIGN")
    s.new_cell("TOP")
    s.rect(0, 0, 0, 1000, 1000, pin="1")
    s.rect(0, 2000, 0, 3000, 1000)
    interp = CommandInterpreter(s, console=None)

    out = interp.run("PIN LIST")
    assert "pin=1" in out
    assert "pin=(unset)" in out

    out2 = interp.run("PIN SET 1 2")
    assert "pin='2'" in out2
    assert s.list_pins()[1]["pin"] == "2"


# --- Phase C: click-to-increment canvas tool --------------------------------

def _make_app():
    from hlayout.gui.app import App

    lib = Library("EDIT")
    top = lib.new_cell("TOP")
    try:
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.canvas.fit_view()
    app.update()
    app.snap_var.set(False)
    app._on_snap_change()
    return app, top


def _ev(x, y):
    return types.SimpleNamespace(x=x, y=y)


def test_pin_numbering_tool_stamps_increasing_pins_on_successive_clicks():
    app, top = _make_app()
    try:
        r1 = top.add_rectangle(0, 0, 0, 1000, 1000)
        r2 = top.add_rectangle(0, 2000, 0, 3000, 1000)
        app.canvas.redraw()

        app.canvas.start_pin_numbering("1")
        assert app.canvas.draw_mode == "pin_number"

        for shape, expected in [(r1, "1"), (r2, "2")]:
            cx = (shape.points[0][0] + shape.points[2][0]) // 2
            cy = (shape.points[0][1] + shape.points[2][1]) // 2
            app.canvas._on_click(_ev(*app.canvas.to_px(cx, cy)))
            assert shape.pin == expected
    finally:
        app.destroy()


def _pin_label_items(canvas, text):
    return [i for i in canvas.find_all()
            if canvas.type(i) == "text" and canvas.itemcget(i, "text") == text]


def test_numbered_pad_shows_its_pin_number_on_the_canvas():
    app, top = _make_app()
    try:
        r = top.add_rectangle(0, 0, 0, 4000, 4000, pin="3")
        top.add_rectangle(0, 6000, 0, 8000, 4000)          # unnumbered -> no label
        app.canvas.fit_view()
        app.canvas.redraw()
        assert len(_pin_label_items(app.canvas, "3")) == 1
        # clicking the number picks the pad it belongs to
        item = _pin_label_items(app.canvas, "3")[0]
        assert app.canvas._item_to_shape[item] == ("shape", r)

        app.show_pins_var.set(False)
        app._on_show_pins_change()
        assert _pin_label_items(app.canvas, "3") == []
    finally:
        app.destroy()


def test_pin_number_appears_as_soon_as_the_tool_stamps_it():
    app, top = _make_app()
    try:
        r = top.add_rectangle(0, 0, 0, 4000, 4000)
        app.canvas.fit_view()
        app.canvas.redraw()
        assert _pin_label_items(app.canvas, "1") == []      # nothing yet

        app.canvas.start_pin_numbering("1")
        cx = (r.points[0][0] + r.points[2][0]) // 2
        cy = (r.points[0][1] + r.points[2][1]) // 2
        app.canvas._on_click(_ev(*app.canvas.to_px(cx, cy)))
        app.update()
        assert r.pin == "1"
        assert len(_pin_label_items(app.canvas, "1")) == 1   # visible right away
    finally:
        app.destroy()


def test_pin_numbering_tool_escape_returns_to_select():
    app, top = _make_app()
    try:
        app.canvas.start_pin_numbering("1")
        assert app.canvas.draw_mode == "pin_number"
        app.canvas.cancel_draw()
        assert app.canvas.draw_mode == "select"
    finally:
        app.destroy()


def test_pin_numbering_tool_reclicking_a_numbered_pad_asks_for_confirmation(monkeypatch):
    from hlayout.gui import canvas as canvas_module

    app, top = _make_app()
    try:
        r1 = top.add_rectangle(0, 0, 0, 1000, 1000, pin="5")
        app.canvas.redraw()
        app.canvas.start_pin_numbering("1")

        asked = {}

        def fake_askyesno(title, message):
            asked["called"] = True
            return False  # decline the overwrite

        monkeypatch.setattr(canvas_module.messagebox, "askyesno", fake_askyesno)

        cx, cy = 500, 500
        app.canvas._on_click(_ev(*app.canvas.to_px(cx, cy)))
        assert asked.get("called") is True
        assert r1.pin == "5"  # unchanged since the confirmation was declined
        assert app.canvas.pin_number_next == "1"  # not consumed either
    finally:
        app.destroy()


# --- Properties panel / PROPS command surface pin and label -----------------

def test_describe_selection_reports_pin_on_a_selected_pad():
    from hlayout.gui.canvas import describe_selection

    lib = Library("L")
    cell = lib.new_cell("TOP")
    rect = cell.add_rectangle(0, 0, 0, 1000, 1000, pin="2")

    fake_canvas = types.SimpleNamespace(
        library=lib, selected_multi=[], selected=("shape", rect),
    )
    text = describe_selection(fake_canvas)
    assert "pin='2'" in text


def test_describe_selection_omits_pin_when_unset():
    from hlayout.gui.canvas import describe_selection

    lib = Library("L")
    cell = lib.new_cell("TOP")
    rect = cell.add_rectangle(0, 0, 0, 1000, 1000)

    fake_canvas = types.SimpleNamespace(
        library=lib, selected_multi=[], selected=("shape", rect),
    )
    text = describe_selection(fake_canvas)
    assert "pin=" not in text


def test_describe_selection_reports_label_on_a_selected_cell_instance():
    from hlayout.gui.canvas import describe_selection

    lib = Library("L")
    lib.new_cell("PART")
    top = lib.new_cell("TOP")
    ref = top.add_sref("PART", (0, 0), label="R1")

    fake_canvas = types.SimpleNamespace(
        library=lib, selected_multi=[], selected=("sref", ref),
    )
    text = describe_selection(fake_canvas)
    assert "label='R1'" in text


def test_props_command_reports_pin_and_label():
    from hlayout.gui.commands import CommandInterpreter

    s = Session.new("L")
    s.new_cell("PART")
    top = s.new_cell("TOP")
    ref = top.add_sref("PART", (0, 0), label="R1")

    class _FakeView:
        def __init__(self, library, selected):
            self.library = library
            self.selected_multi = []
            self.selected = selected

    interp = CommandInterpreter(s, console=None)
    interp.view = _FakeView(s.library, ("sref", ref))
    out = interp.run("PROPS")
    assert "label='R1'" in out
