// The app's single floating window: drag by its header, position + collapsed state kept in localStorage.

import { SPEED_RAMP } from './flow';
import { PALETTES } from './oil';
import type { CatalogSpill, ResponseCategory } from './types';

const KEY = 'oilflood.panel.v1';

interface Saved { x: number; y: number; collapsed: boolean }

function load(): Saved | null {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || 'null');
    return s && Number.isFinite(s.x) && Number.isFinite(s.y) ? s : null;
  } catch {
    return null;
  }
}

function save(s: Saved) {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* private mode */ }
}

/** Wires dragging/collapsing on `panel` (needs a `.ph` header and a `.collapse` button). */
export function initPanel(panel: HTMLElement) {
  const header = panel.querySelector<HTMLElement>('.ph')!;
  const btn = panel.querySelector<HTMLButtonElement>('.collapse')!;
  const st: Saved = load() ?? { x: innerWidth - 336 - 16, y: 16, collapsed: false };

  const clamp = () => {
    const w = panel.offsetWidth, h = header.offsetHeight;
    st.x = Math.min(Math.max(0, st.x), Math.max(0, innerWidth - w));
    st.y = Math.min(Math.max(0, st.y), Math.max(0, innerHeight - h));
    panel.style.left = `${st.x}px`;
    panel.style.top = `${st.y}px`;
    // keep the body inside the viewport
    panel.style.setProperty('--maxh', `${Math.max(160, innerHeight - st.y - 16)}px`);
  };
  const setCollapsed = (c: boolean) => {
    st.collapsed = c;
    panel.classList.toggle('collapsed', c);
    btn.title = c ? 'Expand' : 'Collapse';
    btn.setAttribute('aria-expanded', String(!c));
  };
  setCollapsed(st.collapsed);
  clamp();

  btn.addEventListener('click', () => { setCollapsed(!st.collapsed); clamp(); save(st); });

  let drag: { id: number; dx: number; dy: number } | null = null;
  header.addEventListener('pointerdown', (e) => {
    if ((e.target as HTMLElement).closest('button, input, a') || e.button !== 0) return;
    drag = { id: e.pointerId, dx: e.clientX - st.x, dy: e.clientY - st.y };
    header.setPointerCapture(e.pointerId);
    panel.classList.add('dragging');
    e.preventDefault();
  });
  header.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    st.x = e.clientX - drag.dx;
    st.y = e.clientY - drag.dy;
    clamp();
  });
  const end = (e: PointerEvent) => {
    if (!drag || e.pointerId !== drag.id) return;
    drag = null;
    panel.classList.remove('dragging');
    save(st);
  };
  header.addEventListener('pointerup', end);
  header.addEventListener('pointercancel', end);
  header.addEventListener('dblclick', (e) => {
    if ((e.target as HTMLElement).closest('button')) return;
    setCollapsed(!st.collapsed); clamp(); save(st);
  });
  addEventListener('resize', clamp);
}

/** Speed colour ramp legend for the current streaks. */
export function legendHTML(show: boolean, sourceLine: string, models: ('openoil' | 'ours')[] = ['openoil']) {
  const oil = models.map((m) => `<span><i style="background:${PALETTES[m].css}"></i>oil (${m === 'ours' ? 'our model' : 'OpenOil'})</span>`).join('')
    + `<span>${models.map((m) => `<i style="background:${PALETTES[m].cssStranded}"></i>`).join('')}stranded</span>`;
  const max = 1.8;
  const stops = SPEED_RAMP.map(([v, r, g, b]) => `rgb(${r},${g},${b}) ${(v / max) * 100}%`).join(',');
  return `<div class="row"><div class="label" style="margin:0">Surface current</div>
      <label class="toggle"><input type="checkbox" id="cur-toggle" ${show ? 'checked' : ''}/> show</label></div>
    <div class="ramp" style="margin-top:6px;background:linear-gradient(90deg,${stops})"></div>
    <div class="ticks"><span>0</span><span>0.5</span><span>1.0</span><span>1.5+ m/s</span></div>
    <div class="lg-oil">${oil}<span><i class="ring"></i>observed outline</span></div>
    ${sourceLine}`;
}

/** Animation rates in simulated hours per real second. */
export const RATES_H = [0.5, 1, 2, 3, 6, 12, 24, 48, 96, 168, 336];

/** Rate whose full playback of `durationH` takes about `targetS` seconds. */
export function autoRateIdx(durationH: number, targetS = 25) {
  const want = durationH / targetS;
  let best = 0;
  for (let i = 0; i < RATES_H.length; i++) if (Math.abs(Math.log(RATES_H[i] / want)) < Math.abs(Math.log(RATES_H[best] / want))) best = i;
  return best;
}

export function rateLabel(h: number) {
  if (h >= 24) { const d = h / 24; return `${Number.isInteger(d) ? d : d.toFixed(1)} days/sec`; }
  return `${h} ${h === 1 ? 'hour' : 'hours'}/sec`;
}

