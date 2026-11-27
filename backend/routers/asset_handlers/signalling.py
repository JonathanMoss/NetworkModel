"""TPS signalling asset handler: signals, points, overlaps, and blocks."""

import re
from typing import Any, Optional

from fastapi import HTTPException

from config import decode_code
from geometry import (
    clean_rail_asset_name,
    interpolate_from_end,
    meters_to_miles_chains,
    resolve_node_path_details,
)
from track_identity import resolve_tps_track_identity
from track_matcher import find_nwr_cross_links_for_tps

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



