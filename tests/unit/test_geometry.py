"""Unit tests for backend/geometry.py."""

import pytest

import geometry
from tests.conftest import FakeCursor


pytestmark = pytest.mark.unit


def test_meters_to_miles_chains_cases():
    """Formats metres to miles/chains, handling None, zero, and 80ch rollover."""
    assert geometry.meters_to_miles_chains(None) is None
    assert geometry.meters_to_miles_chains(0) == "0m 00.0ch (0.000 km)"
    # 1609m -> 79.98ch rounds to 80.0ch -> rolls over to 1m 00.0ch
    assert geometry.meters_to_miles_chains(1609) == "1m 00.0ch (1.609 km)"
    assert geometry.meters_to_miles_chains(1000) == "0m 49.7ch (1.000 km)"


def test_mile_yards_to_total_yards():
    """Converts Network Rail mile.yards notation into integer total yards."""
    assert geometry.mile_yards_to_total_yards(None) == 0
    assert geometry.mile_yards_to_total_yards(28.1440) == 28 * 1760 + 1440
    assert geometry.mile_yards_to_total_yards(-1.0100) == 1860


def test_clean_rail_asset_name_variants():
    """Normalizes signals, points, buffer stops, and DB-prefixed names."""
    assert geometry.clean_rail_asset_name(None, "signal") == "Signal"
    assert geometry.clean_rail_asset_name(None, "points") == "Points"
    assert geometry.clean_rail_asset_name(None, "signal", is_bumper=True) == "Buffer Stop"

    assert geometry.clean_rail_asset_name("53195-207_SIG: T3533", "signal") == "Signal T3533"
    assert geometry.clean_rail_asset_name("Signal SOT433", "signal") == "Signal SOT433"
    assert geometry.clean_rail_asset_name("SIG:", "signal") == "Signal SIG:"

    assert geometry.clean_rail_asset_name("PTS: 402A", "points") == "Points 402A"
    assert geometry.clean_rail_asset_name("SIG: Points 12", "points") == "Points 12"
    assert geometry.clean_rail_asset_name("PTS:", "points") == "Points PTS:"

    assert geometry.clean_rail_asset_name("BUF: Plat 1", "signal", is_bumper=True) == "Buffer Stop Plat 1"
    assert geometry.clean_rail_asset_name("Buffer Stop", "signal", is_bumper=True) == "Buffer Stop"
    assert geometry.clean_rail_asset_name("End Buffer", "signal") == "End Buffer"


def test_interpolate_from_end_and_coords():
    """Interpolates points along polylines in both chainage orientations."""
    coords = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0]]
    edge_lengths = [100.0, 100.0]

    assert geometry.interpolate_from_end([], [], 10.0) is None
    assert geometry.interpolate_from_end([[5.0, 6.0]], [], 10.0) is None
    assert geometry.interpolate_from_end(coords, edge_lengths, 0.0) is None
    assert geometry.interpolate_from_end(coords, edge_lengths, 50.0) == [100.0, 50.0]
    assert geometry.interpolate_from_end(coords, edge_lengths, 150.0) == [50.0, 0.0]
    assert geometry.interpolate_from_end(coords, edge_lengths, 999.0) == [0.0, 0.0]
    assert geometry.interpolate_from_end(coords, [0.0, 0.0], 50.0) == [0.0, 0.0]

    # Polyline interpolation by chainage
    assert geometry.interpolate_pt_along_coords([], 0.0, 200.0, 100.0) is None
    assert geometry.interpolate_pt_along_coords([[1.0, 2.0]], 0.0, 200.0, 100.0) == [1.0, 2.0]
    assert geometry.interpolate_pt_along_coords(coords, 0.0, 200.0, 100.0) == [100.0, 0.0]
    assert geometry.interpolate_pt_along_coords(coords, 200.0, 0.0, 150.0) == [50.0, 0.0]
    assert geometry.interpolate_pt_along_coords(coords, 0.0, 200.0, -50.0) == [0.0, 0.0]
    assert geometry.interpolate_pt_along_coords(coords, 0.0, 200.0, 500.0) == [100.0, 100.0]
    assert geometry.interpolate_pt_along_coords(coords, 100.0, 100.0, 100.0) == [0.0, 0.0]
    assert geometry.interpolate_pt_along_coords([[5.0, 5.0], [5.0, 5.0]], 0.0, 100.0, 50.0) == [5.0, 5.0]


def test_slice_polyline_normal_reversed_and_edge_cases():
    """Slices a polyline to a sub-chainage interval, preserving geometry."""
    coords = [[0.0, 0.0], [100.0, 0.0], [200.0, 0.0]]
    assert geometry.slice_polyline([], 0.0, 200.0, 50.0, 150.0) == []
    assert geometry.slice_polyline(coords, 100.0, 100.0, 50.0, 150.0) == coords

    # Normal forward slice [50, 150]
    sliced = geometry.slice_polyline(coords, 0.0, 200.0, 50.0, 150.0)
    assert sliced[0] == [50.0, 0.0]
    assert sliced[-1] == [150.0, 0.0]
    assert [100.0, 0.0] in sliced

    # Reversed chainage (start > end)
    rev = geometry.slice_polyline(coords, 200.0, 0.0, 50.0, 150.0)
    assert rev[0] == [50.0, 0.0]
    assert rev[-1] == [150.0, 0.0]

    # Clamped out-of-range slice pins both ends to polyline end
    clamped = geometry.slice_polyline(coords, 0.0, 200.0, 500.0, 600.0)
    assert clamped == [[200.0, 0.0], [200.0, 0.0]]

    # Zero-length polyline returns coords unchanged
    zero_coords = [[1.0, 1.0], [1.0, 1.0]]
    assert geometry.slice_polyline(zero_coords, 0.0, 100.0, 20.0, 80.0) == zero_coords


