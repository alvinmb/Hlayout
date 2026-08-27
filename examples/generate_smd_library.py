"""Generate a GDSII library of standard SMD component footprints.

Produces one Cell per package: standard EIA chip resistor/capacitor
sizes (0201..2512), SOT-23, SOD-123, and narrow/wide-body SOIC-14/16/24.
Each cell has a body-outline Path (layer 60, 0.1 mm width) and a
reference-designator placeholder Text (layer 61: R/C/T/D/IC).

See hlayout/core/smdlib.py for the exact package list and dimensions,
and the important caveat about where those dimensions came from.

Usage:
    python examples/generate_smd_library.py [-o out.gds]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.core.smdlib import generate_smd_library
from hlayout.io.gdsio import save_gds


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", default="smd_library.gds", help="Output .gds path (default: smd_library.gds)")
    args = ap.parse_args()

    lib = generate_smd_library()
    save_gds(lib, args.out)
    print(f"{len(lib.cells)} SMD component cells -> {args.out}")
    for cell_name in lib.cells:
        print(f"  {cell_name}")


if __name__ == "__main__":
    main()
