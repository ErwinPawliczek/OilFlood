// Backend client. Everything shown comes from the backend's real cached data; there is no
// client-side synthetic fallback. Failures surface as ApiError with the backend's message.

import type { CatalogSpill, TracksPayload, Tracks, Footprint, CurrentGrid, BudgetRow } from './types';

export class ApiError extends Error {
  constructor(msg: string, public status = 0) { super(msg); }
}

async function getJSON<T = any>(url: string, init?: RequestInit): Promise<T> {
  let r: Response;
  try {
    r = await fetch(url, init);
  } catch {
    throw new ApiError('Backend unreachable. Is the server running?');
  }
  const ct = r.headers.get('content-type') || '';
  if (!ct.includes('json')) throw new ApiError(r.status >= 500 ? 'Backend unreachable. Is the server running?' : `${r.status} ${url}`, r.status);
  const body = await r.json();
  if (!r.ok && r.status !== 202 && r.status !== 409) {
    const d = body?.detail;
    throw new ApiError(typeof d === 'string' ? d : `${r.status} ${url}`, r.status);
  }
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
  // budget may be fractions or absolute mass (kg); normalise each row to fractions of the released total
  const budget: BudgetRow[] = (p.budget ?? []).map((b) => {
    const tot = (b.surface || 0) + (b.evaporated || 0) + (b.dispersed || 0) + (b.stranded || 0);
    const k = tot > 1.001 ? 1 / tot : 1;
    return { t: (toUnix(b.t) - t0) / 3600, surface: (b.surface || 0) * k, evaporated: (b.evaporated || 0) * k, dispersed: (b.dispersed || 0) * k, stranded: (b.stranded || 0) * k };
  });
  return { t0, durationH: Math.max(1, (tEnd - t0) / 3600), particles, budget, meta: (p as any).meta };
}

// ---------- historical ----------
export async function getCatalog(): Promise<CatalogSpill[]> {
  const r = await getJSON<any>('/api/spills');
  return Array.isArray(r) ? r : r.spills ?? r.items ?? [];
}

/** Hindcast tracks. The backend answers 202 {id, status, progress} while the run computes; we poll until done.
 *  Returns null if superseded; throws ApiError (with the backend's message) on failure. */
export async function getHistoricalTracks(s: CatalogSpill, model: ModelId, onProgress: (p: number) => void = () => {}, isStale = () => false): Promise<Tracks | null> {
  const url = `/api/spills/${encodeURIComponent(s.id)}/tracks?model=${model}`;
  let r = await getJSON<any>(url);
  while (!r.particles && r.id && r.status) {
    if (r.status === 'error') throw new ApiError(r.error || 'Hindcast failed');
    if (isStale()) return null;
    onProgress(Number(r.progress ?? 0));
    await new Promise((res) => setTimeout(res, 1000));
    const st = await getJSON<any>(`/api/spill/${encodeURIComponent(r.id)}`);
    if (st.status === 'done') r = await getJSON<any>(url);
    else r = { ...st, id: st.id ?? r.id };
  }
  return normalizeTracks(r as TracksPayload);
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
  return normalizeFootprints(await getJSON<any>(`/api/spills/${encodeURIComponent(s.id)}/footprints`));
}

// ---------- models ----------
/** openoil = OpenDrift OpenOil baseline; ours = OilFlood's own model (+ ML drift correction). */
export type ModelId = 'openoil' | 'ours';
export const MODELS: ModelId[] = ['openoil', 'ours'];
export interface ModelInfo { id: ModelId; label: string; short: string; summary: string; skill?: any }
export async function getModels(): Promise<ModelInfo[]> {
  const r = await getJSON<any>('/api/models');
  return r.models ?? [];
}

// ---------- prediction ----------
export interface SpillJob {
  id: string;
  status?: string;
  lat: number; lon: number;
  /** where the backend actually seeded (may be snapped to the nearest model water) */
  seedLat?: number; seedLon?: number;
}

/** Prediction release settings (set in the panel; amount in barrels, oil = id from GET /api/oils). */
export const PRED = { durationH: 72, volumeBbl: 1000, oilId: 'crude_medium', nParticles: 2000 };

export interface OilOption { id: string; label: string; adios: string }
export async function getOils(): Promise<{ default: string; oils: OilOption[] }> {
  return getJSON('/api/oils');
}

