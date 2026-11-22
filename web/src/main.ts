import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';
import maplibregl from 'maplibre-gl';
import { MapboxOverlay } from '@deck.gl/mapbox';
import { ScatterplotLayer } from '@deck.gl/layers';
import type { Layer } from '@deck.gl/core';
import {
  getCatalog, getHistoricalTracks, getFootprints, getCurrents, postSpill, pollSpill, getSpillTracks, getLive, getOils,
  getModels, toUnix, PRED, GULF_BBOX, MODELS, type CurrentsSource, type SpillJob, type LiveForecast, type OilOption,
  type RunSummary, type ModelId, type ModelInfo,
} from './api';
import { FlowField } from './flow';
import { getGlobalCurrents, type GlobalCurrents } from './globalCurrents';
import { OilView, footprintLayer, footprintAt, budgetAt, PALETTES } from './oil';
import { initPanel, legendHTML, RATES_H, autoRateIdx, rateLabel, responseHTML } from './panel';
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
// Demo default (T17): open on the whole globe, then fly to Houston/Galveston; prediction suggests a spill offshore Galveston.
const HOME = { center: [-94.0, 28.2] as [number, number], zoom: 6.0 };
const DEMO_SPILL = { lon: -94.55, lat: 29.05 }; // ~30 km SE of Galveston, open water in RTOFS and HYCOM
const map = new maplibregl.Map({
  container: 'map',
  center: [-93, 26],
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
  /** first model run that arrived (drives the timeline); all runs are in `runs` */
  tracks: Tracks | null;
  oil: OilView | null;
  runs: Partial<Record<ModelId, { tracks: Tracks; oil: OilView }>>;
  /** model runs requested for this scene (historical hindcasts) */
  runReq: Set<ModelId>;
  /** per-model progress / status text while a hindcast computes */
  runMsg: Partial<Record<ModelId, string>>;
  /** historical: start (once) the hindcast of model m for this scene's spill */
  loadRun?: (m: ModelId) => Promise<void>;
  footprints: Footprint[];
  /** real Gulf HYCOM currents for this window; null = none cached */
  currents: CurrentsSource | null;
  currentsReq: boolean;
  /** global RTOFS/HYCOM layer drawn under the Gulf one; null = none for this window */
  global: GlobalCurrents | null;
  globalReq: boolean;
  /** currents requests still in flight */
  pending: number;
  loading: string | null;
  /** user-facing problems (backend messages), e.g. "No real ocean currents data cached for ..." */
  errors: string[];
}
const nowHour = () => Math.floor(Date.now() / 3600000) * 3600;
const emptyScene = (t0: number, durationH: number): Scene => ({
  t0, durationH, tracks: null, oil: null, runs: {}, runReq: new Set(), runMsg: {}, footprints: [], currents: null, currentsReq: false,
  global: null, globalReq: false, pending: 0, loading: null, errors: [],
});

type ModelChoice = ModelId | 'compare';
const MODEL_KEY = 'oilflood.model.v1';
const savedModel = (() => { try { return localStorage.getItem(MODEL_KEY); } catch { return null; } })();

const state = {
  mode: 'prediction' as Mode,
  /** which trajectory model(s) to show: OpenOil baseline, ours, or both side by side */
  model: (['openoil', 'ours', 'compare'].includes(savedModel ?? '') ? savedModel : 'openoil') as ModelChoice,
  models: [] as ModelInfo[],
  /** prediction: per-model run status for the current click */
  jobRuns: {} as Partial<Record<ModelId, { id: string; status: string; progress: number; error?: string }>>,
  catalog: [] as CatalogSpill[],
  catalogError: null as string | null,
  selected: null as CatalogSpill | null,
  hist: emptyScene(nowHour(), 72),
  pred: emptyScene(nowHour(), PRED.durationH),
  job: null as (SpillJob & RunSummary & { status: string; progress: number; error?: string }) | null,
  /** GET /api/live: the forecast predictions run on */
  live: null as LiveForecast | null,
  liveError: null as string | null,
  oils: [] as OilOption[],
  jobToken: 0,
  tH: 0,
  playing: false,
  rateIdx: autoRateIdx(72),
  showCurrents: true,
};
const scene = () => (state.mode === 'historical' ? state.hist : state.pred);
const wanted = (): ModelId[] => (state.model === 'compare' ? [...MODELS] : [state.model]);
const modelShort = (m: ModelId) => (m === 'ours' ? 'Ours' : 'OpenOil');
/** Model runs of scene s that are currently shown, in a fixed order. */
const shownRuns = (s: Scene) => wanted().filter((m) => s.runs[m]).map((m) => ({ m, ...s.runs[m]! }));
/** Store a finished run in scene s; the first one also drives the timeline. */
function addRun(s: Scene, m: ModelId, tr: Tracks) {
  s.runs[m] = { tracks: tr, oil: new OilView(tr, m) };
  if (!s.tracks) { s.tracks = tr; s.oil = s.runs[m]!.oil; }
}
const simT = () => scene().t0 + state.tH * 3600;
const covers = (c: CurrentsSource | null, t: number) => !!c && t >= c.t0 - 6 * 3600 && t <= c.t1 + 6 * 3600;

// ---------------------------------------------------------------- current layers (Gulf HYCOM on top of global)
// Each layer is a FlowField (windy-style streaks) fed by a CurrentsSource; more layers can be added here.
let flow: FlowField | null = null;
let globalFlow: FlowField | null = null;
// The Gulf layer owns 18-31N 98-80W whenever it has real data for the current time; the global layer fills the rest.
const gulfActive = () => covers(scene().currents, simT());

function loadCurrents(sc: Scene, t0 = sc.t0, t1 = sc.t0 + sc.durationH * 3600) {
  if (!sc.currentsReq) {
    sc.currentsReq = true;
    sc.pending++;
    getCurrents(t0, t1)
      .then((c) => { sc.currents = c; if (scene() === sc) useCurrents(c); })
      .catch((e) => { sc.errors.push(`Currents: ${e.message}`); })
      .finally(() => { sc.pending--; if (scene() === sc) renderContent(); });
  }
  if (!sc.globalReq) {
    sc.globalReq = true;
    sc.pending++;
    getGlobalCurrents(t0, t1, gulfActive)
      .then((g) => { sc.global = g; if (scene() === sc) useGlobal(g); })
      .finally(() => { sc.pending--; if (scene() === sc) renderContent(); });
  }
}
function useCurrents(c: CurrentsSource | null) {
  if (!c) return;
  if (!flow) flow = new FlowField(c);
  else if (flow.source !== c) flow.setSource(c);
}
function useGlobal(g: GlobalCurrents | null) {
  if (!g) return;
  if (!globalFlow) globalFlow = new FlowField(g, { id: 'currents-global', n: 6500, width: 1.6, alpha: 220 });
  else if (globalFlow.source !== g) globalFlow.setSource(g);
}

let globalTick = 0, globalDt = 0;
function currentLayers(s: Scene, tUnix: number, dt: number): Layer[] {
  const out: Layer[] = [];
  const z = map.getZoom();
  const hasGlobal = !!(globalFlow && s.global && globalFlow.source === s.global && covers(s.global, tUnix));
  if (hasGlobal) {
    const c = map.getCenter();
    s.global!.prefetch(tUnix);
    // advance/re-upload the big global layer at half rate (deck re-tessellates paths on every update);
    // in between, layer() returns a new instance on the same data (re-pushing an old instance can hit a finalized one)
    globalDt += dt;
    if (++globalTick % 2 === 0) {
      globalFlow!.step(view, z, tUnix, globalDt * 60, [c.lng, c.lat]);
      globalDt = 0;
    }
    out.push(globalFlow!.layer(state.showCurrents) as any);
  }
  if (flow && s.currents && flow.source === s.currents) {
    s.currents.prefetch(tUnix);
    // thin the dense Gulf streaks when zoomed out so they match the global layer's density
    flow.step(view, z, tUnix, dt * 60, undefined, hasGlobal ? Math.min(1, Math.max(0.12, (z - 1.5) / 3.5)) : 1);
    out.push(flow.layer(state.showCurrents) as any);
  }
  return out;
}

// ---------------------------------------------------------------- UI: the one floating panel
const ui = document.getElementById('ui')!;
ui.innerHTML = `
  <section class="panel glass" id="panel">
    <header class="ph" title="Drag to move · double-click to collapse">
      <span class="grip" aria-hidden="true"></span>
      <b class="brand">OilFlood</b>
      <span class="hclock" id="hclock"></span>
      <button class="collapse" aria-label="Collapse panel"><svg width="12" height="12" viewBox="0 0 12 12"><path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
    </header>
    <div class="pb">
      <div class="mode">
        <button data-mode="historical">Historical</button>
        <button data-mode="prediction">Prediction</button>
      </div>
      <div class="mode model-sel" id="modelsel" title="Trajectory model">
        <button data-model="openoil" title="OpenDrift OpenOil: the established baseline">OpenOil</button>
        <button data-model="ours" title="Our model: Lagrangian + Fay + weathering + ML drift correction">Ours</button>
        <button data-model="compare" title="Both at once: OpenOil amber, ours cyan">Compare</button>
      </div>
      <div id="predform"></div>
      <div id="content"></div>
      <div class="sec time">
        <div class="clock"><b id="clock-utc"></b><span id="clock-local"></span></div>
        <div class="trow">
          <button class="play" id="play" title="Play / pause (space)"></button>
          <input type="range" id="slider" min="0" max="1000" value="0" aria-label="Simulation time" />
        </div>
        <div class="trow small">
          <span id="clock-rel"></span>
          <span class="rate"><button id="slower" title="Slower">−</button><span id="rate"></span><button id="faster" title="Faster">+</button></span>
        </div>
      </div>
      <div id="dyn"></div>
      <div class="sec legend" id="legend"></div>
      <div class="msg" id="msg"></div>
    </div>
  </section>`;
const $ = (id: string) => document.getElementById(id)!;
const panel = $('panel');
const content = $('content');
const slider = $('slider') as HTMLInputElement;
initPanel(panel);
const ICON_PLAY = '<svg width="12" height="12" viewBox="0 0 12 12"><path d="M2.5 1.2v9.6L10.5 6z" fill="currentColor"/></svg>';
const ICON_PAUSE = '<svg width="12" height="12" viewBox="0 0 12 12"><rect x="2" y="1.5" width="3" height="9" rx="0.8" fill="currentColor"/><rect x="7" y="1.5" width="3" height="9" rx="0.8" fill="currentColor"/></svg>';

panel.querySelectorAll<HTMLButtonElement>('.mode button[data-mode]').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode as Mode)));
panel.querySelectorAll<HTMLButtonElement>('#modelsel button').forEach((b) => b.addEventListener('click', () => setModel(b.dataset.model as ModelChoice)));
$('play').addEventListener('click', togglePlay);
$('slower').addEventListener('click', () => { state.rateIdx = Math.max(0, state.rateIdx - 1); renderTime(true); });
$('faster').addEventListener('click', () => { state.rateIdx = Math.min(RATES_H.length - 1, state.rateIdx + 1); renderTime(true); });
slider.addEventListener('input', () => { state.tH = (Number(slider.value) / 1000) * scene().durationH; state.playing = false; renderTime(true); });
addEventListener('keydown', (e) => {
  if (e.code === 'Space' && !(e.target instanceof HTMLElement && e.target.closest('input[type=text], input[type=number], select'))) { e.preventDefault(); togglePlay(); }
});
// Response timeline (historical): open state survives re-renders; a date click moves the clock there.
const respOpen = { resp: true, evo: false };
content.addEventListener('toggle', (e) => {
  const d = e.target as HTMLDetailsElement;
  if (d.dataset?.k === 'resp' || d.dataset?.k === 'evo') respOpen[d.dataset.k as 'resp' | 'evo'] = d.open;
}, true);
content.addEventListener('click', (e) => {
  const d = (e.target as HTMLElement).closest<HTMLElement>('.resp .ev .d');
  if (d) {
    const t = Number(d.closest<HTMLElement>('.ev')!.dataset.t);
    const s = scene();
    const h = (t - s.t0) / 3600;
    if (!Number.isFinite(h)) return;
    if (h < 0 || h > s.durationH) message('That date is outside this replay window (no data loaded for it).', 3500);
    state.tH = Math.min(s.durationH, Math.max(0, h));
    state.playing = false;
    renderTime(true);
    return;
  }
  const b = (e.target as HTMLElement).closest<HTMLButtonElement>('.spills button');
  if (!b) return;
  const c = state.catalog.find((x) => x.id === b.dataset.id);
  if (c) selectSpill(c);
});
$('legend').addEventListener('change', (e) => {
  if ((e.target as HTMLElement).id === 'cur-toggle') state.showCurrents = (e.target as HTMLInputElement).checked;
});

