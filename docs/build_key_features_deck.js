const pptxgen = require('pptxgenjs');
const pres = new pptxgen();
pres.layout = 'LAYOUT_16x9';
pres.title = 'HLayout — Key Features';
pres.author = 'HLayout';
pres.theme = { headFontFace: 'Cambria', bodyFontFace: 'Calibri' };

// Palette: fired alumina, thick-film gold conductor, dielectric green, ink navy
const INK='15233B', GOLD='C9A227', DIEL='2E7D6B', ALU='F3F5F7', WHITE='FFFFFF', MUTED='5B6B7F', LINE='D5DBE3', PALEGOLD='F6EDCF', PALEGRN='DCEEE8';
const W=10, H=5.625;
const sh = () => ({ type:'outer', color:'000000', blur:6, offset:2, angle:90, opacity:0.18 });

function titleSlide(s, t, sub){
  s.background = { color: WHITE };
  s.addText(t, { x:0.5, y:0.3, w:9, h:0.65, fontFace:'Cambria', fontSize:30, bold:true, color:INK, margin:0, isTextBox:true });
  if (sub) s.addText(sub, { x:0.5, y:0.95, w:9, h:0.4, fontSize:14, color:MUTED, margin:0, isTextBox:true });
  s.addImage({ path:'hlayout_splash.png', x:0.5, y:5.2, w:0.3, h:0.3 });
  s.addText('HLayout · Thick-film hybrid layout CAD', { x:0.88, y:5.25, w:6, h:0.25, fontSize:9, color:MUTED, margin:0, isTextBox:true });
}
function pageNum(s, n){ s.addText(String(n), { x:9.0, y:5.25, w:0.5, h:0.25, fontSize:9, color:MUTED, align:'right', margin:0, isTextBox:true }); }
function img(s, path, x, y, maxW, maxH, pw, ph){
  const r = pw/ph; let w=maxW, h=maxW/r; if (h>maxH){ h=maxH; w=maxH*r; }
  const ox = x + (maxW-w)/2, oy = y + (maxH-h)/2;
  s.addShape(pres.shapes.RECTANGLE, { x:ox-0.06, y:oy-0.06, w:w+0.12, h:h+0.12, fill:{color:WHITE}, line:{color:LINE, width:0.75}, shadow:sh() });
  s.addImage({ path, x:ox, y:oy, w, h });
  return {ox,oy,w,h};
}
function caption(s, t, x, y, w){ s.addText(t, { x, y, w, h:0.3, fontSize:10, italic:true, color:MUTED, margin:0, isTextBox:true }); }
// numbered badge = motif (a bond-finger square)
function badge(s, label, x, y, fill, col){
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w:0.42, h:0.42, rectRadius:0.06, fill:{color:fill}, line:{color:fill} });
  s.addText(label, { x, y, w:0.42, h:0.42, fontSize:12, bold:true, color:col||WHITE, align:'center', valign:'middle', margin:0, isTextBox:true });
}
function rows(s, items, x, y, w, rowH, fill){
  items.forEach((it,i)=>{
    const yy = y + i*rowH;
    badge(s, it[0], x, yy, fill||DIEL);
    s.addText(it[1], { x:x+0.6, y:yy-0.04, w:w-0.6, h:0.28, fontSize:14, bold:true, color:INK, margin:0, isTextBox:true });
    s.addText(it[2], { x:x+0.6, y:yy+0.24, w:w-0.6, h:rowH-0.3, fontSize:11.5, color:MUTED, margin:0, valign:'top', isTextBox:true });
  });
}

