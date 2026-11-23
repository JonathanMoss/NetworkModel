"""Reusable diagnostic audit for TPS-to-NWR geographic track matching.

Run inside the backend container:
    docker exec nr_schematic_app python3 /app/audit_track_matching.py --mode signals --limit 1200
    docker exec nr_schematic_app python3 /app/audit_track_matching.py --mode points --limit 1000
    docker exec nr_schematic_app python3 /app/audit_track_matching.py --mode hubs --limit 30
    docker exec nr_schematic_app python3 /app/audit_track_matching.py --mode hubs --station YORK
"""

import argparse
import collections
import re
from psycopg.rows import dict_row

import main


def _check_signal_anomalies(sig, elr, km, ident, res):
    """Return a list of (anomaly_type, detail_tuple) for a matched signal."""
    anomalies = []
    best = res.get("best_centreline")
    if not best:
        return anomalies
    cands = res.get("corridor_centrelines") or []
    tid = str(best.get("track_id") or "")
    dh = ident.get("direction_hint")
    nh = (ident.get("track_name_hint") or "").lower()
    pnum = ident.get("platform_num")
    base_info = (sig["pk"], sig["name"], elr, km, dh, tid, ident.get("track_name_hint"))

    # 1. Direction mismatch (down on 1xxx or up on 2xxx)
    if (dh == "down" and tid.startswith("1")) or (dh == "up" and tid.startswith("2")):
        anomalies.append(("dir_mismatch", base_info))

    # 2. Platform number mismatch (explicit wrong NN, or landed on xx00 when exact NN exists)
    if pnum is not None and 1 <= pnum <= 99 and len(tid) == 4 and tid[2:4].isdigit():
        tid_nn = int(tid[2:4])
        if tid_nn > 0 and tid_nn != pnum:
            anomalies.append(("plat_num_mismatch", base_info + (f"want={pnum:02d}",)))
        elif tid_nn == 0:
            want_suffix = f"{pnum:02d}"
            has_exact = any(
                len(str(c.get("track_id") or "")) == 4
                and str(c["track_id"])[2:4] == want_suffix
                and str(c["track_id"])[1] in ("1", "2", "4", "5", "6", "8")
                and (
                    c.get("elr") != elr
                    or (
                        km is not None
                        and min(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0)
                        <= km
                        <= max(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0)
                    )
                )
                for c in cands
            )
            if has_exact:
                anomalies.append(("plat_num_missed_exact", base_info + (f"avail={want_suffix}",)))

    # 3. Class contradiction between TPS operational label and NWR digit 2
    if nh and len(tid) == 4:
        d2 = tid[1]
        is_exact_plat = pnum is not None and tid[2:4].isdigit() and int(tid[2:4]) == pnum
        is_fast_main = bool(re.search(r"\b(?:fast|main)\b", nh)) and not bool(
            re.search(r"\b(?:slow|relief|loop|platform|bay|terminal|siding|goods)\b", nh)
        )
        is_slow_rel = bool(re.search(r"\b(?:slow|relief)\b", nh)) and not bool(
            re.search(r"\b(?:fast|main|loop|bay|terminal|siding)\b", nh)
        )
        is_bay = bool(re.search(r"\b(?:bay|terminal)\b", nh))
        is_siding = bool(re.search(r"\b(?:siding|depot|yard)\b", nh))

        if is_fast_main and d2 in ("2", "7", "9") and not is_exact_plat:
            anomalies.append(("class_mismatch", base_info + ("fast/main->slow/xover/sdg",)))
        elif is_slow_rel and d2 in ("1", "7", "9") and not is_exact_plat:
            anomalies.append(("class_mismatch", base_info + ("slow/relief->fast/xover/sdg",)))
        elif is_bay and d2 in ("1", "2"):
            has_bay_cand = any(len(str(c.get("track_id") or "")) == 4 and str(c["track_id"])[1] == "6" for c in cands)
            if has_bay_cand:
                anomalies.append(("class_mismatch", base_info + ("bay->main/slow",)))
        elif is_siding and d2 in ("1", "2"):
            has_sdg_cand = any(len(str(c.get("track_id") or "")) == 4 and str(c["track_id"])[1] in ("8", "9") for c in cands)
            if has_sdg_cand:
                anomalies.append(("class_mismatch", base_info + ("siding->main/slow",)))

    # 4. Non-covering hijack (same-ELR best is >50m outside [min_m, max_m] when another same-ELR cand covers km)
    if best.get("elr") == elr and km is not None:
        b_min = min(best.get("start_meters") or 0.0, best.get("end_meters") or 0.0)
        b_max = max(best.get("start_meters") or 0.0, best.get("end_meters") or 0.0)
        if km < b_min - 50.0 or km > b_max + 50.0:
            has_covering = any(
                c.get("elr") == elr
                and min(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0)
                <= km
                <= max(c.get("start_meters") or 0.0, c.get("end_meters") or 0.0)
                for c in cands
            )
            if has_covering:
                anomalies.append(("non_covering_match", base_info))

    return anomalies


