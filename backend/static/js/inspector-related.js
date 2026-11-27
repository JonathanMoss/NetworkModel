/**
 * @fileoverview Inspector "Topology & Cross-Links" dispatcher, signalling
 * (signal/points/block) relationship builders, and lazy card collapse helper.
 *
 * Companion modules:
 * - `inspector-related-nwr.js`: NWR cross-link switcher & NWR/Fusion sections
 * - `inspector-related-tps.js`: Station, edge, node, track & traction sections
 */

/** Collect Topology & Cross-Links sections for `signal` and `points` assets. */
function buildSignalRelatedSections(data, rel) {
  const sections = [buildSignallingEnvelopeWidget(data)];

  if (rel.outgoing_blocks && rel.outgoing_blocks.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Protected Blocks in Advance (${rel.outgoing_blocks.length})</h4>
        ${rel.outgoing_blocks
          .map(
            (b) => `
          <div class="rel-card" onclick="loadAssetDetails('block', ${b.pk}, true)">
            <div class="rel-card-top">
              <span style="color:#10b981">Block #${b.block_id} (${b.shunting_flag ? "Shunt Route" : "Main Block"})</span>
              <span>${b.length_meters} m (${Math.round(b.length_meters / 0.9144)} yd)</span>
            </div>
            <div class="rel-card-sub">
              Pre-signal: ${b.pre_signal_distance}m • Sighting: ${b.sight_distance}m • ${b.turnout_count || 0} point(s) • Vmax: ${b.min_vmax || "—"}–${b.max_vmax || "—"} km/h
            </div>
            ${
              b.exit_signals && b.exit_signals.length
                ? `<div style="margin-top:5px;display:flex;flex-wrap:wrap;gap:4px">
                    ${b.exit_signals
                      .map((xs) => {
                        const isPts = xs.is_points || (xs.name && xs.name.toUpperCase().startsWith("POINT"));
                        const pfx = isPts ? "Next Points: " : "Next Signal: ";
                        const chipType = isPts ? "points" : "signal";
                        const borderCol = isPts ? "#a855f7" : "#10b981";
                        const textCol = isPts ? "#e9d5ff" : "#6ee7b7";
                        return `<button class="candidate-chip" style="border-color:${borderCol};color:${textCol}" onclick="event.stopPropagation();loadAssetDetails('${chipType}', ${xs.pk}, true)">${pfx}${escapeHtml(xs.name)} &rarr;</button>`;
                      })
                      .join("")}
                  </div>`
                : ""
            }
          </div>`
          )
          .join("")}
      </div>
    `);
  }

  if (rel.approach_blocks && rel.approach_blocks.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Approach Blocks in Rear (${rel.approach_blocks.length})</h4>
        ${rel.approach_blocks
          .map(
            (b) => `
          <div class="rel-card" onclick="loadAssetDetails('block', ${b.pk}, true)">
            <div class="rel-card-top">
              <span style="color:#38bdf8">Approach Block #${b.block_id} (${b.shunting_flag ? "Shunt" : "Main"})</span>
              <span>${b.length_meters} m</span>
            </div>
            <div class="rel-card-sub">
              Sighting distance to this signal: ${b.sight_distance}m • Pre-signal (AWS): ${b.pre_signal_distance}m
            </div>
            ${
              b.entry_signals && b.entry_signals.length
                ? `<div style="margin-top:5px;display:flex;flex-wrap:wrap;gap:4px">
                    ${b.entry_signals
                      .map((es) => {
                        const isPts = es.is_points || (es.name && es.name.toUpperCase().startsWith("POINT"));
                        const pfx = isPts ? "Rear Points: " : "Rear Signal: ";
                        const chipType = isPts ? "points" : "signal";
                        const borderCol = isPts ? "#a855f7" : "#38bdf8";
                        const textCol = isPts ? "#e9d5ff" : "#7dd3fc";
                        return `<button class="candidate-chip" style="border-color:${borderCol};color:${textCol}" onclick="event.stopPropagation();loadAssetDetails('${chipType}', ${es.pk}, true)">&larr; ${pfx}${escapeHtml(es.name)}</button>`;
                      })
                      .join("")}
                  </div>`
                : ""
            }
          </div>`
          )
          .join("")}
      </div>
    `);
  }

  if (rel.security_sections && rel.security_sections.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Safety Overlaps &amp; Flank Protection (${rel.security_sections.length})</h4>
        ${rel.security_sections
          .map(
            (ss) => `
          <div class="rel-card" onclick="setSignalHighlightFilter('${ss.group || "overlap_" + ss.index}')">
            <div class="rel-card-top">
              <span style="color:${ss.color || "#d946ef"}">Overlap #${ss.index} — ${ss.length_meters || 0}m (${ss.length_yards || 0} yd)</span>
              <span>Vmax ${ss.vmax} km/h (${ss.vmax_mph || 0} mph)</span>
            </div>
            <div class="rel-card-sub">
              ${(ss.nodes || []).length} overlap nodes • ${(ss.turnout_nodes || []).length} point(s) locked • ${(ss.boundary_nodes || []).length} flank boundary node(s)
            </div>
            <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:6px">
              ${(ss.nodes || [])
                .map(
                  (n) =>
                    `<button class="candidate-chip" style="${
                      n.boundarynode
                        ? "border-color:#f43f5e;color:#fda4af"
                        : n.is_turnout
                        ? "border-color:#d946ef;color:#f5d0fe"
                        : ""
                    }" onclick="event.stopPropagation();loadAssetDetails('node', ${n.nodeid}, true)">#${n.nodeid}${
                      n.boundarynode ? " [Flank]" : n.is_turnout ? " [Pts]" : ""
                    }</button>`
                )
                .join("")}
            </div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }

  if (rel.protected_stations && rel.protected_stations.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Station Boundaries Protected (${rel.protected_stations.length})</h4>
        ${rel.protected_stations
          .map(
            (s) => `
          <div class="rel-card" onclick="loadAssetDetails('station', ${s.station_id}, true)">
            <div class="rel-card-top"><span>${escapeHtml(s.longname)} (${escapeHtml(s.abbrev)})</span><span>${escapeHtml(s.crscode || "")}</span></div>
            <div class="rel-card-sub">Boundary Role: ${escapeHtml(s.boundary_type_desc || "Type " + s.boundary_type_id)}</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }
  return sections;
}


