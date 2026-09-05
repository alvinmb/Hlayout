import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.scripting import Session, ScriptConsole
from hlayout.gui.commands import CommandInterpreter, _tokenize


def test_tokenize_keeps_windows_backslash_paths_intact():
    # A plain shlex.split() runs POSIX-mode and eats backslashes as
    # escapes, so a Windows path argument like export=C:\Users\me\x.csv
    # came back as export=C:Usersmex.csv -- the command then wrote its
    # output to a mangled relative path in the cwd.
    toks = _tokenize(r"NETLIST RUN 3 export=C:\Users\me\Temp\out.csv")
    assert toks == ["NETLIST", "RUN", "3", r"export=C:\Users\me\Temp\out.csv"]

    # quotes still group and are stripped; both quote styles work
    assert _tokenize(r'SAVE "C:\My Designs\board.gds"') == ["SAVE", r"C:\My Designs\board.gds"]
    assert _tokenize("TEXT 3 0,0 'hello world'") == ["TEXT", "3", "0,0", "hello world"]

    # an unbalanced quote still raises, same as shlex.split()
    with pytest.raises(ValueError):
        _tokenize('SAVE "unterminated')


def test_gerber_command_accepts_a_windows_style_prefix(tmp_path):
    from hlayout.core.units import mm_to_dbu
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.rect(0, 0, 0, mm_to_dbu(1), mm_to_dbu(1))
    interp = CommandInterpreter(s, ScriptConsole(s))
    # force backslash separators regardless of the host os.sep
    prefix = str(tmp_path / "board").replace("/", "\\")

    out = interp.run(f"GERBER {prefix}")

    assert "wrote 1 Gerber file(s)" in out
    assert list(tmp_path.glob("board_L00_*.gbr"))


def test_session_shortcuts():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.rect(0, 0, 0, 100, 50)
    s.polygon(1, [(0, 0), (10, 0), (5, 10)])
    s.path(2, [(0, 0), (20, 20)], width=4, cap="round")
    s.text(3, (1, 1), "HI")
    assert len(s.cell.shapes) == 4


def test_console_exec_and_notify():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    changed = []
    s.on_change(lambda: changed.append(1))
    console = ScriptConsole(s)
    out = console.run("s.rect(0, 0, 0, 10, 10)")
    assert out == ""
    assert len(changed) == 1
    assert len(s.cell.shapes) == 1


def test_console_captures_errors_without_raising():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    out = console.run("1 / 0")
    assert "ZeroDivisionError" in out


def test_command_interpreter_basic_flow():
    s = Session.new("DEMO")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    assert "current cell" in interp.run("CELL TOP")
    assert interp.run("RECT 0 0,0 10,10") == "rectangle added"
    assert interp.run("POLY 1 0,0 10,0 5,10") == "polygon added (3 points)"
    assert interp.run('PATH 2 5 round 0,0 10,10') == "path added (2 points)"
    assert interp.run('TEXT 3 1,1 hello') == "text added"
    assert len(s.cell.shapes) == 4
    assert interp.run("DELETE LAST") == "removed last shape"
    assert len(s.cell.shapes) == 3


def test_command_interpreter_falls_back_to_python():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)
    out = interp.run("s.rect(0, 0, 0, 5, 5)")
    assert len(s.cell.shapes) == 1


def test_help_command_returns_full_categorized_reference():
    from hlayout.gui.commands import COMMAND_REFERENCE

    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    out = interp.run("HELP")
    assert out == COMMAND_REFERENCE
    # every documented command category is present
    for category in ("Cells & shapes", "Layers:", "View:", "Files:",
                      "Attached libraries", "Scripting:"):
        assert category in out
    # a representative command from each category actually exists
    for cmd_name in ("CELL", "RECT", "POLY", "PATH", "CIRCLE", "TEXT", "SREF",
                      "DELETE", "LAYER", "ZOOM", "GRID", "SAVE", "OPEN",
                      "ATTACH", "LIBS", "LIBLIST", "FIND", "PLACE", "PY", "HELP"):
        assert hasattr(interp, f"_cmd_{cmd_name.lower()}"), f"missing handler for {cmd_name}"


