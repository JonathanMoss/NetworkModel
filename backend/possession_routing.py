"""Straight-through track-graph pathfinding for Engineers' Possessions.

Replaces naive hop-count BFS and straight-line ("as the crow flies") fallbacks
with direction-aware Dijkstra on both:
  1. The TPS Schematic track graph (``edges`` / ``nodes``), disallowing
     reversals and heavily penalizing diverging crossovers at junctions so
     possessions strictly follow the straight-through road along track sections.
  2. The NWR Geographic Track Model graph (``nwr_track_centrelines`` connected
     by ``start_node_id`` / ``end_node_id``), disallowing reversals, heavily
     penalizing ``*7xx`` crossovers and track-ID changes, and trimming the start
     and end centrelines precisely to the OSGB36 PLB coordinates.
"""

import heapq
import math
from typing import Any, Optional

# Turn / crossover penalties (in metres equivalent)
_TPS_CROSSOVER_PENALTY = 500_000.0
_NWR_CROSSOVER_TRACK_PENALTY = 500_000.0
_NWR_DIVERGING_ANGLE_PENALTY = 250_000.0
_NWR_TRACK_CHANGE_PENALTY = 100_000.0
_NWR_ELR_BOUNDARY_TRACK_PENALTY = 2_000.0

# Cosine thresholds for direction continuity
_REVERSAL_COS_LIMIT = -0.15       # > ~99° turn = physically impossible reversal
_STRAIGHT_COS_THRESHOLD = 0.965   # < ~15° deflection = straight-through route


def _unit_vec(dx: float, dy: float) -> Optional[tuple[float, float]]:
    """Return a 2D unit vector, or ``None`` for degenerate zero-length vectors."""
    mag = math.hypot(dx, dy)
    if mag < 1e-9:
        return None
    return (dx / mag, dy / mag)


def _polyline_end_tangent(coords: list[list[float]], at_start: bool) -> Optional[tuple[float, float]]:
    """Compute a stable unit tangent vector at the start or end of a polyline.

    Walks up to ~25m along the polyline from the chosen endpoint so tiny
    1-metre digitization jitter at switch tips does not distort the angle.
    """
    if len(coords) < 2:
        return None
    if at_start:
        p0 = coords[0]
        target = coords[1]
        for pt in coords[1:]:
            target = pt
            if math.hypot(pt[0] - p0[0], pt[1] - p0[1]) >= 25.0:
                break
        return _unit_vec(float(target[0]) - float(p0[0]), float(target[1]) - float(p0[1]))
    p_end = coords[-1]
    prev = coords[-2]
    for pt in reversed(coords[:-1]):
        prev = pt
        if math.hypot(p_end[0] - pt[0], p_end[1] - pt[1]) >= 25.0:
            break
    return _unit_vec(float(p_end[0]) - float(prev[0]), float(p_end[1]) - float(prev[1]))


def _is_nwr_crossover(track_id: Optional[str]) -> bool:
    """Return True if an NWR 4-digit track ID denotes a crossover (``17xx``/``27xx``/``37xx``)."""
    tid = str(track_id or "").strip()
    return len(tid) >= 2 and tid[1] == "7"


def _project_point_on_polyline(
    coords: list[list[float]],
    px: float,
    py: float,
) -> tuple[float, float, int, list[float]]:
    """Project ``(px, py)`` onto a polyline.

    Returns ``(dist_sq, dist_along, seg_idx, projected_xy)`` where
    ``dist_along`` is cumulative distance from ``coords[0]``.
    """
    best_d2 = float("inf")
    best_along = 0.0
    best_idx = 0
    best_pt = [float(coords[0][0]), float(coords[0][1])]
    cum = 0.0

    for i in range(len(coords) - 1):
        ax, ay = float(coords[i][0]), float(coords[i][1])
        bx, by = float(coords[i + 1][0]), float(coords[i + 1][1])
        abx, aby = bx - ax, by - ay
        seg_len2 = abx * abx + aby * aby
        if seg_len2 < 1e-12:
            t = 0.0
        else:
            t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / seg_len2))
        qx, qy = ax + t * abx, ay + t * aby
        d2 = (px - qx) ** 2 + (py - qy) ** 2
        seg_len = math.sqrt(seg_len2)
        if d2 < best_d2:
            best_d2 = d2
            best_along = cum + t * seg_len
            best_idx = i
            best_pt = [round(qx, 3), round(qy, 3)]
        cum += seg_len

    return best_d2, best_along, best_idx, best_pt


