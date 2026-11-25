/**
 * @fileoverview Debounced viewport data fetching (TPS & NWR), canvas click
 * picking, and asset detail API loading.
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

async function fetchViewportData() {
  if (!STATE.ready || (STATE.activeView !== "tps" && STATE.activeView !== "split") || STATE.tpsCam.zoom < 0.16) return;
  if (STATE.viewportAbort) STATE.viewportAbort.abort();
  STATE.viewportAbort = new AbortController();

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

  try {
    const res = await fetch(`/api/viewport?${params.toString()}`, {
      signal: STATE.viewportAbort.signal,
    });
    if (!res.ok) return;
    const data = await res.json();
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
  STATE.nwrViewportAbort = new AbortController();

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

  try {
    const res = await fetch(`/api/nwr/viewport?${params.toString()}`, {
      signal: STATE.nwrViewportAbort.signal,
    });
    if (!res.ok) return;
    const data = await res.json();
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
  } catch (err) {
    if (err.name !== "AbortError") console.error(err);
  }
}


/* Click Picking & Asset Inspection */
async function handleCanvasClick(wx, wy, sx, sy, targetView = null) {
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
    await loadAssetDetails(cands[0].type, cands[0].id, false);
    return;
  }

  // Multiple candidates: load primary candidate into inspector and display the clean, aligned, scrollable list
  const primary = cands[0];
  renderPickCandidates(cands, primary);
  await loadAssetDetails(primary.type, primary.id, false);
}

async function loadAssetDetails(assetType, assetId, shouldFly = true, nwrFid = null) {
  const isNwrAsset = String(assetType).startsWith("nwr_") || String(assetType).startsWith("nf_");
  const apiPrefix = isNwrAsset ? "/api/nwr/asset" : "/api/asset";
  const querySuffix = (!isNwrAsset && nwrFid !== null && nwrFid !== undefined && nwrFid !== "")
    ? `?nwr_fid=${encodeURIComponent(nwrFid)}`
    : "";
  try {
    const res = await fetch(`${apiPrefix}/${assetType}/${assetId}${querySuffix}`);
    if (!res.ok) return;
    const data = await res.json();
    STATE.selectedAsset = data;

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

    // Set highlight paths on canvas
    STATE.highlightGroupFilter = "all";
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

    // Synchronized Dual-View Cross-Model Pulsing
    if (isNwrAsset) {
      // Real-world track/ELR clicked -> highlight & pulse TPS schematic edges/signals
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
          if (STATE.activeView === "split" && isFinite(minX) && isFinite(maxX)) {
            const cx = (minX + maxX) / 2;
            const cy = (minY + maxY) / 2;
            const span = Math.max(maxX - minX, maxY - minY, 250);
            const targetZ = Math.min(1.8, (canvas.clientWidth * 0.7) / span);
            flyToTps(cx, cy, targetZ);
          }
        } else {
          STATE.crossHighlight.tps.active = false;
          document.getElementById("tps-sync-tag")?.classList.add("hidden");
        }
      }
    } else {
      // TPS schematic asset clicked (signal, block, edge, station, track) -> highlight & pulse NWR physical track
      const nwrCross = data.related && data.related.nwr_cross_links;
      if (nwrCross) {
        const ways = [];
        const pts = [];
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
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
          if (STATE.activeView === "split") {
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
    }
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
    console.error(err);
  }
}
