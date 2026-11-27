/**
 * @fileoverview Canvas click picking, cross-view highlight synchronization,
 * and AbortController + LRU cached asset detail loading.
 */

/**
 * Determine whether a TPS signal/points selection should render in minimal
 * Dual-View mode (selected marker only on both TPS and Geo canvases, hiding
 * TPS routes/blocks/overlaps and the full NWR track centreline).
 *
 * Single-clicking a signal/point in Dual View ("split") uses minimal mode;
 * double-clicking passes `expandFull: true` to show full routes and Geo track.
 *
 * @param {string} assetType Canonical asset type returned by the API.
 * @param {boolean} expandFull Whether the user explicitly requested full detail.
 * @returns {boolean} True when minimal highlighting should be applied.
 */
function shouldUseMinimalDualHighlight(assetType, expandFull = false) {
  return (
    STATE.activeView === "split" &&
    !expandFull &&
    (assetType === "signal" || assetType === "points")
  );
}

/* Click Picking & Asset Inspection */
async function handleCanvasClick(wx, wy, sx, sy, targetView = null, options = {}) {
  const view = targetView || (STATE.activeView === "nwr" ? "nwr" : "tps");
  if (sx === undefined || sy === undefined) {
    const [scx, scy] = worldToScreen(wx, wy, view);
    sx = scx;
    sy = scy;
  }

  // 1. Client-side hits at click location
  const clientHits = findObjectsAtCursor(sx, sy, wx, wy, view);

  // 2. Fetch server database candidates at click coordinates
  const zoom = view === "nwr" ? STATE.nwrCam.zoom : STATE.tpsCam.zoom;
  const radius = Math.max(10, 14 / zoom);
  const endpoint = view === "nwr" ? "/api/nwr/pick" : "/api/pick";
  let serverCandidates = [];
  try {
    const res = await fetch(
      `${endpoint}?x=${wx.toFixed(1)}&y=${wy.toFixed(1)}&radius=${radius.toFixed(1)}`
    );
    if (res.ok) {
      const data = await res.json();
      serverCandidates = data.candidates || [];
    }
  } catch (err) {
    // Ignore transient network errors
  }

  // 3. Merge candidates with geometry and priority
  const cands = mergeCandidates(clientHits, serverCandidates);

  if (cands.length === 0) {
    renderPickCandidates([], null);
    return;
  }

  if (cands.length === 1) {
    renderPickCandidates([], null);
    await loadAssetDetails(cands[0].type, cands[0].id, false, null, options);
    return;
  }

  // Multiple candidates: load primary candidate into inspector and display the clean, aligned, scrollable list
  const primary = cands[0];
  renderPickCandidates(cands, primary);
  await loadAssetDetails(primary.type, primary.id, false, null, options);
}

/**
 * Apply cross-model highlights (TPS <-> NWR) and optionally pan/zoom the
 * correlated view onto the selected asset.
 *
 * Called both when an asset is initially loaded (`loadAssetDetails`) and when
 * the user switches between TPS, NWR Track Model, and Dual View (`switchView`)
 * so an already-selected object is immediately highlighted and centered.
 *
 * @param {object} data Full asset payload stored in `STATE.selectedAsset`.
 * @param {boolean} expandFull Whether the asset was double-clicked (full mode).
 * @param {boolean} shouldCenterTarget Whether to fly/zoom the correlated view.
 */
