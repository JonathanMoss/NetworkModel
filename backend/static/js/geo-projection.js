/**
 * @fileoverview OSGB36 <-> WGS84 coordinate transformations and
 * OpenStreetMap slippy-map tile rendering.
 */

/* ============================================================================
   EPSG:27700 (OSGB36) <-> EPSG:4326 (WGS84) TRANSFORMATION FOR OSM BASEMAP
   ============================================================================ */
function osgb36_to_wgs84(E, N) {
  const a = 6377563.396, b = 6356256.909, F0 = 0.9996012717;
  const lat0 = (49.0 * Math.PI) / 180.0, lon0 = (-2.0 * Math.PI) / 180.0;
  const N0 = -100000.0, E0 = 400000.0;
  const e2 = 1.0 - (b * b) / (a * a);
  const n = (a - b) / (a + b);
  const n2 = n * n, n3 = n * n * n;
  let lat = lat0, M = 0;
  for (let iter = 0; iter < 100; iter++) {
    lat = (N - N0 - M) / (a * F0) + lat;
    const Ma = (1 + n + (5 / 4) * n2 + (5 / 4) * n3) * (lat - lat0);
    const Mb = (3 * n + 3 * n2 + (21 / 8) * n3) * Math.sin(lat - lat0) * Math.cos(lat + lat0);
    const Mc = ((15 / 8) * n2 + (15 / 8) * n3) * Math.sin(2 * (lat - lat0)) * Math.cos(2 * (lat + lat0));
    const Md = (35 / 24) * n3 * Math.sin(3 * (lat - lat0)) * Math.cos(3 * (lat + lat0));
    M = b * F0 * (Ma - Mb + Mc - Md);
    if (Math.abs(N - N0 - M) < 0.00001) break;
  }
  const cosLat = Math.cos(lat), sinLat = Math.sin(lat);
  const nu = (a * F0) / Math.sqrt(1 - e2 * sinLat * sinLat);
  const rho = (a * F0 * (1 - e2)) / Math.pow(1 - e2 * sinLat * sinLat, 1.5);
  const eta2 = nu / rho - 1;
  const tanLat = Math.tan(lat), tan2 = tanLat * tanLat, tan4 = tan2 * tan2, tan6 = tan4 * tan2;
  const secLat = 1.0 / cosLat;
  const nu3 = nu * nu * nu, nu5 = nu3 * nu * nu, nu7 = nu5 * nu * nu;
  const dE = E - E0, dE2 = dE * dE, dE3 = dE2 * dE, dE4 = dE3 * dE, dE5 = dE4 * dE, dE6 = dE5 * dE, dE7 = dE6 * dE;
  const VII = tanLat / (2 * rho * nu);
  const VIII = (tanLat / (24 * rho * nu3)) * (5 + 3 * tan2 + eta2 - 9 * tan2 * eta2);
  const IX = (tanLat / (720 * rho * nu5)) * (61 + 90 * tan2 + 45 * tan4);
  const lat_osgb = lat - VII * dE2 + VIII * dE4 - IX * dE6;
  const X = secLat / nu;
  const XI = (secLat / (6 * nu3)) * (nu / rho + 2 * tan2);
  const XII = (secLat / (120 * nu5)) * (5 + 28 * tan2 + 24 * tan4);
  const XIIA = (secLat / (5040 * nu7)) * (61 + 662 * tan2 + 1320 * tan4 + 720 * tan6);
  const lon_osgb = lon0 + X * dE - XI * dE3 + XII * dE5 - XIIA * dE7;

  // Helmert OSGB36 -> WGS84
  const cosPhi = Math.cos(lat_osgb), sinPhi = Math.sin(lat_osgb);
  const cosLam = Math.cos(lon_osgb), sinLam = Math.sin(lon_osgb);
  const v = a / Math.sqrt(1 - e2 * sinPhi * sinPhi);
  const x1 = v * cosPhi * cosLam, y1 = v * cosPhi * sinLam, z1 = (1 - e2) * v * sinPhi;
  const tx = 446.448, ty = -125.157, tz = 542.060, s = -20.4894e-6;
  const rx = (0.1502 / 3600.0) * Math.PI / 180.0;
  const ry = (0.2470 / 3600.0) * Math.PI / 180.0;
  const rz = (0.8421 / 3600.0) * Math.PI / 180.0;
  const x2 = tx + (1 + s) * x1 - rz * y1 + ry * z1;
  const y2 = ty + rz * x1 + (1 + s) * y1 - rx * z1;
  const z2 = tz - ry * x1 + rx * y1 + (1 + s) * z1;

  const a_w = 6378137.0, b_w = 6356752.314245;
  const e2_w = 1.0 - (b_w * b_w) / (a_w * a_w);
  const p = Math.sqrt(x2 * x2 + y2 * y2);
  let phi = Math.atan2(z2, p * (1 - e2_w));
  for (let i = 0; i < 5; i++) {
    const sinP = Math.sin(phi);
    const v_w = a_w / Math.sqrt(1 - e2_w * sinP * sinP);
    phi = Math.atan2(z2 + e2_w * v_w * sinP, p);
  }
  const lam = Math.atan2(y2, x2);
  return [(phi * 180.0) / Math.PI, (lam * 180.0) / Math.PI];
}

