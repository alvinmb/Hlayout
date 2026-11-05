# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Keeping tool sets between sessions.

The saved sets behind Tools > Gerber Export Sets..., Mask Operation
Batch... and DRC... live in a design's tech_params (see
hlayout.io.techfile), which used to reach disk only when the DESIGN was
saved -- so a set defined and then lost by closing without File > Save
came back empty on the next visit. This module closes both gaps:

  * remember() writes a changed set through to the design's sidecar
    (``<design>.gds.hlaux``) straight away, without saving geometry, and
    also records it as the user's LAST-USED set in the app settings file
    (hlayout.io.app_settings).
  * seed_from_last_used() fills a design that has no such set at all
    (a new design, or one from before the set existed) from that
    last-used copy, so the window does not open empty.

A design that has the set saved -- even as an explicitly emptied list --
always wins over the last-used copy.
"""

import json
import ntpath
import os
from typing import Iterable, Optional

from ..core.library import Library
from . import app_settings
from .sidecar import read_sidecar, sidecar_path, write_sidecar

LAST_USED_KEY = "last_tool_sets"


def _last_used(settings_path=None) -> dict:
    data = app_settings.load_app_settings(settings_path).get(LAST_USED_KEY)
    return data if isinstance(data, dict) else {}


def seed_from_last_used(library: Library, key: str, transform=None,
                        settings_path: Optional[str] = None) -> bool:
    """If ``library`` has no ``key`` set at all, copy the user's last-used
    value in (through ``transform(raw) -> raw`` when given). Returns True
    when something was seeded. Never raises."""
    if key in library.tech_params:
        return False
    raw = _last_used(settings_path).get(key)
    if not isinstance(raw, str) or not raw:
        return False
    if transform is not None:
        try:
            raw = transform(raw)
        except Exception:
            return False
    library.tech_params[key] = raw
    return True


def patch_sidecar(gds_path: str, library: Library, keys: Iterable[str]) -> bool:
    """Update just ``keys`` in the technology params of the sidecar next
    to ``gds_path`` (everything else in it -- layers, other params, the
    embedded attached libraries -- is left exactly as it was). Returns
    False when there is no readable sidecar to patch."""
    data = read_sidecar(gds_path)
    if data is None:
        return False
    tech = data.setdefault("technology", {})
    params = tech.get("params")
    if not isinstance(params, dict):
        params = tech["params"] = {}
    for key in keys:
        if key in library.tech_params:
            params[key] = library.tech_params[key]
        else:
            params.pop(key, None)
    path = sidecar_path(gds_path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)
        f.write("\n")
    os.replace(tmp, path)
    return True


def remember(library: Library, keys: Iterable[str], app=None,
             last_used_keys: Optional[Iterable[str]] = None,
             settings_path: Optional[str] = None) -> dict:
    """Make the tool set(s) under ``keys`` survive the session.

    ``app`` is the main window (for the open design's path and attached
    libraries); without one only the last-used copy is recorded.
    ``last_used_keys`` (default: all of ``keys``) are the ones also
    remembered as the user's last-used set.

    Returns {"sidecar": bool, "last_used": bool}. Never raises -- failing
    to persist must not break the edit the user just made."""
    keys = list(keys)
    done = {"sidecar": False, "last_used": False}

    design_path = getattr(app, "current_path", None) if app is not None else None
    if design_path and os.path.isfile(design_path):
        try:
            if patch_sidecar(design_path, library, keys):
                done["sidecar"] = True
            else:
                # A design file with no sidecar yet: give it one.
                session = app.session
                write_sidecar(library, list(session.attached_libraries),
                              list(session._attached_paths), design_path)
                done["sidecar"] = True
        except Exception:
            pass

    try:
        wanted = keys if last_used_keys is None else list(last_used_keys)
        if wanted:
            settings = app_settings.load_app_settings(settings_path)
            store = settings.get(LAST_USED_KEY)
            if not isinstance(store, dict):
                store = {}
            for key in wanted:
                if key in library.tech_params:
                    store[key] = library.tech_params[key]
            settings[LAST_USED_KEY] = store
            app_settings.save_app_settings(settings, settings_path)
            done["last_used"] = True
    except Exception:
        pass
    return done


def gerber_jobs_for_another_design(raw: str) -> str:
    """Last-used Gerber jobs, made safe to reuse in a different design:
    a job that pointed at a full path (the OTHER design's folder) keeps
    only its file-name prefix, so it writes into this design's own
    Output folder instead of over the other design's files."""
    jobs = json.loads(raw)
    for job in jobs:
        prefix = job.get("out_prefix") or ""
        if os.path.isabs(prefix) or ntpath.isabs(prefix) or ntpath.splitdrive(prefix)[0]:
            job["out_prefix"] = ntpath.basename(prefix.replace("/", "\\")) or "gerber"
    return json.dumps(jobs)


def dxf_jobs_for_another_design(raw: str, out_dir: Optional[str] = None) -> str:
    """Last-used DXF jobs, made safe to reuse in a different design: a
    job that pointed at a full path (the OTHER design's folder) keeps
    only its file name, placed in ``out_dir`` (this design's own dxf
    folder) when one is known, so it can't overwrite the other design's
    files."""
    jobs = json.loads(raw)
    for job in jobs:
        path = job.get("path") or ""
        if os.path.isabs(path) or ntpath.isabs(path) or ntpath.splitdrive(path)[0]:
            name = ntpath.basename(path.replace("/", "\\")) or "export.dxf"
            job["path"] = os.path.join(out_dir, name) if out_dir else name
    return json.dumps(jobs)
