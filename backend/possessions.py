"""Geometry resolution helpers for Engineers' Possessions and inner Worksites.

Resolves both TPS Schematic (``netx, nety``) and NWR Geographic Track Model
(OSGB36 Easting/Northing) polylines and limit markers from either:
  1. Signal / Points limits (including ``at``, ``approach``, ``beyond``, and
     ``buffer`` qualifiers), using bounded BFS shortest-path traversal over TPS
     ``edges`` and clipped NWR ``nwr_track_centrelines``.
  2. ELR + Start/End Mileage + Track ID(s), clipping NWR centrelines directly
     and resolving correlated TPS edges in that ELR mileage range.
"""

from typing import Any, Optional

from config import decode_code, get_saved_track_override
from geometry import meters_to_miles_chains, slice_polyline
from possession_routing import find_nwr_straight_path, find_tps_straight_path
from routers.asset_handlers.geo import get_asset_geo_point

QUALIFIER_PREFIXES: dict[str, str] = {
    "at": "",
    "approach": "Approach to ",
    "beyond": "Beyond ",
    "buffer": "Buffer / ",
}


def format_limit_label(
    base_label: Optional[str],
    qualifier: Optional[str],
    role: str,
) -> str:
    """Format a human-readable protecting/limit marker label."""
    clean = (base_label or "").strip() or role
    prefix = QUALIFIER_PREFIXES.get((qualifier or "at").lower(), "")
    if prefix and not clean.lower().startswith(prefix.lower()):
        clean = f"{prefix}{clean}"
    return f"{role}: {clean}"


def _compute_bbox(ways: list[dict[str, Any]], markers: list[dict[str, Any]]) -> Optional[list[float]]:
    """Compute ``[min_x, min_y, max_x, max_y]`` across ways and markers."""
    min_x, min_y = float("inf"), float("inf")
    max_x, max_y = float("-inf"), float("-inf")
    for w in ways:
        for pt in w.get("coords") or []:
            if len(pt) >= 2 and pt[0] is not None and pt[1] is not None:
                min_x = min(min_x, float(pt[0]))
                max_x = max(max_x, float(pt[0]))
                min_y = min(min_y, float(pt[1]))
                max_y = max(max_y, float(pt[1]))
    for m in markers:
        if m.get("x") is not None and m.get("y") is not None:
            min_x = min(min_x, float(m["x"]))
            max_x = max(max_x, float(m["x"]))
            min_y = min(min_y, float(m["y"]))
            max_y = max(max_y, float(m["y"]))
    if min_x == float("inf"):
        return None
    return [round(min_x, 2), round(min_y, 2), round(max_x, 2), round(max_y, 2)]


