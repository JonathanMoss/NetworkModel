/**
 * @fileoverview Mileage conversion utilities, HTML5 Canvas Elevation &
 * Curvature Profile renderer, and compact Inspector elevation SVG builder.
 */

function mileYardsToTotalYards(val) {
  if (val === null || val === undefined) return 0;
  const v = Math.abs(Number(val));
  const miles = Math.floor(v);
  const yards = Math.round((v - miles) * 10000);
  const total = miles * 1760 + yards;
  return Number(val) < 0 ? -total : total;
}

function totalYardsToMileage(totalYards) {
  if (totalYards === null || totalYards === undefined || isNaN(totalYards)) return "0.0000";
  const sign = totalYards < 0 ? "-" : "";
  const absY = Math.abs(totalYards);
  const m = Math.floor(absY / 1760);
  const y = Math.round(absY % 1760);
  return `${sign}${m}.${String(y).padStart(4, "0")}`;
}

function formatMileageYards(totalYards) {
  if (totalYards === null || totalYards === undefined || isNaN(totalYards)) return "0 mi 0 yd";
  const sign = totalYards < 0 ? "-" : "";
  const absY = Math.abs(totalYards);
  const m = Math.floor(absY / 1760);
  const y = Math.round(absY % 1760);
  return `${sign}${m} mi ${y} yd`;
}

function getAssetElr(assetData) {
  if (!assetData) return "";
  return String(
    assetData.raw_attrs?.ELR ||
    assetData.raw_attrs?.elr ||
    assetData.decoded?.["Engineer's Line Reference (ELR)"] ||
    assetData.decoded?.["ELR"] ||
    assetData.decoded?.["ELR Code"] ||
    (assetData.title && assetData.title.match(/ELR\s+(?:Corridor\s+)?([A-Z0-9]+)/i)?.[1]) ||
    ""
  ).trim().toUpperCase();
}

function getAssetTrackId(assetData) {
  if (!assetData) return "";
  return String(
    assetData.raw_attrs?.TRACK_ID ||
    assetData.raw_attrs?.track_id ||
    assetData.decoded?.["Track ID"] ||
    ""
  ).trim();
}

function getAssetTrackName(assetData) {
  if (!assetData) return "";
  return String(
    assetData.raw_attrs?.TRACK_NAME ||
    assetData.raw_attrs?.track_name ||
    assetData.decoded?.["Track Name"] ||
    assetData.decoded?.["Track ID & Classification"] ||
    ""
  ).trim();
}

function getAssetStartMileage(assetData) {
  if (!assetData) return null;
  return assetData.raw_attrs?.START ?? assetData.raw_attrs?.start_mile_yards ?? assetData.decoded?.["Start Mileage"];
}

function getAssetEndMileage(assetData) {
  if (!assetData) return null;
  return assetData.raw_attrs?.END ?? assetData.raw_attrs?.end_mile_yards ?? assetData.decoded?.["End Mileage"];
}

