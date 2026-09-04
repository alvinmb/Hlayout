"""Thick-Film Rectangular Resistor Design tool -- Tools > Thick-Film
Resistor Designer...

Ported from a standalone PyQt5 prototype (examples/Thickfilm_resisteresign.py)
to Tkinter, so it runs in-process with the rest of hlayout (same toolkit,
same event loop) instead of as a separate Qt program, and is reachable
from the Tools menu like every other utility in the app.

Design rules -- the aim percentage, aspect-ratio limits, and which ink
sheet resistances are available on the process -- used to live in a
module-level ``Settings`` singleton in the standalone tool: in-memory
only, reset every time the program restarted. That's now
``hlayout.io.techfile.load_resistor_design_rules()`` /
``save_resistor_design_rules()``, which read and write
``Library.tech_params`` -- the same open-ended technology-file storage
used for everything else under Setup > Load/Save Technology File... .
Opening this window loads the current design's rules (or sensible
defaults if none are saved yet); editing them via Design Rules... saves
them back into the library immediately, so the next Setup > Save
Technology File... captures them alongside the layer stack.

Printing: Tk has no native print dialog. "Print / Preview Report..."
below builds the same HTML report the original tool printed via Qt, and
opens it in the system's default web browser -- printing from there
(Ctrl+P) is the standard cross-platform way to get a PDF/printed copy
out of a Tk application.
"""

import csv
import json
import math
import os
import tempfile
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import tkinter as tk
from tkinter import filedialog, ttk

from . import dialogs
from .fonts import section_header_font
from .trim_sim_window import open_trim_simulator
from ..core.errors import LayoutError
from ..io.techfile import (
    load_resistor_design_rules,
    load_resistor_geometry_rules,
    load_resistor_list,
    save_resistor_design_rules,
    save_resistor_geometry_rules,
    save_resistor_list,
)
from ..core.units import dbu_to_mm, mm_to_dbu

# ─────────────────────────────────────────────────────────────────────────
# Constants (unchanged from the original tool -- pure data, no UI toolkit
# dependency, so nothing here needed to change for the Tk port)
# ─────────────────────────────────────────────────────────────────────────

DEFAULT_INKS = [
    10, 30, 100, 300, 1_000, 3_000,
    10_000, 30_000, 100_000, 300_000, 1_000_000,
]

AR_MIN_DEFAULT = 0.25
AR_MAX_DEFAULT = 4.00
AIM_PCT_DEFAULT = 75.0

TOLERANCES = ["0.1%", "0.5%", "1%", "2%", "5%", "10%", "20%"]

# Standard thick-film body widths with power ratings.
# (width_um, max_power_mw) -- max_power_mw is for the FULL width.
# During laser trimming up to 50% of the width may be removed, so the
# effective power rating used for sizing is max_power_mw * 0.5.
POWER_WIDTH_TABLE = [
    (1000, 62.5),
    (1400, 125.0),
    (1800, 200.0),
    (2000, 250.0),
    (2400, 375.0),
    (2800, 500.0),
    (3000, 625.0),
    (3400, 750.0),
    (3800, 1000.0),
]
WIDTH_LABELS = {w: f"{w / 1000:.1f} mm" for w, _ in POWER_WIDTH_TABLE}

TRIM_FACTOR = 0.5  # worst-case fraction of width remaining after laser trim


def effective_max_power(max_power_mw: float) -> float:
    """Power rating after assuming 50% width removal during laser trim."""
    return max_power_mw * TRIM_FACTOR


def width_for_power(power_mw: float) -> int:
    """Smallest body width whose trimmed power rating covers power_mw."""
    for w, max_p in POWER_WIDTH_TABLE:
        if power_mw <= effective_max_power(max_p):
            return w
    return POWER_WIDTH_TABLE[-1][0]


def max_power_for_width(width_um: int) -> float:
    """Rated (full-width) max power for a given body width."""
    for w, max_p in POWER_WIDTH_TABLE:
        if w == width_um:
            return max_p
    return 0.0


def effective_power_for_width(width_um: int) -> float:
    """Trimmed (50% width) effective power rating."""
    return effective_max_power(max_power_for_width(width_um))


# Colour scheme -- kept close to the original tool's dark theme, applied
# to the Tk widgets that support it (Treeview rows/tags, the AR-diagram
# Canvas, status labels) rather than a global stylesheet.
BG = "#0d1117"
PANEL = "#161b22"
BORDER = "#30363d"
ACCENT = "#58a6ff"
ACCENT2 = "#3fb950"
WARN = "#f78166"
GOLD = "#d29922"
TEXT = "#e6edf3"
MUTED = "#8b949e"
SEL_BG = "#1f3a5f"


def _eng(r: float) -> str:
    if r <= 0:
        return "0R"
    if r < 1:
        return f"{r * 1000:.0f}mΩ"
    if r < 1000:
        w = int(r)
        f = round((r - w) * 10)
        return f"{w}R{f}" if f else f"{w}R"
    if r < 1e6:
        k = r / 1e3
        w = int(k)
        f = round((k - w) * 10)
        return f"{w}k{f}" if f else f"{w}k"
    m = r / 1e6
    w = int(m)
    f = round((m - w) * 10)
    return f"{w}M{f}" if f else f"{w}M"


def _tol_fraction(tol_str: str) -> float:
    """Convert a tolerance string like '5%' to a fraction 0.05."""
    try:
        return float(tol_str.strip().rstrip("%")) / 100.0
    except ValueError:
        return 0.0


# ─────────────────────────────────────────────────────────────────────────
# Data model (unchanged logic; DesignRules replaces the old module-level
# Settings singleton -- see module docstring)
# ─────────────────────────────────────────────────────────────────────────

class DesignRules:
    """Holds the aim percentage, aspect-ratio limits, and available inks
    for one resistor-designer session. Loaded from / saved to the
    current library's technology-file data -- see
    hlayout.io.techfile.load_resistor_design_rules()/
    save_resistor_design_rules() -- instead of being a process-lifetime
    global like the original tool's Settings class."""

    def __init__(self, aim_pct=AIM_PCT_DEFAULT, ar_min=AR_MIN_DEFAULT,
                 ar_max=AR_MAX_DEFAULT, available_inks=None):
        self.aim_pct = aim_pct
        self.ar_min = ar_min
        self.ar_max = ar_max
        self.available_inks = list(available_inks) if available_inks else list(DEFAULT_INKS)

    @classmethod
    def from_library(cls, library) -> "DesignRules":
        data = load_resistor_design_rules(library)
        return cls(aim_pct=data["aim_pct"], ar_min=data["ar_min"],
                    ar_max=data["ar_max"], available_inks=data["available_inks"])

    def save_to_library(self, library) -> None:
        save_resistor_design_rules(library, {
            "aim_pct": self.aim_pct, "ar_min": self.ar_min,
            "ar_max": self.ar_max, "available_inks": list(self.available_inks),
        })


@dataclass
class Solution:
    ink_rsh: float
    squares: float
    body_width: float
    body_length: float
    aspect_ratio: float
    score: float
    recommended: bool = False

    @property
    def ar_label(self) -> str:
        ar = self.aspect_ratio
        return f"{ar:.3f}:1" if ar >= 1 else f"1:{1 / ar:.3f}"

    @property
    def width_label(self) -> str:
        return WIDTH_LABELS.get(int(self.body_width), f"{self.body_width / 1000:.2f} mm")


