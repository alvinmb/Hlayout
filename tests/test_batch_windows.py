# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")


def _make_app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    try:
        lib = Library("BATCHWIN")
        lib.new_cell("TOP")
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    return app


# --- DXF: _layer_result_desc helper ----------------------------------------

def test_dxf_layer_result_desc_all_requested_layers_written():
    from hlayout.gui.dxf_batch_window import _layer_result_desc
    assert _layer_result_desc([5, 10], [5, 10]) == "5, 10"


def test_dxf_layer_result_desc_notes_a_requested_layer_with_no_data():
    from hlayout.gui.dxf_batch_window import _layer_result_desc
    assert _layer_result_desc([5, 7, 10], [5, 10]) == "5, 10 (no data on layer(s) 7)"


def test_dxf_layer_result_desc_all_requested_layers_empty():
    from hlayout.gui.dxf_batch_window import _layer_result_desc
    assert _layer_result_desc([7], []) == "no data on layer(s) 7"


def test_dxf_layer_result_desc_implicit_all_visible_empty():
    from hlayout.gui.dxf_batch_window import _layer_result_desc
    assert _layer_result_desc(None, []) == "(no visible layer had data)"


def test_dxf_layer_result_desc_implicit_all_visible_with_data():
    from hlayout.gui.dxf_batch_window import _layer_result_desc
    assert _layer_result_desc(None, [0, 3]) == "0, 3"


# --- DXF batch window end to end --------------------------------------------

def test_dxf_run_batch_notes_missing_layer_in_result_table(tmp_path):
    from hlayout.gui.dxf_batch_window import DxfBatchWindow
    from hlayout.io.dxfio import DxfExportJob

    app = _make_app()
    try:
        app.session.rect(0, 0, 0, 100, 100)  # data only on layer 0
        win = DxfBatchWindow(app, app.session.library, app=app)
        try:
            path = str(tmp_path / "out.dxf")
            win.jobs = [DxfExportJob(name="j1", path=path, layers=[0, 7])]
            win._run_batch()
            rows = [win.result_table.item(i)["values"] for i in win.result_table.get_children()]
            assert len(rows) == 1
            assert rows[0][0] == "j1"
            assert "0" in rows[0][1] and "no data on layer(s) 7" in rows[0][1]
        finally:
            win.destroy()
    finally:
        app.destroy()


# --- Gerber batch window end to end ------------------------------------------

def test_gerber_run_batch_adds_a_row_for_a_missing_layer(tmp_path):
    from hlayout.gui.gerber_batch_window import GerberBatchWindow
    from hlayout.io.gerberio import GerberExportJob

    app = _make_app()
    try:
        app.session.rect(0, 0, 0, 100, 100)  # data only on layer 0
        win = GerberBatchWindow(app, app.session.library, app=app)
        try:
            prefix = str(tmp_path / "gb")
            win.jobs = [GerberExportJob(name="j1", out_prefix=prefix, layers=[0, 7])]
            win._run_batch()
            rows = [win.result_table.item(i)["values"] for i in win.result_table.get_children()]
            assert len(rows) == 2
            assert rows[0][0] == "j1" and rows[0][1] == 0
            assert rows[1] == ["j1", 7, "(no data on this layer)"]
        finally:
            win.destroy()
    finally:
        app.destroy()


def test_gerber_run_batch_all_requested_layers_empty(tmp_path):
    from hlayout.gui.gerber_batch_window import GerberBatchWindow
    from hlayout.io.gerberio import GerberExportJob

    app = _make_app()
    try:
        win = GerberBatchWindow(app, app.session.library, app=app)
        try:
            prefix = str(tmp_path / "gb")
            win.jobs = [GerberExportJob(name="j1", out_prefix=prefix, layers=[7])]
            win._run_batch()
            rows = [win.result_table.item(i)["values"] for i in win.result_table.get_children()]
            assert len(rows) == 1
            assert rows[0] == ["j1", "", "no data on layer(s) 7"]
        finally:
            win.destroy()
    finally:
        app.destroy()


# --- Gerber Export Sets: output folder --------------------------------------

def test_gerber_window_output_folder_defaults_to_gerber_beside_the_design(tmp_path):
    from hlayout.gui.gerber_batch_window import GerberBatchWindow

    app = _make_app()
    try:
        app.current_path = str(tmp_path / "board.gds")
        win = GerberBatchWindow(app, app.session.library, app=app)
        try:
            assert win.out_dir_var.get() == str(tmp_path / "Gerber")
            assert not (tmp_path / "Gerber").exists()   # not created until used
        finally:
            win.destroy()
    finally:
        app.destroy()


def test_gerber_window_runs_relative_jobs_into_the_chosen_output_folder(tmp_path):
    from hlayout.gui.gerber_batch_window import GerberBatchWindow
    from hlayout.io.gerberio import GerberExportJob
    from hlayout.io.techfile import load_gerber_output_dir

    app = _make_app()
    try:
        app.current_path = str(tmp_path / "board.gds")
        app.session.rect(0, 0, 0, 100, 100)
        win = GerberBatchWindow(app, app.session.library, app=app)
        try:
            chosen = tmp_path / "fab_out"
            win.out_dir_var.set(str(chosen))
            win.jobs = [GerberExportJob(name="j1", out_prefix="board", layers=[0])]
            win._run_batch()
            assert [p.name for p in chosen.iterdir()] and \
                all(p.name.startswith("board_L00") for p in chosen.iterdir())
            # a non-default folder is remembered with the design...
            assert load_gerber_output_dir(app.session.library) == str(chosen)
            # ...and Default goes back to <current dir>/Gerber and forgets it
            win._reset_out_dir()
            assert win.out_dir_var.get() == str(tmp_path / "Gerber")
            assert load_gerber_output_dir(app.session.library) == ""
        finally:
            win.destroy()
    finally:
        app.destroy()


def test_gerber_window_scale_box_scales_the_run_and_is_remembered(tmp_path):
    from hlayout.gui.gerber_batch_window import GerberBatchWindow
    from hlayout.io.gerberio import GerberExportJob
    from hlayout.io.techfile import load_gerber_scale

    app = _make_app()
    try:
        app.current_path = str(tmp_path / "board.gds")
        app.session.rect(0, 1000, 2000, 3000, 4000)
        win = GerberBatchWindow(app, app.session.library, app=app)
        try:
            assert win.scale_var.get() == "5X"          # default: 5X artwork
            win.scale_var.set("10X")
            win.jobs = [GerberExportJob(name="j1", out_prefix="board", layers=[0])]
            win._run_batch()
            (f,) = list((tmp_path / "Gerber").iterdir())
            assert "X10000000Y20000000D02*" in f.read_text()
            assert load_gerber_scale(app.session.library) == 10.0
            assert "10X" in win.status_var.get()
        finally:
            win.destroy()
    finally:
        app.destroy()
