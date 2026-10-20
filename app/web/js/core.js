/* Saw Host — core: templating, API client, dialogs, toasts, formatting, theme.
   Every dynamic value that reaches the DOM goes through html`` (auto-escaped). */
import { ic } from "./icons.js";
export { ic };

export const API = "/panel/api";

/* ───────── Templating ───────── */
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ESC[c]);
class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
export const raw = s => new Raw(String(s ?? ""));
const part = v => v instanceof Raw ? v.s : Array.isArray(v) ? v.map(part).join("") : (v === false || v == null) ? "" : esc(v);
export const html = (strs, ...vals) => new Raw(strs.reduce((o, s, i) => o + s + (i < vals.length ? part(vals[i]) : ""), ""));
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
export const setHTML = (el, r) => { el.innerHTML = part(r); return el; };
export const icon = (n, c = "") => raw(ic(n, c));

/* ───────── State ───────── */
export const state = { me: null, bearer: "", csrf: "", pending: null, theme: null };
const meta = document.querySelector('meta[name="saw-csrf"]');
if (meta && meta.content && !meta.content.startsWith("__")) state.csrf = meta.content;
export const tg = (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) ? window.Telegram.WebApp : null;
export const isMini = !!tg;

export const go = h => { if (location.hash === h) window.dispatchEvent(new HashChangeEvent("hashchange")); else location.hash = h; };
export const openExt = url => { if (tg) { try { tg.openLink(url); return; } catch { /* fall through */ } } window.open(url, "_blank", "noopener,noreferrer"); };

/* ───────── Formatting ───────── */
export const nf = new Intl.NumberFormat("en-US");
const dtf = new Intl.DateTimeFormat("ar-IQ-u-nu-latn", { dateStyle: "medium", timeStyle: "short" });
const dfm = new Intl.DateTimeFormat("ar-IQ-u-nu-latn", { dateStyle: "medium" });
const LRM = "‎";
export const fmtDT = v => v ? dtf.format(new Date(v)) : "—";
export const fmtD = v => v ? dfm.format(new Date(v)) : "—";
export const fmtSize = b => LRM + (b < 1024 ? `${b} B` : b < 1048576 ? `${(b / 1024).toFixed(1)} KB` : `${(b / 1048576).toFixed(1)} MB`) + LRM;
export const fmtMB = mb => LRM + (mb >= 1024 ? `${(mb / 1024).toFixed(mb >= 10240 ? 0 : 1)} GB` : `${nf.format(mb)} MB`) + LRM;
export const fmtIQD = n => nf.format(n) + " د.ع";
export const daysLeft = iso => iso ? Math.ceil((new Date(iso).getTime() - Date.now()) / 864e5) : null;
export function ago(iso) {
  if (!iso) return "—";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 45) return "الآن";
  if (s < 3600) { const m = Math.round(s / 60); return m === 1 ? "قبل دقيقة" : m === 2 ? "قبل دقيقتين" : m <= 10 ? `قبل ${m} دقائق` : `قبل ${m} دقيقة`; }
  if (s < 86400) { const h = Math.round(s / 3600); return h === 1 ? "قبل ساعة" : h === 2 ? "قبل ساعتين" : h <= 10 ? `قبل ${h} ساعات` : `قبل ${h} ساعة`; }
  if (s < 86400 * 30) { const d = Math.round(s / 86400); return d === 1 ? "أمس" : d === 2 ? "قبل يومين" : d <= 10 ? `قبل ${d} أيام` : `قبل ${d} يوماً`; }
  return fmtD(iso);
}
export const initials = n => (String(n || "?").trim().match(/[\p{L}\p{N}]/u) || ["؟"])[0].toUpperCase();
export const avatarCls = seed => "c" + (Math.abs([...String(seed ?? "")].reduce((a, c) => a * 31 + c.charCodeAt(0), 7)) % 6);
export const avatar = (name, seed, size = "") => html`<span class="avatar ${size} ${avatarCls(seed ?? name)}">${initials(name)}</span>`;
export const plural = (n, one, two, few, many) => n === 1 ? one : n === 2 ? two : n >= 3 && n <= 10 ? `${nf.format(n)} ${few}` : `${nf.format(n)} ${many}`;