@dataclass
class Resistor:
    ref_des: str
    value: float
    tolerance: str
    power_mw: float
    body_width: float
    solutions: List[Solution] = field(default_factory=list)
    selected_idx: Optional[int] = None

    @property
    def value_label(self) -> str:
        return _eng(self.value)

    @property
    def width_label(self) -> str:
        return WIDTH_LABELS.get(int(self.body_width), f"{self.body_width / 1000:.2f} mm")

    @property
    def max_power_mw(self) -> float:
        return max_power_for_width(int(self.body_width))

    @property
    def effective_max_power_mw(self) -> float:
        return effective_power_for_width(int(self.body_width))

    @property
    def power_ok(self) -> bool:
        return self.power_mw <= self.effective_max_power_mw

    def aim_value(self, rules: DesignRules) -> float:
        """Aim value = value x (aim_pct% + tolerance%), e.g. 1kOhm 5% tol,
        75% aim -> 1000 x (0.75 + 0.05) = 800 Ohm."""
        tol_frac = _tol_fraction(self.tolerance)
        return self.value * (rules.aim_pct / 100.0 + tol_frac)

    def aim_value_label(self, rules: DesignRules) -> str:
        return _eng(self.aim_value(rules))

    @property
    def selected(self) -> Optional[Solution]:
        if self.selected_idx is not None and 0 <= self.selected_idx < len(self.solutions):
            return self.solutions[self.selected_idx]
        return None

    def compute_solutions(self, rules: DesignRules):
        self.solutions.clear()
        aim = self.aim_value(rules)
        for rsh in rules.available_inks:
            sq = aim / rsh
            if not (rules.ar_min <= sq <= rules.ar_max):
                continue
            self.solutions.append(Solution(
                ink_rsh=rsh, squares=sq,
                body_width=self.body_width, body_length=sq * self.body_width,
                aspect_ratio=sq, score=abs(math.log(sq)),
            ))
        self.solutions.sort(key=lambda s: s.score)
        if self.solutions:
            self.solutions[0].recommended = True
            self.selected_idx = 0
        else:
            self.selected_idx = None


def _ar_color(ar: float) -> str:
    if 0.9 <= ar <= 1.1:
        return ACCENT2
    if ar < 0.5 or ar > 2.0:
        return WARN
    return GOLD


# ─────────────────────────────────────────────────────────────────────────
# Add/Edit dialog
# ─────────────────────────────────────────────────────────────────────────

