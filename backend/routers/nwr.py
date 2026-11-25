"""NWR Track Model (OSGB36) geospatial API router and manual track overrides.

Provides overview/viewport queries, ELR/track/node search, coordinate picking,
NWR asset inspection with TPS and Network Fusion cross-links, and CRUD
endpoints for persisting manual TPS-to-NWR track overrides.
"""

import math
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from psycopg.rows import dict_row

from config import (
    DATABASE_URL,
    NF_DIR,
    NWR_DIR,
    ensure_track_overrides_table,
    get_conn,
)
from geometry import mile_yards_to_total_yards
from ingest_network_fusion import ensure_network_fusion_tables_and_ingest
from ingest_nwr import (
    VALENCY_DESC,
    decode_track_id,
    ensure_nwr_tables_and_ingest,
    format_mile_yards,
)
from track_matcher import find_network_fusion_for_elr, find_tps_cross_links

router = APIRouter(tags=["nwr"])

NWR_OVERVIEW_CACHE: Optional[dict[str, Any]] = None


def reset_nwr_overview_cache() -> None:
    """Clear the cached NWR overview payload (used by unit/E2E tests)."""
    global NWR_OVERVIEW_CACHE
    NWR_OVERVIEW_CACHE = None


@router.post("/api/nwr/track-override")
def api_save_track_override(payload: dict = Body(...)):
    asset_type = str(payload.get("asset_type") or "").strip().lower()
    asset_id = payload.get("asset_id")
    nwr_fid = payload.get("nwr_fid")
    if not asset_type or asset_id is None or nwr_fid is None:
        raise HTTPException(status_code=400, detail="asset_type, asset_id, and nwr_fid are required")
    with get_conn() as conn:
        with conn.cursor() as cur:
            ensure_track_overrides_table(cur)
            cur.execute(
                """
                INSERT INTO tps_nwr_track_overrides (asset_type, asset_id, nwr_fid, updated_at)
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (asset_type, asset_id)
                DO UPDATE SET nwr_fid = EXCLUDED.nwr_fid, updated_at = NOW()
                """,
                (asset_type, int(asset_id), int(nwr_fid)),
            )
            conn.commit()
    return {"ok": True, "asset_type": asset_type, "asset_id": int(asset_id), "nwr_fid": int(nwr_fid)}


@router.delete("/api/nwr/track-override/{asset_type}/{asset_id}")
def api_delete_track_override(asset_type: str, asset_id: int):
    atype = str(asset_type or "").strip().lower()
    with get_conn() as conn:
        with conn.cursor() as cur:
            ensure_track_overrides_table(cur)
            if atype in ("signal", "points"):
                cur.execute(
                    "DELETE FROM tps_nwr_track_overrides WHERE asset_type IN ('signal', 'points') AND asset_id = %s",
                    (asset_id,),
                )
            else:
                cur.execute(
                    "DELETE FROM tps_nwr_track_overrides WHERE asset_type = %s AND asset_id = %s",
                    (atype, asset_id),
                )
            conn.commit()
    return {"ok": True, "asset_type": atype, "asset_id": asset_id}



