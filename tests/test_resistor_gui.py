import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")

from hlayout.core.library import Library
from hlayout.core.shapes import Rectangle, Text
from hlayout.io.techfile import load_resistor_geometry_rules


def _make_app():
    from hlayout.gui.app import App
    lib = Library("RESGUITEST")
    lib.new_cell("TOP")
    try:
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    return app


def _find_toplevels(widget):
    found = []
    for w in widget.winfo_children():
        if isinstance(w, tk.Toplevel):
            found.append(w)
        found.extend(_find_toplevels(w))
    return found


def _seed(win, specs=(("R1", 1000, "5%", 125.0), ("R2", 10000, "1%", 62.5),
                       ("R3", 470, "5%", 200.0))):
    """The Resistor Designer opens with an EMPTY table now (the old
    hard-coded R1..R8 demo set was removed). Tests that need a few sized
    resistors to work with add them explicitly via this helper."""
    from hlayout.gui.resistor_designer import Resistor, width_for_power
    for ref, val, tol, pwr in specs:
        r = Resistor(ref_des=ref, value=val, tolerance=tol,
                     power_mw=pwr, body_width=float(width_for_power(pwr)))
        r.compute_solutions(win.rules)
        win.resistors.append(r)
    win._refresh_all()


def test_resistor_list_persists_across_reopening_the_designer():
    from hlayout.gui.resistor_designer import open_resistor_designer
    from hlayout.io.techfile import load_resistor_list

    app = _make_app()
    try:
        win = open_resistor_designer(app)
        _seed(win)                                   # 3 resistors, sized
        win.res_table.select_row(0)
        app.update()
        win._on_solution_chosen(0)                   # pick a solution for R1
        picked_ink = win.resistors[0].selected.ink_rsh

        # the list is now saved into the design's tech_params
        saved = load_resistor_list(app.session.library)
        assert [r["ref_des"] for r in saved] == ["R1", "R2", "R3"]
        assert saved[0]["selected_ink"] == picked_ink

        win.destroy()
        app._resistor_designer_window = None         # force a fresh window

        win2 = open_resistor_designer(app)
        app.update()
        assert [r.ref_des for r in win2.resistors] == ["R1", "R2", "R3"]
        assert win2.resistors[0].value == 1000
        assert win2.resistors[0].selected is not None
        assert win2.resistors[0].selected.ink_rsh == picked_ink
    finally:
        app.destroy()


def test_resistor_list_survives_save_then_reopen_via_the_file_menu(tmp_path):
    from unittest import mock
    from hlayout.gui.resistor_designer import open_resistor_designer

    app = _make_app()
    try:
        win = open_resistor_designer(app)
        _seed(win)
        win.res_table.select_row(0)
        app.update()
        win._on_solution_chosen(0)

        path = str(tmp_path / "rd.gds")
        app.current_path = path
        app.save_gds()

        # File > Open GDS the same design, with the designer left open --
        # the stale window (bound to the now-discarded library) must be
        # torn down so the next open reloads from the reopened design.
        assert win.winfo_exists()
        with mock.patch("hlayout.gui.app.filedialog.askopenfilename", return_value=path):
            app.open_gds()
        assert not win.winfo_exists()
        assert getattr(app, "_resistor_designer_window", None) is None

        win2 = open_resistor_designer(app)
        app.update()
        assert [r.ref_des for r in win2.resistors] == ["R1", "R2", "R3"]
        assert win2.resistors[0].selected is not None
    finally:
        app.destroy()


def test_new_design_closes_the_open_resistor_designer(tmp_path):
    from hlayout.gui.resistor_designer import open_resistor_designer

    app = _make_app()
    try:
        win = open_resistor_designer(app)
        _seed(win)
        assert win.winfo_exists()
        app.new_library()
        assert not win.winfo_exists()
        # a fresh designer against the new (empty) design opens empty
        win2 = open_resistor_designer(app)
        app.update()
        assert win2.resistors == []
    finally:
        app.destroy()


