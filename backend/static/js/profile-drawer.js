/**
 * @fileoverview Elevation & Curvature Profile Drawer lifecycle, interactive
 * scrub/zoom events, and NWR manual track-override switcher controls.
 */

async function openProfileDrawer(assetData, targetScope = "track") {
  if (!assetData) return;
  STATE.profileDrawer.currentAsset = assetData;

  const isElrAsset = assetData.asset_type === "nwr_elr";
  if (isElrAsset) {
    targetScope = "corridor";
  }
  STATE.profileDrawer.scope = targetScope;

  const pDrawer = document.getElementById("profile-drawer");
  const dockBtn = document.getElementById("btn-dock-profile");
  const scopeBtn = document.getElementById("btn-profile-scope");

  const elr = getAssetElr(assetData);
  const trackId = getAssetTrackId(assetData);
  const trackName = getAssetTrackName(assetData);
  const sMy = getAssetStartMileage(assetData);
  const eMy = getAssetEndMileage(assetData);
  const startYd = sMy !== null && sMy !== undefined ? mileYardsToTotalYards(sMy) : null;
  const endYd = eMy !== null && eMy !== undefined ? mileYardsToTotalYards(eMy) : null;

  STATE.profileDrawer.currentElr = elr;

  // Fetch or retrieve profile data
  let profileData = null;

  if (targetScope === "track") {
    if (assetData.related?.network_fusion?.points?.length) {
      profileData = assetData.related.network_fusion;
    } else if (elr && startYd !== null && endYd !== null) {
      try {
        const url = `/api/network-fusion/profile?elr=${encodeURIComponent(elr)}&start_yards=${Math.min(startYd, endYd)}&end_yards=${Math.max(startYd, endYd)}`;
        const res = await fetch(url);
        if (res.ok) profileData = await res.json();
      } catch (err) {
        console.error("Failed to fetch section profile:", err);
      }
    }
  } else {
    // Corridor Scope
    if (isElrAsset && assetData.related?.network_fusion?.points?.length) {
      profileData = assetData.related.network_fusion;
    } else if (elr) {
      try {
        const url = `/api/network-fusion/profile?elr=${encodeURIComponent(elr)}`;
        const res = await fetch(url);
        if (res.ok) profileData = await res.json();
      } catch (err) {
        console.error("Failed to fetch corridor profile:", err);
      }
    }
  }

  if (!profileData) {
    profileData = assetData.related?.network_fusion || { summary: null, points: [], curvature: [] };
  }

  STATE.profileDrawer.data = profileData;
  STATE.profileDrawer.visible = true;

  // Populate Header
  const titleEl = document.getElementById("profile-title");
  const subEl = document.getElementById("profile-subtitle");
  const lenEl = document.getElementById("prof-len");
  const elevEl = document.getElementById("prof-elev");
  const tightEl = document.getElementById("prof-tightest");

  if (targetScope === "corridor") {
    titleEl.textContent = `ELR ${elr || "Corridor"} Route Profile`;
    if (isElrAsset) {
      scopeBtn.textContent = "Corridor";
      scopeBtn.disabled = true;
      scopeBtn.style.opacity = "0.6";
      scopeBtn.title = "Viewing entire ELR corridor route";
    } else {
      scopeBtn.textContent = "Track Section";
      scopeBtn.disabled = false;
      scopeBtn.style.opacity = "1";
      scopeBtn.title = "Switch back to single track section scope";
    }
  } else {
    titleEl.textContent = `${elr} ${trackId ? `Track ${trackId}` : ""} ${trackName ? `— ${trackName}` : ""}`.trim();
    scopeBtn.textContent = "Full Corridor";
    scopeBtn.disabled = false;
    scopeBtn.style.opacity = "1";
    scopeBtn.title = "Switch to full ELR corridor profile";
  }

  const pts = profileData.points || [];
  const curvs = profileData.curvature || [];
  const summary = profileData.summary;

  if (pts.length || curvs.length) {
    let minYd = pts[0]?.total_yards ?? curvs[0]?.total_yards_from ?? 0;
    let maxYd = pts[pts.length - 1]?.total_yards ?? curvs[curvs.length - 1]?.total_yards_to ?? 0;
    const lenYd = Math.max(0, maxYd - minYd);
    const lenMi = (lenYd / 1760).toFixed(2);
    const lenKm = (lenYd * 0.0009144).toFixed(2);
    lenEl.textContent = `${lenMi} mi (${lenKm} km)`;

    const places = (summary && summary.nearest_places && summary.nearest_places.length)
      ? summary.nearest_places.join(" → ")
      : "";
    subEl.textContent = `Mileage: ${formatMileageYards(minYd)} → ${formatMileageYards(maxYd)}${places ? ` • ${places}` : ""}`;

    if (summary && summary.min_elevation_m !== null && summary.max_elevation_m !== null) {
      const dE = (summary.max_elevation_m - summary.min_elevation_m).toFixed(1);
      elevEl.textContent = `${summary.min_elevation_m}m → ${summary.max_elevation_m}m (Δ${dE}m)`;
    } else {
      elevEl.textContent = "--";
    }

    if (summary && summary.tightest_curve && summary.tightest_curve.radius_min_m) {
      tightEl.textContent = `R=${summary.tightest_curve.radius_min_m}m (${summary.tightest_curve.class || "Tight"})`;
      tightEl.style.color = getCurvatureColor(summary.tightest_curve.radius_min_m);
    } else {
      tightEl.textContent = "Straight";
      tightEl.style.color = "#38bdf8";
    }
  } else {
    subEl.textContent = `ELR: ${elr} • No survey data recorded`;
    lenEl.textContent = "--";
    elevEl.textContent = "--";
    tightEl.textContent = "--";
  }

  pDrawer.classList.remove("hidden");
  dockBtn.classList.add("hidden");

  requestAnimationFrame(() => {
    renderProfileCanvas();
  });
}