def test_open_command_reports_missing_file_as_clean_error_not_crash():
    # Typed into the command bar / script console: OPEN <bad path> used
    # to bubble up gdstk's own stderr noise plus a bare OSError.
    # CommandInterpreter.run() catches everything and returns a string,
    # so it shouldn't raise -- it should return a clean "error: ..." with
    # the path in it, same as any other CommandError.
    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    out = interp.run("OPEN does_not_exist_at_all.gds")
    assert out.startswith("error:")
    assert "does_not_exist_at_all.gds" in out


def test_attach_command_reports_missing_file_as_clean_error_not_crash():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    out = interp.run("ATTACH does_not_exist_at_all.gds")
    assert out.startswith("error:")
    assert "does_not_exist_at_all.gds" in out


def test_layer_all_command_toggles_every_layer_visibility():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    out = interp.run("LAYER ALL visible=0")
    assert "hidden" in out
    assert all(not s.library.layers[n].visible for n in range(len(s.library.layers)))

    out = interp.run("LAYER ALL visible=1")
    assert "shown" in out
    assert all(s.library.layers[n].visible for n in range(len(s.library.layers)))


def test_after_layer_all_off_a_single_layer_can_be_switched_on_via_command_bar():
    # "when you switch all layers off you can switch on 1 or more
    # layers" -- via the command bar as well as the Layers tab.
    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    interp.run("LAYER ALL visible=0")
    assert all(not s.library.layers[n].visible for n in range(len(s.library.layers)))

    interp.run("LAYER 3 visible=1")
    interp.run("LAYER 7 visible=1")
    assert s.library.layers[3].visible is True
    assert s.library.layers[7].visible is True
    # Everything else stays off.
    assert all(not s.library.layers[n].visible for n in range(len(s.library.layers))
               if n not in (3, 7))


def test_layer_all_command_rejects_bad_usage():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    out = interp.run("LAYER ALL")
    assert out.startswith("error:")
    out = interp.run("LAYER ALL color=#ff0000")
    assert out.startswith("error:")


def test_move_and_rotate_commands_require_an_attached_view():
    # No view= was passed to this interpreter, matching how the command
    # bar works when used from a plain script context -- MOVE/ROTATE
    # need a LayoutCanvas (for the current selection) just like ZOOM/GRID.
    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    out = interp.run("MOVE 1,1")
    assert out.startswith("error:")
    out = interp.run("ROTATE 90")
    assert out.startswith("error:")


def test_move_command_rejects_bad_usage():
    class FakeView:
        def move_selected(self, dx, dy):
            return True

    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console, view=FakeView())

    out = interp.run("MOVE")
    assert out.startswith("error:")
    out = interp.run("MOVE 1,2,3")
    assert out.startswith("error:")


def test_tech_save_and_load_commands_round_trip(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)
    path = str(tmp_path / "tech.json")

    s.library.layers.set_style(5, name="VIA1", color="#00ff00")
    out = interp.run(f"TECH SAVE {path}")
    assert "saved" in out

    s2 = Session.new("OTHER")
    s2.new_cell("TOP")
    interp2 = CommandInterpreter(s2, ScriptConsole(s2))
    out = interp2.run(f"TECH LOAD {path}")
    assert "loaded" in out
    assert s2.library.layers[5].name == "VIA1"
    assert s2.library.layers[5].color == "#00ff00"


def test_gerber_command_writes_one_file_per_visible_layer_with_data(tmp_path):
    from hlayout.core.units import mm_to_dbu
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.rect(0, 0, 0, mm_to_dbu(1), mm_to_dbu(1))
    s.rect(1, 0, 0, mm_to_dbu(1), mm_to_dbu(1))
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)
    prefix = str(tmp_path / "board")

    out = interp.run(f"GERBER {prefix}")

    assert "wrote 2 Gerber file(s)" in out
    assert list(tmp_path.glob("board_L00_*.gbr"))
    assert list(tmp_path.glob("board_L01_*.gbr"))


