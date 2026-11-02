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

import os
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
    PAN <dx,dy>                        move the view by dx,dy DBU (+x right, +y up)
                                       without changing the zoom
    PAN TO <x,y>                       centre the view on point x,y (DBU)
    PAN LEFT | RIGHT | UP | DOWN [f]   step the view by fraction f of the visible
                                       width/height (default 0.25) -- same as
                                       Ctrl+arrow keys / View > Pan Step
                                       (View > Pan, or P on the canvas, is the
                                       drag-to-pan tool)
    REFDES PIN1 [<refdes>|ALL] [gap=<dbu>]
                                       put a placed part's ref-des beside its
                                       pin 1 (outside the pin row); no ref-des
                                       = every connector/header (J, P, X, CN,
                                       CON, HDR). Done automatically when a
                                       connector is placed/labelled with one
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
    GERBER <output_prefix> [cell=<name>] [scale=<n>]
                                       export Gerber RS-274X -- one flattened file per
                                       visible layer with data, named
                                       <output_prefix>_L<NN>_<layer name>.gbr;
                                       cell defaults to the current cell; scale=5 or
                                       scale=10 plots 5X / 10X artwork (coordinates
                                       and path widths enlarged about the origin;
                                       default 1 = actual size)
    FREECAD <path.FCMacro> [cell=<name>]
                                       write a FreeCAD macro that rebuilds the design
                                       as a 3D solid stack (one coloured solid per
                                       printed film at its fired-thickness elevation,
                                       wire bonds as raised loops, components as
                                       fixed-height bodies or substituted models);
                                       run it inside FreeCAD. Needs shapely
    PRINT [cell=<name>] [out=<path.html>] [scale=<n>] [pinlayers=<n,n,...>]
                                       render the design (default: the current cell)
                                       as a printable HTML page -- a legend plus an
                                       SVG drawing of its visible geometry -- and hand
                                       it to the system's print flow (or just open it
                                       if that fails, so Ctrl+P still works). scale
                                       defaults to auto (a design under ~150mm across
                                       is scaled up to about that size instead of
                                       printing as a speck at "100%"); pass 1 to force
                                       true physical size. pinlayers= draws a numbered
                                       label at every pin on those layers -- e.g. a bare
                                       die's own pad layer, for wire-bonding reference --
                                       deliberately opt-in and layer-scoped, since an
                                       ordinary part's own 2-terminal pins are ALSO
                                       pin-tagged and would otherwise clutter every part.
                                       Same as File > Print...
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
    CELLSTAMPS [current|0|1]          created/modified date/time of every cell, design + attached libraries
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
    VIA DELETE <x,y> [tol=<dbu>] [layers=<a,b,...>] [cell=<name>]
                                       remove the via at <x,y> (pads on every layer +
                                       dielectric window sharing that centre); tracks
                                       ending on it stay. Also Edit > Delete Via and
                                       Session.delete_via()
    VIA FIX [cell=<name>]              redraw every via to the design's one via: pad from
                                       the Autoroute signal/power via, window = pad + 2 x
                                       the Dielectric via window margin; the Route tool's
                                       via cell too. Also Tools > Fix Vias...
    FREEROUTE EXPORT <file.dsn> | IMPORT <file.ses> | RUN [passes=<n>] [keep=0|1]
              [fresh=0|1] [layers=auto|single|two] [finger=<um>] [fingerlayer=1|2]
              [optpasses=<n>] [optstop=<%>] [directions=hv|vh|off] [viaopt=0|1]
              [viacost=<n>] [viaflip=<um>] [shorten=0|1] [dejog=0|1] [maxjog=<um>]
              |   FREEROUTE VIAOPT [viaflip=<um>]   |   FREEROUTE SHORTEN   |   FREEROUTE DEJOG
                                       Freerouting autorouter round trip: write a
                                       Specctra DSN (route layers, pads, net names,
                                       existing routing), read a session back (tracks,
                                       vias + dielectric windows, fill regenerated), or
                                       RUN both with Freerouting in between (blocks
                                       until done -- Tools > Autoroute (Freerouting)...
                                       runs it in the background). fresh=1 rips up
                                       all tracks/vias first; layers= picks one or both
                                       conductors; chip-and-wire: every wire bond's
                                       substrate end becomes a bond finger (finger= um)
                                       on the 1st or 2nd conductor (fingerlayer=);
                                       passes= routing passes (0 = until done),
                                       optpasses= optimizer passes (0 = off),
                                       optstop= stop optimizing below this % gain;
                                       directions= one track direction per conductor
                                       (hv: 1st horizontal, 2nd vertical; off = none),
                                       viaopt=1 takes out every via it can after the
                                       route (viaflip= caps the track moved per via),
                                       viacost= the router's cost of a via.
                                       FREEROUTE VIAOPT runs just the via pass;
                                       shorten=1 (default) then straightens detours and
                                       pulls corners tight, FREEROUTE SHORTEN runs just that;
                                       dejog=1 (default) then lines up jogs -- short
                                       sideways steps up to maxjog= um (default 1000) --
                                       FREEROUTE DEJOG runs just that
    AUTOPLACE RUN|IMPROVE <netlist> [overrides=<csv>] [effort=] [seed=] [thermal=] [grid=]
                                       place the netlist's parts (KiCad .net, CSV or
                                       Tango) for the shortest weighted net length;
                                       IMPROVE refines the current placement.
                                       AUTOPLACE WEIGHT <net> <w> sets a net weight.
                                       Also Tools > Auto Place... and
                                       Session.auto_place()
    SLI [ON|OFF]                       straight-line interconnects: total and longest
                                       nets; ON/OFF shows them (View > Straight-Line
                                       Interconnects). Session.sli()
    UNUSEDBONDS [LIST|REMOVE]          bonds/fingers on die pads no net uses: list / take off
    UNROUTED [<net>|NEXT|PREV|CLEAR]   list unrouted nets / zoom to one and show its SLI
    IOPADS [PLAN|APPLY] [w=<um>] [l=<um>] [inset=<um>] [csv=<file>]
                                       optimise the module pinout: each I/O net onto the
                                       header pin / I/O pad with the shortest, uncrossed
                                       way to its dice; APPLY draws the pads at the header
                                       bond landings (size from Technology Properties,
                                       I/O Pads tab, unless w=/l=/inset= are given) and
                                       renames the nets. Also Tools >
                                       Assign I/O Pads... and Session.assign_io_pads()
    IOPADS BOND [all=1] [pins=<n,n,...>] [layer=<n>] [w=] [l=] [inset=] [margin=]
                                       bond the header pins: an I/O pad at the nearest
                                       substrate edge (outer end margin= um in) and a
                                       header bond for each pin with an I/O net (all=1:
                                       every pin). Also Tools > Bond Header Pins... and
                                       Session.bond_header()
    SHORTS                             copper joining two or more named nets (a short
                                       circuit spacing DRC can't see) and where they meet.
                                       Session.find_shorts()
    RIPUP <x1,y1> <x2,y2> [route=0|1] [tol=<dbu>]
                                       rip up the track between two points of one net
                                       (through T-junctions and vias; a via left with
                                       one side bare goes too), then start routing
                                       again from the first point to the ringed second
                                       (route=0: just rip up). Also Edit > Rip Up and
                                       Reroute and Session.rip_up_between()
    SOLIDCRAFT [window=0|1] [export=<file.step|3mf|stl|obj>]
                                       build the current cell as a 3D model in
                                       SolidCraft through its MCP server (window=1:
                                       open it and build in front of you); export=
                                       also writes a 3D file. File > 3D View in
                                       SolidCraft... does it in the background
    HYBRID IMPORT <file.routed.json> [cell=<name>] [fill=0|1] | HYBRID BONDS [<file.csv>]
                                       chip-and-wire router result: IMPORT draws its
                                       substrate, dies, parts, pads, traces, crossover
                                       windows (+ dielectric fill) and wire bonds in a
                                       new cell and keeps its bond diagram; BONDS lists
                                       the bond diagram or writes it as CSV. Also
                                       Session.import_routed_hybrid()
    MOVEFINGER <pad|x,y> <x,y>         move a bond finger / jumper land of an imported
                                       routed hybrid to a new centre; its wires follow
                                       and are checked against the bond rules. Also
                                       Edit > Move Bond Finger
    RESIZE <refdes> [length=<um>] [width=<um>] [preview=1]
                                       re-size an imported printed resistor; its value
                                       follows R = Rs x L / W. Also Edit > Resize Resistor
    BONDINFO <x,y>                     the wire bond nearest x,y: length, wire, loop
                                       height, departure angles
    BONDTABLE <cell_name> <layer> [origin=<x,y>] [rowheight=<dbu>] [textheight=<dbu>]
                                       draw the stored bond diagram as a table cell
                                       (SEQ, WIRE, NET, FIRST/SECOND BOND, LENGTH,
                                       WIRE, LOOP); also in the assembly drawing
    NETNAMES IMPORT <file.csv> [merge=1] | NETNAMES CLEAR | NETNAMES
                                       the design net names Highlight Net reports: a CSV
                                       with net + pad (or ref + pin) columns
    HILITE <x,y> | <RefDes.Pin> | <net name> | OFF [layers=<a,b,...>] [cell=<name>]
                                       highlight every pad and track on the net under
                                       <x,y> or of pad RefDes.Pin (module I/O pads are
                                       IO.<pin>); layers connect only through via
                                       windows in the dielectric. OFF clears it. Also
                                       Edit > Highlight Net and Session.highlight_net()
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
                                       Technology Properties, Resistors tab (the technology
                                       file) when omitted -- see that dialog for what
                                       A (overlap), B (pad length), C (side space),
                                       and D (glaze oversize, on all 4 sides) mean.
                                       dlayer= is still accepted but ignored.
    RESTABLE <cell_name> <layer> [origin=<x,y>] [rowheight=<dbu>] [textheight=<dbu>]
             [linelayer=<n|name>] [linewidth=<dbu>] [title=<text>] [titleinset=<dbu>]
             [overwrite=0|1]
                                       build a resistor BOM table -- REF DES / VALUE
                                       (OHMS) / TOL% / AIM VALUE / INK (OHMS/SQ) -- as
                                       a grid of Text shapes (every cell centred within
                                       its own column), PLUS ruled row/column border
                                       lines so every cell reads as a real bordered box,
                                       all in its own cell, generated
                                       fresh from the saved Thick-Film Resistor
                                       Designer rows (the same data its own table
                                       shows); "aim value" uses that Designer's own
                                       formula (value x (aim_pct% + tolerance%)) so it
                                       always matches whatever it used to pick each
                                       ink. linelayer defaults to the same layer as the
                                       text; linewidth defaults to a thin 20 DBU rule.
                                       title (default "RESISTOR TABLE"; quote it if it
                                       has spaces, or pass title="" to omit it) adds one
                                       more row above the column headers, spanning the
                                       full table width as a single merged cell (no
                                       column dividers through it), horizontally centred
                                       by default -- pass titleinset=<dbu> to left-align
                                       it at that inset instead. Generic and
                                       re-runnable -- overwrite defaults to 1, so
                                       running it again after adding/editing resistors
                                       just rebuilds the cell fresh; place it (SREF)
                                       wherever needed, e.g. alongside the
                                       assembly drawing for PRINT. Also available as
                                       Session.generate_resistor_table()
    PINTABLE <cell_name> <layer> <pin_count> [pergroup=<n>] [origin=<x,y>]
             [rowheight=<dbu>] [textheight=<dbu>] [linelayer=<n|name>]
             [linewidth=<dbu>] [groupgap=<dbu>] [title=<text>] [titleinset=<dbu>]
             [overwrite=0|1]
                                       build a pinout reference table -- one PIN # /
                                       PIN FUNCTION column-pair per group of pergroup
                                       pins (default 10), groups placed side by side
                                       left to right (e.g. 20 pins -> 2 groups, 50 pins
                                       -> 5 groups); pin_count must be a whole multiple
                                       of pergroup. FUNCTION starts blank on every pin --
                                       fill it in by hand afterward (there's no
                                       Session.generate_pin_function_table() functions=
                                       mapping exposed here; pass one directly in a
                                       script if you want pins pre-filled). Every cell's
                                       text is centred within its own column, same as
                                       RESTABLE; groupgap (default 0 -- groups sit edge
                                       to edge) is the empty space between one group's
                                       own bordered box and the next, if any -- each
                                       group is still its own separate mini-table. title
                                       (default "PIN FUNCTION TABLE") spans the full
                                       width of every group plus the gaps between them,
                                       centred by default -- same title/titleinset/
                                       overwrite conventions as RESTABLE. Also available
                                       as Session.generate_pin_function_table()
    SHEET <cell_name> <layer> [size=A4|A3] [orientation=portrait|landscape]
          [origin=<x,y>] [margin=<mm>] [titleheight=<mm>] [textheight=<mm>]
          [linewidth=<mm>] [linelayer=<n|name>] [title=<text>] [dwgno=<text>]
          [issue=<text>] [drawnby=<text>] [drawndate=<text>] [checkedby=<text>]
          [checkeddate=<text>] [approvedby=<text>] [approveddate=<text>]
          [overwrite=0|1]
                                       build a standard drawing sheet -- a ruled
                                       border frame sized to a real ISO paper size
                                       (size defaults to A3; A4 also fits, A2 and up
                                       don't -- hlayout's database is capped at
                                       500x500mm), orientation defaults to portrait,
                                       with a standard title block ruled across the
                                       full width of the bottom of the frame: TITLE
                                       (full width), then DWG NO | ISSUE, then
                                       DRAWN BY | DATE, CHECKED BY | DATE, and
                                       APPROVED BY | DATE. margin/titleheight/
                                       textheight/linewidth are real millimetres
                                       (unlike RESTABLE/PINTABLE's DBU), converted
                                       internally to match hlayout's own DBU grid.
                                       Every title-block field is blank by default
                                       (reads "LABEL:") -- pass any of the text
                                       options above to pre-fill it (quote a value
                                       with spaces). The area above the title block,
                                       inside the margin, is left empty for the
                                       actual drawing content. Generic and
                                       re-runnable -- overwrite defaults to 1, so
                                       re-running it (e.g. with updated title-block
                                       text) just rebuilds the cell fresh; place it
                                       (SREF) wherever needed, e.g. behind the
                                       assembly drawing, for PRINT. Also available as
                                       Session.generate_drawing_sheet()
    ASSEMBLY [<key>=<value> ...]
                                       build the complete assembly drawing in one
                                       go: drawing SHEET + a TOPVIEW of the design
                                       at the largest standard scale that fits +
                                       PINTABLE + RESTABLE, each its own cell
                                       (ASSY_SHEET, ASSY_TOPVIEW, ASSY_PINTABLE,
                                       ASSY_RESTABLE), placed into cell ASSEMBLY.
                                       Keys: size=A4|A3 orientation=portrait|
                                       landscape scale=<n> layout=auto|side|bottom
                                       title= dwgno= issue= drawnby= drawndate=
                                       checkedby= checkeddate= approvedby=
                                       approveddate= pins=<n> pergroup=<n>
                                       pintable=0|1 restable=0|1 tabletext=<mm>
                                       design=<cell> cell=<name> layer=<n|name>,
                                       or any assembly setting by its own name.
                                       Settings are saved with the design, so a
                                       bare ASSEMBLY rebuilds the last drawing.
                                       Also available as
                                       Session.generate_assembly_drawing()

Layer-based mask operations (resize/boolean, like a GDSII CAM system's
Size/AND/OR deck operators -- any Path on an input layer is converted
to its stroked-outline polygon first; requires the shapely package):
    CLEAR <layer> [cell=<name>]
                                       delete every shape on layer from the
                                       named cell's OWN shapes (never
                                       descending into placed sub-cells);
                                       cell defaults to the current cell.
                                       Handy for a scratch/result layer
                                       between mask operations. Also
                                       available as Session.clear_layer()
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
    OUTLINE <layer> width=<dbu> [cell=<name>]
                                       replace every Rectangle or Polygon on layer
                                       (the named cell's OWN shapes only -- never
                                       descending into placed sub-cells) with an
                                       equivalent closed-ring Path tracing the same
                                       corners, width DBU wide -- e.g. so a
                                       substrate or package body is a stroked
                                       outline instead of a filled, closed shape;
                                       cell defaults to the current cell; also
                                       available as Session.convert_rects_to_outline()
    TOPVIEW                            rebuild the top view with the settings
                                       saved in Tools > Top View...
    TOPVIEW result=<layer> [layers=<n,n,...>] [exclude=<n,n,...>] [hatch=<dbu>]
            [width=<dbu>] [cell=<name>] [out=<cellname>] [flatten=0|1]
            [simplify=<dbu>] [refdes=0|1] [refdes_height=<dbu>]
                                       (refdes=1: ref-des written last, on top)
                                       draw a top-down, hidden-line-removal
                                       pictorial view of the hybrid circuit onto
                                       result_layer of a SEPARATE cell named
                                       TOPVIEW (out=<cellname> to rename it) --
                                       never into the viewed cell itself; the
                                       output cell is rebuilt on every run and
                                       keeps the viewed cell's coordinates, so
                                       SREF TOPVIEW 0,0 overlays it exactly --
                                       the visible OUTLINE edges
                                       of the design as seen from directly above
                                       (as in AutoCAD's HIDE), with the part of a
                                       lower entry's edge buried under a higher
                                       entry's filled footprint actually removed;
                                       with no layers=, stacking follows the real
                                       fab process order by ROLE regardless of
                                       layer numbers -- a cell counts as a
                                       component by NAME (an EIA size code),
                                       FOOTPRINT (a known pin pattern), an
                                       explicit component_models/Component
                                       Library entry, OR STRUCTURALLY as a bare
                                       part -- a placed cell whose own shapes sit
                                       entirely on non-role layers and that has
                                       at least one child instance (e.g. a naked
                                       die with no size code and no catalogued
                                       pin-count table entry) -- not by which
                                       layer it draws on, and sits topmost; then
                                       resistor ink, then conductor (incl.
                                       Route layers), then any other layer with
                                       real geometry, then substrate;
                                       an unclassified "other" layer can never
                                       occlude conductor/resistor/a component just
                                       for having a higher number; glaze AND the
                                       Wire Bond layer are both non-occluding
                                       outline overlays, always fully shown (a
                                       bond wire terminates ON a pad, it doesn't
                                       run underneath one); no fired-film
                                       thickness is needed
                                       anywhere in any of this; exclude=<n,n,...>
                                       drops specific layers out of the role-based
                                       default (ignored once layers= is given
                                       explicitly) -- it ALSO gates the
                                       structural bare-part test above: a bare
                                       part is walked into instead of treated as
                                       an occluding component when ALL of its own
                                       shapes sit on an excluded layer -- the
                                       escape hatch for a sealed package's own
                                       case/lid, whose outline can legitimately
                                       span the whole board and would otherwise
                                       occlude every real component inside it;
                                       hatch=<dbu> fills every
                                       conductor-tier layer's own visible region
                                       with a 45/-45 degree cross-hatch at that
                                       pitch, the standard technical-drawing
                                       convention for a solid material (also
                                       ignored once layers= is given explicitly);
                                       pass layers= to bypass all of the role
                                       logic and get the simpler "every given
                                       layer occludes equally by plain number"
                                       behaviour instead; width=<dbu> sets the
                                       drawn outline width (default 100 =
                                       0.1mm); writes Path shapes, not filled
                                       polygons -- also Tools > Top View... and
                                       Session.mask_op_top_view()

Design Rule Check (DRC -- single-layer and between-layer geometric checks;
requires the shapely package; also available as Tools > DRC...):
    DRCRULE ADD <kind> <layer_a> [layer_b] value=<n> [name=<name>]
                                       add and save a rule to the technology file;
                                       kind: MIN_WIDTH/MIN_AREA/MIN_SPACE (one layer,
                                       omit layer_b) or MIN_SPACE_BETWEEN/MIN_ENCLOSURE
                                       (two layers, layer_b required) -- see Tools >
                                       DRC... for what each kind checks;
                                       EDGE_CLEARANCE <conductor> <substrate> value=<dbu>
                                       keeps a conductor that far in from the substrate
                                       edge; wire bonds: BOND_MIN_LENGTH/BOND_MAX_LENGTH
                                       <bond_layer> value=<dbu>, BOND_MAX_ANGLE
                                       <bond_layer> <die_pad_layer> value=<degrees>
    DRCRULE BONDS [angle=<deg>]       add the wire-bond rules from Setup > Wire Bond
                                       Rules (bond layer, min/max length, each die-pad
                                       layer) with a max departure angle (default 45)
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

Wire bonds (Setup > Technology Properties (Wire Bonds tab); chip-and-wire assembly jumpers):
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
    BOND MOVE <near_x,near_y> <to_x,to_y> [layer=<n|name>] [tol=<dbu>] [cell=<name>]
                                       move ONE end of the wire bond nearest <near>
                                       to <to> -- the end closer to <near> moves
                                       (with its disc), the other stays; the new span
                                       must be within min/max and must not cross
                                       another bond. Also Edit > Move Wire Bond and
                                       Session.move_wire_bond()
    BOND DELETE <x,y> [layer=<n|name>] [tol=<dbu>] [cell=<name>]
                                       remove the wire bond nearest <x,y> (connector
                                       + both discs). Also Edit > Delete Wire Bond
                                       and Session.delete_wire_bond()

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

Pick and Place (centroid file; also File > Export Pick and Place...):
    PNP LIST [cell=<name>] [origin=substrate|design|<x,y>] [printed=0|1] [dies=0|1]
                                       list every placed part with a ref-des: centroid
                                       (pad-pattern centre; body centre for a bare die)
                                       in mm from the origin (default the substrate's
                                       lower-left corner) and placement angle (deg CCW)
    PNP EXPORT <path> [cell=<name>] [origin=...] [format=csv|kicad] [printed=0|1]
               [dies=0|1]              write the pick-and-place file: .pos = KiCad
                                       footprint positions, else CSV (Designator,
                                       Comment, Footprint, Kind, Mid X, Mid Y, Rotation,
                                       Side, Mirrored) after "#" lines with the design,
                                       created date/time, units and origin (header=0
                                       leaves them out; .pos files carry the date too);
                                       printed resistors are left out unless printed=1.
                                       Session.export_pick_and_place()

Netlist Extraction (requires the shapely package; also available as
Tools > Extract Netlist...):
    NETLIST RUN [layer1 layer2 ...] [cell=<name>] [export=<path>]
                [format=csv|report|kicad|tango] [bonds=0|1]
                                       extract the netlist across the given conductor
                                       layer(s) of the current cell (or cell=<name>),
                                       descending into every placed cell instance
                                       (Sref) same as MASKOP/DRC; each net's members
                                       are identified as RefDes.Pin from Rectangle.pin/
                                       Polygon.pin and the placing Sref's Label (Edit
                                       Selected... "Label", or PAD/RESGEN's built-in
                                       auto-numbering); wire bonds on the Wire Bond
                                       Rules... bond layer are included automatically,
                                       each joining what its two ENDS land on (bonds=0
                                       to opt out); the rules' isolated pad layers (die
                                       pads, header pins) stay apart from substrate
                                       copper and join only via bonds -- for chip-and-
                                       wire list only the substrate copper layers;
                                       output flags [open bond end(s)] that landed on
                                       nothing; export=<path> also writes the
                                       netlist to a file -- .csv (net,ref_des,pin),
                                       .txt (report with issues), .net (KiCad) or
                                       .tng (Tango/Protel), or the format= given;
                                       also available as Session.extract_netlist()/
                                       export_netlist()

