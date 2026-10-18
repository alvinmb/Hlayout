"""Assembly drawing generator.

Composes a complete assembly drawing -- drawing sheet (border + title
block), a scaled top view of the design, a pin function table and a
resistor table -- each built in its own cell by the existing Session
generators, then placed as Srefs into one assembly cell sized to a real
ISO sheet:

    ASSEMBLY            (the drawing; PRINT / export this)
      +- ASSY_SHEET     generate_drawing_sheet()
      +- ASSY_TOPVIEW   mask_op_top_view(), placed at a standard scale
      +- ASSY_PINTABLE  generate_pin_function_table()
      +- ASSY_RESTABLE  generate_resistor_table()
      +- ASSY_BONDTABLE generate_bond_table() (chip-and-wire designs with a bond diagram)

Everything on the sheet is specified in real paper millimetres. The top
view is regenerated with its line width, hatch pitch and ref-des height
divided by the chosen scale, so they come out at the requested size on
paper rather than being magnified along with the geometry. Tables are
built directly at paper size (their DBU defaults are multiplied up), so
they are placed at magnification 1.

The layout picks the largest standard scale that fits, trying the tables
both as a column down the right of the drawing area and as a row along
the bottom (just above the title block), and keeps whichever arrangement
gives the bigger view.

All parameters are saved to the design's tech_params (so they ride along
in the sidecar), and a re-run with no arguments rebuilds the drawing from
the saved settings -- e.g. after editing resistors or moving parts.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from ..core.errors import LayoutError
from ..core.units import mm_to_dbu

ASSEMBLY_SETTINGS_KEY = "assembly_drawing"

# Standard drawing scales (n:1), largest first.
STANDARD_SCALES = (100.0, 50.0, 25.0, 20.0, 10.0, 5.0, 4.0, 2.5, 2.0, 1.0,
                   0.5, 0.4, 0.25, 0.2, 0.1)

# The generators' own DBU defaults are sized for this text height.
_GEN_TEXT_DBU = 150.0
_PIN_ROW, _PIN_COLS = 250.0, (800.0, 2000.0)
_RES_ROW, _RES_COLS = 250.0, (1200.0, 2500.0, 1000.0, 2000.0, 2000.0)
_BOND_ROW, _BOND_COLS = 250.0, (600.0, 900.0, 1500.0, 1500.0, 1500.0, 1100.0, 1000.0, 900.0)

DEFAULT_ASSEMBLY_SETTINGS = {
    "cell_name": "ASSEMBLY",
    "design_cell": None,          # None = library.top_cell_name
    "layer": None,                # None = Top View result layer
    "line_layer": None,           # None = same as layer
    # sheet
    "size": "A3",
    "orientation": "landscape",
    "margin_mm": 10.0,
    "title_block_height_mm": 45.0,
    "title_text_mm": 3.0,
    "sheet_line_mm": 0.5,
    "gap_mm": 8.0,
    "title": "", "dwg_no": "", "issue": "",
    "drawn_by": "", "drawn_date": "",
    "checked_by": "", "checked_date": "",
    "approved_by": "", "approved_date": "",
    # top view
    "scale": None,                # None = largest standard scale that fits
    "view_line_mm": 0.25,
    "view_hatch_mm": 1.0,
    "view_refdes_mm": 2.5,
    "view_label": True,           # "TOP VIEW  SCALE n:1" under the view
    "label_text_mm": 3.5,
    # tables
    "table_text_mm": 2.5,
    "table_line_mm": 0.25,
    "layout": "auto",             # "auto" | "side" | "bottom"
    "pin_table": True,
    "pin_count": None,            # None = guess from a placed "<n>_PIN" package
    "pins_per_group": 10,
    "pin_functions": None,        # {pin: "function"}
    "resistor_table": True,
    "bond_table": True,           # only when the design has a bond diagram
}

_PART_NAMES = ("SHEET", "TOPVIEW", "PINTABLE", "RESTABLE", "BONDTABLE")


# --- settings ----------------------------------------------------------------
#
# Two layers of memory:
#   * the design's own tech_params (travels with the .gds sidecar) --
#     every field, so a design always rebuilds exactly as last drawn;
#   * the app settings (~/.hlayout/settings.json) -- the house-style
#     fields that aren't specific to one design (sheet size, orientation,
#     scales, text sizes, who draws/checks/approves ...), so a NEW design
#     starts from what you used last rather than the factory defaults.

APP_SETTINGS_KEY = "assembly_defaults"

#: fields that only make sense for one design -- never carried to others
DESIGN_ONLY_KEYS = frozenset({
    "design_cell", "layer", "line_layer", "cell_name",
    "title", "dwg_no", "issue", "drawn_date", "checked_date", "approved_date",
    "pin_count", "pin_functions",
})


def _app_defaults() -> dict:
    try:
        from ..io.app_settings import load_app_settings
        data = load_app_settings().get(APP_SETTINGS_KEY) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if k in DEFAULT_ASSEMBLY_SETTINGS and k not in DESIGN_ONLY_KEYS}


def _remember_app_defaults(settings: dict) -> None:
    try:
        from ..io.app_settings import load_app_settings, save_app_settings
        allset = load_app_settings()
        allset[APP_SETTINGS_KEY] = {k: v for k, v in settings.items()
                                    if k in DEFAULT_ASSEMBLY_SETTINGS
                                    and k not in DESIGN_ONLY_KEYS}
        save_app_settings(allset)
    except (OSError, TypeError, ValueError):
        pass        # failing to remember house style must never block a build


def load_assembly_settings(library) -> dict:
    """Assembly drawing settings, complete: factory defaults, overlaid
    by the remembered house-style defaults (app settings), overlaid by
    this design's own saved settings."""
    out = json.loads(json.dumps(DEFAULT_ASSEMBLY_SETTINGS))
    out.update(_app_defaults())
    raw = library.tech_params.get(ASSEMBLY_SETTINGS_KEY)
    if raw:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, dict):
            out.update({k: v for k, v in data.items() if k in DEFAULT_ASSEMBLY_SETTINGS})
    if isinstance(out.get("pin_functions"), dict):
        out["pin_functions"] = {int(k): str(v) for k, v in out["pin_functions"].items()}
    return out


