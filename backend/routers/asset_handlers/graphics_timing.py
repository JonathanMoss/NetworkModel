"""TPS schematic graphics and timing-load / traction / station-bank handlers."""

from typing import Any, Optional

from fastapi import HTTPException

from config import decode_code
from geometry import meters_to_miles_chains, parse_traction_curves

def _get_graphictext_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a schematic graphic text label."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
    cur.execute("SELECT * FROM graphic_texts WHERE pk = %s", (asset_id,))
    gt = cur.fetchone()
    if not gt:
        raise HTTPException(status_code=404, detail="Graphic text not found")
    decoded = {
        "Graphic Text PK": gt["pk"],
        "Label Text": gt["text"],
        "Coordinates (x, y)": f"({gt['x']}, {gt['y']})",
        "Font Size": gt["size"],
        "Rotation Angle": f"{gt['angle']}°",
        "Layer": gt["layers"],
        "Validity Window": f"{gt['valid_from']} → {gt['valid_to']}",
        "Last Modified": gt["last_modified"],
    }
    return {
        "asset_type": "graphictext",
        "id": gt["pk"],
        "title": f"CAD Label \"{gt['text']}\"",
        "subtitle": f"Layer {gt['layers']} • Size {gt['size']} • ({gt['x']}, {gt['y']})",
        "x": gt["x"],
        "y": gt["y"],
        "decoded": decoded,
        "related": {},
        "highlight": {
            "ways": [],
            "points": [{"x": gt["x"], "y": gt["y"], "label": gt["text"], "color": "#38bdf8"}],
        },
        "raw_attrs": gt["raw_attrs"],
        "raw_children": [],
    }



def _get_graphicvector_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a schematic graphic vector polyline."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
    cur.execute("SELECT * FROM graphic_vectors WHERE pk = %s", (asset_id,))
    gv = cur.fetchone()
    if not gv:
        raise HTTPException(status_code=404, detail="Graphic vector not found")
    decoded = {
        "Graphic Vector PK": gv["pk"],
        "Start (x0, y0)": f"({gv['x0']}, {gv['y0']})",
        "End (x1, y1)": f"({gv['x1']}, {gv['y1']})",
        "Layer": gv["layers"],
        "Validity Window": f"{gv['valid_from']} → {gv['valid_to']}",
        "Last Modified": gv["last_modified"],
    }
    return {
        "asset_type": "graphicvector",
        "id": gv["pk"],
        "title": f"CAD Vector #{gv['pk']}",
        "subtitle": f"Layer {gv['layers']} • ({gv['x0']}, {gv['y0']}) → ({gv['x1']}, {gv['y1']})",
        "x": (gv["x0"] + gv["x1"]) / 2.0,
        "y": (gv["y0"] + gv["y1"]) / 2.0,
        "decoded": decoded,
        "related": {},
        "highlight": {
            "ways": [{"label": f"Vector #{gv['pk']}", "coords": [[gv["x0"], gv["y0"]], [gv["x1"], gv["y1"]]], "color": "#38bdf8"}],
            "points": [],
        },
        "raw_attrs": gv["raw_attrs"],
        "raw_children": [],
    }



def _get_annotation_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a schematic annotation marker."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
    cur.execute("SELECT * FROM annotations WHERE pk = %s", (asset_id,))
    an = cur.fetchone()
    if not an:
        raise HTTPException(status_code=404, detail="Annotation not found")
    decoded = {
        "Annotation PK": an["pk"],
        "Coordinates (x, y)": f"({an['x']}, {an['y']})",
        "Operational Note (HTML)": an["text"],
        "Validity Window": f"{an['valid_from']} → {an['valid_to']}",
        "Last Modified": an["last_modified"],
    }
    return {
        "asset_type": "annotation",
        "id": an["pk"],
        "title": f"Operational Note #{an['pk']}",
        "subtitle": f"({an['x']}, {an['y']}) • Last Modified {an['last_modified']}",
        "x": an["x"],
        "y": an["y"],
        "decoded": decoded,
        "related": {},
        "highlight": {
            "ways": [],
            "points": [{"x": an["x"], "y": an["y"], "label": f"Note #{an['pk']}", "color": "#ec4899"}],
        },
        "raw_attrs": an["raw_attrs"],
        "raw_children": [],
    }



