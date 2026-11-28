/**
 * @fileoverview Global application state, DOM element references, canvas sizing,
 * and layer label scale/orientation helpers.
 */

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
    possessions: true,
  },
  nwrLayers: {
    osm: true,
    centrelines: true,
    elrs: true,
    nodes: true,
    waymarks: true,
    curvature: true,
    nf_points: false,
    possessions: true,
  },
  osmOpacity: 30,

  layerStyles: {
    edges: { color: "#38bdf8", width: 1.5 },
    tracks: { color: "#818cf8", width: 3.0 },
    vectors: { color: "#64748b", width: 1.0 },
    blocks: { color: "#10b981", width: 2.5 },
    polys: { color: "#a855f7", width: 1.5 },
    nwr_centrelines: { color: "#38bdf8", width: 1.8 },
    nwr_elrs: { color: "#a855f7", width: 2.0 },
    nwr_curvature: { color: "#f43f5e", width: 2.5 },
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
    nf_curvature: 1.0,
    nf_points: 1.0,
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
    nf_curvature: 0,
    nf_points: 0,
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
    curvature: [],
    nf_points: [],
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
  drawnNfCurvatureLabels: [],
  drawnNfPointLabels: [],

  // Hover submenu & live preview state
  hoverCandidates: [],
  hoverPreview: null,
  isPointerInMenu: false,
  hoverHideTimer: null,
  hoverEnrichTimer: null,
  hoverWorldPos: null,
  lastHoverAnchor: { sx: 0, sy: 0 },

  selectedAsset: null,
  possessions: [],
  visiblePossessionIds: new Set(),
  openPossessionStyleIds: new Set(),
  activePossessionId: null,
  possessionPickTarget: null,
  highlightGroupFilter: "all",
  highlight: {
    ways: [],
    points: [],
    segments: [],
  },

  profileDrawer: {
    visible: false,
    scope: "track",
    data: null,
    hoverItem: null,
    scrubWorldPos: null,
  },

  catalogTags: [],
  catalogCurrentTag: null,
  catalogOffset: 0,
  catalogLimit: 50,

  leftPaneCollapsed: false,
  rightPaneCollapsed: false,

  splitRatio: 0.5,
  tpsCam: { camX: 0, camY: 0, zoom: 1.0 },
  nwrCam: { camX: 400000, camY: 300000, zoom: 0.005 },
  crossHighlight: {
    tps: { ways: [], points: [], pulseStart: 0, pulseDuration: 4500, active: false, title: "" },
    nwr: { ways: [], points: [], pulseStart: 0, pulseDuration: 4500, active: false, title: "" },
  },
  dragTargetCanvas: null,
};

const canvas = document.getElementById("schematic-canvas");
const ctx = canvas.getContext("2d");
const nwrCanvas = document.getElementById("nwr-canvas");
const nwrCtx = nwrCanvas ? nwrCanvas.getContext("2d") : null;
const hoverTooltip = document.getElementById("hover-tooltip");

function resizeCanvas() {
  const container = document.getElementById("canvas-container");
  if (!container) return;
  const dpr = window.devicePixelRatio || 1;
  const isSplit = STATE.activeView === "split";
  const isNwr = STATE.activeView === "nwr";

  container.className = `canvas-wrap view-mode-${STATE.activeView}`;
  if (isSplit) {
    container.style.setProperty("--split-ratio", `${(STATE.splitRatio * 100).toFixed(1)}%`);
  }

  const paneTps = document.getElementById("pane-tps");
  if (paneTps && (isSplit || !isNwr)) {
    const w = paneTps.clientWidth;
    const h = paneTps.clientHeight;
    if (w > 0 && h > 0) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
  }

  const paneNwr = document.getElementById("pane-nwr");
  if (paneNwr && nwrCanvas && (isSplit || isNwr)) {
    const w = paneNwr.clientWidth;
    const h = paneNwr.clientHeight;
    if (w > 0 && h > 0) {
      nwrCanvas.width = w * dpr;
      nwrCanvas.height = h * dpr;
      if (nwrCtx) nwrCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
  }

  renderProfileCanvas();
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

