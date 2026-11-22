// First-responder map layer (T21): draws the outline's `layer` block (backend/app/response_layer.py) on the globe,
// synced to the clock: shoreline arrival-time segments (real NOAA ESI lines), offshore arrival isochrones, boom plan
// (planned -> in place at its deploy-by time), dispersant / in-situ burn zones and skimmer targets per 3 h step,
// staging bases with travel lines, and a "Next actions" list for the panel.
// Globe gotcha (A3): LineLayer/TextLayer misrender on the MapLibre globe -> PathLayer/GeoJsonLayer + HTML labels.

import { PathLayer, ScatterplotLayer, GeoJsonLayer } from '@deck.gl/layers';
import { PathStyleExtension } from '@deck.gl/extensions';
import type { Layer } from '@deck.gl/core';
import maplibregl from 'maplibre-gl';
import { ON_SURFACE } from './types';

type RGBA = [number, number, number, number];
type MultiPoly = number[][][][];

export interface RespLayer {
  version: string;
  bands: string[];
  shore_source: 'lines' | 'cells';
  shore: { path?: [number, number][]; point?: [number, number]; eta_h: number; band: number; impact: string; esi: string; tier: number }[];
  isochrones: { t_h: number; coordinates: MultiPoly }[];
  /** best guess + uncertainty (T23): cumulative reach with windage 1-4 % (GNOME range) */
  uncertainty?: { t_h: number; coordinates: MultiPoly }[];
  circles?: { kind: string; path: [number, number][]; label: string }[];
  booms: Boom[];
  inlets: { name: string; lon: number; lat: number; eta_h: number; deploy_by_h: number; priority: number; late?: boolean }[];
  zones: { kind: 'disp' | 'burn'; h: number; pct: number; coordinates: MultiPoly }[];
  zone_step_h: number;
  dispersant_line?: [number, number][][];
  skimmer_targets: { h: number; lon: number; lat: number; rank: number; pct: number; ok: boolean }[];
  staging_bases: { name: string; type: string; lon: number; lat: number }[];
  staging_ops: { for: string; h: number; lon: number; lat: number; base: Staging }[];
  timeline: { h: number; kind: string; lon: number; lat: number; text: string }[];
  assumptions: Record<string, number | null>;
  rules: string[];
  notes: string[];
  sources: { what: string; ref: string; url: string }[];
  error?: string;
}
interface Staging { name: string; type: string; lon: number; lat: number; km: number; travel_h: number }
export interface Lead { lead_h: number; tier1_h: number; hvp: string | null; calc_h: number; travel_h: number | null; en_route_h: number; base: string | null; rule: string }
type BoomKind = 'exclusion' | 'deflection' | 'nohold' | 'beach';
export interface Boom {
  priority: number; name: string | null; lon: number; lat: number; eta_h: number; eta_earliest_h?: number | null; deploy_by_h: number; mobilize_by_h: number;
  late: boolean; kind: BoomKind; current_kt: number | null; across_kt?: number | null; boom_angle_deg: number | null; km: number;
  lines: { path: [number, number][]; kind: BoomKind; esi: string; km: number; across_kt?: number | null }[];
  staging: Staging[]; esi: string; esi_label: string; label: string; lead?: Lead; rule?: string;
}

/** Arrival bands <12, 12-24, 24-48, 48-72, >72 h: red, orange, yellow, pale yellow, grey-blue. */
export const BAND_COL: RGBA[] = [[255, 59, 48, 255], [255, 149, 0, 255], [255, 214, 10, 255], [255, 240, 170, 255], [150, 170, 200, 255]];
export const BOOM_COL: Record<string, RGBA> = { exclusion: [255, 255, 255, 255], deflection: [255, 64, 200, 255], nohold: [255, 70, 70, 255], beach: [196, 160, 110, 255] };
const UNC_COL: RGBA = [200, 170, 255, 200];
const dash = new PathStyleExtension({ dash: true });
/** MultiPolygon -> outer/inner rings as paths (for dashed outlines) */
const rings = (coordinates: MultiPoly) => coordinates.flatMap((poly) => poly);
const DISP_COL: RGBA = [64, 156, 255, 80];
const BURN_COL: RGBA = [255, 96, 32, 90];
const SKIM_COL: RGBA = [110, 240, 140, 255];
const css = (c: RGBA) => `rgb(${c[0]},${c[1]},${c[2]})`;
const esc = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const mp = (coordinates: MultiPoly, props: any = {}) => ({ type: 'Feature', properties: props, geometry: { type: 'MultiPolygon', coordinates } });
const fc = (features: any[]) => ({ type: 'FeatureCollection', features });

