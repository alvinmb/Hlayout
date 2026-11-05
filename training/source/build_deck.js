// HLayout - thick-film hybrid circuit layout
// Copyright (C) 2026 Alvin Brown
// SPDX-License-Identifier: GPL-3.0-or-later

// HLayout two-day training course deck -- ALU8 chip-and-wire hybrid.
const pptxgen = require('pptxgenjs');
const path = require('path');
const DIMS = require('./img/dims.json');
const IMG = f => path.join(__dirname, 'img', f);

const pres = new pptxgen();
pres.layout = 'LAYOUT_16x9';
pres.title = 'HLayout in Practice: Designing a Chip-and-Wire Hybrid';
pres.author = 'HLayout';
pres.theme = { headFontFace: 'Cambria', bodyFontFace: 'Calibri' };

const INK = '15233B', GOLD = 'B8860B', GOLDL = 'E2B33C', DIEL = '2C7A67', ALU = 'F2F4F5', WHITE = 'FFFFFF',
  MUTED = '56657A', LINE = 'D5DBE3', PGOLD = 'F6EDCF', PGRN = 'DDEFE8', RED = 'C0392B', DARK2 = '1F3150', SOFT = 'C9D3E0';
const sh = () => ({ type: 'outer', color: '000000', blur: 6, offset: 2, angle: 90, opacity: 0.16 });
const STAGES = ['Schematic', 'Tech & parts', 'Placement', 'Nets & bonds', 'Routing', 'Verify', 'Outputs'];
let n = 0;

function T(s, text, o) { s.addText(text, Object.assign({ margin: 0, isTextBox: true, fontFace: 'Calibri', color: INK, valign: 'top' }, o)); }
function footer(s, mod) {
  n++;
  s.addImage({ path: IMG('hlayout_splash.png'), x: 0.45, y: 5.22, w: 0.26, h: 0.26 });
  T(s, 'HLayout in Practice' + (mod ? '  ·  ' + mod : ''), { x: 0.78, y: 5.25, w: 6, h: 0.22, fontSize: 9, color: MUTED });
  T(s, String(n), { x: 9.0, y: 5.25, w: 0.55, h: 0.22, fontSize: 9, color: MUTED, align: 'right' });
}
function head(s, title, sub) {
  s.background = { color: WHITE };
  T(s, title, { x: 0.45, y: 0.28, w: 9.1, h: 0.6, fontFace: 'Cambria', fontSize: 26, bold: true });
  if (sub) T(s, sub, { x: 0.45, y: 0.88, w: 9.1, h: 0.35, fontSize: 13, color: MUTED });
}
function img(s, f, x, y, maxW, maxH, frame = true) {
  const [pw, ph] = DIMS[f]; const r = pw / ph; let w = maxW, h = maxW / r; if (h > maxH) { h = maxH; w = maxH * r; }
  const ox = x + (maxW - w) / 2, oy = y + (maxH - h) / 2;
  if (frame) s.addShape(pres.shapes.RECTANGLE, { x: ox - 0.05, y: oy - 0.05, w: w + 0.1, h: h + 0.1, fill: { color: WHITE }, line: { color: LINE, width: 0.75 }, shadow: sh() });
  s.addImage({ path: IMG(f), x: ox, y: oy, w, h });
  return { ox, oy, w, h };
}
function cap(s, t, r) { T(s, t, { x: r.ox, y: r.oy + r.h + 0.08, w: r.w, h: 0.28, fontSize: 10, italic: true, color: MUTED }); }
function bullets(s, items, x, y, w, h, fs = 14) {
  const runs = [];
  items.forEach((it, i) => {
    const last = i === items.length - 1;
    if (Array.isArray(it)) { runs.push({ text: it[0], options: { bold: true, bullet: true, color: INK } }); runs.push({ text: ' ' + it[1], options: { color: '33404F', breakLine: !last } }); }
    else runs.push({ text: it, options: { bullet: true, color: '33404F', breakLine: !last } });
  });
  T(s, runs, { x, y, w, h, fontSize: fs, paraSpaceAfter: 6 });
}
function badge(s, label, x, y, fill, col, size = 0.42) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: size, h: size, rectRadius: 0.06, fill: { color: fill }, line: { color: fill } });
  T(s, label, { x, y, w: size, h: size, fontSize: 12, bold: true, color: col || WHITE, align: 'center', valign: 'middle' });
}
function stageBar(s, cur, x = 0.45, y = 4.55, w = 9.1, dark = false) {
  const gap = 0.06, bw = (w - gap * 6) / 7;
  STAGES.forEach((st, i) => {
    const on = cur.includes(i);
    s.addShape(pres.shapes.RECTANGLE, { x: x + i * (bw + gap), y, w: bw, h: 0.42, fill: { color: on ? GOLD : (dark ? DARK2 : ALU) }, line: { color: on ? GOLD : (dark ? DARK2 : ALU) } });
    T(s, `${i + 1}  ${st}`, { x: x + i * (bw + gap) + 0.06, y, w: bw - 0.08, h: 0.42, fontSize: 9.5, bold: on, color: on ? WHITE : (dark ? SOFT : MUTED), valign: 'middle' });
  });
}
function code(s, text, x, y, w, h, fs = 11) {
  s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: INK }, line: { color: INK } });
  T(s, text, { x: x + 0.18, y: y + 0.14, w: w - 0.3, h: h - 0.24, fontFace: 'Courier New', fontSize: fs, color: 'E6EDF5' });
}
function table(s, rows, x, y, w, colW, fs = 11, rowH = 0.3) {
  const hdr = rows[0].map(c => ({ text: c, options: { bold: true, color: WHITE, fill: { color: INK }, fontSize: fs } }));
  const body = rows.slice(1).map((r, i) => r.map((c, j) => ({ text: c, options: { fontSize: fs, color: j === 0 ? INK : '33404F', bold: j === 0, fill: { color: i % 2 ? WHITE : ALU } } })));
  s.addTable([hdr, ...body], { x, y, w, colW, rowH, border: { type: 'solid', pt: 0.5, color: LINE }, margin: [0.03, 0.08, 0.03, 0.08], valign: 'middle', fontFace: 'Calibri' });
}

// ---- slide types -----------------------------------------------------------
function divider(day, title, sub, notes) {
  const s = pres.addSlide(); s.background = { color: INK };
  T(s, day, { x: 0.6, y: 1.3, w: 8, h: 0.4, fontSize: 14, bold: true, color: GOLDL, charSpacing: 2 });
  T(s, title, { x: 0.6, y: 1.75, w: 8.8, h: 1.0, fontFace: 'Cambria', fontSize: 38, bold: true, color: WHITE });
  T(s, sub, { x: 0.6, y: 2.85, w: 8.4, h: 1.0, fontSize: 15, color: SOFT });
  s.addImage({ path: IMG('hlayout_splash.png'), x: 8.55, y: 0.35, w: 1.0, h: 1.0 });
  n++; s.addNotes(notes); return s;
}
function opener(num, title, time, objectives, stages, picture, notes) {
  const s = pres.addSlide(); s.background = { color: INK };
  T(s, `MODULE ${num}  ·  ${time}`, { x: 0.45, y: 0.35, w: 6, h: 0.3, fontSize: 11, bold: true, color: GOLDL, charSpacing: 1.5 });
  T(s, title, { x: 0.45, y: 0.7, w: picture ? 5.0 : 9, h: 0.95, fontFace: 'Cambria', fontSize: 30, bold: true, color: WHITE });
  T(s, 'By the end of this module you can:', { x: 0.45, y: 1.75, w: 5, h: 0.3, fontSize: 12, color: SOFT });
  const runs = objectives.map((o, i) => ({ text: o, options: { bullet: true, color: WHITE, breakLine: i < objectives.length - 1 } }));
  T(s, runs, { x: 0.45, y: 2.08, w: picture ? 4.9 : 9, h: 2.3, fontSize: 13.5, paraSpaceAfter: 5 });
  if (picture) img(s, picture, 5.75, 0.45, 3.8, 3.8);
  stageBar(s, stages, 0.45, 4.6, 9.1, true);
  s.addImage({ path: IMG('hlayout_splash.png'), x: 0.45, y: 5.18, w: 0.26, h: 0.26 });
  n++; T(s, String(n), { x: 9.0, y: 5.22, w: 0.55, h: 0.22, fontSize: 9, color: SOFT, align: 'right' });
  s.addNotes(notes); return s;
}
function lab(num, title, time, goal, start, steps, done, mod, notes) {
  const s = pres.addSlide(); s.background = { color: WHITE };
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 10, h: 1.15, fill: { color: PGOLD }, line: { color: PGOLD } });
  T(s, `LAB ${num}  ·  ${time}`, { x: 0.45, y: 0.2, w: 6, h: 0.3, fontSize: 11, bold: true, color: GOLD, charSpacing: 1.5 });
  T(s, title, { x: 0.45, y: 0.48, w: 9.1, h: 0.55, fontFace: 'Cambria', fontSize: 24, bold: true });
  T(s, [{ text: 'Goal  ', options: { bold: true, color: GOLD } }, { text: goal, options: { color: INK } }], { x: 0.45, y: 1.3, w: 9.1, h: 0.45, fontSize: 13 });
  T(s, [{ text: 'Start from  ', options: { bold: true, color: GOLD } }, { text: start, options: { color: INK, fontFace: 'Courier New' } }], { x: 0.45, y: 1.75, w: 9.1, h: 0.3, fontSize: 12 });
  const runs = steps.map((st, i) => ({ text: st, options: { bullet: { type: 'number' }, color: '33404F', breakLine: i < steps.length - 1 } }));
  T(s, runs, { x: 0.45, y: 2.18, w: 5.9, h: 2.9, fontSize: 12.5, paraSpaceAfter: 4 });
  s.addShape(pres.shapes.RECTANGLE, { x: 6.6, y: 2.18, w: 2.95, h: 2.75, fill: { color: PGRN }, line: { color: PGRN } });
  T(s, 'Done when', { x: 6.78, y: 2.3, w: 2.6, h: 0.3, fontSize: 12, bold: true, color: DIEL });
  const d = done.map((t, i) => ({ text: t, options: { bullet: true, color: INK, breakLine: i < done.length - 1 } }));
  T(s, d, { x: 6.78, y: 2.65, w: 2.65, h: 2.2, fontSize: 11.5, paraSpaceAfter: 4 });
  footer(s, mod); s.addNotes(notes); return s;
}
function content(title, sub, mod, notes) { const s = pres.addSlide(); head(s, title, sub); footer(s, mod); s.addNotes(notes); return s; }

// =============================================================================

// COURSE INTRO

{
  const s = pres.addSlide(); s.background = { color: INK }; n++;
  s.addImage({ path: IMG('hlayout_splash.png'), x: 0.6, y: 0.45, w: 1.0, h: 1.0 });
  T(s, 'HLayout in Practice', { x: 0.6, y: 1.65, w: 5, h: 0.75, fontFace: 'Cambria', fontSize: 36, bold: true, color: WHITE });
  T(s, 'Designing a chip-and-wire hybrid, from netlist to 3D', { x: 0.6, y: 2.4, w: 4.7, h: 0.75, fontSize: 17, color: GOLDL });
  T(s, 'Three-day hands-on course built around the ALU8: an 8-bit ALU of six bare dies on a two-conductor thick-film substrate in a 40-pin Kovar package.', { x: 0.6, y: 3.25, w: 4.5, h: 1.0, fontSize: 13, color: SOFT });
  T(s, 'HLayout 0.7  ·  Trainer deck with speaker notes', { x: 0.6, y: 4.85, w: 5, h: 0.3, fontSize: 11, color: '8FA0B5' });
  img(s, 'alu8_3d.png', 5.4, 0.45, 4.2, 4.7);
  s.addNotes('WELCOME (09:00, about 10 minutes). Introduce yourself and the course. Tell students that over two days each of them will take one real design, the ALU8 chip-and-wire hybrid, from its netlist to Gerbers, a bond diagram, an assembly drawing and the 3D model on the right. Ask each person for their name, their role, and whether they have designed thick-film or PCB layouts before. Note anyone new to chip and wire: modules 2 and 8 will matter most to them. Housekeeping: breaks at 10:15 and 15:00, lunch 12:30, fire exits, Wi-Fi. Check every machine has HLayout 0.7 installed and the course folder copied (slide 6).');
}
{
  const s = content('What you will be able to do', 'By the end of day 3, on your own machine', 'Introduction',
    'Read the objectives aloud and link each to a day. Day 1 covers the foundations: shapes and cells, technology, mask operations and dielectric, and printed resistors. Day 2 takes the ALU8 from netlist to a routed design. Day 3 checks it, makes every output, links it to 3D models and ends with a capstone. Stress that this is a hands-on course: about 60% of the time is labs. Each lab has a checkpoint file, so nobody gets left behind if a step goes wrong; they load the checkpoint and carry on. Ask: what does each of you most want to leave with? Write the answers on the flipchart and come back to them at the wrap-up.');
  const items = [['Set up', 'a thick-film process: layers, rules, wire bonds and dielectric, saved as a technology file'],
  ['Build', 'shapes and cells, vias, pads and dies with the wizards, and printed resistors; place them from a KiCad netlist'],
  ['Bond', 'every die and package pin, and clear unused bonds before routing'],
  ['Route', 'two conductors with Freerouting and clean the result up'],
  ['Verify', 'with DRC, bond-angle checks, connectivity and thermal analysis'],
  ['Deliver', 'Gerbers, DXF, pick and place, a bond list, an assembly drawing and a 3D model linked to STEP files']];
  items.forEach((it, i) => {
    const col = i % 2, row = Math.floor(i / 2), x = 0.45 + col * 4.6, y = 1.4 + row * 1.15;
    badge(s, String(i + 1), x, y + 0.05, i < 3 ? DIEL : GOLD);
    T(s, it[0], { x: x + 0.6, y, w: 3.8, h: 0.3, fontSize: 15, bold: true });
    T(s, it[1], { x: x + 0.6, y: y + 0.32, w: 3.8, h: 0.7, fontSize: 12, color: MUTED });
  });
}
{
  const s = content('The design: ALU8', 'An 8-bit arithmetic logic unit as a chip-and-wire hybrid', 'Introduction',
    'Show the finished design so students know where they are heading. Six bare dies: two 54AS181 4-bit ALU slices (U1 low nibble, U2 high nibble) and four 54LS gates that make the status flags. 46 nets. The dies sit on die-attach pads on the second conductor and are wire-bonded to bond fingers. The substrate sits inside a 40-pin Kovar DIP package; its 37 used pins are wire-bonded to I/O pads at the substrate edge. Point out the two conductors: orange is the 1st conductor, purple the 2nd, with dielectric between them. Note the die sizes are representative: TI does not publish die drawings for these parts.');
  const r = img(s, 'stage4_routed.png', 0.45, 1.35, 5.6, 3.0); cap(s, 'The finished, routed ALU8 (examples\\8bit_ALU__chip_wire\\8bit_alu.gds)', r);
  table(s, [['Item', 'ALU8'], ['Dies', 'U1, U2: 54AS181 (3.0 mm, 24 pads)\nU3–U6: 54LS27/30/86/00 (1.5 mm, 14 pads)'], ['Nets', '46, including VCC and GND'], ['Substrate', 'Alumina, 21.7 × 14.9 mm, two conductors'], ['Package', 'Kovar DIP40, 37 pins used'], ['Bonds', '94 die-pad bonds plus header bonds']], 6.35, 1.35, 3.2, [0.9, 2.3], 10, 0.42);
}
{
  const s = content('Three-day agenda', null, 'Introduction',
    'Walk through the timetable. Day 1 is foundations: the tools and concepts every layout uses, including the new modules on shapes and cells, mask operations and dielectric, and printed resistors. Day 2 builds the ALU8 from its netlist to a routed design. Day 3 verifies it, makes the outputs, links the 2D cells to 3D STEP models, and ends with a capstone in which students repeat the flow alone with an engineering change. Labs 8 (Auto Place) and 10 (routing) run long most often; protect that time.');
  table(s, [['Time', 'Day 1: foundations', 'Day 2: netlist to routed design', 'Day 3: checks, outputs, 3D'],
  ['09:00', '1  Welcome · 2  Hybrids', '8  Circuit and netlist · Lab 6', '14  Verification · Lab 12'],
  ['10:30', '3  Tour of HLayout · Lab 1', '9  Wizards, dies, package · Lab 7 (09:45)', '15  Outputs · Lab 13 (10:15)'],
  ['11:15', '4  Shapes, paths, SRef, ARef · Lab 2', '10  Auto Place · Lab 8 (11:15)', '16  2D to 3D STEP · Lab 14'],
  ['13:15', '5  Technology setup · Lab 3', '11  Nets and wire bonds · Lab 9', '17  Documentation, 3D · Lab 15'],
  ['14:05', '6  Mask ops, dielectric, bond edits · Lab 4', '12  Routing · Lab 10 (14:45)', '18  Automation · Lab 16 (14:00)'],
  ['15:15', '7  Thick-film resistors · Lab 5', '13  Editing the route · Lab 11 (16:00)', 'Capstone (14:30)'],
  ['16:35', 'Day 1 review (16:40)', 'Day 2 review (16:45)', 'Quiz and wrap-up (16:15)']], 0.45, 1.25, 9.1, [0.75, 2.85, 2.75, 2.75], 10.5, 0.42);
  T(s, 'Lunch 12:30–13:15. Breaks: day 1 10:15 and 15:00 · day 2 11:00 and 14:30 · day 3 11:00 and during the capstone.', { x: 0.45, y: 4.85, w: 9.1, h: 0.3, fontSize: 10, color: MUTED });
}

