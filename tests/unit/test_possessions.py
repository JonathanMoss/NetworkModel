"""Unit tests for backend/possessions.py (TPS BFS, points qualifiers, NWR clipping, worksites)."""

import pytest

import possessions as poss
from tests.conftest import FakeCursor

pytestmark = pytest.mark.unit


def test_format_limit_label_and_bbox():
    """Formats approach/beyond/buffer qualifiers cleanly and computes bounding boxes."""
    assert poss.format_limit_label("T3533", "at", "PLB Start") == "PLB Start: T3533"
    assert poss.format_limit_label("Points 101", "approach", "PLB Start") == "PLB Start: Approach to Points 101"
    assert poss.format_limit_label("Points 102", "beyond", "PLB End") == "PLB End: Beyond Points 102"
    assert poss.format_limit_label("Buffer 5", "buffer", "PLB End") == "PLB End: Buffer / Buffer 5"
    assert poss.format_limit_label(None, "at", "PLB Start") == "PLB Start: PLB Start"

    bbox = poss._compute_bbox([{"coords": [[10.0, 20.0], [30.0, 40.0]]}], [{"x": 5.0, "y": 50.0}])  # pylint: disable=protected-access
    assert bbox == [5.0, 20.0, 30.0, 50.0]
    assert poss._compute_bbox([], []) is None  # pylint: disable=protected-access


def test_resolve_possession_signal_points_and_worksites(monkeypatch):
    """Resolves signal/points possession (with approach/beyond qualifiers) and inner worksite."""
    monkeypatch.setattr(poss, "decode_code", lambda _cat, _cid: "MLN1")
    monkeypatch.setattr(poss, "get_saved_track_override", lambda *_a: None)
    monkeypatch.setattr(
        poss,
        "get_asset_geo_point",
        lambda _cur, atype, aid, _fid: {
            "x": 500000.0 + aid,
            "y": 180000.0,
            "elr": "MLN1",
            "track_id": "1100",
        },
    )
    monkeypatch.setattr(
        poss,
        "find_tps_straight_path",
        lambda _cur, s_nid, e_nid: [[0.0, 0.0], [100.0, 0.0]] if s_nid == 11 and e_nid == 20 else [],
    )
    monkeypatch.setattr(poss, "find_nwr_straight_path", lambda *_a: [])

    cur = FakeCursor(
        responses=[
            # start endpoint (points, approach -> start_node_id=10)
            [{"pk": 1, "signal_id": 100, "name": "POINTS 101", "master_name": "POINTS 101", "start_node_id": 10, "end_node_id": 11, "point_node_id": 11, "x": 0.0, "y": 0.0, "km_region_id": 5}],
            [{"netx": 0.0, "nety": 0.0, "km_region_id": 5, "km_value": 10000}],
            # end endpoint (signal, beyond -> end_node_id=20)
            [{"pk": 2, "signal_id": 200, "name": "T3535", "master_name": "T3535", "start_node_id": 19, "end_node_id": 20, "point_node_id": None, "x": 100.0, "y": 0.0, "km_region_id": 5}],
            [{"netx": 100.0, "nety": 0.0, "km_region_id": 5, "km_value": 12000}],
            # NWR centrelines clipped between 10000 and 12000 (fallback when graph empty)
            [{"fid": 77, "elr": "MLN1", "track_id": "1100", "start_meters": 9000, "end_meters": 15000, "coords": [[500000.0, 180000.0], [506000.0, 180000.0]]}],
        ]
    )

    tps_g, nwr_g = poss.resolve_possession_geometries(
        cur,
        {
            "limit_mode": "signal_points",
            "start_asset_type": "points",
            "start_asset_id": 1,
            "start_qualifier": "approach",
            "end_asset_type": "signal",
            "end_asset_id": 2,
            "end_qualifier": "beyond",
        },
    )
    assert len(tps_g["ways"]) == 1
    assert len(tps_g["markers"]) == 2
    assert "Approach to POINTS 101" in tps_g["markers"][0]["label"]
    assert "Beyond T3535" in tps_g["markers"][1]["label"]
    assert len(nwr_g["ways"]) == 1
    assert len(nwr_g["markers"]) == 2
    assert nwr_g["bbox"] is not None
    # Verify exact matched track_id ('1100') was passed to filter NWR centrelines
    assert any(
        "track_id = ANY" in sql and params and params[1] == ["1100"]
        for sql, params in cur.executed
    )


