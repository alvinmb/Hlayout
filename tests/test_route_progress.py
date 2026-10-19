import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hlayout.io.route_progress import RouteProgress

# real Freerouting log lines (prefix kept on some, as the window gets them)
LOG = [
    "2026-10-18 15:37:25.100 INFO   [2E10DF\\69EB8F] Loading board file 'TOP.dsn'...",
    "Fanout pass #5 on board 'a' completed in 0.44 seconds with 17 SMD pins fanouted, 4 not routed, "
    "0 insert errors, +0 extra vias (0 SMD pins still to check in pass, ripup costs=500).",
    "Fanout stage completed: started with 256 total SMD pins, completed in 16.69 seconds, "
    "escaped pins: 242/256 (94.5%)",
    "Auto-routing stage started on board 'b' with baseline score 179.34 for 125 unrouted items.",
    "Auto-routing pass #1 on board 'c' was completed in 9.00 seconds with score 500.00 "
    "(100 unrouted and 3 violations)",
    "Auto-routing pass #2 on board 'd' was completed in 9.00 seconds with score 900.00 "
    "(25 unrouted and 0 violations)",
    "Auto-routing pass #3 on board 'd' was completed in 9.00 seconds with score 1000.00 "
    "(0 unrouted and 0 violations)",
    "Auto-routing stage completed: started with 171 unrouted nets, completed in 39.73 seconds",
    "Optimization stage started on board 'e'. Baseline router score: 1000.00, optimizer score: "
    "174.68, incomplete connections: 0, clearance violations: 0.",
    "Optimizer pass #1 on board 'f' was completed in 49.27 seconds with the score of 194.53 "
    "(0 unrouted and 0 violations).",
    "Optimizer pass #2 candidate rejected: worse. Restoring incumbent (optimizer score 190.00, "
    "incomplete connections: 0, clearance violations: 0).",
    "Optimization stage completed:. Baseline router score: 1000.00",
    "Successfully saved output file 'TOP.ses' (76905 bytes).",
]


def test_percent_rises_through_the_stages_and_never_falls():
    p = RouteProgress()
    seen = []
    for line in LOG:
        p.feed(line)
        seen.append(round(p.percent, 1))
    assert seen == sorted(seen)                      # monotonic
    assert seen[1] == 2.5                            # fanout pass 5 of 20 -> 10% * 5/20
    assert seen[3] == 10.0                           # autorouting begins
    assert seen[4] == 26.0                           # 25 of 125 routed
    assert seen[5] == 74.0                           # 100 of 125
    assert seen[6] == 90.0                           # all routed
    assert 90.0 < seen[9] < 99.0                     # optimizing
    assert seen[10] == seen[9]                       # a rejected candidate isn't progress
    assert seen[-2] == 99.0 and seen[-1] == 100.0


def test_text_says_where_it_is():
    p = RouteProgress()
    assert p.text().startswith("Routing 0% -- loading the board -- 0:0")
    for line in LOG[:5]:
        p.feed(line)
    assert p.text().startswith("Routing 26% -- pass 1, 100 of 125 connections left -- ")
    for line in LOG[5:10]:
        p.feed(line)
    assert "all routed -- optimizing (pass 1)" in p.text()


def test_unrelated_lines_change_nothing():
    p = RouteProgress()
    assert not p.feed("WARN   BoxShapeSearchTree.complete_shape: unexpected shapeToBeContained")
    assert p.percent == 0


def test_a_known_optimizer_pass_count_is_counted_off():
    p = RouteProgress(opt_passes=4)
    for line in LOG[:9]:
        p.feed(line)
    p.feed(LOG[9])                                   # optimizer pass 1 of 4
    assert p.percent == 90.0 + 9.0 * 0.25
    assert "optimizing (pass 1 of 4)" in p.text()
