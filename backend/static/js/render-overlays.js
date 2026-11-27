/**
 * @fileoverview Render loop scheduler, animated cross-highlight pulse ring,
 * and topology/hover preview overlay drawing.
 */

let renderPending = false;
function requestRender() {
  if (!renderPending) {
    renderPending = true;
    requestAnimationFrame(() => {
      renderPending = false;
      if (STATE.activeView === "split") {
        renderSchematic();
        renderNwrModel();
      } else if (STATE.activeView === "nwr") {
        renderNwrModel();
      } else {
        renderSchematic();
      }
    });
  }
}

let pulseLoopRunning = false;
function triggerPulseLoop() {
  if (pulseLoopRunning) return;
  pulseLoopRunning = true;
  function step(now) {
    let continueAnimation = false;
    if (STATE.crossHighlight.tps && STATE.crossHighlight.tps.active) {
      const elapsed = now - STATE.crossHighlight.tps.pulseStart;
      if (elapsed < STATE.crossHighlight.tps.pulseDuration) {
        continueAnimation = true;
      }
    }
    if (STATE.crossHighlight.nwr && STATE.crossHighlight.nwr.active) {
      const elapsed = now - STATE.crossHighlight.nwr.pulseStart;
      if (elapsed < STATE.crossHighlight.nwr.pulseDuration) {
        continueAnimation = true;
      }
    }
    requestRender();
    if (continueAnimation) {
      requestAnimationFrame(step);
    } else {
      pulseLoopRunning = false;
    }
  }
  requestAnimationFrame(step);
}