def test_show_resistor_geometry_dialog_saves_rules_on_return():
    # show_resistor_geometry_dialog() just creates a non-modal-to-Python
    # Toplevel and returns immediately (same pattern as
    # show_route_layers_dialog()) -- no nested wait_window() loop, so no
    # app.after() polling trick is needed here.
    app = _make_app()
    try:
        app.show_resistor_geometry_dialog()
        tops = _find_toplevels(app)
        assert tops
        win = tops[0]
        win.focus_force()
        app.update()
        win.event_generate("<Return>")
        app.update()

        rules = load_resistor_geometry_rules(app.session.library)
        assert rules["overlap_a"] >= 0
        assert not tops[0].winfo_exists()  # OK closed the dialog
    finally:
        app.destroy()


def test_resistor_designer_place_in_layout_draws_resistor():
    app = _make_app()
    try:
        from hlayout.gui.resistor_designer import open_resistor_designer, PlaceResistorDialog

        win = open_resistor_designer(app)
        _seed(win)
        app.update()
        # Use the first seeded resistor -- it already has a solution selected.
        assert win.resistors
        win.res_table.select_row(0)
        app.update()
        res = win.resistors[0]
        assert res.selected is not None

        seen = {"opened": False}

        def drive_place_dialog():
            tops = [w for w in _find_toplevels(win) if isinstance(w, PlaceResistorDialog)]
            if not tops:
                app.after(20, drive_place_dialog)
                return
            dlg = tops[0]
            seen["opened"] = True
            dlg.x_var.set(1.0)  # mm -- 1000 DBU
            dlg.y_var.set(0.5)  # mm -- 500 DBU
            dlg.rot_var.set("0")
            dlg._accept()

        app.after(20, drive_place_dialog)
        win._place_in_layout()
        assert seen["opened"] is True

        # generate_resistor() builds each resistor as its own new cell,
        # placed via a single Sref in the parent cell -- resolve that
        # before inspecting shapes.
        cell = app.session.cell
        assert len(cell.srefs) == 1
        sref = cell.srefs[0]
        device_cell = app.session.library.cells[sref.cell_name]
        assert len(device_cell.shapes) == 4  # ink + glaze + 2 pads (ref-des is the instance label)
        rules = load_resistor_geometry_rules(app.session.library)
        ink = next(sh for sh in device_cell.shapes if isinstance(sh, Rectangle)
                   and sh.layer == rules["resistor_layer"])
        glaze = next(sh for sh in device_cell.shapes if isinstance(sh, Rectangle)
                     and sh.layer == rules["glaze_layer"])
        gx0, gy0, gx1, gy1 = glaze.bbox()
        ix0, iy0, ix1, iy1 = ink.bbox()
        d = rules["glaze_oversize"]
        assert (gx0, gy0, gx1, gy1) == (ix0 - d, iy0 - d, ix1 + d, iy1 + d)
        assert not any(isinstance(sh, Text) for sh in device_cell.shapes)
        assert sref.label == res.ref_des
        # The device cell's own ink is drawn at local origin; the Sref
        # placement in the parent cell carries the requested (1000, 500).
        assert sref.origin == (1000, 500)
        x0, y0, x1, y1 = ink.bbox()
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        assert abs(cx - 0) < 1e-6
        assert abs(cy - 0) < 1e-6
    finally:
        app.destroy()


