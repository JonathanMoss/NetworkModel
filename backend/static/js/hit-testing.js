/**
 * @fileoverview Client-side spatial hit-testing dispatcher and TPS
 * schematic layer hit detection.
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
