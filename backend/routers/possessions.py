"""FastAPI CRUD router for Engineers' Possessions (PICOP / T3) and Worksites (ES)."""

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from config import ensure_possessions_tables, get_conn
from ingest_nwr import decode_track_id
from possessions import resolve_possession_geometries, resolve_worksite_geometries

router = APIRouter(tags=["possessions"])


class PossessionLimitPoint(BaseModel):
    """A single picked From/To limit endpoint (signal, points, or buffer/node)."""

    asset_type: Optional[str] = None
    asset_id: Optional[int] = None
    label: Optional[str] = None
    qualifier: str = "at"


class PossessionLinePayload(BaseModel):
    """A single blocked line (with freeform designation and one or more From/To limits)."""

    line_name: Optional[str] = None
    limit_mode: str = "signal_points"
    from_limits: list[PossessionLimitPoint] = Field(default_factory=list)
    to_limits: list[PossessionLimitPoint] = Field(default_factory=list)
    start_asset_type: Optional[str] = None
    start_asset_id: Optional[int] = None
    start_label: Optional[str] = None
    start_qualifier: str = "at"
    end_asset_type: Optional[str] = None
    end_asset_id: Optional[int] = None
    end_label: Optional[str] = None
    end_qualifier: str = "at"
    elr: Optional[str] = None
    start_meters: Optional[int] = None
    end_meters: Optional[int] = None
    track_ids: list[str] = Field(default_factory=list)


class PossessionPayload(BaseModel):
    """Request body for creating or updating an Engineers' Possession."""

    won_ref: Optional[str] = None
    title: str = Field(..., min_length=1, max_length=200)
    picop_name: Optional[str] = None
    picop_contact: Optional[str] = None
    start_dt: Optional[str] = None
    end_dt: Optional[str] = None
    lines_blocked: Optional[str] = None
    ole_isolation: str = "N/A"
    status: str = "Planned"
    notes: Optional[str] = None
    color: str = "#f59e0b"
    limit_mode: str = "signal_points"
    start_asset_type: Optional[str] = None
    start_asset_id: Optional[int] = None
    start_label: Optional[str] = None
    start_qualifier: str = "at"
    end_asset_type: Optional[str] = None
    end_asset_id: Optional[int] = None
    end_label: Optional[str] = None
    end_qualifier: str = "at"
    elr: Optional[str] = None
    start_meters: Optional[int] = None
    end_meters: Optional[int] = None
    track_ids: list[str] = Field(default_factory=list)
    lines: list[PossessionLinePayload] = Field(default_factory=list)


