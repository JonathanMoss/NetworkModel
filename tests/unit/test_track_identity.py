"""Unit tests for backend/track_identity.py."""

import pytest

import track_identity as ti
from tests.conftest import FakeCursor


pytestmark = pytest.mark.unit


def test_load_elr_polarity_cache_and_infer_direction(monkeypatch):
    """Computes per-ELR mileage polarity from vote rows and infers direction."""
    rows = [
        {"km_region_id": 10, "normal_votes": 15, "reverse_votes": 2},
        {"km_region_id": 20, "normal_votes": 1, "reverse_votes": 12},
        {"km_region_id": 30, "normal_votes": 5, "reverse_votes": 5},
    ]
    cur = FakeCursor(responses=[rows])
    monkeypatch.setattr(ti, "decode_code", lambda _cat, cid: {10: "MLN1", 20: "CMD2"}.get(cid))

    pol = ti.load_elr_polarity_cache(cur)
    assert pol["MLN1"] == 1
    assert pol["CMD2"] == -1
    assert ti.load_elr_polarity_cache(FakeCursor()) is pol

    assert ti.infer_direction_from_km_delta(cur, "MLN1", 1000, 1500) == "down"
    assert ti.infer_direction_from_km_delta(cur, "MLN1", 1500, 1000) == "up"
    assert ti.infer_direction_from_km_delta(cur, "CMD2", 1000, 1500) == "up"
    assert ti.infer_direction_from_km_delta(cur, "MLN1", 1000, 1000) is None
    assert ti.infer_direction_from_km_delta(cur, None, 1000, 1500) == "down"


def test_extract_direction_from_tokens_variants():
    """Extracts up, down, reversible, or None from name/desc/SA/ROP fields."""
    assert ti._extract_direction_from_tokens("D-DML", None, None, None) == "down"
    assert ti._extract_direction_from_tokens("U-UML", None, None, None) == "up"
    assert ti._extract_direction_from_tokens("E-DF", None, None, None) == "down"
    assert ti._extract_direction_from_tokens("E-UF", None, None, None) == "up"
    assert ti._extract_direction_from_tokens("U/D Loop", None, None, None) == "reversible"
    assert ti._extract_direction_from_tokens(None, "Bi-Dir Platform 1", None, None) == "reversible"
    assert ti._extract_direction_from_tokens(None, "Down & Up Relief", None, None) == "reversible"
    assert ti._extract_direction_from_tokens(None, "Up Fast", None, None) == "up"
    assert ti._extract_direction_from_tokens(None, "Down Slow", None, None) == "down"
    assert ti._extract_direction_from_tokens(None, None, "DFL", None) == "down"
    assert ti._extract_direction_from_tokens(None, None, "UFL", None) == "up"
    assert ti._extract_direction_from_tokens(None, None, None, "REV") == "reversible"
    assert ti._extract_direction_from_tokens("Platform 2", None, None, None) is None
    assert ti._extract_direction_from_tokens(None, None, None, None) is None
    # Wrong-direction phrasing, SA override codes, and bare track prefixes
    assert ti._extract_direction_from_tokens(None, "Down Up Slow", None, None) == "up"
    assert ti._extract_direction_from_tokens(None, "Up Down Fast", None, None) == "down"
    assert ti._extract_direction_from_tokens(None, None, "DUS", None) == "up"
    assert ti._extract_direction_from_tokens(None, None, "UDS", None) == "reversible"
    assert ti._extract_direction_from_tokens("D-1", None, None, None) == "down"
    assert ti._extract_direction_from_tokens("U-2", None, None, None) == "up"


