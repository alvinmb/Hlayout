import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.errors import LayoutError
from hlayout.core.shapes import Path, Text
from hlayout.scripting import Session


def _fresh_session():
    s = Session.new("PINTABLETEST")
    s.new_cell("TOP")
    s.clear_undo_history()
    return s


def _all_texts(cell):
    return [sh.text for sh in cell.shapes if isinstance(sh, Text)]


def _pin_rows(cell):
    """{pin_number: function_text} for every (PIN NUM, PIN FUNCTION)
    pair -- found by matching each digit-only PIN NUM text to the
    nearest PIN FUNCTION text at the same row y, one column over."""
    texts = [sh for sh in cell.shapes if isinstance(sh, Text)]
    by_y = {}
    for sh in texts:
        by_y.setdefault(sh.origin[1], []).append(sh)
    out = {}
    for y, shapes in by_y.items():
        shapes.sort(key=lambda sh: sh.origin[0])
        for i, sh in enumerate(shapes):
            if sh.text.isdigit():
                func = shapes[i + 1].text if i + 1 < len(shapes) else ""
                # only pair up PIN NUM with an immediately-adjacent
                # non-digit function cell (guards against pairing across
                # two side-by-side groups' PIN NUM cells at the same y).
                out[int(sh.text)] = "" if func.isdigit() else func
    return out


# --- Session.generate_pin_function_table() / PINTABLE -----------------------
# Per user direction: a pinout reference table -- one PIN NUM / PIN
# FUNCTION column-pair per group of 10 pins, groups placed side by side,
# handling 20/30/40/50-pin packages. FUNCTION starts blank, filled in by
# hand (or via the functions= mapping) afterward.

def test_pin_function_table_20_pins_makes_two_groups_of_ten():
    s = _fresh_session()
    name = s.generate_pin_function_table("PINTABLE", 62, 20)
    cell = s.library.cells[name]
    rows = _pin_rows(cell)
    assert sorted(rows) == list(range(1, 21))
    assert all(v == "" for v in rows.values())  # blank by default


def test_pin_function_table_30_40_50_pins():
    for n in (30, 40, 50):
        s = _fresh_session()
        name = s.generate_pin_function_table("PINTABLE", 62, n)
        rows = _pin_rows(s.library.cells[name])
        assert sorted(rows) == list(range(1, n + 1))


def test_pin_function_table_rejects_a_pin_count_not_a_multiple_of_pergroup():
    s = _fresh_session()
    try:
        s.generate_pin_function_table("PINTABLE", 62, 25)
        assert False, "expected LayoutError"
    except LayoutError as e:
        assert "pin_count" in str(e)


def test_pin_function_table_rejects_a_non_positive_pin_count():
    s = _fresh_session()
    try:
        s.generate_pin_function_table("PINTABLE", 62, 0)
        assert False, "expected LayoutError"
    except LayoutError:
        pass


def test_pin_function_table_custom_pins_per_group():
    s = _fresh_session()
    name = s.generate_pin_function_table("PINTABLE", 62, 20, pins_per_group=5)
    rows = _pin_rows(s.library.cells[name])
    assert sorted(rows) == list(range(1, 21))
    # 4 groups of 5 -> 4 "PIN #" headers, 4 "PIN FUNCTION" headers.
    texts = _all_texts(s.library.cells[name])
    assert texts.count("PIN #") == 4
    assert texts.count("PIN FUNCTION") == 4


def test_pin_function_table_functions_mapping_fills_in_named_pins():
    s = _fresh_session()
    name = s.generate_pin_function_table("PINTABLE", 62, 20,
                                          functions={1: "VCC", 11: "GND"})
    rows = _pin_rows(s.library.cells[name])
    assert rows[1] == "VCC"
    assert rows[11] == "GND"
    assert rows[2] == ""  # everything else still blank


def test_pin_function_table_requires_a_cell_name():
    s = _fresh_session()
    try:
        s.generate_pin_function_table("", 62, 20)
        assert False, "expected LayoutError"
    except LayoutError:
        pass


def test_pin_function_table_overwrite_defaults_true_and_rebuilds():
    s = _fresh_session()
    name = s.generate_pin_function_table("PINTABLE", 62, 20, functions={1: "VCC"})
    s.generate_pin_function_table("PINTABLE", 62, 20, functions={1: "RESET"})  # no overwrite= passed
    rows = _pin_rows(s.library.cells[name])
    assert rows[1] == "RESET"


def test_pin_function_table_overwrite_false_refuses_an_existing_cell():
    s = _fresh_session()
    s.generate_pin_function_table("PINTABLE", 62, 20)
    try:
        s.generate_pin_function_table("PINTABLE", 62, 20, overwrite=False)
        assert False, "expected LayoutError"
    except LayoutError as e:
        assert "already exists" in str(e)