function drawCrossHighlightPulse(targetCtx, highlight, toScreen, mode) {
  if (!highlight || !highlight.active) return;
  const now = performance.now();
  const elapsed = now - (highlight.pulseStart || now);
  const isPulsing = elapsed < (highlight.pulseDuration || 4500);

  const pulsePhase = (elapsed % 1200) / 1200;
  const glowAlpha = isPulsing ? 0.35 + 0.35 * Math.sin(pulsePhase * Math.PI * 2) : 0.35;
  const glowCol = mode === "nwr" ? "rgba(6, 182, 212, " : "rgba(244, 63, 94, ";

  targetCtx.save();

  // 1. Draw glowing corridors / ways
  for (const way of highlight.ways || []) {
    if (!way.coords || way.coords.length < 2) continue;
    const pts = way.coords.map((pt) => toScreen(pt[0], pt[1]));

    targetCtx.beginPath();
    targetCtx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) {
      targetCtx.lineTo(pts[i][0], pts[i][1]);
    }
    targetCtx.strokeStyle = `${glowCol}${glowAlpha})`;
    targetCtx.lineWidth = mode === "nwr" ? 14 : 12;
    targetCtx.lineCap = "round";
    targetCtx.lineJoin = "round";
    targetCtx.stroke();

    targetCtx.strokeStyle = `${glowCol}${Math.min(1, glowAlpha + 0.3)})`;
    targetCtx.lineWidth = mode === "nwr" ? 7 : 6;
    targetCtx.stroke();

    targetCtx.strokeStyle = mode === "nwr" ? "#22d3ee" : "#fda4af";
    targetCtx.lineWidth = 3;
    if (isPulsing) {
      targetCtx.setLineDash([8, 6]);
      targetCtx.lineDashOffset = -(elapsed * 0.04) % 14;
    }
    targetCtx.stroke();
    targetCtx.setLineDash([]);

    const midIdx = Math.floor(pts.length / 2);
    const midPt = pts[midIdx];
    if (isPulsing && midPt) {
      const rippleR = 6 + pulsePhase * 28;
      const rippleAlpha = Math.max(0, 1 - pulsePhase);
      targetCtx.beginPath();
      targetCtx.arc(midPt[0], midPt[1], rippleR, 0, Math.PI * 2);
      targetCtx.strokeStyle = `${glowCol}${rippleAlpha})`;
      targetCtx.lineWidth = 2.5;
      targetCtx.stroke();

      const phase2 = (pulsePhase + 0.5) % 1.0;
      const rippleR2 = 6 + phase2 * 28;
      const rippleAlpha2 = Math.max(0, 1 - phase2);
      targetCtx.beginPath();
      targetCtx.arc(midPt[0], midPt[1], rippleR2, 0, Math.PI * 2);
      targetCtx.strokeStyle = `${glowCol}${rippleAlpha2})`;
      targetCtx.lineWidth = 1.8;
      targetCtx.stroke();
    }
  }

  // 2. Draw points (signals, nodes, stations)
  for (const pt of highlight.points || []) {
    const [sx, sy] = toScreen(pt.x, pt.y);
    const ptCol = pt.color || (mode === "nwr" ? "#22d3ee" : "#f59e0b");
    const isDiamond = pt.shape === "diamond";
    const isSignal = pt.shape === "signal";

    if (isPulsing) {
      const rippleR = 8 + pulsePhase * 32;
      const rippleAlpha = Math.max(0, 1 - pulsePhase);
      const isEmerald = ptCol === "#10b981";
      targetCtx.beginPath();
      targetCtx.arc(sx, sy, rippleR, 0, Math.PI * 2);
      targetCtx.strokeStyle = isEmerald
        ? `rgba(16, 185, 129, ${rippleAlpha})`
        : isDiamond
        ? `rgba(168, 85, 247, ${rippleAlpha})`
        : `rgba(245, 158, 11, ${rippleAlpha})`;
      targetCtx.lineWidth = 2.5;
      targetCtx.stroke();

      const phase2 = (pulsePhase + 0.5) % 1.0;
      const rippleR2 = 8 + phase2 * 32;
      const rippleAlpha2 = Math.max(0, 1 - phase2);
      targetCtx.beginPath();
      targetCtx.arc(sx, sy, rippleR2, 0, Math.PI * 2);
      targetCtx.strokeStyle = isEmerald
        ? `rgba(52, 211, 153, ${rippleAlpha2 * 0.85})`
        : isDiamond
        ? `rgba(217, 70, 239, ${rippleAlpha2 * 0.8})`
        : `rgba(236, 72, 153, ${rippleAlpha2 * 0.8})`;
      targetCtx.lineWidth = 1.8;
      targetCtx.stroke();
    }

    if (isDiamond) {
      const dSize = 8;
      targetCtx.beginPath();
      targetCtx.moveTo(sx, sy - dSize);
      targetCtx.lineTo(sx + dSize, sy);
      targetCtx.lineTo(sx, sy + dSize);
      targetCtx.lineTo(sx - dSize, sy);
      targetCtx.closePath();
      targetCtx.fillStyle = ptCol;
      targetCtx.shadowColor = ptCol;
      targetCtx.shadowBlur = 14;
      targetCtx.fill();
      targetCtx.shadowBlur = 0;
      targetCtx.strokeStyle = "#ffffff";
      targetCtx.lineWidth = 2;
      targetCtx.stroke();
    } else {
      const radius = isSignal ? 7.5 : 7;
      targetCtx.beginPath();
      targetCtx.arc(sx, sy, radius, 0, Math.PI * 2);
      targetCtx.fillStyle = ptCol;
      targetCtx.shadowColor = ptCol;
      targetCtx.shadowBlur = isSignal ? 14 : 10;
      targetCtx.fill();
      targetCtx.shadowBlur = 0;
      targetCtx.strokeStyle = "#ffffff";
      targetCtx.lineWidth = 2;
      targetCtx.stroke();
    }

    if (pt.label) {
      targetCtx.font = "600 11px Inter, sans-serif";
      const tm = targetCtx.measureText(pt.label);
      const tw = tm.width;
      const th = 20;
      const bx = sx + 13;
      const by = sy - th / 2;
      targetCtx.fillStyle = "rgba(15, 23, 42, 0.92)";
      targetCtx.fillRect(bx - 5, by, tw + 10, th);
      targetCtx.strokeStyle = ptCol;
      targetCtx.lineWidth = 1.2;
      targetCtx.strokeRect(bx - 5, by, tw + 10, th);
      targetCtx.fillStyle = "#ffffff";
      targetCtx.textBaseline = "middle";
      targetCtx.fillText(pt.label, bx, sy);
    }
  }

  targetCtx.restore();
}

