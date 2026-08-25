import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core.errors import LayoutError
from hlayout.core.library import Library
from hlayout.core.shapes import Path, Polygon, Rectangle, Text
from hlayout.core.units import dbu_to_mm
from hlayout.io.kicadio import (
    COPPER_LAYER,
    COURTYARD_LAYER,
    DRILL_LAYER,
    FAB_LAYER,
    SILKSCREEN_LAYER,
    load_kicad_footprint,
    load_kicad_footprint_library,
)

# A real SOT-23 footprint (KiCad's own Package_TO_SOT_SMD.pretty, legacy
# "(module ...)" format) -- three SMD rectangular pads, a Fab outline, a
# CrtYd courtyard, SilkS lines, and a deliberately-included zero-length
# fp_line (this exists in the real upstream file and once broke GDSII
# round-tripping -- see kicadio.py's _dedupe_consecutive).
SOT23 = """\
(module SOT-23 (layer F.Cu) (tedit 5A02FF57)
  (descr "SOT-23, Standard")
  (tags SOT-23)
  (attr smd)
  (fp_text reference REF** (at 0 -2.5) (layer F.SilkS)
    (effects (font (size 1 1) (thickness 0.15)))
  )
  (fp_text value SOT-23 (at 0 2.5) (layer F.Fab)
    (effects (font (size 1 1) (thickness 0.15)))
  )
  (fp_line (start -0.7 -0.7) (end -0.7 -0.7) (layer F.SilkS) (width 0.12))
  (fp_line (start -0.7 -0.95) (end -0.7 1.5) (layer F.Fab) (width 0.1))
  (fp_line (start -1.7 -1.75) (end 1.7 -1.75) (layer F.CrtYd) (width 0.05))
  (fp_line (start 1.7 -1.75) (end 1.7 1.75) (layer F.CrtYd) (width 0.05))
  (fp_line (start 1.7 1.75) (end -1.7 1.75) (layer F.CrtYd) (width 0.05))
  (fp_line (start -1.7 1.75) (end -1.7 -1.75) (layer F.CrtYd) (width 0.05))
  (pad 1 smd rect (at -1 -0.95) (size 0.9 0.8) (layers F.Cu F.Paste F.Mask))
  (pad 2 smd rect (at -1 0.95) (size 0.9 0.8) (layers F.Cu F.Paste F.Mask))
  (pad 3 smd rect (at 1 0) (size 0.9 0.8) (layers F.Cu F.Paste F.Mask))
  (model ${KISYS3DMOD}/Package_TO_SOT_SMD.3dshapes/SOT-23.wrl
    (at (xyz 0 0 0))
    (scale (xyz 1 1 1))
    (rotate (xyz 0 0 0))
  )
)
"""

# A rotated roundrect pad -- exercises corner tessellation plus the
# (at x y angle) rotation path together.
ROUNDRECT_MOD = """\
(module TESTPAD (layer F.Cu) (tedit 00000000)
  (pad 1 smd roundrect (at 1 2 90) (size 2 1) (layers F.Cu F.Mask F.Paste)
    (roundrect_rratio 0.25))
)
"""

# A round thru-hole pad with a drill -- exercises the drill-layer path.
THRUHOLE_MOD = """\
(module TESTHOLE (layer F.Cu) (tedit 00000000)
  (pad 1 thru_hole circle (at 0 0) (size 1.6 1.6) (drill 0.8) (layers *.Cu *.Mask))
)
"""


def _load_text(tmp_path, name, text):
    lib = Library("TEST")
    path = tmp_path / name
    path.write_text(text)
    cell = load_kicad_footprint(str(path), lib)
    return lib, cell