def _slice_polyline_by_dist(
    coords: list[list[float]],
    d_start: float,
    d_end: float,
) -> list[list[float]]:
    """Slice ``coords`` between cumulative distances ``[d_start, d_end]`` (supports reverse)."""
    if len(coords) < 2:
        return []
    reverse_out = d_start > d_end
    lo, hi = (d_end, d_start) if reverse_out else (d_start, d_end)
    if hi - lo < 1e-6:
        return []

    out: list[list[float]] = []
    cum = 0.0
    for i in range(len(coords) - 1):
        ax, ay = float(coords[i][0]), float(coords[i][1])
        bx, by = float(coords[i + 1][0]), float(coords[i + 1][1])
        seg_len = math.hypot(bx - ax, by - ay)
        if seg_len < 1e-9:
            continue
        seg_end = cum + seg_len
        if seg_end >= lo and cum <= hi:
            t0 = max(0.0, (lo - cum) / seg_len)
            t1 = min(1.0, (hi - cum) / seg_len)
            p0 = [round(ax + t0 * (bx - ax), 3), round(ay + t0 * (by - ay), 3)]
            p1 = [round(ax + t1 * (bx - ax), 3), round(ay + t1 * (by - ay), 3)]
            if not out or math.hypot(p0[0] - out[-1][0], p0[1] - out[-1][1]) > 1e-3:
                out.append(p0)
            if math.hypot(p1[0] - out[-1][0], p1[1] - out[-1][1]) > 1e-3:
                out.append(p1)
        cum = seg_end

    if reverse_out:
        out.reverse()
    return out


def _run_tps_dijkstra(
    adj: dict[int, list[tuple[int, float, float, float, float, float]]],
    start_node_id: int,
    end_node_id: int,
) -> list[int]:
    """Run direction-aware straight-through Dijkstra over a TPS adjacency map."""
    if start_node_id not in adj or end_node_id not in adj:
        return []

    # State: (cost, curr_node, prev_node)
    pq: list[tuple[float, int, int]] = [(0.0, start_node_id, -1)]
    best_cost: dict[tuple[int, int], float] = {(start_node_id, -1): 0.0}
    parent: dict[tuple[int, int], Optional[tuple[int, int]]] = {(start_node_id, -1): None}
    end_state: Optional[tuple[int, int]] = None

    while pq:
        cost, curr, prev = heapq.heappop(pq)
        state = (curr, prev)
        if cost > best_cost.get(state, float("inf")):
            continue
        if curr == end_node_id:
            end_state = state
            break

        # Incoming unit vector from prev -> curr
        in_vec: Optional[tuple[float, float]] = None
        if prev != -1:
            for nb, _l, ux, uy, vx, vy in adj.get(prev, ()):
                if nb == curr:
                    in_vec = _unit_vec(vx - ux, vy - uy)
                    break

        neighbors = adj.get(curr, ())
        is_junction = len(neighbors) >= 3
        for nxt, edge_len, ux, uy, vx, vy in neighbors:
            if nxt == prev:
                continue
            penalty = 0.0
            if in_vec is not None:
                out_vec = _unit_vec(vx - ux, vy - uy)
                if out_vec is not None:
                    dot = in_vec[0] * out_vec[0] + in_vec[1] * out_vec[1]
                    if dot < _REVERSAL_COS_LIMIT:
                        # Physically impossible reversal through a trailing switch
                        continue
                    if is_junction and dot < _STRAIGHT_COS_THRESHOLD:
                        penalty += _TPS_CROSSOVER_PENALTY
                    else:
                        penalty += (1.0 - max(0.0, dot)) * 50.0

            new_cost = cost + edge_len + penalty
            nxt_state = (nxt, curr)
            if new_cost < best_cost.get(nxt_state, float("inf")):
                best_cost[nxt_state] = new_cost
                parent[nxt_state] = state
                heapq.heappush(pq, (new_cost, nxt, curr))

    if end_state is None:
        return []

    path_nids: list[int] = []
    st: Optional[tuple[int, int]] = end_state
    while st is not None:
        path_nids.append(st[0])
        st = parent.get(st)
    path_nids.reverse()
    return path_nids


