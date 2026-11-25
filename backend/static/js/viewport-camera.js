/**
 * @fileoverview World/screen projections, camera fit & animated fly-to helpers,
 * split-pane layout management, and TPS/NWR/Dual view switching.
 */

/* Coordinate Projections (TPS netY is South-positive; NWR OSGB36 Northing Y is North-positive) */
function worldToScreen(wx, wy, targetView = null) {
  const view = targetView || (STATE.activeView === "split" ? "tps" : STATE.activeView);
  if (view === "nwr") {
    const c = nwrCanvas || canvas;
    const w = c.clientWidth;
    const h = c.clientHeight;
    const cam = STATE.nwrCam;
    return [
      (wx - cam.camX) * cam.zoom + w / 2,
      (cam.camY - wy) * cam.zoom + h / 2,
    ];
  }
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  const cam = STATE.tpsCam;
  return [
    (wx - cam.camX) * cam.zoom + w / 2,
    (wy - cam.camY) * cam.zoom + h / 2,
  ];
}

function screenToWorld(sx, sy, targetView = null) {
  const view = targetView || (STATE.activeView === "split" ? "tps" : STATE.activeView);
  if (view === "nwr") {
    const c = nwrCanvas || canvas;
    const w = c.clientWidth;
    const h = c.clientHeight;
    const cam = STATE.nwrCam;
    return [
      (sx - w / 2) / cam.zoom + cam.camX,
      cam.camY - (sy - h / 2) / cam.zoom,
    ];
  }
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  const cam = STATE.tpsCam;
  return [
    (sx - w / 2) / cam.zoom + cam.camX,
    (sy - h / 2) / cam.zoom + cam.camY,
  ];
}

function getViewportBBox(padFactor = 0.25, forView = null) {
  const view = forView || (STATE.activeView === "nwr" ? "nwr" : "tps");
  const c = (view === "nwr" && nwrCanvas) ? nwrCanvas : canvas;
  const w = c.clientWidth;
  const h = c.clientHeight;
  const [x0, y0] = screenToWorld(0, 0, view);
  const [x1, y1] = screenToWorld(w, h, view);
  const minX = Math.min(x0, x1);
  const maxX = Math.max(x0, x1);
  const minY = Math.min(y0, y1);
  const maxY = Math.max(y0, y1);
  const dx = (maxX - minX) * padFactor;
  const dy = (maxY - minY) * padFactor;
  return {
    min_x: minX - dx,
    max_x: maxX + dx,
    min_y: minY - dy,
    max_y: maxY + dy,
  };
}


/* Fit Network Functions */
function fitTpsNetwork() {
  if (!STATE.overview) return;
  const b = STATE.overview.bounds;
  const spanX = b.max_x - b.min_x;
  const spanY = b.max_y - b.min_y;
  const w = canvas.clientWidth || 800;
  const h = canvas.clientHeight || 600;
  STATE.tpsCam.camX = (b.min_x + b.max_x) / 2;
  STATE.tpsCam.camY = (b.min_y + b.max_y) / 2;
  STATE.tpsCam.zoom = Math.min((w * 0.88) / spanX, (h * 0.88) / spanY);
  if (STATE.activeView === "tps") {
    STATE.camX = STATE.tpsCam.camX;
    STATE.camY = STATE.tpsCam.camY;
    STATE.zoom = STATE.tpsCam.zoom;
  }
  scheduleViewportFetch();
  requestRender();
}

function fitNwrCorridor() {
  if (!STATE.nwrOverview) return;
  const b = STATE.nwrOverview.bounds;
  const spanX = b.max_x - b.min_x;
  const spanY = b.max_y - b.min_y;
  const c = nwrCanvas || canvas;
  const w = c.clientWidth || 800;
  const h = c.clientHeight || 600;
  STATE.nwrCam.camX = (b.min_x + b.max_x) / 2;
  STATE.nwrCam.camY = (b.min_y + b.max_y) / 2;
  STATE.nwrCam.zoom = Math.min((w * 0.88) / spanX, (h * 0.88) / spanY);
  if (STATE.activeView === "nwr") {
    STATE.camX = STATE.nwrCam.camX;
    STATE.camY = STATE.nwrCam.camY;
    STATE.zoom = STATE.nwrCam.zoom;
  }
  scheduleViewportFetch();
  requestRender();
}

