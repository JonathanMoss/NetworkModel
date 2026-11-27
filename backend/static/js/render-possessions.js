/**
 * @fileoverview HTML5 Canvas overlay renderer for Engineers' Possessions
 * (PICOP / T3) and inner Worksites (ES zones) on both TPS and NWR views.
 */

/**
 * Draw all saved Engineers' Possessions and their inner Worksites onto the
 * active TPS Schematic or NWR Geographic Track Model canvas.
 *
 * @param {CanvasRenderingContext2D} targetCtx Target 2D canvas context.
 * @param {"tps"|"nwr"} targetView Coordinate space (`"tps"` or `"nwr"`).
 */
function drawPossessionsOverlay(targetCtx, targetView) {
  const isNwr = targetView === "nwr";
  const layerOn = isNwr ? STATE.nwrLayers?.possessions : STATE.layers?.possessions;
  if (!layerOn) return;
  const list = STATE.possessions || [];
  if (!list.length) return;

  const c = targetCtx.canvas || (isNwr ? nwrCanvas : canvas);
  const w = c.clientWidth;
  const h = c.clientHeight;
  const cam = isNwr ? STATE.nwrCam : STATE.tpsCam;
  if (!cam) return;

  const toScreen = (wx, wy) =>
    isNwr
      ? [(wx - cam.camX) * cam.zoom + w / 2, (cam.camY - wy) * cam.zoom + h / 2]
      : [(wx - cam.camX) * cam.zoom + w / 2, (wy - cam.camY) * cam.zoom + h / 2];

  targetCtx.save();

  for (const poss of list) {
    if (poss.status === "Cancelled") continue;
    const geom = isNwr ? poss.nwr_geometry : poss.tps_geometry;
    if (!geom) continue;
    const isActive = STATE.activePossessionId === poss.id;
    const baseCol = poss.color || "#f59e0b";
    const ways = geom.ways || [];
    const markers = geom.markers || [];

    // 1. Possession blocked-line band (glow halo + high-vis dashed core)
    for (const way of ways) {
      const pts = way.coords || [];
      if (pts.length < 2) continue;
      targetCtx.beginPath();
      const [x0, y0] = toScreen(pts[0][0], pts[0][1]);
      targetCtx.moveTo(x0, y0);
      for (let i = 1; i < pts.length; i++) {
        const [xi, yi] = toScreen(pts[i][0], pts[i][1]);
        targetCtx.lineTo(xi, yi);
      }

      // Outer hazard halo (tight on NWR Geo view so adjacent parallel tracks stay unobstructed)
      targetCtx.save();
      targetCtx.strokeStyle = baseCol;
      targetCtx.globalAlpha = isNwr ? (isActive ? 0.30 : 0.18) : (isActive ? 0.38 : 0.22);
      targetCtx.lineWidth = isNwr ? (isActive ? 5.2 : 3.8) : (isActive ? 14 : 10);
      targetCtx.lineCap = "round";
      targetCtx.lineJoin = "round";
      targetCtx.stroke();
      targetCtx.restore();

      // Core engineering dashed stripe
      targetCtx.strokeStyle = baseCol;
      targetCtx.lineWidth = isNwr ? (isActive ? 2.6 : 1.9) : (isActive ? 5.0 : 3.6);
      targetCtx.lineCap = "round";
      targetCtx.setLineDash(isNwr ? [7, 5] : [10, 6]);
      targetCtx.stroke();
      targetCtx.setLineDash([]);
    }

    // 2. Possession Limit Board (PLB) / Protecting Signal markers
    for (const m of markers) {
      if (m.x === null || m.x === undefined || m.y === null || m.y === undefined) continue;
      const [sx, sy] = toScreen(m.x, m.y);
      const isStart = m.role === "start";
      const markerCol = isStart ? "#ef4444" : baseCol;

      // Diamond limit board marker
      const sz = isNwr ? (isActive ? 6.5 : 5.2) : (isActive ? 8 : 6.5);
      targetCtx.beginPath();
      targetCtx.moveTo(sx, sy - sz);
      targetCtx.lineTo(sx + sz, sy);
      targetCtx.lineTo(sx, sy + sz);
      targetCtx.lineTo(sx - sz, sy);
      targetCtx.closePath();
      targetCtx.fillStyle = markerCol;
      targetCtx.fill();
      targetCtx.strokeStyle = "#ffffff";
      targetCtx.lineWidth = 1.6;
      targetCtx.stroke();

      // Floating PLB label badge (always shown for active possession, or zoomed in)
      const showLabel = isActive || (isNwr ? cam.zoom >= 0.04 : cam.zoom >= 0.35);
      if (showLabel && m.label) {
        const prefixParts = [];
        if (poss.won_ref) prefixParts.push(poss.won_ref);
        if (m.line_name) prefixParts.push(m.line_name);
        const tag = prefixParts.length ? `[${prefixParts.join(" • ")}] ${m.label}` : m.label;
        targetCtx.font = "700 10px Inter, sans-serif";
        const tw = targetCtx.measureText(tag).width;
        const bx = sx + 10;
        const by = sy - 10;
        targetCtx.fillStyle = "rgba(15, 23, 42, 0.92)";
        targetCtx.fillRect(bx - 4, by, tw + 8, 17);
        targetCtx.strokeStyle = markerCol;
        targetCtx.lineWidth = 1.2;
        targetCtx.strokeRect(bx - 4, by, tw + 8, 17);
        targetCtx.fillStyle = "#fef3c7";
        targetCtx.textBaseline = "middle";
        targetCtx.fillText(tag, bx, by + 8.5);
      }
    }

    // 3. Inner Worksites (ES Zones) in Cyan (#06b6d4)
    for (const ws of poss.worksites || []) {
      const wsGeom = isNwr ? ws.nwr_geometry : ws.tps_geometry;
      if (!wsGeom) continue;
      for (const way of wsGeom.ways || []) {
        const pts = way.coords || [];
        if (pts.length < 2) continue;
        targetCtx.beginPath();
        const [wx0, wy0] = toScreen(pts[0][0], pts[0][1]);
        targetCtx.moveTo(wx0, wy0);
        for (let i = 1; i < pts.length; i++) {
          const [wxi, wyi] = toScreen(pts[i][0], pts[i][1]);
          targetCtx.lineTo(wxi, wyi);
        }
        targetCtx.strokeStyle = "#06b6d4";
        targetCtx.lineWidth = isNwr ? (isActive ? 2.4 : 1.8) : (isActive ? 4.2 : 3.0);
        targetCtx.setLineDash(isNwr ? [4, 4] : [5, 5]);
        targetCtx.stroke();
        targetCtx.setLineDash([]);
      }

      for (const wm of wsGeom.markers || []) {
        if (wm.x === null || wm.x === undefined || wm.y === null || wm.y === undefined) continue;
        const [sx, sy] = toScreen(wm.x, wm.y);
        targetCtx.beginPath();
        targetCtx.arc(sx, sy, 5.2, 0, Math.PI * 2);
        targetCtx.fillStyle = "#06b6d4";
        targetCtx.fill();
        targetCtx.strokeStyle = "#ffffff";
        targetCtx.lineWidth = 1.4;
        targetCtx.stroke();

        if (isActive && wm.label) {
          const lbl = `${ws.title}: ${wm.label}`;
          targetCtx.font = "600 9.5px Inter, sans-serif";
          const tw = targetCtx.measureText(lbl).width;
          targetCtx.fillStyle = "rgba(15, 23, 42, 0.9)";
          targetCtx.fillRect(sx + 8, sy + 4, tw + 8, 15);
          targetCtx.strokeStyle = "#06b6d4";
          targetCtx.lineWidth = 1;
          targetCtx.strokeRect(sx + 8, sy + 4, tw + 8, 15);
          targetCtx.fillStyle = "#cffafe";
          targetCtx.textBaseline = "middle";
          targetCtx.fillText(lbl, sx + 12, sy + 11.5);
        }
      }
    }
  }

  targetCtx.restore();
}

