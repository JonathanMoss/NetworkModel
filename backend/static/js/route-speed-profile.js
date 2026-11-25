/**
 * @fileoverview Inspector "Route Speed Profile" card and inline SVG step-chart.
 *
 * Displays base permissible speed steps (mph & km/h), governing/min & peak max
 * speeds, and differential velocity profiles (e.g. MU, HST, EPS) for any
 * signal-to-signal, signal-to-buffer, block, or station route.
 */

/**
 * Convert km/h to rounded mph (1 decimal place, or integer when exact).
 *
 * @param {number|null|undefined} kmh Speed in km/h.
 * @returns {number|null} Speed in mph, or null if unavailable.
 */
function kmhToMph(kmh) {
  if (kmh === null || kmh === undefined || kmh <= 0) return null;
  return Math.round(kmh * 0.621371 * 10) / 10;
}

/**
 * Pick a colour matching the UK rail speed tier (in mph).
 *
 * @param {number} mph Speed in mph.
 * @returns {string} Hex colour string.
 */
function getSpeedTierColor(mph) {
  if (!mph || mph <= 0) return "#64748b";
  if (mph <= 25) return "#ef4444";
  if (mph <= 50) return "#f59e0b";
  if (mph <= 75) return "#38bdf8";
  if (mph <= 100) return "#10b981";
  return "#a855f7";
}

/**
 * Collect normalized route speed-profile entries from any supported asset payload
 * (`signal`, `points`, `block`, or `route`).
 *
 * @param {object} data Asset detail payload returned by `/api/asset/{type}/{id}`.
 * @returns {Array<object>} Ordered list of route profile descriptors.
 */
function extractRoutesForSpeedProfile(data) {
  if (!data || !data.related) return [];
  const rel = data.related;
  const routes = [];

  if (data.asset_type === "signal" || data.asset_type === "points") {
    for (const blk of rel.outgoing_blocks || []) {
      if (blk.speed_profile && blk.speed_profile.length) {
        routes.push({
          key: `outgoing:${blk.block_id}`,
          kind: "Protected (Advance)",
          label: `Protected: ${blk.route_label || `Block #${blk.block_id}`}`,
          length_meters: blk.length_meters || 0,
          min_vmax: blk.min_vmax,
          max_vmax: blk.max_vmax,
          speed_profile: blk.speed_profile,
        });
      }
    }
    for (const blk of rel.approach_blocks || []) {
      if (blk.speed_profile && blk.speed_profile.length) {
        routes.push({
          key: `approach:${blk.block_id}`,
          kind: "Approach (Rear)",
          label: `Approach: ${blk.route_label || `Block #${blk.block_id}`}`,
          length_meters: blk.length_meters || 0,
          min_vmax: blk.min_vmax,
          max_vmax: blk.max_vmax,
          speed_profile: blk.speed_profile,
        });
      }
    }
  } else if (
    (data.asset_type === "block" || data.asset_type === "route") &&
    rel.speed_profile &&
    rel.speed_profile.length
  ) {
    routes.push({
      key: `${data.asset_type}:${data.id}`,
      kind: data.asset_type === "block" ? "Signalling Block" : "Station Route",
      label: data.title || `${data.asset_type} #${data.id}`,
      length_meters: rel.length_meters || 0,
      min_vmax: rel.min_vmax,
      max_vmax: rel.max_vmax,
      speed_profile: rel.speed_profile,
    });
  }

  return routes;
}

/**
 * Render a responsive inline SVG step-chart of base permissible speed (mph)
 * versus distance (m) along a route.
 *
 * @param {Array<object>} steps Merged speed_profile steps.
 * @param {number} totalLengthM Total route length in metres.
 * @returns {string} HTML/SVG markup string.
 */
