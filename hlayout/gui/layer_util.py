# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Layer pick-list helpers shared by the GUI windows.

Two label styles are in use: "3 (COND1)" (the default) and "3: COND1"
(colon=True). parse_layer_choice() reads either.
"""


def layer_label(library, number, colon=False):
    """The combobox text for layer `number` ("" for None)."""
    if number is None:
        return ""
    name = library.layers[number].name
    return f"{number}: {name}" if colon else f"{number} ({name})"


def layer_choices(library, colon=False, count=None):
    """Labels for layers 0..count-1 (default: every layer in the table)."""
    n = len(library.layers) if count is None else count
    return [layer_label(library, i, colon) for i in range(n)]


def parse_layer_choice(text):
    """The layer number at the front of a label, or None for blank."""
    text = text.strip()
    if not text:
        return None
    return int(text.split(" ", 1)[0].split(":", 1)[0])