/** Collect Topology & Cross-Links sections for `block` assets. */
function buildBlockRelatedSections(data, rel) {
  const sections = [buildSignallingEnvelopeWidget(data)];

  if (rel.entry_signals && rel.entry_signals.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Entry Signal(s) Governing Block (${rel.entry_signals.length})</h4>
        ${rel.entry_signals
          .map(
            (s) => `
          <div class="rel-card" onclick="loadAssetDetails('signal', ${s.pk}, true)">
            <div class="rel-card-top"><span style="color:#38bdf8">&larr; Entry Signal ${escapeHtml(s.name)}</span><span>Master #${s.signal_id}</span></div>
            <div class="rel-card-sub">${escapeHtml(s.interlocking_desc || "")}</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }

  if (rel.exit_signals && rel.exit_signals.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Exit Signal(s) at End of Block (${rel.exit_signals.length})</h4>
        ${rel.exit_signals
          .map((s) => {
            const isPts = s.is_points || (s.name && s.name.toUpperCase().startsWith("POINT"));
            const pfx = isPts ? "Exit Points " : "Exit Signal ";
            const chipType = isPts ? "points" : "signal";
            return `
          <div class="rel-card" onclick="loadAssetDetails('${chipType}', ${s.pk}, true)">
            <div class="rel-card-top"><span style="color:${isPts ? "#a855f7" : "#f59e0b"}">${pfx}${escapeHtml(s.name)} &rarr;</span><span>Master #${s.signal_id}</span></div>
            <div class="rel-card-sub">${escapeHtml(s.interlocking_desc || "")}</div>
          </div>`;
          })
          .join("")}
      </div>
    `);
  }

  if (rel.exit_overlaps && rel.exit_overlaps.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Safety Overlaps Beyond Exit Signal (${rel.exit_overlaps.length})</h4>
        ${rel.exit_overlaps
          .map(
            (ov) => `
          <div class="rel-card" onclick="setSignalHighlightFilter('overlap_${ov.index}')">
            <div class="rel-card-top"><span style="color:#d946ef">Overlap #${ov.index} (${escapeHtml(ov.signal_name)})</span><span>${ov.length_meters}m (${ov.length_yards} yd)</span></div>
            <div class="rel-card-sub">Release Vmax: ${ov.vmax} km/h • ${ov.node_count} overlap nodes</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }

  if (rel.turnout_nodes && rel.turnout_nodes.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Points Traversed (${rel.turnout_nodes.length})</h4>
        <div style="display:flex;flex-wrap:wrap;gap:4px">
          ${rel.turnout_nodes
            .map(
              (t) =>
                `<button class="candidate-chip" style="border-color:#a855f7;color:#e9d5ff" onclick="loadAssetDetails('node', ${t.node_id}, true)">Points #${t.node_id} (deg ${t.degree})</button>`
            )
            .join("")}
        </div>
      </div>
    `);
  }
  return sections;
}

/**
 * Collapse long `.rel-section` card lists to `maxInitial` items with an
 * expandable "Show all" button for snappy rendering.
 *
 * @param {HTMLElement} container Parent `#tab-related` element.
 * @param {number} [maxInitial=12] Number of cards initially shown per section.
 */
function applyLazyCardCollapse(container, maxInitial = 12) {
  if (!container) return;
  container.querySelectorAll(".rel-section").forEach((sec) => {
    const cards = Array.from(sec.querySelectorAll(":scope > .rel-card"));
    if (cards.length > maxInitial) {
      for (let i = maxInitial; i < cards.length; i++) {
        cards[i].style.display = "none";
      }
      const moreCount = cards.length - maxInitial;
      const btn = document.createElement("button");
      btn.className = "btn btn-sm btn-secondary";
      btn.style.cssText = "width:100%;margin-top:4px;justify-content:center";
      btn.textContent = `Show all ${cards.length} items (+${moreCount} more)`;
      btn.addEventListener("click", () => {
        for (let i = maxInitial; i < cards.length; i++) {
          cards[i].style.display = "";
        }
        btn.remove();
      });
      sec.appendChild(btn);
    }
  });
}

/**
 * Render the Inspector's "Topology & Cross-Links" tab for the selected asset.
 *
 * @param {object} data Asset detail payload.
 */
function renderRelatedTab(data) {
  const rel = data.related || {};
  const assetType = String(data.asset_type || "");
  let sections = [];

  if (assetType.startsWith("nwr_")) {
    sections = buildNwrRelatedSections(rel);
  } else if (assetType === "nf_point" || assetType === "nf_curvature") {
    const tps = rel.tps_cross_links || {};
    if (tps.tps_stations && tps.tps_stations.length) {
      sections.push(buildTpsStationsCrossLinkSection(tps.tps_stations));
    }
  } else if (assetType === "station") {
    sections = buildStationRelatedSections(rel);
  } else if (assetType === "edge") {
    sections = buildEdgeRelatedSections(rel);
  } else if (assetType === "node") {
    sections = buildNodeRelatedSections(rel);
  } else if (assetType === "signal" || assetType === "points") {
    sections = buildSignalRelatedSections(data, rel);
  } else if (assetType === "track") {
    sections = buildTrackRelatedSections(rel);
  } else if (assetType === "block") {
    sections = buildBlockRelatedSections(data, rel);
  } else if (assetType === "attributesection" && rel.section_attributes && rel.section_attributes.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Section Attributes (${rel.section_attributes.length})</h4>
        <table class="prop-table">
          <tbody>
            ${rel.section_attributes
              .slice(0, 60)
              .map(
                (sa) => `
              <tr>
                <th>${escapeHtml(sa.type_name)}</th>
                <td>Value: ${escapeHtml(String(sa.value))} • ${escapeHtml(sa.km_begin || "")} → ${escapeHtml(sa.km_end || "")}</td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>
      </div>
    `);
  } else if (assetType === "modeltrain" || assetType === "tractionunit") {
    sections = buildTractionRelatedSections(data, rel);
  } else if (assetType === "stationbank") {
    for (const sec of rel.corridor_sections || []) {
      sections.push(`
        <div class="rel-section">
          <h4>Corridor Section ${escapeHtml(sec.name)}: ${escapeHtml(sec.description)} (${(sec.stops || []).length} Timing Points)</h4>
          ${(sec.stops || [])
            .map(
              (s) => `
            <div class="rel-card" onclick="loadAssetDetails('station', ${s.station_id}, true)">
              <div class="rel-card-top">
                <span>#${s.seq}. ${escapeHtml(s.longname)} (${escapeHtml(s.crscode || s.abbrev)})</span>
                <span class="pill-tag ${s.timing_role.includes("Mandatory") ? "mandatory" : ""}">${escapeHtml(s.timing_role)}</span>
              </div>
              <div class="rel-card-sub">
                TIPLOC: ${escapeHtml(s.abbrev)} • ${escapeHtml(s.planning_category)} • ELR: ${escapeHtml(s.elr || "—")} ${escapeHtml(s.km_str || "")}
                ${s.layouts && s.layouts.length ? ` • Layout: ${escapeHtml(s.layouts.join(", "))}` : ""}
              </div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
  }

  if (rel.nwr_cross_links && !assetType.startsWith("nwr_")) {
    const cw = buildNwrCrossLinkWidget(rel.nwr_cross_links);
    if (cw) sections.push(cw);
  }

  if (rel.node_ids && rel.node_ids.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Ordered Node Sequence (${rel.node_ids.length} nodes)</h4>
        <div style="display:flex;flex-wrap:wrap;gap:4px">
          ${rel.node_ids
            .slice(0, 80)
            .map(
              (nid) =>
                `<button class="candidate-chip" onclick="loadAssetDetails('node', ${nid}, true)">#${nid}</button>`
            )
            .join("")}
        </div>
      </div>
    `);
  }

  const relContainer = document.getElementById("tab-related");
  relContainer.innerHTML =
    sections.join("") || `<p class="empty-state">No additional linked records.</p>`;
  applyLazyCardCollapse(relContainer);
}

