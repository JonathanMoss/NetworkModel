"""TPS topology asset handlers: stations, track edges, and track nodes."""

from typing import Any, Optional

from fastapi import HTTPException

from config import decode_code
from geometry import meters_to_miles_chains
from timing_catalog import get_timing_catalog
from track_identity import resolve_tps_track_identity
from track_matcher import find_nwr_cross_links_for_tps

def _get_station_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a TPS station, its tracks, platforms, and geographic cross-links."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
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
                asset_type="station",
                preferred_fid=nwr_fid,
                saved_fid=saved_override_fid,
            ),
        },
        "highlight": {
            "ways": highlight_ways,
            "points": highlight_points,
        },
        "raw_attrs": st["raw_attrs"],
        "raw_children": st["raw_children"],
    }



def _get_edge_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a TPS track edge, speed/gradient profile, and clipped NWR centreline."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
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

    edge_elr = elr_start or (decode_code("kmregionmasterdesc", n_end["km_region_id"]) if n_end else None)
    edge_identity = resolve_tps_track_identity(
        cur,
        [ed["start_node_id"], ed["end_node_id"]],
        start_node_id=ed["start_node_id"],
        end_node_id=ed["end_node_id"],
        elr=edge_elr,
        start_km=start_km,
        end_km=end_km,
    )
    track_hint = edge_identity["track_name_hint"] or (tracks[0]["name"] if tracks else None)

    nwr_cross_links = find_nwr_cross_links_for_tps(
        cur,
        edge_elr,
        start_km,
        end_km_meters=end_km,
        direction_hint=edge_identity["direction_hint"],
        track_name_hint=track_hint,
        item_label=f"Edge #{ed['edge_id']}",
        is_extrapolated=is_ed_extrapolated,
        asset_type="edge",
        preferred_fid=nwr_fid,
        match_source=edge_identity["source"],
        platform_num=edge_identity.get("platform_num"),
        saved_fid=saved_override_fid,
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



def _get_node_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a TPS schematic node, connected edges, and snapped NWR physical node."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
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

    is_points_node = len(conn_edges) >= 3
    decoded["Node Classification"] = (
        f"Points / Turnout (Valency {len(conn_edges)})"
        if is_points_node
        else ("Continuous Track Joint (Valency 2)" if len(conn_edges) == 2 else "Buffer Stop / Dead End (Valency 1)")
    )

    nd_identity = resolve_tps_track_identity(
        cur,
        [nd["node_id"]],
        elr=elr1,
    )
    nwr_cross_links = find_nwr_cross_links_for_tps(
        cur,
        elr1,
        nd_km,
        direction_hint=nd_identity["direction_hint"],
        track_name_hint=nd_identity["track_name_hint"],
        tps_valency=len(conn_edges),
        item_label=f"Points #{nd['node_id']}" if is_points_node else f"Track Node #{nd['node_id']}",
        is_extrapolated=is_nd_extrapolated,
        asset_type="points" if is_points_node else "node",
        preferred_fid=nwr_fid,
        match_source=nd_identity["source"],
        platform_num=nd_identity.get("platform_num"),
        saved_fid=saved_override_fid,
    )

    return {
        "asset_type": "node",
        "id": nd["node_id"],
        "title": f"Points Node #{nd['node_id']} (Valency {len(conn_edges)})" if is_points_node else f"Track Node #{nd['node_id']}",
        "subtitle": f"{'Points • ' if is_points_node else ''}ELR {elr1 or 'N/A'} • {meters_to_miles_chains(nd['km_value']) or ''} • ({nd['netx']}, {nd['nety']})",
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
                {
                    "x": nd["netx"],
                    "y": nd["nety"],
                    "label": f"Points #{nd['node_id']}" if is_points_node else f"Track Node #{nd['node_id']}",
                    "color": "#a855f7" if is_points_node else "#10b981",
                    "shape": "diamond" if is_points_node else "circle",
                }
            ],
        },
        "raw_attrs": nd["raw_attrs"],
        "raw_children": nd["raw_children"],
    }



