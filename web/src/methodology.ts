// /methodology: the pipeline Data -> Model -> Ensemble/probabilities -> Protocol gates -> Action plan, with the
// equations (KaTeX, bundled), the live parameter table (GET /api/model/params), validation numbers
// (GET /api/backtest, GET /api/models) and limits. docs/METHODOLOGY.md is inlined at build time when it exists.

import 'katex/dist/katex.min.css';
import './methodology.css';
import { tex, renderMarkdown } from './md';

const docs = import.meta.glob('../../docs/METHODOLOGY.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
const METHODOLOGY_MD: string | null = Object.values(docs)[0] ?? null;

const esc = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const T = (s: string) => tex(s, false);
const D = (s: string) => `<div class="eq">${tex(s, true)}</div>`;

interface Step { id: string; n: number; title: string; lede: string; body: string }

const STEPS: Step[] = [
  {
    id: 'data', n: 1, title: 'Data', lede: 'Real forcing only. No synthetic fallback anywhere.',
    body: `
      <div class="cards">
        <div class="card"><b>Ocean currents</b><span>Live: NOAA RTOFS / HYCOM ESPC surface currents (forecast). Historical: HYCOM-TSIS Gulf of Mexico reanalysis, 1/25°.</span></div>
        <div class="card"><b>Winds</b><span>Live: NCEP GFS 10 m winds. Historical: the reanalysis winds paired with the currents.</span></div>
        <div class="card"><b>Oil</b><span>NOAA ADIOS oil library: density, viscosity, distillation cuts, pour point, emulsion limits.</span></div>
        <div class="card"><b>Shorelines</b><span>NOAA Environmental Sensitivity Index (ESI) lines, GSHHG coastline for stranding, ETOPO1 depth.</span></div>
        <div class="card"><b>Truth</b><span>NOAA/NESDIS daily satellite oil outlines (Deepwater Horizon), IncidentNews overflights, NOAA Global Drifter Program tracks.</span></div>
        <div class="card"><b>Rules</b><span>49 sourced response rules: RRT-6 dispersant and in-situ burn plans, USCG 33 CFR, NOAA guidance (docs/PROTOCOLS.md).</span></div>
      </div>`,
  },
  {
    id: 'model', n: 2, title: 'Model', lede: 'Thousands of Lagrangian oil particles, each pushed by every force we can measure.',
    body: `
      <p>Each particle carries a mass of oil. Every 15 minutes it moves with the surface current, a fraction of the wind, wave-driven Stokes drift and (optionally) a learned correction, plus a random walk for turbulence:</p>
      ${D(String.raw`\frac{d\mathbf{x}}{dt} \;=\; \underbrace{\mathbf{U}_c}_{\text{current}} \;+\; \underbrace{\alpha\,\mathbf{U}_{10}}_{\text{wind drift}} \;+\; \underbrace{\mathbf{U}_S}_{\text{Stokes}} \;+\; \underbrace{\Delta\mathbf{U}_{ML}}_{\text{learned}} \;+\; \underbrace{\sqrt{2K/\Delta t}\;\boldsymbol{\xi}}_{\text{diffusion}}`)}
      <div class="forces">
        <div><i style="--c:#4fc3ff"></i><b>Current</b> ${T(String.raw`\mathbf{U}_c`)}: the ocean model's surface velocity, bilinear in space and linear in time.</div>
        <div><i style="--c:#ffd166"></i><b>Wind drift</b> ${T(String.raw`\alpha\,\mathbf{U}_{10}`)}: oil skids at about ${T(String.raw`\alpha = 2\text{–}3\,\%`)} of the 10 m wind (GNOME uses 1–4 %).</div>
        <div><i style="--c:#7cf0b0"></i><b>Stokes drift</b> ${T(String.raw`\mathbf{U}_S`)}: net push of the waves, from a wave model or parameterised from the wind.</div>
        <div><i style="--c:#d58bff"></i><b>ML correction</b> ${T(String.raw`\Delta\mathbf{U}_{ML}`)}: gradient-boosted residual learned from 42 NOAA drifters, masked to where they sampled. Experimental, off by default.</div>
        <div><i style="--c:#9aa4b2"></i><b>Diffusion</b> ${T(String.raw`K = K_h + K_{Fay}(t)`)}: ${T(String.raw`K_h = 10\ \mathrm{m^2/s}`)} plus Fay spreading as an equivalent diffusivity.</div>
      </div>
      <p>The oil weathers while it drifts. Mass leaves the surface by evaporation and natural dispersion; the rest emulsifies, thickens and eventually strands on the coast.</p>
      <div class="eqgrid">
        <div><div class="eqh">Fay spreading (gravity–viscous)</div>${D(String.raw`R = 1.45\left(\frac{\Delta g V_0^2\, t^{3/2}}{\nu_w^{1/2}}\right)^{1/6},\quad K_{Fay}=\tfrac18\,\frac{d(R^2)}{dt}`)}</div>
        <div><div class="eqh">Fingas evaporation (crudes)</div>${D(String.raw`\%E_v = \big(0.165\,\%D + 0.045\,(T-15)\big)\ln t`)}</div>
        <div><div class="eqh">Mackay emulsification</div>${D(String.raw`\frac{dY}{dt} = 2\cdot10^{-6}\,(U+1)^2\Big(1-\frac{Y}{Y_{max}}\Big)`)}</div>
        <div><div class="eqh">Mackay natural dispersion</div>${D(String.raw`\frac{dF_d}{dt} = \frac{0.11\,(U+1)^2}{1 + 50\,\mu^{1/2} h\, S}`)}</div>
      </div>
      <p class="hint">Two engines run on identical forcing and oil records: <b>OpenOil</b> (OpenDrift, the established baseline: 2 % wind + Stokes) and <b>ours</b> (3 % wind, Fay spreading, Fingas/Mackay weathering). Full derivation: docs/OURMODEL.md.</p>`,
  },
  {
    id: 'ensemble', n: 3, title: 'Ensemble → probabilities', lede: 'One forecast is a guess. Many perturbed forecasts are a probability.',
    body: `
      <p>We rerun our model ${T('N')} times (default 20, up to 30), each member with its own draw of the inputs we are least sure of. Every member ${T('k')} gives an oil mass field ${T(String.raw`m_k(c,t)`)} on a 0.05° (≈ 5 km) grid of cells ${T('c')}, every 3 h.</p>
      <table class="tbl"><tr><th>Perturbed input</th><th>Distribution per member</th><th>Why</th></tr>
        <tr><td>Windage ${T(String.raw`\alpha`)}</td><td>${T(String.raw`\mathcal{U}(1\,\%,\,4\,\%)`)}</td><td class="hint">NOAA GNOME minimum-regret range</td></tr>
        <tr><td>Wind direction</td><td>whole field rotated by ${T(String.raw`\theta \sim \mathcal{N}(0,\,20^\circ)`)}</td><td class="hint">GFS 10 m direction error at 1–3 day lead</td></tr>
        <tr><td>Current speed</td><td>${T(String.raw`c \sim \mathcal{N}(1,\,0.2)`)}, clipped to [0.5, 1.5]</td><td class="hint">model vs drifter speed errors of 20–30 %</td></tr>
        <tr><td>Diffusivity ${T('K')}</td><td>${T(String.raw`K \cdot e^{\mathcal{N}(0,\,\ln 2)}`)}</td><td class="hint">factor-of-2 spread, Okubo (1971) scales</td></tr>
      </table>
      <p>From the members:</p>
      <div class="eqgrid">
        <div><div class="eqh">Probability of oil</div>${D(String.raw`P(c,t) = \frac{1}{N}\sum_{k=1}^{N}\mathbb{1}\big[m_k(c,t) > m_{min}\big]`)}</div>
        <div><div class="eqh">Expected surface mass</div>${D(String.raw`\bar m(c,t) = \frac{1}{N}\sum_{k=1}^{N} m_k(c,t)`)}</div>
        <div><div class="eqh">Arrival time quantiles per shoreline segment</div>${D(String.raw`\mathrm{ETA}_{q} = Q_q\big\{\,t^{\,k}_{\text{first}}(s)\,\big\}_{k=1}^{N},\quad q\in\{10,50,90\}`)}</div>
        <div><div class="eqh">Probability a shoreline is oiled</div>${D(String.raw`P_{\text{oiled}}(s) = \frac{1}{N}\sum_{k}\mathbb{1}\big[t^{\,k}_{\text{first}}(s) \le H\big]`)}</div>
      </div>
      <p class="hint">With ${T('N = 20')} the resolution of ${T('P')} is 5 % and its standard error ${T(String.raw`\sqrt{P(1-P)/N}`)} is at most 11 %.</p>
      <p>The map draws ${T('P')} as a 3D surface (height and colour), with the 50 % and 90 % contours draped on the sea, and P10 / P50 / P90 arrival times on each threatened shoreline. P10 is "plan for this": oil arrives that early in 1 of 10 members.</p>`,
  },
  {
    id: 'gates', n: 4, title: 'Protocol gates', lede: 'Every tactic is checked hour by hour against the written rules responders already use.',
    body: `
      <table class="tbl">
        <tr><th>Tactic</th><th>Gate (fails → tactic not allowed that hour)</th><th>Source</th></tr>
        <tr><td>Dispersant</td><td>≥ 3 nm offshore and ≥ 10 m depth; API 17–45; water in emulsion ≤ 25 %; wind ≤ 25 kt; daylight</td><td>RRT-6 preauthorization</td></tr>
        <tr><td>In-situ burn</td><td>Emulsion limit; > 500 bbl per ~6 km patch; wind ≤ 21.7 kt; current ≤ 0.75 kt; sun > 15°; 3 mi from populated places</td><td>RRT-6 ISB plan</td></tr>
        <tr><td>Boom</td><td>Current across the boom ≤ 0.7 kt (exclusion), 0.7–1 kt (deflection), > 1 kt won't hold</td><td>NOAA / USCG boom guidance</td></tr>
        <tr><td>Lead time</td><td>Notify 0.5 h + en route 2 h + road travel, floored at USCG Tier 1 (6 h / 12 h)</td><td>33 CFR 154.1045</td></tr>
      </table>
      <p>With an ensemble, each gate is evaluated in every member, so a gate becomes a <b>pass fraction</b>:</p>
      ${D(String.raw`\text{pass}(g,t) = \frac{1}{N}\sum_{k=1}^{N}\mathbb{1}\big[\text{gate } g \text{ holds in member } k \text{ at } t\big]`)}`,
  },
  {
    id: 'plan', n: 5, title: 'Action plan', lede: 'Translate probabilities into a ranked, timed list of things to do.',
    body: `
      <ol class="plan">
        <li><b>Protect first</b>: shoreline segments ranked by ${T(String.raw`P_{\text{oiled}}`)}, ESI sensitivity and P10 arrival.</li>
        <li><b>Deploy by</b>: boom must be in place before ${T(String.raw`\mathrm{ETA}_{10}`)} minus the lead time, so it is still in place in 9 of 10 futures.</li>
        <li><b>Windows</b>: dispersant / burn / skimming hours where the gate pass fraction is high.</li>
        <li><b>Closures and notifications</b> from the oil's predicted reach.</li>
      </ol>
      <p class="hint">Rule-based decision support, not operational guidance: the Federal On-Scene Coordinator / Unified Command decides.</p>`,
  },
];

function pipelineSVG() {
  return `<nav class="pipe">${STEPS.map((s, i) => `<a href="#${s.id}"><span class="n">${s.n}</span><span class="t">${esc(s.title)}</span></a>${i < STEPS.length - 1 ? '<span class="arr" aria-hidden="true"></span>' : ''}`).join('')}</nav>`;
}

// ---------------------------------------------------------------- live numbers
async function getJSON(url: string) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}