Thermal analysis (requires numpy, scipy and shapely; also available as
Tools > Thermal Analysis...):
    THERMAL PARTS [cell=<name>]       list every resistor and component that can
                                       be given a power, with its current power and
                                       where that power came from
    THERMAL POWER <ref_des> <mW>      set the power a part dissipates, by reference
                                       designator; a resistor left unset uses its
                                       Resistor Designer power, anything else 0.
                                       THERMAL POWER <ref_des> CLEAR removes it
    THERMAL SET <key> <value>         change one boundary or limit setting:
                                       base_temp_c, ambient_c, base_mode (fixed|htc),
                                       base_htc, top_htc, substrate_material,
                                       substrate_um, substrate_layer, mount_material,
                                       mount_um, grid_um, resistor_max_c, die_max_c,
                                       component_max_c, resistor_max_w_mm2
    THERMAL MATERIAL <layer> <material|k>
                                       set a film layer's material by name (THERMAL
                                       MATERIALS lists them) or its conductivity in
                                       W/m.K; THERMAL MATERIAL <layer> CLEAR goes back
                                       to the layer's role default
    THERMAL MATERIALS                 list the named materials and their conductivity
    THERMAL RUN [cell=<name>] [grid=<dbu>] [base=<degC>]
                                       steady-state analysis of the current cell (or
                                       cell=<name>): builds the stack from the
                                       substrate outline, each printed film and every
                                       powered part, solves heat conduction, and
                                       reports each part's peak and average
                                       temperature against its limit, hottest first,
                                       with the energy balance. Never modifies the
                                       design; also Session.run_thermal()
    THERMAL REPORT out=<path.html> [csv=<path.csv>]
                                       write the last THERMAL RUN as an HTML report
                                       (parts table, temperature maps, stack, setup)
                                       and optionally a CSV of the parts table
    THERMAL CONTOURS <layer> [step=<degC>] [slice=<name>]
                                       draw the last run's isotherms onto <layer> as
                                       paths, one every step deg C (default 5), over
                                       the top surface or a named slice; appends --
                                       CLEAR the layer first for a clean redraw

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


