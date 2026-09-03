import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.errors import LayoutError
from hlayout.core.library import Library
from hlayout.core.shapes import Path
from hlayout.io.techfile import (
    DEFAULT_BOND_RULES,
    load_and_apply_techfile,
    load_bond_rules,
    save_bond_rules,
    save_techfile,
)
from hlayout.scripting import Session

BOND = 6


def _design():
    """Two conductor pads on L3 and a die-pin pad on L58, room for a
    ~2 mm bond between the L3 pads."""
    s = Session.new("DIE")
    s.new_cell("TOP")
    s.rect(3, 0, 0, 100, 100, pin="A")
    s.rect(3, 2000, 0, 2100, 100, pin="B")
    save_bond_rules(s.library, {"layer": BOND, "width": 30, "pad": 100,
                                "min_length": 200, "max_length": 4000})
    s.clear_undo_history()
    return s


def _bonds(s):
    return [sh for sh in s.cell.shapes if isinstance(sh, Path) and sh.layer == BOND]


# --- tech rules ------------------------------------------------------

def test_bond_rules_default_when_absent():
    assert load_bond_rules(Library("X")) == DEFAULT_BOND_RULES


def test_bond_rules_round_trip_through_a_full_techfile(tmp_path):
    lib = Library("X")
    save_bond_rules(lib, {"layer": 6, "width": 25, "pad": 90,
                          "min_length": 150, "max_length": 3500})
    assert load_bond_rules(lib) == {
        "layer": 6, "width": 25, "pad": 90,
        "min_length": 150, "max_length": 3500,
    }
    path = str(tmp_path / "t.json")
    save_techfile(lib, path)
    lib2 = Library("Y")
    load_and_apply_techfile(lib2, path)
    assert load_bond_rules(lib2) == load_bond_rules(lib)


# --- drawing -------------------------------------------------------

def test_wire_bond_needs_a_configured_layer():
    s = Session.new("X")
    s.new_cell("TOP")
    with pytest.raises(LayoutError):
        s.wire_bond([(0, 0), (1000, 0)])


def test_wire_bond_draws_an_o_bar_o_figure():
    s = _design()
    s.wire_bond([(50, 50), (2050, 50)])
    kinds = [type(sh).__name__ for sh in s.cell.shapes if sh.layer == BOND]
    # two end balls (circles are stored as Polygons) + one connecting Path
    assert kinds.count("Path") == 1
    assert kinds.count("Polygon") == 2


def test_wire_bond_rejects_a_span_shorter_than_min():
    s = _design()
    with pytest.raises(LayoutError, match="shorter than"):
        s.wire_bond([(50, 50), (120, 50)])
    assert _bonds(s) == []


def test_wire_bond_rejects_a_span_longer_than_max():
    s = _design()
    with pytest.raises(LayoutError, match="longer than"):
        s.wire_bond([(0, 0), (9000, 0)])


def test_wire_bond_rejects_wrong_point_count():
    s = _design()
    with pytest.raises(LayoutError):
        s.wire_bond([(0, 0)])
    with pytest.raises(LayoutError):
        s.wire_bond([(0, 0), (1000, 0), (2000, 0)])


def test_wire_bonds_may_not_cross():
    s = _design()
    s.wire_bond([(50, 50), (2050, 50)])
    with pytest.raises(LayoutError, match="cross"):
        s.wire_bond([(1000, -400), (1000, 500)])


def test_parallel_and_endpoint_sharing_bonds_are_allowed():
    s = _design()
    s.wire_bond([(50, 50), (2050, 50)])
    s.wire_bond([(50, 300), (2050, 300)])          # parallel, clear
    s.wire_bond([(50, 50), (50, 900)])             # shares an endpoint pad
    assert len(_bonds(s)) == 3


def test_wire_bond_is_one_undo_step():
    s = _design()
    s.wire_bond([(50, 50), (2050, 50)])
    assert _bonds(s)
    assert s.undo() is True
    assert _bonds(s) == []


# --- netlist -----------------------------------------------------

def test_netlist_folds_in_the_bond_layer_and_bridges_to_the_die_pin():
    s = Session.new("DIE")
    die = s.new_cell("DIE")
    s.rect(58, 0, 0, 80, 80, pin="3")
    s.new_cell("TOP")
    s.use_cell("TOP")
    s.sref("DIE", (0, 0), label="U1")
    s.rect(3, 2000, 0, 2200, 200, pin="A")
    save_bond_rules(s.library, {"layer": BOND, "width": 30, "pad": 150,
                                "min_length": 100, "max_length": 6000})
    s.wire_bond([(40, 40), (2100, 100)])

    joined = s.extract_netlist([3, 58])
    assert len(joined) == 1
    labels = sorted(p.label() for p in joined[0].pins)
    assert labels == ["?.A", "U1.3"]

    split = s.extract_netlist([3, 58], include_bonds=False)
    assert len(split) == 2


# --- command bar ----------------------------------------------

def test_bond_command_draws_and_reports_span():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _design()
    interp = CommandInterpreter(s, ScriptConsole(s))
    out = interp.run("BOND 50,50 2050,50")
    assert "span" in out
    assert len(_bonds(s)) == 1


def test_bond_command_surfaces_a_crossing_error():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _design()
    interp = CommandInterpreter(s, ScriptConsole(s))
    interp.run("BOND 50,50 2050,50")
    out = interp.run("BOND 1000,-400 1000,500")
    assert "error" in out.lower() and "cross" in out.lower()


def test_netlist_command_bonds_opt_out():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _design()
    interp = CommandInterpreter(s, ScriptConsole(s))
    interp.run("BOND 50,50 2050,50")
    with_bonds = interp.run("NETLIST RUN 3")
    without = interp.run("NETLIST RUN 3 bonds=0")
    assert with_bonds != without
