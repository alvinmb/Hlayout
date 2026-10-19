import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.scripting import Session
from hlayout.io import solidcraftio as S
from hlayout.io.solidcraft_mcp import SolidCraftClient, find_solidcraft


def _hybrid():
    """1st conductor (L3) with a hole, a die cell with a pad, one bond."""
    from hlayout.io.techfile import load_bond_rules, save_bond_rules, save_layer_thickness
    s = Session.new("SC")
    s.new_cell("TOP")
    s.rect(57, -5000, -5000, 5000, 5000)                       # substrate
    s.library.layers[57].name = "Substrate"
    s.cell.add_polygon(3, [(-4000, -4000), (4000, -4000), (4000, -3000), (-4000, -3000)])
    s.rect(3, 2000, -1000, 3000, 1000)
    save_layer_thickness(s.library, {"3": 12, "57": 635})
    br = load_bond_rules(s.library)
    br.update(layer=59, die_pad_layers=[58])
    save_bond_rules(s.library, br)
    s.create_chip_cell("DIE", 1500, 1500, graphics_layer=61, epoxy_length=1400,
                       epoxy_width=1400, epoxy_layer=20)
    s.library.cells["DIE"].add_rectangle(58, -700, -50, -600, 50, pin="1")
    s.use_cell("TOP")
    s.sref("DIE", (0, 0), label="U1")
    s.wire_bond([(-650, 0), (2500, 0)], layer=59, width=25, pad=75)
    return s


def test_build_batches_makes_substrate_films_parts_and_bonds():
    s = _hybrid()
    b = S.build_batches(s.library, "TOP")
    labels = [l for l, _c, _f in b]
    assert labels[0] == "Substrate"
    assert any(l.startswith("Layer 3") for l in labels)
    assert any(l.endswith("parts") for l in labels)
    assert labels[-1] == "1 wire bonds"
    sub = b[0][1]
    assert sub[0]["command"] == "component.create"
    assert sub[1]["params"]["offset"] == pytest.approx(-0.635)
    ext = [c for c in sub if c["command"] == "solid.extrude"][0]["params"]
    assert ext["distance"] == pytest.approx(0.635) and ext["operation"] == "new"
    l3 = [c for l, c, _f in b if l.startswith("Layer 3")][0]
    lines = [c for c in l3 if c["command"] == "sketch.line"]
    assert len(lines) == 2 and all(c["params"]["closed"] for c in lines)
    picks = [c for c in l3 if c["command"] == "solid.extrude"][0]["params"]["profiles"]
    assert len(picks) == 2 and all("point" in p for p in picks)
    parts = [c for l, c, _f in b if l.endswith("parts")][0]
    box = [c for c in parts if c["command"] == "solid.box"][0]["params"]
    assert box["body_name"] == "U1" and box["length"] == pytest.approx(1.5, abs=0.05)   # die outline
    bond = b[-1][1]
    arc = [c for c in bond if c["command"] == "sketch.arc.three_point"][0]["params"]
    assert arc["end"][0] == pytest.approx(3.15)                           # span in mm
    assert arc["through"][1] > max(0.0, arc["end"][1])                    # loops up
    assert json.dumps(b)                                                  # plain JSON


_APP, _CLI = find_solidcraft()


@pytest.mark.skipif(_CLI is None, reason="SolidCraft not installed")
def test_builds_headless_in_solidcraft(tmp_path):
    s = _hybrid()
    out = str(tmp_path / "h.stl")
    r = S.send_to_solidcraft(s.library, "TOP", window=False, export=out)
    assert r["errors"] == []
    assert r["bodies"] >= 5            # substrate, 2 copper, die, bond
    assert os.path.getsize(out) > 1000
