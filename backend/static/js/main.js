/**
 * @fileoverview HTML escaping utility and application bootstrap (initApp).
 */


function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* Initialization & Status Polling */
async function initApp() {
  loadPersistedLayerStyles();
  loadPersistedOsmSettings();
  loadPersistedLayerToggles();
  resizeCanvas();
  updateLegend();
  updateLabelUi();
  updateLayerStyleUi();
  updateLayerToggleUi();
  setupProfileDrawerEvents();

  const overlay = document.getElementById("ingest-overlay");
  while (true) {
    try {
      const res = await fetch("/api/status");
      if (res.ok) {
        const st = await res.json();
        if (st.project && st.infra) {
          document.getElementById("dataset-subtitle").textContent =
            `${st.infra.name || "INFRA FREEZE"} • NWR_TrackModel 20261026`;
        }
        if (st.ingest.status === "ready") {
          overlay.classList.add("hidden");
          break;
        } else {
          overlay.classList.remove("hidden");
          document.getElementById("ingest-phase").textContent = st.ingest.phase || "Processing...";
          document.getElementById("ingest-progress-bar").style.width = `${st.ingest.percent || 5}%`;
          document.getElementById("ingest-count").textContent = `${(st.ingest.elements_processed || 0).toLocaleString()} / ~2,508,000 elements`;
          document.getElementById("ingest-pct").textContent = `${st.ingest.percent || 0}%`;
        }
      }
    } catch (e) {
      console.error(e);
    }
    await new Promise((r) => setTimeout(r, 1200));
  }

  // Load whole-network overview
  const ovRes = await fetch("/api/overview");
  if (ovRes.ok) {
    STATE.overview = await ovRes.json();
    STATE.ready = true;
    fitTpsNetwork();
  }

  // Pre-fetch NWR Overview for dual view coordinate camera calibration
  try {
    const nwrRes = await fetch("/api/nwr/overview");
    if (nwrRes.ok) {
      STATE.nwrOverview = await nwrRes.json();
      const b = STATE.nwrOverview.bounds;
      if (b) {
        STATE.nwrCam.camX = (b.min_x + b.max_x) / 2;
        STATE.nwrCam.camY = (b.min_y + b.max_y) / 2;
        const c = nwrCanvas || canvas;
        const spanX = b.max_x - b.min_x;
        const spanY = b.max_y - b.min_y;
        STATE.nwrCam.zoom = Math.min(((c.clientWidth || 800) * 0.88) / spanX, ((c.clientHeight || 600) * 0.88) / spanY);
      }
    }
  } catch (err) {
    // Ignore transient network errors
  }
}

initApp();
