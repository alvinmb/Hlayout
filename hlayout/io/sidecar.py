"""Design sidecar: bundles everything a saved design needs to fully
restore a working session -- alongside the plain GDSII file, not
inside it.

GDSII (hlayout/io/gdsio.py) only carries geometry (cells, shapes,
srefs); it has no notion of layer names/colours, open-ended technology
parameters, or attached reference libraries (see hlayout/io/techfile.py
and Session.attach_library()). Saving a design as a *bare* .gds file
therefore drops all of that on the floor -- reopening it later gives
back the geometry but with a default, unnamed layer stack and no
attached libraries, forcing the technology file and every attached
library to be reloaded by hand.

To fix that without touching the .gds file's format at all (so it
stays a plain, standards-compliant GDSII file any other tool can open),
File > Save GDS... additionally writes a JSON *sidecar* file next to
it, named ``<path>.hlaux``, containing:

  * the technology (layer stack + tech_params -- which is also where
    the Thick-Film Resistor Designer's design rules live, see
    hlayout.io.techfile.RESISTOR_RULES_KEY, so "the resistor design
    file" is already covered by the same blob), and
  * a full copy (embedded, base64-encoded GDSII bytes -- not just a
    path) of every attached reference library, so re-opening the
    design still works even if the original attached file was moved,
    renamed, or deleted in the meantime.

File > Open GDS... (and the OPEN command) look for this sidecar next
to whatever .gds file is opened and, if present, restore all of the
above automatically. A .gds with no sidecar next to it (a file from
another tool, or one saved before this feature existed) opens exactly
as before -- this is purely additive.
"""

import base64
import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional

from ..core.library import Library
from .gdsio import load_gds, save_gds
from .techfile import apply_techfile, techfile_data

FORMAT_NAME = "hlayout-sidecar"
FORMAT_VERSION = 1
SIDECAR_SUFFIX = ".hlaux"


def sidecar_path(gds_path: str) -> str:
    return gds_path + SIDECAR_SUFFIX


def write_sidecar(library: Library, attached_libraries: List[Library],
                   attached_paths: List[str], gds_path: str) -> str:
    """Write the sidecar for a design being saved to ``gds_path``.
    Returns the sidecar's own path."""
    attached_entries = []
    for lib, src_path in zip(attached_libraries, attached_paths):
        fd, tmp_path = tempfile.mkstemp(suffix=".gds")
        os.close(fd)
        try:
            save_gds(lib, tmp_path)
            with open(tmp_path, "rb") as f:
                blob = f.read()
        finally:
            os.remove(tmp_path)
        attached_entries.append({
            "name": lib.name,
            "source_path": src_path,
            "gds_base64": base64.b64encode(blob).decode("ascii"),
        })

    data = {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "technology": techfile_data(library),
        "attached_libraries": attached_entries,
    }
    path = sidecar_path(gds_path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.write("\n")
    return path


def read_sidecar(gds_path: str) -> Optional[dict]:
    """Read the sidecar next to ``gds_path``, or return None if there
    isn't one (or it's not a recognizable sidecar) -- opening a .gds
    with no sidecar is not an error, just nothing extra to restore."""
    path = sidecar_path(gds_path)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("format") != FORMAT_NAME:
        return None
    return data


@dataclass
class LoadedDesign:
    """Everything restored from a design file + its sidecar (if any).
    ``attach_errors`` holds a human-readable message for each attached
    library that failed to decode/load -- the rest of the design still
    opens normally even if one embedded library is corrupt."""
    library: Library
    attached_libraries: List[Library] = field(default_factory=list)
    attached_paths: List[str] = field(default_factory=list)
    had_sidecar: bool = False
    attach_errors: List[str] = field(default_factory=list)


def load_design(gds_path: str) -> LoadedDesign:
    """Load a .gds file plus its sidecar (if present), returning a
    LoadedDesign ready to hand to a Session -- see
    Session.open_design()."""
    library = load_gds(gds_path)
    data = read_sidecar(gds_path)
    if data is None:
        return LoadedDesign(library=library)

    tech = data.get("technology")
    if isinstance(tech, dict):
        apply_techfile(library, tech)

    attached_libraries = []
    attached_paths = []
    attach_errors = []
    for entry in data.get("attached_libraries", []):
        name = entry.get("name", "?")
        try:
            blob = base64.b64decode(entry["gds_base64"])
        except Exception as e:
            attach_errors.append(f"{name}: could not decode embedded library ({e})")
            continue
        fd, tmp_path = tempfile.mkstemp(suffix=".gds")
        os.close(fd)
        try:
            with open(tmp_path, "wb") as f:
                f.write(blob)
            lib = load_gds(tmp_path, library_name=name)
        except Exception as e:
            attach_errors.append(f"{name}: could not load embedded library ({e})")
            continue
        finally:
            os.remove(tmp_path)
        attached_libraries.append(lib)
        attached_paths.append(entry.get("source_path") or f"(restored from {os.path.basename(gds_path)}{SIDECAR_SUFFIX})")

    return LoadedDesign(
        library=library,
        attached_libraries=attached_libraries,
        attached_paths=attached_paths,
        had_sidecar=True,
        attach_errors=attach_errors,
    )


def save_design(library: Library, attached_libraries: List[Library],
                 attached_paths: List[str], gds_path: str) -> str:
    """Save a design's .gds file plus its sidecar. Returns the sidecar
    path (the .gds itself is written at gds_path, unchanged in format
    from a plain save_gds() call)."""
    library.check_bounds()
    save_gds(library, gds_path)
    return write_sidecar(library, attached_libraries, attached_paths, gds_path)
