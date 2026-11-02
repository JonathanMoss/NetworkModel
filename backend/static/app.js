const STATE = {
  activeView: "tps", // "tps" | "nwr"
  ready: false,
  overview: null,
  nwrOverview: null,

  // Separate camera states per view so switching views remembers your position
  camX: 110000,
  camY: -5000,
  zoom: 0.018,
  savedCam: {
    tps: { camX: 110000, camY: -5000, zoom: 0.018 },
    nwr: { camX: 410000, camY: 450000, zoom: 0.0009 },
  },

  isDragging: false,
  dragStartX: 0,
  dragStartY: 0,
  camStartX: 0,
  camStartY: 0,
  pointerMoved: false,

  colorMode: "electrified",
  nwrColorMode: "track_type",

  layers: {
    edges: true,
    tracks: true,
    vectors: true,
    texts: true,
    stations: true,
    signals: true,
    blocks: false,
    nodes: false,
    polys: true,
  },
  nwrLayers: {
    centrelines: true,
    elrs: true,
    nodes: true,
    waymarks: true,
  },

  labelScale: {
    all: 1.0,
    texts: 1.0,
    stations: 1.0,
    signals: 1.0,
    blocks: 1.0,
    nodes: 1.0,
    polys: 1.0,
    nwr_elrs: 1.0,
    nwr_waymarks: 1.0,
    nwr_nodes: 1.0,
  },

  labelOrientation: {
    all: "",
    texts: "native",
    stations: 0,
    signals: 0,
    blocks: 0,
    nodes: 0,
    polys: 0,
    nwr_elrs: 0,
    nwr_waymarks: 0,
    nwr_nodes: 0,
  },

  viewportData: {
    edges: [],
    tracks: [],
    vectors: [],
    texts: [],
    signals: [],
    nodes: [],
    blocks: [],
    bbox: null,
  },
  nwrViewportData: {
    centrelines: [],
    nodes: [],
    waymarks: [],
    bbox: null,
  },
  viewportTimer: null,
  viewportAbort: null,

  // Screen-space bounding boxes of labels drawn in the current frame
  drawnStationLabels: [],
  drawnSignalLabels: [],
  drawnCadLabels: [],
  drawnBlockLabels: [],
  drawnNodeLabels: [],
  drawnAnnotationLabels: [],
  drawnNwrElrLabels: [],
  drawnNwrWaymarkLabels: [],
  drawnNwrNodeLabels: [],

  // Hover submenu & live preview state
  hoverCandidates: [],
  hoverPreview: null,
  isPointerInMenu: false,
  hoverHideTimer: null,
  hoverEnrichTimer: null,
  hoverWorldPos: null,
  lastHoverAnchor: { sx: 0, sy: 0 },

  selectedAsset: null,
  highlightGroupFilter: "all",
  highlight: {
    ways: [],
    points: [],
    segments: [],
  },

  catalogTags: [],
  catalogCurrentTag: null,
  catalogOffset: 0,
  catalogLimit: 50,

  leftPaneCollapsed: false,
  rightPaneCollapsed: false,
};

const canvas = document.getElementById("schematic-canvas");
const ctx = canvas.getContext("2d");
const hoverTooltip = document.getElementById("hover-tooltip");

function resizeCanvas() {
  const wrap = document.getElementById("canvas-container");
  const dpr = window.devicePixelRatio || 1;
  canvas.width = wrap.clientWidth * dpr;
  canvas.height = wrap.clientHeight * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  requestRender();
}
window.addEventListener("resize", resizeCanvas);

/* Label Scale & Orientation Calculation Helpers */
function getLayerLabelScale(key) {
  const master = STATE.labelScale.all || 1.0;
  const layer = STATE.labelScale[key] !== undefined ? STATE.labelScale[key] : 1.0;
  return Math.max(0.4, Math.min(4.0, master * layer));
}

function getLayerLabelAngle(key, nativeAngle = 0) {
  if (STATE.labelOrientation.all !== "") {
    return Number(STATE.labelOrientation.all) || 0;
  }
  const setting = STATE.labelOrientation[key];
  if (setting === "native") {
    return Number(nativeAngle) || 0;
  }
  return Number(setting) || 0;
}

function isPointInOrientedBox(px, py, ox, oy, w, h, angleDeg = 0, alignY = "center") {
  if (!angleDeg) {
    const y0 = alignY === "center" ? oy - h / 2 : oy;
    return px >= ox - 3 && px <= ox + w + 3 && py >= y0 - 3 && py <= y0 + h + 3;
  }
  const rad = (-angleDeg * Math.PI) / 180;
  const dx = px - ox;
  const dy = py - oy;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const localX = dx * cos - dy * sin;
  const localY = dx * sin + dy * cos;
  const y0 = alignY === "center" ? -h / 2 : 0;
  return localX >= -3 && localX <= w + 3 && localY >= y0 - 3 && localY <= y0 + h + 3;
}

/* Coordinate Projections (TPS netY is South-positive; NWR OSGB36 Northing Y is North-positive) */
function worldToScreen(wx, wy) {
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (STATE.activeView === "nwr") {
    return [
      (wx - STATE.camX) * STATE.zoom + w / 2,
      (STATE.camY - wy) * STATE.zoom + h / 2,
    ];
  }
  return [
    (wx - STATE.camX) * STATE.zoom + w / 2,
    (wy - STATE.camY) * STATE.zoom + h / 2,
  ];
}

function screenToWorld(sx, sy) {
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (STATE.activeView === "nwr") {
    return [
      (sx - w / 2) / STATE.zoom + STATE.camX,
      STATE.camY - (sy - h / 2) / STATE.zoom,
    ];
  }
  return [
    (sx - w / 2) / STATE.zoom + STATE.camX,
    (sy - h / 2) / STATE.zoom + STATE.camY,
  ];
}

function getViewportBBox(padFactor = 0.25) {
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  const [x0, y0] = screenToWorld(0, 0);
  const [x1, y1] = screenToWorld(w, h);
  const minX = Math.min(x0, x1);
  const maxX = Math.max(x0, x1);
  const minY = Math.min(y0, y1);
  const maxY = Math.max(y0, y1);
  const dx = (maxX - minX) * padFactor;
  const dy = (maxY - minY) * padFactor;
  return {
    min_x: minX - dx,
    max_x: maxX + dx,
    min_y: minY - dy,
    max_y: maxY + dy,
  };
}

let renderPending = false;
function requestRender() {
  if (!renderPending) {
    renderPending = true;
    requestAnimationFrame(() => {
      renderPending = false;
      if (STATE.activeView === "nwr") {
        renderNwrModel();
      } else {
        renderSchematic();
      }
    });
  }
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
  15: { color: "#64748b", label: "Switch Diamond", short: "SW-D", aspects: 0 },
  16: { color: "#64748b", label: "Switch / Points", short: "SW", aspects: 0 },
};

function getSignalInterlockingInfo(ilockId, bumper) {
  if (bumper === 1) {
    return { color: "#ef4444", label: "Buffer Stop / Bumper", short: "BUF", aspects: 0 };
  }
  return (
    INTERLOCKING_STYLES[ilockId] || {
      color: "#f59e0b",
      label: `Interlocking #${ilockId || 0}`,
      short: "SIG",
      aspects: 0,
    }
  );
}

function getEdgeColor(vmax, electrified, gradient, vpCount = 0) {
  if (STATE.colorMode === "electrified") {
    return electrified ? "#38bdf8" : "#f59e0b";
  }
  if (STATE.colorMode === "signalling") {
    return "#475569";
  }
  if (STATE.colorMode === "timing") {
    return vpCount > 0 ? "#22d3ee" : "#334155";
  }
  if (STATE.colorMode === "vmax") {
    if (vmax >= 160) return "#f43f5e";
    if (vmax >= 120) return "#f97316";
    if (vmax >= 80) return "#eab308";
    if (vmax >= 45) return "#10b981";
    return "#6366f1";
  }
  if (STATE.colorMode === "gradient") {
    if (gradient > 2.0) return "#f43f5e";
    if (gradient < -2.0) return "#38bdf8";
    return "#10b981";
  }
  return "#94a3b8";
}

function getNwrTrackColor(trackId, ownerCodeOrStr) {
  const tid = String(trackId || "");
  if (STATE.nwrColorMode === "ownership") {
    if (typeof ownerCodeOrStr === "number") {
      if (ownerCodeOrStr === 0) return "#38bdf8";
      if (ownerCodeOrStr === 1) return "#f43f5e";
      return "#f59e0b";
    }
    const s = String(ownerCodeOrStr || "");
    if (s.includes("NETWORK RAIL:100%")) return "#38bdf8";
    if (s.includes("NETWORK RAIL:0%") && !s.includes("THIRD PARTY:0%")) return "#f43f5e";
    return "#f59e0b";
  }
  if (STATE.nwrColorMode === "direction") {
    const d1 = tid[0];
    if (d1 === "1") return "#38bdf8"; // UP
    if (d1 === "2") return "#f97316"; // DOWN
    if (d1 === "3") return "#a855f7"; // BI-DIRECTIONAL
    return "#94a3b8";
  }
  // Default: track_type (2nd digit of TRACK_ID per Appendix A)
  const d2 = tid[1];
  if (d2 === "1") return "#38bdf8"; // MAIN FAST
  if (d2 === "2") return "#10b981"; // SLOW
  if (d2 === "4") return "#eab308"; // SINGLE
  if (d2 === "5") return "#818cf8"; // LOOP
  if (d2 === "3") return "#f97316"; // GOODS
  if (d2 === "6" || d2 === "7") return "#f43f5e"; // TERMINAL / CROSSOVER
  return "#64748b"; // SIDING / OTHER
}

function updateLegend() {
  const box = document.getElementById("legend-box");
  let items = [];
  if (STATE.activeView === "nwr") {
    if (STATE.nwrColorMode === "track_type") {
      items = [
        ["#38bdf8", "Main Fast (x100)"],
        ["#10b981", "Slow Line (x200)"],
        ["#eab308", "Single Line (x400)"],
        ["#818cf8", "Loop (x500)"],
        ["#f97316", "Goods Line (x300)"],
        ["#f43f5e", "Crossover / Terminal (x600/x700)"],
        ["#64748b", "Siding / Engine (x800/x900)"],
        ["#a855f7", "ELR Route Reference Line"],
      ];
    } else if (STATE.nwrColorMode === "direction") {
      items = [
        ["#38bdf8", "Up Direction (1xxx)"],
        ["#f97316", "Down Direction (2xxx)"],
        ["#a855f7", "Reversible / Bi-Directional (3xxx)"],
      ];
    } else {
      items = [
        ["#38bdf8", "Network Rail (100%)"],
        ["#f43f5e", "Third Party Owned"],
        ["#f59e0b", "Mixed / Unclassified Ownership"],
      ];
    }
  } else {
    const mode = STATE.colorMode;
    if (mode === "electrified") {
      items = [
        ["#38bdf8", "Electrified (OHLE / 3rd Rail)"],
        ["#f59e0b", "Non-Electrified (Diesel)"],
        ["#818cf8", "Station Platform / Loop Way"],
        ["#64748b", "CAD Schematic Vector"],
      ];
    } else if (mode === "signalling") {
      items = [
        ["#10b981", "4-Aspect Colour Light (IECC / Panel)"],
        ["#38bdf8", "3-Aspect Colour Light (IECC / Panel)"],
        ["#eab308", "2-Aspect Colour Light (IECC / Panel)"],
        ["#f97316", "Semaphore (Home / Starter / Shunt)"],
        ["#a855f7", "Ground Position Light / Limit of Shunt"],
        ["#d946ef", "Safety Overlap (<securitysection>)"],
        ["#ef4444", "Stop Board / Buffer Stop"],
      ];
    } else if (mode === "timing") {
      items = [
        ["#f43f5e", "Mandatory TIPLOC Timing Point (2,380)"],
        ["#38bdf8", "Optional Stop / Passenger Point (6,213)"],
        ["#f59e0b", "Optional Freight / Yard Point (1,374)"],
        ["#a855f7", "Optional Crossing / Junction (813)"],
        ["#22d3ee", "Differential Speed Profile Edge (EPS/HST/MU)"],
        ["#334155", "Standard Line Speed Edge"],
      ];
    } else if (mode === "vmax") {
      items = [
        ["#f43f5e", "High Speed (≥ 160 km/h / 100 mph)"],
        ["#f97316", "Fast Main (120–159 km/h)"],
        ["#eab308", "Medium (80–119 km/h)"],
        ["#10b981", "Regional / Slow (45–79 km/h)"],
        ["#6366f1", "Low Speed / Goods (< 45 km/h)"],
      ];
    } else if (mode === "gradient") {
      items = [
        ["#f43f5e", "Rising Gradient (> +2‰)"],
        ["#10b981", "Level / Gentle (-2‰ to +2‰)"],
        ["#38bdf8", "Falling Gradient (< -2‰)"],
      ];
    } else {
      items = [
        ["#94a3b8", "Running Track Edge"],
        ["#818cf8", "Station Track / Platform Way"],
        ["#f59e0b", "Signal / Aspect Post"],
      ];
    }
  }
  box.innerHTML = items
    .map(
      ([col, lbl]) => `
      <div class="legend-row">
        <span class="legend-swatch" style="background:${col}"></span>
        <span>${lbl}</span>
      </div>`
    )
    .join("");
}

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
    ctx.fillStyle = "rgba(168, 85, 247, 0.06)";
    ctx.strokeStyle = "rgba(168, 85, 247, 0.25)";
    ctx.lineWidth = 1;
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
    ctx.strokeStyle = "rgba(100, 116, 139, 0.48)";
    ctx.lineWidth = Math.max(0.8, Math.min(2.0, zoom * 0.45));
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
        const col = getEdgeColor(vmax, elec === 1, grad);
        let arr = batches.get(col);
        if (!arr) {
          arr = [];
          batches.set(col, arr);
        }
        arr.push(x0, y0, x1, y1);
      }
      ctx.lineWidth = Math.max(1.1, Math.min(2.4, zoom * 18));
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
        const col = getEdgeColor(e[8], e[9] === 1, e[10], e[12] || 0);
        let arr = batches.get(col);
        if (!arr) {
          arr = [];
          batches.set(col, arr);
        }
        arr.push(x0, y0, x1, y1);
      }
      ctx.lineWidth = Math.max(1.4, Math.min(3.5, zoom * 0.9));
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
    ctx.strokeStyle = "rgba(129, 140, 248, 0.55)";
    ctx.lineWidth = Math.max(2.5, Math.min(6.0, zoom * 1.6));
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
    ctx.save();
    ctx.lineWidth = Math.max(2.0, Math.min(4.2, zoom * 1.1));
    for (const b of STATE.viewportData.blocks) {
      // [pk, block_id, pre_signal_distance, sight_distance, shunting_flag, atc_flag, moving_block, coords]
      const coords = b[7];
      if (!coords || coords.length < 2) continue;
      const isShunt = b[4] === 1;
      ctx.strokeStyle = isShunt
        ? "rgba(168, 85, 247, 0.45)"
        : "rgba(16, 185, 129, 0.42)";
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
  drawHighlightAndPreviewOverlays();

  document.getElementById("hud-zoom").textContent = `${zoom.toFixed(3)}x (${
    useMicro ? "TPS Microscopic" : "TPS Macro"
  })`;
}

/* ============================================================================
   VIEW 2: NWR GEOSPATIAL TRACK MODEL RENDER LOOP (OSGB36 BRITISH NATIONAL GRID)
   ============================================================================ */