def test_pin_function_table_title_defaults_on_and_centred():
    s = _fresh_session()
    name = s.generate_pin_function_table("PINTABLE", 62, 20)
    assert "PIN FUNCTION TABLE" in _all_texts(s.library.cells[name])


def test_pin_function_table_title_can_be_omitted():
    s = _fresh_session()
    name = s.generate_pin_function_table("PINTABLE", 62, 20, title=None)
    assert "PIN FUNCTION TABLE" not in _all_texts(s.library.cells[name])


def test_pin_function_table_custom_title_text():
    s = _fresh_session()
    name = s.generate_pin_function_table("PINTABLE", 62, 20, title="20-PIN HYBRID PACKAGE")
    assert "20-PIN HYBRID PACKAGE" in _all_texts(s.library.cells[name])


def test_pin_function_table_groups_sit_edge_to_edge_by_default():
    # Per user direction: no gap between sections by default -- group
    # 1's own left border coincides exactly with group 0's own right
    # border (not offset by any gap), and group 1's own right border
    # sits at exactly 2x a single group's width.
    s = _fresh_session()
    col_widths = (800.0, 2000.0)
    name = s.generate_pin_function_table("PINTABLE", 62, 20, col_widths=col_widths)
    cell = s.library.cells[name]
    lines = [sh for sh in cell.shapes if isinstance(sh, Path)]
    vert = [ln for ln in lines if ln.points[0][0] == ln.points[1][0]]
    x_positions = sorted({ln.points[0][0] for ln in vert})
    group_width = sum(col_widths)
    assert group_width in x_positions          # group 0's right == group 1's left
    assert 2 * group_width in x_positions      # group 1's own right edge


def test_pin_function_table_groups_are_separate_bordered_boxes_with_a_gap():
    # Each group is its OWN mini-table (outer box + one internal
    # divider between PIN #/PIN FUNCTION) with empty space to the
    # next group's own box, when group_gap is explicitly given.
    s = _fresh_session()
    col_widths = (800.0, 2000.0)
    group_gap = 300.0
    name = s.generate_pin_function_table("PINTABLE", 62, 20, col_widths=col_widths,
                                          group_gap=group_gap)
    cell = s.library.cells[name]
    lines = [sh for sh in cell.shapes if isinstance(sh, Path)]
    vert = [ln for ln in lines if ln.points[0][0] == ln.points[1][0]]
    x_positions = sorted({ln.points[0][0] for ln in vert})
    group_width = sum(col_widths)
    # group 0: left=0, mid=800, right=2800; group 1 starts at
    # group_width+group_gap=3100.
    assert 0.0 in x_positions
    assert col_widths[0] in x_positions
    assert group_width in x_positions
    assert (group_width + group_gap) in x_positions


def test_pin_function_table_rejects_row_height_too_small_for_text():
    s = _fresh_session()
    try:
        s.generate_pin_function_table("PINTABLE", 62, 20, row_height=100.0, text_height=150.0)
        assert False, "expected LayoutError"
    except LayoutError as e:
        assert "row_height" in str(e)


def test_pin_function_table_col_widths_must_have_two_entries():
    s = _fresh_session()
    try:
        s.generate_pin_function_table("PINTABLE", 62, 20, col_widths=(800.0,))
        assert False, "expected LayoutError"
    except LayoutError as e:
        assert "col_widths" in str(e)


def test_pin_function_table_line_layer_separate_from_text_layer():
    s = _fresh_session()
    name = s.generate_pin_function_table("PINTABLE", 62, 20, line_layer=10)
    cell = s.library.cells[name]
    assert all(sh.layer == 62 for sh in cell.shapes if isinstance(sh, Text))
    assert all(sh.layer == 10 for sh in cell.shapes if isinstance(sh, Path))


# --- PINTABLE command bar ----------------------------------------------------

def test_pintable_command_bar():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("PINTABLE PINTABLE_TEST 62 20")
    assert "generated pin function table" in out
    assert "PINTABLE_TEST" in s.library.cells


def test_pintable_command_bar_pergroup_option():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    interp.run("PINTABLE PINTABLE_TEST 62 20 pergroup=5")
    texts = _all_texts(s.library.cells["PINTABLE_TEST"])
    assert texts.count("PIN #") == 4


def test_pintable_command_bar_requires_pin_count():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("PINTABLE PINTABLE_TEST 62")
    assert "error" in out.lower()


def test_pintable_command_bar_rejects_non_numeric_pin_count():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("PINTABLE PINTABLE_TEST 62 abc")
    assert "error" in out.lower()