function renderRouteSpeedStepSvg(steps, totalLengthM) {
  const W = 340;
  const H = 112;
  const padL = 34;
  const padR = 10;
  const padT = 12;
  const padB = 22;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const maxDist = Math.max(
    1,
    totalLengthM || (steps.length ? steps[steps.length - 1].end_m : 1)
  );
  const peakMph = Math.max(
    25,
    ...steps.map((s) => Number(s.vmax_mph || 0))
  );
  // Round Y-axis top up to a clean 25 mph tick.
  const yMax = Math.ceil(peakMph / 25) * 25;

  const xOf = (m) => padL + (Math.min(maxDist, Math.max(0, m)) / maxDist) * plotW;
  const yOf = (mph) => padT + plotH - (Math.min(yMax, Math.max(0, mph)) / yMax) * plotH;

  // Horizontal gridlines (0, mid, max)
  const ticks = [0, Math.round(yMax / 2), yMax];
  const gridSvg = ticks
    .map((tk) => {
      const y = yOf(tk).toFixed(1);
      return `
        <line x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}" stroke="rgba(148,163,184,0.18)" stroke-dasharray="2,2" />
        <text x="${padL - 4}" y="${(Number(y) + 3).toFixed(1)}" text-anchor="end" fill="#94a3b8" font-size="8.5">${tk}</text>
      `;
    })
    .join("");

  // Step bars + top stroke segments
  const barsSvg = steps
    .map((s) => {
      const x0 = xOf(s.start_m);
      const x1 = xOf(s.end_m);
      const bw = Math.max(2, x1 - x0);
      const mph = Number(s.vmax_mph || 0);
      const yTop = yOf(mph);
      const yBot = yOf(0);
      const col = getSpeedTierColor(mph);
      const showLabel = bw >= 30 && mph > 0;
      return `
        <rect x="${x0.toFixed(1)}" y="${yTop.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, yBot - yTop).toFixed(1)}" fill="${col}" fill-opacity="0.22" />
        <line x1="${x0.toFixed(1)}" y1="${yTop.toFixed(1)}" x2="${(x0 + bw).toFixed(1)}" y2="${yTop.toFixed(1)}" stroke="${col}" stroke-width="2.2" />
        ${
          showLabel
            ? `<text x="${(x0 + bw / 2).toFixed(1)}" y="${Math.max(padT + 8, yTop - 3).toFixed(1)}" text-anchor="middle" fill="#f8fafc" font-size="8.5" font-weight="700">${Math.round(mph)}</text>`
            : ""
        }
      `;
    })
    .join("");

  // Vertical transition risers between adjacent steps
  let risersSvg = "";
  for (let i = 1; i < steps.length; i++) {
    const prev = steps[i - 1];
    const curr = steps[i];
    const x = xOf(curr.start_m).toFixed(1);
    const y1 = yOf(Number(prev.vmax_mph || 0)).toFixed(1);
    const y2 = yOf(Number(curr.vmax_mph || 0)).toFixed(1);
    risersSvg += `<line x1="${x}" y1="${y1}" x2="${x}" y2="${y2}" stroke="#cbd5e1" stroke-width="1.2" stroke-opacity="0.7" />`;
  }

  return `
    <svg class="rsp-svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Route speed profile chart (mph vs metres)">
      ${gridSvg}
      ${barsSvg}
      ${risersSvg}
      <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + plotH}" stroke="rgba(148,163,184,0.4)" />
      <line x1="${padL}" y1="${padT + plotH}" x2="${W - padR}" y2="${padT + plotH}" stroke="rgba(148,163,184,0.4)" />
      <text x="${padL}" y="${H - 5}" text-anchor="start" fill="#94a3b8" font-size="8.5">0 m</text>
      <text x="${(padL + plotW / 2).toFixed(1)}" y="${H - 5}" text-anchor="middle" fill="#94a3b8" font-size="8.5">${Math.round(maxDist / 2)} m</text>
      <text x="${W - padR}" y="${H - 5}" text-anchor="end" fill="#94a3b8" font-size="8.5">${Math.round(maxDist)} m</text>
      <text x="8" y="${padT + 6}" fill="#64748b" font-size="7.5">mph</text>
    </svg>
  `;
}

