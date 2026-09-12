"""FreeCAD 3D component library builder.

Writes a ``.FCMacro`` -- a plain Python script -- that, run inside
FreeCAD (Macro > Macros... > Execute, or ``freecadcmd file.FCMacro``),
builds a small set of generic 3D component bodies and exports each as
its own STEP file: a few standard EIA ceramic chip-capacitor case
sizes, and a 20-pin sealed metal/ceramic hybrid flatpack.

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

#: (name, length_mm, width_mm, height_mm, end_termination_length_mm) --
#: EIA imperial case-size codes, roughly true to their nominal size
#: (real vendor dimensions vary a little around these).
DEFAULT_CERAMIC_CAPS: Tuple[Tuple[str, float, float, float, float], ...] = (
    ("CAP_0603", 1.6, 0.8, 0.8, 0.3),
    ("CAP_0805", 2.0, 1.25, 1.25, 0.4),
    ("CAP_1206", 3.2, 1.6, 1.6, 0.5),
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
) -> str:
    """Write a ``.FCMacro`` at ``path`` (defaults to
    ``<out_dir>/build_component_library.FCMacro``) that, run inside
    FreeCAD, builds every part in ``ceramic_caps`` (default:
    DEFAULT_CERAMIC_CAPS -- 0603/0805/1206) plus one 20-pin package
    (default: DEFAULT_PKG20) and exports each as its own
    ``<name>.step`` file into ``out_dir``.

    ``ceramic_caps`` is a list of (name, length_mm, width_mm,
    height_mm, end_termination_length_mm) tuples -- pass your own to
    generate different case sizes (or none at all, with an empty
    list). ``pkg20`` overrides any subset of DEFAULT_PKG20's keys
    (e.g. ``{"leads_per_side": 8}`` for a 16-pin version) -- pass
    ``pkg20=False`` to skip the package entirely.

    Returns ``path``. The STEP files themselves only exist once the
    macro is actually run inside FreeCAD -- this function only writes
    the macro text, the same "this module only emits text" convention
    hlayout.io.freecadio's design-export macro follows."""
    if path is None:
        path = os.path.join(out_dir, "build_component_library.FCMacro")
    caps = DEFAULT_CERAMIC_CAPS if ceramic_caps is None else ceramic_caps
    pkg = None if pkg20 is False else {**DEFAULT_PKG20, **(pkg20 or {})}

    lines = [
        '"""HLayout-generated FreeCAD macro: builds a small 3D component',
        "library (generic ceramic chip capacitors and a 20-pin sealed",
        "metal/ceramic hybrid flatpack) and exports each as its own STEP",
        "file, ready to be referenced from Setup > Technology",
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

    lines.append('App.Console.PrintMessage("HLayout: component library build complete.\\n")')

    out_text = "\n".join(lines) + "\n"
    macro_dir = os.path.dirname(os.path.abspath(path))
    if macro_dir:
        os.makedirs(macro_dir, exist_ok=True)
    with open(path, "w") as f:
        f.write(out_text)
    return path