def save_assembly_settings(library, settings: dict, remember: bool = True) -> None:
    """Merge ``settings`` into the design's saved assembly settings and,
    with ``remember`` (default), store the house-style fields as the
    app-wide defaults for new designs too."""
    cur = load_assembly_settings(library)
    cur.update({k: v for k, v in (settings or {}).items() if k in DEFAULT_ASSEMBLY_SETTINGS})
    if isinstance(cur.get("pin_functions"), dict):
        cur["pin_functions"] = {str(k): v for k, v in cur["pin_functions"].items()}
    library.tech_params[ASSEMBLY_SETTINGS_KEY] = json.dumps(cur)
    if remember:
        _remember_app_defaults(cur)


# --- helpers -----------------------------------------------------------------

def _part(cell_name: str, part: str) -> str:
    base = "ASSY" if cell_name.upper() == "ASSEMBLY" else cell_name
    return f"{base}_{part}"


def guess_pin_count(library, design_cell: str) -> Optional[int]:
    """Pin count of the package the design sits in, guessed from a placed
    cell named like '20_PIN_pkg' / 'LCC28' / 'CFP-16'. None if nothing
    looks like a package."""
    cell = library.cells.get(design_cell)
    if cell is None:
        return None
    pats = (re.compile(r"(\d+)[ _-]?PIN", re.I),
            re.compile(r"(?:LCC|CFP|DIP|SOIC|QFP|FP)[ _-]?(\d+)", re.I))
    names = [r.cell_name for r in cell.srefs] + [a.cell_name for a in getattr(cell, "arefs", [])]
    for name in names:
        for p in pats:
            m = p.search(name)
            if m and 2 <= int(m.group(1)) <= 400:
                return int(m.group(1))
    return None


def _group_size(pin_count: int, wanted: int) -> int:
    """The divisor of pin_count closest to `wanted` (ties go to the larger),
    so generate_pin_function_table() gets equal groups and never has to
    invent phantom pins -- e.g. 28 pins at 10 per group -> 7."""
    wanted = max(1, int(wanted))
    divisors = [d for d in range(1, pin_count + 1) if pin_count % d == 0]
    return min(divisors, key=lambda d: (abs(d - wanted), -d))


