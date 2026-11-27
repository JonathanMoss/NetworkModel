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
  const cnt = (STATE.possessions || []).length;
  const el1 = document.getElementById("cnt-possessions");
  const el2 = document.getElementById("cnt-nwr-possessions");
  if (el1) el1.textContent = String(cnt);
  if (el2) el2.textContent = String(cnt);
}

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
      <select class="pl-lim-qual" title="Limit qualifier">
        <option value="at" ${pt.qualifier === "at" ? "selected" : ""}>At</option>
        <option value="approach" ${pt.qualifier === "approach" ? "selected" : ""}>Approach</option>
        <option value="beyond" ${pt.qualifier === "beyond" ? "selected" : ""}>Beyond</option>
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
  STATE.activePossessionId = poss.id;

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
        }))
      : [
          {
            asset_type: ln.start_asset_type || "",
            asset_id: ln.start_asset_id ?? "",
            label: ln.start_label || "",
            qualifier: ln.start_qualifier || "at",
          },
        ];
  const tos =
    Array.isArray(ln.to_limits) && ln.to_limits.length
      ? ln.to_limits.map((p) => ({
          asset_type: p.asset_type || "",
          asset_id: p.asset_id ?? "",
          label: p.label || "",
          qualifier: p.qualifier || "at",
        }))
      : [
          {
            asset_type: ln.end_asset_type || "",
            asset_id: ln.end_asset_id ?? "",
            label: ln.end_label || "",
            qualifier: ln.end_qualifier || "at",
          },
        ];
  return {
    line_name: ln.line_name || "",
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

      return `
        <div class="poss-card ${STATE.activePossessionId === p.id ? "active" : ""}" style="border-left-color:${escapeHtml(p.color || "#f59e0b")}" onclick="focusPossession(${p.id})">
          <div class="poss-card-top">
            <span class="poss-card-title">${escapeHtml(p.title)}</span>
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
          <div class="poss-card-actions">
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
        limit_mode: "signal_points",
        from_limits: froms,
        to_limits: tos,
      };
    });

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
