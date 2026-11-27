/**
 * @fileoverview Inspector "Topology & Cross-Links" renderers for TPS topology
 * and traction assets (stations, edges, nodes, station tracks, timing loads).
 */

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
