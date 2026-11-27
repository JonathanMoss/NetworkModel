/**
 * @fileoverview Client-side spatial hit-testing (TPS & NWR), hover cursor
 * feedback, candidate deduplication, and nearby-candidates picker UI.
 */

/* Distance from point (px, py) to segment (x0, y0)-(x1, y1) */
function distToSegment(px, py, x0, y0, x1, y1) {
  const dx = x1 - x0;
  const dy = y1 - y0;
  const l2 = dx * dx + dy * dy;
  if (l2 === 0) return Math.hypot(px - x0, py - y0);
  const t = Math.max(0, Math.min(1, ((px - x0) * dx + (py - y0) * dy) / l2));
  return Math.hypot(px - (x0 + t * dx), py - (y0 + t * dy));
}

/* Comprehensive Client-Side Hit Testing Across Active View Layers & Labels */
function findObjectsAtCursor(sx, sy, wx, wy, targetView = null) {
  const activeView = targetView || STATE.activeView;
  if (activeView === "nwr") {
    return findNwrObjectsAtCursor(sx, sy, wx, wy);
  }
  if (!STATE.overview) return [];
  const hits = [];
  const seen = new Set();
  const addHit = (item) => {
    const key = `${item.type}:${item.id}`;
    if (!seen.has(key)) {
      seen.add(key);
      hits.push(item);
    }
  };

  const zoom = STATE.zoom;
  const useMicro = zoom >= 0.16 && STATE.viewportData.edges.length > 0;
  const pxTol = 10;

  // 1. Signals & Signal Labels
  if (STATE.layers.signals && useMicro && zoom >= 0.35) {
    for (const s of STATE.viewportData.signals) {
      if (s[5] === null) continue;
      const [ssx, ssy] = worldToScreen(s[5], s[6]);
      const d = Math.hypot(sx - ssx, sy - ssy);
      if (d <= pxTol) {
        const ilock = getSignalInterlockingInfo(s[4], s[9]);
        const ovCnt = s[12] || 0;
        const sigWay =
          s[7] !== null && (s[7] !== s[5] || s[8] !== s[6])
            ? [
                [s[7], s[8]],
                [s[5], s[6]],
              ]
            : null;
        const sName = s[3] || "#" + s[2];
        const isPts = s[4] === 15 || s[4] === 16 || sName.toUpperCase().startsWith("POINT");
        const itemType = isPts ? "points" : "signal";
        const title = isPts
          ? (sName.toUpperCase().startsWith("POINT") ? sName : `Points ${sName}`)
          : `${s[9] === 1 ? "Buffer Stop" : "Signal"} ${sName}`;
        addHit({
          type: itemType,
          id: s[0],
          title: title,
          sub: `${isPts ? "Points" : ilock.label}${ovCnt ? " • " + ovCnt + " Overlap(s)" : ""} • #${s[10] || "?"}→#${s[11] || "?"}`,
          x: s[5],
          y: s[6],
          previewWay: sigWay,
          score: d,
        });
      }
    }
    for (const lbl of STATE.drawnSignalLabels) {
      if (isPointInOrientedBox(sx, sy, lbl.x, lbl.y, lbl.w, lbl.h, lbl.angle || 0, lbl.alignY || "center")) {
        const s = lbl.sig;
        const ilock = getSignalInterlockingInfo(s[4], s[9]);
        const sigWay =
          s[7] !== null && (s[7] !== s[5] || s[8] !== s[6])
            ? [
                [s[7], s[8]],
                [s[5], s[6]],
              ]
            : null;
        const sName = s[3] || "#" + s[2];
        const isPts = s[4] === 15 || s[4] === 16 || sName.toUpperCase().startsWith("POINT");
        const itemType = isPts ? "points" : "signal";
        const title = isPts
          ? (sName.toUpperCase().startsWith("POINT") ? sName : `Points ${sName}`)
          : `Signal ${sName}`;
        addHit({
          type: itemType,
          id: s[0],
          title: title,
          sub: `${isPts ? "Points" : ilock.label} • Master ID #${s[2]}`,
          x: s[5],
          y: s[6],
          previewWay: sigWay,
          previewBox: { x: lbl.x, y: lbl.y, w: lbl.w, h: lbl.h, angle: lbl.angle, alignY: lbl.alignY },
          score: 3,
        });
      }
    }
  }

  // 1b. Signalling Block Sections & Block Labels
  if (
    (STATE.layers.blocks || (STATE.colorMode === "signalling" && zoom >= 0.55)) &&
    useMicro &&
    STATE.viewportData.blocks &&
    STATE.viewportData.blocks.length
  ) {
    for (const lbl of STATE.drawnBlockLabels) {
      if (isPointInOrientedBox(sx, sy, lbl.x, lbl.y, lbl.w, lbl.h, lbl.angle || 0, lbl.alignY || "center")) {
        const b = lbl.block;
        const coords = b[7];
        const midPt = coords ? coords[Math.floor(coords.length / 2)] : [0, 0];
        addHit({
          type: "block",
          id: b[0],
          title: `Signalling Block #${b[1]} (${b[4] === 1 ? "Shunt" : "Main"})`,
          sub: `Label hit • Pre-sig: ${b[2]}m • Sighting: ${b[3]}m`,
          x: midPt[0],
          y: midPt[1],
          previewWay: coords,
          previewBox: { x: lbl.x, y: lbl.y, w: lbl.w, h: lbl.h, angle: lbl.angle, alignY: lbl.alignY },
          score: 1.5,
        });
      }
    }
    for (const b of STATE.viewportData.blocks) {
      // [pk, block_id, pre_signal_distance, sight_distance, shunting_flag, atc_flag, moving_block, coords]
      const coords = b[7];
      if (!coords || coords.length < 2) continue;
      let minD = Infinity;
      for (let i = 0; i < coords.length - 1; i++) {
        const [x0, y0] = worldToScreen(coords[i][0], coords[i][1]);
        const [x1, y1] = worldToScreen(coords[i + 1][0], coords[i + 1][1]);
        const d = distToSegment(sx, sy, x0, y0, x1, y1);
        if (d < minD) minD = d;
      }
      if (minD <= pxTol) {
        const midPt = coords[Math.floor(coords.length / 2)];
        addHit({
          type: "block",
          id: b[0],
          title: `Signalling Block #${b[1]} (${b[4] === 1 ? "Shunt" : "Main"})`,
          sub: `Pre-sig: ${b[2]}m • Sighting: ${b[3]}m • ${coords.length} nodes`,
          x: midPt[0],
          y: midPt[1],
          previewWay: coords,
          score: minD + 1.8,
        });
      }
    }
  }

  // 2. Stations & Station Labels
  if (STATE.layers.stations && STATE.overview.stations) {
    const getStationTrackParts = (stId) =>
      useMicro && STATE.viewportData.tracks
        ? STATE.viewportData.tracks
            .filter((t) => t[2] === stId && t[7] && t[7].length >= 2)
            .map((t) => t[7])
        : [];
    for (const st of STATE.overview.stations) {
      const [stx, sty] = worldToScreen(st.x, st.y);
      const d = Math.hypot(sx - stx, sy - sty);
      if (d <= pxTol + 2) {
        addHit({
          type: "station",
          id: st.id,
          title: `${st.name} (${st.crs || st.abbrev})`,
          sub: `TIPLOC: ${st.abbrev} • STANOX: ${st.stanox || "N/A"} • ${st.active_tracks} tracks`,
          x: st.x,
          y: st.y,
          previewParts: getStationTrackParts(st.id),
          score: d,
        });
      }
    }
    for (const lbl of STATE.drawnStationLabels) {
      if (isPointInOrientedBox(sx, sy, lbl.x, lbl.y, lbl.w, lbl.h, lbl.angle || 0, lbl.alignY || "center")) {
        const st = lbl.st;
        addHit({
          type: "station",
          id: st.id,
          title: `${st.name} (${st.crs || st.abbrev})`,
          sub: `Label hit • TIPLOC: ${st.abbrev} • STANOX: ${st.stanox || "N/A"}`,
          x: st.x,
          y: st.y,
          previewParts: getStationTrackParts(st.id),
          previewBox: { x: lbl.x, y: lbl.y, w: lbl.w, h: lbl.h, angle: lbl.angle, alignY: lbl.alignY },
          score: 4,
        });
      }
    }
  }

  // 3. Microscopic Track Nodes & Node Labels
  if ((STATE.layers.nodes || zoom >= 2.2) && useMicro && STATE.viewportData.nodes.length) {
    for (const lbl of STATE.drawnNodeLabels) {
      if (isPointInOrientedBox(sx, sy, lbl.x, lbl.y, lbl.w, lbl.h, lbl.angle || 0, lbl.alignY || "center")) {
        const n = lbl.node;
        addHit({
          type: "node",
          id: n[0],
          title: `Track Node #${n[0]}`,
          sub: `Label hit • KM: ${n[5] || 0}m • Angle ${n[3]}°`,
          x: n[1],
          y: n[2],
          previewBox: { x: lbl.x, y: lbl.y, w: lbl.w, h: lbl.h, angle: lbl.angle, alignY: lbl.alignY },
          score: 2.5,
        });
      }
    }
    for (const n of STATE.viewportData.nodes) {
      const [nsx, nsy] = worldToScreen(n[1], n[2]);
      const d = Math.hypot(sx - nsx, sy - nsy);
      if (d <= pxTol) {
        const connEdges = STATE.viewportData.edges
          ? STATE.viewportData.edges
              .filter((e) => (e[2] === n[0] || e[3] === n[0]) && e[4] !== null && e[6] !== null)
              .map((e) => [
                [e[4], e[5]],
                [e[6], e[7]],
              ])
          : [];
        const isPtsNode = connEdges.length >= 3;
        addHit({
          type: "node",
          id: n[0],
          title: isPtsNode ? `Points Node #${n[0]} (Valency ${connEdges.length})` : `Track Node #${n[0]}`,
          sub: isPtsNode
            ? `Points • KM: ${n[5] || 0}m • Angle ${n[3]}°`
            : `KM: ${n[5] || 0}m • Angle ${n[3]}° • (${n[1]}, ${n[2]})`,
          x: n[1],
          y: n[2],
          previewParts: connEdges,
          score: d + 1,
        });
      }
    }
  }

  // 4. Station Platform / Track Ways
  if (STATE.layers.tracks && useMicro && STATE.viewportData.tracks.length) {
    for (const t of STATE.viewportData.tracks) {
      const coords = t[7];
      if (!coords || coords.length < 2) continue;
      let minD = Infinity;
      for (let i = 0; i < coords.length - 1; i++) {
        const [x0, y0] = worldToScreen(coords[i][0], coords[i][1]);
        const [x1, y1] = worldToScreen(coords[i + 1][0], coords[i + 1][1]);
        const d = distToSegment(sx, sy, x0, y0, x1, y1);
        if (d < minD) minD = d;
      }
      if (minD <= pxTol) {
        const midPt = coords[Math.floor(coords.length / 2)];
        addHit({
          type: "track",
          id: t[0],
          title: `Platform / Track ${t[3]} (${t[4] || "Track #" + t[1]})`,
          sub: `Track ID #${t[1]} • Station #${t[2]}`,
          x: midPt[0],
          y: midPt[1],
          previewWay: coords,
          score: minD + 2,
        });
      }
    }
  }

  // 5. Microscopic Track Edges
  if (STATE.layers.edges && useMicro && STATE.viewportData.edges.length) {
    for (const e of STATE.viewportData.edges) {
      if (e[4] === null || e[6] === null) continue;
      const [x0, y0] = worldToScreen(e[4], e[5]);
      const [x1, y1] = worldToScreen(e[6], e[7]);
      const d = distToSegment(sx, sy, x0, y0, x1, y1);
      if (d <= pxTol - 1) {
        addHit({
          type: "edge",
          id: e[0],
          title: `Track Edge #${e[1]} (${e[2]} → ${e[3]})`,
          sub: `${e[11]}m • Vmax ${e[8]} km/h • ${e[9] === 1 ? "Electrified" : "Diesel"} • Grad ${e[10]}‰`,
          x: (e[4] + e[6]) / 2,
          y: (e[5] + e[7]) / 2,
          previewWay: [
            [e[4], e[5]],
            [e[6], e[7]],
          ],
          score: d + 3,
        });
      }
    }
  }

  // 6. CAD Track Labels (<graphictext>)
  if (STATE.layers.texts && useMicro && STATE.drawnCadLabels.length) {
    for (const lbl of STATE.drawnCadLabels) {
      if (isPointInOrientedBox(sx, sy, lbl.x, lbl.y, lbl.w, lbl.h, lbl.angle || 0, lbl.alignY || "center")) {
        const gt = lbl.gt;
        addHit({
          type: "graphictext",
          id: gt[0],
          title: `CAD Label "${gt[4]}"`,
          sub: `Layer ${gt[3]} • Angle ${gt[5]}° • (${gt[1]}, ${gt[2]})`,
          x: gt[1],
          y: gt[2],
          previewBox: { x: lbl.x, y: lbl.y, w: lbl.w, h: lbl.h, angle: lbl.angle, alignY: lbl.alignY },
          score: 5,
        });
      }
    }
  }

  // 7. Operational Annotations (<annotation>) & Note Labels
  if (STATE.layers.polys && STATE.overview.annotations) {
    for (const lbl of STATE.drawnAnnotationLabels) {
      if (isPointInOrientedBox(sx, sy, lbl.x, lbl.y, lbl.w, lbl.h, lbl.angle || 0, lbl.alignY || "center")) {
        const an = lbl.an;
        const cleanText = (an.text || "").replace(/<[^>]+>/g, " ").trim().slice(0, 65);
        addHit({
          type: "annotation",
          id: an.pk,
          title: `Operational Note #${an.pk}`,
          sub: cleanText || `(${an.x}, ${an.y})`,
          x: an.x,
          y: an.y,
          previewBox: { x: lbl.x, y: lbl.y, w: lbl.w, h: lbl.h, angle: lbl.angle, alignY: lbl.alignY },
          score: 3.5,
        });
      }
    }
    for (const an of STATE.overview.annotations) {
      const [asx, asy] = worldToScreen(an.x, an.y);
      const d = Math.hypot(sx - asx, sy - asy);
      if (d <= pxTol + 2) {
        const cleanText = (an.text || "").replace(/<[^>]+>/g, " ").trim().slice(0, 65);
        addHit({
          type: "annotation",
          id: an.pk,
          title: `Operational Note #${an.pk}`,
          sub: cleanText || `(${an.x}, ${an.y})`,
          x: an.x,
          y: an.y,
          score: d,
        });
      }
    }
  }

  hits.sort((a, b) => {
    const aPriority = a.type === "block" || a.type === "route" ? 0 : 1;
    const bPriority = b.type === "block" || b.type === "route" ? 0 : 1;
    if (aPriority !== bPriority) return aPriority - bPriority;
    return a.score - b.score;
  });
  return hits.slice(0, 12);
}

