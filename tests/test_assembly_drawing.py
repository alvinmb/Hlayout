# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.errors import LayoutError
from hlayout.core.library import Library
from hlayout.core.units import mm_to_dbu
from hlayout.gui.commands import CommandInterpreter
from hlayout.io.techfile import save_resistor_list, save_topview_settings
from hlayout.scripting import Session
from hlayout.scripting.assembly import (guess_pin_count, load_assembly_settings,
                                        _group_size, _pick_scale)


def _session(resistors=True):
    """A 30 x 28 mm substrate inside a 20-pin package, with resistors."""
    lib = Library("ASSYTEST")
    pkg = lib.new_cell("20_PIN_pkg")
    pkg.add_rectangle(61, -1000, -1000, 31000, 29000)
    top = lib.new_cell("TOP")
    top.add_rectangle(0, 0, 0, 30000, 28000)
    top.add_rectangle(3, 2000, 2000, 6000, 3000)
    top.add_sref("20_PIN_pkg", (0, 0))
    lib.top_cell_name = "TOP"
    s = Session(lib)
    s.use_cell("TOP")
    save_topview_settings(lib, {"result_layer": 50})
    if resistors:
        save_resistor_list(lib, [
            {"ref_des": "R1", "value": 1000.0, "tolerance": "5%", "power_mw": 50.0,
             "body_width": 1000.0, "selected_ink": 1000.0},
            {"ref_des": "R2", "value": 10000.0, "tolerance": "1%", "power_mw": 50.0,
             "body_width": 1000.0, "selected_ink": 10000.0},
        ])
    return s


def _placed_boxes(lib, cell="ASSEMBLY"):
    out = {}
    for ref in lib.cells[cell].srefs:
        x0, y0, x1, y1 = lib.cell_bbox(ref.cell_name)
        m = ref.magnification
        ox, oy = ref.origin
        out[ref.cell_name] = (ox + x0 * m, oy + y0 * m, ox + x1 * m, oy + y1 * m)
    return out


def _overlap(p, q):
    return not (p[2] <= q[0] or q[2] <= p[0] or p[3] <= q[1] or q[3] <= p[1])


def test_assembly_places_every_part_without_overlap():
    s = _session()
    r = s.generate_assembly_drawing(title="TEST", dwg_no="D1")
    lib = s.library
    assert r["cell"] == "ASSEMBLY" and not r["warnings"]
    boxes = _placed_boxes(lib)
    assert set(boxes) == {"ASSY_SHEET", "ASSY_TOPVIEW", "ASSY_PINTABLE", "ASSY_RESTABLE"}
    sheet = boxes.pop("ASSY_SHEET")
    title_top = mm_to_dbu(10.0 + 45.0)
    items = list(boxes.items())
    for i, (n, b) in enumerate(items):
        # inside the frame, above the title block
        assert b[0] >= sheet[0] and b[2] <= sheet[2] and b[3] <= sheet[3], n
        assert b[1] >= title_top, n
        for m, c in items[i + 1:]:
            assert not _overlap(b, c), (n, m)


def test_assembly_scale_is_standard_and_view_magnified():
    s = _session()
    r = s.generate_assembly_drawing()
    ref = [x for x in s.library.cells["ASSEMBLY"].srefs if x.cell_name == "ASSY_TOPVIEW"][0]
    assert ref.magnification == r["scale"]
    assert r["scale"] == 5.0          # 30 mm design on A3 landscape beside the tables


def test_assembly_explicit_scale_and_label():
    s = _session()
    r = s.generate_assembly_drawing(scale=2.0)
    assert r["scale"] == 2.0
    texts = [sh.text for sh in s.library.cells["ASSEMBLY"].shapes]
    assert any("SCALE 2:1" in t for t in texts)


def test_assembly_settings_are_saved_and_reused():
    s = _session()
    s.generate_assembly_drawing(title="NOTCH", orientation="portrait", size="A3")
    st = load_assembly_settings(s.library)
    assert st["title"] == "NOTCH" and st["orientation"] == "portrait"
    s.generate_assembly_drawing()      # bare rebuild keeps them
    x0, y0, x1, y1 = s.library.cell_bbox("ASSY_SHEET")
    assert (y1 - y0) > (x1 - x0)       # still portrait


def test_assembly_skips_missing_tables_with_warnings():
    s = _session(resistors=False)
    s.library.cells["TOP"].srefs.clear()
    r = s.generate_assembly_drawing()
    assert len(r["warnings"]) == 2
    assert set(_placed_boxes(s.library)) == {"ASSY_SHEET", "ASSY_TOPVIEW"}


