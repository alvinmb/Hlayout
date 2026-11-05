# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Database unit handling.

Per spec: database units are 1 micron (0.001 mm), and the database
extent is capped at 500 x 500 mm.

(Originally 0.1 mm; changed to 1 micron so hlayout's own GDSII grid
matches the database unit used by externally supplied reference
libraries, avoiding rounding/precision loss when attaching them.)
"""

DBU_MM = 0.001  # 1 database unit = 1 micron = 0.001 mm
MAX_SIZE_MM = 500.0  # database is at most 500 x 500 mm
MAX_SIZE_DBU = int(round(MAX_SIZE_MM / DBU_MM))  # 500000 DBU


def mm_to_dbu(value_mm: float) -> int:
    """Convert millimetres to (rounded) integer database units."""
    return int(round(value_mm / DBU_MM))


def dbu_to_mm(value_dbu: int) -> float:
    """Convert integer database units to millimetres."""
    return value_dbu * DBU_MM


def format_rkm(ohms: float, sig: int = 3) -> str:
    """A resistance in RKM / letter-and-digit code (IEC 60062), as printed
    on drawings: the multiplier letter stands in for the decimal point,
    with ``sig`` (default 3) significant figures --

        10 -> "10", 100 -> "100", 1200 -> "1K20", 680000 -> "680K",
        10.4e6 -> "10M4", 4.7 -> "4R70", 0.47 -> "R470"

    Whole values under 1000 ohms are written plainly. A value that needs
    more than ``sig`` figures to be exact gets one more (e.g. 1234 ->
    "1K234") rather than being silently rounded."""
    v = float(ohms)
    if v < 0:
        return "-" + format_rkm(-v, sig)
    if v == 0:
        return "0"
    if v < 1000 and abs(v - round(v)) < 1e-9:
        return str(int(round(v)))
    for mult, letter in ((1e9, "G"), (1e6, "M"), (1e3, "K"), (1.0, "R")):
        if v >= mult or letter == "R":
            break
    for n in (sig, sig + 1):
        m = v / mult
        int_digits = len(str(int(m))) if m >= 1 else 0
        text = f"{m:.{max(n - int_digits, 0)}f}"
        if float(text) >= 1000 and letter != "G":       # rounded up into the next decade
            return format_rkm(1000 * mult, sig)
        if abs(float(text) - m) <= abs(m) * 1e-9:
            break
    whole, _, frac = text.partition(".")
    if whole == "0":
        whole = ""
    return f"{whole}{letter}{frac}"

