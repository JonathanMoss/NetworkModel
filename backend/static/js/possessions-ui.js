/**
 * @fileoverview Interactive Engineers' Possessions (PICOP / T3) & Worksites (ES)
 * manager drawer, multi-line / multi-From-To editor, CRUD API wiring, Pick-on-Map
 * mode, and camera fly-to.
 */

/** @type {Array<object>} Working list of blocked lines in the open create/edit form. */
let POSS_FORM_LINES = [];

/**
 * Create a blank From/To limit endpoint entry.
 *
 * @returns {object} Default limit endpoint specification.
 */
function createEmptyLimitPoint() {
  return {
    asset_type: "",
    asset_id: "",
    label: "",
    qualifier: "at",
    distance_meters: "",
  };
}

/**
 * Create a blank blocked-line object for the multi-line possession editor.
 *
 * @returns {object} Default line specification with 1 empty From and 1 empty To slot.
 */
function createEmptyPossessionLine() {
  return {
    line_name: "",
    from_limits: [createEmptyLimitPoint()],
    to_limits: [createEmptyLimitPoint()],
  };
}

/**
 * Fetch all persisted Engineers' Possessions from PostgreSQL and re-render
 * the drawer list and map overlays.
 */
async function loadPossessions() {
  try {
    const res = await fetch("/api/possessions");
    if (!res.ok) return;
    const data = await res.json();
    STATE.possessions = data.possessions || [];
    updatePossessionCounts();
    renderPossessionsList();
    requestRender();
  } catch (err) {
    console.error("Failed to load possessions:", err);
  }
}

function updatePossessionCounts() {
  const total = (STATE.possessions || []).length;
  const vis = STATE.visiblePossessionIds ? STATE.visiblePossessionIds.size : 0;
  const txt = vis > 0 ? `${vis}/${total}` : String(total);
  const el1 = document.getElementById("cnt-possessions");
  const el2 = document.getElementById("cnt-nwr-possessions");
  if (el1) el1.textContent = txt;
  if (el2) el2.textContent = txt;
}

/** @type {Map<number, number>} Debounce timers for style PATCH calls per possession ID. */
const _POSS_STYLE_TIMERS = new Map();

/**
 * Toggle whether a specific possession is rendered on the TPS & NWR views.
 *
 * @param {number} possessionId Possession ID.
 * @param {boolean} isVisible Whether to render it.
 */
window.togglePossessionVisibility = function (possessionId, isVisible) {
  if (!STATE.visiblePossessionIds) STATE.visiblePossessionIds = new Set();
  if (isVisible) {
    STATE.visiblePossessionIds.add(possessionId);
  } else {
    STATE.visiblePossessionIds.delete(possessionId);
    if (STATE.activePossessionId === possessionId) STATE.activePossessionId = null;
  }
  updatePossessionCounts();
  renderPossessionsList();
  requestRender();
};

window.showAllPossessions = function () {
  if (!STATE.visiblePossessionIds) STATE.visiblePossessionIds = new Set();
  for (const p of STATE.possessions || []) {
    if (p.status !== "Cancelled") STATE.visiblePossessionIds.add(p.id);
  }
  updatePossessionCounts();
  renderPossessionsList();
  requestRender();
};

window.hideAllPossessions = function () {
  if (!STATE.visiblePossessionIds) STATE.visiblePossessionIds = new Set();
  STATE.visiblePossessionIds.clear();
  STATE.activePossessionId = null;
  updatePossessionCounts();
  renderPossessionsList();
  requestRender();
};

window.togglePossessionStylePanel = function (possessionId) {
  if (!STATE.openPossessionStyleIds) STATE.openPossessionStyleIds = new Set();
  if (STATE.openPossessionStyleIds.has(possessionId)) {
    STATE.openPossessionStyleIds.delete(possessionId);
  } else {
    STATE.openPossessionStyleIds.add(possessionId);
  }
  renderPossessionsList();
};

/**
 * Optimistically apply a colour or render-style change to a possession and
 * persist it to PostgreSQL via `PATCH /api/possessions/{id}/style`.
 *
 * @param {number} possessionId Possession ID.
 * @param {object} patch Style fields (`color`, `pattern`, `thickness`, `halo`, `halo_opacity`, `labels`).
 */
window.updatePossessionStyle = function (possessionId, patch) {
  const poss = (STATE.possessions || []).find((p) => p.id === possessionId);
  if (!poss || !patch) return;

  if (patch.color) poss.color = patch.color;
  poss.render_style = { ...(poss.render_style || {}) };
  for (const k of ["pattern", "thickness", "halo", "halo_opacity", "labels"]) {
    if (patch[k] !== undefined) poss.render_style[k] = patch[k];
  }

  // Update card border colour and opacity readout live without full DOM rebuild during drag
  const card = document.querySelector(`.poss-card[data-poss-id="${possessionId}"]`);
  if (card && patch.color) card.style.borderLeftColor = patch.color;
  if (card && patch.halo_opacity !== undefined) {
    const valEl = card.querySelector(".poss-halo-val");
    if (valEl) valEl.textContent = `${Math.round(patch.halo_opacity * 100)}%`;
  }
  requestRender();

  if (_POSS_STYLE_TIMERS.has(possessionId)) {
    clearTimeout(_POSS_STYLE_TIMERS.get(possessionId));
  }
  const timer = setTimeout(async () => {
    _POSS_STYLE_TIMERS.delete(possessionId);
    try {
      await fetch(`/api/possessions/${possessionId}/style`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          color: poss.color,
          ...poss.render_style,
        }),
      });
    } catch (err) {
      console.error("Failed to save possession style:", err);
    }
  }, 180);
  _POSS_STYLE_TIMERS.set(possessionId, timer);
};

