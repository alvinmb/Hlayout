import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core.errors import LayoutError
from hlayout.core.library import Library
from hlayout.io.printio import (
    PRINT_TARGET_MM,
    _auto_scale,
    render_cell_svg,
    send_to_system_printer,
    write_print_html,
)
from hlayout.scripting import Session


def _design():
    s = Session.new("PRINTTEST")
    top = s.new_cell("TOP")
    top.add_rectangle(0, 0, 0, 1000, 1000)
    top.add_path(1, [(0, 0), (2000, 0)], width=100, cap="round")
    return s


# --- render_cell_svg() ------------------------------------------------

def test_render_cell_svg_includes_only_visible_layers():
    s = _design()
    s.library.layers.set_style(1, visible=False)
    svg, layer_order, _scale = render_cell_svg(s.library, "TOP", scale=1.0)
    assert layer_order == [0]
    assert 'id="layer-1"' not in svg
    assert 'id="layer-0"' in svg


def test_render_cell_svg_sizes_to_true_physical_scale_when_forced():
    from hlayout.core.units import DBU_MM
    s = _design()
    svg, _layers, scale_used = render_cell_svg(s.library, "TOP", margin_dbu=0, scale=1.0)
    # bbox is (0,0)-(2000,1000) DBU with margin_dbu=0 -> exact mm size
    assert scale_used == 1.0
    assert f'width="{2000 * DBU_MM:.3f}mm"' in svg
    assert f'height="{1000 * DBU_MM:.3f}mm"' in svg


def test_render_cell_svg_flattens_a_placed_instance():
    s = _design()
    s.new_cell("CHILD")
    s.rect(2, 0, 0, 500, 500)
    s.use_cell("TOP")
    s.sref("CHILD", (5000, 5000))
    svg, layer_order, _scale = render_cell_svg(s.library, "TOP", scale=1.0)
    assert 2 in layer_order
    # The combined bbox (TOP's own 1000x1000 rect + CHILD's 500x500,
    # placed out at (5000,5000)) is far bigger than either alone --
    # proof the instance's offset actually reached the flattened
    # geometry, not just that layer 2 was seen at all.
    assert 'width="9.500mm"' in svg and 'height="9.500mm"' in svg


def test_render_cell_svg_empty_cell_does_not_raise():
    s = Session.new("EMPTY")
    s.new_cell("BLANK")
    svg, layer_order, _scale = render_cell_svg(s.library, "BLANK")
    assert layer_order == []
    assert "<svg" in svg


# --- Auto-scale-up for print ("100%" of a tiny part is a speck) -------

def test_auto_scale_leaves_large_designs_at_1x():
    assert _auto_scale(PRINT_TARGET_MM, PRINT_TARGET_MM) == 1.0
    assert _auto_scale(500.0, 20.0) == 1.0     # only the LARGER dimension matters


def test_auto_scale_picks_the_smallest_clean_factor_that_reaches_the_target():
    # A 2mm-wide part needs >=75x to reach PRINT_TARGET_MM (150mm) --
    # the next clean factor at or above that is 100x, not some exact
    # decimal that happens to land precisely on 150mm.
    assert _auto_scale(2.0, 1.0) == 100.0


def test_render_cell_svg_auto_scales_a_small_design_up():
    s = _design()   # 2mm x 1mm -- well under PRINT_TARGET_MM
    svg, _layers, scale_used = render_cell_svg(s.library, "TOP", margin_dbu=0)
    assert scale_used > 1.0
    from hlayout.core.units import DBU_MM
    expected_width_mm = 2000 * DBU_MM * scale_used
    assert f'width="{expected_width_mm:.3f}mm"' in svg
    # the viewBox itself stays in raw (unscaled) DBU -- the browser
    # stretches to the bigger physical width/height above, so the
    # drawing's own coordinates never need touching for this.
    assert 'viewBox="0 0 2000 1000"' in svg


