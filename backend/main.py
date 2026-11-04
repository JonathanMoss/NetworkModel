import os
import math
import threading
from contextlib import asynccontextmanager
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import ORJSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from ingest import INGEST_STATE, check_already_ingested, run_ingestion
from ingest_nwr import (
    ensure_nwr_tables_and_ingest,
    decode_track_id,
    format_mile_yards,
    VALENCY_DESC,
)
from ingest_network_fusion import ensure_network_fusion_tables_and_ingest

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://nr_user:nr_password@localhost:5433/network_rail_tps"
)
XML_PATH = os.environ.get("XML_PATH", "/data/tps")
NWR_DIR = os.environ.get("NWR_DIR", "/data/nwr")
NF_DIR = os.environ.get("NF_DIR", "/data/network_fusion")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

OVERVIEW_CACHE = None
NWR_OVERVIEW_CACHE = None
LOOKUP_CACHE = None
TIMING_CATALOG_CACHE = None


def get_conn():
    return psycopg.connect(DATABASE_URL)


def meters_to_miles_chains(meters: Optional[int]) -> Optional[str]:
    if meters is None:
        return None
    total_chains = meters / 20.1168
    miles = int(total_chains // 80)
    chains = round(total_chains % 80, 1)
    if chains >= 80.0:
        miles += 1
        chains -= 80.0
    return f"{miles}m {chains:04.1f}ch ({meters / 1000.0:.3f} km)"


def load_lookup_cache():
    global LOOKUP_CACHE
    if LOOKUP_CACHE is not None:
        return LOOKUP_CACHE
    cache = {}
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT category, code_id, label, vanilla_text, description, raw_attrs FROM lookup_codes"
            )
            for r in cur:
                cat = r["category"]
                if cat not in cache:
                    cache[cat] = {}
                display = r["label"] or r["vanilla_text"] or r["description"] or r["code_id"]
                if r["vanilla_text"] and r["label"] and r["vanilla_text"] != r["label"]:
                    display = f"{r['label']} ({r['vanilla_text']})"
                cache[cat][str(r["code_id"])] = {
                    "display": display,
                    "label": r["label"],
                    "vanilla_text": r["vanilla_text"],
                    "description": r["description"],
                    "raw_attrs": r["raw_attrs"],
                }
    LOOKUP_CACHE = cache
    return cache


def decode_code(category: str, code_id) -> Optional[str]:
    if code_id is None:
        return None
    cache = load_lookup_cache()
    cat_map = cache.get(category, {})
    entry = cat_map.get(str(code_id))
    return entry["display"] if entry else None


def start_background_ingestion():
    def _worker():
        if not check_already_ingested(DATABASE_URL):
            run_ingestion(DATABASE_URL, XML_PATH, SCHEMA_PATH)
        ensure_nwr_tables_and_ingest(DATABASE_URL, NWR_DIR)
        ensure_network_fusion_tables_and_ingest(DATABASE_URL, NF_DIR)
        try:
            load_lookup_cache()
            get_timing_catalog()
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    start_background_ingestion()
    yield


app = FastAPI(
    title="Network Rail TPS Schematic & Asset Explorer",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)


@app.get("/api/status")
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


@app.get("/api/overview")
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


@app.get("/api/viewport")
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