/**
 * Snapshot all current input values inside `#poss-lines-container` back into
 * `POSS_FORM_LINES` so user edits survive re-renders.
 */
function syncPossessionLinesFromDom() {
  const container = document.getElementById("poss-lines-container");
  if (!container) return;
  container.querySelectorAll(".poss-line-card").forEach((card) => {
    const idx = Number(card.dataset.idx);
    const ln = POSS_FORM_LINES[idx];
    if (!ln) return;
    ln.line_name = card.querySelector(".pl-name")?.value || "";

    const readSide = (side) => {
      const rows = card.querySelectorAll(`.pl-limit-row[data-side="${side}"]`);
      return Array.from(rows).map((row) => ({
        qualifier: row.querySelector(".pl-lim-qual")?.value || "at",
        distance_meters: row.querySelector(".pl-lim-dist")?.value || "",
        label: row.querySelector(".pl-lim-label")?.value || "",
        asset_type: row.querySelector(".pl-lim-type")?.value || "",
        asset_id: row.querySelector(".pl-lim-id")?.value || "",
      }));
    };
    const froms = readSide("from");
    const tos = readSide("to");
    ln.from_limits = froms.length ? froms : [createEmptyLimitPoint()];
    ln.to_limits = tos.length ? tos : [createEmptyLimitPoint()];
  });
}

function renderLimitRowHtml(lineIdx, side, ptIdx, pt, canRemove) {
  const ph = side === "from" ? "Click 🎯 + Pick From on map" : "Click 🎯 + Pick To on map";
  return `
    <div class="pl-limit-row" data-side="${side}" data-pt-idx="${ptIdx}">
      <input type="number" class="pl-lim-dist" min="0" step="50" value="${escapeHtml(String(pt.distance_meters ?? ""))}" placeholder="m" title="Optional offset in metres (e.g. 400)" />
      <select class="pl-lim-qual" title="Limit qualifier">
        <option value="at" ${pt.qualifier === "at" ? "selected" : ""}>At</option>
        <option value="approach" ${pt.qualifier === "approach" ? "selected" : ""}>Approach</option>
        <option value="beyond" ${pt.qualifier === "beyond" ? "selected" : ""}>Beyond</option>
        <option value="clear_of" ${pt.qualifier === "clear_of" ? "selected" : ""}>Clear Of</option>
        <option value="buffer" ${pt.qualifier === "buffer" ? "selected" : ""}>Buffer</option>
      </select>
      <input type="text" class="pl-lim-label" value="${escapeHtml(pt.label || "")}" placeholder="${ph}" />
      <input type="hidden" class="pl-lim-type" value="${escapeHtml(pt.asset_type || "")}" />
      <input type="hidden" class="pl-lim-id" value="${escapeHtml(String(pt.asset_id ?? ""))}" />
      <button type="button" class="btn btn-xs btn-accent" onclick="startPossessionMapPick({ kind: 'line_limit', idx: ${lineIdx}, side: '${side}', ptIdx: ${ptIdx} })" title="Pick / replace on map">🎯</button>
      ${canRemove ? `<button type="button" class="btn btn-xs btn-secondary" onclick="removePossessionLimit(${lineIdx}, '${side}', ${ptIdx})" title="Remove limit">&times;</button>` : ""}
    </div>`;
}

/**
 * Render the dynamic list of blocked-line cards inside `#poss-lines-container`.
 */
function renderPossessionLinesEditor() {
  const container = document.getElementById("poss-lines-container");
  if (!container) return;
  if (!POSS_FORM_LINES.length) {
    POSS_FORM_LINES = [createEmptyPossessionLine()];
  }

  container.innerHTML = POSS_FORM_LINES.map((ln, idx) => {
    const canRemoveLine = POSS_FORM_LINES.length > 1;
    const froms = ln.from_limits?.length ? ln.from_limits : [createEmptyLimitPoint()];
    const tos = ln.to_limits?.length ? ln.to_limits : [createEmptyLimitPoint()];
    return `
      <div class="poss-line-card" data-idx="${idx}">
        <div class="poss-line-card-head">
          <span class="poss-line-num">Line ${idx + 1}</span>
          <input type="text" class="pl-name" value="${escapeHtml(ln.line_name || "")}" placeholder="e.g. Up Fast/Main, Platform 1/2/3 &amp; Up Main" />
          ${canRemoveLine ? `<button type="button" class="btn btn-xs btn-secondary" onclick="removePossessionLine(${idx})" title="Remove Line">&times;</button>` : ""}
        </div>

        <div class="poss-endpoint-box">
          <div class="poss-endpoint-head">
            <span>From (Start Limits)</span>
            <button type="button" class="btn btn-xs btn-accent" onclick="pickNextPossessionLimit(${idx}, 'from')">🎯 + Pick From</button>
          </div>
          <div class="pl-limits-list">
            ${froms.map((pt, pIdx) => renderLimitRowHtml(idx, "from", pIdx, pt, froms.length > 1)).join("")}
          </div>
        </div>

        <div class="poss-endpoint-box">
          <div class="poss-endpoint-head">
            <span>To (End Limits)</span>
            <button type="button" class="btn btn-xs btn-accent" onclick="pickNextPossessionLimit(${idx}, 'to')">🎯 + Pick To</button>
          </div>
          <div class="pl-limits-list">
            ${tos.map((pt, pIdx) => renderLimitRowHtml(idx, "to", pIdx, pt, tos.length > 1)).join("")}
          </div>
        </div>
      </div>`;
  }).join("");
}

