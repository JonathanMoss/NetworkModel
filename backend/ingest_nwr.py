import math
import os
import struct
import tarfile
import threading
import time
import zipfile
import orjson
import psycopg

TRACK_ID_APPENDIX_A = {
    "11": "UP MAIN FAST",
    "12": "UP SLOW",
    "13": "UP GOODS",
    "14": "UP SINGLE",
    "15": "UP LOOP",
    "16": "UP TERMINAL",
    "17": "UP CROSSOVER",
    "18": "UP OTHER/ENGINE",
    "19": "UP SIDING",
    "21": "DOWN MAIN FAST",
    "22": "DOWN SLOW",
    "23": "DOWN GOODS",
    "24": "DOWN SINGLE",
    "25": "DOWN LOOP",
    "26": "DOWN TERMINAL",
    "27": "DOWN CROSSOVER",
    "28": "DOWN OTHER/ENGINE",
    "29": "DOWN SIDING",
    "31": "REVERSIBLE/BI-DIRECTIONAL MAIN FAST",
    "32": "REVERSIBLE/BI-DIRECTIONAL SLOW",
    "33": "REVERSIBLE/BI-DIRECTIONAL GOODS",
    "34": "REVERSIBLE/BI-DIRECTIONAL SINGLE",
    "35": "REVERSIBLE/BI-DIRECTIONAL LOOP",
    "36": "REVERSIBLE/BI-DIRECTIONAL TERMINAL",
    "37": "REVERSIBLE/BI-DIRECTIONAL CROSSOVER",
    "38": "REVERSIBLE/BI-DIRECTIONAL OTHER/ENGINE",
    "39": "REVERSIBLE/BI-DIRECTIONAL SIDING",
}

VALENCY_DESC = {
    1: "Tracks start/end (Buffer stop or boundary)",
    2: "Pseudo Node (Attributional change only, no direction change)",
    3: "Switch / Turnout (Directional change possible)",
    4: "Centre of Diamond Crossing (No directional change)",
}


def decode_track_id(track_id: str) -> str:
    if not track_id:
        return "Unknown"
    prefix = track_id[:2]
    base = TRACK_ID_APPENDIX_A.get(prefix)
    if base:
        return f"{base} ({track_id})"
    d1 = {"1": "UP", "2": "DOWN", "3": "REVERSIBLE/BI-DIRECTIONAL", "4": "NON-DIRECTIONAL"}.get(track_id[0], "OTHER")
    return f"{d1} TRACK ({track_id})"


def mile_yards_to_meters(val: float) -> float:
    sign = -1.0 if val < 0 else 1.0
    v = abs(float(val))
    miles = int(v)
    yards = round((v - miles) * 10000.0)
    return round(sign * (miles * 1760.0 + yards) * 0.9144, 2)


def format_mile_yards(val: float) -> str:
    if val is None:
        return "—"
    sign = "-" if val < 0 else ""
    v = abs(float(val))
    miles = int(v)
    yards = int(round((v - miles) * 10000.0))
    chains = round(yards / 22.0, 1)
    return f"{sign}{miles}m {yards:04d}yd ({sign}{miles}m {chains:04.1f}ch)"


def iter_shapefile(shp_path: str, dbf_path: str):
    """Pure-Python binary ESRI .shp + .dbf streaming reader."""
    with open(shp_path, "rb") as sf, open(dbf_path, "rb") as df:
        sf.seek(100)
        dhdr = df.read(32)
        num_records, header_len, record_len = struct.unpack("<4xIHH20x", dhdr)
        fields = []
        while True:
            fld = df.read(32)
            if not fld or fld[0] == 0x0D:
                break
            name = fld[:11].split(b"\x00")[0].decode("latin1")
            ftype = chr(fld[11])
            flen = fld[16]
            fields.append((name, ftype, flen))
        df.seek(header_len)

        for fid in range(1, num_records + 1):
            rec_hdr = sf.read(8)
            if not rec_hdr:
                break
            _, content_len = struct.unpack(">ii", rec_hdr)
            content = sf.read(content_len * 2)
            stype = struct.unpack("<i", content[:4])[0]

            drec = df.read(record_len)
            attrs = {}
            off = 1
            for name, ftype, flen in fields:
                raw_v = drec[off : off + flen].decode("latin1").strip()
                if ftype in ("F", "N"):
                    try:
                        attrs[name] = float(raw_v) if "." in raw_v else int(raw_v)
                    except ValueError:
                        attrs[name] = None
                else:
                    attrs[name] = raw_v
                off += flen

            if stype == 1:  # Point
                x, y = struct.unpack("<dd", content[4:20])
                geom = {"type": "Point", "x": round(x, 3), "y": round(y, 3)}
            elif stype == 3:  # PolyLine
                xmin, ymin, xmax, ymax, num_parts, num_points = struct.unpack(
                    "<4dii", content[4:44]
                )
                parts_idx = struct.unpack(f"<{num_parts}i", content[44 : 44 + 4 * num_parts])
                pts_off = 44 + 4 * num_parts
                all_pts = struct.unpack(
                    f"<{num_points * 2}d",
                    content[pts_off : pts_off + 16 * num_points],
                )
                parts = []
                for p_i in range(num_parts):
                    start_idx = parts_idx[p_i]
                    end_idx = parts_idx[p_i + 1] if p_i + 1 < num_parts else num_points
                    part_coords = [
                        [round(all_pts[j * 2], 2), round(all_pts[j * 2 + 1], 2)]
                        for j in range(start_idx, end_idx)
                    ]
                    parts.append(part_coords)
                geom = {
                    "type": "PolyLine",
                    "bbox": [round(xmin, 2), round(ymin, 2), round(xmax, 2), round(ymax, 2)],
                    "parts": parts,
                }
            else:
                geom = {"type": "Null"}

            yield fid, attrs, geom


