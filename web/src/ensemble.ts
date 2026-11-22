// Ensemble products of our model (docs/ENSEMBLE.md, backend T24): the model-builder schema, the probability grid
// (P(oil present) + expected surface mass per 3 h step, shoreline P(oiled) + ETA quantiles) and the per-force drift
// decomposition. Rendering: a 3D extruded probability surface (ColumnLayer, smoothly interpolated between steps),
// 50 / 90 % contours draped on the sea (marching squares), shoreline ribbons, and a chained "force polygon" of 3D arrows
// at the slick centroid. All data comes from the backend; nothing is synthesised here.

import { ColumnLayer, PathLayer, ScatterplotLayer } from '@deck.gl/layers';
import type { Layer } from '@deck.gl/core';
import { ON_SURFACE } from './types';

// ---------------------------------------------------------------- wire types
export interface ParamDef {
  name: string; label: string; type: 'number' | 'int' | 'bool'; unit?: string | null;
  min: number | null; max: number | null; step?: number | null; default: number | boolean;
  description?: string; citation?: string; group?: string;
}
export interface ParamsSchema {
  model: string; version: string; params: ParamDef[]; groups: { id: string; label: string }[];
  ensemble: { max_members: number; default_members: number; seed_default: number; output_step_h: number; grid_deg: number; perturbations: any[] };
}
export interface Vec { u: number; v: number; speed: number; dir_to: number }
export interface ForceStep {
  h: number; t: number; current: Vec; wind: Vec; stokes: Vec; ml: Vec; total: Vec; wind10?: Vec;
  diffusion?: { k_m2s: number; spread_km: number; speed: number };
  budget?: { surface: number; evaporated: number; dispersed: number; stranded: number };
}
export interface ForcesPayload { run_id: string; source: 'ensemble' | 'control'; members: number; step_h: number; components: string[]; steps: ForceStep[] }
export interface ProbShore {
  id: number; name: string; state: string; lon: number; lat: number; esi: string; esi_label: string; esi_rank: number; shoreline_km: number;
  path: [number, number][]; p_oiled: Record<string, number>; eta_h: { p10: number | null; p50: number | null; p90: number | null };
}
export interface ProbStep {
  h: number; t: number; cells: [number, number, number, number][]; p_max: number;
  area_km2: { p10: number; p50: number; p90: number }; surface_kg: { mean: number; p10: number; p90: number };
  centroid: { mean: [number, number] | null; members: ([number, number] | null)[]; ellipse50?: any; ellipse90?: any };
}
export interface ProbPayload {
  run_id: string; members: number; members_ok: number; seed: number; step_h: number; t0: number; hours: number[]; times: number[];
  grid: { res_deg: number; lon0: number; lat0: number; nx: number; ny: number };
  steps: ProbStep[]; shorelines: ProbShore[]; perturbations: any[];
}

async function poll<T>(url: string, isStale: () => boolean, ok: (b: any) => boolean): Promise<T | null> {
  for (let i = 0; i < 800; i++) {
    if (isStale()) return null;
    const r = await fetch(url);
    const body = await r.json().catch(() => null);
    if (r.status === 200 && body && ok(body)) return body as T;
    if (r.status === 202) { await new Promise((res) => setTimeout(res, 1500)); continue; }
    throw new Error(body?.detail || body?.error || `${r.status} ${url}`);
  }
  return null;
}

let schemaP: Promise<ParamsSchema> | null = null;
export function getModelParams(): Promise<ParamsSchema> {
  schemaP ??= fetch('/api/model/params').then(async (r) => {
    const b = await r.json().catch(() => null);
    if (!r.ok || !b?.params) throw new Error(b?.detail || `Model builder unavailable (${r.status})`);
    return b as ParamsSchema;
  });
  schemaP.catch(() => { schemaP = null; });
  return schemaP;
}
export const fetchProbability = (id: string, isStale: () => boolean) =>
  poll<ProbPayload>(`/api/spill/${encodeURIComponent(id)}/probability`, isStale, (b) => Array.isArray(b.steps));
export const fetchForces = (id: string, isStale: () => boolean) =>
  poll<ForcesPayload>(`/api/spill/${encodeURIComponent(id)}/forces`, isStale, (b) => Array.isArray(b.steps));

