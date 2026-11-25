"""E2E tests verifying the composed HTML frontend and all modular CSS/JS static assets."""

import re

from fastapi.testclient import TestClient
import pytest

import main

pytestmark = pytest.mark.e2e


@pytest.fixture
def client(monkeypatch):
    """Return a TestClient with background ingestion disabled."""
    monkeypatch.setattr(main, "start_background_ingestion", lambda: None)
    with TestClient(main.app) as tc:
        yield tc


def test_frontend_index_and_all_modular_static_assets_serve_200(client):
    """GET / serves composed modular HTML and every linked CSS/JS file returns HTTP 200."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    html = resp.text

    # Verify all inspector tabs, views, and modals are present
    for marker in [
        "TPS Schematic View",
        "NWR Track Model View",
        "Dual View (Sync)",
        'data-tab="tab-decoded"',
        'data-tab="tab-related"',
        'data-tab="tab-raw"',
        'id="catalog-modal"',
        'id="timing-modal"',
        'id="profile-drawer"',
    ]:
        assert marker in html

    # Fetch every modular CSS and JS asset referenced by base.html
    asset_urls = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
    assert len(asset_urls) >= 21

    for url in asset_urls:
        asset_resp = client.get(url)
        assert asset_resp.status_code == 200, f"Failed to serve {url}"
        assert len(asset_resp.text.strip()) > 0, f"Empty asset body for {url}"

