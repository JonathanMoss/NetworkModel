/**
 * @fileoverview Signalling & Interlocking Envelope Inspector card and
 * interactive route/overlap highlight filter controls.
 */

/**
 * Restore asset highlight geometry from `STATE.selectedAsset.highlight` when
 * the user previously clicked "Clear Highlight" and then picks a route/group.
 */
function restoreAssetHighlightIfCleared() {
  const hasActiveHighlight =
    (STATE.highlight.ways && STATE.highlight.ways.length > 0) ||
    (STATE.highlight.points && STATE.highlight.points.length > 0) ||
    (STATE.highlight.segments && STATE.highlight.segments.length > 0);
  if (!hasActiveHighlight && STATE.selectedAsset && STATE.selectedAsset.highlight) {
    STATE.highlight = {
      ways: STATE.selectedAsset.highlight.ways || [],
      points: STATE.selectedAsset.highlight.points || [],
      segments: STATE.selectedAsset.highlight.segments || [],
    };
    if (
      STATE.highlight.ways.length ||
      STATE.highlight.points.length ||
      STATE.highlight.segments.length
    ) {
      document.getElementById("btn-clear-highlight")?.classList.remove("hidden");
    }
  }
}

/**
 * Sync a single split filter control's `<select>` value and summary label.
 *
 * @param {string} dataGroup Control `data-group` value (`"approach"`, `"outgoing"`, `"overlaps"`).
 * @param {string} activeGroup Active filter group string.
 * @param {string} itemPrefix Prefix identifying a specific item (`"approach:"`, `"outgoing:"`, `"overlap_"`).
 * @param {string} defaultValue Default `<select>` option value when no specific item is chosen.
 * @param {string} fallbackPrefix Prefix for fallback short label if option has no `data-short`.
 * @param {string} fallbackDefault Fallback label if `data-default` is missing.
 */
function syncEnvelopeFilterControl(
  dataGroup,
  activeGroup,
  itemPrefix,
  defaultValue,
  fallbackPrefix,
  fallbackDefault
) {
  const control = document.querySelector(`.sig-filter-control[data-group="${dataGroup}"]`);
  if (!control) return;
  const sel = control.querySelector(".sig-filter-select");
  const textSpan = control.querySelector(".sig-filter-text");
  const isSpecific = activeGroup.startsWith(itemPrefix);
  if (sel) sel.value = isSpecific ? activeGroup : defaultValue;
  if (textSpan) {
    if (isSpecific) {
      const opt = sel ? sel.options[sel.selectedIndex] : null;
      textSpan.textContent = opt
        ? opt.dataset.short || `${fallbackPrefix}${activeGroup.slice(itemPrefix.length)}`
        : `${fallbackPrefix}${activeGroup.slice(itemPrefix.length)}`;
    } else {
      textSpan.textContent = textSpan.dataset.default || fallbackDefault;
    }
  }
}

/**
 * Apply a signalling envelope highlight filter on the TPS canvas and sync the
 * Signalling Envelope control bar and Route Speed Profile card.
 *
 * @param {string} group Filter key (`"all"`, `"approach"`, `"outgoing"`, `"all_overlaps"`,
 *   `"approach:<block_id>"`, `"outgoing:<block_id>"`, or `"overlap_<index>"`).
 * @param {object} [opts] Optional flags (e.g. `{ fromSpeedProfile: true }`).
 */
window.setSignalHighlightFilter = function (group, opts = {}) {
  restoreAssetHighlightIfCleared();

  STATE.highlightGroupFilter = group;
  document.querySelectorAll(".sig-filter-btn, .sig-filter-control").forEach((el) => {
    const g = el.dataset.group;
    if (group === "all") {
      el.classList.toggle("active", g === "all");
    } else if (group === "approach" || group.startsWith("approach:")) {
      el.classList.toggle("active", g === "approach");
    } else if (group === "outgoing" || group.startsWith("outgoing:")) {
      el.classList.toggle("active", g === "outgoing");
    } else if (group === "all_overlaps" || group === "overlaps" || group.startsWith("overlap_")) {
      el.classList.toggle("active", g === "overlaps");
    } else {
      el.classList.toggle("active", g === group);
    }
  });

  syncEnvelopeFilterControl("approach", group, "approach:", "approach", "B#", "Approach");
  syncEnvelopeFilterControl("outgoing", group, "outgoing:", "outgoing", "B#", "Protected");
  syncEnvelopeFilterControl("overlaps", group, "overlap_", "all_overlaps", "Ov #", "Overlaps");

  // Sync the Route Speed Profile card when a specific approach/outgoing route is chosen
  // from the Signalling Envelope strip (skip when triggered from the Speed Profile card).
  if (
    !opts.fromSpeedProfile &&
    (group.startsWith("outgoing:") || group.startsWith("approach:")) &&
    typeof window.selectRouteSpeedProfile === "function"
  ) {
    window.selectRouteSpeedProfile(group, false);
  }

  requestRender();
};