def _get_polyarea_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a schematic polygon area."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
    cur.execute("SELECT * FROM poly_areas WHERE pk = %s", (asset_id,))
    pa = cur.fetchone()
    if not pa:
        raise HTTPException(status_code=404, detail="Poly area not found")
    cx = (pa["min_x"] + pa["max_x"]) / 2.0 if pa["min_x"] is not None else None
    cy = (pa["min_y"] + pa["max_y"]) / 2.0 if pa["min_y"] is not None else None
    decoded = {
        "Poly Area PK / XML ID": f"{pa['pk']} / {pa['id']}",
        "Name": pa["name"],
        "Description": pa["description"] or "—",
        "Corner Points": len(pa["corners"] or []),
        "Bounding Box": f"[{pa['min_x']}, {pa['min_y']}] → [{pa['max_x']}, {pa['max_y']}]",
    }
    corners = pa["corners"] or []
    closed_way = corners + [corners[0]] if len(corners) >= 3 else corners
    return {
        "asset_type": "polyarea",
        "id": pa["pk"],
        "title": f"Poly Region {pa['name'] or '#' + str(pa['id'])}",
        "subtitle": f"ID {pa['id']} • {len(corners)} polygon vertices",
        "x": cx,
        "y": cy,
        "decoded": decoded,
        "related": {},
        "highlight": {
            "ways": [{"label": pa["name"], "coords": closed_way, "color": "#a855f7"}] if closed_way else [],
            "points": [],
        },
        "raw_attrs": pa["raw_attrs"],
        "raw_children": pa["raw_children"],
    }



