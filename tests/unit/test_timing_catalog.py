"""Unit tests for backend/timing_catalog.py."""

import pytest

import timing_catalog
from tests.conftest import FakeConnection, FakeCursor


pytestmark = pytest.mark.unit


def test_get_timing_catalog_builds_and_caches(monkeypatch):
    """Builds complete timing catalog from DB tables and memoizes result."""
    traction_rows = [
        {
            "entity_id": "101",
            "name": "Class 800",
            "raw_attrs": {
                "desc": "IET Bi-mode",
                "type": "1",
                "motortype": "2",
                "maxallowedspeed": "200",
                "weight": "400",
                "length": "260",
                "defretardation": "0.9",
                "coeffA": "1200",
            },
            "raw_children": [
                {
                    "tag": "fsdiagram",
                    "attrs": {"id": "1"},
                    "children": [{"tag": "fsdiagvalue", "attrs": {"speed": "50", "force": "250"}}],
                }
            ],
        }
    ]
    coach_rows = [
        {
            "entity_id": "201",
            "name": "Mk4 Coach",
            "raw_attrs": {"desc": "Trailer", "coachtypeid": "5", "maxspeed": "225", "weight": "42", "length": "23"},
        }
    ]
    model_train_rows = [
        {
            "entity_id": "301",
            "name": "IET 9-car",
            "raw_attrs": {
                "desc": "9-car Express",
                "veloprofileid": "7",
                "traintypeid": "2",
                "maxspeed": "200",
                "length": "234",
                "weight": "430",
            },
            "raw_children": [
                {"tag": "tractionunit", "attrs": {"id": "101"}},
                {"tag": "coach", "attrs": {"id": "201", "count": "2"}},
            ],
        }
    ]
    station_bank_rows = [
        {
            "entity_id": "401",
            "name": "PADB",
            "raw_attrs": {"description": "Paddington Bank", "lastmodified": "2026-01-01"},
            "raw_children": [{"tag": "stationbankid", "attrs": {"stationbankid": "501"}}],
        }
    ]
    station_bank_sections = [
        {
            "entity_id": "501",
            "name": "SEC1",
            "raw_attrs": {"description": "GWML Section"},
            "raw_children": [
                {"tag": "stationbankstation", "attrs": {"id": "10"}},
                {"tag": "stationbankstation", "attrs": {"id": "10"}},
            ],
        }
    ]
    timing_roles_rows = [{"station_type": 1, "cnt": 100}]
    planning_cat_rows = [{"station_category": 2, "cnt": 50}]

    cur = FakeCursor(
        responses=[
            traction_rows,
            coach_rows,
            model_train_rows,
            station_bank_rows,
            station_bank_sections,
            timing_roles_rows,
            planning_cat_rows,
        ]
    )
    calls = {"n": 0}

    def fake_get_conn():
        calls["n"] += 1
        return FakeConnection(cur)

    monkeypatch.setattr(timing_catalog, "get_conn", fake_get_conn)
    monkeypatch.setattr(
        timing_catalog,
        "load_lookup_cache",
        lambda: {
            "veloprofiledesc": {
                "0": {"label": "None", "vanilla_text": "", "description": ""},
                "7": {"label": "HST", "vanilla_text": "HST 125", "description": "Fast"},
            },
            "traintype": {
                "2": {"label": "EXP", "vanilla_text": "Express", "description": "Express Passenger"},
            },
        },
    )
    monkeypatch.setattr(
        timing_catalog,
        "decode_code",
        lambda cat, cid: f"{cat}:{cid}" if cid else None,
    )

    cat1 = timing_catalog.get_timing_catalog()
    cat2 = timing_catalog.get_timing_catalog()
    assert cat1 is cat2
    assert calls["n"] == 1

    assert cat1["counts"]["traction_units"] == 1
    assert cat1["counts"]["coaches"] == 1
    assert cat1["counts"]["model_trains"] == 1
    assert cat1["counts"]["velocity_profiles"] == 1
    assert cat1["counts"]["train_types"] == 1
    assert cat1["counts"]["station_banks"] == 1
    assert "10" in cat1["station_to_banks"]
    assert cat1["station_to_banks"]["10"][0]["code"] == "PADB"
    assert cat1["timing_roles"][0]["count"] == 100
    assert cat1["planning_categories"][0]["count"] == 50
