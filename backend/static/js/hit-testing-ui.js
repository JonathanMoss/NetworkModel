/**
 * @fileoverview Hover cursor feedback, candidate formatting/deduplication,
 * and nearby-candidates picker popover UI.
 */

/* Hover Detection & Precision Pointer Cursor (No floating tooltip) */
function handleHover(sx, sy, wx, wy, targetView = null) {
  const view = targetView || (STATE.activeView === "nwr" ? "nwr" : "tps");
  const targetCanvas = view === "nwr" && nwrCanvas ? nwrCanvas : canvas;
  if (
    sx < 0 ||
    sy < 0 ||
    sx > targetCanvas.clientWidth ||
    sy > targetCanvas.clientHeight
  ) {
    targetCanvas.classList.remove("has-target");
    return;
  }

  const hits = findObjectsAtCursor(sx, sy, wx, wy, view);
  if (hits.length > 0) {
    targetCanvas.classList.add("has-target");
    STATE.hoverCandidates = hits;
  } else {
    targetCanvas.classList.remove("has-target");
    STATE.hoverCandidates = [];
  }
}

/* Candidate Display & Interaction Helpers */
function getAssetIcon(type) {
  switch (type) {
    case "points": return "🔀";
    case "signal": return "🚦";
    case "station": return "🚉";
    case "platform": return "🚏";
    case "track":
    case "nwr_track": return "🛤️";
    case "block": return "🔲";
    case "node":
    case "nwr_node": return "📍";
    case "nwr_elr": return "🏷️";
    case "nwr_milepost": return "📍";
    default: return "🔹";
  }
}

function formatAssetTypeName(type) {
  switch (type) {
    case "points": return "Points";
    case "signal": return "Signal";
    case "station": return "Station";
    case "platform": return "Platform";
    case "track": return "Track";
    case "block": return "Block";
    case "node": return "Node";
    case "nwr_track": return "NWR Track";
    case "nwr_node": return "NWR Node";
    case "nwr_elr": return "ELR";
    case "nwr_milepost": return "Milepost";
    default: return String(type).replace("nwr_", "").toUpperCase();
  }
}

function formatAssetSubtitle(c) {
  if (c.sub) return c.sub;
  if (c.type === "points") return `Points ID #${c.id}`;
  if (c.type === "signal") return `Signal ID #${c.id}`;
  if (c.type === "track") return `Track ID #${c.id}`;
  if (c.type === "block") return `Block #${c.id}`;
  if (c.type === "station") return `Station #${c.id}`;
  if (c.type === "node") return `Node #${c.id}`;
  if (c.x !== undefined && c.y !== undefined) return `(${Math.round(c.x)}, ${Math.round(c.y)})`;
  return `ID #${c.id}`;
}

function mergeCandidates(clientHits, serverCandidates) {
  const byKey = new Map();

  // Add client hits first (they have exact canvas coordinates, label bounding boxes, etc.)
  for (const c of clientHits || []) {
    const key = `${c.type}:${c.id}`;
    byKey.set(key, { ...c });
  }

  // Augment with server candidates
  for (const s of serverCandidates || []) {
    let key = `${s.type}:${s.id}`;
    let existing = byKey.get(key);
    if (!existing && (s.type === "signal" || s.type === "points")) {
      const altKey = s.type === "signal" ? `points:${s.id}` : `signal:${s.id}`;
      const altExisting = byKey.get(altKey);
      if (altExisting) {
        if (s.type === "points") {
          byKey.delete(altKey);
          altExisting.type = "points";
          byKey.set(key, altExisting);
          existing = altExisting;
        } else {
          existing = altExisting;
        }
      }
    }
    if (!existing) {
      byKey.set(key, {
        type: s.type,
        id: s.id,
        title: s.label || `${s.type} #${s.id}`,
        sub: s.sub || "",
        x: s.x,
        y: s.y,
        previewWay: s.coords || null,
        previewParts: s.parts || null,
      });
    } else {
      if (!existing.previewWay && s.coords) existing.previewWay = s.coords;
      if (!existing.previewParts && s.parts) existing.previewParts = s.parts;
      if (!existing.sub && s.sub) existing.sub = s.sub;
      if (existing.x === undefined && s.x !== undefined) existing.x = s.x;
      if (existing.y === undefined && s.y !== undefined) existing.y = s.y;
    }
  }

  const list = Array.from(byKey.values());

  const typeOrder = {
    points: 1,
    signal: 1.5,
    station: 2,
    platform: 3,
    block: 4,
    track: 5,
    nwr_track: 5,
    nwr_elr: 6,
    nwr_milepost: 7,
    node: 8,
    nwr_node: 8,
    edge: 9,
  };

  list.sort((a, b) => {
    const pa = typeOrder[a.type] || 99;
    const pb = typeOrder[b.type] || 99;
    return pa - pb;
  });

  return list;
}

function renderPickCandidates(cands, activeItem) {
  const candBar = document.getElementById("pick-candidates");
  if (!candBar) return;
  if (!cands || cands.length <= 1) {
    candBar.classList.add("hidden");
    candBar.innerHTML = "";
    return;
  }

  const activeKey = activeItem
    ? `${activeItem.type}:${activeItem.id}`
    : `${cands[0].type}:${cands[0].id}`;

  candBar.innerHTML = `
    <div class="pick-candidates-header">
      <div class="pick-candidates-title-group">
        <span class="pick-candidates-badge">${cands.length}</span>
        <span class="pick-candidates-heading">Overlapping Objects at Click</span>
      </div>
      <span class="pick-candidates-hint">Hover to highlight • Click to inspect</span>
    </div>
    <div class="pick-candidates-list">
      ${cands
        .map((c, i) => {
          const key = `${c.type}:${c.id}`;
          const isActive = key === activeKey;
          const icon = getAssetIcon(c.type);
          const typeName = formatAssetTypeName(c.type);
          const subtitle = c.sub || formatAssetSubtitle(c);
          return `
            <button class="candidate-row ${isActive ? "active" : ""}" data-idx="${i}" data-type="${escapeHtml(c.type)}" data-id="${escapeHtml(String(c.id))}">
              <span class="candidate-icon candidate-icon-${escapeHtml(c.type)}">${icon}</span>
              <div class="candidate-info">
                <div class="candidate-main">
                  <span class="candidate-type-badge candidate-type-${escapeHtml(c.type)}">${escapeHtml(typeName)}</span>
                  <span class="candidate-label" title="${escapeHtml(c.title || c.label)}">${escapeHtml(c.title || c.label)}</span>
                </div>
                ${subtitle ? `<div class="candidate-sub" title="${escapeHtml(subtitle)}">${escapeHtml(subtitle)}</div>` : ""}
              </div>
              <span class="candidate-arrow">&rarr;</span>
            </button>
          `;
        })
        .join("")}
    </div>
  `;

  candBar.classList.remove("hidden");

  candBar.querySelectorAll(".candidate-row").forEach((btn) => {
    const idx = parseInt(btn.dataset.idx, 10);
    const item = cands[idx];
    btn.addEventListener("mouseenter", () => {
      if (item) {
        STATE.hoverPreview = item;
        requestRender();
      }
    });
    btn.addEventListener("mouseleave", () => {
      if (STATE.hoverPreview === item) {
        STATE.hoverPreview = null;
        requestRender();
      }
    });
    btn.addEventListener("click", () => {
      candBar.querySelectorAll(".candidate-row").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      loadAssetDetails(btn.dataset.type, btn.dataset.id, false);
    });
  });
}
