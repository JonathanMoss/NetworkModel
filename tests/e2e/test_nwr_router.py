"""Comprehensive E2E tests for backend/routers/nwr.py (all 7 endpoints)."""

from fastapi.testclient import TestClient
import pytest

import main
from routers import nwr as nwr_router
from tests.conftest import FakeConnection, FakeCursor

pytestmark = pytest.mark.e2e


@pytest.fixture
def client(monkeypatch):
    """Return a TestClient with background ingestion disabled."""
    monkeypatch.setattr(main, "start_background_ingestion", lambda: None)
    with TestClient(main.app) as tc:
        yield tc


def test_nwr_overview_builds_and_caches(client, monkeypatch):
    """GET /api/nwr/overview builds bounds, decimated ELRs, macro links, and fusion stats, then caches."""
    monkeypatch.setattr(nwr_router, "ensure_nwr_tables_and_ingest", lambda *_a: None)
    monkeypatch.setattr(nwr_router, "ensure_network_fusion_tables_and_ingest", lambda *_a: None)

    long_part = [[float(i * 10), float(i * 5)] for i in range(30)]
    short_part = [[0.0, 0.0], [10.0, 10.0]]
    cur = FakeCursor(
        responses=[
            [(100000.0, 600000.0, 50000.0, 900000.0, 50000)],
            [(1, "ELR1", "MLN1", 0.0, 100.0, 400000.0, 500000.0, 170000.0, 190000.0, [short_part, long_part])],
            [
                (10, "MLN1", "1100", "NETWORK RAIL:100%", 400000.0, 170000.0, 401000.0, 170500.0),
                (11, "MLN1", "1200", "NETWORK RAIL:0%, THIRD PARTY:100%", 401000.0, 170500.0, 402000.0, 171000.0),
                (12, "MLN1", "2100", "MIXED", 402000.0, 171000.0, 403000.0, 171500.0),
            ],
            [(50000, 30000, 1598, 20000)],
            [("MLN1", 1500, 600)],
            [("MLN1", 45, 10, 120)],
        ]
    )
    calls = {"n": 0}

    def fake_conn():
        calls["n"] += 1
        return FakeConnection(cur)

    monkeypatch.setattr(nwr_router, "get_conn", fake_conn)

    r1 = client.get("/api/nwr/overview")
    assert r1.status_code == 200
    body = r1.json()
    assert body["counts"]["elrs"] == 1598
    assert len(body["elrs"]) == 1
    assert len(body["macro_links"]) == 3
    assert [m[3] for m in body["macro_links"]] == [0, 1, 2]
    assert body["elr_fusion_stats"]["MLN1"]["avg_elev"] == 45

    # Cached on second request
    assert client.get("/api/nwr/overview").status_code == 200
    assert calls["n"] == 1


def test_nwr_viewport_all_layers(client, monkeypatch):
    """GET /api/nwr/viewport fetches all 5 geospatial layers when enabled."""
    cur = FakeCursor(
        responses=[
            [[1, "A1", "MLN1", "1100", 0.0, 1.0, "NR", [[0, 0], [10, 0]], 1609]],
            [[2, "N1", 3, 5.0, 5.0]],
            [[3, "W1", "MLN1", "M", 1.0, 5.0, 5.0]],
            [[4, "MLN1", 0, 1760, "0m", "1m", 500, 900, 700, "Moderate", 0, 0, 10, 0]],
            [[5, "MLN1", 100, "0m 100yd", 5.0, 5.0, 30.0, "Slough", "SL1", 4, "Chalk", 700]],
        ]
    )
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(cur))
    resp = client.get("/api/nwr/viewport?min_x=0&max_x=100&min_y=0&max_y=100&include_nf_points=true")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["centrelines"]) == 1
    assert len(data["nodes"]) == 1
    assert len(data["waymarks"]) == 1
    assert len(data["curvature"]) == 1
    assert len(data["nf_points"]) == 1


