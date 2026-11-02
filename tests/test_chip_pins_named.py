"""Chip Wizard pins are real pins (SRef.pin), may be names (E, B), and a
die whose back is a contact gets a pinned die-attach pad with a tab."""

import pytest

from hlayout.core.errors import LayoutError
from hlayout.core.library import Library
from hlayout.core.netlist import extract_netlist
from hlayout.scripting.api import Session
from hlayout.scripting.autoplace_session import cell_pins


def _session():
    lib = Library("T")
    pin = lib.new_cell("chip_pin")
    pin.add_rectangle(58, -50, -50, 50, 50)
    lib.new_cell("TOP")
    return Session(lib)


def test_named_pins_and_back_contact_tab():
    s = _session()
    s.create_chip_cell("DIE_NPN", 600, 600, 61, 500, 500, 20, attach_pad_layer=5,
                       attach_pad_margin=200, attach_pad_pin="C", attach_tab=600)
    s.place_cell("chip_pin", (-100, 200), label="E", pin="E")
    s.place_cell("chip_pin", (100, 200), label="B", pin="B")
    pad = [sh for sh in s.library.cells["DIE_NPN"].shapes if sh.layer == 5][0]
    assert pad.pin == "C"
    assert pad.bbox() == (-500, -1100, 500, 500)
    assert set(cell_pins(s.library, "DIE_NPN")) == {"C", "E", "B"}

    s.use_cell("TOP")
    s.sref("DIE_NPN", (5000, 5000), label="Q1")
    labels = sorted(p.label() for n in extract_netlist(s.library, s.library.cells["TOP"], [3, 5],
                                                       bond_layer=59, die_pad_layers=[58])
                    for p in n.pins)
    assert labels == ["Q1.B", "Q1.C", "Q1.E"]


@pytest.mark.parametrize("side,box", [("top", (-500, -500, 500, 1100)),
                                      ("left", (-1100, -500, 500, 500)),
                                      ("right", (-500, -500, 1100, 500))])
def test_tab_sides(side, box):
    s = _session()
    s.create_chip_cell("D", 600, 600, 61, 500, 500, 20, attach_pad_layer=5,
                       attach_pad_pin="D", attach_tab=600, attach_tab_side=side)
    pad = [sh for sh in s.library.cells["D"].shapes if sh.layer == 5][0]
    assert pad.bbox() == box and pad.pin == "D"


def test_back_contact_needs_attach_layer_and_valid_side():
    s = _session()
    with pytest.raises(LayoutError):
        s.create_chip_cell("D", 600, 600, 61, 500, 500, 20, attach_pad_pin="C")
    with pytest.raises(LayoutError):
        s.create_chip_cell("D2", 600, 600, 61, 500, 500, 20, attach_pad_layer=5,
                           attach_pad_pin="C", attach_tab=100, attach_tab_side="middle")


def test_unpinned_attach_pad_unchanged():
    s = _session()
    s.create_chip_cell("D", 600, 600, 61, 500, 500, 20, attach_pad_layer=5)
    pad = [sh for sh in s.library.cells["D"].shapes if sh.layer == 5][0]
    assert pad.bbox() == (-500, -500, 500, 500) and pad.pin == ""


def test_pin_name_parsing():
    from hlayout.gui.app import chip_pin_names
    assert chip_pin_names("E,B") == ["E", "B"]
    assert chip_pin_names(" E ; B  K ") == ["E", "B", "K"]
    assert chip_pin_names("") == []
