"""Connector/header ref-des beside pin 1 (hlayout.core.refdes,
Session.refdes_to_pin1, REFDES PIN1, auto on placement)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.refdes import is_connector_refdes, refdes_pin1_offset
from hlayout.gui.commands import CommandInterpreter
from hlayout.scripting import Session


def _header(s):
    """A 1x4 header: round-lead template placed four times along X with
    SRef pins 1..4, pin 1 at the left end."""
    s.new_cell("LEAD")
    s.use_cell("LEAD")
    s.circle(3, (0, 0), 400, segments=16)
    s.new_cell("HDR4")
    s.use_cell("HDR4")
    for k in range(4):
        s.sref("LEAD", (k * 2540, 0), pin=str(k + 1))
    s.new_cell("TOP")
    s.use_cell("TOP")


def _anchor(s, ref):
    from hlayout.core.library import _transform_bbox
    x1, y1, x2, y2 = _transform_bbox(s.library.cell_bbox(ref.cell_name), ref)
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    return cx + ref.label_offset[0], cy + ref.label_offset[1]


def test_connector_refdes_names():
    for good in ("J1", "j12", "P3", "X1", "CN2", "CON1", "HDR4"):
        assert is_connector_refdes(good)
    for bad in ("", "R1", "U1", "C3", "ML1", "JUMPER"):
        assert not is_connector_refdes(bad)


def test_connector_placed_with_label_gets_refdes_beside_pin1():
    s = Session.new("RD")
    _header(s)
    j = s.sref("HDR4", (10000, 5000), label="J1")
    ax, ay = _anchor(s, j)
    # pin 1 is the left end at (10000, 5000), pad radius 400: label 800 beyond it
    assert abs(ax - (10000 - 400 - 800)) <= 1 and abs(ay - 5000) <= 1
    r = s.sref("HDR4", (10000, 9000), label="U5")        # not a connector
    assert tuple(r.label_offset) == (0, 0)


def test_rotated_header_and_explicit_call():
    s = Session.new("RD")
    _header(s)
    j = s.sref("HDR4", (0, 0), rotation=180)            # unlabelled -> untouched
    assert tuple(j.label_offset) == (0, 0)
    j.label = "P2"
    assert s.refdes_to_pin1() == ["P2"]
    ax, ay = _anchor(s, j)
    # rotated 180: pin 1 is now the RIGHT end, at (0, 0)
    assert abs(ax - (400 + 800)) <= 1 and abs(ay) <= 1
    s.undo()                                           # one undo step
    assert tuple(s.library.cells["TOP"].srefs[0].label_offset) == (0, 0)


def test_refdes_pin1_command():
    s = Session.new("RD")
    _header(s)
    j = s.sref("HDR4", (0, 0))
    j.label = "J7"
    out = CommandInterpreter(s, console=None).run("REFDES PIN1 J7 gap=500")
    assert "J7" in out
    ax, _ = _anchor(s, j)
    assert abs(ax - (-400 - 500)) <= 1
    assert CommandInterpreter(s, console=None).run("REFDES").startswith("error")