/* Colour & Signalling Interlocking helpers for TPS & NWR */
const INTERLOCKING_STYLES = {
  1: { color: "#ef4444", label: "Stop Board", short: "STOP", aspects: 0 },
  2: { color: "#ef4444", label: "Buffer Stop", short: "BUF", aspects: 0 },
  3: { color: "#f97316", label: "Speed Board", short: "SPD", aspects: 0 },
  4: { color: "#eab308", label: "2-Aspect Colour Light (IECC)", short: "2A IECC", aspects: 2 },
  5: { color: "#eab308", label: "2-Aspect Colour Light (Panel)", short: "2A PNL", aspects: 2 },
  6: { color: "#38bdf8", label: "3-Aspect Colour Light (IECC)", short: "3A IECC", aspects: 3 },
  7: { color: "#38bdf8", label: "3-Aspect Colour Light (Panel)", short: "3A PNL", aspects: 3 },
  8: { color: "#10b981", label: "4-Aspect Colour Light (IECC)", short: "4A IECC", aspects: 4 },
  9: { color: "#10b981", label: "4-Aspect Colour Light (Panel)", short: "4A PNL", aspects: 4 },
  10: { color: "#a855f7", label: "Ground Position Light (Shunt)", short: "GPL", aspects: 0 },
  11: { color: "#f97316", label: "Semaphore Shunting Signal", short: "SEM-SH", aspects: 0 },
  12: { color: "#f97316", label: "Home/Starter Semaphore", short: "SEM", aspects: 0 },
  13: { color: "#ec4899", label: "Phone for Authorisation", short: "PHONE", aspects: 0 },
  14: { color: "#a855f7", label: "Limit of Shunt", short: "LOS", aspects: 0 },
  15: { color: "#a855f7", label: "Switch Diamond", short: "SW-D", aspects: 0 },
  16: { color: "#a855f7", label: "Points", short: "PTS", aspects: 0 },
};



function getHoverHighlightColor(type) {
  switch (type) {
    case "block":
      return "#10b981";
    case "route":
      return "#f97316";
    case "signal":
    case "nwr_waymark":
      return "#fbbf24";
    case "attributesection":
    case "nwr_elr":
      return "#c084fc";
    case "track":
      return "#818cf8";
    case "nf_curvature":
      return "#f43f5e";
    case "nf_point":
      return "#06b6d4";
    default:
      return "#22d3ee";
  }
}