/** "T+5 h" / "T+1.5 h" */
export const tplus = (h: number) => `T+${h < 10 && h % 1 ? h.toFixed(1) : Math.round(h)} h`;

export class ResponseMap {
  private iso: any;
  private unc: { t_h: number; path: number[][] }[] = [];
  private zonesBySteps = new Map<number, { disp: any; burn: any }>();
  private shoreLines: RespLayer['shore'];
  private shorePts: RespLayer['shore'];
  private boomLines: { path: [number, number][]; kind: string; deploy: number; mob: number; late: boolean }[] = [];
  private travel: { path: [number, number][]; mob: number; deploy: number }[] = [];
  private markers: maplibregl.Marker[] = [];
  private markerEls: { el: HTMLElement; showFrom: number }[] = [];
  private lastVis = '';

  constructor(public L: RespLayer, public key: string) {
    this.iso = fc(L.isochrones.map((i) => mp(i.coordinates, { t_h: i.t_h })));
    for (const u of L.uncertainty ?? []) for (const r of rings(u.coordinates)) this.unc.push({ t_h: u.t_h, path: r });
    for (const z of L.zones) {
      const k = Math.round(z.h);
      const e = this.zonesBySteps.get(k) ?? { disp: fc([]), burn: fc([]) };
      e[z.kind].features.push(mp(z.coordinates, { pct: z.pct }));
      this.zonesBySteps.set(k, e);
    }
    this.shoreLines = L.shore.filter((s) => s.path);
    this.shorePts = L.shore.filter((s) => s.point);
    for (const b of L.booms) {
      for (const ln of b.lines) this.boomLines.push({ path: ln.path, kind: ln.kind, deploy: b.deploy_by_h, mob: b.mobilize_by_h, late: b.late });
      const st = b.staging[0];
      if (st) this.travel.push({ path: [[st.lon, st.lat], [b.lon, b.lat]], mob: b.mobilize_by_h, deploy: b.deploy_by_h });
    }
  }

