import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")

from hlayout.core.library import Library
from hlayout.io.gdsio import load_gds, save_gds
from hlayout.io.techfile import save_refdes_annotation_layer


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
    assert ref.label_height == 14


def test_sref_label_height_is_editable_and_validated():
    from hlayout.core.errors import GeometryError
    from hlayout.core.shapes import SRef

    lib = Library("X")
    lib.new_cell("PART")
    top = lib.new_cell("TOP")
    ref = top.add_sref("PART", (0, 0))
    ref.label = "C1"
    ref.label_height = 24
    assert ref.label_height == 24
    with pytest.raises(GeometryError):
        SRef(cell_name="PART", origin=(0, 0), label="C2", label_height=0)


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
    a.label_height = 22
    a.label_offset = (250, -400)
    b = top.add_sref("PART", (2000, 0))  # no label
    lib.top_cell_name = "TOP"

    path = str(tmp_path / "lbl.gds")
    save_gds(lib, path)
    reloaded = load_gds(path)

    refs = sorted(reloaded.cells["TOP"].srefs, key=lambda r: r.origin[0])
    assert refs[0].label == "C1"
    assert refs[0].label_height == 22
    assert refs[0].label_offset == (250, -400)
    assert refs[1].label == ""
    assert refs[1].label_height == 14  # default, unset on this instance
    assert refs[1].label_offset == (0, 0)


def test_label_offset_defaults_zero_and_rounds():
    from hlayout.core.shapes import SRef
    r = SRef(cell_name="P", origin=(0, 0))
    assert r.label_offset == (0, 0)
    r2 = SRef(cell_name="P", origin=(0, 0), label="U1", label_offset=(12.6, -3.2))
    assert r2.label_offset == (13, -3)


def test_float_label_anchor_is_the_instance_centre_plus_offset():
    app, top, a, b = _make_app_with_two_instances()
    try:
        a.label = "U1"
        # PART is a 100x100 rect at its cell origin, so the instance's
        # bbox centre is origin + (50, 50).
        ax, ay = app.canvas._sref_label_anchor(a)
        assert (ax, ay) == (a.origin[0] + 50, a.origin[1] + 50)
        a.label_offset = (300, -200)
        ax, ay = app.canvas._sref_label_anchor(a)
        assert (ax, ay) == (a.origin[0] + 50 + 300, a.origin[1] + 50 - 200)
    finally:
        app.destroy()