def test_place_in_layout_matches_ink_layer_to_sheet_resistance():
    app = _make_app()
    try:
        from hlayout.gui.resistor_designer import open_resistor_designer, PlaceResistorDialog
        from hlayout.io.techfile import save_resistor_geometry_rules

        win = open_resistor_designer(app)
        _seed(win)
        app.update()
        win.res_table.select_row(0)
        app.update()
        res = win.resistors[0]
        sol = res.selected
        assert sol is not None

        save_resistor_geometry_rules(app.session.library, {
            "overlap_a": 200, "pad_b": 500, "space_c": 200,
            "resistor_layer": 30, "conductor_layer": 31, "glaze_layer": 32,
            "ink_layers": {str(sol.ink_rsh): 25},
        })

        def drive_place_dialog():
            tops = [w for w in _find_toplevels(win) if isinstance(w, PlaceResistorDialog)]
            if not tops:
                app.after(20, drive_place_dialog)
                return
            tops[0]._accept()

        app.after(20, drive_place_dialog)
        win._place_in_layout()

        device_cell = app.session.library.cells[app.session.cell.srefs[-1].cell_name]
        layers = {sh.layer for sh in device_cell.shapes}
        assert 25 in layers
        assert 30 not in layers
    finally:
        app.destroy()


def test_place_in_layout_conductor_layer_can_be_overridden_per_placement():
    app = _make_app()
    try:
        from hlayout.gui.resistor_designer import open_resistor_designer, PlaceResistorDialog

        win = open_resistor_designer(app)
        _seed(win)
        app.update()
        win.res_table.select_row(0)
        app.update()

        def drive_place_dialog():
            tops = [w for w in _find_toplevels(win) if isinstance(w, PlaceResistorDialog)]
            if not tops:
                app.after(20, drive_place_dialog)
                return
            dlg = tops[0]
            dlg.conductor_layer_var.set(app._layer_label(9))
            dlg._accept()

        app.after(20, drive_place_dialog)
        win._place_in_layout()

        rules = load_resistor_geometry_rules(app.session.library)
        device_cell = app.session.library.cells[app.session.cell.srefs[-1].cell_name]
        pads = [sh for sh in device_cell.shapes if isinstance(sh, Rectangle) and sh.layer == 9]
        assert len(pads) == 2
        assert rules["conductor_layer"] != 9  # confirms this was a per-placement override
    finally:
        app.destroy()


def test_place_in_layout_without_a_solution_shows_info_and_draws_nothing(monkeypatch):
    app = _make_app()
    try:
        from hlayout.gui.resistor_designer import open_resistor_designer, Resistor

        win = open_resistor_designer(app)
        # Add a resistor with an impossible aim value so it has no solution.
        r = Resistor(ref_des="RX", value=1e15, tolerance="5%", power_mw=1.0, body_width=1000.0)
        r.compute_solutions(win.rules)
        assert r.selected is None
        win.resistors.append(r)
        win._refresh_all()
        win.res_table.select_row(len(win.resistors) - 1)
        app.update()

        shown = []
        monkeypatch.setattr(
            "hlayout.gui.resistor_designer.dialogs.show_info",
            lambda title, msg, **kw: shown.append((title, msg)),
        )
        win._place_in_layout()
        assert shown
        assert len(app.session.cell.shapes) == 0
    finally:
        app.destroy()


def test_design_rules_dialog_saves_per_ink_layer_choice():
    app = _make_app()
    try:
        from hlayout.gui.resistor_designer import DesignRules, DesignRulesDialog

        lib = app.session.library
        lib.layers.set_style(22, name="RES1K")
        rules = DesignRules.from_library(lib)
        dlg = DesignRulesDialog(app, rules, library=lib)
        # Ink 1000 (1kOhm/sq) is one of the default available inks.
        dlg.ink_layer_vars[1000].set("22: RES1K")
        dlg._accept()

        from hlayout.io.techfile import load_resistor_geometry_rules
        saved = load_resistor_geometry_rules(lib)
        assert saved["ink_layers"]["1000"] == 22
    finally:
        app.destroy()


# --- placing more than one resistor doesn't stack them on top of each other -

def _bbox_union(shapes):
    xs0, ys0, xs1, ys1 = zip(*(sh.bbox() for sh in shapes))
    return min(xs0), min(ys0), max(xs1), max(ys1)


