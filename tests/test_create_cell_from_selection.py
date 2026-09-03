import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")

import types

from hlayout.core.errors import CellError
from hlayout.core.shapes import Polygon, Rectangle, SRef


def _ev(x, y):
    return types.SimpleNamespace(x=x, y=y)


def _make_app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    try:
        lib = Library("MAKECELL")
        top = lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.canvas.fit_view()
    app.update()
    app.mode_var.set("select")
    app._on_mode_change()
    return app, top


def test_create_cell_from_two_shapes_rebases_geometry_to_the_origin():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 5000, 5000, 5100, 5100)
        b = top.add_rectangle(0, 5100, 5000, 5300, 5200)
        app.canvas.selected_multi = [("shape", a), ("shape", b)]

        assert app.canvas.create_cell_from_selection("PAD", (5000, 5000)) is True

        assert "PAD" in app.canvas.library.cells
        pad = app.canvas.library.cells["PAD"]
        assert len(pad.shapes) == 2
        assert a in pad.shapes
        assert b in pad.shapes
        assert a not in top.shapes
        assert b not in top.shapes
        # geometry re-based so (5000, 5000) becomes local (0, 0)
        xs = [p[0] for p in a.points] + [p[0] for p in b.points]
        ys = [p[1] for p in a.points] + [p[1] for p in b.points]
        assert min(xs) == 0
        assert min(ys) == 0
    finally:
        app.destroy()


def test_original_cell_gets_an_instance_in_place_of_the_selection():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 5000, 5000, 5100, 5100)
        app.canvas.selected_multi = [("shape", a)]

        app.canvas.create_cell_from_selection("PAD", (5000, 5000))

        assert len(top.srefs) == 1
        ref = top.srefs[0]
        assert ref.cell_name == "PAD"
        assert ref.origin == (5000, 5000)
        assert app.canvas.selected == ("sref", ref)
        assert app.canvas.selected_multi == []
    finally:
        app.destroy()


def test_visual_position_is_unchanged_after_wrapping_into_a_cell():
    """The point of MAKECELL/Create Cell from Selection is that nothing
    visually moves -- the new SRef placed at `origin` should reproduce
    the exact same absolute geometry the original shape had."""
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 5000, 5000, 5100, 5100)
        original_points = list(a.points)
        app.canvas.selected_multi = [("shape", a)]

        app.canvas.create_cell_from_selection("PAD", (5000, 5000))

        ref = top.srefs[0]
        ox, oy = ref.origin
        local_points = [p for p in app.canvas.library.cells["PAD"].shapes[0].points]
        reconstructed = [(px + ox, py + oy) for px, py in local_points]
        assert sorted(reconstructed) == sorted(original_points)
    finally:
        app.destroy()


def test_create_cell_undoes_cleanly():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 0, 0, 100, 100)
        app.canvas.selected_multi = [("shape", a)]

        app.canvas.create_cell_from_selection("PAD", (0, 0))
        assert "PAD" in app.canvas.library.cells
        assert len(top.shapes) == 0
        assert len(top.srefs) == 1

        app.session.undo()
        assert len(app.session.cell.shapes) == 1
        assert len(app.session.cell.srefs) == 0
        # the new cell itself is not retroactively un-created by undo
        # (Session.undo() restores the *current* cell's contents from
        # a Library snapshot; PAD still exists in the library) -- but
        # the design no longer references it, matching a real undo of
        # "the selection became a cell instance"
    finally:
        app.destroy()


def test_name_collision_raises_cell_error_and_leaves_selection_alone():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 0, 0, 100, 100)
        app.canvas.selected_multi = [("shape", a)]

        with pytest.raises(CellError):
            app.canvas.create_cell_from_selection("TOP", (0, 0))  # TOP already exists

        assert a in top.shapes  # untouched
    finally:
        app.destroy()