  /** deck.gl layers at hour tH after this run's release. */
  layers(tH: number): Layer[] {
    const L = this.L;
    const step = L.zone_step_h || 3;
    const sk = Math.floor(Math.max(0, tH) / step) * step;
    const zs = this.zonesBySteps.get(sk);
    const out: Layer[] = [];
    const tq = Math.round(tH * 4) / 4; // update triggers at 15 min resolution
    out.push(new GeoJsonLayer({
      id: 'resp-iso', data: this.iso as any, filled: false, stroked: true,
      getLineColor: (f: any) => { const t = f.properties.t_h; const c = BAND_COL[t <= 12 ? 0 : t <= 24 ? 1 : t <= 48 ? 2 : 3]; return [c[0], c[1], c[2], f.properties.t_h <= tH ? 150 : 70]; },
      lineWidthUnits: 'pixels', getLineWidth: (f: any) => (f.properties.t_h === 6 ? 0.8 : 1.3),
      updateTriggers: { getLineColor: tq }, parameters: ON_SURFACE,
    }) as any);
    if (this.unc.length) {
      // only the latest envelope reached by the clock (or the first one before that), dashed
      const hs = [...new Set(this.unc.map((u) => u.t_h))].sort((a, b) => a - b);
      const cur = hs.filter((h) => h <= Math.max(tH, hs[0])).pop() ?? hs[0];
      out.push(new PathLayer({
        id: 'resp-unc', data: this.unc.filter((u) => u.t_h === cur), getPath: (d: any) => d.path, getColor: UNC_COL,
        getWidth: 1.6, widthUnits: 'pixels', extensions: [dash], getDashArray: [5, 4], dashJustified: true,
        updateTriggers: { data: cur }, parameters: ON_SURFACE,
      } as any) as any);
    }
    if (L.circles?.length) {
      out.push(new PathLayer({
        id: 'resp-circles', data: L.circles, getPath: (d: any) => d.path,
        getColor: (d: any) => (d.kind === 'grand_isle' ? [255, 140, 80, 170] : [120, 190, 255, 150]),
        getWidth: 1.2, widthUnits: 'pixels', extensions: [dash], getDashArray: [2, 4], parameters: ON_SURFACE,
      } as any) as any);
    }
    if (L.dispersant_line?.length) {
      out.push(new PathLayer({
        id: 'resp-disp-line', data: L.dispersant_line, getPath: (d: any) => d, getColor: [120, 190, 255, 150],
        getWidth: 1.2, widthUnits: 'pixels', parameters: ON_SURFACE,
      }) as any);
    }
    if (zs) {
      out.push(new GeoJsonLayer({
        id: 'resp-zone-disp', data: zs.disp, filled: true, stroked: true, getFillColor: DISP_COL, getLineColor: [120, 190, 255, 230],
        lineWidthUnits: 'pixels', getLineWidth: 1.5, parameters: ON_SURFACE,
      }) as any);
      out.push(new GeoJsonLayer({
        id: 'resp-zone-burn', data: zs.burn, filled: true, stroked: true, getFillColor: BURN_COL, getLineColor: [255, 140, 80, 240],
        lineWidthUnits: 'pixels', getLineWidth: 1.5, parameters: ON_SURFACE,
      }) as any);
    }
    out.push(new PathLayer({
      id: 'resp-shore', data: this.shoreLines, getPath: (d: any) => d.path,
      getColor: (d: any) => { const c = BAND_COL[d.band]; return [c[0], c[1], c[2], d.eta_h <= tH ? 255 : 120]; },
      getWidth: (d: any) => (d.eta_h <= tH ? (d.impact === 'landfall' ? 4 : 3) : 2) + (d.tier === 1 ? 0.5 : 0),
      widthUnits: 'pixels', widthMinPixels: 1.5, capRounded: true, jointRounded: true,
      updateTriggers: { getColor: tq, getWidth: tq }, parameters: ON_SURFACE,
    }) as any);
    if (this.shorePts.length) {
      out.push(new ScatterplotLayer({
        id: 'resp-shore-pts', data: this.shorePts, getPosition: (d: any) => d.point, radiusUnits: 'pixels',
        getRadius: (d: any) => (d.eta_h <= tH ? 5 : 3.5), getFillColor: (d: any) => BAND_COL[d.band],
        updateTriggers: { getRadius: tq }, parameters: ON_SURFACE,
      }) as any);
    }
    out.push(new PathLayer({
      id: 'resp-travel', data: this.travel, getPath: (d: any) => d.path,
      getColor: (d: any) => (tH >= d.mob - 0.5 && tH <= d.deploy ? [170, 210, 255, 230] : [170, 200, 235, 70]),
      getWidth: (d: any) => (tH >= d.mob - 0.5 && tH <= d.deploy ? 2 : 1), widthUnits: 'pixels',
      updateTriggers: { getColor: tq, getWidth: tq }, parameters: ON_SURFACE,
    }) as any);
    out.push(new PathLayer({
      id: 'resp-boom', data: this.boomLines, getPath: (d: any) => d.path,
      getColor: (d: any) => { const c = BOOM_COL[d.kind]; return [c[0], c[1], c[2], tH >= d.deploy && !d.late ? 255 : 110]; },
      getWidth: (d: any) => (tH >= d.deploy && !d.late ? 5 : 2.5), widthUnits: 'pixels', capRounded: true, jointRounded: true,
      updateTriggers: { getColor: tq, getWidth: tq }, parameters: ON_SURFACE,
    }) as any);
    out.push(new ScatterplotLayer({
      id: 'resp-inlets', data: L.inlets, getPosition: (d: any) => [d.lon, d.lat], radiusUnits: 'pixels', getRadius: 6,
      filled: false, stroked: true, lineWidthUnits: 'pixels', getLineWidth: (d: any) => (tH >= d.deploy_by_h ? 3 : 1.5),
      getLineColor: [255, 255, 255, 230], updateTriggers: { getLineWidth: tq }, parameters: ON_SURFACE,
    }) as any);
    out.push(new ScatterplotLayer({
      id: 'resp-skim', data: L.skimmer_targets.filter((s) => Math.round(s.h) === sk), getPosition: (d: any) => [d.lon, d.lat],
      radiusUnits: 'pixels', getRadius: (d: any) => 11 - 2 * d.rank, filled: false, stroked: true, lineWidthUnits: 'pixels',
      getLineWidth: 2.5, getLineColor: (d: any) => (d.ok ? SKIM_COL : [110, 240, 140, 90]), parameters: ON_SURFACE,
    }) as any);
    out.push(new ScatterplotLayer({
      id: 'resp-bases', data: L.staging_bases, getPosition: (d: any) => [d.lon, d.lat], radiusUnits: 'pixels', getRadius: 3.5,
      getFillColor: (d: any) => (d.type.startsWith('uscg') ? [235, 240, 250, 230] : [150, 230, 255, 230]),
      stroked: true, getLineColor: [10, 20, 40, 255], lineWidthUnits: 'pixels', getLineWidth: 1, parameters: ON_SURFACE,
    }) as any);
    return out;
  }