{
  const s = content('One workflow, seven stages', 'The same stages every hybrid passes through; the bar at the bottom of each module shows where we are', 'Introduction',
    'This is the map for the whole course. Only stage 1 (schematic capture in KiCad) and the machines at the end sit outside HLayout. Freerouting is called from inside HLayout and its result comes straight back as one undo step. Point out the files that pass between stages: the KiCad netlist, the technology file, the Specctra DSN and SES files, and the outputs. Every module opener shows this bar with the current stage highlighted in gold, so students always know where they are in the flow.');
  const st = [['1', 'Schematic', 'KiCad netlist, ref-des, die-pad numbers', 'KiCad'], ['2', 'Tech & parts', 'Layers, rules, dies, package', 'HLayout'], ['3', 'Placement', 'Auto Place on a 50 µm grid', 'HLayout'], ['4', 'Nets & bonds', 'Net names, wire bonds, I/O pads', 'HLayout'], ['5', 'Routing', 'Two conductors, vias, bond fingers', 'Freerouting'], ['6', 'Verify', 'DRC, connectivity, thermal', 'HLayout'], ['7', 'Outputs', 'CAM, drawings, 3D', 'HLayout · FreeCAD']];
  const bw = 1.22, gap = 0.1;
  st.forEach((t, i) => {
    const x = 0.45 + i * (bw + gap), ext = t[3] !== 'HLayout';
    s.addShape(pres.shapes.RECTANGLE, { x, y: 1.55, w: bw, h: 2.2, fill: { color: ext ? PGOLD : PGRN }, line: { color: ext ? GOLD : DIEL, width: 1 } });
    badge(s, t[0], x + 0.1, 1.67, ext ? GOLD : DIEL);
    T(s, t[1], { x: x + 0.1, y: 2.2, w: bw - 0.15, h: 0.5, fontSize: 12.5, bold: true });
    T(s, t[2], { x: x + 0.1, y: 2.72, w: bw - 0.15, h: 0.75, fontSize: 10.5, color: MUTED });
    T(s, t[3], { x: x + 0.1, y: 3.42, w: bw - 0.15, h: 0.25, fontSize: 9.5, bold: true, color: ext ? GOLD : DIEL });
  });
  T(s, [{ text: 'Files between stages: ', options: { bold: true } }, { text: 'alu8_die.net  →  .gds + .hlaux  →  Specctra .dsn / .ses  →  Gerber, DXF, .csv, .pos, STEP', options: { color: MUTED, fontFace: 'Courier New' } }], { x: 0.45, y: 4.05, w: 9.1, h: 0.35, fontSize: 11.5 });
}
{
  const s = content('Course files and checkpoints', 'In C:\\dev\\HLayout\\training\\checkpoints (copied from examples\\8bit_ALU__chip_wire)', 'Introduction',
    'Show students where the files are. The checkpoints let anyone who falls behind or breaks their design rejoin the class. They are copies of the example files, so students cannot damage the originals. Explain the .hlaux sidecar: it travels with every .gds and holds the technology, net names, attached libraries and window settings; always copy both files together. The technology file alu8_tech.json is the process on its own, without any design. CP3 deliberately contains two DRC spacing errors; that is the start of Lab 12. CP0 is the finished drawing for the shapes lab.');
  table(s, [['File', 'Contains', 'Used in'],
  ['CP0_shapes_reference.gds', 'Reference drawing for the shapes lab', 'Lab 2'],
  ['alu8_die.net', 'KiCad netlist, pins numbered by die pad', 'Labs 6, 8'],
  ['alu8_netlist.csv', 'Every pin: LCC terminal and die pad', 'Lab 6'],
  ['alu8_tech.json', 'The ALU8 process: layers, rules, routing settings', 'Labs 3, 5'],
  ['CP1_dies_on_substrate.gds', 'Six dies and I/O pads on a 30 × 30 mm substrate', 'Labs 7, 8, capstone'],
  ['CP2_bonded_ready_to_route.gds', 'Placed in the DIP40, bonded, fingers fanned out', 'Labs 9, 10, 16'],
  ['CP3_routed.gds', 'All 46 nets routed; two DRC errors left in', 'Labs 4, 11–15'],
  ['8bit_alu.gds', 'The finished reference design', 'Lab 1, comparison'],
  ], 0.45, 1.3, 9.1, [2.7, 4.6, 1.8], 10.5, 0.34);
  T(s, 'Always copy a .gds together with its .hlaux sidecar.', { x: 0.45, y: 4.75, w: 8, h: 0.3, fontSize: 12, bold: true, color: GOLD });
}

// =============================================================================
divider('DAY 1', 'Foundations', 'Hybrids in brief, the HLayout window, shapes and cells, technology setup, mask operations and dielectric, and thick-film resistors.',
  'DAY 1 opens. Today is about the tools and ideas every layout uses. By 17:00 everyone can draw and edit shapes and cells, set up the ALU8 process, use mask operations and the dielectric, and size and trim a printed resistor. Tomorrow we build the ALU8 itself.');

// ---- Module 2
opener(2, 'Thick-film hybrids in 30 minutes', '09:45 · 30 min', ['Name the printed films of a thick-film hybrid and their print order', 'Explain die attach, wire bonds and bond fingers', 'Pick the right build path for a hybrid'], [0, 1], 'die_bonded_zoom.png',
  'Keep this short and visual: it is the vocabulary for the rest of the course. Experienced hybrid engineers can help explain; PCB designers need the differences. Key message: a hybrid is printed and fired film by film on a ceramic substrate, so layer order means print order, and a via is a hole in a printed dielectric rather than a drilled hole.');
{
  const s = content('Built film by film', 'Each layer is screen-printed and fired in turn on a ceramic substrate', 'Module 2 · Hybrids',
    'Walk up the stack from the bottom. Alumina substrate, 0.635 mm thick by default. 1st conductor printed and fired (12 µm in the ALU8 technology). Dielectric printed over it, about 30 µm, with a window at every via; this is what insulates the two conductors. 2nd conductor on top (12 µm). Resistor inks and glaze come after; the ALU8 has no printed resistors, but the Resistor Designer covers them. Then die-attach epoxy and the dies. Ask: why can a track on the 1st conductor not run outside the dielectric if the 2nd conductor crosses it? Because the two would short where the dielectric is missing.');
  const L = [['Glaze overprint', 'D5E1EC', INK, 0.35], ['Resistor ink', '2F3B4F', WHITE, 0.3], ['2nd conductor · 12 µm', 'A64FC2', WHITE, 0.32], ['Dielectric · 30 µm, window at each via', DIEL, WHITE, 0.45], ['1st conductor · 12 µm', 'E0A83A', INK, 0.32], ['Alumina substrate · 0.635 mm', 'E8E4DA', INK, 0.8]];
  let y = 1.4; L.forEach(l => { s.addShape(pres.shapes.RECTANGLE, { x: 0.45, y, w: 4.6, h: l[3], fill: { color: l[1] }, line: { color: WHITE, width: 0.5 } }); T(s, l[0], { x: 0.6, y, w: 4.3, h: l[3], fontSize: 11.5, color: l[2], valign: 'middle', bold: true }); y += l[3]; });
  bullets(s, [['Print order is layer order.', 'HLayout gives every layer a role, and the top view, 3D model and thermal model stack them in that order.'], ['A via is a window', 'in the printed dielectric, filled when the 2nd conductor is printed.'], ['No dielectric, no crossing.', 'Outside the dielectric only the 1st conductor may run.'], ['Thicknesses matter', 'for the 3D model and the thermal analysis.']], 5.4, 1.4, 4.15, 3.2, 13);
}
{
  const s = content('Chip and wire', 'Bare dies glued down and wire-bonded to the substrate', 'Module 2 · Hybrids',
    'Use the close-up of U1, a 54AS181. The die (salmon) sits on epoxy on a die-attach pad on the 2nd conductor, 200 µm larger than the die all round. Each die pad has a gold wire bond (pale lines) out to a bond finger, a small square pad on the 2nd conductor where the router picks the net up. Bonds must be between 1.0 and 2.5 mm long in the ALU8 rules and leave the die within 45 degrees of straight out, so they do not cross or touch the die edge. Ask: why not route straight to the die pad? Because the die pad is above the substrate, on the die; the only connection is the wire.');
  const r = img(s, 'die_bonded_zoom.png', 0.45, 1.3, 4.3, 3.6); cap(s, 'U1 (54AS181) bonded, before routing', r);
  bullets(s, [['Die attach pad', 'on the 2nd conductor, 0.2 mm larger than the die all round; epoxy on top.'], ['Die pads', '100 µm square, numbered as the DIP pins (U1.12 = pad 12 = GND).'], ['Wire bond', '30 µm gold wire from die pad to bond finger.'], ['Bond finger', 'the substrate end of the bond; the router connects to it.'], ['Bond rules', '1.0–2.5 mm long, at most 45° off the die-edge normal.']], 5.1, 1.3, 4.45, 3.6, 13);
}
{
  const s = content('Three ways to build a hybrid', 'Pick the path before placing anything: it decides which layers carry which pads', 'Module 2 · Hybrids',
    'Path A uses packaged parts such as LCC chip carriers on two conductors; example 2 is the ALU16 built that way. Path B is what we do in this course: bare dies, two conductors, bond fingers on the 2nd conductor. Path C has one conductor and no dielectric, so where two tracks would cross a wire-bond jumper carries one over the other; the notch filter example uses it. The ALU8 could be built on path A with LCC packages; going to bare dies saves area and weight, at the cost of bonding.');
  img(s, 'toolchain_paths.png', 0.45, 1.3, 5.7, 3.7);
  bullets(s, [['A · Packaged parts', 'LCC, SMD, SOT on two conductors.'], ['B · Chip and wire, two conductors', 'bare dies, bond fingers on the 2nd conductor. This course.'], ['C · Chip and wire, one conductor', 'wire-bond jumpers instead of crossings.']], 6.4, 1.4, 3.15, 3.4, 13);
}

// ---- Module 3
opener(3, 'A tour of HLayout', '10:30 · 45 min with Lab 1', ['Find your way round the main window, menus and panels', 'Use the mouse, the command bar and Python on the same design', 'Know what the .gds and .hlaux files hold'], [], 'main_alu16.png',
  'Demo-heavy module. Open 8bit_alu.gds on the projector and drive while students watch, then they repeat it in Lab 1. The screenshot here is the ALU16 example; the window is the same.');
{
  const s = content('The main window', null, 'Module 3 · Tour',
    'Point to each area on the projector rather than reading the list. Menu bar across the top: File, Setup, View, Create, Edit, Tools, Help. The canvas takes most of the width; F fits the design, G toggles the grid, the scroll wheel zooms. The right-hand panel has three tabs: Layers (visibility, colour, fill per layer), Properties (whatever is selected) and Console (Python). The command bar sits under the canvas, and the status bar under that reports what each tool did; read it often. Help → User Manual and Command Reference (F1) are always one click away, and Help → Report Bug is new in 0.6.');
  const r = img(s, 'main_alu16.png', 0.45, 1.25, 5.7, 3.65); cap(s, 'HLayout 0.7 main window (ALU16 example shown)', r);
  bullets(s, [['Canvas', 'F fits, G grid, wheel zooms, drag to pan.'], ['Layers tab', 'show, hide and colour each of 64 layers.'], ['Properties tab', 'the selected shape, part or bond.'], ['Console tab', 'Python on the open design.'], ['Command bar', 'short commands: FIT, DRC RUN, BOND.'], ['Status bar', 'what each tool just did. Read it.']], 6.4, 1.25, 3.15, 3.7, 12.5);
}
{
  const s = content('Three ways to do everything', 'The mouse, the command bar and Python all work on the same design and the same undo history', 'Module 3 · Tour',
    'Show the same action three ways: highlight the GND net by clicking with Edit → Highlight Net, then type HILITE GND on the command bar, then run the Python lines in the Console tab. Coordinates on the command bar are in database units, which are microns: 1 DBU = 1 µm. Dialogs mostly use millimetres. Everything is undoable with Ctrl+Z, including a whole Freerouting run.');
  bullets(s, [['Mouse:', 'Edit → Highlight Net, then click any pad.'], ['Command bar:', 'type a command and press Enter; Up recalls history.'], ['Python:', 'the Console tab, or Tools → Run Script File.'], ['Units:', '1 DBU = 1 µm on the command bar and in scripts; dialogs use mm.'], ['Undo:', 'Ctrl+Z / Ctrl+Y, even after a whole route.']], 0.45, 1.4, 4.3, 3.4, 13.5);
  code(s, '# command bar\nFIT\nHILITE GND\nZOOM WINDOW 44000,-17500 56000,-6500\nHILITE OFF\n\n# Python (Console tab)\nnets = s.sli()\nprint(nets["complete"], "nets complete")', 5.0, 1.4, 4.55, 3.2, 11.5);
}
{
  const s = content('What is in a design file', null, 'Module 3 · Tour',
    'A design is two files. The .gds is standard GDSII geometry that any layout tool can read. The .hlaux sidecar beside it holds everything HLayout adds: the technology (layer names and roles, rules, routing settings), net names, the bond diagram, attached libraries and saved window settings. HLayout writes both on File → Save GDS and keeps three rolling backups (.bak1 to .bak3). A technology file (.json) carries the process on its own, so the next job on the same line starts from it. File → Archive Design zips everything for the job record.');
  const cards = [['.gds', 'Geometry in standard GDSII: cells, shapes, placed parts. 1 DBU = 1 µm.', DIEL], ['.gds.hlaux', 'Technology, net names, bond diagram, attached libraries, window settings.', GOLD], ['.json technology file', 'The process alone: layers, rules, routing and bond settings. Reuse it on the next job.', INK], ['.bak1 – .bak3', 'Three rolling backups written on every save.', MUTED]];
  cards.forEach((c, i) => { const x = 0.45 + (i % 2) * 4.6, y = 1.35 + Math.floor(i / 2) * 1.65; s.addShape(pres.shapes.RECTANGLE, { x, y, w: 4.45, h: 1.45, fill: { color: ALU }, line: { color: ALU } }); s.addShape(pres.shapes.RECTANGLE, { x: x + 0.2, y: y + 0.22, w: 0.22, h: 0.22, fill: { color: c[2] }, line: { color: c[2] } }); T(s, c[0], { x: x + 0.55, y: y + 0.16, w: 3.7, h: 0.35, fontSize: 15, bold: true, fontFace: 'Courier New' }); T(s, c[1], { x: x + 0.2, y: y + 0.62, w: 4.05, h: 0.75, fontSize: 12, color: MUTED }); });
}
lab(1, 'Find your way round the ALU8', '10:45 · 30 min', 'Open the finished design and learn to navigate, inspect and measure it.', 'checkpoints\\8bit_alu.gds',
  ['File → Open GDS… and open 8bit_alu.gds; press F to fit.', 'Hide and show the 1st conductor (layer 3) and the dielectric (layer 4) in the Layers tab.', 'Zoom to U1 and click a die pad; read its pin in the Properties tab.', 'Edit → Highlight Net, click a pad of C4; follow the carry from U1 to U2.', 'Use Edit → Measure to measure a wire bond.', 'On the command bar type HILITE GND, then HILITE OFF.', 'In the Console tab run print(s.sli()["complete"]).'],
  ['C4 runs from U1 pad 16 to U2 pad 7', 'You measured a bond between 1.0 and 2.5 mm', 'The console prints 46'], 'Lab 1',
  'Circulate. Common trips: students click the die body rather than the pad, so the Properties tab shows the part; zoom in further. If HILITE reports an unknown net, they typed it in lower case on a version that is case sensitive; use upper case. The console line confirms all 46 nets are routed in the reference design. Early finishers: hide every layer except 58 and 59 to see only die pads and bonds.');

// ---- Module 4
// ---- Module 4: shapes, paths, SRef, ARef
opener(4, 'Shapes, paths and cell instances', '11:15 · 75 min with Lab 2', ['Draw rectangles, polygons, circles, text and paths, and choose path end caps', 'Build a cell and place it as an SRef or an ARef', 'Edit shapes and instances: reshape, rotate, mirror, copy, smash and merge'], [1], 'shapes_demo.png',
  'The building blocks. Everything in a hybrid layout, from a bond finger to a whole die part, is made of these few primitives and cell instances. Students who know GDSII tools will recognise them; the HLayout-specific points are the end caps, the 200-point limit, and how editing a cell changes every instance of it.');
{
  const s = content('The drawing primitives', 'Create menu, or the matching command; every shape sits on one of 64 layers', 'Module 4 · Shapes',
    'Walk through the table, demonstrating each on the canvas. A polygon is limited to 200 points, the GDSII limit; HLayout fractures generated geometry like the dielectric fill to stay under it. A rectangle stays a rectangle until you drag one of its vertices or edges; it then becomes a polygon, keeping its layer and pin. A circle is stored as a regular polygon of 8, 16, 32 or 64 segments, set in Setup → Circle Segments. Text has a height and a font and is not exported to Gerber. Every primitive has a command-bar equivalent with coordinates in microns.');
  table(s, [['Primitive', 'How to draw it', 'Command'], ['Rectangle', 'Click two opposite corners', 'RECT layer x1,y1 x2,y2'], ['Polygon', 'Click points; Enter or double-click closes (3–200 points)', 'POLY layer x1,y1 x2,y2 …'], ['Path', 'Click the centre line; width and end cap from Path Style', 'PATH layer width cap x1,y1 …'], ['Circle', 'Click centre, then a point on the radius', 'CIRCLE layer cx,cy r [segments]'], ['Text', 'Click the insertion point, type the string', 'TEXT layer x,y "text" [height]'], ['Cell instance', 'Pick a cell, click where it goes', 'SREF cell x,y [rot] [mag] [mirror]'], ['Array instance', 'Pick a cell, columns, rows, spacing', 'AREF cell x,y cols rows dx dy']], 0.45, 1.3, 5.7, [1.35, 2.45, 1.9], 10.5, 0.38);
  const r = img(s, 'shapes_demo.png', 6.4, 1.3, 3.15, 3.0); cap(s, 'CP0_shapes_reference.gds', r);
}
{
  const s = content('Paths: width and end caps', 'A path is a centre line with a width; the end cap decides where the copper stops', 'Module 4 · Shapes',
    'Show the three end caps on the reference drawing. Flush stops exactly at the end point. Square extends half the width past it, so two paths meeting at a corner overlap cleanly. Round adds a half circle, also half the width past the end. Set the defaults in Setup → Path Style; Edit Selected changes one path. While Ortho/45° routing is on, every segment snaps to horizontal, vertical or 45 degrees. Backspace removes the last point while drawing; Enter finishes. To extend a finished path use Edit → Continue Path, and Edit → Insert Vertex on Edge adds a corner to an existing path. Mask operations, DXF and DRC turn a path into its outline polygon, so the cap affects spacing checks.');
  const caps = [['Flush', 'Stops at the end point', 0], ['Square', 'Extends half the width past the end', 1], ['Round', 'Half circle, half the width past the end', 2]];
  caps.forEach((c, i) => {
    const y = 1.45 + i * 0.95;
    s.addShape(pres.shapes.RECTANGLE, { x: 0.65 - (i === 1 ? 0.18 : 0), y: y + 0.12, w: 2.2 + (i === 1 ? 0.18 : 0), h: 0.36, fill: { color: 'A64FC2' }, line: { color: 'A64FC2' } });
    if (i === 2) s.addShape(pres.shapes.OVAL, { x: 0.47, y: y + 0.12, w: 0.36, h: 0.36, fill: { color: 'A64FC2' }, line: { color: 'A64FC2' } });
    s.addShape(pres.shapes.LINE, { x: 0.65, y: y + 0.3, w: 2.2, h: 0, line: { color: WHITE, width: 1, dashType: 'dash' } });
    s.addShape(pres.shapes.OVAL, { x: 0.6, y: y + 0.25, w: 0.1, h: 0.1, fill: { color: GOLD }, line: { color: GOLD } });
    T(s, c[0], { x: 3.1, y: y + 0.05, w: 1.6, h: 0.3, fontSize: 14, bold: true });
    T(s, c[1], { x: 3.1, y: y + 0.36, w: 2.3, h: 0.4, fontSize: 11.5, color: MUTED });
  });
  T(s, 'Gold dot: the end point. Dashed line: the centre line.', { x: 0.45, y: 4.3, w: 5, h: 0.3, fontSize: 10.5, italic: true, color: MUTED });
  bullets(s, [['Defaults', 'Setup → Path Style… (width, end cap).'], ['Draw', 'click points; Backspace undoes a point; Enter finishes.'], ['Extend', 'Edit → Continue Path…'], ['Add a corner', 'Edit → Insert Vertex on Edge.'], ['Reshape', 'drag a vertex or a whole segment.']], 5.7, 1.4, 3.85, 3.0, 12.5);
  code(s, 'PATH 5 400 round 0,4000 3000,4000 3000,3300', 5.7, 4.3, 3.85, 0.5, 10.5);
}
{
  const s = content('Cells, SRefs and ARefs', 'Draw once, place many times', 'Module 4 · Shapes',
    'A cell is a named group of shapes, like a footprint, a die or a via. An SRef places one copy of a cell at an origin, with rotation, magnification, mirroring and a label, which is the reference designator. An ARef places a grid of copies: columns, rows and the spacing between them. Both are references: change the cell and every instance changes, which is how one die part serves U3 to U6 in the ALU8. Rotation is counter-clockwise about the cell origin, so where you put the origin when you draw the cell matters; it is also the point the 3D STEP model is aligned to on day 3. Cells can come from the design or from an attached library through Find & Place Cell. Create → Create Cell from Selection turns shapes you have drawn into a new cell and replaces them with an instance of it.');
  bullets(s, [['Cell', 'a named group of shapes: footprint, die, via, package.'], ['SRef', 'one placement: origin, rotation, magnification, mirror, label (ref-des).'], ['ARef', 'columns × rows of one cell with fixed spacing.'], ['Origin', 'rotation and mirroring happen about it; the STEP model is aligned to it.'], ['Create', 'Create → Cell Instance / Array Instance / Find & Place Cell…'], ['Make a cell', 'Create → Create Cell from Selection… replaces the shapes with an instance.']], 0.45, 1.35, 5.2, 3.6, 12.5);
  code(s, 'CELL PAD\nRECT 3 -300,-300 300,300\nCELL TOP\nSREF PAD 5300,2600\nSREF PAD 6700,2600 90\nAREF PAD 4900,-300 5 2 1100 1100', 5.9, 1.35, 3.65, 2.2, 11);
  T(s, 'Change PAD once and all 13 instances change.', { x: 5.9, y: 3.7, w: 3.65, h: 0.5, fontSize: 12, bold: true, color: GOLD });
}
{
  const s = content('Editing shapes and instances', 'Every edit is one undo step, whether by mouse, command or script', 'Module 4 · Shapes',
    'Demonstrate each edit. Click to select, drag to move, drag on empty space for a window selection. Drag a vertex to move one point, an edge to move a whole segment; ortho keeps edges parallel. Double-click opens Edit Selected, where you type exact coordinates, width, layer, rotation, magnification, mirror or label. R rotates the selection 90 degrees counter-clockwise; ROTATE takes any angle. Copy asks for an offset. Arrow keys nudge one grid step. Smash Selected to SRefs breaks an ARef into separate SRefs so you can move or delete one member. Merge Selected unions touching shapes on one layer into a single polygon. Delete removes the selection.');
  table(s, [['Edit', 'How', 'Command'], ['Move', 'Drag, or arrow keys (one grid step)', 'MOVE dx,dy'], ['Reshape', 'Drag a vertex or an edge', '–'], ['Exact values', 'Double-click → Edit Selected…', 'PROPS'], ['Rotate', 'R (90° counter-clockwise)', 'ROTATE deg'], ['Mirror, magnify, label', 'Edit Selected… on an instance', '–'], ['Copy', 'Edit → Copy… with dx, dy', 'COPY dx,dy'], ['Break an array', 'Edit → Smash Selected to SRefs', 'SMASH'], ['Join shapes', 'Window-select, Edit → Merge Selected…', 'UNION'], ['Delete', 'Delete key', 'DELETE LAST']], 0.45, 1.3, 9.1, [2.4, 4.3, 2.4], 11.5, 0.35);
}
lab(2, 'Draw and edit the basics', '11:50 · 40 min', 'Draw every primitive, make a cell, place it as SRefs and an ARef, and edit them.', 'File → New, then open checkpoints\\CP0_shapes_reference.gds to compare',
  ['Draw a rectangle, a polygon and a 32-segment circle on layer 3.', 'Setup → Path Style…: 0.4 mm. Draw three L-shaped paths on layer 5, one with each end cap.', 'Drag a rectangle\'s corner: it becomes a polygon. Undo.', 'Draw a 0.6 mm pad and a small triangle in one corner; select both and Create → Create Cell from Selection… named PAD.', 'Place three PAD SRefs: one plain, one rotated 90° (R), one mirrored (Edit Selected…).', 'Place a 5 × 2 ARef of PAD at 1.1 mm pitch. Smash it and delete one pad.', 'Edit the PAD cell: enlarge the pad. Watch every instance change.'],
  ['All shapes and three path caps drawn', 'PAD placed as SRefs and an ARef', 'Editing PAD changed every copy'], 'Lab 2',
  'The triangle makes rotation and mirroring visible; without it a square pad looks the same every way. Point out that Smash makes the ARef members independent: after smashing, editing the PAD cell still changes them, because they are still SRefs of PAD, but moving one no longer moves the rest. Early finishers: draw a via with Tools → Via Wizard and place it as an SRef.');

