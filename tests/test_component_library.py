import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.io.component_library import (
    DEFAULT_CERAMIC_CAPS,
    DEFAULT_PKG20,
    write_component_library_macro,
)
from hlayout.scripting import Session


def test_write_component_library_macro_default_path(tmp_path):
    out_dir = str(tmp_path / "complib")
    path = write_component_library_macro(out_dir)
    assert path == os.path.join(out_dir, "build_component_library.FCMacro")
    assert os.path.isfile(path)


def test_write_component_library_macro_custom_path(tmp_path):
    out_dir = str(tmp_path / "complib")
    path = str(tmp_path / "somewhere_else" / "build.FCMacro")
    result = write_component_library_macro(out_dir, path=path)
    assert result == path
    assert os.path.isfile(path)


def test_macro_contains_freecad_boilerplate_and_output_dir(tmp_path):
    out_dir = str(tmp_path / "complib")
    path = write_component_library_macro(out_dir)
    text = open(path).read()
    assert "import FreeCAD as App" in text
    assert "import Part" in text
    assert repr(out_dir) in text
    assert "def make_ceramic_cap(" in text
    assert "def make_pkg20(" in text


def test_macro_emits_one_export_call_per_default_ceramic_cap(tmp_path):
    out_dir = str(tmp_path / "complib")
    path = write_component_library_macro(out_dir)
    text = open(path).read()
    for name, length, width, height, term in DEFAULT_CERAMIC_CAPS:
        assert repr(name) in text
        assert f"make_ceramic_cap({length!r}, {width!r}, {height!r}, {term!r})" in text


def test_macro_emits_pkg20_export_by_default(tmp_path):
    out_dir = str(tmp_path / "complib")
    path = write_component_library_macro(out_dir)
    text = open(path).read()
    assert "'PKG20'" in text
    assert f"{DEFAULT_PKG20['leads_per_side']!r}" in text


def test_custom_ceramic_caps_list_replaces_the_default(tmp_path):
    out_dir = str(tmp_path / "complib")
    path = write_component_library_macro(
        out_dir, ceramic_caps=[("CAP_CUSTOM", 5.0, 2.5, 2.0, 0.6)])
    text = open(path).read()
    assert "CAP_CUSTOM" in text
    assert "CAP_0603" not in text


def test_empty_ceramic_caps_list_skips_capacitors_entirely(tmp_path):
    out_dir = str(tmp_path / "complib")
    path = write_component_library_macro(out_dir, ceramic_caps=[])
    text = open(path).read()
    assert text.count("make_ceramic_cap(") == 1  # only the def, no export() calls
    assert "CAP_0603" not in text


def test_pkg20_false_skips_the_package_entirely(tmp_path):
    out_dir = str(tmp_path / "complib")
    path = write_component_library_macro(out_dir, pkg20=False)
    text = open(path).read()
    assert "'PKG20'" not in text


def test_pkg20_overrides_merge_onto_the_default(tmp_path):
    out_dir = str(tmp_path / "complib")
    path = write_component_library_macro(out_dir, pkg20={"leads_per_side": 8})
    text = open(path).read()
    assert "make_pkg20(" in text
    assert "8," in text  # overridden leads_per_side made it into the call
    assert f"{DEFAULT_PKG20['length_mm']!r}" in text  # untouched default still present


def test_session_generate_component_library_delegates(tmp_path):
    s = Session.new("COMPLIBTEST")
    out_dir = str(tmp_path / "complib")
    path = s.generate_component_library(out_dir)
    assert os.path.isfile(path)
    assert path == os.path.join(out_dir, "build_component_library.FCMacro")
