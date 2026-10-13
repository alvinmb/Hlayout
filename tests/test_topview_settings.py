import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.library import Library
from hlayout.gui.commands import CommandInterpreter
from hlayout.io.techfile import load_topview_settings, save_topview_settings
from hlayout.scripting import Session


def _session():
    lib = Library("TV")
    top = lib.new_cell("TOP")
    top.add_rectangle(0, 0, 0, 1000, 1000)
    top.add_rectangle(2, 2000, 0, 3000, 1000)
    lib.top_cell_name = "TOP"
    s = Session(lib)
    s.use_cell("TOP")
    return s


def test_topview_settings_round_trip_and_defaults():
    lib = Library("X")
    st = load_topview_settings(lib)
    assert st["saved"] is False and st["out_cell"] == "TOPVIEW" and st["result_layer"] == -1
    save_topview_settings(lib, {"result_layer": 7, "excluded": [3, 3, 1], "hatch": True,
                                "hatch_angles": [30], "out_cell": "VIEW", "bogus": 1})
    st = load_topview_settings(lib)
    assert st["saved"] and st["result_layer"] == 7 and st["excluded"] == [1, 3]
    assert st["hatch"] is True and st["hatch_angles"] == [30.0] and st["out_cell"] == "VIEW"
    assert "bogus" not in lib.tech_params["topview_settings"]


def test_topview_from_settings_and_bare_command():
    s = _session()
    interp = CommandInterpreter(s, console=None)
    assert interp.run("TOPVIEW").startswith("error")          # no result layer saved yet
    save_topview_settings(s.library, {"result_layer": 1, "excluded": [2]})
    out = interp.run("TOPVIEW")
    assert "saved settings" in out and "cell TOPVIEW" in out
    xs = [p[0] for sh in s.library.cells["TOPVIEW"].shapes for p in getattr(sh, "points", [])]
    assert xs and max(xs) <= 1100                             # layer 2 was left out


def test_top_view_layer_roles_names_the_substrate_and_others():
    s = _session()
    s.library.layers.set_style(2, name="Substrate", fill=True, visible=True)
    roles = s.top_view_layer_roles()
    assert roles.get(2) == "substrate" and roles.get(0) == "conductor"   # default Route layer A



def test_top_view_writes_refdes_last_and_can_leave_them_out():
    from hlayout.core.shapes import Text
    s = _session()
    lib = s.library
    part = lib.new_cell("PART")
    part.add_rectangle(3, 0, 0, 2000, 1000)
    part.add_rectangle(61, -100, -100, 2100, 1100)
    lib.cells["TOP"].add_sref("PART", (10000, 0), label="U7")
    save_topview_settings(lib, {"result_layer": 1, "refdes_height_um": 500})
    s.top_view_from_settings()
    shapes = lib.cells["TOPVIEW"].shapes
    texts = [sh for sh in shapes if isinstance(sh, Text)]
    assert [t.text for t in texts] == ["U7"]
    assert shapes[-1] is texts[0]                       # written last
    t = texts[0]
    assert t.height == 500 and t.layer == 1
    cx = t.origin[0] + 0.6 * 500 * 2 / 2
    assert abs(cx - 11000) <= 1                         # centred on the part
    save_topview_settings(lib, {"refdes": False})
    s.top_view_from_settings()
    assert not any(isinstance(sh, Text) for sh in lib.cells["TOPVIEW"].shapes)