def test_resolve_multi_line_possession_with_freeform_names(monkeypatch):
    """Resolves multiple independent blocked lines with freeform designations."""
    monkeypatch.setattr(poss, "decode_code", lambda _cat, _cid: "MLN1")
    monkeypatch.setattr(poss, "get_saved_track_override", lambda *_a: None)
    monkeypatch.setattr(
        poss,
        "get_asset_geo_point",
        lambda _cur, _atype, aid, _fid: {
            "x": 500000.0 + aid,
            "y": 180000.0,
            "elr": "MLN1",
            "track_id": "1100" if aid in (1, 2) else "2200",
        },
    )
    monkeypatch.setattr(
        poss,
        "find_tps_straight_path",
        lambda *_a: [[0.0, 0.0], [100.0, 0.0]],
    )
    monkeypatch.setattr(
        poss,
        "find_nwr_straight_path",
        lambda *_a: [{"fid": 77, "elr": "MLN1", "track_id": "1100", "coords": [[500000.0, 180000.0], [502000.0, 180000.0]]}],
    )
    cur = FakeCursor(
        responses=[
            # Line 1: Up Fast/Main (signal_points)
            [{"pk": 1, "signal_id": 100, "name": "T100", "master_name": "T100", "start_node_id": 10, "end_node_id": 11, "point_node_id": None, "x": 0.0, "y": 0.0, "km_region_id": 5}],
            [{"netx": 0.0, "nety": 0.0, "km_region_id": 5, "km_value": 10000}],
            [{"pk": 2, "signal_id": 200, "name": "T200", "master_name": "T200", "start_node_id": 19, "end_node_id": 20, "point_node_id": None, "x": 100.0, "y": 0.0, "km_region_id": 5}],
            [{"netx": 100.0, "nety": 0.0, "km_region_id": 5, "km_value": 12000}],
            # Line 2: Platform 1 Line/Up Slow (elr_mileage)
            [{"fid": 88, "elr": "MLN1", "track_id": "2200", "start_meters": 9000, "end_meters": 15000, "coords": [[500000.0, 180004.0], [506000.0, 180004.0]]}],
            [{"code_id": "5"}],
            [{"x0": 0.0, "y0": 10.0, "x1": 100.0, "y1": 10.0}],
        ]
    )
    tps_g, nwr_g = poss.resolve_possession_geometries(
        cur,
        {
            "lines": [
                {
                    "line_name": "Up Fast/Main",
                    "limit_mode": "signal_points",
                    "start_asset_type": "signal",
                    "start_asset_id": 1,
                    "end_asset_type": "signal",
                    "end_asset_id": 2,
                },
                {
                    "line_name": "Platform 1 Line/Up Slow",
                    "limit_mode": "elr_mileage",
                    "elr": "MLN1",
                    "start_meters": 10000,
                    "end_meters": 12000,
                    "track_ids": ["2200"],
                },
            ]
        },
    )
    assert len(tps_g["ways"]) == 2
    assert len(nwr_g["ways"]) == 2
    assert {w.get("line_name") for w in nwr_g["ways"]} == {"Up Fast/Main", "Platform 1 Line/Up Slow"}
    assert {m.get("line_name") for m in tps_g["markers"]} == {"Up Fast/Main", "Platform 1 Line/Up Slow"}


