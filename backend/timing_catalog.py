"""Timing loads, traction units, coaches, velocity profiles, and station banks.

Builds and caches the in-memory timing catalog queried by search, timing-load
browser endpoints, and station-detail inspectors.
"""

from typing import Any, Optional

from psycopg.rows import dict_row

from config import decode_code, get_conn, load_lookup_cache
from geometry import parse_traction_curves

TIMING_CATALOG_CACHE: Optional[dict[str, Any]] = None


def reset_timing_catalog_cache() -> None:
    """Clear the cached timing catalog (used by unit tests)."""
    global TIMING_CATALOG_CACHE
    TIMING_CATALOG_CACHE = None


def _load_traction_units(cur) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Load all ``<tractionunit>`` elements and extract their traction/resistance curves.

    Args:
        cur: Active PostgreSQL cursor returning dict rows.

    Returns:
        Tuple of ``(traction_units_list, id_to_traction_unit_map)``.
    """
    cur.execute(
        "SELECT entity_id, name, raw_attrs, raw_children FROM raw_tps_elements "
        "WHERE tag = 'tractionunit' AND entity_id != '0' ORDER BY name ASC"
    )
    traction_units: list[dict[str, Any]] = []
    tu_lookup: dict[str, dict[str, Any]] = {}
    for r in cur.fetchall():
        ta = r["raw_attrs"] or {}
        tc = r["raw_children"] or []
        parsed = parse_traction_curves(ta, tc, decode_fn=decode_code)
        vmax_kmh = float(ta.get("maxallowedspeed") or 0)
        item = {
            "id": int(r["entity_id"]) if str(r["entity_id"]).isdigit() else r["entity_id"],
            "name": r["name"],
            "desc": ta.get("desc") or "",
            "type_id": ta.get("type"),
            "type_code": decode_code("tractiontypedesc", ta.get("type")) or "—",
            "motor_id": ta.get("motortype"),
            "motor_desc": decode_code("motortypedesc", ta.get("motortype")) or "—",
            "length_m": float(ta.get("length") or 0),
            "weight_t": float(ta.get("weight") or 0),
            "max_speed_kmh": vmax_kmh,
            "max_speed_mph": round(vmax_kmh * 0.621371, 1),
            "retardation": float(ta.get("defretardation") or 0),
            "coeffA": float(ta.get("coeffA") or 0),
            "coeffB": float(ta.get("coeffB") or 0),
            "coeffC": float(ta.get("coeffC") or 0),
            "max_force_kn": parsed["max_force_kn"],
            "max_power_kw": parsed["max_power_kw"],
            "curve_count": len(parsed["fs_diagrams"]),
        }
        traction_units.append(item)
        tu_lookup[str(r["entity_id"])] = item
    return traction_units, tu_lookup


def _load_coaches(cur) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Load all trailing ``<coach>`` definitions from ``raw_tps_elements``.

    Args:
        cur: Active PostgreSQL cursor returning dict rows.

    Returns:
        Tuple of ``(coaches_list, id_to_coach_map)``.
    """
    cur.execute(
        "SELECT entity_id, name, raw_attrs FROM raw_tps_elements "
        "WHERE tag = 'coach' AND entity_id != '0' ORDER BY name ASC"
    )
    coaches: list[dict[str, Any]] = []
    coach_lookup: dict[str, dict[str, Any]] = {}
    for r in cur.fetchall():
        ca = r["raw_attrs"] or {}
        vmax_kmh = float(ca.get("maxspeed") or 0)
        citem = {
            "id": int(r["entity_id"]) if str(r["entity_id"]).isdigit() else r["entity_id"],
            "name": r["name"],
            "desc": ca.get("desc") or "",
            "coach_type": decode_code("coachtypedesc", ca.get("coachtypeid")) or "—",
            "length_m": float(ca.get("length") or 0),
            "weight_t": float(ca.get("weight") or 0),
            "max_speed_kmh": vmax_kmh,
            "max_speed_mph": round(vmax_kmh * 0.621371, 1),
        }
        coaches.append(citem)
        coach_lookup[str(r["entity_id"])] = citem
    return coaches, coach_lookup


