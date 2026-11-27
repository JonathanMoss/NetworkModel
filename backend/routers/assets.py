"""Detailed TPS asset inspection router (``/api/asset/{asset_type}/{asset_id}``).

Decomposes the 15 TPS asset inspection branches into dedicated, single-concern
handler functions dispatched via :data:`_ASSET_HANDLERS`.
"""

import re
from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row

from config import (
    decode_code,
    get_conn,
    get_saved_track_override,
    load_lookup_cache,
)
from geometry import (
    clean_rail_asset_name,
    interpolate_from_end,
    meters_to_miles_chains,
    parse_traction_curves,
    resolve_node_path_details,
)
from timing_catalog import get_timing_catalog
from track_identity import _parse_station_track_rows, resolve_tps_track_identity
from track_matcher import find_nwr_cross_links_for_tps

router = APIRouter(tags=["assets"])


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



def _get_signal_or_points_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a TPS signal, buffer stop, or points/turnout with full NWR matching."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
    cur.execute("SELECT * FROM signals WHERE pk = %s", (asset_id,))
    sig = cur.fetchone()
    if not sig:
        cur.execute("SELECT * FROM signals WHERE id = %s", (asset_id,))
        sig = cur.fetchone()
    if not sig:
        raise HTTPException(status_code=404, detail="Signal or Points not found")

    sig_name_raw = (sig["name"] or sig["master_name"] or "").strip()
    is_points_asset = (
        asset_type == "points"
        or sig["interlocking_sys_id"] in (15, 16)
        or sig_name_raw.upper().startswith("POINT")
    )
    clean_item_name = clean_rail_asset_name(
        sig_name_raw,
        "points" if is_points_asset else "signal",
        is_bumper=bool(sig["bumper"]),
    )

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
            "label": f"🔀 {clean_item_name}" if is_points_asset else f"🚦 {clean_item_name}",
            "color": "#a855f7" if is_points_asset else "#f59e0b",
            "group": "points" if is_points_asset else "signal",
            "shape": "diamond" if is_points_asset else "signal",
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

            p_info = resolve_node_path_details(cur, nids, decode_fn=decode_code)
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
                    es_name = es["name"] or es["master_name"]
                    es_is_pts = (es["interlocking_sys_id"] in (15, 16)) or (es_name and es_name.upper().startswith("POINT"))
                    entry_sigs.append({
                        "pk": es["pk"],
                        "signal_id": es["signal_id"],
                        "name": es_name,
                        "is_points": es_is_pts,
                        "interlocking_desc": decode_code("interlockingsystem", es["interlocking_sys_id"]),
                        "x": es["x"],
                        "y": es["y"],
                    })
                    if es["x"] is not None:
                        highlight_points.append({
                            "x": es["x"],
                            "y": es["y"],
                            "label": f"Rear {'Points' if es_is_pts else 'Signal'}: {es_name}",
                            "color": "#a855f7" if es_is_pts else "#38bdf8",
                            "group": "approach",
                            "block_id": b["block_id"],
                            "shape": "diamond" if es_is_pts else "circle",
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
                    "speed_profile": p_info.get("speed_profile", []),
                    "node_count": len(nids),
                    "turnout_count": len(p_info["turnout_nodes"]),
                    "sighting_point": sight_pt,
                    "pre_signal_point": presig_pt,
                    "entry_signals": entry_sigs,
                    "start_node_id": nids[0] if nids else None,
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
                        xs_name = xs["name"] or xs["master_name"]
                        xs_is_pts = (xs["interlocking_sys_id"] in (15, 16)) or (xs_name and xs_name.upper().startswith("POINT"))
                        exit_sigs.append({
                            "pk": xs["pk"],
                            "signal_id": xs["signal_id"],
                            "name": xs_name,
                            "is_points": xs_is_pts,
                            "interlocking_desc": decode_code("interlockingsystem", xs["interlocking_sys_id"]),
                            "x": xs["x"],
                            "y": xs["y"],
                        })
                        if xs["x"] is not None:
                            highlight_points.append({
                                "x": xs["x"],
                                "y": xs["y"],
                                "label": f"Next {'Points' if xs_is_pts else 'Signal'}: {xs_name}",
                                "color": "#a855f7" if xs_is_pts else "#10b981",
                                "group": "outgoing",
                                "block_id": b["block_id"],
                                "shape": "diamond" if xs_is_pts else "circle",
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
                    "speed_profile": p_info.get("speed_profile", []),
                    "node_count": len(nids),
                    "turnout_count": len(p_info["turnout_nodes"]),
                    "turnout_nodes": p_info["turnout_nodes"],
                    "exit_signals": exit_sigs,
                    "end_node_id": nids[-1] if nids else None,
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

    # Fallback to connected track edges if node mileage is missing
    if not sig_elr or sig_km_val is None or sig_km_val <= 0:
        cand_nids = [
            nid
            for nid in (
                sig.get("start_node_id"),
                sig.get("end_node_id"),
                sig.get("point_node_id"),
            )
            if nid
        ]
        if cand_nids:
            cur.execute(
                """
                SELECT e.edge_id, e.length, e.start_node_id, e.end_node_id,
                       ns.km_region_id AS s_reg, ns.km_value AS s_km,
                       ne.km_region_id AS e_reg, ne.km_value AS e_km
                FROM edges e
                JOIN nodes ns ON ns.node_id = e.start_node_id
                JOIN nodes ne ON ne.node_id = e.end_node_id
                WHERE (e.start_node_id = ANY(%s) OR e.end_node_id = ANY(%s))
                  AND (ns.km_value > 0 OR ne.km_value > 0)
                LIMIT 1
                """,
                (cand_nids, cand_nids),
            )
            adj_e = cur.fetchone()
            if adj_e:
                if not sig_elr:
                    reg_id = adj_e["s_reg"] or adj_e["e_reg"]
                    if reg_id:
                        sig_elr = decode_code("kmregionmasterdesc", reg_id)
                if adj_e["s_km"] and adj_e["s_km"] > 0:
                    sig_km_val = adj_e["s_km"]
                elif adj_e["e_km"] and adj_e["e_km"] > 0:
                    sig_km_val = adj_e["e_km"]
                is_sig_extrapolated = True

    # Resolve physical track direction & line class from TPS station_tracks / routes / graph walk / ELR polarity
    sig_start_km = None
    sig_end_km = None
    if sig.get("start_node_id") and sig.get("end_node_id"):
        cur.execute(
            "SELECT node_id, km_value FROM nodes WHERE node_id IN (%s, %s)",
            (sig["start_node_id"], sig["end_node_id"]),
        )
        km_pair = {r["node_id"]: r["km_value"] for r in cur.fetchall()}
        sig_start_km = km_pair.get(sig["start_node_id"])
        sig_end_km = km_pair.get(sig["end_node_id"])

    sig_node_list = [
        nid
        for nid in (sig.get("end_node_id"), sig.get("start_node_id"), sig.get("point_node_id"))
        if nid is not None
    ]
    sig_identity = resolve_tps_track_identity(
        cur,
        sig_node_list,
        start_node_id=sig.get("start_node_id"),
        end_node_id=sig.get("end_node_id"),
        elr=sig_elr,
        start_km=sig_start_km,
        end_km=sig_end_km,
    )
    sig_dir = sig_identity["direction_hint"]
    sig_track_hint = sig_identity["track_name_hint"]
    sig_match_source = sig_identity["source"]

    # Only fall back to explicit UP/DOWN words in signal name if TPS topology gave no direction
    if not sig_dir:
        sig_name_str = (sig["name"] or sig["master_name"] or "").strip().upper()
        if re.search(r"\b(?:DOWN|DN)\b", sig_name_str):
            sig_dir = "down"
            sig_match_source = "Signal Name"
        elif re.search(r"\bUP\b", sig_name_str):
            sig_dir = "up"
            sig_match_source = "Signal Name"

    # Compute TPS node valency for points assets so NWR snapping prioritizes switch nodes on the matched track
    sig_tps_valency = None
    if is_points_asset:
        pt_nid = sig.get("point_node_id") or sig.get("end_node_id") or sig.get("start_node_id")
        if pt_nid:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM edges WHERE start_node_id = %s OR end_node_id = %s",
                (pt_nid, pt_nid),
            )
            vc_row = cur.fetchone()
            sig_tps_valency = max(3, int(vc_row["cnt"])) if vc_row else 3
        else:
            sig_tps_valency = 3

    nwr_cross_links = find_nwr_cross_links_for_tps(
        cur,
        sig_elr,
        sig_km_val,
        direction_hint=sig_dir,
        track_name_hint=sig_track_hint,
        tps_valency=sig_tps_valency,
        item_label=clean_item_name,
        is_extrapolated=is_sig_extrapolated,
        asset_type="points" if is_points_asset else "signal",
        preferred_fid=nwr_fid,
        match_source=sig_match_source,
        platform_num=sig_identity.get("platform_num"),
        saved_fid=saved_override_fid,
        secondary_dir=sig_identity.get("secondary_dir"),
        edge_vmax=approach_edge.get("vmax") if approach_edge else None,
        tps_node_ids=[n for n in (sig["start_node_id"], sig["end_node_id"]) if n is not None],
    )

    ov_summary = (
        f"{len(enriched_overlaps)} overlap(s): "
        + ", ".join(
            f"#{o['index']} ({o['length_meters']}m / {o['length_yards']}yd @ {o['vmax']} km/h)"
            for o in enriched_overlaps
        )
        if enriched_overlaps
        else "None defined"
    )

    type_label = (
        "Points"
        if is_points_asset
        else ("Buffer Stop" if sig["bumper"] else "Signal")
    )
    decoded = {
        f"{type_label} PK / XML ID": f"{sig['pk']} / {sig['id']}",
        f"Master {type_label} ID & Name": f"#{sig['signal_id']} — {sig['master_name']}",
        f"{type_label} Plate Name": sig["name"] or sig["master_name"],
        "Interlocking System Class": f"{ilock_name} (ID {sig['interlocking_sys_id']})",
        "Base Operating Type": (
            f"{base_sig_type or '—'} ({aspects_cnt} aspects)"
            if aspects_cnt
            else (base_sig_type or "—")
        ),
        "Interlocking Route Set / Release Time": (
            f"Set: {set_time} s • Release/Freeing: {freeing_time} s "
            f"(Release Mode: {ilock_attrs.get('releasemode', '—')})"
        ),
        "Engineer's Line Ref (ELR) & Mileage": (
            f"{sig_elr or '—'} • {meters_to_miles_chains(sig_km_val) or '—'}"
        ),
        "Directed Track Connection": (
            f"#{sig['start_node_id']} → #{sig['end_node_id']}"
            + (
                f" (Edge #{approach_edge['edge_id']}, {approach_edge['length']}m, "
                f"Vmax {approach_edge['vmax']} km/h)"
                if approach_edge else ""
            )
            if sig["start_node_id"]
            else f"Point Node #{sig['point_node_id']}"
        ),
        "Approach Blocks (In Rear)": (
            f"{len(approach_blocks)} block(s) terminating at {type_label.lower()}"
        ),
        "Protected Blocks (In Advance)": (
            f"{len(outgoing_blocks)} block(s) governed by {type_label.lower()}"
        ),
        "Safety Overlaps (Security Sections)": ov_summary,
        "Overlap Release Timer": (
            f"{sig['sec_sect_freeing_time']} s "
            f"(Timed Release Active: {sig['use_sec_sect_freeing_time']})"
        ),
        "Buffer Stop / Bumper": (
            "Yes (Bumper / Buffer Stop)" if sig["bumper"] else "No"
        ),
        "Temporarily Closed": sig["tmp_closed"],
        "Schematic Coordinates (x, y)": f"({sig['x']}, {sig['y']})",
        "Validity Window": f"{sig['valid_from']} → {sig['valid_to']}",
    }

    if is_points_asset:
        asset_title = clean_item_name
        asset_sub = (
            f"Points • {ilock_name} • {sig_elr or base_sig_type or ''} • "
            f"{len(outgoing_blocks)} Protected Block(s) • "
            f"{len(enriched_overlaps)} Overlap(s)"
        )
    else:
        asset_title = clean_item_name
        asset_sub = (
            f"{ilock_name} • {sig_elr or base_sig_type or ''} • "
            f"{len(outgoing_blocks)} Protected Block(s) • "
            f"{len(enriched_overlaps)} Overlap(s)"
        )

    return {
        "asset_type": "points" if is_points_asset else "signal",
        "id": sig["pk"],
        "title": asset_title,
        "subtitle": asset_sub,
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



def _get_track_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a TPS station track, its ordered node path, and NWR match."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
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
    trk_parsed = _parse_station_track_rows([trk])
    nwr_cross_links = find_nwr_cross_links_for_tps(
        cur,
        trk_elr,
        trk_km,
        end_km_meters=trk_end_km,
        direction_hint=trk_parsed["direction_hint"],
        track_name_hint=trk_parsed["track_name_hint"] or trk.get("name"),
        item_label=f"Track {trk['name']}",
        asset_type="track",
        preferred_fid=nwr_fid,
        match_source=f"TPS Track ({trk_parsed['label']})" if trk_parsed["label"] else "TPS Track",
        platform_num=trk_parsed.get("platform_num"),
        saved_fid=saved_override_fid,
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



def _get_block_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a TPS signaling block, bounding signals, and track path."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
    cur.execute("SELECT * FROM blocks WHERE pk = %s", (asset_id,))
    blk = cur.fetchone()
    if not blk:
        raise HTTPException(status_code=404, detail="Block not found")

    nids = blk["node_ids"] or []
    p_info = resolve_node_path_details(cur, nids, decode_fn=decode_code)
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
            xs_name = xs["name"] or xs["master_name"]
            xs_is_pts = (
                (xs["interlocking_sys_id"] in (15, 16))
                or (xs_name and xs_name.upper().startswith("POINT"))
            )
            exit_sigs.append({
                "pk": xs["pk"],
                "signal_id": xs["signal_id"],
                "name": xs_name,
                "is_points": xs_is_pts,
                "interlocking_desc": decode_code("interlockingsystem", xs["interlocking_sys_id"]),
                "x": xs["x"],
                "y": xs["y"],
            })
            if xs["x"] is not None:
                highlight_points.append({
                    "x": xs["x"],
                    "y": xs["y"],
                    "label": f"Exit {'Points' if xs_is_pts else 'Signal'}: {xs_name}",
                    "color": "#a855f7" if xs_is_pts else "#f59e0b",
                    "group": "points" if xs_is_pts else "signal",
                    "shape": "diamond" if xs_is_pts else "circle",
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
                        "signal_name": xs_name,
                        "length_meters": ov_p["length_meters"],
                        "length_yards": round(ov_p["length_meters"] / 0.9144, 1),
                        "vmax": ss.get("vmax", 0),
                        "node_count": len(ss_nids),
                    })

    for tn in p_info["turnout_nodes"]:
        highlight_points.append({
            "x": tn["x"],
            "y": tn["y"],
            "label": f"Points #{tn['node_id']}",
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
        "Points Traversed": f"{len(p_info['turnout_nodes'])} points",
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
        blk_identity = resolve_tps_track_identity(
            cur,
            nids,
            start_node_id=nids[0] if nids else None,
            end_node_id=nids[-1] if nids else None,
            elr=blk_elr,
            start_km=blk_km,
            end_km=blk_end_km,
        )
        blk_dir = blk_identity["direction_hint"]
        blk_track_hint = blk_identity["track_name_hint"]
        blk_match_source = blk_identity["source"]
        blk_plat_num = blk_identity.get("platform_num")
    else:
        blk_track_hint = None
        blk_match_source = None
        blk_plat_num = None

    nwr_cross_links = find_nwr_cross_links_for_tps(
        cur,
        blk_elr,
        blk_km,
        end_km_meters=blk_end_km,
        direction_hint=blk_dir,
        track_name_hint=blk_track_hint,
        item_label=f"Block #{blk['block_id']}",
        is_extrapolated=is_blk_extrapolated,
        asset_type="block",
        preferred_fid=nwr_fid,
        match_source=blk_match_source,
        platform_num=blk_plat_num,
        saved_fid=saved_override_fid,
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
            "min_vmax": p_info["min_vmax"],
            "max_vmax": p_info["max_vmax"],
            "speed_profile": p_info.get("speed_profile", []),
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



def _get_route_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a TPS train route, ordered nodes/edges, and bounding signals."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
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

    rt_identity = resolve_tps_track_identity(
        cur,
        rt.get("node_ids") or [],
        start_node_id=rt["node_ids"][0] if rt.get("node_ids") else None,
        end_node_id=rt["node_ids"][-1] if rt.get("node_ids") else None,
        elr=rt_elr,
        start_km=rt_km,
        end_km=rt_end_km,
    )
    nwr_cross_links = find_nwr_cross_links_for_tps(
        cur,
        rt_elr,
        rt_km,
        end_km_meters=rt_end_km,
        direction_hint=rt_identity["direction_hint"],
        track_name_hint=rt_identity["track_name_hint"],
        item_label=f"Route #{rt['route_id']}",
        is_extrapolated=is_rt_extrapolated,
        asset_type="route",
        preferred_fid=nwr_fid,
        match_source=rt_identity["source"],
        platform_num=rt_identity.get("platform_num"),
        saved_fid=saved_override_fid,
    )

    rt_path = resolve_node_path_details(cur, rt.get("node_ids") or [], decode_fn=decode_code)

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
            "length_meters": rt_path["length_meters"],
            "min_vmax": rt_path["min_vmax"],
            "max_vmax": rt_path["max_vmax"],
            "speed_profile": rt_path.get("speed_profile", []),
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



def _get_attributesection_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a TPS attribute section (ELR/line segment) and member assets."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
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
        asset_type="attributesection",
        preferred_fid=nwr_fid,
        saved_fid=saved_override_fid,
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



def _get_graphictext_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a schematic graphic text label."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
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



def _get_graphicvector_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a schematic graphic vector polyline."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
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



def _get_annotation_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a schematic annotation marker."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
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



def _get_polyarea_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a schematic polygon area."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
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



def _get_modeltrain_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a TPS model train, traction curves, and velocity profile."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
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



def _get_tractionunit_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a TPS traction unit and its tractive-effort/resistance curves."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
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



def _get_stationbank_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a TPS station bank and its associated stations/tracks."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
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


_ASSET_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "station": _get_station_asset,
    "edge": _get_edge_asset,
    "node": _get_node_asset,
    "signal": _get_signal_or_points_asset,
    "points": _get_signal_or_points_asset,
    "track": _get_track_asset,
    "block": _get_block_asset,
    "route": _get_route_asset,
    "attributesection": _get_attributesection_asset,
    "graphictext": _get_graphictext_asset,
    "graphicvector": _get_graphicvector_asset,
    "annotation": _get_annotation_asset,
    "polyarea": _get_polyarea_asset,
    "modeltrain": _get_modeltrain_asset,
    "tractionunit": _get_tractionunit_asset,
    "stationbank": _get_stationbank_asset,
}


@router.get("/api/asset/{asset_type}/{asset_id}")
def api_get_asset(
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int] = Query(default=None),
) -> dict[str, Any]:
    """Inspect any TPS asset by type and ID, including NWR geographic cross-links.

    Args:
        asset_type: Canonical TPS asset type (e.g. ``"signal"``, ``"points"``,
            ``"edge"``, ``"node"``, ``"station"``, ``"route"``).
        asset_id: Database identifier (or ``pk``) of the asset.
        nwr_fid: Optional transient manual NWR centreline feature ID override.

    Returns:
        Structured inspection payload for the right-hand detail panel.

    Raises:
        HTTPException: 400 if ``asset_type`` is unknown, or 404 if not found.
    """
    handler = _ASSET_HANDLERS.get(asset_type)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"Unknown asset type: {asset_type}")

    lookup = load_lookup_cache()
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            saved_override_fid = get_saved_track_override(cur, asset_type, asset_id)
            return handler(cur, asset_type, asset_id, nwr_fid, saved_override_fid, lookup)
