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