function applyCrossHighlightsForSelectedAsset(
  data,
  expandFull = false,
  shouldCenterTarget = true,
  preserveFilter = false
) {
  if (!data) return;
  const isNwrAsset = String(data.asset_type).startsWith("nwr_") || String(data.asset_type).startsWith("nf_");
  const isSignalOrPoints = data.asset_type === "signal" || data.asset_type === "points";
  const minimalDualMode = shouldUseMinimalDualHighlight(data.asset_type, expandFull);
  const minimalGeoMode =
    !expandFull &&
    isSignalOrPoints &&
    (STATE.activeView === "split" || STATE.activeView === "nwr");

  const isSpecificRouteFilter =
    typeof STATE.highlightGroupFilter === "string" &&
    (STATE.highlightGroupFilter.startsWith("outgoing:") ||
      STATE.highlightGroupFilter.startsWith("approach:"));

  // Keep TPS highlight filter in sync with the active view, preserving any
  // explicitly chosen envelope/route filter when restoring Geo markers.
  if (!isNwrAsset && !isSpecificRouteFilter && !preserveFilter) {
    STATE.highlightGroupFilter = minimalDualMode ? "minimal" : "all";
  }

  // If a single route is already isolated for a signal/points asset, plot both
  // route endpoints geographically and frame them in the Geo camera.
  if (isSignalOrPoints && isSpecificRouteFilter) {
    syncGeoHighlightsForRouteFilter(STATE.highlightGroupFilter, shouldCenterTarget);
    return;
  }

  if (isNwrAsset) {
    // Real-world track/ELR selected -> highlight & pulse correlated TPS schematic edges/signals
    const tpsCross = data.related && data.related.tps_cross_links;
    if (tpsCross) {
      const ways = [];
      const pts = [];
      let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      for (const e of tpsCross.tps_edges || []) {
        if (e.x0 !== null && e.y0 !== null && e.x1 !== null && e.y1 !== null) {
          ways.push({
            label: `Edge #${e.edge_id} (${e.length}m, ${e.vmax}km/h)`,
            coords: [[e.x0, e.y0], [e.x1, e.y1]],
            color: "#f43f5e",
          });
          minX = Math.min(minX, e.x0, e.x1);
          maxX = Math.max(maxX, e.x0, e.x1);
          minY = Math.min(minY, e.y0, e.y1);
          maxY = Math.max(maxY, e.y0, e.y1);
        }
      }
      for (const s of tpsCross.tps_signals || []) {
        if (s.x !== null && s.y !== null) {
          pts.push({
            x: s.x,
            y: s.y,
            label: `🚦 ${s.name || s.master_name}`,
            color: "#f59e0b",
          });
          minX = Math.min(minX, s.x);
          maxX = Math.max(maxX, s.x);
          minY = Math.min(minY, s.y);
          maxY = Math.max(maxY, s.y);
        }
      }
      for (const st of tpsCross.tps_stations || []) {
        if (st.x !== null && st.y !== null) {
          pts.push({
            x: st.x,
            y: st.y,
            label: `🚉 ${st.longname || st.abbrev}`,
            color: "#38bdf8",
          });
          minX = Math.min(minX, st.x);
          maxX = Math.max(maxX, st.x);
          minY = Math.min(minY, st.y);
          maxY = Math.max(maxY, st.y);
        }
      }
      if (ways.length || pts.length) {
        STATE.crossHighlight.tps = {
          ways,
          points: pts,
          pulseStart: performance.now(),
          pulseDuration: 4500,
          active: true,
          title: data.title,
        };
        triggerPulseLoop();
        document.getElementById("tps-sync-tag")?.classList.remove("hidden");
        if (
          shouldCenterTarget &&
          (STATE.activeView === "split" || STATE.activeView === "tps") &&
          isFinite(minX) &&
          isFinite(maxX)
        ) {
          const cx = (minX + maxX) / 2;
          const cy = (minY + maxY) / 2;
          const span = Math.max(maxX - minX, maxY - minY, 250);
          const targetZ = Math.min(1.8, ((canvas?.clientWidth || 600) * 0.7) / span);
          flyToTps(cx, cy, targetZ);
        }
      } else {
        STATE.crossHighlight.tps.active = false;
        document.getElementById("tps-sync-tag")?.classList.add("hidden");
      }
    }
    return;
  }

  // TPS schematic asset selected -> highlight & pulse NWR physical track / discrete marker
  const nwrCross = data.related && data.related.nwr_cross_links;
  if (!nwrCross) return;

  const ways = [];
  const pts = [];
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  // In minimal mode (single-click on TPS signal/points), show only the discrete
  // signal/point marker in Geo view and suppress the full NWR track centreline highlight.
  if (!minimalGeoMode) {
    for (const c of nwrCross.centrelines || []) {
      if (c.coords && c.coords.length >= 2) {
        const segSuffix = c.clipped ? ` (${c.clipped_length_meters || ''}m Segment)` : "";
        ways.push({
          label: `${c.elr} Track ${c.track_id}${segSuffix}`,
          coords: c.coords,
          color: "#06b6d4",
        });
        for (const pt of c.coords) {
          minX = Math.min(minX, pt[0]);
          maxX = Math.max(maxX, pt[0]);
          minY = Math.min(minY, pt[1]);
          maxY = Math.max(maxY, pt[1]);
        }
      }
    }
    if (!ways.length && nwrCross.elr_record && nwrCross.elr_record.parts) {
      for (const p of nwrCross.elr_record.parts) {
        if (p.length >= 2) {
          ways.push({
            label: `ELR ${nwrCross.elr_record.elr}`,
            coords: p,
            color: "#06b6d4",
          });
          for (const pt of p) {
            minX = Math.min(minX, pt[0]);
            maxX = Math.max(maxX, pt[0]);
            minY = Math.min(minY, pt[1]);
            maxY = Math.max(maxY, pt[1]);
          }
        }
      }
    }
  }

  // Discrete node & signal beacon points
  for (const np of nwrCross.node_points || []) {
    if (np.x !== undefined && np.y !== undefined) {
      pts.push({
        x: np.x,
        y: np.y,
        label: np.label,
        color: np.color || "#f59e0b",
        shape: np.shape,
        snapped_node: np.snapped_node,
      });
      minX = Math.min(minX, np.x);
      maxX = Math.max(maxX, np.x);
      minY = Math.min(minY, np.y);
      maxY = Math.max(maxY, np.y);
    }
  }
  if (!pts.length && nwrCross.node_point) {
    const np = nwrCross.node_point;
    pts.push({
      x: np.x,
      y: np.y,
      label: np.label,
      color: np.color || "#f59e0b",
      shape: np.shape,
      snapped_node: np.snapped_node,
    });
    minX = Math.min(minX, np.x);
    maxX = Math.max(maxX, np.x);
    minY = Math.min(minY, np.y);
    maxY = Math.max(maxY, np.y);
  }

  if (ways.length || pts.length) {
    STATE.crossHighlight.nwr = {
      ways,
      points: pts,
      snappedNode: nwrCross.snapped_node,
      pulseStart: performance.now(),
      pulseDuration: 4500,
      active: true,
      title: data.title,
    };
    triggerPulseLoop();
    document.getElementById("nwr-sync-tag")?.classList.remove("hidden");
    if (shouldCenterTarget && (STATE.activeView === "split" || STATE.activeView === "nwr")) {
      const snap = nwrCross.geo_snap;
      if (snap && isFinite(snap.x) && isFinite(snap.y)) {
        flyToNwr(snap.x, snap.y, snap.target_zoom || 0.28, true);
        const tagEl = document.getElementById("nwr-sync-tag");
        if (tagEl) {
          tagEl.textContent = `⚡ Snapped: ${snap.elr || ''} @ ${snap.mileage_str || ''}${snap.is_extrapolated ? ' (Extrapolated)' : ''}`;
        }
      } else if (pts.length) {
        const cx = pts.length === 1 ? pts[0].x : (pts[0].x + pts[1].x) / 2;
        const cy = pts.length === 1 ? pts[0].y : (pts[0].y + pts[1].y) / 2;
        const span = pts.length > 1 ? Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y) : 250;
        const targetZ = Math.min(0.28, Math.max(0.04, ((nwrCanvas?.clientWidth || 600) * 0.75) / Math.max(span, 250)));
        flyToNwr(cx, cy, targetZ, true);
      } else if (isFinite(minX) && isFinite(maxX)) {
        const cx = (minX + maxX) / 2;
        const cy = (minY + maxY) / 2;
        const span = Math.max(maxX - minX, maxY - minY, 350);
        const targetZ = Math.min(0.25, Math.max(0.04, ((nwrCanvas?.clientWidth || 600) * 0.7) / span));
        flyToNwr(cx, cy, targetZ, true);
      }
    }
  } else {
    STATE.crossHighlight.nwr.active = false;
    document.getElementById("nwr-sync-tag")?.classList.add("hidden");
  }
}
const _ASSET_DETAIL_CACHE_MAX = 50;
const _assetDetailCache = new Map();
let _assetDetailAbortController = null;

