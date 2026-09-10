import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.errors import LayoutError
from hlayout.core.library import Library
from hlayout.core.shapes import Path
from hlayout.io.techfile import (
    DEFAULT_BOND_RULES,
    load_and_apply_techfile,
    load_bond_rules,
    save_bond_rules,
    save_techfile,
)
from hlayout.scripting import Session

BOND = 6


def _design():
    """Two conductor pads on L3 and a die-pin pad on L58, room for a
    ~2 mm bond between the L3 pads."""
    s = Session.new("DIE")
    s.new_cell("TOP")
    s.rect(3, 0, 0, 100, 100, pin="A")
    s.rect(3, 2000, 0, 2100, 100, pin="B")
    save_bond_rules(s.library, {"layer": BOND, "width": 30, "pad": 100,
                                "min_length": 200, "max_length": 4000})
    s.clear_undo_history()
    return s


def _bonds(s):
    return [sh for sh in s.cell.shapes if isinstance(sh, Path) and sh.layer == BOND]


# --- tech rules ------------------------------------------------------

def test_bond_rules_default_when_absent():
    assert load_bond_rules(Library("X")) == DEFAULT_BOND_RULES


def test_bond_rules_round_trip_through_a_full_techfile(tmp_path):
    lib = Library("X")
    save_bond_rules(lib, {"layer": 6, "width": 25, "pad": 90,
                          "min_length": 150, "max_length": 3500})
    assert load_bond_rules(lib) == {
        "layer": 6, "width": 25, "pad": 90,
        "min_length": 150, "max_length": 3500,
    }
    path = str(tmp_path / "t.json")
    save_techfile(lib, path)
    lib2 = Library("Y")
    load_and_apply_techfile(lib2, path)
    assert load_bond_rules(lib2) == load_bond_rules(lib)


# --- drawing -------------------------------------------------------

def test_wire_bond_needs_a_configured_layer():
    s = Session.new("X")
    s.new_cell("TOP")
    with pytest.raises(LayoutError):
        s.wire_bond([(0, 0), (1000, 0)])


def test_wire_bond_draws_an_o_bar_o_figure():
    s = _design()
    s.wire_bond([(50, 50), (2050, 50)])
    kinds = [type(sh).__name__ for sh in s.cell.shapes if sh.layer == BOND]
    # two end balls (circles are stored as Polygons) + one connecting Path
    assert kinds.count("Path") == 1
    assert kinds.count("Polygon") == 2


def test_wire_bond_rejects_a_span_shorter_than_min():
    s = _design()
    with pytest.raises(LayoutError, match="shorter than"):
        s.wire_bond([(50, 50), (120, 50)])
    assert _bonds(s) == []


def test_wire_bond_rejects_a_span_longer_than_max():
    s = _design()
    with pytest.raises(LayoutError, match="longer than"):
        s.wire_bond([(0, 0), (9000, 0)])


def test_wire_bond_rejects_wrong_point_count():
    s = _design()
    with pytest.raises(LayoutError):
        s.wire_bond([(0, 0)])
    with pytest.raises(LayoutError):
        s.wire_bond([(0, 0), (1000, 0), (2000, 0)])


def test_wire_bonds_may_not_cross():
    s = _design()
    s.wire_bond([(50, 50), (2050, 50)])
    with pytest.raises(LayoutError, match="cross"):
        s.wire_bond([(1000, -400), (1000, 500)])


def test_parallel_and_endpoint_sharing_bonds_are_allowed():
    s = _design()
    s.wire_bond([(50, 50), (2050, 50)])
    s.wire_bond([(50, 300), (2050, 300)])          # parallel, clear
    s.wire_bond([(50, 50), (50, 900)])             # shares an endpoint pad
    assert len(_bonds(s)) == 3


def test_wire_bond_is_one_undo_step():
    s = _design()
    s.wire_bond([(50, 50), (2050, 50)])
    assert _bonds(s)
    assert s.undo() is True
    assert _bonds(s) == []


# --- netlist -----------------------------------------------------

def test_netlist_folds_in_the_bond_layer_and_bridges_to_the_die_pin():
    s = Session.new("DIE")
    die = s.new_cell("DIE")
    s.rect(58, 0, 0, 80, 80, pin="3")
    s.new_cell("TOP")
    s.use_cell("TOP")
    s.sref("DIE", (0, 0), label="U1")
    s.rect(3, 2000, 0, 2200, 200, pin="A")
    save_bond_rules(s.library, {"layer": BOND, "width": 30, "pad": 150,
                                "min_length": 100, "max_length": 6000})
    s.wire_bond([(40, 40), (2100, 100)])

    joined = s.extract_netlist([3, 58])
    assert len(joined) == 1
    labels = sorted(p.label() for p in joined[0].pins)
    assert labels == ["?.A", "U1.3"]

    split = s.extract_netlist([3, 58], include_bonds=False)
    assert len(split) == 2


