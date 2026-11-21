import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';
import maplibregl from 'maplibre-gl';
import { MapboxOverlay } from '@deck.gl/mapbox';
import { ScatterplotLayer } from '@deck.gl/layers';
import type { Layer } from '@deck.gl/core';
import {
  FORCE_MOCK, onMock, getCatalog, getHistoricalTracks, getFootprints, getCurrents,
  postSpill, pollSpill, getSpillTracks, toUnix, type CurrentsSource, type SpillJob,
} from './api';
import { FlowField, SPEED_RAMP } from './flow';
import { getGlobalCurrents, type GlobalCurrents } from './globalCurrents';
import { OilView, footprintLayer, budgetAt } from './oil';
import { MOCK_BBOX, isWater } from './geo';
import { ON_SURFACE, type CatalogSpill, type Tracks, type Footprint } from './types';

// ---------------------------------------------------------------- starfield
(() => {
  const c = document.createElement('canvas');
  c.id = 'stars';
  const dpr = Math.min(2, devicePixelRatio);
  c.width = innerWidth * dpr; c.height = innerHeight * dpr;
  c.style.width = '100%'; c.style.height = '100%';
  const g = c.getContext('2d')!;
  for (let i = 0; i < 700; i++) {
    const r = Math.random() ** 3 * 1.3 * dpr + 0.2;
    g.fillStyle = `rgba(${200 + Math.random() * 55},${210 + Math.random() * 45},255,${0.25 + Math.random() * 0.6})`;
    g.beginPath(); g.arc(Math.random() * c.width, Math.random() * c.height, r, 0, 7); g.fill();
  }
  document.body.prepend(c);
})();

// ---------------------------------------------------------------- map
const map = new maplibregl.Map({
  container: 'map',
  center: [-90, 24.5],
  zoom: 0.9,
  maxPitch: 70,
  attributionControl: { compact: true },
  canvasContextAttributes: { antialias: true },
  style: {
    version: 8,
    projection: { type: 'globe' },
    sky: {
      'sky-color': '#0b1a36',
      'horizon-color': '#2a63b8',
      'fog-color': '#0a1a33',
      'sky-horizon-blend': 0.6,
      'horizon-fog-blend': 0.6,
      'fog-ground-blend': 0.9,
      'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 1, 5, 1, 7, 0],
    },
    sources: {
      ocean: {
        type: 'raster',
        tiles: ['https://services.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}'],
        tileSize: 256,
        maxzoom: 10,
        attribution: 'Basemap © Esri, GEBCO, NOAA, Garmin, HERE',
      },
    },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': '#071222' } },
      { id: 'ocean', type: 'raster', source: 'ocean', paint: { 'raster-brightness-max': 0.42, 'raster-saturation': -0.4, 'raster-contrast': 0.2, 'raster-fade-duration': 200 } },
    ],
  } as any,
});

const overlay = new MapboxOverlay({
  interleaved: true,
  layers: [],
});
map.addControl(overlay as any);

// ---------------------------------------------------------------- state
type Mode = 'historical' | 'prediction';
interface Scene {
  t0: number; // unix s
  durationH: number;
  tracks: Tracks | null;
  oil: OilView | null;
  footprints: Footprint[];
  currents: CurrentsSource | null;
  /** global RTOFS/HYCOM layer drawn under the Gulf one; null = none for this window */
  global: GlobalCurrents | null;
  globalReq: boolean;
  loading: string | null;
}
const nowHour = () => Math.floor(Date.now() / 3600000) * 3600;
const emptyScene = (t0: number, durationH: number): Scene => ({ t0, durationH, tracks: null, oil: null, footprints: [], currents: null, global: null, globalReq: false, loading: null });

const SPEEDS = [0.25, 0.5, 1, 2, 4];
const state = {
  mode: 'prediction' as Mode,
  catalog: [] as CatalogSpill[],
  selected: null as CatalogSpill | null,
  hist: emptyScene(nowHour(), 72),
  pred: emptyScene(nowHour(), 72),
  job: null as (SpillJob & { status: string; progress: number; error?: string }) | null,
  jobToken: 0,
  tH: 0,
  playing: false,
  speedIdx: 2,
  showCurrents: true,
  mockParts: [] as string[],
};
const scene = () => (state.mode === 'historical' ? state.hist : state.pred);

