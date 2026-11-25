"""Unit tests for backend/track_matcher.py."""

import pytest

import track_matcher as tm
from tests.conftest import FakeCursor


pytestmark = pytest.mark.unit


def _make_centreline(
    fid: int,
    elr: str,
    track_id: str,
    start_m: float = 1000.0,
    end_m: float = 2000.0,
    coords=None,
    start_node_id: int = 10,
    end_node_id: int = 20,
    **extra,
):
    """Build a synthetic nwr_track_centrelines row dictionary."""
    pts = coords or [[1000.0, 2000.0], [1500.0, 2000.0], [2000.0, 2000.0]]
    row = {
        "fid": fid,
        "asset_id": fid * 10,
        "elr": elr,
        "track_id": track_id,
        "start_mile_yards": 0.1000,
        "end_mile_yards": 1.0200,
        "start_meters": start_m,
        "end_meters": end_m,
        "length_meters": abs(end_m - start_m),
        "owner": "NWR",
        "coords": pts,
        "start_node_id": start_node_id,
        "end_node_id": end_node_id,
        "min_x": min(p[0] for p in pts),
        "max_x": max(p[0] for p in pts),
        "min_y": min(p[1] for p in pts),
        "max_y": max(p[1] for p in pts),
    }
    row.update(extra)
    return row


def test_find_34xx_continuation_tracks_prefers_pure_over_mixed():
    """Prefers 34xx tracks connecting exclusively to the requested class and caches results."""
    rows = [
        {"track_id": "3412", "sm": 1000, "em": 1500, "p0": [100.0, 200.0], "p1": [500.0, 200.0]},
        {"track_id": "3402", "sm": 1000, "em": 1500, "p0": [100.0, 200.0], "p1": [600.0, 200.0]},
        {"track_id": "1100", "sm": 1500, "em": 3000, "p0": [100.5, 200.0], "p1": [900.0, 200.0]},
        {"track_id": "1201", "sm": 1500, "em": 3000, "p0": [600.2, 200.0], "p1": [900.0, 220.0]},
    ]
    cur = FakeCursor(responses=[rows])
    # 3412 touches 1100 only (pure); 3402 touches both 1100 and 1201 (mixed)
    res = tm._find_34xx_continuation_tracks(cur, "SWM2", {"3412", "3402"}, "11", 1200.0)
    assert res == {"3412"}

    # Second call served from cache without touching cursor
    res_cached = tm._find_34xx_continuation_tracks(FakeCursor(), "SWM2", {"3402"}, "11", 1200.0)
    assert res_cached == {"3402"}


def test_find_valency2_adjacent_elr(monkeypatch):
    """Walks strictly through valency <= 2 nodes and stops at turnouts."""
    assert tm._find_valency2_adjacent_elr(FakeCursor(), [], "LEC4") is None

    monkeypatch.setattr(tm, "decode_code", lambda _cat, cid: {1: "LEC4", 2: "NBS1"}.get(cid))
    cur = FakeCursor(
        responses=[
            [{"start_node_id": 10, "end_node_id": 11}],
            [{"node_id": 11, "km_region_id": 2}],
        ]
    )
    assert tm._find_valency2_adjacent_elr(cur, [10], "LEC4") == "NBS1"

    # Turnout (valency 3) blocks traversal
    cur_turnout = FakeCursor(
        responses=[
            [
                {"start_node_id": 10, "end_node_id": 11},
                {"start_node_id": 10, "end_node_id": 12},
                {"start_node_id": 10, "end_node_id": 13},
            ]
        ]
    )
    assert tm._find_valency2_adjacent_elr(cur_turnout, [10], "LEC4") is None


def test_compute_switch_track_boost():
    """Boosts candidates with valency >= 3 switch endpoints within 15m/35m."""
    c1 = _make_centreline(1, "MLN1", "1100", start_m=1005.0, end_m=2000.0, start_node_id=501)
    c2 = _make_centreline(2, "MLN1", "2100", start_m=975.0, end_m=2000.0, start_node_id=502)
    cur = FakeCursor(responses=[[{"asset_id": 501}, {"asset_id": 502}]])
    boosts = tm._compute_switch_track_boost(cur, [c1, c2], 1000.0, "points", 3)
    assert boosts["1100"] == 60.0
    assert boosts["2100"] == 35.0
    # Non-points asset gets no switch boost
    assert tm._compute_switch_track_boost(FakeCursor(), [c1, c2], 1000.0, "signal", 2) == {}


