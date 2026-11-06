# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Where HLayout keeps its per-user files (settings, bug reports, hang
tracebacks).

Normally that is ``~/.hlayout``. The portable Windows build ships a
marker file ``portable.txt`` beside ``hlayout.exe``; when it is there
everything goes in a ``data`` folder next to the exe instead, so the
whole folder can live on a USB stick and leave nothing on the PC.
Setting the environment variable ``HLAYOUT_HOME`` overrides both.
"""

import os
import sys

PORTABLE_MARKER = "portable.txt"


def app_dir() -> str:
    """Folder holding the frozen executable (or the repo root when
    running from source)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_portable() -> bool:
    return os.path.isfile(os.path.join(app_dir(), PORTABLE_MARKER))


def user_dir() -> str:
    """``HLAYOUT_HOME``, else ``<exe folder>/data`` when portable, else
    ``~/.hlayout``. Not created here."""
    env = os.environ.get("HLAYOUT_HOME")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if is_portable():
        return os.path.join(app_dir(), "data")
    return os.path.join(os.path.expanduser("~"), ".hlayout")