# --- command bar ----------------------------------------------

def test_bond_command_draws_and_reports_span():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _design()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("BOND 50,50 2050,50")
    assert "span" in out
    assert len(_bonds(s)) == 1


def test_bond_command_surfaces_a_crossing_error():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _design()
    interp = CommandInterpreter(s, ScriptConsole(s))
    interp.run("BOND 50,50 2050,50")
    out = interp.run("BOND 1000,-400 1000,500")
    assert "error" in out.lower() and "cross" in out.lower()


def test_netlist_command_bonds_opt_out():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _design()
    interp = CommandInterpreter(s, ScriptConsole(s))
    interp.run("BOND 50,50 2050,50")
    with_bonds = interp.run("NETLIST RUN 3")
    without = interp.run("NETLIST RUN 3 bonds=0")
    assert with_bonds != without


# --- move / delete an existing bond -----------------------------

def _polys(s):
    from hlayout.core.shapes import Polygon
    return [sh for sh in s.cell.shapes if isinstance(sh, Polygon) and sh.layer == BOND]


def test_find_wire_bond_matches_a_click_anywhere_along_it():
    s = _design()
    s.wire_bond([(50, 50), (2050, 50)])
    # a point on the connector, nowhere near either end disc
    found = s.find_wire_bond((1000, 50))
    assert found is not None
    path, balls = found
    assert list(path.points) == [(50, 50), (2050, 50)]
    assert len(balls) == 2
    # far away -> nothing
    assert s.find_wire_bond((1000, 5000)) is None


def test_move_wire_bond_moves_only_the_grabbed_end():
    s = _design()
    s.wire_bond([(50, 50), (2050, 50)])
    centres_before = {tuple(round(v) for v in _bbox_centre(p)) for p in _polys(s)}

    fixed, moved = s.move_wire_bond((50, 50), (50, 650))
    assert fixed == (2050, 50)                    # the far end stayed put
    assert moved == (50, 650)
    assert set(map(tuple, _bonds(s)[0].points)) == {(50, 650), (2050, 50)}

    centres_after = {tuple(round(v) for v in _bbox_centre(p)) for p in _polys(s)}
    # one disc moved from (50, 50) to (50, 650); the other is unchanged
    assert (2050, 50) in centres_after
    assert (50, 650) in centres_after
    assert (50, 50) not in centres_after
    assert centres_before == {(50, 50), (2050, 50)}


def test_move_wire_bond_picks_whichever_end_the_click_is_closer_to():
    s = _design()
    s.wire_bond([(50, 50), (2050, 50)])
    fixed, moved = s.move_wire_bond((2050, 50), (2050, 700))   # grab the RIGHT end
    assert fixed == (50, 50)
    assert moved == (2050, 700)


def test_move_wire_bond_rejects_a_new_span_out_of_range():
    s = _design()                                # min 200, max 4000
    s.wire_bond([(50, 50), (2050, 50)])
    with pytest.raises(LayoutError, match="shorter than"):
        s.move_wire_bond((50, 50), (2000, 50))   # 50 DBU from the fixed end
    with pytest.raises(LayoutError, match="longer than"):
        s.move_wire_bond((50, 50), (50, 6000))   # ~6500 DBU from the fixed end
    assert list(_bonds(s)[0].points) == [(50, 50), (2050, 50)]   # unchanged


def test_move_wire_bond_is_one_undo_step_and_rejects_a_crossing():
    s = _design()
    s.wire_bond([(50, 50), (2050, 50)])              # the bond we move
    s.wire_bond([(1000, -1000), (1000, -200)])       # a vertical bond below it
    s.clear_undo_history()

    with pytest.raises(LayoutError, match="cross"):
        s.move_wire_bond((50, 50), (50, -700))       # left end swings down through it
    assert list(_bonds(s)[0].points) == [(50, 50), (2050, 50)]   # unchanged

    fixed, moved = s.move_wire_bond((50, 50), (50, 900))         # up is clear
    assert (fixed, moved) == ((2050, 50), (50, 900))
    assert s.undo() is True
    assert list(_bonds(s)[0].points) == [(50, 50), (2050, 50)]


def test_delete_wire_bond_removes_all_three_shapes_in_one_step():
    s = _design()
    s.wire_bond([(50, 50), (2050, 50)])
    s.wire_bond([(50, 400), (2050, 400)])
    s.clear_undo_history()

    e1, e2 = s.delete_wire_bond((1000, 50))
    assert (e1, e2) == ((50, 50), (2050, 50))
    assert len(_bonds(s)) == 1                    # the other bond is untouched
    assert len(_polys(s)) == 2
    assert s.undo() is True
    assert len(_bonds(s)) == 2
    assert len(_polys(s)) == 4


