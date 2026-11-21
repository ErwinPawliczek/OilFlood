import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';
import maplibregl from 'maplibre-gl';
import { MapboxOverlay } from '@deck.gl/mapbox';
import { ScatterplotLayer, TextLayer } from '@deck.gl/layers';
import type { Layer } from '@deck.gl/core';
import {
  FORCE_MOCK, onMock, getCatalog, getHistoricalTracks, getFootprints, getCurrents,
  postSpill, pollSpill, getSpillTracks, toUnix, type CurrentsSource, type SpillJob,
} from './api';
import { FlowField, SPEED_RAMP } from './flow';
import { OilView, footprintLayer, budgetAt } from './oil';
import { MOCK_BBOX, isWater } from './geo';
import type { CatalogSpill, Tracks, Footprint } from './types';

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
  zoom: 1.4,
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
      { id: 'ocean', type: 'raster', source: 'ocean', paint: { 'raster-brightness-max': 0.5, 'raster-saturation': -0.35, 'raster-contrast': 0.15, 'raster-fade-duration': 200 } },
    ],
  } as any,
});

const overlay = new MapboxOverlay({
  interleaved: true,
  layers: [],
  onClick: (info: any) => {
    if (state.mode === 'historical' && info.layer?.id === 'pins' && info.object) selectSpill(info.object);
  },
  onHover: (info: any) => {
    const pin = state.mode === 'historical' && info.layer?.id === 'pins' && info.object;
    map.getCanvas().style.cursor = pin ? 'pointer' : state.mode === 'prediction' ? 'crosshair' : '';
  },
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
  loading: string | null;
}
const nowHour = () => Math.floor(Date.now() / 3600000) * 3600;
const emptyScene = (t0: number, durationH: number): Scene => ({ t0, durationH, tracks: null, oil: null, footprints: [], currents: null, loading: null });

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
  </div>`;
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
    .map(([k, v, c]) => `<span>${k}</span><div class="bar"><div style="width:${pct(v)};background:${c}"></div></div><span class="pct">${pct(v)}</span>`)
    .join('')}</div></div>`;
}

function mockBadgeHTML() {
  if (!state.mockParts.length && !FORCE_MOCK) return '';
  const what = FORCE_MOCK ? 'forced by ?mock=1' : `backend offline: ${state.mockParts.join(', ')}`;
  return `<div class="badge" title="Synthetic client-side data for demo purposes; not a real forecast.">MOCK DATA <small>· ${esc(what)}</small></div>`;
}