def test_extract_platform_number_variants():
    """Extracts integer platform numbers while rejecting non-platform codes."""
    assert ti._extract_platform_number("Platform 14", None, None, None) == 14
    assert ti._extract_platform_number("P1: SLOUGH", None, None, None) == 1
    assert ti._extract_platform_number("Plat 3A", None, None, None) == 3
    assert ti._extract_platform_number("Bay 5", None, None, None) == 5
    assert ti._extract_platform_number("U-P4", None, None, None) == 4
    assert ti._extract_platform_number("1A", "1A", None, None) == 1
    assert ti._extract_platform_number("Down Fast", None, None, None) is None
    assert ti._extract_platform_number(None, None, None, None) is None
    assert (
        ti._extract_platform_number("3", "3", None, None, station_longname="Acton Yard")
        == 3
    )
    assert ti._extract_platform_number("3", None, None, None) is None
    assert ti._extract_platform_number("D-12", None, None, None) == 12
    assert ti._extract_platform_number("Siding", None, "Siding", None, station_longname="GLASGOW NO.1 SDG") == 1
    assert ti._extract_platform_number("SDG", None, "SDG", None, station_longname="Acton Siding 4") == 4


def test_parse_station_track_rows_directed_and_bidirectional(monkeypatch):
    """Parses station_tracks rows into direction, secondary_dir, hint, and platform."""
    monkeypatch.setattr(
        ti,
        "decode_code",
        lambda cat, cid: {
            "salinecodedesc": {1: "UM", 2: "Not Set"},
            "roplinecodedesc": {1: "UM", 2: "_"},
        }
        .get(cat, {})
        .get(cid),
    )

    assert ti._parse_station_track_rows([])["direction_hint"] is None

    # Single directed track + BUS row penalty + sibling SA/ROP inheritance
    res1 = ti._parse_station_track_rows(
        [
            {"name": "BUS", "longname": "BUS", "description": "Bus Stop", "node_ids": [1, 2]},
            {"name": "D-DF", "longname": "DF", "description": "Down Fast", "node_ids": [1, 2], "sa_line_code": 2, "rop_line_code": 2},
            {"name": "D-DF", "longname": "DF", "description": None, "node_ids": [1], "sa_line_code": 1, "rop_line_code": 1},
        ],
        start_node_id=1,
        end_node_id=2,
    )
    assert res1["direction_hint"] == "down"
    assert res1["secondary_dir"] == "up"  # inherited explicit SA code UM

    # Opposing Up & Down tracks on identical longname -> promoted to reversible
    res2 = ti._parse_station_track_rows(
        [
            {"name": "P1", "longname": "PLAT 1", "description": "Up Main", "node_ids": [10, 20], "sa_line_code": 1},
            {"name": "P1", "longname": "PLAT 1", "description": "Down Main", "node_ids": [20, 10], "sa_line_code": 1},
        ],
        start_node_id=20,
        end_node_id=10,
    )
    assert res2["direction_hint"] == "reversible"
    assert res2["secondary_dir"] == "up"
    assert res2["platform_num"] == 1

    # Yard/siding station appends Siding hint when no line class is present
    res3 = ti._parse_station_track_rows(
        [
            {
                "name": "TRK1",
                "longname": "T1",
                "description": "Road 1",
                "node_ids": [5],
                "station_longname": "Crewe Carriage Siding",
            }
        ]
    )
    assert "Siding" in (res3["track_name_hint"] or "")


def test_collinear_turnout_and_straight_through_neighbors():
    """Selects collinear turnout leg (cos_sim >= 0.92) and rejects sharp diverging leg."""
    node_xy = {
        1: (0.0, 0.0),
        2: (100.0, 0.0),
        3: (200.0, 0.0),
        4: (150.0, 80.0),
    }
    pair = ti._find_collinear_turnout_pair(100.0, 0.0, {1, 3, 4}, node_xy, set())
    assert set(pair) == {1, 3}

    nxt = ti._choose_straight_through_neighbors(2, 1, {1, 3, 4}, node_xy, {1, 2}, allow_hop1_pair=False)
    assert nxt == [3]

    nxt_apex = ti._choose_straight_through_neighbors(2, None, {1, 3, 4}, node_xy, {2}, allow_hop1_pair=True)
    assert set(nxt_apex) == {1, 3}

    nxt_plain = ti._choose_straight_through_neighbors(2, 1, {1, 3}, node_xy, {1, 2}, allow_hop1_pair=False)
    assert nxt_plain == [3]

    # Missing coords gracefully returns []
    assert ti._choose_straight_through_neighbors(2, 1, {1, 3, 4}, {}, {1, 2}, allow_hop1_pair=False) == []


