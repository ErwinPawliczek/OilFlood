// Oil particle rendering: fading trails + per-frame interpolated particle heads, plus observed footprints.

import { ScatterplotLayer, GeoJsonLayer, PathLayer } from '@deck.gl/layers';
import { ON_SURFACE, type Tracks, type Footprint, type BudgetRow, type Particle } from './types';

const FRESH = [255, 190, 70];
const AGED = [205, 105, 35];
const STRANDED = [255, 70, 90];

const K = 8; // trail vertices per particle

/** Interpolated position of particle p at hour t (t clamped to its path). */
function at(p: Particle, t: number, out: Float32Array, o: number) {
  const ts = p.ts, last = ts.length - 1;
  if (t <= ts[0] || last === 0) { out[o] = p.path[0][0]; out[o + 1] = p.path[0][1]; return; }
  if (t >= ts[last]) { out[o] = p.path[last][0]; out[o + 1] = p.path[last][1]; return; }
  let lo = 0, hi = last;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (ts[m] <= t) lo = m; else hi = m; }
  const f = (t - ts[lo]) / (ts[hi] - ts[lo] || 1);
  out[o] = p.path[lo][0] + (p.path[hi][0] - p.path[lo][0]) * f;
  out[o + 1] = p.path[lo][1] + (p.path[hi][1] - p.path[lo][1]) * f;
}

// Trails are re-interpolated every frame and drawn with a binary PathLayer
// (TripsLayer mis-assigns timestamps on the MapLibre globe).
export class OilView {
  private bufs: { pos: Float32Array; col: Uint8Array; head: Float32Array; hcol: Uint8Array }[];
  private starts: Uint32Array;
  private cur = 0;
  private count = 0;

  constructor(public tracks: Tracks) {
    const n = tracks.particles.length;
    this.bufs = [0, 1].map(() => ({ pos: new Float32Array(n * K * 2), col: new Uint8Array(n * K * 4), head: new Float32Array(n * 2), hcol: new Uint8Array(n * 4) }));
    this.starts = Uint32Array.from({ length: n }, (_, i) => i * K);
  }

  /** Interpolate trails + heads at hour tH into the next ping-pong buffer. */
  update(tH: number) {
    this.cur ^= 1;
    const { pos, col, head, hcol } = this.bufs[this.cur];
    const ageScale = Math.max(24, this.tracks.durationH / 2);
    const trail = Math.max(2, this.tracks.durationH / 14);
    let n = 0;
    for (const p of this.tracks.particles) {
      const ts = p.ts;
      const last = ts.length - 1;
      if (tH < ts[0]) continue;
      if (tH >= ts[last] && p.status !== 'active' && p.status !== 'stranded') continue; // evaporated / dispersed
      const stranded = p.status === 'stranded' && tH >= ts[last];
      let r: number, g: number, b: number;
      if (stranded) { r = STRANDED[0]; g = STRANDED[1]; b = STRANDED[2]; }
      else {
        const a = Math.min(1, (tH - ts[0]) / ageScale);
        r = FRESH[0] + (AGED[0] - FRESH[0]) * a; g = FRESH[1] + (AGED[1] - FRESH[1]) * a; b = FRESH[2] + (AGED[2] - FRESH[2]) * a;
      }
      for (let k = 0; k < K; k++) {
        // vertex k: oldest (k=0) .. newest (k=K-1)
        const t = Math.max(ts[0], tH - trail * (1 - k / (K - 1)));
        const o = (n * K + k) * 2;
        at(p, t, pos, o);
        const c = (n * K + k) * 4;
        col[c] = r; col[c + 1] = g; col[c + 2] = b; col[c + 3] = 150 * (k / (K - 1)) ** 1.5;
      }
      head[2 * n] = pos[(n * K + K - 1) * 2]; head[2 * n + 1] = pos[(n * K + K - 1) * 2 + 1];
      hcol[4 * n] = r; hcol[4 * n + 1] = g; hcol[4 * n + 2] = b; hcol[4 * n + 3] = stranded ? 235 : 215;
      n++;
    }
    this.count = n;
  }

  layers(opacity = 1) {
    const { pos, col, head, hcol } = this.bufs[this.cur];
    return [
      new PathLayer({
        id: 'oil-trails',
        opacity,
        data: {
          length: this.count,
          startIndices: this.starts.subarray(0, this.count),
          attributes: {
            getPath: { value: pos.subarray(0, this.count * K * 2), size: 2 },
            getColor: { value: col.subarray(0, this.count * K * 4), size: 4, normalized: true },
          },
        } as any,
        _pathType: 'open',
        getWidth: 1.4,
        widthUnits: 'pixels',
        parameters: ON_SURFACE,
      }),
      new ScatterplotLayer({
        id: 'oil-heads',
        opacity,
        data: {
          length: this.count,
          attributes: {
            getPosition: { value: head.subarray(0, this.count * 2), size: 2 },
            getFillColor: { value: hcol.subarray(0, this.count * 4), size: 4, normalized: true },
          },
        } as any,
        radiusUnits: 'pixels',
        getRadius: 2.2,
        radiusMinPixels: 1.5,
        stroked: false,
        parameters: ON_SURFACE,
      }),
    ];
  }
}

/** Latest footprint at or before t (unix s); hidden before the first and once it is older than maxAgeS. */
export function footprintAt(fps: Footprint[], tUnix: number, maxAgeS = 36 * 3600): Footprint | null {
  let cur: Footprint | null = null;
  for (const f of fps) { if (f.t <= tUnix) cur = f; else break; }
  return cur && tUnix - cur.t <= maxAgeS ? cur : null;
}

export function footprintLayer(fps: Footprint[], tUnix: number) {
  const cur = footprintAt(fps, tUnix);
  return new GeoJsonLayer({
    id: 'footprint',
    data: cur ? cur.geojson : { type: 'FeatureCollection', features: [] },
    filled: true,
    stroked: true,
    getFillColor: [255, 150, 50, 60],
    getLineColor: [255, 200, 110, 240],
    lineWidthUnits: 'pixels',
    getLineWidth: 2,
    parameters: ON_SURFACE,
    updateTriggers: { getFillColor: cur?.t },
  });
}

export function budgetAt(budget: BudgetRow[], tH: number): BudgetRow | null {
  if (!budget.length) return null;
  if (tH <= budget[0].t) return budget[0];
  for (let i = 1; i < budget.length; i++) {
    if (budget[i].t >= tH) {
      const a = budget[i - 1], b = budget[i];
      const f = (tH - a.t) / (b.t - a.t || 1);
      const l = (k: keyof BudgetRow) => a[k] + (b[k] - a[k]) * f;
      return { t: tH, surface: l('surface'), evaporated: l('evaporated'), dispersed: l('dispersed'), stranded: l('stranded') };
    }
  }
  return budget[budget.length - 1];
}
