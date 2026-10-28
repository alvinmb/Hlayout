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



def test_top_view_refdes_sits_on_the_part_centroid_not_the_floated_label():
    from hlayout.core.shapes import Text
    s = _session()
    lib = s.library
    part = lib.new_cell("PART")
    part.add_rectangle(3, 0, 0, 2000, 1000)
    part.add_text(62, (5000, 5000), "U7", height=100)        # baked label off to one side
    ref = lib.cells["TOP"].add_sref("PART", (10000, 0), rotation=90, label="U7")
    ref.label_offset = (3000, 3000)                           # dragged on the canvas
    save_topview_settings(lib, {"result_layer": 1, "refdes_height_um": 500, "excluded": [62]})
    s.top_view_from_settings()
    t = [sh for sh in lib.cells["TOPVIEW"].shapes if isinstance(sh, Text)][0]
    from hlayout.core.library import _transform_bbox
    bx0, by0, bx1, by1 = _transform_bbox((0, 0, 2000, 1000), ref)   # body only, not the text
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    assert abs(t.origin[0] + 0.6 * 500 * 2 / 2 - cx) <= 1
    assert abs(t.origin[1] + 500 / 2 - cy) <= 1


def test_top_view_die_with_its_own_bonds_hides_the_conductor_under_it():
    """A chip-and-wire die part (body, pads and its own wire bonds drawn
    flat in one cell, no placed pins) is a solid component: a conductor
    track running under the die is hidden there."""
    from hlayout.io.techfile import load_bond_rules, save_bond_rules
    s = _session()
    lib = s.library
    bond = 40
    rules = load_bond_rules(lib)
    rules["layer"] = bond
    save_bond_rules(lib, rules)
    die = lib.new_cell("DIE_X")
    die.add_rectangle(20, -700, -700, 700, 700)               # die body
    die.add_rectangle(21, -600, 500, -500, 600)               # die pad
    die.add_path(bond, [(-550, 550), (-1500, 1500)], 25)       # its own bond
    s.place_cell("DIE_X", (5000, 5000))
    s.cell.add_rectangle(0, 3000, 4900, 7000, 5100)           # track under the die
    s.mask_op_top_view(9, exclude_layers=[2], out_cell="TV")
    xs = sorted({p[0] for sh in lib.cells["TV"].shapes for p in sh.points
                 if 4900 <= p[1] <= 5100 and getattr(sh, "layer", 9) == 9})
    assert not [x for x in xs if 4400 < x < 5600]             # nothing drawn under the body


def test_top_view_epoxy_wider_than_the_die_shows_and_hides_the_conductor():
    """Die-attach epoxy (a layer named Epoxy) is its own tier under the
    part: where the fillet spreads past the die its edge is drawn and the
    conductor under it is hidden."""
    from hlayout.io.techfile import load_bond_rules, save_bond_rules
    s = _session()
    lib = s.library
    rules = load_bond_rules(lib)
    rules["layer"] = 40
    save_bond_rules(lib, rules)
    lib.layers.set_style(20, name="Epoxy", fill=False, visible=False)
    die = lib.new_cell("DIE_E")
    die.add_rectangle(21, -700, -700, 700, 700)               # die body
    die.add_rectangle(20, -1200, -1200, 1200, 1200)           # wider epoxy fillet
    die.add_path(40, [(-550, 550), (-1500, 1500)], 25)
    s.place_cell("DIE_E", (5000, 5000))
    s.cell.add_rectangle(0, 3000, 4900, 7000, 5100)           # track under it
    assert s.top_view_layer_roles().get(20) == "epoxy"
    s.mask_op_top_view(9, exclude_layers=[2], out_cell="TV")
    pts = [p for sh in lib.cells["TV"].shapes for p in sh.points]
    xs = sorted({p[0] for p in pts if 4900 <= p[1] <= 5100})
    assert not [x for x in xs if 3900 < x < 6100]             # hidden under the epoxy
    assert any(p[0] == 3800 for p in pts) and any(p[0] == 4300 for p in pts)  # epoxy + die edges


def test_top_view_draws_bonds_as_bold_lines_on_every_bond_layer():
    """Bond wires come out as one line of the bond width along the wire
    -- on the Wire Bond layer AND any other layer named for bonds (a
    header-bond layer) -- not as the hairline outline of the real wire."""
    from hlayout.core.shapes import Path
    from hlayout.io.techfile import save_topview_settings
    s = _session()
    lib = s.library
    lib.layers.set_style(41, name="Header_wire_Bond", fill=True, visible=True)
    s.cell.add_path(41, [(500, 500), (500, 4000)], 50)
    assert s.top_view_layer_roles().get(41) == "bond"
    save_topview_settings(lib, {"result_layer": 9, "excluded": [2], "bond_width_um": 250})
    s.top_view_from_settings()
    bonds = [sh for sh in lib.cells["TOPVIEW"].shapes
             if isinstance(sh, Path) and sh.width == 250]
    assert [list(map(tuple, b.points)) for b in bonds] == [[(500, 500), (500, 4000)]]
    save_topview_settings(lib, {"bond_width_um": 0})
    s.top_view_from_settings()
    assert not [sh for sh in lib.cells["TOPVIEW"].shapes if getattr(sh, "width", 0) == 250]
