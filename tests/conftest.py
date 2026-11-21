# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared test setup.

Every test gets its own throw-away app settings file, so nothing a test
does (a DRC rule added through a window, a Gerber job, ...) is written
into -- or read back from -- the real ~/.hlayout/settings.json of
whoever is running the suite.
"""

import atexit
import faulthandler
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _retrying_tk_init():
    """Tk on some Windows Python installs (seen on 3.14) intermittently
    fails to start -- "Can't find a usable tk.tcl", "couldn't read file
    .../ttk/cursors.tcl" -- for files that are plainly there. Every GUI
    test then skips as "no display available", silently hiding roughly
    one test in ten. Retry the start a couple of times before giving up;
    a machine with genuinely no display still fails (and skips) as
    before."""
    try:
        import tkinter
    except ImportError:
        return
    orig = tkinter.Tk.__init__
    if getattr(orig, "_hlayout_retry", False):
        return

    def __init__(self, *args, **kwargs):
        for attempt in range(3):
            try:
                return orig(self, *args, **kwargs)
            except tkinter.TclError as e:
                if attempt == 2 or "display" in str(e).lower():
                    raise

    __init__._hlayout_retry = True
    tkinter.Tk.__init__ = __init__


_retrying_tk_init()


#: seconds one test may run before it is treated as hung: the test's name
#: and every thread's stack go to HANG_LOG and the run stops (exit code 1),
#: instead of waiting forever on (say) a modal dialog nobody will click.
#: HLAYOUT_TEST_TIMEOUT=0 turns it off. (A file, not stderr: pytest's
#: output capture would swallow a dump written as the process exits.)
#: One log per run (pid in the name, so parallel runs don't overwrite
#: each other), removed again on a normal exit -- a leftover
#: %TEMP%/hlayout-test-hang-*.log IS a hang report.
_HANG_TIMEOUT = float(os.environ.get("HLAYOUT_TEST_TIMEOUT", "300"))
HANG_LOG = os.environ.get("HLAYOUT_HANG_LOG") or os.path.join(
    tempfile.gettempdir(), f"hlayout-test-hang-{os.getpid()}.log")
_hang_log = None


def _drop_hang_log():
    if _hang_log is not None:
        _hang_log.close()
        try:
            os.remove(HANG_LOG)
        except OSError:
            pass


@pytest.fixture(autouse=True)
def _hang_watchdog(request):
    global _hang_log
    if _HANG_TIMEOUT <= 0:
        yield
        return
    if _hang_log is None:
        _hang_log = open(HANG_LOG, "w")
        atexit.register(_drop_hang_log)
    _hang_log.seek(0)
    _hang_log.truncate()
    _hang_log.write(f"HUNG > {_HANG_TIMEOUT:g}s: {request.node.nodeid}\n")
    _hang_log.flush()
    faulthandler.dump_traceback_later(_HANG_TIMEOUT, exit=True, file=_hang_log)
    yield
    faulthandler.cancel_dump_traceback_later()


@pytest.fixture(autouse=True)
def _isolated_app_settings(tmp_path, monkeypatch):
    from hlayout.io import app_settings
    monkeypatch.setattr(app_settings, "SETTINGS_PATH",
                        str(tmp_path / "hlayout_test_settings.json"))
    yield
