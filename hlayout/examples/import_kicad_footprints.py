#!/usr/bin/env python3
# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Convert one or more KiCad `.pretty` footprint libraries into GDSII --
one .gds file per library, with one Cell per footprint (pads, drill
holes, silkscreen/fab/courtyard outlines -- see hlayout/io/kicadio.py
for exactly what's converted and what isn't).

Usage:
    python examples/import_kicad_footprints.py PATH/TO/Foo.pretty [Bar.pretty ...] [-o out_dir]

Each footprint becomes a standalone Cell named after its .kicad_mod
file (e.g. "SOT-23"); place one with Session.use_cell()/sref(), or open
the .gds directly in HLayout to browse/copy footprints out of it.
"""
import argparse
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_root = _here
for _ in range(4):
    if os.path.isdir(os.path.join(_root, "hlayout")):
        break
    _root = os.path.dirname(_root)
sys.path.insert(0, _root)

from hlayout.core.library import Library
from hlayout.io.gdsio import save_gds
from hlayout.io.kicadio import load_kicad_footprint_library, style_kicad_layers


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pretty_dirs", nargs="+", help="One or more .pretty footprint library directories")
    ap.add_argument("-o", "--out-dir", default=".", help="Directory to write .gds files into (default: current directory)")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    for pretty_dir in args.pretty_dirs:
        pretty_dir = pretty_dir.rstrip("/\\")
        lib_name = os.path.basename(pretty_dir)
        if lib_name.endswith(".pretty"):
            lib_name = lib_name[: -len(".pretty")]
        library = Library(lib_name)
        style_kicad_layers(library)
        created = load_kicad_footprint_library(pretty_dir, library)
        out_path = os.path.join(args.out_dir, f"{lib_name}.gds")
        save_gds(library, out_path)
        print(f"{pretty_dir}: {len(created)} footprints -> {out_path}")


if __name__ == "__main__":
    main()