def _detect_parallel_collisions(matched_records):
    """Flag pairs of signals at the same (elr, km±10m) on different TPS tracks that collide onto the same track_id."""
    collisions = []
    by_elr = collections.defaultdict(list)
    for rec in matched_records:
        by_elr[rec["elr"]].append(rec)

    for elr, recs in by_elr.items():
        recs.sort(key=lambda r: r["km"])
        for i, r1 in enumerate(recs):
            for r2 in recs[i + 1 :]:
                if r2["km"] - r1["km"] > 10:
                    break
                if r1["best_tid"] != r2["best_tid"]:
                    continue
                if r1["dir_hint"] != r2["dir_hint"] or r1["dir_hint"] not in ("up", "down"):
                    continue
                if not r1["hint"] or not r2["hint"] or r1["hint"] == r2["hint"]:
                    continue
                # Only flag if there was a second same-direction open-line candidate available
                d1 = r1["best_tid"][:1]
                alt_tids = {
                    str(c.get("track_id") or "")
                    for c in r1["cands"]
                    if str(c.get("track_id") or "").startswith(d1)
                    and str(c.get("track_id") or "") != r1["best_tid"]
                    and len(str(c.get("track_id") or "")) == 4
                    and str(c.get("track_id") or "")[1] in ("1", "2", "3", "4", "5")
                }
                if alt_tids:
                    collisions.append(
                        (
                            elr,
                            r1["km"],
                            r1["best_tid"],
                            f"{r1['name']} ({r1['hint']})",
                            f"{r2['name']} ({r2['hint']})",
                            sorted(alt_tids)[:3],
                        )
                    )
    return collisions


