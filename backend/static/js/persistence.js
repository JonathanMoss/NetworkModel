/**
 * @fileoverview Cookie/storage persistence for layer styles, OSM opacity,
 * and layer visibility toggles.
 */

/* ============================================================================
   COOKIE & LOCALSTORAGE PERSISTENCE FOR LAYER STYLES AND OSM BASEMAP
   ============================================================================ */
function setCookie(name, value, days = 365) {
  const expires = new Date(Date.now() + days * 864e5).toUTCString();
  const valStr = encodeURIComponent(typeof value === "string" ? value : JSON.stringify(value));
  document.cookie = `${name}=${valStr}; expires=${expires}; path=/; SameSite=Lax`;
  try {
    localStorage.setItem(name, typeof value === "string" ? value : JSON.stringify(value));
  } catch (_) {}
}

function getCookie(name) {
  const nameEQ = `${name}=`;
  const ca = document.cookie.split(";");
  for (let i = 0; i < ca.length; i++) {
    let c = ca[i].trim();
    if (c.indexOf(nameEQ) === 0) {
      const val = decodeURIComponent(c.substring(nameEQ.length));
      try {
        return JSON.parse(val);
      } catch (_) {
        return val;
      }
    }
  }
  try {
    const ls = localStorage.getItem(name);
    if (ls) {
      try {
        return JSON.parse(ls);
      } catch (_) {
        return ls;
      }
    }
  } catch (_) {}
  return null;
}

const DEFAULT_LAYER_STYLES = {
  edges: { color: "#38bdf8", width: 1.5 },
  tracks: { color: "#818cf8", width: 3.0 },
  vectors: { color: "#64748b", width: 1.0 },
  blocks: { color: "#10b981", width: 2.5 },
  polys: { color: "#a855f7", width: 1.5 },
  nwr_centrelines: { color: "#38bdf8", width: 1.8 },
  nwr_elrs: { color: "#a855f7", width: 2.0 },
  nwr_curvature: { color: "#f43f5e", width: 2.5 },
};

function loadPersistedLayerStyles() {
  const saved = getCookie("nr_layer_styles");
  if (saved && typeof saved === "object") {
    for (const [k, def] of Object.entries(DEFAULT_LAYER_STYLES)) {
      if (!STATE.layerStyles[k]) STATE.layerStyles[k] = { ...def };
      if (saved[k]) {
        if (typeof saved[k].color === "string") STATE.layerStyles[k].color = saved[k].color;
        if (typeof saved[k].width === "number") STATE.layerStyles[k].width = saved[k].width;
      }
    }
  }
}

function savePersistedLayerStyles() {
  setCookie("nr_layer_styles", STATE.layerStyles, 365);
}

function resetLayerStyles(scope = "all") {
  if (scope === "tps" || scope === "all") {
    for (const k of ["edges", "tracks", "vectors", "blocks", "polys"]) {
      STATE.layerStyles[k] = { ...DEFAULT_LAYER_STYLES[k] };
    }
  }
  if (scope === "nwr" || scope === "all") {
    for (const k of ["nwr_centrelines", "nwr_elrs", "nwr_curvature"]) {
      STATE.layerStyles[k] = { ...DEFAULT_LAYER_STYLES[k] };
    }
  }
  savePersistedLayerStyles();
  updateLayerStyleUi();
  updateLegend();
  requestRender();
}

function loadPersistedOsmSettings() {
  const saved = getCookie("nr_osm_settings");
  if (saved && typeof saved === "object") {
    if (typeof saved.enabled === "boolean") STATE.nwrLayers.osm = saved.enabled;
    // Default to 30%; migrate old 65% default to new 30% default
    if (typeof saved.opacity === "number" && saved.opacity !== 65) {
      STATE.osmOpacity = Math.max(0, Math.min(100, saved.opacity));
    } else {
      STATE.osmOpacity = 30;
    }
  }
}

function savePersistedOsmSettings() {
  setCookie(
    "nr_osm_settings",
    {
      enabled: STATE.nwrLayers.osm,
      opacity: STATE.osmOpacity,
    },
    365
  );
}

const TPS_LAYER_KEYS = ["edges", "tracks", "vectors", "texts", "stations", "signals", "blocks", "nodes", "polys", "possessions"];
const NWR_LAYER_MAP = [
  ["osm", "nwr-layer-osm"],
  ["centrelines", "nwr-layer-centrelines"],
  ["elrs", "nwr-layer-elrs"],
  ["nodes", "nwr-layer-nodes"],
  ["waymarks", "nwr-layer-waymarks"],
  ["curvature", "nwr-layer-curvature"],
  ["nf_points", "nwr-layer-nf-points"],
  ["possessions", "nwr-layer-possessions"],
];