function drawHighlightAndPreviewOverlays(targetCtx = ctx, targetView = null) {
  const ctx = targetCtx;
  const c = targetCtx.canvas || canvas;
  const w = c.clientWidth;
  const h = c.clientHeight;
  const isNwrTarget = targetView === "nwr" || (targetView === null && STATE.activeView === "nwr");
  const worldToScreen = (wx, wy) => {
    if (isNwrTarget) {
      return [
        (wx - STATE.nwrCam.camX) * STATE.nwrCam.zoom + w / 2,
        (STATE.nwrCam.camY - wy) * STATE.nwrCam.zoom + h / 2,
      ];
    }
    return [
      (wx - STATE.tpsCam.camX) * STATE.tpsCam.zoom + w / 2,
      (wy - STATE.tpsCam.camY) * STATE.tpsCam.zoom + h / 2,
    ];
  };

  if (
    STATE.highlight.ways.length ||
    STATE.highlight.points.length ||
    (STATE.highlight.segments && STATE.highlight.segments.length)
  ) {
    ctx.save();
    const grpFilter = STATE.highlightGroupFilter || "all";

    if (grpFilter !== "minimal" && STATE.highlight.segments && STATE.highlight.segments.length) {
      ctx.strokeStyle = "#f43f5e";
      ctx.lineWidth = 4.5;
      ctx.shadowColor = "#f43f5e";
      ctx.shadowBlur = 10;
      ctx.beginPath();
      for (const seg of STATE.highlight.segments) {
        const [sx0, sy0] = worldToScreen(seg[0], seg[1]);
        const [sx1, sy1] = worldToScreen(seg[2], seg[3]);
        ctx.moveTo(sx0, sy0);
        ctx.lineTo(sx1, sy1);
      }
      ctx.stroke();
    }

    for (const way of STATE.highlight.ways) {
      if (grpFilter === "minimal") break;
      if (!way.coords || way.coords.length < 2) continue;
      if (grpFilter !== "all") {
        if (grpFilter === "all_overlaps" || grpFilter === "overlaps") {
          if (!way.group || !way.group.startsWith("overlap")) continue;
        } else if (grpFilter.startsWith("approach:")) {
          const targetBid = grpFilter.slice(9);
          if (way.group !== "approach" || (way.block_id !== undefined && String(way.block_id) !== targetBid)) continue;
        } else if (grpFilter.startsWith("outgoing:")) {
          const targetBid = grpFilter.slice(9);
          if (way.group !== "outgoing" || (way.block_id !== undefined && String(way.block_id) !== targetBid)) continue;
        } else if (way.group && way.group !== grpFilter) {
          continue;
        }
      }

      const isIsolated = grpFilter.includes(":") || grpFilter.startsWith("overlap_");
      ctx.strokeStyle = way.color || "#38bdf8";
      ctx.lineWidth = isIsolated ? (way.width ? way.width + 1.2 : 5.8) : (way.width || 4.5);
      ctx.shadowColor = way.color || "#38bdf8";
      ctx.shadowBlur = isIsolated ? 16 : 12;
      ctx.setLineDash(way.dash || []);
      ctx.beginPath();
      const [sx0, sy0] = worldToScreen(way.coords[0][0], way.coords[0][1]);
      ctx.moveTo(sx0, sy0);
      for (let i = 1; i < way.coords.length; i++) {
        const [sxi, syi] = worldToScreen(way.coords[i][0], way.coords[i][1]);
        ctx.lineTo(sxi, syi);
      }
      ctx.stroke();

      // Draw directional chevron at midpoint of highlighted block/overlap way
      ctx.setLineDash([]);
      const midIdx = Math.floor((way.coords.length - 1) / 2);
      const [mx0, my0] = worldToScreen(way.coords[midIdx][0], way.coords[midIdx][1]);
      const [mx1, my1] = worldToScreen(way.coords[midIdx + 1][0], way.coords[midIdx + 1][1]);
      const dx = mx1 - mx0;
      const dy = my1 - my0;
      const dlen = Math.hypot(dx, dy);
      if (dlen > 18) {
        const cx = (mx0 + mx1) / 2;
        const cy = (my0 + my1) / 2;
        const ang = Math.atan2(dy, dx);
        ctx.fillStyle = way.color || "#38bdf8";
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(ang) * 7, cy + Math.sin(ang) * 7);
        ctx.lineTo(cx + Math.cos(ang + 2.5) * 6, cy + Math.sin(ang + 2.5) * 6);
        ctx.lineTo(cx + Math.cos(ang - 2.5) * 6, cy + Math.sin(ang - 2.5) * 6);
        ctx.closePath();
        ctx.fill();
      }
    }

    ctx.setLineDash([]);
    for (const pt of STATE.highlight.points) {
      if (grpFilter === "minimal") {
        if (pt.group !== "signal" && pt.group !== "points") continue;
      } else if (
        grpFilter !== "all" &&
        pt.group &&
        pt.group !== "signal" &&
        pt.group !== "points"
      ) {
        if (grpFilter === "all_overlaps" || grpFilter === "overlaps") {
          if (!pt.group || !pt.group.startsWith("overlap")) continue;
        } else if (grpFilter.startsWith("approach:")) {
          const targetBid = grpFilter.slice(9);
          if (pt.group !== "approach" || (pt.block_id !== undefined && String(pt.block_id) !== targetBid)) continue;
        } else if (grpFilter.startsWith("outgoing:")) {
          const targetBid = grpFilter.slice(9);
          if (pt.group !== "outgoing" || (pt.block_id !== undefined && String(pt.block_id) !== targetBid)) continue;
        } else if (pt.group !== grpFilter) {
          continue;
        }
      }
      const [sx, sy] = worldToScreen(pt.x, pt.y);
      ctx.fillStyle = pt.color || "#f59e0b";
      ctx.shadowColor = pt.color || "#f59e0b";
      ctx.shadowBlur = 12;

      if (pt.shape === "diamond") {
        const r = 6.5;
        ctx.beginPath();
        ctx.moveTo(sx, sy - r);
        ctx.lineTo(sx + r, sy);
        ctx.lineTo(sx, sy + r);
        ctx.lineTo(sx - r, sy);
        ctx.closePath();
        ctx.fill();
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 1.4;
        ctx.stroke();
      } else {
        const r = pt.shape === "signal" ? 7.5 : 5.8;
        ctx.beginPath();
        ctx.arc(sx, sy, r, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = "#fff";
        ctx.lineWidth = 1.6;
        ctx.stroke();
      }

      if (pt.label) {
        ctx.fillStyle = "rgba(7, 11, 20, 0.85)";
        const tw = pt.label.length * 6.2 + 8;
        ctx.fillRect(sx + 8, sy - 8, tw, 15);
        ctx.fillStyle = "#fff";
        ctx.font = "700 10.5px Inter, sans-serif";
        ctx.fillText(pt.label, sx + 11, sy + 3);
      }
    }
    ctx.restore();
  }

  if (STATE.hoverPreview) {
    ctx.save();
    const hp = STATE.hoverPreview;
    const col = getHoverHighlightColor(hp.type);
    ctx.strokeStyle = col;
    ctx.fillStyle = col;
    ctx.shadowColor = col;
    ctx.shadowBlur = 16;

    // Collect all polylines to highlight (previewParts and/or previewWay)
    const waysToDraw = [];
    if (hp.previewParts && hp.previewParts.length) {
      for (const p of hp.previewParts) {
        if (p && p.length >= 2) waysToDraw.push(p);
      }
    }
    if (hp.previewWay && hp.previewWay.length >= 2) {
      waysToDraw.push(hp.previewWay);
    }

    for (const way of waysToDraw) {
      // Outer glow halo
      ctx.save();
      ctx.strokeStyle = col;
      ctx.globalAlpha = 0.32;
      ctx.lineWidth = 10;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.beginPath();
      const [hx0, hy0] = worldToScreen(way[0][0], way[0][1]);
      ctx.moveTo(hx0, hy0);
      for (let i = 1; i < way.length; i++) {
        const [hxi, hyi] = worldToScreen(way[i][0], way[i][1]);
        ctx.lineTo(hxi, hyi);
      }
      ctx.stroke();
      ctx.restore();

      // Core bright stroke
      ctx.lineWidth = 4.5;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.beginPath();
      const [sx0, sy0] = worldToScreen(way[0][0], way[0][1]);
      ctx.moveTo(sx0, sy0);
      for (let i = 1; i < way.length; i++) {
        const [sxi, syi] = worldToScreen(way[i][0], way[i][1]);
        ctx.lineTo(sxi, syi);
      }
      ctx.stroke();

      // Endpoint rings & directional chevron
      const [ex, ey] = worldToScreen(way[way.length - 1][0], way[way.length - 1][1]);
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(sx0, sy0, 4.2, 0, Math.PI * 2);
      ctx.arc(ex, ey, 4.2, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = col;

      const midIdx = Math.floor((way.length - 1) / 2);
      const [mx0, my0] = worldToScreen(way[midIdx][0], way[midIdx][1]);
      const [mx1, my1] = worldToScreen(way[midIdx + 1][0], way[midIdx + 1][1]);
      const dx = mx1 - mx0;
      const dy = my1 - my0;
      if (Math.hypot(dx, dy) > 16) {
        const cx = (mx0 + mx1) / 2;
        const cy = (my0 + my1) / 2;
        const ang = Math.atan2(dy, dx);
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(ang) * 8, cy + Math.sin(ang) * 8);
        ctx.lineTo(cx + Math.cos(ang + 2.5) * 7, cy + Math.sin(ang + 2.5) * 7);
        ctx.lineTo(cx + Math.cos(ang - 2.5) * 7, cy + Math.sin(ang - 2.5) * 7);
        ctx.closePath();
        ctx.fill();
      }
    }

    // Highlight bounding box if hovering a label (CAD label, Station label, Signal label, ELR label, etc.)
    if (hp.previewBox) {
      const b = hp.previewBox;
      ctx.save();
      ctx.strokeStyle = col;
      ctx.lineWidth = 2.2;
      ctx.translate(b.x, b.y);
      if (b.angle) {
        ctx.rotate((b.angle * Math.PI) / 180);
      }
      const by = b.alignY === "center" ? -b.h / 2 : 0;
      ctx.strokeRect(-2, by - 2, b.w + 4, b.h + 4);
      ctx.restore();
    }

    // Target reticle + floating callout badge at (hp.x, hp.y)
    if (hp.x !== undefined && hp.y !== undefined && hp.x !== null) {
      const [sx, sy] = worldToScreen(hp.x, hp.y);
      ctx.strokeStyle = col;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(sx, sy, 9.5, 0, Math.PI * 2);
      ctx.stroke();

      // Crosshair ticks
      ctx.beginPath();
      ctx.moveTo(sx - 14, sy);
      ctx.lineTo(sx - 6, sy);
      ctx.moveTo(sx + 6, sy);
      ctx.lineTo(sx + 14, sy);
      ctx.moveTo(sx, sy - 14);
      ctx.lineTo(sx, sy - 6);
      ctx.moveTo(sx, sy + 6);
      ctx.lineTo(sx, sy + 14);
      ctx.stroke();

      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(sx, sy, 3.8, 0, Math.PI * 2);
      ctx.fill();

      if (hp.title) {
        ctx.shadowBlur = 0;
        ctx.font = "700 10.5px Inter, sans-serif";
        const tw = Math.min(280, hp.title.length * 6.1 + 14);
        const bx = sx + 13;
        const by = sy - 22;
        ctx.fillStyle = "rgba(7, 11, 20, 0.92)";
        ctx.fillRect(bx, by, tw, 18);
        ctx.strokeStyle = col;
        ctx.lineWidth = 1.4;
        ctx.strokeRect(bx, by, tw, 18);
        ctx.fillStyle = "#f8fafc";
        ctx.fillText(hp.title.slice(0, 44), bx + 6, by + 12.5);
      }
    }
    ctx.restore();
  }

  // Profile Drawer hover scrub reticle on 2D map
  if (STATE.profileDrawer && STATE.profileDrawer.visible && STATE.profileDrawer.scrubWorldPos) {
    const sp = STATE.profileDrawer.scrubWorldPos;
    if (sp.x !== undefined && sp.y !== undefined && sp.x !== null && sp.y !== null) {
      const [sx, sy] = worldToScreen(sp.x, sp.y);
      ctx.save();
      ctx.strokeStyle = "#06b6d4";
      ctx.fillStyle = "#06b6d4";
      ctx.shadowColor = "#06b6d4";
      ctx.shadowBlur = 18;

      // Outer pulsing ring
      ctx.beginPath();
      ctx.lineWidth = 2.5;
      ctx.arc(sx, sy, 11, 0, Math.PI * 2);
      ctx.stroke();

      // Crosshairs
      ctx.lineWidth = 1.8;
      ctx.beginPath();
      ctx.moveTo(sx - 16, sy);
      ctx.lineTo(sx - 5, sy);
      ctx.moveTo(sx + 5, sy);
      ctx.lineTo(sx + 16, sy);
      ctx.moveTo(sx, sy - 16);
      ctx.lineTo(sx, sy - 5);
      ctx.moveTo(sx, sy + 5);
      ctx.lineTo(sx, sy + 16);
      ctx.stroke();

      // Center dot
      ctx.fillStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(sx, sy, 3.5, 0, Math.PI * 2);
      ctx.fill();

      // Callout label badge
      if (sp.label) {
        ctx.shadowBlur = 0;
        ctx.font = "700 11px Inter, sans-serif";
        const tw = Math.min(280, sp.label.length * 6.5 + 16);
        const bx = sx + 14;
        const by = sy - 24;
        ctx.fillStyle = "rgba(7, 11, 20, 0.92)";
        ctx.fillRect(bx, by, tw, 20);
        ctx.strokeStyle = "#06b6d4";
        ctx.lineWidth = 1.4;
        ctx.strokeRect(bx, by, tw, 20);
        ctx.fillStyle = "#38bdf8";
        ctx.fillText(sp.label, bx + 7, by + 14);
      }
      ctx.restore();
    }
  }
}

