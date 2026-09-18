import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.errors import LayoutError
from hlayout.core.shapes import Path, Text
from hlayout.core.units import mm_to_dbu
from hlayout.scripting import Session


def _fresh_session():
    s = Session.new("SHEETTEST")
    s.new_cell("TOP")
    s.clear_undo_history()
    return s


def _texts(cell):
    return {sh.text for sh in cell.shapes if isinstance(sh, Text)}


# --- Session.generate_drawing_sheet() / SHEET --------------------------------
# Per user direction: an A3 portrait drawing sheet with a standard title
# block on the bottom, including drawn by, checked by and approved by
# (each with its own date), drawing number, and issue number.

# the drawn border is the FRAME (inset by margin_mm from the raw sheet
# edge on all 4 sides), not the raw sheet size itself -- the sheet edge
# is never drawn (see generate_drawing_sheet()'s own docstring).
_DEFAULT_MARGIN_MM = 10.0


def test_drawing_sheet_default_is_a3_portrait():
    s = _fresh_session()
    name = s.generate_drawing_sheet("SHEET1", 62)
    cell = s.library.cells[name]
    x0, y0, x1, y1 = cell.bbox()
    frame_w = mm_to_dbu(297.0 - 2 * _DEFAULT_MARGIN_MM)
    frame_h = mm_to_dbu(420.0 - 2 * _DEFAULT_MARGIN_MM)
    assert abs((x1 - x0) - frame_w) < mm_to_dbu(1.0)
    assert abs((y1 - y0) - frame_h) < mm_to_dbu(1.0)


def test_drawing_sheet_landscape_swaps_width_and_height():
    s = _fresh_session()
    name = s.generate_drawing_sheet("SHEET1", 62, orientation="landscape")
    cell = s.library.cells[name]
    x0, y0, x1, y1 = cell.bbox()
    frame_w = mm_to_dbu(420.0 - 2 * _DEFAULT_MARGIN_MM)
    frame_h = mm_to_dbu(297.0 - 2 * _DEFAULT_MARGIN_MM)
    assert abs((x1 - x0) - frame_w) < mm_to_dbu(1.0)
    assert abs((y1 - y0) - frame_h) < mm_to_dbu(1.0)


def test_drawing_sheet_a4_size():
    s = _fresh_session()
    name = s.generate_drawing_sheet("SHEET1", 62, size="a4")
    cell = s.library.cells[name]
    x0, y0, x1, y1 = cell.bbox()
    frame_w = mm_to_dbu(210.0 - 2 * _DEFAULT_MARGIN_MM)
    frame_h = mm_to_dbu(297.0 - 2 * _DEFAULT_MARGIN_MM)
    assert abs((x1 - x0) - frame_w) < mm_to_dbu(1.0)
    assert abs((y1 - y0) - frame_h) < mm_to_dbu(1.0)


def test_drawing_sheet_rejects_an_unrecognised_size():
    s = _fresh_session()
    try:
        s.generate_drawing_sheet("SHEET1", 62, size="A2")
        assert False, "expected LayoutError"
    except LayoutError as e:
        assert "size" in str(e)


def test_drawing_sheet_rejects_an_unrecognised_orientation():
    s = _fresh_session()
    try:
        s.generate_drawing_sheet("SHEET1", 62, orientation="sideways")
        assert False, "expected LayoutError"
    except LayoutError as e:
        assert "orientation" in str(e)


def test_drawing_sheet_requires_a_cell_name():
    s = _fresh_session()
    try:
        s.generate_drawing_sheet("", 62)
        assert False, "expected LayoutError"
    except LayoutError:
        pass


def test_drawing_sheet_title_block_fields_blank_by_default():
    s = _fresh_session()
    name = s.generate_drawing_sheet("SHEET1", 62)
    texts = _texts(s.library.cells[name])
    assert "TITLE:" in texts
    assert "DWG NO:" in texts
    assert "ISSUE:" in texts
    assert "DRAWN BY:" in texts
    assert "CHECKED BY:" in texts
    assert "APPROVED BY:" in texts
    # each of the 3 signature rows has its own blank DATE field
    all_texts = [sh.text for sh in s.library.cells[name].shapes if isinstance(sh, Text)]
    assert all_texts.count("DATE:") == 3


