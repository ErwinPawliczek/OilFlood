// Oil as a slick (T27): the particle cloud of a run turned into merged black polygons per frame.
// Particles are interpolated to the clock (same as the particle view), splatted on a fixed lon/lat lattice, blurred
// with a Gaussian kernel and contoured (marching squares, d3-contour) at three density levels:
//   sheen edge (lighter grey)  ->  slick (#1a1a1a)  ->  thick core (#0b0b0b)
// Holes and several blobs come out of the contouring for free. Stranded oil is a black shoreline band.
// Cost: O(particles + grid cells) per new clock value (grid <= ~260 x 260); results are cached per 0.05 h so
// replays and scrubbing back are free.

import { contours } from 'd3-contour';
import { GeoJsonLayer, ScatterplotLayer } from '@deck.gl/layers';
import { PathStyleExtension } from '@deck.gl/extensions';
import type { Layer } from '@deck.gl/core';
import { ON_SURFACE, type Tracks, type Particle } from './types';

export type SlickStyle = 'fill' | 'outline';
type RGBA = [number, number, number, number];

/** fill colours per level (outer -> inner) and the outline colour */
const FILL: RGBA[] = [[60, 60, 60, 150], [28, 28, 28, 230], [10, 10, 10, 252]];
const EDGE: RGBA = [92, 92, 92, 230];
const OUTLINE_ONLY: RGBA = [205, 205, 205, 235];
/** outer edge in multiples of one isolated particle's peak (a lone particle stays invisible); the inner levels are
 *  shares of the current peak density, so every slick shows sheen -> slick -> thick core */
const EDGE_LEVEL = 1.15;
const INNER = [0.12, 0.45];
const MAX_CELLS = 260;
const dash = new PathStyleExtension({ dash: true });

interface Frame { polys: any; stranded: Float32Array; ns: number; centroid: [number, number] | null }

/** Our model keeps simulating particles after they weathered away (no more path points) and can still flag them
 *  "stranded" later; their path ends early. Those never reached the shore as visible oil: mark them evaporated.
 *  Visibly stranded particles of our model keep recording at the beach to the end of the run, so their stranding
 *  time is the start of the final motionless stretch. OpenOil paths end at stranding. Returns per-particle strand
 *  hour (Infinity = never). */
export function strandTimes(tracks: Tracks, model: string): Float32Array {
  const out = new Float32Array(tracks.particles.length).fill(Infinity);
  tracks.particles.forEach((p, i) => {
    if (p.status !== 'stranded') return;
    const last = p.ts.length - 1;
    if (model !== 'ours') { out[i] = p.ts[last]; return; }
    if (p.ts[last] < tracks.durationH - 0.75) { p.status = 'evaporated'; return; }
    let k = last;
    const [x, y] = p.path[last];
    while (k > 0 && p.path[k - 1][0] === x && p.path[k - 1][1] === y) k--;
    out[i] = p.ts[k];
  });
  return out;
}

function at(p: Particle, t: number): [number, number] {
  const ts = p.ts, last = ts.length - 1;
  if (t <= ts[0] || last === 0) return p.path[0];
  if (t >= ts[last]) return p.path[last];
  let lo = 0, hi = last;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (ts[m] <= t) lo = m; else hi = m; }
  const f = (t - ts[lo]) / (ts[hi] - ts[lo] || 1);
  const a = p.path[lo], b = p.path[hi];
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
}

function blur1D(src: Float32Array, dst: Float32Array, nx: number, ny: number, k: Float32Array, horiz: boolean) {
  const r = (k.length - 1) >> 1;
  if (horiz) {
    for (let y = 0; y < ny; y++) {
      const o = y * nx;
      for (let x = 0; x < nx; x++) {
        let s = 0;
        for (let i = -r; i <= r; i++) { const xx = x + i; if (xx >= 0 && xx < nx) s += src[o + xx] * k[i + r]; }
        dst[o + x] = s;
      }
    }
  } else {
    for (let y = 0; y < ny; y++) {
      for (let x = 0; x < nx; x++) {
        let s = 0;
        for (let i = -r; i <= r; i++) { const yy = y + i; if (yy >= 0 && yy < ny) s += src[yy * nx + x] * k[i + r]; }
        dst[y * nx + x] = s;
      }
    }
  }
}

export class SlickView {
  private dLat: number;
  private dLon: number;
  private sigma = 1.6; // kernel sigma in cells
  private kernel: Float32Array;
  private cache = new Map<number, Frame>();
  private cur: Frame | null = null;
  private curKey = NaN;
  private strandT: Float32Array;