function closeProfileDrawer(fullyHide = false) {
  STATE.profileDrawer.visible = false;
  STATE.profileDrawer.hoverItem = null;
  STATE.profileDrawer.scrubWorldPos = null;

  const pDrawer = document.getElementById("profile-drawer");
  const dockBtn = document.getElementById("btn-dock-profile");
  const tooltip = document.getElementById("profile-scrub-tooltip");

  if (pDrawer) pDrawer.classList.add("hidden");
  if (tooltip) tooltip.classList.add("hidden");

  if (fullyHide) {
    STATE.profileDrawer.data = null;
    if (dockBtn) dockBtn.classList.add("hidden");
  } else {
    if (dockBtn && STATE.profileDrawer.data) {
      dockBtn.classList.remove("hidden");
    }
  }

  requestRender();
}

async function toggleProfileScope() {
  if (!STATE.profileDrawer.currentAsset) return;
  if (STATE.profileDrawer.currentAsset.asset_type === "nwr_elr") return;
  const nextScope = STATE.profileDrawer.scope === "track" ? "corridor" : "track";
  await openProfileDrawer(STATE.profileDrawer.currentAsset, nextScope);
}

function setupProfileDrawerEvents() {
  const pCanvas = document.getElementById("profile-canvas");
  const tooltip = document.getElementById("profile-scrub-tooltip");
  const pDrawer = document.getElementById("profile-drawer");
  const dockBtn = document.getElementById("btn-dock-profile");
  const scopeBtn = document.getElementById("btn-profile-scope");
  const collapseBtn = document.getElementById("btn-profile-collapse");
  const closeBtn = document.getElementById("btn-profile-close");

  if (pCanvas) {
    pCanvas.addEventListener("mousemove", (e) => {
      if (!STATE.profileDrawer || !STATE.profileDrawer.data || !STATE.profileDrawer.geometry) return;
      const geo = STATE.profileDrawer.geometry;
      const rect = pCanvas.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;

      if (mouseX < geo.padL || mouseX > geo.padL + geo.plotW) {
        STATE.profileDrawer.hoverItem = null;
        STATE.profileDrawer.scrubWorldPos = null;
        tooltip.classList.add("hidden");
        renderProfileCanvas();
        requestRender();
        return;
      }

      const fraction = (mouseX - geo.padL) / geo.plotW;
      const targetYd = geo.minYards + fraction * geo.yardSpan;

      const pts = (STATE.profileDrawer.data.points || []).filter((p) => p.elevation_m !== null);
      if (!pts.length) return;

      // Fast binary search for nearest point (O(log N))
      let low = 0;
      let high = pts.length - 1;
      while (low <= high) {
        const mid = (low + high) >> 1;
        if (pts[mid].total_yards < targetYd) low = mid + 1;
        else high = mid - 1;
      }
      let nearestIdx = Math.max(0, Math.min(pts.length - 1, low));
      if (nearestIdx > 0 && Math.abs(pts[nearestIdx - 1].total_yards - targetYd) < Math.abs(pts[nearestIdx].total_yards - targetYd)) {
        nearestIdx = nearestIdx - 1;
      }
      const nearest = pts[nearestIdx];

      // Slope calculation with adjacent sample
      let slopeStr = "";
      if (nearestIdx > 0 || nearestIdx < pts.length - 1) {
        const prev = pts[Math.max(0, nearestIdx - 1)];
        const next = pts[Math.min(pts.length - 1, nearestIdx + 1)];
        const dElev = next.elevation_m - prev.elevation_m;
        const dDistM = (next.total_yards - prev.total_yards) * 0.9144;
        if (dDistM > 0) {
          const slopePct = (dElev / dDistM) * 100;
          const ratio = Math.abs(slopePct) > 0.01 ? Math.round(100 / Math.abs(slopePct)) : null;
          const dir = dElev > 0.1 ? "▲ Rising" : dElev < -0.1 ? "▼ Falling" : "— Level";
          slopeStr = ratio ? `${dir} 1:${ratio} (${slopePct >= 0 ? "+" : ""}${slopePct.toFixed(2)}%)` : `${dir} (${slopePct.toFixed(2)}%)`;
        }
      }

      // Fast binary search for active curvature segment
      const curvs = STATE.profileDrawer.data.curvature || [];
      let activeCurve = null;
      let cLow = 0;
      let cHigh = curvs.length - 1;
      while (cLow <= cHigh) {
        const mid = (cLow + cHigh) >> 1;
        const c = curvs[mid];
        if (c.total_yards_to < nearest.total_yards) {
          cLow = mid + 1;
        } else if (c.total_yards_from > nearest.total_yards) {
          cHigh = mid - 1;
        } else {
          activeCurve = c;
          break;
        }
      }

      STATE.profileDrawer.hoverItem = {
        ...nearest,
        activeCurve,
        slopeStr,
      };

      STATE.profileDrawer.scrubWorldPos = {
        x: nearest.easting,
        y: nearest.northing,
        label: `${nearest.mileage} • ${nearest.elevation_m}m${nearest.place_name ? ` • ${nearest.place_name}` : ""}`,
      };

      const curvCol = activeCurve ? getCurvatureColor(activeCurve.radius_avg_m) : "#38bdf8";
      const curvLabel = activeCurve
        ? `${activeCurve.radius_avg_m ? `Radius ${activeCurve.radius_avg_m}m` : "Straight"} (${activeCurve.radius_avg_class || "Track Curve"})`
        : "Standard Track Alignment";

      tooltip.innerHTML = `
        <div style="font-weight:700; color:#38bdf8; margin-bottom:2px;">
          📍 Mileage: ${escapeHtml(nearest.mileage)} (${formatMileageYards(nearest.total_yards)})
        </div>
        <div style="display:flex; gap:12px; margin-bottom:2px;">
          <span>Elevation: <strong>${nearest.elevation_m.toFixed(1)}m</strong></span>
          ${slopeStr ? `<span>Grade: <strong>${escapeHtml(slopeStr)}</strong></span>` : ""}
        </div>
        <div>
          Curvature: <strong style="color:${curvCol}">${escapeHtml(curvLabel)}</strong>
        </div>
        ${
          nearest.place_name || nearest.postcode
            ? `<div style="color:#94a3b8; font-size:0.68rem; margin-top:2px;">Location: ${escapeHtml([nearest.place_name, nearest.postcode].filter(Boolean).join(", "))}</div>`
            : ""
        }
        <div style="font-size:0.64rem; color:#64748b; margin-top:3px; text-align:right;">
          Click to fly map here
        </div>
      `;

      const toolTipX = Math.max(130, Math.min(geo.W - 130, mouseX));
      tooltip.style.left = `${toolTipX}px`;
      tooltip.style.top = `8px`;
      tooltip.classList.remove("hidden");

      renderProfileCanvas();
      requestRender();
    });

    pCanvas.addEventListener("mouseleave", () => {
      if (STATE.profileDrawer) {
        STATE.profileDrawer.hoverItem = null;
        STATE.profileDrawer.scrubWorldPos = null;
      }
      tooltip.classList.add("hidden");
      renderProfileCanvas();
      requestRender();
    });

    pCanvas.addEventListener("click", () => {
      if (STATE.profileDrawer && STATE.profileDrawer.scrubWorldPos) {
        const { x, y } = STATE.profileDrawer.scrubWorldPos;
        flyTo(x, y, 0.45);
      }
    });
  }

  if (dockBtn) {
    dockBtn.addEventListener("click", () => {
      if (STATE.profileDrawer && STATE.profileDrawer.data) {
        STATE.profileDrawer.visible = true;
        pDrawer.classList.remove("hidden");
        dockBtn.classList.add("hidden");
        renderProfileCanvas();
      }
    });
  }

  if (scopeBtn) {
    scopeBtn.addEventListener("click", () => {
      toggleProfileScope();
    });
  }

  if (collapseBtn) {
    collapseBtn.addEventListener("click", () => {
      closeProfileDrawer(false);
    });
  }

  if (closeBtn) {
    closeBtn.addEventListener("click", () => {
      closeProfileDrawer(false);
    });
  }
}
window.switchNwrCrossTrack = function (fid) {
  if (!STATE.selectedAsset) return;
  const parsedFid = (fid === null || fid === undefined || fid === "" || fid === "auto") ? null : Number(fid);
  loadAssetDetails(STATE.selectedAsset.asset_type, STATE.selectedAsset.id, false, parsedFid);
};

