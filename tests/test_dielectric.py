import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from shapely.geometry import Point

from hlayout.core.errors import LayoutError
from hlayout.core.shapes import MAX_POLY_POINTS, Polygon, Rectangle
from hlayout.io.techfile import (
    DEFAULT_DIELECTRIC_RULES,
    load_dielectric_rules,
    save_dielectric_rules,
    save_techfile,
    load_and_apply_techfile,
)
from hlayout.core.library import Library
from hlayout.scripting import Session

DIE = 4
FILL_DT = Session.DIELECTRIC_FILL_DATATYPE   # 1


def _design(boundary=True):
    """A small design: two routing traces (L3, L5), a via window and a
    keep-out rect drawn straight on the dielectric layer (L4, datatype
    0), and -- unless boundary=False -- an enclosing L4 rectangle."""
    s = Session.new("DIE")
    s.new_cell("TOP")
    s.rect(3, -5000, -100, 5000, 100)      # L3 trace
    s.rect(5, -100, -5000, 100, 5000)      # L5 trace
    if boundary:
        s.rect(DIE, -6000, -6000, 6000, 6000)   # fill boundary (largest, encloses rest)
    s.rect(DIE, -150, -150, 150, 150)      # a via window at the origin
    s.rect(DIE, 3000, 3000, 4000, 3600)    # a keep-out rectangle
    s.clear_undo_history()
    return s


def _fill(s):
    return [sh for sh in s.cell.shapes
            if isinstance(sh, Polygon) and sh.layer == DIE and sh.datatype == FILL_DT]


def _inputs(s):
    return [sh for sh in s.cell.shapes
            if sh.layer == DIE and getattr(sh, "datatype", 0) == 0]


# --- tech rules --------------------------------------------------------

def test_dielectric_rules_default_when_absent():
    assert load_dielectric_rules(Library("X")) == DEFAULT_DIELECTRIC_RULES


def test_dielectric_rules_round_trip_and_survive_full_techfile(tmp_path):
    lib = Library("X")
    save_dielectric_rules(lib, {"layer": 4, "margin": 800, "clearance": 120,
                                "keepout_layers": [7, 19]})
    assert load_dielectric_rules(lib) == {
        "layer": 4, "margin": 800, "clearance": 120, "keepout_layers": [7, 19],
    }
    path = str(tmp_path / "t.json")
    save_techfile(lib, path)
    lib2 = Library("Y")
    load_and_apply_techfile(lib2, path)
    assert load_dielectric_rules(lib2)["keepout_layers"] == [7, 19]
    assert load_dielectric_rules(lib2)["clearance"] == 120


# --- generate / clear ------------------------------------------------

def test_generate_dielectric_requires_a_layer():
    s = _design()
    with pytest.raises(LayoutError):
        s.generate_dielectric()          # nothing configured, no layer= given


def test_generate_uses_the_enclosing_shape_as_boundary_and_the_rest_as_holes():
    s = _design(boundary=True)
    n = s.generate_dielectric(layer=DIE, clearance=100)
    fill = _fill(s)
    assert n == len(fill) > 0
    for p in fill:
        assert len(p.points) <= MAX_POLY_POINTS

    from hlayout.core.maskops import layer_to_shapely_flattened
    filled = layer_to_shapely_flattened(s.library, s.cell, DIE, datatype=FILL_DT)
    assert filled.covers(Point(-4000, -4000))              # substrate is filled
    assert not filled.intersects(Point(0, 0).buffer(1))    # via window is open
    assert not filled.intersects(Point(3500, 3300).buffer(1))  # keep-out is open
    # never spills past the boundary rectangle
    assert filled.bounds[0] >= -6000 - 1 and filled.bounds[2] <= 6000 + 1


def test_generate_falls_back_to_bbox_plus_margin_when_no_boundary_encloses():
    s = _design(boundary=False)          # only the via window + keep-out on L4
    n = s.generate_dielectric(layer=DIE, clearance=50, margin=1000)
    assert n > 0
    from hlayout.core.maskops import layer_to_shapely_flattened
    filled = layer_to_shapely_flattened(s.library, s.cell, DIE, datatype=FILL_DT)
    # covers the routing traces' extent + margin, holes still cut
    assert filled.covers(Point(-5000, 0))
    assert not filled.intersects(Point(0, 0).buffer(1))