def test_augment_platform_cross_elr_and_branch_bay():
    """Handles both cross-ELR hub platforms and single-track branch bays (Slough)."""
    primary = [_make_centreline(1, "MLN1", "1100", start_m=0.0, end_m=30000.0)]
    cross_plat = _make_centreline(2, "YMS", "3804", start_m=0.0, end_m=400.0)
    cur_hub = FakeCursor(responses=[[cross_plat]])
    aug, ref = tm._augment_platform_cross_elr_candidates(
        cur_hub, "MLN1", primary, 15000.0, 15000.0, 4, "Platform 4"
    )
    assert ref is not None
    assert any(c.get("_cross_elr") and c["elr"] == "YMS" for c in aug)

    # Branch bay fallback (Slough WIN 3600) when no cross-ELR suffix match exists
    branch_bay = _make_centreline(3, "WIN", "3600", start_m=29600.0, end_m=29900.0)
    cur_bay = FakeCursor(responses=[[], [branch_bay]])
    aug_bay, _ = tm._augment_platform_cross_elr_candidates(
        cur_bay, "MLN1", primary, 29700.0, 29700.0, 1, "P1: SLOUGH"
    )
    assert any(c.get("_branch_bay") and c["elr"] == "WIN" for c in aug_bay)


def test_augment_running_line_cross_elr_flyovers_and_chords(monkeypatch):
    """Covers Slow flyover, reversible chord, named-branch flyover, and valency-2 chord."""
    primary = [_make_centreline(1, "LEC4", "2100", start_m=20000.0, end_m=25000.0)]

    # 1) Slow flyover fallback (LEC6 2200)
    fly_slow = _make_centreline(2, "LEC6", "2200", start_m=21000.0, end_m=24000.0)
    cur_slow = FakeCursor(responses=[[fly_slow]])
    aug_s, _ = tm._augment_running_line_cross_elr_candidates(
        cur_slow, "LEC4", primary, 22000.0, 22000.0, "down", "Down Slow", None
    )
    assert any(c["elr"] == "LEC6" for c in aug_s)

    # 2) Reversible bi-dir chord fallback (NBS1 3100)
    rev_chord = _make_centreline(3, "NBS1", "3100", start_m=21000.0, end_m=24000.0)
    cur_rev = FakeCursor(responses=[[rev_chord]])
    aug_r, _ = tm._augment_running_line_cross_elr_candidates(
        cur_rev, "LEC4", primary, 22000.0, 22000.0, "reversible", "Bi-Dir Chord", None
    )
    assert any(c["elr"] == "NBS1" for c in aug_r)

    # 3) Named-branch re-chained zero-based ELR fallback (NBS 1100)
    nbs_row = _make_centreline(4, "NBS", "1100", start_m=0.0, end_m=2000.0)
    cur_br = FakeCursor(responses=[[], [nbs_row]])
    aug_b, _ = tm._augment_running_line_cross_elr_candidates(
        cur_br, "LEC4", primary, 22000.0, 22000.0, "up", "U-UNB", None
    )
    assert any(c.get("_flyover_branch") and c["elr"] == "NBS" for c in aug_b)

    # 4) Valency-2 single-track chord fallback
    v2_chord = _make_centreline(5, "NBS1", "3100", start_m=0.0, end_m=1500.0)
    monkeypatch.setattr(tm, "_find_valency2_adjacent_elr", lambda *_a, **_kw: "NBS1")
    cur_v2 = FakeCursor(responses=[[v2_chord]])
    aug_v2, _ = tm._augment_running_line_cross_elr_candidates(
        cur_v2, "LEC4", primary, 22000.0, 22000.0, "up", "Plain Line", [99]
    )
    assert any(c.get("_valency2_chord") and c["elr"] == "NBS1" for c in aug_v2)