opener(5, 'Technology setup', '13:15 · 50 min with Lab 3', ['Set up the layer stack in print order with a role for each layer', 'Enter the route, wire-bond, I/O-pad and dielectric settings', 'Write the design rules and save it all as a technology file'], [1], 'topview_window.png',
  'After lunch. The technology is the process; set it once and reuse it on every job for this line. Students build it from a blank design in Lab 3, then compare with alu8_tech.json.');
{
  const s = content('The ALU8 layer stack', 'Setup → Layers names and colours every layer; numbers are fixed in the technology file', 'Module 5 · Technology',
    'Go through the layers the ALU8 uses. 3 and 5 are the two conductors; 4 the dielectric. 20 is die-attach epoxy. 57 is the substrate outline: it sets the routing area and the thermal model. 58 holds the die pads and 49 the package (header) pads; both are isolated pad layers because they sit above the substrate and connect only through bonds. 59 is the die bonds, 60 the header bonds. 61, 62 and 63 are documentation: outlines, reference designators, dimensions. 6 receives DRC markers. The names come from the ALU8 technology file and include some original spellings.');
  table(s, [['Layer', 'Name in alu8_tech.json', 'Role', 'Fired thickness'], ['3', '1st Routing Conductor', '1st conductor', '12 µm'], ['4', 'Routing Dielectric', 'Dielectric (generated)', '30 µm'], ['5', '2nd Routing Conductor', '2nd conductor, die-attach and fingers', '12 µm'], ['20', 'Epoxy', 'Die attach', '–'], ['57', 'substrate utie', 'Substrate outline', '0.635 mm'], ['58', 'Chip_Pads', 'Die pads (isolated)', '10 µm'], ['49', 'L49', 'Package pin pads (isolated)', '10 µm'], ['59 / 60', 'Chip_wire_Bond / Header_wire_Bond', 'Die bonds / header bonds', '12 / 10 µm'], ['6', 'DRC ERROR', 'DRC markers', '–']], 0.45, 1.3, 9.1, [0.8, 3.0, 3.6, 1.7], 11, 0.33);
}
{
  const s = content('Technology Properties', 'Setup → Technology Properties… holds every process setting in one tabbed window', 'Module 5 · Technology',
    'Open the window on the projector and visit each tab. One OK validates and saves all tabs. Values shown are the ALU8 settings. Route: the two conductors and the via cell. Wire Bonds: bond layer, wire width, minimum and maximum length, and the isolated pad layers, which is important because without it die pads merge with the copper under them in netlist extraction. I/O Pads: size of the pads Bond Header Pins draws. Dielectric: fill layer and the via window margin. Thicknesses feed the 3D model and the thermal model. The More tab links to the DRC rules.');
  table(s, [['Tab', 'ALU8 setting'], ['Route', 'Layer A = 3, Layer B = 5, via cell VIA_Layer3_to_Layer5'], ['Wire Bonds', 'Layer 59, wire 30 µm, ball 100 µm, isolated pad layers 49, 58'], ['I/O Pads', 'Pad 0.9 × 0.9 mm, bond inset 0.2 mm'], ['Dielectric', 'Layer 4, margin 0.2 mm, clearance 0.1 mm, via window margin 0.1 mm'], ['Thicknesses', 'Conductors 12 µm, dielectric 30 µm, bonds 12 µm'], ['Ref-Des', 'Layer 62']], 0.45, 1.3, 9.1, [1.8, 7.3], 12, 0.42);
  T(s, 'Save it: File → Save Technology File… writes the process without any design, ready for the next job.', { x: 0.45, y: 4.45, w: 9.1, h: 0.35, fontSize: 12.5, bold: true, color: GOLD });
}
{
  const s = content('Design rules for the ALU8', 'Tools → DRC… holds the rules; the router uses the same widths and clearances', 'Module 5 · Technology',
    'Five rules for the ALU8. Width and spacing on the 1st conductor at 200 µm. Three wire-bond rules: 1.0 mm minimum length, 2.5 mm maximum, and 45 degrees maximum departure angle from the die-edge normal. Add Bond Rules on the DRC toolbar fills the bond rules in from the Wire Bonds tab. Important: HLayout raises the router clearance and track width to the largest spacing and width rules on the route layers, so the routing passes your own DRC. A good extension exercise is to add the same width and spacing rules for layer 5 and an EDGE_CLEARANCE rule for conductor to substrate edge.');
  table(s, [['Rule', 'Kind', 'Layers', 'Value'], ['1st con min_width', 'MIN_WIDTH', '3', '200 µm'], ['1st con metal_2_metal', 'MIN_SPACE_BETWEEN', '3 – 3', '200 µm'], ['BOND.L.MIN', 'BOND_MIN_LENGTH', '59', '1.0 mm'], ['BOND.L.MAX', 'BOND_MAX_LENGTH', '59', '2.5 mm'], ['BOND.ANGLE', 'BOND_MAX_ANGLE', '59 – 58', '45°']], 0.45, 1.3, 5.9, [2.0, 2.1, 0.9, 0.9], 11.5, 0.4);
  code(s, 'DRCRULE ADD MIN_WIDTH 3 value=200 name="1st con min_width"\nDRCRULE ADD MIN_SPACE_BETWEEN 3 3 value=200\nDRCRULE BONDS angle=45\nDRC RUN error=6', 0.45, 3.85, 9.1, 0.95, 11);
  bullets(s, [['Try adding', 'the same rules for layer 5, and EDGE_CLEARANCE 3 57 to keep copper off the scribe line.']], 6.6, 1.3, 2.95, 2.3, 12.5);
}
lab(3, 'Set up the ALU8 process', '13:35 · 30 min', 'Build the ALU8 technology from a blank design and save it as a technology file.', 'File → New',
  ['Setup → Layers: name layers 3–6, 20, 49, 57–63 (handout table 3.1).', 'Technology Properties → Route: layers 3 and 5.', 'Wire Bonds tab: layer 59, wire 0.03 mm, isolated layers 58, 49.', 'Dielectric tab: layer 4, margin 0.2 mm; Thicknesses: 12/30/12 µm.', 'Tools → DRC: add the five ALU8 rules (use Add Bond Rules).', 'File → Save Technology File… as my_alu8_tech.json.', 'Compare with alu8_tech.json (Setup → Load Technology File in a second window).'],
  ['my_alu8_tech.json is saved', 'Five DRC rules listed', 'Isolated pad layers 58, 49'], 'Lab 3',
  'The full field list is in the handout. Watch for students entering wire width in µm where the field is in mm (0.03, not 30). If anyone finishes early, have them open checkpoints\\alu8_tech.json and note the differences; the router settings in the Autoroute window are also saved in the technology file, which they will see in Lab 10.');

// ---- Module 6
// ---- Module 6: mask operations, dielectric, moving bonds
opener(6, 'Mask operations, dielectric and bond edits', '14:05 · 55 min with Lab 4', ['Grow, shrink and combine layers with mask operations', 'Create, regenerate and delete the dielectric fill', 'Move a wire bond and check it against the rules'], [1, 3, 4], 'diel_after.png',
  'Three tools that change derived or connecting geometry rather than drawing it. All of them work on the routed ALU8, so this module uses a scratch copy of CP3. Students will meet the dielectric again after routing on day 2 and bonds in module 11; here they learn what each tool does to the layout.');
{
  const s = content('Mask operations', 'Tools → Mask Operations… writes a new layer from existing ones; the sources are never changed', 'Module 6 · Mask ops',
    'Six operations. OVERSIZE and UNDERSIZE grow or shrink every shape on a layer by a fixed amount, with sharp mitred corners by default. AND, OR, NOT and XOR combine two layers. Typical hybrid uses: a glaze overprint is the resistor ink oversized by D; a clearance ring is a conductor oversized; NOT gives what is on one layer but not another, such as bare substrate; XOR between two versions of a layer shows exactly what changed, and the ALU8 technology has a saved XOR step for that. Options: a cell, flatten (descend into instances, on by default) and simplify to reduce vertices. The picture shows the ALU8 1st conductor oversized by 100 µm onto layer 55. Paths count as their outline. Tools → Mask Operation Batch saves a recipe of steps and runs it in one go.');
  table(s, [['Op', 'Result', 'Hybrid use'], ['OVERSIZE', 'Layer grown by an amount', 'Glaze over ink, clearance rings'], ['UNDERSIZE', 'Layer shrunk by an amount', 'Inset openings'], ['AND', 'Where both layers are', 'Overlap checks'], ['OR', 'Either layer', 'Merge print layers'], ['NOT', 'A minus B', 'Bare substrate, keep-outs'], ['XOR', 'Where they differ', 'Compare two versions']], 0.45, 1.3, 5.6, [1.3, 2.1, 2.2], 11, 0.38);
  const r = img(s, 'maskop_oversize.png', 6.3, 1.3, 3.25, 2.6); cap(s, 'Layer 3 oversized by 100 µm (green)', r);
  code(s, 'MASKOP OVERSIZE 3 result=55 amount=100\nMASKOP NOT 57 3 result=54', 0.45, 4.25, 5.6, 0.62, 10.5);
}
{
  const s = content('How the dielectric is made', 'You draw the inputs; HLayout generates the printed sheet', 'Module 6 · Dielectric',
    'The dielectric layer holds two kinds of shape. Datatype 0 are the inputs you or the tools draw: the largest enclosing shape is the fill boundary, and everything inside it is a hole: every via window, every resistor keep-out, any exclusion rectangle you draw. Datatype 1 is the generated fill: the boundary minus the holes, fractured into hole-free polygons under the 200-point limit. Keep-outs are grown by the clearance before they are cut; a via window is cut at its drawn size, because that is the printed opening. In the ALU8, 94 input shapes, mostly via windows, give 23 fill pieces. The fill is regenerated automatically after routing and after you move a via. Only the datatype 1 fill is exported and drawn in 3D.');
  const a = img(s, 'diel_before.png', 0.45, 1.3, 3.0, 2.85); cap(s, 'Inputs only: via windows (red)', a);
  const b = img(s, 'diel_after.png', 3.65, 1.3, 3.0, 2.85); cap(s, 'After Generate: fill (blue) with holes', b);
  bullets(s, [['Datatype 0', 'inputs: boundary, via windows, keep-outs.'], ['Datatype 1', 'the generated fill; printed and exported.'], ['ALU8', '94 inputs → 23 fill pieces.'], ['Automatic', 'after routing and via moves.']], 6.9, 1.35, 2.65, 3.2, 12);
}
{
  const s = content('Create, regenerate and delete the dielectric', 'Settings on the Dielectric tab of Technology Properties', 'Module 6 · Dielectric',
    'Tools → Generate Dielectric Fill creates or replaces the fill from the current inputs; run it again whenever the layout changes. Tools → Remove Dielectric Fill deletes only the datatype 1 fill and leaves the inputs, so you can regenerate later. To remove an opening, delete its input shape and regenerate. To make a new opening, draw a shape on the dielectric layer inside the boundary and regenerate. The Dielectric tab holds the fill layer, the margin, the clearance and extra keep-out layers; the via window margin there sets how much larger than its pad every via window is. Resistor keep-outs on the ALU8 are drawn straight onto layer 4, so they become holes automatically.');
  table(s, [['Task', 'Menu', 'Command'], ['Create or replace the fill', 'Tools → Generate Dielectric Fill…', 'DIELECTRIC'], ['Delete the fill', 'Tools → Remove Dielectric Fill', 'DIELECTRIC CLEAR'], ['Add an opening', 'Draw on layer 4 (datatype 0), then Generate', '–'], ['Remove an opening', 'Delete its input shape, then Generate', '–'], ['Change margins', 'Technology Properties → Dielectric tab', 'TECH SET …'], ['Redraw vias and windows', 'Tools → Fix Vias…', 'VIA FIX']], 0.45, 1.3, 9.1, [2.6, 4.2, 2.3], 12, 0.42);
  T(s, 'ALU8: margin 0.2 mm, clearance 0.1 mm, via window = pad + 2 × 0.05 mm (300 µm pad, 400 µm window).', { x: 0.45, y: 4.45, w: 9.1, h: 0.35, fontSize: 12, color: MUTED });
}
{
  const s = content('Moving wire bonds', 'Change where a bond lands without redrawing it', 'Module 6 · Bond edits',
    'Edit → Move Wire Bond: click near the bond; the end nearer your click moves and the other stays. While you move, a live preview redraws the wire from the fixed end with its span, over a faint line showing where it is now, so you can see the length against the minimum and maximum before clicking. Moving the substrate end moves where the bond finger will be laid on the next route; if the design is already routed, the finger is then off its track and that net needs rerouting. Edit → Delete Wire Bond removes the whole bond. For designs from the HLayout chip-and-wire router there is also Edit → Move Bond Finger, which moves the finger and every wire on it, drawing each wire green or red against the bond rules, and BONDINFO, which reports a bond\'s length and angle. After any bond edit run DRC with the bond rules.');
  const r = img(s, 'die_bonded_zoom.png', 0.45, 1.3, 3.6, 3.4); cap(s, 'Bonds round U1', r);
  bullets(s, [['Move Wire Bond', 'moves the nearer end; live span check before you click.'], ['Delete Wire Bond', 'removes the wire and both end balls.'], ['After moving', 'the finger moves on the next route; reroute that net if already routed.'], ['Move Bond Finger', 'chip-and-wire router designs: wires follow, drawn green or red.'], ['Check', 'DRC bond rules: 1.0–2.5 mm, ≤ 45°.']], 4.35, 1.35, 5.2, 3.5, 12.5);
}
lab(4, 'Mask ops, dielectric and a bond move', '14:30 · 30 min', 'Make derived layers, delete and rebuild the dielectric, and move a wire bond safely.', 'checkpoints\\CP3_routed.gds, saved as my_playground.gds',
  ['Save CP3 as my_playground.gds so you never change the checkpoint.', 'Tools → Mask Operations…: OVERSIZE layer 3 by 100 µm into layer 55. Hide 3 and look at 55.', 'NOT: layer 57 minus layer 3 into 54. What does 54 show?', 'Tools → Remove Dielectric Fill. Hide everything but layer 4: what is left?', 'Tools → Generate Dielectric Fill… with the default settings. How many fill pieces?', 'Edit → Move Wire Bond on a U3 bond: drag until the preview turns red, then back to a legal length and click.', 'Run DRC; Ctrl+Z the bond move.'],
  ['Layer 55 is layer 3 grown by 0.1 mm', 'Fill removed and regenerated', 'Bond move checked with DRC'], 'Lab 4',
  'Answers: layer 54 is the bare substrate not covered by 1st conductor. After Remove Dielectric Fill only the 94 input shapes on layer 4 remain, mostly via windows; Generate gives 23 fill pieces again. A bond moved outside 1.0–2.5 mm shows in DRC as BOND.L.MIN or BOND.L.MAX. Make sure students work on the copy.');