function renderNwrModel() {
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  ctx.clearRect(0, 0, w, h);

  STATE.drawnNwrElrLabels = [];
  STATE.drawnNwrWaymarkLabels = [];
  STATE.drawnNwrNodeLabels = [];

  if (!STATE.nwrOverview) return;

  const bbox = getViewportBBox(0.05);
  const zoom = STATE.zoom;
  const useMicro = zoom >= 0.025 && STATE.nwrViewportData.centrelines.length > 0;

  // 1. NWR_ELRs Reference Lines
  if (STATE.nwrLayers.elrs && STATE.nwrOverview.elrs) {
    ctx.strokeStyle = "rgba(168, 85, 247, 0.35)";
    ctx.lineWidth = useMicro ? 2.5 : 1.2;
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
    if (!useMicro) {
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
        const col = getNwrTrackColor(m[2], m[3]);
        let arr = batches.get(col);
        if (!arr) {
          arr = [];
          batches.set(col, arr);
        }
        arr.push(x0, y0, x1, y1);
      }
      ctx.lineWidth = Math.max(1.1, Math.min(2.2, zoom * 250));
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
        const col = getNwrTrackColor(cl[3], cl[6]);
        let arr = batches.get(col);
        if (!arr) {
          arr = [];
          batches.set(col, arr);
        }
        arr.push(coords);
      }
      ctx.lineWidth = Math.max(1.6, Math.min(3.8, zoom * 12));
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

  // 3. NWR_TrackNodes (Valency 1-4)
  if (STATE.nwrLayers.nodes && useMicro && zoom >= 0.06 && STATE.nwrViewportData.nodes.length) {
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

  // 4. NWR_Waymarks (Mileposts)
  if (STATE.nwrLayers.waymarks && useMicro && zoom >= 0.04 && STATE.nwrViewportData.waymarks.length) {
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
  drawHighlightAndPreviewOverlays();

  document.getElementById("hud-zoom").textContent = `${(zoom * 1000).toFixed(2)} px/km (${
    useMicro ? "NWR Detailed" : "NWR Macro"
  })`;
}

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
    default:
      return "#22d3ee";
  }
}

