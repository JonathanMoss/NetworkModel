"""E2E tests for backend/routers/possessions.py (Possessions & Worksites CRUD + ELR tracks)."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient
import pytest

import main
from routers import possessions as poss_router
from tests.conftest import FakeConnection, FakeCursor

pytestmark = pytest.mark.e2e


@pytest.fixture
def client(monkeypatch):
    """Return a TestClient with background ingestion disabled and geometry resolver stubbed."""
    monkeypatch.setattr(main, "start_background_ingestion", lambda: None)
    monkeypatch.setattr(poss_router, "ensure_possessions_tables", lambda _cur: None)
    monkeypatch.setattr(
        poss_router,
        "resolve_possession_geometries",
        lambda _cur, _data: (
            {"ways": [{"coords": [[0, 0], [10, 0]]}], "markers": [], "bbox": [0, 0, 10, 0]},
            {"ways": [{"coords": [[500000, 180000], [501000, 180000]]}], "markers": [], "bbox": [500000, 180000, 501000, 180000]},
        ),
    )
    monkeypatch.setattr(
        poss_router,
        "resolve_worksite_geometries",
        lambda _cur, _parent, _data: (
            {"ways": [], "markers": [], "bbox": None},
            {"ways": [], "markers": [], "bbox": None},
        ),
    )
    with TestClient(main.app) as tc:
        yield tc


def test_possessions_crud_and_worksites_e2e(client, monkeypatch):
    """Covers list, create, update (200/404), delete (200/404), worksites, and elr-tracks."""
    now = datetime(2026, 11, 27, 14, 0, tzinfo=timezone.utc)
    sample_poss = {
        "id": 1,
        "won_ref": "WON Wk34",
        "title": "Slough Up Main Renewal",
        "picop_name": "A. Smith",
        "status": "Planned",
        "ole_isolation": "Earthed",
        "limit_mode": "signal_points",
        "tps_geometry": {"ways": [], "markers": [], "bbox": None},
        "nwr_geometry": {"ways": [], "markers": [], "bbox": None},
        "created_at": now,
        "updated_at": now,
    }
    sample_ws = {
        "id": 10,
        "possession_id": 1,
        "title": "WS-1 Tamping",
        "es_name": "B. Jones",
        "created_at": now,
    }

    # GET /api/possessions
    monkeypatch.setattr(
        poss_router,
        "get_conn",
        lambda: FakeConnection(FakeCursor(responses=[[sample_poss], [sample_ws]])),
    )
    r_list = client.get("/api/possessions")
    assert r_list.status_code == 200
    assert len(r_list.json()["possessions"]) == 1
    assert len(r_list.json()["possessions"][0]["worksites"]) == 1

    # POST /api/possessions
    monkeypatch.setattr(
        poss_router,
        "get_conn",
        lambda: FakeConnection(FakeCursor(responses=[[sample_poss]])),
    )
    r_create = client.post(
        "/api/possessions",
        json={
            "title": "Slough Up Main Renewal",
            "won_ref": "WON Wk34",
            "picop_name": "A. Smith",
            "lines": [
                {
                    "line_name": "Platform 1/2 & Up Main",
                    "limit_mode": "signal_points",
                    "from_limits": [
                        {"asset_type": "node", "asset_id": 1, "label": "Plt 1 Buffers", "qualifier": "buffer"},
                        {"asset_type": "node", "asset_id": 2, "label": "Plt 2 Buffers", "qualifier": "buffer"},
                    ],
                    "to_limits": [
                        {"asset_type": "signal", "asset_id": 3, "label": "T200", "qualifier": "at"},
                    ],
                },
                {"line_name": "Down Slow", "limit_mode": "elr_mileage", "elr": "MLN1", "start_meters": 28000, "end_meters": 32000},
            ],
        },
    )
    assert r_create.status_code == 200
    assert r_create.json()["id"] == 1

    # PUT /api/possessions/{id} (200 + 404)
    monkeypatch.setattr(
        poss_router,
        "get_conn",
        lambda: FakeConnection(FakeCursor(responses=[[sample_poss], [sample_ws]])),
    )
    r_upd = client.put("/api/possessions/1", json={"title": "Slough Updated", "status": "Active"})
    assert r_upd.status_code == 200
    monkeypatch.setattr(poss_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.put("/api/possessions/999", json={"title": "Missing"}).status_code == 404

    # POST & DELETE worksites (200 + 404)
    monkeypatch.setattr(
        poss_router,
        "get_conn",
        lambda: FakeConnection(FakeCursor(responses=[[sample_poss], [sample_ws]])),
    )
    r_ws = client.post("/api/possessions/1/worksites", json={"title": "WS-1 Tamping", "es_name": "B. Jones"})
    assert r_ws.status_code == 200
    assert r_ws.json()["id"] == 10

    monkeypatch.setattr(poss_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.post("/api/possessions/999/worksites", json={"title": "WS"}).status_code == 404

    monkeypatch.setattr(poss_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[{"id": 10}]])))
    assert client.delete("/api/possessions/1/worksites/10").status_code == 200
    monkeypatch.setattr(poss_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.delete("/api/possessions/1/worksites/999").status_code == 404

    # DELETE /api/possessions/{id} (200 + 404)
    monkeypatch.setattr(poss_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[{"id": 1}]])))
    assert client.delete("/api/possessions/1").status_code == 200
    monkeypatch.setattr(poss_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.delete("/api/possessions/999").status_code == 404

    # GET /api/possessions/elr-tracks
    monkeypatch.setattr(
        poss_router,
        "get_conn",
        lambda: FakeConnection(
            FakeCursor(responses=[[{"track_id": "1100", "min_meters": 0, "max_meters": 50000, "segment_count": 4}]])
        ),
    )
    r_trks = client.get("/api/possessions/elr-tracks?elr=MLN1")
    assert r_trks.status_code == 200
    assert r_trks.json()["elr"] == "MLN1"
    assert r_trks.json()["tracks"][0]["track_id"] == "1100"

