"""Remove Unused Bonds: a die pad the design doesn't use loses its wire
bond and bond finger -- on that die only (its own copy of the part)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.shapes import Path
from hlayout.io.techfile import save_bond_rules
from hlayout.scripting import Session

PAD, BOND, FING = 58, 59, 5


def _design():
    s = Session.new("UB")
    s.new_cell("DIE")
    save_bond_rules(s.library, {"layer": BOND, "width": 25, "pad": 75, "min_length": 300,
                                "max_length": 5000, "die_pad_layers": [PAD]})
    for pin, (x, y), land in (("1", (-400, 0), (-1900, 0)), ("2", (400, 0), (1900, 0)),
                              ("3", (0, 400), (0, 1900))):
        s.rect(PAD, x - 50, y - 50, x + 50, y + 50, pin=pin)
        s.wire_bond([(x, y), land], cell_name="DIE")
        s.rect(FING, land[0] - 150, land[1] - 150, land[0] + 150, land[1] + 150, pin=pin)
    s.new_cell("TOP")
    s.sref("DIE", (0, 0), label="U1")
    s.sref("DIE", (10000, 0), label="U2")
    s.set_net_names({"U1.1": "A", "U1.2": "B", "U2.1": "A", "U2.2": "B", "U2.3": "C"})
    s.clear_undo_history()
    return s


def _bonds(cell):
    return sorted(round(sh.points[-1][0]) * 10000 + round(sh.points[-1][1])
                  for sh in cell.shapes if sh.layer == BOND and isinstance(sh, Path))


def test_only_the_die_that_leaves_a_pad_unused_loses_its_bond_and_finger():
    s = _design()
    assert s.remove_unused_bonds(dry_run=True)["dies"] == {"U1": ["3"]}
    r = s.remove_unused_bonds()
    assert r["dies"] == {"U1": ["3"]} and r["cells"] == {"U1": "DIE_U1"} and r["bonds"] == 1
    lib = s.library
    refs = {x.label: x.cell_name for x in lib.cells["TOP"].srefs}
    assert refs == {"U1": "DIE_U1", "U2": "DIE"}
    u1, part = lib.cells["DIE_U1"], lib.cells["DIE"]
    assert len(_bonds(u1)) == 2 and len(_bonds(part)) == 3          # the shared part keeps all three
    assert not [sh for sh in u1.shapes if sh.layer == FING and sh.pin == "3"]
    assert [sh for sh in u1.shapes if sh.layer == PAD and sh.pin == "3"]   # the die pad stays
    balls = [sh for sh in u1.shapes if sh.layer == BOND and not isinstance(sh, Path)]
    assert len(balls) == 4                                           # two per remaining wire
    assert s.remove_unused_bonds()["bonds"] == 0                     # nothing left to take off
    landings = {tuple(map(round, l[:2])) for l in s.bond_landings("TOP") if l[2]}
    assert (0, 1900) not in landings and (10000, 1900) in landings


def test_one_undo_puts_it_back():
    s = _design()
    s.remove_unused_bonds()
    s.undo()
    assert {x.label: x.cell_name for x in s.library.cells["TOP"].srefs} == {"U1": "DIE", "U2": "DIE"}


def test_refuses_without_net_names():
    s = _design()
    s.set_net_names({}, replace=True)
    with pytest.raises(Exception):
        s.remove_unused_bonds()


def test_unusedbonds_command():
    pytest.importorskip("tkinter")
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _design()
    ci = CommandInterpreter(s, ScriptConsole(s))
    assert ci.run("UNUSEDBONDS") == "1 unused bond(s): U1: 3"
    assert ci.run("UNUSEDBONDS REMOVE") == "1 removed bond(s): U1: 3"
    assert ci.run("UNUSEDBONDS") == "no unused bonds"
