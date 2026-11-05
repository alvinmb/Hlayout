# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""merge_layer.py -- merge every Polygon, Rectangle and Path on one layer.

Everything drawn directly in the cell on the given layer is unioned:
shapes that touch or overlap become one polygon, isolated shapes come
back as plain polygons, and Paths are converted to their stroked
outline first. Text on the layer is left alone. One undo step.

Scope and limits
  * Only the cell's OWN shapes are merged. Geometry inside placed
    sub-cells (Srefs/Arefs) is not touched.
  * Pin numbers on merged shapes are lost (the result is new polygons).
  * A layer holding more than one datatype (e.g. dielectric input at
    datatype 0 plus generated fill at datatype 1) is refused unless
    all_datatypes=True, because the union would mix them together.

Use inside HLayout (command bar):
    PY exec(open(r"C:\\dev\\HLayout\\scripts\\merge_layer.py").read())
    PY merge_layer(s, 3)
    PY merge_layer(s, "1st Routing Conductor", cell_name="TOP")

Use from a shell (headless, edits the file):
    python merge_layer.py design.gds 3 [cell] [out.gds]
"""

MERGEABLE = ("Polygon", "Rectangle", "Path")


def _layer_number(session, layer):
    """Accept a layer number or an existing layer name."""
    if isinstance(layer, int):
        return layer
    text = str(layer).strip()
    if text.isdigit():
        return int(text)
    for n in range(64):
        try:
            if session.library.layers[n].name.lower() == text.lower():
                return n
        except (KeyError, IndexError):
            continue
    raise ValueError("no layer named %r" % layer)


def merge_layer(session, layer, cell_name=None, simplify=None,
                datatype=0, all_datatypes=False):
    """Union all Polygon/Rectangle/Path shapes on `layer` in one cell.

    session       the hlayout.scripting.Session (`s` in the command bar)
    layer         layer number (0-63) or layer name
    cell_name     cell to work on; default is the current cell
    simplify      optional vertex-reduction tolerance in DBU
    datatype      datatype the merged polygons are written at
    all_datatypes merge even if the layer holds several datatypes

    Returns {"cell", "layer", "before", "after"}.
    """
    num = _layer_number(session, layer)
    name = cell_name or session.cell.name
    cell = session.library.get_cell(name)

    originals = [sh for sh in cell.shapes
                 if sh.layer == num and type(sh).__name__ in MERGEABLE]
    result = {"cell": name, "layer": num,
              "before": len(originals), "after": len(originals)}
    if len(originals) < 2:
        return result

    datatypes = sorted({sh.datatype for sh in originals})
    if len(datatypes) > 1 and not all_datatypes:
        raise ValueError(
            "layer %d in %s holds datatypes %s; pass all_datatypes=True "
            "to merge them together" % (num, name, datatypes))

    previous = session.cell.name
    with session.batch():                       # one undo step
        if previous != name:
            session.use_cell(name)
        try:
            written = session.mask_op(
                "OR", num, num, layer_b=num, cell_names=[name],
                datatype=datatype, flatten=False, simplify=simplify)
            if not written.get(name):
                raise RuntimeError("merge produced no geometry; "
                                   "nothing was changed")
            keep = {id(sh) for sh in originals}
            for sh in [x for x in cell.shapes if id(x) in keep]:
                session.remove(sh)
        finally:
            if previous != name:
                session.use_cell(previous)

    result["after"] = sum(1 for sh in cell.shapes
                          if sh.layer == num
                          and type(sh).__name__ in MERGEABLE)
    return result


if __name__ == "__main__":
    import sys
    from hlayout.scripting import Session

    if len(sys.argv) < 3:
        sys.exit("usage: python merge_layer.py design.gds <layer> "
                 "[cell] [out.gds]")
    src, lay = sys.argv[1], sys.argv[2]
    cel = sys.argv[3] if len(sys.argv) > 3 else None
    out = sys.argv[4] if len(sys.argv) > 4 else src
    ses = Session.open_design(src)
    print(merge_layer(ses, lay, cell_name=cel))
    ses.save_design(out)
    print("saved", out)