function findNwrObjectsAtCursor(sx, sy, wx, wy) {
  if (!STATE.nwrOverview) return [];
  const hits = [];
  const seen = new Set();
  const addHit = (item) => {
    const key = `${item.type}:${item.id}`;
    if (!seen.has(key)) {
      seen.add(key);
      hits.push(item);
    }
  };

  const zoom = STATE.nwrCam ? STATE.nwrCam.zoom : STATE.zoom;
  const pxTol = 10;

  // 0. Clickable Geo Route Endpoint Markers (Entrance / Exit signals & buffers)
  if (STATE.crossHighlight && STATE.crossHighlight.nwr && STATE.crossHighlight.nwr.active) {
    const targetCanvas = STATE.activeView === "split" && nwrCanvas ? nwrCanvas : canvas;
    const cw = targetCanvas ? targetCanvas.clientWidth : 800;
    const ch = targetCanvas ? targetCanvas.clientHeight : 600;
    const camX = STATE.nwrCam ? STATE.nwrCam.camX : STATE.camX;
    const camY = STATE.nwrCam ? STATE.nwrCam.camY : STATE.camY;
    for (const pt of STATE.crossHighlight.nwr.points || []) {
      if (!pt.asset_type || pt.asset_id === undefined || pt.asset_id === null) continue;
      const psx = (pt.x - camX) * zoom + cw / 2;
      const psy = (camY - pt.y) * zoom + ch / 2;
      const d = Math.hypot(sx - psx, sy - psy);
      if (d <= 15) {
        addHit({
          type: pt.asset_type,
          id: pt.asset_id,
          title: pt.label || `${pt.asset_type} #${pt.asset_id}`,
          sub: "Click to step to this signal / endpoint",
          x: pt.x,
          y: pt.y,
          score: -10 + d * 0.01,
        });
      }
    }
  }

  // 1. NWR Track Nodes & Node Labels
  if (STATE.nwrLayers.nodes && zoom >= 0.035 && STATE.nwrViewportData.nodes) {
    for (const lbl of STATE.drawnNwrNodeLabels) {
      if (isPointInOrientedBox(sx, sy, lbl.x, lbl.y, lbl.w, lbl.h, lbl.angle || 0, lbl.alignY || "center")) {
        const n = lbl.node;
        const isPts = n[2] >= 3;
        addHit({
          type: "nwr_node",
          id: n[0],
          title: isPts ? `Points Node ${n[1]}` : `Track Node ${n[1]}`,
          sub: `Label hit • ${isPts ? "Points • " : ""}Valency ${n[2]} • (${n[3]} E, ${n[4]} N)`,
          x: n[3],
          y: n[4],
          previewBox: { x: lbl.x, y: lbl.y, w: lbl.w, h: lbl.h, angle: lbl.angle, alignY: lbl.alignY },
          score: 2,
        });
      }
    }
    for (const n of STATE.nwrViewportData.nodes) {
      // [fid, asset_id, valency, x, y]
      const [nsx, nsy] = worldToScreen(n[3], n[4]);
      const d = Math.hypot(sx - nsx, sy - nsy);
      if (d <= pxTol) {
        const isPts = n[2] >= 3;
        addHit({
          type: "nwr_node",
          id: n[0],
          title: isPts ? `Points Node ${n[1]}` : `Track Node ${n[1]}`,
          sub: `${isPts ? "Points • " : ""}Valency ${n[2]} • (${n[3]} E, ${n[4]} N)`,
          x: n[3],
          y: n[4],
          score: d,
        });
      }
    }
  }

  // 2. NWR Waymarks & Labels
  if (STATE.nwrLayers.waymarks && zoom >= 0.02 && STATE.nwrViewportData.waymarks) {
    for (const lbl of STATE.drawnNwrWaymarkLabels) {
      if (isPointInOrientedBox(sx, sy, lbl.x, lbl.y, lbl.w, lbl.h, lbl.angle || 0, lbl.alignY || "center")) {
        const wm = lbl.wm;
        addHit({
          type: "nwr_waymark",
          id: wm[0],
          title: `Waymark ${wm[2]} @ ${wm[4]} ${wm[3]}`,
          sub: `Label hit • Asset ID ${wm[1]}`,
          x: wm[5],
          y: wm[6],
          previewBox: { x: lbl.x, y: lbl.y, w: lbl.w, h: lbl.h, angle: lbl.angle, alignY: lbl.alignY },
          score: 2.5,
        });
      }
    }
    for (const wm of STATE.nwrViewportData.waymarks) {
      // [fid, asset_id, elr, unit, value, x, y]
      const [wsx, wsy] = worldToScreen(wm[5], wm[6]);
      const d = Math.hypot(sx - wsx, sy - wsy);
      if (d <= pxTol) {
        addHit({
          type: "nwr_waymark",
          id: wm[0],
          title: `Waymark ${wm[2]} @ ${wm[4]} ${wm[3]}`,
          sub: `Asset ID ${wm[1]}`,
          x: wm[5],
          y: wm[6],
          score: d + 0.5,
        });
      }
    }
  }

  // 3. NWR Track Centre Lines
  if (STATE.nwrLayers.centrelines && STATE.nwrViewportData.centrelines && STATE.nwrViewportData.centrelines.length > 0) {
    for (const cl of STATE.nwrViewportData.centrelines) {
      // [fid, asset_id, elr, track_id, s_my, e_my, owner, coords, len_m]
      const coords = cl[7];
      if (!coords || coords.length < 2) continue;
      let minD = Infinity;
      for (let i = 0; i < coords.length - 1; i++) {
        const [x0, y0] = worldToScreen(coords[i][0], coords[i][1]);
        const [x1, y1] = worldToScreen(coords[i + 1][0], coords[i + 1][1]);
        const d = distToSegment(sx, sy, x0, y0, x1, y1);
        if (d < minD) minD = d;
      }
      if (minD <= pxTol) {
        const midPt = coords[Math.floor(coords.length / 2)];
        addHit({
          type: "nwr_centreline",
          id: cl[0],
          title: `Track ${cl[2]} / ${cl[3]} (${cl[1]})`,
          sub: `${Number(cl[4]).toFixed(4)} → ${Number(cl[5]).toFixed(4)} mi.yd • ${cl[8]}m`,
          x: midPt[0],
          y: midPt[1],
          previewWay: coords,
          score: minD + 1.5,
        });
      }
    }
  }

  // 4. NWR ELR Labels & Reference Lines
  if (STATE.nwrLayers.elrs && STATE.nwrOverview.elrs) {
    for (const lbl of STATE.drawnNwrElrLabels) {
      if (isPointInOrientedBox(sx, sy, lbl.x, lbl.y, lbl.w, lbl.h, lbl.angle || 0, lbl.alignY || "center")) {
        const elr = lbl.elr;
        addHit({
          type: "nwr_elr",
          id: elr.fid,
          title: `ELR Corridor ${elr.elr} (${elr.asset_id})`,
          sub: `${Number(elr.start_my).toFixed(2)} → ${Number(elr.end_my).toFixed(2)} mi.yd`,
          x: elr.x,
          y: elr.y,
          previewWay: (elr.parts && elr.parts[0]) || [],
          previewParts: elr.parts || [],
          previewBox: { x: lbl.x, y: lbl.y, w: lbl.w, h: lbl.h, angle: lbl.angle, alignY: lbl.alignY },
          score: 2,
        });
      }
    }
    for (const elr of STATE.nwrOverview.elrs) {
      if (wx < elr.min_x - 2000 || wx > elr.max_x + 2000 || wy < elr.min_y - 2000 || wy > elr.max_y + 2000)
        continue;
      let minD = Infinity;
      for (const part of elr.parts || []) {
        for (let i = 0; i < part.length - 1; i++) {
          const [x0, y0] = worldToScreen(part[i][0], part[i][1]);
          const [x1, y1] = worldToScreen(part[i + 1][0], part[i + 1][1]);
          const d = distToSegment(sx, sy, x0, y0, x1, y1);
          if (d < minD) minD = d;
        }
      }
      if (minD <= pxTol) {
        addHit({
          type: "nwr_elr",
          id: elr.fid,
          title: `ELR Corridor ${elr.elr} (${elr.asset_id})`,
          sub: `${Number(elr.start_my).toFixed(2)} → ${Number(elr.end_my).toFixed(2)} mi.yd`,
          x: elr.x,
          y: elr.y,
          previewWay: (elr.parts && elr.parts[0]) || [],
          previewParts: elr.parts || [],
          score: minD + 3,
        });
      }
    }
  }

  const highlightedFusion = getHighlightedTrackFusionData();

  // 5. Network Fusion Curvature Segments & Labels (only when visible for highlighted track)
  if (STATE.nwrLayers.curvature && highlightedFusion && highlightedFusion.curvature.length > 0) {
    for (const lbl of STATE.drawnNfCurvatureLabels) {
      if (isPointInOrientedBox(sx, sy, lbl.x, lbl.y, lbl.w, lbl.h, lbl.angle || 0, lbl.alignY || "center")) {
        const c = lbl.curv;
        const pk = c.pk !== undefined ? c.pk : c[0];
        const elr = c.elr !== undefined ? c.elr : c[1];
        const rClass = c.radius_avg_class !== undefined ? c.radius_avg_class : c[9];
        const rAvg = c.radius_avg_m !== undefined ? c.radius_avg_m : c[8];
        const rMin = c.radius_min_m !== undefined ? c.radius_min_m : c[6];
        const mFrom = c.mileage_from !== undefined ? c.mileage_from : c[4];
        const mTo = c.mileage_to !== undefined ? c.mileage_to : c[5];
        const x0 = c.x0 !== undefined ? c.x0 : c[10];
        const y0 = c.y0 !== undefined ? c.y0 : c[11];
        const x1 = c.x1 !== undefined ? c.x1 : c[12];
        const y1 = c.y1 !== undefined ? c.y1 : c[13];
        addHit({
          type: "nf_curvature",
          id: pk,
          title: `Curvature ${elr} (${rClass || "Curve"})`,
          sub: `Label hit • R=${rAvg}m • ${mFrom} → ${mTo}`,
          x: (x0 + x1) / 2,
          y: (y0 + y1) / 2,
          previewWay: [[x0, y0], [x1, y1]],
          previewBox: { x: lbl.x, y: lbl.y, w: lbl.w, h: lbl.h, angle: lbl.angle, alignY: lbl.alignY },
          score: 1.8,
        });
      }
    }
    for (const c of highlightedFusion.curvature) {
      const x0 = c.x0 !== undefined ? c.x0 : c[10];
      const y0 = c.y0 !== undefined ? c.y0 : c[11];
      const x1 = c.x1 !== undefined ? c.x1 : c[12];
      const y1 = c.y1 !== undefined ? c.y1 : c[13];
      if (x0 === undefined || y0 === undefined || x1 === undefined || y1 === undefined) continue;
      const [sx0, sy0] = worldToScreen(x0, y0);
      const [sx1, sy1] = worldToScreen(x1, y1);
      const d = distToSegment(sx, sy, sx0, sy0, sx1, sy1);
      if (d <= pxTol) {
        const pk = c.pk !== undefined ? c.pk : c[0];
        const elr = c.elr !== undefined ? c.elr : c[1];
        const rClass = c.radius_avg_class !== undefined ? c.radius_avg_class : c[9];
        const rAvg = c.radius_avg_m !== undefined ? c.radius_avg_m : c[8];
        const rMin = c.radius_min_m !== undefined ? c.radius_min_m : c[6];
        const mFrom = c.mileage_from !== undefined ? c.mileage_from : c[4];
        const mTo = c.mileage_to !== undefined ? c.mileage_to : c[5];
        addHit({
          type: "nf_curvature",
          id: pk,
          title: `Curvature ${elr} (${rClass || "Curve"})`,
          sub: `R=${rAvg}m (Min: ${rMin}m) • ${mFrom} → ${mTo}`,
          x: (x0 + x1) / 2,
          y: (y0 + y1) / 2,
          previewWay: [[x0, y0], [x1, y1]],
          score: d + 1.2,
        });
      }
    }
  }

  // 6. Network Fusion Tenth-Mile Sample Points & Labels (only when visible for highlighted track)
  if (STATE.nwrLayers.nf_points && highlightedFusion && highlightedFusion.points.length > 0) {
    for (const lbl of STATE.drawnNfPointLabels) {
      if (isPointInOrientedBox(sx, sy, lbl.x, lbl.y, lbl.w, lbl.h, lbl.angle || 0, lbl.alignY || "center")) {
        const p = lbl.point;
        const pk = p.pk !== undefined ? p.pk : p[0];
        const elr = p.elr !== undefined ? p.elr : p[1];
        const mileage = p.mileage !== undefined ? p.mileage : p[3];
        const elev = p.elevation_m !== undefined ? p.elevation_m : p[6];
        const loc = (p.place_name || p.postcode) || (p[7] || p[8] || "");
        const px = p.easting !== undefined ? p.easting : p[4];
        const py = p.northing !== undefined ? p.northing : p[5];
        addHit({
          type: "nf_point",
          id: pk,
          title: `Tenth-Mile Point ${elr} @ ${mileage}`,
          sub: `Label hit • ${elev}m elev • ${loc}`,
          x: px,
          y: py,
          previewBox: { x: lbl.x, y: lbl.y, w: lbl.w, h: lbl.h, angle: lbl.angle, alignY: lbl.alignY },
          score: 2.0,
        });
      }
    }
    for (const p of highlightedFusion.points) {
      const px = p.easting !== undefined ? p.easting : p[4];
      const py = p.northing !== undefined ? p.northing : p[5];
      if (px === undefined || py === undefined) continue;
      const [psx, psy] = worldToScreen(px, py);
      const d = Math.hypot(sx - psx, sy - psy);
      if (d <= pxTol) {
        const pk = p.pk !== undefined ? p.pk : p[0];
        const elr = p.elr !== undefined ? p.elr : p[1];
        const mileage = p.mileage !== undefined ? p.mileage : p[3];
        const elev = p.elevation_m !== undefined ? p.elevation_m : p[6];
        const loc = (p.place_name || p.postcode) || (p[7] || p[8] || "");
        addHit({
          type: "nf_point",
          id: pk,
          title: `Tenth-Mile Point ${elr} @ ${mileage}`,
          sub: `${elev}m elev • ${loc}`,
          x: px,
          y: py,
          score: d + 1.0,
        });
      }
    }
  }

  hits.sort((a, b) => a.score - b.score);
  return hits.slice(0, 12);
}


