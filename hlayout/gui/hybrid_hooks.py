"""Canvas hooks for the chip-and-wire editing tools (hybrid router Phase 6).

The canvas knows nothing about sessions; LayoutCanvas.hybrid_hooks is an
instance of this class, set by the App, that answers its questions about
the imported routed hybrid: what bond is under the cursor, what a finger
move or a resistor resize would do. Everything is computed by the Session
(bond_wire_info(), move_bond_finger(dry_run=True), resize_resistor(
dry_run=True)), so the preview is exactly what the click then commits.
"""

from ..io.hybrid_routed import rotate


def new_resistor_length(pick: dict, to) -> float:
    """Body length a cursor at ``to`` asks for: twice its distance from the
    resistor's centre along the body's length axis (at least 10 um)."""
    cx, cy = pick["centre"]
    inv = {"R90": "R270", "R270": "R90"}.get(pick.get("rotation"), pick.get("rotation"))
    lx, _ly = rotate(inv, to[0] - cx, to[1] - cy)
    return max(10.0, 2.0 * abs(lx))


def resistor_outline(pick: dict, length: float, width: float):
    """The four corners of a body ``length`` x ``width`` centred on the
    resistor, turned with it."""
    cx, cy = pick["centre"]
    out = []
    for lx, ly in ((-length / 2, -width / 2), (length / 2, -width / 2),
                   (length / 2, width / 2), (-length / 2, width / 2)):
        dx, dy = rotate(pick.get("rotation"), lx, ly)
        out.append((cx + dx, cy + dy))
    return out


class HybridHooks:
    def __init__(self, app):
        self.app = app

    @property
    def session(self):
        return self.app.session

    def _cell(self):
        return self.app.canvas.cell_name

    def _tol(self):
        return 6.0 / max(self.app.canvas.scale, 1e-9)

    def bond_info(self, pt, tol):
        return self.session.bond_wire_info(pt, tol=tol, cell_name=self._cell())

    def finger_at(self, pt):
        cell = self.session.library.cells.get(self._cell())
        if cell is None:
            return None
        pad, _pid = self.session._hybrid_pad_at(cell, pt, self._tol())
        return pad

    def finger_preview(self, grab, to):
        return self.session.move_bond_finger(grab, to, cell_name=self._cell(), dry_run=True,
                                             tol=self._tol())

    def resistor_at(self, pt):
        return self.session.resistor_at(pt, cell_name=self._cell())

    def resistor_preview(self, grab, to):
        pick = self.resistor_at(grab)
        if pick is None:
            return None
        length = new_resistor_length(pick, to)
        pv = self.session.resize_resistor(pick["refdes"], length=length, dry_run=True)
        pv["outline"] = resistor_outline(pick, pv["length_um"], pv["width_um"])
        return pv