window.addPossessionLine = function () {
  syncPossessionLinesFromDom();
  POSS_FORM_LINES.push(createEmptyPossessionLine());
  renderPossessionLinesEditor();
};

window.removePossessionLine = function (idx) {
  syncPossessionLinesFromDom();
  if (POSS_FORM_LINES.length <= 1) return;
  POSS_FORM_LINES.splice(idx, 1);
  renderPossessionLinesEditor();
};

/**
 * Pick into the first empty From/To slot on a line, or append a new slot and
 * immediately enter Pick-on-Map mode.
 *
 * @param {number} lineIdx Index of the line in `POSS_FORM_LINES`.
 * @param {"from"|"to"} side Which side to pick for.
 */
window.pickNextPossessionLimit = function (lineIdx, side) {
  syncPossessionLinesFromDom();
  const ln = POSS_FORM_LINES[lineIdx];
  if (!ln) return;
  const arr = side === "from" ? ln.from_limits : ln.to_limits;
  let ptIdx = arr.findIndex((p) => !p.asset_id && !(p.label || "").trim());
  if (ptIdx === -1) {
    arr.push(createEmptyLimitPoint());
    ptIdx = arr.length - 1;
    renderPossessionLinesEditor();
  }
  startPossessionMapPick({ kind: "line_limit", idx: lineIdx, side, ptIdx });
};

window.removePossessionLimit = function (lineIdx, side, ptIdx) {
  syncPossessionLinesFromDom();
  const ln = POSS_FORM_LINES[lineIdx];
  if (!ln) return;
  const arr = side === "from" ? ln.from_limits : ln.to_limits;
  if (arr.length <= 1) return;
  arr.splice(ptIdx, 1);
  renderPossessionLinesEditor();
};

/**
 * Begin interactive "Pick on Map" mode so clicking any signal, points, buffer,
 * or track node on the TPS canvas populates the selected From/To or worksite field.
 *
 * @param {string|{kind:string, idx:number, side:string, ptIdx:number}} target Target spec.
 */
window.startPossessionMapPick = function (target) {
  STATE.possessionPickTarget = target;
  const banner = document.getElementById("poss-pick-banner");
  const txt = document.getElementById("poss-pick-banner-text");
  let msg = "Click a signal, points, buffer, or track node on the map...";
  if (target && typeof target === "object" && target.kind === "line_limit") {
    const lineNum = target.idx + 1;
    const sideLbl = target.side === "from" ? "From" : "To";
    msg = `Line ${lineNum} (${sideLbl} #${target.ptIdx + 1}): Click any Signal, Points, Buffer, or Node on the TPS map...`;
  } else if (target === "ws_start") {
    msg = "Click a Signal, Points, or Node for Worksite Start (WMB)...";
  } else if (target === "ws_end") {
    msg = "Click a Signal, Points, or Node for Worksite End (WMB)...";
  }
  if (txt) txt.textContent = msg;
  banner?.classList.remove("hidden");
};

function cancelPossessionMapPick() {
  STATE.possessionPickTarget = null;
  document.getElementById("poss-pick-banner")?.classList.add("hidden");
}

/**
 * Handle a map canvas click intercepted during Pick-on-Map mode.
 *
 * @param {object} candidate Best hit candidate from `handleCanvasClick`.
 */
async function handlePossessionMapPick(candidate) {
  const target = STATE.possessionPickTarget;
  if (!target || !candidate) return;
  cancelPossessionMapPick();

  const mappedType =
    candidate.type === "points"
      ? "points"
      : candidate.type === "signal"
      ? "signal"
      : "node";
  const rawTitle = candidate.title || `${mappedType} #${candidate.id}`;
  const isBuffer = /buffer/i.test(rawTitle);
  const cleanTitle = rawTitle.replace(/^(Signal|Points|Buffer Stop)\s+/i, "");

  if (target && typeof target === "object" && target.kind === "line_limit") {
    syncPossessionLinesFromDom();
    const ln = POSS_FORM_LINES[target.idx];
    if (!ln) return;
    const arr = target.side === "from" ? ln.from_limits : ln.to_limits;
    const pt = arr[target.ptIdx];
    if (!pt) return;
    pt.asset_type = mappedType;
    pt.asset_id = candidate.id;
    pt.label = cleanTitle;
    if (isBuffer && (!pt.qualifier || pt.qualifier === "at")) {
      pt.qualifier = "buffer";
    }
    renderPossessionLinesEditor();
    return;
  }

  if (target === "ws_start") {
    document.getElementById("poss-ws-start-type").value = mappedType;
    document.getElementById("poss-ws-start-id").value = candidate.id;
    document.getElementById("poss-ws-start-label").value = cleanTitle;
  } else if (target === "ws_end") {
    document.getElementById("poss-ws-end-type").value = mappedType;
    document.getElementById("poss-ws-end-id").value = candidate.id;
    document.getElementById("poss-ws-end-label").value = cleanTitle;
  }
}

