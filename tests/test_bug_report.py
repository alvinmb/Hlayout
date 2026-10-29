"""Help > Report Bug...: the activity log, the report contents and the
.zip bundle (hlayout/gui/bug_report.py)."""

import json
import os
import sys
import urllib.parse
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

tk = pytest.importorskip("tkinter")


def _make_app():
    from hlayout.gui.app import App
    from hlayout.core import Library
    try:
        lib = Library("BUGREPORT")
        cell = lib.new_cell("TOP")
        cell.add_rectangle(3, 0, 0, 1000, 500)
        app = App(lib)
    except tk.TclError as e:
        pytest.skip(f"no display available: {e}")
    app.update()
    return app


@pytest.fixture
def app(monkeypatch):
    from hlayout.gui import bug_report
    monkeypatch.setattr(bug_report, "_LOG", None)  # fresh log per test
    a = _make_app()
    yield a
    try:
        a.destroy()
    except tk.TclError:
        pass


def test_help_menu_has_report_bug(app):
    from hlayout.gui import bug_report
    # the menu bar is a row of buttons with their own tk.Menu each
    labels = []
    for w in app.winfo_children():
        stack = [w]
        while stack:
            x = stack.pop()
            stack.extend(x.winfo_children())
            if isinstance(x, tk.Menu):
                for i in range((x.index("end") or -1) + 1):
                    try:
                        labels.append(x.entrycget(i, "label"))
                    except tk.TclError:
                        pass
    assert "Report Bug..." in labels
    assert bug_report.get_log() is not None


def test_log_records_commands_status_and_errors(app):
    from hlayout.gui import bug_report
    log = bug_report.get_log()
    app.interp.run("FIT")
    app.status.set("hello from the test")
    try:
        raise ValueError("boom in a callback")
    except ValueError:
        log.exception(*sys.exc_info(), where="Tk callback")
    text = "\n".join(log.events)
    assert "command  FIT" in text
    assert "hello from the test" in text
    assert log.last_error()["summary"] == "ValueError: boom in a callback"
    assert "Traceback" in log.last_error()["traceback"]


def test_stderr_is_captured(app):
    # pytest swaps sys.stderr per test, so drive a tee directly; with no
    # real stderr behind it (the windowed build) it must still record.
    from hlayout.gui import bug_report
    log = bug_report.get_log()
    tee = bug_report._StderrTee(log, None)
    print("written to stderr", file=tee)
    assert "written to stderr" in log.stderr_text()


def test_report_text_and_issue_url(app):
    from hlayout.gui import bug_report
    try:
        raise RuntimeError("router failed")
    except RuntimeError:
        bug_report.get_log().exception(*sys.exc_info(), where="Tk callback")
    rep = bug_report.BugReport(app, summary="Autoroute crashes",
                               steps="1. open\n2. route", expected="tracks")
    text = rep.to_text()
    for s in ("Autoroute crashes", "HLayout", "Python", "current_cell: TOP",
              "RuntimeError: router failed", "== Recent activity =="):
        assert s in text
    url = rep.issue_url()
    assert url.startswith(bug_report.ISSUES_URL)
    assert len(url) <= bug_report.MAX_ISSUE_URL
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["title"] == ["Autoroute crashes"]
    assert "router failed" in q["body"][0]


def test_activity_can_be_left_out(app):
    from hlayout.gui import bug_report
    app.interp.run("FIT")
    rep = bug_report.BugReport(app, summary="x", include_activity=False)
    text = rep.to_text()
    assert "== Recent activity ==" not in text
    assert rep.to_json()["events"] == []


def test_home_folder_is_redacted():
    from hlayout.gui import bug_report
    home = os.path.expanduser("~")
    assert bug_report.redact(os.path.join(home, "x", "y.gds")).startswith("~")


def test_issue_url_is_trimmed_for_long_tracebacks(app):
    from hlayout.gui import bug_report
    log = bug_report.get_log()

    def deep(n):
        if n == 0:
            raise KeyError("deep " + "x" * 300)
        deep(n - 1)
    try:
        deep(60)
    except KeyError:
        log.exception(*sys.exc_info())
    rep = bug_report.BugReport(app, summary="long", steps="s" * 3000)
    assert len(rep.issue_url()) <= bug_report.MAX_ISSUE_URL


def test_zip_bundle_contents(app, tmp_path):
    from hlayout.gui import bug_report
    app.interp.run("FIT")
    rep = bug_report.BugReport(app, summary="bundle", include_design=True)
    path = rep.save_zip(str(tmp_path))
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        assert "report.txt" in names and "report.json" in names
        assert any(n.startswith("design/") and n.endswith(".gds") for n in names)
        data = json.loads(z.read("report.json"))
    assert data["summary"] == "bundle"
    assert data["design"]["cells"] == 1
    assert "FIT" in data["commands"]


def test_window_opens_and_reuses(app):
    from hlayout.gui.bug_report import open_bug_report_window
    w1 = open_bug_report_window(app, summary="prefilled")
    app.update()
    assert w1.summary_var.get() == "prefilled"
    w2 = open_bug_report_window(app)
    assert w1 is w2
    w1.destroy()
