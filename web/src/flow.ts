// Windy-style animated current streaks: CPU-advected particles with fading trails, drawn as one binary PathLayer
// (LineLayer does not render on the MapLibre globe in interleaved mode)
// from preallocated binary buffers (no per-frame allocations).

import { PathLayer } from '@deck.gl/layers';
import type { CurrentsSource, BBox } from './api';
import { ON_SURFACE } from './types';

const TRAIL = 14;
const SEG = TRAIL - 1;

// speed colour ramp (m/s): deep blue -> cyan -> green -> yellow -> magenta
const RAMP: [number, number, number, number][] = [
  [0.0, 70, 120, 230],
  [0.15, 60, 175, 240],
  [0.35, 60, 220, 190],
  [0.6, 170, 240, 110],
  [0.9, 250, 220, 80],
  [1.3, 250, 120, 90],
  [1.8, 240, 90, 200],
];
export const SPEED_RAMP = RAMP;

function ramp(s: number, out: Uint8Array, o: number) {
  let k = 0;
  while (k < RAMP.length - 2 && s > RAMP[k + 1][0]) k++;
  const a = RAMP[k], b = RAMP[k + 1];
  const f = Math.min(1, Math.max(0, (s - a[0]) / (b[0] - a[0])));
  out[o] = a[1] + (b[1] - a[1]) * f;
  out[o + 1] = a[2] + (b[2] - a[2]) * f;
  out[o + 2] = a[3] + (b[3] - a[3]) * f;
}

export interface FlowOptions {
  id?: string;
  n?: number; // particle count
  width?: number; // px
  alpha?: number; // 0..255 peak trail opacity
}

export class FlowField {
  private n: number;
  private hist: Float32Array; // ring of positions per particle (oldest..newest by head index)
  private head: Int32Array;
  private age: Float32Array;
  private life: Float32Array;
  private alive: Uint8Array;
  private speed: Float32Array;
  // ping-pong buffers: deck.gl only re-uploads external buffers when the reference changes
  private bufs: { pos: Float32Array; col: Uint8Array }[];
  private starts: Uint32Array;
  private cur = 0;
  private version = 0;
  private id: string;
  private width: number;
  private alpha: number;
  /** spawn on a spherical cap around this point (visible hemisphere) when the view is the whole globe */
  private capCenter: [number, number] | null = null;

  constructor(public source: CurrentsSource, opts: FlowOptions = {}) {
    const N = (this.n = opts.n ?? 3500);
    this.id = opts.id ?? 'currents';
    this.width = opts.width ?? 1.8;
    this.alpha = opts.alpha ?? 235;
    this.hist = new Float32Array(N * TRAIL * 2);
    this.head = new Int32Array(N);
    this.age = new Float32Array(N);
    this.life = new Float32Array(N);
    this.alive = new Uint8Array(N);
    this.speed = new Float32Array(N);
    this.bufs = [0, 1].map(() => ({ pos: new Float32Array(N * TRAIL * 2), col: new Uint8Array(N * TRAIL * 4) }));
    this.starts = Uint32Array.from({ length: N }, (_, i) => i * TRAIL);
  }

  setSource(s: CurrentsSource) {
    this.source = s;
    this.alive.fill(0);
  }

  private spawn(p: number, box: BBox, t: number): boolean {
    const cap = this.capCenter;
    for (let tries = 0; tries < 6; tries++) {
      let lon: number, lat: number;
      if (cap) [lon, lat] = randomOnCap(cap[0], cap[1], 80);
      else {
        lon = box.minLon + Math.random() * (box.maxLon - box.minLon);
        lat = box.minLat + Math.random() * (box.maxLat - box.minLat);
      }
      if (!this.source.sample(lon, lat, t)) continue;
      const base = p * TRAIL * 2;
      for (let k = 0; k < TRAIL; k++) { this.hist[base + 2 * k] = lon; this.hist[base + 2 * k + 1] = lat; }
      this.head[p] = 0;
      this.age[p] = 0;
      this.life[p] = 50 + Math.random() * 90;
      this.alive[p] = 1;
      return true;
    }
    this.alive[p] = 0;
    return false;
  }

