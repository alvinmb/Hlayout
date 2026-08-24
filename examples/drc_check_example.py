"""Example: run Design Rule Check (DRC) from a script -- MIN_WIDTH (a
single layer's minimum feature width) and MIN_SPACE (minimum spacing
between distinct shapes on a single layer) -- and make any violations
visible two ways: printed to the console, and drawn as marker polygons
on an "errors" layer in the saved GDSII (so they show up in any viewer,
not just this script's own output). Run with:

    python examples/drc_check_example.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.drc import DrcRule
from hlayout.scripting import Session

s = Session.new("DRC_EXAMPLE")
top = s.new_cell("TOP")

INK = 30       # thick-film ink layer being checked
ERRORS = 63    # DRC violations get drawn here (last available layer number)

# --- Layout deliberately containing two kinds of mistake ------------------

# 1. A "dog bone" shape: two wide pads joined by a neck that's much
#    narrower than the rest of the ink -- MIN_WIDTH should flag the neck.
s.rect(INK, 0, 0, 200, 200)          # left pad, 200 wide
s.rect(INK, 200, 90, 260, 110)       # neck, only 20 wide
s.rect(INK, 260, 0, 460, 200)        # right pad, 200 wide

# 2. Two separate ink pads placed too close together -- MIN_SPACE should
#    flag the gap between them.
s.rect(INK, 0, 400, 150, 550)
s.rect(INK, 170, 400, 320, 550)      # only 20 DBU away from the first pad

# --- Rules ------------------------------------------------------------

rules = [
    DrcRule(kind="MIN_WIDTH", layer_a=INK, value=50.0, name="INK.W.1"),
    DrcRule(kind="MIN_SPACE", layer_a=INK, value=50.0, name="INK.S.1"),
]

# --- Run DRC, drawing every violation's marker polygon onto ERRORS ---------

violations = s.run_drc(rules, error_layer=ERRORS)

print(f"{len(violations)} violation(s) found:")
for v in violations:
    x1, y1, x2, y2 = v.bbox()
    print(f"  [{v.rule.name or v.rule.kind}] {v.message} "
          f"in cell {v.cell_name!r}, near ({x1:.0f}, {y1:.0f})-({x2:.0f}, {y2:.0f})")

if not violations:
    print("  (none -- try shrinking the neck or the gap above to see one)")

s.set_layer_style(INK, name="Thick-Film Ink", color="#f5c518")
s.set_layer_style(ERRORS, name="DRC Errors", color="#ff0000")

out_path = os.path.join(os.path.dirname(__file__), "drc_check_example.gds")
s.save(out_path)
print(f"Saved {out_path} -- open it and look at layer {ERRORS} (DRC Errors) "
      f"to see the violation markers drawn directly over the offending geometry.")