// ---- Module 7: thick-film resistors
opener(7, 'Thick-film resistor design', '15:15 · 90 min with Lab 5', ['Explain sheet resistance, squares and why resistors are printed low and trimmed up', 'Size a resistor by hand the way the Resistor Designer does', 'Choose a resistor shape, place it and simulate its laser trim'], [1], 'resistor_anatomy.png',
  'The ALU8 has no printed resistors, so this module uses worked examples and a scratch design; the capstone on day 3 adds a printed pull-up to the ALU8. The numbers on these slides come from HLayout\'s own sizing code and trim simulator.');
{
  const s = content('How a printed resistor works', 'Resistance comes from the ink and the shape: R = Rsh × L / W', 'Module 7 · Resistors',
    'A thick-film resistor is a printed rectangle of resistive ink between two conductor pads. The ink is specified by its sheet resistance, Rsh, in ohms per square at the fired thickness. Any square of that ink, whatever its size, has the same resistance, so R = Rsh × number of squares, and squares = L / W, where L is the gap between the pads and W the ink width. Inks come in decades: the default set is 10, 30, 100, 300 Ω/□ and so on up to 1 MΩ/□. Fired values scatter by tens of percent, so every resistor is laser-trimmed. A laser cut can only remove ink, which only raises resistance, so a resistor is always printed below its target and trimmed up. That is why the designer aims low.');
  s.addShape(pres.shapes.RECTANGLE, { x: 0.45, y: 1.4, w: 4.3, h: 1.5, fill: { color: PGOLD }, line: { color: PGOLD } });
  T(s, 'R = Rsh × L / W', { x: 0.65, y: 1.55, w: 4.0, h: 0.6, fontFace: 'Cambria', fontSize: 30, bold: true, color: GOLD });
  T(s, 'Rsh: ink sheet resistance (Ω/□) · L: pad gap · W: ink width', { x: 0.65, y: 2.25, w: 4.0, h: 0.55, fontSize: 12, color: INK });
  bullets(s, [['Squares', 'L / W. One square of any size has resistance Rsh.'], ['Inks', '10, 30, 100, 300 Ω/□ … 1 MΩ/□ (11 by default).'], ['Trim', 'a laser cut removes ink: R only goes up.'], ['So', 'print low, then trim up to value.']], 0.45, 3.1, 4.3, 1.8, 13);
  const r = img(s, 'resistor_anatomy.png', 5.0, 1.3, 4.55, 3.45, false);
}
{
  const s = content('How the Resistor Designer sizes a resistor', 'Worked example: 1 kΩ, ±5 %, 50 mW, default design rules', 'Module 7 · Resistors',
    'Go through the steps on the worked example. Step 1: the body width comes from the power. Each width has a rating, halved because trimming can remove up to half the width; 50 mW needs 1.4 mm, rated 125 mW, 62.5 mW after trim. Step 2: the aim value is value × (aim % + tolerance %): 1000 × (0.75 + 0.05) = 800 Ω, well below the target so there is room to trim up. Step 3: for each ink, squares = aim / Rsh. Step 4: keep only inks giving 0.25 to 4 squares, the aspect-ratio window. Step 5: recommend the one closest to a single square, the smallest |ln squares|. Here the 1 kΩ/□ ink gives 0.8 squares. Step 6: length = squares × width = 0.8 × 1.4 = 1.12 mm. The other candidates are 300 Ω/□ at 2.67 squares and 3 kΩ/□ at 0.27 squares.');
  const st = [['1', 'Width from power', '50 mW → 1.4 mm (62.5 mW after trim)'], ['2', 'Aim value', '1000 × (75 % + 5 %) = 800 Ω'], ['3', 'Squares per ink', 'squares = 800 / Rsh'], ['4', 'Aspect window', 'keep 0.25 ≤ squares ≤ 4'], ['5', 'Recommend', 'closest to 1 square: 1 kΩ/□, 0.8 sq'], ['6', 'Length', 'L = 0.8 × 1.4 mm = 1.12 mm']];
  st.forEach((t, i) => { const y = 1.3 + i * 0.58; badge(s, t[0], 0.45, y, i === 4 ? GOLD : DIEL, WHITE, 0.4); T(s, t[1], { x: 1.0, y: y - 0.02, w: 1.9, h: 0.45, fontSize: 13, bold: true, valign: 'middle' }); T(s, t[2], { x: 2.9, y: y - 0.02, w: 3.0, h: 0.45, fontSize: 12.5, color: '33404F', valign: 'middle' }); });
  table(s, [['Ink Ω/□', 'Squares', 'L (mm)', ''], ['1 k', '0.80', '1.12', '★'], ['300', '2.67', '3.73', ''], ['3 k', '0.27', '0.37', ''], ['100', '8.00', '–', 'out']], 6.2, 1.3, 3.35, [1.0, 0.85, 0.85, 0.65], 12, 0.4);
  T(s, 'Defaults: aim 75 %, aspect 0.25–4, all 11 inks. Set in Design Rules….', { x: 6.2, y: 3.45, w: 3.35, h: 0.7, fontSize: 11, color: MUTED });
}
{
  const s = content('Width and power', 'Trimming can remove half the width, so each width is rated at half its full power', 'Module 7 · Resistors',
    'The table is the one HLayout uses when Auto from power is ticked: it picks the smallest width whose trim-derated rating covers the power. A resistor dissipating 200 mW therefore needs 2.8 mm, because 2.4 mm is only good for 187.5 mW after trim. These values come from the program itself; the user manual chapter 11 currently lists slightly different full-power figures for 1.8, 2.4, 3.0 and 3.4 mm, and the program is what the designer actually uses. Wider resistors need more area and longer lengths for the same number of squares, so do not oversize. The power density limit for the thermal analysis is separate: 0.08 W/mm² by default.');
  table(s, [['Width (mm)', '1.0', '1.4', '1.8', '2.0', '2.4', '2.8', '3.0', '3.4', '3.8'], ['Full power (mW)', '62.5', '125', '200', '250', '375', '500', '625', '750', '1000'], ['After trim (mW)', '31.3', '62.5', '100', '125', '187.5', '250', '312.5', '375', '500']], 0.45, 1.35, 9.1, [1.9, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8, 0.8], 12, 0.45);
  table(s, [['Example', 'Power', 'Width', 'Aim', 'Ink', 'Squares', 'L (mm)'], ['1 kΩ ±5 %', '50 mW', '1.4', '800 Ω', '1 kΩ/□', '0.80', '1.12'], ['10 kΩ ±1 %', '100 mW', '1.8', '7.6 kΩ', '10 kΩ/□', '0.76', '1.37'], ['47 Ω ±5 %', '200 mW', '2.8', '37.6 Ω', '30 Ω/□', '1.25', '3.51'], ['220 kΩ ±10 %', '10 mW', '1.0', '187 kΩ', '300 kΩ/□', '0.62', '0.62']], 0.45, 2.95, 9.1, [1.7, 1.1, 1.0, 1.2, 1.4, 1.2, 1.5], 12, 0.38);
}
{
  const s = content('From size to layout', 'Place in Layout… builds the ink, pads, glaze and keep-out as one cell', 'Module 7 · Resistors',
    'The geometry rules live on the Resistors tab of Technology Properties. The ink extends A past the gap at each end, so it overlaps the pads for a reliable contact. Each pad runs B beyond that overlap and is C wider than the ink on each side. The glaze covers the ink plus D all round. The dielectric keep-out covers the gap by the ink width, plus E all round; on the ALU8 it is drawn straight on the dielectric layer, so the ink fires on bare ceramic. For the 1 kΩ example with the ALU8 rules: ink 1.72 × 1.4 mm, each pad 0.8 × 2.0 mm, overall 2.72 × 2.0 mm. Each ink is drawn on its own layer, mapped by sheet resistance, so each ink gets its own print screen. Place All in Layout lays every sized resistor out in a row; the reference designator goes on the ref-des layer, and the resistor becomes a two-pin part, pins 1 and 2.');
  table(s, [['Rule', 'Meaning', 'ALU8'], ['A', 'Ink overlap into each pad', '0.3 mm'], ['B', 'Pad length beyond the overlap', '0.5 mm'], ['C', 'Pad wider than the ink, each side', '0.3 mm'], ['D', 'Glaze oversize all round', '0.3 mm'], ['E', 'Dielectric keep-out oversize', '1.0 mm']], 0.45, 1.3, 4.6, [0.6, 2.9, 1.1], 11.5, 0.38);
  T(s, '1 kΩ example: ink 1.72 × 1.40 mm · pads 0.80 × 2.00 mm · overall 2.72 × 2.00 mm', { x: 0.45, y: 3.75, w: 4.6, h: 0.6, fontSize: 12, bold: true, color: GOLD });
  const r = img(s, 'resistor_anatomy.png', 5.3, 1.3, 4.25, 3.3, false);
  code(s, 'RESGEN 0,0 1400 1120 ink=1000 ref=R1', 0.45, 4.4, 4.6, 0.5, 11);
}
{
  const s = content('Laser trim', 'Resistor Designer → Trim Simulator… solves the current flow at every step of the cut', 'Module 7 · Resistors',
    'The trim simulator models the body as a resistor network and records resistance and sensitivity as the cut advances. The run here is the 1 kΩ example: printed at about 793 Ω, close to the 800 Ω aim. A plunge cut in from the bottom edge to 25 % of the width raises it to 913 Ω, but sensitivity, the ohms added per micron of cut, climbs steeply as the tip narrows the current path. Turning the cut 90 degrees into an L-cut for 400 µm brings it to 995 Ω while sensitivity falls from about 0.4 to under 0.1 Ω/µm, which is what makes the final approach to value controllable. That is why production trims use L-cuts, and why the aim value leaves headroom. The heat map in the simulator shows the current crowding at the cut tip.');
  const r = img(s, 'trim_curve.png', 0.45, 1.3, 5.9, 2.85, false); cap(s, '1 kΩ example: plunge to 25 % of the width, then a 400 µm L-cut (40 µm kerf)', r);
  table(s, [['Stage', 'R (Ω)', 'Sens. (Ω/µm)'], ['As printed', '793', '–'], ['Plunge 25 %', '913', '0.42'], ['L-cut 400 µm', '995', '0.09']], 6.6, 1.3, 2.95, [1.35, 0.7, 0.9], 11.5, 0.4);
  T(s, 'Plunge: fast but sensitivity rises. L-cut: slow, controllable final approach.', { x: 6.6, y: 3.0, w: 2.95, h: 1.0, fontSize: 12, color: MUTED });
}
{
  const s = content('Beyond the rectangle', 'Resistor Designer → Add/Edit → Shape: seven more thick-film shapes', 'Module 7 · Resistors',
    'A rectangle sized for a high value is long and thin, needs pads at both ends and has a limited trim range. The Designer now sizes seven other shapes. Top-hat: a crown on the middle of the bar; the plunge runs up through the bar into the crown, so the crown adds trim range. Top-hat with roof: a conductor roof caps the crown; once the plunge has cut the bar, current goes up one side of the crown, across the roof and down the other, so the cut can run almost to the roof and never opens the resistor. U-shape: both pads side by side. U with roof: a roofed skirt under the U for the same safe trim. Angle, double angle, three-sided angle: pads on adjacent or opposite sides to suit the routing. Sizing: squares are still aim / Rsh, but a shape is solved as a resistive sheet, so each right-angle corner counts as about 0.56 of a square and a crown or roof is counted exactly; the Designer then adjusts the shape length for each ink inside the aspect rules. Placing draws pin 1 and 2 terminations to A, B, C, a pin-less roof, glaze grown by D and keep-out grown by E, and the resistor DRC checks the real outlines.');
  const r = img(s, 'resistor_shapes.png', 0.45, 1.25, 5.6, 3.45); cap(s, 'The eight shapes, each sized to the squares shown', r);
  bullets(s, [['Top-hat', 'crown adds trim range'], ['With roof', 'cut never opens the resistor'], ['U-shape', 'pads side by side'], ['Angles', 'pads where the routing needs them'], ['Sizing', 'solved as a sheet: a corner is about 0.56 sq'], ['Checks', 'DRC on the real outline']], 6.25, 1.35, 3.3, 3.4, 12);
}
{
  const s = content('Trimming a shaped resistor', 'Trim Simulator… cuts along the shape\'s own plunge path', 'Module 7 · Resistors',
    'For a shaped resistor the Trim Simulator opens a simpler window: kerf, plunge depth (pre-filled with the deepest safe cut) and steps. The example is a 4.7 kOhm 5 % resistor drawn as a top-hat with roof: 1 kOhm/sq ink, 3.76 squares, bar 6.06 mm. Printed at about 3750 Ohm, the plunge reaches 4.7 kOhm at about 1.55 mm and could go on to about 5530 Ohm at the full 2.66 mm without opening, because the roof keeps a path round the cut; the hot spots are the crown corners. In the lab students do the same with R1 (1 kOhm): 300 Ohm/sq, 2.67 squares, printed about 797 Ohm, value at about 1.24 mm. Compare an angle of the same squares: its plunge across one arm tops out at about 935 Ohm, so it cannot reach value; the window says so. That is the point of the crown and the roof: trim range.');
  const r = img(s, 'shape_trim.png', 0.45, 1.3, 9.1, 3.2, false); cap(s, 'A 4.7 kΩ top-hat with roof (1 kΩ/□, 3.76 squares): power map after the plunge, and the trim curve', r);
}
lab(5, 'Size, place and trim resistors', '16:00 · 40 min', 'Size four resistors with the Resistor Designer, check them by hand, place them and trim one.', 'File → New, then Setup → Load Technology File… alu8_tech.json',
  ['Tools → Thick-Film Resistor Designer…. Check Design Rules…: aim 75 %, aspect 0.25–4.', 'Add R1 1 kΩ 5 % 50 mW, R2 10 kΩ 1 % 100 mW, R3 47 Ω 5 % 200 mW, R4 220 kΩ 10 % 10 mW, with Auto from power.', 'Fill in the handout table by hand first, then compare with the Solutions tab.', 'Auto-Select All, then Place All in Layout…. Look at each resistor\'s layers.', 'Select R1 and open Trim Simulator…: plunge 25 % depth, 40 µm kerf; then Add L-Cut 400 µm.', 'Edit R1: Shape Top-hat with roof. Note ink, squares and L; trim it and find where it reaches 1 kΩ.'],
  ['Your hand sizes match the Designer', 'Four resistors placed with glaze and keep-out', 'R1 trims to about 995 Ω; as a roofed top-hat it never opens'], 'Lab 5',
  'Answers: R1 1.4 mm, aim 800 Ω, 1 kΩ/□, 0.80 sq, 1.12 mm. R2 1.8 mm, aim 7.6 kΩ, 10 kΩ/□, 0.76 sq, 1.37 mm. R3 2.8 mm (2.4 mm is only 187.5 mW after trim), aim 37.6 Ω, 30 Ω/□, 1.25 sq, 3.51 mm. R4 1.0 mm, aim 187 kΩ, 300 kΩ/□, 0.62 sq, 0.62 mm. The trim simulator needs numpy and scipy, which the installers include. Discuss why R3 needs a long body: low-value inks and high power. Shape step: R1 as a top-hat with roof takes 300 Ohm/sq ink, 2.67 squares, bar 4.53 mm; printed about 797 Ohm, reaches 1000 Ohm at about 1.24 mm of plunge and never opens.');

{
  const s = content('Day 1 review', 'Five questions before we go', 'Day 1 review',
    'Take answers from the room. 1: square extends half the width past the end point; flush stops at it. 2: rotation is about the cell origin, and the 3D model is aligned to it. 3: Remove Dielectric Fill deletes only the generated fill (datatype 1), so the inputs stay and you can regenerate. 4: 800 Ω, so the laser can trim up to 1 kΩ; a laser can only raise resistance. 5: 1.4 mm; 1.0 mm is only 31.3 mW after trim. Preview day 2: the ALU8 circuit, dies, placement, bonds and routing.');
  const q = ['What is the difference between a square and a flush path end?', 'Why does it matter where a cell\'s origin is?', 'What does Remove Dielectric Fill delete, and what does it keep?', 'Why is a 1 kΩ ±5 % resistor printed at 800 Ω?', 'Which body width does a 50 mW resistor need, and why not 1.0 mm?'];
  q.forEach((t, i) => { badge(s, String(i + 1), 0.45, 1.35 + i * 0.7, GOLD); T(s, t, { x: 1.05, y: 1.4 + i * 0.7, w: 8.4, h: 0.5, fontSize: 14.5 }); });
}

divider('DAY 2', 'From netlist to a routed design', 'The ALU8 circuit, bare dies and the package, Auto Place, nets and wire bonds, routing with Freerouting and editing the route.',
  'DAY 2 opens. Recap day 1 in two minutes. Today the class builds the ALU8: the circuit and netlist, dies, placement, bonds and routing. After lunch everyone moves to CP2, the reference placement in the DIP40 with the dies bonded and fingers fanned out, so the room stays in step whatever happened with their own placement.');

