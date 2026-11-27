/**
 * @fileoverview Inspector "Topology & Cross-Links" renderers for NWR Track
 * Model assets, Network Fusion overlays, and the TPS-to-NWR manual track switcher.
 */

/**
 * Build the Cross-Model Link card (with manual track switcher and persistent
 * override buttons) for TPS assets linked to the NWR Track Model.
 *
 * @param {object} nwrCross Cross-link payload from `data.related.nwr_cross_links`.
 * @returns {string} HTML markup, or empty string if no link data exists.
 */
function buildNwrCrossLinkWidget(nwrCross) {
  if (!nwrCross) return "";
  const best = nwrCross.best_centreline || (nwrCross.centrelines && nwrCross.centrelines[0]);
  const snapped = nwrCross.snapped_node;
  const corridor = nwrCross.corridor_centrelines || [];
  const matchInfo = nwrCross.match_info || {};
  const isSaved = Boolean(matchInfo.saved_override);
  const isManual = Boolean(matchInfo.manual_override);
  const autoBestFid = matchInfo.auto_best_fid;

  if (!best && !snapped && (!nwrCross.elr_record || !nwrCross.elr_record.elr)) {
    return "";
  }

  let html = `<div class="rel-section">
    <h4>Cross-Model Link: NWR Track Model (OSGB36)</h4>`;

  if (best) {
    const isClipped = best.clipped || nwrCross.clipped;
    const lenStr = isClipped
      ? `${best.clipped_length_meters || "?"}m (Exact Sub-segment)`
      : `${best.length_meters || "?"}m`;
    const badgeText = isSaved
      ? "Saved Override"
      : isManual
      ? "Manual Preview"
      : "Auto Best Match";
    const badgeStyle = isSaved
      ? "background: rgba(16, 185, 129, 0.22); color: #34d399;"
      : isManual
      ? "background: rgba(245, 158, 11, 0.22); color: #fbbf24;"
      : "background: rgba(6, 182, 212, 0.2); color: #38bdf8;";
    const borderCol = isSaved ? "#10b981" : isManual ? "#f59e0b" : "#06b6d4";

    html += `
      <div class="rel-card" style="border-left: 3px solid ${borderCol};" onclick="jumpToNwrAsset('nwr_centreline', ${best.fid})">
        <div class="rel-card-top">
          <span style="font-weight: 700; color: #22d3ee;">
            ${escapeHtml(best.elr)} / Track ${escapeHtml(best.track_id)}
            <span style="font-size: 10px; ${badgeStyle} padding: 2px 6px; border-radius: 4px; margin-left: 6px;">${badgeText}</span>
          </span>
          <span style="color:#38bdf8">Open in OSGB36 View &rarr;</span>
        </div>
        <div class="rel-card-sub" style="margin-top: 4px;">
          ${escapeHtml(best.track_id_decoded || "")} • ${lenStr}
        </div>
        <div class="rel-card-sub" style="font-size: 11px; opacity: 0.85;">
          Chainage: ${Number(best.start_mile_yards).toFixed(4)} → ${Number(best.end_mile_yards).toFixed(4)} mi.yd (${Number(best.start_meters).toFixed(0)}m → ${Number(best.end_meters).toFixed(0)}m)
        </div>
        ${
          matchInfo.source
            ? `<div class="rel-card-sub" style="font-size: 11px; margin-top: 3px; color: ${isSaved ? "#34d399" : isManual ? "#fbbf24" : "#94a3b8"};">
                Match Basis: ${escapeHtml(matchInfo.source)}${matchInfo.direction_hint ? ` • Dir: ${escapeHtml(String(matchInfo.direction_hint).toUpperCase())}` : ""}${matchInfo.platform_num ? ` • Plat ${escapeHtml(String(matchInfo.platform_num))}` : ""}
              </div>`
            : ""
        }
      </div>
    `;

    if (corridor.length > 1) {
      const optionsHtml = corridor
        .map((c) => {
          const isSelected = c.fid === best.fid;
          const isAuto = autoBestFid && c.fid === autoBestFid;
          const isSavedItem = matchInfo.saved_fid && c.fid === matchInfo.saved_fid;
          const tag = isSavedItem ? " ★ Saved" : isAuto ? " ★ Auto" : "";
          return `<option value="${c.fid}" ${isSelected ? "selected" : ""}>Track ${escapeHtml(c.track_id)} — ${escapeHtml(c.track_id_decoded || "")} (${Number(c.length_meters).toFixed(0)}m)${tag}</option>`;
        })
        .join("");

      const showSaveBtn = isManual && !isSaved;
      const showResetBtn = isManual || isSaved;

      html += `
        <div class="nwr-track-switcher">
          <label class="nwr-track-switcher-label" for="nwr-track-switcher-select">Plot on Track:</label>
          <select id="nwr-track-switcher-select" class="nwr-track-switcher-select" onchange="switchNwrCrossTrack(this.value)">
            ${optionsHtml}
          </select>
          ${showSaveBtn ? `<button type="button" class="nwr-track-save-btn" title="Persist this track override in PostgreSQL" onclick="saveNwrCrossTrackOverride(${best.fid})">💾 Save</button>` : ""}
          ${showResetBtn ? `<button type="button" class="nwr-track-reset-btn" title="Clear override and reset to automatic best match" onclick="resetNwrCrossTrackOverride()">↺ Auto</button>` : ""}
        </div>
      `;
    }
  }

  if (snapped) {
    const isSw = snapped.is_switch || snapped.valency >= 3;
    const icon = isSw ? "🔀" : "📍";
    const badgeCol = isSw ? "#ec4899" : "#f59e0b";
    const badgeBg = isSw ? "rgba(236, 72, 153, 0.2)" : "rgba(245, 158, 11, 0.2)";
    html += `
      <div class="rel-card" style="border-left: 3px solid ${badgeCol}; margin-top: 6px;" onclick="jumpToNwrAsset('nwr_node', ${snapped.fid})">
        <div class="rel-card-top">
          <span style="font-weight: 700; color: #f8fafc;">
            ${icon} Nearest Geographic Node #${escapeHtml(snapped.asset_id)}
            <span style="font-size: 10px; background: ${badgeBg}; color: ${badgeCol}; padding: 2px 6px; border-radius: 4px; margin-left: 6px;">
              ${escapeHtml(snapped.valency_desc || "Valency " + snapped.valency)}
            </span>
          </span>
          <span style="color:${badgeCol}">Select Node &rarr;</span>
        </div>
        <div class="rel-card-sub" style="margin-top: 4px;">
          OSGB36 (${Number(snapped.x).toFixed(2)} E, ${Number(snapped.y).toFixed(2)} N) • Distance: ${snapped.dist_meters}m from schematic point
        </div>
      </div>
    `;
  }

  if (corridor.length > 1) {
    const otherCentrelines = corridor.filter((c) => !best || c.fid !== best.fid);
    if (otherCentrelines.length) {
      html += `
        <details style="margin-top: 8px; font-size: 12px; color: #94a3b8;">
          <summary style="cursor: pointer; padding: 4px 0; color: #38bdf8;">
            Other Parallel Tracks in Corridor (${otherCentrelines.length})
          </summary>
          <div style="margin-top: 6px; display: flex; flex-direction: column; gap: 4px;">
            ${otherCentrelines
              .map(
                (c) => `
              <div class="rel-card" style="padding: 6px 8px;" onclick="jumpToNwrAsset('nwr_centreline', ${c.fid})">
                <div class="rel-card-top">
                  <span>Track ${escapeHtml(c.track_id)} (${escapeHtml(c.track_id_decoded || "")})</span>
                  <span style="display:flex; align-items:center; gap:6px;">
                    <button type="button" class="nwr-plot-here-btn" onclick="event.stopPropagation(); switchNwrCrossTrack(${c.fid});">Plot Here</button>
                    <span style="color:#38bdf8">View &rarr;</span>
                  </span>
                </div>
                <div class="rel-card-sub">${c.length_meters}m • ${Number(c.start_mile_yards).toFixed(4)} → ${Number(c.end_mile_yards).toFixed(4)} mi.yd</div>
              </div>`
              )
              .join("")}
          </div>
        </details>
      `;
    }
  }

  html += `</div>`;
  return html;
}