// ---------------------------------------------------------------- colours
type RGB = [number, number, number];
/** Sequential ramp for P(oil): dim ember -> amber -> pale gold (one warm hue family, light = more likely). */
const P_RAMP: [number, RGB][] = [[0, [120, 34, 12]], [0.15, [205, 72, 20]], [0.35, [245, 128, 34]], [0.6, [255, 184, 70]], [0.85, [255, 226, 140]], [1, [255, 248, 220]]];
/** Expected mass: deep teal -> aqua -> near white. */
const M_RAMP: [number, RGB][] = [[0, [8, 40, 60]], [0.3, [14, 110, 140]], [0.65, [40, 200, 220]], [1, [210, 250, 255]]];
function ramp(r: [number, RGB][], x: number): RGB {
  x = Math.min(1, Math.max(0, x));
  for (let i = 1; i < r.length; i++) {
    if (x <= r[i][0]) {
      const [a, ca] = r[i - 1], [b, cb] = r[i];
      const f = (x - a) / (b - a || 1);
      return [ca[0] + (cb[0] - ca[0]) * f, ca[1] + (cb[1] - ca[1]) * f, ca[2] + (cb[2] - ca[2]) * f];
    }
  }
  return r[r.length - 1][1];
}
export const rampCSS = (kind: 'p' | 'mass') => {
  const r = kind === 'p' ? P_RAMP : M_RAMP;
  return `linear-gradient(90deg,${r.map(([x, c]) => `rgb(${c.map(Math.round).join(',')}) ${x * 100}%`).join(',')})`;
};

/** Force colours (validated categorical set, dark surface): current, wind drift, Stokes, ML. Total = white. */
export const FORCE_COL: Record<string, string> = { current: '#2f8fe0', wind: '#b88420', stokes: '#24ad78', ml: '#a06ae6', total: '#f2f5fa' };
/** Brighter versions for the map arrows (glow over the dark sea). */
const FORCE_RGB: Record<string, RGB> = { current: [80, 170, 255], wind: [255, 196, 70], stokes: [60, 225, 150], ml: [190, 140, 255], total: [255, 255, 255] };
export const BUDGET_COL: Record<string, string> = { surface: '#c47c1c', evaporated: '#6d84ea', dispersed: '#24a08a', stranded: '#e2464f' };

export const FORCE_INFO: Record<string, { label: string; text: string; eq: string }> = {
  current: { label: 'Current', text: 'The ocean surface current carries the oil with it.', eq: String.raw`\mathbf{U}_c(\mathbf{x},t)` },
  wind: { label: 'Wind drift', text: 'Wind skids floating oil across the water at a few percent of the wind speed.', eq: String.raw`\alpha\,\mathbf{U}_{10},\ \ \alpha \approx 3\,\%` },
  stokes: { label: 'Stokes drift', text: 'Passing waves nudge the surface water forward a little with every crest.', eq: String.raw`\mathbf{U}_S \approx \omega k a^2\,\hat{\mathbf{k}}` },
  ml: { label: 'ML correction', text: 'A learned correction for what the ocean model gets wrong, trained on NOAA drifters.', eq: String.raw`\Delta\mathbf{U}_{ML} = f_{GBM}(\mathbf{U}_c, \mathbf{U}_{10}, \zeta, \delta)` },
  diffusion: { label: 'Diffusion', text: 'Turbulent eddies and spreading smear the slick randomly in every direction.', eq: String.raw`\sigma = \sqrt{2K\Delta t},\ K = K_h + K_{Fay}` },
  total: { label: 'Net drift', text: 'The vector sum of all pushes: where the slick centre actually heads.', eq: String.raw`\frac{d\mathbf{x}}{dt} = \mathbf{U}_c + \alpha\mathbf{U}_{10} + \mathbf{U}_S + \Delta\mathbf{U}_{ML}` },
};

// ---------------------------------------------------------------- probability view
const KM_PER_DEG = 111.32;
export type SurfaceKind = 'p' | 'mass';

/** Linear interpolation helper over a sorted list of step hours. */
function bracket(hours: number[], tH: number): [number, number, number] {
  if (tH <= hours[0]) return [0, 0, 0];
  const n = hours.length - 1;
  if (tH >= hours[n]) return [n, n, 0];
  let i = 0;
  while (i < n && hours[i + 1] < tH) i++;
  return [i, i + 1, (tH - hours[i]) / (hours[i + 1] - hours[i] || 1)];
}