def test_dragging_the_float_label_moves_only_the_label():
    import types as _t
    app, top, a, b = _make_app_with_two_instances()
    try:
        a.label = "U1"
        app.canvas.snap_enabled = False
        app.canvas.redraw()
        app.update()
        item = next(i for i, (k, o) in app.canvas._item_to_shape.items()
                    if k == "sref_label" and o is a)
        bb = app.canvas.bbox(item)
        cx, cy = int((bb[0] + bb[2]) // 2), int((bb[1] + bb[3]) // 2)
        app.canvas._select_click(_t.SimpleNamespace(x=cx, y=cy))
        assert app.canvas.selected[0] == "sref_label"
        sx, sy = app.canvas._drag_start_mouse
        mx, my = app.canvas.to_px(sx + 4000, sy - 3000)
        app.canvas._on_drag_motion(_t.SimpleNamespace(x=int(mx), y=int(my)))
        assert a.label_offset != (0, 0)
        assert a.origin == (0, 0)          # the instance itself did not move
    finally:
        app.destroy()


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


def test_edit_dialog_has_label_and_label_size_fields():
    """Drives the real Edit Selected... dialog (not just setting
    ref.label directly) to lock in the field order and confirm both
    the label text and its size are saved from the dialog."""
    app, top, a, b = _make_app_with_two_instances()
    try:
        app.canvas.selected = ("sref", a)
        app.canvas.selected_multi = []

        def find_toplevel(widget):
            for w in widget.children.values():
                if isinstance(w, tk.Toplevel):
                    return w
                found = find_toplevel(w)
                if found is not None:
                    return found
            return None

        def interact():
            win = find_toplevel(app)
            assert win is not None
            entries = []

            def walk(widget):
                for child in widget.winfo_children():
                    if isinstance(child, tk.Entry):
                        entries.append(child)
                    walk(child)

            walk(win)
            # add_field() order for kind == "sref": Label, Label Size (px),
            # Label Offset X, Label Offset Y, X, Y, Rotation, Magnification,
            # Mirror
            assert len(entries) == 9
            entries[0].delete(0, "end")
            entries[0].insert(0, "IC1")
            entries[1].delete(0, "end")
            entries[1].insert(0, "20")

            buttons = []

            def walk_btn(widget):
                for child in widget.winfo_children():
                    if isinstance(child, tk.Button):
                        buttons.append(child)
                    walk_btn(child)

            walk_btn(win)
            [b for b in buttons if b.cget("text") == "OK"][0].invoke()

        app.after(200, interact)
        assert app.canvas.edit_selected() is True
        assert a.label == "IC1"
        assert a.label_height == 20
    finally:
        app.destroy()


def _app_with_instance_of(part_shapes, label):
    """App showing TOP, which holds one SRef of PART (built from
    `part_shapes`, a list of (kind, *args)) carrying `label`."""
    from hlayout.gui.app import App

    lib = Library("LBL")
    part = lib.new_cell("PART")
    for kind, *args in part_shapes:
        if kind == "rect":
            part.add_rectangle(*args)
        elif kind == "text":
            layer, origin, text = args
            part.add_text(layer, origin, text)
    top = lib.new_cell("TOP")
    ref = top.add_sref("PART", (0, 0), label=label)
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
    app.canvas.redraw()
    app.update()
    return app, ref


def _floating_label_items(canvas, text):
    return [i for i in canvas.find_all()
            if canvas.type(i) == "text" and canvas.itemcget(i, "text") == text]


def test_floating_ref_des_is_suppressed_when_cell_bakes_it_as_text():
    # PART carries "R1" as a real Text shape (its assigned ref-des
    # layer) -- the canvas must NOT also float "R1" over the instance.
    app, ref = _app_with_instance_of(
        [("rect", 0, 0, 0, 100, 100), ("text", 13, (0, 0), "R1")], "R1")
    try:
        # the baked Text on layer 13 is drawn once; no *second* "R1".
        assert len(_floating_label_items(app.canvas, "R1")) == 1
    finally:
        app.destroy()


def test_floating_ref_des_still_shows_when_cell_only_bakes_a_prefix():
    # PART bakes just the prefix "C" (a placeholder, != "C1"): the
    # per-instance floating "C1" annotation still appears.
    app, ref = _app_with_instance_of(
        [("rect", 0, 0, 0, 100, 100), ("text", 13, (0, 0), "C")], "C1")
    try:
        assert len(_floating_label_items(app.canvas, "C1")) == 1
        assert len(_floating_label_items(app.canvas, "C")) == 1
    finally:
        app.destroy()


def test_floating_ref_des_is_dropped_when_every_layer_is_off_even_unconfigured():
    # No annotation layer configured (default). The float still shows
    # normally, but "All Off" must blank it too, not leave it hanging.
    app, ref = _app_with_instance_of([("rect", 0, 0, 0, 100, 100)], "U1")
    try:
        assert len(_floating_label_items(app.canvas, "U1")) == 1
        app.layer_panel.set_all_visible(False)
        app.canvas.redraw(); app.update()
        assert _floating_label_items(app.canvas, "U1") == []
        app.layer_panel.set_all_visible(True)
        app.canvas.redraw(); app.update()
        assert len(_floating_label_items(app.canvas, "U1")) == 1
    finally:
        app.destroy()


def test_floating_ref_des_follows_the_configured_annotation_layer_visibility():
    from hlayout.gui.app import App

    lib = Library("LBL")
    part = lib.new_cell("PART")
    part.add_rectangle(0, 0, 0, 100, 100)
    top = lib.new_cell("TOP")
    top.add_sref("PART", (0, 0), label="U1")
    save_refdes_annotation_layer(lib, 5)      # tie the annotation to layer 5
    try:
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    try:
        app.update()
        app.cell_var.set("TOP")
        app.session.use_cell("TOP")
        app.canvas.set_cell("TOP")

        lib.layers[5].visible = True
        app.canvas.redraw(); app.update()
        assert len(_floating_label_items(app.canvas, "U1")) == 1

        lib.layers[5].visible = False        # "all layers off" case
        app.canvas.redraw(); app.update()
        assert _floating_label_items(app.canvas, "U1") == []

        lib.layers[5].visible = True
        app.canvas.redraw(); app.update()
        assert len(_floating_label_items(app.canvas, "U1")) == 1
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


def test_clicking_the_instances_body_selects_and_drags_the_whole_sref():
    # Previously only the tiny origin handle was clickable for a placed
    # cell instance -- clicking anywhere on its actual drawn geometry
    # (e.g. a resistor's ink body, not just an 8x8px handle) did
    # nothing. Every item _draw_sref()/_draw_nested_sref() draws for an
    # instance is now registered as a ("sref", ref) click target too.
    import types
    app, top, a, b = _make_app_with_two_instances()
    try:
        app.canvas.snap_enabled = False
        app.canvas.redraw()
        app.update()
        # find a canvas item belonging to instance `a`'s own drawn body
        # (not the small origin handle, which is also "sref"/a but tiny)
        body_items = [i for i, (k, o) in app.canvas._item_to_shape.items()
                      if k == "sref" and o is a and app.canvas.type(i) != "rectangle"]
        assert body_items, "instance body has no registered click target"
        item = body_items[0]
        bb = app.canvas.bbox(item)
        # click a few px inside a corner of the body -- nowhere near the
        # instance's origin handle (drawn at the instance origin) or,
        # since `a` carries no label here, no ref-des annotation either.
        px, py = bb[0] + 4, bb[1] + 4
        app.canvas._select_click(types.SimpleNamespace(x=px, y=py))
        assert app.canvas.selected == ("sref", a)

        before = a.origin
        sx, sy = app.canvas._drag_start_mouse
        mx, my = app.canvas.to_px(sx + 500, sy + 300)
        app.canvas._on_drag_motion(types.SimpleNamespace(x=int(mx), y=int(my)))
        assert a.origin != before
        assert b.origin == (5000, 0)   # the other instance is untouched
    finally:
        app.destroy()


def test_clicking_a_placed_parts_empty_margin_still_selects_it():
    # A part whose footprint is mostly unfilled outline/silkscreen with
    # only small pads at each end (an SMD capacitor -- unlike a
    # resistor, whose ink fills nearly the whole footprint) used to have
    # no click target at all in the empty middle of its own outline.
    # Every instance now gets an invisible full-bbox hit rectangle,
    # pushed to the bottom of the stack so a real shape -- from this
    # instance or any other, however the bounding boxes overlap -- still
    # wins the pick wherever one actually exists.
    import types
    from hlayout.gui.app import App

    lib = Library("CAPTEST")
    cap = lib.new_cell("CAP")
    cap.add_rectangle(20, 0, -200, 600, 200, pin="1")       # pad1: 0..600
    cap.add_rectangle(20, 1700, -200, 2100, 200, pin="2")   # pad2: 1700..2100
    cap.add_path(61, [(-500, -300), (2500, -300), (2500, 300), (-500, 300), (-500, -300)],
                 width=50)
    top = lib.new_cell("TOP")
    # A alone: its own empty margin (between/around the pads) is the
    # "no fill here" case. B sits close enough that its bounding box
    # (and hit rectangle) overlaps A's real pad2, without B's own real
    # pads reaching that far -- B's pad1 is 500..1100, pad2 is 2200..2600.
    a = top.add_sref("CAP", (0, 0), label="C1")
    b = top.add_sref("CAP", (500, 0), label="C2")
    try:
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    try:
        app.update()
        app._refresh_cell_list()
        app.cell_var.set("TOP")
        app.session.use_cell("TOP")
        app.canvas.set_cell("TOP")
        app.canvas.fit_view()
        app.canvas.snap_enabled = False
        app.canvas.redraw()
        app.update()

        # Click in A's outline margin to the left of its pads (world
        # x=-200, inside A's -500..2500 outline but left of pad1's 0..600
        # -- and outside B's 0..3000 bounding box entirely, so this can
        # only be A's hit rectangle). No fill there before this fix.
        px, py = app.canvas.to_px(-200, 0)
        app.canvas._select_click(types.SimpleNamespace(x=int(px), y=int(py)))
        assert app.canvas.selected == ("sref", a)

        # A's real pad2 (world 1700..2100) still beats B's overlapping
        # -but-lowered hit-rect there (B's own real pads don't reach it).
        px2, py2 = app.canvas.to_px(1900, 0)
        app.canvas._select_click(types.SimpleNamespace(x=int(px2), y=int(py2)))
        assert app.canvas.selected == ("sref", a)
    finally:
        app.destroy()


def test_a_background_instance_placed_between_two_others_cannot_bury_them():
    # Regression for a real layout: a large board-outline/keep-out
    # instance (e.g. "Substrate") sitting in cell.srefs BETWEEN two
    # component instances -- and sharing their conductor layer -- used
    # to visually and click-wise bury only the ones placed BEFORE it in
    # that list (the ones placed after stayed selectable), since drawing
    # walked cell.srefs in raw list order per instance instead of
    # pooling every instance's geometry into one globally layer-sorted
    # pass. Even after pooling by layer, a big same-layer background
    # shape still needs to lose the *same-layer* tie to the small real
    # features sharing that layer -- otherwise it keeps burying every
    # component regardless of list position, just via the tie-break
    # instead of raw order. _draw_cell breaks same-layer ties by area
    # (smaller drawn last, i.e. on top) specifically so this can't
    # happen no matter where the background instance sits in the list.
    import types
    from hlayout.gui.app import App

    lib = Library("BGTEST")
    pad = lib.new_cell("PAD")
    pad.add_rectangle(5, 0, 0, 200, 200)          # small, on layer 5
    board = lib.new_cell("BOARD")
    board.add_rectangle(5, 0, 0, 20000, 20000)    # huge, SAME layer 5
    top = lib.new_cell("TOP")
    a = top.add_sref("PAD", (1000, 1000))
    b = top.add_sref("PAD", (5000, 1000))
    bg = top.add_sref("BOARD", (0, 0))            # placed between b and c
    c = top.add_sref("PAD", (9000, 1000))
    assert list(top.srefs) == [a, b, bg, c]

    try:
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    try:
        app.update()
        app._refresh_cell_list()
        app.cell_var.set("TOP")
        app.session.use_cell("TOP")
        app.canvas.set_cell("TOP")
        app.canvas.fit_view()
        app.canvas.snap_enabled = False
        app.canvas.redraw()
        app.update()

        for name, ref, (ox, oy) in (("C1", a, (1000, 1000)), ("C2", b, (5000, 1000)),
                                     ("C3", c, (9000, 1000))):
            px, py = app.canvas.to_px(ox + 100, oy + 100)   # centre of the 200x200 pad
            app.canvas._select_click(types.SimpleNamespace(x=int(px), y=int(py)))
            assert app.canvas.selected == ("sref", ref), (
                f"clicking {name}'s real pad selected {app.canvas.selected!r} instead"
            )
    finally:
        app.destroy()