def find_tps_straight_path(
    cur: Any,
    start_node_id: Optional[int],
    end_node_id: Optional[int],
) -> list[list[float]]:
    """Find the straight-through TPS schematic track path between two nodes.

    Fetches the local corridor of ``edges`` in a single spatial query and runs
    direction-aware Dijkstra so the route follows every TPS track section
    without jumping across crossovers onto adjacent lines.
    """
    if start_node_id is None or end_node_id is None:
        return []
    if start_node_id == end_node_id:
        cur.execute("SELECT netx, nety FROM nodes WHERE node_id = %s", (start_node_id,))
        row = cur.fetchone()
        return [[float(row["netx"]), float(row["nety"])]] if row else []

    cur.execute(
        "SELECT node_id, netx, nety FROM nodes WHERE node_id IN (%s, %s)",
        (start_node_id, end_node_id),
    )
    end_nodes = {int(r["node_id"]): (float(r["netx"]), float(r["nety"])) for r in cur.fetchall() if r["netx"] is not None}
    if start_node_id not in end_nodes or end_node_id not in end_nodes:
        return []

    sx, sy = end_nodes[start_node_id]
    ex, ey = end_nodes[end_node_id]
    span = math.hypot(ex - sx, ey - sy)

    for pad_mult in (0.45, 1.5):
        pad = max(2500.0, span * pad_mult)
        min_x, max_x = min(sx, ex) - pad, max(sx, ex) + pad
        min_y, max_y = min(sy, ey) - pad, max(sy, ey) + pad
        cur.execute(
            """
            SELECT start_node_id, end_node_id, x0, y0, x1, y1, length
            FROM edges
            WHERE max_x >= %s AND min_x <= %s AND max_y >= %s AND min_y <= %s
            """,
            (min_x, max_x, min_y, max_y),
        )
        rows = cur.fetchall()
        adj: dict[int, list[tuple[int, float, float, float, float, float]]] = {}
        node_xy: dict[int, list[float]] = {
            start_node_id: [sx, sy],
            end_node_id: [ex, ey],
        }
        for r in rows:
            a, b = r.get("start_node_id"), r.get("end_node_id")
            if a is None or b is None or r.get("x0") is None or r.get("x1") is None:
                continue
            ia, ib = int(a), int(b)
            x0, y0, x1, y1 = float(r["x0"]), float(r["y0"]), float(r["x1"]), float(r["y1"])
            elen = float(r.get("length") or 0.0)
            if elen <= 0.0:
                elen = max(math.hypot(x1 - x0, y1 - y0), 1.0)
            adj.setdefault(ia, []).append((ib, elen, x0, y0, x1, y1))
            adj.setdefault(ib, []).append((ia, elen, x1, y1, x0, y0))
            node_xy.setdefault(ia, [x0, y0])
            node_xy.setdefault(ib, [x1, y1])

        path_nids = _run_tps_dijkstra(adj, start_node_id, end_node_id)
        if path_nids:
            return [node_xy[nid] for nid in path_nids if nid in node_xy]

    return []


