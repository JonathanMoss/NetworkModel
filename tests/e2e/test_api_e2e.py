"""End-to-end API tests using FastAPI TestClient across all application routers."""

from fastapi.testclient import TestClient
import pytest

import main
from routers import assets as assets_router
from routers import network_fusion as nf_router
from routers import nwr as nwr_router
from routers import tps as tps_router
from tests.conftest import FakeConnection, FakeCursor

pytestmark = pytest.mark.e2e


@pytest.fixture
def client(monkeypatch):
    """Return a TestClient with background ingestion disabled during test startup."""
    monkeypatch.setattr(main, "start_background_ingestion", lambda: None)
    with TestClient(main.app) as test_client:
        yield test_client


def test_root_index_serves_html(client):
    """GET / serves the frontend index.html file."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_start_background_ingestion_spawns_worker(monkeypatch):
    """start_background_ingestion runs ingestion & cache warm-up on a daemon thread."""
    called = []
    monkeypatch.setattr(main, "check_already_ingested", lambda _dsn: False)
    monkeypatch.setattr(main, "run_ingestion", lambda *_a: called.append("tps"))
    monkeypatch.setattr(main, "ensure_nwr_tables_and_ingest", lambda *_a: called.append("nwr"))
    monkeypatch.setattr(
        main, "ensure_network_fusion_tables_and_ingest", lambda *_a: called.append("nf")
    )
    monkeypatch.setattr(main, "ensure_performance_indexes", lambda: called.append("indexes"))
    monkeypatch.setattr(main, "load_lookup_cache", lambda: called.append("lookup"))
    monkeypatch.setattr(main, "get_timing_catalog", lambda: called.append("timing"))
    monkeypatch.setattr(main, "api_overview", lambda: called.append("overview"))
    monkeypatch.setattr(main, "api_nwr_overview", lambda: called.append("nwr_overview"))

    threads = []

    class ImmediateThread:
        """Test double executing target synchronously."""

        def __init__(self, target, daemon=True):
            self._target = target
            self.daemon = daemon

        def start(self):
            threads.append(self)
            self._target()

    monkeypatch.setattr(main.threading, "Thread", ImmediateThread)
    main.start_background_ingestion()
    assert len(threads) == 1
    assert called == ["tps", "nwr", "nf", "indexes", "lookup", "timing", "overview", "nwr_overview"]


def test_api_status_pending_and_ready(client, monkeypatch):
    """GET /api/status reports pending state before ingestion and project info once ready."""
    monkeypatch.setattr(tps_router, "check_already_ingested", lambda _dsn: False)
    monkeypatch.setitem(tps_router.INGEST_STATE, "status", "ingesting")
    resp_pending = client.get("/api/status")
    assert resp_pending.status_code == 200
    assert resp_pending.json()["ingest"]["status"] == "ingesting"

    monkeypatch.setitem(tps_router.INGEST_STATE, "status", "ready")
    cur = FakeCursor(
        responses=[
            [{"raw_attrs": {"name": "UK Rail"}, "raw_children": [{"attrs": {"region": "GB"}}]}],
            [{"raw_attrs": {"schema": "2026"}}],
        ]
    )
    monkeypatch.setattr(tps_router, "get_conn", lambda: FakeConnection(cur))
    resp_ready = client.get("/api/status")
    assert resp_ready.status_code == 200
    body = resp_ready.json()
    assert body["ingest"]["status"] == "ready"
    assert body["project"]["name"] == "UK Rail"
    assert body["infra"]["region"] == "GB"
    assert body["version"]["schema"] == "2026"


def test_network_fusion_endpoints_e2e(client, monkeypatch):
    """Exercises /status, /profile, and /nearest on the Network Fusion router."""
    monkeypatch.setattr(nf_router, "ensure_network_fusion_tables_and_ingest", lambda *_a: None)

    # /api/network-fusion/status
    cur_status = FakeCursor(
        responses=[
            [{"value": {"completed": True}}],
            [{"total_points": 1200}],
            [{"total_curv": 340}],
            [{"elrs": 25}],
        ]
    )
    monkeypatch.setattr(nf_router, "get_conn", lambda: FakeConnection(cur_status))
    r_status = client.get("/api/network-fusion/status")
    assert r_status.status_code == 200
    assert r_status.json()["points_count"] == 1200

    # /api/network-fusion/profile
    cur_prof = FakeCursor(
        responses=[
            [{"elevation_m": 30.0, "place_name": "Cardiff", "postcode": "CF10", "bedrock": "Marl"}],
            [
                {
                    "radius_min_m": 600,
                    "radius_avg_m": 750,
                    "radius_avg_class": "Sharp",
                    "mileage_from": "170m 00ch",
                    "mileage_to": "170m 20ch",
                }
            ],
        ]
    )
    monkeypatch.setattr(nf_router, "get_conn", lambda: FakeConnection(cur_prof))
    r_prof = client.get("/api/network-fusion/profile?elr=swm2&start_yards=1000&end_yards=2000")
    assert r_prof.status_code == 200
    assert r_prof.json()["summary"]["min_radius_m"] == 600

    # /api/network-fusion/nearest
    cur_near = FakeCursor(responses=[[{"pk": 7, "elr": "SWM2", "dist_m": 12.5}]])
    monkeypatch.setattr(nf_router, "get_conn", lambda: FakeConnection(cur_near))
    r_near = client.get("/api/network-fusion/nearest?x=318000&y=176000&radius=500")
    assert r_near.status_code == 200
    assert r_near.json()["point"]["elr"] == "SWM2"


def test_nwr_track_override_crud_and_validation(client, monkeypatch):
    """Tests POST validation (400), POST save (200), and DELETE (200) for track overrides."""
    r_bad = client.post("/api/nwr/track-override", json={"asset_type": "signal"})
    assert r_bad.status_code == 400

    cur = FakeCursor(responses=[[], [], []])
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(cur))
    r_save = client.post(
        "/api/nwr/track-override",
        json={"asset_type": "edge", "asset_id": 77, "nwr_fid": 1234},
    )
    assert r_save.status_code == 200
    assert r_save.json()["nwr_fid"] == 1234

    r_del = client.delete("/api/nwr/track-override/edge/77")
    assert r_del.status_code == 200
    assert r_del.json()["ok"] is True


def test_asset_routers_error_handling(client, monkeypatch):
    """Unknown asset types return 400 and missing records return 404."""
    assert client.get("/api/asset/invalid_type/1").status_code == 400

    cur_missing = FakeCursor(responses=[[], []])
    monkeypatch.setattr(assets_router, "load_lookup_cache", lambda: {})
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(cur_missing))
    assert client.get("/api/asset/station/999999").status_code == 404

    cur_nwr_missing = FakeCursor(responses=[[]])
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(cur_nwr_missing))
    assert client.get("/api/nwr/asset/unknown_nwr/1").status_code == 400
    assert client.get("/api/nwr/asset/nwr_centreline/999999").status_code == 404



def test_api_health_and_server_timing_header(client, monkeypatch):
    """GET /api/health reports 200 when DB responds, 503 on error, and /api/* emits Server-Timing."""
    monkeypatch.setattr(main, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[(1,)]])))
    r_ok = client.get("/api/health")
    assert r_ok.status_code == 200
    assert "app;dur=" in r_ok.headers.get("server-timing", "")
    body = r_ok.json()
    assert body["status"] == "ok"
    assert body["db_ok"] is True
    assert "caches" in body

    def boom():
        raise RuntimeError("pg unreachable")

    monkeypatch.setattr(main, "get_conn", boom)
    r_err = client.get("/api/health")
    assert r_err.status_code == 503
    assert r_err.json()["status"] == "degraded"
    assert r_err.json()["db_ok"] is False
