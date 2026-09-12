# HLayout — Thick & Thin Film Hybrid Layout CAD Tool

A layout editor for thick-and-thin-film hybrid microelectronics, built to
the requirements captured in `spec.odt`:

- Database units: **1 micron (0.001 mm)** — matches the database unit
  used by most external GDSII libraries, so attaching one (see below)
  loses no precision
- Max database size: **500 x 500 mm**
- Default file format: **GDSII**
- **64 layers**, each with a colour, one of **9 stipple** fill patterns
  (or **No Fill** for outline-only rendering), and a user-assignable
  **name** — refer to a layer by number or by name anywhere a layer is
  expected (draw toolbar, command bar, scripting)
- Primitives: closed **polygon** (up to 200 points), **path** (up to 200
  points, width, round/square end caps), **rectangle** (4 points),
  **circle** (drawn as a regular polygon, user-chosen segment count —
  8, 16, 32, or 64), **text** (origin, size, selectable font), **Sref**
  (cell instance, containing data on any layer)
- Window commands: major/minor dot grid, fit view, zoom in/out, multiple
  view windows
- **Python scripting** to read and write layout data
- Up to **2 attached reference libraries** (other GDSII files) to search
  and place cells/Srefs from, plus the ability to save the current
  design out under a chosen library name for reuse as one

## Install

```
pip install -r requirements.txt
```

The GUI uses **Tkinter**, which ships with the standard Python installer
on Windows and macOS. On Debian/Ubuntu Linux you may need
`sudo apt install python3-tk`.

### Prebuilt installers (Windows / Linux / macOS)

Pushing a version tag (`v0.1.0`, `v0.2.0`, ...) triggers
`.github/workflows/build-installers.yml`, which builds a Windows
installer (`.exe`, via PyInstaller + Inno Setup), a Debian package
(`.deb`), and a macOS `.dmg` on GitHub's own native runners for each
OS, then attaches all three to a GitHub Release:

```
git tag v0.1.0
git push origin v0.1.0
```

You can also trigger a build without cutting a release from the
Actions tab ("Build installers" → Run workflow) to sanity-check a
change; the resulting installers are attached to that run as
downloadable artifacts instead of a release.

These are unsigned builds -- Windows SmartScreen and macOS Gatekeeper
will warn on first launch ("Windows protected your PC" → More info →
Run anyway; on macOS, right-click the app → Open). See
`packaging/` for the PyInstaller spec, Inno Setup script, and `.deb`
packaging used by the workflow.

## Run the GUI

```
python hlayout_app.py                # blank library
python hlayout_app.py mydesign.gds   # open an existing GDSII file
```

Every font in the app — menus, buttons, labels, the layers table,
dialogs — is set to a consistent 14pt (`App.UI_FONT_SIZE`) rather than
each widget's platform default, which otherwise varies in size and
looks inconsistent across widgets. This doesn't affect the size of
design text drawn on the canvas (the Text shape primitive) — that's a
DBU height you set per shape, unrelated to UI chrome.

**Info/error/warning/confirmation popups** (Export Gerber's summary,
DRC's "add at least one rule" message, the Resistor Designer's various
warnings, etc.) are built by `hlayout/gui/dialogs.py`, not
`tkinter.messagebox`. On Windows, `tkinter.messagebox` calls the
native Win32 `MessageBox()` dialog, which — like the menu bar case
below — is drawn by the OS itself and ignores every Tk font setting
entirely; confirmed live to still render at a small system font even
after every other dialog and the main window had already been resized
to 14pt (most visibly on the Export Gerber summary, which lists the
files just written). `dialogs.show_info()`/`show_error()`/
`show_warning()`/`ask_yes_no()` are plain ttk-built windows instead,
so they pick up `TkDefaultFont` — and therefore `UI_FONT_SIZE` — the
same way every other widget in the app already does.

The top-level menu **bar** itself (File, Setup, View, ...) is not a
native OS menu bar (`self.config(menu=...)`); it's an ordinary row of
buttons (`App._build_menu`), each opening a normal dropdown menu. This
is deliberate: on Windows, a `tk.Menu` installed as a window's native
system menu is drawn by the OS itself, which ignores Tk's font
entirely for that bar — no amount of resizing the shared font object,
or passing an explicit `font=...` to that menu, changes its rendered
size there (confirmed still too small after an earlier attempt at
exactly that fix). A dropdown opened from an ordinary button, by
contrast, is Tk-drawn on every platform including Windows, so its font
size is fully under this app's control — which is what makes the
whole bar, not just the dropdowns, reliably render at 14pt everywhere.

### Menu bar

The top-level menu bar is organized into eight menus, and every command
in the app (toolbar buttons, command bar equivalents, etc.) has a
home under one of them:

- **File** — New, Open GDS..., Save GDS..., Save As..., Save As
  Library..., Archive Design..., Export Gerber..., Print... (`Ctrl+P`),
  Exit. Pure file I/O for the design itself; library-attach and
  cell-placement live under Setup/Create instead (see below).
- **Setup** — Attach Library..., Attached Libraries..., Layers... (jumps
  to the Layers tab), Snap to Grid (checkbox), Path Style... (a small
  dialog to set the width and end cap used when drawing new paths —
  see below), Circle Segments... (a small dialog to set how many
  segments the Circle draw tool uses — 8/16/32/64 — since it's the one
  draw-tool option that only applies to a single tool), Route Layers...
  (configures the Route (2-Layer) tool's two conductor layers and via
  style — see Two-layer Route tool, below), Resistor Geometry Rules...
  (configures the A/B/C overlap/pad/space dimensions and the two layers
  used when a sized resistor is actually drawn into the layout — see
  Thick-Film Resistor Designer, below), Remap Layers... (bulk-move
  shapes from one layer to another across the library — see Remap
  layers, below), Load Technology File... and Save Technology File...
  (see Technology files, below).
- **View** — Fit, Zoom In, Zoom Out, Toggle Grid, New View Window.
- **Create** — the six draw tools (Rectangle, Polygon, Path, Circle,
  Text, Cell Instance), the Route (2-Layer) tool (see Two-layer Route
  tool, below), plus Find && Place Cell... This is the only place to
  pick a draw tool now — there's no separate toolbar row of the same
  buttons any more (see GUI layout, below).
- **Edit** — Undo and Redo (see Undo and redo, below; greyed out when
  there's nothing to undo/redo), Select (mode), Edit Selected... (opens
  the same property dialog as double-clicking a shape), Delete
  Selected, Rotate..., and Copy... (see Selection highlight, Rotate,
  and Copy, below — repositioning itself is mouse-only now, via drag or
  window-select, with no Move... dialog), and Properties (jumps to the
  read-only Properties tab — see Properties panel, below).
- **Tools** — Script Console (jumps to that tab), Run Script File...
  (browse for and execute a `.py` file against the current session),
  Command Bar (focuses the command line), Thick-Film Resistor
  Designer... (opens the resistor sizing tool — see Thick-Film
  Resistor Designer, below), Via Wizard... (creates a via cell and
  saves it into an attached library — see Via Wizard, below), Mask
  Operations... (layer-based Oversize/Undersize/AND/OR/NOT/XOR — see
  Layer-based mask operations, below), DRC... (single-layer and
  between-layer design rule checks — see Design Rule Check, below),
  Export Gerber... (same as File > Export Gerber... — see Export
  Gerber, below).
