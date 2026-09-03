"""Text command-line interpreter for the GUI's command bar.

This gives the tool an AutoCAD-style command line as an alternative to
clicking toolbar buttons — type a command and arguments, press Enter.
Unrecognised input is treated as a line of Python and handed to the
Session's ScriptConsole, so the command bar doubles as a quick script
prompt (e.g. type ``PY print(s.cell.shapes)``, or just
``s.rect(0,0,0,10,10)`` directly). Type ``HELP`` in the command bar (or
use File > Command Reference in the GUI) for the categorized command
list below, at any time — see ``COMMAND_REFERENCE``.
"""

import shlex
from typing import Callable, Optional

from ..core.errors import GeometryError, LayoutError
from ..core.maskops import MASK_OPS
from ..core.units import dbu_to_mm, mm_to_dbu

# The full command-bar reference, grouped by category. This is the
# single source of truth for command syntax — surfaced via the HELP
# command, the File > Command Reference dialog, and this module's
# docstring above, so all three always agree.
COMMAND_REFERENCE = """\
Command bar reference (case-insensitive; comma- or space-separated
point args like `0,0` or `0 0` both work). Anywhere <layer> appears,
pass either a layer number (0-63) or an existing layer name (set one
via LAYER ... name=... or the Layers tab, then refer to it by name
from then on).

Cells & shapes:
    CELL <name>                       create-or-switch current cell
    RECT <layer> <x1,y1> <x2,y2>      add a rectangle
    POLY <layer> <x1,y1> <x2,y2> ...  add a polygon (>=3 points)
    PATH <layer> <width> <cap> <x1,y1> <x2,y2> ...
                                       add a path; cap = round|square|flush
    CIRCLE <layer> <cx,cy> <radius> [segments]
                                       add a circle (regular polygon);
                                       segments: 8, 16, 32, or 64 (default 32)
    TEXT <layer> <x,y> "<string>" [height] [font]
    SREF <cellname> <x,y> [rotation] [mag] [mirror]
    AREF <cellname> <x,y> <columns> <rows> <col_spacing> <row_spacing>
         [rotation] [mag] [mirror]
                                       add an array of cell instances (GDSII AREF) --
                                       columns x rows, spaced col_spacing/row_spacing
                                       DBU apart before rotation/mirror is applied;
                                       see SMASH to explode one back into plain SREFs
    DELETE LAST                       remove the most recently added shape/sref/aref

Layers:
    LAYER <n|name> [color=#rrggbb] [stipple=0-8] [fill=0|1] [visible=0|1] [name=...]
    LAYER ALL visible=0|1             turn every layer off/on at once
    LAYER MOVE <from> <to> [cell=<name>]
                                       move every shape on <from> to <to>, across
                                       every cell in the library (or just cell=<name>);
                                       from/to accept a layer number or name -- the
                                       way to align an existing design's geometry
                                       with a layer stack loaded from a technology file

View:
    ZOOM IN | OUT | FIT | <factor>
    ZOOM WINDOW <x1,y1> <x2,y2>       zoom/pan so that DBU-space rectangle fills
                                       the view -- the typed/scripted equivalent
                                       of dragging a Zoom Window box in the GUI
                                       (View > Zoom Window, or the toolbar button)
    FIT                                shorthand for ZOOM FIT -- fit the whole
                                        current cell in the active view
    GRID                              show whether the grid is on/off and its
                                        current spacing
    GRID ON | OFF                     show/hide the grid
    GRID <spacing_mm>                 change the grid spacing (also updates
                                        snap-to-grid to match)

Selection (Select tool click a shape/cell instance first -- it turns
white so it's clear what's selected):
    MOVE <dx,dy>                      move the current selection by dx,dy DBU
    ROTATE <degrees>                  rotate the current selection (CCW)
    COPY <dx,dy>                      duplicate the current selection, offsetting
                                       the copy by dx,dy DBU, and select the copy
    PROPS                             list the current selection's properties --
                                       layer/width for a path, layer for a polygon/
                                       rectangle, cell name/origin/rotation/etc for
                                       a cell instance (also in the Properties tab)
    SMASH                             explode the selected array instance (AREF) back
                                       into individual cell instances (SREFs), one per
                                       array position, and select the new SREFs
    UNION                             merge a window-selected group of Polygon/
                                       Rectangle shapes on the same layer into one
                                       polygon, wherever they touch or overlap --
                                       errors if they're on different layers or
                                       don't actually touch/overlap; see also
                                       MASKOP OR for unioning a whole layer instead
    MAKECELL <name> <x,y>            turn the current selection into a new named
      [replace=1 | revision=1]        cell (moves the selected shapes/instances
                                       into it, re-based so x,y becomes the new
                                       cell's local origin), then places a fresh
                                       instance of it at x,y so nothing visually
                                       moves -- draw/place geometry, select it,
                                       MAKECELL, and it's a reusable library cell.
                                       On a name clash: replace=1 rebuilds the
                                       existing cell in place (its instances keep
                                       working and switch to the new geometry);
                                       revision=1 saves under the next free
                                       <name>_N instead, leaving the old cell and
                                       its instances untouched

Undo/redo (covers every edit, however it was made -- command bar,
mouse-driven select/move/rotate/edit/delete, or a script):
    UNDO                              undo the last edit to the current design
    REDO                              redo the last undone edit

Files:
    SAVE <path.gds> [library_name]    with no library_name: saves the design plus a
                                       sidecar (<path>.hlaux) capturing technology
                                       (layer stack/tech params, incl. Resistor Designer
                                       rules) + a full copy of every attached library;
                                       with library_name: publishes a plain, bare .gds
                                       under that library name (no sidecar) -- for
                                       Save As Library / reusable part libraries
    OPEN <path.gds>                   restores the sidecar above, if one sits next to
                                       the file (a plain .gds with none opens as usual)
    GERBER <output_prefix> [cell=<name>]
                                       export Gerber RS-274X -- one flattened file per
                                       visible layer with data, named
                                       <output_prefix>_L<NN>_<layer name>.gbr;
                                       cell defaults to the current cell
    MERGE <lib1.gds> <lib2.gds> <lib3.gds> [library_name]
                                       combine every cell from lib1 and lib2 into a
                                       new file lib3 -- a plain file-to-file operation,
                                       doesn't touch the currently open design; a cell
                                       name that exists in both files gets a numeric
                                       suffix (_2, _3, ...) in lib3 so nothing collides

Technology file (layer stack + open-ended global parameters; usually
loaded once at the start of a design):
    TECH LOAD <path.json>             load a technology file, applying it to the
                                       current library's layer stack + params
    TECH SAVE <path.json>             save the current layer stack + params
    TECH SET <key> <value>            set a global technology parameter
    TECH GET <key>                    show one technology parameter
    TECH PARAMS                       list all technology parameters

Attached libraries:
    ATTACH <path.gds>                 attach a GDSII file as a reference library (max 2)
    LIBS                              list attached reference libraries
    LIBLIST [current|0|1]             list the cells in a library (default: current)
    FIND <substring>                  search cell names, current + attached libraries
    PLACE <cellname> <x,y> [source=current|0|1] [rotation] [mag] [mirror]
                                       find-and-place a cell as an Sref; source
                                       disambiguates if the name exists in more
                                       than one attached library
    VIA <layer1> <layer2> [shape=square|round] [size=<dbu>] [name=<cellname>]
        [dielectric_layer=<n>] [dielectric_window=<dbu>] [index=0|1] [overwrite=0|1]
                                       create a via cell (a square/round pad on both
                                       layers, named <name> or VIA_Layer<n1>_to_Layer<n2>
                                       if not given) directly inside an attached library
                                       and save that library's file in place; shape/size
                                       default to square/100. dielectric_layer/window (if
                                       both given) also cut a same-shape opening in the
                                       dielectric between the two conductor layers
    PAD <name> <conductor_layer> <paste_layer> <length_mm> <width_mm> <margin_mm>
           [rows=1] [cols=1] [pitch_x=<mm>] [pitch_y=<mm>] [index=0|1] [overwrite=0|1]
                                       create a rectangular pad cell -- a conductor
                                       rectangle plus a solder paste rectangle undersized
                                       by margin_mm on every side -- directly inside an
                                       attached library and save that library's file in
                                       place; rows/cols lay out a centred grid of pads
                                       (pitch_x/pitch_y required when cols/rows > 1); a
                                       genuine 2-pad grid is auto-numbered pin 1/2

Pin numbering (a Rectangle/Polygon's pin -- see Edit > Number Pins... for the
click-to-increment canvas tool this backs; PAD/RESGEN auto-number 2-pad parts):
    PIN LIST [cell=<name>]            list every pin-capable shape in a cell (default:
                                       current cell) by index, layer, pin, and bbox
    PIN SET <index> <pin> [cell=<name>]
                                       set the pin number/name of the shape at <index>
                                       (from PIN LIST) -- pass an empty string ("") to
                                       clear a pin

Thick-film resistor generation (Tools > Thick-Film Resistor Designer... sizes
a resistor; this draws it):
    RESGEN <x,y> <body_width> <body_length> [rotation=<deg>] [ink=<rsh>]
           [ref=<designator>] [a=<dbu>] [b=<dbu>] [c=<dbu>] [d=<dbu>]
           [rlayer=<n|name>] [clayer=<n|name>] [glayer=<n|name>] [kolayer=<n|name>]
                                       build a resistor -- ink body + glaze overprint +
                                       its two end conductor pads -- as its own new
                                       cell, and place one instance (Sref) of it
                                       centred at x,y; body_width/body_length usually
                                       come from a sized Resistor's
                                       body_width/selected.body_length; ink=<rsh>
                                       looks up the ink layer by sheet resistance in
                                       the technology file's per-ink layer map
                                       (Tools > Thick-Film Resistor Designer... >
                                       Design Rules...); rlayer overrides that lookup
                                       entirely; ref=<name> (e.g. "R1") is set as the
                                       placed instance's ref designator -- the canvas
                                       floats it at the instance centre, draggable,
                                       same as any other part (no baked Text);
                                       a/b/c/d/clayer/glayer default to Setup >
                                       Resistor Geometry Rules... (the technology
                                       file) when omitted -- see that dialog for what
                                       A (overlap), B (pad length), C (side space),
                                       and D (glaze oversize, on all 4 sides) mean.
                                       dlayer= is still accepted but ignored.

Layer-based mask operations (resize/boolean, like a GDSII CAM system's
Size/AND/OR deck operators -- any Path on an input layer is converted
to its stroked-outline polygon first; requires the shapely package):
    MASKOP OVERSIZE <layer_a> result=<layer> amount=<dbu> [cell=<name>] [flatten=0|1]
                                       [simplify=<dbu>]
                                       grow everything on layer_a by amount DBU,
                                       write the result to result_layer (mitred
                                       corners, so rectilinear shapes stay square)
    MASKOP UNDERSIZE <layer_a> result=<layer> amount=<dbu> [cell=<name>] [flatten=0|1]
                                       [simplify=<dbu>]
                                       same, but shrink by amount DBU
    MASKOP AND <layer_a> <layer_b> result=<layer> [cell=<name>] [flatten=0|1] [simplify=<dbu>]
                                       intersection of layer_a and layer_b
    MASKOP OR <layer_a> <layer_b> result=<layer> [cell=<name>] [flatten=0|1] [simplify=<dbu>]
                                       union of layer_a and layer_b
    MASKOP NOT <layer_a> <layer_b> result=<layer> [cell=<name>] [flatten=0|1] [simplify=<dbu>]
                                       layer_a minus layer_b (difference)
    MASKOP XOR <layer_a> <layer_b> result=<layer> [cell=<name>] [flatten=0|1] [simplify=<dbu>]
                                       symmetric difference of layer_a and layer_b
                                       (everything on exactly one of the two)
                                       -- all six default to the current cell;
                                       cell=<name> runs on a different cell instead;
                                       result= may be the same layer as layer_a or a
                                       fresh one; flatten defaults to 1 (on), descending
                                       into every placed cell instance (Sref) -- e.g. a
                                       resistor placed by the Resistor Designer -- to
                                       gather layer_a/layer_b's input geometry; pass
                                       flatten=0 to look only at the operated-on cell's
                                       own directly-drawn shapes; simplify=<dbu> applies
                                       vertex-reduction (Douglas-Peucker, topology
                                       preserved) to the result at that DBU tolerance
                                       before it's written -- useful for staying under
                                       the 200-point-per-polygon limit on a heavily
                                       sized/booleaned result; also available as
                                       Session.mask_op()

Design Rule Check (DRC -- single-layer and between-layer geometric checks;
requires the shapely package; also available as Tools > DRC...):
    DRCRULE ADD <kind> <layer_a> [layer_b] value=<n> [name=<name>]
                                       add and save a rule to the technology file;
                                       kind: MIN_WIDTH/MIN_AREA/MIN_SPACE (one layer,
                                       omit layer_b) or MIN_SPACE_BETWEEN/MIN_ENCLOSURE
                                       (two layers, layer_b required) -- see Tools >
                                       DRC... for what each kind checks
    DRCRULE LIST                      list saved rules, numbered
    DRCRULE REMOVE <index>            remove one saved rule by its LIST number
    DRCRULE CLEAR                     remove every saved rule
    DRC RUN [cell=<name>] [error=<layer>]
                                       run every saved rule against the current cell
                                       (or cell=<name>), descending into every placed
                                       cell instance (Sref) the same way MASKOP does;
                                       error=<layer> also draws each violation's marker
                                       polygon onto that layer in the design; reports a
                                       violation count broken down by rule; also
                                       available as Session.run_drc()

Dielectric Fill (Tools > Generate Dielectric Fill...; requires shapely):
    DIELECTRIC [layer=<n>] [clearance=<dbu>] [margin=<dbu>] [keepout=<n>[,<n>...]]
        [cell=<name>]                   fill the dielectric layer from its own
                                       datatype-0 geometry -- the enclosing shape is
                                       the boundary, every other shape inside it (a
                                       via window, a resistor keep-out, a hand-drawn
                                       box) is a hole grown by clearance; result is
                                       written at datatype 1
    DIELECTRIC CLEAR [layer=<n>] [all=1] [cell=<name>]
                                       remove the generated fill (datatype 1) so it
                                       can be regenerated; all=1 wipes the layer,
                                       inputs included; also Session.generate_dielectric()
                                       / clear_dielectric()

Wire bonds (Setup > Wire Bond Rules...; chip-and-wire assembly jumpers):
    BOND <x1,y1> <x2,y2> [layer=<n|name>] [width=<dbu>] [pad=<dbu>] [min=<dbu>]
        [max=<dbu>] [cell=<name>]       draw a wire bond -- an O------O figure (a ball
                                       of diameter pad at each end joined by a
                                       width-wide round path) -- on the bond layer,
                                       tying a bare-die pad to a conductor pad, a
                                       conductor pad to a package pin, or two
                                       conductor pads (a jumper). The span must lie
                                       within min/max and must not cross an existing
                                       bond (crossed bare wires short). layer/width/
                                       pad/min/max default to Setup > Wire Bond
                                       Rules...; also Session.wire_bond()

Automatic routing (requires the shapely package; also the two-click
Edit > Auto-Route Two Pins tool):
    AUTOROUTE <x1,y1> <x2,y2> [layer=<n|name>] [grid=<dbu>] [width=<dbu>]
        [clearance=<dbu>] [margin=<dbu>] [boundary=<n|name>] [cell=<name>]
                                       route a clear orthogonal trace between the two
                                       points on the routing layer (default: Route
                                       Layers layer_a -- the 1st routing conductor),
                                       A*-searching a grid so it dodges every other
                                       shape on that layer; obstacles are grown by
                                       width/2 + clearance (clearance defaults to one
                                       grid step) so the finished copper stays clear.
                                       grid/width default to the canvas's current grid
                                       spacing and path width. boundary=<layer> (or the
                                       Route Layers boundary_layer) keeps the whole trace
                                       inside that layer's geometry. Draws a round-capped
                                       Path in one undo step; errors if the two points
                                       can't be joined at this grid/width. Also
                                       Session.auto_route()

Netlist Extraction (requires the shapely package; also available as
Tools > Extract Netlist...):
    NETLIST RUN <layer1> [layer2 ...] [cell=<name>] [export=<path.csv>] [bonds=0|1]
                                       extract the netlist across the given conductor
                                       layer(s) of the current cell (or cell=<name>),
                                       descending into every placed cell instance
                                       (Sref) same as MASKOP/DRC; each net's members
                                       are identified as RefDes.Pin from Rectangle.pin/
                                       Polygon.pin and the placing Sref's Label (Edit
                                       Selected... "Label", or PAD/RESGEN's built-in
                                       auto-numbering); the Wire Bond Rules... bond
                                       layer is folded in automatically so bonds carry
                                       connectivity through to the die pin (bonds=0 to
                                       opt out); export=<path.csv> also writes a
                                       net,ref_des,pin CSV; also available as
                                       Session.extract_netlist()/export_netlist_csv()

Scripting:
    PY <python source>                run arbitrary Python against the session
                                       (also the fallback for unrecognised input)
    HELP                              show this command reference
"""


