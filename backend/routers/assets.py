"""Detailed TPS asset inspection router (``/api/asset/{asset_type}/{asset_id}``).

Dispatches the 15 TPS asset inspection branches to dedicated domain handler
modules under :mod:`routers.asset_handlers`:
  - :mod:`routers.asset_handlers.topology`: stations, edges, nodes
  - :mod:`routers.asset_handlers.signalling`: signals, points, overlaps
  - :mod:`routers.asset_handlers.blocks_routes`: tracks, blocks, routes, attribute sections
  - :mod:`routers.asset_handlers.graphics_timing`: graphics, model trains, traction units, station banks
"""

from typing import Any, Callable, Optional

from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row

from config import get_conn, get_saved_track_override, load_lookup_cache
from routers.asset_handlers.blocks_routes import (
    _get_attributesection_asset,
    _get_block_asset,
    _get_route_asset,
    _get_track_asset,
)
from routers.asset_handlers.geo import get_asset_geo_point
from routers.asset_handlers.graphics_timing import (
    _get_annotation_asset,
    _get_graphictext_asset,
    _get_graphicvector_asset,
    _get_modeltrain_asset,
    _get_polyarea_asset,
    _get_stationbank_asset,
    _get_tractionunit_asset,
)
from routers.asset_handlers.signalling import _get_signal_or_points_asset
from routers.asset_handlers.topology import (
    _get_edge_asset,
    _get_node_asset,
    _get_station_asset,
)

router = APIRouter(tags=["assets"])

_ASSET_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "station": _get_station_asset,
    "edge": _get_edge_asset,
    "node": _get_node_asset,
    "signal": _get_signal_or_points_asset,
    "points": _get_signal_or_points_asset,
    "track": _get_track_asset,
    "block": _get_block_asset,
    "route": _get_route_asset,
    "attributesection": _get_attributesection_asset,
    "graphictext": _get_graphictext_asset,
    "graphicvector": _get_graphicvector_asset,
    "annotation": _get_annotation_asset,
    "polyarea": _get_polyarea_asset,
    "modeltrain": _get_modeltrain_asset,
    "tractionunit": _get_tractionunit_asset,
    "stationbank": _get_stationbank_asset,
}


@router.get("/api/asset/{asset_type}/{asset_id}")
def api_get_asset(
    asset_type: str,
    asset_id: int,
    nwr_fid: Optional[int] = Query(default=None),
) -> dict[str, Any]:
    """Inspect any TPS asset by type and ID, including NWR geographic cross-links.

    Args:
        asset_type: Canonical TPS asset type (e.g. ``"signal"``, ``"points"``,
            ``"edge"``, ``"node"``, ``"station"``, ``"route"``).
        asset_id: Database identifier (or ``pk``) of the asset.
        nwr_fid: Optional transient manual NWR centreline feature ID override.

    Returns:
        Structured inspection payload for the right-hand detail panel.

    Raises:
        HTTPException: 400 if ``asset_type`` is unknown, or 404 if not found.
    """
    handler = _ASSET_HANDLERS.get(asset_type)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"Unknown asset type: {asset_type}")

    lookup = load_lookup_cache()
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            saved_override_fid = get_saved_track_override(cur, asset_type, asset_id)
            return handler(cur, asset_type, asset_id, nwr_fid, saved_override_fid, lookup)


@router.get("/api/asset/{asset_type}/{asset_id}/geo")
def api_get_asset_geo(asset_type: str, asset_id: int) -> dict[str, Any]:
    """Return lightweight OSGB36 coordinates for a TPS signal, points, or node endpoint."""
    with get_conn() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            saved_override_fid = get_saved_track_override(cur, asset_type, asset_id)
            return get_asset_geo_point(cur, asset_type, asset_id, saved_override_fid)
