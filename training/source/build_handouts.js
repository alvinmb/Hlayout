// Student handout + trainer guide for the HLayout ALU8 course (Word).
const fs = require('fs');
const path = require('path');
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, Table, TableRow, TableCell, WidthType,
  ShadingType, BorderStyle, ImageRun, PageBreak, Header, Footer, PageNumber, LevelFormat, TableOfContents,
  PositionalTab, PositionalTabAlignment, PositionalTabRelativeTo, PositionalTabLeader, TabStopType,
} = require('docx');
const DIMS = require('./img/dims.json');
const NETS = require('./nets.json');

const INK = '15233B', GOLD = 'B8860B', DIEL = '2C7A67', MUTED = '56657A', ALU = 'F2F4F5', LINE = 'C9D1DB', PGOLD = 'F6EDCF', PGRN = 'DDEFE8';
const PAGE_W = 11906, MARGIN = 1134, CW = PAGE_W - 2 * MARGIN; // A4, 2 cm margins -> 9638 DXA

// ---------- helpers ----------------------------------------------------------
const r = (t, o = {}) => new TextRun(Object.assign({ text: t, font: 'Calibri' }, o));
const P = (children, o = {}) => new Paragraph(Object.assign({ children: Array.isArray(children) ? children : [r(children)], spacing: { after: 100, line: 276 } }, o));
const H1 = t => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [r(t)], pageBreakBefore: true });
const H1n = t => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [r(t)] });
const H2 = t => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [r(t)] });
const H3 = t => new Paragraph({ heading: HeadingLevel.HEADING_3, children: [r(t)] });
const mono = t => r(t, { font: 'Consolas', size: 19 });
const B = (t) => r(t, { bold: true });
// rich text: **bold** and `code`
function rich(t, base = {}) {
  const out = []; const re = /(\*\*[^*]+\*\*|`[^`]+`)/g; let i = 0, m;
  while ((m = re.exec(t))) { if (m.index > i) out.push(r(t.slice(i, m.index), base)); const s = m[0]; out.push(s.startsWith('**') ? r(s.slice(2, -2), Object.assign({ bold: true }, base)) : r(s.slice(1, -1), Object.assign({ font: 'Consolas', size: 19 }, base))); i = m.index + s.length; }
  if (i < t.length) out.push(r(t.slice(i), base)); return out;
}
const T = (t, o = {}) => P(rich(t), o);
const bullet = (t, lvl = 0) => new Paragraph({ children: rich(t), numbering: { reference: 'bul', level: lvl }, spacing: { after: 60, line: 264 } });
let numInst = 0;
function steps(items) { numInst++; const ref = 'steps'; return items.map(t => new Paragraph({ children: rich(t), numbering: { reference: ref, level: 0, instance: numInst }, spacing: { after: 80, line: 264 } })); }
const border = { style: BorderStyle.SINGLE, size: 4, color: LINE };
const borders = { top: border, bottom: border, left: border, right: border };
function table(rows, widths, o = {}) {
  const fs = o.size || 19;
  const total = widths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: total, type: WidthType.DXA }, columnWidths: widths,
    rows: rows.map((row, i) => new TableRow({
      tableHeader: i === 0,
      children: row.map((c, j) => new TableCell({
        borders, width: { size: widths[j], type: WidthType.DXA },
        shading: { fill: i === 0 ? INK : (i % 2 ? 'FFFFFF' : ALU), type: ShadingType.CLEAR, color: 'auto' },
        margins: { top: 50, bottom: 50, left: 90, right: 90 },
        children: String(c).split('\n').map(line => new Paragraph({ spacing: { after: 0 }, children: i === 0 ? [r(line, { bold: true, color: 'FFFFFF', size: fs })] : rich(line, { size: fs, bold: j === 0 && o.boldFirst !== false && !!line && o.boldFirst !== undefined ? true : undefined }) })),
      })),
    })),
  });
}
function box(title, lines, fill, titleColor) {
  return new Table({
    width: { size: CW, type: WidthType.DXA }, columnWidths: [CW],
    rows: [new TableRow({ cantSplit: true, children: [new TableCell({
      borders: { top: { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' }, bottom: { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' }, left: { style: BorderStyle.SINGLE, size: 0, color: fill }, right: { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' } },
      width: { size: CW, type: WidthType.DXA }, shading: { fill, type: ShadingType.CLEAR, color: 'auto' }, margins: { top: 110, bottom: 110, left: 180, right: 180 },
      children: [new Paragraph({ spacing: { after: 60 }, children: [r(title, { bold: true, color: titleColor })] }), ...lines.map(l => typeof l === 'string' ? new Paragraph({ children: [r('☐  ', { color: titleColor }), ...rich(l)], spacing: { after: 40 } }) : l)],
    })] })],
  });
}
const note = (title, text, fill = PGOLD, col = GOLD) => box(title, [new Paragraph({ children: rich(text), spacing: { after: 0 } })], fill, col);
function img(f, maxWcm, caption) {
  const [w, h] = DIMS[f]; const maxW = maxWcm * 37.8; // px at 96 dpi
  const W = Math.min(maxW, w), H = W * h / w;
  const out = [new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 80, after: 40 }, children: [new ImageRun({ type: 'png', data: fs.readFileSync(path.join(__dirname, 'img', f)), transformation: { width: Math.round(W), height: Math.round(H) }, altText: { title: caption || f, description: caption || f, name: f } })] })];
  if (caption) out.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 160 }, children: [r(caption, { italics: true, size: 18, color: MUTED })] }));
  return out;
}
const lines = (n) => Array.from({ length: n }, () => new Paragraph({ spacing: { before: 120, after: 0 }, border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: LINE, space: 4 } }, children: [r(' ')] }));
const codeBlock = (text) => new Table({ width: { size: CW, type: WidthType.DXA }, columnWidths: [CW], rows: [new TableRow({ children: [new TableCell({ borders: { top: { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' }, bottom: { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' }, left: { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' }, right: { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' } }, width: { size: CW, type: WidthType.DXA }, shading: { fill: 'EEF1F4', type: ShadingType.CLEAR, color: 'auto' }, margins: { top: 100, bottom: 100, left: 160, right: 160 }, children: text.split('\n').map(l => new Paragraph({ spacing: { after: 0 }, children: [mono(l || ' ')] })) })] })] });
const sp = (n = 120) => new Paragraph({ spacing: { after: n }, children: [] });

function labBlock(L) {
  const out = [H1(`Lab ${L.n}: ${L.title}`)];
  out.push(table([['Module', 'Time', 'Start from'], [L.module, L.time, L.start]], [2600, 1700, CW - 4300], { size: 19 }));
  out.push(sp(80));
  out.push(T(`**Goal.** ${L.goal}`));
  if (L.before) L.before.forEach(x => out.push(x));
  out.push(H3('Steps'));
  out.push(...steps(L.steps));
  if (L.after) L.after.forEach(x => out.push(x));
  out.push(sp(60));
  out.push(box('Check it worked', L.check, PGRN, DIEL));
  if (L.questions) { out.push(H3('Your notes and answers')); L.questions.forEach(q => { out.push(T(q, { spacing: { before: 120, after: 40 } })); out.push(...lines(2)); }); }
  return out;
}

// ---------- LAB CONTENT ---------------------------------------------------------
const LABS = [
  { n: 1, title: 'Find your way round the ALU8', module: '3 · Tour of HLayout', time: 'Day 1, 10:45 · 30 min', start: 'checkpoints\\8bit_alu.gds', goal: 'Open the finished design and learn to navigate, inspect and measure it.',
    steps: ['**File → Open GDS…** and open `8bit_alu.gds`. Press **F** to fit the design in the window.', 'In the **Layers** tab, untick layer 3 (1st conductor) and layer 4 (dielectric), then tick them again. Watch what disappears.', 'Zoom to U1 (the right-hand large die) with the mouse wheel. Click one of its die pads and read the pin number in the **Properties** tab.', '**Edit → Highlight Net**, then click any pad of net C4. Follow the carry from U1 to U2. **Edit → Clear Net Highlight** when done.', 'Press **M** for the Measure tool and measure one wire bond from end to end.', 'On the command bar type `HILITE GND` and press Enter. Then type `HILITE OFF`.', 'Open the **Console** tab and run `print(s.sli()["complete"])`.'],
    check: ['C4 joins U1 pad 16 to U2 pad 7', 'Your bond measured between 1.0 and 2.5 mm', 'The console printed 46'],
    questions: ['Which layer would you hide to see the 2nd conductor on its own?', 'What does the status bar show while you measure?'] },
  { n: 2, title: 'Draw and edit the basics', module: '4 · Shapes, paths and cell instances', time: 'Day 1, 11:50 · 40 min', start: 'File → New; compare with checkpoints\\CP0_shapes_reference.gds', goal: 'Draw every primitive, make a cell, place it as SRefs and an ARef, and edit them.',
    before: [H3('Table 2.1  Drawing primitives'), table([['Primitive', 'How to draw it', 'Command'], ['Rectangle', 'Click two opposite corners', 'RECT layer x1,y1 x2,y2'], ['Polygon', 'Click points; Enter or double-click closes (3–200 points)', 'POLY layer x1,y1 x2,y2 …'], ['Path', 'Click the centre line; width and end cap from Path Style', 'PATH layer width cap x1,y1 …'], ['Circle', 'Click the centre, then a point on the radius', 'CIRCLE layer cx,cy r [segments]'], ['Text', 'Click the insertion point, type the string', 'TEXT layer x,y "text" [height]'], ['SRef (cell instance)', 'Pick a cell, click where it goes', 'SREF cell x,y [rot] [mag] [mirror]'], ['ARef (array instance)', 'Pick a cell, columns, rows, spacing', 'AREF cell x,y cols rows dx dy']], [2200, 4038, 3400], { size: 18 }), sp(),
      H3('Table 2.2  Path end caps'), table([['End cap', 'Where the path ends', 'Use it for'], ['Flush', 'Exactly at the end point', 'Ends that must stop on a pad edge'], ['Square (extended)', 'Half the width past the end point', 'Corners and joins that must overlap cleanly'], ['Round', 'A half circle, half the width past the end point', 'Smooth ends; matches a printed track end']], [2200, 3700, 3738]), sp(),
      H3('Table 2.3  Editing'), table([['Edit', 'How', 'Command'], ['Move', 'Drag, or arrow keys (one grid step)', 'MOVE dx,dy'], ['Reshape', 'Drag a vertex or an edge', '–'], ['Exact values', 'Double-click → Edit Selected…', 'PROPS'], ['Rotate', 'R (90° counter-clockwise)', 'ROTATE deg'], ['Mirror, magnify', 'Edit Selected… on an instance', '–'], ['Copy', 'Edit → Copy… with dx, dy', 'COPY dx,dy'], ['Extend a path', 'Edit → Continue Path…', '–'], ['Break an array', 'Edit → Smash Selected to SRefs', 'SMASH'], ['Join shapes', 'Window-select, Edit → Merge Selected…', 'UNION']], [2200, 4438, 3000], { size: 18 })],
    steps: ['Draw a rectangle, a polygon and a 32-segment circle on layer 3.', '**Setup → Path Style…**: width 0.4 mm. Draw three L-shaped paths on layer 5, one with each end cap.', 'Drag one corner of the rectangle: it becomes a polygon. **Ctrl+Z**.', 'Draw a 0.6 mm square pad with a small triangle in one corner. Select both and **Create → Create Cell from Selection…**, named `PAD`.', 'Place three PAD SRefs: one plain, one rotated 90° (**R**), one mirrored (**Edit Selected…**).', 'Place a 5 × 2 ARef of PAD at 1.1 mm pitch. Select it, **Edit → Smash Selected to SRefs**, and delete one pad.', 'Edit the PAD cell: make the pad larger. Watch every instance change.', 'Open `CP0_shapes_reference.gds` and compare with your drawing.'],
    after: [...img('shapes_demo.png', 13, 'CP0: every primitive, the three path caps, PAD as SRefs and as a 5 × 2 ARef'), note('Why the triangle?', 'A square pad looks the same rotated or mirrored. The triangle shows which way each instance is turned.')],
    check: ['All shapes and three path caps drawn', 'PAD placed as SRefs and an ARef', 'Editing PAD changed every copy'],
    questions: ['How far past the end point does a square end cap reach on a 0.4 mm path?', 'After Smash, does editing the PAD cell still change the nine pads? Why?'] },
  { n: 3, title: 'Set up the ALU8 process', module: '5 · Technology setup', time: 'Day 1, 13:35 · 30 min', start: 'File → New', goal: 'Build the ALU8 technology from a blank design and save it as a technology file.',
    before: [H3('Table 3.1  ALU8 layers'), table([['Layer', 'Name', 'Role', 'Thickness'], ['3', '1st Routing Conductor', '1st conductor', '12 µm'], ['4', 'Routing Dielectric', 'Dielectric (generated)', '30 µm'], ['5', '2nd Routing Conductor', '2nd conductor, die-attach pads, bond fingers', '12 µm'], ['6', 'DRC ERROR', 'DRC markers', '–'], ['20', 'Epoxy', 'Die attach epoxy', '–'], ['49', 'L49', 'Package pin pads (isolated)', '10 µm'], ['57', 'substrate utie', 'Substrate outline', '0.635 mm substrate'], ['58', 'Chip_Pads', 'Die pads (isolated)', '10 µm'], ['59', 'Chip_wire_Bond', 'Die wire bonds', '12 µm'], ['60', 'Header_wire_Bond', 'Header wire bonds', '10 µm'], ['61', 'Document 1', 'Outlines', '–'], ['62', 'RefDes', 'Reference designators', '–'], ['63', 'Dimention  2', 'Dimensions', '–']], [900, 2600, 4238, 1900]), sp(),
      H3('Table 3.2  Technology Properties'), table([['Tab', 'Field', 'ALU8 value'], ['Route', 'Layer A / Layer B', '3 / 5'], ['Route', 'Via cell', 'VIA_Layer3_to_Layer5'], ['Wire Bonds', 'Bond layer', '59'], ['Wire Bonds', 'Wire width / end ball', '0.03 mm / 0.1 mm'], ['Wire Bonds', 'Isolated pad layers', '58, 49'], ['I/O Pads', 'Pad width × length / bond inset', '0.9 × 0.9 mm / 0.2 mm'], ['Dielectric', 'Fill layer / margin / clearance / via window margin', '4 / 0.2 mm / 0.1 mm / 0.1 mm'], ['Thicknesses', 'Layers 3, 4, 5', '12, 30, 12 µm'], ['Ref-Des', 'Reference designator layer', '62']], [1800, 3900, 3938]), sp(),
      H3('Table 3.3  ALU8 design rules'), table([['Name', 'Kind', 'Layers', 'Value'], ['1st con min_width', 'MIN_WIDTH', '3', '200 µm'], ['1st con metal_2_metal', 'MIN_SPACE_BETWEEN', '3, 3', '200 µm'], ['BOND.L.MIN', 'BOND_MIN_LENGTH', '59', '1000 µm'], ['BOND.L.MAX', 'BOND_MAX_LENGTH', '59', '2500 µm'], ['BOND.ANGLE', 'BOND_MAX_ANGLE', '59, 58', '45°']], [2600, 2800, 1700, 2538])],
    steps: ['**Setup → Layers…**: name the layers as in Table 3.1. Pick colours you can tell apart.', '**Setup → Technology Properties…**, **Route** tab: Layer A 3, Layer B 5.', '**Wire Bonds** tab: layer 59, wire width 0.03 mm, isolated pad layers `58, 49`.', '**Dielectric** tab: layer 4, margin 0.2 mm, clearance 0.1 mm, via window margin 0.1 mm. **Thicknesses** tab: 3 = 12 µm, 4 = 30 µm, 5 = 12 µm. Press **OK**.', '**Tools → DRC…**: add the two layer-3 rules from Table 3.3, then press **Add Bond Rules** and set the limits to 1.0 mm, 2.5 mm and 45°.', '**File → Save Technology File…** as `my_alu8_tech.json`.', 'Compare with `alu8_tech.json`: open it in a text editor and look at the `drc_rules` and `bond_rules` entries.'],
    after: [note('Watch the units', 'Dialogs take millimetres; the command bar and the technology file use microns (DBU). A 30 µm wire is 0.03 in the Wire Bonds tab and 30 in the file.')],
    check: ['`my_alu8_tech.json` is saved', 'Five rules listed in Tools → DRC', 'Isolated pad layers read 58, 49'] },
  { n: 4, title: 'Mask ops, dielectric and a bond move', module: '6 · Mask operations, dielectric and bond edits', time: 'Day 1, 14:30 · 30 min', start: 'checkpoints\\CP3_routed.gds, saved as my_playground.gds', goal: 'Make derived layers, delete and rebuild the dielectric, and move a wire bond safely.',
    before: [H3('Table 4.1  Mask operations'), table([['Operation', 'Result', 'Typical hybrid use'], ['OVERSIZE', 'Every shape grown by a fixed amount', 'Glaze over a resistor; a clearance ring round a conductor'], ['UNDERSIZE', 'Every shape shrunk by a fixed amount', 'Check a minimum width survives'], ['AND', 'Where both layers are', 'Overlap of conductor and dielectric'], ['OR', 'Either layer', 'Combine two layers into one'], ['NOT', 'First layer minus the second', 'Bare substrate (57 NOT 3)'], ['XOR', 'One layer or the other, not both', 'What changed between two versions']], [1700, 3400, 4538]), sp(),
      H3('Table 4.2  The dielectric'), table([['Task', 'Menu', 'Command'], ['Create or replace the fill', 'Tools → Generate Dielectric Fill…', 'DIELECTRIC'], ['Delete the fill', 'Tools → Remove Dielectric Fill', 'DIELECTRIC CLEAR'], ['Add an opening', 'Draw on layer 4 (datatype 0), then Generate', '–'], ['Remove an opening', 'Delete its input shape, then Generate', '–'], ['Change margins', 'Technology Properties → Dielectric tab', '–'], ['Redraw vias and windows', 'Tools → Fix Vias…', 'VIA FIX']], [3000, 4338, 2300]), sp(),
      T('The dielectric has two parts on layer 4. **Inputs** (datatype 0) are the boundary, via windows and keep-outs. The **fill** (datatype 1) is generated from them. Remove Dielectric Fill deletes only the fill, so you can always generate it again.')],
    steps: ['**File → Save As…** `my_playground.gds`, so the checkpoint is never changed.', '**Tools → Mask Operations…**: OVERSIZE layer 3 by 100 µm into layer 55. Hide layer 3 and look at 55.', 'NOT: layer 57 minus layer 3 into layer 54. What does 54 show?', '**Tools → Remove Dielectric Fill**. Hide every layer but 4: what is left?', '**Tools → Generate Dielectric Fill…** with the default settings. How many fill pieces does the log report?', '**Edit → Move Wire Bond** on a U3 bond: drag an end until the preview turns red, then back to a legal length and click.', 'Run **Tools → DRC…**. Then **Ctrl+Z** the bond move.'],
    after: [...img('maskop_oversize.png', 6.5, 'Layer 3 oversized by 100 µm onto layer 55'), ...img('diel_before.png', 5.5, 'After Remove Dielectric Fill: only the inputs remain'), ...img('diel_after.png', 5.5, 'After Generate Dielectric Fill')],
    check: ['Layer 55 is layer 3 grown by 0.1 mm', 'Fill removed and regenerated', 'Bond move checked with DRC'],
    questions: ['What does layer 54 show?', 'What was left on layer 4 after Remove Dielectric Fill, and how many fill pieces came back?', 'Which DRC rule flags a bond moved too far?'] },
  { n: 5, title: 'Size, place and trim resistors', module: '7 · Thick-film resistor design', time: 'Day 1, 15:55 · 40 min', start: 'File → New, then Setup → Load Technology File… alu8_tech.json', goal: 'Size four resistors with the Resistor Designer, check them by hand, place them and trim one.',
    before: [H3('How the Resistor Designer sizes a resistor'), T('**R = Rsh × L / W.** Rsh is the ink sheet resistance in Ω/□; L / W is the number of **squares**. Laser trimming only cuts ink away, so it can only raise the resistance: print low, then trim up to value.'),
      ...steps(['**Width from power.** Pick the smallest width in Table 5.1 whose after-trim rating covers the power.', '**Aim value** = value × (aim % + tolerance %). Default aim 75 %.', '**Squares** = aim value / Rsh, for each ink.', '**Aspect window.** Keep inks giving 0.25 to 4 squares.', '**Recommend** the ink closest to one square (smallest |ln squares|).', '**Length** L = squares × W.']),
      H3('Table 5.1  Width and power (ALU8 rules)'), table([['Width (mm)', '1.0', '1.4', '1.8', '2.0', '2.4', '2.8', '3.0', '3.4', '3.8'], ['Full power (mW)', '62.5', '125', '200', '250', '375', '500', '625', '750', '1000'], ['After trim (mW)', '31.3', '62.5', '100', '125', '187.5', '250', '312.5', '375', '500']], [1838, 867, 867, 867, 867, 867, 867, 867, 867, 870], { size: 18 }), sp(),
      T('Inks: 10, 30, 100, 300 Ω/□, 1, 3, 10, 30, 100, 300 kΩ/□ and 1 MΩ/□. Layout rules: ink overlaps each pad by **A** 0.3 mm; pad runs **B** 0.5 mm beyond; pad **C** 0.3 mm wider than the ink each side; glaze **D** 0.3 mm all round; dielectric keep-out **E** 1.0 mm.'),
      ...img('resistor_anatomy.png', 9, 'A 1 kΩ resistor: ink 1.72 × 1.40 mm, pads 0.80 × 2.00 mm, glaze, keep-out')],
    steps: ['**Tools → Thick-Film Resistor Designer…**. Check **Design Rules…**: aim 75 %, aspect 0.25–4.', 'Add R1 1 kΩ 5 % 50 mW, R2 10 kΩ 1 % 100 mW, R3 47 Ω 5 % 200 mW, R4 220 kΩ 10 % 10 mW, with width **Auto from power**.', 'Fill in Table 5.2 by hand first, then compare with the **Solutions** tab.', '**Auto-Select All**, then **Place All in Layout…**. Look at each resistor\'s layers: ink, pads, glaze, keep-out.', 'Select R1 and open **Trim Simulator…**: plunge cut to 25 % of the width, 40 µm kerf; then **Add L-Cut** 400 µm.', 'Fill in Table 5.3.'],
    after: [H3('Table 5.2  Your hand sizing'), table([['Ref', 'Value', 'Power', 'Width (mm)', 'Aim (Ω)', 'Ink (Ω/□)', 'Squares', 'L (mm)'], ['R1', '1 kΩ 5 %', '50 mW', '', '', '', '', ''], ['R2', '10 kΩ 1 %', '100 mW', '', '', '', '', ''], ['R3', '47 Ω 5 %', '200 mW', '', '', '', '', ''], ['R4', '220 kΩ 10 %', '10 mW', '', '', '', '', '']], [700, 1400, 1100, 1300, 1300, 1300, 1238, 1300]), sp(),
      H3('Table 5.3  Trimming R1'), table([['Stage', 'Resistance (Ω)', 'Sensitivity (Ω/µm)'], ['As printed', '', '–'], ['After plunge, 25 % of width', '', ''], ['After L-cut, 400 µm', '', '']], [3800, 2900, 2938]), sp(),
      ...img('trim_curve.png', 13, 'R1 trim: a fast plunge, then a slow, controllable L-cut')],
    check: ['Your hand sizes match the Designer', 'Four resistors placed with glaze and keep-out', 'R1 trims to about 995 Ω'],
    questions: ['Why does R3 need a 2.8 mm body and not 2.4 mm?', 'Why finish with an L-cut instead of a deeper plunge?'] },
  { n: 6, title: 'Read the ALU8 netlist', module: '8 · Circuit and netlist', time: 'Day 2, 09:20 · 25 min', start: 'checkpoints\\alu8_die.net, alu8_netlist.csv', goal: 'Find the facts you will need for placement and routing in the netlist files.',
    before: [T('Use the full net list in **Appendix A** of this handout, or open the files themselves.')],
    steps: ['Open `alu8_netlist.csv` in a spreadsheet or text editor. Columns: net, ref, part, lcc_pin, dip_die_pad, pin_name, pin_type.', 'How many pins does **GND** have? Which dies are on it?', 'Which die pads carry the carry net **C4**?', 'Which nets join U1 and U2 directly?', 'Open `alu8_die.net` in a text editor and find net **F7**. How many pins does it have, and why are two header pins on it?', 'Which pins of U4 are tied to VCC, and why?'],
    check: ['GND: 11 pins, all six dies plus J1 and J2', 'C4: U1 pad 16 and U2 pad 7', 'Shared by U1 and U2: S0–S3, M and C4', 'F7: 6 pins'],
    questions: ['GND pins:', 'C4 die pads:', 'Nets shared by U1 and U2:', 'F7 pins, and why two header pins:', 'U4 pins on VCC, and why:'] },
  { n: 7, title: 'Build a parts library with the wizards', module: '9 · Wizards, bare dies and the package', time: 'Day 2, 10:15 · 45 min', start: 'File → New, Setup → Load Technology File… alu8_tech.json, then attach checkpoints\\my_parts.gds', goal: 'Make a via, a capacitor land and a 54LS00 die with the three wizards, save them in your own parts library, and place them.',
    before: [T('The three wizards on the **Tools** menu add new parts to a library. Each one writes the finished cell straight into an **attached** library file, so attach the library first (**Setup → Attach Library…**). Without one, the wizard tells you to attach one and stops. `my_parts.gds` is your own copy of the example parts library `chip_wire.gds`; it already holds the die-pad cells `chip_pin_1` (200 µm, marks pin 1) and `chip_pin` (100 µm).'),
      H3('Table 7.1  The wizards'), table([['Wizard', 'Makes', 'Command / Python'], ['Via Wizard…', 'Square or round pad on two conductors, with an optional window in the dielectric between them. Defaults come from the Autoroute via and the Dielectric tab, so it matches the routed vias.', 'VIA · create_via_cell()'], ['Pad Wizard…', 'A conductor pad with a paste (or epoxy) pad undersized by a margin on every side; one pad or a rows × cols grid. Two-pad footprints are numbered 1 and 2 for you.', 'PAD · create_pad_cell()'], ['Chip Wizard…', 'A bare-die footprint: body outline, epoxy, optional die-attach pad, then one click per pin. Saved to the first attached library when the last pin is placed.', 'create_chip_cell() + export_cell_to_library()']], [1700, 5438, 2500], { size: 18 }), sp(),
      H3('Table 7.2  Settings for this lab'), table([['Part', 'Wizard settings'], ['VIA_R300', 'Shape round, size 0.3 mm, Layer 1 = 3, Layer 2 = 5, Add dielectric via window on 4, window 0.5 mm, Cell name VIA_R300'], ['LAND_0805', 'Cell name LAND_0805, length 1.0, width 1.4, paste undersize 0.1 mm, conductor layer 5, paste layer 20 (epoxy), rows × cols 1 × 2, pitch X 1.9 mm'], ['MY_54LS00', 'Chip 1.5 × 1.5 mm, graphics layer 61, epoxy 1.4 × 1.4 mm on 20, die-attach pad on 5, 0.2 mm larger; pin 1 cell chip_pin_1, pins 2..N chip_pin, 14 pins; Save to attached library']], [1800, CW - 1800], { size: 18 }), sp(),
      H3('Table 7.3  Pin positions for MY_54LS00 (mm from the die centre, grid 0.1 mm)'), table([['Pin', '1', '2', '3', '4', '5', '6', '7'], ['x, y', '−0.2, 0.6', '−0.4, 0.6', '−0.6, 0.4', '−0.6, 0', '−0.6, −0.4', '−0.4, −0.6', '−0.2, −0.6'], ['Pin', '8', '9', '10', '11', '12', '13', '14'], ['x, y', '0.2, −0.6', '0.4, −0.6', '0.6, −0.4', '0.6, 0', '0.6, 0.4', '0.4, 0.6', '0.2, 0.6']], [1040, 1228, 1228, 1228, 1228, 1228, 1228, 1230], { size: 18 })],
    steps: ['**Setup → Attach Library…** and choose `checkpoints\\my_parts.gds`.', '**Tools → Via Wizard…** with the Table 7.2 settings. Press **Create**.', '**Tools → Pad Wizard…** with the Table 7.2 settings. Press **Create**.', '**Tools → Chip Wizard…** with the Table 7.2 settings. Press **Start Placing Pins**.', 'Click pins 1 to 14 at the Table 7.3 positions, counter-clockwise from pin 1. The status bar asks for each pin; each is labelled with its number.', '**Create → Find & Place Cell…**: place VIA_R300, LAND_0805 and MY_54LS00. Select the land and read its pin numbers in **Properties**.', '**Setup → Library Cells…**: find your three cells and their created and modified dates.', 'Open `checkpoints\\CP1_dies_on_substrate.gds`. Compare MY_54LS00 with `DIE_54LS00` (U6). Then open CP2: where does the substrate sit?'],
    after: [...img('wizards_demo.png', 13, 'The three parts: VIA_R300, LAND_0805 and MY_54LS00'), table([['Die cell', 'Used as', 'Size', 'Pads'], ['DIE_54AS181', 'U1, U2', '3.0 × 3.0 mm', '24'], ['DIE_54LS27', 'U3', '1.5 × 1.5 mm', '14'], ['DIE_54LS30', 'U4', '1.5 × 1.5 mm', '14'], ['DIE_54LS86', 'U5', '1.5 × 1.5 mm', '14'], ['DIE_54LS00', 'U6', '1.5 × 1.5 mm', '14']], [2600, 2000, 2800, 2238]), sp(), note('Die sizes', 'TI does not publish die drawings for these parts. The ALU8 die sizes are representative; on a real job use the supplier\'s die drawing.')],
    check: ['VIA_R300, LAND_0805 and MY_54LS00 are in my_parts.gds', 'LAND_0805 pads are pins 1 and 2', 'MY_54LS00 has 14 numbered pins and a 1.9 mm die-attach pad'],
    questions: ['How big is the dielectric window on VIA_R300, and where does that size come from?', 'Why does the lab not use the default via name?', 'Why must the die pins run in the same order as the LCC terminals?'] },
  { n: 8, title: 'Place the ALU8', module: '10 · Auto Place', time: 'Day 2, 11:45 · 45 min', start: 'checkpoints\\CP1_dies_on_substrate.gds', goal: 'Place the six dies from the netlist with the shortest total net length you can get.',
    before: [table([['Setting', 'Value'], ['Netlist', 'alu8_die.net (pins numbered by die pad)'], ['Grid', '50 µm'], ['Clearance', '0.3 mm'], ['Bond reach', '1.5 mm'], ['Thermal', '1'], ['Effort / seed', '1 / 0']], [2600, CW - 2600])],
    steps: ['**Tools → Auto Place…** and choose `alu8_die.net`.', 'On the **Parts** tab check every U1–U6 maps to a `DIE_` cell with family **die**.', 'Enter the settings above and press **Run**.', 'Tick **View → Straight-Line Interconnects**. Write the total length from the status bar in the table below.', 'Improve the placement by dragging and rotating (**R**) parts, or with **Improve**. Record your best total.', 'On the command bar run `AUTOPLACE WEIGHT C4 3`, then **Improve**. What moved?', '**File → Save As…** `my_alu8_placed.gds`.'],
    after: [...img('alu8_autoplace_preview.png', 15, 'CP1 before (left) and after Auto Place (right)'), table([['Run', 'Total SLI length (mm)', 'What you changed'], ['First Auto Place', '', ''], ['After hand moves', '', ''], ['After C4 weight 3 + Improve', '', '']], [3000, 2600, CW - 5600])],
    check: ['No overlaps; every die inside the I/O pad ring', 'Totals recorded in the table', 'U1 and U2 side by side'] },
  { n: 9, title: 'Bond the ALU8', module: '11 · Nets and wire bonds', time: 'Day 2, 13:50 · 40 min', start: 'checkpoints\\CP2_bonded_ready_to_route.gds', goal: 'Check net names, practise bonding, remove unused bonds and follow the header bonds.',
    steps: ['**Edit → Highlight Net**: click the I/O pad for header pin 18 and check it is VCC; click U1 die pad 16 and check it is C4.', 'Zoom to U3. **Edit → Delete Wire Bond** on one of its bonds, then redraw it with **Create → Wire Bond**: click the die pad, then the landing point.', '**Edit → Move Wire Bond**: drag an end until the preview turns red (too long or too short). Press **Esc**, or **Ctrl+Z** if you committed it.', 'On the command bar run `UNUSEDBONDS`. Note which pads are listed.', 'Run **Tools → Remove Unused Bonds…**. Note the new die cell names in the cell list.', 'In the Layers tab hide everything except 49, 59 and 60. Follow a header bond from a package pin to its I/O pad.', 'Save as `my_alu8_bonded.gds`.'],
    after: [...img('die_bonded_zoom.png', 9, 'U1 bonded in CP2: die pads to bond fingers on the 2nd conductor')],
    check: ['Your redrawn bond is between 1.0 and 2.5 mm', 'You can name the unused pads, or explain why none are listed', 'You traced a header bond pin to pad'],
    questions: ['Unused pads listed by UNUSEDBONDS:', 'Why remove unused bonds before routing?'] },
  { n: 10, title: 'Route the ALU8', module: '12 · Routing with Freerouting', time: 'Day 2, 15:15 · 45 min', start: 'my_alu8_bonded.gds, or checkpoints\\CP2_bonded_ready_to_route.gds', goal: 'Route every net with Freerouting and get 46 of 46 complete.',
    before: [H3('Table 10.1  ALU8 Autoroute settings'), table([['Field', 'Value'], ['Launcher / .jar', 'freerouting.exe (Java build); a .jar needs Java 25 or later'], ['Signal width / power width', '200 µm / 200 µm'], ['Clearance', '200 µm'], ['Signal via pad / power via pad', '300 µm / 300 µm (dielectric window 500 µm)'], ['Route passes / optimize passes', '25 / 25, stop optimizing below 1 %'], ['Bond finger / die keep-out', '200 µm / 200 µm'], ['Via cost / against-direction cost', '35 / 3'], ['Power nets', 'VCC, GND'], ['90-degree routing only', 'On'], ['Remove ALL existing tracks and vias', 'On'], ['Layers', 'Both conductors'], ['Bond fingers on', '2nd conductor'], ['Track directions', '1st horizontal, 2nd vertical'], ['Via optimisation at the end', 'On'], ['Shorten tracks at the end', 'On']], [3800, CW - 3800])],
    steps: ['**Tools → Autoroute (Freerouting)…**. Check the status line says Freerouting is ready; if not, set the **Launcher / .jar** path.', 'Enter the settings in Table 10.1.', 'Press **Route**. Watch the percentage; the window comes back when the route finishes.', 'Read the log: tracks and vias imported, bond fingers laid, and **Nets complete: N of 46**.', 'If any nets are left: **View → Unrouted Nets…**, then route again with **Keep existing tracks and vias** ticked.', 'Note how many vias the log reports before and after via optimisation.', 'On the command bar run `SHORTS`. Save as `my_alu8_routed.gds`.'],
    after: [...img('stage4_routed.png', 15, 'The routed ALU8: 1st conductor orange, 2nd conductor purple, dielectric blue'), note('Via windows', 'Each via window in the dielectric is the via pad plus the via window margin on every side: 300 µm + 2 × 0.1 mm = 500 µm.')],
    check: ['Nets complete: 46 of 46', 'SHORTS reports none', 'my_alu8_routed.gds saved'],
    questions: ['Vias before and after optimisation:', 'Route time:'] },
  { n: 11, title: 'Break it and fix it', module: '13 · Editing the route', time: 'Day 2, 16:15 · 30 min', start: 'my_alu8_routed.gds, or checkpoints\\CP3_routed.gds', goal: 'Rip up a net, find it again with the tools and route it back by hand.',
    steps: ['Run `HILITE C4` to see the carry net, then `HILITE OFF`.', '**Edit → Rip Up and Reroute**: click on the C4 track near U1, then near U2. The track between is removed and you are in Route mode.', 'Press **Esc** to leave Route mode. Open **View → Unrouted Nets…**: is C4 listed? How long is the gap?', 'Route C4 back by hand with the Route tool. Press **Space** to drop a via and change conductor.', 'Drag a segment of another net by its edge. Watch the via at its end move with it. **Ctrl+Z** to undo.', 'Run `SHORTS`. If it reports anything, use Rip Up and Reroute to fix it.', 'Check the status bar or `print(s.sli())`: 46 complete, total length 0.'],
    check: ['C4 complete again', 'SHORTS reports none', 'Unrouted Nets list empty'] },
  { n: 12, title: 'Prove the design is right', module: '14 · Verification', time: 'Day 3, 09:35 · 40 min', start: 'checkpoints\\CP3_routed.gds', goal: 'Clear the DRC, check bonds and connectivity, and estimate die temperatures.',
    steps: ['**Tools → DRC…** → **Run**, with markers on layer 6. How many violations, and which rule?', 'Click each violation to zoom to it. Fix it by moving or rerouting the short segment, then run DRC again.', '**Tools → Extract Netlist…**: tick layers 3 and 5 as **Conductor**, 58 and 49 as **Isolated**, keep **Include wire bonds** on. Run. Note any opens or shorts.', 'Run `SHORTS` and check the SLIs show 46 complete.', '**Tools → Thermal Analysis…**, **Powers** tab: U1 and U2 400 mW, U3 25 mW, U4 15 mW, U5 40 mW, U6 20 mW. Run.', '**Boundary** tab: change the base to cooled by a heat-transfer coefficient of 200 W/m²K. Apply and run again. Compare.', 'Save as `my_alu8_checked.gds`.'],
    after: [...img('drc_zoom.png', 8, 'The DRC violation in CP3, ringed: 1st-conductor copper closer than 200 µm'), ...img('thermal_top.png', 15, 'Temperature rise with the base cooled at 200 W/m²K'), note('About the powers', 'The die powers here are representative values for the exercise, not datasheet figures. The model leaves out the package, wire bonds and airflow: treat the result as a design-stage estimate.'), sp(), table([['Die', 'Power', 'Fixed base (°C)', 'Cooled base (°C)'], ['U1', '400 mW', '', ''], ['U2', '400 mW', '', ''], ['U3', '25 mW', '', ''], ['U4', '15 mW', '', ''], ['U5', '40 mW', '', ''], ['U6', '20 mW', '', '']], [1800, 1800, 3000, 3038])],
    check: ['DRC reports no violations', 'No opens or shorts', 'U1 about 34 °C with the cooled base'],
    questions: ['DRC violations found, rule and location:', 'How did you fix them?'] },
  { n: 13, title: 'Make the output set', module: '15 · Manufacturing outputs', time: 'Day 3, 10:35 · 25 min', start: 'my_alu8_checked.gds, or CP3 with the DRC fixed', goal: 'Produce every file the print line, the bonder and the tester need.',
    steps: ['**File → Gerber Export Sets…**: run the set **1-CON** into a folder named `out`.', 'Count the `.gbr` files and note which layers they are.', '**File → DXF Export Sets…**: export the conductors as DXF.', '**File → Export Pick and Place…** as `out\\alu8_pnp.csv`. Check U1–U6 are listed with kind **die**.', 'Write the bond list for the bonder with the script below (paste it into the Console tab).', '**Tools → Extract Netlist…** → **Export…** as CSV.', '**File → Archive Design**.'],
    after: [H3('Bond list script'), codeBlock('import csv, math\nrows = s.bond_departures()\nwith open("out/alu8_bonds.csv", "w", newline="") as f:\n    w = csv.writer(f)\n    w.writerow(["die", "x1_um", "y1_um", "x2_um", "y2_um", "length_mm", "angle_deg"])\n    for b in rows:\n        w.writerow([b["die"], *b["a"], *b["b"],\n                    round(math.dist(b["a"], b["b"]) / 1000, 3), b["angle_deg"]])\nprint(len(rows), "bonds")'), sp(), note('Why a script?', 'A design routed with Freerouting keeps no bond diagram, so HYBRID BONDS has nothing to export. HYBRID BONDS and BONDTABLE work on designs from the HLayout chip-and-wire router.')],
    check: ['One Gerber per print layer', 'Six dies in the pick-and-place file', 'Bond list with 94 rows', 'Archive zip written'] },
  { n: 14, title: 'Link 2D cells to 3D models', module: '16 · Linking 2D cells to 3D STEP models', time: 'Day 3, 11:50 · 40 min', start: 'checkpoints\\CP3_routed.gds, saved as my_alu8_3d.gds', goal: 'Check the package link, add a part that links automatically, and break and fix a link.',
    before: [H3('How 2D becomes 3D'), bullet('**Films**: each drawn layer is extruded at its fired thickness, in print order. The dielectric uses only the generated fill (datatype 1).'), bullet('**Bonds**: gold loops landing on the surface under each end.'), bullet('**Parts**: each cell instance is replaced by its linked STEP model, or drawn as a box of its footprint.'), bullet('**The link is the origin**: the STEP model is placed at the instance origin, so its origin must match the 2D cell origin, with z = 0 on the seating surface.'), sp(),
      H3('Table 14.1  Component model fields'), table([['Field', 'Meaning', 'ALU8 KOVAR_DIP40'], ['mode', 'model (STEP file) or box', 'model'], ['model_path', 'STEP file; relative to the design folder or absolute', 'C:/dev/HLayout/examples/step_models/kovar_dip/KOVAR_DIP40_0.9in.step'], ['rotation_deg', 'Correction when the model\'s axes differ from the cell', '0'], ['z_ref', 'surface, or substrate_bottom for headers', 'substrate_bottom'], ['z_offset_um', 'Shift up or down', '0'], ['height_um', 'Box height, or model height hint', '1000']], [1800, 3838, 4000], { size: 18 }), sp(),
      T('**Automatic matching:** passives match a STEP file by the EIA size code in the cell name (CAP_0805 → CAP_0805.step); packages by pin count and pitch. A manual link always wins. Build the shared library once with `python build_STEP_parts\\build_all.py`.')],
    steps: ['**File → Component Library Manager…**: which file is KOVAR_DIP40 linked to? Is the link manual or auto?', '**Setup → Attach Library…**: `hlayout\\examples\\chip_wire.gds`. Place `CAP_0805` near U1 as C1, a decoupling capacitor.', 'Press **Refresh** in the Library Manager: does C1 match `CAP_0805.step` automatically?', 'Set a rotation correction of 90° on CAP_0805 and export to FreeCAD. What happens? Then **Clear Override**.', 'Copy `KOVAR_DIP40_0.9in.step` next to your design and **Set STEP File…** to it. Note the path is now relative.', '**File → Export to FreeCAD (3D Macro)…**. Check C1 sits on its pads and the substrate sits in the package well.'],
    after: [...img('alu8_3d_die.png', 7, 'A 54AS181 die, its bonds and the printed films in 3D')],
    check: ['C1 auto-matched to CAP_0805.step', 'Package linked with a relative path', 'Model looks right in FreeCAD'],
    questions: ['What did the 90° correction do to C1, and why?', 'Why is a relative path better for a design you share?'] },
  { n: 15, title: 'Draw it and see it in 3D', module: '17 · Documentation and 3D', time: 'Day 3, 13:35 · 25 min', start: 'my_alu8_checked.gds, or CP3', goal: 'Build the assembly drawing and the 3D model of your ALU8.',
    steps: ['**Tools → Top View…**: build it on layer 56 with conductor hatching on.', '**Tools → Assembly Drawing…**: A3, portrait. Title **8-bit ALU** (the example still says 4 bit ALU), drawing number ALU8-001, your initials and today\'s date.', 'Pin functions: enter at least `18 = VCC` and `3 = GND`; the full list is in Appendix B.', 'Press **Build**, then **Print Preview**.', '**File → Export to FreeCAD (3D Macro)…**. Run the macro in FreeCAD and orbit the model.'],
    after: [...img('alu8_assembly.png', 7, 'The ALU8 assembly drawing'), ...img('alu8_3d.png', 11, 'The ALU8 in FreeCAD, seated in its Kovar DIP40')],
    check: ['Title block reads 8-bit ALU', 'Sheet has the top view and the pin table', 'The ALU8 opens in FreeCAD'] },
  { n: 16, title: 'Optional: script the build', module: '18 · Automation and support', time: 'Day 3, 14:10 · 20 min', start: 'checkpoints\\CP2_bonded_ready_to_route.gds', goal: 'Run routing, checks and outputs from a script.',
    steps: ['Open CP2 and the **Console** tab.', 'On the command bar run `FREEROUTE RUN fresh=1 fingerlayer=2`.', 'In the console run `print(s.sli()["complete"])` and `print(len(s.run_drc()))`.', 'Save the script below as `build_alu8.py` next to CP2 and run it with **Tools → Run Script File…**.', 'Open **Help → Report Bug…** and press **Preview** to see what a report would contain. Close it without sending.'],
    after: [codeBlock('from hlayout.scripting.api import Session\ns = Session.open_design("CP2_bonded_ready_to_route.gds")\n\ns.freeroute(remove_existing=True)   # uses the saved Autoroute settings\nprint(s.sli()["complete"], "of 46 nets")\nprint(len(s.run_drc()), "DRC violations")\n\ns.export_gerber("out/alu8")\ns.export_pick_and_place("out/alu8_pnp.csv")\ns.generate_assembly_drawing(size="A3", title="8-bit ALU")\ns.save_design("alu8_scripted.gds")')],
    check: ['46 of 46 from the script', 'Outputs in the out folder', 'You have seen a bug report preview'] },
];

// ---------- CAPSTONE --------------------------------------------------------------
function capstoneBlock() {
  const out = [H1('Capstone: the ALU8 on your own')];
  out.push(table([['When', 'Time', 'Start from'], ['Day 3 afternoon', '14:30 · 105 min', 'checkpoints\\CP1_dies_on_substrate.gds and alu8_tech.json']], [2600, 1700, CW - 4300], { size: 19 }));
  out.push(sp(80));
  out.push(T('**Brief.** Take the ALU8 through the whole flow without step-by-step help. Work alone or in pairs.'));
  out.push(note('Engineering change', 'U4\'s five unused inputs (pads 4, 5, 6, 11 and 12) are no longer tied straight to VCC. They go to VCC through a printed 1 kΩ ±5 % pull-up resistor, R1, rated 25 mW.'));
  out.push(H3('Tasks'));
  out.push(...steps(['Size R1 in the Resistor Designer and place it near U4.', 'Make the new net **PU**: U4.4, U4.5, U4.6, U4.11, U4.12 and R1.2. R1.1 joins VCC. Update the net names (`NETNAMES IMPORT`).', 'Auto Place, bond, remove unused bonds and route: **47 of 47** nets.', 'DRC clean, no shorts, the dielectric keep-out round R1 opened in the fill, thermal check.', 'Outputs, an assembly drawing titled 8-bit ALU, and a 3D model with R1 visible.']));
  out.push(H3('R1 sizing'));
  out.push(table([['Width (mm)', 'After-trim rating (mW)', 'Aim (Ω)', 'Ink (Ω/□)', 'Squares', 'L (mm)'], ['', '', '', '', '', '']], [1600, 2100, 1400, 1500, 1500, 1538]));
  out.push(sp(60));
  out.push(box('Hand in', ['Your .gds and its .hlaux', 'Gerber and DXF set', 'Pick and place file and bond list', 'Assembly drawing as PDF', 'A screenshot of the 3D model'], PGRN, DIEL));
  out.push(H3('Your notes'));
  out.push(...lines(6));
  return out;
}

// ---------- QUIZ ---------------------------------------------------------------
const QUIZ = [
  ['Which netlist file should Auto Place use for the ALU8 bare dies?', ['alu8.net', 'alu8_die.net', 'alu8_netlist.txt', 'io_pinout.csv'], 1],
  ['In the netlist text U1.14[12](GND), what is 12?', ['The LCC terminal', 'The die pad (DIP pin)', 'The net number', 'The header pin'], 1],
  ['Why are layers 58 and 49 isolated pad layers?', ['They are documentation only', 'They are printed under the dielectric', 'They sit above the substrate and connect only through wire bonds', 'They hold DRC markers'], 2],
  ['How much larger than the die is the die-attach pad?', ['0.05 mm all round', '0.1 mm all round', '0.2 mm all round', '1.5 mm all round'], 2],
  ['On which layer are the ALU8 bond fingers laid by the router?', ['1st conductor', '2nd conductor', 'Dielectric', 'Die-pad layer'], 1],
  ['What is the ALU8 maximum wire-bond departure angle?', ['30°', '45°', '60°', '90°'], 1],
  ['Why does Auto Place grow each die by the bond reach?', ['To keep the dies cool', 'To leave room for the bond fingers and measure nets to them', 'To meet the DRC spacing rule', 'To align dies to the grid'], 1],
  ['What does Tools → Remove Unused Bonds need first?', ['A routed design', 'Net names', 'A DRC run', 'A thermal model'], 1],
  ['Which statement about Freerouting in HLayout is true?', ['It ignores your DRC rules', 'Your DRC width and spacing rules set the router widths and clearance', 'It routes wire bonds', 'It runs only on one conductor'], 1],
  ['Why can spacing DRC not find a short circuit?', ['Shorts are on the dielectric', 'Touching copper merges into one shape, so there is no gap to measure', 'DRC skips power nets', 'Shorts only show in 3D'], 1],
  ['What does via optimisation do?', ['Adds vias at every turn', 'Moves tracks to the other conductor so vias can be removed', 'Enlarges every via window', 'Converts vias to bond fingers'], 1],
  ['Outside the printed dielectric, which conductor may carry tracks?', ['Only the 1st conductor', 'Only the 2nd conductor', 'Both', 'Neither'], 0],
  ['What does the .hlaux file hold?', ['Only the geometry', 'The technology, net names, libraries and settings for the design', 'The Gerber files', 'The FreeCAD model'], 1],
  ['With a cooled base, roughly how hot did U1 run in Lab 12?', ['26 °C', '34 °C', '85 °C', '125 °C'], 1],
  ['Where does the assembly drawing take its pin count from when Pins is blank?', ['The netlist', 'The package placed in the design', 'The DRC rules', 'The technology file'], 1],
  ['Which path end cap extends half the path width past the end point with a square end?', ['Flush', 'Square (extended)', 'Round', 'Mitred'], 1],
  ['You enlarge the pad in the PAD cell. What happens to the 10 PAD instances in an ARef?', ['Nothing; instances are copies', 'Only the first one changes', 'All of them change', 'The ARef is smashed'], 2],
  ['What does Tools → Remove Dielectric Fill delete?', ['Everything on layer 4', 'Only the generated fill; the inputs stay so you can generate it again', 'Only the via windows', 'The dielectric layer from the technology'], 1],
  ['Why is a 1 kΩ ±5 % resistor printed at about 800 Ω?', ['To save ink', 'Firing always raises the value 25 %', 'Laser trimming can only raise resistance, so it is printed low and trimmed up', 'To stay inside the 0.25–4 squares window'], 2],
  ['A STEP model sits in the wrong place in 3D. What must match first?', ['Its file name and the cell name', 'Its origin and the 2D cell origin', 'Its colour and the layer colour', 'Its units and the Gerber units'], 1],
];

// ---------- STUDENT HANDOUT -----------------------------------------------------
const numbering = { config: [
  { reference: 'bul', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 300 } } } }, { level: 1, format: LevelFormat.BULLET, text: '–', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 1000, hanging: 300 } } } }] },
  { reference: 'steps', levels: [{ level: 0, format: LevelFormat.DECIMAL, text: '%1.', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 360 } }, run: { bold: true, color: GOLD } } }] },
  { reference: 'quiz', levels: [{ level: 0, format: LevelFormat.DECIMAL, text: '%1.', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 540, hanging: 400 } }, run: { bold: true } } }, { level: 1, format: LevelFormat.LOWER_LETTER, text: '%2)', alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 1080, hanging: 360 } } } }] },
] };
const styles = {
  default: { document: { run: { font: 'Calibri', size: 21, color: '24303F' } } },
  paragraphStyles: [
    { id: 'Heading1', name: 'Heading 1', basedOn: 'Normal', next: 'Normal', quickFormat: true, run: { font: 'Cambria', size: 36, bold: true, color: INK }, paragraph: { spacing: { before: 120, after: 200 }, outlineLevel: 0 } },
    { id: 'Heading2', name: 'Heading 2', basedOn: 'Normal', next: 'Normal', quickFormat: true, run: { font: 'Cambria', size: 27, bold: true, color: INK }, paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 1 } },
    { id: 'Heading3', name: 'Heading 3', basedOn: 'Normal', next: 'Normal', quickFormat: true, run: { font: 'Calibri', size: 22, bold: true, color: GOLD }, paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 2 } },
  ],
};
function hdrftr(label) {
  return {
    headers: { default: new Header({ children: [new Paragraph({ border: { bottom: { style: BorderStyle.SINGLE, size: 4, color: LINE, space: 4 } }, children: [r('HLayout in Practice', { size: 17, color: MUTED }), r(label, { size: 17, color: MUTED }), new TextRun({ children: [new PositionalTab({ alignment: PositionalTabAlignment.RIGHT, relativeTo: PositionalTabRelativeTo.MARGIN, leader: PositionalTabLeader.NONE }), 'ALU8 chip-and-wire hybrid'], size: 17, color: MUTED, font: 'Calibri' })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ children: ['Page ', PageNumber.CURRENT, ' of ', PageNumber.TOTAL_PAGES], size: 17, color: MUTED, font: 'Calibri' })] })] }) },
  };
}
const pageProps = { page: { size: { width: PAGE_W, height: 16838 }, margin: { top: 1134, bottom: 1134, left: MARGIN, right: MARGIN, header: 560, footer: 560 } } };

function cover(subtitle, extra) {
  return [
    new Paragraph({ spacing: { before: 600, after: 200 }, children: [new ImageRun({ type: 'png', data: fs.readFileSync(path.join(__dirname, 'img', 'hlayout_splash.png')), transformation: { width: 110, height: 110 }, altText: { title: 'HLayout logo', description: 'HLayout logo', name: 'logo' } })] }),
    new Paragraph({ spacing: { after: 120 }, children: [r('HLayout in Practice', { font: 'Cambria', size: 60, bold: true, color: INK })] }),
    new Paragraph({ spacing: { after: 360 }, children: [r('Designing a chip-and-wire hybrid, from netlist to 3D', { size: 30, color: GOLD })] }),
    new Paragraph({ spacing: { after: 120 }, children: [r(subtitle, { size: 26, bold: true, color: INK })] }),
    new Paragraph({ spacing: { after: 400 }, children: [r('Three-day course · HLayout 0.7 · built around the ALU8 example', { size: 22, color: MUTED })] }),
    ...img('alu8_3d.png', 12),
    ...extra,
  ];
}

const student = [];
student.push(...cover('Student handout', [sp(200), table([['Name', ''], ['Organisation', ''], ['Course dates', ''], ['Trainer', '']], [2600, CW - 2600])]));
student.push(H1('Contents'));
student.push(new TableOfContents('Contents', { hyperlink: true, headingStyleRange: '1-2' }));
student.push(T('If the page numbers are missing, right-click the contents in Word and choose **Update Field**.', { spacing: { before: 200 } }));

// 1 course map
student.push(H1('1  Course map'));
student.push(T('Over three days you learn the drawing basics, mask operations and printed resistors, then take one real design, the **ALU8**, from its KiCad netlist to print artwork, a bond list, an assembly drawing and a 3D model, and link its 2D cells to 3D STEP models. Each module ends with a lab. Each lab has a checkpoint file, so if you fall behind or break your design you can load the checkpoint and carry on.'));
student.push(H2('What you will be able to do'));
['**Draw** shapes, paths and cells, and place them as SRefs and ARefs.', '**Set up** a thick-film process: layers, rules, wire bonds and dielectric, saved as a technology file.', '**Derive** layers with mask operations, and create and delete the dielectric.', '**Size** printed resistors from value and power, place them and simulate the trim.', '**Build** vias, land patterns and bare-die parts with the wizards, and place them automatically from a KiCad netlist.', '**Bond** every die and package pin, and clear unused bonds before routing.', '**Route** two conductors with Freerouting and clean the result up.', '**Verify** with DRC, bond-angle checks, connectivity and thermal analysis.', '**Deliver** Gerbers, DXF, pick and place, a bond list, an assembly drawing and a 3D model linked to STEP files.'].forEach(t => student.push(bullet(t)));
student.push(H2('Timetable'));
student.push(table([['Time', 'Day 1: foundations', 'Day 2: netlist to routed design', 'Day 3: checks, outputs, 3D'],
  ['09:00', '1  Welcome · 2  Hybrids', '8  Circuit and netlist · Lab 6', '14  Verification · Lab 12'],
  ['10:30', '3  Tour of HLayout · Lab 1', '9  Wizards, dies, package · Lab 7 (09:45)', '15  Outputs · Lab 13 (10:15)'],
  ['11:15', '4  Shapes, paths, SRef, ARef · Lab 2', '10  Auto Place · Lab 8 (11:15)', '16  2D to 3D STEP · Lab 14'],
  ['13:15', '5  Technology setup · Lab 3', '11  Nets and wire bonds · Lab 9', '17  Documentation, 3D · Lab 15'],
  ['14:05', '6  Mask ops, dielectric, bond edits · Lab 4', '12  Routing · Lab 10 (14:45)', '18  Automation · Lab 16 (14:00)'],
  ['15:15', '7  Thick-film resistors · Lab 5', '13  Editing the route · Lab 11 (16:00)', 'Capstone (14:30)'],
  ['16:35', 'Day 1 review', 'Day 2 review (16:45)', 'Quiz and wrap-up (16:15)']], [900, 2913, 2913, 2912], { size: 18 }));
student.push(T('Lunch 12:30–13:15. Breaks: day 1 10:15 and 15:00; day 2 11:00 and 14:30; day 3 11:00 and during the capstone.', { spacing: { before: 100 } }));
student.push(H2('The workflow'));
student.push(T('Every hybrid passes through the same seven stages. Only stage 1 and the machines at the end sit outside HLayout.'));
student.push(table([['Stage', 'What happens', 'Tool', 'Course'], ['1  Schematic', 'Reference designators, die-pad numbers, power nets; export the netlist', 'KiCad', 'Module 8'], ['2  Technology and parts', 'Shapes and cells, layers, rules, dielectric, resistors, dies, package', 'HLayout', 'Modules 4–7, 9'], ['3  Placement', 'Auto Place from the netlist on a 50 µm grid', 'HLayout', 'Module 10'], ['4  Nets and bonds', 'Net names, wire bonds, I/O pads', 'HLayout', 'Modules 6, 11'], ['5  Routing', 'Two conductors, vias, bond fingers', 'Freerouting', 'Modules 12, 13'], ['6  Verification', 'DRC, connectivity, thermal', 'HLayout', 'Module 14'], ['7  Outputs', 'Gerber, DXF, pick and place, drawings, 3D linked to STEP', 'HLayout, FreeCAD', 'Modules 15–17']], [2300, 4038, 1600, 1700]));
student.push(H2('Course files'));
student.push(T('Everything is in `C:\\dev\\HLayout\\training\\checkpoints`, copied from `examples\\8bit_ALU__chip_wire`. Always copy a `.gds` together with its `.gds.hlaux` sidecar.'));
student.push(table([['File', 'Contains', 'Used in'], ['CP0_shapes_reference.gds', 'Reference drawing for the shapes lab', 'Lab 2'], ['my_parts.gds', 'Your own parts library: a copy of chip_wire.gds the wizards write into', 'Lab 7'], ['alu8_die.net', 'KiCad netlist, pins numbered by die pad', 'Labs 6, 8'], ['alu8.net', 'The same netlist numbered by LCC terminal', 'Lab 6'], ['alu8_netlist.csv', 'Every pin with both numbers', 'Lab 6'], ['io_pinout.csv', 'Package pin to net', 'Labs 9, 15'], ['alu8_tech.json', 'The ALU8 technology file', 'Labs 3, 5, capstone'], ['CP1_dies_on_substrate.gds', 'Six dies and I/O pads on a 30 × 30 mm substrate', 'Labs 7, 8, capstone'], ['CP2_bonded_ready_to_route.gds', 'Placed in the DIP40, bonded, fingers fanned out', 'Labs 7, 9, 10, 16'], ['CP3_routed.gds', 'All 46 nets routed, two DRC errors left in', 'Labs 4, 11–15'], ['8bit_alu.gds', 'The finished reference design', 'Lab 1']], [3300, 4338, 2000]));

// 2 design
student.push(H1('2  The ALU8 design'));
student.push(...img('stage4_routed.png', 16, 'The finished ALU8: six bare dies on a two-conductor substrate inside a 40-pin Kovar DIP'));
student.push(T('The ALU8 is an 8-bit arithmetic logic unit built as a chip-and-wire hybrid. Two **54AS181** 4-bit ALU slices do the arithmetic and logic: U1 takes bits 0–3 and U2 bits 4–7. The carry ripples from U1 Cn+4 to U2 Cn on net **C4**. Four 54LS gate dies make the status flags.'));
student.push(table([['Ref', 'Part', 'Function in the ALU8', 'Die'], ['U1', '54AS181', 'ALU slice, bits 0–3', '3.0 mm, 24 pads'], ['U2', '54AS181', 'ALU slice, bits 4–7', '3.0 mm, 24 pads'], ['U3', '54LS27', 'Triple 3-input NOR: zero detect on F0–F7', '1.5 mm, 14 pads'], ['U4', '54LS30', '8-input NAND: combines the NOR outputs into Z', '1.5 mm, 14 pads'], ['U5', '54LS86', 'Quad XOR: overflow and subtract logic', '1.5 mm, 14 pads'], ['U6', '54LS00', 'Quad NAND: carry and overflow flags', '1.5 mm, 14 pads']], [800, 1300, 5038, 2500]));
student.push(H2('Operations'));
student.push(table([['Operation', 'S3 S2 S1 S0', 'M', 'CN', 'SUB'], ['ADD', '1 0 0 1', '0', '1', '0'], ['SUB', '0 1 1 0', '0', '0', '1'], ['AND', '1 0 1 1', '1', '–', '–'], ['OR', '1 1 1 0', '1', '–', '–'], ['XOR', '0 1 1 0', '1', '–', '–'], ['NOT A', '0 0 0 0', '1', '–', '–']], [2200, 2400, 1600, 1600, 1838]));
student.push(T('Active-high data. Flags: **Z** = all of F7–F0 low · **N** = F7 · **C** = NOT Cn+4 (1 = carry, or no borrow) · **V** = (A7 ⊕ F7) · (B7 ⊕ SUB ⊕ F7).', { spacing: { before: 120 } }));
student.push(H2('Physical build'));
student.push(table([['Item', 'Value'], ['Substrate', 'Alumina 0.635 mm, 21.7 × 14.9 mm, two printed conductors with dielectric'], ['Package', 'KOVAR_DIP40: 40 pins on 2.54 mm pitch, rows 22.86 mm apart; 37 pins used'], ['Dies', 'On epoxy, on die-attach pads on the 2nd conductor'], ['Wire bonds', '94 die bonds, 30 µm gold, 1.65–1.89 mm long; plus one header bond per used pin'], ['Nets', '46, with VCC and GND as power nets']], [2200, CW - 2200]));
student.push(note('Representative die sizes', 'TI does not publish die drawings for these parts, so the ALU8 die sizes and pad positions are representative. On a real job, use the supplier\'s die drawing.'));

// labs
LABS.forEach(L => student.push(...labBlock(L)));
student.push(...capstoneBlock());

// quick ref
student.push(H1('Quick reference'));
student.push(H2('Menus you will use most'));
student.push(table([['Task', 'Menu'], ['Open, save, archive', 'File → Open GDS… / Save GDS… / Archive Design'], ['Technology', 'Setup → Layers… / Technology Properties… / Save Technology File…'], ['Shapes', 'Create → Rectangle / Polygon / Path / Circle / Text · Setup → Path Style… · Edit → Continue Path…'], ['Cells', 'Create → Create Cell from Selection… · Edit → Smash Selected to SRefs / Merge Selected…'], ['Mask operations', 'Tools → Mask Operations… / Mask Operation Batch…'], ['Dielectric', 'Tools → Generate Dielectric Fill… / Remove Dielectric Fill · Tools → Fix Vias…'], ['Resistors', 'Tools → Thick-Film Resistor Designer… → Design Rules… / Place All in Layout… / Trim Simulator…'], ['Wizards', 'Tools → Via Wizard… / Pad Wizard… / Chip Wizard… (attach a library first) · Setup → Library Cells…'], ['Placement', 'Tools → Auto Place… · View → Straight-Line Interconnects'], ['Bonds', 'Create → Wire Bond · Edit → Move / Delete Wire Bond · Tools → Remove Unused Bonds… / Bond Header Pins…'], ['Nets', 'Edit → Highlight Net · View → Unrouted Nets…'], ['Routing', 'Tools → Autoroute (Freerouting)… · Edit → Rip Up and Reroute · Tools → Fix Vias…'], ['Checks', 'Tools → DRC… / Extract Netlist… / Thermal Analysis…'], ['Outputs', 'File → Gerber Export Sets… / DXF Export Sets… / Export Pick and Place…'], ['Drawings and 3D', 'Tools → Top View… / Assembly Drawing… · File → Export to FreeCAD (3D Macro)…'], ['3D links', 'File → Component Library Manager… → Set STEP File… / Clear Override / Refresh'], ['Help', 'Help → User Manual · Command Reference (F1) · Report Bug…']], [2400, CW - 2400]));
student.push(H2('Command bar'));
student.push(T('Coordinates and sizes on the command bar are in microns (1 DBU = 1 µm).'));
student.push(table([['Command', 'Does'], ['FIT · ZOOM IN|OUT · ZOOM WINDOW x1,y1 x2,y2', 'View control'], ['RECT · POLY · PATH layer width cap … · CIRCLE · TEXT', 'Draw primitives'], ['SREF cell x,y [rot] · AREF cell x,y cols rows dx dy · SMASH', 'Place and break instances'], ['VIA 3 5 shape=round size=300 dielectric_layer=4 dielectric_window=500', 'Via Wizard from the command bar (µm)'], ['PAD name 5 20 1.0 1.4 0.1 cols=2 pitch_x=1.9', 'Pad Wizard from the command bar (mm)'], ['CELLSTAMPS', 'Created / modified date of every cell'], ['MOVE · COPY dx,dy · ROTATE deg · UNION', 'Edit selection'], ['MASKOP OVERSIZE 3 result=55 amount=100', 'Mask operation'], ['DIELECTRIC · DIELECTRIC CLEAR', 'Generate or delete the dielectric fill'], ['RESGEN x,y width length ink=1000 ref=R1', 'Place a sized resistor'], ['HILITE <net>|<RefDes.Pin> · HILITE OFF', 'Highlight a net'], ['NETNAMES IMPORT file.csv', 'Load net names from a pad,net CSV'], ['AUTOPLACE RUN|IMPROVE file.net [seed=] [effort=]', 'Auto Place'], ['AUTOPLACE WEIGHT <net> <w>', 'Weight one net'], ['SLI ON|OFF · UNROUTED [NEXT]', 'Straight-line interconnects, unrouted nets'], ['BOND x1,y1 x2,y2', 'Draw a wire bond'], ['UNUSEDBONDS [REMOVE]', 'List or remove unused bonds'], ['IOPADS BOND all=1', 'Bond the header pins'], ['FREEROUTE RUN [fresh=1] [fingerlayer=2]', 'Route with Freerouting'], ['FREEROUTE VIAOPT | SHORTEN | DEJOG', 'Tidy existing routing'], ['RIPUP x1,y1 x2,y2 · SHORTS · VIA FIX', 'Edit and check routing'], ['DRC RUN error=6', 'Run every DRC rule, markers on layer 6'], ['NETLIST RUN 3 5 bonds=1', 'Extract the netlist'], ['GERBER prefix · PNP EXPORT file.csv', 'Outputs'], ['ASSEMBLY size=A3 title="8-bit ALU"', 'Assembly drawing']], [4600, CW - 4600], { size: 18 }));
student.push(H2('Resistor sizing'));
student.push(table([['Step', 'Rule'], ['Value', 'R = Rsh × squares; squares = L / W'], ['Width', 'Smallest width whose after-trim rating (half the full rating) covers the power: Table 5.1'], ['Aim', 'value × (aim % + tolerance %); aim 75 %'], ['Ink', 'squares = aim / Rsh within 0.25–4; pick the one closest to 1 square'], ['Length', 'L = squares × W'], ['Layout', 'A 0.3 ink into pad · B 0.5 pad beyond · C 0.3 pad wider each side · D 0.3 glaze · E 1.0 keep-out (mm)'], ['Trim', 'Plunge for speed, L-cut to finish; the value only goes up']], [1500, CW - 1500], { size: 18 }));
student.push(H2('Keyboard'));
student.push(table([['Key', 'Action', 'Key', 'Action'], ['F', 'Fit view', 'Ctrl+Z / Ctrl+Y', 'Undo / redo'], ['G', 'Grid on/off', 'Ctrl+S', 'Save'], ['W', 'Zoom window', 'R', 'Rotate selection 90°'], ['P', 'Pan tool', 'Delete', 'Delete selection'], ['M', 'Measure', 'Arrow keys', 'Nudge one grid step'], ['Space', 'Drop a via while routing', 'Esc', 'Cancel the current tool'], ['Enter', 'Finish a path or route', 'Backspace', 'Remove last point'], ['F1', 'Command Reference', 'Mouse wheel', 'Zoom at cursor']], [1500, 3319, 1500, 3319]));

// glossary
student.push(H1('Glossary'));
student.push(table([['Term', 'Meaning'], ['ARef', 'Array reference: one instance placing a cell in columns and rows at a fixed pitch.'], ['Aspect window', 'The allowed range of squares for a printed resistor, 0.25 to 4 by default.'], ['Bond finger', 'Small conductor pad where a wire bond lands on the substrate; the router connects to it.'], ['Bond reach', 'How far from a die its bond fingers sit; Auto Place grows each die by it.'], ['Chip and wire', 'Building with bare dies glued down and wire-bonded, instead of packaged parts.'], ['Checkpoint', 'A saved design at a known stage, used to restart a lab.'], ['DBU', 'Database unit: 1 µm in HLayout.'], ['Departure angle', 'Angle a wire leaves a die pad, measured from straight out of the die edge.'], ['Die-attach pad', 'Conductor pad under a die, larger than the die, carrying its epoxy.'], ['Dielectric', 'Printed insulating layer between the two conductors, with a window at each via.'], ['Dielectric inputs / fill', 'Inputs (datatype 0) are the boundary, windows and keep-outs; the fill (datatype 1) is generated from them.'], ['DRC', 'Design rule check: widths, spacings, edge clearance, bond rules.'], ['End cap', 'How a path ends: flush, square (extended half the width) or round.'], ['Glaze', 'Glass overprint covering a resistor, larger than the ink by D.'], ['Header', 'The package pins; here the 40 pins of the Kovar DIP.'], ['.hlaux', 'Sidecar file next to a .gds holding the technology, net names and settings.'], ['I/O pad', 'Pad at the substrate edge that a header pin is bonded to.'], ['Isolated pad layer', 'A layer whose pads sit above the substrate and join a net only through bonds.'], ['Kerf', 'Width of the laser cut when trimming.'], ['Land pattern', 'The conductor pads a part is attached to; made with the Pad Wizard.'], ['Kovar', 'Iron-nickel-cobalt alloy that expands like glass and ceramic; used for hermetic packages.'], ['L-cut', 'Trim cut that plunges, then turns 90° and runs along the resistor for fine adjustment.'], ['Mask operation', 'A layer calculation: OVERSIZE, UNDERSIZE, AND, OR, NOT, XOR.'], ['Paste / epoxy pad', 'The smaller pad the Pad Wizard draws inside each conductor pad: solder paste, or conductive epoxy on a hybrid.'], ['Plunge cut', 'Straight trim cut into the resistor width; fast but increasingly sensitive.'], ['Sheet resistance', 'Resistance of one square of fired ink, in Ω/□.'], ['SLI', 'Straight-line interconnect: the drawn connection still to route.'], ['Smash', 'Break an ARef into separate SRefs.'], ['Specctra DSN / SES', 'The design and session files HLayout and Freerouting exchange.'], ['Square', 'A resistor length equal to its width; resistance = Rsh × number of squares.'], ['SRef', 'Structure reference: one placed instance of a cell, with rotation, magnification and mirror.'], ['STEP', '3D model file format; HLayout places the STEP model of a cell at the cell origin.'], ['Technology file', 'A .json holding a process: layers, rules and settings, without a design.'], ['Trim', 'Cutting resistor ink with a laser to raise its value to target.'], ['Via', 'A window in the dielectric joining the two conductors.'], ['Wizard', 'A Tools-menu form that builds a part (via, pad, die) and saves it into an attached library.'], ['z_ref', 'The vertical reference of a 3D part: the surface, or the substrate bottom for headers.']], [2400, CW - 2400]));

// appendix A nets
student.push(H1('Appendix A  ALU8 net list'));
student.push(T('Die pins are given as **RefDes.DiePad**, the numbering in `alu8_die.net`. I/O pads are given as **IO.n**, where n is the package pin.'));
student.push(table([['Net', 'I/O pad', 'Die pins'], ...NETS], [1100, 1500, CW - 2600], { size: 18 }));
// appendix B pinout
student.push(H1('Appendix B  Package pinout'));
student.push(T('KOVAR_DIP40, from `io_pinout.csv`. Pin 1 is bottom left; numbering runs counter-clockwise. Pins 37–39 are not used.'));
const pins = '1 A1,2 V,3 GND,4 C,5 B1,6 B7,7 B6,8 B4,9 A4,10 S3,11 S2,12 F2,13 F3,14 B0,15 B3,16 A3,17 B2,18 VCC,19 B5,20 A5,21 F6,22 F7,23 F7,24 F0,25 F1,26 A0,27 S1,28 S0,29 CN,30 Z,31 M,32 F4,33 F5,34 A2,35 SUB,36 A7,37 –,38 –,39 –,40 A6'.split(',').map(x => x.split(' '));
const prow = []; for (let i = 0; i < 10; i++) prow.push([pins[i][0], pins[i][1], pins[i + 10][0], pins[i + 10][1], pins[i + 20][0], pins[i + 20][1], pins[i + 30][0], pins[i + 30][1]]);
student.push(table([['Pin', 'Net', 'Pin', 'Net', 'Pin', 'Net', 'Pin', 'Net'], ...prow], Array(8).fill(Math.floor(CW / 8))));
student.push(T('F7 appears on pins 22 and 23: one is the F7 output and the other the **N** (negative) flag, which is the same signal.', { spacing: { before: 120 } }));

// quiz
student.push(H1('Assessment quiz'));
student.push(T('Circle one answer for each question. 16 or more correct to pass. Time: 20 minutes.'));
QUIZ.forEach(q => { student.push(new Paragraph({ numbering: { reference: 'quiz', level: 0 }, spacing: { before: 160, after: 60 }, keepNext: true, children: rich(q[0]) })); q[1].forEach((o, i) => student.push(new Paragraph({ numbering: { reference: 'quiz', level: 1 }, spacing: { after: 20 }, keepNext: i < 3, children: [r(o)] }))); });
// feedback
student.push(H1('Course feedback'));
student.push(T('Please rate each item from 1 (poor) to 5 (excellent).'));
student.push(table([['Item', '1', '2', '3', '4', '5'], ['The course met its objectives', '', '', '', '', ''], ['The pace was right', '', '', '', '', ''], ['The labs were clear', '', '', '', '', ''], ['The ALU8 example was useful', '', '', '', '', ''], ['The trainer explained things well', '', '', '', '', ''], ['I can now use HLayout on my own designs', '', '', '', '', '']], [5138, 900, 900, 900, 900, 900]));
['What was most useful?', 'What should change?', 'What would you like to learn next?'].forEach(q => { student.push(T(`**${q}**`, { spacing: { before: 240 } })); student.push(...lines(3)); });

const studentDoc = new Document({ creator: 'HLayout', title: 'HLayout in Practice: Student Handout', styles, numbering, features: { updateFields: true },
  sections: [{ properties: pageProps, ...hdrftr('  ·  Student handout'), children: student }] });

// ---------- TRAINER GUIDE --------------------------------------------------------
const tg = [];
tg.push(...cover('Trainer guide', [sp(100), T('Setup checklist, timing, checkpoint map, lab and capstone answers, quiz key and known issues. Use with the slide deck `HLayout_Training_ALU8.pptx`, whose speaker notes carry the talk for every slide.')]));
tg.push(H1('Before the course'));
tg.push(box('Room and machines', ['One PC per student, Windows 10/11 64-bit (or Linux/macOS with the matching installer)', 'HLayout 0.7 installed; check Help → About shows 0.7.0', 'Freerouting installed (Java build, freerouting.exe). A .jar needs Java 25 or later', 'FreeCAD installed for Labs 14 and 15 (or demo them on the projector)', 'STEP models built: python build_STEP_parts\\build_all.py; check examples\\step_models\\chip_wire and kovar_dip exist', 'C:\\dev\\HLayout\\training\\checkpoints copied to every machine, including CP0_shapes_reference.gds and a fresh my_parts.gds for each student (the wizards write into it)', 'Projector, flipchart, printed student handouts, one per student', 'A test run of Lab 10 on the trainer machine (route time on this hardware)'], ALU, INK));
tg.push(sp());
tg.push(box('Day before', ['Open each checkpoint and confirm it loads with its sidecar', 'Run DRC on CP3: two violations, both 1st con metal_2_metal', 'Open the Resistor Designer and Trim Simulator once: they need numpy and scipy (included in the installers)', 'Check the KOVAR_DIP40 STEP path in the Component Library Manager resolves on the training PCs', 'Print the quiz answer key (this guide) separately', 'Check Wi-Fi is not needed: the labs run offline'], ALU, INK));
tg.push(H2('Timing'));
tg.push(T('Three days, 18 modules, 16 labs and a capstone. The deck has 101 slides: about 40 % talk and demo, 60 % hands-on. Day 1 covers the foundations (shapes and cells, technology, mask operations and dielectric, printed resistors); day 2 takes the ALU8 from netlist to a routed design; day 3 verifies it, makes the outputs, links the 3D models and ends with the capstone and quiz.'));
tg.push(table([['Day', 'Runs long most often', 'Where to recover time'], ['1', 'Lab 2 (shapes) for students new to CAD; Lab 5 (resistors)', 'Shorten module 2 (hybrids) or the day 1 review'], ['2', 'Lab 8 (Auto Place) and Lab 10 (routing)', 'Demo Lab 11 instead of running it in full'], ['3', 'Lab 14 (3D linking) if FreeCAD is slow', 'Lab 16 is optional; the capstone can stop at routing']], [700, 4469, 4469]));
tg.push(H1('Checkpoint map'));
tg.push(table([['Checkpoint', 'Copied from', 'State', 'Facts checked'], ['my_parts.gds', 'hlayout\\examples\\chip_wire.gds', 'Example parts library: packages, chip capacitors, dies, chip_pin_1 and chip_pin die-pad cells, VIA_Layer3_to_Layer5', 'Students add VIA_R300, LAND_0805, MY_54LS00'], ['CP0_shapes_reference.gds', 'Built for the course', 'Cell PAD (0.6 mm pad with triangle); TOP with a rectangle, polygon, circle, three paths with flush, square and round caps, three PAD SRefs and a 5 × 2 ARef', 'Compare only'], ['CP1_dies_on_substrate.gds', 'alu8_dies.gds', 'Six dies and I/O pads on a 30 × 30 mm substrate; not placed by Auto Place', '0 of 46 nets routed; DRC clean'], ['CP2_bonded_ready_to_route.gds', 'pre_route_hold.gds', 'Reference placement in the DIP40; dies and header bonded; fingers fanned out on the 2nd conductor', '6 of 46 nets complete; DRC clean; no shorts'], ['CP3_routed.gds', '8bit_alu.gds', 'Fully routed with dielectric', '46 of 46 complete; no shorts; 2 DRC violations; dielectric 94 inputs, 23 fill pieces'], ['8bit_alu.gds', '8bit_alu.gds', 'Reference design, identical to CP3', 'As CP3'], ['alu8_tech.json', '8bit_alu.json', 'ALU8 technology file', '5 DRC rules']], [2600, 1700, 3238, 2100], { size: 18 }));
tg.push(T('CP1 uses the 30 × 30 mm working substrate from `build_alu8_dies.py`; CP2 onwards uses the final 21.7 × 14.9 mm substrate in the KOVAR_DIP40. Explain the jump on day 2: everyone restarts from the same reference placement.', { spacing: { before: 120 } }));
tg.push(H1('Lab answer key'));
const ans = [
  ['Lab 1', 'C4 joins U1 pad 16 (Cn+4) to U2 pad 7 (Cn). Bonds measure 1.65–1.89 mm. Console prints 46. To see the 2nd conductor alone hide layers 3 and 4. The status bar shows the distance and dx, dy while measuring.'],
  ['Lab 2', 'A square cap on a 0.4 mm path reaches 0.2 mm past the end point; round also reaches 0.2 mm, flush 0. After Smash the pads are separate SRefs of PAD, so editing PAD still changes all of them, but moving one no longer moves the rest. The triangle shows rotation and mirroring. Early finishers: Tools → Via Wizard and place the via as an SRef.'],
  ['Lab 3', 'Five rules as Table 3.3. Common error: wire width entered as 30 instead of 0.03 mm. The dielectric margin (0.2 mm) is the margin round the boundary; the via window margin (0.1 mm) is added to each side of the via pad.'],
  ['Lab 4', 'Layer 54 is the bare substrate not covered by 1st conductor. After Remove Dielectric Fill only the 94 input shapes on layer 4 remain (boundary, via windows, keep-outs); Generate gives 23 fill pieces again. A bond moved outside 1.0–2.5 mm shows in DRC as BOND.L.MIN or BOND.L.MAX; a steep move as BOND.ANGLE. Make sure students work on the copy.'],
  ['Lab 5', 'R1: 1.4 mm (62.5 mW after trim), aim 800 Ω, 1 kΩ/□, 0.80 sq, 1.12 mm; ink 1.72 × 1.40 mm, pads 0.80 × 2.00 mm, overall 2.72 × 2.00 mm. R2: 1.8 mm, aim 7.6 kΩ, 10 kΩ/□, 0.76 sq, 1.37 mm. R3: 2.8 mm (2.4 mm is only 187.5 mW after trim), aim 37.6 Ω, 30 Ω/□, 1.25 sq, 3.51 mm. R4: 1.0 mm, aim 187 kΩ, 300 kΩ/□, 0.62 sq, 0.62 mm. Trim R1: printed 793 Ω; plunge 25 % → 913 Ω, sensitivity 0.42 Ω/µm; L-cut 400 µm → 995 Ω, 0.09 Ω/µm. The L-cut finishes because its sensitivity is low, so the laser can stop accurately.'],
  ['Lab 6', 'GND has 11 pins: J1, J2 and nine die pins (U1.12, U2.12, U3.7, U3.11, U4.7, U5.7, U6.7, U6.12, U6.13) across all six dies. C4: U1 pad 16, U2 pad 7. U1 and U2 share S0–S3, M and C4. F7 has 6 pins (J2.8, J2.11, U2.13, U3.10, U5.5, U5.10); two header pins because N, the negative flag, is the same signal as F7. U4 pads 4, 5, 6, 11, 12 (inputs D–H) are tied to VCC with pad 14 (VCC): the 8-input NAND uses only three inputs, so the unused ones are held high.'],
  ['Lab 7', 'VIA_R300: round 300 µm pads on 3 and 5, 500 µm window on 4 (pad + 2 × the 0.1 mm via window margin, the Dielectric tab). The default name VIA_Layer3_to_Layer5 already exists in chip_wire.gds, so the wizard would ask to overwrite it. LAND_0805: two 1.0 × 1.4 mm pads on 5 at x = ±0.95 mm, epoxy 0.8 × 1.2 mm on 20, pins 1 (left) and 2 numbered automatically. MY_54LS00: attach pad 1.9 mm on 5, epoxy 1.4 mm on 20, outline 1.53 mm on 61, 14 pins labelled 1–14; the script-built DIE_54LS00 has its top and bottom pads at 0.225 and 0.45 mm, off the 0.1 mm grid, so positions differ slightly. Pins follow the LCC terminal order so bonds fan out without crossing. CP2 has the smaller 21.7 × 14.9 mm substrate inside the KOVAR_DIP40. Common faults: no library attached (step 1 skipped); everyone sharing one my_parts.gds on a network drive.'],
  ['Lab 8', 'Results vary with hand moves. U1 and U2 should end side by side; raising C4 to weight 3 pulls them closer.'],
  ['Lab 9', 'On a 54AS181 the unused die pads are 14 (A=B), 15 (P) and 17 (G). In CP2 these bonds may already be removed; then UNUSEDBONDS lists nothing. Removing them frees routing channels round the die. After removal the dies get their own part copies, e.g. DIE_54AS181_U1.'],
  ['Lab 10', '46 of 46 with the Table 10.1 settings. Fewer usually means fingers on the 1st conductor or Remove ALL not ticked. Via windows are 500 µm (300 µm pad + 2 × 0.1 mm).'],
  ['Lab 11', 'C4 appears in Unrouted Nets after the rip-up. Routing it back gives 46 complete and total 0.'],
  ['Lab 12', 'CP3 has 2 violations of 1st con metal_2_metal (MIN_SPACE_BETWEEN layer 3, 200 µm) at about x 55.1 mm, y −7.8 mm (DBU 55115, −7845), just left of U3: a short diagonal 1st-conductor segment. Thermal, fixed base 25 °C: U1 and U2 26.0 °C, U5 25.2 °C, others 25.1 °C. Cooled base, 200 W/m²K: U1 34.0, U2 33.9, U5 28.7, U3 28.8, U4 28.7, U6 28.0 °C (±1 °C with grid size). 900 mW total.'],
  ['Lab 13', 'Pick and place lists U1–U6 as kind die; U1 at (34.02, 8.80) mm rotated 270°, U2 at (19.52, 9.55) mm rotated 180° from the substrate corner. The bond list has 94 rows: U1 and U2 21 each, U3 and U5 14, U6 13, U4 11; lengths 1.65–1.89 mm; steepest angle 29.3°.'],
  ['Lab 14', 'KOVAR_DIP40 is linked manually (mode model) to C:/dev/HLayout/examples/step_models/kovar_dip/KOVAR_DIP40_0.9in.step, z_ref substrate_bottom so the substrate sits in the package well. C1 matches CAP_0805.step automatically by its EIA code. With a 90° correction the capacitor body turns across its pads: the correction is applied on top of the instance rotation. After Set STEP File… to a copy next to the design the path is stored relative, so the design works on another PC. No FreeCAD: use File → 3D View in SolidCraft or the projector.'],
  ['Lab 15', 'The example title block says "4 bit ALU"; students change it to 8-bit ALU. Pin table from Appendix B.'],
  ['Lab 16', 'The script needs the Freerouting path saved in Lab 10. 46 of 46; two DRC violations remain unless fixed (they come from the router, so a new route may differ).'],
  ['Capstone', 'R1, 25 mW: 1.0 mm (31.3 mW after trim), aim 800 Ω, 1 kΩ/□, 0.80 squares, 0.80 mm long. The new net PU makes 47 nets, so routing should report 47 of 47. The keep-out round R1 is drawn on the dielectric layer as an input; the regenerated fill opens over it. Check each hand-in against the list. The capstone is not marked; it is practice for the quiz and real work. Strong finishers can add C1 from Lab 14 to the netlist.'],
];
tg.push(table([['Lab', 'Answers and notes'], ...ans], [1200, CW - 1200], { size: 19 }));
tg.push(H1('Quiz answer key'));
tg.push(table([['Q', 'Answer', 'Q', 'Answer'], ...Array.from({ length: 10 }, (_, i) => { const a = QUIZ[i], b = QUIZ[i + 10]; return [String(i + 1), `${'abcd'[a[2]]})  ${a[1][a[2]]}`, b ? String(i + 11) : '', b ? `${'abcd'[b[2]]})  ${b[1][b[2]]}` : '']; })], [600, 4219, 600, 4219], { size: 18 }));
tg.push(T('Pass mark: 16 of 20.', { spacing: { before: 120 } }));
tg.push(H1('Known issues and tips'));
[ '**No bond diagram for Freerouting designs.** HYBRID BONDS and BONDTABLE need a design imported from the chip-and-wire router; the ALU8 is routed with Freerouting, so Lab 13 writes the bond list from Python, and the assembly drawing has no bond table.',
  '**Example title block.** The ALU8 example\'s assembly drawing is titled "4 bit ALU". Lab 15 fixes it; consider fixing the example itself.',
  '**Resistor power table.** The width/power table in the User Manual (chapter 11) does not match the values the Resistor Designer uses. The course uses the Designer\'s values (Table 5.1), which are what students will see on screen. Fix the manual.',
  '**Absolute STEP path.** The ALU8 example links the package to C:/dev/HLayout/… with an absolute path. On a PC with HLayout elsewhere the package draws as a box. Lab 14 shows the fix: a relative path.',
  '**Via cell window.** The stored VIA_Layer3_to_Layer5 cell carries a 400 µm window from an older setting; the dielectric generator cuts 500 µm windows (pad + 2 × via window margin). If a student spots the difference, that is the reason.',
  '**Representative values.** Die sizes and pad positions are representative (TI publishes no die drawings), and the thermal powers are exercise values. Say so when students ask.',
  '**Freerouting builds.** Use the Java build. The native CLI build crashes on multi-pass runs, so HLayout limits it to one pass and fewer nets complete.',
  '**Netlist extraction** needs layers 58 and 49 ticked as Isolated; without them the die pads merge with the copper underneath and every net joins.',
  '**Sidecars.** A .gds copied without its .hlaux loses net names and technology; most "nothing works" cases in labs come from this.',
  '**Units.** Dialogs use mm, the command bar and technology file µm.'].forEach(t => tg.push(bullet(t)));
const trainerDoc = new Document({ creator: 'HLayout', title: 'HLayout in Practice: Trainer Guide', styles, numbering, features: { updateFields: true },
  sections: [{ properties: pageProps, ...hdrftr('  ·  Trainer guide'), children: tg }] });

Promise.all([Packer.toBuffer(studentDoc), Packer.toBuffer(trainerDoc)]).then(([a, b]) => {
  fs.writeFileSync('HLayout_Training_ALU8_Student_Handout.docx', a);
  fs.writeFileSync('HLayout_Training_ALU8_Trainer_Guide.docx', b);
  console.log('written');
});
