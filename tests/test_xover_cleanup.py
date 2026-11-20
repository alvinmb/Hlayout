# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""remove_needless_crossovers(): re-route each net with a local crossover
with crossovers made dear; keep only what is better -- never a short, a
lost pad or a net in pieces."""
import os

import pytest

from hlayout.core import Library
from hlayout.scripting.api import Session
from hlayout.scripting import xover_cleanup as X

DATA = os.path.join(os.path.dirname(__file__), "data", "op_amp_routed.gds")


def test_find_crossovers_needs_a_contact_on_both_conductors_at_each_end():
    lib = Library("T")
    s = Session(lib)
    s.new_cell("TOP")
    c = lib.cells["TOP"]
    c.add_path(5, [(0, 0), (0, 2000)], width=300)                      # a bridge
    for y in (0, 2000):
        c.add_rectangle(3, -150, y - 150, 150, y + 150)
        c.add_rectangle(5, -150, y - 150, 150, y + 150)
    c.add_polygon(4, [(-450, 400), (450, 400), (450, 1600), (-450, 1600)], datatype=1)
    c.add_path(5, [(5000, 0), (5000, 2000)], width=300)                # COND2, no contacts
    xs = X.find_crossovers(s, "TOP", 3, 5, 4)
    assert len(xs) == 1 and xs[0]["strip"] is not None
    assert xs[0]["ends"] == ((0, 0), (0, 2000))


@pytest.mark.skipif(not os.path.exists(DATA), reason="routed op-amp fixture not present")
def test_needless_crossovers_go_and_nothing_else_breaks():
    from hlayout.io.sidecar import load_design
    from hlayout.io.techfile import load_net_names
    d = load_design(DATA)
    s = Session(d.library)
    s.use_cell("TOP")
    cell = s.library.cells["TOP"]

    def state():
        out = {}
        for n in sorted({v for v in load_net_names(s.library).values() if v}):
            t = s.highlight_net(n, cell_name="TOP", notify=False)
            out[n] = (sorted(t.pins), len(X._components(t, 3, 5, X._diel_union(cell, 4))))
        return out
    before, shorts = state(), len(s.find_shorts(cell_name="TOP"))
    n_before = len(X.find_crossovers(s, "TOP", 3, 5, 4))
    r = s.remove_needless_crossovers(cell_name="TOP")
    after = state()
    assert len(s.find_shorts(cell_name="TOP")) <= shorts
    for n, (pins, parts) in before.items():
        assert set(after[n][0]) >= set(pins), n
        assert after[n][1] <= parts, n
    gone = sum(x["crossovers"] for x in r["removed"])
    assert len(X.find_crossovers(s, "TOP", 3, 5, 4)) == n_before - gone
    assert gone >= 1                     # Net-(Q3-B) loses one at no extra cost
    s.undo()
    assert len(X.find_crossovers(s, "TOP", 3, 5, 4)) == n_before