/* Hover Detection & Precision Pointer Cursor (No floating tooltip) */
function handleHover(sx, sy, wx, wy, targetView = null) {
  const view = targetView || (STATE.activeView === "nwr" ? "nwr" : "tps");
  const targetCanvas = view === "nwr" && nwrCanvas ? nwrCanvas : canvas;
  if (
    sx < 0 ||
    sy < 0 ||
    sx > targetCanvas.clientWidth ||
    sy > targetCanvas.clientHeight
  ) {
    targetCanvas.classList.remove("has-target");
    return;
  }

  const hits = findObjectsAtCursor(sx, sy, wx, wy, view);
  if (hits.length > 0) {
    targetCanvas.classList.add("has-target");
    STATE.hoverCandidates = hits;
  } else {
    targetCanvas.classList.remove("has-target");
    STATE.hoverCandidates = [];
  }
}

/* Candidate Display & Interaction Helpers */
function getAssetIcon(type) {
  switch (type) {
    case "points": return "🔀";
    case "signal": return "🚦";
    case "station": return "🚉";
    case "platform": return "🚏";
    case "track":
    case "nwr_track": return "🛤️";
    case "block": return "🔲";
    case "node":
    case "nwr_node": return "📍";
    case "nwr_elr": return "🏷️";
    case "nwr_milepost": return "📍";
    default: return "🔹";
  }
}

