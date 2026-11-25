"""BDD step definitions for tests/bdd/features/asset_inspection.feature."""

from fastapi.testclient import TestClient
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

import main
from routers import assets as assets_router
from routers import nwr as nwr_router
from tests.conftest import FakeConnection, FakeCursor

pytestmark = pytest.mark.bdd

scenarios("features/asset_inspection.feature")


@pytest.fixture
def insp_ctx():
    """Per-scenario state bag for asset inspection BDD tests."""
    return {}


@given(parsers.parse('a mocked asset inspector database for graphictext {pk:d} with label "{label}"'))
def given_graphictext_db(insp_ctx, pk, label, monkeypatch):
    """Configure mocked DB returning a single graphictext record."""
    monkeypatch.setattr(main, "start_background_ingestion", lambda: None)
    monkeypatch.setattr(assets_router, "load_lookup_cache", lambda: {})
    monkeypatch.setattr(assets_router, "get_saved_track_override", lambda *_a: None)
    cur = FakeCursor(
        responses=[
            [
                {
                    "pk": pk, "text": label, "x": 100.0, "y": 200.0, "size": 14,
                    "angle": 0, "layers": [1], "valid_from": "2025", "valid_to": "2026",
                    "last_modified": "2026-01-01", "raw_attrs": {},
                }
            ]
        ]
    )
    monkeypatch.setattr(assets_router, "get_conn", lambda: FakeConnection(cur))
    insp_ctx["client"] = TestClient(main.app)


@given(parsers.parse('a mocked NWR inspector database for waymark {fid:d} on ELR "{elr}"'))
def given_nwr_waymark_db(insp_ctx, fid, elr, monkeypatch):
    """Configure mocked DB returning a single NWR waymark record."""
    monkeypatch.setattr(main, "start_background_ingestion", lambda: None)
    monkeypatch.setattr(
        nwr_router,
        "find_tps_cross_links",
        lambda *_a: {"tps_stations": [], "tps_nodes_count": 3},
    )
    cur = FakeCursor(
        responses=[
            [
                {
                    "fid": fid, "asset_id": "WM7", "elr": elr, "unit": "M",
                    "value": 36.0, "meters": 57936.0, "x": 471000.0, "y": 173000.0,
                    "version": "1", "extracted": "2026-10-26", "raw_attrs": {},
                }
            ]
        ]
    )
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(cur))
    insp_ctx["client"] = TestClient(main.app)


@when(parsers.parse('I request GET "{path}"'))
def when_request_get(insp_ctx, path):
    """Send HTTP GET to the FastAPI TestClient."""
    insp_ctx["response"] = insp_ctx["client"].get(path)


@then(parsers.parse("the inspector status should be {status:d}"))
def then_inspector_status(insp_ctx, status):
    """Assert HTTP status code."""
    assert insp_ctx["response"].status_code == status


@then(parsers.parse('the inspector title should contain "{substring}"'))
def then_inspector_title(insp_ctx, substring):
    """Assert returned asset title contains expected substring."""
    assert substring in insp_ctx["response"].json()["title"]