  /** HTML labels for boom sites + used staging bases; visibility follows the clock. */
  /** returns true when label visibility changed (caller re-runs label collision handling) */
  syncMarkers(map: maplibregl.Map, tH: number, on: boolean): boolean {
    if (!this.markers.length && on) {
      for (const b of this.L.booms) {
        const el = document.createElement('div');
        el.className = 'rlbl';
        const c = css(BOOM_COL[b.kind]);
        el.style.setProperty('--c', c);
        const t1 = b.lead ? ` (Tier 1 ${b.lead.tier1_h} h${b.lead.hvp ? ', port area' : ''})` : '';
        el.innerHTML = b.late
          ? `<span><b class="late">too late for boom</b> · oil ashore ~${tplus(b.eta_h)} · boom on scene ~${tplus(b.deploy_by_h)}${t1} · shoreline crews</span>`
          : `<span>${esc(b.label)} · <b>on scene ${tplus(b.deploy_by_h)}</b>${t1}${b.across_kt != null ? ` · ${b.across_kt} kt across` : ''}</span>`;
        el.title = `${b.name ?? ''}: ESI ${b.esi} ${b.esi_label}. Oil predicted at ${tplus(b.eta_h)}`
          + (b.eta_earliest_h != null ? ` (earliest plausible ${tplus(b.eta_earliest_h)})` : '')
          + `; Tier 1 en route by ${tplus(b.mobilize_by_h)}${b.staging[0] ? ` from ${b.staging[0].name} (~${b.staging[0].travel_h} h by road)` : ''}.`
          + (b.current_kt != null ? ` Forecast current ${b.current_kt} kt, ${b.across_kt ?? '?'} kt across the boom.` : '')
          + (b.rule ? ` ${b.rule}` : '');
        this.markers.push(new maplibregl.Marker({ element: el, anchor: 'top-left', offset: [6, 10] }).setLngLat([b.lon, b.lat]).addTo(map));
        this.markerEls.push({ el, showFrom: -1e9 });
      }
      const used = new Map<string, Staging>();
      for (const b of this.L.booms) if (b.staging[0]) used.set(b.staging[0].name, b.staging[0]);
      for (const o of this.L.staging_ops) used.set(o.base.name, o.base);
      for (const s of used.values()) {
        const el = document.createElement('div');
        el.className = 'rlbl base';
        el.innerHTML = `<span>${esc(s.name)}</span>`;
        this.markers.push(new maplibregl.Marker({ element: el, anchor: 'left', offset: [6, 0] }).setLngLat([s.lon, s.lat]).addTo(map));
        this.markerEls.push({ el, showFrom: -1e9 });
      }
    }
    const vis = on ? 'on' : 'off';
    void tH;
    if (vis === this.lastVis) return false;
    this.lastVis = vis;
    for (const m of this.markerEls) m.el.style.display = on ? '' : 'none';
    return true;
  }

