// Client-side mock data so the UI is fully demoable without the backend.
// Everything here is FAKE but physically plausible; the UI labels it as mock.

import { isWater, rng, M_PER_DEG } from './geo';
import type { CatalogSpill, TracksPayload, BudgetRow, Footprint } from './types';

export const MOCK_CATALOG: CatalogSpill[] = [
  { id: 'deepwater-horizon', name: 'Deepwater Horizon', date_start: '2010-04-20', date_end: '2010-07-15', lat: 28.736, lon: -88.387, volume_bbl: 3190000, oil_type: 'Louisiana light sweet crude', has_observed_outlines: true, source_urls: ['https://incidentnews.noaa.gov/incident/8220'] },
  { id: 'texas-city-y', name: 'Texas City "Y"', date_start: '2014-03-22', date_end: '2014-03-27', lat: 29.36, lon: -94.8, volume_bbl: 4000, oil_type: 'IFO 380', has_observed_outlines: false, source_urls: ['https://incidentnews.noaa.gov/incident/8994'] },
  { id: 'ixtoc-i', name: 'Ixtoc I', date_start: '1979-06-03', date_end: '1979-08-31', lat: 19.408, lon: -92.325, volume_bbl: 3300000, oil_type: 'Crude', has_observed_outlines: false, source_urls: [] },
  { id: 'mega-borg', name: 'Mega Borg', date_start: '1990-06-08', date_end: '1990-06-16', lat: 28.82, lon: -94.33, volume_bbl: 100000, oil_type: 'Palanca crude', has_observed_outlines: false, source_urls: [] },
  { id: 'taylor-mc20', name: 'Taylor Energy MC-20', date_start: '2004-09-16', date_end: '2004-10-15', lat: 28.938, lon: -88.97, volume_bbl: 10000, oil_type: 'Crude', has_observed_outlines: false, source_urls: [] },
  { id: 'tampa-bay-1993', name: 'Tampa Bay collision', date_start: '1993-08-10', date_end: '1993-08-16', lat: 27.62, lon: -82.76, volume_bbl: 7900, oil_type: 'No. 6 fuel oil', has_observed_outlines: false, source_urls: [] },
];

// ---------- analytic current field ----------
type Pt = [number, number];
const LOOP: Pt[] = [
  [-85.6, 20.6], [-85.9, 22.0], [-86.6, 23.8], [-87.4, 25.4], [-87.2, 26.6], [-86.0, 27.1],
  [-84.6, 26.6], [-83.9, 25.4], [-83.6, 24.5], [-82.4, 24.15], [-81.0, 24.2], [-80.2, 25.0], [-79.9, 26.5],
];
const SHELF: Pt[] = [
  [-88.8, 29.0], [-90.5, 28.75], [-92.5, 29.2], [-94.0, 29.3], [-95.2, 28.6], [-96.6, 27.8], [-97.0, 26.8], [-97.0, 25.5], [-97.2, 23.5],
];
const DAY = 86400;

function jet(lon: number, lat: number, line: Pt[], speed: number, width: number, out: number[]) {
  const c = Math.cos((lat * Math.PI) / 180);
  let best = Infinity, tx = 0, ty = 0;
  for (let k = 0; k < line.length - 1; k++) {
    const ax = line[k][0] * c, ay = line[k][1];
    const bx = line[k + 1][0] * c, by = line[k + 1][1];
    const dx = bx - ax, dy = by - ay;
    const L2 = dx * dx + dy * dy;
    let s = ((lon * c - ax) * dx + (lat - ay) * dy) / L2;
    s = s < 0 ? 0 : s > 1 ? 1 : s;
    const px = ax + s * dx - lon * c, py = ay + s * dy - lat;
    const d2 = px * px + py * py;
    if (d2 < best) { best = d2; const L = Math.sqrt(L2); tx = dx / L; ty = dy / L; }
  }
  const w = speed * Math.exp(-best / (width * width));
  out[0] += tx * w; out[1] += ty * w;
}

function vortex(lon: number, lat: number, cx: number, cy: number, r: number, vmax: number, ccw: number, out: number[]) {
  const dx = (lon - cx) * Math.cos((lat * Math.PI) / 180), dy = lat - cy;
  const d = Math.sqrt(dx * dx + dy * dy) + 1e-9;
  const q = d / r;
  if (q > 4) return;
  const vt = vmax * q * Math.exp(0.5 * (1 - q * q));
  out[0] += ((-dy / d) * vt) * ccw; out[1] += ((dx / d) * vt) * ccw;
}

