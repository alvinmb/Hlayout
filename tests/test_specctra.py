import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

pytest.importorskip("shapely")

from hlayout.core.shapes import Path
from hlayout.io.specctra import (export_dsn, freerouting_command, parse_sexpr, parse_ses,
                                 run_freerouting)
from tests.test_nettrace import _board


def _named(via=True):
    s = _board(via=via)
    s.set_net_names({"IO.1": "SIG", "U1.2": "SIG", "U1.1": "LONE"})
    return s


def test_export_dsn_structure(tmp_path):
    s = _named()
    p = str(tmp_path / "b.dsn")
    summ = s.export_dsn(p)
    txt = open(p).read()
    assert txt.count("(") == txt.count(")")
    tree = parse_sexpr(txt)[0]
    assert tree[0] == "pcb"
    assert "(layer L5 (type signal) (property (index 0)))" in txt     # 2nd conductor on top
    assert "(layer L3 (type signal) (property (index 1)))" in txt
    assert "(snap_angle ninety_degree)" in txt
    assert "(net SIG (pins IO-1 U1-2))" in txt
    assert "(net LONE (pins U1-1))" in txt
    assert "(padstack PAD_L5_400x400 (shape (rect L5 -200 -200 200 200)) (attach off))" in txt
    # the IO pad is offered only as its 0.3 mm inner strip; the rest of it
    # is a netless obstacle set back by the clearance
    assert "(pin PAD_L3_300x800 1 -4550 0)" in txt
    assert "(image IMG_HL_IOREST" in txt and "PAD_L3_400x800" in txt
    # off the dielectric only the 1st conductor may run: band closed to L5
    assert "(image IMG_HL_EDGE" in txt
    assert "(place IO 0 0 front 0)" in txt and "(place U1 1000 0 front 0)" in txt
    assert "(net SIG)(type protect)" in txt                          # existing routing kept
    assert "(via VIA_SIG 4000 0(net SIG)(type protect))" in txt
    assert summ.nets == 2
    assert summ.wires == 2 and summ.vias == 1


def test_export_requires_net_names(tmp_path):
    from hlayout.core.errors import LayoutError
    s = _board()
    with pytest.raises(LayoutError, match="net names"):
        s.export_dsn(str(tmp_path / "x.dsn"))


def test_route_mode_marks_wiring_route(tmp_path):
    s = _named()
    p = str(tmp_path / "b.dsn")
    s.export_dsn(p, keep_existing=False, exclude_nets=["LONE"], power_nets=["SIG"])
    txt = open(p).read()
    assert "(type route)" in txt and "(type protect)" not in txt
    assert "(class HL_POWER SIG" in txt and "(class HL_EXCLUDED LONE" in txt


SES = """(session x
  (base_design x)
  (routes
    (resolution um 10)
    (network_out
      (net SIG
        (wire (path L3 3000 -44000 0 40000 0))
        (via VIA_SIG 40000 0)
        (wire (path L5 3000 40000 0 40000 15000 20000 15000 20000 0))
        (wire (path L5 3000 0 -5000 0 -9000))
        (via VIA_PWR 0 -9000)
      )
    )
  )
)
"""


def test_parse_ses_scales_resolution(tmp_path):
    p = tmp_path / "a.ses"
    p.write_text(SES)
    r = parse_ses(str(p))
    assert r.wires[0] == ("SIG", "L3", 300.0, [(-4400.0, 0.0), (4000.0, 0.0)])
    assert r.vias == [("SIG", "VIA_SIG", 4000.0, 0.0), ("SIG", "VIA_PWR", 0.0, -900.0)]


def test_import_ses_keeps_existing_and_adds_new(tmp_path):
    s = _named(via=False)
    # no via yet, so SIG is open; the session adds it (plus a stub + power via)
    assert s.highlight_net("IO.1").missing == ["U1.2"]
    p = tmp_path / "a.ses"
    p.write_text(SES)
    n_paths = sum(isinstance(x, Path) for x in s.cell.shapes)
    st = s.import_ses(str(p))
    # the L3 wire and the first L5 wire duplicate existing tracks -> skipped
    assert st["wires"] == 1 and st["vias"] == 2 and st["skipped"] == 2
    assert sum(isinstance(x, Path) for x in s.cell.shapes) == n_paths + 1
    win = [x for x in s.cell.shapes if x.layer == 4 and x.datatype == 0
           and (x.bbox()[2] - x.bbox()[0]) in (600, 800)]
    assert len(win) == 2                                          # each via got its window
    assert any(x.layer == 4 and x.datatype == 1 for x in s.cell.shapes)   # fill regenerated
    assert st["nets"] == 1 and st["complete"] == 1 and st["unrouted"] == []
    assert s.undo() is True
    assert sum(isinstance(x, Path) for x in s.cell.shapes) == n_paths


def test_import_ses_replace_mode(tmp_path):
    s = _named()
    p = tmp_path / "a.ses"
    p.write_text(SES)
    st = s.import_ses(str(p), keep_existing=False)
    paths = [x for x in s.cell.shapes if isinstance(x, Path)]
    assert len(paths) == 3 and st["wires"] == 3


def test_freerouting_command_shapes():
    cmd = freerouting_command("C:/fr/freerouting.exe", "a.dsn", "a.ses", passes=7,
                              exclude_classes=["HL_EXCLUDED"])
    assert cmd == ["C:/fr/freerouting.exe", "-de", "a.dsn", "-do", "a.ses", "-mp", "7",
                   "--gui.enabled=false", "-da", "-inc", "HL_EXCLUDED"]
    cmd = freerouting_command("fr.jar", "a.dsn", "a.ses", java=sys.executable)
    assert cmd[:3] == [sys.executable, "-jar", "fr.jar"]
    cmd = freerouting_command("C:/x/freerouting-cli.exe", "a.dsn", "a.ses", passes=50)
    assert cmd[cmd.index("-mp") + 1] == "1" and "-mt" in cmd    # native build: 1 pass only


