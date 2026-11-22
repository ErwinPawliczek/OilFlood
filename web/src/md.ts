// Markdown + KaTeX renderer for the methodology page (and the model builder tooltips).
// Math: $$...$$ (display) and $...$ (inline) are pulled out before marked sees them, rendered with KaTeX, put back.

import { marked } from 'marked';
import katex from 'katex';

export function tex(src: string, display = false): string {
  try {
    return katex.renderToString(src, { displayMode: display, throwOnError: false, output: 'html' });
  } catch {
    return `<code>${src.replace(/[&<>]/g, (c) => `&#${c.charCodeAt(0)};`)}</code>`;
  }
}

/** Render markdown with $$display$$ and $inline$ math. GitHub-style ```math blocks are display math too. */
export function renderMarkdown(md: string): string {
  const math: string[] = [];
  const keep = (html: string) => `KTXM${math.push(html) - 1}XMTK`;
  let s = md
    .replace(/```math\n([\s\S]*?)```/g, (_, m) => keep(tex(m.trim(), true)))
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, m) => keep(tex(m.trim(), true)))
    // inline: $x$ but not prices like "$5" (needs a non-space right after the opening $ and before the closing $)
    .replace(/(^|[^\\$\w])\$(?=\S)([^$\n]+?)(?<=\S)\$(?!\d)/g, (_, pre, m) => pre + keep(tex(m, false)));
  s = marked.parse(s, { async: false, gfm: true }) as string;
  return s.replace(/KTXM(\d+)XMTK/g, (_, i) => math[Number(i)]);
}
