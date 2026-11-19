# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""Hang watchdog: when the Tk main loop stops turning over for
``stall_s`` seconds (the window shows "Not responding"), write every
thread's Python stack to ``hang_traceback.txt`` -- next to hlayout_app.py
and in ~/.hlayout -- so the line it is stuck on can be found. Written once
per stall; costs one Tk timer a second and a sleeping thread."""

import os
import sys
import threading
import time
import traceback


def _dump(paths, stalled_for):
    frames = sys._current_frames()
    names = {t.ident: t.name for t in threading.enumerate()}
    lines = [f"HLayout not responding for {stalled_for:.0f} s -- "
             f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n"]
    for ident, frame in frames.items():
        lines.append(f"\n--- thread {names.get(ident, ident)} ---\n")
        lines.extend(traceback.format_stack(frame))
    text = "".join(lines)
    for p in paths:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass


def start(app, stall_s: float = 15.0, paths=None):
    if paths is None:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        paths = [os.path.join(root, "hang_traceback.txt"),
                 os.path.join(os.path.expanduser("~"), ".hlayout", "hang_traceback.txt")]
    state = {"beat": time.monotonic(), "dumped": False}

    def beat():
        state["beat"] = time.monotonic()
        state["dumped"] = False
        try:
            app.after(1000, beat)
        except Exception:
            pass

    def watch():
        while True:
            time.sleep(2.0)
            idle = time.monotonic() - state["beat"]
            if idle > stall_s and not state["dumped"]:
                state["dumped"] = True
                _dump(paths, idle)

    app.after(1000, beat)
    threading.Thread(target=watch, name="hang-watchdog", daemon=True).start()
    return paths
