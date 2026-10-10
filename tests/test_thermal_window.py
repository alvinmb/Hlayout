"""Tools > Thermal Analysis... window: opens against the live session,
persists setup edits into the technology parameters, and shows a
solved run as a map plus a parts table."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")
pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("shapely")

from hlayout.io import techfile  # noqa: E402


def _make_app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    try:
        lib = Library("THERMALWIN")
        lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    s = app.session
    s.set_layer_style(10, name="Substrate")
    s.use_cell("TOP")
    s.rect(10, 0, 0, 10000, 6000)
    s.generate_resistor(3000, 3000, body_width=1000, body_length=2000, ref_des="R1",
                        resistor_layer=30)
    s.generate_resistor(7000, 3000, body_width=1000, body_length=2000, ref_des="R2",
                        resistor_layer=30)
    s.use_cell("TOP")
    app.update()
    return app


def test_tools_menu_has_thermal_analysis_item():
    app = _make_app()
    try:
        tools = next(sub for label, sub in app._top_menus if label == "Tools")
        labels = []
        for j in range(tools.index("end") + 1):
            try:
                labels.append(tools.entrycget(j, "label"))
            except tk.TclError:
                pass                                 # separator
        assert "Thermal Analysis..." in labels
    finally:
        app.destroy()


def test_thermal_window_lists_parts_and_stack_and_is_a_singleton():
    from hlayout.gui.thermal_window import open_thermal_window
    app = _make_app()
    try:
        win = open_thermal_window(app)
        win.update()
        assert open_thermal_window(app) is win
        assert set(win.power_table.get_children()) == {"R1", "R2"}
        rows = win.stack_table.get_children()
        assert rows[0] == "substrate" and {"30", "31", "32"} <= set(rows)
        assert win.stack_table.set("30", "role") == "resistor"
    finally:
        app.destroy()


def test_thermal_window_persists_edits_and_shows_a_run():
    from hlayout.gui.thermal_window import open_thermal_window
    app = _make_app()
    try:
        win = open_thermal_window(app)
        win.rules["powers"]["R1"] = 100.0
        win._persist()
        win._refresh_powers()
        assert win.power_table.set("R1", "power") == "100.0"
        assert win.power_table.set("R1", "source") == "thermal"
        win._form_vars[("boundary", "base_temp_c")].set("70")
        win._form_vars[("boundary", "grid_um")].set("250")
        win._apply_form("boundary")
        saved = techfile.load_thermal_rules(app.session.library)
        assert saved["boundary"]["base_temp_c"] == 70.0 and saved["powers"] == {"R1": 100.0}

        win._run()
        win.update()
        assert win.result is not None and win.result.model.base_temp_c == 70.0
        rows = win.part_table.get_children()
        assert rows[0] == "R1"                      # hottest first
        assert float(win.part_table.set("R1", "tmax")) > 70.0
        assert win.part_table.set("R1", "status") == "OK"
        assert win._photo is not None and win.map_canvas.find_withtag("map")
        assert "All parts within limits" in win.status_var.get()

        win.slice_var.set(win.result.slice_names()[0])
        win._draw_map()
        win.part_table.selection_set("R1")
        win._mark_selected_part()
        assert win.map_canvas.find_withtag("mark")
    finally:
        app.destroy()


def test_thermal_window_flags_a_part_over_its_limit():
    from hlayout.gui.thermal_window import open_thermal_window
    app = _make_app()
    try:
        win = open_thermal_window(app)
        win.rules["powers"]["R1"] = 5000.0          # 2.5 W/mm2
        win.rules["boundary"]["grid_um"] = 250.0
        win._persist()
        win._run()
        assert win.part_table.item("R1", "tags") == ("bad",)
        assert win.part_table.set("R1", "status") == "over power density"
        assert "OVER LIMIT" in win.status_var.get()
    finally:
        app.destroy()


def test_thermal_window_is_closed_when_the_library_is_replaced():
    from hlayout.gui.thermal_window import open_thermal_window
    app = _make_app()
    try:
        win = open_thermal_window(app)
        app._close_library_bound_tool_windows()
        assert not win.winfo_exists() and app._thermal_window is None
    finally:
        app.destroy()