const shiftedLoopCache = new Map<number, Pt[]>();
function loopAt(t: number): Pt[] {
  const key = Math.floor(t / (6 * 3600));
  let l = shiftedLoopCache.get(key);
  if (!l) {
    const north = 0.7 * Math.sin((2 * Math.PI * t) / (210 * DAY));
    l = LOOP.map(([x, y]) => [x - (y > 24 ? 0.4 * north : 0), y + (y > 24 ? north * Math.min(1, (y - 24) / 2) : 0)] as Pt);
    if (shiftedLoopCache.size > 200) shiftedLoopCache.clear();
    shiftedLoopCache.set(key, l);
  }
  return l;
}

/** Mock surface current (m/s) at lon/lat and unix time t; null on land. */
export function mockCurrent(lon: number, lat: number, t: number): [number, number] | null {
  if (!isWater(lon, lat)) return null;
  const out = [0, 0];
  jet(lon, lat, loopAt(t), 1.5, 0.38, out);
  jet(lon, lat, SHELF, 0.28, 0.55, out);
  const days = t / DAY;
  const drift = (days % 300) * 0.025; // westward eddy drift
  vortex(lon, lat, -89.5 - drift * 0.6, 25.6, 1.3, 0.65, -1, out); // detached warm ring (anticyclonic)
  vortex(lon, lat, -93.6, 27.0, 0.8, 0.35, 1, out);
  vortex(lon, lat, -95.4, 23.6, 0.9, 0.35, -1, out);
  vortex(lon, lat, -84.6, 27.7, 0.7, 0.3, 1, out);
  vortex(lon, lat, -91.5, 22.6, 0.8, 0.25, 1, out);
  // mesoscale "noise" so the mock field looks organic
  const tt = t / (3 * DAY);
  out[0] += 0.14 * Math.sin(lat * 1.9 + lon * 0.6 + tt) + 0.07 * Math.sin(lat * 4.3 - lon * 2.1 - 0.7 * tt);
  out[1] += 0.14 * Math.cos(lon * 1.5 - lat * 0.8 + 0.8 * tt) + 0.07 * Math.cos(lon * 3.7 + lat * 2.6 + tt);
  const ph = (2 * Math.PI * t) / (24 * 3600);
  out[0] += 0.05 * Math.cos(ph); out[1] -= 0.05 * Math.sin(ph); // inertial wiggle
  return [out[0], out[1]];
}

function mockWind(t: number): [number, number] {
  const a = (135 + 40 * Math.sin((2 * Math.PI * t) / (2.3 * DAY))) * (Math.PI / 180); // from SE-ish
  const s = 6 + 2 * Math.sin((2 * Math.PI * t) / (1.7 * DAY));
  return [-Math.sin(a) * s, -Math.cos(a) * s]; // meteorological 'from' direction -> vector
}

// ---------- mock particle run ----------
export interface MockRunOpts {
  lat: number; lon: number; startUnix: number; durationH: number;
  nSteps?: number; n?: number; releaseH?: number; seed?: number;
}

export function mockTracks(o: MockRunOpts): TracksPayload {
  const nSteps = o.nSteps ?? 145;
  const n = o.n ?? 2000;
  const stepH = o.durationH / (nSteps - 1);
  const stepS = stepH * 3600;
  const releaseH = o.releaseH ?? 0;
  const R = rng(o.seed ?? Math.round(o.lat * 1000 + o.lon * 7919 + o.startUnix));
  const times = Array.from({ length: nSteps }, (_, i) => Math.round(o.startUnix + i * stepS));
  const scale = Math.max(1, o.durationH / 72);
  const K = 20; // horizontal diffusivity m^2/s
  const diff = Math.sqrt(2 * K * stepS) / M_PER_DEG;
  const particles: TracksPayload['particles'] = [];
  const endStatus: string[] = [];
  const endIdx: number[] = [];
  for (let p = 0; p < n; p++) {
    const rel = Math.min(nSteps - 2, Math.floor((R.next() * releaseH) / stepH));
    const evapT = R.next() < 0.3 ? rel * stepH + 18 * scale * -Math.log(Math.max(1e-6, R.next())) : Infinity;
    const dispT = R.next() < 0.12 ? rel * stepH + 30 * scale * -Math.log(Math.max(1e-6, R.next())) : Infinity;
    const windage = 0.025 + 0.01 * R.next();
    let lon = o.lon + R.gauss() * 0.01, lat = o.lat + R.gauss() * 0.01;
    if (!isWater(lon, lat)) { lon = o.lon; lat = o.lat; }
    const path: [number, number][] = [[+lon.toFixed(5), +lat.toFixed(5)]];
    const ts = [times[rel]];
    let status = 'active';
    for (let i = rel + 1; i < nSteps; i++) {
      const tH = i * stepH;
      if (tH > evapT) { status = 'evaporated'; break; }
      if (tH > dispT) { status = 'dispersed'; break; }
      const t = times[i - 1];
      const c = mockCurrent(lon, lat, t) ?? [0, 0];
      const w = mockWind(t);
      const u = c[0] + windage * w[0], v = c[1] + windage * w[1];
      const coslat = Math.cos((lat * Math.PI) / 180);
      const nlon = lon + (u * stepS) / (M_PER_DEG * coslat) + (R.gauss() * diff) / coslat;
      const nlat = lat + (v * stepS) / M_PER_DEG + R.gauss() * diff;
      if (!isWater(nlon, nlat)) {
        path.push([+nlon.toFixed(5), +nlat.toFixed(5)]); ts.push(times[i]);
        status = 'stranded';
        break;
      }
      lon = nlon; lat = nlat;
      path.push([+lon.toFixed(5), +lat.toFixed(5)]); ts.push(times[i]);
    }
    particles.push({ path, timestamps: ts, status_end: status });
    endStatus.push(status);
    endIdx.push(rel + ts.length - 1);
  }
  // Oil budget from particle counts (fractions of released oil).
  const budget: BudgetRow[] = times.map((t, i) => {
    let surface = 0, evaporated = 0, dispersed = 0, stranded = 0, released = 0;
    for (let p = 0; p < n; p++) {
      const start = endIdx[p] - (particles[p].timestamps.length - 1);
      if (i < start) continue;
      released++;
      if (i < endIdx[p] || endStatus[p] === 'active') { surface++; continue; }
      const s = endStatus[p];
      if (s === 'evaporated') evaporated++; else if (s === 'dispersed') dispersed++; else if (s === 'stranded') stranded++; else surface++;
    }
    const d = Math.max(1, released);
    return { t, surface: surface / d, evaporated: evaporated / d, dispersed: dispersed / d, stranded: stranded / d };
  });
  return { times, particles, budget };
}