function wgs84_to_osgb36(lat_w, lon_w) {
  const a_w = 6378137.0, b_w = 6356752.314245;
  const e2_w = 1.0 - (b_w * b_w) / (a_w * a_w);
  const phi_w = (lat_w * Math.PI) / 180.0, lam_w = (lon_w * Math.PI) / 180.0;
  const sinP = Math.sin(phi_w), cosP = Math.cos(phi_w);
  const sinL = Math.sin(lam_w), cosL = Math.cos(lam_w);
  const v_w = a_w / Math.sqrt(1 - e2_w * sinP * sinP);
  const x1 = v_w * cosP * cosL, y1 = v_w * cosP * sinL, z1 = (1 - e2_w) * v_w * sinP;

  const tx = -446.448, ty = 125.157, tz = -542.060, s = 20.4894e-6;
  const rx = (-0.1502 / 3600.0) * Math.PI / 180.0;
  const ry = (-0.2470 / 3600.0) * Math.PI / 180.0;
  const rz = (-0.8421 / 3600.0) * Math.PI / 180.0;
  const x2 = tx + (1 + s) * x1 - rz * y1 + ry * z1;
  const y2 = ty + rz * x1 + (1 + s) * y1 - rx * z1;
  const z2 = tz - ry * x1 + rx * y1 + (1 + s) * z1;

  const a = 6377563.396, b = 6356256.909, e2 = 1.0 - (b * b) / (a * a);
  const p = Math.sqrt(x2 * x2 + y2 * y2);
  let phi = Math.atan2(z2, p * (1 - e2));
  for (let i = 0; i < 5; i++) {
    const sinPhi = Math.sin(phi);
    const v = a / Math.sqrt(1 - e2 * sinPhi * sinPhi);
    phi = Math.atan2(z2 + e2 * v * sinPhi, p);
  }
  const lam = Math.atan2(y2, x2);

  const F0 = 0.9996012717, lat0 = (49.0 * Math.PI) / 180.0, lon0 = (-2.0 * Math.PI) / 180.0;
  const N0 = -100000.0, E0 = 400000.0;
  const n = (a - b) / (a + b), n2 = n * n, n3 = n * n * n;
  const cosLat = Math.cos(phi), sinLat = Math.sin(phi);
  const nu = (a * F0) / Math.sqrt(1 - e2 * sinLat * sinLat);
  const rho = (a * F0 * (1 - e2)) / Math.pow(1 - e2 * sinLat * sinLat, 1.5);
  const eta2 = nu / rho - 1;

  const Ma = (1 + n + (5 / 4) * n2 + (5 / 4) * n3) * (phi - lat0);
  const Mb = (3 * n + 3 * n2 + (21 / 8) * n3) * Math.sin(phi - lat0) * Math.cos(phi + lat0);
  const Mc = ((15 / 8) * n2 + (15 / 8) * n3) * Math.sin(2 * (phi - lat0)) * Math.cos(2 * (phi + lat0));
  const Md = (35 / 24) * n3 * Math.sin(3 * (phi - lat0)) * Math.cos(3 * (phi + lat0));
  const M = b * F0 * (Ma - Mb + Mc - Md);

  const dLam = lam - lon0, dLam2 = dLam * dLam, dLam3 = dLam2 * dLam, dLam4 = dLam3 * dLam, dLam5 = dLam4 * dLam, dLam6 = dLam5 * dLam;
  const tanLat = Math.tan(phi), tan2 = tanLat * tanLat, tan4 = tan2 * tan2;
  const I = M + N0;
  const II = (nu / 2) * sinLat * cosLat;
  const III = (nu / 24) * sinLat * Math.pow(cosLat, 3) * (5 - tan2 + 9 * eta2);
  const IIIA = (nu / 720) * sinLat * Math.pow(cosLat, 5) * (61 - 58 * tan2 + tan4);
  const N = I + II * dLam2 + III * dLam4 + IIIA * dLam6;

  const IV = nu * cosLat;
  const V = (nu / 6) * Math.pow(cosLat, 3) * (nu / rho - tan2);
  const VI = (nu / 120) * Math.pow(cosLat, 5) * (5 - 18 * tan2 + tan4 + 14 * eta2 - 58 * tan2 * eta2);
  const E = E0 + IV * dLam + V * dLam3 + VI * dLam5;

  return [E, N];
}