export class ProbView {
  readonly hours: number[];
  /** cells that ever hold oil: index into the dense grid */
  private idx: Uint32Array;
  private pos: Float32Array;
  private P: Float32Array[]; // per step, per active cell
  private M: Float32Array[];
  private mMax = 0;
  private elev = new Float32Array(0);
  private col = new Uint8Array(0);
  private dense: Float32Array;
  private contourKey = '';
  private contours: { level: number; segs: [number, number][][] }[] = [];
  private bbox = [0, 0, 0, 0]; // ix0, iy0, ix1, iy1 of active cells
  readonly mmaxKg: number;

  constructor(public d: ProbPayload) {
    const { nx, ny, lon0, lat0, res_deg: r } = d.grid;
    this.hours = d.steps.map((s) => s.h);
    const active = new Map<number, number>();
    for (const s of d.steps) for (const c of s.cells) { const k = c[1] * nx + c[0]; if (!active.has(k)) active.set(k, active.size); }
    this.idx = Uint32Array.from(active.keys());
    const n = this.idx.length;
    this.pos = new Float32Array(n * 2);
    let ix0 = nx, iy0 = ny, ix1 = 0, iy1 = 0;
    this.idx.forEach((k, i) => {
      const ix = k % nx, iy = (k / nx) | 0;
      this.pos[2 * i] = lon0 + (ix + 0.5) * r; this.pos[2 * i + 1] = lat0 + (iy + 0.5) * r;
      ix0 = Math.min(ix0, ix); iy0 = Math.min(iy0, iy); ix1 = Math.max(ix1, ix); iy1 = Math.max(iy1, iy);
    });
    this.bbox = [Math.max(0, ix0 - 1), Math.max(0, iy0 - 1), Math.min(nx - 1, ix1 + 1), Math.min(ny - 1, iy1 + 1)];
    this.P = d.steps.map(() => new Float32Array(n));
    this.M = d.steps.map(() => new Float32Array(n));
    d.steps.forEach((s, j) => {
      for (const [ix, iy, p, m] of s.cells) {
        const i = active.get(iy * nx + ix)!;
        this.P[j][i] = p; this.M[j][i] = m;
        if (m > this.mMax) this.mMax = m;
      }
    });
    this.mmaxKg = this.mMax;
    this.elev = new Float32Array(n);
    this.col = new Uint8Array(n * 4);
    this.dense = new Float32Array(nx * ny);
  }

  /** [w, s, e, n] of every cell that ever holds oil plus the oiled shorelines (camera framing). */
  bounds(): [number, number, number, number] {
    const { lon0, lat0, res_deg: r } = this.d.grid;
    const [x0, y0, x1, y1] = this.bbox;
    let w = lon0 + x0 * r, s = lat0 + y0 * r, e = lon0 + (x1 + 1) * r, n = lat0 + (y1 + 1) * r;
    for (const sh of this.d.shorelines) for (const [x, y] of sh.path ?? []) { w = Math.min(w, x); e = Math.max(e, x); s = Math.min(s, y); n = Math.max(n, y); }
    return [w, s, e, n];
  }
  get members() { return this.d.members_ok ?? this.d.members; }
  get cellKm() { return this.d.grid.res_deg * KM_PER_DEG; }

  /** Step summary interpolated at tH (area, centroid). */
  stepAt(tH: number): ProbStep | null {
    if (!this.d.steps.length) return null;
    const [a, b, f] = bracket(this.hours, tH);
    return f < 0.5 ? this.d.steps[a] : this.d.steps[b];
  }
  centroidAt(tH: number): [number, number] | null {
    const [a, b, f] = bracket(this.hours, tH);
    const ca = this.d.steps[a]?.centroid?.mean, cb = this.d.steps[b]?.centroid?.mean;
    if (ca && cb) return [ca[0] + (cb[0] - ca[0]) * f, ca[1] + (cb[1] - ca[1]) * f];
    return ca ?? cb ?? null;
  }

