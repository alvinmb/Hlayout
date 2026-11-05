# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Setup > Technology Properties > Resistor Shapes: the allowed list,
its techfile round trip and its enforcement in generate_resistor()."""
import json

import pytest

from hlayout.core.errors import LayoutError
from hlayout.core.tf_shapes import SHAPE_KEYS
from hlayout.io.techfile import (
    RESISTOR_SHAPES_KEY,
    load_allowed_resistor_shapes,
    resistor_shape_allowed,
    save_allowed_resistor_shapes,
    save_techfile,
    load_techfile,
)
from hlayout.core.library import Library
from hlayout.scripting.api import Session


def test_default_is_all_shapes():
    s = Session(Library("T"))
    assert load_allowed_resistor_shapes(s.library) == SHAPE_KEYS


def test_save_load_keeps_menu_order_and_drops_unknown():
    s = Session(Library("T"))
    save_allowed_resistor_shapes(s.library, ["angle", "RECT", "bogus", "tophat_roof"])
    assert load_allowed_resistor_shapes(s.library) == ["rect", "tophat_roof", "angle"]
    assert json.loads(s.library.tech_params[RESISTOR_SHAPES_KEY]) == ["rect", "tophat_roof", "angle"]
    assert resistor_shape_allowed(s.library, "angle")
    assert not resistor_shape_allowed(s.library, "u")


def test_empty_selection_rejected():
    s = Session(Library("T"))
    with pytest.raises(LayoutError):
        save_allowed_resistor_shapes(s.library, [])


def test_bad_stored_value_falls_back_to_all():
    s = Session(Library("T"))
    s.library.tech_params[RESISTOR_SHAPES_KEY] = "not json"
    assert load_allowed_resistor_shapes(s.library) == SHAPE_KEYS
    s.library.tech_params[RESISTOR_SHAPES_KEY] = "[]"
    assert load_allowed_resistor_shapes(s.library) == SHAPE_KEYS


def test_travels_in_technology_file(tmp_path):
    s = Session(Library("T"))
    save_allowed_resistor_shapes(s.library, ["rect", "u"])
    path = tmp_path / "t.json"
    save_techfile(s.library, str(path))
    assert json.loads(load_techfile(str(path))["params"][RESISTOR_SHAPES_KEY]) == ["rect", "u"]


def test_generate_resistor_enforces_allowed():
    s = Session(Library("T"))
    s.new_cell("TOP")
    save_allowed_resistor_shapes(s.library, ["rect", "angle"])
    s.generate_resistor(0, 0, 1000, 2000)
    s.generate_resistor(5000, 0, 1000, 1500, shape="angle")
    with pytest.raises(LayoutError, match="not allowed"):
        s.generate_resistor(10000, 0, 1000, 4000, shape="tophat")