// 1 Title
{ const s = pres.addSlide(); s.background = { color: INK };
  s.addImage({ path:'hlayout_splash.png', x:0.5, y:0.35, w:1.05, h:1.05, rounding:false });
  s.addText('HLayout', { x:0.5, y:1.55, w:4.6, h:0.9, fontFace:'Cambria', fontSize:48, bold:true, color:WHITE, margin:0, isTextBox:true });
  s.addText('Thick-film hybrid layout CAD', { x:0.5, y:2.4, w:4.6, h:0.45, fontSize:20, color:GOLD, margin:0, isTextBox:true });
  s.addText('From a KiCad schematic to print artwork, bond diagrams, assembly drawings and 3D models — on an open-source toolchain.', { x:0.5, y:3.0, w:4.3, h:1.0, fontSize:14, color:'C9D3E0', margin:0, valign:'top', isTextBox:true });
  s.addText('Key features overview', { x:0.5, y:4.8, w:4, h:0.3, fontSize:11, color:'8FA0B5', margin:0, isTextBox:true });
  img(s, 'main_alu16.png', 5.3, 1.05, 4.3, 3.5, 1500, 950);
  s.addNotes('HLayout is a CAD tool for thick-film hybrid circuits: printed conductors, dielectric, resistor inks and glaze on a ceramic substrate, with packaged parts or bare dies. The screenshot is the ALU16 module from example 2.');
}

// 2 At a glance
{ const s = pres.addSlide(); titleSlide(s, 'Built for hybrids, not PCBs', 'One GDSII-native design database driven by mouse, commands or Python'); pageNum(s,2);
  const stats = [['64','independent layers, each with colour, fill and name'],['1 µm','database unit — external GDSII libraries drop straight in'],['500','mm square maximum design area, hierarchical cells'],['3','ways to drive it: canvas, command bar, Python / MCP']];
  stats.forEach((st,i)=>{ const x=0.5+i*2.3;
    s.addShape(pres.shapes.RECTANGLE, { x, y:1.65, w:2.05, h:2.0, fill:{color:ALU}, line:{color:ALU} });
    s.addText(st[0], { x:x+0.15, y:1.8, w:1.75, h:0.8, fontFace:'Cambria', fontSize:36, bold:true, color:DIEL, margin:0, isTextBox:true });
    s.addText(st[1], { x:x+0.15, y:2.65, w:1.75, h:0.9, fontSize:12, color:INK, margin:0, valign:'top', isTextBox:true });
  });
  s.addText([
    {text:'Thick-film aware: ', options:{bold:true, color:INK}},
    {text:'resistor designer and laser-trim simulator, dielectric fill with via windows, wire bonds, chip-and-wire dies, thermal analysis and process-order top views.', options:{color:MUTED}}
  ], { x:0.5, y:4.0, w:9, h:0.8, fontSize:14, margin:0, valign:'top', isTextBox:true });
  s.addNotes('Every action is available three ways: click it, type it on the command bar, or script it through the Session object. The MCP server lets Claude drive the same session.');
}

