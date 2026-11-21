// The app's single floating window: drag by its header, position + collapsed state kept in localStorage.

import { SPEED_RAMP } from './flow';

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