window.saveNwrCrossTrackOverride = async function (fid) {
  if (!STATE.selectedAsset || fid === null || fid === undefined) return;
  try {
    const res = await fetch("/api/nwr/track-override", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        asset_type: STATE.selectedAsset.asset_type,
        asset_id: STATE.selectedAsset.id,
        nwr_fid: Number(fid),
      }),
    });
    if (res.ok) {
      if (typeof invalidateAssetDetailCache === "function") {
        invalidateAssetDetailCache(STATE.selectedAsset.asset_type, STATE.selectedAsset.id);
      }
      await loadAssetDetails(STATE.selectedAsset.asset_type, STATE.selectedAsset.id, false, null);
    }
  } catch (err) {
    console.error("Failed to save track override:", err);
  }
};

window.resetNwrCrossTrackOverride = async function () {
  if (!STATE.selectedAsset) return;
  const mi = STATE.selectedAsset.related?.nwr_cross_links?.match_info || {};
  try {
    if (mi.saved_override || mi.saved_fid) {
      await fetch(
        `/api/nwr/track-override/${encodeURIComponent(STATE.selectedAsset.asset_type)}/${encodeURIComponent(STATE.selectedAsset.id)}`,
        { method: "DELETE" }
      );
    }
    if (typeof invalidateAssetDetailCache === "function") {
      invalidateAssetDetailCache(STATE.selectedAsset.asset_type, STATE.selectedAsset.id);
    }
    await loadAssetDetails(STATE.selectedAsset.asset_type, STATE.selectedAsset.id, false, null);
  } catch (err) {
    console.error("Failed to reset track override:", err);
  }
};
