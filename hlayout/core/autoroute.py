"""Grid maze router.

Connect two points on a single conductor layer with an orthogonal
(Manhattan) trace that dodges every other piece of copper already on
that layer.

The plane is discretised into a uniform grid whose pitch is the
design's current grid spacing. A grid node is *blocked* when it falls
inside any obstacle -- every other shape on the routing layer, grown by
``half the trace width + clearance`` so the routed centre-line keeps the
finished copper clear of its neighbours. A* (4-connected, with a small
turn penalty so the result prefers straight runs over staircases) finds
the shortest clear node path; collinear runs are then collapsed to a
short vertex list ready to hand to ``Session.path()``.

Pure / GUI-free so it can be unit-tested directly. Needs shapely only
for :func:`rasterize_blocked` (the A* itself is plain Python).
"""

import heapq

#: Hard ceiling on grid nodes (nx * ny). Past this the router refuses
#: rather than locking the UI up on a pathologically fine grid over a
#: large board -- the caller surfaces this as "use a coarser grid".
MAX_NODES = 400_000


def rasterize_blocked(obstacles, x0, y0, grid, nx, ny):
    """Return an ``nx x ny`` list-of-lists of bool: ``blocked[i][j]`` is
    True when the grid point ``(x0 + i*grid, y0 + j*grid)`` lies inside
    ``obstacles`` (any shapely geometry; an empty geometry blocks
    nothing)."""
    from shapely.geometry import Point
    from shapely.prepared import prep

    blocked = [[False] * ny for _ in range(nx)]
    if obstacles is None or obstacles.is_empty:
        return blocked
    pobs = prep(obstacles)
    for i in range(nx):
        px = x0 + i * grid
        col = blocked[i]
        for j in range(ny):
            if pobs.intersects(Point(px, y0 + j * grid)):
                col[j] = True
    return blocked


def clear_disc(blocked, ci, cj, radius_cells):
    """Force every node within ``radius_cells`` (Chebyshev) of
    ``(ci, cj)`` walkable -- used on the two endpoints so a pin that
    sits on its own copper is still reachable."""
    nx, ny = len(blocked), len(blocked[0])
    r = max(0, int(radius_cells))
    for i in range(max(0, ci - r), min(nx, ci + r + 1)):
        for j in range(max(0, cj - r), min(ny, cj + r + 1)):
            blocked[i][j] = False


_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def astar(blocked, start, goal, turn_penalty=2):
    """Shortest 4-connected path over ``blocked`` (list[list[bool]],
    indexed ``[i][j]``) from ``start`` to ``goal`` (each an ``(i, j)``
    tuple). ``turn_penalty`` is added to the step cost whenever the
    direction changes, so straight runs win ties over staircases.
    Returns a list of ``(i, j)`` including both ends, or ``None`` if the
    goal is unreachable."""
    nx, ny = len(blocked), len(blocked[0])
    si, sj = start
    gi, gj = goal
    if not (0 <= si < nx and 0 <= sj < ny and 0 <= gi < nx and 0 <= gj < ny):
        return None
    if blocked[si][sj] or blocked[gi][gj]:
        return None
    if start == goal:
        return [start]

    def h(i, j):
        return abs(i - gi) + abs(j - gj)

    # state = (i, j, incoming-direction index or -1); the direction is
    # part of the state so the turn penalty is consistent.
    start_state = (si, sj, -1)
    g_score = {start_state: 0}
    came = {}
    open_heap = [(h(si, sj), 0, start_state)]
    while open_heap:
        _f, gc, state = heapq.heappop(open_heap)
        i, j, din = state
        if (i, j) == goal:
            # walk the came-from chain back to the start
            path = [(i, j)]
            while state in came:
                state = came[state]
                path.append((state[0], state[1]))
            path.reverse()
            return path
        if gc > g_score.get(state, gc):
            continue
        for d, (dx, dy) in enumerate(_DIRS):
            ni, nj = i + dx, j + dy
            if not (0 <= ni < nx and 0 <= nj < ny) or blocked[ni][nj]:
                continue
            step = 1 + (turn_penalty if din != -1 and d != din else 0)
            nstate = (ni, nj, d)
            ng = gc + step
            if ng < g_score.get(nstate, 1 << 60):
                g_score[nstate] = ng
                came[nstate] = state
                heapq.heappush(open_heap, (ng + h(ni, nj), ng, nstate))
    return None


def _collapse(points):
    """Drop interior points that lie on the straight segment between
    their neighbours, so an axis-aligned run becomes just its two ends."""
    if len(points) <= 2:
        return list(points)
    out = [points[0]]
    for k in range(1, len(points) - 1):
        ax, ay = out[-1]
        bx, by = points[k]
        cx, cy = points[k + 1]
        # collinear (and, being 4-connected, axis-aligned) -> skip b
        if (bx - ax) * (cy - ay) == (by - ay) * (cx - ax):
            continue
        out.append(points[k])
    out.append(points[-1])
    return out


def route(start_xy, goal_xy, obstacles, grid, x0, y0, nx, ny,
          endpoint_clear_cells=1, turn_penalty=2):
    """Full grid route in DBU coordinates.

    ``start_xy`` / ``goal_xy`` are DBU points; they are snapped to the
    nearest grid node of the lattice anchored at ``(x0, y0)`` with pitch
    ``grid`` and ``nx x ny`` nodes. ``obstacles`` is a shapely geometry
    already grown by the trace's half-width + clearance. Returns a list
    of DBU ``(x, y)`` vertices (collinear runs collapsed, endpoints
    forced to the exact input points), or ``None`` if unroutable.
    Raises ``ValueError`` if ``nx * ny`` exceeds :data:`MAX_NODES`."""
    if nx * ny > MAX_NODES:
        raise ValueError(
            f"routing grid is {nx} x {ny} = {nx * ny} nodes (limit "
            f"{MAX_NODES}); use a coarser grid or route a shorter span"
        )

    def snap(x, y):
        i = min(max(round((x - x0) / grid), 0), nx - 1)
        j = min(max(round((y - y0) / grid), 0), ny - 1)
        return i, j

    blocked = rasterize_blocked(obstacles, x0, y0, grid, nx, ny)
    si, sj = snap(*start_xy)
    gi, gj = snap(*goal_xy)
    clear_disc(blocked, si, sj, endpoint_clear_cells)
    clear_disc(blocked, gi, gj, endpoint_clear_cells)

    node_path = astar(blocked, (si, sj), (gi, gj), turn_penalty=turn_penalty)
    if node_path is None:
        return None

    pts = [(x0 + i * grid, y0 + j * grid) for i, j in node_path]
    pts = _collapse(pts)
    # Pin the very ends to the caller's exact points so the trace lands
    # dead-centre on each pad rather than on the nearest grid node.
    pts[0] = (int(round(start_xy[0])), int(round(start_xy[1])))
    pts[-1] = (int(round(goal_xy[0])), int(round(goal_xy[1])))
    return _collapse(pts)