- **Help** — User Manual: opens `docs/HLayout_User_Manual.docx` (this
  walkthrough, organized by what you're trying to do) with whatever's
  registered to handle `.docx` on this machine. Command Reference
  (also `F1`): opens `docs/help.html` — the exhaustive, searchable
  command-bar/Python API syntax reference — in the default browser,
  falling back to a scrollable plain-text dialog if that file can't be
  found.
- **About** — a single direct item (not a submenu) with the app name,
  a short description, and the version number.

Common actions also have keyboard accelerators: `Ctrl+N`/`Ctrl+O`/
`Ctrl+S` for New/Open/Save, `Ctrl+Shift+S` for Save As..., `Ctrl+P` for
Print..., `F` to fit the view, `G` to toggle the grid, `F1` for the
command reference. In Select mode, the arrow keys nudge the current
selection by one grid unit (`R` rotates it 90 degrees).

### GUI layout

- **Canvas** (left): the layout view, starting right below the menu
  bar. Middle-drag or right-drag to pan, mouse wheel to zoom.
- **Top toolbar** (right under the menu bar): the Cell selector, the
  target Layer (pick from the dropdown, or type a layer number or name
  directly), and an **Auto-save (15 min)** switch — turn it on to save
  the design (a plain Save GDS..., .gds + .hlaux sidecar) automatically
  every 15 minutes while it's open. It's a per-session setting only
  (off by default, not remembered across reopening the app) and skips
  silently — noted on the status line, switch left on — until the
  design has been saved at least once by hand, since that's what gives
  it a location to write to.
- **Bottom toolbar** (above the status bar and Command bar): Fit,
  Select, Zoom Win, Grid, New View, a Snap to grid toggle, and Delete
  Selected. There's no separate draw-tool button row any more —
  picking a draw tool (Select, Rect, Polygon, Path, Circle, Text, Cell
  Instance) is done from the **Create** and **Edit** menus (see Menu
  bar, above), which used to duplicate a row of buttons here. Path
  width/end cap and circle segment count live in
  **Setup > Path Style...** and **Setup > Circle Segments...** instead
  of on this toolbar (see below) — both are options that only apply to
  a single draw tool, unlike Snap to grid.
- **Layers tab** (right): an **All On** / **All Off** button pair above
  the table to toggle every layer's visibility at once, then all 64
  layers with visibility checkbox, an editable **name** field, colour
  swatch (click to change), a **Fill** checkbox (uncheck for
  outline-only rendering — no interior fill or stipple), and stipple
  pattern (greyed out when Fill is off). Rename a layer here (or via
  `LAYER n name=...` / scripting) and it's usable by that name
  everywhere else in the tool. **All On**/**All Off** only set every
  layer's visibility once, as a convenience starting point — they
  don't lock the per-layer checkboxes afterwards. After **All Off**,
  checking one or more individual layers back on (in this table, or
  via `LAYER <n> visible=1` from the command bar/a script) works
  exactly as it would from any other starting state.
- **Script Console tab** (right): a live prompt bound to the current
  session, accepting the *same short commands as the Command bar*
  (`RECT`, `CELL`, `LAYER`, `HELP`, ...) as well as raw Python — see
  Scripting, below. History (`Up`/`Down`) is shared with the Command
  bar.
- **Command bar** (bottom): a text command line — type a command and
  press Enter. Anything that isn't a recognised command is passed
  straight to the Python console, so you can also type Python
  directly here.
- **Cell selector** (toolbar): switch which cell the canvas displays.
- **Setup > Path Style...**: a small dialog to set the Width and End
  cap (round/square/flush) used for new paths — applies immediately to
  the current drawing session, same as the old toolbar fields did.
- **Help > Command Reference** (also `F1`): the full categorized
  command bar reference in a scrollable dialog — the same text `HELP`
  prints in the command bar or Script Console.

Both the Script Console entry and the Command bar support the same
line-editing keys as a Windows console (PowerShell/cmd.exe), on top of
Tk's normal Left/Right/Backspace/Delete:

| Key | Action |
| --- | --- |
| `Home` / `End` | jump to start / end of line |
| `Ctrl+A` | select the whole line |
| `Ctrl+Left` / `Ctrl+Right` | jump left/right by one word |
| `Ctrl+Backspace` / `Ctrl+Delete` | delete the previous/next word |
| `Escape` | clear the line |
| `Up` / `Down` | recall previous/next entry from this line's history |

### Drawing and editing with the mouse

Pick a tool from the **Create** or **Edit** menu, then work directly on the canvas
(DBU coordinates come from wherever you click; enable Snap to grid to
lock clicks to the 1 mm grid). With Snap to grid on, a small cyan
crosshair marks exactly where the next click will land — the cursor
itself follows your mouse normally (moving the real OS pointer would be
jarring), but the crosshair jumps to the nearest grid point so you
always know the coordinate you're about to place before you click:

- **Rect**: click the first corner, then the opposite corner.
- **Polygon** / **Path**: click each point in turn; double-click or
  press Enter to finish (needs 3+ points for a polygon, 2+ for a path);
  Escape cancels the shape in progress; Backspace removes the last
  point placed. Path width/cap come from Setup > Path Style..., and while
  drawing you see a "ghost" of the actual path width (a faint thick
  line under the thin centreline) so you can judge the real width
  before committing it.
- **Circle**: click the center, then click a point on the edge to set
  the radius. Stored as a regular polygon with the segment count
  chosen in **Setup > Circle Segments...** (8, 16, 32, or 64) — GDSII
  has no native circle primitive, so this is the standard way to
  represent one.
- **Text**: click the insertion point, then fill in the text and height
  in the popup.
- **Cell Instance**: click the placement point, then pick which cell to
  place (and optionally its rotation) in the popup. The picker lists
  cells from the current design *and every attached library* (Setup >
  Attach Library...) — picking one from an attached library imports it
  (and its dependency closure) the same way Create > Find && Place Cell
  does, rather than just the current design's own cells.
  Placing a cell instance that would create a cyclic reference — a cell
  instancing itself directly, or instancing another cell that
  (transitively) already contains an instance of it — is rejected up
  front with a clear error message instead of being allowed to
  silently corrupt the design (which used to only surface later, as a
  crash, when the view was re-fit or the file was saved). This applies
  everywhere an instance gets placed: **Create > Cell Instance**,
  **Create > Find && Place Cell...**, and the `SREF` command.
- **Select**: click a shape to select it and drag to move it, or press
  Delete (or "Delete Selected") to remove it. Editing is precise about
  *what* you clicked:
  - Click a **vertex** of a polygon or path — shown as a yellow circle
    handle — and only that vertex moves when you drag; Delete removes
    just that point (refused if it would drop the shape below its
    minimum point count).
  - Click an **edge** of a polygon (or a path segment between two
    vertices) — shown as a thick yellow line — and dragging moves only
    that edge's two endpoints, reshaping the segment without touching
    the rest of the shape.
  - Click elsewhere on a shape (its fill, or a rectangle/text/cell
    instance, none of which support vertex/edge editing) and dragging
    moves the whole object; double-click anywhere on a shape opens a
    property-edit dialog (layer, coordinates, width/cap, text/height/
    font, or rotation/magnification/mirror, depending on the shape).

Note: Select currently only picks up shapes and cell instances that
belong directly to the cell you're viewing — not shapes nested several
levels down inside a referenced cell. Vertex/edge editing applies to
polygons and paths only; rectangles keep their 4-point shape and are
reshaped via the edit dialog instead, to avoid accidentally turning a
rectangle into an arbitrary quadrilateral.

### Window-select (marquee multi-select)

Dragging on **empty canvas** in Select mode (instead of clicking on a
shape) draws a dashed selection rectangle — release the mouse and
every shape or cell instance *fully enclosed* inside it becomes
selected together (a shape only partly inside the box is left out).
Each selected member is outlined the same way a single selection is.
From there:

- **Dragging any one of the selected shapes** moves the whole group
  together, keeping their relative positions.
- **Delete** (or Delete Selected) removes every selected member.
- **Edit > Rotate...** and **Edit > Copy...** (and the `ROTATE`/`COPY`
  command-bar commands) act on the whole group — each member rotates
  about its own centroid/origin, and Copy duplicates every member at
  the given offset, leaving the originals untouched.
- **Edit > Edit Selected...** only works when a *single* object is
  window-selected (editing several objects' individual properties at
  once isn't well-defined) — with more than one selected it asks you
  to pick just one instead.

Clicking empty canvas without dragging (or clicking a shape not in the
current group) clears the window-selection, same as a plain click
always has.

### Selection highlight, Rotate, and Copy

Whatever is currently selected — a shape, a cell instance, a single
vertex/edge, or a window-selected group — is redrawn in white instead
of its own layer colour, so it's always visually unambiguous what a
drag, Delete, Rotate, or Copy is about to act on. A selected polygon or
rectangle draws as a white **outline box** only — never a solid white
fill, even on a filled/stippled layer — so a large selected shape
doesn't hide everything underneath it; a selected path or text label
draws in white as usual (a path is already just a stroke, not a fill).

Repositioning a selection is purely mouse-driven now: drag it (or, for
a window-selected group, drag any one of its members) — there is no
numeric x,y move dialog. Rotating and copying still need a number (an
angle, an offset) that isn't naturally expressed with the mouse, so
those stay as dialogs:

- **Edit > Rotate...** prompts for an angle in degrees (counter-
  clockwise) and rotates the selection about its own centroid (for a
  polygon, rectangle, or path) or about its origin (for a cell instance
  or text label, via its `rotation` field). A selected vertex or edge
  can't be rotated on its own and is rejected with a message asking you
  to select the whole shape instead.
- **Edit > Copy...** prompts for a `dx,dy` offset and duplicates the
  selected shape or cell instance (or every member of a window-selected
  group) into the same cell at that offset — the original(s) are left
  untouched, and the new copy/copies become the current selection, so a
  follow-up drag/Rotate/Copy acts on the copy. Like Rotate, a single
  vertex or edge can't be copied on its own and is rejected with a
  message asking you to select the whole shape instead.

The same two operations are available from the command bar / Script
Console as `ROTATE <degrees>` and `COPY <dx,dy>` — see Command bar
reference, below. A precise numeric move is still available from
there too, as `MOVE <dx,dy>`, for scripting/automation use even though
the GUI no longer has a Move... dialog of its own. All three require
something to already be selected (via the Select tool) and report an
error otherwise.

### Properties panel

The **Properties** tab (next to Layers and Script Console on the right,
or **Edit > Properties** to jump straight to it) shows a live,
read-only summary of whatever is currently selected — updated
automatically on every click, window-select, or edit, with no need to
open a dialog first. What it reports depends on the kind of thing
selected:

- **Path** — layer (number and name), width, cap style, point count.
- **Polygon** / **Rectangle** — layer (number and name), point count
  (polygon) or corner coordinates and size (rectangle).
- **Text** — layer, the text string, height, font index, rotation.
- **Cell Instance** (sref) — the referenced cell's name, origin,
  rotation, magnification, and whether it's mirrored.
- A selected **vertex** or **edge** reports its own point(s)/index
  alongside a summary of the shape it belongs to.
- A **window-selected group** (see Window-select, above) lists every
  member, one line each (capped at 25, with a "...and N more" note
  past that so nothing is silently hidden).
- Nothing selected: "Nothing selected."

The same information is available from the command bar / Script
Console as the `PROPS` command, sharing one implementation with the
panel so the two never disagree.

### Undo and redo

**Edit > Undo** (`Ctrl+Z`) and **Edit > Redo** (`Ctrl+Y`, also
`Ctrl+Shift+Z`) cover every edit to the current design, however it was
made: command bar / Script Console commands (`RECT`, `SREF`,
`DELETE LAST`, `LAYER MOVE`, `TECH LOAD`, ...), the scripting API
(`session.rect()`, `session.remove()`, `session.remap_layers()`,
`session.place_cell()`, ...), and mouse-driven editing in Select mode
(dragging a shape/vertex/edge or a window-selected group, **Edit >
Rotate...**/**Copy...**, **Edit > Edit Selected...**, **Delete
Selected**). The menu items grey
themselves out automatically when there's nothing left to undo/redo.

