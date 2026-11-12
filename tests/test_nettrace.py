# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.errors import LayoutError
from hlayout.scripting import Session, ScriptConsole
from hlayout.gui.commands import CommandInterpreter
from hlayout.io.techfile import (load_dielectric_rules, load_route_rules,
                                 save_dielectric_rules, save_route_rules)


def _board(via=True):
    """Layer 3 trace from module pad IO.1 under a layer-5 part pad (U1.1),
    dielectric (layer 4) everywhere except an optional via window at the
    trace's far end, where a layer-5 trace carries on to U1.2."""
    s = Session.new("NT")
    s.new_cell("PART")
    s.rect(5, -200, -200, 200, 200, pin="1")
    s.rect(5, 1800, -200, 2200, 200, pin="2")
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr["layer_a"] = 3; rr["layer_b"] = 5
    save_route_rules(s.library, rr)
    dr = load_dielectric_rules(s.library); dr["layer"] = 4; dr["clearance"] = 0
    save_dielectric_rules(s.library, dr)
    s.rect(3, -5200, -400, -4400, 400, pin="1")              # module I/O pad
    s.path(3, [(-4400, 0), (4000, 0)], 300)                  # passes under U1.1
    s.rect(4, -6000, -3000, 6000, 3000)                      # dielectric boundary
    if via:
        s.drop_via(4000, 0, 3, 5, size=400, dielectric_layer=4, dielectric_window=600)
    s.path(5, [(4000, 0), (4000, 1500), (2000, 1500), (2000, 0)], 300)
    s.sref("PART", (0, 0)).label = "U1"
    s.generate_dielectric()
    return s


def test_buried_trace_under_a_pad_is_not_connected_to_it():
    s = _board()
    tr = s.highlight_net("U1.1")
    assert tr.pins == ["U1.1"]               # the layer-3 trace below doesn't touch it


def test_via_window_joins_the_two_layers():
    s = _board()
    tr = s.highlight_net("IO.1")
    assert tr.pins == ["IO.1", "U1.2"]
    assert tr.layers == [3, 5]
    assert s.highlight_net((3000, 1500)).pins == ["IO.1", "U1.2"]   # click on the L5 trace


def test_no_via_no_connection():
    s = _board(via=False)
    assert s.highlight_net("IO.1").pins == ["IO.1"]


def test_highlight_errors_and_clear():
    s = _board()
    with pytest.raises(LayoutError):
        s.highlight_net((9000, 9000))
    with pytest.raises(LayoutError):
        s.highlight_net("U9.9")
    s.highlight_net("U1.2")
    assert s.net_highlight is not None and s.net_highlight.pad_boxes
    s.clear_net_highlight()
    assert s.net_highlight is None


def test_hilite_command():
    s = _board()
    ci = CommandInterpreter(s, ScriptConsole(s))
    out = ci.run("HILITE U1.2")
    assert "2 pad(s)" in out and "IO.1" in out
    assert "U1.2" in ci.run("HILITE 3000,1500")
    assert "cleared" in ci.run("HILITE OFF")
    assert s.net_highlight is None


def test_highlight_reports_the_design_net_name_and_whats_missing():
    s = _board()
    s.set_net_names({"IO.1": "SIG", "U1.2": "SIG", "U1.1": "OTHER"})
    tr = s.highlight_net("U1.2")
    assert tr.name == "SIG" and tr.missing == [] and tr.fragments == 1
    tr = s.highlight_net("U1.1")
    assert tr.name == "OTHER"
    s.set_net_names({"IO.1": "SIG", "U1.2": "SIG", "U1.1": "SIG"})
    tr = s.highlight_net("U1.1")
    assert tr.missing == ["IO.1", "U1.2"]          # not joined to the piece picked ...
    assert tr.pins == ["IO.1", "U1.1", "U1.2"] and tr.fragments == 2   # ... but all shown
    # by net name: every fragment of the net, joined or not
    tr = s.highlight_net("SIG")
    assert tr.pins == ["IO.1", "U1.1", "U1.2"] and tr.fragments == 2


def test_connected_pads_of_two_named_nets_report_a_short():
    s = _board()
    s.set_net_names({"IO.1": "A", "U1.2": "B"})
    assert s.highlight_net("IO.1").name == "A + B (SHORT)"
    s.set_net_names({})
    assert s.highlight_net("IO.1").name == "unnamed"


def test_import_net_names_csv_and_command(tmp_path):
    p = tmp_path / "nets.csv"
    p.write_text("Ref,Pin,Net\r\nU1,2,SIG\r\n,1,X\r\n")
    s = _board()
    assert s.import_net_names(str(p)) == 1
    ci = CommandInterpreter(s, ScriptConsole(s))
    out = ci.run("HILITE U1.2")
    assert out.startswith("net SIG:")
    q = tmp_path / "pads.csv"
    q.write_text("pad,net\r\nIO.1,SIG\r\n")
    assert "2 named" in ci.run(f"NETNAMES IMPORT {q} merge=1")
    assert "net SIG" in ci.run("HILITE SIG")


def test_miss_messages_and_click_tolerance():
    s = _board()
    with pytest.raises(LayoutError) as e:
        s.highlight_net((0, -2500))
    assert "no copper" not in str(e.value).lower() and "click on a pad or track" in str(e.value)
    with pytest.raises(LayoutError) as e:
        s.highlight_net("NOPE")
    assert "no pad or net called 'NOPE'" in str(e.value)
    # 0.1 mm beside the L5 trace (half-width 150): missed exactly, picked with tolerance
    with pytest.raises(LayoutError):
        s.highlight_net((3000, 1750))
    assert s.highlight_net((3000, 1750), tol=200).pins == ["IO.1", "U1.2"]


def test_without_generated_fill_only_via_windows_connect():
    s = _board()
    s.clear_dielectric()
    assert s.highlight_net("U1.1").pins == ["U1.1"]          # no overlap short
    assert s.highlight_net("IO.1").pins == ["IO.1", "U1.2"]  # via window still joins


def test_a_via_cell_dropped_after_the_fill_still_joins_the_layers():
    """The Route tool's Space places a via CELL (pads + its own window);
    the fill made before it still covers the spot until it is redone --
    the window must count as an opening anyway."""
    s = _board(via=False)
    s.new_cell("VIA35")
    s.rect(3, -200, -200, 200, 200)
    s.rect(5, -200, -200, 200, 200)
    s.rect(4, -300, -300, 300, 300)
    s.use_cell("TOP")
    assert s.highlight_net("IO.1").pins == ["IO.1"]      # fill already generated
    s.sref("VIA35", (4000, 0))
    assert s.highlight_net("IO.1").pins == ["IO.1", "U1.2"]


def test_the_highlight_follows_a_moved_part():
    s = Session.new("FOLLOW")
    s.new_cell("PART")
    s.rect(3, -200, -200, 200, 200, pin="1")
    s.new_cell("TOP")
    rr = load_route_rules(s.library); rr["layer_a"] = 3; rr["layer_b"] = 5
    save_route_rules(s.library, rr)
    ref = s.sref("PART", (0, 0))
    ref.label = "U1"
    s.set_net_names({"U1.1": "SIG"})
    tr = s.highlight_net("U1.1")
    assert tr.pad_boxes[0][0] == -200
    ref.origin = (5000, 0)                         # dragged
    tr2 = s.retrace_net_highlight()
    assert tr2.pad_boxes[0][0] == 4800 and s.net_highlight is tr2 and tr2.name == "SIG"
