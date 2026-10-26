/* Saw Host — entry: router, app shell, command palette, session bootstrap. */
import {
  $, $$, html, raw, setHTML, icon, state, api, post, go, tg, isMini, toast, fail, initTheme, toggleTheme, curTheme,
  menuAt, openDialog, avatar, nf, daysLeft, bar, loadingBlock, errorBlock, debounce, openExt, confirmBox,
} from "./core.js";
import { initTelegram, syncBack } from "./tgapp.js";

initTheme();
initTelegram();   // ready/expand فوراً (كان بعد طلب الدخول فيبقى المستخدم بنصف شاشة لثوانٍ)
const app = $("#app");
let ctxSeq = 0, current = null;

/* ───────── Route table ───────── */
const mods = {
  pub: () => import("./public.js"),
  dash: () => import("./dash.js"),
  site: () => import("./site.js"),
  acct: () => import("./account.js"),
  admin: () => import("./admin.js"),
};
const ADMIN_SECTIONS = ["overview", "requests", "users", "sites", "plans", "domains", "messages", "notifs", "audit", "system"];
function resolve(path) {
  let m;
  if (path === "/welcome") return { mod: "pub", fn: "viewLanding", pub: true, layout: "none" };
  if (path === "/login") return { mod: "pub", fn: "viewLogin", pub: true, layout: "none" };
  if (path === "/about") return { mod: "pub", fn: "viewAbout", pub: true, layout: "auto" };
  if (path === "/" || path === "") return { mod: "dash", fn: "viewHome" };
  if (path === "/sites") return { mod: "dash", fn: "viewSites" };
  if (path === "/new") return { mod: "dash", fn: "viewNew" };
  if ((m = path.match(/^\/site\/([0-9a-f-]{36})(?:\/([a-z]+))?$/))) return { mod: "site", fn: "viewSite", args: [m[1], m[2] || "overview"], wide: m[2] === "files" };
  if (path === "/plan") return { mod: "acct", fn: "viewPlan" };
  if (path === "/invite") return { mod: "acct", fn: "viewInvite" };
  if (path === "/account") return { mod: "acct", fn: "viewAccount" };
  if ((m = path.match(/^\/admin(?:\/([a-z]+))?$/))) return { mod: "admin", fn: "viewAdmin", args: [ADMIN_SECTIONS.includes(m[1]) ? m[1] : "overview"], admin: true };
  return null;
}
function parseHash() {
  const h = location.hash.replace(/^#/, "") || "/";
  const [path, qs] = h.split("?");
  return { path: path || "/", q: new URLSearchParams(qs || "") };
}
const newCtx = (el, q) => {
  const c = { id: ++ctxSeq, el, q, timers: [], alive: () => current === c };
  return c;
};
function endCtx() { if (current) { current.timers.forEach(clearTimeout); current.onEnd && current.onEnd(); } current = null; }

/* ───────── Session ───────── */
async function loadMe(quiet = true) {
  try { state.me = await api("/me", { quiet401: quiet }); }
  catch (e) { if (e.status === 401) state.me = null; else throw e; }
  return state.me;
}
async function tryTelegramLogin() {
  if (!tg) return false;
  try {
    const r = await post("/login/webapp", { init_data: tg.initData });
    state.bearer = r.token || ""; if (r.csrf) state.csrf = r.csrf;
    try { tg.ready(); tg.expand(); } catch { /* ignore */ }
    return true;
  } catch { return false; }
}

/* ───────── Render ───────── */
let shellFor = null; // "user" | "admin" | null
async function render() {
  endCtx();
  document.body.classList.remove("nav-open");
  const { path, q } = parseHash();
  syncBack(path);
  // داخل تيليجرام: هوية initData الموقَّعة هي المرجع دائماً، فلا نعتمد على كوكي قديم قد يخص حساباً آخر
  if (tg && !state.triedTg) { state.triedTg = true; state.me = null; if (await tryTelegramLogin()) await loadMe(); }
  if (!state.me) await loadMe();
  const r = resolve(path);
  const me = state.me;

  if (!r) { go(me ? "#/" : "#/welcome"); return; }
  if (!me && !r.pub) { go("#/welcome"); return; }
  if (me && (path === "/welcome" || path === "/login")) { go("#/"); return; }
  if (r.admin && !me.user.is_admin) { go("#/"); return; }

  window.scrollTo(0, 0);
  const useShell = me && (r.layout !== "none");
  let host = app;
  if (useShell) {
    const kind = r.admin ? "admin" : "user";
    if (shellFor !== kind || !$(".app")) { buildShell(kind); }
    updateShell(path);
    host = $("#view");
    host.className = "view" + (r.wide ? " wide" : "");
  } else {
    shellFor = null;
    if (!(me && me.maintenance && !me.user.is_admin)) app.className = "";
  }

  const ctx = newCtx(host, q); current = ctx;
  if (useShell) setHTML(host, loadingBlock());
  try {
    if (me && me.maintenance && !me.user.is_admin) { setHTML(app, maintenanceScreen(me)); shellFor = null; return; }
    const m = await mods[r.mod]();
    if (!ctx.alive()) return;
    const args = r.args || [];
    await m[r.fn](ctx, ...args, q);
  } catch (e) {
    if (!ctx.alive()) return;
    if (e.status === 401) { state.me = null; go("#/login"); return; }
    setHTML(host, errorBlock(e));
    const rt = $("[data-retry]", host); if (rt) rt.onclick = () => render();
  }
}

const maintenanceScreen = me => html`<div class="boot" style="padding:24px;text-align:center"><img src="/panel/static/img/mark-96.png" width="72" height="72" alt="" style="border-radius:50%"><h1>المنصة تحت الصيانة</h1><p class="mute" style="max-width:34em">${me.maintenance_msg || "نعمل على تحسينات وسنعود قريباً."}</p><button class="btn ghost" id="mretry">إعادة المحاولة</button></div>`;

/* ───────── Shell ───────── */
const USER_NAV = [
  ["#/", "الرئيسية", "home", p => p === "/"],
  ["#/sites", "مواقعي", "globe", p => p === "/sites" || p.startsWith("/site/") || p === "/new"],
  ["#/plan", "اشتراكي", "card", p => p === "/plan"],
  ["#/invite", "الدعوات والتجربة", "gift", p => p === "/invite"],
  ["#/about", "عن المنصة", "info", p => p === "/about"],
];
const ADMIN_NAV = [
  ["overview", "نظرة عامة", "chart"], ["requests", "طلبات الاشتراك", "receipt"], ["users", "المستخدمون", "users"], ["sites", "المواقع", "globe"],
  ["plans", "الخطط والأسعار", "layers"], ["domains", "دومينات المنصة", "link"], ["messages", "الرسائل والنصوص", "horn"],
  ["notifs", "الإشعارات", "bell"], ["audit", "سجل العمليات", "history"], ["system", "النظام والإعدادات", "settings"],
];
const adminBadge = (k, me) => k === "requests" ? me.pending_requests : k === "notifs" ? me.unread_notifs : 0;

function buildShell(kind) {
  shellFor = kind;
  const me = state.me, u = me.user;
  setHTML(app, html`<div class="app">
    <aside class="side" id="side" aria-label="القائمة الرئيسية">
      <a class="brand" href="#/"><img src="/panel/static/img/mark-96.png" alt="" width="42" height="42"><div><b>Saw Host</b><small>${kind === "admin" ? "لوحة الإدارة" : "لوحة التحكم"}</small></div></a>
      ${kind === "user" && me.has_access ? html`<a class="cta" href="#/new">${icon("plus", "sm")} موقع جديد</a>` : ""}
      <nav class="nav" id="nav"></nav>
      <div class="side-foot"><div id="planchip"></div>
        <button class="me" id="meBtn" type="button" aria-haspopup="menu">${avatar(u.first_name || u.username, u.telegram_id, "")}<div class="who"><b>${u.first_name || u.username || "حسابي"}</b><span>${u.is_admin ? "مدير المنصة" : u.username ? "@" + u.username : "مستخدم"}</span></div>${icon("chevD", "sm")}</button></div>
    </aside>
    <div class="scrim" id="scrim"></div>
    <div class="main">
      <header class="topbar">
        <button class="iconbtn menu-btn" id="menuBtn" type="button" aria-label="القائمة">${icon("menu")}</button>
        <a class="tb-brand" href="#/"><img src="/panel/static/img/mark-64.png" alt="">Saw Host</a>
        <button class="searchbtn" id="searchBtn" type="button" aria-label="بحث">${icon("search", "sm")}<span>ابحث في المواقع والصفحات…</span><kbd>Ctrl K</kbd></button>
        <span class="sp"></span>
        <button class="iconbtn" data-theme-btn type="button" aria-label="تبديل المظهر"></button>
        ${u.is_admin ? html`<a class="iconbtn" id="bell" href="#/admin/notifs" aria-label="الإشعارات">${icon("bell")}<i class="dot" hidden></i></a>` : ""}
      </header>
      <main class="view" id="view" tabindex="-1"></main>
    </div>
    <nav class="tabbar" id="tabbar" aria-label="تنقل سريع"></nav>
  </div>`);
  $("#menuBtn").onclick = () => document.body.classList.toggle("nav-open");
  $("#scrim").onclick = () => document.body.classList.remove("nav-open");
  $("#searchBtn").onclick = openPalette;
  $$("[data-theme-btn]").forEach(b => b.onclick = toggleTheme);
  initTheme();
  $("#meBtn").onclick = e => userMenu(e.currentTarget);
}

function updateShell(path) {
  const me = state.me, u = me.user, admin = shellFor === "admin";
  const nav = $("#nav"); if (!nav) return;
  if (admin) {
    const sec = (path.split("/")[2]) || "overview";
    setHTML(nav, html`<a href="#/">${icon("arrowR", "sm")} العودة للوحتي</a><div class="grp">الإدارة</div>
      ${ADMIN_NAV.map(([k, l, i]) => { const n = adminBadge(k, me); return html`<a href="#/admin/${k}" ${k === sec ? raw('aria-current="page"') : ""}>${icon(i)}<span>${l}</span>${n ? html`<span class="badge warn">${n}</span>` : ""}</a>`; })}`);
    setHTML($("#planchip"), "");
  } else {
    const adminN = (me.pending_requests || 0) + (me.unread_notifs || 0);
    setHTML(nav, html`${USER_NAV.map(([h, l, i, m]) => html`<a href="${h}" ${m(path) ? raw('aria-current="page"') : ""}>${icon(i)}<span>${l}</span></a>`)}
      <a href="https://t.me/${me.admin_username}" target="_blank" rel="noopener noreferrer">${icon("headset")}<span>الدعم الفني</span></a>
      ${u.is_admin ? html`<div class="grp">الإدارة</div><a href="#/admin">${icon("shield")}<span>لوحة الإدارة</span>${adminN ? html`<span class="badge warn">${adminN}</span>` : ""}</a>` : ""}`);
    const sub = me.subscription;
    if (sub) {
      const dl = daysLeft(sub.expires_at);
      const left = u.trial_active ? "تجربة مجانية" : sub.expired ? "منتهٍ" : dl === null ? "دائم" : dl + " يوماً";
      setHTML($("#planchip"), html`<a class="planchip" href="#/plan"><div class="t"><b>${sub.plan.icon} ${sub.effective_plan_name}</b><span>${left}</span></div>${bar(me.usage.sites, sub.limits.sites)}</a>`);
    }
  }
  // Mobile tab bar
  const tb = $("#tabbar");
  if (admin) {
    const T = [["overview", "نظرة", "chart"], ["requests", "الطلبات", "receipt"], ["users", "المستخدمون", "users"], ["sites", "المواقع", "globe"]];
    const sec = (path.split("/")[2]) || "overview";
    setHTML(tb, html`${T.map(([k, l, i]) => { const n = adminBadge(k, me); return html`<a href="#/admin/${k}" ${k === sec ? raw('aria-current="page"') : ""}>${icon(i)}<span>${l}</span>${n ? html`<span class="badge warn">${n}</span>` : ""}</a>`; })}<button type="button" id="moreBtn">${icon("menu")}<span>المزيد</span></button>`);
  } else {
    const t = (h, l, i, m) => html`<a href="${h}" ${m ? raw('aria-current="page"') : ""}>${icon(i)}<span>${l}</span></a>`;
    setHTML(tb, html`${t("#/", "الرئيسية", "home", path === "/")}${t("#/sites", "مواقعي", "globe", path === "/sites" || path.startsWith("/site/"))}
      ${me.has_access ? html`<a class="fab" href="#/new" aria-label="موقع جديد"><i>${icon("plus")}</i></a>` : html`<a class="fab" href="#/plan"><i>${icon("card")}</i></a>`}
      ${t("#/plan", "اشتراكي", "card", path === "/plan")}<button type="button" id="moreBtn">${icon("menu")}<span>المزيد</span></button>`);
  }
  const more = $("#moreBtn"); if (more) more.onclick = () => document.body.classList.add("nav-open");
  const bell = $("#bell"); if (bell) $(".dot", bell).hidden = !(me.unread_notifs > 0);
  $$("[data-theme-btn]").forEach(b => setHTML(b, icon(curTheme() === "dark" ? "sun" : "moon")));
}

async function refreshMe() { try { const me = await api("/me"); state.me = me; updateShell(parseHash().path); } catch { /* ignore */ } }
window.__sawRefreshMe = refreshMe;

function userMenu(anchor) {
  const u = state.me.user;
  menuAt(anchor, [
    { head: u.username ? "@" + u.username : (u.first_name || "حسابي") },
    { label: "الحساب والأمان", icon: "user", run: () => go("#/account") },
    { label: curTheme() === "dark" ? "المظهر الفاتح" : "المظهر الداكن", icon: curTheme() === "dark" ? "sun" : "moon", run: toggleTheme },
    { label: "الدعم الفني", icon: "headset", href: `https://t.me/${state.me.admin_username}`, blank: true },
    { hr: true },
    { label: "تسجيل الخروج", icon: "logout", bad: true, run: doLogout },
  ]);
}
async function doLogout() {
  try { const r = await post("/logout"); if (r && r.csrf) state.csrf = r.csrf; } catch { /* ignore */ }
  state.me = null; state.bearer = ""; shellFor = null; go("#/welcome");
}

/* ───────── Command palette ───────── */
async function openPalette() {
  const me = state.me; if (!me) return;
  let sites = [];
  try { sites = (await api("/sites")).sites; } catch { /* ignore */ }
  const pages = [["الرئيسية", "#/", "home"], ["مواقعي", "#/sites", "globe"], ["موقع جديد", "#/new", "plus"], ["اشتراكي والخطط", "#/plan", "card"], ["الدعوات والتجربة", "#/invite", "gift"], ["عن المنصة", "#/about", "info"], ["الحساب والأمان", "#/account", "user"]];
  if (me.user.is_admin) ADMIN_NAV.forEach(([k, l, i]) => pages.push(["الإدارة: " + l, "#/admin/" + k, i]));
  const items = [...pages.map(([label, href, ico]) => ({ g: "الصفحات", label, hint: "", href, ico })),
    ...sites.flatMap(s => [{ g: "مواقعك", label: s.name, hint: s.slug, href: "#/site/" + s.id, ico: "globe" }, { g: "مواقعك", label: "محرر: " + s.name, hint: s.slug, href: `#/site/${s.id}/files`, ico: "code" }])];
  let sel = 0, list = items;
  const dlgP = openDialog({
    title: "", noHead: true, size: "",
    body: html`<div class="pal"><div class="input-icon">${icon("search", "sm")}<input class="input" id="palq" placeholder="ابحث عن صفحة أو موقع…" autocomplete="off"></div><div id="pall" class="pal-list" role="listbox"></div></div>`,
    onOpen: d => {
      const q = $("#palq", d), box = $("#pall", d);
      const draw = () => {
        const t = q.value.trim().toLowerCase();
        list = items.filter(i => !t || (i.label + " " + i.hint).toLowerCase().includes(t)).slice(0, 14);
        sel = Math.min(sel, Math.max(0, list.length - 1));
        let g = "";
        setHTML(box, list.length ? list.map((it, i) => { const head = it.g !== g ? html`<div class="pal-g">${it.g}</div>` : ""; g = it.g; return html`${head}<button type="button" role="option" class="pal-i" data-i="${i}" aria-selected="${i === sel}">${icon(it.ico, "sm")}<span>${it.label}</span><small class="ltr">${it.hint}</small></button>`; }) : html`<div class="empty" style="padding:24px">لا نتائج.</div>`);
        $$(".pal-i", box).forEach(b => b.onclick = () => { d.close("ok"); go(list[+b.dataset.i].href); });
      };
      q.addEventListener("input", () => { sel = 0; draw(); });
      q.addEventListener("keydown", e => {
        if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(list.length - 1, sel + 1); draw(); }
        else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(0, sel - 1); draw(); }
        else if (e.key === "Enter" && list[sel]) { e.preventDefault(); d.close("ok"); go(list[sel].href); }
      });
      draw();
    },
  });
  return dlgP;
}
document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k" && state.me) { e.preventDefault(); if (!$("dialog[open]")) openPalette(); }
});

/* ───────── Boot ───────── */
window.addEventListener("hashchange", render);
document.addEventListener("click", e => { if (e.target.closest("#nav a") || e.target.closest("#tabbar a")) document.body.classList.remove("nav-open"); });
document.addEventListener("click", e => { if (e.target.id === "mretry") location.reload(); });
render();
