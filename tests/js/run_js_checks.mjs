/**
 * Automated JavaScript syntax & pure-helper unit test suite.
 *
 * 1. Runs `node --check` on every `.js` module in `backend/static/js/`.
 * 2. Executes unit tests in isolated `vm` sandboxes for:
 *    - `route-speed-profile.js` (speed conversion, SVG chart, forward/backward stepping)
 *    - `data-loader.js` (dual-view minimal mode, geo extraction, LRU detail cache)
 *    - `geo-projection.js` (OSGB36 <-> WGS84 projection round-trip accuracy)
 *    - `persistence.js` (cookie/localStorage persistence & style resets)
 *    - `signalling-envelope.js` (filter sync & Clear Highlight restoration)
 */

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const JS_DIR = path.resolve("backend/static/js");
const jsFiles = fs
  .readdirSync(JS_DIR)
  .filter((f) => f.endsWith(".js"))
  .sort();

assert.ok(jsFiles.length >= 20, `Expected >= 20 JS modules, found ${jsFiles.length}`);

// 1. Syntax check every modular JS file
for (const file of jsFiles) {
  execFileSync(process.execPath, ["--check", path.join(JS_DIR, file)], {
    stdio: "inherit",
  });
}
console.log(`✔ Syntax check passed for all ${jsFiles.length} JS modules.`);

// 2. Unit test route-speed-profile.js helpers & corridor stepping buttons
const rspCtx = vm.createContext({
  window: {},
  STATE: { selectedAsset: null, crossHighlight: { nwr: { active: false, ways: [] } } },
  document: { getElementById: () => null },
  escapeHtml: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"),
  requestRender: () => {},
});
vm.runInContext(fs.readFileSync(path.join(JS_DIR, "route-speed-profile.js"), "utf8"), rspCtx);

assert.equal(rspCtx.kmhToMph(160), 99.4);
assert.equal(rspCtx.kmhToMph(0), null);
assert.equal(rspCtx.kmhToMph(null), null);
assert.equal(rspCtx.getSpeedTierColor(20), "#ef4444");
assert.equal(rspCtx.getSpeedTierColor(45), "#f59e0b");
assert.equal(rspCtx.getSpeedTierColor(70), "#38bdf8");
assert.equal(rspCtx.getSpeedTierColor(100), "#10b981");
assert.equal(rspCtx.getSpeedTierColor(125), "#a855f7");

const sampleSignal = {
  asset_type: "signal",
  id: 50,
  title: "Signal T3533",
  related: {
    outgoing_blocks: [
      {
        block_id: 120,
        route_label: "From T3533 to T3535",
        length_meters: 400,
        min_vmax: 80,
        max_vmax: 160,
        exit_signals: [{ pk: 51, signal_id: 5001, name: "T3535", is_points: false }],
        speed_profile: [
          { start_m: 0, end_m: 200, length_m: 200, vmax_kmh: 80, vmax_mph: 49.7, differential_speeds: [] },
          {
            start_m: 200,
            end_m: 400,
            length_m: 200,
            vmax_kmh: 160,
            vmax_mph: 99.4,
            differential_speeds: [{ profile_name: "EPS", vmax_kmh: 200, vmax_mph: 124.3 }],
          },
        ],
      },
    ],
    approach_blocks: [
      {
        block_id: 110,
        route_label: "From T3531 to T3533",
        length_meters: 300,
        min_vmax: 100,
        max_vmax: 100,
        entry_signals: [{ pk: 49, signal_id: 4999, name: "T3531", is_points: false }],
        speed_profile: [
          { start_m: 0, end_m: 300, length_m: 300, vmax_kmh: 100, vmax_mph: 62.1, differential_speeds: [] },
        ],
      },
    ],
  },
};

const routes = rspCtx.extractRoutesForSpeedProfile(sampleSignal);
assert.equal(routes.length, 2);
assert.equal(routes[0].key, "outgoing:120");
assert.equal(routes[0].step_target.direction, "forward");
assert.equal(routes[1].key, "approach:110");
assert.equal(routes[1].step_target.direction, "backward");

