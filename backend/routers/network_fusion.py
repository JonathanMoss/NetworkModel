"""Network Fusion (GeoFurlong) API endpoints.

Provides ingestion status, corridor elevation/curvature profiles, and nearest
spatial point lookups against ``network_fusion_points`` and
``network_fusion_curvature``.
"""

from typing import Any, Optional

from fastapi import APIRouter, Query
from psycopg.rows import dict_row

from config import DATABASE_URL, NF_DIR, get_conn
from ingest_network_fusion import ensure_network_fusion_tables_and_ingest
from track_matcher import find_network_fusion_for_elr

router = APIRouter(prefix="/api/network-fusion", tags=["network-fusion"])


@router.get("/status")
def api_network_fusion_status() -> dict[str, Any]:
    """Return ingestion status and row counts for Network Fusion tables.

    Triggers lazy ingestion if the tables have not yet been populated.

    Returns:
        Dictionary with ``status`` (``"ready"`` or ``"pending"``), ``meta``,
        ``points_count``, ``curvature_count``, and ``elrs_count``.
    """
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


@router.get("/profile")
def api_network_fusion_profile(
    elr: str = Query(...),
    start_yards: Optional[int] = Query(None),
    end_yards: Optional[int] = Query(None),
) -> dict[str, Any]:
    """Fetch Network Fusion elevation/climate points and curvature for an ELR.

    Args:
        elr: Engineer's Line Reference code (case-insensitive).
        start_yards: Optional lower chainage bound in total yards.
        end_yards: Optional upper chainage bound in total yards.

    Returns:
        Corridor summary, points list, and curvature segments list.
    """
    elr_clean = elr.strip().upper()
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            return find_network_fusion_for_elr(cur, elr_clean, start_yards, end_yards)


@router.get("/nearest")
def api_network_fusion_nearest(
    x: float = Query(...),
    y: float = Query(...),
    radius: float = Query(2500.0),
) -> dict[str, Any]:
    """Find the single nearest Network Fusion point to an OSGB36 coordinate.

    Args:
        x: OSGB36 Easting in metres.
        y: OSGB36 Northing in metres.
        radius: Square bounding-box half-width in metres (default 2,500m).

    Returns:
        Dictionary containing ``point`` (nearest row dict or ``None``).
    """
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

