import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core.errors import LayoutError
from hlayout.core.library import Library
from hlayout.io.techfile import (
    DEFAULT_RESISTOR_GEOMETRY,
    DEFAULT_RESISTOR_RULES,
    DEFAULT_ROUTE_RULES,
    apply_techfile,
    load_and_apply_techfile,
    load_resistor_design_rules,
    load_resistor_geometry_rules,
    load_route_rules,
    load_techfile,
    save_resistor_design_rules,
    save_resistor_geometry_rules,
    save_route_rules,
    save_techfile,
    techfile_data,
)
from hlayout.scripting import Session


def test_techfile_data_round_trip(tmp_path):
    lib = Library("DEMO")
    lib.layers.set_style(0, name="METAL1", color="#123456", stipple=3, fill=False, visible=False)
    lib.tech_params["process"] = "0.35um"
    lib.tech_params["foundry"] = "acme"

    path = str(tmp_path / "tech.json")
    save_techfile(lib, path)

    lib2 = Library("OTHER")
    data = load_and_apply_techfile(lib2, path)
    assert len(data["layers"]) == 64
    assert lib2.layers[0].name == "METAL1"
    assert lib2.layers[0].color == "#123456"
    assert lib2.layers[0].stipple == 3
    assert lib2.layers[0].fill is False
    assert lib2.layers[0].visible is False
    assert lib2.tech_params == {"process": "0.35um", "foundry": "acme"}


def test_load_techfile_missing_file_raises_clean_error(tmp_path):
    with pytest.raises(LayoutError):
        load_techfile(str(tmp_path / "nope.json"))


