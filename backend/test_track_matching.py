"""Automated regression test suite for TPS-to-NWR geographic track matching.

Run inside the backend container:
    docker exec nr_schematic_app python3 /app/test_track_matching.py
"""

import sys
from psycopg.rows import dict_row

import main


# (label, asset_type, pk, expected_elr, expected_track_id)
REGRESSION_CASES = [
    # Stoke / CMD1 / CMD2 (reverse-chained ELR)
    ("SOT313 (CMD1 Down Main)", "signal", 7277, "CMD1", "2100"),
    ("SOT312 (CMD1 Up Main)", "signal", 7276, "CMD1", "1100"),
    ("SOT303 (CMD1 Open Down)", "signal", 7727, "CMD1", "2100"),
    ("SOT302 (CMD2 Open Up)", "signal", 7696, "CMD2", "1100"),
    ("SOT311 (CMD1 Open Down)", "signal", 7278, "CMD1", "2100"),
    ("POINTS 695A (CMD1 Up Fast)", "points", 54440, "CMD1", "1100"),
    # Crewe (LEC5)
    ("CE116 (Crewe Plat 11 Up)", "signal", 7233, "LEC5", "3211"),
    ("CE139 (Crewe Plat 11 Dn)", "signal", 7234, "LEC5", "3211"),
    ("CE135 (Crewe Plat 12)", "signal", 7263, "LEC5", "3212"),
    ("CE122 (Crewe Plat 6)", "signal", 7220, "LEC5", "2200"),
    ("CE128 (Crewe Plat 5)", "signal", 7228, "LEC5", "1200"),
    ("CE137 (Crewe U&D Loop)", "signal", 7232, "LEC5", "3500"),
    ("CE144 (Crewe Up Fast)", "signal", 7259, "LEC5", "1100"),
    # Sandbach / Wilmslow (CMP1)
    ("MS3713 (CMP1 Down Indep)", "signal", 57334, "CMP1", "2200"),
    ("MS4038 (CMP1 Up Wilmslow)", "signal", 7643, "CMP1", "1100"),
    # Stafford (LEC3 / LEC4)
    ("SC9355 (Stafford Plat 1)", "signal", 47152, "LEC3", "1500"),
    ("LS9346 (Stafford Plat 3)", "signal", 47150, "LEC3", "2500"),
    ("LS5588 (Stafford Plat 4)", "signal", 47175, "LEC3", "1200"),
    ("SC9349 (Stafford Plat 4 Dn)", "signal", 47151, "LEC3", "1200"),
    ("SC5587 (Stafford Plat 5)", "signal", 47149, "LEC3", "2200"),
    ("SC9345 (Stafford Plat 6)", "signal", 47178, "LEC3", "3200"),
    ("SC3604 (Stafford Up Fast)", "signal", 12557, "LEC4", "1100"),
    # Paddington / Portobello / Old Oak Common / Didcot (MLN1)
    ("SN6003 (Paddington Engine)", "signal", 50483, "MLN1", "3800"),
    ("SN104 (MLN1 Line 2)", "signal", 28416, "MLN1", "3102"),
    ("SB931 (Didcot Down Main)", "signal", 17874, "MLN1", "2100"),
    # Euston (LEC1)
    ("WM101 (Euston D-MBR)", "signal", 59530, "LEC1", "3902"),
    ("POINTS 2001B (Euston MBR)", "points", 57088, "LEC1", "3901"),
    # Liverpool Street (LTN1 + LVS multi-ELR)
    ("LIVST Plat 1", "signal", 28727, "LTN1", "3601"),
    ("LIVST Plat 10", "signal", 4338, "LTN1", "3610"),
    ("LIVST Plat 11 (LVS)", "signal", 4346, "LVS", "3611"),
    ("LIVST Plat 12 (LVS)", "signal", 4347, "LVS", "3612"),
    ("LIVST Plat 17 (LVS)", "signal", 28711, "LVS", "3617"),
    # York (ECM4 / ECM5 / YMS multi-ELR)
    ("YORK Plat 1", "signal", 3234, "ECM4", "3601"),
    ("YORK Plat 2 (YMS)", "signal", 3236, "YMS", "3602"),
    ("YORK Plat 8", "signal", 3235, "ECM5", "3608"),
    ("YORK Plat 11", "signal", 3251, "ECM4", "3811"),
    # Birmingham New Street (RBS1 / RBS2)
    ("BHAMNWS Plat 1", "signal", 37602, "RBS2", "3801"),
    ("BHAMNWS Plat 2", "signal", 37604, "RBS2", "3802"),
    ("BHAMNWS Plat 3", "signal", 16107, "RBS2", "3803"),
    ("BHAMNWS Plat 12", "signal", 12434, "RBS2", "3812"),
    # Reading (MLN1)
    ("RDG T1682 (Plat 7 DML)", "signal", 44388, "MLN1", "2100"),
    ("RDG T1684 (Plat 8)", "signal", 43994, "MLN1", "2508"),
    ("RDG T1692 (Plat 12 DRL)", "signal", 43987, "MLN1", "2200"),
    ("RDG T1695 (Plat 13)", "signal", 43986, "MLN1", "2513"),
    ("RDG T1697 (Plat 14)", "signal", 43980, "MLN1", "1514"),
    ("RDG T1698 (Plat 15 URL)", "signal", 43973, "MLN1", "1200"),
]


def run_tests() -> int:
    passed = 0
    failed = 0
    with main.get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            for label, atype, pk, exp_elr, exp_tid in REGRESSION_CASES:
                cur.execute(
                    """
                    SELECT s.pk, s.name, s.start_node_id, s.end_node_id, s.point_node_id, s.km_region_id,
                           n1.km_region_id AS r1, n1.km_value AS km1,
                           n2.km_region_id AS r2, n2.km_value AS km2,
                           (SELECT e.vmax FROM edges e WHERE e.start_node_id = s.start_node_id AND e.end_node_id = s.end_node_id LIMIT 1) AS vmax
                    FROM signals s
                    LEFT JOIN nodes n1 ON n1.node_id = s.start_node_id
                    LEFT JOIN nodes n2 ON n2.node_id = s.end_node_id
                    WHERE s.pk = %s
                    """,
                    (pk,),
                )
                s = cur.fetchone()
                if not s:
                    print(f"FAIL  {label:30s}: pk={pk} not found")
                    failed += 1
                    continue
                elr = main.decode_code("kmregionmasterdesc", s["km_region_id"] or s["r2"] or s["r1"])
                km = s["km2"] or s["km1"]
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
                    tps_valency=3 if atype == "points" else None,
                    item_label=s["name"],
                    asset_type=atype,
                    match_source=ident["source"],
                    platform_num=ident.get("platform_num"),
                    secondary_dir=ident.get("secondary_dir"),
                    edge_vmax=s.get("vmax"),
                )
                b = res.get("best_centreline") or {}
                got_elr, got_tid = b.get("elr"), b.get("track_id")
                if got_elr == exp_elr and got_tid == exp_tid:
                    passed += 1
                else:
                    failed += 1
                    print(
                        f"FAIL  {label:30s}: expected {exp_elr} {exp_tid}, got {got_elr} {got_tid} "
                        f"(sc={b.get('_score')}) | ident={ident}"
                    )

    print(f"\nREGRESSION SUITE: {passed}/{passed + failed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())

