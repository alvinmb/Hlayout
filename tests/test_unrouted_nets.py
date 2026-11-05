# HLayout - thick-film hybrid circuit layout
# Copyright (C) 2026 Alvin Brown
# SPDX-License-Identifier: GPL-3.0-or-later

"""View > Unrouted Nets / UNROUTED: pick one unrouted net, show its SLI
thick and zoom to it."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")


def _app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    from hlayout.io.techfile import load_route_rules, save_net_names, save_route_rules
    try:
        lib = Library("UNR")
        top = lib.new_cell("TOP")
        rr = load_route_rules(lib); rr["layer_a"] = 3; rr["layer_b"] = 5
        save_route_rules(lib, rr)
        # A: two pads 20 mm apart, routed; B: two pads 1 mm apart, open;
        # C: three pads far apart, open
        top.add_rectangle(3, -200, -200, 200, 200, pin="1")
        top.add_rectangle(3, 19800, -200, 20200, 200, pin="2")
        top.add_path(3, [(0, 0), (20000, 0)], 200)
        top.add_rectangle(3, 40000, 40000, 40400, 40400, pin="3")
        top.add_rectangle(3, 41400, 40000, 41800, 40400, pin="4")
        for i, (x, y) in enumerate([(0, 60000), (30000, 60000), (60000, 60000)]):
            top.add_rectangle(3, x - 200, y - 200, x + 200, y + 200, pin=str(5 + i))
        save_net_names(lib, {"IO.1": "A", "IO.2": "A", "IO.3": "B", "IO.4": "B",
                             "IO.5": "C", "IO.6": "C", "IO.7": "C"})
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    app.canvas.fit_view()
    return app


def test_window_lists_only_open_nets_and_zooms_to_the_pick():
    from hlayout.gui.unrouted_window import open_unrouted_window
    app = _app()
    try:
        win = open_unrouted_window(app)
        assert [n["net"] for n in win.nets] == ["C", "B"]           # longest first, A done
        assert "2 unrouted, 1 complete" == win.status_var.get()
        cv = app.canvas
        before = cv.scale
        e = win.select("B")
        assert e["net"] == "B" and cv.sli_focus == "B" and cv.show_sli
        assert cv.scale > before * 5                                 # zoomed right in
        x0, y0 = cv.to_px(40200, 40200)
        x1, y1 = cv.to_px(41600, 40200)
        w, h = cv.winfo_width(), cv.winfo_height()
        assert 0 < x0 < w and 0 < x1 < w and 0 < y0 < h               # both ends in view
        assert cv.find_withtag("sli")
        assert win.step(1)["net"] == "C"                             # wraps round
        assert win.step(1)["net"] == "B"
        win.close()
        assert cv.sli_focus is None
    finally:
        app.destroy()


def test_unrouted_command():
    from hlayout.gui.commands import CommandInterpreter
    from hlayout.scripting import ScriptConsole
    app = _app()
    try:
        ci = CommandInterpreter(app.session, ScriptConsole(app.session), view=app.canvas)
        assert ci.run("UNROUTED") == "2 unrouted: C (2), B (1)"
        assert ci.run("UNROUTED b").startswith("B: 1 gap(s), 1.00 mm")
        assert app.canvas.sli_focus == "B"
        assert ci.run("UNROUTED NEXT").startswith("C: 2 gap(s)")
        assert ci.run("UNROUTED CLEAR") == "unrouted net pick cleared"
        assert app.canvas.sli_focus is None
    finally:
        app.destroy()
