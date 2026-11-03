import os
import tarfile
import time
import traceback
import zipfile
from collections import Counter
import lxml.etree as ET
import orjson
import psycopg

INGEST_STATE = {
    "status": "idle",       # idle | running | indexing | ready | error
    "phase": "Waiting to start",
    "elements_processed": 0,
    "percent": 0.0,
    "started_at": None,
    "finished_at": None,
    "error": None,
    "tag_counts": {},
}

DEDICATED_TAGS = {
    "node",
    "edge",
    "mastersignal",
    "signal",
    "station",
    "stationboundary",
    "block",
    "route",
    "section",
    "attributesection",
    "graphicvector",
    "graphictext",
    "polyarea",
    "annotation",
}

LOOKUP_TAGS = {
    "avdirection", "avusetype", "basesignaltypedesc", "brakingmodedesc",
    "cancellationreasoncode", "changegrouptypedesc", "coachtypedesc",
    "commutertypedesc", "contractstatedesc", "conventionalsigntypedesc",
    "directionofline", "directionoftraveldesc", "electricprofiledesc",
    "etadvicetypedesc", "eventsite", "geogislinecodedesc", "gpdatatype",
    "holdingtype", "infraperioddesc", "infraregiondesc", "interlockingaspectdesc",
    "interlockingsystem", "kmregionmasterdesc", "line", "linecategory",
    "lineproperty", "linepropertytemplate", "linetrackdesc", "linetypedesc",
    "linktype", "motortypedesc", "nonrequiredhandlingdesc", "opdaytype",
    "operator", "passengertypedesc", "period", "planningorigin",
    "platformtypedesc", "powerrestrictiondesc", "prioritydesc", "projecttype",
    "publictraintype", "refcode", "refcodetype", "requestactiontypedesc",
    "restrictionactivitydetail", "restrictionactivitytype",
    "restrictionaffectedinfratype", "restrictioncode", "restrictionimpacttypedesc",
    "restrictionopconddesc", "restrictionreqchangedesc", "restrictionstatedesc",
    "restrictiontype", "restrictionworkflowstatedesc", "restrictionworktype",
    "roplinecodedesc", "rounding", "salinecodedesc", "schedversiontype",
    "sectionattributetypedesc", "sectiontypedesc", "servicetype", "signalZone",
    "sitetrackdesc", "solutiontypedesc", "specialday", "speedrestrictiontype",
    "stationbank", "stationbanksection", "stationboundarytypedesc",
    "stationcategorydesc", "stationlayoutcode", "stationset", "stationsettype",
    "stationtypedesc", "stationzone", "stoptypedesc", "structuregauge",
    "tastatedesc", "timetabletypedesc", "tpsicodelistdesc", "tpsideliverytypedesc",
    "trackcategorydesc", "tractiontypedesc", "trafficactivity",
    "trafficoperationtypedesc", "traindeptypedesc", "trainflagdesc",
    "traingrouptypedesc", "trainlayerdesc", "trainstatedesc",
    "traintractiongroupdesc", "traintype", "transporttype", "uiccodedesc",
    "vehicletypedesc", "veloprofiledesc", "veloprofilenameset",
    "workflowstatedesc", "worksheetdocumentstatedesc", "worksheettypedesc",
}


def elem_children_to_list(elem):
    res = []
    for child in elem:
        item = {"tag": child.tag}
        if child.attrib:
            item["attrs"] = dict(child.attrib)
        if len(child):
            item["children"] = elem_children_to_list(child)
        if child.text and child.text.strip():
            item["text"] = child.text.strip()
        res.append(item)
    return res


def json_str(obj) -> str:
    return orjson.dumps(obj).decode("utf-8")


def to_int(val, default=None):
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        try:
            return int(float(val))
        except ValueError:
            return default


def to_float(val, default=None):
    if val is None or val == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


def to_bool(val, default=False):
    if val is None:
        return default
    return str(val).lower() in ("true", "1", "yes")


class BatchCopyBuffer:
    def __init__(self, conn, table_sql: str, flush_every: int = 25000):
        self.conn = conn
        self.table_sql = table_sql
        self.flush_every = flush_every
        self.rows = []

    def add(self, row: tuple):
        self.rows.append(row)
        if len(self.rows) >= self.flush_every:
            self.flush()

    def flush(self):
        if not self.rows:
            return
        with self.conn.cursor() as cur:
            with cur.copy(self.table_sql) as copy:
                for r in self.rows:
                    copy.write_row(r)
        self.conn.commit()
        self.rows.clear()


def check_already_ingested(db_url: str) -> bool:
    try:
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT value FROM ingestion_meta WHERE key = 'ingestion_complete'"
                )
                row = cur.fetchone()
                if row and row[0].get("completed") is True:
                    INGEST_STATE["status"] = "ready"
                    INGEST_STATE["phase"] = "Complete"
                    INGEST_STATE["percent"] = 100.0
                    INGEST_STATE["tag_counts"] = row[0].get("tag_counts", {})
                    INGEST_STATE["elements_processed"] = row[0].get("total_elements", 0)
                    return True
    except Exception:
        pass
    return False


def init_schema(db_url: str, schema_path: str):
    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()