class ResistorDialog(tk.Toplevel):
    """Modal Add/Edit form -- ttk.Toplevel replacement for the original
    QDialog, following the same OK/Cancel + Return/Escape convention as
    hlayout's own dialogs (see App.show_path_style_dialog)."""

    def __init__(self, parent, resistor: Optional[Resistor] = None):
        super().__init__(parent)
        self.title("Add Resistor" if resistor is None else "Edit Resistor")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.result = None
        self._build(resistor)
        self.bind("<Return>", lambda e: self._accept())
        self.bind("<Escape>", lambda e: self.destroy())

    def _build(self, r):
        form = ttk.Frame(self, padding=16)
        form.pack(fill="both", expand=True)

        ttk.Label(form, text="Ref Des:").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.ref_var = tk.StringVar(value=r.ref_des if r else "R1")
        ttk.Entry(form, textvariable=self.ref_var, width=20).grid(
            row=0, column=1, columnspan=2, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Value (Ω):").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        self.val_var = tk.DoubleVar(value=r.value if r else 1000.0)
        ttk.Spinbox(form, from_=0.001, to=1e9, increment=1, textvariable=self.val_var,
                    width=18).grid(row=1, column=1, columnspan=2, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Tolerance:").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        self.tol_var = tk.StringVar(value=r.tolerance if r else "5%")
        ttk.Combobox(form, textvariable=self.tol_var, values=TOLERANCES, width=8,
                     state="readonly").grid(row=2, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Power (mW):").grid(row=3, column=0, sticky="e", padx=4, pady=4)
        self.pwr_var = tk.DoubleVar(value=r.power_mw if r else 125.0)
        pwr_spin = ttk.Spinbox(form, from_=0.001, to=10000.0, increment=1,
                                textvariable=self.pwr_var, width=18)
        pwr_spin.grid(row=3, column=1, columnspan=2, sticky="w", padx=4, pady=4)

        ttk.Separator(form).grid(row=4, column=0, columnspan=3, sticky="ew", pady=8)

        ttk.Label(form, text="Body Width:").grid(row=5, column=0, sticky="e", padx=4, pady=4)
        self._width_choices = [
            (w_um, f"{w_um / 1000:.1f} mm   (<= {effective_max_power(max_mw):.0f} mW after trim)")
            for w_um, max_mw in POWER_WIDTH_TABLE
        ]
        self.width_var = tk.StringVar()
        self.width_combo = ttk.Combobox(form, textvariable=self.width_var, width=34,
                                         state="readonly",
                                         values=[label for _, label in self._width_choices])
        self.width_combo.grid(row=5, column=1, sticky="w", padx=4, pady=4)

        self.auto_var = tk.BooleanVar(value=r is None)
        ttk.Checkbutton(form, text="Auto from power", variable=self.auto_var,
                         command=self._on_auto).grid(row=5, column=2, sticky="w", padx=4)

        self.pwr_label_var = tk.StringVar(value="")
        self.pwr_label = ttk.Label(form, textvariable=self.pwr_label_var)
        self.pwr_label.grid(row=6, column=0, columnspan=3, sticky="w", padx=4, pady=(0, 4))

        if r:
            idx = next((i for i, (w, _) in enumerate(self._width_choices)
                        if w == int(r.body_width)), 0)
            self.width_combo.current(idx)
        else:
            self.width_combo.current(0)

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=(0, 12))
        ttk.Button(btn_row, text="OK", command=self._accept).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left", padx=6)

        pwr_spin.bind("<KeyRelease>", lambda e: self._update())
        self.width_combo.bind("<<ComboboxSelected>>", lambda e: self._update_indicator())
        self._on_auto()

    def _on_auto(self):
        self.width_combo.configure(state="disabled" if self.auto_var.get() else "readonly")
        if self.auto_var.get():
            self._update()
        else:
            self._update_indicator()

    def _update(self):
        if self.auto_var.get():
            try:
                pwr = self.pwr_var.get()
            except tk.TclError:
                pwr = 0.0
            w = width_for_power(pwr)
            idx = next((i for i, (ww, _) in enumerate(self._width_choices) if ww == w), 0)
            self.width_combo.current(idx)
        self._update_indicator()

    def _current_width(self):
        idx = self.width_combo.current()
        if idx < 0:
            idx = 0
        return self._width_choices[idx][0]

    def _update_indicator(self):
        try:
            pwr = self.pwr_var.get()
        except tk.TclError:
            pwr = 0.0
        w_um = self._current_width()
        eff_p = effective_power_for_width(w_um)
        full_p = max_power_for_width(w_um)
        if pwr <= eff_p:
            self.pwr_label_var.set(
                f"OK  {pwr:.1f} mW <= {eff_p:.0f} mW (trim-derated from {full_p:.0f} mW)")
            self.pwr_label.configure(foreground=ACCENT2)
        else:
            self.pwr_label_var.set(
                f"WARNING  {pwr:.1f} mW EXCEEDS {eff_p:.0f} mW trim-derated rating!")
            self.pwr_label.configure(foreground=WARN)

    def _accept(self):
        try:
            value = float(self.val_var.get())
            power = float(self.pwr_var.get())
        except (tk.TclError, ValueError):
            dialogs.show_warning("Add Resistor", "Value and Power must be numbers.", parent=self)
            return
        self.result = (
            self.ref_var.get().strip() or "R?",
            value,
            self.tol_var.get(),
            power,
            float(self._current_width()),
        )
        self.destroy()


class PlaceResistorDialog(tk.Toplevel):
    """Modal X/Y/rotation prompt for Place in Layout... -- this is a
    creation dialog (like Add Resistor... above), not the numeric "Move"
    dialog that was removed from Edit in favour of mouse-driven window-
    select; there's no way to place a brand-new resistor by clicking
    since its geometry doesn't exist on the canvas yet to click on."""

    def __init__(self, parent, ref_des, library=None, default_conductor_layer=None,
                 default_x=0.0, default_y=0.0):
        super().__init__(parent)
        self.title(f"Place {ref_des} in Layout")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.result = None
        self.library = library

        form = ttk.Frame(self, padding=16)
        form.pack(fill="both", expand=True)

        # Defaults come from the designer window's running placement
        # cursor (see ResistorDesignerWindow._advance_place_cursor()),
        # NOT a hardcoded (0, 0) -- placing several resistors one after
        # another without touching X/Y previously stacked every one of
        # them on top of each other at the same spot.
        ttk.Label(form, text="X (mm):").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        self.x_var = tk.DoubleVar(value=dbu_to_mm(default_x))
        ttk.Spinbox(form, from_=-1e6, to=1e6, increment=0.001, textvariable=self.x_var, width=14).grid(
            row=0, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Y (mm):").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        self.y_var = tk.DoubleVar(value=dbu_to_mm(default_y))
        ttk.Spinbox(form, from_=-1e6, to=1e6, increment=0.001, textvariable=self.y_var, width=14).grid(
            row=1, column=1, sticky="w", padx=4, pady=4)

        ttk.Label(form, text="Rotation:").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        self.rot_var = tk.StringVar(value="0")
        ttk.Combobox(form, textvariable=self.rot_var, width=6, state="readonly",
                     values=["0", "90", "180", "270"]).grid(row=2, column=1, sticky="w", padx=4, pady=4)

        # Conductor pad layer: user-selected from this technology's named
        # layers (not tied to a specific ink the way the resistor ink
        # layer is), defaulting to Setup > Resistor Geometry Rules...'s
        # configured conductor layer but overridable per placement.
        ttk.Label(form, text="Conductor pad layer:").grid(row=3, column=0, sticky="e", padx=4, pady=4)
        default_label = (
            _layer_label(self.library, default_conductor_layer)
            if self.library is not None and default_conductor_layer is not None else ""
        )
        self.conductor_layer_var = tk.StringVar(value=default_label)
        conductor_combo = ttk.Combobox(
            form, textvariable=self.conductor_layer_var, width=20, state="readonly",
            values=_layer_choices(self.library) if self.library is not None else [])
        conductor_combo.grid(row=3, column=1, sticky="w", padx=4, pady=4)

        self.error_var = tk.StringVar(value="")
        ttk.Label(form, textvariable=self.error_var, foreground="red").grid(
            row=4, column=0, columnspan=2, sticky="w", padx=4)

        btn_row = ttk.Frame(self)
        btn_row.pack(pady=(0, 12))
        ttk.Button(btn_row, text="OK", command=self._accept).pack(side="left", padx=6)
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="left", padx=6)

        self.bind("<Return>", lambda e: self._accept())
        self.bind("<Escape>", lambda e: self.destroy())

    def _accept(self):
        try:
            x, y = mm_to_dbu(self.x_var.get()), mm_to_dbu(self.y_var.get())
        except tk.TclError:
            self.error_var.set("X and Y must be numbers.")
            return
        conductor_layer = None
        if self.library is not None and self.conductor_layer_var.get():
            try:
                conductor_layer = _resolve_layer_choice(self.library, self.conductor_layer_var.get())
            except LayoutError as e:
                self.error_var.set(str(e))
                return
        self.result = (x, y, float(self.rot_var.get()), conductor_layer)
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────
# Design Rules dialog (was "Setup" in the original tool -- renamed here
# to avoid colliding with hlayout's own Setup menu)
# ─────────────────────────────────────────────────────────────────────────

def _layer_choices(library):
    return [f"{n}: {library.layers[n].name}" for n in range(len(library.layers))]


def _layer_label(library, number):
    return f"{number}: {library.layers[number].name}"


def _resolve_layer_choice(library, text):
    text = text.strip()
    if ":" in text:
        text = text.split(":", 1)[0].strip()
    return library.layers.resolve(text)


class DesignRulesDialog(tk.Toplevel):
    """Configure the aim percentage, aspect-ratio limits, available
    inks, and (per available ink) which layer that ink's resistor body
    is drawn on -- the "design rules" the user asked to have moved into
    the technology file. The aim/AR/inks fields save straight into the
    passed-in DesignRules instance (and from there into the library's
    tech_params) on OK; the per-ink layer map saves alongside the A/B/C
    geometry rules in the *same* tech_params entry
    (resistor_geometry_rules) via hlayout.io.techfile's
    resistor_ink_layer()/save_resistor_geometry_rules(), since it's read
    by the same Session.generate_resistor() call that reads A/B/C."""

    def __init__(self, parent, rules: DesignRules, library=None):
        super().__init__(parent)
        self.title("Design Rules")
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)
        self.rules = rules
        self.library = library
        self.accepted = False
        self._build()
        self.bind("<Escape>", lambda e: self.destroy())

    def _build(self):
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)

        aim_box = ttk.LabelFrame(outer, text="Aim Value Settings", padding=10)
        aim_box.pack(fill="x", pady=(0, 10))
        ttk.Label(aim_box, text="Aim value = Nominal × (Aim% + Tolerance%)\n"
                                 "e.g. 1kΩ 5% tol, 75% aim → 1000 × (0.75 + 0.05) = 800 Ω\n"
                                 "This is the target resistance used for initial sizing.",
                  foreground=MUTED, justify="left").pack(anchor="w")
        aim_row = ttk.Frame(aim_box)
        aim_row.pack(fill="x", pady=(6, 0))
        ttk.Label(aim_row, text="Aim percentage:").pack(side="left")
        self.aim_var = tk.DoubleVar(value=self.rules.aim_pct)
        ttk.Spinbox(aim_row, from_=0.0, to=200.0, increment=1, textvariable=self.aim_var,
                    width=10).pack(side="left", padx=6)
        ttk.Label(aim_row, text="%").pack(side="left")

        ar_box = ttk.LabelFrame(outer, text="Aspect Ratio Limits", padding=10)
        ar_box.pack(fill="x", pady=(0, 10))
        ar_row = ttk.Frame(ar_box)
        ar_row.pack(fill="x")
        ttk.Label(ar_row, text="Min AR (squares):").pack(side="left")
        self.ar_min_var = tk.DoubleVar(value=self.rules.ar_min)
        ttk.Spinbox(ar_row, from_=0.01, to=1.0, increment=0.05, textvariable=self.ar_min_var,
                    width=8).pack(side="left", padx=(4, 20))
        ttk.Label(ar_row, text="Max AR (squares):").pack(side="left")
        self.ar_max_var = tk.DoubleVar(value=self.rules.ar_max)
        ttk.Spinbox(ar_row, from_=1.0, to=20.0, increment=0.5, textvariable=self.ar_max_var,
                    width=8).pack(side="left", padx=4)

        ink_box = ttk.LabelFrame(outer, text="Available Inks", padding=10)
        ink_box.pack(fill="x", pady=(0, 10))
        ttk.Label(ink_box, text="Check which ink sheet resistances are available "
                                 "on your process:").pack(anchor="w")
        grid = ttk.Frame(ink_box)
        grid.pack(fill="x", pady=(6, 6))
        self.ink_vars = {}
        for i, rsh in enumerate(DEFAULT_INKS):
            var = tk.BooleanVar(value=rsh in self.rules.available_inks)
            self.ink_vars[rsh] = var
            ttk.Checkbutton(grid, text=_eng(rsh) + "/□", variable=var).grid(
                row=i // 4, column=i % 4, sticky="w", padx=(0, 20), pady=2)
        sel_row = ttk.Frame(ink_box)
        sel_row.pack(fill="x")
        ttk.Button(sel_row, text="Select All",
                   command=lambda: [v.set(True) for v in self.ink_vars.values()]).pack(
            side="left", padx=(0, 6))
        ttk.Button(sel_row, text="Select None",
                   command=lambda: [v.set(False) for v in self.ink_vars.values()]).pack(side="left")

        self.ink_layer_vars = {}
        if self.library is not None:
            self._geom_rules = load_resistor_geometry_rules(self.library)
            layer_box = ttk.LabelFrame(
                outer, text="Ink Layers (which layer each ink's resistor body is drawn on)",
                padding=10)
            layer_box.pack(fill="x", pady=(0, 10))
            ttk.Label(
                layer_box,
                text="A resistor's actual layer (Place in Layout.../RESGEN) is looked "
                     "up here by its chosen ink's sheet resistance; unset falls back "
                     "to Setup > Resistor Geometry Rules... 's default ink layer.",
                foreground=MUTED, wraplength=520, justify="left",
            ).pack(anchor="w", pady=(0, 6))
            layer_grid = ttk.Frame(layer_box)
            layer_grid.pack(fill="x")
            choices = ["(default)"] + _layer_choices(self.library)
            ink_layers = self._geom_rules.get("ink_layers", {})
            for i, rsh in enumerate(DEFAULT_INKS):
                ttk.Label(layer_grid, text=_eng(rsh) + "/□:").grid(
                    row=i // 2, column=(i % 2) * 2, sticky="e", padx=(0, 4), pady=2)
                saved = ink_layers.get(str(rsh))
                value = _layer_label(self.library, saved) if saved is not None else "(default)"
                var = tk.StringVar(value=value)
                self.ink_layer_vars[rsh] = var
                ttk.Combobox(layer_grid, textvariable=var, width=20, state="readonly",
                             values=choices).grid(
                    row=i // 2, column=(i % 2) * 2 + 1, sticky="w", padx=(0, 16), pady=2)

        btn_row = ttk.Frame(outer)
        btn_row.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_row, text="Restore Defaults", command=self._restore).pack(side="left")
        ttk.Button(btn_row, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btn_row, text="OK", command=self._accept).pack(side="right")

    def _restore(self):
        self.aim_var.set(AIM_PCT_DEFAULT)
        self.ar_min_var.set(AR_MIN_DEFAULT)
        self.ar_max_var.set(AR_MAX_DEFAULT)
        for v in self.ink_vars.values():
            v.set(True)

    def _accept(self):
        selected = [rsh for rsh, v in self.ink_vars.items() if v.get()]
        if not selected:
            dialogs.show_warning("Design Rules", "At least one ink must be selected.", parent=self)
            return
        try:
            ar_min, ar_max = self.ar_min_var.get(), self.ar_max_var.get()
        except tk.TclError:
            dialogs.show_warning("Design Rules", "AR limits must be numbers.", parent=self)
            return
        if ar_min >= ar_max:
            dialogs.show_warning("Design Rules", "Min AR must be less than Max AR.", parent=self)
            return
        if self.library is not None:
            try:
                ink_layers = {}
                for rsh, var in self.ink_layer_vars.items():
                    text = var.get()
                    if text == "(default)":
                        continue
                    ink_layers[str(rsh)] = _resolve_layer_choice(self.library, text)
            except LayoutError as e:
                dialogs.show_warning("Design Rules", str(e), parent=self)
                return
            geom = dict(self._geom_rules)
            geom["ink_layers"] = ink_layers
            save_resistor_geometry_rules(self.library, geom)

        self.rules.aim_pct = self.aim_var.get()
        self.rules.ar_min = ar_min
        self.rules.ar_max = ar_max
        self.rules.available_inks = sorted(selected)
        self.accepted = True
        self.destroy()


# ─────────────────────────────────────────────────────────────────────────
# AR diagram -- a small tk.Canvas drawing, replacing the original's
# custom QPainter paintEvent()
# ─────────────────────────────────────────────────────────────────────────

class ARDiagram(tk.Canvas):
    def __init__(self, master, **kwargs):
        kwargs.setdefault("height", 120)
        kwargs.setdefault("bg", PANEL)
        kwargs.setdefault("highlightthickness", 0)
        super().__init__(master, **kwargs)
        self._sol = None
        self.bind("<Configure>", lambda e: self._redraw())

    def set_solution(self, sol: Optional[Solution]):
        self._sol = sol
        self._redraw()

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width() or 300
        h = self.winfo_height() or 120
        if not self._sol:
            self.create_text(w / 2, h / 2, text="No solution selected", fill=MUTED)
            return
        sol = self._sol
        ar = sol.aspect_ratio
        mg, lh = 12, 26
        aw, ah = w - 2 * mg, h - 2 * mg - lh
        if ar >= 1:
            rw = aw
            rh = min(ah, max(4, aw / ar))
        else:
            rh = ah
            rw = min(aw, max(4, ah * ar))
        rx = mg + (aw - rw) / 2
        ry = mg + (ah - rh) / 2
        color = _ar_color(ar)
        self.create_rectangle(rx, ry, rx + rw, ry + rh, fill=color, outline=color,
                               stipple="gray50")
        self.create_text(
            w / 2, ry + rh + lh / 2,
            text=f"L = {dbu_to_mm(sol.body_length):.3f} mm   W = {sol.body_width / 1000:.1f} mm"
                 f"   ({sol.ar_label})",
            fill=TEXT)


# ─────────────────────────────────────────────────────────────────────────
# Solution panel
# ─────────────────────────────────────────────────────────────────────────

class SolutionPanel(ttk.Frame):
    COLS = ("ink", "squares", "length", "width", "trim_p", "ratio", "rec")
    HEADINGS = ["Ink (Ω/□)", "Squares", "L (mm)", "Width", "Trim P (mW)", "Ratio", "★"]

    def __init__(self, master, on_use):
        super().__init__(master, padding=4)
        self._on_use = on_use
        self._solutions: List[Solution] = []
        self._current_idx = None
        self._rules: Optional[DesignRules] = None

        ttk.Label(self, text="SOLUTIONS", foreground=ACCENT, font=section_header_font(self)).pack(
            anchor="w")
        self.info_var = tk.StringVar(value="Select a resistor to view solutions.")
        ttk.Label(self, textvariable=self.info_var, foreground=MUTED, wraplength=520,
                  justify="left").pack(anchor="w", pady=(0, 6), fill="x")

        table_frame = ttk.Frame(self)
        table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_frame, columns=self.COLS, show="headings", height=8)
        for col, heading in zip(self.COLS, self.HEADINGS):
            self.table.heading(col, text=heading)
            self.table.column(col, width=90, anchor="center")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=vsb.set)
        self.table.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.table.bind("<<TreeviewSelect>>", self._on_row_sel)
        self.table.tag_configure("rec", background="#1a2e10")

        self.use_btn = ttk.Button(self, text="Use This Solution", command=self._use, state="disabled")
        self.use_btn.pack(fill="x", pady=(6, 6))

        self.diagram = ARDiagram(self)
        self.diagram.pack(fill="x")

    def show_solutions(self, res: Resistor, rules: DesignRules):
        self._solutions = res.solutions
        self._current_idx = res.selected_idx
        self._rules = rules
        eff_p = res.effective_max_power_mw
        self.info_var.set(
            f"{res.ref_des}  {res.value_label}  {res.tolerance}  |  "
            f"Aim: {res.aim_value_label(rules)}  ({rules.aim_pct:.0f}% + tol)  |  "
            f"Power: {res.power_mw:.1f} mW  |  Width: {res.width_label}  "
            f"(trim-rated: {eff_p:.0f} mW)  |  {len(res.solutions)} solution(s)"
        )
        self.table.delete(*self.table.get_children())
        for i, sol in enumerate(self._solutions):
            max_p = effective_power_for_width(int(sol.body_width))
            values = (_eng(sol.ink_rsh), f"{sol.squares:.4f}", f"{dbu_to_mm(sol.body_length):.3f}",
                      sol.width_label, f"{max_p:.0f}", sol.ar_label,
                      "★ REC" if sol.recommended else "")
            tags = ("rec",) if sol.recommended else ()
            self.table.insert("", "end", iid=str(i), values=values, tags=tags)
        if self._current_idx is not None and str(self._current_idx) in self.table.get_children():
            self.table.selection_set(str(self._current_idx))
        self._update_diagram()

    def _on_row_sel(self, event=None):
        sel = self.table.selection()
        self.use_btn.configure(state="normal" if sel else "disabled")
        if sel:
            idx = int(sel[0])
            if 0 <= idx < len(self._solutions):
                self.diagram.set_solution(self._solutions[idx])

    def _use(self):
        sel = self.table.selection()
        if sel:
            idx = int(sel[0])
            if 0 <= idx < len(self._solutions):
                self._current_idx = idx
                self._on_use(idx)

    def _update_diagram(self):
        idx = self._current_idx
        sol = self._solutions[idx] if idx is not None and idx < len(self._solutions) else None
        self.diagram.set_solution(sol)

    def clear(self):
        self._solutions = []
        self._current_idx = None
        self.info_var.set("Select a resistor to view solutions.")
        self.table.delete(*self.table.get_children())
        self.diagram.set_solution(None)
        self.use_btn.configure(state="disabled")