def _size(bb):
    return (bb[2] - bb[0], bb[3] - bb[1])


def _pick_scale(fit: float) -> float:
    for sc in STANDARD_SCALES:
        if sc <= fit:
            return sc
    raise LayoutError(f"design is too large for the sheet even at "
                      f"{STANDARD_SCALES[-1]:g}:1")


def _scale_text(scale: float) -> str:
    if scale >= 1:
        return f"{scale:g}:1"
    return f"1:{1 / scale:g}"


def _top_view_kwargs(library, design_cell, out_cell, scale, st):
    """mask_op_top_view() kwargs: the design's saved Top View settings with
    widths/heights converted from paper mm at `scale`."""
    from ..io.techfile import load_topview_settings
    tv = load_topview_settings(library)
    result_layer = int(tv.get("result_layer", -1))
    if result_layer < 0:
        raise LayoutError("no Top View result layer set -- pick one in Tools > Top View...")
    excluded = list(tv.get("excluded") or [])
    if result_layer not in excluded:
        excluded.append(result_layer)
    kw = {
        "out_cell": out_cell,
        "cell_names": [design_cell],
        "flatten": bool(tv.get("flatten", True)),
        "line_width": mm_to_dbu(st["view_line_mm"]) / scale,
        "exclude_layers": excluded,
        "refdes": bool(tv.get("refdes", True)),
        "refdes_height": mm_to_dbu(st["view_refdes_mm"]) / scale,
    }
    if tv.get("hatch") and st["view_hatch_mm"] > 0:
        kw["hatch_conductor_pitch"] = mm_to_dbu(st["view_hatch_mm"]) / scale
        kw["hatch_conductor_angles"] = tuple(tv.get("hatch_angles") or (45.0, -45.0))
    if float(tv.get("simplify_um", 0)) > 0:
        kw["simplify"] = float(tv["simplify_um"])
    return result_layer, kw


# --- main entry ----------------------------------------------------------------