def _is_number(tok: str) -> bool:
    try:
        float(tok)
        return True
    except ValueError:
        return False


def _join_points(args, start, count=None):
    """Let a point be typed as two tokens (``0 0``) as well as one
    (``0,0``): from index ``start``, the next ``count`` points (all the
    rest if None) are each turned into a single ``x,y`` token, leaving
    everything else untouched. A token that already has a comma, or
    isn't a number, is passed through as is."""
    out = list(args[:start])
    i, n = start, 0
    while i < len(args) and (count is None or n < count):
        a = args[i]
        if ("," not in a and _is_number(a) and i + 1 < len(args)
                and "," not in args[i + 1] and _is_number(args[i + 1])):
            out.append(f"{a},{args[i + 1]}")
            i += 2
        else:
            out.append(a)
            i += 1
        n += 1
    out.extend(args[i:])
    return out


def _tokenize(line: str):
    """Split a command line into tokens: whitespace-separated, quotes
    (single or double) group and are stripped.

    Unlike a plain ``shlex.split()``, backslash is NOT an escape
    character -- so a Windows path like ``export=C:\\Users\\me\\x.csv``
    survives intact instead of losing every ``\\U`` / ``\\m`` to escape
    processing. Raises ``ValueError`` on an unbalanced quote, same as
    ``shlex.split()``.
    """
    lex = shlex.shlex(line, posix=True)
    lex.whitespace_split = True
    lex.escape = ""          # backslash is a literal character, not an escape
    lex.commenters = ""      # '#' is not a comment in a command line
    return list(lex)


def net_summary(tr, limit=20):
    """One-line-plus description of a Session.highlight_net() result:
    net name first, then pads, fragments and what's still unconnected."""
    shown = ", ".join(tr.pins[:limit]) + (f", ... (+{len(tr.pins) - limit})"
                                          if len(tr.pins) > limit else "")
    frag = getattr(tr, "fragments", 1)
    miss = getattr(tr, "missing", [])
    head = (f"net {tr.name}: {len(tr.pins)} pad(s) on layer(s) "
            f"{', '.join(map(str, tr.layers))}")
    if frag > 1:
        head += f", in {frag} unjoined pieces"
    if miss:
        head += f"; not connected: {', '.join(miss)}"
    return head + (f"\n  {shown}" if shown else "")


