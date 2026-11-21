# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Point transforms for walking a cell hierarchy (SREF placement)."""

import math
from typing import Callable, Tuple

Point = Tuple[float, float]


def sref_transform(ref) -> Callable[[Point], Point]:
    """Map a point in `ref`'s cell into the parent cell, in GDSII SREF
    order: mirror about X, scale by magnification, rotate, translate.
    The canvas, the mask ops, DRC and every exporter use this one."""
    theta = math.radians(ref.rotation)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    mag = ref.magnification
    ox, oy = ref.origin
    xr = ref.x_reflection

    def transform(pt):
        x, y = pt
        if xr:
            y = -y
        x *= mag
        y *= mag
        return (x * cos_t - y * sin_t + ox, x * sin_t + y * cos_t + oy)

    return transform


def chain_transform(parent: Callable[[Point], Point], ref) -> Callable[[Point], Point]:
    """`ref`'s own transform followed by `parent` (the accumulated
    transform of the cell `ref` sits in): child-local -> top-level."""
    local = sref_transform(ref)
    return lambda pt: parent(local(pt))