def test_snap_point_and_discrete_markers():
    """Snaps points/signals to NWR physical nodes and generates markers & geo_snap."""
    best = _make_centreline(1, "MLN1", "1100")
    nodes = [
        {"fid": 10, "asset_id": 900, "valency": 2, "x": 1501.0, "y": 2000.0, "dist": 1.0, "on_matched_track": True},
        {"fid": 11, "asset_id": 901, "valency": 3, "x": 1505.0, "y": 2000.0, "dist": 5.0, "on_matched_track": True},
    ]
    # Points asset prioritizes valency >= 3 switch on matched track
    snapped_pts = tm._snap_point_to_nwr_node(FakeCursor(responses=[nodes]), [1500.0, 2000.0], best, "points", 3)
    assert snapped_pts["asset_id"] == 901
    assert snapped_pts["is_switch"] is True

    # Signal asset takes nearest node on matched track
    snapped_sig = tm._snap_point_to_nwr_node(FakeCursor(responses=[nodes]), [1500.0, 2000.0], best, "signal", 2)
    assert snapped_sig["asset_id"] == 900

    # Discrete markers for signal, buffer, points, node, generic, and edge
    for atype, lbl, val in [
        ("signal", "Signal T3533", 2),
        ("signal", "Buffer Stop 1", 1),
        ("points", "Points 101", 3),
        ("node", "Node 5", 3),
        ("node", "Node 6", 2),
        ("other", "Custom", 2),
    ]:
        pts = tm._build_discrete_node_points(
            best["coords"], 1000.0, 2000.0, 1500.0, None, [1500.0, 2000.0], snapped_pts, atype, val, lbl
        )
        assert len(pts) == 1
        snap = tm._build_geo_snap("MLN1", 1500.0, None, pts, best, False)
        assert snap["target_zoom"] == 0.28

    # Edge (start + end markers)
    edge_pts = tm._build_discrete_node_points(
        best["coords"], 1000.0, 2000.0, 1200.0, 1800.0, [1200.0, 2000.0], snapped_sig, "edge", None, None
    )
    assert len(edge_pts) == 2
    edge_snap = tm._build_geo_snap("MLN1", 1200.0, 1800.0, edge_pts, best, False)
    assert edge_snap["mileage_meters"] == 1500.0


def test_find_nwr_cross_links_for_tps_end_to_end():
    """Exercises full find_nwr_cross_links_for_tps pipeline including overrides and clipping."""
    # Empty ELR returns empty structure
    assert tm.find_nwr_cross_links_for_tps(FakeCursor(), None, 1000.0)["single_match"] is False

    # No candidates on ELR falls back to ELR polyline midpoint
    elr_rec = {"fid": 1, "elr": "MLN1", "parts": [[[100.0, 200.0], [300.0, 400.0]]]}
    cur_empty = FakeCursor(responses=[[elr_rec], [], []])
    res_empty = tm.find_nwr_cross_links_for_tps(cur_empty, "MLN1", 1500.0)
    assert res_empty["single_match"] is False
    assert res_empty["geo_snap"]["is_extrapolated"] is True

    # Full match with edge clipping and saved manual override
    c1 = _make_centreline(10, "MLN1", "1100", start_m=1000.0, end_m=2000.0)
    c2 = _make_centreline(20, "MLN1", "2100", start_m=1000.0, end_m=2000.0)
    cur_ok = FakeCursor(
        responses=[
            [elr_rec],    # nwr_elrs
            [c1, c2],     # primary candidates
            [],           # nearby physical nodes
        ]
    )
    res = tm.find_nwr_cross_links_for_tps(
        cur_ok,
        "MLN1",
        1200.0,
        end_km_meters=1800.0,
        direction_hint="up",
        track_name_hint="Up Main",
        asset_type="edge",
        saved_fid=20,
    )
    assert res["single_match"] is True
    assert res["best_centreline"]["fid"] == 20
    assert res["clipped"] is True
    assert res["match_info"]["saved_override"] is True
    assert res["match_info"]["source"] == "Saved Track Override"


