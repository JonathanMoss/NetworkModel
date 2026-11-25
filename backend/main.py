"""FastAPI application entrypoint for the Network Rail TPS & NWR Explorer.

This module wires together the modular backend components:
  - :mod:`config`: database connection, environment paths, lookup cache, and
    manual track-override persistence.
  - :mod:`geometry`: rail mileage formatting, asset-name normalization,
    polyline interpolation/clipping, and traction-curve parsing.
  - :mod:`timing_catalog`: cached timing-load, traction-unit, coach, and
    station-bank catalog.
  - :mod:`track_identity`: 4-stage TPS direction, platform, and polarity
    resolution.
  - :mod:`track_scoring`: NWR Appendix A 4-digit ``track_id`` scoring engine.
  - :mod:`track_matcher`: TPS-to-NWR and NWR-to-TPS cross-linking plus Network
    Fusion corridor aggregation.
  - :mod:`routers`: FastAPI ``APIRouter`` modules for TPS, NWR, Network Fusion,
    and detailed TPS asset inspection.

All core helper functions are also re-exported here so existing CLI scripts
(``test_track_matching.py``, ``batch_audit.py``, ``audit_track_matching.py``)
continue to work without modification.
"""

from contextlib import asynccontextmanager
import os
import threading

from fastapi import FastAPI
from fastapi.responses import FileResponse, ORJSONResponse
from fastapi.staticfiles import StaticFiles

from config import (
    DATABASE_URL,
    NF_DIR,
    NWR_DIR,
    SCHEMA_PATH,
    XML_PATH,
    decode_code,
    ensure_track_overrides_table,
    get_conn,
    get_saved_track_override,
    load_lookup_cache,
)
from geometry import (
    clean_rail_asset_name,
    interpolate_from_end,
    interpolate_pt_along_coords,
    meters_to_miles_chains,
    mile_yards_to_total_yards,
    parse_traction_curves,
    resolve_node_path_details,
    slice_polyline,
)
from ingest import check_already_ingested, run_ingestion
from ingest_network_fusion import ensure_network_fusion_tables_and_ingest
from ingest_nwr import ensure_nwr_tables_and_ingest
from routers.assets import api_get_asset, router as assets_router
from routers.network_fusion import router as network_fusion_router
from routers.nwr import router as nwr_router
from routers.tps import router as tps_router
from timing_catalog import get_timing_catalog
from track_identity import (
    _extract_direction_from_tokens,
    _extract_platform_number,
    _parse_station_track_rows,
    _resolve_reversible_through_line_dir,
    infer_direction_from_km_delta,
    load_elr_polarity_cache,
    resolve_tps_track_identity,
)
from track_matcher import (
    _find_34xx_continuation_tracks,
    _find_valency2_adjacent_elr,
    find_network_fusion_for_elr,
    find_nwr_cross_links_for_tps,
    find_tps_cross_links,
)
from track_scoring import score_track_candidate

__all__ = [
    "DATABASE_URL",
    "NF_DIR",
    "NWR_DIR",
    "SCHEMA_PATH",
    "XML_PATH",
    "api_get_asset",
    "app",
    "clean_rail_asset_name",
    "decode_code",
    "ensure_track_overrides_table",
    "find_network_fusion_for_elr",
    "find_nwr_cross_links_for_tps",
    "find_tps_cross_links",
    "get_conn",
    "get_saved_track_override",
    "get_timing_catalog",
    "infer_direction_from_km_delta",
    "interpolate_from_end",
    "interpolate_pt_along_coords",
    "load_elr_polarity_cache",
    "load_lookup_cache",
    "meters_to_miles_chains",
    "mile_yards_to_total_yards",
    "parse_traction_curves",
    "resolve_node_path_details",
    "resolve_tps_track_identity",
    "score_track_candidate",
    "slice_polyline",
    "start_background_ingestion",
    "_extract_direction_from_tokens",
    "_extract_platform_number",
    "_find_34xx_continuation_tracks",
    "_find_valency2_adjacent_elr",
    "_parse_station_track_rows",
    "_resolve_reversible_through_line_dir",
]


def start_background_ingestion() -> None:
    """Spawn a daemon thread that ensures TPS, NWR, and Network Fusion are ingested.

    Once ingestion finishes, warms the lookup-code and timing-load caches so
    initial UI requests respond with low latency.
    """

    def _worker() -> None:
        if not check_already_ingested(DATABASE_URL):
            run_ingestion(DATABASE_URL, XML_PATH, SCHEMA_PATH)
        ensure_nwr_tables_and_ingest(DATABASE_URL, NWR_DIR)
        ensure_network_fusion_tables_and_ingest(DATABASE_URL, NF_DIR)
        try:
            load_lookup_cache()
            get_timing_catalog()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """FastAPI lifespan hook launching background dataset ingestion on startup."""
    start_background_ingestion()
    yield


app = FastAPI(
    title="Network Rail TPS Schematic & Asset Explorer",
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)

app.include_router(tps_router)
app.include_router(nwr_router)
app.include_router(network_fusion_router)
app.include_router(assets_router)

static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def index() -> FileResponse:
    """Serve the single-page HTML5 Canvas Network Rail Explorer frontend."""
    return FileResponse(os.path.join(static_dir, "index.html"))