function drawHighlightAndPreviewOverlays() {
  if (
    STATE.highlight.ways.length ||
    STATE.highlight.points.length ||
    (STATE.highlight.segments && STATE.highlight.segments.length)
  ) {
    ctx.save();
    const grpFilter = STATE.highlightGroupFilter || "all";

    if (STATE.highlight.segments && STATE.highlight.segments.length) {
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
      if (
        grpFilter !== "all" &&
        pt.group &&
        pt.group !== "signal"
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
}

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
function findObjectsAtCursor(sx, sy, wx, wy) {
  if (STATE.activeView === "nwr") {
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
        addHit({
          type: "signal",
          id: s[0],
          title: `${s[9] === 1 ? "Buffer Stop" : "Signal"} ${s[3] || "#" + s[2]}`,
          sub: `${ilock.label}${ovCnt ? " • " + ovCnt + " Overlap(s)" : ""} • #${s[10] || "?"}→#${s[11] || "?"}`,
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
        addHit({
          type: "signal",
          id: s[0],
          title: `Signal ${s[3] || "#" + s[2]}`,
          sub: `${ilock.label} • Master ID #${s[2]}`,
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
        addHit({
          type: "node",
          id: n[0],
          title: `Track Node #${n[0]}`,
          sub: `KM: ${n[5] || 0}m • Angle ${n[3]}° • (${n[1]}, ${n[2]})`,
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

  const zoom = STATE.zoom;
  const useMicro = zoom >= 0.025 && STATE.nwrViewportData.centrelines.length > 0;
  const pxTol = 10;

  // 1. NWR Track Nodes & Node Labels
  if (STATE.nwrLayers.nodes && useMicro && zoom >= 0.06) {
    for (const lbl of STATE.drawnNwrNodeLabels) {
      if (isPointInOrientedBox(sx, sy, lbl.x, lbl.y, lbl.w, lbl.h, lbl.angle || 0, lbl.alignY || "center")) {
        const n = lbl.node;
        addHit({
          type: "nwr_node",
          id: n[0],
          title: `Track Node ${n[1]}`,
          sub: `Label hit • Valency ${n[2]} • (${n[3]} E, ${n[4]} N)`,
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
        addHit({
          type: "nwr_node",
          id: n[0],
          title: `Track Node ${n[1]}`,
          sub: `Valency ${n[2]} • (${n[3]} E, ${n[4]} N)`,
          x: n[3],
          y: n[4],
          score: d,
        });
      }
    }
  }

  // 2. NWR Waymarks & Labels
  if (STATE.nwrLayers.waymarks && useMicro && zoom >= 0.04) {
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
  if (STATE.nwrLayers.centrelines && useMicro) {
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

  hits.sort((a, b) => a.score - b.score);
  return hits.slice(0, 12);
}

/* Viewport Data Fetching */
function scheduleViewportFetch() {
  if (STATE.viewportTimer) clearTimeout(STATE.viewportTimer);
  if (STATE.activeView === "nwr") {
    if (STATE.zoom < 0.025) {
      document.getElementById("hud-loaded").textContent = `${
        STATE.nwrOverview ? STATE.nwrOverview.macro_links.length.toLocaleString() : 0
      } NWR links`;
      requestRender();
      return;
    }
    STATE.viewportTimer = setTimeout(fetchNwrViewportData, 90);
    return;
  }

  if (STATE.zoom < 0.16) {
    document.getElementById("hud-loaded").textContent = `${
      STATE.overview ? STATE.overview.macro_edges.length.toLocaleString() : 0
    } macro segs`;
    requestRender();
    return;
  }
  STATE.viewportTimer = setTimeout(fetchViewportData, 90);
}

async function fetchViewportData() {
  if (!STATE.ready || STATE.activeView !== "tps" || STATE.zoom < 0.16) return;
  if (STATE.viewportAbort) STATE.viewportAbort.abort();
  STATE.viewportAbort = new AbortController();

  const bbox = getViewportBBox(0.3);
  const params = new URLSearchParams({
    min_x: bbox.min_x.toFixed(1),
    max_x: bbox.max_x.toFixed(1),
    min_y: bbox.min_y.toFixed(1),
    max_y: bbox.max_y.toFixed(1),
    zoom: STATE.zoom.toFixed(4),
    include_vectors: STATE.layers.vectors,
    include_texts: STATE.layers.texts && STATE.zoom >= 0.4,
    include_signals: STATE.layers.signals && STATE.zoom >= 0.3,
    include_nodes: STATE.layers.nodes || STATE.zoom >= 2.0,
    include_tracks: STATE.layers.tracks,
    include_blocks:
      STATE.layers.blocks ||
      (STATE.colorMode === "signalling" && STATE.zoom >= 0.55),
    include_vp: STATE.colorMode === "timing",
    include_overlaps: STATE.colorMode === "signalling" || STATE.zoom >= 1.1,
  });

  try {
    const res = await fetch(`/api/viewport?${params.toString()}`, {
      signal: STATE.viewportAbort.signal,
    });
    if (!res.ok) return;
    const data = await res.json();
    STATE.viewportData = { ...data, bbox };
    document.getElementById("hud-loaded").textContent = `${data.edges.length.toLocaleString()} edges, ${data.signals.length.toLocaleString()} sigs`;
    requestRender();
  } catch (err) {
    if (err.name !== "AbortError") console.error(err);
  }
}

async function fetchNwrViewportData() {
  if (STATE.activeView !== "nwr" || STATE.zoom < 0.025) return;
  if (STATE.viewportAbort) STATE.viewportAbort.abort();
  STATE.viewportAbort = new AbortController();

  const bbox = getViewportBBox(0.3);
  const params = new URLSearchParams({
    min_x: bbox.min_x.toFixed(1),
    max_x: bbox.max_x.toFixed(1),
    min_y: bbox.min_y.toFixed(1),
    max_y: bbox.max_y.toFixed(1),
    zoom: STATE.zoom.toFixed(5),
    include_centrelines: STATE.nwrLayers.centrelines,
    include_nodes: STATE.nwrLayers.nodes && STATE.zoom >= 0.06,
    include_waymarks: STATE.nwrLayers.waymarks && STATE.zoom >= 0.04,
  });

  try {
    const res = await fetch(`/api/nwr/viewport?${params.toString()}`, {
      signal: STATE.viewportAbort.signal,
    });
    if (!res.ok) return;
    const data = await res.json();
    STATE.nwrViewportData = { ...data, bbox };
    document.getElementById("hud-loaded").textContent = `${data.centrelines.length.toLocaleString()} links, ${data.nodes.length.toLocaleString()} nodes`;
    requestRender();
  } catch (err) {
    if (err.name !== "AbortError") console.error(err);
  }
}

/* Fit Whole Network */
function fitUKNetwork() {
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (STATE.activeView === "nwr") {
    if (!STATE.nwrOverview) return;
    const b = STATE.nwrOverview.bounds;
    const spanX = b.max_x - b.min_x;
    const spanY = b.max_y - b.min_y;
    STATE.camX = (b.min_x + b.max_x) / 2;
    STATE.camY = (b.min_y + b.max_y) / 2;
    STATE.zoom = Math.min((w * 0.88) / spanX, (h * 0.88) / spanY);
  } else {
    if (!STATE.overview) return;
    const b = STATE.overview.bounds;
    const spanX = b.max_x - b.min_x;
    const spanY = b.max_y - b.min_y;
    STATE.camX = (b.min_x + b.max_x) / 2;
    STATE.camY = (b.min_y + b.max_y) / 2;
    STATE.zoom = Math.min((w * 0.88) / spanX, (h * 0.88) / spanY);
  }
  scheduleViewportFetch();
  requestRender();
}

function flyTo(x, y, targetZoom = 1.6) {
  if (x === null || y === null || isNaN(x) || isNaN(y)) return;
  STATE.camX = x;
  STATE.camY = y;
  if (targetZoom) STATE.zoom = targetZoom;
  scheduleViewportFetch();
  requestRender();
}

/* Pane Folding & View-Dependent Layout */
function updatePaneLayout() {
  const isNwr = STATE.activeView === "nwr";
  const leftPane = document.getElementById("left-panel");
  const rightPane = document.getElementById("inspector-drawer");
  const btnLeft = document.getElementById("btn-toggle-left-pane");
  const btnRight = document.getElementById("btn-toggle-right-pane");

  if (leftPane) {
    leftPane.classList.toggle("collapsed", STATE.leftPaneCollapsed);
  }
  if (btnLeft) {
    btnLeft.innerHTML = STATE.leftPaneCollapsed ? "&#9654; Show Layers" : "&#9664; Hide Layers";
  }

  if (rightPane) {
    // Do not show the right pane in the geographical (NWR) view
    const hideRight = isNwr || STATE.rightPaneCollapsed;
    rightPane.classList.toggle("collapsed", hideRight);
  }
  if (btnRight) {
    btnRight.classList.toggle("hidden", isNwr);
    btnRight.innerHTML = STATE.rightPaneCollapsed ? "&#9664; Show Inspector" : "Hide Inspector &#9654;";
  }

  resizeCanvas();
  scheduleViewportFetch();
  requestRender();
}

const btnToggleLeftPane = document.getElementById("btn-toggle-left-pane");
if (btnToggleLeftPane) {
  btnToggleLeftPane.addEventListener("click", () => {
    STATE.leftPaneCollapsed = !STATE.leftPaneCollapsed;
    updatePaneLayout();
  });
}

const btnToggleRightPane = document.getElementById("btn-toggle-right-pane");
if (btnToggleRightPane) {
  btnToggleRightPane.addEventListener("click", () => {
    STATE.rightPaneCollapsed = !STATE.rightPaneCollapsed;
    updatePaneLayout();
  });
}

/* Switch Between TPS Schematic View and NWR Track Model View */
async function switchView(targetView) {
  if (STATE.activeView === targetView) return;

  // Save current camera
  STATE.savedCam[STATE.activeView] = {
    camX: STATE.camX,
    camY: STATE.camY,
    zoom: STATE.zoom,
  };

  STATE.activeView = targetView;
  hoverTooltip.classList.add("hidden");
  STATE.hoverCandidates = [];
  STATE.hoverPreview = null;
  STATE.highlight = { ways: [], points: [], segments: [] };
  document.getElementById("btn-clear-highlight").classList.add("hidden");
  document.getElementById("pick-candidates").classList.add("hidden");

  const isNwr = targetView === "nwr";
  document.getElementById("view-btn-tps").classList.toggle("active", !isNwr);
  document.getElementById("view-btn-nwr").classList.toggle("active", isNwr);

  document.getElementById("tps-layers-panel").classList.toggle("hidden", isNwr);
  document.getElementById("nwr-layers-panel").classList.toggle("hidden", !isNwr);
  document.getElementById("tps-hubs-panel").classList.toggle("hidden", isNwr);
  document.getElementById("nwr-hubs-panel").classList.toggle("hidden", !isNwr);
  document.getElementById("tps-color-control").classList.toggle("hidden", isNwr);
  document.getElementById("nwr-color-control").classList.toggle("hidden", !isNwr);
  document.getElementById("btn-timing-loads").classList.toggle("hidden", isNwr);
  document.getElementById("os-copyright").classList.toggle("hidden", !isNwr);
  document.getElementById("hud-coord-label").textContent = isNwr
    ? "OSGB36 (Easting, Northing):"
    : "Cursor (netx, nety):";
  document.getElementById("global-search").placeholder = isNwr
    ? "Search NWR Track Model by ELR (e.g. ECM1, AAV), ASSET_ID (9001...), or TRACK_ID (1100)..."
    : "Search Station (Name, CRS, TIPLOC, STANOX), Signal, Timing Load (390, 800, 450, 66, HST), Timetable Corridor (MD710, Marylebone), Node ID, Block, Route, or ELR...";

  updatePaneLayout();

  if (isNwr && !STATE.nwrOverview) {
    const res = await fetch("/api/nwr/overview");
    if (res.ok) {
      STATE.nwrOverview = await res.json();
      fitUKNetwork();
      updateLegend();
      return;
    }
  }

  // Restore saved camera for targetView
  const saved = STATE.savedCam[targetView];
  STATE.camX = saved.camX;
  STATE.camY = saved.camY;
  STATE.zoom = saved.zoom;

  updateLegend();
  scheduleViewportFetch();
  requestRender();
}

document.getElementById("view-btn-tps").addEventListener("click", () => switchView("tps"));
document.getElementById("view-btn-nwr").addEventListener("click", () => switchView("nwr"));

/* Cross-Model Jump Helpers (called from Inspector cards) */
window.jumpToTpsAsset = async function (assetType, assetId) {
  await switchView("tps");
  await loadAssetDetails(assetType, assetId, true);
};

window.jumpToNwrElr = async function (elrCode) {
  await switchView("nwr");
  if (!STATE.nwrOverview) return;
  const elrObj = STATE.nwrOverview.elrs.find(
    (e) => e.elr.toUpperCase() === String(elrCode).toUpperCase()
  );
  if (elrObj) {
    await loadAssetDetails("nwr_elr", elrObj.fid, true);
  }
};

/* Hover Submenu Interaction Wiring */
hoverTooltip.addEventListener("mouseenter", () => {
  STATE.isPointerInMenu = true;
  if (STATE.hoverHideTimer) {
    clearTimeout(STATE.hoverHideTimer);
    STATE.hoverHideTimer = null;
  }
});

hoverTooltip.addEventListener("mouseleave", () => {
  STATE.isPointerInMenu = false;
  STATE.hoverPreview = null;
  hoverTooltip.classList.add("hidden");
  requestRender();
});

function renderHoverMenu(candidates, sx, sy, reposition = true) {
  if (!candidates.length) {
    hoverTooltip.classList.add("hidden");
    return;
  }

  const headerText =
    candidates.length > 1
      ? `${candidates.length} Overlapping Objects — Roll Over to Highlight, Click to View`
      : `1 Object Under Cursor — Click to View`;

  const activeKey = STATE.hoverPreview
    ? `${STATE.hoverPreview.type}:${STATE.hoverPreview.id}`
    : `${candidates[0].type}:${candidates[0].id}`;

  hoverTooltip.innerHTML = `
    <div class="hover-menu-header">
      <span>${headerText}</span>
    </div>
    ${candidates
      .map((c, idx) => {
        const isActive = `${c.type}:${c.id}` === activeKey;
        return `
      <div class="hover-menu-item ${isActive ? "active" : ""}" data-idx="${idx}">
        <span class="hover-type-badge hover-type-${escapeHtml(c.type)}">${escapeHtml(c.type.replace("nwr_", ""))}</span>
        <div class="hover-item-body">
          <div class="hover-item-title">${escapeHtml(c.title)}</div>
          <div class="hover-item-sub">${escapeHtml(c.sub || "")}</div>
        </div>
      </div>`;
      })
      .join("")}
  `;

  hoverTooltip.querySelectorAll(".hover-menu-item").forEach((el) => {
    const idx = Number(el.dataset.idx);
    const item = candidates[idx];
    el.addEventListener("mouseenter", () => {
      hoverTooltip.querySelectorAll(".hover-menu-item").forEach((r) => r.classList.remove("active"));
      el.classList.add("active");
      STATE.hoverPreview = item;
      requestRender();
    });
    el.addEventListener("click", (ev) => {
      ev.stopPropagation();
      STATE.isPointerInMenu = false;
      hoverTooltip.classList.add("hidden");
      loadAssetDetails(item.type, item.id, false);
    });
  });

  if (reposition) {
    const padX = 14;
    const padY = -14;
    const maxLeft = Math.max(10, canvas.clientWidth - 365);
    const maxTop = Math.max(10, canvas.clientHeight - 280);
    hoverTooltip.style.left = `${Math.min(maxLeft, sx + padX)}px`;
    hoverTooltip.style.top = `${Math.min(maxTop, Math.max(10, sy + padY))}px`;
    STATE.lastHoverAnchor = { sx, sy };
  }
  hoverTooltip.classList.remove("hidden");
}

/* Mouse / Wheel Pan & Zoom */
canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  STATE.isDragging = true;
  STATE.pointerMoved = false;
  STATE.dragStartX = e.clientX;
  STATE.dragStartY = e.clientY;
  STATE.camStartX = STATE.camX;
  STATE.camStartY = STATE.camY;
});

window.addEventListener("mousemove", (e) => {
  if (STATE.isPointerInMenu) return;

  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;
  const [wx, wy] = screenToWorld(sx, sy);

  if (sx >= 0 && sy >= 0 && sx <= rect.width && sy <= rect.height) {
    document.getElementById("hud-coords").textContent = `(${Math.round(wx)}, ${Math.round(wy)})`;
  }

  if (STATE.isDragging) {
    const dx = e.clientX - STATE.dragStartX;
    const dy = e.clientY - STATE.dragStartY;
    if (Math.hypot(dx, dy) > 4) {
      STATE.pointerMoved = true;
      hoverTooltip.classList.add("hidden");
    }
    STATE.camX = STATE.camStartX - dx / STATE.zoom;
    if (STATE.activeView === "nwr") {
      STATE.camY = STATE.camStartY + dy / STATE.zoom;
    } else {
      STATE.camY = STATE.camStartY - dy / STATE.zoom;
    }
    requestRender();
    return;
  }

  handleHover(sx, sy, wx, wy);
});

window.addEventListener("mouseup", (e) => {
  if (!STATE.isDragging) return;
  STATE.isDragging = false;
  if (STATE.pointerMoved) {
    scheduleViewportFetch();
  } else {
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const [wx, wy] = screenToWorld(sx, sy);
    handleCanvasClick(wx, wy);
  }
});

canvas.addEventListener(
  "wheel",
  (e) => {
    e.preventDefault();
    hoverTooltip.classList.add("hidden");
    const rect = canvas.getBoundingClientRect();
    const sx = e.clientX - rect.left;
    const sy = e.clientY - rect.top;
    const [wxBefore, wyBefore] = screenToWorld(sx, sy);

    const factor = e.deltaY < 0 ? 1.25 : 0.8;
    const minZ = STATE.activeView === "nwr" ? 0.0004 : 0.008;
    const maxZ = STATE.activeView === "nwr" ? 5.0 : 25.0;
    STATE.zoom = Math.max(minZ, Math.min(maxZ, STATE.zoom * factor));

    const [wxAfter, wyAfter] = screenToWorld(sx, sy);
    STATE.camX += wxBefore - wxAfter;
    STATE.camY += wyBefore - wyAfter;

    scheduleViewportFetch();
    requestRender();
  },
  { passive: false }
);

/* Hover Detection, Precision Pointer Cursor & Submenu */
function handleHover(sx, sy, wx, wy) {
  if (
    sx < 0 ||
    sy < 0 ||
    sx > canvas.clientWidth ||
    sy > canvas.clientHeight
  ) {
    canvas.classList.remove("has-target");
    if (!STATE.isPointerInMenu) {
      hoverTooltip.classList.add("hidden");
      STATE.hoverPreview = null;
      requestRender();
    }
    return;
  }

  const menuOpen = !hoverTooltip.classList.contains("hidden");
  if (
    menuOpen &&
    STATE.hoverCandidates.length > 1 &&
    Math.hypot(sx - STATE.lastHoverAnchor.sx, sy - STATE.lastHoverAnchor.sy) < 22
  ) {
    canvas.classList.add("has-target");
    return;
  }

  const hits = findObjectsAtCursor(sx, sy, wx, wy);

  if (hits.length > 0) {
    if (STATE.hoverHideTimer) {
      clearTimeout(STATE.hoverHideTimer);
      STATE.hoverHideTimer = null;
    }
    canvas.classList.add("has-target");
    STATE.hoverCandidates = hits;
    STATE.hoverPreview = hits[0];
    STATE.hoverWorldPos = { wx, wy, sx, sy };
    renderHoverMenu(hits, sx, sy, true);
    requestRender();

    if (STATE.hoverEnrichTimer) clearTimeout(STATE.hoverEnrichTimer);
    STATE.hoverEnrichTimer = setTimeout(() => enrichHoverSubmenu(wx, wy, sx, sy), 130);
  } else {
    canvas.classList.remove("has-target");
    if (STATE.hoverEnrichTimer) clearTimeout(STATE.hoverEnrichTimer);
    if (!STATE.hoverHideTimer) {
      STATE.hoverHideTimer = setTimeout(() => {
        if (!STATE.isPointerInMenu) {
          hoverTooltip.classList.add("hidden");
          STATE.hoverCandidates = [];
          STATE.hoverPreview = null;
          requestRender();
        }
        STATE.hoverHideTimer = null;
      }, 200);
    }
  }
}

async function enrichHoverSubmenu(wx, wy, sx, sy) {
  if (!STATE.hoverCandidates.length) return;
  const radius = Math.max(10, 14 / STATE.zoom);
  const endpoint = STATE.activeView === "nwr" ? "/api/nwr/pick" : "/api/pick";
  try {
    const res = await fetch(
      `${endpoint}?x=${wx.toFixed(1)}&y=${wy.toFixed(1)}&radius=${radius.toFixed(1)}`
    );
    if (!res.ok) return;
    const data = await res.json();
    const byKey = new Map(STATE.hoverCandidates.map((c) => [`${c.type}:${c.id}`, c]));
    let updated = false;
    for (const c of data.candidates || []) {
      const key = `${c.type}:${c.id}`;
      const existing = byKey.get(key);
      if (!existing) {
        const candObj = {
          type: c.type,
          id: c.id,
          title: c.label,
          sub: `Database topology match at cursor`,
          x: c.x,
          y: c.y,
          previewWay: c.coords || null,
          previewParts: c.parts || null,
        };
        byKey.set(key, candObj);
        STATE.hoverCandidates.push(candObj);
        updated = true;
      } else {
        if (!existing.previewWay && c.coords) {
          existing.previewWay = c.coords;
          updated = true;
        }
        if (!existing.previewParts && c.parts) {
          existing.previewParts = c.parts;
          updated = true;
        }
      }
    }
    if (updated) {
      STATE.hoverCandidates.sort((a, b) => {
        const aPriority = a.type === "block" || a.type === "route" ? 0 : 1;
        const bPriority = b.type === "block" || b.type === "route" ? 0 : 1;
        return aPriority - bPriority;
      });
      if (!STATE.isPointerInMenu) {
        STATE.hoverPreview = STATE.hoverCandidates[0];
      }
      if (!hoverTooltip.classList.contains("hidden")) {
        renderHoverMenu(STATE.hoverCandidates, sx, sy, false);
        requestRender();
      }
    }
  } catch (err) {
    // Ignore transient network errors
  }
}

/* Click Picking & Asset Inspection */
async function handleCanvasClick(wx, wy) {
  if (STATE.hoverCandidates.length > 0) {
    const cands = [...STATE.hoverCandidates];
    const candBar = document.getElementById("pick-candidates");
    if (cands.length > 1) {
      candBar.innerHTML =
        `<span>Overlapping at click (${cands.length}):</span>` +
        cands
          .slice(0, 8)
          .map(
            (c, i) =>
              `<button class="candidate-chip ${i === 0 ? "active" : ""}" data-idx="${i}" data-type="${c.type}" data-id="${c.id}">${escapeHtml(c.title)}</button>`
          )
          .join("");
      candBar.classList.remove("hidden");
      candBar.querySelectorAll(".candidate-chip").forEach((btn) => {
        const idx = parseInt(btn.dataset.idx, 10);
        btn.addEventListener("mouseenter", () => {
          if (cands[idx]) {
            STATE.hoverPreview = cands[idx];
            requestRender();
          }
        });
        btn.addEventListener("mouseleave", () => {
          if (STATE.hoverPreview === cands[idx]) {
            STATE.hoverPreview = null;
            requestRender();
          }
        });
        btn.addEventListener("click", () => {
          candBar.querySelectorAll(".candidate-chip").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          loadAssetDetails(btn.dataset.type, btn.dataset.id, false);
        });
      });
    } else {
      candBar.classList.add("hidden");
    }
    const target = STATE.hoverPreview || cands[0];
    await loadAssetDetails(target.type, target.id, false);
    return;
  }

  const radius = Math.max(12, 18 / STATE.zoom);
  const endpoint = STATE.activeView === "nwr" ? "/api/nwr/pick" : "/api/pick";
  try {
    const res = await fetch(
      `${endpoint}?x=${wx.toFixed(1)}&y=${wy.toFixed(1)}&radius=${radius.toFixed(1)}`
    );
    if (!res.ok) return;
    const data = await res.json();
    const cands = data.candidates || [];
    const candBar = document.getElementById("pick-candidates");

    if (!cands.length) {
      candBar.classList.add("hidden");
      return;
    }

    if (cands.length > 1) {
      candBar.innerHTML =
        `<span>Nearby at click:</span>` +
        cands
          .slice(0, 8)
          .map(
            (c, i) =>
              `<button class="candidate-chip ${i === 0 ? "active" : ""}" data-idx="${i}" data-type="${c.type}" data-id="${c.id}">${escapeHtml(c.label)}</button>`
          )
          .join("");
      candBar.classList.remove("hidden");
      candBar.querySelectorAll(".candidate-chip").forEach((btn) => {
        const idx = parseInt(btn.dataset.idx, 10);
        const item = cands[idx];
        const previewItem = item
          ? {
              type: item.type,
              id: item.id,
              title: item.label,
              x: item.x,
              y: item.y,
              previewWay: item.coords || null,
              previewParts: item.parts || null,
            }
          : null;
        btn.addEventListener("mouseenter", () => {
          if (previewItem) {
            STATE.hoverPreview = previewItem;
            requestRender();
          }
        });
        btn.addEventListener("mouseleave", () => {
          if (STATE.hoverPreview === previewItem) {
            STATE.hoverPreview = null;
            requestRender();
          }
        });
        btn.addEventListener("click", () => {
          candBar.querySelectorAll(".candidate-chip").forEach((b) => b.classList.remove("active"));
          btn.classList.add("active");
          loadAssetDetails(btn.dataset.type, btn.dataset.id, false);
        });
      });
    } else {
      candBar.classList.add("hidden");
    }

    await loadAssetDetails(cands[0].type, cands[0].id, false);
  } catch (err) {
    console.error(err);
  }
}

async function loadAssetDetails(assetType, assetId, shouldFly = true) {
  const isNwrAsset = String(assetType).startsWith("nwr_");
  const apiPrefix = isNwrAsset ? "/api/nwr/asset" : "/api/asset";
  try {
    const res = await fetch(`${apiPrefix}/${assetType}/${assetId}`);
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
          return data.asset_type === "nwr_elr" ? 0.04 : 0.35;
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
    requestRender();

    // Lazy-render Inspector tabs: only build DOM for the currently visible tab
    STATE.renderedInspectorTabs = {
      "tab-decoded": false,
      "tab-related": false,
      "tab-raw": false,
    };
    renderActiveInspectorTab();
  } catch (err) {
    console.error(err);
  }
}

function renderActiveInspectorTab() {
  const data = STATE.selectedAsset;
  if (!data) return;
  const activeBtn = document.querySelector(".inspector-tabs .tab-btn.active");
  const activeTabId = activeBtn ? activeBtn.dataset.tab : "tab-decoded";
  if (STATE.renderedInspectorTabs && STATE.renderedInspectorTabs[activeTabId]) {
    return;
  }
  if (!STATE.renderedInspectorTabs) {
    STATE.renderedInspectorTabs = {};
  }

  if (activeTabId === "tab-decoded") {
    const sigEnvelopeHtml = buildSignallingEnvelopeWidget(data);
    const tractionPhysicsHtml = buildTractionPhysicsWidget(data);
    const decodedEntries = Object.entries(data.decoded || {});
    document.getElementById("tab-decoded").innerHTML = `
      ${sigEnvelopeHtml}
      ${tractionPhysicsHtml}
      <table class="prop-table">
        <tbody>
          ${decodedEntries
            .map(
              ([k, v]) => `
            <tr>
              <th>${escapeHtml(k)}</th>
              <td>${escapeHtml(String(v ?? "—"))}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>
    `;
    STATE.renderedInspectorTabs["tab-decoded"] = true;
  } else if (activeTabId === "tab-related") {
    renderRelatedTab(data);
    STATE.renderedInspectorTabs["tab-related"] = true;
  } else if (activeTabId === "tab-raw") {
    renderRawTab(data, false);
    STATE.renderedInspectorTabs["tab-raw"] = true;
  }
}

window.expandFullRawJson = function () {
  if (STATE.selectedAsset) {
    renderRawTab(STATE.selectedAsset, true);
  }
};

function renderRawTab(data, showFull = false) {
  const attrsStr = JSON.stringify(data.raw_attrs, null, 2) || "{}";
  const childrenFullStr = JSON.stringify(data.raw_children, null, 2) || "[]";
  const MAX_PREVIEW = 12000;
  const isTruncated = !showFull && childrenFullStr.length > MAX_PREVIEW;
  const childrenDisplay = isTruncated
    ? childrenFullStr.slice(0, MAX_PREVIEW) + "\n... [Truncated for responsiveness]"
    : childrenFullStr;

  document.getElementById("tab-raw").innerHTML = `
    <div class="rel-section">
      <h4>Raw Source Attributes (JSONB)</h4>
      <pre class="json-viewer">${escapeHtml(attrsStr)}</pre>
    </div>
    <div class="rel-section">
      <h4>Nested Children / Geometry Metadata (JSONB)</h4>
      <pre class="json-viewer">${escapeHtml(childrenDisplay)}</pre>
      ${
        isTruncated
          ? `<button class="btn btn-sm btn-secondary" style="width:100%" onclick="expandFullRawJson()">
              Show Full Raw JSONB (${(childrenFullStr.length / 1024).toFixed(1)} KB)
            </button>`
          : ""
      }
    </div>
  `;
}

function renderRelatedTab(data) {
  const rel = data.related || {};
  const sections = [];

  // NWR Track Model Cross-Links to TPS Model
  if (String(data.asset_type).startsWith("nwr_")) {
    if (rel.start_node || rel.end_node) {
      sections.push(`
        <div class="rel-section">
          <h4>Connected NWR Track Nodes</h4>
          ${
            rel.start_node
              ? `<div class="rel-card" onclick="loadAssetDetails('nwr_node', ${rel.start_node.fid}, true)">
                  <div class="rel-card-top"><span>Start Node ${escapeHtml(rel.start_node.asset_id)}</span><span>Valency ${rel.start_node.valency}</span></div>
                  <div class="rel-card-sub">OSGB36 (${rel.start_node.x} E, ${rel.start_node.y} N)</div>
                </div>`
              : ""
          }
          ${
            rel.end_node
              ? `<div class="rel-card" onclick="loadAssetDetails('nwr_node', ${rel.end_node.fid}, true)">
                  <div class="rel-card-top"><span>End Node ${escapeHtml(rel.end_node.asset_id)}</span><span>Valency ${rel.end_node.valency}</span></div>
                  <div class="rel-card-sub">OSGB36 (${rel.end_node.x} E, ${rel.end_node.y} N)</div>
                </div>`
              : ""
          }
        </div>
      `);
    }

    if (rel.elr_record) {
      const er = rel.elr_record;
      sections.push(`
        <div class="rel-section">
          <h4>Parent NWR ELR Reference Corridor</h4>
          <div class="rel-card" onclick="loadAssetDetails('nwr_elr', ${er.fid}, true)">
            <div class="rel-card-top"><span>ELR ${escapeHtml(er.elr)}</span><span>${escapeHtml(er.asset_id)}</span></div>
            <div class="rel-card-sub">Mileage: ${Number(er.start_mile_yards).toFixed(4)} → ${Number(er.end_mile_yards).toFixed(4)} mi.yd</div>
          </div>
        </div>
      `);
    }

    if (rel.connected_centrelines && rel.connected_centrelines.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Connected NWR Track Centre Lines (${rel.connected_centrelines.length})</h4>
          ${rel.connected_centrelines
            .map(
              (c) => `
            <div class="rel-card" onclick="loadAssetDetails('nwr_centreline', ${c.fid}, true)">
              <div class="rel-card-top"><span>${escapeHtml(c.elr)} / ${escapeHtml(c.track_id)} (${escapeHtml(c.asset_id)})</span><span>${c.length_meters}m</span></div>
              <div class="rel-card-sub">${Number(c.start_mile_yards).toFixed(4)} → ${Number(c.end_mile_yards).toFixed(4)} mi.yd</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }

    if (rel.waymarks && rel.waymarks.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Nearby NWR Milepost Waymarks (${rel.waymarks.length})</h4>
          ${rel.waymarks
            .map(
              (wm) => `
            <div class="rel-card" onclick="loadAssetDetails('nwr_waymark', ${wm.fid}, true)">
              <div class="rel-card-top"><span>${escapeHtml(wm.elr)} @ ${wm.value} ${escapeHtml(wm.unit)}</span><span>${escapeHtml(wm.asset_id)}</span></div>
              <div class="rel-card-sub">${wm.meters} m from ELR zero</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }

    const tps = rel.tps_cross_links || {};
    if (tps.tps_stations && tps.tps_stations.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Cross-Model Link: TPS Stations on this ELR (${tps.tps_stations.length})</h4>
          ${tps.tps_stations
            .map(
              (s) => `
            <div class="rel-card" onclick="jumpToTpsAsset('station', ${s.station_id})">
              <div class="rel-card-top"><span>${escapeHtml(s.longname || s.abbrev)} (${escapeHtml(s.crscode || s.abbrev)})</span><span style="color:#38bdf8">Open in TPS View &rarr;</span></div>
              <div class="rel-card-sub">TPS Station #${s.station_id} • STANOX ${escapeHtml(s.stanox || "N/A")} • KM ${(s.km_value / 1000).toFixed(3)} km</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }

    if (tps.tps_attribute_sections && tps.tps_attribute_sections.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Cross-Model Link: TPS Attribute Sections (${tps.tps_attribute_sections.length})</h4>
          ${tps.tps_attribute_sections
            .map(
              (a) => `
            <div class="rel-card" onclick="jumpToTpsAsset('attributesection', ${a.pk})">
              <div class="rel-card-top"><span>${escapeHtml(a.description)} (${escapeHtml(a.short_description)})</span><span style="color:#38bdf8">Open in TPS View &rarr;</span></div>
              <div class="rel-card-sub">TPS Attribute Section ID #${a.id}</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
  } else if (data.asset_type === "station") {
    if (rel.routes && rel.routes.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Station Signalling Routes (${rel.routes.length})</h4>
          ${rel.routes
            .map(
              (r) => `
            <div class="rel-card" onclick="loadAssetDetails('route', ${r.pk}, true)">
              <div class="rel-card-top">
                <span style="color:#10b981">Route #${r.route_id}</span>
                <span>${r.node_count} nodes</span>
              </div>
              <div class="rel-card-sub">ROP: ${escapeHtml(r.rop_line_code_desc || "—")} • SA: ${escapeHtml(r.sa_line_code_desc || "—")}</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
    if (rel.boundary_signals && rel.boundary_signals.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Station Boundary Signals (${rel.boundary_signals.length})</h4>
          ${rel.boundary_signals
            .map(
              (b) => `
            <div class="rel-card" ${b.signal_pk ? `onclick="loadAssetDetails('signal', ${b.signal_pk}, true)"` : ""}>
              <div class="rel-card-top">
                <span>${escapeHtml(b.signal_name || "Signal #" + b.signal_id)}</span>
                <span>${escapeHtml(b.interlocking_desc || "")}</span>
              </div>
              <div class="rel-card-sub">Master Signal #${b.signal_id} • Boundary Type ${b.boundary_type_id}</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
    if (rel.tracks && rel.tracks.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Station Tracks &amp; Platforms (${rel.tracks.length})</h4>
          ${rel.tracks
            .map(
              (t) => `
            <div class="rel-card" onclick="loadAssetDetails('track', ${t.pk}, true)">
              <div class="rel-card-top">
                <span>${escapeHtml(t.name)} — ${escapeHtml(t.description || t.longname)}</span>
                <span>${escapeHtml(t.track_category_desc || "")}</span>
              </div>
              <div class="rel-card-sub">
                Track ID #${t.track_id} • ${t.node_count} way nodes • ROP: ${escapeHtml(t.rop_line_code_desc || "—")} • SA: ${escapeHtml(t.sa_line_code_desc || "—")}
              </div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
    if (rel.station_banks && rel.station_banks.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Timetable Planning Corridors / Station Banks (${rel.station_banks.length})</h4>
          ${rel.station_banks
            .map(
              (sb) => `
            <div class="rel-card" onclick="loadAssetDetails('stationbank', ${sb.bank_id}, true)">
              <div class="rel-card-top">
                <span style="color:#c4b5fd">Corridor ${escapeHtml(sb.code || "")}</span>
                <span style="color:#38bdf8">Illuminate Corridor &rarr;</span>
              </div>
              <div class="rel-card-sub">${escapeHtml(sb.description || "")}</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
  } else if (data.asset_type === "edge") {
    if (rel.blocks && rel.blocks.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Signalling Blocks Traversing Edge (${rel.blocks.length})</h4>
          ${rel.blocks
            .map(
              (b) => `
            <div class="rel-card" onclick="loadAssetDetails('block', ${b.pk}, true)">
              <div class="rel-card-top"><span style="color:#10b981">Block #${b.block_id}</span><span>Pre-sig ${b.pre_signal_distance}m</span></div>
              <div class="rel-card-sub">Sight distance: ${b.sight_distance}m • ATC: ${b.atc_flag}</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
    if (rel.routes && rel.routes.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Station Signalling Routes Traversing Edge (${rel.routes.length})</h4>
          ${rel.routes
            .map(
              (r) => `
            <div class="rel-card" onclick="loadAssetDetails('route', ${r.pk}, true)">
              <div class="rel-card-top"><span style="color:#10b981">Route #${r.route_id}</span><span>${escapeHtml(r.station_name || "")}</span></div>
              <div class="rel-card-sub">Station ID #${r.station_id || "—"}</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
    if (rel.velocity_profiles && rel.velocity_profiles.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Train Velocity Profile Overrides (${rel.velocity_profiles.length})</h4>
          <table class="prop-table">
            <tbody>
              ${rel.velocity_profiles
                .map(
                  (vp) => `
                <tr>
                  <th>${escapeHtml(vp.profile_name)}</th>
                  <td>${vp.vmax} km/h (${vp.vmax_mph} mph)</td>
                </tr>`
                )
                .join("")}
            </tbody>
          </table>
        </div>
      `);
    }
    if (rel.start_node || rel.end_node) {
      sections.push(`
        <div class="rel-section">
          <h4>Endpoint Track Nodes</h4>
          ${
            rel.start_node
              ? `<div class="rel-card" onclick="loadAssetDetails('node', ${rel.start_node.node_id}, true)">
                  <div class="rel-card-top"><span>Start Node #${rel.start_node.node_id}</span><span>(${rel.start_node.netx}, ${rel.start_node.nety})</span></div>
                  <div class="rel-card-sub">KM Value: ${rel.start_node.km_value}m • Angle: ${rel.start_node.angle}°</div>
                </div>`
              : ""
          }
          ${
            rel.end_node
              ? `<div class="rel-card" onclick="loadAssetDetails('node', ${rel.end_node.node_id}, true)">
                  <div class="rel-card-top"><span>End Node #${rel.end_node.node_id}</span><span>(${rel.end_node.netx}, ${rel.end_node.nety})</span></div>
                  <div class="rel-card-sub">KM Value: ${rel.end_node.km_value}m • Angle: ${rel.end_node.angle}°</div>
                </div>`
              : ""
          }
        </div>
      `);
    }
    if (rel.station_tracks && rel.station_tracks.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Station Platform / Running Tracks (${rel.station_tracks.length})</h4>
          ${rel.station_tracks
            .map(
              (t) => `
            <div class="rel-card" onclick="loadAssetDetails('track', ${t.pk}, true)">
              <div class="rel-card-top"><span>${escapeHtml(t.station_name || "")} — ${escapeHtml(t.name)}</span></div>
              <div class="rel-card-sub">${escapeHtml(t.description || "")}</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
    if (rel.attribute_sections && rel.attribute_sections.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Corridor Attribute Sections (${rel.attribute_sections.length})</h4>
          ${rel.attribute_sections
            .map(
              (a) => `
            <div class="rel-card" onclick="loadAssetDetails('attributesection', ${a.pk}, true)">
              <div class="rel-card-top"><span>${escapeHtml(a.description)} (${escapeHtml(a.short_description)})</span><span>ELR: ${escapeHtml(a.elr || "—")}</span></div>
              <div class="rel-card-sub">${(a.attributes || []).length} corridor attributes</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
  } else if (data.asset_type === "node") {
    if (rel.blocks && rel.blocks.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Signalling Blocks Through Node (${rel.blocks.length})</h4>
          ${rel.blocks
            .map(
              (b) => `
            <div class="rel-card" onclick="loadAssetDetails('block', ${b.pk}, true)">
              <div class="rel-card-top"><span style="color:#10b981">Block #${b.block_id}</span><span>Pre-sig ${b.pre_signal_distance}m</span></div>
              <div class="rel-card-sub">Sighting distance: ${b.sight_distance}m</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
    if (rel.signals_at_node && rel.signals_at_node.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Signals at Node (${rel.signals_at_node.length})</h4>
          ${rel.signals_at_node
            .map(
              (s) => `
            <div class="rel-card" onclick="loadAssetDetails('signal', ${s.pk}, true)">
              <div class="rel-card-top"><span>${escapeHtml(s.name || s.master_name)}</span><span>Master #${s.signal_id}</span></div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
    if (rel.connected_edges && rel.connected_edges.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Connected Track Edges (${rel.connected_edges.length})</h4>
          ${rel.connected_edges
            .map(
              (e) => `
            <div class="rel-card" onclick="loadAssetDetails('edge', ${e.pk}, true)">
              <div class="rel-card-top"><span>Edge #${e.edge_id} (${e.start_node_id} → ${e.end_node_id})</span><span>${e.length}m</span></div>
              <div class="rel-card-sub">Vmax: ${e.vmax} km/h • Grad: ${e.gradient}‰ • ${e.electrified ? "Electrified" : "Diesel"}</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
    if (rel.station_tracks && rel.station_tracks.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Station Tracks (${rel.station_tracks.length})</h4>
          ${rel.station_tracks
            .map(
              (t) => `
            <div class="rel-card" onclick="loadAssetDetails('track', ${t.pk}, true)">
              <div class="rel-card-top"><span>${escapeHtml(t.station_name || "")} — ${escapeHtml(t.name)}</span></div>
              <div class="rel-card-sub">${escapeHtml(t.description || "")}</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
  } else if (data.asset_type === "signal") {
    sections.push(buildSignallingEnvelopeWidget(data));

    if (rel.outgoing_blocks && rel.outgoing_blocks.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Protected Blocks in Advance (${rel.outgoing_blocks.length})</h4>
          ${rel.outgoing_blocks
            .map(
              (b) => `
            <div class="rel-card" onclick="loadAssetDetails('block', ${b.pk}, true)">
              <div class="rel-card-top">
                <span style="color:#10b981">Block #${b.block_id} (${b.shunting_flag ? "Shunt Route" : "Main Block"})</span>
                <span>${b.length_meters} m (${Math.round(b.length_meters / 0.9144)} yd)</span>
              </div>
              <div class="rel-card-sub">
                Pre-signal: ${b.pre_signal_distance}m • Sighting: ${b.sight_distance}m • ${b.turnout_count || 0} switch(es) • Vmax: ${b.min_vmax || "—"}–${b.max_vmax || "—"} km/h
              </div>
              ${
                b.exit_signals && b.exit_signals.length
                  ? `<div style="margin-top:5px;display:flex;flex-wrap:wrap;gap:4px">
                      ${b.exit_signals
                        .map(
                          (xs) =>
                            `<button class="candidate-chip" style="border-color:#10b981;color:#6ee7b7" onclick="event.stopPropagation();loadAssetDetails('signal', ${xs.pk}, true)">Next Signal: ${escapeHtml(
                              xs.name
                            )} &rarr;</button>`
                        )
                        .join("")}
                    </div>`
                  : ""
              }
            </div>`
            )
            .join("")}
        </div>
      `);
    }

    if (rel.approach_blocks && rel.approach_blocks.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Approach Blocks in Rear (${rel.approach_blocks.length})</h4>
          ${rel.approach_blocks
            .map(
              (b) => `
            <div class="rel-card" onclick="loadAssetDetails('block', ${b.pk}, true)">
              <div class="rel-card-top">
                <span style="color:#38bdf8">Approach Block #${b.block_id} (${b.shunting_flag ? "Shunt" : "Main"})</span>
                <span>${b.length_meters} m</span>
              </div>
              <div class="rel-card-sub">
                Sighting distance to this signal: ${b.sight_distance}m • Pre-signal (AWS): ${b.pre_signal_distance}m
              </div>
              ${
                b.entry_signals && b.entry_signals.length
                  ? `<div style="margin-top:5px;display:flex;flex-wrap:wrap;gap:4px">
                      ${b.entry_signals
                        .map(
                          (es) =>
                            `<button class="candidate-chip" style="border-color:#38bdf8;color:#7dd3fc" onclick="event.stopPropagation();loadAssetDetails('signal', ${es.pk}, true)">&larr; Rear Signal: ${escapeHtml(
                              es.name
                            )}</button>`
                        )
                        .join("")}
                    </div>`
                  : ""
              }
            </div>`
            )
            .join("")}
        </div>
      `);
    }

    if (rel.security_sections && rel.security_sections.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Safety Overlaps &amp; Flank Protection (${rel.security_sections.length})</h4>
          ${rel.security_sections
            .map(
              (ss) => `
            <div class="rel-card" onclick="setSignalHighlightFilter('${ss.group || "overlap_" + ss.index}')">
              <div class="rel-card-top">
                <span style="color:${ss.color || "#d946ef"}">Overlap #${ss.index} — ${ss.length_meters || 0}m (${ss.length_yards || 0} yd)</span>
                <span>Vmax ${ss.vmax} km/h (${ss.vmax_mph || 0} mph)</span>
              </div>
              <div class="rel-card-sub">
                ${(ss.nodes || []).length} overlap nodes • ${(ss.turnout_nodes || []).length} switch(es) locked • ${(ss.boundary_nodes || []).length} flank boundary node(s)
              </div>
              <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">
                ${(ss.nodes || [])
                  .map(
                    (n) =>
                      `<button class="candidate-chip" style="${
                        n.boundarynode
                          ? "border-color:#f43f5e;color:#fda4af"
                          : n.is_turnout
                          ? "border-color:#d946ef;color:#f5d0fe"
                          : ""
                      }" onclick="event.stopPropagation();loadAssetDetails('node', ${n.nodeid}, true)">#${n.nodeid}${
                        n.boundarynode ? " [Flank]" : n.is_turnout ? " [Pts]" : ""
                      }</button>`
                  )
                  .join("")}
              </div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }

    if (rel.protected_stations && rel.protected_stations.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Station Boundaries Protected (${rel.protected_stations.length})</h4>
          ${rel.protected_stations
            .map(
              (s) => `
            <div class="rel-card" onclick="loadAssetDetails('station', ${s.station_id}, true)">
              <div class="rel-card-top"><span>${escapeHtml(s.longname)} (${escapeHtml(s.abbrev)})</span><span>${escapeHtml(s.crscode || "")}</span></div>
              <div class="rel-card-sub">Boundary Role: ${escapeHtml(s.boundary_type_desc || "Type " + s.boundary_type_id)}</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }

    if (rel.nwr_cross_links && rel.nwr_cross_links.centrelines && rel.nwr_cross_links.centrelines.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Cross-Model Link: NWR Track Model (OSGB36) (${rel.nwr_cross_links.centrelines.length})</h4>
          ${rel.nwr_cross_links.centrelines
            .map(
              (c) => `
            <div class="rel-card" onclick="jumpToNwrAsset('nwr_centreline', ${c.fid})">
              <div class="rel-card-top"><span>${escapeHtml(c.elr)} / ${escapeHtml(c.track_id)} (${escapeHtml(c.asset_id)})</span><span style="color:#38bdf8">Open in OSGB36 View &rarr;</span></div>
              <div class="rel-card-sub">${escapeHtml(c.track_id_decoded)} • ${Number(c.start_mile_yards).toFixed(4)} → ${Number(c.end_mile_yards).toFixed(4)} mi.yd</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
  } else if (data.asset_type === "track") {
    if (rel.blocks && rel.blocks.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Signalling Blocks Traversing Track (${rel.blocks.length})</h4>
          ${rel.blocks
            .map(
              (b) => `
            <div class="rel-card" onclick="loadAssetDetails('block', ${b.pk}, true)">
              <div class="rel-card-top"><span style="color:#10b981">Block #${b.block_id} (${b.shunting_flag ? "Shunt" : "Main"})</span><span>Pre-sig ${b.pre_signal_distance}m</span></div>
              <div class="rel-card-sub">Sight distance: ${b.sight_distance}m • ATC: ${b.atc_flag}</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
    if (rel.routes && rel.routes.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Station Signalling Routes Traversing Track (${rel.routes.length})</h4>
          ${rel.routes
            .map(
              (r) => `
            <div class="rel-card" onclick="loadAssetDetails('route', ${r.pk}, true)">
              <div class="rel-card-top"><span style="color:#10b981">Route #${r.route_id}</span><span>Station #${r.station_id || "—"}</span></div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
  } else if (data.asset_type === "block") {
    sections.push(buildSignallingEnvelopeWidget(data));

    if (rel.entry_signals && rel.entry_signals.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Entry Signal(s) Governing Block (${rel.entry_signals.length})</h4>
          ${rel.entry_signals
            .map(
              (s) => `
            <div class="rel-card" onclick="loadAssetDetails('signal', ${s.pk}, true)">
              <div class="rel-card-top"><span style="color:#38bdf8">&larr; Entry Signal ${escapeHtml(s.name)}</span><span>Master #${s.signal_id}</span></div>
              <div class="rel-card-sub">${escapeHtml(s.interlocking_desc || "")}</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }

    if (rel.exit_signals && rel.exit_signals.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Exit Signal(s) at End of Block (${rel.exit_signals.length})</h4>
          ${rel.exit_signals
            .map(
              (s) => `
            <div class="rel-card" onclick="loadAssetDetails('signal', ${s.pk}, true)">
              <div class="rel-card-top"><span style="color:#f59e0b">Exit Signal ${escapeHtml(s.name)} &rarr;</span><span>Master #${s.signal_id}</span></div>
              <div class="rel-card-sub">${escapeHtml(s.interlocking_desc || "")}</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }

    if (rel.exit_overlaps && rel.exit_overlaps.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Safety Overlaps Beyond Exit Signal (${rel.exit_overlaps.length})</h4>
          ${rel.exit_overlaps
            .map(
              (ov) => `
            <div class="rel-card" onclick="setSignalHighlightFilter('overlap_${ov.index}')">
              <div class="rel-card-top"><span style="color:#d946ef">Overlap #${ov.index} (${escapeHtml(ov.signal_name)})</span><span>${ov.length_meters}m (${ov.length_yards} yd)</span></div>
              <div class="rel-card-sub">Release Vmax: ${ov.vmax} km/h • ${ov.node_count} overlap nodes</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }

    if (rel.turnout_nodes && rel.turnout_nodes.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Switches / Turnouts Traversed (${rel.turnout_nodes.length})</h4>
          <div style="display:flex;flex-wrap:wrap;gap:4px">
            ${rel.turnout_nodes
              .map(
                (t) =>
                  `<button class="candidate-chip" style="border-color:#a855f7;color:#e9d5ff" onclick="loadAssetDetails('node', ${t.node_id}, true)">Switch #${t.node_id} (deg ${t.degree})</button>`
              )
              .join("")}
          </div>
        </div>
      `);
    }
  } else if (data.asset_type === "attributesection") {
    if (rel.section_attributes && rel.section_attributes.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Section Attributes (${rel.section_attributes.length})</h4>
          <table class="prop-table">
            <tbody>
              ${rel.section_attributes
                .slice(0, 60)
                .map(
                  (sa) => `
                <tr>
                  <th>${escapeHtml(sa.type_name)}</th>
                  <td>Value: ${escapeHtml(String(sa.value))} • ${escapeHtml(sa.km_begin || "")} → ${escapeHtml(sa.km_end || "")}</td>
                </tr>`
                )
                .join("")}
            </tbody>
          </table>
        </div>
      `);
    }
  } else if (data.asset_type === "modeltrain" || data.asset_type === "tractionunit") {
    sections.push(buildTractionPhysicsWidget(data));

    if (rel.traction_units && rel.traction_units.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Traction Unit Consist &amp; Running Resistance (${rel.traction_units.length})</h4>
          ${rel.traction_units
            .map(
              (u) => `
            <div class="rel-card" onclick="loadAssetDetails('tractionunit', ${u.id}, false)">
              <div class="rel-card-top">
                <span style="color:#c4b5fd">#${u.seq} Traction Unit ${escapeHtml(u.name)} — ${escapeHtml(u.desc)}</span>
                <span>${escapeHtml(u.type_code)} (${escapeHtml(u.motor_desc)})</span>
              </div>
              <div class="rel-card-sub">
                ${u.length_m}m • ${u.weight_t}t • Max ${u.max_speed_kmh} km/h (${u.max_speed_mph} mph) • Starting Effort: ${u.max_force_kn} kN • Peak Power: ${u.max_power_kw} kW
              </div>
              <div class="rel-card-sub" style="margin-top:3px;color:#38bdf8">
                Davis Drag R(v) = ${u.coeffA} + ${u.coeffB}·v + ${u.coeffC}·v² N • Retardation: ${u.retardation} m/s²
              </div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }

    if (rel.coach) {
      const c = rel.coach;
      sections.push(`
        <div class="rel-section">
          <h4>Trailing Coaching Stock / Wagon Template (&lt;coach&gt;)</h4>
          <div class="rel-card" style="cursor:default">
            <div class="rel-card-top">
              <span>${escapeHtml(c.name)} — ${escapeHtml(c.desc)}</span>
              <span>Type: ${escapeHtml(c.coach_type)}</span>
            </div>
            <div class="rel-card-sub">
              Vehicle Length: ${c.unit_length_m}m • Vehicle Weight: ${c.unit_weight_t}t • Max Speed: ${c.max_speed_kmh} km/h
            </div>
          </div>
        </div>
      `);
    }

    if (rel.transitions && rel.transitions.length) {
      sections.push(`
        <div class="rel-section">
          <h4>En-Route Timing Load Transitions (&lt;mtchangetransiting&gt;) (${rel.transitions.length})</h4>
          ${rel.transitions
            .map(
              (tr) => `
            <div class="rel-card" onclick="loadAssetDetails('modeltrain', ${
              tr.from_id == data.id ? tr.to_id : tr.from_id
            }, false)">
              <div class="rel-card-top">
                <span>${escapeHtml(tr.from_name)} &harr; ${escapeHtml(tr.to_name)}</span>
                <span style="color:#38bdf8">Inspect Linked Load &rarr;</span>
              </div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }

    if (rel.model_trains && rel.model_trains.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Timing Loads (&lt;modeltrain&gt;) Powered by This Traction Unit (${rel.model_trains.length})</h4>
          ${rel.model_trains
            .map(
              (mt) => `
            <div class="rel-card" onclick="loadAssetDetails('modeltrain', ${mt.id}, false)">
              <div class="rel-card-top">
                <span style="color:#38bdf8">${escapeHtml(mt.name)} (${escapeHtml(mt.publicname)})</span>
                <span>${mt.maxspeed_kmh} km/h (${mt.maxspeed_mph} mph)</span>
              </div>
              <div class="rel-card-sub">
                ${escapeHtml(mt.desc)} • Profile: ${escapeHtml(mt.profile_name)} • Trailing: ${mt.coaches_length_m}m / ${mt.coaches_weight_t}t
              </div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
  } else if (data.asset_type === "stationbank") {
    for (const sec of rel.corridor_sections || []) {
      sections.push(`
        <div class="rel-section">
          <h4>Corridor Section ${escapeHtml(sec.name)}: ${escapeHtml(sec.description)} (${(sec.stops || []).length} Timing Points)</h4>
          ${(sec.stops || [])
            .map(
              (s) => `
            <div class="rel-card" onclick="loadAssetDetails('station', ${s.station_id}, true)">
              <div class="rel-card-top">
                <span>#${s.seq}. ${escapeHtml(s.longname)} (${escapeHtml(s.crscode || s.abbrev)})</span>
                <span class="pill-tag ${s.timing_role.includes("Mandatory") ? "mandatory" : ""}">${escapeHtml(s.timing_role)}</span>
              </div>
              <div class="rel-card-sub">
                TIPLOC: ${escapeHtml(s.abbrev)} • ${escapeHtml(s.planning_category)} • ELR: ${escapeHtml(s.elr || "—")} ${escapeHtml(s.km_str || "")}
                ${s.layouts && s.layouts.length ? ` • Layout: ${escapeHtml(s.layouts.join(", "))}` : ""}
              </div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
  }

  if (rel.node_ids && rel.node_ids.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Ordered Node Sequence (${rel.node_ids.length} nodes)</h4>
        <div style="display:flex;flex-wrap:wrap;gap:4px">
          ${rel.node_ids
            .slice(0, 80)
            .map(
              (nid) =>
                `<button class="candidate-chip" onclick="loadAssetDetails('node', ${nid}, true)">#${nid}</button>`
            )
            .join("")}
        </div>
      </div>
    `);
  }

  const relContainer = document.getElementById("tab-related");
  relContainer.innerHTML =
    sections.join("") || `<p class="empty-state">No additional linked records.</p>`;
  applyLazyCardCollapse(relContainer);
}

function applyLazyCardCollapse(container, maxInitial = 12) {
  if (!container) return;
  container.querySelectorAll(".rel-section").forEach((sec) => {
    const cards = Array.from(sec.querySelectorAll(":scope > .rel-card"));
    if (cards.length > maxInitial) {
      for (let i = maxInitial; i < cards.length; i++) {
        cards[i].style.display = "none";
      }
      const moreCount = cards.length - maxInitial;
      const btn = document.createElement("button");
      btn.className = "btn btn-sm btn-secondary";
      btn.style.cssText = "width:100%;margin-top:4px;justify-content:center";
      btn.textContent = `Show all ${cards.length} items (+${moreCount} more)`;
      btn.addEventListener("click", () => {
        for (let i = maxInitial; i < cards.length; i++) {
          cards[i].style.display = "";
        }
        btn.remove();
      });
      sec.appendChild(btn);
    }
  });
}

/* Signalling & Interlocking Control Strip & Interactive Envelope Filter */
window.setSignalHighlightFilter = function (group) {
  STATE.highlightGroupFilter = group;
  document.querySelectorAll(".sig-filter-btn, .sig-filter-control").forEach((el) => {
    const g = el.dataset.group;
    if (group === "all") {
      el.classList.toggle("active", g === "all");
    } else if (group === "approach" || group.startsWith("approach:")) {
      el.classList.toggle("active", g === "approach");
    } else if (group === "outgoing" || group.startsWith("outgoing:")) {
      el.classList.toggle("active", g === "outgoing");
    } else if (group === "all_overlaps" || group === "overlaps" || group.startsWith("overlap_")) {
      el.classList.toggle("active", g === "overlaps");
    } else {
      el.classList.toggle("active", g === group);
    }
  });

  const appControl = document.querySelector('.sig-filter-control[data-group="approach"]');
  if (appControl) {
    const sel = appControl.querySelector(".sig-filter-select");
    const textSpan = appControl.querySelector(".sig-filter-text");
    if (sel) sel.value = group.startsWith("approach:") ? group : "approach";
    if (textSpan) {
      if (group.startsWith("approach:")) {
        const opt = sel ? sel.options[sel.selectedIndex] : null;
        textSpan.textContent = opt ? opt.dataset.short || `B#${group.slice(9)}` : `B#${group.slice(9)}`;
      } else {
        textSpan.textContent = textSpan.dataset.default || "Approach";
      }
    }
  }

  const outControl = document.querySelector('.sig-filter-control[data-group="outgoing"]');
  if (outControl) {
    const sel = outControl.querySelector(".sig-filter-select");
    const textSpan = outControl.querySelector(".sig-filter-text");
    if (sel) sel.value = group.startsWith("outgoing:") ? group : "outgoing";
    if (textSpan) {
      if (group.startsWith("outgoing:")) {
        const opt = sel ? sel.options[sel.selectedIndex] : null;
        textSpan.textContent = opt ? opt.dataset.short || `B#${group.slice(9)}` : `B#${group.slice(9)}`;
      } else {
        textSpan.textContent = textSpan.dataset.default || "Protected";
      }
    }
  }

  const ovControl = document.querySelector('.sig-filter-control[data-group="overlaps"]');
  if (ovControl) {
    const sel = ovControl.querySelector(".sig-filter-select");
    const textSpan = ovControl.querySelector(".sig-filter-text");
    if (sel) sel.value = group.startsWith("overlap_") ? group : "all_overlaps";
    if (textSpan) {
      if (group.startsWith("overlap_")) {
        const opt = sel ? sel.options[sel.selectedIndex] : null;
        textSpan.textContent = opt ? opt.dataset.short || `Ov #${group.slice(8)}` : `Ov #${group.slice(8)}`;
      } else {
        textSpan.textContent = textSpan.dataset.default || "Overlaps";
      }
    }
  }

  requestRender();
};

window.jumpToNwrAsset = async function (assetType, fid) {
  await switchView("nwr");
  await loadAssetDetails(assetType, fid, true);
};

function buildSignallingEnvelopeWidget(data) {
  if (!data || (data.asset_type !== "signal" && data.asset_type !== "block")) {
    return "";
  }
  const rel = data.related || {};

  if (data.asset_type === "signal") {
    const ilock = rel.interlocking_system || {};
    const appBlocks = rel.approach_blocks || [];
    const outBlocks = rel.outgoing_blocks || [];
    const overlaps = rel.security_sections || [];
    const aspects = Number(ilock.aspects || 0);

    let lampsHtml = "";
    if (aspects === 4) {
      lampsHtml = `
        <span class="sig-lamp" style="background:#ef4444"></span>
        <span class="sig-lamp" style="background:#eab308"></span>
        <span class="sig-lamp" style="background:#eab308"></span>
        <span class="sig-lamp" style="background:#10b981"></span>`;
    } else if (aspects === 3) {
      lampsHtml = `
        <span class="sig-lamp" style="background:#ef4444"></span>
        <span class="sig-lamp" style="background:#eab308"></span>
        <span class="sig-lamp" style="background:#10b981"></span>`;
    } else if (aspects === 2) {
      lampsHtml = `
        <span class="sig-lamp" style="background:#ef4444"></span>
        <span class="sig-lamp" style="background:#10b981"></span>`;
    } else {
      lampsHtml = `<span class="sig-lamp" style="background:#a855f7"></span>`;
    }

    const firstApp = appBlocks[0];
    const firstOut = outBlocks[0];
    const firstOv = overlaps[0];
    const curSigName = (data.title || "").replace(/^Signal\s+/i, "") || "Signal";

    // 1. Approach Routes Control
    const hasApp = appBlocks.length > 0;
    const isAppActive = STATE.highlightGroupFilter === "approach" || (STATE.highlightGroupFilter && STATE.highlightGroupFilter.startsWith("approach:"));
    const appControlHtml = `
      <div class="sig-filter-control ${!hasApp ? 'disabled' : ''} ${isAppActive ? 'active' : ''}" data-group="approach">
        <button class="sig-filter-btn-split" data-group="approach" ${hasApp ? "onclick=\"setSignalHighlightFilter('approach')\"" : "disabled"} title="${hasApp ? 'View all approach routes' : 'No approach blocks in database'}">
          <span class="sig-lamp" style="background:#38bdf8"></span>
          <span class="sig-filter-text" data-default="Approach (${appBlocks.length})">Approach (${appBlocks.length})</span>
        </button>
        <div class="sig-filter-select-wrap">
          <select class="sig-filter-select" data-parent-group="approach" ${hasApp ? 'onchange="setSignalHighlightFilter(this.value)"' : 'disabled'} title="Select specific approach route">
            <option value="approach" data-short="Approach (${appBlocks.length})">All Approach (${appBlocks.length})</option>
            ${appBlocks
              .map((b) => {
                const routeLabel = b.route_label || `To ${curSigName} (Block #${b.block_id})`;
                const shortLabel = `App B#${b.block_id}`;
                return `<option value="approach:${b.block_id}" data-short="${escapeHtml(shortLabel)}">${escapeHtml(routeLabel)} (B#${b.block_id} • ${b.length_meters}m)</option>`;
              })
              .join("")}
          </select>
          <span class="sig-select-arrow">&#9662;</span>
        </div>
      </div>
    `;

    // 2. Outgoing / Protected Blocks Control
    const hasOut = outBlocks.length > 0;
    const isOutActive = STATE.highlightGroupFilter === "outgoing" || (STATE.highlightGroupFilter && STATE.highlightGroupFilter.startsWith("outgoing:"));
    const outControlHtml = `
      <div class="sig-filter-control ${!hasOut ? 'disabled' : ''} ${isOutActive ? 'active' : ''}" data-group="outgoing">
        <button class="sig-filter-btn-split" data-group="outgoing" ${hasOut ? "onclick=\"setSignalHighlightFilter('outgoing')\"" : "disabled"} title="${hasOut ? 'View all protected blocks in advance' : 'No protected blocks ahead in database'}">
          <span class="sig-lamp" style="background:#10b981"></span>
          <span class="sig-filter-text" data-default="Protected (${outBlocks.length})">Protected (${outBlocks.length})</span>
        </button>
        <div class="sig-filter-select-wrap">
          <select class="sig-filter-select" data-parent-group="outgoing" ${hasOut ? 'onchange="setSignalHighlightFilter(this.value)"' : 'disabled'} title="Select specific protected block">
            <option value="outgoing" data-short="Protected (${outBlocks.length})">All Protected (${outBlocks.length})</option>
            ${outBlocks
              .map((b) => {
                const routeLabel = b.route_label || `From ${curSigName} (Block #${b.block_id})`;
                const shortLabel = `Prot B#${b.block_id}`;
                return `<option value="outgoing:${b.block_id}" data-short="${escapeHtml(shortLabel)}">${escapeHtml(routeLabel)} (B#${b.block_id} • ${b.length_meters}m)</option>`;
              })
              .join("")}
          </select>
          <span class="sig-select-arrow">&#9662;</span>
        </div>
      </div>
    `;

    // 3. Safety Overlaps Control
    const hasOv = overlaps.length > 0;
    const isOvActive = STATE.highlightGroupFilter === "all_overlaps" || STATE.highlightGroupFilter === "overlaps" || (STATE.highlightGroupFilter && STATE.highlightGroupFilter.startsWith("overlap_"));
    const ovControlHtml = `
      <div class="sig-filter-control ${!hasOv ? 'disabled' : ''} ${isOvActive ? 'active' : ''}" data-group="overlaps">
        <button class="sig-filter-btn-split" data-group="overlaps" ${hasOv ? "onclick=\"setSignalHighlightFilter('all_overlaps')\"" : "disabled"} title="${hasOv ? 'View all safety overlaps' : 'No safety overlaps defined'}">
          <span class="sig-lamp" style="background:#d946ef"></span>
          <span class="sig-filter-text" data-default="Overlaps (${overlaps.length})">Overlaps (${overlaps.length})</span>
        </button>
        <div class="sig-filter-select-wrap">
          <select class="sig-filter-select" data-parent-group="overlaps" ${hasOv ? 'onchange="setSignalHighlightFilter(this.value)"' : 'disabled'} title="Select specific safety overlap">
            <option value="all_overlaps" data-short="Overlaps (${overlaps.length})">All Overlaps (${overlaps.length})</option>
            ${overlaps
              .map((ov) => {
                const shortLabel = `Overlap #${ov.index}`;
                return `<option value="${ov.group}" data-short="${escapeHtml(shortLabel)}">Overlap #${ov.index} (${ov.length_meters}m • ${ov.vmax} km/h)</option>`;
              })
              .join("")}
          </select>
          <span class="sig-select-arrow">&#9662;</span>
        </div>
      </div>
    `;

    return `
      <div class="sig-envelope-card">
        <div class="sig-envelope-header">
          <span class="sig-envelope-title">Signalling &amp; Interlocking Envelope</span>
          <span class="sig-aspect-pill">
            ${lampsHtml}
            <span>${escapeHtml(ilock.display_name || "Signal")}</span>
          </span>
        </div>

        <div class="sig-strip-diagram">
          <div class="sig-strip-box approach" onclick="setSignalHighlightFilter('approach')" title="Click to view approach envelope">
            <div class="sig-strip-label">In Rear (Approach)</div>
            <div class="sig-strip-val">${
              firstApp ? (firstApp.route_label ? escapeHtml(firstApp.route_label) : `Block #${firstApp.block_id} (${firstApp.length_meters}m)`) : "None"
            }</div>
            <div class="sig-strip-sub">${
              firstApp
                ? `Sight: ${firstApp.sight_distance}m • AWS: ${firstApp.pre_signal_distance}m`
                : `${appBlocks.length} blocks`
            }</div>
          </div>

          <div class="sig-strip-box current" onclick="setSignalHighlightFilter('all')" title="Click to view all envelope">
            <div class="sig-strip-label">Selected Signal</div>
            <div class="sig-strip-val">${escapeHtml(data.title.replace("Signal ", ""))}</div>
            <div class="sig-strip-sub">Set ${ilock.settime || 0}s • Free ${ilock.freeingtime || 0}s</div>
          </div>

          <div class="sig-strip-box outgoing" onclick="setSignalHighlightFilter('outgoing')" title="Click to view protected block envelope">
            <div class="sig-strip-label">In Advance (Block)</div>
            <div class="sig-strip-val">${
              firstOut ? (firstOut.route_label ? escapeHtml(firstOut.route_label) : `Block #${firstOut.block_id} (${firstOut.length_meters}m)`) : "None"
            }</div>
            <div class="sig-strip-sub">${outBlocks.length} protected route(s)</div>
          </div>

          <div class="sig-strip-box overlap" onclick="setSignalHighlightFilter('${firstOv ? firstOv.group : 'all'}')" title="Click to view safety overlap">
            <div class="sig-strip-label">Safety Overlap</div>
            <div class="sig-strip-val">${
              firstOv ? `${firstOv.length_meters}m (${firstOv.length_yards}yd)` : "None"
            }</div>
            <div class="sig-strip-sub">${overlaps.length} swinging overlap(s)</div>
          </div>
        </div>

        <div class="sig-filter-bar">
          <button class="sig-filter-btn ${STATE.highlightGroupFilter === 'all' ? 'active' : ''}" data-group="all" onclick="setSignalHighlightFilter('all')">
            All Envelope
          </button>
          ${appControlHtml}
          ${outControlHtml}
          ${ovControlHtml}
        </div>
      </div>
    `;
  }

  // Block envelope widget
  const entrySigs = rel.entry_signals || [];
  const exitSigs = rel.exit_signals || [];
  const exitOvs = rel.exit_overlaps || [];
  return `
    <div class="sig-envelope-card">
      <div class="sig-envelope-header">
        <span class="sig-envelope-title">Block Section &amp; Overlap Envelope</span>
        <span class="sig-aspect-pill">
          <span class="sig-lamp" style="background:#10b981"></span>
          <span>${rel.length_meters || 0} m (${Math.round((rel.length_meters || 0) / 0.9144)} yd)</span>
        </span>
      </div>
      <div class="sig-strip-diagram">
        <div class="sig-strip-box approach">
          <div class="sig-strip-label">Entry Signal</div>
          <div class="sig-strip-val">${
            entrySigs[0] ? escapeHtml(entrySigs[0].name) : "—"
          }</div>
          <div class="sig-strip-sub">${entrySigs.length} entry signal(s)</div>
        </div>
        <div class="sig-strip-box outgoing">
          <div class="sig-strip-label">Block Section</div>
          <div class="sig-strip-val">${escapeHtml(data.title)}</div>
          <div class="sig-strip-sub">${(rel.turnout_nodes || []).length} switch(es)</div>
        </div>
        <div class="sig-strip-box current">
          <div class="sig-strip-label">Exit Signal</div>
          <div class="sig-strip-val">${
            exitSigs[0] ? escapeHtml(exitSigs[0].name) : "—"
          }</div>
          <div class="sig-strip-sub">${exitOvs.length} overlap(s) ahead</div>
        </div>
      </div>
      <div class="sig-filter-bar">
        <button class="sig-filter-btn active" data-group="all" onclick="setSignalHighlightFilter('all')">
          All Block &amp; Overlaps
        </button>
        <button class="sig-filter-btn" data-group="outgoing" onclick="setSignalHighlightFilter('outgoing')">
          <span class="sig-lamp" style="background:#10b981"></span>
          Block Only
        </button>
        ${exitOvs
          .map(
            (ov) => `
          <button class="sig-filter-btn" data-group="overlap_${ov.index}" onclick="setSignalHighlightFilter('overlap_${ov.index}')">
            <span class="sig-lamp" style="background:#d946ef"></span>
            Exit Overlap #${ov.index} (${ov.length_meters}m)
          </button>`
          )
          .join("")}
      </div>
    </div>
  `;
}

/* Global Search */
const searchInput = document.getElementById("global-search");
const searchDropdown = document.getElementById("search-results");
let searchTimer = null;
let searchAbort = null;

searchInput.addEventListener("input", () => {
  if (searchTimer) clearTimeout(searchTimer);
  const q = searchInput.value.trim();
  if (!q) {
    if (searchAbort) searchAbort.abort();
    searchDropdown.classList.add("hidden");
    return;
  }
  searchTimer = setTimeout(async () => {
    if (searchAbort) searchAbort.abort();
    searchAbort = new AbortController();
    const endpoint = STATE.activeView === "nwr" ? "/api/nwr/search" : "/api/search";
    try {
      const res = await fetch(`${endpoint}?q=${encodeURIComponent(q)}`, {
        signal: searchAbort.signal,
      });
      if (!res.ok) return;
      const data = await res.json();
      const results = data.results || [];
      if (!results.length) {
        searchDropdown.innerHTML = `<div class="search-item"><span class="search-item-sub">No matching assets found</span></div>`;
      } else {
        searchDropdown.innerHTML = results
          .map(
            (r) => `
          <div class="search-item" data-type="${r.type}" data-id="${r.id}">
            <div>
              <div class="search-item-title">${escapeHtml(r.title)}</div>
              <div class="search-item-sub">${escapeHtml(r.subtitle || "")}</div>
            </div>
            <span class="search-type-pill">${escapeHtml(r.type.replace("nwr_", ""))}</span>
          </div>`
          )
          .join("");
        searchDropdown.querySelectorAll(".search-item[data-type]").forEach((el) => {
          el.addEventListener("click", () => {
            searchDropdown.classList.add("hidden");
            loadAssetDetails(el.dataset.type, el.dataset.id, true);
          });
        });
      }
      searchDropdown.classList.remove("hidden");
    } catch (e) {
      if (e.name !== "AbortError") console.error(e);
    }
  }, 200);
});

document.addEventListener("click", (e) => {
  if (!e.target.closest(".search-container")) {
    searchDropdown.classList.add("hidden");
  }
});

/* Controls & Tabs Wiring */
document.getElementById("color-mode").addEventListener("change", (e) => {
  STATE.colorMode = e.target.value;
  updateLegend();
  scheduleViewportFetch();
  requestRender();
});

document.getElementById("nwr-color-mode").addEventListener("change", (e) => {
  STATE.nwrColorMode = e.target.value;
  updateLegend();
  requestRender();
});

for (const key of ["edges", "tracks", "vectors", "texts", "stations", "signals", "blocks", "nodes", "polys"]) {
  const el = document.getElementById(`layer-${key}`);
  if (el) {
    el.addEventListener("change", () => {
      STATE.layers[key] = el.checked;
      scheduleViewportFetch();
      requestRender();
    });
  }
}

for (const key of ["centrelines", "elrs", "nodes", "waymarks"]) {
  const el = document.getElementById(`nwr-layer-${key}`);
  if (el) {
    el.addEventListener("change", () => {
      STATE.nwrLayers[key] = el.checked;
      scheduleViewportFetch();
      requestRender();
    });
  }
}

/* ============================================================================
   LABEL SIZE & ORIENTATION CONTROLS (TPS SCHEMATIC & NWR GEOGRAPHIC)
   ============================================================================ */
function updateLabelUi() {
  const masterSlider = document.getElementById("master-label-slider");
  if (masterSlider) {
    masterSlider.value = Math.round((STATE.labelScale.all || 1.0) * 100);
  }
  const masterOrient = document.getElementById("master-label-orient");
  if (masterOrient) {
    masterOrient.value = STATE.labelOrientation.all !== undefined ? String(STATE.labelOrientation.all) : "";
  }
  document.querySelectorAll(".label-stepper").forEach((stepper) => {
    const layer = stepper.dataset.layer;
    const readout = stepper.querySelector(".lbl-scale-readout");
    if (!readout) return;
    const val = layer === "all" ? STATE.labelScale.all : STATE.labelScale[layer];
    readout.textContent = `${Math.round((val !== undefined ? val : 1.0) * 100)}%`;
  });
  document.querySelectorAll(".lbl-orient-select").forEach((sel) => {
    const layer = sel.dataset.layer;
    if (!layer || layer === "all") return;
    const val = STATE.labelOrientation[layer];
    sel.value = val !== undefined ? String(val) : "0";
  });
}

document.querySelectorAll(".label-stepper .lbl-step-btn").forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const stepper = btn.closest(".label-stepper");
    const layer = stepper.dataset.layer;
    const dir = parseInt(btn.dataset.dir, 10) || 0;
    const current = (layer === "all" ? STATE.labelScale.all : STATE.labelScale[layer]) || 1.0;
    const next = Math.max(0.4, Math.min(3.0, Math.round((current + dir * 0.1) * 10) / 10));
    if (layer === "all") {
      STATE.labelScale.all = next;
    } else {
      STATE.labelScale[layer] = next;
    }
    updateLabelUi();
    requestRender();
  });
});

document.querySelectorAll(".label-stepper .lbl-scale-readout").forEach((readout) => {
  readout.addEventListener("dblclick", (e) => {
    e.stopPropagation();
    const stepper = readout.closest(".label-stepper");
    const layer = stepper.dataset.layer;
    if (layer === "all") {
      STATE.labelScale.all = 1.0;
    } else {
      STATE.labelScale[layer] = 1.0;
    }
    updateLabelUi();
    requestRender();
  });
});

const masterLabelSlider = document.getElementById("master-label-slider");
if (masterLabelSlider) {
  masterLabelSlider.addEventListener("input", (e) => {
    STATE.labelScale.all = Math.max(0.5, Math.min(3.0, Number(e.target.value) / 100));
    updateLabelUi();
    requestRender();
  });
}

const masterOrientSel = document.getElementById("master-label-orient");
if (masterOrientSel) {
  masterOrientSel.addEventListener("change", (e) => {
    STATE.labelOrientation.all = e.target.value;
    updateLabelUi();
    requestRender();
  });
}

document.querySelectorAll(".lbl-orient-select").forEach((sel) => {
  const layer = sel.dataset.layer;
  if (!layer || layer === "all") return;
  sel.addEventListener("change", (e) => {
    STATE.labelOrientation[layer] = e.target.value === "native" ? "native" : Number(e.target.value);
    updateLabelUi();
    requestRender();
  });
});

const btnResetLabels = document.getElementById("btn-reset-labels");
if (btnResetLabels) {
  btnResetLabels.addEventListener("click", () => {
    STATE.labelScale = {
      all: 1.0,
      texts: 1.0,
      stations: 1.0,
      signals: 1.0,
      blocks: 1.0,
      nodes: 1.0,
      polys: 1.0,
      nwr_elrs: 1.0,
      nwr_waymarks: 1.0,
      nwr_nodes: 1.0,
    };
    STATE.labelOrientation = {
      all: "",
      texts: "native",
      stations: 0,
      signals: 0,
      blocks: 0,
      nodes: 0,
      polys: 0,
      nwr_elrs: 0,
      nwr_waymarks: 0,
      nwr_nodes: 0,
    };
    updateLabelUi();
    requestRender();
  });
}

document.getElementById("btn-fit-uk").addEventListener("click", fitUKNetwork);
document.getElementById("btn-zoom-in").addEventListener("click", () => {
  const maxZ = STATE.activeView === "nwr" ? 5.0 : 25.0;
  STATE.zoom = Math.min(maxZ, STATE.zoom * 1.45);
  scheduleViewportFetch();
  requestRender();
});
document.getElementById("btn-zoom-out").addEventListener("click", () => {
  const minZ = STATE.activeView === "nwr" ? 0.0004 : 0.008;
  STATE.zoom = Math.max(minZ, STATE.zoom / 1.45);
  scheduleViewportFetch();
  requestRender();
});
document.getElementById("btn-clear-highlight").addEventListener("click", () => {
  STATE.highlight = { ways: [], points: [], segments: [] };
  document.getElementById("btn-clear-highlight").classList.add("hidden");
  requestRender();
});

document.querySelectorAll(".tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-pane").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(btn.dataset.tab).classList.add("active");
    renderActiveInspectorTab();
  });
});

document.querySelectorAll("#quick-hubs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    const crs = btn.dataset.crs;
    if (!STATE.overview) return;
    const st = STATE.overview.stations.find((s) => s.crs === crs);
    if (st) {
      loadAssetDetails("station", st.id, true);
    }
  });
});

document.querySelectorAll("#nwr-quick-elrs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    const elrCode = btn.dataset.elr;
    if (!STATE.nwrOverview) return;
    const elr = STATE.nwrOverview.elrs.find((e) => e.elr === elrCode);
    if (elr) {
      loadAssetDetails("nwr_elr", elr.fid, true);
    }
  });
});

/* Full Database Catalog Modal */
const catalogModal = document.getElementById("catalog-modal");
document.getElementById("btn-catalog").addEventListener("click", async () => {
  catalogModal.classList.remove("hidden");
  const res = await fetch("/api/catalog");
  if (res.ok) {
    const data = await res.json();
    STATE.catalogTags = data.tags || [];
    renderCatalogTagList("");
    if (STATE.catalogTags.length && !STATE.catalogCurrentTag) {
      selectCatalogTag(STATE.catalogTags[0].tag);
    }
  }
});

document.getElementById("btn-close-catalog").addEventListener("click", () => {
  catalogModal.classList.add("hidden");
});

document.getElementById("catalog-tag-filter").addEventListener("input", (e) => {
  renderCatalogTagList(e.target.value.trim().toLowerCase());
});

function renderCatalogTagList(filter) {
  const listEl = document.getElementById("catalog-tag-list");
  const filtered = STATE.catalogTags.filter((t) =>
    t.tag.toLowerCase().includes(filter)
  );
  listEl.innerHTML = filtered
    .map(
      (t) => `
    <div class="catalog-tag-item ${STATE.catalogCurrentTag === t.tag ? "active" : ""}" data-tag="${t.tag}">
      <span>${t.tag.startsWith("nwr_") ? t.tag : "&lt;" + t.tag + "&gt;"}</span>
      <strong>${t.count.toLocaleString()}</strong>
    </div>`
    )
    .join("");
  listEl.querySelectorAll(".catalog-tag-item").forEach((el) => {
    el.addEventListener("click", () => selectCatalogTag(el.dataset.tag));
  });
}

async function selectCatalogTag(tag) {
  STATE.catalogCurrentTag = tag;
  STATE.catalogOffset = 0;
  renderCatalogTagList(document.getElementById("catalog-tag-filter").value.trim().toLowerCase());
  await fetchCatalogRows();
}

async function fetchCatalogRows() {
  const tag = STATE.catalogCurrentTag;
  if (!tag) return;
  const q = document.getElementById("catalog-row-search").value.trim();
  document.getElementById("catalog-current-tag").textContent = tag.startsWith("nwr_") ? tag : `<${tag}>`;
  document.getElementById("catalog-page-info").textContent = `${STATE.catalogOffset + 1}–${STATE.catalogOffset + STATE.catalogLimit}`;

  const res = await fetch(
    `/api/catalog/${encodeURIComponent(tag)}?q=${encodeURIComponent(q)}&limit=${STATE.catalogLimit}&offset=${STATE.catalogOffset}`
  );
  if (!res.ok) return;
  const data = await res.json();
  const container = document.getElementById("catalog-rows-container");
  if (!data.rows.length) {
    container.innerHTML = `<p class="empty-state">No records found.</p>`;
    return;
  }
  container.innerHTML = data.rows
    .map(
      (r) => `
    <div class="rel-card" style="cursor:default">
      <div class="rel-card-top">
        <span>ID: ${escapeHtml(String(r.entity_id || "—"))}</span>
        <span>${escapeHtml(String(r.name || ""))}</span>
      </div>
      <pre class="json-viewer" style="margin-top:6px;margin-bottom:0">${escapeHtml(
        JSON.stringify({ attrs: r.raw_attrs, children: r.raw_children }, null, 2)
      )}</pre>
    </div>`
    )
    .join("");
}

let catalogSearchTimer = null;
document.getElementById("catalog-row-search").addEventListener("input", () => {
  if (catalogSearchTimer) clearTimeout(catalogSearchTimer);
  catalogSearchTimer = setTimeout(() => {
    STATE.catalogOffset = 0;
    fetchCatalogRows();
  }, 200);
});
document.getElementById("catalog-prev").addEventListener("click", () => {
  STATE.catalogOffset = Math.max(0, STATE.catalogOffset - STATE.catalogLimit);
  fetchCatalogRows();
});
document.getElementById("catalog-next").addEventListener("click", () => {
  STATE.catalogOffset += STATE.catalogLimit;
  fetchCatalogRows();
});

/* ============================================================================
   TIMING LOADS, TRACTION PHYSICS CURVES & TIMETABLE CORRIDORS EXPLORER
   ============================================================================ */
let ACTIVE_TIMING_TAB = "model_trains";
let TIMING_OFFSET = 0;
const TIMING_LIMIT = 40;
let TIMING_TOTAL = 0;
let timingFilterTimer = null;
let timingAbort = null;

function buildTractionPhysicsWidget(data) {
  if (!data || (data.asset_type !== "modeltrain" && data.asset_type !== "tractionunit")) {
    return "";
  }
  const rel = data.related || {};
  const units = rel.traction_units || [];
  if (!units.length) return "";

  const u = units[0];
  const diag = (u.fs_diagrams || [])[0];
  const sr = u.sr_curve || [];
  const fPts = diag ? diag.points || [] : [];
  if (!fPts.length && !sr.length) return "";

  const W = 390;
  const H = 195;
  const padL = 38;
  const padR = 38;
  const padT = 16;
  const padB = 28;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const maxSpd = Math.max(
    100,
    u.max_speed_kmh || 0,
    ...fPts.map((p) => p.speed_kmh),
    ...sr.map((p) => p.speed_kmh)
  );
  const maxForce = Math.max(
    50,
    ...fPts.map((p) => p.force_kn),
    ...sr.map((p) => p.resistance_kn * 4)
  );
  const maxPower = Math.max(500, ...fPts.map((p) => p.power_kw || 0));

  const xOf = (spd) => padL + (spd / maxSpd) * plotW;
  const yForce = (f) => padT + plotH - Math.min(1, f / maxForce) * plotH;
  const yPower = (p) => padT + plotH - Math.min(1, p / maxPower) * plotH;

  const forcePath = fPts
    .map((p, i) => `${i === 0 ? "M" : "L"}${xOf(p.speed_kmh).toFixed(1)},${yForce(p.force_kn).toFixed(1)}`)
    .join(" ");
  const powerPath = fPts
    .map((p, i) => `${i === 0 ? "M" : "L"}${xOf(p.speed_kmh).toFixed(1)},${yPower(p.power_kw).toFixed(1)}`)
    .join(" ");
  const resPath = sr
    .map((p, i) => `${i === 0 ? "M" : "L"}${xOf(p.speed_kmh).toFixed(1)},${yForce(p.resistance_kn).toFixed(1)}`)
    .join(" ");

  const xTicks = [0, 0.25, 0.5, 0.75, 1.0].map((t) => Math.round(maxSpd * t));
  const yTicks = [0, 0.5, 1.0].map((t) => Math.round(maxForce * t));

  return `
    <div class="traction-curve-card">
      <div class="traction-curve-header">
        <span class="traction-curve-title">Tractive Effort &amp; Davis Resistance (${escapeHtml(u.name)})</span>
        <div class="traction-curve-legend">
          <span style="color:#22d3ee">&#9644; Force (${u.max_force_kn} kN)</span>
          <span style="color:#a855f7">-- Power (${u.max_power_kw} kW)</span>
          ${sr.length ? `<span style="color:#f43f5e">&#9644; Drag R(v)</span>` : ""}
        </div>
      </div>
      <svg viewBox="0 0 ${W} ${H}" class="traction-curve-svg">
        <!-- Grid lines -->
        ${yTicks
          .map(
            (yv) => `
          <line x1="${padL}" y1="${yForce(yv)}" x2="${W - padR}" y2="${yForce(yv)}" stroke="#1e293b" stroke-width="1" />
          <text x="${padL - 5}" y="${yForce(yv) + 3}" fill="#94a3b8" font-size="8.5" text-anchor="end">${yv}kN</text>`
          )
          .join("")}
        <text x="${W - padR + 4}" y="${padT + 6}" fill="#c4b5fd" font-size="8.5">${Math.round(maxPower)}kW</text>
        <text x="${W - padR + 4}" y="${padT + plotH / 2 + 3}" fill="#c4b5fd" font-size="8.5">${Math.round(maxPower / 2)}kW</text>
        ${xTicks
          .map(
            (xv) => `
          <line x1="${xOf(xv)}" y1="${padT}" x2="${xOf(xv)}" y2="${padT + plotH}" stroke="#1e293b" stroke-width="1" />
          <text x="${xOf(xv)}" y="${H - 12}" fill="#94a3b8" font-size="8.5" text-anchor="middle">${xv}</text>
          <text x="${xOf(xv)}" y="${H - 3}" fill="#64748b" font-size="7.5" text-anchor="middle">(${Math.round(xv * 0.621371)}mph)</text>`
          )
          .join("")}
        <!-- Curves -->
        ${powerPath ? `<path d="${powerPath}" fill="none" stroke="#a855f7" stroke-width="1.6" stroke-dasharray="4 3" />` : ""}
        ${resPath ? `<path d="${resPath}" fill="none" stroke="#f43f5e" stroke-width="1.8" />` : ""}
        ${forcePath ? `<path d="${forcePath}" fill="none" stroke="#22d3ee" stroke-width="2.3" />` : ""}
        ${fPts
          .map(
            (p) =>
              `<circle cx="${xOf(p.speed_kmh).toFixed(1)}" cy="${yForce(p.force_kn).toFixed(1)}" r="2.2" fill="#38bdf8"><title>${p.speed_kmh} km/h (${p.speed_mph} mph): ${p.force_kn} kN / ${p.power_kw} kW</title></circle>`
          )
          .join("")}
      </svg>
      <div style="display:flex;justify-content:space-between;font-size:0.7rem;color:#94a3b8;margin-top:5px">
        <span>Davis R(v) = ${u.coeffA} + ${u.coeffB}·v + ${u.coeffC}·v² N</span>
        <span>Braking: ${u.retardation} m/s² • Mass: ${u.weight_t}t (${u.length_m}m)</span>
      </div>
    </div>
  `;
}

const timingModal = document.getElementById("timing-modal");
document.getElementById("btn-timing-loads").addEventListener("click", () => {
  timingModal.classList.remove("hidden");
  TIMING_OFFSET = 0;
  fetchAndRenderTimingModalTab();
});

document.getElementById("btn-close-timing").addEventListener("click", () => {
  timingModal.classList.add("hidden");
});

document.querySelectorAll(".timing-tab-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".timing-tab-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    ACTIVE_TIMING_TAB = btn.dataset.ttab;
    TIMING_OFFSET = 0;
    fetchAndRenderTimingModalTab();
  });
});

document.getElementById("timing-filter-input").addEventListener("input", () => {
  if (timingFilterTimer) clearTimeout(timingFilterTimer);
  timingFilterTimer = setTimeout(() => {
    TIMING_OFFSET = 0;
    fetchAndRenderTimingModalTab();
  }, 180);
});

const timingPrevBtn = document.getElementById("timing-prev");
const timingNextBtn = document.getElementById("timing-next");
if (timingPrevBtn) {
  timingPrevBtn.addEventListener("click", () => {
    if (TIMING_OFFSET > 0) {
      TIMING_OFFSET = Math.max(0, TIMING_OFFSET - TIMING_LIMIT);
      fetchAndRenderTimingModalTab();
    }
  });
}
if (timingNextBtn) {
  timingNextBtn.addEventListener("click", () => {
    if (TIMING_OFFSET + TIMING_LIMIT < TIMING_TOTAL) {
      TIMING_OFFSET += TIMING_LIMIT;
      fetchAndRenderTimingModalTab();
    }
  });
}

window.inspectFromTimingModal = async function (assetType, assetId, fly = false) {
  timingModal.classList.add("hidden");
  if (fly) {
    await switchView("tps");
  }
  await loadAssetDetails(assetType, assetId, fly);
};

window.filterTimingLoadsByProfile = function (profId) {
  ACTIVE_TIMING_TAB = "model_trains";
  TIMING_OFFSET = 0;
  document.querySelectorAll(".timing-tab-btn").forEach((b) => {
    b.classList.toggle("active", b.dataset.ttab === "model_trains");
  });
  const inp = document.getElementById("timing-filter-input");
  inp.value = `profile:${profId}`;
  fetchAndRenderTimingModalTab();
};

async function fetchAndRenderTimingModalTab() {
  const container = document.getElementById("timing-modal-content");
  const rawFilter = document.getElementById("timing-filter-input").value.trim();

  if (timingAbort) timingAbort.abort();
  timingAbort = new AbortController();

  try {
    const res = await fetch(
      `/api/timing-loads/list?tab=${encodeURIComponent(ACTIVE_TIMING_TAB)}&q=${encodeURIComponent(rawFilter)}&limit=${TIMING_LIMIT}&offset=${TIMING_OFFSET}`,
      { signal: timingAbort.signal }
    );
    if (!res.ok) return;
    const data = await res.json();

    const c = data.counts || {};
    document.getElementById("tcnt-trains").textContent = (c.model_trains || 0).toLocaleString();
    document.getElementById("tcnt-units").textContent = (c.traction_units || 0).toLocaleString();
    document.getElementById("tcnt-profiles").textContent = `${c.velocity_profiles || 0} / ${c.coaches || 0}`;
    document.getElementById("tcnt-banks").textContent = (c.station_banks || 0).toLocaleString();

    TIMING_TOTAL = data.total || 0;
    const pagerEl = document.getElementById("timing-pager");
    const pageInfoEl = document.getElementById("timing-page-info");

    if (ACTIVE_TIMING_TAB === "profiles_coaches") {
      if (pagerEl) pagerEl.style.display = "none";
    } else {
      if (pagerEl) pagerEl.style.display = "flex";
      const startIdx = TIMING_TOTAL === 0 ? 0 : TIMING_OFFSET + 1;
      const endIdx = Math.min(TIMING_TOTAL, TIMING_OFFSET + (data.rows || []).length);
      if (pageInfoEl) {
        pageInfoEl.textContent = `${startIdx}–${endIdx} of ${TIMING_TOTAL.toLocaleString()}`;
      }
      if (timingPrevBtn) timingPrevBtn.disabled = TIMING_OFFSET <= 0;
      if (timingNextBtn) timingNextBtn.disabled = TIMING_OFFSET + TIMING_LIMIT >= TIMING_TOTAL;
    }

    if (ACTIVE_TIMING_TAB === "model_trains") {
      const rows = data.rows || [];
      container.innerHTML = `
        <table class="timing-grid-table">
          <thead>
            <tr>
              <th>Timing Load Code</th>
              <th>Public Name</th>
              <th>Description</th>
              <th>Traction Unit(s)</th>
              <th>Propulsion</th>
              <th>Max Speed</th>
              <th>Speed Profile</th>
              <th>Length / Mass</th>
              <th>Braking</th>
            </tr>
          </thead>
          <tbody>
            ${rows
              .map(
                (r) => `
              <tr class="clickable-row" onclick="inspectFromTimingModal('modeltrain', ${r.id}, false)">
                <td><strong style="color:#38bdf8">${escapeHtml(r.name)}</strong></td>
                <td>${escapeHtml(r.publicname)}</td>
                <td>${escapeHtml(r.desc)}</td>
                <td><code>${escapeHtml(r.traction_summary)}</code></td>
                <td><span class="pill-tag ${r.propulsion.includes("Electric") ? "electric" : "diesel"}">${escapeHtml(r.propulsion)}</span></td>
                <td><strong>${r.maxspeed_kmh} km/h</strong> (${r.maxspeed_mph} mph)</td>
                <td><span class="pill-tag">${escapeHtml(r.profile_name)}</span></td>
                <td>${r.total_length_m}m / ${r.total_weight_t}t</td>
                <td>${r.retardation} m/s²</td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>
      `;
    } else if (ACTIVE_TIMING_TAB === "traction_units") {
      const rows = data.rows || [];
      container.innerHTML = `
        <table class="timing-grid-table">
          <thead>
            <tr>
              <th>Traction Unit</th>
              <th>Description</th>
              <th>Type / Propulsion</th>
              <th>Max Speed</th>
              <th>Length / Mass</th>
              <th>Starting Effort</th>
              <th>Peak Rim Power</th>
              <th>Davis Drag R(v) = A + Bv + Cv²</th>
              <th>Braking</th>
            </tr>
          </thead>
          <tbody>
            ${rows
              .map(
                (r) => `
              <tr class="clickable-row" onclick="inspectFromTimingModal('tractionunit', ${r.id}, false)">
                <td><strong style="color:#c4b5fd">${escapeHtml(r.name)}</strong></td>
                <td>${escapeHtml(r.desc)}</td>
                <td><span class="pill-tag ${r.motor_desc === "Electric" ? "electric" : "diesel"}">${escapeHtml(r.type_code)} • ${escapeHtml(r.motor_desc)}</span></td>
                <td><strong>${r.max_speed_kmh} km/h</strong> (${r.max_speed_mph} mph)</td>
                <td>${r.length_m}m / ${r.weight_t}t</td>
                <td><strong style="color:#22d3ee">${r.max_force_kn} kN</strong></td>
                <td><strong style="color:#a855f7">${r.max_power_kw} kW</strong> (${Math.round(r.max_power_kw * 1.34102)} bhp)</td>
                <td><code>${r.coeffA} + ${r.coeffB}v + ${r.coeffC}v²</code></td>
                <td>${r.retardation} m/s²</td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>
      `;
    } else if (ACTIVE_TIMING_TAB === "profiles_coaches") {
      const profs = data.velocity_profiles || [];
      const coaches = data.coaches || [];
      const ttypes = data.train_types || [];
      container.innerHTML = `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:20px">
          <div>
            <h3 style="font-size:0.86rem;color:#38bdf8;margin-bottom:8px">Differential Velocity Profiles (&lt;veloprofiledesc&gt;)</h3>
            <table class="timing-grid-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Profile Code</th>
                  <th>Description</th>
                  <th>Timing Loads</th>
                </tr>
              </thead>
              <tbody>
                ${profs
                  .map(
                    (p) => `
                  <tr class="clickable-row" onclick="filterTimingLoadsByProfile('${p.id}')">
                    <td>#${p.id}</td>
                    <td><strong style="color:#22d3ee">${escapeHtml(p.code)}</strong></td>
                    <td>${escapeHtml(p.description)}</td>
                    <td><span class="pill-tag">${p.timing_load_count} loads &rarr;</span></td>
                  </tr>`
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
          <div>
            <h3 style="font-size:0.86rem;color:#c4b5fd;margin-bottom:8px">Trailing Coach &amp; Freight Wagon Templates (&lt;coach&gt; — ${coaches.length})</h3>
            <table class="timing-grid-table">
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Description</th>
                  <th>Type</th>
                  <th>Unit Length / Mass</th>
                  <th>Max Speed</th>
                </tr>
              </thead>
              <tbody>
                ${coaches
                  .map(
                    (c) => `
                  <tr>
                    <td><strong>${escapeHtml(c.name)}</strong></td>
                    <td>${escapeHtml(c.desc)}</td>
                    <td><span class="pill-tag">${escapeHtml(c.coach_type)}</span></td>
                    <td>${c.length_m}m / ${c.weight_t}t</td>
                    <td>${c.max_speed_kmh} km/h (${c.max_speed_mph} mph)</td>
                  </tr>`
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        </div>
        <div>
          <h3 style="font-size:0.86rem;color:#fbbf24;margin-bottom:8px">CIF / TPS Train Classification Categories (&lt;traintype&gt; — ${ttypes.length})</h3>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:6px">
            ${ttypes
              .map(
                (t) => `
              <div class="rel-card" style="cursor:default;margin-bottom:0">
                <div class="rel-card-top"><span style="color:#fbbf24">${escapeHtml(t.code)}</span><span>ID #${t.id}</span></div>
                <div class="rel-card-sub">${escapeHtml(t.description)}</div>
              </div>`
              )
              .join("")}
          </div>
        </div>
      `;
    } else if (ACTIVE_TIMING_TAB === "station_banks") {
      const banks = data.rows || [];
      const roles = data.timing_roles || [];
      const cats = data.planning_categories || [];
      container.innerHTML = `
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:18px">
          <div>
            <h3 style="font-size:0.85rem;color:#fb7185;margin-bottom:8px">TPS Timing Point Calculation Roles (&lt;stationtypedesc&gt;)</h3>
            <table class="timing-grid-table">
              <thead>
                <tr><th>ID</th><th>Timing Calculation Role</th><th>Count</th></tr>
              </thead>
              <tbody>
                ${roles
                  .map(
                    (r) => `
                  <tr>
                    <td>#${r.type_id}</td>
                    <td><span class="pill-tag ${r.type_id === 2 ? "mandatory" : ""}">${escapeHtml(r.label)}</span></td>
                    <td><strong>${r.count.toLocaleString()}</strong></td>
                  </tr>`
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
          <div>
            <h3 style="font-size:0.85rem;color:#38bdf8;margin-bottom:8px">Planning Location Hierarchy (&lt;stationcategorydesc&gt;)</h3>
            <table class="timing-grid-table">
              <thead>
                <tr><th>ID</th><th>Planning Location Category</th><th>Count</th></tr>
              </thead>
              <tbody>
                ${cats
                  .map(
                    (c) => `
                  <tr>
                    <td>#${c.category_id}</td>
                    <td>${escapeHtml(c.label)}</td>
                    <td><strong>${c.count.toLocaleString()}</strong></td>
                  </tr>`
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        </div>
        <h3 style="font-size:0.86rem;color:#c4b5fd;margin-bottom:8px">Timetable Planning Corridors / Station Banks (&lt;stationbank&gt; — Click any corridor to illuminate on schematic)</h3>
        <table class="timing-grid-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Corridor Code</th>
              <th>Timetable Planning Corridor Description</th>
              <th>Sections</th>
              <th>Last Modified</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            ${banks
              .map(
                (b) => `
              <tr class="clickable-row" onclick="inspectFromTimingModal('stationbank', ${b.id}, true)">
                <td>#${b.id}</td>
                <td><strong style="color:#c4b5fd">${escapeHtml(b.code)}</strong></td>
                <td>${escapeHtml(b.description)}</td>
                <td>${b.section_count}</td>
                <td>${escapeHtml(b.last_modified)}</td>
                <td><span style="color:#38bdf8;font-weight:700">Illuminate Corridor &rarr;</span></td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>
      `;
    }
    container.scrollTop = 0;
  } catch (e) {
    if (e.name !== "AbortError") console.error(e);
  }
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* Initialization & Status Polling */
async function initApp() {
  resizeCanvas();
  updateLegend();
  updateLabelUi();

  const overlay = document.getElementById("ingest-overlay");
  while (true) {
    try {
      const res = await fetch("/api/status");
      if (res.ok) {
        const st = await res.json();
        if (st.project && st.infra) {
          document.getElementById("dataset-subtitle").textContent =
            `${st.infra.name || "INFRA FREEZE"} • NWR_TrackModel 20261026`;
        }
        if (st.ingest.status === "ready") {
          overlay.classList.add("hidden");
          break;
        } else {
          overlay.classList.remove("hidden");
          document.getElementById("ingest-phase").textContent = st.ingest.phase || "Processing...";
          document.getElementById("ingest-progress-bar").style.width = `${st.ingest.percent || 5}%`;
          document.getElementById("ingest-count").textContent = `${(st.ingest.elements_processed || 0).toLocaleString()} / ~2,508,000 elements`;
          document.getElementById("ingest-pct").textContent = `${st.ingest.percent || 0}%`;
        }
      }
    } catch (e) {
      console.error(e);
    }
    await new Promise((r) => setTimeout(r, 1200));
  }

  // Load whole-network overview
  const ovRes = await fetch("/api/overview");
  if (ovRes.ok) {
    STATE.overview = await ovRes.json();
    STATE.ready = true;
    fitUKNetwork();
  }
}

initApp();
