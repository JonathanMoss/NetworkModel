/**
 * @fileoverview Geographic route-endpoint resolution (/geo API), bounded
 * endpoint cache, and dual-signal Geo camera auto-framing.
 */

/* ============================================================================
   ROUTE ENDPOINT GEOGRAPHIC HIGHLIGHTING (ENTRANCE + EXIT / BUFFER MARKERS)
   ============================================================================ */
const _routeGeoEndpointCache = new Map();
let _routeGeoSyncSeq = 0;

/**
 * Extract the primary OSGB36 (x, y) point from an asset's `nwr_cross_links` payload.
 *
 * @param {object|null|undefined} nwrCross Cross-link payload from `/api/asset/...`.
 * @returns {{x: number, y: number}|null} OSGB36 coordinates, or null if unmapped.
 */
function extractGeoPointFromCrossLinks(nwrCross) {
  if (!nwrCross) return null;
  const firstNodePt =
    (Array.isArray(nwrCross.node_points) && nwrCross.node_points[0]) ||
    nwrCross.node_point ||
    null;
  if (firstNodePt && isFinite(firstNodePt.x) && isFinite(firstNodePt.y)) {
    return { x: Number(firstNodePt.x), y: Number(firstNodePt.y) };
  }
  const snap = nwrCross.geo_snap;
  if (snap && isFinite(snap.x) && isFinite(snap.y)) {
    return { x: Number(snap.x), y: Number(snap.y) };
  }
  return null;
}

/**
 * Resolve and cache the OSGB36 (x, y) geographic coordinates of a TPS signal,
 * points, or track node via the lightweight `/api/asset/{type}/{id}/geo` endpoint.
 *
 * @param {string} assetType `"signal"`, `"points"`, or `"node"`.
 * @param {number|string} assetId Asset PK or node ID.
 * @returns {Promise<{x: number, y: number}|null>} Resolved OSGB36 point or null.
 */
async function fetchAssetGeoPoint(assetType, assetId) {
  if (assetId === null || assetId === undefined) return null;
  const cacheKey = `${assetType}:${assetId}`;
  if (_routeGeoEndpointCache.has(cacheKey)) {
    return _routeGeoEndpointCache.get(cacheKey);
  }
  try {
    const res = await fetch(
      `/api/asset/${encodeURIComponent(assetType)}/${encodeURIComponent(assetId)}/geo`
    );
    if (!res.ok) return null;
    const payload = await res.json();
    const pt =
      payload && isFinite(payload.x) && isFinite(payload.y) && payload.x !== null && payload.y !== null
        ? { x: Number(payload.x), y: Number(payload.y) }
        : null;
    _routeGeoEndpointCache.set(cacheKey, pt);
    return pt;
  } catch (err) {
    console.error("Failed to resolve route endpoint geo point:", err);
    return null;
  }
}

/**
 * Fit the NWR / Geo camera to frame a set of OSGB36 markers with comfortable
 * padding so both markers and their floating labels remain clearly visible.
 *
 * @param {Array<{x: number, y: number}>} pts OSGB36 points to frame.
 */
function fitGeoCameraToRoutePoints(pts) {
  if (!pts || !pts.length) return;
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const p of pts) {
    if (!isFinite(p.x) || !isFinite(p.y)) continue;
    minX = Math.min(minX, p.x);
    maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y);
    maxY = Math.max(maxY, p.y);
  }
  if (!isFinite(minX) || !isFinite(maxX)) return;

  const targetCanvas = STATE.activeView === "split" ? nwrCanvas : canvas;
  const cw = targetCanvas?.clientWidth || 600;
  const ch = targetCanvas?.clientHeight || 500;
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;
  const spanX = Math.max(maxX - minX, 180);
  const spanY = Math.max(maxY - minY, 180);
  const targetZ = Math.min(0.28, Math.max(0.012, Math.min((cw * 0.56) / spanX, (ch * 0.56) / spanY)));
  flyToNwr(cx, cy, targetZ, true);
}

/**
 * Synchronize NWR / Geo markers when the user isolates a single signal route
 * (`outgoing:<block_id>` or `approach:<block_id>`) or switches back to an
 * envelope group filter.
 *
 * For an isolated single route:
 * - Clears any Geo track centreline highlight (`ways = []`).
 * - Plots both the Entrance signal (Emerald `#10b981`) and Exit signal / Buffer
 *   (Amber `#f59e0b`) geographically.
 * - Auto-fits the Geo camera to frame both markers together.
 *
 * @param {string} group Active `STATE.highlightGroupFilter` value.
 * @param {boolean} [shouldFitCamera=true] Whether to pan/zoom the Geo camera.
 */
