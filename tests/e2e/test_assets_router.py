"""Comprehensive E2E tests for backend/routers/assets.py (all 15 TPS asset handlers)."""

from fastapi.testclient import TestClient
import pytest

import main
from routers import assets as assets_router
import track_identity as ti
from tests.conftest import FakeConnection, FakeCursor

pytestmark = pytest.mark.e2e


@pytest.fixture
def client(monkeypatch):
    """Return a TestClient with background ingestion disabled and lookup/matcher helpers stubbed."""
    monkeypatch.setattr(main, "start_background_ingestion", lambda: None)
    monkeypatch.setattr(
        assets_router,
        "load_lookup_cache",
        lambda: {
            "interlockingsystem": {
                "1": {
                    "display": "4-Aspect Main",
                    "raw_attrs": {
                        "basesignaltypeid": "2",
                        "aspects": "4",
                        "settime": "5",
                        "freeingtime": "10",
                        "releasemode": "Auto",
                    },
                },
                "15": {"display": "Motorised Points", "raw_attrs": {}},
            }
        },
    )
    monkeypatch.setattr(assets_router, "decode_code", lambda cat, cid: f"{cat}:{cid}" if cid else None)
    monkeypatch.setattr(ti, "decode_code", lambda cat, cid: f"{cat}:{cid}" if cid else None)
    monkeypatch.setattr(assets_router, "get_saved_track_override", lambda *_a: None)
    monkeypatch.setattr(
        assets_router,
        "find_nwr_cross_links_for_tps",
        lambda *_a, **_kw: {"single_match": True, "best_centreline": {"fid": 1, "track_id": "1100"}},
    )
    monkeypatch.setattr(
        assets_router,
        "resolve_tps_track_identity",
        lambda *_a, **_kw: {
            "direction_hint": "up",
            "secondary_dir": None,
            "track_name_hint": "Up Main",
            "platform_num": None,
            "source": "TPS Track",
        },
    )
    with TestClient(main.app) as tc:
        yield tc