def test_drawing_sheet_fields_can_be_prefilled():
    s = _fresh_session()
    name = s.generate_drawing_sheet(
        "SHEET1", 62,
        title="NOTCH FILTER ASSEMBLY", dwg_no="DWG-001", issue="B",
        drawn_by="A. BROWN", drawn_date="2026-09-18",
        checked_by="J. SMITH", checked_date="2026-09-19",
        approved_by="R. LEE", approved_date="2026-09-20",
    )
    texts = _texts(s.library.cells[name])
    assert "TITLE: NOTCH FILTER ASSEMBLY" in texts
    assert "DWG NO: DWG-001" in texts
    assert "ISSUE: B" in texts
    assert "DRAWN BY: A. BROWN" in texts
    assert "DATE: 2026-09-18" in texts
    assert "CHECKED BY: J. SMITH" in texts
    assert "DATE: 2026-09-19" in texts
    assert "APPROVED BY: R. LEE" in texts
    assert "DATE: 2026-09-20" in texts


def test_drawing_sheet_overwrite_defaults_true_and_rebuilds():
    s = _fresh_session()
    name = s.generate_drawing_sheet("SHEET1", 62, drawn_by="FIRST")
    s.generate_drawing_sheet("SHEET1", 62, drawn_by="SECOND")  # no overwrite= passed
    texts = _texts(s.library.cells[name])
    assert "DRAWN BY: SECOND" in texts
    assert "DRAWN BY: FIRST" not in texts


def test_drawing_sheet_overwrite_false_refuses_an_existing_cell():
    s = _fresh_session()
    s.generate_drawing_sheet("SHEET1", 62)
    try:
        s.generate_drawing_sheet("SHEET1", 62, overwrite=False)
        assert False, "expected LayoutError"
    except LayoutError as e:
        assert "already exists" in str(e)


def test_drawing_sheet_rejects_a_margin_too_large_for_the_sheet():
    s = _fresh_session()
    try:
        s.generate_drawing_sheet("SHEET1", 62, margin_mm=200.0)
        assert False, "expected LayoutError"
    except LayoutError as e:
        assert "margin_mm" in str(e)


def test_drawing_sheet_rejects_a_title_block_leaving_no_drawing_area():
    s = _fresh_session()
    try:
        s.generate_drawing_sheet("SHEET1", 62, title_block_height_mm=500.0)
        assert False, "expected LayoutError"
    except LayoutError as e:
        assert "title_block_height_mm" in str(e)


def test_drawing_sheet_rejects_a_title_block_too_short_for_the_text():
    s = _fresh_session()
    try:
        s.generate_drawing_sheet("SHEET1", 62, title_block_height_mm=5.0, text_height_mm=3.0)
        assert False, "expected LayoutError"
    except LayoutError as e:
        assert "title_block_height_mm" in str(e)


def test_drawing_sheet_line_layer_separate_from_text_layer():
    s = _fresh_session()
    name = s.generate_drawing_sheet("SHEET1", 62, line_layer=10)
    cell = s.library.cells[name]
    assert all(sh.layer == 62 for sh in cell.shapes if isinstance(sh, Text))
    assert all(sh.layer == 10 for sh in cell.shapes if isinstance(sh, Path))


def test_drawing_sheet_border_is_a_closed_rectangle():
    s = _fresh_session()
    name = s.generate_drawing_sheet("SHEET1", 62)
    cell = s.library.cells[name]
    lines = [sh for sh in cell.shapes if isinstance(sh, Path)]
    vert = [ln for ln in lines if ln.points[0][0] == ln.points[1][0]]
    x_positions = sorted({ln.points[0][0] for ln in vert})
    # outer left/right edges plus the name/date column divider
    assert len(x_positions) == 3


# --- SHEET command bar --------------------------------------------------------

def test_sheet_command_bar():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run('SHEET SHEET_TEST 62 title="NOTCH FILTER" drawnby="A. BROWN"')
    assert "generated drawing sheet" in out
    assert "SHEET_TEST" in s.library.cells
    texts = _texts(s.library.cells["SHEET_TEST"])
    assert "TITLE: NOTCH FILTER" in texts
    assert "DRAWN BY: A. BROWN" in texts


def test_sheet_command_bar_size_and_orientation_options():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    interp.run("SHEET SHEET_TEST 62 size=A4 orientation=landscape")
    x0, y0, x1, y1 = s.library.cells["SHEET_TEST"].bbox()
    frame_w = mm_to_dbu(297.0 - 2 * _DEFAULT_MARGIN_MM)
    frame_h = mm_to_dbu(210.0 - 2 * _DEFAULT_MARGIN_MM)
    assert abs((x1 - x0) - frame_w) < mm_to_dbu(1.0)
    assert abs((y1 - y0) - frame_h) < mm_to_dbu(1.0)


def test_sheet_command_bar_requires_a_layer():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("SHEET SHEET_TEST")
    assert "error" in out.lower() or "usage" in out.lower()


def test_sheet_command_bar_rejects_unknown_option():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _fresh_session()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("SHEET SHEET_TEST 62 bogus=1")
    assert "error" in out.lower()