const cardHtml = rspCtx.buildRouteSpeedProfileCard(sampleSignal);
assert.ok(cardHtml.includes("Route Speed Profile"));
assert.ok(cardHtml.includes("Highlight Route"));
assert.ok(cardHtml.includes("Next: T3535 →"));
assert.ok(cardHtml.includes("EPS"));
assert.ok(cardHtml.includes("<svg"));

const approachCardHtml = rspCtx.buildRouteSpeedProfileCard(sampleSignal, "approach:110");
assert.ok(approachCardHtml.includes("← Rear: T3531"));

// 3. Unit test data-loader modules (pure helpers, viewport LRU, geo extraction, detail LRU)
const dlCtx = vm.createContext({
  window: {},
  STATE: { activeView: "split" },
  document: { getElementById: () => null },
  console,
});
const dlSource = [
  "data-loader-viewport.js",
  "data-loader-geo.js",
  "data-loader.js",
]
  .map((f) => fs.readFileSync(path.join(JS_DIR, f), "utf8"))
  .join("\n");
vm.runInContext(
  `
  const hoverTooltip = { classList: { add() {}, remove() {} } };
  const canvas = { clientWidth: 800, clientHeight: 600 };
  const nwrCanvas = { clientWidth: 800, clientHeight: 600 };
  ${dlSource}
  `,
  dlCtx
);

assert.equal(dlCtx.shouldUseMinimalDualHighlight("signal", false), true);
assert.equal(dlCtx.shouldUseMinimalDualHighlight("points", false), true);
assert.equal(dlCtx.shouldUseMinimalDualHighlight("signal", true), false);
assert.equal(dlCtx.shouldUseMinimalDualHighlight("station", false), false);

assert.deepEqual(
  { ...dlCtx.extractGeoPointFromCrossLinks({ node_points: [{ x: 512345.5, y: 180123.2 }] }) },
  { x: 512345.5, y: 180123.2 }
);
assert.deepEqual(
  { ...dlCtx.extractGeoPointFromCrossLinks({ geo_snap: { x: 500000, y: 175000 } }) },
  { x: 500000, y: 175000 }
);
assert.equal(dlCtx.extractGeoPointFromCrossLinks(null), null);

dlCtx.setCachedAssetDetail("signal:50", { id: 50, title: "Signal T3533" });
assert.equal(dlCtx.getCachedAssetDetail("signal:50").id, 50);
dlCtx.invalidateAssetDetailCache("signal", 50);
assert.equal(dlCtx.getCachedAssetDetail("signal:50"), null);

const vpCache = new Map();
for (let i = 0; i < 20; i++) {
  dlCtx.setViewportCacheEntry(vpCache, `k${i}`, { idx: i });
}
assert.equal(vpCache.size, 16);
assert.equal(dlCtx.getViewportCacheEntry(vpCache, "k0"), null);
assert.equal(dlCtx.getViewportCacheEntry(vpCache, "k19").idx, 19);

// 4. Unit test geo-projection.js (OSGB36 <-> WGS84 round-trip accuracy & LRU OSM tile cap)
const geoCtx = vm.createContext({ Math });
vm.runInContext(fs.readFileSync(path.join(JS_DIR, "geo-projection.js"), "utf8"), geoCtx);

// London Paddington area: OSGB36 E=526600, N=181300 -> approx 51.516 N, -0.177 W
const [lat, lon] = geoCtx.osgb36_to_wgs84(526600, 181300);
assert.ok(lat > 51.5 && lat < 51.53, `Unexpected latitude: ${lat}`);
assert.ok(lon > -0.2 && lon < -0.15, `Unexpected longitude: ${lon}`);
const [eBack, nBack] = geoCtx.wgs84_to_osgb36(lat, lon);
assert.ok(Math.abs(eBack - 526600) < 1.0, `Easting round-trip drift: ${eBack - 526600}`);
assert.ok(Math.abs(nBack - 181300) < 1.0, `Northing round-trip drift: ${nBack - 181300}`);

