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
