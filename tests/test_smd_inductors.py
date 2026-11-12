# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples"))

import generate_smd_inductors as G


def test_every_inductor_has_numbered_metal_pads_with_epoxy_inside():
    lib = G.build()
    M = G._models()
    assert len(M.names()) == 23
    for n in M.names():
        c = lib.cells[n]
        pads = {sh.pin: sh.bbox() for sh in c.shapes if sh.layer == G.PAD_LAYER}
        epoxy = [sh.bbox() for sh in c.shapes if sh.layer == G.EPOXY_LAYER]
        assert sorted(pads) == ["1", "2"] and len(epoxy) == 2, n
        assert pads["1"][0] < pads["2"][0]                       # pin 1 on the -X end
        for e in epoxy:                                          # each deposit inside a pad
            assert any(p[0] < e[0] and p[1] < e[1] and e[2] < p[2] and e[3] < p[3]
                       for p in pads.values()), n
        L, W, _H, terms = M.footprint(n)
        g = int(round(G.PAD_GROW_MM * 1000))
        t = terms[0]
        want = (t[0] * 1000 - g, t[1] * 1000 - g, t[2] * 1000 + g, t[3] * 1000 + g)
        assert all(abs(a - b) <= 1 for a, b in zip(pads["1"], want)), n
    assert lib.layers[G.EPOXY_LAYER].name == "Epoxy"


def test_layers_can_be_moved():
    lib = G.build(pad_layer=5, epoxy_layer=21)
    c = lib.cells["L_SHLD_5030"]
    assert {sh.layer for sh in c.shapes} >= {5, 21} and 3 not in {sh.layer for sh in c.shapes}
