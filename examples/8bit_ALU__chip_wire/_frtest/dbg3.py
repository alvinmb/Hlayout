
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

r = s.freeroute(work_dir=sys.argv[1], cell_name="TOP", layer_mode="two", remove_existing=True, keep_existing=False,
                via_optimize=False, passes=30, optimize_passes=30, timeout=600, snap_90=True, io_entry_depth=0)
print("start", nets_check(s), len(find_vias(s.library.cells["TOP"], (3, 5))))
import hlayout.core.viaopt as VO
from hlayout.core.shapes import Path
cell = s.library.cells["TOP"]
for step in range(40):
    snap = [(id(sh), sh.layer) for sh in cell.shapes]
    rr = VO.optimize_vias(s.library, cell, (3, 5), 100.0, max_rounds=1)
    if not rr["removed"]:
        print("done at step", step); break
    c = nets_check(s)
    if c["broken"] or c["shorted"]:
        x, y = rr["removed"][0]
        moved = [sh for sh in cell.shapes if isinstance(sh, Path) and (id(sh), sh.layer) not in snap]
        print("BAD step", step, "via", (x, y), c, "moved:", [(m.layer, m.points, m.width) for m in moved])
        # what else is near on the moved layer
        from shapely.geometry import LineString
        for m in moved:
            g = LineString(m.points).buffer(m.width/2)
            near = [(sh.layer, type(sh).__name__, sh.bbox(), getattr(sh, "pin", "")) for sh in cell.shapes if sh is not m and sh.layer == m.layer and hasattr(sh, "bbox") and g.distance(__import__("shapely.geometry", fromlist=["box"]).box(*sh.bbox())) < 150][:6]
            print("  near:", near)
        print("  touch:", [(sh.layer, type(sh).__name__, getattr(sh,"points",None) if isinstance(sh, Path) else sh.bbox()) for sh in cell.shapes if hasattr(sh,"bbox") and sh.layer in (3,5) and __import__("shapely.geometry", fromlist=["box"]).box(x-201,y-201,x+201,y+201).intersects(__import__("shapely.geometry", fromlist=["box"]).box(*sh.bbox()))][:10])
        break