function fitUKNetwork() {
  if (STATE.activeView === "nwr") {
    fitNwrCorridor();
  } else if (STATE.activeView === "split") {
    fitTpsNetwork();
    fitNwrCorridor();
  } else {
    fitTpsNetwork();
  }
}

let tpsFlyAnim = null;
let nwrFlyAnim = null;

function easeOutCubic(t) {
  return 1 - Math.pow(1 - t, 3);
}

function flyToTps(x, y, targetZoom = 1.6, animate = true) {
  if (x === null || y === null || isNaN(x) || isNaN(y)) return;
  if (!animate) {
    STATE.tpsCam.camX = x;
    STATE.tpsCam.camY = y;
    if (targetZoom) STATE.tpsCam.zoom = targetZoom;
    if (STATE.activeView === "tps") {
      STATE.camX = x;
      STATE.camY = y;
      if (targetZoom) STATE.zoom = targetZoom;
    }
    scheduleViewportFetch();
    requestRender();
    return;
  }

  if (tpsFlyAnim) cancelAnimationFrame(tpsFlyAnim);
  const startX = STATE.tpsCam.camX;
  const startY = STATE.tpsCam.camY;
  const startZ = STATE.tpsCam.zoom;
  const destZ = targetZoom || startZ;
  const startTime = performance.now();
  const duration = 380;

  function step(now) {
    const elapsed = now - startTime;
    const progress = Math.min(1.0, elapsed / duration);
    const ease = easeOutCubic(progress);

    STATE.tpsCam.camX = startX + (x - startX) * ease;
    STATE.tpsCam.camY = startY + (y - startY) * ease;
    STATE.tpsCam.zoom = startZ + (destZ - startZ) * ease;
    if (STATE.activeView === "tps") {
      STATE.camX = STATE.tpsCam.camX;
      STATE.camY = STATE.tpsCam.camY;
      STATE.zoom = STATE.tpsCam.zoom;
    }
    requestRender();
    if (progress < 1.0) {
      tpsFlyAnim = requestAnimationFrame(step);
    } else {
      tpsFlyAnim = null;
      scheduleViewportFetch();
    }
  }
  tpsFlyAnim = requestAnimationFrame(step);
}

function flyToNwr(x, y, targetZoom = 0.04, animate = true) {
  if (x === null || y === null || isNaN(x) || isNaN(y)) return;
  if (!animate) {
    STATE.nwrCam.camX = x;
    STATE.nwrCam.camY = y;
    if (targetZoom) STATE.nwrCam.zoom = targetZoom;
    if (STATE.activeView === "nwr") {
      STATE.camX = x;
      STATE.camY = y;
      if (targetZoom) STATE.zoom = targetZoom;
    }
    scheduleViewportFetch();
    requestRender();
    return;
  }

  if (nwrFlyAnim) cancelAnimationFrame(nwrFlyAnim);
  const startX = STATE.nwrCam.camX;
  const startY = STATE.nwrCam.camY;
  const startZ = STATE.nwrCam.zoom;
  const destZ = targetZoom || startZ;
  const startTime = performance.now();
  const duration = 380;

  function step(now) {
    const elapsed = now - startTime;
    const progress = Math.min(1.0, elapsed / duration);
    const ease = easeOutCubic(progress);

    STATE.nwrCam.camX = startX + (x - startX) * ease;
    STATE.nwrCam.camY = startY + (y - startY) * ease;
    STATE.nwrCam.zoom = startZ + (destZ - startZ) * ease;
    if (STATE.activeView === "nwr") {
      STATE.camX = STATE.nwrCam.camX;
      STATE.camY = STATE.nwrCam.camY;
      STATE.zoom = STATE.nwrCam.zoom;
    }
    requestRender();
    if (progress < 1.0) {
      nwrFlyAnim = requestAnimationFrame(step);
    } else {
      nwrFlyAnim = null;
      scheduleViewportFetch();
    }
  }
  nwrFlyAnim = requestAnimationFrame(step);
}