def test_load_techfile_bad_json_raises_clean_error(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json{{{")
    with pytest.raises(LayoutError):
        load_techfile(str(path))


def test_apply_techfile_rejects_out_of_range_layer_number():
    lib = Library("DEMO")
    with pytest.raises(LayoutError):
        apply_techfile(lib, {"layers": [{"number": 999, "name": "X"}]})


def test_apply_techfile_only_touches_layers_it_mentions():
    lib = Library("DEMO")
    original_name_1 = lib.layers[1].name
    apply_techfile(lib, {"layers": [{"number": 0, "name": "ONLY_ZERO"}]})
    assert lib.layers[0].name == "ONLY_ZERO"
    assert lib.layers[1].name == original_name_1


def test_session_load_and_save_technology(tmp_path):
    s = Session.new("DEMO")
    s.new_cell("TOP")
    s.library.layers.set_style(2, name="POLY", color="#abcdef")
    s.library.tech_params["units_note"] = "microns"

    path = str(tmp_path / "tech.json")
    s.save_technology(path)

    s2 = Session.new("OTHER")
    s2.new_cell("TOP")
    data = s2.load_technology(path)
    assert s2.library.layers[2].name == "POLY"
    assert s2.library.layers[2].color == "#abcdef"
    assert s2.library.tech_params["units_note"] == "microns"
    assert len(data["layers"]) == 64


def test_load_resistor_design_rules_returns_defaults_when_absent():
    lib = Library("DEMO")
    rules = load_resistor_design_rules(lib)
    assert rules == DEFAULT_RESISTOR_RULES


def test_resistor_design_rules_round_trip_through_tech_params():
    lib = Library("DEMO")
    custom = {
        "aim_pct": 80.0,
        "ar_min": 0.2,
        "ar_max": 5.0,
        "available_inks": [10, 100, 1000],
    }
    save_resistor_design_rules(lib, custom)
    assert lib.tech_params  # a single JSON entry was stashed there
    assert load_resistor_design_rules(lib) == custom


def test_resistor_design_rules_survive_a_full_techfile_save_and_load(tmp_path):
    lib = Library("DEMO")
    save_resistor_design_rules(lib, {
        "aim_pct": 60.0, "ar_min": 0.1, "ar_max": 8.0,
        "available_inks": [30, 300, 3000],
    })
    path = str(tmp_path / "tech.json")
    save_techfile(lib, path)

    lib2 = Library("OTHER")
    load_and_apply_techfile(lib2, path)
    rules = load_resistor_design_rules(lib2)
    assert rules["aim_pct"] == 60.0
    assert rules["ar_min"] == 0.1
    assert rules["ar_max"] == 8.0
    assert rules["available_inks"] == [30, 300, 3000]


def test_load_resistor_design_rules_tolerates_malformed_entry():
    lib = Library("DEMO")
    lib.tech_params["resistor_design_rules"] = "not valid json{{{"
    assert load_resistor_design_rules(lib) == DEFAULT_RESISTOR_RULES


def test_load_resistor_design_rules_fills_in_missing_keys_from_defaults():
    lib = Library("DEMO")
    import json
    lib.tech_params["resistor_design_rules"] = json.dumps({"aim_pct": 90.0})
    rules = load_resistor_design_rules(lib)
    assert rules["aim_pct"] == 90.0
    assert rules["ar_min"] == DEFAULT_RESISTOR_RULES["ar_min"]
    assert rules["ar_max"] == DEFAULT_RESISTOR_RULES["ar_max"]
    assert rules["available_inks"] == DEFAULT_RESISTOR_RULES["available_inks"]


# --- Resistor GEOMETRY rules (A/B/C + layers, for actually drawing one) ----

def test_load_resistor_geometry_rules_returns_defaults_when_absent():
    lib = Library("DEMO")
    assert load_resistor_geometry_rules(lib) == DEFAULT_RESISTOR_GEOMETRY


def test_resistor_geometry_rules_round_trip_through_tech_params():
    lib = Library("DEMO")
    custom = {"overlap_a": 150.0, "pad_b": 400.0, "space_c": 100.0, "glaze_oversize": 75.0,
              "resistor_layer": 12, "conductor_layer": 13,
              "glaze_layer": 14, "doc_layer": 15, "ink_layers": {"1000": 20}}
    save_resistor_geometry_rules(lib, custom)
    assert lib.tech_params
    assert load_resistor_geometry_rules(lib) == custom


def test_resistor_geometry_rules_survive_a_full_techfile_save_and_load(tmp_path):
    lib = Library("DEMO")
    save_resistor_geometry_rules(lib, {
        "overlap_a": 250.0, "pad_b": 600.0, "space_c": 300.0,
        "resistor_layer": 20, "conductor_layer": 21,
    })
    path = str(tmp_path / "tech.json")
    save_techfile(lib, path)

    lib2 = Library("OTHER")
    load_and_apply_techfile(lib2, path)
    rules = load_resistor_geometry_rules(lib2)
    assert rules["overlap_a"] == 250.0
    assert rules["pad_b"] == 600.0
    assert rules["space_c"] == 300.0
    assert rules["resistor_layer"] == 20
    assert rules["conductor_layer"] == 21


def test_load_resistor_geometry_rules_tolerates_malformed_entry():
    lib = Library("DEMO")
    lib.tech_params["resistor_geometry_rules"] = "not valid json{{{"
    assert load_resistor_geometry_rules(lib) == DEFAULT_RESISTOR_GEOMETRY


# --- Create > Route (2-Layer) settings --------------------------------------
# Simplified to just the two conductor layers plus the name of a reusable
# via cell (Tools > Via Wizard.../scripting's create_via_cell()) that Space
# places an instance of -- the via's own pad size/shape/dielectric window
# live in that cell's own geometry instead of being duplicated here.

def test_load_route_rules_returns_defaults_when_absent():
    lib = Library("DEMO")
    assert load_route_rules(lib) == DEFAULT_ROUTE_RULES
    assert DEFAULT_ROUTE_RULES["via_cell"] == ""


def test_route_rules_round_trip_through_tech_params():
    lib = Library("DEMO")
    custom = {"layer_a": 2, "layer_b": 5, "via_cell": "VIA_Layer2_to_Layer5"}
    save_route_rules(lib, custom)
    assert lib.tech_params
    assert load_route_rules(lib) == custom


def test_route_rules_survive_a_full_techfile_save_and_load(tmp_path):
    # The actual point of the request: configure the Route Layers dialog
    # once, and a later session loading this technology file sees it
    # again with nothing re-entered by hand.
    lib = Library("DEMO")
    save_route_rules(lib, {
        "layer_a": 3, "layer_b": 7, "via_cell": "VIA_Layer3_to_Layer7",
    })
    path = str(tmp_path / "tech.json")
    save_techfile(lib, path)

    lib2 = Library("OTHER")
    load_and_apply_techfile(lib2, path)
    rules = load_route_rules(lib2)
    assert rules["layer_a"] == 3
    assert rules["layer_b"] == 7
    assert rules["via_cell"] == "VIA_Layer3_to_Layer7"


def test_load_route_rules_tolerates_malformed_entry():
    lib = Library("DEMO")
    lib.tech_params["route_rules"] = "not valid json{{{"
    assert load_route_rules(lib) == DEFAULT_ROUTE_RULES


def test_load_resistor_geometry_rules_fills_in_missing_keys_from_defaults():
    lib = Library("DEMO")
    import json
    lib.tech_params["resistor_geometry_rules"] = json.dumps({"overlap_a": 999.0})
    rules = load_resistor_geometry_rules(lib)
    assert rules["overlap_a"] == 999.0
    assert rules["pad_b"] == DEFAULT_RESISTOR_GEOMETRY["pad_b"]
    assert rules["space_c"] == DEFAULT_RESISTOR_GEOMETRY["space_c"]
    assert rules["resistor_layer"] == DEFAULT_RESISTOR_GEOMETRY["resistor_layer"]
    assert rules["conductor_layer"] == DEFAULT_RESISTOR_GEOMETRY["conductor_layer"]