/* ───────── Theme ───────── */
const THEME_KEY = "saw-theme";
const rootEl = document.documentElement;
export const curTheme = () => rootEl.getAttribute("data-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
function paintChrome(t) {
  const c = t === "dark" ? "#09111f" : "#f3f5f9";
  const m = document.querySelector("meta[name=theme-color]"); if (m) m.setAttribute("content", c);
  if (tg) { try { tg.setHeaderColor(c); tg.setBackgroundColor(c); } catch { /* old client */ } }
  $$("[data-theme-btn]").forEach(b => setHTML(b, icon(t === "dark" ? "sun" : "moon")));
}
export function applyTheme(t) {
  rootEl.setAttribute("data-theme", t);
  try { localStorage.setItem(THEME_KEY, t); } catch { /* storage blocked */ }
  paintChrome(t);
}
export const toggleTheme = () => applyTheme(curTheme() === "dark" ? "light" : "dark");
export const initTheme = () => {
  try { const t = localStorage.getItem(THEME_KEY); if (t === "dark" || t === "light") rootEl.setAttribute("data-theme", t); } catch { /* ignore */ }
  paintChrome(curTheme());
};

/* ───────── API ───────── */
export class ApiError extends Error { constructor(m, s, code) { super(m); this.status = s; this.code = code || ""; } }
let reloaded = false;
function headers(extra = {}) {
  const h = { "X-Saw-CSRF": state.csrf || "", ...extra };
  if (state.bearer) h["Authorization"] = "Bearer " + state.bearer;
  return h;
}
export async function api(path, opt = {}) {
  const o = { method: opt.method || "GET", headers: headers(), credentials: "same-origin", cache: "no-store" };
  if (opt.json !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(opt.json); }
  let r;
  try { r = await fetch(API + path, o); } catch { throw new ApiError("تعذّر الاتصال بالسيرفر. تحقق من الإنترنت.", 0); }
  let d = null; try { d = await r.json(); } catch { /* not json */ }
  if (r.status === 403 && d && d.code === "csrf" && !reloaded) {
    reloaded = true; location.reload(); throw new ApiError("جارِ تحديث الجلسة…", 403, "csrf");
  }
  if (r.status === 401 && !opt.quiet401) {
    state.me = null;
    if (!location.hash.startsWith("#/login")) go("#/login");
    throw new ApiError(d?.error || "سجّل الدخول للمتابعة.", 401);
  }
  if (!r.ok) throw new ApiError(d?.error || `خطأ غير متوقع (${r.status}).`, r.status, d?.code);
  return d;
}
export const post = (p, json = {}) => api(p, { method: "POST", json });
export const put = (p, json = {}) => api(p, { method: "PUT", json });

export function xhrUpload(path, form, onProgress, onSent) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", API + path);
    const h = headers(); Object.keys(h).forEach(k => x.setRequestHeader(k, h[k]));
    x.upload.onprogress = e => { if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total); };
    x.upload.onload = () => { if (onSent) onSent(); };
    x.onload = () => {
      let d = null; try { d = JSON.parse(x.responseText); } catch { /* ignore */ }
      if (x.status >= 200 && x.status < 300) resolve(d);
      else reject(new ApiError(d?.error || `فشل الرفع (${x.status}).`, x.status, d?.code));
    };
    x.onerror = () => reject(new ApiError("انقطع الاتصال أثناء الرفع.", 0));
    x.send(form);
  });
}
export async function downloadVia(path, body = {}) {
  try {
    const r = await post(path, body);
    const url = location.origin + r.url;
    if (tg) openExt(url); else location.href = url;
  } catch (e) { fail(e); }
}

/* ───────── Timers bound to the current view ───────── */
export const later = (ctx, fn, ms) => { const t = setTimeout(() => { if (ctx.alive()) fn(); }, ms); ctx.timers.push(t); return t; };

/* ───────── Toasts ───────── */
export function toast(msg, kind = "", dur = 0) {
  const box = $("#toasts"); if (!box) return;
  const t = document.createElement("div");
  t.className = "toast " + kind;
  setHTML(t, html`${kind === "ok" ? icon("checkCircle", "sm") : kind === "bad" ? icon("alert", "sm") : ""}<span>${msg}</span>`);
  box.append(t);
  setTimeout(() => t.remove(), dur || (kind === "bad" || kind === "warn" ? 6000 : 3000));
  return t;
}
export const fail = e => { if (e && e.status !== 401 && e.code !== "csrf") toast(e.message || "حدث خطأ.", "bad"); };

export async function copyText(t, msg = "تم النسخ") {
  try { await navigator.clipboard.writeText(t); }
  catch {
    const a = document.createElement("textarea"); a.value = t; a.style.cssText = "position:fixed;opacity:0";
    document.body.append(a); a.select(); try { document.execCommand("copy"); } catch { /* ignore */ } a.remove();
  }
  toast(msg, "ok");
}
document.addEventListener("click", e => {
  const a = e.target.closest("a[target=_blank]");
  if (a && tg && a.href) { e.preventDefault(); openExt(a.href); return; }
  const c = e.target.closest("[data-copy]");
  if (c) { e.preventDefault(); copyText(c.dataset.copy); }
});