function flyTo(x, y, targetZoom = null) {
  const isNwr = STATE.activeView === "nwr" || (STATE.selectedAsset && (String(STATE.selectedAsset.asset_type).startsWith("nwr_") || String(STATE.selectedAsset.asset_type).startsWith("nf_")));
  if (isNwr) {
    flyToNwr(x, y, targetZoom || 0.04);
  } else {
    flyToTps(x, y, targetZoom || 1.6);
  }
}

/* Pane Folding & View-Dependent Layout */
function updatePaneLayout() {
  const isNwr = STATE.activeView === "nwr";
  const isSplit = STATE.activeView === "split";
  const leftPane = document.getElementById("left-panel");
  const rightPane = document.getElementById("inspector-drawer");
  const btnLeft = document.getElementById("btn-toggle-left-pane");
  const btnRight = document.getElementById("btn-toggle-right-pane");

  if (leftPane) {
    leftPane.classList.toggle("collapsed", STATE.leftPaneCollapsed);
  }
  if (btnLeft) {
    btnLeft.innerHTML = STATE.leftPaneCollapsed ? "&#9654; Show Layers" : "&#9664; Hide Layers";
  }

  if (rightPane) {
    // In single NWR view without split, hide right pane; in split or TPS, show if not collapsed
    const hideRight = (isNwr && !isSplit) || STATE.rightPaneCollapsed;
    rightPane.classList.toggle("collapsed", hideRight);
  }
  if (btnRight) {
    btnRight.classList.toggle("hidden", isNwr && !isSplit);
    btnRight.innerHTML = STATE.rightPaneCollapsed ? "&#9664; Show Inspector" : "Hide Inspector &#9654;";
  }

  resizeCanvas();
  scheduleViewportFetch();
  requestRender();
}

const btnToggleLeftPane = document.getElementById("btn-toggle-left-pane");
if (btnToggleLeftPane) {
  btnToggleLeftPane.addEventListener("click", () => {
    STATE.leftPaneCollapsed = !STATE.leftPaneCollapsed;
    updatePaneLayout();
  });
}

const btnToggleRightPane = document.getElementById("btn-toggle-right-pane");
if (btnToggleRightPane) {
  btnToggleRightPane.addEventListener("click", () => {
    STATE.rightPaneCollapsed = !STATE.rightPaneCollapsed;
    updatePaneLayout();
  });
}