// 3 Open-source workflow diagram
{ const s = pres.addSlide(); titleSlide(s, 'An open-source workflow, end to end', 'Seven stages; only schematic capture and the machines sit outside HLayout'); pageNum(s,3);
  const st = [
    ['1','Schematic','KiCad Eeschema',true],
    ['2','Tech & parts','Layers, libraries, dies, resistors',false],
    ['3','Placement','Auto Place from the netlist',false],
    ['4','Nets & bonds','Net names, wire bonds',false],
    ['5','Routing','Freerouting / hybrid router',true],
    ['6','Verify','DRC, netlist, thermal, trim',false],
    ['7','Outputs','CAM, docs, 3D (FreeCAD)',true],
  ];
  const bw=1.17, gap=0.16, y=1.75;
  st.forEach((t,i)=>{ const x=0.5+i*(bw+gap);
    const ext = t[3];
    s.addShape(pres.shapes.RECTANGLE, { x, y, w:bw, h:1.75, fill:{color: ext?PALEGOLD:PALEGRN}, line:{color: ext?GOLD:DIEL, width:1} });
    badge(s, t[0], x+0.1, y+0.12, ext?GOLD:DIEL, ext?INK:WHITE);
    s.addText(t[1], { x:x+0.1, y:y+0.65, w:bw-0.2, h:0.35, fontSize:12.5, bold:true, color:INK, margin:0, isTextBox:true });
    s.addText(t[2], { x:x+0.1, y:y+1.0, w:bw-0.2, h:0.7, fontSize:10.5, color:MUTED, margin:0, valign:'top', isTextBox:true });
    if (i<st.length-1) s.addShape(pres.shapes.RIGHT_TRIANGLE ? pres.shapes.ISOSCELES_TRIANGLE : pres.shapes.RECTANGLE, { x:x+bw+0.02, y:y+0.8, w:0.12, h:0.16, rotate:90, fill:{color:MUTED}, line:{color:MUTED} });
  });
  // legend
  s.addShape(pres.shapes.RECTANGLE, { x:0.5, y:3.75, w:0.22, h:0.22, fill:{color:PALEGOLD}, line:{color:GOLD} });
  s.addText('Open-source tool in the loop (KiCad, Freerouting, FreeCAD)', { x:0.8, y:3.72, w:4.3, h:0.28, fontSize:11, color:INK, margin:0, isTextBox:true });
  s.addShape(pres.shapes.RECTANGLE, { x:5.2, y:3.75, w:0.22, h:0.22, fill:{color:PALEGRN}, line:{color:DIEL} });
  s.addText('Done inside HLayout', { x:5.5, y:3.72, w:3, h:0.28, fontSize:11, color:INK, margin:0, isTextBox:true });
  s.addText([
    {text:'Open formats at every hand-off: ', options:{bold:true, color:INK}},
    {text:'KiCad .net · GDSII · Specctra DSN/SES · Gerber RS-274X · DXF · STEP · CSV / KiCad .pos', options:{color:MUTED}}
  ], { x:0.5, y:4.3, w:9, h:0.6, fontSize:13, margin:0, valign:'top', isTextBox:true });
  s.addNotes('Freerouting and the hybrid router are called from inside HLayout and their results come straight back into the design as one undo step. HLayout itself is built on open-source Python libraries: gdstk, shapely, numpy and scipy.');
}

// 4 Three build paths
{ const s = pres.addSlide(); titleSlide(s, 'Three ways to build a hybrid', 'Same seven stages; parts, bonds and routing differ'); pageNum(s,4);
  const p = [
    ['A','Packaged parts, multilayer','LCC carriers, SMD caps and SOT packages on a two-conductor stack. Freerouting routes both conductors with vias through the dielectric.','Example 2 — ALU16, ten LCCs'],
    ['B','Chip and wire, two conductors','Bare dies on die-attach pads, wire-bonded to bond fingers, with printed resistors and chip caps. Fingers laid on the 2nd conductor.','Example 4 — 85 of 85 nets, no shorts or opens'],
    ['C','Chip and wire, one conductor','One printed conductor, no dielectric. Wire-bond jumpers carry one track over another; Freerouting single-layer or the hybrid router.','Notch filter example'],
  ];
  p.forEach((c,i)=>{ const x=0.5+i*3.07, y=1.6, w=2.86, h=2.8;
    s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill:{color:WHITE}, line:{color:LINE, width:0.75}, shadow:sh() });
    badge(s, c[0], x+0.2, y+0.2, i===0?INK:(i===1?DIEL:GOLD), i===2?INK:WHITE);
    s.addText(c[1], { x:x+0.75, y:y+0.18, w:w-0.9, h:0.5, fontSize:13.5, bold:true, color:INK, margin:0, valign:'middle', isTextBox:true });
    s.addText(c[2], { x:x+0.2, y:y+0.85, w:w-0.4, h:1.3, fontSize:12, color:MUTED, margin:0, valign:'top', isTextBox:true });
    s.addText(c[3], { x:x+0.2, y:y+2.2, w:w-0.4, h:0.45, fontSize:11, italic:true, color:DIEL, margin:0, valign:'top', isTextBox:true });
  });
  s.addNotes('Pick the path from the parts list and the process before placing anything; it decides which layers carry which pads.');
}

