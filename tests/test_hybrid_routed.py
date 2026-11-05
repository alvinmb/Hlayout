# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.library import Library
from hlayout.gui.commands import CommandInterpreter
from hlayout.io import hybrid_routed as HR
from hlayout.io.techfile import load_and_apply_techfile, load_net_names, save_topview_settings
from hlayout.scripting import Session

ROOT = os.path.join(os.path.dirname(__file__), "..")
EXAMPLES = os.path.join(ROOT, "router", "examples")
TECH = os.path.join(ROOT, "hlayout", "examples", "ThickFilm_2Con.json")
OPAMP = os.path.join(EXAMPLES, "single-die-op-amp.routed.json")
CROSSING = os.path.join(EXAMPLES, "single-layer-crossing.routed.json")

pytestmark = pytest.mark.skipif(not os.path.exists(OPAMP),
                                reason="router examples not present")


def _session():
    lib = Library("HYB")
    lib.new_cell("TOP")
    lib.top_cell_name = "TOP"
    if os.path.exists(TECH):
        load_and_apply_techfile(lib, TECH)
    s = Session(lib)
    s.use_cell("TOP")
    return s


def _connectivity(s, cell, design):
    """(shorts, opens) of the imported copper + bond wires against the
    router design's own pad nets. Copper is traced stack-aware (layers join
    only through dielectric windows); wires join their two end pads."""
    tr = s.net_tracer(cell_name=cell)
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def pad(pin):
        n = tr.pin_node.get(pin)
        return ("cu", tr.dsu.find(n)) if n is not None else ("pad", pin)

    def end(e):
        return ("die", f"{e['die']}.{e['pad']}") if e["kind"] == "DIE_PAD" else pad("IO." + e["pad"])

    for w in design["wires"]:
        parent[find(end(w["from"]))] = find(end(w["to"]))
    want = {}
    for d in design["dies"]:
        for p in d["pads"]:
            if p.get("net"):
                want[("die", f"{d['refdes']}.{p['id']}")] = p["net"]
    for p in design["substratePads"]:
        want[pad("IO." + p["id"])] = p["net"]
    for c in design["components"]:
        for no, k in (("1", "pinA"), ("2", "pinB")):
            want[pad(f"{c['refdes']}.{no}")] = c[k]["net"]
    groups, by_net = {}, {}
    for item, net in want.items():
        groups.setdefault(find(item), set()).add(net)
        by_net.setdefault(net, set()).add(find(item))
    return ([g for g in groups.values() if len(g) > 1],
            [n for n, g in by_net.items() if len(g) > 1])


def test_bond_diagram_rows_and_order():
    design = HR.load_routed(OPAMP)["design"]
    rows = HR.bond_diagram(design)
    assert len(rows) == len(design["wires"])
    assert [r["seq"] for r in rows] == list(range(1, len(rows) + 1))
    kinds = [r["purpose"] for r in rows]
    assert kinds == sorted(kinds, key=["DIE_BOND", "DIE_TO_DIE", "JUMPER"].index)
    die_bonds = [r for r in rows if r["purpose"] == "DIE_BOND"]
    assert all(r["first_angle_deg"] is not None for r in die_bonds)
    assert all(r["second_angle_deg"] is None for r in die_bonds)
    for r in rows:
        assert r["length_um"] > 0


def test_bond_diagram_csv_round_trip(tmp_path):
    rows = HR.bond_diagram(HR.load_routed(CROSSING)["design"])
    p = str(tmp_path / "w.csv")
    HR.write_bond_diagram_csv(rows, p)
    with open(p, encoding="utf-8") as f:
        assert f.readline().strip() == ",".join(HR.BOND_COLUMNS)
    assert HR.read_bond_diagram_csv(p) == rows


def test_import_two_layer_result_keeps_every_net_and_no_shorts():
    s = _session()
    st = s.import_routed_hybrid(OPAMP)
    data = HR.load_routed(OPAMP)
    assert st["cell"] == "single-die-op-amp"
    assert (st["dies"], st["components"], st["windows"], st["wires"], st["jumpers"]) == (1, 3, 2, 8, 1)
    assert st["traces"] == len(data["traces"])
    shorts, opens = _connectivity(s, st["cell"], data["design"])
    assert shorts == [] and opens == []
    diel = st["layer_map"]["DIEL1"]
    cell = s.library.cells[st["cell"]]
    assert any(sh.layer == diel and getattr(sh, "datatype", 0) == 1 for sh in cell.shapes)
    names = load_net_names(s.library)
    assert names.get("U1.1") == "GND" and names.get("IO.F1") == "GND"
    assert len(s.bond_diagram()) == 8


def test_import_single_layer_result_with_jumpers():
    s = _session()
    st = s.import_routed_hybrid(CROSSING)
    data = HR.load_routed(CROSSING)
    assert st["jumpers"] == 3 and st["windows"] == 0
    assert "COND2" not in st["layer_map"]
    shorts, opens = _connectivity(s, st["cell"], data["design"])
    assert shorts == [] and opens == []


def test_import_is_one_undo_step_and_can_overwrite():
    s = _session()
    before = set(s.library.cells)
    s.import_routed_hybrid(OPAMP)
    s.undo()
    assert set(s.library.cells) == before
    s.import_routed_hybrid(OPAMP)
    s.import_routed_hybrid(OPAMP)            # overwrite in place
    assert sum(1 for c in s.library.cells if c == "single-die-op-amp") == 1


def test_bond_table_has_a_row_per_wire():
    s = _session()
    s.import_routed_hybrid(OPAMP)
    name = s.generate_bond_table("BONDS", 61)
    texts = [sh for sh in s.library.cells[name].shapes if hasattr(sh, "text")]
    assert any(t.text == "BOND DIAGRAM" for t in texts)
    assert sum(1 for t in texts if t.text in ("W1", "W2", "W3")) == 3
    # 1 title + 8 header + 8 per wire
    assert len(texts) == 1 + 8 + 8 * 8


def test_commands(tmp_path):
    s = _session()
    interp = CommandInterpreter(s, console=None)
    out = interp.run(f'HYBRID IMPORT "{OPAMP}" cell=HYB')
    assert "imported 'HYB'" in out
    assert "W1" in interp.run("HYBRID BONDS")
    csv_path = str(tmp_path / "b.csv")
    assert "8 wires" in interp.run(f'HYBRID BONDS "{csv_path}"')
    assert os.path.exists(csv_path)
    assert "BT" in interp.run("BONDTABLE BT 61")


def test_assembly_drawing_adds_the_bond_table():
    s = _session()
    st = s.import_routed_hybrid(OPAMP, cell_name="TOP")
    s.library.top_cell_name = "TOP"
    save_topview_settings(s.library, {"result_layer": 50})
    r = s.generate_assembly_drawing(title="HYB", pin_table=False, resistor_table=False)
    placed = {ref.cell_name for ref in s.library.cells[r["cell"]].srefs}
    assert "ASSY_BONDTABLE" in placed