@router.get("/api/nwr/overview")
def api_nwr_overview():
    global NWR_OVERVIEW_CACHE
    if NWR_OVERVIEW_CACHE is not None:
        return NWR_OVERVIEW_CACHE

    ensure_nwr_tables_and_ingest(DATABASE_URL, NWR_DIR)
    ensure_network_fusion_tables_and_ingest(DATABASE_URL, NF_DIR)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MIN(min_x), MAX(max_x), MIN(min_y), MAX(max_y), COUNT(*)
                FROM nwr_track_centrelines
            """)
            b_row = cur.fetchone()
            bounds = {
                "min_x": float(b_row[0] or 147517.0),
                "max_x": float(b_row[1] or 669557.0),
                "min_y": float(b_row[2] or 30543.0),
                "max_y": float(b_row[3] or 967997.0),
                "centreline_count": int(b_row[4] or 0),
            }

            # All 1,598 ELR reference lines (with simplified/decimated vertices for macro rendering + labels)
            cur.execute("""
                SELECT fid, asset_id, elr, start_mile_yards, end_mile_yards,
                       min_x, max_x, min_y, max_y, parts
                FROM nwr_elrs
                ORDER BY elr ASC
            """)
            elrs = []
            for r in cur.fetchall():
                parts = r[9] or []
                decimated_parts = []
                for p in parts:
                    if len(p) <= 12:
                        decimated_parts.append(p)
                    else:
                        step = max(1, len(p) // 12)
                        dp = p[::step]
                        if dp[-1] != p[-1]:
                            dp.append(p[-1])
                        decimated_parts.append(dp)
                elrs.append({
                    "fid": r[0],
                    "asset_id": r[1],
                    "elr": r[2],
                    "start_my": r[3],
                    "end_my": r[4],
                    "min_x": r[5],
                    "max_x": r[6],
                    "min_y": r[7],
                    "max_y": r[8],
                    "x": round((r[5] + r[6]) / 2.0, 1),
                    "y": round((r[7] + r[8]) / 2.0, 1),
                    "parts": decimated_parts,
                })

            # Macro track centreline segments (start -> end of each link)
            cur.execute("""
                SELECT
                    fid, elr, track_id, owner,
                    coords->0->0 AS x0,
                    coords->0->1 AS y0,
                    coords->-1->0 AS x1,
                    coords->-1->1 AS y1
                FROM nwr_track_centrelines
                WHERE jsonb_array_length(coords) >= 2
            """)
            macro_links = []
            for r in cur.fetchall():
                own_code = 0  # 0 = NR 100%, 1 = Third Party, 2 = Mixed/Unclassified
                own_str = r[3] or ""
                if "NETWORK RAIL:0%" in own_str and "THIRD PARTY:0%" not in own_str:
                    own_code = 1
                elif "NETWORK RAIL:100%" not in own_str:
                    own_code = 2
                macro_links.append([
                    r[0],
                    r[1],
                    r[2],
                    own_code,
                    float(r[4]),
                    float(r[5]),
                    float(r[6]),
                    float(r[7]),
                ])

            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM nwr_track_centrelines),
                    (SELECT COUNT(*) FROM nwr_track_nodes),
                    (SELECT COUNT(*) FROM nwr_elrs),
                    (SELECT COUNT(*) FROM nwr_waymarks)
            """)
            c_row = cur.fetchone()
            counts = {
                "centrelines": c_row[0],
                "nodes": c_row[1],
                "elrs": c_row[2],
                "waymarks": c_row[3],
            }

            # ELR-level Network Fusion summary for whole-network Colour By modes
            elr_fusion_stats = {}
            try:
                cur.execute("""
                    SELECT elr,
                           ROUND(AVG(radius_avg_m))::int AS avg_r,
                           MIN(radius_min_m) AS min_r
                    FROM network_fusion_curvature
                    GROUP BY elr
                """)
                for r in cur.fetchall():
                    elr_fusion_stats[r[0]] = {"avg_r": r[1], "min_r": r[2]}

                cur.execute("""
                    SELECT elr,
                           ROUND(AVG(elevation_m))::int AS avg_elev,
                           ROUND(MIN(elevation_m))::int AS min_elev,
                           ROUND(MAX(elevation_m))::int AS max_elev
                    FROM network_fusion_points
                    GROUP BY elr
                """)
                for r in cur.fetchall():
                    st = elr_fusion_stats.setdefault(r[0], {})
                    st["avg_elev"] = r[1]
                    st["min_elev"] = r[2]
                    st["max_elev"] = r[3]
            except Exception:
                pass

    NWR_OVERVIEW_CACHE = {
        "bounds": bounds,
        "counts": counts,
        "elrs": elrs,
        "macro_links": macro_links,
        "elr_fusion_stats": elr_fusion_stats,
    }
    return NWR_OVERVIEW_CACHE