def test_resolve_reversible_through_line_dir(monkeypatch):
    """Walks straight-through out of a bi-dir platform to find its physical line direction."""
    monkeypatch.setattr(ti, "decode_code", lambda *_a: None)
    assert ti._resolve_reversible_through_line_dir(FakeCursor(), [], None, None) is None

    # Discovers unanimous Down Main on hop 1
    cur = FakeCursor(
        responses=[
            [{"start_node_id": 1, "end_node_id": 2}],
            [{"name": "D-DM", "longname": "DM", "description": "Down Main", "sa_line_code": None, "node_ids": [2, 3]}],
        ]
    )
    assert ti._resolve_reversible_through_line_dir(cur, [1], None, None, max_hops=3) == "down"

    # Conflicting Up + Down running lines -> returns None
    cur_conflict = FakeCursor(
        responses=[
            [{"start_node_id": 1, "end_node_id": 2}, {"start_node_id": 1, "end_node_id": 3}],
            [
                {"name": "U-UM", "longname": "UM", "description": "Up Main", "sa_line_code": None, "node_ids": [2]},
                {"name": "D-DM", "longname": "DM", "description": "Down Main", "sa_line_code": None, "node_ids": [3]},
            ],
        ]
    )
    assert ti._resolve_reversible_through_line_dir(cur_conflict, [1], 10, 1, max_hops=3) is None

    # Turnout (valency >= 3) triggers node coordinate lookup and skips platform/reversible rows
    cur_turnout = FakeCursor(
        responses=[
            [
                {"start_node_id": 2, "end_node_id": 1},
                {"start_node_id": 2, "end_node_id": 3},
                {"start_node_id": 2, "end_node_id": 4},
            ],
            [
                {"node_id": 1, "netx": 0.0, "nety": 0.0},
                {"node_id": 2, "netx": 100.0, "nety": 0.0},
                {"node_id": 3, "netx": 200.0, "nety": 0.0},
                {"node_id": 4, "netx": 150.0, "nety": 80.0},
            ],
            [
                {"name": "P2", "longname": "2", "description": "Platform 2", "sa_line_code": None, "node_ids": [3]},
                {"name": "REV", "longname": "R", "description": "Up and Down Main", "sa_line_code": None, "node_ids": [3]},
                {"name": "U-UM", "longname": "UM", "description": "Up Main", "sa_line_code": None, "node_ids": [3]},
            ],
        ]
    )
    assert ti._resolve_reversible_through_line_dir(cur_turnout, [1, 2], 1, 2, max_hops=2) == "up"

    # Dead-end frontier breaks cleanly and returns None
    cur_dead = FakeCursor(responses=[[]])
    assert ti._resolve_reversible_through_line_dir(cur_dead, [99], None, None, max_hops=2) is None


