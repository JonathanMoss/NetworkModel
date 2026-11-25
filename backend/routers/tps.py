"""TPS schematic map, search, picking, timing-loads, and catalog API router.

Handles schematic overview/viewport rendering, multi-table asset search,
coordinate picking, timing-load catalog browsing, and generic table inspection.
"""

import math
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row

from config import DATABASE_URL, decode_code, get_conn, load_lookup_cache
from ingest import INGEST_STATE, check_already_ingested
from timing_catalog import get_timing_catalog

router = APIRouter(tags=["tps"])

OVERVIEW_CACHE: Optional[dict[str, Any]] = None


def reset_overview_cache() -> None:
    """Clear the cached TPS overview payload (used by unit/E2E tests)."""
    global OVERVIEW_CACHE
    OVERVIEW_CACHE = None


@router.get("/api/status")
def api_status():
    if INGEST_STATE["status"] != "ready":
        check_already_ingested(DATABASE_URL)

    project_info = None
    infra_info = None
    version_info = None
    if INGEST_STATE["status"] == "ready":
        try:
            with get_conn() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        "SELECT raw_attrs, raw_children FROM raw_tps_elements WHERE tag = 'projectinfo' LIMIT 1"
                    )
                    row = cur.fetchone()
                    if row:
                        project_info = row["raw_attrs"]
                        if row["raw_children"]:
                            infra_info = row["raw_children"][0].get("attrs")
                    cur.execute(
                        "SELECT raw_attrs FROM raw_tps_elements WHERE tag = 'version' LIMIT 1"
                    )
                    vrow = cur.fetchone()
                    if vrow:
                        version_info = vrow["raw_attrs"]
        except Exception:
            pass

    return {
        "ingest": INGEST_STATE,
        "project": project_info,
        "infra": infra_info,
        "version": version_info,
    }


