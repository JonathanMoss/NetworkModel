/**
 * @fileoverview Label control UI bindings, Database Catalog modal, and
 * Timing Loads / Traction Physics / Timetable Corridors modal.
 */

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
  if (STATE.activeView === "nwr") {
    STATE.nwrCam.zoom = Math.min(5.0, STATE.nwrCam.zoom * 1.45);
    STATE.zoom = STATE.nwrCam.zoom;
  } else if (STATE.activeView === "split") {
    if (STATE.dragTarget === "nwr") {
      STATE.nwrCam.zoom = Math.min(5.0, STATE.nwrCam.zoom * 1.45);
    } else {
      STATE.tpsCam.zoom = Math.min(25.0, STATE.tpsCam.zoom * 1.45);
    }
  } else {
    STATE.tpsCam.zoom = Math.min(25.0, STATE.tpsCam.zoom * 1.45);
    STATE.zoom = STATE.tpsCam.zoom;
  }
  scheduleViewportFetch();
  requestRender();
});
document.getElementById("btn-zoom-out").addEventListener("click", () => {
  if (STATE.activeView === "nwr") {
    STATE.nwrCam.zoom = Math.max(0.0004, STATE.nwrCam.zoom / 1.45);
    STATE.zoom = STATE.nwrCam.zoom;
  } else if (STATE.activeView === "split") {
    if (STATE.dragTarget === "nwr") {
      STATE.nwrCam.zoom = Math.max(0.0004, STATE.nwrCam.zoom / 1.45);
    } else {
      STATE.tpsCam.zoom = Math.max(0.008, STATE.tpsCam.zoom / 1.45);
    }
  } else {
    STATE.tpsCam.zoom = Math.max(0.008, STATE.tpsCam.zoom / 1.45);
    STATE.zoom = STATE.tpsCam.zoom;
  }
  scheduleViewportFetch();
  requestRender();
});
document.getElementById("btn-clear-highlight").addEventListener("click", () => {
  STATE.highlight = { ways: [], points: [], segments: [] };
  STATE.selectedAsset = null;
  STATE.crossHighlight.tps.active = false;
  STATE.crossHighlight.nwr.active = false;
  document.getElementById("tps-sync-tag")?.classList.add("hidden");
  document.getElementById("nwr-sync-tag")?.classList.add("hidden");
  document.getElementById("btn-clear-highlight").classList.add("hidden");
  closeProfileDrawer(true);
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