  /** Advance one frame. view = visible lon/lat box, zoom = map zoom, t = sim unix time. */
  step(view: BBox, zoom: number, t: number, dtFrames = 1, center?: [number, number], activeFrac = 1) {
    const N = this.n;
    const active = Math.round(N * Math.min(1, Math.max(0, activeFrac)));
    const fb = this.source.bbox;
    // whole-globe view: spawn uniformly over the visible hemisphere instead of a lon/lat box (avoids piling
    // particles at the poles and on the far side of the globe)
    this.capCenter = center && view.maxLon - view.minLon >= 300 ? center : null;
    const box: BBox = {
      minLon: Math.max(fb.minLon, view.minLon), maxLon: Math.min(fb.maxLon, view.maxLon),
      minLat: Math.max(fb.minLat, view.minLat), maxLat: Math.min(fb.maxLat, view.maxLat),
    };
    const visible = box.minLon < box.maxLon && box.minLat < box.maxLat;
    // degrees moved per frame for 1 m/s, scaled so streak length on screen is roughly zoom-independent
    const k = 0.012 * Math.pow(2, 4.5 - zoom) * dtFrames;
    const { hist, head } = this;
    for (let p = 0; p < N; p++) {
      if (!visible || p >= active) { this.alive[p] = 0; continue; }
      if (!this.alive[p] || this.age[p] > this.life[p]) {
        if (!this.spawn(p, box, t)) continue;
      }
      const base = p * TRAIL * 2;
      const h = head[p];
      const lon = hist[base + 2 * h], lat = hist[base + 2 * h + 1];
      if (lon < box.minLon || lon > box.maxLon || lat < box.minLat || lat > box.maxLat) { this.alive[p] = 0; continue; }
      const v = this.source.sample(lon, lat, t);
      if (!v) { this.age[p] = this.life[p] + 1; continue; }
      const s = Math.hypot(v[0], v[1]);
      this.speed[p] = s;
      const nh = (h + 1) % TRAIL;
      hist[base + 2 * nh] = lon + (v[0] * k) / Math.cos((lat * Math.PI) / 180);
      hist[base + 2 * nh + 1] = lat + v[1] * k;
      head[p] = nh;
      this.age[p] += dtFrames;
    }
    this.build();
  }

  private build() {
    const N = this.n;
    this.cur ^= 1;
    const { pos, col } = this.bufs[this.cur];
    const { hist, head } = this;
    for (let p = 0; p < N; p++) {
      const base = p * TRAIL * 2;
      const h = head[p];
      const life = this.life[p], age = this.age[p];
      const fade = this.alive[p] ? Math.max(0, Math.min(1, age / 12, (life - age) / 20)) : 0;
      for (let s = 0; s < TRAIL; s++) {
        // vertex s: oldest (s=0) .. newest (s=TRAIL-1)
        const a = (h + 1 + s) % TRAIL;
        const o = base + 2 * s;
        pos[o] = hist[base + 2 * a]; pos[o + 1] = hist[base + 2 * a + 1];
        const c = (p * TRAIL + s) * 4;
        ramp(this.speed[p], col, c);
        col[c + 3] = this.alpha * fade * (s / SEG) ** 1.4;
      }
    }
    this.version++;
  }

  layer(visible: boolean) {
    const { pos, col } = this.bufs[this.cur];
    return new PathLayer({
      id: this.id,
      visible,
      data: {
        length: this.n,
        startIndices: this.starts,
        attributes: {
          getPath: { value: pos, size: 2 },
          getColor: { value: col, size: 4, normalized: true },
        },
      } as any,
      _pathType: 'open',
      getWidth: this.width,
      widthUnits: 'pixels',
      capRounded: false,
      jointRounded: false,
      parameters: ON_SURFACE,
    });
  }
}

/** Uniform random point on a spherical cap (angular radius r deg) around lon0/lat0. */
function randomOnCap(lon0: number, lat0: number, rDeg: number): [number, number] {
  const D = Math.PI / 180;
  const cosR = Math.cos(rDeg * D);
  const z = cosR + Math.random() * (1 - cosR); // cos of angular distance, uniform in area
  const d = Math.acos(z), az = Math.random() * 2 * Math.PI;
  const la0 = lat0 * D;
  const lat = Math.asin(Math.sin(la0) * Math.cos(d) + Math.cos(la0) * Math.sin(d) * Math.cos(az));
  const lon = lon0 * D + Math.atan2(Math.sin(az) * Math.sin(d) * Math.cos(la0), Math.cos(d) - Math.sin(la0) * Math.sin(lat));
  return [((lon / D + 540) % 360) - 180, lat / D];
}
