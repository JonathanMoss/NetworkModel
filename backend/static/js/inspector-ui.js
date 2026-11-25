/**
 * @fileoverview Right Inspector Drawer tab coordinator (Properties & Raw JSONB
 * tab rendering and cross-view asset jump helpers).
 *
 * Companion modules:
 * - `route-speed-profile.js`: Route Speed Profile SVG card & table
 * - `signalling-envelope.js`: Signalling Envelope widget & filter bar
 * - `inspector-related.js`: Topology & Cross-Links tab & NWR track switcher
 * - `toolbar-controls.js`: Search, Colour-By, layer toggles & line styling
 */

/**
 * Render the currently active Inspector tab lazily for `STATE.selectedAsset`.
 */
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
    const routeSpeedHtml =
      typeof buildRouteSpeedProfileCard === "function"
        ? buildRouteSpeedProfileCard(data)
        : "";
    const tractionPhysicsHtml = buildTractionPhysicsWidget(data);
    const decodedEntries = Object.entries(data.decoded || {});
    document.getElementById("tab-decoded").innerHTML = `
      ${sigEnvelopeHtml}
      ${routeSpeedHtml}
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

/**
 * Render the Inspector's "Raw JSONB" tab with optional truncation for large payloads.
 *
 * @param {object} data Asset detail payload.
 * @param {boolean} [showFull=false] Whether to render full untruncated children JSON.
 */
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

/**
 * Switch to NWR Track Model view and inspect a specific NWR asset.
 *
 * @param {string} assetType NWR asset type (e.g. `"nwr_centreline"`, `"nwr_node"`).
 * @param {number|string} fid NWR feature ID.
 */
window.jumpToNwrAsset = async function (assetType, fid) {
  await switchView("nwr");
  await loadAssetDetails(assetType, fid, true);
};