def test_nwr_search_and_pick(client, monkeypatch):
    """Tests /api/nwr/search and /api/nwr/pick across all geospatial asset types."""
    cur_search = FakeCursor(
        responses=[
            [{"fid": 1, "asset_id": "E1", "elr": "MLN1", "start_mile_yards": 0.0, "end_mile_yards": 100.0, "min_x": 0.0, "max_x": 100.0, "min_y": 0.0, "max_y": 100.0}],
            [{"fid": 2, "asset_id": "C1", "elr": "MLN1", "track_id": "1100", "start_mile_yards": 0.0, "end_mile_yards": 1.0, "min_x": 0.0, "max_x": 10.0, "min_y": 0.0, "max_y": 10.0}],
            [{"fid": 3, "asset_id": "N1", "valency": 3, "x": 5.0, "y": 5.0}],
            [{"fid": 4, "asset_id": "W1", "elr": "MLN1", "unit": "M", "value": 1.0, "x": 5.0, "y": 5.0}],
        ]
    )
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(cur_search))
    r_s = client.get("/api/nwr/search?q=MLN1")
    assert r_s.status_code == 200
    assert {r["type"] for r in r_s.json()["results"]} == {"nwr_elr", "nwr_centreline", "nwr_node", "nwr_waymark"}

    cur_pick = FakeCursor(
        responses=[
            [
                {"fid": 1, "asset_id": "N1", "valency": 3, "x": 10.0, "y": 10.0, "dist": 1.0},
                {"fid": 2, "asset_id": "N2", "valency": 2, "x": 12.0, "y": 10.0, "dist": 2.0},
            ],
            [{"fid": 3, "asset_id": "W1", "elr": "MLN1", "unit": "M", "value": 1.0, "x": 11.0, "y": 10.0, "dist": 1.5}],
            [
                {"fid": 4, "asset_id": "C1", "elr": "MLN1", "track_id": "1100", "coords": [[0.0, 10.0], [20.0, 10.0]]},
                {"fid": 5, "asset_id": "C2", "elr": "MLN1", "track_id": "2100", "coords": [[10.0, 10.0], [10.0, 10.0]]},
            ],
            [
                {"fid": 6, "asset_id": "E1", "elr": "MLN1", "parts": [[[0.0, 10.0], [20.0, 10.0]], [[10.0, 10.0], [10.0, 10.0]]]},
            ],
            [
                {"pk": 7, "elr": "MLN1", "total_yards_from": 0, "total_yards_to": 100, "mileage_from": "0m", "mileage_to": "0m 100yd", "radius_min_m": 600, "radius_max_m": 800, "radius_avg_m": 700, "radius_avg_class": "Mod", "x0": 0.0, "y0": 10.0, "x1": 20.0, "y1": 10.0},
                {"pk": 8, "elr": "MLN1", "total_yards_from": 0, "total_yards_to": 0, "mileage_from": "0m", "mileage_to": "0m", "radius_min_m": 600, "radius_max_m": 800, "radius_avg_m": 700, "radius_avg_class": "Mod", "x0": 10.0, "y0": 10.0, "x1": 10.0, "y1": 10.0},
            ],
            [{"pk": 9, "elr": "MLN1", "total_yards": 100, "mileage": "0m 100yd", "easting": 10.0, "northing": 10.0, "elevation_m": 25.0, "place_name": "Slough", "postcode": "SL1", "dist": 0.5}],
        ]
    )
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(cur_pick))
    r_p = client.get("/api/nwr/pick?x=10&y=10&radius=50")
    assert r_p.status_code == 200
    assert {"nwr_node", "nwr_waymark", "nwr_centreline", "nwr_elr", "nf_curvature", "nf_point"} <= {
        c["type"] for c in r_p.json()["candidates"]
    }