// 5 Schematic capture & placement
{ const s = pres.addSlide(); titleSlide(s, 'Schematic capture to placement', 'KiCad netlist in, parts placed for the shortest weighted net length'); pageNum(s,5);
  rows(s, [
    ['1','Capture in KiCad','Reference designators, pin or die-pad numbers, VCC/GND nets. Export the .net — or a pad,net CSV or Tango file.'],
    ['2','Carry HLayout data in the schematic','HL_Family, HL_Cell, HL_Power_mW, HL_Weight, HL_Fixed fields — or an overrides CSV per job.'],
    ['3','Auto Place','Weighted nets including power and ground, 50 µm grid, thermal term keeps hot parts apart. ALU16: ten LCCs in ~10 s.'],
    ['4','Straight-line interconnects','Live ratsnest that follows every drag; Unrouted Nets list steps through what is left.'],
  ], 0.5, 1.6, 4.7, 0.88);
  const r = img(s, 'autoplace_sli.png', 5.6, 1.5, 3.9, 3.15, 1100, 1000);
  caption(s, 'ALU16 placed from its KiCad netlist, SLIs shown', r.ox, r.oy+r.h+0.1, r.w);
  s.addNotes('Footprints map to cells automatically: same RefDes, HL_Cell field, saved map, same name, or package letters plus pin count. Printed resistors in the netlist are sized by the Resistor Designer and placed like any other part.');
}

// 6 Layout tools
{ const s = pres.addSlide(); titleSlide(s, 'Layout tools made for thick film', 'Everything a fired-film stack needs, in one editor'); pageNum(s,6);
  const g = [
    ['Technology setup','Print-order layers, route layers, wire-bond and dielectric-fill rules saved as a reusable tech file.'],
    ['Parts libraries','Attached GDS libraries, Via and Pad Wizards, Chip Wizard for bare dies with numbered pads.'],
    ['Resistor Designer','Sizes printed resistors from value, tolerance, power and ink; places them as cells.'],
    ['Trim Simulator','Plunge and L-cut laser trim with live resistance, sensitivity and heat map.'],
    ['Dielectric fill','Generated sheet inside the I/O ring with a window at every via.'],
    ['Mask operations','Oversize, undersize and boolean layer ops, run singly or as saved batches.'],
  ];
  g.forEach((c,i)=>{ const col=i%3, row=Math.floor(i/3); const x=0.5+col*3.07, y=1.6+row*1.72, w=2.86, h=1.52;
    s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill:{color:ALU}, line:{color:ALU} });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:x+0.2, y:y+0.22, w:0.26, h:0.26, rectRadius:0.04, fill:{color:i%2?DIEL:GOLD}, line:{color:i%2?DIEL:GOLD} });
    s.addText(c[0], { x:x+0.6, y:y+0.17, w:w-0.75, h:0.36, fontSize:14, bold:true, color:INK, margin:0, isTextBox:true });
    s.addText(c[1], { x:x+0.2, y:y+0.62, w:w-0.4, h:0.82, fontSize:11.5, color:MUTED, margin:0, valign:'top', isTextBox:true });
  });
  s.addNotes('All dialogs have command-bar and Python equivalents, and every operation is a single undo step.');
}

// 7 Routing
{ const s = pres.addSlide(); titleSlide(s, 'Autorouting with Freerouting', 'Open-source router for multilayer and chip-and-wire hybrids'); pageNum(s,7);
  const r = img(s, 'toolchain_example4.png', 0.5, 1.55, 5.0, 2.6, 1800, 900);
  caption(s, 'Example 4: die U2 with wire bonds fanning out to bond fingers', r.ox, r.oy+r.h+0.1, r.w);
  s.addText('85/85', { x:5.9, y:1.5, w:3.6, h:0.75, fontFace:'Cambria', fontSize:40, bold:true, color:DIEL, margin:0, isTextBox:true });
  s.addText('nets routed on the ALU16 chip-and-wire build, no shorts or opens', { x:5.9, y:2.25, w:3.6, h:0.5, fontSize:12, color:MUTED, margin:0, valign:'top', isTextBox:true });
  s.addText([
    {text:'Your DRC rules set the router’s widths and clearance', options:{bullet:true, breakLine:true}},
    {text:'Wire-bond landings become bond fingers automatically', options:{bullet:true, breakLine:true}},
    {text:'Hybrid router: one conductor with wire-bond jumpers', options:{bullet:true, breakLine:true}},
    {text:'Via optimisation, track shortening, rip-up and reroute', options:{bullet:true}},
  ], { x:5.9, y:2.95, w:3.6, h:1.9, fontSize:12.5, color:INK, margin:0, valign:'top', paraSpaceAfter:5, isTextBox:true });
  s.addNotes('HLayout writes a Specctra .dsn, Freerouting routes it and writes a .ses, and HLayout reads tracks and vias back as one undo step, then checks every net. The hybrid router (a Freerouting fork, HybridRouterCli) adds crossovers, jumpers and the bond diagram.');
}

