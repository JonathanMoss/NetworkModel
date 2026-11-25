/**
 * @fileoverview HTML5 Canvas renderer for the TPS Signalling & Timetable Schematic.
 */

/* ============================================================================
   VIEW 1: TPS SCHEMATIC RENDER LOOP
   ============================================================================ */
function renderSchematic() {
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  ctx.clearRect(0, 0, w, h);

  STATE.drawnStationLabels = [];
  STATE.drawnSignalLabels = [];
  STATE.drawnCadLabels = [];
  STATE.drawnBlockLabels = [];
  STATE.drawnNodeLabels = [];
  STATE.drawnAnnotationLabels = [];

  if (!STATE.overview) return;

  const bbox = getViewportBBox(0.05);
  const zoom = STATE.zoom;
  const useMicro = zoom >= 0.16 && STATE.viewportData.edges.length > 0;

  // 1. Poly Areas
  if (STATE.layers.polys && STATE.overview.poly_areas) {
    const polyWidthMult = ((STATE.layerStyles && STATE.layerStyles.polys && STATE.layerStyles.polys.width) || 1.5) / 1.5;
    const polyCol = (STATE.layerStyles && STATE.layerStyles.polys && STATE.layerStyles.polys.color) || "#a855f7";
    ctx.fillStyle = hexToRgba(polyCol, 0.06);
    ctx.strokeStyle = hexToRgba(polyCol, 0.35);
    ctx.lineWidth = Math.max(0.5, 1.0 * polyWidthMult);
    for (const pa of STATE.overview.poly_areas) {
      if (
        pa.max_x < bbox.min_x ||
        pa.min_x > bbox.max_x ||
        pa.max_y < bbox.min_y ||
        pa.min_y > bbox.max_y
      )
        continue;
      const corners = pa.corners;
      if (!corners || corners.length < 3) continue;
      ctx.beginPath();
      const [sx0, sy0] = worldToScreen(corners[0][0], corners[0][1]);
      ctx.moveTo(sx0, sy0);
      for (let i = 1; i < corners.length; i++) {
        const [sxi, syi] = worldToScreen(corners[i][0], corners[i][1]);
        ctx.lineTo(sxi, syi);
      }
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    }
  }

  // 2. CAD Graphic Vectors (when zoomed in)
  if (STATE.layers.vectors && useMicro && STATE.viewportData.vectors.length) {
    const vecWidthMult = ((STATE.layerStyles && STATE.layerStyles.vectors && STATE.layerStyles.vectors.width) || 1.0) / 1.0;
    const vecCol = (STATE.layerStyles && STATE.layerStyles.vectors && STATE.layerStyles.vectors.color) || "#64748b";
    ctx.strokeStyle = hexToRgba(vecCol, 0.55);
    ctx.lineWidth = Math.max(0.5, Math.max(0.8, Math.min(2.0, zoom * 0.45)) * vecWidthMult);
    ctx.beginPath();
    for (const v of STATE.viewportData.vectors) {
      const [x0, y0] = worldToScreen(v[1], v[2]);
      const [x1, y1] = worldToScreen(v[3], v[4]);
      ctx.moveTo(x0, y0);
      ctx.lineTo(x1, y1);
    }
    ctx.stroke();
  }

  // 3. Track Edges
  if (STATE.layers.edges) {
    const edgeWidthMult = ((STATE.layerStyles && STATE.layerStyles.edges && STATE.layerStyles.edges.width) || 1.5) / 1.5;
    const isUniform = STATE.colorMode === "uniform";
    const uniformEdgeCol = (STATE.layerStyles && STATE.layerStyles.edges && STATE.layerStyles.edges.color) || "#38bdf8";
    if (!useMicro) {
      const batches = new Map();
      for (const e of STATE.overview.macro_edges) {
        const [x0, y0, x1, y1, vmax, elec, grad] = e;
        if (
          Math.max(x0, x1) < bbox.min_x ||
          Math.min(x0, x1) > bbox.max_x ||
          Math.max(y0, y1) < bbox.min_y ||
          Math.min(y0, y1) > bbox.max_y
        )
          continue;
        const col = isUniform ? uniformEdgeCol : getEdgeColor(vmax, elec === 1, grad);
        let arr = batches.get(col);
        if (!arr) {
          arr = [];
          batches.set(col, arr);
        }
        arr.push(x0, y0, x1, y1);
      }
      ctx.lineWidth = Math.max(0.75, Math.max(1.1, Math.min(2.4, zoom * 18)) * edgeWidthMult);
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
      for (const e of STATE.viewportData.edges) {
        const x0 = e[4],
          y0 = e[5],
          x1 = e[6],
          y1 = e[7];
        if (x0 === null || x1 === null) continue;
        const col = isUniform ? uniformEdgeCol : getEdgeColor(e[8], e[9] === 1, e[10], e[12] || 0);
        let arr = batches.get(col);
        if (!arr) {
          arr = [];
          batches.set(col, arr);
        }
        arr.push(x0, y0, x1, y1);
      }
      ctx.lineWidth = Math.max(0.9, Math.max(1.4, Math.min(3.5, zoom * 0.9)) * edgeWidthMult);
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
    }
  }

  // 4. Station Platform / Track Ways
  if (STATE.layers.tracks && useMicro && STATE.viewportData.tracks.length) {
    const trackWidthMult = ((STATE.layerStyles && STATE.layerStyles.tracks && STATE.layerStyles.tracks.width) || 3.0) / 3.0;
    const trackCol = (STATE.layerStyles && STATE.layerStyles.tracks && STATE.layerStyles.tracks.color) || "#818cf8";
    ctx.strokeStyle = hexToRgba(trackCol, 0.65);
    ctx.lineWidth = Math.max(1.0, Math.max(2.5, Math.min(6.0, zoom * 1.6)) * trackWidthMult);
    ctx.beginPath();
    for (const t of STATE.viewportData.tracks) {
      const coords = t[7];
      if (!coords || coords.length < 2) continue;
      const [sx0, sy0] = worldToScreen(coords[0][0], coords[0][1]);
      ctx.moveTo(sx0, sy0);
      for (let i = 1; i < coords.length; i++) {
        const [sxi, syi] = worldToScreen(coords[i][0], coords[i][1]);
        ctx.lineTo(sxi, syi);
      }
    }
    ctx.stroke();
  }

  // 5. CAD Graphic Texts
  if (
    STATE.layers.texts &&
    useMicro &&
    zoom >= 0.45 &&
    STATE.viewportData.texts.length
  ) {
    const scale = getLayerLabelScale("texts");
    const baseFontSize = Math.max(8, Math.min(13, zoom * 4.2));
    const fontSize = Math.round(baseFontSize * scale);
    ctx.font = `${fontSize}px ui-monospace, SFMono-Regular, monospace`;
    ctx.fillStyle = "#cbd5e1";
    for (const gt of STATE.viewportData.texts) {
      // gt: [pk, x, y, layer_type, text, angle]
      const [sx, sy] = worldToScreen(gt[1], gt[2]);
      if (sx < -80 || sx > w + 80 || sy < -80 || sy > h + 80) continue;
      const textStr = gt[4] || "";
      const angle = getLayerLabelAngle("texts", gt[5] || 0);
      const tw = textStr.length * (fontSize * 0.6);
      const th = fontSize + 4;

      ctx.save();
      ctx.translate(sx, sy);
      if (angle) {
        ctx.rotate((angle * Math.PI) / 180);
      }
      ctx.textBaseline = "middle";
      ctx.fillText(textStr, 0, 0);
      ctx.restore();

      STATE.drawnCadLabels.push({
        x: sx,
        y: sy,
        w: tw,
        h: th,
        angle,
        alignY: "center",
        gt,
      });
    }
  }

  // 6. Microscopic Track Nodes
  if ((STATE.layers.nodes || zoom >= 2.2) && useMicro && STATE.viewportData.nodes.length) {
    ctx.fillStyle = "#10b981";
    const r = Math.max(1.8, Math.min(4.0, zoom * 0.8));
    ctx.beginPath();
    for (const n of STATE.viewportData.nodes) {
      const [sx, sy] = worldToScreen(n[1], n[2]);
      if (sx < 0 || sx > w || sy < 0 || sy > h) continue;
      ctx.moveTo(sx + r, sy);
      ctx.arc(sx, sy, r, 0, Math.PI * 2);
    }
    ctx.fill();

    // Node ID text labels when nodes layer is explicitly active
    if (STATE.layers.nodes && zoom >= 1.2) {
      const scale = getLayerLabelScale("nodes");
      const angle = getLayerLabelAngle("nodes");
      const fontSize = Math.round(8.5 * scale);
      ctx.font = `600 ${fontSize}px ui-monospace, monospace`;
      for (const n of STATE.viewportData.nodes) {
        const [sx, sy] = worldToScreen(n[1], n[2]);
        if (sx < -20 || sx > w + 20 || sy < -20 || sy > h + 20) continue;
        const nText = `#${n[0]}`;
        const nw = nText.length * (fontSize * 0.6);
        const nh = fontSize + 2;
        ctx.save();
        ctx.translate(sx + 5, sy);
        if (angle) ctx.rotate((angle * Math.PI) / 180);
        ctx.textBaseline = "middle";
        ctx.fillStyle = "#34d399";
        ctx.fillText(nText, 0, 0);
        ctx.restore();
        STATE.drawnNodeLabels.push({
          x: sx + 5,
          y: sy,
          w: nw,
          h: nh,
          angle,
          alignY: "center",
          node: n,
        });
      }
    }
  }

  // 6b. Signalling Block Sections Overlay & Section Labels
  if (
    (STATE.layers.blocks || (STATE.colorMode === "signalling" && zoom >= 0.55)) &&
    useMicro &&
    STATE.viewportData.blocks &&
    STATE.viewportData.blocks.length
  ) {
    const blockWidthMult = ((STATE.layerStyles && STATE.layerStyles.blocks && STATE.layerStyles.blocks.width) || 2.5) / 2.5;
    const blockCol = (STATE.layerStyles && STATE.layerStyles.blocks && STATE.layerStyles.blocks.color) || "#10b981";
    ctx.save();
    ctx.lineWidth = Math.max(1.0, Math.max(2.0, Math.min(4.2, zoom * 1.1)) * blockWidthMult);
    for (const b of STATE.viewportData.blocks) {
      // [pk, block_id, pre_signal_distance, sight_distance, shunting_flag, atc_flag, moving_block, coords]
      const coords = b[7];
      if (!coords || coords.length < 2) continue;
      const isShunt = b[4] === 1;
      ctx.strokeStyle = isShunt
        ? "rgba(168, 85, 247, 0.45)"
        : hexToRgba(blockCol, 0.45);
      ctx.setLineDash(isShunt ? [4, 3] : []);
      ctx.beginPath();
      const [sx0, sy0] = worldToScreen(coords[0][0], coords[0][1]);
      ctx.moveTo(sx0, sy0);
      for (let i = 1; i < coords.length; i++) {
        const [sxi, syi] = worldToScreen(coords[i][0], coords[i][1]);
        ctx.lineTo(sxi, syi);
      }
      ctx.stroke();

      // Block midpoint tag label
      if (STATE.layers.blocks && zoom >= 0.5) {
        const scale = getLayerLabelScale("blocks");
        const angle = getLayerLabelAngle("blocks");
        const midIdx = Math.floor(coords.length / 2);
        const [bx, by] = worldToScreen(coords[midIdx][0], coords[midIdx][1]);
        if (bx >= -50 && bx <= w + 50 && by >= -50 && by <= h + 50) {
          const bText = `B#${b[1]}`;
          const fontSize = Math.round(9 * scale);
          const bw = bText.length * (fontSize * 0.62) + 6;
          const bh = fontSize + 4;
          ctx.save();
          ctx.translate(bx, by);
          if (angle) ctx.rotate((angle * Math.PI) / 180);
          ctx.textBaseline = "middle";
          ctx.fillStyle = "rgba(15, 23, 42, 0.9)";
          ctx.fillRect(-2, -bh / 2, bw, bh);
          ctx.strokeStyle = isShunt ? "rgba(168, 85, 247, 0.75)" : "rgba(16, 185, 129, 0.75)";
          ctx.lineWidth = 1;
          ctx.strokeRect(-2, -bh / 2, bw, bh);
          ctx.fillStyle = isShunt ? "#c084fc" : "#6ee7b7";
          ctx.font = `700 ${fontSize}px ui-monospace, monospace`;
          ctx.fillText(bText, 1, 0);
          ctx.restore();
          STATE.drawnBlockLabels.push({
            x: bx,
            y: by,
            w: bw,
            h: bh,
            angle,
            alignY: "center",
            block: b,
          });
        }
      }
    }
    ctx.restore();
  }

  // 7. Signals & Buffer Stops
  if (STATE.layers.signals && useMicro && zoom >= 0.35 && STATE.viewportData.signals.length) {
    const sr = Math.max(2.4, Math.min(5.6, zoom * 1.2));
    const sigMode = STATE.colorMode === "signalling";
    for (const s of STATE.viewportData.signals) {
      // [pk, id, signal_id, name, interlocking_sys_id, x, y, x1, y1, bumper, start_node_id, end_node_id, overlap_count]
      if (s[5] === null) continue;
      const [sx, sy] = worldToScreen(s[5], s[6]);
      if (sx < -15 || sx > w + 15 || sy < -15 || sy > h + 15) continue;

      const ilockInfo = getSignalInterlockingInfo(s[4], s[9]);
      const hasOverlaps = (s[12] || 0) > 0;

      // Directional approach vector if start_node (x1, y1) != end_node (x, y)
      if (zoom >= 0.85 && s[7] !== null && (s[7] !== s[5] || s[8] !== s[6])) {
        const [sx1, sy1] = worldToScreen(s[7], s[8]);
        const dx = sx - sx1;
        const dy = sy - sy1;
        const len = Math.hypot(dx, dy);
        if (len > 1) {
          const ux = dx / len;
          const uy = dy / len;
          // Draw short directional approach stem behind signal head
          ctx.strokeStyle = sigMode ? ilockInfo.color : "#fde047";
          ctx.lineWidth = 1.6;
          ctx.beginPath();
          ctx.moveTo(sx - ux * (sr + 5), sy - uy * (sr + 5));
          ctx.lineTo(sx, sy);
          ctx.stroke();
        }
      }

      // Draw overlap halo ring when signal has <securitysection> overlaps
      if (hasOverlaps && (sigMode || zoom >= 1.1)) {
        ctx.strokeStyle = "rgba(217, 70, 239, 0.75)";
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.arc(sx, sy, sr + 2.4, 0, Math.PI * 2);
        ctx.stroke();
      }

      if (s[9] === 1) {
        ctx.fillStyle = "#ef4444";
        ctx.fillRect(sx - sr, sy - sr, sr * 2, sr * 2);
      } else {
        ctx.fillStyle = sigMode ? ilockInfo.color : "#f59e0b";
        ctx.beginPath();
        ctx.arc(sx, sy, sr, 0, Math.PI * 2);
        ctx.fill();
      }

      const sigScale = getLayerLabelScale("signals");
      const sigAngle = getLayerLabelAngle("signals");
      const canShowSigLbl = (zoom >= 1.45 || (zoom >= 0.75 && sigScale >= 1.3)) && s[3];
      if (canShowSigLbl) {
        const fontSize = Math.round(9 * sigScale);
        const tagText = sigMode ? `${s[3]} [${ilockInfo.short}]` : s[3];
        const lw = tagText.length * (fontSize * 0.6) + 4;
        const lh = fontSize + 4;
        const lx = sx + sr + 4;
        const ly = sy;

        ctx.save();
        ctx.translate(lx, ly);
        if (sigAngle) ctx.rotate((sigAngle * Math.PI) / 180);
        ctx.textBaseline = "middle";
        ctx.fillStyle = "rgba(7, 11, 20, 0.88)";
        ctx.fillRect(-2, -lh / 2, lw + 4, lh);
        ctx.fillStyle = sigMode ? "#f8fafc" : "#fde68a";
        ctx.font = `600 ${fontSize}px ui-monospace, monospace`;
        ctx.fillText(tagText, 0, 0);
        ctx.restore();

        STATE.drawnSignalLabels.push({
          x: lx,
          y: ly,
          w: lw,
          h: lh,
          angle: sigAngle,
          alignY: "center",
          sig: s,
        });
      }
    }
  }

  // 8. Stations & Timing Points
  if (STATE.layers.stations && STATE.overview.stations) {
    const occupiedBoxes = [];
    const timingMode = STATE.colorMode === "timing";
    const stScale = getLayerLabelScale("stations");
    const stAngle = getLayerLabelAngle("stations");

    for (const st of STATE.overview.stations) {
      if (
        st.x < bbox.min_x ||
        st.x > bbox.max_x ||
        st.y < bbox.min_y ||
        st.y > bbox.max_y
      )
        continue;

      const isMandatory = st.type === 2;
      const isMajor = st.cat === 1 || (st.crs && st.active_tracks >= 4) || (timingMode && isMandatory && st.cat <= 2);
      const isPassenger = Boolean(st.crs) || (timingMode && isMandatory);
      if (zoom < 0.035 && !isMajor) continue;
      if (zoom < 0.08 && !isPassenger && st.cat > 2) continue;

      const [sx, sy] = worldToScreen(st.x, st.y);
      const r = isMajor ? 4.3 : isPassenger ? 3.2 : 2.3;

      let stColor = isMajor ? "#38bdf8" : isPassenger ? "#f8fafc" : "#94a3b8";
      if (timingMode) {
        if (st.type === 2) {
          stColor = "#f43f5e"; // Mandatory TIPLOC
        } else if (st.type === 4 || st.type === 6 || st.type === 9) {
          stColor = "#38bdf8"; // Optional Stop / Passenger
        } else if (st.type === 5 || st.type === 10 || st.type === 12) {
          stColor = "#f59e0b"; // Optional Freight
        } else if (st.type === 7 || st.type === 8 || st.type === 11) {
          stColor = "#a855f7"; // Optional Crossing
        } else {
          stColor = "#64748b";
        }
        if (isMandatory && zoom >= 0.06) {
          ctx.strokeStyle = "rgba(244, 63, 94, 0.7)";
          ctx.lineWidth = 1.4;
          ctx.beginPath();
          ctx.arc(sx, sy, r + 2.3, 0, Math.PI * 2);
          ctx.stroke();
        }
      }

      ctx.fillStyle = stColor;
      ctx.beginPath();
      ctx.arc(sx, sy, r, 0, Math.PI * 2);
      ctx.fill();

      const showLabel =
        (isMajor && zoom >= 0.015) ||
        (isPassenger && zoom >= 0.06) ||
        zoom >= 0.22;
      if (showLabel) {
        const roleSuffix = timingMode && isMandatory ? " [MAND]" : "";
        const lbl =
          zoom >= 0.35
            ? `${st.name} (${st.crs || st.abbrev})${roleSuffix}`
            : st.crs
            ? `${st.name} [${st.crs}]${roleSuffix}`
            : `${st.name}${roleSuffix}`;
        const baseSize = isMajor ? 11 : 10;
        const fontSize = Math.round(baseSize * stScale);
        const estW = lbl.length * (fontSize * 0.62) + 6;
        const estH = fontSize + 6;
        const lx = sx + r + 4;
        const ly = sy;

        let collides = false;
        if (stAngle === 0) {
          for (const b of occupiedBoxes) {
            if (
              lx < b.x + b.w &&
              lx + estW > b.x &&
              ly - estH / 2 < b.y + b.h &&
              ly + estH / 2 > b.y
            ) {
              collides = true;
              break;
            }
          }
        }
        if (!collides) {
          const box = { x: lx, y: ly, w: estW, h: estH, angle: stAngle, alignY: "center", st };
          occupiedBoxes.push({ x: lx - 2, y: ly - estH / 2, w: estW + 4, h: estH });
          STATE.drawnStationLabels.push(box);

          ctx.save();
          ctx.translate(lx, ly);
          if (stAngle) ctx.rotate((stAngle * Math.PI) / 180);
          ctx.textBaseline = "middle";
          ctx.fillStyle = "rgba(7, 11, 20, 0.88)";
          ctx.fillRect(-2, -estH / 2, estW + 4, estH);
          ctx.strokeStyle = "rgba(56, 189, 248, 0.35)";
          ctx.lineWidth = 1;
          ctx.strokeRect(-2, -estH / 2, estW + 4, estH);
          ctx.fillStyle = isMajor ? "#f8fafc" : "#cbd5e1";
          ctx.font = `${isMajor ? "700" : "500"} ${fontSize}px Inter, sans-serif`;
          ctx.fillText(lbl, 0, 0);
          ctx.restore();
        }
      }
    }
  }

  // 9. Operational Annotations
  if (STATE.layers.polys && STATE.overview.annotations) {
    const polyScale = getLayerLabelScale("polys");
    const polyAngle = getLayerLabelAngle("polys");
    for (const an of STATE.overview.annotations) {
      const [sx, sy] = worldToScreen(an.x, an.y);
      if (sx < -20 || sx > w + 20 || sy < -20 || sy > h + 20) continue;
      ctx.fillStyle = "#ec4899";
      ctx.beginPath();
      ctx.arc(sx, sy, 5, 0, Math.PI * 2);
      ctx.fill();

      if (zoom >= 0.35 && an.text) {
        const cleanText = an.text.replace(/<[^>]+>/g, " ").trim().slice(0, 45);
        const fontSize = Math.round(9 * polyScale);
        const pw = cleanText.length * (fontSize * 0.58) + 6;
        const ph = fontSize + 4;
        ctx.save();
        ctx.translate(sx + 7, sy);
        if (polyAngle) ctx.rotate((polyAngle * Math.PI) / 180);
        ctx.textBaseline = "middle";
        ctx.fillStyle = "rgba(15, 23, 42, 0.88)";
        ctx.fillRect(-2, -ph / 2, pw + 4, ph);
        ctx.fillStyle = "#f472b6";
        ctx.font = `500 ${fontSize}px Inter, sans-serif`;
        ctx.fillText(cleanText, 0, 0);
        ctx.restore();
        STATE.drawnAnnotationLabels.push({
          x: sx + 7,
          y: sy,
          w: pw,
          h: ph,
          angle: polyAngle,
          alignY: "center",
          an,
        });
      }
    }
  }

  // 10. Highlight & Hover Preview Overlays (drawn on top of all layers and labels)
  drawHighlightAndPreviewOverlays(ctx, "tps");
  if (STATE.crossHighlight.tps && STATE.crossHighlight.tps.active) {
    drawCrossHighlightPulse(ctx, STATE.crossHighlight.tps, (wx, wy) => worldToScreen(wx, wy, "tps"), "tps");
  }

  document.getElementById("hud-zoom").textContent = `${zoom.toFixed(3)}x (${
    useMicro ? "TPS Microscopic" : "TPS Macro"
  })`;
}