@app.get("/api/search")
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

            # 2. Signals
            cur.execute(
                """
                SELECT pk, id, signal_id, name, master_name, x, y
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
                results.append({
                    "type": "signal",
                    "id": r["pk"],
                    "title": f"Signal {r['name'] or r['master_name']}",
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


@app.get("/api/pick")
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
            # Signals near point
            cur.execute(
                """
                SELECT pk, id, signal_id, name, master_name, x, y,
                       SQRT(POWER(x - %s, 2) + POWER(y - %s, 2)) AS dist
                FROM signals
                WHERE x BETWEEN %s AND %s AND y BETWEEN %s AND %s
                ORDER BY dist ASC
                LIMIT 5
                """,
                (x, y, min_x, max_x, min_y, max_y),
            )
            for r in cur.fetchall():
                candidates.append({
                    "type": "signal",
                    "id": r["pk"],
                    "label": f"Signal {r['name'] or r['master_name']}",
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
                candidates.append({
                    "type": "node",
                    "id": r["node_id"],
                    "label": f"Node #{r['node_id']}",
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


def resolve_node_path_details(cur, node_ids: list):
    """Resolve ordered node coordinates, edge lengths (m), speed limits, and turnout switches along a node path."""
    clean_ids = [int(n) for n in (node_ids or []) if n is not None]
    if not clean_ids:
        return {
            "coords": [],
            "length_meters": 0,
            "min_vmax": None,
            "max_vmax": None,
            "edge_lengths": [],
            "turnout_nodes": [],
            "node_map": {},
        }

    cur.execute(
        "SELECT node_id, netx, nety, km_region_id, km_value FROM nodes WHERE node_id = ANY(%s)",
        (clean_ids,),
    )
    nmap = {r["node_id"]: r for r in cur.fetchall()}

    cur.execute(
        """
        SELECT pk, edge_id, start_node_id, end_node_id, length, vmax, electrified, gradient
        FROM edges
        WHERE (start_node_id = ANY(%s) OR end_node_id = ANY(%s))
          AND edge_id > 0
        """,
        (clean_ids, clean_ids),
    )
    edge_lookup = {}
    node_degree = {}
    for er in cur.fetchall():
        u = er["start_node_id"]
        v = er["end_node_id"]
        node_degree[u] = node_degree.get(u, 0) + 1
        node_degree[v] = node_degree.get(v, 0) + 1
        edge_lookup[(u, v)] = er
        if (v, u) not in edge_lookup:
            edge_lookup[(v, u)] = er

    coords = []
    for nid in clean_ids:
        if nid in nmap:
            coords.append([nmap[nid]["netx"], nmap[nid]["nety"]])

    total_len = 0
    edge_lengths = []
    vmax_vals = []
    for i in range(len(clean_ids) - 1):
        u, v = clean_ids[i], clean_ids[i + 1]
        er = edge_lookup.get((u, v))
        if er:
            el = int(er["length"] or 0)
            total_len += el
            edge_lengths.append(el)
            if er["vmax"] and er["vmax"] > 0:
                vmax_vals.append(int(er["vmax"]))
        else:
            edge_lengths.append(0)

    turnout_nodes = []
    seen_t = set()
    for nid in clean_ids:
        deg = node_degree.get(nid, 0)
        if deg >= 3 and nid in nmap and nid not in seen_t:
            seen_t.add(nid)
            turnout_nodes.append({
                "node_id": nid,
                "x": nmap[nid]["netx"],
                "y": nmap[nid]["nety"],
                "degree": deg,
            })

    return {
        "coords": coords,
        "length_meters": total_len,
        "min_vmax": min(vmax_vals) if vmax_vals else None,
        "max_vmax": max(vmax_vals) if vmax_vals else None,
        "edge_lengths": edge_lengths,
        "turnout_nodes": turnout_nodes,
        "node_map": nmap,
    }


def interpolate_from_end(coords: list, edge_lengths: list, dist_from_end_m: float):
    """Walk backwards dist_from_end_m metres from the end of a path and return [x, y]."""
    if not coords or len(coords) < 2 or not dist_from_end_m or dist_from_end_m <= 0:
        return None
    n_seg = min(len(coords) - 1, len(edge_lengths))
    rem = float(dist_from_end_m)
    for i in range(n_seg - 1, -1, -1):
        seg_len = float(edge_lengths[i] or 0.0)
        p0 = coords[i]
        p1 = coords[i + 1]
        if seg_len <= 0:
            continue
        if rem <= seg_len:
            frac = rem / seg_len
            ix = p1[0] + frac * (p0[0] - p1[0])
            iy = p1[1] + frac * (p0[1] - p1[1])
            return [round(ix, 2), round(iy, 2)]
        rem -= seg_len
    return [round(coords[0][0], 2), round(coords[0][1], 2)]


def interpolate_pt_along_coords(coords: list, c_start_m: float, c_end_m: float, target_m: float):
    """Interpolate [x, y] coordinates along an OSGB36 polyline for a target mileage."""
    if not coords:
        return None
    if len(coords) == 1 or abs(c_end_m - c_start_m) < 0.001:
        return [round(coords[0][0], 3), round(coords[0][1], 3)]
    t = (target_m - c_start_m) / (c_end_m - c_start_m)
    t = max(0.0, min(1.0, t))

    seg_lens = []
    total_len = 0.0
    for i in range(len(coords) - 1):
        dx = coords[i + 1][0] - coords[i][0]
        dy = coords[i + 1][1] - coords[i][1]
        sl = math.hypot(dx, dy)
        seg_lens.append(sl)
        total_len += sl

    if total_len <= 0.0001:
        return [round(coords[0][0], 3), round(coords[0][1], 3)]

    target_dist = t * total_len
    acc = 0.0
    for i, sl in enumerate(seg_lens):
        if acc + sl >= target_dist or i == len(seg_lens) - 1:
            frac = (target_dist - acc) / sl if sl > 0 else 0.0
            frac = max(0.0, min(1.0, frac))
            ix = coords[i][0] + frac * (coords[i + 1][0] - coords[i][0])
            iy = coords[i][1] + frac * (coords[i + 1][1] - coords[i][1])
            return [round(ix, 3), round(iy, 3)]
        acc += sl
    return [round(coords[-1][0], 3), round(coords[-1][1], 3)]


def slice_polyline(coords: list, c_start_m: float, c_end_m: float, from_m: float, to_m: float):
    """Slice an OSGB36 polyline between from_m and to_m mileages."""
    if not coords or len(coords) < 2 or abs(c_end_m - c_start_m) < 0.001:
        return coords
    t1 = (from_m - c_start_m) / (c_end_m - c_start_m)
    t2 = (to_m - c_start_m) / (c_end_m - c_start_m)
    t_start = max(0.0, min(1.0, t1, t2))
    t_end = min(1.0, max(0.0, t1, t2))
    if t_end - t_start < 0.0001:
        pt = interpolate_pt_along_coords(coords, c_start_m, c_end_m, from_m)
        return [pt, pt] if pt else coords

    seg_lens = []
    total_len = 0.0
    for i in range(len(coords) - 1):
        dx = coords[i + 1][0] - coords[i][0]
        dy = coords[i + 1][1] - coords[i][1]
        sl = math.hypot(dx, dy)
        seg_lens.append(sl)
        total_len += sl

    if total_len <= 0.0001:
        return coords

    d_start = t_start * total_len
    d_end = t_end * total_len

    out = []
    acc = 0.0
    start_added = False
    for i, sl in enumerate(seg_lens):
        seg_start_dist = acc
        seg_end_dist = acc + sl

        if not start_added and (d_start <= seg_end_dist or i == len(seg_lens) - 1):
            frac = (d_start - seg_start_dist) / sl if sl > 0 else 0.0
            frac = max(0.0, min(1.0, frac))
            sx = coords[i][0] + frac * (coords[i + 1][0] - coords[i][0])
            sy = coords[i][1] + frac * (coords[i + 1][1] - coords[i][1])
            out.append([round(sx, 3), round(sy, 3)])
            start_added = True

        if start_added:
            if seg_end_dist < d_end:
                out.append([round(coords[i + 1][0], 3), round(coords[i + 1][1], 3)])
            else:
                frac = (d_end - seg_start_dist) / sl if sl > 0 else 0.0
                frac = max(0.0, min(1.0, frac))
                ex = coords[i][0] + frac * (coords[i + 1][0] - coords[i][0])
                ey = coords[i][1] + frac * (coords[i + 1][1] - coords[i][1])
                out.append([round(ex, 3), round(ey, 3)])
                break
        acc += sl

    return out if len(out) >= 2 else coords


def score_track_candidate(
    track_id: str,
    dir_hint: Optional[str],
    name_hint: Optional[str],
    c_start: float,
    c_end: float,
    target_m: Optional[float],
) -> float:
    """Score a candidate NWR centreline to pick the single best match."""
    score = 50.0
    tid = str(track_id or "")

    # Coverage bonus / penalty
    if target_m is not None:
        min_m = min(c_start, c_end)
        max_m = max(c_start, c_end)
        if min_m - 50.0 <= target_m <= max_m + 50.0:
            score += 40.0
        else:
            mid = (c_start + c_end) / 2.0
            score -= min(30.0, abs(mid - target_m) * 0.02)

    # Direction matching (1 = UP, 2 = DOWN, 3 = REVERSIBLE)
    if dir_hint:
        dh = dir_hint.lower()
        if "down" in dh or dh == "dn":
            if tid.startswith("2"):
                score += 60.0
            elif tid.startswith("3"):
                score += 25.0
            elif tid.startswith("1"):
                score -= 50.0
        elif "up" in dh:
            if tid.startswith("1"):
                score += 60.0
            elif tid.startswith("3"):
                score += 25.0
            elif tid.startswith("2"):
                score -= 50.0

    # Name matching (Fast / Slow / Platform / Loop / Siding)
    if name_hint:
        nh = name_hint.lower()
        if any(w in nh for w in ("fast", "main", "line 1")):
            if len(tid) >= 2 and tid[1] == "1":
                score += 45.0
        elif any(w in nh for w in ("slow", "relief", "line 2")):
            if len(tid) >= 2 and tid[1] == "2":
                score += 45.0
        elif "loop" in nh:
            if len(tid) >= 2 and tid[1] == "5":
                score += 45.0
        elif "siding" in nh or "yard" in nh:
            if len(tid) >= 2 and tid[1] == "9":
                score += 45.0
        elif "crossover" in nh:
            if len(tid) >= 2 and tid[1] == "7":
                score += 45.0
        elif "platform" in nh or "plat" in nh:
            if len(tid) >= 2 and tid[1] in ("4", "6", "8"):
                score += 35.0

    return score


def find_nwr_cross_links_for_tps(
    cur,
    elr: Optional[str],
    km_meters: Optional[float],
    end_km_meters: Optional[float] = None,
    direction_hint: Optional[str] = None,
    track_name_hint: Optional[str] = None,
    tps_valency: Optional[int] = None,
    item_label: Optional[str] = None,
    is_extrapolated: bool = False,
):
    """Find matching NWR Track Model (OSGB36) ELR, single best-match track, and discrete node/signal points."""
    if not elr:
        return {
            "elr_record": None,
            "centrelines": [],
            "corridor_centrelines": [],
            "best_centreline": None,
            "node_point": None,
            "node_points": [],
            "snapped_node": None,
            "geo_snap": None,
            "single_match": False,
        }
    try:
        cur.execute(
            "SELECT fid, asset_id, elr, start_mile_yards, end_mile_yards, parts, min_x, max_x, min_y, max_y FROM nwr_elrs WHERE elr = %s LIMIT 1",
            (elr,),
        )
        elr_rec = cur.fetchone()

        target_m = km_meters
        if km_meters is not None and end_km_meters is not None:
            target_m = (km_meters + end_km_meters) / 2.0
            span_min = min(km_meters, end_km_meters) - 300.0
            span_max = max(km_meters, end_km_meters) + 300.0
        elif km_meters is not None:
            span_min = km_meters - 400.0
            span_max = km_meters + 400.0
        else:
            span_min = None
            span_max = None

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
                ORDER BY ABS(((start_meters + end_meters) / 2.0) - %s) ASC
                LIMIT 16
                """,
                (elr, span_max, span_min, target_m),
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
                LIMIT 8
                """,
                (elr,),
            )

        raw_candidates = cur.fetchall()
        if not raw_candidates:
            # Fallback: find nearest centreline on this ELR even if outside the search margin
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
                (elr, target_m if target_m is not None else 0),
            )
            raw_candidates = cur.fetchall()
            if raw_candidates:
                is_extrapolated = True

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
            }

        candidates = []
        for c in raw_candidates:
            score = score_track_candidate(
                c["track_id"],
                direction_hint,
                track_name_hint,
                c["start_meters"],
                c["end_meters"],
                target_m,
            )
            candidates.append({
                **c,
                "track_id_decoded": decode_track_id(c["track_id"]),
                "_score": score,
            })

        candidates.sort(key=lambda x: x["_score"], reverse=True)
        best_candidate = dict(candidates[0])
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
            from_m = min(km_meters, end_km_meters)
            to_m = max(km_meters, end_km_meters)
            sliced = slice_polyline(
                orig_coords,
                c_start_m,
                c_end_m,
                from_m,
                to_m,
            )
            if sliced and len(sliced) >= 2:
                best_candidate["coords"] = sliced
                best_candidate["clipped"] = True
                clipped_len = round(abs(end_km_meters - km_meters), 1)
                best_candidate["clipped_length_meters"] = clipped_len
                is_clipped = True

        # Discrete points & Physical Node Snapping
        node_points = []
        snapped_nwr_node = None

        if km_meters is not None and orig_coords:
            pt_main = interpolate_pt_along_coords(
                orig_coords,
                c_start_m,
                c_end_m,
                km_meters,
            )
            if pt_main:
                # Query physical nwr_track_nodes near pt_main
                cur.execute(
                    """
                    SELECT fid, asset_id, valency, x, y,
                           SQRT(POWER(x - %s, 2) + POWER(y - %s, 2)) AS dist
                    FROM nwr_track_nodes
                    WHERE x BETWEEN %s - 90.0 AND %s + 90.0
                      AND y BETWEEN %s - 90.0 AND %s + 90.0
                    ORDER BY dist ASC
                    LIMIT 6
                    """,
                    (pt_main[0], pt_main[1], pt_main[0], pt_main[0], pt_main[1], pt_main[1]),
                )
                nearby_nodes = cur.fetchall()
                if nearby_nodes:
                    if tps_valency is not None and tps_valency >= 3:
                        # Prioritize switch/turnout nodes with valency >= 3
                        switch_nodes = [nd for nd in nearby_nodes if nd["valency"] >= 3]
                        picked = switch_nodes[0] if switch_nodes else nearby_nodes[0]
                    else:
                        picked = nearby_nodes[0]

                    snapped_nwr_node = {
                        "fid": picked["fid"],
                        "asset_id": picked["asset_id"],
                        "valency": picked["valency"],
                        "valency_desc": VALENCY_DESC.get(picked["valency"], f"Valency {picked['valency']}"),
                        "x": picked["x"],
                        "y": picked["y"],
                        "dist_meters": round(picked["dist"], 2),
                        "is_switch": picked["valency"] >= 3,
                    }

                # Construct discrete node points
                if end_km_meters is not None and abs(end_km_meters - km_meters) >= 1.0:
                    pt_end = interpolate_pt_along_coords(
                        orig_coords,
                        c_start_m,
                        c_end_m,
                        end_km_meters,
                    )
                    start_lbl = f"Start Node ({meters_to_miles_chains(int(km_meters))})"
                    end_lbl = f"End Node ({meters_to_miles_chains(int(end_km_meters))})"
                    node_points.append({
                        "x": pt_main[0],
                        "y": pt_main[1],
                        "label": start_lbl,
                        "color": "#38bdf8",
                        "snapped_node": snapped_nwr_node,
                    })
                    if pt_end:
                        node_points.append({
                            "x": pt_end[0],
                            "y": pt_end[1],
                            "label": end_lbl,
                            "color": "#10b981",
                        })
                else:
                    if snapped_nwr_node and snapped_nwr_node["is_switch"]:
                        pt_label = f"🔀 Switch Node #{snapped_nwr_node['asset_id']} (Valency {snapped_nwr_node['valency']})"
                        pt_col = "#ec4899"
                        use_x = snapped_nwr_node["x"]
                        use_y = snapped_nwr_node["y"]
                    elif snapped_nwr_node:
                        pt_label = f"📍 Physical Node #{snapped_nwr_node['asset_id']} ({meters_to_miles_chains(int(km_meters))})"
                        pt_col = "#f59e0b"
                        use_x = snapped_nwr_node["x"]
                        use_y = snapped_nwr_node["y"]
                    else:
                        pt_label = f"{item_label or 'Point'} ({meters_to_miles_chains(int(km_meters))})"
                        pt_col = "#f59e0b"
                        use_x = pt_main[0]
                        use_y = pt_main[1]

                    node_points.append({
                        "x": use_x,
                        "y": use_y,
                        "label": pt_label,
                        "color": pt_col,
                        "snapped_node": snapped_nwr_node,
                    })

        # Geographic snap camera target
        geo_snap = None
        if node_points:
            if len(node_points) == 1:
                geo_snap = {
                    "x": node_points[0]["x"],
                    "y": node_points[0]["y"],
                    "elr": elr,
                    "mileage_meters": km_meters,
                    "mileage_str": meters_to_miles_chains(int(km_meters)) if km_meters else None,
                    "target_zoom": 0.28,
                    "is_extrapolated": is_extrapolated,
                }
            elif len(node_points) >= 2:
                p1 = node_points[0]
                p2 = node_points[1]
                mid_x = round((p1["x"] + p2["x"]) / 2.0, 3)
                mid_y = round((p1["y"] + p2["y"]) / 2.0, 3)
                span = math.hypot(p2["x"] - p1["x"], p2["y"] - p1["y"])
                calc_zoom = round(min(0.28, max(0.04, 450.0 / max(span, 250.0))), 3)
                mid_m = ((km_meters or 0) + (end_km_meters or 0)) / 2.0
                geo_snap = {
                    "x": mid_x,
                    "y": mid_y,
                    "elr": elr,
                    "mileage_meters": mid_m,
                    "mileage_str": meters_to_miles_chains(int(mid_m)),
                    "target_zoom": calc_zoom,
                    "is_extrapolated": is_extrapolated,
                }
        elif best_candidate and best_candidate.get("coords"):
            mid_c = best_candidate["coords"][len(best_candidate["coords"]) // 2]
            geo_snap = {
                "x": mid_c[0],
                "y": mid_c[1],
                "elr": elr,
                "mileage_meters": km_meters,
                "mileage_str": meters_to_miles_chains(int(km_meters)) if km_meters else None,
                "target_zoom": 0.15,
                "is_extrapolated": is_extrapolated,
            }

        return {
            "elr_record": elr_rec,
            "centrelines": [best_candidate],
            "corridor_centrelines": candidates[:10],
            "best_centreline": best_candidate,
            "node_point": node_points[0] if node_points else None,
            "node_points": node_points,
            "snapped_node": snapped_nwr_node,
            "geo_snap": geo_snap,
            "clipped": is_clipped,
            "clipped_length_meters": clipped_len if is_clipped else None,
            "single_match": True,
        }
    except Exception:
        return {
            "elr_record": None,
            "centrelines": [],
            "corridor_centrelines": [],
            "best_centreline": None,
            "node_point": None,
            "node_points": [],
            "snapped_node": None,
            "geo_snap": None,
            "single_match": False,
        }


@app.get("/api/asset/{asset_type}/{asset_id}")
def api_get_asset(asset_type: str, asset_id: int):
    lookup = load_lookup_cache()

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if asset_type == "station":
                cur.execute("SELECT * FROM stations WHERE station_id = %s", (asset_id,))
                st = cur.fetchone()
                if not st:
                    raise HTTPException(status_code=404, detail="Station not found")

                cur.execute(
                    """
                    SELECT * FROM station_tracks
                    WHERE station_id = %s
                    ORDER BY seq ASC, track_id ASC
                    """,
                    (asset_id,),
                )
                tracks = []
                highlight_ways = []
                for t in cur.fetchall():
                    if t["coords"]:
                        highlight_ways.append({
                            "label": f"Track {t['name']} ({t['description']})",
                            "coords": t["coords"],
                            "color": "#38bdf8",
                        })
                    tracks.append({
                        "pk": t["pk"],
                        "track_id": t["track_id"],
                        "name": t["name"],
                        "longname": t["longname"],
                        "seq": t["seq"],
                        "description": t["description"],
                        "business_reference_id": t["business_reference_id"],
                        "track_category": t["track_category"],
                        "track_category_desc": decode_code("trackcategorydesc", t["track_category"]),
                        "effective_length": t["effective_length"],
                        "rop_line_code": t["rop_line_code"],
                        "rop_line_code_desc": decode_code("roplinecodedesc", t["rop_line_code"]),
                        "sa_line_code": t["sa_line_code"],
                        "sa_line_code_desc": decode_code("salinecodedesc", t["sa_line_code"]),
                        "permissive_working": t["permissive_working"],
                        "directed": t["directed"],
                        "valid_from": t["valid_from"],
                        "valid_to": t["valid_to"],
                        "node_count": len(t["node_ids"] or []),
                        "node_ids": t["node_ids"],
                        "raw_attrs": t["raw_attrs"],
                    })

                # Station boundary signals
                cur.execute(
                    """
                    SELECT
                        sb.pk, sb.signal_id, sb.boundary_type_id, sb.valid_from, sb.valid_to,
                        s.pk AS sig_pk, s.name AS sig_name, s.master_name, s.x, s.y, s.interlocking_sys_id
                    FROM station_boundaries sb
                    LEFT JOIN signals s ON s.signal_id = sb.signal_id
                    WHERE %s = ANY(sb.station_ids)
                    LIMIT 100
                    """,
                    (asset_id,),
                )
                boundaries = []
                highlight_points = []
                for b in cur.fetchall():
                    if b["x"] is not None and b["y"] is not None:
                        highlight_points.append({
                            "x": b["x"],
                            "y": b["y"],
                            "label": b["sig_name"] or b["master_name"] or f"Sig {b['signal_id']}",
                            "color": "#f59e0b",
                        })
                    boundaries.append({
                        "signal_id": b["signal_id"],
                        "signal_pk": b["sig_pk"],
                        "signal_name": b["sig_name"] or b["master_name"],
                        "boundary_type_id": b["boundary_type_id"],
                        "boundary_type_desc": decode_code("stationboundarytypedesc", b["boundary_type_id"]),
                        "interlocking_desc": decode_code("interlockingsystem", b["interlocking_sys_id"]),
                        "x": b["x"],
                        "y": b["y"],
                    })

                # Routes associated with this station
                cur.execute(
                    """
                    SELECT pk, route_id, rop_line_code_id, sa_line_code_id, prelocation, node_ids, coords
                    FROM routes
                    WHERE station_id = %s
                    ORDER BY route_id ASC
                    LIMIT 100
                    """,
                    (asset_id,),
                )
                routes = []
                for r in cur.fetchall():
                    routes.append({
                        "pk": r["pk"],
                        "route_id": r["route_id"],
                        "rop_line_code_id": r["rop_line_code_id"],
                        "rop_line_code_desc": decode_code("roplinecodedesc", r["rop_line_code_id"]),
                        "sa_line_code_id": r["sa_line_code_id"],
                        "sa_line_code_desc": decode_code("salinecodedesc", r["sa_line_code_id"]),
                        "prelocation": r["prelocation"],
                        "node_count": len(r["node_ids"] or []),
                    })

                # Station Bank Corridors (<stationbank> / <stationbanksection>) containing this Timing Point — O(1) in-memory lookup
                station_banks = get_timing_catalog().get("station_to_banks", {}).get(str(asset_id), [])

                elr = decode_code("kmregionmasterdesc", st["km_region_id"]) if st.get("km_region_id") else None
                st_km = st.get("km_value")
                is_st_extrapolated = False
                if not elr or st_km is None or st_km <= 0:
                    cur.execute(
                        """
                        SELECT n.km_region_id, n.km_value
                        FROM station_tracks s_trk
                        JOIN nodes n ON n.node_id = ANY(s_trk.node_ids)
                        WHERE s_trk.station_id = %s AND n.km_value > 0
                        ORDER BY n.km_value ASC
                        LIMIT 1
                        """,
                        (asset_id,),
                    )
                    found_st = cur.fetchone()
                    if found_st:
                        if not elr and found_st.get("km_region_id"):
                            elr = decode_code("kmregionmasterdesc", found_st["km_region_id"])
                        st_km = found_st.get("km_value")
                        is_st_extrapolated = True

                decoded = {
                    "Station ID": st["station_id"],
                    "Abbreviation (TIPLOC)": st["abbrev"],
                    "Long Name": st["longname"],
                    "CRS Code": st["crscode"] or "—",
                    "STANOX": st["stanox"] or "—",
                    "NLC (NALCO)": f"{st['nalco']}{st['nlccheckchr'] or ''}" if st["nalco"] else "—",
                    "UIC Station Code": f"{st['uiccode']}-{st['uicstationcode']}" if st["uicstationcode"] else "—",
                    "Timing Point Role (Station Type)": f"{decode_code('stationtypedesc', st['station_type'])} (ID {st['station_type']})",
                    "Planning Location Category": f"{decode_code('stationcategorydesc', st['station_category'])} (ID {st['station_category']})",
                    "Engineer's Line Ref (ELR)": f"{elr}{' (Extrapolated)' if is_st_extrapolated else ''}" if elr else str(st["km_region_id"] or "—"),
                    "Mileage / Kilometrage": f"{meters_to_miles_chains(st_km)}{' (Extrapolated)' if is_st_extrapolated else ''}" if st_km else "—",
                    "Standard Stopping Time": f"{st['std_stopping_time']} s",
                    "Standard Connection Time": f"{st['std_connection_time']} s",
                    "Compulsory Stop": st["compulsory_stop"],
                    "Timetable Corridors (Station Banks)": f"{len(station_banks)} corridor(s)" if station_banks else "None",
                    "Schematic Center (netx, nety)": f"({round(st['x'], 1)}, {round(st['y'], 1)})" if st["x"] is not None else "—",
                    "Total Tracks / Active Tracks": f"{st['track_count']} total / {st['active_track_count']} with geometry",
                }

                return {
                    "asset_type": "station",
                    "id": st["station_id"],
                    "title": f"{st['longname'] or st['abbrev']} ({st['crscode'] or st['abbrev']})",
                    "subtitle": f"{decode_code('stationtypedesc', st['station_type'])} • {decode_code('stationcategorydesc', st['station_category'])} • STANOX {st['stanox'] or 'N/A'}",
                    "x": st["x"],
                    "y": st["y"],
                    "bbox": [st["min_x"], st["min_y"], st["max_x"], st["max_y"]] if st["min_x"] is not None else None,
                    "decoded": decoded,
                    "related": {
                        "tracks": tracks,
                        "boundary_signals": boundaries,
                        "routes": routes,
                        "station_banks": station_banks,
                        "positions": st["positions"],
                        "nwr_cross_links": find_nwr_cross_links_for_tps(
                            cur,
                            elr,
                            st_km,
                            item_label=f"Station {st['longname'] or st['abbrev']}",
                            is_extrapolated=is_st_extrapolated,
                        ),
                    },
                    "highlight": {
                        "ways": highlight_ways,
                        "points": highlight_points,
                    },
                    "raw_attrs": st["raw_attrs"],
                    "raw_children": st["raw_children"],
                }

            elif asset_type == "edge":
                cur.execute("SELECT * FROM edges WHERE pk = %s", (asset_id,))
                ed = cur.fetchone()
                if not ed:
                    raise HTTPException(status_code=404, detail="Edge not found")

                cur.execute(
                    "SELECT * FROM nodes WHERE node_id IN (%s, %s)",
                    (ed["start_node_id"], ed["end_node_id"]),
                )
                node_map = {n["node_id"]: n for n in cur.fetchall()}
                n_start = node_map.get(ed["start_node_id"])
                n_end = node_map.get(ed["end_node_id"])

                # Decode velocity profiles
                vprofs = []
                for vp in ed["velocity_profiles"] or []:
                    pid = vp.get("profileid")
                    vprofs.append({
                        "profile_id": pid,
                        "profile_name": decode_code("veloprofiledesc", pid) or f"Profile {pid}",
                        "vmax": vp.get("vmax"),
                        "vmax_mph": round((vp.get("vmax") or 0) * 0.621371, 1),
                    })

                # Blocks containing both start_node_id and end_node_id
                cur.execute(
                    """
                    SELECT pk, block_id, pre_signal_distance, sight_distance, atc_flag, moving_block, coords
                    FROM blocks
                    WHERE node_ids @> ARRAY[%s::int, %s::int]
                    LIMIT 30
                    """,
                    (ed["start_node_id"], ed["end_node_id"]),
                )
                blocks = cur.fetchall()

                # Station tracks containing either node
                cur.execute(
                    """
                    SELECT st.pk, st.track_id, st.station_id, st.name, st.description,
                           st.track_category, s.longname AS station_name, s.abbrev AS station_abbrev,
                           st.coords
                    FROM station_tracks st
                    LEFT JOIN stations s ON s.station_id = st.station_id
                    WHERE st.node_ids && ARRAY[%s::int, %s::int]
                    LIMIT 20
                    """,
                    (ed["start_node_id"], ed["end_node_id"]),
                )
                tracks = cur.fetchall()

                # Routes containing both nodes
                cur.execute(
                    """
                    SELECT r.pk, r.route_id, r.station_id, r.rop_line_code_id, r.sa_line_code_id,
                           s.longname AS station_name
                    FROM routes r
                    LEFT JOIN stations s ON s.station_id = r.station_id
                    WHERE r.node_ids @> ARRAY[%s::int, %s::int]
                    LIMIT 30
                    """,
                    (ed["start_node_id"], ed["end_node_id"]),
                )
                routes = cur.fetchall()

                # Attribute sections containing both nodes
                cur.execute(
                    """
                    SELECT pk, id, section_id, km_region_id, short_description, description, section_attrs
                    FROM attribute_sections
                    WHERE node_ids @> ARRAY[%s::int, %s::int]
                    LIMIT 20
                    """,
                    (ed["start_node_id"], ed["end_node_id"]),
                )
                attr_secs = []
                for a in cur.fetchall():
                    decoded_sattrs = []
                    for sa in a["section_attrs"] or []:
                        sat_id = sa.get("sectionattributetypeid")
                        decoded_sattrs.append({
                            "type_id": sat_id,
                            "type_name": decode_code("sectionattributetypedesc", sat_id) or f"Type {sat_id}",
                            "value": sa.get("value"),
                            "km_begin": meters_to_miles_chains(int(sa["kmvaluebegin"])) if sa.get("kmvaluebegin") else None,
                            "km_end": meters_to_miles_chains(int(sa["kmvalueend"])) if sa.get("kmvalueend") else None,
                            "ascending": sa.get("ascending"),
                        })
                    attr_secs.append({
                        "pk": a["pk"],
                        "id": a["id"],
                        "section_id": a["section_id"],
                        "elr": decode_code("kmregionmasterdesc", a["km_region_id"]),
                        "short_description": a["short_description"],
                        "description": a["description"],
                        "attributes": decoded_sattrs,
                    })

                elr_start = decode_code("kmregionmasterdesc", n_start["km_region_id"]) if n_start else None
                decoded = {
                    "Edge PK / XML ID": f"{ed['pk']} / {ed['edge_id']}",
                    "Directed Nodes": f"Node #{ed['start_node_id']} → Node #{ed['end_node_id']}",
                    "Length": f"{ed['length']} m ({round(ed['length'] * 3.28084, 1)} ft)",
                    "Base Line Speed (Vmax)": f"{ed['vmax']} km/h ({round(ed['vmax'] * 0.621371, 1)} mph)",
                    "Gradient": f"{ed['gradient']} ‰ (1 in {round(1000.0 / abs(ed['gradient']), 0) if ed['gradient'] else '∞'})",
                    "Electrified": "Yes (Electrified)" if ed["electrified"] else "No (Non-Electrified)",
                    "Partial Release": ed["partial_release"],
                    "Approach Signal": ed["approach_signal"],
                    "Radius / Height Diff": f"Radius: {ed['radius']} m • Height Diff: {ed['height_difference']} m",
                    "Tunnel Area / Factor": f"{ed['tunnel_square_area']} m² / Factor {ed['tunnel_factor']}",
                    "GeoGIS Line Code": f"{decode_code('geogislinecodedesc', ed['geogis_line_code'])} ({ed['geogis_line_code']})",
                    "Start Node ELR & Mileage": f"{elr_start or ''} {meters_to_miles_chains(n_start['km_value']) if n_start else ''}",
                    "Validity Window": f"{ed['valid_from']} → {ed['valid_to']}",
                    "Last Modified": ed["last_modified"],
                }

                cx = (ed["x0"] + ed["x1"]) / 2.0 if ed["x0"] is not None else None
                cy = (ed["y0"] + ed["y1"]) / 2.0 if ed["y0"] is not None else None

                start_km = n_start["km_value"] if (n_start and n_start.get("km_value")) else None
                end_km = n_end["km_value"] if (n_end and n_end.get("km_value")) else None
                is_ed_extrapolated = False
                if start_km is not None and end_km is None and ed.get("length"):
                    end_km = start_km + ed["length"]
                    is_ed_extrapolated = True
                elif end_km is not None and start_km is None and ed.get("length"):
                    start_km = max(0, end_km - ed["length"])
                    is_ed_extrapolated = True

                edge_dir = None
                if start_km is not None and end_km is not None:
                    if start_km < end_km:
                        edge_dir = "down"
                    elif start_km > end_km:
                        edge_dir = "up"
                track_hint = tracks[0]["name"] if tracks else None

                nwr_cross_links = find_nwr_cross_links_for_tps(
                    cur,
                    elr_start or (decode_code("kmregionmasterdesc", n_end["km_region_id"]) if n_end else None),
                    start_km,
                    end_km_meters=end_km,
                    direction_hint=edge_dir,
                    track_name_hint=track_hint,
                    item_label=f"Edge #{ed['edge_id']}",
                    is_extrapolated=is_ed_extrapolated,
                )

                return {
                    "asset_type": "edge",
                    "id": ed["pk"],
                    "title": f"Track Edge #{ed['edge_id']} ({ed['start_node_id']} → {ed['end_node_id']})",
                    "subtitle": f"{ed['length']}m • Vmax {ed['vmax']} km/h • {'Electrified' if ed['electrified'] else 'Diesel'} • Grad {ed['gradient']}‰",
                    "x": cx,
                    "y": cy,
                    "bbox": [ed["min_x"], ed["min_y"], ed["max_x"], ed["max_y"]],
                    "decoded": decoded,
                    "related": {
                        "velocity_profiles": vprofs,
                        "start_node": n_start,
                        "end_node": n_end,
                        "station_tracks": tracks,
                        "blocks": blocks,
                        "routes": routes,
                        "attribute_sections": attr_secs,
                        "nwr_cross_links": nwr_cross_links,
                    },
                    "highlight": {
                        "ways": [
                            {
                                "label": f"Edge #{ed['edge_id']}",
                                "coords": [[ed["x0"], ed["y0"]], [ed["x1"], ed["y1"]]],
                                "color": "#f43f5e",
                            }
                        ] if ed["x0"] is not None else [],
                        "points": [
                            {"x": ed["x0"], "y": ed["y0"], "label": f"Start #{ed['start_node_id']}", "color": "#22c55e"},
                            {"x": ed["x1"], "y": ed["y1"], "label": f"End #{ed['end_node_id']}", "color": "#ef4444"},
                        ] if ed["x0"] is not None else [],
                    },
                    "raw_attrs": ed["raw_attrs"],
                    "raw_children": ed["raw_children"],
                }

            elif asset_type == "node":
                cur.execute("SELECT * FROM nodes WHERE node_id = %s", (asset_id,))
                nd = cur.fetchone()
                if not nd:
                    raise HTTPException(status_code=404, detail="Node not found")

                cur.execute(
                    """
                    SELECT pk, edge_id, start_node_id, end_node_id, length, vmax, gradient, electrified, x0, y0, x1, y1
                    FROM edges
                    WHERE start_node_id = %s OR end_node_id = %s
                    """,
                    (asset_id, asset_id),
                )
                conn_edges = cur.fetchall()

                cur.execute(
                    """
                    SELECT pk, id, signal_id, name, master_name, interlocking_sys_id, bumper
                    FROM signals
                    WHERE start_node_id = %s OR end_node_id = %s OR point_node_id = %s
                    """,
                    (asset_id, asset_id, asset_id),
                )
                conn_signals = cur.fetchall()

                cur.execute(
                    """
                    SELECT st.pk, st.track_id, st.station_id, st.name, st.description, s.longname AS station_name
                    FROM station_tracks st
                    LEFT JOIN stations s ON s.station_id = st.station_id
                    WHERE %s = ANY(st.node_ids)
                    """,
                    (asset_id,),
                )
                conn_tracks = cur.fetchall()

                cur.execute(
                    """
                    SELECT pk, block_id, pre_signal_distance, sight_distance
                    FROM blocks
                    WHERE %s = ANY(node_ids)
                    LIMIT 30
                    """,
                    (asset_id,),
                )
                conn_blocks = cur.fetchall()

                elr1 = decode_code("kmregionmasterdesc", nd["km_region_id"])
                elr2 = decode_code("kmregionmasterdesc", nd["second_km_region_id"])
                decoded = {
                    "Node ID / Business Number": f"{nd['node_id']} / {nd['business_number']}",
                    "Line ID": nd["line_id"],
                    "Schematic Coordinates (netx, nety, netz)": f"({nd['netx']}, {nd['nety']}, {nd['netz']})",
                    "Line Coordinates (linex, liney, linez)": f"({nd['linex']}, {nd['liney']}, {nd['linez']})",
                    "Angle": f"{nd['angle']}°",
                    "Primary ELR (kmregionid)": f"{elr1} (ID {nd['km_region_id']})" if elr1 else str(nd["km_region_id"]),
                    "Primary Kilometrage / Mileage": meters_to_miles_chains(nd["km_value"]) or "—",
                    "Secondary ELR (secondkmregionid)": f"{elr2} (ID {nd['second_km_region_id']})" if elr2 else str(nd["second_km_region_id"]),
                    "Secondary Kilometrage / Mileage": meters_to_miles_chains(nd["second_km_value"]) if nd["second_km_value"] else "—",
                    "Validity Window": f"{nd['valid_from']} → {nd['valid_to']}",
                    "Last Modified": nd["last_modified"],
                }

                nd_km = nd.get("km_value")
                is_nd_extrapolated = False
                if not elr1 or nd_km is None or nd_km <= 0:
                    cur.execute(
                        """
                        SELECT e.length, e.start_node_id, n.km_region_id, n.km_value
                        FROM edges e
                        JOIN nodes n ON (n.node_id = CASE WHEN e.start_node_id = %s THEN e.end_node_id ELSE e.start_node_id END)
                        WHERE (e.start_node_id = %s OR e.end_node_id = %s)
                          AND n.km_value > 0
                        LIMIT 1
                        """,
                        (nd["node_id"], nd["node_id"], nd["node_id"]),
                    )
                    adj_nd = cur.fetchone()
                    if adj_nd:
                        if not elr1 and adj_nd.get("km_region_id"):
                            elr1 = decode_code("kmregionmasterdesc", adj_nd["km_region_id"])
                        if adj_nd["start_node_id"] == nd["node_id"]:
                            nd_km = max(0, adj_nd["km_value"] - (adj_nd["length"] or 0))
                        else:
                            nd_km = adj_nd["km_value"] + (adj_nd["length"] or 0)
                        is_nd_extrapolated = True

                nwr_cross_links = find_nwr_cross_links_for_tps(
                    cur,
                    elr1,
                    nd_km,
                    tps_valency=len(conn_edges),
                    item_label=f"Node #{nd['node_id']}",
                    is_extrapolated=is_nd_extrapolated,
                )

                return {
                    "asset_type": "node",
                    "id": nd["node_id"],
                    "title": f"Track Node #{nd['node_id']}",
                    "subtitle": f"ELR {elr1 or 'N/A'} • {meters_to_miles_chains(nd['km_value']) or ''} • ({nd['netx']}, {nd['nety']})",
                    "x": nd["netx"],
                    "y": nd["nety"],
                    "decoded": decoded,
                    "related": {
                        "connected_edges": conn_edges,
                        "signals_at_node": conn_signals,
                        "station_tracks": conn_tracks,
                        "blocks": conn_blocks,
                        "nwr_cross_links": nwr_cross_links,
                    },
                    "highlight": {
                        "ways": [
                            {
                                "label": f"Edge #{e['edge_id']}",
                                "coords": [[e["x0"], e["y0"]], [e["x1"], e["y1"]]],
                                "color": "#38bdf8",
                            }
                            for e in conn_edges if e["x0"] is not None
                        ],
                        "points": [
                            {"x": nd["netx"], "y": nd["nety"], "label": f"Node #{nd['node_id']}", "color": "#f43f5e"}
                        ],
                    },
                    "raw_attrs": nd["raw_attrs"],
                    "raw_children": nd["raw_children"],
                }

            elif asset_type == "signal":
                cur.execute("SELECT * FROM signals WHERE pk = %s", (asset_id,))
                sig = cur.fetchone()
                if not sig:
                    raise HTTPException(status_code=404, detail="Signal not found")

                # Interlocking system details
                ilock = lookup.get("interlockingsystem", {}).get(str(sig["interlocking_sys_id"]), {})
                ilock_attrs = ilock.get("raw_attrs", {})
                base_sig_type = decode_code("basesignaltypedesc", ilock_attrs.get("basesignaltypeid"))
                ilock_name = ilock.get("display") or str(sig["interlocking_sys_id"])
                aspects_cnt = int(ilock_attrs.get("aspects") or 0)
                set_time = int(ilock_attrs.get("settime") or 0)
                freeing_time = int(ilock_attrs.get("freeingtime") or 0)

                # Resolve signal node ELR & mileage + approach edge
                anchor_nid = sig["end_node_id"] or sig["point_node_id"] or sig["start_node_id"]
                sig_elr = decode_code("kmregionmasterdesc", sig["km_region_id"]) if sig["km_region_id"] else None
                sig_km_val = None
                if anchor_nid:
                    cur.execute(
                        "SELECT node_id, netx, nety, km_region_id, km_value, angle FROM nodes WHERE node_id = %s",
                        (anchor_nid,),
                    )
                    nd_row = cur.fetchone()
                    if nd_row:
                        if not sig_elr and nd_row["km_region_id"]:
                            sig_elr = decode_code("kmregionmasterdesc", nd_row["km_region_id"])
                        sig_km_val = nd_row["km_value"]

                approach_edge = None
                if sig["start_node_id"] and sig["end_node_id"]:
                    cur.execute(
                        """
                        SELECT pk, edge_id, start_node_id, end_node_id, length, vmax, gradient, electrified, x0, y0, x1, y1
                        FROM edges
                        WHERE start_node_id = %s AND end_node_id = %s
                        LIMIT 1
                        """,
                        (sig["start_node_id"], sig["end_node_id"]),
                    )
                    approach_edge = cur.fetchone()

                highlight_ways = []
                highlight_points = []
                if sig["x"] is not None:
                    highlight_points.append({
                        "x": sig["x"],
                        "y": sig["y"],
                        "label": f"🚦 {sig['name'] or sig['master_name']}",
                        "color": "#f59e0b",
                        "group": "signal",
                        "shape": "signal",
                    })

                # 1. Resolve Approach Blocks & Protected Outgoing Blocks connected at signal nodes
                approach_blocks = []
                outgoing_blocks = []
                sig_nids = set(nid for nid in (sig.get("end_node_id"), sig.get("start_node_id"), sig.get("point_node_id")) if nid is not None)
                if sig_nids:
                    cur.execute(
                        """
                        SELECT pk, block_id, line_id, pre_signal_distance, sight_distance,
                               priority, atc_length, atc_flag, moving_block, shunting_flag,
                               additional_aspects, node_ids, coords
                        FROM blocks
                        WHERE node_ids && %s::integer[]
                        ORDER BY shunting_flag ASC, block_id ASC
                        LIMIT 50
                        """,
                        (list(sig_nids),),
                    )
                    cand_blocks = cur.fetchall()
                    for b in cand_blocks:
                        nids = b["node_ids"] or []
                        if not nids:
                            continue
                        # Approach block terminating at this signal: ends at one of the signal nodes
                        is_approach = nids[-1] in sig_nids and nids[0] not in sig_nids
                        # Outgoing block governed by this signal: starts at one of the signal nodes
                        is_outgoing = nids[0] in sig_nids and nids[-1] not in sig_nids

                        if not is_approach and not is_outgoing:
                            continue

                        p_info = resolve_node_path_details(cur, nids)
                        b_coords = p_info["coords"] or b["coords"] or []
                        b_len = p_info["length_meters"]

                        if is_approach and len(approach_blocks) < 12:
                            sight_pt = interpolate_from_end(b_coords, p_info["edge_lengths"], b["sight_distance"])
                            presig_pt = interpolate_from_end(b_coords, p_info["edge_lengths"], b["pre_signal_distance"])
                            # Find entry signal(s) at nids[0]
                            entry_sigs = []
                            cur.execute(
                                """
                                SELECT pk, id, signal_id, name, master_name, interlocking_sys_id, start_node_id, end_node_id, x, y
                                FROM signals
                                WHERE end_node_id = %s OR point_node_id = %s
                                LIMIT 6
                                """,
                                (nids[0], nids[0]),
                            )
                            for es in cur.fetchall():
                                if len(nids) >= 2 and es["start_node_id"] == nids[1]:
                                    continue
                                entry_sigs.append({
                                    "pk": es["pk"],
                                    "signal_id": es["signal_id"],
                                    "name": es["name"] or es["master_name"],
                                    "interlocking_desc": decode_code("interlockingsystem", es["interlocking_sys_id"]),
                                    "x": es["x"],
                                    "y": es["y"],
                                })
                                if es["x"] is not None:
                                    highlight_points.append({
                                        "x": es["x"],
                                        "y": es["y"],
                                        "label": f"Rear: {es['name'] or es['master_name']}",
                                        "color": "#38bdf8",
                                        "group": "approach",
                                        "block_id": b["block_id"],
                                    })

                            if sight_pt:
                                highlight_points.append({
                                    "x": sight_pt[0],
                                    "y": sight_pt[1],
                                    "label": f"Sighting ({b['sight_distance']}m)",
                                    "color": "#fde047",
                                    "group": "approach",
                                    "shape": "diamond",
                                    "block_id": b["block_id"],
                                })

                            if presig_pt:
                                highlight_points.append({
                                    "x": presig_pt[0],
                                    "y": presig_pt[1],
                                    "label": f"AWS ({b['pre_signal_distance']}m)",
                                    "color": "#38bdf8",
                                    "group": "approach",
                                    "shape": "diamond",
                                    "block_id": b["block_id"],
                                })

                            if b_coords:
                                highlight_ways.append({
                                    "label": f"Approach Block #{b['block_id']} ({b_len}m)",
                                    "coords": b_coords,
                                    "color": "#38bdf8",
                                    "group": "approach",
                                    "block_id": b["block_id"],
                                    "width": 4.0,
                                })

                            rear_names = [es["name"] or es["master_name"] for es in entry_sigs if (es.get("name") or es.get("master_name"))]
                            rear_str = "/".join(rear_names) if rear_names else (f"Node #{nids[0]}" if nids else "Rear")
                            cur_sig_str = sig["name"] or sig["master_name"] or f"Signal #{sig['signal_id']}"
                            route_label = f"From {rear_str} to {cur_sig_str}"

                            approach_blocks.append({
                                "pk": b["pk"],
                                "block_id": b["block_id"],
                                "length_meters": b_len,
                                "pre_signal_distance": b["pre_signal_distance"],
                                "sight_distance": b["sight_distance"],
                                "shunting_flag": b["shunting_flag"],
                                "atc_flag": b["atc_flag"],
                                "moving_block": b["moving_block"],
                                "min_vmax": p_info["min_vmax"],
                                "max_vmax": p_info["max_vmax"],
                                "node_count": len(nids),
                                "turnout_count": len(p_info["turnout_nodes"]),
                                "sighting_point": sight_pt,
                                "pre_signal_point": presig_pt,
                                "entry_signals": entry_sigs,
                                "coords": b_coords,
                                "route_label": route_label,
                            })

                        if is_outgoing and len(outgoing_blocks) < 15:
                            # Find exit signal(s) at end of block
                            exit_sigs = []
                            if len(nids) >= 2:
                                cur.execute(
                                    """
                                    SELECT pk, id, signal_id, name, master_name, interlocking_sys_id, start_node_id, end_node_id, x, y
                                    FROM signals
                                    WHERE (start_node_id = %s AND end_node_id = %s)
                                       OR (end_node_id = %s)
                                       OR (point_node_id = %s)
                                    LIMIT 6
                                    """,
                                    (nids[-2], nids[-1], nids[-1], nids[-1]),
                                )
                                for xs in cur.fetchall():
                                    exit_sigs.append({
                                        "pk": xs["pk"],
                                        "signal_id": xs["signal_id"],
                                        "name": xs["name"] or xs["master_name"],
                                        "interlocking_desc": decode_code("interlockingsystem", xs["interlocking_sys_id"]),
                                        "x": xs["x"],
                                        "y": xs["y"],
                                    })
                                    if xs["x"] is not None:
                                        highlight_points.append({
                                            "x": xs["x"],
                                            "y": xs["y"],
                                            "label": f"Next: {xs['name'] or xs['master_name']}",
                                            "color": "#10b981",
                                            "group": "outgoing",
                                            "block_id": b["block_id"],
                                        })

                            if b_coords:
                                highlight_ways.append({
                                    "label": f"Protected Block #{b['block_id']} ({b_len}m)",
                                    "coords": b_coords,
                                    "color": "#10b981" if not b["shunting_flag"] else "#34d399",
                                    "group": "outgoing",
                                    "block_id": b["block_id"],
                                    "width": 5.0,
                                })

                            next_names = [xs["name"] or xs["master_name"] for xs in exit_sigs if (xs.get("name") or xs.get("master_name"))]
                            next_str = "/".join(next_names) if next_names else (f"Buffer/Node #{nids[-1]}" if nids else "Advance")
                            cur_sig_str = sig["name"] or sig["master_name"] or f"Signal #{sig['signal_id']}"
                            route_label = f"From {cur_sig_str} to {next_str}"

                            outgoing_blocks.append({
                                "pk": b["pk"],
                                "block_id": b["block_id"],
                                "length_meters": b_len,
                                "pre_signal_distance": b["pre_signal_distance"],
                                "sight_distance": b["sight_distance"],
                                "shunting_flag": b["shunting_flag"],
                                "atc_flag": b["atc_flag"],
                                "moving_block": b["moving_block"],
                                "min_vmax": p_info["min_vmax"],
                                "max_vmax": p_info["max_vmax"],
                                "node_count": len(nids),
                                "turnout_count": len(p_info["turnout_nodes"]),
                                "turnout_nodes": p_info["turnout_nodes"],
                                "exit_signals": exit_sigs,
                                "coords": b_coords,
                                "route_label": route_label,
                            })

                # 2. Resolve Security Section Overlaps & Flank Protection Paths
                overlap_colors = ["#d946ef", "#a855f7", "#ec4899", "#f43f5e", "#c084fc"]
                enriched_overlaps = []
                for idx, ss in enumerate(sig["security_sections"] or []):
                    raw_nodes = ss.get("nodes", [])
                    ss_nids = [n["nodeid"] for n in raw_nodes if n.get("nodeid") is not None]
                    full_ov_nids = ([anchor_nid] if anchor_nid else []) + ss_nids
                    ov_info = resolve_node_path_details(cur, full_ov_nids)
                    ov_coords = ov_info["coords"]
                    ov_len_m = ov_info["length_meters"]
                    ov_len_yd = round(ov_len_m / 0.9144, 1)
                    ov_col = overlap_colors[idx % len(overlap_colors)]
                    grp_name = f"overlap_{idx + 1}"

                    # Annotate individual nodes with coordinates & flank/turnout roles
                    nmap = ov_info["node_map"]
                    turnout_set = {t["node_id"] for t in ov_info["turnout_nodes"]}
                    boundary_nodes = []
                    for n in raw_nodes:
                        nid = n.get("nodeid")
                        if nid in nmap:
                            n["x"] = nmap[nid]["netx"]
                            n["y"] = nmap[nid]["nety"]
                            n["is_turnout"] = nid in turnout_set
                            if n.get("boundarynode"):
                                boundary_nodes.append({
                                    "node_id": nid,
                                    "x": n["x"],
                                    "y": n["y"],
                                })
                                highlight_points.append({
                                    "x": n["x"],
                                    "y": n["y"],
                                    "label": f"Flank Boundary #{nid}",
                                    "color": "#f43f5e",
                                    "group": grp_name,
                                    "shape": "diamond",
                                })

                    # Mark turnout switches inside overlap
                    for tn in ov_info["turnout_nodes"]:
                        if tn["node_id"] != anchor_nid:
                            highlight_points.append({
                                "x": tn["x"],
                                "y": tn["y"],
                                "label": f"Overlap Points #{tn['node_id']}",
                                "color": ov_col,
                                "group": grp_name,
                                "shape": "diamond",
                            })

                    # Mark terminal overlap clearing point
                    if ss_nids and ss_nids[-1] in nmap:
                        end_n = nmap[ss_nids[-1]]
                        highlight_points.append({
                            "x": end_n["netx"],
                            "y": end_n["nety"],
                            "label": f"Overlap #{idx + 1} End ({ov_len_m}m / {ov_len_yd}yd)",
                            "color": ov_col,
                            "group": grp_name,
                        })

                    if len(ov_coords) >= 2:
                        highlight_ways.append({
                            "label": f"Overlap #{idx + 1} ({ov_len_m}m @ {ss.get('vmax', 0)} km/h)",
                            "coords": ov_coords,
                            "color": ov_col,
                            "group": grp_name,
                            "dash": [7, 4],
                            "width": 5.5,
                        })

                    enriched_overlaps.append({
                        "index": idx + 1,
                        "vmax": ss.get("vmax", 0),
                        "vmax_mph": round((ss.get("vmax") or 0) * 0.621371, 1),
                        "length_meters": ov_len_m,
                        "length_yards": ov_len_yd,
                        "blockoccupying": ss.get("blockoccupying", False),
                        "accelerationattail": ss.get("accelerationattail", False),
                        "nodes": raw_nodes,
                        "full_node_ids": full_ov_nids,
                        "turnout_nodes": ov_info["turnout_nodes"],
                        "boundary_nodes": boundary_nodes,
                        "coords": ov_coords,
                        "color": ov_col,
                        "group": grp_name,
                    })

                # 3. Station boundaries referencing this signal_id
                cur.execute(
                    """
                    SELECT sb.boundary_type_id, s.station_id, s.longname, s.abbrev, s.crscode
                    FROM station_boundaries sb
                    JOIN stations s ON s.station_id = ANY(sb.station_ids)
                    WHERE sb.signal_id = %s
                    """,
                    (sig["signal_id"],),
                )
                prot_stations = []
                for ps in cur.fetchall():
                    prot_stations.append({
                        **ps,
                        "boundary_type_desc": decode_code("stationboundarytypedesc", ps["boundary_type_id"]) or f"Type {ps['boundary_type_id']}",
                    })

                # 4. NWR Track Model Cross-Links (OSGB36)
                is_sig_extrapolated = False
                if not sig_elr or sig_km_val is None or sig_km_val <= 0:
                    for alt_nid in (sig.get("start_node_id"), sig.get("end_node_id"), sig.get("point_node_id")):
                        if alt_nid:
                            cur.execute(
                                "SELECT km_region_id, km_value FROM nodes WHERE node_id = %s AND km_value > 0",
                                (alt_nid,),
                            )
                            alt_nd = cur.fetchone()
                            if alt_nd:
                                if not sig_elr and alt_nd.get("km_region_id"):
                                    sig_elr = decode_code("kmregionmasterdesc", alt_nd["km_region_id"])
                                sig_km_val = alt_nd["km_value"]
                                is_sig_extrapolated = True
                                break

                sig_dir = None
                sig_name_str = (sig["name"] or sig["master_name"] or "").strip().upper()
                if sig_name_str.startswith("DN") or " DOWN " in f" {sig_name_str} " or sig_name_str.startswith("D"):
                    sig_dir = "down"
                elif sig_name_str.startswith("UP") or " UP " in f" {sig_name_str} " or sig_name_str.startswith("U"):
                    sig_dir = "up"
                elif approach_edge and approach_edge.get("start_node_id") and approach_edge.get("end_node_id"):
                    cur.execute("SELECT km_value FROM nodes WHERE node_id = %s", (approach_edge["start_node_id"],))
                    n_a = cur.fetchone()
                    cur.execute("SELECT km_value FROM nodes WHERE node_id = %s", (approach_edge["end_node_id"],))
                    n_b = cur.fetchone()
                    if n_a and n_b and n_a.get("km_value") and n_b.get("km_value"):
                        sig_dir = "down" if n_a["km_value"] < n_b["km_value"] else "up"

                nwr_cross_links = find_nwr_cross_links_for_tps(
                    cur,
                    sig_elr,
                    sig_km_val,
                    direction_hint=sig_dir,
                    item_label=f"Signal {sig['name'] or sig['master_name']}",
                    is_extrapolated=is_sig_extrapolated,
                )

                ov_summary = (
                    f"{len(enriched_overlaps)} overlap(s): "
                    + ", ".join(f"#{o['index']} ({o['length_meters']}m / {o['length_yards']}yd @ {o['vmax']} km/h)" for o in enriched_overlaps)
                    if enriched_overlaps
                    else "None defined"
                )

                decoded = {
                    "Signal PK / XML ID": f"{sig['pk']} / {sig['id']}",
                    "Master Signal ID & Name": f"#{sig['signal_id']} — {sig['master_name']}",
                    "Signal Plate Name": sig["name"] or sig["master_name"],
                    "Interlocking System Class": f"{ilock_name} (ID {sig['interlocking_sys_id']})",
                    "Base Signal Type & Aspects": f"{base_sig_type or '—'} ({aspects_cnt} aspects)" if aspects_cnt else (base_sig_type or "—"),
                    "Interlocking Route Set / Release Time": f"Set: {set_time} s • Release/Freeing: {freeing_time} s (Release Mode: {ilock_attrs.get('releasemode', '—')})",
                    "Engineer's Line Ref (ELR) & Mileage": f"{sig_elr or '—'} • {meters_to_miles_chains(sig_km_val) or '—'}",
                    "Directed Signal Edge": (
                        f"#{sig['start_node_id']} → #{sig['end_node_id']}"
                        + (f" (Edge #{approach_edge['edge_id']}, {approach_edge['length']}m, Vmax {approach_edge['vmax']} km/h)" if approach_edge else "")
                        if sig["start_node_id"]
                        else f"Point Node #{sig['point_node_id']}"
                    ),
                    "Approach Blocks (In Rear)": f"{len(approach_blocks)} block(s) terminating at signal",
                    "Protected Blocks (In Advance)": f"{len(outgoing_blocks)} block(s) governed by signal",
                    "Safety Overlaps (Security Sections)": ov_summary,
                    "Overlap Release Timer": f"{sig['sec_sect_freeing_time']} s (Timed Release Active: {sig['use_sec_sect_freeing_time']})",
                    "Buffer Stop / Bumper": "Yes (Bumper / Buffer Stop)" if sig["bumper"] else "No",
                    "Temporarily Closed": sig["tmp_closed"],
                    "Schematic Coordinates (x, y)": f"({sig['x']}, {sig['y']})",
                    "Validity Window": f"{sig['valid_from']} → {sig['valid_to']}",
                }

                return {
                    "asset_type": "signal",
                    "id": sig["pk"],
                    "title": f"Signal {sig['name'] or sig['master_name']}",
                    "subtitle": f"{ilock_name} • { sig_elr or base_sig_type or '' } • {len(outgoing_blocks)} Protected Block(s) • {len(enriched_overlaps)} Overlap(s)",
                    "x": sig["x"],
                    "y": sig["y"],
                    "decoded": decoded,
                    "related": {
                        "interlocking_system": {
                            **ilock_attrs,
                            "display_name": ilock_name,
                            "base_signal_type": base_sig_type,
                            "elr": sig_elr,
                            "km_str": meters_to_miles_chains(sig_km_val),
                        },
                        "approach_edge": approach_edge,
                        "approach_blocks": approach_blocks,
                        "outgoing_blocks": outgoing_blocks,
                        "security_sections": enriched_overlaps,
                        "protected_stations": prot_stations,
                        "nwr_cross_links": nwr_cross_links,
                    },
                    "highlight": {
                        "ways": highlight_ways,
                        "points": highlight_points,
                    },
                    "raw_attrs": sig["raw_attrs"],
                    "raw_children": sig["raw_children"],
                }

            elif asset_type == "track":
                cur.execute(
                    """
                    SELECT st.*, s.longname AS station_name, s.abbrev AS station_abbrev, s.crscode
                    FROM station_tracks st
                    LEFT JOIN stations s ON s.station_id = st.station_id
                    WHERE st.pk = %s
                    """,
                    (asset_id,),
                )
                trk = cur.fetchone()
                if not trk:
                    raise HTTPException(status_code=404, detail="Track not found")

                decoded = {
                    "Track PK / Track ID": f"{trk['pk']} / {trk['track_id']}",
                    "Station": f"{trk['station_name']} ({trk['station_abbrev']})",
                    "Track Name / Long Name": f"{trk['name']} / {trk['longname']}",
                    "Description": trk["description"],
                    "Track Category": f"{decode_code('trackcategorydesc', trk['track_category'])} (ID {trk['track_category']})",
                    "Effective Length": f"{trk['effective_length']} m",
                    "Rules of the Plan (ROP) Line Code": f"{decode_code('roplinecodedesc', trk['rop_line_code'])} (ID {trk['rop_line_code']})",
                    "Sectional Appendix (SA) Line Code": f"{decode_code('salinecodedesc', trk['sa_line_code'])} (ID {trk['sa_line_code']})",
                    "Business Reference ID": trk["business_reference_id"] or "—",
                    "Permissive Working": trk["permissive_working"],
                    "Directed": trk["directed"],
                    "Temporarily Closed": trk["track_tmp_closed"],
                    "Validity Window": f"{trk['valid_from']} → {trk['valid_to']}",
                    "Way Nodes Count": len(trk["node_ids"] or []),
                }

                cx = (trk["min_x"] + trk["max_x"]) / 2.0 if trk["min_x"] is not None else None
                cy = (trk["min_y"] + trk["max_y"]) / 2.0 if trk["min_y"] is not None else None

                trk_blocks = []
                trk_routes = []
                if trk["node_ids"]:
                    cur.execute(
                        """
                        SELECT pk, block_id, pre_signal_distance, sight_distance, shunting_flag, atc_flag
                        FROM blocks
                        WHERE node_ids && %s::int[]
                        ORDER BY shunting_flag ASC, block_id ASC
                        LIMIT 25
                        """,
                        (trk["node_ids"],),
                    )
                    trk_blocks = cur.fetchall()
                    cur.execute(
                        """
                        SELECT pk, route_id, station_id, rop_line_code_id, sa_line_code_id
                        FROM routes
                        WHERE node_ids && %s::int[]
                        ORDER BY route_id ASC
                        LIMIT 25
                        """,
                        (trk["node_ids"],),
                    )
                    trk_routes = cur.fetchall()

                trk_elr = None
                trk_km = None
                trk_end_km = None
                if trk.get("node_ids"):
                    cur.execute(
                        "SELECT km_region_id, km_value FROM nodes WHERE node_id = ANY(%s) AND km_value IS NOT NULL ORDER BY km_value ASC",
                        (trk["node_ids"],),
                    )
                    t_nodes = cur.fetchall()
                    if t_nodes:
                        if t_nodes[0].get("km_region_id"):
                            trk_elr = decode_code("kmregionmasterdesc", t_nodes[0]["km_region_id"])
                        trk_km = t_nodes[0].get("km_value")
                        if len(t_nodes) > 1:
                            trk_end_km = t_nodes[-1].get("km_value")
                nwr_cross_links = find_nwr_cross_links_for_tps(
                    cur,
                    trk_elr,
                    trk_km,
                    end_km_meters=trk_end_km,
                    track_name_hint=trk.get("name"),
                    item_label=f"Track {trk['name']}",
                )

                return {
                    "asset_type": "track",
                    "id": trk["pk"],
                    "title": f"{trk['station_name']} — Track {trk['name']} ({trk['description']})",
                    "subtitle": f"{decode_code('trackcategorydesc', trk['track_category'])} • {len(trk['node_ids'] or [])} nodes",
                    "x": cx,
                    "y": cy,
                    "bbox": [trk["min_x"], trk["min_y"], trk["max_x"], trk["max_y"]],
                    "decoded": decoded,
                    "related": {
                        "blocks": trk_blocks,
                        "routes": trk_routes,
                        "node_ids": trk["node_ids"],
                        "station_id": trk["station_id"],
                        "nwr_cross_links": nwr_cross_links,
                    },
                    "highlight": {
                        "ways": [
                            {
                                "label": f"Track {trk['name']}",
                                "coords": trk["coords"],
                                "color": "#38bdf8",
                            }
                        ] if trk["coords"] else [],
                        "points": [],
                    },
                    "raw_attrs": trk["raw_attrs"],
                    "raw_children": trk["raw_children"],
                }

            elif asset_type == "block":
                cur.execute("SELECT * FROM blocks WHERE pk = %s", (asset_id,))
                blk = cur.fetchone()
                if not blk:
                    raise HTTPException(status_code=404, detail="Block not found")

                nids = blk["node_ids"] or []
                p_info = resolve_node_path_details(cur, nids)
                b_coords = p_info["coords"] or blk["coords"] or []
                b_len = p_info["length_meters"]

                sight_pt = interpolate_from_end(b_coords, p_info["edge_lengths"], blk["sight_distance"])
                presig_pt = interpolate_from_end(b_coords, p_info["edge_lengths"], blk["pre_signal_distance"])

                highlight_ways = []
                highlight_points = []
                if b_coords:
                    highlight_ways.append({
                        "label": f"Block #{blk['block_id']} ({b_len}m)",
                        "coords": b_coords,
                        "color": "#10b981",
                        "group": "outgoing",
                        "width": 5.0,
                    })

                if sight_pt:
                    highlight_points.append({
                        "x": sight_pt[0],
                        "y": sight_pt[1],
                        "label": f"Sighting ({blk['sight_distance']}m)",
                        "color": "#fde047",
                        "group": "outgoing",
                        "shape": "diamond",
                    })
                if presig_pt:
                    highlight_points.append({
                        "x": presig_pt[0],
                        "y": presig_pt[1],
                        "label": f"Pre-Signal ({blk['pre_signal_distance']}m)",
                        "color": "#38bdf8",
                        "group": "outgoing",
                        "shape": "diamond",
                    })

                # Entry signal(s) at nids[0]
                entry_sigs = []
                if nids:
                    cur.execute(
                        """
                        SELECT pk, id, signal_id, name, master_name, interlocking_sys_id, start_node_id, end_node_id, x, y
                        FROM signals
                        WHERE end_node_id = %s
                        LIMIT 6
                        """,
                        (nids[0],),
                    )
                    for es in cur.fetchall():
                        if len(nids) >= 2 and es["start_node_id"] == nids[1]:
                            continue
                        entry_sigs.append({
                            "pk": es["pk"],
                            "signal_id": es["signal_id"],
                            "name": es["name"] or es["master_name"],
                            "interlocking_desc": decode_code("interlockingsystem", es["interlocking_sys_id"]),
                            "x": es["x"],
                            "y": es["y"],
                        })
                        if es["x"] is not None:
                            highlight_points.append({
                                "x": es["x"],
                                "y": es["y"],
                                "label": f"Entry: {es['name'] or es['master_name']}",
                                "color": "#38bdf8",
                                "group": "approach",
                            })

                # Exit signal(s) at nids[-2] -> nids[-1] + their safety overlaps!
                exit_sigs = []
                exit_overlaps = []
                if len(nids) >= 2:
                    cur.execute(
                        """
                        SELECT pk, id, signal_id, name, master_name, interlocking_sys_id, start_node_id, end_node_id, x, y, security_sections
                        FROM signals
                        WHERE start_node_id = %s AND end_node_id = %s
                        LIMIT 5
                        """,
                        (nids[-2], nids[-1]),
                    )
                    for xs in cur.fetchall():
                        exit_sigs.append({
                            "pk": xs["pk"],
                            "signal_id": xs["signal_id"],
                            "name": xs["name"] or xs["master_name"],
                            "interlocking_desc": decode_code("interlockingsystem", xs["interlocking_sys_id"]),
                            "x": xs["x"],
                            "y": xs["y"],
                        })
                        if xs["x"] is not None:
                            highlight_points.append({
                                "x": xs["x"],
                                "y": xs["y"],
                                "label": f"Exit: {xs['name'] or xs['master_name']}",
                                "color": "#f59e0b",
                                "group": "signal",
                            })
                        for idx, ss in enumerate(xs["security_sections"] or []):
                            ss_nids = [n["nodeid"] for n in ss.get("nodes", []) if n.get("nodeid") is not None]
                            full_ov = [xs["end_node_id"]] + ss_nids
                            ov_p = resolve_node_path_details(cur, full_ov)
                            if len(ov_p["coords"]) >= 2:
                                highlight_ways.append({
                                    "label": f"Exit Signal Overlap #{idx + 1} ({ov_p['length_meters']}m)",
                                    "coords": ov_p["coords"],
                                    "color": "#d946ef",
                                    "group": f"overlap_{idx + 1}",
                                    "dash": [7, 4],
                                    "width": 5.0,
                                })
                                exit_overlaps.append({
                                    "index": idx + 1,
                                    "signal_name": xs["name"] or xs["master_name"],
                                    "length_meters": ov_p["length_meters"],
                                    "length_yards": round(ov_p["length_meters"] / 0.9144, 1),
                                    "vmax": ss.get("vmax", 0),
                                    "node_count": len(ss_nids),
                                })

                for tn in p_info["turnout_nodes"]:
                    highlight_points.append({
                        "x": tn["x"],
                        "y": tn["y"],
                        "label": f"Switch Node #{tn['node_id']}",
                        "color": "#a855f7",
                        "group": "outgoing",
                        "shape": "diamond",
                    })

                decoded = {
                    "Block PK / Block ID": f"{blk['pk']} / {blk['block_id']}",
                    "Business Reference ID": blk["business_reference_id"] or "—",
                    "Block Type": "Shunting Block Route" if blk["shunting_flag"] else ("Moving Block (ETCS)" if blk["moving_block"] else "Main Running Block Section"),
                    "Calculated Block Length": f"{b_len} m ({round(b_len / 0.9144, 1)} yd)",
                    "Entry Signal(s)": ", ".join(s["name"] for s in entry_sigs) or f"Node #{nids[0] if nids else '—'}",
                    "Exit Signal(s)": ", ".join(s["name"] for s in exit_sigs) or f"Node #{nids[-1] if nids else '—'}",
                    "Exit Signal Overlaps": f"{len(exit_overlaps)} overlap path(s) beyond exit signal",
                    "Pre-Signal Distance (AWS/Distant)": f"{blk['pre_signal_distance']} m",
                    "Signal Sighting Distance": f"{blk['sight_distance']} m",
                    "Speed Envelope (Vmax)": f"{p_info['min_vmax']}–{p_info['max_vmax']} km/h" if p_info["min_vmax"] else "—",
                    "Switches / Turnouts Traversed": f"{len(p_info['turnout_nodes'])} switch node(s)",
                    "Priority": blk["priority"],
                    "ATC Flag / Length": f"{blk['atc_flag']} ({blk['atc_length']} m)",
                    "Moving Block / Shunting Flag": f"Moving: {blk['moving_block']} • Shunting: {blk['shunting_flag']}",
                    "Additional Aspects": blk["additional_aspects"],
                    "Node Path Length": f"{len(nids)} nodes (#{nids[0]} → #{nids[-1]})" if nids else "0 nodes",
                    "Validity Window": f"{blk['valid_from']} → {blk['valid_to']}",
                }
                cx = (blk["min_x"] + blk["max_x"]) / 2.0 if blk["min_x"] is not None else None
                cy = (blk["min_y"] + blk["max_y"]) / 2.0 if blk["min_y"] is not None else None

                blk_elr = None
                blk_km = None
                blk_end_km = None
                blk_dir = None
                is_blk_extrapolated = False
                if nids:
                    cur.execute(
                        "SELECT node_id, km_region_id, km_value FROM nodes WHERE node_id = %s",
                        (nids[0],),
                    )
                    bn_start = cur.fetchone()
                    cur.execute(
                        "SELECT node_id, km_region_id, km_value FROM nodes WHERE node_id = %s",
                        (nids[-1],),
                    )
                    bn_end = cur.fetchone()
                    if bn_start:
                        if bn_start.get("km_region_id"):
                            blk_elr = decode_code("kmregionmasterdesc", bn_start["km_region_id"])
                        blk_km = bn_start.get("km_value")
                    if bn_end:
                        if not blk_elr and bn_end.get("km_region_id"):
                            blk_elr = decode_code("kmregionmasterdesc", bn_end["km_region_id"])
                        blk_end_km = bn_end.get("km_value")
                    if (not blk_elr or blk_km is None) and len(nids) > 2:
                        cur.execute(
                            "SELECT km_region_id, km_value FROM nodes WHERE node_id = ANY(%s) AND km_value > 0 LIMIT 1",
                            (nids,),
                        )
                        mid_bn = cur.fetchone()
                        if mid_bn:
                            if not blk_elr and mid_bn.get("km_region_id"):
                                blk_elr = decode_code("kmregionmasterdesc", mid_bn["km_region_id"])
                            if blk_km is None:
                                blk_km = mid_bn.get("km_value")
                            is_blk_extrapolated = True
                    if blk_km is not None and blk_end_km is not None:
                        blk_dir = "down" if blk_km < blk_end_km else "up"

                nwr_cross_links = find_nwr_cross_links_for_tps(
                    cur,
                    blk_elr,
                    blk_km,
                    end_km_meters=blk_end_km,
                    direction_hint=blk_dir,
                    item_label=f"Block #{blk['block_id']}",
                    is_extrapolated=is_blk_extrapolated,
                )

                return {
                    "asset_type": "block",
                    "id": blk["pk"],
                    "title": f"Signalling Block #{blk['block_id']} ({b_len}m)",
                    "subtitle": f"{'Shunt' if blk['shunting_flag'] else 'Main'} Block • Pre-sig {blk['pre_signal_distance']}m • Sight {blk['sight_distance']}m • {len(nids)} nodes",
                    "x": cx,
                    "y": cy,
                    "bbox": [blk["min_x"], blk["min_y"], blk["max_x"], blk["max_y"]],
                    "decoded": decoded,
                    "related": {
                        "length_meters": b_len,
                        "entry_signals": entry_sigs,
                        "exit_signals": exit_sigs,
                        "exit_overlaps": exit_overlaps,
                        "turnout_nodes": p_info["turnout_nodes"],
                        "node_ids": nids,
                        "nwr_cross_links": nwr_cross_links,
                    },
                    "highlight": {
                        "ways": highlight_ways,
                        "points": highlight_points,
                    },
                    "raw_attrs": blk["raw_attrs"],
                    "raw_children": blk["raw_children"],
                }

            elif asset_type == "route":
                cur.execute(
                    """
                    SELECT r.*, s.longname AS station_name, s.abbrev AS station_abbrev
                    FROM routes r
                    LEFT JOIN stations s ON s.station_id = r.station_id
                    WHERE r.pk = %s
                    """,
                    (asset_id,),
                )
                rt = cur.fetchone()
                if not rt:
                    raise HTTPException(status_code=404, detail="Route not found")

                decoded = {
                    "Route PK / Route ID": f"{rt['pk']} / {rt['route_id']}",
                    "Station": f"{rt['station_name']} ({rt['station_abbrev']}) [ID {rt['station_id']}]",
                    "ROP Line Code": f"{decode_code('roplinecodedesc', rt['rop_line_code_id'])} (ID {rt['rop_line_code_id']})",
                    "SA Line Code": f"{decode_code('salinecodedesc', rt['sa_line_code_id'])} (ID {rt['sa_line_code_id']})",
                    "Prelocation": rt["prelocation"],
                    "Way Nodes Count": len(rt["node_ids"] or []),
                    "Validity Window": f"{rt['valid_from']} → {rt['valid_to']}",
                    "Last Modified": rt["last_modified"],
                }
                cx = (rt["min_x"] + rt["max_x"]) / 2.0 if rt["min_x"] is not None else None
                cy = (rt["min_y"] + rt["max_y"]) / 2.0 if rt["min_y"] is not None else None

                rt_elr = None
                rt_km = None
                rt_end_km = None
                is_rt_extrapolated = False
                if rt.get("node_ids"):
                    cur.execute(
                        "SELECT km_region_id, km_value FROM nodes WHERE node_id = %s",
                        (rt["node_ids"][0],),
                    )
                    rn_start = cur.fetchone()
                    if rn_start:
                        if rn_start.get("km_region_id"):
                            rt_elr = decode_code("kmregionmasterdesc", rn_start["km_region_id"])
                        rt_km = rn_start.get("km_value")
                    if len(rt["node_ids"]) > 1:
                        cur.execute(
                            "SELECT km_region_id, km_value FROM nodes WHERE node_id = %s",
                            (rt["node_ids"][-1],),
                        )
                        rn_end = cur.fetchone()
                        if rn_end and rn_end.get("km_value"):
                            rt_end_km = rn_end["km_value"]
                    if (not rt_elr or rt_km is None) and len(rt["node_ids"]) > 2:
                        cur.execute(
                            "SELECT km_region_id, km_value FROM nodes WHERE node_id = ANY(%s) AND km_value > 0 LIMIT 1",
                            (rt["node_ids"],),
                        )
                        mid_rn = cur.fetchone()
                        if mid_rn:
                            if not rt_elr and mid_rn.get("km_region_id"):
                                rt_elr = decode_code("kmregionmasterdesc", mid_rn["km_region_id"])
                            if rt_km is None:
                                rt_km = mid_rn.get("km_value")
                            is_rt_extrapolated = True

                nwr_cross_links = find_nwr_cross_links_for_tps(
                    cur,
                    rt_elr,
                    rt_km,
                    end_km_meters=rt_end_km,
                    item_label=f"Route #{rt['route_id']}",
                    is_extrapolated=is_rt_extrapolated,
                )

                return {
                    "asset_type": "route",
                    "id": rt["pk"],
                    "title": f"Route #{rt['route_id']} — {rt['station_name'] or ''}",
                    "subtitle": f"ROP: {decode_code('roplinecodedesc', rt['rop_line_code_id'])} • {len(rt['node_ids'] or [])} nodes",
                    "x": cx,
                    "y": cy,
                    "bbox": [rt["min_x"], rt["min_y"], rt["max_x"], rt["max_y"]],
                    "decoded": decoded,
                    "related": {
                        "node_ids": rt["node_ids"],
                        "station_id": rt["station_id"],
                        "nwr_cross_links": nwr_cross_links,
                    },
                    "highlight": {
                        "ways": [
                            {
                                "label": f"Route #{rt['route_id']}",
                                "coords": rt["coords"],
                                "color": "#f97316",
                            }
                        ] if rt["coords"] else [],
                        "points": [],
                    },
                    "raw_attrs": rt["raw_attrs"],
                    "raw_children": rt["raw_children"],
                }

            elif asset_type == "attributesection":
                cur.execute(
                    """
                    SELECT a.*, s.name AS sec_name, s.section_type
                    FROM attribute_sections a
                    LEFT JOIN sections s ON s.section_id = a.section_id
                    WHERE a.pk = %s
                    """,
                    (asset_id,),
                )
                asec = cur.fetchone()
                if not asec:
                    raise HTTPException(status_code=404, detail="Attribute Section not found")

                decoded_sattrs = []
                for sa in asec["section_attrs"] or []:
                    sat_id = sa.get("sectionattributetypeid")
                    decoded_sattrs.append({
                        "type_id": sat_id,
                        "type_name": decode_code("sectionattributetypedesc", sat_id) or f"Type {sat_id}",
                        "value": sa.get("value"),
                        "param": sa.get("attributeparam"),
                        "km_begin": meters_to_miles_chains(int(sa["kmvaluebegin"])) if sa.get("kmvaluebegin") else None,
                        "km_end": meters_to_miles_chains(int(sa["kmvalueend"])) if sa.get("kmvalueend") else None,
                        "ascending": sa.get("ascending"),
                    })

                elr = decode_code("kmregionmasterdesc", asec["km_region_id"])
                decoded = {
                    "Attribute Section PK / ID": f"{asec['pk']} / {asec['id']}",
                    "Description (Corridor / ELR)": asec["description"],
                    "Short Description": asec["short_description"],
                    "Parent Section": f"{asec['sec_name']} (ID {asec['section_id']})",
                    "KM Region / ELR": f"{elr} (ID {asec['km_region_id']})",
                    "Directed Edges Count": len(asec["edge_pairs"] or []),
                    "Section Attributes Count": len(decoded_sattrs),
                    "Validity Window": f"{asec['valid_from']} → {asec['valid_to']}",
                    "Last Modified": asec["last_modified"],
                }
                cx = (asec["min_x"] + asec["max_x"]) / 2.0 if asec["min_x"] is not None else None
                cy = (asec["min_y"] + asec["max_y"]) / 2.0 if asec["min_y"] is not None else None

                as_km = None
                as_end_km = None
                is_as_extrapolated = False
                if asec.get("section_attrs"):
                    for sa in asec["section_attrs"]:
                        if sa.get("kmvaluebegin"):
                            as_km = float(sa["kmvaluebegin"])
                        if sa.get("kmvalueend"):
                            as_end_km = float(sa["kmvalueend"])
                        if as_km is not None:
                            break
                if as_km is None and asec.get("node_ids"):
                    cur.execute(
                        "SELECT km_value FROM nodes WHERE node_id = ANY(%s) AND km_value > 0 LIMIT 1",
                        (asec["node_ids"],),
                    )
                    as_nd = cur.fetchone()
                    if as_nd:
                        as_km = as_nd["km_value"]
                        is_as_extrapolated = True

                nwr_cross_links = find_nwr_cross_links_for_tps(
                    cur,
                    elr or asec.get("description"),
                    as_km,
                    end_km_meters=as_end_km,
                    item_label=f"Attribute Section {asec['short_description']}",
                    is_extrapolated=is_as_extrapolated,
                )

                return {
                    "asset_type": "attributesection",
                    "id": asec["pk"],
                    "title": f"Attribute Section {asec['description']} ({asec['short_description']})",
                    "subtitle": f"ELR {elr or asec['description']} • {len(asec['edge_pairs'] or [])} segments • {len(decoded_sattrs)} attributes",
                    "x": cx,
                    "y": cy,
                    "bbox": [asec["min_x"], asec["min_y"], asec["max_x"], asec["max_y"]],
                    "decoded": decoded,
                    "related": {
                        "section_attributes": decoded_sattrs,
                        "node_ids": asec["node_ids"],
                        "nwr_cross_links": nwr_cross_links,
                    },
                    "highlight": {
                        "segments": asec["coords"],
                        "ways": [],
                        "points": [],
                    },
                    "raw_attrs": asec["raw_attrs"],
                    "raw_children": asec["raw_children"],
                }

            elif asset_type == "graphictext":
                cur.execute("SELECT * FROM graphic_texts WHERE pk = %s", (asset_id,))
                gt = cur.fetchone()
                if not gt:
                    raise HTTPException(status_code=404, detail="Graphic text not found")
                decoded = {
                    "Graphic Text PK": gt["pk"],
                    "Label Text": gt["text"],
                    "Coordinates (x, y)": f"({gt['x']}, {gt['y']})",
                    "Font Size": gt["size"],
                    "Rotation Angle": f"{gt['angle']}°",
                    "Layer": gt["layers"],
                    "Validity Window": f"{gt['valid_from']} → {gt['valid_to']}",
                    "Last Modified": gt["last_modified"],
                }
                return {
                    "asset_type": "graphictext",
                    "id": gt["pk"],
                    "title": f"CAD Label \"{gt['text']}\"",
                    "subtitle": f"Layer {gt['layers']} • Size {gt['size']} • ({gt['x']}, {gt['y']})",
                    "x": gt["x"],
                    "y": gt["y"],
                    "decoded": decoded,
                    "related": {},
                    "highlight": {
                        "ways": [],
                        "points": [{"x": gt["x"], "y": gt["y"], "label": gt["text"], "color": "#38bdf8"}],
                    },
                    "raw_attrs": gt["raw_attrs"],
                    "raw_children": [],
                }

            elif asset_type == "graphicvector":
                cur.execute("SELECT * FROM graphic_vectors WHERE pk = %s", (asset_id,))
                gv = cur.fetchone()
                if not gv:
                    raise HTTPException(status_code=404, detail="Graphic vector not found")
                decoded = {
                    "Graphic Vector PK": gv["pk"],
                    "Start (x0, y0)": f"({gv['x0']}, {gv['y0']})",
                    "End (x1, y1)": f"({gv['x1']}, {gv['y1']})",
                    "Layer": gv["layers"],
                    "Validity Window": f"{gv['valid_from']} → {gv['valid_to']}",
                    "Last Modified": gv["last_modified"],
                }
                return {
                    "asset_type": "graphicvector",
                    "id": gv["pk"],
                    "title": f"CAD Vector #{gv['pk']}",
                    "subtitle": f"Layer {gv['layers']} • ({gv['x0']}, {gv['y0']}) → ({gv['x1']}, {gv['y1']})",
                    "x": (gv["x0"] + gv["x1"]) / 2.0,
                    "y": (gv["y0"] + gv["y1"]) / 2.0,
                    "decoded": decoded,
                    "related": {},
                    "highlight": {
                        "ways": [{"label": f"Vector #{gv['pk']}", "coords": [[gv["x0"], gv["y0"]], [gv["x1"], gv["y1"]]], "color": "#38bdf8"}],
                        "points": [],
                    },
                    "raw_attrs": gv["raw_attrs"],
                    "raw_children": [],
                }

            elif asset_type == "annotation":
                cur.execute("SELECT * FROM annotations WHERE pk = %s", (asset_id,))
                an = cur.fetchone()
                if not an:
                    raise HTTPException(status_code=404, detail="Annotation not found")
                decoded = {
                    "Annotation PK": an["pk"],
                    "Coordinates (x, y)": f"({an['x']}, {an['y']})",
                    "Operational Note (HTML)": an["text"],
                    "Validity Window": f"{an['valid_from']} → {an['valid_to']}",
                    "Last Modified": an["last_modified"],
                }
                return {
                    "asset_type": "annotation",
                    "id": an["pk"],
                    "title": f"Operational Note #{an['pk']}",
                    "subtitle": f"({an['x']}, {an['y']}) • Last Modified {an['last_modified']}",
                    "x": an["x"],
                    "y": an["y"],
                    "decoded": decoded,
                    "related": {},
                    "highlight": {
                        "ways": [],
                        "points": [{"x": an["x"], "y": an["y"], "label": f"Note #{an['pk']}", "color": "#ec4899"}],
                    },
                    "raw_attrs": an["raw_attrs"],
                    "raw_children": [],
                }

            elif asset_type == "polyarea":
                cur.execute("SELECT * FROM poly_areas WHERE pk = %s", (asset_id,))
                pa = cur.fetchone()
                if not pa:
                    raise HTTPException(status_code=404, detail="Poly area not found")
                cx = (pa["min_x"] + pa["max_x"]) / 2.0 if pa["min_x"] is not None else None
                cy = (pa["min_y"] + pa["max_y"]) / 2.0 if pa["min_y"] is not None else None
                decoded = {
                    "Poly Area PK / XML ID": f"{pa['pk']} / {pa['id']}",
                    "Name": pa["name"],
                    "Description": pa["description"] or "—",
                    "Corner Points": len(pa["corners"] or []),
                    "Bounding Box": f"[{pa['min_x']}, {pa['min_y']}] → [{pa['max_x']}, {pa['max_y']}]",
                }
                corners = pa["corners"] or []
                closed_way = corners + [corners[0]] if len(corners) >= 3 else corners
                return {
                    "asset_type": "polyarea",
                    "id": pa["pk"],
                    "title": f"Poly Region {pa['name'] or '#' + str(pa['id'])}",
                    "subtitle": f"ID {pa['id']} • {len(corners)} polygon vertices",
                    "x": cx,
                    "y": cy,
                    "decoded": decoded,
                    "related": {},
                    "highlight": {
                        "ways": [{"label": pa["name"], "coords": closed_way, "color": "#a855f7"}] if closed_way else [],
                        "points": [],
                    },
                    "raw_attrs": pa["raw_attrs"],
                    "raw_children": pa["raw_children"],
                }

            elif asset_type == "modeltrain":
                cur.execute(
                    "SELECT * FROM raw_tps_elements WHERE tag = 'modeltrain' AND entity_id = %s LIMIT 1",
                    (str(asset_id),),
                )
                mt = cur.fetchone()
                if not mt:
                    raise HTTPException(status_code=404, detail="Model train / timing load not found")
                attrs = mt["raw_attrs"] or {}
                children = mt["raw_children"] or []

                # Extract traction unit IDs in consist order
                tu_ids = [
                    str(c.get("attrs", {}).get("id"))
                    for c in children
                    if c.get("tag") == "tractionunit" and c.get("attrs", {}).get("id")
                ]
                tu_map = {}
                if tu_ids:
                    cur.execute(
                        "SELECT entity_id, name, raw_attrs, raw_children FROM raw_tps_elements WHERE tag = 'tractionunit' AND entity_id = ANY(%s)",
                        (list(set(tu_ids)),),
                    )
                    for r in cur.fetchall():
                        tu_map[str(r["entity_id"])] = r

                consist_units = []
                total_tu_len = 0.0
                total_tu_wt = 0.0
                for idx, tid in enumerate(tu_ids):
                    trec = tu_map.get(tid)
                    if not trec:
                        continue
                    ta = trec["raw_attrs"] or {}
                    tc = trec["raw_children"] or []
                    t_len = float(ta.get("length") or 0)
                    t_wt = float(ta.get("weight") or 0)
                    total_tu_len += t_len
                    total_tu_wt += t_wt
                    parsed_curves = parse_traction_curves(ta, tc)
                    consist_units.append({
                        "seq": idx + 1,
                        "id": int(tid) if tid.isdigit() else tid,
                        "name": trec["name"],
                        "desc": ta.get("desc") or "",
                        "type_code": decode_code("tractiontypedesc", ta.get("type")) or "—",
                        "motor_desc": decode_code("motortypedesc", ta.get("motortype")) or "—",
                        "length_m": t_len,
                        "weight_t": t_wt,
                        "max_speed_kmh": float(ta.get("maxallowedspeed") or 0),
                        "max_speed_mph": round(float(ta.get("maxallowedspeed") or 0) * 0.621371, 1),
                        "retardation": float(ta.get("defretardation") or 0),
                        "engine_mass_factor": float(ta.get("enginemassfactor") or 1.0),
                        "coeffA": float(ta.get("coeffA") or 0),
                        "coeffB": float(ta.get("coeffB") or 0),
                        "coeffC": float(ta.get("coeffC") or 0),
                        "fs_diagrams": parsed_curves["fs_diagrams"],
                        "sr_curve": parsed_curves["sr_curve"],
                        "max_force_kn": parsed_curves["max_force_kn"],
                        "max_power_kw": parsed_curves["max_power_kw"],
                    })

                # Resolve trailing coach template if coachid > 0
                coach_id = str(attrs.get("coachid") or "0")
                coach_info = None
                if coach_id != "0":
                    cur.execute(
                        "SELECT entity_id, name, raw_attrs FROM raw_tps_elements WHERE tag = 'coach' AND entity_id = %s LIMIT 1",
                        (coach_id,),
                    )
                    crow = cur.fetchone()
                    if crow:
                        ca = crow["raw_attrs"] or {}
                        coach_info = {
                            "id": crow["entity_id"],
                            "name": crow["name"],
                            "desc": ca.get("desc") or "",
                            "coach_type": decode_code("coachtypedesc", ca.get("coachtypeid")) or "—",
                            "unit_length_m": float(ca.get("length") or 0),
                            "unit_weight_t": float(ca.get("weight") or 0),
                            "max_speed_kmh": float(ca.get("maxspeed") or 0),
                        }

                # En-route timing load transitions (<mtchangetransiting>)
                cur.execute(
                    """
                    SELECT raw_attrs
                    FROM raw_tps_elements
                    WHERE tag = 'mtchangetransiting'
                      AND (raw_attrs->>'firstmodeltrainid' = %s OR raw_attrs->>'secondmodeltrainid' = %s)
                    LIMIT 30
                    """,
                    (str(asset_id), str(asset_id)),
                )
                other_mt_ids = set()
                trans_pairs = []
                for tr in cur.fetchall():
                    ra = tr["raw_attrs"] or {}
                    f_id = ra.get("firstmodeltrainid")
                    s_id = ra.get("secondmodeltrainid")
                    if f_id:
                        other_mt_ids.add(f_id)
                    if s_id:
                        other_mt_ids.add(s_id)
                    trans_pairs.append((f_id, s_id))

                mt_names = {}
                if other_mt_ids:
                    cur.execute(
                        "SELECT entity_id, name, raw_attrs->>'desc' AS descr FROM raw_tps_elements WHERE tag = 'modeltrain' AND entity_id = ANY(%s)",
                        (list(other_mt_ids),),
                    )
                    for r in cur.fetchall():
                        mt_names[str(r["entity_id"])] = f"{r['name']} ({r['descr'] or ''})"

                transitions = [
                    {
                        "from_id": int(f) if str(f).isdigit() else f,
                        "from_name": mt_names.get(str(f), f"#{f}"),
                        "to_id": int(s) if str(s).isdigit() else s,
                        "to_name": mt_names.get(str(s), f"#{s}"),
                    }
                    for f, s in trans_pairs
                ]

                vmax_kmh = float(attrs.get("maxspeed") or 0)
                vmax_mph = round(vmax_kmh * 0.621371, 1)
                coaches_len = float(attrs.get("coachestotallength") or 0)
                coaches_wt = float(attrs.get("coachestotalweight") or 0)
                total_len = round(total_tu_len + coaches_len, 1)
                total_wt = round(total_tu_wt + coaches_wt, 1)
                prof_id = attrs.get("profile", "0")
                prof_desc = decode_code("veloprofiledesc", prof_id) or "Standard Line Speed (Profile 0)"

                decoded = {
                    "Timing Load ID / Code": f"#{mt['entity_id']} — {mt['name']}",
                    "Public Timetable Timing Load": attrs.get("publicname") or mt["name"],
                    "Description": attrs.get("desc") or "—",
                    "Maximum Permitted Speed": f"{vmax_kmh:.0f} km/h ({vmax_mph} mph)",
                    "Differential Speed Profile": f"{prof_desc} (Profile ID {prof_id})",
                    "Traction Unit Consist": (
                        f"{len(consist_units)} unit(s): " + " + ".join(f"{u['name']} ({u['desc']})" for u in consist_units)
                        if consist_units
                        else "None"
                    ),
                    "Trailing Load / Coaching Stock": (
                        f"{coach_info['name']} ({coach_info['desc']}) — {coaches_len} m / {coaches_wt} t"
                        if coach_info
                        else f"{coaches_len} m / {coaches_wt} t"
                    ),
                    "Total Train Length & Mass": f"{total_len} m • {total_wt} tonnes",
                    "Service Braking Retardation": f"{attrs.get('retardation', '0')} m/s² (ATC: {attrs.get('atcretardation', '0')} m/s²)",
                    "Comfort Acceleration": f"{attrs.get('comfortacceleration', '0')} m/s²",
                    "Supplementary Run Time": f"{attrs.get('supplruntime', '0')}% • Min Stop Time: {attrs.get('minstoptime', '0')} s",
                    "ATC / Check Effective Track Length": f"ATC: {attrs.get('atc')} • Check Effective Track Length: {attrs.get('checkEffectiveTrackLength')}",
                    "En-Route Timing Load Transitions": f"{len(transitions)} valid transition pair(s)" if transitions else "None",
                    "Last Modified": attrs.get("lastmodified") or "—",
                }

                return {
                    "asset_type": "modeltrain",
                    "id": int(mt["entity_id"]) if str(mt["entity_id"]).isdigit() else mt["entity_id"],
                    "title": f"Timing Load {mt['name']} ({attrs.get('desc') or attrs.get('publicname') or ''})",
                    "subtitle": f"Max {vmax_kmh:.0f} km/h ({vmax_mph} mph) • {prof_desc} • {total_len}m / {total_wt}t",
                    "x": None,
                    "y": None,
                    "decoded": decoded,
                    "related": {
                        "traction_units": consist_units,
                        "coach": coach_info,
                        "velocity_profile": {
                            "id": prof_id,
                            "display": prof_desc,
                        },
                        "transitions": transitions,
                    },
                    "highlight": {"ways": [], "points": []},
                    "raw_attrs": attrs,
                    "raw_children": children,
                }

            elif asset_type == "tractionunit":
                cur.execute(
                    "SELECT * FROM raw_tps_elements WHERE tag = 'tractionunit' AND entity_id = %s LIMIT 1",
                    (str(asset_id),),
                )
                tu = cur.fetchone()
                if not tu:
                    raise HTTPException(status_code=404, detail="Traction unit not found")
                ta = tu["raw_attrs"] or {}
                tc = tu["raw_children"] or []
                parsed = parse_traction_curves(ta, tc)

                # Find all <modeltrain> timing loads powered by this traction unit
                cur.execute(
                    """
                    SELECT entity_id, name, raw_attrs
                    FROM raw_tps_elements
                    WHERE tag = 'modeltrain'
                      AND raw_children @> jsonb_build_array(jsonb_build_object('tag', 'tractionunit', 'attrs', jsonb_build_object('id', %s::text)))
                    ORDER BY name ASC
                    LIMIT 100
                    """,
                    (str(asset_id),),
                )
                linked_trains = []
                for mrow in cur.fetchall():
                    ma = mrow["raw_attrs"] or {}
                    mv = float(ma.get("maxspeed") or 0)
                    linked_trains.append({
                        "id": int(mrow["entity_id"]) if str(mrow["entity_id"]).isdigit() else mrow["entity_id"],
                        "name": mrow["name"],
                        "publicname": ma.get("publicname") or mrow["name"],
                        "desc": ma.get("desc") or "",
                        "maxspeed_kmh": mv,
                        "maxspeed_mph": round(mv * 0.621371, 1),
                        "profile_name": decode_code("veloprofiledesc", ma.get("profile")) or "Standard",
                        "coaches_length_m": float(ma.get("coachestotallength") or 0),
                        "coaches_weight_t": float(ma.get("coachestotalweight") or 0),
                    })

                vmax_kmh = float(ta.get("maxallowedspeed") or 0)
                vmax_mph = round(vmax_kmh * 0.621371, 1)
                ttype = decode_code("tractiontypedesc", ta.get("type")) or "—"
                mtype = decode_code("motortypedesc", ta.get("motortype")) or "—"
                prof_desc = decode_code("veloprofiledesc", ta.get("defveloprof")) or "Standard (0)"
                ca = float(ta.get("coeffA") or 0)
                cb = float(ta.get("coeffB") or 0)
                cc = float(ta.get("coeffC") or 0)

                decoded = {
                    "Traction Unit ID / Code": f"#{tu['entity_id']} — {tu['name']}",
                    "Description": ta.get("desc") or "—",
                    "Traction Classification": f"{ttype} (Type {ta.get('type')}) • Propulsion: {mtype}",
                    "Unit Length & Mass": f"{ta.get('length', 0)} m • {ta.get('weight', 0)} tonnes (Rotational Mass Factor: {ta.get('enginemassfactor', '1.0')})",
                    "Maximum Allowed Speed": f"{vmax_kmh:.0f} km/h ({vmax_mph} mph)",
                    "Default Differential Speed Profile": f"{prof_desc} (ID {ta.get('defveloprof', '0')})",
                    "Default Braking Retardation": f"{ta.get('defretardation', '0')} m/s²",
                    "Davis Running Resistance R(v)": f"R(v) = {ca} + {cb}·v + {cc}·v² N (v in km/h)",
                    "Starting Tractive Effort": f"{parsed['max_force_kn']} kN",
                    "Peak Wheel-Rim Power": f"{parsed['max_power_kw']} kW ({round(parsed['max_power_kw'] * 1.34102):,} bhp)",
                    "Tractive Effort Diagrams": f"{len(parsed['fs_diagrams'])} F-v curve(s) • {len(parsed['sr_curve'])} resistance point(s)",
                    "Timing Loads Using This Unit": f"{len(linked_trains)} <modeltrain> timing load(s)",
                    "Last Modified": ta.get("lastmodified") or "—",
                }

                return {
                    "asset_type": "tractionunit",
                    "id": int(tu["entity_id"]) if str(tu["entity_id"]).isdigit() else tu["entity_id"],
                    "title": f"Traction Unit {tu['name']} — {ta.get('desc') or ''}",
                    "subtitle": f"{ttype} ({mtype}) • {ta.get('length', 0)}m / {ta.get('weight', 0)}t • {parsed['max_force_kn']} kN / {parsed['max_power_kw']} kW",
                    "x": None,
                    "y": None,
                    "decoded": decoded,
                    "related": {
                        "traction_units": [
                            {
                                "seq": 1,
                                "id": int(tu["entity_id"]) if str(tu["entity_id"]).isdigit() else tu["entity_id"],
                                "name": tu["name"],
                                "desc": ta.get("desc") or "",
                                "type_code": ttype,
                                "motor_desc": mtype,
                                "length_m": float(ta.get("length") or 0),
                                "weight_t": float(ta.get("weight") or 0),
                                "max_speed_kmh": vmax_kmh,
                                "max_speed_mph": vmax_mph,
                                "retardation": float(ta.get("defretardation") or 0),
                                "engine_mass_factor": float(ta.get("enginemassfactor") or 1.0),
                                "coeffA": ca,
                                "coeffB": cb,
                                "coeffC": cc,
                                "fs_diagrams": parsed["fs_diagrams"],
                                "sr_curve": parsed["sr_curve"],
                                "max_force_kn": parsed["max_force_kn"],
                                "max_power_kw": parsed["max_power_kw"],
                            }
                        ],
                        "model_trains": linked_trains,
                    },
                    "highlight": {"ways": [], "points": []},
                    "raw_attrs": ta,
                    "raw_children": tc,
                }

            elif asset_type == "stationbank":
                cur.execute(
                    "SELECT * FROM raw_tps_elements WHERE tag = 'stationbank' AND entity_id = %s LIMIT 1",
                    (str(asset_id),),
                )
                sb = cur.fetchone()
                if not sb:
                    # Fallback if section_id was passed directly
                    cur.execute(
                        "SELECT * FROM raw_tps_elements WHERE tag = 'stationbanksection' AND entity_id = %s LIMIT 1",
                        (str(asset_id),),
                    )
                    sb = cur.fetchone()
                    if not sb:
                        raise HTTPException(status_code=404, detail="Station bank corridor not found")
                    sec_ids = [str(sb["entity_id"])]
                else:
                    sec_ids = [
                        str(c.get("attrs", {}).get("stationbankid"))
                        for c in (sb["raw_children"] or [])
                        if c.get("tag") == "stationbankid" and c.get("attrs", {}).get("stationbankid")
                    ]

                sections_rows = []
                if sec_ids:
                    cur.execute(
                        "SELECT entity_id, name, raw_attrs, raw_children FROM raw_tps_elements WHERE tag = 'stationbanksection' AND entity_id = ANY(%s)",
                        (sec_ids,),
                    )
                    sections_rows = cur.fetchall()

                all_st_ids = set()
                for srow in sections_rows:
                    for ch in srow["raw_children"] or []:
                        if ch.get("tag") == "stationbankstation":
                            sid = ch.get("attrs", {}).get("id")
                            if sid and str(sid).isdigit():
                                all_st_ids.add(int(sid))

                st_map = {}
                if all_st_ids:
                    cur.execute(
                        """
                        SELECT station_id, abbrev, longname, crscode, stanox,
                               station_type, station_category, x, y, km_region_id, km_value
                        FROM stations
                        WHERE station_id = ANY(%s)
                        """,
                        (list(all_st_ids),),
                    )
                    for r in cur.fetchall():
                        st_map[r["station_id"]] = r

                sections_enriched = []
                highlight_ways = []
                highlight_points = []
                xs, ys = [], []

                for srow in sections_rows:
                    sa = srow["raw_attrs"] or {}
                    way_coords = []
                    stops = []
                    for ch in srow["raw_children"] or []:
                        if ch.get("tag") != "stationbankstation":
                            continue
                        ca = ch.get("attrs", {})
                        sid = int(ca["id"]) if str(ca.get("id", "")).isdigit() else 0
                        st_rec = st_map.get(sid)
                        layouts = []
                        for lch in ch.get("children") or []:
                            if lch.get("tag") == "stationbankstationlayout":
                                lid = lch.get("attrs", {}).get("stationlayoutcodeid")
                                ldesc = decode_code("stationlayoutcode", lid) or f"Layout {lid}"
                                layouts.append(ldesc)
                        if st_rec:
                            stype_desc = decode_code("stationtypedesc", st_rec["station_type"]) or "—"
                            scat_desc = decode_code("stationcategorydesc", st_rec["station_category"]) or "—"
                            elr = decode_code("kmregionmasterdesc", st_rec["km_region_id"])
                            if st_rec["x"] is not None and st_rec["y"] is not None:
                                way_coords.append([st_rec["x"], st_rec["y"]])
                                xs.append(st_rec["x"])
                                ys.append(st_rec["y"])
                                is_mand = st_rec["station_type"] == 2
                                highlight_points.append({
                                    "x": st_rec["x"],
                                    "y": st_rec["y"],
                                    "label": f"{ int(ca.get('seq', 0)) + 1 }. {st_rec['longname'] or st_rec['abbrev']} ({st_rec['abbrev']})",
                                    "color": "#f43f5e" if is_mand else "#38bdf8",
                                    "shape": "diamond" if is_mand else "circle",
                                })
                            stops.append({
                                "seq": int(ca.get("seq") or 0) + 1,
                                "station_id": sid,
                                "abbrev": st_rec["abbrev"],
                                "longname": st_rec["longname"] or st_rec["abbrev"],
                                "crscode": st_rec["crscode"],
                                "stanox": st_rec["stanox"],
                                "timing_role": stype_desc,
                                "planning_category": scat_desc,
                                "elr": elr,
                                "km_str": meters_to_miles_chains(st_rec["km_value"]),
                                "layouts": layouts,
                                "x": st_rec["x"],
                                "y": st_rec["y"],
                            })
                    if len(way_coords) >= 2:
                        highlight_ways.append({
                            "label": f"{srow['name']} ({sa.get('description') or ''})",
                            "coords": way_coords,
                            "color": "#a855f7",
                            "width": 4.5,
                        })
                    sections_enriched.append({
                        "section_id": srow["entity_id"],
                        "name": srow["name"],
                        "description": sa.get("description") or "",
                        "stops": stops,
                    })

                sb_attrs = sb["raw_attrs"] or {}
                total_stops = sum(len(s["stops"]) for s in sections_enriched)
                cx = sum(xs) / len(xs) if xs else None
                cy = sum(ys) / len(ys) if ys else None
                bbox = [min(xs), min(ys), max(xs), max(ys)] if xs else None

                decoded = {
                    "Station Bank ID / Code": f"#{sb['entity_id']} — {sb['name']}",
                    "Timetable Corridor Description": sb_attrs.get("description") or "—",
                    "Corridor Sections": len(sections_enriched),
                    "Ordered Timing Points (TIPLOCs)": total_stops,
                    "Origin → Destination": (
                        f"{sections_enriched[0]['stops'][0]['longname']} ({sections_enriched[0]['stops'][0]['abbrev']}) → "
                        f"{sections_enriched[-1]['stops'][-1]['longname']} ({sections_enriched[-1]['stops'][-1]['abbrev']})"
                        if sections_enriched and sections_enriched[0]["stops"] and sections_enriched[-1]["stops"]
                        else "—"
                    ),
                    "Last Modified": sb_attrs.get("lastmodified") or "—",
                }

                return {
                    "asset_type": "stationbank",
                    "id": int(sb["entity_id"]) if str(sb["entity_id"]).isdigit() else sb["entity_id"],
                    "title": f"Timetable Corridor {sb['name']} — {sb_attrs.get('description') or ''}",
                    "subtitle": f"Station Bank #{sb['entity_id']} • {total_stops} Ordered Timing Points",
                    "x": cx,
                    "y": cy,
                    "bbox": bbox,
                    "decoded": decoded,
                    "related": {
                        "corridor_sections": sections_enriched,
                    },
                    "highlight": {
                        "ways": highlight_ways,
                        "points": highlight_points,
                    },
                    "raw_attrs": sb_attrs,
                    "raw_children": sb["raw_children"],
                }

    raise HTTPException(status_code=400, detail=f"Unknown asset type: {asset_type}")


def parse_traction_curves(ta: dict, tc: list) -> dict:
    """Extracts Speed-Force (<fsdiagram>) and Speed-Resistance (<srdiagvalue> / Davis) curves from a <tractionunit>."""
    sr_curve = []
    fs_diagrams = []
    max_force_kn = 0.0
    max_power_kw = 0.0

    for ch in tc or []:
        tag = ch.get("tag")
        if tag == "srdiagvalue":
            a = ch.get("attrs", {})
            spd = float(a.get("speed") or 0)
            ret_n = float(a.get("retardation") or 0)
            sr_curve.append({
                "speed_kmh": round(spd, 2),
                "speed_mph": round(spd * 0.621371, 1),
                "resistance_n": round(ret_n, 1),
                "resistance_kn": round(ret_n / 1000.0, 3),
            })
        elif tag == "fsdiagram":
            da = ch.get("attrs", {})
            pts = []
            for fch in ch.get("children") or []:
                if fch.get("tag") == "fsdiagvalue":
                    fa = fch.get("attrs", {})
                    spd = float(fa.get("speed") or 0)
                    frc = float(fa.get("force") or 0)
                    pwr_kw = round((frc * spd) / 3.6, 1)
                    if frc > max_force_kn:
                        max_force_kn = round(frc, 2)
                    max_power_kw = max(max_power_kw, pwr_kw)
                    pts.append({
                        "speed_kmh": round(spd, 2),
                        "speed_mph": round(spd * 0.621371, 1),
                        "force_kn": round(frc, 2),
                        "power_kw": pwr_kw,
                    })
            pts.sort(key=lambda p: p["speed_kmh"])
            if pts:
                eprof = decode_code("electricprofiledesc", da.get("electricprofilenameid")) or "Default"
                fs_diagrams.append({
                    "id": da.get("id"),
                    "electric_profile": eprof,
                    "points": pts,
                })

    sr_curve.sort(key=lambda p: p["speed_kmh"])
    # Synthesize from Davis formula R(v) = A + B*v + C*v^2 if srdiagvalue is absent but coefficients exist
    ca = float(ta.get("coeffA") or 0)
    cb = float(ta.get("coeffB") or 0)
    cc = float(ta.get("coeffC") or 0)
    vmax = float(ta.get("maxallowedspeed") or 160)
    if not sr_curve and (ca > 0 or cb > 0 or cc > 0) and vmax > 0:
        steps = 12
        for i in range(steps + 1):
            v = (vmax * i) / steps
            r_n = ca + cb * v + cc * (v * v)
            sr_curve.append({
                "speed_kmh": round(v, 2),
                "speed_mph": round(v * 0.621371, 1),
                "resistance_n": round(r_n, 1),
                "resistance_kn": round(r_n / 1000.0, 3),
            })

    return {
        "sr_curve": sr_curve,
        "fs_diagrams": fs_diagrams,
        "max_force_kn": max_force_kn,
        "max_power_kw": max_power_kw,
    }


def get_timing_catalog():
    """Builds and caches all Timing Loads (<modeltrain>), Traction Units (<tractionunit>), Coaches (<coach>),
    Differential Velocity Profiles (<veloprofiledesc>), Train Types (<traintype>),
    Station Bank Corridors (<stationbank>), and O(1) reverse lookup indexes."""
    global TIMING_CATALOG_CACHE
    if TIMING_CATALOG_CACHE is not None:
        return TIMING_CATALOG_CACHE

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # 1. Traction Units
            cur.execute(
                "SELECT entity_id, name, raw_attrs, raw_children FROM raw_tps_elements WHERE tag = 'tractionunit' AND entity_id != '0' ORDER BY name ASC"
            )
            traction_units = []
            tu_lookup = {}
            for r in cur.fetchall():
                ta = r["raw_attrs"] or {}
                tc = r["raw_children"] or []
                parsed = parse_traction_curves(ta, tc)
                vmax_kmh = float(ta.get("maxallowedspeed") or 0)
                ttype = decode_code("tractiontypedesc", ta.get("type")) or "—"
                mtype = decode_code("motortypedesc", ta.get("motortype")) or "—"
                item = {
                    "id": int(r["entity_id"]) if str(r["entity_id"]).isdigit() else r["entity_id"],
                    "name": r["name"],
                    "desc": ta.get("desc") or "",
                    "type_id": ta.get("type"),
                    "type_code": ttype,
                    "motor_id": ta.get("motortype"),
                    "motor_desc": mtype,
                    "length_m": float(ta.get("length") or 0),
                    "weight_t": float(ta.get("weight") or 0),
                    "max_speed_kmh": vmax_kmh,
                    "max_speed_mph": round(vmax_kmh * 0.621371, 1),
                    "retardation": float(ta.get("defretardation") or 0),
                    "coeffA": float(ta.get("coeffA") or 0),
                    "coeffB": float(ta.get("coeffB") or 0),
                    "coeffC": float(ta.get("coeffC") or 0),
                    "max_force_kn": parsed["max_force_kn"],
                    "max_power_kw": parsed["max_power_kw"],
                    "curve_count": len(parsed["fs_diagrams"]),
                }
                traction_units.append(item)
                tu_lookup[str(r["entity_id"])] = item

            # 2. Coaches
            cur.execute(
                "SELECT entity_id, name, raw_attrs FROM raw_tps_elements WHERE tag = 'coach' AND entity_id != '0' ORDER BY name ASC"
            )
            coaches = []
            coach_lookup = {}
            for r in cur.fetchall():
                ca = r["raw_attrs"] or {}
                vmax_kmh = float(ca.get("maxspeed") or 0)
                citem = {
                    "id": int(r["entity_id"]) if str(r["entity_id"]).isdigit() else r["entity_id"],
                    "name": r["name"],
                    "desc": ca.get("desc") or "",
                    "coach_type": decode_code("coachtypedesc", ca.get("coachtypeid")) or "—",
                    "length_m": float(ca.get("length") or 0),
                    "weight_t": float(ca.get("weight") or 0),
                    "max_speed_kmh": vmax_kmh,
                    "max_speed_mph": round(vmax_kmh * 0.621371, 1),
                }
                coaches.append(citem)
                coach_lookup[str(r["entity_id"])] = citem

            # 3. Model Trains (Timing Loads)
            cur.execute(
                "SELECT entity_id, name, raw_attrs, raw_children FROM raw_tps_elements WHERE tag = 'modeltrain' AND entity_id != '0' ORDER BY name ASC"
            )
            model_trains = []
            profile_train_counts = {}
            for r in cur.fetchall():
                ma = r["raw_attrs"] or {}
                mc = r["raw_children"] or []
                tu_ids = [
                    str(c.get("attrs", {}).get("id"))
                    for c in mc
                    if c.get("tag") == "tractionunit" and c.get("attrs", {}).get("id")
                ]
                tu_names = []
                tu_len = 0.0
                tu_wt = 0.0
                motor_types = set()
                for tid in tu_ids:
                    tinfo = tu_lookup.get(tid)
                    if tinfo:
                        tu_names.append(tinfo["name"])
                        tu_len += tinfo["length_m"]
                        tu_wt += tinfo["weight_t"]
                        if tinfo["motor_desc"] and tinfo["motor_desc"] != "—":
                            motor_types.add(tinfo["motor_desc"])

                cid = str(ma.get("coachid") or "0")
                cinfo = coach_lookup.get(cid)
                c_len = float(ma.get("coachestotallength") or 0)
                c_wt = float(ma.get("coachestotalweight") or 0)
                vmax_kmh = float(ma.get("maxspeed") or 0)
                prof_id = str(ma.get("profile") or "0")
                profile_train_counts[prof_id] = profile_train_counts.get(prof_id, 0) + 1

                model_trains.append({
                    "id": int(r["entity_id"]) if str(r["entity_id"]).isdigit() else r["entity_id"],
                    "name": r["name"],
                    "publicname": ma.get("publicname") or r["name"],
                    "desc": ma.get("desc") or "",
                    "maxspeed_kmh": vmax_kmh,
                    "maxspeed_mph": round(vmax_kmh * 0.621371, 1),
                    "retardation": float(ma.get("retardation") or 0),
                    "profile_id": prof_id,
                    "profile_name": decode_code("veloprofiledesc", prof_id) or "Standard (0)",
                    "traction_summary": " + ".join(tu_names) if tu_names else "—",
                    "propulsion": "/".join(sorted(motor_types)) if motor_types else "—",
                    "coach_name": cinfo["name"] if cinfo else ("—" if c_len == 0 else "Custom Trailing"),
                    "coaches_length_m": c_len,
                    "coaches_weight_t": c_wt,
                    "total_length_m": round(tu_len + c_len, 1),
                    "total_weight_t": round(tu_wt + c_wt, 1),
                    "atc": ma.get("atc") == "true",
                })

            # 4. Velocity Profiles (<veloprofiledesc>)
            lookup = load_lookup_cache()
            velocity_profiles = []
            for pid, pinfo in sorted(lookup.get("veloprofiledesc", {}).items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999):
                if pid == "0":
                    continue
                velocity_profiles.append({
                    "id": pid,
                    "code": pinfo["label"],
                    "description": pinfo["vanilla_text"] or pinfo["description"] or pinfo["label"],
                    "timing_load_count": profile_train_counts.get(pid, 0),
                })

            # 5. Train Service Categories (<traintype>)
            train_types = []
            for tid, tinfo in sorted(lookup.get("traintype", {}).items(), key=lambda x: x[1]["label"] or ""):
                if tid in ("0", "181"):
                    continue
                train_types.append({
                    "id": tid,
                    "code": tinfo["label"],
                    "description": tinfo["description"] or tinfo["vanilla_text"] or "",
                })

            # 6. Station Bank Timetable Corridors (<stationbank>) + O(1) station_to_banks index
            cur.execute(
                "SELECT entity_id, name, raw_attrs, raw_children FROM raw_tps_elements WHERE tag = 'stationbank' ORDER BY name ASC"
            )
            station_banks = []
            sec_to_bank = {}
            for r in cur.fetchall():
                sa = r["raw_attrs"] or {}
                bid = int(r["entity_id"]) if str(r["entity_id"]).isdigit() else r["entity_id"]
                bobj = {
                    "id": bid,
                    "code": r["name"],
                    "description": sa.get("description") or "",
                    "section_count": len(r["raw_children"] or []),
                    "last_modified": sa.get("lastmodified") or "",
                }
                station_banks.append(bobj)
                for ch in r["raw_children"] or []:
                    if ch.get("tag") == "stationbankid":
                        s_id = str(ch.get("attrs", {}).get("stationbankid") or "")
                        if s_id:
                            sec_to_bank[s_id] = bobj

            cur.execute(
                "SELECT entity_id, name, raw_attrs, raw_children FROM raw_tps_elements WHERE tag = 'stationbanksection'"
            )
            station_to_banks = {}
            for srow in cur.fetchall():
                sec_id = str(srow["entity_id"])
                sa = srow["raw_attrs"] or {}
                parent_bank = sec_to_bank.get(sec_id)
                b_entry = {
                    "bank_id": parent_bank["id"] if parent_bank else (int(sec_id) if sec_id.isdigit() else sec_id),
                    "section_id": sec_id,
                    "code": parent_bank["code"] if parent_bank else srow["name"],
                    "description": (parent_bank["description"] if parent_bank else sa.get("description")) or "",
                }
                seen_st_in_sec = set()
                for ch in srow["raw_children"] or []:
                    if ch.get("tag") == "stationbankstation":
                        st_id = str(ch.get("attrs", {}).get("id") or "")
                        if st_id and st_id not in seen_st_in_sec:
                            seen_st_in_sec.add(st_id)
                            lst = station_to_banks.setdefault(st_id, [])
                            if len(lst) < 25:
                                lst.append(b_entry)

            # 7. Timing Point Role & Category distribution
            cur.execute("SELECT station_type, COUNT(*) AS cnt FROM stations WHERE station_id > 0 GROUP BY station_type ORDER BY cnt DESC")
            timing_roles = [
                {
                    "type_id": r["station_type"],
                    "label": decode_code("stationtypedesc", r["station_type"]) or f"Type {r['station_type']}",
                    "count": r["cnt"],
                }
                for r in cur.fetchall()
            ]
            cur.execute("SELECT station_category, COUNT(*) AS cnt FROM stations WHERE station_id > 0 GROUP BY station_category ORDER BY cnt DESC")
            planning_categories = [
                {
                    "category_id": r["station_category"],
                    "label": decode_code("stationcategorydesc", r["station_category"]) or f"Category {r['station_category']}",
                    "count": r["cnt"],
                }
                for r in cur.fetchall()
            ]

    TIMING_CATALOG_CACHE = {
        "counts": {
            "model_trains": len(model_trains),
            "traction_units": len(traction_units),
            "coaches": len(coaches),
            "velocity_profiles": len(velocity_profiles),
            "train_types": len(train_types),
            "station_banks": len(station_banks),
        },
        "model_trains": model_trains,
        "traction_units": traction_units,
        "coaches": coaches,
        "velocity_profiles": velocity_profiles,
        "train_types": train_types,
        "station_banks": station_banks,
        "timing_roles": timing_roles,
        "planning_categories": planning_categories,
        "station_to_banks": station_to_banks,
    }
    return TIMING_CATALOG_CACHE


@app.get("/api/timing-loads/catalog")
def api_timing_loads_catalog():
    cat = get_timing_catalog()
    return {k: v for k, v in cat.items() if k != "station_to_banks"}


@app.get("/api/timing-loads/list")
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


@app.get("/api/catalog")
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


@app.get("/api/catalog/{tag}")
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


# ==============================================================================
# Network Fusion (GeoFurlong / Alan Morrison) Endpoints
# ==============================================================================

@app.get("/api/network-fusion/status")
def api_network_fusion_status():
    ensure_network_fusion_tables_and_ingest(DATABASE_URL, NF_DIR)
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT value FROM ingestion_meta WHERE key = 'network_fusion_complete'")
            row = cur.fetchone()
            meta = row["value"] if row else {"completed": False}

            cur.execute("SELECT COUNT(*) AS total_points FROM network_fusion_points")
            pt_count = cur.fetchone()["total_points"]

            cur.execute("SELECT COUNT(*) AS total_curv FROM network_fusion_curvature")
            curv_count = cur.fetchone()["total_curv"]

            cur.execute("SELECT COUNT(DISTINCT elr) AS elrs FROM network_fusion_points")
            elrs_count = cur.fetchone()["elrs"]

            return {
                "status": "ready" if meta.get("completed") else "pending",
                "meta": meta,
                "points_count": pt_count,
                "curvature_count": curv_count,
                "elrs_count": elrs_count,
            }


# ==============================================================================
# NWR_TrackModel (OSGB36 British National Grid Geospatial Model) Endpoints
# ==============================================================================

@app.get("/api/nwr/overview")
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


@app.get("/api/nwr/viewport")
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


@app.get("/api/nwr/search")
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


@app.get("/api/nwr/pick")
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
                candidates.append({
                    "type": "nwr_node",
                    "id": r["fid"],
                    "label": f"Node {r['asset_id']} (Valency {r['valency']})",
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


def find_tps_cross_links(cur, elr: str, min_m: Optional[float] = None, max_m: Optional[float] = None):
    """Relational bridge from NWR_TrackModel (ELR + mileage in metres) to TPS (XML_p.xml)."""
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

    tps_stations = []
    tps_nodes_count = 0
    tps_edges = []
    tps_signals = []
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


def mile_yards_to_total_yards(val: float) -> int:
    """Convert mile.yards float (e.g. 28.1440) to integer total yards (50720)."""
    if val is None:
        return 0
    v = abs(float(val))
    miles = int(v)
    yards = int(round((v - miles) * 10000.0))
    return miles * 1760 + yards


def find_network_fusion_for_elr(cur, elr: str, min_yd: Optional[int] = None, max_yd: Optional[int] = None):
    """Fetch and summarize Network Fusion (GeoFurlong) points and curvature for an ELR corridor."""
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
            "tightest_curve": {
                "class": tightest_c["radius_avg_class"] if tightest_c else None,
                "mileage": f"{tightest_c['mileage_from']} → {tightest_c['mileage_to']}" if tightest_c else None,
                "radius_min_m": tightest_c["radius_min_m"] if tightest_c else None,
            } if tightest_c else None,
            "nearest_places": list(dict.fromkeys(places))[:3],
            "postcodes": list(dict.fromkeys(postcodes))[:3],
            "bedrocks": list(dict.fromkeys(bedrocks))[:2],
        }

    return {
        "summary": summary,
        "points": points,
        "curvature": curvature,
    }


@app.get("/api/network-fusion/profile")
def api_network_fusion_profile(
    elr: str = Query(...),
    start_yards: Optional[int] = Query(None),
    end_yards: Optional[int] = Query(None),
):
    elr_clean = elr.strip().upper()
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            return find_network_fusion_for_elr(cur, elr_clean, start_yards, end_yards)


@app.get("/api/network-fusion/nearest")
def api_network_fusion_nearest(
    x: float = Query(...),
    y: float = Query(...),
    radius: float = Query(2500.0),
):
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT pk, elr, total_yards, mileage, easting, northing,
                       elevation_m, place_name, postcode, admin_area, county_district,
                       country, nr_region, bedrock, deposit, annual_rainfall_mm,
                       min_air_temperature_c, max_air_temperature_c, olc,
                       SQRT(POWER(easting - %s, 2) + POWER(northing - %s, 2)) AS dist_m
                FROM network_fusion_points
                WHERE easting BETWEEN %s AND %s
                  AND northing BETWEEN %s AND %s
                ORDER BY dist_m ASC
                LIMIT 1
                """,
                (x, y, x - radius, x + radius, y - radius, y + radius),
            )
            row = cur.fetchone()
            return {"point": row}


@app.get("/api/nwr/asset/{asset_type}/{asset_id}")
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


static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(static_dir, "index.html"))

