/**
 * @fileoverview Dynamic track/signal/curvature/elevation colour resolvers
 * and Colour Legend panel renderer.
 */

function getSignalInterlockingInfo(ilockId, bumper) {
  if (bumper === 1) {
    return { color: "#ef4444", label: "Buffer Stop / Bumper", short: "BUF", aspects: 0 };
  }
  return (
    INTERLOCKING_STYLES[ilockId] || {
      color: "#f59e0b",
      label: `Interlocking #${ilockId || 0}`,
      short: "SIG",
      aspects: 0,
    }
  );
}

function getEdgeColor(vmax, electrified, gradient, vpCount = 0) {
  if (STATE.colorMode === "electrified") {
    return electrified ? "#38bdf8" : "#f59e0b";
  }
  if (STATE.colorMode === "signalling") {
    return "#475569";
  }
  if (STATE.colorMode === "timing") {
    return vpCount > 0 ? "#22d3ee" : "#334155";
  }
  if (STATE.colorMode === "vmax") {
    if (vmax >= 160) return "#f43f5e";
    if (vmax >= 120) return "#f97316";
    if (vmax >= 80) return "#eab308";
    if (vmax >= 45) return "#10b981";
    return "#6366f1";
  }
  if (STATE.colorMode === "gradient") {
    if (gradient > 2.0) return "#f43f5e";
    if (gradient < -2.0) return "#38bdf8";
    return "#10b981";
  }
  return "#94a3b8";
}

function getCurvatureColor(radiusAvgM) {
  if (radiusAvgM === null || radiusAvgM === undefined) return "#64748b";
  const r = Number(radiusAvgM);
  if (r <= 400) return "#f43f5e"; // Extremely/Very Tight
  if (r <= 800) return "#f97316"; // Moderately Tight
  if (r <= 1200) return "#f59e0b"; // Moderate
  if (r <= 2500) return "#06b6d4"; // Moderately Flat
  return "#3b82f6"; // Flat / Straight
}

function getElevationColor(elevM) {
  if (elevM === null || elevM === undefined) return "#64748b";
  const e = Number(elevM);
  if (e <= 30) return "#22c55e";   // Coastal / Lowland
  if (e <= 100) return "#84cc16";  // Gentle plain
  if (e <= 200) return "#eab308";  // Rolling hills
  if (e <= 300) return "#f97316";  // High moorland
  return "#ec4899";                // Mountain summit
}

function getNwrTrackColor(trackId, ownerCodeOrStr, elr) {
  if (STATE.nwrColorMode === "uniform") {
    return (STATE.layerStyles && STATE.layerStyles.nwr_centrelines && STATE.layerStyles.nwr_centrelines.color) || "#38bdf8";
  }
  const tid = String(trackId || "");
  const elrCode = String(elr || "").toUpperCase();
  const fusionStats = (STATE.nwrOverview && STATE.nwrOverview.elr_fusion_stats) || {};
  const elrStat = fusionStats[elrCode];

  if (STATE.nwrColorMode === "curvature") {
    if (elrStat) {
      const r = elrStat.avg_r ?? elrStat.min_r;
      if (r !== undefined && r !== null) return getCurvatureColor(r);
    }
    return "#64748b";
  }

  if (STATE.nwrColorMode === "elevation") {
    if (elrStat && elrStat.avg_elev !== undefined && elrStat.avg_elev !== null) {
      return getElevationColor(elrStat.avg_elev);
    }
    return "#64748b";
  }

  if (STATE.nwrColorMode === "ownership") {
    if (typeof ownerCodeOrStr === "number") {
      if (ownerCodeOrStr === 0) return "#38bdf8";
      if (ownerCodeOrStr === 1) return "#f43f5e";
      return "#f59e0b";
    }
    const s = String(ownerCodeOrStr || "");
    if (s.includes("NETWORK RAIL:100%")) return "#38bdf8";
    if (s.includes("NETWORK RAIL:0%") && !s.includes("THIRD PARTY:0%")) return "#f43f5e";
    return "#f59e0b";
  }
  if (STATE.nwrColorMode === "direction") {
    const d1 = tid[0];
    if (d1 === "1") return "#38bdf8"; // UP
    if (d1 === "2") return "#f97316"; // DOWN
    if (d1 === "3") return "#a855f7"; // BI-DIRECTIONAL
    return "#94a3b8";
  }
  // Default: track_type (2nd digit of TRACK_ID per Appendix A)
  const d2 = tid[1];
  if (d2 === "1") return "#38bdf8"; // MAIN FAST
  if (d2 === "2") return "#10b981"; // SLOW
  if (d2 === "4") return "#eab308"; // SINGLE
  if (d2 === "5") return "#818cf8"; // LOOP
  if (d2 === "3") return "#f97316"; // GOODS
  if (d2 === "6" || d2 === "7") return "#f43f5e"; // TERMINAL / CROSSOVER
  return "#64748b"; // SIDING / OTHER
}