def test_nwr_asset_all_six_types_and_404s(client, monkeypatch):
    """Tests /api/nwr/asset/{type}/{id} across all 6 NWR/NF asset types and missing-record 404s."""
    monkeypatch.setattr(
        nwr_router,
        "find_tps_cross_links",
        lambda *_a: {"tps_stations": [{"abbrev": "SLO"}], "tps_nodes_count": 5},
    )
    monkeypatch.setattr(
        nwr_router,
        "find_network_fusion_for_elr",
        lambda *_a: {
            "summary": {
                "min_elevation_m": 20,
                "max_elevation_m": 50,
                "min_radius_m": 600,
                "avg_radius_m": 800,
                "point_count": 10,
                "tightest_curve": {"class": "Mod", "mileage": "18m"},
                "bedrocks": ["Chalk"],
                "nearest_places": ["Slough"],
                "postcodes": ["SL1"],
            }
        },
    )

    # 1. nwr_centreline
    cur_cl = FakeCursor(
        responses=[
            [
                {
                    "fid": 1, "asset_id": "CL1", "elr": "MLN1", "track_id": "1100",
                    "start_mile_yards": 18.0, "end_mile_yards": 19.0, "start_meters": 28968.0, "end_meters": 30577.0,
                    "length_meters": 1609.0, "start_node_id": "N1", "end_node_id": "N2",
                    "owner": "NETWORK RAIL:100%", "version": "1", "extracted": "2026-10-26",
                    "min_x": 495000.0, "max_x": 496000.0, "min_y": 179000.0, "max_y": 180000.0,
                    "coords": [[495000.0, 179000.0], [496000.0, 180000.0]], "raw_attrs": {},
                }
            ],
            [
                {"asset_id": "N1", "valency": 3, "x": 495000.0, "y": 179000.0},
                {"asset_id": "N2", "valency": 2, "x": 496000.0, "y": 180000.0},
            ],
            [{"fid": 10, "asset_id": "E1", "elr": "MLN1", "start_mile_yards": 0.0, "end_mile_yards": 100.0}],
            [{"fid": 20, "asset_id": "W1", "elr": "MLN1", "unit": "M", "value": 18.5, "meters": 29772.0, "x": 495500.0, "y": 179500.0}],
        ]
    )
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(cur_cl))
    r_cl = client.get("/api/nwr/asset/nwr_centreline/1")
    assert r_cl.status_code == 200
    assert "Network Fusion Elevation" in r_cl.json()["decoded"]

    # 2. nwr_node (200 + 404)
    cur_nd = FakeCursor(
        responses=[
            [{"fid": 2, "asset_id": "N1", "valency": 3, "x": 495000.0, "y": 179000.0, "version": "1", "extracted": "2026", "raw_attrs": {}}],
            [{"fid": 1, "asset_id": "CL1", "elr": "MLN1", "track_id": "1100", "start_mile_yards": 0.0, "end_mile_yards": 1.0, "length_meters": 100.0, "coords": [[0, 0], [10, 0]]}],
        ]
    )
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(cur_nd))
    assert client.get("/api/nwr/asset/nwr_node/2").status_code == 200
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/nwr/asset/nwr_node/999").status_code == 404

    # 3. nwr_elr (200 + 404)
    cur_elr = FakeCursor(
        responses=[
            [{"fid": 3, "asset_id": "E1", "elr": "MLN1", "start_mile_yards": 0.0, "end_mile_yards": 100.0, "parts": [[[0, 0], [10, 0]]], "min_x": 0.0, "max_x": 10.0, "min_y": 0.0, "max_y": 10.0, "version": "1", "extracted": "2026", "raw_attrs": {}}],
            [{"fid": 1, "asset_id": "CL1", "elr": "MLN1", "track_id": "1100", "start_mile_yards": 0.0, "end_mile_yards": 1.0, "length_meters": 100.0}],
        ]
    )
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(cur_elr))
    assert client.get("/api/nwr/asset/nwr_elr/3").status_code == 200
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/nwr/asset/nwr_elr/999").status_code == 404

    # 4. nwr_waymark (200 + 404)
    cur_wm = FakeCursor(
        responses=[
            [{"fid": 4, "asset_id": "W1", "elr": "MLN1", "unit": "M", "value": 18.0, "meters": 28968.0, "x": 495000.0, "y": 179000.0, "version": "1", "extracted": "2026", "raw_attrs": {}}]
        ]
    )
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(cur_wm))
    assert client.get("/api/nwr/asset/nwr_waymark/4").status_code == 200
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/nwr/asset/nwr_waymark/999").status_code == 404

    # 5. nf_point (200 + 404)
    cur_nfp = FakeCursor(
        responses=[
            [
                {
                    "pk": 5, "elr": "MLN1", "mileage": "18m 00ch", "total_yards": 31680,
                    "easting": 495000.0, "northing": 179000.0, "latitude": 51.51, "longitude": -0.59,
                    "os_grid_ref": "SU9579", "olc": "9C3X", "elevation_m": 30.0, "linear_accuracy_m": 2.0,
                    "track_count_primary": 4, "track_count_all": 6, "track_ids_primary": "1100,2100",
                    "track_ids_siding": "", "track_ids_crossover": "",
                    "min_air_temperature_c": 2.0, "max_air_temperature_c": 22.0, "mean_wind_speed_ms": 4.5, "annual_rainfall_mm": 650,
                    "bedrock": "Chalk", "deposit": "Gravel", "material": "Ballast",
                    "place_name": "Slough", "place_distance_m": 100, "postcode": "SL1",
                    "admin_area": "Slough", "county_district": "Berkshire", "country": "England", "nr_region": "Western",
                }
            ]
        ]
    )
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(cur_nfp))
    assert client.get("/api/nwr/asset/nf_point/5").status_code == 200
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/nwr/asset/nf_point/999").status_code == 404

    # 6. nf_curvature (200 + 404)
    cur_nfc = FakeCursor(
        responses=[
            [
                {
                    "pk": 6, "elr": "MLN1", "mileage_from": "18m", "mileage_to": "19m",
                    "total_yards_from": 31680, "total_yards_to": 33440,
                    "radius_avg_m": 350, "radius_avg_class": "Sharp",
                    "radius_min_m": 300, "radius_min_class": "Sharp",
                    "radius_max_m": 400, "radius_max_class": "Sharp",
                    "x0": 495000.0, "y0": 179000.0, "x1": 496000.0, "y1": 180000.0, "coords": None,
                }
            ]
        ]
    )
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(cur_nfc))
    assert client.get("/api/nwr/asset/nf_curvature/6").status_code == 200
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/nwr/asset/nf_curvature/999").status_code == 404