  constructor(public tracks: Tracks, public id: string, public style: SlickStyle = 'fill') {
    // fixed lattice for the whole run (no jitter while the slick moves): cell ~ 1/150 of the run's extent, 0.3-3 km
    this.strandT = strandTimes(tracks, id);
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const p of tracks.particles) for (const q of p.path) {
      if (q[0] < x0) x0 = q[0]; if (q[0] > x1) x1 = q[0]; if (q[1] < y0) y0 = q[1]; if (q[1] > y1) y1 = q[1];
    }
    const latM = Number.isFinite(y0) ? (y0 + y1) / 2 : 27;
    const cos = Math.cos((latM * Math.PI) / 180);
    const extKm = Number.isFinite(x0) ? Math.max((x1 - x0) * 111.32 * cos, (y1 - y0) * 110.57) : 50;
    const cellKm = Math.min(3, Math.max(0.3, extKm / 150));
    this.dLat = cellKm / 110.57;
    this.dLon = cellKm / (111.32 * cos);
    const r = Math.ceil(this.sigma * 3);
    this.kernel = Float32Array.from({ length: 2 * r + 1 }, (_, i) => Math.exp(-((i - r) ** 2) / (2 * this.sigma ** 2)));
    const s = this.kernel.reduce((a, b) => a + b, 0);
    for (let i = 0; i < this.kernel.length; i++) this.kernel[i] /= s;
  }

  /** Recompute the slick at hour tH (since this run's t0), cached per 0.05 h. */
  update(tH: number) {
    const key = Math.round(tH * 20);
    if (key === this.curKey) return;
    this.curKey = key;
    const hit = this.cache.get(key);
    if (hit) { this.cur = hit; return; }
    const f = this.compute(key / 20);
    this.cache.set(key, f);
    if (this.cache.size > 1500) this.cache.delete(this.cache.keys().next().value!);
    this.cur = f;
  }

  private compute(tH: number): Frame {
    const P = this.tracks.particles;
    const surf: number[] = [];
    const strand: number[] = [];
    for (let i = 0; i < P.length; i++) {
      const p = P[i];
      const ts = p.ts, last = ts.length - 1;
      if (tH < ts[0]) continue;
      if (tH >= this.strandT[i]) { strand.push(p.path[last][0], p.path[last][1]); continue; }
      if (tH >= ts[last] && p.status !== 'active') continue; // evaporated / dispersed / left the domain
      const q = at(p, tH);
      surf.push(q[0], q[1]);
    }
    const n = surf.length / 2;
    const stranded = Float32Array.from(strand);
    if (!n) return { polys: { type: 'FeatureCollection', features: [] }, stranded, ns: strand.length / 2, centroid: null };
    // window = particle bbox + 4 sigma, snapped to the lattice; coarsen x2 if it would exceed MAX_CELLS
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity, cx = 0, cy = 0;
    for (let i = 0; i < surf.length; i += 2) {
      const x = surf[i], y = surf[i + 1];
      cx += x; cy += y;
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    let dLon = this.dLon, dLat = this.dLat, sc = 1;
    while (((x1 - x0) / dLon > MAX_CELLS - 30 || (y1 - y0) / dLat > MAX_CELLS - 30) && sc < 16) { dLon *= 2; dLat *= 2; sc *= 2; }
    const pad = Math.ceil(this.sigma * 4) + 1;
    const gx0 = (Math.floor(x0 / dLon) - pad) * dLon, gy0 = (Math.floor(y0 / dLat) - pad) * dLat;
    const nx = Math.ceil((x1 - gx0) / dLon) + pad + 1, ny = Math.ceil((y1 - gy0) / dLat) + pad + 1;
    const a = new Float32Array(nx * ny), b = new Float32Array(nx * ny);
    // bilinear splat (sub-cell positions -> smooth motion between frames)
    for (let i = 0; i < surf.length; i += 2) {
      const fx = (surf[i] - gx0) / dLon - 0.5, fy = (surf[i + 1] - gy0) / dLat - 0.5;
      const ix = Math.floor(fx), iy = Math.floor(fy), ux = fx - ix, uy = fy - iy;
      if (ix < 0 || iy < 0 || ix + 1 >= nx || iy + 1 >= ny) continue;
      const o = iy * nx + ix;
      a[o] += (1 - ux) * (1 - uy); a[o + 1] += ux * (1 - uy); a[o + nx] += (1 - ux) * uy; a[o + nx + 1] += ux * uy;
    }
    blur1D(a, b, nx, ny, this.kernel, true);
    blur1D(b, a, nx, ny, this.kernel, false);
    // a lone particle peaks at ~kernel[r]^2; levels are multiples of that (coarsened grids hold more per cell)
    const r = (this.kernel.length - 1) >> 1;
    const p1 = this.kernel[r] * this.kernel[r];
    let mx = 0;
    for (let i = 0; i < a.length; i++) if (a[i] > mx) mx = a[i];
    const l0 = EDGE_LEVEL * p1;
    const l1 = Math.max(l0 * 1.6, INNER[0] * mx), l2 = Math.max(l1 * 1.6, INNER[1] * mx);
    const thr = this.style === 'fill' ? [l0, l1, l2] : [l0];
    const cs = contours().size([nx, ny]).thresholds(thr)(a as unknown as number[]);
    const features = cs.filter((c) => c.coordinates.length).map((c, li) => ({
      type: 'Feature',
      properties: { level: li },
      geometry: {
        type: 'MultiPolygon',
        coordinates: c.coordinates.map((poly) => poly.map((ring) => ring.map(([x, y]) => [gx0 + x * dLon, gy0 + y * dLat]))),
      },
    }));
    return { polys: { type: 'FeatureCollection', features }, stranded, ns: strand.length / 2, centroid: [cx / n, cy / n] };
  }

  setStyle(st: SlickStyle) {
    if (st === this.style) return;
    this.style = st;
    this.cache.clear();
    this.curKey = NaN;
  }

  /** where to pin the model label (compare mode) */
  centroid() { return this.cur?.centroid ?? null; }

  layers(opacity = 1): Layer[] {
    const f = this.cur;
    if (!f) return [];
    const k = this.curKey;
    const out: Layer[] = [];
    if (this.style === 'fill') {
      out.push(new GeoJsonLayer({
        id: `slick-${this.id}`, data: f.polys, opacity, filled: true, stroked: true,
        getFillColor: (d: any) => FILL[d.properties.level] ?? FILL[2],
        getLineColor: (d: any) => (d.properties.level === 0 ? EDGE : [0, 0, 0, 0]),
        lineWidthUnits: 'pixels', getLineWidth: (d: any) => (d.properties.level === 0 ? 1.2 : 0),
        updateTriggers: { getFillColor: k, getLineColor: k, getLineWidth: k }, parameters: ON_SURFACE,
      }) as any);
    } else {
      out.push(new GeoJsonLayer({
        id: `slick-${this.id}`, data: f.polys, opacity, filled: true, stroked: true,
        getFillColor: [40, 40, 40, 70], getLineColor: OUTLINE_ONLY, lineWidthUnits: 'pixels', getLineWidth: 1.8,
        extensions: [dash], getDashArray: [5, 3], dashJustified: true, parameters: ON_SURFACE,
      } as any) as any);
    }
    if (f.ns) {
      out.push(new ScatterplotLayer({
        id: `slick-stranded-${this.id}`, opacity,
        data: { length: f.ns, attributes: { getPosition: { value: f.stranded, size: 2 } } } as any,
        radiusUnits: 'meters', getRadius: 350, radiusMinPixels: 2.6, radiusMaxPixels: 9,
        getFillColor: this.style === 'fill' ? [8, 8, 8, 255] : [40, 40, 40, 200],
        stroked: true, getLineColor: this.style === 'fill' ? [70, 70, 70, 255] : OUTLINE_ONLY, lineWidthUnits: 'pixels', getLineWidth: 0.6,
        parameters: ON_SURFACE,
      }) as any);
    }
    return out;
  }
}

/** Legend swatches for the slick (HTML). */
export function slickLegendHTML(compare: boolean, fillName: string, outlineName: string) {
  const sw = (bg: string, border = '#5c5c5c', dashBorder = false) =>
    `<i class="sw box" style="background:${bg};border:1px ${dashBorder ? 'dashed' : 'solid'} ${border}"></i>`;
  return `<div class="item">${sw('#0b0b0b')}${sw('#1a1a1a')}${sw('rgba(58,58,58,.7)')} Oil slick${compare ? ` (${fillName})` : ''}: darker = thicker (more oil per area), grey edge = thin sheen</div>`
    + (compare ? `<div class="item">${sw('rgba(40,40,40,.3)', '#cdcdcd', true)} ${outlineName} slick (outline only)</div>` : '')
    + `<div class="item"><i class="sw dot" style="background:#080808;border:1px solid #555"></i> Stranded oil on the shore</div>`;
}
