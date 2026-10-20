/* Saw Host — code editor: textarea + highlight layer. No dependencies. */
import { $, $$, html, raw, setHTML, icon, esc } from "./core.js";

/* ───────── Languages ───────── */
const EXT = {
  html: "html", htm: "html", xhtml: "html", svg: "xml", xml: "xml", css: "css", js: "js", mjs: "js", cjs: "js", jsx: "js", ts: "js", tsx: "js",
  json: "json", map: "json", webmanifest: "json", py: "py", md: "md", markdown: "md", txt: "txt", sh: "sh", bash: "sh", env: "sh",
  yml: "yaml", yaml: "yaml", php: "php", toml: "yaml", ini: "yaml", conf: "txt", csv: "txt", log: "txt",
};
export const LANG_LABEL = { html: "HTML", xml: "XML", css: "CSS", js: "JavaScript", json: "JSON", py: "Python", md: "Markdown", txt: "نص", sh: "Shell", yaml: "YAML", php: "PHP" };
export function langOf(name) {
  const n = String(name || "").toLowerCase();
  if (n === "dockerfile" || n === ".env" || n.startsWith(".env.")) return "sh";
  if (n === ".htaccess" || n === ".gitignore" || n === "license") return "txt";
  const i = n.lastIndexOf(".");
  return i < 0 ? "txt" : (EXT[n.slice(i + 1)] || "txt");
}
const COMMENT = { js: ["//"], css: ["/*", "*/"], html: ["<!--", "-->"], xml: ["<!--", "-->"], py: ["#"], sh: ["#"], yaml: ["#"], php: ["//"], md: ["<!--", "-->"] };
const PAIRS = { "(": ")", "[": "]", "{": "}", '"': '"', "'": "'", "`": "`" };
const CODE_LANGS = new Set(["js", "css", "json", "py", "php", "sh", "html", "xml", "yaml"]);

const kw = s => new RegExp("\\b(?:" + s.trim().split(/\s+/).join("|") + ")\\b").source;
const STR_DQ = String.raw`"(?:\\.|[^"\\\n])*"?`, STR_SQ = String.raw`'(?:\\.|[^'\\\n])*'?`;
const NUM = String.raw`\b(?:0[xX][\da-fA-F]+|\d[\d_]*\.?\d*(?:[eE][+-]?\d+)?)\b`;