function lonToTileX(lon, z) {
  return Math.floor(((lon + 180.0) / 360.0) * (1 << z));
}

function latToTileY(lat, z) {
  const latRad = (lat * Math.PI) / 180.0;
  return Math.floor(
    ((1.0 - Math.log(Math.tan(latRad) + 1.0 / Math.cos(latRad)) / Math.PI) / 2.0) * (1 << z)
  );
}

function tileXToLon(x, z) {
  return (x / (1 << z)) * 360.0 - 180.0;
}

function tileYToLat(y, z) {
  const n = Math.PI - (2.0 * Math.PI * y) / (1 << z);
  return (180.0 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
}

function getOsmTileUrl(z, x, y) {
  const sub = ["a", "b", "c"][(x + y) % 3];
  return `https://${sub}.tile.openstreetmap.org/${z}/${x}/${y}.png`;
}

const OSM_TILE_CACHE_MAX = 256;
const osmTileCache = new Map();
let osmOffscreenCanvas = null;
let osmOffscreenCtx = null;

/**
 * Retrieve an OSM tile entry and refresh its LRU recency.
 *
 * @param {string} tileKey Cache key (`"z_x_y"`).
 * @returns {object|null} Cached tile entry or null.
 */
function getOsmTileEntry(tileKey) {
  if (!osmTileCache.has(tileKey)) return null;
  const entry = osmTileCache.get(tileKey);
  osmTileCache.delete(tileKey);
  osmTileCache.set(tileKey, entry);
  return entry;
}

/**
 * Insert an OSM tile entry into the bounded LRU cache, evicting the
 * least-recently-used tile and releasing its Image handlers when full.
 *
 * @param {string} tileKey Cache key (`"z_x_y"`).
 * @param {object} entry Tile descriptor `{ img, loaded, error }`.
 */
function setOsmTileEntry(tileKey, entry) {
  if (osmTileCache.has(tileKey)) {
    osmTileCache.delete(tileKey);
  } else if (osmTileCache.size >= OSM_TILE_CACHE_MAX) {
    const oldestKey = osmTileCache.keys().next().value;
    const evicted = osmTileCache.get(oldestKey);
    if (evicted && evicted.img) {
      evicted.img.onload = null;
      evicted.img.onerror = null;
    }
    osmTileCache.delete(oldestKey);
  }
  osmTileCache.set(tileKey, entry);
}

function getOsmOffscreen(w, h) {
  if (!osmOffscreenCanvas) {
    osmOffscreenCanvas = document.createElement("canvas");
    osmOffscreenCtx = osmOffscreenCanvas.getContext("2d");
  }
  if (osmOffscreenCanvas.width !== w || osmOffscreenCanvas.height !== h) {
    osmOffscreenCanvas.width = w;
    osmOffscreenCanvas.height = h;
  } else {
    osmOffscreenCtx.clearRect(0, 0, w, h);
  }
  return { canvas: osmOffscreenCanvas, ctx: osmOffscreenCtx };
}

function renderOsmTiles(ctx, w, h) {
  if (!STATE.nwrLayers.osm) return;
  if (!STATE.nwrCam) return;

  const opacity = Math.max(0, Math.min(1, (STATE.osmOpacity !== undefined ? STATE.osmOpacity : 30) / 100));
  if (opacity <= 0) return;

  const zoom = STATE.nwrCam.zoom;
  const worldToScreen = (wx, wy) => [
    (wx - STATE.nwrCam.camX) * zoom + w / 2,
    (STATE.nwrCam.camY - wy) * zoom + h / 2,
  ];
  const screenToWorld = (sx, sy) => [
    STATE.nwrCam.camX + (sx - w / 2) / zoom,
    STATE.nwrCam.camY - (sy - h / 2) / zoom,
  ];

  const [minE, maxN] = screenToWorld(-64, -64);
  const [maxE, minN] = screenToWorld(w + 64, h + 64);

  const [latTL, lonTL] = osgb36_to_wgs84(minE, maxN);
  const [latBR, lonBR] = osgb36_to_wgs84(maxE, minN);
  const [latTR, lonTR] = osgb36_to_wgs84(maxE, maxN);
  const [latBL, lonBL] = osgb36_to_wgs84(minE, minN);

  const minLat = Math.min(latTL, latBR, latTR, latBL);
  const maxLat = Math.max(latTL, latBR, latTR, latBL);
  const minLon = Math.min(lonTL, lonBR, lonTR, lonBL);
  const maxLon = Math.max(lonTL, lonBR, lonTR, lonBL);

  if (maxLat < 48 || minLat > 62 || maxLon < -13 || minLon > 4) return;

  let z = Math.round(Math.log2(92015 * zoom));
  z = Math.max(5, Math.min(18, z));

  let x0 = lonToTileX(minLon, z);
  let x1 = lonToTileX(maxLon, z);
  let y0 = latToTileY(maxLat, z);
  let y1 = latToTileY(minLat, z);

  let startX = Math.max(0, Math.min(x0, x1));
  let endX = Math.min((1 << z) - 1, Math.max(x0, x1));
  let startY = Math.max(0, Math.min(y0, y1));
  let endY = Math.min((1 << z) - 1, Math.max(y0, y1));

  if ((endX - startX + 1) * (endY - startY + 1) > 64 && z > 5) {
    z -= 1;
    x0 = lonToTileX(minLon, z);
    x1 = lonToTileX(maxLon, z);
    y0 = latToTileY(maxLat, z);
    y1 = latToTileY(minLat, z);
    startX = Math.max(0, Math.min(x0, x1));
    endX = Math.min((1 << z) - 1, Math.max(x0, x1));
    startY = Math.max(0, Math.min(y0, y1));
    endY = Math.min((1 << z) - 1, Math.max(y0, y1));
  }

  // Pre-calculate shared grid vertex screen coordinates to prevent any seams/gaps between tiles
  const gridXCount = endX - startX + 2;
  const gridYCount = endY - startY + 2;
  const gridVertices = new Array(gridXCount);
  for (let i = 0; i < gridXCount; i++) {
    gridVertices[i] = new Array(gridYCount);
    const tx = startX + i;
    const lon = tileXToLon(tx, z);
    for (let j = 0; j < gridYCount; j++) {
      const ty = startY + j;
      const lat = tileYToLat(ty, z);
      const [e, n] = wgs84_to_osgb36(lat, lon);
      gridVertices[i][j] = worldToScreen(e, n);
    }
  }

  const { canvas: offCanvas, ctx: offCtx } = getOsmOffscreen(w, h);
  let anyDrawn = false;

  for (let ty = startY; ty <= endY; ty++) {
    const j = ty - startY;
    for (let tx = startX; tx <= endX; tx++) {
      const i = tx - startX;
      const pNW = gridVertices[i][j];
      const pNE = gridVertices[i + 1][j];
      const pSW = gridVertices[i][j + 1];

      const tileKey = `${z}_${tx}_${ty}`;
      let entry = getOsmTileEntry(tileKey);
      if (!entry) {
        const img = new Image();
        img.crossOrigin = "anonymous";
        entry = { img, loaded: false, error: false };
        setOsmTileEntry(tileKey, entry);
        img.onload = () => {
          entry.loaded = true;
          requestRender();
        };
        img.onerror = () => {
          entry.error = true;
        };
        img.src = getOsmTileUrl(z, tx, ty);
      }

      if (entry.loaded && !entry.error) {
        anyDrawn = true;
        // Affine transform matrix mapping [0, 256]x[0, 256] to OSGB36 projected quadrilateral
        const m00 = (pNE[0] - pNW[0]) / 256.0;
        const m01 = (pNE[1] - pNW[1]) / 256.0;
        const m10 = (pSW[0] - pNW[0]) / 256.0;
        const m11 = (pSW[1] - pNW[1]) / 256.0;
        const tx0 = pNW[0];
        const ty0 = pNW[1];

        offCtx.save();
        offCtx.setTransform(m00, m01, m10, m11, tx0, ty0);
        // Draw with 0.75px bleed to eliminate subpixel raster seams at 100% opacity
        offCtx.drawImage(entry.img, -0.75, -0.75, 257.5, 257.5);
        offCtx.restore();
      }
    }
  }

  if (anyDrawn) {
    ctx.save();
    ctx.globalAlpha = opacity;
    ctx.drawImage(offCanvas, 0, 0);
    ctx.restore();
  }
}

