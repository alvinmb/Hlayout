# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""step_colour: a SOT model's moulded body black, its leads silver."""
import os
import re

import pytest

cq = pytest.importorskip("cadquery")

from hlayout.io.step_colour import (BODY_BLACK, LEAD_SILVER, colour_body_and_leads,
                                    colour_sot_models, is_coloured)


def _part(path, fused=False):
    body = cq.Solid.makeBox(2.8, 1.3, 0.9)
    leads = [cq.Solid.makeBox(0.43, 0.5, 0.12, cq.Vector(x, -0.5, 0)) for x in (0.2, 2.2)]
    shape = body.fuse(*leads) if fused else cq.Compound.makeCompound([body] + leads)
    cq.exporters.export(cq.Workplane().add(shape), str(path))
    return str(path)


def _colours(path):
    with open(path, encoding="latin-1") as f:
        t = f.read()
    return {tuple(round(float(v), 2) for v in m.split(","))
            for m in re.findall(r"COLOUR_RGB\('[^']*',([^)]*)\)", t.replace("\n", ""))}


def test_body_black_leads_silver(tmp_path):
    p = _part(tmp_path / "SOT-23.step")
    assert not is_coloured(p)
    r = colour_body_and_leads(p)
    assert r["solids"] == 3 and abs(r["body_volume"] - 2.8 * 1.3 * 0.9) < 1e-6
    assert _colours(p) == {tuple(round(c, 2) for c in BODY_BLACK),
                           tuple(round(c, 2) for c in LEAD_SILVER)}
    bb = cq.importers.importStep(p).val().BoundingBox()
    assert (round(bb.xlen, 3), round(bb.ylen, 3), round(bb.zlen, 3)) == (2.8, 1.8, 0.9)


def test_a_fused_model_is_refused(tmp_path):
    with pytest.raises(ValueError, match="one fused solid"):
        colour_body_and_leads(_part(tmp_path / "SOT-89.step", fused=True))


def test_colour_sot_models_skips_backups_and_other_names(tmp_path):
    a = _part(tmp_path / "SOT_23.step")
    os.makedirs(tmp_path / "_old")
    b = _part(tmp_path / "_old" / "SOT-23.step")
    c = _part(tmp_path / "sot__SOT-23.step")
    r = colour_sot_models(str(tmp_path))
    assert r["coloured"] == [a]
    assert not is_coloured(b) and not is_coloured(c)