const JS_KW = kw("break case catch class const continue debugger default delete do else export extends finally for from function if import in instanceof let new of return static super switch this throw try typeof var void while with yield async await null true false undefined NaN Infinity get set interface type enum implements public private protected readonly");
const JS_BI = kw("console window document Math JSON Object Array String Number Boolean Promise Map Set Date RegExp Error require module exports process fetch localStorage setTimeout setInterval Buffer");
const RULES = {
  js: [["c", String.raw`\/\/[^\n]*`], ["c", String.raw`\/\*[\s\S]*?(?:\*\/|$)`], ["s", STR_DQ], ["s", STR_SQ], ["s", String.raw`\x60(?:\\[\s\S]|[^\x60\\])*\x60?`],
  ["n", NUM], ["k", JS_KW], ["v", JS_BI], ["f", String.raw`[A-Za-z_$][\w$]*(?=\s*\()`]],
  php: [["c", String.raw`\/\/[^\n]*|#[^\n]*`], ["c", String.raw`\/\*[\s\S]*?(?:\*\/|$)`], ["s", STR_DQ], ["s", STR_SQ], ["t", String.raw`<\?(?:php|=)?|\?>`], ["v", String.raw`\$[A-Za-z_]\w*`],
  ["n", NUM], ["k", kw("function class public private protected static return if else elseif foreach for while echo new null true false array use namespace try catch throw require include isset empty as extends implements")], ["f", String.raw`[A-Za-z_]\w*(?=\s*\()`]],
  css: [["c", String.raw`\/\*[\s\S]*?(?:\*\/|$)`], ["s", STR_DQ], ["s", STR_SQ], ["k", String.raw`@[\w-]+`], ["n", String.raw`#(?:[\da-fA-F]{8}|[\da-fA-F]{6}|[\da-fA-F]{3,4})\b`],
  ["t", String.raw`[.#][A-Za-z_][\w-]*`], ["p", String.raw`--?[A-Za-z_][\w-]*(?=\s*:)|[A-Za-z-]+(?=\s*:)`], ["n", String.raw`-?\d*\.?\d+(?:px|em|rem|%|vh|vw|vmin|vmax|ch|s|ms|deg|fr)?\b`], ["v", String.raw`!important|var(?=\()`], ["f", String.raw`[a-z-]+(?=\()`]],
  json: [["p", String.raw`"(?:\\.|[^"\\\n])*"(?=\s*:)`], ["s", STR_DQ], ["n", String.raw`-?\b\d+\.?\d*(?:[eE][+-]?\d+)?\b`], ["k", kw("true false null")]],
  py: [["c", String.raw`#[^\n]*`], ["s", String.raw`"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$)`], ["s", STR_DQ], ["s", STR_SQ], ["n", NUM],
  ["k", kw("and as assert async await break class continue def del elif else except finally for from global if import in is lambda None nonlocal not or pass raise return try while with yield True False self")],
  ["v", String.raw`@\w+`], ["v", kw("print len range str int float list dict set tuple open isinstance super")], ["f", String.raw`[A-Za-z_]\w*(?=\()`]],
  sh: [["c", String.raw`(?:^|\s)#[^\n]*`], ["s", STR_DQ], ["s", STR_SQ], ["v", String.raw`\$(?:\{[^}\n]*\}|\w+|[@#?$!*])`], ["n", NUM],
  ["k", kw("if then else elif fi for do done while until case esac function in echo cd export source exit return local set unset FROM RUN CMD COPY ADD WORKDIR ENV EXPOSE ENTRYPOINT")]],
  yaml: [["c", String.raw`(?:^|\s)#[^\n]*`], ["p", String.raw`^[ \t-]*[\w."/-]+(?=\s*:)`], ["s", STR_DQ], ["s", STR_SQ], ["n", NUM], ["k", kw("true false null yes no on off")]],
  md: [["s", String.raw`^\x60\x60\x60[\s\S]*?(?:^\x60\x60\x60|$)`], ["h", String.raw`^#{1,6}[ \t].*$`], ["c", String.raw`^>.*$`], ["s", String.raw`\x60[^\x60\n]+\x60`], ["k", String.raw`\*\*[^*\n]+\*\*|__[^_\n]+__`],
  ["f", String.raw`!?\[[^\]\n]*\]\([^)\n]*\)`], ["k", String.raw`^[ \t]*(?:[-*+]|\d+\.)[ \t]`]],
};
const COMPILED = {};
for (const [lang, rules] of Object.entries(RULES)) COMPILED[lang] = { re: new RegExp(rules.map(r => "(" + r[1] + ")").join("|"), "gm"), cls: rules.map(r => r[0]) };

function tokenize(src, lang, out = []) {
  const c = COMPILED[lang];
  if (!c) { out.push(["", src]); return out; }
  c.re.lastIndex = 0;
  let last = 0, m;
  while ((m = c.re.exec(src))) {
    if (m[0] === "") { c.re.lastIndex++; continue; }
    if (m.index > last) out.push(["", src.slice(last, m.index)]);
    let g = 1; while (g < m.length && m[g] === undefined) g++;
    out.push([c.cls[g - 1], m[0]]);
    last = m.index + m[0].length;
  }
  if (last < src.length) out.push(["", src.slice(last)]);
  if (lang === "css") {                       // selectors outside {} are not "properties"
    let depth = 0;
    for (const t of out) {
      if (t[0] === "") { for (const ch of t[1]) { if (ch === "{") depth++; else if (ch === "}") depth = Math.max(0, depth - 1); } }
      else if (t[0] === "p" && depth === 0) t[0] = "t";
    }
  }
  return out;
}