_NWR_LOCK = threading.Lock()
_NWR_COMPLETE = False


def resolve_nwr_shapefile_dir(nwr_dir: str) -> str:
    """Resolve the directory containing NWR_TrackNodes.shp from a directory, subdirectory, or archive."""
    candidates = [nwr_dir, "/data/nwr", "/data/NWR_TrackModel"]
    for cand in candidates:
        if not cand or not os.path.exists(cand):
            continue
        if os.path.isdir(cand):
            if os.path.isfile(os.path.join(cand, "NWR_TrackNodes.shp")):
                return cand
            archives = []
            for root, _, files in os.walk(cand):
                if "NWR_TrackNodes.shp" in files:
                    return root
                for fname in files:
                    if fname.lower().endswith((".zip", ".tar.gz", ".tgz", ".tar")):
                        archives.append(os.path.join(root, fname))
            for arch in archives:
                resolved = resolve_nwr_shapefile_dir(arch)
                if resolved:
                    return resolved
        elif os.path.isfile(cand):
            extract_dir = "/tmp/nwr_extracted"
            os.makedirs(extract_dir, exist_ok=True)
            lower = cand.lower()
            if lower.endswith(".zip"):
                with zipfile.ZipFile(cand, "r") as zf:
                    zf.extractall(extract_dir)
                return resolve_nwr_shapefile_dir(extract_dir)
            if lower.endswith((".tar.gz", ".tgz", ".tar")):
                with tarfile.open(cand, "r:*") as tf:
                    tf.extractall(extract_dir)
                return resolve_nwr_shapefile_dir(extract_dir)
    return ""