def resolve_tps_xml_path(xml_path: str) -> str:
    """Resolve a TPS XML file path from a direct XML file, directory, or archive (.tar.gz / .zip)."""
    candidates = [xml_path, "/data/tps", "/data/XML_p.xml"]
    for cand in candidates:
        if not cand or not os.path.exists(cand):
            continue
        if os.path.isfile(cand):
            lower = cand.lower()
            if lower.endswith(".xml"):
                return cand
            if lower.endswith((".tar.gz", ".tgz", ".tar")):
                extract_dir = "/tmp/tps_extracted"
                os.makedirs(extract_dir, exist_ok=True)
                with tarfile.open(cand, "r:*") as tf:
                    for member in tf.getmembers():
                        if member.isfile() and member.name.lower().endswith(".xml"):
                            tf.extract(member, path=extract_dir)
                            return os.path.join(extract_dir, member.name)
            if lower.endswith(".zip"):
                extract_dir = "/tmp/tps_extracted"
                os.makedirs(extract_dir, exist_ok=True)
                with zipfile.ZipFile(cand, "r") as zf:
                    for name in zf.namelist():
                        if name.lower().endswith(".xml"):
                            zf.extract(name, path=extract_dir)
                            return os.path.join(extract_dir, name)
        elif os.path.isdir(cand):
            xml_files = []
            archives = []
            for root, _, files in os.walk(cand):
                for fname in files:
                    full = os.path.join(root, fname)
                    fl = fname.lower()
                    if fl.endswith(".xml"):
                        xml_files.append(full)
                    elif fl.endswith((".tar.gz", ".tgz", ".tar", ".zip")):
                        archives.append(full)
            if xml_files:
                xml_files.sort(key=lambda p: (0 if os.path.basename(p) == "XML_p.xml" else 1, -os.path.getsize(p)))
                return xml_files[0]
            for arch in archives:
                resolved = resolve_tps_xml_path(arch)
                if resolved and os.path.isfile(resolved):
                    return resolved
    raise FileNotFoundError(
        f"TPS XML reference file not found at '{xml_path}'. "
        "Specify the path to your TPS XML file or archive in the Dockerfile (ARG TPS_MODEL_PATH)."
    )