/**
 * Build colored aspect lamp dots for a signal's interlocking system.
 *
 * @param {number} aspects Number of aspects (2, 3, 4, or 0 for points/other).
 * @returns {string} HTML markup for aspect lamps.
 */
function buildAspectLampsHtml(aspects) {
  if (aspects === 4) {
    return `
      <span class="sig-lamp" style="background:#ef4444"></span>
      <span class="sig-lamp" style="background:#eab308"></span>
      <span class="sig-lamp" style="background:#eab308"></span>
      <span class="sig-lamp" style="background:#10b981"></span>`;
  }
  if (aspects === 3) {
    return `
      <span class="sig-lamp" style="background:#ef4444"></span>
      <span class="sig-lamp" style="background:#eab308"></span>
      <span class="sig-lamp" style="background:#10b981"></span>`;
  }
  if (aspects === 2) {
    return `
      <span class="sig-lamp" style="background:#ef4444"></span>
      <span class="sig-lamp" style="background:#10b981"></span>`;
  }
  return `<span class="sig-lamp" style="background:#a855f7"></span>`;
}

/**
 * Build the Signalling & Interlocking Envelope card for signals and points.
 *
 * @param {object} data Selected signal/points asset payload.
 * @param {object} rel Asset `related` payload.
 * @returns {string} HTML markup for the envelope card.
 */
