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
from hlayout.core.shapes import Path, Rectangle
from hlayout.gui.commands import CommandInterpreter
from hlayout.io import hybrid_routed as HR
from hlayout.io.techfile import load_and_apply_techfile
from hlayout.scripting import Session

ROOT = os.path.join(os.path.dirname(__file__), "..")
OPAMP = os.path.join(ROOT, "router", "examples", "single-die-op-amp.routed.json")
TECH = os.path.join(ROOT, "hlayout", "examples", "ThickFilm_2Con.json")

pytestmark = pytest.mark.skipif(not os.path.exists(OPAMP), reason="router examples not present")


def _session(lib=None):
    lib = lib or Library("HYB")
    if "TOP" not in lib.cells:
        lib.new_cell("TOP")
    if os.path.exists(TECH):
        load_and_apply_techfile(lib, TECH)
    s = Session(lib)
    st = s.import_routed_hybrid(OPAMP)
    return s, st["cell"]


def _pad(s, cell, pin):
    return next(sh for sh in s.library.cells[cell].shapes
                if isinstance(sh, Rectangle) and getattr(sh, "pin", "") == pin)


def test_parse_and_format_ohms():
    assert HR.parse_ohms("1k") == 1000 and HR.parse_ohms("4k7") == pytest.approx(4700)
    assert HR.parse_ohms("100R") == 100 and HR.parse_ohms("2.2M") == pytest.approx(2.2e6)
    assert HR.parse_ohms(330) == 330 and HR.parse_ohms("bogus") is None
    assert HR.format_ohms(1200) == "1.2 k" and HR.format_ohms(47) == "47"


def test_bond_info_names_the_wire_and_its_loop():
    s, cell = _session()
    info = s.bond_wire_info((4500, 2800), tol=50)
    assert info["wire"] == "W1" and info["net"] == "GND"
    assert info["loop_height_um"] == 150 and info["length_um"] == 650
    assert "loop 150 um" in info["text"] and "W1 GND" in info["text"]
    assert s.bond_wire_info((100, 100), tol=50) is None


def test_move_finger_rebonds_and_checks_rules():
    s, cell = _session()
    pv = s.move_bond_finger((4500, 2500), (4500, 500), dry_run=True)
    assert pv["warnings"] and not pv["on_trace"]          # 2.65 mm > 2.5 mm max
    assert _pad(s, cell, "F1").bbox()[1] == 2400           # dry run changed nothing

    r = s.move_bond_finger((4500, 2500), (4500, 2300))
    assert r["warnings"] == [] and r["on_trace"]
    x0, y0, x1, y1 = _pad(s, cell, "F1").bbox()
    assert ((x0 + x1) / 2, (y0 + y1) / 2) == (4500, 2300)
    bond_layer = s._bond_layer()
    ends = [sh.points for sh in s.library.cells[cell].shapes
            if isinstance(sh, Path) and sh.layer == bond_layer]
    assert any((4500, 2300) in pts for pts in ends)
    row = next(r for r in s.bond_diagram() if r["wire"] == "W1")
    assert (row["second_x_um"], row["second_y_um"], row["length_um"]) == (4500, 2300, 850)
    s.undo()
    row = next(r for r in s.bond_diagram() if r["wire"] == "W1")
    assert row["length_um"] == 650


def test_move_finger_angle_rule():
    s, _cell = _session()
    pv = s.move_bond_finger((4500, 2500), (3200, 2300), dry_run=True)
    assert any("deg off the die-edge normal" in w for w in pv["warnings"])


def test_resize_resistor_value_follows_squares():
    s, _cell = _session()
    info = s.resistor_info("R1")
    assert info["value_ohms"] == 1000 and info["squares"] == pytest.approx(0.75)
    r = s.resize_resistor("R1", length=900)
    assert r["value_ohms"] == pytest.approx(1200) and r["pins_moved_um"] == 75
    body = [sh for sh in s.library.cells["CMP_R1"].shapes if not getattr(sh, "pin", "")]
    assert body[0].bbox() == (-450, -500, 450, 500)
    pin2 = next(sh for sh in s.library.cells["CMP_R1"].shapes if getattr(sh, "pin", "") == "2")
    assert (pin2.bbox()[0] + pin2.bbox()[2]) / 2 == 375 + 75
    assert s.resistor_info("R1")["value_ohms"] == pytest.approx(1200)
    # Rs is kept: a second edit back to the original length gives the original value
    assert s.resize_resistor("R1", length=750)["value_ohms"] == pytest.approx(1000)
    with pytest.raises(LayoutError):
        s.resize_resistor("R9", length=100)


def test_resistor_hit_test():
    s, _cell = _session()
    assert s.resistor_at((8000, 2500))["refdes"] == "R1"
    assert s.resistor_at((100, 100)) is None


def test_commands():
    s, cell = _session()
    s.use_cell(cell)
    interp = CommandInterpreter(s, console=None)
    assert "W1 GND" in interp.run("BONDINFO 4500,2800")
    assert "R = 1.2 k" in interp.run("RESIZE R1 length=900 preview=1")
    assert "Rs 1.33 k/sq" in interp.run("RESIZE R1")
    out = interp.run("MOVEFINGER F1 4500,2300")
    assert "moved" in out and "W1: 0.85 mm" in out


def _mkevent(x, y):
    class _E:
        pass
    e = _E()
    e.x, e.y = x, y
    return e


def test_gui_tools_preview_and_commit():
    tk = pytest.importorskip("tkinter")
    from hlayout.gui.app import App
    lib = Library("HYBGUI")
    lib.new_cell("TOP")
    try:
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    try:
        if os.path.exists(TECH):
            load_and_apply_techfile(app.session.library, TECH)
        cell = app.session.import_routed_hybrid(OPAMP)["cell"]
        app.session.use_cell(cell)
        app.canvas.set_cell(cell)
        app.canvas.snap_enabled = False
        app.canvas.fit_view()
        app.update()
        cv = app.canvas
        statuses = []
        cv.on_status = statuses.append
        # hover a wire
        cv.set_mode("select")
        cv._on_motion(_mkevent(*cv.to_px(4500, 2800)))
        assert any("W1 GND" in m for m in statuses), (statuses, cv.to_dbu(*cv.to_px(4500, 2800)),
                                                      cv.cell_name, cv.draw_mode)
        # move a finger with the mouse
        modes = []
        cv.on_mode_status = modes.append
        app._move_finger_tool()
        cv._on_click(_mkevent(*cv.to_px(4500, 2500)))
        cv._update_preview(4500, 2300)
        assert any("Finger F1" in m and "OK" in m for m in modes)
        cv._on_click(_mkevent(*cv.to_px(4500, 2300)))
        x0, y0, x1, y1 = _pad(app.session, cell, "F1").bbox()
        assert abs((y0 + y1) / 2 - 2300) <= 2 / max(cv.scale, 1e-9) + 2
        # resize a resistor with the mouse
        app._resize_resistor_tool()
        cv._on_click(_mkevent(*cv.to_px(8000, 2500)))
        cv._update_preview(8450, 2500)
        assert any("R = 1.2 k" in m for m in modes)
        cv._on_click(_mkevent(*cv.to_px(8450, 2500)))
        assert app.session.resistor_info("R1")["length_um"] == pytest.approx(900, abs=20)
    finally:
        app.destroy()
