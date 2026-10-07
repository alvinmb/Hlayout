"""The operations the MCP server exposes, written once against a small
``Context`` so the exact same code runs headless (inside the MCP server
process) and live (inside the GUI, via bridge.py).

Every operation takes ``(ctx, args: dict)`` and returns text. Failures
raise ``OpError`` (or any LayoutError), which the caller reports as a
tool error.
"""

import contextlib
import inspect
import io
import json
import traceback

MAX_OUTPUT_CHARS = 60000


class OpError(Exception):
    """An operation failed; str(e) is the message shown to the client."""


class Context:
    """What an operation may touch.

    ``session``  hlayout.scripting.Session (always present)
    ``console``  hlayout.scripting.ScriptConsole bound to that session
    ``interp``   gui.commands.CommandInterpreter, or None when the
                 command bar isn't available (headless without Tk)
    ``mode``     "live" or "headless"
    ``get_path`` / ``set_path``  the design's current file path
    """

    def __init__(self, session, console, interp=None, mode="headless",
                 get_path=None, set_path=None):
        self.session = session
        self.console = console
        self.interp = interp
        self.mode = mode
        self._path = None
        self._get_path = get_path
        self._set_path = set_path

    @property
    def path(self):
        return self._get_path() if self._get_path else self._path

    @path.setter
    def path(self, value):
        if self._set_path:
            self._set_path(value)
        else:
            self._path = value


def _dump(obj) -> str:
    return json.dumps(obj, indent=1, default=str)


def _clip(text: str) -> str:
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + f"\n... [truncated, {len(text)} chars total]"
    return text


# --- operations ----------------------------------------------------------
def op_status(ctx, args):
    from .. import __version__

    s = ctx.session
    lib = s.library
    names = sorted(lib.cells)
    named_layers = {
        layer.number: layer.name for layer in lib.layers
        if layer.name != f"L{layer.number}"
    }
    return _dump({
        "mode": ctx.mode,
        "hlayout_version": __version__,
        "file": ctx.path,
        "library": lib.name,
        "current_cell": s.current_cell_name,
        "top_cells": [c.name for c in lib.top_cells()],
        "cell_count": len(names),
        "cells": names[:200],
        "attached_libraries": [l.name for l in s.attached_libraries],
        "named_layers": named_layers,
        "can_undo": s.can_undo(),
        "can_redo": s.can_redo(),
        "units": "1 DBU = 1 micron (0.001 mm); coordinates are DBU",
        "command_bar_available": ctx.interp is not None,
    })


def op_open(ctx, args):
    from ..io.sidecar import load_design

    path = args.get("path")
    if not path:
        raise OpError("path is required")
    loaded = load_design(path)
    s = ctx.session
    s.library = loaded.library
    s.attached_libraries = loaded.attached_libraries
    s._attached_paths = loaded.attached_paths
    s.attach_errors = loaded.attach_errors
    s.had_sidecar = loaded.had_sidecar
    s.current_cell_name = loaded.library.top_cell_name
    s.clear_undo_history()
    # The console's convenience name `library` must follow the document.
    ctx.console.namespace["library"] = loaded.library
    ctx.path = path
    msg = f"opened {path}: library {loaded.library.name!r}, {len(loaded.library.cells)} cell(s)"
    if loaded.had_sidecar:
        msg += f", sidecar restored ({len(loaded.attached_libraries)} attached library/ies)"
    for err in loaded.attach_errors:
        msg += f"\nwarning: attached library failed to restore: {err}"
    return msg


def op_save(ctx, args):
    path = args.get("path") or ctx.path
    if not path:
        raise OpError("the design has no file yet; pass path")
    sidecar = ctx.session.save_design(path)
    ctx.path = path
    return f"saved {path} (+ sidecar {sidecar})"


def op_command(ctx, args):
    text = args.get("text") or ""
    if ctx.interp is None:
        raise OpError("the command bar is not available in this headless "
                      "session (Tkinter missing); use hlayout_python instead")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise OpError("text is required")
    out = []
    failed = False
    for line in lines:
        result = ctx.interp.run(line)
        failed = failed or result.startswith("error:")
        out.append(result if len(lines) == 1 else f"> {line}\n{result}")
        if failed:
            break  # don't keep building on top of a failed step
    text = _clip("\n".join(out))
    if failed:
        raise OpError(text)
    return text


def op_python(ctx, args):
    code = args.get("code") or ""
    if not code.strip():
        raise OpError("code is required")
    ns = ctx.console.namespace
    ns["library"] = ctx.session.library
    out = io.StringIO()
    failed = False
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            try:
                compiled, is_expr = compile(code, "<mcp>", "eval"), True
            except SyntaxError:
                compiled, is_expr = compile(code, "<mcp>", "exec"), False
            value = eval(compiled, ns) if is_expr else exec(compiled, ns)
            if is_expr and value is not None:
                print(repr(value))
        except Exception:
            traceback.print_exc()
            failed = True
    text = _clip(out.getvalue())
    if failed:
        raise OpError(text)
    return text or "ok (no output -- print() what you want to see)"