/**
 * Render the compact segment breakdown table for a route's speed steps.
 *
 * @param {Array<object>} steps Merged speed_profile steps.
 * @returns {string} HTML table markup.
 */
function renderRouteSpeedStepsTable(steps) {
  const rows = steps
    .map((s) => {
      const mph = s.vmax_mph > 0 ? `${Math.round(s.vmax_mph)} mph` : "—";
      const kmh = s.vmax_kmh > 0 ? `${s.vmax_kmh} km/h` : "—";
      const col = getSpeedTierColor(s.vmax_mph);
      // Summarize differential profiles concisely (group profiles sharing a speed)
      let diffHtml = "";
      if (s.differential_speeds && s.differential_speeds.length) {
        const bySpd = new Map();
        for (const dp of s.differential_speeds) {
          const k = `${Math.round(dp.vmax_mph)} mph`;
          if (!bySpd.has(k)) bySpd.set(k, []);
          bySpd.get(k).push(dp.profile_name);
        }
        const chips = Array.from(bySpd.entries())
          .slice(0, 3)
          .map(([spd, names]) => {
            const label = names.length > 2 ? `${names.slice(0, 2).join("/")}+${names.length - 2}` : names.join("/");
            return `<span class="rsp-diff-chip" title="${escapeHtml(names.join(", "))}">${escapeHtml(label)}: ${escapeHtml(spd)}</span>`;
          })
          .join("");
        diffHtml = `<div class="rsp-diff-row">${chips}</div>`;
      }
      return `
        <tr>
          <td>${s.start_m}–${s.end_m}m <span class="rsp-muted">(${s.length_m}m)</span></td>
          <td>
            <span class="rsp-speed-pill" style="border-color:${col};color:${col}">${mph}</span>
            <span class="rsp-muted">${kmh}</span>
            ${diffHtml}
          </td>
        </tr>
      `;
    })
    .join("");

  return `
    <table class="rsp-table">
      <thead>
        <tr>
          <th>Distance</th>
          <th>Max Speed (Base &amp; Differential)</th>
        </tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

/**
 * Render the inner body (KPIs + SVG step-chart + step table) for a single route.
 *
 * @param {object} route Normalized route object from `extractRoutesForSpeedProfile`.
 * @returns {string} HTML markup for the card body.
 */
function renderRouteSpeedProfileBody(route) {
  const minMph = kmhToMph(route.min_vmax);
  const maxMph = kmhToMph(route.max_vmax);
  const lenYd = Math.round((route.length_meters || 0) / 0.9144);
  const govColor = getSpeedTierColor(minMph || 0);

  return `
    <div class="rsp-kpi-grid">
      <div class="rsp-kpi">
        <span class="rsp-kpi-label">Governing (Min)</span>
        <strong class="rsp-kpi-val" style="color:${govColor}">${minMph !== null ? `${Math.round(minMph)} mph` : "—"}</strong>
        <small>${route.min_vmax ? `${route.min_vmax} km/h` : ""}</small>
      </div>
      <div class="rsp-kpi">
        <span class="rsp-kpi-label">Peak (Max)</span>
        <strong class="rsp-kpi-val">${maxMph !== null ? `${Math.round(maxMph)} mph` : "—"}</strong>
        <small>${route.max_vmax ? `${route.max_vmax} km/h` : ""}</small>
      </div>
      <div class="rsp-kpi">
        <span class="rsp-kpi-label">Route Length</span>
        <strong class="rsp-kpi-val">${route.length_meters || 0} m</strong>
        <small>${lenYd} yd • ${route.speed_profile.length} step(s)</small>
      </div>
    </div>
    ${renderRouteSpeedStepSvg(route.speed_profile, route.length_meters)}
    ${renderRouteSpeedStepsTable(route.speed_profile)}
  `;
}

/**
 * Build the Inspector "Route Speed Profile" card for signals, points, blocks,
 * and station routes.
 *
 * @param {object} data Selected asset payload.
 * @param {string|null} selectedKey Optional route key (e.g. `"outgoing:120"`).
 * @returns {string} Card HTML markup, or empty string if asset has no routes.
 */
function buildRouteSpeedProfileCard(data, selectedKey = null) {
  const routes = extractRoutesForSpeedProfile(data);
  if (!routes.length) return "";

  const activeRoute =
    (selectedKey && routes.find((r) => r.key === selectedKey)) || routes[0];

  const selectorHtml =
    routes.length > 1
      ? `
        <select class="rsp-route-select" id="rsp-route-select" onchange="selectRouteSpeedProfile(this.value, true)" title="Choose a route to inspect its speed profile and highlight only this route on the map">
          ${routes
            .map(
              (r) =>
                `<option value="${escapeHtml(r.key)}" ${r.key === activeRoute.key ? "selected" : ""}>${escapeHtml(r.label)}</option>`
            )
            .join("")}
        </select>
      `
      : `<div class="rsp-route-single">${escapeHtml(activeRoute.label)}</div>`;

  const canHighlightRoute =
    activeRoute.key.startsWith("outgoing:") || activeRoute.key.startsWith("approach:");
  const highlightBtnHtml = canHighlightRoute
    ? `<button type="button" class="rsp-highlight-btn" onclick="selectRouteSpeedProfile(document.getElementById('rsp-route-select')?.value || '${escapeHtml(activeRoute.key)}', true)" title="Highlight only this route on the TPS map">Highlight Route</button>`
    : "";

  return `
    <div class="rsp-card" id="route-speed-profile-card">
      <div class="rsp-card-header">
        <span class="rsp-badge">SPEED PROFILE</span>
        <span class="rsp-card-title">Route Speed Profile</span>
        ${highlightBtnHtml}
      </div>
      ${selectorHtml}
      <div id="rsp-card-body">
        ${renderRouteSpeedProfileBody(activeRoute)}
      </div>
    </div>
  `;
}

/**
 * Switch the active route displayed inside the Inspector's Route Speed Profile card.
 *
 * When `syncHighlight` is true (user-initiated from this card), clears all other
 * TPS highlights and highlights only the selected route, keeps the Signalling
 * Envelope filter strip above in sync, and keeps only the signal/point marker
 * (removing any extra track centreline highlight) in Geo view.
 *
 * @param {string} routeKey Route key (e.g. `"outgoing:120"` or `"approach:110"`).
 * @param {boolean} syncHighlight Whether to isolate this route on the canvas & envelope bar.
 */
window.selectRouteSpeedProfile = function (routeKey, syncHighlight = true) {
  if (!STATE.selectedAsset) return;
  const routes = extractRoutesForSpeedProfile(STATE.selectedAsset);
  const match = routes.find((r) => r.key === routeKey);
  if (!match) return;

  const bodyEl = document.getElementById("rsp-card-body");
  if (bodyEl) {
    bodyEl.innerHTML = renderRouteSpeedProfileBody(match);
  }
  const selEl = document.getElementById("rsp-route-select");
  if (selEl && selEl.value !== routeKey) {
    selEl.value = routeKey;
  }

  if (
    syncHighlight &&
    (routeKey.startsWith("outgoing:") || routeKey.startsWith("approach:"))
  ) {
    // Keep the selected signal/point marker in Geo view and clear any extra Geo track highlight.
    if (STATE.crossHighlight && STATE.crossHighlight.nwr && STATE.crossHighlight.nwr.active) {
      STATE.crossHighlight.nwr.ways = [];
    }
    if (typeof window.setSignalHighlightFilter === "function") {
      window.setSignalHighlightFilter(routeKey, { fromSpeedProfile: true });
    } else {
      STATE.highlightGroupFilter = routeKey;
      requestRender();
    }
  }
};

