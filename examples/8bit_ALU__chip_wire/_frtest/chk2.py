# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later


import sys, json, itertools
sys.path.insert(0, r"C:\dev\HLayout")
exec(open(sys.argv[2]).read().split("res = {}")[0])
r = s.freeroute(work_dir=sys.argv[1], cell_name="TOP", layer_mode="two", remove_existing=True, keep_existing=False,
                via_optimize=True, passes=30, optimize_passes=30, timeout=600, snap_90=True, io_entry_depth=0)
from hlayout.io.techfile import load_net_names
names = load_net_names(s.library)
tr = s.net_tracer()
broken = []
for net in sorted(set(names.values())):
    t = tr.trace_net_name(net, names)
    roots = {tr.dsu.find(tr.pin_node[p]) for p, n in names.items() if n == net and p in tr.pin_node}
    if len(roots) != 1: broken.append(net)
# clearance between different nets, per layer
R = s.freerouting_rules(); clr = R["clearance"]
groups = {}
for k, (lay, g) in enumerate(tr.nodes):
    groups.setdefault(lay, []).append((tr.dsu.find(k), g))
worst = None
for lay, items in groups.items():
    for (ra, ga), (rb, gb) in itertools.combinations(items, 2):
        if ra != rb:
            d = ga.distance(gb)
            worst = d if worst is None else min(worst, d)
net_root = {}
for p, n in names.items():
    if p in tr.pin_node: net_root.setdefault(n, set()).add(tr.dsu.find(tr.pin_node[p]))
allroots = [next(iter(v)) for v in net_root.values() if len(v) == 1]
shorts = len(allroots) - len(set(allroots))
print("nodes per layer", {lay: len(v) for lay, v in groups.items()}, "nets", len(net_root), "distinct roots", len(set(allroots)), "shorts", shorts)
print(json.dumps({"vias_before": r.get("vias_before"), "vias_after": r.get("vias_after"), "broken_nets": broken,
                  "min_spacing_between_nets_um": round(worst, 1) if worst is not None else None, "clearance_rule": clr}))
