// Global surface currents (NOAA RTOFS live / HYCOM global hindcasts) from PNG u/v textures:
//   GET /api/currents?layer=global&start&end  -> {times, vmax, png: "/api/currents/global/{t}.png", gulf_box}
//   GET /api/currents/global/{t}.png          -> RGBA, cell-centred global grid, rows north->south,
//                                                R=(u/vmax+1)*127.5, G=(v/vmax+1)*127.5, A=0 land.
// The Gulf HYCOM layer is drawn on top: sample() returns null inside the Gulf box while `maskGulf()` is true.

import type { CurrentsSource, BBox } from './api';

const MAX_W = 1440; // texture cap (0.25 deg); bigger PNGs are downscaled on decode
const MAX_H = 720;
const KEEP = 8; // decoded grids kept in memory (~4 MB each at 0.25 deg)

interface Tex { t: number; w: number; h: number; px: Uint8ClampedArray }

export interface GlobalCurrents extends CurrentsSource {
  times: number[];
  gulfBox: BBox;
  source: string;
}

const GULF: BBox = { minLon: -98, minLat: 18, maxLon: -80, maxLat: 31 };

async function decodePNG(url: string): Promise<Tex> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  const t = Number(r.headers.get('X-Field-Time')) || NaN;
  const bmp = await createImageBitmap(await r.blob(), { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
  const w = Math.min(MAX_W, bmp.width), h = Math.min(MAX_H, bmp.height);
  const cv = typeof OffscreenCanvas !== 'undefined' ? new OffscreenCanvas(w, h) : Object.assign(document.createElement('canvas'), { width: w, height: h });
  const g = (cv as any).getContext('2d', { willReadFrequently: true }) as CanvasRenderingContext2D;
  g.imageSmoothingEnabled = false;
  g.drawImage(bmp, 0, 0, w, h);
  bmp.close?.();
  return { t, w, h, px: g.getImageData(0, 0, w, h).data };
}

/** u/v (m/s) at lon/lat from a texture: bilinear on the cell-centred grid, wraps in longitude, null on land. */
function sampleTex(tx: Tex, vmax: number, lon: number, lat: number): [number, number] | null {
  const { w, h, px } = tx;
  const x = ((((lon + 180) / 360) * w - 0.5) % w + w) % w;
  const y = ((90 - lat) / 180) * h - 0.5; // image rows north->south
  if (y < 0 || y > h - 1) return null;
  const i0 = Math.floor(x), j0 = Math.floor(y);
  const i1 = (i0 + 1) % w, j1 = Math.min(h - 1, j0 + 1);
  const fx = x - i0, fy = y - j0;
  const k00 = (j0 * w + i0) * 4, k10 = (j0 * w + i1) * 4, k01 = (j1 * w + i0) * 4, k11 = (j1 * w + i1) * 4;
  if (!px[k00 + 3] || !px[k10 + 3] || !px[k01 + 3] || !px[k11 + 3]) return null; // land
  const s = vmax / 127.5;
  const u = (px[k00] * (1 - fx) + px[k10] * fx) * (1 - fy) + (px[k01] * (1 - fx) + px[k11] * fx) * fy;
  const v = (px[k00 + 1] * (1 - fx) + px[k10 + 1] * fx) * (1 - fy) + (px[k01 + 1] * (1 - fx) + px[k11 + 1] * fx) * fy;
  return [u * s - vmax, v * s - vmax];
}

/** Global currents for [startUnix, endUnix] (padded a day each side); null if the backend has none. */
export async function getGlobalCurrents(startUnix: number, endUnix: number, maskGulf: () => boolean): Promise<GlobalCurrents | null> {
  if (new URLSearchParams(location.search).has('mock')) return null;
  const iso = (t: number) => new Date(t * 1000).toISOString().slice(0, 19) + 'Z';
  let r: any;
  try {
    const res = await fetch(`/api/currents?layer=global&start=${iso(startUnix - 86400)}&end=${iso(endUnix + 86400)}`);
    if (!res.ok || !(res.headers.get('content-type') || '').includes('json')) return null;
    r = await res.json();
  } catch {
    return null;
  }
  const times: number[] = (r.times ?? []).map(Number).filter(Number.isFinite).sort((a: number, b: number) => a - b);
  if (!times.length) return null;
  const vmax = Number(r.vmax) || 2.5;
  const tmpl: string = r.png || '/api/currents/global/{t}.png';
  const gb = r.gulf_box ?? [GULF.minLon, GULF.minLat, GULF.maxLon, GULF.maxLat];
  const gulfBox: BBox = { minLon: gb[0], minLat: gb[1], maxLon: gb[2], maxLat: gb[3] };
  const texs = new Map<number, Tex | 'loading' | 'error'>();
  let frameT = NaN, ta: Tex | null = null, tb: Tex | null = null, f = 0;
  const evict = () => { // keep the KEEP loaded textures closest in time to the current frame
    const loaded = [...texs.keys()].filter((k) => typeof texs.get(k) === 'object');
    if (loaded.length <= KEEP) return;
    const ref = Number.isFinite(frameT) ? frameT : times[0];
    loaded.sort((x, y) => Math.abs(x - ref) - Math.abs(y - ref));
    for (const k of loaded.slice(KEEP)) texs.delete(k);
  };

  const load = (k: number) => {
    const t = times[k];
    if (t == null || texs.has(t)) return;
    texs.set(t, 'loading');
    decodePNG(tmpl.replace('{t}', String(t)))
      .then((tx) => { texs.set(t, { ...tx, t }); evict(); frameT = NaN; })
      .catch((e) => { console.warn('global current', t, e); texs.set(t, 'error'); });
  };
  const idxAt = (t: number) => {
    let lo = 0, hi = times.length - 1;
    if (t <= times[0]) return 0;
    if (t >= times[hi]) return hi;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (times[m] <= t) lo = m; else hi = m; }
    return lo;
  };
  let lastGood: Tex | null = null;
  const inGulf = (lon: number, lat: number) => {
    const L = ((lon + 540) % 360) - 180;
    return L >= gulfBox.minLon && L <= gulfBox.maxLon && lat >= gulfBox.minLat && lat <= gulfBox.maxLat;
  };
  // Per-frame cache of which textures to blend (sample() is called thousands of times per frame).
  const prep = (t: number) => {
    if (t === frameT) return;
    frameT = t;
    const k = idxAt(t);
    const a = texs.get(times[k]), b = k + 1 < times.length ? texs.get(times[k + 1]) : undefined;
    ta = typeof a === 'object' ? a : null;
    tb = ta && typeof b === 'object' ? b : null; // blend only between two loaded neighbours
    if (ta) lastGood = ta; else ta = lastGood; // keep showing the last field while the next one loads
    f = ta && tb && tb.t > ta.t ? Math.min(1, Math.max(0, (t - ta.t) / (tb.t - ta.t))) : 0;
  };

  return {
    mock: false,
    bbox: { minLon: -180, minLat: -80, maxLon: 180, maxLat: 89 },
    times,
    gulfBox,
    source: (r.windows ?? []).map((w: any) => w.source).join('; '),
    prefetch(t) { const k = idxAt(t); load(k); load(k + 1); load(k + 2); },
    sample(lon, lat, t) {
      if (maskGulf() && inGulf(lon, lat)) return null;
      prep(t);
      if (!ta) return null;
      const p = sampleTex(ta, vmax, lon, lat);
      if (!p || !tb || f === 0) return p;
      const q = sampleTex(tb, vmax, lon, lat);
      if (!q) return p;
      return [p[0] + (q[0] - p[0]) * f, p[1] + (q[1] - p[1]) * f];
    },
  };
}
