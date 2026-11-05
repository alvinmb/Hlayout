# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Thermal analysis outputs: a CSV of the parts table and a
self-contained HTML report (setup, stack, temperature maps, parts
table, energy balance) in the same spirit as hlayout.io.printio's
printable page. Map images are PNGs encoded by
hlayout.core.thermal.rgb_to_png and inlined as data URIs, so the report
is one file with no imaging dependency."""
import base64
import csv
import html
from typing import Optional

PART_COLUMNS = ("ref_des", "kind", "cell_name", "power_mw", "area_mm2", "density_w_mm2",
                "t_max", "t_avg", "rise", "limit", "ok")


def write_thermal_csv(result, path: str) -> str:
    """One row per part, hottest first. Returns ``path``."""
    rows = sorted(result.parts, key=lambda p: -(p.t_max if p.t_max == p.t_max else -1e9))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ref_des", "kind", "cell", "power_mW", "area_mm2", "density_W_per_mm2",
                    "t_max_C", "t_avg_C", "rise_C", "limit_C", "status"])
        for p in rows:
            w.writerow([p.ref_des, p.kind, p.cell_name, f"{p.power_mw:.3f}",
                        f"{p.area_mm2:.4f}", f"{p.density_w_mm2:.5f}", f"{p.t_max:.2f}",
                        f"{p.t_avg:.2f}", f"{p.rise:.2f}",
                        "" if p.limit is None else f"{p.limit:.1f}",
                        "OK" if p.ok else "OVER LIMIT"])
    return path


def _map_image(result, grid, t_min, t_max, max_px: int = 640) -> str:
    from ..core.thermal import rgb_to_png, temps_to_rgb
    rgb = temps_to_rgb(grid, t_min, t_max)
    scale = max(1, min(8, max_px // max(rgb.shape[1], 1)))
    data = base64.b64encode(rgb_to_png(rgb, scale)).decode("ascii")
    return (f'<img alt="temperature map" src="data:image/png;base64,{data}" '
            f'style="max-width:100%;border:1px solid #999;image-rendering:pixelated">')


def _scale_bar(t_min, t_max) -> str:
    from ..core.thermal import heat_color
    stops = ",".join("rgb(%d,%d,%d) %d%%" % (heat_color(i / 10.0) + (i * 10,))
                     for i in range(11))
    return (f'<div style="display:flex;align-items:center;gap:8px;margin:6px 0 14px">'
            f'<span>{t_min:.1f} &deg;C</span>'
            f'<span style="flex:1;max-width:420px;height:12px;border:1px solid #999;'
            f'background:linear-gradient(90deg,{stops})"></span>'
            f'<span>{t_max:.1f} &deg;C</span></div>')


def _map_figure(result, grid, display=None, lo=None, hi=None, max_px: int = 640,
                caption: str = "") -> str:
    """One temperature map as an inline SVG figure in the classic
    thermal-simulator layout: the map inside a frame with Length/Width
    axes in mm, and a LEGEND of band colours and values on the right."""
    from ..core.thermal import MapView, rgb_to_png
    v = MapView(result, grid, display, background=(0, 0, 0), lo=lo, hi=hi)
    h_px, w_px = v.rgb.shape[:2]
    scale = max(1, min(8, max_px // max(w_px, 1)))
    data = base64.b64encode(rgb_to_png(v.rgb, scale)).decode("ascii")
    iw, ih = w_px * scale, h_px * scale
    pad = 14                                   # frame margin round the map
    left, top = 70, 34
    fx0, fy0, fx1, fy1 = left - pad, top - pad, left + iw + pad, top + ih + pad
    legend_x = fx1 + 60
    entries = v.legend()
    row = 18
    width = legend_x + 120
    height = max(fy1 + 40, top + 24 + row * len(entries) + 20)
    ink, frame = "#e6e6e6", "#1f8f3a"
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}" style="max-width:100%;background:#000;'
           f'font-family:Segoe UI,Arial,sans-serif;font-size:12px">',
           f'<rect x="0" y="0" width="{width}" height="{height}" fill="#000"/>',
           f'<image x="{left}" y="{top}" width="{iw}" height="{ih}" '
           f'style="image-rendering:pixelated" href="data:image/png;base64,{data}"/>',
           f'<rect x="{fx0}" y="{fy0}" width="{fx1 - fx0}" height="{fy1 - fy0}" '
           f'fill="none" stroke="{frame}" stroke-width="2"/>',
           f'<text x="{fx0}" y="{fy0 - 6}" fill="{ink}">Width [mm]</text>',
           f'<text x="{fx1 + 6}" y="{fy1 + 4}" fill="{ink}">Length [mm]</text>']
    for mm in v.axis_ticks(v.width_mm):
        x = left + mm / v.width_mm * iw if v.width_mm else left
        out.append(f'<text x="{x:.1f}" y="{fy1 + 18}" fill="{ink}" '
                   f'text-anchor="middle">{mm:.2f}</text>')
    for mm in v.axis_ticks(v.height_mm):
        y = top + ih - (mm / v.height_mm * ih if v.height_mm else 0)
        out.append(f'<text x="{fx0 - 6}" y="{y + 4:.1f}" fill="{ink}" '
                   f'text-anchor="end">{mm:.2f}</text>')
    out.append(f'<text x="{legend_x}" y="{top}" fill="{ink}" font-weight="bold">LEGEND</text>')
    out.append(f'<text x="{legend_x}" y="{top + 14}" fill="#9a9a9a" '
               f'font-size="10">{html.escape(v.unit)}</text>')
    y = top + 26
    for (r, g_, b), label in entries:          # coolest at the top, like the window
        out.append(f'<rect x="{legend_x}" y="{y}" width="64" height="{row - 3}" '
                   f'fill="rgb({r},{g_},{b})" stroke="#666" stroke-width="0.5"/>')
        dark = (r * 299 + g_ * 587 + b * 114) / 1000 > 140
        out.append(f'<text x="{legend_x + 60}" y="{y + row - 7}" text-anchor="end" '
                   f'fill="{"#000" if dark else "#fff"}">{html.escape(label)}</text>')
        y += row
    out.append("</svg>")
    cap = f"<p class='note'>{html.escape(caption)}</p>" if caption else ""
    return cap + "<div style='margin:6px 0 16px'>" + "".join(out) + "</div>"


def write_thermal_html(result, path: str, title: Optional[str] = None,
                       display: Optional[dict] = None) -> str:
    """Write the report. ``display`` is the map style (thermal rules'
    "display" section; see hlayout.core.thermal.MapView). Returns ``path``."""
    import numpy as np
    m = result.model
    meta = getattr(m, "meta", {}) or {}
    title = title or f"Thermal analysis: {meta.get('cell', 'model')}"
    esc = html.escape
    ref = m.reference_temp()
    parts = sorted(result.parts, key=lambda p: (p.ok, -(p.t_max if p.t_max == p.t_max else -1e9)))
    out = ["<!doctype html><html><head><meta charset='utf-8'>",
           f"<title>{esc(title)}</title>",
           "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#111;"
           "max-width:1000px}h1{font-size:20px}h2{font-size:15px;margin-top:26px;"
           "border-bottom:1px solid #999;padding-bottom:3px}table{border-collapse:collapse;"
           "font-size:13px}th,td{border:1px solid #999;padding:4px 9px;text-align:right}"
           "th{background:#eee}td.l,th.l{text-align:left}tr.bad td{background:#fde0e0}"
           ".note{color:#555;font-size:12px;max-width:760px}</style></head><body>",
           f"<h1>{esc(title)}</h1>"]

    n_bad = len(result.failures)
    verdict = ("All parts are within their limits." if not n_bad else
               f"{n_bad} part(s) over limit.")
    out.append(f"<p><b>{esc(verdict)}</b> Hottest point {result.max_temp:.1f} &deg;C, "
               f"{result.max_temp - ref:.1f} &deg;C above the "
               f"{'base' if m.base_mode == 'fixed' else 'ambient'} at {ref:.1f} &deg;C. "
               f"Total power {result.power_in * 1000:.1f} mW.</p>")

    out.append("<h2>Parts</h2><table><tr><th class='l'>Ref des</th><th class='l'>Kind</th>"
               "<th>Power (mW)</th><th>Area (mm&sup2;)</th><th>W/mm&sup2;</th>"
               "<th>Max (&deg;C)</th><th>Avg (&deg;C)</th><th>Rise (&deg;C)</th>"
               "<th>Limit (&deg;C)</th><th class='l'>Status</th></tr>")
    for p in parts:
        lim = "" if p.limit is None else f"{p.limit:.0f}"
        why = "OK"
        if not p.ok:
            why = "over temperature" if (p.limit is not None and p.t_max > p.limit) \
                else "over power density"
        out.append(f"<tr class='{'' if p.ok else 'bad'}'><td class='l'>{esc(p.ref_des)}</td>"
                   f"<td class='l'>{esc(p.kind)}</td><td>{p.power_mw:.1f}</td>"
                   f"<td>{p.area_mm2:.3f}</td><td>{p.density_w_mm2:.4f}</td>"
                   f"<td>{p.t_max:.1f}</td><td>{p.t_avg:.1f}</td><td>{p.rise:.1f}</td>"
                   f"<td>{lim}</td><td class='l'>{why}</td></tr>")
    out.append("</table>")

    surface = result.surface_temps()
    t_min = float(np.nanmin(surface)) if np.isfinite(surface).any() else ref
    t_max = result.max_temp
    out.append("<h2>Temperature maps</h2>")
    out.append(_map_figure(result, surface, display, t_min, t_max,
                           caption="Top surface: the topmost material in every column, "
                                   "as an infrared camera looking down would see it."))
    sub_top = max((i for i, s in enumerate(m.slices) if s.role == "substrate"), default=None)
    if sub_top is not None:
        out.append(_map_figure(result, result.slice_temps(sub_top), display, t_min, t_max,
                               caption="Top of the substrate (same scale)."))

    out.append("<h2>Stack, bottom to top</h2><table><tr><th class='l'>Slice</th>"
               "<th>Thickness (&micro;m)</th><th>k (W/m&middot;K)</th>"
               "<th>Coverage</th></tr>")
    cells = m.nx * m.ny
    for s in m.slices:
        here = s.t > 0
        if not here.any():
            continue
        ks = np.unique(np.round(s.k[here], 4))
        ts = np.unique(np.round(s.t[here], 3))
        k_txt = f"{ks[0]:g}" if ks.size == 1 else f"{ks.min():g} to {ks.max():g}"
        t_txt = f"{ts[0]:g}" if ts.size == 1 else f"{ts.min():g} to {ts.max():g}"
        out.append(f"<tr><td class='l'>{esc(s.name)}</td><td>{t_txt}</td><td>{k_txt}</td>"
                   f"<td>{100.0 * here.sum() / cells:.0f}%</td></tr>")
    out.append("</table>")

    base = (f"fixed at {m.base_temp_c:.1f} &deg;C" if m.base_mode == "fixed" else
            f"{m.base_htc:g} W/m&sup2;&middot;K to ambient at {m.ambient_c:.1f} &deg;C")
    top = ("sealed (no loss)" if m.top_htc <= 0 else
           f"{m.top_htc:g} W/m&sup2;&middot;K to ambient at {m.ambient_c:.1f} &deg;C")
    out.append("<h2>Setup and checks</h2><table>"
               f"<tr><td class='l'>Base</td><td class='l'>{base}</td></tr>"
               f"<tr><td class='l'>Top surface</td><td class='l'>{top}</td></tr>"
               f"<tr><td class='l'>Grid</td><td class='l'>{m.nx} &times; {m.ny} cells at "
               f"{m.pitch_um:g} &micro;m, {len(m.slices)} slices, {result.unknowns} unknowns"
               "</td></tr>"
               f"<tr><td class='l'>Heat in / out</td><td class='l'>"
               f"{result.power_in * 1000:.2f} mW / {result.power_out * 1000:.2f} mW "
               f"(base {result.power_base * 1000:.2f}, top {result.power_top * 1000:.2f}); "
               f"balance error {result.energy_error * 100:.4f}%</td></tr></table>")
    if result.warnings:
        out.append("<h2>Warnings</h2><ul>" + "".join(f"<li>{esc(w)}</li>"
                                                      for w in result.warnings) + "</ul>")
    out.append("<p class='note'>Steady-state conduction only, with conductivity constant "
               "with temperature. Material values are the ones listed above; results are "
               "only as good as those values. Treat this as a design-stage estimate and "
               "confirm marginal parts by measurement.</p></body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    return path