/**
 * Invalidate cached asset detail and route geo endpoint entries (e.g. after
 * saving or resetting a manual NWR track override).
 *
 * @param {string} assetType Canonical asset type.
 * @param {number|string} assetId Asset ID / PK.
 */
function invalidateAssetDetailCache(assetType, assetId) {
  const key = `${assetType}:${assetId}`;
  _assetDetailCache.delete(key);
  _routeGeoEndpointCache.delete(key);
  if (assetType === "signal" || assetType === "points") {
    _assetDetailCache.delete(`signal:${assetId}`);
    _assetDetailCache.delete(`points:${assetId}`);
    _routeGeoEndpointCache.delete(`signal:${assetId}`);
    _routeGeoEndpointCache.delete(`points:${assetId}`);
  }
}

function getCachedAssetDetail(cacheKey) {
  if (!_assetDetailCache.has(cacheKey)) return null;
  const val = _assetDetailCache.get(cacheKey);
  // Refresh LRU recency
  _assetDetailCache.delete(cacheKey);
  _assetDetailCache.set(cacheKey, val);
  return val;
}

function setCachedAssetDetail(cacheKey, data) {
  if (_assetDetailCache.has(cacheKey)) {
    _assetDetailCache.delete(cacheKey);
  } else if (_assetDetailCache.size >= _ASSET_DETAIL_CACHE_MAX) {
    const oldestKey = _assetDetailCache.keys().next().value;
    _assetDetailCache.delete(oldestKey);
  }
  _assetDetailCache.set(cacheKey, data);
}

