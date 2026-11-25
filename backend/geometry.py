"""Pure rail-domain formatting, polyline geometry, and traction-curve helpers.

All functions in this module are stateless and side-effect free (with the sole
exception of ``resolve_node_path_details`` which reads node/edge geometry from
a supplied cursor), making them fast and straightforward to unit-test.
"""

import math
import re
from typing import Any, Callable, Optional


def meters_to_miles_chains(meters: Optional[int]) -> Optional[str]:
    """Format a metric chainage (metres) as UK railway miles & chains plus km.

    One UK railway chain equals 22 yards = 20.1168 metres, and 80 chains equal
    1 statute mile.

    Args:
        meters: Distance in metres (or ``None``).

    Returns:
        Formatted string such as ``'12m 34.5ch (20.000 km)'``, or ``None`` if
        ``meters`` is ``None``.
    """
    if meters is None:
        return None
    total_chains = meters / 20.1168
    miles = int(total_chains // 80)
    chains = round(total_chains % 80, 1)
    # Guard against rounding 79.96ch up to 80.0ch by carrying into miles.
    if chains >= 80.0:
        miles += 1
        chains -= 80.0
    return f"{miles}m {chains:04.1f}ch ({meters / 1000.0:.3f} km)"


def mile_yards_to_total_yards(val: Optional[float]) -> int:
    """Convert an NWR ``miles.yards`` decimal value (e.g. ``28.1440``) to total yards.

    In Network Rail shapefiles, ``28.1440`` encodes 28 miles and 1,440 yards
    (four fractional digits represent yards out of 1,760, not base-10 decimals).

    Args:
        val: ``miles.yards`` float value (or ``None``).

    Returns:
        Non-negative integer total yards (``miles * 1760 + yards``).
    """
    if val is None:
        return 0
    v = abs(float(val))
    miles = int(v)
    yards = int(round((v - miles) * 10000.0))
    return miles * 1760 + yards


def clean_rail_asset_name(
    raw_name: Optional[str],
    asset_type: str,
    is_bumper: bool = False,
) -> str:
    """Normalize raw TPS signal, points, or buffer-stop names into canonical labels.

    Strips internal TPS node-pair prefixes (e.g. ``'53195-207_'``) and redundant
    abbreviations (``'SIG:'``, ``'POINTS:'``, ``'PTS:'``, ``'BUF:'``) so every
    asset displays consistently in search results and inspector headers.

    Args:
        raw_name: Raw name or ``master_name`` from the TPS ``signals`` table.
        asset_type: Either ``'points'`` or ``'signal'``.
        is_bumper: ``True`` if the TPS ``bumper`` flag is set.

    Returns:
        Canonical display name (e.g. ``'Signal WH302'``, ``'Points 870B'``,
        ``'Buffer Stop'``).
    """
    if not raw_name:
        return "Points" if asset_type == "points" else ("Buffer Stop" if is_bumper else "Signal")

    # Strip internal TPS master_name prefixes like '53195-207_'.
    name = re.sub(r"^\d+-\d+_", "", raw_name.strip())

    if asset_type == "points":
        # Remove leading POINTS:, POINT:, PTS:, and stray SIG: prefixes.
        cleaned = re.sub(r"^(?:POINTS?|PTS)\s*[:\-]?\s*", "", name, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"^(?:SIG(?:NAL)?)\s*[:\-]?\s*", "", cleaned, flags=re.IGNORECASE).strip()
        if not cleaned:
            cleaned = name
        if not re.search(r"\bpoints?\b", cleaned, flags=re.IGNORECASE):
            return f"Points {cleaned}"
        return cleaned

    if is_bumper or "buffer" in name.lower():
        cleaned = re.sub(
            r"^(?:SIG(?:NAL)?|BUFFER\s*STOP?|BUF)\s*[:\-]?\s*",
            "",
            name,
            flags=re.IGNORECASE,
        ).strip()
        if not cleaned or cleaned.lower() in ("buffer", "buffer stop"):
            return "Buffer Stop"
        if not re.search(r"\bbuffer\b", cleaned, flags=re.IGNORECASE):
            return f"Buffer Stop {cleaned}"
        return cleaned

    # Standard running/shunt signal.
    cleaned = re.sub(r"^(?:SIG(?:NAL)?)\s*[:\-]?\s*", "", name, flags=re.IGNORECASE).strip()
    if not cleaned:
        cleaned = name
    if not re.search(r"\bsignal\b", cleaned, flags=re.IGNORECASE):
        return f"Signal {cleaned}"
    return cleaned


def interpolate_from_end(
    coords: list[list[float]],
    edge_lengths: list[float],
    dist_from_end_m: float,
) -> Optional[list[float]]:
    """Walk backwards ``dist_from_end_m`` metres from the end of a schematic path.

    Used to place TPS overlap markers and approach points at exact metric
    offsets along a multi-edge schematic route.

    Args:
        coords: Ordered list of ``[x, y]`` vertices along the path.
        edge_lengths: Metric length (m) for each consecutive edge segment.
        dist_from_end_m: Positive distance in metres to step backwards from the end.

    Returns:
        Interpolated ``[x, y]`` rounded to 2 decimal places, or ``None`` if the
        path is empty/degenerate or ``dist_from_end_m <= 0``.
    """
    if not coords or len(coords) < 2 or not dist_from_end_m or dist_from_end_m <= 0:
        return None
    n_seg = min(len(coords) - 1, len(edge_lengths))
    rem = float(dist_from_end_m)
    for i in range(n_seg - 1, -1, -1):
        seg_len = float(edge_lengths[i] or 0.0)
        p0 = coords[i]
        p1 = coords[i + 1]
        if seg_len <= 0:
            continue
        if rem <= seg_len:
            frac = rem / seg_len
            ix = p1[0] + frac * (p0[0] - p1[0])
            iy = p1[1] + frac * (p0[1] - p1[1])
            return [round(ix, 2), round(iy, 2)]
        rem -= seg_len
    return [round(coords[0][0], 2), round(coords[0][1], 2)]


def interpolate_pt_along_coords(
    coords: list[list[float]],
    c_start_m: float,
    c_end_m: float,
    target_m: float,
) -> Optional[list[float]]:
    """Interpolate an OSGB36 ``[easting, northing]`` point along a polyline by mileage.

    Linearly maps ``target_m`` within ``[c_start_m, c_end_m]`` (clamped to
    ``[0, 1]``) onto the cumulative Euclidean arc length of ``coords``.

    Args:
        coords: Ordered list of ``[x, y]`` vertices of the NWR centreline.
        c_start_m: Start chainage of the centreline in metres.
        c_end_m: End chainage of the centreline in metres.
        target_m: Target mileage in metres.

    Returns:
        Interpolated ``[x, y]`` rounded to 3 decimal places, or ``None`` if
        ``coords`` is empty.
    """
    if not coords:
        return None
    if len(coords) == 1 or abs(c_end_m - c_start_m) < 0.001:
        return [round(coords[0][0], 3), round(coords[0][1], 3)]

    t = (target_m - c_start_m) / (c_end_m - c_start_m)
    t = max(0.0, min(1.0, t))

    seg_lens = []
    total_len = 0.0
    for i in range(len(coords) - 1):
        sl = math.hypot(coords[i + 1][0] - coords[i][0], coords[i + 1][1] - coords[i][1])
        seg_lens.append(sl)
        total_len += sl

    if total_len <= 0.0001:
        return [round(coords[0][0], 3), round(coords[0][1], 3)]

    target_dist = t * total_len
    acc = 0.0
    for i, sl in enumerate(seg_lens):
        if acc + sl >= target_dist or i == len(seg_lens) - 1:
            frac = (target_dist - acc) / sl if sl > 0 else 0.0
            frac = max(0.0, min(1.0, frac))
            ix = coords[i][0] + frac * (coords[i + 1][0] - coords[i][0])
            iy = coords[i][1] + frac * (coords[i + 1][1] - coords[i][1])
            return [round(ix, 3), round(iy, 3)]
        acc += sl
    return [round(coords[-1][0], 3), round(coords[-1][1], 3)]


def slice_polyline(
    coords: list[list[float]],
    c_start_m: float,
    c_end_m: float,
    from_m: float,
    to_m: float,
) -> list[list[float]]:
    """Slice a sub-polyline from an OSGB36 centreline between two mileages.

    Handles both ascending and descending ``from_m``/``to_m`` pairs and clamps
    both endpoints to the centreline's mileage span.

    Args:
        coords: Ordered list of ``[x, y]`` vertices.
        c_start_m: Start chainage of the polyline in metres.
        c_end_m: End chainage of the polyline in metres.
        from_m: First boundary mileage in metres.
        to_m: Second boundary mileage in metres.

    Returns:
        Sliced list of ``[x, y]`` coordinates (or the original ``coords`` if
        degenerate).
    """
    if not coords or len(coords) < 2 or abs(c_end_m - c_start_m) < 0.001:
        return coords

    t1 = (from_m - c_start_m) / (c_end_m - c_start_m)
    t2 = (to_m - c_start_m) / (c_end_m - c_start_m)
    t_start = max(0.0, min(1.0, t1, t2))
    t_end = min(1.0, max(0.0, t1, t2))
    if t_end - t_start < 0.0001:
        pt = interpolate_pt_along_coords(coords, c_start_m, c_end_m, from_m)
        return [pt, pt] if pt else coords

    seg_lens = []
    total_len = 0.0
    for i in range(len(coords) - 1):
        sl = math.hypot(coords[i + 1][0] - coords[i][0], coords[i + 1][1] - coords[i][1])
        seg_lens.append(sl)
        total_len += sl

    if total_len <= 0.0001:
        return coords

    d_start = t_start * total_len
    d_end = t_end * total_len

    out: list[list[float]] = []
    acc = 0.0
    start_added = False
    for i, sl in enumerate(seg_lens):
        seg_start_dist = acc
        seg_end_dist = acc + sl

        if not start_added and (d_start <= seg_end_dist or i == len(seg_lens) - 1):
            frac = (d_start - seg_start_dist) / sl if sl > 0 else 0.0
            frac = max(0.0, min(1.0, frac))
            sx = coords[i][0] + frac * (coords[i + 1][0] - coords[i][0])
            sy = coords[i][1] + frac * (coords[i + 1][1] - coords[i][1])
            out.append([round(sx, 3), round(sy, 3)])
            start_added = True

        if start_added:
            if seg_end_dist < d_end:
                out.append([round(coords[i + 1][0], 3), round(coords[i + 1][1], 3)])
            else:
                frac = (d_end - seg_start_dist) / sl if sl > 0 else 0.0
                frac = max(0.0, min(1.0, frac))
                ex = coords[i][0] + frac * (coords[i + 1][0] - coords[i][0])
                ey = coords[i][1] + frac * (coords[i + 1][1] - coords[i][1])
                out.append([round(ex, 3), round(ey, 3)])
                break
        acc += sl

    return out if len(out) >= 2 else coords


def parse_traction_curves(
    ta: dict[str, Any],
    tc: list[dict[str, Any]],
    decode_fn: Optional[Callable[[str, Any], Optional[str]]] = None,
) -> dict[str, Any]:
    """Extract Speed-Force (``<fsdiagram>``) and Speed-Resistance curves for a traction unit.

    When explicit ``<srdiagvalue>`` points are absent but Davis rolling-resistance
    coefficients (``coeffA``, ``coeffB``, ``coeffC``) are present, synthesizes a
    13-point resistance curve using ``R(v) = A + B*v + C*v^2`` up to
    ``maxallowedspeed``.

    Args:
        ta: Raw XML attributes dict for the ``<tractionunit>``.
        tc: Raw XML child element list for the ``<tractionunit>``.
        decode_fn: Optional lookup decoder ``(category, code_id) -> Optional[str]``
            for resolving electric profile names.

    Returns:
        Dict with ``sr_curve``, ``fs_diagrams``, ``max_force_kn``, and
        ``max_power_kw``.
    """
    sr_curve = []
    fs_diagrams = []
    max_force_kn = 0.0
    max_power_kw = 0.0

    for ch in tc or []:
        tag = ch.get("tag")
        if tag == "srdiagvalue":
            a = ch.get("attrs", {})
            spd = float(a.get("speed") or 0)
            ret_n = float(a.get("retardation") or 0)
            sr_curve.append({
                "speed_kmh": round(spd, 2),
                "speed_mph": round(spd * 0.621371, 1),
                "resistance_n": round(ret_n, 1),
                "resistance_kn": round(ret_n / 1000.0, 3),
            })
        elif tag == "fsdiagram":
            da = ch.get("attrs", {})
            pts = []
            for fch in ch.get("children") or []:
                if fch.get("tag") == "fsdiagvalue":
                    fa = fch.get("attrs", {})
                    spd = float(fa.get("speed") or 0)
                    frc = float(fa.get("force") or 0)
                    # Power (kW) = Force (kN) * Speed (m/s) = Force * (km/h / 3.6).
                    pwr_kw = round((frc * spd) / 3.6, 1)
                    if frc > max_force_kn:
                        max_force_kn = round(frc, 2)
                    max_power_kw = max(max_power_kw, pwr_kw)
                    pts.append({
                        "speed_kmh": round(spd, 2),
                        "speed_mph": round(spd * 0.621371, 1),
                        "force_kn": round(frc, 2),
                        "power_kw": pwr_kw,
                    })
            pts.sort(key=lambda p: p["speed_kmh"])
            if pts:
                eprof = (
                    decode_fn("electricprofiledesc", da.get("electricprofilenameid"))
                    if decode_fn
                    else None
                ) or "Default"
                fs_diagrams.append({
                    "id": da.get("id"),
                    "electric_profile": eprof,
                    "points": pts,
                })

    sr_curve.sort(key=lambda p: p["speed_kmh"])
    # Synthesize from Davis formula R(v) = A + B*v + C*v^2 if explicit points are absent.
    ca = float(ta.get("coeffA") or 0)
    cb = float(ta.get("coeffB") or 0)
    cc = float(ta.get("coeffC") or 0)
    vmax = float(ta.get("maxallowedspeed") or 160)
    if not sr_curve and (ca > 0 or cb > 0 or cc > 0) and vmax > 0:
        steps = 12
        for i in range(steps + 1):
            v = (vmax * i) / steps
            r_n = ca + cb * v + cc * (v * v)
            sr_curve.append({
                "speed_kmh": round(v, 2),
                "speed_mph": round(v * 0.621371, 1),
                "resistance_n": round(r_n, 1),
                "resistance_kn": round(r_n / 1000.0, 3),
            })

    return {
        "sr_curve": sr_curve,
        "fs_diagrams": fs_diagrams,
        "max_force_kn": max_force_kn,
        "max_power_kw": max_power_kw,
    }


def resolve_node_path_details(
    cur,
    node_ids: list,
    decode_fn: Optional[Callable[[str, Any], Optional[str]]] = None,
) -> dict[str, Any]:
    """Resolve ordered schematic coordinates, edge lengths, speeds, speed profile, and turnouts.

    Walks each consecutive directed edge along ``node_ids`` to compute:
      - Schematic coordinates and metric lengths
      - Minimum (governing) and maximum permissible base speeds (``min_vmax`` / ``max_vmax``)
      - Merged step-by-step ``speed_profile`` segments (including any differential
        ``velocity_profiles`` per train class)
      - Switch/turnout junctions (valency >= 3) along the path

    Args:
        cur: An active PostgreSQL cursor returning dict rows.
        node_ids: Ordered sequence of TPS ``node_id`` integers.
        decode_fn: Optional lookup decoder ``(category, code_id) -> Optional[str]``
            for resolving differential velocity profile labels.

    Returns:
        Dict containing ``coords``, ``length_meters``, ``min_vmax``, ``max_vmax``,
        ``edge_lengths``, ``speed_profile``, ``turnout_nodes``, and ``node_map``.
    """
    clean_ids = [int(n) for n in (node_ids or []) if n is not None]
    if not clean_ids:
        return {
            "coords": [],
            "length_meters": 0,
            "min_vmax": None,
            "max_vmax": None,
            "edge_lengths": [],
            "speed_profile": [],
            "turnout_nodes": [],
            "node_map": {},
        }

    cur.execute(
        "SELECT node_id, netx, nety, km_region_id, km_value FROM nodes WHERE node_id = ANY(%s)",
        (clean_ids,),
    )
    nmap = {r["node_id"]: r for r in cur.fetchall()}

    cur.execute(
        """
        SELECT pk, edge_id, start_node_id, end_node_id, length, vmax, electrified, gradient,
               velocity_profiles
        FROM edges
        WHERE (start_node_id = ANY(%s) OR end_node_id = ANY(%s))
          AND edge_id > 0
        """,
        (clean_ids, clean_ids),
    )
    edge_lookup: dict[tuple[int, int], dict[str, Any]] = {}
    node_degree: dict[int, int] = {}
    for er in cur.fetchall():
        u = er["start_node_id"]
        v = er["end_node_id"]
        node_degree[u] = node_degree.get(u, 0) + 1
        node_degree[v] = node_degree.get(v, 0) + 1
        edge_lookup[(u, v)] = er
        if (v, u) not in edge_lookup:
            edge_lookup[(v, u)] = er

    coords = [[nmap[nid]["netx"], nmap[nid]["nety"]] for nid in clean_ids if nid in nmap]

    total_len = 0
    edge_lengths = []
    vmax_vals = []
    speed_profile: list[dict[str, Any]] = []
    for i in range(len(clean_ids) - 1):
        u, v = clean_ids[i], clean_ids[i + 1]
        er = edge_lookup.get((u, v))
        if er:
            el = int(er["length"] or 0)
            seg_start = total_len
            total_len += el
            edge_lengths.append(el)
            vmax_kmh = int(er["vmax"] or 0)
            if vmax_kmh > 0:
                vmax_vals.append(vmax_kmh)

            # Decode any differential velocity profiles (e.g. MU, HST, EPS) on this edge.
            diff_list = []
            for vp in er.get("velocity_profiles") or []:
                vp_vmax = int(vp.get("vmax") or 0)
                if vp_vmax <= 0:
                    continue
                pid = vp.get("profileid")
                pname = (
                    decode_fn("veloprofiledesc", pid)
                    if decode_fn is not None
                    else None
                ) or f"Profile {pid}"
                diff_list.append({
                    "profile_id": pid,
                    "profile_name": pname,
                    "vmax_kmh": vp_vmax,
                    "vmax_mph": round(vp_vmax * 0.621371, 1),
                })
            diff_key = tuple((d["profile_id"], d["vmax_kmh"]) for d in diff_list)

            # Merge consecutive edges with identical base + differential speeds into one step.
            if (
                speed_profile
                and speed_profile[-1]["vmax_kmh"] == vmax_kmh
                and speed_profile[-1]["_diff_key"] == diff_key
            ):
                speed_profile[-1]["end_m"] = total_len
                speed_profile[-1]["length_m"] += el
            else:
                speed_profile.append({
                    "start_m": seg_start,
                    "end_m": total_len,
                    "length_m": el,
                    "vmax_kmh": vmax_kmh,
                    "vmax_mph": round(vmax_kmh * 0.621371, 1) if vmax_kmh > 0 else 0.0,
                    "differential_speeds": diff_list,
                    "_diff_key": diff_key,
                })
        else:
            edge_lengths.append(0)

    # Strip internal merge key before returning JSON-serializable steps.
    for step in speed_profile:
        step.pop("_diff_key", None)

    # Identify switch/turnout junctions (valency >= 3) along the path.
    turnout_nodes = []
    seen_t: set[int] = set()
    for nid in clean_ids:
        deg = node_degree.get(nid, 0)
        if deg >= 3 and nid in nmap and nid not in seen_t:
            seen_t.add(nid)
            turnout_nodes.append({
                "node_id": nid,
                "x": nmap[nid]["netx"],
                "y": nmap[nid]["nety"],
                "degree": deg,
            })

    return {
        "coords": coords,
        "length_meters": total_len,
        "min_vmax": min(vmax_vals) if vmax_vals else None,
        "max_vmax": max(vmax_vals) if vmax_vals else None,
        "edge_lengths": edge_lengths,
        "speed_profile": speed_profile,
        "turnout_nodes": turnout_nodes,
        "node_map": nmap,
    }

