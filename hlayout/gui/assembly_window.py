"""Assembly Drawing tool -- Tools > Assembly Drawing...

One window that drives Session.generate_assembly_drawing(): the drawing
sheet and title block, the top view (scale picked automatically or
fixed), and the pin function / resistor tables, all placed into one
assembly cell. Every field starts from the design's saved assembly
settings, and Build saves them back, so the window (and a bare ASSEMBLY
command) reproduce the last drawing next time.
"""

import os
import tkinter as tk
import webbrowser
from tkinter import ttk

from ..core.errors import CellError, LayoutError
from ..core.layers import NUM_LAYERS
from ..scripting.assembly import (STANDARD_SCALES, _scale_text, guess_pin_count,
                                  load_assembly_settings, save_assembly_settings)

_AUTO = "Auto (largest that fits)"
_TITLE_FIELDS = (
    ("Title", "title", None),
    ("Dwg No", "dwg_no", ("Issue", "issue")),
    ("Drawn by", "drawn_by", ("Date", "drawn_date")),
    ("Checked by", "checked_by", ("Date", "checked_date")),
    ("Approved by", "approved_by", ("Date", "approved_date")),
)


class AssemblyWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.session = app.session
        self.title("Assembly Drawing")
        self.transient(master)
        self.minsize(760, 520)
        self.vars = {}
        self._build()
        self._loading = True
        self.reload()
        self._loading = False
        # vertical sheet -> tables below the view; horizontal -> beside it
        self._var("orientation").trace_add("write", lambda *_: self._on_orientation())
        # every field is saved (to the design, and its house-style fields
        # app-wide) shortly after it changes, and again on close
        self._save_job = None
        for key, var in self.vars.items():
            var.trace_add("write", lambda *_: self._schedule_save())
        self.fn_text.bind("<<Modified>>", self._on_fn_modified)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._fit_to_screen()
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<F5>", lambda e: self.build())

    # ------------------------------------------------------------- helpers

    def _fit_to_screen(self):
        self.update_idletasks()
        w = max(self.winfo_reqwidth(), 760)
        h = min(self.winfo_reqheight(), self.winfo_screenheight() - 90)
        self.geometry(f"{w}x{h}")

    def _layer_label(self, n):
        return f"{n}: {self.session.library.layers[n].name}"

    def _layer_choices(self):
        return [self._layer_label(n) for n in range(NUM_LAYERS)]

    @staticmethod
    def _layer_of(text):
        text = (text or "").strip()
        if not text:
            return None
        try:
            return int(text.split(":", 1)[0])
        except ValueError:
            return None

    def _var(self, key, kind=tk.StringVar):
        v = self.vars.get(key)
        if v is None:
            v = self.vars[key] = kind()
        return v

    def _entry(self, parent, key, row, col, width=12, label=None):
        if label:
            ttk.Label(parent, text=label).grid(row=row, column=col, sticky="e", padx=4, pady=3)
            col += 1
        e = ttk.Entry(parent, textvariable=self._var(key), width=width)
        e.grid(row=row, column=col, sticky="w", padx=4, pady=3)
        return e

    def _combo(self, parent, key, values, row, col, width=12, label=None):
        if label:
            ttk.Label(parent, text=label).grid(row=row, column=col, sticky="e", padx=4, pady=3)
            col += 1
        c = ttk.Combobox(parent, textvariable=self._var(key), values=values,
                         width=width, state="readonly")
        c.grid(row=row, column=col, sticky="w", padx=4, pady=3)
        return c

    def _say(self, text, error=False):
        self.msg_var.set(text)
        self.msg.configure(foreground="red" if error else "")

    # ------------------------------------------------------------------ UI

    def _build(self):
        btns = ttk.Frame(self)
        btns.pack(side="bottom", pady=(6, 12))
        ttk.Button(btns, text="Build  (F5)", command=self.build).pack(side="left", padx=6)
        ttk.Button(btns, text="Open Drawing", command=self.open_output).pack(side="left", padx=6)
        ttk.Button(btns, text="Print Preview", command=self.print_preview).pack(side="left", padx=6)
        ttk.Button(btns, text="Close", command=self.close).pack(side="left", padx=6)
        self.msg_var = tk.StringVar(value="")
        self.msg = ttk.Label(self, textvariable=self.msg_var, wraplength=740, justify="left")
        self.msg.pack(side="bottom", anchor="w", padx=12, pady=(4, 0))

        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True, padx=8, pady=8)
        left, right = ttk.Frame(body), ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=4)
        right.pack(side="left", fill="both", expand=True, padx=4)

        # --- sheet ---------------------------------------------------------
        sheet = ttk.Labelframe(left, text="Sheet")
        sheet.pack(fill="x", pady=(0, 6))
        self._combo(sheet, "size", ["A4", "A3"], 0, 0, 6, "Size:")
        self._combo(sheet, "orientation", ["landscape", "portrait"], 0, 2, 10, "Orientation:")
        self.layer_combo = self._combo(sheet, "layer", self._layer_choices(), 1, 0, 22, "Layer:")
        self.layer_combo.grid(columnspan=3)
        self.design_combo = self._combo(sheet, "design_cell", [], 2, 0, 22, "Design cell:")
        self.design_combo.grid(columnspan=3)
        self._entry(sheet, "cell_name", 3, 0, 22, "Drawing cell:").grid(columnspan=3)

        # --- title block ---------------------------------------------------------
        tb = ttk.Labelframe(left, text="Title block")
        tb.pack(fill="x", pady=(0, 6))
        for r, (label, key, extra) in enumerate(_TITLE_FIELDS):
            e = self._entry(tb, key, r, 0, 24 if extra else 40, label + ":")
            if extra is None:
                e.grid(columnspan=3)
            else:
                self._entry(tb, extra[1], r, 2, 12, extra[0] + ":")

        # --- top view ------------------------------------------------------------
        tv = ttk.Labelframe(right, text="Top view")
        tv.pack(fill="x", pady=(0, 6))
        self._combo(tv, "scale", [_AUTO] + [_scale_text(s) for s in STANDARD_SCALES],
                    0, 0, 22, "Scale:").grid(columnspan=3)
        self._entry(tv, "view_line_mm", 1, 0, 6, "Line (mm):")
        self._entry(tv, "view_hatch_mm", 1, 2, 6, "Hatch pitch (mm):")
        self._entry(tv, "view_refdes_mm", 2, 0, 6, "Ref-des (mm):")
        ttk.Checkbutton(tv, text="Scale label under view",
                        variable=self._var("view_label", tk.BooleanVar)).grid(
            row=2, column=2, columnspan=2, sticky="w", padx=4)
        ttk.Label(tv, text="Line widths and text are paper sizes -- the view is redrawn "
                           "to suit the scale.", foreground="gray", wraplength=360).grid(
            row=3, column=0, columnspan=4, sticky="w", padx=4, pady=(0, 3))

        # --- tables ----------------------------------------------------------------
        tbl = ttk.Labelframe(right, text="Tables")
        tbl.pack(fill="both", expand=True)
        self._combo(tbl, "layout", ["auto", "side", "bottom"], 0, 0, 8, "Placement:")
        self._entry(tbl, "table_text_mm", 0, 2, 6, "Text (mm):")
        ttk.Checkbutton(tbl, text="Resistor table (from the Resistor Designer)",
                        variable=self._var("resistor_table", tk.BooleanVar)).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=4, pady=3)
        ttk.Checkbutton(tbl, text="Bond diagram table (routed hybrid)",
                        variable=self._var("bond_table", tk.BooleanVar)).grid(
            row=1, column=2, columnspan=2, sticky="w", padx=4, pady=3)
        ttk.Checkbutton(tbl, text="Pin function table", command=self._sync_pins,
                        variable=self._var("pin_table", tk.BooleanVar)).grid(
            row=2, column=0, columnspan=4, sticky="w", padx=4, pady=3)
        self.pins_entry = self._entry(tbl, "pin_count", 3, 0, 6, "Pins:")
        self.group_entry = self._entry(tbl, "pins_per_group", 3, 2, 6, "Per group:")
        self.pins_hint = ttk.Label(tbl, text="", foreground="gray")
        self.pins_hint.grid(row=4, column=0, columnspan=4, sticky="w", padx=4)
        ttk.Label(tbl, text="Pin functions (one per line, e.g. 1 = VCC):").grid(
            row=5, column=0, columnspan=4, sticky="w", padx=4, pady=(6, 0))
        fn = ttk.Frame(tbl)
        fn.grid(row=6, column=0, columnspan=4, sticky="nsew", padx=4, pady=(0, 4))
        tbl.rowconfigure(6, weight=1)
        tbl.columnconfigure(3, weight=1)
        self.fn_text = tk.Text(fn, width=34, height=7, undo=True)
        sb = ttk.Scrollbar(fn, orient="vertical", command=self.fn_text.yview)
        self.fn_text.configure(yscrollcommand=sb.set)
        self.fn_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")

    # ------------------------------------------------------------ persistence

    def _on_fn_modified(self, _event=None):
        if self.fn_text.edit_modified():
            self.fn_text.edit_modified(False)
            self._schedule_save()

    def _schedule_save(self):
        if getattr(self, "_loading", False):
            return
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(400, self.save_settings)

    def save_settings(self):
        """Save the form as it stands. A field that doesn't parse yet
        (half-typed number) is left at its last good value; everything
        else is still saved. Returns True when every field was valid."""
        self._save_job = None
        try:
            settings = self._collect()
            ok = True
        except ValueError:
            settings, ok = self._collect_valid_fields(), False
        save_assembly_settings(self.session.library, settings)
        return ok

    def close(self):
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        try:
            self.save_settings()
        finally:
            self.destroy()

    def _on_orientation(self):
        if getattr(self, "_loading", False):
            return
        portrait = self._var("orientation").get() == "portrait"
        self._var("layout").set("bottom" if portrait else "side")

    def _sync_pins(self):
        state = "normal" if self._var("pin_table", tk.BooleanVar).get() else "disabled"
        for w in (self.pins_entry, self.group_entry, self.fn_text):
            w.configure(state=state)

    # --------------------------------------------------------- load / save

    def reload(self):
        prev, self._loading = getattr(self, "_loading", False), True
        try:
            self._reload()
        finally:
            self._loading = prev

    def _reload(self):
        lib = self.session.library
        st = load_assembly_settings(lib)
        cells = sorted(lib.cells)
        self.design_combo["values"] = cells
        self.layer_combo["values"] = self._layer_choices()
        design = st["design_cell"] or lib.top_cell_name or ""
        self._var("design_cell").set(design)
        layer = st["layer"]
        if layer is None:
            from ..io.techfile import load_topview_settings
            layer = load_topview_settings(lib).get("result_layer", -1)
        try:
            layer = lib.layers.resolve(layer) if layer is not None and layer != -1 else None
        except Exception:
            layer = None
        self._var("layer").set(self._layer_label(layer) if layer is not None else "")
        for key in ("size", "orientation", "cell_name", "layout", "title", "dwg_no", "issue",
                    "drawn_by", "drawn_date", "checked_by", "checked_date",
                    "approved_by", "approved_date"):
            self._var(key).set(st[key] or "")
        for key in ("view_line_mm", "view_hatch_mm", "view_refdes_mm", "table_text_mm"):
            self._var(key).set(f"{float(st[key]):g}")
        for key in ("view_label", "resistor_table", "pin_table", "bond_table"):
            self._var(key, tk.BooleanVar).set(bool(st.get(key, True)))
        self._var("scale").set(_scale_text(float(st["scale"])) if st["scale"] else _AUTO)
        self._var("pin_count").set(str(st["pin_count"] or ""))
        self._var("pins_per_group").set(str(st["pins_per_group"] or 10))
        guess = guess_pin_count(lib, design) if design in lib.cells else None
        self.pins_hint.configure(text=f"Blank pins = from the package ({guess} found)"
                                 if guess else "Blank pins = from the package (none found)")
        self.fn_text.configure(state="normal")
        self.fn_text.delete("1.0", "end")
        for pin, fn in sorted((st["pin_functions"] or {}).items()):
            self.fn_text.insert("end", f"{pin} = {fn}\n")
        self._sync_pins()

    def _field_parsers(self):
        """{setting: parser()} -- each parser returns the field's value or
        raises ValueError with a readable message."""
        g = lambda k: self._var(k).get().strip()

        def text(key):
            return lambda: g(key)

        def mm(key, name):
            def parse():
                try:
                    v = float(g(key))
                except ValueError:
                    raise ValueError(f"{name} must be a number (mm).")
                if v < 0 or (v == 0 and key != "view_hatch_mm"):
                    raise ValueError(f"{name} must be greater than zero.")
                return v
            return parse

        def flag(key):
            return lambda: bool(self._var(key, tk.BooleanVar).get())

        def whole(key, name, blank):
            def parse():
                t = g(key)
                if not t:
                    return blank
                try:
                    n = int(t)
                except ValueError:
                    raise ValueError(f"{name} must be a whole number.")
                if n <= 0:
                    raise ValueError(f"{name} must be greater than zero.")
                return n
            return parse

        def layer():
            n = self._layer_of(g("layer"))
            if n is None:
                raise ValueError("Pick a drawing layer.")
            return n

        def scale():
            t = g("scale")
            if not t or t == _AUTO:
                return None
            a, b = t.split(":")
            return float(a) / float(b)

        def pin_functions():
            fns = {}
            for i, line in enumerate(self.fn_text.get("1.0", "end").splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                sep = "=" if "=" in line else (":" if ":" in line else None)
                if sep is None:
                    raise ValueError(f"Pin functions line {i}: write it as  <pin> = <function>")
                pin, fn = line.split(sep, 1)
                try:
                    fns[int(pin.strip())] = fn.strip()
                except ValueError:
                    raise ValueError(f"Pin functions line {i}: {pin.strip()!r} isn't a pin number")
            return fns or None

        p = {k: text(k) for k in ("size", "orientation", "layout", "title", "dwg_no", "issue",
                                  "drawn_by", "drawn_date", "checked_by", "checked_date",
                                  "approved_by", "approved_date")}
        p["cell_name"] = lambda: g("cell_name") or "ASSEMBLY"
        p["design_cell"] = lambda: g("design_cell") or None
        p["layer"] = layer
        p["view_line_mm"] = mm("view_line_mm", "Line")
        p["view_hatch_mm"] = mm("view_hatch_mm", "Hatch pitch")
        p["view_refdes_mm"] = mm("view_refdes_mm", "Ref-des height")
        p["table_text_mm"] = mm("table_text_mm", "Table text")
        for k in ("view_label", "resistor_table", "pin_table", "bond_table"):
            p[k] = flag(k)
        p["scale"] = scale
        p["pin_count"] = whole("pin_count", "Pins", None)
        p["pins_per_group"] = whole("pins_per_group", "Per group", 10)
        p["pin_functions"] = pin_functions
        return p

    def _collect(self):
        """The window's fields as generate_assembly_drawing() settings;
        raises ValueError (first bad field) with a readable message."""
        return {k: parse() for k, parse in self._field_parsers().items()}

    def _collect_valid_fields(self):
        """Only the fields that parse, for saving a half-filled form."""
        good = {}
        for k, parse in self._field_parsers().items():
            try:
                good[k] = parse()
            except ValueError:
                pass
        return good

    # ------------------------------------------------------------- actions

    def build(self):
        try:
            settings = self._collect()
        except ValueError as e:
            self._say(str(e), error=True)
            return
        try:
            r = self.session.generate_assembly_drawing(**settings)
        except (LayoutError, CellError) as e:
            self._say(str(e), error=True)
            return
        layer = self.session.library.layers[settings["layer"]]
        if not layer.visible:
            layer.visible = True
        self.app.refresh_all()
        text = (f"Built {r['cell']}: top view at {_scale_text(r['scale'])}, "
                f"tables {r['layout']} ({', '.join(sorted(r['parts']))}).")
        if r["warnings"]:
            text += "\n" + "\n".join(r["warnings"])
        self._say(text, error=False)
        self.app.status.set(f"ASSEMBLY: built {r['cell']} at {_scale_text(r['scale'])}")
        self.open_output()

    def _output_name(self):
        return self._var("cell_name").get().strip() or "ASSEMBLY"

    def open_output(self):
        name = self._output_name()
        if name not in self.session.library.cells:
            self._say(f"There is no {name} cell yet -- Build first.", error=True)
            return
        self.session.use_cell(name)
        self.app.canvas.set_cell(name)
        try:
            self.app.cell_var.set(name)
        except Exception:
            pass
        self.app.canvas.fit_view()

    def print_preview(self):
        name = self._output_name()
        if name not in self.session.library.cells:
            self._say(f"There is no {name} cell yet -- Build first.", error=True)
            return
        try:
            path = self.session.print_cell(name)
        except Exception as e:
            self._say(f"Print preview failed: {e}", error=True)
            return
        webbrowser.open("file:///" + os.path.abspath(path).replace(os.sep, "/"))
        self._say(f"Print preview written to {path}")


def open_assembly_window(app):
    """Tools > Assembly Drawing... . One window per App: calling again
    brings the existing one to the front, reloaded from the design."""
    win = getattr(app, "_assembly_window", None)
    if win is not None and win.winfo_exists():
        win.reload()
        win.lift()
        win.focus_set()
        return win
    win = AssemblyWindow(app, app)
    app._assembly_window = win
    return win
