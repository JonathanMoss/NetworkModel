"""TPS-to-Geographic (NWR Track Model) and Network Fusion cross-linking engine.

This module orchestrates matching between schematic TPS assets (signals,
points, edges, nodes, stations) and geographic Network Rail Track Model
centrelines and physical nodes (OSGB36), as well as the reverse NWR-to-TPS
bridge and Network Fusion (GeoFurlong) corridor lookups.

Each stage of the matching pipeline is decomposed into a small, single-concern
function so individual heuristics (primary retrieval, cross-ELR hub/flyover
augmentation, switch-endpoint boosting, 34xx continuation tracing, node
snapping, and camera framing) can be documented, understood, and unit-tested
independently.
"""

import math
import re
from typing import Any, Optional

from config import decode_code
from geometry import (
    interpolate_pt_along_coords,
    meters_to_miles_chains,
    slice_polyline,
)
from ingest_nwr import VALENCY_DESC, decode_track_id
from track_scoring import score_track_candidate


# Cache mapping (elr, want_prefix, 2km_bucket) -> (pure_34xx_tids, matched_34xx_tids)
# to avoid repeated spatial endpoint scans across multi-platform station approaches.
_CONT_34XX_CACHE: dict[tuple[str, str, int], tuple[set[str], set[str]]] = {}


def reset_continuation_cache() -> None:
    """Clear the cached 34xx continuation lookup table (primarily for tests)."""
    _CONT_34XX_CACHE.clear()


