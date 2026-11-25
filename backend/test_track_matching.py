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
    ("LS4310 (Stafford Plat 2 Bay)", "signal", 47173, "LEC3", "1600"),
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
    ("WM107 (Euston Line E UF)", "signal", 17709, "LEC1", "1104"),
    ("WM488 (Euston Line A UF)", "signal", 14046, "LEC1", "1101"),
    ("WM501 (Euston Line A UF)", "signal", 43068, "LEC1", "1101"),
    ("POINTS 2001B (Euston MBR)", "points", 57088, "LEC1", "3901"),
    # Norton Bridge Flyover (LEC4 -> LEC6 multi-ELR Down Slow & Up/Down Norton Bridge flyover)
    ("SC5609 (Norton Brg DS LEC6)", "signal", 48110, "LEC6", "2200"),
    ("SC5611 (Norton Brg DS LEC6)", "signal", 48112, "LEC6", "2200"),
    ("NS4328 (Norton Brg Up LEC6)", "signal", 48111, "LEC6", "1101"),
    ("NS4330 (Norton Brg Up NBS)", "signal", 48114, "NBS", "1100"),
    ("NS4331 (Norton Brg Dn NBS)", "signal", 48113, "NBS", "2100"),
    ("NS4332 (Norton Brg Up NBS)", "signal", 48116, "NBS", "1100"),
    ("NS4333 (Norton Brg Dn NBS)", "signal", 48115, "NBS", "2100"),
    ("NS5692 (Norton Brg NBS1 3100)", "signal", 48120, "NBS1", "3100"),
    ("NS5693 (Norton Brg NBS1 3100)", "signal", 48119, "NBS1", "3100"),
    ("NS5694 (Norton Brg NBS1 3100)", "signal", 17286, "NBS1", "3100"),
    ("NS5695 (Norton Brg NBS1 3100)", "signal", 48118, "NBS1", "3100"),
    ("NS5688 (Norton Brg NBS1 3100)", "signal", 17319, "NBS1", "3100"),
    ("NS5691 (Norton Brg NBS1 3100)", "signal", 48121, "NBS1", "3100"),
    # Leeds East & West approaches (DOL2 / HUL4)
    ("LEEDS 13621 (DOL2 not xover)", "signal", 15320, "DOL2", "2100"),
    ("LEEDS L3644 (DOL2 Line A)", "signal", 15335, "DOL2", "3101"),
    ("LEEDS L3631 (DOL2 Line A)", "signal", 15410, "DOL2", "3101"),
    ("LEEDSWJ U-B (DOL2 Line B)", "signal", 50890, "DOL2", "3102"),
    ("LEEDSEJ (HUL4 U-DM -> 2100)", "signal", 15310, "HUL4", "2100"),
    # Liverpool Street (LTN1 + LVS multi-ELR)
    ("LIVST Plat 1", "signal", 28727, "LTN1", "3601"),
    ("LIVST Plat 10", "signal", 4338, "LTN1", "3610"),
    ("LIVST Plat 11 (LVS)", "signal", 4346, "LVS", "3611"),
    ("LIVST Plat 12 (LVS)", "signal", 4347, "LVS", "3612"),
    ("LIVST Plat 17 (LVS)", "signal", 28711, "LVS", "3617"),
    # York (ECM4 / ECM5 / YMS multi-ELR)
    ("YORK Plat 1", "signal", 3234, "ECM4", "3601"),
    ("YORK Plat 2 (YMS)", "signal", 3236, "YMS", "3602"),
    ("YORK Y221 Plat 3 Dn (ECM4)", "signal", 3239, "ECM4", "1100"),
    ("YORK Y235 Plat 4 (YMS)", "signal", 3242, "YMS", "3804"),
    ("YORK Y228 Plat 5 Up (ECM4)", "signal", 3243, "ECM4", "2100"),
    ("YORK Plat 8", "signal", 3235, "ECM5", "3608"),
    ("YORK Y231 Plat 8 (ECM5)", "signal", 3248, "ECM5", "3608"),
    ("YORK Plat 11", "signal", 3251, "ECM4", "3811"),
    # York South Approach (NOC / ECM4 parallel 1150/2150 Leeds Fast vs 1100/2100 Main)
    ("YORK Y761 (NOC Dn Leeds)", "signal", 2054, "NOC", "2150"),
    ("YORK Y763 (NOC Dn Main)", "signal", 2055, "NOC", "2100"),
    ("YORK Y764 (ECM4 Up Leeds)", "signal", 2053, "ECM4", "1150"),
    ("YORK Y766 (ECM4 Up Main)", "signal", 2052, "ECM4", "1100"),
    # Midland Main Line / Erewash (SPC9 parallel 2150 vs 2100)
    ("SPC9 TC4753 (Dn Erewash)", "signal", 14819, "SPC9", "2150"),
    ("SPC9 DC4855 (Dn Main)", "signal", 14820, "SPC9", "2100"),
    # Birmingham New Street (RBS1 / RBS2)
    ("BHAMNWS Plat 1", "signal", 37602, "RBS2", "3801"),
    ("BHAMNWS Plat 2", "signal", 37604, "RBS2", "3802"),
    ("BHAMNWS Plat 3", "signal", 16107, "RBS2", "3803"),
    ("BHAMNWS Plat 12", "signal", 12434, "RBS2", "3812"),
    # Reading (MLN1)
    ("RDG T1682 (Plat 7 DML)", "signal", 44388, "MLN1", "2100"),
    ("RDG T1684 (Plat 8)", "signal", 43994, "MLN1", "2508"),
    ("RDG T1692 (Plat 12 DRL)", "signal", 43987, "MLN1", "2200"),
    ("RDG T1717 (Plat 12 Dn)", "signal", 43991, "MLN1", "2200"),
    ("RDG T1695 (Plat 13)", "signal", 43986, "MLN1", "2513"),
    ("RDG T1697 (Plat 14)", "signal", 43980, "MLN1", "1514"),
    ("RDG T1698 (Plat 15 URL)", "signal", 43973, "MLN1", "1200"),
    ("RDG T1706 (Plat 15 Dn)", "signal", 43974, "MLN1", "1200"),
    # King's Cross (ECM1 platforms, Plat 0, Belle Isle A-F, Holloway)
    ("KGX P0 (Plat 0 Bay)", "signal", 52202, "ECM1", "3600"),
    ("KGX YA1000 (Plat 0 Bay)", "signal", 52203, "ECM1", "3600"),
    ("KGX P10 (10B Bay)", "signal", 52223, "ECM1", "3610"),
    ("KGX Belle Isle Line D", "signal", 62157, "ECM1", "3104"),
    ("KGX YA1073 (ECM1 Dn Slow)", "signal", 67235, "ECM1", "2200"),
    # Glasgow Central (WCM2 Eglinton St, Bridge St 1L-6L, Clyde Bridge A-H & numbered sidings)
    ("GLC GG5224 (1L -> 1101)", "signal", 32304, "WCM2", "1101"),
    ("GLC GG5260 (No1 Sdg -> 3901)", "signal", 33986, "WCM2", "3901"),
    ("GLC GG5269 (No1 Sdg -> 3901)", "signal", 34021, "WCM2", "3901"),
    ("GLC GG5281 (4L -> 3204)", "signal", 33837, "WCM2", "3204"),
    ("GLC GG5283 (3L -> 3103)", "signal", 33836, "WCM2", "3103"),
    ("GLC GG5284 (1L -> 1101)", "signal", 29912, "WCM2", "1101"),
    ("GLC GG5285 (2L -> 2102)", "signal", 29863, "WCM2", "2102"),
    ("GLC GG5481 (L6 -> 3206)", "signal", 29896, "WCM2", "3206"),
    ("GLC GG5484 (Line C -> 3103)", "signal", 58970, "WCM2", "3103"),
    ("GLC GG5520 (Line A -> 1101)", "signal", 29913, "WCM2", "1101"),
    ("GLC GG5522 (Line B -> 2102)", "signal", 58986, "WCM2", "2102"),
    ("GLC GG5524 (Line C -> 3103)", "signal", 29904, "WCM2", "3103"),
    ("GLC GG5523 (Line G -> 1207)", "signal", 59992, "WCM2", "1207"),
    ("GLC GG5521 (Line H -> 2208)", "signal", 29897, "WCM2", "2208"),
    ("GLC GG5460 (No3 Sdg -> 3903)", "signal", 42738, "WCM2", "3903"),
    ("GLC GG5462 (No4 Sdg -> 3904)", "signal", 36693, "WCM2", "3904"),
    # Crewe & North approaches (CMP1 Up Manchester Loop, LLI Independent)
    ("Crewe CE152 (Up Manch Loop)", "signal", 7258, "CMP1", "1500"),
    ("Crewe North CY55 (Dn Indep)", "signal", 7215, "LLI", "2100"),
    # York South Approach (ECM4 Up/Down Leeds 1150/2150 vs Goods Loop 3500)
    ("York Y201 (Dn Leeds -> 2150)", "signal", 2594, "ECM4", "2150"),
    ("York 613 (Up Leeds -> 1150)", "signal", 2596, "ECM4", "1150"),
    ("York Y202 (Up Leeds -> 1150)", "signal", 2303, "ECM4", "1150"),
    ("York Y205 (Dn Leeds -> 2150)", "signal", 2304, "ECM4", "2150"),
    ("York Y625 (Up Leeds -> 1150)", "signal", 2599, "ECM4", "1150"),
    # Warrington Bank Quay & Royal Mail (CGJ3 Plat 1, Plat 4, Down Slow, Royal Mail sidings)
    ("WBQ WN196 (Plat 1 -> 1200)", "signal", 7880, "CGJ3", "1200"),
    ("WBQ WN191 (Plat 1 -> 1200)", "signal", 7881, "CGJ3", "1200"),
    ("WBQ WN197 (Plat 4 -> 2202)", "signal", 62002, "CGJ3", "2202"),
    ("WBQ WN193 (Plat 4 -> 2202)", "signal", 7883, "CGJ3", "2202"),
    ("WBQ WN198 (Dn Slow -> 2201)", "signal", 62003, "CGJ3", "2201"),
    ("WBQ WN194 (Dn Slow -> 2201)", "signal", 7885, "CGJ3", "2201"),
    ("WBQ WN138 (Royal Mail -> 1900)", "signal", 7876, "CGJ3", "1900"),
    # Weaver Junction Liverpool Flyover (CGJ1 -> WJL1 1100/2100)
    ("Weaver Jn WD168 (Up Lpool)", "signal", 7777, "WJL1", "1100"),
    ("Weaver Jn WD171 (Dn Lpool)", "signal", 7778, "WJL1", "2100"),
    ("Weaver Jn WD170 (WJL1 Up)", "signal", 7525, "WJL1", "1100"),
    # Great Eastern Main Line (LTN1 Electric 1152/2152, Suburban 1154/2154, Gidea Park) & BGK
    ("LTN1 203 (Dn Elec -> 2152)", "signal", 4077, "LTN1", "2152"),
    ("LTN1 202 (Up Elec -> 1152)", "signal", 4079, "LTN1", "1152"),
    ("LTN1 70 (Up Sub -> 1154)", "signal", 4243, "LTN1", "1154"),
    ("LTN1 81 (Dn Sub -> 2154)", "signal", 4365, "LTN1", "2154"),
    ("LTN1 433 (Gidea Dn El -> 2152)", "signal", 4493, "LTN1", "2152"),
    ("LTN1 435 (Gidea Dn Ml -> 2100)", "signal", 4494, "LTN1", "2100"),
    ("LTN1 GIDEPKS U-2C (-> 2902)", "signal", 50227, "LTN1", "2902"),
    ("BGK 90 (Up Sub -> 1150)", "signal", 4068, "BGK", "1150"),
    ("BGK 91 (Dn Sub -> 2150)", "signal", 4069, "BGK", "2150"),
    ("ECM1 D22 (Up Fast -> 1100)", "signal", 2, "ECM1", "1100"),
    # Norton Bridge -> Stone (NBS zero-based branch must not jump back onto trunk LEC4)
    ("Stone SOT401 (NBS Dn)", "signal", 17281, "NBS", "2100"),
    ("Stone SOT403 (NBS Dn)", "signal", 17280, "NBS", "2100"),
    ("Stone SOT405 (NBS Dn)", "signal", 17279, "NBS", "2100"),
    ("Stone P786B (NBS Dn)", "points", 56328, "NBS", "2100"),
    # ECML South (ECM1 Slow 2, Huntingdon Bay, Peterborough Slow 2, Newark DUPL, Doncaster)
    ("ECM1 K403 (Dn Slow 2 -> 2252)", "signal", 568, "ECM1", "2252"),
    ("ECM1 K453 (Dn Slow 2 -> 2252)", "signal", 52, "ECM1", "2252"),
    ("ECM1 P1 HNTNGDN (Bay -> 1201)", "signal", 138, "ECM1", "1201"),
    ("ECM1 P438 (PBRO US2 -> 1252)", "signal", 893, "ECM1", "1252"),
    ("ECM1 D77 (Newark DUPL -> 3500)", "signal", 469, "ECM1", "3500"),
    ("ECM1 D278 (Doncaster -> 1801)", "signal", 3853, "ECM1", "1801"),
    # MML South (SPC1 Luton Plat 1-5 + Points, Cricklewood WH218) & GWML (MLN1 DR/UR Relief)
    ("SPC1 WH302 (Luton P1 -> 1200)", "signal", 13308, "SPC1", "1200"),
    ("SPC1 WH465 (Luton P1 -> 1200)", "signal", 13287, "SPC1", "1200"),
    ("SPC1 WH478 (Luton P2 -> 3501)", "signal", 13309, "SPC1", "3501"),
    ("SPC1 WH463 (Luton P2 -> 3501)", "signal", 13310, "SPC1", "3501"),
    ("SPC1 WH476 (Luton P3 -> 2200)", "signal", 13300, "SPC1", "2200"),
    ("SPC1 WH102 (Luton P4 -> 1100)", "signal", 45440, "SPC1", "1100"),
    ("SPC1 WH101 (Luton P5 -> 2100)", "signal", 13269, "SPC1", "2100"),
    ("SPC1 P864B (Luton P3 -> 2200)", "points", 67590, "SPC1", "2200"),
    ("SPC1 P869 (Luton Jn -> 1200)", "points", 67593, "SPC1", "1200"),
    ("SPC1 P871 (Luton P1 -> 1200)", "points", 67594, "SPC1", "1200"),
    ("SPC1 P870B (Luton P2 -> 3501)", "points", 67597, "SPC1", "3501"),
    ("SPC1 WH218 (Cricklewood -> 1200)", "signal", 13281, "SPC1", "1200"),
    ("MLN1 EALINGB (P3 DR -> 2200)", "signal", 18019, "MLN1", "2200"),
    ("MLN1 SN209 (Ealing DR -> 2200)", "signal", 18234, "MLN1", "2200"),
    ("MLN1 WEALJN (W Ealing UR -> 1200)", "signal", 48263, "MLN1", "1200"),
    ("MLN1 T576 (Maidenhead -> 1200)", "signal", 18451, "MLN1", "1200"),
    # SEML (XTD Goods Loops, Paddock Wood Up/Down Loops, Sevenoaks), WCML (LEC1 Rugby UGL), SWML (BML1 Eastleigh)
    ("XTD HEADCRN (U-UGL -> 1300)", "signal", 23741, "XTD", "1300"),
    ("XTD P1213 (Headcorn UGL -> 1300)", "points", 69507, "XTD", "1300"),
    ("XTD P913B (Hither Grn GL -> 1300)", "points", 63678, "XTD", "1300"),
    ("XTD AD184 (Pdk Wd DPL -> 3503)", "signal", 23319, "XTD", "3503"),
    ("XTD AD197 (Pdk Wd DPL -> 3503)", "signal", 23320, "XTD", "3503"),
    ("XTD AD190 (Pdk Wd UPL -> 1200)", "signal", 23322, "XTD", "1200"),
    ("XTD AD203X (Pdk Wd UPL -> 1200)", "signal", 23323, "XTD", "1200"),
    ("XTD AD102 (Sevenoaks UL -> 3502)", "signal", 26019, "XTD", "3502"),
    ("LEC1 RN4182 (Rugby UGL -> 1300)", "signal", 14546, "LEC1", "1300"),
    ("BML1 E89 (Eastleigh DPL -> 2500)", "signal", 22883, "BML1", "2500"),
    ("BML1 E339 (U-RVL Fast -> 1100)", "signal", 26318, "BML1", "1100"),
    # MML North (SPC5 rev Fast, Loughborough Plat 2 vmax), Derby (SPC8), SWML (SWM2)
    ("SPC5 647 (Rev U-FL -> 1100)", "signal", 12216, "SPC5", "1100"),
    ("SPC5 LR452 (Rev U-FL -> 1100)", "signal", 16404, "SPC5", "1100"),
    ("SPC5 LR503 (Loughb P2 -> 2200)", "signal", 16454, "SPC5", "2200"),
    ("SPC8 DC5099 (Breadsall GL -> 3205)", "signal", 49641, "SPC8", "3205"),
    ("SPC8 DC5089 (Derby D-DL -> 3204)", "signal", 49633, "SPC8", "3204"),
    ("SPC8 P344A (Derby D-DL -> 3204)", "points", 68431, "SPC8", "3204"),
    ("SWM2 19 (Main Line -> 1100)", "signal", 21864, "SWM2", "1100"),
    ("SWM2 BRGEND (Dn Main -> 2100)", "signal", 28940, "SWM2", "2100"),
    # Final UK-wide edge-case guards (Radlett WH261, Reading T1745, SCM2 3SDG, Sheffield TJC1 S85)
    ("SPC1 WH261 (Radlett P2 -> 2200)", "signal", 13478, "SPC1", "2200"),
    ("MLN1 T1745 (Reading P12 -> 3500)", "signal", 18473, "MLN1", "3500"),
    ("SCM2 SHUNT (U-3SDG -> 1503)", "signal", 33293, "SCM2", "1503"),
    ("TJC1 S85 (Sheffield Bay -> 2500)", "signal", 16242, "TJC1", "2500"),
    # Cross-ELR single-track branch bay (Slough Plat 1 -> WIN 3600/3100) & Old Oak Common (WLL9)
    ("MLN1 T3533 (Slough P1 -> WIN 3600)", "signal", 18485, "WIN", "3600"),
    ("MLN1 P1 SLOUGH (-> WIN 3600)", "signal", 18491, "WIN", "3600"),
    ("MLN1 T3538 (Slough -> WIN 3100)", "signal", 18481, "WIN", "3100"),
    ("MLN1 OLDOXRS (P1 -> WLL9 3801)", "signal", 50046, "WLL9", "3801"),
    # Cardiff Central East bi-directional 34xx approach continuation (3412=UM, 3413=DM, 3402=UR, 3403=DR)
    ("SWM2 CF2016 (Cardiff UF -> 3412)", "signal", 48292, "SWM2", "3412"),
    ("SWM2 CF2018 (Cardiff UM -> 3412)", "signal", 45412, "SWM2", "3412"),
    ("SWM2 CF2618 (Cardiff DM -> 3413)", "signal", 21693, "SWM2", "3413"),
    ("SWM2 CF2228 (Cardiff UR -> 3402)", "signal", 48290, "SWM2", "3402"),
    ("SWM2 CF2616 (Cardiff UR -> 3401)", "signal", 45413, "SWM2", "3401"),
    ("SWM2 CF2710 (Cardiff DR -> 3403)", "signal", 48289, "SWM2", "3403"),
    ("SWM2 CF2712 (Cardiff DR -> 3403)", "signal", 49105, "SWM2", "3403"),
    ("SWM2 CF2231 (Cardiff DR -> 3403)", "signal", 49107, "SWM2", "3403"),
    # Stoke-on-Trent (CMD2 Plat 1/2/3 Bay, SCQ1 cross-ELR guard, bi-dir DM override)
    ("CMD2 SOT433 (Stoke P1 -> 1100)", "signal", 7749, "CMD2", "1100"),
    ("CMD2 SOT278 (Stoke P1 -> 1100)", "signal", 7672, "CMD2", "1100"),
    ("CMD2 SOT277 (Stoke P2 -> 2100)", "signal", 7668, "CMD2", "2100"),
    ("CMD2 SOT426 (Stoke P2 DM -> 2100)", "signal", 7669, "CMD2", "2100"),
    ("CMD2 SOT431 (Stoke P3 Bay -> 3600)", "signal", 7674, "CMD2", "3600"),
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
                    tps_node_ids=nids,
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