# ─────────────────────────────────────────────────────────────────────────
# Resistor list table
# ─────────────────────────────────────────────────────────────────────────

class ResistorTable(ttk.Frame):
    COLS = ("ref", "value", "aim", "tol", "power", "width", "ink",
            "squares", "length", "ratio", "sols", "pok", "status")
    HEADINGS = ["Ref Des", "Value", "Aim Value", "Tol", "Power (mW)", "Width",
                "Ink (Ω/□)", "Squares", "L (mm)", "Ratio", "Sols", "P OK", "Status"]

    def __init__(self, master, on_select):
        super().__init__(master, padding=4)
        self._on_select = on_select
        self.table = ttk.Treeview(self, columns=self.COLS, show="headings", height=20)
        for col, heading in zip(self.COLS, self.HEADINGS):
            self.table.heading(col, text=heading)
            self.table.column(col, width=80, anchor="center")
        self.table.column("value", width=90)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=vsb.set)
        self.table.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.table.bind("<<TreeviewSelect>>", self._on_row_sel)
        self.table.tag_configure("nosol", background="#2a1010")
        self.table.tag_configure("pwarn", background="#2a1510")

    def populate(self, resistors: List[Resistor], rules: DesignRules):
        self.table.delete(*self.table.get_children())
        for i, res in enumerate(resistors):
            self._fill(i, res, rules)

    def refresh_row(self, row, res: Resistor, rules: DesignRules):
        self._fill(row, res, rules)

    def current_row(self) -> int:
        sel = self.table.selection()
        if not sel:
            return -1
        return int(sel[0])

    def select_row(self, row: int):
        iid = str(row)
        if iid in self.table.get_children():
            self.table.selection_set(iid)
            self.table.see(iid)

    def _fill(self, i, res: Resistor, rules: DesignRules):
        sol = res.selected
        n = len(res.solutions)
        pok = res.power_ok
        status = "OK" if sol else ("NO SOL" if n == 0 else "—")
        p_txt = "OK" if pok else f"> {res.effective_max_power_mw:.0f}"
        values = (
            res.ref_des, res.value_label, res.aim_value_label(rules), res.tolerance,
            f"{res.power_mw:.1f}", res.width_label,
            _eng(sol.ink_rsh) if sol else "—",
            f"{sol.squares:.3f}" if sol else "—",
            f"{dbu_to_mm(sol.body_length):.3f}" if sol else "—",
            sol.ar_label if sol else "—",
            str(n), p_txt, status,
        )
        tags = []
        if n == 0:
            tags.append("nosol")
        elif not pok:
            tags.append("pwarn")
        iid = str(i)
        if iid in self.table.get_children():
            self.table.item(iid, values=values, tags=tags)
        else:
            self.table.insert("", "end", iid=iid, values=values, tags=tags)

    def _on_row_sel(self, event=None):
        row = self.current_row()
        if row >= 0:
            self._on_select(row)


