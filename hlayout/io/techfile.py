"""Technology file: save/load the layer stack (and open-ended global
parameters) as a separate JSON file from the design itself.

A "technology" in this tool is everything about *how a process draws*
that isn't part of any one design: the 64-layer stack (names, colours,
stipple, fill, visibility) plus a small open-ended dict of global
parameters (``Library.tech_params``) that can grow over time without
needing a file-format change. The usual workflow is to load a
technology file once at the start of a design (File > Load Technology
File..., or the ``TECH LOAD`` command), then work in the resulting
layer stack; the same design can later be re-saved as an updated
technology file (File > Save Technology File..., ``TECH SAVE``) to
capture layer edits made along the way, e.g. as a starting point for
the next design.

On disk this is plain JSON (not GDSII -- GDSII has no notion of layer
names, colours, or arbitrary global parameters), with the shape::

    {
        "format": "hlayout-tech",
        "version": 1,
        "layers": [
            {"number": 0, "name": "METAL1", "color": "#e6194b",
             "stipple": 0, "fill": true, "visible": true},
            ...
        ],
        "params": {"process": "0.35um", ...}
    }
"""

import json
import os
import re

from ..core.errors import LayoutError
from ..core.layers import NUM_LAYERS
from ..core.library import Library

FORMAT_NAME = "hlayout-tech"
FORMAT_VERSION = 1


def techfile_data(library: Library) -> dict:
    """Build the plain-dict/JSON-ready representation of a library's
    layer stack + global parameters -- the payload save_techfile()
    writes to disk."""
    layers = []
    for n in range(len(library.layers)):
        layer = library.layers[n]
        layers.append({
            "number": layer.number,
            "name": layer.name,
            "color": layer.color,
            "stipple": layer.stipple,
            "fill": layer.fill,
            "visible": layer.visible,
        })
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "layers": layers,
        "params": dict(library.tech_params),
    }


def save_techfile(library: Library, path: str) -> None:
    """Write library's layer stack + tech_params to a technology file
    (JSON) at path."""
    data = techfile_data(library)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.write("\n")