@router.get("/api/overview")
def api_overview():
    global OVERVIEW_CACHE
    if OVERVIEW_CACHE is not None:
        return OVERVIEW_CACHE

    if INGEST_STATE["status"] != "ready" and not check_already_ingested(DATABASE_URL):
        raise HTTPException(status_code=503, detail="Database ingestion in progress")

    lookup = load_lookup_cache()

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Coordinate bounds
            cur.execute("""
                SELECT
                    MIN(netx), MAX(netx), MIN(nety), MAX(nety), COUNT(*)
                FROM nodes
                WHERE netx != 0 OR nety != 0
            """)
            b_row = cur.fetchone()
            bounds = {
                "min_x": float(b_row[0] or 92844.0),
                "max_x": float(b_row[1] or 128576.0),
                "min_y": float(b_row[2] or -36544.0),
                "max_y": float(b_row[3] or 19563.0),
                "node_count": int(b_row[4] or 0),
            }

            # All stations with coordinates
            cur.execute("""
                SELECT
                    station_id, abbrev, longname, crscode, stanox,
                    station_type, station_category, x, y,
                    min_x, max_x, min_y, max_y,
                    track_count, active_track_count, km_region_id, km_value
                FROM stations
                WHERE x IS NOT NULL AND y IS NOT NULL AND station_id > 0
                ORDER BY station_category ASC, active_track_count DESC
            """)
            stations = []
            for r in cur.fetchall():
                elr = decode_code("kmregionmasterdesc", r[15])
                stations.append({
                    "id": r[0],
                    "abbrev": r[1],
                    "name": r[2] or r[1],
                    "crs": r[3],
                    "stanox": r[4],
                    "type": r[5],
                    "cat": r[6],
                    "x": round(r[7], 1),
                    "y": round(r[8], 1),
                    "min_x": round(r[9], 1) if r[9] is not None else round(r[7], 1),
                    "max_x": round(r[10], 1) if r[10] is not None else round(r[7], 1),
                    "min_y": round(r[11], 1) if r[11] is not None else round(r[8], 1),
                    "max_y": round(r[12], 1) if r[12] is not None else round(r[8], 1),
                    "tracks": r[13],
                    "active_tracks": r[14],
                    "elr": elr,
                    "km": r[16],
                })

            # Macro backbone segments: quantize endpoints to 12-unit schematic cells
            # so the full UK rail network renders at 60fps when zoomed out
            cur.execute("""
                SELECT
                    ROUND(x0 / 10.0) * 10.0 AS qx0,
                    ROUND(y0 / 10.0) * 10.0 AS qy0,
                    ROUND(x1 / 10.0) * 10.0 AS qx1,
                    ROUND(y1 / 10.0) * 10.0 AS qy1,
                    MAX(vmax) AS max_v,
                    BOOL_OR(electrified) AS is_elec,
                    AVG(gradient) AS avg_grad
                FROM edges
                WHERE x0 IS NOT NULL AND x1 IS NOT NULL AND edge_id > 0
                  AND (ROUND(x0 / 10.0) != ROUND(x1 / 10.0) OR ROUND(y0 / 10.0) != ROUND(y1 / 10.0))
                GROUP BY 1, 2, 3, 4
            """)
            macro_edges = [
                [r[0], r[1], r[2], r[3], r[4] or 0, 1 if r[5] else 0, round(float(r[6] or 0.0), 1)]
                for r in cur.fetchall()
            ]

            # Poly areas
            cur.execute("""
                SELECT pk, id, name, description, corners, min_x, max_x, min_y, max_y
                FROM poly_areas
                WHERE min_x IS NOT NULL
            """)
            poly_areas = [
                {
                    "pk": r[0],
                    "id": r[1],
                    "name": r[2],
                    "description": r[3],
                    "corners": r[4],
                    "min_x": r[5],
                    "max_x": r[6],
                    "min_y": r[7],
                    "max_y": r[8],
                }
                for r in cur.fetchall()
            ]

            # Annotations
            cur.execute("""
                SELECT pk, x, y, text, valid_from, valid_to, last_modified
                FROM annotations
            """)
            annotations = [
                {
                    "pk": r[0],
                    "x": r[1],
                    "y": r[2],
                    "text": r[3],
                    "valid_from": r[4],
                    "valid_to": r[5],
                    "last_modified": r[6],
                }
                for r in cur.fetchall()
            ]

            # Summary statistics
            cur.execute("""
                SELECT
                    (SELECT COUNT(*) FROM nodes) AS nodes,
                    (SELECT COUNT(*) FROM edges WHERE edge_id > 0) AS edges,
                    (SELECT COUNT(*) FROM signals) AS signals,
                    (SELECT COUNT(*) FROM mastersignals) AS mastersignals,
                    (SELECT COUNT(*) FROM stations WHERE station_id > 0) AS stations,
                    (SELECT COUNT(*) FROM station_tracks) AS tracks,
                    (SELECT COUNT(*) FROM blocks) AS blocks,
                    (SELECT COUNT(*) FROM routes) AS routes,
                    (SELECT COUNT(*) FROM attribute_sections) AS attr_sections,
                    (SELECT COUNT(*) FROM graphic_vectors) AS graphic_vectors,
                    (SELECT COUNT(*) FROM graphic_texts) AS graphic_texts,
                    (SELECT COUNT(*) FROM lookup_codes) AS lookup_codes,
                    (SELECT COUNT(*) FROM raw_tps_elements) AS raw_elements
            """)
            s_row = cur.fetchone()
            counts = {
                "nodes": s_row[0],
                "edges": s_row[1],
                "signals": s_row[2],
                "mastersignals": s_row[3],
                "stations": s_row[4],
                "station_tracks": s_row[5],
                "blocks": s_row[6],
                "routes": s_row[7],
                "attribute_sections": s_row[8],
                "graphic_vectors": s_row[9],
                "graphic_texts": s_row[10],
                "lookup_codes": s_row[11],
                "raw_elements": s_row[12],
            }

    OVERVIEW_CACHE = {
        "bounds": bounds,
        "counts": counts,
        "stations": stations,
        "macro_edges": macro_edges,
        "poly_areas": poly_areas,
        "annotations": annotations,
        "station_categories": {
            k: v["display"] for k, v in lookup.get("stationcategorydesc", {}).items()
        },
        "station_types": {
            k: v["display"] for k, v in lookup.get("stationtypedesc", {}).items()
        },
        "track_categories": {
            k: v["display"] for k, v in lookup.get("trackcategorydesc", {}).items()
        },
        "velocity_profiles": {
            k: v["display"] for k, v in lookup.get("veloprofiledesc", {}).items()
        },
    }
    return OVERVIEW_CACHE


