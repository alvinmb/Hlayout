import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")

from hlayout.scripting import Session


class _Status:
    def __init__(self):
        self.text = ""

    def set(self, text):
        self.text = text


class _App:
    def __init__(self, session):
        self.session = session
        self.status = _Status()


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    r.withdraw()
    yield r
    r.destroy()


def _window(root):
    from hlayout.gui.freerouting_window import FreeroutingWindow
    s = Session.new("FR")
    s.new_cell("TOP")
    app = _App(s)
    return FreeroutingWindow(root, app), app


def test_closing_while_routing_hides_the_window_and_it_pops_back_when_done(root, tmp_path):
    root.geometry("1x1+-3000+-3000")   # a transient stays hidden while its master is
    root.deiconify()
    win, app = _window(root)
    win._running = True
    win.close()
    root.update()
    assert win.winfo_exists() and win.state() == "withdrawn"
    assert "background" in app.status.text
    win._finished(str(tmp_path / "none.ses"), 1, [], None)   # what the worker's after() calls
    root.update()
    assert win.winfo_exists() and win.state() != "withdrawn"   # back up, loudly
    assert win.banner.winfo_ismapped() and "FAILED" in win.banner.cget("text")
    assert "FAILED" in win.title() and "no session file" in app.status.text
    win.destroy()


def test_a_finished_route_says_so_in_the_banner(root, tmp_path):
    win, app = _window(root)
    win._do_import = lambda ses: {"wires": 476, "vias": 170, "skipped": 0, "nets": 85,
                                  "complete": 85, "unrouted": []}
    ses = tmp_path / "TOP.ses"
    ses.write_text("x")
    win._running = True
    win._finished(str(ses), 0, [], None)
    root.update()
    assert win.banner.cget("text").startswith("FINISHED in ")
    assert "all 85 nets complete (100%)" in win.banner.cget("text")
    assert win.banner.cget("bg") == win._BANNER_COLOURS["ok"]
    win._do_import = lambda ses: {"wires": 1, "vias": 0, "skipped": 0, "nets": 85,
                                  "complete": 83, "unrouted": ["GND", "VCC"]}
    win._finished(str(ses), 0, [], None)
    assert "83 of 85 nets complete (97%); unrouted: GND, VCC" in win.banner.cget("text")
    assert win.banner.cget("bg") == win._BANNER_COLOURS["warn"]
    win.destroy()


def test_a_late_router_callback_on_a_destroyed_window_is_ignored(root, tmp_path):
    win, _app = _window(root)
    win.destroy()
    win._say_router("Auto-routing pass #3 ...")
    win._finished(str(tmp_path / "none.ses"), 0, [], None)   # used to raise TclError


def test_stop_imports_nothing(root, tmp_path):
    win, app = _window(root)
    ses = tmp_path / "old.ses"
    ses.write_text("(session x (routes (resolution um 1) (network_out)))\n")
    win._running, win._cancel = True, True
    win._finished(str(ses), 1, [], None)
    assert "nothing imported" in app.status.text
    win.destroy()


def test_percent_complete_shows_on_the_bar_banner_and_title(root):
    from hlayout.io.route_progress import RouteProgress
    win, app = _window(root)
    win._running, win._prog = True, RouteProgress()
    win._say_router("2026-10-18 15:37:43.058 INFO   [2E10DF\\69EB8F] Auto-routing stage started on "
                    "board 'ab' with baseline score 179.34 for 120 unrouted items.")
    win._say_router("Auto-routing pass #4 on board 'cd' was completed in 3.10 seconds with score "
                    "640.00 (30 unrouted and 0 violations)")
    root.update()
    assert win.progress["value"] == pytest.approx(70.0)
    assert win.banner.cget("text").startswith("Routing 70% -- pass 4, 30 of 120 connections left")
    assert win.title().endswith("70%") and "70%" in app.status.text
    win.destroy()


def test_pass_fields_accept_zero_and_decimals(root):
    win, _app = _window(root)
    win.vars["passes"].set("0")
    win.vars["optimize_passes"].set("0")
    win.vars["optimize_threshold"].set("1.5")
    r = win._rules()
    assert (r["passes"], r["optimize_passes"], r["optimize_threshold"]) == (0, 0, 1.5)
    win.vars["passes"].set("-1")
    with pytest.raises(Exception):
        win._rules()
    win.destroy()



def test_a_finished_route_runs_via_optimisation_and_reports_it(root, tmp_path):
    win, app = _window(root)
    ses = tmp_path / "TOP.ses"
    ses.write_text("x")
    win._do_import = lambda s: {"wires": 300, "vias": 60, "skipped": 0, "nets": 46,
                                "complete": 46, "unrouted": []}
    win._optimize_vias = lambda: {"before": 60, "after": 38, "moved": 22, "removed": []}
    win._running = True
    win._finished(str(ses), 0, [], None)
    text = win.banner.cget("text")
    assert "all 46 nets complete" in text and "via optimisation 60 -> 38 vias" in text
    win.destroy()
