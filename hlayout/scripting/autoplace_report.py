# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Auto Place report: what a run did and what needs attention.

``build_report(session, placer, info, secs)`` -> {"legal", "headline",
"sections": [{"title", "rows": [row, ...]}]}; a row is {"sev" ("error" |
"warning" | "info"), "item", "detail", "refs" [refs to select], "box"
(x0, y0, x1, y1) to zoom to or None, "fix" {"ref", "dx", "dy"} or None}.
Each overlap / off-substrate part carries the smallest grid move that
clears it without adding a new overlap, so the window can apply it. ``to_text(report)`` is the same
report as plain text (Copy / Save, the auto_place() result).
"""
import math

from ..core.autoplace import _cross_pt, _outside, _ov, mst

ERR, WARN, INFO = "error", "warning", "info"


def _union(boxes, pad=0.0):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return (min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad,
            max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad)


def _hits(placer, p):
    """What part ``p`` overlaps now: {ref | "obstacle"}; "edge" if it leaves the region."""
    b = p.world_bbox()
    out = {q.ref for q in placer.parts if q is not p and _ov(b, q.world_bbox()) > 1}
    if any(_ov(b, o) > 1 for o in placer.obstacles):
        out.add("obstacle")
    if _outside(b, placer.region) > 1:
        out.add("edge")
    return out


def suggest_move(placer, refs, clear=None, max_steps=40):
    """The smallest grid move of one of ``refs`` (not fixed) that clears
    ``clear`` (the other part of the overlap, "obstacle" or "edge") without
    adding any new overlap -> {"ref", "dx", "dy"} or None."""
    g = float(placer.grid or 50.0)
    by = {p.ref: p for p in placer.parts}
    best = None
    for ref in refs:
        p = by.get(ref)
        if p is None or p.fixed:
            continue
        target = clear if clear is not None else None
        if target is None or target == ref:
            target = next((r for r in refs if r != ref), None) or "edge"
        before = _hits(placer, p)
        x0, y0 = p.x, p.y
        found = None
        for k in range(1, max_steps + 1):
            for dx, dy in ((k, 0), (-k, 0), (0, k), (0, -k), (k, k), (-k, k), (k, -k), (-k, -k)):
                p.x, p.y = x0 + dx * g, y0 + dy * g
                now = _hits(placer, p)
                p.x, p.y = x0, y0
                if target not in now and now <= (before - {target}):
                    found = (math.hypot(dx, dy) * g, dx * g, dy * g)
                    break
            if found:
                break
        if found and (best is None or found[0] < best[0]):
            best = (found[0], ref, found[1], found[2])
    if best is None:
        return None
    return {"ref": best[1], "dx": best[2], "dy": best[3]}


def _dir(dx, dy):
    parts = []
    if dx:
        parts.append(f"{abs(dx):g} um {'right' if dx > 0 else 'left'}")
    if dy:
        parts.append(f"{abs(dy):g} um {'up' if dy > 0 else 'down'}")
    return " and ".join(parts)


def _mm(v):
    return f"{v / 1000:.2f} mm"


def build_report(session, placer, info, secs=None):
    by = {p.ref: p for p in placer.parts}
    S = info.get("settings") or {}
    bad, out = placer.overlaps()
    legal = not bad and not out
    region = placer.region
    rw, rh = region[2] - region[0], region[3] - region[1]
    moved = [p for p in placer.parts if not p.fixed]
    need = sum((p.bbox[2] - p.bbox[0]) * (p.bbox[3] - p.bbox[1]) for p in placer.parts)
    try:
        total = session.sli()["total"]
    except Exception:
        total = placer.wirelength()
    xings = placer.crossings()

    # ---- summary
    summ = []
    summ.append({"sev": INFO if legal else ERR, "item": "Result",
                 "detail": ("LEGAL -- no overlaps, every part on the substrate" if legal else
                            f"NOT LEGAL -- {len(bad)} overlap(s), {len(out)} part(s) off the substrate"),
                 "refs": [], "box": None, "fix": None})
    summ.append({"sev": INFO, "item": "Parts",
                 "detail": f"{len(moved)} placed, {len(placer.parts) - len(moved)} fixed"
                           + (f", {len(info.get('header') or [])} header / package left off the substrate"
                              if info.get("header") else "")
                           + (f" -- in {secs:.0f} s" if secs is not None else ""),
                 "refs": [p.ref for p in moved], "box": None, "fix": None})
    summ.append({"sev": INFO, "item": "Net length (SLI)",
                 "detail": f"{total / 1000:.1f} mm total over {len(placer.nets)} nets; "
                           f"{xings:g} SLI crossing(s)", "refs": [], "box": None, "fix": None})
    fill = need / (rw * rh) if rw * rh > 0 else 0.0
    summ.append({"sev": WARN if fill > 0.75 else INFO, "item": "Area used",
                 "detail": f"footprints (clearance incl.) {need / 1e6:.0f} mm2 of "
                           f"{rw / 1000:.1f} x {rh / 1000:.1f} mm = {rw * rh / 1e6:.0f} mm2 "
                           f"({100 * fill:.0f}%)" + (" -- tight: little room left for tracks"
                                                      if fill > 0.75 else ""),
                 "refs": [], "box": tuple(region), "fix": None})
    summ.append({"sev": INFO, "item": "Settings",
                 "detail": ", ".join(f"{k} {S[k]:g}" for k in ("grid", "clearance", "edge", "effort",
                                                                 "seed", "thermal", "sli_crossing")
                                     if isinstance(S.get(k), (int, float))),
                 "refs": [], "box": None, "fix": None})

    # a gap between parts holds a track only if it is a track and two copper
    # spacings wide (plus a grid step): less, and no track can pass between
    try:
        from .autoplace_session import _copper_space, _track_width
        chan = 3.0 * _track_width(session)          # a routing channel: 3 track widths
        clr_ = float(S.get("clearance", 0) or 0)
        if clr_ < chan:
            summ.append({"sev": WARN, "item": "Clearance",
                         "detail": f"{clr_:g} um between parts is less than one track channel "
                                   f"({chan:g} um: 3 track widths) -- no track can pass "
                                   "between neighbours; raise it, or run Check Routability",
                         "refs": [], "box": None, "fix": None})
    except Exception:
        pass

    # ---- problems
    prob = []
    for a, b in bad:
        pa = by[a]
        ba = pa.world_bbox()
        if b == "obstacle":
            ob = next((o for o in placer.obstacles if _ov(ba, o) > 1), None)
            fix = suggest_move(placer, [a], "obstacle")
            prob.append({"sev": ERR, "item": f"{a} on a placed obstacle",
                         "detail": "footprint overlaps a part not in the netlist (or a fixed instance)"
                                   + (f" -- fix: move {a} {_dir(fix['dx'], fix['dy'])}" if fix else
                                      " -- no clear spot within 2 mm: move it by hand"),
                         "refs": [a], "box": _union([ba, ob], 500), "fix": fix})
            continue
        pb = by[b]
        bb = pb.world_bbox()
        wx = min(ba[2], bb[2]) - max(ba[0], bb[0])
        wy = min(ba[3], bb[3]) - max(ba[1], bb[1])
        fix = suggest_move(placer, [a, b])
        prob.append({"sev": ERR, "item": f"{a} overlaps {b}",
                     "detail": f"footprints (clearance incl.) overlap {wx:.0f} x {wy:.0f} um"
                               + (f" -- fix: move {fix['ref']} {_dir(fix['dx'], fix['dy'])}" if fix else
                                  " -- no clear spot within 2 mm: move one by hand or enlarge the substrate"),
                     "refs": [a, b], "box": _union([ba, bb], 500), "fix": fix})
    for r in out:
        p = by[r]
        b = p.world_bbox()
        over = {"left": region[0] - b[0], "right": b[2] - region[2],
                "below": region[1] - b[1], "above": b[3] - region[3]}
        side = ", ".join(f"{v:.0f} um {k}" for k, v in over.items() if v > 0)
        fix = suggest_move(placer, [r], "edge")
        prob.append({"sev": ERR, "item": f"{r} off the substrate",
                     "detail": f"beyond the placement area: {side}"
                               + (f" -- fix: move {r} {_dir(fix['dx'], fix['dy'])}" if fix else ""),
                     "refs": [r], "box": _union([b, tuple(region)], 300), "fix": fix})
    for r in getattr(placer, "stuck", None) or []:
        prob.append({"sev": ERR, "item": f"{r} has no legal spot",
                     "detail": "left where it was -- no free place big enough on the grid",
                     "refs": [r], "box": _union([by[r].world_bbox()], 500) if r in by else None,
                     "fix": None})
    for r in info.get("unmapped") or []:
        prob.append({"sev": WARN, "item": f"{r} not placed",
                     "detail": "no cell for its footprint -- map one on the Parts tab",
                     "refs": [], "box": None, "fix": None})
    for r in info.get("skipped") or []:
        prob.append({"sev": WARN, "item": f"{r} not placed", "detail": "the netlist gives it no footprint",
                     "refs": [], "box": None, "fix": None})
    for pin in info.get("missing_pins") or []:
        prob.append({"sev": WARN, "item": f"pin {pin} missing",
                     "detail": "the netlist pin is not on the footprint -- that connection is left out",
                     "refs": [pin.split(".")[0]], "box": None, "fix": None})
    for r in info.get("header") or []:
        prob.append({"sev": INFO, "item": f"{r} header / package",
                     "detail": "not placed on the substrate; its pins reach the circuit through the I/O pads",
                     "refs": [r], "box": None, "fix": None})
    cb = info.get("carried") or {}
    if cb.get("bonds"):
        prob.append({"sev": INFO, "item": "Wire bonds",
                     "detail": f"{cb['bonds']} wire bond(s) and {cb.get('fingers', 0)} bond finger(s) "
                               "moved with their dies", "refs": [], "box": None, "fix": None})
    fb = info.get("fitted") or {}
    if fb.get("fingers"):
        prob.append({"sev": INFO, "item": "Bond fingers fitted",
                     "detail": f"{fb['fingers']} finger(s) moved within the bond length and angle "
                               f"rules towards their nets, {fb.get('saved_um', 0) / 1000:.1f} mm "
                               "of net length saved", "refs": sorted(fb.get("dies") or {}),
                     "box": None, "fix": None})

    # ---- nets: the longest, and who crosses whom
    geo = []
    for k, n in enumerate(placer.nets):
        pts = placer._net_points(k)
        who = [placer.parts[i].ref for i, _p in n.members] + ["I/O"] * len(n.anchors)
        ln, edges = mst(pts)
        segs = [(pts[i], pts[j], who[i], who[j]) for i, j in edges]
        refs = sorted({placer.parts[i].ref for i, _p in n.members})
        geo.append((n, ln, segs, refs, pts))
    longest = sorted(geo, key=lambda g_: -g_[1])[:12]
    nets = []
    for n, ln, segs, refs, pts in longest:
        nets.append({"sev": INFO, "item": n.name or "(unnamed)",
                     "detail": f"{_mm(ln)} -- {len(pts)} point(s): {', '.join(refs)}"
                               + (" + I/O pad" if n.anchors else ""),
                     "refs": refs, "box": _union([(x, y, x, y) for x, y in pts], 600), "fix": None})
    pairs = []
    for a in range(len(geo)):
        for b in range(a + 1, len(geo)):
            pts, ends = [], set()
            for p, q, wp, wq in geo[a][2]:
                for r, t, wr, wt in geo[b][2]:
                    c = _cross_pt(p, q, r, t)
                    if c is not None:
                        pts.append(c)
                        ends.update((wp, wq, wr, wt))
            if pts:
                pairs.append((geo[a], geo[b], pts, sorted(ends)))
    pairs.sort(key=lambda t_: -len(t_[2]))
    cross = []
    for ga, gb, pts, ends in pairs[:15]:
        cross.append({"sev": INFO, "item": f"{ga[0].name} x {gb[0].name}",
                      "detail": f"{len(pts)} crossing(s) -- the crossing SLI lines run between "
                                + ", ".join(ends),
                      "refs": [e for e in ends if e != "I/O"],
                      "box": _union([(x, y, x, y) for x, y in pts], 1500), "fix": None})

    sections = [{"title": "Summary", "rows": summ},
                {"title": f"Problems ({sum(1 for r in prob if r['sev'] != INFO)})", "rows": prob},
                {"title": f"SLI crossings ({len(pairs)} net pairs)", "rows": cross},
                {"title": "Longest nets", "rows": nets}]
    head = (f"PLACED in {secs:.0f} s -- " if secs is not None else "PLACED -- ") + \
           f"{len(moved)} parts, SLI {total / 1000:.1f} mm, {xings:g} crossing(s)"
    nerr = sum(1 for r in prob if r["sev"] == ERR)
    nwarn = sum(1 for r in prob if r["sev"] == WARN)
    if nerr:
        head += f" -- NOT LEGAL: {nerr} problem(s), see the Report tab"
    elif nwarn:
        head += f" -- {nwarn} warning(s), see the Report tab"
    return {"legal": legal, "headline": head, "sections": sections,
            "errors": nerr, "warnings": nwarn}


def to_text(report):
    lines = [report["headline"], ""]
    for sec in report["sections"]:
        lines.append(sec["title"])
        if not sec["rows"]:
            lines.append("  (none)")
        for r in sec["rows"]:
            mark = {"error": "!!", "warning": " !", "info": "  "}[r["sev"]]
            lines.append(f"{mark} {r['item']}: {r['detail']}")
        lines.append("")
    return "\n".join(lines)