  remove() {
    this.markers.forEach((m) => m.remove());
    this.markers = [];
    this.markerEls = [];
  }

  /** Upcoming actions at hour tH (the next few + anything happening right now). */
  nextActions(tH: number, n = 6) {
    return this.L.timeline.filter((e) => e.h >= tH - 0.5).slice(0, n);
  }
}

/** Legend + toggle for the panel's response section. */
export function respLayerHTML(L: RespLayer | undefined, on: boolean) {
  if (!L) return '';
  if (L.error) return `<div class="err">Response layer: ${esc(L.error)}</div>`;
  const sw = (c: RGBA, w = 14, h = 4) => `<i class="sw" style="width:${w}px;height:${h}px;background:${css(c)}"></i>`;
  return `<div class="rl-box">
    <label class="rl-tog"><input type="checkbox" id="resp-layer-cb" ${on ? 'checked' : ''}> <b>Response layer</b> <small class="hint">on the map, follows the clock</small></label>
    ${on ? `<div class="rl-leg">
      <div><span class="hint">Oil arrival</span> ${L.bands.map((b, i) => `${sw(BAND_COL[i])}${esc(b)}`).join(' ')}</div>
      <div>${sw(BOOM_COL.exclusion, 14, 5)}exclusion boom (&le;0.7 kt across) ${sw(BOOM_COL.deflection, 14, 5)}deflection (0.7–1 kt) ${sw(BOOM_COL.nohold, 14, 5)}won't hold (&gt;1 kt) ${sw(BOOM_COL.beach, 14, 5)}beach crews <i class="sw ring" style="border-color:#fff"></i>inlet</div>
      <div><i class="sw dashl" style="border-color:${css(UNC_COL)}"></i><b>best guess + uncertainty</b>: solid = this run, dashed = could be reached with 1–4 % windage (NOAA-style) <i class="sw dashl" style="border-color:#78beff"></i>10 nm dispersant circle${L.circles?.some((c) => c.kind === 'grand_isle') ? ' <i class="sw dashl" style="border-color:#ff8c50"></i>Grand Isle 7 mi ISB buffer' : ''}</div>
      <div><i class="sw box" style="background:rgba(64,156,255,.5);border:1px solid #78beff"></i>dispersant zone <i class="sw box" style="background:rgba(255,96,32,.5);border:1px solid #ff8c50"></i>in-situ burn <i class="sw ring" style="border-color:${css(SKIM_COL)}"></i>skimmer target <i class="sw dot"></i>staging base ${sw([120, 190, 255, 255], 14, 2)}3 nm / 10 m dispersant line</div>
      <div class="hint">Faint = planned; bright = boom on scene (USCG Tier 1 lead time) / oil arrived; blue lines = road travel from the equipment base. Thin outlines = where surface oil has been by T+6/12/24/48/72 h.</div>
      ${L.notes.map((n) => `<div class="warn">${esc(n)}</div>`).join('')}
    </div>` : ''}
  </div><div id="next-actions"></div>`;
}

export function nextActionsHTML(rm: ResponseMap | null, tH: number) {
  if (!rm) return '';
  const ev = rm.nextActions(tH);
  if (!ev.length) return `<div class="label ol-h">Next actions</div><div class="hint">No more planned actions in this forecast window.</div>`;
  const icon: Record<string, string> = {
    mobilize: '🚚', deploy: '〰', inlet: '⛔', landfall: '⚠', late: '⚠', disp_open: '✈', disp_close: '✈', burn_open: '🔥', burn_close: '🔥', mech_open: '⛴', mech_close: '⛴',
  };
  return `<div class="label ol-h">Next actions <small>${tplus(Math.max(0, tH))} now · click to fly there</small></div><div class="ol-list">${ev.map((e) => {
    const dt = e.h - tH;
    return `<button class="ol-shore na k-${esc(e.kind)}" data-lon="${e.lon}" data-lat="${e.lat}"><b>${icon[e.kind] ?? '•'}</b><span>${esc(e.text)}</span><em>${tplus(e.h)}<small>${dt > 0.25 ? ` in ${dt < 10 ? dt.toFixed(1) : Math.round(dt)} h` : ' now'}</small></em></button>`;
  }).join('')}</div>`;
}