def test_extra_keepout_layers_are_also_punched():
    s = _design(boundary=True)
    s.rect(19, -4000, -4000, -3000, -3200)   # exclusion zone on another layer
    s.generate_dielectric(layer=DIE, clearance=50, keepout_layers=[19])
    from hlayout.core.maskops import layer_to_shapely_flattened
    filled = layer_to_shapely_flattened(s.library, s.cell, DIE, datatype=FILL_DT)
    assert not filled.intersects(Point(-3500, -3600).buffer(1))


def test_generate_is_rerunnable_and_never_eats_its_inputs():
    s = _design(boundary=True)
    s.generate_dielectric(layer=DIE)
    first = len(_fill(s))
    n_inputs = len(_inputs(s))
    assert n_inputs == 3                       # boundary + window + keep-out
    s.generate_dielectric(layer=DIE)           # re-run
    assert len(_fill(s)) == first              # replaced, not stacked
    assert len(_inputs(s)) == n_inputs         # inputs untouched


def test_clear_dielectric_removes_only_the_fill():
    s = _design(boundary=True)
    s.generate_dielectric(layer=DIE)
    assert _fill(s)
    removed = s.clear_dielectric(layer=DIE)
    assert removed > 0
    assert _fill(s) == []
    assert len(_inputs(s)) == 3                # boundary + window + keep-out kept
    assert any(sh.layer == 3 for sh in s.cell.shapes)   # routing untouched


def test_clear_dielectric_all_datatypes_wipes_the_layer():
    s = _design(boundary=True)
    s.generate_dielectric(layer=DIE)
    s.clear_dielectric(layer=DIE, all_datatypes=True)
    assert not any(sh.layer == DIE for sh in s.cell.shapes)


def test_generate_then_clear_are_each_one_undo_step():
    s = _design(boundary=True)
    s.generate_dielectric(layer=DIE)
    assert _fill(s)
    assert s.undo() is True
    assert _fill(s) == []
    assert len(_inputs(s)) == 3


# --- command bar ---------------------------------------------------

def test_dielectric_command_generate_and_clear():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _design(boundary=True)
    interp = CommandInterpreter(s, ScriptConsole(s))

    out = interp.run(f"DIELECTRIC layer={DIE} clearance=100")
    assert "polygon(s) written" in out
    assert _fill(s)

    out2 = interp.run(f"DIELECTRIC CLEAR layer={DIE}")
    assert "removed" in out2
    assert _fill(s) == []
    assert len(_inputs(s)) == 3

# --- no drawn boundary: the I/O pad ring and the substrate bound it ----

def _module(pads=True):
    """A 20 x 20 mm substrate (L57, the route boundary) with an I/O pad
    on each edge (L3, pin-labelled, 1.6 mm deep, 0.5 mm from the edge),
    a trace and a via window -- but no fill boundary on L4."""
    from hlayout.io.techfile import load_route_rules, save_route_rules
    s = Session.new("MOD")
    s.new_cell("TOP")
    s.rect(57, -10000, -10000, 10000, 10000)
    rr = load_route_rules(s.library)
    rr.update(layer_a=3, layer_b=5, boundary_layer=57)
    save_route_rules(s.library, rr)
    if pads:
        for k, (x0, y0, x1, y1) in enumerate([(-800, 7900, 800, 9500), (-800, -9500, 800, -7900),
                                              (-9500, -800, -7900, 800), (7900, -800, 9500, 800)], 1):
            s.cell.add_rectangle(3, x0, y0, x1, y1).pin = str(k)
    s.rect(3, -5000, -100, 5000, 100)
    s.rect(DIE, -150, -150, 150, 150)       # via windows only
    s.rect(DIE, 2000, -150, 2300, 150)
    s.clear_undo_history()
    return s


def _extent(s):
    bb = [p.bbox() for p in _fill(s)]
    return (min(b[0] for b in bb), min(b[1] for b in bb), max(b[2] for b in bb), max(b[3] for b in bb))


def test_fill_without_a_boundary_stops_at_the_io_pads_inner_strip():
    s = _module()
    s.set_freerouting_rules(io_entry_depth=300)
    s.generate_dielectric(layer=DIE, margin=1000)
    # pads' inner edges at +-7900, overlapped by the 300 um entry strip
    assert _extent(s) == (-8200, -8200, 8200, 8200)


def test_fill_without_a_boundary_or_pads_never_leaves_the_substrate():
    s = _module(pads=False)
    s.rect(3, -9900, 9000, 9900, 9900)      # copper near the edge: bbox + margin overshoots
    s.generate_dielectric(layer=DIE, margin=1000)
    x0, y0, x1, y1 = _extent(s)
    assert x0 >= -10000 and y0 >= -10000 and x1 <= 10000 and y1 <= 10000