  /** Fill elevation + colour buffers at hour tH (interpolated between the bracketing 3 h steps). */
  private fill(tH: number, kind: SurfaceKind, threeD: boolean) {
    const [a, b, f] = bracket(this.hours, tH);
    const Pa = this.P[a], Pb = this.P[b], Ma = this.M[a], Mb = this.M[b];
    const n = this.idx.length;
    const H = threeD ? 60000 : 0; // m at p = 1
    const mNorm = this.mMax > 0 ? 1 / Math.sqrt(this.mMax) : 0;
    for (let i = 0; i < n; i++) {
      const p = Pa[i] + (Pb[i] - Pa[i]) * f;
      const m = Ma[i] + (Mb[i] - Ma[i]) * f;
      const x = kind === 'p' ? p : Math.sqrt(Math.max(0, m)) * mNorm;
      const on = kind === 'p' ? p >= 0.02 : m > 0;
      this.elev[i] = on ? Math.max(60, x * H) : 0;
      const c = ramp(kind === 'p' ? P_RAMP : M_RAMP, x);
      const o = 4 * i;
      this.col[o] = c[0]; this.col[o + 1] = c[1]; this.col[o + 2] = c[2];
      this.col[o + 3] = on ? (threeD ? 150 + 100 * x : 60 + 170 * x) : 0;
    }
    return { a, b, f };
  }

  /** Marching squares on cell centres for the P = 0.5 and 0.9 levels (recomputed every ~0.25 h). */
  private updateContours(tH: number) {
    const key = (Math.round(tH * 4) / 4).toFixed(2);
    if (key === this.contourKey) return;
    this.contourKey = key;
    const { nx, lon0, lat0, res_deg: r } = this.d.grid;
    const [a, b, f] = bracket(this.hours, tH);
    const D = this.dense;
    D.fill(0);
    this.idx.forEach((k, i) => { D[k] = this.P[a][i] + (this.P[b][i] - this.P[a][i]) * f; });
    const [x0, y0, x1, y1] = this.bbox;
    const X = (ix: number) => lon0 + (ix + 0.5) * r, Y = (iy: number) => lat0 + (iy + 0.5) * r;
    this.contours = [0.5, 0.9].map((L) => {
      const segs: [number, number][][] = [];
      for (let iy = y0; iy < y1; iy++) {
        for (let ix = x0; ix < x1; ix++) {
          const v0 = D[iy * nx + ix], v1 = D[iy * nx + ix + 1], v2 = D[(iy + 1) * nx + ix + 1], v3 = D[(iy + 1) * nx + ix];
          const c = (v0 >= L ? 1 : 0) | (v1 >= L ? 2 : 0) | (v2 >= L ? 4 : 0) | (v3 >= L ? 8 : 0);
          if (c === 0 || c === 15) continue;
          const e = (va: number, vb: number) => (L - va) / (vb - va || 1e-9);
          // edge points: bottom (0-1), right (1-2), top (3-2), left (0-3)
          const pB: [number, number] = [X(ix) + e(v0, v1) * r, Y(iy)];
          const pR: [number, number] = [X(ix + 1), Y(iy) + e(v1, v2) * r];
          const pT: [number, number] = [X(ix) + e(v3, v2) * r, Y(iy + 1)];
          const pL: [number, number] = [X(ix), Y(iy) + e(v0, v3) * r];
          const S: Record<number, [number, number][][]> = {
            1: [[pL, pB]], 2: [[pB, pR]], 3: [[pL, pR]], 4: [[pR, pT]], 5: [[pL, pT], [pB, pR]], 6: [[pB, pT]], 7: [[pL, pT]],
            8: [[pT, pL]], 9: [[pT, pB]], 10: [[pT, pR], [pL, pB]], 11: [[pT, pR]], 12: [[pR, pL]], 13: [[pR, pB]], 14: [[pB, pL]],
          };
          for (const s of S[c]) segs.push(s);
        }
      }
      return { level: L, segs };
    });
  }

  layers(tH: number, kind: SurfaceKind, threeD: boolean): Layer[] {
    const n = this.idx.length;
    if (!n) return [];
    this.fill(tH, kind, threeD);
    this.updateContours(tH);
    const r = this.d.grid.res_deg;
    const radiusM = (r * KM_PER_DEG * 1000 * Math.cos((this.pos[1] * Math.PI) / 180)) / Math.SQRT2 * 0.92;
    const out: Layer[] = [
      new ColumnLayer({
        id: 'ens-surface',
        data: {
          length: n,
          attributes: {
            getPosition: { value: this.pos, size: 2 },
            getElevation: { value: this.elev, size: 1 },
            getFillColor: { value: this.col, size: 4, normalized: true },
          },
        } as any,
        diskResolution: 4,
        angle: 45,
        radius: radiusM,
        extruded: threeD,
        flatShading: false,
        material: { ambient: 0.55, diffuse: 0.6, shininess: 24, specularColor: [255, 220, 170] },
        coverage: 1,
        elevationScale: 1,
        parameters: threeD ? { depthCompare: 'less-equal', depthWriteEnabled: true } : ON_SURFACE,
      } as any),
    ];
    for (const c of this.contours) {
      out.push(new PathLayer({
        id: `ens-contour-${c.level}`,
        data: c.segs,
        getPath: (d: any) => d,
        getColor: c.level >= 0.9 ? [255, 255, 255, 235] : [255, 200, 110, 220],
        getWidth: c.level >= 0.9 ? 2.4 : 1.6,
        widthUnits: 'pixels',
        capRounded: true,
        parameters: ON_SURFACE,
        updateTriggers: { getPath: this.contourKey },
      }));
    }
    return out;
  }