function updateLegend() {
  const box = document.getElementById("legend-box");
  let items = [];
  if (STATE.activeView === "nwr") {
    if (STATE.nwrColorMode === "uniform") {
      items = [
        [(STATE.layerStyles && STATE.layerStyles.nwr_centrelines && STATE.layerStyles.nwr_centrelines.color) || "#38bdf8", "Track Centre Lines (Custom Layer Colour)"],
        [(STATE.layerStyles && STATE.layerStyles.nwr_elrs && STATE.layerStyles.nwr_elrs.color) || "#a855f7", "ELR Reference Lines (Custom Layer Colour)"],
        [(STATE.layerStyles && STATE.layerStyles.nwr_curvature && STATE.layerStyles.nwr_curvature.color) || "#f43f5e", "Track Curvature (Custom Layer Colour)"],
      ];
    } else if (STATE.nwrColorMode === "track_type") {
      items = [
        ["#38bdf8", "Main Fast (x100)"],
        ["#10b981", "Slow Line (x200)"],
        ["#eab308", "Single Line (x400)"],
        ["#818cf8", "Loop (x500)"],
        ["#f97316", "Goods Line (x300)"],
        ["#f43f5e", "Crossover / Terminal (x600/x700)"],
        ["#64748b", "Siding / Engine (x800/x900)"],
        ["#a855f7", "ELR Route Reference Line"],
      ];
    } else if (STATE.nwrColorMode === "direction") {
      items = [
        ["#38bdf8", "Up Direction (1xxx)"],
        ["#f97316", "Down Direction (2xxx)"],
        ["#a855f7", "Reversible / Bi-Directional (3xxx)"],
      ];
    } else if (STATE.nwrColorMode === "curvature") {
      items = [
        ["#f43f5e", "Tight Curve (≤ 400 m)"],
        ["#f97316", "Moderately Tight (401–800 m)"],
        ["#f59e0b", "Moderate Curve (801–1,200 m)"],
        ["#06b6d4", "Moderately Flat (1,201–2,500 m)"],
        ["#3b82f6", "Flat / Straight (> 2,500 m)"],
        ["#64748b", "No Fusion Data / Other"],
      ];
    } else if (STATE.nwrColorMode === "elevation") {
      items = [
        ["#22c55e", "Lowland / Coastal (≤ 30 m)"],
        ["#84cc16", "Undulating Plain (31–100 m)"],
        ["#eab308", "Rolling Hills (101–200 m)"],
        ["#f97316", "High Moorland (201–300 m)"],
        ["#ec4899", "Mountain Summit (> 300 m)"],
        ["#64748b", "No Fusion Data / Other"],
      ];
    } else {
      items = [
        ["#38bdf8", "Network Rail (100%)"],
        ["#f43f5e", "Third Party Owned"],
        ["#f59e0b", "Mixed / Unclassified Ownership"],
      ];
    }
  } else {
    const mode = STATE.colorMode;
    if (mode === "electrified") {
      items = [
        ["#38bdf8", "Electrified (OHLE / 3rd Rail)"],
        ["#f59e0b", "Non-Electrified (Diesel)"],
        ["#818cf8", "Station Platform / Loop Way"],
        ["#64748b", "CAD Schematic Vector"],
      ];
    } else if (mode === "signalling") {
      items = [
        ["#10b981", "4-Aspect Colour Light (IECC / Panel)"],
        ["#38bdf8", "3-Aspect Colour Light (IECC / Panel)"],
        ["#eab308", "2-Aspect Colour Light (IECC / Panel)"],
        ["#f97316", "Semaphore (Home / Starter / Shunt)"],
        ["#a855f7", "Ground Position Light / Limit of Shunt"],
        ["#d946ef", "Safety Overlap (<securitysection>)"],
        ["#ef4444", "Stop Board / Buffer Stop"],
      ];
    } else if (mode === "timing") {
      items = [
        ["#f43f5e", "Mandatory TIPLOC Timing Point (2,380)"],
        ["#38bdf8", "Optional Stop / Passenger Point (6,213)"],
        ["#f59e0b", "Optional Freight / Yard Point (1,374)"],
        ["#a855f7", "Optional Crossing / Junction (813)"],
        ["#22d3ee", "Differential Speed Profile Edge (EPS/HST/MU)"],
        ["#334155", "Standard Line Speed Edge"],
      ];
    } else if (mode === "vmax") {
      items = [
        ["#f43f5e", "High Speed (≥ 160 km/h / 100 mph)"],
        ["#f97316", "Fast Main (120–159 km/h)"],
        ["#eab308", "Medium (80–119 km/h)"],
        ["#10b981", "Regional / Slow (45–79 km/h)"],
        ["#6366f1", "Low Speed / Goods (< 45 km/h)"],
      ];
    } else if (mode === "gradient") {
      items = [
        ["#f43f5e", "Rising Gradient (> +2‰)"],
        ["#10b981", "Level / Gentle (-2‰ to +2‰)"],
        ["#38bdf8", "Falling Gradient (< -2‰)"],
      ];
    } else if (mode === "uniform") {
      items = [
        [(STATE.layerStyles && STATE.layerStyles.edges && STATE.layerStyles.edges.color) || "#38bdf8", "Track Edges (Custom Layer Colour)"],
        [(STATE.layerStyles && STATE.layerStyles.tracks && STATE.layerStyles.tracks.color) || "#818cf8", "Station Platforms / Ways (Custom Layer Colour)"],
        [(STATE.layerStyles && STATE.layerStyles.vectors && STATE.layerStyles.vectors.color) || "#64748b", "CAD Schematic Vectors (Custom Layer Colour)"],
        [(STATE.layerStyles && STATE.layerStyles.blocks && STATE.layerStyles.blocks.color) || "#10b981", "Signalling Blocks (Custom Layer Colour)"],
        [(STATE.layerStyles && STATE.layerStyles.polys && STATE.layerStyles.polys.color) || "#a855f7", "Poly Regions (Custom Layer Colour)"],
      ];
    } else {
      items = [
        ["#94a3b8", "Running Track Edge"],
        ["#818cf8", "Station Track / Platform Way"],
        ["#f59e0b", "Signal / Aspect Post"],
      ];
    }
  }
  box.innerHTML = items
    .map(
      ([col, lbl]) => `
      <div class="legend-row">
        <span class="legend-swatch" style="background:${col}"></span>
        <span>${lbl}</span>
      </div>`
    )
    .join("");
}

