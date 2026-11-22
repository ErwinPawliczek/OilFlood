// First-responder "Response outline" (T16): fetch GET /api/spill/{id}/response (prediction) or
// /api/spills/{id}/response?model= (historical hindcast) and render it as a collapsible panel section.

import { respLayerHTML, type RespLayer } from './responseLayer';

const escO = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

export interface Outline {
  disclaimer: string;
  headline: string[];
  spill: { oil_class_label: string; horizon_h: number; volume_bbl: number; start: string; model: string };
  eta_first_landfall_h: number | null;
  eta_first_landfall: string | null;
  stranded_pct_end: number | null;
  priority_shorelines: Shore[];
  shorelines_total: number;
  boom_first: { priority: number; name: string | null; lat: number; lon: number; eta_h: number; eta_earliest_h?: number; esi: string; esi_label: string; action: string; lead_time_ok: boolean; note: string; lead?: Lead }[];
  tactics: Record<string, { tactic: string; feasible: boolean; windows: { start: string; end: string; from_h: number; to_h: number; hours: number }[]; total_hours: number; not_applicable: string | null; conditions: string[]; notes: string[]; gates?: Gate[]; precedents?: Prec[] }>;
  boom_precedents?: Prec[]; shoreline_precedents?: Prec[]; closure_precedents?: Prec[];
  uncertainty?: { label: string; method: string; skill: string; envelope_only_shoreline: { cells: number; shoreline_km: number; earliest_h: number | null; places: string[] } };
  oil?: { adios: string; api: number | null; pour_point_c: number | null; api_band: string; continuous_release: boolean; emulsion_pct: Record<string, number>; window_note: string };
  protocol?: { doc_url: string; steps: { step: string; text: string; this_run: string; rules: string[] }[] };
  model_vs_reality?: { incident: string; text: string; url: string | null }[];
  conditions: { wind_kt: { min: number; mean: number; max: number } | null; current_kt: { mean: number; max: number } | null };
  closures: { type: string; what: string; who?: string; eta_h?: number; basis?: string }[];
  precedents: { similar_spills: { name: string; year: string; oil: string; volume_bbl: number; distance_km: number; outcome: string | null; src: string | null }[]; by_tactic: Record<string, { spill: string; date: string; action: string; quote?: string; src: string }[]> };
  sources: { what: string; ref: string; url: string }[];
  limits: string[];
  briefing?: { text: string | null; note?: string; provider?: string; model?: string };
  /** map geometry for the response layer (T21) */
  layer?: RespLayer;
}
interface Lead { lead_h: number; tier1_h: number; hvp: string | null; travel_h: number | null; base: string | null }
interface Gate { key: string; label: string; limit: string; rules: string[]; soft: boolean; pass_h: number; fail_h: number; unknown_h: number; first_value: string | null; first_fail: { h: number; value: string } | null }
interface Prec { incident: string; text: string; url: string | null }
export interface Shore {
  eta_earliest_h?: number;
  priority: number; name: string | null; state: string; lat: number; lon: number; impact: 'landfall' | 'threatened';
  eta_h: number; eta: string; particles_pct: number; stranded_bbl: number; shoreline_km: number; wetland_km: number;
  esi_most_sensitive: string; esi_most_sensitive_label: string; esi_dominant_label: string; sensitivity_tier: number; shoreline_tactic: string;
}

/** Poll until the run is done (202 while running). Returns null if superseded. */
export async function fetchOutline(url: string, isStale: () => boolean): Promise<Outline | null> {
  for (let i = 0; i < 600; i++) {
    if (isStale()) return null;
    const r = await fetch(url);
    const body = await r.json().catch(() => null);
    if (r.status === 200 && body?.headline) return body as Outline;
    if (r.status === 202) { await new Promise((res) => setTimeout(res, 1500)); continue; }
    throw new Error(body?.detail || body?.error || `Response outline failed (${r.status})`);
  }
  return null;
}