def generate_assembly_drawing(session, save: bool = True, **overrides) -> dict:
    """Build (or rebuild) the assembly drawing. Any key of
    DEFAULT_ASSEMBLY_SETTINGS may be passed; unspecified keys come from
    the design's saved settings. With save=True (default) the merged
    settings are written back, so the next run with no arguments
    reproduces this drawing.

    Returns {"cell", "scale", "layout", "parts": {...}, "warnings": [...]}.
    One undo step."""
    from ..io.techfile import load_resistor_list
    from .api import SHEET_SIZES_MM

    lib = session.library
    unknown = set(overrides) - set(DEFAULT_ASSEMBLY_SETTINGS)
    if unknown:
        raise LayoutError(f"unknown assembly setting(s): {', '.join(sorted(unknown))}")
    st = load_assembly_settings(lib)
    st.update(overrides)
    warnings = []

    design = st["design_cell"] or lib.top_cell_name
    asm = (st["cell_name"] or "ASSEMBLY").strip()
    if not design or design not in lib.cells:
        raise LayoutError(f"design cell {design!r} not found")
    if design == asm:
        raise LayoutError("the assembly cell can't be the design cell itself")
    names = {p: _part(asm, p) for p in _PART_NAMES}

    size = str(st["size"]).upper()
    if size not in SHEET_SIZES_MM:
        raise LayoutError(f"unknown size {size!r}; must be one of {sorted(SHEET_SIZES_MM)}")
    short_mm, long_mm = SHEET_SIZES_MM[size]
    portrait = str(st["orientation"]).lower() == "portrait"
    sheet_w = mm_to_dbu(short_mm if portrait else long_mm)
    sheet_h = mm_to_dbu(long_mm if portrait else short_mm)
    margin = mm_to_dbu(st["margin_mm"])
    gap = mm_to_dbu(st["gap_mm"])
    area = (margin + gap,
            margin + mm_to_dbu(st["title_block_height_mm"]) + gap,
            sheet_w - margin - gap,
            sheet_h - margin - gap)

    tv_layer = None
    try:
        from ..io.techfile import load_topview_settings
        tv_layer = int(load_topview_settings(lib).get("result_layer", -1))
    except Exception:
        pass
    layer = st["layer"] if st["layer"] is not None else tv_layer
    if layer is None or (isinstance(layer, int) and layer < 0):
        raise LayoutError("no drawing layer -- pass layer= or set the Top View result layer")
    line_layer = st["line_layer"] if st["line_layer"] is not None else layer

    k = mm_to_dbu(st["table_text_mm"]) / _GEN_TEXT_DBU
    table_line = mm_to_dbu(st["table_line_mm"])

    with session.batch():
        # 0. drop the previous drawing first -- it references the part
        #    cells, which can't be rebuilt while still placed
        if asm in lib.cells:
            lib.delete_cell(asm)

        # 1. sheet ------------------------------------------------------------
        session.generate_drawing_sheet(
            names["SHEET"], layer, size=size,
            orientation="portrait" if portrait else "landscape",
            margin_mm=st["margin_mm"], title_block_height_mm=st["title_block_height_mm"],
            text_height_mm=st["title_text_mm"], line_width_mm=st["sheet_line_mm"],
            line_layer=line_layer,
            **{f: st[f] for f in ("title", "dwg_no", "issue", "drawn_by", "drawn_date",
                                  "checked_by", "checked_date", "approved_by",
                                  "approved_date")})

        # 2. tables (paper size, magnification 1) --------------------------------
        tables = []
        if st["pin_table"]:
            n = st["pin_count"] or guess_pin_count(lib, design)
            if not n:
                warnings.append("pin table skipped: no pin_count given and no "
                                "'<n>_PIN' package found in the design")
            else:
                g = _group_size(int(n), st["pins_per_group"])
                session.generate_pin_function_table(
                    names["PINTABLE"], layer, int(n), pins_per_group=g,
                    functions=st["pin_functions"],
                    row_height=_PIN_ROW * k, col_widths=tuple(c * k for c in _PIN_COLS),
                    text_height=_GEN_TEXT_DBU * k, line_layer=line_layer,
                    line_width=table_line)
                tables.append(names["PINTABLE"])
        if st["resistor_table"]:
            if not load_resistor_list(lib):
                warnings.append("resistor table skipped: no resistors saved in "
                                "the Resistor Designer")
            else:
                session.generate_resistor_table(
                    names["RESTABLE"], layer,
                    row_height=_RES_ROW * k, col_widths=tuple(c * k for c in _RES_COLS),
                    text_height=_GEN_TEXT_DBU * k, line_layer=line_layer,
                    line_width=table_line)
                tables.append(names["RESTABLE"])
        if st.get("bond_table", True) and session.bond_diagram():
            session.generate_bond_table(
                names["BONDTABLE"], layer,
                row_height=_BOND_ROW * k, col_widths=tuple(c * k for c in _BOND_COLS),
                text_height=_GEN_TEXT_DBU * k, line_layer=line_layer,
                line_width=table_line)
            tables.append(names["BONDTABLE"])
        tsz = {t: _size(lib.cell_bbox(t)) for t in tables}

        # 3. layout + scale -------------------------------------------------------
        dx0, dy0, dx1, dy1 = lib.cell_bbox(design)
        dw, dh = dx1 - dx0, dy1 - dy0
        if dw <= 0 or dh <= 0:
            raise LayoutError(f"design cell {design!r} is empty")
        # allowance for ref-des text / line width hanging past the geometry
        label_h = mm_to_dbu(st["label_text_mm"]) * 2.5 if st["view_label"] else 0
        pad = mm_to_dbu(st["view_refdes_mm"]) * 2

        ax0, ay0, ax1, ay1 = area
        options = {}
        rows = []                      # bottom layout: rows of tables, bottom row first
        if tables:
            col_w = max(w for w, _ in tsz.values())
            col_h = sum(h for _, h in tsz.values()) + gap * (len(tables) - 1)
            if col_w + gap < ax1 - ax0 and col_h <= ay1 - ay0:
                options["side"] = (ax0, ay0, ax1 - col_w - gap, ay1)
            # bottom: tables side by side under the view, wrapping onto
            # another row (stacked upwards) when they don't fit across
            if col_w <= ax1 - ax0:
                cur, cur_w = [], 0.0
                for tname in tables:
                    w = tsz[tname][0]
                    if cur and cur_w + gap + w > ax1 - ax0:
                        rows.append(cur)
                        cur, cur_w = [], 0.0
                    cur_w += (gap if cur else 0) + w
                    cur.append(tname)
                rows.append(cur)
                block_h = sum(max(tsz[n][1] for n in r) for r in rows) + gap * (len(rows) - 1)
                if block_h + gap < ay1 - ay0:
                    options["bottom"] = (ax0, ay0 + block_h + gap, ax1, ay1)
        else:
            options["side"] = area
        want = str(st["layout"]).lower()
        if want in ("side", "bottom"):
            if want not in options:
                raise LayoutError(f"tables don't fit in a {want} layout on "
                                  f"{size} {st['orientation']}")
            options = {want: options[want]}
        if not options:
            raise LayoutError(f"tables don't fit on {size} {st['orientation']} -- "
                              "try a larger sheet, landscape, or smaller table_text_mm")

        def fit(region):
            rw, rh = region[2] - region[0], region[3] - region[1] - label_h
            return min(rw / (dw + 0.0), rh / (dh + 0.0))

        layout = max(options, key=lambda o: (fit(options[o]), o == "side"))
        region = options[layout]
        if st["scale"]:
            scale = float(st["scale"])
            if scale > fit(region) * 1.0001:
                warnings.append(f"scale {_scale_text(scale)} overflows the drawing area")
        else:
            # shrink the fit slightly for ref-des overhang (in paper units)
            rw = region[2] - region[0] - 2 * pad
            rh = region[3] - region[1] - label_h - 2 * pad
            scale = _pick_scale(min(rw / dw, rh / dh))

        # 4. top view at paper-correct widths -------------------------------------
        result_layer, kw = _top_view_kwargs(lib, design, names["TOPVIEW"], scale, st)
        session.current_cell_name = design
        session.mask_op_top_view(result_layer, **kw)
        vx0, vy0, vx1, vy1 = lib.cell_bbox(names["TOPVIEW"])

        # 5. assemble -----------------------------------------------------------------
        lib.new_cell(asm)
        session.current_cell_name = asm
        session.sref(names["SHEET"], (0, 0))

        rx0, ry0, rx1, ry1 = region
        cx = (rx0 + rx1) / 2.0
        cy = (ry0 + label_h + ry1) / 2.0
        vcx, vcy = (vx0 + vx1) / 2.0, (vy0 + vy1) / 2.0
        session.sref(names["TOPVIEW"], (round(cx - vcx * scale), round(cy - vcy * scale)),
                     magnification=scale)
        if st["view_label"]:
            th = mm_to_dbu(st["label_text_mm"])
            text = f"TOP VIEW   SCALE {_scale_text(scale)}"
            view_bottom = cy - (vcy - vy0) * scale
            session.text(layer, (round(cx - len(text) * th * 0.3),
                                 round(view_bottom - th * 2.0)),
                         text, height=round(th))

        if layout == "side":
            y = ay1
            for tname in tables:
                bx0, by0, bx1, by1 = lib.cell_bbox(tname)
                session.sref(tname, (round(ax1 - bx1), round(y - by1)))
                y -= (by1 - by0) + gap
        else:
            # rows centred across the drawing area, tops aligned, the
            # first row (pin + resistor tables) directly under the view
            y_top = region[1] - gap
            for row in rows:
                row_w = sum(tsz[n][0] for n in row) + gap * (len(row) - 1)
                row_h = max(tsz[n][1] for n in row)
                x = (ax0 + ax1 - row_w) / 2.0
                for tname in row:
                    bx0, by0, bx1, by1 = lib.cell_bbox(tname)
                    session.sref(tname, (round(x - bx0), round(y_top - by1)))
                    x += (bx1 - bx0) + gap
                y_top -= row_h + gap

        if save:
            save_assembly_settings(lib, {k2: v for k2, v in st.items()
                                         if k2 in overrides})

    return {"cell": asm, "scale": scale, "layout": layout,
            "parts": {p.lower(): n for p, n in names.items()
                      if n in lib.cells},
            "warnings": warnings}