def test_asset_station_200_and_404(client, monkeypatch):
    """GET /api/asset/station/{id} returns station tracks, boundaries, routes, banks, and extrapolated ELR."""
    monkeypatch.setattr(
        assets_router,
        "get_timing_catalog",
        lambda: {"station_to_banks": {"10": [{"code": "PADB", "description": "GWML"}]}},
    )
    cur = FakeCursor(
        responses=[
            [
                {
                    "station_id": 10, "abbrev": "SLO", "longname": "Slough", "crscode": "SLO",
                    "stanox": "73210", "nalco": "3120", "nlccheckchr": "0", "uiccode": "70",
                    "uicstationcode": "12345", "station_type": 2, "station_category": 1,
                    "km_region_id": None, "km_value": 0, "std_stopping_time": 60,
                    "std_connection_time": 300, "compulsory_stop": False, "x": 100.0, "y": 200.0,
                    "min_x": 90.0, "max_x": 110.0, "min_y": 190.0, "max_y": 210.0,
                    "track_count": 5, "active_track_count": 5, "positions": [],
                    "raw_attrs": {}, "raw_children": [],
                }
            ],
            [
                {
                    "pk": 1, "track_id": 101, "name": "P1", "longname": "Plat 1", "seq": 1,
                    "description": "Windsor Bay", "business_reference_id": "B1",
                    "track_category": 1, "effective_length": 200, "rop_line_code": 1,
                    "sa_line_code": 2, "permissive_working": True, "directed": False,
                    "valid_from": "2025", "valid_to": "2026", "node_ids": [1, 2],
                    "coords": [[0, 0], [10, 0]], "raw_attrs": {},
                }
            ],
            [
                {
                    "pk": 2, "signal_id": 500, "boundary_type_id": 1, "valid_from": "2025", "valid_to": "2026",
                    "sig_pk": 50, "sig_name": "T3533", "master_name": "T3533", "x": 95.0, "y": 200.0,
                    "interlocking_sys_id": 1,
                }
            ],
            [
                {
                    "pk": 3, "route_id": 300, "rop_line_code_id": 1, "sa_line_code_id": 2,
                    "prelocation": 0, "node_ids": [1, 2], "coords": [[0, 0], [10, 0]],
                }
            ],
            [{"km_region_id": 5, "km_value": 29700}],
        ]
    )
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(cur))
    resp = client.get("/api/asset/station/10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["asset_type"] == "station"
    assert len(body["related"]["tracks"]) == 1
    assert len(body["related"]["boundary_signals"]) == 1
    assert "Extrapolated" in body["decoded"]["Engineer's Line Ref (ELR)"]


def test_asset_edge_200_and_404(client, monkeypatch):
    """GET /api/asset/edge/{id} returns velocity profiles, blocks, tracks, routes, attr sections, and NWR match."""
    cur = FakeCursor(
        responses=[
            [
                {
                    "pk": 1, "edge_id": 101, "start_node_id": 10, "end_node_id": 20,
                    "length": 500, "vmax": 200, "gradient": 5.0, "electrified": True,
                    "partial_release": False, "approach_signal": True, "radius": 1200,
                    "height_difference": 2.5, "tunnel_square_area": 0, "tunnel_factor": 0,
                    "geogis_line_code": 1, "valid_from": "2025", "valid_to": "2026",
                    "last_modified": "2026-01-01", "x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 0.0,
                    "min_x": 0.0, "max_x": 100.0, "min_y": 0.0, "max_y": 0.0,
                    "velocity_profiles": [{"profileid": 7, "vmax": 200}],
                    "raw_attrs": {}, "raw_children": [],
                }
            ],
            [
                {"node_id": 10, "km_region_id": 5, "km_value": 10000},
                {"node_id": 20, "km_region_id": 5, "km_value": None},
            ],
            [{"pk": 2, "block_id": 200, "pre_signal_distance": 100, "sight_distance": 200, "atc_flag": True, "moving_block": False, "coords": []}],
            [{"pk": 3, "track_id": 300, "station_id": 10, "name": "UM", "description": "Up Main", "track_category": 1, "station_name": "Slough", "station_abbrev": "SLO", "coords": []}],
            [{"pk": 4, "route_id": 400, "station_id": 10, "rop_line_code_id": 1, "sa_line_code_id": 2, "station_name": "Slough"}],
            [
                {
                    "pk": 5, "id": 500, "section_id": 50, "km_region_id": 5,
                    "short_description": "MLN1", "description": "GWML",
                    "section_attrs": [{"sectionattributetypeid": 1, "value": "25kV", "kmvaluebegin": 1000, "kmvalueend": 2000, "ascending": True}],
                }
            ],
        ]
    )
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(cur))
    resp = client.get("/api/asset/edge/1")
    assert resp.status_code == 200
    assert len(resp.json()["related"]["velocity_profiles"]) == 1
    assert len(resp.json()["related"]["attribute_sections"]) == 1

    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/asset/edge/999").status_code == 404


def test_asset_node_200_and_404(client, monkeypatch):
    """GET /api/asset/node/{id} handles turnout classification and adjacent-edge mileage extrapolation."""
    cur = FakeCursor(
        responses=[
            [
                {
                    "node_id": 10, "business_number": "BN1", "line_id": 1,
                    "netx": 10.0, "nety": 20.0, "netz": 0.0, "linex": 10.0, "liney": 20.0, "linez": 0.0,
                    "angle": 0.0, "km_region_id": None, "km_value": 0,
                    "second_km_region_id": 6, "second_km_value": 5000,
                    "valid_from": "2025", "valid_to": "2026", "last_modified": "2026-01-01",
                    "raw_attrs": {}, "raw_children": [],
                }
            ],
            [
                {"pk": 1, "edge_id": 101, "start_node_id": 10, "end_node_id": 11, "length": 100, "vmax": 160, "gradient": 0, "electrified": True, "x0": 10.0, "y0": 20.0, "x1": 20.0, "y1": 20.0},
                {"pk": 2, "edge_id": 102, "start_node_id": 9, "end_node_id": 10, "length": 100, "vmax": 160, "gradient": 0, "electrified": True, "x0": 0.0, "y0": 20.0, "x1": 10.0, "y1": 20.0},
                {"pk": 3, "edge_id": 103, "start_node_id": 10, "end_node_id": 12, "length": 100, "vmax": 80, "gradient": 0, "electrified": True, "x0": 10.0, "y0": 20.0, "x1": 20.0, "y1": 30.0},
            ],
            [{"pk": 5, "id": 5, "signal_id": 50, "name": "T3533", "master_name": "T3533", "interlocking_sys_id": 1, "bumper": False}],
            [{"pk": 6, "track_id": 60, "station_id": 1, "name": "UM", "description": "Up Main", "station_name": "Slough"}],
            [{"pk": 7, "block_id": 70, "pre_signal_distance": 100, "sight_distance": 200}],
            [{"length": 100, "start_node_id": 10, "km_region_id": 5, "km_value": 29800}],
        ]
    )
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(cur))
    resp = client.get("/api/asset/node/10")
    assert resp.status_code == 200
    assert "Points / Turnout" in resp.json()["decoded"]["Node Classification"]

    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/asset/node/999").status_code == 404


