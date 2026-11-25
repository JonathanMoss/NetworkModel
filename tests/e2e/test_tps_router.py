"""Comprehensive E2E tests for backend/routers/tps.py (all 9 endpoints)."""

from fastapi.testclient import TestClient
import pytest

import main
from routers import tps as tps_router
from tests.conftest import FakeConnection, FakeCursor

pytestmark = pytest.mark.e2e


@pytest.fixture
def client(monkeypatch):
    """Return a TestClient with background ingestion disabled."""
    monkeypatch.setattr(main, "start_background_ingestion", lambda: None)
    with TestClient(main.app) as tc:
        yield tc


def _sample_timing_catalog():
    """Return a realistic timing catalog fixture for search and timing-loads endpoints."""
    return {
        "counts": {
            "model_trains": 1,
            "traction_units": 1,
            "coaches": 1,
            "velocity_profiles": 1,
            "train_types": 1,
            "station_banks": 1,
        },
        "model_trains": [
            {
                "id": 301,
                "name": "IET9",
                "publicname": "Class 800 9-car",
                "desc": "Express Bi-mode",
                "traction_summary": "Class 800",
                "profile_id": "7",
                "profile_name": "HST125",
                "maxspeed_kmh": 200.0,
                "maxspeed_mph": 124.3,
            }
        ],
        "traction_units": [
            {
                "id": 101,
                "name": "Class 800",
                "desc": "Bi-mode Traction",
                "type_code": "EMU",
                "motor_desc": "AC Electric",
                "length_m": 260.0,
                "weight_t": 400.0,
                "max_speed_kmh": 200.0,
            }
        ],
        "coaches": [{"id": 201, "name": "Mk4", "desc": "Trailer Coach"}],
        "velocity_profiles": [{"id": "7", "code": "HST", "description": "High Speed"}],
        "train_types": [{"id": "2", "code": "EXP", "description": "Express"}],
        "station_banks": [{"id": 401, "code": "PADB", "description": "Paddington Corridor"}],
        "timing_roles": [{"type_id": 1, "label": "Main", "count": 50}],
        "planning_categories": [{"category_id": 1, "label": "Primary", "count": 20}],
        "station_to_banks": {"10": [{"code": "PADB"}]},
    }


def test_api_overview_503_and_200_cached(client, monkeypatch):
    """GET /api/overview returns 503 while ingesting, then builds and caches overview."""
    monkeypatch.setitem(tps_router.INGEST_STATE, "status", "ingesting")
    monkeypatch.setattr(tps_router, "check_already_ingested", lambda _dsn: False)
    assert client.get("/api/overview").status_code == 503

    monkeypatch.setitem(tps_router.INGEST_STATE, "status", "ready")
    monkeypatch.setattr(
        tps_router,
        "load_lookup_cache",
        lambda: {
            "stationcategorydesc": {"1": {"display": "Major"}},
            "stationtypedesc": {"2": {"display": "Junction"}},
            "trackcategorydesc": {"3": {"display": "Main"}},
            "veloprofiledesc": {"7": {"display": "HST"}},
        },
    )
    monkeypatch.setattr(tps_router, "decode_code", lambda _cat, cid: "MLN1" if cid == 10 else None)

    cur = FakeCursor(
        responses=[
            [(100.0, 500.0, -200.0, 300.0, 1000)],
            [(1, "PAD", "Paddington", "PAD", "73000", 2, 1, 100.0, 200.0, 90.0, 110.0, 190.0, 210.0, 14, 14, 10, 0)],
            [(100.0, 200.0, 120.0, 220.0, 200, True, 0.5)],
            [(1, 10, "Area1", "Desc1", [[0, 0], [10, 10]], 0, 10, 0, 10)],
            [(1, 50.0, 60.0, "Note", "2025", "2026", "2026-01-01")],
            [(100, 200, 50, 25, 10, 40, 30, 20, 5, 60, 70, 80, 90)],
        ]
    )
    calls = {"n": 0}

    def fake_conn():
        calls["n"] += 1
        return FakeConnection(cur)

    monkeypatch.setattr(tps_router, "get_conn", fake_conn)

    r1 = client.get("/api/overview")
    assert r1.status_code == 200
    body = r1.json()
    assert body["bounds"]["node_count"] == 1000
    assert body["stations"][0]["elr"] == "MLN1"
    assert len(body["macro_edges"]) == 1
    assert len(body["poly_areas"]) == 1
    assert len(body["annotations"]) == 1

    # Second call served from OVERVIEW_CACHE
    r2 = client.get("/api/overview")
    assert r2.status_code == 200
    assert calls["n"] == 1