function loadPersistedLayerToggles() {
  const saved = getCookie("nr_layer_toggles");
  if (saved && typeof saved === "object") {
    if (saved.tps && typeof saved.tps === "object") {
      for (const k of TPS_LAYER_KEYS) {
        if (typeof saved.tps[k] === "boolean") {
          STATE.layers[k] = saved.tps[k];
        }
      }
    }
    if (saved.nwr && typeof saved.nwr === "object") {
      for (const [k] of NWR_LAYER_MAP) {
        if (typeof saved.nwr[k] === "boolean") {
          STATE.nwrLayers[k] = saved.nwr[k];
        }
      }
    }
  }
}

function savePersistedLayerToggles() {
  setCookie(
    "nr_layer_toggles",
    {
      tps: { ...STATE.layers },
      nwr: { ...STATE.nwrLayers },
    },
    365
  );
}

function updateLayerToggleUi() {
  for (const k of TPS_LAYER_KEYS) {
    const el = document.getElementById(`layer-${k}`);
    if (el) el.checked = Boolean(STATE.layers[k]);
  }
  for (const [k, id] of NWR_LAYER_MAP) {
    const el = document.getElementById(id);
    if (el) el.checked = Boolean(STATE.nwrLayers[k]);
  }
}

const VALID_TPS_COLOR_MODES = ["electrified", "signalling", "timing", "vmax", "gradient", "uniform"];
const VALID_NWR_COLOR_MODES = ["track_type", "direction", "ownership", "curvature", "elevation", "uniform"];

/**
 * Restore persisted TPS and NWR "Colour By" dropdown modes from cookies/localStorage.
 */
function loadPersistedColorModes() {
  const saved = getCookie("nr_color_modes");
  if (saved && typeof saved === "object") {
    if (typeof saved.tps === "string" && VALID_TPS_COLOR_MODES.includes(saved.tps)) {
      STATE.colorMode = saved.tps;
    }
    if (typeof saved.nwr === "string" && VALID_NWR_COLOR_MODES.includes(saved.nwr)) {
      STATE.nwrColorMode = saved.nwr;
    }
  }
}

/**
 * Persist the active TPS and NWR "Colour By" modes across browser sessions.
 */
function savePersistedColorModes() {
  setCookie(
    "nr_color_modes",
    {
      tps: STATE.colorMode,
      nwr: STATE.nwrColorMode,
    },
    365
  );
}

/**
 * Sync the TPS and NWR "Colour By" <select> elements to match STATE.
 */
function updateColorModeUi() {
  const tpsSel = document.getElementById("color-mode");
  if (tpsSel && VALID_TPS_COLOR_MODES.includes(STATE.colorMode)) {
    tpsSel.value = STATE.colorMode;
  }
  const nwrSel = document.getElementById("nwr-color-mode");
  if (nwrSel && VALID_NWR_COLOR_MODES.includes(STATE.nwrColorMode)) {
    nwrSel.value = STATE.nwrColorMode;
  }
}

function hexToRgba(hex, alpha = 1) {
  if (!hex || typeof hex !== "string") return `rgba(56, 189, 248, ${alpha})`;
  if (hex.startsWith("rgba")) return hex;
  let c = hex.replace("#", "");
  if (c.length === 3) c = c.split("").map((x) => x + x).join("");
  const num = parseInt(c, 16);
  if (isNaN(num)) return `rgba(56, 189, 248, ${alpha})`;
  const r = (num >> 16) & 255;
  const g = (num >> 8) & 255;
  const b = num & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function updateLayerStyleUi() {
  for (const [key, style] of Object.entries(STATE.layerStyles)) {
    const colorInput = document.querySelector(`.layer-color-input[data-layer="${key}"]`);
    if (colorInput) colorInput.value = style.color;

    const swatch = document.getElementById(`swatch-${key.replace(/_/g, "-")}`);
    if (swatch) swatch.style.backgroundColor = style.color;

    const dot = document.getElementById(`swatch-dot-${key.replace(/_/g, "-")}`);
    if (dot) dot.style.backgroundColor = style.color;

    const widthReadout = document.querySelector(`.width-readout[data-readout="${key}"]`);
    if (widthReadout) widthReadout.textContent = `${style.width.toFixed(1)}px`;
  }

  const osmCheck = document.getElementById("nwr-layer-osm");
  if (osmCheck) osmCheck.checked = STATE.nwrLayers.osm;

  const osmSlider = document.getElementById("osm-opacity-slider");
  if (osmSlider) osmSlider.value = STATE.osmOpacity ?? 30;

  const osmReadout = document.getElementById("osm-opacity-readout");
  if (osmReadout) osmReadout.textContent = `${STATE.osmOpacity ?? 30}%`;
}