/** Star-shaped hull outlines of the mock slick at ~nOut times (mimics observed outlines). */
export function mockFootprints(tr: TracksPayload, nOut = 24): Footprint[] {
  const out: Footprint[] = [];
  const nT = tr.times.length;
  for (let k = 1; k <= nOut; k++) {
    const i = Math.round((k / nOut) * (nT - 1));
    const t = tr.times[i];
    const pts: [number, number][] = [];
    for (const p of tr.particles) {
      const j = p.timestamps.indexOf(t);
      if (j >= 0 && (j < p.timestamps.length - 1 || p.status_end === 'active')) pts.push(p.path[j]);
    }
    if (pts.length < 10) continue;
    // cluster into a few blobs by coarse grid so disconnected patches stay separate
    const cells = new Map<string, [number, number][]>();
    for (const q of pts) {
      const key = `${Math.floor(q[0] / 0.6)}:${Math.floor(q[1] / 0.6)}`;
      (cells.get(key) ?? cells.set(key, []).get(key)!).push(q);
    }
    const polys: [number, number][][][] = [];
    for (const c of cells.values()) {
      if (c.length < 6) continue;
      const cx = c.reduce((s, q) => s + q[0], 0) / c.length, cy = c.reduce((s, q) => s + q[1], 0) / c.length;
      const B = 28, r = new Array(B).fill(0.02);
      for (const q of c) {
        const a = Math.atan2(q[1] - cy, q[0] - cx);
        const b = Math.floor(((a + Math.PI) / (2 * Math.PI)) * B) % B;
        r[b] = Math.max(r[b], Math.hypot(q[0] - cx, q[1] - cy) * 1.08);
      }
      const sm = r.map((_, b) => (r[(b + B - 1) % B] + 2 * r[b] + r[(b + 1) % B]) / 4);
      const ring: [number, number][] = sm.map((rr, b) => {
        const a = ((b + 0.5) / B) * 2 * Math.PI - Math.PI;
        return [cx + rr * Math.cos(a), cy + rr * Math.sin(a)];
      });
      ring.push(ring[0]);
      polys.push([ring]);
    }
    out.push({ t, geojson: { type: 'Feature', properties: {}, geometry: { type: 'MultiPolygon', coordinates: polys } } });
  }
  return out;
}

/** Mock run for a catalog spill: continuous release for long blowouts, instantaneous otherwise. */
export function mockHistoricalTracks(s: CatalogSpill): TracksPayload {
  const start = Date.parse(s.date_start + (s.date_start.length <= 10 ? 'T12:00:00Z' : '')) / 1000;
  const end = s.date_end ? Date.parse(s.date_end + (s.date_end.length <= 10 ? 'T12:00:00Z' : '')) / 1000 : start + 72 * 3600;
  const spanH = Math.min(Math.max(72, (end - start) / 3600), 40 * 24);
  const long = spanH > 10 * 24;
  return mockTracks({ lat: s.lat, lon: s.lon, startUnix: start, durationH: spanH, n: 2000, releaseH: long ? spanH * 0.85 : 0, nSteps: 145 });
}