def test_resolve_possession_elr_mileage_and_worksite():
    """Resolves ELR + mileage possession with track_ids filter and inner mileage worksite."""
    cur = FakeCursor(
        responses=[
            # NWR centrelines with track_ids
            [{"fid": 88, "elr": "MLN1", "track_id": "1100", "start_meters": 25000, "end_meters": 35000, "coords": [[510000.0, 180000.0], [520000.0, 180000.0]]}],
            # TPS lookup_codes for MLN1
            [{"code_id": "5"}],
            # TPS edges in range
            [{"x0": 100.0, "y0": 200.0, "x1": 250.0, "y1": 200.0}],
        ]
    )
    tps_g, nwr_g = poss.resolve_possession_geometries(
        cur,
        {
            "limit_mode": "elr_mileage",
            "elr": "MLN1",
            "start_meters": 28000,
            "end_meters": 32000,
            "track_ids": ["1100"],
        },
    )
    assert len(nwr_g["ways"]) == 1
    assert len(tps_g["ways"]) == 1
    assert len(nwr_g["markers"]) == 2
    assert len(tps_g["markers"]) == 2

    # Inner worksite by mileage inherits parent ELR & track_ids and converts PLB -> WMB labels
    cur_ws = FakeCursor(
        responses=[
            [{"fid": 88, "elr": "MLN1", "track_id": "1100", "start_meters": 25000, "end_meters": 35000, "coords": [[510000.0, 180000.0], [520000.0, 180000.0]]}],
            [{"code_id": "5"}],
            [{"x0": 120.0, "y0": 200.0, "x1": 180.0, "y1": 200.0}],
        ]
    )
    ws_tps, ws_nwr = poss.resolve_worksite_geometries(
        cur_ws,
        {"elr": "MLN1", "track_ids": ["1100"]},
        {"title": "WS-1", "start_meters": 29000, "end_meters": 30500},
    )
    assert ws_nwr["markers"][0]["label"].startswith("WMB Start:")
    assert ws_tps["markers"][1]["label"].startswith("WMB End:")


def test_resolve_endpoint_node_missing_and_straight_line_fallback(monkeypatch):
    """Covers node endpoint resolution, missing/unsupported types, and disconnected fallback."""
    monkeypatch.setattr(poss, "decode_code", lambda _cat, _cid: "MLN1")
    monkeypatch.setattr(poss, "get_saved_track_override", lambda *_a: None)
    monkeypatch.setattr(
        poss,
        "get_asset_geo_point",
        lambda _cur, _atype, aid, _fid: {"x": 500000.0 + aid, "y": 180000.0, "elr": "MLN1" if aid == 10 else "GW2"},
    )

    assert poss._resolve_tps_endpoint(FakeCursor(), None, None) is None  # pylint: disable=protected-access
    assert poss._resolve_tps_endpoint(FakeCursor(), "station", 1) is None  # pylint: disable=protected-access
    assert poss._resolve_tps_endpoint(FakeCursor(responses=[[]]), "signal", 99) is None  # pylint: disable=protected-access
    assert poss._resolve_tps_endpoint(FakeCursor(responses=[[]]), "node", 99) is None  # pylint: disable=protected-access

    monkeypatch.setattr(poss, "find_tps_straight_path", lambda *_a: [])
    monkeypatch.setattr(poss, "find_nwr_straight_path", lambda *_a: [])

    # Two node endpoints on different ELRs with disconnected graph -> straight-line TPS & NWR fallback
    cur = FakeCursor(
        responses=[
            [{"node_id": 10, "netx": 0.0, "nety": 0.0, "km_region_id": 5, "km_value": 1000}],
            [{"node_id": 20, "netx": 50.0, "nety": 50.0, "km_region_id": None, "km_value": None}],
        ]
    )
    tps_g, nwr_g = poss.resolve_possession_geometries(
        cur,
        {
            "limit_mode": "signal_points",
            "start_asset_type": "node",
            "start_asset_id": 10,
            "end_asset_type": "node",
            "end_asset_id": 20,
        },
    )
    assert tps_g["ways"][0]["coords"] == [[0.0, 0.0], [50.0, 50.0]]
    assert len(nwr_g["ways"]) == 1


