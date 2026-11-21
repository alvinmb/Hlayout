# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Part locks: parts Auto Place, the router's nudge pass and the canvas
must leave where they are.

Part of hlayout.scripting.api.Session (a mixin): these methods are
Session's own -- call them on a Session as always."""

from typing import Optional

from ..core.errors import LayoutError


class LocksMixin:
    # --- part locks: parts autoplace, the router's nudge pass and the canvas
    # --- must leave where they are
    LOCKED_KEY = "locked_parts"

    def locked_parts(self, cell_name: Optional[str] = None) -> set:
        """The ref-des of the parts locked in place in the cell (current by
        default): autoplace keeps them, the router's nudge pass never moves
        them, and the canvas won't drag, nudge, rotate or mirror them."""
        import json as _json
        name = cell_name or self.current_cell_name
        try:
            d = _json.loads(self.library.tech_params.get(self.LOCKED_KEY) or "{}")
        except ValueError:
            d = {}
        return set(d.get(name) or [])

    def _set_locked(self, name, refs):
        import json as _json
        try:
            d = _json.loads(self.library.tech_params.get(self.LOCKED_KEY) or "{}")
        except ValueError:
            d = {}
        if refs:
            d[name] = sorted(refs, key=lambda r: (len(r), r))
        else:
            d.pop(name, None)
        self.library.tech_params[self.LOCKED_KEY] = _json.dumps(d)

    def lock_parts(self, refs, cell_name: Optional[str] = None, lock: bool = True) -> list:
        """Lock (``lock`` False: unlock) the placed parts ``refs`` (ref-des;
        "*" = every labelled part) in the cell. An I/O pad is locked as
        "IO.<pin>" (its pad number, as in the IO.<pin> net names; "IO.*" =
        every I/O pad drawn in the cell): the canvas puts it back if it is
        moved, edited or deleted, and the I/O Pad Wizard leaves it. Unknown
        refs raise. Returns the cell's locked refs afterwards. One undo step."""
        name = cell_name or self.current_cell_name
        if name is None:
            raise LayoutError("no current cell")
        cell = self.library.get_cell(name)
        placed = {r.label for r in cell.srefs if r.label}
        pads = {f"IO.{p}" for p in self.io_pad_pins(name)}
        cur = self.locked_parts(name)
        want = set()
        for r in ([refs] if isinstance(refs, str) else list(refs)):
            for t in str(r).replace(";", ",").split(","):
                t = t.strip()
                if not t:
                    continue
                if t == "*":
                    want |= placed
                elif t.upper() == "IO.*":
                    want |= pads | ({x for x in cur if x.startswith("IO.")} if not lock else set())
                elif t in placed or t in pads or (not lock and t in cur):
                    want.add(t)
                else:
                    raise LayoutError(f"no placed part {t} in {name}")
        new = (cur | want) if lock else (cur - want)
        if new != cur:
            self.checkpoint()
            self._set_locked(name, new)
            self._notify()
        return sorted(new, key=lambda r: (len(r), r))

    def unlock_parts(self, refs="*", cell_name: Optional[str] = None) -> list:
        """Unlock the parts ``refs`` ("*" = all). Returns the refs still locked."""
        return self.lock_parts(refs, cell_name=cell_name, lock=False)