const f1 = (x: unknown) => (typeof x === 'number' && isFinite(x) ? x.toFixed(1) : '–');
const f2 = (x: unknown) => (typeof x === 'number' && isFinite(x) ? x.toFixed(2) : '–');

/** Horizontal bars: median centroid separation (km) at 24/48/72 h for each model vs persistence (DWH). */
function sepChart(rows: { lab: string; v: number[]; c: string }[]) {
  const max = Math.max(...rows.flatMap((r) => r.v)) * 1.08;
  const W = 520, rowH = 22, gap = 14, left = 150, H = 3 * (rows.length * rowH + gap) + 10;
  let y = 4, out = '';
  ['24 h', '48 h', '72 h'].forEach((h, j) => {
    out += `<text x="0" y="${y + 12}" class="ax">${h}</text>`;
    for (const r of rows) {
      const w = ((W - left - 46) * r.v[j]) / max;
      out += `<text x="${left - 8}" y="${y + 15}" text-anchor="end" class="lb">${esc(r.lab)}</text>
        <rect x="${left}" y="${y + 4}" width="${w.toFixed(1)}" height="${rowH - 8}" rx="3" fill="${r.c}"><title>${esc(r.lab)} ${h}: ${f1(r.v[j])} km</title></rect>
        <text x="${left + w + 6}" y="${y + 15}" class="val">${f1(r.v[j])}</text>`;
      y += rowH;
    }
    y += gap;
  });
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Median separation from observed oil, km">${out}</svg>`;
}

