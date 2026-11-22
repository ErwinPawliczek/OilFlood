// Panel charts for the forces view: (1) drift speed contributed by each force over time, stacked, with the net drift
// as a line on top (the gap = how much the pushes cancel), (2) oil budget (surface / evaporated / dispersed / stranded).
// Two separate charts (two quantities, never two y-axes). A shared clock cursor follows the time slider; hover shows a
// crosshair + values. Legend chips explain each force in one sentence with its equation (KaTeX).

import 'katex/dist/katex.min.css';
import { tex } from './md';
import { FORCE_COL, BUDGET_COL, FORCE_INFO, FORCE_KEYS, type ForcesPayload } from './ensemble';

const W = 292, H = 74, PADL = 30, PADR = 6, PADT = 6, PADB = 14;
const esc = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const BUD = ['surface', 'evaporated', 'dispersed', 'stranded'] as const;
const BUD_LABEL: Record<string, string> = { surface: 'Surface', evaporated: 'Evaporated', dispersed: 'Dispersed', stranded: 'Stranded' };

function niceMax(v: number) {
  const e = 10 ** Math.floor(Math.log10(v || 1));
  return [1, 2, 2.5, 5, 10].map((m) => m * e).find((x) => x >= v) ?? v;
}

interface Series { key: string; color: string; vals: number[] }

/** Stacked area (2 px surface gap between bands via stroke in the panel colour) + optional overlay line. */
function stackedSVG(id: string, hs: number[], series: Series[], yMax: number, yFmt: (v: number) => string, tMax: number, line?: number[]) {
  const x = (h: number) => PADL + ((W - PADL - PADR) * h) / (tMax || 1);
  const y = (v: number) => PADT + (H - PADT - PADB) * (1 - v / yMax);
  const base = hs.map(() => 0);
  let bands = '';
  for (const s of series) {
    const top = s.vals.map((v, i) => base[i] + Math.max(0, v));
    const pts = hs.map((h, i) => `${x(h).toFixed(1)},${y(top[i]).toFixed(1)}`).join(' ');
    const back = hs.map((h, i) => `${x(h).toFixed(1)},${y(base[i]).toFixed(1)}`).reverse().join(' ');
    bands += `<polygon points="${pts} ${back}" fill="${s.color}" stroke="rgba(10,16,28,0.9)" stroke-width="1" stroke-linejoin="round"/>`;
    top.forEach((v, i) => { base[i] = v; });
  }
  const ln = line ? `<polyline points="${hs.map((h, i) => `${x(h).toFixed(1)},${y(line[i]).toFixed(1)}`).join(' ')}" fill="none" stroke="#f2f5fa" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>` : '';
  const ticksX = [0, 24, 48, 72, 96, 120].filter((h) => h <= tMax + 0.1);
  const grid = [0.5, 1].map((f) => `<line x1="${PADL}" x2="${W - PADR}" y1="${y(yMax * f)}" y2="${y(yMax * f)}" class="cg"/><text x="${PADL - 4}" y="${y(yMax * f) + 3}" text-anchor="end" class="ct">${yFmt(yMax * f)}</text>`).join('');
  return `<svg class="fchart" id="${id}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" data-tmax="${tMax}">
    ${grid}<line x1="${PADL}" x2="${W - PADR}" y1="${y(0)}" y2="${y(0)}" class="cb"/><text x="${PADL - 4}" y="${y(0) + 3}" text-anchor="end" class="ct">0</text>
    ${bands}${ln}
    ${ticksX.map((h) => `<text x="${x(h)}" y="${H - 2}" text-anchor="middle" class="ct">${h}h</text>`).join('')}
    <line class="cur" x1="${x(0)}" x2="${x(0)}" y1="${PADT}" y2="${H - PADB}"/>
    <line class="hov" x1="0" x2="0" y1="${PADT}" y2="${H - PADB}" style="display:none"/>
  </svg>`;
}

export function forcesPanelHTML(F: ForcesPayload, tMax: number, opts: { members?: number } = {}) {
  const st = F.steps;
  if (!st.length) return '';
  const hs = [0, ...st.map((s) => s.h)];
  const pick = (k: string) => [0, ...st.map((s) => Math.abs((s as any)[k]?.speed ?? 0))];
  const fs: Series[] = FORCE_KEYS.filter((k) => st.some((s) => ((s as any)[k]?.speed ?? 0) > 1e-4)).map((k) => ({ key: k, color: FORCE_COL[k], vals: pick(k) }));
  const net = pick('total');
  const ymax = niceMax(Math.max(0.05, ...hs.map((_, i) => fs.reduce((a, s) => a + s.vals[i], 0)), ...net));
  const hasBudget = st.some((s) => s.budget);
  const b0 = { surface: 1, evaporated: 0, dispersed: 0, stranded: 0 };
  const bs: Series[] = BUD.map((k) => ({ key: k, color: BUDGET_COL[k], vals: [b0[k], ...st.map((s) => s.budget?.[k] ?? 0)] }));
  const chip = (k: string, color: string, line = false) => `<span class="fchip" data-force="${k}" tabindex="0"><i class="${line ? 'ln' : ''}" style="background:${color}"></i>${esc(FORCE_INFO[k]?.label ?? k)}</span>`;
  const src = F.source === 'ensemble' ? `mean of ${opts.members ?? F.members} members` : 'control run';
  return `<div class="sec forces-sec">
    <div class="label">What pushes the oil <small class="hint">(${esc(src)}, avg over surface oil)</small></div>
    <div class="flegend">${fs.map((s) => chip(s.key, s.color)).join('')}${chip('total', FORCE_COL.total, true)}${st.some((s) => s.diffusion) ? chip('diffusion', '#8796ad') : ''}</div>
    <div class="fwrap" data-chart="forces">${stackedSVG('fc-forces', hs, fs, ymax, (v) => v.toFixed(2), tMax, net)}<div class="ftip"></div></div>
    <div class="hint fcap">Drift speed (m/s) from each force, stacked. White line = net drift: where it sits below the stack, the forces push in different directions.</div>
    ${hasBudget ? `<div class="label" style="margin-top:8px">Oil budget</div>
    <div class="flegend">${bs.map((s) => `<span class="fchip nohelp"><i style="background:${s.color}"></i>${BUD_LABEL[s.key]}</span>`).join('')}</div>
    <div class="fwrap" data-chart="budget">${stackedSVG('fc-budget', hs, bs, 1, (v) => `${Math.round(v * 100)}%`, tMax)}<div class="ftip"></div></div>` : ''}
    <div class="fpop" id="fpop"></div>
  </div>`;
}

