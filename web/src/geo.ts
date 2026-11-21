// Coarse Gulf of Mexico water mask, used only by mock mode (stranding + where to draw currents).
// Hand-drawn polygon around the Gulf, Florida Straits and NW Caribbean; Cuba is carved out.

const WATER: [number, number][] = [
  [-80.0, 25.0], [-80.4, 25.2], [-81.1, 25.2], [-81.8, 26.1], [-82.2, 26.7], [-82.8, 27.8],
  [-82.7, 28.7], [-83.2, 29.2], [-83.6, 29.9], [-84.3, 30.0], [-85.0, 29.6], [-85.4, 29.7],
  [-86.5, 30.35], [-87.6, 30.25], [-88.5, 30.35], [-89.4, 30.15], [-89.65, 29.9], [-89.55, 29.6],
  [-89.4, 29.45], [-89.2, 29.35], [-89.03, 29.2], [-89.14, 28.99], [-89.42, 28.93], [-89.6, 29.15], [-89.9, 29.3], [-90.6, 29.1], [-91.3, 29.25], [-92.3, 29.55],
  [-93.3, 29.75], [-93.9, 29.68], [-94.7, 29.35], [-95.3, 28.9], [-96.4, 28.3], [-97.0, 27.8],
  [-97.3, 27.0], [-97.2, 26.0], [-97.4, 25.0], [-97.7, 24.0], [-97.85, 22.2], [-97.3, 21.0],
  [-96.3, 19.3], [-95.2, 18.7], [-94.4, 18.15], [-93.4, 18.4], [-92.5, 18.65], [-91.8, 18.65],
  [-91.0, 19.0], [-90.7, 19.4], [-90.4, 20.0], [-90.4, 20.9], [-90.0, 21.25], [-89.0, 21.35],
  [-88.0, 21.55], [-87.1, 21.55], [-86.8, 21.1], [-87.4, 20.3], [-87.6, 19.5], [-87.8, 18.5],
  [-88.25, 17.5], [-80.0, 17.5], [-80.0, 21.6], [-81.0, 21.8], [-82.5, 22.0], [-83.5, 21.9],
  [-84.5, 21.8], [-84.95, 21.85], [-84.5, 22.5], [-84.0, 22.8], [-83.0, 23.0], [-82.4, 23.15],
  [-81.5, 23.15], [-80.5, 23.0], [-80.0, 22.95],
];

function pip(lon: number, lat: number, poly: [number, number][]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export const MOCK_BBOX = { minLon: -98, minLat: 17.5, maxLon: -80, maxLat: 31 };
const RES = 0.04;
const NX = Math.round((MOCK_BBOX.maxLon - MOCK_BBOX.minLon) / RES);
const NY = Math.round((MOCK_BBOX.maxLat - MOCK_BBOX.minLat) / RES);
let mask: Uint8Array | null = null;

function buildMask(): Uint8Array {
  const m = new Uint8Array(NX * NY);
  for (let j = 0; j < NY; j++) {
    const lat = MOCK_BBOX.minLat + (j + 0.5) * RES;
    for (let i = 0; i < NX; i++) {
      m[j * NX + i] = pip(MOCK_BBOX.minLon + (i + 0.5) * RES, lat, WATER) ? 1 : 0;
    }
  }
  return m;
}

/** True if (lon,lat) is water inside the mock Gulf domain. */
export function isWater(lon: number, lat: number): boolean {
  const i = Math.floor((lon - MOCK_BBOX.minLon) / RES);
  const j = Math.floor((lat - MOCK_BBOX.minLat) / RES);
  if (i < 0 || j < 0 || i >= NX || j >= NY) return false;
  if (!mask) mask = buildMask();
  return mask[j * NX + i] === 1;
}

/** Deterministic PRNG so mock runs are reproducible. */
export function rng(seed: number) {
  let a = seed >>> 0;
  const next = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const gauss = () => {
    const u = Math.max(1e-9, next());
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * next());
  };
  return { next, gauss };
}

export const M_PER_DEG = 111320;