/**
 * Render related TPS station cross-links for NWR / Network Fusion assets.
 *
 * @param {Array<object>} tpsStations List of TPS station records.
 * @returns {string} HTML section markup.
 */
function buildTpsStationsCrossLinkSection(tpsStations) {
  return `
    <div class="rel-section">
      <h4>Cross-Model Link: TPS Stations on this ELR (${tpsStations.length})</h4>
      ${tpsStations
        .map(
          (s) => `
        <div class="rel-card" onclick="jumpToTpsAsset('station', ${s.station_id})">
          <div class="rel-card-top"><span>${escapeHtml(s.longname || s.abbrev)} (${escapeHtml(s.crscode || s.abbrev)})</span><span style="color:#38bdf8">Open in TPS View &rarr;</span></div>
          <div class="rel-card-sub">TPS Station #${s.station_id} • STANOX ${escapeHtml(s.stanox || "N/A")} • KM ${(s.km_value / 1000).toFixed(3)} km</div>
        </div>`
        )
        .join("")}
    </div>
  `;
}

/** Collect Topology & Cross-Links sections for `nwr_*` assets. */
function buildNwrRelatedSections(rel) {
  const sections = [];
  if (rel.start_node || rel.end_node) {
    sections.push(`
      <div class="rel-section">
        <h4>Connected NWR Track Nodes</h4>
        ${
          rel.start_node
            ? `<div class="rel-card" onclick="loadAssetDetails('nwr_node', ${rel.start_node.fid}, true)">
                <div class="rel-card-top"><span>Start Node ${escapeHtml(rel.start_node.asset_id)}</span><span>Valency ${rel.start_node.valency}</span></div>
                <div class="rel-card-sub">OSGB36 (${rel.start_node.x} E, ${rel.start_node.y} N)</div>
              </div>`
            : ""
        }
        ${
          rel.end_node
            ? `<div class="rel-card" onclick="loadAssetDetails('nwr_node', ${rel.end_node.fid}, true)">
                <div class="rel-card-top"><span>End Node ${escapeHtml(rel.end_node.asset_id)}</span><span>Valency ${rel.end_node.valency}</span></div>
                <div class="rel-card-sub">OSGB36 (${rel.end_node.x} E, ${rel.end_node.y} N)</div>
              </div>`
            : ""
        }
      </div>
    `);
  }

  if (rel.elr_record) {
    const er = rel.elr_record;
    sections.push(`
      <div class="rel-section">
        <h4>Parent NWR ELR Reference Corridor</h4>
        <div class="rel-card" onclick="loadAssetDetails('nwr_elr', ${er.fid}, true)">
          <div class="rel-card-top"><span>ELR ${escapeHtml(er.elr)}</span><span>${escapeHtml(er.asset_id)}</span></div>
          <div class="rel-card-sub">Mileage: ${Number(er.start_mile_yards).toFixed(4)} → ${Number(er.end_mile_yards).toFixed(4)} mi.yd</div>
        </div>
      </div>
    `);
  }

  if (rel.connected_centrelines && rel.connected_centrelines.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Connected NWR Track Centre Lines (${rel.connected_centrelines.length})</h4>
        ${rel.connected_centrelines
          .map(
            (c) => `
          <div class="rel-card" onclick="loadAssetDetails('nwr_centreline', ${c.fid}, true)">
            <div class="rel-card-top"><span>${escapeHtml(c.elr)} / ${escapeHtml(c.track_id)} (${escapeHtml(c.asset_id)})</span><span>${c.length_meters}m</span></div>
            <div class="rel-card-sub">${Number(c.start_mile_yards).toFixed(4)} → ${Number(c.end_mile_yards).toFixed(4)} mi.yd</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }

  if (rel.waymarks && rel.waymarks.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Nearby NWR Milepost Waymarks (${rel.waymarks.length})</h4>
        ${rel.waymarks
          .map(
            (wm) => `
          <div class="rel-card" onclick="loadAssetDetails('nwr_waymark', ${wm.fid}, true)">
            <div class="rel-card-top"><span>${escapeHtml(wm.elr)} @ ${wm.value} ${escapeHtml(wm.unit)}</span><span>${escapeHtml(wm.asset_id)}</span></div>
            <div class="rel-card-sub">${wm.meters} m from ELR zero</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }

  const tps = rel.tps_cross_links || {};
  if (tps.tps_stations && tps.tps_stations.length) {
    sections.push(buildTpsStationsCrossLinkSection(tps.tps_stations));
  }

  if (tps.tps_attribute_sections && tps.tps_attribute_sections.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Cross-Model Link: TPS Attribute Sections (${tps.tps_attribute_sections.length})</h4>
        ${tps.tps_attribute_sections
          .map(
            (a) => `
          <div class="rel-card" onclick="jumpToTpsAsset('attributesection', ${a.pk})">
            <div class="rel-card-top"><span>${escapeHtml(a.description)} (${escapeHtml(a.short_description)})</span><span style="color:#38bdf8">Open in TPS View &rarr;</span></div>
            <div class="rel-card-sub">TPS Attribute Section ID #${a.id}</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }

  const nf = rel.network_fusion;
  if (nf) {
    if (nf.points && nf.points.length > 1) {
      sections.push(renderElevationProfileSvg(nf));
    }
    if (nf.curvature && nf.curvature.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Network Fusion Curvature Segments (${nf.curvature.length})</h4>
          ${nf.curvature
            .slice(0, 15)
            .map(
              (c) => `
            <div class="rel-card" onclick="loadAssetDetails('nf_curvature', ${c.pk}, true)">
              <div class="rel-card-top">
                <span style="color:${getCurvatureColor(c.radius_avg_m)}">R_avg=${c.radius_avg_m}m (${escapeHtml(c.radius_avg_class)})</span>
                <span>Min R=${c.radius_min_m}m</span>
              </div>
              <div class="rel-card-sub">${escapeHtml(c.mileage_from)} &rarr; ${escapeHtml(c.mileage_to)} (${c.total_yards_from} &rarr; ${c.total_yards_to} yd)</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
    if (nf.points && nf.points.length) {
      sections.push(`
        <div class="rel-section">
          <h4>Network Fusion Tenth-Mile Samples (${nf.points.length})</h4>
          ${nf.points
            .slice(0, 12)
            .map(
              (p) => `
            <div class="rel-card" onclick="loadAssetDetails('nf_point', ${p.pk}, true)">
              <div class="rel-card-top">
                <span style="color:${getElevationColor(p.elevation_m)}">${p.elevation_m}m elev @ ${escapeHtml(p.mileage)}</span>
                <span>${escapeHtml(p.place_name || p.postcode || "")}</span>
              </div>
              <div class="rel-card-sub">Bedrock: ${escapeHtml(p.bedrock || "—")} • Rain: ${p.annual_rainfall_mm || "—"}mm/yr • Wind: ${p.mean_wind_speed_ms || "—"}m/s</div>
            </div>`
            )
            .join("")}
        </div>
      `);
    }
  }
  return sections;
}