function formatAssetTypeName(type) {
  switch (type) {
    case "points": return "Points";
    case "signal": return "Signal";
    case "station": return "Station";
    case "platform": return "Platform";
    case "track": return "Track";
    case "block": return "Block";
    case "node": return "Node";
    case "nwr_track": return "NWR Track";
    case "nwr_node": return "NWR Node";
    case "nwr_elr": return "ELR";
    case "nwr_milepost": return "Milepost";
    default: return String(type).replace("nwr_", "").toUpperCase();
  }
}

function formatAssetSubtitle(c) {
  if (c.sub) return c.sub;
  if (c.type === "points") return `Points ID #${c.id}`;
  if (c.type === "signal") return `Signal ID #${c.id}`;
  if (c.type === "track") return `Track ID #${c.id}`;
  if (c.type === "block") return `Block #${c.id}`;
  if (c.type === "station") return `Station #${c.id}`;
  if (c.type === "node") return `Node #${c.id}`;
  if (c.x !== undefined && c.y !== undefined) return `(${Math.round(c.x)}, ${Math.round(c.y)})`;
  return `ID #${c.id}`;
}

function mergeCandidates(clientHits, serverCandidates) {
  const byKey = new Map();

  // Add client hits first (they have exact canvas coordinates, label bounding boxes, etc.)
  for (const c of clientHits || []) {
    const key = `${c.type}:${c.id}`;
    byKey.set(key, { ...c });
  }

  // Augment with server candidates
  for (const s of serverCandidates || []) {
    let key = `${s.type}:${s.id}`;
    let existing = byKey.get(key);
    if (!existing && (s.type === "signal" || s.type === "points")) {
      const altKey = s.type === "signal" ? `points:${s.id}` : `signal:${s.id}`;
      const altExisting = byKey.get(altKey);
      if (altExisting) {
        if (s.type === "points") {
          byKey.delete(altKey);
          altExisting.type = "points";
          byKey.set(key, altExisting);
          existing = altExisting;
        } else {
          existing = altExisting;
        }
      }
    }
    if (!existing) {
      byKey.set(key, {
        type: s.type,
        id: s.id,
        title: s.label || `${s.type} #${s.id}`,
        sub: s.sub || "",
        x: s.x,
        y: s.y,
        previewWay: s.coords || null,
        previewParts: s.parts || null,
      });
    } else {
      if (!existing.previewWay && s.coords) existing.previewWay = s.coords;
      if (!existing.previewParts && s.parts) existing.previewParts = s.parts;
      if (!existing.sub && s.sub) existing.sub = s.sub;
      if (existing.x === undefined && s.x !== undefined) existing.x = s.x;
      if (existing.y === undefined && s.y !== undefined) existing.y = s.y;
    }
  }

  const list = Array.from(byKey.values());

  const typeOrder = {
    points: 1,
    signal: 1.5,
    station: 2,
    platform: 3,
    block: 4,
    track: 5,
    nwr_track: 5,
    nwr_elr: 6,
    nwr_milepost: 7,
    node: 8,
    nwr_node: 8,
    edge: 9,
  };

  list.sort((a, b) => {
    const pa = typeOrder[a.type] || 99;
    const pb = typeOrder[b.type] || 99;
    return pa - pb;
  });

  return list;
}