  /** Shoreline segments as ribbons coloured by P(oiled by tH). */
  shoreLayers(tH: number): Layer[] {
    const sh = this.d.shorelines.filter((s) => s.path?.length > 1);
    if (!sh.length) return [];
    return [new PathLayer({
      id: 'ens-shore',
      data: sh,
      getPath: (s: ProbShore) => s.path,
      getColor: (s: ProbShore) => {
        const p = pOiledAt(s, tH);
        const c = ramp(P_RAMP, 0.35 + 0.65 * p);
        return [c[0], c[1], c[2], p > 0 ? 120 + 135 * p : 50];
      },
      getWidth: (s: ProbShore) => 2 + 6 * pOiledAt(s, tH),
      widthUnits: 'pixels',
      capRounded: true,
      jointRounded: true,
      parameters: ON_SURFACE,
      updateTriggers: { getColor: Math.round(tH * 2), getWidth: Math.round(tH * 2) },
    })];
  }
}

/** P(oiled by hour tH) for a shoreline, interpolated over its p_oiled keys ("24", "48", "72", ...; "end" ignored). */
export function pOiledAt(s: ProbShore, tH: number) {
  const pts = Object.entries(s.p_oiled ?? {}).filter(([k]) => /^\d+(\.\d+)?$/.test(k)).map(([k, v]) => [Number(k), v] as [number, number]).sort((a, b) => a[0] - b[0]);
  if (!pts.length) return s.p_oiled?.end ?? 0;
  // before the P10 arrival nothing is oiled yet; ramp from 0 at P10 ETA to the first tabulated value
  const t10 = s.eta_h?.p10;
  let prev: [number, number] = [t10 != null ? Math.min(t10, pts[0][0]) : 0, 0];
  for (const p of pts) {
    if (tH <= p[0]) return tH <= prev[0] ? prev[1] : prev[1] + ((p[1] - prev[1]) * (tH - prev[0])) / (p[0] - prev[0] || 1);
    prev = p;
  }
  return prev[1];
}

// ---------------------------------------------------------------- forces at the centroid
/** Force vector of component c interpolated at hour tH (each step averages the preceding step_h). */
export function forceAt(F: ForcesPayload, c: string, tH: number): Vec | null {
  const st = F.steps;
  if (!st.length) return null;
  const mid = st.map((s) => s.h - F.step_h / 2);
  const [a, b, f] = bracket(mid, tH);
  const va = (st[a] as any)[c] as Vec | undefined, vb = (st[b] as any)[c] as Vec | undefined;
  if (!va || !vb) return va ?? vb ?? null;
  const u = va.u + (vb.u - va.u) * f, v = va.v + (vb.v - va.v) * f;
  return { u, v, speed: Math.hypot(u, v), dir_to: ((Math.atan2(u, v) * 180) / Math.PI + 360) % 360 };
}
export function stepAt(F: ForcesPayload, tH: number) {
  const mid = F.steps.map((s) => s.h - F.step_h / 2);
  const [a, b, f] = bracket(mid, tH);
  return F.steps[f < 0.5 ? a : b];
}

export const FORCE_KEYS = ['current', 'wind', 'stokes', 'ml'] as const;

/** Chained 3D arrows (current -> wind -> Stokes -> ML, head to tail) plus the net drift from the centroid.
 *  Length: 1 m/s = KM_PER_MS km. Drawn ~ALT m above the sea in 3D (with a stem down to the slick), flat in 2D. */