/**
 * Fly the active TPS and/or NWR cameras to frame a saved possession.
 *
 * @param {number} possessionId Possession ID.
 */
window.focusPossession = function (possessionId) {
  const poss = (STATE.possessions || []).find((p) => p.id === possessionId);
  if (!poss) return;
  if (!STATE.visiblePossessionIds) STATE.visiblePossessionIds = new Set();
  STATE.visiblePossessionIds.add(poss.id);
  STATE.activePossessionId = poss.id;
  updatePossessionCounts();

  const tpsBox = poss.tps_geometry && poss.tps_geometry.bbox;
  if (tpsBox && tpsBox.length === 4 && (STATE.activeView === "tps" || STATE.activeView === "split")) {
    const cx = (tpsBox[0] + tpsBox[2]) / 2;
    const cy = (tpsBox[1] + tpsBox[3]) / 2;
    const span = Math.max(tpsBox[2] - tpsBox[0], tpsBox[3] - tpsBox[1], 300);
    const z = Math.min(2.2, Math.max(0.08, ((canvas?.clientWidth || 700) * 0.6) / span));
    flyToTps(cx, cy, z);
  }

  const nwrBox = poss.nwr_geometry && poss.nwr_geometry.bbox;
  if (nwrBox && nwrBox.length === 4 && (STATE.activeView === "nwr" || STATE.activeView === "split")) {
    const cx = (nwrBox[0] + nwrBox[2]) / 2;
    const cy = (nwrBox[1] + nwrBox[3]) / 2;
    const tc = STATE.activeView === "split" && nwrCanvas ? nwrCanvas : canvas;
    const span = Math.max(nwrBox[2] - nwrBox[0], nwrBox[3] - nwrBox[1], 300);
    const z = Math.min(0.45, Math.max(0.01, ((tc?.clientWidth || 600) * 0.6) / span));
    flyToNwr(cx, cy, z, true);
  }

  renderPossessionsList();
  requestRender();
};

function hydrateLineForEditor(ln) {
  const froms =
    Array.isArray(ln.from_limits) && ln.from_limits.length
      ? ln.from_limits.map((p) => ({
          asset_type: p.asset_type || "",
          asset_id: p.asset_id ?? "",
          label: p.label || "",
          qualifier: p.qualifier || "at",
          distance_meters: p.distance_meters ?? "",
        }))
      : [
          {
            asset_type: ln.start_asset_type || "",
            asset_id: ln.start_asset_id ?? "",
            label: ln.start_label || "",
            qualifier: ln.start_qualifier || "at",
            distance_meters: "",
          },
        ];
  const tos =
    Array.isArray(ln.to_limits) && ln.to_limits.length
      ? ln.to_limits.map((p) => ({
          asset_type: p.asset_type || "",
          asset_id: p.asset_id ?? "",
          label: p.label || "",
          qualifier: p.qualifier || "at",
          distance_meters: p.distance_meters ?? "",
        }))
      : [
          {
            asset_type: ln.end_asset_type || "",
            asset_id: ln.end_asset_id ?? "",
            label: ln.end_label || "",
            qualifier: ln.end_qualifier || "at",
            distance_meters: "",
          },
        ];
  return {
    line_name: ln.line_name || "",
    is_isolated: Boolean(ln.is_isolated),
    from_limits: froms,
    to_limits: tos,
  };
}

window.openEditPossessionForm = function (possessionId) {
  const poss = (STATE.possessions || []).find((p) => p.id === possessionId);
  if (!poss) return;
  document.getElementById("poss-ws-form")?.classList.add("hidden");
  document.getElementById("poss-form")?.classList.remove("hidden");
  document.getElementById("poss-form-title").textContent = `Edit Possession #${poss.id}`;
  document.getElementById("poss-edit-id").value = poss.id;
  document.getElementById("poss-won-ref").value = poss.won_ref || "";
  document.getElementById("poss-title").value = poss.title || "";
  document.getElementById("poss-picop-name").value = poss.picop_name || "";
  document.getElementById("poss-ole").value = poss.ole_isolation || "N/A";
  document.getElementById("poss-status").value = poss.status || "Planned";
  document.getElementById("poss-start-dt").value = poss.start_dt || "";
  document.getElementById("poss-end-dt").value = poss.end_dt || "";
  document.getElementById("poss-lines-blocked").value = poss.lines_blocked || "";
  document.getElementById("poss-color").value = poss.color || "#f59e0b";
  document.getElementById("poss-notes").value = poss.notes || "";

  if (Array.isArray(poss.lines) && poss.lines.length) {
    POSS_FORM_LINES = poss.lines.map(hydrateLineForEditor);
  } else {
    POSS_FORM_LINES = [
      hydrateLineForEditor({
        line_name: poss.lines_blocked || "",
        start_asset_type: poss.start_asset_type,
        start_asset_id: poss.start_asset_id,
        start_label: poss.start_label,
        start_qualifier: poss.start_qualifier,
        end_asset_type: poss.end_asset_type,
        end_asset_id: poss.end_asset_id,
        end_label: poss.end_label,
        end_qualifier: poss.end_qualifier,
      }),
    ];
  }
  renderPossessionLinesEditor();
};

