// Oil particle rendering: TripsLayer trails + per-frame interpolated particle heads, plus observed footprints.

import { TripsLayer } from '@deck.gl/geo-layers';
import { ScatterplotLayer, GeoJsonLayer } from '@deck.gl/layers';
import type { Tracks, Footprint, BudgetRow } from './types';

const FRESH = [255, 190, 70];
const AGED = [120, 60, 20];
const STRANDED = [255, 70, 90];

export class OilView {
  private pos: Float32Array[];
  private col: Uint8Array[];
  private cur = 0;
  private count = 0;

  constructor(public tracks: Tracks) {
    const n = tracks.particles.length;
    this.pos = [new Float32Array(n * 2), new Float32Array(n * 2)];
    this.col = [new Uint8Array(n * 4), new Uint8Array(n * 4)];
  }

  /** Interpolate particle heads at hour tH into the next ping-pong buffer. */
  update(tH: number) {
    this.cur ^= 1;
    const pos = this.pos[this.cur], col = this.col[this.cur];
    const ageScale = Math.max(24, this.tracks.durationH / 2);
    let n = 0;
    for (const p of this.tracks.particles) {
      const ts = p.ts;
      const last = ts.length - 1;
      if (tH < ts[0]) continue;
      let x: number, y: number;
      let stranded = false;
      if (tH >= ts[last]) {
        if (p.status === 'stranded') stranded = true;
        else if (p.status !== 'active') continue; // evaporated / dispersed: gone from the surface
        [x, y] = p.path[last];
      } else {
        let lo = 0, hi = last;
        while (hi - lo > 1) { const m = (lo + hi) >> 1; if (ts[m] <= tH) lo = m; else hi = m; }
        const f = (tH - ts[lo]) / (ts[hi] - ts[lo] || 1);
        x = p.path[lo][0] + (p.path[hi][0] - p.path[lo][0]) * f;
        y = p.path[lo][1] + (p.path[hi][1] - p.path[lo][1]) * f;
      }
      pos[2 * n] = x; pos[2 * n + 1] = y;
      const c = 4 * n;
      if (stranded) {
        col[c] = STRANDED[0]; col[c + 1] = STRANDED[1]; col[c + 2] = STRANDED[2]; col[c + 3] = 230;
      } else {
        const a = Math.min(1, (tH - ts[0]) / ageScale);
        col[c] = FRESH[0] + (AGED[0] - FRESH[0]) * a;
        col[c + 1] = FRESH[1] + (AGED[1] - FRESH[1]) * a;
        col[c + 2] = FRESH[2] + (AGED[2] - FRESH[2]) * a;
        col[c + 3] = 210;
      }
      n++;
    }
    this.count = n;
  }

  layers(tH: number, visible = true) {
    const trail = Math.max(2, this.tracks.durationH / 14);
    return [
      new TripsLayer({
        id: 'oil-trails',
        visible,
        data: this.tracks.particles,
        getPath: (d: any) => d.path,
        getTimestamps: (d: any) => d.ts,
        getColor: (d: any) => (d.status === 'stranded' ? [255, 90, 100] : [235, 150, 50]),
        opacity: 0.35,
        widthMinPixels: 1.2,
        capRounded: true,
        jointRounded: true,
        fadeTrail: true,
        trailLength: trail,
        currentTime: tH,
        parameters: { depthTest: false } as any,
      }),
      new ScatterplotLayer({
        id: 'oil-heads',
        visible,
        data: {
          length: this.count,
          attributes: {
            getPosition: { value: this.pos[this.cur], size: 2 },
            getFillColor: { value: this.col[this.cur], size: 4, normalized: true },
          },
        } as any,
        radiusUnits: 'pixels',
        getRadius: 2.2,
        radiusMinPixels: 1.5,
        stroked: false,
        parameters: { depthTest: false } as any,
      }),
    ];
  }
}

/** Latest footprint at or before t (unix s); hidden before the first. */
export function footprintLayer(fps: Footprint[], tUnix: number) {
  let cur: Footprint | null = null;
  for (const f of fps) { if (f.t <= tUnix) cur = f; else break; }
  return new GeoJsonLayer({
    id: 'footprint',
    data: cur ? cur.geojson : { type: 'FeatureCollection', features: [] },
    filled: true,
    stroked: true,
    getFillColor: [20, 12, 6, 150],
    getLineColor: [255, 170, 60, 230],
    lineWidthUnits: 'pixels',
    getLineWidth: 1.5,
    parameters: { depthTest: false } as any,
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
