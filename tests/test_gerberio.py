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


def test_explicit_layers_filter_overrides_hidden(tmp_path):
    """An explicitly-named layer (e.g. a Gerber Export Sets job's
    hand-picked list) must still export even if it's currently hidden
    on screen -- only the IMPLICIT "every visible layer" default
    (test_invisible_layer_is_not_exported above) should key off the
    Setup > Layers visibility toggle."""
    lib = Library("GBTEST")
    cell = lib.new_cell("TOP")
    cell.add_rectangle(2, 0, 0, mm_to_dbu(1), mm_to_dbu(1))
    lib.layers[2].visible = False
    prefix = str(tmp_path / "out")

    written, _ = write_gerber_layers(lib, "TOP", prefix, layers=[2])

    assert [n for n, _ in written] == [2]


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


# --- Gerber Export Sets: output folder --------------------------------------

def test_default_gerber_output_dir_is_gerber_beside_the_design(tmp_path):
    from hlayout.io.gerberio import default_gerber_output_dir
    design = tmp_path / "board1.gds"
    assert default_gerber_output_dir(str(design)) == str(tmp_path / "Gerber")


def test_default_gerber_output_dir_uses_the_working_dir_when_unsaved(tmp_path, monkeypatch):
    from hlayout.io.gerberio import default_gerber_output_dir
    monkeypatch.chdir(tmp_path)
    assert default_gerber_output_dir(None) == os.path.join(os.getcwd(), "Gerber")


def test_default_gerber_output_dir_reuses_an_existing_folder_in_other_case(tmp_path):
    from hlayout.io.gerberio import default_gerber_output_dir
    (tmp_path / "gerber").mkdir()
    assert default_gerber_output_dir(str(tmp_path / "b.gds")) == str(tmp_path / "gerber")


def test_resolve_out_prefix_relative_goes_in_the_folder_absolute_is_kept(tmp_path):
    from hlayout.io.gerberio import resolve_out_prefix
    out = str(tmp_path / "Gerber")
    assert resolve_out_prefix("board", out) == os.path.join(out, "board")
    assert resolve_out_prefix(os.path.join("fabA", "board"), out) == os.path.join(out, "fabA", "board")
    absolute = str(tmp_path / "elsewhere" / "board")
    assert resolve_out_prefix(absolute, out) == absolute
    assert resolve_out_prefix(r"C:\jobs\out\board", out) == r"C:\jobs\out\board"
    assert resolve_out_prefix("board", None) == "board"


def test_run_gerber_export_batch_out_dir(tmp_path):
    from hlayout.scripting import Session
    s = Session.new("GBSET")
    s.new_cell("TOP")
    s.rect(0, 0, 0, 1000, 1000)
    out = tmp_path / "Gerber"            # does not exist yet
    elsewhere = str(tmp_path / "other" / "abs")
    results = s.run_gerber_export_batch(
        [{"name": "rel", "out_prefix": "board", "layers": [0]},
         {"name": "abs", "out_prefix": elsewhere, "layers": [0]}],
        out_dir=str(out))
    rel_file = results[0][1][0][1]
    abs_file = results[1][1][0][1]
    assert os.path.dirname(rel_file) == str(out) and os.path.isfile(rel_file)
    assert os.path.dirname(abs_file) == str(tmp_path / "other") and os.path.isfile(abs_file)


def test_gerber_output_dir_round_trips_through_tech_params():
    from hlayout.io.techfile import load_gerber_output_dir, save_gerber_output_dir
    lib = Library("GBDIR")
    assert load_gerber_output_dir(lib) == ""
    save_gerber_output_dir(lib, r"D:\fab\out")
    assert load_gerber_output_dir(lib) == r"D:\fab\out"
    save_gerber_output_dir(lib, "")
    assert load_gerber_output_dir(lib) == ""


# --- artwork scale (hybrid screens are plotted at 5X / 10X) -----------------

def _scaled_lib():
    lib = Library("GBSCALE")
    cell = lib.new_cell("TOP")
    cell.add_rectangle(0, mm_to_dbu(1), mm_to_dbu(2), mm_to_dbu(3), mm_to_dbu(4))
    cell.add_path(0, [(0, 0), (mm_to_dbu(1), 0)], mm_to_dbu(0.1))
    return lib


def test_scale_multiplies_coordinates_and_path_widths(tmp_path):
    lib = _scaled_lib()
    (_n, f1), = write_gerber_layers(lib, "TOP", str(tmp_path / "x1"))[0]
    (_n, f5), = write_gerber_layers(lib, "TOP", str(tmp_path / "x5"), scale=5)[0]
    one, five = _read(f1), _read(f5)
    # the 1,2 mm corner (6-decimal mm units) lands at 5,10 mm
    assert "X1000000Y2000000D02*" in one
    assert "X5000000Y10000000D02*" in five and "X1000000Y2000000D02*" not in five
    # the 0.1 mm path aperture becomes 0.5 mm
    assert "C,0.1000*%" in one and "C,0.5000*%" in five
    assert "Artwork scale 5X" in five and "Artwork scale 1X" in one


def test_scale_default_is_actual_size_and_leaves_the_design_alone(tmp_path):
    lib = _scaled_lib()
    before = [list(sh.points) for sh in lib.cells["TOP"].shapes]
    (_n, fa), = write_gerber_layers(lib, "TOP", str(tmp_path / "a"))[0]
    (_n, fb), = write_gerber_layers(lib, "TOP", str(tmp_path / "b"), scale=1)[0]
    write_gerber_layers(lib, "TOP", str(tmp_path / "c"), scale=10)
    assert _read(fa) == _read(fb)
    assert [list(sh.points) for sh in lib.cells["TOP"].shapes] == before


def test_scale_must_be_a_positive_number_and_fit_the_format(tmp_path):
    import pytest
    from hlayout.core.errors import GeometryError
    lib = _scaled_lib()
    for bad in (0, -5, "big", float("inf")):
        with pytest.raises(GeometryError):
            write_gerber_layers(lib, "TOP", str(tmp_path / "bad"), scale=bad)
    with pytest.raises(GeometryError, match="limit"):
        write_gerber_layers(lib, "TOP", str(tmp_path / "huge"), scale=5000)


def test_batch_and_command_bar_take_a_scale(tmp_path):
    from hlayout.scripting import Session
    s = Session.new("GBSET2")
    s.new_cell("TOP")
    s.rect(0, 1000, 2000, 3000, 4000)
    res = s.run_gerber_export_batch([{"name": "j", "out_prefix": "b", "layers": [0]}],
                                    out_dir=str(tmp_path), scale=10)
    assert "X10000000Y20000000D02*" in _read(res[0][1][0][1])

    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run(f"GERBER {tmp_path / 'cmd'} scale=5X")
    assert "at 5X" in out
    assert "X5000000Y10000000D02*" in _read(str(tmp_path / "cmd") + "_L00_L0.gbr")
    assert "error" in interp.run(f"GERBER {tmp_path / 'cmd2'} scale=abc").lower()


def test_gerber_scale_round_trips_through_tech_params():
    from hlayout.io.techfile import load_gerber_scale, save_gerber_scale
    lib = Library("GBSC")
    assert load_gerber_scale(lib) == 5.0        # default: 5X artwork
    save_gerber_scale(lib, 10)
    assert load_gerber_scale(lib) == 10.0
    save_gerber_scale(lib, 1)                   # an explicit 1X sticks
    assert load_gerber_scale(lib) == 1.0
    lib.tech_params["gerber_output_scale"] = "junk"
    assert load_gerber_scale(lib) == 5.0
