"""Lightweight geographic coordinate resolver for TPS route endpoints.

Powers ``GET /api/asset/{asset_type}/{asset_id}/geo`` so Dual-View route
highlighting can snap entrance/exit signals, points, or buffer nodes to their
exact OSGB36 ``(x, y)`` coordinates without computing full blocks, overlaps,
or speed profiles.
"""

import re
from typing import Any, Optional

from fastapi import HTTPException

from config import decode_code
from geometry import clean_rail_asset_name
from track_identity import resolve_tps_track_identity
from track_matcher import find_nwr_cross_links_for_tps


def _extract_geo_point(nwr_cross: Optional[dict[str, Any]]) -> tuple[Optional[float], Optional[float], Optional[str], Optional[str], bool]:
    """Extract primary OSGB36 ``(x, y)`` and snap metadata from ``nwr_cross_links``."""
    if not nwr_cross:
        return None, None, None, None, False
    snap = nwr_cross.get("geo_snap") or {}
    elr = snap.get("elr")
    mileage_str = snap.get("mileage_str")
    is_extrap = bool(snap.get("is_extrapolated"))

    node_pts = nwr_cross.get("node_points") or []
    pt = node_pts[0] if node_pts else nwr_cross.get("node_point")
    if pt and pt.get("x") is not None and pt.get("y") is not None:
        return float(pt["x"]), float(pt["y"]), elr, mileage_str, is_extrap
    if snap.get("x") is not None and snap.get("y") is not None:
        return float(snap["x"]), float(snap["y"]), elr, mileage_str, is_extrap
    return None, None, elr, mileage_str, is_extrap


def _resolve_signal_or_points_geo(
    cur: Any,
    asset_type: str,
    asset_id: int,
    saved_override_fid: Optional[int],
) -> dict[str, Any]:
    """Resolve lightweight OSGB36 coordinates for a TPS signal or points asset."""
    cur.execute(
        """
        SELECT pk, id, signal_id, name, master_name, interlocking_sys_id,
               km_region_id, start_node_id, end_node_id, point_node_id
        FROM signals WHERE pk = %s
        """,
        (asset_id,),
    )
    sig = cur.fetchone()
    if not sig:
        cur.execute(
            """
            SELECT pk, id, signal_id, name, master_name, interlocking_sys_id,
                   km_region_id, start_node_id, end_node_id, point_node_id
            FROM signals WHERE id = %s LIMIT 1
            """,
            (asset_id,),
        )
        sig = cur.fetchone()
    if not sig:
        raise HTTPException(status_code=404, detail="Signal not found")

    is_points = (
        asset_type == "points"
        or sig["interlocking_sys_id"] in (15, 16)
        or (sig["name"] and sig["name"].upper().startswith("POINT"))
    )
    anchor_nid = (
        (sig["point_node_id"] or sig["end_node_id"] or sig["start_node_id"])
        if is_points
        else (sig["end_node_id"] or sig["start_node_id"])
    )

    sig_km_reg = sig["km_region_id"]
    sig_km_val = 0
    is_extrap = False
    if anchor_nid:
        cur.execute(
            "SELECT km_region_id, km_value FROM nodes WHERE node_id = %s",
            (anchor_nid,),
        )
        nd = cur.fetchone()
        if nd:
            if not sig_km_reg and nd["km_region_id"]:
                sig_km_reg = nd["km_region_id"]
            if nd["km_value"]:
                sig_km_val = nd["km_value"]

    approach_edge = None
    if sig["start_node_id"] and sig["end_node_id"]:
        cur.execute(
            "SELECT vmax FROM edges WHERE start_node_id = %s AND end_node_id = %s LIMIT 1",
            (sig["start_node_id"], sig["end_node_id"]),
        )
        approach_edge = cur.fetchone()

    if not sig_km_reg or not sig_km_val:
        for alt_nid in (sig["start_node_id"], sig["end_node_id"]):
            if not alt_nid or alt_nid == anchor_nid:
                continue
            cur.execute(
                "SELECT km_region_id, km_value FROM nodes WHERE node_id = %s",
                (alt_nid,),
            )
            alt_nd = cur.fetchone()
            if alt_nd and alt_nd["km_region_id"] and alt_nd["km_value"]:
                sig_km_reg = alt_nd["km_region_id"]
                sig_km_val = alt_nd["km_value"]
                break

    if (not sig_km_reg or not sig_km_val) and anchor_nid:
        cur.execute(
            """
            SELECT e.length, e.start_node_id,
                   sn.km_region_id AS s_reg, sn.km_value AS s_km,
                   en.km_region_id AS e_reg, en.km_value AS e_km
            FROM edges e
            LEFT JOIN nodes sn ON sn.node_id = e.start_node_id
            LEFT JOIN nodes en ON en.node_id = e.end_node_id
            WHERE e.start_node_id = %s OR e.end_node_id = %s
            LIMIT 6
            """,
            (anchor_nid, anchor_nid),
        )
        for ce in cur.fetchall():
            if ce["s_reg"] and ce["s_km"]:
                sig_km_reg = ce["s_reg"]
                sig_km_val = ce["s_km"] + (ce["length"] or 0 if ce["start_node_id"] != anchor_nid else 0)
                is_extrap = True
                break
            if ce["e_reg"] and ce["e_km"]:
                sig_km_reg = ce["e_reg"]
                sig_km_val = ce["e_km"]
                is_extrap = True
                break

    sig_elr = decode_code("kmregionmasterdesc", sig_km_reg) if sig_km_reg else None
    sig_start_km, sig_end_km = None, None
    if sig["start_node_id"] and sig["end_node_id"]:
        cur.execute(
            "SELECT node_id, km_value FROM nodes WHERE node_id IN (%s, %s)",
            (sig["start_node_id"], sig["end_node_id"]),
        )
        km_map = {r["node_id"]: r["km_value"] for r in cur.fetchall()}
        sig_start_km = km_map.get(sig["start_node_id"])
        sig_end_km = km_map.get(sig["end_node_id"])

    sig_identity = resolve_tps_track_identity(
        cur,
        [sig["start_node_id"], sig["end_node_id"]],
        start_node_id=sig["start_node_id"],
        end_node_id=sig["end_node_id"],
        elr=sig_elr,
        start_km=sig_start_km,
        end_km=sig_end_km,
    )
    sig_dir = sig_identity["direction_hint"]
    if not sig_dir:
        sig_name_str = (sig["name"] or sig["master_name"] or "").strip().upper()
        if re.search(r"\b(?:DOWN|DN)\b", sig_name_str):
            sig_dir = "down"
        elif re.search(r"\bUP\b", sig_name_str):
            sig_dir = "up"

    tps_valency = None
    if is_points:
        pt_nid = sig.get("point_node_id") or sig.get("end_node_id") or sig.get("start_node_id")
        if pt_nid:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM edges WHERE start_node_id = %s OR end_node_id = %s",
                (pt_nid, pt_nid),
            )
            vc = cur.fetchone()
            tps_valency = max(3, int(vc["cnt"])) if vc else 3
        else:
            tps_valency = 3

    nwr_cross = find_nwr_cross_links_for_tps(
        cur,
        sig_elr,
        sig_km_val,
        direction_hint=sig_dir,
        track_name_hint=sig_identity["track_name_hint"],
        tps_valency=tps_valency,
        item_label=clean_rail_asset_name(
            sig["name"] or sig["master_name"] or f"Signal #{sig['signal_id']}",
            "points" if is_points else "signal",
        ),
        is_extrapolated=is_extrap,
        asset_type="points" if is_points else "signal",
        match_source=sig_identity["source"],
        platform_num=sig_identity.get("platform_num"),
        saved_fid=saved_override_fid,
        secondary_dir=sig_identity.get("secondary_dir"),
        edge_vmax=approach_edge.get("vmax") if approach_edge else None,
        tps_node_ids=[n for n in (sig["start_node_id"], sig["end_node_id"]) if n is not None],
    )
    gx, gy, elr, mileage_str, snap_extrap = _extract_geo_point(nwr_cross)
    return {
        "asset_type": "points" if is_points else "signal",
        "id": sig["pk"],
        "x": gx,
        "y": gy,
        "elr": elr or sig_elr,
        "mileage_str": mileage_str,
        "is_extrapolated": snap_extrap or is_extrap,
    }


