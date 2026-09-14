"""FreeCAD 3D component library builder.

Writes a ``.FCMacro`` -- a plain Python script -- that, run inside
FreeCAD (Macro > Macros... > Execute, or ``freecadcmd file.FCMacro``),
builds a small set of generic 3D component bodies and exports each as
its own STEP file: a few standard EIA ceramic chip-capacitor case
sizes, a 20-pin sealed metal/ceramic hybrid flatpack, and the common
SOT (small-outline-transistor) gull-wing package family -- both the
3-lead transistor/diode outlines (SOT-23/323/89/223) and the narrow-
body SOIC-style ICs (SOT-14/16/18).

The point is to hand File > Export to FreeCAD (3D Macro).../
Session.export_freecad()'s per-cell ``component_models`` substitution
(Setup > Technology Properties...) something to substitute *with*: set
a placed cell's mode to "model" and its model_path to one of these
STEP files, and that instance renders as this real 3D body instead of
a flat extruded footprint -- see hlayout.io.freecadio's own module
docstring for that side of the pipeline.

Like hlayout.io.freecadio, this module only emits text -- FreeCAD does
all the solid modelling -- so importing it adds no dependency beyond
the standard library; FreeCAD itself is needed only to *run* the
macro it writes, exactly like the design-export macro.

Geometry conventions (matching what freecadio.py's "model" substitution
expects -- see its ``_sh.read(model_path)`` / Placement code): each
part is centred at the origin in X/Y and sits on Z=0 (the surface it's
placed on) -- a capacitor's body rises from Z=0 to its height, and the
20-pin package's leads have their feet AT Z=0 so it sits flat. Every
part is exported as a Part compound of simple boxes (not a single
boolean-fused solid) -- fusing dozens of coincident-face boxes is a
common source of flaky FreeCAD boolean failures, and a coarse
placeholder body for 3D visualization/interference-checking has no
need to be watertight-fused; ``Part.Shape().read()`` reads a compound
back exactly as readily as a solid.

Verified against FreeCAD 1.1 (freecadcmd, headless): every part below
exports and reads back with the expected bounding box and solid count.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..core.smdlib import CHIP_SIZES_MM, TERMINAL_LENGTH_FRACTION, TERMINAL_LENGTH_MAX_MM

#: (name, length_mm, width_mm, height_mm, end_termination_length_mm) --
#: EIA imperial case-size codes, roughly true to their nominal size
#: (real vendor dimensions vary a little around these). 0603/0805/1206
#: are hand-picked (their exact numbers predate this table and are
#: covered by existing tests/shipped models); every OTHER EIA size
#: hlayout.core.smdlib's 2D footprint library already knows about
#: (0201/0402/1210/1812/2010/2512) is derived from its own
#: CHIP_SIZES_MM -- so the 2D footprint library and this 3D model
#: library can never drift out of sync on which sizes exist or what
#: their body envelope is, and Session.match_freecad_components() can
#: recognize a cell named after any of them (see
#: hlayout.io.component_match.eia_size_tokens()). Height = width and
#: termination length = smdlib's own TERMINAL_LENGTH_FRACTION/_MAX_MM
#: formula, matching the proportions the 3 hand-picked sizes already
#: happen to follow (H equals W for all three; term length is a
#: quarter of body length, capped at 0.6 mm).
DEFAULT_CERAMIC_CAPS: Tuple[Tuple[str, float, float, float, float], ...] = (
    ("CAP_0603", 1.6, 0.8, 0.8, 0.3),
    ("CAP_0805", 2.0, 1.25, 1.25, 0.4),
    ("CAP_1206", 3.2, 1.6, 1.6, 0.5),
) + tuple(
    (f"CAP_{size}", length_mm, width_mm, width_mm,
     round(min(length_mm * TERMINAL_LENGTH_FRACTION, TERMINAL_LENGTH_MAX_MM), 4))
    for size, (length_mm, width_mm) in CHIP_SIZES_MM.items()
    if size not in {"0603", "0805", "1206"}
)

#: A 20-pin sealed metal/ceramic hybrid flatpack -- 10 gull-wing leads
#: per long side at 50 mil (1.27 mm) pitch, a common thick-film hybrid
#: package family. Body/lead dimensions are reasonable defaults, not a
#: specific manufacturer's exact outline -- adjust the generated
#: macro's PKG20_* variables (or pass overrides here) to match a real
#: datasheet if one part number matters.
DEFAULT_PKG20 = dict(
    length_mm=25.0, width_mm=12.0, body_height_mm=2.5, lid_height_mm=1.0,
    lid_margin_mm=1.0, leads_per_side=10, pitch_mm=1.27, lead_width_mm=0.4,
    lead_thickness_mm=0.15, lead_out_mm=1.5, lead_foot_mm=1.2,
)

def _soic_leads(n_per_side, pitch, lead_w=0.4):
    """[(x_mm, lead_w), ...] for n_per_side leads centred on x=0 at
    ``pitch`` spacing -- the same symmetric layout both long sides of
    a narrow-body SOIC-style package share, used to build the SOT-14/
    16/18 entries below without repeating the arithmetic per part."""
    span = (n_per_side - 1) * pitch
    x0 = -span / 2.0
    return [(round(x0 + i * pitch, 4), lead_w) for i in range(n_per_side)]


#: The common SOT (small-outline-transistor) gull-wing package family,
#: each entry a kwargs dict for the generated macro's make_sot() (see
#: _GEOMETRY_FUNCTIONS below). Real vendor dimensions vary a little
#: around these -- adjust or override per part as needed.
#:
#: - SOT-23 / SOT-323 (SC-70): the classic 3-lead transistor/diode
#:   outline -- 2 leads on one side, 1 on the other -- SOT-323 is just
#:   a smaller version of the same shape.
#: - SOT-89: 3 leads on one side plus an exposed metal heatsink tab
#:   flush with the body's top on the other side (not a gull-wing lead
#:   -- physically part of the body).
#: - SOT-223: 3 leads on one side plus one wide gull-wing tab lead
#:   (for heat sinking through the PCB pad) on the other.
#: - SOT-14/16/18: narrow-body SOIC-style ICs -- 7/8/9 leads per long
#:   side at the standard 1.27 mm (50 mil) pitch, symmetric both sides
#:   (unlike the 3-lead transistor outlines above). 14/16-pin body
#:   sizes match the real narrow-SOIC (JEDEC MS-012) footprint; 18-pin
#:   narrow-body isn't a real standardized size, so its body length is
#:   extrapolated from the same lead-span-plus-margin the 14/16-pin
#:   sizes follow.
DEFAULT_SOT_PACKAGES = {
    "SOT_23": dict(L=2.9, W=1.3, H=1.1, lead_t=0.1, lead_out=0.4, lead_foot=0.4,
                   leads_neg_y=[(-0.475, 0.4), (0.475, 0.4)],
                   leads_pos_y=[(0.0, 0.4)]),
    "SOT_323": dict(L=2.0, W=1.25, H=0.95, lead_t=0.1, lead_out=0.3, lead_foot=0.3,
                    leads_neg_y=[(-0.325, 0.25), (0.325, 0.25)],
                    leads_pos_y=[(0.0, 0.25)]),
    "SOT_89": dict(L=4.5, W=2.5, H=1.5, lead_t=0.2, lead_out=0.6, lead_foot=0.8,
                   leads_neg_y=[(-1.5, 0.6), (0.0, 0.6), (1.5, 0.6)],
                   leads_pos_y=[],
                   heat_tab=(1, 2.0, 0.3, 1.0)),
    "SOT_223": dict(L=6.5, W=3.5, H=1.6, lead_t=0.25, lead_out=0.8, lead_foot=1.0,
                    leads_neg_y=[(-2.3, 0.5), (0.0, 0.5), (2.3, 0.5)],
                    leads_pos_y=[(0.0, 3.0)]),
    "SOT_14": dict(L=8.65, W=3.90, H=1.75, lead_t=0.2, lead_out=0.4, lead_foot=0.6,
                   leads_neg_y=_soic_leads(7, 1.27), leads_pos_y=_soic_leads(7, 1.27)),
    "SOT_16": dict(L=9.90, W=3.90, H=1.75, lead_t=0.2, lead_out=0.4, lead_foot=0.6,
                   leads_neg_y=_soic_leads(8, 1.27), leads_pos_y=_soic_leads(8, 1.27)),
    "SOT_18": dict(L=11.16, W=3.90, H=1.75, lead_t=0.2, lead_out=0.4, lead_foot=0.6,
                   leads_neg_y=_soic_leads(9, 1.27), leads_pos_y=_soic_leads(9, 1.27)),
}

_GEOMETRY_FUNCTIONS = '''\
def make_ceramic_cap(L, W, H, term_len, over=0.02):
    """A rectangular ceramic chip capacitor body with two metallised end
    terminations. Centred at the origin in X/Y, sitting on Z=0 (the
    substrate surface) up to Z=H -- long axis along X."""
    body = Part.makeBox(L, W, H, V(-L / 2.0, -W / 2.0, 0))
    term1 = Part.makeBox(term_len, W + 2 * over, H + 2 * over,
                          V(-L / 2.0, -W / 2.0 - over, -over))
    term2 = Part.makeBox(term_len, W + 2 * over, H + 2 * over,
                          V(L / 2.0 - term_len, -W / 2.0 - over, -over))
    return Part.makeCompound([body, term1, term2])


def make_pkg20(L, W, H_body, H_lid, lid_margin, n_per_side, pitch,
               lead_w, lead_t, lead_out, lead_foot):
    """A sealed metal/ceramic 20-pin hybrid flatpack: a rectangular base
    with an inset lid, and n_per_side gull-wing leads per long side.
    Centred at the origin in X/Y, sitting on Z=0 -- each lead's foot
    rests on Z=0 for a realistic seated placement."""
    shapes = [
        Part.makeBox(L, W, H_body, V(-L / 2.0, -W / 2.0, 0)),
        Part.makeBox(L - 2 * lid_margin, W - 2 * lid_margin, H_lid,
                      V(-L / 2.0 + lid_margin, -W / 2.0 + lid_margin, H_body)),
    ]

    span = (n_per_side - 1) * pitch
    x0 = -span / 2.0
    lead_mid_z = H_body * 0.5

    for i in range(n_per_side):
        x = x0 + i * pitch
        for y_wall, y_dir in ((W / 2.0, 1), (-W / 2.0, -1)):
            y_outer = y_wall + y_dir * lead_out
            # 1) stub: horizontal, from the body wall out to y_outer
            stub_y0 = min(y_wall, y_outer)
            stub_y1 = max(y_wall, y_outer)
            shapes.append(Part.makeBox(lead_w, stub_y1 - stub_y0, lead_t,
                                        V(x - lead_w / 2.0, stub_y0, lead_mid_z - lead_t / 2.0)))
            # 2) drop: vertical, at y_outer, from the substrate up to the stub
            drop_y0 = min(y_outer, y_outer + y_dir * lead_t)
            drop_y1 = max(y_outer, y_outer + y_dir * lead_t)
            shapes.append(Part.makeBox(lead_w, drop_y1 - drop_y0, lead_mid_z + lead_t / 2.0,
                                        V(x - lead_w / 2.0, drop_y0, 0)))
            # 3) foot: horizontal, at the substrate, extending further out
            y_tip = y_outer + y_dir * lead_foot
            foot_y0 = min(y_outer, y_tip)
            foot_y1 = max(y_outer, y_tip)
            shapes.append(Part.makeBox(lead_w, foot_y1 - foot_y0, lead_t,
                                        V(x - lead_w / 2.0, foot_y0, 0)))

    return Part.makeCompound(shapes)


def _gullwing_side(y_wall, y_dir, leads, lead_out, lead_t, lead_mid_z, lead_foot):
    """leads: list of (x, lead_w). One stub/drop/foot triple per lead
    -- the same 3-box gull-wing shape make_pkg20 uses, factored out so
    a package can put a different, independent set of leads on each
    side (needed for e.g. SOT-23's 2-leads/1-lead split and SOT-223's
    3 leads + 1 wide tab lead)."""
    shapes = []
    for x, lead_w in leads:
        y_outer = y_wall + y_dir * lead_out
        stub_y0 = min(y_wall, y_outer)
        stub_y1 = max(y_wall, y_outer)
        shapes.append(Part.makeBox(lead_w, stub_y1 - stub_y0, lead_t,
                                    V(x - lead_w / 2.0, stub_y0, lead_mid_z - lead_t / 2.0)))
        drop_y0 = min(y_outer, y_outer + y_dir * lead_t)
        drop_y1 = max(y_outer, y_outer + y_dir * lead_t)
        shapes.append(Part.makeBox(lead_w, drop_y1 - drop_y0, lead_mid_z + lead_t / 2.0,
                                    V(x - lead_w / 2.0, drop_y0, 0)))
        y_tip = y_outer + y_dir * lead_foot
        foot_y0 = min(y_outer, y_tip)
        foot_y1 = max(y_outer, y_tip)
        shapes.append(Part.makeBox(lead_w, foot_y1 - foot_y0, lead_t,
                                    V(x - lead_w / 2.0, foot_y0, 0)))
    return shapes


def make_sot(L, W, H, lead_t, lead_out, lead_foot,
             leads_neg_y=(), leads_pos_y=(), heat_tab=None):
    """A generic small-outline-transistor gull-wing package: a
    rectangular body with independently configurable leads on its -Y
    and +Y sides (each a list of (x_mm, lead_width_mm) pairs) -- covers
    SOT-23/SOT-323's 2-leads/1-lead split and SOT-223's 3 leads + 1
    wide tab lead with the same shape generator. Centred at the origin
    in X/Y, sitting on Z=0.

    heat_tab, if given, is (y_dir, tab_width, tab_thickness, overhang)
    for a flat metal area flush with the body's top on the y_dir side
    (SOT-89's heatsink tab) -- physically part of the body, not a
    gull-wing lead."""
    shapes = [Part.makeBox(L, W, H, V(-L / 2.0, -W / 2.0, 0))]
    lead_mid_z = H * 0.5
    shapes.extend(_gullwing_side(-W / 2.0, -1, leads_neg_y, lead_out, lead_t, lead_mid_z, lead_foot))
    shapes.extend(_gullwing_side(W / 2.0, 1, leads_pos_y, lead_out, lead_t, lead_mid_z, lead_foot))
    if heat_tab is not None:
        y_dir, tab_w, tab_t, overhang = heat_tab
        y_wall = y_dir * W / 2.0
        y_outer = y_wall + y_dir * overhang
        y0 = min(y_wall, y_outer)
        y1 = max(y_wall, y_outer)
        shapes.append(Part.makeBox(tab_w, y1 - y0, tab_t, V(-tab_w / 2.0, y0, H - tab_t)))
    return Part.makeCompound(shapes)


def export(shape, name, out_dir):
    path = os.path.join(out_dir, name + ".step")
    shape.exportStep(path)
    App.Console.PrintMessage("HLayout: wrote %s (%d solids)\\n" % (path, len(shape.Solids)))
'''


def write_component_library_macro(
    out_dir: str,
    path: Optional[str] = None,
    ceramic_caps: Optional[List[Tuple[str, float, float, float, float]]] = None,
    pkg20: Optional[dict] = None,
    sot_packages: Optional[dict] = None,
) -> str:
    """Write a ``.FCMacro`` at ``path`` (defaults to
    ``<out_dir>/build_component_library.FCMacro``) that, run inside
    FreeCAD, builds every part in ``ceramic_caps`` (default:
    DEFAULT_CERAMIC_CAPS -- 0603/0805/1206), one 20-pin package
    (default: DEFAULT_PKG20), and every part in ``sot_packages``
    (default: DEFAULT_SOT_PACKAGES -- SOT-23/323/89/223) and exports
    each as its own ``<name>.step`` file into ``out_dir``.

    ``ceramic_caps`` is a list of (name, length_mm, width_mm,
    height_mm, end_termination_length_mm) tuples -- pass your own to
    generate different case sizes (or none at all, with an empty
    list). ``pkg20`` overrides any subset of DEFAULT_PKG20's keys
    (e.g. ``{"leads_per_side": 8}`` for a 16-pin version) -- pass
    ``pkg20=False`` to skip the package entirely. ``sot_packages`` is a
    ``{name: kwargs}`` dict of make_sot() calls (see
    DEFAULT_SOT_PACKAGES) -- pass your own to add/replace SOT variants
    (e.g. a SOT-23-5), or ``{}``/``sot_packages=False`` to skip the
    family entirely.

    Returns ``path``. The STEP files themselves only exist once the
    macro is actually run inside FreeCAD -- this function only writes
    the macro text, the same "this module only emits text" convention
    hlayout.io.freecadio's design-export macro follows."""
    if path is None:
        path = os.path.join(out_dir, "build_component_library.FCMacro")
    caps = DEFAULT_CERAMIC_CAPS if ceramic_caps is None else ceramic_caps
    pkg = None if pkg20 is False else {**DEFAULT_PKG20, **(pkg20 or {})}
    sots = DEFAULT_SOT_PACKAGES if sot_packages is None else (
        {} if sot_packages is False else sot_packages)

    lines = [
        '"""HLayout-generated FreeCAD macro: builds a small 3D component',
        "library (generic ceramic chip capacitors, a 20-pin sealed",
        "metal/ceramic hybrid flatpack, and the SOT small-outline-",
        "transistor gull-wing package family) and exports each as its",
        "own STEP file, ready to be referenced from Setup > Technology",
        'Properties..."s Resistors/component_models "model" mode -- see',
        'hlayout.io.component_library.write_component_library_macro().',
        "",
        "Run: Macro > Macros... > Execute (inside FreeCAD), or",
        '``freecadcmd build_component_library.FCMacro`` from a shell."""',
        "import os",
        "import FreeCAD as App",
        "import Part",
        "",
        "V = App.Vector",
        f"OUT_DIR = {out_dir!r}",
        "os.makedirs(OUT_DIR, exist_ok=True)",
        "",
        "",
        _GEOMETRY_FUNCTIONS,
        "",
        '_doc = App.newDocument("HLayoutComponentLibrary")',
        "",
    ]

    for name, length, width, height, term in caps:
        lines.append(
            f"export(make_ceramic_cap({length!r}, {width!r}, {height!r}, {term!r}), "
            f"{name!r}, OUT_DIR)"
        )

    if pkg is not None:
        lines.append(
            "export(make_pkg20({length_mm!r}, {width_mm!r}, {body_height_mm!r}, "
            "{lid_height_mm!r}, {lid_margin_mm!r}, {leads_per_side!r}, {pitch_mm!r}, "
            "{lead_width_mm!r}, {lead_thickness_mm!r}, {lead_out_mm!r}, "
            "{lead_foot_mm!r}), 'PKG20', OUT_DIR)".format(**pkg)
        )

    for name, kwargs in sots.items():
        args = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
        lines.append(f"export(make_sot({args}), {name!r}, OUT_DIR)")

    lines.append('App.Console.PrintMessage("HLayout: component library build complete.\\n")')

    out_text = "\n".join(lines) + "\n"
    macro_dir = os.path.dirname(os.path.abspath(path))
    if macro_dir:
        os.makedirs(macro_dir, exist_ok=True)
    with open(path, "w") as f:
        f.write(out_text)
    return path
