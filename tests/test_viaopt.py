"""Via optimisation after routing: Session.optimize_vias / hlayout.core.viaopt."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.shapes import Path
from hlayout.io.specctra import find_vias
from hlayout.io.techfile import (load_dielectric_rules, load_route_rules, save_dielectric_rules,
                                 save_route_rules)
from hlayout.scripting import Session


def _board():
    """Two I/O pads on the 1st conductor (layer 3) joined by a route that
    dog-legs through the 2nd conductor (layer 5) with two vias -- the
    pattern a one-direction-per-layer router leaves behind."""
    s = Session.new("VO")
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr["layer_a"] = 3; rr["layer_b"] = 5
    save_route_rules(s.library, rr)
    dr = load_dielectric_rules(s.library); dr["layer"] = 4; dr["clearance"] = 0
    save_dielectric_rules(s.library, dr)
    s.rect(4, -10000, -10000, 10000, 10000)                       # dielectric boundary
    s.rect(3, -8400, -400, -7600, 400, pin="1")                   # IO.1
    s.rect(3, 7600, 2600, 8400, 3400, pin="2")                    # IO.2
    s.path(3, [(-7600, 0), (0, 0)], 300)
    s.drop_via(0, 0, 3, 5, size=400, dielectric_layer=4, dielectric_window=600)
    s.path(5, [(0, 0), (0, 3000)], 300)
    s.drop_via(0, 3000, 3, 5, size=400, dielectric_layer=4, dielectric_window=600)
    s.path(3, [(0, 3000), (7600, 3000)], 300)
    s.generate_dielectric()
    s.set_net_names({"IO.1": "A", "IO.2": "A"})
    s.clear_undo_history()
    return s


def _connected(s, a="IO.1", b="IO.2"):
    return b in s.highlight_net(a).pins


def test_both_vias_of_a_dog_leg_come_out():
    s = _board()
    assert len(find_vias(s.library.cells["TOP"], (3, 5))) == 2 and _connected(s)
    r = s.optimize_vias()
    assert (r["before"], r["after"], r["moved"]) == (2, 0, 1)
    top = s.library.cells["TOP"]
    assert not [sh for sh in top.shapes if sh.layer == 5]          # nothing left on the 2nd conductor
    assert not [sh for sh in top.shapes if sh.layer == 4 and getattr(sh, "datatype", 0) == 0
                and sh.bbox()[2] - sh.bbox()[0] < 1000]             # windows gone too
    assert _connected(s)
    s.undo()                                                       # one undo step
    assert len(find_vias(s.library.cells["TOP"], (3, 5))) == 2


def test_another_net_in_the_way_keeps_the_vias():
    s = _board()
    s.rect(3, -1000, 1200, 1000, 1800, pin="3")                    # IO.3, on the dog-leg's path
    s.set_net_names({"IO.1": "A", "IO.2": "A", "IO.3": "B"})
    r = s.optimize_vias()
    assert r["after"] == 2 and _connected(s)
    assert "IO.3" not in s.highlight_net("IO.1").pins


def test_clearance_is_kept():
    s = _board()
    s.rect(3, 300, 1200, 900, 1800, pin="3")                       # 150 um clear of the leg's edge
    s.set_net_names({"IO.1": "A", "IO.2": "A", "IO.3": "B"})
    assert s.optimize_vias(clearance=100)["after"] == 0
    s = _board()
    s.rect(3, 300, 1200, 900, 1800, pin="3")
    s.set_net_names({"IO.1": "A", "IO.2": "A", "IO.3": "B"})
    assert s.optimize_vias(clearance=200)["after"] == 2


def test_a_via_under_a_pad_stays():
    s = _board()
    s.rect(5, -300, 2700, 300, 3300, pin="9")                      # a 2nd-conductor pad on the via
    assert s.optimize_vias()["after"] >= 1


def test_max_flip_limits_the_move():
    s = _board()
    assert s.optimize_vias(via_optimize_max_flip=1000)["after"] == 2   # the 3 mm leg is too long
    assert s.optimize_vias(via_optimize_max_flip=5000)["after"] == 0


def test_a_moved_track_changes_layer_not_shape():
    s = _board()
    leg = next(sh for sh in s.library.cells["TOP"].shapes if isinstance(sh, Path) and sh.layer == 5)
    pts = list(leg.points)
    s.optimize_vias()
    assert leg.layer == 3 and list(leg.points) == pts


def test_freeroute_viaopt_command():
    pytest.importorskip("tkinter")
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _board()
    out = CommandInterpreter(s, ScriptConsole(s)).run("FREEROUTE VIAOPT")
    assert out.startswith("via optimisation: 2 -> 0 via(s)")


def test_no_dielectric_layer_a_crossing_track_still_blocks_the_move():
    """Without a dielectric the net tracer joins any two crossing tracks,
    which used to make every net look like one: moving the leg onto the
    1st conductor would then short it into the crossing net."""
    s = Session.new("VO2")
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr["layer_a"] = 3; rr["layer_b"] = 5
    save_route_rules(s.library, rr)
    s.rect(3, -8400, -400, -7600, 400, pin="1")
    s.rect(3, 7600, 2600, 8400, 3400, pin="2")
    s.path(3, [(-7600, 0), (0, 0)], 300)
    s.drop_via(0, 0, 3, 5, size=400)
    s.path(5, [(0, 0), (0, 3000)], 300)
    s.drop_via(0, 3000, 3, 5, size=400)
    s.path(3, [(0, 3000), (7600, 3000)], 300)
    s.rect(3, -6400, 1100, -5600, 1900, pin="3")                   # net B, crossing the leg on L3
    s.rect(3, 5600, 1100, 6400, 1900, pin="4")
    s.path(3, [(-5600, 1500), (5600, 1500)], 300)
    s.set_net_names({"IO.1": "A", "IO.2": "A", "IO.3": "B", "IO.4": "B"})
    r = s.optimize_vias()
    assert r["after"] == 2
    leg = [sh for sh in s.library.cells["TOP"].shapes if isinstance(sh, Path) and sh.layer == 5]
    assert len(leg) == 1


def test_a_stub_to_a_pad_on_its_own_layer_is_not_moved_away_from_it():
    """The stub from a via to an I/O pad on the 1st conductor ends on a
    2nd-conductor track of the same net too: moving it up would still
    'land', but would leave the pad behind."""
    s = Session.new("VO3")
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr["layer_a"] = 3; rr["layer_b"] = 5
    save_route_rules(s.library, rr)
    s.rect(3, 1100, -400, 1900, 400, pin="1")                     # IO.1 on L3
    s.rect(5, 5600, -400, 6400, 400, pin="2")                     # IO.2 on L5
    s.path(5, [(6000, 0), (0, 0)], 300)                           # L5 track passes over IO.1's spot
    s.drop_via(0, 0, 3, 5, size=400)
    s.path(3, [(0, 0), (1500, 0)], 300)                           # L3 stub back to IO.1
    s.set_net_names({"IO.1": "A", "IO.2": "A"})
    assert s.optimize_vias()["after"] == 1
    assert "IO.2" in s.highlight_net("IO.1").pins


def test_a_stub_from_a_finger_inside_a_die_part_keeps_its_via():
    """The 2nd-conductor stub from a die part's own bond finger to a via:
    moving it onto the 1st conductor would leave the finger (copper inside
    the part, not on the board) cut off -- even with a same-net 1st-
    conductor track passing close to the finger."""
    s = Session.new("VO4")
    s.new_cell("DIE")
    s.rect(5, -150, -150, 150, 150, pin="1")                      # the part's bond finger (L5)
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr["layer_a"] = 3; rr["layer_b"] = 5
    save_route_rules(s.library, rr)
    s.sref("DIE", (0, 0), label="U1")
    s.rect(3, 7600, 2600, 8400, 3400, pin="2")                    # IO.2 on L3
    s.path(5, [(0, 0), (0, 300), (700, 300)], 300)                # stub, finger -> via
    s.drop_via(700, 300, 3, 5, size=300)
    s.path(3, [(700, 300), (-100, 300), (-100, 3000), (7600, 3000)], 300)   # passes 0.1 mm from the finger
    s.set_net_names({"U1.1": "A", "IO.2": "A"})
    r = s.optimize_vias()
    assert r["after"] == 1
    stub = [sh for sh in s.library.cells["TOP"].shapes if isinstance(sh, Path) and sh.points[0] == (0, 0)]
    assert stub and stub[0].layer == 5