def ensure_nwr_tables_and_ingest(db_url: str, nwr_dir: str):
    global _NWR_COMPLETE
    if _NWR_COMPLETE:
        return True

    with _NWR_LOCK:
        if _NWR_COMPLETE:
            return True
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT value FROM ingestion_meta WHERE key = 'nwr_trackmodel_complete'"
                )
                row = cur.fetchone()
                if row and row[0].get("completed") is True:
                    _NWR_COMPLETE = True
                    return True

            resolved_dir = resolve_nwr_shapefile_dir(nwr_dir)
            if not resolved_dir:
                return False
            nwr_dir = resolved_dir

            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS nwr_track_nodes (
                        fid INTEGER PRIMARY KEY,
                        asset_id TEXT UNIQUE,
                        valency INTEGER,
                        version DOUBLE PRECISION,
                        extracted TEXT,
                        x DOUBLE PRECISION NOT NULL,
                        y DOUBLE PRECISION NOT NULL,
                        raw_attrs JSONB NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS nwr_track_centrelines (
                        fid INTEGER PRIMARY KEY,
                        asset_id TEXT UNIQUE,
                        elr TEXT,
                        track_id TEXT,
                        start_mile_yards DOUBLE PRECISION,
                        end_mile_yards DOUBLE PRECISION,
                        start_meters DOUBLE PRECISION,
                        end_meters DOUBLE PRECISION,
                        length_meters DOUBLE PRECISION,
                        version DOUBLE PRECISION,
                        owner TEXT,
                        extracted TEXT,
                        start_node_id TEXT,
                        end_node_id TEXT,
                        min_x DOUBLE PRECISION,
                        max_x DOUBLE PRECISION,
                        min_y DOUBLE PRECISION,
                        max_y DOUBLE PRECISION,
                        coords JSONB NOT NULL,
                        raw_attrs JSONB NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS nwr_elrs (
                        fid INTEGER PRIMARY KEY,
                        asset_id TEXT UNIQUE,
                        elr TEXT,
                        start_mile_yards DOUBLE PRECISION,
                        end_mile_yards DOUBLE PRECISION,
                        start_meters DOUBLE PRECISION,
                        end_meters DOUBLE PRECISION,
                        version DOUBLE PRECISION,
                        extracted TEXT,
                        min_x DOUBLE PRECISION,
                        max_x DOUBLE PRECISION,
                        min_y DOUBLE PRECISION,
                        max_y DOUBLE PRECISION,
                        parts JSONB NOT NULL,
                        raw_attrs JSONB NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS nwr_waymarks (
                        fid INTEGER PRIMARY KEY,
                        asset_id TEXT UNIQUE,
                        elr TEXT,
                        unit TEXT,
                        value DOUBLE PRECISION,
                        meters DOUBLE PRECISION,
                        version DOUBLE PRECISION,
                        extracted TEXT,
                        x DOUBLE PRECISION NOT NULL,
                        y DOUBLE PRECISION NOT NULL,
                        raw_attrs JSONB NOT NULL
                    );
                """)

                t0 = time.time()
                cur.execute("""
                    TRUNCATE TABLE
                        nwr_track_nodes, nwr_track_centrelines, nwr_elrs, nwr_waymarks
                    RESTART IDENTITY;
                """)
            conn.commit()

            # 1. Ingest NWR_TrackNodes.shp & build coordinate lookup for link endpoint matching
            node_coord_lookup = {}
            nodes_shp = os.path.join(nwr_dir, "NWR_TrackNodes.shp")
            nodes_dbf = os.path.join(nwr_dir, "NWR_TrackNodes.dbf")
            node_rows = []
            for fid, attrs, geom in iter_shapefile(nodes_shp, nodes_dbf):
                x, y = geom["x"], geom["y"]
                aid = attrs.get("ASSET_ID", "")
                valency = int(round(float(attrs.get("VALENCY") or 0)))
                node_coord_lookup[(round(x, 2), round(y, 2))] = aid
                node_rows.append((
                    fid,
                    aid,
                    valency,
                    float(attrs.get("VERSION") or 0.0),
                    str(attrs.get("EXTRACTED") or ""),
                    x,
                    y,
                    orjson.dumps(attrs).decode("utf-8"),
                ))

            with conn.cursor() as cur:
                with cur.copy(
                    """COPY nwr_track_nodes (
                        fid, asset_id, valency, version, extracted, x, y, raw_attrs
                    ) FROM STDIN"""
                ) as copy:
                    for r in node_rows:
                        copy.write_row(r)
            conn.commit()
            node_rows.clear()

            # 2. Ingest NWR_TrackCentreLines.shp (linking start_node_id and end_node_id via exact geometry match)
            tcl_shp = os.path.join(nwr_dir, "NWR_TrackCentreLines.shp")
            tcl_dbf = os.path.join(nwr_dir, "NWR_TrackCentreLines.dbf")
            tcl_rows = []
            for fid, attrs, geom in iter_shapefile(tcl_shp, tcl_dbf):
                parts = geom.get("parts", [])
                flat_coords = parts[0] if len(parts) == 1 else [pt for p in parts for pt in p]
                if flat_coords:
                    p0 = flat_coords[0]
                    p1 = flat_coords[-1]
                    s_node = node_coord_lookup.get((round(p0[0], 2), round(p0[1], 2)))
                    e_node = node_coord_lookup.get((round(p1[0], 2), round(p1[1], 2)))
                    geom_len = sum(
                        math.hypot(flat_coords[i + 1][0] - flat_coords[i][0], flat_coords[i + 1][1] - flat_coords[i][1])
                        for i in range(len(flat_coords) - 1)
                    )
                else:
                    s_node = e_node = None
                    geom_len = 0.0

                s_my = float(attrs.get("START") or 0.0)
                e_my = float(attrs.get("END") or 0.0)
                s_m = mile_yards_to_meters(s_my)
                e_m = mile_yards_to_meters(e_my)
                bbox = geom.get("bbox", [0, 0, 0, 0])

                tcl_rows.append((
                    fid,
                    attrs.get("ASSET_ID", ""),
                    attrs.get("ELR", ""),
                    attrs.get("TRACK_ID", ""),
                    s_my,
                    e_my,
                    s_m,
                    e_m,
                    round(geom_len, 2),
                    float(attrs.get("VERSION") or 0.0),
                    attrs.get("OWNER", ""),
                    str(attrs.get("EXTRACTED") or ""),
                    s_node,
                    e_node,
                    bbox[0],
                    bbox[2],
                    bbox[1],
                    bbox[3],
                    orjson.dumps(flat_coords).decode("utf-8"),
                    orjson.dumps(attrs).decode("utf-8"),
                ))

            with conn.cursor() as cur:
                with cur.copy(
                    """COPY nwr_track_centrelines (
                        fid, asset_id, elr, track_id, start_mile_yards, end_mile_yards,
                        start_meters, end_meters, length_meters, version, owner, extracted,
                        start_node_id, end_node_id, min_x, max_x, min_y, max_y,
                        coords, raw_attrs
                    ) FROM STDIN"""
                ) as copy:
                    for r in tcl_rows:
                        copy.write_row(r)
            conn.commit()
            tcl_rows.clear()

            # 3. Ingest NWR_ELRs.shp
            elr_shp = os.path.join(nwr_dir, "NWR_ELRs.shp")
            elr_dbf = os.path.join(nwr_dir, "NWR_ELRs.dbf")
            elr_rows = []
            for fid, attrs, geom in iter_shapefile(elr_shp, elr_dbf):
                s_my = float(attrs.get("START") or 0.0)
                e_my = float(attrs.get("END") or 0.0)
                bbox = geom.get("bbox", [0, 0, 0, 0])
                elr_rows.append((
                    fid,
                    attrs.get("ASSET_ID", ""),
                    attrs.get("ELR", ""),
                    s_my,
                    e_my,
                    mile_yards_to_meters(s_my),
                    mile_yards_to_meters(e_my),
                    float(attrs.get("VERSION") or 0.0),
                    str(attrs.get("EXTRACTED") or ""),
                    bbox[0],
                    bbox[2],
                    bbox[1],
                    bbox[3],
                    orjson.dumps(geom.get("parts", [])).decode("utf-8"),
                    orjson.dumps(attrs).decode("utf-8"),
                ))

            with conn.cursor() as cur:
                with cur.copy(
                    """COPY nwr_elrs (
                        fid, asset_id, elr, start_mile_yards, end_mile_yards,
                        start_meters, end_meters, version, extracted,
                        min_x, max_x, min_y, max_y, parts, raw_attrs
                    ) FROM STDIN"""
                ) as copy:
                    for r in elr_rows:
                        copy.write_row(r)
            conn.commit()
            elr_rows.clear()

            # 4. Ingest NWR_Waymarks.shp
            wm_shp = os.path.join(nwr_dir, "NWR_Waymarks.shp")
            wm_dbf = os.path.join(nwr_dir, "NWR_Waymarks.dbf")
            wm_rows = []
            for fid, attrs, geom in iter_shapefile(wm_shp, wm_dbf):
                unit = attrs.get("UNIT", "M")
                val = float(attrs.get("VALUE") or 0.0)
                if unit == "K":
                    meters = round(val * 1000.0, 2)
                else:
                    meters = mile_yards_to_meters(val)
                wm_rows.append((
                    fid,
                    attrs.get("ASSET_ID", ""),
                    attrs.get("ELR", ""),
                    unit,
                    val,
                    meters,
                    float(attrs.get("VERSION") or 0.0),
                    str(attrs.get("EXTRACTED") or ""),
                    geom["x"],
                    geom["y"],
                    orjson.dumps(attrs).decode("utf-8"),
                ))

            with conn.cursor() as cur:
                with cur.copy(
                    """COPY nwr_waymarks (
                        fid, asset_id, elr, unit, value, meters,
                        version, extracted, x, y, raw_attrs
                    ) FROM STDIN"""
                ) as copy:
                    for r in wm_rows:
                        copy.write_row(r)

                # Build spatial & relational indexes
                cur.execute("CREATE INDEX IF NOT EXISTS idx_nwr_nodes_xy ON nwr_track_nodes (x, y);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_nwr_nodes_aid ON nwr_track_nodes (asset_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_nwr_tcl_bbox ON nwr_track_centrelines (min_x, max_x, min_y, max_y);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_nwr_tcl_elr ON nwr_track_centrelines (elr);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_nwr_tcl_nodes ON nwr_track_centrelines (start_node_id, end_node_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_nwr_elrs_bbox ON nwr_elrs (min_x, max_x, min_y, max_y);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_nwr_elrs_elr ON nwr_elrs (elr);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_nwr_wm_xy ON nwr_waymarks (x, y);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_nwr_wm_elr ON nwr_waymarks (elr, meters);")

                meta = {
                    "completed": True,
                    "duration_sec": round(time.time() - t0, 2),
                    "counts": {
                        "nwr_track_centrelines": 49731,
                        "nwr_track_nodes": 38689,
                        "nwr_elrs": 1598,
                        "nwr_waymarks": 42878,
                    },
                }
                cur.execute(
                    """
                    INSERT INTO ingestion_meta (key, value)
                    VALUES ('nwr_trackmodel_complete', %s::jsonb)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();
                    """,
                    (orjson.dumps(meta).decode("utf-8"),),
                )
            conn.commit()
            _NWR_COMPLETE = True
    return True