# ─────────────────────────────────────────────────────────────────────────
# Summary / BOM panel
# ─────────────────────────────────────────────────────────────────────────

class SummaryPanel(ttk.Frame):
    def __init__(self, master):
        super().__init__(master, padding=8)
        ttk.Label(self, text="DESIGN SUMMARY", foreground=ACCENT,
                  font=section_header_font(self)).pack(anchor="w")
        self.text = tk.Text(self, bg=PANEL, fg=TEXT, insertbackground=TEXT,
                             wrap="none", state="disabled", height=25)
        self.text.pack(fill="both", expand=True, pady=(4, 0))

    def refresh(self, resistors: List[Resistor], rules: DesignRules):
        lines = ["=" * 70,
                 "  THICK-FILM RESISTOR DESIGN SUMMARY",
                 f"  Aim: value x ({rules.aim_pct:.0f}% + tol)  |  "
                 f"AR: {rules.ar_min}-{rules.ar_max}  |  "
                 f"Inks: {', '.join(_eng(r) for r in rules.available_inks)}",
                 "=" * 70]
        ink_map: dict = {}
        no_sol = []
        pwr_warn = []
        for res in resistors:
            sol = res.selected
            if sol:
                ink_map.setdefault(sol.ink_rsh, []).append(res)
            else:
                no_sol.append(res)
            if not res.power_ok:
                pwr_warn.append(res)

        lines += [
            f"\n  Total resistors  : {len(resistors)}",
            f"  With solution    : {len(resistors) - len(no_sol)}",
            f"  No solution      : {len(no_sol)}",
            f"  Power warnings   : {len(pwr_warn)}",
            f"  Inks required    : {len(ink_map)}",
        ]
        if ink_map:
            lines.append("\n  -- Ink Breakdown --------------------------------------------")
            for rsh in sorted(ink_map.keys()):
                parts = ink_map[rsh]
                lines.append(f"\n  {_eng(rsh):>10}/□   ({len(parts)} resistor(s))")
                lines.append(f"  {'Ref':<8}  {'Value':<10}  {'Aim':<10}  {'Tol':<6}  "
                              f"{'Power':>8}  {'Width':>8}  "
                              f"{'Sq':>7}  {'L(mm)':>8}  {'Ratio':>10}  {'P?':>3}")
                lines.append("  " + "-" * 82)
                for res in parts:
                    sol = res.selected
                    lines.append(
                        f"  {res.ref_des:<8}  {res.value_label:<10}  "
                        f"{res.aim_value_label(rules):<10}  {res.tolerance:<6}  "
                        f"{res.power_mw:>7.1f}m  {res.width_label:>8}  "
                        f"{sol.squares:>7.4f}  {dbu_to_mm(sol.body_length):>8.3f}  "
                        f"{sol.ar_label:>10}  {'OK' if res.power_ok else '!!':>3}"
                    )
        if no_sol:
            lines.append("\n  -- No Valid Solution ------------------------------------------")
            for res in no_sol:
                lines.append(f"    {res.ref_des:<8} {res.value_label:<10} "
                              f"(aim: {res.aim_value_label(rules)}) {res.tolerance}")
        if pwr_warn:
            lines.append("\n  -- Power Warnings (trim-derated ratings) -----------------------")
            for res in pwr_warn:
                lines.append(
                    f"    {res.ref_des:<8} {res.power_mw:.1f} mW exceeds "
                    f"{res.width_label} trim rating ({res.effective_max_power_mw:.0f} mW)")
        lines.append("\n" + "=" * 70)

        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", "\n".join(lines))
        self.text.configure(state="disabled")


# ─────────────────────────────────────────────────────────────────────────
# Main window
# ─────────────────────────────────────────────────────────────────────────