def _get_modeltrain_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a TPS model train, traction curves, and velocity profile."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
    cur.execute(
        "SELECT * FROM raw_tps_elements WHERE tag = 'modeltrain' AND entity_id = %s LIMIT 1",
        (str(asset_id),),
    )
    mt = cur.fetchone()
    if not mt:
        raise HTTPException(status_code=404, detail="Model train / timing load not found")
    attrs = mt["raw_attrs"] or {}
    children = mt["raw_children"] or []

    # Extract traction unit IDs in consist order
    tu_ids = [
        str(c.get("attrs", {}).get("id"))
        for c in children
        if c.get("tag") == "tractionunit" and c.get("attrs", {}).get("id")
    ]
    tu_map = {}
    if tu_ids:
        cur.execute(
            "SELECT entity_id, name, raw_attrs, raw_children FROM raw_tps_elements WHERE tag = 'tractionunit' AND entity_id = ANY(%s)",
            (list(set(tu_ids)),),
        )
        for r in cur.fetchall():
            tu_map[str(r["entity_id"])] = r

    consist_units = []
    total_tu_len = 0.0
    total_tu_wt = 0.0
    for idx, tid in enumerate(tu_ids):
        trec = tu_map.get(tid)
        if not trec:
            continue
        ta = trec["raw_attrs"] or {}
        tc = trec["raw_children"] or []
        t_len = float(ta.get("length") or 0)
        t_wt = float(ta.get("weight") or 0)
        total_tu_len += t_len
        total_tu_wt += t_wt
        parsed_curves = parse_traction_curves(ta, tc)
        consist_units.append({
            "seq": idx + 1,
            "id": int(tid) if tid.isdigit() else tid,
            "name": trec["name"],
            "desc": ta.get("desc") or "",
            "type_code": decode_code("tractiontypedesc", ta.get("type")) or "—",
            "motor_desc": decode_code("motortypedesc", ta.get("motortype")) or "—",
            "length_m": t_len,
            "weight_t": t_wt,
            "max_speed_kmh": float(ta.get("maxallowedspeed") or 0),
            "max_speed_mph": round(float(ta.get("maxallowedspeed") or 0) * 0.621371, 1),
            "retardation": float(ta.get("defretardation") or 0),
            "engine_mass_factor": float(ta.get("enginemassfactor") or 1.0),
            "coeffA": float(ta.get("coeffA") or 0),
            "coeffB": float(ta.get("coeffB") or 0),
            "coeffC": float(ta.get("coeffC") or 0),
            "fs_diagrams": parsed_curves["fs_diagrams"],
            "sr_curve": parsed_curves["sr_curve"],
            "max_force_kn": parsed_curves["max_force_kn"],
            "max_power_kw": parsed_curves["max_power_kw"],
        })

    # Resolve trailing coach template if coachid > 0
    coach_id = str(attrs.get("coachid") or "0")
    coach_info = None
    if coach_id != "0":
        cur.execute(
            "SELECT entity_id, name, raw_attrs FROM raw_tps_elements WHERE tag = 'coach' AND entity_id = %s LIMIT 1",
            (coach_id,),
        )
        crow = cur.fetchone()
        if crow:
            ca = crow["raw_attrs"] or {}
            coach_info = {
                "id": crow["entity_id"],
                "name": crow["name"],
                "desc": ca.get("desc") or "",
                "coach_type": decode_code("coachtypedesc", ca.get("coachtypeid")) or "—",
                "unit_length_m": float(ca.get("length") or 0),
                "unit_weight_t": float(ca.get("weight") or 0),
                "max_speed_kmh": float(ca.get("maxspeed") or 0),
            }

    # En-route timing load transitions (<mtchangetransiting>)
    cur.execute(
        """
        SELECT raw_attrs
        FROM raw_tps_elements
        WHERE tag = 'mtchangetransiting'
          AND (raw_attrs->>'firstmodeltrainid' = %s OR raw_attrs->>'secondmodeltrainid' = %s)
        LIMIT 30
        """,
        (str(asset_id), str(asset_id)),
    )
    other_mt_ids = set()
    trans_pairs = []
    for tr in cur.fetchall():
        ra = tr["raw_attrs"] or {}
        f_id = ra.get("firstmodeltrainid")
        s_id = ra.get("secondmodeltrainid")
        if f_id:
            other_mt_ids.add(f_id)
        if s_id:
            other_mt_ids.add(s_id)
        trans_pairs.append((f_id, s_id))

    mt_names = {}
    if other_mt_ids:
        cur.execute(
            "SELECT entity_id, name, raw_attrs->>'desc' AS descr FROM raw_tps_elements WHERE tag = 'modeltrain' AND entity_id = ANY(%s)",
            (list(other_mt_ids),),
        )
        for r in cur.fetchall():
            mt_names[str(r["entity_id"])] = f"{r['name']} ({r['descr'] or ''})"

    transitions = [
        {
            "from_id": int(f) if str(f).isdigit() else f,
            "from_name": mt_names.get(str(f), f"#{f}"),
            "to_id": int(s) if str(s).isdigit() else s,
            "to_name": mt_names.get(str(s), f"#{s}"),
        }
        for f, s in trans_pairs
    ]

    vmax_kmh = float(attrs.get("maxspeed") or 0)
    vmax_mph = round(vmax_kmh * 0.621371, 1)
    coaches_len = float(attrs.get("coachestotallength") or 0)
    coaches_wt = float(attrs.get("coachestotalweight") or 0)
    total_len = round(total_tu_len + coaches_len, 1)
    total_wt = round(total_tu_wt + coaches_wt, 1)
    prof_id = attrs.get("profile", "0")
    prof_desc = decode_code("veloprofiledesc", prof_id) or "Standard Line Speed (Profile 0)"

    decoded = {
        "Timing Load ID / Code": f"#{mt['entity_id']} — {mt['name']}",
        "Public Timetable Timing Load": attrs.get("publicname") or mt["name"],
        "Description": attrs.get("desc") or "—",
        "Maximum Permitted Speed": f"{vmax_kmh:.0f} km/h ({vmax_mph} mph)",
        "Differential Speed Profile": f"{prof_desc} (Profile ID {prof_id})",
        "Traction Unit Consist": (
            f"{len(consist_units)} unit(s): " + " + ".join(f"{u['name']} ({u['desc']})" for u in consist_units)
            if consist_units
            else "None"
        ),
        "Trailing Load / Coaching Stock": (
            f"{coach_info['name']} ({coach_info['desc']}) — {coaches_len} m / {coaches_wt} t"
            if coach_info
            else f"{coaches_len} m / {coaches_wt} t"
        ),
        "Total Train Length & Mass": f"{total_len} m • {total_wt} tonnes",
        "Service Braking Retardation": f"{attrs.get('retardation', '0')} m/s² (ATC: {attrs.get('atcretardation', '0')} m/s²)",
        "Comfort Acceleration": f"{attrs.get('comfortacceleration', '0')} m/s²",
        "Supplementary Run Time": f"{attrs.get('supplruntime', '0')}% • Min Stop Time: {attrs.get('minstoptime', '0')} s",
        "ATC / Check Effective Track Length": f"ATC: {attrs.get('atc')} • Check Effective Track Length: {attrs.get('checkEffectiveTrackLength')}",
        "En-Route Timing Load Transitions": f"{len(transitions)} valid transition pair(s)" if transitions else "None",
        "Last Modified": attrs.get("lastmodified") or "—",
    }

    return {
        "asset_type": "modeltrain",
        "id": int(mt["entity_id"]) if str(mt["entity_id"]).isdigit() else mt["entity_id"],
        "title": f"Timing Load {mt['name']} ({attrs.get('desc') or attrs.get('publicname') or ''})",
        "subtitle": f"Max {vmax_kmh:.0f} km/h ({vmax_mph} mph) • {prof_desc} • {total_len}m / {total_wt}t",
        "x": None,
        "y": None,
        "decoded": decoded,
        "related": {
            "traction_units": consist_units,
            "coach": coach_info,
            "velocity_profile": {
                "id": prof_id,
                "display": prof_desc,
            },
            "transitions": transitions,
        },
        "highlight": {"ways": [], "points": []},
        "raw_attrs": attrs,
        "raw_children": children,
    }



