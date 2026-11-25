/**
 * @fileoverview HTML5 Canvas renderer for the NWR Geospatial Track Model (OSGB36)
 * and Network Fusion overlays.
 */

/* ============================================================================
   VIEW 2: NWR GEOSPATIAL TRACK MODEL RENDER LOOP (OSGB36 BRITISH NATIONAL GRID)
   ============================================================================ */
function getHighlightedTrackFusionData() {
  if (!STATE.selectedAsset) return null;
  const sa = STATE.selectedAsset;
  const isTrack =
    sa.asset_type === "nwr_centreline" ||
    sa.asset_type === "nwr_elr" ||
    sa.asset_type === "track" ||
    sa.asset_type === "edge" ||
    sa.asset_type === "nf_curvature" ||
    sa.asset_type === "nf_point";
  if (!isTrack) return null;

  const hasHighlight =
    (STATE.highlight.ways && STATE.highlight.ways.length > 0) ||
    (STATE.highlight.segments && STATE.highlight.segments.length > 0) ||
    (STATE.highlight.points && STATE.highlight.points.length > 0);
  if (!hasHighlight) return null;

  const nf = sa.related?.network_fusion || {};
  let curvList = Array.isArray(nf.curvature) ? [...nf.curvature] : [];
  let ptList = Array.isArray(nf.points) ? [...nf.points] : [];

  if (sa.asset_type === "nf_curvature" && sa.raw_attrs) {
    if (!curvList.some((c) => (c.pk || c[0]) === (sa.raw_attrs.pk || sa.id))) {
      curvList.push(sa.raw_attrs);
    }
  }
  if (sa.asset_type === "nf_point" && sa.raw_attrs) {
    if (!ptList.some((p) => (p.pk || p[0]) === (sa.raw_attrs.pk || sa.id))) {
      ptList.push(sa.raw_attrs);
    }
  }

  const selElr =
    (sa.decoded && sa.decoded["Engineer's Line Reference (ELR)"]) ||
    sa.raw_attrs?.ELR ||
    sa.elr ||
    (sa.related?.elr_record && sa.related.elr_record.elr);

  if (selElr && (!curvList.length || !ptList.length)) {
    const elrUpper = String(selElr).toUpperCase();
    if (!curvList.length && STATE.nwrViewportData?.curvature) {
      for (const c of STATE.nwrViewportData.curvature) {
        const cElr = c.elr || c[1];
        if (cElr && String(cElr).toUpperCase() === elrUpper) {
          curvList.push(c);
        }
      }
    }
    if (!ptList.length && STATE.nwrViewportData?.nf_points) {
      for (const p of STATE.nwrViewportData.nf_points) {
        const pElr = p.elr || p[1];
        if (pElr && String(pElr).toUpperCase() === elrUpper) {
          ptList.push(p);
        }
      }
    }
  }

  return {
    curvature: curvList,
    points: ptList,
  };
}

