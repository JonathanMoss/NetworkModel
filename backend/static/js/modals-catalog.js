/**
 * @fileoverview Map label controls, zoom/clear-highlight buttons, quick
 * hub navigation, and Full Database Catalog modal.
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
  STATE.highlightGroupFilter = "none";
  STATE.crossHighlight.tps.active = false;
  STATE.crossHighlight.nwr.active = false;
  document.getElementById("tps-sync-tag")?.classList.add("hidden");
  document.getElementById("nwr-sync-tag")?.classList.add("hidden");
  document.getElementById("btn-clear-highlight").classList.add("hidden");

  // Reset Signalling Envelope filter controls so re-selecting any route triggers onchange
  document.querySelectorAll(".sig-filter-btn, .sig-filter-control").forEach((el) => {
    el.classList.remove("active");
    const sel = el.querySelector(".sig-filter-select");
    const textSpan = el.querySelector(".sig-filter-text");
    if (sel && sel.options.length > 0) {
      sel.selectedIndex = 0;
    }
    if (textSpan && textSpan.dataset.default) {
      textSpan.textContent = textSpan.dataset.default;
    }
  });

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
