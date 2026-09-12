import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

shapely = pytest.importorskip("shapely")

from hlayout.core.errors import GeometryError
from hlayout.core.units import DBU_MM
from hlayout.io.dxfio import REFDES_EXPORT_HEIGHT_DBU, DxfExportJob, write_dxf
from hlayout.io.techfile import save_refdes_annotation_layer
from hlayout.scripting import Session


def _design():
    s = Session.new("DXFTEST")
    s.new_cell("TOP")
    return s


def _polylines(text):
    """Split the ENTITIES section's raw group codes into one flat list
    of (vertex_x, vertex_y) tuples per POLYLINE...SEQEND run, in file
    order -- just enough parsing to assert on shape/count without a
    real DXF parser."""
    lines = text.splitlines()
    polylines = []
    i = 0
    while i < len(lines):
        if lines[i] == "0" and lines[i + 1] == "POLYLINE":
            i += 2
            verts = []
            while not (lines[i] == "0" and lines[i + 1] == "SEQEND"):
                if lines[i] == "0" and lines[i + 1] == "VERTEX":
                    j = i + 2
                    x = y = None
                    while lines[j] != "0":
                        if lines[j] == "10":
                            x = float(lines[j + 1])
                        elif lines[j] == "20":
                            y = float(lines[j + 1])
                        j += 2
                    verts.append((x, y))
                    i = j
                else:
                    i += 2
            polylines.append(verts)
            i += 2
        else:
            i += 1
    return polylines


def _texts(text):
    """[(x, y, string, height_mm, rotation), ...] for every TEXT entity."""
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if lines[i] == "0" and lines[i + 1] == "TEXT":
            i += 2
            fields = {}
            while i < len(lines) and lines[i] != "0":
                fields[lines[i]] = lines[i + 1]
                i += 2
            out.append((float(fields["10"]), float(fields["20"]), fields["1"],
                        float(fields["40"]), float(fields["50"])))
        else:
            i += 1
    return out


# --- Merging overlapping/touching shapes on one layer -------------------

def test_write_dxf_merges_two_touching_rectangles_into_one_polyline(tmp_path):
    s = _design()
    s.rect(0, 0, 0, 1000, 1000)
    s.rect(0, 1000, 0, 2000, 1000)   # shares the (1000,0)-(1000,1000) edge
    out = str(tmp_path / "merged.dxf")
    written, count = write_dxf(s.library, "TOP", out)
    assert written == [0]
    assert count == 1
    text = open(out).read()
    polylines = _polylines(text)
    assert len(polylines) == 1
    xs = [x for x, _ in polylines[0]]
    assert max(xs) - min(xs) == pytest.approx(2000 * DBU_MM, abs=0.001)  # full merged width


def test_write_dxf_keeps_separate_shapes_on_one_layer_apart(tmp_path):
    s = _design()
    s.rect(0, 0, 0, 100, 100)
    s.rect(0, 5000, 0, 5100, 100)   # far away -- doesn't touch
    out = str(tmp_path / "separate.dxf")
    written, count = write_dxf(s.library, "TOP", out)
    assert written == [0]
    assert count == 2
    assert len(_polylines(open(out).read())) == 2


# --- A path's width is baked into real polygon geometry, not dropped ----

def test_write_dxf_path_width_becomes_real_polygon_width(tmp_path):
    s = _design()
    s.path(1, [(0, 0), (10000, 0)], width=1000, cap="flush")
    out = str(tmp_path / "path.dxf")
    written, count = write_dxf(s.library, "TOP", out)
    assert written == [1]
    assert count == 1
    ys = [y for _, y in _polylines(open(out).read())[0]]
    # a flush-capped 1000-DBU-wide stroke spans y in [-500, 500] DBU
    assert max(ys) - min(ys) == pytest.approx(1000 * DBU_MM, abs=0.001)


def test_write_dxf_a_path_and_an_overlapping_pad_merge_together(tmp_path):
    s = _design()
    s.path(2, [(0, 0), (10000, 0)], width=1000, cap="flush")
    s.rect(2, -500, -500, 500, 500)   # sits right on top of the path's start
    out = str(tmp_path / "merged_path.dxf")
    written, count = write_dxf(s.library, "TOP", out)
    assert written == [2]
    assert count == 1   # one merged region, not two separate entities


# --- Text and ref designators --------------------------------------------

def test_write_dxf_exports_plain_text_shapes(tmp_path):
    s = _design()
    s.text(3, (1000, 2000), "HLAYOUT", height=300)
    out = str(tmp_path / "text.dxf")
    written, count = write_dxf(s.library, "TOP", out)
    assert written == [3]
    texts = _texts(open(out).read())
    assert len(texts) == 1
    x, y, string, height_mm, _rot = texts[0]
    assert string == "HLAYOUT"
    assert x == pytest.approx(1000 * DBU_MM)
    assert y == pytest.approx(2000 * DBU_MM)
    assert height_mm == pytest.approx(300 * DBU_MM)


