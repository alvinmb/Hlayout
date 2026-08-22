import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core import Library
from hlayout.core.units import mm_to_dbu
from hlayout.io import write_gerber_layers


def _read(path):
    with open(path) as f:
        return f.read()


def test_exports_one_file_per_visible_layer_with_data(tmp_path):
    lib = Library("GBTEST")
    cell = lib.new_cell("TOP")
    cell.add_rectangle(0, 0, 0, mm_to_dbu(10), mm_to_dbu(10))
    cell.add_rectangle(1, mm_to_dbu(20), mm_to_dbu(20), mm_to_dbu(25), mm_to_dbu(25))
    prefix = str(tmp_path / "out")

    written, skipped_text = write_gerber_layers(lib, "TOP", prefix)

    assert skipped_text == 0
    assert [n for n, _ in written] == [0, 1]
    for _, path in written:
        assert os.path.exists(path)
        content = _read(path)
        assert "%FSLAX46Y46*%" in content
        assert "%MOMM*%" in content
        assert "G36*" in content and "G37*" in content
        assert content.strip().endswith("M02*")


def test_invisible_layer_is_not_exported(tmp_path):
    lib = Library("GBTEST")
    cell = lib.new_cell("TOP")
    cell.add_rectangle(0, 0, 0, mm_to_dbu(10), mm_to_dbu(10))
    cell.add_rectangle(2, 0, 0, mm_to_dbu(10), mm_to_dbu(10))
    lib.layers[2].visible = False
    prefix = str(tmp_path / "out")

    written, _ = write_gerber_layers(lib, "TOP", prefix)

    assert [n for n, _ in written] == [0]


def test_layer_with_no_data_produces_no_file(tmp_path):
    lib = Library("GBTEST")
    lib.new_cell("TOP")
    prefix = str(tmp_path / "out")

    written, skipped_text = write_gerber_layers(lib, "TOP", prefix)

    assert written == []
    assert skipped_text == 0
    assert list(tmp_path.iterdir()) == []


def test_sref_hierarchy_is_flattened_into_the_gerber_file(tmp_path):
    lib = Library("GBTEST")
    sub = lib.new_cell("SUB")
    sub.add_rectangle(0, 0, 0, mm_to_dbu(5), mm_to_dbu(5))
    top = lib.new_cell("TOP")
    top.add_sref("SUB", origin=(mm_to_dbu(50), mm_to_dbu(50)))
    prefix = str(tmp_path / "out")

    written, _ = write_gerber_layers(lib, "TOP", prefix)

    assert [n for n, _ in written] == [0]
    content = _read(written[0][1])
    # The rectangle should be plotted at the sref's offset location
    # (50mm = 50_000_000 gerber units), not at its own-cell origin.
    assert "X50000000Y50000000D02*" in content


def test_path_gets_a_circular_aperture_sized_to_its_width(tmp_path):
    lib = Library("GBTEST")
    cell = lib.new_cell("TOP")
    cell.add_path(3, [(0, 0), (mm_to_dbu(10), 0)], width=mm_to_dbu(0.2), cap="round")
    prefix = str(tmp_path / "out")

    written, _ = write_gerber_layers(lib, "TOP", prefix)

    content = _read(written[0][1])
    assert "%ADD11C,0.2000*%" in content
    assert "D11*" in content


def test_text_shapes_are_skipped_and_counted(tmp_path):
    lib = Library("GBTEST")
    cell = lib.new_cell("TOP")
    cell.add_rectangle(0, 0, 0, mm_to_dbu(1), mm_to_dbu(1))
    cell.add_text(0, (0, 0), "hello", height=10, font=0)
    prefix = str(tmp_path / "out")

    written, skipped_text = write_gerber_layers(lib, "TOP", prefix)

    assert skipped_text == 1
    assert len(written) == 1


def test_layers_filter_restricts_export(tmp_path):
    lib = Library("GBTEST")
    cell = lib.new_cell("TOP")
    cell.add_rectangle(0, 0, 0, mm_to_dbu(1), mm_to_dbu(1))
    cell.add_rectangle(1, 0, 0, mm_to_dbu(1), mm_to_dbu(1))
    prefix = str(tmp_path / "out")

    written, _ = write_gerber_layers(lib, "TOP", prefix, layers=[1])

    assert [n for n, _ in written] == [1]


def test_unknown_cell_raises_value_error(tmp_path):
    import pytest
    lib = Library("GBTEST")
    lib.new_cell("TOP")
    prefix = str(tmp_path / "out")
    with pytest.raises(ValueError):
        write_gerber_layers(lib, "NOPE", prefix)


def test_session_export_gerber_uses_current_cell(tmp_path):
    from hlayout.scripting import Session
    s = Session.new("SESSGB")
    s.new_cell("TOP")
    s.rect(0, 0, 0, mm_to_dbu(1), mm_to_dbu(1))
    prefix = str(tmp_path / "sess_out")
    written, skipped_text = s.export_gerber(prefix)
    assert len(written) == 1
    assert skipped_text == 0