/* Switch Between TPS Schematic View, NWR Track Model View, and Synchronized Dual View */
async function switchView(targetView) {
  if (STATE.activeView === targetView) return;

  // Save current camera
  if (STATE.activeView === "tps") {
    STATE.savedCam.tps = { ...STATE.tpsCam };
  } else if (STATE.activeView === "nwr") {
    STATE.savedCam.nwr = { ...STATE.nwrCam };
  }

  STATE.activeView = targetView;
  hoverTooltip.classList.add("hidden");
  STATE.hoverCandidates = [];
  STATE.hoverPreview = null;
  if (!STATE.selectedAsset) {
    document.getElementById("btn-clear-highlight").classList.add("hidden");
  }
  document.getElementById("pick-candidates").classList.add("hidden");
  closeProfileDrawer(true);

  const isSplit = targetView === "split";
  const isNwr = targetView === "nwr";

  document.getElementById("view-btn-tps").classList.toggle("active", targetView === "tps");
  document.getElementById("view-btn-nwr").classList.toggle("active", targetView === "nwr");
  document.getElementById("view-btn-split").classList.toggle("active", isSplit);

  document.getElementById("tps-layers-panel").classList.toggle("hidden", isNwr);
  document.getElementById("nwr-layers-panel").classList.toggle("hidden", targetView === "tps");
  document.getElementById("tps-hubs-panel").classList.toggle("hidden", isNwr);
  document.getElementById("nwr-hubs-panel").classList.toggle("hidden", targetView === "tps");
  document.getElementById("tps-color-control").classList.toggle("hidden", isNwr);
  document.getElementById("nwr-color-control").classList.toggle("hidden", targetView === "tps");
  document.getElementById("btn-timing-loads").classList.toggle("hidden", isNwr);
  document.getElementById("os-copyright").classList.toggle("hidden", targetView === "tps");
  document.getElementById("hud-coord-label").textContent = isSplit
    ? "Dual Mode Cursor:"
    : isNwr
    ? "OSGB36 (Easting, Northing):"
    : "Cursor (netx, nety):";
  document.getElementById("global-search").placeholder = isNwr
    ? "Search NWR Track Model by ELR (e.g. ECM1, AAV), ASSET_ID (9001...), or TRACK_ID (1100)..."
    : isSplit
    ? "Universal Search across Schematic Interlocking & Geographic Tracks..."
    : "Search Station (Name, CRS, TIPLOC, STANOX), Signal, Timing Load (390, 800, 450, 66, HST), Timetable Corridor (MD710, Marylebone), Node ID, Block, Route, or ELR...";

  updatePaneLayout();

  if ((isNwr || isSplit) && !STATE.nwrOverview) {
    const res = await fetch("/api/nwr/overview");
    if (res.ok) {
      STATE.nwrOverview = await res.json();
      fitNwrCorridor();
    }
  }

  if (targetView === "tps") {
    STATE.camX = STATE.tpsCam.camX;
    STATE.camY = STATE.tpsCam.camY;
    STATE.zoom = STATE.tpsCam.zoom;
  } else if (targetView === "nwr") {
    STATE.camX = STATE.nwrCam.camX;
    STATE.camY = STATE.nwrCam.camY;
    STATE.zoom = STATE.nwrCam.zoom;
  }

  updateLegend();
  resizeCanvas();

  // If an asset is already selected (and its highlight wasn't explicitly cleared),
  // re-apply its highlights and center/zoom the newly opened view on it immediately.
  if (
    STATE.selectedAsset &&
    STATE.highlightGroupFilter !== "none" &&
    typeof applyCrossHighlightsForSelectedAsset === "function"
  ) {
    applyCrossHighlightsForSelectedAsset(
      STATE.selectedAsset,
      Boolean(STATE.selectedAssetExpandFull),
      true
    );
  }

  scheduleViewportFetch();
  requestRender();
}

document.getElementById("view-btn-tps").addEventListener("click", () => switchView("tps"));
document.getElementById("view-btn-nwr").addEventListener("click", () => switchView("nwr"));
document.getElementById("view-btn-split")?.addEventListener("click", () => switchView("split"));
document.getElementById("btn-fit-tps")?.addEventListener("click", fitTpsNetwork);
document.getElementById("btn-fit-nwr")?.addEventListener("click", fitNwrCorridor);

/* Cross-Model Jump Helpers (called from Inspector cards) */
window.jumpToTpsAsset = async function (assetType, assetId) {
  if (STATE.activeView !== "split") {
    await switchView("tps");
  }
  await loadAssetDetails(assetType, assetId, true);
};

window.jumpToNwrElr = async function (elrCode) {
  if (STATE.activeView !== "split") {
    await switchView("nwr");
  }
  if (!STATE.nwrOverview) {
    const res = await fetch("/api/nwr/overview");
    if (res.ok) STATE.nwrOverview = await res.json();
  }
  if (!STATE.nwrOverview) return;
  const elrObj = STATE.nwrOverview.elrs.find(
    (e) => e.elr.toUpperCase() === String(elrCode).toUpperCase()
  );
  if (elrObj) {
    await loadAssetDetails("nwr_elr", elrObj.fid, true);
  }
};