def test_gerber_command_requires_a_prefix():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("GERBER")
    assert "error" in out.lower()


def test_tech_load_command_reports_missing_file_as_clean_error():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("TECH LOAD does_not_exist.json")
    assert out.startswith("error:")
    assert "does_not_exist.json" in out


def test_tech_set_get_and_params_commands():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    interp = CommandInterpreter(s, ScriptConsole(s))

    out = interp.run("TECH SET process 0.35um")
    assert "0.35um" in out
    assert s.library.tech_params["process"] == "0.35um"

    out = interp.run("TECH GET process")
    assert "0.35um" in out

    out = interp.run("TECH GET nosuchkey")
    assert out.startswith("error:")

    out = interp.run("TECH PARAMS")
    assert "process = 0.35um" in out


def test_tech_command_rejects_bad_usage():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    interp = CommandInterpreter(s, ScriptConsole(s))
    assert interp.run("TECH").startswith("error:")
    assert interp.run("TECH BOGUS").startswith("error:")
    assert interp.run("TECH SAVE").startswith("error:")
    assert interp.run("TECH LOAD").startswith("error:")


def test_rotate_command_rejects_bad_usage():
    class FakeView:
        def rotate_selected(self, angle):
            return True

    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console, view=FakeView())

    out = interp.run("ROTATE")
    assert out.startswith("error:")
    out = interp.run("ROTATE notanumber")
    assert out.startswith("error:")


def test_sref_rejects_a_cell_instancing_itself():
    # Regression: Find & Place Cell / Create > Cell Instance letting a
    # cell (e.g. "TOP") place an instance of itself corrupted the design
    # with a cyclic Sref that only surfaced later as a crash (e.g. from
    # fit_view() -> cell_bbox() -> "cyclic cell reference detected").
    from hlayout.core.errors import LibraryError

    s = Session.new("DEMO")
    s.new_cell("TOP")
    with pytest.raises(LibraryError, match="cyclic"):
        s.sref("TOP", (0, 0))
    assert len(s.cell.srefs) == 0  # nothing was added


def test_sref_rejects_an_indirect_cycle():
    from hlayout.core.errors import LibraryError

    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.new_cell("SUB")
    s.use_cell("TOP")
    s.sref("SUB", (0, 0))  # fine: TOP -> SUB

    s.use_cell("SUB")
    with pytest.raises(LibraryError, match="cyclic"):
        s.sref("TOP", (0, 0))  # would close SUB -> TOP -> SUB
    assert len(s.cell.srefs) == 0


def test_place_cell_rejects_self_placement_from_current_library():
    # The exact scenario from the field report: Find & Place Cell finds
    # the current cell itself in its own search results (source="current")
    # and placing it crashes without this guard.
    from hlayout.core.errors import LibraryError

    s = Session.new("DEMO")
    s.new_cell("TOP")
    with pytest.raises(LibraryError, match="cyclic"):
        s.place_cell("TOP", (0, 0), source="current")
    assert len(s.cell.srefs) == 0


def test_sref_self_reference_rejection_does_not_push_a_spurious_undo_step():
    from hlayout.core.errors import LibraryError

    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.clear_undo_history()
    with pytest.raises(LibraryError):
        s.sref("TOP", (0, 0))
    assert not s.can_undo()


def test_copy_command_requires_a_view_and_valid_usage():
    class FakeView:
        def copy_selected(self, dx, dy):
            return True

    s = Session.new("DEMO")
    s.new_cell("TOP")
    console = ScriptConsole(s)

    interp_no_view = CommandInterpreter(s, console)
    out = interp_no_view.run("COPY 1,1")
    assert out.startswith("error:")

    interp = CommandInterpreter(s, console, view=FakeView())
    out = interp.run("COPY")
    assert out.startswith("error:")
    out = interp.run("COPY 1,1")
    assert "copied" in out