def test_replace_true_rebuilds_an_existing_cell_in_place_keeping_its_instances():
    app, top = _make_app()
    try:
        # a PAD cell already exists and is instanced twice in the design
        pad = app.canvas.library.new_cell("PAD")
        pad.add_rectangle(0, 0, 0, 100, 100)          # old geometry: 100 x 100
        ref_a = top.add_sref("PAD", (10000, 0))
        ref_b = top.add_sref("PAD", (20000, 0))

        # new geometry to redefine PAD with
        g = top.add_rectangle(0, 5000, 5000, 5400, 5300)
        app.canvas.selected_multi = [("shape", g)]

        assert app.canvas.create_cell_from_selection("PAD", (5000, 5000), replace=True) is True

        pad = app.canvas.library.cells["PAD"]
        assert len(pad.shapes) == 1
        x0, y0, x1, y1 = pad.shapes[0].bbox()
        assert (x1 - x0, y1 - y0) == (400, 300)       # rebuilt with the new geometry
        # the two pre-existing instances are still there and still valid
        assert ref_a in top.srefs and ref_b in top.srefs
        assert ref_a.cell_name == "PAD" and ref_b.cell_name == "PAD"
        assert "PAD" in app.canvas.library.cells
    finally:
        app.destroy()


def test_replace_false_still_raises_on_a_taken_name():
    app, top = _make_app()
    try:
        app.canvas.library.new_cell("PAD")
        a = top.add_rectangle(0, 0, 0, 100, 100)
        app.canvas.selected_multi = [("shape", a)]
        with pytest.raises(CellError):
            app.canvas.create_cell_from_selection("PAD", (0, 0), replace=False)
        assert a in top.shapes
    finally:
        app.destroy()


def test_replace_refuses_a_self_referential_selection():
    app, top = _make_app()
    try:
        app.canvas.library.new_cell("PAD")
        inst = top.add_sref("PAD", (1000, 1000))       # selecting an instance of PAD...
        app.canvas.selected_multi = [("sref", inst)]
        with pytest.raises(CellError):                  # ...can't become PAD's own contents
            app.canvas.create_cell_from_selection("PAD", (1000, 1000), replace=True)
        assert inst in top.srefs                        # selection untouched
    finally:
        app.destroy()


def test_wrapping_an_instance_that_reaches_the_current_cell_is_refused():
    # TOP -> MOD -> TOP-reaching would-be cycle: MOD contains an instance
    # of TOP; wrapping MOD into a new cell and placing that back in TOP
    # would close a loop. Must be refused before any mutation.
    app, top = _make_app()
    try:
        lib = app.canvas.library
        mod = lib.new_cell("MOD")
        mod.add_sref("TOP", (0, 0))                     # MOD already references TOP
        inst = top.add_sref("MOD", (5000, 0))
        app.canvas.selected_multi = [("sref", inst)]

        with pytest.raises(CellError):
            app.canvas.create_cell_from_selection("WRAP", (5000, 0))
        assert "WRAP" not in lib.cells                  # nothing created
        assert inst in top.srefs                        # selection untouched
    finally:
        app.destroy()


def test_redraw_survives_a_cyclic_cell_reference():
    # A design that somehow ends up with a cyclic reference must still
    # open, redraw, and fit -- not crash every repaint -- so the user
    # can find and break the loop.
    app, top = _make_app()
    try:
        lib = app.canvas.library
        a = lib.new_cell("CYC_A")
        b = lib.new_cell("CYC_B")
        a.add_rectangle(0, 0, 0, 100, 100)
        a.add_sref("CYC_B", (0, 0))
        b.add_sref("CYC_A", (0, 0))                     # A <-> B loop (low-level, unguarded)
        ref = top.add_sref("CYC_A", (0, 0))
        app.canvas.selected = ("sref", ref)

        app.canvas._cell_bbox_safe("CYC_A")             # returns None, no raise
        assert app.canvas._cell_bbox_safe("CYC_A") is None
        app.canvas.redraw()                             # must not raise
        app.canvas.fit_view()                           # must not raise
    finally:
        app.destroy()


def _patch_collision_dialog(monkeypatch, chosen):
    """Make the name prompt return 'PAD' and the collision dialog return
    `chosen` ('revision' / 'replace' / None). Returns the list the
    dialog message is appended to."""
    from hlayout.gui.app import simpledialog as sd
    from hlayout.gui.app import dialogs
    monkeypatch.setattr(sd, "askstring", lambda *A, **K: "PAD")
    monkeypatch.setattr(dialogs, "show_info", lambda *A, **K: None)
    seen = []
    monkeypatch.setattr(dialogs, "ask_choice",
                        lambda title, message, parent, choices, **K:
                        seen.append((message, choices)) or chosen)
    return seen