@router.get("/api/viewport")
def api_viewport(
    min_x: float = Query(...),
    max_x: float = Query(...),
    min_y: float = Query(...),
    max_y: float = Query(...),
    zoom: float = Query(1.0),
    include_vectors: bool = Query(True),
    include_texts: bool = Query(True),
    include_signals: bool = Query(True),
    include_nodes: bool = Query(False),
    include_tracks: bool = Query(True),
    include_blocks: bool = Query(False),
    include_vp: bool = Query(False),
    include_overlaps: bool = Query(False),
):
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 1. Microscopic edges in bbox
            # Format: [pk, edge_id, start_node_id, end_node_id, x0, y0, x1, y1, vmax, electrified(1/0), gradient, length, vp_count]
            cur.execute(
                """
                SELECT
                    pk, edge_id, start_node_id, end_node_id,
                    x0, y0, x1, y1, vmax,
                    CASE WHEN electrified THEN 1 ELSE 0 END,
                    gradient, length,
                    CASE WHEN %s THEN jsonb_array_length(velocity_profiles) ELSE 0 END
                FROM edges
                WHERE min_x <= %s AND max_x >= %s
                  AND min_y <= %s AND max_y >= %s
                  AND edge_id > 0
                LIMIT 60000
                """,
                (include_vp, max_x, min_x, max_y, min_y),
            )
            edges = cur.fetchall()

            # 2. Station platform tracks in bbox
            tracks = []
            if include_tracks:
                cur.execute(
                    """
                    SELECT
                        pk, track_id, station_id, name, description,
                        track_category, effective_length, coords
                    FROM station_tracks
                    WHERE min_x <= %s AND max_x >= %s
                      AND min_y <= %s AND max_y >= %s
                    LIMIT 15000
                    """,
                    (max_x, min_x, max_y, min_y),
                )
                tracks = cur.fetchall()

            # 3. Graphic vectors (CAD schematic lines)
            vectors = []
            if include_vectors:
                cur.execute(
                    """
                    SELECT pk, x0, y0, x1, y1, layers
                    FROM graphic_vectors
                    WHERE min_x <= %s AND max_x >= %s
                      AND min_y <= %s AND max_y >= %s
                    LIMIT 60000
                    """,
                    (max_x, min_x, max_y, min_y),
                )
                vectors = cur.fetchall()

            # 4. Graphic texts (CAD schematic labels)
            texts = []
            if include_texts:
                cur.execute(
                    """
                    SELECT pk, x, y, size, text, angle
                    FROM graphic_texts
                    WHERE x BETWEEN %s AND %s
                      AND y BETWEEN %s AND %s
                    LIMIT 15000
                    """,
                    (min_x, max_x, min_y, max_y),
                )
                texts = cur.fetchall()

            # 5. Signals
            # Format: [pk, id, signal_id, name, interlocking_sys_id, x, y, x1, y1, bumper(1/0), start_node_id, end_node_id, overlap_count]
            signals = []
            if include_signals:
                cur.execute(
                    """
                    SELECT
                        pk, id, signal_id, name, interlocking_sys_id,
                        x, y, x1, y1,
                        CASE WHEN bumper THEN 1 ELSE 0 END,
                        start_node_id, end_node_id,
                        CASE WHEN %s THEN jsonb_array_length(security_sections) ELSE 0 END
                    FROM signals
                    WHERE x BETWEEN %s AND %s
                      AND y BETWEEN %s AND %s
                    LIMIT 25000
                    """,
                    (include_overlaps, min_x, max_x, min_y, max_y),
                )
                signals = cur.fetchall()

            # 6. Nodes
            nodes = []
            if include_nodes:
                cur.execute(
                    """
                    SELECT node_id, netx, nety, angle, km_region_id, km_value
                    FROM nodes
                    WHERE netx BETWEEN %s AND %s
                      AND nety BETWEEN %s AND %s
                    LIMIT 35000
                    """,
                    (min_x, max_x, min_y, max_y),
                )
                nodes = cur.fetchall()

            # 7. Signalling Blocks (optional overlay)
            # Format: [pk, block_id, pre_signal_distance, sight_distance, shunting_flag(1/0), atc_flag(1/0), moving_block(1/0), coords]
            blocks = []
            if include_blocks:
                cur.execute(
                    """
                    SELECT
                        pk, block_id, pre_signal_distance, sight_distance,
                        CASE WHEN shunting_flag THEN 1 ELSE 0 END,
                        CASE WHEN atc_flag THEN 1 ELSE 0 END,
                        CASE WHEN moving_block THEN 1 ELSE 0 END,
                        coords
                    FROM blocks
                    WHERE min_x <= %s AND max_x >= %s
                      AND min_y <= %s AND max_y >= %s
                    LIMIT 8000
                    """,
                    (max_x, min_x, max_y, min_y),
                )
                blocks = cur.fetchall()

    return {
        "edges": edges,
        "tracks": tracks,
        "vectors": vectors,
        "texts": texts,
        "signals": signals,
        "nodes": nodes,
        "blocks": blocks,
    }


