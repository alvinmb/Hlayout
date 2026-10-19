import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.autoplace import PNet, Part, Placer, mst, rot_pt
from hlayout.io.kicad_netlist import parse_sexpr, read_kicad_netlist
from hlayout.scripting import Session

KICAD = """(export (version "E")
 (components
  (comp (ref "U1") (value "LM741") (footprint "Pkg:SO-8")
    (fields (field (name "HL_Family") "die") (field (name "HL_Power_mW") "120")))
  (comp (ref "C1") (value "100n") (footprint "Cap:C_0805")
    (property (name "HL_Weight") (value "2")))
  (comp (ref "R1") (value "4.7k")))
 (nets
  (net (code "1") (name "GND") (class "Power")
   (node (ref "U1") (pin "4") (pinfunction "V-") (pintype "power_in"))
   (node (ref "C1") (pin "2") (pintype "passive")))
  (net (code "2") (name "OUT")
   (node (ref "U1") (pin "6") (pintype "output"))
   (node (ref "R1") (pin "1") (pintype "passive")))))
"""


def test_kicad_netlist_reads_parts_fields_nets_and_classes():
    nl = read_kicad_netlist(KICAD)
    assert set(nl.components) == {"U1", "C1", "R1"}
    assert nl.components["U1"].footprint == "Pkg:SO-8"
    assert nl.components["U1"].fields == {"HL_Family": "die", "HL_Power_mW": "120"}
    assert nl.components["C1"].fields["HL_Weight"] == "2"            # KiCad 7+ (property ...)
    gnd = nl.nets[0]
    assert (gnd.name, gnd.cls) == ("GND", "Power")
    assert [(n.ref, n.pin, n.pintype) for n in gnd.nodes] == [("U1", "4", "power_in"), ("C1", "2", "passive")]
    assert nl.pad_nets() == {"U1.4": "GND", "C1.2": "GND", "U1.6": "OUT", "R1.1": "OUT"}


def test_sexpr_handles_quotes_and_nesting():
    assert parse_sexpr('(a "b c" (d "e(f)" g))') == [["a", "b c", ["d", "e(f)", "g"]]]


def test_mst_and_rotation():
    assert mst([(0, 0), (3, 4)])[0] == 5.0
    length, edges = mst([(0, 0), (10, 0), (10, 10), (0, 10)])
    assert length == 30.0 and len(edges) == 3
    assert rot_pt(1, 0, 90) == (0, 1) and rot_pt(1, 0, 180) == (-1, 0) and rot_pt(1, 0, 270) == (0, -1)


def _two_parts(fixed_b=False):
    a = Part("A", "X", "smd", (-500, -500, 500, 500), {"1": (500, 0)})
    b = Part("B", "X", "smd", (-500, -500, 500, 500), {"1": (-500, 0)},
             x=8000, y=0, placed=fixed_b, fixed=fixed_b)
    return [a, b]


def test_placer_pulls_connected_parts_together_without_overlap():
    parts = _two_parts()
    net = PNet("N", 1.0, members=[(0, "1"), (1, "1")], anchors=[(-9000, 0)])
    pl = Placer(parts, [net], region=(-10000, -10000, 10000, 10000), grid=50, seed=3)
    pl.run(effort=0.5)
    bad, out = pl.overlaps()
    assert not bad and not out
    assert pl.wirelength() < 4000                     # anchor -> A -> B, all close
    for p in parts:
        assert p.x % 50 == 0 and p.y % 50 == 0        # on the grid (region origin on it)


def test_fixed_parts_never_move_and_thermal_pushes_hot_parts_apart():
    parts = _two_parts(fixed_b=True)
    pl = Placer(parts, [PNet("N", 1.0, members=[(0, "1"), (1, "1")])],
                region=(-10000, -10000, 10000, 10000), seed=1)
    pl.run(effort=0.5)
    assert (parts[1].x, parts[1].y) == (8000, 0)
    near = abs(parts[0].x - 8000) + abs(parts[0].y)
    hot = _two_parts(fixed_b=True)
    for p in hot:
        p.power = 500.0
    pl2 = Placer(hot, [PNet("N", 1.0, members=[(0, "1"), (1, "1")])],
                 region=(-10000, -10000, 10000, 10000), seed=1, thermal=5.0)
    pl2.run(effort=0.5)
    assert abs(hot[0].x - 8000) + abs(hot[0].y) > near


def _design():
    """A 30 x 30 mm substrate, two I/O pads with net names, and two 2-pin
    part cells; the netlist ties IN -> U1 -> U2 -> OUT."""
    from hlayout.io.techfile import load_route_rules, save_route_rules
    s = Session.new("AP")
    s.new_cell("PART")
    s.rect(61, -1000, -600, 1000, 600)
    s.cell.add_rectangle(5, -1000, -300, -600, 300).pin = "1"
    s.cell.add_rectangle(5, 600, -300, 1000, 300).pin = "2"
    s.new_cell("TOP")
    s.rect(57, -15000, -15000, 15000, 15000)
    rr = load_route_rules(s.library)
    rr.update(layer_a=3, layer_b=5, boundary_layer=57)
    save_route_rules(s.library, rr)
    s.cell.add_rectangle(3, -14500, -500, -13500, 500).pin = "1"
    s.cell.add_rectangle(3, 13500, -500, 14500, 500).pin = "2"
    s.set_net_names({"IO.1": "IN", "IO.2": "OUT"})
    return s


NET = """(export (version "E")
 (components (comp (ref "U1") (value "x") (footprint "Lib:PART"))
             (comp (ref "U2") (value "x") (footprint "Lib:PART")))
 (nets (net (code "1") (name "IN") (node (ref "U1") (pin "1")))
       (net (code "2") (name "MID") (node (ref "U1") (pin "2")) (node (ref "U2") (pin "1")))
       (net (code "3") (name "OUT") (node (ref "U2") (pin "2")))))
"""