let flow: FlowField | null = null;
let globalFlow: FlowField | null = null;
// The Gulf layer owns 18-31N 98-80W whenever it has real (non-mock) data; the global layer fills everything else.
const gulfReal = () => !!scene().currents && !scene().currents!.mock;

/** Load the global layer for a scene once (t0..t0+duration); safe to call repeatedly. */
function loadGlobal(sc: Scene, t0 = sc.t0, t1 = sc.t0 + sc.durationH * 3600) {
  if (sc.globalReq) return;
  sc.globalReq = true;
  getGlobalCurrents(t0, t1, gulfReal).then((g) => {
    sc.global = g;
    if (scene() === sc) { useGlobal(g); renderCard(); }
  });
}
function useGlobal(g: GlobalCurrents | null) {
  if (!g) return;
  if (!globalFlow) globalFlow = new FlowField(g, { id: 'currents-global', n: 10000, width: 1.5, alpha: 215 });
  else if (globalFlow.source !== g) globalFlow.setSource(g);
}

// ---------------------------------------------------------------- UI
const ui = document.getElementById('ui')!;
ui.innerHTML = `
  <div class="mode glass">
    <button data-mode="historical">Historical</button>
    <button data-mode="prediction">Prediction</button>
  </div>
  <div class="card glass" id="card"></div>
  <div class="toast glass" id="toast" style="opacity:0"></div>
  <div class="bar glass">
    <button class="play" id="play" title="Play / pause (space)"></button>
    <button class="speed" id="speed" title="Playback speed"></button>
    <input type="range" id="slider" min="0" max="1000" value="0" />
    <div class="clock"><b id="clock-date"></b><span id="clock-rel"></span></div>
  </div>`;
const $ = (id: string) => document.getElementById(id)!;
const card = $('card');
const slider = $('slider') as HTMLInputElement;
const ICON_PLAY = '<svg width="12" height="12" viewBox="0 0 12 12"><path d="M2.5 1.2v9.6L10.5 6z" fill="currentColor"/></svg>';
const ICON_PAUSE = '<svg width="12" height="12" viewBox="0 0 12 12"><rect x="2" y="1.5" width="3" height="9" rx="0.8" fill="currentColor"/><rect x="7" y="1.5" width="3" height="9" rx="0.8" fill="currentColor"/></svg>';

ui.querySelectorAll<HTMLButtonElement>('.mode button').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode as Mode)));
$('play').addEventListener('click', togglePlay);
$('speed').addEventListener('click', () => { state.speedIdx = (state.speedIdx + 1) % SPEEDS.length; renderBar(); });
slider.addEventListener('input', () => { state.tH = (Number(slider.value) / 1000) * scene().durationH; state.playing = false; renderBar(); });
addEventListener('keydown', (e) => {
  if (e.code === 'Space' && !(e.target instanceof HTMLInputElement && e.target.type === 'text')) { e.preventDefault(); togglePlay(); }
});

let toastTimer = 0;
function toast(msg: string, ms = 2500) {
  const t = $('toast');
  t.textContent = msg; t.style.opacity = '1';
  clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => (t.style.opacity = '0'), ms);
}

function togglePlay() {
  const s = scene();
  if (!state.playing && state.tH >= s.durationH - 1e-6) state.tH = 0;
  state.playing = !state.playing;
  renderBar();
}