def test_collision_dialog_offers_revision_and_replace(monkeypatch):
    app, top = _make_app()
    try:
        app.canvas.library.new_cell("PAD")
        top.add_rectangle(0, 40000, 30000, 41000, 31000)
        app.canvas.selected_multi = [("shape", top.shapes[0])]

        seen = _patch_collision_dialog(monkeypatch, "replace")
        app._create_cell_from_selection()

        msg, choices = seen[0]
        assert "already exists" in msg.lower()
        keys = [c[0] for c in choices]
        assert keys == ["revision", "replace", None]
        assert choices[0][1] == "New revision (PAD_1)"
        assert app.canvas.draw_mode == "cell_origin"
        assert app.canvas._pending_cell_replace is True
    finally:
        app.destroy()


def test_collision_dialog_revision_choice_uses_the_suffixed_name(monkeypatch):
    app, top = _make_app()
    try:
        app.canvas.library.new_cell("PAD")
        app.canvas.library.new_cell("PAD_1")          # PAD_2 is the next free name
        top.add_rectangle(0, 40000, 30000, 41000, 31000)
        app.canvas.selected_multi = [("shape", top.shapes[0])]

        _patch_collision_dialog(monkeypatch, "revision")
        app._create_cell_from_selection()

        assert app.canvas._pending_cell_name == "PAD_2"
        assert app.canvas._pending_cell_replace is False
        assert app.canvas.draw_mode == "cell_origin"
    finally:
        app.destroy()


def test_collision_dialog_cancel_does_nothing(monkeypatch):
    app, top = _make_app()
    try:
        app.canvas.library.new_cell("PAD")
        top.add_rectangle(0, 0, 0, 100, 100)
        app.canvas.selected_multi = [("shape", top.shapes[0])]

        _patch_collision_dialog(monkeypatch, None)
        app._create_cell_from_selection()

        assert app.canvas.draw_mode == "select"
        assert list(app.canvas.library.cells) == ["TOP", "PAD"]
    finally:
        app.destroy()


def test_makecell_command_replace_option(monkeypatch):
    app, top = _make_app()
    try:
        pad = app.canvas.library.new_cell("PAD")
        pad.add_rectangle(0, 0, 0, 100, 100)
        g = top.add_rectangle(0, 0, 0, 400, 400)
        app.canvas.selected_multi = [("shape", g)]

        out = app.interp.run("MAKECELL PAD 0,0")
        assert out.startswith("error:") and "already exists" in out

        out2 = app.interp.run("MAKECELL PAD 0,0 replace=1")
        assert "replaced" in out2
        x0, y0, x1, y1 = app.canvas.library.cells["PAD"].shapes[0].bbox()
        assert (x1 - x0, y1 - y0) == (400, 400)
    finally:
        app.destroy()


def test_makecell_command_revision_option():
    app, top = _make_app()
    try:
        app.canvas.library.new_cell("PAD")
        old_ref = top.add_sref("PAD", (9000, 0))
        g = top.add_rectangle(0, 0, 0, 400, 400)
        app.canvas.selected_multi = [("shape", g)]

        out = app.interp.run("MAKECELL PAD 0,0 revision=1")
        assert "PAD_1" in out and "saved" in out
        assert "PAD" in app.canvas.library.cells
        assert "PAD_1" in app.canvas.library.cells
        assert old_ref.cell_name == "PAD"          # old instance untouched
        assert app.canvas.library.cells["PAD"].shapes == []  # old PAD unchanged
    finally:
        app.destroy()


def test_nothing_selected_returns_false():
    app, top = _make_app()
    try:
        app.canvas.selected = None
        app.canvas.selected_multi = []
        assert app.canvas.create_cell_from_selection("PAD", (0, 0)) is False
    finally:
        app.destroy()