def test_render_cell_svg_scale_argument_overrides_auto():
    s = _design()
    svg, _layers, scale_used = render_cell_svg(s.library, "TOP", margin_dbu=0, scale=3.0)
    assert scale_used == 3.0
    from hlayout.core.units import DBU_MM
    assert f'width="{2000 * DBU_MM * 3.0:.3f}mm"' in svg


# --- Outline-only layers stay visible at print scale (a package body /
# component outline is routinely drawn with Fill unchecked) ------------

def test_outline_only_polygon_gets_a_physical_hairline_not_1_dbu():
    from hlayout.io.printio import _hairline_dbu

    s = _design()
    s.library.layers.set_style(5, fill=False)
    s.rect(5, 0, 0, 20000, 20000)   # a 20x20mm outline-only package body
    svg, layer_order, scale_used = render_cell_svg(s.library, "TOP", margin_dbu=0, scale=1.0)
    assert 5 in layer_order
    assert scale_used == 1.0
    expected = _hairline_dbu(1.0)
    assert expected == 150.0   # 0.15mm hairline / 0.001 mm-per-DBU
    assert f'stroke-width="{expected:.4f}"' in svg
    # the old bug: a literal 1-DBU (1 micron) stroke -- present in the
    # geometry, invisible at any real scale, not merely faint.
    assert 'stroke-width="1"/>' not in svg


def test_outline_only_polygon_hairline_shrinks_with_scale():
    from hlayout.io.printio import _hairline_dbu

    s = _design()
    s.library.layers.set_style(5, fill=False)
    s.rect(5, 0, 0, 500, 500)   # tiny -- auto-scales up
    svg, _layers, scale_used = render_cell_svg(s.library, "TOP", margin_dbu=0)
    assert scale_used > 1.0
    expected = _hairline_dbu(scale_used)
    assert f'stroke-width="{expected:.4f}"' in svg


def test_thin_path_is_not_thinner_than_the_hairline_floor():
    from hlayout.io.printio import _hairline_dbu

    s = Session.new("THINPATH")
    s.new_cell("TOP")
    s.path(1, [(0, 0), (20000, 0)], width=1, cap="round")   # 1 DBU -- essentially zero
    svg, _layers, scale_used = render_cell_svg(s.library, "TOP", margin_dbu=0, scale=1.0)
    assert f'stroke-width="{_hairline_dbu(scale_used):.4f}"' in svg


# --- write_print_html() ------------------------------------------------

def test_write_print_html_writes_a_file_with_a_legend(tmp_path):
    s = _design()
    out = str(tmp_path / "out.html")
    path = write_print_html(s.library, "TOP", out)
    assert path == out
    text = open(path, encoding="utf-8").read()
    assert "<svg" in text
    assert "TOP" in text
    assert "0: " in text or "0:" in text  # layer legend entry for layer 0


def test_write_print_html_defaults_to_a_temp_file():
    s = _design()
    path = write_print_html(s.library, "TOP")
    try:
        assert os.path.exists(path)
        assert path.endswith(".html")
    finally:
        os.remove(path)


def test_write_print_html_shows_the_scale_used_on_the_page(tmp_path):
    s = _design()   # small -- auto-scales up
    out = str(tmp_path / "auto.html")
    write_print_html(s.library, "TOP", out)
    text = open(out, encoding="utf-8").read()
    assert "Scale: 50x actual size." in text

    out2 = str(tmp_path / "forced.html")
    write_print_html(s.library, "TOP", out2, scale=1.0)
    text2 = open(out2, encoding="utf-8").read()
    assert "Scale: 1:1 (true physical size)." in text2


# --- Session.print_cell() ----------------------------------------------

def test_print_cell_defaults_to_current_cell(tmp_path):
    s = _design()
    out = str(tmp_path / "p.html")
    path = s.print_cell(out_path=out)
    assert path == out
    assert os.path.exists(out)


def test_print_cell_passes_scale_through(tmp_path):
    s = _design()
    out = str(tmp_path / "p2.html")
    s.print_cell(out_path=out, scale=4.0)
    assert "Scale: 4x actual size." in open(out, encoding="utf-8").read()