@router.get("/api/search")
def api_search(q: str = Query(..., min_length=1)):
    q_clean = q.strip()
    q_like = f"%{q_clean}%"
    q_prefix = f"{q_clean}%"
    q_int = int(q_clean) if q_clean.isdigit() else None

    results = []
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # 1. Stations
            cur.execute(
                """
                SELECT
                    station_id, abbrev, longname, crscode, stanox, nalco,
                    x, y, active_track_count, station_category
                FROM stations
                WHERE station_id > 0 AND (
                    UPPER(crscode) = UPPER(%s)
                    OR UPPER(abbrev) LIKE UPPER(%s)
                    OR UPPER(longname) LIKE UPPER(%s)
                    OR stanox = %s
                    OR nalco = %s
                    OR (%s::int IS NOT NULL AND station_id = %s::int)
                )
                ORDER BY
                    CASE
                        WHEN UPPER(crscode) = UPPER(%s) THEN 0
                        WHEN UPPER(abbrev) = UPPER(%s) THEN 1
                        WHEN UPPER(longname) LIKE UPPER(%s) THEN 2
                        ELSE 3
                    END,
                    active_track_count DESC
                LIMIT 25
                """,
                (
                    q_clean, q_like, q_like, q_clean, q_clean, q_int, q_int,
                    q_clean, q_clean, q_prefix,
                ),
            )
            for r in cur.fetchall():
                sub = []
                if r["crscode"]:
                    sub.append(f"CRS: {r['crscode']}")
                if r["abbrev"]:
                    sub.append(f"TIPLOC: {r['abbrev']}")
                if r["stanox"]:
                    sub.append(f"STANOX: {r['stanox']}")
                results.append({
                    "type": "station",
                    "id": r["station_id"],
                    "title": r["longname"] or r["abbrev"],
                    "subtitle": " • ".join(sub),
                    "x": r["x"],
                    "y": r["y"],
                })

            # 2. Signals & Points
            cur.execute(
                """
                SELECT pk, id, signal_id, name, master_name, interlocking_sys_id, x, y
                FROM signals
                WHERE UPPER(name) LIKE UPPER(%s)
                   OR UPPER(master_name) LIKE UPPER(%s)
                   OR (%s::int IS NOT NULL AND (signal_id = %s::int OR id = %s::int))
                ORDER BY CASE WHEN UPPER(name) = UPPER(%s) THEN 0 ELSE 1 END
                LIMIT 20
                """,
                (q_like, q_like, q_int, q_int, q_int, q_clean),
            )
            for r in cur.fetchall():
                r_name = (r["name"] or r["master_name"] or "").strip()
                is_pts = (r.get("interlocking_sys_id") in (15, 16)) or r_name.upper().startswith("POINT")
                if is_pts:
                    clean_title = r_name if r_name.upper().startswith("POINT") else f"Points {r_name}"
                    results.append({
                        "type": "points",
                        "id": r["pk"],
                        "title": clean_title,
                        "subtitle": f"Points • Master ID: {r['signal_id']} • Master: {r['master_name']}",
                        "x": r["x"],
                        "y": r["y"],
                    })
                else:
                    results.append({
                        "type": "signal",
                        "id": r["pk"],
                        "title": f"Signal {r_name}",
                        "subtitle": f"Master ID: {r['signal_id']} • Master: {r['master_name']}",
                        "x": r["x"],
                        "y": r["y"],
                    })

            # 3. Attribute sections / ELRs
            cur.execute(
                """
                SELECT
                    a.pk, a.id, a.short_description, a.description,
                    a.min_x, a.max_x, a.min_y, a.max_y
                FROM attribute_sections a
                WHERE UPPER(a.description) LIKE UPPER(%s)
                   OR UPPER(a.short_description) LIKE UPPER(%s)
                LIMIT 15
                """,
                (q_like, q_like),
            )
            for r in cur.fetchall():
                cx = (r["min_x"] + r["max_x"]) / 2.0 if r["min_x"] is not None else None
                cy = (r["min_y"] + r["max_y"]) / 2.0 if r["min_y"] is not None else None
                results.append({
                    "type": "attributesection",
                    "id": r["pk"],
                    "title": f"Attribute Section {r['description']} ({r['short_description']})",
                    "subtitle": f"Attribute Section ID: {r['id']}",
                    "x": cx,
                    "y": cy,
                })

            # 4. Exact numeric lookups for Block, Route, and Node (Blocks & Routes prioritized at top)
            if q_int is not None:
                block_route_hits = []
                cur.execute(
                    "SELECT pk, block_id, min_x, max_x, min_y, max_y, pre_signal_distance FROM blocks WHERE block_id = %s LIMIT 5",
                    (q_int,),
                )
                for brow in cur.fetchall():
                    cx = (brow["min_x"] + brow["max_x"]) / 2.0 if brow["min_x"] is not None else None
                    cy = (brow["min_y"] + brow["max_y"]) / 2.0 if brow["min_y"] is not None else None
                    block_route_hits.append({
                        "type": "block",
                        "id": brow["pk"],
                        "title": f"Signalling Block #{brow['block_id']}",
                        "subtitle": f"Pre-signal dist: {brow['pre_signal_distance']}m",
                        "x": cx,
                        "y": cy,
                    })

                cur.execute(
                    "SELECT pk, route_id, station_id, min_x, max_x, min_y, max_y FROM routes WHERE route_id = %s LIMIT 5",
                    (q_int,),
                )
                for rrow in cur.fetchall():
                    cx = (rrow["min_x"] + rrow["max_x"]) / 2.0 if rrow["min_x"] is not None else None
                    cy = (rrow["min_y"] + rrow["max_y"]) / 2.0 if rrow["min_y"] is not None else None
                    block_route_hits.append({
                        "type": "route",
                        "id": rrow["pk"],
                        "title": f"Station Route #{rrow['route_id']}",
                        "subtitle": f"Station ID: {rrow['station_id']}",
                        "x": cx,
                        "y": cy,
                    })

                if block_route_hits:
                    results = block_route_hits + results

                cur.execute(
                    "SELECT node_id, netx, nety, km_region_id, km_value FROM nodes WHERE node_id = %s",
                    (q_int,),
                )
                nrow = cur.fetchone()
                if nrow:
                    elr = decode_code("kmregionmasterdesc", nrow["km_region_id"])
                    results.append({
                        "type": "node",
                        "id": nrow["node_id"],
                        "title": f"Node #{nrow['node_id']}",
                        "subtitle": f"ELR: {elr or 'N/A'} • ({nrow['netx']}, {nrow['nety']})",
                        "x": nrow["netx"],
                        "y": nrow["nety"],
                    })

            # 5. Timing Loads (<modeltrain>), Traction Units (<tractionunit>), and Station Bank Corridors (<stationbank>) — fast in-memory lookup
            tcat = get_timing_catalog()
            q_up = q_clean.upper()
            matched_items = []
            for mt in tcat.get("model_trains", []):
                if (
                    q_up in mt["name"].upper()
                    or q_up in mt["publicname"].upper()
                    or q_up in mt["desc"].upper()
                ):
                    matched_items.append((
                        0 if mt["name"].upper() == q_up else 1,
                        {
                            "type": "modeltrain",
                            "id": mt["id"],
                            "title": f"Timing Load {mt['name']} — {mt['desc'] or mt['publicname']}",
                            "subtitle": f"Max {int(mt['maxspeed_kmh'])} km/h ({mt['maxspeed_mph']} mph) • Profile: {mt['profile_name']}",
                            "x": None,
                            "y": None,
                        },
                    ))
            for tu in tcat.get("traction_units", []):
                if q_up in tu["name"].upper() or q_up in tu["desc"].upper():
                    matched_items.append((
                        0 if tu["name"].upper() == q_up else 1,
                        {
                            "type": "tractionunit",
                            "id": tu["id"],
                            "title": f"Traction Unit {tu['name']} — {tu['desc']}",
                            "subtitle": f"Type: {tu['type_code']} • {tu['length_m']}m / {tu['weight_t']}t • Max {int(tu['max_speed_kmh'])} km/h",
                            "x": None,
                            "y": None,
                        },
                    ))
            for sb in tcat.get("station_banks", []):
                if q_up in sb["code"].upper() or q_up in sb["description"].upper():
                    matched_items.append((
                        0 if sb["code"].upper() == q_up else 1,
                        {
                            "type": "stationbank",
                            "id": sb["id"],
                            "title": f"Timing Corridor {sb['code']} — {sb['description']}",
                            "subtitle": f"Station Bank #{sb['id']}",
                            "x": None,
                            "y": None,
                        },
                    ))
            matched_items.sort(key=lambda x: x[0])
            for _, item in matched_items[:18]:
                results.append(item)

    return {"results": results}


