"""A minimal in-process Python console for driving a Session interactively.

Used by the GUI's Script Console dock, but works standalone too:

    from hlayout.scripting.api import Session
    from hlayout.scripting.console import ScriptConsole

    session = Session.new("DEMO")
    console = ScriptConsole(session)
    print(console.run("s.new_cell('TOP'); s.rect(0, 0, 0, 10, 10)"))
"""

import code
import io
import contextlib
from typing import Optional

from .api import Session


class ScriptConsole:
    """Executes user-supplied Python source against a Session.

    The session is bound to the name ``s`` (and ``hl`` as an alias) in
    the execution namespace, plus the shape/library classes for
    convenience, so scripts can both call the high-level Session API
    and, if needed, drop down to the core data model directly.
    """

    def __init__(self, session: Session, extra_globals: Optional[dict] = None):
        self.session = session
        ns = {
            "s": session,
            "hl": session,
            "library": session.library,
        }
        try:
            from ..core import (Cell, Library, Path, PathCap, Polygon,
                                  Rectangle, SRef, Text)
            ns.update(Cell=Cell, Library=Library, Path=Path, PathCap=PathCap,
                      Polygon=Polygon, Rectangle=Rectangle, SRef=SRef, Text=Text)
        except ImportError:
            pass
        if extra_globals:
            ns.update(extra_globals)
        self._interpreter = code.InteractiveInterpreter(ns)
        self.namespace = ns
        self.history = []
        self._history_index = 0

    def run(self, source: str) -> str:
        """Execute a block of source, returning captured stdout/stderr text."""
        self.history.append(source)
        self._history_index = len(self.history)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                compiled = compile(source, "<console>", "exec")
                exec(compiled, self.namespace)
            except Exception as exc:  # surfaced to the console output, not raised
                import traceback
                traceback.print_exc(file=out)
        return out.getvalue()

    def run_file(self, path: str) -> str:
        with open(path, "r", encoding="utf-8") as f:
            return self.run(f.read())

    # --- history navigation (Up/Down in the GUI console entry) -----------
    def prev_history(self, current: str) -> str:
        if not self.history:
            return current
        self._history_index = max(0, self._history_index - 1)
        return self.history[self._history_index]

    def next_history(self, current: str) -> str:
        if self._history_index >= len(self.history) - 1:
            self._history_index = len(self.history)
            return ""
        self._history_index += 1
        return self.history[self._history_index]
