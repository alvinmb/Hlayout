# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Help > Report Bug...: collect what's needed to reproduce and fix a
problem, let the user describe it, and package it up.

Two halves:

* ``ActivityLog`` -- installed once at start-up (``install(app)``). It
  keeps a short, bounded record of what happened in this session so a
  report can show the lead-up to a bug, not just the end state:
    - every command-bar line run, and whether it raised,
    - status-bar messages (deduplicated),
    - every uncaught exception, with its full traceback -- from Tk
      callbacks (App._report_callback_exception), the main thread
      (sys.excepthook) and worker threads (threading.excepthook),
    - a tail of everything written to stderr. In the windowed build
      (console=False) stderr goes nowhere, so this is the only place
      that output survives.
  Everything is held in memory and capped, so it costs next to nothing
  when no one reports a bug.

* ``BugReportWindow`` -- the dialog. The user types a summary and the
  steps, picks what to include (recent activity, the open design, a
  screenshot) and then:
    - Save Report...       writes one .zip to ~/.hlayout/bug_reports,
    - Open GitHub Issue    opens a pre-filled issue in the browser
                           (the .zip is dragged onto the issue),
    - Copy Details         puts the text report on the clipboard.
  Preview shows exactly what will be sent before anything leaves the
  machine. The user's home folder is written as "~" in every path.

