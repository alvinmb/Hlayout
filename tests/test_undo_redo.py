import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.scripting import Session, ScriptConsole
from hlayout.gui.commands import CommandInterpreter


def _fresh_session(name="DEMO", cell="TOP"):
    """A Session with one cell already created, and undo history reset
    to empty -- new_cell() is itself a checkpointed, undoable operation
    (correct for real usage: undoing a script's first CELL command
    should work), but these tests want a clean baseline to isolate the
    specific edit under test, not that setup step."""
    s = Session.new(name)
    s.new_cell(cell)
    s.clear_undo_history()
    return s


def test_undo_reverts_a_shape_add_redo_brings_it_back():
    s = _fresh_session()
    assert not s.can_undo()

    s.rect(0, 0, 0, 10, 10)
    assert len(s.cell.shapes) == 1
    assert s.can_undo()

    assert s.undo() is True
    assert len(s.cell.shapes) == 0
    assert s.can_redo()

    assert s.redo() is True
    assert len(s.cell.shapes) == 1
    x1, y1 = s.cell.shapes[0].points[0]
    assert (x1, y1) == (0, 0)


def test_undo_with_nothing_to_undo_returns_false():
    s = _fresh_session()
    assert s.undo() is False
    assert s.redo() is False


def test_multiple_undo_steps_in_order():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    s.rect(0, 20, 20, 30, 30)
    s.rect(0, 40, 40, 50, 50)
    assert len(s.cell.shapes) == 3

    s.undo()
    assert len(s.cell.shapes) == 2
    s.undo()
    assert len(s.cell.shapes) == 1
    s.undo()
    assert len(s.cell.shapes) == 0
    assert s.undo() is False  # nothing further back


def test_new_edit_after_undo_clears_redo_history():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    s.undo()
    assert s.can_redo()

    s.rect(0, 5, 5, 15, 15)  # a fresh edit, not a redo
    assert not s.can_redo()


def test_undo_covers_polygon_path_circle_text_and_sref():
    s = _fresh_session()
    s.new_cell("SUB")
    s.use_cell("TOP")
    s.clear_undo_history()

    s.polygon(0, [(0, 0), (10, 0), (5, 10)])
    s.path(1, [(0, 0), (20, 20)], width=4, cap="round")
    s.circle(2, (0, 0), 5)
    s.text(3, (0, 0), "HI")
    s.sref("SUB", (0, 0))
    assert len(s.cell.shapes) == 4
    assert len(s.cell.srefs) == 1

    assert s.undo() is True  # undoes the sref
    assert len(s.cell.srefs) == 0
    assert s.undo() is True  # undoes the text
    assert len(s.cell.shapes) == 3
    assert s.undo() is True  # circle
    assert s.undo() is True  # path
    assert s.undo() is True  # polygon
    assert len(s.cell.shapes) == 0


def test_undo_covers_remove():
    s = _fresh_session()
    shape = s.rect(0, 0, 0, 10, 10)
    s.remove(shape)
    assert len(s.cell.shapes) == 0
    assert s.undo() is True
    assert len(s.cell.shapes) == 1


def test_undo_covers_new_cell():
    s = _fresh_session()
    assert list(s.library.cells) == ["TOP"]
    s.new_cell("EXTRA")
    assert set(s.library.cells) == {"TOP", "EXTRA"}
    assert s.current_cell_name == "EXTRA"

    assert s.undo() is True
    assert set(s.library.cells) == {"TOP"}
    # the undone cell no longer exists -- current_cell_name falls back
    assert s.current_cell_name == "TOP"


def test_undo_covers_remap_layers():
    s = _fresh_session()
    s.rect(3, 0, 0, 10, 10)
    s.remap_layers({3: 7})
    assert s.cell.shapes[0].layer == 7

    assert s.undo() is True
    assert s.cell.shapes[0].layer == 3


def test_undo_covers_place_cell_as_a_single_step(tmp_path):
    # place_cell() from an attached library does two things internally
    # (import the cell closure, then add the sref) -- they should undo
    # together as one user-facing step, not two.
    part = Session.new("PARTS")
    part.new_cell("PAD")
    part.rect(0, 0, 0, 10, 10)
    path = str(tmp_path / "parts.gds")
    part.save(path)

    s = _fresh_session("DEMO")
    s.attach_library(path)
    assert "PAD" not in s.library.cells

    s.place_cell("PAD", (0, 0))
    assert "PAD" in s.library.cells
    assert len(s.cell.srefs) == 1

    assert s.undo() is True
    assert "PAD" not in s.library.cells
    assert len(s.cell.srefs) == 0
    assert s.undo() is False  # only one undo step was consumed


def test_undo_covers_load_technology(tmp_path):
    s = _fresh_session()
    s.library.layers.set_style(0, name="ORIGINAL")
    s.clear_undo_history()
    tech_path = str(tmp_path / "tech.json")

    other = _fresh_session("OTHER")
    other.library.layers.set_style(0, name="FROMTECH")
    other.save_technology(tech_path)

    s.load_technology(tech_path)
    assert s.library.layers[0].name == "FROMTECH"

    assert s.undo() is True
    assert s.library.layers[0].name == "ORIGINAL"


def test_undo_history_capped_at_limit():
    s = _fresh_session()
    s.UNDO_LIMIT = 3
    for i in range(5):
        s.rect(0, i, i, i + 1, i + 1)
    assert len(s._undo_stack) == 3
    # can still undo exactly 3 times
    assert s.undo() and s.undo() and s.undo()
    assert s.undo() is False


def test_clear_undo_history_empties_both_stacks():
    s = _fresh_session()
    s.rect(0, 0, 0, 10, 10)
    s.undo()
    assert s.can_redo()
    s.clear_undo_history()
    assert not s.can_undo()
    assert not s.can_redo()


def test_undo_does_not_touch_attached_libraries(tmp_path):
    part = Session.new("PARTS")
    part.new_cell("DUMMY")
    part.rect(0, 0, 0, 1, 1)
    path = str(tmp_path / "parts.gds")
    part.save(path)

    s = _fresh_session("DEMO")
    s.rect(0, 0, 0, 10, 10)
    s.attach_library(path)
    s.create_via_cell(0, 1)  # mutates the attached library, not s.library

    assert s.undo() is True  # undoes the rect, not the via
    assert len(s.cell.shapes) == 0
    assert "VIA_Layer0_to_Layer1" in s.attached_libraries[0].cells


def test_undo_redo_commands_via_command_bar():
    s = _fresh_session()
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    interp.run("RECT 0 0,0 10,10")
    assert len(s.cell.shapes) == 1

    out = interp.run("UNDO")
    assert out == "undo"
    assert len(s.cell.shapes) == 0

    out = interp.run("REDO")
    assert out == "redo"
    assert len(s.cell.shapes) == 1


def test_undo_redo_commands_report_when_nothing_to_do():
    s = _fresh_session()
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    assert interp.run("UNDO") == "nothing to undo"
    assert interp.run("REDO") == "nothing to redo"


def test_open_command_clears_undo_history(tmp_path):
    other = Session.new("OTHER")
    other.new_cell("TOP")
    other.rect(0, 0, 0, 1, 1)
    path = str(tmp_path / "other.gds")
    other.save(path)

    s = _fresh_session("DEMO")
    s.rect(0, 0, 0, 10, 10)
    assert s.can_undo()

    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)
    interp.run(f"OPEN {path}")
    assert not s.can_undo()
    assert not s.can_redo()
