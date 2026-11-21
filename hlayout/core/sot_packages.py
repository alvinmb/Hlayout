# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""The SOT / small-outline package family -- one table for everything
that draws or models these parts:

* 2D_Part_Generator_scripts/generate_sot_packages.py draws the land
  patterns (footprint cells) from PACKAGES and builds the matching STEP
  models from step_parts();
* hlayout.io.component_library builds its SOT_* STEP library entries
  from library_sot_packages() (plus two library-only extras);
* hlayout.core.smdlib's built-in T_SOT23 takes its SOT-23 numbers from
  here.

All dimensions in mm. Datasheet sources are noted per entry.
"""

#: name -> nominal geometry, all mm.
#:   pins       total pin count (pads are numbered 1..pins)
#:   body       (length, width) of the moulded body outline -- for
#:              "lr" (default) packages this is (across-the-rows,
#:              along-a-row); for "tb" packages it's (along-a-row,
#:              across-the-rows) instead, i.e. the two axes are
#:              transposed relative to "lr" -- see `rows` below.
#:   pitch      centre-to-centre spacing of pads along each row
#:   row_gap    centre-to-centre spacing between the two pad rows
#:   pad        (length, width) of an ordinary land pad, same axis
#:              convention as body: transposed for "tb" packages.
#:   tab_pin    pin number drawn as an oversized tab, or None
#:   tab        (length, width) of that tab pad, when tab_pin is set
#:   rows       "lr" (default, omitted) -- rows on the LEFT/RIGHT
#:              walls, pins spread along Y, pin 1 at the bottom of the
#:              left row. "tb" -- rows on the TOP/BOTTOM walls, pins
#:              spread along X, pin 1 at the LEFT of the bottom row
#:              (row 1 = bottom, left-to-right; row 2 = top,
#:              right-to-left, continuing the same winding direction)
#:              -- see build_package() and _spec_step_leads().
#:   row1       pins in row 1 (default: half, rounded up) -- 3 for the
#:              "3 leads + tab" power packages (SOT-89, SOT-223)
#:   row_y      ("tb") (row 1, row 2) pad-centre Y from the body centre,
#:              for packages whose rows aren't symmetric (SOT-89's tab)
#:   tab_label  pin number written on the tab pad (default: tab_pin) --
#:              "2" where the tab IS lead 2's metal (KiCad's TabPin2)
#:   lead_reach / tab_reach   real lead / tab reach from the body wall
#:              for the layer-61 outline, when not derivable from span
#:   tab_lead_w the tab's real metal width for the 3D model
#:   flat       3D only: flat leads + exposed underside tab
#:              (make_power_flat kwargs, less body/positions)
PACKAGES = {
    # 2026-09-23: SOT-23/23-5/23-6/143/363 switched "lr" -> "tb". Real
    # parts in this family have leads exiting the TOP/BOTTOM long
    # edges (spread along X, same-row pads share a Y and differ in X),
    # not the left/right short ends -- same class of bug as the SOT-8
    # gull-wing family below. `pad`/`tab` tuples are swapped to match
    # "tb"'s (along-row, across-row) axis convention -- see PACKAGES's
    # own docstring above -- otherwise the along-row pad width would
    # exceed `pitch` and adjacent pads would overlap.
    # 2026-11-19: SOT-23 (TO-236) to the datasheet drawing -- body
    # 2.8 x 1.3 (1.2-1.4), pins 1 and 2 are 1.9 apart on the bottom
    # row with pin 3 centred on the top row (pitch=1.9 with 2+1 pins
    # gives exactly that; the old 0.95 put pins 1/2 only 0.95 apart),
    # overall lead span 2.3 (2.1-2.5), lead width 0.43 (0.38-0.48),
    # lead thickness 0.12 (0.09-0.15), foot 0.35 (0.15-0.45), seated
    # height 0.9. span/lead_* override the generic lead estimates for
    # both the layer-61 lead outline and the 3D model.
    "SOT-23":   dict(pins=3, body=(2.8, 1.3), pitch=1.9, row_gap=2.3, pad=(0.6, 1.0), rows="tb",
                     span=2.3, lead_w=0.43, lead_t=0.12, lead_out=0.15, lead_foot=0.35),
    # SOT-23-5: pins 4 and 5 sit at the two ENDS of the top row (over
    # pins 3 and 1), 1.9 apart -- not packed together in the middle at
    # the 0.95 pitch. row2 gives the top row's centre-relative
    # positions in pin order (pin 4 right, pin 5 left).
    "SOT-23-5": dict(pins=5, body=(2.9, 1.6), pitch=0.95, row_gap=2.6, pad=(0.55, 1.1), rows="tb",
                     row2=(0.95, -0.95),
                     span=2.8, lead_w=0.4, lead_t=0.12, lead_out=0.15, lead_foot=0.45),
    # SOT-23-5/-6 leads (JEDEC MO-178): span 2.8 (2.6-3.0), lead width
    # 0.4 (0.3-0.5), foot 0.45 (0.3-0.6).
    "SOT-23-6": dict(pins=6, body=(2.9, 1.6), pitch=0.95, row_gap=2.6, pad=(0.55, 1.1), rows="tb",
                     span=2.8, lead_w=0.4, lead_t=0.12, lead_out=0.15, lead_foot=0.45),
    # 2026-11-21: SOT-89 and SOT-223 are 3 leads in a row plus a tab
    # opposite (the tab is lead 2's metal), not 2 + 2 as drawn before.
    # SOT-89 to the datasheet drawing: body D 4.5 x E1 2.5, height A
    # 1.5, FLAT leads (not gull-wing) e 1.5 pitch, b 0.42 (middle b1
    # 0.47), L 1.05 out of the wall, c 0.40 thick; tab D1 1.6 wide,
    # standing 0.5 proud of the far wall (E 4.05 - E1 2.5 - L 1.05),
    # V 2.6 of metal from its tip back under the body. Land: 0.7 x 1.3
    # lead pads (0.1 under the body, 0.15 past the toe); the tab pad
    # covers D1 + 0.15 a side and the whole V run + 0.15.
    "SOT-89":   dict(pins=4, body=(4.5, 2.5), pitch=1.5, row_gap=2.325, pad=(0.7, 1.3), rows="tb",
                     row1=3, row_y=(-1.8, 0.525), tab_pin=4, tab_label="2", tab=(1.9, 2.75),
                     lead_w=0.42, lead_reach=1.05, tab_reach=0.5,
                     flat=dict(lead_ws=(0.42, 0.47, 0.42), lead_l=1.05, lead_t=0.40,
                               tab_w=1.6, tab_out=0.5, tab_back=2.6)),
    "SOT-143":  dict(pins=4, body=(2.9, 1.3), pitch=0.95, row_gap=2.3, pad=(0.55, 1.0),
                     tab_pin=4, tab=(1.1, 1.0), rows="tb"),
    # SOT-223 to the datasheet drawing: body D 6.50 x E 3.50, A 1.60,
    # gull-wing leads b 0.70 at e1 2.30, tab lead b1 3.00, overall span
    # E1 7.00, lead C 0.25 thick, foot L 0.95 -- reach per side is
    # stub + foot (the foot starts where the stub ends), so the stub is
    # (7.00 - 3.50) / 2 - 0.95 = 0.80. Land pads as KiCad's
    # SOT-223-3_TabPin2 (1.5 x 2.0 leads, 3.8 x 2.0 tab, rows 6.3 apart).
    "SOT-223":  dict(pins=4, body=(6.5, 3.5), pitch=2.3, row_gap=6.3, pad=(1.5, 2.0), rows="tb",
                     row1=3, tab_pin=4, tab_label="2", tab=(3.8, 2.0),
                     span=7.0, lead_w=0.7, tab_lead_w=3.0, lead_t=0.25, lead_out=0.80, lead_foot=0.95),
    "SOT-363":  dict(pins=6, body=(2.0, 1.25), pitch=0.65, row_gap=2.0, pad=(0.4, 0.7), rows="tb"),
    # Gull-wing multi-lead ICs at the standard 1.27 mm (0.05") SOIC pitch --
    # see the "NOTE ON NAMING" in the module docstring. These are "tb"
    # packages: rows on the TOP/BOTTOM walls (real SOIC leads exit the
    # two LONG sides, and here that's the top/bottom pair), pins spread
    # along X, matching a real datasheet pad-layout diagram directly
    # (2026-09 bug report, round 4 -- pin 1 is bottom-LEFT of the
    # BOTTOM row, row 1 = pins 1..per_row left-to-right along the
    # bottom, row 2 = pins per_row+1..pins right-to-left along the top;
    # confirmed against the user's own reference image: BL=1, BR=4,
    # TR=5, TL=8 for an 8-pin part). body/pad here are therefore
    # (along-a-row, across-the-rows) -- the OPPOSITE axis order from the
    # "lr" packages above -- so body_l is the dimension that grows with
    # pin count. SOT-8's body/row_gap/pitch match a real 8-pin SOIC
    # datasheet drawing (5.1 x 3.95, row_gap/lead-span 6.0, pitch 1.27,
    # all confirmed against it directly) -- SOT-16 shares its narrow-
    # body width (3.95) for the same reason, but keeps its own already-
    # reasonable along-row length (9.9) since no 16-pin reference was
    # checked against.
    "SOT-8":    dict(pins=8,  body=(5.1, 3.95), pitch=1.27, row_gap=6.0, pad=(0.6, 1.5), rows="tb"),
    "SOT-16":   dict(pins=16, body=(9.9, 3.95), pitch=1.27, row_gap=6.0, pad=(0.6, 1.5), rows="tb"),
    "SOT-18":   dict(pins=18, body=(11.5, 7.5), pitch=1.27, row_gap=9.8, pad=(0.6, 1.5), rows="tb"),
    "SOT-20":   dict(pins=20, body=(12.8, 7.5), pitch=1.27, row_gap=9.8, pad=(0.6, 1.5), rows="tb"),
    "SOT-24":   dict(pins=24, body=(15.4, 7.5), pitch=1.27, row_gap=9.8, pad=(0.6, 1.5), rows="tb"),
    "SOT-28":   dict(pins=28, body=(17.9, 7.5), pitch=1.27, row_gap=9.8, pad=(0.6, 1.5), rows="tb"),
}

#: body height, mm -- 3D export only, no use in the 2D footprint.
#: SOT-8/16 are narrow-body SOICs (JEDEC MS-012, A max 1.75);
#: SOT-18/20/24/28 are wide-body (SOIC-W, A 2.35-2.65).
HEIGHT_MM = {
    "SOT-23": 0.9, "SOT-23-5": 1.1, "SOT-23-6": 1.1,
    "SOT-89": 1.5, "SOT-143": 1.1, "SOT-223": 1.6, "SOT-363": 0.95,
    "SOT-8": 1.75, "SOT-16": 1.75, "SOT-18": 2.5,
    "SOT-20": 2.5, "SOT-24": 2.5, "SOT-28": 2.5,
}

#: real lead width ("b" on a datasheet), mm -- 3D export only for the
#: gull-wing sub-family. The 2D land pad is deliberately wider (see
#: PACKAGES's own pad=) for a real solderable copper pad, but the
#: actual metal lead itself -- what the 3D model should show -- is
#: this much narrower, matching the same 8-pin SOIC reference drawing.
LEAD_WIDTH_MM = 0.38


def _spec_step_leads(spec, lead_w=None):
    """leads for make_dip()/make_dip_x() from a PACKAGES entry -- same
    (relative-to-centre) positions build_package() itself computes for
    its two rows, just returned as plain data instead of drawn, so the
    2D cell and the 3D model can never drift apart. Works for both
    `rows` conventions: for "lr" the along-row axis is Y and the
    across-row pad dimension is pad[1]; for "tb" the along-row axis is
    X and the across-row (protrusion) pad dimension is pad[1] too (pad
    is stored (along-row, across-row) for "tb" -- see PACKAGES's own
    docstring). `lead_w` overrides the ordinary (non-tab) lead's
    along-row width -- the 2D land pad is deliberately wider than the
    real metal lead (see LEAD_WIDTH_MM); defaults to the 2D pad's own
    along-row width when not given. A tab_pin's own tab width is never
    overridden."""
    pins = spec["pins"]
    pitch = spec["pitch"]
    rows = spec.get("rows", "lr")
    # For "lr", pad=(protrusion, along-row-width) -> along-row is [1].
    # For "tb", pad=(along-row-width, protrusion) -> along-row is [0].
    along_row_idx = 0 if rows == "tb" else 1
    pad_w = lead_w if lead_w is not None else spec["pad"][along_row_idx]
    tab_pin = spec.get("tab_pin")
    tab = spec.get("tab")
    per_row = spec.get("row1", (pins + 1) // 2)
    tab_w = spec.get("tab_lead_w", tab[along_row_idx] if tab else None)

    def _positions(count):
        span = (count - 1) * pitch
        return [(-span / 2.0) + k * pitch for k in range(count)]

    def _leads(positions, first_pin):
        out = []
        for i, p in enumerate(positions):
            n = first_pin + i
            w = tab_w if tab_pin == n and tab else pad_w
            out.append((round(p, 4), w))
        return out

    row1 = _leads(_positions(per_row), 1)
    if spec.get("row2"):
        row2 = _leads(list(spec["row2"]), per_row + 1)
    else:
        row2 = list(reversed(_leads(_positions(pins - per_row), per_row + 1)))
    return row1, row2


def step_parts():
    """(name, "make_dip"/"make_dip_x", kwargs) for every PACKAGES entry
    -- see hlayout.io.example_step3d.make_dip()/make_dip_x(). Which one
    is used follows the spec's own `rows` ("tb" -> make_dip, rows along
    Y at the top/bottom walls; "lr"/default -> make_dip_x, rows along X
    at the left/right walls) -- matching this script's own 2D axis
    convention directly, no swapping needed. Bottom-left origin,
    matching build()'s own cells, so a STEP file here lines up with its
    GDS cell.

    KNOWN GAP: reach (below) is computed from the spec's ordinary pad
    size only -- a tab_pin's own bigger tab size isn't factored in, so
    SOT-89/143/223's 3D tab lead falls a little short of where the 2D
    tab pad's own outer edge actually sits (confirmed ~0.3 mm on
    SOT-89 -- _gullwing_side_x() only takes one lead_out for a whole
    side, not a per-lead override, so fixing this needs a real API
    change, not just a number tweak)."""
    parts = []
    for name, spec in PACKAGES.items():
        body_l, body_w = spec["body"]
        rows = spec.get("rows", "lr")
        row_gap = spec["row_gap"]
        if spec.get("flat"):
            # flat leads + exposed underside tab (SOT-89): row 1's
            # positions, each with its own real lead width
            flat = dict(spec["flat"])
            widths = flat.pop("lead_ws")
            row1, _row2 = _spec_step_leads(spec)
            leads = [(x, w) for (x, _w), w in zip(row1, widths)]
            parts.append((name, "make_power_flat", dict(
                L=body_l, W=body_w, H=HEIGHT_MM[name], leads=leads, **flat)))
            continue
        # The across-row (protrusion) pad dimension, and the body
        # dimension the leads protrude from, both depend on `rows` --
        # see PACKAGES's own docstring on the axis transpose for "tb".
        across_idx = 1 if rows == "tb" else 0
        pad_across = spec["pad"][across_idx]
        body_across = body_w if rows == "tb" else body_l
        # gull-wing ICs (SOT-8 and up) get their real, narrower metal
        # lead width in 3D; the transistor-family entries keep using
        # their pad width as before.
        lead_w = spec.get("lead_w", LEAD_WIDTH_MM if spec["pins"] >= 8 else None)
        row1, row2 = _spec_step_leads(spec, lead_w)
        # Reach the leads/feet need to cover, from the 3D body's own
        # wall out to roughly where the 2D pad's own outer edge sits --
        # see the module docstring's note that a pad can extend a
        # little past the outline. lead_t itself also adds to the
        # total reach (the "drop" segment has width lead_t), so it's
        # subtracted back out.
        lead_t = 0.15
        # A real gull-wing foot is the DOMINANT flat, solderable part of
        # the lead, and the stub where it exits the body is short -- a
        # flat 0.05/0.35 mm split (2026-09, first pass) still read as a
        # long stub with a barely-there foot (see reference photos:
        # 2026-09 bug report, round 2). Then: total tip reach was kept
        # close to the 2D land pad's own outer edge (reach - lead_t, as
        # before, but with 0.35 mm of extra budget folded in so there's
        # room for a real foot), split so the stub gets at most 0.5 mm
        # (floored at 0.15 mm so it's still visible) and the foot got
        # whatever was left over. For the larger SOIC-style packages
        # (SOT-8 and up) that leftover-budget approach handed the foot
        # 1.5-1.6 mm -- well past the real datasheet spec (LTC S-package
        # 16-lead SOIC, DWG 05-08-1610 Rev G: foot length .016-.050in =
        # 0.4-1.27mm) and nearly as long as half the body itself (2026-09
        # bug report, round 3). So the foot is now capped at 1.0 mm,
        # comfortably inside that spec range; any leftover budget beyond
        # stub+foot is simply unused rather than inflating the foot.
        reach = max((row_gap / 2.0 + pad_across / 2.0) - body_across / 2.0, 0.05)
        total_budget = max(reach - lead_t, 0.02) + lead_t + 0.35
        lead_out = min(0.5, max(0.15, total_budget * 0.25)) if rows != "tb" else min(0.25, max(0.15, total_budget * 0.25))
        lead_foot = min(1.0, max(0.15, total_budget - lead_out - lead_t))
        if "lead_out" in spec:
            # datasheet lead geometry given -- use it as-is
            lead_t = spec.get("lead_t", lead_t)
            lead_out = spec["lead_out"]
            lead_foot = spec.get("lead_foot", lead_foot)
        if rows == "tb":
            parts.append((name, "make_dip", dict(
                L=body_l, W=body_w, H=HEIGHT_MM[name],
                leads_neg_y=row1, leads_pos_y=row2,
                lead_t=lead_t, lead_out=lead_out, lead_foot=lead_foot,
            )))
        else:
            parts.append((name, "make_dip_x", dict(
                L=body_l, W=body_w, H=HEIGHT_MM[name],
                leads_neg_x=row1, leads_pos_x=row2,
                lead_t=lead_t, lead_out=lead_out, lead_foot=lead_foot,
            )))
    return parts


#: component-library STEP name -> PACKAGES name, for the entries the
#: library builds from this table (its SOT_323 / SOT_14 are its own).
LIBRARY_NAMES = {
    "SOT_23": "SOT-23",
    "SOT_89": "SOT-89",
    "SOT_223": "SOT-223",
    "SOT_16": "SOT-16",
    "SOT_18": "SOT-18",
}


def library_sot_packages():
    """{library_name: kwargs} for hlayout.io.component_library's SOT
    entries, from the same step_parts() the 2D generator's own STEP
    models use -- so a library SOT_89.step and examples' SOT-89.step are
    the same part. Each entry carries "builder" (the macro function:
    "make_sot" for gull-wing parts, whose kwargs are make_dip()'s, or
    "make_power_flat"); "tb" parts only, the library's leads run along
    X on the +/-Y walls."""
    parts = {name: (func, kw) for name, func, kw in step_parts()}
    out = {}
    for lib_name, name in LIBRARY_NAMES.items():
        func, kw = parts[name]
        if func == "make_dip":
            out[lib_name] = dict(builder="make_sot", **{k: v for k, v in kw.items() if k != "lid"})
        elif func == "make_power_flat":
            out[lib_name] = dict(builder="make_power_flat", **kw)
        else:
            raise ValueError(f"{name}: no library builder for {func}")
    return out