def test_resolve_multi_from_to_fan_in_deduplicates_shared_trunk(monkeypatch):
    """Routes multiple From limits (e.g. Bay 1 & Bay 2 buffers) to 1 To signal and deduplicates shared trunk."""
    monkeypatch.setattr(poss, "decode_code", lambda _cat, _cid: "MLN1")
    monkeypatch.setattr(poss, "get_saved_track_override", lambda *_a: None)
    monkeypatch.setattr(
        poss,
        "get_asset_geo_point",
        lambda _cur, _atype, aid, _fid: {"x": 500000.0 + aid * 10, "y": 180000.0, "elr": "MLN1", "track_id": "1100"},
    )
    # Bay 1 (node 1) -> junction (50,0) -> signal (100,0)
    # Bay 2 (node 2) -> junction (50,0) -> signal (100,0)  (shared trunk [50,0]->[100,0]!)
    monkeypatch.setattr(
        poss,
        "find_tps_straight_path",
        lambda _cur, s_nid, _e_nid: (
            [[0.0, 0.0], [50.0, 0.0], [100.0, 0.0]]
            if s_nid == 1
            else [[0.0, 20.0], [50.0, 0.0], [100.0, 0.0]]
        ),
    )
    shared_nwr = {"fid": 50, "elr": "MLN1", "track_id": "1100", "coords": [[500500.0, 180000.0], [501000.0, 180000.0]]}
    monkeypatch.setattr(
        poss,
        "find_nwr_straight_path",
        lambda _cur, s_ep, _e_ep: [
            {"fid": s_ep["asset_id"], "elr": "MLN1", "track_id": "1600", "coords": [[500000.0, 180000.0 + s_ep["asset_id"]], [500500.0, 180000.0]]},
            shared_nwr,
        ],
    )
    cur = FakeCursor(
        responses=[
            [{"node_id": 1, "netx": 0.0, "nety": 0.0, "km_region_id": 5, "km_value": 1000}],
            [{"node_id": 2, "netx": 0.0, "nety": 20.0, "km_region_id": 5, "km_value": 1000}],
            [{"pk": 3, "signal_id": 300, "name": "T300", "master_name": "T300", "start_node_id": 29, "end_node_id": 30, "point_node_id": None, "x": 100.0, "y": 0.0, "km_region_id": 5}],
            [{"netx": 100.0, "nety": 0.0, "km_region_id": 5, "km_value": 2000}],
        ]
    )
    tps_g, nwr_g = poss.resolve_possession_geometries(
        cur,
        {
            "lines": [
                {
                    "line_name": "Platform 1/2 & Up Main",
                    "limit_mode": "signal_points",
                    "from_limits": [
                        {"asset_type": "node", "asset_id": 1, "label": "Plt 1 Buffer", "qualifier": "buffer"},
                        {"asset_type": "node", "asset_id": 2, "label": "Plt 2 Buffer", "qualifier": "buffer"},
                    ],
                    "to_limits": [
                        {"asset_type": "signal", "asset_id": 3, "label": "T300", "qualifier": "at"},
                    ],
                }
            ]
        },
    )
    # 2 start markers + 1 end marker
    assert len(tps_g["markers"]) == 3
    assert len(nwr_g["markers"]) == 3
    # Shared NWR trunk (fid=50) deduplicated -> 3 total ways (bay1, shared, bay2) instead of 4
    assert [w["fid"] for w in nwr_g["ways"]] == [1, 50, 2]
    # Shared TPS trunk deduplicated -> 2nd way only has bay2 spur [[0,20],[50,0]]
    assert len(tps_g["ways"]) == 2
    assert tps_g["ways"][1]["coords"] == [[0.0, 20.0], [50.0, 0.0]]