def _pick_anchor_centrelines(
    rows: list[dict[str, Any]],
    px: float,
    py: float,
    pref_elr: Optional[str],
    pref_track_id: Optional[str],
) -> list[tuple[float, dict[str, Any], float]]:
    """Find the best NWR centreline(s) matching an OSGB36 endpoint.

    When ``pref_track_id`` is known and present near ``(px, py)``, restricts
    anchors strictly to that ``track_id`` so an adjacent parallel road (3.5m
    away) can never be seeded as an equal-cost alternative.
    """
    scored: list[tuple[float, dict[str, Any], float]] = []
    elr_u = (pref_elr or "").strip().upper()
    tid_s = str(pref_track_id or "").strip()

    for r in rows:
        coords = r.get("coords") or []
        if len(coords) < 2:
            continue
        d2, along, _idx, _pt = _project_point_on_polyline(coords, px, py)
        dist = math.sqrt(d2)
        if dist > 80.0:
            continue
        rank = dist
        if elr_u and (r.get("elr") or "").strip().upper() != elr_u:
            rank += 25.0
        if tid_s and str(r.get("track_id") or "").strip() != tid_s:
            rank += 500.0
        if _is_nwr_crossover(r.get("track_id")):
            rank += 40.0
        scored.append((rank, r, along))

    if tid_s:
        exact_tid = [item for item in scored if str(item[1].get("track_id") or "").strip() == tid_s]
        if exact_tid:
            scored = exact_tid

    scored.sort(key=lambda item: item[0])
    return scored[:2]


def _nwr_endpoint_key(node_id: Any, pt: list[float]) -> str:
    """Return a canonical NWR junction key, falling back to a 0.5m OSGB36 spatial key.

    Network Rail centrelines leave ``start_node_id`` / ``end_node_id`` as ``NULL``
    at ELR boundaries (e.g. ``LEC5`` -> ``LEC4``). Synthesizing a spatial key from
    the endpoint coordinates stitches those segments seamlessly.
    """
    if node_id is not None and str(node_id).strip():
        return str(node_id).strip()
    return f"XY:{round(float(pt[0]) * 2.0) / 2.0:.1f},{round(float(pt[1]) * 2.0) / 2.0:.1f}"