def run_hub_audit(limit: int = 30, station_filter: str = None):
    """Audit all signals across the busiest UK multi-platform stations, including same-platform split checks."""
    with main.get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if station_filter:
                cur.execute(
                    """
                    SELECT station_id, abbrev, longname, crscode, active_track_count
                    FROM stations
                    WHERE UPPER(abbrev) LIKE %s OR UPPER(longname) LIKE %s OR UPPER(crscode) = %s
                    ORDER BY active_track_count DESC
                    LIMIT %s
                    """,
                    (f"%{station_filter.upper()}%", f"%{station_filter.upper()}%", station_filter.upper(), limit),
                )
            else:
                cur.execute(
                    """
                    SELECT station_id, abbrev, longname, crscode, active_track_count
                    FROM stations
                    WHERE crscode IS NOT NULL AND crscode <> '' AND active_track_count >= 6
                    ORDER BY active_track_count DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
            hubs = cur.fetchall()

            total_stats = collections.Counter()
            hub_summaries = []
            anomaly_samples = collections.defaultdict(list)

            for hub in hubs:
                cur.execute(
                    """
                    SELECT DISTINCT ON (s.pk)
                           s.pk, s.name, s.start_node_id, s.end_node_id, s.point_node_id, s.km_region_id,
                           n1.km_region_id AS r1, n1.km_value AS km1,
                           n2.km_region_id AS r2, n2.km_value AS km2,
                           st.longname AS plat_ln,
                           (SELECT e.vmax FROM edges e WHERE e.start_node_id = s.start_node_id AND e.end_node_id = s.end_node_id LIMIT 1) AS vmax
                    FROM station_tracks st
                    JOIN signals s ON (s.start_node_id = ANY(st.node_ids) OR s.end_node_id = ANY(st.node_ids))
                    LEFT JOIN nodes n1 ON n1.node_id = s.start_node_id
                    LEFT JOIN nodes n2 ON n2.node_id = s.end_node_id
                    WHERE st.station_id = %s
                      AND s.bumper = false
                      AND s.name IS NOT NULL AND s.name <> ''
                      AND s.name NOT ILIKE 'POINT%%'
                      AND COALESCE(n2.km_value, n1.km_value) > 0
                    ORDER BY s.pk, st.pk
                    """,
                    (hub["station_id"],),
                )
                sigs = cur.fetchall()
                hub_anomalies = collections.Counter()
                plat_groups = collections.defaultdict(list)
                matched_records = []

                for s in sigs:
                    elr = main.decode_code("kmregionmasterdesc", s["km_region_id"] or s["r2"] or s["r1"])
                    km = s["km2"] or s["km1"]
                    if not elr or not km:
                        continue
                    nids = [n for n in (s["end_node_id"], s["start_node_id"], s["point_node_id"]) if n is not None]
                    ident = main.resolve_tps_track_identity(
                        cur,
                        nids,
                        start_node_id=s["start_node_id"],
                        end_node_id=s["end_node_id"],
                        elr=elr,
                        start_km=s["km1"],
                        end_km=s["km2"],
                    )
                    res = main.find_nwr_cross_links_for_tps(
                        cur,
                        elr,
                        km,
                        direction_hint=ident["direction_hint"],
                        track_name_hint=ident["track_name_hint"],
                        item_label=s["name"],
                        asset_type="signal",
                        match_source=ident["source"],
                        platform_num=ident.get("platform_num"),
                        secondary_dir=ident.get("secondary_dir"),
                        edge_vmax=s.get("vmax"),
                    )
                    best = res.get("best_centreline")
                    if not best:
                        total_stats["no_nwr_match"] += 1
                        continue
                    total_stats["total_matched"] += 1
                    best_tid = str(best.get("track_id") or "")

                    for atype, detail in _check_signal_anomalies(s, elr, km, ident, res):
                        total_stats[atype] += 1
                        hub_anomalies[atype] += 1
                        if len(anomaly_samples[atype]) < 12:
                            anomaly_samples[atype].append((hub["crscode"] or hub["abbrev"],) + detail)

                    plat_key = str(s["plat_ln"] or "").strip().upper()
                    if plat_key:
                        plat_groups[plat_key].append((s["pk"], s["name"], best.get("elr"), best_tid, ident.get("track_name_hint")))

                    matched_records.append(
                        {
                            "pk": s["pk"],
                            "name": s["name"],
                            "elr": elr,
                            "km": km,
                            "best_tid": best_tid,
                            "dir_hint": ident.get("direction_hint"),
                            "hint": ident.get("track_name_hint"),
                            "cands": res.get("corridor_centrelines") or [],
                        }
                    )

                # 5. Same-platform split check (signals on identical physical station_track landing on different NWR track_ids)
                for plat_key, members in plat_groups.items():
                    distinct_tids = {m[3] for m in members if m[3]}
                    if len(distinct_tids) > 1:
                        total_stats["same_platform_split"] += 1
                        hub_anomalies["same_platform_split"] += 1
                        if len(anomaly_samples["same_platform_split"]) < 15:
                            sample_str = ", ".join(f"{m[1]}->{m[2]} {m[3]}" for m in members[:4])
                            anomaly_samples["same_platform_split"].append(
                                (hub["crscode"] or hub["abbrev"], f"plat={plat_key}", sorted(distinct_tids), sample_str)
                            )

                # 6. Parallel track collision check
                for col in _detect_parallel_collisions(matched_records):
                    total_stats["parallel_collision"] += 1
                    hub_anomalies["parallel_collision"] += 1
                    if len(anomaly_samples["parallel_collision"]) < 12:
                        anomaly_samples["parallel_collision"].append((hub["crscode"] or hub["abbrev"],) + col)

                hub_summaries.append(
                    (
                        sum(hub_anomalies.values()),
                        hub["crscode"] or hub["abbrev"],
                        hub["longname"],
                        len(sigs),
                        dict(hub_anomalies),
                    )
                )

    print(f"=== UK MAJOR HUB AUDIT ({len(hubs)} hubs, {total_stats['total_matched']} matched signals) ===")
    for k, v in sorted(total_stats.items()):
        print(f"  {k:32s}: {v}")

    print("\n=== HUBS RANKED BY ANOMALY COUNT ===")
    for cnt, crs, name, n_sig, breakdown in sorted(hub_summaries, key=lambda x: (-x[0], x[1]))[:25]:
        if cnt > 0 or station_filter:
            print(f"  {crs:5s} {name:28s} ({n_sig:3d} sigs) -> {cnt:2d} anomalies {breakdown}")

    for atype, samples in sorted(anomaly_samples.items()):
        print(f"\n=== SAMPLE {atype.upper()} ({len(samples)} shown) ===")
        for ex in samples:
            print(" ", ex)


def run_audit(mode: str = "signals", limit: int = 1200, station_filter: str = None):
    if mode == "hubs":
        run_hub_audit(limit=limit, station_filter=station_filter)
        return

    is_points = mode == "points"
    with main.get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            if is_points:
                cur.execute(
                    """
                    SELECT s.pk, s.name, s.start_node_id, s.end_node_id, s.point_node_id, s.km_region_id,
                           n1.km_region_id AS r1, n1.km_value AS km1,
                           n2.km_region_id AS r2, n2.km_value AS km2,
                           (SELECT e.vmax FROM edges e WHERE e.start_node_id = s.start_node_id AND e.end_node_id = s.end_node_id LIMIT 1) AS vmax
                    FROM signals s
                    LEFT JOIN nodes n1 ON n1.node_id = s.start_node_id
                    LEFT JOIN nodes n2 ON n2.node_id = s.end_node_id
                    WHERE (s.interlocking_sys_id IN (15, 16) OR s.name ILIKE 'POINT%%')
                      AND COALESCE(n2.km_value, n1.km_value) > 0
                    ORDER BY md5(s.pk::text)
                    LIMIT %s
                    """,
                    (limit,),
                )
            else:
                cur.execute(
                    """
                    SELECT s.pk, s.name, s.start_node_id, s.end_node_id, s.point_node_id, s.km_region_id,
                           n1.km_region_id AS r1, n1.km_value AS km1,
                           n2.km_region_id AS r2, n2.km_value AS km2,
                           (SELECT e.vmax FROM edges e WHERE e.start_node_id = s.start_node_id AND e.end_node_id = s.end_node_id LIMIT 1) AS vmax
                    FROM signals s
                    LEFT JOIN nodes n1 ON n1.node_id = s.start_node_id
                    LEFT JOIN nodes n2 ON n2.node_id = s.end_node_id
                    WHERE s.bumper = false
                      AND s.name IS NOT NULL AND s.name <> ''
                      AND s.name NOT ILIKE 'POINT%%'
                      AND n2.km_value > 0
                    ORDER BY md5(s.pk::text)
                    LIMIT %s
                    """,
                    (limit,),
                )
            rows = cur.fetchall()

            stats = collections.Counter()
            tie_pairs = collections.Counter()
            tie_examples = collections.defaultdict(list)
            anomaly_samples = collections.defaultdict(list)

            for s in rows:
                elr = main.decode_code("kmregionmasterdesc", s["km_region_id"] or s["r2"] or s["r1"])
                km = s["km2"] or s["km1"]
                if not elr or not km:
                    stats["no_elr_or_km"] += 1
                    continue

                nids = [n for n in (s["end_node_id"], s["start_node_id"], s["point_node_id"]) if n is not None]
                ident = main.resolve_tps_track_identity(
                    cur,
                    nids,
                    start_node_id=s["start_node_id"],
                    end_node_id=s["end_node_id"],
                    elr=elr,
                    start_km=s["km1"],
                    end_km=s["km2"],
                )
                res = main.find_nwr_cross_links_for_tps(
                    cur,
                    elr,
                    km,
                    direction_hint=ident["direction_hint"],
                    track_name_hint=ident["track_name_hint"],
                    tps_valency=3 if is_points else None,
                    item_label=s["name"],
                    asset_type="points" if is_points else "signal",
                    match_source=ident["source"],
                    platform_num=ident.get("platform_num"),
                    secondary_dir=ident.get("secondary_dir"),
                    edge_vmax=s.get("vmax"),
                )
                cands = res.get("corridor_centrelines") or []
                best = res.get("best_centreline")
                snap = res.get("snapped_node")
                if not best:
                    stats["no_nwr_match"] += 1
                    continue

                stats["total_matched"] += 1
                src = (ident["source"] or "None").split("(")[0].strip()
                if src.startswith("ELR "):
                    stats["src:ELR Polarity"] += 1
                else:
                    stats[f"src:{src}"] += 1

                if not ident["direction_hint"]:
                    stats["no_direction"] += 1

                if is_points:
                    if not snap:
                        stats["snap:none_within_90m"] += 1
                    elif snap.get("is_switch") and snap.get("on_matched_track"):
                        stats["snap:switch_on_matched_track"] += 1
                    elif snap.get("is_switch"):
                        stats["snap:switch_off_matched_track"] += 1
                    elif snap.get("on_matched_track"):
                        stats["snap:non_switch_on_track"] += 1
                    else:
                        stats["snap:non_switch_off_track"] += 1
                else:
                    for atype, detail in _check_signal_anomalies(s, elr, km, ident, res):
                        stats[atype] += 1
                        if len(anomaly_samples[atype]) < 8:
                            anomaly_samples[atype].append(detail)

                top_score = best.get("_score")
                diff_tied = [c for c in cands[1:] if c.get("_score") == top_score and c["track_id"] != best["track_id"]]
                if diff_tied:
                    stats["top_tie_diff_track"] += 1
                    pair = tuple(sorted([best["track_id"], diff_tied[0]["track_id"]]))
                    tie_pairs[pair] += 1
                    if len(tie_examples[pair]) < 2:
                        tie_examples[pair].append((s["pk"], s["name"], elr, ident["direction_hint"], ident["track_name_hint"]))

    print(f"=== {mode.upper()} AUDIT SUMMARY ({limit} stratified UK {mode}) ===")
    for k, v in sorted(stats.items()):
        print(f"  {k:32s}: {v}")
    print(f"\n=== TOP TIED TRACK_ID PAIRS ({mode.upper()}) ===")
    for pair, cnt in tie_pairs.most_common(10):
        print(f"  {pair} : {cnt}x | e.g. {tie_examples[pair]}")
    for atype, samples in sorted(anomaly_samples.items()):
        print(f"\n=== SAMPLE {atype.upper()} ===")
        for ex in samples:
            print(" ", ex)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit TPS-to-NWR track matching across UK signals, points, or major hubs.")
    parser.add_argument("--mode", choices=["signals", "points", "hubs"], default="signals", help="Asset class or hub sweep to audit")
    parser.add_argument("--limit", type=int, default=1200, help="Number of stratified assets or hubs to audit")
    parser.add_argument("--station", type=str, default=None, help="Optional station name/CRS filter for --mode hubs")
    args = parser.parse_args()
    run_audit(args.mode, args.limit, station_filter=args.station)
