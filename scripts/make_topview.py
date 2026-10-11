r"""make_topview.py -- build the annotated assembly top view in cell TOPVIEW.

Draws a hidden-line top view of the main design cell into the TOPVIEW
cell (cleared and rebuilt each run), then adds what the plain top view
leaves out:

  * conductor hatched at one angle, hatch lines thinner than outlines
  * substrate outline
  * package outline and pins
  * reference designators, centred on each part

The design cell itself is never modified. One undo step.

Use inside HLayout (command bar, no PY prefix, one line at a time):
    exec(open(r"C:\dev\HLayout\scripts\make_topview.py").read())
    make_topview(s)

Options, e.g.:
    make_topview(s, hatch_pitch=300, hatch_angle=30)
    make_topview(s, hatch_pitch=0)            # no hatch
"""
import math

# ---- settings (all lengths in DBU = microns) ------------------------------
RESULT_LAYER = 48              # layer the whole drawing is written on
OUT_CELL = "TOPVIEW"           # output cell (rebuilt every run)
LINE_WIDTH = 100               # outlines
HATCH_PITCH = 200              # 0 = no hatch
HATCH_ANGLE = 45.0             # single direction; use (45, -45) for cross
HATCH_WIDTH = 50               # hatch line width
EXCLUDE_LAYERS = (4, 61, 62, 63)   # dielectric + document/refdes/dimension
SUBSTRATE_CELL = "Substrate"
PACKAGE_CELL = "20_PIN_pkg"
REFDES_HEIGHT = 500
REFDES_CHAR_WIDTH = 0.6        # glyph width as a fraction of text height


def _xform(pt, origin, rotation, mag, mirror):
    x, y = pt
    if mirror:
        y = -y
    a = math.radians(rotation)
    return (origin[0] + mag * (x * math.cos(a) - y * math.sin(a)),
            origin[1] + mag * (x * math.sin(a) + y * math.cos(a)))


def _outlines(library, cell, chain=()):
    """Closed outline rings of every Rectangle/Polygon in `cell` and in
    everything placed inside it, in the coordinates of the outermost
    placement in `chain`."""
    rings = []
    for sh in cell.shapes:
        if type(sh).__name__ in ("Rectangle", "Polygon"):
            pts = list(sh.points)
            for t in reversed(chain):
                pts = [_xform(p, *t) for p in pts]
            pts = [(int(round(x)), int(round(y))) for x, y in pts]
            rings.append(pts + [pts[0]])
    for r in cell.srefs:
        t = (r.origin, r.rotation, r.magnification, r.x_reflection)
        rings += _outlines(library, library.get_cell(r.cell_name), chain + (t,))
    for a in cell.arefs:
        child = library.get_cell(a.cell_name)
        for row in range(a.rows):
            for col in range(a.columns):
                local = (col * a.col_spacing, row * a.row_spacing)
                # array offsets are applied before the array's own
                # rotation/mirror, then moved to its origin
                org = _xform(local, a.origin, a.rotation, a.magnification,
                             a.x_reflection)
                t = (org, a.rotation, a.magnification, a.x_reflection)
                rings += _outlines(library, child, chain + (t,))
    return rings


def _is_hatch(shape, angles):
    if type(shape).__name__ != "Path" or len(shape.points) != 2:
        return False
    dx = shape.points[1][0] - shape.points[0][0]
    dy = shape.points[1][1] - shape.points[0][1]
    if dx == 0 or dy == 0:
        return False
    # Allow a couple of DBU of sideways drift: hatch ends are rounded to
    # the grid, so a short stub is not at exactly the nominal angle.
    for a in angles:
        r = math.radians(a)
        if abs(dx * math.sin(r) - dy * math.cos(r)) <= 2.5:
            return True
    return False


def make_topview(session, result_layer=RESULT_LAYER, out_cell=OUT_CELL,
                 line_width=LINE_WIDTH, hatch_pitch=HATCH_PITCH,
                 hatch_angle=HATCH_ANGLE, hatch_width=HATCH_WIDTH,
                 exclude_layers=EXCLUDE_LAYERS, substrate_cell=SUBSTRATE_CELL,
                 package_cell=PACKAGE_CELL, refdes_height=REFDES_HEIGHT):
    """Build the annotated top view. Returns a summary dict."""
    lib = session.library
    main = lib.top_cell_name
    if not main or main == out_cell:
        raise ValueError("no main design cell to draw a top view of")
    top = lib.get_cell(main)
    angles = (tuple(float(a) for a in hatch_angle)
              if isinstance(hatch_angle, (tuple, list)) else (float(hatch_angle),))
    previous = session.cell.name
    summary = {"view_of": main, "cell": out_cell, "layer": result_layer}

    with session.batch():                       # one undo step
        kwargs = dict(cell_names=[main], exclude_layers=list(exclude_layers),
                      line_width=line_width, out_cell=out_cell)
        if hatch_pitch:
            kwargs["hatch_conductor_pitch"] = hatch_pitch
            kwargs["hatch_conductor_angles"] = angles
        written = session.mask_op_top_view(result_layer, **kwargs)
        summary["view_paths"] = written.get(main, 0)
        if out_cell not in lib.cells:
            lib.new_cell(out_cell)
        view = lib.get_cell(out_cell)

        # thinner hatch lines
        hatch = ([sh for sh in view.shapes if _is_hatch(sh, angles)]
                 if hatch_pitch else [])
        for sh in hatch:
            sh.width = hatch_width
        summary["hatch_lines"] = len(hatch)

        session.use_cell(out_cell)
        try:
            # substrate and package (with pins): outlines of the placed cells
            frames = 0
            for r in top.srefs:
                if r.cell_name in (substrate_cell, package_cell):
                    t = (r.origin, r.rotation, r.magnification, r.x_reflection)
                    for ring in _outlines(lib, lib.get_cell(r.cell_name), (t,)):
                        session.path(result_layer, ring, line_width)
                        frames += 1
            summary["frame_paths"] = frames

            # reference designators, centred on each labelled part
            labels = 0
            for r in top.srefs:
                if not r.label:
                    continue
                x1, y1, x2, y2 = lib.cell_bbox(r.cell_name)
                cx, cy = _xform(((x1 + x2) / 2.0, (y1 + y2) / 2.0), r.origin,
                                r.rotation, r.magnification, r.x_reflection)
                half = REFDES_CHAR_WIDTH * refdes_height * len(r.label) / 2.0
                session.text(result_layer,
                             (int(round(cx - half)),
                              int(round(cy - refdes_height / 2.0))),
                             r.label, refdes_height)
                labels += 1
            summary["ref_des"] = labels
        finally:
            session.use_cell(previous)

    layer = lib.layers[result_layer]
    if not layer.visible:
        layer.visible = True
    return summary
