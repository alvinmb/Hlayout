
import sys, json
sys.path.insert(0, r"C:\dev\HLayout")
from hlayout.scripting import Session
from hlayout.io import techfile as T
s = Session.new("VIATEST"); s.new_cell("TOP")
s.set_layer_style(57, name="substrate")
s.rect(57, 0, 0, 20000, 20000)
rr = T.load_route_rules(s.library); rr.update(layer_a=3, layer_b=5, boundary_layer=57); T.save_route_rules(s.library, rr)
names = {}; n = 0
for i in range(6):          # 6 left->right nets and 6 bottom->top nets: they must cross
    y = 3000 + i * 2500; x = 3000 + i * 2500
    for (px, py), net in (((1500, y), f"H{i}"), ((18500, 20000 - y), f"H{i}"), ((x, 1500), f"V{i}"), ((20000 - x, 18500), f"V{i}")):
        n += 1; s.rect(3, px - 400, py - 400, px + 400, py + 400, pin=str(n)); names[f"IO.{n}"] = net
s.set_net_names(names)
res = {}
for vo in (False, True):
    s2 = Session(s.library.__class__.__new__(s.library.__class__)) if False else None
    r = s.freeroute(work_dir=sys.argv[1] + ("_vo" if vo else "_plain"), cell_name="TOP", layer_mode="two",
                    remove_existing=True, keep_existing=False, via_optimize=vo, passes=30, optimize_passes=30, timeout=600,
                    snap_90=True, io_entry_depth=0)
    res["vo" if vo else "plain"] = {k: r.get(k) for k in ("complete", "nets", "vias", "wires", "vias_before", "vias_after", "via_optimize_skipped", "unrouted")}
print(json.dumps(res))
