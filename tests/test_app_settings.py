import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.io.app_settings import (
    get_component_library_dir,
    load_app_settings,
    save_app_settings,
    set_component_library_dir,
)


def test_load_app_settings_missing_file_returns_empty_dict(tmp_path):
    path = str(tmp_path / "does_not_exist.json")
    assert load_app_settings(path) == {}


def test_load_app_settings_corrupt_file_returns_empty_dict(tmp_path):
    path = str(tmp_path / "bad.json")
    with open(path, "w") as f:
        f.write("{not valid json")
    assert load_app_settings(path) == {}


def test_save_and_load_app_settings_round_trip(tmp_path):
    path = str(tmp_path / "sub" / "settings.json")   # parent doesn't exist yet
    save_app_settings({"a": 1, "b": "two"}, path)
    assert load_app_settings(path) == {"a": 1, "b": "two"}


def test_component_library_dir_round_trips(tmp_path):
    path = str(tmp_path / "settings.json")
    assert get_component_library_dir(path) is None
    set_component_library_dir(r"C:\parts\step", path)
    assert get_component_library_dir(path) == r"C:\parts\step"


def test_set_component_library_dir_merges_not_replaces(tmp_path):
    path = str(tmp_path / "settings.json")
    save_app_settings({"other_key": "keep me"}, path)
    set_component_library_dir("/parts", path)
    settings = load_app_settings(path)
    assert settings["other_key"] == "keep me"
    assert settings["component_library_dir"] == "/parts"


def test_functions_honor_a_monkeypatched_settings_path(tmp_path, monkeypatch):
    """App.component_library_dir()/change_component_library_dir() call
    these without an explicit path, relying on the module-level
    SETTINGS_PATH default -- must be read at CALL time (not bound once
    at import time) so tests (and any future per-install override) can
    monkeypatch it."""
    import hlayout.io.app_settings as app_settings
    fake_path = str(tmp_path / "settings.json")
    monkeypatch.setattr(app_settings, "SETTINGS_PATH", fake_path)

    app_settings.set_component_library_dir("/shared/parts")
    assert app_settings.get_component_library_dir() == "/shared/parts"
    assert os.path.isfile(fake_path)
