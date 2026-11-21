// Backend client with automatic mock fallback.
// ?mock=1 forces mock everywhere; otherwise any call whose backend is unreachable falls back to mock
// and is reported via onMock so the UI can label it.

import { MOCK_CATALOG, mockTracks, mockHistoricalTracks, mockFootprints, mockCurrent } from './mock';
import { MOCK_BBOX } from './geo';
import type { CatalogSpill, TracksPayload, Tracks, Footprint, CurrentGrid, BudgetRow } from './types';

const qs = new URLSearchParams(location.search);
export const FORCE_MOCK = qs.has('mock') && qs.get('mock') !== '0';

const mockParts = new Set<string>();
let mockListener: (parts: string[]) => void = () => {};
export function onMock(fn: (parts: string[]) => void) { mockListener = fn; fn([...mockParts]); }
function markMock(part: string) { mockParts.add(part); mockListener([...mockParts]); }
function markReal(part: string) { if (mockParts.delete(part)) mockListener([...mockParts]); }

class BackendDown extends Error {}

async function getJSON<T = any>(url: string, init?: RequestInit): Promise<T> {
  if (FORCE_MOCK) throw new BackendDown('mock forced');
  let r: Response;
  try {
    r = await fetch(url, init);
  } catch (e) {
    throw new BackendDown(String(e));
  }
  const ct = r.headers.get('content-type') || '';
  // Vite proxy returns 5xx/HTML when the backend is down; treat 404 as "endpoint not implemented yet".
  if (r.status === 404 || r.status === 502 || r.status === 503 || r.status === 504 || !ct.includes('json')) {
    throw new BackendDown(`${r.status} ${url}`);
  }
  const body = await r.json();
  if (!r.ok) throw new Error(body?.detail ? String(body.detail) : `${r.status} ${url}`);
  return body as T;
}

// ---------- time helpers ----------
export function toUnix(t: unknown): number {
  if (typeof t === 'number') return t > 1e11 ? t / 1000 : t;
  if (typeof t === 'string') {
    if (/^\d+(\.\d+)?$/.test(t)) return toUnix(Number(t));
    const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(t) || t.length <= 10 ? t : t + 'Z';
    return Date.parse(iso) / 1000;
  }
  return NaN;
}

// ---------- tracks ----------
export function normalizeTracks(p: TracksPayload): Tracks {
  const times = (p.times ?? []).map(toUnix);
  let t0 = times.length ? times[0] : Infinity;
  let tEnd = times.length ? times[times.length - 1] : -Infinity;
  if (!times.length) {
    for (const q of p.particles) for (const t of q.timestamps) { const u = toUnix(t); if (u < t0) t0 = u; if (u > tEnd) tEnd = u; }
  }
  const particles = p.particles
    .filter((q) => q.path && q.path.length > 0)
    .map((q) => ({
      path: q.path,
      ts: q.timestamps.map((t) => (toUnix(t) - t0) / 3600),
      status: q.status_end ?? 'active',
    }));
  const budget: BudgetRow[] = (p.budget ?? []).map((b) => ({ ...b, t: (toUnix(b.t) - t0) / 3600 }));
  return { t0, durationH: Math.max(1, (tEnd - t0) / 3600), particles, budget };
}

// ---------- historical ----------
export async function getCatalog(): Promise<CatalogSpill[]> {
  try {
    const r = await getJSON<any>('/api/spills');
    const list: CatalogSpill[] = Array.isArray(r) ? r : r.spills ?? r.items ?? [];
    if (!list.length) throw new BackendDown('empty catalog');
    markReal('catalog');
    return list;
  } catch (e) {
    if (!(e instanceof BackendDown)) console.warn(e);
    markMock('catalog');
    return MOCK_CATALOG;
  }
}

const mockHistCache = new Map<string, TracksPayload>();
function mockHist(s: CatalogSpill) {
  let t = mockHistCache.get(s.id);
  if (!t) { t = mockHistoricalTracks(s); mockHistCache.set(s.id, t); }
  return t;
}

export async function getHistoricalTracks(s: CatalogSpill): Promise<Tracks | null> {
  try {
    const r = await getJSON<TracksPayload>(`/api/spills/${encodeURIComponent(s.id)}/tracks`);
    markReal('hindcast tracks');
    return normalizeTracks(r);
  } catch (e) {
    if (!(e instanceof BackendDown)) { console.warn(e); return null; }
    markMock('hindcast tracks');
    return normalizeTracks(mockHist(s));
  }
}