def test_placing_two_resistors_in_a_row_does_not_overlap():
    app = _make_app()
    try:
        from hlayout.gui.resistor_designer import open_resistor_designer, PlaceResistorDialog

        win = open_resistor_designer(app)
        _seed(win)
        app.update()

        def accept_default(_dlg_list=[]):
            tops = [w for w in _find_toplevels(win) if isinstance(w, PlaceResistorDialog)]
            if not tops:
                app.after(20, accept_default)
                return
            tops[0]._accept()

        # Place the first two demo resistors (R1, R2) back to back with
        # no manual X/Y entry -- just accepting each dialog's default.
        win.res_table.select_row(0)
        app.update()
        app.after(20, accept_default)
        win._place_in_layout()

        win.res_table.select_row(1)
        app.update()
        app.after(20, accept_default)
        win._place_in_layout()

        cell = app.session.cell
        from hlayout.io.techfile import load_resistor_geometry_rules
        rules = load_resistor_geometry_rules(app.session.library)
        assert len(cell.srefs) == 2
        # Each placement is its own device cell; resolve the ink shape
        # inside each and re-express its bbox in the parent cell's
        # coordinates using the Sref's own origin (both are unrotated
        # here, so a simple translation is enough).
        r_shapes = []
        for sref in cell.srefs:
            device_cell = app.session.library.cells[sref.cell_name]
            ink = next(sh for sh in device_cell.shapes if sh.layer == rules["resistor_layer"])
            ox, oy = sref.origin
            ix0, iy0, ix1, iy1 = ink.bbox()
            r_shapes.append((ix0 + ox, iy0 + oy, ix1 + ox, iy1 + oy))
        assert len(r_shapes) == 2
        (x0a, y0a, x1a, y1a), (x0b, y0b, x1b, y1b) = r_shapes
        # The two ink bodies must not overlap in X (they were placed in
        # a left-to-right row).
        assert x1a <= x0b or x1b <= x0a
    finally:
        app.destroy()


def test_place_all_in_layout_places_every_sized_resistor_without_overlap():
    app = _make_app()
    try:
        from hlayout.gui.resistor_designer import open_resistor_designer

        win = open_resistor_designer(app)
        _seed(win)
        app.update()
        assert len(win.resistors) >= 2
        sized_before = [r for r in win.resistors if r.selected is not None]

        win._place_all_in_layout()
        app.update()

        from hlayout.io.techfile import load_resistor_geometry_rules
        rules = load_resistor_geometry_rules(app.session.library)
        cell = app.session.cell
        assert len(cell.srefs) == len(sized_before)

        boxes = []
        for sref in cell.srefs:
            device_cell = app.session.library.cells[sref.cell_name]
            ink = next(sh for sh in device_cell.shapes if sh.layer == rules["resistor_layer"])
            ox, _oy = sref.origin
            ix0, _iy0, ix1, _iy1 = ink.bbox()
            boxes.append((ix0 + ox, ix1 + ox))

        # No two resistor ink bodies overlap in X.
        boxes.sort()
        for (x0a, x1a), (x0b, x1b) in zip(boxes, boxes[1:]):
            assert x1a <= x0b
    finally:
        app.destroy()


def test_place_all_in_layout_skips_resistors_with_no_solution(monkeypatch):
    app = _make_app()
    try:
        from hlayout.gui.resistor_designer import open_resistor_designer, Resistor

        win = open_resistor_designer(app)
        _seed(win)
        r = Resistor(ref_des="RX", value=1e15, tolerance="5%", power_mw=1.0, body_width=1000.0)
        r.compute_solutions(win.rules)
        assert r.selected is None
        win.resistors.append(r)
        win._refresh_all()

        sized_before = [r for r in win.resistors if r.selected is not None]

        win._place_all_in_layout()

        # RX (no solution) is reported as skipped in the status line, and
        # only the resistors that DO have a selected solution get placed
        # -- RX contributes no Sref/device cell of its own.
        assert "RX" in win.status_var.get()
        assert len(app.session.cell.srefs) == len(sized_before)
    finally:
        app.destroy()