function renderPickCandidates(cands, activeItem) {
  const candBar = document.getElementById("pick-candidates");
  if (!candBar) return;
  if (!cands || cands.length <= 1) {
    candBar.classList.add("hidden");
    candBar.innerHTML = "";
    return;
  }

  const activeKey = activeItem
    ? `${activeItem.type}:${activeItem.id}`
    : `${cands[0].type}:${cands[0].id}`;

  candBar.innerHTML = `
    <div class="pick-candidates-header">
      <div class="pick-candidates-title-group">
        <span class="pick-candidates-badge">${cands.length}</span>
        <span class="pick-candidates-heading">Overlapping Objects at Click</span>
      </div>
      <span class="pick-candidates-hint">Hover to highlight • Click to inspect</span>
    </div>
    <div class="pick-candidates-list">
      ${cands
        .map((c, i) => {
          const key = `${c.type}:${c.id}`;
          const isActive = key === activeKey;
          const icon = getAssetIcon(c.type);
          const typeName = formatAssetTypeName(c.type);
          const subtitle = c.sub || formatAssetSubtitle(c);
          return `
            <button class="candidate-row ${isActive ? "active" : ""}" data-idx="${i}" data-type="${escapeHtml(c.type)}" data-id="${escapeHtml(String(c.id))}">
              <span class="candidate-icon candidate-icon-${escapeHtml(c.type)}">${icon}</span>
              <div class="candidate-info">
                <div class="candidate-main">
                  <span class="candidate-type-badge candidate-type-${escapeHtml(c.type)}">${escapeHtml(typeName)}</span>
                  <span class="candidate-label" title="${escapeHtml(c.title || c.label)}">${escapeHtml(c.title || c.label)}</span>
                </div>
                ${subtitle ? `<div class="candidate-sub" title="${escapeHtml(subtitle)}">${escapeHtml(subtitle)}</div>` : ""}
              </div>
              <span class="candidate-arrow">&rarr;</span>
            </button>
          `;
        })
        .join("")}
    </div>
  `;

  candBar.classList.remove("hidden");

  candBar.querySelectorAll(".candidate-row").forEach((btn) => {
    const idx = parseInt(btn.dataset.idx, 10);
    const item = cands[idx];
    btn.addEventListener("mouseenter", () => {
      if (item) {
        STATE.hoverPreview = item;
        requestRender();
      }
    });
    btn.addEventListener("mouseleave", () => {
      if (STATE.hoverPreview === item) {
        STATE.hoverPreview = null;
        requestRender();
      }
    });
    btn.addEventListener("click", () => {
      candBar.querySelectorAll(".candidate-row").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      loadAssetDetails(btn.dataset.type, btn.dataset.id, false);
    });
  });
}

