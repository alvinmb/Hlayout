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
# is the documentation/silkscreen-style layer the resistor's reference
# designator (e.g. "R1") is labelled on, centred on the ink body -- see
# Session.generate_resistor()'s ref_des parameter.

RESISTOR_GEOMETRY_KEY = "resistor_geometry_rules"

#: All lengths in DBU (1 DBU = 1 micron, matching body_width/body_length
#: from the Resistor Designer's sizing table).
DEFAULT_RESISTOR_GEOMETRY = {
    "overlap_a": 200,
    "pad_b": 500,
    "space_c": 200,
    "glaze_oversize": 50,
    "resistor_layer": 30,
    "conductor_layer": 31,
    "glaze_layer": 32,
    "doc_layer": 33,
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


# --- Create > Route (2-Layer) settings ---------------------------------------
#
# Setup > Route Layers... configures the two conductor layers the Route
# tool draws on, the via it drops (Space) when switching between them,
# and its dielectric via window: a same-centred opening cut in the
# dielectric layer that sits between the two conductor layers,
# representing the actual electrical contact area. A real via always
# has this opening -- there is no "no window" case in a real process --
# and it's normally sized AT OR ABOVE the via pad size (the pad lands
# within/over the opening), which is why it's a separate size rather
# than reusing via_size. dielectric_layer/dielectric_window therefore
# ship with real defaults rather than None/off. Same one-JSON-blob-
# per-tech_params-entry pattern as everything else in this module, so
# these settings are saved/reloaded automatically with the technology
# file/sidecar instead of needing to be re-entered every session.

ROUTE_RULES_KEY = "route_rules"

DEFAULT_ROUTE_RULES = {
    "layer_a": 0,
    "layer_b": 1,
    "via_size": 100,
    "via_shape": "square",
    "dielectric_layer": 2,
    "dielectric_window": 140,
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