def test_advance_place_cursor_moves_past_the_footprint_plus_margin():
    app = _make_app()
    try:
        from hlayout.gui.resistor_designer import open_resistor_designer

        win = open_resistor_designer(app)
        _seed(win)
        res = win.resistors[0]
        sol = res.selected
        from hlayout.io.techfile import load_resistor_geometry_rules
        rules = load_resistor_geometry_rules(app.session.library)

        # The cursor tracks the next available LEFT EDGE, not a centre
        # point -- advancing from a resistor placed centred at x=0.0
        # moves the cursor to that resistor's own right edge (half its
        # footprint) plus the margin. This is what keeps differently
        # sized resistors placed back-to-back from overlapping (a
        # centre-to-centre advance under-spaces whenever a later
        # resistor's footprint is more than double the earlier one's).
        win._next_place_x = 0.0
        win._advance_place_cursor(0.0, sol, rules)
        footprint = sol.body_length + 2 * rules["overlap_a"] + 2 * rules["pad_b"]
        expected = footprint / 2.0 + rules["pad_b"]
        assert win._next_place_x == expected
    finally:
        app.destroy()


# --- top-hat geometry + whole-design selection criteria --------------------

def test_high_value_resistor_gets_an_auto_top_hat_solution():
    from hlayout.gui.resistor_designer import Resistor, DesignRules
    rules = DesignRules(aim_pct=75, ar_min=0.25, ar_max=4.0,
                        available_inks=[10000])
    # aim ~ 300k * 0.8 = 240k; on the only ink (10k/sq) that's 24
    # squares -- can't be a rectangle within 4:1, so it auto-folds.
    r = Resistor(ref_des="RH", value=300_000, tolerance="5%",
                 power_mw=25.0, body_width=500.0)
    r.compute_solutions(rules)
    assert len(r.solutions) == 1
    top = r.solutions[0]
    assert top.is_top_hat and top.n_folds >= 2
    assert top.footprint_length < top.body_length     # folded is more compact
    assert top.aspect_ratio <= rules.ar_max + 1e-9


def test_generate_resistor_n_folds_draws_a_meander_path():
    from hlayout.scripting import Session
    from hlayout.core.shapes import Path
    s = Session.new("D")
    s.new_cell("TOP")
    name = s.generate_resistor(0, 0, 500, 40000, ink_rsh=10000,
                               ref_des="R1", n_folds=4)
    body = s.library.cells[name].shapes
    paths = [sh for sh in body if isinstance(sh, Path)]
    assert len(paths) == 1
    assert len(paths[0].points) == 8                   # 4 legs x 2 points
    assert paths[0].width == 500


def test_optimise_radio_repicks_the_whole_design(monkeypatch):
    app = _make_app()
    try:
        from hlayout.gui.resistor_designer import open_resistor_designer
        win = open_resistor_designer(app)
        win.rules.available_inks = [100, 1000, 10000]
        _seed(win, specs=(("R1", 1000, "5%", 50.0), ("R2", 10000, "5%", 50.0),
                          ("R3", 3000, "5%", 50.0)))
        app.update()

        win.select_mode_var.set("inks")
        win._on_select_mode_change()
        inks_used = {r.selected.ink_rsh for r in win.resistors if r.selected}
        assert len(inks_used) == 1                     # one ink covers all three

        win.select_mode_var.set("area")
        win._on_select_mode_change()
        for r in win.resistors:
            if r.selected:
                assert r.selected.area == min(s.area for s in r.solutions)

        # the choice is persisted into the design's tech params
        from hlayout.io.techfile import load_resistor_design_rules
        assert load_resistor_design_rules(app.session.library)["select_mode"] == "area"
    finally:
        app.destroy()