def load_techfile(path: str) -> dict:
    """Read a technology file from disk and return its parsed payload
    (the same shape as techfile_data()), without applying it to any
    library yet -- see apply_techfile()."""
    if not os.path.isfile(path):
        raise LayoutError(f"technology file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise LayoutError(f"could not read technology file {path}: {e}") from e
    if not isinstance(data, dict) or "layers" not in data:
        raise LayoutError(f"not a valid technology file: {path}")
    return data


def apply_techfile(library: Library, data: dict) -> None:
    """Apply a parsed technology file's layer stack + params onto an
    existing library, in place -- overwriting each layer's style with
    what the file specifies (layers the file doesn't mention are left
    untouched) and replacing tech_params wholesale with the file's
    params dict."""
    for entry in data.get("layers", []):
        try:
            n = int(entry["number"])
        except (KeyError, TypeError, ValueError):
            raise LayoutError(f"technology file has a layer entry with no valid number: {entry!r}")
        if not (0 <= n < NUM_LAYERS):
            raise LayoutError(f"technology file layer number {n} out of range 0-{NUM_LAYERS - 1}")
        library.layers.set_style(
            n,
            name=entry.get("name"),
            color=entry.get("color"),
            stipple=entry.get("stipple"),
            fill=entry.get("fill"),
            visible=entry.get("visible"),
        )
    params = data.get("params", {})
    if not isinstance(params, dict):
        raise LayoutError("technology file 'params' must be an object")
    library.tech_params = {str(k): str(v) for k, v in params.items()}


def load_and_apply_techfile(library: Library, path: str) -> dict:
    """Convenience: load_techfile() + apply_techfile() in one call --
    what File > Load Technology File.../TECH LOAD use. Returns the
    parsed payload in case a caller wants to report what was loaded."""
    data = load_techfile(path)
    apply_techfile(library, data)
    return data


# --- Thick-Film Resistor Designer design rules -----------------------------
#
# Tools > Thick-Film Resistor Designer... (hlayout/gui/resistor_designer.py)
# used to keep its "design rules" -- aim percentage, aspect-ratio limits,
# and which ink sheet resistances are available on the process -- in a
# module-level Settings singleton that only lived for the life of the
# running program. Since those rules are exactly the kind of "additional
# global parameters" this technology-file format was built to carry (see
# the module docstring), they're stored here as one JSON blob under a
# single tech_params key instead: no schema change to techfile_data()/
# apply_techfile() was needed, since tech_params already round-trips
# wholesale.

RESISTOR_RULES_KEY = "resistor_design_rules"

#: Falls back to these if the current technology (or a design with none
#: loaded yet) doesn't have resistor design rules saved -- matches the
#: defaults the original standalone tool shipped with.
DEFAULT_RESISTOR_RULES = {
    "aim_pct": 75.0,
    "ar_min": 0.25,
    "ar_max": 4.0,
    "available_inks": [10, 30, 100, 300, 1000, 3000,
                        10_000, 30_000, 100_000, 300_000, 1_000_000],
    # How the Thick-Film Resistor Designer auto-picks each resistor's
    # solution: "aspect" (nearest to square, per resistor -- the
    # original behaviour), "inks" (fewest distinct sheet-resistance inks
    # across the whole design), or "area" (smallest total resistor ink
    # area).
    "select_mode": "aspect",
}


def load_resistor_design_rules(library: Library) -> dict:
    """Read the resistor designer's design rules from library.tech_params.
    Always returns a complete dict (falling back to
    DEFAULT_RESISTOR_RULES key by key) so callers never need to guard
    against a partial or missing entry -- e.g. a technology file saved
    before this feature existed, or no technology file loaded at all."""
    rules = dict(DEFAULT_RESISTOR_RULES)
    raw = library.tech_params.get(RESISTOR_RULES_KEY)
    if not raw:
        return rules
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return rules
    if isinstance(data, dict):
        rules.update({k: data[k] for k in DEFAULT_RESISTOR_RULES if k in data})
    return rules


def save_resistor_design_rules(library: Library, rules: dict) -> None:
    """Write the resistor designer's design rules into library.tech_params
    (as one JSON-encoded entry) so Setup > Save Technology File... /
    TECH SAVE captures them alongside the layer stack."""
    payload = {k: rules[k] for k in DEFAULT_RESISTOR_RULES if k in rules}
    library.tech_params[RESISTOR_RULES_KEY] = json.dumps(payload)


# --- The resistor designer's working RESISTOR LIST -------------------------
#
# The rows in Tools > Thick-Film Resistor Designer... (ref-des, value,
# tolerance, power, chosen body width, and which ink solution was
# picked). Stored here so the list survives closing the window / the app
# -- it travels with the design in the .hlaux sidecar (File > Save GDS)
# and Setup > Save Technology File... / TECH SAVE, the same as the
# design rules above. Solutions are recomputed from the design rules on
# load; only the chosen ink's sheet resistance is persisted, so the
# selection is re-applied even if the available-ink list changes.

RESISTOR_LIST_KEY = "resistor_list"


def load_resistor_list(library: Library) -> list:
    """Read the saved resistor-designer rows. Returns a list of dicts
    (ref_des, value, tolerance, power_mw, body_width, selected_ink);
    an empty list when nothing is saved or the entry is malformed."""
    raw = library.tech_params.get(RESISTOR_LIST_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for d in data:
        if not isinstance(d, dict):
            continue
        try:
            sel = d.get("selected_ink")
            out.append({
                "ref_des": str(d.get("ref_des", "")),
                "value": float(d["value"]),
                "tolerance": str(d.get("tolerance", "5%")),
                "power_mw": float(d.get("power_mw", 0.0)),
                "body_width": float(d.get("body_width", 0.0)),
                "selected_ink": (float(sel) if sel is not None else None),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def save_resistor_list(library: Library, rows) -> None:
    """Write the resistor-designer rows into library.tech_params. `rows`
    is an iterable of dicts with the keys load_resistor_list() returns."""
    payload = []
    for r in rows:
        sel = r.get("selected_ink")
        payload.append({
            "ref_des": str(r.get("ref_des", "")),
            "value": float(r["value"]),
            "tolerance": str(r.get("tolerance", "5%")),
            "power_mw": float(r.get("power_mw", 0.0)),
            "body_width": float(r.get("body_width", 0.0)),
            "selected_ink": (float(sel) if sel is not None else None),
        })
    library.tech_params[RESISTOR_LIST_KEY] = json.dumps(payload)


# --- Thick-film resistor GEOMETRY rules (generating the actual shapes) -----
#
# Separate from RESISTOR_RULES_KEY above (which sizes a resistor -- picks
# an ink and computes body_width/body_length), these three parameters
# describe how the resistor-ink body and its two end conductor pads are
# actually drawn once a size has been picked, per the standard thick-film
# pad/overlap diagram:
#
#            |<---------- pad (A+B) --------->|<--- body_length (L) --->|
#      ______________________________       ___________________________
#     |                              |     |                            |
#     |         CONDUCTOR PAD        |=====|      RESISTOR INK BODY     |...
#     |______________________________|     |____________________________|
#            |<-- B -->|<-- A -->|
#                        (ink extends A under the pad for a reliable
#                         resistor-to-conductor overlap/contact)
#
#   A (overlap_a)   -- length of resistor ink material added at BOTH ends
#                      of the electrical body_length, so the ink extends
#                      under (overlaps) the conductor pad for contact.
#                      Total ink length drawn = body_length + 2*A.
#   B (pad_b)       -- minimum conductor pad length, beyond the overlap
#                      zone, at each end. Each pad's own footprint is
#                      (A + B) long, of which the innermost A sits directly
#                      over the resistor ink (the overlap) and B is pad
#                      only. Total structure length (pad to pad, outer
#                      edges) = body_length + 2*A + 2*B.
#   C (space_c)     -- minimum conductor space on the side: how far each
#                      pad extends beyond the resistor ink's width on
#                      either side. Pad width = body_width + 2*C.
#   D (glaze_oversize) -- the glaze overprint's oversize margin: the
#                      glaze is the same shape as the resistor ink body,
#                      grown by D on ALL FOUR sides (both ends AND both
#                      long edges), not just matching the ink's own
#                      footprint. Glaze size = (body_width + 2*D) x
#                      (body_length + 2*A + 2*D).
#   E (keepout_oversize) -- a DIELECTRIC KEEP-OUT rectangle over the
#                      resistor ink footprint BETWEEN THE TWO CONDUCTOR
#                      TERMINALS: body_length (the gap that separates the
#                      pads) by body_width (the ink width, NOT the wider
#                      pad width), on "keepout_layer", grown by E on all
#                      four sides. It marks where the inter-layer
#                      dielectric must be cleared so the printed resistor
#                      ink fires on a bare surface -- subtract this layer
#                      from the dielectric. Keep-out size = (body_width +
#                      2*E) x (body_length + 2*E). "keepout_layer" = -1
#                      (the default) disables it -- nothing is drawn.
#
# Also stored here: which layers the resistor ink, the conductor pads,
# the resistor glaze overprint, and the resistor's reference-designator
# label are drawn on, so Session.generate_resistor()
# (hlayout/scripting/api.py) and Tools > Thick-Film Resistor
# Designer.../"Place in Layout..." don't need those passed in
# separately every time.
#
# The resistor ink layer is not just one fixed number: a process
# usually prints a different ink on a different layer per sheet
# resistance (Rsh), so "ink_layers" maps each available ink's Rsh
# (as a string key, since JSON object keys must be strings) to the
# layer it's printed on -- e.g. {"100": 20, "1000": 21}. "resistor_layer"
# is the fallback used when a resistor's ink has no entry in that map
# (or when generating without an ink_rsh at all). See
# resistor_ink_layer() below. The conductor pad layer stays a single
# process-wide layer (still user-selectable from the technology file's
# named layers -- see Setup > Resistor Geometry Rules... and "Place in
# Layout..."'s own per-placement override); "glaze_layer" is the single
# layer the protective resistor-glaze overprint is drawn on; "doc_layer"
# is the DOCUMENTATION layer -- not a fab/silkscreen output -- that the
# resistor's reference designator (e.g. "R1") is labelled on, centred on
# the ink body. It is the only place the ref-des appears. See
# Session.generate_resistor()'s ref_des parameter.

RESISTOR_GEOMETRY_KEY = "resistor_geometry_rules"

#: All lengths in DBU (1 DBU = 1 micron, matching body_width/body_length
#: from the Resistor Designer's sizing table).
DEFAULT_RESISTOR_GEOMETRY = {
    "overlap_a": 200,
    "pad_b": 500,
    "space_c": 200,
    "glaze_oversize": 50,
    "keepout_oversize": 0,
    "resistor_layer": 30,
    "conductor_layer": 31,
    "glaze_layer": 32,
    "doc_layer": 33,
    "keepout_layer": -1,   # -1 = no dielectric keep-out drawn
    "ink_layers": {},
}


def load_resistor_geometry_rules(library: Library) -> dict:
    """Read the resistor ink/pad/glaze geometry rules (A, B, C, the
    per-ink layer map, and the conductor/glaze layer numbers) from
    library.tech_params. Always returns a complete dict (falling back
    to DEFAULT_RESISTOR_GEOMETRY key by key, including a fresh {} for
    ink_layers), same pattern as load_resistor_design_rules() above."""
    rules = dict(DEFAULT_RESISTOR_GEOMETRY)
    rules["ink_layers"] = {}
    raw = library.tech_params.get(RESISTOR_GEOMETRY_KEY)
    if not raw:
        return rules
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return rules
    if isinstance(data, dict):
        rules.update({k: data[k] for k in DEFAULT_RESISTOR_GEOMETRY if k in data})
        if not isinstance(rules.get("ink_layers"), dict):
            rules["ink_layers"] = {}
    return rules


def resistor_ink_layer(rules: dict, ink_rsh) -> int:
    """Resolve which layer a resistor's ink is drawn on, given its
    chosen ink's sheet resistance (ink_rsh, e.g. 1000 for 1k Ohm/sq) and
    a rules dict from load_resistor_geometry_rules(). Looks the Rsh up
    in rules["ink_layers"] (keyed by str(Rsh), since that's how it's
    stored in JSON); falls back to rules["resistor_layer"] if there's no
    entry for this ink (including when ink_rsh is None).

    ink_rsh may arrive as an int, a float (e.g. from a command-bar
    argument parsed with float()), or a string -- 1000, 1000.0, and
    "1000" are all tried against the same "1000" map key so a whole-
    valued float doesn't silently miss its mapping."""
    if ink_rsh is not None:
        ink_layers = rules.get("ink_layers", {})
        candidates = [str(ink_rsh)]
        try:
            as_float = float(ink_rsh)
            if as_float == int(as_float):
                candidates.append(str(int(as_float)))
        except (TypeError, ValueError):
            pass
        for key in candidates:
            if key in ink_layers:
                return int(ink_layers[key])
    return rules["resistor_layer"]


def save_resistor_geometry_rules(library: Library, rules: dict) -> None:
    """Write the resistor ink/pad geometry rules into library.tech_params
    so Setup > Save Technology File... / TECH SAVE captures them alongside
    the layer stack and the sizing design rules."""
    payload = {k: rules[k] for k in DEFAULT_RESISTOR_GEOMETRY if k in rules}
    library.tech_params[RESISTOR_GEOMETRY_KEY] = json.dumps(payload)


# --- Tools > Thin-Film Resistor Designer... settings -------------------------
#
# Unlike the thick-film ink-print resistor above (a different ink, and
# therefore a different sheet resistance, per resistor -- see
# "ink_layers" above), a thin-film design has ONE sheet resistivity
# for the whole design: it's a single uniformly-deposited film, not a
# choice of printed inks. So "sheet_rho" here is one number per
# design, set once in the wizard, not looked up per resistor.
#
# All lengths in DBU (1 DBU = 1 micron). The defaults below come from
# a common laser-trim design-guide convention (probe pad clearance,
# artwork overlap, minimum resistor length, and laser kerf width all
# specified there in mils): 0.003in -> 76 DBU, 0.002in -> 51 DBU,
# 0.0015in -> 38 DBU, 0.0005in -> 13 DBU.

THIN_FILM_KEY = "thin_film_rules"

DEFAULT_THIN_FILM = {
    "sheet_rho": 100.0,          # Ω/sq -- the one design-wide sheet resistivity
    "trace_width": 25,           # default serpentine trace / minimum feature width
    "kerf": 38,                  # laser kerf width (~0.0015in)
    "link_fraction": 0.15,       # fraction of squares reserved for coarse link-area trim
    "corner_factor": 0.5,        # meander corner's squares contribution (see core.thinfilm)
    "min_probe_pad_clearance": 76,   # ~0.003in
    "min_artwork_overlap": 51,       # ~0.002in
    "min_resistor_length": 51,       # ~0.002in
    "probe_pad_min": 13,             # ~0.0005in
    "resistor_layer": 40,
    "pad_layer": 41,
    "doc_layer": 33,             # shared with the thick-film doc layer by default
}


def load_thin_film_rules(library: Library) -> dict:
    """Read the thin-film design-wide sheet resistivity and serpentine/
    top-hat geometry rules from library.tech_params. Always returns a
    complete dict, falling back to DEFAULT_THIN_FILM key by key --
    same pattern as load_resistor_geometry_rules() above."""
    rules = dict(DEFAULT_THIN_FILM)
    raw = library.tech_params.get(THIN_FILM_KEY)
    if not raw:
        return rules
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return rules
    if isinstance(data, dict):
        rules.update({k: data[k] for k in DEFAULT_THIN_FILM if k in data})
    return rules


def save_thin_film_rules(library: Library, rules: dict) -> None:
    """Write the thin-film sheet resistivity and geometry rules into
    library.tech_params so Setup > Save Technology File... / TECH SAVE
    captures them alongside the layer stack."""
    payload = {k: rules[k] for k in DEFAULT_THIN_FILM if k in rules}
    library.tech_params[THIN_FILM_KEY] = json.dumps(payload)


# --- Create > Route (2-Layer) settings ---------------------------------------
#
# Setup > Route Layers... configures the two conductor layers the Route
# tool draws on, and which reusable via cell (Tools > Via Wizard.../
# scripting's create_via_cell()) Space places at the point where a route
# switches between them. The via's own footprint -- pad size/shape,
# dielectric window, anything else -- lives entirely in that cell's own
# geometry, not here: this dialog used to also ask for via size/shape and
# a separate dielectric layer/window, but that duplicated what the via
# cell itself already describes, so it's just the cell's name now. Same
# one-JSON-blob-per-tech_params-entry pattern as everything else in this
# module, so this is saved/reloaded automatically with the technology
# file/sidecar instead of needing to be re-entered every session.

ROUTE_RULES_KEY = "route_rules"

DEFAULT_ROUTE_RULES = {
    "layer_a": 0,
    "layer_b": 1,
    "via_cell": "",
    # Auto-Route keep-in boundary: the trace from AUTOROUTE / Edit >
    # Auto-Route Two Pins must stay inside the geometry on this layer.
    # -1 = no boundary (route anywhere).
    "boundary_layer": -1,
}


def load_route_rules(library: Library) -> dict:
    """Read the saved Route Layers settings from library.tech_params.
    Always returns a complete dict (falling back to DEFAULT_ROUTE_RULES
    key by key), same pattern as load_resistor_geometry_rules()."""
    rules = dict(DEFAULT_ROUTE_RULES)
    raw = library.tech_params.get(ROUTE_RULES_KEY)
    if not raw:
        return rules
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return rules
    if isinstance(data, dict):
        rules.update({k: data[k] for k in DEFAULT_ROUTE_RULES if k in data})
    return rules


def save_route_rules(library: Library, rules: dict) -> None:
    """Write the Route Layers settings into library.tech_params so
    Setup > Save Technology File... / TECH SAVE (and the per-design
    .hlaux sidecar written by File > Save GDS...) capture them
    alongside the layer stack, so a user who configured this once
    doesn't have to re-enter it in a later session."""
    payload = {k: rules[k] for k in DEFAULT_ROUTE_RULES if k in rules}
    library.tech_params[ROUTE_RULES_KEY] = json.dumps(payload)


# --- Per-instance ref-designator annotation layer -------------------------
#
# The canvas draws each placed instance's SRef.label (e.g. "U1") as a
# small floating ref-des annotation at the instance origin. On its own
# that annotation belongs to no layer, so it stays on screen even with
# every layer switched off. This setting ties it to ONE layer's
# visibility -- normally the same documentation layer a resistor's
# ref-des Text is drawn on -- so all ref-designators show/hide together.
# Stored as a bare layer number (same one-blob-per-tech_params-entry
# persistence as everything else here); -1, the default, means "not tied
# to a specific layer" -- the canvas then still shows the annotation,
# except when every layer is hidden ("All Off"), where it's dropped too
# so the canvas actually goes blank.

REFDES_ANNOTATION_LAYER_KEY = "refdes_annotation_layer"


def load_refdes_annotation_layer(library: Library) -> int:
    """Layer number the per-instance ref-des annotation is tied to, or
    -1 if it isn't tied to any layer (always shown). See
    REFDES_ANNOTATION_LAYER_KEY above."""
    raw = library.tech_params.get(REFDES_ANNOTATION_LAYER_KEY)
    if raw in (None, ""):
        return -1
    try:
        return int(json.loads(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return -1


def save_refdes_annotation_layer(library: Library, layer: int) -> None:
    """Write the ref-des annotation layer into library.tech_params so it
    round-trips with the technology file / sidecar. Pass -1 to clear it
    (annotation always shown)."""
    library.tech_params[REFDES_ANNOTATION_LAYER_KEY] = json.dumps(int(layer))


# --- Dielectric fill rules -----------------------------------------------
#
# Tools > Generate Dielectric Fill... (Session.generate_dielectric())
# turns whatever is drawn on "layer" at datatype 0 into a filled sheet
# with holes: the largest shape that encloses the rest is the fill
# boundary (or the design bbox + "margin" if none), and every other
# datatype-0 shape inside it -- a via window stamped by drop_via, a
# resistor keep-out, a hand-drawn exclusion rect -- is a hole, grown by
# "clearance". So vias/keep-outs need NO dedicated layer: draw them on
# the dielectric layer and they're auto-detected. "keepout_layers" is an
# optional extra for cross-layer exclusion zones. The fill is written at
# datatype 1. Same one-JSON-blob-per-tech_params-entry persistence as
# the other rule sets. layer = -1 means "not set yet".

DIELECTRIC_RULES_KEY = "dielectric_rules"

DEFAULT_DIELECTRIC_RULES = {
    "layer": -1,
    "margin": 1000,
    "clearance": 150,
    "keepout_layers": [],
}


def load_dielectric_rules(library: Library) -> dict:
    """Read the Dielectric Fill rules from library.tech_params. Always
    returns a complete dict (falling back to DEFAULT_DIELECTRIC_RULES
    key by key); keepout_layers is always a list of ints."""
    rules = dict(DEFAULT_DIELECTRIC_RULES)
    rules["keepout_layers"] = []
    raw = library.tech_params.get(DIELECTRIC_RULES_KEY)
    if not raw:
        return rules
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return rules
    if isinstance(data, dict):
        for k in ("layer", "margin", "clearance"):
            if k in data:
                try:
                    rules[k] = int(data[k])
                except (TypeError, ValueError):
                    pass
        kl = data.get("keepout_layers")
        if isinstance(kl, list):
            out = []
            for v in kl:
                try:
                    out.append(int(v))
                except (TypeError, ValueError):
                    pass
            rules["keepout_layers"] = out
    return rules


def save_dielectric_rules(library: Library, rules: dict) -> None:
    """Write the Dielectric Fill rules into library.tech_params so
    Setup > Save Technology File... / TECH SAVE and the per-design
    .hlaux sidecar capture them."""
    payload = {
        "layer": int(rules.get("layer", -1)),
        "margin": int(rules.get("margin", DEFAULT_DIELECTRIC_RULES["margin"])),
        "clearance": int(rules.get("clearance", DEFAULT_DIELECTRIC_RULES["clearance"])),
        "keepout_layers": [int(v) for v in rules.get("keepout_layers", [])],
    }
    library.tech_params[DIELECTRIC_RULES_KEY] = json.dumps(payload)


# --- Wire-bond rules ---------------------------------------------------------
#
# The BOND command / Session.wire_bond() draw a chip-and-wire assembly
# wire bond -- a Path on "layer" with a small ball/stitch footprint of
# side "pad" at each end -- to connect a chip-die pin to a conductor
# pad, a conductor pad to a package pin, or two conductor pads as a
# jumper. The bond layer is treated as a conductor by
# Session.extract_netlist()/NETLIST RUN (folded in automatically), so a
# net's connectivity is reported right through the bond to the die pin.
# "die_pad_layers" lists isolated bond-pad layers (bare-die pads, header
# pins): Extract Netlist keeps every shape on them isolated from the
# substrate copper underneath and connects it only where a bond lands.
# Same one-JSON-blob-per-tech_params-entry persistence as the rest.
# layer = -1 means "not configured -- BOND has nowhere to draw".

BOND_RULES_KEY = "bond_rules"

DEFAULT_BOND_RULES = {
    "layer": -1,
    "width": 50,        # DBU -- a ~50 um / 2 mil bond wire (thicker/heavier gauge)
    "pad": 100,         # DBU -- ball/stitch bond footprint diameter at each end
    "min_length": 300,  # DBU -- shortest span a bonder can reliably loop
    "max_length": 5000, # DBU -- longest unsupported span before sag/shorting
    "die_pad_layers": [],  # isolated bond-pad layers (bare-die pads, header
                           # pins): never merged with substrate copper by
                           # extract_netlist(), connected only by bonds
}

_BOND_RULE_KEYS = ("layer", "width", "pad", "min_length", "max_length")


def _layer_list(value) -> list:
    """Normalise a pad-layer setting -- a list, a single int, or a
    "58, 49" string -- to a sorted list of distinct non-negative ints."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [v for v in re.split(r"[,\s]+", value) if v]
    elif not isinstance(value, (list, tuple, set)):
        value = [value]
    out = set()
    for v in value:
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n >= 0:
            out.add(n)
    return sorted(out)


def load_bond_rules(library: Library) -> dict:
    """Read the wire-bond rules from library.tech_params. Always returns
    a complete dict (falling back to DEFAULT_BOND_RULES key by key)."""
    rules = dict(DEFAULT_BOND_RULES)
    raw = library.tech_params.get(BOND_RULES_KEY)
    if not raw:
        return rules
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return rules
    if isinstance(data, dict):
        for k in _BOND_RULE_KEYS:
            if k in data:
                try:
                    rules[k] = int(data[k])
                except (TypeError, ValueError):
                    pass
        if "die_pad_layers" in data:
            rules["die_pad_layers"] = _layer_list(data["die_pad_layers"])
        elif "die_pad_layer" in data:          # single-layer form, pre-list
            rules["die_pad_layers"] = _layer_list(data["die_pad_layer"])
    return rules


def save_bond_rules(library: Library, rules: dict) -> None:
    """Write the wire-bond rules into library.tech_params so
    Setup > Save Technology File... / TECH SAVE and the per-design
    .hlaux sidecar capture them."""
    out = {k: int(rules.get(k, DEFAULT_BOND_RULES[k])) for k in _BOND_RULE_KEYS}
    pads = rules.get("die_pad_layers")
    if pads is None and "die_pad_layer" in rules:
        pads = rules["die_pad_layer"]
    out["die_pad_layers"] = _layer_list(pads)
    library.tech_params[BOND_RULES_KEY] = json.dumps(out)


# --- Top View settings (Tools > Top View...) ---------------------------------
#
# Everything the Top View window asks for, saved with the design so the
# next run (window, or TOPVIEW with no arguments) starts from the same
# choices. "excluded" lists the layers unticked in the window -- stored
# as exclusions, so a layer drawn later is included by default.

TOPVIEW_SETTINGS_KEY = "topview_settings"

DEFAULT_TOPVIEW_SETTINGS = {
    "result_layer": -1,
    "out_cell": "TOPVIEW",
    "excluded": [],
    "line_width_um": 100.0,
    "hatch": False,
    "hatch_pitch_um": 250.0,
    "hatch_angles": [45.0, -45.0],
    "flatten": True,
    "simplify_um": 0.0,
    "used_only": True,
    "refdes": True,
    "refdes_height_um": 600.0,
}


def load_topview_settings(library: Library) -> dict:
    """The saved Top View settings, complete (defaults filled in);
    ``saved`` is True only when the design has its own entry."""
    out = json.loads(json.dumps(DEFAULT_TOPVIEW_SETTINGS))
    out["saved"] = False
    raw = library.tech_params.get(TOPVIEW_SETTINGS_KEY)
    if not raw:
        return out
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return out
    if not isinstance(data, dict):
        return out
    out["saved"] = True
    try:
        out["result_layer"] = int(data.get("result_layer", -1))
    except (TypeError, ValueError):
        pass
    if isinstance(data.get("out_cell"), str) and data["out_cell"].strip():
        out["out_cell"] = data["out_cell"].strip()
    if "excluded" in data:
        out["excluded"] = _layer_list(data["excluded"])
    for k in ("line_width_um", "hatch_pitch_um", "simplify_um", "refdes_height_um"):
        try:
            out[k] = float(data.get(k, out[k]))
        except (TypeError, ValueError):
            pass
    for k in ("hatch", "flatten", "used_only", "refdes"):
        if k in data:
            out[k] = bool(data[k])
    try:
        ang = [float(a) for a in data.get("hatch_angles", out["hatch_angles"])]
        if ang:
            out["hatch_angles"] = ang
    except (TypeError, ValueError):
        pass
    return out


def save_topview_settings(library: Library, settings: dict) -> None:
    cur = load_topview_settings(library)
    cur.update({k: v for k, v in (settings or {}).items() if k in DEFAULT_TOPVIEW_SETTINGS})
    payload = {k: cur[k] for k in DEFAULT_TOPVIEW_SETTINGS}
    payload["excluded"] = _layer_list(payload["excluded"])
    library.tech_params[TOPVIEW_SETTINGS_KEY] = json.dumps(payload)


# --- Netlist extraction settings (Tools > Extract Netlist...) ----------------
#
# Which layers Extract Netlist treats as substrate conductor, whether
# wire bonds are included, and whether the dialog lists only layers that
# carry geometry. Persisted per design (sidecar/technology file) so the
# selection is there next time without re-picking it. The isolated pad
# layers (die pads, header pins) are NOT stored here: they belong to the
# wire-bond rules above (die_pad_layers), which the extractor reads.

NETLIST_SETTINGS_KEY = "netlist_settings"

DEFAULT_NETLIST_SETTINGS = {
    "conductor_layers": [],
    "include_bonds": True,
    "used_only": True,
}


def load_netlist_settings(library: Library) -> dict:
    """Read the saved Extract Netlist settings. Always returns a complete
    dict; ``saved`` is True only if the design has its own saved entry
    (so a caller can pick sensible defaults the first time)."""
    out = {k: (list(v) if isinstance(v, list) else v)
           for k, v in DEFAULT_NETLIST_SETTINGS.items()}
    out["saved"] = False
    raw = library.tech_params.get(NETLIST_SETTINGS_KEY)
    if not raw:
        return out
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return out
    if isinstance(data, dict):
        out["saved"] = True
        if "conductor_layers" in data:
            out["conductor_layers"] = _layer_list(data["conductor_layers"])
        for k in ("include_bonds", "used_only"):
            if k in data:
                out[k] = bool(data[k])
    return out


def save_netlist_settings(library: Library, settings: dict) -> None:
    """Write the Extract Netlist settings into library.tech_params."""
    library.tech_params[NETLIST_SETTINGS_KEY] = json.dumps({
        "conductor_layers": _layer_list(settings.get("conductor_layers", [])),
        "include_bonds": bool(settings.get("include_bonds", True)),
        "used_only": bool(settings.get("used_only", True)),
    })


# --- Design Rule Check (DRC) rules ------------------------------------------
#
# Unlike the fixed-shape dicts above, a set of DRC rules is a variable-
# length LIST -- a process might have three rules or thirty -- so it's
# stored as one JSON-encoded list of {"kind", "layer_a", "layer_b",
# "value", "name", "color"} dicts under a single tech_params key, same overall
# pattern (one JSON blob per tech_params entry) as everything else in
# this module. See hlayout.core.drc for what each rule kind means and
# how a rule is actually checked; this module only knows how to
# serialize/deserialize the rule list, not how to run it.

DRC_RULES_KEY = "drc_rules"


def load_drc_rules(library: Library) -> list:
    """Read the saved list of DRC rule dicts from library.tech_params.
    Always returns a list (empty if none saved, or if the stored data
    is malformed) -- never raises, so callers don't need to guard
    against a technology file saved before this feature existed."""
    raw = library.tech_params.get(DRC_RULES_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    rules = []
    for item in data:
        if not isinstance(item, dict) or "kind" not in item or "layer_a" not in item or "value" not in item:
            continue
        rules.append({
            "kind": item["kind"],
            "layer_a": item["layer_a"],
            "layer_b": item.get("layer_b"),
            "value": item["value"],
            "name": item.get("name", ""),
            "color": item.get("color", ""),
        })
    return rules


def save_drc_rules(library: Library, rules) -> None:
    """Write a list of DRC rule dicts (or hlayout.core.drc.DrcRule
    instances -- either is accepted) into library.tech_params so Setup
    > Save Technology File... / TECH SAVE captures them alongside the
    layer stack."""
    payload = []
    for r in rules:
        d = r.to_dict() if hasattr(r, "to_dict") else r
        payload.append({
            "kind": d["kind"],
            "layer_a": d["layer_a"],
            "layer_b": d.get("layer_b"),
            "value": d["value"],
            "name": d.get("name", ""),
            "color": d.get("color", ""),
        })
    library.tech_params[DRC_RULES_KEY] = json.dumps(payload)


# --- mask op batch (Tools > Mask Operation Batch...) ----------------------
# A saved, ORDERED list of MaskOpStep dicts -- order matters here (unlike
# DRC rules, which are independent of each other): a later step's
# layer_a/layer_b can be an earlier step's own result_layer, so running
# the batch replays the steps as a multi-step derived-layer recipe, not
# just a bag of independent operations. Same tech_params-blob persistence
# pattern as everything else in this module. See hlayout.core.maskops for
# what each step field means and hlayout.scripting.api.Session.
# run_mask_op_batch() for how the list is actually run.

MASK_OP_STEPS_KEY = "mask_op_steps"


def load_mask_op_steps(library: Library) -> list:
    """Read the saved, ordered list of mask-op step dicts from
    library.tech_params. Always returns a list (empty if none saved, or
    if the stored data is malformed) -- never raises, so callers don't
    need to guard against a technology file saved before this feature
    existed."""
    raw = library.tech_params.get(MASK_OP_STEPS_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    steps = []
    for item in data:
        if (not isinstance(item, dict) or "kind" not in item
                or "layer_a" not in item or "result_layer" not in item):
            continue
        steps.append({
            "kind": item["kind"],
            "layer_a": item["layer_a"],
            "result_layer": item["result_layer"],
            "layer_b": item.get("layer_b"),
            "amount": item.get("amount", 0.0),
            "name": item.get("name", ""),
            "flatten": item.get("flatten", True),
            "simplify": item.get("simplify", 0.0),
        })
    return steps


def save_mask_op_steps(library: Library, steps) -> None:
    """Write an ordered list of mask-op step dicts (or
    hlayout.core.maskops.MaskOpStep instances -- either is accepted)
    into library.tech_params so Setup > Save Technology File... / TECH
    SAVE captures them alongside the layer stack. Order is preserved --
    it's what makes a batch a *recipe* rather than an unordered set."""
    payload = []
    for s in steps:
        d = s.to_dict() if hasattr(s, "to_dict") else s
        payload.append({
            "kind": d["kind"],
            "layer_a": d["layer_a"],
            "result_layer": d["result_layer"],
            "layer_b": d.get("layer_b"),
            "amount": d.get("amount", 0.0),
            "name": d.get("name", ""),
            "flatten": d.get("flatten", True),
            "simplify": d.get("simplify", 0.0),
        })
    library.tech_params[MASK_OP_STEPS_KEY] = json.dumps(payload)


# --- Gerber export sets (Tools > Gerber Export Sets...) -------------------
# A saved list of GerberExportJob dicts -- unlike mask op steps, order
# doesn't matter here (each job's output is independent -- Gerber export
# never modifies the design, only writes files), so this is a plain list,
# not a recipe. Same tech_params-blob persistence pattern as everything
# else in this module. See hlayout.io.gerberio for what each job field
# means and hlayout.scripting.api.Session.run_gerber_export_batch() for
# how the list is actually run.

GERBER_JOBS_KEY = "gerber_export_jobs"


def load_gerber_export_jobs(library: Library) -> list:
    """Read the saved list of Gerber export job dicts from
    library.tech_params. Always returns a list (empty if none saved, or
    if the stored data is malformed) -- never raises, so callers don't
    need to guard against a technology file saved before this feature
    existed."""
    raw = library.tech_params.get(GERBER_JOBS_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    jobs = []
    for item in data:
        if not isinstance(item, dict) or "out_prefix" not in item:
            continue
        jobs.append({
            "name": item.get("name", ""),
            "out_prefix": item["out_prefix"],
            "layers": item.get("layers"),
        })
    return jobs


def save_gerber_export_jobs(library: Library, jobs) -> None:
    """Write a list of Gerber export job dicts (or
    hlayout.io.gerberio.GerberExportJob instances -- either is
    accepted) into library.tech_params so Setup > Save Technology
    File... / TECH SAVE captures them alongside the layer stack."""
    payload = []
    for j in jobs:
        d = j.to_dict() if hasattr(j, "to_dict") else j
        payload.append({
            "name": d.get("name", ""),
            "out_prefix": d["out_prefix"],
            "layers": d.get("layers"),
        })
    library.tech_params[GERBER_JOBS_KEY] = json.dumps(payload)


GERBER_OUTPUT_DIR_KEY = "gerber_output_dir"


def load_gerber_output_dir(library: Library) -> str:
    """The Gerber Export Sets output folder saved with this design, or
    "" when none was chosen (meaning: use the default, <current dir>/Gerber
    -- see hlayout.io.gerberio.default_gerber_output_dir())."""
    value = library.tech_params.get(GERBER_OUTPUT_DIR_KEY)
    return value.strip() if isinstance(value, str) else ""


def save_gerber_output_dir(library: Library, path) -> None:
    """Remember (or, with an empty path, forget) the Gerber Export Sets
    output folder in library.tech_params."""
    path = (path or "").strip()
    if path:
        library.tech_params[GERBER_OUTPUT_DIR_KEY] = path
    else:
        library.tech_params.pop(GERBER_OUTPUT_DIR_KEY, None)


GERBER_SCALE_KEY = "gerber_output_scale"

# Hybrid screen artwork is plotted enlarged, so Gerber Export Sets starts
# at 5X for a design that has no scale saved yet. (The scripting calls --
# Session.export_gerber() etc. -- still default to 1, actual size.)
DEFAULT_GERBER_SCALE = 5.0


def load_gerber_scale(library: Library) -> float:
    """The Gerber Export Sets artwork scale saved with this design
    (1 = actual size, 5 / 10 = the usual hybrid artwork scales).
    DEFAULT_GERBER_SCALE (5X) when none is saved or the stored value is
    unusable."""
    try:
        value = float(library.tech_params.get(GERBER_SCALE_KEY))
    except (TypeError, ValueError):
        return DEFAULT_GERBER_SCALE
    ok = value > 0 and value == value and value != float("inf")
    return value if ok else DEFAULT_GERBER_SCALE


def save_gerber_scale(library: Library, scale) -> None:
    """Remember the Gerber Export Sets artwork scale in
    library.tech_params. Always stored, 1X included, so an explicit
    choice of actual size isn't mistaken for "nothing chosen yet"."""
    library.tech_params[GERBER_SCALE_KEY] = repr(float(scale))


# --- DXF export sets (Tools > DXF Export Sets...) --------------------------
# A saved list of DxfExportJob dicts -- same shape/rationale as Gerber
# export jobs above: order doesn't matter (a DXF export never modifies
# the design, only writes a file), so this is a plain list, not a
# recipe. See hlayout.io.dxfio for what each job field means and
# hlayout.scripting.api.Session.run_dxf_export_batch() for how the list
# is actually run.

DXF_JOBS_KEY = "dxf_export_jobs"


def load_dxf_export_jobs(library: Library) -> list:
    """Read the saved list of DXF export job dicts from
    library.tech_params. Always returns a list (empty if none saved, or
    if the stored data is malformed) -- never raises, so callers don't
    need to guard against a technology file saved before this feature
    existed."""
    raw = library.tech_params.get(DXF_JOBS_KEY)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    jobs = []
    for item in data:
        if not isinstance(item, dict) or "path" not in item:
            continue
        jobs.append({
            "name": item.get("name", ""),
            "path": item["path"],
            "layers": item.get("layers"),
        })
    return jobs


def save_dxf_export_jobs(library: Library, jobs) -> None:
    """Write a list of DXF export job dicts (or
    hlayout.io.dxfio.DxfExportJob instances -- either is accepted) into
    library.tech_params so Setup > Save Technology File... / TECH SAVE
    captures them alongside the layer stack."""
    payload = []
    for j in jobs:
        d = j.to_dict() if hasattr(j, "to_dict") else j
        payload.append({
            "name": d.get("name", ""),
            "path": d["path"],
            "layers": d.get("layers"),
        })
    library.tech_params[DXF_JOBS_KEY] = json.dumps(payload)


# --- 3D / FreeCAD export: per-layer fired-film thickness -------------------
#
# The layer table's number order IS the print/fire (z) order; the only
# extra datum needed for a 3D view is how thick each printed film is.
# A layer with no entry (or 0) is not a physical film -- documentation,
# ref-des annotation, keep-out markers, route boundary -- and is skipped
# entirely by the FreeCAD export (contributes nothing to geometry or to
# the running elevation). See hlayout.io.freecadio.

LAYER_THICKNESS_KEY = "layer_thickness"

#: Fired-film thickness defaults (micron) by inferred layer role.
_DEFAULT_FILM_UM = {
    "substrate": 635,     # 25-mil alumina
    "dielectric": 30,
    "conductor": 12,
    "resistor": 15,
    "glaze": 20,
}


#: Fall-back film thickness (micron) for a drawn-on, filled, visible
#: layer that matches no recognised role.
_GENERIC_FILM_UM = 10


def default_layer_thickness(library: Library) -> dict:
    """Best-effort {str(layer): thickness_um}: the recognised role
    layers (resistor / route / bond / dielectric / glaze) get a
    role-specific fired thickness, every other layer that actually
    carries filled, visible geometry gets a generic film thickness, and
    the lowest such layer is bumped to a ceramic-substrate thickness.
    Layers with nothing drawn on them (or drawn outline-only / hidden)
    get no entry -> non-physical, skipped by the 3D export."""
    out: dict = {}
    try:
        rg = load_resistor_geometry_rules(library)
        for n in (rg.get("resistor_layer"),):
            if isinstance(n, int) and n >= 0:
                out[str(n)] = _DEFAULT_FILM_UM["resistor"]
        for n in (rg.get("ink_layers") or {}).values():
            if isinstance(n, int) and n >= 0:
                out[str(n)] = _DEFAULT_FILM_UM["resistor"]
        for n in (rg.get("conductor_layer"),):
            if isinstance(n, int) and n >= 0:
                out[str(n)] = _DEFAULT_FILM_UM["conductor"]
        for n in (rg.get("glaze_layer"),):
            if isinstance(n, int) and n >= 0:
                out[str(n)] = _DEFAULT_FILM_UM["glaze"]
    except Exception:
        pass
    try:
        rr = load_route_rules(library)
        for n in (rr.get("layer_a"), rr.get("layer_b")):
            if isinstance(n, int) and n >= 0:
                out.setdefault(str(n), _DEFAULT_FILM_UM["conductor"])
    except Exception:
        pass
    try:
        b = int(load_bond_rules(library)["layer"])
        if b >= 0:
            out.setdefault(str(b), _DEFAULT_FILM_UM["conductor"])
    except Exception:
        pass
    try:
        d = int(load_dielectric_rules(library)["layer"])
        if d >= 0:
            out.setdefault(str(d), _DEFAULT_FILM_UM["dielectric"])
    except Exception:
        pass

    # every other layer that actually carries filled, visible geometry
    drawn = set()
    for cell in library.cells.values():
        for sh in cell.shapes:
            drawn.add(sh.layer)
    filmable = sorted(
        n for n in drawn
        if 0 <= n < len(library.layers)
        and library.layers[n].visible and library.layers[n].fill
    )
    for n in filmable:
        out.setdefault(str(n), _GENERIC_FILM_UM)
        nm = library.layers[n].name.lower()
        if any(w in nm for w in ("substrate", "alumina", "ceramic")):
            out[str(n)] = max(out.get(str(n), 0), _DEFAULT_FILM_UM["substrate"])
    return out


def load_layer_thickness(library: Library) -> dict:
    """{str(layer): thickness_um} from library.tech_params, or {} if none
    saved (the caller falls back to default_layer_thickness())."""
    raw = library.tech_params.get(LAYER_THICKNESS_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                t = float(v)
            except (TypeError, ValueError):
                continue
            if t > 0:
                out[str(int(k))] = t
    return out


def save_layer_thickness(library: Library, thickness: dict) -> None:
    payload = {}
    for k, v in (thickness or {}).items():
        try:
            t = float(v)
        except (TypeError, ValueError):
            continue
        if t > 0:
            payload[str(int(k))] = t
    library.tech_params[LAYER_THICKNESS_KEY] = json.dumps(payload)


# --- 3D / FreeCAD export: per-component-cell 3D representation -------------
#
# Each placed cell instance is shown in the 3D export as either a flat
# extrusion of a fixed body height ("extrude") or by substituting an
# external STEP/IGES model FreeCAD imports and positions ("model").
# {cell_name: {"mode","height_um","z_offset_um","model_path","rotation_deg",
#  "z_ref"}} -- z_ref: see COMPONENT_Z_REFS below.
# rotation_deg (degrees, default 0.0) is an extra correction applied ON
# TOP of the placement's own rotation in "model" mode -- see
# hlayout.io.freecadio's model-substitution block and
# hlayout.io.component_match's _gullwing_rotation_correction() (the
# footprint matcher is deliberately orientation-independent when
# DETECTING a package, so an auto-matched substitution can come out 90
# degrees off from the real footprint's lead direction; this field is
# how that gets corrected, whether set automatically or by hand via
# the Component Library Manager).

COMPONENT_MODELS_KEY = "component_models"

#: Fixed extruded height for a "bare chip" component -- one with no
#: matched or manually-assigned STEP model, shown in the 3D export as a
#: flat box of its own 2D footprint instead. 1mm is a generic stand-in
#: for an unpackaged/unknown part's real height, not a measurement of
#: any specific device.
DEFAULT_COMPONENT_HEIGHT_UM = 1000


def load_component_models(library: Library) -> dict:
    raw = library.tech_params.get(COMPONENT_MODELS_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    out = {}
    if isinstance(data, dict):
        for name, d in data.items():
            if not isinstance(d, dict):
                continue
            mode = d.get("mode", "extrude")
            if mode not in ("extrude", "model"):
                mode = "extrude"
            entry = {"mode": mode,
                     "height_um": float(d.get("height_um", DEFAULT_COMPONENT_HEIGHT_UM)),
                     "z_offset_um": float(d.get("z_offset_um", 0.0)),
                     "model_path": str(d.get("model_path", "") or ""),
                     "rotation_deg": float(d.get("rotation_deg", 0.0)),
                     "z_ref": _z_ref(d.get("z_ref"))}
            out[str(name)] = entry
    return out


def save_component_models(library: Library, models: dict) -> None:
    payload = {}
    for name, d in (models or {}).items():
        if not isinstance(d, dict):
            continue
        mode = d.get("mode", "extrude")
        payload[str(name)] = {
            "mode": mode if mode in ("extrude", "model") else "extrude",
            "height_um": float(d.get("height_um", DEFAULT_COMPONENT_HEIGHT_UM)),
            "z_offset_um": float(d.get("z_offset_um", 0.0)),
            "model_path": str(d.get("model_path", "") or ""),
            "rotation_deg": float(d.get("rotation_deg", 0.0)),
            "z_ref": _z_ref(d.get("z_ref")),
        }
    library.tech_params[COMPONENT_MODELS_KEY] = json.dumps(payload)


#: Where a component's z=0 goes in the 3D export: "surface" -- the top
#: of whatever printed film lies under its footprint (a chip on its pads,
#: a die on its attach area); "substrate_bottom" -- the underside of the
#: substrate (a package the substrate sits IN, e.g. a header/bathtub
#: package whose model has the well floor at z=0).
COMPONENT_Z_REFS = ("surface", "substrate_bottom")


def _z_ref(value) -> str:
    return value if value in COMPONENT_Z_REFS else "surface"


def default_component_models(library: Library) -> dict:
    """No per-cell overrides by default -- every placed component is
    shown as a fixed-height extrusion (DEFAULT_COMPONENT_HEIGHT_UM).
    Placed here as the hook a future Setup dialog / role heuristic can
    fill in."""
    return {}


# --- Thermal analysis (Tools > Thermal Analysis...) ------------------------
#
# Everything hlayout.core.thermal_build needs beyond the layout itself,
# stored as ONE JSON blob with five sections so a design's thermal setup
# travels with its technology file / .hlaux sidecar like every other
# rule set here:
#
#   materials  {str(layer): material name or conductivity in W/m.K} --
#              overrides the role default (conductor / dielectric /
#              resistor / glaze) for that layer's film. Naming a layer
#              here also brings a layer with no recognised role into the
#              thermal stack.
#   powers     {ref_des: milliwatts} -- a resistor left out takes its
#              Resistor Designer power; anything else left out is 0.
#   parts      {cell name or ref_des: {"kind", "body_material",
#              "body_um", "attach_material", "attach_um", "size_um"}} --
#              how a die or component is built up above the films.
#   boundary   substrate, mounting and the base/top boundary conditions.
#   limits     pass/fail thresholds reported per part.
#
# Material names are hlayout.core.thermal.MATERIALS keys; this module
# only stores them and never imports numpy/scipy.

THERMAL_KEY = "thermal"

DEFAULT_THERMAL = {
    "materials": {},
    "powers": {},
    "parts": {},
    "boundary": {
        "substrate_layer": -1,          # -1 = find it by name, else the films' bounding box
        "substrate_material": "alumina_96",
        "substrate_um": 635.0,
        "base_mode": "fixed",           # "fixed" temperature, or "htc" to ambient
        "base_temp_c": 25.0,
        "ambient_c": 25.0,
        "base_htc": 0.0,                # W/m^2.K, base_mode "htc" only
        "top_htc": 10.0,                # W/m^2.K; 0 = sealed, no loss from the top
        "mount_material": "",           # "" = substrate sits straight on the base
        "mount_um": 0.0,
        "grid_um": 0.0,                 # 0 = choose automatically
    },
    "limits": {
        "resistor_max_c": 150.0,
        "die_max_c": 125.0,
        "component_max_c": 125.0,
        "resistor_max_w_mm2": 0.08,     # about 50 W/in^2, a common thick-film figure
    },
    # how the temperature maps are drawn (window and report) -- see
    # hlayout.core.thermal.MapView
    "display": {
        "style": "banded",              # "banded" solid colour steps, or "smooth"
        "bands": 10,
        "quantity": "rise",             # "rise" above base/ambient, or "temperature"
        "spacing": "cool",              # "cool" = bands closer at the cool end, or "even"
    },
}

#: Default build-up of a bare die / a chip component above the films.
DEFAULT_THERMAL_PART = {
    "die": {"body_material": "silicon", "body_um": 380.0,
            "attach_material": "epoxy_ag", "attach_um": 40.0},
    "component": {"body_material": "ceramic_chip", "body_um": 600.0,
                  "attach_material": "solder", "attach_um": 50.0},
}


def load_thermal_rules(library: Library) -> dict:
    """The thermal setup from library.tech_params: always a complete
    dict with every section, DEFAULT_THERMAL filled in key by key."""
    rules = json.loads(json.dumps(DEFAULT_THERMAL))
    raw = library.tech_params.get(THERMAL_KEY)
    if not raw:
        return rules
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return rules
    if not isinstance(data, dict):
        return rules
    for section in ("materials", "powers", "parts"):
        if isinstance(data.get(section), dict):
            rules[section] = {str(k): v for k, v in data[section].items()}
    for section in ("boundary", "limits", "display"):
        if isinstance(data.get(section), dict):
            for k, default in DEFAULT_THERMAL[section].items():
                if k not in data[section]:
                    continue
                v = data[section][k]
                try:
                    if isinstance(default, str):
                        rules[section][k] = str(v)
                    elif isinstance(default, int):
                        rules[section][k] = int(v)
                    else:
                        rules[section][k] = float(v)
                except (TypeError, ValueError):
                    pass
    return rules


def save_thermal_rules(library: Library, rules: dict) -> None:
    """Write the thermal setup into library.tech_params. Sections left
    out of ``rules`` keep whatever was saved before."""
    merged = load_thermal_rules(library)
    for section in ("materials", "powers", "parts"):
        if isinstance(rules.get(section), dict):
            merged[section] = {str(k): v for k, v in rules[section].items()}
    for section in ("boundary", "limits", "display"):
        if isinstance(rules.get(section), dict):
            for k in DEFAULT_THERMAL[section]:
                if k in rules[section]:
                    merged[section][k] = rules[section][k]
    library.tech_params[THERMAL_KEY] = json.dumps(merged)
    # round-trip through the loader so a bad value typed into a form
    # falls back to its default instead of being stored as-is
    library.tech_params[THERMAL_KEY] = json.dumps(load_thermal_rules(library))