@router.get("/api/pick")
def api_pick(
    x: float = Query(...),
    y: float = Query(...),
    radius: float = Query(50.0),
):
    min_x, max_x = x - radius, x + radius
    min_y, max_y = y - radius, y + radius
    candidates = []

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # Signals & Points near point
            cur.execute(
                """
                SELECT pk, id, signal_id, name, master_name, interlocking_sys_id, bumper, x, y,
                       SQRT(POWER(x - %s, 2) + POWER(y - %s, 2)) AS dist
                FROM signals
                WHERE x BETWEEN %s AND %s AND y BETWEEN %s AND %s
                ORDER BY dist ASC
                LIMIT 6
                """,
                (x, y, min_x, max_x, min_y, max_y),
            )
            for r in cur.fetchall():
                r_name = (r["name"] or r["master_name"] or "").strip()
                is_pts = (r.get("interlocking_sys_id") in (15, 16)) or r_name.upper().startswith("POINT")
                if is_pts:
                    clean_lbl = r_name if r_name.upper().startswith("POINT") else f"Points {r_name}"
                    candidates.append({
                        "type": "points",
                        "id": r["pk"],
                        "label": clean_lbl,
                        "dist": round(r["dist"], 2),
                        "x": r["x"],
                        "y": r["y"],
                    })
                else:
                    candidates.append({
                        "type": "signal",
                        "id": r["pk"],
                        "label": f"Buffer Stop {r_name}" if r.get("bumper") == 1 else f"Signal {r_name}",
                        "dist": round(r["dist"], 2),
                        "x": r["x"],
                        "y": r["y"],
                    })

            # Nodes near point
            cur.execute(
                """
                SELECT node_id, netx, nety, km_region_id, km_value,
                       SQRT(POWER(netx - %s, 2) + POWER(nety - %s, 2)) AS dist
                FROM nodes
                WHERE netx BETWEEN %s AND %s AND nety BETWEEN %s AND %s
                ORDER BY dist ASC
                LIMIT 5
                """,
                (x, y, min_x, max_x, min_y, max_y),
            )
            for r in cur.fetchall():
                cur.execute(
                    "SELECT COUNT(*) AS deg FROM edges WHERE start_node_id = %s OR end_node_id = %s",
                    (r["node_id"], r["node_id"]),
                )
                deg_row = cur.fetchone()
                deg = deg_row["deg"] if deg_row else 2
                node_lbl = f"Points Node #{r['node_id']}" if deg >= 3 else f"Node #{r['node_id']}"
                candidates.append({
                    "type": "node",
                    "id": r["node_id"],
                    "label": node_lbl,
                    "dist": round(r["dist"], 2),
                    "x": r["netx"],
                    "y": r["nety"],
                })

            # Edges whose bbox intersects point
            cur.execute(
                """
                SELECT pk, edge_id, start_node_id, end_node_id, x0, y0, x1, y1, vmax, length, electrified
                FROM edges
                WHERE min_x <= %s AND max_x >= %s
                  AND min_y <= %s AND max_y >= %s
                  AND edge_id > 0
                LIMIT 20
                """,
                (max_x, min_x, max_y, min_y),
            )
            for r in cur.fetchall():
                # Point-to-segment distance
                dx = r["x1"] - r["x0"]
                dy = r["y1"] - r["y0"]
                l2 = dx * dx + dy * dy
                if l2 == 0:
                    dist = math.hypot(x - r["x0"], y - r["y0"])
                else:
                    t = max(0.0, min(1.0, ((x - r["x0"]) * dx + (y - r["y0"]) * dy) / l2))
                    px = r["x0"] + t * dx
                    py = r["y0"] + t * dy
                    dist = math.hypot(x - px, y - py)
                if dist <= radius:
                    candidates.append({
                        "type": "edge",
                        "id": r["pk"],
                        "label": f"Edge #{r['edge_id']} ({r['start_node_id']} → {r['end_node_id']}, {r['length']}m, Vmax {r['vmax']})",
                        "dist": round(dist, 2),
                        "x": (r["x0"] + r["x1"]) / 2.0,
                        "y": (r["y0"] + r["y1"]) / 2.0,
                        "coords": [[r["x0"], r["y0"]], [r["x1"], r["y1"]]],
                    })

            # Stations near point
            cur.execute(
                """
                SELECT station_id, abbrev, longname, crscode, x, y,
                       SQRT(POWER(x - %s, 2) + POWER(y - %s, 2)) AS dist
                FROM stations
                WHERE x BETWEEN %s AND %s AND y BETWEEN %s AND %s AND station_id > 0
                ORDER BY dist ASC
                LIMIT 5
                """,
                (x, y, min_x - 100, max_x + 100, min_y - 100, max_y + 100),
            )
            for r in cur.fetchall():
                candidates.append({
                    "type": "station",
                    "id": r["station_id"],
                    "label": f"Station {r['longname']} ({r['abbrev']})",
                    "dist": round(r["dist"], 2),
                    "x": r["x"],
                    "y": r["y"],
                })

            # Station platform tracks near point
            cur.execute(
                """
                SELECT st.pk, st.track_id, st.name, st.description, s.longname AS station_name, st.coords
                FROM station_tracks st
                LEFT JOIN stations s ON s.station_id = st.station_id
                WHERE st.min_x <= %s AND st.max_x >= %s
                  AND st.min_y <= %s AND st.max_y >= %s
                LIMIT 10
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
                    mid = coords[len(coords) // 2] if coords else [x, y]
                    candidates.append({
                        "type": "track",
                        "id": r["pk"],
                        "label": f"Track {r['name']} — {r['station_name'] or ''} ({r['description'] or ''})",
                        "dist": round(best_d, 2),
                        "x": mid[0],
                        "y": mid[1],
                        "coords": coords,
                    })

            # Signalling blocks passing through nearby nodes
            node_ids_near = [c["id"] for c in candidates if c["type"] == "node"]
            if not node_ids_near:
                cur.execute(
                    "SELECT start_node_id, end_node_id FROM edges WHERE min_x <= %s AND max_x >= %s AND min_y <= %s AND max_y >= %s LIMIT 5",
                    (max_x, min_x, max_y, min_y),
                )
                for er in cur.fetchall():
                    if er["start_node_id"]:
                        node_ids_near.append(er["start_node_id"])
                    if er["end_node_id"]:
                        node_ids_near.append(er["end_node_id"])

            if node_ids_near:
                cur.execute(
                    """
                    SELECT pk, block_id, pre_signal_distance, sight_distance, coords
                    FROM blocks
                    WHERE node_ids && %s::int[]
                    LIMIT 6
                    """,
                    (node_ids_near,),
                )
                for r in cur.fetchall():
                    bcoords = r["coords"] or []
                    bmid = bcoords[len(bcoords) // 2] if bcoords else [x, y]
                    candidates.append({
                        "type": "block",
                        "id": r["pk"],
                        "label": f"Block #{r['block_id']} (Pre-sig {r['pre_signal_distance']}m, Sight {r['sight_distance']}m)",
                        "dist": round(radius * 0.6, 2),
                        "x": bmid[0],
                        "y": bmid[1],
                        "coords": bcoords,
                    })

                cur.execute(
                    """
                    SELECT r.pk, r.route_id, s.longname AS station_name, r.coords
                    FROM routes r
                    LEFT JOIN stations s ON s.station_id = r.station_id
                    WHERE r.node_ids && %s::int[]
                    LIMIT 5
                    """,
                    (node_ids_near,),
                )
                for r in cur.fetchall():
                    rcoords = r["coords"] or []
                    rmid = rcoords[len(rcoords) // 2] if rcoords else [x, y]
                    candidates.append({
                        "type": "route",
                        "id": r["pk"],
                        "label": f"Route #{r['route_id']} ({r['station_name'] or ''})",
                        "dist": round(radius * 0.65, 2),
                        "x": rmid[0],
                        "y": rmid[1],
                        "coords": rcoords,
                    })

                cur.execute(
                    """
                    SELECT pk, id, short_description, description, coords
                    FROM attribute_sections
                    WHERE node_ids && %s::int[]
                    LIMIT 5
                    """,
                    (node_ids_near,),
                )
                for r in cur.fetchall():
                    acoords = r["coords"] or []
                    amid = acoords[len(acoords) // 2] if acoords else [x, y]
                    candidates.append({
                        "type": "attributesection",
                        "id": r["pk"],
                        "label": f"Attr Section {r['description']} ({r['short_description']})",
                        "dist": round(radius * 0.7, 2),
                        "x": amid[0],
                        "y": amid[1],
                        "coords": acoords,
                    })

    candidates.sort(key=lambda c: (0 if c["type"] in ("block", "route") else 1, c["dist"]))
    return {"candidates": candidates[:15]}


@router.get("/api/timing-loads/catalog")
def api_timing_loads_catalog():
    cat = get_timing_catalog()
    return {k: v for k, v in cat.items() if k != "station_to_banks"}


@router.get("/api/timing-loads/list")
def api_timing_loads_list(
    tab: str = Query("model_trains"),
    q: str = Query(""),
    limit: int = Query(40, le=200),
    offset: int = Query(0),
):
    """Fast server-side paginated & filtered endpoint for the Timing Loads, Train Classes & Corridors Explorer."""
    cat = get_timing_catalog()
    q_low = q.strip().lower()

    if tab == "model_trains":
        rows = cat["model_trains"]
        if q_low.startswith("profile:"):
            pid = q_low.split(":", 1)[1].strip()
            rows = [r for r in rows if str(r["profile_id"]) == pid]
        elif q_low:
            rows = [
                r
                for r in rows
                if q_low in r["name"].lower()
                or q_low in r["publicname"].lower()
                or q_low in r["desc"].lower()
                or q_low in r["traction_summary"].lower()
                or q_low in r["profile_name"].lower()
            ]
        return {
            "tab": tab,
            "counts": cat["counts"],
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "rows": rows[offset : offset + limit],
        }

    elif tab == "traction_units":
        rows = cat["traction_units"]
        if q_low:
            rows = [
                r
                for r in rows
                if q_low in r["name"].lower()
                or q_low in r["desc"].lower()
                or q_low in r["type_code"].lower()
                or q_low in r["motor_desc"].lower()
            ]
        return {
            "tab": tab,
            "counts": cat["counts"],
            "total": len(rows),
            "offset": offset,
            "limit": limit,
            "rows": rows[offset : offset + limit],
        }

    elif tab == "profiles_coaches":
        profs = cat["velocity_profiles"]
        coaches = cat["coaches"]
        ttypes = cat["train_types"]
        if q_low:
            profs = [p for p in profs if q_low in p["code"].lower() or q_low in p["description"].lower()]
            coaches = [c for c in coaches if q_low in c["name"].lower() or q_low in c["desc"].lower()]
            ttypes = [t for t in ttypes if q_low in t["code"].lower() or q_low in t["description"].lower()]
        return {
            "tab": tab,
            "counts": cat["counts"],
            "total": len(coaches),
            "offset": 0,
            "limit": limit,
            "velocity_profiles": profs,
            "coaches": coaches,
            "train_types": ttypes,
        }

    elif tab == "station_banks":
        banks = cat["station_banks"]
        if q_low:
            banks = [
                b
                for b in banks
                if q_low in b["code"].lower() or q_low in b["description"].lower()
            ]
        return {
            "tab": tab,
            "counts": cat["counts"],
            "total": len(banks),
            "offset": offset,
            "limit": limit,
            "rows": banks[offset : offset + limit],
            "timing_roles": cat["timing_roles"],
            "planning_categories": cat["planning_categories"],
        }

    raise HTTPException(status_code=400, detail=f"Unknown timing tab: {tab}")


@router.get("/api/catalog")
def api_catalog():
    """Returns all 122 XML tag types + 4 NWR TrackModel tables and their exact counts stored in PostgreSQL."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT value FROM ingestion_meta WHERE key = 'ingestion_complete'"
            )
            row = cur.fetchone()
            tag_counts = dict(row[0].get("tag_counts", {})) if row else {}
            cur.execute(
                "SELECT value FROM ingestion_meta WHERE key = 'nwr_trackmodel_complete'"
            )
            nrow = cur.fetchone()
            if nrow and nrow[0].get("counts"):
                tag_counts.update(nrow[0]["counts"])
    items = [
        {"tag": k, "count": v}
        for k, v in sorted(tag_counts.items(), key=lambda x: (-x[1], x[0]))
    ]
    return {"total_tags": len(items), "tags": items}


