# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""A busy/progress box for long-running jobs.

run_with_progress() runs ``work`` on a worker thread while a small
modal window shows a moving progress bar and the elapsed time (and the
App's status bar says what is running), so the main window keeps
repainting instead of Windows marking HLayout "Not Responding" and
closing it. ``on_done(result)`` / ``on_error(exc)`` are called back on
the Tk thread when the job finishes. ``work`` must not touch Tk.
"""

import threading
import time
import tkinter as tk
from tkinter import ttk


class ProgressBox(tk.Toplevel):
    def __init__(self, master, title, text):
        super().__init__(master)
        self.title(title)
        self.transient(master)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", lambda: None)   # can't close mid-job
        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=text).pack(anchor="w")
        self.bar = ttk.Progressbar(frm, mode="indeterminate", length=320)
        self.bar.pack(fill="x", pady=(10, 6))
        self.time_var = tk.StringVar(value="0 s")
        ttk.Label(frm, textvariable=self.time_var).pack(anchor="w")
        self.bar.start(12)
        try:
            self.update_idletasks()
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
            y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
            self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
            self.grab_set()
        except tk.TclError:
            pass

    def close(self):
        try:
            self.bar.stop()
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def run_with_progress(master, work, on_done=None, on_error=None,
                      title="Working", text="Working...", app=None, session=None):
    """Run ``work()`` in the background with a progress box over
    ``master``; returns the worker thread.

    ``session`` (default: ``app.session``) has its change callbacks held
    back while the job runs -- they redraw the canvas, which must only
    happen on the Tk thread -- and fired once, on the Tk thread, after."""
    if session is None:
        session = getattr(app, "session", None)
    held = None
    if session is not None and hasattr(session, "_on_change"):
        held = session._on_change
        session._on_change = []
    box = ProgressBox(master, title, text)
    status = getattr(app, "status", None)
    state = {}
    t0 = time.time()

    def worker():
        try:
            state["result"] = work()
        except BaseException as e:      # reported on the Tk thread
            state["error"] = e

    th = threading.Thread(target=worker, daemon=True)
    th.start()

    def poll():
        secs = int(time.time() - t0)
        try:
            box.time_var.set(f"{secs} s")
        except tk.TclError:
            pass
        if status is not None:
            try:
                status.set(f"{text}  {secs} s")
            except Exception:
                pass
        if th.is_alive():
            master.after(200, poll)
            return
        box.close()
        if held is not None:
            session._on_change = held + [cb for cb in session._on_change if cb not in held]
            try:
                session._notify()
            except Exception:
                pass
        if "error" in state:
            if on_error:
                on_error(state["error"])
            else:
                raise state["error"]
        elif on_done:
            on_done(state.get("result"))

    master.after(200, poll)
    return th
