"""TPS block, route, station track, and corridor attribute-section handlers."""

from typing import Any, Optional

from fastapi import HTTPException

from config import decode_code
from geometry import (
    interpolate_from_end,
    meters_to_miles_chains,
    resolve_node_path_details,
)
from track_identity import _parse_station_track_rows, resolve_tps_track_identity
from track_matcher import find_nwr_cross_links_for_tps

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