/* ───────── Dialogs ───────── */
export function openDialog({ title, body, buttons = [], size = "", drawer = false, onOpen, noHead = false }) {
  return new Promise(resolve => {
    const d = document.createElement("dialog");
    d.className = "dlg " + (drawer ? "drawer " : "") + size;
    setHTML(d, html`${noHead ? "" : html`<div class="dlg-head"><h2>${title}</h2><button class="iconbtn" data-x aria-label="إغلاق" type="button">${icon("x")}</button></div>`}<div class="dlg-body">${body}</div><div class="dlg-err form-err" hidden style="margin:0 22px 14px"></div><div class="dlg-foot"></div>`);
    const foot = $(".dlg-foot", d), err = $(".dlg-err", d);
    if (!buttons.length) foot.remove();
    buttons.forEach(b => {
      const el = document.createElement("button");
      el.type = "button"; el.className = "btn " + (b.kind || "ghost"); el.textContent = b.label;
      if (b.kind && b.kind !== "ghost" && b.kind !== "danger" && b.kind !== "quiet") el.dataset.main = "1";
      if (b.kind === undefined && b === buttons[0]) { el.className = "btn"; el.dataset.main = "1"; }
      el.onclick = async () => {
        err.hidden = true;
        if (!b.run) { d.close("cancel"); return; }
        el.classList.add("is-busy");
        try { const res = await b.run(d); if (res !== false) d.close("ok"); }
        catch (e) { err.textContent = e.message || "حدث خطأ."; err.hidden = false; }
        finally { el.classList.remove("is-busy"); }
      };
      foot.append(el);
    });
    const x = $("[data-x]", d); if (x) x.onclick = () => d.close("cancel");
    d.addEventListener("keydown", e => {
      if (e.key === "Enter" && e.target.tagName === "INPUT" && !e.isComposing) { const m = $("[data-main]", d); if (m) { e.preventDefault(); m.click(); } }
    });
    d.addEventListener("click", e => { if (e.target === d) d.close("cancel"); });
    d.addEventListener("close", () => { d.remove(); resolve(d.returnValue); });
    document.body.append(d); d.showModal();
    $$("[data-close]", d).forEach(a => a.addEventListener("click", () => d.close("cancel")));
    if (onOpen) onOpen(d);
    const first = $("input:not([type=hidden]):not([readonly]),textarea,select", d); if (first && !drawer) first.focus();
  });
}
export const confirmBox = ({ title, text, label = "تأكيد", danger = false }) => openDialog({
  title, body: html`<p>${text}</p>`,
  buttons: [{ label, kind: danger ? "solid-danger" : "primary", run: () => true }, { label: "إلغاء", kind: "ghost" }],
}).then(r => r === "ok");

export async function promptBox(o) {
  let out = null;
  const field = o.multiline
    ? html`<textarea class="textarea" id="pb" placeholder="${o.placeholder || ""}">${o.value || ""}</textarea>`
    : html`<input class="input ${o.mono ? "mono" : ""}" id="pb" value="${o.value || ""}" placeholder="${o.placeholder || ""}" autocomplete="off">`;
  const r = await openDialog({
    title: o.title,
    body: html`<div class="field" style="margin:0"><label for="pb">${o.label || ""}</label>${field}${o.hint ? html`<span class="hint">${o.hint}</span>` : ""}</div>`,
    buttons: [{
      label: o.confirm || "حفظ", kind: "primary",
      run: d => { const v = $("#pb", d).value.trim(); if (!v && !o.allowEmpty) throw new Error("هذا الحقل مطلوب."); out = v; },
    }, { label: "إلغاء", kind: "ghost" }],
  });
  return r === "ok" ? out : null;
}