// 8 Verification
{ const s = pres.addSlide(); titleSlide(s, 'Verify before you print', 'Rules, connectivity, heat and trim checked in the same tool'); pageNum(s,8);
  rows(s, [
    ['DRC','Design Rule Check','Width, area, spacing, enclosure, substrate-edge clearance and wire-bond length and departure angle.'],
    ['NL','Netlist extraction','Rebuilds connectivity from copper and bonds; compares with net names for opens and shorts.'],
    ['°C','Thermal analysis','Steady-state conduction through substrate and films; banded maps and an HTML report.'],
    ['Ω','Trim simulation','Confirms each printed resistor can be trimmed to value.'],
  ], 0.5, 1.6, 5.3, 0.86, INK);
  s.addShape(pres.shapes.RECTANGLE, { x:6.3, y:1.6, w:3.2, h:3.2, fill:{color:PALEGRN}, line:{color:PALEGRN} });
  s.addText('171', { x:6.55, y:1.8, w:2.8, h:0.75, fontFace:'Cambria', fontSize:40, bold:true, color:DIEL, margin:0, isTextBox:true });
  s.addText('die-pad bond ends checked on example 4', { x:6.55, y:2.55, w:2.8, h:0.5, fontSize:12, color:INK, margin:0, valign:'top', isTextBox:true });
  s.addText('33.4°', { x:6.55, y:3.2, w:2.8, h:0.75, fontFace:'Cambria', fontSize:40, bold:true, color:DIEL, margin:0, isTextBox:true });
  s.addText('steepest departure — passes the 45° bond-angle rule', { x:6.55, y:3.95, w:2.8, h:0.6, fontSize:12, color:INK, margin:0, valign:'top', isTextBox:true });
  s.addNotes('Thermal analysis is a design-stage estimate: checked against closed-form cases, not yet against FEA or measured hybrids.');
}

// 9 CAM outputs
{ const s = pres.addSlide(); titleSlide(s, 'CAM outputs for every machine', 'Each file is made from the checked design and feeds one step of the build'); pageNum(s,9);
  const t = [
    ['Gerber RS-274X','One file per print layer','Screens and photo-tools'],
    ['DXF','Merged as-fired geometry, mm','Laser trim, scribing, mechanical'],
    ['Pick and place','Centroid + rotation, CSV or KiCad .pos','Placement machine'],
    ['Bond diagram','Every wire in bonding order, length, angle','Wire bonder'],
    ['Test netlist','CSV, KiCad or Tango','Bare-substrate and module test'],
    ['Archive','Design, technology and libraries','Job record'],
  ];
  const hdr = ['Output','What it holds','Goes to'].map(h=>({text:h, options:{bold:true, color:WHITE, fill:{color:INK}, fontSize:12.5}}));
  const body = t.map((r,i)=> r.map((c,j)=>({text:c, options:{fontSize:12, color: j===0?INK:MUTED, bold:j===0, fill:{color: i%2?WHITE:ALU}}})));
  s.addTable([hdr, ...body], { x:0.5, y:1.55, w:9, colW:[2.4,3.8,2.8], rowH:0.48, border:{type:'solid', pt:0.5, color:LINE}, margin:[0,0.12,0,0.12], valign:'middle' });
  s.addNotes('Gerber and DXF export run as named, repeatable export sets. DXF bakes path widths into closed polygons and merges touching shapes, matching the fired film.');
}

