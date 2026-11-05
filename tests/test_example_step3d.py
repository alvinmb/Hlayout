# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.io.example_step3d import (
    _FREECAD_FALLBACK_GLOBS,
    find_freecadcmd,
    soic_leads,
    write_step_macro,
)


def test_soic_leads_symmetric_about_zero_at_the_given_pitch():
    leads = soic_leads(4, 1.27, 0.4)
    assert len(leads) == 4
    xs = [x for x, _w in leads]
    assert xs == sorted(xs)
    assert abs(xs[0] + xs[-1]) < 1e-9  # symmetric about 0
    deltas = [b - a for a, b in zip(xs, xs[1:])]
    assert all(abs(d - 1.27) < 1e-9 for d in deltas)
    assert all(w == 0.4 for _x, w in leads)


def test_write_step_macro_default_path(tmp_path):
    out_dir = str(tmp_path / "step_out")
    path = write_step_macro([], out_dir)
    assert path == os.path.join(out_dir, "build_step_models.FCMacro")
    assert os.path.isfile(path)


def test_write_step_macro_custom_path(tmp_path):
    out_dir = str(tmp_path / "step_out")
    path = str(tmp_path / "elsewhere" / "build.FCMacro")
    result = write_step_macro([], out_dir, path=path)
    assert result == path
    assert os.path.isfile(path)


def test_macro_contains_freecad_boilerplate_and_all_geometry_functions(tmp_path):
    out_dir = str(tmp_path / "step_out")
    path = write_step_macro([], out_dir)
    text = open(path).read()
    assert "import FreeCAD as App" in text
    assert "import Part" in text
    assert repr(out_dir) in text
    for fn in ("make_end_cap(", "make_lcc(", "make_substrate_pins(", "make_dip(", "make_dip_x("):
        assert f"def {fn}" in text


def test_macro_emits_one_export_call_per_part(tmp_path):
    out_dir = str(tmp_path / "step_out")
    parts = [
        ("A", "make_end_cap", dict(L=3.2, W=1.6, H=1.6, term_len=0.4)),
        ("B", "make_lcc", dict(side=5.0, pitch=1.27, per_side=4, pad_w=0.75, pad_depth=1.0, H=2.3)),
    ]
    path = write_step_macro(parts, out_dir)
    text = open(path).read()
    assert "export(make_end_cap(" in text
    assert "export(make_lcc(" in text
    assert "'A'" in text
    assert "'B'" in text


def test_macro_export_skips_a_step_file_that_already_exists(tmp_path):
    out_dir = str(tmp_path / "step_out")
    path = write_step_macro([], out_dir)
    text = open(path).read()
    assert "if os.path.isfile(path):" in text
    assert "already exists, leaving it alone" in text


def test_find_freecadcmd_returns_a_string_or_none():
    # Doesn't assert FreeCAD is actually installed on the test machine --
    # just that the lookup itself doesn't raise, and only ever returns
    # a real path or None.
    result = find_freecadcmd()
    assert result is None or (isinstance(result, str) and os.path.isfile(result))


def test_freecad_fallback_globs_cover_windows_and_unix():
    assert any("Program Files" in g for g in _FREECAD_FALLBACK_GLOBS)
    assert any(g.startswith("/") for g in _FREECAD_FALLBACK_GLOBS)