def test_assembly_is_one_undo_step():
    s = _session()
    s.clear_undo_history()
    s.generate_assembly_drawing()
    assert "ASSEMBLY" in s.library.cells
    s.undo()
    assert "ASSEMBLY" not in s.library.cells and "ASSY_SHEET" not in s.library.cells


def test_assembly_rejects_unknown_settings():
    s = _session()
    with pytest.raises(LayoutError):
        s.generate_assembly_drawing(bogus=1)


def test_assembly_command():
    s = _session()
    interp = CommandInterpreter(s, console=None)
    out = interp.run('ASSEMBLY title="MY BOARD" dwgno=HL-1 scale=2 layout=bottom')
    assert "generated assembly drawing" in out and "2:1" in out and "bottom" in out
    assert load_assembly_settings(s.library)["dwg_no"] == "HL-1"
    assert interp.run("ASSEMBLY bogus=1").startswith("error")


def test_helpers():
    assert _group_size(20, 10) == 10
    assert _group_size(28, 10) == 7
    assert _group_size(18, 10) == 9
    assert _pick_scale(7.3) == 5.0 and _pick_scale(0.3) == 0.25
    lib = Library("X")
    c = lib.new_cell("TOP")
    lib.new_cell("LCC28")
    c.add_sref("LCC28", (0, 0))
    assert guess_pin_count(lib, "TOP") == 28



# --- Tools > Assembly Drawing... window ---------------------------------------

def _gui_app():
    tk = pytest.importorskip("tkinter")
    from hlayout.gui.app import App
    try:
        s = _session()
        app = App(s.library)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.session.use_cell("TOP")
    app.update()
    return app


def test_assembly_window_builds_from_the_tools_menu_and_saves():
    app = _gui_app()
    try:
        win = app.show_assembly_dialog()
        assert win.vars["layer"].get().startswith("50:")      # Top View result layer
        assert "20 found" in win.pins_hint.cget("text")
        win.vars["title"].set("GUI BOARD")
        win.vars["scale"].set("2:1")
        win.fn_text.insert("end", "1 = VCC\n20 = GND\n")
        win.build()
        lib = app.session.library
        assert "ASSEMBLY" in lib.cells and "ASSY_PINTABLE" in lib.cells
        assert "2:1" in win.msg_var.get() and "ASSEMBLY" in app.status.get()
        assert app.session.current_cell_name == "ASSEMBLY"
        st = load_assembly_settings(lib)
        assert st["title"] == "GUI BOARD" and st["scale"] == 2.0
        assert st["pin_functions"] == {1: "VCC", 20: "GND"}
        assert app.show_assembly_dialog() is win               # one window, reused
        assert win.vars["title"].get() == "GUI BOARD"
    finally:
        app.destroy()


def test_assembly_window_reports_bad_entries():
    app = _gui_app()
    try:
        win = app.show_assembly_dialog()
        win.vars["table_text_mm"].set("abc")
        win.build()
        assert "Table text" in win.msg_var.get()
        win.vars["table_text_mm"].set("2.5")
        win.fn_text.insert("end", "VCC\n")
        win.build()
        assert "line 1" in win.msg_var.get()
        assert "ASSEMBLY" not in app.session.library.cells
    finally:
        app.destroy()


def test_tools_menu_has_assembly_drawing():
    app = _gui_app()
    try:
        tools = dict(app._top_menus)["Tools"]
        labels = [tools.entrycget(i, "label") for i in range(tools.index("end") + 1)
                  if tools.type(i) == "command"]
        assert "Assembly Drawing..." in labels
    finally:
        app.destroy()



def test_vertical_a3_puts_tables_below_the_view():
    s = _session()
    r = s.generate_assembly_drawing(size="A3", orientation="portrait", layout="bottom")
    assert r["layout"] == "bottom"
    b = _placed_boxes(s.library)
    sx0, sy0, sx1, sy1 = b.pop("ASSY_SHEET")
    assert (sy1 - sy0) > (sx1 - sx0)                       # vertical sheet
    view = b.pop("ASSY_TOPVIEW")
    title_top = mm_to_dbu(10.0 + 45.0)
    for name, box in b.items():
        assert box[3] < view[1], name                      # below the view
        assert box[1] >= title_top, name                   # above the title block
    tables = list(b.values())
    assert not _overlap(tables[0], tables[1])
    assert abs(tables[0][3] - tables[1][3]) < 1            # tops aligned in one row