window.openAddWorksiteForm = function (possessionId) {
  document.getElementById("poss-form")?.classList.add("hidden");
  const wsForm = document.getElementById("poss-ws-form");
  if (!wsForm) return;
  wsForm.reset();
  document.getElementById("poss-ws-parent-id").value = possessionId;
  document.getElementById("poss-ws-start-type").value = "";
  document.getElementById("poss-ws-start-id").value = "";
  document.getElementById("poss-ws-end-type").value = "";
  document.getElementById("poss-ws-end-id").value = "";
  wsForm.classList.remove("hidden");
};

window.deletePossession = async function (possessionId) {
  try {
    const res = await fetch(`/api/possessions/${possessionId}`, { method: "DELETE" });
    if (res.ok) {
      if (STATE.activePossessionId === possessionId) STATE.activePossessionId = null;
      await loadPossessions();
    }
  } catch (err) {
    console.error("Failed to delete possession:", err);
  }
};

window.deleteWorksite = async function (possessionId, worksiteId) {
  try {
    const res = await fetch(`/api/possessions/${possessionId}/worksites/${worksiteId}`, {
      method: "DELETE",
    });
    if (res.ok) {
      await loadPossessions();
    }
  } catch (err) {
    console.error("Failed to delete worksite:", err);
  }
};

function formatLineSummaryRow(ln) {
  const namePrefix = ln.line_name ? `<strong>${escapeHtml(ln.line_name)}:</strong> ` : "";
  const fromLabels =
    Array.isArray(ln.from_limits) && ln.from_limits.length
      ? ln.from_limits.map((p) => p.label || "Start").join(", ")
      : ln.start_label || "Start";
  const toLabels =
    Array.isArray(ln.to_limits) && ln.to_limits.length
      ? ln.to_limits.map((p) => p.label || "End").join(", ")
      : ln.end_label || "End";
  return `<div class="poss-card-line-row">${namePrefix}${escapeHtml(fromLabels)} → ${escapeHtml(toLabels)}</div>`;
}