/** km of arrow per m/s: the largest chained force stack of the run maps to ~45 km, so weak drifts stay readable. */
const scaleCache = new WeakMap<ForcesPayload, number>();
function kmPerMs(F: ForcesPayload) {
  let s = scaleCache.get(F);
  if (s == null) {
    const mx = Math.max(0.02, ...F.steps.map((st) => FORCE_KEYS.reduce((a, k) => a + ((st as any)[k]?.speed ?? 0), 0)));
    s = Math.min(1500, Math.max(40, 45 / mx));
    scaleCache.set(F, s);
  }
  return s;
}
export function forceLayers(F: ForcesPayload, tH: number, at: [number, number], threeD: boolean, onHover: (info: any) => void): Layer[] {
  const KM_PER_MS = kmPerMs(F);
  const alt = threeD ? 22000 : 0;
  const cosLat = Math.cos((at[1] * Math.PI) / 180);
  const step = (p: [number, number], v: Vec): [number, number] => [p[0] + (v.u * KM_PER_MS) / (KM_PER_DEG * cosLat), p[1] + (v.v * KM_PER_MS) / KM_PER_DEG];
  const arrows: { key: string; from: [number, number]; to: [number, number]; v: Vec }[] = [];
  let p = at;
  for (const k of FORCE_KEYS) {
    const v = forceAt(F, k, tH);
    if (!v || v.speed < 0.004) continue;
    const q = step(p, v);
    arrows.push({ key: k, from: p, to: q, v });
    p = q;
  }
  const tot = forceAt(F, 'total', tH);
  if (tot && tot.speed > 0.004) arrows.push({ key: 'total', from: at, to: step(at, tot), v: tot });
  const z = (x: [number, number]) => [x[0], x[1], alt];
  const paths = arrows.map((a) => {
    // arrow head: two barbs at 25° back from the tip, 22 % of the length (min ~2 km)
    const dx = (a.to[0] - a.from[0]) * cosLat, dy = a.to[1] - a.from[1];
    const len = Math.hypot(dx, dy) || 1e-9;
    const hl = Math.max(0.02, 0.22 * len);
    const barb = (ang: number) => {
      const c = Math.cos(ang), s = Math.sin(ang);
      const bx = (-dx / len) * c - (-dy / len) * s, by = (-dx / len) * s + (-dy / len) * c;
      return [a.to[0] + (bx * hl) / cosLat, a.to[1] + by * hl] as [number, number];
    };
    const r = (25 * Math.PI) / 180;
    return { ...a, path: [z(a.from), z(a.to)], head: [z(barb(r)), z(a.to), z(barb(-r))] };
  });
  const col = (k: string, al = 255) => [...FORCE_RGB[k], al] as [number, number, number, number];
  const layers: Layer[] = [];
  if (threeD) {
    layers.push(new PathLayer({
      id: 'force-stem', data: [{ path: [[at[0], at[1], 0], [at[0], at[1], alt]] }], getPath: (d: any) => d.path,
      getColor: [255, 255, 255, 120], getWidth: 1.2, widthUnits: 'pixels',
    }));
  }
  layers.push(
    new PathLayer({
      id: 'force-glow', data: paths, getPath: (d: any) => d.path, getColor: (d: any) => col(d.key, 60),
      getWidth: (d: any) => (d.key === 'total' ? 18 : 14), widthUnits: 'pixels', capRounded: true,
      parameters: threeD ? undefined : ON_SURFACE,
    }),
    new PathLayer({
      id: 'force-shaft', data: paths, getPath: (d: any) => d.path, getColor: (d: any) => col(d.key),
      getWidth: (d: any) => (d.key === 'total' ? 5.5 : 4.5), widthUnits: 'pixels', capRounded: true,
      pickable: true, autoHighlight: true, highlightColor: [255, 255, 255, 255], onHover,
      parameters: threeD ? undefined : ON_SURFACE,
    }),
    new PathLayer({
      id: 'force-head', data: paths, getPath: (d: any) => d.head, getColor: (d: any) => col(d.key),
      getWidth: (d: any) => (d.key === 'total' ? 5.5 : 4.5), widthUnits: 'pixels', capRounded: true, jointRounded: true,
      pickable: true, onHover, parameters: threeD ? undefined : ON_SURFACE,
    }),
    new ScatterplotLayer({
      id: 'force-origin', data: [z(at)], getPosition: (d: any) => d, getRadius: 5, radiusUnits: 'pixels',
      getFillColor: [255, 255, 255, 255], stroked: true, getLineColor: [10, 16, 28, 255], lineWidthUnits: 'pixels', getLineWidth: 2,
      parameters: threeD ? undefined : ON_SURFACE,
    }),
  );
  return layers;
}
