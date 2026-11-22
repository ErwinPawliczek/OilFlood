// "Build your model": a panel section generated from GET /api/model/params (docs/ENSEMBLE.md). Sliders / toggles with
// units, defaults and a cited tooltip; ensemble size; Run; compare against OpenOil; a shareable URL holding the params.

import type { ParamDef, ParamsSchema } from './ensemble';

export interface BuilderConfig {
  /** only values that differ from the schema defaults */
  params: Record<string, number | boolean>;
  ensemble: number;
  seed: number;
  compare: boolean;
}

const esc = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const fmt = (p: ParamDef, v: number | boolean) => (typeof v === 'boolean' ? (v ? 'on' : 'off') : p.type === 'int' ? String(Math.round(v)) : String(+v.toFixed(3)));

/** URL <-> config: ?ens=20&seed=1&cmp=1&p=windage_pct:2.5,stokes:1 */
export function configFromURL(q: URLSearchParams, S: ParamsSchema): BuilderConfig | null {
  if (!q.has('p') && !q.has('ens')) return null;
  const params: Record<string, number | boolean> = {};
  for (const kv of (q.get('p') ?? '').split(',').filter(Boolean)) {
    const [k, raw] = kv.split(':');
    const d = S.params.find((x) => x.name === k);
    if (!d || raw == null) continue;
    if (d.type === 'bool') params[k] = raw === '1' || raw === 'true';
    else { const v = Number(raw); if (Number.isFinite(v)) params[k] = Math.min(d.max ?? v, Math.max(d.min ?? v, v)); }
  }
  const ens = Math.min(S.ensemble.max_members, Math.max(0, Math.round(Number(q.get('ens') ?? S.ensemble.default_members)) || 0));
  return { params, ensemble: ens, seed: Number(q.get('seed') ?? S.ensemble.seed_default) || 1, compare: q.get('cmp') === '1' };
}
export function configToQuery(c: BuilderConfig): Record<string, string> {
  const p = Object.entries(c.params).map(([k, v]) => `${k}:${typeof v === 'boolean' ? (v ? 1 : 0) : v}`).join(',');
  return { ...(p ? { p } : {}), ens: String(c.ensemble), ...(c.seed !== 1 ? { seed: String(c.seed) } : {}), ...(c.compare ? { cmp: '1' } : {}) };
}

export class Builder {
  values: Record<string, number | boolean> = {};
  ensemble: number;
  seed: number;
  compare = false;

  constructor(public S: ParamsSchema, private el: HTMLElement, private hooks: { onRun: (c: BuilderConfig) => void; onCopy: (c: BuilderConfig) => void; isOpen: () => boolean; setOpen: (o: boolean) => void }, init?: BuilderConfig | null) {
    for (const p of S.params) this.values[p.name] = p.default;
    this.ensemble = S.ensemble.default_members;
    this.seed = S.ensemble.seed_default ?? 1;
    if (init) {
      Object.assign(this.values, init.params);
      this.ensemble = init.ensemble; this.seed = init.seed; this.compare = init.compare;
    }
    this.render();
    el.addEventListener('input', (e) => this.onInput(e));
    el.addEventListener('change', (e) => this.onInput(e));
    el.addEventListener('click', (e) => {
      const b = (e.target as HTMLElement).closest<HTMLButtonElement>('button[data-act]');
      if (!b) return;
      if (b.dataset.act === 'run') hooks.onRun(this.config());
      else if (b.dataset.act === 'reset') { for (const p of S.params) this.values[p.name] = p.default; this.ensemble = S.ensemble.default_members; this.render(); }
      else if (b.dataset.act === 'copy') hooks.onCopy(this.config());
    });
    el.addEventListener('toggle', (e) => { if ((e.target as HTMLElement).matches('details.builder')) hooks.setOpen((e.target as HTMLDetailsElement).open); }, true);
  }

  config(): BuilderConfig {
    const params: Record<string, number | boolean> = {};
    for (const p of this.S.params) if (this.values[p.name] !== p.default) params[p.name] = this.values[p.name];
    return { params, ensemble: this.ensemble, seed: this.seed, compare: this.compare };
  }