// 10 Documentation
{ const s = pres.addSlide(); titleSlide(s, 'Assembly documentation in one step', 'Tools → Assembly Drawing builds the whole sheet and rebuilds it on demand'); pageNum(s,10);
  const r = img(s, 'topview_result.png', 0.6, 1.5, 3.4, 3.35, 700, 700);
  caption(s, 'Hidden-line top view, conductor hatched', r.ox, r.oy+r.h+0.1, 3.4);
  rows(s, [
    ['TV','Top view','Hidden-line pictorial view in true process order, scaled 100:1 to 1:10 automatically.'],
    ['RT','Resistor table','Ref, value, tolerance, aim value and ink in RKM code, read from the Resistor Designer.'],
    ['PT','Pin and bond tables','Pinout table per package; bond table added for chip-and-wire builds.'],
    ['SH','ISO drawing sheet','A4 or A3 with a full title block; house style remembered between designs.'],
  ], 4.5, 1.6, 5.0, 0.86, GOLD);
  s.addNotes('Each part is its own regenerable cell (ASSY_SHEET, ASSY_TOPVIEW, ASSY_PINTABLE, ASSY_RESTABLE). After moving parts or editing resistors, press Build again. Print renders a true-scale HTML/SVG page.');
}

// 11 3D view
{ const s = pres.addSlide(); titleSlide(s, 'See it in 3D', 'The fired-film stack and every component, as real solids'); pageNum(s,11);
  const r = img(s, 'freecad_3d_iso.png', 0.5, 1.5, 5.2, 3.2, 1800, 1200);
  caption(s, 'ALU16 rebuilt in FreeCAD from the exported macro', r.ox, r.oy+r.h+0.1, r.w);
  s.addText([
    {text:'FreeCAD macro: ', options:{bold:true, color:INK}}, {text:'one solid per film at its fired thickness; wire bonds as raised loops', options:{color:MUTED, breakLine:true}},
    {text:'STEP library: ', options:{bold:true, color:INK}}, {text:'chip caps 0201–2512, SOT family, LCC, Kovar headers, bare dies — built by script', options:{color:MUTED, breakLine:true}},
    {text:'Auto-match: ', options:{bold:true, color:INK}}, {text:'parts tied to STEP bodies by size code or pad footprint; overrides in the Component Library Manager', options:{color:MUTED, breakLine:true}},
    {text:'SolidCraft: ', options:{bold:true, color:INK}}, {text:'3D view through its MCP server; writes STEP, 3MF, STL or OBJ', options:{color:MUTED}},
  ], { x:6.05, y:1.55, w:3.45, h:3.4, fontSize:12, margin:0, valign:'top', paraSpaceAfter:9, isTextBox:true });
  s.addNotes('Components rest on whatever film lies under their footprint, so a die sits on its conductor rather than floating. Headers can be referenced to the substrate underside. build_STEP_parts/build_all.py rebuilds every family.');
}


// 11b ALU8 example: layout and corresponding 3D
{ const s = pres.addSlide(); titleSlide(s, 'Example 5: ALU8, layout to 3D', 'Six bare dies wire-bonded on two conductors, sealed in a 40-pin Kovar DIP'); pageNum(s,12);
  const a = img(s, 'alu8_layout.png', 0.5, 1.5, 4.4, 3.2, 1430, 731);
  caption(s, 'Layout: 1st conductor orange, 2nd purple, dies bonded to fingers', a.ox, a.oy+a.h+0.12, a.w);
  const b = img(s, 'alu8_3d.png', 5.15, 1.45, 4.35, 3.35, 1629, 1200);
  caption(s, 'The corresponding 3D view, exported to FreeCAD', b.ox, b.oy+b.h+0.1, b.w);
  s.addNotes('Example 5 is an 8-bit ALU: two 54AS181 slices and four 54LS gate dies, 46 nets, placed by Auto Place and routed with Freerouting with bond fingers on the 2nd conductor. The 3D view is the same design exported to FreeCAD: fired films at real thickness, dies on attach pads, substrate seated in the Kovar DIP40 with glass-sealed pins.');
}