const TIER_COL = ['', '#ff6b6b', '#ffb547', '#8fb8ff'];
const fmtH = (h: number) => (h < 1 ? '<1 h' : `${Math.round(h)} h`);
const utc = (s: string) => s.replace(/^\d{4}-(\d{2})-(\d{2})T(\d{2}:\d{2})Z$/, '$1-$2 $3Z');
const CAT_LABEL: Record<string, string> = {
  closure: 'Closures', boom: 'Boom', skimming: 'Skimming', dispersant: 'Dispersant', in_situ_burn: 'In-situ burn',
  shoreline_cleanup: 'Shoreline cleanup', wildlife: 'Wildlife',
};

const precHTML = (ps?: Prec[], label = 'Real responders') => (ps?.length ? `<div class="ol-prec"><span class="hint">${label}:</span> ${ps.map((p) => `<span title="${escO(p.text)}"><b>${escO(p.incident)}</b>: ${escO(p.text.length > 150 ? `${p.text.slice(0, 147)}…` : p.text)}${p.url ? ` <a href="${escO(p.url)}" target="_blank" rel="noopener">src</a>` : ''}</span>`).join(' · ')}</div>` : '');

/** gate chips: green = passed every hour with oil, red = blocked some hours, grey = not in the forecast (check) */
const gatesHTML = (gs?: Gate[]) => (gs?.length ? `<div class="ol-gates">${gs.map((g) => {
  const cls = g.fail_h === 0 && g.pass_h > 0 ? 'g-ok' : g.pass_h === 0 && g.fail_h === 0 ? 'g-unk' : g.soft ? 'g-soft' : g.pass_h === 0 ? 'g-no' : 'g-part';
  const tip = `${g.label}: ${g.limit} [${g.rules.join(', ')}]. ${g.pass_h} h pass, ${g.fail_h} h fail, ${g.unknown_h} h not in forecast.${g.first_fail ? ` First fails at T+${Math.round(g.first_fail.h)} h (${g.first_fail.value}).` : g.first_value ? ` Now: ${g.first_value}.` : ''}${g.soft ? ' Advisory only.' : ''}`;
  return `<span class="gate ${cls}" title="${escO(tip)}">${escO(g.label.replace(/ \(.*\)$/, ''))}</span>`;
}).join('')}</div>` : '');