/** Move the clock cursor of both charts to hour tH (cheap: attribute updates only). */
export function setChartTime(root: HTMLElement, tH: number) {
  root.querySelectorAll<SVGSVGElement>('svg.fchart').forEach((svg) => {
    const tMax = Number(svg.dataset.tmax) || 1;
    const x = PADL + ((W - PADL - PADR) * Math.min(tMax, Math.max(0, tH))) / tMax;
    const c = svg.querySelector('.cur');
    if (c) { c.setAttribute('x1', String(x)); c.setAttribute('x2', String(x)); }
  });
}

/** Hover crosshair + value tooltip on the charts, and the force explainer popover on the legend chips. */
export function wireForcesPanel(root: HTMLElement, getF: () => ForcesPayload | null) {
  root.addEventListener('mousemove', (e) => {
    const wrap = (e.target as Element).closest<HTMLElement>('.fwrap');
    root.querySelectorAll<HTMLElement>('.fwrap').forEach((w) => { if (w !== wrap) hide(w); });
    const F = getF();
    if (!wrap || !F) return;
    const svg = wrap.querySelector('svg')!;
    const r = svg.getBoundingClientRect();
    const tMax = Number(svg.dataset.tmax) || 1;
    const sx = ((e.clientX - r.left) / r.width) * W;
    const h = Math.min(tMax, Math.max(0, ((sx - PADL) / (W - PADL - PADR)) * tMax));
    const s = F.steps.reduce((a, b) => (Math.abs(b.h - h) < Math.abs(a.h - h) ? b : a));
    const hov = svg.querySelector<SVGLineElement>('.hov')!;
    const x = PADL + ((W - PADL - PADR) * s.h) / tMax;
    hov.setAttribute('x1', String(x)); hov.setAttribute('x2', String(x)); hov.style.display = '';
    const tip = wrap.querySelector<HTMLElement>('.ftip')!;
    const row = (c: string, lab: string, v: string) => `<div><i style="background:${c}"></i>${esc(lab)}<b>${v}</b></div>`;
    tip.innerHTML = `<div class="th">T+${s.h} h</div>` + (wrap.dataset.chart === 'forces'
      ? [...FORCE_KEYS.filter((k) => (s as any)[k]?.speed > 1e-4).map((k) => row(FORCE_COL[k], FORCE_INFO[k].label, `${(s as any)[k].speed.toFixed(3)} m/s`)),
        row(FORCE_COL.total, 'Net drift', `${s.total.speed.toFixed(3)} m/s → ${Math.round(s.total.dir_to)}°`)].join('')
      : s.budget ? BUD.map((k) => row(BUDGET_COL[k], BUD_LABEL[k], `${Math.round(s.budget![k] * 100)} %`)).join('') : '');
    tip.style.display = 'block';
    const left = ((x / W) * r.width);
    tip.style.left = `${Math.min(r.width - tip.offsetWidth, Math.max(0, left + (left > r.width / 2 ? -tip.offsetWidth - 10 : 10)))}px`;
  });
  root.addEventListener('mouseleave', () => root.querySelectorAll<HTMLElement>('.fwrap').forEach(hide), true);
  const pop = () => root.querySelector<HTMLElement>('#fpop');
  root.addEventListener('mouseover', (e) => {
    const c = (e.target as Element).closest<HTMLElement>('.fchip[data-force]');
    const p = pop();
    if (!p) return;
    if (!c) { p.classList.remove('on'); return; }
    const info = FORCE_INFO[c.dataset.force!];
    if (!info) return;
    p.innerHTML = `<b>${esc(info.label)}</b> ${esc(info.text)}<div class="feq">${tex(info.eq, false)}</div>`;
    p.classList.add('on');
  });
}
function hide(w: HTMLElement) {
  const t = w.querySelector<HTMLElement>('.ftip'); if (t) t.style.display = 'none';
  const h = w.querySelector<SVGLineElement>('.hov'); if (h) h.style.display = 'none';
}

/** Floating map tooltip for a hovered force arrow. */
export function arrowTooltipHTML(key: string, speed: number, dir: number) {
  const info = FORCE_INFO[key];
  if (!info) return '';
  return `<b style="color:${FORCE_COL[key] === '#f2f5fa' ? '#fff' : FORCE_COL[key]}">${esc(info.label)}</b> <span class="hint">${speed.toFixed(2)} m/s toward ${Math.round(dir)}°</span><div>${esc(info.text)}</div><div class="feq">${tex(info.eq, false)}</div>`;
}