// ---- Module 8
opener(8, 'The circuit and its netlist', '09:00 · 45 min with Lab 6', ['Explain how the two 54AS181 slices and four gates make an 8-bit ALU with flags', 'Read a KiCad netlist and tell LCC terminals from die pads', 'Know which netlist file Auto Place needs for bare dies'], [0], 'alu8_dies_preview.png',
  'Day 2 starts with the circuit. Electrical background for the layout. Designers do not need to verify the logic, but knowing which nets are critical (carry, power) helps them judge placement and routing. Keep the logic short and spend the time on pin numbering, which is the most common source of errors.');
{
  const s = content('How the ALU8 works', 'Two 4-bit slices in series, plus four gates for the flags', 'Module 8 · Circuit',
    'U1 handles bits 0–3 and U2 bits 4–7. The carry ripples from U1 Cn+4 to U2 Cn on net C4, so no 54S182 look-ahead chip is needed for two slices. S0–S3 and M choose the function and go to both slices. The gates make the status flags: U3 (54LS27 triple NOR) and U4 (54LS30 8-input NAND) give Z, zero, when all eight F outputs are low; U5 (54LS86 XOR) and U6 (54LS00 NAND) give C, carry, and V, overflow. N, negative, is simply F7. Placement consequence: U1 and U2 share many nets (S0–S3, M, C4) and should sit close together; the flag gates cluster near the F outputs.');
  const box = (x, y, w, h, t, sub, c) => { s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: c }, line: { color: c } }); T(s, t, { x: x + 0.1, y: y + 0.08, w: w - 0.2, h: 0.3, fontSize: 13, bold: true, color: WHITE }); T(s, sub, { x: x + 0.1, y: y + 0.4, w: w - 0.2, h: h - 0.45, fontSize: 10.5, color: SOFT }); };
  box(0.45, 1.45, 2.3, 1.3, 'U1  54AS181', 'Bits 0–3\nA0–A3, B0–B3 → F0–F3', INK);
  box(3.2, 1.45, 2.3, 1.3, 'U2  54AS181', 'Bits 4–7\nA4–A7, B4–B7 → F4–F7', INK);
  s.addShape(pres.shapes.LINE, { x: 2.75, y: 2.1, w: 0.45, h: 0, line: { color: GOLD, width: 2.5, endArrowType: 'triangle' } });
  T(s, 'C4', { x: 2.75, y: 1.75, w: 0.45, h: 0.3, fontSize: 11, bold: true, color: GOLD, align: 'center' });
  box(0.45, 3.1, 1.45, 1.25, 'U3  LS27', 'NOR\nZ from F0–F7', DIEL); box(2.0, 3.1, 1.45, 1.25, 'U4  LS30', '8-in NAND\nZ', DIEL); box(3.55, 3.1, 1.45, 1.25, 'U5  LS86', 'XOR\nV, B7 ^ SUB', DIEL); box(5.1, 3.1, 1.45, 1.25, 'U6  LS00', 'NAND\nC, V', DIEL);
  table(s, [['Op', 'S3–S0', 'M', 'CN'], ['ADD', '1001', '0', '1'], ['SUB', '0110', '0', '0'], ['AND', '1011', '1', '–'], ['OR', '1110', '1', '–'], ['XOR', '0110', '1', '–']], 6.95, 1.45, 2.6, [0.7, 0.8, 0.5, 0.6], 11, 0.32);
  T(s, 'Flags: Z = all F low · N = F7 · C = NOT Cn+4 · V = overflow', { x: 6.95, y: 3.55, w: 2.6, h: 0.8, fontSize: 11, color: MUTED });
}
{
  const s = content('Two pin numbers for every pin', 'A packaged part and its bare die number the same signal differently', 'Module 8 · Circuit',
    'This is the most important slide of the module. The 54AS181 in its LCC package has its GND on terminal 14; on the bare die the same signal is pad 12, which is DIP pin 12. The netlist text shows both: U1.14[12](GND) means LCC terminal 14, die pad 12. alu8.net numbers pins by LCC terminal and suits a packaged build (path A). alu8_die.net numbers them by die pad and is the one Auto Place needs for bare dies, because the die cells are numbered by pad. Using the wrong file gives a placement where every net goes to the wrong pad, and nothing warns you until the route makes no sense.');
  table(s, [['Signal', 'LCC terminal', 'Die pad', 'Netlist text'], ['U1 GND', '14', '12', 'U1.14[12](GND)'], ['U1 VCC', '28', '24', 'U1.28[24](VCC)'], ['U1 Cn+4 (C4)', '19', '16', 'U1.19[16](Cn+4)'], ['U2 Cn (C4)', '9', '7', 'U2.9[7](Cn)'], ['U6 2Y (V)', '9', '6', 'U6.9[6](2Y)']], 0.45, 1.4, 5.4, [1.4, 1.2, 1.0, 1.8], 12, 0.38);
  s.addShape(pres.shapes.RECTANGLE, { x: 6.15, y: 1.4, w: 3.4, h: 3.3, fill: { color: PGOLD }, line: { color: PGOLD } });
  T(s, 'Which file?', { x: 6.35, y: 1.55, w: 3, h: 0.35, fontSize: 15, bold: true, color: GOLD });
  bullets(s, [['alu8.net', 'LCC terminals. For packaged parts.'], ['alu8_die.net', 'Die pads. Use this for the ALU8.'], ['alu8_netlist.csv', 'Both numbers for every pin, for checking.']], 6.35, 1.95, 3.05, 2.6, 12.5);
}
{
  const s = content('What HLayout reads from KiCad', 'File → Export → Netlist in Eeschema writes the .net file', 'Module 8 · Circuit',
    'Auto Place reads the KiCad netlist directly: parts, footprints, symbol fields, nets, net classes and pin types. Name supply nets VCC and GND so they are treated as power. HLayout also reads extra symbol fields that KiCad ignores: HL_Family (die, lcc, smd, resistor), HL_Cell to force a cell, HL_Power_mW for the thermal term, HL_Fixed with HL_X, HL_Y, HL_Rot to lock a part. Without editing the schematic, an overrides CSV with the same columns does the job per project. J1 and J2 in the ALU8 netlist are the input and output pad groups; they have no footprint, so Auto Place does not place them.');
  bullets(s, [['Parts', 'reference designators U1–U6, J1, J2.'], ['Pins', 'die-pad numbers in alu8_die.net.'], ['Nets', '46, with VCC and GND as power nets.'], ['Symbol fields', 'HL_Family, HL_Cell, HL_Power_mW, HL_Fixed, HL_X, HL_Y, HL_Rot.'], ['Overrides CSV', 'the same per job: ref, cell, x, y, rotation, locked, power_mw, layer, family.'], ['J1, J2', 'pad groups with no footprint: not placed.']], 0.45, 1.4, 5.0, 3.6, 13.5);
  code(s, '(net (code "21") (name "C4")\n  (node (ref "U1") (pin "16"))\n  (node (ref "U2") (pin "7")))\n\n(net (code "1") (name "GND")\n  (node (ref "U1") (pin "12"))\n  (node (ref "U2") (pin "12"))\n  ...', 5.75, 1.4, 3.8, 3.0, 11);
  T(s, 'Excerpt from alu8_die.net (simplified)', { x: 5.75, y: 4.45, w: 3.8, h: 0.25, fontSize: 10, italic: true, color: MUTED });
}
lab(6, 'Read the ALU8 netlist', '09:20 · 25 min', 'Find the facts you will need for placement and routing in the netlist files.', 'checkpoints\\alu8_die.net, alu8_netlist.csv',
  ['Open alu8_netlist.csv in a spreadsheet or text editor.', 'How many pins does GND have? Which dies are on it?', 'Which die pads carry the carry net C4?', 'Which nets join U1 and U2 directly?', 'Open alu8_die.net in a text editor and find net F7. How many pins?', 'Which pins of U4 are tied to VCC, and why?'],
  ['GND: 11 pins, all six dies', 'C4: U1 pad 16, U2 pad 7', 'Shared by U1 and U2: S0–S3, M, C4', 'F7 has 6 pins'], 'Lab 6',
  'Answers are in the trainer answer key. Point 6 is a good discussion: U4 is an 8-input NAND but only three inputs (N0, N1, N2) are used; the unused inputs D–H are tied high to VCC so the gate behaves as a 3-input NAND. That is why VCC has 14 pins. Similarly some LS gate inputs are tied to GND. These ties matter for routing because VCC and GND get wider tracks.');

// =============================================================================
// ---- Module 5
opener(9, 'Wizards, bare dies and the package', '09:45 · 75 min with Lab 7', ['Build reusable parts with the Via, Pad and Chip Wizards', 'Describe the parts of a die cell: outline, epoxy, pads, die-attach pad', 'Place the Kovar DIP40 and the substrate'], [1], 'stage1_dies.png',
  'Picture: CP1, the six dies on the 30 × 30 mm working substrate with its I/O pads, as built by build_alu8_dies.py. This module introduces the three wizards that add parts to a library: Via, Pad and Chip. Students build one of each in Lab 7, then look at how the ALU8 dies and package are put together.');
{
  const s = content('Three wizards, one pattern', 'Tools → Via Wizard… / Pad Wizard… / Chip Wizard… add reusable parts to a library', 'Module 9 · Wizards',
    'The wizards are how new parts get into a parts library. All three follow the same pattern: attach a library first (Setup → Attach Library…), fill in a short form, and the finished cell is written straight into that library file, so every design that attaches the library can use it. Without an attached library the wizard tells you to attach one and stops. Each has a command-bar and Python equivalent, which is how the example libraries were built. Every cell they write carries a created and modified date and time, visible in Setup → Library Cells…; the Via and Pad Wizards ask before overwriting a cell of the same name.');
  table(s, [['Wizard', 'Makes', 'You enter', 'Command / Python'], ['Via', 'Square or round pad on two conductors, optional dielectric window', 'Shape, size, layers, window', 'VIA · create_via_cell()'], ['Pad', 'Conductor pad with an undersized paste or epoxy pad; single or rows × cols grid', 'Length, width, margin, layers, grid, pitch', 'PAD · create_pad_cell()'], ['Chip', 'Bare-die footprint: outline, epoxy, die-attach pad, numbered pins', 'Body, epoxy, layers, pin cells, pin count; then click each pin', 'create_chip_cell() + export_cell_to_library()']], 0.45, 1.3, 9.1, [0.9, 3.2, 2.7, 2.3], 11.5, 0.5);
  T(s, [{ text: 'First  ', options: { bold: true, color: GOLD } }, { text: 'Setup → Attach Library… the .gds the part should be saved into. Then Find & Place Cell… places it.', options: { color: INK } }], { x: 0.45, y: 4.35, w: 9.1, h: 0.4, fontSize: 12.5 });
}
{
  const s = content('Via Wizard', 'A via cell that matches the routed vias', 'Module 9 · Wizards',
    'The Via Wizard starts from the design\'s one via definition: the Autoroute signal via pad and the Dielectric tab\'s layer and via window margin. So a cell built with the defaults matches what the router and Fix Vias draw: for the ALU8, a 0.3 mm pad on layers 3 and 5 and a 0.5 mm window on layer 4 (pad plus 0.1 mm each side). The window size follows the pad size until you change it. The default name is VIA_Layer3_to_Layer5; type your own to keep a round and a square version side by side. The Route tab of Technology Properties has a New Via Cell… button that opens this wizard and selects the result.');
  bullets(s, [['Shape', 'square or round; size is the full width or diameter.'], ['Layers', 'Layer 1 and Layer 2, the two conductors it joins.'], ['Window', 'tick Add dielectric via window: layer 4, pad + 2 × margin.'], ['Name', 'VIA_Layer<n1>_to_Layer<n2> by default, or your own.'], ['Save to', 'which attached library.']], 0.45, 1.4, 5.0, 2.9, 13);
  code(s, 'VIA 3 5 shape=round size=300 name=VIA_R300\n    dielectric_layer=4 dielectric_window=500', 0.45, 4.25, 5.0, 0.6, 10.5);
  const r = img(s, 'wiz_via.png', 5.8, 1.35, 3.75, 3.2); cap(s, 'VIA_R300: pad on 3 and 5 (gold), window on 4 (blue)', r);
}
{
  const s = content('Pad Wizard', 'Land patterns: a conductor pad with a smaller paste or epoxy pad', 'Module 9 · Wizards',
    'The Pad Wizard draws a conductor rectangle, length along X and width along Y, with a second rectangle centred on it and undersized by the margin on every side. On a solder process the second layer is solder paste; on a chip-and-wire hybrid like the ALU8 it is the conductive epoxy that holds a chip capacitor, layer 20. Rows and columns lay out a grid of identical pads centred on the origin, with pitch between pad centres. A two-pad footprint is numbered automatically, pin 1 at lower X; any other grid is left for you to number with Edit → Number Pins…, because which pad is pin 1 depends on the part. In the lab students make an 0805 capacitor land: two 1.0 × 1.4 mm pads at 1.9 mm pitch with epoxy 0.1 mm smaller.');
  table(s, [['Field', '0805 land on the ALU8'], ['Length × width', '1.0 × 1.4 mm'], ['Margin', '0.1 mm (epoxy 0.8 × 1.2 mm)'], ['Conductor / paste layer', '5 / 20 (epoxy)'], ['Rows × cols, pitch X', '1 × 2, 1.9 mm'], ['Pins', '1 and 2, numbered for you']], 0.45, 1.3, 5.1, [2.3, 2.8], 12, 0.42);
  code(s, 'PAD LAND_0805 5 20 1.0 1.4 0.1 cols=2 pitch_x=1.9', 0.45, 4.25, 5.1, 0.45, 10.5);
  const r = img(s, 'wiz_pad.png', 5.8, 1.3, 3.75, 2.0); cap(s, 'LAND_0805: conductor (purple), epoxy (grey)', r);
  T(s, [{ text: 'Grid  ', options: { bold: true, color: GOLD } }, { text: 'rows × cols > 1 for a connector strip, with a pitch per direction.', options: { color: INK } }], { x: 5.8, y: 3.8, w: 3.75, h: 0.5, fontSize: 11.5 });
  T(s, [{ text: 'Pins  ', options: { bold: true, color: GOLD } }, { text: '2 pads numbered for you; 3 or more: Edit → Number Pins….', options: { color: INK } }], { x: 5.8, y: 4.25, w: 3.75, h: 0.5, fontSize: 11.5 });
}
{
  const s = content('Anatomy of a die cell', 'Every die is built the same way, so bonding and DRC can find its pads', 'Module 9 · Dies',
    'Use the close-up again, this time naming the layers. Body outline on layer 61, documentation only. Epoxy die attach on layer 20, 100 µm smaller than the body. Die-attach pad on the 2nd conductor (layer 5), 200 µm larger than the die all round. Bond pads on layer 58, 100 µm square, 150 µm in from the die edge, each with a pin number equal to its die-pad number. The pads go round the die in the same order as the LCC terminals, so the bonds fan out without crossing. The 50 µm gap at each corner keeps end pads of adjacent sides apart.');
  const r = img(s, 'die_bonded_zoom.png', 0.45, 1.3, 4.0, 3.55); cap(s, 'U1 die cell with its bonds', r);
  table(s, [['Part', 'Layer', 'Size'], ['Body outline', '61', 'Die size'], ['Epoxy', '20', 'Body − 100 µm'], ['Die-attach pad', '5', 'Body + 200 µm all round'], ['Bond pads', '58', '100 µm, 150 µm from edge'], ['Pin numbers', '–', 'Die-pad number = DIP pin']], 4.8, 1.3, 4.75, [1.6, 0.7, 2.45], 12, 0.42);
  T(s, 'TI does not publish die drawings for these parts: the ALU8 die sizes are representative. Use your supplier\'s die drawing on a real job.', { x: 4.8, y: 4.0, w: 4.75, h: 0.7, fontSize: 11, italic: true, color: MUTED });
}
{
  const s = content('The five ALU8 die types', 'Built by build_alu8_dies.py from one table', 'Module 9 · Dies',
    'Show the table and the preview image of all five dies. All four 54LS parts share the 1.5 mm, 14-pad layout; the 54AS181 is 3.0 mm with 24 pads. Because the script builds from a table, changing to real die sizes is a one-line change per die. In Lab 7 students build a 54LS00 by hand with Chip Wizard to see each step the script automates.');
  table(s, [['Die cell', 'Used as', 'Size', 'Pads'], ['DIE_54AS181', 'U1, U2', '3.0 × 3.0 mm', '24'], ['DIE_54LS27', 'U3', '1.5 × 1.5 mm', '14'], ['DIE_54LS30', 'U4', '1.5 × 1.5 mm', '14'], ['DIE_54LS86', 'U5', '1.5 × 1.5 mm', '14'], ['DIE_54LS00', 'U6', '1.5 × 1.5 mm', '14']], 0.45, 1.3, 4.5, [1.5, 1.0, 1.2, 0.8], 12, 0.42);
  const r = img(s, 'alu8_dies_preview.png', 5.2, 1.3, 4.35, 2.4); cap(s, 'All six dies, and U1 with its pads numbered', r);
  code(s, 'cd examples\\8bit_ALU__chip_wire\npython build_alu8_dies.py', 0.45, 4.1, 4.5, 0.65, 11);
}
{
  const s = content('Chip Wizard', 'Tools → Chip Wizard… builds a die interactively', 'Module 9 · Dies',
    'Demo Chip Wizard on the projector. It needs an attached library holding the die-pad cells; the course uses a copy of chip_wire.gds, which has chip_pin_1 (200 µm square, marks pin 1) and chip_pin (100 µm). Enter the body size, the outline layer (61), the epoxy size and layer (1.4 mm on 20) and tick the die-attach pad (layer 5, 0.2 mm larger all round). Choose the pin cells and the pin count, then Start Placing Pins: the grid switches to 0.1 mm and the status bar asks for each pin in turn; each click places a pin labelled with its number. After the last pin the cell is copied into the first attached library if Save to attached library is ticked, and the grid goes back. Each pin is a real pin of the part, so netlist extraction, Auto Place and the router see it. Pin names: leave blank to number 1 to N, or list names in click order, such as E,B for a transistor; they must match the pin numbers in the KiCad netlist exactly. The collector of a transistor is the back of the die, connected through conductive epoxy to the die-attach pad: tick Back of die is a contact, name it C, and give it a tab (0.6 mm) that reaches past the die keep-out so a track can connect. Compare the result with the script-built DIE_54LS00 in CP1.');
  bullets(s, [['1  Body', 'size, outline layer, epoxy size and layer, die-attach pad.'], ['2  Pins', 'pin cells and count; Pin names blank = 1..N, or e.g. E,B.'], ['Back', 'die back is a contact (collector, drain): attach pad becomes pin C, with a tab.'], ['3  Place', 'Start Placing Pins, then click each pad in order on the 0.1 mm grid.'], ['4  Save', 'tick Save to attached library; name the library cell.']], 0.45, 1.4, 5.2, 3.4, 13);
  const r = img(s, 'wiz_chip.png', 5.9, 1.3, 3.65, 3.45); cap(s, 'MY_54LS00 from the Chip Wizard', r);
}
{
  const s = content('Package and substrate', 'The substrate sits in the Kovar DIP40; its pins are bonded to the substrate edge', 'Module 9 · Dies',
    'The final ALU8 substrate is 21.7 × 14.9 mm and sits in a KOVAR_DIP40: a machined Kovar tub with two rows of 0.46 mm pins on 2.54 mm pitch, 22.86 mm row to row, each in a glass-to-metal seal. The seals are numbered pads on layer 49, pin 1 bottom left, counter-clockwise. examples\\generate_kovar_dip_packages.py makes the package cells. The package is placed first, then the substrate inside it; I/O pads come later from Bond Header Pins in module 8. CP1 uses a larger 30 × 30 mm substrate with I/O pads around the edge for the Auto Place exercise; CP2 onwards uses the DIP40.');
  const r = img(s, 'stage3_bonded.png', 0.45, 1.3, 5.6, 3.0); cap(s, 'The substrate in the DIP40 (CP2)', r);
  table(s, [['Item', 'Value'], ['Package', 'KOVAR_DIP40'], ['Pins', '40 on 2.54 mm pitch, 0.46 mm'], ['Row spacing', '22.86 mm (0.9 in)'], ['Pin pads', 'Layer 49, numbered, pin 1 bottom left'], ['Substrate', '21.7 × 14.9 mm alumina'], ['Pins used', '37 (38, 39 unused)']], 6.3, 1.3, 3.25, [1.15, 2.1], 10.5, 0.38);
}
lab(7, 'Build a parts library with the wizards', '10:15 · 45 min', 'Make a via, a capacitor land and a 54LS00 die with the wizards, save them in your own library, and place them.', 'File → New, load alu8_tech.json; attach checkpoints\\my_parts.gds',
  ['Setup → Attach Library…: checkpoints\\my_parts.gds (a copy of chip_wire.gds).', 'Via Wizard: round, 0.3 mm, layers 3 and 5, window on 4 (0.5 mm), name VIA_R300.', 'Pad Wizard: LAND_0805, 1.0 × 1.4 mm, margin 0.1, layers 5 and 20, 1 × 2 at 1.9 mm.', 'Chip Wizard: MY_54LS00, 1.5 mm body on 61, epoxy 1.4 mm on 20, attach pad on 5; 14 pins.', 'Click pins 1–14 counter-clockwise, 0.6 mm from the centre (handout table 7.1).', 'Find & Place Cell… each part; check Setup → Library Cells… for their dates.', 'Open CP1: compare MY_54LS00 with DIE_54LS00. Then CP2: where does the substrate sit?'],
  ['Three new cells in my_parts.gds', 'LAND_0805 pads numbered 1 and 2', 'MY_54LS00: 14 pins, attach pad 1.9 mm'], 'Lab 7',
  'Each student needs their own copy of my_parts.gds: the wizards write into the attached file. If a wizard says no library is attached, the student skipped step 1. The Via Wizard default name is VIA_Layer3_to_Layer5, which already exists in chip_wire.gds; that is why the lab names it VIA_R300. Pin positions on the 0.1 mm grid differ slightly from the script-built die, whose top and bottom pads sit at 0.225 and 0.45 mm; that is fine, the point is the structure and the counter-clockwise order. Lab 8 starts from CP1, so nothing here is needed later.');