const fmtDate = (t: number) => {
  const d = new Date(t * 1000);
  return d.toLocaleString('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }) + ' UTC';
};
const pct = (x: number) => `${Math.round(x * 100)}%`;
const esc = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

let lastClock = '';
function renderBar() {
  const s = scene();
  $('play').innerHTML = state.playing ? ICON_PAUSE : ICON_PLAY;
  $('speed').textContent = `${SPEEDS[state.speedIdx]}×`;
  slider.value = String(Math.round((state.tH / s.durationH) * 1000));
  const date = fmtDate(s.t0 + state.tH * 3600);
  const rel = s.durationH > 96 ? `Day ${Math.floor(state.tH / 24) + 1} · T+${Math.round(state.tH)} h` : `T+${state.tH.toFixed(1)} h of ${Math.round(s.durationH)} h`;
  const key = date + rel;
  if (key !== lastClock) { lastClock = key; $('clock-date').textContent = date; $('clock-rel').textContent = rel; }
}

function legendHTML() {
  const max = 1.8;
  const stops = SPEED_RAMP.map(([v, r, g, b]) => `rgb(${r},${g},${b}) ${(v / max) * 100}%`).join(',');
  return `<div class="sec legend">
    <div class="row"><div class="label" style="margin:0">Surface current</div>
      <label class="toggle"><input type="checkbox" id="cur-toggle" ${state.showCurrents ? 'checked' : ''}/> show</label></div>
    <div class="ramp" style="margin-top:6px;background:linear-gradient(90deg,${stops})"></div>
    <div class="ticks"><span>0</span><span>0.5</span><span>1.0</span><span>1.5+ m/s</span></div>
    ${currentSourceHTML()}
  </div>`;
}

function currentSourceHTML() {
  const s = scene();
  const parts: string[] = [];
  if (s.currents && !s.currents.mock) parts.push('Gulf: HYCOM');
  if (s.global) parts.push(/RTOFS/.test(s.global.source) ? 'Global: NOAA RTOFS' : 'Global: HYCOM GOFS 3.1');
  return parts.length ? `<div class="hint" style="margin-top:4px">${esc(parts.join(' · '))}</div>` : '';
}

function budgetHTML() {
  const s = scene();
  if (!s.tracks || !s.tracks.budget.length) return '';
  const b = budgetAt(s.tracks.budget, state.tH - (s.tracks.t0 - s.t0) / 3600);
  if (!b) return '';
  const rows: [string, number, string][] = [
    ['Surface', b.surface, '#ffb547'], ['Evaporated', b.evaporated, '#8fb8ff'],
    ['Dispersed', b.dispersed, '#62d5c0'], ['Stranded', b.stranded, '#ff5a64'],
  ];
  return `<div class="sec"><div class="label">Oil budget</div><div class="budget">${rows
    .map(([k, v, c]) => `<span>${k}</span><div class="bbar"><div style="width:${pct(v)};background:${c}"></div></div><span class="pct">${pct(v)}</span>`)
    .join('')}</div></div>`;
}

function mockBadgeHTML() {
  const parts = state.mockParts.filter((p) => !(p === 'currents' && scene().global));
  if (!parts.length && !FORCE_MOCK) return '';
  const what = FORCE_MOCK ? 'forced by ?mock=1' : `not from backend: ${parts.join(', ')}`;
  return `<div class="badge" title="Synthetic client-side data (backend unavailable or has no data for this window). Not a real forecast.">MOCK DATA <small>· ${esc(what)}</small></div>`;
}

function renderCard() {
  const s = scene();
  let html = '';
  if (state.mode === 'historical') {
    const sel = state.selected;
    html += `<h2>${sel ? esc(sel.name) : 'Historical spills'}</h2>`;
    if (sel) {
      const year = sel.date_start.slice(0, 4);
      const srcs = [s.footprints.length ? 'observed outlines' : '', s.tracks ? 'hindcast particles' : ''].filter(Boolean).join(' + ');
      html += `<div class="sub">${esc(year)}${srcs ? ' · ' + srcs : ''}${s.loading ? esc(s.loading) : ''}</div>
      <div class="sec kv">
        <span>Start</span><span>${esc(sel.date_start.slice(0, 10))}</span>
        ${sel.date_end ? `<span>End</span><span>${esc(sel.date_end.slice(0, 10))}</span>` : ''}
        ${sel.volume_bbl ? `<span>Volume</span><span>${Number(sel.volume_bbl).toLocaleString()} bbl</span>` : ''}
        ${sel.oil_type ? `<span>Oil</span><span>${esc(sel.oil_type)}</span>` : ''}
        <span>Position</span><span>${sel.lat.toFixed(3)}°, ${sel.lon.toFixed(3)}°</span>
      </div>`;
      if (sel.source_urls?.length) html += `<div class="links" style="margin-top:6px">${sel.source_urls.map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u.replace(/^https?:\/\//, ''))}</a>`).join('<br>')}</div>`;
    } else {
      html += `<div class="sub">Pick a spill on the globe or below to replay it.</div>`;
    }
    html += `<div class="sec"><div class="spills">${state.catalog
      .map((c) => `<button data-id="${esc(c.id)}" class="${sel?.id === c.id ? 'on' : ''}"><span>${esc(c.name)}</span><small>${esc(c.date_start.slice(0, 4))}</small></button>`)
      .join('')}</div></div>`;
  } else {
    const j = state.job;
    html += `<h2>Spill prediction</h2>`;
    if (!j) {
      html += `<div class="hint">Click anywhere on the ocean to release a spill and watch it spread over the next 72 hours.</div>`;
    } else {
      html += `<div class="sub">${j.lat.toFixed(3)}°, ${j.lon.toFixed(3)}° · 500 m³ IFO 380 · 72 h</div>`;
      if (j.status !== 'done') {
        html += `<div class="sec"><div class="row"><span class="hint">${j.status === 'error' ? 'Run failed' : j.status === 'queued' ? 'Queued…' : 'Simulating drift…'}</span><span class="hint">${pct(j.progress)}</span></div>
          <div class="progress"><div style="width:${pct(j.progress)}"></div></div>${j.error ? `<div class="err">${esc(j.error)}</div>` : ''}</div>`;
      } else {
        html += `<div class="sec kv"><span>Particles</span><span>${s.tracks?.particles.length ?? 0}</span><span>Start</span><span>${fmtDate(s.t0)}</span></div>`;
      }
      html += `<div class="hint" style="margin-top:8px">Click elsewhere on the ocean for a new spill.</div>`;
    }
  }
  html += budgetHTML();
  html += legendHTML();
  html += mockBadgeHTML();
  card.innerHTML = html;
  syncPins();
  card.querySelectorAll<HTMLButtonElement>('.spills button').forEach((b) =>
    b.addEventListener('click', () => { const c = state.catalog.find((x) => x.id === b.dataset.id); if (c) selectSpill(c); }),
  );
  ($('cur-toggle') as HTMLInputElement).addEventListener('change', (e) => { state.showCurrents = (e.target as HTMLInputElement).checked; });
}

// The budget panel changes with time; re-render the card at a modest rate while playing.
let lastCardRender = 0;
function maybeRenderCard(now: number) {
  if (now - lastCardRender > 250 && scene().tracks?.budget.length) { lastCardRender = now; renderCard(); }
}

onMock((parts) => { state.mockParts = parts; renderCard(); });

// ---------------------------------------------------------------- modes / data
async function setMode(m: Mode) {
  state.mode = m;
  ui.querySelectorAll<HTMLButtonElement>('.mode button').forEach((b) => b.classList.toggle('on', b.dataset.mode === m));
  state.playing = false;
  state.tH = 0;
  map.getCanvas().style.cursor = m === 'prediction' ? 'crosshair' : '';
  const s = scene();
  loadGlobal(s);
  if (!s.currents) {
    s.currents = await getCurrents(s.t0, s.t0 + s.durationH * 3600);
  }
  if (state.mode === m) {
    useCurrents(s.currents);
    useGlobal(s.global);
    if (s.tracks) { state.tH = 0; state.playing = true; }
  }
  renderCard(); renderBar();
}

function useCurrents(c: CurrentsSource) {
  if (!flow) flow = new FlowField(c);
  else if (flow.source !== c) flow.setSource(c);
}

async function selectSpill(s: CatalogSpill) {
  if (state.selected?.id === s.id && state.hist.loading == null) {
    map.flyTo({ center: [s.lon, s.lat], zoom: 6.3, pitch: 25, duration: 2000, essential: true });
    return;
  }
  state.selected = s;
  const t0 = toUnix(s.date_start);
  const t1 = s.date_end ? toUnix(s.date_end) : t0 + 72 * 3600;
  const sc = emptyScene(t0, Math.max(1, (t1 - t0) / 3600));
  sc.loading = ' · loading…';
  state.hist = sc;
  state.playing = false; state.tH = 0;
  renderCard(); renderBar();
  map.flyTo({ center: [s.lon, s.lat], zoom: 6.3, pitch: 25, duration: 3200, essential: true, curve: 1.6 });
  const stale = () => state.selected !== s || state.hist !== sc;
  const active = () => state.mode === 'historical' && !stale();

  // Timeline = union of observed outlines and hindcast; keep the absolute clock when it changes.
  const applyTimeline = () => {
    let a = Infinity, b = -Infinity;
    if (sc.tracks) { a = sc.tracks.t0; b = sc.tracks.t0 + sc.tracks.durationH * 3600; }
    for (const f of sc.footprints) { a = Math.min(a, f.t); b = Math.max(b, f.t); }
    if (!Number.isFinite(a)) return;
    const abs = sc.t0 + state.tH * 3600;
    sc.t0 = a; sc.durationH = Math.max(1, (b - a) / 3600);
    if (active()) state.tH = Math.min(sc.durationH, Math.max(0, (abs - sc.t0) / 3600));
  };
  const startPlayback = () => { if (active() && !state.playing) { state.tH = 0; state.playing = true; } };

  loadGlobal(sc, t0, t1);
  const pCurrents = getCurrents(t0, t1).then((c) => {
    if (stale()) return;
    sc.currents = c;
    if (active()) useCurrents(c);
  });
  const pFootprints = getFootprints(s).then((fp) => {
    if (stale()) return;
    sc.footprints = fp;
    applyTimeline();
    if (fp.length) startPlayback();
    renderCard(); renderBar();
  });
  const onProgress = (p: number) => {
    if (stale()) return;
    sc.loading = ` · computing hindcast ${Math.round((p > 1 ? p / 100 : p) * 100)}%`;
    renderCard();
  };
  const pTracks = getHistoricalTracks(s, onProgress, stale).then((tr) => {
    if (stale()) return;
    sc.tracks = tr;
    sc.oil = tr ? new OilView(tr) : null;
    applyTimeline();
    if (tr) startPlayback();
  });
  await Promise.all([pCurrents, pFootprints, pTracks]);
  if (stale()) return;
  sc.loading = sc.tracks || sc.footprints.length ? null : ' · no track data available';
  renderCard(); renderBar();
}

async function startSpill(lon: number, lat: number) {
  if (!isWater(lon, lat) && lon > MOCK_BBOX.minLon && lon < MOCK_BBOX.maxLon && lat > MOCK_BBOX.minLat && lat < MOCK_BBOX.maxLat) {
    toast('That looks like land. Click on the ocean.');
    return;
  }
  const token = ++state.jobToken;
  if (map.getZoom() < 5) map.flyTo({ center: [lon, lat], zoom: 6.5, duration: 2500, essential: true });
  state.playing = false; state.tH = 0;
  state.pred = emptyScene(nowHour(), 72);
  state.job = { id: '', mock: false, lat, lon, status: 'queued', progress: 0 };
  renderCard(); renderBar();
  try {
    const job = await postSpill(lat, lon);
    if (token !== state.jobToken) return;
    state.job = { ...job, status: 'queued', progress: 0 };
    for (;;) {
      const st = await pollSpill(job);
      if (token !== state.jobToken) return;
      Object.assign(state.job, st);
      renderCard();
      if (st.status === 'done') break;
      if (st.status === 'error') return;
      await new Promise((r) => setTimeout(r, job.mock ? 120 : 700));
    }
    const tracks = await getSpillTracks(job);
    if (token !== state.jobToken) return;
    const sc = state.pred;
    sc.t0 = tracks.t0; sc.durationH = tracks.durationH;
    sc.tracks = tracks; sc.oil = new OilView(tracks);
    loadGlobal(sc);
    sc.currents = await getCurrents(sc.t0, sc.t0 + sc.durationH * 3600);
    if (token !== state.jobToken) return;
    if (state.mode === 'prediction') { useCurrents(sc.currents); useGlobal(sc.global); state.tH = 0; state.playing = true; }
    renderCard(); renderBar();
  } catch (e) {
    if (token !== state.jobToken || !state.job) return;
    state.job.status = 'error';
    state.job.error = String((e as Error).message ?? e);
    renderCard();
  }
}

map.on('click', (e) => {
  if (state.mode !== 'prediction') return;
  startSpill(e.lngLat.lng, e.lngLat.lat);
});

// ---------------------------------------------------------------- render loop
let view = { ...MOCK_BBOX };
function updateView() {
  const b = map.getBounds();
  const z = map.getZoom();
  view = z < 3
    ? { minLon: -180, minLat: -85, maxLon: 180, maxLat: 85 }
    : { minLon: b.getWest(), minLat: b.getSouth(), maxLon: b.getEast(), maxLat: b.getNorth() };
}
map.on('moveend', updateView);
map.on('zoom', () => document.body.classList.toggle('lowzoom', map.getZoom() < 5.5));
map.on('move', () => { if (map.getZoom() < 3.2) updateView(); });

// Pins are HTML markers: crisp labels, native click handling, and MapLibre hides them behind the globe.
const pinMarkers = new Map<string, { m: maplibregl.Marker; el: HTMLElement }>();
function syncPins() {
  const show = state.mode === 'historical';
  for (const c of state.catalog) {
    let pm = pinMarkers.get(c.id);
    if (!pm) {
      const el = document.createElement('div');
      el.className = 'pin';
      el.innerHTML = `<i></i><span>${esc(c.name)}</span>`;
      el.title = `${c.name} (${c.date_start.slice(0, 4)})`;
      el.addEventListener('click', (e) => { e.stopPropagation(); selectSpill(c); });
      const m = new maplibregl.Marker({ element: el, anchor: 'left', offset: [-7, 0] }).setLngLat([c.lon, c.lat]).addTo(map);
      pm = { m, el };
      pinMarkers.set(c.id, pm);
    }
    pm.el.style.display = show ? '' : 'none';
    pm.el.classList.toggle('on', state.selected?.id === c.id);
  }
}

function markerLayer(): Layer[] {
  if (state.mode !== 'prediction' || !state.job) return [];
  const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 300);
  const running = state.job.status !== 'done';
  return [
    new ScatterplotLayer({
      id: 'spill-marker',
      data: [state.job],
      getPosition: (d: any) => [d.lon, d.lat],
      getRadius: running ? 8 + 6 * pulse : 7,
      radiusUnits: 'pixels',
      filled: false,
      stroked: true,
      getLineColor: [255, 90, 100, running ? 255 - 150 * pulse : 220],
      lineWidthUnits: 'pixels',
      getLineWidth: 2,
      updateTriggers: { getRadius: pulse, getLineColor: pulse },
      parameters: ON_SURFACE,
    }),
  ];
}

let last = performance.now();
let lastTH = -1;
function frame(now: number) {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  const s = scene();
  if (state.playing) {
    state.tH += dt * (s.durationH / 30) * SPEEDS[state.speedIdx];
    if (state.tH >= s.durationH) { state.tH = s.durationH; state.playing = false; renderCard(); }
  }
  if (state.tH !== lastTH) { lastTH = state.tH; renderBar(); maybeRenderCard(now); }
  const tUnix = s.t0 + state.tH * 3600;
  const layers: Layer[] = [];
  // global layer first (underneath), Gulf on top; both stepped with the same clock
  if (globalFlow && s.global && globalFlow.source === s.global) {
    const c = map.getCenter();
    s.global.prefetch(tUnix);
    globalFlow.step(view, map.getZoom(), tUnix, dt * 60, [c.lng, c.lat]);
    layers.push(globalFlow.layer(state.showCurrents) as any);
  }
  // a mock Gulf field is hidden when real global data exists (the global layer then covers the Gulf too)
  if (flow && !(s.currents?.mock && s.global)) {
    s.currents?.prefetch(tUnix);
    // thin the dense Gulf streaks when zoomed out so they match the global layer's density
    const z = map.getZoom();
    flow.step(view, z, tUnix, dt * 60, undefined, s.global ? Math.min(1, Math.max(0.12, (z - 1.5) / 3.5)) : 1);
    layers.push(flow.layer(state.showCurrents) as any);
  }
  if (s.footprints.length) layers.push(footprintLayer(s.footprints, tUnix) as any);
  if (s.oil && s.tracks) {
    const oilH = state.tH - (s.tracks.t0 - s.t0) / 3600;
    s.oil.update(oilH);
    layers.push(...(s.oil.layers(s.footprints.length ? 0.45 : 1) as any[]));
  }
  layers.push(...markerLayer());
  overlay.setProps({ layers });
  requestAnimationFrame(frame);
}

map.on('load', async () => {
  updateView();
  // zoom so the whole globe floats in view (globe radius ~= 512 * 2^z / 2pi px)
  const fitZoom = Math.log2((0.47 * Math.min(innerWidth, innerHeight) * 2 * Math.PI) / 512);
  map.easeTo({ zoom: Math.max(1.6, fitZoom), duration: 2600, easing: (t) => 1 - Math.pow(1 - t, 3) });
  state.catalog = await getCatalog();
  const initial = new URLSearchParams(location.search).get('mode') === 'prediction' ? 'prediction' : 'historical';
  await setMode(initial);
  requestAnimationFrame(frame);
});
renderBar();
// debugging handle for the console / tests
(window as any).__oilflood = { map, state, overlay, get flow() { return flow; } };
