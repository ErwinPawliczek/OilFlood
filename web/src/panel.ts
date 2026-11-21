// The app's single floating window: drag by its header, position + collapsed state kept in localStorage.

import { SPEED_RAMP } from './flow';
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
export function legendHTML(show: boolean, sourceLine: string) {
  const max = 1.8;
  const stops = SPEED_RAMP.map(([v, r, g, b]) => `rgb(${r},${g},${b}) ${(v / max) * 100}%`).join(',');
  return `<div class="row"><div class="label" style="margin:0">Surface current</div>
      <label class="toggle"><input type="checkbox" id="cur-toggle" ${show ? 'checked' : ''}/> show</label></div>
    <div class="ramp" style="margin-top:6px;background:linear-gradient(90deg,${stops})"></div>
    <div class="ticks"><span>0</span><span>0.5</span><span>1.0</span><span>1.5+ m/s</span></div>
    <div class="lg-oil"><span><i style="background:#ffb547"></i>oil (model)</span><span><i class="ring"></i>observed outline</span><span><i style="background:#ff5a64"></i>stranded</span></div>
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
          <button class="d" title="Jump to ${escH(a.date)}">${shortDate(a.date, y0)}</button>
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
          <button class="d" title="Jump to ${escH(e.date)}">${shortDate(e.date, y0)}</button>
          <span>${escH(e.note)} <a href="${escH(e.src)}" target="_blank" rel="noopener" title="${escH(srcLabel(e.src))}">src</a></span></div>`)
      .join('')}</div></details>`;
  }
  return html;
}
