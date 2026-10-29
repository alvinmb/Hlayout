import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.dejog import remove_jogs
from hlayout.core.library import Library
from hlayout.core.shapes import Path


def _cell():
    lib = Library("t")
    c = lib.new_cell("TOP")
    return lib, c


def _tracks(c, layer=3):
    return [list(map(tuple, p.points)) for p in c.shapes if isinstance(p, Path) and p.layer == layer]


def test_jog_in_the_middle_of_a_track_goes():
    lib, c = _cell()
    c.add_rectangle(3, -500, -500, 500, 500)                      # pad
    c.add_rectangle(3, 9500, 1500, 10500, 2500)                   # pad
    c.add_path(3, [(0, 0), (3000, 0), (3000, 1000), (6000, 1000), (6000, 1300),
                   (8000, 1300), (8000, 2000), (10000, 2000)], 200)
    r = remove_jogs(lib, c, (3, 5), clearance=200)
    assert r["jogs"] >= 1
    (pts,) = _tracks(c)
    assert pts[0] == (0, 0) and pts[-1] == (10000, 2000)
    assert len(pts) < 8                                           # fewer bends


def test_end_slides_inside_its_pad():
    lib, c = _cell()
    c.add_rectangle(3, -450, -450, 450, 450)                      # 0.9 mm pad
    c.add_rectangle(3, 4800, -200, 5200, 500)
    c.add_path(3, [(0, 0), (2000, 0), (2000, 150), (5000, 150)], 200)
    r = remove_jogs(lib, c, (3, 5), clearance=200)
    assert r["jogs"] == 1
    (pts,) = _tracks(c)
    assert pts == [(0, 150), (5000, 150)]


def test_jog_kept_when_the_move_would_hit_another_net():
    lib, c = _cell()
    c.add_rectangle(3, -500, -500, 500, 500)
    c.add_rectangle(3, 9500, -300, 10500, 700)
    c.add_path(3, [(0, 0), (0, 2000), (4000, 2000), (4000, 2300), (8000, 2300),
                   (8000, 200), (10000, 200)], 200)
    # other nets' copper just clear of each run: sliding the lower run up
    # or the upper run down would each bring a run within the clearance
    c.add_rectangle(3, 1000, 2350 + 300, 3500, 2900)              # above the lower run
    c.add_rectangle(3, 4500, 1600, 7500, 1700)                    # below the upper run
    before = _tracks(c)
    r = remove_jogs(lib, c, (3, 5), clearance=300)
    after = _tracks(c)
    assert all(p[0] == (0, 0) and p[-1] == (10000, 200) for p in after)
    # whichever side moved, nothing now comes within the clearance of the block
    assert r["jogs"] == 0 and after == before


def test_jog_kept_when_something_joins_the_moving_segment():
    lib, c = _cell()
    c.add_rectangle(3, -500, -500, 500, 500)
    c.add_rectangle(3, 9500, -300, 10500, 700)
    c.add_path(3, [(0, 0), (0, 2000), (4000, 2000), (4000, 2300), (8000, 2300),
                   (8000, 200), (10000, 200)], 200)
    c.add_path(3, [(2000, 2000), (2000, 5000)], 200)              # a branch off the lower run
    c.add_path(3, [(6000, 2300), (6000, 5000)], 200)              # and off the upper run
    r = remove_jogs(lib, c, (3, 5), clearance=200)
    assert r["jogs"] == 0


def test_jog_across_the_join_of_two_tracks():
    lib, c = _cell()
    c.add_rectangle(3, -500, -500, 500, 500)
    c.add_rectangle(3, 9500, 1500, 10500, 2500)
    c.add_path(3, [(0, 0), (0, 1000), (4000, 1000)], 200)
    c.add_path(3, [(4000, 1000), (4000, 1250), (8000, 1250), (8000, 2000), (10000, 2000)], 200)
    r = remove_jogs(lib, c, (3, 5), clearance=200)
    assert r["jogs"] == 2                       # the one at the join, and the 0.75 mm one
    tr = _tracks(c)
    assert len(tr) == 1 and tr[0][0] == (0, 0) and tr[0][-1] == (10000, 2000)


def test_session_remove_jogs_and_freeroute_dejog_command():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.io.techfile import load_route_rules, save_route_rules
    from hlayout.scripting import Session
    lib, c = _cell()
    rr = load_route_rules(lib)
    rr["layer_a"], rr["layer_b"] = 3, 5
    save_route_rules(lib, rr)
    lib.top_cell_name = "TOP"
    c.add_rectangle(3, -500, -500, 500, 500)
    c.add_rectangle(3, 9500, 1500, 10500, 2500)
    c.add_path(3, [(0, 0), (3000, 0), (3000, 1000), (6000, 1000), (6000, 1300),
                   (8000, 1300), (8000, 2000), (10000, 2000)], 200)
    s = Session(lib)
    s.use_cell("TOP")
    out = CommandInterpreter(s, console=None).run("FREEROUTE DEJOG maxjog=500")
    assert out.startswith("removed 1 of"), out
    s.undo()
    assert len(_tracks(s.library.cells["TOP"])[0]) == 8
    assert s.freerouting_rules()["remove_jogs"] is True
