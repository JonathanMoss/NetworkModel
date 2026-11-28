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
        'id="possessions-drawer"',
    ]:
        assert marker in html

    # Fetch every modular CSS and JS asset referenced by base.html
    asset_urls = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
    assert len(asset_urls) >= 35

    for url in asset_urls:
        asset_resp = client.get(url)
        assert asset_resp.status_code == 200, f"Failed to serve {url}"
        assert "max-age=" in asset_resp.headers.get("cache-control", "")
        assert len(asset_resp.text.strip()) > 0, f"Empty asset body for {url}"

    # Verify minimal Dual-View single-click & full double-click highlight logic is present
    dl_js = client.get("/static/js/data-loader.js").text
    assert "shouldUseMinimalDualHighlight" in dl_js
    assert "applyCrossHighlightsForSelectedAsset" in dl_js
    assert "invalidateAssetDetailCache" in dl_js
    assert 'STATE.highlightGroupFilter = minimalDualMode ? "minimal" : "all"' in dl_js
    dl_geo_js = client.get("/static/js/data-loader-geo.js").text
    assert "syncGeoHighlightsForRouteFilter" in dl_geo_js
    assert "fitGeoCameraToRoutePoints" in dl_geo_js
    dl_vp_js = client.get("/static/js/data-loader-viewport.js").text
    assert "fetchViewportData" in dl_vp_js
    assert "fetchNwrViewportData" in dl_vp_js
    ov_js = client.get("/static/js/render-overlays.js").text
    assert 'grpFilter === "minimal"' in ov_js
    cam_js = client.get("/static/js/viewport-camera.js").text
    assert "expandFull = e.detail >= 2" in cam_js
    assert "applyCrossHighlightsForSelectedAsset" in cam_js
    pers_js = client.get("/static/js/persistence.js").text
    assert "loadPersistedColorModes" in pers_js
    assert "savePersistedColorModes" in pers_js
    rsp_js = client.get("/static/js/route-speed-profile.js").text
    assert "buildRouteSpeedProfileCard" in rsp_js
    assert "renderRouteSpeedStepSvg" in rsp_js
    assert "stepToRouteEndpoint" in rsp_js
    env_js = client.get("/static/js/signalling-envelope.js").text
    assert "setSignalHighlightFilter" in env_js
    assert "buildSignallingEnvelopeWidget" in env_js
    rel_js = client.get("/static/js/inspector-related.js").text
    assert "renderRelatedTab" in rel_js
    assert "buildSignalRelatedSections" in rel_js
    rel_nwr_js = client.get("/static/js/inspector-related-nwr.js").text
    assert "buildNwrCrossLinkWidget" in rel_nwr_js
    assert "buildNwrRelatedSections" in rel_nwr_js
    rel_tps_js = client.get("/static/js/inspector-related-tps.js").text
    assert "buildStationRelatedSections" in rel_tps_js
    assert "buildTractionRelatedSections" in rel_tps_js
    tb_js = client.get("/static/js/toolbar-controls.js").text
    assert "global-search" in tb_js
    assert "osm-opacity-slider" in tb_js
    poss_js = client.get("/static/js/possessions-ui.js").text
    assert "loadPossessions" in poss_js
    assert "startPossessionMapPick" in poss_js
    rposs_js = client.get("/static/js/render-possessions.js").text
    assert "drawPossessionsOverlay" in rposs_js

