/* Saw Host — tiny SVG charts (no dependencies). */
import { html, raw, esc, nf } from "./core.js";

const dfm = new Intl.DateTimeFormat("ar-IQ-u-nu-latn", { day: "numeric", month: "short" });

/** Bars for a daily series. labels: ISO dates. */
export function barChart({ labels, values, title, h = 150 }) {
  const n = values.length, W = 600, pad = { l: 8, r: 8, t: 14, b: 26 };
  const max = Math.max(1, ...values), iw = W - pad.l - pad.r, ih = h - pad.t - pad.b, bw = iw / n;
  const nice = max <= 4 ? max : Math.ceil(max / 4) * 4;
  const grid = [0, .5, 1].map(f => { const y = pad.t + ih - ih * f; return `<line x1="${pad.l}" x2="${W - pad.r}" y1="${y}" y2="${y}" class="gl"/><text x="${W - pad.r}" y="${y - 3}" class="gt" text-anchor="end">${nf.format(Math.round(nice * f))}</text>`; }).join("");
  const bars = values.map((v, i) => {
    const bh = Math.max(v ? 2 : 0, ih * v / nice), x = pad.l + i * bw + bw * .18, y = pad.t + ih - bh;
    return `<g><rect x="${x}" y="${pad.t}" width="${bw * .64}" height="${ih}" fill="transparent"><title>${esc(dfm.format(new Date(labels[i])))}: ${v}</title></rect><rect x="${x}" y="${y}" width="${bw * .64}" height="${bh}" rx="2.5" class="bar${i === n - 1 ? " last" : ""}" pointer-events="none"/></g>`;
  }).join("");
  const xl = labels.map((l, i) => (i % 3 === 0 && i <= n - 3 || i === n - 1) ? `<text x="${pad.l + i * bw + bw / 2}" y="${h - 8}" class="xt" text-anchor="middle">${esc(dfm.format(new Date(l)))}</text>` : "").join("");
  return html`<svg class="chart" viewBox="0 0 ${W} ${h}" role="img" aria-label="${title}" preserveAspectRatio="xMidYMid meet">${raw(grid + bars + xl)}</svg>`;
}

/** Donut with centre label. parts: [{label, value, cls}] */
export function donut(parts, centre, sub) {
  const total = parts.reduce((a, p) => a + p.value, 0) || 1, R = 46, C = 2 * Math.PI * R;
  let off = 0;
  const arcs = parts.map(p => { const len = C * p.value / total, s = `<circle cx="60" cy="60" r="${R}" fill="none" class="arc ${p.cls}" stroke-width="14" stroke-dasharray="${Math.max(0, len - 2)} ${C - Math.max(0, len - 2)}" stroke-dashoffset="${-off}" transform="rotate(-90 60 60)"/>`; off += len; return s; }).join("");
  return html`<svg class="donut" viewBox="0 0 120 120" role="img" aria-label="${centre} ${sub}"><circle cx="60" cy="60" r="${R}" fill="none" class="arc bg" stroke-width="14"/>${raw(arcs)}<text x="60" y="58" text-anchor="middle" class="dv">${centre}</text><text x="60" y="76" text-anchor="middle" class="ds">${sub}</text></svg>`;
}