class WorksitePayload(BaseModel):
    """Request body for adding an inner Engineering Supervisor (ES) worksite."""

    title: str = Field(..., min_length=1, max_length=200)
    es_name: Optional[str] = None
    wmb_start_label: Optional[str] = None
    wmb_end_label: Optional[str] = None
    start_asset_type: Optional[str] = None
    start_asset_id: Optional[int] = None
    end_asset_type: Optional[str] = None
    end_asset_id: Optional[int] = None
    start_meters: Optional[int] = None
    end_meters: Optional[int] = None
    notes: Optional[str] = None


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert DB timestamp columns to ISO strings for JSON serialization."""
    out = dict(row)
    for k in ("created_at", "updated_at"):
        if out.get(k) is not None and hasattr(out[k], "isoformat"):
            out[k] = out[k].isoformat()
    return out


@router.get("/api/possessions")
def api_list_possessions() -> dict[str, Any]:
    """Return all saved Engineers' Possessions with nested Worksites and geometries."""
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            ensure_possessions_tables(cur)
            cur.execute(
                "SELECT * FROM engineers_possessions ORDER BY updated_at DESC, id DESC"
            )
            possessions = [_serialize_row(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT * FROM possession_worksites ORDER BY id ASC"
            )
            ws_by_pid: dict[int, list[dict[str, Any]]] = {}
            for ws in cur.fetchall():
                pid = int(ws["possession_id"])
                ws_by_pid.setdefault(pid, []).append(_serialize_row(ws))
            for p in possessions:
                p["worksites"] = ws_by_pid.get(int(p["id"]), [])
            conn.commit()
    return {"possessions": possessions}


def _normalize_possession_data(data: dict[str, Any]) -> dict[str, Any]:
    """Sync top-level summary fields from ``lines`` when multi-line entries are provided."""
    lines = data.get("lines") or []
    if lines:
        names = [ln["line_name"].strip() for ln in lines if (ln.get("line_name") or "").strip()]
        if names and not (data.get("lines_blocked") or "").strip():
            data["lines_blocked"] = ", ".join(names)
        first = lines[0]
        from_lims = first.get("from_limits") or []
        to_lims = first.get("to_limits") or []
        if from_lims and not first.get("start_asset_id"):
            first["start_asset_type"] = from_lims[0].get("asset_type")
            first["start_asset_id"] = from_lims[0].get("asset_id")
            first["start_label"] = from_lims[0].get("label")
            first["start_qualifier"] = from_lims[0].get("qualifier") or "at"
        if to_lims and not first.get("end_asset_id"):
            first["end_asset_type"] = to_lims[0].get("asset_type")
            first["end_asset_id"] = to_lims[0].get("asset_id")
            first["end_label"] = to_lims[0].get("label")
            first["end_qualifier"] = to_lims[0].get("qualifier") or "at"
        for key in (
            "limit_mode", "start_asset_type", "start_asset_id", "start_label", "start_qualifier",
            "end_asset_type", "end_asset_id", "end_label", "end_qualifier",
            "elr", "start_meters", "end_meters", "track_ids",
        ):
            data[key] = first.get(key)
    return data


@router.post("/api/possessions")
def api_create_possession(body: PossessionPayload) -> dict[str, Any]:
    """Create a new Engineers' Possession, resolving both TPS and NWR geometries."""
    data = _normalize_possession_data(body.model_dump())
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            ensure_possessions_tables(cur)
            tps_geom, nwr_geom = resolve_possession_geometries(cur, data)
            cur.execute(
                """
                INSERT INTO engineers_possessions (
                    won_ref, title, picop_name, picop_contact, start_dt, end_dt,
                    lines_blocked, ole_isolation, status, notes, color,
                    limit_mode, start_asset_type, start_asset_id, start_label, start_qualifier,
                    end_asset_type, end_asset_id, end_label, end_qualifier,
                    elr, start_meters, end_meters, track_ids, lines, tps_geometry, nwr_geometry
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb
                )
                RETURNING *
                """,
                (
                    data["won_ref"], data["title"], data["picop_name"], data["picop_contact"],
                    data["start_dt"], data["end_dt"], data["lines_blocked"], data["ole_isolation"],
                    data["status"], data["notes"], data["color"], data["limit_mode"],
                    data["start_asset_type"], data["start_asset_id"], data["start_label"], data["start_qualifier"],
                    data["end_asset_type"], data["end_asset_id"], data["end_label"], data["end_qualifier"],
                    data["elr"], data["start_meters"], data["end_meters"],
                    json.dumps(data["track_ids"] or []), json.dumps(data["lines"] or []),
                    json.dumps(tps_geom), json.dumps(nwr_geom),
                ),
            )
            created = _serialize_row(cur.fetchone())
            created["worksites"] = []
            conn.commit()
    return created


@router.put("/api/possessions/{possession_id}")
def api_update_possession(possession_id: int, body: PossessionPayload) -> dict[str, Any]:
    """Update an existing Engineers' Possession and re-resolve its geometries."""
    data = _normalize_possession_data(body.model_dump())
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            ensure_possessions_tables(cur)
            tps_geom, nwr_geom = resolve_possession_geometries(cur, data)
            cur.execute(
                """
                UPDATE engineers_possessions SET
                    won_ref = %s, title = %s, picop_name = %s, picop_contact = %s,
                    start_dt = %s, end_dt = %s, lines_blocked = %s, ole_isolation = %s,
                    status = %s, notes = %s, color = %s, limit_mode = %s,
                    start_asset_type = %s, start_asset_id = %s, start_label = %s, start_qualifier = %s,
                    end_asset_type = %s, end_asset_id = %s, end_label = %s, end_qualifier = %s,
                    elr = %s, start_meters = %s, end_meters = %s,
                    track_ids = %s::jsonb, lines = %s::jsonb, tps_geometry = %s::jsonb, nwr_geometry = %s::jsonb,
                    updated_at = NOW()
                WHERE id = %s
                RETURNING *
                """,
                (
                    data["won_ref"], data["title"], data["picop_name"], data["picop_contact"],
                    data["start_dt"], data["end_dt"], data["lines_blocked"], data["ole_isolation"],
                    data["status"], data["notes"], data["color"], data["limit_mode"],
                    data["start_asset_type"], data["start_asset_id"], data["start_label"], data["start_qualifier"],
                    data["end_asset_type"], data["end_asset_id"], data["end_label"], data["end_qualifier"],
                    data["elr"], data["start_meters"], data["end_meters"],
                    json.dumps(data["track_ids"] or []), json.dumps(data["lines"] or []),
                    json.dumps(tps_geom), json.dumps(nwr_geom),
                    possession_id,
                ),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Possession not found")
            updated = _serialize_row(row)
            cur.execute(
                "SELECT * FROM possession_worksites WHERE possession_id = %s ORDER BY id ASC",
                (possession_id,),
            )
            updated["worksites"] = [_serialize_row(w) for w in cur.fetchall()]
            conn.commit()
    return updated


@router.delete("/api/possessions/{possession_id}")
def api_delete_possession(possession_id: int) -> dict[str, Any]:
    """Delete an Engineers' Possession (cascading its inner worksites)."""
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            ensure_possessions_tables(cur)
            cur.execute(
                "DELETE FROM engineers_possessions WHERE id = %s RETURNING id",
                (possession_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Possession not found")
            conn.commit()
    return {"deleted": True, "id": possession_id}


@router.post("/api/possessions/{possession_id}/worksites")
def api_create_worksite(possession_id: int, body: WorksitePayload) -> dict[str, Any]:
    """Add an inner Engineering Supervisor (ES) worksite to an existing possession."""
    data = body.model_dump()
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            ensure_possessions_tables(cur)
            cur.execute(
                "SELECT * FROM engineers_possessions WHERE id = %s",
                (possession_id,),
            )
            parent = cur.fetchone()
            if not parent:
                raise HTTPException(status_code=404, detail="Possession not found")
            tps_geom, nwr_geom = resolve_worksite_geometries(cur, parent, data)
            cur.execute(
                """
                INSERT INTO possession_worksites (
                    possession_id, title, es_name, wmb_start_label, wmb_end_label,
                    start_asset_type, start_asset_id, end_asset_type, end_asset_id,
                    start_meters, end_meters, notes, tps_geometry, nwr_geometry
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s::jsonb, %s::jsonb
                )
                RETURNING *
                """,
                (
                    possession_id, data["title"], data["es_name"],
                    data["wmb_start_label"], data["wmb_end_label"],
                    data["start_asset_type"], data["start_asset_id"],
                    data["end_asset_type"], data["end_asset_id"],
                    data["start_meters"], data["end_meters"], data["notes"],
                    json.dumps(tps_geom), json.dumps(nwr_geom),
                ),
            )
            created = _serialize_row(cur.fetchone())
            conn.commit()
    return created


@router.delete("/api/possessions/{possession_id}/worksites/{worksite_id}")
def api_delete_worksite(possession_id: int, worksite_id: int) -> dict[str, Any]:
    """Remove an inner worksite from a possession."""
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            ensure_possessions_tables(cur)
            cur.execute(
                "DELETE FROM possession_worksites WHERE id = %s AND possession_id = %s RETURNING id",
                (worksite_id, possession_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Worksite not found")
            conn.commit()
    return {"deleted": True, "id": worksite_id}


@router.get("/api/possessions/elr-tracks")
def api_get_elr_tracks(elr: str = Query(..., min_length=2, max_length=12)) -> dict[str, Any]:
    """Return available NWR track IDs and mileage bounds for an ELR corridor."""
    elr_u = elr.strip().upper()
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT track_id,
                       MIN(start_meters) AS min_meters,
                       MAX(end_meters) AS max_meters,
                       COUNT(*) AS segment_count
                FROM nwr_track_centrelines
                WHERE UPPER(elr) = %s
                GROUP BY track_id
                ORDER BY track_id ASC
                """,
                (elr_u,),
            )
            tracks = []
            for r in cur.fetchall():
                tid = str(r["track_id"] or "")
                tracks.append(
                    {
                        "track_id": tid,
                        "decoded": decode_track_id(tid) if tid else "",
                        "min_meters": int(r["min_meters"] or 0),
                        "max_meters": int(r["max_meters"] or 0),
                        "segment_count": int(r["segment_count"] or 0),
                    }
                )
    return {"elr": elr_u, "tracks": tracks}
