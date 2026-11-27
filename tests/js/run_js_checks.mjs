/**
 * Automated JavaScript syntax & pure-helper unit test suite.
 *
 * 1. Runs `node --check` on every `.js` module in `backend/static/js/`.
 * 2. Executes pure-function unit tests for `route-speed-profile.js` and
 *    `data-loader.js` route-highlight helpers in an isolated `vm` sandbox.
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

assert.ok(jsFiles.length >= 16, `Expected >= 16 JS modules, found ${jsFiles.length}`);

// 1. Syntax check every modular JS file
for (const file of jsFiles) {
  execFileSync(process.execPath, ["--check", path.join(JS_DIR, file)], {
    stdio: "inherit",
  });
}
console.log(`✔ Syntax check passed for all ${jsFiles.length} JS modules.`);

// 2. Unit test route-speed-profile.js helpers in an isolated VM context
const rspCtx = vm.createContext({
  window: {},
  STATE: { selectedAsset: null, crossHighlight: { nwr: { active: false, ways: [] } } },
  document: { getElementById: () => null },
  escapeHtml: (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"),
  requestRender: () => {},
});
vm.runInContext(
  fs.readFileSync(path.join(JS_DIR, "route-speed-profile.js"), "utf8"),
  rspCtx
);

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
        speed_profile: [
          { start_m: 0, end_m: 200, length_m: 200, vmax_kmh: 80, vmax_mph: 49.7, differential_speeds: [] },
          { start_m: 200, end_m: 400, length_m: 200, vmax_kmh: 160, vmax_mph: 99.4, differential_speeds: [{ profile_name: "EPS", vmax_kmh: 200, vmax_mph: 124.3 }] },
        ],
      },
    ],
    approach_blocks: [],
  },
};

const routes = rspCtx.extractRoutesForSpeedProfile(sampleSignal);
assert.equal(routes.length, 1);
assert.equal(routes[0].key, "outgoing:120");
const cardHtml = rspCtx.buildRouteSpeedProfileCard(sampleSignal);
assert.ok(cardHtml.includes("Route Speed Profile"));
assert.ok(cardHtml.includes("Highlight Route"));
assert.ok(cardHtml.includes("EPS"));
assert.ok(cardHtml.includes("<svg"));

// 3. Unit test pure helpers in data-loader.js (dual-view minimal mode + geo point extraction)
const dlCtx = vm.createContext({
  window: {},
  STATE: { activeView: "split" },
  document: { getElementById: () => null },
  console,
});
// Extract pure helper functions from data-loader.js safely via a minimal stubbed DOM
const dlSource = fs.readFileSync(path.join(JS_DIR, "data-loader.js"), "utf8");
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

console.log("✔ JS pure-helper unit tests passed.");