function renderPossessionsList() {
  const listEl = document.getElementById("poss-list");
  if (!listEl) return;
  const items = STATE.possessions || [];
  if (!items.length) {
    listEl.innerHTML = `<p class="empty-state">No engineers' possessions recorded yet. Click <strong>+ New</strong> to map one.</p>`;
    return;
  }

  listEl.innerHTML = items
    .map((p) => {
      const statusCls =
        p.status === "Active"
          ? "status-active"
          : p.status === "Handed Back"
          ? "status-handed"
          : "status-planned";
      const oleCls =
        p.ole_isolation === "Earthed"
          ? "ole-earthed"
          : p.ole_isolation === "Isolated"
          ? "ole-isolated"
          : "";
      const hasMultiLines = Array.isArray(p.lines) && p.lines.length > 0;
      const linesHtml = hasMultiLines
        ? `<div class="poss-card-lines">${p.lines.map(formatLineSummaryRow).join("")}</div>`
        : `<div class="poss-card-meta">${escapeHtml(`${p.start_label || "Start"} → ${p.end_label || "End"}`)}</div>`;
      const wsHtml = (p.worksites || []).length
        ? `<div class="poss-ws-list">
            ${p.worksites
              .map(
                (ws) => `
              <div class="poss-ws-chip">
                <span>🔧 ${escapeHtml(ws.title)}${ws.es_name ? ` (ES: ${escapeHtml(ws.es_name)})` : ""}</span>
                <button type="button" class="btn btn-xs btn-secondary" onclick="event.stopPropagation(); deleteWorksite(${p.id}, ${ws.id})" title="Remove worksite">&times;</button>
              </div>`
              )
              .join("")}
          </div>`
        : "";

      const isVisible = STATE.visiblePossessionIds ? STATE.visiblePossessionIds.has(p.id) : false;
      const isStyleOpen = STATE.openPossessionStyleIds ? STATE.openPossessionStyleIds.has(p.id) : false;
      const rs = p.render_style || {};
      const pat = rs.pattern || "dashed";
      const thk = rs.thickness || "normal";
      const haloOn = rs.halo !== false;
      const haloOp = typeof rs.halo_opacity === "number" ? rs.halo_opacity : 0.3;
      const lblMode = rs.labels || "zoomed";
      const stylePanelHtml = isStyleOpen
        ? `<div class="poss-style-panel" onclick="event.stopPropagation()">
            <div class="poss-style-grid">
              <div class="poss-style-field">
                <span>Line Pattern</span>
                <select onchange="updatePossessionStyle(${p.id}, { pattern: this.value })">
                  <option value="dashed" ${pat === "dashed" ? "selected" : ""}>Dashed</option>
                  <option value="solid" ${pat === "solid" ? "selected" : ""}>Solid</option>
                  <option value="dotted" ${pat === "dotted" ? "selected" : ""}>Dotted</option>
                  <option value="double" ${pat === "double" ? "selected" : ""}>Double-stripe</option>
                </select>
              </div>
              <div class="poss-style-field">
                <span>Thickness</span>
                <select onchange="updatePossessionStyle(${p.id}, { thickness: this.value })">
                  <option value="thin" ${thk === "thin" ? "selected" : ""}>Thin</option>
                  <option value="normal" ${thk === "normal" ? "selected" : ""}>Normal</option>
                  <option value="bold" ${thk === "bold" ? "selected" : ""}>Bold</option>
                </select>
              </div>
            </div>
            <div class="poss-style-halo-row">
              <label style="flex-direction:row;align-items:center;gap:4px;cursor:pointer;">
                <input type="checkbox" ${haloOn ? "checked" : ""} onchange="updatePossessionStyle(${p.id}, { halo: this.checked })" />
                <span>Halo</span>
              </label>
              <input type="range" min="0" max="60" step="5" value="${Math.round(haloOp * 100)}" oninput="updatePossessionStyle(${p.id}, { halo_opacity: Number(this.value) / 100 })" title="Halo glow opacity" />
              <span class="poss-halo-val">${Math.round(haloOp * 100)}%</span>
            </div>
            <div class="poss-style-field">
              <span>PLB Marker Labels</span>
              <select onchange="updatePossessionStyle(${p.id}, { labels: this.value })">
                <option value="zoomed" ${lblMode === "zoomed" ? "selected" : ""}>Zoomed / Active</option>
                <option value="always" ${lblMode === "always" ? "selected" : ""}>Always Show</option>
                <option value="off" ${lblMode === "off" ? "selected" : ""}>Hidden</option>
              </select>
            </div>
          </div>`
        : "";

      return `
        <div class="poss-card ${STATE.activePossessionId === p.id ? "active" : ""}" data-poss-id="${p.id}" style="border-left-color:${escapeHtml(p.color || "#f59e0b")}" onclick="focusPossession(${p.id})">
          <div class="poss-card-top">
            <div class="poss-card-lead">
              <input type="checkbox" class="poss-vis-chk" ${isVisible ? "checked" : ""} onclick="event.stopPropagation()" onchange="togglePossessionVisibility(${p.id}, this.checked)" title="Show / hide on map views" />
              <input type="color" class="poss-card-swatch" value="${escapeHtml(p.color || "#f59e0b")}" onclick="event.stopPropagation()" oninput="updatePossessionStyle(${p.id}, { color: this.value })" title="Quick colour picker" />
              <span class="poss-card-title">${escapeHtml(p.title)}</span>
            </div>
            <span class="poss-pill ${statusCls}">${escapeHtml(p.status || "Planned")}</span>
          </div>
          ${p.won_ref ? `<div class="poss-card-meta"><strong>${escapeHtml(p.won_ref)}</strong></div>` : ""}
          ${linesHtml}
          <div class="poss-card-meta">
            ${p.picop_name ? `PICOP: ${escapeHtml(p.picop_name)} • ` : ""}${p.start_dt || p.end_dt ? `${escapeHtml(p.start_dt || "")} – ${escapeHtml(p.end_dt || "")}` : "Window TBC"}
          </div>
          <div class="poss-pill-row">
            ${hasMultiLines ? `<span class="poss-pill">${p.lines.length} Line(s)</span>` : ""}
            ${p.lines_blocked && !hasMultiLines ? `<span class="poss-pill">Lines: ${escapeHtml(p.lines_blocked)}</span>` : ""}
            <span class="poss-pill ${oleCls}">OLE: ${escapeHtml(p.ole_isolation || "N/A")}</span>
            <span class="poss-pill">${(p.worksites || []).length} Worksite(s)</span>
          </div>
          ${wsHtml}
          ${stylePanelHtml}
          <div class="poss-card-actions">
            <button type="button" class="btn btn-xs btn-secondary" onclick="event.stopPropagation(); openPossessionWonModal(${p.id})" title="View &amp; export Weekly Operating Notice">📄 WON</button>
            <button type="button" class="btn btn-xs ${isStyleOpen ? "btn-primary" : "btn-secondary"}" onclick="event.stopPropagation(); togglePossessionStylePanel(${p.id})">🎨 Style</button>
            <button type="button" class="btn btn-xs btn-secondary" onclick="event.stopPropagation(); openEditPossessionForm(${p.id})">Edit</button>
            <button type="button" class="btn btn-xs btn-accent" onclick="event.stopPropagation(); openAddWorksiteForm(${p.id})">+ Worksite (ES)</button>
            <button type="button" class="btn btn-xs btn-secondary" onclick="event.stopPropagation(); deletePossession(${p.id})">Delete</button>
          </div>
        </div>
      `;
    })
    .join("");
}