let msgTimer = 0;
function message(msg: string, ms = 5000) {
  const t = $('msg');
  t.textContent = msg; t.classList.add('on');
  clearTimeout(msgTimer);
  msgTimer = window.setTimeout(() => t.classList.remove('on'), ms);
}

function togglePlay() {
  const s = scene();
  if (!state.playing && state.tH >= s.durationH - 1e-6) state.tH = 0;
  state.playing = !state.playing;
  renderTime(true);
}

const pad = (n: number) => String(n).padStart(2, '0');
const fmtUTC = (t: number) => {
  const d = new Date(t * 1000);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
};
const fmtLocal = (t: number) => new Date(t * 1000).toLocaleString(undefined, {
  month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short',
});
const fmtDay = (t: number) => fmtUTC(t).slice(0, 10);
const pct = (x: number) => `${Math.round(x * 100)}%`;
const esc = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

let lastClock = '';
function renderTime(force = false) {
  const s = scene();
  const t = simT();
  const utc = fmtUTC(t);
  const rel = s.durationH > 96 ? `Day ${Math.floor(state.tH / 24) + 1} of ${Math.ceil(s.durationH / 24)}` : `T+${state.tH.toFixed(1)} h of ${Math.round(s.durationH)} h`;
  const key = utc + rel + state.playing + state.rateIdx;
  slider.value = String(Math.round((state.tH / s.durationH) * 1000));
  if (key === lastClock && !force) return;
  lastClock = key;
  $('play').innerHTML = state.playing ? ICON_PAUSE : ICON_PLAY;
  $('clock-utc').textContent = utc;
  $('clock-local').textContent = fmtLocal(t);
  $('hclock').textContent = utc;
  $('clock-rel').textContent = rel;
  $('rate').textContent = rateLabel(RATES_H[state.rateIdx]);
  ($('slower') as HTMLButtonElement).disabled = state.rateIdx === 0;
  ($('faster') as HTMLButtonElement).disabled = state.rateIdx === RATES_H.length - 1;
}