async function syncGeoHighlightsForRouteFilter(group, shouldFitCamera = true) {
  const mySeq = ++_routeGeoSyncSeq;
  const asset = STATE.selectedAsset;
  if (!asset || (asset.asset_type !== "signal" && asset.asset_type !== "points")) {
    return;
  }

  const isOutgoing = typeof group === "string" && group.startsWith("outgoing:");
  const isApproach = typeof group === "string" && group.startsWith("approach:");

  if (!isOutgoing && !isApproach) {
    if (group !== "none") {
      applyCrossHighlightsForSelectedAsset(asset, Boolean(STATE.selectedAssetExpandFull), false, true);
    }
    return;
  }

  const blockId = group.slice(group.indexOf(":") + 1);
  const rel = asset.related || {};
  const blocks = isOutgoing ? (rel.outgoing_blocks || []) : (rel.approach_blocks || []);
  const blk = blocks.find((b) => String(b.block_id) === String(blockId));
  if (!blk) return;

  const curName =
    (asset.title || "").replace(/^(Signal|Points|Buffer Stop)\s+/i, "") ||
    asset.title ||
    "Signal";
  const curShape = asset.asset_type === "points" ? "diamond" : "signal";
  const curGeo = extractGeoPointFromCrossLinks(rel.nwr_cross_links);

  const selectedMarker = curGeo
    ? {
        x: curGeo.x,
        y: curGeo.y,
        label: isOutgoing ? `Entry: ${curName}` : `Exit: ${curName}`,
        color: isOutgoing ? "#10b981" : "#f59e0b",
        shape: curShape,
        asset_type: asset.asset_type,
        asset_id: asset.id,
      }
    : null;

  // Immediately clear any track centreline in Geo view and show the known endpoint
  if (selectedMarker) {
    STATE.crossHighlight.nwr = {
      ways: [],
      points: [selectedMarker],
      snappedNode: null,
      pulseStart: performance.now(),
      pulseDuration: 4500,
      active: true,
      title: blk.route_label || asset.title,
    };
    triggerPulseLoop();
    requestRender();
  }

  // Determine the opposite endpoint(s) to resolve geographically
  const targets = [];
  if (isOutgoing) {
    const exitSigs = blk.exit_signals || [];
    if (exitSigs.length) {
      for (const xs of exitSigs.slice(0, 3)) {
        const isPts = Boolean(xs.is_points || (xs.name && xs.name.toUpperCase().startsWith("POINT")));
        targets.push({
          type: isPts ? "points" : "signal",
          id: xs.pk,
          label: `Exit: ${xs.name || `Signal #${xs.signal_id}`}`,
          color: "#f59e0b",
          shape: isPts ? "diamond" : "signal",
        });
      }
    } else if (blk.end_node_id !== null && blk.end_node_id !== undefined) {
      targets.push({
        type: "node",
        id: blk.end_node_id,
        label: `Buffer / End (#${blk.end_node_id})`,
        color: "#f59e0b",
        shape: "diamond",
      });
    }
  } else {
    const entrySigs = blk.entry_signals || [];
    if (entrySigs.length) {
      for (const es of entrySigs.slice(0, 3)) {
        const isPts = Boolean(es.is_points || (es.name && es.name.toUpperCase().startsWith("POINT")));
        targets.push({
          type: isPts ? "points" : "signal",
          id: es.pk,
          label: `Entry: ${es.name || `Signal #${es.signal_id}`}`,
          color: "#10b981",
          shape: isPts ? "diamond" : "signal",
        });
      }
    } else if (blk.start_node_id !== null && blk.start_node_id !== undefined) {
      targets.push({
        type: "node",
        id: blk.start_node_id,
        label: `Start (#${blk.start_node_id})`,
        color: "#10b981",
        shape: "diamond",
      });
    }
  }

  const resolvedOtherPts = await Promise.all(
    targets.map(async (t) => {
      const geo = await fetchAssetGeoPoint(t.type, t.id);
      return geo
        ? {
            x: geo.x,
            y: geo.y,
            label: t.label,
            color: t.color,
            shape: t.shape,
            asset_type: t.type,
            asset_id: t.id,
          }
        : null;
    })
  );

  // Guard against stale async responses if the user switched route, asset, or cleared highlight
  if (
    mySeq !== _routeGeoSyncSeq ||
    STATE.selectedAsset !== asset ||
    STATE.highlightGroupFilter !== group
  ) {
    return;
  }

  const otherMarkers = resolvedOtherPts.filter(Boolean);
  const finalPts = isOutgoing
    ? [...(selectedMarker ? [selectedMarker] : []), ...otherMarkers]
    : [...otherMarkers, ...(selectedMarker ? [selectedMarker] : [])];

  if (!finalPts.length) return;

  STATE.crossHighlight.nwr = {
    ways: [],
    points: finalPts,
    snappedNode: null,
    pulseStart: performance.now(),
    pulseDuration: 4500,
    active: true,
    title: blk.route_label || asset.title,
  };
  const tagEl = document.getElementById("nwr-sync-tag");
  if (tagEl) {
    tagEl.textContent = `⚡ Route: ${blk.route_label || `Block #${blk.block_id}`}`;
    tagEl.classList.remove("hidden");
  }
  triggerPulseLoop();

  if (shouldFitCamera && (STATE.activeView === "split" || STATE.activeView === "nwr")) {
    fitGeoCameraToRoutePoints(finalPts);
  }
  requestRender();
}
