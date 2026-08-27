import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")

from hlayout.core.library import Library
from hlayout.io.gdsio import load_gds, save_gds


def _make_app_with_two_instances(tmp_path=None):
    from hlayout.gui.app import App

    lib = Library("LBL")
    part = lib.new_cell("PART")
    part.add_rectangle(0, 0, 0, 100, 100)
    top = lib.new_cell("TOP")
    a = top.add_sref("PART", (0, 0))
    b = top.add_sref("PART", (5000, 0))
    try:
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app._refresh_cell_list()
    app.cell_var.set("TOP")
    app.session.use_cell("TOP")
    app.canvas.set_cell("TOP")
    app.canvas.fit_view()
    app.update()
    return app, top, a, b


def test_sref_label_defaults_empty():
    lib = Library("X")
    part = lib.new_cell("PART")
    top = lib.new_cell("TOP")
    ref = top.add_sref("PART", (0, 0))
    assert ref.label == ""


def test_two_instances_of_same_cell_can_have_different_labels():
    lib = Library("X")
    lib.new_cell("PART")
    top = lib.new_cell("TOP")
    a = top.add_sref("PART", (0, 0))
    b = top.add_sref("PART", (1000, 0))
    a.label = "C1"
    b.label = "C2"
    assert a.label == "C1"
    assert b.label == "C2"
    # editing one instance's label never touches the referenced cell's
    # own shapes or the other instance
    assert lib.cells["PART"].shapes == []


def test_label_round_trips_through_gds(tmp_path):
    lib = Library("X")
    part = lib.new_cell("PART")
    part.add_rectangle(0, 0, 0, 100, 100)
    top = lib.new_cell("TOP")
    a = top.add_sref("PART", (0, 0))
    a.label = "C1"
    b = top.add_sref("PART", (2000, 0))  # no label
    lib.top_cell_name = "TOP"

    path = str(tmp_path / "lbl.gds")
    save_gds(lib, path)
    reloaded = load_gds(path)

    refs = sorted(reloaded.cells["TOP"].srefs, key=lambda r: r.origin[0])
    assert refs[0].label == "C1"
    assert refs[1].label == ""


def test_edit_dialog_saves_label_and_it_survives_a_move():
    app, top, a, b = _make_app_with_two_instances()
    try:
        app.canvas.selected = ("sref", a)
        a.label = "C1"
        b.label = "C2"

        app.canvas.move_selected(1000, 500)
        assert a.origin == (1000, 500)
        assert a.label == "C1"  # unaffected by the move
        assert b.origin == (5000, 0)  # the other instance untouched
        assert b.label == "C2"
    finally:
        app.destroy()


def test_labeled_sref_draws_a_text_item_that_tracks_the_instance():
    app, top, a, b = _make_app_with_two_instances()
    try:
        a.label = "C1"
        app.canvas.redraw()
        app.update()
        items_before = [i for i in app.canvas.find_all()
                         if app.canvas.type(i) == "text" and app.canvas.itemcget(i, "text") == "C1"]
        assert len(items_before) == 1
        pos_before = app.canvas.coords(items_before[0])

        app.canvas.selected = ("sref", a)
        app.canvas.move_selected(2000, 2000)
        app.canvas.redraw()
        app.update()
        items_after = [i for i in app.canvas.find_all()
                       if app.canvas.type(i) == "text" and app.canvas.itemcget(i, "text") == "C1"]
        assert len(items_after) == 1
        pos_after = app.canvas.coords(items_after[0])
        assert pos_after != pos_before
    finally:
        app.destroy()