export interface ForecastPart { model: string; issued_at: string | null; valid_from: string; valid_until: string; source?: string }
/** GET /api/live: the live forecast every prediction runs on. */
export interface LiveForecast {
  available: boolean;
  currents: ForecastPart;
  winds: ForecastPart;
  valid_from: string;
  valid_until: string;
  updated_at?: string;
  hours_available: number;
  short_by_h: number;
  stale: boolean;
  note?: string;
}
export async function getLive(): Promise<LiveForecast> {
  const r = await fetch('/api/live').catch(() => null);
  if (!r) throw new ApiError('Backend unreachable. Is the server running?');
  const body = await r.json().catch(() => null);
  if (!body) throw new ApiError(`${r.status} /api/live`, r.status);
  if (r.status === 503) throw new ApiError(body.note || 'No live forecast on the server', 503);
  return body as LiveForecast;
}

/** What the backend accepted for a run (POST /api/spill and GET /api/spill/{id}). */
export interface RunSummary {
  duration_h?: number;
  requested_duration_h?: number;
  volume_bbl?: number;
  oil_type?: string;
  truncated?: boolean;
  note?: string;
  forecast?: { currents: ForecastPart; winds: ForecastPart; valid_from: string; valid_until: string };
}

export async function postSpill(lat: number, lon: number, model: ModelId = 'openoil'): Promise<SpillJob & RunSummary> {
  const r = await getJSON<any>('/api/spill', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ lat, lon, duration_h: PRED.durationH, volume_bbl: PRED.volumeBbl, oil_type: PRED.oilId, n_particles: PRED.nParticles, model }),
  });
  return { ...r, id: String(r.id), lat, lon };
}

export async function pollSpill(job: SpillJob): Promise<{ status: string; progress: number; error?: string }> {
  const r = await getJSON<any>(`/api/spill/${encodeURIComponent(job.id)}`);
  let progress = Number(r.progress ?? 0);
  if (progress > 1) progress /= 100;
  return { status: r.status, progress, error: r.error };
}

export async function getSpillTracks(job: SpillJob): Promise<Tracks> {
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
  /** unix s span of the real fields; sample() returns null outside it (plus a half-step tolerance) */
  t0: number;
  t1: number;
  /** human-readable data source */
  source: string;
  bbox: BBox;
  /** current (m/s) at unix time t; null for land / outside */
  sample(lon: number, lat: number, t: number): [number, number] | null;
  /** prefetch grids around t */
  prefetch(t: number): void;
}

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

export const GULF_BBOX: BBox = { minLon: -98, minLat: 18, maxLon: -80, maxLat: 31 };

/** Real cached Gulf HYCOM currents for [start, end]: lists timesteps, lazily fetches grids.
 *  Returns null when the backend has no cached currents for that window. */
export async function getCurrents(startUnix: number, endUnix: number, bbox: BBox = GULF_BBOX): Promise<CurrentsSource | null> {
  {
    const iso = (t: number) => new Date(t * 1000).toISOString().slice(0, 19) + 'Z';
    const b = `${bbox.minLon},${bbox.minLat},${bbox.maxLon},${bbox.maxLat}`;
    const r = await getJSON<any>(`/api/currents?layer=gulf&start=${iso(startUnix)}&end=${iso(endUnix)}&bbox=${b}`);
    const raw: any[] = Array.isArray(r) ? r : r.times ?? r.timesteps ?? [];
    const steps = raw.map((x) => {
      const key = typeof x === 'object' ? x.t ?? x.time ?? x.id : x;
      return { key: String(key), t: toUnix(key) };
    }).filter((s) => Number.isFinite(s.t)).sort((a, b) => a.t - b.t);
    if (!steps.length) return null;
    if (Array.isArray(r.bbox) && r.bbox.length === 4) bbox = { minLon: r.bbox[0], minLat: r.bbox[1], maxLon: r.bbox[2], maxLat: r.bbox[3] };
    const grids = new Map<string, CurrentGrid | 'loading' | 'error'>();
    const load = (k: number) => {
      const s = steps[k];
      if (!s || grids.has(s.key)) return;
      grids.set(s.key, 'loading');
      getJSON<any>(`/api/currents/${encodeURIComponent(s.key)}?layer=gulf`)
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
    const tol = steps.length > 1 ? Math.min(6 * 3600, (steps[steps.length - 1].t - steps[0].t) / (steps.length - 1)) : 3 * 3600;
    const src: CurrentsSource = {
      bbox,
      t0: steps[0].t,
      t1: steps[steps.length - 1].t,
      source: typeof r.source === 'string' ? r.source : 'HYCOM Gulf of Mexico (1/25°)',
      prefetch(t) { const k = idxAt(t); load(k); load(k + 1); load(k + 2); },
      sample(lon, lat, t) {
        if (t < steps[0].t - tol || t > steps[steps.length - 1].t + tol) return null; // no real data for this time
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
  }
}