def _load_model_trains(
    cur,
    tu_lookup: dict[str, dict[str, Any]],
    coach_lookup: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Load all ``<modeltrain>`` timing loads and aggregate traction/coach totals.

    Args:
        cur: Active PostgreSQL cursor returning dict rows.
        tu_lookup: Traction-unit lookup keyed by entity ID string.
        coach_lookup: Coach lookup keyed by entity ID string.

    Returns:
        Tuple of ``(model_trains_list, velocity_profile_usage_counts)``.
    """
    cur.execute(
        "SELECT entity_id, name, raw_attrs, raw_children FROM raw_tps_elements "
        "WHERE tag = 'modeltrain' AND entity_id != '0' ORDER BY name ASC"
    )
    model_trains: list[dict[str, Any]] = []
    profile_train_counts: dict[str, int] = {}
    for r in cur.fetchall():
        ma = r["raw_attrs"] or {}
        mc = r["raw_children"] or []
        tu_ids = [
            str(c.get("attrs", {}).get("id"))
            for c in mc
            if c.get("tag") == "tractionunit" and c.get("attrs", {}).get("id")
        ]
        tu_names: list[str] = []
        tu_len = 0.0
        tu_wt = 0.0
        motor_types: set[str] = set()
        for tid in tu_ids:
            tinfo = tu_lookup.get(tid)
            if tinfo:
                tu_names.append(tinfo["name"])
                tu_len += tinfo["length_m"]
                tu_wt += tinfo["weight_t"]
                if tinfo["motor_desc"] and tinfo["motor_desc"] != "—":
                    motor_types.add(tinfo["motor_desc"])

        cid = str(ma.get("coachid") or "0")
        cinfo = coach_lookup.get(cid)
        c_len = float(ma.get("coachestotallength") or 0)
        c_wt = float(ma.get("coachestotalweight") or 0)
        vmax_kmh = float(ma.get("maxspeed") or 0)
        prof_id = str(ma.get("profile") or "0")
        profile_train_counts[prof_id] = profile_train_counts.get(prof_id, 0) + 1

        model_trains.append({
            "id": int(r["entity_id"]) if str(r["entity_id"]).isdigit() else r["entity_id"],
            "name": r["name"],
            "publicname": ma.get("publicname") or r["name"],
            "desc": ma.get("desc") or "",
            "maxspeed_kmh": vmax_kmh,
            "maxspeed_mph": round(vmax_kmh * 0.621371, 1),
            "retardation": float(ma.get("retardation") or 0),
            "profile_id": prof_id,
            "profile_name": decode_code("veloprofiledesc", prof_id) or "Standard (0)",
            "traction_summary": " + ".join(tu_names) if tu_names else "—",
            "propulsion": "/".join(sorted(motor_types)) if motor_types else "—",
            "coach_name": cinfo["name"] if cinfo else ("—" if c_len == 0 else "Custom Trailing"),
            "coaches_length_m": c_len,
            "coaches_weight_t": c_wt,
            "total_length_m": round(tu_len + c_len, 1),
            "total_weight_t": round(tu_wt + c_wt, 1),
            "atc": ma.get("atc") == "true",
        })
    return model_trains, profile_train_counts


def _load_station_banks(cur) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Load ``<stationbank>`` corridors and build an O(1) ``station_id -> banks`` index.

    Args:
        cur: Active PostgreSQL cursor returning dict rows.

    Returns:
        Tuple of ``(station_banks_list, station_to_banks_map)``.
    """
    cur.execute(
        "SELECT entity_id, name, raw_attrs, raw_children FROM raw_tps_elements "
        "WHERE tag = 'stationbank' ORDER BY name ASC"
    )
    station_banks: list[dict[str, Any]] = []
    sec_to_bank: dict[str, dict[str, Any]] = {}
    for r in cur.fetchall():
        sa = r["raw_attrs"] or {}
        bid = int(r["entity_id"]) if str(r["entity_id"]).isdigit() else r["entity_id"]
        bobj = {
            "id": bid,
            "code": r["name"],
            "description": sa.get("description") or "",
            "section_count": len(r["raw_children"] or []),
            "last_modified": sa.get("lastmodified") or "",
        }
        station_banks.append(bobj)
        for ch in r["raw_children"] or []:
            if ch.get("tag") == "stationbankid":
                s_id = str(ch.get("attrs", {}).get("stationbankid") or "")
                if s_id:
                    sec_to_bank[s_id] = bobj

    cur.execute(
        "SELECT entity_id, name, raw_attrs, raw_children FROM raw_tps_elements "
        "WHERE tag = 'stationbanksection'"
    )
    station_to_banks: dict[str, list[dict[str, Any]]] = {}
    for srow in cur.fetchall():
        sec_id = str(srow["entity_id"])
        sa = srow["raw_attrs"] or {}
        parent_bank = sec_to_bank.get(sec_id)
        b_entry = {
            "bank_id": parent_bank["id"] if parent_bank else (int(sec_id) if sec_id.isdigit() else sec_id),
            "section_id": sec_id,
            "code": parent_bank["code"] if parent_bank else srow["name"],
            "description": (parent_bank["description"] if parent_bank else sa.get("description")) or "",
        }
        seen_st_in_sec: set[str] = set()
        for ch in srow["raw_children"] or []:
            if ch.get("tag") == "stationbankstation":
                st_id = str(ch.get("attrs", {}).get("id") or "")
                if st_id and st_id not in seen_st_in_sec:
                    seen_st_in_sec.add(st_id)
                    lst = station_to_banks.setdefault(st_id, [])
                    if len(lst) < 25:
                        lst.append(b_entry)
    return station_banks, station_to_banks


def get_timing_catalog() -> dict[str, Any]:
    """Build and cache the full TPS timing catalog (trains, traction, profiles, banks).

    Returns:
        Dict containing ``counts``, ``model_trains``, ``traction_units``,
        ``coaches``, ``velocity_profiles``, ``train_types``, ``station_banks``,
        ``timing_roles``, ``planning_categories``, and ``station_to_banks``.
    """
    global TIMING_CATALOG_CACHE
    if TIMING_CATALOG_CACHE is not None:
        return TIMING_CATALOG_CACHE

    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            traction_units, tu_lookup = _load_traction_units(cur)
            coaches, coach_lookup = _load_coaches(cur)
            model_trains, profile_train_counts = _load_model_trains(cur, tu_lookup, coach_lookup)

            lookup = load_lookup_cache()
            velocity_profiles = [
                {
                    "id": pid,
                    "code": pinfo["label"],
                    "description": pinfo["vanilla_text"] or pinfo["description"] or pinfo["label"],
                    "timing_load_count": profile_train_counts.get(pid, 0),
                }
                for pid, pinfo in sorted(
                    lookup.get("veloprofiledesc", {}).items(),
                    key=lambda x: int(x[0]) if x[0].isdigit() else 999,
                )
                if pid != "0"
            ]

            train_types = [
                {
                    "id": tid,
                    "code": tinfo["label"],
                    "description": tinfo["description"] or tinfo["vanilla_text"] or "",
                }
                for tid, tinfo in sorted(
                    lookup.get("traintype", {}).items(),
                    key=lambda x: x[1]["label"] or "",
                )
                if tid not in ("0", "181")
            ]

            station_banks, station_to_banks = _load_station_banks(cur)

            cur.execute(
                "SELECT station_type, COUNT(*) AS cnt FROM stations "
                "WHERE station_id > 0 GROUP BY station_type ORDER BY cnt DESC"
            )
            timing_roles = [
                {
                    "type_id": r["station_type"],
                    "label": decode_code("stationtypedesc", r["station_type"]) or f"Type {r['station_type']}",
                    "count": r["cnt"],
                }
                for r in cur.fetchall()
            ]
            cur.execute(
                "SELECT station_category, COUNT(*) AS cnt FROM stations "
                "WHERE station_id > 0 GROUP BY station_category ORDER BY cnt DESC"
            )
            planning_categories = [
                {
                    "category_id": r["station_category"],
                    "label": decode_code("stationcategorydesc", r["station_category"])
                    or f"Category {r['station_category']}",
                    "count": r["cnt"],
                }
                for r in cur.fetchall()
            ]

    TIMING_CATALOG_CACHE = {
        "counts": {
            "model_trains": len(model_trains),
            "traction_units": len(traction_units),
            "coaches": len(coaches),
            "velocity_profiles": len(velocity_profiles),
            "train_types": len(train_types),
            "station_banks": len(station_banks),
        },
        "model_trains": model_trains,
        "traction_units": traction_units,
        "coaches": coaches,
        "velocity_profiles": velocity_profiles,
        "train_types": train_types,
        "station_banks": station_banks,
        "timing_roles": timing_roles,
        "planning_categories": planning_categories,
        "station_to_banks": station_to_banks,
    }
    return TIMING_CATALOG_CACHE

