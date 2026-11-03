"""Network Fusion (GeoFurlong / Alan Morrison) Data Ingestion Pipeline.

Ingests all 7 CSV datasets (nf_core, nf_tracks, nf_elevation, nf_climate,
nf_geology, nf_gazetteer, nf_curvature) into PostgreSQL without discarding
any attributes.
"""

import csv
import os
import tarfile
import threading
import time
import zipfile
from typing import Optional, Tuple, Dict

import orjson
import psycopg

_NF_LOCK = threading.Lock()
_NF_COMPLETE = False


def _to_int(val: Optional[str]) -> Optional[int]:
    """Convert string to integer or return None."""
    if val is None or val == "":
        return None
    try:
        return int(round(float(val)))
    except (ValueError, TypeError):
        return None


def _to_float(val: Optional[str]) -> Optional[float]:
    """Convert string to float or return None."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _to_str(val: Optional[str]) -> Optional[str]:
    """Convert string to non-empty trimmed string or None."""
    if val is None:
        return None
    stripped = str(val).strip()
    return stripped or None


def resolve_network_fusion_dir(nf_dir: str) -> str:
    """Resolve the directory containing Network Fusion CSV files.

    Supports direct folder, parent directory, zip archive, or tar archive.
    """
    candidates = [
        nf_dir,
        "/data/network_fusion",
        "/data/nf",
        "./network_fusion_2026-10-26",
        "./network_fusion_2026-10-26.zip",
    ]
    for cand in candidates:
        if not cand or not os.path.exists(cand):
            continue
        if os.path.isdir(cand):
            if os.path.isfile(os.path.join(cand, "nf_core.csv")):
                return cand
            for root, _, files in os.walk(cand):
                if "nf_core.csv" in files:
                    return root
                for fname in files:
                    if fname.lower().startswith("network_fusion") and fname.lower().endswith((".zip", ".tar.gz", ".tgz")):
                        res = resolve_network_fusion_dir(os.path.join(root, fname))
                        if res:
                            return res
        elif os.path.isfile(cand):
            extract_dir = "/tmp/nf_extracted"
            os.makedirs(extract_dir, exist_ok=True)
            lower = cand.lower()
            if lower.endswith(".zip"):
                with zipfile.ZipFile(cand, "r") as zf:
                    zf.extractall(extract_dir)
                return resolve_network_fusion_dir(extract_dir)
            if lower.endswith((".tar.gz", ".tgz", ".tar")):
                with tarfile.open(cand, "r:*") as tf:
                    tf.extractall(extract_dir)
                return resolve_network_fusion_dir(extract_dir)
    return ""


def ensure_network_fusion_tables_and_ingest(db_url: str, nf_dir: str) -> bool:
    """Create Network Fusion tables and ingest all 7 CSV files without data loss."""
    global _NF_COMPLETE
    if _NF_COMPLETE:
        return True

    with _NF_LOCK:
        if _NF_COMPLETE:
            return True

        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT value FROM ingestion_meta WHERE key = 'network_fusion_complete'"
                )
                row = cur.fetchone()
                if row and row[0].get("completed") is True:
                    _NF_COMPLETE = True
                    return True

            resolved_dir = resolve_network_fusion_dir(nf_dir)
            if not resolved_dir:
                return False
            nf_dir = resolved_dir

            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS network_fusion_points (
                        pk BIGSERIAL PRIMARY KEY,
                        elr TEXT NOT NULL,
                        total_yards INTEGER NOT NULL,
                        mileage TEXT,
                        easting DOUBLE PRECISION,
                        northing DOUBLE PRECISION,
                        longitude DOUBLE PRECISION,
                        latitude DOUBLE PRECISION,
                        os_grid_ref TEXT,
                        linear_accuracy_m INTEGER,
                        olc TEXT,
                        track_count_primary INTEGER,
                        track_count_all INTEGER,
                        track_ids_primary TEXT,
                        track_ids_crossover TEXT,
                        track_ids_siding TEXT,
                        elevation_m DOUBLE PRECISION,
                        min_air_temperature_c DOUBLE PRECISION,
                        max_air_temperature_c DOUBLE PRECISION,
                        mean_wind_speed_ms DOUBLE PRECISION,
                        annual_rainfall_mm INTEGER,
                        material TEXT,
                        deposit TEXT,
                        bedrock TEXT,
                        nr_region TEXT,
                        country TEXT,
                        admin_area TEXT,
                        county_district TEXT,
                        place_name TEXT,
                        place_distance_m INTEGER,
                        postcode TEXT,
                        postcode_distance_m INTEGER
                    );

                    CREATE TABLE IF NOT EXISTS network_fusion_curvature (
                        pk BIGSERIAL PRIMARY KEY,
                        elr TEXT NOT NULL,
                        total_yards_from INTEGER NOT NULL,
                        total_yards_to INTEGER NOT NULL,
                        mileage_from TEXT,
                        mileage_to TEXT,
                        radius_min_m INTEGER,
                        radius_max_m INTEGER,
                        radius_avg_m INTEGER,
                        radius_min_class TEXT,
                        radius_max_class TEXT,
                        radius_avg_class TEXT,
                        x0 DOUBLE PRECISION,
                        y0 DOUBLE PRECISION,
                        x1 DOUBLE PRECISION,
                        y1 DOUBLE PRECISION,
                        coords JSONB
                    );

                    TRUNCATE TABLE network_fusion_points, network_fusion_curvature RESTART IDENTITY;
                """)
            conn.commit()

            t0 = time.time()
            coord_lookup: Dict[Tuple[str, int], Tuple[float, float]] = {}

            # 1. Ingest 6 unified point CSV files (180,024 rows in exact lockstep)
            core_path = os.path.join(nf_dir, "nf_core.csv")
            tracks_path = os.path.join(nf_dir, "nf_tracks.csv")
            elev_path = os.path.join(nf_dir, "nf_elevation.csv")
            clim_path = os.path.join(nf_dir, "nf_climate.csv")
            geol_path = os.path.join(nf_dir, "nf_geology.csv")
            gaz_path = os.path.join(nf_dir, "nf_gazetteer.csv")

            with open(core_path, "r", encoding="utf-8") as f_core, \
                 open(tracks_path, "r", encoding="utf-8") as f_tracks, \
                 open(elev_path, "r", encoding="utf-8") as f_elev, \
                 open(clim_path, "r", encoding="utf-8") as f_clim, \
                 open(geol_path, "r", encoding="utf-8") as f_geol, \
                 open(gaz_path, "r", encoding="utf-8") as f_gaz:

                r_core = csv.reader(f_core)
                r_tracks = csv.reader(f_tracks)
                r_elev = csv.reader(f_elev)
                r_clim = csv.reader(f_clim)
                r_geol = csv.reader(f_geol)
                r_gaz = csv.reader(f_gaz)

                # Skip header rows
                next(r_core)
                next(r_tracks)
                next(r_elev)
                next(r_clim)
                next(r_geol)
                next(r_gaz)

                points_rows = []
                for row_c, row_t, row_e, row_cl, row_ge, row_ga in zip(
                    r_core, r_tracks, r_elev, r_clim, r_geol, r_gaz
                ):
                    elr = row_c[0]
                    total_yards = _to_int(row_c[1]) or 0
                    mileage = _to_str(row_c[2])
                    easting = _to_float(row_c[3])
                    northing = _to_float(row_c[4])
                    longitude = _to_float(row_c[5])
                    latitude = _to_float(row_c[6])
                    os_grid_ref = _to_str(row_c[7])
                    linear_accuracy_m = _to_int(row_c[8])
                    olc = _to_str(row_c[9])

                    if easting is not None and northing is not None:
                        coord_lookup[(elr, total_yards)] = (easting, northing)

                    track_count_primary = _to_int(row_t[3])
                    track_count_all = _to_int(row_t[4])
                    track_ids_primary = _to_str(row_t[5])
                    track_ids_crossover = _to_str(row_t[6])
                    track_ids_siding = _to_str(row_t[7])

                    elevation_m = _to_float(row_e[3])

                    min_air_temperature_c = _to_float(row_cl[3])
                    max_air_temperature_c = _to_float(row_cl[4])
                    mean_wind_speed_ms = _to_float(row_cl[5])
                    annual_rainfall_mm = _to_int(row_cl[6])

                    material = _to_str(row_ge[3])
                    deposit = _to_str(row_ge[4])
                    bedrock = _to_str(row_ge[5])

                    nr_region = _to_str(row_ga[3])
                    country = _to_str(row_ga[4])
                    admin_area = _to_str(row_ga[5])
                    county_district = _to_str(row_ga[6])
                    place_name = _to_str(row_ga[7])
                    place_distance_m = _to_int(row_ga[8])
                    postcode = _to_str(row_ga[9])
                    postcode_distance_m = _to_int(row_ga[10])

                    points_rows.append((
                        elr,
                        total_yards,
                        mileage,
                        easting,
                        northing,
                        longitude,
                        latitude,
                        os_grid_ref,
                        linear_accuracy_m,
                        olc,
                        track_count_primary,
                        track_count_all,
                        track_ids_primary,
                        track_ids_crossover,
                        track_ids_siding,
                        elevation_m,
                        min_air_temperature_c,
                        max_air_temperature_c,
                        mean_wind_speed_ms,
                        annual_rainfall_mm,
                        material,
                        deposit,
                        bedrock,
                        nr_region,
                        country,
                        admin_area,
                        county_district,
                        place_name,
                        place_distance_m,
                        postcode,
                        postcode_distance_m,
                    ))

            with conn.cursor() as cur:
                with cur.copy(
                    """COPY network_fusion_points (
                        elr, total_yards, mileage, easting, northing, longitude, latitude,
                        os_grid_ref, linear_accuracy_m, olc, track_count_primary, track_count_all,
                        track_ids_primary, track_ids_crossover, track_ids_siding, elevation_m,
                        min_air_temperature_c, max_air_temperature_c, mean_wind_speed_ms,
                        annual_rainfall_mm, material, deposit, bedrock, nr_region, country,
                        admin_area, county_district, place_name, place_distance_m, postcode,
                        postcode_distance_m
                    ) FROM STDIN"""
                ) as copy:
                    for r in points_rows:
                        copy.write_row(r)
            conn.commit()
            points_count = len(points_rows)
            points_rows.clear()

            # 2. Ingest Curvature segments (178,912 rows)
            curv_path = os.path.join(nf_dir, "nf_curvature.csv")
            curv_rows = []
            with open(curv_path, "r", encoding="utf-8") as f_curv:
                r_curv = csv.reader(f_curv)
                next(r_curv)  # Skip header
                for row in r_curv:
                    elr = row[0]
                    y_from = _to_int(row[1]) or 0
                    y_to = _to_int(row[2]) or 0
                    m_from = _to_str(row[3])
                    m_to = _to_str(row[4])
                    r_min = _to_int(row[5])
                    r_max = _to_int(row[6])
                    r_avg = _to_int(row[7])
                    r_min_cls = _to_str(row[8])
                    r_max_cls = _to_str(row[9])
                    r_avg_cls = _to_str(row[10])

                    p0 = coord_lookup.get((elr, y_from))
                    p1 = coord_lookup.get((elr, y_to))
                    x0 = p0[0] if p0 else None
                    y0 = p0[1] if p0 else None
                    x1 = p1[0] if p1 else None
                    y1 = p1[1] if p1 else None
                    coords_json = (
                        orjson.dumps([[x0, y0], [x1, y1]]).decode("utf-8")
                        if (x0 is not None and y0 is not None and x1 is not None and y1 is not None)
                        else None
                    )

                    curv_rows.append((
                        elr,
                        y_from,
                        y_to,
                        m_from,
                        m_to,
                        r_min,
                        r_max,
                        r_avg,
                        r_min_cls,
                        r_max_cls,
                        r_avg_cls,
                        x0,
                        y0,
                        x1,
                        y1,
                        coords_json,
                    ))

            with conn.cursor() as cur:
                with cur.copy(
                    """COPY network_fusion_curvature (
                        elr, total_yards_from, total_yards_to, mileage_from, mileage_to,
                        radius_min_m, radius_max_m, radius_avg_m, radius_min_class,
                        radius_max_class, radius_avg_class, x0, y0, x1, y1, coords
                    ) FROM STDIN"""
                ) as copy:
                    for cr in curv_rows:
                        copy.write_row(cr)
            conn.commit()
            curv_count = len(curv_rows)
            curv_rows.clear()
            coord_lookup.clear()

            # 3. Create High-Performance Indexes
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_nf_points_elr_yards ON network_fusion_points (elr, total_yards);
                    CREATE INDEX IF NOT EXISTS idx_nf_points_coords ON network_fusion_points (easting, northing);
                    CREATE INDEX IF NOT EXISTS idx_nf_points_elr ON network_fusion_points (elr);
                    CREATE INDEX IF NOT EXISTS idx_nf_points_postcode ON network_fusion_points (postcode);
                    CREATE INDEX IF NOT EXISTS idx_nf_points_elevation ON network_fusion_points (elevation_m);

                    CREATE INDEX IF NOT EXISTS idx_nf_curv_elr_yards ON network_fusion_curvature (elr, total_yards_from, total_yards_to);
                    CREATE INDEX IF NOT EXISTS idx_nf_curv_elr ON network_fusion_curvature (elr);
                    CREATE INDEX IF NOT EXISTS idx_nf_curv_radius_avg ON network_fusion_curvature (radius_avg_m);
                    CREATE INDEX IF NOT EXISTS idx_nf_curv_bbox ON network_fusion_curvature (x0, x1, y0, y1);
                """)
                cur.execute("""
                    INSERT INTO ingestion_meta (key, value)
                    VALUES (
                        'network_fusion_complete',
                        jsonb_build_object(
                            'completed', true,
                            'points_count', %s,
                            'curvature_count', %s,
                            'duration_seconds', round((extract(epoch from clock_timestamp()) - %s)::numeric, 2)
                        )
                    )
                    ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value;
                """, (points_count, curv_count, t0))
            conn.commit()

            _NF_COMPLETE = True
            return True