/* Cursor Hover Tooltip disabled: hovering updates cursor pointer without popup obstruction */
if (hoverTooltip) {
  hoverTooltip.style.display = "none";
}

/* Draggable Split Divider for Dual View */
const splitDivider = document.getElementById("split-divider");
let isDraggingDivider = false;
if (splitDivider) {
  splitDivider.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    isDraggingDivider = true;
    splitDivider.classList.add("dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });
}

/* Mouse / Wheel Pan & Zoom for Schematic & Geospatial Canvases */
canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  STATE.isDragging = true;
  STATE.dragTarget = "tps";
  STATE.dragTargetCanvas = canvas;
  STATE.pointerMoved = false;
  STATE.dragStartX = e.clientX;
  STATE.dragStartY = e.clientY;
  STATE.camStartX = STATE.tpsCam.camX;
  STATE.camStartY = STATE.tpsCam.camY;
});

if (nwrCanvas) {
  nwrCanvas.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    STATE.isDragging = true;
    STATE.dragTarget = "nwr";
    STATE.dragTargetCanvas = nwrCanvas;
    STATE.pointerMoved = false;
    STATE.dragStartX = e.clientX;
    STATE.dragStartY = e.clientY;
    STATE.camStartX = STATE.nwrCam.camX;
    STATE.camStartY = STATE.nwrCam.camY;
  });
}

window.addEventListener("mousemove", (e) => {
  if (isDraggingDivider) {
    const wrap = document.getElementById("canvas-container");
    const rect = wrap.getBoundingClientRect();
    let ratio = (e.clientX - rect.left) / rect.width;
    ratio = Math.max(0.18, Math.min(0.82, ratio));
    STATE.splitRatio = ratio;
    wrap.style.setProperty("--split-ratio", `${(ratio * 100).toFixed(1)}%`);
    resizeCanvas();
    return;
  }

  if (STATE.isPointerInMenu) return;

  if (STATE.isDragging) {
    const dx = e.clientX - STATE.dragStartX;
    const dy = e.clientY - STATE.dragStartY;
    if (Math.hypot(dx, dy) > 4) {
      STATE.pointerMoved = true;
      hoverTooltip.classList.add("hidden");
    }
    if (STATE.dragTarget === "nwr") {
      STATE.nwrCam.camX = STATE.camStartX - dx / STATE.nwrCam.zoom;
      STATE.nwrCam.camY = STATE.camStartY + dy / STATE.nwrCam.zoom;
      if (STATE.activeView === "nwr") {
        STATE.camX = STATE.nwrCam.camX;
        STATE.camY = STATE.nwrCam.camY;
      }
    } else {
      STATE.tpsCam.camX = STATE.camStartX - dx / STATE.tpsCam.zoom;
      STATE.tpsCam.camY = STATE.camStartY - dy / STATE.tpsCam.zoom;
      if (STATE.activeView === "tps") {
        STATE.camX = STATE.tpsCam.camX;
        STATE.camY = STATE.tpsCam.camY;
      }
    }
    requestRender();
    return;
  }

  // Hover detection based on cursor canvas location
  const tpsRect = canvas.getBoundingClientRect();
  const nwrRect = nwrCanvas ? nwrCanvas.getBoundingClientRect() : null;

  if (
    nwrRect &&
    e.clientX >= nwrRect.left &&
    e.clientX <= nwrRect.right &&
    e.clientY >= nwrRect.top &&
    e.clientY <= nwrRect.bottom &&
    (STATE.activeView === "nwr" || STATE.activeView === "split")
  ) {
    const sx = e.clientX - nwrRect.left;
    const sy = e.clientY - nwrRect.top;
    const [wx, wy] = screenToWorld(sx, sy, "nwr");
    document.getElementById("hud-coords").textContent = `OSGB36 (${Math.round(wx)}, ${Math.round(wy)})`;
    handleHover(sx, sy, wx, wy, "nwr");
  } else if (
    e.clientX >= tpsRect.left &&
    e.clientX <= tpsRect.right &&
    e.clientY >= tpsRect.top &&
    e.clientY <= tpsRect.bottom &&
    (STATE.activeView === "tps" || STATE.activeView === "split")
  ) {
    const sx = e.clientX - tpsRect.left;
    const sy = e.clientY - tpsRect.top;
    const [wx, wy] = screenToWorld(sx, sy, "tps");
    document.getElementById("hud-coords").textContent = `TPS (${Math.round(wx)}, ${Math.round(wy)})`;
    handleHover(sx, sy, wx, wy, "tps");
  }
});

