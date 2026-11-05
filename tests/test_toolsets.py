# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tool sets (Gerber jobs, mask-op steps, DRC rules) keep between sessions."""

import json
import os
import types

from hlayout.core import Library
from hlayout.io import toolsets
from hlayout.io.sidecar import load_design, read_sidecar, sidecar_path
from hlayout.io.techfile import (DRC_RULES_KEY, GERBER_JOBS_KEY, MASK_OP_STEPS_KEY,
                                 load_drc_rules, load_gerber_export_jobs,
                                 save_gerber_export_jobs)
from hlayout.scripting import Session


def _saved_design(tmp_path):
    """A design saved to disk (gds + sidecar) and a stand-in for the App."""
    s = Session.new("KEEP")
    s.new_cell("TOP")
    s.rect(3, 0, 0, 100, 100)
    s.set_layer_style(3, name="Conductor")
    path = str(tmp_path / "board.gds")
    s.save_design(path)
    app = types.SimpleNamespace(current_path=path, session=s)
    return s, app, path


def test_a_changed_set_is_written_to_the_sidecar_without_saving_the_design(tmp_path):
    s, app, path = _saved_design(tmp_path)
    gds_before = open(path, "rb").read()
    save_gerber_export_jobs(s.library, [{"name": "fab", "out_prefix": "board", "layers": [3]}])

    done = toolsets.remember(s.library, [GERBER_JOBS_KEY], app=app)

    assert done == {"sidecar": True, "last_used": True}
    assert open(path, "rb").read() == gds_before          # geometry file untouched
    reopened = load_design(path).library                  # i.e. next session
    assert load_gerber_export_jobs(reopened) == [
        {"name": "fab", "out_prefix": "board", "layers": [3]}]
    assert reopened.layers[3].name == "Conductor"         # rest of the sidecar kept


def test_patching_the_sidecar_leaves_other_params_and_removes_a_cleared_key(tmp_path):
    s, app, path = _saved_design(tmp_path)
    s.library.tech_params["other_tool"] = "unsaved edit"   # not ours to write
    s.library.tech_params[DRC_RULES_KEY] = "[]"
    toolsets.remember(s.library, [DRC_RULES_KEY], app=app)
    params = read_sidecar(path)["technology"]["params"]
    assert params[DRC_RULES_KEY] == "[]" and "other_tool" not in params

    del s.library.tech_params[DRC_RULES_KEY]
    toolsets.remember(s.library, [DRC_RULES_KEY], app=app)
    assert DRC_RULES_KEY not in read_sidecar(path)["technology"]["params"]


def test_a_design_file_with_no_sidecar_gets_one(tmp_path):
    s, app, path = _saved_design(tmp_path)
    os.remove(sidecar_path(path))
    s.library.tech_params[MASK_OP_STEPS_KEY] = "[]"
    assert toolsets.remember(s.library, [MASK_OP_STEPS_KEY], app=app)["sidecar"] is True
    assert read_sidecar(path)["technology"]["params"][MASK_OP_STEPS_KEY] == "[]"


def test_an_unsaved_design_still_records_the_last_used_set():
    lib = Library("NEVER_SAVED")
    lib.tech_params[DRC_RULES_KEY] = json.dumps(
        [{"kind": "MIN_WIDTH", "layer_a": 3, "layer_b": None, "value": 300.0,
          "name": "w", "color": "#e6194b"}])
    app = types.SimpleNamespace(current_path=None, session=None)
    assert toolsets.remember(lib, [DRC_RULES_KEY], app=app) == {
        "sidecar": False, "last_used": True}

    fresh = Library("NEXT_DESIGN")                        # a later session
    assert toolsets.seed_from_last_used(fresh, DRC_RULES_KEY) is True
    assert [r["name"] for r in load_drc_rules(fresh)] == ["w"]


def test_a_design_with_its_own_set_is_never_overwritten_by_last_used():
    other = Library("OTHER")
    other.tech_params[DRC_RULES_KEY] = '[{"kind": "MIN_WIDTH", "layer_a": 1, "value": 5}]'
    toolsets.remember(other, [DRC_RULES_KEY])

    emptied = Library("EMPTIED")
    emptied.tech_params[DRC_RULES_KEY] = "[]"             # user deleted every rule
    assert toolsets.seed_from_last_used(emptied, DRC_RULES_KEY) is False
    assert emptied.tech_params[DRC_RULES_KEY] == "[]"


def test_seeding_with_nothing_remembered_does_nothing():
    lib = Library("EMPTY")
    assert toolsets.seed_from_last_used(lib, GERBER_JOBS_KEY) is False
    assert GERBER_JOBS_KEY not in lib.tech_params


def test_last_used_keys_limits_what_is_shared_across_designs():
    lib = Library("A")
    lib.tech_params[GERBER_JOBS_KEY] = "[]"
    lib.tech_params["gerber_output_dir"] = "D:/only/this/design"
    toolsets.remember(lib, [GERBER_JOBS_KEY, "gerber_output_dir"],
                      last_used_keys=[GERBER_JOBS_KEY])
    fresh = Library("B")
    assert toolsets.seed_from_last_used(fresh, "gerber_output_dir") is False


def test_last_used_gerber_jobs_lose_another_designs_folder():
    raw = json.dumps([
        {"name": "a", "out_prefix": "C:\\jobs\\board1\\gerber\\board1", "layers": None},
        {"name": "b", "out_prefix": "/home/me/out/top", "layers": [3]},
        {"name": "c", "out_prefix": "fabA/board", "layers": None},
    ])
    jobs = json.loads(toolsets.gerber_jobs_for_another_design(raw))
    assert [j["out_prefix"] for j in jobs] == ["board1", "top", "fabA/board"]


def test_last_used_dxf_jobs_are_rehomed_into_this_designs_folder(tmp_path):
    raw = json.dumps([
        {"name": "a", "path": "C:\\jobs\\board1\\dxf\\board1.dxf", "layers": None},
        {"name": "b", "path": "rel/out.dxf", "layers": [3]},
    ])
    here = str(tmp_path / "dxf")
    jobs = json.loads(toolsets.dxf_jobs_for_another_design(raw, here))
    assert jobs[0]["path"] == os.path.join(here, "board1.dxf")
    assert jobs[1]["path"] == "rel/out.dxf"
    assert json.loads(toolsets.dxf_jobs_for_another_design(raw))[0]["path"] == "board1.dxf"


def test_dxf_jobs_write_through_and_seed_like_the_other_sets(tmp_path):
    from hlayout.io.techfile import DXF_JOBS_KEY, load_dxf_export_jobs, save_dxf_export_jobs
    s, app, path = _saved_design(tmp_path)
    save_dxf_export_jobs(s.library, [{"name": "d", "path": "out.dxf", "layers": None}])
    assert toolsets.remember(s.library, [DXF_JOBS_KEY], app=app) == {
        "sidecar": True, "last_used": True}
    assert [j["path"] for j in load_dxf_export_jobs(load_design(path).library)] == ["out.dxf"]
    fresh = Library("NEW")
    assert toolsets.seed_from_last_used(fresh, DXF_JOBS_KEY) is True
    assert [j["name"] for j in load_dxf_export_jobs(fresh)] == ["d"]