def _get_tractionunit_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a TPS traction unit and its tractive-effort/resistance curves."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
    cur.execute(
        "SELECT * FROM raw_tps_elements WHERE tag = 'tractionunit' AND entity_id = %s LIMIT 1",
        (str(asset_id),),
    )
    tu = cur.fetchone()
    if not tu:
        raise HTTPException(status_code=404, detail="Traction unit not found")
    ta = tu["raw_attrs"] or {}
    tc = tu["raw_children"] or []
    parsed = parse_traction_curves(ta, tc)

    # Find all <modeltrain> timing loads powered by this traction unit
    cur.execute(
        """
        SELECT entity_id, name, raw_attrs
        FROM raw_tps_elements
        WHERE tag = 'modeltrain'
          AND raw_children @> jsonb_build_array(jsonb_build_object('tag', 'tractionunit', 'attrs', jsonb_build_object('id', %s::text)))
        ORDER BY name ASC
        LIMIT 100
        """,
        (str(asset_id),),
    )
    linked_trains = []
    for mrow in cur.fetchall():
        ma = mrow["raw_attrs"] or {}
        mv = float(ma.get("maxspeed") or 0)
        linked_trains.append({
            "id": int(mrow["entity_id"]) if str(mrow["entity_id"]).isdigit() else mrow["entity_id"],
            "name": mrow["name"],
            "publicname": ma.get("publicname") or mrow["name"],
            "desc": ma.get("desc") or "",
            "maxspeed_kmh": mv,
            "maxspeed_mph": round(mv * 0.621371, 1),
            "profile_name": decode_code("veloprofiledesc", ma.get("profile")) or "Standard",
            "coaches_length_m": float(ma.get("coachestotallength") or 0),
            "coaches_weight_t": float(ma.get("coachestotalweight") or 0),
        })

    vmax_kmh = float(ta.get("maxallowedspeed") or 0)
    vmax_mph = round(vmax_kmh * 0.621371, 1)
    ttype = decode_code("tractiontypedesc", ta.get("type")) or "—"
    mtype = decode_code("motortypedesc", ta.get("motortype")) or "—"
    prof_desc = decode_code("veloprofiledesc", ta.get("defveloprof")) or "Standard (0)"
    ca = float(ta.get("coeffA") or 0)
    cb = float(ta.get("coeffB") or 0)
    cc = float(ta.get("coeffC") or 0)

    decoded = {
        "Traction Unit ID / Code": f"#{tu['entity_id']} — {tu['name']}",
        "Description": ta.get("desc") or "—",
        "Traction Classification": f"{ttype} (Type {ta.get('type')}) • Propulsion: {mtype}",
        "Unit Length & Mass": f"{ta.get('length', 0)} m • {ta.get('weight', 0)} tonnes (Rotational Mass Factor: {ta.get('enginemassfactor', '1.0')})",
        "Maximum Allowed Speed": f"{vmax_kmh:.0f} km/h ({vmax_mph} mph)",
        "Default Differential Speed Profile": f"{prof_desc} (ID {ta.get('defveloprof', '0')})",
        "Default Braking Retardation": f"{ta.get('defretardation', '0')} m/s²",
        "Davis Running Resistance R(v)": f"R(v) = {ca} + {cb}·v + {cc}·v² N (v in km/h)",
        "Starting Tractive Effort": f"{parsed['max_force_kn']} kN",
        "Peak Wheel-Rim Power": f"{parsed['max_power_kw']} kW ({round(parsed['max_power_kw'] * 1.34102):,} bhp)",
        "Tractive Effort Diagrams": f"{len(parsed['fs_diagrams'])} F-v curve(s) • {len(parsed['sr_curve'])} resistance point(s)",
        "Timing Loads Using This Unit": f"{len(linked_trains)} <modeltrain> timing load(s)",
        "Last Modified": ta.get("lastmodified") or "—",
    }

    return {
        "asset_type": "tractionunit",
        "id": int(tu["entity_id"]) if str(tu["entity_id"]).isdigit() else tu["entity_id"],
        "title": f"Traction Unit {tu['name']} — {ta.get('desc') or ''}",
        "subtitle": f"{ttype} ({mtype}) • {ta.get('length', 0)}m / {ta.get('weight', 0)}t • {parsed['max_force_kn']} kN / {parsed['max_power_kw']} kW",
        "x": None,
        "y": None,
        "decoded": decoded,
        "related": {
            "traction_units": [
                {
                    "seq": 1,
                    "id": int(tu["entity_id"]) if str(tu["entity_id"]).isdigit() else tu["entity_id"],
                    "name": tu["name"],
                    "desc": ta.get("desc") or "",
                    "type_code": ttype,
                    "motor_desc": mtype,
                    "length_m": float(ta.get("length") or 0),
                    "weight_t": float(ta.get("weight") or 0),
                    "max_speed_kmh": vmax_kmh,
                    "max_speed_mph": vmax_mph,
                    "retardation": float(ta.get("defretardation") or 0),
                    "engine_mass_factor": float(ta.get("enginemassfactor") or 1.0),
                    "coeffA": ca,
                    "coeffB": cb,
                    "coeffC": cc,
                    "fs_diagrams": parsed["fs_diagrams"],
                    "sr_curve": parsed["sr_curve"],
                    "max_force_kn": parsed["max_force_kn"],
                    "max_power_kw": parsed["max_power_kw"],
                }
            ],
            "model_trains": linked_trains,
        },
        "highlight": {"ways": [], "points": []},
        "raw_attrs": ta,
        "raw_children": tc,
    }