def _empty_nwr_cross_links(
    elr_rec: Optional[dict[str, Any]] = None,
    geo_snap: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build the canonical empty cross-link response payload.

    Args:
        elr_rec: Optional ELR record if one was found despite having no tracks.
        geo_snap: Optional fallback geographic camera target.

    Returns:
        Dictionary matching the public contract of
        :func:`find_nwr_cross_links_for_tps`.
    """
    return {
        "elr_record": elr_rec,
        "centrelines": [],
        "corridor_centrelines": [],
        "best_centreline": None,
        "node_point": None,
        "node_points": [],
        "snapped_node": None,
        "geo_snap": geo_snap,
        "single_match": False,
        "match_info": None,
    }


def _find_34xx_continuation_tracks(
    cur: Any,
    elr: str,
    candidate_34_tids: set[str],
    want_prefix: str,
    target_m: float,
) -> set[str]:
    """Find covering ``34xx`` tracks that physically continue into ``want_prefix``.

    At complex bi-directional station approaches (e.g. Cardiff Central where
    ``3402``, ``3403``, ``3412``, ``3413`` run in parallel), this traces the
    outer OSGB36 endpoints of each ``34xx`` track within +/-3 km of ``target_m``
    and checks whether they touch a primary running track matching
    ``want_prefix`` (e.g. ``11``, ``21``, ``12``, ``22``) within 1.5 metres
    (squared distance ``<= 2.25``).

    Tracks that connect *exclusively* to the requested running class (``pure_34``)
    are preferred over tracks that bridge multiple classes (``matched_34``).

    Args:
        cur: Active database cursor returning dict rows.
        elr: Engineer's Line Reference code.
        candidate_34_tids: Set of covering ``34xx`` track IDs to disambiguate.
        want_prefix: Two-digit NWR prefix (direction + class, e.g. ``"11"``).
        target_m: Target chainage in metres along ``elr``.

    Returns:
        Subset of ``candidate_34_tids`` that physically continue into
        ``want_prefix``.
    """
    bucket = int(target_m // 2000)
    cache_key = (elr, want_prefix, bucket)
    if cache_key in _CONT_34XX_CACHE:
        pure_c, matched_c = _CONT_34XX_CACHE[cache_key]
        return (pure_c & candidate_34_tids) or (matched_c & candidate_34_tids)

    cur.execute(
        """
        SELECT track_id, LEAST(start_meters, end_meters) AS sm, GREATEST(start_meters, end_meters) AS em,
               coords->0 AS p0, coords->-1 AS p1
        FROM nwr_track_centrelines
        WHERE elr = %s
          AND (
              track_id LIKE '34%%'
              OR track_id IN ('1100', '2100', '1200', '1201', '2200', '2201')
          )
          AND LEAST(start_meters, end_meters) <= %s + 3000.0
          AND GREATEST(start_meters, end_meters) >= %s - 3000.0
        """,
        (elr, target_m, target_m),
    )

    want_pts: list[tuple[float, float]] = []
    other_class_pts: list[tuple[float, float]] = []
    want_d2 = want_prefix[1]
    ends_by_34: dict[str, list[tuple[float, float, list]]] = {}

    for row in cur.fetchall():
        tid = str(row["track_id"] or "")
        p0, p1 = row.get("p0"), row.get("p1")
        if tid.startswith("34"):
            ends_by_34.setdefault(tid, []).append((float(row["sm"]), float(row["em"]), [p0, p1]))
        elif tid.startswith(want_prefix):
            if p0 and len(p0) >= 2:
                want_pts.append((float(p0[0]), float(p0[1])))
            if p1 and len(p1) >= 2:
                want_pts.append((float(p1[0]), float(p1[1])))
        elif len(tid) == 4 and tid[1] != want_d2:
            if p0 and len(p0) >= 2:
                other_class_pts.append((float(p0[0]), float(p0[1])))
            if p1 and len(p1) >= 2:
                other_class_pts.append((float(p1[0]), float(p1[1])))

    matched_34: set[str] = set()
    pure_34: set[str] = set()
    if want_pts:
        for tid34, segs in ends_by_34.items():
            # Only inspect the outermost chainage segments of this 34xx track.
            min_seg = min(segs, key=lambda x: x[0])
            max_seg = max(segs, key=lambda x: x[1])
            outer_pts = [
                (float(p[0]), float(p[1]))
                for p in (min_seg[2] + max_seg[2])
                if p and len(p) >= 2
            ]
            hits_want = any(
                (px - tx) ** 2 + (py - ty) ** 2 <= 2.25
                for px, py in outer_pts
                for tx, ty in want_pts
            )
            if hits_want:
                matched_34.add(tid34)
                hits_other = any(
                    (px - tx) ** 2 + (py - ty) ** 2 <= 2.25
                    for px, py in outer_pts
                    for tx, ty in other_class_pts
                )
                if not hits_other:
                    pure_34.add(tid34)

    # Prefer tracks that connect exclusively to the requested class
    # (e.g. 3412->1100 vs 3402->1100+1201).
    _CONT_34XX_CACHE[cache_key] = (pure_34, matched_34)
    return (pure_34 & candidate_34_tids) or (matched_34 & candidate_34_tids)


def _find_valency2_adjacent_elr(
    cur: Any,
    node_ids: list,
    primary_elr: str,
    max_hops: int = 10,
) -> Optional[str]:
    """Walk through valency <= 2 nodes to detect a single-track cross-ELR chord.

    Because the walk stops at any node with degree > 2 (a turnout), it never
    leaks onto parallel trunk lines and only identifies an adjacent ELR when
    the TPS node lies on an uninterrupted single-track chord leading into it.

    Args:
        cur: Active database cursor.
        node_ids: Starting TPS node IDs.
        primary_elr: Primary ELR name that we are walking *away* from.
        max_hops: Maximum BFS depth along plain-line edges.

    Returns:
        First encountered adjacent ELR name differing from ``primary_elr``, or
        ``None`` if no ELR transition occurs within ``max_hops`` plain-line hops.
    """
    clean = [int(n) for n in (node_ids or []) if n is not None]
    if not clean or not primary_elr:
        return None
    visited = set(clean)
    frontier = list(clean)
    for _ in range(max_hops):
        if not frontier:
            break
        cur.execute(
            "SELECT start_node_id, end_node_id FROM edges WHERE start_node_id = ANY(%s) OR end_node_id = ANY(%s)",
            (frontier, frontier),
        )
        adj: dict[int, set[int]] = {}
        for edge in cur.fetchall():
            a, b = edge["start_node_id"], edge["end_node_id"]
            if a is not None and b is not None:
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)
        nxt: list[int] = []
        for u in frontier:
            nbrs = adj.get(u, set())
            # Strictly require valency <= 2 (zero turnouts) before advancing.
            if len(nbrs) <= 2:
                for v in nbrs:
                    if v not in visited:
                        visited.add(v)
                        nxt.append(v)
        if not nxt:
            break
        cur.execute("SELECT node_id, km_region_id FROM nodes WHERE node_id = ANY(%s)", (nxt,))
        for nr in cur.fetchall():
            elr_name = decode_code("kmregionmasterdesc", nr["km_region_id"])
            if elr_name and elr_name != primary_elr and elr_name != "---":
                return elr_name
        frontier = nxt
    return None


def _get_spatial_anchor(
    raw_candidates: list[dict[str, Any]],
    km_meters: Optional[float],
    allow_bbox_fallback: bool = False,
) -> Optional[tuple[float, float]]:
    """Compute an OSGB36 ``(easting, northing)`` anchor from the first candidate.

    Args:
        raw_candidates: Non-empty list of primary-ELR centreline rows.
        km_meters: Target chainage in metres, or ``None``.
        allow_bbox_fallback: When ``True``, fall back to the candidate's
            bounding-box midpoint if polyline interpolation is unavailable.

    Returns:
        ``(x, y)`` tuple in OSGB36 metres, or ``None`` if unavailable.
    """
    if not raw_candidates:
        return None
    c0 = raw_candidates[0]
    if km_meters is not None and c0.get("coords"):
        pt = interpolate_pt_along_coords(
            c0["coords"], c0["start_meters"], c0["end_meters"], km_meters
        )
        if pt:
            return (float(pt[0]), float(pt[1]))
    if allow_bbox_fallback and c0.get("min_x") is not None and c0.get("min_y") is not None:
        return (
            (float(c0["min_x"]) + float(c0["max_x"])) / 2.0,
            (float(c0["min_y"]) + float(c0["max_y"])) / 2.0,
        )
    return None


def _fetch_primary_candidates(
    cur: Any,
    elr: str,
    target_m: Optional[float],
    span_min: Optional[float],
    span_max: Optional[float],
    effective_pref_fid: Optional[int],
    is_extrapolated: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch primary-ELR track centreline candidates plus any manual override.

    Args:
        cur: Active database cursor.
        elr: Primary ELR code.
        target_m: Midpoint chainage in metres (or ``None``).
        span_min: Lower chainage search bound in metres (or ``None``).
        span_max: Upper chainage search bound in metres (or ``None``).
        effective_pref_fid: Manual or saved NWR feature ID override, if any.
        is_extrapolated: Initial extrapolation flag from caller.

    Returns:
        Tuple ``(raw_candidates, is_extrapolated)``.
    """
    if span_min is not None:
        cur.execute(
            """
            SELECT fid, asset_id, elr, track_id, start_mile_yards, end_mile_yards,
                   start_meters, end_meters, length_meters, owner, coords,
                   start_node_id, end_node_id, min_x, max_x, min_y, max_y
            FROM nwr_track_centrelines
            WHERE elr = %s
              AND LEAST(start_meters, end_meters) <= %s
              AND GREATEST(start_meters, end_meters) >= %s
            ORDER BY
              CASE WHEN %s BETWEEN LEAST(start_meters, end_meters) AND GREATEST(start_meters, end_meters) THEN 0 ELSE 1 END ASC,
              ABS(((start_meters + end_meters) / 2.0) - %s) ASC
            LIMIT 64
            """,
            (elr, span_max, span_min, target_m, target_m),
        )
    else:
        cur.execute(
            """
            SELECT fid, asset_id, elr, track_id, start_mile_yards, end_mile_yards,
                   start_meters, end_meters, length_meters, owner, coords,
                   start_node_id, end_node_id, min_x, max_x, min_y, max_y
            FROM nwr_track_centrelines
            WHERE elr = %s
            ORDER BY start_meters ASC
            LIMIT 24
            """,
            (elr,),
        )

    raw_candidates = list(cur.fetchall())
    if not raw_candidates:
        # Fallback: find nearest centreline on this ELR even outside the margin.
        cur.execute(
            """
            SELECT fid, asset_id, elr, track_id, start_mile_yards, end_mile_yards,
                   start_meters, end_meters, length_meters, owner, coords,
                   start_node_id, end_node_id, min_x, max_x, min_y, max_y
            FROM nwr_track_centrelines
            WHERE elr = %s
            ORDER BY LEAST(ABS(start_meters - %s), ABS(end_meters - %s)) ASC
            LIMIT 1
            """,
            (elr, target_m if target_m is not None else 0, target_m if target_m is not None else 0),
        )
        raw_candidates = list(cur.fetchall())
        if raw_candidates:
            is_extrapolated = True

    # Ensure any explicitly requested/saved override fid is included in the pool.
    if effective_pref_fid is not None and not any(c["fid"] == effective_pref_fid for c in raw_candidates):
        cur.execute(
            """
            SELECT fid, asset_id, elr, track_id, start_mile_yards, end_mile_yards,
                   start_meters, end_meters, length_meters, owner, coords,
                   start_node_id, end_node_id, min_x, max_x, min_y, max_y
            FROM nwr_track_centrelines
            WHERE fid = %s
            LIMIT 1
            """,
            (effective_pref_fid,),
        )
        pref_row = cur.fetchone()
        if pref_row:
            raw_candidates.append(pref_row)

    return raw_candidates, is_extrapolated


def _augment_platform_cross_elr_candidates(
    cur: Any,
    elr: str,
    raw_candidates: list[dict[str, Any]],
    target_m: Optional[float],
    km_meters: Optional[float],
    platform_num: int,
    track_name_hint: Optional[str],
) -> tuple[list[dict[str, Any]], Optional[tuple[float, float]]]:
    """Augment candidates with cross-ELR platform or branch-bay tracks.

    Handles multi-ELR major hubs (e.g. Liverpool St 11-17 on ``LVS``, York 2/4
    on ``YMS``, Reading 4-6 on ``RDG2``) and single-track branch bays (e.g.
    Slough Platform 1 on ``WIN 3600`` where TPS retains ``MLN1``).

    Args:
        cur: Active database cursor.
        elr: Primary ELR code.
        raw_candidates: Current candidate list (not mutated in place).
        target_m: Target chainage in metres.
        km_meters: Primary asset chainage in metres.
        platform_num: Extracted integer platform number (1..99).
        track_name_hint: Optional TPS track/platform name hint.

    Returns:
        Tuple ``(updated_candidates, ref_pt_xy)``.
    """
    plat_suffix = f"{platform_num:02d}"
    has_plat_on_elr = any(
        len(str(c.get("track_id") or "")) == 4
        and str(c["track_id"])[2:4] == plat_suffix
        and str(c["track_id"])[1] in ("1", "2", "4", "5", "6", "8")
        and (
            target_m is None
            or min(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0) - 50.0
            <= target_m
            <= max(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0) + 50.0
        )
        for c in raw_candidates
    )
    if has_plat_on_elr:
        return raw_candidates, None

    th_plat_l = (track_name_hint or "").lower()
    th_plat_toks = set(re.findall(r"[a-z0-9]+", th_plat_l)) if th_plat_l else set()
    has_primary_running_cover = bool(
        target_m is not None
        and (
            bool(re.search(r"\b(?:fast|main|slow|relief|loop)\b", th_plat_l))
            or bool(
                th_plat_toks
                & {"dm", "um", "df", "uf", "ds", "us", "dr", "ur", "upl", "dpl", "ml", "fl", "sl", "rl"}
            )
        )
        and any(
            len(str(c.get("track_id") or "")) == 4
            and str(c["track_id"])[1] in ("1", "2")
            and str(c["track_id"])[2:4] == "00"
            and min(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0)
            <= target_m
            <= max(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0)
            for c in raw_candidates
        )
    )

    ref_pt_xy = _get_spatial_anchor(raw_candidates, km_meters, allow_bbox_fallback=True)
    if not ref_pt_xy:
        return raw_candidates, None

    rx, ry = ref_pt_xy
    # When the primary ELR already strictly covers a running line matching the
    # hint (e.g. CMD2 1100/2100 at Stoke-on-Trent), only allow dedicated
    # cross-ELR station platform classes (5/6/8, e.g. YMS 3804), never
    # open-line branch tracks (1/2/4, e.g. SCQ1 3101/3102).
    cross_classes = ("5", "6", "8") if has_primary_running_cover else ("1", "2", "4", "5", "6", "8")
    cur.execute(
        """
        SELECT fid, asset_id, elr, track_id, start_mile_yards, end_mile_yards,
               start_meters, end_meters, length_meters, owner, coords,
               start_node_id, end_node_id, min_x, max_x, min_y, max_y
        FROM nwr_track_centrelines
        WHERE elr <> %s
          AND RIGHT(track_id, 2) = %s
          AND SUBSTRING(track_id, 2, 1) = ANY(%s)
          AND max_x >= %s - 300.0 AND min_x <= %s + 300.0
          AND max_y >= %s - 300.0 AND min_y <= %s + 300.0
        LIMIT 16
        """,
        (elr, plat_suffix, list(cross_classes), rx, rx, ry, ry),
    )
    cross_elr_rows = cur.fetchall()
    if cross_elr_rows:
        existing_fids = {c["fid"] for c in raw_candidates}
        augmented = list(raw_candidates) + [
            {**r, "_cross_elr": True}
            for r in cross_elr_rows
            if r["fid"] not in existing_fids
        ]
        return augmented, ref_pt_xy

    # Single-track branch bay/platform fallback (e.g. Slough Plat 1 WIN 3600/3100
    # where TPS retains main-line ELR MLN1 up to the branch junction).
    is_generic_plat_hint = (
        target_m is not None
        and not bool(
            th_plat_toks
            & {
                "dm", "um", "df", "uf", "ds", "us", "dr", "ur",
                "ml", "fl", "sl", "rl", "dml", "uml", "dfl", "ufl",
                "dsl", "usl", "drl", "url",
            }
        )
        and not bool(
            re.search(r"\b(?:fast|main|slow|relief|goods|freight|loop|platform)\b", th_plat_l)
        )
    )
    if is_generic_plat_hint:
        cur.execute(
            """
            SELECT fid, asset_id, elr, track_id, start_mile_yards, end_mile_yards,
                   start_meters, end_meters, length_meters, owner, coords,
                   start_node_id, end_node_id, min_x, max_x, min_y, max_y
            FROM nwr_track_centrelines
            WHERE elr <> %s
              AND SUBSTRING(track_id, 1, 1) = '3'
              AND SUBSTRING(track_id, 2, 1) IN ('1', '4', '6')
              AND RIGHT(track_id, 2) = '00'
              AND LEAST(start_meters, end_meters) <= %s + 20.0
              AND GREATEST(start_meters, end_meters) >= %s - 20.0
              AND max_x >= %s - 300.0 AND min_x <= %s + 300.0
              AND max_y >= %s - 300.0 AND min_y <= %s + 300.0
            LIMIT 8
            """,
            (elr, target_m, target_m, rx, rx, ry, ry),
        )
        branch_bay_rows = cur.fetchall()
        if branch_bay_rows:
            existing_fids = {c["fid"] for c in raw_candidates}
            augmented = list(raw_candidates) + [
                {**r, "_branch_bay": True}
                for r in branch_bay_rows
                if r["fid"] not in existing_fids
            ]
            return augmented, ref_pt_xy

    return raw_candidates, ref_pt_xy


def _augment_running_line_cross_elr_candidates(
    cur: Any,
    elr: str,
    raw_candidates: list[dict[str, Any]],
    target_m: float,
    km_meters: Optional[float],
    direction_hint: Optional[str],
    track_name_hint: str,
    tps_node_ids: Optional[list],
) -> tuple[list[dict[str, Any]], Optional[tuple[float, float]]]:
    """Augment candidates for flyovers, diverging branches, and bi-dir chords.

    Covers four non-platform multi-ELR patterns:
      1. Flyover/dive-under Slow/Relief lines (e.g. Norton Bridge ``LEC6 2200``).
      2. Bi-directional single-track chords (e.g. Norton Bridge ``NBS1 3100``).
      3. Named-branch / ``DL``/``UL`` flyovers and re-chained zero-based branch
         ELRs (e.g. ``LEC6 1101``, ``WJL1 1100/2100``, ``NBS 1100/2100``).
      4. Zero-turnout valency-2 cross-ELR chord fallback.

    Args:
        cur: Active database cursor.
        elr: Primary ELR code.
        raw_candidates: Current candidate list.
        target_m: Target chainage in metres.
        km_meters: Primary asset chainage in metres.
        direction_hint: ``"up"``, ``"down"``, ``"reversible"``, or ``None``.
        track_name_hint: TPS track name hint string.
        tps_node_ids: Optional list of TPS node IDs for valency-2 graph walks.

    Returns:
        Tuple ``(updated_candidates, ref_pt_xy)``.
    """
    ref_pt_xy: Optional[tuple[float, float]] = None
    candidates = list(raw_candidates)
    th_l = track_name_hint.lower()
    th_toks = set(re.findall(r"[a-z0-9]+", th_l))
    wants_slow = bool(re.search(r"\b(?:slow|relief)\b", th_l)) or bool(
        th_toks & {"ds", "us", "dsl", "usl", "drl", "url"}
    )

    if wants_slow and direction_hint in ("up", "down"):
        want_d1 = "2" if direction_hint == "down" else "1"
        has_covering_slow = any(
            len(str(c.get("track_id") or "")) == 4
            and str(c["track_id"])[0] in (want_d1, "3")
            and str(c["track_id"])[1] in ("2", "3", "5")
            and min(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0) - 50.0
            <= target_m
            <= max(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0) + 50.0
            for c in candidates
        )
        if not has_covering_slow:
            ref_pt_xy = _get_spatial_anchor(candidates, km_meters)
            if ref_pt_xy:
                rx, ry = ref_pt_xy
                cur.execute(
                    """
                    SELECT fid, asset_id, elr, track_id, start_mile_yards, end_mile_yards,
                           start_meters, end_meters, length_meters, owner, coords,
                           start_node_id, end_node_id, min_x, max_x, min_y, max_y
                    FROM nwr_track_centrelines
                    WHERE elr <> %s
                      AND SUBSTRING(track_id, 1, 1) = %s
                      AND SUBSTRING(track_id, 2, 1) = '2'
                      AND LEAST(start_meters, end_meters) <= %s
                      AND GREATEST(start_meters, end_meters) >= %s
                      AND max_x >= %s - 1200.0 AND min_x <= %s + 1200.0
                      AND max_y >= %s - 1200.0 AND min_y <= %s + 1200.0
                    LIMIT 8
                    """,
                    (elr, want_d1, target_m, target_m, rx, rx, ry, ry),
                )
                flyover_rows = cur.fetchall()
                if flyover_rows:
                    existing_fids = {c["fid"] for c in candidates}
                    candidates.extend(r for r in flyover_rows if r["fid"] not in existing_fids)

    elif direction_hint == "reversible":
        has_covering_rev = any(
            len(str(c.get("track_id") or "")) == 4
            and str(c["track_id"])[0] == "3"
            and str(c["track_id"])[1] in ("1", "2", "4")
            and min(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0) - 50.0
            <= target_m
            <= max(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0) + 50.0
            for c in candidates
        )
        if not has_covering_rev:
            ref_pt_xy = _get_spatial_anchor(candidates, km_meters)
            if ref_pt_xy:
                rx, ry = ref_pt_xy
                cur.execute(
                    """
                    SELECT fid, asset_id, elr, track_id, start_mile_yards, end_mile_yards,
                           start_meters, end_meters, length_meters, owner, coords,
                           start_node_id, end_node_id, min_x, max_x, min_y, max_y
                    FROM nwr_track_centrelines
                    WHERE elr <> %s
                      AND SUBSTRING(track_id, 1, 1) = '3'
                      AND SUBSTRING(track_id, 2, 1) IN ('1', '4')
                      AND LEAST(start_meters, end_meters) <= %s
                      AND GREATEST(start_meters, end_meters) >= %s
                      AND max_x >= %s - 1200.0 AND min_x <= %s + 1200.0
                      AND max_y >= %s - 1200.0 AND min_y <= %s + 1200.0
                    LIMIT 8
                    """,
                    (elr, target_m, target_m, rx, rx, ry, ry),
                )
                rev_chord_rows = cur.fetchall()
                if rev_chord_rows:
                    existing_fids = {c["fid"] for c in candidates}
                    candidates.extend(r for r in rev_chord_rows if r["fid"] not in existing_fids)

    else:
        br_m = re.match(r"^(?:d|u)-((?:d|u)[a-z]{2,3})\b", th_l)
        is_ul_dl_branch = (
            direction_hint in ("up", "down")
            and bool(th_toks & {"dl", "ul"})
            and "loop" not in th_l
            and (
                bool(re.search(r"\bfast\b", th_l))
                or bool(
                    re.search(
                        r"\b(?:up|down)\s+(?!loop\b|goods\b|freight\b|siding\b|slow\b|relief\b|main\b|line\b)[a-z]{3,}\b",
                        th_l,
                    )
                )
            )
            and not any(
                len(str(c.get("track_id") or "")) == 4
                and str(c["track_id"])[2:4] == "50"
                and min(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0) - 50.0
                <= target_m
                <= max(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0) + 50.0
                for c in candidates
            )
        )
        br_sub = (
            br_m.group(1)[1:]
            if br_m and len(br_m.group(1)) >= 3 and br_m.group(1)[0] in ("u", "d")
            else (br_m.group(1) if br_m else "")
        )
        is_primary_elr_name = bool(br_sub) and len(br_sub) >= 2 and elr.lower().startswith(br_sub)
        is_named_branch = bool(br_m) and not is_primary_elr_name and (
            direction_hint in ("up", "down")
            and not bool(
                th_toks
                & {
                    "dm", "um", "df", "uf", "ds", "us", "dr", "ur", "dg", "ug",
                    "dl", "ul", "dml", "uml", "dfl", "ufl", "dsl", "usl",
                    "drl", "url", "dgl", "ugl", "dpl", "upl",
                }
            )
            and not bool(
                re.search(
                    r"\b(?:main|fast|slow|relief|goods|freight|indep[a-z]*|loop|single|siding|yard|depot|platform|bay|terminal)\b",
                    th_l,
                )
            )
        )
        if is_named_branch or is_ul_dl_branch:
            want_d1 = "2" if direction_hint == "down" else "1"
            ref_pt_xy = _get_spatial_anchor(candidates, km_meters)
            if ref_pt_xy:
                rx, ry = ref_pt_xy
                # 1) Same-chainage flyover ELR (e.g. LEC6 1101, WJL1 1100/2100).
                cur.execute(
                    """
                    SELECT c.fid, c.asset_id, c.elr, c.track_id, c.start_mile_yards, c.end_mile_yards,
                           c.start_meters, c.end_meters, c.length_meters, c.owner, c.coords,
                           c.start_node_id, c.end_node_id, c.min_x, c.max_x, c.min_y, c.max_y,
                           (SELECT MIN(LEAST(c2.start_meters, c2.end_meters))
                            FROM nwr_track_centrelines c2 WHERE c2.elr = c.elr) AS elr_min_m
                    FROM nwr_track_centrelines c
                    WHERE c.elr <> %s
                      AND SUBSTRING(c.track_id, 1, 1) = %s
                      AND SUBSTRING(c.track_id, 2, 1) = '1'
                      AND LEAST(c.start_meters, c.end_meters) <= %s
                      AND GREATEST(c.start_meters, c.end_meters) >= %s
                      AND c.max_x >= %s - 1500.0 AND c.min_x <= %s + 1500.0
                      AND c.max_y >= %s - 1500.0 AND c.min_y <= %s + 1500.0
                    LIMIT 8
                    """,
                    (elr, want_d1, target_m, target_m, rx, rx, ry, ry),
                )
                branch_rows = cur.fetchall()
                if is_ul_dl_branch and not is_named_branch:
                    # Only jump to an adjacent ELR that originates near this
                    # junction (never back onto a trunk ELR starting >1.5km away).
                    branch_rows = [
                        r for r in branch_rows
                        if r.get("elr_min_m") is not None and abs(float(r["elr_min_m"]) - target_m) <= 1500.0
                    ]
                if branch_rows:
                    existing_fids = {c["fid"] for c in candidates}
                    candidates.extend(
                        {**r, "_flyover_branch": True}
                        for r in branch_rows
                        if r["fid"] not in existing_fids
                    )
                elif is_named_branch and target_m >= 5000.0 and br_m is not None:
                    # 2) Re-chained branch ELR starting near 0m (e.g. NBS 1100/2100).
                    cur.execute(
                        """
                        SELECT fid, asset_id, elr, track_id, start_mile_yards, end_mile_yards,
                               start_meters, end_meters, length_meters, owner, coords,
                               start_node_id, end_node_id, min_x, max_x, min_y, max_y
                        FROM nwr_track_centrelines
                        WHERE elr <> %s
                          AND SUBSTRING(track_id, 1, 1) = %s
                          AND SUBSTRING(track_id, 2, 1) = '1'
                          AND LEAST(start_meters, end_meters) < 5000.0
                          AND ABS(((start_meters + end_meters) / 2.0) - %s) > 10000.0
                          AND max_x >= %s - 1500.0 AND min_x <= %s + 1500.0
                          AND max_y >= %s - 1500.0 AND min_y <= %s + 1500.0
                        LIMIT 16
                        """,
                        (elr, want_d1, target_m, rx, rx, ry, ry),
                    )
                    adj_rows = cur.fetchall()
                    adj_elrs = {r["elr"] for r in adj_rows}
                    chosen_adj = adj_rows if len(adj_elrs) == 1 else []
                    if not chosen_adj and adj_rows:
                        br_name = br_m.group(0).upper()
                        cur.execute(
                            """
                            SELECT DISTINCT n.km_region_id
                            FROM station_tracks st
                            JOIN nodes n ON n.node_id = ANY(st.node_ids)
                            WHERE UPPER(st.name) = %s AND n.km_region_id IS NOT NULL
                            """,
                            (br_name,),
                        )
                        st_elrs = {
                            decode_code("kmregionmasterdesc", row["km_region_id"])
                            for row in cur.fetchall()
                        }
                        matched_elrs = adj_elrs & st_elrs
                        if len(matched_elrs) == 1:
                            chosen_adj = [r for r in adj_rows if r["elr"] in matched_elrs]
                    if chosen_adj:
                        existing_fids = {c["fid"] for c in candidates}
                        candidates.extend(
                            {**r, "_cross_elr": True, "_flyover_branch": True}
                            for r in chosen_adj
                            if r["fid"] not in existing_fids
                        )

    # Zero-turnout valency-2 cross-ELR chord fallback (e.g. Norton Bridge
    # NS5688/NS5691 tagged LEC4 on the southern end of NBS1 3100).
    has_primary_rev_main = any(
        len(str(c.get("track_id") or "")) == 4 and str(c["track_id"])[:2] in ("31", "34")
        for c in candidates
    )
    if tps_node_ids and not has_primary_rev_main and not any(c.get("_flyover_branch") for c in candidates):
        if ref_pt_xy is None:
            ref_pt_xy = _get_spatial_anchor(candidates, km_meters)
        if ref_pt_xy:
            rx, ry = ref_pt_xy
            cur.execute(
                """
                SELECT fid, asset_id, elr, track_id, start_mile_yards, end_mile_yards,
                       start_meters, end_meters, length_meters, owner, coords,
                       start_node_id, end_node_id, min_x, max_x, min_y, max_y
                FROM nwr_track_centrelines
                WHERE elr <> %s
                  AND track_id IN ('3100', '3400')
                  AND max_x >= %s - 1200.0 AND min_x <= %s + 1200.0
                  AND max_y >= %s - 1200.0 AND min_y <= %s + 1200.0
                LIMIT 8
                """,
                (elr, rx, rx, ry, ry),
            )
            near_chords = cur.fetchall()
            if near_chords:
                adj_elr = _find_valency2_adjacent_elr(cur, tps_node_ids, elr, max_hops=10)
                if adj_elr:
                    matching_chords = [
                        r for r in near_chords
                        if r["elr"] == adj_elr or r["elr"].startswith(adj_elr)
                    ]
                    if matching_chords:
                        existing_fids = {c["fid"] for c in candidates}
                        candidates.extend(
                            {**r, "_cross_elr": True, "_valency2_chord": True}
                            for r in matching_chords
                            if r["fid"] not in existing_fids
                        )

    return candidates, ref_pt_xy


def _compute_switch_track_boost(
    cur: Any,
    raw_candidates: list[dict[str, Any]],
    target_m: Optional[float],
    asset_type: Optional[str],
    tps_valency: Optional[int],
) -> dict[str, float]:
    """Boost track IDs that have a physical switch endpoint near ``target_m``.

    For points/turnouts, candidates whose start or end node is a valency >= 3
    physical switch within 15m (+60) or 35m (+35) of ``target_m`` receive a
    tie-breaking score boost.

    Args:
        cur: Active database cursor.
        raw_candidates: Candidate centreline rows.
        target_m: Target chainage in metres.
        asset_type: Asset type string (e.g. ``"points"``).
        tps_valency: Degree of the TPS node, if known.

    Returns:
        Mapping ``track_id -> boost_score``.
    """
    is_pts_asset = (asset_type == "points") or (tps_valency is not None and tps_valency >= 3)
    switch_track_boost: dict[str, float] = {}
    if not (is_pts_asset and target_m is not None and len(raw_candidates) > 1):
        return switch_track_boost

    near_node_ids: set[Any] = set()
    cand_endpoint_dists: list[tuple[str, Any, float]] = []
    for c in raw_candidates:
        if c.get("start_node_id") and c.get("start_meters") is not None:
            d_s = abs(float(c["start_meters"]) - float(target_m))
            if d_s <= 35.0:
                near_node_ids.add(c["start_node_id"])
                cand_endpoint_dists.append((c["track_id"], c["start_node_id"], d_s))
        if c.get("end_node_id") and c.get("end_meters") is not None:
            d_e = abs(float(c["end_meters"]) - float(target_m))
            if d_e <= 35.0:
                near_node_ids.add(c["end_node_id"])
                cand_endpoint_dists.append((c["track_id"], c["end_node_id"], d_e))

    if near_node_ids:
        cur.execute(
            "SELECT asset_id FROM nwr_track_nodes WHERE asset_id = ANY(%s) AND valency >= 3",
            (list(near_node_ids),),
        )
        sw_ids = {r["asset_id"] for r in cur.fetchall()}
        for tid_c, nid_c, d_c in cand_endpoint_dists:
            if nid_c in sw_ids:
                boost = 60.0 if d_c <= 15.0 else 35.0
                if boost > switch_track_boost.get(tid_c, 0.0):
                    switch_track_boost[tid_c] = boost
    return switch_track_boost


def _compute_34xx_continuation_boost(
    cur: Any,
    elr: str,
    raw_candidates: list[dict[str, Any]],
    target_m: Optional[float],
    platform_num: Optional[int],
    track_name_hint: Optional[str],
    direction_hint: Optional[str],
    secondary_dir: Optional[str],
) -> set[str]:
    """Identify covering ``34xx`` tracks that continue into the requested line.

    Resolves ties among multiple parallel bi-directional approach tracks (e.g.
    Cardiff Central ``3402``/``3403``/``3412``/``3413``) when no primary
    ``11xx``/``21xx``/``12xx``/``22xx`` track covers ``target_m``.

    Args:
        cur: Active database cursor.
        elr: Primary ELR code.
        raw_candidates: Candidate centreline rows.
        target_m: Target chainage in metres.
        platform_num: Extracted platform number (must be ``None`` to activate).
        track_name_hint: TPS track name hint.
        direction_hint: Primary direction hint.
        secondary_dir: Secondary direction hint for reversible lines.

    Returns:
        Set of ``34xx`` track IDs that should receive the continuation boost.
    """
    if not (platform_num is None and track_name_hint and target_m is not None):
        return set()

    covering_34 = {
        str(c["track_id"])
        for c in raw_candidates
        if c.get("elr") == elr
        and str(c.get("track_id") or "").startswith("34")
        and min(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0)
        <= target_m
        <= max(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0)
    }
    if len(covering_34) < 2:
        return set()

    th_c = track_name_hint.lower()
    th_ctoks = set(re.findall(r"[a-z0-9]+", th_c))
    dh_c = (direction_hint or "").lower()
    if "up" in dh_c or (dh_c == "reversible" and secondary_dir == "up"):
        want_d1_c = "1"
    elif "down" in dh_c or dh_c == "dn" or (dh_c == "reversible" and secondary_dir == "down"):
        want_d1_c = "2"
    else:
        want_d1_c = ""

    if bool(re.search(r"\b(?:slow|relief)\b", th_c)) or bool(
        th_ctoks & {"ds", "us", "dr", "ur", "sl", "rl", "dsl", "usl", "drl", "url"}
    ):
        want_d2_c = "2"
    elif bool(re.search(r"\b(?:fast|main)\b", th_c)) or bool(
        th_ctoks & {"dm", "um", "df", "uf", "ml", "fl", "dml", "uml", "dfl", "ufl"}
    ):
        want_d2_c = "1"
    else:
        want_d2_c = ""

    want_pref = want_d1_c + want_d2_c
    if len(want_pref) != 2:
        return set()

    already_covered = any(
        c.get("elr") == elr
        and str(c.get("track_id") or "").startswith(want_pref)
        and min(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0)
        <= target_m
        <= max(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0)
        for c in raw_candidates
    )
    if already_covered:
        return set()
    return _find_34xx_continuation_tracks(cur, elr, covering_34, want_pref, target_m)


def _snap_point_to_nwr_node(
    cur: Any,
    pt_main: list[float],
    best_candidate: dict[str, Any],
    asset_type: Optional[str],
    tps_valency: Optional[int],
) -> Optional[dict[str, Any]]:
    """Snap an interpolated geographic point to the nearest physical NWR node.

    Queries ``nwr_track_nodes`` within +/-90m of ``pt_main`` and prioritizes
    nodes lying on ``best_candidate`` (and switches with ``valency >= 3`` when
    matching points/turnouts).

    Args:
        cur: Active database cursor.
        pt_main: ``[easting, northing]`` interpolated point on the centreline.
        best_candidate: Winning NWR centreline dictionary.
        asset_type: TPS asset type (``"points"``, ``"signal"``, ``"node"``, etc.).
        tps_valency: TPS node degree, if applicable.

    Returns:
        Dictionary describing the snapped physical node, or ``None`` if no
        physical node exists within 90m.
    """
    cur.execute(
        """
        SELECT n.fid, n.asset_id, n.valency, n.x, n.y,
               SQRT(POWER(n.x - %s, 2) + POWER(n.y - %s, 2)) AS dist,
               EXISTS (
                   SELECT 1
                   FROM nwr_track_centrelines c
                   WHERE c.elr = %s
                     AND c.track_id = %s
                     AND (c.start_node_id = n.asset_id OR c.end_node_id = n.asset_id)
               ) AS on_matched_track
        FROM nwr_track_nodes n
        WHERE n.x BETWEEN %s - 90.0 AND %s + 90.0
          AND n.y BETWEEN %s - 90.0 AND %s + 90.0
        ORDER BY dist ASC
        LIMIT 32
        """,
        (
            pt_main[0],
            pt_main[1],
            best_candidate["elr"],
            best_candidate["track_id"],
            pt_main[0],
            pt_main[0],
            pt_main[1],
            pt_main[1],
        ),
    )
    nearby_nodes = cur.fetchall()
    if not nearby_nodes:
        return None

    is_pts_snap = (asset_type == "points") or (tps_valency is not None and tps_valency >= 3)
    if is_pts_snap:
        # Priority: (1) switch on matched track, (2) switch within 20m,
        # (3) any node on matched track, (4) nearest switch, (5) nearest node.
        sw_on_trk = [nd for nd in nearby_nodes if nd["valency"] >= 3 and nd["on_matched_track"]]
        sw_close = [nd for nd in nearby_nodes if nd["valency"] >= 3 and nd["dist"] <= 20.0]
        any_on_trk = [nd for nd in nearby_nodes if nd["on_matched_track"]]
        sw_any = [nd for nd in nearby_nodes if nd["valency"] >= 3]
        picked = (
            sw_on_trk[0]
            if sw_on_trk
            else (
                sw_close[0]
                if sw_close
                else (
                    any_on_trk[0]
                    if any_on_trk
                    else (sw_any[0] if sw_any else nearby_nodes[0])
                )
            )
        )
    else:
        any_on_trk = [nd for nd in nearby_nodes if nd["on_matched_track"] and nd["dist"] <= 45.0]
        picked = any_on_trk[0] if any_on_trk else nearby_nodes[0]

    return {
        "fid": picked["fid"],
        "asset_id": picked["asset_id"],
        "valency": picked["valency"],
        "valency_desc": VALENCY_DESC.get(picked["valency"], f"Valency {picked['valency']}"),
        "x": picked["x"],
        "y": picked["y"],
        "dist_meters": round(picked["dist"], 2),
        "is_switch": picked["valency"] >= 3,
        "on_matched_track": bool(picked["on_matched_track"]),
    }


def _build_discrete_node_points(
    orig_coords: list,
    c_start_m: float,
    c_end_m: float,
    km_meters: float,
    end_km_meters: Optional[float],
    pt_main: list[float],
    snapped_nwr_node: Optional[dict[str, Any]],
    asset_type: Optional[str],
    tps_valency: Optional[int],
    item_label: Optional[str],
) -> list[dict[str, Any]]:
    """Construct display marker points for the geographic map overlay.

    Edges (where ``end_km_meters`` differs from ``km_meters`` by >= 1m) emit
    start and end markers; point assets emit a single styled marker tailored to
    signals, buffer stops, points/turnouts, or plain nodes.

    Args:
        orig_coords: Unclipped centreline polyline coordinates.
        c_start_m: Centreline start chainage in metres.
        c_end_m: Centreline end chainage in metres.
        km_meters: Primary asset chainage in metres.
        end_km_meters: Optional end chainage in metres (for edges).
        pt_main: Interpolated primary ``[x, y]`` coordinate.
        snapped_nwr_node: Snapped physical NWR node dictionary, or ``None``.
        asset_type: TPS asset type string.
        tps_valency: TPS node degree, if applicable.
        item_label: Human-readable asset label.

    Returns:
        List of 1 or 2 marker dictionaries.
    """
    node_points: list[dict[str, Any]] = []
    if end_km_meters is not None and abs(end_km_meters - km_meters) >= 1.0:
        pt_end = interpolate_pt_along_coords(orig_coords, c_start_m, c_end_m, end_km_meters)
        node_points.append({
            "x": pt_main[0],
            "y": pt_main[1],
            "label": f"Start Node ({meters_to_miles_chains(int(km_meters))})",
            "color": "#38bdf8",
            "shape": "circle",
            "snapped_node": snapped_nwr_node,
        })
        if pt_end:
            node_points.append({
                "x": pt_end[0],
                "y": pt_end[1],
                "label": f"End Node ({meters_to_miles_chains(int(end_km_meters))})",
                "color": "#10b981",
                "shape": "circle",
            })
        return node_points

    m_suffix = f" ({meters_to_miles_chains(int(km_meters))})" if km_meters is not None else ""
    if asset_type == "signal":
        is_buf = bool(item_label and "buffer" in item_label.lower())
        pt_icon = "🛑" if is_buf else "🚦"
        pt_label = f"{pt_icon} {item_label or 'Signal'}{m_suffix}"
        pt_col = "#ef4444" if is_buf else "#f59e0b"
        pt_shape = "signal"
        use_x, use_y = pt_main[0], pt_main[1]
    elif asset_type == "points":
        use_x = snapped_nwr_node["x"] if snapped_nwr_node else pt_main[0]
        use_y = snapped_nwr_node["y"] if snapped_nwr_node else pt_main[1]
        pt_label = f"🔀 {item_label or 'Points'}{m_suffix}"
        pt_col = "#a855f7"
        pt_shape = "diamond"
    elif asset_type == "node":
        use_x = snapped_nwr_node["x"] if snapped_nwr_node else pt_main[0]
        use_y = snapped_nwr_node["y"] if snapped_nwr_node else pt_main[1]
        if tps_valency and tps_valency >= 3:
            pt_label = f"🔀 {item_label or 'Points Node'}{m_suffix}"
            pt_col = "#a855f7"
            pt_shape = "diamond"
        else:
            pt_label = f"📍 {item_label or 'Track Node'}{m_suffix}"
            pt_col = "#10b981"
            pt_shape = "circle"
    else:
        if snapped_nwr_node and snapped_nwr_node["is_switch"]:
            pt_label = f"🔀 Points #{snapped_nwr_node['asset_id']}{m_suffix}"
            pt_col = "#a855f7"
            pt_shape = "diamond"
            use_x, use_y = snapped_nwr_node["x"], snapped_nwr_node["y"]
        elif snapped_nwr_node:
            pt_label = f"📍 Physical Node #{snapped_nwr_node['asset_id']}{m_suffix}"
            pt_col = "#38bdf8"
            pt_shape = "circle"
            use_x, use_y = snapped_nwr_node["x"], snapped_nwr_node["y"]
        else:
            pt_label = f"{item_label or 'Point'}{m_suffix}"
            pt_col = "#38bdf8"
            pt_shape = "circle"
            use_x, use_y = pt_main[0], pt_main[1]

    node_points.append({
        "x": use_x,
        "y": use_y,
        "label": pt_label,
        "color": pt_col,
        "shape": pt_shape,
        "snapped_node": snapped_nwr_node,
    })
    return node_points


def _build_geo_snap(
    elr: str,
    km_meters: Optional[float],
    end_km_meters: Optional[float],
    node_points: list[dict[str, Any]],
    best_candidate: Optional[dict[str, Any]],
    is_extrapolated: bool,
) -> Optional[dict[str, Any]]:
    """Compute the geographic camera target and zoom level for the matched asset.

    Args:
        elr: Primary ELR code.
        km_meters: Start/point chainage in metres.
        end_km_meters: End chainage in metres for edges, or ``None``.
        node_points: Discrete overlay markers generated for the asset.
        best_candidate: Winning centreline dictionary.
        is_extrapolated: Whether the match fell back outside the normal search window.

    Returns:
        Camera framing dictionary with ``x``, ``y``, ``target_zoom``, and
        mileage metadata, or ``None`` if no coordinates exist.
    """
    if node_points:
        if len(node_points) == 1:
            return {
                "x": node_points[0]["x"],
                "y": node_points[0]["y"],
                "elr": elr,
                "mileage_meters": km_meters,
                "mileage_str": meters_to_miles_chains(int(km_meters)) if km_meters else None,
                "target_zoom": 0.28,
                "is_extrapolated": is_extrapolated,
            }
        p1, p2 = node_points[0], node_points[1]
        mid_x = round((p1["x"] + p2["x"]) / 2.0, 3)
        mid_y = round((p1["y"] + p2["y"]) / 2.0, 3)
        span = math.hypot(p2["x"] - p1["x"], p2["y"] - p1["y"])
        calc_zoom = round(min(0.28, max(0.04, 450.0 / max(span, 250.0))), 3)
        mid_m = ((km_meters or 0) + (end_km_meters or 0)) / 2.0
        return {
            "x": mid_x,
            "y": mid_y,
            "elr": elr,
            "mileage_meters": mid_m,
            "mileage_str": meters_to_miles_chains(int(mid_m)),
            "target_zoom": calc_zoom,
            "is_extrapolated": is_extrapolated,
        }

    if best_candidate and best_candidate.get("coords"):
        mid_c = best_candidate["coords"][len(best_candidate["coords"]) // 2]
        return {
            "x": mid_c[0],
            "y": mid_c[1],
            "elr": elr,
            "mileage_meters": km_meters,
            "mileage_str": meters_to_miles_chains(int(km_meters)) if km_meters else None,
            "target_zoom": 0.15,
            "is_extrapolated": is_extrapolated,
        }
    return None


def find_nwr_cross_links_for_tps(
    cur: Any,
    elr: Optional[str],
    km_meters: Optional[float],
    end_km_meters: Optional[float] = None,
    direction_hint: Optional[str] = None,
    track_name_hint: Optional[str] = None,
    tps_valency: Optional[int] = None,
    item_label: Optional[str] = None,
    is_extrapolated: bool = False,
    asset_type: Optional[str] = None,
    preferred_fid: Optional[int] = None,
    match_source: Optional[str] = None,
    platform_num: Optional[int] = None,
    saved_fid: Optional[int] = None,
    secondary_dir: Optional[str] = None,
    edge_vmax: Optional[int] = None,
    tps_node_ids: Optional[list] = None,
) -> dict[str, Any]:
    """Match a TPS rail asset to its geographic NWR Track Model centreline.

    Executes the full multi-stage matching pipeline:
      1. Look up the NWR ELR record and primary-ELR candidate centrelines.
      2. Augment with cross-ELR hub platforms, branch bays, flyovers, or
         valency-2 single-track chords when appropriate.
      3. Compute physical switch-endpoint and ``34xx`` continuation boosts.
      4. Score all candidates via :func:`score_track_candidate` and apply any
         saved/manual override.
      5. Clip edge polylines to ``[km_meters, end_km_meters]``, snap point
         coordinates to physical NWR nodes, and frame the camera target.

    Args:
        cur: Active database cursor returning dict rows.
        elr: Primary Engineer's Line Reference code.
        km_meters: Primary asset chainage in metres.
        end_km_meters: Optional end chainage in metres (for edges).
        direction_hint: ``"up"``, ``"down"``, ``"reversible"``, or ``None``.
        track_name_hint: Optional TPS track/platform/branch label.
        tps_valency: Optional TPS node degree (>= 3 indicates points/turnout).
        item_label: Human-readable label for map markers.
        is_extrapolated: Whether ``km_meters`` was extrapolated by the caller.
        asset_type: Asset type string (``"signal"``, ``"points"``, ``"edge"``,
            ``"node"``, etc.).
        preferred_fid: Transient manual NWR feature ID override.
        match_source: Human-readable label describing how the hint was derived.
        platform_num: Extracted integer platform number, if applicable.
        saved_fid: Persisted manual NWR feature ID override from DB.
        secondary_dir: Secondary direction hint for reversible tracks.
        edge_vmax: Maximum line speed in km/h for fast/slow disambiguation.
        tps_node_ids: TPS node IDs for valency-2 cross-ELR chord walks.

    Returns:
        Cross-link dictionary containing ``elr_record``, ``best_centreline``,
        ``corridor_centrelines``, ``node_points``, ``snapped_node``,
        ``geo_snap``, and ``match_info``.
    """
    if not elr:
        return _empty_nwr_cross_links()
    if item_label:
        lbl_u = item_label.strip().upper()
        if re.search(r"\bUBS\b", lbl_u):
            direction_hint = "up"
            track_name_hint = "Up Bay Terminal"
        elif re.search(r"\bDBS\b", lbl_u):
            direction_hint = "down"
            track_name_hint = "Down Bay Terminal"
    try:
        cur.execute(
            "SELECT fid, asset_id, elr, start_mile_yards, end_mile_yards, parts, min_x, max_x, min_y, max_y FROM nwr_elrs WHERE elr = %s LIMIT 1",
            (elr,),
        )
        elr_rec = cur.fetchone()

        target_m = km_meters
        if km_meters is not None and end_km_meters is not None:
            target_m = (km_meters + end_km_meters) / 2.0
            span_min: Optional[float] = min(km_meters, end_km_meters) - 300.0
            span_max: Optional[float] = max(km_meters, end_km_meters) + 300.0
        elif km_meters is not None:
            span_min = km_meters - 400.0
            span_max = km_meters + 400.0
        else:
            span_min = None
            span_max = None

        effective_pref_fid = preferred_fid if preferred_fid is not None else saved_fid
        raw_candidates, is_extrapolated = _fetch_primary_candidates(
            cur, elr, target_m, span_min, span_max, effective_pref_fid, is_extrapolated
        )

        if not raw_candidates:
            geo_snap = None
            if elr_rec and elr_rec.get("parts"):
                p0 = elr_rec["parts"][0]
                mid_pt = p0[len(p0) // 2] if p0 else None
                if mid_pt:
                    geo_snap = {
                        "x": mid_pt[0],
                        "y": mid_pt[1],
                        "elr": elr,
                        "mileage_meters": target_m,
                        "mileage_str": meters_to_miles_chains(int(target_m)) if target_m else None,
                        "target_zoom": 0.08,
                        "is_extrapolated": True,
                    }
            return _empty_nwr_cross_links(elr_rec=elr_rec, geo_snap=geo_snap)

        # Stage 2: Cross-ELR augmentation (hub platforms, branch bays, flyovers, chords).
        ref_pt_xy: Optional[tuple[float, float]] = None
        if platform_num is not None and 1 <= platform_num <= 99:
            raw_candidates, ref_pt_xy = _augment_platform_cross_elr_candidates(
                cur, elr, raw_candidates, target_m, km_meters, platform_num, track_name_hint
            )
        elif platform_num is None and track_name_hint and target_m is not None:
            raw_candidates, ref_pt_xy = _augment_running_line_cross_elr_candidates(
                cur, elr, raw_candidates, target_m, km_meters, direction_hint, track_name_hint, tps_node_ids
            )

        # Stage 3: Structural boosts (switch endpoints & 34xx continuations).
        switch_track_boost = _compute_switch_track_boost(
            cur, raw_candidates, target_m, asset_type, tps_valency
        )
        cont_34_boost = _compute_34xx_continuation_boost(
            cur, elr, raw_candidates, target_m, platform_num, track_name_hint, direction_hint, secondary_dir
        )

        # Stage 4: Score every candidate and rank descending.
        candidates = []
        for c in raw_candidates:
            # Evaluate spatially matched cross-ELR platforms at their own midpoint
            # so a different ELR chainage origin does not penalize coverage.
            cand_target_m = (
                (float(c["start_meters"]) + float(c["end_meters"])) / 2.0
                if c.get("_cross_elr")
                else target_m
            )
            score = score_track_candidate(
                c["track_id"],
                direction_hint,
                track_name_hint,
                c["start_meters"],
                c["end_meters"],
                cand_target_m,
                asset_type=asset_type,
                platform_num=platform_num,
                secondary_dir=secondary_dir,
                edge_vmax=edge_vmax,
            )
            if switch_track_boost:
                score += switch_track_boost.get(c["track_id"], 0.0)
            if c.get("_flyover_branch"):
                score += 25.0
            if c.get("_branch_bay"):
                score += 65.0
            if cont_34_boost and c.get("elr") == elr and str(c.get("track_id") or "") in cont_34_boost:
                score += 95.0
            if c.get("_valency2_chord"):
                score += 75.0
            candidates.append({
                **c,
                "track_id_decoded": decode_track_id(c["track_id"]),
                "_score": score,
            })

        candidates.sort(key=lambda x: x["_score"], reverse=True)
        auto_best_fid = candidates[0]["fid"]
        manual_override = False
        is_saved_override = False
        best_candidate = dict(candidates[0])
        if effective_pref_fid is not None:
            for c in candidates:
                if c["fid"] == effective_pref_fid:
                    best_candidate = dict(c)
                    is_saved_override = saved_fid is not None and c["fid"] == saved_fid
                    manual_override = c["fid"] != auto_best_fid or is_saved_override
                    break

        # Stage 5: Optional edge polyline clipping to [km_meters, end_km_meters].
        orig_coords = list(best_candidate.get("coords") or [])
        c_start_m = best_candidate["start_meters"]
        c_end_m = best_candidate["end_meters"]

        is_clipped = False
        clipped_len = best_candidate.get("length_meters")
        if (
            km_meters is not None
            and end_km_meters is not None
            and abs(end_km_meters - km_meters) >= 1.0
            and orig_coords
        ):
            sliced = slice_polyline(
                orig_coords,
                c_start_m,
                c_end_m,
                min(km_meters, end_km_meters),
                max(km_meters, end_km_meters),
            )
            if sliced and len(sliced) >= 2:
                best_candidate["coords"] = sliced
                best_candidate["clipped"] = True
                clipped_len = round(abs(end_km_meters - km_meters), 1)
                best_candidate["clipped_length_meters"] = clipped_len
                is_clipped = True

        # Stage 6: Discrete marker placement and track-constrained node snapping.
        node_points: list[dict[str, Any]] = []
        snapped_nwr_node: Optional[dict[str, Any]] = None

        if km_meters is not None and orig_coords:
            if best_candidate.get("_cross_elr") and ref_pt_xy:
                # Project the primary-ELR spatial anchor onto the cross-ELR polyline.
                rx, ry = ref_pt_xy
                best_proj = orig_coords[len(orig_coords) // 2]
                best_d2 = float("inf")
                for pt_c in orig_coords:
                    d2 = (pt_c[0] - rx) ** 2 + (pt_c[1] - ry) ** 2
                    if d2 < best_d2:
                        best_d2 = d2
                        best_proj = pt_c
                pt_main: Optional[list[float]] = [round(best_proj[0], 3), round(best_proj[1], 3)]
            else:
                pt_main = interpolate_pt_along_coords(orig_coords, c_start_m, c_end_m, km_meters)

            if pt_main:
                snapped_nwr_node = _snap_point_to_nwr_node(
                    cur, pt_main, best_candidate, asset_type, tps_valency
                )
                node_points = _build_discrete_node_points(
                    orig_coords,
                    c_start_m,
                    c_end_m,
                    km_meters,
                    end_km_meters,
                    pt_main,
                    snapped_nwr_node,
                    asset_type,
                    tps_valency,
                    item_label,
                )

        geo_snap = _build_geo_snap(
            elr, km_meters, end_km_meters, node_points, best_candidate, is_extrapolated
        )

        return {
            "elr_record": elr_rec,
            "centrelines": [best_candidate],
            "corridor_centrelines": candidates[:24],
            "best_centreline": best_candidate,
            "node_point": node_points[0] if node_points else None,
            "node_points": node_points,
            "snapped_node": snapped_nwr_node,
            "geo_snap": geo_snap,
            "clipped": is_clipped,
            "clipped_length_meters": clipped_len if is_clipped else None,
            "single_match": True,
            "match_info": {
                "direction_hint": direction_hint,
                "track_name_hint": track_name_hint,
                "platform_num": platform_num,
                "source": (
                    "Saved Track Override"
                    if is_saved_override
                    else (
                        "Manual Track Override"
                        if manual_override
                        else (match_source or "ELR & Mileage Match")
                    )
                ),
                "manual_override": manual_override,
                "saved_override": is_saved_override,
                "saved_fid": saved_fid,
                "auto_best_fid": auto_best_fid,
            },
        }
    except Exception:  # pylint: disable=broad-exception-caught
        return _empty_nwr_cross_links()


def find_tps_cross_links(
    cur: Any,
    elr: str,
    min_m: Optional[float] = None,
    max_m: Optional[float] = None,
) -> dict[str, Any]:
    """Build the relational bridge from NWR Track Model (ELR + metres) to TPS.

    Resolves matching ``kmregionmasterdesc`` lookup codes for ``elr`` and
    fetches TPS stations, nodes count, edges, signals, and attribute sections
    within the requested chainage window (expanded by +/-450m when bounded).

    Args:
        cur: Active database cursor returning dict rows.
        elr: Engineer's Line Reference code.
        min_m: Optional lower chainage bound in metres.
        max_m: Optional upper chainage bound in metres.

    Returns:
        Dictionary with keys ``km_regions``, ``tps_stations``,
        ``tps_attribute_sections``, ``tps_nodes_count``, ``tps_edges``, and
        ``tps_signals``.
    """
    if not elr:
        return {
            "km_regions": [],
            "tps_stations": [],
            "tps_attribute_sections": [],
            "tps_nodes_count": 0,
            "tps_edges": [],
            "tps_signals": [],
        }

    cur.execute(
        """
        SELECT code_id, label, vanilla_text
        FROM lookup_codes
        WHERE category = 'kmregionmasterdesc'
          AND (UPPER(vanilla_text) = UPPER(%s) OR UPPER(label) = UPPER(%s))
        """,
        (elr.strip(), elr.strip()),
    )
    km_regs = cur.fetchall()
    km_ids = [int(r["code_id"]) for r in km_regs if str(r["code_id"]).isdigit()]

    tps_stations: list[dict[str, Any]] = []
    tps_nodes_count = 0
    tps_edges: list[dict[str, Any]] = []
    tps_signals: list[dict[str, Any]] = []

    if km_ids:
        cur.execute(
            """
            SELECT station_id, abbrev, longname, crscode, stanox, km_value, x, y
            FROM stations
            WHERE km_region_id = ANY(%s)
            ORDER BY km_value ASC
            LIMIT 40
            """,
            (km_ids,),
        )
        tps_stations = cur.fetchall()

        if min_m is not None and max_m is not None:
            lo = min(min_m, max_m) - 450.0
            hi = max(min_m, max_m) + 450.0
            cur.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM nodes
                WHERE km_region_id = ANY(%s) AND km_value BETWEEN %s AND %s
                """,
                (km_ids, lo, hi),
            )
            tps_nodes_count = cur.fetchone()["cnt"]

            cur.execute(
                """
                SELECT e.pk, e.edge_id, e.x0, e.y0, e.x1, e.y1, e.length, e.vmax, n1.km_value
                FROM edges e
                JOIN nodes n1 ON n1.node_id = e.start_node_id
                WHERE n1.km_region_id = ANY(%s) AND n1.km_value BETWEEN %s AND %s
                LIMIT 25
                """,
                (km_ids, lo, hi),
            )
            tps_edges = cur.fetchall()

            cur.execute(
                """
                SELECT s.pk, s.signal_id, s.name, s.master_name, s.x, s.y, n.km_value
                FROM signals s
                LEFT JOIN nodes n ON n.node_id = COALESCE(s.end_node_id, s.start_node_id, s.point_node_id)
                WHERE (s.km_region_id = ANY(%s) OR n.km_region_id = ANY(%s))
                  AND n.km_value BETWEEN %s AND %s
                LIMIT 20
                """,
                (km_ids, km_ids, lo, hi),
            )
            tps_signals = cur.fetchall()
        else:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM nodes WHERE km_region_id = ANY(%s)",
                (km_ids,),
            )
            tps_nodes_count = cur.fetchone()["cnt"]

            cur.execute(
                """
                SELECT e.pk, e.edge_id, e.x0, e.y0, e.x1, e.y1, e.length, e.vmax
                FROM edges e
                JOIN nodes n1 ON n1.node_id = e.start_node_id
                WHERE n1.km_region_id = ANY(%s)
                LIMIT 30
                """,
                (km_ids,),
            )
            tps_edges = cur.fetchall()

            cur.execute(
                """
                SELECT s.pk, s.signal_id, s.name, s.master_name, s.x, s.y
                FROM signals s
                LEFT JOIN nodes n ON n.node_id = COALESCE(s.end_node_id, s.start_node_id, s.point_node_id)
                WHERE s.km_region_id = ANY(%s) OR n.km_region_id = ANY(%s)
                LIMIT 20
                """,
                (km_ids, km_ids),
            )
            tps_signals = cur.fetchall()

    cur.execute(
        """
        SELECT pk, id, section_id, km_region_id, short_description, description
        FROM attribute_sections
        WHERE UPPER(description) = UPPER(%s)
           OR (%s::int[] != '{}' AND km_region_id = ANY(%s::int[]))
        LIMIT 25
        """,
        (elr.strip(), km_ids, km_ids),
    )
    tps_attr_secs = cur.fetchall()

    return {
        "km_regions": km_regs,
        "tps_stations": tps_stations,
        "tps_attribute_sections": tps_attr_secs,
        "tps_nodes_count": tps_nodes_count,
        "tps_edges": tps_edges,
        "tps_signals": tps_signals,
    }


def find_network_fusion_for_elr(
    cur: Any,
    elr: str,
    min_yd: Optional[int] = None,
    max_yd: Optional[int] = None,
) -> dict[str, Any]:
    """Fetch and summarize Network Fusion (GeoFurlong) points and curvature.

    Retrieves elevation, climate, geology, and horizontal curvature data along
    ``elr`` (optionally bounded to ``[min_yd - 220, max_yd + 220]`` total yards)
    and computes aggregate corridor statistics.

    Args:
        cur: Active database cursor returning dict rows.
        elr: Engineer's Line Reference code.
        min_yd: Optional lower chainage bound in total yards.
        max_yd: Optional upper chainage bound in total yards.

    Returns:
        Dictionary with keys ``summary``, ``points``, and ``curvature``.
    """
    if not elr:
        return {"summary": None, "points": [], "curvature": []}

    elr_clean = elr.strip().upper()
    if min_yd is not None and max_yd is not None:
        lo_yd = max(0, min(min_yd, max_yd) - 220)
        hi_yd = max(min_yd, max_yd) + 220
        cur.execute(
            """
            SELECT pk, elr, total_yards, mileage, easting, northing,
                   elevation_m, min_air_temperature_c, max_air_temperature_c,
                   mean_wind_speed_ms, annual_rainfall_mm, material, deposit, bedrock,
                   place_name, place_distance_m, postcode, track_count_primary, olc
            FROM network_fusion_points
            WHERE UPPER(elr) = %s AND total_yards BETWEEN %s AND %s
            ORDER BY total_yards ASC
            LIMIT 3500
            """,
            (elr_clean, lo_yd, hi_yd),
        )
    else:
        cur.execute(
            """
            SELECT pk, elr, total_yards, mileage, easting, northing,
                   elevation_m, min_air_temperature_c, max_air_temperature_c,
                   mean_wind_speed_ms, annual_rainfall_mm, material, deposit, bedrock,
                   place_name, place_distance_m, postcode, track_count_primary, olc
            FROM network_fusion_points
            WHERE UPPER(elr) = %s
            ORDER BY total_yards ASC
            LIMIT 7000
            """,
            (elr_clean,),
        )
    points = cur.fetchall()

    if min_yd is not None and max_yd is not None:
        lo_yd = max(0, min(min_yd, max_yd) - 220)
        hi_yd = max(min_yd, max_yd) + 220
        cur.execute(
            """
            SELECT pk, elr, total_yards_from, total_yards_to, mileage_from, mileage_to,
                   radius_min_m, radius_max_m, radius_avg_m, radius_avg_class,
                   x0, y0, x1, y1
            FROM network_fusion_curvature
            WHERE UPPER(elr) = %s AND total_yards_to >= %s AND total_yards_from <= %s
            ORDER BY total_yards_from ASC
            LIMIT 3500
            """,
            (elr_clean, lo_yd, hi_yd),
        )
    else:
        cur.execute(
            """
            SELECT pk, elr, total_yards_from, total_yards_to, mileage_from, mileage_to,
                   radius_min_m, radius_max_m, radius_avg_m, radius_avg_class,
                   x0, y0, x1, y1
            FROM network_fusion_curvature
            WHERE UPPER(elr) = %s
            ORDER BY total_yards_from ASC
            LIMIT 7000
            """,
            (elr_clean,),
        )
    curvature = cur.fetchall()

    summary = None
    if points or curvature:
        elevs = [p["elevation_m"] for p in points if p["elevation_m"] is not None]
        radii_avg = [c["radius_avg_m"] for c in curvature if c["radius_avg_m"] is not None]
        radii_min = [c["radius_min_m"] for c in curvature if c["radius_min_m"] is not None]
        tightest_c = min(curvature, key=lambda c: c["radius_min_m"] or 999999) if curvature else None
        places = [p["place_name"] for p in points if p["place_name"]]
        postcodes = [p["postcode"] for p in points if p["postcode"]]
        bedrocks = [p["bedrock"] for p in points if p["bedrock"]]

        summary = {
            "point_count": len(points),
            "curvature_count": len(curvature),
            "min_elevation_m": min(elevs) if elevs else None,
            "max_elevation_m": max(elevs) if elevs else None,
            "avg_radius_m": int(round(sum(radii_avg) / len(radii_avg))) if radii_avg else None,
            "min_radius_m": min(radii_min) if radii_min else None,
            "tightest_curve": (
                {
                    "class": tightest_c["radius_avg_class"],
                    "mileage": f"{tightest_c['mileage_from']} → {tightest_c['mileage_to']}",
                    "radius_min_m": tightest_c["radius_min_m"],
                }
                if tightest_c
                else None
            ),
            "nearest_places": list(dict.fromkeys(places))[:3],
            "postcodes": list(dict.fromkeys(postcodes))[:3],
            "bedrocks": list(dict.fromkeys(bedrocks))[:2],
        }

    return {
        "summary": summary,
        "points": points,
        "curvature": curvature,
    }
