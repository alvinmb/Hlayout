# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""The front end of the design flow: read the netlist, design the printed
resistors, then place.

  1. read_design_netlist() (Tools > Read Netlist..., READNETLIST) reads a
     KiCad .net / CSV / Tango netlist: every pad's net name is stored with
     the design, and every printed resistor in it -- HL_Family resistor,
     or a ref R* with a value and no footprint -- goes into the Resistor
     Designer's list with its value, tolerance, power and shape.
  2. The resistors are designed in the Resistor Designer (an ink and a
     size chosen for each).
  3. Auto Place builds each resistor from its design; it will not run
     while a resistor in the netlist is still undesigned
     (resistor_status()).
"""

from typing import Dict, List, Optional

from ..core.errors import LayoutError

DEFAULT_POWER_MW = 50.0


def _is_resistor(comp) -> bool:
    fam = (comp.fields.get("HL_Family") or "").strip().lower()
    if fam:
        return fam == "resistor"
    return comp.ref[:1].upper() == "R" and not comp.footprint and bool(comp.value)


def _f(v, default):
    try:
        return float(str(v).strip().lower().replace("mw", "")) if v not in (None, "") else default
    except ValueError:
        return default


def netlist_resistors(netlist) -> List[dict]:
    """The netlist's printed resistors as Resistor Designer rows:
    [{ref_des, value (ohms), tolerance, power_mw, shape}] -- a value that
    can't be read is reported with value None."""
    from ..core import tf_shapes
    from ..io.hybrid_routed import parse_ohms
    out = []
    for ref, comp in netlist.components.items():
        if not _is_resistor(comp):
            continue
        try:
            value = parse_ohms(comp.value)
        except Exception:
            value = None
        shape = str(comp.fields.get("HL_Shape") or "rect").strip()
        shape = shape.lower() if shape.lower() in tf_shapes.SHAPE_KEYS else \
            tf_shapes.SHAPE_BY_LABEL.get(shape, "rect")
        out.append({"ref_des": ref, "value": value,
                    "tolerance": (comp.fields.get("Tolerance") or comp.fields.get("HL_Tolerance")
                                  or "5%").strip(),
                    "power_mw": _f(comp.fields.get("HL_Power_mW") or comp.fields.get("Power"),
                                   DEFAULT_POWER_MW),
                    "shape": shape})
    return out


def read_design_netlist(session, path: str, resistors: bool = True) -> dict:
    """Read the design's netlist (step 1 of the flow -- see the module doc).

    Net names: every "REF.PIN" in the netlist gets its net; names stored
    for the netlist's parts before are replaced, anything else (the
    module's IO.<pin> pads, parts not in this netlist) is kept.
    Resistors (``resistors=True``): each one goes into the Resistor
    Designer's list -- a new one undesigned, one already there kept with
    its design if its value, tolerance and power are unchanged, else
    updated and marked to design again. The netlist becomes Auto Place's.

    Returns {"parts", "nets", "names", "resistors": [refs], "added",
    "changed", "kept", "bad_values": [refs]}."""
    from ..io.kicad_netlist import read_netlist
    from ..io.techfile import (load_net_names, load_resistor_list, save_resistor_list)
    nl = read_netlist(path)
    refs = set(nl.components)
    names = {k: v for k, v in load_net_names(session.library).items()
             if k.split(".", 1)[0] not in refs}
    for net in nl.nets:
        nm = net.name.lstrip("/")
        for n in net.nodes:
            names[f"{n.ref}.{n.pin}"] = nm
    res = {"parts": len(nl.components), "nets": len(nl.nets), "resistors": [],
           "added": [], "changed": [], "kept": [], "bad_values": []}
    with session._batch():
        session.set_net_names(names, replace=True)
        res["names"] = len(names)
        if resistors:
            rows = load_resistor_list(session.library)
            by_ref = {r["ref_des"]: r for r in rows}
            for nr in netlist_resistors(nl):
                ref = nr["ref_des"]
                res["resistors"].append(ref)
                if nr["value"] is None:
                    res["bad_values"].append(ref)
                    continue
                old = by_ref.get(ref)
                if old is None:
                    row = dict(nr, body_width=0.0, selected_ink=None)
                    rows.append(row)
                    by_ref[ref] = row
                    res["added"].append(ref)
                elif (abs(old["value"] - nr["value"]) > 1e-9 * max(1.0, nr["value"])
                      or old["tolerance"] != nr["tolerance"]
                      or abs(old["power_mw"] - nr["power_mw"]) > 1e-9):
                    old.update(value=nr["value"], tolerance=nr["tolerance"],
                               power_mw=nr["power_mw"], body_width=0.0, selected_ink=None)
                    res["changed"].append(ref)
                else:
                    res["kept"].append(ref)
            save_resistor_list(session.library, rows)
        from .iopads import fill_io_names
        res["io_named"] = fill_io_names(session)
        try:
            session.set_autoplace_settings(last_netlist=path)
        except Exception:
            pass
    session._notify()
    return res


def designed_solution(library, ref: str, rules=None):
    """The chosen design of resistor ``ref`` from the Resistor Designer's
    list -- (Resistor, Solution) -- or None when it has none (never
    designed, or its ink is no longer offered by the design rules)."""
    from ..gui.resistor_designer import DesignRules, Resistor, width_for_power
    from ..io.techfile import load_resistor_list
    row = next((r for r in load_resistor_list(library) if r["ref_des"] == ref), None)
    if row is None or row["selected_ink"] is None:
        return None
    r = Resistor(ref_des=ref, value=row["value"], tolerance=row["tolerance"],
                 power_mw=row["power_mw"],
                 body_width=row["body_width"] or float(width_for_power(row["power_mw"])),
                 shape=row.get("shape", "rect"))
    r.compute_solutions(rules or DesignRules.from_library(library))
    for i, sol in enumerate(r.solutions):
        if abs(sol.ink_rsh - row["selected_ink"]) <= 1e-9 * max(1.0, sol.ink_rsh):
            r.selected_idx = i
            return r, sol
    return None


def resistor_status(session, netlist, placed: Optional[set] = None) -> Dict[str, List[str]]:
    """{"designed": [...], "undesigned": [...]} for the netlist's printed
    resistors; ``placed`` refs (already in the layout) count as done."""
    from ..gui.resistor_designer import DesignRules
    rules = DesignRules.from_library(session.library)
    out = {"designed": [], "undesigned": []}
    for nr in netlist_resistors(netlist):
        ref = nr["ref_des"]
        if (placed and ref in placed) or designed_solution(session.library, ref, rules):
            out["designed"].append(ref)
        else:
            out["undesigned"].append(ref)
    return out
