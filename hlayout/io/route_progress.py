"""Percent-complete estimate for a running Freerouting job, worked out
from its log lines (the Freerouting CLI reports no progress of its own).

The run has three stages, weighted roughly by how long each takes:

  * fanout       0-10 %   one step per "Fanout pass #n" (it runs up to 20)
  * autorouting 10-90 %   connections routed so far, from the "N unrouted"
                          count each pass reports against the count the
                          stage started with
  * optimizing  90-99 %   each "Optimizer pass #n" closes part of the gap

100 % once the session file is saved. The estimate never goes backwards.
"""

import re
import time

_FANOUT_PASS = re.compile(r"Fanout pass #(\d+)")
_ROUTE_START = re.compile(r"Auto-routing stage started.*?for (\d+) unrouted item")
_ROUTE_PASS = re.compile(r"Auto-routing pass #(\d+).*?\((\d+) unrouted and (\d+) violation")
_OPT_PASS = re.compile(r"Optimizer pass #(\d+)\b(?!.*candidate)")

FANOUT_PASSES = 20


class RouteProgress:
    def __init__(self, max_passes=None, opt_passes=None):
        self.max_passes = int(max_passes) if max_passes else None
        #: optimizer pass limit (None/0 = unknown): with it the 90-99 %
        #: stage counts passes off against it instead of creeping
        self.opt_passes = int(opt_passes) if opt_passes else None
        self.percent = 0.0
        self.stage = "starting"
        self.route_pass = 0
        self.opt_pass = 0
        self.total = None          # unrouted connections when autorouting began
        self.left = None           # unrouted connections now
        self.violations = None
        self.started = time.monotonic()

    def _bump(self, p):
        self.percent = max(self.percent, min(100.0, float(p)))

    def feed(self, line: str) -> bool:
        """Take one log line; True if the estimate or stage changed."""
        before = (self.percent, self.stage, self.left, self.opt_pass)
        m = _FANOUT_PASS.search(line)
        if m:
            self.stage = "fanout"
            self._bump(10.0 * min(int(m.group(1)), FANOUT_PASSES) / FANOUT_PASSES)
        elif "Fanout stage completed" in line:
            self._bump(10.0)
        m = _ROUTE_START.search(line)
        if m:
            self.stage = "routing"
            self.total = self.left = int(m.group(1))
            self._bump(10.0)
        m = _ROUTE_PASS.search(line)
        if m:
            self.stage = "routing"
            self.route_pass = int(m.group(1))
            self.left, self.violations = int(m.group(2)), int(m.group(3))
            if not self.total:
                self.total = max(self.left, 1)
            done = 1.0 - self.left / float(self.total) if self.total else 0.0
            self._bump(10.0 + 80.0 * max(0.0, min(1.0, done)))
        if "Auto-routing stage completed" in line:
            self._bump(90.0 if not self.left else self.percent)
        if "Optimization stage started" in line:
            self.stage = "optimizing"
            self._bump(90.0)
        m = _OPT_PASS.search(line)
        if m and "candidate rejected" not in line:
            self.stage = "optimizing"
            self.opt_pass = max(self.opt_pass, int(m.group(1)))
            if self.opt_passes:
                self._bump(90.0 + 9.0 * min(1.0, self.opt_pass / float(self.opt_passes)))
            else:
                self._bump(99.0 - 9.0 * (0.75 ** self.opt_pass))
        if "Skipping optimization" in line or re.search(r"Optimization stage (completed|interrupted)", line):
            self.stage = "saving"
            self._bump(99.0)
        if "Successfully saved output file" in line or "finished with state: COMPLETED" in line:
            self.stage = "done"
            self._bump(100.0 if "saved output" in line else 99.0)
        return before != (self.percent, self.stage, self.left, self.opt_pass)

    def elapsed(self) -> str:
        s = int(time.monotonic() - self.started)
        return f"{s // 60}:{s % 60:02d}"

    def text(self) -> str:
        """One line for the window: 'Routing 63% -- pass 7, 46 of 125
        connections left -- 2:31'."""
        pct = f"{int(round(self.percent))}%"
        if self.stage == "fanout":
            what = "fanning out pins"
        elif self.stage == "routing":
            what = f"pass {self.route_pass}" if self.route_pass else "starting the router"
            if self.left is not None and self.total:
                what += f", {self.left} of {self.total} connections left"
        elif self.stage == "optimizing":
            of = f" of {self.opt_passes}" if self.opt_passes else ""
            what = f"all routed -- optimizing (pass {self.opt_pass}{of})" if self.opt_pass \
                else "all routed -- optimizing"
        elif self.stage in ("saving", "done"):
            what = "saving the result"
        else:
            what = "loading the board"
        return f"Routing {pct} -- {what} -- {self.elapsed()}"
