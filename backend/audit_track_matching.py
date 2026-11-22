"""Reusable diagnostic audit for TPS-to-NWR geographic track matching.

Run inside the backend container:
    docker exec nr_schematic_app python3 /app/audit_track_matching.py --mode signals --limit 1200
    docker exec nr_schematic_app python3 /app/audit_track_matching.py --mode points --limit 1000
"""

import argparse
import collections
from psycopg.rows import dict_row

import main


def run_audit(mode: str = "signals", limit: int = 1200):
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
            dir_mismatch_examples = []

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
                    tid = str(best["track_id"] or "")
                    if (ident["direction_hint"] == "down" and tid.startswith("1")) or (
                        ident["direction_hint"] == "up" and tid.startswith("2")
                    ):
                        stats["dir_mismatch"] += 1
                        if len(dir_mismatch_examples) < 8:
                            dir_mismatch_examples.append((s["pk"], s["name"], elr, ident["direction_hint"], tid, ident["track_name_hint"]))

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
    if dir_mismatch_examples:
        print("\n=== SAMPLE DIRECTION MISMATCHES ===")
        for ex in dir_mismatch_examples:
            print(" ", ex)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Audit TPS-to-NWR track matching across UK signals or points.")
    parser.add_argument("--mode", choices=["signals", "points"], default="signals", help="Asset class to audit")
    parser.add_argument("--limit", type=int, default=1200, help="Number of stratified assets to audit")
    args = parser.parse_args()
    run_audit(args.mode, args.limit)
