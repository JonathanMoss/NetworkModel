/**
 * @fileoverview Inspector "Topology & Cross-Links" tab renderers, NWR manual
 * track switcher widget, and per-asset-type relationship section builders.
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

/** Collect Topology & Cross-Links sections for `station` assets. */
function buildStationRelatedSections(rel) {
  const sections = [];
  if (rel.routes && rel.routes.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Station Signalling Routes (${rel.routes.length})</h4>
        ${rel.routes
          .map(
            (r) => `
          <div class="rel-card" onclick="loadAssetDetails('route', ${r.pk}, true)">
            <div class="rel-card-top">
              <span style="color:#10b981">Route #${r.route_id}</span>
              <span>${r.node_count} nodes</span>
            </div>
            <div class="rel-card-sub">ROP: ${escapeHtml(r.rop_line_code_desc || "—")} • SA: ${escapeHtml(r.sa_line_code_desc || "—")}</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }
  if (rel.boundary_signals && rel.boundary_signals.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Station Boundary Signals (${rel.boundary_signals.length})</h4>
        ${rel.boundary_signals
          .map(
            (b) => `
          <div class="rel-card" ${b.signal_pk ? `onclick="loadAssetDetails('signal', ${b.signal_pk}, true)"` : ""}>
            <div class="rel-card-top">
              <span>${escapeHtml(b.signal_name || "Signal #" + b.signal_id)}</span>
              <span>${escapeHtml(b.interlocking_desc || "")}</span>
            </div>
            <div class="rel-card-sub">Master Signal #${b.signal_id} • Boundary Type ${b.boundary_type_id}</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }
  if (rel.tracks && rel.tracks.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Station Tracks &amp; Platforms (${rel.tracks.length})</h4>
        ${rel.tracks
          .map(
            (t) => `
          <div class="rel-card" onclick="loadAssetDetails('track', ${t.pk}, true)">
            <div class="rel-card-top">
              <span>${escapeHtml(t.name)} — ${escapeHtml(t.description || t.longname)}</span>
              <span>${escapeHtml(t.track_category_desc || "")}</span>
            </div>
            <div class="rel-card-sub">
              Track ID #${t.track_id} • ${t.node_count} way nodes • ROP: ${escapeHtml(t.rop_line_code_desc || "—")} • SA: ${escapeHtml(t.sa_line_code_desc || "—")}
            </div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }
  if (rel.station_banks && rel.station_banks.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Timetable Planning Corridors / Station Banks (${rel.station_banks.length})</h4>
        ${rel.station_banks
          .map(
            (sb) => `
          <div class="rel-card" onclick="loadAssetDetails('stationbank', ${sb.bank_id}, true)">
            <div class="rel-card-top">
              <span style="color:#c4b5fd">Corridor ${escapeHtml(sb.code || "")}</span>
              <span style="color:#38bdf8">Illuminate Corridor &rarr;</span>
            </div>
            <div class="rel-card-sub">${escapeHtml(sb.description || "")}</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }
  return sections;
}

/** Collect Topology & Cross-Links sections for `edge` assets. */
function buildEdgeRelatedSections(rel) {
  const sections = [];
  if (rel.blocks && rel.blocks.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Signalling Blocks Traversing Edge (${rel.blocks.length})</h4>
        ${rel.blocks
          .map(
            (b) => `
          <div class="rel-card" onclick="loadAssetDetails('block', ${b.pk}, true)">
            <div class="rel-card-top"><span style="color:#10b981">Block #${b.block_id}</span><span>Pre-sig ${b.pre_signal_distance}m</span></div>
            <div class="rel-card-sub">Sight distance: ${b.sight_distance}m • ATC: ${b.atc_flag}</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }
  if (rel.routes && rel.routes.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Station Signalling Routes Traversing Edge (${rel.routes.length})</h4>
        ${rel.routes
          .map(
            (r) => `
          <div class="rel-card" onclick="loadAssetDetails('route', ${r.pk}, true)">
            <div class="rel-card-top"><span style="color:#10b981">Route #${r.route_id}</span><span>${escapeHtml(r.station_name || "")}</span></div>
            <div class="rel-card-sub">Station ID #${r.station_id || "—"}</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }
  if (rel.velocity_profiles && rel.velocity_profiles.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Train Velocity Profile Overrides (${rel.velocity_profiles.length})</h4>
        <table class="prop-table">
          <tbody>
            ${rel.velocity_profiles
              .map(
                (vp) => `
              <tr>
                <th>${escapeHtml(vp.profile_name)}</th>
                <td>${vp.vmax} km/h (${vp.vmax_mph} mph)</td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>
      </div>
    `);
  }
  if (rel.start_node || rel.end_node) {
    sections.push(`
      <div class="rel-section">
        <h4>Endpoint Track Nodes</h4>
        ${
          rel.start_node
            ? `<div class="rel-card" onclick="loadAssetDetails('node', ${rel.start_node.node_id}, true)">
                <div class="rel-card-top"><span>Start Node #${rel.start_node.node_id}</span><span>(${rel.start_node.netx}, ${rel.start_node.nety})</span></div>
                <div class="rel-card-sub">KM Value: ${rel.start_node.km_value}m • Angle: ${rel.start_node.angle}°</div>
              </div>`
            : ""
        }
        ${
          rel.end_node
            ? `<div class="rel-card" onclick="loadAssetDetails('node', ${rel.end_node.node_id}, true)">
                <div class="rel-card-top"><span>End Node #${rel.end_node.node_id}</span><span>(${rel.end_node.netx}, ${rel.end_node.nety})</span></div>
                <div class="rel-card-sub">KM Value: ${rel.end_node.km_value}m • Angle: ${rel.end_node.angle}°</div>
              </div>`
            : ""
        }
      </div>
    `);
  }
  if (rel.station_tracks && rel.station_tracks.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Station Platform / Running Tracks (${rel.station_tracks.length})</h4>
        ${rel.station_tracks
          .map(
            (t) => `
          <div class="rel-card" onclick="loadAssetDetails('track', ${t.pk}, true)">
            <div class="rel-card-top"><span>${escapeHtml(t.station_name || "")} — ${escapeHtml(t.name)}</span></div>
            <div class="rel-card-sub">${escapeHtml(t.description || "")}</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }
  if (rel.attribute_sections && rel.attribute_sections.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Corridor Attribute Sections (${rel.attribute_sections.length})</h4>
        ${rel.attribute_sections
          .map(
            (a) => `
          <div class="rel-card" onclick="loadAssetDetails('attributesection', ${a.pk}, true)">
            <div class="rel-card-top"><span>${escapeHtml(a.description)} (${escapeHtml(a.short_description)})</span><span>ELR: ${escapeHtml(a.elr || "—")}</span></div>
            <div class="rel-card-sub">${(a.attributes || []).length} corridor attributes</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }
  return sections;
}

/** Collect Topology & Cross-Links sections for `node` assets. */
function buildNodeRelatedSections(rel) {
  const sections = [];
  if (rel.blocks && rel.blocks.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Signalling Blocks Through Node (${rel.blocks.length})</h4>
        ${rel.blocks
          .map(
            (b) => `
          <div class="rel-card" onclick="loadAssetDetails('block', ${b.pk}, true)">
            <div class="rel-card-top"><span style="color:#10b981">Block #${b.block_id}</span><span>Pre-sig ${b.pre_signal_distance}m</span></div>
            <div class="rel-card-sub">Sighting distance: ${b.sight_distance}m</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }
  if (rel.signals_at_node && rel.signals_at_node.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Signals at Node (${rel.signals_at_node.length})</h4>
        ${rel.signals_at_node
          .map(
            (s) => `
          <div class="rel-card" onclick="loadAssetDetails('signal', ${s.pk}, true)">
            <div class="rel-card-top"><span>${escapeHtml(s.name || s.master_name)}</span><span>Master #${s.signal_id}</span></div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }
  if (rel.connected_edges && rel.connected_edges.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Connected Track Edges (${rel.connected_edges.length})</h4>
        ${rel.connected_edges
          .map(
            (e) => `
          <div class="rel-card" onclick="loadAssetDetails('edge', ${e.pk}, true)">
            <div class="rel-card-top"><span>Edge #${e.edge_id} (${e.start_node_id} → ${e.end_node_id})</span><span>${e.length}m</span></div>
            <div class="rel-card-sub">Vmax: ${e.vmax} km/h • Grad: ${e.gradient}‰ • ${e.electrified ? "Electrified" : "Diesel"}</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }
  if (rel.station_tracks && rel.station_tracks.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Station Tracks (${rel.station_tracks.length})</h4>
        ${rel.station_tracks
          .map(
            (t) => `
          <div class="rel-card" onclick="loadAssetDetails('track', ${t.pk}, true)">
            <div class="rel-card-top"><span>${escapeHtml(t.station_name || "")} — ${escapeHtml(t.name)}</span></div>
            <div class="rel-card-sub">${escapeHtml(t.description || "")}</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }
  return sections;
}

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

/** Collect Topology & Cross-Links sections for `track` assets. */
function buildTrackRelatedSections(rel) {
  const sections = [];
  if (rel.blocks && rel.blocks.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Signalling Blocks Traversing Track (${rel.blocks.length})</h4>
        ${rel.blocks
          .map(
            (b) => `
          <div class="rel-card" onclick="loadAssetDetails('block', ${b.pk}, true)">
            <div class="rel-card-top"><span style="color:#10b981">Block #${b.block_id} (${b.shunting_flag ? "Shunt" : "Main"})</span><span>Pre-sig ${b.pre_signal_distance}m</span></div>
            <div class="rel-card-sub">Sight distance: ${b.sight_distance}m • ATC: ${b.atc_flag}</div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }
  if (rel.routes && rel.routes.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Station Signalling Routes Traversing Track (${rel.routes.length})</h4>
        ${rel.routes
          .map(
            (r) => `
          <div class="rel-card" onclick="loadAssetDetails('route', ${r.pk}, true)">
            <div class="rel-card-top"><span style="color:#10b981">Route #${r.route_id}</span><span>Station #${r.station_id || "—"}</span></div>
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

/** Collect Topology & Cross-Links sections for `modeltrain` and `tractionunit` assets. */
function buildTractionRelatedSections(data, rel) {
  const sections = [buildTractionPhysicsWidget(data)];

  if (rel.traction_units && rel.traction_units.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Traction Unit Consist &amp; Running Resistance (${rel.traction_units.length})</h4>
        ${rel.traction_units
          .map(
            (u) => `
          <div class="rel-card" onclick="loadAssetDetails('tractionunit', ${u.id}, false)">
            <div class="rel-card-top">
              <span style="color:#c4b5fd">#${u.seq} Traction Unit ${escapeHtml(u.name)} — ${escapeHtml(u.desc)}</span>
              <span>${escapeHtml(u.type_code)} (${escapeHtml(u.motor_desc)})</span>
            </div>
            <div class="rel-card-sub">
              ${u.length_m}m • ${u.weight_t}t • Max ${u.max_speed_kmh} km/h (${u.max_speed_mph} mph) • Starting Effort: ${u.max_force_kn} kN • Peak Power: ${u.max_power_kw} kW
            </div>
            <div class="rel-card-sub" style="margin-top:3px;color:#38bdf8">
              Davis Drag R(v) = ${u.coeffA} + ${u.coeffB}·v + ${u.coeffC}·v² N • Retardation: ${u.retardation} m/s²
            </div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }

  if (rel.coach) {
    const c = rel.coach;
    sections.push(`
      <div class="rel-section">
        <h4>Trailing Coaching Stock / Wagon Template (&lt;coach&gt;)</h4>
        <div class="rel-card" style="cursor:default">
          <div class="rel-card-top">
            <span>${escapeHtml(c.name)} — ${escapeHtml(c.desc)}</span>
            <span>Type: ${escapeHtml(c.coach_type)}</span>
          </div>
          <div class="rel-card-sub">
            Vehicle Length: ${c.unit_length_m}m • Vehicle Weight: ${c.unit_weight_t}t • Max Speed: ${c.max_speed_kmh} km/h
          </div>
        </div>
      </div>
    `);
  }

  if (rel.transitions && rel.transitions.length) {
    sections.push(`
      <div class="rel-section">
        <h4>En-Route Timing Load Transitions (&lt;mtchangetransiting&gt;) (${rel.transitions.length})</h4>
        ${rel.transitions
          .map(
            (tr) => `
          <div class="rel-card" onclick="loadAssetDetails('modeltrain', ${
            tr.from_id == data.id ? tr.to_id : tr.from_id
          }, false)">
            <div class="rel-card-top">
              <span>${escapeHtml(tr.from_name)} &harr; ${escapeHtml(tr.to_name)}</span>
              <span style="color:#38bdf8">Inspect Linked Load &rarr;</span>
            </div>
          </div>`
          )
          .join("")}
      </div>
    `);
  }

  if (rel.model_trains && rel.model_trains.length) {
    sections.push(`
      <div class="rel-section">
        <h4>Timing Loads (&lt;modeltrain&gt;) Powered by This Traction Unit (${rel.model_trains.length})</h4>
        ${rel.model_trains
          .map(
            (mt) => `
          <div class="rel-card" onclick="loadAssetDetails('modeltrain', ${mt.id}, false)">
            <div class="rel-card-top">
              <span style="color:#38bdf8">${escapeHtml(mt.name)} (${escapeHtml(mt.publicname)})</span>
              <span>${mt.maxspeed_kmh} km/h (${mt.maxspeed_mph} mph)</span>
            </div>
            <div class="rel-card-sub">
              ${escapeHtml(mt.desc)} • Profile: ${escapeHtml(mt.profile_name)} • Trailing: ${mt.coaches_length_m}m / ${mt.coaches_weight_t}t
            </div>
          </div>`
          )
          .join("")}
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