def test_resolve_tps_track_identity_all_stages(monkeypatch):
    """Exercises all 4 resolution stages + dead-end spur & opposing-signal bi-dir promotion."""
    monkeypatch.setattr(ti, "decode_code", lambda *_a: None)

    # Empty nodes -> fallback to mileage polarity
    monkeypatch.setattr(ti, "infer_direction_from_km_delta", lambda *_a: "up")
    res_empty = ti.resolve_tps_track_identity(
        FakeCursor(), [], elr="MLN1", start_km=2000, end_km=1000
    )
    assert res_empty["direction_hint"] == "up"
    assert res_empty["source"] == "ELR MLN1 Mileage Polarity"

    # Stage 1: Direct station_tracks membership (reversible platform triggers through-line walk)
    monkeypatch.setattr(ti, "_resolve_reversible_through_line_dir", lambda *_a, **_kw: "up")
    cur_s1 = FakeCursor(
        responses=[
            [
                {
                    "name": "P2",
                    "longname": "P2",
                    "description": "Bi-Dir Platform 2",
                    "node_ids": [101],
                    "sa_line_code": None,
                    "rop_line_code": None,
                    "station_longname": "Reading",
                }
            ],
        ]
    )
    res_s1 = ti.resolve_tps_track_identity(cur_s1, [101], elr="MLN1")
    assert res_s1["direction_hint"] == "reversible"
    assert res_s1["secondary_dir"] == "up"
    assert res_s1["platform_num"] == 2
    assert "TPS Track" in res_s1["source"]

    # Stage 2: Direct routes membership (with SA line code)
    monkeypatch.setattr(
        ti,
        "decode_code",
        lambda cat, cid: "USL" if (cat == "salinecodedesc" and cid == 9) else None,
    )
    cur_s2 = FakeCursor(
        responses=[
            [],  # stage 1 station_tracks empty
            [{"route_id": 1, "rop_line_code_id": None, "sa_line_code_id": 9, "node_ids": [202]}],
        ]
    )
    res_s2 = ti.resolve_tps_track_identity(cur_s2, [202], elr="MLN1")
    assert res_s2["direction_hint"] == "up"
    assert "TPS Route" in res_s2["source"]

    # Stage 3: Bounded graph walk finds directed track + opposing signal promotes to reversible
    monkeypatch.setattr(ti, "decode_code", lambda *_a: None)
    cur_s3 = FakeCursor(
        responses=[
            [],  # stage 1
            [],  # stage 2
            [{"start_node_id": 301, "end_node_id": 302}],  # hop 1 edges
            [
                {
                    "name": "U-UF",
                    "longname": "UF",
                    "description": "Up Fast",
                    "node_ids": [302],
                    "sa_line_code": None,
                    "rop_line_code": None,
                    "station_longname": "Slough",
                }
            ],
            [{"?column?": 1}],  # opposing signal found -> reversible
        ]
    )
    res_s3 = ti.resolve_tps_track_identity(cur_s3, [300, 301], start_node_id=300, end_node_id=301, elr="MLN1")
    assert res_s3["direction_hint"] == "reversible"
    assert res_s3["secondary_dir"] == "up"
    assert "TPS Track Walk" in res_s3["source"]

    # Stage 3b: Dead-end valency-1 spur detection when walk finds nothing
    monkeypatch.setattr(ti, "infer_direction_from_km_delta", lambda *_a: None)
    cur_s3b = FakeCursor(
        responses=[
            [],  # stage 1
            [],  # stage 2
            [],  # hop 1 edges empty -> exit walk
            [{"start_node_id": 401, "end_node_id": 402}],  # valency-1 endpoint
        ]
    )
    res_s3b = ti.resolve_tps_track_identity(cur_s3b, [401], start_node_id=401, end_node_id=None, elr="MLN1")
    assert res_s3b["track_name_hint"] == "Dead-End Bay"
    assert res_s3b["source"] == "TPS Dead-End Spur"

    # Partial hint from undirected Stage 1 track + turnout coordinate walk + final None fallback
    cur_partial = FakeCursor(
        responses=[
            [{"name": "P4", "longname": "4", "description": "Platform 4", "node_ids": [501], "sa_line_code": None, "rop_line_code": None, "station_longname": "Slough"}],
            [],  # stage 2 routes empty
            [
                {"start_node_id": 502, "end_node_id": 501},
                {"start_node_id": 502, "end_node_id": 503},
                {"start_node_id": 502, "end_node_id": 504},
            ],
            [
                {"node_id": 501, "netx": 0.0, "nety": 0.0},
                {"node_id": 502, "netx": 100.0, "nety": 0.0},
                {"node_id": 503, "netx": 200.0, "nety": 0.0},
                {"node_id": 504, "netx": 150.0, "nety": 80.0},
            ],
            [],  # walk station_tracks empty
            [],  # hop 2 edges empty
        ]
    )
    res_partial = ti.resolve_tps_track_identity(cur_partial, [501, 502], start_node_id=501, end_node_id=502, elr="MLN1")
    assert res_partial["direction_hint"] is None
    assert res_partial["platform_num"] == 4

    # Completely unresolved returns all-None dict
    cur_none = FakeCursor(responses=[[], [], []])
    res_none = ti.resolve_tps_track_identity(cur_none, [601], elr="MLN1")
    assert res_none == {"direction_hint": None, "track_name_hint": None, "platform_num": None, "source": None}