def test_bottom_layout_wraps_tables_onto_rows_when_too_wide():
    s = _session()
    r = s.generate_assembly_drawing(size="A4", orientation="portrait", layout="bottom")
    b = _placed_boxes(s.library)
    pin, res = b["ASSY_PINTABLE"], b["ASSY_RESTABLE"]
    assert not _overlap(pin, res) and res[3] < pin[1]      # resistor table on the row below
    assert all(box[3] < b["ASSY_TOPVIEW"][1] for box in (pin, res))


def test_assembly_window_portrait_switches_tables_below():
    app = _gui_app()
    try:
        win = app.show_assembly_dialog()
        win.vars["orientation"].set("portrait")
        assert win.vars["layout"].get() == "bottom"
        win.vars["orientation"].set("landscape")
        assert win.vars["layout"].get() == "side"
    finally:
        app.destroy()



def test_format_rkm():
    from hlayout.core.units import format_rkm
    cases = {10: "10", 100: "100", 1200: "1K20", 680e3: "680K", 10.4e6: "10M4",
             1000: "1K00", 10e3: "10K0", 100e3: "100K", 4.7: "4R70", 0.47: "R470",
             47.5: "47R5", 2.2e6: "2M20", 999.96: "1K00", 1234: "1K234"}
    for v, want in cases.items():
        assert format_rkm(v) == want, (v, format_rkm(v))


def _row_centring_errors(cell):
    """|gap above text - gap below text| for every data/header Text, using
    the horizontal grid lines directly above and below it."""
    from hlayout.core.shapes import Path, Text
    ys = sorted({ln.points[0][1] for ln in cell.shapes
                 if isinstance(ln, Path) and ln.points[0][1] == ln.points[-1][1]})
    errs = []
    for sh in cell.shapes:
        if not isinstance(sh, Text):
            continue
        y0, y1 = sh.origin[1], sh.origin[1] + sh.height
        above = min(y for y in ys if y >= y1)
        below = max(y for y in ys if y <= y0)
        errs.append(abs((above - y1) - (y0 - below)))
    return errs


def test_table_text_is_vertically_centred_in_rows():
    s = _session()
    s.generate_resistor_table("RT", 62)
    s.generate_pin_function_table("PT", 62, 20)
    for name in ("RT", "PT"):
        errs = _row_centring_errors(s.library.cells[name])
        assert errs and max(errs) <= 1, (name, max(errs))



# --- persistence --------------------------------------------------------------

def test_house_style_is_remembered_for_new_designs_but_title_is_not():
    s = _session()
    s.generate_assembly_drawing(orientation="portrait", layout="bottom", drawn_by="AB",
                                title="BOARD ONE", dwg_no="D-1", table_text_mm=3.0)
    fresh = Library("OTHER")
    st = load_assembly_settings(fresh)
    assert st["orientation"] == "portrait" and st["layout"] == "bottom"
    assert st["drawn_by"] == "AB" and st["table_text_mm"] == 3.0
    assert st["title"] == "" and st["dwg_no"] == ""          # design-only fields


def test_design_settings_beat_house_style():
    s = _session()
    s.generate_assembly_drawing(orientation="landscape")
    other = _session()
    other.generate_assembly_drawing(orientation="portrait")  # house style now portrait
    assert load_assembly_settings(s.library)["orientation"] == "landscape"


def test_assembly_window_saves_fields_as_you_type_and_on_close():
    app = _gui_app()
    try:
        win = app.show_assembly_dialog()
        win.vars["title"].set("TYPED")
        win.vars["drawn_by"].set("ALVIN")
        win.fn_text.insert("end", "3 = OUT\n")
        win.vars["table_text_mm"].set("2.")                    # half-typed: still valid
        win.vars["view_line_mm"].set("x")                      # invalid: keeps last good
        win.close()                                             # no Build
        st = load_assembly_settings(app.session.library)
        assert st["title"] == "TYPED" and st["drawn_by"] == "ALVIN"
        assert st["pin_functions"] == {3: "OUT"} and st["table_text_mm"] == 2.0
        assert st["view_line_mm"] == 0.25
        win = app.show_assembly_dialog()                        # reopened: values back
        assert win.vars["title"].get() == "TYPED"
        assert "3 = OUT" in win.fn_text.get("1.0", "end")
        assert load_assembly_settings(Library("NEW"))["drawn_by"] == "ALVIN"
    finally:
        app.destroy()
