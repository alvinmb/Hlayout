# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generate a GDSII library of SMD inductor footprints for hybrid
assembly -- one cell per inductor, named exactly as its 3D STEP model
from build_STEP_parts/build_smd_inductors.py, plus an INDEX cell that
places them all in labelled rows.

Three families (the same sizes as the STEP models -- the numbers come
from that script, so a cell and its model always line up):

  * chip inductors L_<EIA>_<metric>, EIA 0201 to 1812;
  * shielded moulded power inductors L_SHLD_<size> (5030 = 5.0 x 5.0 mm);
  * unshielded drum-core power inductors L_DRUM_<size> (CD54 style).

Each cell has, for each of its two terminals (pin "1" on the -X end):

  * a METAL pad on the 1st conductor, LAYER 3: the terminal's footprint
    on the substrate grown PAD_GROW_MM (0.2 mm) on every side, numbered
    "1" / "2";
  * an EPOXY deposit on LAYER 20: the conductive epoxy printed or
    dispensed under the terminal -- the terminal footprint shrunk
    EPOXY_SHRINK_MM (0.05 mm) on every side, so it sits inside the pad
    and under the part, with the pad showing round it;

plus the body outline as a 0.1 mm Path on LAYER 61 and an "L" ref-des
placeholder on LAYER 62. --pad-layer and --epoxy-layer move the pads
and the epoxy to other layers.

Origin: the BOTTOM-LEFT corner of the body, the body spanning (0, 0) to
(L, W), long axis along X -- the same as the STEP models, so a cell
given its model in the Component Library lines up with no offset.

IMPORTANT: round-number catalogue outlines for layout practice, not a
maker's land pattern -- check the datasheet before committing a design.

Usage:
    python examples/generate_smd_inductors.py
    python examples/generate_smd_inductors.py --out my_inductors.gds --epoxy-layer 21
"""
import argparse
import importlib.util
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_root = _here
for _ in range(4):
    if os.path.isdir(os.path.join(_root, "hlayout")):
        break
    _root = os.path.dirname(_root)
sys.path.insert(0, _root)

from hlayout.core.library import Library
from hlayout.core.units import mm_to_dbu
from hlayout.io.gdsio import save_gds

PAD_LAYER = 3            # 1st conductor -- the metal pads
EPOXY_LAYER = 20         # conductive epoxy
OUTLINE_LAYER = 61
LABEL_LAYER = 62
OUTLINE_WIDTH_MM = 0.1
LABEL_HEIGHT_MM = 0.6

PAD_GROW_MM = 0.2        # terminal footprint -> metal pad
EPOXY_SHRINK_MM = 0.05   # terminal footprint -> epoxy deposit
EPOXY_MIN_MM = 0.05      # never shrink a deposit below this across
INDEX_GAP_MM = 3.0


def _models():
    """The 3D script's module: its tables and footprint()."""
    path = os.path.join(_root, "build_STEP_parts", "build_smd_inductors.py")
    spec = importlib.util.spec_from_file_location("build_smd_inductors", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _grow(rect, d):
    x0, y0, x1, y1 = rect
    return (x0 - d, y0 - d, x1 + d, y1 + d)


def _shrink_keep(rect, d, minimum):
    """Shrink by d on every side, but no side shorter than ``minimum``."""
    x0, y0, x1, y1 = rect
    dx = min(d, max(0.0, (x1 - x0 - minimum) / 2.0))
    dy = min(d, max(0.0, (y1 - y0 - minimum) / 2.0))
    return (x0 + dx, y0 + dy, x1 - dx, y1 - dy)


def _rect(cell, layer, r, pin=""):
    cell.add_rectangle(layer, mm_to_dbu(r[0]), mm_to_dbu(r[1]), mm_to_dbu(r[2]),
                       mm_to_dbu(r[3]), pin=pin)


def build_cell(lib, name, M, pad_layer=PAD_LAYER, epoxy_layer=EPOXY_LAYER):
    L, W, _H, terms = M.footprint(name)
    cell = lib.new_cell(name)
    for pin, term in enumerate(terms, 1):
        _rect(cell, pad_layer, _grow(term, PAD_GROW_MM), pin=str(pin))
        _rect(cell, epoxy_layer, _shrink_keep(term, EPOXY_SHRINK_MM, EPOXY_MIN_MM))
    lw = mm_to_dbu(OUTLINE_WIDTH_MM)
    pts = [(0, 0), (mm_to_dbu(L), 0), (mm_to_dbu(L), mm_to_dbu(W)), (0, mm_to_dbu(W)), (0, 0)]
    cell.add_path(OUTLINE_LAYER, pts, lw)
    cell.add_text(LABEL_LAYER, (mm_to_dbu(L / 2.0), mm_to_dbu(W / 2.0)), "L",
                  height=mm_to_dbu(min(LABEL_HEIGHT_MM, W * 0.6)))
    return cell


def build(pad_layer=PAD_LAYER, epoxy_layer=EPOXY_LAYER):
    M = _models()
    lib = Library("SMD_INDUCTORS")
    lib.layers.set_style(epoxy_layer, name="Epoxy")
    index = lib.new_cell("INDEX")
    rows = (("L_0", "L_1"), ("L_SHLD_",), ("L_DRUM_",))
    y = 0.0
    for prefixes in rows:
        group = [n for n in M.names() if n.startswith(prefixes)]
        x, tallest = 0.0, 0.0
        for n in group:
            build_cell(lib, n, M, pad_layer, epoxy_layer)
            L, W, _H, _t = M.footprint(n)
            index.add_sref(n, (mm_to_dbu(x), mm_to_dbu(y)))
            index.add_text(LABEL_LAYER, (mm_to_dbu(x), mm_to_dbu(y - 1.2)), n,
                           height=mm_to_dbu(0.5))
            x += L + INDEX_GAP_MM + 2 * PAD_GROW_MM
            tallest = max(tallest, W)
        y += tallest + INDEX_GAP_MM + 2.0
    lib.top_cell_name = "INDEX"
    return lib


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.join(_here, "smd_inductors.gds"))
    ap.add_argument("--pad-layer", type=int, default=PAD_LAYER)
    ap.add_argument("--epoxy-layer", type=int, default=EPOXY_LAYER)
    a = ap.parse_args()
    lib = build(a.pad_layer, a.epoxy_layer)
    save_gds(lib, a.out)
    print(f"wrote {a.out}: {len(lib.cells) - 1} inductors (pads on layer {a.pad_layer}, "
          f"epoxy on layer {a.epoxy_layer})")


if __name__ == "__main__":
    main()