// ---- Module 7
opener(10, 'Auto Place', '11:15 · 75 min with Lab 8', ['Run Auto Place from alu8_die.net and read its result', 'Use straight-line interconnects to judge a placement', 'Improve a placement by hand, lock parts and run Improve'], [2], 'alu8_autoplace_preview.png',
  'Picture: CP1 as built (left) and after Auto Place (right). Auto Place is where students first see the design come together, and they enjoy comparing results. Keep the seed and settings the same across the room so results can be compared, then let them experiment.');
{
  const s = content('How Auto Place decides', 'Shortest total weighted net length, no overlaps, hot parts apart', 'Module 10 · Auto Place',
    'Explain the score. Every net counts in proportion to its weight, power and ground included. A two-pin net counts 1.2 times, a net on a bare die 1.5 times. Bare dies grow by the bond reach, 1.5 mm, on every side, so the bond fingers have room and nets are measured to where the fingers will be. The thermal term pushes hot parts apart in proportion to the product of their powers over their distance; the default power is 100 mW for a die. Parts land on a 50 µm grid with 0.3 mm clearance. Effort 1 takes about ten seconds on this design; the seed gives a different but repeatable result.');
  table(s, [['Setting', 'ALU8 value', 'Effect'], ['Grid', '50 µm', 'Every part lands on it'], ['Clearance', '0.3 mm', 'Space between footprints'], ['Bond reach', '1.5 mm', 'Room for the bond fingers'], ['Thermal', '1', 'Push hot dies apart (0 = off)'], ['Two-pin net weight', '× 1.2', 'Short point-to-point nets'], ['Die net weight', '× 1.5', 'Nets on bare dies count more'], ['Effort, seed', '1, 0', 'Search time; repeatable result']], 0.45, 1.3, 6.2, [1.8, 1.3, 3.1], 11.5, 0.38);
  const r = img(s, 'autoplace_window.png', 6.85, 1.3, 2.7, 2.6); cap(s, 'Tools → Auto Place…', r);
}
{
  const s = content('Straight-line interconnects', 'View → Straight-Line Interconnects shows every connection still to make', 'Module 10 · Auto Place',
    'The SLIs are the rat\'s nest: each net drawn along its shortest tree. Signal nets yellow, power orange, ground blue, and any single connection longer than 15 mm red and dashed. They follow a part as you drag it, and the status bar shows the number of nets and the total length, so every hand move shows at once whether it helped. Only what is still to route is drawn: once a net is routed, its lines disappear. A bonded die pad connects at its bond landing, not at the pad. View → Unrouted Nets… lists the nets still to route, longest first, which we use again tomorrow.');
  const r = img(s, 'alu8_autoplace_preview.png', 0.45, 1.3, 6.0, 3.0); cap(s, 'CP1 before (left) and after Auto Place (right), with the dies grouped by shared nets', r);
  bullets(s, [['Yellow', 'signal'], ['Orange', 'power'], ['Blue', 'ground'], ['Red dashed', 'longer than 15 mm'], ['Status bar', 'nets left and total length']], 6.75, 1.4, 2.8, 2.7, 13);
}
{
  const s = content('Making it better', 'Auto Place gives a good start; you finish it', 'Module 10 · Auto Place',
    'Three ways to improve a placement. Drag or rotate parts by hand while watching the SLI total. Run Improve, which refines the current placement and never accepts a worse total than it started from. Select a few parts and use Place Selection to move only those. Lock any part you do not want moved: HL_Fixed in the schematic or locked=1 in the overrides CSV. Every run is one undo step. For the ALU8 look for: U1 and U2 side by side (they share S0–S3, M and C4); U3 and U4 near the F outputs; U5 and U6 between the slices and the flag outputs.');
  bullets(s, [['Drag and rotate', 'watch the SLI total in the status bar.'], ['Improve', 'refines without scrambling; never makes it worse.'], ['Place Selection', 'moves only the selected parts.'], ['Lock', 'HL_Fixed, or locked=1 in the overrides CSV.'], ['Undo', 'each run is one step.']], 0.45, 1.4, 4.6, 3.3, 13.5);
  code(s, 'AUTOPLACE RUN alu8_die.net effort=1 seed=0\nSLI ON\nAUTOPLACE IMPROVE alu8_die.net\nAUTOPLACE WEIGHT C4 3\nUNROUTED', 5.3, 1.4, 4.25, 1.75, 11.5);
  T(s, 'Raising the weight of C4 pulls U1 and U2 together: the carry is the slowest path.', { x: 5.3, y: 3.35, w: 4.25, h: 0.7, fontSize: 12, color: MUTED });
}
lab(8, 'Place the ALU8', '11:45 · 45 min', 'Place the six dies from the netlist and get the shortest total length you can.', 'checkpoints\\CP1_dies_on_substrate.gds',
  ['Tools → Auto Place…; netlist alu8_die.net.', 'Check the Parts tab: every U maps to a DIE_ cell with family die.', 'Settings: grid 50 µm, clearance 0.3 mm, bond reach 1.5 mm, seed 0. Run.', 'Turn on SLIs; write down the total length.', 'Improve by hand or with Improve. Can you beat the first result?', 'Try weight 3 on C4 and run Improve. What moved?', 'Save as my_alu8_placed.gds.'],
  ['No overlaps, all dies inside the I/O ring', 'Total length written down', 'U1 and U2 side by side'], 'Lab 8',
  'Put a table on the whiteboard and collect each student\'s total length after the first run and after their improvements; it makes a good finish to the morning. If the Parts tab shows an unmapped part, the student opened alu8.net instead of alu8_die.net; module 4 explains why. If a run looks scrambled, check the seed and that they did not press Run twice with different settings.');
opener(11, 'Nets and wire bonds', '13:15 · 75 min with Lab 9', ['Load and check net names, including I/O pads', 'Draw, move and check wire bonds against the rules', 'Remove unused bonds and bond the package pins'], [3], 'die_bonded_zoom.png',
  'Bonding is where chip and wire differs most from a PCB. Take time on the bond rules and the reason for each.');
{
  const s = content('Net names', 'The router and every check need to know which pads belong together', 'Module 11 · Bonds',
    'Pads get names RefDes.Pin: U1.12 is die pad 12 of U1. Module I/O pads drawn directly in the design are IO.n, where n is the header pin. Auto Place sets the net names from the netlist; otherwise import a pad,net CSV with NETNAMES IMPORT or the Autoroute window\'s Import Net Names. In the ALU8, IO.18 is VCC and IO.3 is GND; the full list is in io_pinout.csv and in the handout. Check them with Edit → Highlight Net: click any pad and the status bar names its net.');
  bullets(s, [['RefDes.Pin', 'U1.12 = U1 die pad 12 (GND).'], ['IO.n', 'module I/O pad for header pin n (IO.18 = VCC).'], ['Set by', 'Auto Place, or NETNAMES IMPORT file.csv.'], ['Check with', 'Edit → Highlight Net, or HILITE U1.12.']], 0.45, 1.4, 4.6, 3.0, 14);
  table(s, [['pad', 'net'], ['IO.1', 'A1'], ['IO.2', 'V'], ['IO.3', 'GND'], ['IO.4', 'C'], ['IO.18', 'VCC'], ['U1.12', 'GND'], ['U1.16', 'C4']], 5.4, 1.4, 2.5, [1.25, 1.25], 12, 0.36);
  T(s, 'Excerpt of the ALU8 net names', { x: 5.4, y: 4.4, w: 4, h: 0.25, fontSize: 10, italic: true, color: MUTED });
}
{
  const s = content('Wire-bond rules and why', 'Checked live while you draw, and again by DRC', 'Module 11 · Bonds',
    'Explain each rule from the assembly point of view. Minimum length 1.0 mm: the shortest loop the bonder can form reliably. Maximum 2.5 mm: beyond that the wire sags or sweeps into its neighbour when the module is potted or vibrated. Maximum angle 45 degrees off the die-edge normal: a wire leaving at a steep angle crosses its neighbour or touches the die edge. In the finished ALU8 there are 94 bond ends on die pads and the steepest leaves at 29.3 degrees, comfortably inside the rule. The wire is 30 µm gold.');
  table(s, [['Rule', 'ALU8', 'Why'], ['Minimum length', '1.0 mm', 'Shortest loop the bonder forms reliably'], ['Maximum length', '2.5 mm', 'Longer wires sag or sweep into neighbours'], ['Maximum angle', '45° off the die-edge normal', 'Steep wires cross or touch the die edge'], ['Wire', '30 µm gold', 'Width drawn on layer 59']], 0.45, 1.3, 9.1, [2.0, 2.8, 4.3], 12.5, 0.45);
  s.addShape(pres.shapes.RECTANGLE, { x: 0.45, y: 3.75, w: 9.1, h: 0.9, fill: { color: PGRN }, line: { color: PGRN } });
  T(s, [{ text: 'In the finished ALU8: ', options: { bold: true, color: DIEL } }, { text: '94 die bonds, 1.65–1.89 mm long, steepest departure 29.3°. Every bond passes.', options: { color: INK } }], { x: 0.65, y: 3.95, w: 8.7, h: 0.5, fontSize: 14 });
}
{
  const s = content('Drawing and editing bonds', 'Create → Wire Bond, or BOND on the command bar', 'Module 11 · Bonds',
    'Demo on U3. Create → Wire Bond: click the die pad, then where the finger should be. The bond is drawn ball to ball on layer 59. Its substrate end becomes a bond finger when routing, so put the end where you want the finger. Edit → Move Wire Bond moves the end nearest your click; a live preview shows the span against the length rule before you commit. Edit → Delete Wire Bond removes the whole bond. Netlist extraction connects a bond only at its two ends, so it flies over anything in between without shorting. Moving bonds was covered in module 6; recap it here on a real die.');
  const r = img(s, 'die_bonded_zoom.png', 0.45, 1.3, 4.0, 3.55); cap(s, 'Bonds fanned out from U1 in CP2', r);
  bullets(s, [['Draw', 'Create → Wire Bond: click the die pad, then the landing.'], ['Move', 'Edit → Move Wire Bond moves the nearer end, with a live length check.'], ['Delete', 'Edit → Delete Wire Bond.'], ['Landing', 'becomes the bond finger the router connects to.'], ['Fly-over', 'a bond connects only at its two ends.']], 4.75, 1.35, 4.8, 2.6, 13);
  code(s, 'BOND 50970,-11800 52620,-11775', 4.75, 4.05, 4.8, 0.55, 11.5);
}
{
  const s = content('Unused bonds and the package', 'Clear the channels, then bond the header', 'Module 11 · Bonds',
    'Two tools. Remove Unused Bonds: a die part built with a bond on every pad carries bonds the design does not use; on the 54AS181, pads 14, 15 and 17 are unused, for example. They block routing channels for nothing. Tools → Remove Unused Bonds lists them by die and removes them, giving each changed die its own copy of the part named after it, which is why the finished design has DIE_54AS181_U1 and DIE_54AS181_U2. It needs net names, and should run before routing. Bond Header Pins: for each package pin with an I/O net it draws a 0.9 mm I/O pad on the 1st conductor at the nearest substrate edge and a header bond on layer 60 from the pin to it. Assign I/O Pads can choose which net goes on which pin for the shortest routing, but only when the pinout is free.');
  bullets(s, [['Remove Unused Bonds', 'Tools menu or UNUSEDBONDS REMOVE. Each changed die gets its own copy (DIE_54AS181_U1).'], ['Run it before routing', 'so the router can use the freed channels.'], ['Bond Header Pins', 'IOPADS BOND: an I/O pad at the substrate edge and a header bond per pin.'], ['Assign I/O Pads', 'IOPADS: picks the pinout with the shortest routing. Only if the pinout is yours to choose.']], 0.45, 1.4, 5.0, 3.5, 13);
  code(s, 'UNUSEDBONDS\nUNUSEDBONDS REMOVE\n\nIOPADS BOND all=1\nIOPADS            # plan only', 5.75, 1.4, 3.8, 1.9, 11.5);
}
lab(9, 'Bond the ALU8', '13:50 · 40 min', 'Check net names, practise bonding, remove unused bonds and bond the header.', 'checkpoints\\CP2_bonded_ready_to_route.gds',
  ['Edit → Highlight Net: check IO.18 is VCC and U1.16 is C4.', 'Zoom to U3. Delete one of its bonds, then redraw it with Create → Wire Bond.', 'Move a bond end with Edit → Move Wire Bond until it turns red; undo.', 'Run UNUSEDBONDS on the command bar. Which pads are listed?', 'Run Tools → Remove Unused Bonds…; note the new die cell names.', 'Hide all layers except 49, 59, 60: follow a header bond from a pin to its I/O pad.', 'Save as my_alu8_bonded.gds.'],
  ['Your redrawn bond is 1.0–2.5 mm', 'You can name the unused pads', 'Header bonds traced pin to pad'], 'Lab 9',
  'CP2 already has most bonds in place, so this lab is about understanding and editing rather than bonding 94 pads by hand. If UNUSEDBONDS lists nothing, unused bonds were already removed in this copy; the student should explain why that matters before routing instead. The red bond preview is the clearest way to show the length rule.');

// ---- Module 9
opener(12, 'Routing with Freerouting', '14:45 · 75 min with Lab 10', ['Set up the Autoroute window for a two-conductor chip-and-wire build', 'Route the ALU8 and read the log', 'Let HLayout optimise vias and tidy the tracks'], [4], 'stage3_bonded.png',
  'Routing takes the longest real time; set the run going and talk through the log while it works. Freerouting must be installed (the Java build); check this at the start of the day if not done already.');
{
  const s = content('How HLayout drives Freerouting', 'Freerouting is open source; HLayout prepares the job and checks the answer', 'Module 12 · Routing',
    'HLayout writes the board as a Specctra design file (.dsn), runs Freerouting in the background, reads the session file (.ses) back as one undo step, then checks every net. What it tells the router matters: your DRC rules set the widths and clearance; dies and their epoxy are keep-outs grown by the die keep-out; each bond landing becomes a bond finger on the conductor you choose; outside the printed dielectric only the 1st conductor may run. Use the Java build of Freerouting; the native CLI build crashes on multi-pass runs so HLayout limits it to one pass.');
  const bx = (x, t, sub, c) => { s.addShape(pres.shapes.RECTANGLE, { x, y: 1.5, w: 2.55, h: 1.4, fill: { color: c }, line: { color: c } }); T(s, t, { x: x + 0.15, y: 1.6, w: 2.3, h: 0.35, fontSize: 15, bold: true, color: WHITE }); T(s, sub, { x: x + 0.15, y: 2.0, w: 2.3, h: 0.85, fontSize: 11.5, color: SOFT }); };
  bx(0.45, 'HLayout', 'Writes TOP.dsn: board, rules, fingers, keep-outs', DIEL); bx(3.75, 'Freerouting', 'Routes both conductors, writes TOP.ses', GOLD); bx(7.0, 'HLayout', 'Imports tracks and vias, optimises, checks every net', DIEL);
  [3.0, 6.3].forEach(x => s.addShape(pres.shapes.LINE, { x: x + 0.02, y: 2.2, w: 0.7, h: 0, line: { color: INK, width: 2, endArrowType: 'triangle' } }));
  bullets(s, [['Your DRC wins:', 'clearance and widths are raised to your rules.'], ['Dies are keep-outs,', 'grown by the die keep-out (0.2 mm).'], ['Bond landings', 'become bond fingers on the conductor you choose.'], ['Off the dielectric,', 'only the 1st conductor may run.']], 0.45, 3.15, 9.1, 1.7, 13);
}
{
  const s = content('Autoroute settings for the ALU8', 'Tools → Autoroute (Freerouting)…; saved with the design', 'Module 12 · Routing',
    'Go through the settings that matter for the ALU8. Tracks 200 µm with 200 µm clearance, matching the DRC. Vias have 300 µm pads; the dielectric window is the pad plus the 0.1 mm via window margin on each side, 500 µm, and the router keeps other nets clear of the whole window. VCC and GND are power nets. Layers set to both conductors, and bond fingers on the 2nd conductor, which is the top, so fingers need no dielectric window. Track directions hv: 1st conductor horizontal, 2nd vertical; tracks change layer through a via to turn, which keeps the route tidy, and via optimisation takes out the vias it can afterwards. Via cost 35. Remove all existing tracks for a fresh start. 25 route and 25 optimise passes.');
  table(s, [['Setting', 'ALU8 value'], ['Signal / power width', '200 µm / 200 µm'], ['Clearance', '200 µm'], ['Via pad / window', '300 µm / 500 µm'], ['Power nets', 'VCC, GND'], ['Layers', 'Both conductors'], ['Bond fingers on', '2nd conductor, 200 µm'], ['Die keep-out', '200 µm'], ['Track directions', '1st horizontal, 2nd vertical'], ['90-degree routing only', 'On'], ['Via cost', '35'], ['Passes', '25 route, 25 optimise'], ['After the route', 'Via optimisation, shorten tracks'], ['Existing tracks', 'Remove all (fresh start)']], 0.45, 1.25, 5.6, [2.3, 3.3], 10.5, 0.27);
  bullets(s, [['Fingers on the 2nd conductor', 'sit on top: no dielectric window needed.'], ['Track directions', 'keep each conductor tidy; vias do the turns.'], ['Via optimisation', 'then removes every via it safely can.']], 6.35, 1.3, 3.2, 3.4, 12.5);
}
{
  const s = content('After the route', 'HLayout tidies the result automatically, one undo step each', 'Module 12 · Routing',
    'Three passes run after Freerouting when ticked. Via optimisation: for each via, if all the tracks on one side end at it and can move to the other conductor safely, they move and the via goes, along with its dielectric window. Every via is a window, a registration step and a reliability risk, so fewer is better. Shorten tracks: detours between two fixed points are re-laid the shortest clear way, a via counting as 1 mm of track. Remove jogs: short sideways steps up to 1 mm are lined up. All three keep clearance and stay on the dielectric. Then the dielectric fill is regenerated with a window at every remaining via.');
  bullets(s, [['Via optimisation', 'moves tracks to the other conductor to remove vias. FREEROUTE VIAOPT.'], ['Shorten tracks', 'straightens detours; a via counts as 1 mm. FREEROUTE SHORTEN.'], ['Remove jogs', 'lines up short side steps up to 1 mm. FREEROUTE DEJOG.'], ['Dielectric fill', 'regenerated with a window at each via.']], 0.45, 1.4, 4.7, 3.4, 13.5);
  const r = img(s, 'die_routed_zoom.png', 5.4, 1.3, 4.15, 3.55); cap(s, 'U1 after routing: 1st conductor orange, 2nd purple', r);
}
{
  const s = content('Reading the result', 'The log and the status bar tell you whether you are finished', 'Module 12 · Routing',
    'When the route finishes the Autoroute window comes back. The log reports tracks and vias imported, bond fingers laid, and Nets complete: N of M, with the names of any net still open. For the ALU8 the target is 46 of 46. Every run ends with a short check: any copper joining two named nets is reported. If nets are left, look at View → Unrouted Nets, and either route them by hand or adjust settings and route again with Keep existing tracks ticked. The finished reference design has all 46 nets complete and no shorts.');
  const r = img(s, 'stage4_routed.png', 0.45, 1.3, 5.6, 3.0); cap(s, 'The routed ALU8 (CP3): 46 of 46 nets, no shorts', r);
  s.addShape(pres.shapes.RECTANGLE, { x: 6.3, y: 1.3, w: 3.25, h: 3.1, fill: { color: PGRN }, line: { color: PGRN } });
  T(s, '46 / 46', { x: 6.5, y: 1.45, w: 3, h: 0.8, fontFace: 'Cambria', fontSize: 40, bold: true, color: DIEL });
  T(s, 'nets complete', { x: 6.5, y: 2.25, w: 3, h: 0.3, fontSize: 13 });
  T(s, '0', { x: 6.5, y: 2.75, w: 3, h: 0.7, fontFace: 'Cambria', fontSize: 36, bold: true, color: DIEL });
  T(s, 'short circuits (SHORTS)', { x: 6.5, y: 3.45, w: 3, h: 0.3, fontSize: 13 });
}
lab(10, 'Route the ALU8', '15:15 · 45 min', 'Route every net with Freerouting and get 46 of 46 complete.', 'my_alu8_bonded.gds, or checkpoints\\CP2_bonded_ready_to_route.gds',
  ['Tools → Autoroute (Freerouting)…; check the Launcher path.', 'Enter the ALU8 settings (handout table 10.1); tick Remove ALL existing tracks.', 'Press Route. Watch the percentage; the window returns when done.', 'Read the log: tracks, vias, fingers, Nets complete.', 'If nets remain: View → Unrouted Nets…; route again with Keep existing.', 'Count the vias before and after via optimisation.', 'Save as my_alu8_routed.gds.'],
  ['Nets complete: 46 of 46', 'SHORTS reports none', 'Saved my_alu8_routed.gds'], 'Lab 10',
  'Runs take a few minutes; use the time to go through the log on the projector. If Freerouting cannot be found, the Launcher field needs the freerouting.exe in its install folder or the .jar plus a Java 25 runtime. If a student gets fewer than 46 nets, the common causes are fingers set to the 1st conductor, or Remove ALL not ticked so an old partial route blocks channels. Anyone stuck can load CP3, which is the routed design, and carry on with Lab 11.');