function buildSignalOrPointsEnvelopeHtml(data, rel) {
  const isPoints = data.asset_type === "points";
  const ilock = rel.interlocking_system || {};
  const appBlocks = rel.approach_blocks || [];
  const outBlocks = rel.outgoing_blocks || [];
  const overlaps = rel.security_sections || [];
  const lampsHtml = buildAspectLampsHtml(Number(ilock.aspects || 0));

  const firstApp = appBlocks[0];
  const firstOut = outBlocks[0];
  const firstOv = overlaps[0];
  const curSigName =
    (data.title || "").replace(/^(Signal|Points)\s+/i, "") || (isPoints ? "Points" : "Signal");

  const hasApp = appBlocks.length > 0;
  const isAppActive =
    STATE.highlightGroupFilter === "approach" ||
    (STATE.highlightGroupFilter && STATE.highlightGroupFilter.startsWith("approach:"));
  const appControlHtml = `
    <div class="sig-filter-control ${!hasApp ? "disabled" : ""} ${isAppActive ? "active" : ""}" data-group="approach">
      <button class="sig-filter-btn-split" data-group="approach" ${hasApp ? "onclick=\"setSignalHighlightFilter('approach')\"" : "disabled"} title="${hasApp ? "View all approach routes" : "No approach blocks in database"}">
        <span class="sig-lamp" style="background:#38bdf8"></span>
        <span class="sig-filter-text" data-default="Approach (${appBlocks.length})">Approach (${appBlocks.length})</span>
      </button>
      <div class="sig-filter-select-wrap">
        <select class="sig-filter-select" data-parent-group="approach" ${hasApp ? 'onchange="setSignalHighlightFilter(this.value)"' : "disabled"} title="Select specific approach route">
          <option value="approach" data-short="Approach (${appBlocks.length})">All Approach (${appBlocks.length})</option>
          ${appBlocks
            .map((b) => {
              const routeLabel = b.route_label || `To ${curSigName} (Block #${b.block_id})`;
              const shortLabel = `App B#${b.block_id}`;
              return `<option value="approach:${b.block_id}" data-short="${escapeHtml(shortLabel)}">${escapeHtml(routeLabel)} (B#${b.block_id} • ${b.length_meters}m)</option>`;
            })
            .join("")}
        </select>
        <span class="sig-select-arrow">&#9662;</span>
      </div>
    </div>
  `;

  const hasOut = outBlocks.length > 0;
  const isOutActive =
    STATE.highlightGroupFilter === "outgoing" ||
    (STATE.highlightGroupFilter && STATE.highlightGroupFilter.startsWith("outgoing:"));
  const outControlHtml = `
    <div class="sig-filter-control ${!hasOut ? "disabled" : ""} ${isOutActive ? "active" : ""}" data-group="outgoing">
      <button class="sig-filter-btn-split" data-group="outgoing" ${hasOut ? "onclick=\"setSignalHighlightFilter('outgoing')\"" : "disabled"} title="${hasOut ? "View all protected blocks in advance" : "No protected blocks ahead in database"}">
        <span class="sig-lamp" style="background:#10b981"></span>
        <span class="sig-filter-text" data-default="Protected (${outBlocks.length})">Protected (${outBlocks.length})</span>
      </button>
      <div class="sig-filter-select-wrap">
        <select class="sig-filter-select" data-parent-group="outgoing" ${hasOut ? 'onchange="setSignalHighlightFilter(this.value)"' : "disabled"} title="Select specific protected block">
          <option value="outgoing" data-short="Protected (${outBlocks.length})">All Protected (${outBlocks.length})</option>
          ${outBlocks
            .map((b) => {
              const routeLabel = b.route_label || `From ${curSigName} (Block #${b.block_id})`;
              const shortLabel = `Prot B#${b.block_id}`;
              return `<option value="outgoing:${b.block_id}" data-short="${escapeHtml(shortLabel)}">${escapeHtml(routeLabel)} (B#${b.block_id} • ${b.length_meters}m)</option>`;
            })
            .join("")}
        </select>
        <span class="sig-select-arrow">&#9662;</span>
      </div>
    </div>
  `;

  const hasOv = overlaps.length > 0;
  const isOvActive =
    STATE.highlightGroupFilter === "all_overlaps" ||
    STATE.highlightGroupFilter === "overlaps" ||
    (STATE.highlightGroupFilter && STATE.highlightGroupFilter.startsWith("overlap_"));
  const ovControlHtml = `
    <div class="sig-filter-control ${!hasOv ? "disabled" : ""} ${isOvActive ? "active" : ""}" data-group="overlaps">
      <button class="sig-filter-btn-split" data-group="overlaps" ${hasOv ? "onclick=\"setSignalHighlightFilter('all_overlaps')\"" : "disabled"} title="${hasOv ? "View all safety overlaps" : "No safety overlaps defined"}">
        <span class="sig-lamp" style="background:#d946ef"></span>
        <span class="sig-filter-text" data-default="Overlaps (${overlaps.length})">Overlaps (${overlaps.length})</span>
      </button>
      <div class="sig-filter-select-wrap">
        <select class="sig-filter-select" data-parent-group="overlaps" ${hasOv ? 'onchange="setSignalHighlightFilter(this.value)"' : "disabled"} title="Select specific safety overlap">
          <option value="all_overlaps" data-short="Overlaps (${overlaps.length})">All Overlaps (${overlaps.length})</option>
          ${overlaps
            .map((ov) => {
              const shortLabel = `Overlap #${ov.index}`;
              return `<option value="${ov.group}" data-short="${escapeHtml(shortLabel)}">${escapeHtml(shortLabel)} (${ov.length_meters}m • ${ov.vmax} km/h)</option>`;
            })
            .join("")}
        </select>
        <span class="sig-select-arrow">&#9662;</span>
      </div>
    </div>
  `;

  return `
    <div class="sig-envelope-card">
      <div class="sig-envelope-header">
        <span class="sig-envelope-title">${isPoints ? "Points Interlocking &amp; Envelope" : "Signalling &amp; Interlocking Envelope"}</span>
        <span class="sig-aspect-pill">
          ${lampsHtml}
          <span>${escapeHtml(ilock.display_name || (isPoints ? "Points" : "Signal"))}</span>
        </span>
      </div>

      <div class="sig-strip-diagram">
        <div class="sig-strip-box approach" onclick="setSignalHighlightFilter('approach')" title="Click to view approach envelope">
          <div class="sig-strip-label">In Rear (Approach)</div>
          <div class="sig-strip-val">${
            firstApp ? (firstApp.route_label ? escapeHtml(firstApp.route_label) : `Block #${firstApp.block_id} (${firstApp.length_meters}m)`) : "None"
          }</div>
          <div class="sig-strip-sub">${
            firstApp
              ? `Sight: ${firstApp.sight_distance}m • AWS: ${firstApp.pre_signal_distance}m`
              : `${appBlocks.length} blocks`
          }</div>
        </div>

        <div class="sig-strip-box current" onclick="setSignalHighlightFilter('all')" title="Click to view all envelope">
          <div class="sig-strip-label">${isPoints ? "Selected Points" : "Selected Signal"}</div>
          <div class="sig-strip-val">${escapeHtml((data.title || "").replace(/^(Signal|Points)\s+/i, ""))}</div>
          <div class="sig-strip-sub">Set ${ilock.settime || 0}s • Free ${ilock.freeingtime || 0}s</div>
        </div>

        <div class="sig-strip-box outgoing" onclick="setSignalHighlightFilter('outgoing')" title="Click to view protected block envelope">
          <div class="sig-strip-label">In Advance (Block)</div>
          <div class="sig-strip-val">${
            firstOut ? (firstOut.route_label ? escapeHtml(firstOut.route_label) : `Block #${firstOut.block_id} (${firstOut.length_meters}m)`) : "None"
          }</div>
          <div class="sig-strip-sub">${outBlocks.length} protected route(s)</div>
        </div>

        <div class="sig-strip-box overlap" onclick="setSignalHighlightFilter('${firstOv ? firstOv.group : "all"}')" title="Click to view safety overlap">
          <div class="sig-strip-label">Safety Overlap</div>
          <div class="sig-strip-val">${
            firstOv ? `${firstOv.length_meters}m (${firstOv.length_yards}yd)` : "None"
          }</div>
          <div class="sig-strip-sub">${overlaps.length} swinging overlap(s)</div>
        </div>
      </div>

      <div class="sig-filter-bar">
        <button class="sig-filter-btn ${STATE.highlightGroupFilter === "all" ? "active" : ""}" data-group="all" onclick="setSignalHighlightFilter('all')">
          All Envelope
        </button>
        ${appControlHtml}
        ${outControlHtml}
        ${ovControlHtml}
      </div>
    </div>
  `;
}

/**
 * Build the Block Section & Overlap Envelope card for a signalling block asset.
 *
 * @param {object} data Selected block asset payload.
 * @param {object} rel Asset `related` payload.
 * @returns {string} HTML markup for the block envelope card.
 */
function buildBlockEnvelopeHtml(data, rel) {
  const entrySigs = rel.entry_signals || [];
  const exitSigs = rel.exit_signals || [];
  const exitOvs = rel.exit_overlaps || [];
  return `
    <div class="sig-envelope-card">
      <div class="sig-envelope-header">
        <span class="sig-envelope-title">Block Section &amp; Overlap Envelope</span>
        <span class="sig-aspect-pill">
          <span class="sig-lamp" style="background:#10b981"></span>
          <span>${rel.length_meters || 0} m (${Math.round((rel.length_meters || 0) / 0.9144)} yd)</span>
        </span>
      </div>
      <div class="sig-strip-diagram cols-3">
        <div class="sig-strip-box approach">
          <div class="sig-strip-label">Entry Signal</div>
          <div class="sig-strip-val">${
            entrySigs[0] ? escapeHtml(entrySigs[0].name) : "—"
          }</div>
          <div class="sig-strip-sub">${entrySigs.length} entry signal(s)</div>
        </div>
        <div class="sig-strip-box outgoing">
          <div class="sig-strip-label">Block Section</div>
          <div class="sig-strip-val">${escapeHtml(data.title)}</div>
          <div class="sig-strip-sub">${(rel.turnout_nodes || []).length} point(s)</div>
        </div>
        <div class="sig-strip-box current">
          <div class="sig-strip-label">Exit Signal</div>
          <div class="sig-strip-val">${
            exitSigs[0] ? escapeHtml(exitSigs[0].name) : "—"
          }</div>
          <div class="sig-strip-sub">${exitOvs.length} overlap(s) ahead</div>
        </div>
      </div>
      <div class="sig-filter-bar">
        <button class="sig-filter-btn active" data-group="all" onclick="setSignalHighlightFilter('all')">
          All Block &amp; Overlaps
        </button>
        <button class="sig-filter-btn" data-group="outgoing" onclick="setSignalHighlightFilter('outgoing')">
          <span class="sig-lamp" style="background:#10b981"></span>
          Block Only
        </button>
        ${exitOvs
          .map(
            (ov) => `
          <button class="sig-filter-btn" data-group="overlap_${ov.index}" onclick="setSignalHighlightFilter('overlap_${ov.index}')">
            <span class="sig-lamp" style="background:#d946ef"></span>
            Exit Overlap #${ov.index} (${ov.length_meters}m)
          </button>`
          )
          .join("")}
      </div>
    </div>
  `;
}

/**
 * Build the Signalling Envelope Inspector widget for signals, points, or blocks.
 *
 * @param {object} data Selected asset payload.
 * @returns {string} HTML markup, or empty string for unsupported asset types.
 */
function buildSignallingEnvelopeWidget(data) {
  if (!data || (data.asset_type !== "signal" && data.asset_type !== "points" && data.asset_type !== "block")) {
    return "";
  }
  const rel = data.related || {};
  if (data.asset_type === "signal" || data.asset_type === "points") {
    return buildSignalOrPointsEnvelopeHtml(data, rel);
  }
  return buildBlockEnvelopeHtml(data, rel);
}