def test_print_cell_raises_with_no_current_cell():
    s = Session.new("X")
    with pytest.raises(LayoutError):
        s.print_cell()


# --- PRINT command bar command -------------------------------------------

def test_print_command_writes_a_file_and_reports_the_outcome(tmp_path, monkeypatch):
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole

    s = _design()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = str(tmp_path / "cmd.html")

    monkeypatch.setattr("hlayout.io.printio.send_to_system_printer", lambda p: True)
    result = interp.run(f"PRINT out={out}")
    assert "sent to the system printer" in result
    assert os.path.exists(out)


def test_print_command_falls_back_to_opening_when_no_print_handoff(tmp_path, monkeypatch):
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole

    s = _design()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = str(tmp_path / "cmd2.html")

    monkeypatch.setattr("hlayout.io.printio.send_to_system_printer", lambda p: False)
    opened = []
    monkeypatch.setattr("webbrowser.open", lambda p: opened.append(p))
    result = interp.run(f"PRINT out={out}")
    assert "Ctrl+P" in result
    assert opened == [out]


def test_print_command_rejects_unknown_options():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole

    s = _design()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("PRINT bogus=1")
    assert out.startswith("error:")


def test_print_command_scale_option(tmp_path, monkeypatch):
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole

    s = _design()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = str(tmp_path / "scaled.html")
    monkeypatch.setattr("hlayout.io.printio.send_to_system_printer", lambda p: True)
    interp.run(f"PRINT out={out} scale=8")
    assert "Scale: 8x actual size." in open(out, encoding="utf-8").read()


def test_print_command_rejects_a_non_numeric_scale():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole

    s = _design()
    interp = CommandInterpreter(s, ScriptConsole(s))
    assert interp.run("PRINT scale=big").startswith("error:")


def test_print_command_rejects_a_non_positive_scale():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole

    s = _design()
    interp = CommandInterpreter(s, ScriptConsole(s))
    assert interp.run("PRINT scale=0").startswith("error:")
    assert interp.run("PRINT scale=-2").startswith("error:")


# --- send_to_system_printer() -------------------------------------------

def test_send_to_system_printer_false_when_startfile_unavailable(monkeypatch, tmp_path):
    import hlayout.io.printio as printio_mod
    out = str(tmp_path / "x.html")
    open(out, "w").close()
    monkeypatch.delattr(os, "startfile", raising=False)
    assert printio_mod.send_to_system_printer(out) is False


def test_send_to_system_printer_true_when_startfile_succeeds(monkeypatch, tmp_path):
    import hlayout.io.printio as printio_mod
    out = str(tmp_path / "x.html")
    open(out, "w").close()
    monkeypatch.setattr(os, "startfile", lambda p, verb: None, raising=False)
    assert printio_mod.send_to_system_printer(out) is True


# --- App.print_current_view() -------------------------------------------

def _make_app():
    tk = pytest.importorskip("tkinter")
    from hlayout.gui.app import App
    lib = Library("PRINTAPP")
    lib.new_cell("TOP")
    try:
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    return app


def test_print_current_view_uses_the_system_print_handoff(monkeypatch):
    from hlayout.gui import app as app_module
    app = _make_app()
    try:
        app.session.rect(0, 0, 0, 100, 100)
        sent = []
        monkeypatch.setattr(app_module, "send_to_system_printer",
                             lambda p: sent.append(p) or True)
        app.print_current_view()
        assert len(sent) == 1
        assert os.path.exists(sent[0])
        assert "sent to the system printer" in app.status.get()
    finally:
        app.destroy()


def test_print_current_view_falls_back_to_the_browser(monkeypatch):
    from hlayout.gui import app as app_module
    app = _make_app()
    try:
        app.session.rect(0, 0, 0, 100, 100)
        monkeypatch.setattr(app_module, "send_to_system_printer", lambda p: False)
        opened = []
        monkeypatch.setattr(app_module.webbrowser, "open", lambda p: opened.append(p))
        app.print_current_view()
        assert len(opened) == 1
        assert "Ctrl+P" in app.status.get()
    finally:
        app.destroy()
