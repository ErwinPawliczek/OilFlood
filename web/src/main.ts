import 'maplibre-gl/dist/maplibre-gl.css';
import './style.css';
import maplibregl from 'maplibre-gl';
import { MapboxOverlay } from '@deck.gl/mapbox';
import { ScatterplotLayer } from '@deck.gl/layers';
import type { Layer } from '@deck.gl/core';
import {
  getCatalog, getHistoricalTracks, getFootprints, getCurrents, postSpill, pollSpill, getSpillTracks, getLive, getOils,
  toUnix, PRED, GULF_BBOX, type CurrentsSource, type SpillJob, type LiveForecast, type OilOption, type RunSummary,
} from './api';
import { FlowField } from './flow';
import { getGlobalCurrents, type GlobalCurrents } from './globalCurrents';
import { OilView, footprintLayer, footprintAt, budgetAt } from './oil';
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
  t0, durationH, tracks: null, oil: null, footprints: [], currents: null, currentsReq: false,
  global: null, globalReq: false, pending: 0, loading: null, errors: [],
});

const state = {
  mode: 'prediction' as Mode,
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
let globalLayer: ReturnType<FlowField['layer']> | null = null;
function currentLayers(s: Scene, tUnix: number, dt: number): Layer[] {
  const out: Layer[] = [];
  const z = map.getZoom();
  const hasGlobal = !!(globalFlow && s.global && globalFlow.source === s.global && covers(s.global, tUnix));
  if (hasGlobal) {
    const c = map.getCenter();
    s.global!.prefetch(tUnix);
    // advance/re-upload the big global layer at half rate (deck re-tessellates paths on every update)
    globalDt += dt;
    if (++globalTick % 2 === 0 || !globalLayer) {
      globalFlow!.step(view, z, tUnix, globalDt * 60, [c.lng, c.lat]);
      globalDt = 0;
      globalLayer = globalFlow!.layer(state.showCurrents);
    } else if (globalLayer.props.visible !== state.showCurrents) {
      globalLayer = globalFlow!.layer(state.showCurrents);
    }
    out.push(globalLayer as any);
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

panel.querySelectorAll<HTMLButtonElement>('.mode button').forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode as Mode)));
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
  if (!s.tracks || !s.tracks.budget.length) return '';
  const b = budgetAt(s.tracks.budget, state.tH - (s.tracks.t0 - s.t0) / 3600);
  if (!b) return '';
  const rows: [string, number, string][] = [
    ['Surface', b.surface, '#ffb547'], ['Evaporated', b.evaporated, '#8fb8ff'],
    ['Dispersed', b.dispersed, '#62d5c0'], ['Stranded', b.stranded, '#ff5a64'],
  ];
  return `<div class="sec"><div class="label">Oil budget (model)</div><div class="budget">${rows
    .map(([k, v, c]) => `<span>${k}</span><div class="bbar"><div style="width:${pct(v)};background:${c}"></div></div><span class="pct">${pct(v)}</span>`)
    .join('')}</div></div>`;
}

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
  const html = legendHTML(state.showCurrents, sourceLine());
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
      const srcs = [s.footprints.length ? `${s.footprints.length} observed outlines (NOAA/NESDIS)` : '', s.tracks ? 'model hindcast' : ''].filter(Boolean).join(' + ');
      html += `<div class="sec"><h2>${esc(sel.name)}</h2>
      <div class="sub">${srcs ? esc(srcs) : ''}${s.loading ? esc(s.loading) : ''}</div>
      <div class="kv" style="margin-top:6px">
        <span>Start</span><span>${esc(sel.date_start.slice(0, 10))}</span>
        ${sel.date_end ? `<span>End</span><span>${esc(sel.date_end.slice(0, 10))}</span>` : ''}
        ${sel.volume_bbl ? `<span>Volume</span><span>${Number(sel.volume_bbl).toLocaleString()} bbl</span>` : ''}
        ${sel.oil_type ? `<span>Oil</span><span>${esc(sel.oil_type)}${s.tracks?.meta?.oil_type && s.tracks.meta.oil_type !== sel.oil_type ? ` <small class="hint">(model: ${esc(s.tracks.meta.oil_type)})</small>` : ''}</span>` : ''}
        <span>Position</span><span>${sel.lat.toFixed(3)}°, ${sel.lon.toFixed(3)}°</span>
      </div>${errorsHTML(s.errors)}`;
      html += responseHTML(sel, respOpen);
      if (sel.source_urls?.length) html += `<details class="links"><summary>Sources</summary>${sel.source_urls.map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u.replace(/^https?:\/\//, ''))}</a>`).join('<br>')}</details>`;
      html += `</div>`;
    } else {
      html += `<div class="hint">Pick a spill above or on the globe to replay it with its real currents.</div>`;
    }
  } else {
    const j = state.job;
    if (!j) {
      html += `<div class="hint">Click the ocean (Gulf of Mexico) to release ${fmtBbl(PRED.volumeBbl)} of ${esc(oilLabel(PRED.oilId).toLowerCase())} now and forecast its drift with the live forecast above.</div>`;
    } else {
      html += `<div class="sec"><h2>Spill prediction</h2><div class="sub">${j.lat.toFixed(3)}°, ${j.lon.toFixed(3)}°${j.volume_bbl ? ` · ${fmtBbl(j.volume_bbl)}` : ''}${j.oil_type ? ` · ${esc(j.oil_type)}` : ''}</div>`;
      if (j.note) html += `<div class="warn">${esc(j.note)}</div>`;
      if (j.status !== 'done') {
        html += j.status === 'error'
          ? `<div class="err">${esc(j.error || 'Run failed')}</div>`
          : `<div class="row" style="margin-top:6px"><span class="hint">${j.status === 'queued' ? 'Queued…' : 'Simulating drift…'}</span><span class="hint">${pct(j.progress)}</span></div>
          <div class="progress"><div style="width:${pct(j.progress)}"></div></div>`;
      } else if (s.tracks) {
        const m = s.tracks.meta ?? {};
        html += `<div class="kv" style="margin-top:6px"><span>Start</span><span>${fmtUTC(s.tracks.t0)}</span>
          <span>Horizon</span><span>${Math.round(s.tracks.durationH)} h${m.requested_duration_h && m.requested_duration_h > s.tracks.durationH + 0.5 ? ` of ${Math.round(m.requested_duration_h)} h (forecast ends)` : ''}</span>
          ${m.forecast ? `<span>Forcing</span><span>${esc(shortModel(m.forecast.currents?.model).split(' ')[0])} ${fmtRun(m.forecast.currents?.issued_at)} · ${esc(shortModel(m.forecast.winds?.model).split(' ')[0])} ${fmtRun(m.forecast.winds?.issued_at)}</span>` : ''}
          <span>Particles</span><span>${s.tracks.particles.length}</span></div>`;
        if (m.seed_moved_km > 0) html += `<div class="hint" style="margin-top:4px">Seeded ${m.seed_moved_km} km away, at the nearest model water cell.</div>`;
      }
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
    if (sc.tracks) { a = sc.tracks.t0; b = sc.tracks.t0 + sc.tracks.durationH * 3600; }
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
  const onProgress = (p: number) => {
    if (stale()) return;
    sc.loading = `Computing hindcast on real forcing… ${Math.round((p > 1 ? p / 100 : p) * 100)}%`;
    renderContent();
  };
  const pTracks = getHistoricalTracks(s, onProgress, stale).then((tr) => {
    if (stale() || !tr) return;
    sc.tracks = tr;
    sc.oil = new OilView(tr);
    applyTimeline();
    startPlayback();
  }).catch((e) => { if (!stale()) sc.errors.push(`Hindcast: ${e.message}`); });
  renderContent();
  await Promise.all([pFootprints.then(() => !stale() && (renderContent(), renderTime(true))), pTracks]);
  if (stale()) return;
  sc.loading = null;
  renderContent(); renderTime(true);
}

async function startSpill(lon: number, lat: number) {
  const token = ++state.jobToken;
  if (map.getZoom() < 5) map.flyTo({ center: [lon, lat], zoom: 6.5, duration: 2500, essential: true });
  const prev = state.pred;
  state.playing = false;
  state.job = { id: '', lat, lon, status: 'queued', progress: 0 };
  prev.errors = [];
  renderContent(); renderTime(true);
  try {
    const job = await postSpill(lat, lon);
    if (token !== state.jobToken) return;
    state.job = { ...job, status: 'queued', progress: 0 };
    if (job.forecast && state.live && job.forecast.valid_until !== state.live.valid_until) refreshLive();
    for (;;) {
      const st = await pollSpill(job);
      if (token !== state.jobToken) return;
      Object.assign(state.job, st);
      renderContent();
      if (st.status === 'done') break;
      if (st.status === 'error') return;
      await new Promise((r) => setTimeout(r, 700));
    }
    const tracks = await getSpillTracks(job);
    if (token !== state.jobToken) return;
    const sc = emptyScene(tracks.t0, tracks.durationH);
    sc.tracks = tracks; sc.oil = new OilView(tracks);
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
    renderContent(); renderTime(true);
  } catch (e) {
    if (token !== state.jobToken || !state.job) return;
    state.job.status = 'error';
    state.job.error = String((e as Error).message ?? e);
    renderContent();
  }
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
  if (state.mode !== 'prediction' || !state.job) return [];
  const pulse = 0.5 + 0.5 * Math.sin(performance.now() / 300);
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
  try {
    state.catalog = await getCatalog();
  } catch (e) {
    state.catalogError = (e as Error).message;
  }
  initPredForm();
  const q = new URLSearchParams(location.search);
  setMode(q.get('mode') === 'prediction' ? 'prediction' : 'historical');
  const pick = q.get('spill') && state.catalog.find((c) => c.id === q.get('spill'));
  if (pick) selectSpill(pick);
  requestAnimationFrame(frame);
});
renderTime(true);
// debugging handle for the console / tests
(window as any).__oilflood = { map, state, overlay, get flow() { return flow; }, get globalFlow() { return globalFlow; } };