function budgetHTML() {
  const s = scene();
  const runs = shownRuns(s).filter((r) => r.tracks.budget.length);
  if (!runs.length) return '';
  const bs = runs.map((r) => budgetAt(r.tracks.budget, state.tH - (r.tracks.t0 - s.t0) / 3600));
  if (bs.some((b) => !b)) return '';
  const keys: [keyof BudgetKeys, string, string][] = [
    ['surface', 'Surface', '#ffb547'], ['evaporated', 'Evaporated', '#8fb8ff'],
    ['dispersed', 'Dispersed', '#62d5c0'], ['stranded', 'Stranded', '#ff5a64'],
  ];
  const cols = `78px repeat(${runs.length}, 1fr 34px)`;
  const head = runs.length > 1
    ? `<span></span>${runs.map((r) => `<span class="bhead" style="color:${PALETTES[r.m].css}">${modelShort(r.m)}</span><span></span>`).join('')}`
    : '';
  const title = runs.length > 1 ? 'Oil budget (OpenOil vs ours)' : `Oil budget (${modelShort(runs[0].m) === 'Ours' ? 'our model' : 'OpenOil'})`;
  return `<div class="sec"><div class="label">${title}</div><div class="budget" style="grid-template-columns:${cols}">${head}${keys
    .map(([k, lab, c]) => `<span>${lab}</span>${bs.map((b) => `<div class="bbar"><div style="width:${pct(b![k])};background:${c}"></div></div><span class="pct">${pct(b![k])}</span>`).join('')}`)
    .join('')}</div></div>`;
}
type BudgetKeys = { surface: number; evaporated: number; dispersed: number; stranded: number };

