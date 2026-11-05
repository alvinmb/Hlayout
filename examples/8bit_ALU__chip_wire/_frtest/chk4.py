# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later


import sys, json, itertools
sys.path.insert(0, r"C:\dev\HLayout")
exec(open(sys.argv[2]).read().split("res = {}")[0])
from shapely.geometry import box
from hlayout.core.nettrace import NetTracer, _DSU
from hlayout.io.specctra import find_vias
from hlayout.io.techfile import load_net_names
def nets_check(s):
    cell = s.library.cells["TOP"]; names = load_net_names(s.library)
    tr = NetTracer(s.library, cell, [3, 5]); d = _DSU(len(tr.nodes))
    for x, y, w in find_vias(cell, (3, 5)):
        vb = box(x - w/2, y - w/2, x + w/2, y + w/2); hit = [j for j in tr._query(vb) if tr._geoms[j].intersects(vb)]
        for j in hit[1:]: d.union(hit[0], j)
    roots = {}
    for p, n in names.items():
        if p in tr.pin_node: roots.setdefault(n, set()).add(d.find(tr.pin_node[p]))
    broken = sorted(n for n, r in roots.items() if len(r) != 1)
    single = [next(iter(r)) for r in roots.values() if len(r) == 1]
    worst = None
    for (ka, (la_, ga)), (kb, (lb_, gb)) in itertools.combinations(list(enumerate(tr.nodes)), 2):
        if la_ == lb_ and d.find(ka) != d.find(kb):
            dd = ga.distance(gb); worst = dd if worst is None else min(worst, dd)
    return {"broken": broken, "shorted": len(single) - len(set(single)), "min_gap_um": round(worst, 1) if worst is not None else None}
out = {}
for vo in (False, True):
    r = s.freeroute(work_dir=sys.argv[1] + str(vo), cell_name="TOP", layer_mode="two", remove_existing=True, keep_existing=False,
                    via_optimize=vo, layer_directions="off", via_cost=2, seed=1, passes=30, optimize_passes=30, timeout=600, snap_90=True, io_entry_depth=0)
    out[str(vo)] = dict(vias=len(find_vias(s.library.cells["TOP"], (3, 5))), complete=r["complete"], **nets_check(s))
print(json.dumps(out))