/** Accepts FeatureCollection with properties.time|t|date, an array of {t|time, geojson|geometry|feature}, or {times, features}. */
export function normalizeFootprints(r: any): Footprint[] {
  const out: Footprint[] = [];
  const timeOf = (o: any) => toUnix(o?.t ?? o?.time ?? o?.date ?? o?.timestamp ?? o?.properties?.time ?? o?.properties?.t ?? o?.properties?.date);
  if (r?.type === 'FeatureCollection') {
    for (const f of r.features) out.push({ t: timeOf(f), geojson: f });
  } else if (Array.isArray(r)) {
    for (const o of r) out.push({ t: timeOf(o), geojson: o.geojson ?? o.feature ?? o.geometry ?? o });
  } else if (r?.times && r?.features) {
    r.times.forEach((t: any, i: number) => out.push({ t: toUnix(t), geojson: r.features[i] }));
  } else if (r?.footprints) {
    return normalizeFootprints(r.footprints);
  }
  return out.filter((f) => Number.isFinite(f.t) && f.geojson).sort((a, b) => a.t - b.t);
}

export async function getFootprints(s: CatalogSpill): Promise<Footprint[]> {
  if (!s.has_observed_outlines) return [];
  try {
    const r = await getJSON<any>(`/api/spills/${encodeURIComponent(s.id)}/footprints`);
    markReal('observed outlines');
    return normalizeFootprints(r);
  } catch (e) {
    if (!(e instanceof BackendDown)) { console.warn(e); return []; }
    markMock('observed outlines');
    return mockFootprints(mockHist(s));
  }
}

// ---------- prediction ----------
export interface SpillJob {
  id: string;
  mock: boolean;
  lat: number; lon: number;
}

export async function postSpill(lat: number, lon: number): Promise<SpillJob> {
  try {
    const r = await getJSON<any>('/api/spill', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ lat, lon, duration_h: 72, volume_m3: 500, oil_type: 'IFO 380', n_particles: 2000 }),
    });
    markReal('prediction run');
    return { id: String(r.id), mock: false, lat, lon };
  } catch (e) {
    if (!(e instanceof BackendDown)) throw e;
    markMock('prediction run');
    return { id: `mock-${Date.now()}`, mock: true, lat, lon };
  }
}

const mockJobStart = new Map<string, number>();
export async function pollSpill(job: SpillJob): Promise<{ status: string; progress: number; error?: string }> {
  if (job.mock) {
    const s = mockJobStart.get(job.id) ?? (mockJobStart.set(job.id, performance.now()), performance.now());
    const p = Math.min(1, (performance.now() - s) / 1600);
    return { status: p >= 1 ? 'done' : 'running', progress: p };
  }
  const r = await getJSON<any>(`/api/spill/${encodeURIComponent(job.id)}`);
  let progress = Number(r.progress ?? 0);
  if (progress > 1) progress /= 100;
  return { status: r.status, progress, error: r.error };
}

export async function getSpillTracks(job: SpillJob): Promise<Tracks> {
  if (job.mock) {
    const now = Math.floor(Date.now() / 3600000) * 3600;
    return normalizeTracks(mockTracks({ lat: job.lat, lon: job.lon, startUnix: now, durationH: 72 }));
  }
  return normalizeTracks(await getJSON<TracksPayload>(`/api/spill/${encodeURIComponent(job.id)}/tracks`));
}

// ---------- currents ----------
export interface BBox { minLon: number; minLat: number; maxLon: number; maxLat: number }

/** Parses a u/v grid. Preferred: {time, lon0, lat0, dlon, dlat, nx, ny, u:[ny*nx], v:[ny*nx]} (row-major, lat ascending, null = land).
 *  Also accepts {lon:[...], lat:[...], u:[[...]] | [...], v}. */
export function parseGrid(r: any, tFallback: number): CurrentGrid {
  const flat = (a: any): number[] => (Array.isArray(a?.[0]) ? a.flat() : a);
  let { lon0, lat0, dlon, dlat, nx, ny } = r;
  if (r.lon && r.lat) {
    nx = r.lon.length; ny = r.lat.length;
    lon0 = r.lon[0]; lat0 = r.lat[0];
    dlon = (r.lon[nx - 1] - lon0) / (nx - 1); dlat = (r.lat[ny - 1] - lat0) / (ny - 1);
  }
  let u = flat(r.u), v = flat(r.v);
  const U = new Float32Array(nx * ny), V = new Float32Array(nx * ny);
  // flip to lat-ascending if needed
  const flip = dlat < 0;
  for (let j = 0; j < ny; j++) {
    const src = flip ? ny - 1 - j : j;
    for (let i = 0; i < nx; i++) {
      const a = u[src * nx + i], b = v[src * nx + i];
      U[j * nx + i] = a == null ? NaN : a; V[j * nx + i] = b == null ? NaN : b;
    }
  }
  if (flip) { lat0 = lat0 + dlat * (ny - 1); dlat = -dlat; }
  return { t: toUnix(r.time ?? r.t ?? tFallback), lon0, lat0, dlon, dlat, nx, ny, u: U, v: V };
}