A few things worth knowing about the scope:

- Undo/redo tracks the *current design* only. Attached libraries
  (Setup > Attach Library...) are read-mostly reference data and are
  never affected — creating a via with the Via Wizard, for instance,
  isn't undoable through this mechanism (see Via Wizard, above).
- **File > New** and **File > Open GDS...** (and the `OPEN` command)
  load an unrelated document, so they clear the undo/redo history
  rather than trying to undo into it.
- A multi-step operation that's really one user action -- e.g.
  **Create > Cell Instance** placing a cell from an attached library,
  which both imports the cell's definition and adds the Sref -- undoes
  as a single step, not two.
- History is capped at 50 steps per session; the oldest step is
  dropped once the cap is reached.

The same two operations are available from the command bar / Script
Console as `UNDO` and `REDO` — see Command bar reference, below — and
from a script as `session.undo()` / `session.redo()` (see
`hlayout/scripting/api.py`), each returning `True`/`False` for whether
there was anything to undo/redo.

### Canvas performance

Panning (middle- or right-drag) and dragging a selected shape/vertex/
edge no longer force a full delete-and-recreate redraw on every single
mouse-motion event — that used to make interaction visibly laggy on
anything but a tiny design:

- **Pan** now translates the existing canvas items in place (a single
  cheap native operation) while the mouse button is held, and does one
  real redraw only when you release the button. The grid is drawn with
  a small buffer beyond the visible area so short pans don't show a
  gap before that final redraw fixes up the view.
- **Dragging** a shape, vertex, or edge updates the underlying geometry
  immediately on every motion event (so the data is always current),
  but coalesces the visual redraw into at most one per screen refresh
  instead of one per raw event.
- The **dot grid** draws a dot at every minor-grid intersection in
  view — no zoom-dependent thinning. Earlier versions capped the
  number of individual canvas dot items and thinned the grid out (via
  an adaptive stride) past that cap, which read as missing grid points
  once you zoomed out far enough to cross the cap. Instead, the whole
  grid is now rasterized into a single image (`LayoutCanvas.
  _draw_grid_raster()`, one `create_image()` canvas item regardless of
  dot count) using vectorized numpy when it's installed, with a plain
  Tkinter `PhotoImage.put()` fallback when it isn't — so every point
  gets a dot at any zoom level without falling back to thousands of
  `create_oval()` items. A dot only gets the larger "major" styling at
  a true major-grid intersection (both its column *and* row on a major
  line, every `LayoutCanvas.GRID_MAJOR_EVERY`-th minor line); an
  earlier version keyed that off the column alone, which made an
  entire major column look uniformly "major" top to bottom. Dot size
  is `LayoutCanvas.GRID_MINOR_DOT_R` / `GRID_MAJOR_DOT_R` (pixel radius
  of the square dot blob).

None of this changes what you see at rest — only how much work happens
per interaction frame. See `tests/test_canvas_performance.py` for the
regression coverage (item reuse during pan, redraw coalescing during
drag).

### Layer draw order

Every screen redraw draws shapes in **ascending layer order (layer 0
first, layer 63 last)**, not in whatever order they happen to sit in
the cell's shape list. This means the on-screen stacking is always
consistent and predictable: a higher-numbered layer is always drawn on
top of a lower-numbered one, regardless of which was drawn or loaded
first. Shapes that share the same layer keep their original relative
(insertion) order among themselves. This applies both to a cell's own
shapes and to the flattened shapes of any placed sref (cell instance),
including nested srefs. See `tests/test_layer_draw_order.py`.

### Attached libraries and find-and-place

Beyond the current design, up to **2 other GDSII files** can be
attached as reference libraries — a way to keep a small parts/symbol
library (pads, resistors, standard cells, ...) alongside the design
you're editing, without merging its contents in until you actually use
something from it. hlayout's own database unit (1 micron / 0.001 mm)
matches the unit most external GDSII libraries use, so attaching one
is lossless — no coordinate rounding on import — as long as the source
file's own database unit is 1 micron or coarser (finer, sub-micron
source files still round to the nearest micron):

- **File > Attach Library...** — pick a GDSII file to attach (index 0,
  then 1; a third attach is refused until one is detached).
- **File > Attached Libraries...** — lists what's currently attached,
  with each library's name, cell count, and source path.
- **File > Find & Place Cell...** — type a substring to search cell
  (Sref target) names across the current design *and* every attached
  library, pick a match from the results, give a placement point, and
  either click Place or just double-click the match (or press Enter
  with it selected) to place it immediately. If the chosen cell comes
  from an attached library, it (and everything it references) is
  copied into the current design first — GDSII requires an Sref's
  target cell to be defined in the same file — using its original name
  unless that name is already taken by a *different*, unrelated cell
  here, in which case the import is renamed (`PAD` -> `PAD_1`) rather
  than overwriting it. The view is automatically fit after placing, so
  everything in the newly placed cell is immediately visible rather
  than possibly landing off-screen. Switching the current cell (the
  toolbar's Cell dropdown) also fits the view to that cell's contents
  for the same reason.
- **File > Save As Library...** — save the current design to a GDSII
  file under a library name of your choosing, independent of the
  design's own name — the way to publish a design (or a cell you built
  in it) as a reusable library other designs can later attach.

The same operations are available from the command bar / scripting API
(see below) as `ATTACH`, `LIBS`, `LIBLIST`, `FIND`, `PLACE`, and the
`SAVE <path> [library_name]` form.

### Export Gerber

**File > Export Gerber...** writes the current design out as
**Gerber RS-274X** — a manufacturing output format, separate from
GDSII (still the tool's own database format, used for File > Save/Save
As Library/attached libraries). Prompts for a base filename (the
prefix) and writes one flattened file per layer, named
`<prefix>_L<NN>_<layer name>.gbr`:

- Only **visible** layers (Setup > Layers..., or `LAYER <n>
  visible=0|1`) that actually **have data** get a file — a hidden or
  empty layer produces nothing, so the output matches what's on screen
  rather than every one of the 64 layer slots that merely exist.
- Every cell instance (Sref) is **flattened** — its shapes are copied
  out with the placement's rotation/magnification/mirror/offset already
  applied — so a Gerber file never contains a nested block of its own,
  matching what fab tools expect to receive.
- Polygons and rectangles are drawn as filled regions (`G36`/`G37`);
  paths are drawn as strokes with a circular aperture sized to the
  path's width (the closest RS-274X equivalent to a round cap --
  square/flush caps aren't distinguished, a minor fidelity gap only at
  the very ends of a trace, since Gerber's standard aperture set has no
  projecting/flush line-cap concept).
- **Text is not exported** — Gerber has no native text primitive.
  A summary dialog reports how many text shapes were skipped so it's
  never silent about it; if a text layer is meant for fabrication,
  redraw it as a path/polygon layer instead.

The same export is available from the command bar / scripting API as
`GERBER <output_prefix> [cell=<name>]` (cell defaults to the current
cell) or `session.export_gerber(prefix, cell_name=None, layers=None)`,
the latter also accepting an optional list of layer numbers to restrict
the export to.

### Export DXF

**File > DXF Export Sets...** (also under Tools) writes the current
design out as a single **AutoCAD R12 (AC1009)** file — one DXF layer
per hlayout layer, rather than one file per layer like Gerber, since
DXF has its own native LAYER table. R12 is deliberately the oldest
common dialect: every CAD tool, old or new, reads it cleanly.

- Only **visible** layers that actually have data are written, same
  "print what's on screen" rule Gerber follows.