function renderCard() {
  const s = scene();
  let html = '';
  if (state.mode === 'historical') {
    const sel = state.selected;
    html += `<h2>${sel ? esc(sel.name) : 'Historical spills'}</h2>`;
    if (sel) {
      const year = sel.date_start.slice(0, 4);
      html += `<div class="sub">${esc(year)} · ${s.footprints.length ? 'observed outlines' : s.tracks ? 'hindcast particle tracks' : ''}${s.loading ? esc(s.loading) : ''}</div>
      <div class="sec kv">
        <span>Start</span><span>${esc(sel.date_start)}</span>
        ${sel.date_end ? `<span>End</span><span>${esc(sel.date_end)}</span>` : ''}
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
  if (!s.currents) {
    s.currents = await getCurrents(s.t0, s.t0 + s.durationH * 3600);
  }
  if (state.mode === m) {
    useCurrents(s.currents);
    if (s.tracks) { state.tH = 0; state.playing = true; }
  }
  renderCard(); renderBar();
}

function useCurrents(c: CurrentsSource) {
  if (!flow) flow = new FlowField(c);
  else if (flow.source !== c) flow.setSource(c);
}

async function selectSpill(s: CatalogSpill) {
  state.selected = s;
  const sc = emptyScene(toUnix(s.date_start), 72);
  sc.loading = ' · loading…';
  state.hist = sc;
  state.playing = false; state.tH = 0;
  renderCard(); renderBar();
  map.flyTo({ center: [s.lon, s.lat], zoom: 6.3, pitch: 25, duration: 3200, essential: true, curve: 1.6 });
  const [tracks, footprints] = await Promise.all([getHistoricalTracks(s), getFootprints(s)]);
  if (state.selected !== s) return;
  let t0 = Infinity, t1 = -Infinity;
  if (tracks) { t0 = tracks.t0; t1 = tracks.t0 + tracks.durationH * 3600; }
  for (const f of footprints) { t0 = Math.min(t0, f.t); t1 = Math.max(t1, f.t); }
  if (!Number.isFinite(t0)) { t0 = toUnix(s.date_start); t1 = s.date_end ? toUnix(s.date_end) : t0 + 72 * 3600; }
  sc.t0 = t0; sc.durationH = Math.max(1, (t1 - t0) / 3600);
  sc.tracks = tracks;
  sc.oil = tracks ? new OilView(tracks) : null;
  sc.footprints = footprints;
  sc.currents = await getCurrents(sc.t0, sc.t0 + sc.durationH * 3600);
  if (state.selected !== s) return;
  sc.loading = tracks || footprints.length ? null : ' · no track data available';
  if (state.mode === 'historical') { useCurrents(sc.currents); state.tH = 0; state.playing = !!(tracks || footprints.length); }
  renderCard(); renderBar();
}

async function startSpill(lon: number, lat: number) {
  if (!isWater(lon, lat) && lon > MOCK_BBOX.minLon && lon < MOCK_BBOX.maxLon && lat > MOCK_BBOX.minLat && lat < MOCK_BBOX.maxLat) {
    toast('That looks like land. Click on the ocean.');
    return;
  }
  const token = ++state.jobToken;
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
    sc.currents = await getCurrents(sc.t0, sc.t0 + sc.durationH * 3600);
    if (token !== state.jobToken) return;
    if (state.mode === 'prediction') { useCurrents(sc.currents); state.tH = 0; state.playing = true; }
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
map.on('move', () => { if (map.getZoom() < 3.2) updateView(); });

function pinLayers(): Layer[] {
  if (state.mode !== 'historical') return [];
  const sel = state.selected?.id;
  return [
    new ScatterplotLayer({
      id: 'pins',
      data: state.catalog,
      getPosition: (d: CatalogSpill) => [d.lon, d.lat],
      getRadius: (d: CatalogSpill) => (d.id === sel ? 8 : 6),
      radiusUnits: 'pixels',
      getFillColor: (d: CatalogSpill) => (d.id === sel ? [255, 181, 71, 255] : [255, 181, 71, 170]),
      getLineColor: [255, 255, 255, 220],
      lineWidthUnits: 'pixels',
      getLineWidth: 1.5,
      stroked: true,
      pickable: true,
      autoHighlight: true,
      highlightColor: [255, 230, 160, 255],
      updateTriggers: { getRadius: sel, getFillColor: sel },
      parameters: { depthTest: false } as any,
    }),
    new TextLayer({
      id: 'pin-labels',
      data: state.catalog,
      getPosition: (d: CatalogSpill) => [d.lon, d.lat],
      getText: (d: CatalogSpill) => d.name,
      getSize: 12,
      getColor: (d: CatalogSpill) => (d.id === sel ? [255, 210, 140, 255] : [220, 230, 245, 200]),
      getPixelOffset: [10, 0],
      getTextAnchor: 'start',
      getAlignmentBaseline: 'center',
      fontFamily: 'ui-sans-serif, system-ui, sans-serif',
      fontWeight: 500,
      outlineWidth: 3,
      outlineColor: [5, 10, 20, 220],
      fontSettings: { sdf: true },
      updateTriggers: { getColor: sel },
      parameters: { depthTest: false } as any,
    }),
  ];
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
      parameters: { depthTest: false } as any,
    }),
  ];
}

let last = performance.now();
function frame(now: number) {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  const s = scene();
  if (state.playing) {
    state.tH += dt * (s.durationH / 30) * SPEEDS[state.speedIdx];
    if (state.tH >= s.durationH) { state.tH = s.durationH; state.playing = false; renderCard(); }
    renderBar();
    maybeRenderCard(now);
  }
  const tUnix = s.t0 + state.tH * 3600;
  const layers: Layer[] = [];
  if (flow) {
    s.currents?.prefetch(tUnix);
    flow.step(view, map.getZoom(), tUnix, dt * 60);
    layers.push(flow.layer(state.showCurrents) as any);
  }
  if (s.footprints.length) layers.push(footprintLayer(s.footprints, tUnix) as any);
  if (s.oil && s.tracks) {
    const oilH = state.tH - (s.tracks.t0 - s.t0) / 3600;
    s.oil.update(oilH);
    layers.push(...(s.oil.layers(oilH) as any[]));
  }
  layers.push(...pinLayers(), ...markerLayer());
  overlay.setProps({ layers });
  requestAnimationFrame(frame);
}

map.on('load', async () => {
  updateView();
  map.easeTo({ zoom: 3.4, duration: 2600, easing: (t) => 1 - Math.pow(1 - t, 3) });
  state.catalog = await getCatalog();
  const initial = new URLSearchParams(location.search).get('mode') === 'prediction' ? 'prediction' : 'historical';
  await setMode(initial);
  requestAnimationFrame(frame);
});
renderBar();
(window as any).__oilflood = { map, state };
