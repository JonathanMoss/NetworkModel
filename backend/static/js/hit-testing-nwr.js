/**
 * @fileoverview Client-side spatial hit-testing for the NWR Track Model
 * (clickable Geo route markers, track nodes, centrelines, ELRs, waymarks, Fusion).
 */

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
