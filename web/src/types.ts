// Shapes used across the app. Wire formats follow docs/ARCHITECTURE.md (Step A).

export interface CatalogSpill {
  id: string;
  name: string;
  date_start: string; // ISO date
  date_end?: string;
  lat: number;
  lon: number;
  volume_bbl?: number | null;
  oil_type?: string | null;
  source_urls?: string[];
  has_observed_outlines?: boolean;
}

/** Wire format of GET /api/spill/{id}/tracks and /api/spills/{id}/tracks */
export interface TracksPayload {
  times: number[]; // unix seconds
  particles: {
    path: [number, number][];
    timestamps: number[]; // unix seconds
    status_end?: string; // active | stranded | evaporated | (dispersed)
  }[];
  budget?: BudgetRow[];
}

export interface BudgetRow {
  t: number; // unix seconds
  surface: number;
  evaporated: number;
  dispersed: number;
  stranded: number;
}

/** Internal track representation: times are hours relative to t0 (float32-safe). */
export interface Particle {
  path: [number, number][];
  ts: number[]; // hours since t0
  status: string;
}

export interface Tracks {
  t0: number; // unix seconds
  durationH: number;
  particles: Particle[];
  budget: BudgetRow[]; // t in hours since t0
  /** backend run metadata (forcing files, seed_moved_km, oil_type, ...) */
  meta?: Record<string, any>;
}

/** A single observed outline at time t (unix seconds). */
export interface Footprint {
  t: number;
  geojson: any; // Feature | FeatureCollection | Geometry
}

/** A regular lon/lat grid of surface currents in m/s. NaN = land / no data. */
export interface CurrentGrid {
  t: number; // unix seconds
  lon0: number;
  lat0: number;
  dlon: number;
  dlat: number;
  nx: number;
  ny: number;
  u: Float32Array; // row-major, ny rows (lat ascending) x nx cols
  v: Float32Array;
}

export type Sampler = (lon: number, lat: number) => [number, number] | null;

/** deck.gl draw parameters for layers lying on the globe surface: always pass the depth test
 *  (avoids z-fighting with the MapLibre globe in interleaved mode). */
export const ON_SURFACE: any = { depthCompare: 'always', depthWriteEnabled: false };