function renderNwrModel() {
  const targetCanvas = (STATE.activeView === "split" || STATE.activeView === "nwr") ? (nwrCanvas || canvas) : canvas;
  const ctx = targetCanvas.getContext("2d");
  const w = targetCanvas.clientWidth;
  const h = targetCanvas.clientHeight;
  ctx.clearRect(0, 0, w, h);

  // 0. OpenStreetMap Basemap Layer (rendered underneath all vector layers)
  if (STATE.nwrLayers.osm) {
    renderOsmTiles(ctx, w, h);
  }

  STATE.drawnNwrElrLabels = [];
  STATE.drawnNwrWaymarkLabels = [];
  STATE.drawnNwrNodeLabels = [];
  STATE.drawnNfCurvatureLabels = [];
  STATE.drawnNfPointLabels = [];

  if (!STATE.nwrOverview) return;

  const worldToScreen = (wx, wy) => [
    (wx - STATE.nwrCam.camX) * STATE.nwrCam.zoom + w / 2,
    (STATE.nwrCam.camY - wy) * STATE.nwrCam.zoom + h / 2,
  ];
  const zoom = STATE.nwrCam.zoom;
  const bbox = getViewportBBox(0.05, "nwr");
  const useDetailedCentrelines = zoom >= 0.008 && STATE.nwrViewportData.centrelines && STATE.nwrViewportData.centrelines.length > 0;

  // 1. NWR_ELRs Reference Lines
  if (STATE.nwrLayers.elrs && STATE.nwrOverview.elrs) {
    const elrWidthMult = ((STATE.layerStyles && STATE.layerStyles.nwr_elrs && STATE.layerStyles.nwr_elrs.width) || 2.0) / 2.0;
    const elrCol = (STATE.layerStyles && STATE.layerStyles.nwr_elrs && STATE.layerStyles.nwr_elrs.color) || "#a855f7";
    ctx.strokeStyle = hexToRgba(elrCol, 0.45);
    ctx.lineWidth = Math.max(0.8, (useDetailedCentrelines ? 2.5 : 1.2) * elrWidthMult);
    ctx.beginPath();
    for (const elr of STATE.nwrOverview.elrs) {
      if (
        elr.max_x < bbox.min_x ||
        elr.min_x > bbox.max_x ||
        elr.max_y < bbox.min_y ||
        elr.min_y > bbox.max_y
      )
        continue;
      for (const part of elr.parts || []) {
        if (part.length < 2) continue;
        const [sx0, sy0] = worldToScreen(part[0][0], part[0][1]);
        ctx.moveTo(sx0, sy0);
        for (let i = 1; i < part.length; i++) {
          const [sxi, syi] = worldToScreen(part[i][0], part[i][1]);
          ctx.lineTo(sxi, syi);
        }
      }
    }
    ctx.stroke();
  }

  // 2. NWR_TrackCentreLines (Macro links when zoomed out, full polylines when zoomed in)
  if (STATE.nwrLayers.centrelines) {
    const clWidthMult = ((STATE.layerStyles && STATE.layerStyles.nwr_centrelines && STATE.layerStyles.nwr_centrelines.width) || 1.8) / 1.8;
    if (!useDetailedCentrelines) {
      const batches = new Map();
      for (const m of STATE.nwrOverview.macro_links) {
        // [fid, elr, track_id, own_code, x0, y0, x1, y1]
        const x0 = m[4],
          y0 = m[5],
          x1 = m[6],
          y1 = m[7];
        if (
          Math.max(x0, x1) < bbox.min_x ||
          Math.min(x0, x1) > bbox.max_x ||
          Math.max(y0, y1) < bbox.min_y ||
          Math.min(y0, y1) > bbox.max_y
        )
          continue;
        const col = getNwrTrackColor(m[2], m[3], m[1]);
        let arr = batches.get(col);
        if (!arr) {
          arr = [];
          batches.set(col, arr);
        }
        arr.push(x0, y0, x1, y1);
      }
      ctx.lineWidth = Math.max(0.8, Math.max(1.1, Math.min(2.2, zoom * 250)) * clWidthMult);
      for (const [col, coords] of batches.entries()) {
        ctx.strokeStyle = col;
        ctx.beginPath();
        for (let i = 0; i < coords.length; i += 4) {
          const [sx0, sy0] = worldToScreen(coords[i], coords[i + 1]);
          const [sx1, sy1] = worldToScreen(coords[i + 2], coords[i + 3]);
          ctx.moveTo(sx0, sy0);
          ctx.lineTo(sx1, sy1);
        }
        ctx.stroke();
      }
    } else {
      const batches = new Map();
      for (const cl of STATE.nwrViewportData.centrelines) {
        // [fid, asset_id, elr, track_id, s_my, e_my, owner, coords, len_m]
        const coords = cl[7];
        if (!coords || coords.length < 2) continue;
        const col = getNwrTrackColor(cl[3], cl[6], cl[2]);
        let arr = batches.get(col);
        if (!arr) {
          arr = [];
          batches.set(col, arr);
        }
        arr.push(coords);
      }
      ctx.lineWidth = Math.max(1.0, Math.max(1.6, Math.min(3.8, zoom * 12)) * clWidthMult);
      for (const [col, polylines] of batches.entries()) {
        ctx.strokeStyle = col;
        ctx.beginPath();
        for (const pts of polylines) {
          const [sx0, sy0] = worldToScreen(pts[0][0], pts[0][1]);
          ctx.moveTo(sx0, sy0);
          for (let i = 1; i < pts.length; i++) {
            const [sxi, syi] = worldToScreen(pts[i][0], pts[i][1]);
            ctx.lineTo(sxi, syi);
          }
        }
        ctx.stroke();
      }
    }
  }

  const highlightedFusion = getHighlightedTrackFusionData();

  // 3. Network Fusion Track Curvature & Cant Segments (Contextual: shown ONLY when a track is clicked/highlighted AND layer is checked)
  if (STATE.nwrLayers.curvature && highlightedFusion && highlightedFusion.curvature.length > 0) {
    const curvWidthMult = ((STATE.layerStyles && STATE.layerStyles.nwr_curvature && STATE.layerStyles.nwr_curvature.width) || 2.5) / 2.5;
    const isCurvUniform = STATE.nwrColorMode === "uniform";
    const customCurvCol = (STATE.layerStyles && STATE.layerStyles.nwr_curvature && STATE.layerStyles.nwr_curvature.color) || "#f43f5e";
    const curvBatches = new Map();
    for (const c of highlightedFusion.curvature) {
      const x0 = c.x0 !== undefined ? c.x0 : c[10];
      const y0 = c.y0 !== undefined ? c.y0 : c[11];
      const x1 = c.x1 !== undefined ? c.x1 : c[12];
      const y1 = c.y1 !== undefined ? c.y1 : c[13];
      if (x0 === undefined || y0 === undefined || x1 === undefined || y1 === undefined) continue;
      const rAvg = c.radius_avg_m !== undefined ? c.radius_avg_m : c[8];
      const col = isCurvUniform ? customCurvCol : getCurvatureColor(rAvg);
      let arr = curvBatches.get(col);
      if (!arr) {
        arr = [];
        curvBatches.set(col, arr);
      }
      arr.push(x0, y0, x1, y1);
    }

    ctx.save();
    ctx.lineWidth = Math.max(1.5, Math.max(3.8, Math.min(8.0, zoom * 24)) * curvWidthMult);
    for (const [col, coords] of curvBatches.entries()) {
      ctx.strokeStyle = col;
      ctx.shadowColor = col;
      ctx.shadowBlur = 8;
      ctx.beginPath();
      for (let i = 0; i < coords.length; i += 4) {
        const [sx0, sy0] = worldToScreen(coords[i], coords[i + 1]);
        const [sx1, sy1] = worldToScreen(coords[i + 2], coords[i + 3]);
        ctx.moveTo(sx0, sy0);
        ctx.lineTo(sx1, sy1);
      }
      ctx.stroke();
    }
    ctx.restore();

    // Radius badges & labels for highlighted track
    if (zoom >= 0.003) {
      const curvScale = getLayerLabelScale("nf_curvature");
      const curvAngle = getLayerLabelAngle("nf_curvature");
      const fontSize = Math.max(9, Math.round(9.5 * curvScale));
      ctx.font = `700 ${fontSize}px ui-monospace, monospace`;

      for (const c of highlightedFusion.curvature) {
        const x0 = c.x0 !== undefined ? c.x0 : c[10];
        const y0 = c.y0 !== undefined ? c.y0 : c[11];
        const x1 = c.x1 !== undefined ? c.x1 : c[12];
        const y1 = c.y1 !== undefined ? c.y1 : c[13];
        if (x0 === undefined || y0 === undefined || x1 === undefined || y1 === undefined) continue;
        const rAvg = c.radius_avg_m !== undefined ? c.radius_avg_m : c[8];
        const rClass = c.radius_avg_class !== undefined ? c.radius_avg_class : c[9];
        const mx = (x0 + x1) / 2;
        const my = (y0 + y1) / 2;
        const [sx, sy] = worldToScreen(mx, my);
        if (sx < -30 || sx > w + 30 || sy < -30 || sy > h + 30) continue;
        const lbl = `R=${rAvg}m (${rClass || "Curve"})`;
        const lw = lbl.length * (fontSize * 0.6) + 8;
        const lh = fontSize + 6;

        ctx.save();
        ctx.translate(sx, sy);
        if (curvAngle) ctx.rotate((curvAngle * Math.PI) / 180);
        ctx.textBaseline = "middle";
        ctx.fillStyle = "rgba(15, 23, 42, 0.92)";
        ctx.fillRect(-2, -lh / 2, lw, lh);
        ctx.strokeStyle = getCurvatureColor(rAvg);
        ctx.lineWidth = 1.5;
        ctx.strokeRect(-2, -lh / 2, lw, lh);
        ctx.fillStyle = "#f8fafc";
        ctx.fillText(lbl, 2, 0);
        ctx.restore();

        STATE.drawnNfCurvatureLabels.push({
          x: sx,
          y: sy,
          w: lw,
          h: lh,
          angle: curvAngle,
          alignY: "center",
          curv: c,
        });
      }
    }
  }

  // 4. Network Fusion Tenth-Mile Sample Points (Contextual: shown ONLY when a track is clicked/highlighted AND layer is checked)
  if (STATE.nwrLayers.nf_points && highlightedFusion && highlightedFusion.points.length > 0) {
    const ptScale = getLayerLabelScale("nf_points");
    const ptAngle = getLayerLabelAngle("nf_points");
    const fontSize = Math.max(9, Math.round(9.5 * ptScale));
    const r = Math.max(3.8, Math.min(7.5, zoom * 20));

    for (const p of highlightedFusion.points) {
      const px = p.easting !== undefined ? p.easting : p[4];
      const py = p.northing !== undefined ? p.northing : p[5];
      if (px === undefined || py === undefined) continue;
      const elev = p.elevation_m !== undefined ? p.elevation_m : p[6];
      const loc = (p.place_name || p.postcode) || (p[7] || p[8] || "");
      const mileage = p.mileage || p[3] || "";
      const [sx, sy] = worldToScreen(px, py);
      if (sx < -30 || sx > w + 30 || sy < -30 || sy > h + 30) continue;
      const col = getElevationColor(elev);

      ctx.save();
      ctx.fillStyle = col;
      ctx.strokeStyle = "#ffffff";
      ctx.lineWidth = 1.5;
      ctx.shadowColor = col;
      ctx.shadowBlur = 6;
      ctx.beginPath();
      // Draw diamond
      ctx.moveTo(sx, sy - r);
      ctx.lineTo(sx + r, sy);
      ctx.lineTo(sx, sy + r);
      ctx.lineTo(sx - r, sy);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.restore();

      if (zoom >= 0.003) {
        const lbl = `${elev !== null && elev !== undefined ? elev + "m" : ""}${mileage ? " • " + mileage : ""}${loc ? " • " + loc : ""}`;
        const lw = lbl.length * (fontSize * 0.6) + 8;
        const lh = fontSize + 6;

        ctx.save();
        ctx.translate(sx + r + 4, sy);
        if (ptAngle) ctx.rotate((ptAngle * Math.PI) / 180);
        ctx.textBaseline = "middle";
        ctx.fillStyle = "rgba(15, 23, 42, 0.92)";
        ctx.fillRect(-2, -lh / 2, lw, lh);
        ctx.strokeStyle = col;
        ctx.lineWidth = 1.5;
        ctx.strokeRect(-2, -lh / 2, lw, lh);
        ctx.fillStyle = col;
        ctx.font = `700 ${fontSize}px ui-monospace, monospace`;
        ctx.fillText(lbl, 2, 0);
        ctx.restore();

        STATE.drawnNfPointLabels.push({
          x: sx + r + 4,
          y: sy,
          w: lw,
          h: lh,
          angle: ptAngle,
          alignY: "center",
          point: p,
        });
      }
    }
  }

  // 5. NWR_TrackNodes (Valency 1-4)
  if (STATE.nwrLayers.nodes && zoom >= 0.035 && STATE.nwrViewportData.nodes && STATE.nwrViewportData.nodes.length) {
    const r = Math.max(2.0, Math.min(4.8, zoom * 16));
    for (const n of STATE.nwrViewportData.nodes) {
      // [fid, asset_id, valency, x, y]
      const [sx, sy] = worldToScreen(n[3], n[4]);
      if (sx < -10 || sx > w + 10 || sy < -10 || sy > h + 10) continue;
      const val = n[2];
      ctx.fillStyle =
        val === 3
          ? "#10b981" // Switch / Turnout
          : val === 4
          ? "#f43f5e" // Diamond crossing
          : val === 1
          ? "#f59e0b" // Buffer / End
          : "#64748b"; // Pseudo node
      ctx.beginPath();
      ctx.arc(sx, sy, r, 0, Math.PI * 2);
      ctx.fill();
    }

    // Node asset ID labels when zoomed in
    if (zoom >= 0.12) {
      const nodeScale = getLayerLabelScale("nwr_nodes");
      const nodeAngle = getLayerLabelAngle("nwr_nodes");
      const fontSize = Math.round(8.5 * nodeScale);
      ctx.font = `600 ${fontSize}px ui-monospace, monospace`;
      for (const n of STATE.nwrViewportData.nodes) {
        const [sx, sy] = worldToScreen(n[3], n[4]);
        if (sx < -20 || sx > w + 20 || sy < -20 || sy > h + 20) continue;
        const nText = `${n[1]}`;
        const nw = nText.length * (fontSize * 0.6) + 4;
        const nh = fontSize + 2;
        ctx.save();
        ctx.translate(sx + 6, sy);
        if (nodeAngle) ctx.rotate((nodeAngle * Math.PI) / 180);
        ctx.textBaseline = "middle";
        ctx.fillStyle = "#94a3b8";
        ctx.fillText(nText, 0, 0);
        ctx.restore();
        STATE.drawnNwrNodeLabels.push({
          x: sx + 6,
          y: sy,
          w: nw,
          h: nh,
          angle: nodeAngle,
          alignY: "center",
          node: n,
        });
      }
    }
  }

  // 6. NWR_Waymarks (Mileposts)
  if (STATE.nwrLayers.waymarks && zoom >= 0.02 && STATE.nwrViewportData.waymarks && STATE.nwrViewportData.waymarks.length) {
    const wmScale = getLayerLabelScale("nwr_waymarks");
    const wmAngle = getLayerLabelAngle("nwr_waymarks");
    const fontSize = Math.round(9 * wmScale);

    for (const wm of STATE.nwrViewportData.waymarks) {
      // [fid, asset_id, elr, unit, value, x, y]
      const [sx, sy] = worldToScreen(wm[5], wm[6]);
      if (sx < -20 || sx > w + 20 || sy < -20 || sy > h + 20) continue;
      ctx.fillStyle = "#fbbf24";
      ctx.fillRect(sx - 2.5, sy - 2.5, 5, 5);

      if (zoom >= 0.12) {
        const lbl = `${wm[2]} ${Number(wm[4]).toFixed(2)}${wm[3]}`;
        const lw = lbl.length * (fontSize * 0.6) + 6;
        const lh = fontSize + 4;
        const lx = sx + 5;
        const ly = sy;

        ctx.save();
        ctx.translate(lx, ly);
        if (wmAngle) ctx.rotate((wmAngle * Math.PI) / 180);
        ctx.textBaseline = "middle";
        ctx.fillStyle = "rgba(15, 23, 42, 0.88)";
        ctx.fillRect(-2, -lh / 2, lw, lh);
        ctx.fillStyle = "#fde68a";
        ctx.font = `600 ${fontSize}px ui-monospace, monospace`;
        ctx.fillText(lbl, 1, 0);
        ctx.restore();

        STATE.drawnNwrWaymarkLabels.push({
          x: lx,
          y: ly,
          w: lw,
          h: lh,
          angle: wmAngle,
          alignY: "center",
          wm,
        });
      }
    }
  }

  // 5. ELR Corridor Labels (with collision avoidance)
  if (STATE.nwrLayers.elrs && STATE.nwrOverview.elrs) {
    const elrScale = getLayerLabelScale("nwr_elrs");
    const elrAngle = getLayerLabelAngle("nwr_elrs");
    const fontSize = Math.round(10 * elrScale);
    const occupied = [];

    for (const elr of STATE.nwrOverview.elrs) {
      if (
        elr.x < bbox.min_x ||
        elr.x > bbox.max_x ||
        elr.y < bbox.min_y ||
        elr.y > bbox.max_y
      )
        continue;
      const [sx, sy] = worldToScreen(elr.x, elr.y);
      const lbl = elr.elr;
      const estW = lbl.length * (fontSize * 0.68) + 10;
      const estH = fontSize + 6;
      const lx = sx;
      const ly = sy;

      let collides = false;
      if (elrAngle === 0) {
        for (const b of occupied) {
          if (
            lx - estW / 2 < b.x + b.w + 14 &&
            lx + estW / 2 + 14 > b.x &&
            ly - estH / 2 < b.y + b.h + 8 &&
            ly + estH / 2 + 8 > b.y
          ) {
            collides = true;
            break;
          }
        }
      }
      if (!collides) {
        const box = { x: lx - estW / 2, y: ly, w: estW, h: estH, angle: elrAngle, alignY: "center", elr };
        occupied.push({ x: lx - estW / 2, y: ly - estH / 2, w: estW, h: estH });
        STATE.drawnNwrElrLabels.push(box);

        ctx.save();
        ctx.translate(lx - estW / 2, ly);
        if (elrAngle) ctx.rotate((elrAngle * Math.PI) / 180);
        ctx.textBaseline = "middle";
        ctx.fillStyle = "rgba(15, 23, 42, 0.88)";
        ctx.fillRect(0, -estH / 2, estW, estH);
        ctx.strokeStyle = "rgba(168, 85, 247, 0.55)";
        ctx.lineWidth = 1;
        ctx.strokeRect(0, -estH / 2, estW, estH);
        ctx.fillStyle = "#e9d5ff";
        ctx.font = `700 ${fontSize}px ui-monospace, monospace`;
        ctx.fillText(lbl, 5, 0);
        ctx.restore();
      }
    }
  }

  // 6. Highlight & Hover Preview Overlays (drawn on top of all layers and labels)
  drawHighlightAndPreviewOverlays(ctx, "nwr");
  if (STATE.crossHighlight.nwr && STATE.crossHighlight.nwr.active) {
    drawCrossHighlightPulse(ctx, STATE.crossHighlight.nwr, worldToScreen, "nwr");
  }

  document.getElementById("hud-zoom").textContent = `${(zoom * 1000).toFixed(2)} px/km (${
    useDetailedCentrelines ? "NWR Detailed" : "NWR Macro"
  })`;
}
