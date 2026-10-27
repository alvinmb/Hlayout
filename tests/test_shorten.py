"""Track shortening after routing (Session.shorten_tracks / core.shorten)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from shapely.geometry import LineString

from hlayout.core.shapes import Path
from hlayout.io.specctra import find_vias
from hlayout.io.techfile import (load_dielectric_rules, load_route_rules, save_dielectric_rules,
                                 save_route_rules)
from hlayout.scripting import Session


def _board(blocker=False):
    """IO.1 (left) and IO.2 (right) on the 1st conductor, joined by a
    staircase detour that hops layers through four vias -- what a
    one-direction-per-layer router leaves."""
    s = Session.new("SH")
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr["layer_a"] = 3; rr["layer_b"] = 5
    save_route_rules(s.library, rr)
    dr = load_dielectric_rules(s.library); dr["layer"] = 4; dr["clearance"] = 0
    save_dielectric_rules(s.library, dr)
    s.rect(4, -12000, -12000, 12000, 12000)
    s.rect(3, -10400, -400, -9600, 400, pin="1")
    s.rect(3, 9600, -400, 10400, 400, pin="2")
    s.set_freerouting_rules(clearance=200, signal_width=200, signal_via=300,
                            signal_via_window=400) if hasattr(s, "set_freerouting_rules") else None
    pts = [(-9600, 0), (-6000, 0)]
    s.path(3, pts, 200)
    s.drop_via(-6000, 0, 3, 5, size=300, dielectric_layer=4, dielectric_window=400)
    s.path(5, [(-6000, 0), (-6000, 6000)], 200)
    s.drop_via(-6000, 6000, 3, 5, size=300, dielectric_layer=4, dielectric_window=400)
    s.path(3, [(-6000, 6000), (6000, 6000)], 200)
    s.drop_via(6000, 6000, 3, 5, size=300, dielectric_layer=4, dielectric_window=400)
    s.path(5, [(6000, 6000), (6000, 0)], 200)
    s.drop_via(6000, 0, 3, 5, size=300, dielectric_layer=4, dielectric_window=400)
    s.path(3, [(6000, 0), (9600, 0)], 200)
    if blocker:
        # another net's track across the middle on BOTH layers
        s.rect(3, -500, -9000, 500, 3000, pin="3")
        s.rect(5, -500, -9000, 500, 3000)
    s.generate_dielectric()
    names = {"IO.1": "A", "IO.2": "A"}
    if blocker:
        names["IO.3"] = "B"
    s.set_net_names(names)
    s.clear_undo_history()
    return s


def _length(s):
    return sum(LineString(p.points).length for p in s.library.cells["TOP"].shapes
               if isinstance(p, Path) and p.layer in (3, 5))


def test_a_staircase_detour_is_straightened():
    s = _board()
    L0 = _length(s)
    r = s.shorten_tracks()
    assert r["tracks"] == 1 and r["after"] < r["before"]
    assert abs(_length(s) - 19200) < 1                            # IO.1 -> IO.2 straight
    assert find_vias(s.library.cells["TOP"], (3, 5)) == []        # straight on the 1st conductor
    assert s.highlight_net("IO.1").fragments == 1 and "IO.2" in s.highlight_net("IO.1").pins
    s.undo()
    assert abs(_length(s) - L0) < 1 and len(find_vias(s.library.cells["TOP"], (3, 5))) == 4


def test_the_new_route_keeps_clear_of_another_net():
    s = _board(blocker=True)
    s.shorten_tracks()
    top = s.library.cells["TOP"]
    blk = [sh for sh in top.shapes if getattr(sh, "pin", "") == "3"][0]
    from shapely.geometry import box
    bg = box(*blk.bbox())
    for p in top.shapes:
        if isinstance(p, Path) and p.layer == 3:
            assert LineString(p.points).buffer(p.width / 2).distance(bg) >= 199
    assert "IO.2" in s.highlight_net("IO.1").pins
    assert "IO.3" not in s.highlight_net("IO.1").pins


def test_freeroute_shorten_command():
    pytest.importorskip("tkinter")
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _board()
    out = CommandInterpreter(s, ScriptConsole(s)).run("FREEROUTE SHORTEN")
    assert out.startswith("shortened 1 track(s)")


def test_via_optimiser_keeps_a_via_on_a_finger_inside_a_part():
    """A via sitting on a bond finger that lives inside a placed die part:
    the part's copper is on that side, so the via must stay."""
    s = Session.new("VF")
    s.new_cell("DIE")
    s.rect(5, -200, -200, 200, 200, pin="1")                      # the finger, inside the part
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr["layer_a"] = 3; rr["layer_b"] = 5
    save_route_rules(s.library, rr)
    s.rect(3, -5400, -400, -4600, 400, pin="1")                   # IO.1 on the 1st conductor
    s.sref("DIE", (0, 0)).label = "U1"
    s.path(3, [(-4600, 0), (0, 0)], 200)
    s.drop_via(0, 0, 3, 5, size=300)
    s.set_net_names({"IO.1": "A", "U1.1": "A"})
    assert "U1.1" in s.highlight_net("IO.1").pins
    s.optimize_vias()
    assert len(find_vias(s.library.cells["TOP"], (3, 5))) == 1
    assert "U1.1" in s.highlight_net("IO.1").pins


def test_find_shorts_reports_two_nets_joined():
    s = _board(blocker=True)
    assert s.find_shorts() == []
    s.path(3, [(-6000, 0), (0, 0)], 200)          # net A's track run onto net B's pad
    sh = s.find_shorts()
    assert len(sh) == 1 and sh[0]["nets"] == ["A", "B"]


def test_shorts_command():
    pytest.importorskip("tkinter")
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    s = _board(blocker=True)
    ci = CommandInterpreter(s, ScriptConsole(s))
    assert ci.run("SHORTS").startswith("no shorts")