class CommandError(LayoutError):
    pass


def _parse_point(tok: str):
    tok = tok.replace(",", " ")
    parts = tok.split()
    if len(parts) != 2:
        raise CommandError(f"expected a point like x,y, got {tok!r}")
    return (float(parts[0]), float(parts[1]))


class CommandInterpreter:
    def __init__(self, session, console, on_change: Optional[Callable[[], None]] = None, view=None):
        self.session = session
        self.console = console
        self.on_change = on_change or (lambda: None)
        self.view = view  # optional LayoutCanvas, for ZOOM/GRID/FIT commands
        self.history = []
        self._hist_index = 0

    def run(self, line: str) -> str:
        line = line.strip()
        if not line:
            return ""
        self.history.append(line)
        self._hist_index = len(self.history)
        try:
            tokens = shlex.split(line)
        except ValueError as e:
            return f"error: {e}"
        if not tokens:
            return ""
        cmd, args = tokens[0].upper(), tokens[1:]
        try:
            handler = getattr(self, f"_cmd_{cmd.lower()}", None)
            if handler is None:
                # Fall through to the Python console for anything else,
                # so raw script lines work directly in the command bar too.
                return self.console.run(line)
            result = handler(args)
            self.on_change()
            return result or "ok"
        except CommandError as e:
            return f"error: {e}"
        except Exception as e:  # pragma: no cover - defensive
            return f"error: {e}"

    def prev_history(self, current: str) -> str:
        if not self.history:
            return current
        self._hist_index = max(0, self._hist_index - 1)
        return self.history[self._hist_index]

    def next_history(self, current: str) -> str:
        if self._hist_index >= len(self.history) - 1:
            self._hist_index = len(self.history)
            return ""
        self._hist_index += 1
        return self.history[self._hist_index]

    # --- cells & shapes -----------------------------------------------
    def _cmd_cell(self, args):
        if not args:
            raise CommandError("usage: CELL <name>")
        name = args[0]
        if name in self.session.library.cells:
            self.session.use_cell(name)
        else:
            self.session.new_cell(name)
        return f"current cell: {name}"

    def _cmd_rect(self, args):
        if len(args) != 3:
            raise CommandError("usage: RECT <layer> <x1,y1> <x2,y2>  (layer: number or name)")
        layer = args[0]
        x1, y1 = _parse_point(args[1])
        x2, y2 = _parse_point(args[2])
        self.session.rect(layer, x1, y1, x2, y2)
        return "rectangle added"

    def _cmd_poly(self, args):
        if len(args) < 4:
            raise CommandError("usage: POLY <layer> <x1,y1> <x2,y2> <x3,y3> ...  (layer: number or name)")
        layer = args[0]
        points = [_parse_point(a) for a in args[1:]]
        self.session.polygon(layer, points)
        return f"polygon added ({len(points)} points)"

    def _cmd_path(self, args):
        if len(args) < 5:
            raise CommandError("usage: PATH <layer> <width> <cap> <x1,y1> <x2,y2> ...  (layer: number or name)")
        layer = args[0]
        width = float(args[1])
        cap = args[2].lower()
        points = [_parse_point(a) for a in args[3:]]
        self.session.path(layer, points, width, cap=cap)
        return f"path added ({len(points)} points)"

    def _cmd_circle(self, args):
        if len(args) < 3:
            raise CommandError(
                "usage: CIRCLE <layer> <cx,cy> <radius> [segments]  "
                "(segments: 8, 16, 32, or 64; default 32)"
            )
        layer = args[0]
        center = _parse_point(args[1])
        radius = float(args[2])
        segments = int(args[3]) if len(args) > 3 else 32
        self.session.circle(layer, center, radius, segments=segments)
        return f"circle added ({segments} segments)"

    def _cmd_text(self, args):
        if len(args) < 3:
            raise CommandError('usage: TEXT <layer> <x,y> "<string>" [height] [font]  (layer: number or name)')
        layer = args[0]
        origin = _parse_point(args[1])
        text = args[2]
        height = int(args[3]) if len(args) > 3 else 10
        font = int(args[4]) if len(args) > 4 else 0
        self.session.text(layer, origin, text, height=height, font=font)
        return "text added"

    def _cmd_sref(self, args):
        if len(args) < 2:
            raise CommandError("usage: SREF <cellname> <x,y> [rotation] [mag] [mirror]")
        cell_name = args[0]
        origin = _parse_point(args[1])
        rotation = float(args[2]) if len(args) > 2 else 0.0
        mag = float(args[3]) if len(args) > 3 else 1.0
        mirror = args[4].lower() in ("1", "true", "yes", "mirror") if len(args) > 4 else False
        self.session.sref(cell_name, origin, rotation=rotation, magnification=mag, x_reflection=mirror)
        return f"sref to {cell_name} added"

    def _cmd_aref(self, args):
        usage = ("usage: AREF <cellname> <x,y> <columns> <rows> <col_spacing> <row_spacing> "
                 "[rotation] [mag] [mirror]")
        if len(args) < 6:
            raise CommandError(usage)
        cell_name = args[0]
        origin = _parse_point(args[1])
        try:
            columns = int(args[2])
            rows = int(args[3])
            col_spacing = float(args[4])
            row_spacing = float(args[5])
        except ValueError:
            raise CommandError(usage)
        rotation = float(args[6]) if len(args) > 6 else 0.0
        mag = float(args[7]) if len(args) > 7 else 1.0
        mirror = args[8].lower() in ("1", "true", "yes", "mirror") if len(args) > 8 else False
        self.session.aref(cell_name, origin, columns=columns, rows=rows,
                           col_spacing=col_spacing, row_spacing=row_spacing,
                           rotation=rotation, magnification=mag, x_reflection=mirror)
        return f"aref to {cell_name} added ({columns}x{rows})"

    def _cmd_delete(self, args):
        if not args or args[0].upper() != "LAST":
            raise CommandError("usage: DELETE LAST")
        cell = self.session.cell
        if cell.shapes:
            cell.shapes.pop()
            return "removed last shape"
        if cell.srefs:
            cell.srefs.pop()
            return "removed last sref"
        if cell.arefs:
            cell.arefs.pop()
            return "removed last aref"
        return "nothing to delete"

    # --- layers -----------------------------------------------------------
    def _cmd_layer(self, args):
        if not args:
            raise CommandError(
                "usage: LAYER <n|name> [color=#rrggbb] [stipple=0-8] [fill=0|1] "
                "[visible=0|1] [name=...]\n"
                "       LAYER ALL visible=0|1   turn every layer off/on at once"
            )
        if args[0].upper() == "ALL":
            if len(args) != 2 or not args[1].startswith("visible="):
                raise CommandError("usage: LAYER ALL visible=0|1")
            visible = args[1].split("=", 1)[1] not in ("0", "false", "no")
            for n in range(len(self.session.library.layers)):
                self.session.set_layer_style(n, visible=visible)
            return f"all layers {'shown' if visible else 'hidden'}"
        if args[0].upper() == "MOVE":
            rest = args[1:]
            cell_name = None
            positional = []
            for a in rest:
                if a.lower().startswith("cell="):
                    cell_name = a.split("=", 1)[1]
                else:
                    positional.append(a)
            if len(positional) != 2:
                raise CommandError("usage: LAYER MOVE <from> <to> [cell=<name>]")
            counts = self.session.remap_layers(
                {positional[0]: positional[1]},
                cell_names=[cell_name] if cell_name else None,
            )
            moved = sum(counts.values())
            scope = f"cell {cell_name!r}" if cell_name else "the whole library"
            if moved == 0:
                return f"nothing on layer {positional[0]} in {scope}"
            return f"moved {moved} shape(s) from layer {positional[0]} to {positional[1]} in {scope}"
        n = self.session.library.layers.resolve(args[0])
        kwargs = {}
        for a in args[1:]:
            if "=" not in a:
                raise CommandError(f"bad option {a!r}, expected key=value")
            k, v = a.split("=", 1)
            if k == "color":
                kwargs["color"] = v
            elif k == "stipple":
                kwargs["stipple"] = int(v)
            elif k == "fill":
                kwargs["fill"] = v not in ("0", "false", "no")
            elif k == "visible":
                kwargs["visible"] = v not in ("0", "false", "no")
            elif k == "name":
                kwargs["name"] = v
            else:
                raise CommandError(f"unknown layer option {k!r}")
        self.session.set_layer_style(n, **kwargs)
        return f"layer {n} updated"

    # --- view ---------------------------------------------------------
    def _cmd_zoom(self, args):
        if self.view is None:
            raise CommandError("ZOOM requires an attached view")
        if not args:
            raise CommandError("usage: ZOOM IN|OUT|FIT|WINDOW <x1,y1> <x2,y2>|<factor>")
        arg = args[0].upper()
        if arg == "IN":
            self.view.zoom_in()
        elif arg == "OUT":
            self.view.zoom_out()
        elif arg == "FIT":
            self.view.fit_view()
        elif arg == "WINDOW":
            if len(args) != 3:
                raise CommandError("usage: ZOOM WINDOW <x1,y1> <x2,y2>")
            x0, y0 = _parse_point(args[1])
            x1, y1 = _parse_point(args[2])
            self.view.zoom_to_window(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        else:
            self.view.zoom(float(args[0]))
        return f"zoom {arg.lower()}"

    def _cmd_fit(self, args):
        """FIT with no arguments: shorthand for ZOOM FIT -- fit the
        whole current cell in the active view. Same operation as the
        View menu/toolbar's Fit button and the F key accelerator."""
        if self.view is None:
            raise CommandError("FIT requires an attached view")
        if args:
            raise CommandError("usage: FIT  (takes no arguments -- see also ZOOM FIT)")
        self.view.fit_view()
        return "fit"

    def _cmd_grid(self, args):
        if self.view is None:
            raise CommandError("GRID requires an attached view")
        usage = "usage: GRID  |  GRID ON|OFF  |  GRID <spacing_mm>"
        if not args:
            # No args: report the current grid state instead of erroring,
            # so GRID with nothing typed is a quick "what's my grid set to
            # right now" query.
            state = "on" if self.view.show_grid else "off"
            return f"grid {state}, spacing {dbu_to_mm(self.view.grid_dbu):g} mm"
        if len(args) != 1:
            raise CommandError(usage)
        arg = args[0].upper()
        if arg in ("ON", "OFF"):
            want_on = arg == "ON"
            if want_on != self.view.show_grid:
                self.view.toggle_grid()
            return f"grid {'on' if want_on else 'off'}"
        try:
            spacing_mm = float(args[0])
        except ValueError:
            raise CommandError(usage)
        try:
            self.view.set_grid_spacing(mm_to_dbu(spacing_mm))
        except GeometryError as e:
            raise CommandError(str(e))
        return f"grid spacing set to {dbu_to_mm(self.view.grid_dbu):g} mm"

    # --- selection edits ------------------------------------------------
    def _cmd_move(self, args):
        if self.view is None:
            raise CommandError("MOVE requires an attached view")
        if len(args) != 1:
            raise CommandError("usage: MOVE <dx,dy>")
        dx, dy = _parse_point(args[0])
        if not self.view.move_selected(dx, dy):
            raise CommandError("nothing selected to move")
        return f"moved by {dx:g},{dy:g}"

    def _cmd_rotate(self, args):
        if self.view is None:
            raise CommandError("ROTATE requires an attached view")
        if len(args) != 1:
            raise CommandError("usage: ROTATE <degrees>")
        try:
            angle = float(args[0])
        except ValueError:
            raise CommandError(f"expected a number of degrees, got {args[0]!r}")
        if not self.view.rotate_selected(angle):
            raise CommandError("nothing selected to rotate")
        return f"rotated by {angle:g} degrees"

    def _cmd_copy(self, args):
        if self.view is None:
            raise CommandError("COPY requires an attached view")
        if len(args) != 1:
            raise CommandError("usage: COPY <dx,dy>")
        dx, dy = _parse_point(args[0])
        if not self.view.copy_selected(dx, dy):
            raise CommandError("nothing selected to copy")
        return f"copied, offset by {dx:g},{dy:g}"

    def _cmd_props(self, args):
        if self.view is None:
            raise CommandError("PROPS requires an attached view")
        from .canvas import describe_selection
        return describe_selection(self.view)

    def _cmd_smash(self, args):
        if self.view is None:
            raise CommandError("SMASH requires an attached view")
        if args:
            raise CommandError("usage: SMASH  (smashes the selected array instance)")
        if not self.view.smash_selected():
            raise CommandError("nothing selected to smash -- select an array instance (AREF) first")
        return "array instance smashed into individual cell instances"

    def _cmd_union(self, args):
        if self.view is None:
            raise CommandError("UNION requires an attached view")
        if args:
            raise CommandError("usage: UNION  (merges the window-selected same-layer "
                                "Polygon/Rectangle shapes into one)")
        if not self.view.merge_selected():
            raise CommandError("nothing suitable selected to merge -- window-select at "
                                "least two Polygon/Rectangle shapes on the same layer first")
        return "merged selected shapes into one polygon"

    def _cmd_makecell(self, args):
        if self.view is None:
            raise CommandError("MAKECELL requires an attached view")
        usage = "usage: MAKECELL <new cell name> <origin x,y> [replace=1 | revision=1]"
        positional, replace, revision = [], False, False
        for a in args:
            k = a.lower()
            if k.startswith("replace="):
                replace = k[len("replace="):] in ("1", "true", "yes")
            elif k.startswith("revision="):
                revision = k[len("revision="):] in ("1", "true", "yes")
            else:
                positional.append(a)
        if len(positional) != 2:
            raise CommandError(usage)
        if replace and revision:
            raise CommandError("MAKECELL: use replace=1 OR revision=1, not both")
        name, origin_text = positional
        origin = _parse_point(origin_text)
        verb = "created"
        if revision and name in self.session.library.cells:
            name = self.session.library._unique_cell_name(name)
            verb = "saved"
        try:
            ok = self.view.create_cell_from_selection(name, origin, replace=replace)
        except LayoutError as e:
            raise CommandError(str(e))
        if not ok:
            raise CommandError("nothing suitable selected to make a cell from -- select a "
                                "whole shape or cell instance (not a single vertex/edge) first")
        if replace:
            verb = "replaced"
        return f"cell {name!r} {verb} from the selection"

    # --- undo/redo --------------------------------------------------------
    def _cmd_undo(self, args):
        if not self.session.undo():
            return "nothing to undo"
        if self.view is not None:
            self.view.selected = None
            self.view.set_cell(self.session.current_cell_name)
        return "undo"

    def _cmd_redo(self, args):
        if not self.session.redo():
            return "nothing to redo"
        if self.view is not None:
            self.view.selected = None
            self.view.set_cell(self.session.current_cell_name)
        return "redo"

    # --- files ----------------------------------------------------------
    def _cmd_save(self, args):
        if not args:
            raise CommandError("usage: SAVE <path.gds> [library_name]")
        name = args[1] if len(args) > 1 else None
        if name:
            self.session.save(args[0], library_name=name)
            return f"saved to {args[0]} as library {name!r}"
        # No library_name given: this is a plain "save the design"
        # (matches File > Save GDS...), so also write the sidecar --
        # technology + attached libraries -- see hlayout.io.sidecar.
        self.session.save_design(args[0])
        return f"saved to {args[0]} (+ technology/attached-libraries sidecar)"

    def _cmd_open(self, args):
        if not args:
            raise CommandError("usage: OPEN <path.gds>")
        from ..io.sidecar import load_design
        loaded = load_design(args[0])
        self.session.library = loaded.library
        self.session.attached_libraries = loaded.attached_libraries
        self.session._attached_paths = loaded.attached_paths
        self.session.attach_errors = loaded.attach_errors
        self.session.had_sidecar = loaded.had_sidecar
        self.session.current_cell_name = self.session.library.top_cell_name
        # A different document was just loaded -- nothing to undo into it.
        self.session.clear_undo_history()
        msg = f"opened {args[0]}"
        if loaded.had_sidecar:
            msg += f" (restored technology + {len(loaded.attached_libraries)} attached library/ies)"
        if loaded.attach_errors:
            msg += f" -- {len(loaded.attach_errors)} attached library/ies failed to restore"
        return msg

    def _cmd_gerber(self, args):
        if not args:
            raise CommandError("usage: GERBER <output_prefix> [cell=<name>]")
        cell_name = None
        prefix = None
        for a in args:
            if a.lower().startswith("cell="):
                cell_name = a.split("=", 1)[1]
            elif prefix is None:
                prefix = a
            else:
                raise CommandError("usage: GERBER <output_prefix> [cell=<name>]")
        if prefix is None:
            raise CommandError("usage: GERBER <output_prefix> [cell=<name>]")
        written, skipped_text = self.session.export_gerber(prefix, cell_name=cell_name)
        if not written:
            return "no visible layers had data to export"
        msg = f"wrote {len(written)} Gerber file(s): " + ", ".join(f"L{n}" for n, _ in written)
        if skipped_text:
            msg += f" ({skipped_text} text shape(s) skipped -- Gerber has no text primitive)"
        return msg

    def _cmd_merge(self, args):
        usage = "usage: MERGE <lib1.gds> <lib2.gds> <lib3.gds> [library_name]"
        if len(args) not in (3, 4):
            raise CommandError(usage)
        lib_name = args[3] if len(args) == 4 else None
        result = self.session.merge_gds(args[0], args[1], args[2], library_name=lib_name)
        msg = f"merged {args[0]} + {args[1]} -> {args[2]} ({result['cells']} cells)"
        if result["renamed"]:
            renames = ", ".join(f"{old} -> {new}" for old, new in result["renamed"].items())
            msg += f"; renamed on collision: {renames}"
        return msg

    # --- technology file --------------------------------------------------
    def _cmd_tech(self, args):
        usage = ("usage: TECH LOAD <path.json> | TECH SAVE <path.json> | "
                 "TECH SET <key> <value> | TECH GET <key> | TECH PARAMS")
        if not args:
            raise CommandError(usage)
        sub = args[0].upper()
        if sub == "LOAD":
            if len(args) != 2:
                raise CommandError("usage: TECH LOAD <path.json>")
            data = self.session.load_technology(args[1])
            return f"loaded technology from {args[1]} ({len(data.get('layers', []))} layers, {len(data.get('params', {}))} params)"
        if sub == "SAVE":
            if len(args) != 2:
                raise CommandError("usage: TECH SAVE <path.json>")
            self.session.save_technology(args[1])
            return f"saved technology to {args[1]}"
        if sub == "SET":
            if len(args) != 3:
                raise CommandError("usage: TECH SET <key> <value>")
            self.session.library.tech_params[args[1]] = args[2]
            return f"tech param {args[1]!r} = {args[2]!r}"
        if sub == "GET":
            if len(args) != 2:
                raise CommandError("usage: TECH GET <key>")
            if args[1] not in self.session.library.tech_params:
                raise CommandError(f"no tech param named {args[1]!r}")
            return f"{args[1]} = {self.session.library.tech_params[args[1]]}"
        if sub == "PARAMS":
            params = self.session.library.tech_params
            if not params:
                return "(no tech params set)"
            return "\n".join(f"{k} = {v}" for k, v in params.items())
        raise CommandError(usage)

    # --- attached libraries ---------------------------------------------
    def _cmd_attach(self, args):
        if not args:
            raise CommandError("usage: ATTACH <path.gds>")
        lib = self.session.attach_library(args[0])
        idx = len(self.session.attached_libraries) - 1
        return f"attached library {idx}: {lib.name!r} ({len(lib.cells)} cells)"

    def _cmd_libs(self, args):
        libs = self.session.list_attached_libraries()
        if not libs:
            return "no attached libraries"
        return "\n".join(f"[{l['index']}] {l['name']} ({l['cells']} cells) - {l['path']}" for l in libs)

    def _cmd_liblist(self, args):
        source = args[0] if args else "current"
        if source.lower() != "current":
            source = int(source)
        else:
            source = "current"
        cells = self.session.list_library(source)
        if not cells:
            return "(no cells)"
        return "\n".join(f"{c['name']}: {c['shapes']} shapes, {c['srefs']} srefs" for c in cells)

    def _cmd_find(self, args):
        if not args:
            raise CommandError("usage: FIND <substring>")
        query = " ".join(args)
        matches = self.session.find_cells(query)
        if not matches:
            return f"no cells matching {query!r}"
        return "\n".join(f"{m['name']} (source: {m['source']})" for m in matches)

    def _cmd_place(self, args):
        if len(args) < 2:
            raise CommandError(
                "usage: PLACE <cellname> <x,y> [source=current|0|1] [rotation] [mag] [mirror]"
            )
        cell_name = args[0]
        origin = _parse_point(args[1])
        source = None
        positional = []
        for a in args[2:]:
            if a.lower().startswith("source="):
                val = a.split("=", 1)[1]
                source = "current" if val.lower() == "current" else int(val)
            else:
                positional.append(a)
        rotation = float(positional[0]) if len(positional) > 0 else 0.0
        mag = float(positional[1]) if len(positional) > 1 else 1.0
        mirror = positional[2].lower() in ("1", "true", "yes", "mirror") if len(positional) > 2 else False
        self.session.place_cell(cell_name, origin, source=source, rotation=rotation,
                                 magnification=mag, x_reflection=mirror)
        if self.view is not None:
            # Make sure the newly placed cell's contents are actually
            # visible right away rather than possibly landing outside
            # the current view.
            self.view.fit_view()
        return f"placed {cell_name}"

    def _cmd_resgen(self, args):
        usage = ("usage: RESGEN <x,y> <body_width> <body_length> [rotation=<deg>] "
                 "[ink=<rsh>] [ref=<designator>] [a=<dbu>] [b=<dbu>] [c=<dbu>] "
                 "[d=<dbu>] [e=<dbu>] [rlayer=<n|name>] [clayer=<n|name>] "
                 "[glayer=<n|name>] [kolayer=<n|name>] [dlayer=<n|name> (ignored)]")
        if len(args) < 3:
            raise CommandError(usage)
        origin = _parse_point(args[0])
        try:
            body_width = float(args[1])
            body_length = float(args[2])
        except ValueError:
            raise CommandError(usage)
        kwargs = {}
        rotation = 0.0
        for a in args[3:]:
            if "=" not in a:
                raise CommandError(usage)
            k, v = a.split("=", 1)
            k = k.lower()
            if k == "rotation":
                rotation = float(v)
            elif k == "ink":
                kwargs["ink_rsh"] = float(v)
            elif k == "ref":
                kwargs["ref_des"] = v
            elif k == "a":
                kwargs["overlap_a"] = float(v)
            elif k == "b":
                kwargs["pad_b"] = float(v)
            elif k == "c":
                kwargs["space_c"] = float(v)
            elif k == "d":
                kwargs["glaze_oversize"] = float(v)
            elif k == "e":
                kwargs["keepout_oversize"] = float(v)
            elif k == "rlayer":
                kwargs["resistor_layer"] = self.session.library.layers.resolve(v)
            elif k == "clayer":
                kwargs["conductor_layer"] = self.session.library.layers.resolve(v)
            elif k == "glayer":
                kwargs["glaze_layer"] = self.session.library.layers.resolve(v)
            elif k == "dlayer":
                kwargs["doc_layer"] = self.session.library.layers.resolve(v)
            elif k == "kolayer":
                kwargs["keepout_layer"] = self.session.library.layers.resolve(v)
            else:
                raise CommandError(f"unknown RESGEN option {k!r}")
        device_name = self.session.generate_resistor(
            origin[0], origin[1], body_width, body_length, rotation=rotation, **kwargs)
        return (f"generated resistor cell {device_name!r}, placed at {origin} "
                f"(W={body_width}, L={body_length})")

    # --- mask ops ---------------------------------------------------------
    def _cmd_maskop(self, args):
        usage = ("usage: MASKOP OVERSIZE|UNDERSIZE <layer_a> result=<layer> amount=<dbu> "
                 "[cell=<name>] [flatten=0|1] [simplify=<dbu>]\n"
                 "       MASKOP AND|OR|NOT|XOR <layer_a> <layer_b> result=<layer> "
                 "[cell=<name>] [flatten=0|1] [simplify=<dbu>]")
        if len(args) < 2:
            raise CommandError(usage)
        op = args[0].upper()
        if op not in MASK_OPS:
            raise CommandError(f"{usage}\n(op must be one of {', '.join(MASK_OPS)})")
        positional = []
        kwargs = {}
        for a in args[1:]:
            if "=" in a:
                k, v = a.split("=", 1)
                k = k.lower()
                if k == "result":
                    kwargs["result_layer"] = v
                elif k == "amount":
                    kwargs["amount"] = float(v)
                elif k == "cell":
                    kwargs["cell_names"] = [v]
                elif k == "flatten":
                    kwargs["flatten"] = v not in ("0", "false", "no")
                elif k == "simplify":
                    kwargs["simplify"] = float(v)
                else:
                    raise CommandError(f"unknown MASKOP option {k!r}")
            else:
                positional.append(a)
        if "result_layer" not in kwargs:
            raise CommandError(f"{usage}\n(result=<layer> is required)")
        result_layer = kwargs.pop("result_layer")
        if op in ("OVERSIZE", "UNDERSIZE"):
            if len(positional) != 1:
                raise CommandError(usage)
            layer_a = positional[0]
            if "amount" not in kwargs:
                raise CommandError(f"{usage}\n(amount=<dbu> is required for {op})")
            written = self.session.mask_op(op, layer_a, result_layer, **kwargs)
        else:
            if len(positional) != 2:
                raise CommandError(usage)
            layer_a, layer_b = positional
            written = self.session.mask_op(op, layer_a, result_layer, layer_b=layer_b, **kwargs)
        total = sum(written.values())
        if total == 0:
            return f"{op} produced no output on layer {result_layer}"
        # A mask op run from the Command Bar/Script Console is watched on
        # the canvas the same way the Mask Operations dialog is, so real
        # output written to a layer whose visibility happens to be off
        # would otherwise look identical to writing nothing at all. Turn
        # it on rather than leaving a correct result silently hidden.
        resolved_layer = self.session.library.layers.resolve(result_layer)
        layer_obj = self.session.library.layers[resolved_layer]
        made_visible = False
        if not layer_obj.visible:
            layer_obj.visible = True
            made_visible = True
        suffix = " (layer visibility turned on)" if made_visible else ""
        return f"{op} wrote {total} polygon(s) to layer {result_layer}{suffix}"

    # --- DRC (Design Rule Check) -------------------------------------------
    def _cmd_drcrule(self, args):
        from ..core.drc import DRC_RULE_KINDS, TWO_LAYER_KINDS, DrcRule
        from ..io.techfile import load_drc_rules, save_drc_rules

        usage = (
            "usage: DRCRULE ADD <kind> <layer_a> [layer_b] value=<n> [name=<name>]\n"
            "       DRCRULE LIST\n"
            "       DRCRULE REMOVE <index>\n"
            "       DRCRULE CLEAR\n"
            f"(kind: {', '.join(DRC_RULE_KINDS)}; layer_b is required for "
            f"{', '.join(TWO_LAYER_KINDS)})"
        )
        if not args:
            raise CommandError(usage)
        sub = args[0].upper()
        rules = load_drc_rules(self.session.library)

        if sub == "LIST":
            if not rules:
                return "no DRC rules saved"
            lines = [f"{i + 1}. {DrcRule.from_dict(r).label}" for i, r in enumerate(rules)]
            return "\n".join(lines)

        if sub == "CLEAR":
            save_drc_rules(self.session.library, [])
            return f"cleared {len(rules)} DRC rule(s)"

        if sub == "REMOVE":
            if len(args) != 2:
                raise CommandError("usage: DRCRULE REMOVE <index>")
            try:
                idx = int(args[1]) - 1
            except ValueError:
                raise CommandError("usage: DRCRULE REMOVE <index>")
            if not (0 <= idx < len(rules)):
                raise CommandError(f"no rule #{args[1]} (have {len(rules)})")
            removed = rules.pop(idx)
            save_drc_rules(self.session.library, rules)
            return f"removed rule: {DrcRule.from_dict(removed).label}"

        if sub == "ADD":
            resolve = self.session.library.layers.resolve
            positional = []
            kwargs = {}
            for a in args[1:]:
                if "=" in a:
                    k, v = a.split("=", 1)
                    k = k.lower()
                    if k == "value":
                        kwargs["value"] = float(v)
                    elif k == "name":
                        kwargs["name"] = v
                    else:
                        raise CommandError(f"unknown DRCRULE option {k!r}")
                else:
                    positional.append(a)
            if len(positional) < 2:
                raise CommandError(usage)
            kind = positional[0].upper()
            if kind not in DRC_RULE_KINDS:
                raise CommandError(f"{usage}\n(unknown kind {positional[0]!r})")
            layer_a = resolve(positional[1])
            layer_b = None
            if kind in TWO_LAYER_KINDS:
                if len(positional) != 3:
                    raise CommandError(f"{usage}\n({kind} requires layer_b)")
                layer_b = resolve(positional[2])
            elif len(positional) != 2:
                raise CommandError(usage)
            if "value" not in kwargs:
                raise CommandError(f"{usage}\n(value=<n> is required)")
            rule = DrcRule(kind=kind, layer_a=layer_a, layer_b=layer_b, **kwargs)
            rules.append(rule.to_dict())
            save_drc_rules(self.session.library, rules)
            return f"added rule #{len(rules)}: {rule.label}"

        raise CommandError(usage)

    def _cmd_netlist(self, args):
        usage = ("usage: NETLIST RUN <layer1> [layer2 ...] [cell=<name>] "
                 "[export=<path.csv>] [bonds=0|1]")
        if not args or args[0].upper() != "RUN":
            raise CommandError(usage)
        cell_name = None
        export_path = None
        include_bonds = True
        layers = []
        for a in args[1:]:
            if a.lower().startswith("cell="):
                cell_name = a.split("=", 1)[1]
            elif a.lower().startswith("export="):
                export_path = a.split("=", 1)[1]
            elif a.lower().startswith("bonds="):
                include_bonds = a.split("=", 1)[1] not in ("0", "false", "no")
            else:
                layers.append(a)
        if not layers:
            raise CommandError(f"{usage}\n(at least one conductor layer is required)")
        nets = self.session.extract_netlist(layers, cell_name=cell_name,
                                            include_bonds=include_bonds)
        if export_path:
            self.session.export_netlist_csv(nets, export_path)
        lines = [f"netlist: {len(nets)} net(s)"]
        for net in nets:
            members = ", ".join(p.label() for p in net.pins) or "(no pins)"
            extra = f" [{net.unresolved_count} unresolved]" if net.unresolved_count else ""
            lines.append(f"  {net.id}: {members}{extra}")
        if export_path:
            lines.append(f"exported to {export_path}")
        return "\n".join(lines)

    def _cmd_drc(self, args):
        usage = "usage: DRC RUN [cell=<name>] [error=<layer>]"
        if not args or args[0].upper() != "RUN":
            raise CommandError(usage)
        kwargs = {}
        for a in args[1:]:
            if "=" not in a:
                raise CommandError(usage)
            k, v = a.split("=", 1)
            k = k.lower()
            if k == "cell":
                kwargs["cell_names"] = [v]
            elif k == "error":
                kwargs["error_layer"] = v
            else:
                raise CommandError(f"unknown DRC option {k!r}")
        violations = self.session.run_drc(**kwargs)
        if not violations:
            return "DRC: no violations found"
        by_rule = {}
        for v in violations:
            by_rule.setdefault(v.rule.label, 0)
            by_rule[v.rule.label] += 1
        lines = [f"DRC: {len(violations)} violation(s) found"]
        for label, count in by_rule.items():
            lines.append(f"  {count} x {label}")
        return "\n".join(lines)

    def _cmd_pin(self, args):
        usage = (
            "usage: PIN LIST [cell=<name>]\n"
            "       PIN SET <index> <pin> [cell=<name>]"
        )
        if not args:
            raise CommandError(usage)
        sub = args[0].upper()
        rest = args[1:]
        cell_name = None
        positional = []
        for a in rest:
            if a.lower().startswith("cell="):
                cell_name = a.split("=", 1)[1]
            else:
                positional.append(a)

        if sub == "LIST":
            if positional:
                raise CommandError(usage)
            pins = self.session.list_pins(cell_name=cell_name)
            if not pins:
                return "no pin-capable shapes (Rectangle/Polygon) in this cell"
            lines = [f"{p['index']}: layer {p['layer']}, pin={p['pin'] or '(unset)'}, bbox={p['bbox']}"
                     for p in pins]
            return "\n".join(lines)

        if sub == "SET":
            if len(positional) != 2:
                raise CommandError(usage)
            try:
                index = int(positional[0])
            except ValueError:
                raise CommandError(usage)
            pin = positional[1]
            self.session.set_pin(index, pin, cell_name=cell_name)
            return f"set shape {index} pin={pin!r}"

        raise CommandError(usage)

    def _cmd_dielectric(self, args):
        usage = (
            "usage: DIELECTRIC [layer=<n>] [clearance=<dbu>] [margin=<dbu>] "
            "[keepout=<n>[,<n>...]] [cell=<name>]     -- generate the fill\n"
            "       DIELECTRIC CLEAR [layer=<n>] [all=1] [cell=<name>]      -- remove it\n"
            "  (draws the fill from datatype-0 geometry on the dielectric layer:\n"
            "   the enclosing shape is the boundary, everything else is a hole)"
        )
        if args and args[0].upper() in ("CLEAR", "REMOVE"):
            kwargs = {}
            for a in args[1:]:
                if "=" not in a:
                    raise CommandError(usage)
                k, v = a.split("=", 1)
                k = k.lower()
                if k == "layer":
                    kwargs["layer"] = v
                elif k == "cell":
                    kwargs["cell_name"] = v
                elif k == "all":
                    kwargs["all_datatypes"] = v not in ("0", "false", "no")
                else:
                    raise CommandError(f"unknown DIELECTRIC CLEAR option {k!r}")
            removed = self.session.clear_dielectric(**kwargs)
            return f"removed {removed} dielectric polygon(s)"

        kwargs = {}
        for a in args:
            if "=" not in a:
                raise CommandError(usage)
            k, v = a.split("=", 1)
            k = k.lower()
            if k == "layer":
                kwargs["layer"] = v
            elif k == "clearance":
                kwargs["clearance"] = float(v)
            elif k == "margin":
                kwargs["margin"] = float(v)
            elif k == "keepout":
                kwargs["keepout_layers"] = [t for t in v.split(",") if t]
            elif k == "cell":
                kwargs["cell_name"] = v
            else:
                raise CommandError(f"unknown DIELECTRIC option {k!r}")
        written = self.session.generate_dielectric(**kwargs)
        return f"dielectric fill: {written} polygon(s) written"

    def _cmd_bond(self, args):
        usage = (
            "usage: BOND <x1,y1> <x2,y2> [layer=<n|name>] [width=<dbu>] "
            "[pad=<dbu>] [min=<dbu>] [max=<dbu>] [cell=<name>]\n"
            "  draws a wire bond (O------O) between two pads on the bond layer;\n"
            "  span must be within min/max and must not cross an existing bond.\n"
            "  Bond layer/defaults come from Setup > Wire Bond Rules..."
        )
        if len(args) < 2:
            raise CommandError(usage)
        p1 = _parse_point(args[0])
        p2 = _parse_point(args[1])
        kwargs = {}
        for a in args[2:]:
            if "=" not in a:
                raise CommandError(usage)
            k, v = a.split("=", 1)
            k = k.lower()
            if k == "layer":
                kwargs["layer"] = v
            elif k == "width":
                kwargs["width"] = int(float(v))
            elif k == "pad":
                kwargs["pad"] = int(float(v))
            elif k == "min":
                kwargs["min_length"] = float(v)
            elif k == "max":
                kwargs["max_length"] = float(v)
            elif k == "cell":
                kwargs["cell_name"] = v
            else:
                raise CommandError(f"unknown BOND option {k!r}")
        self.session.wire_bond([p1, p2], **kwargs)
        import math as _m
        span = _m.hypot(p2[0] - p1[0], p2[1] - p1[1])
        return f"wire bond {p1} -> {p2}  (span {span:.0f} DBU)"

    def _cmd_autoroute(self, args):
        usage = (
            "usage: AUTOROUTE <x1,y1> <x2,y2> [layer=<n|name>] [grid=<dbu>] "
            "[width=<dbu>] [clearance=<dbu>] [margin=<dbu>] [boundary=<n|name>] "
            "[cell=<name>]\n"
            "  routes a clear orthogonal trace between the two points on the\n"
            "  routing layer (default: Route Layers layer_a), dodging every\n"
            "  other shape on that layer. grid/width default to the canvas's\n"
            "  current grid spacing and path width. boundary=<layer> (or the\n"
            "  Route Layers boundary_layer) keeps the trace inside that layer's\n"
            "  geometry."
        )
        if len(args) < 2:
            raise CommandError(usage)
        p1 = _parse_point(args[0])
        p2 = _parse_point(args[1])
        kwargs = {}
        if self.view is not None:
            kwargs["grid"] = int(self.view.grid_dbu)
            kwargs["width"] = int(self.view.path_width)
        for a in args[2:]:
            if "=" not in a:
                raise CommandError(usage)
            k, v = a.split("=", 1)
            k = k.lower()
            if k == "layer":
                kwargs["layer"] = v
            elif k == "grid":
                kwargs["grid"] = int(float(v))
            elif k == "width":
                kwargs["width"] = int(float(v))
            elif k == "clearance":
                kwargs["clearance"] = float(v)
            elif k == "margin":
                kwargs["margin"] = float(v)
            elif k == "boundary":
                kwargs["boundary_layer"] = v
            elif k == "cell":
                kwargs["cell_name"] = v
            else:
                raise CommandError(f"unknown AUTOROUTE option {k!r}")
        if "grid" not in kwargs:
            raise CommandError(f"{usage}\n(no canvas attached -- pass grid= and width=)")
        path = self.session.auto_route(p1, p2, **kwargs)
        return (f"auto-routed {p1} -> {p2}: {len(path.points)}-point trace on "
                f"layer {path.layer}")

    def _cmd_via(self, args):
        usage = ("usage: VIA <layer1> <layer2> [shape=square|round] [size=<dbu>] "
                 "[name=<cellname>] [dielectric_layer=<n>] [dielectric_window=<dbu>] "
                 "[index=0|1] [overwrite=0|1]")
        if len(args) < 2:
            raise CommandError(usage)
        positional = []
        kwargs = {}
        for a in args:
            if "=" in a:
                k, v = a.split("=", 1)
                k = k.lower()
                if k == "shape":
                    kwargs["shape"] = v
                elif k == "size":
                    kwargs["size"] = float(v)
                elif k == "name":
                    kwargs["name"] = v
                elif k == "dielectric_layer":
                    kwargs["dielectric_layer"] = v
                elif k == "dielectric_window":
                    kwargs["dielectric_window"] = float(v)
                elif k == "index":
                    kwargs["index"] = int(v)
                elif k == "overwrite":
                    kwargs["overwrite"] = v not in ("0", "false", "no")
                else:
                    raise CommandError(f"unknown VIA option {k!r}")
            else:
                positional.append(a)
        if len(positional) != 2:
            raise CommandError(usage)
        name, path = self.session.create_via_cell(positional[0], positional[1], **kwargs)
        return f"created via cell {name!r}, saved to {path}"

    def _cmd_pad(self, args):
        usage = ("usage: PAD <name> <conductor_layer> <paste_layer> <length_mm> <width_mm> "
                 "<margin_mm> [rows=1] [cols=1] [pitch_x=<mm>] [pitch_y=<mm>] "
                 "[index=0|1] [overwrite=0|1]")
        if len(args) < 6:
            raise CommandError(usage)
        positional = []
        kwargs = {}
        for a in args:
            if "=" in a:
                k, v = a.split("=", 1)
                k = k.lower()
                if k == "rows":
                    kwargs["rows"] = int(v)
                elif k == "cols":
                    kwargs["cols"] = int(v)
                elif k == "pitch_x":
                    kwargs["pitch_x"] = mm_to_dbu(float(v))
                elif k == "pitch_y":
                    kwargs["pitch_y"] = mm_to_dbu(float(v))
                elif k == "index":
                    kwargs["index"] = int(v)
                elif k == "overwrite":
                    kwargs["overwrite"] = v not in ("0", "false", "no")
                else:
                    raise CommandError(f"unknown PAD option {k!r}")
            else:
                positional.append(a)
        if len(positional) != 6:
            raise CommandError(usage)
        pad_name, conductor_layer, paste_layer, length_mm, width_mm, margin_mm = positional
        name, path, pad_count = self.session.create_pad_cell(
            pad_name, conductor_layer, paste_layer,
            mm_to_dbu(float(length_mm)), mm_to_dbu(float(width_mm)), mm_to_dbu(float(margin_mm)),
            **kwargs,
        )
        return f"created pad cell {name!r} ({pad_count} pad(s)), saved to {path}"

    # --- scripting / help -------------------------------------------------
    def _cmd_py(self, args):
        code_str = " ".join(args)
        return self.console.run(code_str)

    def _cmd_help(self, args):
        return COMMAND_REFERENCE
