// Time-series action document (T27): GET .../actions (backend/app/actions.py) rendered as a compact table in step 5.
// Rows are on the run's clock; a click moves the time slider there. Export = Markdown / CSV downloads and a
// print-friendly HTML page from the same endpoint (each carries the "not operational guidance" disclaimer).

export interface ActionRow {
  h: number; kind: string; tplus: string; t_utc: string; t_local: string; action: string; where: string; why: string;
  rules: string; confidence: string; p: number | null; lon: number; lat: number;
}
export interface Actions {
  version: string; disclaimer: string; timezone: string; spill: { start: string; horizon_h: number };
  ensemble: { members: number } | null; rows: ActionRow[]; rules_doc: string;
}

const esc = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

const ICON: Record<string, string> = {
  start: '●', notify: '☎', overflight: '✈', mobilize: '🚚', safety: '⛔', boom_on_scene: '〰', tactic_open: '▶', tactic_close: '■',
  tier: '⬆', closure: '⛔', eta_p10: '⚠', deadline: '⏰', eta_p50: '⚠', landfall: '⚠', reforecast: '↻', late: '⚠', end: '⏹',
};

export async function fetchActions(url: string): Promise<Actions | null> {
  for (let i = 0; i < 400; i++) {
    const r = await fetch(url);
    const b = await r.json().catch(() => null);
    if (r.status === 200 && b?.rows) return b as Actions;
    if (r.status === 202) { await new Promise((res) => setTimeout(res, 1500)); continue; }
    throw new Error(b?.detail || `Action timeline failed (${r.status})`);
  }
  return null;
}

/** Step 5 block. `url` = the JSON endpoint (exports add ?format=). */
export function actionsHTML(a: Actions | null, url: string, st: { loading?: string | null; error?: string | null }, open: boolean) {
  const exp = url ? (f: string) => `${url}${url.includes('?') ? '&' : '?'}format=${f}` : () => '#';
  let body: string;
  if (st.error) body = `<div class="err">${esc(st.error)}</div>`;
  else if (!a) body = `<div class="hint">${esc(st.loading || 'Building the action timeline…')}</div>`;
  else {
    body = `<div class="act-disc">${esc(a.disclaimer)}</div>
    <div class="act-exp"><span class="hint">Export</span>
      <a class="pill" href="${esc(exp('md'))}" download>Markdown</a>
      <a class="pill" href="${esc(exp('csv'))}" download>CSV</a>
      <a class="pill" href="${esc(exp('html'))}" target="_blank" rel="noopener">Print</a></div>
    <div class="act-head"><span>Time <small>${esc(a.timezone.replace(/^America\//, ''))}</small></span><span>Action · where · why [rule] · confidence</span></div>
    <div class="act-tbl" id="act-tbl">${a.rows.map((r, i) => `
      <button class="act-row k-${esc(r.kind)}" data-i="${i}" data-h="${r.h}" title="${esc(`${r.t_utc} · ${r.where}\n${r.why} [${r.rules}]\nConfidence: ${r.confidence}`)}">
        <span class="act-t"><b>${esc(r.tplus)}</b><small>${esc(r.t_local.replace(/^\w{3} /, ''))}</small></span>
        <span class="act-a"><b><i>${ICON[r.kind] ?? '•'}</i> ${esc(r.action)}</b>
          <small>${r.where && !r.action.includes(r.where.replace(/ \(ESI.*$/, '')) ? `${esc(r.where)} · ` : ''}${esc(r.why)} <em>[${esc(r.rules)}]</em></small>
          <small class="act-c">${r.p != null ? `<span class="pq" style="--p:${r.p}">${Math.round(r.p * 100)}%</span> ` : ''}${esc(r.confidence)}</small></span>
      </button>`).join('')}</div>
    <div class="hint">Click a row to move the clock there. Rows: protocol checkpoints (notification, Tier 1/2/3, re-forecast every 6–12 h), boom on scene vs the earliest plausible arrival, tactic windows when the gates pass, shoreline P10/P50 arrival. Rule IDs: <a href="${esc(a.rules_doc)}" target="_blank" rel="noopener">PROTOCOLS.md</a>.</div>`;
  }
  return `<details class="ol-sub acts" data-k="acts" ${open ? 'open' : ''}><summary>Action timeline <small>what to do when, from this forecast</small></summary>${body}</details>`;
}

/** Mark past / current rows for hour tH (run clock). Cheap: only touches rows whose state changed. */
let lastMark = '';
export function markActions(root: HTMLElement, a: Actions | null, tH: number) {
  if (!a) return;
  const tbl = root.querySelector<HTMLElement>('#act-tbl');
  if (!tbl) return;
  // "now" = the latest row at or before tH (rows within 0.25 h of each other share it)
  let cur = -1;
  for (let i = 0; i < a.rows.length; i++) if (a.rows[i].h <= tH + 1e-6) cur = i;
  const key = `${cur}:${tbl.childElementCount}`;
  if (key === lastMark) return;
  lastMark = key;
  const curH = cur >= 0 ? a.rows[cur].h : -1;
  tbl.querySelectorAll<HTMLElement>('.act-row').forEach((el) => {
    const h = Number(el.dataset.h);
    el.classList.toggle('past', h < curH - 1e-6);
    el.classList.toggle('now', cur >= 0 && Math.abs(h - curH) < 1e-6);
  });
  const now = tbl.querySelector<HTMLElement>('.act-row.now');
  if (now) tbl.scrollTop = Math.max(0, now.offsetTop - tbl.offsetTop - 40);
}
export function resetActionMarks() { lastMark = ''; }