// 12 Automation
{ const s = pres.addSlide(); titleSlide(s, 'Scriptable, and Claude-ready', 'Anything you can click you can type, script or hand to an AI assistant'); pageNum(s,13);
  rows(s, [
    ['>_','Command bar','Short AutoCAD-style language: AUTOPLACE, DRC RUN, GERBER, ASSEMBLY, PNP EXPORT.'],
    ['Py','Python Session API','Full scripting of cells, layers, routing, DRC, thermal and export; a tested cookbook in the manual.'],
    ['AI','MCP server','Claude drives a live window (edits land in its undo history) or a headless session.'],
  ], 0.5, 1.6, 4.5, 1.05, DIEL);
  s.addShape(pres.shapes.RECTANGLE, { x:5.3, y:1.6, w:4.2, h:3.2, fill:{color:INK}, line:{color:INK} });
  s.addText(
    'AUTOPLACE RUN alu16.net effort=1\nDRC RUN error=60\nGERBER out\\alu16\nPNP EXPORT alu16.pos\nASSEMBLY size=A3 title="ALU16"\n\n# Python\nr = s.generate_assembly_drawing(\n      size="A3", title="ALU16")',
    { x:5.5, y:1.75, w:3.9, h:2.95, fontFace:'Courier New', fontSize:11.5, color:'E6EDF5', margin:0, valign:'top', isTextBox:true });
  s.addNotes('The MCP bridge is off until enabled, listens on 127.0.0.1 only, and every request carries a random per-run token.');
}

// 13 Close
{ const s = pres.addSlide(); s.background = { color: INK };
  s.addImage({ path:'hlayout_splash.png', x:8.45, y:0.45, w:1.05, h:1.05 });
  s.addText('One design. Every output.', { x:0.5, y:0.9, w:7.8, h:0.8, fontFace:'Cambria', fontSize:36, bold:true, color:WHITE, margin:0, isTextBox:true });
  const c = [['Schematic','KiCad netlist'],['Layout','Placement, routing'],['CAM','Gerber, DXF, PnP, bonds'],['Docs','Assembly drawing'],['3D','FreeCAD, STEP, SolidCraft']];
  c.forEach((t,i)=>{ const x=0.5+i*1.84;
    s.addShape(pres.shapes.RECTANGLE, { x, y:2.0, w:1.66, h:1.3, fill:{color:'1F3150'}, line:{color:'2C4366'} });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x:x+0.15, y:2.15, w:0.24, h:0.24, rectRadius:0.04, fill:{color:i%2?DIEL:GOLD}, line:{color:i%2?DIEL:GOLD} });
    s.addText(t[0], { x:x+0.15, y:2.5, w:1.4, h:0.3, fontSize:14, bold:true, color:WHITE, margin:0, isTextBox:true });
    s.addText(t[1], { x:x+0.15, y:2.82, w:1.4, h:0.45, fontSize:10.5, color:'C9D3E0', margin:0, valign:'top', isTextBox:true });
  });
  s.addText('Get started', { x:0.5, y:3.75, w:4, h:0.35, fontSize:14, bold:true, color:GOLD, margin:0, isTextBox:true });
  s.addText('pip install -r requirements.txt\npython hlayout_app.py example2\\alu16_MCM.gds', { x:0.5, y:4.1, w:6, h:0.65, fontFace:'Courier New', fontSize:12, color:'E6EDF5', margin:0, valign:'top', isTextBox:true });
  s.addText('Full guide: docs\\HLayout_User_Manual.pdf · Help → Command Reference (F1)', { x:0.5, y:4.95, w:9, h:0.3, fontSize:11, color:'8FA0B5', margin:0, isTextBox:true });
  s.addNotes('Recap: the same design database feeds every downstream output, and the open toolchain (KiCad, Freerouting, FreeCAD) means no proprietary licences in the loop.');
}

pres.writeFile({ fileName: 'HLayout_Key_Features.pptx' }).then(f=>console.log('wrote', f));
