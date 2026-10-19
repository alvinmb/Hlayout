"""Tiny app-level settings store -- for the handful of preferences that
belong to the *installation*, not to any one design.

Everything else hlayout persists (layer stack, tech-params, DXF/Gerber
export job lists, component_models, ...) lives in a design's own GDSII
sidecar or a shared technology .json file (see hlayout/io/techfile.py)
-- by design, since those are meant to travel with a design or a
process. The FreeCAD component STEP library is different: it's a
generic parts library (ceramic caps, SOT transistors, ...) meant to be
built ONCE and reused by every design, not regenerated per design --
so its folder can't live in any one design's tech-params without
tying it to whichever design happens to be open. This module is that
one exception: a single JSON file at a fixed, OS-user-level path,
independent of any design.

Currently holds just ``component_library_dir`` -- the folder File >
Build 3D Component Library... writes STEP files into, and
Export to FreeCAD's auto-matcher reads them back from (see
hlayout.gui.app.App.component_library_dir()). Structured as a
{key: value} dict from the start so a future app-level preference has
somewhere to go without a new file format.
"""

import json
import os

#: ~/.hlayout/settings.json -- deliberately NOT next to the hlayout
#: package itself (which may be read-only, e.g. installed site-wide),
#: and NOT an OS-specific "app data" path (no extra dependency to
#: locate one) -- os.path.expanduser("~") is the simplest thing that
#: works the same way on Windows/macOS/Linux.
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".hlayout", "settings.json")
_REAL_SETTINGS_PATH = SETTINGS_PATH

COMPONENT_LIBRARY_DIR_KEY = "component_library_dir"


def load_app_settings(path: str = None) -> dict:
    """The app-level settings dict, or ``{}`` if the file doesn't
    exist yet or is unreadable/corrupt (never raises -- a missing or
    bad settings file just means "nothing configured yet", not a
    startup failure). ``path`` defaults to the CURRENT value of
    SETTINGS_PATH at call time (not import time), so tests can
    monkeypatch ``app_settings.SETTINGS_PATH`` and have every function
    here honor it without threading an explicit path through."""
    if path is None:
        path = SETTINGS_PATH
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_app_settings(settings: dict, path: str = None) -> None:
    """Write ``settings`` out as the app-level settings file, creating
    its parent folder if needed."""
    if path is None:
        path = SETTINGS_PATH
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)


def get_component_library_dir(path: str = None):
    """The remembered FreeCAD component STEP library folder, or None
    if never set."""
    d = load_app_settings(path).get(COMPONENT_LIBRARY_DIR_KEY)
    # A folder left behind by a test run (pytest's temp dirs) is never a
    # real library -- one leaked in once and silently swapped every 3D
    # export over to stale STEP models. Treat it as unset.
    # (Only for the real settings file -- a test's own isolated settings
    # legitimately point into its temp folder.)
    real = path is None and os.path.normcase(os.path.abspath(SETTINGS_PATH)) == \
        os.path.normcase(os.path.abspath(_REAL_SETTINGS_PATH))
    if real and d and "pytest-of-" in d.replace("\\", "/"):
        return None
    return d


def set_component_library_dir(folder: str, path: str = None) -> None:
    """Remember ``folder`` as the FreeCAD component STEP library
    folder, for every design from now on -- merges into (rather than
    replaces) whatever else is already in the settings file."""
    settings = load_app_settings(path)
    settings[COMPONENT_LIBRARY_DIR_KEY] = folder
    save_app_settings(settings, path)


#: Freerouting launcher (.exe/app) or .jar, and an optional Java runtime
#: to run a .jar with -- remembered per installation (Setup >
#: Freerouting...), like the component library folder.
FREEROUTING_PATH_KEY = "freerouting_path"
JAVA_PATH_KEY = "java_path"


def get_setting(key: str, path: str = None):
    return load_app_settings(path).get(key)


def set_setting(key: str, value, path: str = None) -> None:
    data = load_app_settings(path)
    if value in (None, ""):
        data.pop(key, None)
    else:
        data[key] = value
    save_app_settings(data, path)
