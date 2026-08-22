import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core import Library, BoundsError, GeometryError, LayerError, CellError
from hlayout.core.units import MAX_SIZE_DBU, mm_to_dbu


def test_layer_table_has_64_layers():
    lib = Library()
    assert len(lib.layers) == 64
    with pytest.raises(LayerError):
        lib.layers[64]


def test_polygon_point_limit():
    lib = Library()
    cell = lib.new_cell("TOP")
    cell.add_polygon(0, [(0, 0), (10, 0), (10, 10)])
    with pytest.raises(GeometryError):
        cell.add_polygon(0, [(i, i % 2) for i in range(201)])


def test_path_point_limit_and_cap():
    lib = Library()
    cell = lib.new_cell("TOP")
    p = cell.add_path(1, [(0, 0), (10, 10)], width=5, cap="round")
    assert p.width == 5
    with pytest.raises(GeometryError):
        cell.add_path(1, [(i, 0) for i in range(201)], width=1)


def test_rectangle_from_corners():
    lib = Library()
    cell = lib.new_cell("TOP")
    r = cell.add_rectangle(2, 10, 10, 0, 0)
    assert r.points == [(0, 0), (10, 0), (10, 10), (0, 10)]


def test_bounds_check_rejects_oversize():
    lib = Library()
    cell = lib.new_cell("TOP")
    cell.add_rectangle(0, 0, 0, mm_to_dbu(600), mm_to_dbu(600))  # 600mm x 600mm > 500x500mm limit
    with pytest.raises(BoundsError):
        lib.check_bounds()


def test_bounds_check_accepts_full_size():
    lib = Library()
    cell = lib.new_cell("TOP")
    cell.add_rectangle(0, 0, 0, MAX_SIZE_DBU, MAX_SIZE_DBU)  # exactly 500mm x 500mm
    lib.check_bounds()  # should not raise


def test_sref_cycle_detection():
    lib = Library()
    a = lib.new_cell("A")
    b = lib.new_cell("B")
    a.add_sref("B", origin=(0, 0))
    b.add_sref("A", origin=(0, 0))
    with pytest.raises(CellError):
        lib.check_no_cycles()


def test_sref_unknown_cell_detection():
    lib = Library()
    a = lib.new_cell("A")
    a.add_sref("MISSING", origin=(0, 0))
    with pytest.raises(CellError):
        lib.check_no_cycles()


def test_cell_delete_blocked_when_referenced():
    lib = Library()
    a = lib.new_cell("A")
    lib.new_cell("B")
    a.add_sref("B", origin=(0, 0))
    with pytest.raises(CellError):
        lib.delete_cell("B")


def test_would_create_cycle_detects_direct_self_reference():
    lib = Library()
    lib.new_cell("TOP")
    assert lib.would_create_cycle("TOP", "TOP") is True


def test_would_create_cycle_detects_indirect_reference():
    lib = Library()
    top = lib.new_cell("TOP")
    lib.new_cell("SUB")
    top.add_sref("SUB", origin=(0, 0))
    # SUB doesn't (yet) reference TOP, so placing SUB inside TOP is fine...
    assert lib.would_create_cycle("TOP", "SUB") is False
    # ...but placing TOP inside SUB would close the loop TOP -> SUB -> TOP.
    assert lib.would_create_cycle("SUB", "TOP") is True


def test_would_create_cycle_false_for_unrelated_cells():
    lib = Library()
    lib.new_cell("A")
    lib.new_cell("B")
    lib.new_cell("C")
    assert lib.would_create_cycle("A", "B") is False
    assert lib.would_create_cycle("A", "C") is False
