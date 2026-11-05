# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""FreeCAD 3D STEP export for the standalone example/practice package
generators in ``examples/generate_*.py`` -- SOT (incl. the 8/16/18/
20/24/28-pin gull-wing IC variants), LCC, tantalum capacitors, ceramic
substrates, and Kyocera-style CFP packages.

Geometry convention matches ``hlayout.io.component_library`` (and
``hlayout.core.smdlib``'s 2D footprint library, for the same reason --
see that module's own docstring): each part's origin (0, 0) is the
BOTTOM-LEFT corner of its body footprint in X/Y -- the body spans
(0, 0) to (length_mm, width_mm) -- sitting on Z=0. The ``examples/
generate_*.py`` 2D cells this pairs with use the SAME bottom-left
origin (each script's own docstring says so), so a STEP file
generated here lines up exactly with the GDS cell of the same name
from the SAME script -- and with the rest of hlayout's component
library, if one of these ever gets wired into
``Session.export_freecad()``'s ``component_models`` too.

Like ``hlayout.io.component_library``, this module only emits text --
FreeCAD does the modelling -- so importing it adds no dependency
beyond the standard library; FreeCAD (``freecadcmd``) is needed only
to *run* the macro this writes. Every part is a Part compound of
simple boxes/cylinders (not a boolean-fused solid), the same
reliability reasoning ``hlayout.io.component_library`` documents.

Verified against FreeCAD 1.1.1 (freecadcmd, headless).
"""

import os
import subprocess
from typing import List, Optional, Tuple

_GEOMETRY_FUNCTIONS_3D = '''\
def make_end_cap(L, W, H, term_len, overhang=0.0, over=0.02):
    """A rectangular body with two metallised end terminations, each
    extending `overhang` past the body's own end -- the real
    wraparound solder fillet sticking out beyond the body, matching
    generate_tantalum_capacitors.py's own pad_outer/pad_cx overhang
    math exactly (overhang=0 draws terminations flush with the body
    ends instead). Origin at the BOTTOM-LEFT corner in X/Y (body spans
    (0, 0) to (L, W)), sitting on Z=0 up to Z=H -- long axis along X."""
    body = Part.makeBox(L, W, H, V(-L / 2.0, -W / 2.0, 0))
    pad_outer = L / 2.0 + overhang
    pad_inner = pad_outer - term_len
    term1 = Part.makeBox(term_len, W + 2 * over, H + 2 * over,
                          V(-pad_outer, -W / 2.0 - over, -over))
    term2 = Part.makeBox(term_len, W + 2 * over, H + 2 * over,
                          V(pad_inner, -W / 2.0 - over, -over))
    shape = Part.makeCompound([body, term1, term2])
    shape.translate(V(L / 2.0, W / 2.0, 0))
    return shape


def make_lcc(side, pitch, per_side, pad_w, pad_depth, H):
    """A square leadless chip carrier: a body with castellated pads
    straddling all four edges, full-height (Z=0 to H) the way a real
    castellation plates the fired side of the part. Origin at the
    BOTTOM-LEFT corner in X/Y (body spans (0, 0) to (side, side)),
    matching generate_lcc_packages.py's own bottom-left cell origin
    (see that script for the pin-1-bottom-left, counterclockwise
    numbering this follows)."""
    half = side / 2.0
    shapes = [Part.makeBox(side, side, H, V(-half, -half, 0))]
    span = (per_side - 1) * pitch
    xs = [(-span / 2.0) + k * pitch for k in range(per_side)]

    def _pad(cx, cy, horizontal):
        if horizontal:
            return Part.makeBox(pad_w, pad_depth, H, V(cx - pad_w / 2.0, cy - pad_depth / 2.0, 0))
        return Part.makeBox(pad_depth, pad_w, H, V(cx - pad_depth / 2.0, cy - pad_w / 2.0, 0))

    for x in xs:
        shapes.append(_pad(x, -half, True))
    for y in xs:
        shapes.append(_pad(half, y, False))
    for x in reversed(xs):
        shapes.append(_pad(x, half, True))
    for y in reversed(xs):
        shapes.append(_pad(-half, y, False))
    shape = Part.makeCompound(shapes)
    shape.translate(V(half, half, 0))
    return shape


def make_substrate_pins(L, W, H, per_side, pitch, pad_dia, pin_length):
    """A dual-row ceramic substrate: a body with round pads on its two
    long edges. pin_length > 0 draws each pad as a cylindrical pin
    running from pin_length below Z=0 up through the body to Z=H (a
    braze feedthrough pin, the _HOLE variant); pin_length == 0 draws a
    thin round surface pad instead (no protruding pin). Origin at the
    BOTTOM-LEFT corner in X/Y (body spans (0, 0) to (L, W)), matching
    generate_ceramic_substrates.py's own bottom-left cell origin."""
    half_l, half_w = L / 2.0, W / 2.0
    shapes = [Part.makeBox(L, W, H, V(-half_l, -half_w, 0))]
    span = (per_side - 1) * pitch
    xs = [(-span / 2.0) + k * pitch for k in range(per_side)]
    r = pad_dia / 2.0
    for x in xs:
        for y in (-half_w, half_w):
            if pin_length > 0:
                shapes.append(Part.makeCylinder(r, H + pin_length, V(x, y, -pin_length)))
            else:
                pad_h = max(H * 0.1, 0.05)
                shapes.append(Part.makeCylinder(r, pad_h, V(x, y, H - pad_h)))
    shape = Part.makeCompound(shapes)
    shape.translate(V(half_l, half_w, 0))
    return shape


def _gullwing_side(y_wall, y_dir, leads, lead_out, lead_t, lead_mid_z, lead_foot):
    """leads: list of (x, lead_w). One stub/drop/foot triple per lead."""
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


def make_dip(L, W, H, leads_neg_y, leads_pos_y, lead_t, lead_out, lead_foot, lid=None):
    """A dual-row gull-wing package with its two rows along Y (at
    Y=0 and Y=W) and pins spread along X within each row -- covers a
    Kyocera-style CFP (lid=(lid_margin, lid_height); see
    generate_kyocera_cfp_packages.py, whose own 2D convention runs
    this same way). Origin at the BOTTOM-LEFT corner in X/Y (body
    spans (0, 0) to (L, W)), sitting on Z=0."""
    shapes = [Part.makeBox(L, W, H, V(-L / 2.0, -W / 2.0, 0))]
    lead_mid_z = H * 0.5
    shapes.extend(_gullwing_side(-W / 2.0, -1, leads_neg_y, lead_out, lead_t, lead_mid_z, lead_foot))
    shapes.extend(_gullwing_side(W / 2.0, 1, leads_pos_y, lead_out, lead_t, lead_mid_z, lead_foot))
    if lid is not None:
        lid_margin, lid_h = lid
        shapes.append(Part.makeBox(L - 2 * lid_margin, W - 2 * lid_margin, lid_h,
                                    V(-L / 2.0 + lid_margin, -W / 2.0 + lid_margin, H)))
    shape = Part.makeCompound(shapes)
    shape.translate(V(L / 2.0, W / 2.0, 0))
    return shape


def _gullwing_side_x(x_wall, x_dir, leads, lead_out, lead_t, lead_mid_z, lead_foot):
    """The _gullwing_side() twin for a package whose two rows run
    along X instead of Y -- leads: list of (y, lead_w)."""
    shapes = []
    for y, lead_w in leads:
        x_outer = x_wall + x_dir * lead_out
        stub_x0 = min(x_wall, x_outer)
        stub_x1 = max(x_wall, x_outer)
        shapes.append(Part.makeBox(stub_x1 - stub_x0, lead_w, lead_t,
                                    V(stub_x0, y - lead_w / 2.0, lead_mid_z - lead_t / 2.0)))
        drop_x0 = min(x_outer, x_outer + x_dir * lead_t)
        drop_x1 = max(x_outer, x_outer + x_dir * lead_t)
        shapes.append(Part.makeBox(drop_x1 - drop_x0, lead_w, lead_mid_z + lead_t / 2.0,
                                    V(drop_x0, y - lead_w / 2.0, 0)))
        x_tip = x_outer + x_dir * lead_foot
        foot_x0 = min(x_outer, x_tip)
        foot_x1 = max(x_outer, x_tip)
        shapes.append(Part.makeBox(foot_x1 - foot_x0, lead_w, lead_t,
                                    V(foot_x0, y - lead_w / 2.0, 0)))
    return shapes


def make_dip_x(L, W, H, leads_neg_x, leads_pos_x, lead_t, lead_out, lead_foot):
    """The make_dip() twin for a package whose two rows run along X
    instead of Y (at X=0 and X=L), pins spread along Y within each
    row -- matches generate_sot_packages.py's own 2D convention (see
    that script's build_package() for why rows run along X there).
    Origin at the BOTTOM-LEFT corner in X/Y (body spans (0, 0) to
    (L, W)), sitting on Z=0."""
    shapes = [Part.makeBox(L, W, H, V(-L / 2.0, -W / 2.0, 0))]
    lead_mid_z = H * 0.5
    shapes.extend(_gullwing_side_x(-L / 2.0, -1, leads_neg_x, lead_out, lead_t, lead_mid_z, lead_foot))
    shapes.extend(_gullwing_side_x(L / 2.0, 1, leads_pos_x, lead_out, lead_t, lead_mid_z, lead_foot))
    shape = Part.makeCompound(shapes)
    shape.translate(V(L / 2.0, W / 2.0, 0))
    return shape


def export(shape, name, out_dir):
    path = os.path.join(out_dir, name + ".step")
    if os.path.isfile(path):
        App.Console.PrintMessage("HLayout: %s already exists, leaving it alone\\n" % path)
        return
    shape.exportStep(path)
    App.Console.PrintMessage("HLayout: wrote %s (%d solids)\\n" % (path, len(shape.Solids)))
'''


def soic_leads(n_per_side: int, pitch: float, lead_w: float) -> List[Tuple[float, float]]:
    """[(x_mm, lead_w), ...] for n_per_side leads centred on x=0 at
    `pitch` spacing -- the symmetric per-side lead layout make_dip's
    leads_neg_y/leads_pos_y expect, shared by SOT-8+ and Kyocera CFP."""
    span = (n_per_side - 1) * pitch
    x0 = -span / 2.0
    return [(round(x0 + i * pitch, 4), lead_w) for i in range(n_per_side)]


def write_step_macro(parts: List[Tuple[str, str, dict]], out_dir: str,
                      path: Optional[str] = None) -> str:
    """Write a ``.FCMacro`` at `path` (default:
    ``<out_dir>/build_step_models.FCMacro``) that, run inside FreeCAD,
    builds every part in `parts` -- a list of (name, geometry_function,
    kwargs) -- and exports each as its own ``<name>.step`` file into
    `out_dir`. `geometry_function` is one of "make_end_cap"/"make_lcc"/
    "make_substrate_pins"/"make_dip" (see _GEOMETRY_FUNCTIONS_3D
    above). Returns `path`; run it with
    ``freecadcmd build_step_models.FCMacro`` (or run_step_macro()
    below) to actually produce the STEP files -- writing the macro
    never touches FreeCAD itself. Re-running never overwrites a
    ``<name>.step`` that already exists in `out_dir`."""
    if path is None:
        path = os.path.join(out_dir, "build_step_models.FCMacro")

    lines = [
        '"""HLayout-generated FreeCAD macro: builds 3D STEP models for',
        "one of the examples/generate_*.py standalone package scripts,",
        "centred on the origin to match that script's own 2D cells --",
        "see hlayout.io.example_step3d.write_step_macro().",
        "",
        "Run: Macro > Macros... > Execute (inside FreeCAD), or",
        '``freecadcmd build_step_models.FCMacro`` from a shell."""',
        "import os",
        "import FreeCAD as App",
        "import Part",
        "",
        "V = App.Vector",
        f"OUT_DIR = {out_dir!r}",
        "os.makedirs(OUT_DIR, exist_ok=True)",
        "",
        "",
        _GEOMETRY_FUNCTIONS_3D,
        "",
        '_doc = App.newDocument("HLayoutStepModels")',
        "",
    ]
    for name, func, kwargs in parts:
        args = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
        lines.append(f"export({func}({args}), {name!r}, OUT_DIR)")
    lines.append('App.Console.PrintMessage("HLayout: STEP model build complete.\\n")')

    out_text = "\n".join(lines) + "\n"
    macro_dir = os.path.dirname(os.path.abspath(path))
    if macro_dir:
        os.makedirs(macro_dir, exist_ok=True)
    with open(path, "w") as f:
        f.write(out_text)
    return path


#: Common install locations to try when plain "freecadcmd" isn't on
#: PATH -- glob patterns so any installed FreeCAD version matches
#: without hardcoding one. Checked in order; the first that exists on
#: disk wins.
_FREECAD_FALLBACK_GLOBS = [
    r"C:\Program Files\FreeCAD *\bin\freecadcmd.exe",
    r"C:\Program Files (x86)\FreeCAD *\bin\freecadcmd.exe",
    "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
    "/usr/bin/freecadcmd",
    "/usr/local/bin/freecadcmd",
]


def find_freecadcmd() -> Optional[str]:
    """Locate a freecadcmd executable: plain "freecadcmd" if it's on
    PATH, else the first match among _FREECAD_FALLBACK_GLOBS, else
    None."""
    import glob
    import shutil
    found = shutil.which("freecadcmd")
    if found:
        return found
    for pattern in _FREECAD_FALLBACK_GLOBS:
        matches = sorted(glob.glob(pattern), reverse=True)  # newest version first
        if matches:
            return matches[0]
    return None


def run_step_macro(macro_path: str, freecadcmd: Optional[str] = None, timeout: int = 120) -> str:
    """Run `macro_path` with `freecadcmd` (its full path, or None to
    auto-locate one with find_freecadcmd()) and return its combined
    stdout/stderr. Raises RuntimeError (including that output) if
    freecadcmd can't be found/run or exits non-zero -- this is the one
    place in this module that actually needs FreeCAD installed; writing
    the macro itself never does."""
    exe = freecadcmd or find_freecadcmd()
    if exe is None:
        raise RuntimeError(
            "freecadcmd not found on PATH or in any common install location -- "
            f"install FreeCAD, or pass its full path, to actually run {macro_path} "
            "and produce STEP files"
        )
    try:
        result = subprocess.run(
            [exe, macro_path], capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(f"{exe!r} not found")
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise RuntimeError(f"freecadcmd exited {result.returncode}:\n{output}")
    return output