def test_delete_wire_bond_with_nothing_near_raises():
    s = _design()
    s.wire_bond([(50, 50), (2050, 50)])
    with pytest.raises(LayoutError, match="no wire bond"):
        s.delete_wire_bond((1000, 9000))


def test_bond_move_and_delete_commands():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _design()
    interp = CommandInterpreter(s, ScriptConsole(s))
    interp.run("BOND 50,50 2050,50")

    out = interp.run("BOND MOVE 50,50 50,650")
    assert "moved" in out.lower()
    assert set(map(tuple, _bonds(s)[0].points)) == {(50, 650), (2050, 50)}

    out = interp.run("BOND DELETE 1000,350")
    assert "removed" in out.lower()
    assert _bonds(s) == []

    out = interp.run("BOND DELETE 1000,650")
    assert "error" in out.lower() and "no wire bond" in out.lower()


def _bbox_centre(poly):
    x1, y1, x2, y2 = poly.bbox()
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


# --- Edit menu guided tools ------------------------------------

def _app_with_a_bond():
    tk = pytest.importorskip("tkinter")
    from hlayout.gui.app import App
    lib = Library("T")
    lib.new_cell("TOP")
    try:
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    s = app.session
    s.rect(3, 0, 0, 100, 100)
    s.rect(3, 2000, 0, 2100, 100)
    save_bond_rules(lib, {"layer": BOND, "width": 30, "pad": 100,
                          "min_length": 200, "max_length": 4000})
    s.wire_bond([(50, 50), (2050, 50)])
    app.canvas.set_cell("TOP")
    return app


def test_edit_menu_has_move_and_delete_wire_bond_items():
    tk = pytest.importorskip("tkinter")
    app = _app_with_a_bond()
    try:
        for label, sub in app._top_menus:
            if label == "Edit":
                labels = [sub.entrycget(j, "label")
                          for j in range(sub.index("end") + 1)
                          if _entry_label(sub, j)]
                assert "Move Wire Bond" in labels
                assert "Delete Wire Bond" in labels
                return
        raise AssertionError("no Edit menu")
    finally:
        app.destroy()


def _entry_label(sub, j):
    import tkinter as tk
    try:
        sub.entrycget(j, "label")
        return True
    except tk.TclError:
        return False


def test_delete_wire_bond_tool_arms_and_removes_on_click():
    app = _app_with_a_bond()
    try:
        s = app.session
        app._delete_wire_bond_tool()
        assert app.canvas.draw_mode == "del_bond"
        app._on_canvas_commit("del_bond", 3, [(1000, 50)], {})
        assert _bonds(s) == []
        assert _polys(s) == []
        assert app.canvas.draw_mode == "del_bond"     # stays armed
        app.canvas.cancel_draw()
        assert app.canvas.draw_mode == "select"        # Esc exits
    finally:
        app.destroy()


def test_move_wire_bond_tool_moves_the_grabbed_end_only():
    app = _app_with_a_bond()                       # bond is (50,50)-(2050,50)
    try:
        s = app.session
        app._move_wire_bond_tool()
        assert app.canvas.draw_mode == "move_bond"
        # first click near the left end, second click its new spot
        app._on_canvas_commit("move_bond", 3, [(50, 50), (50, 650)], {})
        assert set(map(tuple, _bonds(s)[0].points)) == {(50, 650), (2050, 50)}
        assert len(_polys(s)) == 2
        assert app.canvas.draw_mode == "move_bond"    # stays armed
    finally:
        app.destroy()


def test_find_bond_to_move_returns_the_far_end_as_fixed():
    app = _app_with_a_bond()                       # bond (50,50)-(2050,50)
    try:
        c = app.canvas
        assert c._find_bond_to_move((60, 50)) == ((2050, 50), (50, 50))
        assert c._find_bond_to_move((2040, 50)) == ((50, 50), (2050, 50))
        assert c._find_bond_to_move((1000, 60)) is not None   # anywhere along it
        assert c._find_bond_to_move((1000, 9000)) is None     # nowhere near a bond
    finally:
        app.destroy()


def test_move_wire_bond_preview_is_drawn_after_the_grab_and_cleared_on_exit():
    app = _app_with_a_bond()
    try:
        c = app.canvas
        c.set_mode("move_bond")
        c._pending_points = [(60, 50)]            # left end grabbed
        c._update_preview(50, 650)               # cursor now here
        assert c.find_withtag("preview")          # O------O + ghost on screen
        c.cancel_draw()
        assert not c.find_withtag("preview")      # cleared when the tool exits
    finally:
        app.destroy()