def op_api(ctx, args):
    from ..scripting.api import Session

    name = (args.get("name") or "").strip()
    if name.lower() == "commands":
        try:
            from ..gui.commands import COMMAND_REFERENCE
        except ImportError as e:
            raise OpError(f"command reference unavailable: {e}")
        return COMMAND_REFERENCE
    if name:
        member = getattr(Session, name, None)
        if member is None or name.startswith("_"):
            raise OpError(f"Session has no public member {name!r}")
        target = member.fget if isinstance(member, property) else member
        try:
            sig = str(inspect.signature(target))
        except (TypeError, ValueError):
            sig = ""
        return f"Session.{name}{sig}\n\n{inspect.getdoc(target) or '(no docstring)'}"
    lines = ["Session methods (in hlayout_python the session is `s`). "
             "Call hlayout_api with name=<method> for full docs, "
             "or name='commands' for the command-bar reference.", ""]
    for mname, member in inspect.getmembers(Session):
        if mname.startswith("_"):
            continue
        target = member.fget if isinstance(member, property) else member
        target = getattr(target, "__func__", target)
        if not callable(target):
            continue
        try:
            sig = str(inspect.signature(target))
        except (TypeError, ValueError):
            sig = "(...)"
        doc = (inspect.getdoc(target) or "").split("\n\n")[0].replace("\n", " ")
        if len(doc) > 160:
            doc = doc[:157] + "..."
        lines.append(f"{mname}{sig}" + (f"\n    {doc}" if doc else ""))
    return "\n".join(lines)


def op_describe_cell(ctx, args):
    s = ctx.session
    lib = s.library
    name = args.get("name") or s.current_cell_name
    if not name:
        raise OpError("no current cell; pass name")
    cell = lib.get_cell(name)
    limit = int(args.get("max_items") or 50)

    layers = {}
    for shape in cell.shapes:
        entry = layers.setdefault(shape.layer, {})
        kind = type(shape).__name__
        entry[kind] = entry.get(kind, 0) + 1
    layer_rows = [
        {"layer": n, "name": lib.layers[n].name, "shapes": counts}
        for n, counts in sorted(layers.items())
    ]
    bbox = lib.cell_bbox(name)
    info = {
        "cell": name,
        "bbox_dbu": bbox,
        "size_mm": ([round((bbox[2] - bbox[0]) * lib.dbu_mm, 4),
                     round((bbox[3] - bbox[1]) * lib.dbu_mm, 4)] if bbox else None),
        "own_shape_count": len(cell.shapes),
        "layers": layer_rows,
        "sref_count": len(cell.srefs),
        "srefs": [
            {"cell": r.cell_name, "origin": r.origin, "rotation": r.rotation,
             "label": r.label, "pin": r.pin}
            for r in cell.srefs[:limit]
        ],
        "aref_count": len(cell.arefs),
        "arefs": [
            {"cell": r.cell_name, "origin": r.origin, "columns": r.columns,
             "rows": r.rows, "col_spacing": r.col_spacing,
             "row_spacing": r.row_spacing, "rotation": r.rotation}
            for r in cell.arefs[:limit]
        ],
        "pins": [p for p in s.list_pins(name) if p["pin"]][:limit],
    }
    return _dump(info)


OPS = {
    "status": op_status,
    "open": op_open,
    "save": op_save,
    "command": op_command,
    "python": op_python,
    "api": op_api,
    "describe_cell": op_describe_cell,
}


def call(ctx, op: str, args: dict) -> str:
    """Run one operation; raises OpError on any failure."""
    handler = OPS.get(op)
    if handler is None:
        raise OpError(f"unknown operation {op!r}")
    try:
        return handler(ctx, args or {})
    except OpError:
        raise
    except Exception as e:
        raise OpError(f"{type(e).__name__}: {e}")


def local_context(design_path=None):
    """A headless Context around a fresh Session (optionally opened
    from ``design_path``)."""
    from ..scripting import ScriptConsole, Session

    session = Session.open_design(design_path) if design_path else Session.new("HLIB")
    console = ScriptConsole(session)
    interp = None
    try:  # hlayout.gui imports Tkinter; fine to do without it
        from ..gui.commands import CommandInterpreter
        interp = CommandInterpreter(session, console)
    except Exception:
        pass
    ctx = Context(session, console, interp, mode="headless")
    ctx.path = design_path
    return ctx
