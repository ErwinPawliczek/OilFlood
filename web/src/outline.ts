// First-responder "Response outline" (T16): fetch GET /api/spill/{id}/response (prediction) or
// /api/spills/{id}/response?model= (historical hindcast) and render it as a collapsible panel section.

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
  boom_first: { priority: number; name: string | null; lat: number; lon: number; eta_h: number; esi: string; esi_label: string; action: string; lead_time_ok: boolean; note: string }[];
  tactics: Record<string, { tactic: string; feasible: boolean; windows: { start: string; end: string; from_h: number; to_h: number; hours: number }[]; total_hours: number; not_applicable: string | null; conditions: string[]; notes: string[] }>;
  conditions: { wind_kt: { min: number; mean: number; max: number } | null; current_kt: { mean: number; max: number } | null };
  closures: { type: string; what: string; who?: string; eta_h?: number }[];
  precedents: { similar_spills: { name: string; year: string; oil: string; volume_bbl: number; distance_km: number; outcome: string | null; src: string | null }[]; by_tactic: Record<string, { spill: string; date: string; action: string; quote?: string; src: string }[]> };
  sources: { what: string; ref: string; url: string }[];
  limits: string[];
  briefing?: { text: string | null; note?: string; provider?: string; model?: string };
}
export interface Shore {
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

export function outlineHTML(o: Outline | null, st: { loading?: string | null; error?: string | null }, open: boolean) {
  let body = '';
  if (st.error) body = `<div class="err">${escO(st.error)}</div>`;
  else if (!o) body = `<div class="hint">${escO(st.loading || 'Waiting for the forecast run…')}</div>`;
  else {
    body += `<div class="disc">${escO(o.disclaimer)}</div>`;
    const b = o.briefing;
    if (b?.text) body += `<div class="brief"><div class="label">Briefing <small>(${escO(b.provider)} ${escO(b.model)}, written from the outline below)</small></div>${escO(b.text).replace(/\n/g, '<br>')}</div>`;
    body += `<div class="resp-out">${o.headline.map(escO).join(' ')}</div>`;
    body += `<div class="kv" style="margin-top:4px">
      <span>Oil</span><span>${escO(o.spill.oil_class_label)} · ${Number(o.spill.volume_bbl).toLocaleString()} bbl</span>
      <span>First landfall</span><span>${o.eta_first_landfall_h != null ? `<b>${fmtH(o.eta_first_landfall_h)}</b> <small class="hint">${utc(o.eta_first_landfall!)}</small>` : `none in ${Math.round(o.spill.horizon_h)} h`}</span>
      ${o.conditions.wind_kt ? `<span>Wind</span><span>${o.conditions.wind_kt.mean} kt mean, max ${o.conditions.wind_kt.max} kt</span>` : ''}
      ${o.conditions.current_kt ? `<span>Current</span><span>${o.conditions.current_kt.mean} kt mean, max ${o.conditions.current_kt.max} kt</span>` : ''}
    </div>`;
    if (o.priority_shorelines.length) {
      body += `<div class="label ol-h">Priority shorelines <small>NOAA ESI + arrival</small></div><div class="ol-list">${o.priority_shorelines.slice(0, 6).map((s) => `
        <button class="ol-shore" data-lon="${s.lon}" data-lat="${s.lat}" title="${escO(s.shoreline_tactic)}">
          <b style="color:${TIER_COL[s.sensitivity_tier]}">${s.priority}</b>
          <span>${escO(s.name ?? `${s.lat}, ${s.lon}`)} <small>ESI ${escO(s.esi_most_sensitive)} ${escO(s.esi_most_sensitive_label)}</small></span>
          <em>${s.impact === 'landfall' ? `${fmtH(s.eta_h)} · ${s.particles_pct}%` : `near in ${fmtH(s.eta_h)}`}</em>
        </button>`).join('')}</div>`;
      if (o.shorelines_total > 6) body += `<div class="hint">+${o.shorelines_total - 6} more stretches in the JSON.</div>`;
    }
    if (o.boom_first.length) {
      body += `<div class="label ol-h">Boom first</div>${o.boom_first.slice(0, 3).map((x) => `
        <div class="ol-item"><b>${escO(x.name ?? '')}</b>: ${escO(x.action)}. <span class="${x.lead_time_ok ? 'hint' : 'warn-t'}">${escO(x.note)}</span></div>`).join('')}`;
    }
    body += `<div class="label ol-h">Windows <small>(hours after release)</small></div>`;
    for (const t of Object.values(o.tactics)) {
      const w = t.windows.map((x) => `${Math.round(x.from_h)}–${Math.round(x.to_h + 1)} h`).join(', ');
      body += `<div class="ol-item" title="${escO(t.conditions.join('\n'))}"><b>${escO(t.tactic)}</b>: ${t.not_applicable ? `<span class="hint">${escO(t.not_applicable)}</span>` : t.feasible ? `<span class="ok-t">${w}</span> <small class="hint">${t.total_hours} h total</small>` : '<span class="warn-t">no window in this forecast (wind, daylight, distance from shore or oil age)</span>'}</div>`;
    }
    if (o.closures.length) body += `<div class="label ol-h">Closures / notifications</div>${o.closures.map((c) => `<div class="ol-item">${escO(c.what)}${c.eta_h != null ? ` <small class="hint">(oil within 15 km in ${fmtH(c.eta_h)})</small>` : ''}${c.who ? ` <small class="hint">· ${escO(c.who)}</small>` : ''}</div>`).join('')}`;
    const cats = Object.entries(o.precedents.by_tactic);
    if (cats.length) {
      body += `<details class="ol-sub"><summary>Precedents <small>${o.precedents.similar_spills.map((s) => `${escO(s.name)} ${s.year}`).slice(0, 3).join(' · ')}</small></summary>${cats.map(([c, acts]) => `
        <div class="ol-item"><b>${escO(CAT_LABEL[c] ?? c)}</b>: ${acts.map((a) => `<span title="${escO(a.quote ? `“${a.quote}”` : '')}">${escO(a.spill)} (${escO(a.date)}): ${escO(a.action)} <a href="${escO(a.src)}" target="_blank" rel="noopener">src</a></span>`).join(' · ')}</div>`).join('')}</details>`;
    }
    body += `<details class="ol-sub"><summary>Sources &amp; limits</summary>${o.sources.map((s) => `<div class="ol-item">${escO(s.what)}: <a href="${escO(s.url)}" target="_blank" rel="noopener">${escO(s.ref)}</a></div>`).join('')}
      <ul class="ol-lim">${o.limits.map((l) => `<li>${escO(l)}</li>`).join('')}</ul>
      ${b && !b.text && b.note ? `<div class="hint">${escO(b.note)}</div>` : ''}</details>`;
  }
  return `<details class="resp outline" data-k="outline" ${open ? 'open' : ''}><summary>Response outline <small>rule-based · not operational guidance</small></summary>${body}</details>`;
}
