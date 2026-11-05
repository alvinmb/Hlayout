# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared test setup.

Every test gets its own throw-away app settings file, so nothing a test
does (a DRC rule added through a window, a Gerber job, ...) is written
into -- or read back from -- the real ~/.hlayout/settings.json of
whoever is running the suite.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def _isolated_app_settings(tmp_path, monkeypatch):
    from hlayout.io import app_settings
    monkeypatch.setattr(app_settings, "SETTINGS_PATH",
                        str(tmp_path / "hlayout_test_settings.json"))
    yield
