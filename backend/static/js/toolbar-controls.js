/**
 * @fileoverview Top-bar global search, Colour-By dropdowns, TPS/NWR layer
 * visibility checkboxes, OSM opacity slider, and layer line-styling controls.
 */

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

/* Colour-By Mode Dropdowns */
document.getElementById("color-mode").addEventListener("change", (e) => {
  STATE.colorMode = e.target.value;
  savePersistedColorModes();
  updateLegend();
  scheduleViewportFetch();
  requestRender();
});

document.getElementById("nwr-color-mode").addEventListener("change", (e) => {
  STATE.nwrColorMode = e.target.value;
  savePersistedColorModes();
  updateLegend();
  requestRender();
});

/* Layer Visibility Checkboxes */
for (const key of TPS_LAYER_KEYS) {
  const el = document.getElementById(`layer-${key}`);
  if (el) {
    el.addEventListener("change", () => {
      STATE.layers[key] = el.checked;
      savePersistedLayerToggles();
      scheduleViewportFetch();
      requestRender();
    });
  }
}

for (const [key, id] of NWR_LAYER_MAP) {
  const el = document.getElementById(id);
  if (el) {
    el.addEventListener("change", () => {
      STATE.nwrLayers[key] = el.checked;
      savePersistedLayerToggles();
      if (key === "osm") {
        savePersistedOsmSettings();
        requestRender();
      } else {
        scheduleViewportFetch();
        requestRender();
      }
    });
  }
}

/* OpenStreetMap Opacity Slider */
const osmOpacitySlider = document.getElementById("osm-opacity-slider");
if (osmOpacitySlider) {
  osmOpacitySlider.addEventListener("input", (e) => {
    STATE.osmOpacity = parseInt(e.target.value, 10) || 0;
    const readout = document.getElementById("osm-opacity-readout");
    if (readout) readout.textContent = `${STATE.osmOpacity}%`;
    savePersistedOsmSettings();
    requestRender();
  });
}

/* Layer Line Styling Controls (Colour Picker, Width Stepper, Reset) */
document.querySelectorAll(".layer-color-input").forEach((inp) => {
  const onColorChange = (e) => {
    const layer = inp.dataset.layer;
    if (!layer || !STATE.layerStyles[layer]) return;
    STATE.layerStyles[layer].color = e.target.value;
    const swatch = document.getElementById(`swatch-${layer.replace(/_/g, "-")}`);
    if (swatch) swatch.style.backgroundColor = e.target.value;
    const dot = document.getElementById(`swatch-dot-${layer.replace(/_/g, "-")}`);
    if (dot) dot.style.backgroundColor = e.target.value;
    savePersistedLayerStyles();
    updateLegend();
    requestRender();
  };
  inp.addEventListener("input", onColorChange);
  inp.addEventListener("change", onColorChange);
});

document.querySelectorAll(".width-stepper .width-step-btn").forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const stepper = btn.closest(".width-stepper");
    const layer = stepper.dataset.layer;
    if (!layer || !STATE.layerStyles[layer]) return;
    const dir = parseFloat(btn.dataset.dir) || 0;
    const cur = STATE.layerStyles[layer].width || 1.5;
    const next = Math.max(0.5, Math.min(8.0, Math.round((cur + dir) * 10) / 10));
    STATE.layerStyles[layer].width = next;
    const readout = stepper.querySelector(".width-readout");
    if (readout) readout.textContent = `${next.toFixed(1)}px`;
    savePersistedLayerStyles();
    requestRender();
  });
});

document.querySelectorAll(".btn-reset-layer-style").forEach((btn) => {
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    const layer = btn.dataset.layer;
    if (!layer || !DEFAULT_LAYER_STYLES[layer]) return;
    STATE.layerStyles[layer] = { ...DEFAULT_LAYER_STYLES[layer] };
    savePersistedLayerStyles();
    updateLayerStyleUi();
    updateLegend();
    requestRender();
  });
});

const btnResetTps = document.getElementById("btn-reset-tps-styles");
if (btnResetTps) {
  btnResetTps.addEventListener("click", () => resetLayerStyles("tps"));
}

const btnResetNwr = document.getElementById("btn-reset-nwr-styles");
if (btnResetNwr) {
  btnResetNwr.addEventListener("click", () => resetLayerStyles("nwr"));
}

