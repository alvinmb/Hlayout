# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""hlayout.userdir: ~/.hlayout normally, data\\ beside the exe for the
portable Windows build, HLAYOUT_HOME overriding both."""
import os
import sys

from hlayout import userdir


def test_default_is_home_dot_hlayout(monkeypatch, tmp_path):
    monkeypatch.delenv("HLAYOUT_HOME", raising=False)
    monkeypatch.setattr(userdir, "app_dir", lambda: str(tmp_path))
    assert not userdir.is_portable()
    assert userdir.user_dir() == os.path.join(os.path.expanduser("~"), ".hlayout")


def test_portable_marker_uses_data_beside_exe(monkeypatch, tmp_path):
    monkeypatch.delenv("HLAYOUT_HOME", raising=False)
    monkeypatch.setattr(userdir, "app_dir", lambda: str(tmp_path))
    (tmp_path / userdir.PORTABLE_MARKER).write_text("x")
    assert userdir.is_portable()
    assert userdir.user_dir() == os.path.join(str(tmp_path), "data")


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("HLAYOUT_HOME", str(tmp_path / "h"))
    (tmp_path / userdir.PORTABLE_MARKER).write_text("x")
    monkeypatch.setattr(userdir, "app_dir", lambda: str(tmp_path))
    assert userdir.user_dir() == str(tmp_path / "h")


def test_frozen_app_dir_is_exe_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "hlayout.exe"))
    assert userdir.app_dir() == str(tmp_path)


def test_portable_txt_shipped():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert os.path.isfile(os.path.join(root, "packaging", "windows", "portable.txt"))