def test_auto_place_on_a_design_places_names_and_is_one_undo_step(tmp_path):
    s = _design()
    p = tmp_path / "a.net"
    p.write_text(NET)
    s.clear_undo_history()
    r = s.auto_place(str(p), effort=0.5, seed=2)
    assert r["placed"] == 2 and not r["overlaps"] and not r["outside"]
    refs = {x.label: x for x in s.library.cells["TOP"].srefs}
    assert set(refs) == {"U1", "U2"} and all(x.cell_name == "PART" for x in refs.values())
    d = s.sli()
    assert {n["net"] for n in d["nets"]} == {"IN", "MID", "OUT"}
    assert d["total"] < 32000                       # the straight run is 27 mm of pads
    assert refs["U1"].origin[0] < refs["U2"].origin[0]   # IN side first
    s.undo()
    assert not [x for x in s.library.cells["TOP"].srefs if x.label]


def test_improve_never_makes_it_worse(tmp_path):
    s = _design()
    p = tmp_path / "a.net"
    p.write_text(NET)
    s.auto_place(str(p), effort=0.3, seed=5)
    before = s.sli()["total"]
    s.auto_place(str(p), improve=True, effort=0.5, seed=6)
    assert s.sli()["total"] <= before + 1


ALU = os.path.join(os.path.dirname(__file__), "..", "example2", "alu16_MCM.gds")


@pytest.mark.skipif(not os.path.exists(ALU), reason="example 2 not present")
def test_alu16_scrambled_places_within_ten_percent_of_hand_placement():
    from hlayout.io.sidecar import load_design
    lib = load_design(ALU).library
    s = Session(lib)
    s.use_cell("TOP")
    top = lib.cells["TOP"]
    top.shapes[:] = [sh for sh in top.shapes
                     if not (sh.layer == 4 or (sh.layer in (3, 5) and not getattr(sh, "pin", None)))]
    top.srefs[:] = [r for r in top.srefs if not r.cell_name.startswith("VIA_")]
    ex = os.path.dirname(ALU)
    s.import_net_names(os.path.join(ex, "alu16_net_names.csv"))
    hand = s.sli()["total"]
    top.srefs[:] = [r for r in top.srefs if not r.cell_name.startswith("LCC_")]
    r = s.auto_place(os.path.join(ex, "alu16_netlist", "alu16.net"), seed=1, thermal=0)
    assert r["placed"] == 10 and not r["overlaps"] and not r["outside"]
    assert s.sli()["total"] < 1.10 * hand


def test_auto_place_window_runs_and_shows_slis(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.geometry("1x1+-3000+-3000")
    from hlayout.gui.autoplace_window import AutoPlaceWindow

    class Canvas:
        selected, selected_multi, sli_show_power, cell_name = None, [], True, "TOP"

        def redraw(self):
            pass

    class Status:
        text = ""

        def set(self, t):
            self.text = t

    class App:
        def __init__(self, s):
            self.session, self.canvas, self.status, self.sli = s, Canvas(), Status(), None

        def set_sli(self, on):
            self.sli = on

        def refresh_all(self):
            pass

    s = _design()
    p = tmp_path / "a.net"
    p.write_text(NET)
    app = App(s)
    win = AutoPlaceWindow(root, app)
    win.v["netlist"].set(str(p))
    win._load_netlist()
    assert set(win.parts.get_children()) == {"U1", "U2"}
    assert win.parts.set("U1", "cell") == "PART"
    assert set(win.nets.get_children()) == {"IN", "MID", "OUT"}
    win.v["effort"].set("0.3")
    win.run("run")
    for _ in range(200):
        root.update()
        if not win._running:
            break
        root.after(50)
    root.update()
    assert not win._running
    assert win.banner.cget("text").startswith("PLACED")
    assert app.sli is True
    assert {x.label for x in s.library.cells["TOP"].srefs} == {"U1", "U2"}
    win.destroy()
    root.destroy()


def test_printed_resistor_is_generated_from_its_value(tmp_path):
    s = _design()
    net = NET.replace('(comp (ref "U2") (value "x") (footprint "Lib:PART")))',
                      '(comp (ref "U2") (value "x") (footprint "Lib:PART")) (comp (ref "R1") (value "4.7k")))')
    net = net.replace('(node (ref "U2") (pin "2")))))', '(node (ref "U2") (pin "2")) (node (ref "R1") (pin "1")))))')
    p = tmp_path / "r.net"
    p.write_text(net)
    r = s.auto_place(str(p), effort=0.3, seed=1)
    assert r["generated"] == ["R1"]
    refs = {x.label: x for x in s.library.cells["TOP"].srefs if x.label}
    assert "R1" in refs and not r["overlaps"]


def test_overrides_force_a_cell_and_lock_a_part(tmp_path):
    s = _design()
    s.new_cell("PART2")
    s.rect(61, -1200, -600, 1200, 600)
    s.cell.add_rectangle(5, -1200, -300, -800, 300).pin = "1"
    s.cell.add_rectangle(5, 800, -300, 1200, 300).pin = "2"
    s.use_cell("TOP")
    p = tmp_path / "a.net"
    p.write_text(NET)
    ov = tmp_path / "o.csv"
    ov.write_text("ref,cell,x,y,locked\nU2,PART2,5000,5000,1\n")
    s.auto_place(str(p), overrides=str(ov), effort=0.3)
    refs = {x.label: x for x in s.library.cells["TOP"].srefs if x.label}
    assert refs["U2"].cell_name == "PART2" and tuple(refs["U2"].origin) == (5000, 5000)
