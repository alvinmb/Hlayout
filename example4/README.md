# Example 4 -- ALU16 MCM, chip-and-wire

The 16-bit ALU module from example2, rebuilt as a chip-and-wire hybrid: the
ten LCC carriers (U1-U4 54AS181, U5 54S182, U6-U10 54LS27/27/30/86/00) are
replaced by bare dies and gold wire bonds.

| File | What |
|---|---|
| alu16_chipwire.gds (+ .hlaux) | the design, bond fingers on the 1st conductor |
| alu16_chipwire_l5.gds (+ .hlaux) | the current design: bond fingers, die-attach pads and cap pads on the 2nd (top) conductor |
| build_alu16_chipwire.py | rebuilds it from ../example2/alu16_MCM.gds and re-routes (`--out <file.gds>`, `--fingers-1st`) |
| alu16_net_names.csv | pad -> net map, die pads keyed by die pad number (U1.12 = GND) |
| alu16_netlist/ | the ALU16 netlist (alu16.net, alu16_netlist.csv, alu16_netlist.txt) |
| freerouting/ | the DSN / SES of the routing run |
| alu16_chipwire_preview.png | quick preview plot |

What was done
- The LCCs, the old routing, the vias and the dielectric are removed. The
  caps, the 68 I/O pads and the substrate outline are kept.
- Die cells DIE_54AS181, DIE_54S182 and DIE_54LS27/30/86/00 have their pads on
  layer 58, epoxy on layer 20 and the outline on layer 61. They sit where the
  LCCs were, with the same rotation.
- Die pads are numbered by the DIP / die pad number (the dip_die_pad column
  in alu16_netlist.csv). Their order around each die follows the DIP pin
  order, so the bonds fan out without crossing.
- There are 171 bonds on layer 59 (25 um wire), one for every connected die
  pad. Unconnected pads (for example 54AS181 pad 14) are not bonded.
- Wire Bond Rules: die pad layer is 58.
- Every die sits on a 2nd-conductor die-attach pad 200 um larger than the chip
  all round (Chip Wizard / create_chip_cell(attach_pad_layer=, attach_pad_margin=)).
  The router keeps tracks 200 um clear of it.
- The chip capacitors' (CAP_0805) pads are on the 2nd conductor.
- Bond fingers: Tools > Autoroute (Freerouting)... > "Bond fingers on:" picks the
  1st or 2nd conductor (rule `finger_layer`; script `FREEROUTE RUN fingerlayer=2`).
  On the 2nd (top) conductor the bond lands on exposed copper, with no
  dielectric window needed. This forces a two-layer route.
- Freerouting chip-and-wire run: 2 layers (3 and 5) with 300 um signal tracks,
  400 um power tracks and 500 um bond fingers. Result: 85/85 nets complete,
  no shorts and no opens (stack-aware net tracer).
- DRC: 4 places at 279-296 um against the 300 um spacing rule. These are
  square path corners, the same kind as example2's 68.

Assumption: TI does not publish die drawings for these parts, so the die
sizes are representative only: 54AS181 3.0 x 3.0 mm, 54S182 2.0 x 2.0 mm,
54LSxx 1.5 x 1.5 mm. Swap in real die cells and pad positions before using
this for anything real.