function renderProfileCanvas() {
  const pCanvas = document.getElementById("profile-canvas");
  if (!pCanvas) return;
  const pDrawer = document.getElementById("profile-drawer");
  if (!pDrawer || pDrawer.classList.contains("hidden")) return;
  if (!STATE.profileDrawer || !STATE.profileDrawer.data) return;

  const data = STATE.profileDrawer.data;
  const points = (data.points || []).filter((p) => p.elevation_m !== null && p.elevation_m !== undefined);
  const curvature = data.curvature || [];

  const rect = pCanvas.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return;
  const dpr = window.devicePixelRatio || 1;
  pCanvas.width = rect.width * dpr;
  pCanvas.height = rect.height * dpr;
  const pCtx = pCanvas.getContext("2d");
  pCtx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const W = rect.width;
  const H = rect.height;

  pCtx.clearRect(0, 0, W, H);

  if (points.length < 2 && curvature.length === 0) {
    pCtx.fillStyle = "#94a3b8";
    pCtx.font = "600 12px Inter, sans-serif";
    pCtx.textAlign = "center";
    pCtx.fillText("No elevation or curvature survey samples available for this route.", W / 2, H / 2);
    return;
  }

  // Calculate Yard bounds
  let minYards = Infinity;
  let maxYards = -Infinity;
  if (points.length) {
    minYards = Math.min(minYards, points[0].total_yards);
    maxYards = Math.max(maxYards, points[points.length - 1].total_yards);
  }
  if (curvature.length) {
    minYards = Math.min(minYards, curvature[0].total_yards_from);
    maxYards = Math.max(maxYards, curvature[curvature.length - 1].total_yards_to);
  }
  if (!isFinite(minYards) || !isFinite(maxYards) || maxYards <= minYards) {
    maxYards = minYards + 1760;
  }
  const yardSpan = Math.max(1, maxYards - minYards);

  // Layout geometry
  const padL = 54;
  const padR = 24;
  const padT = 16;
  const padB = 22; // space for mileage X-axis
  const curvH = 26; // height of curvature ribbon strip
  const curvY = H - padB - curvH;
  const elevH = Math.max(30, curvY - padT - 16);
  const elevB = padT + elevH;
  const plotW = Math.max(10, W - padL - padR);

  STATE.profileDrawer.geometry = {
    padL,
    padR,
    padT,
    padB,
    curvH,
    curvY,
    elevH,
    elevB,
    plotW,
    minYards,
    maxYards,
    yardSpan,
    W,
    H,
  };

  function yardToX(yd) {
    return padL + ((yd - minYards) / yardSpan) * plotW;
  }

  // Calculate Elevation bounds
  let minElev = 0;
  let maxElev = 50;
  if (points.length) {
    const elevs = points.map((p) => p.elevation_m);
    minElev = Math.min(...elevs);
    maxElev = Math.max(...elevs);
  }
  const eMin = Math.floor(Math.max(0, minElev - 5));
  const eMax = Math.ceil(maxElev + 5);
  const eSpan = Math.max(10, eMax - eMin);

  function elevToY(e) {
    return elevB - ((e - eMin) / eSpan) * elevH;
  }
  STATE.profileDrawer.geometry.eMin = eMin;
  STATE.profileDrawer.geometry.eSpan = eSpan;

  // 1. Draw Elevation Grid Lines & Y-Axis Labels
  const gridStep = eSpan <= 20 ? 5 : eSpan <= 60 ? 10 : eSpan <= 150 ? 25 : 50;
  pCtx.strokeStyle = "rgba(148, 163, 184, 0.12)";
  pCtx.lineWidth = 1;
  pCtx.fillStyle = "#94a3b8";
  pCtx.font = "600 9.5px Inter, sans-serif";
  pCtx.textAlign = "right";
  pCtx.textBaseline = "middle";

  const firstGrid = Math.ceil(eMin / gridStep) * gridStep;
  for (let val = firstGrid; val <= eMax; val += gridStep) {
    const gy = elevToY(val);
    if (gy < padT || gy > elevB) continue;
    pCtx.beginPath();
    pCtx.moveTo(padL, gy);
    pCtx.lineTo(padL + plotW, gy);
    pCtx.stroke();
    pCtx.fillText(`${val}m`, padL - 7, gy);
  }

  // Baseline separator under elevation graph
  pCtx.strokeStyle = "rgba(148, 163, 184, 0.25)";
  pCtx.beginPath();
  pCtx.moveTo(padL, elevB);
  pCtx.lineTo(padL + plotW, elevB);
  pCtx.stroke();

  // 2. Draw Elevation Profile Area & Curve
  if (points.length >= 2) {
    const areaGrad = pCtx.createLinearGradient(0, padT, 0, elevB);
    areaGrad.addColorStop(0, "rgba(6, 182, 212, 0.38)");
    areaGrad.addColorStop(1, "rgba(6, 182, 212, 0.03)");

    pCtx.beginPath();
    pCtx.moveTo(yardToX(points[0].total_yards), elevB);
    for (const pt of points) {
      pCtx.lineTo(yardToX(pt.total_yards), elevToY(pt.elevation_m));
    }
    pCtx.lineTo(yardToX(points[points.length - 1].total_yards), elevB);
    pCtx.closePath();
    pCtx.fillStyle = areaGrad;
    pCtx.fill();

    // Elevation stroke line
    pCtx.beginPath();
    pCtx.moveTo(yardToX(points[0].total_yards), elevToY(points[0].elevation_m));
    for (let i = 1; i < points.length; i++) {
      pCtx.lineTo(yardToX(points[i].total_yards), elevToY(points[i].elevation_m));
    }
    pCtx.strokeStyle = "#06b6d4";
    pCtx.lineWidth = 2.4;
    pCtx.lineCap = "round";
    pCtx.lineJoin = "round";
    pCtx.shadowColor = "rgba(6, 182, 212, 0.55)";
    pCtx.shadowBlur = 8;
    pCtx.stroke();
    pCtx.shadowBlur = 0;

    // Survey tenth-mile points (drawn only when zoom/sample count is reasonable)
    if (points.length <= 160) {
      pCtx.fillStyle = "#38bdf8";
      for (const pt of points) {
        pCtx.beginPath();
        pCtx.arc(yardToX(pt.total_yards), elevToY(pt.elevation_m), 2.2, 0, Math.PI * 2);
        pCtx.fill();
      }
    }

    // Place labels at locations along route
    const namedPts = points.filter((p) => p.place_name);
    if (namedPts.length) {
      const distinctPlaces = [];
      const seen = new Set();
      for (const np of namedPts) {
        if (!seen.has(np.place_name)) {
          seen.add(np.place_name);
          distinctPlaces.push(np);
        }
      }
      const stepN = Math.max(1, Math.floor(distinctPlaces.length / 5));
      pCtx.fillStyle = "#e2e8f0";
      pCtx.font = "600 9px Inter, sans-serif";
      pCtx.textAlign = "center";
      for (let i = 0; i < distinctPlaces.length; i += stepN) {
        const np = distinctPlaces[i];
        const nx = yardToX(np.total_yards);
        const ny = elevToY(np.elevation_m);
        pCtx.strokeStyle = "rgba(56, 189, 248, 0.3)";
        pCtx.setLineDash([2, 2]);
        pCtx.beginPath();
        pCtx.moveTo(nx, ny);
        pCtx.lineTo(nx, padT + 4);
        pCtx.stroke();
        pCtx.setLineDash([]);
        pCtx.fillText(np.place_name.slice(0, 18), nx, padT + 2);
      }
    }
  }

  // 3. Draw Curvature & Cant Ribbon
  pCtx.fillStyle = "rgba(15, 23, 42, 0.85)";
  pCtx.fillRect(padL, curvY, plotW, curvH);
  pCtx.strokeStyle = "rgba(148, 163, 184, 0.22)";
  pCtx.strokeRect(padL, curvY, plotW, curvH);

  pCtx.fillStyle = "#94a3b8";
  pCtx.font = "700 9px Inter, sans-serif";
  pCtx.textAlign = "right";
  pCtx.textBaseline = "middle";
  pCtx.fillText("CURVE", padL - 7, curvY + curvH / 2);

  if (curvature.length) {
    for (const c of curvature) {
      const bx0 = Math.max(padL, yardToX(c.total_yards_from));
      const bx1 = Math.min(padL + plotW, yardToX(c.total_yards_to));
      const bw = Math.max(1.0, bx1 - bx0);
      const col = getCurvatureColor(c.radius_avg_m);

      pCtx.fillStyle = col;
      pCtx.fillRect(bx0, curvY + 1, bw, curvH - 2);

      // Only stroke border if block is wide enough to avoid black border fill blowout on dense corridors
      if (bw >= 5) {
        pCtx.strokeStyle = "rgba(15, 23, 42, 0.4)";
        pCtx.strokeRect(bx0, curvY + 1, bw, curvH - 2);
      }

      if (bw > 42) {
        pCtx.fillStyle = "#ffffff";
        pCtx.font = "700 9px Inter, sans-serif";
        pCtx.textAlign = "center";
        const lbl = c.radius_avg_m ? `R=${Math.round(c.radius_avg_m)}m` : "Straight";
        pCtx.fillText(lbl, bx0 + bw / 2, curvY + curvH / 2 + 1);
      }
    }
  }

  // 4. Draw Mileage X-Axis & Ticks
  pCtx.strokeStyle = "rgba(148, 163, 184, 0.25)";
  pCtx.beginPath();
  pCtx.moveTo(padL, H - 18);
  pCtx.lineTo(padL + plotW, H - 18);
  pCtx.stroke();

  const miSpan = yardSpan / 1760;
  let tickYards = 1760;
  if (miSpan <= 1.5) tickYards = 440;
  else if (miSpan <= 4) tickYards = 880;
  else if (miSpan <= 12) tickYards = 1760;
  else if (miSpan <= 30) tickYards = 1760 * 2;
  else if (miSpan <= 60) tickYards = 1760 * 5;
  else if (miSpan <= 120) tickYards = 1760 * 10;
  else tickYards = 1760 * 20;

  const startTickYd = Math.ceil(minYards / tickYards) * tickYards;
  pCtx.fillStyle = "#94a3b8";
  pCtx.font = "600 9px Inter, sans-serif";
  pCtx.textAlign = "center";
  pCtx.textBaseline = "top";

  for (let yd = startTickYd; yd <= maxYards; yd += tickYards) {
    const tx = yardToX(yd);
    if (tx < padL || tx > padL + plotW) continue;
    pCtx.strokeStyle = "rgba(148, 163, 184, 0.35)";
    pCtx.beginPath();
    pCtx.moveTo(tx, H - 18);
    pCtx.lineTo(tx, H - 14);
    pCtx.stroke();

    const m = Math.floor(yd / 1760);
    const yRem = Math.round(yd % 1760);
    const lbl = yRem === 0 ? `${m} mi` : `${m}m ${yRem}y`;
    pCtx.fillText(lbl, tx, H - 12);
  }

  // 5. Draw Active Hover Scrub Line & Point
  if (STATE.profileDrawer.hoverItem) {
    const hi = STATE.profileDrawer.hoverItem;
    const hx = yardToX(hi.total_yards);
    const hy = hi.elevation_m !== null ? elevToY(hi.elevation_m) : elevB;

    // Vertical neon scrub line
    pCtx.strokeStyle = "#38bdf8";
    pCtx.lineWidth = 1.8;
    pCtx.setLineDash([3, 3]);
    pCtx.beginPath();
    pCtx.moveTo(hx, padT);
    pCtx.lineTo(hx, curvY + curvH);
    pCtx.stroke();
    pCtx.setLineDash([]);

    // Glowing point on curve
    if (hi.elevation_m !== null) {
      pCtx.fillStyle = "#06b6d4";
      pCtx.shadowColor = "#38bdf8";
      pCtx.shadowBlur = 10;
      pCtx.beginPath();
      pCtx.arc(hx, hy, 5.5, 0, Math.PI * 2);
      pCtx.fill();
      pCtx.shadowBlur = 0;

      pCtx.strokeStyle = "#ffffff";
      pCtx.lineWidth = 2;
      pCtx.beginPath();
      pCtx.arc(hx, hy, 5.5, 0, Math.PI * 2);
      pCtx.stroke();
    }
  }
}
function renderElevationProfileSvg(nfData) {
  if (!nfData || !nfData.points || nfData.points.length < 2) return "";
  const pts = nfData.points.filter((p) => p.elevation_m !== null && p.elevation_m !== undefined);
  if (pts.length < 2) return "";

  const elevs = pts.map((p) => p.elevation_m);
  const minE = Math.floor(Math.min(...elevs));
  const maxE = Math.ceil(Math.max(...elevs));
  const rangeE = Math.max(10, maxE - minE);

  const W = 360;
  const H = 115;
  const padL = 36;
  const padR = 14;
  const padT = 14;
  const padB = 26;
  const chartW = W - padL - padR;
  const chartH = H - padT - padB;

  const minYard = pts[0].total_yards;
  const maxYard = pts[pts.length - 1].total_yards;
  const yardSpan = Math.max(1, maxYard - minYard);

  const coords = pts.map((p) => {
    const x = padL + ((p.total_yards - minYard) / yardSpan) * chartW;
    const y = padT + (1 - (p.elevation_m - minE) / rangeE) * chartH;
    return [x.toFixed(1), y.toFixed(1)];
  });

  const polylinePts = coords.map((c) => c.join(",")).join(" ");
  const areaPts = `${padL},${padT + chartH} ${polylinePts} ${coords[coords.length - 1][0]},${padT + chartH}`;

  // Curvature severity markers along bottom strip
  let curvBars = "";
  if (nfData.curvature && nfData.curvature.length) {
    for (const c of nfData.curvature) {
      const bx0 = Math.max(padL, padL + ((c.total_yards_from - minYard) / yardSpan) * chartW);
      const bx1 = Math.min(padL + chartW, padL + ((c.total_yards_to - minYard) / yardSpan) * chartW);
      const bw = Math.max(2.0, bx1 - bx0);
      const col = getCurvatureColor(c.radius_avg_m);
      curvBars += `<rect x="${bx0.toFixed(1)}" y="${H - 7}" width="${bw.toFixed(1)}" height="5" fill="${col}" opacity="0.9"><title>R=${c.radius_avg_m}m (${c.radius_avg_class})</title></rect>`;
    }
  }

  return `
    <div class="nf-profile-container">
      <div class="nf-profile-header">
        <span class="nf-profile-title">Elevation &amp; Curvature Profile</span>
        <span class="nf-profile-stats">${minE}m &rarr; ${maxE}m &bull; &Delta; ${(maxE - minE)}m &bull; ${pts.length} samples</span>
      </div>
      <svg class="nf-profile-svg" viewBox="0 0 ${W} ${H}">
        <defs>
          <linearGradient id="nf-elev-grad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#38bdf8" stop-opacity="0.45"/>
            <stop offset="100%" stop-color="#38bdf8" stop-opacity="0.02"/>
          </linearGradient>
        </defs>
        <!-- Grid lines -->
        <line x1="${padL}" y1="${padT}" x2="${W - padR}" y2="${padT}" stroke="rgba(255,255,255,0.08)" stroke-dasharray="2,2"/>
        <line x1="${padL}" y1="${(padT + chartH / 2).toFixed(1)}" x2="${W - padR}" y2="${(padT + chartH / 2).toFixed(1)}" stroke="rgba(255,255,255,0.08)" stroke-dasharray="2,2"/>
        <line x1="${padL}" y1="${padT + chartH}" x2="${W - padR}" y2="${padT + chartH}" stroke="rgba(255,255,255,0.18)"/>

        <!-- Y axis elevation labels -->
        <text x="${padL - 4}" y="${padT + 4}" font-size="9" fill="#94a3b8" text-anchor="end">${maxE}m</text>
        <text x="${padL - 4}" y="${(padT + chartH / 2 + 3).toFixed(1)}" font-size="9" fill="#94a3b8" text-anchor="end">${Math.round((minE + maxE) / 2)}m</text>
        <text x="${padL - 4}" y="${padT + chartH + 2}" font-size="9" fill="#94a3b8" text-anchor="end">${minE}m</text>

        <!-- Shaded area and line -->
        <polygon points="${areaPts}" fill="url(#nf-elev-grad)"/>
        <polyline points="${polylinePts}" fill="none" stroke="#38bdf8" stroke-width="2" stroke-linecap="round"/>

        <!-- X axis mileage labels -->
        <text x="${padL}" y="${H - 12}" font-size="8.5" fill="#64748b" text-anchor="start">${pts[0].mileage || ''}</text>
        <text x="${W - padR}" y="${H - 12}" font-size="8.5" fill="#64748b" text-anchor="end">${pts[pts.length - 1].mileage || ''}</text>

        <!-- Bottom Curvature Severity Strip -->
        ${curvBars}
      </svg>
    </div>
  `;
}
