import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples"))

import generate_kovar_dip_packages as K


def test_kovar_dip_cells_pins_rows_and_walls():
    lib = K.build([20, 30, 40])
    for n in (20, 30, 40):
        c = lib.cells[f"KOVAR_DIP{n}"]
        pins = {int(r.pin): r.origin for r in c.srefs}
        assert sorted(pins) == list(range(1, n + 1))
        per = n // 2
        assert pins[2][0] - pins[1][0] == 2540                    # 0.1 in pitch
        assert pins[n][1] - pins[1][1] == 22860                   # 0.9 in row to row
        assert pins[n][0] == pins[1][0] and pins[per + 1][0] == pins[per][0]
        assert pins[1][0] < pins[per][0] and pins[1][1] < pins[n][1]   # CCW from bottom left
        outer, inner = [sh for sh in c.shapes if sh.layer == K.OUTLINE_LAYER]
        ob, ib = outer.bbox(), inner.bbox()
        assert ib[0] - ob[0] == 1000 and ob[2] - ib[2] == 1000     # 1 mm walls
        assert min(p[0] for p in outer.points) == 0 == min(p[1] for p in outer.points)  # origin
        assert len(outer.points) == 9                               # all four corners chamfered
    seal = lib.cells[K.PIN_CELL]
    assert any(sh.layer == K.SEAL_LAYER for sh in seal.shapes)