// ---- Module 10
opener(13, 'Editing the route', '16:00 · 45 min with Lab 11', ['Find what is left to route with the Unrouted Nets list', 'Rip up and reroute a track by hand', 'Find shorts and redraw vias to the via definition'], [4], 'die_routed_zoom.png',
  'Real designs always need hand edits after autorouting. This module gives students the confidence to change a routed design without breaking it.');
{
  const s = content('Tools for routed designs', null, 'Module 13 · Editing',
    'Go through the tools in the order you use them. Unrouted Nets lists nets still open, longest first, and steps through them with Next and Prev, drawing the gap thick in magenta and zooming to it. Rip Up and Reroute: click two points on a net, the track between is cut out and you are left drawing from the first point with the second ringed as the target. Dragging a segment brings its vias and the meeting tracks with it. SHORTS lists any two nets joined by copper, which spacing DRC cannot see because touching copper merges into one shape. Fix Vias redraws every via to the one via definition after you change sizes. Every edit regenerates the dielectric about half a second after you stop.');
  table(s, [['Tool', 'Menu', 'Command'], ['Unrouted nets', 'View → Unrouted Nets…', 'UNROUTED, UNROUTED NEXT'], ['Rip up and reroute', 'Edit → Rip Up and Reroute', 'RIPUP x1,y1 x2,y2'], ['Move a segment', 'Drag its edge, or arrow keys', '–'], ['Find shorts', '–', 'SHORTS'], ['Redraw vias', 'Tools → Fix Vias…', 'VIA FIX'], ['Dielectric', 'Tools → Generate Dielectric Fill…', 'automatic after edits']], 0.45, 1.3, 9.1, [2.2, 3.7, 3.2], 12, 0.42);
  T(s, 'Spacing DRC cannot find shorts: touching copper merges into one shape. Use SHORTS.', { x: 0.45, y: 4.5, w: 9.1, h: 0.3, fontSize: 12.5, bold: true, color: GOLD });
}
lab(11, 'Break it and fix it', '16:15 · 30 min', 'Rip up a net, find it again with the tools and route it back by hand.', 'my_alu8_routed.gds, or checkpoints\\CP3_routed.gds',
  ['HILITE C4, then HILITE OFF.', 'Edit → Rip Up and Reroute: cut C4 between U1 and U2.', 'View → Unrouted Nets…: is C4 listed? How long is the gap?', 'Route C4 back by hand on either conductor; Space drops a via.', 'Drag a segment of another net; watch the via follow.', 'Run SHORTS. Fix anything reported with Rip Up and Reroute.', 'Check SLIs: 46 complete, total 0.'],
  ['C4 complete again', 'SHORTS reports none', 'Unrouted Nets list empty'], 'Lab 11',
  'C4 is a good choice because it is short and between the two main dies. Some students will route onto the wrong layer at a crossing and create a short; that is a useful mistake, as SHORTS then finds it. Remind them that Ctrl+Z undoes a whole rip-up.');

// =============================================================================
// ---- Module 11
{
  const s = content('Day 2 review', 'Four questions before we go', 'Day 2 review',
    'Answers: 1: alu8_die.net, because the die cells are numbered by die pad. 2: so the router can use the channels round the die; they are pads with no net. 3: fingers on the 2nd conductor sit on top and need no dielectric window. 4: touching copper merges into one shape, so there is no gap for spacing DRC to measure; use SHORTS. Preview day 3: verification, outputs, linking 2D cells to 3D models, documentation, automation and the capstone.');
  const q = ['Which netlist file does Auto Place need for the ALU8 dies, and why?', 'Why remove unused bonds before routing?', 'Why put the bond fingers on the 2nd conductor?', 'Why can spacing DRC not find a short circuit?'];
  q.forEach((t, i) => { badge(s, String(i + 1), 0.45, 1.4 + i * 0.8, GOLD); T(s, t, { x: 1.05, y: 1.45 + i * 0.8, w: 8.4, h: 0.5, fontSize: 15 }); });
}

divider('DAY 3', 'Checks, outputs and 3D', 'Verification, manufacturing outputs, linking 2D cells to 3D STEP models, documentation, automation and the capstone.',
  'DAY 3 opens. Everyone starts from CP3, the routed ALU8, so the room is in step. Morning: check it and make the outputs, then link the cells to 3D models. Afternoon: documentation, automation, then the capstone and the quiz.');

opener(14, 'Verification', '09:00 · 75 min with Lab 12', ['Run DRC and fix the violations it finds', 'Check wire bonds against length and angle rules', 'Prove connectivity and estimate die temperatures'], [5], 'drc_zoom.png',
  'Day 3 opens with verification. Verification is where the design becomes trustworthy. CP3 contains two real DRC violations; finding and fixing them is the core of Lab 12.');
{
  const s = content('DRC on the ALU8', 'Tools → DRC… → Run, with markers on layer 6', 'Module 14 · Verification',
    'Run DRC on CP3 live. It reports two violations, both of rule 1st con metal_2_metal: two pieces of 1st-conductor copper closer than 200 µm, at the same place just left of U3, around x 55.1 mm, y −7.8 mm. The marker is drawn on layer 6 at the gap. Each rule has its own colour in the rule table and in the violation list. Click a violation to zoom to it. The fix is usually to move the short diagonal segment or rip it up and reroute. After fixing, run DRC again until it reports none.');
  const r = img(s, 'drc_zoom.png', 0.45, 1.3, 4.6, 3.6); cap(s, 'The violation in CP3, ringed: 1st-conductor spacing below 200 µm', r);
  s.addShape(pres.shapes.RECTANGLE, { x: 5.35, y: 1.3, w: 4.2, h: 1.35, fill: { color: PGOLD }, line: { color: PGOLD } });
  T(s, '2 violations', { x: 5.55, y: 1.4, w: 3.9, h: 0.6, fontFace: 'Cambria', fontSize: 28, bold: true, color: GOLD });
  T(s, 'both 1st con metal_2_metal, near x 55.1, y −7.8 mm', { x: 5.55, y: 2.05, w: 3.9, h: 0.5, fontSize: 12 });
  bullets(s, [['Run', 'DRC RUN error=6 draws markers on layer 6.'], ['Find', 'click a violation to zoom to it.'], ['Fix', 'move or rip up the segment, then run again.'], ['Target', 'no violations.']], 5.35, 2.85, 4.2, 2.0, 13);
}
{
  const s = content('Bonds and connectivity', 'Two checks DRC on its own cannot give you', 'Module 14 · Verification',
    'Bond checks: the three bond rules in DRC check length and departure angle; s.bond_departures() lists every die-pad end with its angle. The ALU8 has 94 ends, steepest 29.3 degrees. Connectivity: Tools → Extract Netlist rebuilds the nets from the copper and compares them with the net names. Tick the two conductors as Conductor, and 58 and 49 as Isolated, and keep Include wire bonds on. The target is no opens and no shorts. Export the result as CSV, KiCad or Tango for the test station. SLIs are a quick cross-check: complete 46 and total length 0.');
  bullets(s, [['Bond rules', 'length 1.0–2.5 mm and angle ≤ 45° in DRC.'], ['ALU8 result', '94 die-pad bond ends, steepest 29.3°.'], ['Extract Netlist', 'conductors 3 and 5; isolated 58 and 49; include wire bonds.'], ['Target', 'no opens, no shorts.'], ['Quick check', 'SLIs: 46 complete, total 0.']], 0.45, 1.4, 5.0, 3.5, 13.5);
  code(s, 'DRC RUN error=6\nNETLIST RUN 3 5 bonds=1\nSHORTS\n\n# Python\nends = s.bond_departures()\nprint(len(ends),\n      max(e["angle_deg"] for e in ends))\n# 94 29.3', 5.75, 1.4, 3.8, 3.2, 11.5);
}
{
  const s = content('Thermal analysis', 'Tools → Thermal Analysis… estimates every die temperature from the layout', 'Module 14 · Verification',
    'The model is the substrate and every printed film as a stack of slices on a grid, with heat injected where each part sits. Set a power for each die in the Powers tab; values here are representative for the exercise, not datasheet figures: 400 mW for each 54AS181, 15–40 mW for the gates, 900 mW in all. With the base held at 25 °C, as if clamped to a perfect heatsink, the hottest die reaches only 26 °C. With the base cooled through a heat-transfer coefficient of 200 W/m²K, U1 reaches 34 °C. Both are far below the 125 °C die limit. The point: the boundary condition you choose matters more than anything else. The model leaves out the package, wire bonds and airflow, so treat it as a design-stage estimate.');
  const r = img(s, 'thermal_top.png', 0.45, 1.3, 5.3, 2.2); cap(s, 'Temperature rise with the base cooled at 200 W/m²K: U1 and U2 hottest', r);
  table(s, [['Die', 'Power', 'Fixed base', 'Cooled base'], ['U1 54AS181', '400 mW', '26.0 °C', '34.0 °C'], ['U2 54AS181', '400 mW', '26.0 °C', '33.9 °C'], ['U5 54LS86', '40 mW', '25.2 °C', '28.7 °C'], ['U3, U4, U6', '15–25 mW', '25.1 °C', '28.0–28.8 °C']], 5.95, 1.3, 3.6, [1.15, 0.8, 0.8, 0.85], 10.5, 0.4);
  T(s, 'Powers are representative values for this exercise. Limit: 125 °C per die.', { x: 0.45, y: 4.1, w: 9.1, h: 0.3, fontSize: 11, italic: true, color: MUTED });
}
lab(12, 'Prove the design is right', '09:35 · 40 min', 'Clear the DRC, check the bonds and connectivity, and estimate die temperatures.', 'checkpoints\\CP3_routed.gds',
  ['Tools → DRC… → Run. How many violations, and which rule?', 'Zoom to them; fix by moving or rerouting the segment. Run DRC again.', 'Tools → Extract Netlist…: conductors 3, 5; isolated 58, 49; include bonds. Run.', 'Run SHORTS and check SLIs show 46 complete.', 'Tools → Thermal Analysis…: set U1, U2 400 mW; U3 25, U4 15, U5 40, U6 20 mW. Run.', 'Change the base to cooled (200 W/m²K) and run again. Compare.', 'Save as my_alu8_checked.gds.'],
  ['DRC: no violations', 'No opens, no shorts', 'U1 about 34 °C with a cooled base'], 'Lab 12',
  'The two DRC violations are at the same spot just left of U3. Students sometimes "fix" them by shrinking the rule; stop that and discuss why the rule exists. Thermal results depend slightly on grid size; anything between 33 and 35 °C for U1 with the cooled base is right. Ask: what would raise U1 a lot? Answer: a poor die attach or no heatsink, which the boundary condition stands in for.');

// ---- Module 12
opener(15, 'Manufacturing outputs', '10:15 · 45 min with Lab 13', ['Produce the print artwork as Gerber and DXF export sets', 'Write the pick-and-place file and the bond list for the bonder', 'Export the test netlist and archive the job'], [6], 'stage4_routed.png',
  'Every output comes from the checked design and goes to one step of the build. Emphasise that outputs are only made after verification passes.');
{
  const s = content('The ALU8 output set', 'Each file feeds one step of the build', 'Module 15 · Outputs',
    'Walk through the table from print line to test. Gerber: one RS-274X file per print layer, named prefix_Lnn_layer.gbr; text is not exported. The ALU8 design has saved export sets named 1-CON, 2-CON and 1-DIE. DXF: the same layers merged as fired, in millimetres, for laser trim, scribing and mechanical drawings. Pick and place: each die\'s centroid and rotation from the substrate corner, as CSV or KiCad .pos; dies are kind die. Bond list for the bonder: a Freerouting design keeps no bond diagram, so we write the bond list from Python with s.bond_departures(): every die bond with both ends, its length and departure angle; the script is in the handout. HYBRID BONDS and BONDTABLE work on designs from the HLayout chip-and-wire router, which keeps a full bond diagram in bonding order. Test netlist from Extract Netlist. Archive Design bundles the design, technology and libraries for the job record.');
  table(s, [['Output', 'Menu or command', 'Goes to'], ['Gerber RS-274X', 'File → Gerber Export Sets… · GERBER prefix', 'Screens and photo-tools'], ['DXF', 'File → DXF Export Sets…', 'Laser trim, scribing, mechanical'], ['Pick and place', 'File → Export Pick and Place… · PNP EXPORT', 'Die bonder placement'], ['Bond list', 'Python: s.bond_departures() to CSV', 'Wire bonder'], ['Test netlist', 'Extract Netlist → Export…', 'Substrate and module test'], ['Archive', 'File → Archive Design', 'Job record']], 0.45, 1.3, 9.1, [1.8, 4.2, 3.1], 11.5, 0.42);
  code(s, 'GERBER out\\alu8\nPNP EXPORT out\\alu8_pnp.csv\nPNP EXPORT out\\alu8.pos origin=design', 0.45, 4.35, 9.1, 0.75, 11);
}
lab(13, 'Make the output set', '10:35 · 25 min', 'Produce every file the line, the bonder and the tester need.', 'my_alu8_checked.gds, or CP3 with the DRC fixed',
  ['File → Gerber Export Sets…: run set 1-CON into an out folder.', 'How many .gbr files? Which layers?', 'File → DXF Export Sets…: export the conductors as DXF.', 'File → Export Pick and Place… as CSV. Check U1–U6 are kind die.', 'Run the bond-list script from the handout: out\\alu8_bonds.csv, 94 rows.', 'Extract Netlist → Export… as CSV.', 'File → Archive Design.'],
  ['One Gerber per print layer', 'Six dies in the pick-and-place file', 'Bond list: 94 bonds'], 'Lab 13',
  'Have students open one Gerber in any free Gerber viewer if available. The bond list has 94 rows: U1 and U2 21 each, U3 and U5 14, U6 13, U4 11. Lengths run from 1.65 to 1.89 mm. HYBRID BONDS would report no bond diagram here, because that comes only from the chip-and-wire router. Check pick-and-place rotation: dies placed rotated show their angle counter-clockwise from above.');