// Verify bounded LRU eviction on OSM tile cache (max 256)
for (let i = 0; i < 260; i++) {
  geoCtx.setOsmTileEntry(`12_${i}_0`, { img: { onload: () => {}, onerror: () => {} }, loaded: true, error: false });
}
assert.equal(vm.runInContext("osmTileCache.size", geoCtx), 256);
assert.equal(geoCtx.getOsmTileEntry("12_0_0"), null);
assert.ok(geoCtx.getOsmTileEntry("12_259_0") !== null);

// 5. Unit test persistence.js (Colour-By & layer styles persistence)
const store = new Map();
const persCtx = vm.createContext({
  STATE: {
    colorMode: "vmax",
    nwrColorMode: "track_class",
    osmOpacity: 30,
    layers: {},
    nwrLayers: { osm: true },
    layerStyles: {},
  },
  document: {
    _cookie: "",
    get cookie() {
      return this._cookie;
    },
    set cookie(v) {
      this._cookie = v.split(";")[0];
    },
    getElementById: () => null,
    querySelector: () => null,
  },
  localStorage: {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
  },
  updateLayerStyleUi: () => {},
  updateLegend: () => {},
  requestRender: () => {},
});
vm.runInContext(fs.readFileSync(path.join(JS_DIR, "persistence.js"), "utf8"), persCtx);

persCtx.STATE.colorMode = "gradient";
persCtx.STATE.nwrColorMode = "curvature";
persCtx.savePersistedColorModes();
persCtx.STATE.colorMode = "vmax";
persCtx.STATE.nwrColorMode = "track_type";
persCtx.loadPersistedColorModes();
assert.equal(persCtx.STATE.colorMode, "gradient");
assert.equal(persCtx.STATE.nwrColorMode, "curvature");

persCtx.resetLayerStyles("tps");
assert.equal(persCtx.STATE.layerStyles.edges.color, "#38bdf8");

// 6. Unit test signalling-envelope.js (Clear Highlight restoration & two-way sync)
let syncedSpeedKey = null;
let syncedGeoGroup = null;
let renderCount = 0;
let clearBtnHidden = true;

const envCtx = vm.createContext({
  window: {},
  STATE: {
    highlightGroupFilter: "none",
    highlight: { ways: [], points: [], segments: [] },
    selectedAsset: {
      asset_type: "signal",
      id: 50,
      highlight: {
        ways: [{ group: "outgoing", block_id: 120, coords: [[0, 0], [10, 0]] }],
        points: [{ x: 0, y: 0 }],
        segments: [],
      },
    },
  },
  document: {
    getElementById: (id) =>
      id === "btn-clear-highlight"
        ? { classList: { remove: (c) => { if (c === "hidden") clearBtnHidden = false; } } }
        : null,
    querySelectorAll: () => [],
    querySelector: () => null,
  },
  syncGeoHighlightsForRouteFilter: (grp) => {
    syncedGeoGroup = grp;
  },
  requestRender: () => {
    renderCount += 1;
  },
  escapeHtml: (s) => String(s),
});
envCtx.window.selectRouteSpeedProfile = (key, syncFlag) => {
  syncedSpeedKey = `${key}:${syncFlag}`;
};
vm.runInContext(fs.readFileSync(path.join(JS_DIR, "signalling-envelope.js"), "utf8"), envCtx);

envCtx.window.setSignalHighlightFilter("outgoing:120");
assert.equal(envCtx.STATE.highlightGroupFilter, "outgoing:120");
assert.equal(envCtx.STATE.highlight.ways.length, 1, "Highlight geometry should restore after Clear Highlight");
assert.equal(clearBtnHidden, false, "Clear Highlight button should reappear");
assert.equal(syncedSpeedKey, "outgoing:120:false");
assert.equal(syncedGeoGroup, "outgoing:120");
assert.equal(renderCount, 1);

console.log("✔ JS pure-helper unit tests passed (route-speed-profile, data-loader, geo-projection, persistence, signalling-envelope).");