def _resolve_tps_endpoint(
    cur: Any,
    asset_type: Optional[str],
    asset_id: Optional[int],
    qualifier: str = "at",
) -> Optional[dict[str, Any]]:
    """Resolve a TPS signal, points, or node endpoint to TPS + OSGB36 coordinates."""
    if not asset_type or asset_id is None:
        return None

    node_id: Optional[int] = None
    tps_x: Optional[float] = None
    tps_y: Optional[float] = None
    label: str = f"{asset_type} #{asset_id}"
    elr: Optional[str] = None
    km_value: Optional[int] = None
    qual = (qualifier or "at").lower()

    if asset_type in ("signal", "points"):
        cur.execute(
            """
            SELECT pk, signal_id, name, master_name, start_node_id, end_node_id,
                   point_node_id, x, y, km_region_id
            FROM signals WHERE pk = %s
            """,
            (asset_id,),
        )
        sig = cur.fetchone()
        if not sig:
            return None
        label = sig["name"] or sig["master_name"] or f"Signal #{sig['signal_id']}"
        tps_x = float(sig["x"]) if sig["x"] is not None else None
        tps_y = float(sig["y"]) if sig["y"] is not None else None
        # Anchor at the asset's actual physical node (end_node for signals/buffers,
        # point_node for points). Qualifiers ("approach"/"beyond"/"buffer") describe
        # operational PLB placement rather than jumping back to an approach-edge tail.
        if asset_type == "points":
            raw_nid = sig["point_node_id"] or sig["end_node_id"] or sig["start_node_id"]
            node_id = int(raw_nid) if raw_nid is not None else None
        else:
            raw_nid = sig["end_node_id"] or sig["start_node_id"]
            node_id = int(raw_nid) if raw_nid is not None else None

        if node_id is not None:
            cur.execute(
                "SELECT netx, nety, km_region_id, km_value FROM nodes WHERE node_id = %s",
                (node_id,),
            )
            nd = cur.fetchone()
            if nd:
                if tps_x is None and nd["netx"] is not None:
                    tps_x, tps_y = float(nd["netx"]), float(nd["nety"])
                km_reg = sig["km_region_id"] or nd["km_region_id"]
                elr = decode_code("kmregionmasterdesc", km_reg) if km_reg else None
                km_value = int(nd["km_value"]) if nd["km_value"] is not None else None
    elif asset_type == "node":
        cur.execute(
            "SELECT node_id, netx, nety, km_region_id, km_value FROM nodes WHERE node_id = %s",
            (asset_id,),
        )
        nd = cur.fetchone()
        if not nd:
            return None
        node_id = int(nd["node_id"])
        tps_x = float(nd["netx"]) if nd["netx"] is not None else None
        tps_y = float(nd["nety"]) if nd["nety"] is not None else None
        label = f"Node #{node_id}"
        elr = decode_code("kmregionmasterdesc", nd["km_region_id"]) if nd["km_region_id"] else None
        km_value = int(nd["km_value"]) if nd["km_value"] is not None else None
    else:
        return None

    nwr_x: Optional[float] = None
    nwr_y: Optional[float] = None
    nwr_track_id: Optional[str] = None
    try:
        saved_fid = get_saved_track_override(cur, asset_type, int(asset_id))
        geo = get_asset_geo_point(cur, asset_type, int(asset_id), saved_fid)
        if geo.get("x") is not None and geo.get("y") is not None:
            nwr_x = float(geo["x"])
            nwr_y = float(geo["y"])
        if not elr and geo.get("elr"):
            elr = geo["elr"]
        if geo.get("track_id"):
            nwr_track_id = str(geo["track_id"])
    except Exception:
        pass

    return {
        "asset_type": asset_type,
        "asset_id": int(asset_id),
        "node_id": node_id,
        "x": tps_x,
        "y": tps_y,
        "nwr_x": nwr_x,
        "nwr_y": nwr_y,
        "track_id": nwr_track_id,
        "label": label,
        "elr": elr,
        "km_value": km_value,
        "qualifier": qual,
    }


