"""Batched full-network TPS-to-NWR track matching audit & snapshot diff tool.

Run inside the backend container (snapshots persist on host via ./audit_snapshots):

  # 1. Full UK batched baseline snapshot (all ~55k signals + ~14k points):
  docker exec nr_schematic_app python3 /app/batch_audit.py \
      --out /app/audit_snapshots/baseline.jsonl.gz --workers 6 --batch-size 1000

  # 2. Fast targeted snapshot for specific ELRs:
  docker exec nr_schematic_app python3 /app/batch_audit.py \
      --elr WCM2,CGJ3,ECM4 --out /app/audit_snapshots/corridor.jsonl.gz

  # 3. Re-summarize any saved snapshot (optionally export anomalies to CSV):
  docker exec nr_schematic_app python3 /app/batch_audit.py \
      --summarize /app/audit_snapshots/baseline.jsonl.gz \
      --csv /app/audit_snapshots/baseline_anomalies.csv

  # 4. Before/After diff report to validate code changes & catch regressions:
  docker exec nr_schematic_app python3 /app/batch_audit.py \
      --diff /app/audit_snapshots/baseline.jsonl.gz /app/audit_snapshots/after.jsonl.gz \
      --csv /app/audit_snapshots/diff.csv
"""

import argparse
import collections
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import gzip
import json
import math
import os
import sys
import time
from psycopg.rows import dict_row

import audit_track_matching as atm
import main