@router.get("/api/nwr/viewport")
def api_nwr_viewport(
    min_x: float = Query(...),
    max_x: float = Query(...),
    min_y: float = Query(...),
    max_y: float = Query(...),
    zoom: float = Query(1.0),
    include_centrelines: bool = Query(True),
    include_nodes: bool = Query(True),
    include_waymarks: bool = Query(True),
    include_curvature: bool = Query(True),
    include_nf_points: bool = Query(False),
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            centrelines = []
            if include_centrelines:
                cur.execute(
                    """
                    SELECT
                        fid, asset_id, elr, track_id,
                        start_mile_yards, end_mile_yards,
                        owner, coords, length_meters
                    FROM nwr_track_centrelines
                    WHERE min_x <= %s AND max_x >= %s
                      AND min_y <= %s AND max_y >= %s
                    LIMIT 25000
                    """,
                    (max_x, min_x, max_y, min_y),
                )
                centrelines = cur.fetchall()

            nodes = []
            if include_nodes:
                cur.execute(
                    """
                    SELECT fid, asset_id, valency, x, y
                    FROM nwr_track_nodes
                    WHERE x BETWEEN %s AND %s
                      AND y BETWEEN %s AND %s
                    LIMIT 25000
                    """,
                    (min_x, max_x, min_y, max_y),
                )
                nodes = cur.fetchall()

            waymarks = []
            if include_waymarks:
                cur.execute(
                    """
                    SELECT fid, asset_id, elr, unit, value, x, y
                    FROM nwr_waymarks
                    WHERE x BETWEEN %s AND %s
                      AND y BETWEEN %s AND %s
                    LIMIT 20000
                    """,
                    (min_x, max_x, min_y, max_y),
                )
                waymarks = cur.fetchall()

            curvature = []
            if include_curvature:
                cur.execute(
                    """
                    SELECT pk, elr, total_yards_from, total_yards_to, mileage_from, mileage_to,
                           radius_min_m, radius_max_m, radius_avg_m, radius_avg_class,
                           x0, y0, x1, y1
                    FROM network_fusion_curvature
                    WHERE ((x0 BETWEEN %s AND %s) OR (x1 BETWEEN %s AND %s))
                      AND ((y0 BETWEEN %s AND %s) OR (y1 BETWEEN %s AND %s))
                    LIMIT 25000
                    """,
                    (min_x, max_x, min_x, max_x, min_y, max_y, min_y, max_y),
                )
                curvature = cur.fetchall()

            nf_points = []
            if include_nf_points:
                cur.execute(
                    """
                    SELECT pk, elr, total_yards, mileage, easting, northing,
                           elevation_m, place_name, postcode, track_count_primary,
                           bedrock, annual_rainfall_mm
                    FROM network_fusion_points
                    WHERE easting BETWEEN %s AND %s
                      AND northing BETWEEN %s AND %s
                    LIMIT 20000
                    """,
                    (min_x, max_x, min_y, max_y),
                )
                nf_points = cur.fetchall()

    return {
        "centrelines": centrelines,
        "nodes": nodes,
        "waymarks": waymarks,
        "curvature": curvature,
        "nf_points": nf_points,
    }


@router.get("/api/nwr/search")
def api_nwr_search(q: str = Query(..., min_length=1)):
    q_clean = q.strip()
    q_like = f"%{q_clean}%"
    results = []

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # 1. ELRs
            cur.execute(
                """
                SELECT fid, asset_id, elr, start_mile_yards, end_mile_yards,
                       min_x, max_x, min_y, max_y
                FROM nwr_elrs
                WHERE UPPER(elr) LIKE UPPER(%s) OR asset_id LIKE %s
                ORDER BY CASE WHEN UPPER(elr) = UPPER(%s) THEN 0 ELSE 1 END, elr ASC
                LIMIT 20
                """,
                (q_like, q_like, q_clean),
            )
            for r in cur.fetchall():
                results.append({
                    "type": "nwr_elr",
                    "id": r["fid"],
                    "title": f"ELR {r['elr']} ({format_mile_yards(r['start_mile_yards'])} → {format_mile_yards(r['end_mile_yards'])})",
                    "subtitle": f"Asset ID: {r['asset_id']} • Route Reference Line",
                    "x": (r["min_x"] + r["max_x"]) / 2.0,
                    "y": (r["min_y"] + r["max_y"]) / 2.0,
                })

            # 2. Track Centre Lines
            cur.execute(
                """
                SELECT fid, asset_id, elr, track_id, start_mile_yards, end_mile_yards,
                       min_x, max_x, min_y, max_y
                FROM nwr_track_centrelines
                WHERE UPPER(elr) = UPPER(%s)
                   OR asset_id LIKE %s
                   OR track_id = %s
                ORDER BY elr ASC, start_mile_yards ASC
                LIMIT 20
                """,
                (q_clean, q_like, q_clean),
            )
            for r in cur.fetchall():
                results.append({
                    "type": "nwr_centreline",
                    "id": r["fid"],
                    "title": f"Track {r['elr']} / {r['track_id']} ({r['asset_id']})",
                    "subtitle": f"{decode_track_id(r['track_id'])} • {format_mile_yards(r['start_mile_yards'])} → {format_mile_yards(r['end_mile_yards'])}",
                    "x": (r["min_x"] + r["max_x"]) / 2.0,
                    "y": (r["min_y"] + r["max_y"]) / 2.0,
                })

            # 3. Track Nodes
            cur.execute(
                """
                SELECT fid, asset_id, valency, x, y
                FROM nwr_track_nodes
                WHERE asset_id LIKE %s
                LIMIT 10
                """,
                (q_like,),
            )
            for r in cur.fetchall():
                results.append({
                    "type": "nwr_node",
                    "id": r["fid"],
                    "title": f"NWR Track Node {r['asset_id']}",
                    "subtitle": f"Valency {r['valency']} • ({r['x']}, {r['y']})",
                    "x": r["x"],
                    "y": r["y"],
                })

            # 4. Waymarks
            cur.execute(
                """
                SELECT fid, asset_id, elr, unit, value, x, y
                FROM nwr_waymarks
                WHERE UPPER(elr) = UPPER(%s) OR asset_id LIKE %s
                ORDER BY value ASC
                LIMIT 15
                """,
                (q_clean, q_like),
            )
            for r in cur.fetchall():
                results.append({
                    "type": "nwr_waymark",
                    "id": r["fid"],
                    "title": f"Waymark {r['elr']} @ {r['value']} {r['unit']}",
                    "subtitle": f"Asset ID: {r['asset_id']}",
                    "x": r["x"],
                    "y": r["y"],
                })

    return {"results": results}


@router.get("/api/nwr/pick")
def api_nwr_pick(
    x: float = Query(...),
    y: float = Query(...),
    radius: float = Query(60.0),
):
    min_x, max_x = x - radius, x + radius
    min_y, max_y = y - radius, y + radius
    candidates = []

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # 1. Track Nodes
            cur.execute(
                """
                SELECT fid, asset_id, valency, x, y,
                       SQRT(POWER(x - %s, 2) + POWER(y - %s, 2)) AS dist
                FROM nwr_track_nodes
                WHERE x BETWEEN %s AND %s AND y BETWEEN %s AND %s
                ORDER BY dist ASC
                LIMIT 6
                """,
                (x, y, min_x, max_x, min_y, max_y),
            )
            for r in cur.fetchall():
                is_pts = r["valency"] >= 3
                lbl = (
                    f"Points {r['asset_id']} (Valency {r['valency']})"
                    if is_pts
                    else f"Node {r['asset_id']} (Valency {r['valency']})"
                )
                candidates.append({
                    "type": "nwr_node",
                    "id": r["fid"],
                    "label": lbl,
                    "dist": round(r["dist"], 2),
                    "x": r["x"],
                    "y": r["y"],
                })

            # 2. Waymarks
            cur.execute(
                """
                SELECT fid, asset_id, elr, unit, value, x, y,
                       SQRT(POWER(x - %s, 2) + POWER(y - %s, 2)) AS dist
                FROM nwr_waymarks
                WHERE x BETWEEN %s AND %s AND y BETWEEN %s AND %s
                ORDER BY dist ASC
                LIMIT 6
                """,
                (x, y, min_x, max_x, min_y, max_y),
            )
            for r in cur.fetchall():
                candidates.append({
                    "type": "nwr_waymark",
                    "id": r["fid"],
                    "label": f"Waymark {r['elr']} {r['value']}{r['unit']} ({r['asset_id']})",
                    "dist": round(r["dist"], 2),
                    "x": r["x"],
                    "y": r["y"],
                })

            # 3. Track Centre Lines
            cur.execute(
                """
                SELECT fid, asset_id, elr, track_id, coords
                FROM nwr_track_centrelines
                WHERE min_x <= %s AND max_x >= %s
                  AND min_y <= %s AND max_y >= %s
                LIMIT 25
                """,
                (max_x, min_x, max_y, min_y),
            )
            for r in cur.fetchall():
                coords = r["coords"] or []
                best_d = 999999.0
                for i in range(len(coords) - 1):
                    x0, y0 = coords[i]
                    x1, y1 = coords[i + 1]
                    dx, dy = x1 - x0, y1 - y0
                    l2 = dx * dx + dy * dy
                    if l2 == 0:
                        d = math.hypot(x - x0, y - y0)
                    else:
                        t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / l2))
                        d = math.hypot(x - (x0 + t * dx), y - (y0 + t * dy))
                    if d < best_d:
                        best_d = d
                if best_d <= radius:
                    cmid = coords[len(coords) // 2] if coords else [x, y]
                    candidates.append({
                        "type": "nwr_centreline",
                        "id": r["fid"],
                        "label": f"Track {r['elr']} {r['track_id']} — {decode_track_id(r['track_id'])} ({r['asset_id']})",
                        "dist": round(best_d, 2),
                        "x": cmid[0],
                        "y": cmid[1],
                        "coords": coords,
                    })

            # 4. ELR Reference Lines
            cur.execute(
                """
                SELECT fid, asset_id, elr, parts
                FROM nwr_elrs
                WHERE min_x <= %s AND max_x >= %s
                  AND min_y <= %s AND max_y >= %s
                LIMIT 10
                """,
                (max_x, min_x, max_y, min_y),
            )
            for r in cur.fetchall():
                best_d = 999999.0
                for part in r["parts"] or []:
                    for i in range(len(part) - 1):
                        x0, y0 = part[i]
                        x1, y1 = part[i + 1]
                        dx, dy = x1 - x0, y1 - y0
                        l2 = dx * dx + dy * dy
                        if l2 == 0:
                            d = math.hypot(x - x0, y - y0)
                        else:
                            t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / l2))
                            d = math.hypot(x - (x0 + t * dx), y - (y0 + t * dy))
                        if d < best_d:
                            best_d = d
                if best_d <= radius * 1.5:
                    parts = r["parts"] or []
                    first_part = parts[0] if parts else []
                    emid = first_part[len(first_part) // 2] if first_part else [x, y]
                    candidates.append({
                        "type": "nwr_elr",
                        "id": r["fid"],
                        "label": f"ELR Corridor {r['elr']} ({r['asset_id']})",
                        "dist": round(best_d + 2.0, 2),
                        "x": emid[0],
                        "y": emid[1],
                        "coords": first_part,
                        "parts": parts,
                    })

            # 5. Network Fusion Curvature Segments
            cur.execute(
                """
                SELECT pk, elr, total_yards_from, total_yards_to, mileage_from, mileage_to,
                       radius_min_m, radius_max_m, radius_avg_m, radius_avg_class,
                       x0, y0, x1, y1
                FROM network_fusion_curvature
                WHERE ((x0 BETWEEN %s AND %s) OR (x1 BETWEEN %s AND %s))
                  AND ((y0 BETWEEN %s AND %s) OR (y1 BETWEEN %s AND %s))
                LIMIT 25
                """,
                (min_x, max_x, min_x, max_x, min_y, max_y, min_y, max_y),
            )
            for r in cur.fetchall():
                dx, dy = r["x1"] - r["x0"], r["y1"] - r["y0"]
                l2 = dx * dx + dy * dy
                if l2 == 0:
                    cdist = math.hypot(x - r["x0"], y - r["y0"])
                else:
                    t = max(0.0, min(1.0, ((x - r["x0"]) * dx + (y - r["y0"]) * dy) / l2))
                    cdist = math.hypot(x - (r["x0"] + t * dx), y - (r["y0"] + t * dy))
                if cdist <= radius:
                    candidates.append({
                        "type": "nf_curvature",
                        "id": r["pk"],
                        "label": f"Curve {r['elr']} R={r['radius_avg_m']}m ({r['radius_avg_class']})",
                        "dist": round(cdist, 2),
                        "x": (r["x0"] + r["x1"]) / 2.0,
                        "y": (r["y0"] + r["y1"]) / 2.0,
                    })

            # 6. Network Fusion Sample Points (Tenth-Mile)
            cur.execute(
                """
                SELECT pk, elr, total_yards, mileage, easting, northing,
                       elevation_m, place_name, postcode,
                       SQRT(POWER(easting - %s, 2) + POWER(northing - %s, 2)) AS dist
                FROM network_fusion_points
                WHERE easting BETWEEN %s AND %s AND northing BETWEEN %s AND %s
                ORDER BY dist ASC
                LIMIT 6
                """,
                (x, y, min_x, max_x, min_y, max_y),
            )
            for r in cur.fetchall():
                loc = r["place_name"] or r["postcode"] or ""
                loc_str = f" • {loc}" if loc else ""
                candidates.append({
                    "type": "nf_point",
                    "id": r["pk"],
                    "label": f"NF Point {r['elr']} @ {r['mileage']} ({r['elevation_m']}m{loc_str})",
                    "dist": round(r["dist"], 2),
                    "x": r["easting"],
                    "y": r["northing"],
                })

    candidates.sort(key=lambda c: (0 if c["type"] in ("block", "route") else 1, c["dist"]))
    return {"candidates": candidates[:15]}


@router.get("/api/nwr/asset/{asset_type}/{asset_id}")
def api_nwr_asset(asset_type: str, asset_id: int):
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if asset_type == "nwr_centreline":
                cur.execute("SELECT * FROM nwr_track_centrelines WHERE fid = %s", (asset_id,))
                tcl = cur.fetchone()
                if not tcl:
                    raise HTTPException(status_code=404, detail="Track centre line not found")

                # Start and End NWR_TrackNodes
                cur.execute(
                    "SELECT * FROM nwr_track_nodes WHERE asset_id IN (%s, %s)",
                    (tcl["start_node_id"], tcl["end_node_id"]),
                )
                nmap = {r["asset_id"]: r for r in cur.fetchall()}
                s_node = nmap.get(tcl["start_node_id"])
                e_node = nmap.get(tcl["end_node_id"])

                # Parent NWR_ELRs record
                cur.execute("SELECT fid, asset_id, elr, start_mile_yards, end_mile_yards FROM nwr_elrs WHERE elr = %s", (tcl["elr"],))
                elr_rec = cur.fetchone()

                # Nearby Waymarks on same ELR
                lo_m = min(tcl["start_meters"], tcl["end_meters"]) - 500
                hi_m = max(tcl["start_meters"], tcl["end_meters"]) + 500
                cur.execute(
                    """
                    SELECT fid, asset_id, elr, unit, value, meters, x, y
                    FROM nwr_waymarks
                    WHERE elr = %s AND meters BETWEEN %s AND %s
                    ORDER BY value ASC
                    LIMIT 15
                    """,
                    (tcl["elr"], lo_m, hi_m),
                )
                waymarks = cur.fetchall()

                # Cross-model TPS links
                tps_links = find_tps_cross_links(cur, tcl["elr"], tcl["start_meters"], tcl["end_meters"])

                # Network Fusion Cross-Links (GeoFurlong tenth-mile points & curvature)
                s_yd = mile_yards_to_total_yards(tcl["start_mile_yards"])
                e_yd = mile_yards_to_total_yards(tcl["end_mile_yards"])
                nf_links = find_network_fusion_for_elr(cur, tcl["elr"], s_yd, e_yd)

                cx = (tcl["min_x"] + tcl["max_x"]) / 2.0
                cy = (tcl["min_y"] + tcl["max_y"]) / 2.0
                decoded = {
                    "Shapefile FID / ASSET_ID": f"{tcl['fid']} / {tcl['asset_id']}",
                    "Engineer's Line Reference (ELR)": tcl["elr"],
                    "Track ID & Classification": decode_track_id(tcl["track_id"]),
                    "Start Calibrated Mileage": f"{tcl['start_mile_yards']:.4f} ({format_mile_yards(tcl['start_mile_yards'])} / {tcl['start_meters']} m)",
                    "End Calibrated Mileage": f"{tcl['end_mile_yards']:.4f} ({format_mile_yards(tcl['end_mile_yards'])} / {tcl['end_meters']} m)",
                    "2D OSGB36 Polyline Length": f"{tcl['length_meters']} m",
                    "Start Track Node": f"{tcl['start_node_id']} (Valency {s_node['valency'] if s_node else '?'})",
                    "End Track Node": f"{tcl['end_node_id']} (Valency {e_node['valency'] if e_node else '?'})",
                    "Track Ownership": tcl["owner"],
                    "Version / Extracted Date": f"v{tcl['version']} • Extracted {tcl['extracted']}",
                    "OSGB36 Bounding Box (E, N)": f"[{tcl['min_x']}, {tcl['min_y']}] → [{tcl['max_x']}, {tcl['max_y']}]",
                    "TPS Cross-Model Match": f"{len(tps_links['tps_stations'])} TPS stations on {tcl['elr']} • {tps_links['tps_nodes_count']} TPS nodes in mileage window",
                }

                if nf_links.get("summary"):
                    nfs = nf_links["summary"]
                    if nfs.get("min_elevation_m") is not None and nfs.get("max_elevation_m") is not None:
                        decoded["Network Fusion Elevation"] = f"{nfs['min_elevation_m']} m → {nfs['max_elevation_m']} m above sea level"
                    if nfs.get("min_radius_m") is not None:
                        tc = nfs.get("tightest_curve") or {}
                        decoded["Network Fusion Curvature"] = f"Min R: {nfs['min_radius_m']} m ({tc.get('class', 'Curve')}) • Section Avg R: {nfs['avg_radius_m']} m"
                    if nfs.get("bedrocks"):
                        decoded["Network Fusion Geology"] = " / ".join(nfs["bedrocks"])
                    if nfs.get("nearest_places"):
                        pcs = f" ({', '.join(nfs['postcodes'])})" if nfs.get("postcodes") else ""
                        decoded["Network Fusion Location / Gazetteer"] = f"{', '.join(nfs['nearest_places'])}{pcs}"

                pts = []
                if s_node:
                    pts.append({"x": s_node["x"], "y": s_node["y"], "label": f"Start {s_node['asset_id']}", "color": "#22c55e"})
                if e_node:
                    pts.append({"x": e_node["x"], "y": e_node["y"], "label": f"End {e_node['asset_id']}", "color": "#ef4444"})

                return {
                    "asset_type": "nwr_centreline",
                    "id": tcl["fid"],
                    "title": f"Track Centre Line {tcl['elr']} — {tcl['track_id']} ({tcl['asset_id']})",
                    "subtitle": f"{decode_track_id(tcl['track_id'])} • {format_mile_yards(tcl['start_mile_yards'])} → {format_mile_yards(tcl['end_mile_yards'])}",
                    "x": cx,
                    "y": cy,
                    "decoded": decoded,
                    "related": {
                        "start_node": s_node,
                        "end_node": e_node,
                        "elr_record": elr_rec,
                        "waymarks": waymarks,
                        "tps_cross_links": tps_links,
                        "network_fusion": nf_links,
                    },
                    "highlight": {
                        "ways": [{"label": f"{tcl['elr']} {tcl['track_id']}", "coords": tcl["coords"], "color": "#22d3ee"}],
                        "points": pts,
                    },
                    "raw_attrs": tcl["raw_attrs"],
                    "raw_children": [{"geometry_vertices": len(tcl["coords"] or []), "coords_preview": (tcl["coords"] or [])[:20]}],
                }

            elif asset_type == "nwr_node":
                cur.execute("SELECT * FROM nwr_track_nodes WHERE fid = %s", (asset_id,))
                nd = cur.fetchone()
                if not nd:
                    raise HTTPException(status_code=404, detail="NWR Track Node not found")

                cur.execute(
                    """
                    SELECT fid, asset_id, elr, track_id, start_mile_yards, end_mile_yards, length_meters, coords
                    FROM nwr_track_centrelines
                    WHERE start_node_id = %s OR end_node_id = %s
                    """,
                    (nd["asset_id"], nd["asset_id"]),
                )
                conn_links = cur.fetchall()
                elr = conn_links[0]["elr"] if conn_links else None
                tps_links = find_tps_cross_links(cur, elr) if elr else {}

                decoded = {
                    "Shapefile FID / ASSET_ID": f"{nd['fid']} / {nd['asset_id']}",
                    "Valency": f"{nd['valency']} — {VALENCY_DESC.get(nd['valency'], 'Unknown')}",
                    "Connected Track Links": len(conn_links),
                    "OSGB36 Easting / Northing": f"({nd['x']} E, {nd['y']} N)",
                    "Version / Extracted Date": f"v{nd['version']} • Extracted {nd['extracted']}",
                }

                return {
                    "asset_type": "nwr_node",
                    "id": nd["fid"],
                    "title": f"NWR Track Node {nd['asset_id']}",
                    "subtitle": f"Valency {nd['valency']}: {VALENCY_DESC.get(nd['valency'], '')}",
                    "x": nd["x"],
                    "y": nd["y"],
                    "decoded": decoded,
                    "related": {
                        "connected_centrelines": conn_links,
                        "tps_cross_links": tps_links,
                    },
                    "highlight": {
                        "ways": [
                            {"label": l["asset_id"], "coords": l["coords"], "color": "#38bdf8"}
                            for l in conn_links
                        ],
                        "points": [{"x": nd["x"], "y": nd["y"], "label": nd["asset_id"], "color": "#f43f5e"}],
                    },
                    "raw_attrs": nd["raw_attrs"],
                    "raw_children": [],
                }

            elif asset_type == "nwr_elr":
                cur.execute("SELECT * FROM nwr_elrs WHERE fid = %s", (asset_id,))
                elr_rec = cur.fetchone()
                if not elr_rec:
                    raise HTTPException(status_code=404, detail="NWR ELR not found")

                cur.execute(
                    """
                    SELECT fid, asset_id, elr, track_id, start_mile_yards, end_mile_yards, length_meters
                    FROM nwr_track_centrelines
                    WHERE elr = %s
                    ORDER BY start_mile_yards ASC
                    LIMIT 60
                    """,
                    (elr_rec["elr"],),
                )
                links = cur.fetchall()

                tps_links = find_tps_cross_links(cur, elr_rec["elr"])
                nf_links = find_network_fusion_for_elr(cur, elr_rec["elr"])

                cx = (elr_rec["min_x"] + elr_rec["max_x"]) / 2.0
                cy = (elr_rec["min_y"] + elr_rec["max_y"]) / 2.0

                decoded = {
                    "Shapefile FID / ASSET_ID": f"{elr_rec['fid']} / {elr_rec['asset_id']}",
                    "Engineer's Line Reference (ELR)": elr_rec["elr"],
                    "Start Calibrated Mileage": f"{elr_rec['start_mile_yards']:.4f} ({format_mile_yards(elr_rec['start_mile_yards'])})",
                    "End Calibrated Mileage": f"{elr_rec['end_mile_yards']:.4f} ({format_mile_yards(elr_rec['end_mile_yards'])})",
                    "Polyline Parts": len(elr_rec["parts"] or []),
                    "Version / Extracted Date": f"v{elr_rec['version']} • Extracted {elr_rec['extracted']}",
                    "TPS Stations on ELR": len(tps_links.get("tps_stations", [])),
                    "TPS Track Nodes on ELR": tps_links.get("tps_nodes_count", 0),
                }

                if nf_links.get("summary"):
                    nfs = nf_links["summary"]
                    if nfs.get("min_elevation_m") is not None and nfs.get("max_elevation_m") is not None:
                        decoded["Network Fusion Corridor Terrain"] = f"Elevation: {nfs['min_elevation_m']}m – {nfs['max_elevation_m']}m • {nfs['point_count']} sample points"
                    if nfs.get("min_radius_m") is not None:
                        tc = nfs.get("tightest_curve") or {}
                        decoded["Network Fusion Tightest Curve"] = f"R={nfs['min_radius_m']}m ({tc.get('class', '')} @ {tc.get('mileage', '')})"

                return {
                    "asset_type": "nwr_elr",
                    "id": elr_rec["fid"],
                    "title": f"ELR Corridor {elr_rec['elr']} ({elr_rec['asset_id']})",
                    "subtitle": f"{format_mile_yards(elr_rec['start_mile_yards'])} → {format_mile_yards(elr_rec['end_mile_yards'])} • {len(tps_links.get('tps_stations', []))} TPS Stations",
                    "x": cx,
                    "y": cy,
                    "decoded": decoded,
                    "related": {
                        "connected_centrelines": links,
                        "tps_cross_links": tps_links,
                        "network_fusion": nf_links,
                    },
                    "highlight": {
                        "ways": [
                            {"label": f"ELR {elr_rec['elr']}", "coords": part, "color": "#a855f7"}
                            for part in (elr_rec["parts"] or [])
                        ],
                        "points": [],
                    },
                    "raw_attrs": elr_rec["raw_attrs"],
                    "raw_children": [],
                }

            elif asset_type == "nwr_waymark":
                cur.execute("SELECT * FROM nwr_waymarks WHERE fid = %s", (asset_id,))
                wm = cur.fetchone()
                if not wm:
                    raise HTTPException(status_code=404, detail="NWR Waymark not found")

                tps_links = find_tps_cross_links(cur, wm["elr"], wm["meters"] - 500, wm["meters"] + 500)
                unit_desc = "Miles (mile.yards)" if wm["unit"] == "M" else "Kilometres"
                decoded = {
                    "Shapefile FID / ASSET_ID": f"{wm['fid']} / {wm['asset_id']}",
                    "Engineer's Line Reference (ELR)": wm["elr"],
                    "Master Unit of Measurement": f"{wm['unit']} — {unit_desc}",
                    "Waymark Value": f"{wm['value']:.4f} ({format_mile_yards(wm['value']) if wm['unit'] == 'M' else str(wm['value']) + ' km'})",
                    "Metric Distance from ELR Zero": f"{wm['meters']} m ({wm['meters']/1000.0:.3f} km)",
                    "OSGB36 Coordinates (E, N)": f"({wm['x']} E, {wm['y']} N)",
                    "Version / Extracted Date": f"v{wm['version']} • Extracted {wm['extracted']}",
                }

                return {
                    "asset_type": "nwr_waymark",
                    "id": wm["fid"],
                    "title": f"Waymark {wm['elr']} @ {wm['value']} {wm['unit']}",
                    "subtitle": f"Asset ID {wm['asset_id']} • {wm['meters']/1000.0:.3f} km",
                    "x": wm["x"],
                    "y": wm["y"],
                    "decoded": decoded,
                    "related": {
                        "tps_cross_links": tps_links,
                    },
                    "highlight": {
                        "ways": [],
                        "points": [{"x": wm["x"], "y": wm["y"], "label": f"{wm['elr']} {wm['value']}{wm['unit']}", "color": "#f59e0b"}],
                    },
                    "raw_attrs": wm["raw_attrs"],
                    "raw_children": [],
                }

            elif asset_type == "nf_point":
                cur.execute("SELECT * FROM network_fusion_points WHERE pk = %s", (asset_id,))
                p = cur.fetchone()
                if not p:
                    raise HTTPException(status_code=404, detail="Network Fusion point not found")

                tps_links = find_tps_cross_links(cur, p["elr"])
                decoded = {
                    "ELR & Mileage Position": f"{p['elr']} @ {p['mileage']} ({p['total_yards']} total yards)",
                    "OSGB36 National Grid (E, N)": f"({p['easting']:.1f} E, {p['northing']:.1f} N)",
                    "WGS84 Coordinates": f"Lat {p['latitude']:.6f}, Lon {p['longitude']:.6f}",
                    "OS Grid Reference": p["os_grid_ref"] or "—",
                    "Open Location Code (Plus Code)": p["olc"] or "—",
                    "Elevation & Vertical Accuracy": f"{p['elevation_m']} m (linear accuracy {p['linear_accuracy_m']} m)",
                    "Track Configuration": f"Primary: {p['track_count_primary']}, Total: {p['track_count_all']}",
                    "Primary Track IDs": p["track_ids_primary"] or "—",
                    "Siding / Crossover Tracks": f"Siding: {p['track_ids_siding'] or '—'} • Crossover: {p['track_ids_crossover'] or '—'}",
                    "Local Climate": f"Temp: {p['min_air_temperature_c']}°C to {p['max_air_temperature_c']}°C • Wind: {p['mean_wind_speed_ms']} m/s • Rainfall: {p['annual_rainfall_mm']} mm/yr",
                    "Geology & Ground": f"Bedrock: {p['bedrock'] or '—'} • Deposit: {p['deposit'] or '—'} • Material: {p['material'] or '—'}",
                    "Gazetteer & Place": f"{p['place_name']} ({p['place_distance_m']}m away)",
                    "Postcode & Admin": f"{p['postcode']} • {p['admin_area']}, {p['county_district']}, {p['country']} ({p['nr_region']})",
                }

                return {
                    "asset_type": "nf_point",
                    "id": p["pk"],
                    "title": f"Network Fusion Tenth-Mile Point {p['elr']} @ {p['mileage']}",
                    "subtitle": f"Elevation {p['elevation_m']}m • {p['place_name'] or p['postcode'] or p['elr']}",
                    "x": p["easting"],
                    "y": p["northing"],
                    "decoded": decoded,
                    "related": {
                        "tps_cross_links": tps_links,
                    },
                    "highlight": {
                        "ways": [],
                        "points": [{"x": p["easting"], "y": p["northing"], "label": f"{p['elr']} {p['mileage']}", "color": "#06b6d4"}],
                    },
                    "raw_attrs": p,
                    "raw_children": [],
                }

            elif asset_type == "nf_curvature":
                cur.execute("SELECT * FROM network_fusion_curvature WHERE pk = %s", (asset_id,))
                c = cur.fetchone()
                if not c:
                    raise HTTPException(status_code=404, detail="Network Fusion curvature not found")

                tps_links = find_tps_cross_links(cur, c["elr"])
                cx = (c["x0"] + c["x1"]) / 2.0
                cy = (c["y0"] + c["y1"]) / 2.0
                decoded = {
                    "ELR & Mileage Span": f"{c['elr']} {c['mileage_from']} → {c['mileage_to']} ({c['total_yards_from']} → {c['total_yards_to']} yd)",
                    "Average Curve Radius": f"{c['radius_avg_m']} m ({c['radius_avg_class']})",
                    "Minimum Curve Radius": f"{c['radius_min_m']} m ({c['radius_min_class']})",
                    "Maximum Curve Radius": f"{c['radius_max_m']} m ({c['radius_max_class']})",
                    "OSGB36 Start (E, N)": f"({c['x0']:.1f}, {c['y0']:.1f})",
                    "OSGB36 End (E, N)": f"({c['x1']:.1f}, {c['y1']:.1f})",
                }

                col = "#ef4444" if (c["radius_avg_m"] or 50000) <= 400 else "#f59e0b" if (c["radius_avg_m"] or 50000) <= 1200 else "#38bdf8"
                return {
                    "asset_type": "nf_curvature",
                    "id": c["pk"],
                    "title": f"Curvature Segment {c['elr']} ({c['radius_avg_class']})",
                    "subtitle": f"R_avg = {c['radius_avg_m']}m • {c['mileage_from']} → {c['mileage_to']}",
                    "x": cx,
                    "y": cy,
                    "decoded": decoded,
                    "related": {
                        "tps_cross_links": tps_links,
                    },
                    "highlight": {
                        "ways": [{"label": f"Curve R={c['radius_avg_m']}m", "coords": c["coords"] or [[c["x0"], c["y0"]], [c["x1"], c["y1"]]], "color": col}],
                        "points": [
                            {"x": c["x0"], "y": c["y0"], "label": c["mileage_from"], "color": col},
                            {"x": c["x1"], "y": c["y1"], "label": c["mileage_to"], "color": col},
                        ],
                    },
                    "raw_attrs": c,
                    "raw_children": [],
                }

    raise HTTPException(status_code=400, detail=f"Unknown NWR asset type: {asset_type}")