def _get_stationbank_asset(
    cur: Any,
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int],
    saved_override_fid: Optional[int],
    lookup: dict[str, Any],
) -> dict[str, Any]:
    """Inspect a TPS station bank and its associated stations/tracks."""
    _ = (asset_type, nwr_fid, saved_override_fid, lookup)
    cur.execute(
        "SELECT * FROM raw_tps_elements WHERE tag = 'stationbank' AND entity_id = %s LIMIT 1",
        (str(asset_id),),
    )
    sb = cur.fetchone()
    if not sb:
        # Fallback if section_id was passed directly
        cur.execute(
            "SELECT * FROM raw_tps_elements WHERE tag = 'stationbanksection' AND entity_id = %s LIMIT 1",
            (str(asset_id),),
        )
        sb = cur.fetchone()
        if not sb:
            raise HTTPException(status_code=404, detail="Station bank corridor not found")
        sec_ids = [str(sb["entity_id"])]
    else:
        sec_ids = [
            str(c.get("attrs", {}).get("stationbankid"))
            for c in (sb["raw_children"] or [])
            if c.get("tag") == "stationbankid" and c.get("attrs", {}).get("stationbankid")
        ]

    sections_rows = []
    if sec_ids:
        cur.execute(
            "SELECT entity_id, name, raw_attrs, raw_children FROM raw_tps_elements WHERE tag = 'stationbanksection' AND entity_id = ANY(%s)",
            (sec_ids,),
        )
        sections_rows = cur.fetchall()

    all_st_ids = set()
    for srow in sections_rows:
        for ch in srow["raw_children"] or []:
            if ch.get("tag") == "stationbankstation":
                sid = ch.get("attrs", {}).get("id")
                if sid and str(sid).isdigit():
                    all_st_ids.add(int(sid))

    st_map = {}
    if all_st_ids:
        cur.execute(
            """
            SELECT station_id, abbrev, longname, crscode, stanox,
                   station_type, station_category, x, y, km_region_id, km_value
            FROM stations
            WHERE station_id = ANY(%s)
            """,
            (list(all_st_ids),),
        )
        for r in cur.fetchall():
            st_map[r["station_id"]] = r

    sections_enriched = []
    highlight_ways = []
    highlight_points = []
    xs, ys = [], []

    for srow in sections_rows:
        sa = srow["raw_attrs"] or {}
        way_coords = []
        stops = []
        for ch in srow["raw_children"] or []:
            if ch.get("tag") != "stationbankstation":
                continue
            ca = ch.get("attrs", {})
            sid = int(ca["id"]) if str(ca.get("id", "")).isdigit() else 0
            st_rec = st_map.get(sid)
            layouts = []
            for lch in ch.get("children") or []:
                if lch.get("tag") == "stationbankstationlayout":
                    lid = lch.get("attrs", {}).get("stationlayoutcodeid")
                    ldesc = decode_code("stationlayoutcode", lid) or f"Layout {lid}"
                    layouts.append(ldesc)
            if st_rec:
                stype_desc = decode_code("stationtypedesc", st_rec["station_type"]) or "—"
                scat_desc = decode_code("stationcategorydesc", st_rec["station_category"]) or "—"
                elr = decode_code("kmregionmasterdesc", st_rec["km_region_id"])
                if st_rec["x"] is not None and st_rec["y"] is not None:
                    way_coords.append([st_rec["x"], st_rec["y"]])
                    xs.append(st_rec["x"])
                    ys.append(st_rec["y"])
                    is_mand = st_rec["station_type"] == 2
                    highlight_points.append({
                        "x": st_rec["x"],
                        "y": st_rec["y"],
                        "label": f"{ int(ca.get('seq', 0)) + 1 }. {st_rec['longname'] or st_rec['abbrev']} ({st_rec['abbrev']})",
                        "color": "#f43f5e" if is_mand else "#38bdf8",
                        "shape": "diamond" if is_mand else "circle",
                    })
                stops.append({
                    "seq": int(ca.get("seq") or 0) + 1,
                    "station_id": sid,
                    "abbrev": st_rec["abbrev"],
                    "longname": st_rec["longname"] or st_rec["abbrev"],
                    "crscode": st_rec["crscode"],
                    "stanox": st_rec["stanox"],
                    "timing_role": stype_desc,
                    "planning_category": scat_desc,
                    "elr": elr,
                    "km_str": meters_to_miles_chains(st_rec["km_value"]),
                    "layouts": layouts,
                    "x": st_rec["x"],
                    "y": st_rec["y"],
                })
        if len(way_coords) >= 2:
            highlight_ways.append({
                "label": f"{srow['name']} ({sa.get('description') or ''})",
                "coords": way_coords,
                "color": "#a855f7",
                "width": 4.5,
            })
        sections_enriched.append({
            "section_id": srow["entity_id"],
            "name": srow["name"],
            "description": sa.get("description") or "",
            "stops": stops,
        })

    sb_attrs = sb["raw_attrs"] or {}
    total_stops = sum(len(s["stops"]) for s in sections_enriched)
    cx = sum(xs) / len(xs) if xs else None
    cy = sum(ys) / len(ys) if ys else None
    bbox = [min(xs), min(ys), max(xs), max(ys)] if xs else None

    decoded = {
        "Station Bank ID / Code": f"#{sb['entity_id']} — {sb['name']}",
        "Timetable Corridor Description": sb_attrs.get("description") or "—",
        "Corridor Sections": len(sections_enriched),
        "Ordered Timing Points (TIPLOCs)": total_stops,
        "Origin → Destination": (
            f"{sections_enriched[0]['stops'][0]['longname']} ({sections_enriched[0]['stops'][0]['abbrev']}) → "
            f"{sections_enriched[-1]['stops'][-1]['longname']} ({sections_enriched[-1]['stops'][-1]['abbrev']})"
            if sections_enriched and sections_enriched[0]["stops"] and sections_enriched[-1]["stops"]
            else "—"
        ),
        "Last Modified": sb_attrs.get("lastmodified") or "—",
    }

    return {
        "asset_type": "stationbank",
        "id": int(sb["entity_id"]) if str(sb["entity_id"]).isdigit() else sb["entity_id"],
        "title": f"Timetable Corridor {sb['name']} — {sb_attrs.get('description') or ''}",
        "subtitle": f"Station Bank #{sb['entity_id']} • {total_stops} Ordered Timing Points",
        "x": cx,
        "y": cy,
        "bbox": bbox,
        "decoded": decoded,
        "related": {
            "corridor_sections": sections_enriched,
        },
        "highlight": {
            "ways": highlight_ways,
            "points": highlight_points,
        },
        "raw_attrs": sb_attrs,
        "raw_children": sb["raw_children"],
    }