/* Popover menu anchored to an element. items: {label, icon, run, bad, href, hr, head} */
export function menuAt(anchor, items) {
  $$(".menu").forEach(m => m.remove());
  const m = document.createElement("div");
  m.className = "menu"; m.setAttribute("role", "menu");
  setHTML(m, items.map((it, i) => it.hr ? html`<hr>` : it.head ? html`<div class="mhead">${it.head}</div>` : it.href
    ? html`<a href="${it.href}" role="menuitem" data-i="${i}" ${it.blank ? raw('target="_blank" rel="noopener noreferrer"') : ""}>${it.icon ? icon(it.icon, "sm") : ""}<span>${it.label}</span></a>`
    : html`<button type="button" role="menuitem" data-i="${i}" class="${it.bad ? "bad" : ""}">${it.icon ? icon(it.icon, "sm") : ""}<span>${it.label}</span></button>`));
  document.body.append(m);
  const r = anchor.getBoundingClientRect(), mw = m.offsetWidth, mh = m.offsetHeight;
  let left = r.left; if (left + mw > innerWidth - 8) left = innerWidth - mw - 8; if (left < 8) left = 8;
  let top = r.bottom + 6; if (top + mh > innerHeight - 8) top = Math.max(8, r.top - mh - 6);
  m.style.left = left + "px"; m.style.top = top + "px";
  const close = () => { m.remove(); document.removeEventListener("pointerdown", away, true); document.removeEventListener("keydown", key, true); };
  const away = e => { if (!m.contains(e.target)) close(); };
  const key = e => { if (e.key === "Escape") close(); };
  setTimeout(() => { document.addEventListener("pointerdown", away, true); document.addEventListener("keydown", key, true); }, 0);
  $$("[data-i]", m).forEach(b => b.addEventListener("click", () => { close(); const it = items[+b.dataset.i]; if (it.run) it.run(); }));
  const f = $("[data-i]", m); if (f) f.focus();
  return close;
}

/* ───────── Shared view pieces ───────── */
export const STATUS = { running: ["ok", "يعمل"], stopped: ["", "متوقف"], deploying: ["warn", "جارِ النشر"], error: ["bad", "خطأ"], suspended: ["bad", "موقوف"] };
export const pill = (st, label) => { const [c, t] = STATUS[st] || ["", st]; return html`<span class="pill ${c}"><i></i>${label || t}</span>`; };
export const ledCls = st => ({ running: "ok", deploying: "warn", error: "bad", suspended: "bad" }[st] || "");
export const KIND = { static: "موقع ثابت", python: "Python", node: "Node.js" };
export const siteUrl = s => s.url || (location.origin + s.path);
/* -1 = غير محدود (مدير المنصة) */
export const fmtLim = (v, f = n => nf.format(n)) => v < 0 ? "∞" : f(v);
export function meter(label, used, max, fmt = v => nf.format(v)) {
  const unl = max < 0;
  const pct = unl ? 0 : max > 0 ? Math.min(100, Math.round(used / max * 100)) : 100;
  const cls = pct >= 100 ? "full" : pct >= 80 ? "hot" : "";
  return html`<div class="meter"><div class="top"><span>${label}</span><b>${unl ? fmt(used) + " · غير محدود" : `${fmt(used)} من ${fmt(max)}`}</b></div><div class="track"><div class="fill ${cls}" style="width:${pct}%"></div></div></div>`;
}
export const bar = (used, max, thin = false) => { const pct = max < 0 ? 0 : max > 0 ? Math.min(100, Math.round(used / max * 100)) : 100; return html`<div class="track ${thin ? "thin" : ""}"><div class="fill ${pct >= 100 ? "full" : pct >= 80 ? "hot" : ""}" style="width:${pct}%"></div></div>`; };
export const loadingBlock = (t = "جارِ التحميل…") => html`<div class="loading"><span class="spin"></span>${t}</div>`;
export const errorBlock = e => html`<div class="empty"><span class="ico">${icon("alert", "lg")}</span><b>تعذّر تحميل الصفحة</b><p>${e && e.message || "حدث خطأ غير متوقع."}</p><button class="btn ghost sm" data-retry>إعادة المحاولة</button></div>`;
export const emptyBlock = (ico, t, s = "", action = "") => html`<div class="empty"><span class="ico">${icon(ico, "lg")}</span><b>${t}</b>${s ? html`<p>${s}</p>` : ""}${action}</div>`;
export const notice = (kind, title, text, actions = "") => html`<div class="notice ${kind}">${icon(kind === "ok" ? "checkCircle" : kind === "bad" ? "alert" : kind === "warn" ? "alert" : "info")}<div><b>${title}</b>${text ? html`<p>${text}</p>` : ""}${actions ? html`<div class="actions">${actions}</div>` : ""}</div></div>`;
export const debounce = (fn, ms = 250) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
export const tgLink = u => u.username ? `https://t.me/${u.username}` : `tg://user?id=${u.telegram_id}`;

/* File helpers shared by editor/file manager */
export const joinPath = (a, b) => a === "." || !a ? b : a + "/" + b;
export const parentPath = p => p.includes("/") ? p.slice(0, p.lastIndexOf("/")) : ".";
export const baseName = p => p.includes("/") ? p.slice(p.lastIndexOf("/") + 1) : p;