def _run_nwr_dijkstra(
    rows: list[dict[str, Any]],
    start_anchors: list[tuple[float, dict[str, Any], float]],
    end_anchors: list[tuple[float, dict[str, Any], float]],
) -> list[tuple[dict[str, Any], bool, float, float]]:
    """Run straight-through Dijkstra over NWR centrelines connected by track nodes.

    Returns an ordered list of ``(centreline_row, forward_flag, clip_lo_m, clip_hi_m)``.
    """
    end_by_fid = {int(r["fid"]): (rk, r, along) for rk, r, along in end_anchors}

    # Fast path: start and end lie on the exact same centreline segment
    for _rk, s_row, s_along in start_anchors:
        sfid = int(s_row["fid"])
        if sfid in end_by_fid:
            _erk, e_row, e_along = end_by_fid[sfid]
            return [(e_row, s_along <= e_along, s_along, e_along)]

    # Build node -> oriented centreline adjacency
    by_fid: dict[int, dict[str, Any]] = {}
    node_adj: dict[str, list[tuple[int, bool, str, float, Optional[tuple[float, float]], Optional[tuple[float, float]]]]] = {}
    total_len_by_fid: dict[int, float] = {}

    for r in rows:
        coords = r.get("coords") or []
        if len(coords) < 2:
            continue
        sn_s = _nwr_endpoint_key(r.get("start_node_id"), coords[0])
        en_s = _nwr_endpoint_key(r.get("end_node_id"), coords[-1])
        fid = int(r["fid"])
        by_fid[fid] = r
        plen = sum(
            math.hypot(float(coords[i + 1][0]) - float(coords[i][0]), float(coords[i + 1][1]) - float(coords[i][1]))
            for i in range(len(coords) - 1)
        )
        total_len_by_fid[fid] = max(plen, 1.0)
        t_start = _polyline_end_tangent(coords, at_start=True)
        t_end = _polyline_end_tangent(coords, at_start=False)
        t_start_rev = (-t_start[0], -t_start[1]) if t_start else None
        t_end_rev = (-t_end[0], -t_end[1]) if t_end else None

        node_adj.setdefault(sn_s, []).append((fid, True, en_s, total_len_by_fid[fid], t_start, t_end))
        node_adj.setdefault(en_s, []).append((fid, False, sn_s, total_len_by_fid[fid], t_end_rev, t_start_rev))

    # State: (cost, curr_node_str, curr_fid, curr_forward)
    pq: list[tuple[float, str, int, bool]] = []
    best_cost: dict[tuple[str, int, bool], float] = {}
    parent: dict[tuple[str, int, bool], Optional[tuple[str, int, bool]]] = {}
    start_along_map: dict[int, float] = {}

    for rk, s_row, s_along in start_anchors:
        sfid = int(s_row["fid"])
        if sfid not in by_fid:
            continue
        start_along_map[sfid] = s_along
        plen = total_len_by_fid[sfid]
        s_coords = s_row["coords"]
        sn_s = _nwr_endpoint_key(s_row.get("start_node_id"), s_coords[0])
        en_s = _nwr_endpoint_key(s_row.get("end_node_id"), s_coords[-1])
        anchor_pen = rk * 100.0
        # Can leave start centreline forwards toward en_s or backwards toward sn_s
        for fwd, exit_node, init_dist in (
            (True, en_s, max(plen - s_along, 0.0) + anchor_pen),
            (False, sn_s, max(s_along, 0.0) + anchor_pen),
        ):
            st = (exit_node, sfid, fwd)
            if init_dist < best_cost.get(st, float("inf")):
                best_cost[st] = init_dist
                parent[st] = None
                heapq.heappush(pq, (init_dist, exit_node, sfid, fwd))

    best_goal_cost = float("inf")
    goal_chain: Optional[tuple[tuple[str, int, bool], int, bool, float]] = None

    while pq:
        cost, curr_node, curr_fid, curr_fwd = heapq.heappop(pq)
        st = (curr_node, curr_fid, curr_fwd)
        if cost > best_cost.get(st, float("inf")) or cost >= best_goal_cost:
            continue

        curr_row = by_fid[curr_fid]
        curr_coords = curr_row["coords"]
        if curr_fwd:
            curr_exit_vec = _polyline_end_tangent(curr_coords, at_start=False)
        else:
            raw_t = _polyline_end_tangent(curr_coords, at_start=True)
            curr_exit_vec = (-raw_t[0], -raw_t[1]) if raw_t else None
        curr_tid = str(curr_row.get("track_id") or "").strip()
        curr_elr = (curr_row.get("elr") or "").strip().upper()

        for nxt_fid, nxt_fwd, nxt_exit_node, nxt_len, nxt_entry_vec, _nxt_exit_vec in node_adj.get(curr_node, ()):
            if nxt_fid == curr_fid:
                continue
            nxt_row = by_fid[nxt_fid]
            penalty = 0.0
            if curr_exit_vec is not None and nxt_entry_vec is not None:
                dot = curr_exit_vec[0] * nxt_entry_vec[0] + curr_exit_vec[1] * nxt_entry_vec[1]
                if dot < _REVERSAL_COS_LIMIT:
                    continue
                if dot < _STRAIGHT_COS_THRESHOLD:
                    penalty += _NWR_DIVERGING_ANGLE_PENALTY
                else:
                    penalty += (1.0 - max(0.0, dot)) * 50.0

            nxt_tid = str(nxt_row.get("track_id") or "").strip()
            nxt_elr = (nxt_row.get("elr") or "").strip().upper()
            if _is_nwr_crossover(nxt_tid):
                penalty += _NWR_CROSSOVER_TRACK_PENALTY
            elif curr_tid and nxt_tid and nxt_tid != curr_tid:
                penalty += (
                    _NWR_TRACK_CHANGE_PENALTY
                    if curr_elr == nxt_elr
                    else _NWR_ELR_BOUNDARY_TRACK_PENALTY
                )

            if nxt_fid in end_by_fid:
                end_rk, _e_row, e_along = end_by_fid[nxt_fid]
                partial = e_along if nxt_fwd else max(nxt_len - e_along, 0.0)
                total = cost + partial + penalty + end_rk * 100.0
                if total < best_goal_cost:
                    best_goal_cost = total
                    goal_chain = (st, nxt_fid, nxt_fwd, e_along)
                continue

            new_cost = cost + nxt_len + penalty
            nxt_st = (nxt_exit_node, nxt_fid, nxt_fwd)
            if new_cost < best_cost.get(nxt_st, float("inf")):
                best_cost[nxt_st] = new_cost
                parent[nxt_st] = st
                heapq.heappush(pq, (new_cost, nxt_exit_node, nxt_fid, nxt_fwd))

    if goal_chain is None:
        return []

    last_st, end_fid, end_fwd, end_along = goal_chain
    rev_states: list[tuple[str, int, bool]] = []
    cur_st: Optional[tuple[str, int, bool]] = last_st
    while cur_st is not None:
        rev_states.append(cur_st)
        cur_st = parent.get(cur_st)
    rev_states.reverse()

    segments: list[tuple[dict[str, Any], bool, float, float]] = []
    for idx, (_node, fid, fwd) in enumerate(rev_states):
        plen = total_len_by_fid[fid]
        if idx == 0:
            s_along = start_along_map[fid]
            lo, hi = (s_along, plen) if fwd else (s_along, 0.0)
        else:
            lo, hi = (0.0, plen) if fwd else (plen, 0.0)
        segments.append((by_fid[fid], fwd, lo, hi))

    end_plen = total_len_by_fid[end_fid]
    e_lo, e_hi = (0.0, end_along) if end_fwd else (end_plen, end_along)
    segments.append((by_fid[end_fid], end_fwd, e_lo, e_hi))
    return segments