/** Grouped bars: share of the observed slick inside the ensemble P >= 10 % area vs inside the single control run. */
function covChart(rows: any[]) {
  const S = [{ k: 'coverage_p10_median', lab: 'Ensemble, P ≥ 10 % area', c: '#e39a32' }, { k: 'coverage_p50_median', lab: 'Ensemble, P ≥ 50 % area', c: '#b07cf0' }, { k: 'coverage_ctrl_median', lab: 'Single deterministic run', c: '#8796ad' }];
  const W = 520, H = 170, L = 40, B = 24, gw = (W - L) / rows.length, bw = 26;
  const y = (v: number) => 8 + (H - B - 8) * (1 - v);
  let g = [0, 0.25, 0.5, 0.75, 1].map((v) => `<line x1="${L}" x2="${W}" y1="${y(v)}" y2="${y(v)}" stroke="rgba(140,180,255,${v ? 0.08 : 0.25})"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" class="lb">${v * 100}%</text>`).join('');
  rows.forEach((r, i) => {
    const x0 = L + i * gw + (gw - S.length * (bw + 3)) / 2;
    S.forEach((s, j) => {
      const v = r[s.k] ?? 0, x = x0 + j * (bw + 3);
      g += `<rect x="${x}" y="${y(v)}" width="${bw}" height="${y(0) - y(v)}" rx="3" fill="${s.c}"><title>${esc(s.lab)} at ${r.h} h: ${Math.round(v * 100)} %</title></rect>`;
      if (j === 0) g += `<text x="${x + bw / 2}" y="${y(v) - 5}" text-anchor="middle" class="val">${Math.round(v * 100)}%</text>`;
    });
    g += `<text x="${L + i * gw + gw / 2}" y="${H - 6}" text-anchor="middle" class="ax">${r.h} h</text>`;
  });
  return `<div class="clegend">${S.map((s) => `<span><i class="sw" style="background:${s.c}"></i>${esc(s.lab)}</span>`).join('')}</div>
    <svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Share of the observed slick covered">${g}</svg>`;
}