/** Small honest notes about what real data exists at the current sim time. */
function notesHTML() {
  const s = scene();
  const t = simT();
  const notes: string[] = [];
  if (s.pending > 0 && !covers(s.currents, t) && !covers(s.global, t)) notes.push('Loading currents…');
  else if (!covers(s.currents, t) && !covers(s.global, t)) {
    notes.push(`No current data for ${fmtDay(t)}`);
  }
  if (state.mode === 'historical' && state.selected) {
    if (s.footprints.length && !footprintAt(s.footprints, t)) notes.push(`No observed outline for ${fmtDay(t)}`);
    if (s.tracks) {
      const end = s.tracks.t0 + s.tracks.durationH * 3600;
      if (t < s.tracks.t0) notes.push('Model hindcast starts ' + fmtUTC(s.tracks.t0));
      else if (t > end + 3600) notes.push('Model hindcast ended ' + fmtUTC(end));
    }
  }
  return notes.length ? `<div class="notes">${notes.map((n) => `<div class="note">${esc(n)}</div>`).join('')}</div>` : '';
}

/** Dim response/evolution rows that lie after the current sim day. */
let lastPastKey = '';
function markPast() {
  if (state.mode !== 'historical' || !state.selected) return;
  const day = Math.floor(simT() / 86400);
  const key = `${state.selected.id}:${day}`;
  if (key === lastPastKey) return;
  lastPastKey = key;
  const end = (day + 1) * 86400;
  content.querySelectorAll<HTMLElement>('.resp .ev').forEach((el) => el.classList.toggle('past', Number(el.dataset.t) < end));
}

let lastDyn = '';
function renderDyn() {
  markPast();
  const html = notesHTML() + budgetHTML();
  if (html !== lastDyn) { lastDyn = html; $('dyn').innerHTML = html; }
}

function sourceLine() {
  const s = scene();
  const t = simT();
  const parts: string[] = [];
  if (covers(s.currents, t)) parts.push(`Gulf: ${s.currents!.source}`);
  if (s.global && covers(s.global, t)) parts.push(/RTOFS/.test(s.global.source) ? 'Global: NOAA RTOFS' : /ESPC/.test(s.global.source) ? 'Global: HYCOM ESPC' : 'Global: HYCOM GOFS 3.1');
  return parts.length ? `<div class="hint" style="margin-top:4px">${esc(parts.join(' · '))}</div>` : '';
}
let lastLegend = '';
function renderLegend() {
  const ms = shownRuns(scene()).map((r) => r.m);
  const html = legendHTML(state.showCurrents, sourceLine(), ms.length ? ms : wanted());
  if (html !== lastLegend) { lastLegend = html; $('legend').innerHTML = html; }
}

function errorsHTML(errs: string[]) {
  return errs.map((e) => `<div class="err">${esc(e)}</div>`).join('');
}