def _resolve_node_geo(cur: Any, asset_id: int, saved_override_fid: Optional[int]) -> dict[str, Any]:
    """Resolve lightweight OSGB36 coordinates for a TPS track node (e.g. buffer stop)."""
    cur.execute(
        "SELECT node_id, km_region_id, km_value FROM nodes WHERE node_id = %s",
        (asset_id,),
    )
    nd = cur.fetchone()
    if not nd:
        raise HTTPException(status_code=404, detail="Node not found")

    elr_str = decode_code("kmregionmasterdesc", nd["km_region_id"]) if nd["km_region_id"] else None
    node_identity = resolve_tps_track_identity(
        cur,
        [nd["node_id"]],
        start_node_id=nd["node_id"],
        end_node_id=nd["node_id"],
        elr=elr_str,
    )
    nwr_cross = find_nwr_cross_links_for_tps(
        cur,
        elr_str,
        nd["km_value"],
        direction_hint=node_identity["direction_hint"],
        track_name_hint=node_identity["track_name_hint"],
        item_label=f"Node #{nd['node_id']}",
        asset_type="node",
        match_source=node_identity["source"],
        platform_num=node_identity.get("platform_num"),
        saved_fid=saved_override_fid,
        secondary_dir=node_identity.get("secondary_dir"),
        tps_node_ids=[nd["node_id"]],
    )
    gx, gy, elr, mileage_str, snap_extrap = _extract_geo_point(nwr_cross)
    return {
        "asset_type": "node",
        "id": nd["node_id"],
        "x": gx,
        "y": gy,
        "elr": elr or elr_str,
        "mileage_str": mileage_str,
        "is_extrapolated": snap_extrap,
    }


def get_asset_geo_point(
    cur: Any,
    asset_type: str,
    asset_id: int,
    saved_override_fid: Optional[int],
) -> dict[str, Any]:
    """Dispatch lightweight geographic snapping for supported TPS endpoint types."""
    if asset_type in ("signal", "points"):
        return _resolve_signal_or_points_geo(cur, asset_type, asset_id, saved_override_fid)
    if asset_type == "node":
        return _resolve_node_geo(cur, asset_id, saved_override_fid)
    raise HTTPException(
        status_code=400,
        detail=f"Lightweight geo lookup unsupported for asset type: {asset_type}",
    )