def test_sot23_pads_match_source_exactly(tmp_path):
    lib, cell = _load_text(tmp_path, "SOT-23.kicad_mod", SOT23)
    assert cell.name == "SOT-23"

    copper = [s for s in cell.shapes if s.layer == COPPER_LAYER]
    assert len(copper) == 3
    boxes = sorted(
        tuple(round(dbu_to_mm(v), 3) for pt in s.points for v in pt)
        for s in copper
    )
    # hand-computed from (at -1,-0.95)/(at -1,0.95)/(at 1,0), size 0.9x0.8
    expected = sorted([
        (-1.45, -1.35, -0.55, -1.35, -0.55, -0.55, -1.45, -0.55),
        (-1.45, 0.55, -0.55, 0.55, -0.55, 1.35, -1.45, 1.35),
        (0.55, -0.4, 1.45, -0.4, 1.45, 0.4, 0.55, 0.4),
    ])
    assert boxes == expected


def test_sot23_zero_length_line_is_skipped_not_broken(tmp_path):
    # The zero-length fp_line in the fixture must not produce a
    # 1-point (or 0-point) Path -- it should simply be dropped.
    lib, cell = _load_text(tmp_path, "SOT-23.kicad_mod", SOT23)
    for s in cell.shapes:
        if isinstance(s, Path):
            assert len(s.points) >= 2


def test_sot23_reference_art_and_text_present(tmp_path):
    lib, cell = _load_text(tmp_path, "SOT-23.kicad_mod", SOT23)
    assert any(isinstance(s, Path) and s.layer == FAB_LAYER for s in cell.shapes)
    assert any(isinstance(s, Path) and s.layer == COURTYARD_LAYER for s in cell.shapes)
    texts = [s for s in cell.shapes if isinstance(s, Text)]
    assert {t.text for t in texts} == {"REF**", "SOT-23"}


def test_roundrect_pad_bbox_and_rotation(tmp_path):
    # size (2,1) rotated 90 degrees swaps the effective width/height,
    # then the pad is offset to (1,2)mm.
    lib, cell = _load_text(tmp_path, "TESTPAD.kicad_mod", ROUNDRECT_MOD)
    copper = [s for s in cell.shapes if s.layer == COPPER_LAYER]
    assert len(copper) == 1
    xs = [dbu_to_mm(p[0]) for p in copper[0].points]
    ys = [dbu_to_mm(p[1]) for p in copper[0].points]
    assert min(xs) == pytest.approx(0.5, abs=1e-3)
    assert max(xs) == pytest.approx(1.5, abs=1e-3)
    assert min(ys) == pytest.approx(1.0, abs=1e-3)
    assert max(ys) == pytest.approx(3.0, abs=1e-3)
    assert len(copper[0].points) > 4  # corners were actually tessellated


def test_thru_hole_pad_draws_drill_circle(tmp_path):
    lib, cell = _load_text(tmp_path, "TESTHOLE.kicad_mod", THRUHOLE_MOD)
    drill_shapes = [s for s in cell.shapes if s.layer == DRILL_LAYER]
    assert len(drill_shapes) == 1
    assert isinstance(drill_shapes[0], Polygon)
    xs = [dbu_to_mm(p[0]) for p in drill_shapes[0].points]
    # drill diameter 0.8mm -> radius 0.4mm
    assert max(xs) - min(xs) == pytest.approx(0.8, abs=0.02)


def test_footprint6_format_rejected(tmp_path):
    path = tmp_path / "new.kicad_mod"
    path.write_text('(footprint "X" (version 1) (generator pcbnew))')
    with pytest.raises(LayoutError):
        load_kicad_footprint(str(path), Library("TEST"))


def test_load_kicad_footprint_library_imports_every_file(tmp_path):
    (tmp_path / "SOT-23.kicad_mod").write_text(SOT23)
    (tmp_path / "TESTPAD.kicad_mod").write_text(ROUNDRECT_MOD)
    (tmp_path / "not_a_footprint.txt").write_text("ignore me")
    lib = Library("TEST")
    created = load_kicad_footprint_library(str(tmp_path), lib)
    assert sorted(created) == ["SOT-23", "TESTPAD"]
    assert set(lib.cells.keys()) == {"SOT-23", "TESTPAD"}
