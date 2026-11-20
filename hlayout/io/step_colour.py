# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Colour a packaged part's STEP model: the moulded body black, the metal
(leads, heat-sink tabs) silver.

The HLayout STEP generators write a part as a compound of separate solids --
the body plus one solid per lead / lead segment / tab. The body is the
largest solid; everything else is metal. ``colour_body_and_leads`` reads a
STEP file, colours it that way and writes it back (needs cadquery: pip
install cadquery). A model saved as one fused solid has nothing to tell
apart and is refused -- rebuild it with the current generator first.
"""
import os

BODY_BLACK = (0.10, 0.10, 0.10)
LEAD_SILVER = (0.78, 0.78, 0.80)


def colour_body_and_leads(path: str, out_path: str = None, body=BODY_BLACK,
                          metal=LEAD_SILVER) -> dict:
    """Recolour the STEP model ``path`` (written to ``out_path``, default in
    place): its largest solid ``body``, every other solid ``metal`` (RGB
    0..1). Returns {"solids", "body_volume"}. Raises ValueError for a model
    of one solid."""
    import cadquery as cq
    shape = cq.importers.importStep(path).val()
    solids = list(shape.Solids())
    if len(solids) < 2:
        raise ValueError(f"{os.path.basename(path)} is one fused solid -- nothing to tell "
                         "the body from the leads; rebuild it with the current generator")
    solids.sort(key=lambda s: s.Volume(), reverse=True)
    name = os.path.splitext(os.path.basename(path))[0]
    assy = cq.Assembly(name=name)
    assy.add(solids[0], name="body", color=cq.Color(*body))
    for k, s in enumerate(solids[1:], 1):
        assy.add(s, name=f"metal_{k}", color=cq.Color(*metal))
    target = out_path or path
    tmp = target + ".tmp.step"
    if hasattr(assy, "export"):
        assy.export(tmp)
    else:
        assy.save(tmp)
    os.replace(tmp, target)
    return {"solids": len(solids), "body_volume": solids[0].Volume()}


def is_coloured(path: str) -> bool:
    """True when the STEP file carries colours."""
    with open(path, "r", encoding="latin-1", errors="ignore") as f:
        return "COLOUR_RGB" in f.read()


def colour_sot_models(folder: str, recursive: bool = True, force: bool = False) -> dict:
    """Colour every SOT model (SOT-*.step / SOT_*.step) under ``folder``:
    body black, leads silver. Ones already coloured are left alone unless
    ``force``. Returns {"coloured": [paths], "skipped": {path: why}}; cadquery
    missing -> {"coloured": [], "skipped": {folder: "cadquery not installed"}}."""
    import glob
    try:
        import cadquery  # noqa: F401
    except ImportError:
        return {"coloured": [], "skipped": {folder: "cadquery not installed (pip install cadquery)"}}
    pat = os.path.join(folder, "**", "SOT*.step") if recursive else os.path.join(folder, "SOT*.step")
    done, skipped = [], {}
    for p in sorted(glob.glob(pat, recursive=recursive)):
        rel = os.path.relpath(p, folder)
        # SOT at the start of the name, exactly (Windows globbing ignores
        # case), and not in a backup / scratch folder (_old_..., _bench)
        if not os.path.basename(p).startswith("SOT") or any(
                part.startswith("_") for part in rel.split(os.sep)[:-1]):
            continue
        if not force and is_coloured(p):
            skipped[p] = "already coloured"
            continue
        try:
            colour_body_and_leads(p)
            done.append(p)
        except Exception as e:
            skipped[p] = str(e)
    return {"coloured": done, "skipped": skipped}