async function validationHTML(): Promise<string> {
  const [bt, models] = await Promise.all([getJSON('/api/backtest').catch(() => null), getJSON('/api/models').catch(() => null)]);
  let html = '';
  const dwh = bt?.cases?.find((c: any) => c.id === 'dwh');
  if (dwh?.summary) {
    const s = dwh.summary;
    const R: [string, string, string][] = [['persistence', 'Persistence', '#8796ad'], ['openoil', 'OpenOil', '#ffb547'], ['ours_noml', 'Ours', '#5ae1ff'], ['ours_ml', 'Ours + ML', '#b58cff']];
    const rows = R.filter(([k]) => s[k]?.sep_km_median).map(([k, lab, c]) => ({ lab, c, v: s[k].sep_km_median as number[], s: s[k] }));
    html += `<div class="vcard"><h3>Deepwater Horizon, ${s.openoil?.n ?? '?'} forecasts scored against satellite outlines</h3>
      <p class="hint">${esc(dwh.method)}</p>
      ${sepChart(rows)}
      <table class="tbl num"><tr><th></th><th>Separation 72 h (km)</th><th>Liu–Weisberg skill 72 h</th><th>FSS 72 h (25 km)</th><th>Stranded (mean)</th></tr>
      ${rows.map((r) => `<tr><td><i class="sw" style="background:${r.c}"></i>${esc(r.lab)}</td><td>${f1(r.s.sep_km_median[2])}</td><td>${f2(r.s.lw_median[2])}</td><td>${f2(r.s.fss_median[2])}</td><td>${r.s.stranded_mean ? `${Math.round(r.s.stranded_mean[2] * 100)} %` : '–'}</td></tr>`).join('')}</table>
      <p class="hint">Lower separation and higher skill are better. Honest result: in these 52 start days, "the slick stays where it is" (persistence) is a hard baseline to beat at 24–72 h. That is exactly why the app shows probabilities, not a single line.</p></div>`;
  }
  const cal = models?.models?.find((m: any) => m.id === 'ours')?.calibration;
  if (cal?.summary?.length) {
    const rows = cal.summary as any[];
    html += `<div class="vcard"><h3>Is the probability map calibrated? ${esc(cal.members)}-member ensemble on ${rows[0].n} Deepwater Horizon days</h3>
      <p class="hint">${esc(cal.observed)}. ${esc(cal.model)}.</p>
      ${covChart(rows)}
      <table class="tbl num"><tr><th>Horizon</th><th>Observed slick inside P ≥ 10 %</th><th>Inside single run</th><th>Brier: ensemble vs single run</th><th>Centroid in 90 % ellipse</th></tr>
      ${rows.map((r) => `<tr><td>${r.h} h</td><td><b>${Math.round(r.coverage_p10_median * 100)} %</b></td><td>${Math.round(r.coverage_ctrl_median * 100)} %</td><td>${f2(r.brier_mean).replace(/^0/, '')} vs ${f2(r.brier_ctrl_mean).replace(/^0/, '')}</td><td>${Math.round(r.centroid_in90_frac * 100)} %</td></tr>`).join('')}</table>
      <p class="hint">The P ≥ 10 % area holds about twice as much of the real slick as one deterministic run, for ~${f1(rows[rows.length - 1].area_ratio_p10_median)}× the observed area, and its Brier score is ~30 % lower. But the centroid ellipses are under-dispersive (a calibrated 90 % ellipse would hit ~90 %): the ensemble does not sample the eddy/front position errors of the current field, so read them as parameter spread, not a confidence region.</p></div>`;
  }
  const mp = bt?.cases?.find((c: any) => c.id === 'main_pass_2023');
  if (mp?.models) {
    html += `<div class="vcard"><h3>Main Pass 2023: direction and stranding</h3><p class="hint">${esc(mp.observed)}</p>
      <table class="tbl num"><tr><th></th><th>Bearing 24 h</th><th>Stranded 72 h</th></tr>${[['openoil', 'OpenOil'], ['ours_noml', 'Ours'], ['ours_ml', 'Ours + ML']].filter(([k]) => mp.models[k]).map(([k, lab]) => `<tr><td>${lab}</td><td>${mp.models[k].bearing_deg?.[0] ?? '–'}°</td><td>${Math.round((mp.models[k].stranded?.[2] ?? 0) * 100)} %</td></tr>`).join('')}
      <tr><td>Observed</td><td>SW (180–270°)</td><td>0 %</td></tr></table>
      <p class="hint">The ML correction strands 62 % on the delta where none came ashore. That is why it ships off by default.</p></div>`;
  }
  const sk = models?.models?.find((m: any) => m.id === 'ours')?.skill;
  if (sk?.traj_24h) {
    html += `<div class="vcard"><h3>ML drift correction on held-out drifters</h3><p class="hint">${esc(sk.validation)}; ${sk.drifters} drifters, ${Number(sk.rows).toLocaleString()} hourly fixes.</p>
      <table class="tbl num"><tr><th>Median separation</th><th>Currents only</th><th>+ wind</th><th>+ ML</th><th>ML closer</th></tr>
      <tr><td>24 h (n=${sk.traj_24h.n})</td><td>${f1(sk.traj_24h.median_km_current)} km</td><td>${f1(sk.traj_24h['median_km_current+wind'])} km</td><td><b>${f1(sk.traj_24h.median_km_ml)} km</b></td><td>${Math.round(sk.traj_24h.ml_closer_fraction * 100)} %</td></tr>
      ${sk.traj_48h ? `<tr><td>48 h (n=${sk.traj_48h.n})</td><td>${f1(sk.traj_48h.median_km_current)} km</td><td>${f1(sk.traj_48h['median_km_current+wind'])} km</td><td><b>${f1(sk.traj_48h.median_km_ml)} km</b></td><td>${Math.round(sk.traj_48h.ml_closer_fraction * 100)} %</td></tr>` : ''}</table></div>`;
  }
  return html || '<p class="hint">Validation numbers load from the backend (/api/backtest, /api/models); it is not reachable right now.</p>';
}

