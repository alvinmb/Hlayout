import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core.errors import LibraryError
from hlayout.core.library import Library
from hlayout.io.gdsio import save_gds
from hlayout.scripting import Session


def _session_with_attached_library(tmp_path):
    """A Session on a fresh design, with an empty parts library attached
    at index 0 -- the setup Tools > Via Wizard... (Session.create_via_cell())
    needs, same pattern the GUI wizard requires (Setup > Attach Library...
    first)."""
    design = Library("DESIGN")
    design.new_cell("TOP")
    session = Session(design)

    parts = Library("PARTS")
    parts_path = str(tmp_path / "parts.gds")
    save_gds(parts, parts_path)
    session.attach_library(parts_path)
    return session, parts_path


def test_create_via_cell_uses_default_name_when_none_given(tmp_path):
    session, path = _session_with_attached_library(tmp_path)
    name, returned_path = session.create_via_cell(0, 1, size=100)
    assert name == "VIA_Layer0_to_Layer1"
    assert returned_path == path
    assert name in session.attached_libraries[0].cells


def test_create_via_cell_accepts_a_custom_name(tmp_path):
    session, _ = _session_with_attached_library(tmp_path)
    name, _ = session.create_via_cell(0, 1, size=100, name="MY_VIA")
    assert name == "MY_VIA"
    assert "MY_VIA" in session.attached_libraries[0].cells
    assert "VIA_Layer0_to_Layer1" not in session.attached_libraries[0].cells


def test_create_via_cell_with_dielectric_window_draws_a_third_shape(tmp_path):
    session, _ = _session_with_attached_library(tmp_path)
    name, _ = session.create_via_cell(
        0, 1, size=100, dielectric_layer=2, dielectric_window=140
    )
    cell = session.attached_libraries[0].cells[name]
    assert len(cell.shapes) == 3
    layers = sorted(s.layer for s in cell.shapes)
    assert layers == [0, 1, 2]
    dielectric_shape = next(s for s in cell.shapes if s.layer == 2)
    xs = [p[0] for p in dielectric_shape.points]
    assert max(xs) - min(xs) == 140  # full window size, not the radius/half


def test_create_via_cell_without_dielectric_draws_only_two_shapes(tmp_path):
    session, _ = _session_with_attached_library(tmp_path)
    name, _ = session.create_via_cell(0, 1, size=100)
    cell = session.attached_libraries[0].cells[name]
    assert len(cell.shapes) == 2


def test_create_via_cell_requires_dielectric_layer_and_window_together(tmp_path):
    session, _ = _session_with_attached_library(tmp_path)
    with pytest.raises(LibraryError):
        session.create_via_cell(0, 1, size=100, dielectric_layer=2)
    with pytest.raises(LibraryError):
        session.create_via_cell(0, 1, size=100, dielectric_window=140)


def test_create_via_cell_rejects_dielectric_layer_colliding_with_conductors(tmp_path):
    session, _ = _session_with_attached_library(tmp_path)
    with pytest.raises(LibraryError):
        session.create_via_cell(0, 1, size=100, dielectric_layer=1, dielectric_window=140)


def test_via_command_bar_accepts_name_and_dielectric_options(tmp_path):
    from hlayout.gui.commands import CommandInterpreter

    session, _ = _session_with_attached_library(tmp_path)
    interp = CommandInterpreter(session, console=None)
    out = interp.run(
        "VIA 0 1 shape=round size=80 name=CUSTOM_VIA "
        "dielectric_layer=2 dielectric_window=120"
    )
    assert "CUSTOM_VIA" in out
    cell = session.attached_libraries[0].cells["CUSTOM_VIA"]
    assert len(cell.shapes) == 3