// ---------------------------------------------------------------- historical response timeline


const escH = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

/** Short label + colour per response category. */
export const RESPONSE_CATS: Record<ResponseCategory, [string, string]> = {
  notification: ['report', '#9aa4b2'],
  assessment: ['assess', '#8fb8ff'],
  source_control: ['source', '#c79bff'],
  boom: ['boom', '#ffd166'],
  skimming: ['skim', '#62d5c0'],
  dispersant: ['dispers.', '#6fa8ff'],
  in_situ_burn: ['burn', '#ff7a45'],
  shoreline_cleanup: ['shore', '#e8b07a'],
  wildlife: ['wildlife', '#9be37a'],
  closure: ['closure', '#ff5a64'],
  waste: ['waste', '#b0a090'],
  recovery_total: ['totals', '#ffffff'],
  outcome: ['outcome', '#d0d6e0'],
  other: ['other', '#9aa4b2'],
};

/** "YYYY-MM-DD" | "YYYY-MM" -> unix seconds (UTC noon, so it lands inside that day). */
export function eventUnix(date: string) {
  const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(date);
  if (!m) return NaN;
  return Date.UTC(+m[1], +m[2] - 1, m[3] ? +m[3] : 1, 12) / 1000;
}

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
function shortDate(date: string, year0: string) {
  const m = /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(date);
  if (!m) return escH(date);
  const md = m[3] ? `${MON[+m[2] - 1]} ${+m[3]}` : MON[+m[2] - 1];
  return m[1] === year0 ? md : `${md} ’${m[1].slice(2)}`;
}

const srcLabel = (u: string) => u.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '');

/** Compact, sourced response timeline (+ collapsed oil-movement notes) for a catalog spill.
 *  Rows carry data-t (unix s) so a click can move the clock there. */
export function responseHTML(s: CatalogSpill, open: { resp: boolean; evo: boolean }) {
  const resp = s.response ?? [];
  const evo = s.evolution ?? [];
  const sum = s.response_summary;
  if (!resp.length && !evo.length && !sum) return '';
  const y0 = s.date_start.slice(0, 4);
  let html = '';
  if (resp.length || sum) {
    html += `<details class="resp" data-k="resp" ${open.resp ? 'open' : ''}><summary>Response log <small>${resp.length} dated actions</small></summary>`;
    if (sum?.outcome) html += `<div class="resp-out">${escH(sum.outcome)}</div>`;
    html += `<div class="tl">${resp
      .map((a) => {
        const [lab, col] = RESPONSE_CATS[a.category] ?? RESPONSE_CATS.other;
        return `<div class="ev" data-t="${eventUnix(a.date)}" title="${escH(a.quote ? `“${a.quote}”` : '')}">
          <button class="d" title="Jump to ${escH(a.date)}${a.date_approx ? ' (approximate)' : ''}">${a.date_approx ? '~' : ''}${shortDate(a.date, y0)}</button>
          <i style="--c:${col}">${lab}</i>
          <span>${escH(a.action)} <a href="${escH(a.src)}" target="_blank" rel="noopener" title="${escH(srcLabel(a.src))}">src</a></span></div>`;
      })
      .join('')}</div>`;
    if (sum?.gaps?.length) html += `<div class="gaps"><b>Not in the sources:</b> ${sum.gaps.map(escH).join(' · ')}</div>`;
    html += `</details>`;
  }
  if (evo.length) {
    html += `<details class="resp" data-k="evo" ${open.evo ? 'open' : ''}><summary>Where the oil went <small>${evo.length} notes</small></summary><div class="tl">${evo
      .map((e) => `<div class="ev" data-t="${eventUnix(e.date)}" title="${escH(e.quote ? `“${e.quote}”` : '')}">
          <button class="d" title="Jump to ${escH(e.date)}${e.date_approx ? ' (approximate)' : ''}">${e.date_approx ? '~' : ''}${shortDate(e.date, y0)}</button>
          <span>${escH(e.note)} <a href="${escH(e.src)}" target="_blank" rel="noopener" title="${escH(srcLabel(e.src))}">src</a></span></div>`)
      .join('')}</div></details>`;
  }
  return html;
}

// ---------- backtest (P5): scores of each model against what was observed (GET /api/backtest) ----------
const BT_ROWS: [string, string][] = [['persistence', 'Persistence'], ['openoil', 'OpenOil'], ['ours_ml', 'Ours + ML'], ['ours_noml', 'Ours, no ML']];
const f0 = (x: unknown) => (typeof x === 'number' && isFinite(x) ? x.toFixed(0) : '–');
const f2 = (x: unknown) => (typeof x === 'number' && isFinite(x) ? x.toFixed(2) : '–');
const pct = (x: unknown) => (typeof x === 'number' && isFinite(x) ? `${Math.round(x * 100)}%` : '–');