  private onInput(e: Event) {
    const t = e.target as HTMLInputElement;
    if (t.dataset.p) {
      const d = this.S.params.find((x) => x.name === t.dataset.p)!;
      this.values[d.name] = d.type === 'bool' ? t.checked : d.type === 'int' ? Math.round(Number(t.value)) : Number(t.value);
      const out = this.el.querySelector<HTMLElement>(`[data-out="${d.name}"]`);
      if (out) out.textContent = fmt(d, this.values[d.name]);
      t.closest('.bp')?.classList.toggle('chg', this.values[d.name] !== d.default);
    } else if (t.id === 'b-ens') {
      this.ensemble = Math.round(Number(t.value));
      this.el.querySelector<HTMLElement>('[data-out="ens"]')!.textContent = this.ensemble ? `${this.ensemble} members` : 'single run';
    } else if (t.id === 'b-cmp') this.compare = t.checked;
    else return;
    this.renderDiff();
  }

  private renderDiff() {
    const c = this.config();
    const n = Object.keys(c.params).length;
    const el = this.el.querySelector<HTMLElement>('.bdiff');
    if (el) el.innerHTML = n ? `${n} change${n > 1 ? 's' : ''} from the default: ${Object.entries(c.params).map(([k, v]) => { const d = this.S.params.find((x) => x.name === k)!; return `<b>${esc(d.label)}</b> ${esc(fmt(d, d.default))}→${esc(fmt(d, v))}`; }).join(' · ')}` : 'Default parameters (the published forecast).';
  }

  private paramHTML(p: ParamDef) {
    const v = this.values[p.name];
    const tip = `${p.description ?? ''}${p.citation ? `\nSource: ${p.citation}` : ''}\nDefault: ${fmt(p, p.default)}${p.unit && p.type !== 'bool' ? ` ${p.unit}` : ''}`;
    const chg = v !== p.default ? ' chg' : '';
    if (p.type === 'bool') {
      return `<label class="bp bool${chg}" title="${esc(tip)}"><span class="bl">${esc(p.label)}</span><input type="checkbox" class="sw" data-p="${esc(p.name)}" ${v ? 'checked' : ''}/><i class="swi"></i></label>`;
    }
    const step = p.step ?? (p.type === 'int' ? 1 : ((p.max ?? 1) - (p.min ?? 0)) / 100);
    return `<div class="bp${chg}" title="${esc(tip)}"><div class="row"><span class="bl">${esc(p.label)}</span><span class="bv"><b data-out="${esc(p.name)}">${esc(fmt(p, v))}</b> <small>${esc(p.unit ?? '')}</small></span></div>
      <input type="range" data-p="${esc(p.name)}" min="${p.min}" max="${p.max}" step="${step}" value="${v}" aria-label="${esc(p.label)}"/></div>`;
  }

  render() {
    const S = this.S;
    const groups = S.groups?.length ? S.groups : [{ id: '', label: '' }];
    const inGroup = (g: string) => S.params.filter((p) => (p.group ?? '') === g || (!g && !groups.some((x) => x.id === p.group)));
    const pert = S.ensemble.perturbations.map((p: any) => `${p.name}${p.dist === 'uniform' ? ` U(${p.low}, ${p.high})` : p.dist === 'normal' ? ` ±${p.sigma}` : p.dist === 'lognormal' ? ` ×/÷${p.factor}` : ''}`).join(', ');
    this.el.innerHTML = `<details class="resp builder" data-k="builder" ${this.hooks.isOpen() ? 'open' : ''}>
      <summary>Advanced: build your model <small>${this.ensemble ? `${this.ensemble} members` : 'single run'}</small></summary>
      <div class="hint">Hover a setting for its source. Applies to Ours.</div>
      ${groups.map((g) => `${g.label ? `<div class="bg">${esc(g.label)}</div>` : ''}${inGroup(g.id).map((p) => this.paramHTML(p)).join('')}`).join('')}
      <div class="bg">Ensemble</div>
      <div class="bp" title="${esc(`Each member reruns the model with perturbed inputs: ${pert}. P(oil) = share of members with oil in a cell.`)}"><div class="row"><span class="bl">Members</span><span class="bv"><b data-out="ens">${this.ensemble ? `${this.ensemble} members` : 'single run'}</b></span></div>
        <input type="range" id="b-ens" min="0" max="${S.ensemble.max_members}" step="1" value="${this.ensemble}" aria-label="Ensemble members"/></div>
      <label class="bp bool" title="Run OpenDrift OpenOil (default settings) on the same spill, drawn amber next to yours"><span class="bl">Compare with OpenOil</span><input type="checkbox" class="sw" id="b-cmp" ${this.compare ? 'checked' : ''}/><i class="swi"></i></label>
      <div class="bdiff hint"></div>
      <div class="bbtns"><button data-act="run" class="brun">Run with these</button><button data-act="copy" title="Copy a link that reruns this model at this spill">Share</button><button data-act="reset" title="Back to the default parameters">Reset</button></div>
    </details>`;
    this.renderDiff();
  }
}