class CommandInterpreter:
    MAX_HISTORY = 10  # Up/Down in the command bar recalls at most this many

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
        del self.history[:-self.MAX_HISTORY]
        self._hist_index = len(self.history)
        try:
            tokens = _tokenize(line)
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
        args = _join_points(args, 1, 2)
        if len(args) != 3:
            raise CommandError("usage: RECT <layer> <x1,y1> <x2,y2>  (layer: number or name)")
        layer = args[0]
        x1, y1 = _parse_point(args[1])
        x2, y2 = _parse_point(args[2])
        self.session.rect(layer, x1, y1, x2, y2)
        return "rectangle added"

    def _cmd_poly(self, args):
        args = _join_points(args, 1, None)
        if len(args) < 4:
            raise CommandError("usage: POLY <layer> <x1,y1> <x2,y2> <x3,y3> ...  (layer: number or name)")
        layer = args[0]
        points = [_parse_point(a) for a in args[1:]]
        self.session.polygon(layer, points)
        return f"polygon added ({len(points)} points)"

    def _cmd_path(self, args):
        args = _join_points(args, 3, None)
        if len(args) < 5:
            raise CommandError("usage: PATH <layer> <width> <cap> <x1,y1> <x2,y2> ...  (layer: number or name)")
        layer = args[0]
        width = float(args[1])
        cap = args[2].lower()
        points = [_parse_point(a) for a in args[3:]]
        self.session.path(layer, points, width, cap=cap)
        return f"path added ({len(points)} points)"

    def _cmd_circle(self, args):
        args = _join_points(args, 1, 1)
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
        args = _join_points(args, 1, 1)
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
        args = _join_points(args, 1, 1)
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
        args = _join_points(args, 1, 1)
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
        kind = self.session.delete_last()
        return f"removed last {kind}" if kind else "nothing to delete"

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
        if args and args[0].upper() == "WINDOW":
            args = _join_points(args, 1, 2)
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

    def _cmd_pan(self, args):
        """PAN <dx,dy> | PAN TO <x,y> | PAN LEFT|RIGHT|UP|DOWN [fraction]:
        move the view without changing the zoom (canvas.pan_by/pan_to/
        pan_step). Coordinates are DBU, like ZOOM WINDOW."""
        usage = "usage: PAN <dx,dy> | PAN TO <x,y> | PAN LEFT|RIGHT|UP|DOWN [fraction]"
        if self.view is None:
            raise CommandError("PAN requires an attached view")
        if not args:
            raise CommandError(usage)
        head = args[0].upper()
        if head in ("LEFT", "RIGHT", "UP", "DOWN"):
            if len(args) > 2:
                raise CommandError(usage)
            frac = float(args[1]) if len(args) == 2 else None
            self.view.pan_step(head.lower(), frac)
            cx, cy = self.view.view_center()
            return f"pan {head.lower()} -- view centre {cx:.0f},{cy:.0f}"
        if head == "TO":
            args = _join_points(args, 1, 1)
            if len(args) != 2:
                raise CommandError(usage)
            x, y = _parse_point(args[1])
            self.view.pan_to(x, y)
            return f"view centred on {x:g},{y:g}"
        args = _join_points(args, 0, 1)
        if len(args) != 1:
            raise CommandError(usage)
        dx, dy = _parse_point(args[0])
        self.view.pan_by(dx, dy)
        cx, cy = self.view.view_center()
        return f"pan {dx:g},{dy:g} -- view centre {cx:.0f},{cy:.0f}"

    def _cmd_refdes(self, args):
        """REFDES PIN1 [<refdes>|ALL] [gap=<dbu>]: move a placed part's
        floated ref-des beside its pin 1 (Session.refdes_to_pin1). No
        ref-des / ALL = every connector/header (J, P, X, CN, CON, HDR)."""
        usage = "usage: REFDES PIN1 [<refdes>|ALL] [gap=<dbu>]"
        if not args or args[0].upper() != "PIN1":
            raise CommandError(usage)
        target, gap = None, None
        for a in args[1:]:
            if a.lower().startswith("gap="):
                gap = int(float(a.split("=", 1)[1]))
            elif a.upper() == "ALL":
                target = None
            elif target is None:
                target = a
            else:
                raise CommandError(usage)
        moved = self.session.refdes_to_pin1(target, gap=gap)
        if not moved:
            return "no connector/header with a pin 1 found" if target is None else \
                f"{target} has no pin 1"
        return "ref-des moved beside pin 1: " + ", ".join(moved)

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
        args = _join_points(args, 0, 1)
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
        args = _join_points(args, 0, 1)
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
        args = _join_points(args, 1, 1)
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
        usage = "usage: GERBER <output_prefix> [cell=<name>] [scale=<n>]"
        if not args:
            raise CommandError(usage)
        cell_name = None
        prefix = None
        scale = 1.0
        for a in args:
            if a.lower().startswith("cell="):
                cell_name = a.split("=", 1)[1]
            elif a.lower().startswith("scale="):
                text = a.split("=", 1)[1].strip().rstrip("xX")
                try:
                    scale = float(text)
                except ValueError:
                    raise CommandError(f"scale must be a number, e.g. scale=5 (got {text!r})")
            elif prefix is None:
                prefix = a
            else:
                raise CommandError(usage)
        if prefix is None:
            raise CommandError(usage)
        written, skipped_text = self.session.export_gerber(prefix, cell_name=cell_name,
                                                           scale=scale)
        if not written:
            return "no visible layers had data to export"
        msg = f"wrote {len(written)} Gerber file(s): " + ", ".join(f"L{n}" for n, _ in written)
        if scale != 1.0:
            msg += f" at {scale:g}X"
        if skipped_text:
            msg += f" ({skipped_text} text shape(s) skipped -- Gerber has no text primitive)"
        return msg

    def _cmd_freecad(self, args):
        usage = "usage: FREECAD <path.FCMacro> [cell=<name>]"
        if not args:
            raise CommandError(usage)
        cell_name = None
        out = None
        for a in args:
            if a.lower().startswith("cell="):
                cell_name = a.split("=", 1)[1]
            elif out is None:
                out = a
            else:
                raise CommandError(usage)
        if out is None:
            raise CommandError(usage)
        try:
            written = self.session.export_freecad(out, cell_name=cell_name)
        except LayoutError as e:
            raise CommandError(str(e))
        return f"FreeCAD 3D macro written to {written}"

    def _cmd_print(self, args):
        usage = ("usage: PRINT [cell=<name>] [out=<path.html>] [scale=<n>] "
                  "[pinlayers=<n,n,...>]")
        cell_name = None
        out = None
        scale = None
        pin_layers = None
        for a in args:
            if a.lower().startswith("cell="):
                cell_name = a.split("=", 1)[1]
            elif a.lower().startswith("out="):
                out = a.split("=", 1)[1]
            elif a.lower().startswith("scale="):
                try:
                    scale = float(a.split("=", 1)[1])
                except ValueError:
                    raise CommandError("scale must be a number")
                if scale <= 0:
                    raise CommandError("scale must be positive")
            elif a.lower().startswith("pinlayers="):
                pin_layers = [tok.strip() for tok in a.split("=", 1)[1].split(",") if tok.strip()]
            else:
                raise CommandError(usage)
        try:
            path = self.session.print_cell(cell_name=cell_name, out_path=out, scale=scale,
                                            pin_label_layers=pin_layers)
        except LayoutError as e:
            raise CommandError(str(e))
        from ..io.printio import hidden_layers_with_data, send_to_system_printer
        if send_to_system_printer(path):
            msg = f"sent to the system printer: {path}"
        else:
            import webbrowser
            webbrowser.open(path)
            msg = f"opened {path} -- use Ctrl+P to print (no system print handoff available here)"
        resolved_cell = cell_name or self.session.current_cell_name
        hidden = hidden_layers_with_data(self.session.library, resolved_cell)
        if hidden:
            names = ", ".join(str(n) for n in hidden)
            msg += f" (note: layer(s) {names} have geometry here but are hidden -- not printed)"
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
        return "\n".join(f"{c['name']}: {c['shapes']} shapes, {c['srefs']} srefs  "
                         f"{_stamp_str(c)}" for c in cells)

    def _cmd_cellstamps(self, args):
        source = None
        if args:
            source = "current" if args[0].lower() == "current" else int(args[0])
        rows = self.session.cell_stamps(source)
        if not rows:
            return "(no cells)"
        rows.sort(key=lambda r: (str(r["source"]), r["name"].lower()))
        w = max(len(r["name"]) for r in rows)
        return "\n".join(f"[{r['source']}] {r['name']:<{w}}  {_stamp_str(r)}" for r in rows)

    def _cmd_find(self, args):
        if not args:
            raise CommandError("usage: FIND <substring>")
        query = " ".join(args)
        matches = self.session.find_cells(query)
        if not matches:
            return f"no cells matching {query!r}"
        return "\n".join(f"{m['name']} (source: {m['source']})  {_stamp_str(m)}" for m in matches)

    def _cmd_place(self, args):
        args = _join_points(args, 1, 1)
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
        args = _join_points(args, 0, 1)
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

    def _cmd_restable(self, args):
        usage = ("usage: RESTABLE <cell_name> <layer> [origin=<x,y>] "
                 "[rowheight=<dbu>] [textheight=<dbu>] [linelayer=<n|name>] "
                 "[linewidth=<dbu>] [title=<text>] [titleinset=<dbu>] "
                 "[overwrite=0|1]  (quote title=\"...\" if it has spaces)")
        if len(args) < 2:
            raise CommandError(usage)
        cell_name, layer = args[0], args[1]
        kwargs = {}
        for a in args[2:]:
            if "=" not in a:
                raise CommandError(usage)
            k, v = a.split("=", 1)
            k = k.lower()
            if k == "origin":
                kwargs["origin"] = _parse_point(v)
            elif k == "rowheight":
                kwargs["row_height"] = float(v)
            elif k == "textheight":
                kwargs["text_height"] = float(v)
            elif k == "linelayer":
                kwargs["line_layer"] = self.session.library.layers.resolve(v)
            elif k == "linewidth":
                kwargs["line_width"] = float(v)
            elif k == "title":
                kwargs["title"] = v
            elif k == "titleinset":
                kwargs["title_inset"] = float(v)
            elif k == "overwrite":
                kwargs["overwrite"] = v not in ("0", "false", "no")
            else:
                raise CommandError(f"unknown RESTABLE option {k!r}")
        try:
            name = self.session.generate_resistor_table(cell_name, layer, **kwargs)
        except LayoutError as e:
            raise CommandError(str(e))
        return f"generated resistor table cell {name!r}"

    def _cmd_pintable(self, args):
        usage = ("usage: PINTABLE <cell_name> <layer> <pin_count> "
                 "[pergroup=<n>] [origin=<x,y>] [rowheight=<dbu>] [textheight=<dbu>] "
                 "[linelayer=<n|name>] [linewidth=<dbu>] [groupgap=<dbu>] "
                 "[title=<text>] [titleinset=<dbu>] [overwrite=0|1]  "
                 "(quote title=\"...\" if it has spaces)")
        if len(args) < 3:
            raise CommandError(usage)
        cell_name, layer = args[0], args[1]
        try:
            pin_count = int(args[2])
        except ValueError:
            raise CommandError(f"{usage}\n(pin_count must be a whole number)")
        kwargs = {}
        for a in args[3:]:
            if "=" not in a:
                raise CommandError(usage)
            k, v = a.split("=", 1)
            k = k.lower()
            if k == "pergroup":
                kwargs["pins_per_group"] = int(v)
            elif k == "origin":
                kwargs["origin"] = _parse_point(v)
            elif k == "rowheight":
                kwargs["row_height"] = float(v)
            elif k == "textheight":
                kwargs["text_height"] = float(v)
            elif k == "linelayer":
                kwargs["line_layer"] = self.session.library.layers.resolve(v)
            elif k == "linewidth":
                kwargs["line_width"] = float(v)
            elif k == "groupgap":
                kwargs["group_gap"] = float(v)
            elif k == "title":
                kwargs["title"] = v
            elif k == "titleinset":
                kwargs["title_inset"] = float(v)
            elif k == "overwrite":
                kwargs["overwrite"] = v not in ("0", "false", "no")
            else:
                raise CommandError(f"unknown PINTABLE option {k!r}")
        try:
            name = self.session.generate_pin_function_table(cell_name, layer, pin_count, **kwargs)
        except LayoutError as e:
            raise CommandError(str(e))
        return f"generated pin function table cell {name!r}"

    def _cmd_sheet(self, args):
        usage = ("usage: SHEET <cell_name> <layer> [size=A4|A3] "
                 "[orientation=portrait|landscape] [origin=<x,y>] [margin=<mm>] "
                 "[titleheight=<mm>] [textheight=<mm>] [linewidth=<mm>] "
                 "[linelayer=<n|name>] [title=<text>] [dwgno=<text>] [issue=<text>] "
                 "[drawnby=<text>] [drawndate=<text>] [checkedby=<text>] "
                 "[checkeddate=<text>] [approvedby=<text>] [approveddate=<text>] "
                 "[overwrite=0|1]  (quote a value with spaces, e.g. title=\"...\")")
        if len(args) < 2:
            raise CommandError(usage)
        cell_name, layer = args[0], args[1]
        kwargs = {}
        for a in args[2:]:
            if "=" not in a:
                raise CommandError(usage)
            k, v = a.split("=", 1)
            k = k.lower()
            if k == "size":
                kwargs["size"] = v
            elif k == "orientation":
                kwargs["orientation"] = v
            elif k == "origin":
                kwargs["origin"] = _parse_point(v)
            elif k == "margin":
                kwargs["margin_mm"] = float(v)
            elif k == "titleheight":
                kwargs["title_block_height_mm"] = float(v)
            elif k == "textheight":
                kwargs["text_height_mm"] = float(v)
            elif k == "linewidth":
                kwargs["line_width_mm"] = float(v)
            elif k == "linelayer":
                kwargs["line_layer"] = self.session.library.layers.resolve(v)
            elif k == "title":
                kwargs["title"] = v
            elif k == "dwgno":
                kwargs["dwg_no"] = v
            elif k == "issue":
                kwargs["issue"] = v
            elif k == "drawnby":
                kwargs["drawn_by"] = v
            elif k == "drawndate":
                kwargs["drawn_date"] = v
            elif k == "checkedby":
                kwargs["checked_by"] = v
            elif k == "checkeddate":
                kwargs["checked_date"] = v
            elif k == "approvedby":
                kwargs["approved_by"] = v
            elif k == "approveddate":
                kwargs["approved_date"] = v
            elif k == "overwrite":
                kwargs["overwrite"] = v not in ("0", "false", "no")
            else:
                raise CommandError(f"unknown SHEET option {k!r}")
        try:
            name = self.session.generate_drawing_sheet(cell_name, layer, **kwargs)
        except LayoutError as e:
            raise CommandError(str(e))
        return f"generated drawing sheet cell {name!r}"

    _ASSEMBLY_ALIASES = {
        "dwgno": "dwg_no", "drawnby": "drawn_by", "drawndate": "drawn_date",
        "checkedby": "checked_by", "checkeddate": "checked_date",
        "approvedby": "approved_by", "approveddate": "approved_date",
        "pins": "pin_count", "pergroup": "pins_per_group",
        "tabletext": "table_text_mm", "design": "design_cell", "cell": "cell_name",
        "restable": "resistor_table", "pintable": "pin_table",
    }

    def _cmd_assembly(self, args):
        from ..scripting.assembly import DEFAULT_ASSEMBLY_SETTINGS, _scale_text
        usage = ("usage: ASSEMBLY [size=A4|A3] [orientation=portrait|landscape] "
                 "[scale=<n>] [layout=auto|side|bottom] [title=<text>] [dwgno=<text>] "
                 "[issue=<text>] [drawnby=<text>] ... [pins=<n>] [pergroup=<n>] "
                 "[pintable=0|1] [restable=0|1] [tabletext=<mm>] [design=<cell>] "
                 "[cell=<name>] [layer=<n|name>]  (no args = rebuild from saved settings)")
        kwargs = {}
        for a in args:
            if "=" not in a:
                raise CommandError(usage)
            k, v = a.split("=", 1)
            k = self._ASSEMBLY_ALIASES.get(k.lower(), k.lower())
            if k not in DEFAULT_ASSEMBLY_SETTINGS or k == "pin_functions":
                raise CommandError(f"unknown ASSEMBLY option {k!r}")
            default = DEFAULT_ASSEMBLY_SETTINGS[k]
            try:
                if k in ("layer", "line_layer"):
                    kwargs[k] = self.session.library.layers.resolve(v)
                elif isinstance(default, bool):
                    kwargs[k] = v.lower() not in ("0", "false", "no", "off")
                elif isinstance(default, float) or k == "scale":
                    kwargs[k] = float(v) if v else None
                elif k in ("pin_count", "pins_per_group"):
                    kwargs[k] = int(v) if v else None
                elif default is None:
                    kwargs[k] = v or None
                else:
                    kwargs[k] = v
            except ValueError:
                raise CommandError(f"bad value for ASSEMBLY option {k!r}: {v!r}")
        try:
            r = self.session.generate_assembly_drawing(**kwargs)
        except LayoutError as e:
            raise CommandError(str(e))
        msg = (f"generated assembly drawing {r['cell']!r} -- top view at "
               f"{_scale_text(r['scale'])}, tables {r['layout']}")
        if r["warnings"]:
            msg += "; " + "; ".join(r["warnings"])
        return msg

    # --- mask ops ---------------------------------------------------------
    def _cmd_clear(self, args):
        usage = "usage: CLEAR <layer> [cell=<name>]"
        if not args:
            raise CommandError(usage)
        layer = args[0]
        kwargs = {}
        for a in args[1:]:
            if "=" not in a:
                raise CommandError(usage)
            k, v = a.split("=", 1)
            k = k.lower()
            if k == "cell":
                kwargs["cell_names"] = [v]
            else:
                raise CommandError(f"unknown CLEAR option {k!r}")
        written = self.session.clear_layer(layer, **kwargs)
        total = sum(written.values())
        if total == 0:
            return f"CLEAR: nothing on layer {layer} to delete"
        return f"CLEAR: deleted {total} shape(s) from layer {layer}"

    def _cmd_outline(self, args):
        usage = "usage: OUTLINE <layer> width=<dbu> [cell=<name>]"
        if not args:
            raise CommandError(usage)
        layer = args[0]
        kwargs = {}
        for a in args[1:]:
            if "=" not in a:
                raise CommandError(usage)
            k, v = a.split("=", 1)
            k = k.lower()
            if k == "width":
                kwargs["width"] = float(v)
            elif k == "cell":
                kwargs["cell_names"] = [v]
            else:
                raise CommandError(f"unknown OUTLINE option {k!r}")
        if "width" not in kwargs:
            raise CommandError(f"{usage}\n(width=<dbu> is required)")
        written = self.session.convert_rects_to_outline(layer, **kwargs)
        total = sum(written.values())
        if total == 0:
            return f"OUTLINE converted no rectangles/polygons on layer {layer}"
        return f"OUTLINE converted {total} shape(s) to outline paths on layer {layer}"

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

    def _cmd_topview(self, args):
        usage = ("usage: TOPVIEW result=<layer> [layers=<n,n,...>] [exclude=<n,n,...>] "
                 "[hatch=<dbu>] [hatch_angle=<deg>[,<deg>]] [width=<dbu>] [cell=<name>] "
                 "[out=<cellname>] [flatten=0|1] [simplify=<dbu>] [refdes=0|1] "
                 "[refdes_height=<dbu>]   "
                 "(TOPVIEW alone = the settings saved in Tools > Top View...)")
        if not args:
            from ..io.techfile import load_topview_settings
            try:
                written = self.session.top_view_from_settings()
            except LayoutError as e:
                raise CommandError(f"{e}\n{usage}")
            st = load_topview_settings(self.session.library)
            total = sum(written.values())
            layer_obj = self.session.library.layers[int(st["result_layer"])]
            if total and not layer_obj.visible:
                layer_obj.visible = True
            return (f"TOPVIEW (saved settings) wrote {total} path(s) to layer "
                    f"{st['result_layer']} of cell {st['out_cell']}")
        kwargs = {}
        for a in args:
            if "=" not in a:
                raise CommandError(usage)
            k, v = a.split("=", 1)
            k = k.lower()
            if k == "result":
                kwargs["result_layer"] = v
            elif k == "layers":
                kwargs["layers"] = [tok.strip() for tok in v.split(",") if tok.strip()]
            elif k == "exclude":
                kwargs["exclude_layers"] = [tok.strip() for tok in v.split(",") if tok.strip()]
            elif k == "hatch":
                kwargs["hatch_conductor_pitch"] = float(v)
            elif k == "hatch_angle":
                kwargs["hatch_conductor_angles"] = tuple(
                    float(tok.strip()) for tok in v.split(",") if tok.strip())
            elif k == "width":
                kwargs["line_width"] = float(v)
            elif k == "cell":
                kwargs["cell_names"] = [v]
            elif k == "out":
                kwargs["out_cell"] = v
            elif k == "flatten":
                kwargs["flatten"] = v not in ("0", "false", "no")
            elif k == "simplify":
                kwargs["simplify"] = float(v)
            elif k == "refdes":
                kwargs["refdes"] = v not in ("0", "false", "no")
            elif k == "refdes_height":
                kwargs["refdes_height"] = float(v)
            else:
                raise CommandError(f"unknown TOPVIEW option {k!r}")
        if "result_layer" not in kwargs:
            raise CommandError(f"{usage}\n(result=<layer> is required)")
        result_layer = kwargs.pop("result_layer")
        written = self.session.mask_op_top_view(result_layer, **kwargs)
        total = sum(written.values())
        if total == 0:
            return f"TOPVIEW produced no output on layer {result_layer}"
        resolved_layer = self.session.library.layers.resolve(result_layer)
        layer_obj = self.session.library.layers[resolved_layer]
        made_visible = False
        if not layer_obj.visible:
            layer_obj.visible = True
            made_visible = True
        suffix = " (layer visibility turned on)" if made_visible else ""
        out_name = kwargs.get("out_cell", "TOPVIEW")
        return (f"TOPVIEW wrote {total} path(s) to layer {result_layer} "
                f"of cell {out_name}{suffix}")

    # --- DRC (Design Rule Check) -------------------------------------------
    def _cmd_drcrule(self, args):
        from ..core.drc import DRC_RULE_KINDS, TWO_LAYER_KINDS, DrcRule
        from ..io.techfile import load_drc_rules, save_drc_rules

        usage = (
            "usage: DRCRULE ADD <kind> <layer_a> [layer_b] value=<n> [name=<name>]\n"
            "       DRCRULE BONDS [angle=<deg>]\n"
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

        if sub == "BONDS":
            from ..core.drc import DEFAULT_BOND_MAX_ANGLE, bond_rules_from_settings
            from ..core.errors import LayoutError
            from ..io.techfile import load_bond_rules
            angle = DEFAULT_BOND_MAX_ANGLE
            for a in args[1:]:
                if a.lower().startswith("angle="):
                    try:
                        angle = float(a.split("=", 1)[1])
                    except ValueError:
                        raise CommandError("usage: DRCRULE BONDS [angle=<deg>]")
                else:
                    raise CommandError("usage: DRCRULE BONDS [angle=<deg>]")
            existing = [DrcRule.from_dict(r) for r in rules]
            try:
                new = bond_rules_from_settings(load_bond_rules(self.session.library), existing,
                                               angle=angle)
            except LayoutError as e:
                raise CommandError(str(e))
            for r in new:
                rules.append(r.to_dict())
            save_drc_rules(self.session.library, rules)
            if not new:
                return "the wire-bond rules are already saved"
            return "\n".join(f"added rule #{len(rules) - len(new) + i + 1}: {r.label}"
                             for i, r in enumerate(new))

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

    def _cmd_pnp(self, args):
        usage = ("usage: PNP LIST [cell=<name>] [origin=substrate|design|<x,y>] "
                 "[printed=0|1] [dies=0|1]\n"
                 "       PNP EXPORT <path> [cell=<name>] [origin=...] [format=csv|kicad] "
                 "[printed=0|1] [dies=0|1] [header=0|1]")
        if not args or args[0].upper() not in ("LIST", "EXPORT"):
            raise CommandError(usage)
        sub = args[0].upper()
        kw = {"origin": "substrate"}
        path, fmt, header = None, None, True
        for a in args[1:]:
            if "=" not in a:
                if sub == "EXPORT" and path is None:
                    path = a
                    continue
                raise CommandError(usage)
            k, v = a.split("=", 1)
            k = k.lower()
            if k == "cell":
                kw["cell_name"] = v
            elif k == "origin":
                if v.lower() in ("substrate", "design"):
                    kw["origin"] = v.lower()
                else:
                    try:
                        x, y = (float(t) for t in v.split(","))
                    except ValueError:
                        raise CommandError(f"{usage}\n(origin=<x,y> is two DBU numbers)")
                    kw["origin"] = (x, y)
            elif k == "format" and sub == "EXPORT":
                fmt = v.lower()
            elif k in ("printed", "dies"):
                kw["include_" + k] = v.lower() not in ("0", "false", "no")
            elif k == "header" and sub == "EXPORT":
                header = v.lower() not in ("0", "false", "no")
            else:
                raise CommandError(f"unknown PNP option {k!r}")
        if sub == "EXPORT":
            if not path:
                raise CommandError(usage)
            try:
                t = self.session.export_pick_and_place(path, fmt=fmt, header=header, **kw)
            except ValueError as e:
                raise CommandError(str(e))
        else:
            t = self.session.pick_and_place(**kw)
        ox, oy = t["origin"]
        lines = [f"{len(t['rows'])} part(s); origin {t['origin_kind']} "
                 f"({dbu_to_mm(ox):g}, {dbu_to_mm(oy):g}) mm"]
        if sub == "EXPORT":
            lines[0] += f"; wrote {t['format']} to {path} (created {t['created']})"
        else:
            for r in t["rows"]:
                lines.append(f"  {r['designator']:<8} {r['footprint']:<20} {r['kind']:<9} "
                             f"x {r['x_mm']:9.4f}  y {r['y_mm']:9.4f}  rot {r['rotation']:7.2f}"
                             f"{'  mirrored' if r['mirrored'] else ''}")
        return "\n".join(lines)

    def _cmd_netlist(self, args):
        usage = ("usage: NETLIST RUN [layer1 layer2 ...] [cell=<name>] "
                 "[export=<path>] [format=csv|report|kicad|tango] [bonds=0|1]  "
                 "(no layers = the design's saved Extract Netlist selection; the "
                 "export format follows the extension: .csv .txt .net(KiCad) .tng(Tango))")
        if not args or args[0].upper() != "RUN":
            raise CommandError(usage)
        cell_name = None
        export_path = None
        export_fmt = None
        include_bonds = True
        layers = []
        for a in args[1:]:
            if a.lower().startswith("format="):
                export_fmt = a.split("=", 1)[1].lower()
            elif a.lower().startswith("cell="):
                cell_name = a.split("=", 1)[1]
            elif a.lower().startswith("export="):
                export_path = a.split("=", 1)[1]
            elif a.lower().startswith("bonds="):
                include_bonds = a.split("=", 1)[1] not in ("0", "false", "no")
            else:
                layers.append(a)
        if not layers:
            from ..io.techfile import load_netlist_settings
            layers = load_netlist_settings(self.session.library)["conductor_layers"]
            if not layers:
                raise CommandError(
                    f"{usage}\n(at least one conductor layer is required -- none given "
                    "and none saved in Tools > Extract Netlist...)")
        nets = self.session.extract_netlist(layers, cell_name=cell_name,
                                            include_bonds=include_bonds)
        if export_path:
            try:
                used_fmt = self.session.export_netlist(nets, export_path, fmt=export_fmt,
                                                       cell_name=cell_name)
            except ValueError as e:
                raise CommandError(str(e))
        lines = [f"netlist: {len(nets)} net(s)"]
        for net in nets:
            members = ", ".join(p.label() for p in net.pins) or "(no pins)"
            extra = f" [{net.unresolved_count} unresolved]" if net.unresolved_count else ""
            if getattr(net, "bonds", 0):
                extra += f" [{net.bonds} bond(s)]"
            opens = getattr(net, "open_bond_ends", [])
            if opens:
                extra += " [open bond end(s): " + ", ".join(f"{x},{y}" for x, y in opens) + "]"
            lines.append(f"  {net.id}: {members}{extra}")
        if export_path:
            lines.append(f"exported ({used_fmt}) to {os.path.abspath(export_path)}")
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
        from ..core.drc import BOND_KINDS
        edge = [v.message for v in violations if v.rule.kind == "EDGE_CLEARANCE"]
        if edge:
            lines.append("substrate edge:")
            lines.extend(f"  {m}" for m in edge)
        bonds = [v.message for v in violations if v.rule.kind in BOND_KINDS]
        if bonds:
            lines.append("wire bonds:")
            lines.extend(f"  {m}" for m in bonds)
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
        sub = args[0].upper() if args else ""
        if sub in ("MOVE", "DELETE"):
            args = _join_points(args, 1, 2 if sub == "MOVE" else 1)
        else:
            args = _join_points(args, 0, 2)
        usage = (
            "usage: BOND <x1,y1> <x2,y2> [layer=<n|name>] [width=<dbu>] "
            "[pad=<dbu>] [min=<dbu>] [max=<dbu>] [cell=<name>]\n"
            "       BOND MOVE <near_x,near_y> <to_x,to_y> [layer=<n|name>] [tol=<dbu>] [cell=<name>]\n"
            "       BOND DELETE <x,y> [layer=<n|name>] [tol=<dbu>] [cell=<name>]\n"
            "  draws / moves / removes a wire bond (O------O) on the bond layer;\n"
            "  a new/moved span must be within min/max and must not cross another.\n"
            "  MOVE picks the bond nearest <near>, then moves the end closer to\n"
            "  <near> to <to> (the other end stays). DELETE picks the bond nearest\n"
            "  <x,y>. Bond layer/defaults come from Setup > Technology Properties (Wire Bonds tab)"
        )
        if not args:
            raise CommandError(usage)

        sub = args[0].upper()
        if sub in ("MOVE", "DELETE"):
            rest = args[1:]
            positional, kwargs = [], {}
            for a in rest:
                if "=" in a:
                    k, v = a.split("=", 1)
                    k = k.lower()
                    if k == "layer":
                        kwargs["layer"] = v
                    elif k == "tol":
                        kwargs["tol"] = float(v)
                    elif k == "cell":
                        kwargs["cell_name"] = v
                    else:
                        raise CommandError(f"unknown BOND {sub} option {k!r}")
                else:
                    positional.append(a)
            if sub == "MOVE":
                if len(positional) != 2:
                    raise CommandError(usage)
                near = _parse_point(positional[0])
                to = _parse_point(positional[1])
                fixed, moved = self.session.move_wire_bond(near, to, **kwargs)
                return f"wire bond end moved to {moved}  (other end {fixed})"
            if len(positional) != 1:
                raise CommandError(usage)
            px, py = _parse_point(positional[0])
            e1, e2 = self.session.delete_wire_bond((px, py), **kwargs)
            return f"wire bond {e1} - {e2} removed"

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
        args = _join_points(args, 0, 2)
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
                 "[index=0|1] [overwrite=0|1]\n"
                 "       VIA DELETE <x,y> [tol=<dbu>] [layers=<a,b,...>] [cell=<name>]\n"
                 "       VIA FIX [cell=<name>]")
        if args and args[0].upper() == "FIX":
            cell = next((a.split("=", 1)[1] for a in args[1:] if a.lower().startswith("cell=")), None)
            r = self.session.fix_vias(cell_name=cell)
            sig = self.session.via_spec(False)
            return (f"redrew {r['vias']} via(s) and {r['via_cells']} via cell(s): signal via pad "
                    f"{sig['pad']:g}, window {sig['window']:g}")
        if args and args[0].upper() == "DELETE":
            positional, kwargs = [], {}
            for a in args[1:]:
                if "=" in a:
                    k, v = a.split("=", 1)
                    k = k.lower()
                    if k == "tol":
                        kwargs["tol"] = float(v)
                    elif k == "cell":
                        kwargs["cell_name"] = v
                    elif k == "layers":
                        kwargs["layers"] = [int(x) if x.lstrip("-").isdigit() else x
                                            for x in v.split(",") if x]
                    else:
                        raise CommandError(f"unknown VIA DELETE option {k!r}")
                else:
                    positional.append(a)
            if len(positional) != 1:
                raise CommandError(usage)
            centre, layers = self.session.delete_via(_parse_point(positional[0]), **kwargs)
            return f"via at {centre} removed (layers {', '.join(map(str, layers))})"
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

    def _cmd_autoplace(self, args):
        usage = ("usage: AUTOPLACE RUN <netlist> [overrides=<csv>] [effort=<n>] [seed=<n>] "
                 "[thermal=<x>] [grid=<um>] | AUTOPLACE IMPROVE <netlist> [...] | "
                 "AUTOPLACE WEIGHT <net> <w>")
        if not args:
            raise CommandError(usage)
        sub = args[0].upper()
        if sub == "WEIGHT":
            if len(args) != 3:
                raise CommandError(usage)
            ws = dict(self.session.autoplace_settings()["weights"])
            ws[args[1]] = float(args[2])
            self.session.set_autoplace_settings(weights=ws)
            return f"net {args[1]} weight {float(args[2]):g}"
        if sub not in ("RUN", "IMPROVE") or len(args) < 2:
            raise CommandError(usage)
        opts, ov = {}, None
        for a in args[2:]:
            k, _, v = a.partition("=")
            k = k.lower()
            if k == "overrides":
                ov = v
            elif k in ("effort", "thermal", "grid"):
                opts[k] = float(v)
            elif k == "seed":
                opts[k] = int(v)
            else:
                raise CommandError(usage)
        r = self.session.auto_place(args[1], overrides=ov, improve=(sub == "IMPROVE"), **opts)
        d = self.session.sli()
        msg = f"placed {r['placed']} part(s); SLI total {d['total'] / 1000:.1f} mm"
        if r["skipped"]:
            msg += "; not placed: " + ", ".join(r["skipped"])
        if r["unmapped"]:
            msg += "; no cell for: " + ", ".join(r["unmapped"])
        if r["overlaps"] or r["outside"]:
            msg += f"; {len(r['overlaps'])} overlap(s), {len(r['outside'])} outside"
        return msg

    def _cmd_unusedbonds(self, args):
        """UNUSEDBONDS [LIST|REMOVE] -- wire bonds and fingers on die pads no
        net uses: list them (default), or take them off."""
        sub = (args[0].upper() if args else "LIST")
        if sub not in ("LIST", "REMOVE"):
            raise CommandError("usage: UNUSEDBONDS [LIST|REMOVE]")
        r = self.session.remove_unused_bonds(dry_run=(sub == "LIST"))
        if not r["dies"]:
            return "no unused bonds"
        detail = "; ".join(f"{ref}: {','.join(p)}" for ref, p in sorted(r["dies"].items()))
        verb = "unused" if sub == "LIST" else "removed"
        return f"{r['bonds']} {verb} bond(s): {detail}"

    def _cmd_iopads(self, args):
        """IOPADS [PLAN|APPLY] [w=<um>] [l=<um>] [inset=<um>] [csv=<file>] --
        optimise the I/O pinout; pad size from the I/O Pads rules unless given."""
        usage = ("usage: IOPADS [PLAN|APPLY] [w=<um>] [l=<um>] [inset=<um>] [csv=<file>] | "
                 "IOPADS BOND [all=1] [pins=<n,n,...>] [layer=<n>] [w=] [l=] [inset=] [margin=]")
        sub = (args[0].upper() if args and "=" not in args[0] else "PLAN")
        if sub == "BOND":
            kw = {}
            for a in args[1:]:
                k, eq, v = a.partition("=")
                k = k.lower()
                if not eq:
                    raise CommandError(usage)
                try:
                    if k == "all":
                        kw["all_pins"] = v.lower() not in ("0", "false", "no")
                    elif k == "pins":
                        kw["pins"] = [t for t in v.replace(";", ",").split(",") if t.strip()]
                    elif k == "layer":
                        kw["layer"] = int(v) if v.lstrip("-").isdigit() else v
                    elif k in ("w", "l", "inset", "margin"):
                        kw[{"w": "pad_w", "l": "pad_l", "inset": "bond_inset",
                            "margin": "edge_margin"}[k]] = float(v)
                    else:
                        raise CommandError(usage)
                except ValueError:
                    raise CommandError(usage)
            r = self.session.bond_header(**kw)
            out = f"header bonded: {r['pads']} pads + bonds on layer {r['layer']}"
            if r["skipped"]:
                out += "; skipped " + "; ".join(f"pin {p}: {w}" for p, w in r["skipped"].items())
            return out
        if sub not in ("PLAN", "APPLY"):
            raise CommandError(usage)
        opts = {}
        for a in args:
            k, eq, v = a.partition("=")
            if not eq:
                continue
            k = k.lower()
            try:
                if k == "w":
                    opts["pad_w"] = float(v)
                elif k == "l":
                    opts["pad_l"] = float(v)
                elif k == "inset":
                    opts["bond_inset"] = float(v)
                elif k == "csv":
                    opts["csv_path"] = v
                else:
                    raise CommandError(usage)
            except ValueError:
                raise CommandError(usage)
        p = self.session.assign_io_pads(apply=(sub == "APPLY"), **opts)
        moved = sum(1 for st in p["sites"] if st["net"] != st.get("was"))
        pins = ", ".join(f"{st['pin']}={st['net']}" for st in p["sites"])
        return (f"I/O pinout {'applied' if sub == 'APPLY' else 'plan'}: pad-to-die "
                f"{p['before'] / 1000:.0f} -> {p['after'] / 1000:.0f} mm, {moved} pin(s) change; {pins}")

    def _cmd_shorts(self, args):
        """SHORTS -- copper joining two or more named nets, and where."""
        shorts = self.session.find_shorts()
        if not shorts:
            return "no shorts: no copper joins two named nets"
        lines = []
        for x in shorts:
            where = ", ".join(f"{a},{b}" for a, b in x["at"][:4])
            lines.append(f"{' + '.join(x['nets'])}" + (f" meet at {where}" if where else ""))
        return f"{len(shorts)} short(s): " + "; ".join(lines)

    def _cmd_ripup(self, args):
        """RIPUP <x1,y1> <x2,y2> [route=0|1] [tol=<dbu>] -- rip up the track
        between two points of one net, then (route=1, the default, with a
        canvas) start routing again from the first point."""
        usage = "usage: RIPUP <x1,y1> <x2,y2> [route=0|1] [tol=<dbu>]"
        pts, route, tol = [], True, 0.0
        for a in args:
            k, eq, v = a.partition("=")
            if eq and k.lower() == "route":
                route = v.strip() not in ("0", "no", "false")
            elif eq and k.lower() == "tol":
                tol = float(v)
            else:
                pts.append(self._xy(a, usage))
        if len(pts) != 2:
            raise CommandError(usage)
        r = self.session.rip_up_between(pts[0], pts[1], tol=tol)
        if route and self.view is not None and hasattr(self.view, "start_reroute"):
            self.view.start_reroute(r["a"][:2], r["b"][:2], r["a"][2], net=r.get("net"))
        ax, ay, al = r["a"]
        bx, by, bl = r["b"]
        what = f"net {r['net']}" if r.get("net") else "track"
        return (f"ripped up {what}: {r['length'] / 1000:.2f} mm of track, {len(r['vias'])} via(s); "
                f"loose ends ({ax / 1000:.3f}, {ay / 1000:.3f}) mm on layer {al} and "
                f"({bx / 1000:.3f}, {by / 1000:.3f}) mm on layer {bl}")

    def _cmd_unrouted(self, args):
        """UNROUTED [<net>|NEXT|PREV|CLEAR] -- no argument: list the nets
        still to route; a net (or NEXT/PREV through the list): show its SLI
        thick and zoom to it; CLEAR: drop the pick."""
        d = self.session.sli()
        names = [n["net"] for n in d["nets"]]
        if not args:
            if not names:
                return f"no unrouted nets ({d.get('complete', 0)} complete)"
            return f"{len(names)} unrouted: " + ", ".join(
                f"{n['net']} ({len(n['segments'])})" for n in d["nets"])
        a = args[0]
        view = self.view
        if a.upper() == "CLEAR":
            if view is not None:
                view.focus_sli_net(None)
            return "unrouted net pick cleared"
        if a.upper() in ("NEXT", "PREV"):
            if not names:
                return "no unrouted nets"
            cur = getattr(view, "sli_focus", None)
            step = 1 if a.upper() == "NEXT" else -1
            i = names.index(cur) + step if cur in names else (0 if step > 0 else -1)
            net = names[i % len(names)]
        else:
            net = next((n for n in names if n.upper() == a.upper()), None)
            if net is None:
                raise CommandError(f"{a} is not an unrouted net" + (
                    f" (unrouted: {', '.join(names)})" if names else ""))
        entry = next(n for n in d["nets"] if n["net"] == net)
        if view is not None:
            view.focus_sli_net(net)
        return f"{net}: {len(entry['segments'])} gap(s), {entry['length'] / 1000:.2f} mm unrouted"

    def _cmd_sli(self, args):
        """SLI [ON|OFF] -- straight-line interconnects; no argument: report."""
        d = self.session.sli()
        if args and args[0].upper() in ("ON", "OFF"):
            if self.view is not None:
                self.view.show_sli = args[0].upper() == "ON"
                self.view.redraw()
        top = ", ".join(f"{n['net']} {n['length'] / 1000:.1f}" for n in d["nets"][:5])
        return (f"{len(d['nets'])} net(s), total {d['total'] / 1000:.1f} mm"
                + (f"; longest: {top}" if top else ""))

    def _cmd_solidcraft(self, args):
        usage = "usage: SOLIDCRAFT [window=0|1] [export=<file.step|3mf|stl|obj>]"
        window, export = True, None
        for a in args:
            k, _, v = a.partition("=")
            k = k.lower()
            if k == "window":
                window = v.lower() not in ("0", "no", "false")
            elif k == "export" and v:
                export = v
            else:
                raise CommandError(usage)
        if not window and not export:
            raise CommandError("nothing to do: window=0 and no export=")
        r = self.session.send_to_solidcraft(window=window, export=export)
        msg = f"SolidCraft: {r['bodies']} bodies built"
        if r.get("export"):
            msg += f", wrote {r['export']}"
        if r["errors"]:
            msg += "; problems: " + "; ".join(r["errors"])
        return msg

    def _cmd_freeroute(self, args):
        usage = ("usage: FREEROUTE EXPORT <file.dsn> | FREEROUTE IMPORT <file.ses> [keep=0|1] | "
                 "FREEROUTE RUN [passes=<n>] [keep=0|1] [fresh=0|1] [layers=auto|single|two] "
                 "[finger=<um>] [fingerlayer=1|2] [optpasses=<n>] [optstop=<%>] "
                 "[directions=hv|vh|off] [viaopt=0|1] [viacost=<n>] [viaflip=<um>] [shorten=0|1] "
                 "[dejog=0|1] [maxjog=<um>] | "
                 "FREEROUTE VIAOPT [viaflip=<um>] | FREEROUTE SHORTEN | FREEROUTE DEJOG [maxjog=<um>]")
        if not args:
            raise CommandError(usage)
        sub = args[0].upper()
        opts = {}
        pos = []
        for a in args[1:]:
            if "=" in a:
                k, v = a.split("=", 1)
                k = k.lower()
                if k == "keep":
                    opts["keep_existing"] = v.lower() not in ("0", "no", "false")
                elif k == "passes":
                    opts["passes"] = int(v)
                elif k in ("optpasses", "optimize"):
                    opts["optimize_passes"] = int(v)
                elif k == "optstop":
                    opts["optimize_threshold"] = float(v)
                elif k == "fresh":
                    opts["remove_existing"] = v.lower() not in ("0", "no", "false")
                elif k == "layers":
                    if v.lower() not in ("auto", "single", "two"):
                        raise CommandError("layers= must be auto, single or two")
                    opts["layer_mode"] = v.lower()
                elif k == "finger":
                    opts["bond_finger"] = float(v)
                elif k == "fingerlayer":
                    if v.lower() not in ("1", "2", "first", "second"):
                        raise CommandError("fingerlayer= must be 1 or 2")
                    opts["finger_layer"] = "second" if v.lower() in ("2", "second") else "first"
                elif k == "directions":
                    if v.lower() not in ("hv", "vh", "off"):
                        raise CommandError("directions= must be hv (1st conductor horizontal), "
                                           "vh or off")
                    opts["layer_directions"] = v.lower()
                elif k == "viaopt":
                    opts["via_optimize"] = v.lower() not in ("0", "no", "false")
                elif k == "viacost":
                    opts["via_cost"] = int(v)
                elif k == "viaflip":
                    opts["via_optimize_max_flip"] = float(v)
                elif k == "shorten":
                    opts["shorten_tracks"] = v.lower() not in ("0", "no", "false")
                elif k == "dejog":
                    opts["remove_jogs"] = v.lower() not in ("0", "no", "false")
                elif k == "maxjog":
                    opts["max_jog"] = float(v)
                else:
                    raise CommandError(f"unknown FREEROUTE option {k!r}")
            else:
                pos.append(a)
        if sub == "DEJOG" and not pos:
            r = self.session.remove_jogs(**opts)
            if r.get("undone"):
                return "jogs left in: removing them would have joined " + \
                    "; ".join(" + ".join(x["nets"]) for x in r["shorts"])
            return (f"removed {r['jogs']} of {r['found']} jog(s) in {r['tracks']} track(s)"
                    + (" -- left: " + ", ".join(f"{k} {v}" for k, v in r["why"].items())
                       if r["why"] else ""))
        if sub == "SHORTEN" and not pos:
            r = self.session.shorten_tracks(**opts)
            return (f"shortened {r['tracks']} track(s): {r['before'] / 1000:.1f} -> "
                    f"{r['after'] / 1000:.1f} mm")
        if sub == "VIAOPT" and not pos:
            r = self.session.optimize_vias(**opts)
            return (f"via optimisation: {r['before']} -> {r['after']} via(s), "
                    f"{r['moved']} track(s) moved to the other conductor")
        if sub == "EXPORT" and len(pos) == 1:
            sm = self.session.export_dsn(pos[0], **opts)
            return (f"wrote {pos[0]}: {sm.components} part(s), {sm.pins} pad(s), {sm.nets} net(s), "
                    f"{sm.wires} track(s), {sm.vias} via(s)")
        if sub in ("IMPORT", "RUN"):
            if sub == "IMPORT":
                if len(pos) != 1:
                    raise CommandError(usage)
                st = self.session.import_ses(pos[0], **opts)
            else:
                st = self.session.freeroute(**opts)
            msg = (f"{st['wires']} track(s) and {st['vias']} via(s) added "
                   f"({st['skipped']} already there); nets complete {st['complete']}/{st['nets']}")
            if st.get("fingers"):
                msg += f"; {st['fingers']} bond finger(s) laid"
            if st.get("widened"):
                msg += f"; {st['widened']} necked track(s) widened back to the minimum width"
            if "vias_before" in st:
                msg += f"; via optimisation {st['vias_before']} -> {st['vias_after']} via(s)"
            if st["unrouted"]:
                msg += "\n  unrouted: " + ", ".join(st["unrouted"])
            return msg
        raise CommandError(usage)

    def _cmd_hybrid(self, args):
        usage = ("usage: HYBRID IMPORT <file.routed.json> [cell=<name>] [fill=0|1] | "
                 "HYBRID BONDS [<file.csv>]")
        if not args:
            raise CommandError(usage)
        sub = args[0].upper()
        if sub == "IMPORT" and len(args) >= 2:
            kw = {}
            for a in args[2:]:
                k, _, v = a.partition("=")
                k = k.lower()
                if k == "cell" and v:
                    kw["cell_name"] = v
                elif k == "fill":
                    kw["dielectric_fill"] = v.lower() not in ("0", "no", "false")
                else:
                    raise CommandError(f"unknown HYBRID IMPORT option {a!r}")
            st = self.session.import_routed_hybrid(args[1], **kw)
            msg = (f"imported {st['cell']!r}: {st['dies']} die(s), {st['components']} part(s), "
                   f"{st['pads']} pad(s) ({st['fingers']} fingers, {st['jumper_lands']} jumper "
                   f"lands), {st['traces']} trace(s), {st['windows']} window(s), {st['wires']} "
                   f"wire(s) ({st['jumpers']} jumpers)")
            if st["unrouted"]:
                msg += f"\n  router left {len(st['unrouted'])} connection(s) unrouted"
            if st["issues"]:
                msg += f"\n  router reported {st['issues']} post-route DRC issue(s)"
            return msg
        if sub == "BONDS":
            if len(args) >= 2:
                n = self.session.export_bond_diagram(args[1])
                return f"wrote bond diagram ({n} wires) to {args[1]}"
            rows = self.session.bond_diagram()
            if not rows:
                return "no bond diagram -- HYBRID IMPORT a routed result first"
            lines = [f"{r['seq']:>3} {r['wire']:<6} {str(r.get('net') or ''):<10} "
                     f"{r['first_bond']:<10} -> {r['second_bond']:<10} "
                     f"{(r.get('length_um') or 0) / 1000.0:6.2f} mm  {r['purpose']}"
                     for r in rows]
            return "\n".join(lines)
        raise CommandError(usage)

    def _xy(self, s, usage):
        try:
            x, y = s.split(",")
            return float(x), float(y)
        except ValueError:
            raise CommandError(usage)

    def _cmd_movefinger(self, args):
        usage = "usage: MOVEFINGER <pad|x,y> <x,y>"
        if len(args) != 2:
            raise CommandError(usage)
        to = self._xy(args[1], usage)
        if "," in args[0]:
            at = self._xy(args[0], usage)
        else:
            cell = self.session.library.get_cell(self.session.current_cell_name)
            from ..core.shapes import Rectangle
            pad = next((sh for sh in cell.shapes if isinstance(sh, Rectangle)
                        and getattr(sh, "pin", "") == args[0]), None)
            if pad is None:
                raise CommandError(f"no pad {args[0]!r} in this cell")
            x0, y0, x1, y1 = pad.bbox()
            at = ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        r = self.session.move_bond_finger(at, to)
        msg = f"finger {r['pad']} moved {r['from']} -> {r['to']}"
        for row in r["rows"]:
            msg += f"\n  {row.get('wire')}: {row['length_um'] / 1000:.2f} mm"
        for w in r["warnings"]:
            msg += f"\n  WARNING {w}"
        if not r["on_trace"]:
            msg += "\n  off its trace: reroute the net"
        return msg

    def _cmd_resize(self, args):
        usage = "usage: RESIZE <refdes> [length=<um>] [width=<um>] [preview=1]"
        if not args:
            raise CommandError(usage)
        kw, dry = {}, False
        for a in args[1:]:
            k, _, v = a.partition("=")
            k = k.lower()
            try:
                if k in ("length", "width"):
                    kw[k] = float(v)
                elif k == "preview":
                    dry = v.lower() not in ("0", "no", "false")
                else:
                    raise CommandError(f"unknown RESIZE option {k!r}")
            except ValueError:
                raise CommandError(usage)
        if not kw:
            info = self.session.resistor_info(args[0])
            from ..io.hybrid_routed import format_ohms
            return (f"{args[0]}: L {info['length_um']} um, W {info['width_um']} um, "
                    f"{info['squares']:.2f} sq, R {format_ohms(info['value_ohms'])}, "
                    f"Rs {format_ohms(info['sheet_ohms'])}/sq")
        r = self.session.resize_resistor(args[0], dry_run=dry, **kw)
        return ("(preview) " if dry else "") + r["text"]

    def _cmd_bondinfo(self, args):
        usage = "usage: BONDINFO <x,y>"
        if len(args) != 1:
            raise CommandError(usage)
        info = self.session.bond_wire_info(self._xy(args[0], usage), tol=200.0)
        return info["text"] if info else "no wire bond there"

    def _cmd_bondtable(self, args):
        usage = ("usage: BONDTABLE <cell_name> <layer> [origin=<x,y>] [rowheight=<dbu>] "
                 "[textheight=<dbu>]")
        pos = [a for a in args if "=" not in a]
        if len(pos) != 2:
            raise CommandError(usage)
        kw = {}
        for a in args:
            if "=" not in a:
                continue
            k, _, v = a.partition("=")
            k = k.lower()
            try:
                if k == "origin":
                    x, y = v.split(",")
                    kw["origin"] = (float(x), float(y))
                elif k == "rowheight":
                    kw["row_height"] = float(v)
                elif k == "textheight":
                    kw["text_height"] = float(v)
                else:
                    raise CommandError(f"unknown BONDTABLE option {k!r}")
            except ValueError:
                raise CommandError(usage)
        layer = int(pos[1]) if pos[1].lstrip("-").isdigit() else pos[1]
        name = self.session.generate_bond_table(pos[0], layer, **kw)
        return f"generated bond diagram table cell {name!r}"

    def _cmd_netnames(self, args):
        usage = "usage: NETNAMES IMPORT <file.csv> [merge=1] | NETNAMES CLEAR | NETNAMES"
        from ..io.techfile import load_net_names, save_net_names
        if not args:
            names = load_net_names(self.session.library)
            return f"{len(names)} named pad(s) in {len(set(names.values()))} net(s)"
        sub = args[0].upper()
        if sub == "CLEAR":
            save_net_names(self.session.library, {})
            return "net names cleared"
        if sub == "IMPORT" and len(args) >= 2:
            merge = any(a.lower() in ("merge=1", "merge=yes", "merge=true") for a in args[2:])
            n = self.session.import_net_names(args[1], replace=not merge)
            return f"{n} named pad(s) stored"
        raise CommandError(usage)

    def _cmd_hilite(self, args):
        usage = ("usage: HILITE <x,y> | <RefDes.Pin> | OFF [layers=<a,b,...>] "
                 "[cell=<name>]")
        if not args:
            raise CommandError(usage)
        if args[0].upper() in ("OFF", "CLEAR", "NONE"):
            self.session.clear_net_highlight()
            return "net highlight cleared"
        kwargs = {}
        target = None
        for a in args:
            if "=" in a:
                k, v = a.split("=", 1)
                k = k.lower()
                if k == "layers":
                    kwargs["layers"] = [int(x) if x.lstrip("-").isdigit() else x
                                        for x in v.split(",") if x]
                elif k == "cell":
                    kwargs["cell_name"] = v
                else:
                    raise CommandError(f"unknown HILITE option {k!r}")
            elif target is None:
                target = a
            else:
                raise CommandError(usage)
        if target is None:
            raise CommandError(usage)
        if "," in target:                 # x,y -- else a pad name like U1.5
            try:
                target = _parse_point(target)
            except Exception:
                pass
        tr = self.session.highlight_net(target, **kwargs)
        return net_summary(tr)

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
    def _cmd_thermal(self, args):
        usage = ("usage: THERMAL PARTS | POWER <ref_des> <mW> | SET <key> <value> | "
                 "MATERIAL <layer> <material|k> | MATERIALS | RUN [cell=] [grid=] [base=] | "
                 "REPORT out=<path.html> [csv=<path.csv>] | CONTOURS <layer> [step=] [slice=]")
        if not args:
            raise CommandError(usage)
        sub, rest = args[0].upper(), args[1:]
        from ..io.techfile import DEFAULT_THERMAL, load_thermal_rules, save_thermal_rules

        def keyed(items):
            opts, plain = {}, []
            for a in items:
                if "=" in a:
                    k, v = a.split("=", 1)
                    opts[k.lower()] = v
                else:
                    plain.append(a)
            return opts, plain

        if sub == "PARTS":
            opts, _ = keyed(rest)
            parts = self.session.thermal_parts(opts.get("cell"))
            if not parts:
                return "thermal: no resistors or components found"
            lines = [f"thermal: {len(parts)} part(s)"]
            for p in parts:
                src = f" ({p['source']})" if p["source"] else ""
                lines.append(f"  {p['ref_des']:<12} {p['kind']:<9} {p['power_mw']:8.1f} mW{src}"
                             f"  [{p['cell']}]")
            return "\n".join(lines)
        if sub == "POWER":
            if len(rest) != 2:
                raise CommandError("usage: THERMAL POWER <ref_des> <mW|CLEAR>")
            if rest[1].upper() == "CLEAR":
                self.session.set_thermal_power(rest[0], None)
                return f"thermal: power for {rest[0]} cleared"
            try:
                mw = float(rest[1])
            except ValueError:
                raise CommandError("usage: THERMAL POWER <ref_des> <mW|CLEAR>")
            self.session.set_thermal_power(rest[0], mw)
            return f"thermal: {rest[0]} = {mw:g} mW"
        if sub == "SET":
            if len(rest) != 2:
                raise CommandError("usage: THERMAL SET <key> <value>")
            key = rest[0].lower()
            section = next((s for s in ("boundary", "limits") if key in DEFAULT_THERMAL[s]),
                           None)
            if section is None:
                raise CommandError(f"unknown thermal setting {rest[0]!r}; one of: "
                                   + ", ".join(list(DEFAULT_THERMAL["boundary"])
                                               + list(DEFAULT_THERMAL["limits"])))
            value = rest[1]
            if key == "substrate_layer":
                value = self.session.library.layers.resolve(value) \
                    if value not in ("-1", "auto") else -1
            elif key == "base_mode" and value.lower() not in ("fixed", "htc"):
                raise CommandError("base_mode must be fixed or htc")
            elif key in ("substrate_material", "mount_material") and value:
                from ..core.thermal import material_k
                material_k(value)
            rules = load_thermal_rules(self.session.library)
            rules[section][key] = value.lower() if key == "base_mode" else value
            save_thermal_rules(self.session.library, rules)
            return f"thermal: {key} = {load_thermal_rules(self.session.library)[section][key]}"
        if sub == "MATERIALS":
            from ..core.thermal import MATERIALS
            return "\n".join(["thermal materials (W/m.K):"]
                             + [f"  {name:<14} {m['k']:>7g}   {m['label']}"
                                for name, m in MATERIALS.items()])
        if sub == "MATERIAL":
            if len(rest) != 2:
                raise CommandError("usage: THERMAL MATERIAL <layer> <material|k|CLEAR>")
            n = self.session.library.layers.resolve(rest[0])
            rules = load_thermal_rules(self.session.library)
            if rest[1].upper() == "CLEAR":
                rules["materials"].pop(str(n), None)
                save_thermal_rules(self.session.library, rules)
                return f"thermal: layer {n} back to its role default"
            from ..core.thermal import material_k
            k = material_k(rest[1])
            rules["materials"][str(n)] = rest[1].lower()
            save_thermal_rules(self.session.library, rules)
            return f"thermal: layer {n} = {rest[1].lower()} ({k:g} W/m.K)"
        if sub == "RUN":
            opts, plain = keyed(rest)
            if plain or set(opts) - {"cell", "grid", "base"}:
                raise CommandError("usage: THERMAL RUN [cell=<name>] [grid=<dbu>] [base=<degC>]")
            try:
                grid = float(opts["grid"]) if "grid" in opts else None
                base = float(opts["base"]) if "base" in opts else None
            except ValueError:
                raise CommandError("grid= and base= must be numbers")
            result = self.session.run_thermal(opts.get("cell"), grid=grid, base_temp=base)
            return "\n".join(result.summary_lines())
        if sub in ("REPORT", "CONTOURS"):
            result = getattr(self.session, "last_thermal_result", None)
            if result is None:
                raise CommandError("no thermal result yet -- THERMAL RUN first")
            opts, plain = keyed(rest)
            if sub == "REPORT":
                if "out" not in opts and "csv" not in opts:
                    raise CommandError("usage: THERMAL REPORT out=<path.html> [csv=<path.csv>]")
                done = []
                if "out" in opts:
                    done.append(self.session.export_thermal_report(result, opts["out"]))
                if "csv" in opts:
                    done.append(self.session.export_thermal_csv(result, opts["csv"]))
                return "thermal: wrote " + ", ".join(done)
            if len(plain) != 1:
                raise CommandError("usage: THERMAL CONTOURS <layer> [step=<degC>] [slice=<name>]")
            try:
                step = float(opts.get("step", 5.0))
            except ValueError:
                raise CommandError("step= must be a number")
            count = self.session.draw_thermal_contours(
                result, plain[0], step=step, which=opts.get("slice"))
            if count == 0:
                import numpy as np
                from ..core.thermal import nice_contour_step
                which = opts.get("slice")
                grid = result.surface_temps() if which is None else result.slice_temps(which)
                if not np.isfinite(grid).any():
                    return "thermal: that slice has no material, nothing to draw"
                lo, hi = float(np.nanmin(grid)), float(np.nanmax(grid))
                return (f"thermal: no contours drawn -- temperatures only span {lo:.2f} to "
                        f"{hi:.2f} C, less than one {step:g} C step; try "
                        f"step={nice_contour_step(lo, hi):g}")
            return f"thermal: drew {count} contour path(s) every {step:g} C"
        raise CommandError(usage)

    def _cmd_py(self, args):
        code_str = " ".join(args)
        return self.console.run(code_str)

    def _cmd_help(self, args):
        return COMMAND_REFERENCE


def _stamp_str(row) -> str:
    """'created 2026-11-02 13:05:07  modified ...' from a cell row dict."""
    c, m = row.get("created"), row.get("modified")
    if not c and not m:
        return "not saved yet"
    return f"created {(c or '?').replace('T', ' ')}  modified {(m or '?').replace('T', ' ')}"