def test_api_viewport_all_layers(client, monkeypatch):
    """GET /api/viewport queries all 7 optional schematic layers when enabled."""
    cur = FakeCursor(
        responses=[
            [[1, 101, 10, 20, 0, 0, 100, 0, 160, 1, 0, 500, 2]],
            [[2, 201, 1, "P1", "Plat 1", 1, 250, [[0, 0], [50, 0]]]],
            [[3, 0, 0, 10, 10, [1]]],
            [[4, 5, 5, 12, "SLOUGH", 0]],
            [[5, 501, 501, "T3533", 1, 10, 10, 12, 10, 0, 10, 20, 1]],
            [[10, 0, 0, 0, 1, 1000]],
            [[6, 601, 100, 200, 0, 1, 0, [[0, 0], [100, 0]]]],
        ]
    )
    monkeypatch.setattr(tps_router, "get_conn", lambda: FakeConnection(cur))
    resp = client.get(
        "/api/viewport?min_x=0&max_x=200&min_y=0&max_y=200"
        "&include_nodes=true&include_blocks=true&include_vp=true&include_overlaps=true"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["edges"]) == 1
    assert len(data["tracks"]) == 1
    assert len(data["vectors"]) == 1
    assert len(data["texts"]) == 1
    assert len(data["signals"]) == 1
    assert len(data["nodes"]) == 1
    assert len(data["blocks"]) == 1


def test_api_search_all_entity_types(client, monkeypatch):
    """GET /api/search aggregates stations, signals, points, attr sections, blocks, routes, nodes, and timing loads."""
    monkeypatch.setattr(tps_router, "get_timing_catalog", _sample_timing_catalog)
    monkeypatch.setattr(tps_router, "decode_code", lambda _cat, _cid: "MLN1")

    cur = FakeCursor(
        responses=[
            [{"station_id": 1, "abbrev": "PAD", "longname": "Paddington", "crscode": "PAD", "stanox": "73000", "nalco": "1", "x": 10.0, "y": 20.0}],
            [
                {"pk": 11, "id": 11, "signal_id": 100, "name": "T3533", "master_name": "T3533", "interlocking_sys_id": 1, "x": 10.0, "y": 20.0},
                {"pk": 12, "id": 12, "signal_id": 101, "name": "402A", "master_name": "POINTS 402A", "interlocking_sys_id": 15, "x": 11.0, "y": 21.0},
            ],
            [{"pk": 21, "id": 21, "short_description": "MLN1", "description": "GWML", "min_x": 0.0, "max_x": 100.0, "min_y": 0.0, "max_y": 100.0}],
            [{"pk": 31, "block_id": 800, "min_x": 0.0, "max_x": 50.0, "min_y": 0.0, "max_y": 50.0, "pre_signal_distance": 120}],
            [{"pk": 41, "route_id": 800, "station_id": 1, "min_x": 0.0, "max_x": 50.0, "min_y": 0.0, "max_y": 50.0}],
            [{"node_id": 800, "netx": 15.0, "nety": 25.0, "km_region_id": 10, "km_value": 1000}],
        ]
    )
    monkeypatch.setattr(tps_router, "get_conn", lambda: FakeConnection(cur))

    resp = client.get("/api/search?q=800")
    assert resp.status_code == 200
    types = {r["type"] for r in resp.json()["results"]}
    assert {"station", "signal", "points", "attributesection", "block", "route", "node", "modeltrain", "tractionunit"} <= types

    # Also test text search matching station_banks
    cur_txt = FakeCursor(responses=[[], [], []])
    monkeypatch.setattr(tps_router, "get_conn", lambda: FakeConnection(cur_txt))
    resp_bank = client.get("/api/search?q=PADB")
    assert resp_bank.status_code == 200
    assert resp_bank.json()["results"][0]["type"] == "stationbank"