- Every cell instance (Sref) is **flattened**, same as Gerber.
- Every layer's Polygon/Rectangle/Path shapes are **merged** with the
  same shapely pipeline Tools > Mask Operations... uses
  (`hlayout.core.maskops.layer_to_shapely_flattened()`) before being
  written, rather than exported as separate, possibly-overlapping
  entities: a Path is first stroked into its outline (buffered by half
  its width, honouring its end-cap style) — so its width is baked
  directly into real polygon geometry, not a DXF "constant width"
  POLYLINE attribute some readers ignore — and touching/overlapping
  shapes on the same layer merge into one clean region, matching the
  as-fired reality of a screen-printed film. A resulting hole (e.g. a
  fill with a via window cut in it) is closed with the same
  "bridge to a zero-width slit" trick used for GDSII mask-op output,
  since DXF's own POLYLINE has no native hole concept either. Requires
  the `shapely` package (also needed for Mask Operations/DRC).
- **Text is exported** (DXF has a native TEXT entity, unlike Gerber),
  including every **reference designator** — a ref-des already baked
  into its own cell as a real Text shape is exported as-is; the much
  more common case, `SRef.label` floating over the instance as a
  canvas-only overlay with no shape of its own, is synthesized into a
  real TEXT entity at the same instance-centre-plus-offset position the
  canvas computes, on the Ref-Des annotation layer (Setup > Technology
  Properties...).
- Colour: R12 has no true-colour support, so each layer's colour is
  approximated to the nearest of the 9 standard AutoCAD Color Index
  entries.

The same export is available from the scripting API as
`session.export_dxf(path, cell_name=None, layers=None)` — `layers`
optionally restricts the export to a subset of layer numbers, same as
`export_gerber()`. (There's no command-bar `DXF` command yet — use
`export_dxf()` directly, or the File menu.)

### Technology files

A **technology file** captures everything about *how a process draws*
that isn't part of any one design: the 64-layer stack (name, colour,
stipple, fill, visibility for every layer) plus a small open-ended set
of global parameters that can grow over time as new needs come up
(`Library.tech_params`, a plain string-keyed dict — e.g. a process
name or design-rule note today, more later). It's a separate JSON
file, not GDSII (GDSII has no notion of layer names, colours, or
arbitrary parameters).

The usual workflow is to load a technology file once, right at the
start of a design, so the layer names/colours/etc. for the target
process are in place before you draw anything:

- **Setup > Load Technology File...** — pick a `.json` technology file
  and apply its layer stack + parameters onto the current design's
  layers, in place (layers the file doesn't mention are left
  untouched). Can be re-run any time, not just at the start.
- **Setup > Save Technology File...** — write the current design's
  layer stack + parameters out as a technology file, independent of
  saving the design itself — e.g. to capture layer edits made along
  the way as the starting point for the next design.

The same operations are available from the command bar / Script
Console and the scripting API:

```
TECH LOAD <path.json>     load a technology file onto the current library
TECH SAVE <path.json>     save the current layer stack + params
TECH SET <key> <value>    set a global technology parameter
TECH GET <key>             show one technology parameter
TECH PARAMS                list all technology parameters
```

or, from a script, `session.load_technology(path)` and
`session.save_technology(path)` (see `hlayout/io/techfile.py`).