def test_run_freerouting_streams_output(tmp_path):
    ses = str(tmp_path / "o.ses")
    code = ("import sys;print('pass 1');print('pass 2');"
            f"open({ses!r},'w').write('(session x)')")
    lines = []
    rc, tail = run_freerouting([sys.executable, "-c", code], ses, timeout=30,
                               on_line=lines.append)
    assert rc == 0 and lines == ["pass 1", "pass 2"] and os.path.exists(ses)


#: verbatim output of Freerouting CLI v2.5.0 for this board (L5 jumper
#: removed so SIG needed routing) -- note it repeats the protected wire
REAL_SES = """(session t
  (base_design t)
  (placement
    (resolution um 1)
    (component "IMG_IO"
      (place IO 0 0 front 0)
    )
    (component "IMG_U1"
      (place U1 1000 0 front 0)
    )
  )
  (was_is
  )
  (routes 
    (resolution um 1)
    (parser
      (host_cad HLayout)
      (host_version "1")
    )
    (library_out 
      (padstack "VIA_SIG"
        (shape
          (rect L5 -200 -200 200 200)
        )
        (shape
          (rect L3 -200 -200 200 200)
        )
        (attach off)
      )
    )
    (network_out 
      (net SIG
        (wire
          (path L5 300
            -4077 -675
            2000 -675
            2000 0
          )
        )
        (wire
          (path L3 300
            -4077 -675
            -4800 -675
            -4800 0
          )
        )
        (via "VIA_SIG" -4077 -675
        )
        (wire
          (path L3 300
            -4400 0
            -4800 0
          )
        )
        (wire
          (path L3 300
            -4400 0
            4000 0
          )
          (type protect)
        )
      )
    )
  )
)
"""


def test_import_real_freerouting_output(tmp_path):
    s = _named(via=False)
    s.cell.shapes[:] = [x for x in s.cell.shapes if not (isinstance(x, Path) and x.layer == 5)]
    p = tmp_path / "real.ses"
    p.write_text(REAL_SES)
    st = s.import_ses(str(p))
    assert st == {"wires": 3, "vias": 1, "skipped": 1, "fingers": 0, "nets": 1, "complete": 1,
                  "unrouted": []}
    assert s.highlight_net("U1.2").pins == ["IO.1", "U1.2"]


# ---------------------------------------------------------------------------
# chip-and-wire (wire-bonded dice, one conductor) -- the notch filter example
# ---------------------------------------------------------------------------

NOTCH = os.path.join(os.path.dirname(__file__), "..", "hlayout", "examples", "notch_filter.gds")


def _notch_stripped():
    """The notch filter with its hand-drawn copper removed and its nets
    named from the original connectivity (bonds included)."""
    from hlayout.io.sidecar import load_design
    from hlayout.scripting.api import Session
    lib = load_design(NOTCH).library
    s = Session(lib)
    names = {}
    for n in s.extract_netlist([3], include_bonds=True):
        if n.id != "UNASSIGNED" and len(n.pins) > 1:
            for p in n.pins:
                names[f"{p.ref_des}.{p.pin}"] = n.id
    s.set_net_names(names)
    top = lib.cells["TOP"]
    top.shapes[:] = [sh for sh in top.shapes if sh.layer != 3]
    return s, names


@pytest.mark.skipif(not os.path.exists(NOTCH), reason="notch filter example not present")
def test_bond_landings_name_the_die_pad_at_the_other_end():
    s, names = _notch_stripped()
    land = s.bond_landings("TOP")
    assert len(land) == 22
    pads = {p for _x, _y, ps in land for p in ps}
    assert "ML1.3" in pads and "J1.19" in pads          # die pad and header pin
    assert all(len(ps) == 1 for _x, _y, ps in land)


@pytest.mark.skipif(not os.path.exists(NOTCH), reason="notch filter example not present")
def test_chip_and_wire_dsn_has_bond_fingers_keepouts_and_one_layer(tmp_path):
    s, _names = _notch_stripped()
    p = str(tmp_path / "n.dsn")
    summ = s.export_dsn(p, cell_name="TOP")
    txt = open(p).read()
    assert txt.count("(") == txt.count(")")
    assert "(layer L3 (type signal)" in txt and "(layer L5" not in txt   # nothing on L5: single layer
    assert txt.count("(pin PAD_L3_400x400 B") == 22                     # a finger per landing
    assert "(image IMG_HL_KEEP" in txt                                   # dice / part bodies
    assert "(clearance 300)" in txt                                      # tightened to the DRC rules
    assert summ.nets == 17
    assert all(not u.startswith("BOND.") for u in summ.unknown_pads)


@pytest.mark.skipif(not os.path.exists(NOTCH), reason="notch filter example not present")
def test_import_lays_bond_fingers_and_counts_landings_in_the_completion_check(tmp_path):
    s, names = _notch_stripped()
    land = s._bond_nets(s.bond_landings("TOP"), names)
    # a fake session: one straight L3 wire per landing to nowhere -- the
    # point is the fingers, so route nothing and check they are laid
    ses = tmp_path / "n.ses"
    ses.write_text("(session n (routes (resolution um 1) (network_out)))\n")
    out = s.import_ses(str(ses), cell_name="TOP")
    assert out["fingers"] == len(land) == 22
    assert out["complete"] < out["nets"]          # nothing routed: fingers alone don't join nets