def test_write_dxf_synthesizes_a_floating_ref_des(tmp_path):
    s = _design()
    save_refdes_annotation_layer(s.library, 10)
    s.new_cell("R1DEV")
    s.rect(0, -500, -500, 500, 500)
    s.use_cell("TOP")
    s.sref("R1DEV", (2000, 3000), label="R1")
    out = str(tmp_path / "refdes.dxf")
    written, count = write_dxf(s.library, "TOP", out)
    assert 10 in written
    texts = _texts(open(out).read())
    labels = [t[2] for t in texts]
    assert "R1" in labels
    r1 = next(t for t in texts if t[2] == "R1")
    assert r1[0] == pytest.approx(2000 * DBU_MM)   # instance origin == its own bbox centre here
    assert r1[1] == pytest.approx(3000 * DBU_MM)
    assert r1[3] == pytest.approx(REFDES_EXPORT_HEIGHT_DBU * DBU_MM)


def test_write_dxf_respects_a_custom_label_offset(tmp_path):
    s = _design()
    save_refdes_annotation_layer(s.library, 10)
    s.new_cell("R1DEV")
    s.rect(0, -500, -500, 500, 500)
    s.use_cell("TOP")
    ref = s.sref("R1DEV", (0, 0), label="R2")
    ref.label_offset = (100, 200)
    out = str(tmp_path / "refdes_offset.dxf")
    write_dxf(s.library, "TOP", out)
    r2 = next(t for t in _texts(open(out).read()) if t[2] == "R2")
    assert r2[0] == pytest.approx(100 * DBU_MM)
    assert r2[1] == pytest.approx(200 * DBU_MM)


def test_write_dxf_does_not_double_export_a_baked_ref_des(tmp_path):
    s = _design()
    save_refdes_annotation_layer(s.library, 10)
    s.new_cell("R3DEV")
    s.rect(0, -500, -500, 500, 500)
    s.text(10, (0, 0), "R3", height=200)   # baked -- matches the label exactly
    s.use_cell("TOP")
    s.sref("R3DEV", (5000, 5000), label="R3")
    out = str(tmp_path / "baked.dxf")
    write_dxf(s.library, "TOP", out)
    labels = [t[2] for t in _texts(open(out).read()) if t[2] == "R3"]
    assert labels == ["R3"]   # exactly once, not also floated


def test_write_dxf_skips_ref_des_when_no_annotation_layer_configured(tmp_path):
    s = _design()   # save_refdes_annotation_layer() never called -- defaults to none
    s.new_cell("R4DEV")
    s.rect(0, -500, -500, 500, 500)
    s.use_cell("TOP")
    s.sref("R4DEV", (0, 0), label="R4")
    out = str(tmp_path / "no_refdes_layer.dxf")
    write_dxf(s.library, "TOP", out)
    assert _texts(open(out).read()) == []


# --- Visibility / layer filtering (unchanged behaviour) ------------------

def test_write_dxf_skips_a_hidden_layer(tmp_path):
    s = _design()
    s.rect(0, 0, 0, 100, 100)
    s.library.layers.set_style(0, visible=False)
    out = str(tmp_path / "hidden.dxf")
    written, count = write_dxf(s.library, "TOP", out)
    assert written == []
    assert count == 0


def test_write_dxf_layers_argument_restricts_export(tmp_path):
    s = _design()
    s.rect(0, 0, 0, 100, 100)
    s.rect(1, 200, 0, 300, 100)
    out = str(tmp_path / "subset.dxf")
    written, count = write_dxf(s.library, "TOP", out, layers=[1])
    assert written == [1]
    assert count == 1


def test_write_dxf_no_such_cell_raises():
    s = _design()
    with pytest.raises(ValueError):
        write_dxf(s.library, "NOPE", "x.dxf")


# --- Requires shapely -----------------------------------------------------

def test_write_dxf_raises_a_clear_error_without_shapely(tmp_path, monkeypatch):
    import hlayout.core.maskops as maskops_mod
    s = _design()
    s.rect(0, 0, 0, 100, 100)
    monkeypatch.setattr(maskops_mod, "_ShapelyPolygon", None)
    with pytest.raises(GeometryError, match="shapely"):
        write_dxf(s.library, "TOP", str(tmp_path / "x.dxf"))


# --- DxfExportJob ----------------------------------------------------------

def test_dxf_export_job_round_trips_through_dict():
    job = DxfExportJob(name="Fab", path="out/board.dxf", layers=[3, 5])
    d = job.to_dict()
    restored = DxfExportJob.from_dict(d)
    assert restored.name == "Fab"
    assert restored.path == "out/board.dxf"
    assert restored.layers == [3, 5]


def test_dxf_export_job_requires_a_path():
    with pytest.raises(GeometryError):
        DxfExportJob(name="", path="")