function tokenizeHTML(src) {
  const out = [];
  const re = /<!--[\s\S]*?(?:-->|$)|<![A-Za-z][^>]*>?|<\/?[A-Za-z][^\s>/]*/g;
  let pos = 0, m;
  const text = (s) => { if (!s) return; const ent = /&#?\w+;/g; let l = 0, e; while ((e = ent.exec(s))) { if (e.index > l) out.push(["", s.slice(l, e.index)]); out.push(["v", e[0]]); l = e.index + e[0].length; } if (l < s.length) out.push(["", s.slice(l)]); };
  while ((m = re.exec(src))) {
    text(src.slice(pos, m.index));
    const tok = m[0];
    if (tok.startsWith("<!--")) { out.push(["c", tok]); pos = re.lastIndex; continue; }
    if (tok.startsWith("<!")) { out.push(["k", tok]); pos = re.lastIndex; continue; }
    out.push(["t", tok]);
    const tag = tok.replace(/^<\/?/, "").toLowerCase();
    // attributes
    let i = re.lastIndex;
    const at = /\s+|[^\s=>/"']+|=|"[^"]*"?|'[^']*'?|\/?>/y;
    let afterEq = false;
    for (; ;) {
      at.lastIndex = i; const a = at.exec(src);
      if (!a) break;
      const s = a[0]; i = at.lastIndex;
      if (/^\s+$/.test(s)) { out.push(["", s]); continue; }
      if (s === ">" || s === "/>") { out.push(["t", s]); break; }
      if (s === "=") { out.push(["", s]); afterEq = true; continue; }
      if (s[0] === '"' || s[0] === "'") { out.push(["s", s]); afterEq = false; continue; }
      out.push([afterEq ? "s" : "a", s]); afterEq = false;
    }
    pos = re.lastIndex = i;
    if (!tok.startsWith("</") && (tag === "script" || tag === "style")) {
      const close = src.toLowerCase().indexOf("</" + tag, pos);
      const end = close < 0 ? src.length : close;
      const inner = src.slice(pos, end);
      const isJson = tag === "script" && /type\s*=\s*["']application\/(?:ld\+)?json/i.test(tok + src.slice(m.index, pos));
      tokenize(inner, tag === "style" ? "css" : isJson ? "json" : "js", out);
      pos = re.lastIndex = end;
    }
  }
  text(src.slice(pos));
  return out;
}

export function highlightTokens(src, lang) {
  if (lang === "html" || lang === "xml") return tokenizeHTML(src);
  return tokenize(src, lang);
}
const escText = s => s.replace(/[&<>]/g, c => c === "&" ? "&amp;" : c === "<" ? "&lt;" : "&gt;");

function render(tokens, marks) {
  let out = "", pos = 0, mi = 0;
  for (const [cls, text] of tokens) {
    const start = pos, end = pos + text.length;
    pos = end;
    if (!marks.length) { out += cls ? `<span class="tk-${cls}">${escText(text)}</span>` : escText(text); continue; }
    let p = start;
    while (p < end) {
      while (mi < marks.length && marks[mi].e <= p) mi++;
      const mk = mi < marks.length ? marks[mi] : null;
      let to = end, m = 0;
      if (mk && mk.s <= p) { to = Math.min(end, mk.e); m = mk.cur ? 2 : 1; }
      else if (mk && mk.s < end) to = mk.s;
      const seg = escText(text.slice(p - start, to - start));
      const c = (cls ? "tk-" + cls : "") + (m ? " m" + m : "");
      out += c ? `<span class="${c.trim()}">${seg}</span>` : seg;
      p = to;
    }
  }
  return out;
}

/* ───────── Editor ───────── */
const LS = { get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch { return d; } }, set(k, v) { try { localStorage.setItem(k, v); } catch { /* ignore */ } } };
const BIG = 400_000;

export class Editor {
  constructor(host, { onChange, onSave, onCursor } = {}) {
    this.host = host; this.onChange = onChange; this.onSave = onSave; this.onCursor = onCursor;
    this.lang = "txt"; this.name = ""; this.marks = []; this.q = { text: "", cs: false, re: false }; this.matches = []; this.mi = -1;
    this.fs = Math.min(22, Math.max(11, +LS.get("saw-ed-fs", 14))); this.wrap = LS.get("saw-ed-wrap", "0") === "1"; this.escTab = false; this.lines = 0;
    setHTML(host, html`<div class="ed" dir="ltr">
      <div class="ed-find" hidden>
        <div class="row"><input class="input sm f-q" placeholder="بحث" aria-label="بحث"><span class="f-count"></span>
          <button class="iconbtn sm" data-a="prev" title="السابق (Shift+Enter)" type="button">${icon("chevU", "sm")}</button><button class="iconbtn sm" data-a="next" title="التالي (Enter)" type="button">${icon("chevD", "sm")}</button>
          <button class="chip sm" data-a="cs" title="مطابقة الحالة" type="button">Aa</button><button class="chip sm" data-a="re" title="تعبير نمطي" type="button">.*</button>
          <button class="iconbtn sm" data-a="close" title="إغلاق (Esc)" type="button">${icon("x", "sm")}</button></div>
        <div class="row f-rrow"><input class="input sm f-r" placeholder="استبدال" aria-label="استبدال"><button class="btn ghost sm" data-a="rep" type="button">استبدل</button><button class="btn ghost sm" data-a="repall" type="button">الكل</button></div>
      </div>
      <div class="ed-body"><div class="ed-gutter" aria-hidden="true"><pre></pre></div>
        <div class="ed-code"><div class="ed-line" hidden></div><pre class="ed-hl" aria-hidden="true"></pre>
          <textarea class="ed-ta" spellcheck="false" wrap="off" autocapitalize="off" autocomplete="off" autocorrect="off" aria-label="محرر الكود" dir="ltr"></textarea></div></div>
      <div class="ed-status"><span class="s-lang"></span><span class="s-pos"></span><span class="s-sel"></span><span class="sp"></span>
        <button type="button" data-a="fm" title="تصغير الخط" aria-label="تصغير الخط">A−</button><button type="button" data-a="fp" title="تكبير الخط" aria-label="تكبير الخط">A+</button>
        <button type="button" data-a="wrap" aria-pressed="false" title="التفاف الأسطر">التفاف</button><button type="button" data-a="goto" title="اذهب إلى سطر (Ctrl+G)">سطر</button><span class="s-size"></span></div></div>`);
    const q = s => $(s, host);
    this.ta = q(".ed-ta"); this.hl = q(".ed-hl"); this.gut = q(".ed-gutter pre"); this.gutBox = q(".ed-gutter"); this.find = q(".ed-find"); this.fq = q(".f-q"); this.fr = q(".f-r"); this.lineEl = q(".ed-line"); this.box = q(".ed");
    this._applyView();
    const ta = this.ta;
    ta.addEventListener("input", () => { this._refresh(); this.onChange && this.onChange(); });
    ta.addEventListener("scroll", () => this._sync());
    ["keyup", "click", "focus", "select", "pointerup"].forEach(e => ta.addEventListener(e, () => this._cursor()));
    document.addEventListener("selectionchange", this._sel = () => { if (document.activeElement === ta) this._cursor(); });
    ta.addEventListener("beforeinput", e => this._beforeInput(e));
    ta.addEventListener("keydown", e => this._keydown(e));
    host.addEventListener("click", e => { const b = e.target.closest("[data-a]"); if (b) this._action(b.dataset.a); });
    this.fq.addEventListener("input", () => { this.q.text = this.fq.value; this._search(true); });
    this.fq.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); this._step(e.shiftKey ? -1 : 1); } else if (e.key === "Escape") { e.preventDefault(); this.closeFind(); } });
    this.fr.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); this._replaceOne(); } else if (e.key === "Escape") { e.preventDefault(); this.closeFind(); } });
    this._ro = new ResizeObserver(() => this._sync()); this._ro.observe(ta);
  }
  destroy() { document.removeEventListener("selectionchange", this._sel); this._ro.disconnect(); }

  /* public */
  get value() { return this.ta.value; }
  load(text, name) {
    this.name = name || ""; this.lang = langOf(name); this.ta.value = text; this.marks = []; this.matches = []; this.mi = -1;
    this.ta.scrollTop = 0; this.ta.scrollLeft = 0; this.ta.setSelectionRange(0, 0);
    this._refresh(true); this._cursor();
    $(".s-lang", this.host).textContent = LANG_LABEL[this.lang] || "نص";
    if (!this.find.hidden) this._search(false);
  }
  setReadOnly(b) { this.ta.readOnly = b; }
  focus() { this.ta.focus({ preventScroll: true }); }
  state() { return { st: this.ta.scrollTop, sl: this.ta.scrollLeft, a: this.ta.selectionStart, b: this.ta.selectionEnd }; }
  restore(s) { if (!s) return; this.ta.scrollTop = s.st; this.ta.scrollLeft = s.sl; this.ta.setSelectionRange(s.a, s.b); this._sync(); this._cursor(); }
  openFind(replace = false) {
    this.find.hidden = false; this.box.classList.add("finding"); $(".f-rrow", this.host).hidden = !replace;
    const sel = this.ta.value.slice(this.ta.selectionStart, this.ta.selectionEnd);
    if (sel && !sel.includes("\n")) { this.fq.value = sel; this.q.text = sel; }
    this.fq.focus(); this.fq.select(); this._search(false);
  }
  closeFind() { this.find.hidden = true; this.box.classList.remove("finding"); this.marks = []; this.matches = []; this._paint(); this.focus(); }
  gotoLine(n) {
    const lines = this.ta.value.split("\n"); n = Math.max(1, Math.min(lines.length, n | 0));
    let off = 0; for (let i = 0; i < n - 1; i++) off += lines[i].length + 1;
    this.ta.setSelectionRange(off, off + lines[n - 1].length); this.focus(); this._reveal(n - 1); this._cursor();
  }

  /* view */
  _lh() { return Math.round(this.fs * 1.65); }
  _applyView() {
    this.box.style.setProperty("--ed-fs", this.fs + "px"); this.box.style.setProperty("--ed-lh", this._lh() + "px");
    this.ta.wrap = this.wrap ? "soft" : "off"; this.box.classList.toggle("wrap", this.wrap);
    const w = $("[data-a=wrap]", this.host); if (w) w.setAttribute("aria-pressed", String(this.wrap));
    this.gutBox.hidden = this.wrap;
  }
  _sync() { this.hl.scrollTop = this.ta.scrollTop; this.hl.scrollLeft = this.ta.scrollLeft; this.gutBox.scrollTop = this.ta.scrollTop; this._lineHL(); }
  _paint() {
    const v = this.ta.value;
    if (v.length > BIG || this.lang === "txt") { this.hl.innerHTML = this.marks.length ? render([["", v]], this.marks) + "\n" : escText(v) + "\n"; return; }
    this.hl.innerHTML = render(highlightTokens(v, this.lang), this.marks) + "\n";
  }
  _refresh(force) {
    cancelAnimationFrame(this._raf);
    const go = () => {
      this._paint();
      const n = this.ta.value.split("\n").length;
      if (n !== this.lines || force) { this.lines = n; let s = ""; for (let i = 1; i <= n; i++) s += i + "\n"; this.gut.textContent = s; this.gutBox.style.setProperty("--digits", String(String(n).length)); }
      $(".s-size", this.host).textContent = new Intl.NumberFormat("en-US").format(new Blob([this.ta.value]).size) + " B";
      this._sync();
    };
    if (force) go(); else this._raf = requestAnimationFrame(go);
    if (!this.find.hidden && !force) this._search(false, true);
  }
  _cursor() {
    const ta = this.ta, v = ta.value, p = ta.selectionStart;
    const before = v.lastIndexOf("\n", p - 1) + 1;
    let ln = 1; for (let i = v.indexOf("\n"); i >= 0 && i < p; i = v.indexOf("\n", i + 1)) ln++;
    const col = p - before + 1, sel = Math.abs(ta.selectionEnd - p);
    $(".s-pos", this.host).textContent = `سطر ${ln}، عمود ${col}`;
    $(".s-sel", this.host).textContent = sel ? `(${sel} محدد)` : "";
    this._ln = ln; this._lineHL();
    if (this.onCursor) this.onCursor(ln, col);
  }
  _lineHL() {
    if (this.wrap || this.ta.selectionStart !== this.ta.selectionEnd) { this.lineEl.hidden = true; return; }
    this.lineEl.hidden = false; this.lineEl.style.transform = `translateY(${(this._ln - 1) * this._lh() - this.ta.scrollTop}px)`;
  }
  _reveal(lineIdx) {
    const lh = this._lh(), top = lineIdx * lh, h = this.ta.clientHeight;
    if (top < this.ta.scrollTop || top + lh > this.ta.scrollTop + h) this.ta.scrollTop = Math.max(0, top - h / 3);
    this._sync();
  }
  _revealOffset(off) {
    const line = this.ta.value.slice(0, off).split("\n").length - 1; this._reveal(line);
    if (!this.wrap) { const ls = this.ta.value.lastIndexOf("\n", off - 1) + 1; const x = (off - ls) * this.fs * 0.6; const w = this.ta.clientWidth; if (x < this.ta.scrollLeft || x > this.ta.scrollLeft + w - 40) this.ta.scrollLeft = Math.max(0, x - w / 2); this._sync(); }
  }

  /* editing primitives (keep native undo stack) */
  _put(start, end, text, selA, selB) {
    const ta = this.ta; ta.focus({ preventScroll: true }); ta.setSelectionRange(start, end);
    let ok = false;
    try { ok = text === "" ? (start < end && document.execCommand("delete")) : document.execCommand("insertText", false, text); } catch { ok = false; }
    if (!ok) { ta.setRangeText(text, start, end, "end"); ta.dispatchEvent(new Event("input", { bubbles: true })); }
    if (selA !== undefined) ta.setSelectionRange(selA, selB === undefined ? selA : selB);
    this._cursor();
  }
  _unit() { return this.lang === "py" ? "    " : "  "; }
  _lineRange(a, b) {
    const v = this.ta.value, s = v.lastIndexOf("\n", a - 1) + 1; let e = v.indexOf("\n", b > a && v[b - 1] === "\n" ? b - 1 : b); if (e < 0) e = v.length; return [s, e];
  }
  _beforeInput(e) {
    if (this.ta.readOnly || e.isComposing) return;
    const ta = this.ta, v = ta.value, a = ta.selectionStart, b = ta.selectionEnd, t = e.inputType;
    if (t === "insertLineBreak" || t === "insertParagraph") {
      const ls = v.lastIndexOf("\n", a - 1) + 1, line = v.slice(ls, a), indent = (line.match(/^[ \t]*/) || [""])[0];
      const prev = v[a - 1], next = v[b];
      let extra = "";
      if (/[{(\[]$/.test(line.trimEnd()) || (this.lang === "py" && /:\s*$/.test(line)) || ((this.lang === "html" || this.lang === "xml") && /<(?!\/|!|br|hr|img|meta|link|input)[A-Za-z][^>]*[^/]>$/.test(line.trimEnd()) && next === "<")) extra = this._unit();
      e.preventDefault();
      if ("{[(".includes(prev) && prev && PAIRS[prev] === next) { this._put(a, b, "\n" + indent + extra + "\n" + indent, a + 1 + indent.length + extra.length); return; }
      if ((this.lang === "html" || this.lang === "xml") && extra && next === "<") { this._put(a, b, "\n" + indent + extra + "\n" + indent, a + 1 + indent.length + extra.length); return; }
      this._put(a, b, "\n" + indent + extra); this._revealOffset(ta.selectionStart); return;
    }
    if (t === "insertText" && e.data && e.data.length === 1 && CODE_LANGS.has(this.lang)) {
      const ch = e.data, next = v[b], prev = v[a - 1] || "";
      if (a !== b && PAIRS[ch]) { e.preventDefault(); this._put(a, b, ch + v.slice(a, b) + PAIRS[ch], a + 1, b + 1); return; }
      if ((ch === ")" || ch === "]" || ch === "}" || ch === '"' || ch === "'" || ch === "`") && next === ch) { e.preventDefault(); ta.setSelectionRange(a + 1, a + 1); this._cursor(); return; }
      if (PAIRS[ch]) {
        const quote = ch === '"' || ch === "'" || ch === "`";
        if (quote && (/\w/.test(prev) || (next && /\w/.test(next)))) return;
        if (!quote && next && /\w/.test(next)) return;
        e.preventDefault(); this._put(a, b, ch + PAIRS[ch], a + 1);
      }
    }
    if (t === "deleteContentBackward" && a === b && a > 0 && PAIRS[v[a - 1]] && PAIRS[v[a - 1]] === v[a] && CODE_LANGS.has(this.lang)) { e.preventDefault(); this._put(a - 1, a + 1, ""); }
  }
  _keydown(e) {
    if (e.isComposing) return;
    const ta = this.ta, mod = e.ctrlKey || e.metaKey, k = e.key;
    if (mod && !e.altKey) {
      const lk = k.toLowerCase();
      if (lk === "s") { e.preventDefault(); this.onSave && this.onSave(); return; }
      if (lk === "f") { e.preventDefault(); this.openFind(false); return; }
      if (lk === "h") { e.preventDefault(); this.openFind(true); return; }
      if (lk === "g") { e.preventDefault(); this._goto(); return; }
      if (k === "/") { e.preventDefault(); this._comment(); return; }
      if (lk === "d") { e.preventDefault(); this._duplicate(); return; }
      if (lk === "k" && e.shiftKey) { e.preventDefault(); this._deleteLines(); return; }
      if (k === "Enter") { e.preventDefault(); const [, en] = this._lineRange(ta.selectionStart, ta.selectionEnd); this._put(en, en, "\n" + (ta.value.slice(ta.value.lastIndexOf("\n", en - 1) + 1, en).match(/^[ \t]*/) || [""])[0]); return; }
    }
    if (e.altKey && (k === "ArrowUp" || k === "ArrowDown")) { e.preventDefault(); this._move(k === "ArrowUp" ? -1 : 1); return; }
    if (k === "Escape") { if (!this.find.hidden) { this.closeFind(); } else this.escTab = true; return; }
    if (k === "Tab" && !mod && !e.altKey) {
      if (this.escTab) { this.escTab = false; return; }
      e.preventDefault();
      const a = ta.selectionStart, b = ta.selectionEnd, v = ta.value, u = this._unit();
      if (a === b && !e.shiftKey) { this._put(a, b, u); return; }
      const [s, en] = this._lineRange(a, b), block = v.slice(s, en);
      if (e.shiftKey) {
        const re = new RegExp("^(?:\\t|" + u + "| {1," + u.length + "})");
        const out = block.split("\n").map(l => l.replace(re, "")).join("\n");
        const first = block.match(re); const d1 = first ? first[0].length : 0;
        this._put(s, en, out, Math.max(s, a - d1), b - (block.length - out.length));
      } else {
        const out = block.split("\n").map(l => l.length ? u + l : l).join("\n");
        this._put(s, en, out, a + u.length, b + (out.length - block.length));
      }
      return;
    }
    if (k !== "Tab" && k !== "Shift") this.escTab = false;
  }
  _comment() {
    const ta = this.ta, a = ta.selectionStart, b = ta.selectionEnd, c = COMMENT[this.lang]; if (!c) return;
    const [s, en] = this._lineRange(a, b), lines = ta.value.slice(s, en).split("\n");
    const nonEmpty = lines.filter(l => l.trim());
    let out;
    if (c.length === 1) {
      const on = nonEmpty.length && nonEmpty.every(l => l.trimStart().startsWith(c[0]));
      out = lines.map(l => !l.trim() ? l : on ? l.replace(c[0] + " ", "").replace(c[0], "") : l.replace(/^(\s*)/, "$1" + c[0] + " ")).join("\n");
    } else {
      const text = lines.join("\n"), on = text.trimStart().startsWith(c[0]) && text.trimEnd().endsWith(c[1]);
      out = on ? text.replace(c[0] + " ", "").replace(c[0], "").replace(" " + c[1], "").replace(c[1], "") : lines[0].match(/^\s*/)[0] + c[0] + " " + text.trimStart() + " " + c[1];
    }
    this._put(s, en, out, s, s + out.length);
  }
  _duplicate() {
    const ta = this.ta, a = ta.selectionStart, b = ta.selectionEnd, v = ta.value;
    if (a !== b) { this._put(b, b, v.slice(a, b), b, b + (b - a)); return; }
    const [s, en] = this._lineRange(a, b); this._put(en, en, "\n" + v.slice(s, en), a + (en - s) + 1);
  }
  _deleteLines() {
    const ta = this.ta, [s, en] = this._lineRange(ta.selectionStart, ta.selectionEnd), v = ta.value;
    const end = en < v.length ? en + 1 : en, start = end === v.length && s > 0 && en >= v.length ? s - 1 : s;
    this._put(start, end, "", start);
  }
  _move(dir) {
    const ta = this.ta, v = ta.value, a = ta.selectionStart, b = ta.selectionEnd, [s, en] = this._lineRange(a, b);
    if (dir < 0) {
      if (s === 0) return; const ps = v.lastIndexOf("\n", s - 2) + 1, prev = v.slice(ps, s - 1), cur = v.slice(s, en);
      this._put(ps, en, cur + "\n" + prev, a - prev.length - 1, b - prev.length - 1);
    } else {
      if (en >= v.length) return; let ne = v.indexOf("\n", en + 1); if (ne < 0) ne = v.length; const next = v.slice(en + 1, ne), cur = v.slice(s, en);
      this._put(s, ne, next + "\n" + cur, a + next.length + 1, b + next.length + 1);
    }
  }
  _goto() {
    const n = prompt("رقم السطر:", String(this._ln || 1)); if (n && /^\d+$/.test(n)) this.gotoLine(+n);
  }

  /* find & replace */
  _action(a) {
    if (a === "next") this._step(1); else if (a === "prev") this._step(-1); else if (a === "close") this.closeFind();
    else if (a === "cs" || a === "re") { this.q[a] = !this.q[a]; $(`[data-a=${a}]`, this.host).setAttribute("aria-pressed", String(this.q[a])); this._search(true); }
    else if (a === "rep") this._replaceOne(); else if (a === "repall") this._replaceAll();
    else if (a === "fp" || a === "fm") { this.fs = Math.min(22, Math.max(11, this.fs + (a === "fp" ? 1 : -1))); LS.set("saw-ed-fs", this.fs); this._applyView(); this._sync(); }
    else if (a === "wrap") { this.wrap = !this.wrap; LS.set("saw-ed-wrap", this.wrap ? "1" : "0"); this._applyView(); this._sync(); }
    else if (a === "goto") this._goto();
  }
  _regex() {
    if (!this.q.text) return null;
    try { return new RegExp(this.q.re ? this.q.text : this.q.text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g" + (this.q.cs ? "" : "i") + "m"); } catch { return null; }
  }
  _search(jump, keep) {
    const re = this._regex(), v = this.ta.value; this.matches = [];
    $(".f-q", this.host).classList.toggle("bad", !!this.q.text && !re);
    if (re) { let m; while ((m = re.exec(v)) && this.matches.length < 5000) { if (m[0] === "") { re.lastIndex++; continue; } this.matches.push({ s: m.index, e: m.index + m[0].length }); } }
    if (!keep || this.mi >= this.matches.length) {
      const p = this.ta.selectionStart; let i = this.matches.findIndex(m => m.s >= p); this.mi = this.matches.length ? (i < 0 ? 0 : i) : -1;
    }
    this._marks(); if (jump && this.mi >= 0) this._select(this.mi, false);
  }
  _marks() {
    this.marks = this.matches.map((m, i) => ({ s: m.s, e: m.e, cur: i === this.mi }));
    $(".f-count", this.host).textContent = this.q.text ? (this.matches.length ? `${this.mi + 1} / ${this.matches.length}${this.matches.length >= 5000 ? "+" : ""}` : "لا نتائج") : "";
    this._paint();
  }
  _select(i, focus) {
    const m = this.matches[i]; if (!m) return; this.mi = i;
    if (focus) this.ta.focus({ preventScroll: true });
    this.ta.setSelectionRange(m.s, m.e); this._revealOffset(m.s); this._marks();
  }
  _step(d) { if (!this.matches.length) { this._search(true); return; } this._select((this.mi + d + this.matches.length) % this.matches.length, false); }
  _replaceOne() {
    const m = this.matches[this.mi]; if (!m) return;
    const re = this._regex(), src = this.ta.value.slice(m.s, m.e);
    const rep = this.q.re ? src.replace(new RegExp(re.source, re.flags.replace("g", "")), this.fr.value) : this.fr.value;
    this._put(m.s, m.e, rep, m.s + rep.length); this._search(true);
  }
  _replaceAll() {
    const re = this._regex(); if (!re || !this.matches.length) return;
    const n = this.matches.length, out = this.ta.value.replace(re, this.q.re ? this.fr.value : () => this.fr.value);
    this._put(0, this.ta.value.length, out, 0); this._search(false);
    $(".f-count", this.host).textContent = `استُبدل ${n}`;
  }
}