window.addEventListener("mouseup", (e) => {
  if (isDraggingDivider) {
    isDraggingDivider = false;
    splitDivider?.classList.remove("dragging");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    scheduleViewportFetch();
    return;
  }

  if (!STATE.isDragging) return;
  STATE.isDragging = false;
  if (STATE.pointerMoved) {
    scheduleViewportFetch();
  } else {
    if (STATE.dragTarget === "nwr" && nwrCanvas) {
      const rect = nwrCanvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const [wx, wy] = screenToWorld(sx, sy, "nwr");
      handleCanvasClick(wx, wy, sx, sy, "nwr");
    } else {
      const rect = canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const [wx, wy] = screenToWorld(sx, sy, "tps");
      // Single click in Dual View shows minimal marker-only highlight for signals/points;
      // double click (e.detail >= 2) expands to full TPS routes/overlaps + Geo track section.
      const expandFull = e.detail >= 2;
      handleCanvasClick(wx, wy, sx, sy, "tps", { expandFull });
    }
  }
});

canvas.addEventListener(
  "wheel",
  (e) => {
    e.preventDefault();
    hoverTooltip.classList.add("hidden");
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const [wxBefore, wyBefore] = screenToWorld(sx, sy, "tps");

    const factor = e.deltaY < 0 ? 1.25 : 0.8;
    STATE.tpsCam.zoom = Math.max(0.008, Math.min(25.0, STATE.tpsCam.zoom * factor));
    if (STATE.activeView === "tps") STATE.zoom = STATE.tpsCam.zoom;

    const [wxAfter, wyAfter] = screenToWorld(sx, sy, "tps");
    STATE.tpsCam.camX += wxBefore - wxAfter;
    STATE.tpsCam.camY += wyBefore - wyAfter;
    if (STATE.activeView === "tps") {
      STATE.camX = STATE.tpsCam.camX;
      STATE.camY = STATE.tpsCam.camY;
    }

    scheduleViewportFetch();
    requestRender();
  },
  { passive: false }
);

if (nwrCanvas) {
  nwrCanvas.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      hoverTooltip.classList.add("hidden");
      const rect = nwrCanvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const [wxBefore, wyBefore] = screenToWorld(sx, sy, "nwr");

      const factor = e.deltaY < 0 ? 1.25 : 0.8;
      STATE.nwrCam.zoom = Math.max(0.0004, Math.min(5.0, STATE.nwrCam.zoom * factor));
      if (STATE.activeView === "nwr") STATE.zoom = STATE.nwrCam.zoom;

      const [wxAfter, wyAfter] = screenToWorld(sx, sy, "nwr");
      STATE.nwrCam.camX += wxBefore - wxAfter;
      STATE.nwrCam.camY += wyBefore - wyAfter;
      if (STATE.activeView === "nwr") {
        STATE.camX = STATE.nwrCam.camX;
        STATE.camY = STATE.nwrCam.camY;
      }

      scheduleViewportFetch();
      requestRender();
    },
    { passive: false }
  );
}