Note: the Thick-Film Resistor Designer's design rules (Tools >
Thick-Film Resistor Designer... > Design Rules...) also live in
`tech_params` under the hood, so a technology file (and, per the next
section, a saved design's sidecar) already carries them too — there's
no separate "resistor design file" to manage on top of this.

### Save and Open restore everything, not just geometry

**File > Save GDS...** writes the current design as a plain,
standards-compliant `.gds` file — unchanged, still openable by any
other GDSII tool — plus a companion **sidecar** file next to it, named
`<path>.hlaux` (JSON). The sidecar captures everything a `.gds` file
itself can't hold:

- the current **technology** — the full 64-layer stack (names,
  colours, stipple, fill, visibility) and `tech_params`, which is also
  where the Resistor Designer's design rules live (see above) — so
  those come back too, with no separate file to track;
- a full, **embedded copy** of every attached reference library (Setup
  \> Attach Library...) — not just a remembered path, so re-opening the
  design still restores them even if the original attached file was
  since moved, renamed, or deleted.

**File > Open GDS...** (and launching `hlayout_app.py mydesign.gds`
from the command line) automatically looks for `<path>.hlaux` next to
whatever `.gds` file is opened and restores all of the above if it's
there. A `.gds` with no sidecar — a file from another tool, or one
saved with this tool before this feature existed — opens exactly as it
always did, with default layers and nothing attached; this is purely
additive; and **File > Save As Library...** deliberately does *not*
write a sidecar, since that command exists to publish a bare, portable
`.gds` for other designs to attach, not to snapshot a whole session.

**File > Save As...** (`Ctrl+Shift+S`) always prompts for a location —
pre-filled with the current file's folder/name, if any — and saves
there (design + sidecar, exactly like Save GDS...), unlike Save
GDS.../`Ctrl+S`, which silently reuses the current file once one is
set. The new location becomes the file Save/`Ctrl+S` writes to from
then on, so Save As... is how to save a copy under a new name or move
the open design to a different folder; the old file on disk is left
untouched, not deleted.

**File > Archive Design...** bundles every file that makes up the open
(already-saved) design into one `.zip`: the `.gds` itself, its
`.hlaux` sidecar if one exists, and every attached library `.gds` still
reachable at its recorded path (an embedded copy already lives in the
sidecar, so the standalone originals aren't needed to reopen the
design — this is about keeping them together for handoff/backup). An
attached-library path that no longer points at a real file — since
moved, deleted, or restored from the sidecar with no live source — is
skipped rather than failing the whole archive; two files that happen to
share a name get de-duplicated (`part.gds`, `part_2.gds`, ...) inside
the zip. Refuses with a message if the design has never been saved,
since there's no `current_path` to define "this design"'s files from.

The same behaviour is available from the command bar / scripting API:
`SAVE <path.gds>` with no `library_name` (`session.save_design(path)`)
writes the sidecar; `SAVE <path.gds> <library_name>` (`session.save(path,
library_name=...)`) still writes a bare `.gds`, no sidecar, for
publishing a library. `OPEN <path.gds>` (`Session.open_design(path)`)
restores a sidecar if present. If an embedded attached library fails to
restore (corrupted sidecar, truncated file), the rest of the design
still opens — check `session.attach_errors` (a list of messages, empty
if nothing went wrong) or watch for the warning dialog in the GUI.

### Printing

**File > Print...** (`Ctrl+P`) renders the current cell — its visible,
flattened geometry (every SRef/ARef resolved, same "print what's on
screen" rule the Gerber/DXF exporters follow) plus a colour-swatch
layer legend — as an SVG drawing wrapped in a printable HTML page.
Tkinter has no print dialog of its own, so this hands the page straight
to the OS's own print flow (`os.startfile(path, "print")` on Windows)
and falls back to just opening it in the default browser — Ctrl+P
there still prints it — if that handoff isn't available on this
platform/setup. Text shapes ARE included (unlike Gerber, which has no
text primitive), drawn at their position but unrotated; path end caps
are always drawn round regardless of their own cap style; an outline
(drawn whenever Fill is on, and the only thing drawn when it's off —
how a component body/package outline is normally represented) is held
to a fixed 0.15 mm hairline on the page rather than the design's own
DBU width, so it stays visibly a line instead of shrinking to a
fraction of a pixel at scale — all disclosed simplifications in
`hlayout.io.printio`, not silent approximations.

A layer with no data drawn on paper falls into one of two buckets, and
the page tells you which: nothing on that layer in this cell at all,
or the layer has geometry but is currently hidden (Setup >
Layers...) — Print skips a hidden layer exactly the way the canvas
does, "print what's on screen," and a printed page with anything
hidden gets a "Not shown: `<n>` (`<name>`) ... currently hidden" note
naming it, instead of leaving a missing shape to guesswork. The same
note is appended to the PRINT command's result and the status line
after File > Print....

The drawing is normally printed at true physical scale (1:1) from the
design's own units — but a design smaller than
`hlayout.io.printio.PRINT_TARGET_MM` (150 mm, comfortably true of most
hybrid parts) in its larger dimension is auto-scaled UP to about that
size instead, since printing a 2 mm resistor "at 100%" is a barely
visible speck. The factor chosen is always one of a fixed, clean set
(2x, 5x, 10x, 20x, ...) rather than an arbitrary decimal, and is always
shown on the printed page itself ("Scale: 10x actual size") so what's
on paper is never ambiguous. Pass `scale=` to pick a different factor,
or `scale=1` to force true size regardless of how small the design is.

The same behaviour is available from the command bar / scripting API:
`PRINT [cell=<name>] [out=<path.html>] [scale=<n>]`
(`Session.print_cell()` to build the page,
`hlayout.io.printio.send_to_system_printer()` for the OS handoff) —
`cell` defaults to the current cell, `out` to a fresh temp file, and
`scale` to the auto behaviour above.

### Remap layers

**Setup > Remap Layers...** bulk-reassigns shapes from one layer to
another — the way to bring an existing design's geometry into
alignment with a layer stack loaded from a technology file. If a
design was drawn before its target technology was decided (or was
drawn against a different one), its layer numbers/names won't
necessarily match the technology file you eventually load — e.g. what
this design has been calling layer 3 should really be layer 7
("METAL1") in the target process. Remap Layers moves the actual
geometry over to match, rather than just renaming the layer (which
would only change the label, not which layer number the drawn shapes
are on).

- Add one row per layer you want to reassign (From layer -> To layer;
  either side can also be picked by name), then **Apply**.
- **Apply to** defaults to the whole library (every cell, not just the
  one you're currently viewing) since that's the usual case when
  aligning to a new technology — but you can restrict it to a single
  named cell instead.
- Only cell instances' geometry moves; the Sref hierarchy itself is
  untouched, so nested/referenced cells are remapped too as part of
  "every cell in the library."

The same operation is available from the command bar / Script Console
and the scripting API:

```
LAYER MOVE <from> <to> [cell=<name>]   move every shape on <from> to <to>,
                                        across every cell (or just cell=<name>)
```

or, from a script, `session.remap_layers({<from>: <to>, ...}, cell_names=None)`
(see `hlayout/scripting/api.py`), which accepts several from/to pairs
at once and returns how many shapes moved on each source layer.

### Layer-based mask operations (Oversize/Undersize/AND/OR/NOT/XOR)

**Tools > Mask Operations...** is a "resize"/boolean layer operator,
the kind found in a GDSII CAM system (e.g. Cadence's DRC/LVS deck
Size/AND/OR/NOT/XOR operators): grow or shrink one layer by a fixed
amount, or combine two layers with a boolean intersection, union,
difference, or symmetric difference, writing the result as brand-new
polygons on a target layer (which can be the same layer as the input,
or a fresh one). It requires the `shapely` package (`pip install
shapely`, also listed in `requirements.txt`); the dialog and the
`MASKOP` command both raise a clear error if it isn't installed rather
than failing silently.

- **OVERSIZE** grows every shape on Layer A outward by **Amount**
  (DBU), on all sides, with mitred (sharp) corners — the sizing stays
  rectilinear-friendly rather than rounding off 90-degree corners.
- **UNDERSIZE** is the same, shrinking inward by Amount instead.
- **AND** writes the intersection of Layer A and Layer B.
- **OR** writes the union of Layer A and Layer B.
- **NOT** writes Layer A minus Layer B (everything on A that isn't
  also on B).
- **XOR** writes the symmetric difference of Layer A and Layer B
  (everything on exactly one of the two, not both) — a quick way to
  see what changed between two versions of the same layer.
- **Result layer** is where the new polygon(s) land — pick the same
  layer as Layer A to grow/shrink geometry in place (the original
  shapes on Layer A are left alone; the result is added alongside
  them), or a different layer to keep the mask result separate.
- **Apply to** defaults to the current cell; pick a different cell to
  run the same operation there instead. Each cell's own Layer A/Layer B
  input geometry is combined only with itself — output from one cell
  is never combined with another cell's.
- **A result layer that's toggled off in the Layers panel is turned
  back on automatically** whenever the operation actually writes
  output (the status bar/Command Bar output says so when it happens).
  Writing correct polygons to a hidden layer would otherwise look
  exactly like writing nothing at all, since the canvas doesn't change
  either way — this fixes a reported case where AND/OR/NOT/XOR
  appeared to "not produce data" when the result layer simply had its
  visibility off. This only applies to the dialog and the `MASKOP`
  command bar/Script Console command; `Session.mask_op()` called from
  a standalone script never touches layer visibility, since a script
  may deliberately use a hidden layer as scratch space partway through
  a multi-step operation.
- **Simplify (DBU, 0=off)** applies vertex-reduction (Douglas-Peucker,
  topology preserved) to the result at the given DBU tolerance before
  it's written — useful for keeping a heavily-sized or heavily-
  booleaned result under the 200-point-per-polygon limit. A larger
  tolerance can visibly round off corners, so start small (1-2 DBU)
  and only raise it if the operation still reports too many vertices
  in one polygon.

**Placed cells (Srefs) are included by default.** Most real content in
a design ends up placed via Sref rather than drawn straight into the
cell you're operating on — every resistor the Thick-Film Resistor
Designer places, for instance, is its own device cell placed by a
single Sref (see Thick-Film Resistor Designer, above), not raw shapes
in the current cell. **Include placed cells (Sref) when gathering
input geometry** (checked by default) descends into every Sref in the
operated-on cell, recursively, folding in each instance's own
rotation/magnification/mirror/origin, so a mask op run against a cell
full of placed resistors, vias, etc. actually finds their geometry —
unchecking it restricts the operation to only the shapes drawn
directly into the named cell itself. This defaulted on after an
earlier version always restricted mask ops to a cell's own directly-
owned shapes, which meant a design built entirely from placed cells
(the common case) silently produced no output at all.

**Paths are converted to polygons first.** A Path has no filled area
of its own (it's a centreline plus a width), so before any sizing or
boolean op runs, every Path on an input layer is stroked into its own
outline polygon — offset by half its width, honouring its round/
square/flush end-cap style — exactly as the tool's own mask-op spec
requires ("paths would be converted to polygons"). Rectangles and
Polygons already have a filled area and are used directly.

**Holes are bridged, not represented natively.** Neither this tool's
Polygon shape nor GDSII's boundary record has a concept of a hole, so
if a mask op's result has one (e.g. ANDing a large fill against a
layer with a cutout in it), the hole is closed into the surrounding
polygon with a zero-width slit to the nearest point on the outer
boundary — the standard technique for representing a "donut" shape
(like a guard ring) without a native hole primitive.

The same operation is available from the command bar / Script Console
and the scripting API:

```
MASKOP OVERSIZE <layer_a> result=<layer> amount=<dbu> [cell=<name>] [flatten=0|1] [simplify=<dbu>]
MASKOP UNDERSIZE <layer_a> result=<layer> amount=<dbu> [cell=<name>] [flatten=0|1] [simplify=<dbu>]
MASKOP AND <layer_a> <layer_b> result=<layer> [cell=<name>] [flatten=0|1] [simplify=<dbu>]
MASKOP OR <layer_a> <layer_b> result=<layer> [cell=<name>] [flatten=0|1] [simplify=<dbu>]
MASKOP NOT <layer_a> <layer_b> result=<layer> [cell=<name>] [flatten=0|1] [simplify=<dbu>]
MASKOP XOR <layer_a> <layer_b> result=<layer> [cell=<name>] [flatten=0|1] [simplify=<dbu>]
```

(`flatten` defaults to 1/on, matching the dialog's checkbox) or, from a
script, `session.mask_op(op, layer_a, result_layer, layer_b=None,
amount=0.0, cell_names=None, flatten=True, simplify=None)` (see
`hlayout/scripting/api.py`, backed by `hlayout/core/maskops.py`),
which returns `{cell_name: polygons_written}` for cells that actually
produced output.

### Design Rule Check (DRC)

**Tools > DRC...** checks a design against a saved set of geometric
rules — single-layer checks (does every feature meet a minimum width,
area, or spacing from its neighbours?) and between-layer checks (is
one layer properly spaced from, or enclosing, another?) — the same
kind of checks a GDSII DRC deck runs. It requires the `shapely`
package (same as Mask Operations, above; see `hlayout/core/drc.py`).

Rule kinds:

- **MIN_WIDTH** — every feature on Layer A must be at least Value DBU
  wide everywhere (checked via morphological "opening": erode by
  Value/2, dilate back by Value/2; whatever gets eaten away and never
  comes back is the violation).
- **MIN_AREA** — every connected shape on Layer A must have area >=
  Value DBU². Touching same-layer shapes are treated as one connected
  component, matching how they'd actually print/etch as a single
  feature.
- **MIN_SPACE** — every pair of *distinct* (non-touching) connected
  components on Layer A must be at least Value DBU apart.
- **MIN_SPACE_BETWEEN** — every shape on Layer A must be at least
  Value DBU from every non-overlapping shape on Layer B.
- **MIN_ENCLOSURE** — every shape on Layer B must be enclosed by Layer
  A by at least Value DBU on all sides (e.g. metal enclosing a via).

**Limitation: self-notch spacing isn't checked.** A concave notch cut
into the edge of a single connected shape (rather than a gap between
two separate shapes) is not flagged by MIN_SPACE — only spacing
between *distinct* components is checked. A future round could add
this via a dedicated width-of-notch check if it turns out to matter in
practice.

**Placed cells (Srefs) are always included**, the same way Mask
Operations does it: every check descends into every Sref in the cell
being checked, recursively, folding in each instance's own rotation/
magnification/mirror/origin, so a design built out of placed cells
(resistors, vias, ...) is actually checked, not just whatever's drawn
directly into the current cell.

**Rules are saved into the technology file** the moment they're
added/edited/deleted — there's no separate "Save Rules" step to
forget, the same convention Setup > Resistor Geometry Rules... uses.
**Run DRC** checks the saved rules against the current cell and lists
every violation found (rule, cell, message) in the Violations table;
optionally check **Mark violations on layer** and pick a layer to also
draw each violation's marker polygon directly onto the design, so it
shows up in the canvas (and in any exported GDSII/Gerber) right where
the problem is.

**Each rule has its own colour**, so with several rule kinds active the
Violations table (and the rule table above it) reads at a glance by
error type instead of every row needing the Rule column read one by
one. A new rule gets one automatically from a small default palette;
click the colour swatch next to Name in **Add Rule.../Edit Rule...**
to pick a different one (the same click-a-swatch interaction as a
layer's colour in the Layer panel). The colour is a display preference
only — it has no effect on what a rule checks — and is saved into the
technology file alongside the rest of the rule. It isn't settable from
the command bar/scripting API yet (see below); a rule saved before
this existed gets backfilled with a default colour the first time
Tools > DRC... is opened.

**Checking scope defaults to the current cell only, not every cell in
the library** — since every check already descends into that cell's
own placed instances, checking the current cell alone normally covers
the whole design as seen from there. Checking a sub-cell *and* a
parent that places it would report the same physical violation twice
(once from each cell's own point of view), so the default avoids that;
pass explicit `cell_names` from a script only if independent per-cell
reports are actually what's wanted.

The same operation is available from the command bar / Script Console
and the scripting API:

```
DRCRULE ADD <kind> <layer_a> [layer_b] value=<n> [name=<name>]
DRCRULE LIST
DRCRULE REMOVE <index>
DRCRULE CLEAR
DRC RUN [cell=<name>] [error=<layer>]
```

or, from a script, `DrcRule(kind, layer_a, value, layer_b=None,
name="", color="")` (`color` is the GUI-only display colour described
above — a hex string like `"#e6194b"`, or `""` for "not picked yet";
it plays no part in the check itself) and `session.run_drc(rules=None,
cell_names=None,
error_layer=None)` (see `hlayout/scripting/api.py`, backed by
`hlayout/core/drc.py`) — `rules` defaults to whatever's saved in the
technology file, and the call returns the list of `DrcViolation`
found (each with `.rule`, `.cell_name`, `.points`, `.message`,
`.bbox()`). See `examples/drc_check_example.py` for a complete
runnable script that builds a layout with a too-narrow neck and a
too-close pair of pads, runs MIN_WIDTH and MIN_SPACE against it,
prints every violation, and draws the markers onto an errors layer in
the saved GDSII.

### Via Wizard

**Tools > Via Wizard...** creates a via cell and saves it directly
into an attached reference library's own file — the way to add a new
reusable part to a shared library, as opposed to drawing shapes that
only ever live in the current design.

Requires at least one attached library first (**Setup > Attach
Library...**); if none is attached yet, the wizard says so and points
you at that menu item instead of opening.

Parameters:

- **Shape** — Square or Round.
- **Size (µm)** — the pad's full width (square) or diameter (round).
- **Layer 1** / **Layer 2** — the two layers the via connects, picked
  from the current design's layer stack (by number or name).
- **Save to library** — which attached library to write into (only
  shown as a choice when two are attached; with one attached it's
  used automatically).

Clicking **Create** draws the same square/round pad, centered at the
origin, on both Layer 1 and Layer 2 as a new cell named
`VIA_Layer<n1>_to_Layer<n2>` (e.g. `VIA_Layer3_to_Layer7`), adds it to
the chosen attached library, and saves that library's file back to
disk in place — so the via becomes a real, reusable part the next
time that file is attached or opened. If a via with that same
layer-pair name already exists in the library, you're asked whether
to replace it. The library already attached to this session is
updated in memory too, so **Create > Cell Instance** / **Create >
Find && Place Cell...** can place the new via immediately, without
needing to detach and re-attach.

The same operation is available from the command bar / Script Console
and the scripting API:

```
VIA <layer1> <layer2> [shape=square|round] [size=<dbu>] [index=0|1] [overwrite=0|1]
```

or, from a script, `session.create_via_cell(layer1, layer2, shape=,
size=, index=, overwrite=)` (see `hlayout/scripting/api.py`), which
returns the new cell's name and the file path it was saved to.

### Two-layer Route tool

**Create > Route (2-Layer)** is an interactive routing tool for tracing
a connection across two conductor layers, dropping a via wherever the
route needs to hop between them, with no separate Via Wizard step and
no attached library involved — everything is drawn straight into the
current cell:

- Click points to trace a path on the **active** layer (starts on
  **Layer A**), same as the regular Path tool — width/cap come from
  **Setup > Path Style...**, same setting the Path tool uses.
- Press **Space** to drop a via at the last point clicked: the segment
  drawn so far is committed on the layer you're leaving, a via pad
  (square or round, sized per Setup > Route Layers...) is placed
  connecting Layer A and Layer B at that point, and routing continues
  from that same point on the *other* layer. Repeat as many times as
  the route needs to hop back and forth.
- The same via press also cuts a same-centered opening in the
  configured **dielectric layer** (see below), sized independently of
  the via pads — this window, not the pads, is what actually defines
  the electrical contact area between the two routing layers in a real
  hybrid process. A real via always has this window, and it's normally
  sized **at or above** the via pad size, so the pad lands within/over
  the opening.
- **Double-click or Enter** finishes the route: the final segment is
  committed on whichever layer is currently active — no via is placed.
- **Esc** cancels only the segment currently in progress (not yet
  committed); everything already placed by earlier Space presses stays.
- Each Space press (segment + via + dielectric window, all together)
  is one undo step, so `Ctrl+Z` walks back one hop at a time.

**Setup > Route Layers...** configures the tool: **Layer A** (where a
route starts), **Layer B** (what Space switches to and back), via size
(DBU), via shape (square/round), a **dielectric layer**, and a
**dielectric window size (DBU)** — the via window cut in the
dielectric layer sandwiched between Layer A and Layer B. The dielectric
layer and window are always required (a real via always has one) and
the window must be at least as large as the via size — the dialog
rejects a smaller value. All of these settings are saved into the
technology file (`route_rules`) as soon as you click OK, and travel
automatically with a saved design's `.hlaux` sidecar (see Technology
Files below) or a separately saved/loaded technology file — reopening
the design reloads them with nothing to re-enter by hand.

There's no dedicated command-bar equivalent for the interactive
click-and-Space flow (it's inherently mouse-driven), but the building
blocks it's made of are directly scriptable: `session.path(...)` for
each segment and `session.drop_via(x, y, layer_a, layer_b, size=,
shape=, dielectric_layer=, dielectric_window=)` for each via — the
latter draws a same-size pad on both layers at `(x, y)`, plus the
dielectric window when `dielectric_layer`/`dielectric_window` are
given (leaving both unset drops a bare two-pad via with no window, for
scripted geometry that isn't modelling a real dielectric stack), as a
single undo step (`session.batch()` groups several calls into one
step, the same way place_cell() and the GUI's Route tool itself do
internally).

### Thick-Film Resistor Designer

**Tools > Thick-Film Resistor Designer...** opens a standalone
resistor-sizing tool (its own window, one instance shared per
session — opening it again while it's already open just brings it
to the front) for laying out thick-film resistors: given a target
value, tolerance, and power dissipation, it works out which ink
sheet resistance to print on and what body width/length gives that
value, and lets you compare several candidate solutions before
committing to one.

This began as a separate PyQt5 program (`examples/Thickfilm_resisteresign.py`)
and has been rewritten in Tkinter and folded into the main app
(`hlayout/gui/resistor_designer.py`), so it now shares the same
window chrome, fonts, and technology-file plumbing as the rest of
HLayout instead of needing its own Qt runtime. The original
standalone script is left in `examples/` for reference, but the
Tools menu entry is the one to use going forward.

- **Resistor list** (left): one row per resistor (ref des, target
  value, tolerance, power, computed aim value, chosen ink, squares,
  body length, aspect ratio). Add..., Edit..., Delete manage rows;
  Auto-Select All picks each resistor's best-scored solution in one
  step; Recalculate re-runs sizing for every row (e.g. after
  changing design rules).
- **Solutions** tab (right): for the selected resistor, every ink
  that can hit the target within the aspect-ratio limits, each with
  its own body size and a live aspect-ratio diagram; Use This
  Solution assigns it to the resistor.
- **Summary / BOM** tab: a plain-text rollup — ink usage counts,
  resistors with no valid solution, and power-rating warnings.
- **Import CSV.../Export CSV...** round-trip the resistor list to a
  spreadsheet-friendly file.
- **Print / Preview Report...** builds the same report as an HTML
  file and opens it in your default browser, where Ctrl+P/Print
  handles the actual printing/PDF export (Tkinter has no built-in
  print dialog, unlike the original Qt version's `QPrinter`).

**Design rules are stored in the technology file, not the resistor
tool itself.** The original standalone program kept its aim
percentage, minimum/maximum aspect ratio, and list of available ink
sheet resistances in an in-memory setting that reset every time you
ran it. In HLayout, the resistor designer window's own **Design Rules...**
toolbar button edits those same values, but saving them (OK) writes
them into the current design's `Library.tech_params` (as a single
JSON entry, `resistor_design_rules`) — the same mechanism Technology
files (above) already use for layer stacks and other global
parameters. That means:

- Loading a technology file (**Setup > Load Technology File...**)
  brings in that process's resistor design rules along with its
  layer stack.
- Saving a technology file (**Setup > Save Technology File...**)
  captures any resistor design rule changes made in the session, so
  the next design started from that file starts with the same
  rules.
- A design/technology with no resistor design rules saved yet falls
  back to sensible defaults (75% aim, 0.25-4.0 aspect ratio, a
  standard set of decade ink values from 10 Ω/sq to 1 MΩ/sq).

**Generating a sized resistor into the actual layout.** Sizing a
resistor (above) only computes numbers — a body width and length.
**Place in Layout...** on the resistor designer's toolbar takes a
resistor with a chosen solution and draws it for real: the resistor
ink body, a protective glaze overprint, the two end conductor pads,
and (automatically, from the resistor's own ref des) a number label —
as up to five real shapes. It asks for an X/Y placement point, a
rotation (0/90/180/270°), and a conductor pad layer (see below), then
calls `Session.generate_resistor()` — also available directly as the
`RESGEN <x,y> <body_width> <body_length> [rotation=] [ink=] [ref=]
[a=] [b=] [c=] [d=] [rlayer=] [clayer=] [glayer=] [dlayer=]`
command-bar command, or from a script.

**Each resistor is its own cell, placed by reference (Sref) —
not raw shapes drawn straight into the current cell.**
`generate_resistor()` builds the ink/glaze/pads/label as a brand-new
cell (named after the ref des when one is given, e.g. `R1`, otherwise
`RESISTOR` — automatically de-duplicated if that name is already
taken, e.g. `R1_1`) with everything drawn axis-aligned at the cell's
own local origin, then places a single `Sref` instance of that cell
at the requested X/Y with the requested rotation, back in whichever
cell was current when it was called. This replaced an earlier version
that drew the five shapes directly into the current cell every time:
with many resistors in one design that meant no way to select, move,
or reuse "a resistor" as one thing, and rotation had to be computed
by hand into rotated polygon corners instead of just being a
placement's rotation. With the cell+Sref approach, `RESGEN`/`Session.
generate_resistor()` returns the new cell's name, the resistor is a
normal selectable/movable instance like any other block, and the same
resistor cell can be placed again elsewhere (a second `Sref` to the
same cell name) without redrawing its geometry. The whole
cell-creation + shape-adds + placement sequence is still one undo
step.

The A/B/C/D dimensions are configured via **Setup > Resistor Geometry
Rules...** and saved into `Library.tech_params` (`resistor_geometry_rules`,
alongside the sizing rules above), so they travel with the technology
file and the design's sidecar the same way everything else does:

```
      |<-------- pad (A+B) -------->|<---- body_length (L) ---->|
 ______________________________    ____________________________
|                              |  |                            |
|        CONDUCTOR PAD         |==|      RESISTOR INK BODY      |...
|______________________________|  |____________________________|
      |<-- B -->|<-- A -->|
                 (ink extends A under the pad -- the
                  resistor-to-conductor overlap/contact)
```

- **A (overlap)** — length of resistor ink added at *both* ends of the
  electrical `body_length`, so the ink extends under (overlaps) the
  conductor pad for a reliable contact. Total ink length drawn =
  `body_length + 2×A`.
- **B (pad length)** — minimum conductor pad length beyond the overlap
  zone, at each end. Each pad's own footprint is `A+B` long, so the
  overall structure (pad to pad, outer edges) is
  `body_length + 2×A + 2×B` long.
- **C (side space)** — minimum conductor space on the side: how far
  each pad extends beyond the resistor ink's width on either side. Pad
  width = `body_width + 2×C`.
- **D (glaze oversize)** — how far the glaze overprint (below) is grown
  beyond the resistor ink's own shape, on *all four sides* (both ends
  and both long edges) — not just matching the ink's footprint.

**Resistor ink layer — matched to the ink automatically.** A process
usually prints a different resistor ink on a different layer per sheet
resistance, so the ink layer isn't just one fixed number: **Tools >
Thick-Film Resistor Designer... > Design Rules...** has an **Ink
Layers** section listing every available ink (same list the aim/AR
sizing already uses) with a layer picker next to each one, saved into
the same `resistor_geometry_rules` tech-file entry as a small map from
sheet resistance to layer number. When a resistor is placed, its
*chosen solution's* ink automatically picks the matching layer — no
manual layer selection needed per resistor. An ink with no entry in
the map (or `RESGEN` used without `ink=`) falls back to a single
default resistor-ink layer set in Setup > Resistor Geometry Rules...
(default: layer 30); `RESGEN`'s `rlayer=`/`Session.generate_resistor()`'s
`resistor_layer=` bypass the lookup entirely for a one-off override.

**Conductor pad layer — user-selected, from the technology file's
named layers.** Unlike the ink layer, the conductor pad layer isn't
tied to anything about the resistor itself, so it's chosen directly:
**Place in Layout...**'s placement dialog has its own "Conductor pad
layer" dropdown (populated from the current technology's named
layers), pre-filled with Setup > Resistor Geometry Rules...'s
configured default (layer 31) but overridable for that one placement —
e.g. to land a resistor's pads on whichever conductor layer it's
actually routed on. `RESGEN`'s `clayer=`/`Session.generate_resistor()`'s
`conductor_layer=` do the same from the command bar/a script.

**Resistor glaze overprint — oversized on all 4 sides.** Every
generated resistor is also overprinted with a resistor glaze — a
protective layer the same shape as the resistor ink body, but grown by
**D** on all four sides (both ends *and* both long edges, not just the
ink's own footprint) — on its own layer, set in Setup > Resistor
Geometry Rules... (default: layer 32, oversize 50 DBU) via `RESGEN`'s
`glayer=`/`d=`/`Session.generate_resistor()`'s `glaze_layer=`/
`glaze_oversize=`.

**Resistor number label.** Passing a reference designator (e.g.
`"R1"`) — `ref_des` in `Session.generate_resistor()`, `res.ref_des`
automatically for **Place in Layout...**, or `ref=<name>` for
`RESGEN` — adds a text label centred on the resistor ink body, on a
documentation layer (default: layer 33, distinct from the electrical
ink/pad/glaze layers) set in Setup > Resistor Geometry Rules... . No
`ref_des` given means no label is drawn.

All shapes (ink + glaze + 2 pads, plus the label when a ref_des is
given) are added — along with the new device cell and its placing
Sref — as a single undo step. See `tests/test_resistor_generate.py`
and `tests/test_resistor_gui.py` for the regression coverage.

**Placing more than one resistor without stacking them on top of each
other.** Every earlier "Place in Layout..." dialog defaulted its X/Y
to (0, 0) — fine for a single resistor, but placing a second and
third one without manually typing a new position each time landed
every one of them exactly on top of the first. The resistor designer
window now keeps a running placement cursor: after each successful
placement it advances to just clear of that resistor's own footprint
(body length + 2×A + 2×B) plus a margin (default: the pad length B),
tracked as the next available *left edge* rather than a centre point
so that a small resistor followed by a much larger one still can't
overlap — a centre-to-centre advance under-spaces whenever the next
part's footprint is more than double the previous one's. The
dialog's X/Y always starts at this cursor (still fully editable), so
placing resistors one after another from the toolbar lays them out
left-to-right instead of stacking. **Place All in Layout...** does
the same for every resistor that already has a solution selected, in
one step with no per-resistor dialog — resistors with no solution are
skipped and reported in the status line.

### Command bar reference

Commands are grouped below the same way the in-app reference is —
type `HELP` in the command bar, or open **Help > Command Reference**
(also `F1`), to see this exact list from inside the running app at any
time; all three (this table, the command bar's `HELP` output, and the
Help menu dialog) are generated from the same source, so they can't
drift out of sync with each other.

```
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
    DELETE LAST                       remove the most recently added shape/sref

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
    GRID ON | OFF

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
    PRINT [cell=<name>] [out=<path.html>]
                                       render the design (default: the current cell)
                                       as a printable HTML page -- a legend plus an
                                       SVG drawing of its visible geometry at true
                                       physical scale -- and hand it to the system's
                                       print flow (or just open it if that fails, so
                                       Ctrl+P still works). Same as File > Print...

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
    VIA <layer1> <layer2> [shape=square|round] [size=<dbu>] [index=0|1] [overwrite=0|1]
                                       create a via cell (a square/round pad on both
                                       layers, named VIA_Layer<n1>_to_Layer<n2>) directly
                                       inside an attached library and save that library's
                                       file in place; shape/size default to square/100

Thick-film resistor generation (Tools > Thick-Film Resistor Designer... sizes
a resistor; this draws it):
    RESGEN <x,y> <body_width> <body_length> [rotation=<deg>] [ink=<rsh>]
           [ref=<designator>] [a=<dbu>] [b=<dbu>] [c=<dbu>] [d=<dbu>]
           [rlayer=<n|name>] [clayer=<n|name>] [glayer=<n|name>] [dlayer=<n|name>]
                                       build a resistor -- ink body + glaze overprint +
                                       its two end conductor pads (+ a ref-des label
                                       if ref= is given) -- as its own new cell, and
                                       place one instance (Sref) of it centred at x,y;
                                       body_width/body_length usually come from a sized
                                       Resistor's body_width/selected.body_length;
                                       ink=<rsh> looks up the ink layer by sheet
                                       resistance in the technology file's per-ink
                                       layer map (Tools > Thick-Film Resistor
                                       Designer... > Design Rules...); rlayer
                                       overrides that lookup entirely; ref=<name>
                                       (e.g. "R1") labels the resistor, centred on
                                       the ink body, on the documentation layer
                                       (dlayer, or Setup > Resistor Geometry
                                       Rules... 's default); a/b/c/d/clayer/glayer
                                       default to Setup > Resistor Geometry
                                       Rules... (the technology file) when omitted
                                       -- see that dialog for what A (overlap),
                                       B (pad length), C (side space), and D
                                       (glaze oversize, on all 4 sides) mean

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

Scripting:
    PY <python source>                run arbitrary Python against the session
                                       (also the fallback for unrecognised input)
    HELP                              show this command reference
```

All coordinates are in database units (1 DBU = 1 micron = 0.001 mm).
So e.g. a 2mm rectangle is `RECT 0 0,0 2000,2000`. Anywhere a
`<layer>` is expected above, you can pass either its number (0-63) or
its name (e.g. `RECT Thick-Film-Conductor 0,0 100,50`) once you've
named it via `LAYER <n> name=...` or the Layers tab.

## Scripting

The whole database is a plain Python object graph — read and write it
directly, no GUI required:

```python
from hlayout.scripting import Session

s = Session.new("MYLIB")
s.new_cell("TOP")
s.rect(layer=0, x1=0, y1=0, x2=100, y2=50)
s.polygon(layer=1, points=[(0, 0), (10, 0), (5, 10)])
s.path(layer=2, points=[(0, 0), (50, 50)], width=5, cap="round")
s.text(layer=3, origin=(0, 0), text="HELLO")
s.save("out.gds")
```

See `examples/demo_script.py` for a fuller example (a small hybrid
circuit with pads, a thick-film resistor, and thin-film traces), and
`hlayout/scripting/api.py` for the full `Session` API. Loading an
existing file: `Session.open("design.gds")`.

`examples/generate_sot_packages.py` builds a small ready-to-attach
parts library of standard SOT (Small-Outline Transistor) package
footprints — SOT-23, SOT-23-5, SOT-23-6, SOT-89, SOT-143, SOT-223, and
SOT-363 — each with pads, a body outline, and a reference label, saved
as a named GDSII library (`sot_packages.gds`, library name
`SOT-PACKAGES`). Run it once (`python examples/generate_sot_packages.py`),
then attach the result and place a package the normal way:

```python
s.attach_library("examples/sot_packages.gds")
s.place_cell("SOT-23", (0, 0))
```

or from the command bar: `ATTACH examples/sot_packages.gds` then
`PLACE SOT-23 0,0`. The pad/pitch dimensions are generic, commonly
used land-pattern sizes, not pulled from a specific manufacturer's
datasheet — verify against the real
part before using them for fabrication.

`examples/generate_perf_test.py` generates a synthetic design with
1000 rectangles (laid out in a grid) and 100 paths (alternating round/
square caps), purely for exercising canvas draw/pan/drag performance —
see "Canvas performance" above and `tests/test_canvas_performance.py`.
**Run the generator first — `examples/perf_test.gds` is not included in
the project download; it's created on demand:**

```
python examples/generate_perf_test.py     # writes examples/perf_test.gds
python hlayout_app.py examples/perf_test.gds
```

then try panning, zooming, and dragging shapes around in the GUI that
opens. The counts are configurable:
`python examples/generate_perf_test.py --rects 5000 --paths 300 --out big.gds`.
Both commands must be run from the project's root folder (the one
containing `hlayout_app.py`); running `hlayout_app.py examples/perf_test.gds`
from inside `examples/` or before the generator has run will fail with
`GDSII file not found: examples/perf_test.gds` — a clean error naming
the missing path (fixed in this round: it used to be a raw traceback
with an unhelpful `[GDSTK] Unable to open GDSII file for input.`
message ahead of it).

Inside the running GUI, the same `Session` object is exposed as `s`
(and `hl`) in the Script Console tab and via the `PY` command, so
scripts can also drive/inspect the live editor.

### Attached libraries from a script

```python
s.attach_library("parts.gds")           # index 0; at most 2 attached at once
s.attach_library("more_parts.gds")      # index 1
s.list_attached_libraries()             # [{'index', 'name', 'path', 'cells'}, ...]
s.list_library(0)                       # cells in attached library 0
s.list_library("current")               # cells in the current design (default)
s.find_cells("resistor")                # [{'source', 'name'}, ...] name-substring search
s.place_cell("RES_100R", (100, 100))    # searches current + attached libs, imports & places
s.place_cell("PAD", (0, 0), source=1)   # disambiguate when the name exists more than once
s.detach_library(0)
s.save("myparts.gds", library_name="MY-PARTS")   # save under a chosen library name
```

`place_cell` is the scripting equivalent of File > Find & Place Cell:
if the target cell lives in an attached library it (and its dependency
closure) is copied into the current design first, since a GDSII Sref's
target must be defined in the same file; a name collision with an
unrelated existing cell is resolved by renaming the import rather than
overwriting anything.

## Project layout

```
hlayout/
  core/       data model: Library, Cell, Layer/LayerTable, shapes
              (Polygon, Path, Rectangle, Text, SRef), units, errors;
              maskops.py -- layer-based Oversize/Undersize/AND/OR/NOT/
              XOR geometry engine (see "Layer-based mask operations"),
              built on the shapely package; drc.py -- Design Rule Check
              engine (MIN_WIDTH/MIN_AREA/MIN_SPACE/MIN_SPACE_BETWEEN/
              MIN_ENCLOSURE, see "Design Rule Check"), reusing maskops'
              Sref-flattening geometry gatherer
  io/         GDSII read/write (hlayout/io/gdsio.py, via gdstk);
              Gerber RS-274X export (hlayout/io/gerberio.py);
              design sidecar -- technology + attached libraries bundled
              alongside a saved .gds (hlayout/io/sidecar.py)
  scripting/  Session (read/write API) and ScriptConsole (exec engine)
  gui/        Tkinter application: app.py (main window), canvas.py
              (drawing surface), layer_panel.py, commands.py (command bar),
              resistor_designer.py (Tools > Thick-Film Resistor
              Designer..., see "Thick-Film Resistor Designer"),
              drc_window.py (Tools > DRC..., see "Design Rule Check")
hlayout_app.py   GUI launcher
examples/        demo_script.py — headless scripting example
                 drc_check_example.py — MIN_WIDTH + MIN_SPACE DRC example
                 (see "Design Rule Check")
                 generate_perf_test.py — 1000 rects + 100 paths, for
                 canvas performance testing (see "Canvas performance")
                 Thickfilm_resisteresign.py — the original standalone
                 PyQt5 resistor designer, kept for reference; superseded
                 by Tools > Thick-Film Resistor Designer... in the app
tests/           pytest test suite (core model, GDS round-trip, scripting)
```

## Format notes (GDSII mapping)

GDSII does not natively distinguish every hlayout concept, so this tool
uses a couple of conventions (see docstring in `hlayout/io/gdsio.py`):

- A 4-point boundary reads back as a `Rectangle`; any other point count
  reads back as a `Polygon`. Files written by this tool round-trip
  exactly.
- Path end caps: `ROUND` <-> GDS "round", `SQUARE` <-> GDS "extended"
  (projecting half-width cap), `FLUSH` <-> GDS "flush". Paths are
  written as true GDSII PATH elements (not decomposed to boundaries).
- Text height/font: GDSII has no explicit font-size field, so height is
  encoded via the label's magnification and font (0-3) via the GDS
  texttype field, matching GDSII's native 4-font model.
- GDSII database unit: hlayout writes and reads files with the GDSII
  UNITS record set to 1 micron per database unit (with 1 nm internal
  precision), so external files that also use a 1-micron (or coarser)
  database unit attach/open with zero coordinate rounding.

## Tests

```
pytest tests/
```

## What's implemented vs. what's next

Implemented: the full data model with validation (200-point limits, 64
layers, 500x500mm bounds, cycle-safe Srefs), exact GDSII round-tripping,
a Python scripting API + in-app console, and a working Tkinter GUI with
canvas rendering, layer panel (with per-layer Fill on/off), dot grid,
zoom/pan/fit, multiple view windows, a text command line (with
PowerShell/cmd.exe-style line editing), mouse-driven drawing
(rect/polygon/path/circle/text/cell-instance, with snap-to-grid and a
live crosshair/path-width-ghost preview), vertex-, edge-, and
whole-shape-level select/drag-to-move/edit-properties/delete, and up to
2 attached reference libraries with cross-library cell search
(Find & Place) and named-library export. The main window is laid out
with the canvas taking the large majority of the width and the
Layers/Console panel anchored at a fixed width on the right edge.

Not yet implemented (natural next steps): undo/redo, copy/paste,
multi-select, selecting shapes nested inside a referenced cell (only
the currently-viewed cell's own contents are selectable today), and a
proper stipple renderer (Tk's built-in bitmap stipples approximate the
9 patterns rather than reproducing them exactly).