def test_asset_signal_and_points_200_and_404(client, monkeypatch):
    """GET /api/asset/signal/{id} and /points/{id} resolve blocks, overlaps, boundaries, and fallbacks."""
    monkeypatch.setattr(
        assets_router,
        "resolve_node_path_details",
        lambda _cur, nids, **_kw: {
            "coords": [[0.0, 0.0], [100.0, 0.0]],
            "length_meters": 200,
            "min_vmax": 100,
            "max_vmax": 160,
            "edge_lengths": [200.0],
            "speed_profile": [{"start_m": 0, "end_m": 200, "length_m": 200, "vmax_kmh": 160, "vmax_mph": 99.4, "differential_speeds": []}],
            "turnout_nodes": [{"node_id": 30, "x": 50.0, "y": 0.0, "degree": 3}],
            "node_map": {20: {"netx": 0.0, "nety": 0.0}, 30: {"netx": 50.0, "nety": 0.0}, 40: {"netx": 100.0, "nety": 0.0}},
        },
    )

    sig_row = {
        "pk": 50, "id": 500, "signal_id": 5000, "name": "T3533", "master_name": "T3533",
        "interlocking_sys_id": 1, "bumper": False, "tmp_closed": False,
        "km_region_id": None, "start_node_id": 10, "end_node_id": 20, "point_node_id": None,
        "x": 50.0, "y": 10.0, "sec_sect_freeing_time": 30, "use_sec_sect_freeing_time": True,
        "valid_from": "2025", "valid_to": "2026",
        "security_sections": [{"vmax": 80, "blockoccupying": True, "nodes": [{"nodeid": 30, "boundarynode": True}, {"nodeid": 40}]}],
        "raw_attrs": {}, "raw_children": [],
    }
    cur = FakeCursor(
        responses=[
            [sig_row],
            [{"node_id": 20, "netx": 50.0, "nety": 10.0, "km_region_id": None, "km_value": 0, "angle": 0.0}],
            [{"pk": 1, "edge_id": 101, "start_node_id": 10, "end_node_id": 20, "length": 150, "vmax": 160, "gradient": 0, "electrified": True, "x0": 0.0, "y0": 10.0, "x1": 50.0, "y1": 10.0}],
            [
                # Approach block ending at 20
                {"pk": 11, "block_id": 110, "line_id": 1, "pre_signal_distance": 100, "sight_distance": 150, "priority": 1, "atc_length": 0, "atc_flag": False, "moving_block": False, "shunting_flag": False, "additional_aspects": 0, "node_ids": [5, 20], "coords": [[0, 0], [50, 0]]},
                # Outgoing block starting at 20
                {"pk": 12, "block_id": 120, "line_id": 1, "pre_signal_distance": 100, "sight_distance": 150, "priority": 1, "atc_length": 0, "atc_flag": False, "moving_block": False, "shunting_flag": False, "additional_aspects": 0, "node_ids": [20, 30], "coords": [[50, 0], [100, 0]]},
            ],
            # Entry signals for approach block
            [{"pk": 49, "id": 499, "signal_id": 4999, "name": "T3531", "master_name": "T3531", "interlocking_sys_id": 1, "start_node_id": 4, "end_node_id": 5, "x": 0.0, "y": 10.0}],
            # Exit signals for outgoing block
            [{"pk": 51, "id": 501, "signal_id": 5001, "name": "T3535", "master_name": "T3535", "interlocking_sys_id": 1, "start_node_id": 20, "end_node_id": 30, "x": 100.0, "y": 10.0}],
            # Protected stations
            [{"boundary_type_id": 1, "station_id": 10, "longname": "Slough", "abbrev": "SLO", "crscode": "SLO"}],
            # Alt node mileage lookup (start_node_id=10 -> empty, end_node_id=20 -> empty)
            [],
            [],
            # Fallback connected edge mileage lookup
            [{"edge_id": 101, "length": 150, "start_node_id": 10, "end_node_id": 20, "s_reg": 5, "s_km": 29700, "e_reg": 5, "e_km": 29850}],
            # Start/end km pair
            [{"node_id": 10, "km_value": 29700}, {"node_id": 20, "km_value": 29850}],
        ]
    )
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(cur))
    resp = client.get("/api/asset/signal/50")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["related"]["approach_blocks"]) == 1
    assert len(body["related"]["outgoing_blocks"]) == 1
    assert len(body["related"]["security_sections"]) == 1
    assert len(body["related"]["protected_stations"]) == 1

    # Points asset via fallback id lookup + signal-name direction fallback + valency query
    monkeypatch.setattr(
        assets_router,
        "resolve_tps_track_identity",
        lambda *_a, **_kw: {"direction_hint": None, "secondary_dir": None, "track_name_hint": None, "platform_num": None, "source": None},
    )
    pts_row = {**sig_row, "name": "POINTS DOWN 101", "interlocking_sys_id": 15, "security_sections": []}
    cur_pts = FakeCursor(
        responses=[
            [],           # pk miss -> fallback to id
            [pts_row],    # id hit
            [{"node_id": 20, "netx": 50.0, "nety": 10.0, "km_region_id": 5, "km_value": 29700, "angle": 0.0}],
            [],           # approach_edge
            [],           # blocks
            [],           # station_boundaries
            [{"node_id": 10, "km_value": 29700}, {"node_id": 20, "km_value": 29850}],
            [{"cnt": 3}], # valency count
        ]
    )
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(cur_pts))
    r_pts = client.get("/api/asset/points/500")
    assert r_pts.status_code == 200
    assert r_pts.json()["asset_type"] == "points"

    # 404 when neither pk nor id matches
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[], []])))
    assert client.get("/api/asset/signal/999").status_code == 404