@router.get("/api/catalog/{tag}")
def api_catalog_rows(
    tag: str,
    q: str = Query(""),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    """Browse any XML tag or NWR shapefile table in the database losslessly."""
    dedicated_map = {
        "node": ("nodes", "node_id", "name"),
        "edge": ("edges", "edge_id", "last_modified"),
        "signal": ("signals", "signal_id", "name"),
        "mastersignal": ("mastersignals", "id", "name"),
        "station": ("stations", "station_id", "longname"),
        "stationboundary": ("station_boundaries", "signal_id", "last_modified"),
        "block": ("blocks", "block_id", "business_reference_id"),
        "route": ("routes", "route_id", "last_modified"),
        "section": ("sections", "section_id", "name"),
        "attributesection": ("attribute_sections", "id", "description"),
        "graphicvector": ("graphic_vectors", "pk", "last_modified"),
        "graphictext": ("graphic_texts", "pk", "text"),
        "polyarea": ("poly_areas", "id", "name"),
        "annotation": ("annotations", "pk", "text"),
        "nwr_track_centrelines": ("nwr_track_centrelines", "asset_id", "elr"),
        "nwr_track_nodes": ("nwr_track_nodes", "asset_id", "extracted"),
        "nwr_elrs": ("nwr_elrs", "asset_id", "elr"),
        "nwr_waymarks": ("nwr_waymarks", "asset_id", "elr"),
    }

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if tag in dedicated_map:
                tbl, id_col, name_col = dedicated_map[tag]
                has_children = tbl not in (
                    "mastersignals", "sections", "graphic_vectors", "graphic_texts", "annotations",
                    "nwr_track_centrelines", "nwr_track_nodes", "nwr_elrs", "nwr_waymarks"
                )
                child_expr = "raw_children" if has_children else "'[]'::jsonb AS raw_children"
                if q.strip():
                    cur.execute(
                        f"""
                        SELECT {id_col}::text AS entity_id, {name_col}::text AS name, raw_attrs, {child_expr}
                        FROM {tbl}
                        WHERE {id_col}::text ILIKE %s OR {name_col}::text ILIKE %s OR raw_attrs::text ILIKE %s
                        LIMIT %s OFFSET %s
                        """,
                        (f"%{q}%", f"%{q}%", f"%{q}%", limit, offset),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT {id_col}::text AS entity_id, {name_col}::text AS name, raw_attrs, {child_expr}
                        FROM {tbl}
                        LIMIT %s OFFSET %s
                        """,
                        (limit, offset),
                    )
                rows = cur.fetchall()
            else:
                if q.strip():
                    cur.execute(
                        """
                        SELECT entity_id, name, raw_attrs, raw_children
                        FROM raw_tps_elements
                        WHERE tag = %s AND (entity_id ILIKE %s OR name ILIKE %s OR raw_attrs::text ILIKE %s)
                        ORDER BY pk ASC
                        LIMIT %s OFFSET %s
                        """,
                        (tag, f"%{q}%", f"%{q}%", f"%{q}%", limit, offset),
                    )
                else:
                    cur.execute(
                        """
                        SELECT entity_id, name, raw_attrs, raw_children
                        FROM raw_tps_elements
                        WHERE tag = %s
                        ORDER BY pk ASC
                        LIMIT %s OFFSET %s
                        """,
                        (tag, limit, offset),
                    )
                rows = cur.fetchall()
    return {"tag": tag, "rows": rows}