def test_remap_layers_moves_shapes_by_default_across_every_cell():
    s = Session.new("DEMO")
    s.new_cell("A")
    s.rect(3, 0, 0, 10, 10)
    s.new_cell("B")
    s.rect(3, 0, 0, 20, 20)
    s.polygon(5, [(0, 0), (1, 0), (0, 1)])  # untouched layer, should stay put

    counts = s.remap_layers({3: 7})
    assert counts == {3: 2}
    assert s.library.cells["A"].shapes[0].layer == 7
    assert s.library.cells["B"].shapes[0].layer == 7
    assert s.library.cells["B"].shapes[1].layer == 5  # layer 5 shape untouched


def test_remap_layers_accepts_layer_names_and_restricts_to_named_cells():
    s = Session.new("DEMO")
    s.library.layers.set_style(3, name="ROUTE1")
    s.library.layers.set_style(7, name="METAL1")
    s.new_cell("A")
    s.rect(3, 0, 0, 10, 10)
    s.new_cell("B")
    s.rect(3, 0, 0, 20, 20)

    counts = s.remap_layers({"ROUTE1": "METAL1"}, cell_names=["A"])
    assert counts == {3: 1}
    assert s.library.cells["A"].shapes[0].layer == 7
    assert s.library.cells["B"].shapes[0].layer == 3  # cell B untouched


def test_remap_layers_no_op_when_from_equals_to_or_nothing_matches():
    s = Session.new("DEMO")
    s.new_cell("A")
    s.rect(3, 0, 0, 10, 10)

    assert s.remap_layers({3: 3}) == {}
    assert s.remap_layers({9: 10}) == {}
    assert s.library.cells["A"].shapes[0].layer == 3


def test_layer_move_command_moves_shapes_across_whole_library():
    s = Session.new("DEMO")
    s.new_cell("A")
    s.rect(3, 0, 0, 10, 10)
    s.new_cell("B")
    s.rect(3, 0, 0, 10, 10)
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    out = interp.run("LAYER MOVE 3 7")
    assert "moved 2 shape" in out
    assert s.library.cells["A"].shapes[0].layer == 7
    assert s.library.cells["B"].shapes[0].layer == 7


def test_layer_move_command_respects_cell_scope():
    s = Session.new("DEMO")
    s.new_cell("A")
    s.rect(3, 0, 0, 10, 10)
    s.new_cell("B")
    s.rect(3, 0, 0, 10, 10)
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    out = interp.run("LAYER MOVE 3 7 cell=A")
    assert "moved 1 shape" in out
    assert s.library.cells["A"].shapes[0].layer == 7
    assert s.library.cells["B"].shapes[0].layer == 3


def test_layer_move_command_reports_nothing_moved():
    s = Session.new("DEMO")
    s.new_cell("A")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    out = interp.run("LAYER MOVE 3 7")
    assert "nothing on layer 3" in out


def test_layer_move_command_rejects_bad_usage():
    s = Session.new("DEMO")
    s.new_cell("A")
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    assert interp.run("LAYER MOVE").startswith("error:")
    assert interp.run("LAYER MOVE 3").startswith("error:")


def _attach_a_library(s, tmp_path, filename="parts.gds"):
    part = Session.new("PARTS")
    part.new_cell("DUMMY")
    part.rect(0, 0, 0, 1, 1)
    path = str(tmp_path / filename)
    part.save(path)
    s.attach_library(path)
    return path


