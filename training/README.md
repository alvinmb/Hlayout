# HLayout in Practice: three-day training course

Built around the ALU8 chip-and-wire example (8-bit ALU, six bare dies, Kovar DIP40).

| File | What it is |
|---|---|
| `HLayout_Training_ALU8.pptx` | Trainer deck, 104 slides with speaker notes |
| `HLayout_Training_ALU8_Student_Handout.docx` | 16 labs (Lab 7: Via, Pad and Chip Wizards), capstone, quick reference, glossary, quiz |
| `HLayout_Training_ALU8_Trainer_Guide.docx` | Setup checklist, timing, checkpoint map, answer keys, known issues |
| `checkpoints/` | Lab start files (CP0–CP3, netlists, technology file, my_parts.gds for the wizards lab). Copy each `.gds` with its `.hlaux` |
| `source/` | Generators: `npm i pptxgenjs docx`, then `node build_deck.js` and `node build_handouts.js` |

Day 1 foundations (shapes, SRef/ARef, technology, mask operations and dielectric, thick-film resistors) ·
Day 2 netlist to routed design · Day 3 verification, outputs, 2D-to-3D STEP linking, capstone.

Needs HLayout 0.7, Freerouting (Java build) and FreeCAD for the 3D labs.