function renderContent() {
  const s = scene();
  let html = '';
  if (state.mode === 'historical') {
    const sel = state.selected;
    if (state.catalogError) html += errorsHTML([`Spill catalog: ${state.catalogError}`]);
    html += `<div class="sec"><div class="spills">${state.catalog
      .map((c) => `<button data-id="${esc(c.id)}" class="${sel?.id === c.id ? 'on' : ''}"><span>${esc(c.name)}</span><small>${esc(c.date_start.slice(0, 4))}</small></button>`)
      .join('')}</div></div>`;
    if (sel) {
      const hc = shownRuns(s).map((r) => modelShort(r.m));
      const srcs = [s.footprints.length ? `${s.footprints.length} observed outlines (NOAA/NESDIS)` : '', hc.length ? `hindcast: ${hc.join(' + ')}` : ''].filter(Boolean).join(' + ');
      const prog = wanted().filter((m) => !s.runs[m] && s.runMsg[m]).map((m) => `${modelShort(m)}: ${s.runMsg[m]}`).join(' · ');
      html += `<div class="sec"><h2>${esc(sel.name)}</h2>
      <div class="sub">${srcs ? esc(srcs) : ''}${s.loading ? esc(s.loading) : ''}${prog ? `<br>${esc(prog)}` : ''}</div>
      <div class="kv" style="margin-top:6px">
        <span>Start</span><span>${esc(sel.date_start.slice(0, 10))}</span>
        ${sel.date_end ? `<span>End</span><span>${esc(sel.date_end.slice(0, 10))}</span>` : ''}
        ${sel.volume_bbl ? `<span>Volume</span><span>${Number(sel.volume_bbl).toLocaleString()} bbl</span>` : ''}
        ${sel.oil_type ? `<span>Oil</span><span>${esc(sel.oil_type)}${s.tracks?.meta?.oil_type && s.tracks.meta.oil_type !== sel.oil_type ? ` <small class="hint">(model: ${esc(s.tracks.meta.oil_type)})</small>` : ''}</span>` : ''}
        <span>Position</span><span>${sel.lat.toFixed(3)}°, ${sel.lon.toFixed(3)}°</span>
      </div>${modelNoteHTML(s)}${errorsHTML(s.errors)}`;
      html += responseHTML(sel, respOpen);
      if (sel.source_urls?.length) html += `<details class="links"><summary>Sources</summary>${sel.source_urls.map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u.replace(/^https?:\/\//, ''))}</a>`).join('<br>')}</details>`;
      html += `</div>`;
    } else {
      html += `<div class="hint">Pick a spill above or on the globe to replay it with its real currents.</div>`;
    }
  } else {
    const j = state.job;
    if (!j) {
      html += `<div class="hint">Click the ocean (Gulf of Mexico) to release ${fmtBbl(PRED.volumeBbl)} of ${esc(oilLabel(PRED.oilId).toLowerCase())} now and forecast its drift with the live forecast above.</div>
        <button class="demo-spill" id="demo-spill" title="${DEMO_SPILL.lat.toFixed(2)}°N ${(-DEMO_SPILL.lon).toFixed(2)}°W, the pulsing ring on the map">Release it off Galveston</button>`;
    } else {
      html += `<div class="sec"><h2>Spill prediction</h2><div class="sub">${j.lat.toFixed(3)}°, ${j.lon.toFixed(3)}°${j.volume_bbl ? ` · ${fmtBbl(j.volume_bbl)}` : ''}${j.oil_type ? ` · ${esc(j.oil_type)}` : ''}</div>`;
      if (j.note) html += `<div class="warn">${esc(j.note)}</div>`;
      const multi = wanted().length > 1;
      if (!Object.keys(state.jobRuns).length && j.status === 'error') html += `<div class="err">${esc(j.error || 'Run failed')}</div>`;
      for (const mid of wanted()) {
        const r = state.jobRuns[mid];
        if (!r || r.status === 'done') continue;
        const who = multi || mid === 'ours' ? `${modelShort(mid)}: ` : '';
        html += r.status === 'error'
          ? `<div class="err">${esc(who + (r.error || 'Run failed'))}</div>`
          : `<div class="row" style="margin-top:6px"><span class="hint">${esc(who)}${r.status === 'queued' ? 'Queued…' : 'Simulating drift…'}</span><span class="hint">${pct(r.progress)}</span></div>
          <div class="progress"><div style="width:${pct(r.progress)}"></div></div>`;
      }
      const runs = shownRuns(s);
      if (s.tracks && runs.length) {
        const m = s.tracks.meta ?? {};
        html += `<div class="kv" style="margin-top:6px"><span>Start</span><span>${fmtUTC(s.tracks.t0)}</span>
          <span>Horizon</span><span>${Math.round(s.tracks.durationH)} h${m.requested_duration_h && m.requested_duration_h > s.tracks.durationH + 0.5 ? ` of ${Math.round(m.requested_duration_h)} h (forecast ends)` : ''}</span>
          ${m.forecast ? `<span>Forcing</span><span>${esc(shortModel(m.forecast.currents?.model).split(' ')[0])} ${fmtRun(m.forecast.currents?.issued_at)} · ${esc(shortModel(m.forecast.winds?.model).split(' ')[0])} ${fmtRun(m.forecast.winds?.issued_at)}</span>` : ''}
          <span>Model</span><span>${runs.map((r) => `<b style="color:${PALETTES[r.m].css}">${modelShort(r.m)}</b> <small class="hint">${r.tracks.meta?.runtime_s ?? '?'} s</small>`).join(' · ')}</span>
          <span>Particles</span><span>${runs.map((r) => r.tracks.particles.length).join(' · ')}</span></div>`;
        if (m.seed_moved_km > 0) html += `<div class="hint" style="margin-top:4px">Seeded ${m.seed_moved_km} km away, at the nearest model water cell.</div>`;
      }
      html += modelNoteHTML(s);
      html += `<div class="hint" style="margin-top:8px">Click elsewhere on the ocean for a new spill.</div></div>`;
    }
    html += errorsHTML(s.errors);
  }
  const scrolls = [...content.querySelectorAll<HTMLElement>('.resp .tl')].map((el) => el.scrollTop);
  content.innerHTML = html;
  content.querySelectorAll<HTMLElement>('.resp .tl').forEach((el, i) => { el.scrollTop = scrolls[i] ?? 0; });
  lastPastKey = '';
  syncPins();
  renderLegend();
  renderDyn();
}

// ---------------------------------------------------------------- prediction settings + live forecast
const PRED_KEY = 'oilflood.pred.v1';
const fmtBbl = (b: number) => `${Number(b).toLocaleString(undefined, { maximumFractionDigits: 1 })} bbl`;
const oilLabel = (id: string) => state.oils.find((o) => o.id === id)?.label ?? id;
const shortModel = (m?: string) => (m ?? '').replace(/^NOAA |^NCEP |^HYCOM /, '').replace(/ global/, '');
const fmtIso = (s?: string | null) => (s ? fmtUTC(Date.parse(s.endsWith('Z') ? s : s + 'Z') / 1000).replace(/^\d{4}-/, '') : '?');
const fmtRun = (s?: string | null) => (s ? fmtIso(s).replace(/:00 UTC$/, 'z') : '');

function renderForecast() {
  const el = document.getElementById('p-fc');
  if (!el) return;
  const L = state.live;
  if (!L) { el.innerHTML = state.liveError ? `<div class="err">${esc(state.liveError)}</div>` : '<div class="hint">Loading live forecast…</div>'; return; }
  el.innerHTML = `<div class="label" style="margin:8px 0 4px">Live forecast</div>
    <div class="kv">
      <span>Currents</span><span>${esc(shortModel(L.currents.model))} · issued ${fmtIso(L.currents.issued_at)}</span>
      <span>Winds</span><span>${esc(shortModel(L.winds.model))} · issued ${fmtIso(L.winds.issued_at)}</span>
      <span>Valid until</span><span>${fmtIso(L.valid_until)} <small class="hint">(${Math.round(L.hours_available)} h from now)</small></span>
    </div>${L.note ? `<div class="${L.stale ? 'err' : 'warn'}">${esc(L.note)}</div>` : ''}`;
}

async function refreshLive() {
  try {
    state.live = await getLive();
    state.liveError = null;
  } catch (e) {
    state.live = null;
    state.liveError = (e as Error).message;
  }
  renderForecast();
}

async function initPredForm() {
  try {
    const saved = JSON.parse(localStorage.getItem(PRED_KEY) || 'null');
    if (saved && saved.volumeBbl > 0) PRED.volumeBbl = saved.volumeBbl;
    if (saved?.oilId) PRED.oilId = saved.oilId;
  } catch { /* ignore */ }
  try {
    const r = await getOils();
    state.oils = r.oils;
    if (!state.oils.some((o) => o.id === PRED.oilId)) PRED.oilId = r.default;
  } catch (e) {
    state.liveError = `Oil list: ${(e as Error).message}`;
  }
  const f = $('predform');
  f.innerHTML = `<div class="sec pform">
    <div class="frow"><label for="p-amt">Amount</label><input id="p-amt" type="number" min="1" max="10000000" step="any" value="${PRED.volumeBbl}" /><span class="unit">bbl</span></div>
    <div class="frow"><label for="p-oil">Oil type</label><select id="p-oil">${state.oils
      .map((o) => `<option value="${esc(o.id)}" ${o.id === PRED.oilId ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select></div>
    <div id="p-fc"></div>
  </div>`;
  const amt = $('p-amt') as HTMLInputElement, oil = $('p-oil') as HTMLSelectElement;
  const save = () => {
    const v = Number(amt.value);
    amt.classList.toggle('bad', !(v > 0 && v <= 1e7));
    if (v > 0 && v <= 1e7) PRED.volumeBbl = v;
    PRED.oilId = oil.value;
    try { localStorage.setItem(PRED_KEY, JSON.stringify({ volumeBbl: PRED.volumeBbl, oilId: PRED.oilId })); } catch { /* private mode */ }
    if (!state.job) renderContent();
  };
  amt.addEventListener('input', save);
  oil.addEventListener('change', save);
  renderForecast();
  refreshLive();
  setInterval(refreshLive, 10 * 60 * 1000);
}

// ---------------------------------------------------------------- modes / data
function setMode(m: Mode) {
  state.mode = m;
  panel.querySelectorAll<HTMLButtonElement>('.mode button').forEach((b) => b.classList.toggle('on', b.dataset.mode === m));
  state.playing = false;
  state.tH = 0;
  map.getCanvas().style.cursor = m === 'prediction' ? 'crosshair' : '';
  $('predform').style.display = m === 'prediction' ? '' : 'none';
  const s = scene();
  state.rateIdx = autoRateIdx(s.durationH);
  loadCurrents(s);
  useCurrents(s.currents);
  useGlobal(s.global);
  if (s.tracks || s.footprints.length) state.playing = true;
  renderContent(); renderTime(true);
}

async function selectSpill(s: CatalogSpill) {
  if (state.selected?.id === s.id && state.hist.loading == null) {
    map.flyTo({ center: [s.lon, s.lat], zoom: 6.3, pitch: 25, duration: 2000, essential: true });
    return;
  }
  if (state.mode !== 'historical') setMode('historical');
  state.selected = s;
  const t0 = toUnix(s.date_start);
  const t1 = s.date_end ? toUnix(s.date_end) : t0 + 72 * 3600;
  const sc = emptyScene(t0, Math.max(1, (t1 - t0) / 3600));
  sc.loading = 'Loading…';
  state.hist = sc;
  state.playing = false; state.tH = 0;
  state.rateIdx = autoRateIdx(sc.durationH);
  renderContent(); renderTime(true);
  map.flyTo({ center: [s.lon, s.lat], zoom: 6.3, pitch: 25, duration: 3200, essential: true, curve: 1.6 });
  const stale = () => state.selected !== s || state.hist !== sc;
  const active = () => state.mode === 'historical' && !stale();

  // Timeline = union of observed outlines and hindcast; keep the absolute clock when it changes.
  const applyTimeline = () => {
    let a = Infinity, b = -Infinity;
    for (const r of Object.values(sc.runs)) { a = Math.min(a, r!.tracks.t0); b = Math.max(b, r!.tracks.t0 + r!.tracks.durationH * 3600); }
    for (const f of sc.footprints) { a = Math.min(a, f.t); b = Math.max(b, f.t + 86400); }
    if (!Number.isFinite(a)) return;
    const abs = sc.t0 + state.tH * 3600;
    sc.t0 = a; sc.durationH = Math.max(1, (b - a) / 3600);
    if (active()) {
      state.tH = Math.min(sc.durationH, Math.max(0, (abs - sc.t0) / 3600));
      state.rateIdx = autoRateIdx(sc.durationH);
    }
  };
  const startPlayback = () => { if (active() && !state.playing) { state.tH = 0; state.playing = true; } };

  // Currents for the spill's own dates (whole outline/hindcast window, padded a day).
  loadCurrents(sc, t0 - 86400, Math.max(t1, t0 + 72 * 3600) + 86400);
  const pFootprints = getFootprints(s).then((fp) => {
    if (stale()) return;
    sc.footprints = fp;
    applyTimeline();
    if (fp.length) startPlayback();
  }).catch((e) => { if (!stale()) sc.errors.push(`Observed outlines: ${e.message}`); });
  sc.loadRun = (m: ModelId) => {
    if (sc.runReq.has(m)) return Promise.resolve();
    sc.runReq.add(m);
    sc.runMsg[m] = 'computing hindcast on real forcing…';
    const onProgress = (p: number) => {
      if (stale()) return;
      sc.runMsg[m] = `computing hindcast on real forcing… ${Math.round((p > 1 ? p / 100 : p) * 100)}%`;
      renderContent();
    };
    return getHistoricalTracks(s, m, onProgress, stale).then((tr) => {
      if (stale() || !tr) return;
      addRun(sc, m, tr);
      delete sc.runMsg[m];
      applyTimeline();
      startPlayback();
      renderContent(); renderTime(true);
    }).catch((e) => {
      if (stale()) return;
      delete sc.runMsg[m];
      sc.errors.push(`Hindcast (${modelShort(m)}): ${e.message}`);
      renderContent();
    });
  };
  const pTracks = Promise.all(wanted().map((m) => sc.loadRun!(m)));
  renderContent();
  await Promise.all([pFootprints.then(() => !stale() && (renderContent(), renderTime(true))), pTracks]);
  if (stale()) return;
  sc.loading = null;
  renderContent(); renderTime(true);
}

/** Prediction scene of the current click: created when its first model run arrives. */
let jobScene: ((tr: Tracks) => Scene) | null = null;

async function startSpill(lon: number, lat: number) {
  const token = ++state.jobToken;
  if (map.getZoom() < 5) map.flyTo({ center: [lon, lat], zoom: 6.5, duration: 2500, essential: true });
  const prev = state.pred;
  state.playing = false;
  state.job = { id: '', lat, lon, status: 'queued', progress: 0 };
  state.jobRuns = {};
  prev.errors = [];
  let sc: Scene | null = null;
  jobScene = (tracks: Tracks) => {
    if (sc) return sc;
    sc = emptyScene(tracks.t0, tracks.durationH);
    // same live window as before the click: reuse its fields when the run sits inside them
    if (prev.currents && covers(prev.currents, tracks.t0) && covers(prev.currents, tracks.t0 + tracks.durationH * 3600)) {
      sc.currents = prev.currents; sc.currentsReq = true;
    }
    if (prev.global && covers(prev.global, tracks.t0)) { sc.global = prev.global; sc.globalReq = true; }
    state.pred = sc;
    loadCurrents(sc);
    if (state.mode === 'prediction') {
      useCurrents(sc.currents); useGlobal(sc.global);
      state.tH = 0; state.playing = true; state.rateIdx = autoRateIdx(sc.durationH);
    }
    return sc;
  };
  renderContent(); renderTime(true);
  await Promise.all(wanted().map((m) => runPrediction(m, token)));
}

function updateJobStatus() {
  const j = state.job;
  if (!j) return;
  const rs = Object.values(state.jobRuns);
  if (!rs.length) return;
  const fin = rs.every((r) => r!.status === 'done' || r!.status === 'error');
  j.status = fin ? (rs.some((r) => r!.status === 'done') ? 'done' : 'error') : 'running';
  j.progress = rs.reduce((a, r) => a + r!.progress, 0) / rs.length;
  j.error = rs.find((r) => r!.error)?.error;
}

/** One model's run for the current prediction click: POST, poll, fetch tracks, add to the scene. */
async function runPrediction(m: ModelId, token: number) {
  const j0 = state.job;
  if (!j0 || state.jobRuns[m]) return;
  const run = { id: '', status: 'queued', progress: 0 } as { id: string; status: string; progress: number; error?: string };
  state.jobRuns[m] = run;
  updateJobStatus();
  try {
    const job = await postSpill(j0.lat, j0.lon, m);
    if (token !== state.jobToken) return;
    run.id = job.id;
    const { status: _s, progress: _p, id: _id, model: _m, ...summary } = job as any;
    Object.assign(state.job!, summary, { lat: j0.lat, lon: j0.lon });
    if (job.forecast && state.live && job.forecast.valid_until !== state.live.valid_until) refreshLive();
    for (;;) {
      const st = await pollSpill(job);
      if (token !== state.jobToken) return;
      Object.assign(run, st);
      updateJobStatus();
      renderContent();
      if (st.status === 'done') break;
      if (st.status === 'error') return;
      await new Promise((r) => setTimeout(r, 700));
    }
    const tracks = await getSpillTracks(job);
    if (token !== state.jobToken || !jobScene) return;
    addRun(jobScene(tracks), m, tracks);
    updateJobStatus();
    renderContent(); renderTime(true);
  } catch (e) {
    if (token !== state.jobToken || !state.job) return;
    run.status = 'error';
    run.error = String((e as Error).message ?? e);
    updateJobStatus();
    renderContent();
  }
}

/** Model toggle: show OpenOil, ours, or both; runs whatever the current spill is missing. */
function setModel(m: ModelChoice) {
  state.model = m;
  try { localStorage.setItem(MODEL_KEY, m); } catch { /* private mode */ }
  panel.querySelectorAll<HTMLButtonElement>('#modelsel button').forEach((b) => b.classList.toggle('on', b.dataset.model === m));
  if (state.hist.loadRun && state.selected) for (const id of wanted()) state.hist.loadRun(id);
  if (state.job && state.job.lat != null) for (const id of wanted()) if (!state.jobRuns[id]) runPrediction(id, state.jobToken);
  renderContent();
}

/** What our model is + its held-out ML skill (from GET /api/models), shown when ours is on screen. */
function modelNoteHTML(s: Scene) {
  if (!wanted().includes('ours')) return '';
  const info = state.models.find((x) => x.id === 'ours');
  const sk = info?.skill;
  let html = '<div class="hint mnote">';
  if (sk?.traj_24h) {
    html += `<b style="color:${PALETTES.ours.css}">Ours</b>: ML drift correction trained on ${sk.drifters} NOAA drifters `
      + `(${Number(sk.rows).toLocaleString()} hourly fixes). On held-out drifters the median 24 h position error drops `
      + `${sk.traj_24h.median_km_current} → ${sk.traj_24h.median_km_ml} km, 48 h ${sk.traj_48h.median_km_current} → ${sk.traj_48h.median_km_ml} km.`;
  } else if (info) {
    html += esc(info.summary);
  }
  const mo = s.runs.ours?.tracks.meta;
  if (mo && mo.ml_correction === false) html += ' (ML correction off for this run)';
  return html + '</div>';
}

content.addEventListener('click', (e) => {
  if (!(e.target as HTMLElement).closest('#demo-spill')) return;
  map.flyTo({ ...HOME, offset: panelOffset(), duration: 1500, essential: true });
  startSpill(DEMO_SPILL.lon, DEMO_SPILL.lat);
});
/** Shift the camera target right of the panel so the area of interest isn't hidden behind it. */
function panelOffset(): [number, number] {
  const r = panel.getBoundingClientRect();
  if (panel.classList.contains('collapsed') || innerWidth < 900) return [0, 0];
  if (r.right < innerWidth / 2) return [Math.round(r.right / 2), 0]; // panel on the left
  if (r.left > innerWidth / 2) return [-Math.round((innerWidth - r.left) / 2), 0]; // panel on the right (default)
  return [0, 0];
}

map.on('click', (e) => {
  if (state.mode !== 'prediction') { message('Switch to Prediction to release a spill.', 2500); return; }
  const { lng, lat } = e.lngLat;
  if (lng < GULF_BBOX.minLon - 2 || lng > GULF_BBOX.maxLon + 2 || lat < GULF_BBOX.minLat - 2 || lat > GULF_BBOX.maxLat + 2) {
    message('Forecasts need real cached forcing, which covers the Gulf of Mexico (18–31°N, 98–80°W). Click inside the Gulf.');
    return;
  }
  startSpill(lng, lat);
});

// ---------------------------------------------------------------- render loop
let view = { ...GULF_BBOX };
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
  if (state.mode !== 'prediction') return [];
  const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 300);
  if (!state.job) { // suggested demo spill point: a soft ring the user can click (or use the panel button)
    return [new ScatterplotLayer({
      id: 'spill-suggest',
      data: [DEMO_SPILL],
      getPosition: (d: any) => [d.lon, d.lat],
      getRadius: 6 + 4 * pulse,
      radiusUnits: 'pixels',
      filled: false,
      stroked: true,
      getLineColor: [255, 181, 71, 110 + 100 * pulse],
      lineWidthUnits: 'pixels',
      getLineWidth: 1.5,
      updateTriggers: { getRadius: pulse, getLineColor: pulse },
      parameters: ON_SURFACE,
    })];
  }
  const running = state.job.status !== 'done' && state.job.status !== 'error';
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
let lastSlow = 0;
function frame(now: number) {
  const dt = Math.min(0.1, (now - last) / 1000);
  last = now;
  const s = scene();
  if (state.playing) {
    state.tH += dt * RATES_H[state.rateIdx];
    if (state.tH >= s.durationH) { state.tH = s.durationH; state.playing = false; }
  }
  renderTime();
  if (now - lastSlow > 250) { lastSlow = now; renderDyn(); renderLegend(); }
  const tUnix = simT();
  const layers: Layer[] = currentLayers(s, tUnix, dt);
  if (s.footprints.length) layers.push(footprintLayer(s.footprints, tUnix) as any);
  for (const r of shownRuns(s)) {
    r.oil.update(state.tH - (r.tracks.t0 - s.t0) / 3600);
    layers.push(...(r.oil.layers(s.footprints.length ? 0.45 : 1) as any[]));
  }
  layers.push(...markerLayer());
  overlay.setProps({ layers });
  requestAnimationFrame(frame);
}

map.on('load', async () => {
  updateView();
  const q = new URLSearchParams(location.search);
  // demo default: a short fly from the globe down to Houston/Galveston (a ?spill= link flies to that spill instead)
  if (!q.get('spill')) map.flyTo({ ...HOME, offset: panelOffset(), duration: 3500, curve: 1.5, essential: true });
  try {
    state.catalog = await getCatalog();
  } catch (e) {
    state.catalogError = (e as Error).message;
  }
  initPredForm();
  getModels().then((ms) => { state.models = ms; renderContent(); }).catch(() => { /* note only */ });
  const qm = q.get('model');
  setModel(qm === 'ours' || qm === 'openoil' || qm === 'compare' ? qm : state.model);
  setMode(q.get('mode') === 'prediction' ? 'prediction' : 'historical');
  const pick = q.get('spill') && state.catalog.find((c) => c.id === q.get('spill'));
  if (pick) selectSpill(pick);
  requestAnimationFrame(frame);
});
renderTime(true);
// debugging handle for the console / tests
(window as any).__oilflood = { map, state, overlay, get flow() { return flow; }, get globalFlow() { return globalFlow; } };
