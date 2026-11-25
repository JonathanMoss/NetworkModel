"""BDD step definitions for tests/bdd/features/api_explorer.feature."""

from fastapi.testclient import TestClient
import pytest
from pytest_bdd import given, parsers, scenarios, then, when

import main
from routers import network_fusion as nf_router
from routers import nwr as nwr_router
from tests.conftest import FakeConnection, FakeCursor

pytestmark = pytest.mark.bdd

scenarios("features/api_explorer.feature")


@pytest.fixture
def api_ctx():
    """Per-scenario state bag for API BDD tests."""
    return {}


@given("a mocked backend database for API requests")
def given_mock_db(api_ctx, monkeypatch):
    """Attach a lightweight FakeConnection to NWR and main routers."""
    monkeypatch.setattr(main, "start_background_ingestion", lambda: None)
    cur = FakeCursor(responses=[[], [], [], []])
    monkeypatch.setattr(nwr_router, "get_conn", lambda: FakeConnection(cur))
    api_ctx["client"] = TestClient(main.app)


@given("a mocked backend database for Network Fusion status")
def given_mock_nf_db(api_ctx, monkeypatch):
    """Attach a FakeConnection returning Network Fusion status metrics."""
    monkeypatch.setattr(main, "start_background_ingestion", lambda: None)
    monkeypatch.setattr(nf_router, "ensure_network_fusion_tables_and_ingest", lambda *_a: None)
    cur = FakeCursor(
        responses=[
            [{"value": {"completed": True}}],
            [{"total_points": 500}],
            [{"total_curv": 120}],
            [{"elrs": 12}],
        ]
    )
    monkeypatch.setattr(nf_router, "get_conn", lambda: FakeConnection(cur))
    api_ctx["client"] = TestClient(main.app)


@when(
    parsers.parse(
        'I POST a track override for asset type "{atype}", asset id {aid:d}, and nwr_fid {fid:d}'
    )
)
def when_post_override(api_ctx, atype, aid, fid):
    """Send POST /api/nwr/track-override."""
    api_ctx["response"] = api_ctx["client"].post(
        "/api/nwr/track-override",
        json={"asset_type": atype, "asset_id": aid, "nwr_fid": fid},
    )


@when(parsers.parse('I DELETE the track override for asset type "{atype}" and asset id {aid:d}'))
def when_delete_override(api_ctx, atype, aid):
    """Send DELETE /api/nwr/track-override/{atype}/{aid}."""
    api_ctx["response"] = api_ctx["client"].delete(f"/api/nwr/track-override/{atype}/{aid}")


@when(parsers.parse('I GET "{path}"'))
def when_get_path(api_ctx, path):
    """Send an HTTP GET request to the FastAPI app."""
    api_ctx["response"] = api_ctx["client"].get(path)


@then(parsers.parse("the HTTP response status should be {status:d}"))
def then_status(api_ctx, status):
    """Assert HTTP status code."""
    assert api_ctx["response"].status_code == status


@then(parsers.parse('the JSON response field "{field}" should be true'))
def then_json_true(api_ctx, field):
    """Assert boolean true field in JSON response."""
    assert api_ctx["response"].json()[field] is True


@then(parsers.parse('the JSON response field "{field}" should be "{value}"'))
def then_json_str(api_ctx, field, value):
    """Assert string field in JSON response."""
    assert api_ctx["response"].json()[field] == value