/** Model parameters exposed to the builder (GET /api/model/params). Shape is tolerant: {params:[...]} or [...]. */
async function paramsHTML(): Promise<string> {
  const r = await getJSON('/api/model/params').catch(() => null);
  const ps: any[] = Array.isArray(r) ? r : r?.params ?? [];
  if (!ps.length) return '';
  return `<section id="params"><h2>Parameters you can change</h2><p class="lede">The "Build your model" panel on the globe exposes these. Defaults are what the forecast uses.</p>
    <table class="tbl"><tr><th>Parameter</th><th>Default</th><th>Range</th><th>Source</th></tr>${ps.map((p) => `<tr><td><b>${esc(p.label ?? p.key ?? p.id)}</b>${p.description ?? p.help ? `<div class="hint">${esc(p.description ?? p.help)}</div>` : ''}</td>
      <td>${esc(p.default)}${p.unit ? ` ${esc(p.unit)}` : ''}</td><td>${p.min != null ? `${esc(p.min)}–${esc(p.max)}` : p.options ? esc((p.options as any[]).map((o) => o.label ?? o.value ?? o).join(', ')) : '–'}</td>
      <td class="hint">${p.citation_url ?? p.url ? `<a href="${esc(p.citation_url ?? p.url)}" target="_blank" rel="noopener">${esc(p.citation ?? p.source ?? 'source')}</a>` : esc(p.citation ?? p.source ?? '')}</td></tr>`).join('')}</table></section>`;
}

