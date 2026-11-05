# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from hlayout.core.strokefont import GLYPH_HEIGHT_UNITS, glyph, text_to_strokes


def test_every_uppercase_letter_and_digit_has_a_glyph():
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        assert glyph(ch) is not None, f"missing glyph for {ch!r}"


def test_lowercase_folds_to_the_same_glyph_as_uppercase():
    assert glyph("a") == glyph("A")
    assert glyph("z") == glyph("Z")


def test_unsupported_character_has_no_glyph():
    assert glyph("Ω") is None  # Greek capital omega -- not in the font


def test_space_has_a_glyph_with_no_strokes_but_a_nonzero_advance():
    result = glyph(" ")
    assert result is not None
    advance, strokes = result
    assert strokes == []
    assert advance > 0


def test_text_to_strokes_produces_no_strokes_for_an_empty_string():
    strokes, skipped = text_to_strokes("", origin=(0, 0), height=100)
    assert strokes == []
    assert skipped == 0


def test_text_to_strokes_counts_unsupported_characters_without_raising():
    strokes, skipped = text_to_strokes("R1Ω", origin=(0, 0), height=100)
    assert skipped == 1
    assert strokes  # "R" and "1" still produced strokes


def test_text_to_strokes_scales_by_height():
    strokes_small, _ = text_to_strokes("I", origin=(0, 0), height=GLYPH_HEIGHT_UNITS)
    strokes_big, _ = text_to_strokes("I", origin=(0, 0), height=GLYPH_HEIGHT_UNITS * 2)
    # "I" is defined as a single vertical stroke from y=0 to y=7 (see
    # strokefont.py) -- doubling the requested height should double the
    # world-space span of that stroke.
    y_small = [p[1] for p in strokes_small[0]]
    y_big = [p[1] for p in strokes_big[0]]
    assert max(y_big) - min(y_big) == pytest.approx(2 * (max(y_small) - min(y_small)))


def test_text_to_strokes_offsets_by_origin():
    strokes_at_0, _ = text_to_strokes("I", origin=(0, 0), height=GLYPH_HEIGHT_UNITS)
    strokes_at_100, _ = text_to_strokes("I", origin=(100, 200), height=GLYPH_HEIGHT_UNITS)
    x0, y0 = strokes_at_0[0][0]
    x1, y1 = strokes_at_100[0][0]
    assert x1 == pytest.approx(x0 + 100)
    assert y1 == pytest.approx(y0 + 200)


def test_text_to_strokes_rotates_90_degrees_ccw_about_the_origin():
    # "I" is a single vertical stroke (constant x, varying y) when
    # unrotated. Rotating 90 degrees CCW about the origin should swing
    # it to a horizontal stroke (constant y, varying x).
    strokes, _ = text_to_strokes("I", origin=(0, 0), height=GLYPH_HEIGHT_UNITS, rotation=90.0)
    xs = [round(p[0], 6) for p in strokes[0]]
    ys = [round(p[1], 6) for p in strokes[0]]
    assert len(set(ys)) == 1  # constant y -- now horizontal
    assert len(set(xs)) == len(strokes[0])  # x actually varies


def test_multiple_characters_advance_along_x_without_overlapping():
    strokes, _ = text_to_strokes("II", origin=(0, 0), height=GLYPH_HEIGHT_UNITS)
    first_x = strokes[0][0][0]
    second_x = strokes[1][0][0]
    assert second_x > first_x