def test_api_pick_all_candidate_types(client, monkeypatch):
    """GET /api/pick returns sorted candidates across signals, points, nodes, edges, stations, tracks, blocks, routes, and attr sections."""
    cur = FakeCursor(
        responses=[
            # signals & points
            [
                {"pk": 1, "id": 1, "signal_id": 10, "name": "T3533", "master_name": "T3533", "interlocking_sys_id": 1, "bumper": 0, "x": 10.0, "y": 10.0, "dist": 1.0},
                {"pk": 2, "id": 2, "signal_id": 11, "name": "POINT 5", "master_name": "POINT 5", "interlocking_sys_id": 15, "bumper": 0, "x": 12.0, "y": 10.0, "dist": 2.0},
            ],
            # nodes
            [{"node_id": 100, "netx": 10.0, "nety": 10.0, "km_region_id": 1, "km_value": 1000, "dist": 1.5}],
            # degree for node 100
            [{"deg": 3}],
            # edges
            [
                {"pk": 20, "edge_id": 200, "start_node_id": 100, "end_node_id": 101, "x0": 0.0, "y0": 10.0, "x1": 20.0, "y1": 10.0, "vmax": 160, "length": 100, "electrified": True},
                {"pk": 21, "edge_id": 201, "start_node_id": 100, "end_node_id": 100, "x0": 10.0, "y0": 10.0, "x1": 10.0, "y1": 10.0, "vmax": 80, "length": 0, "electrified": False},
            ],
            # stations
            [{"station_id": 1, "abbrev": "SLO", "longname": "Slough", "crscode": "SLO", "x": 15.0, "y": 15.0, "dist": 7.0}],
            # station_tracks
            [
                {"pk": 30, "track_id": 300, "name": "P1", "description": "Plat 1", "station_name": "Slough", "coords": [[0.0, 10.0], [20.0, 10.0]]},
                {"pk": 31, "track_id": 301, "name": "P2", "description": "Zero", "station_name": "Slough", "coords": [[10.0, 10.0], [10.0, 10.0]]},
            ],
            # blocks
            [{"pk": 40, "block_id": 400, "pre_signal_distance": 100, "sight_distance": 200, "coords": [[0.0, 10.0], [20.0, 10.0]]}],
            # routes
            [{"pk": 50, "route_id": 500, "station_name": "Slough", "coords": [[0.0, 10.0], [20.0, 10.0]]}],
            # attribute_sections
            [{"pk": 60, "id": 600, "short_description": "MLN1", "description": "GWML", "coords": [[0.0, 10.0], [20.0, 10.0]]}],
        ]
    )
    monkeypatch.setattr(tps_router, "get_conn", lambda: FakeConnection(cur))
    resp = client.get("/api/pick?x=10&y=10&radius=50")
    assert resp.status_code == 200
    cands = resp.json()["candidates"]
    types = {c["type"] for c in cands}
    assert {"signal", "points", "node", "edge", "station", "track", "block", "route", "attributesection"} <= types
    # Blocks and routes are sorted ahead of other types
    assert cands[0]["type"] in ("block", "route")

    # Also test fallback when no nodes are near (falls back to nearby edges' node IDs)
    cur_no_nodes = FakeCursor(
        responses=[
            [],  # signals
            [],  # nodes
            [],  # edges
            [],  # stations
            [],  # tracks
            [{"start_node_id": 100, "end_node_id": 101}],  # fallback edges
            [],  # blocks
            [],  # routes
            [],  # attribute_sections
        ]
    )
    monkeypatch.setattr(tps_router, "get_conn", lambda: FakeConnection(cur_no_nodes))
    assert client.get("/api/pick?x=10&y=10&radius=20").status_code == 200


def test_timing_loads_and_catalog_endpoints(client, monkeypatch):
    """Tests /api/timing-loads/catalog, /api/timing-loads/list (all tabs + 400), /api/catalog, and /api/catalog/{tag}."""
    monkeypatch.setattr(tps_router, "get_timing_catalog", _sample_timing_catalog)

    r_cat = client.get("/api/timing-loads/catalog")
    assert r_cat.status_code == 200
    assert "station_to_banks" not in r_cat.json()

    assert client.get("/api/timing-loads/list?tab=model_trains&q=profile:7").json()["total"] == 1
    assert client.get("/api/timing-loads/list?tab=model_trains&q=IET").json()["total"] == 1
    assert client.get("/api/timing-loads/list?tab=traction_units&q=800").json()["total"] == 1
    assert client.get("/api/timing-loads/list?tab=profiles_coaches&q=HST").status_code == 200
    assert client.get("/api/timing-loads/list?tab=station_banks&q=PAD").json()["total"] == 1
    assert client.get("/api/timing-loads/list?tab=bogus").status_code == 400

    # /api/catalog
    cur_meta = FakeCursor(
        responses=[
            [({"tag_counts": {"station": 2500, "signal": 40000}},)],
            [({"counts": {"nwr_elrs": 1100}},)],
        ]
    )
    monkeypatch.setattr(tps_router, "get_conn", lambda: FakeConnection(cur_meta))
    r_tags = client.get("/api/catalog")
    assert r_tags.status_code == 200
    assert r_tags.json()["total_tags"] == 3

    # /api/catalog/{tag} for dedicated table (filtered & unfiltered) and raw_tps_elements (filtered & unfiltered)
    for url in [
        "/api/catalog/station?q=PAD",
        "/api/catalog/station",
        "/api/catalog/customtag?q=foo",
        "/api/catalog/customtag",
    ]:
        cur_rows = FakeCursor(responses=[[{"entity_id": "1", "name": "Row1", "raw_attrs": {}, "raw_children": []}]])
        monkeypatch.setattr(tps_router, "get_conn", lambda: FakeConnection(cur_rows))
        resp = client.get(url)
        assert resp.status_code == 200
        assert len(resp.json()["rows"]) == 1

