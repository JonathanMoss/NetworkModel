"""Unit tests for backend/possession_routing.py (TPS & NWR straight-route Dijkstra)."""

import pytest

import possession_routing as pr
from tests.conftest import FakeCursor

pytestmark = pytest.mark.unit


def test_tps_straight_path_avoids_crossover_shortcut():
    """Prefers the straight-through road over a shorter/fewer-hop crossover detour."""
    # Road A (y=0): 1 -> 2 -> 3 -> 4 -> 5 (4 hops, straight along y=0, total length 400)
    # Crossover detour via Road B (y=40): 2 -> 10 (diagonal) -> 4 (diagonal), length 100 each!
    # Unweighted BFS or naive shortest-length Dijkstra would jump 2 -> 10 -> 4;
    # straight-route Dijkstra must stay on Road A: 1 -> 2 -> 3 -> 4 -> 5!
    edges = [
        {"start_node_id": 1, "end_node_id": 2, "x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 0.0, "length": 100},
        {"start_node_id": 2, "end_node_id": 3, "x0": 100.0, "y0": 0.0, "x1": 200.0, "y1": 0.0, "length": 100},
        {"start_node_id": 3, "end_node_id": 4, "x0": 200.0, "y0": 0.0, "x1": 300.0, "y1": 0.0, "length": 100},
        {"start_node_id": 4, "end_node_id": 5, "x0": 300.0, "y0": 0.0, "x1": 400.0, "y1": 0.0, "length": 100},
        # Diverging crossover to Road B (shorter length to tempt naive Dijkstra)
        {"start_node_id": 2, "end_node_id": 10, "x0": 100.0, "y0": 0.0, "x1": 200.0, "y1": 40.0, "length": 50},
        {"start_node_id": 10, "end_node_id": 4, "x0": 200.0, "y0": 40.0, "x1": 300.0, "y1": 0.0, "length": 50},
    ]
    cur = FakeCursor(
        responses=[
            [{"node_id": 1, "netx": 0.0, "nety": 0.0}, {"node_id": 5, "netx": 400.0, "nety": 0.0}],
            edges,
        ]
    )
    coords = pr.find_tps_straight_path(cur, 1, 5)
    assert coords == [[0.0, 0.0], [100.0, 0.0], [200.0, 0.0], [300.0, 0.0], [400.0, 0.0]]


def test_tps_straight_path_same_node_missing_and_reversal_blocked():
    """Handles same-node, missing-node, and rejects physically impossible trailing reversals."""
    assert pr.find_tps_straight_path(FakeCursor(), None, 1) == []
    cur_same = FakeCursor(responses=[[{"netx": 10.0, "nety": 20.0}]])
    assert pr.find_tps_straight_path(cur_same, 1, 1) == [[10.0, 20.0]]

    # Missing endpoint node
    cur_miss = FakeCursor(responses=[[{"node_id": 1, "netx": 0.0, "nety": 0.0}]])
    assert pr.find_tps_straight_path(cur_miss, 1, 99) == []

    # Trailing switch reversal: 1(0,0) -> 2(100,0), and 2(100,0) -> 3(20,5) (almost 180° back)
    # Reaching 3 from 1 via 2 requires reversing direction (dot < -0.15) -> must be rejected!
    rev_edges = [
        {"start_node_id": 1, "end_node_id": 2, "x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 0.0, "length": 100},
        {"start_node_id": 2, "end_node_id": 3, "x0": 100.0, "y0": 0.0, "x1": 20.0, "y1": 5.0, "length": 80},
    ]
    cur_rev = FakeCursor(
        responses=[
            [{"node_id": 1, "netx": 0.0, "nety": 0.0}, {"node_id": 3, "netx": 20.0, "nety": 5.0}],
            rev_edges,
            rev_edges,
        ]
    )
    assert pr.find_tps_straight_path(cur_rev, 1, 3) == []


def test_nwr_straight_path_same_segment_and_multi_segment_no_crossover():
    """Clips NWR centrelines cleanly on same segment and across ELR boundaries without crossover hops."""
    # 1. Same centreline segment (both forward and reverse)
    seg_same = {
        "fid": 10, "elr": "MLN1", "track_id": "1100",
        "start_node_id": "N1", "end_node_id": "N2",
        "start_meters": 0, "end_meters": 1000, "length_meters": 1000,
        "coords": [[500000.0, 180000.0], [500500.0, 180000.0], [501000.0, 180000.0]],
    }
    cur_same = FakeCursor(responses=[[seg_same]])
    ways = pr.find_nwr_straight_path(
        cur_same,
        {"nwr_x": 500200.0, "nwr_y": 180000.0, "elr": "MLN1", "track_id": "1100"},
        {"nwr_x": 500800.0, "nwr_y": 180000.0, "elr": "MLN1", "track_id": "1100"},
    )
    assert len(ways) == 1
    assert ways[0]["coords"][0] == [500200.0, 180000.0]
    assert ways[0]["coords"][-1] == [500800.0, 180000.0]

    # 2. Multi-segment across ELR boundary (MLN1 -> MLN2) with a tempting 1700 crossover detour
    rows = [
        {
            "fid": 1, "elr": "MLN1", "track_id": "1100",
            "start_node_id": "N1", "end_node_id": "N2",
            "start_meters": 0, "end_meters": 500, "length_meters": 500,
            "coords": [[500000.0, 180000.0], [500500.0, 180000.0]],
        },
        {
            "fid": 2, "elr": "MLN2", "track_id": "1100",
            "start_node_id": "N2", "end_node_id": "N3",
            "start_meters": 0, "end_meters": 500, "length_meters": 500,
            "coords": [[500500.0, 180000.0], [501000.0, 180000.0]],
        },
        {
            "fid": 3, "elr": "MLN2", "track_id": "1100",
            "start_node_id": "N3", "end_node_id": "N4",
            "start_meters": 500, "end_meters": 1000, "length_meters": 500,
            "coords": [[501000.0, 180000.0], [501500.0, 180000.0]],
        },
        # Crossover detour N2 -> N3 via 1700 (crossover)
        {
            "fid": 99, "elr": "MLN2", "track_id": "1700",
            "start_node_id": "N2", "end_node_id": "N3",
            "start_meters": 0, "end_meters": 100, "length_meters": 100,
            "coords": [[500500.0, 180000.0], [500750.0, 180080.0], [501000.0, 180000.0]],
        },
    ]
    cur_multi = FakeCursor(responses=[rows])
    ways_multi = pr.find_nwr_straight_path(
        cur_multi,
        {"nwr_x": 500100.0, "nwr_y": 180000.0, "elr": "MLN1", "track_id": "1100"},
        {"nwr_x": 501400.0, "nwr_y": 180000.0, "elr": "MLN2", "track_id": "1100"},
    )
    assert [w["fid"] for w in ways_multi] == [1, 2, 3]
    assert ways_multi[0]["coords"][0] == [500100.0, 180000.0]
    assert ways_multi[-1]["coords"][-1] == [501400.0, 180000.0]


def test_nwr_straight_path_empty_and_helpers():
    """Covers empty inputs, reverse slicing, and crossover track detection."""
    assert pr.find_nwr_straight_path(FakeCursor(), {"nwr_x": None}, {"nwr_x": 1.0}) == []
    assert pr.find_nwr_straight_path(FakeCursor(responses=[[]]), {"nwr_x": 0.0, "nwr_y": 0.0}, {"nwr_x": 10.0, "nwr_y": 0.0}) == []
    assert pr._is_nwr_crossover("1700") is True  # pylint: disable=protected-access
    assert pr._is_nwr_crossover("2701") is True  # pylint: disable=protected-access
    assert pr._is_nwr_crossover("1100") is False  # pylint: disable=protected-access
    assert pr._slice_polyline_by_dist([[0.0, 0.0], [100.0, 0.0]], 80.0, 20.0) == [[80.0, 0.0], [20.0, 0.0]]  # pylint: disable=protected-access


def test_nwr_straight_path_strictly_prefers_matched_track_id_over_shorter_parallel_road():
    """Never picks an adjacent parallel road (e.g. 2100 Down Fast) and stitches NULL-node ELR boundaries."""
    rows = [
        # Up Fast (1100) — outer curve, with NULL node IDs at the LEC5/LEC4 ELR boundary!
        {
            "fid": 11, "elr": "LEC5", "track_id": "1100",
            "start_node_id": "U1", "end_node_id": None,
            "start_meters": 0, "end_meters": 505, "length_meters": 505,
            "coords": [[371000.0, 354800.0], [371500.0, 354800.0]],
        },
        {
            "fid": 12, "elr": "LEC4", "track_id": "1100",
            "start_node_id": None, "end_node_id": "U3",
            "start_meters": 505, "end_meters": 1010, "length_meters": 505,
            "coords": [[371500.0, 354800.0], [372000.0, 354800.0]],
        },
        # Down Fast (2100) — 3.5m adjacent, slightly shorter (990m)
        {
            "fid": 21, "elr": "LEC5", "track_id": "2100",
            "start_node_id": "D1", "end_node_id": "D2",
            "start_meters": 0, "end_meters": 495, "length_meters": 495,
            "coords": [[371000.0, 354803.5], [371495.0, 354803.5]],
        },
        {
            "fid": 22, "elr": "LEC4", "track_id": "2100",
            "start_node_id": "D2", "end_node_id": "D3",
            "start_meters": 495, "end_meters": 990, "length_meters": 495,
            "coords": [[371495.0, 354803.5], [371990.0, 354803.5]],
        },
    ]
    cur = FakeCursor(responses=[rows])
    ways = pr.find_nwr_straight_path(
        cur,
        {"nwr_x": 371100.0, "nwr_y": 354800.0, "elr": "LEC5", "track_id": "1100"},
        {"nwr_x": 371900.0, "nwr_y": 354800.0, "elr": "LEC4", "track_id": "1100"},
    )
    assert [w["track_id"] for w in ways] == ["1100", "1100"]
    assert [w["fid"] for w in ways] == [11, 12]


def test_find_tps_and_nwr_internal_crossovers():
    """Includes crossovers completely between blocked lines and excludes open-line connections."""
    # TPS: Up line (y=0: 1->2->3) and Down line (y=12: 4->5->6) are BOTH blocked.
    # Crossover 2->99->5 (2 hops via degree-2 node 99) connects two blocked lines -> INCLUDED!
    # Crossover 3->7 (node 7 is on an open line at y=24) -> EXCLUDED!
    blocked_tps = [
        {"coords": [[0.0, 0.0], [50.0, 0.0], [100.0, 0.0]]},
        {"coords": [[0.0, 12.0], [50.0, 12.0], [100.0, 12.0]]},
    ]
    tps_edges = [
        {"start_node_id": 1, "end_node_id": 2, "x0": 0.0, "y0": 0.0, "x1": 50.0, "y1": 0.0, "length": 50},
        {"start_node_id": 2, "end_node_id": 3, "x0": 50.0, "y0": 0.0, "x1": 100.0, "y1": 0.0, "length": 50},
        {"start_node_id": 4, "end_node_id": 5, "x0": 0.0, "y0": 12.0, "x1": 50.0, "y1": 12.0, "length": 50},
        {"start_node_id": 5, "end_node_id": 6, "x0": 50.0, "y0": 12.0, "x1": 100.0, "y1": 12.0, "length": 50},
        # Internal crossover 2 -> 99 -> 5
        {"start_node_id": 2, "end_node_id": 99, "x0": 50.0, "y0": 0.0, "x1": 55.0, "y1": 6.0, "length": 15},
        {"start_node_id": 99, "end_node_id": 5, "x0": 55.0, "y0": 6.0, "x1": 50.0, "y1": 12.0, "length": 15},
        # External crossover to open line (node 7 at y=24)
        {"start_node_id": 3, "end_node_id": 7, "x0": 100.0, "y0": 0.0, "x1": 120.0, "y1": 24.0, "length": 30},
    ]
    tps_xovers = pr.find_tps_internal_crossovers(FakeCursor(responses=[tps_edges]), blocked_tps)
    assert len(tps_xovers) == 1
    assert tps_xovers[0]["is_crossover"] is True
    assert tps_xovers[0]["coords"] == [[50.0, 0.0], [55.0, 6.0], [50.0, 12.0]]

    # NWR: Blocked Up (fid=1, y=0) and Down (fid=2, y=4).
    # Internal crossover fid=10 (3700) connects y=0 to y=4 -> INCLUDED!
    # External crossover fid=11 (3700) connects y=4 to open line y=12 -> EXCLUDED!
    blocked_nwr = [
        {"fid": 1, "line_name": "Up", "coords": [[259000.0, 665500.0], [259200.0, 665500.0]]},
        {"fid": 2, "line_name": "Down", "coords": [[259000.0, 665504.0], [259200.0, 665504.0]]},
    ]
    nwr_candidates = [
        {"fid": 1, "elr": "EGM1", "track_id": "1100", "length_meters": 200.0, "coords": [[259000.0, 665500.0], [259200.0, 665500.0]]},
        {"fid": 10, "elr": "EGM1", "track_id": "3700", "length_meters": 60.0, "coords": [[259050.0, 665500.0], [259110.0, 665504.0]]},
        {"fid": 11, "elr": "EGM1", "track_id": "3700", "length_meters": 60.0, "coords": [[259050.0, 665504.0], [259110.0, 665512.0]]},
    ]
    nwr_xovers = pr.find_nwr_internal_crossovers(FakeCursor(responses=[nwr_candidates]), blocked_nwr)
    assert [w["fid"] for w in nwr_xovers] == [10]
    assert nwr_xovers[0]["is_crossover"] is True