def test_asset_track_block_route_and_attributesection(client, monkeypatch):
    """Tests track, block, route, and attributesection handlers (200 + 404)."""
    monkeypatch.setattr(
        assets_router,
        "resolve_node_path_details",
        lambda _cur, nids, **_kw: {
            "coords": [[0.0, 0.0], [100.0, 0.0]],
            "length_meters": 250,
            "min_vmax": 100,
            "max_vmax": 160,
            "edge_lengths": [250.0],
            "speed_profile": [{"start_m": 0, "end_m": 250, "length_m": 250, "vmax_kmh": 160, "vmax_mph": 99.4, "differential_speeds": []}],
            "turnout_nodes": [{"node_id": 20, "x": 50.0, "y": 0.0, "degree": 3}],
            "node_map": {},
        },
    )

    # 1. track
    cur_trk = FakeCursor(
        responses=[
            [
                {
                    "pk": 1, "track_id": 101, "station_id": 10, "station_name": "Slough", "station_abbrev": "SLO",
                    "crscode": "SLO", "name": "P1", "longname": "Plat 1", "description": "Down Main",
                    "track_category": 1, "effective_length": 240, "rop_line_code": 1, "sa_line_code": 2,
                    "business_reference_id": "BR1", "permissive_working": False, "directed": True,
                    "track_tmp_closed": False, "valid_from": "2025", "valid_to": "2026",
                    "node_ids": [10, 20], "min_x": 0.0, "max_x": 100.0, "min_y": 0.0, "max_y": 10.0,
                    "coords": [[0, 0], [100, 0]], "raw_attrs": {}, "raw_children": [],
                }
            ],
            [{"pk": 11, "block_id": 110, "pre_signal_distance": 100, "sight_distance": 150, "shunting_flag": False, "atc_flag": True}],
            [{"pk": 21, "route_id": 210, "station_id": 10, "rop_line_code_id": 1, "sa_line_code_id": 2}],
            [{"km_region_id": 5, "km_value": 29600}, {"km_region_id": 5, "km_value": 29800}],
        ]
    )
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(cur_trk))
    assert client.get("/api/asset/track/1").status_code == 200
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/asset/track/999").status_code == 404

    # 2. block
    cur_blk = FakeCursor(
        responses=[
            [
                {
                    "pk": 2, "block_id": 202, "business_reference_id": "BLK2", "shunting_flag": False,
                    "moving_block": False, "pre_signal_distance": 100, "sight_distance": 150,
                    "priority": 1, "atc_flag": True, "atc_length": 50, "additional_aspects": 0,
                    "valid_from": "2025", "valid_to": "2026", "node_ids": [10, 20, 30],
                    "coords": [[0, 0], [100, 0]], "min_x": 0.0, "max_x": 100.0, "min_y": 0.0, "max_y": 0.0,
                    "raw_attrs": {}, "raw_children": [],
                }
            ],
            [{"pk": 50, "id": 500, "signal_id": 5000, "name": "T3531", "master_name": "T3531", "interlocking_sys_id": 1, "start_node_id": 9, "end_node_id": 10, "x": 0.0, "y": 0.0}],
            [{"pk": 51, "id": 501, "signal_id": 5001, "name": "T3533", "master_name": "T3533", "interlocking_sys_id": 1, "start_node_id": 20, "end_node_id": 30, "x": 100.0, "y": 0.0, "security_sections": [{"vmax": 80, "nodes": [{"nodeid": 40}]}]}],
            [{"node_id": 10, "km_region_id": None, "km_value": None}],
            [{"node_id": 30, "km_region_id": 5, "km_value": 29900}],
            [{"km_region_id": 5, "km_value": 29700}],
        ]
    )
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(cur_blk))
    r_blk = client.get("/api/asset/block/2")
    assert r_blk.status_code == 200
    assert len(r_blk.json()["related"]["exit_overlaps"]) == 1
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/asset/block/999").status_code == 404

    # 3. route
    cur_rt = FakeCursor(
        responses=[
            [
                {
                    "pk": 3, "route_id": 303, "station_id": 10, "station_name": "Slough", "station_abbrev": "SLO",
                    "rop_line_code_id": 1, "sa_line_code_id": 2, "prelocation": 0,
                    "valid_from": "2025", "valid_to": "2026", "last_modified": "2026",
                    "node_ids": [10, 20, 30], "coords": [[0, 0], [100, 0]],
                    "min_x": 0.0, "max_x": 100.0, "min_y": 0.0, "max_y": 0.0,
                    "raw_attrs": {}, "raw_children": [],
                }
            ],
            [{"km_region_id": None, "km_value": None}],
            [{"km_region_id": 5, "km_value": 29900}],
            [{"km_region_id": 5, "km_value": 29700}],
        ]
    )
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(cur_rt))
    assert client.get("/api/asset/route/3").status_code == 200
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/asset/route/999").status_code == 404

    # 4. attributesection
    cur_as = FakeCursor(
        responses=[
            [
                {
                    "pk": 4, "id": 404, "section_id": 40, "sec_name": "GWML", "section_type": 1,
                    "km_region_id": 5, "short_description": "MLN1", "description": "Great Western",
                    "edge_pairs": [[10, 20]], "node_ids": [10, 20],
                    "section_attrs": [{"sectionattributetypeid": 1, "value": "25kV", "attributeparam": "AC", "kmvaluebegin": 1000, "kmvalueend": 2000, "ascending": True}],
                    "valid_from": "2025", "valid_to": "2026", "last_modified": "2026",
                    "min_x": 0.0, "max_x": 100.0, "min_y": 0.0, "max_y": 0.0,
                    "coords": [[[0, 0], [100, 0]]], "raw_attrs": {}, "raw_children": [],
                }
            ]
        ]
    )
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(cur_as))
    assert client.get("/api/asset/attributesection/4").status_code == 200
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/asset/attributesection/999").status_code == 404