async function loadAssetDetails(assetType, assetId, shouldFly = true, nwrFid = null, options = {}) {
  const expandFull = Boolean(options && options.expandFull);
  const isNwrAsset = String(assetType).startsWith("nwr_") || String(assetType).startsWith("nf_");
  const apiPrefix = isNwrAsset ? "/api/nwr/asset" : "/api/asset";
  const hasManualFid = !isNwrAsset && nwrFid !== null && nwrFid !== undefined && nwrFid !== "";
  const querySuffix = hasManualFid ? `?nwr_fid=${encodeURIComponent(nwrFid)}` : "";
  const cacheKey = `${assetType}:${assetId}`;

  // Cancel any previous in-flight asset detail request to prevent out-of-order races
  if (_assetDetailAbortController) {
    _assetDetailAbortController.abort();
  }
  const abortController = typeof AbortController !== "undefined" ? new AbortController() : null;
  _assetDetailAbortController = abortController;

  try {
    let data = !hasManualFid ? getCachedAssetDetail(cacheKey) : null;
    if (!data) {
      const fetchOpts = abortController ? { signal: abortController.signal } : undefined;
      const res = await fetch(`${apiPrefix}/${assetType}/${assetId}${querySuffix}`, fetchOpts);
      if (!res.ok) return;
      data = await res.json();
      if (!hasManualFid) {
        setCachedAssetDetail(cacheKey, data);
      }
    }
    if (abortController && _assetDetailAbortController !== abortController) {
      return;
    }
    STATE.selectedAsset = data;
    STATE.selectedAssetExpandFull = expandFull;

    document.getElementById("insp-badge").textContent = data.asset_type.toUpperCase();
    document.getElementById("insp-title").textContent = data.title;
    document.getElementById("insp-subtitle").textContent = data.subtitle || "";

    const flyBtn = document.getElementById("btn-fly-asset");
    if (data.x !== null && data.x !== undefined) {
      flyBtn.classList.remove("hidden");
      const getTargetZ = () => {
        if (isNwrAsset) {
          if (data.asset_type === "nwr_elr") return 0.04;
          if (data.asset_type === "nf_curvature" || data.asset_type === "nf_point") return 0.25;
          return 0.35;
        }
        if (data.asset_type === "stationbank" && Array.isArray(data.bbox) && data.bbox.length === 4) {
          const spanX = Math.max(500, data.bbox[2] - data.bbox[0]);
          const spanY = Math.max(500, data.bbox[3] - data.bbox[1]);
          return Math.max(0.03, Math.min(1.5, Math.min(canvas.clientWidth / (spanX * 1.4), canvas.clientHeight / (spanY * 1.4))));
        }
        return data.asset_type === "station" ? 1.4 : 2.6;
      };
      flyBtn.onclick = () => flyTo(data.x, data.y, getTargetZ());
      if (shouldFly) {
        flyTo(data.x, data.y, getTargetZ());
      }
    } else {
      flyBtn.classList.add("hidden");
    }

    // Set highlight paths on canvas (minimal marker-only on single-click in Dual View for signals/points)
    const minimalDualMode = shouldUseMinimalDualHighlight(data.asset_type, expandFull);
    STATE.highlightGroupFilter = minimalDualMode ? "minimal" : "all";
    STATE.highlight = {
      ways: (data.highlight && data.highlight.ways) || [],
      points: (data.highlight && data.highlight.points) || [],
      segments: (data.highlight && data.highlight.segments) || [],
    };
    const clearBtn = document.getElementById("btn-clear-highlight");
    if (
      STATE.highlight.ways.length ||
      STATE.highlight.points.length ||
      STATE.highlight.segments.length
    ) {
      clearBtn.classList.remove("hidden");
    } else {
      clearBtn.classList.add("hidden");
    }

    // Synchronized Dual-View / Cross-View Highlighting & Camera Snap
    applyCrossHighlightsForSelectedAsset(data, expandFull, true);
    requestRender();

    // Lazy-render Inspector tabs: only build DOM for the currently visible tab
    STATE.renderedInspectorTabs = {
      "tab-decoded": false,
      "tab-related": false,
      "tab-raw": false,
    };
    renderActiveInspectorTab();

    // Trigger Elevation & Curvature Profile Drawer for tracks/corridors
    if (data.related && data.related.network_fusion && (
      (data.related.network_fusion.points && data.related.network_fusion.points.length > 0) ||
      (data.related.network_fusion.curvature && data.related.network_fusion.curvature.length > 0)
    )) {
      openProfileDrawer(data, data.asset_type === "nwr_elr" ? "corridor" : "track");
    } else if (data.asset_type === "nwr_centreline" || data.asset_type === "nwr_elr" || data.asset_type === "nf_point" || data.asset_type === "nf_curvature") {
      openProfileDrawer(data, data.asset_type === "nwr_elr" ? "corridor" : "track");
    } else {
      if (!STATE.profileDrawer.data) {
        closeProfileDrawer(true);
      }
    }
  } catch (err) {
    if (err && err.name === "AbortError") return;
    console.error(err);
  }
}
