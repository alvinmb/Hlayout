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
  * the source path of every attached reference library, plus a full
    embedded copy (base64-encoded GDSII bytes). On open, the live file
    at the source path is used when it's still there and readable -- so
    edits to an attached library (e.g. the Chip Wizard adding a part)
    show up next time -- and the embedded copy is the silent fallback
    for when that file has moved, been renamed, or deleted.

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
        src = entry.get("source_path")

        # Prefer the live file at source_path when it's still there and
        # readable -- so editing an attached library's .gds (e.g. the
        # Chip Wizard writing a new part into it) shows up on the next
        # open. The embedded base64 copy is the fallback for when the
        # file has moved away (portability), and is used silently then.
        lib = None
        if src and os.path.isfile(src):
            try:
                lib = load_gds(src, library_name=name)
            except Exception as e:
                attach_errors.append(
                    f"{name}: {src} couldn't be read ({e}); using the copy "
                    "embedded in the design"
                )
                lib = None

        if lib is None:
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
        attached_paths.append(src or f"(restored from {os.path.basename(gds_path)}{SIDECAR_SUFFIX})")

    return LoadedDesign(
        library=library,
        attached_libraries=attached_libraries,
        attached_paths=attached_paths,
        had_sidecar=True,
        attach_errors=attach_errors,
    )


#: how many earlier versions save_design() keeps: NAME.bak1.gds (the one
#: just overwritten) ... NAME.bak<KEEP_BACKUPS>.gds, each with its sidecar
KEEP_BACKUPS = 3


def backup_path(gds_path: str, n: int) -> str:
    """NAME.gds -> NAME.bak<n>.gds (opens like any design; sidecar alongside)."""
    root, ext = os.path.splitext(gds_path)
    return f"{root}.bak{n}{ext or '.gds'}"


def keep_backup(gds_path: str, keep: int = KEEP_BACKUPS) -> Optional[str]:
    """Before ``gds_path`` is overwritten: shift NAME.bak1..bak<keep-1>
    up one and copy the current NAME.gds (+ sidecar) to NAME.bak1.gds.
    Returns that path, or None when there was nothing to keep. Never
    raises -- a backup that can't be made mustn't stop the save."""
    import shutil
    if keep <= 0 or not os.path.isfile(gds_path) or os.path.getsize(gds_path) == 0:
        return None
    try:
        for n in range(keep - 1, 0, -1):
            src, dst = backup_path(gds_path, n), backup_path(gds_path, n + 1)
            for a, b in ((src, dst), (sidecar_path(src), sidecar_path(dst))):
                if os.path.exists(a):
                    shutil.copy2(a, b)
        dst = backup_path(gds_path, 1)
        shutil.copy2(gds_path, dst)
        if os.path.exists(sidecar_path(gds_path)):
            shutil.copy2(sidecar_path(gds_path), sidecar_path(dst))
        elif os.path.exists(sidecar_path(dst)):
            open(sidecar_path(dst), "w").close()       # don't pair it with a stale sidecar
        return dst
    except OSError:
        return None


def design_is_empty(library: Library) -> bool:
    """No shapes and no placed instances in any cell."""
    return not any(c.shapes or c.srefs or getattr(c, "arefs", None) for c in library.cells.values())


def save_design(library: Library, attached_libraries: List[Library],
                 attached_paths: List[str], gds_path: str,
                 keep: int = KEEP_BACKUPS) -> str:
    """Save a design's .gds file plus its sidecar. Returns the sidecar
    path (the .gds itself is written at gds_path, unchanged in format
    from a plain save_gds() call). The file being overwritten is kept
    first as NAME.bak1.gds (older ones shift to bak2, bak3 -- see
    keep_backup()); ``keep=0`` skips that."""
    library.check_bounds()
    keep_backup(gds_path, keep)
    save_gds(library, gds_path)
    return write_sidecar(library, attached_libraries, attached_paths, gds_path)