def test_create_via_cell_draws_shape_on_both_layers_and_saves_in_place(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    path = _attach_a_library(s, tmp_path)

    name, saved_path = s.create_via_cell(3, 7, shape="square", size=50)
    assert name == "VIA_Layer3_to_Layer7"
    assert saved_path == path

    lib = s.attached_libraries[0]
    assert name in lib.cells
    cell = lib.cells[name]
    assert {shape.layer for shape in cell.shapes} == {3, 7}
    assert len(cell.shapes) == 2

    # saved to disk in place -- reloading the file shows the new via cell
    from hlayout.io.gdsio import load_gds
    reloaded = load_gds(path)
    assert name in reloaded.cells


def test_create_via_cell_accepts_layer_names_and_round_shape(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.library.layers.set_style(3, name="ROUTE1")
    s.library.layers.set_style(7, name="METAL1")
    _attach_a_library(s, tmp_path)

    name, _ = s.create_via_cell("ROUTE1", "METAL1", shape="round", size=40)
    assert name == "VIA_Layer3_to_Layer7"
    lib = s.attached_libraries[0]
    cell = lib.cells[name]
    from hlayout.core.shapes import Polygon
    assert all(isinstance(shape, Polygon) for shape in cell.shapes)  # circles are polygons


def test_create_via_cell_requires_an_attached_library():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    from hlayout.core.errors import LibraryError
    import pytest
    with pytest.raises(LibraryError):
        s.create_via_cell(3, 7)


def test_create_via_cell_rejects_bad_shape_and_size(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    _attach_a_library(s, tmp_path)
    from hlayout.core.errors import LibraryError
    import pytest
    with pytest.raises(LibraryError):
        s.create_via_cell(3, 7, shape="triangle")
    with pytest.raises(LibraryError):
        s.create_via_cell(3, 7, size=0)


def test_create_via_cell_refuses_duplicate_unless_overwrite(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    _attach_a_library(s, tmp_path)
    from hlayout.core.errors import LibraryError
    import pytest

    s.create_via_cell(3, 7, size=50)
    with pytest.raises(LibraryError):
        s.create_via_cell(3, 7, size=80)

    name, _ = s.create_via_cell(3, 7, size=80, overwrite=True)
    lib = s.attached_libraries[0]
    x1, y1 = lib.cells[name].shapes[0].points[0]
    assert x1 == -40 and y1 == -40  # picked up the new (overwritten) size


def test_create_via_cell_requires_explicit_index_with_two_attached(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    _attach_a_library(s, tmp_path, "parts1.gds")
    _attach_a_library(s, tmp_path, "parts2.gds")
    from hlayout.core.errors import LibraryError
    import pytest

    with pytest.raises(LibraryError):
        s.create_via_cell(3, 7)  # ambiguous -- must pass index

    name, path = s.create_via_cell(3, 7, index=1)
    assert path.endswith("parts2.gds")
    assert name in s.attached_libraries[1].cells
    assert name not in s.attached_libraries[0].cells


def test_via_command_creates_cell_via_command_bar(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    _attach_a_library(s, tmp_path)
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    out = interp.run("VIA 3 7 shape=round size=60")
    assert "created via cell" in out
    assert "VIA_Layer3_to_Layer7" in out
    lib = s.attached_libraries[0]
    assert "VIA_Layer3_to_Layer7" in lib.cells


def test_via_command_rejects_bad_usage(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    _attach_a_library(s, tmp_path)
    console = ScriptConsole(s)
    interp = CommandInterpreter(s, console)

    assert interp.run("VIA").startswith("error:")
    assert interp.run("VIA 3").startswith("error:")
    assert interp.run("VIA 3 7 shape=hexagon").startswith("error:")


def test_drop_via_draws_a_pad_on_both_layers_directly_in_the_cell():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.drop_via(0, 0, 3, 7, size=50, shape="square")
    layers = {shape.layer for shape in s.cell.shapes}
    assert layers == {3, 7}
    assert len(s.cell.shapes) == 2


def test_drop_via_supports_round_shape():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    from hlayout.core.shapes import Polygon
    s.drop_via(10, 20, 0, 1, size=40, shape="round")
    assert all(isinstance(shape, Polygon) for shape in s.cell.shapes)


def test_drop_via_is_a_single_undo_step():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.clear_undo_history()
    s.drop_via(0, 0, 0, 1, size=50)
    assert len(s.cell.shapes) == 2
    assert s.undo() is True
    assert len(s.cell.shapes) == 0
    assert s.undo() is False  # nothing further to undo -- it was one step


def test_drop_via_with_dielectric_window_draws_a_third_shape():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.drop_via(0, 0, 3, 7, size=50, shape="square",
               dielectric_layer=9, dielectric_window=20)
    layers = [shape.layer for shape in s.cell.shapes]
    assert sorted(layers) == [3, 7, 9]
    window = next(sh for sh in s.cell.shapes if sh.layer == 9)
    assert window.bbox() == (-10, -10, 10, 10)


def test_drop_via_without_dielectric_args_draws_no_window_as_before():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.drop_via(0, 0, 3, 7, size=50)
    assert len(s.cell.shapes) == 2
    layers = {shape.layer for shape in s.cell.shapes}
    assert layers == {3, 7}


def test_drop_via_dielectric_window_is_part_of_the_same_undo_step():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.clear_undo_history()
    s.drop_via(0, 0, 0, 1, size=50, dielectric_layer=2, dielectric_window=20)
    assert len(s.cell.shapes) == 3
    assert s.undo() is True
    assert len(s.cell.shapes) == 0
    assert s.undo() is False


def test_drop_via_dielectric_window_respects_round_shape():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    from hlayout.core.shapes import Polygon
    s.drop_via(0, 0, 0, 1, size=50, shape="round", dielectric_layer=2, dielectric_window=20)
    assert all(isinstance(shape, Polygon) for shape in s.cell.shapes)
    assert len(s.cell.shapes) == 3


def test_session_batch_groups_calls_into_one_undo_step():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.clear_undo_history()
    with s.batch():
        s.rect(0, 0, 0, 10, 10)
        s.rect(1, 0, 0, 10, 10)
    assert len(s.cell.shapes) == 2
    assert s.undo() is True
    assert len(s.cell.shapes) == 0


def test_resgen_command_draws_ink_glaze_and_two_pads():
    from hlayout.io.techfile import save_resistor_geometry_rules
    s = Session.new("DEMO")
    s.new_cell("TOP")
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 100.0, "pad_b": 300.0, "space_c": 50.0,
        "resistor_layer": 10, "conductor_layer": 11, "glaze_layer": 12,
    })
    interp = CommandInterpreter(s, ScriptConsole(s))

    out = interp.run("RESGEN 0,0 200 1000")

    assert "generated resistor" in out
    # RESGEN builds each resistor as its own new cell + a single Sref
    # placed in the current cell, rather than raw shapes in-place.
    assert len(s.cell.srefs) == 1
    device_cell = s.library.cells[s.cell.srefs[-1].cell_name]
    assert len(device_cell.shapes) == 4
    layers = {sh.layer for sh in device_cell.shapes}
    assert layers == {10, 11, 12}


def test_resgen_command_accepts_explicit_overrides():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    interp = CommandInterpreter(s, ScriptConsole(s))

    out = interp.run(
        "RESGEN 0,0 200 1000 rotation=90 a=10 b=20 c=5 rlayer=7 clayer=8 glayer=9")

    assert "generated resistor" in out
    sref = s.cell.srefs[-1]
    assert sref.rotation == 90.0
    device_cell = s.library.cells[sref.cell_name]
    layers = {sh.layer for sh in device_cell.shapes}
    assert layers == {7, 8, 9}


def test_resgen_command_ink_option_looks_up_layer_by_sheet_resistance():
    from hlayout.io.techfile import save_resistor_geometry_rules
    s = Session.new("DEMO")
    s.new_cell("TOP")
    save_resistor_geometry_rules(s.library, {
        "overlap_a": 50.0, "pad_b": 100.0, "space_c": 20.0,
        "resistor_layer": 30, "conductor_layer": 31, "glaze_layer": 32,
        "ink_layers": {"1000": 21},
    })
    interp = CommandInterpreter(s, ScriptConsole(s))

    out = interp.run("RESGEN 0,0 100 400 ink=1000")

    assert "generated resistor" in out
    device_cell = s.library.cells[s.cell.srefs[-1].cell_name]
    layers = {sh.layer for sh in device_cell.shapes}
    assert 21 in layers
    assert 30 not in layers


def test_resgen_command_requires_three_positional_args():
    s = Session.new("DEMO")
    s.new_cell("TOP")
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("RESGEN 0,0 200")
    assert "error" in out.lower()