def find_nwr_straight_path(
    cur: Any,
    start_ep: dict[str, Any],
    end_ep: dict[str, Any],
) -> list[dict[str, Any]]:
    """Find the straight-through NWR geographic track path between two endpoints.

    Uses the physical ``nwr_track_centrelines`` node graph so possessions follow
    actual tracks seamlessly across ELR boundaries without jumping crossovers or
    falling back to straight lines.
    """
    sx, sy = start_ep.get("nwr_x"), start_ep.get("nwr_y")
    ex, ey = end_ep.get("nwr_x"), end_ep.get("nwr_y")
    if sx is None or sy is None or ex is None or ey is None:
        return []

    fx, fy, tx, ty = float(sx), float(sy), float(ex), float(ey)
    span = math.hypot(tx - fx, ty - fy)
    pad = max(1500.0, span * 0.35)

    cur.execute(
        """
        SELECT fid, elr, track_id, start_node_id, end_node_id,
               start_meters, end_meters, length_meters, coords
        FROM nwr_track_centrelines
        WHERE max_x >= %s AND min_x <= %s AND max_y >= %s AND min_y <= %s
        """,
        (min(fx, tx) - pad, max(fx, tx) + pad, min(fy, ty) - pad, max(fy, ty) + pad),
    )
    rows = cur.fetchall()
    if not rows:
        return []

    start_anchors = _pick_anchor_centrelines(rows, fx, fy, start_ep.get("elr"), start_ep.get("track_id"))
    end_anchors = _pick_anchor_centrelines(rows, tx, ty, end_ep.get("elr"), end_ep.get("track_id"))
    if not start_anchors or not end_anchors:
        return []

    segments = _run_nwr_dijkstra(rows, start_anchors, end_anchors)
    ways: list[dict[str, Any]] = []
    for row, _fwd, d0, d1 in segments:
        clipped = _slice_polyline_by_dist(row["coords"], d0, d1)
        if len(clipped) >= 2:
            ways.append(
                {
                    "fid": row["fid"],
                    "elr": row.get("elr"),
                    "track_id": row.get("track_id"),
                    "coords": clipped,
                }
            )
    return ways