export function outlineHTML(o: Outline | null, st: { loading?: string | null; error?: string | null }, open: Record<string, boolean>, layerOn = true) {
  let body = '';
  if (st.error) body = `<div class="err">${escO(st.error)}</div>`;
  else if (!o) body = `<div class="hint">${escO(st.loading || 'Waiting for the forecast run…')}</div>`;
  else {
    body += `<div class="disc">${escO(o.disclaimer)}</div>`;
    const b = o.briefing;
    if (b?.text) body += `<div class="brief"><div class="label">Briefing <small>(${escO(b.provider)} ${escO(b.model)}, written from the outline below)</small></div>${escO(b.text).replace(/\n/g, '<br>')}</div>`;
    body += `<div class="resp-out">${o.uncertainty ? `<span class="unc-tag" title="${escO(o.uncertainty.method)}">${escO(o.uncertainty.label)}</span> ` : ''}${o.headline.map(escO).join(' ')}</div>`;
    if (o.protocol) {
      body += `<details class="ol-sub how" data-k="how" ${open.how ? 'open' : ''}><summary>How we go from model to action</summary><ol class="ol-how">${o.protocol.steps.map((st) => `<li><b>${escO(st.step)}</b>: ${escO(st.text)} <small class="hint">This run: ${escO(st.this_run)} · ${escO(st.rules.join(', '))}</small></li>`).join('')}</ol><div class="hint">Every threshold cites a rule in <a href="${escO(o.protocol.doc_url)}" target="_blank" rel="noopener">docs/PROTOCOLS.md</a> (RRT-6, NOAA, USCG / 33 CFR). The FOSC / Unified Command decides.</div></details>`;
    }
    body += respLayerHTML(o.layer, layerOn);
    body += `<div class="kv" style="margin-top:4px">
      <span>Oil</span><span>${escO(o.spill.oil_class_label)} · ${Number(o.spill.volume_bbl).toLocaleString()} bbl${o.oil ? ` <small class="hint" title="${escO(`${o.oil.adios}; pour point ${o.oil.pour_point_c} °C (NOAA ADIOS). ${o.oil.window_note}`)}">${escO(o.oil.api_band)}${o.oil.continuous_release ? ' · continuous release' : Object.keys(o.oil.emulsion_pct).length ? ` · water in emulsion ${Object.entries(o.oil.emulsion_pct).map(([h, v]) => `${v}% @${h} h`).join(', ')}` : ''}</small>` : ''}</span>
      <span>First landfall</span><span>${o.eta_first_landfall_h != null ? `<b>${fmtH(o.eta_first_landfall_h)}</b> <small class="hint">${utc(o.eta_first_landfall!)}</small>` : `none in ${Math.round(o.spill.horizon_h)} h`}</span>
      ${o.conditions.wind_kt ? `<span>Wind</span><span>${o.conditions.wind_kt.mean} kt mean, max ${o.conditions.wind_kt.max} kt</span>` : ''}
      ${o.conditions.current_kt ? `<span>Current</span><span>${o.conditions.current_kt.mean} kt mean, max ${o.conditions.current_kt.max} kt</span>` : ''}
    </div>`;
    if (o.priority_shorelines.length) {
      body += `<div class="label ol-h">Priority shorelines <small>NOAA ESI + arrival</small></div><div class="ol-list">${o.priority_shorelines.slice(0, 6).map((s) => `
        <button class="ol-shore" data-lon="${s.lon}" data-lat="${s.lat}" title="${escO(s.shoreline_tactic)}">
          <b style="color:${TIER_COL[s.sensitivity_tier]}">${s.priority}</b>
          <span>${escO(s.name ?? `${s.lat}, ${s.lon}`)} <small>ESI ${escO(s.esi_most_sensitive)} ${escO(s.esi_most_sensitive_label)}</small></span>
          <em>${s.impact === 'landfall' ? `${fmtH(s.eta_h)} · ${s.particles_pct}%` : `near in ${fmtH(s.eta_h)}`}${s.eta_earliest_h != null && s.eta_earliest_h < s.eta_h - 0.5 ? `<small> earliest ${fmtH(s.eta_earliest_h)}</small>` : ''}</em>
        </button>`).join('')}</div>`;
      if (o.shorelines_total > 6) body += `<div class="hint">+${o.shorelines_total - 6} more stretches in the JSON.</div>`;
      body += `<div class="hint">ICS-232-style list from NOAA ESI only: defer to the ACP / GRP priority protection areas (R8). "earliest" = oil within 10 km in the uncertainty envelope.</div>`;
    }
    const eo = o.uncertainty?.envelope_only_shoreline;
    if (eo && eo.cells > 0) body += `<div class="ol-item warn-t">Uncertainty envelope also reaches ${eo.places.slice(0, 4).map(escO).join(', ') || 'more shoreline'} (earliest ~${fmtH(eo.earliest_h ?? 0)}), not hit in the best guess.</div>`;
    if (o.boom_first.length) {
      body += `<div class="label ol-h">Boom first <small>lead time = USCG Tier 1 (6 h port area / 12 h elsewhere) vs arrival</small></div>${o.boom_first.slice(0, 3).map((x) => `
        <div class="ol-item"><b>${escO(x.name ?? '')}</b>: ${escO(x.action)}. <span class="${x.lead_time_ok ? 'hint' : 'warn-t'}">${escO(x.note)}</span>${x.lead ? ` <small class="hint" title="${escO(`notify 0.5 h + en route 2 h + ${x.lead.travel_h ?? '?'} h road from ${x.lead.base ?? 'nearest base'}; floor ${x.lead.tier1_h} h ${x.lead.hvp ? `(higher-volume port area: ${x.lead.hvp})` : '(outside higher-volume port areas)'}; 33 CFR 154.1045`)}">[MECH-6]</small>` : ''}</div>`).join('')}`;
      body += precHTML(o.boom_precedents);
    }
    body += `<div class="label ol-h">Windows <small>(hours after release; chips = protocol gates, hover for limit + source)</small></div>`;
    for (const t of Object.values(o.tactics)) {
      const w = t.windows.map((x) => `${Math.round(x.from_h)}–${Math.round(x.to_h + 1)} h`).join(', ');
      const block = (t.gates ?? []).filter((g) => !g.soft && g.pass_h === 0 && g.fail_h > 0).map((g) => g.label.toLowerCase());
      body += `<div class="ol-item ol-tac" title="${escO([...t.conditions, ...t.notes].join('\n'))}"><b>${escO(t.tactic)}</b>: ${t.not_applicable ? `<span class="hint">${escO(t.not_applicable)}</span>` : t.feasible ? `<span class="ok-t">${w}</span> <small class="hint">${t.total_hours} h total</small>` : `<span class="warn-t">no window in this forecast${block.length ? ` (blocked: ${escO(block.join(', '))})` : ''}</span>`}${t.not_applicable ? '' : gatesHTML(t.gates)}${precHTML(t.precedents)}</div>`;
    }
    if (o.closures.length) body += `<div class="label ol-h">Closures / notifications</div>${o.closures.map((c) => `<div class="ol-item">${escO(c.what)}${c.eta_h != null ? ` <small class="hint">(oil within 15 km in ${fmtH(c.eta_h)})</small>` : ''}${c.who ? ` <small class="hint">· ${escO(c.who)}</small>` : ''}${c.basis ? ` <small class="hint" title="${escO(c.basis)}">ⓘ</small>` : ''}</div>`).join('')}${precHTML(o.closure_precedents)}`;
    const cats = Object.entries(o.precedents.by_tactic);
    if (cats.length) {
      body += `<details class="ol-sub" data-k="prec" ${open.prec ? 'open' : ''}><summary>Precedents <small>${o.precedents.similar_spills.map((s) => `${escO(s.name)} ${s.year}`).slice(0, 3).join(' · ')}</small></summary>${cats.map(([c, acts]) => `
        <div class="ol-item"><b>${escO(CAT_LABEL[c] ?? c)}</b>: ${acts.map((a) => `<span title="${escO(a.quote ? `“${a.quote}”` : '')}">${escO(a.spill)} (${escO(a.date)}): ${escO(a.action)} <a href="${escO(a.src)}" target="_blank" rel="noopener">src</a></span>`).join(' · ')}</div>`).join('')}</details>`;
    }
    if (o.model_vs_reality?.length || o.uncertainty) {
      body += `<details class="ol-sub" data-k="mvr" ${open.mvr ? 'open' : ''}><summary>Forecast vs reality <small>why we show uncertainty</small></summary>${o.uncertainty ? `<div class="ol-item">${escO(o.uncertainty.method)}</div><div class="ol-item hint">${escO(o.uncertainty.skill)}</div>` : ''}${(o.model_vs_reality ?? []).map((m) => `<div class="ol-item"><b>${escO(m.incident)}</b>: ${escO(m.text)}${m.url ? ` <a href="${escO(m.url)}" target="_blank" rel="noopener">src</a>` : ''}</div>`).join('')}${precHTML(o.shoreline_precedents, 'Shoreline phase')}</details>`;
    }
    if (o.layer && !o.layer.error) {
      body += `<details class="ol-sub" data-k="rules" ${open.rules ? 'open' : ''}><summary>Map layer rules &amp; assumptions</summary><ul class="ol-lim">${o.layer.rules.map((r) => `<li>${escO(r)}</li>`).join('')}</ul></details>`;
    }
    body += `<details class="ol-sub" data-k="srcs" ${open.srcs ? 'open' : ''}><summary>Sources &amp; limits</summary>${[...o.sources, ...(o.layer?.sources ?? [])].map((s) => `<div class="ol-item">${escO(s.what)}: <a href="${escO(s.url)}" target="_blank" rel="noopener">${escO(s.ref)}</a></div>`).join('')}
      <ul class="ol-lim">${o.limits.map((l) => `<li>${escO(l)}</li>`).join('')}</ul>
      ${b && !b.text && b.note ? `<div class="hint">${escO(b.note)}</div>` : ''}</details>`;
  }
  return `<details class="resp outline" data-k="outline" ${open.outline ? 'open' : ''}><summary>Response outline <small>rule-based · not operational guidance</small></summary>${body}</details>`;
}
