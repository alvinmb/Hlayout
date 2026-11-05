# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""DRC demo/validation for HLayout using the 2-Layer_solder_Build tech file.

Checks performed:
  1. MIN_SPACE >= 0.3mm on the 1st Conductor layer (layer 2).
  2. MIN_SPACE >= 0.3mm on the 2nd Conductor layer (layer 4).
  3. Resistor geometry DRC (hlayout.core.resistor_drc) against the tech
     file's resistor_geometry_rules, run over every resistor cell in
     the library.

This script builds a small synthetic design with both passing and
deliberately-failing geometry, to prove each check actually fires.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hlayout.core.library import Library
from hlayout.core.units import mm_to_dbu
from hlayout.core.drc import DrcRule, run_drc
from hlayout.core.resistor_drc import check_all_resistors
from hlayout.io.techfile import load_techfile, apply_techfile, load_resistor_geometry_rules
from hlayout.scripting.api import Session

TECHFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "2-Layer_solder_Build.json")

CONDUCTOR_1 = 2   # "1st_Conductor"
CONDUCTOR_2 = 4   # "2nd_Conductor"
SPACING_MM = 0.3


def build_library():
    lib = Library(name="DRC_DEMO")
    data = load_techfile(TECHFILE)
    apply_techfile(lib, data)
    return lib


def add_conductor_spacing_geometry(sess: Session):
    """Layer 2 (1st Conductor): one pair too close (FAIL), one pair OK (PASS).
    Layer 4 (2nd Conductor): same pattern."""
    sess.new_cell("SPACING_TEST")

    # --- Layer 2: violating pair, spaced 0.1mm apart (< 0.3mm required) ---
    sess.rect(CONDUCTOR_1, 0, 0, 1000, 1000)
    sess.rect(CONDUCTOR_1, 1100, 0, 2100, 1000)  # 100 um gap -> FAIL

    # --- Layer 2: compliant pair, spaced 0.4mm apart (>= 0.3mm required) ---
    sess.rect(CONDUCTOR_1, 0, 2000, 1000, 3000)
    sess.rect(CONDUCTOR_1, 1400, 2000, 2400, 3000)  # 400 um gap -> PASS

    # --- Layer 4: violating pair, spaced 0.15mm apart ---
    sess.rect(CONDUCTOR_2, 0, 5000, 1000, 6000)
    sess.rect(CONDUCTOR_2, 1150, 5000, 2150, 6000)  # 150 um gap -> FAIL

    # --- Layer 4: compliant pair, spaced 0.35mm apart ---
    sess.rect(CONDUCTOR_2, 0, 7000, 1000, 8000)
    sess.rect(CONDUCTOR_2, 1350, 7000, 2350, 8000)  # 350 um gap -> PASS


def add_resistors(sess: Session, rules: dict):
    """One nominal resistor generated via Session.generate_resistor()
    (should PASS every resistor-geometry check), and one hand-built
    "corrupted" resistor cell with insufficient overlap/pad
    length/space/glaze margin (should FAIL every check)."""
    sess.use_cell("SPACING_TEST")

    # Nominal resistor -- ground truth generator, should pass everything.
    sess.generate_resistor(x=5000, y=0, body_width=1000, body_length=2000,
                            ref_des="R1")

    # Deliberately-corrupted resistor: build a cell by hand with every
    # margin under-sized relative to `rules`.
    a = float(rules["overlap_a"])
    b = float(rules["pad_b"])
    c = float(rules["space_c"])
    d = float(rules["glaze_oversize"])
    r_layer = int(rules["resistor_layer"])
    c_layer = int(rules["conductor_layer"])
    g_layer = int(rules["glaze_layer"])

    half_l = 1000.0
    half_w = 500.0
    bad_a = a * 0.5       # insufficient overlap
    bad_b = b * 0.5       # insufficient pad length
    bad_c = c * 0.5       # insufficient pad space
    bad_d = d * 0.5       # insufficient glaze margin
    pad_half_w = half_w + bad_c

    sess.new_cell("R_BAD")
    sess.rect(r_layer, -(half_l + bad_a), -half_w, half_l + bad_a, half_w)
    sess.rect(g_layer, -(half_l + bad_a + bad_d), -(half_w + bad_d),
              half_l + bad_a + bad_d, half_w + bad_d)
    sess.rect(c_layer, -(half_l + bad_a + bad_b), -pad_half_w, -half_l, pad_half_w, pin="1")
    sess.rect(c_layer, half_l, -pad_half_w, half_l + bad_a + bad_b, pad_half_w, pin="2")
    sess.use_cell("SPACING_TEST")
    sess.sref("R_BAD", (9000, 0))


def main():
    lib = build_library()
    sess = Session(lib)

    add_conductor_spacing_geometry(sess)
    rules = load_resistor_geometry_rules(lib)
    add_resistors(sess, rules)

    print("=== Conductor spacing DRC (0.3mm min, layers 2 & 4) ===")
    spacing_rules = [
        DrcRule(kind="MIN_SPACE", layer_a=CONDUCTOR_1, value=mm_to_dbu(SPACING_MM),
                name="1st Conductor Spacing"),
        DrcRule(kind="MIN_SPACE", layer_a=CONDUCTOR_2, value=mm_to_dbu(SPACING_MM),
                name="2nd Conductor Spacing"),
    ]
    spacing_violations = run_drc(lib, spacing_rules)
    if not spacing_violations:
        print("  NO VIOLATIONS FOUND -- unexpected, the test geometry includes deliberate failures!")
    for v in spacing_violations:
        print(f"  [{v.rule.label}] cell={v.cell_name} pts={v.points}")
    print(f"  total: {len(spacing_violations)} violation(s)")

    print()
    print("=== Resistor geometry DRC ===")
    result = check_all_resistors(lib, rules)
    print(f"  checked cells: {result.checked_cells}")
    print(f"  skipped cells (not simple 2-pad resistors): {result.skipped_cells}")
    for v in result.violations:
        print(f"  [{v.kind}] {v.message}")
    print(f"  total: {len(result.violations)} violation(s)")

    print()
    print("=== Summary ===")
    print(f"  conductor spacing violations: {len(spacing_violations)} (expect 2: one on layer 2, one on layer 4)")
    print(f"  resistor geometry violations: {len(result.violations)} (expect 0 from R1, several from R_BAD)")


if __name__ == "__main__":
    main()