export interface CurrentsSource {
  mock: boolean;
  bbox: BBox;
  /** current (m/s) at unix time t; null for land / outside */
  sample(lon: number, lat: number, t: number): [number, number] | null;
  /** prefetch grids around t (no-op for mock) */
  prefetch(t: number): void;
}

const MOCK_SOURCE: CurrentsSource = { mock: true, bbox: MOCK_BBOX, sample: mockCurrent, prefetch() {} };

function sampleGrid(g: CurrentGrid, lon: number, lat: number): [number, number] | null {
  const x = (lon - g.lon0) / g.dlon, y = (lat - g.lat0) / g.dlat;
  const i = Math.floor(x), j = Math.floor(y);
  if (i < 0 || j < 0 || i >= g.nx - 1 || j >= g.ny - 1) return null;
  const fx = x - i, fy = y - j;
  const k = j * g.nx + i;
  const u00 = g.u[k], u10 = g.u[k + 1], u01 = g.u[k + g.nx], u11 = g.u[k + g.nx + 1];
  const v00 = g.v[k], v10 = g.v[k + 1], v01 = g.v[k + g.nx], v11 = g.v[k + g.nx + 1];
  if (u00 !== u00 || u10 !== u10 || u01 !== u01 || u11 !== u11) return null; // NaN = land
  return [
    (u00 * (1 - fx) + u10 * fx) * (1 - fy) + (u01 * (1 - fx) + u11 * fx) * fy,
    (v00 * (1 - fx) + v10 * fx) * (1 - fy) + (v01 * (1 - fx) + v11 * fx) * fy,
  ];
}

/** Loads the list of timesteps for a window and lazily fetches grids; falls back to the analytic mock field. */
export async function getCurrents(startUnix: number, endUnix: number, bbox: BBox = MOCK_BBOX): Promise<CurrentsSource> {
  try {
    const iso = (t: number) => new Date(t * 1000).toISOString().slice(0, 19) + 'Z';
    const b = `${bbox.minLon},${bbox.minLat},${bbox.maxLon},${bbox.maxLat}`;
    const r = await getJSON<any>(`/api/currents?start=${iso(startUnix)}&end=${iso(endUnix)}&bbox=${b}`);
    const raw: any[] = Array.isArray(r) ? r : r.times ?? r.timesteps ?? [];
    const steps = raw.map((x) => {
      const key = typeof x === 'object' ? x.t ?? x.time ?? x.id : x;
      return { key: String(key), t: toUnix(key) };
    }).filter((s) => Number.isFinite(s.t)).sort((a, b) => a.t - b.t);
    if (!steps.length) throw new BackendDown('no current timesteps');
    markReal('currents');
    const grids = new Map<string, CurrentGrid | 'loading' | 'error'>();
    const load = (k: number) => {
      const s = steps[k];
      if (!s || grids.has(s.key)) return;
      grids.set(s.key, 'loading');
      getJSON<any>(`/api/currents/${encodeURIComponent(s.key)}`)
        .then((g) => grids.set(s.key, parseGrid(g, s.t)))
        .catch((e) => { console.warn('current grid', s.key, e); grids.set(s.key, 'error'); });
    };
    const idxAt = (t: number) => {
      let lo = 0, hi = steps.length - 1;
      if (t <= steps[0].t) return 0;
      if (t >= steps[hi].t) return hi;
      while (hi - lo > 1) { const m = (lo + hi) >> 1; if (steps[m].t <= t) lo = m; else hi = m; }
      return lo;
    };
    let lastGood: CurrentGrid | null = null;
    const src: CurrentsSource = {
      mock: false,
      bbox,
      prefetch(t) { const k = idxAt(t); load(k); load(k + 1); load(k + 2); },
      sample(lon, lat, t) {
        const k = idxAt(t);
        const a = grids.get(steps[k].key), b = steps[k + 1] && grids.get(steps[k + 1].key);
        const ga = typeof a === 'object' ? a : null, gb = typeof b === 'object' ? b : null;
        if (ga) lastGood = ga;
        const base = ga ?? lastGood;
        if (!base) return null;
        const p = sampleGrid(base, lon, lat);
        if (!p || !ga || !gb || gb.t <= ga.t) return p;
        const q = sampleGrid(gb, lon, lat);
        if (!q) return p;
        const f = Math.min(1, Math.max(0, (t - ga.t) / (gb.t - ga.t)));
        return [p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f];
      },
    };
    // grid bbox from first grid once loaded
    load(0);
    return src;
  } catch (e) {
    if (!(e instanceof BackendDown)) console.warn(e);
    markMock('currents');
    return MOCK_SOURCE;
  }
}