/* Wire Possessions Drawer DOM Events */
(function initPossessionsUi() {
  const drawer = document.getElementById("possessions-drawer");
  const form = document.getElementById("poss-form");
  const wsForm = document.getElementById("poss-ws-form");
  if (!drawer) return;

  document.getElementById("btn-possessions")?.addEventListener("click", () => {
    drawer.classList.toggle("hidden");
    if (!drawer.classList.contains("hidden")) {
      loadPossessions();
    }
  });
  document.getElementById("btn-close-possessions")?.addEventListener("click", () => {
    cancelPossessionMapPick();
    drawer.classList.add("hidden");
  });
  document.getElementById("btn-cancel-poss-pick")?.addEventListener("click", cancelPossessionMapPick);
  document.getElementById("btn-add-poss-line")?.addEventListener("click", addPossessionLine);
  document.getElementById("btn-poss-show-all")?.addEventListener("click", showAllPossessions);
  document.getElementById("btn-poss-hide-all")?.addEventListener("click", hideAllPossessions);

  // Weekly Operating Notice (WON) Preview & Export Modal
  let _activeWonId = null;
  let _activeWonFilename = "possession_WON.txt";
  const wonModal = document.getElementById("poss-won-modal");
  const wonPre = document.getElementById("poss-won-text");
  const wonTitle = document.getElementById("poss-won-modal-title");
  const wonHeaderChk = document.getElementById("chk-won-include-header");

  async function fetchAndRenderWon() {
    if (!_activeWonId || !wonPre) return;
    const inc = wonHeaderChk ? wonHeaderChk.checked : true;
    wonPre.textContent = "Generating Weekly Operating Notice...";
    try {
      const res = await fetch(`/api/possessions/${_activeWonId}/won?include_header=${inc ? "true" : "false"}`);
      if (!res.ok) return;
      const data = await res.json();
      _activeWonFilename = data.filename || `possession_${_activeWonId}_WON.txt`;
      if (wonTitle) wonTitle.textContent = `WON Preview — ${data.title || `#${_activeWonId}`}`;
      wonPre.textContent = data.text || "";
    } catch (err) {
      console.error("Failed to fetch WON text:", err);
      wonPre.textContent = "Failed to generate WON text.";
    }
  }

  window.openPossessionWonModal = function (possessionId) {
    _activeWonId = possessionId;
    wonModal?.classList.remove("hidden");
    fetchAndRenderWon();
  };

  wonHeaderChk?.addEventListener("change", fetchAndRenderWon);
  document.getElementById("btn-close-won-modal")?.addEventListener("click", () => {
    wonModal?.classList.add("hidden");
  });
  wonModal?.addEventListener("click", (e) => {
    if (e.target === wonModal) wonModal.classList.add("hidden");
  });
  document.getElementById("btn-copy-won")?.addEventListener("click", async () => {
    if (!wonPre) return;
    try {
      await navigator.clipboard.writeText(wonPre.textContent || "");
      const btn = document.getElementById("btn-copy-won");
      if (btn) {
        const orig = btn.textContent;
        btn.textContent = "✓ Copied!";
        setTimeout(() => {
          btn.textContent = orig;
        }, 1500);
      }
    } catch (err) {
      console.error("Copy failed:", err);
    }
  });
  document.getElementById("btn-download-won")?.addEventListener("click", () => {
    if (!wonPre) return;
    const blob = new Blob([wonPre.textContent || ""], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = _activeWonFilename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  });

  document.getElementById("btn-new-possession")?.addEventListener("click", () => {
    wsForm?.classList.add("hidden");
    form?.reset();
    document.getElementById("poss-edit-id").value = "";
    document.getElementById("poss-form-title").textContent = "New Engineers' Possession";
    POSS_FORM_LINES = [createEmptyPossessionLine()];
    renderPossessionLinesEditor();
    form?.classList.remove("hidden");
  });

  document.getElementById("btn-cancel-poss-form")?.addEventListener("click", () => {
    cancelPossessionMapPick();
    form?.classList.add("hidden");
  });
  document.getElementById("btn-cancel-ws-form")?.addEventListener("click", () => {
    cancelPossessionMapPick();
    wsForm?.classList.add("hidden");
  });

  // Post-save Isolated Tracks suggestion prompt
  let _pendingIsolatedPoss = null;
  let _pendingIsolatedItems = [];
  const isoPrompt = document.getElementById("poss-isolated-prompt");
  const isoList = document.getElementById("poss-isolated-list");

  function hideIsolatedPrompt() {
    _pendingIsolatedPoss = null;
    _pendingIsolatedItems = [];
    isoPrompt?.classList.add("hidden");
  }

  function showIsolatedPrompt(savedPoss, suggestions) {
    if (!isoPrompt || !isoList || !Array.isArray(suggestions) || !suggestions.length) {
      hideIsolatedPrompt();
      return;
    }
    _pendingIsolatedPoss = savedPoss;
    _pendingIsolatedItems = suggestions;
    isoList.innerHTML = suggestions
      .map((s, i) => {
        const fromLbl = (s.from_limits || []).map((p) => p.label || "Start").join(", ");
        const toLbl = (s.to_limits || []).map((p) => p.label || "End").join(", ");
        return `
          <label class="poss-isolated-item">
            <input type="checkbox" class="poss-iso-chk" data-idx="${i}" checked />
            <span>
              <strong>${escapeHtml(s.line_name || `Isolated Line ${i + 1}`)}</strong>
              <small>${escapeHtml(fromLbl)} → ${escapeHtml(toLbl)}</small>
            </span>
          </label>`;
      })
      .join("");
    isoPrompt.classList.remove("hidden");
  }

  document.getElementById("btn-dismiss-isolated")?.addEventListener("click", hideIsolatedPrompt);
  document.getElementById("btn-skip-isolated")?.addEventListener("click", hideIsolatedPrompt);
  document.getElementById("btn-accept-isolated")?.addEventListener("click", async () => {
    if (!_pendingIsolatedPoss || !isoList) return;
    const chosen = Array.from(isoList.querySelectorAll(".poss-iso-chk:checked"))
      .map((c) => _pendingIsolatedItems[Number(c.dataset.idx)])
      .filter(Boolean)
      .map((s) => ({
        line_name: s.line_name || null,
        is_isolated: true,
        limit_mode: "signal_points",
        from_limits: s.from_limits || [],
        to_limits: s.to_limits || [],
      }));
    if (!chosen.length) {
      hideIsolatedPrompt();
      return;
    }
    const p = _pendingIsolatedPoss;
    const existingLines = Array.isArray(p.lines) ? p.lines : [];
    const mergedLines = [...existingLines, ...chosen];
    const payload = {
      won_ref: p.won_ref || null,
      title: p.title,
      picop_name: p.picop_name || null,
      picop_contact: p.picop_contact || null,
      start_dt: p.start_dt || null,
      end_dt: p.end_dt || null,
      lines_blocked: null,
      ole_isolation: p.ole_isolation || "N/A",
      status: p.status || "Planned",
      color: p.color || "#f59e0b",
      notes: p.notes || null,
      lines: mergedLines,
      render_style: p.render_style || {},
    };
    hideIsolatedPrompt();
    try {
      const res = await fetch(`/api/possessions/${p.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const updated = await res.json();
        await loadPossessions();
        if (updated && updated.id) focusPossession(updated.id);
      }
    } catch (err) {
      console.error("Failed to add isolated lines:", err);
    }
  });

  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    syncPossessionLinesFromDom();
    const editId = document.getElementById("poss-edit-id").value;
    const toOptInt = (v) => (v !== undefined && v !== null && String(v).trim() !== "" ? Number(v) : null);

    const serializeLimit = (p) => ({
      asset_type: (p.asset_type || "").trim() || null,
      asset_id: toOptInt(p.asset_id),
      label: (p.label || "").trim() || null,
      qualifier: p.qualifier || "at",
      distance_meters: toOptInt(p.distance_meters),
    });

    const linesPayload = POSS_FORM_LINES.map((ln) => {
      const froms = (ln.from_limits || [])
        .map(serializeLimit)
        .filter((p) => p.asset_id !== null || p.label);
      const tos = (ln.to_limits || [])
        .map(serializeLimit)
        .filter((p) => p.asset_id !== null || p.label);
      return {
        line_name: (ln.line_name || "").trim() || null,
        is_isolated: Boolean(ln.is_isolated),
        limit_mode: "signal_points",
        from_limits: froms,
        to_limits: tos,
      };
    });

    const existingPoss = editId
      ? (STATE.possessions || []).find((p) => String(p.id) === String(editId))
      : null;
    const payload = {
      won_ref: document.getElementById("poss-won-ref").value.trim() || null,
      title: document.getElementById("poss-title").value.trim(),
      picop_name: document.getElementById("poss-picop-name").value.trim() || null,
      start_dt: document.getElementById("poss-start-dt").value.trim() || null,
      end_dt: document.getElementById("poss-end-dt").value.trim() || null,
      lines_blocked: document.getElementById("poss-lines-blocked").value.trim() || null,
      ole_isolation: document.getElementById("poss-ole").value,
      status: document.getElementById("poss-status").value,
      color: document.getElementById("poss-color").value || "#f59e0b",
      notes: document.getElementById("poss-notes").value.trim() || null,
      lines: linesPayload,
      render_style: existingPoss?.render_style || {},
    };

    const url = editId ? `/api/possessions/${editId}` : "/api/possessions";
    const method = editId ? "PUT" : "POST";
    try {
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const saved = await res.json();
        form.classList.add("hidden");
        await loadPossessions();
        if (saved && saved.id) focusPossession(saved.id);
        if (saved && Array.isArray(saved.isolated_suggestions) && saved.isolated_suggestions.length) {
          showIsolatedPrompt(saved, saved.isolated_suggestions);
        } else {
          hideIsolatedPrompt();
        }
      }
    } catch (err) {
      console.error("Failed to save possession:", err);
    }
  });

  wsForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const pid = document.getElementById("poss-ws-parent-id").value;
    if (!pid) return;
    const parseOptInt = (id) => {
      const v = document.getElementById(id)?.value;
      return v !== undefined && v !== "" ? Number(v) : null;
    };
    const payload = {
      title: document.getElementById("poss-ws-title").value.trim(),
      es_name: document.getElementById("poss-ws-es").value.trim() || null,
      wmb_start_label: document.getElementById("poss-ws-start-label").value.trim() || null,
      wmb_end_label: document.getElementById("poss-ws-end-label").value.trim() || null,
      start_asset_type: document.getElementById("poss-ws-start-type").value || null,
      start_asset_id: parseOptInt("poss-ws-start-id"),
      end_asset_type: document.getElementById("poss-ws-end-type").value || null,
      end_asset_id: parseOptInt("poss-ws-end-id"),
      start_meters: parseOptInt("poss-ws-start-m"),
      end_meters: parseOptInt("poss-ws-end-m"),
    };
    try {
      const res = await fetch(`/api/possessions/${pid}/worksites`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        wsForm.classList.add("hidden");
        await loadPossessions();
      }
    } catch (err) {
      console.error("Failed to add worksite:", err);
    }
  });
})();