Nothing is uploaded automatically.
"""

from __future__ import annotations

import collections
import datetime as _dt
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
import webbrowser
import zipfile
import tkinter as tk
from tkinter import ttk

from .. import __version__
from . import dialogs

#: Where new issues are opened.
ISSUES_URL = "https://github.com/alvinmb/Hlayout/issues/new"
#: Where saved report bundles go.
REPORT_DIR = os.path.join(os.path.expanduser("~"), ".hlayout", "bug_reports")
#: Browsers and GitHub start refusing very long URLs well before 8 KB.
MAX_ISSUE_URL = 7000

_HOME = os.path.expanduser("~")


def redact(text: str) -> str:
    """Replace the user's home folder with "~" so usernames don't end up
    in a public issue."""
    if not text or not _HOME or len(_HOME) < 3:
        return text
    out = text.replace(_HOME, "~")
    alt = _HOME.replace("\\", "/")
    if alt != _HOME:
        out = out.replace(alt, "~")
    return out


def _now() -> str:
    return time.strftime("%H:%M:%S")


# --------------------------------------------------------------------------
# Activity log
# --------------------------------------------------------------------------

class _StderrTee(io.TextIOBase):
    """Copies everything written to stderr into the activity log, and
    still passes it on to the real stderr when there is one."""

    def __init__(self, log: "ActivityLog", original):
        self._log = log
        self._orig = original

    def write(self, s):
        try:
            self._log.stderr_write(s)
        except Exception:
            pass
        if self._orig is not None:
            try:
                return self._orig.write(s)
            except Exception:
                pass
        return len(s)

    def flush(self):
        if self._orig is not None:
            try:
                self._orig.flush()
            except Exception:
                pass

    @property
    def encoding(self):
        return getattr(self._orig, "encoding", "utf-8")

    def isatty(self):
        return False


class ActivityLog:
    MAX_EVENTS = 300
    MAX_ERRORS = 20
    MAX_STDERR_CHARS = 60_000

    def __init__(self):
        self.started = time.time()
        self.events = collections.deque(maxlen=self.MAX_EVENTS)
        self.errors = collections.deque(maxlen=self.MAX_ERRORS)
        self._stderr = collections.deque()
        self._stderr_len = 0
        self._last_status = None
        self._lock = threading.Lock()

    # -- recording -------------------------------------------------------
    def event(self, kind: str, text: str):
        with self._lock:
            self.events.append(f"{_now()}  {kind:<8} {text}")

    def status(self, text: str):
        text = (text or "").strip()
        if not text or text == self._last_status:
            return
        self._last_status = text
        self.event("status", text)

    def exception(self, exc, val, tb, where: str = "callback"):
        tb_text = "".join(traceback.format_exception(exc, val, tb))
        summary = "".join(traceback.format_exception_only(exc, val)).strip()
        with self._lock:
            self.errors.append({"time": _dt.datetime.now().isoformat(timespec="seconds"),
                                "where": where, "summary": summary,
                                "traceback": tb_text})
        self.event("ERROR", f"{where}: {summary}")

    def stderr_write(self, s: str):
        if not s:
            return
        with self._lock:
            self._stderr.append(s)
            self._stderr_len += len(s)
            while self._stderr_len > self.MAX_STDERR_CHARS and len(self._stderr) > 1:
                self._stderr_len -= len(self._stderr.popleft())

    # -- reading -----------------------------------------------------------
    def stderr_text(self) -> str:
        with self._lock:
            return "".join(self._stderr)

    def last_error(self):
        with self._lock:
            return self.errors[-1] if self.errors else None


_LOG: ActivityLog | None = None


def get_log() -> ActivityLog:
    global _LOG
    if _LOG is None:
        _LOG = ActivityLog()
    return _LOG


def install(app) -> ActivityLog:
    """Start recording for this session. Safe to call more than once."""
    log = get_log()
    if getattr(app, "_bug_log_installed", False):
        return log
    app._bug_log_installed = True

    # stderr (None in the windowed build -- the tee then just records)
    if not isinstance(sys.stderr, _StderrTee):
        sys.stderr = _StderrTee(log, sys.stderr)

    # uncaught exceptions on the main thread and on worker threads
    prev_hook = sys.excepthook

    def _excepthook(exc, val, tb):
        log.exception(exc, val, tb, where="main thread")
        try:
            prev_hook(exc, val, tb)
        except Exception:
            pass
    sys.excepthook = _excepthook

    prev_thook = getattr(threading, "excepthook", None)

    def _thread_hook(args):
        name = getattr(args.thread, "name", "thread")
        log.exception(args.exc_type, args.exc_value, args.exc_traceback,
                      where=f"thread {name}")
        if prev_thook is not None:
            try:
                prev_thook(args)
            except Exception:
                pass
    if prev_thook is not None:
        threading.excepthook = _thread_hook

    # command-bar lines
    interp = getattr(app, "interp", None)
    if interp is not None and not getattr(interp, "_bug_wrapped", False):
        orig_run = interp.run

        def run(line, *a, **kw):
            line_s = (line or "").strip()
            if line_s:
                log.event("command", line_s)
            try:
                return orig_run(line, *a, **kw)
            except Exception:
                log.exception(*sys.exc_info(), where=f"command '{line_s[:60]}'")
                raise
        interp.run = run
        interp._bug_wrapped = True

    # status-bar messages
    status = getattr(app, "status", None)
    if status is not None:
        try:
            status.trace_add("write", lambda *_: log.status(status.get()))
        except Exception:
            pass

    log.event("session", f"HLayout {__version__} started")
    return log


# --------------------------------------------------------------------------
# Collecting the report
# --------------------------------------------------------------------------

def _pkg_version(name: str) -> str:
    try:
        from importlib import metadata
        return metadata.version(name)
    except Exception:
        mod = sys.modules.get(name)
        return getattr(mod, "__version__", "not installed") if mod else "not installed"


def _git_commit() -> str | None:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if not os.path.isdir(os.path.join(root, ".git")):
        return None
    try:
        out = subprocess.run(["git", "-C", root, "describe", "--always", "--dirty", "--tags"],
                             capture_output=True, text=True, timeout=3,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return out.stdout.strip() or None
    except Exception:
        return None


def collect_environment(app=None) -> dict:
    env = {
        "hlayout": __version__,
        "build": "installer" if getattr(sys, "frozen", False) else "source",
        "git": _git_commit(),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "tk": None,
        "packages": {p: _pkg_version(p) for p in ("gdstk", "shapely", "numpy", "scipy", "Pillow")},
    }
    if app is not None:
        try:
            env["tk"] = str(app.tk.call("info", "patchlevel"))
            env["screen"] = f"{app.winfo_screenwidth()}x{app.winfo_screenheight()}"
            env["window"] = f"{app.winfo_width()}x{app.winfo_height()}"
            env["scaling"] = round(float(app.tk.call("tk", "scaling")), 3)
        except Exception:
            pass
    return env


def collect_design(app) -> dict:
    """What's open and what state it's in -- names and counts only, no
    geometry."""
    d = {}
    try:
        lib = app.session.library
        d["file"] = redact(app.current_path) if getattr(app, "current_path", None) else "(unsaved)"
        d["library"] = getattr(lib, "name", None)
        cells = getattr(lib, "cells", {})
        names = list(cells.keys()) if hasattr(cells, "keys") else [getattr(c, "name", "?") for c in cells]
        d["cells"] = len(names)
        d["cell_names"] = names[:40]
        d["current_cell"] = getattr(app.canvas, "cell_name", None)
        cell = cells.get(d["current_cell"]) if hasattr(cells, "get") else None
        if cell is not None:
            shapes = getattr(cell, "shapes", None)
            srefs = getattr(cell, "srefs", None) or getattr(cell, "references", None)
            d["current_cell_shapes"] = len(shapes) if shapes is not None else None
            d["current_cell_placements"] = len(srefs) if srefs is not None else None
            layers = collections.Counter(getattr(s, "layer", None) for s in (shapes or []))
            d["layers_used"] = {str(k): v for k, v in sorted(layers.items(), key=lambda kv: str(kv[0]))}
        tp = getattr(lib, "tech_params", None) or {}
        d["tech_params"] = sorted(tp.keys()) if isinstance(tp, dict) else None
        d["attached_libraries"] = [redact(p) for p in getattr(app.session, "_attached_paths", []) if p]
        d["undo_depth"] = len(getattr(app.session, "_undo_stack", []))
        try:
            from ..io.techfile import load_net_names
            d["named_pads"] = len(load_net_names(lib))
        except Exception:
            pass
    except Exception as e:
        d["error"] = f"couldn't read design state: {e!r}"
    try:
        from .canvas import describe_selection
        d["selection"] = describe_selection(app.canvas)[:600]
    except Exception:
        pass
    try:
        d["canvas_mode"] = getattr(app.canvas, "mode", None)
        d["mcp_bridge"] = getattr(app, "_mcp_bridge", None) is not None
    except Exception:
        pass
    return d


def _hang_files(max_age_days: float = 7.0):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = []
    for p in (os.path.join(root, "hang_traceback.txt"),
              os.path.join(_HOME, ".hlayout", "hang_traceback.txt")):
        try:
            if os.path.isfile(p) and time.time() - os.path.getmtime(p) < max_age_days * 86400:
                if not any(os.path.samefile(p, q) for q in out):
                    out.append(p)
        except OSError:
            pass
    return out


def _settings_text() -> str | None:
    try:
        from ..io import app_settings
        p = app_settings.SETTINGS_PATH
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return redact(f.read())
    except Exception:
        pass
    return None


class BugReport:
    """Everything gathered for one report."""

    def __init__(self, app, summary="", steps="", expected="",
                 include_activity=True, include_design=False, include_screenshot=False):
        self.app = app
        self.summary = summary.strip()
        self.steps = steps.strip()
        self.expected = expected.strip()
        self.include_activity = include_activity
        self.include_design = include_design
        self.include_screenshot = include_screenshot
        self.created = _dt.datetime.now()
        self.env = collect_environment(app)
        self.design = collect_design(app) if app is not None else {}
        log = get_log()
        self.events = list(log.events)
        self.errors = list(log.errors)
        self.stderr = redact(log.stderr_text())
        self.commands = list(getattr(getattr(app, "interp", None), "history", []) or [])[-50:]
        self.hang_files = _hang_files()

    # -- text forms ------------------------------------------------------------
    def env_lines(self):
        e = self.env
        pk = ", ".join(f"{k} {v}" for k, v in e.get("packages", {}).items())
        lines = [f"HLayout {e['hlayout']} ({e['build']}{', ' + e['git'] if e.get('git') else ''})",
                 f"OS: {e['os']} {e['machine']}",
                 f"Python {e['python']}, Tk {e.get('tk')}",
                 f"Packages: {pk}"]
        if e.get("screen"):
            lines.append(f"Screen {e['screen']}, window {e.get('window')}, Tk scaling {e.get('scaling')}")
        return lines

    def to_text(self) -> str:
        out = [f"HLayout bug report -- {self.created:%Y-%m-%d %H:%M:%S}", ""]
        out += ["== Summary ==", self.summary or "(none)", ""]
        out += ["== Steps to reproduce ==", self.steps or "(none)", ""]
        if self.expected:
            out += ["== Expected ==", self.expected, ""]
        out += ["== Environment =="] + self.env_lines() + [""]
        out += ["== Design =="]
        for k, v in self.design.items():
            out.append(f"{k}: {v}")
        out.append("")
        if self.include_activity:
            out += ["== Errors this session =="]
            if self.errors:
                for err in self.errors:
                    out += [f"[{err['time']}] {err['where']}: {err['summary']}",
                            redact(err["traceback"]).rstrip(), ""]
            else:
                out += ["(none recorded)", ""]
            out += ["== Recent commands =="] + (self.commands or ["(none)"]) + [""]
            out += ["== Recent activity =="] + [redact(e) for e in self.events[-120:]] + [""]
            if self.hang_files:
                out += ["== Hang reports ==",
                        *[f"{redact(p)} ({time.ctime(os.path.getmtime(p))})" for p in self.hang_files], ""]
        return "\n".join(out)

    def issue_body(self) -> str:
        """Markdown for the GitHub issue -- kept short enough for a URL;
        the full detail goes in the .zip."""
        parts = ["### What happened", self.summary or "_(describe the problem)_", "",
                 "### Steps to reproduce", self.steps or "1. \n2. \n3. ", ""]
        if self.expected:
            parts += ["### Expected", self.expected, ""]
        parts += ["### Environment", "```", *self.env_lines(), "```", ""]
        last = self.errors[-1] if (self.include_activity and self.errors) else None
        if last:
            tb = redact(last["traceback"]).rstrip().splitlines()
            parts += ["### Last error", f"{last['where']}: `{last['summary']}`",
                      "```", *tb[-25:], "```", ""]
        parts += ["_Bug report bundle (.zip from Help → Report Bug…): drag it onto this issue._"]
        return "\n".join(parts)

    def issue_url(self) -> str:
        title = self.summary.splitlines()[0][:120] if self.summary else "Bug report"
        body = self.issue_body()
        while True:
            url = (f"{ISSUES_URL}?labels=bug&title={urllib.parse.quote(title)}"
                   f"&body={urllib.parse.quote(body)}")
            if len(url) <= MAX_ISSUE_URL or len(body) < 400:
                return url
            # trim the traceback block first, then the body itself
            body = body[: int(len(body) * 0.8)].rstrip() + "\n\n_(truncated -- see the attached .zip)_"

    # -- the bundle --------------------------------------------------------------
    def save_zip(self, folder: str = None) -> str:
        folder = folder or REPORT_DIR
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"hlayout-bug-{self.created:%Y%m%d-%H%M%S}.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("report.txt", self.to_text())
            z.writestr("report.json", json.dumps(self.to_json(), indent=2, default=str))
            if self.include_activity:
                if self.stderr:
                    z.writestr("stderr.log", self.stderr)
                for p in self.hang_files:
                    try:
                        z.write(p, f"hang/{os.path.basename(os.path.dirname(p)) or 'app'}_{os.path.basename(p)}")
                    except OSError:
                        pass
                s = _settings_text()
                if s:
                    z.writestr("settings.json", s)
            if self.include_design and self.app is not None:
                self._add_design(z)
            if self.include_screenshot and self.app is not None:
                png = take_screenshot(self.app)
                if png:
                    z.writestr("screenshot.png", png)
        return path

    def _add_design(self, z: zipfile.ZipFile):
        tmp = tempfile.mkdtemp(prefix="hlayout_bug_")
        name = os.path.splitext(os.path.basename(self.app.current_path or "design.gds"))[0] or "design"
        gds = os.path.join(tmp, name + ".gds")
        try:
            self.app.session.save_design(gds)
            for fn in os.listdir(tmp):
                z.write(os.path.join(tmp, fn), f"design/{fn}")
        except Exception as e:
            z.writestr("design/ERROR.txt", f"couldn't save the design: {e!r}\n"
                                           + traceback.format_exc())
        finally:
            for fn in os.listdir(tmp):
                try:
                    os.remove(os.path.join(tmp, fn))
                except OSError:
                    pass
            try:
                os.rmdir(tmp)
            except OSError:
                pass

    def to_json(self) -> dict:
        return {"created": self.created.isoformat(timespec="seconds"),
                "summary": self.summary, "steps": self.steps, "expected": self.expected,
                "environment": self.env, "design": self.design,
                "errors": [dict(e, traceback=redact(e["traceback"])) for e in self.errors]
                if self.include_activity else [],
                "commands": self.commands if self.include_activity else [],
                "events": [redact(e) for e in self.events] if self.include_activity else []}


def take_screenshot(app) -> bytes | None:
    """PNG of the main window, if Pillow's ImageGrab works here
    (Windows and macOS; on Linux it needs X11). None otherwise."""
    try:
        from PIL import ImageGrab
    except Exception:
        return None
    try:
        app.update_idletasks()
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return None


def screenshot_available() -> bool:
    try:
        from PIL import ImageGrab  # noqa: F401
        return True
    except Exception:
        return False


def open_folder(path: str):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


# --------------------------------------------------------------------------
# The dialog
# --------------------------------------------------------------------------

class BugReportWindow(tk.Toplevel):
    def __init__(self, app, summary: str = ""):
        super().__init__(app)
        self.app = app
        self.title("Report Bug")
        self.transient(app)
        self.minsize(640, 560)
        self._last_zip = None

        outer = ttk.Frame(self, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        ttk.Label(outer, text="Describe the problem. HLayout adds the technical details "
                              "needed to reproduce it, and you can check everything with "
                              "Preview before it leaves your computer.",
                  wraplength=600, justify="left").grid(row=0, column=0, sticky="w", pady=(0, 12))

        ttk.Label(outer, text="Summary").grid(row=1, column=0, sticky="w")
        self.summary_var = tk.StringVar(value=summary)
        e = ttk.Entry(outer, textvariable=self.summary_var)
        e.grid(row=2, column=0, sticky="ew", pady=(2, 10))

        ttk.Label(outer, text="Steps to reproduce").grid(row=3, column=0, sticky="w")
        self.steps = tk.Text(outer, height=7, wrap="word", undo=True)
        self.steps.grid(row=4, column=0, sticky="nsew", pady=(2, 10))
        self.steps.insert("1.0", "1. \n2. \n3. ")
        outer.rowconfigure(4, weight=1)

        ttk.Label(outer, text="What did you expect to happen? (optional)").grid(row=5, column=0, sticky="w")
        self.expected = tk.Text(outer, height=3, wrap="word", undo=True)
        self.expected.grid(row=6, column=0, sticky="nsew", pady=(2, 10))

        inc = ttk.LabelFrame(outer, text="Include", padding=10)
        inc.grid(row=7, column=0, sticky="ew", pady=(0, 10))
        self.v_activity = tk.BooleanVar(value=True)
        self.v_design = tk.BooleanVar(value=False)
        self.v_shot = tk.BooleanVar(value=screenshot_available())
        log = get_log()
        n_err = len(log.errors)
        ttk.Checkbutton(inc, variable=self.v_activity,
                        text=f"Recent activity, errors and logs ({n_err} error"
                             f"{'' if n_err == 1 else 's'} this session)").pack(anchor="w")
        name = os.path.basename(app.current_path) if getattr(app, "current_path", None) else "unsaved design"
        ttk.Checkbutton(inc, variable=self.v_design,
                        text=f"The open design ({name}) -- only if it isn't confidential").pack(anchor="w")
        cb = ttk.Checkbutton(inc, variable=self.v_shot, text="Screenshot of the HLayout window")
        cb.pack(anchor="w")
        if not screenshot_available():
            cb.state(["disabled"])
            self.v_shot.set(False)
        ttk.Label(inc, text="Always included: HLayout, OS, Python and library versions, "
                            "and the design's cell and layer counts (no geometry).",
                  foreground="#5a6676", wraplength=560, justify="left").pack(anchor="w", pady=(6, 0))

        btns = ttk.Frame(outer)
        btns.grid(row=8, column=0, sticky="ew")
        ttk.Button(btns, text="Preview", command=self.preview).pack(side="left")
        ttk.Button(btns, text="Copy Details", command=self.copy).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Open GitHub Issue", command=self.open_issue).pack(side="right", padx=(0, 8))
        ttk.Button(btns, text="Save Report...", command=self.save).pack(side="right", padx=(0, 8))

        self.msg = tk.StringVar(value=f"Reports are saved in {redact(REPORT_DIR)}")
        ttk.Label(outer, textvariable=self.msg, foreground="#5a6676").grid(row=9, column=0, sticky="w", pady=(10, 0))

        self.bind("<Escape>", lambda ev: self.destroy())
        dialogs._center_over(self, app)
        e.focus_set()

    # -- helpers ------------------------------------------------------------------
    def _report(self) -> BugReport:
        return BugReport(self.app,
                         summary=self.summary_var.get(),
                         steps=self._clean(self.steps.get("1.0", "end")),
                         expected=self.expected.get("1.0", "end"),
                         include_activity=self.v_activity.get(),
                         include_design=self.v_design.get(),
                         include_screenshot=self.v_shot.get())

    @staticmethod
    def _clean(steps: str) -> str:
        lines = [ln for ln in steps.strip().splitlines() if ln.strip() not in ("1.", "2.", "3.")]
        return "\n".join(lines)

    def _need_summary(self) -> bool:
        if self.summary_var.get().strip():
            return True
        dialogs.show_info("Report Bug", "Add a one-line summary of the problem first.", parent=self)
        return False

    # -- actions --------------------------------------------------------------------
    def preview(self):
        rep = self._report()
        win = tk.Toplevel(self)
        win.title("Bug report preview")
        win.geometry("820x620")
        txt = tk.Text(win, wrap="none")
        ys = ttk.Scrollbar(win, orient="vertical", command=txt.yview)
        xs = ttk.Scrollbar(win, orient="horizontal", command=txt.xview)
        txt.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        txt.pack(fill="both", expand=True)
        extra = []
        if rep.include_design:
            extra.append("+ design/ (the open design as .gds with its technology sidecar)")
        if rep.include_screenshot:
            extra.append("+ screenshot.png")
        if rep.include_activity:
            extra.append("+ stderr.log, settings.json and any hang_traceback.txt from the last 7 days")
        txt.insert("1.0", rep.to_text() + ("\n\n== Also in the .zip ==\n" + "\n".join(extra) if extra else ""))
        txt.configure(state="disabled")

    def copy(self):
        text = self._report().to_text()
        self.clipboard_clear()
        self.clipboard_append(text)
        self.msg.set("Report text copied to the clipboard.")

    def save(self):
        if not self._need_summary():
            return None
        try:
            self.withdraw()  # keep this dialog out of the screenshot
            self.app.update()
            path = self._report().save_zip()
        except Exception as e:
            self.deiconify()
            dialogs.show_error("Report Bug", f"Couldn't save the report:\n{e}", parent=self)
            return None
        self.deiconify()
        self._last_zip = path
        self.msg.set(f"Saved {redact(path)}")
        get_log().event("report", f"saved {redact(path)}")
        open_folder(os.path.dirname(path))
        return path

    def open_issue(self):
        if not self._need_summary():
            return
        path = self._last_zip or self.save()
        if not path:
            return
        url = self._report().issue_url()
        try:
            webbrowser.open(url)
        except Exception:
            pass
        dialogs.show_info(
            "Report Bug",
            "A new GitHub issue has opened in your browser with the details filled in.\n\n"
            f"Drag {os.path.basename(path)} from the folder that just opened onto the "
            "issue, then press Submit.",
            parent=self)


def open_bug_report_window(app, summary: str = ""):
    win = getattr(app, "_bug_report_window", None)
    if win is not None:
        try:
            if win.winfo_exists():
                win.deiconify()
                win.lift()
                return win
        except tk.TclError:
            pass
    win = BugReportWindow(app, summary=summary)
    app._bug_report_window = win
    return win
