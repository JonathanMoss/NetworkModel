/**
 * @fileoverview Debounced TPS & NWR viewport data fetching with
 * AbortController stale-frame guards and bounded LRU viewport caches.
 */

/* Viewport Data Fetching */
function scheduleViewportFetch() {
  if (STATE.viewportTimer) clearTimeout(STATE.viewportTimer);
  if (STATE.activeView === "split") {
    STATE.viewportTimer = setTimeout(() => {
      fetchViewportData();
      fetchNwrViewportData();
    }, 90);
    return;
  }
  if (STATE.activeView === "nwr") {
    if (STATE.nwrCam.zoom < 0.005) {
      document.getElementById("hud-loaded").textContent = `${
        STATE.nwrOverview ? STATE.nwrOverview.macro_links.length.toLocaleString() : 0
      } NWR links`;
      requestRender();
      return;
    }
    STATE.viewportTimer = setTimeout(fetchNwrViewportData, 90);
    return;
  }

  if (STATE.tpsCam.zoom < 0.16) {
    document.getElementById("hud-loaded").textContent = `${
      STATE.overview ? STATE.overview.macro_edges.length.toLocaleString() : 0
    } macro segs`;
    requestRender();
    return;
  }
  STATE.viewportTimer = setTimeout(fetchViewportData, 90);
}

const _VIEWPORT_CACHE_MAX = 16;
const _tpsViewportCache = new Map();
const _nwrViewportCache = new Map();

function getViewportCacheEntry(cache, key) {
  if (!cache.has(key)) return null;
  const val = cache.get(key);
  cache.delete(key);
  cache.set(key, val);
  return val;
}

function setViewportCacheEntry(cache, key, val) {
  if (cache.has(key)) {
    cache.delete(key);
  } else if (cache.size >= _VIEWPORT_CACHE_MAX) {
    cache.delete(cache.keys().next().value);
  }
  cache.set(key, val);
}

async function fetchViewportData() {
  if (!STATE.ready || (STATE.activeView !== "tps" && STATE.activeView !== "split") || STATE.tpsCam.zoom < 0.16) return;
  if (STATE.viewportAbort) STATE.viewportAbort.abort();
  const myAbort = new AbortController();
  STATE.viewportAbort = myAbort;

  const bbox = getViewportBBox(0.3, "tps");
  const params = new URLSearchParams({
    min_x: bbox.min_x.toFixed(1),
    max_x: bbox.max_x.toFixed(1),
    min_y: bbox.min_y.toFixed(1),
    max_y: bbox.max_y.toFixed(1),
    zoom: STATE.tpsCam.zoom.toFixed(4),
    include_vectors: STATE.layers.vectors,
    include_texts: STATE.layers.texts && STATE.tpsCam.zoom >= 0.4,
    include_signals: STATE.layers.signals && STATE.tpsCam.zoom >= 0.3,
    include_nodes: STATE.layers.nodes || STATE.tpsCam.zoom >= 2.0,
    include_tracks: STATE.layers.tracks,
    include_blocks:
      STATE.layers.blocks ||
      (STATE.colorMode === "signalling" && STATE.tpsCam.zoom >= 0.55),
    include_vp: STATE.colorMode === "timing",
    include_overlaps: STATE.colorMode === "signalling" || STATE.tpsCam.zoom >= 1.1,
  });

  const queryStr = params.toString();
  const cached = getViewportCacheEntry(_tpsViewportCache, queryStr);
  if (cached) {
    STATE.viewportData = { ...cached, bbox };
    if (STATE.activeView !== "nwr") {
      document.getElementById("hud-loaded").textContent = `${cached.edges.length.toLocaleString()} edges, ${cached.signals.length.toLocaleString()} sigs`;
    }
    requestRender();
    return;
  }

  try {
    const res = await fetch(`/api/viewport?${queryStr}`, {
      signal: myAbort.signal,
    });
    if (!res.ok || STATE.viewportAbort !== myAbort) return;
    const data = await res.json();
    if (STATE.viewportAbort !== myAbort) return;
    setViewportCacheEntry(_tpsViewportCache, queryStr, data);
    STATE.viewportData = { ...data, bbox };
    if (STATE.activeView !== "nwr") {
      document.getElementById("hud-loaded").textContent = `${data.edges.length.toLocaleString()} edges, ${data.signals.length.toLocaleString()} sigs`;
    }
    requestRender();
  } catch (err) {
    if (err.name !== "AbortError") console.error(err);
  }
}

async function fetchNwrViewportData() {
  if ((STATE.activeView !== "nwr" && STATE.activeView !== "split") || STATE.nwrCam.zoom < 0.005) return;
  if (STATE.nwrViewportAbort) STATE.nwrViewportAbort.abort();
  const myAbort = new AbortController();
  STATE.nwrViewportAbort = myAbort;

  const bbox = getViewportBBox(0.3, "nwr");
  const params = new URLSearchParams({
    min_x: bbox.min_x.toFixed(1),
    max_x: bbox.max_x.toFixed(1),
    min_y: bbox.min_y.toFixed(1),
    max_y: bbox.max_y.toFixed(1),
    zoom: STATE.nwrCam.zoom.toFixed(5),
    include_centrelines: STATE.nwrLayers.centrelines && STATE.nwrCam.zoom >= 0.008,
    include_nodes: STATE.nwrLayers.nodes && STATE.nwrCam.zoom >= 0.035,
    include_waymarks: STATE.nwrLayers.waymarks && STATE.nwrCam.zoom >= 0.02,
    include_curvature: false,
    include_nf_points: false,
  });

  const applyNwrViewport = (data) => {
    STATE.nwrViewportData = { ...data, bbox };
    const extra = [];
    if (data.centrelines && data.centrelines.length) extra.push(`${data.centrelines.length.toLocaleString()} links`);
    if (data.nodes && data.nodes.length) extra.push(`${data.nodes.length.toLocaleString()} nodes`);
    if (data.waymarks && data.waymarks.length) extra.push(`${data.waymarks.length.toLocaleString()} waymarks`);
    if (data.curvature && data.curvature.length) extra.push(`${data.curvature.length.toLocaleString()} curves`);
    if (data.nf_points && data.nf_points.length) extra.push(`${data.nf_points.length.toLocaleString()} pts`);
    if (STATE.activeView === "nwr") {
      document.getElementById("hud-loaded").textContent = extra.length ? extra.join(", ") : "0 features loaded";
    }
    requestRender();
  };

  const queryStr = params.toString();
  const cached = getViewportCacheEntry(_nwrViewportCache, queryStr);
  if (cached) {
    applyNwrViewport(cached);
    return;
  }

  try {
    const res = await fetch(`/api/nwr/viewport?${queryStr}`, {
      signal: myAbort.signal,
    });
    if (!res.ok || STATE.nwrViewportAbort !== myAbort) return;
    const data = await res.json();
    if (STATE.nwrViewportAbort !== myAbort) return;
    setViewportCacheEntry(_nwrViewportCache, queryStr, data);
    applyNwrViewport(data);
  } catch (err) {
    if (err.name !== "AbortError") console.error(err);
  }
}