def _open_read(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def _open_write(path: str, append: bool = False):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    mode = "at" if append else "wt"
    if path.endswith(".gz"):
        return gzip.open(path, mode, encoding="utf-8")
    return open(path, mode, encoding="utf-8")


def _worker_process_batch(pks: list[int]) -> list[dict]:
    """Process one batch of signal/point PKs in an isolated worker process."""
    records: list[dict] = []
    if not pks:
        return records

    with main.get_conn() as conn:
        conn.autocommit = True
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT s.pk, s.name, s.start_node_id, s.end_node_id, s.point_node_id, s.km_region_id,
                       n1.km_region_id AS r1, n1.km_value AS km1,
                       n2.km_region_id AS r2, n2.km_value AS km2,
                       (SELECT e.vmax
                        FROM edges e
                        WHERE e.start_node_id = s.start_node_id AND e.end_node_id = s.end_node_id
                        LIMIT 1) AS vmax
                FROM signals s
                LEFT JOIN nodes n1 ON n1.node_id = s.start_node_id
                LEFT JOIN nodes n2 ON n2.node_id = s.end_node_id
                WHERE s.pk = ANY(%s)
                ORDER BY s.pk
                """,
                (pks,),
            )
            rows = cur.fetchall()

            for s in rows:
                name = s.get("name") or ""
                is_point = name.upper().startswith("POINT")
                atype = "points" if is_point else "signal"
                elr = main.decode_code("kmregionmasterdesc", s["km_region_id"] or s["r2"] or s["r1"])
                km = s["km2"] if s["km2"] is not None else s["km1"]

                if not elr or km is None or km <= 0:
                    records.append(
                        {
                            "pk": s["pk"],
                            "atype": atype,
                            "name": name,
                            "elr": elr or "",
                            "km": km,
                            "dir": None,
                            "pnum": None,
                            "hint": None,
                            "m_elr": None,
                            "m_tid": None,
                            "m_fid": None,
                            "score": None,
                            "snap_m": None,
                            "alt_same_dir": False,
                            "anomalies": ["no_tps_elr_or_km"],
                        }
                    )
                    continue

                nids = [n for n in (s["end_node_id"], s["start_node_id"], s["point_node_id"]) if n is not None]
                try:
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
                        tps_valency=3 if is_point else None,
                        item_label=name,
                        asset_type=atype,
                        match_source=ident["source"],
                        platform_num=ident.get("platform_num"),
                        secondary_dir=ident.get("secondary_dir"),
                        edge_vmax=s.get("vmax"),
                        tps_node_ids=nids,
                    )
                except Exception as exc:
                    records.append(
                        {
                            "pk": s["pk"],
                            "atype": atype,
                            "name": name,
                            "elr": elr,
                            "km": round(float(km), 1),
                            "dir": None,
                            "pnum": None,
                            "hint": None,
                            "m_elr": None,
                            "m_tid": None,
                            "m_fid": None,
                            "score": None,
                            "snap_m": None,
                            "alt_same_dir": False,
                            "anomalies": [f"error:{type(exc).__name__}"],
                        }
                    )
                    continue
                best = res.get("best_centreline")
                cands = res.get("corridor_centrelines") or []

                anomalies: list[str] = []
                snap_dist = None
                alt_same_dir = False

                if not best:
                    anomalies.append("unmatched")
                    m_elr = None
                    m_tid = None
                    m_fid = None
                    score = None
                else:
                    m_elr = best.get("elr")
                    m_tid = str(best.get("track_id") or "")
                    m_fid = best.get("fid")
                    score = round(float(best["_score"]), 1) if best.get("_score") is not None else None

                    if is_point:
                        dh = ident.get("direction_hint")
                        if (dh == "down" and m_tid.startswith("1")) or (dh == "up" and m_tid.startswith("2")):
                            anomalies.append("dir_mismatch")
                        snapped = res.get("snapped_node")
                        geo_snap = res.get("geo_snap")
                        if not snapped or (snapped.get("valency") or 0) < 3:
                            anomalies.append("not_snapped_to_switch")
                        if snapped and geo_snap and geo_snap.get("x") is not None and snapped.get("x") is not None:
                            snap_dist = round(
                                math.hypot(
                                    float(snapped["x"]) - float(geo_snap["x"]),
                                    float(snapped["y"]) - float(geo_snap["y"]),
                                ),
                                1,
                            )
                            if snap_dist > 25.0:
                                anomalies.append("far_snap_gt_25m")
                    else:
                        sig_anoms = atm._check_signal_anomalies(s, elr, km, ident, res)  # pylint: disable=protected-access
                        anomalies.extend(sorted({a_type for a_type, _ in sig_anoms}))
                        d1 = m_tid[:1]
                        if d1 in ("1", "2"):
                            alt_same_dir = any(
                                str(c.get("track_id") or "").startswith(d1)
                                and str(c.get("track_id") or "") != m_tid
                                and len(str(c.get("track_id") or "")) == 4
                                and str(c.get("track_id") or "")[1] in ("1", "2", "3", "4", "5")
                                for c in cands
                            )

                records.append(
                    {
                        "pk": s["pk"],
                        "atype": atype,
                        "name": name,
                        "elr": elr,
                        "km": round(float(km), 1),
                        "dir": ident.get("direction_hint"),
                        "pnum": ident.get("platform_num"),
                        "hint": ident.get("track_name_hint"),
                        "m_elr": m_elr,
                        "m_tid": m_tid,
                        "m_fid": m_fid,
                        "score": score,
                        "snap_m": snap_dist,
                        "alt_same_dir": alt_same_dir,
                        "anomalies": anomalies,
                    }
                )
    return records


def _annotate_parallel_collisions(records: list[dict]) -> None:
    """Post-pass across matched signals to flag same-(elr, km+-10m) parallel collisions."""
    by_elr = collections.defaultdict(list)
    for rec in records:
        if rec.get("atype") == "signal" and rec.get("m_tid") and rec.get("km") is not None:
            by_elr[rec["elr"]].append(rec)

    for recs in by_elr.values():
        recs.sort(key=lambda r: r["km"])
        for i, r1 in enumerate(recs):
            for r2 in recs[i + 1 :]:
                if r2["km"] - r1["km"] > 10.0:
                    break
                if r1["m_elr"] != r2["m_elr"] or r1["m_tid"] != r2["m_tid"]:
                    continue
                if r1["dir"] != r2["dir"] or r1["dir"] not in ("up", "down"):
                    continue
                if not r1["hint"] or not r2["hint"] or r1["hint"] == r2["hint"]:
                    continue
                if r1.get("alt_same_dir") or r2.get("alt_same_dir"):
                    if "parallel_collision" not in r1["anomalies"]:
                        r1["anomalies"].append("parallel_collision")
                    if "parallel_collision" not in r2["anomalies"]:
                        r2["anomalies"].append("parallel_collision")


def _load_snapshot(path: str) -> list[dict]:
    records: list[dict] = []
    with _open_read(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _print_summary(records: list[dict], elr_filter: set[str] | None = None, csv_out: str | None = None) -> None:
    if elr_filter:
        records = [r for r in records if (r.get("elr") or "").upper() in elr_filter]

    sig_recs = [r for r in records if r.get("atype") == "signal"]
    pt_recs = [r for r in records if r.get("atype") == "points"]

    anom_counts = collections.Counter()
    elr_anoms = collections.Counter()
    elr_totals = collections.Counter()
    samples = collections.defaultdict(list)
    clean_sigs = 0
    clean_pts = 0

    for r in records:
        elr = r.get("elr") or "(none)"
        elr_totals[elr] += 1
        anoms = r.get("anomalies") or []
        if not anoms:
            if r.get("atype") == "signal":
                clean_sigs += 1
            else:
                clean_pts += 1
        else:
            elr_anoms[elr] += 1
            for a in anoms:
                anom_counts[f"{r['atype']}:{a}"] += 1
                if len(samples[f"{r['atype']}:{a}"]) < 6:
                    samples[f"{r['atype']}:{a}"].append(r)

    print(f"\n=== FULL BATCH AUDIT SUMMARY ({len(records):,} assets) ===")
    if sig_recs:
        print(f"  Signals : {len(sig_recs):6,} total | {clean_sigs:6,} clean ({100.0 * clean_sigs / len(sig_recs):5.1f}%) | {len(sig_recs) - clean_sigs:5,} with anomalies")
    if pt_recs:
        print(f"  Points  : {len(pt_recs):6,} total | {clean_pts:6,} clean ({100.0 * clean_pts / len(pt_recs):5.1f}%) | {len(pt_recs) - clean_pts:5,} with anomalies")

    print("\n=== ANOMALY BREAKDOWN ===")
    for k, v in anom_counts.most_common():
        print(f"  {k:34s} : {v:6,}")

    print("\n=== TOP 25 ELRs BY ANOMALY COUNT ===")
    for elr, cnt in elr_anoms.most_common(25):
        tot = elr_totals[elr]
        print(f"  {elr:8s} : {cnt:5,} / {tot:5,} ({100.0 * cnt / max(1, tot):5.1f}%)")

    for k, items in sorted(samples.items()):
        print(f"\n=== SAMPLES: {k} ===")
        for r in items:
            m_str = f"{r['m_elr']} {r['m_tid']}" if r.get("m_tid") else "NONE"
            print(f"  pk={r['pk']:6d} {r['elr']:5s} km={str(r['km']):>8s} {r['name']:16s} dir={str(r['dir']):10s} -> {m_str:10s} | hint={r['hint']}")

    if csv_out:
        parent = os.path.dirname(os.path.abspath(csv_out))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(csv_out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["pk", "atype", "elr", "km", "name", "dir", "pnum", "matched_elr", "matched_track_id", "score", "snap_m", "anomalies", "hint"])
            for r in records:
                if r.get("anomalies"):
                    writer.writerow([
                        r["pk"], r["atype"], r["elr"], r["km"], r["name"], r["dir"], r["pnum"],
                        r["m_elr"], r["m_tid"], r["score"], r["snap_m"], ";".join(r["anomalies"]), r["hint"],
                    ])
        print(f"\nExported anomalies CSV to {csv_out}")


def _run_diff(before_path: str, after_path: str, top_n: int = 40, csv_out: str | None = None) -> int:
    before_map = {r["pk"]: r for r in _load_snapshot(before_path)}
    after_map = {r["pk"]: r for r in _load_snapshot(after_path)}
    common_pks = sorted(set(before_map) & set(after_map))

    fixed = []
    regressed = []
    moved = []
    elr_changes = collections.Counter()
    transitions = collections.Counter()
    before_anom_totals = collections.Counter()
    after_anom_totals = collections.Counter()

    for pk in common_pks:
        b = before_map[pk]
        a = after_map[pk]
        b_anoms = set(b.get("anomalies") or [])
        a_anoms = set(a.get("anomalies") or [])
        for x in b_anoms:
            before_anom_totals[f"{b['atype']}:{x}"] += 1
        for x in a_anoms:
            after_anom_totals[f"{a['atype']}:{x}"] += 1

        b_key = (b.get("m_elr"), b.get("m_tid"))
        a_key = (a.get("m_elr"), a.get("m_tid"))
        if b_key == a_key and b_anoms == a_anoms:
            continue

        elr_changes[a.get("elr") or b.get("elr") or "(none)"] += 1
        if b_key != a_key:
            b_lbl = f"{b_key[0]} {b_key[1]}" if b_key[1] else "NONE"
            a_lbl = f"{a_key[0]} {a_key[1]}" if a_key[1] else "NONE"
            transitions[f"{b_lbl} -> {a_lbl}"] += 1

        entry = (b, a)
        if len(a_anoms) > len(b_anoms) or (b_key[1] and not a_key[1]):
            regressed.append(entry)
        elif len(a_anoms) < len(b_anoms) or (not b_key[1] and a_key[1]):
            fixed.append(entry)
        else:
            moved.append(entry)

    total_changed = len(fixed) + len(regressed) + len(moved)
    print(f"\n=== SNAPSHOT DIFF REPORT ({len(common_pks):,} assets compared) ===")
    print(f"  Before : {before_path}")
    print(f"  After  : {after_path}")
    print(f"  Unchanged : {len(common_pks) - total_changed:6,}")
    print(f"  Changed   : {total_changed:6,} ({100.0 * total_changed / max(1, len(common_pks)):.2f}%)")
    print(f"    FIXED     (fewer anomalies) : {len(fixed):5,}")
    print(f"    REGRESSED (more anomalies)  : {len(regressed):5,}")
    print(f"    MOVED     (same anomaly ct) : {len(moved):5,}")

    print("\n=== NET ANOMALY DELTA ===")
    all_keys = sorted(set(before_anom_totals) | set(after_anom_totals))
    for k in all_keys:
        bv = before_anom_totals[k]
        av = after_anom_totals[k]
        if bv != av:
            sign = "+" if av > bv else ""
            print(f"  {k:34s} : {bv:6,} -> {av:6,} ({sign}{av - bv:,})")

    if elr_changes:
        print("\n=== TOP ELRs BY CHANGED ASSETS ===")
        for elr, cnt in elr_changes.most_common(20):
            print(f"  {elr:8s} : {cnt:5,} changed")

    if transitions:
        print("\n=== TOP TRACK TRANSITIONS ===")
        for tr, cnt in transitions.most_common(20):
            print(f"  {tr:28s} : {cnt:5,}")

    def _print_group(title: str, items: list, limit: int):
        if not items:
            return
        print(f"\n=== {title} (showing {min(len(items), limit)} of {len(items)}) ===")
        for b, a in items[:limit]:
            b_m = f"{b['m_elr']} {b['m_tid']}" if b.get("m_tid") else "NONE"
            a_m = f"{a['m_elr']} {a['m_tid']}" if a.get("m_tid") else "NONE"
            print(
                f"  pk={a['pk']:6d} {a['elr']:5s} km={str(a['km']):>8s} {a['name']:15s} "
                f"{b_m:10s} -> {a_m:10s} | anoms: {b['anomalies']} -> {a['anomalies']} | hint={a['hint']}"
            )

    _print_group("POTENTIAL REGRESSIONS", regressed, top_n)
    _print_group("FIXED ANOMALIES", fixed, top_n)
    _print_group("NEUTRAL TRACK MOVES", moved, top_n)

    if csv_out:
        parent = os.path.dirname(os.path.abspath(csv_out))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(csv_out, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([
                "category", "pk", "atype", "elr", "km", "name",
                "before_track", "after_track", "before_anoms", "after_anoms", "hint",
            ])
            for cat, grp in (("REGRESSED", regressed), ("FIXED", fixed), ("MOVED", moved)):
                for b, a in grp:
                    b_m = f"{b['m_elr']} {b['m_tid']}" if b.get("m_tid") else "NONE"
                    a_m = f"{a['m_elr']} {a['m_tid']}" if a.get("m_tid") else "NONE"
                    writer.writerow([
                        cat, a["pk"], a["atype"], a["elr"], a["km"], a["name"],
                        b_m, a_m, ";".join(b["anomalies"]), ";".join(a["anomalies"]), a["hint"],
                    ])
        print(f"\nExported diff CSV to {csv_out}")

    return 1 if regressed else 0


def _run_snapshot(args) -> int:
    elr_filter = {e.strip().upper() for e in args.elr.split(",") if e.strip()} if args.elr else None

    with main.get_conn() as conn:
        conn.autocommit = True
        with conn.cursor(row_factory=dict_row) as cur:
            where_clauses = []
            params: list = []
            if args.asset_type == "signals":
                where_clauses.append("s.bumper = false AND s.name IS NOT NULL AND s.name <> '' AND s.name NOT ILIKE 'POINT%%'")
            elif args.asset_type == "points":
                where_clauses.append("s.name ILIKE 'POINT%%'")
            else:
                where_clauses.append("((s.bumper = false AND s.name IS NOT NULL AND s.name <> '') OR s.name ILIKE 'POINT%%')")

            if elr_filter:
                cur.execute(
                    """
                    SELECT code_id
                    FROM lookup_codes
                    WHERE category = 'kmregionmasterdesc'
                      AND (UPPER(label) = ANY(%s) OR UPPER(vanilla_text) = ANY(%s) OR UPPER(description) = ANY(%s))
                    """,
                    (list(elr_filter), list(elr_filter), list(elr_filter)),
                )
                rids = [int(r["code_id"]) for r in cur.fetchall()]
                if not rids:
                    print(f"No matching ELRs found for: {sorted(elr_filter)}")
                    return 1
                where_clauses.append(
                    "COALESCE(NULLIF(s.km_region_id, 0), NULLIF(n2.km_region_id, 0), NULLIF(n1.km_region_id, 0)) = ANY(%s)"
                )
                params.append(rids)

            sql = f"""
                SELECT s.pk
                FROM signals s
                LEFT JOIN nodes n1 ON n1.node_id = s.start_node_id
                LEFT JOIN nodes n2 ON n2.node_id = s.end_node_id
                WHERE {' AND '.join(where_clauses)}
                ORDER BY s.pk
            """
            cur.execute(sql, tuple(params))
            all_pks = [r["pk"] for r in cur.fetchall()]

    existing_records: list[dict] = []
    done_pks: set[int] = set()
    if args.resume and args.out and os.path.exists(args.out):
        existing_records = _load_snapshot(args.out)
        done_pks = {r["pk"] for r in existing_records}
        print(f"Resuming {args.out}: {len(done_pks):,} already completed.")

    pending_pks = [pk for pk in all_pks if pk not in done_pks]
    if args.limit and args.limit > 0:
        pending_pks = pending_pks[: args.limit]

    batches = [pending_pks[i : i + args.batch_size] for i in range(0, len(pending_pks), args.batch_size)]
    print(
        f"Starting batched audit: {len(pending_pks):,} assets in {len(batches)} batches "
        f"(batch_size={args.batch_size}, workers={args.workers})"
    )

    t0 = time.time()
    new_records: list[dict] = []
    completed_assets = 0

    if batches:
        with ProcessPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(_worker_process_batch, b): idx for idx, b in enumerate(batches, 1)}
            for done_count, fut in enumerate(as_completed(futures), 1):
                batch_recs = fut.result()
                new_records.extend(batch_recs)
                completed_assets += len(batch_recs)
                elapsed = max(0.001, time.time() - t0)
                rate = completed_assets / elapsed
                eta = (len(pending_pks) - completed_assets) / max(1.0, rate)
                print(
                    f"  [{done_count:3d}/{len(batches):3d}] {completed_assets:6,}/{len(pending_pks):6,} "
                    f"({100.0 * completed_assets / max(1, len(pending_pks)):5.1f}%) | {rate:6.0f}/s | ETA {eta:5.1f}s",
                    flush=True,
                )

    all_records = existing_records + new_records
    all_records.sort(key=lambda r: r["pk"])
    _annotate_parallel_collisions(all_records)

    if args.out:
        with _open_write(args.out, append=False) as fh:
            for rec in all_records:
                fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        size_mb = os.path.getsize(args.out) / (1024 * 1024)
        print(f"Saved snapshot: {args.out} ({len(all_records):,} records, {size_mb:.2f} MB)")

    _print_summary(all_records, csv_out=args.csv)
    return 0


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Batched full-network TPS-to-NWR audit & snapshot diff tool")
    parser.add_argument("--out", help="Output snapshot path (.jsonl or .jsonl.gz)")
    parser.add_argument("--asset-type", choices=("all", "signals", "points"), default="all")
    parser.add_argument("--elr", help="Comma-separated ELR filter (e.g. WCM2,CGJ3,ECM4)")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0, help="Optional cap on number of assets")
    parser.add_argument("--resume", action="store_true", help="Resume an existing partial snapshot at --out")
    parser.add_argument("--summarize", help="Summarize an existing snapshot file without querying DB")
    parser.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"), help="Diff two snapshot files")
    parser.add_argument("--top", type=int, default=40, help="Max sample rows per diff group")
    parser.add_argument("--csv", help="Optional CSV export path for anomalies or diff rows")
    args = parser.parse_args()

    if args.diff:
        return _run_diff(args.diff[0], args.diff[1], top_n=args.top, csv_out=args.csv)
    if args.summarize:
        elr_filter = {e.strip().upper() for e in args.elr.split(",") if e.strip()} if args.elr else None
        _print_summary(_load_snapshot(args.summarize), elr_filter=elr_filter, csv_out=args.csv)
        return 0
    return _run_snapshot(args)


if __name__ == "__main__":
    sys.exit(main_cli())