/** Mark the best value of each column (lower or higher is better) in bold. */
function btTable(head: string[], rows: [string, (string | number | null)[]][], better: ('lo' | 'hi' | '')[]) {
  const best = better.map((b, j) => {
    const v = rows.map(([, r]) => r[j]).filter((x): x is number => typeof x === 'number' && isFinite(x));
    return !b || !v.length ? null : b === 'lo' ? Math.min(...v) : Math.max(...v);
  });
  const cell = (x: string | number | null, j: number, fmt: (x: unknown) => string) =>
    `<td${typeof x === 'number' && x === best[j] ? ' class="best"' : ''}>${typeof x === 'number' ? fmt(x) : escH(x ?? '–')}</td>`;
  return `<table class="bt"><tr>${head.map((h) => `<th>${h}</th>`).join('')}</tr>${rows
    .map(([lab, r]) => `<tr><td>${escH(lab)}</td>${r.map((x, j) => cell(x, j, better[j] === 'hi' ? f2 : f0)).join('')}</tr>`)
    .join('')}</table>`;
}

export function backtestHTML(spillId: string, bt: any | null, open: boolean) {
  if (!bt) return '';
  const c = (bt.cases ?? []).find((x: any) => x.id === spillId);
  const wrap = (sub: string, body: string) =>
    `<details class="resp bt-sec" data-k="bt" ${open ? 'open' : ''}><summary>Backtest <small>${sub}</small></summary>${body}<div class="gaps">Method + limitations: docs/BACKTEST.md · scores precomputed by <code>python -m app.backtest</code></div></details>`;
  if (!c) return wrap('not scored', `<div class="resp-out">Not backtested: no observed positions with cached forcing for this spill. Scored: Deepwater Horizon, Texas City Y, Main Pass 2023.</div>`);
  if (c.kind === 'outlines') {
    const s = c.summary;
    const rows: [string, (number | null)[]][] = BT_ROWS.filter(([k]) => s[k]).map(([k, lab]) => [lab, [...s[k].sep_km_median, s[k].lw_median[2], s[k].fss_median[2]]]);
    const ml = s.ml_closer_share ? `<div class="hint">Ours + ML ends closer than ours without ML in ${pct(s.ml_closer_share[2])} of start days at 72 h.</div>` : '';
    return wrap(`${s.openoil?.n ?? 0} start days`, `<div class="resp-out">Seed inside the NESDIS outline of day D, compare with the outlines of D+1..D+3. Medians over start days. Sep = centroid distance (km); LW = Liu–Weisberg skill (0–1); FSS at 25 km.</div>${btTable(['', 'Sep 24h', '48h', '72h', 'LW 72h', 'FSS 72h'], rows, ['lo', 'lo', 'lo', 'hi', 'hi'])}${ml}`);
  }
  if (c.kind === 'points') {
    let body = `<div class="resp-out">No observed polygons. Distance (km) from the nearest floating model oil to the overflight positions (times not reported: noon CDT assumed), and share stranded.</div>`;
    for (const v of c.variants) {
      const rows: [string, (number | string | null)[]][] = BT_ROWS.filter(([k]) => v.models[k]).map(([k, lab]) => {
        const m = v.models[k];
        const fx = (i: number) => (m.fixes?.[i] ? (m.fixes[i].nearest_km ?? 'all ashore') : 'n/a');
        return [lab, [fx(0), fx(1), m.stranded_end != null ? Math.round(m.stranded_end * 100) : null]];
      });
      body += `<div class="hint" style="margin-top:6px">${escH(v.label)}</div>${btTable(['', 'Mar 24', 'Mar 25', 'Stranded %'], rows, ['lo', 'lo', ''])}`;
    }
    body += `<div class="hint">Observed: 18.9 mi of Galveston-area shoreline oiled by Mar 25; Matagorda Island landfall Mar 27 is after the cached forcing ends (not scored).</div>`;
    return wrap('overflight fixes', body);
  }
  const rows: [string, (number | string | null)[]][] = BT_ROWS.filter(([k]) => c.models[k]).map(([k, lab]) => {
    const m = c.models[k];
    return [lab, [m.bearing_deg?.[0] ?? null, m.bearing_deg?.[2] ?? null, m.stranded?.[2] != null ? Math.round(m.stranded[2] * 100) : null, m.length_24h_km ?? null]];
  });
  rows.unshift(['Observed', ['SW', 'SW', 0, '>64']]);
  return wrap('direction + shoreline', `<div class="resp-out">${escH(c.observed)}</div>${btTable(['', 'Bearing 24h', '72h', 'Stranded %', 'Length 24h km'], rows, ['', '', '', ''])}<div class="hint">Bearing = direction (°) of the floating-oil centroid from the source; SW = 180–270°.</div>`);
}