def test_format_possession_won_text_exact_uk_format():
    """Formats multi-line possessions in authentic UK Weekly Operating Notice (WON) wording."""
    sample = {
        "won_ref": "WON Wk34",
        "title": "Southbury Renewal",
        "picop_name": "A. Smith",
        "start_dt": "Sat 23:30",
        "end_dt": "Sun 06:00",
        "ole_isolation": "Earthed",
        "notes": "Adjacent line open at 20mph.",
        "lines": [
            {
                "line_name": "Up Fast",
                "from_limits": [{"asset_type": "signal", "label": "SIG: NT102", "qualifier": "beyond", "distance_meters": 400}],
                "to_limits": [{"asset_type": "signal", "label": "SB204", "qualifier": "approach", "distance_meters": 400}],
            },
            {
                "line_name": "Up Slow Line",
                "from_limits": [{"asset_type": "signal", "label": "NT106", "qualifier": "beyond", "distance_meters": 400}],
                "to_limits": [{"asset_type": "points", "label": "No. 512A points at Southbury", "qualifier": "clear_of"}],
            },
            {
                "line_name": "Up E&G",
                "from_limits": [{"asset_type": "signal", "label": "SIG: CQ53", "qualifier": "beyond"}],
                "to_limits": [
                    {"asset_type": "signal", "label": "P5: GLGQHL", "qualifier": "approach"},
                    {"asset_type": "signal", "label": "P7: GLGQHL", "qualifier": "approach"},
                    {"asset_type": "signal", "label": "P6: GLGQHL", "qualifier": "approach"},
                ],
            },
        ],
    }
    assert poss.extract_won_station_codes(sample) == ["GLGQHL"]
    text_no_hdr = poss.format_possession_won_text(
        sample,
        include_header=False,
        station_names={"GLGQHL": "Glasgow Queen Street"},
    )
    assert "UP FAST LINE:\nBetween 400 metres BEYOND Signal NT102 and 400 metres ON THE APPROACH TO\nSignal SB204." in text_no_hdr
    assert "UP SLOW LINE:\nBetween 400 metres BEYOND Signal NT106 and CLEAR OF No. 512A points at\nSouthbury." in text_no_hdr
    assert "UP E&G LINE:\nBetween BEYOND Signal CQ53 and ON THE APPROACH TO Buffers Platforms 5, 6,\n7 Glasgow Queen Street." in text_no_hdr

    text_with_hdr = poss.format_possession_won_text(sample, include_header=True)
    assert "WON Wk34 — SOUTHBURY RENEWAL" in text_with_hdr
    assert "PICOP: A. Smith | OLE: Earthed" in text_with_hdr
    assert "Notes: Adjacent line open at 20mph." in text_with_hdr

    # Isolated tracks fold into the primary line heading (and blank primary line falls back to NWR track_id)
    stafford = {
        "title": "JM TEST",
        "nwr_geometry": {"ways": [{"track_id": "1100"}]},
        "lines": [
            {
                "line_name": None,
                "from_limits": [{"asset_type": "signal", "label": "SIG: SC3594", "qualifier": "beyond"}],
                "to_limits": [{"asset_type": "signal", "label": "SIG: LS3580", "qualifier": "approach"}],
            },
            {"line_name": "Platform 1", "is_isolated": True, "from_limits": [], "to_limits": []},
            {"line_name": "Up Bay Siding", "is_isolated": True, "from_limits": [], "to_limits": []},
        ],
    }
    stafford_won = poss.format_possession_won_text(stafford, include_header=False)
    assert stafford_won.strip() == (
        "UP FAST LINE, PLATFORM 1, UP BAY SIDING:\n"
        "Between BEYOND Signal SC3594 and ON THE APPROACH TO Signal LS3580."
    )