def test_vertex_selection_is_not_eligible():
    app, top = _make_app()
    try:
        poly = top.add_polygon(0, [(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
        app.canvas.selected = ("vertex", poly, 0)
        app.canvas.selected_multi = []
        assert app.canvas.create_cell_from_selection("PAD", (0, 0)) is False
    finally:
        app.destroy()


def test_command_bar_makecell():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 0, 0, 100, 100)
        app.canvas.selected_multi = [("shape", a)]
        out = app.interp.run("MAKECELL PAD 0,0")
        assert "created" in out.lower()
        assert "PAD" in app.canvas.library.cells

        app.canvas.selected = None
        app.canvas.selected_multi = []
        out2 = app.interp.run("MAKECELL PAD2 0,0")
        assert out2.startswith("error:")
    finally:
        app.destroy()


def test_selection_bbox_reports_the_combined_box_of_the_selection():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 40000, 30000, 41000, 31000)
        b = top.add_rectangle(0, 41500, 30500, 42000, 32000)
        app.canvas.selected_multi = [("shape", a), ("shape", b)]
        assert app.canvas.selection_bbox() == (40000, 30000, 42000, 32000)

        app.canvas.selected_multi = []
        app.canvas.selected = None
        assert app.canvas.selection_bbox() is None
    finally:
        app.destroy()


def test_create_cell_dialog_asks_only_the_name_then_enters_origin_pick(monkeypatch):
    """The dialog asks just for a name and then hands off to a
    click-to-set-origin mode -- no coordinate typing."""
    from hlayout.gui.app import simpledialog as sd
    from hlayout.gui.app import dialogs
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 40000, 30000, 41000, 31000)
        app.canvas.selected_multi = [("shape", a)]

        prompts = []
        monkeypatch.setattr(sd, "askstring",
                            lambda title, prompt, **kw: prompts.append(prompt) or "PAD")
        infos = []
        monkeypatch.setattr(dialogs, "show_info",
                            lambda title, message, **kw: infos.append(message))
        app._create_cell_from_selection()

        assert len(prompts) == 1                      # name only, no origin prompt
        assert app.canvas.draw_mode == "cell_origin"
        assert app.canvas._pending_cell_name == "PAD"
        assert infos and "origin" in infos[-1].lower()  # user is told to click the origin
    finally:
        app.destroy()


def test_click_in_cell_origin_mode_creates_the_cell_at_that_point():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 40000, 30000, 41000, 31000)
        app.canvas.selected_multi = [("shape", a)]
        app.canvas.snap_enabled = False
        assert app.canvas.start_cell_from_selection("PAD") is True

        app.canvas._on_click(_ev(*app.canvas.to_px(40000, 30000)))

        assert app.canvas.draw_mode == "select"
        assert "PAD" in app.canvas.library.cells
        pad = app.canvas.library.cells["PAD"]
        xs = [p[0] for p in a.points]
        ys = [p[1] for p in a.points]
        assert min(xs) == 0 and min(ys) == 0          # re-based to the clicked point
        assert top.srefs[-1].cell_name == "PAD"
        assert top.srefs[-1].origin == (40000, 30000)
    finally:
        app.destroy()


def test_escape_in_cell_origin_mode_aborts_and_restores_the_selection():
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 40000, 30000, 41000, 31000)
        app.canvas.selected_multi = [("shape", a)]
        app.canvas.start_cell_from_selection("PAD")

        app.canvas.cancel_draw()

        assert app.canvas.draw_mode == "select"
        assert "PAD" not in app.canvas.library.cells
        assert a in top.shapes                         # nothing moved
        assert app.canvas.selected_multi == [("shape", a)]  # selection restored
    finally:
        app.destroy()


def test_selection_far_from_origin_is_placeable_after_the_new_flow():
    """The original bug: with the old typed-'0,0' default a second
    instance landed 40000,30000 off-screen. Clicking the origin inside
    the selection keeps the new cell's geometry near its own (0,0)."""
    app, top = _make_app()
    try:
        a = top.add_rectangle(0, 40000, 30000, 41000, 31000)
        app.canvas.selected_multi = [("shape", a)]
        app.canvas.snap_enabled = False
        app.canvas.start_cell_from_selection("PAD")
        app.canvas._on_click(_ev(*app.canvas.to_px(40000, 30000)))

        pad = app.canvas.library.cells["PAD"]
        px = [p[0] for c in pad.shapes for p in c.points]
        py = [p[1] for c in pad.shapes for p in c.points]
        # geometry now sits near local origin, not 40000,30000 away
        assert max(px) <= 2000 and max(py) <= 2000
    finally:
        app.destroy()


def test_sref_selection_can_also_become_a_cell():
    """Nesting a placed instance into a new cell -- e.g. wrapping a
    couple of placed SMD parts into a bigger reusable module cell."""
    app, top = _make_app()
    try:
        app.canvas.library.new_cell("PART")
        ref = top.add_sref("PART", (2000, 3000))
        app.canvas.selected_multi = [("sref", ref)]

        assert app.canvas.create_cell_from_selection("MODULE", (2000, 3000)) is True
        assert ref in app.canvas.library.cells["MODULE"].srefs
        assert ref not in top.srefs
        assert ref.origin == (0, 0)
    finally:
        app.destroy()