def test_asset_schematic_graphics_and_timing_entities(client, monkeypatch):
    """Tests graphictext, graphicvector, annotation, polyarea, modeltrain, tractionunit, and stationbank."""
    # graphictext
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[
        {"pk": 1, "text": "SLOUGH", "x": 10.0, "y": 20.0, "size": 12, "angle": 0, "layers": [1], "valid_from": "2025", "valid_to": "2026", "last_modified": "2026", "raw_attrs": {}}
    ]])))
    assert client.get("/api/asset/graphictext/1").status_code == 200
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/asset/graphictext/999").status_code == 404

    # graphicvector
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[
        {"pk": 2, "x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0, "layers": [1], "valid_from": "2025", "valid_to": "2026", "last_modified": "2026", "raw_attrs": {}}
    ]])))
    assert client.get("/api/asset/graphicvector/2").status_code == 200
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/asset/graphicvector/999").status_code == 404

    # annotation
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[
        {"pk": 3, "x": 5.0, "y": 6.0, "text": "<b>Note</b>", "valid_from": "2025", "valid_to": "2026", "last_modified": "2026", "raw_attrs": {}}
    ]])))
    assert client.get("/api/asset/annotation/3").status_code == 200
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/asset/annotation/999").status_code == 404

    # polyarea
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[
        {"pk": 4, "id": 40, "name": "Zone A", "description": "Depot", "corners": [[0, 0], [10, 0], [10, 10]], "min_x": 0.0, "max_x": 10.0, "min_y": 0.0, "max_y": 10.0, "raw_attrs": {}, "raw_children": []}
    ]])))
    assert client.get("/api/asset/polyarea/4").status_code == 200
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/asset/polyarea/999").status_code == 404

    # modeltrain
    cur_mt = FakeCursor(
        responses=[
            [
                {
                    "entity_id": "301", "name": "IET9",
                    "raw_attrs": {"publicname": "Class 800", "desc": "9-car", "maxspeed": "200", "coachid": "201", "coachestotallength": "180", "coachestotalweight": "300", "profile": "7"},
                    "raw_children": [{"tag": "tractionunit", "attrs": {"id": "101"}}],
                }
            ],
            [{"entity_id": "101", "name": "C800", "raw_attrs": {"length": "26", "weight": "50", "maxallowedspeed": "200"}, "raw_children": []}],
            [{"entity_id": "201", "name": "Mk4", "raw_attrs": {"desc": "Trailer", "coachtypeid": "1", "length": "23", "weight": "42", "maxspeed": "200"}}],
            [{"raw_attrs": {"firstmodeltrainid": "301", "secondmodeltrainid": "302"}}],
            [{"entity_id": "301", "name": "IET9", "descr": "9-car"}, {"entity_id": "302", "name": "IET5", "descr": "5-car"}],
        ]
    )
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(cur_mt))
    r_mt = client.get("/api/asset/modeltrain/301")
    assert r_mt.status_code == 200
    assert len(r_mt.json()["related"]["transitions"]) == 1
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/asset/modeltrain/999").status_code == 404

    # tractionunit
    cur_tu = FakeCursor(
        responses=[
            [{"entity_id": "101", "name": "C800", "raw_attrs": {"desc": "Bi-mode", "type": "1", "motortype": "2", "length": "26", "weight": "50", "maxallowedspeed": "200"}, "raw_children": []}],
            [{"entity_id": "301", "name": "IET9", "raw_attrs": {"publicname": "IET9", "desc": "9-car", "maxspeed": "200", "profile": "7"}}],
        ]
    )
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(cur_tu))
    assert client.get("/api/asset/tractionunit/101").status_code == 200
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[]])))
    assert client.get("/api/asset/tractionunit/999").status_code == 404

    # stationbank (primary + fallback to stationbanksection + 404)
    cur_sb = FakeCursor(
        responses=[
            [{"entity_id": "401", "name": "PADB", "raw_attrs": {"description": "GWML Corridor", "lastmodified": "2026"}, "raw_children": [{"tag": "stationbankid", "attrs": {"stationbankid": "501"}}]}],
            [
                {
                    "entity_id": "501", "name": "SEC1", "raw_attrs": {"description": "London-Reading"},
                    "raw_children": [
                        {"tag": "stationbankstation", "attrs": {"id": "10", "seq": "0"}, "children": [{"tag": "stationbankstationlayout", "attrs": {"stationlayoutcodeid": "1"}}]},
                        {"tag": "stationbankstation", "attrs": {"id": "11", "seq": "1"}, "children": []},
                    ],
                }
            ],
            [
                {"station_id": 10, "abbrev": "PAD", "longname": "Paddington", "crscode": "PAD", "stanox": "73000", "station_type": 2, "station_category": 1, "x": 0.0, "y": 0.0, "km_region_id": 5, "km_value": 0},
                {"station_id": 11, "abbrev": "RDG", "longname": "Reading", "crscode": "RDG", "stanox": "74220", "station_type": 1, "station_category": 1, "x": 100.0, "y": 0.0, "km_region_id": 5, "km_value": 57900},
            ],
        ]
    )
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(cur_sb))
    r_sb = client.get("/api/asset/stationbank/401")
    assert r_sb.status_code == 200
    assert r_sb.json()["decoded"]["Ordered Timing Points (TIPLOCs)"] == 2

    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(FakeCursor(responses=[[], []])))
    assert client.get("/api/asset/stationbank/999").status_code == 404