def run_ingestion(db_url: str, xml_path: str, schema_path: str):
    t0 = time.time()
    INGEST_STATE["status"] = "running"
    INGEST_STATE["started_at"] = t0
    INGEST_STATE["phase"] = "Initializing PostgreSQL schema"
    INGEST_STATE["percent"] = 1.0

    try:
        init_schema(db_url, schema_path)
        if check_already_ingested(db_url):
            return

        resolved_xml_path = resolve_tps_xml_path(xml_path)

        # Clean tables in case a previous partial ingestion occurred
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    TRUNCATE TABLE
                        ingestion_meta, lookup_codes, nodes, edges, mastersignals,
                        signals, stations, station_tracks, station_boundaries,
                        blocks, routes, sections, attribute_sections,
                        graphic_vectors, graphic_texts, poly_areas, annotations,
                        raw_tps_elements
                    RESTART IDENTITY;
                """)
            conn.commit()

        INGEST_STATE["phase"] = "Streaming XML_p.xml into PostgreSQL"
        tag_counts = Counter()

        # In-memory coordinate & name lookups for instant O(1) resolution
        node_coords = {}      # node_id -> (netx, nety)
        master_signals = {}   # id -> name
        seen_stations = set()
        seen_sections = set()

        # Buffered stations & tracks (appear before <node> in XML_p.xml)
        pending_stations = []
        pending_tracks = []
        lookup_rows = {}

        # Estimate total depth-2 elements (~2,508,000 in XML_p.xml)
        EST_TOTAL = 2508000
        processed = 0

        with psycopg.connect(db_url) as conn:
            buf_nodes = BatchCopyBuffer(
                conn,
                """COPY nodes (
                    node_id, line_id, business_number, netx, nety, netz,
                    linex, liney, linez, km_region_id, km_value,
                    second_km_region_id, second_km_value, name, angle,
                    valid_from, valid_to, last_modified, raw_attrs, raw_children
                ) FROM STDIN""",
                flush_every=30000,
            )
            buf_edges = BatchCopyBuffer(
                conn,
                """COPY edges (
                    edge_id, start_node_id, end_node_id, start_line_id, end_line_id,
                    x0, y0, x1, y1, min_x, max_x, min_y, max_y,
                    length, gradient, vmax, electrified, partial_release,
                    approach_signal, tunnel_square_area, tunnel_factor,
                    erratic_km_dist, height_difference, radius, geogis_line_code,
                    valid_from, valid_to, last_modified, velocity_profiles,
                    raw_attrs, raw_children
                ) FROM STDIN""",
                flush_every=30000,
            )
            buf_mastersig = BatchCopyBuffer(
                conn,
                "COPY mastersignals (id, name, raw_attrs) FROM STDIN",
                flush_every=50000,
            )
            buf_signals = BatchCopyBuffer(
                conn,
                """COPY signals (
                    id, signal_id, master_name, name, interlocking_sys_id, zone_id,
                    start_node_id, end_node_id, point_node_id, x, y, x1, y1,
                    tmp_closed, use_sec_sect_freeing_time, sec_sect_freeing_time,
                    restriction_code_id, bumper, import_locked, km_region_id,
                    line_track_id, valid_from, valid_to, last_modified,
                    security_sections, raw_attrs, raw_children
                ) FROM STDIN""",
                flush_every=25000,
            )
            buf_boundaries = BatchCopyBuffer(
                conn,
                """COPY station_boundaries (
                    signal_id, boundary_type_id, station_ids, valid_from,
                    valid_to, last_modified, raw_attrs, raw_children
                ) FROM STDIN""",
                flush_every=25000,
            )
            buf_blocks = BatchCopyBuffer(
                conn,
                """COPY blocks (
                    block_id, line_id, pre_signal_distance, priority, sight_distance,
                    atc_length, atc_flag, moving_block, shunting_flag,
                    additional_aspects, business_reference_id, valid_from,
                    valid_to, last_modified, node_ids, coords,
                    min_x, max_x, min_y, max_y, raw_attrs, raw_children
                ) FROM STDIN""",
                flush_every=20000,
            )
            buf_routes = BatchCopyBuffer(
                conn,
                """COPY routes (
                    route_id, line_id, station_id, rop_line_code_id, sa_line_code_id,
                    prelocation, valid_from, valid_to, last_modified,
                    node_ids, coords, min_x, max_x, min_y, max_y,
                    raw_attrs, raw_children
                ) FROM STDIN""",
                flush_every=5000,
            )
            buf_sections = BatchCopyBuffer(
                conn,
                "COPY sections (section_id, section_type, name, last_modified, raw_attrs) FROM STDIN",
                flush_every=25000,
            )
            buf_attr_sections = BatchCopyBuffer(
                conn,
                """COPY attribute_sections (
                    id, section_id, km_region_id, short_description, description,
                    valid_from, valid_to, last_modified, node_ids, edge_pairs,
                    section_attrs, coords, min_x, max_x, min_y, max_y,
                    raw_attrs, raw_children
                ) FROM STDIN""",
                flush_every=5000,
            )
            buf_gvec = BatchCopyBuffer(
                conn,
                """COPY graphic_vectors (
                    x0, y0, x1, y1, min_x, max_x, min_y, max_y,
                    layers, valid_from, valid_to, last_modified, raw_attrs
                ) FROM STDIN""",
                flush_every=50000,
            )
            buf_gtxt = BatchCopyBuffer(
                conn,
                """COPY graphic_texts (
                    x, y, size, text, angle, layers, valid_from,
                    valid_to, last_modified, raw_attrs
                ) FROM STDIN""",
                flush_every=25000,
            )
            buf_poly = BatchCopyBuffer(
                conn,
                """COPY poly_areas (
                    id, name, description, corners, min_x, max_x, min_y, max_y,
                    raw_attrs, raw_children
                ) FROM STDIN""",
                flush_every=2000,
            )
            buf_annot = BatchCopyBuffer(
                conn,
                """COPY annotations (
                    x, y, text, valid_from, valid_to, last_modified, raw_attrs
                ) FROM STDIN""",
                flush_every=500,
            )
            buf_raw = BatchCopyBuffer(
                conn,
                """COPY raw_tps_elements (
                    tag, entity_id, name, raw_attrs, raw_children
                ) FROM STDIN""",
                flush_every=30000,
            )

            context = ET.iterparse(resolved_xml_path, events=("start", "end"))
            depth = 0

            for event, elem in context:
                if event == "start":
                    depth += 1
                    continue

                # event == "end"
                if depth == 2:
                    tag = elem.tag
                    tag_counts[tag] += 1
                    processed += 1

                    if processed % 25000 == 0:
                        INGEST_STATE["elements_processed"] = processed
                        INGEST_STATE["percent"] = min(85.0, round((processed / EST_TOTAL) * 85.0, 1))
                        INGEST_STATE["phase"] = f"Ingesting <{tag}> ({processed:,} elements)"
                        INGEST_STATE["tag_counts"] = dict(tag_counts)

                    attrs = dict(elem.attrib)
                    children = elem_children_to_list(elem)
                    raw_attrs_s = json_str(attrs)
                    raw_children_s = json_str(children)

                    if tag in LOOKUP_TAGS:
                        cid = (
                            attrs.get("id")
                            or attrs.get("lineid")
                            or attrs.get("linecategoryid")
                            or attrs.get("refcodeid")
                            or attrs.get("refcodetypeid")
                            or attrs.get("traintypeid")
                            or attrs.get("daytypeid")
                            or str(tag_counts[tag])
                        )
                        label = (
                            attrs.get("text")
                            or attrs.get("name")
                            or attrs.get("abbrev")
                            or attrs.get("code")
                            or attrs.get("shortdesc")
                            or attrs.get("uniquedescription")
                            or attrs.get("desc")
                            or ""
                        )
                        vtext = attrs.get("vanillatext") or attrs.get("customertext") or ""
                        desc = attrs.get("description") or attrs.get("desc") or ""
                        lookup_rows[(tag, str(cid))] = (
                            tag, str(cid), label, vtext, desc, raw_attrs_s, raw_children_s
                        )

                    if tag == "node":
                        pt = elem.find("point")
                        node_id = to_int(pt.get("nodeid") if pt is not None else attrs.get("businessnumber"))
                        line_id = to_int(pt.get("lineid") if pt is not None else 0, 0)
                        nx = to_float(attrs.get("netx"), 0.0)
                        ny = to_float(attrs.get("nety"), 0.0)
                        if node_id is not None and node_id not in node_coords:
                            node_coords[node_id] = (nx, ny)
                            buf_nodes.add((
                                node_id,
                                line_id,
                                to_int(attrs.get("businessnumber")),
                                nx,
                                ny,
                                to_float(attrs.get("netz"), 0.0),
                                to_float(attrs.get("linex"), 0.0),
                                to_float(attrs.get("liney"), 0.0),
                                to_float(attrs.get("linez"), 0.0),
                                to_int(attrs.get("kmregionid")),
                                to_int(attrs.get("kmvalue")),
                                to_int(attrs.get("secondkmregionid")),
                                to_int(attrs.get("secondkmvalue")),
                                attrs.get("name", ""),
                                to_int(attrs.get("angle"), 0),
                                attrs.get("validfrom"),
                                attrs.get("validTo"),
                                attrs.get("lastmodified"),
                                raw_attrs_s,
                                raw_children_s,
                            ))
                        else:
                            buf_raw.add((tag, str(node_id or ""), attrs.get("name", ""), raw_attrs_s, raw_children_s))

                    elif tag == "edge":
                        start_pt = elem.find("./directed/start/point")
                        end_pt = elem.find("./directed/end/point")
                        sn = to_int(start_pt.get("nodeid")) if start_pt is not None else None
                        en = to_int(end_pt.get("nodeid")) if end_pt is not None else None
                        sl = to_int(start_pt.get("lineid"), 0) if start_pt is not None else 0
                        el = to_int(end_pt.get("lineid"), 0) if end_pt is not None else 0

                        c0 = node_coords.get(sn)
                        c1 = node_coords.get(en)
                        x0 = c0[0] if c0 else None
                        y0 = c0[1] if c0 else None
                        x1 = c1[0] if c1 else None
                        y1 = c1[1] if c1 else None
                        if x0 is not None and x1 is not None:
                            min_x, max_x = (x0, x1) if x0 <= x1 else (x1, x0)
                            min_y, max_y = (y0, y1) if y0 <= y1 else (y1, y0)
                        else:
                            min_x = max_x = min_y = max_y = None

                        vprofs = [
                            {"profileid": to_int(vp.get("profileid")), "vmax": to_int(vp.get("vmax"))}
                            for vp in elem.findall("velocityprofile")
                        ]
                        buf_edges.add((
                            to_int(attrs.get("id")),
                            sn,
                            en,
                            sl,
                            el,
                            x0,
                            y0,
                            x1,
                            y1,
                            min_x,
                            max_x,
                            min_y,
                            max_y,
                            to_int(attrs.get("length"), 0),
                            to_float(attrs.get("gradient"), 0.0),
                            to_int(attrs.get("vmax"), 0),
                            to_bool(attrs.get("electrified")),
                            to_bool(attrs.get("partialrelease")),
                            to_bool(attrs.get("approachsignal")),
                            to_float(attrs.get("tunnelsquarearea"), 0.0),
                            to_float(attrs.get("tunnelfactor"), 0.0),
                            to_int(attrs.get("erratickmdist"), 0),
                            to_int(attrs.get("heightdifference"), 0),
                            to_int(attrs.get("radius"), 0),
                            to_int(attrs.get("geogislinecode"), 0),
                            attrs.get("validfrom"),
                            attrs.get("validTo"),
                            attrs.get("lastmodified"),
                            json_str(vprofs),
                            raw_attrs_s,
                            raw_children_s,
                        ))

                    elif tag == "mastersignal":
                        ms_id = to_int(attrs.get("id"))
                        ms_name = attrs.get("name", "")
                        if ms_id is not None and ms_id not in master_signals:
                            master_signals[ms_id] = ms_name
                            buf_mastersig.add((ms_id, ms_name, raw_attrs_s))
                        else:
                            buf_raw.add((tag, str(ms_id or ""), ms_name, raw_attrs_s, raw_children_s))

                    elif tag == "signal":
                        sig_id = to_int(attrs.get("signalid"))
                        start_pt = elem.find("./directed/start/point")
                        end_pt = elem.find("./directed/end/point")
                        direct_pt = elem.find("point")
                        sn = to_int(start_pt.get("nodeid")) if start_pt is not None else None
                        en = to_int(end_pt.get("nodeid")) if end_pt is not None else None
                        pn = to_int(direct_pt.get("nodeid")) if direct_pt is not None else None

                        c0 = node_coords.get(sn) or node_coords.get(pn)
                        c1 = node_coords.get(en)
                        # Place signal at end node (or midpoint if both exist)
                        if c0 and c1:
                            sx = c1[0]
                            sy = c1[1]
                            sx1 = c0[0]
                            sy1 = c0[1]
                        elif c0:
                            sx, sy = c0
                            sx1, sy1 = c0
                        elif c1:
                            sx, sy = c1
                            sx1, sy1 = c1
                        else:
                            sx = sy = sx1 = sy1 = None

                        sec_sects = []
                        for ss in elem.findall("securitysection"):
                            ss_nodes = [
                                {
                                    "nodeid": to_int(ssn.get("nodeid")),
                                    "boundarynode": to_bool(ssn.get("boundarynode")),
                                }
                                for ssn in ss.findall("securitysectionnode")
                            ]
                            sec_sects.append({
                                "vmax": to_int(ss.get("vmax")),
                                "accelerationattail": to_bool(ss.get("accelerationattail")),
                                "blockoccupying": to_bool(ss.get("blockoccupying")),
                                "nodes": ss_nodes,
                            })

                        buf_signals.add((
                            to_int(attrs.get("id")),
                            sig_id,
                            master_signals.get(sig_id, ""),
                            attrs.get("name", ""),
                            to_int(attrs.get("interlockingsysid")),
                            to_int(attrs.get("zoneid")),
                            sn,
                            en,
                            pn,
                            sx,
                            sy,
                            sx1,
                            sy1,
                            to_bool(attrs.get("tmpClosed")),
                            to_bool(attrs.get("useSecSectFreeingTime")),
                            to_int(attrs.get("secSectFreeingTime"), 0),
                            to_int(attrs.get("restrictioncodeid"), 0),
                            to_bool(attrs.get("bumper")),
                            to_bool(attrs.get("importlocked")),
                            to_int(attrs.get("kmRegionID"), 0),
                            to_int(attrs.get("lineTrackID"), 0),
                            attrs.get("validfrom"),
                            attrs.get("validTo"),
                            attrs.get("lastmodified"),
                            json_str(sec_sects),
                            raw_attrs_s,
                            raw_children_s,
                        ))

                    elif tag == "station":
                        st_id = to_int(attrs.get("stationid"))
                        positions = [
                            {
                                "kmregionid": to_int(sp.get("kmregionid")),
                                "kmvalue": to_int(sp.get("kmvalue")),
                            }
                            for sp in elem.findall("stationposition")
                        ]
                        km_reg = positions[0]["kmregionid"] if positions else None
                        km_val = positions[0]["kmvalue"] if positions else None

                        tracks_in_st = elem.findall("track")
                        active_trk_cnt = 0
                        st_node_ids = []

                        for trk in tracks_in_st:
                            t_attrs = dict(trk.attrib)
                            t_children = elem_children_to_list(trk)
                            way_pts = [
                                to_int(pt.get("nodeid"))
                                for pt in trk.findall("./way/point")
                                if pt.get("nodeid") is not None
                            ]
                            if way_pts:
                                active_trk_cnt += 1
                                st_node_ids.extend(way_pts)
                            pending_tracks.append((
                                to_int(t_attrs.get("trackID")),
                                st_id,
                                t_attrs.get("name", ""),
                                t_attrs.get("longname", ""),
                                to_int(t_attrs.get("seq"), 0),
                                t_attrs.get("businessReferenceID", ""),
                                t_attrs.get("description", ""),
                                to_int(t_attrs.get("trackcategory"), 0),
                                to_int(t_attrs.get("effectivelength"), 0),
                                to_int(t_attrs.get("roplinecode"), 0),
                                to_int(t_attrs.get("salinecode"), 0),
                                to_int(t_attrs.get("linepropertytemplateid"), 0),
                                to_bool(t_attrs.get("permissiveWorking")),
                                to_bool(t_attrs.get("directed")),
                                to_bool(t_attrs.get("trackTmpClosed")),
                                t_attrs.get("validfrom"),
                                t_attrs.get("validTo"),
                                t_attrs.get("lastmodified"),
                                way_pts,
                                json_str(t_attrs),
                                json_str(t_children),
                            ))

                        if st_id is not None and st_id not in seen_stations:
                            seen_stations.add(st_id)
                            pending_stations.append((
                                st_id,
                                attrs.get("abbrev", ""),
                                attrs.get("longname", ""),
                                attrs.get("crscode", ""),
                                attrs.get("stanox", ""),
                                attrs.get("nalco", ""),
                                attrs.get("nlccheckchr", ""),
                                attrs.get("uiccode", ""),
                                attrs.get("uicstationcode", ""),
                                to_int(attrs.get("stationtype"), 0),
                                to_int(attrs.get("stationcategory"), 0),
                                to_int(attrs.get("stdstoppingtime"), 0),
                                to_int(attrs.get("stdconnectiontime"), 0),
                                to_bool(attrs.get("compulsorystop")),
                                to_bool(attrs.get("isparent")),
                                km_reg,
                                km_val,
                                len(tracks_in_st),
                                active_trk_cnt,
                                st_node_ids,
                                json_str(positions),
                                raw_attrs_s,
                                raw_children_s,
                            ))
                        else:
                            buf_raw.add((tag, str(st_id or ""), attrs.get("longname", ""), raw_attrs_s, raw_children_s))

                    elif tag == "stationboundary":
                        st_refs = [
                            to_int(sr.get("id"))
                            for sr in elem.findall("stationreference")
                            if sr.get("id") is not None
                        ]
                        buf_boundaries.add((
                            to_int(attrs.get("signalid")),
                            to_int(attrs.get("boundarytypeid")),
                            st_refs,
                            attrs.get("validfrom"),
                            attrs.get("validTo"),
                            attrs.get("lastmodified"),
                            raw_attrs_s,
                            raw_children_s,
                        ))

                    elif tag == "block":
                        nids = [
                            to_int(pt.get("nodeid"))
                            for pt in elem.findall("./way/point")
                            if pt.get("nodeid") is not None
                        ]
                        pts = [node_coords[n] for n in nids if n in node_coords]
                        if pts:
                            xs = [p[0] for p in pts]
                            ys = [p[1] for p in pts]
                            min_x, max_x = min(xs), max(xs)
                            min_y, max_y = min(ys), max(ys)
                        else:
                            min_x = max_x = min_y = max_y = None
                        buf_blocks.add((
                            to_int(attrs.get("blockid")),
                            to_int(attrs.get("lineid"), 0),
                            to_int(attrs.get("presignaldistance"), 0),
                            to_int(attrs.get("priority"), 0),
                            to_int(attrs.get("sightdistance"), 0),
                            to_int(attrs.get("atclength"), 0),
                            to_bool(attrs.get("atcflag")),
                            to_bool(attrs.get("movingblock")),
                            to_bool(attrs.get("shuntingflag")),
                            to_int(attrs.get("additionalaspects"), 0),
                            attrs.get("businessreferenceid", ""),
                            attrs.get("validfrom"),
                            attrs.get("validTo"),
                            attrs.get("lastmodified"),
                            nids,
                            json_str(pts),
                            min_x,
                            max_x,
                            min_y,
                            max_y,
                            raw_attrs_s,
                            raw_children_s,
                        ))

                    elif tag == "route":
                        nids = [
                            to_int(pt.get("nodeid"))
                            for pt in elem.findall("./way/point")
                            if pt.get("nodeid") is not None
                        ]
                        pts = [node_coords[n] for n in nids if n in node_coords]
                        if pts:
                            xs = [p[0] for p in pts]
                            ys = [p[1] for p in pts]
                            min_x, max_x = min(xs), max(xs)
                            min_y, max_y = min(ys), max(ys)
                        else:
                            min_x = max_x = min_y = max_y = None
                        buf_routes.add((
                            to_int(attrs.get("routeid")),
                            to_int(attrs.get("lineid"), 0),
                            to_int(attrs.get("stationid")),
                            to_int(attrs.get("roplinecodeid"), 0),
                            to_int(attrs.get("salinecodeid"), 0),
                            to_bool(attrs.get("prelocation")),
                            attrs.get("validfrom"),
                            attrs.get("validTo"),
                            attrs.get("lastmodified"),
                            nids,
                            json_str(pts),
                            min_x,
                            max_x,
                            min_y,
                            max_y,
                            raw_attrs_s,
                            raw_children_s,
                        ))

                    elif tag == "section":
                        sec_id = to_int(attrs.get("id"))
                        if sec_id is not None and sec_id not in seen_sections:
                            seen_sections.add(sec_id)
                            buf_sections.add((
                                sec_id,
                                to_int(attrs.get("type"), 0),
                                attrs.get("name", ""),
                                attrs.get("lastModified") or attrs.get("lastmodified"),
                                raw_attrs_s,
                            ))
                        else:
                            buf_raw.add((tag, str(sec_id or ""), attrs.get("name", ""), raw_attrs_s, raw_children_s))

                    elif tag == "attributesection":
                        edge_pairs = []
                        nids_ordered = []
                        seen_n = set()
                        for ase in elem.findall("asedge"):
                            sp = ase.find("./directed/start/point")
                            ep = ase.find("./directed/end/point")
                            s_id = to_int(sp.get("nodeid")) if sp is not None else None
                            e_id = to_int(ep.get("nodeid")) if ep is not None else None
                            if s_id is not None and e_id is not None:
                                edge_pairs.append([s_id, e_id])
                                for n in (s_id, e_id):
                                    if n not in seen_n:
                                        seen_n.add(n)
                                        nids_ordered.append(n)

                        sec_attrs = [dict(sa.attrib) for sa in elem.findall("sectionattr")]
                        seg_coords = []
                        xs, ys = [], []
                        for s_id, e_id in edge_pairs:
                            c0 = node_coords.get(s_id)
                            c1 = node_coords.get(e_id)
                            if c0 and c1:
                                seg_coords.append([c0[0], c0[1], c1[0], c1[1]])
                                xs.extend((c0[0], c1[0]))
                                ys.extend((c0[1], c1[1]))

                        min_x = min(xs) if xs else None
                        max_x = max(xs) if xs else None
                        min_y = min(ys) if ys else None
                        max_y = max(ys) if ys else None

                        buf_attr_sections.add((
                            to_int(attrs.get("id")),
                            to_int(attrs.get("sectionid")),
                            to_int(attrs.get("kmregionid")),
                            attrs.get("shortdescription", ""),
                            attrs.get("description", ""),
                            attrs.get("validFrom") or attrs.get("validfrom"),
                            attrs.get("validTo") or attrs.get("validto"),
                            attrs.get("lastModified") or attrs.get("lastmodified"),
                            nids_ordered,
                            json_str(edge_pairs),
                            json_str(sec_attrs),
                            json_str(seg_coords),
                            min_x,
                            max_x,
                            min_y,
                            max_y,
                            raw_attrs_s,
                            raw_children_s,
                        ))

                    elif tag == "graphicvector":
                        x0 = to_float(attrs.get("x0"), 0.0)
                        y0 = to_float(attrs.get("y0"), 0.0)
                        x1 = to_float(attrs.get("x1"), 0.0)
                        y1 = to_float(attrs.get("y1"), 0.0)
                        buf_gvec.add((
                            x0,
                            y0,
                            x1,
                            y1,
                            min(x0, x1),
                            max(x0, x1),
                            min(y0, y1),
                            max(y0, y1),
                            to_int(attrs.get("layers"), 1),
                            attrs.get("validFrom") or attrs.get("validfrom"),
                            attrs.get("validTo") or attrs.get("validto"),
                            attrs.get("lastModified") or attrs.get("lastmodified"),
                            raw_attrs_s,
                        ))

                    elif tag == "graphictext":
                        buf_gtxt.add((
                            to_float(attrs.get("x"), 0.0),
                            to_float(attrs.get("y"), 0.0),
                            to_int(attrs.get("size"), 6),
                            attrs.get("text", ""),
                            to_int(attrs.get("angle"), 0),
                            to_int(attrs.get("layers"), 1),
                            attrs.get("validFrom") or attrs.get("validfrom"),
                            attrs.get("validTo") or attrs.get("validto"),
                            attrs.get("lastModified") or attrs.get("lastmodified"),
                            raw_attrs_s,
                        ))

                    elif tag == "polyarea":
                        corners = [
                            [to_float(c.get("x"), 0.0), to_float(c.get("y"), 0.0)]
                            for c in elem.findall("polyareacorner")
                        ]
                        if corners:
                            xs = [c[0] for c in corners]
                            ys = [c[1] for c in corners]
                            min_x, max_x = min(xs), max(xs)
                            min_y, max_y = min(ys), max(ys)
                        else:
                            min_x = max_x = min_y = max_y = None
                        buf_poly.add((
                            to_int(attrs.get("id")),
                            attrs.get("name", ""),
                            attrs.get("description", ""),
                            json_str(corners),
                            min_x,
                            max_x,
                            min_y,
                            max_y,
                            raw_attrs_s,
                            raw_children_s,
                        ))

                    elif tag == "annotation":
                        buf_annot.add((
                            to_float(attrs.get("x"), 0.0),
                            to_float(attrs.get("y"), 0.0),
                            attrs.get("text", ""),
                            attrs.get("validFrom") or attrs.get("validfrom"),
                            attrs.get("validTo") or attrs.get("validto"),
                            attrs.get("lastModified") or attrs.get("lastmodified"),
                            raw_attrs_s,
                        ))

                    else:
                        # Store every other element losslessly in raw_tps_elements
                        ent_id = (
                            attrs.get("id")
                            or attrs.get("contractid")
                            or attrs.get("lineid")
                            or attrs.get("templateid")
                            or attrs.get("traintypeid")
                            or attrs.get("refcodeid")
                            or attrs.get("refcodetypeid")
                            or attrs.get("tenantid")
                            or ""
                        )
                        ent_name = (
                            attrs.get("name")
                            or attrs.get("text")
                            or attrs.get("desc")
                            or attrs.get("description")
                            or attrs.get("code")
                            or attrs.get("abbrev")
                            or ""
                        )
                        buf_raw.add((
                            tag,
                            str(ent_id),
                            str(ent_name),
                            raw_attrs_s,
                            raw_children_s,
                        ))

                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]

                depth -= 1

            # Flush all streaming buffers
            INGEST_STATE["phase"] = "Flushing bulk COPY buffers"
            INGEST_STATE["percent"] = 86.0
            for b in (
                buf_nodes, buf_edges, buf_mastersig, buf_signals,
                buf_boundaries, buf_blocks, buf_routes, buf_sections,
                buf_attr_sections, buf_gvec, buf_gtxt, buf_poly,
                buf_annot, buf_raw
            ):
                b.flush()

            # Now resolve coordinates for buffered stations and station_tracks using node_coords
            INGEST_STATE["phase"] = "Resolving station & platform coordinates"
            INGEST_STATE["percent"] = 89.0

            buf_stations = BatchCopyBuffer(
                conn,
                """COPY stations (
                    station_id, abbrev, longname, crscode, stanox, nalco,
                    nlccheckchr, uiccode, uicstationcode, station_type,
                    station_category, std_stopping_time, std_connection_time,
                    compulsory_stop, is_parent, km_region_id, km_value,
                    x, y, min_x, max_x, min_y, max_y,
                    track_count, active_track_count, positions, raw_attrs, raw_children
                ) FROM STDIN""",
                flush_every=20000,
            )
            for st in pending_stations:
                (
                    st_id, abbrev, longname, crscode, stanox, nalco,
                    nlccheckchr, uiccode, uicstationcode, station_type,
                    station_category, std_stopping_time, std_connection_time,
                    compulsory_stop, is_parent, km_reg, km_val,
                    trk_cnt, active_trk_cnt, st_node_ids,
                    positions_s, raw_attrs_s, raw_children_s
                ) = st
                pts = [node_coords[n] for n in st_node_ids if n in node_coords]
                if pts:
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)
                    cx = sum(xs) / len(xs)
                    cy = sum(ys) / len(ys)
                else:
                    cx = cy = min_x = max_x = min_y = max_y = None

                buf_stations.add((
                    st_id, abbrev, longname, crscode, stanox, nalco,
                    nlccheckchr, uiccode, uicstationcode, station_type,
                    station_category, std_stopping_time, std_connection_time,
                    compulsory_stop, is_parent, km_reg, km_val,
                    cx, cy, min_x, max_x, min_y, max_y,
                    trk_cnt, active_trk_cnt, positions_s, raw_attrs_s, raw_children_s
                ))
            buf_stations.flush()
            pending_stations.clear()

            buf_tracks = BatchCopyBuffer(
                conn,
                """COPY station_tracks (
                    track_id, station_id, name, longname, seq,
                    business_reference_id, description, track_category,
                    effective_length, rop_line_code, sa_line_code,
                    line_property_template_id, permissive_working, directed,
                    track_tmp_closed, valid_from, valid_to, last_modified,
                    node_ids, coords, min_x, max_x, min_y, max_y,
                    raw_attrs, raw_children
                ) FROM STDIN""",
                flush_every=25000,
            )
            for trk in pending_tracks:
                (
                    track_id, st_id, name, longname, seq,
                    bref, desc, tcat, eff_len, rop, sa, lpt,
                    perm_work, directed, tmp_closed, vf, vt, lm,
                    way_pts, raw_attrs_s, raw_children_s
                ) = trk
                pts = [node_coords[n] for n in way_pts if n in node_coords]
                if pts:
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)
                else:
                    min_x = max_x = min_y = max_y = None
                buf_tracks.add((
                    track_id, st_id, name, longname, seq,
                    bref, desc, tcat, eff_len, rop, sa, lpt,
                    perm_work, directed, tmp_closed, vf, vt, lm,
                    way_pts, json_str(pts), min_x, max_x, min_y, max_y,
                    raw_attrs_s, raw_children_s
                ))
            buf_tracks.flush()
            pending_tracks.clear()

            # Write lookup_codes
            buf_lookup = BatchCopyBuffer(
                conn,
                """COPY lookup_codes (
                    category, code_id, label, vanilla_text, description,
                    raw_attrs, raw_children
                ) FROM STDIN""",
                flush_every=25000,
            )
            for row in lookup_rows.values():
                buf_lookup.add(row)
            buf_lookup.flush()

            # Create spatial & relational indexes for sub-15ms viewport & search queries
            INGEST_STATE["status"] = "indexing"
            INGEST_STATE["phase"] = "Building spatial & relational PostgreSQL indexes"
            INGEST_STATE["percent"] = 93.0

            with conn.cursor() as cur:
                # Backfill stations without track nodes using station_boundaries -> signals coordinates
                cur.execute("""
                    WITH bound_coords AS (
                        SELECT
                            unnest(sb.station_ids) AS st_id,
                            AVG(s.x) AS bx,
                            AVG(s.y) AS by,
                            MIN(s.x) AS min_x,
                            MAX(s.x) AS max_x,
                            MIN(s.y) AS min_y,
                            MAX(s.y) AS max_y
                        FROM station_boundaries sb
                        JOIN signals s ON s.signal_id = sb.signal_id
                        WHERE s.x IS NOT NULL
                        GROUP BY unnest(sb.station_ids)
                    )
                    UPDATE stations st
                    SET
                        x = bc.bx,
                        y = bc.by,
                        min_x = bc.min_x,
                        max_x = bc.max_x,
                        min_y = bc.min_y,
                        max_y = bc.max_y
                    FROM bound_coords bc
                    WHERE st.station_id = bc.st_id AND st.x IS NULL;
                """)

                cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_xy ON nodes (netx, nety);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_nodes_km ON nodes (km_region_id, km_value);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_bbox ON edges (min_x, max_x, min_y, max_y);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_start ON edges (start_node_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_end ON edges (end_node_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_id ON edges (edge_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_xy ON signals (x, y);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_sigid ON signals (signal_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_name ON signals (name);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_nodes ON signals (start_node_id, end_node_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_signals_end_node ON signals (end_node_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_stations_xy ON stations (x, y);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_stations_abbrev ON stations (abbrev);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_stations_crs ON stations (crscode);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_stations_name ON stations (longname);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_station_tracks_st ON station_tracks (station_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_station_tracks_bbox ON station_tracks (min_x, max_x, min_y, max_y);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_station_tracks_nodes ON station_tracks USING GIN (node_ids);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_station_boundaries_st ON station_boundaries USING GIN (station_ids);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_station_boundaries_sig ON station_boundaries (signal_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_blocks_bbox ON blocks (min_x, max_x, min_y, max_y);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_blocks_id ON blocks (block_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_blocks_nodes ON blocks USING GIN (node_ids);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_routes_bbox ON routes (min_x, max_x, min_y, max_y);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_routes_st ON routes (station_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_routes_nodes ON routes USING GIN (node_ids);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_attr_sec_bbox ON attribute_sections (min_x, max_x, min_y, max_y);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_attr_sec_nodes ON attribute_sections USING GIN (node_ids);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_gvec_bbox ON graphic_vectors (min_x, max_x, min_y, max_y);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_gtxt_xy ON graphic_texts (x, y);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_tps_tag ON raw_tps_elements (tag);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_raw_tps_ent ON raw_tps_elements (tag, entity_id);")

                meta_val = {
                    "completed": True,
                    "total_elements": processed,
                    "duration_sec": round(time.time() - t0, 2),
                    "tag_counts": dict(tag_counts),
                }
                cur.execute(
                    """
                    INSERT INTO ingestion_meta (key, value)
                    VALUES ('ingestion_complete', %s::jsonb)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW();
                    """,
                    (json_str(meta_val),),
                )
            conn.commit()

        INGEST_STATE["status"] = "ready"
        INGEST_STATE["phase"] = "Complete"
        INGEST_STATE["percent"] = 100.0
        INGEST_STATE["finished_at"] = time.time()
        INGEST_STATE["tag_counts"] = dict(tag_counts)
        INGEST_STATE["elements_processed"] = processed

    except Exception as e:
        traceback.print_exc()
        INGEST_STATE["status"] = "error"
        INGEST_STATE["error"] = str(e)
        INGEST_STATE["phase"] = f"Error: {e}"