class ResistorDesignerWindow(tk.Toplevel):
    """Tools > Thick-Film Resistor Designer... . `library` is the current
    design's Library (self.session.library in App) -- design rules are
    loaded from it on open and saved back into it (library.tech_params)
    whenever Design Rules... is accepted, so they travel with the design's
    technology file without any extra plumbing."""

    def __init__(self, master, library, app=None):
        super().__init__(master)
        self.title("Thick-Film Resistor Designer")
        self.geometry("1350x820")
        self.library = library
        self.app = app  # the main App, if available -- needed to actually
                         # draw a sized resistor into the layout (Place in
                         # Layout...); the window still works stand-alone
                         # (sizing only) if this is None.
        self.rules = DesignRules.from_library(library)
        self.resistors: List[Resistor] = []
        # Where the NEXT "Place in Layout..."/"Place All in Layout..."
        # placement defaults to. Every successful placement advances
        # this past that resistor's own footprint (+ a margin) so
        # resistors placed one after another lay out in a row instead
        # of landing on top of each other at (0, 0) every time -- see
        # _place_in_layout()/_place_all_in_layout()/_advance_place_cursor().
        self._next_place_x = 0.0
        self._next_place_y = 0.0
        self._build()
        self._load_resistors()
        self._refresh_all()

    # -- persistence of the working resistor list -------------------------
    def _load_resistors(self):
        """Rebuild the resistor list from what was saved with the design
        (hlayout.io.techfile.load_resistor_list) -- so closing the window
        or the app and coming back doesn't lose the work. Solutions are
        recomputed here from the current design rules; the previously
        chosen ink is re-selected by sheet resistance."""
        for d in load_resistor_list(self.library):
            width = d["body_width"] or float(width_for_power(d["power_mw"]))
            res = Resistor(ref_des=d["ref_des"], value=d["value"],
                           tolerance=d["tolerance"], power_mw=d["power_mw"],
                           body_width=width)
            res.compute_solutions(self.rules)
            if d["selected_ink"] is not None:
                for i, sol in enumerate(res.solutions):
                    if sol.ink_rsh == d["selected_ink"]:
                        res.selected_idx = i
                        break
            self.resistors.append(res)

    def _save_resistors(self):
        """Persist the current resistor list into the design's technology
        data (Library.tech_params) so it travels with File > Save GDS /
        Setup > Save Technology File... Called after every change to the
        list."""
        save_resistor_list(self.library, [
            {"ref_des": r.ref_des, "value": r.value, "tolerance": r.tolerance,
             "power_mw": r.power_mw, "body_width": r.body_width,
             "selected_ink": (r.selected.ink_rsh if r.selected else None)}
            for r in self.resistors
        ])

    # -- UI ----------------------------------------------------------------
    def _build(self):
        toolbar = ttk.Frame(self, padding=6)
        toolbar.pack(fill="x")
        for text, cmd in [
            ("Add...", self._add_resistor),
            ("Edit...", self._edit_resistor),
            ("Delete", self._delete_resistor),
        ]:
            ttk.Button(toolbar, text=text, command=cmd).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        for text, cmd in [
            ("Auto-Select All", self._auto_select_all),
            ("Recalculate", self._recalculate_all),
        ]:
            ttk.Button(toolbar, text=text, command=cmd).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="Design Rules...", command=self._open_design_rules).pack(
            side="left", padx=2)
        ttk.Button(toolbar, text="Place in Layout...", command=self._place_in_layout).pack(
            side="left", padx=2)
        ttk.Button(toolbar, text="Place All in Layout...", command=self._place_all_in_layout).pack(
            side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="Trim Simulator...", command=self._open_trim_simulator).pack(
            side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        for text, cmd in [
            ("Import CSV...", self._import_csv),
            ("Export CSV...", self._export_csv),
        ]:
            ttk.Button(toolbar, text=text, command=cmd).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(toolbar, text="Print / Preview Report...", command=self._print_report).pack(
            side="left", padx=2)
        ttk.Button(toolbar, text="About", command=self._about).pack(side="right", padx=2)

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=4, pady=4)

        left = ttk.Frame(body)
        ttk.Label(left, text="RESISTOR LIST", foreground=ACCENT,
                  font=section_header_font(left)).pack(anchor="w", padx=4, pady=(4, 0))
        self.res_table = ResistorTable(left, on_select=self._on_res_selected)
        self.res_table.pack(fill="both", expand=True)
        body.add(left, weight=1)

        right = ttk.Frame(body)
        notebook = ttk.Notebook(right)
        notebook.pack(fill="both", expand=True)
        self.sol_panel = SolutionPanel(notebook, on_use=self._on_solution_chosen)
        notebook.add(self.sol_panel, text="Solutions")
        self.summary_panel = SummaryPanel(notebook)
        notebook.add(self.summary_panel, text="Summary / BOM")
        body.add(right, weight=1)

        status = ttk.Frame(self, padding=(6, 3))
        status.pack(fill="x")
        self.status_var = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.status_var, foreground=MUTED).pack(side="left")

    # -- refresh -------------------------------------------------------------
    def _refresh_all(self):
        self.res_table.populate(self.resistors, self.rules)
        self.summary_panel.refresh(self.resistors, self.rules)
        self._update_status()
        self._save_resistors()

    def _refresh_row(self, row):
        self.res_table.refresh_row(row, self.resistors[row], self.rules)
        self.summary_panel.refresh(self.resistors, self.rules)
        self._update_status()
        self._save_resistors()

    def _update_status(self):
        n = len(self.resistors)
        inks = len({r.selected.ink_rsh for r in self.resistors if r.selected})
        nos = sum(1 for r in self.resistors if not r.solutions)
        pw = sum(1 for r in self.resistors if not r.power_ok)
        self.status_var.set(
            f"Resistors: {n}   |   Inks: {inks}   |   No solution: {nos}   |   "
            f"Trim-power warnings: {pw}")

    # -- handlers --------------------------------------------------------
    def _on_res_selected(self, row):
        if 0 <= row < len(self.resistors):
            self.sol_panel.show_solutions(self.resistors[row], self.rules)

    def _on_solution_chosen(self, idx):
        row = self.res_table.current_row()
        if 0 <= row < len(self.resistors):
            self.resistors[row].selected_idx = idx
            self._refresh_row(row)
            self.sol_panel.show_solutions(self.resistors[row], self.rules)
            res = self.resistors[row]
            self.status_var.set(
                f"Solution set: {res.ref_des}  {_eng(res.selected.ink_rsh)}/□  "
                f"{res.selected.squares:.4f}□  L={dbu_to_mm(res.selected.body_length):.3f}mm")

    # -- CRUD --------------------------------------------------------------
    def _add_resistor(self):
        dlg = ResistorDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            ref, val, tol, pwr, w = dlg.result
            res = Resistor(ref_des=ref, value=val, tolerance=tol, power_mw=pwr, body_width=w)
            res.compute_solutions(self.rules)
            self.resistors.append(res)
            self._refresh_all()
            self.res_table.select_row(len(self.resistors) - 1)

    def _edit_resistor(self):
        row = self.res_table.current_row()
        if row < 0 or row >= len(self.resistors):
            return
        res = self.resistors[row]
        dlg = ResistorDialog(self, resistor=res)
        self.wait_window(dlg)
        if dlg.result:
            ref, val, tol, pwr, w = dlg.result
            res.ref_des, res.value, res.tolerance = ref, val, tol
            res.power_mw, res.body_width = pwr, w
            res.compute_solutions(self.rules)
            self._refresh_row(row)
            self._on_res_selected(row)

    def _delete_resistor(self):
        row = self.res_table.current_row()
        if row < 0 or row >= len(self.resistors):
            return
        if dialogs.ask_yes_no("Delete", f"Delete {self.resistors[row].ref_des}?", parent=self):
            self.resistors.pop(row)
            self._refresh_all()
            self.sol_panel.clear()

    def _auto_select_all(self):
        for res in self.resistors:
            res.compute_solutions(self.rules)
        self._refresh_all()
        r = self.res_table.current_row()
        if r >= 0:
            self._on_res_selected(r)

    def _recalculate_all(self):
        for res in self.resistors:
            prev = res.selected.ink_rsh if res.selected else None
            res.compute_solutions(self.rules)
            if prev:
                for i, sol in enumerate(res.solutions):
                    if sol.ink_rsh == prev:
                        res.selected_idx = i
                        break
        self._refresh_all()

    def _open_design_rules(self):
        dlg = DesignRulesDialog(self, self.rules, library=self.library)
        self.wait_window(dlg)
        if dlg.accepted:
            # Persist immediately into the library's technology-file data
            # (Library.tech_params) -- see module docstring -- so Setup >
            # Save Technology File... captures the change without any
            # extra step here.
            self.rules.save_to_library(self.library)
            self._recalculate_all()
            self.status_var.set(
                f"Design rules saved to technology file: Aim {self.rules.aim_pct:.0f}%  |  "
                f"AR {self.rules.ar_min}-{self.rules.ar_max}  |  "
                f"{len(self.rules.available_inks)} inks active")

    # -- trim simulation --------------------------------------------------
    def _open_trim_simulator(self):
        """Run a plunge/L-cut laser-trim simulation (resistance,
        sensitivity, and a current-density heat map as the cut
        advances) against the currently selected resistor's chosen
        Solution -- see hlayout.core.trim_sim / hlayout.gui.trim_sim_window.
        Same selection requirement as Place in Layout...: a resistor
        row must be selected and have a solution chosen first."""
        row = self.res_table.current_row()
        if row < 0 or row >= len(self.resistors):
            dialogs.show_info("Trim Simulator", "Select a resistor first.", parent=self)
            return
        res = self.resistors[row]
        sol = res.selected
        if sol is None:
            dialogs.show_info(
                "Trim Simulator",
                f"{res.ref_des} has no solution selected -- pick one on the "
                "Solutions tab (Use This Solution) first.",
                parent=self)
            return
        open_trim_simulator(self, res, sol)

    # -- generate into the actual layout ------------------------------------
    #
    # Placing more than one resistor used to default every "Place in
    # Layout..." dialog to (0, 0) -- fine for a single resistor, but
    # placing a second and third one without manually typing a new X/Y
    # each time landed them exactly on top of the first (reported: "ALL
    # RESISTORS ARE PLACED IN THE DESIGN ON TOP OF EACH OTHER"). Now a
    # running placement cursor tracks the next LEFT EDGE available for
    # placement (self._next_place_x/_next_place_y), not a centre point --
    # each resistor is centred half its own footprint to the right of
    # that edge, and the cursor then advances to that resistor's own
    # right edge (+ a margin). Centre-based advancing (cursor +=
    # footprint) was tried first but under-spaces whenever consecutive
    # resistors have different footprints: a small resistor's FULL
    # footprint can be shorter than a following large resistor's HALF
    # footprint, so the next one's left edge lands inside the previous
    # one's body. Left-edge tracking is correct for any mix of sizes,
    # still fully overridable by typing a different X/Y in the dialog.
    # "Place All in Layout..." below does the same thing for every sized
    # resistor in one step, with no dialog at all.

    def _footprint_length(self, sol, geom_rules) -> float:
        """Total pad-to-pad (outer edge to outer edge) length of a
        solution's resistor structure -- body_length + 2*A + 2*B --
        used to space consecutive placements apart."""
        return sol.body_length + 2 * geom_rules["overlap_a"] + 2 * geom_rules["pad_b"]

    def _default_place_x(self, sol, geom_rules) -> float:
        """Default centre X for the next placement: half this
        resistor's own footprint to the right of the next available
        left edge."""
        return self._next_place_x + self._footprint_length(sol, geom_rules) / 2.0

    def _advance_place_cursor(self, x, sol, geom_rules, margin=None):
        """Move the running left-edge cursor past the resistor just
        placed centred at x, so the next default X starts clear of it
        regardless of that next resistor's own size. margin defaults to
        the technology file's own pad length (B) -- a reasonable,
        already-configured gap between adjacent parts."""
        if margin is None:
            margin = geom_rules["pad_b"]
        self._next_place_x = x + self._footprint_length(sol, geom_rules) / 2.0 + margin

    def _place_in_layout(self):
        """Draw the currently selected resistor's ink body, glaze
        overprint, conductor pads, and reference-designator label into
        the design, using its selected solution's body_width/
        body_length/ink_rsh, its ref_des, and this design's Setup >
        Resistor Geometry Rules... (Session.generate_resistor()). The
        resistor ink is matched to its layer automatically by the
        solution's ink sheet resistance (see Design Rules... 's Ink
        Layers section); the conductor pad layer is chosen in the
        placement dialog itself, defaulting to the technology file's
        configured conductor layer. Requires a solution to have been
        chosen (Solutions tab > Use This Solution) and a main App
        window to draw into."""
        if self.app is None:
            dialogs.show_info(
                "Place in Layout",
                "This resistor designer isn't attached to a layout window.",
                parent=self)
            return
        row = self.res_table.current_row()
        if row < 0 or row >= len(self.resistors):
            dialogs.show_info("Place in Layout", "Select a resistor first.", parent=self)
            return
        res = self.resistors[row]
        sol = res.selected
        if sol is None:
            dialogs.show_info(
                "Place in Layout",
                f"{res.ref_des} has no solution selected -- pick one on the "
                "Solutions tab (Use This Solution) first.",
                parent=self)
            return

        geom_rules = load_resistor_geometry_rules(self.library)
        dlg = PlaceResistorDialog(self, res.ref_des, library=self.library,
                                   default_conductor_layer=geom_rules["conductor_layer"],
                                   default_x=self._default_place_x(sol, geom_rules),
                                   default_y=self._next_place_y)
        self.wait_window(dlg)
        if dlg.result is None:
            return
        x, y, rotation, conductor_layer = dlg.result
        try:
            self.app.session.generate_resistor(
                x, y, sol.body_width, sol.body_length, rotation=rotation,
                ink_rsh=sol.ink_rsh, ref_des=res.ref_des, conductor_layer=conductor_layer)
        except LayoutError as e:
            dialogs.show_error("Place in Layout", str(e), parent=self)
            return
        self.app.refresh_all()
        self._advance_place_cursor(x, sol, geom_rules)
        self.status_var.set(
            f"{res.ref_des} placed at ({dbu_to_mm(x):g}, {dbu_to_mm(y):g}) mm  "
            f"W={dbu_to_mm(sol.body_width):g} mm  L={dbu_to_mm(sol.body_length):.3f} mm")

    def _place_all_in_layout(self):
        """Place every resistor that has a chosen solution, one after
        another in a single row starting from the current placement
        cursor, each spaced clear of the last by its own footprint plus
        a margin -- so a whole resistor list can be dropped into the
        layout in one step without any of them overlapping. Resistors
        with no solution selected are skipped and reported. All
        placements share the batch's own per-resistor undo step (each
        Session.generate_resistor() call is already one step); this
        does not additionally group them into a single combined step,
        so an accidental "Place All" can still be undone resistor by
        resistor."""
        if self.app is None:
            dialogs.show_info(
                "Place All in Layout",
                "This resistor designer isn't attached to a layout window.",
                parent=self)
            return
        sized = [r for r in self.resistors if r.selected is not None]
        skipped = [r for r in self.resistors if r.selected is None]
        if not sized:
            dialogs.show_info(
                "Place All in Layout",
                "No resistors have a solution selected yet -- pick one for "
                "each on the Solutions tab (Use This Solution) first.",
                parent=self)
            return

        geom_rules = load_resistor_geometry_rules(self.library)
        placed = 0
        for res in sized:
            sol = res.selected
            x, y = self._default_place_x(sol, geom_rules), self._next_place_y
            try:
                self.app.session.generate_resistor(
                    x, y, sol.body_width, sol.body_length,
                    ink_rsh=sol.ink_rsh, ref_des=res.ref_des)
            except LayoutError as e:
                dialogs.show_error("Place All in Layout",
                                      f"{res.ref_des}: {e}", parent=self)
                break
            self._advance_place_cursor(x, sol, geom_rules)
            placed += 1
        self.app.refresh_all()
        msg = f"placed {placed} resistor(s) in a row, starting after the last placement"
        if skipped:
            msg += f"  |  skipped (no solution): {', '.join(r.ref_des for r in skipped)}"
        self.status_var.set(msg)

    # -- I/O -----------------------------------------------------------------
    def _new(self):
        if dialogs.ask_yes_no("New", "Clear all resistors?", parent=self):
            self.resistors.clear()
            self._refresh_all()
            self.sol_panel.clear()

    def _import_csv(self):
        path = filedialog.askopenfilename(
            parent=self, title="Import CSV",
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")])
        if not path:
            return
        try:
            added = 0
            with open(path, newline="") as f:
                for row in csv.DictReader(f):
                    ref = row.get("ref_des", "R?").strip()
                    val = float(row.get("value", 1000))
                    tol = row.get("tolerance", "5%").strip()
                    pwr = float(row.get("power_mw", 125.0))
                    w = float(row.get("body_width", width_for_power(pwr)))
                    res = Resistor(ref_des=ref, value=val, tolerance=tol,
                                   power_mw=pwr, body_width=w)
                    res.compute_solutions(self.rules)
                    self.resistors.append(res)
                    added += 1
            self._refresh_all()
            self.status_var.set(f"Imported {added} resistors.")
        except (OSError, ValueError, KeyError) as ex:
            dialogs.show_error("Import Error", str(ex), parent=self)

    def _export_csv(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Export CSV", initialfile="resistors.csv",
            defaultextension=".csv", filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", newline="") as f:
                cw = csv.writer(f)
                cw.writerow(["ref_des", "value", "tolerance", "power_mw", "body_width",
                             "ink_rsh", "squares", "body_length", "aspect_ratio",
                             "n_solutions", "power_ok"])
                for res in self.resistors:
                    sol = res.selected
                    cw.writerow([
                        res.ref_des, res.value, res.tolerance,
                        res.power_mw, res.body_width,
                        sol.ink_rsh if sol else "",
                        f"{sol.squares:.4f}" if sol else "",
                        f"{sol.body_length:.2f}" if sol else "",
                        f"{sol.aspect_ratio:.4f}" if sol else "",
                        len(res.solutions),
                        "YES" if res.power_ok else "NO",
                    ])
            self.status_var.set(f"Exported to {path}")
        except OSError as ex:
            dialogs.show_error("Export Error", str(ex), parent=self)

    # -- report / "print" --------------------------------------------------
    def _build_report_html(self) -> str:
        """Build the same full HTML report the original tool printed via
        Qt's QPrinter. Tk has no native print dialog, so Print / Preview
        Report... opens this in the system browser instead -- printing
        from there (Ctrl+P) is the standard cross-platform substitute."""
        rows_html = []
        for res in self.resistors:
            sol_count = len(res.solutions)
            pok = res.power_ok
            p_style = "color:#2da44e;" if pok else "color:#cf222e; font-weight:bold;"
            rows_html.append(
                f'<tr class="res-header"><td colspan="9">'
                f'<b>{res.ref_des}</b>&nbsp;&nbsp;'
                f'{res.value_label}&nbsp;&nbsp;{res.tolerance}&nbsp;&nbsp;'
                f'Aim:&nbsp;<b>{res.aim_value_label(self.rules)}</b>&nbsp;&nbsp;'
                f'Power:&nbsp;<span style="{p_style}">{res.power_mw:.1f}&nbsp;mW'
                f'{"" if pok else "&nbsp;(!)"}</span>&nbsp;&nbsp;'
                f'Width:&nbsp;{res.width_label}&nbsp;&nbsp;'
                f'({sol_count} solution{"s" if sol_count != 1 else ""})'
                f'</td></tr>'
            )
            if not res.solutions:
                rows_html.append(
                    '<tr><td colspan="9" style="color:#cf222e; padding-left:2em;">'
                    'No valid solution found for this resistor</td></tr>'
                )
            else:
                rows_html.append(
                    '<tr class="sol-header"><td></td>'
                    '<td>Ink (Ω/□)</td><td>Squares</td><td>L (mm)</td>'
                    '<td>Width</td><td>Trim P (mW)</td><td>Ratio</td>'
                    '<td>Rec</td><td>Selected</td></tr>'
                )
                for i, sol in enumerate(res.solutions):
                    is_sel = (i == res.selected_idx)
                    is_rec = sol.recommended
                    trim_p = effective_power_for_width(int(sol.body_width))
                    if 0.9 <= sol.aspect_ratio <= 1.1:
                        ar_style = "color:#2da44e;"
                    elif sol.aspect_ratio < 0.5 or sol.aspect_ratio > 2.0:
                        ar_style = "color:#cf222e;"
                    else:
                        ar_style = "color:#b08800;"
                    row_class = "sol-selected" if is_sel else ("sol-rec" if is_rec else "sol-row")
                    rows_html.append(
                        f'<tr class="{row_class}">'
                        f'<td style="text-align:center">{"&gt;" if is_sel else ""}</td>'
                        f'<td>{_eng(sol.ink_rsh)}</td>'
                        f'<td>{sol.squares:.4f}</td>'
                        f'<td>{dbu_to_mm(sol.body_length):.3f}</td>'
                        f'<td>{sol.width_label}</td>'
                        f'<td>{trim_p:.0f}</td>'
                        f'<td style="{ar_style}">{sol.ar_label}</td>'
                        f'<td style="color:#b08800;">{"*" if is_rec else ""}</td>'
                        f'<td style="text-align:center; font-weight:bold; color:#1a7f37;">'
                        f'{"SELECTED" if is_sel else ""}</td>'
                        f'</tr>'
                    )
            rows_html.append('<tr class="spacer"><td colspan="9"></td></tr>')

        rows = "\n".join(rows_html)
        inks_str = ", ".join(_eng(r) for r in self.rules.available_inks)
        now = datetime.now().strftime("%Y-%m-%d  %H:%M")

        return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ font-family: 'Courier New', monospace; font-size: 10pt;
          margin: 15mm 12mm; color: #1a1a1a; }}
  h1   {{ font-size: 14pt; margin-bottom: 2px; }}
  .meta {{ font-size: 9pt; color: #555; margin-bottom: 12px; }}
  table {{ border-collapse: collapse; width: 100%; margin-bottom: 4px; }}
  td, th {{ padding: 3px 7px; font-size: 9.5pt; }}
  .res-header td {{
    background: #1c2333; color: #58a6ff;
    padding: 5px 8px; font-size: 10pt;
    border-top: 2px solid #30363d;
  }}
  .sol-header td {{
    background: #f0f0f0; font-weight: bold; font-size: 8.5pt;
    border-bottom: 1px solid #ccc; padding-left: 2em;
  }}
  .sol-row td   {{ padding-left: 2em; border-bottom: 1px solid #eee; }}
  .sol-rec td   {{ padding-left: 2em; background: #fffbe6;
                   border-bottom: 1px solid #eee; }}
  .sol-selected td {{ padding-left: 2em; background: #e6f4ea;
                      border-bottom: 1px solid #ccc; font-weight: bold; }}
  .spacer td    {{ height: 6px; }}
  .footer {{ font-size: 8pt; color: #888; margin-top: 16px;
             border-top: 1px solid #ccc; padding-top: 6px; }}
</style></head><body>
<h1>Thick-Film Resistor Designer -- Full Solutions Report</h1>
<div class="meta">
  Printed: {now}&nbsp;&nbsp;|&nbsp;&nbsp;
  Resistors: {len(self.resistors)}&nbsp;&nbsp;|&nbsp;&nbsp;
  Aim: {self.rules.aim_pct:.0f}% + tol&nbsp;&nbsp;|&nbsp;&nbsp;
  AR: {self.rules.ar_min}-{self.rules.ar_max}&nbsp;&nbsp;|&nbsp;&nbsp;
  Inks: {inks_str}&nbsp;&nbsp;|&nbsp;&nbsp;
  Power ratings: 50% trim-derated
</div>
<table><tbody>
{rows}
</tbody></table>
<div class="footer">
  &gt; = selected solution | * = recommended (closest to 1:1 AR) |
  SELECTED = active solution | Trim P = power rating after 50% width removal
</div>
</body></html>"""

    def _print_report(self):
        if not self.resistors:
            dialogs.show_info("Print / Preview Report", "No resistors to report.", parent=self)
            return
        html = self._build_report_html()
        fd, path = tempfile.mkstemp(suffix=".html", prefix="hlayout_resistor_report_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
        webbrowser.open(f"file://{path}")
        self.status_var.set(f"Report opened in browser -- use its Print (Ctrl+P) to print/save as PDF: {path}")

    def _about(self):
        tbl = "\n".join(
            f"  {w / 1000:.1f} mm   ->   <= {p:.0f} mW full  /  "
            f"<= {effective_max_power(p):.0f} mW trimmed"
            for w, p in POWER_WIDTH_TABLE
        )
        dialogs.show_info(
            "About",
            "Thick-Film Resistor Designer\n\n"
            "Aim value: nominal x (aim% + tolerance%)\n"
            f"  e.g. 1kΩ 5% tol, {self.rules.aim_pct:.0f}% aim -> "
            f"{1000 * (self.rules.aim_pct / 100 + 0.05):.0f} Ω\n"
            f"Aspect ratio: {self.rules.ar_min}  to  {self.rules.ar_max}\n\n"
            "Power ratings assume 50% width removal during laser trim.\n"
            "Width selected so resistor handles rated power at half width.\n\n"
            "Width / Power table (full / trim-derated):\n" + tbl +
            "\n\nActive inks (Ω/□):\n  " +
            ",  ".join(_eng(r) for r in self.rules.available_inks) +
            "\n\nDesign rules are stored in this design's technology file "
            "(Setup > Save Technology File...).",
            parent=self,
        )


def open_resistor_designer(app):
    """Tools > Thick-Film Resistor Designer... . Reuses a single window
    per App instance -- calling this again while one is already open
    just brings it to the front, instead of stacking up duplicates."""
    win = getattr(app, "_resistor_designer_window", None)
    if win is not None and win.winfo_exists():
        win.lift()
        win.focus_set()
        return win
    win = ResistorDesignerWindow(app, app.session.library, app=app)
    app._resistor_designer_window = win
    return win