def test_find_tps_cross_links_and_network_fusion():
    """Tests reverse NWR-to-TPS bridge and Network Fusion corridor summary."""
    assert tm.find_tps_cross_links(FakeCursor(), "")["tps_nodes_count"] == 0

    cur_tps = FakeCursor(
        responses=[
            [{"code_id": "10", "label": "MLN1", "vanilla_text": "MLN1"}],  # lookup_codes
            [{"station_id": 1, "abbrev": "PAD"}],                         # stations
            [{"cnt": 42}],                                                # nodes count
            [{"pk": 1, "edge_id": 100}],                                  # edges
            [{"pk": 2, "signal_id": 200}],                                # signals
            [{"pk": 3, "description": "MLN1"}],                           # attribute_sections
        ]
    )
    tps_res = tm.find_tps_cross_links(cur_tps, "MLN1", min_m=1000.0, max_m=2000.0)
    assert tps_res["tps_nodes_count"] == 42
    assert len(tps_res["tps_stations"]) == 1
    assert len(tps_res["tps_edges"]) == 1

    # Network Fusion summary calculation
    assert tm.find_network_fusion_for_elr(FakeCursor(), "")["summary"] is None
    nf_points = [
        {"elevation_m": 25.0, "place_name": "Slough", "postcode": "SL1", "bedrock": "Chalk"},
        {"elevation_m": 40.0, "place_name": "Reading", "postcode": "RG1", "bedrock": "Clay"},
    ]
    nf_curv = [
        {
            "radius_min_m": 800,
            "radius_avg_m": 1000,
            "radius_avg_class": "Moderate",
            "mileage_from": "18m 00ch",
            "mileage_to": "18m 40ch",
        }
    ]
    cur_nf = FakeCursor(responses=[nf_points, nf_curv])
    nf_res = tm.find_network_fusion_for_elr(cur_nf, "MLN1", min_yd=1000, max_yd=2000)
    assert nf_res["summary"]["min_elevation_m"] == 25.0
    assert nf_res["summary"]["max_elevation_m"] == 40.0
    assert nf_res["summary"]["min_radius_m"] == 800
    assert nf_res["summary"]["tightest_curve"]["class"] == "Moderate"
    assert nf_res["summary"]["nearest_places"] == ["Slough", "Reading"]

    # Unbounded find_tps_cross_links and find_network_fusion_for_elr
    cur_tps_unb = FakeCursor(
        responses=[
            [{"code_id": "10", "label": "MLN1", "vanilla_text": "MLN1"}],
            [{"station_id": 1, "abbrev": "PAD"}],
            [{"cnt": 10}],
            [{"pk": 1, "edge_id": 100}],
            [{"pk": 2, "signal_id": 200}],
            [{"pk": 3, "description": "MLN1"}],
        ]
    )
    assert tm.find_tps_cross_links(cur_tps_unb, "MLN1")["tps_nodes_count"] == 10

    cur_nf_unb = FakeCursor(responses=[nf_points, nf_curv])
    assert tm.find_network_fusion_for_elr(cur_nf_unb, "MLN1")["summary"]["point_count"] == 2


def test_34xx_continuation_boost_and_error_paths(monkeypatch):
    """Covers _compute_34xx_continuation_boost, cross-ELR anchor projection, and DB error fallback."""
    c3402 = _make_centreline(1, "SWM2", "3402", start_m=1000.0, end_m=2000.0)
    c3412 = _make_centreline(2, "SWM2", "3412", start_m=1000.0, end_m=2000.0)
    monkeypatch.setattr(tm, "_find_34xx_continuation_tracks", lambda *_a: {"3412"})

    # Up Main -> prefix "11"
    boost = tm._compute_34xx_continuation_boost(
        FakeCursor(), "SWM2", [c3402, c3412], 1500.0, None, "Up Main", "up", None
    )
    assert boost == {"3412"}

    # Down Relief -> prefix "22"
    boost_dr = tm._compute_34xx_continuation_boost(
        FakeCursor(), "SWM2", [c3402, c3412], 1500.0, None, "Down Relief", "reversible", "down"
    )
    assert boost_dr == {"3412"}

    # Already covered by primary 1100 -> no boost needed
    c1100 = _make_centreline(3, "SWM2", "1100", start_m=1000.0, end_m=2000.0)
    assert (
        tm._compute_34xx_continuation_boost(
            FakeCursor(), "SWM2", [c3402, c3412, c1100], 1500.0, None, "Up Main", "up", None
        )
        == set()
    )

    # _fetch_primary_candidates unbounded + nearest fallback + override fetch
    cur_fb = FakeCursor(
        responses=[
            [],           # primary empty
            [c1100],      # nearest fallback -> extrapolated
            [c3412],      # explicit override fid=2
        ]
    )
    cands, extrap = tm._fetch_primary_candidates(cur_fb, "SWM2", 1500.0, None, None, 2, False)
    assert extrap is True
    assert {c["fid"] for c in cands} == {3, 2}

    # _build_geo_snap fallback to centreline midpoint when node_points is empty
    snap_mid = tm._build_geo_snap("SWM2", None, None, [], c1100, False)
    assert snap_mid["target_zoom"] == 0.15
    assert tm._build_geo_snap("SWM2", None, None, [], None, False) is None

    # Database error inside find_nwr_cross_links_for_tps degrades safely
    class BoomCursor:
        def execute(self, *_a, **_kw):
            raise RuntimeError("boom")

    assert tm.find_nwr_cross_links_for_tps(BoomCursor(), "MLN1", 1000.0)["single_match"] is False