const LIMITS = [
  'Forecast skill is bounded by the ocean model: live RTOFS currents carry errors of tens of km per day near fronts and eddies.',
  'Surface oil only: no subsurface plume, no refloating of stranded oil, fixed 25 °C water in our model.',
  'Mackay dispersion overstates dispersion of thin light slicks (diesel ~90 % in 72 h vs ~44 % in OpenOil).',
  'The ML drift correction is trained mostly on the DWH summer of 2010 and is not validated on RTOFS; it is experimental and off by default.',
  'Ensemble spread only covers the perturbed inputs; it is not a calibrated probability until verified against many real spills.',
  'Shoreline priority uses NOAA ESI only; Area Contingency Plan / GRP priority areas still have to be added by the planner.',
  'Decision support for planners. The FOSC / Unified Command decides.',
];

async function main() {
  const app = document.getElementById('app')!;
  app.innerHTML = `
    <header class="top"><a class="brand" href="/">OilFlood</a><a class="back" href="/">← Back to the globe</a></header>
    <section class="hero">
      <div class="kicker">Methodology</div>
      <h1>From ocean data to an action plan</h1>
      <p class="lede">OilFlood forecasts where spilled oil goes in the next 72 hours, how sure we are, and what responders should do about it. Five steps, every one sourced.</p>
      ${pipelineSVG()}
    </section>
    ${STEPS.map((s) => `<section id="${s.id}"><div class="sh"><span class="n">${s.n}</span><h2>${esc(s.title)}</h2></div><p class="lede">${esc(s.lede)}</p>${s.body}</section>`).join('')}
    <div id="params-slot"></div>
    <section id="validation"><div class="sh"><span class="n">✓</span><h2>Validation</h2></div><p class="lede">Scored against what actually happened. Numbers below are live from the backend.</p><div id="val" class="hint">Loading…</div></section>
    ${METHODOLOGY_MD ? `<section id="full"><div class="sh"><span class="n">§</span><h2>Full technical write-up</h2></div><p class="lede">Every estimator, assumption and reference, from docs/METHODOLOGY.md.</p><details class="mdfull"><summary>Show the full methodology</summary><div class="md">${renderMarkdown(METHODOLOGY_MD)}</div></details></section>` : ''}
    <section id="limits"><div class="sh"><span class="n">!</span><h2>Limits</h2></div><ul class="lim">${LIMITS.map((l) => `<li>${esc(l)}</li>`).join('')}</ul></section>
    <footer><a href="/">Open the globe</a> · docs: OURMODEL.md, BACKTEST.md, PROTOCOLS.md, RESPONSE.md${METHODOLOGY_MD ? ', METHODOLOGY.md' : ''}</footer>`;
  validationHTML().then((h) => { document.getElementById('val')!.outerHTML = `<div id="val">${h}</div>`; });
  paramsHTML().then((h) => { if (h) document.getElementById('params-slot')!.outerHTML = h; });
  if (location.hash) requestAnimationFrame(() => document.querySelector(location.hash)?.scrollIntoView());
}
main();