def test_parse_traction_curves_explicit_and_davis():
    """Extracts explicit fsdiagram/srdiagvalue curves and synthesizes Davis curves."""
    tc = [
        {"tag": "srdiagvalue", "attrs": {"speed": "100", "retardation": "5000"}},
        {"tag": "srdiagvalue", "attrs": {"speed": "0", "retardation": "1000"}},
        {
            "tag": "fsdiagram",
            "attrs": {"id": "7", "electricprofilenameid": "3"},
            "children": [
                {"tag": "fsdiagvalue", "attrs": {"speed": "72", "force": "200"}},
                {"tag": "fsdiagvalue", "attrs": {"speed": "0", "force": "300"}},
            ],
        },
    ]
    res = geometry.parse_traction_curves({}, tc, decode_fn=lambda _c, cid: f"25kV-{cid}")
    assert len(res["sr_curve"]) == 2
    assert res["sr_curve"][0]["speed_kmh"] == 0.0
    assert len(res["fs_diagrams"]) == 1
    assert res["fs_diagrams"][0]["electric_profile"] == "25kV-3"
    assert res["max_force_kn"] == 300.0
    assert res["max_power_kw"] == 4000.0

    # Davis formula fallback when no srdiagvalue points exist
    res_davis = geometry.parse_traction_curves(
        {"coeffA": "1000", "coeffB": "10", "coeffC": "0.5", "maxallowedspeed": "120"},
        [],
    )
    assert len(res_davis["sr_curve"]) == 13
    assert res_davis["sr_curve"][0]["resistance_n"] == 1000.0


def test_resolve_node_path_details():
    """Resolves ordered TPS node IDs into coords, lengths, speeds, and turnouts."""
    empty = geometry.resolve_node_path_details(FakeCursor(), [])
    assert empty["coords"] == []
    assert empty["length_meters"] == 0
    assert empty["turnout_nodes"] == []

    nodes_rows = [
        {"node_id": 1, "netx": 10.0, "nety": 20.0, "km_value": 1000, "km_region_id": 1},
        {"node_id": 2, "netx": 30.0, "nety": 40.0, "km_value": 1500, "km_region_id": 1},
        {"node_id": 3, "netx": 50.0, "nety": 60.0, "km_value": 2000, "km_region_id": 1},
    ]
    edges_rows = [
        {"pk": 10, "edge_id": 101, "start_node_id": 1, "end_node_id": 2, "length": 500, "vmax": 160, "electrified": 1, "gradient": 0.0, "velocity_profiles": [{"profileid": 16, "vmax": 200}]},
        {"pk": 11, "edge_id": 102, "start_node_id": 2, "end_node_id": 3, "length": 400, "vmax": 120, "electrified": 1, "gradient": 0.0, "velocity_profiles": []},
        {"pk": 12, "edge_id": 103, "start_node_id": 2, "end_node_id": 99, "length": 200, "vmax": 80, "electrified": 0, "gradient": 0.0, "velocity_profiles": []},
    ]
    cur = FakeCursor(responses=[nodes_rows, edges_rows])
    details = geometry.resolve_node_path_details(
        cur, [1, 2, 3], decode_fn=lambda _cat, pid: "MU" if pid == 16 else None
    )
    assert details["coords"] == [[10.0, 20.0], [30.0, 40.0], [50.0, 60.0]]
    assert details["length_meters"] == 900
    assert details["min_vmax"] == 120
    assert details["max_vmax"] == 160
    assert len(details["turnout_nodes"]) == 1
    assert details["turnout_nodes"][0]["node_id"] == 2
    assert len(details["speed_profile"]) == 2
    assert details["speed_profile"][0]["start_m"] == 0
    assert details["speed_profile"][0]["end_m"] == 500
    assert details["speed_profile"][0]["differential_speeds"][0]["profile_name"] == "MU"
    assert details["speed_profile"][1]["vmax_kmh"] == 120

    # Verify consecutive edges with identical speed merge into a single step
    cur_merge = FakeCursor(
        responses=[
            nodes_rows,
            [
                {"pk": 10, "edge_id": 101, "start_node_id": 1, "end_node_id": 2, "length": 300, "vmax": 160, "electrified": 1, "gradient": 0.0, "velocity_profiles": []},
                {"pk": 11, "edge_id": 102, "start_node_id": 2, "end_node_id": 3, "length": 200, "vmax": 160, "electrified": 1, "gradient": 0.0, "velocity_profiles": []},
            ],
        ]
    )
    merged = geometry.resolve_node_path_details(cur_merge, [1, 2, 3])
    assert len(merged["speed_profile"]) == 1
    assert merged["speed_profile"][0]["length_m"] == 500
