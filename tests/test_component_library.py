import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.io.component_library import (
    DEFAULT_CERAMIC_CAPS,
    DEFAULT_PKG20,
    DEFAULT_SOT_PACKAGES,
    MAX_CAP_HEIGHT_MM,
    write_component_library_macro,
)
from hlayout.scripting import Session


def test_ceramic_cap_heights_never_exceed_the_realistic_max():
    # Regression: height used to default to width for every derived
    # size, which overstated 1812/2512 (3.2 mm wide) as equally thick --
    # real MLCC thickness plateaus well below that.
    for name, _length, _width, height, _term in DEFAULT_CERAMIC_CAPS:
        assert height <= MAX_CAP_HEIGHT_MM, f"{name} height {height} exceeds the cap"


def test_ceramic_cap_1812_and_2512_are_capped_not_as_wide_as_tall():
    heights = {name: height for name, _l, _w, height, _t in DEFAULT_CERAMIC_CAPS}
    assert heights["CAP_1812"] == MAX_CAP_HEIGHT_MM
    assert heights["CAP_2512"] == MAX_CAP_HEIGHT_MM


def test_original_hand_picked_cap_sizes_are_unchanged():
    by_name = {name: (length, width, height, term)
               for name, length, width, height, term in DEFAULT_CERAMIC_CAPS}
    assert by_name["CAP_0603"] == (1.6, 0.8, 0.8, 0.3)
    assert by_name["CAP_0805"] == (2.0, 1.25, 1.25, 0.4)
    assert by_name["CAP_1206"] == (3.2, 1.6, 1.6, 0.5)


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
    assert "def make_sot(" in text


def test_macro_export_skips_a_step_file_that_already_exists(tmp_path):
    """Re-running the macro must never clobber a STEP file someone
    hand-replaced with a real vendor model (or a manual edit) -- see
    the module docstring. This only checks the generated TEXT has the
    guard (the actual skip-on-disk behavior needs FreeCAD to run, and
    is verified separately via freecadcmd -- see the module docstring's
    note on that)."""
    out_dir = str(tmp_path / "complib")
    path = write_component_library_macro(out_dir)
    text = open(path).read()
    assert "if os.path.isfile(path):" in text
    assert "already exists, leaving it alone" in text


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


def test_macro_emits_one_export_call_per_default_sot_package(tmp_path):
    out_dir = str(tmp_path / "complib")
    path = write_component_library_macro(out_dir)
    text = open(path).read()
    for name in DEFAULT_SOT_PACKAGES:
        assert repr(name) in text
    assert "make_sot(" in text
    assert text.count("make_sot(") == 1 + len(DEFAULT_SOT_PACKAGES)  # def + one call per part


def test_custom_sot_packages_dict_replaces_the_default(tmp_path):
    out_dir = str(tmp_path / "complib")
    path = write_component_library_macro(
        out_dir, sot_packages={"SOT_CUSTOM": dict(L=3.0, W=1.5, H=1.0, lead_t=0.1,
                                                    lead_out=0.4, lead_foot=0.4,
                                                    leads_neg_y=[(0.0, 0.4)])})
    text = open(path).read()
    assert "SOT_CUSTOM" in text
    assert "SOT_23" not in text


def test_empty_sot_packages_dict_skips_the_family_entirely(tmp_path):
    out_dir = str(tmp_path / "complib")
    path = write_component_library_macro(out_dir, sot_packages={})
    text = open(path).read()
    assert text.count("make_sot(") == 1  # only the def, no export() calls
    for name in DEFAULT_SOT_PACKAGES:
        assert name not in text


def test_default_sot_packages_include_the_soic_style_ic_variants():
    for name, n_leads_per_side in (("SOT_14", 7), ("SOT_16", 8), ("SOT_18", 9)):
        pkg = DEFAULT_SOT_PACKAGES[name]
        assert len(pkg["leads_neg_y"]) == n_leads_per_side
        assert len(pkg["leads_pos_y"]) == n_leads_per_side
        # symmetric both sides, unlike the 3-lead transistor outlines
        assert [x for x, _ in pkg["leads_neg_y"]] == [x for x, _ in pkg["leads_pos_y"]]


def test_macro_emits_sot_14_16_18_by_default(tmp_path):
    out_dir = str(tmp_path / "complib")
    path = write_component_library_macro(out_dir)
    text = open(path).read()
    for name in ("SOT_14", "SOT_16", "SOT_18"):
        assert repr(name) in text


def test_sot_packages_false_also_skips_the_family(tmp_path):
    out_dir = str(tmp_path / "complib")
    path = write_component_library_macro(out_dir, sot_packages=False)
    text = open(path).read()
    assert text.count("make_sot(") == 1


def test_session_generate_component_library_delegates(tmp_path):
    s = Session.new("COMPLIBTEST")
    out_dir = str(tmp_path / "complib")
    path = s.generate_component_library(out_dir)
    assert os.path.isfile(path)
    assert path == os.path.join(out_dir, "build_component_library.FCMacro")