// ---- Module 13
// ---- Module 16: linking 2D cells to 3D STEP models
opener(16, 'Linking 2D cells to 3D STEP models', '11:15 · 75 min with Lab 14', ['Explain how each film and part becomes a 3D solid', 'Tie a 2D cell to a STEP model, automatically or by hand', 'Set rotation, height and the vertical reference so parts sit right'], [6], 'alu8_3d_die.png',
  'This module explains the step from the flat layout to the 3D model, which students used without explanation in earlier courses. The key idea is that a 2D cell and its STEP model share one origin; everything else is rotation and height.');
{
  const s = content('From 2D to 3D', 'Films are extruded from the layout; parts are replaced by STEP bodies or boxes', 'Module 16 · 3D linking',
    'Two kinds of 3D object. Films: every printed layer with something drawn becomes a solid at its fired thickness from the Thicknesses tab, stacked in print order on the substrate slab; the dielectric uses only its generated fill, and keep-out layers are never exported. Wire bonds become loops landing on the actual surface under each end. Parts: every placed cell instance becomes either its STEP model or, without one, a box of its footprint at a set height (a die is 0.38 mm unless told otherwise). A part rests on whatever film is under its footprint, so a die sits on its attach pad, not floating at the top of the stack. A package that encloses the substrate, like the Kovar DIP40, is referenced to the substrate underside so the substrate sits in its well.');
  bullets(s, [['Films', 'each drawn layer extruded at its fired thickness, in print order.'], ['Dielectric', 'only the generated fill (datatype 1).'], ['Bonds', 'gold loops landing on the surface under each end.'], ['Parts', 'STEP model, or a box of the footprint (die: 0.38 mm).'], ['Resting height', 'on the film under the part.'], ['Packages', 'from the substrate underside: the substrate sits in the well.']], 0.45, 1.35, 4.8, 3.6, 12.5);
  const r = img(s, 'alu8_3d.png', 5.5, 1.3, 4.05, 3.45); cap(s, 'ALU8: films, dies, bonds and the DIP40 STEP model', r);
}
{
  const s = content('The link: one origin', 'A 2D cell and its STEP model must share the same origin', 'Module 16 · 3D linking',
    'The rule that makes the link work: the STEP model\'s origin is the 2D cell\'s origin. HLayout places the model at the instance origin, turns it with the instance rotation, then applies the model\'s own rotation correction. So when you draw a footprint, decide where its origin is, for example pin 1 or the body centre, and build or ask for the STEP model on the same point. Z = 0 is the surface the part sits on for normal parts; for a header or bathtub package z = 0 is the well floor, so it uses the substrate-bottom reference. The KOVAR_DIP40 cell and its STEP model both have their origin at the body\'s bottom-left corner. A mirrored instance falls back to a box.');
  const ox = 0.9, oy = 1.6;
  s.addShape(pres.shapes.RECTANGLE, { x: ox, y: oy, w: 2.6, h: 1.6, fill: { color: PGOLD }, line: { color: GOLD, width: 1.5 } });
  T(s, '2D cell', { x: ox + 0.15, y: oy + 0.1, w: 2, h: 0.3, fontSize: 13, bold: true, color: GOLD });
  s.addShape(pres.shapes.RECTANGLE, { x: ox + 3.4, y: oy - 0.2, w: 2.6, h: 1.6, fill: { color: PGRN }, line: { color: DIEL, width: 1.5 } });
  s.addShape(pres.shapes.RECTANGLE, { x: ox + 3.6, y: oy, w: 2.6, h: 1.6, fill: { color: 'C8E6DB' }, line: { color: DIEL, width: 1.5 } });
  T(s, 'STEP model', { x: ox + 3.75, y: oy + 0.1, w: 2, h: 0.3, fontSize: 13, bold: true, color: DIEL });
  [[ox, oy + 1.6], [ox + 3.6, oy + 1.6]].forEach(p => { s.addShape(pres.shapes.OVAL, { x: p[0] - 0.09, y: p[1] - 0.09, w: 0.18, h: 0.18, fill: { color: RED }, line: { color: RED } }); });
  T(s, 'origin (0, 0)', { x: ox - 0.1, y: oy + 1.75, w: 1.6, h: 0.3, fontSize: 11, color: RED });
  T(s, 'origin (0, 0, z = 0)', { x: ox + 3.5, y: oy + 1.75, w: 2.2, h: 0.3, fontSize: 11, color: RED });
  T(s, '=', { x: ox + 2.75, y: oy + 0.5, w: 0.6, h: 0.6, fontSize: 30, bold: true, align: 'center' });
  bullets(s, [['Same origin', 'model placed at the instance origin.'], ['Rotation', 'instance rotation, then the model\'s own correction.'], ['z = 0', 'the surface the part sits on; for headers, the well floor.'], ['Mirrored', 'instances fall back to a box.']], 0.45, 3.85, 9.1, 1.2, 12.5);
}
{
  const s = content('Component models', 'Each linked cell has an entry in the design\'s component models', 'Module 16 · 3D linking',
    'The link is stored per cell name in the design technology, under component models. Mode is model or box. Model path is the STEP file; a relative path is taken from the design\'s folder, so keep models beside the project if the design moves between machines: the ALU8 example uses an absolute C:/dev path, which breaks on another PC. Height is used for a box, or when the model needs a height. Z offset shifts the part up or down. Rotation corrects a model drawn on a different axis. Z reference is surface, resting on the film under it, or substrate bottom for headers. The ALU8 KOVAR_DIP40 entry is shown.');
  table(s, [['Field', 'Meaning', 'ALU8 KOVAR_DIP40'], ['mode', 'model (STEP) or box', 'model'], ['model_path', 'STEP file; relative to the design folder', 'C:/dev/HLayout/examples/step_models/kovar_dip/KOVAR_DIP40_0.9in.step'], ['rotation_deg', 'Correction if the model\'s axes differ', '0'], ['z_ref', 'surface, or substrate_bottom for headers', 'substrate_bottom'], ['z_offset_um', 'Shift up or down', '0'], ['height_um', 'Box height, or model height hint', '1000']], 0.45, 1.3, 9.1, [1.6, 3.5, 4.0], 11.5, 0.45);
  T(s, 'Tip: use relative paths and keep STEP files with the project, or the link breaks on another PC.', { x: 0.45, y: 4.55, w: 9.1, h: 0.35, fontSize: 12.5, bold: true, color: GOLD });
}
{
  const s = content('Automatic matching and the Library Manager', 'File → Component Library Manager… shows and overrides every link', 'Module 16 · 3D linking',
    'Before every 3D export, any placed cell with no manual link is matched against the STEP library. Passives are matched by an EIA size code in the cell name, 0201 to 2512, so CAP_0805 finds CAP_0805.step. Semiconductors and multi-lead packages, the SOT family and the 20-pin flatpack, are matched by their pad footprint: pin count and lead pitch, with an automatic rotation if the pitch lies on the other axis. A manual link always wins. A match whose STEP file is not built yet stays a flat box. A preview lists every match and miss before export. The Component Library Manager has one row per placed cell with its STEP file, rotation and source, manual or auto. Set STEP File links one by hand, Clear Override goes back to automatic, Refresh rematches after you build more models.');
  const r = img(s, 'component_library_manager.png', 0.45, 1.3, 5.0, 2.6); cap(s, 'File → Component Library Manager…', r);
  bullets(s, [['Passives', 'by EIA code in the cell name (CAP_0805).'], ['Packages', 'by pin count and pitch; rotation found for you.'], ['Manual wins', 'Set STEP File…; Clear Override to undo.'], ['Missing STEP', 'drawn as a flat box, never a broken link.']], 5.7, 1.35, 3.85, 3.0, 12.5);
}
{
  const s = content('Building the STEP library', 'One shared library folder, built once, never overwritten', 'Module 16 · 3D linking',
    'Two ways to get models. File → Build 3D Component Library writes a FreeCAD macro that builds generic chip capacitors, the SOT family and a 20-pin flatpack into one shared folder, set once in Setup → 3D Component Library Folder; it never overwrites an existing STEP file, so a vendor model you drop in survives. The build_STEP_parts folder holds a script per family, including the chip-and-wire caps, the LM741 die, the metal headers and the Kovar DIPs; build_all.py runs them all through FreeCADCmd, or with CadQuery for the Kovar packages. For your own parts: put the model origin on the cell origin, z = 0 on the seating surface, and check it once in the Library Manager and in 3D.');
  code(s, 'python build_STEP_parts\\build_all.py              # into C:\\step_models\npython build_STEP_parts\\build_all.py --clean      # rebuild everything\npython build_STEP_parts\\build_all.py --only kovar_dip --out D:\\models', 0.45, 1.35, 9.1, 1.1, 11);
  table(s, [['Folder', 'Models'], ['chip_wire', 'CAP_0201 … CAP_2512 on epoxy, LM741 die, 20/30/40_PIN_pkg headers'], ['kovar_dip', 'KOVAR_DIP20/30/40'], ['ceramic_chip_caps, sot, lcc, kyocera, tantalum, substrates', 'Generic packaged parts']], 0.45, 2.7, 9.1, [3.6, 5.5], 11.5, 0.4);
  T(s, 'Your own models: origin on the cell origin, z = 0 on the seating surface.', { x: 0.45, y: 4.5, w: 9.1, h: 0.35, fontSize: 12.5, bold: true, color: GOLD });
}
lab(14, 'Link 2D cells to 3D models', '11:50 · 40 min', 'Check the package link, add a part that links automatically, and break and fix a link.', 'checkpoints\\CP3_routed.gds, saved as my_alu8_3d.gds',
  ['File → Component Library Manager…: which file is KOVAR_DIP40 linked to? Manual or auto?', 'Setup → Attach Library…: hlayout\\examples\\chip_wire.gds. Place CAP_0805 near U1 as C1 (a decoupling capacitor).', 'Refresh the Library Manager: does C1 match CAP_0805.step automatically?', 'Set a rotation correction of 90° on CAP_0805 and export to FreeCAD. What happens? Clear Override.', 'Copy KOVAR_DIP40_0.9in.step next to your design and Set STEP File… to it; note the path is now relative.', 'Export to FreeCAD and check C1 sits on its pads and the substrate sits in the package well.'],
  ['C1 auto-matched to CAP_0805.step', 'Package linked with a relative path', 'Model looks right in FreeCAD'], 'Lab 14',
  'The STEP models must exist: examples\\step_models\\chip_wire and kovar_dip ship with the source; if not, run build_all.py before the course. With a 90° correction the capacitor body turns across its pads, which is the quickest way to show what the correction does. If FreeCAD is not installed, use File → 3D View in SolidCraft, or show it on the projector.');

opener(17, 'Documentation and the 3D view', '13:15 · 45 min with Lab 15', ['Draw a hidden-line top view of the assembly', 'Build the assembly drawing in one step', 'Export the fired stack and parts to FreeCAD as a 3D model'], [6], 'alu8_3d.png',
  'Back from lunch: the assembly drawing and running the 3D export that module 16 set up.');
{
  const s = content('Top view and assembly drawing', 'Tools → Assembly Drawing… builds the whole sheet; Build again after any change', 'Module 17 · Docs and 3D',
    'Tools → Top View draws the design as seen from above in true print order, with hidden lines removed and the visible conductor cross-hatched. Tools → Assembly Drawing does the whole sheet in one step: an ISO A3 or A4 sheet with a title block, the scaled top view, the pin function table, the resistor table if there are printed resistors, and the bond table when the design carries a bond diagram from the chip-and-wire router; the ALU8 has neither, so its sheet shows the top view and the pin table. Every setting is saved with the design, so after moving parts press Build again. Note: the ALU8 example still has the title "4 bit ALU" in its title block; fixing it to "8-bit ALU" is part of Lab 15.');
  const r = img(s, 'alu8_assembly.png', 0.45, 1.25, 3.0, 3.75); cap(s, 'ALU8 assembly drawing, A3 portrait', r);
  bullets(s, [['Sheet', 'A3 or A4 with a title block: title, drawing number, issue, drawn/checked/approved.'], ['Top view', 'scaled automatically (100:1 to 1:10), hatched conductor.'], ['Tables', 'pin functions; resistors and bonds when the design has them.'], ['Rebuild', 'press Build again after any change; it is one undo step.']], 3.8, 1.35, 5.75, 3.0, 13.5);
  code(s, 'ASSEMBLY size=A3 orientation=portrait title="8-bit ALU" dwgno=ALU8-001', 3.8, 4.35, 5.75, 0.55, 10.5);
}
{
  const s = content('The 3D model', 'File → Export to FreeCAD (3D Macro)… or File → 3D View in SolidCraft…', 'Module 17 · Docs and 3D',
    'The FreeCAD export writes a macro that rebuilds the design as solids: one per printed film at its fired thickness, each die as a body resting on its film, wire bonds as raised loops, and the package from its STEP model. Parts are linked to STEP files as set up in module 16. For the ALU8 the KOVAR_DIP40 is tied to its STEP model and the substrate sits inside it. The SolidCraft view does the same through its MCP server and writes STEP, 3MF, STL or OBJ for the package and enclosure designers.');
  const a = img(s, 'alu8_3d.png', 0.45, 1.25, 4.4, 3.55); cap(s, 'ALU8 in its Kovar DIP40', a);
  const b = img(s, 'alu8_3d_die.png', 5.1, 1.25, 4.45, 3.1); cap(s, 'A 54AS181 die with its bonds', b);
}
lab(15, 'Draw it and see it in 3D', '13:35 · 25 min', 'Build the assembly drawing and the 3D model of your ALU8.', 'my_alu8_checked.gds, or CP3',
  ['Tools → Top View…: build it on layer 56; look at the hatching.', 'Tools → Assembly Drawing…: A3 portrait; title "8-bit ALU", drawing no. ALU8-001, your initials.', 'Pin functions: 18 = VCC, 3 = GND (see handout for all pins).', 'Build, then Print Preview.', 'File → Export to FreeCAD…; run the macro in FreeCAD and orbit the model.'],
  ['Title reads 8-bit ALU', 'Assembly sheet with top view and pin table', 'ALU8 opens in FreeCAD'], 'Lab 15',
  'FreeCAD must be installed for the last step; if not, show it on the projector. The 3D export needs the STEP models built with build_STEP_parts\\build_all.py or a model path set in the Component Library Manager. The title fix is a good habit to call out: the assembly drawing is a controlled document.');

// ---- Module 14
opener(18, 'Automation and support', '14:00 · 30 min with Lab 16', ['Run a whole flow from the command bar or a Python script', 'Let Claude drive HLayout through the MCP server', 'Report a bug with everything needed to fix it'], [0, 1, 2, 3, 4, 5, 6], 'main_alu16.png',
  'A forward-looking finish. Most users will start with the command bar; a few will script whole builds like example 4 and 5 do.');
{
  const s = content('Scripting the flow', 'Anything you can click, you can type or script', 'Module 18 · Automation',
    'Show a short script that runs placement, routing, checks and outputs in one go. The command bar is good for repeatable steps; Python gives full control. Examples 4 and 5 build their designs entirely by script: build_alu8_dies.py is the ALU8 one. The User Manual chapter 19 is a cookbook of tested Python examples, and Help → Command Reference (F1) lists every command and method.');
  code(s, 'from hlayout.scripting.api import Session\ns = Session.open_design("CP2_bonded_ready_to_route.gds")\n\ns.freeroute(remove_existing=True)   # uses the saved Autoroute settings\nprint(s.sli()["complete"], "of 46 nets")\n\nv = s.run_drc()\nprint(len(v), "DRC violations")\n\ns.export_gerber("out/alu8")\ns.export_pick_and_place("out/alu8_pnp.csv")\ns.generate_assembly_drawing(size="A3", title="8-bit ALU")\ns.save_design("alu8_scripted.gds")', 0.45, 1.3, 9.1, 3.3, 11.5);
  T(s, 'Run it with Tools → Run Script File…, or line by line in the Console tab.', { x: 0.45, y: 4.7, w: 9.1, h: 0.3, fontSize: 12, color: MUTED });
}
{
  const s = content('Claude and Report Bug', null, 'Module 18 · Automation',
    'Tools → MCP Server (Claude) starts a bridge that lets Claude drive the open window: run commands, run Python, inspect cells; edits go into the normal undo history. It listens only on this computer and each run uses a new token. Help → Report Bug, new in 0.6, records the errors, recent commands and status messages of the session, and builds one zip with versions and design state; you preview everything, then save it or open a pre-filled GitHub issue. Nothing is uploaded automatically. When an unexpected error appears, HLayout offers to report it with the details filled in.');
  const card = (x, t, items, c) => { s.addShape(pres.shapes.RECTANGLE, { x, y: 1.3, w: 4.45, h: 3.5, fill: { color: ALU }, line: { color: ALU } }); s.addShape(pres.shapes.RECTANGLE, { x: x + 0.2, y: 1.5, w: 0.25, h: 0.25, fill: { color: c }, line: { color: c } }); T(s, t, { x: x + 0.6, y: 1.43, w: 3.6, h: 0.4, fontSize: 16, bold: true }); bullets(s, items, x + 0.2, 2.0, 4.05, 2.7, 12.5); };
  card(0.45, 'MCP server (Claude)', [['Start', 'Tools → MCP Server (Claude).'], ['Claude can', 'run commands and Python, inspect cells.'], ['Safe', 'local only, new token each run, undoable.']], DIEL);
  card(5.1, 'Help → Report Bug', [['Records', 'errors, commands, status messages this session.'], ['Bundles', 'versions, design state, optional design and screenshot.'], ['You decide', 'preview, save the zip, or open a GitHub issue.']], GOLD);
}
lab(16, 'Optional: script the build', '14:10 · 20 min', 'Run routing, checks and outputs from one script.', 'checkpoints\\CP2_bonded_ready_to_route.gds',
  ['Open the Console tab with CP2 loaded.', 'On the command bar run FREEROUTE RUN fresh=1 fingerlayer=2.', 'In the console print s.sli()["complete"].', 'Run len(s.run_drc()).', 'Copy the script from the Scripting the flow slide into a .py file; run it with Tools → Run Script File….', 'Open Help → Report Bug… and press Preview to see what it would send.'],
  ['46 of 46 from the script', 'Outputs written to out\\', 'You have seen a bug report preview'], 'Lab 16',
  'Optional lab for fast groups. The script needs Freerouting configured from Lab 10. If time is short, demo it on the projector instead.');

// ---- Wrap-up
{
  const s = pres.addSlide(); s.background = { color: WHITE };
  s.addShape(pres.shapes.RECTANGLE, { x: 0, y: 0, w: 10, h: 1.15, fill: { color: INK }, line: { color: INK } });
  T(s, 'CAPSTONE  ·  14:30 · 105 min', { x: 0.45, y: 0.2, w: 6, h: 0.3, fontSize: 11, bold: true, color: GOLDL, charSpacing: 1.5 });
  T(s, 'The ALU8 on your own, with an engineering change', { x: 0.45, y: 0.48, w: 9.1, h: 0.55, fontFace: 'Cambria', fontSize: 23, bold: true, color: WHITE });
  T(s, [{ text: 'Brief  ', options: { bold: true, color: GOLD } }, { text: 'Starting from CP1, take the ALU8 through the whole flow without step-by-step help. Engineering change: U4\'s five unused inputs (pads 4, 5, 6, 11, 12) are no longer tied straight to VCC; they go to VCC through a printed 1 kΩ ±5 % pull-up, R1.', options: { color: INK } }], { x: 0.45, y: 1.3, w: 9.1, h: 0.8, fontSize: 12.5 });
  const runs = ['Size R1 in the Resistor Designer (25 mW) and place it near U4.', 'New net PU: U4.4, .5, .6, .11, .12 and R1.2; R1.1 joins VCC. Update the net names (NETNAMES IMPORT).', 'Auto Place, bond, remove unused bonds, route: 47 of 47 nets.', 'DRC clean, no shorts, dielectric keep-out round R1, thermal check.', 'Outputs, assembly drawing titled 8-bit ALU, 3D model with R1 visible.'].map((t, i, a) => ({ text: t, options: { bullet: { type: 'number' }, color: '33404F', breakLine: i < a.length - 1 } }));
  T(s, runs, { x: 0.45, y: 2.2, w: 5.9, h: 2.8, fontSize: 12.5, paraSpaceAfter: 5 });
  s.addShape(pres.shapes.RECTANGLE, { x: 6.6, y: 2.2, w: 2.95, h: 2.75, fill: { color: PGRN }, line: { color: PGRN } });
  T(s, 'Hand in', { x: 6.78, y: 2.32, w: 2.6, h: 0.3, fontSize: 12, bold: true, color: DIEL });
  T(s, ['Your .gds and .hlaux', 'Gerber and DXF set', 'Pick and place, bond list', 'Assembly drawing PDF', 'Screenshot of the 3D model'].map((t, i, a) => ({ text: t, options: { bullet: true, color: INK, breakLine: i < a.length - 1 } })), { x: 6.78, y: 2.67, w: 2.65, h: 2.2, fontSize: 11.5, paraSpaceAfter: 4 });
  footer(s, 'Capstone');
  s.addNotes('CAPSTONE (14:30 to 16:15, with the afternoon break taken when convenient). Students work alone or in pairs; you circulate and only help when someone is stuck for more than ten minutes. R1 sizing answer: 25 mW needs 1.0 mm (31.3 mW after trim); aim 800 Ω; 1 kΩ/□ ink, 0.80 squares, 0.80 mm long. The netlist change adds one net, PU, so routing reports 47 nets. The keep-out round R1 lands on the dielectric layer and the regenerated fill opens over it. Check each hand-in against the list. Strong finishers can add the decoupling capacitor from Lab 14 to the netlist too. The capstone files are not marked; it is practice for the quiz and for real work.');
}

{
  const s = content('What you built', 'The ALU8, stage by stage', 'Wrap-up',
    'Show the progression and go back to the flipchart list from the start of day 1: did everyone get what they came for? Each picture is a checkpoint they have produced themselves: dies on the substrate, placed and bonded in the package, routed and checked, and the 3D model.');
  const pics = [['stage1_dies.png', 'Dies on the substrate'], ['stage3_bonded.png', 'Placed and bonded'], ['stage4_routed.png', 'Routed and checked'], ['alu8_3d.png', '3D model']];
  pics.forEach((p, i) => { const r = img(s, p[0], 0.45 + i * 2.3, 1.5, 2.1, 2.5); T(s, p[1], { x: 0.45 + i * 2.3, y: 4.15, w: 2.1, h: 0.3, fontSize: 12, bold: true, align: 'center' }); });
}
{
  const s = pres.addSlide(); s.background = { color: INK }; n++;
  s.addImage({ path: IMG('hlayout_splash.png'), x: 8.55, y: 0.4, w: 1.0, h: 1.0 });
  T(s, 'Next steps', { x: 0.6, y: 0.6, w: 7, h: 0.7, fontFace: 'Cambria', fontSize: 32, bold: true, color: WHITE });
  const items = [['Assessment', '20-question quiz in your handout; 16 or more to pass.'], ['Feedback', 'the form in the back of your handout.'], ['Practise', 'redo the ALU8 from CP1 on your own, then your own design.'], ['Read', 'User Manual chapters 21–23 and the Python cookbook (chapter 19).'], ['Get help', 'Help → Report Bug, GitHub issues, support@hlayout.co.uk']];
  items.forEach((it, i) => { T(s, [{ text: it[0] + '   ', options: { bold: true, color: GOLDL } }, { text: it[1], options: { color: WHITE } }], { x: 0.6, y: 1.55 + i * 0.6, w: 8.6, h: 0.45, fontSize: 15 }); });
  T(s, 'Thank you', { x: 0.6, y: 4.75, w: 5, h: 0.4, fontSize: 14, color: SOFT });
  s.addNotes('Hand out the quiz (in the student handout) and give 20 minutes. The answer key is in the trainer guide. Collect feedback forms. Point to the support routes: Help → Report Bug, GitHub issues, and support@hlayout.co.uk for consultancy and customisation. Thank everyone.');
}

pres.writeFile({ fileName: 'HLayout_Training_ALU8.pptx' }).then(f => console.log('wrote', f, n, 'slides'));