def _resolve_nwr_ways_by_elr_mileage(
    cur: Any,
    elr: str,
    start_m: int,
    end_m: int,
    track_ids: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Clip NWR track centrelines on ``elr`` between ``[lo_m, hi_m]``."""
    lo_m, hi_m = min(start_m, end_m), max(start_m, end_m)
    elr_u = elr.strip().upper()
    if track_ids:
        cur.execute(
            """
            SELECT fid, elr, track_id, start_meters, end_meters, coords
            FROM nwr_track_centrelines
            WHERE UPPER(elr) = %s
              AND track_id = ANY(%s)
              AND COALESCE(end_meters, 0) >= %s
              AND COALESCE(start_meters, 0) <= %s
            LIMIT 80
            """,
            (elr_u, [str(t) for t in track_ids], lo_m, hi_m),
        )
    else:
        cur.execute(
            """
            SELECT fid, elr, track_id, start_meters, end_meters, coords
            FROM nwr_track_centrelines
            WHERE UPPER(elr) = %s
              AND COALESCE(end_meters, 0) >= %s
              AND COALESCE(start_meters, 0) <= %s
            LIMIT 80
            """,
            (elr_u, lo_m, hi_m),
        )

    ways: list[dict[str, Any]] = []
    for row in cur.fetchall():
        raw_coords = row.get("coords") or []
        if len(raw_coords) < 2:
            continue
        c_start = float(row.get("start_meters") or 0.0)
        c_end = float(row.get("end_meters") or 0.0)
        if c_end > c_start:
            clipped = slice_polyline(raw_coords, c_start, c_end, lo_m, hi_m)
        else:
            clipped = raw_coords
        if len(clipped) >= 2:
            ways.append(
                {
                    "fid": row["fid"],
                    "elr": row["elr"],
                    "track_id": row["track_id"],
                    "coords": clipped,
                }
            )
    return ways


def _resolve_tps_ways_by_elr_mileage(
    cur: Any,
    elr: str,
    start_m: int,
    end_m: int,
) -> list[dict[str, Any]]:
    """Resolve TPS schematic edges on ``elr`` within ``[lo_m, hi_m]``."""
    lo_m, hi_m = min(start_m, end_m), max(start_m, end_m)
    cur.execute(
        """
        SELECT code_id FROM lookup_codes
        WHERE category = 'kmregionmasterdesc'
          AND (UPPER(label) = %s OR UPPER(vanilla_text) = %s)
        """,
        (elr.strip().upper(), elr.strip().upper()),
    )
    reg_ids = [int(r["code_id"]) for r in cur.fetchall() if str(r["code_id"]).isdigit()]
    if not reg_ids:
        return []

    cur.execute(
        """
        SELECT e.x0, e.y0, e.x1, e.y1
        FROM edges e
        JOIN nodes sn ON sn.node_id = e.start_node_id
        JOIN nodes en ON en.node_id = e.end_node_id
        WHERE (sn.km_region_id = ANY(%s) OR en.km_region_id = ANY(%s))
          AND GREATEST(COALESCE(sn.km_value, 0), COALESCE(en.km_value, 0)) >= %s
          AND LEAST(COALESCE(sn.km_value, 0), COALESCE(en.km_value, 0)) <= %s
        LIMIT 400
        """,
        (reg_ids, reg_ids, lo_m, hi_m),
    )
    ways: list[dict[str, Any]] = []
    for row in cur.fetchall():
        if row["x0"] is not None and row["x1"] is not None:
            ways.append(
                {
                    "coords": [
                        [float(row["x0"]), float(row["y0"])],
                        [float(row["x1"]), float(row["y1"])],
                    ]
                }
            )
    return ways


def _extract_limit_specs(line: dict[str, Any], side: str) -> list[dict[str, Any]]:
    """Extract normalized From or To limit specs from a line dict (supports legacy single-endpoint fields)."""
    key = "from_limits" if side == "from" else "to_limits"
    raw = line.get(key)
    if isinstance(raw, list) and raw:
        return [
            item
            for item in raw
            if isinstance(item, dict)
            and (item.get("asset_id") is not None or (item.get("label") or "").strip())
        ]
    prefix = "start" if side == "from" else "end"
    aid = line.get(f"{prefix}_asset_id")
    lbl = (line.get(f"{prefix}_label") or "").strip()
    if aid is not None or lbl:
        return [
            {
                "asset_type": line.get(f"{prefix}_asset_type"),
                "asset_id": aid,
                "label": lbl or None,
                "qualifier": line.get(f"{prefix}_qualifier") or "at",
            }
        ]
    return []


def _resolve_pair_nwr_ways(
    cur: Any,
    start_ep: dict[str, Any],
    end_ep: dict[str, Any],
    explicit_track_ids: list[str],
) -> list[dict[str, Any]]:
    """Resolve NWR geographic ways between one From endpoint and one To endpoint."""
    ways = find_nwr_straight_path(cur, start_ep, end_ep)
    if ways:
        return ways
    same_elr = (
        bool(start_ep["elr"])
        and bool(end_ep["elr"])
        and start_ep["elr"].upper() == end_ep["elr"].upper()
    )
    if same_elr and start_ep["km_value"] is not None and end_ep["km_value"] is not None:
        matched_tids = sorted(
            {str(t) for t in (start_ep.get("track_id"), end_ep.get("track_id")) if t}
        )
        effective_tids = explicit_track_ids or matched_tids or None
        return _resolve_nwr_ways_by_elr_mileage(
            cur,
            start_ep["elr"],
            start_ep["km_value"],
            end_ep["km_value"],
            effective_tids,
        )
    if start_ep["nwr_x"] is not None and end_ep["nwr_x"] is not None:
        return [{"coords": [[start_ep["nwr_x"], start_ep["nwr_y"]], [end_ep["nwr_x"], end_ep["nwr_y"]]]}]
    return []


def _resolve_single_line_geometries(
    cur: Any,
    line: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve TPS & NWR ways and markers for a single blocked line entry (supports multi-From/To)."""
    limit_mode = (line.get("limit_mode") or "signal_points").lower()
    line_name = (line.get("line_name") or "").strip()
    tps_ways: list[dict[str, Any]] = []
    tps_markers: list[dict[str, Any]] = []
    nwr_ways: list[dict[str, Any]] = []
    nwr_markers: list[dict[str, Any]] = []

    if limit_mode == "elr_mileage":
        elr = (line.get("elr") or "").strip().upper()
        start_m = line.get("start_meters")
        end_m = line.get("end_meters")
        track_ids = line.get("track_ids") or []
        if elr and start_m is not None and end_m is not None:
            s_m, e_m = int(start_m), int(end_m)
            nwr_ways = _resolve_nwr_ways_by_elr_mileage(cur, elr, s_m, e_m, track_ids)
            tps_ways = _resolve_tps_ways_by_elr_mileage(cur, elr, s_m, e_m)
            s_lbl = f"PLB Start: {elr} {meters_to_miles_chains(s_m) or f'{s_m}m'}"
            e_lbl = f"PLB End: {elr} {meters_to_miles_chains(e_m) or f'{e_m}m'}"
            if nwr_ways:
                first_pt = nwr_ways[0]["coords"][0]
                last_pt = nwr_ways[-1]["coords"][-1]
                nwr_markers.append({"x": first_pt[0], "y": first_pt[1], "label": s_lbl, "role": "start"})
                nwr_markers.append({"x": last_pt[0], "y": last_pt[1], "label": e_lbl, "role": "end"})
            if tps_ways:
                tps_markers.append({"x": tps_ways[0]["coords"][0][0], "y": tps_ways[0]["coords"][0][1], "label": s_lbl, "role": "start"})
                tps_markers.append({"x": tps_ways[-1]["coords"][-1][0], "y": tps_ways[-1]["coords"][-1][1], "label": e_lbl, "role": "end"})
    else:
        from_specs = _extract_limit_specs(line, "from")
        to_specs = _extract_limit_specs(line, "to")

        resolved_froms: list[dict[str, Any]] = []
        resolved_tos: list[dict[str, Any]] = []

        for spec in from_specs:
            ep = _resolve_tps_endpoint(cur, spec.get("asset_type"), spec.get("asset_id"), spec.get("qualifier") or "at")
            if not ep:
                continue
            resolved_froms.append(ep)
            lbl = format_limit_label(spec.get("label") or ep["label"], spec.get("qualifier"), "PLB Start")
            if ep["x"] is not None:
                tps_markers.append({"x": ep["x"], "y": ep["y"], "label": lbl, "role": "start"})
            if ep["nwr_x"] is not None:
                nwr_markers.append({"x": ep["nwr_x"], "y": ep["nwr_y"], "label": lbl, "role": "start"})

        for spec in to_specs:
            ep = _resolve_tps_endpoint(cur, spec.get("asset_type"), spec.get("asset_id"), spec.get("qualifier") or "at")
            if not ep:
                continue
            resolved_tos.append(ep)
            lbl = format_limit_label(spec.get("label") or ep["label"], spec.get("qualifier"), "PLB End")
            if ep["x"] is not None:
                tps_markers.append({"x": ep["x"], "y": ep["y"], "label": lbl, "role": "end"})
            if ep["nwr_x"] is not None:
                nwr_markers.append({"x": ep["nwr_x"], "y": ep["nwr_y"], "label": lbl, "role": "end"})

        seen_tps_edges: set[tuple[tuple[float, float], tuple[float, float]]] = set()
        seen_nwr_ways: set[tuple[Any, tuple[float, float], tuple[float, float]]] = set()
        explicit_tids = line.get("track_ids") or []

        for start_ep in resolved_froms:
            for end_ep in resolved_tos:
                path_coords = find_tps_straight_path(cur, start_ep["node_id"], end_ep["node_id"])
                if len(path_coords) < 2 and start_ep["x"] is not None and end_ep["x"] is not None:
                    path_coords = [[start_ep["x"], start_ep["y"]], [end_ep["x"], end_ep["y"]]]
                if len(path_coords) >= 2:
                    # Deduplicate overlapping TPS edges when fanning in/out across shared trunk sections
                    cur_run: list[list[float]] = []
                    for i in range(len(path_coords) - 1):
                        p0, p1 = path_coords[i], path_coords[i + 1]
                        k0 = (round(float(p0[0]), 2), round(float(p0[1]), 2))
                        k1 = (round(float(p1[0]), 2), round(float(p1[1]), 2))
                        ekey = (k0, k1) if k0 <= k1 else (k1, k0)
                        if ekey in seen_tps_edges:
                            if len(cur_run) >= 2:
                                tps_ways.append({"coords": cur_run})
                            cur_run = []
                        else:
                            seen_tps_edges.add(ekey)
                            if not cur_run:
                                cur_run = [p0, p1]
                            else:
                                cur_run.append(p1)
                    if len(cur_run) >= 2:
                        tps_ways.append({"coords": cur_run})

                for nw in _resolve_pair_nwr_ways(cur, start_ep, end_ep, explicit_tids):
                    cs = nw.get("coords") or []
                    if len(cs) < 2:
                        continue
                    c0 = (round(float(cs[0][0]), 1), round(float(cs[0][1]), 1))
                    c1 = (round(float(cs[-1][0]), 1), round(float(cs[-1][1]), 1))
                    wkey = (nw.get("fid"), c0, c1) if c0 <= c1 else (nw.get("fid"), c1, c0)
                    if wkey not in seen_nwr_ways:
                        seen_nwr_ways.add(wkey)
                        nwr_ways.append(nw)

    if line_name:
        for coll in (tps_ways, tps_markers, nwr_ways, nwr_markers):
            for item in coll:
                item["line_name"] = line_name

    return tps_ways, tps_markers, nwr_ways, nwr_markers


def resolve_possession_geometries(
    cur: Any,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve both TPS Schematic and NWR OSGB36 geometries for a single- or multi-line possession."""
    raw_lines = payload.get("lines") or []
    line_entries = raw_lines if isinstance(raw_lines, list) and len(raw_lines) > 0 else [payload]

    tps_ways: list[dict[str, Any]] = []
    tps_markers: list[dict[str, Any]] = []
    nwr_ways: list[dict[str, Any]] = []
    nwr_markers: list[dict[str, Any]] = []

    for line in line_entries:
        tw, tm, nw, nm = _resolve_single_line_geometries(cur, line)
        tps_ways.extend(tw)
        tps_markers.extend(tm)
        nwr_ways.extend(nw)
        nwr_markers.extend(nm)

    tps_geom = {"ways": tps_ways, "markers": tps_markers, "bbox": _compute_bbox(tps_ways, tps_markers)}
    nwr_geom = {"ways": nwr_ways, "markers": nwr_markers, "bbox": _compute_bbox(nwr_ways, nwr_markers)}
    return tps_geom, nwr_geom


def resolve_worksite_geometries(
    cur: Any,
    parent_possession: dict[str, Any],
    ws_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve TPS & NWR geometries for an inner Engineering Supervisor (ES) worksite."""
    has_assets = ws_payload.get("start_asset_type") and ws_payload.get("start_asset_id") is not None
    sub_payload = {
        "limit_mode": "signal_points" if has_assets else "elr_mileage",
        "start_asset_type": ws_payload.get("start_asset_type"),
        "start_asset_id": ws_payload.get("start_asset_id"),
        "start_label": ws_payload.get("wmb_start_label") or "WMB Start",
        "start_qualifier": "at",
        "end_asset_type": ws_payload.get("end_asset_type"),
        "end_asset_id": ws_payload.get("end_asset_id"),
        "end_label": ws_payload.get("wmb_end_label") or "WMB End",
        "end_qualifier": "at",
        "elr": parent_possession.get("elr"),
        "start_meters": ws_payload.get("start_meters"),
        "end_meters": ws_payload.get("end_meters"),
        "track_ids": parent_possession.get("track_ids") or [],
    }
    tps_geom, nwr_geom = resolve_possession_geometries(cur, sub_payload)
    for geom in (tps_geom, nwr_geom):
        for m in geom.get("markers", []):
            m["label"] = m["label"].replace("PLB Start:", "WMB Start:").replace("PLB End:", "WMB End:")
    return tps_geom, nwr_geom
