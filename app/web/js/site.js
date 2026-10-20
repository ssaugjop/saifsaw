/* Saw Host — single-site view: overview, files+editor, GitHub, domains, backups, logs, settings. */
import {
  $, $$, html, raw, setHTML, icon, state, api, post, put, go, toast, fail, nf, fmtDT, fmtD, fmtSize, fmtMB, ago, pill, ledCls, KIND, siteUrl,
  loadingBlock, errorBlock, emptyBlock, notice, copyText, confirmBox, promptBox, openDialog, menuAt, xhrUpload, downloadVia, later, debounce,
  joinPath, parentPath, baseName, openExt, tg,
} from "./core.js";
import { Editor, langOf, LANG_LABEL } from "./editor.js";

const TABS = [["overview", "نظرة عامة", "layers"], ["files", "الملفات والمحرر", "code"], ["github", "GitHub", "github"], ["domains", "الدومينات", "globe"], ["backups", "النسخ الاحتياطية", "archive"], ["logs", "السجلات", "terminal"], ["settings", "الإعدادات", "settings"]];
const IMG = /\.(png|jpe?g|gif|webp|svg|ico|avif|bmp)$/i;

export async function viewSite(ctx, sid, tab, q) {
  tab = TABS.some(t => t[0] === tab) ? tab : "overview";
  setHTML(ctx.el, loadingBlock());
  let data;
  try { data = await api(`/sites/${sid}`); } catch (e) { if (e.status === 404 || e.status === 403) { setHTML(ctx.el, html`<div class="card">${emptyBlock("alert", "الموقع غير موجود", "قد يكون حُذف أو لا تملك صلاحية الوصول إليه.", html`<a class="btn" href="#/sites">العودة لمواقعي</a>`)}</div>`); return; } throw e; }
  if (!ctx.alive()) return;
  const S = { site: data.site, deploy: data.deploy, sid };
  const head = () => {
    const s = S.site;
    return html`<a class="back" href="#/sites">${icon("arrowR", "sm")} مواقعي</a>
      <div class="site-head"><span class="led lg ${ledCls(s.status)}"></span><div class="sh-main"><h1>${s.name}</h1><a class="ltr sh-url" href="${siteUrl(s)}" target="_blank" rel="noopener noreferrer">${siteUrl(s).replace(/^https?:\/\//, "")} ${icon("external", "sm")}</a></div>
        <div class="sh-act">${pill(s.status)}${s.status === "running" ? html`<button class="btn ghost sm" data-act="restart" type="button">${icon("refresh", "sm")} إعادة تشغيل</button><button class="btn ghost sm" data-act="stop" type="button">${icon("stop", "sm")} إيقاف</button>`
        : s.status === "deploying" ? "" : html`<button class="btn sm" data-act="start" type="button" ${s.status === "suspended" ? "disabled" : ""}>${icon("play", "sm")} تشغيل</button>`}</div></div>
      ${s.status === "error" && s.last_error ? notice("bad", "حدث خطأ في الموقع", s.last_error, html`<a class="btn sm ghost" href="#/site/${sid}/logs">عرض السجلات</a>`) : ""}
      ${s.status === "suspended" ? notice("bad", "الموقع موقوف من الإدارة", "تواصل مع الدعم لمعرفة السبب.") : ""}
      <nav class="tabs scroll" role="tablist">${TABS.map(([k, t, i]) => html`<a href="#/site/${sid}/${k}" role="tab" aria-selected="${k === tab}">${icon(i, "sm")}<span>${t}</span></a>`)}</nav>`;
  };
  setHTML(ctx.el, html`<div id="sh"></div><div id="tabbody" class="tabbody"></div>`);
  const drawHead = () => {
    setHTML($("#sh"), head());
    $$("[data-act]", $("#sh")).forEach(b => b.onclick = async () => {
      const a = b.dataset.act; b.classList.add("is-busy");
      try { const r = await post(`/sites/${sid}/${a}`); S.site = r.site; toast(a === "stop" ? "تم إيقاف الموقع" : "تم تشغيل الموقع", "ok"); drawHead(); window.__sawRefreshMe && window.__sawRefreshMe(); if (tab === "overview") TAB.overview(ctx, S, $("#tabbody"), drawHead); } catch (e) { fail(e); drawHead(); }
    });
  };
  S.redraw = drawHead; S.set = s => { S.site = s; drawHead(); };
  drawHead();
  if (tab === "files") $(".view").classList.add("wide"); else $(".view").classList.remove("wide");
  const box = $("#tabbody");
  try { await TAB[tab](ctx, S, box, drawHead, q); } catch (e) { if (ctx.alive()) { setHTML(box, errorBlock(e)); const r = $("[data-retry]", box); if (r) r.onclick = () => go(location.hash); } }
  if (S.site.status === "deploying") (function poll() { later(ctx, async () => { try { const r = await api(`/sites/${sid}/status`); S.set(r.site); if (r.site.status === "deploying") poll(); else if (tab === "overview") TAB.overview(ctx, S, $("#tabbody"), drawHead); } catch { /* ignore */ } }, 2000); })();
}

const TAB = {};

/* ───────── Overview ───────── */
TAB.overview = async (ctx, S, box) => {
  const s = S.site, url = siteUrl(s);
  let doms = [], gh = null;
  try { [{ domains: doms }, { link: gh }] = await Promise.all([api(`/sites/${S.sid}/domains`), api(`/sites/${S.sid}/github`)]); } catch { /* optional */ }
  if (!ctx.alive()) return;
  setHTML(box, html`<div class="split">
    <div class="stack">
      <div class="card"><div class="card-head"><h3>رابط الموقع</h3></div><div class="card-body">
        <div class="copyline ltr"><code>${url}</code><button class="iconbtn sm" data-copy="${url}" aria-label="نسخ">${icon("copy", "sm")}</button><a class="iconbtn sm" href="${url}" target="_blank" rel="noopener noreferrer" aria-label="فتح">${icon("external", "sm")}</a></div>
        ${doms.length ? html`<div class="doms">${doms.map(d => html`<div class="row"><span class="led ${d.verify_status === "verified" ? "ok" : "warn"}"></span><span class="ltr grow">${d.hostname}</span>${d.is_primary ? html`<span class="tag">أساسي</span>` : ""}</div>`)}</div>` : ""}
      </div></div>
      <div class="card"><div class="card-head"><h3>الوصول السريع</h3></div><div class="card-body quick-grid">
        <a class="qa" href="#/site/${S.sid}/files"><span class="ico">${icon("code")}</span><div><b>محرر الملفات</b><p>عدّل الكود وشاهد المعاينة</p></div></a>
        <a class="qa" href="#/site/${S.sid}/github"><span class="ico">${icon("github")}</span><div><b>${gh ? "إدارة GitHub" : "ربط GitHub"}</b><p>${gh ? gh.repo : "نشر تلقائي مع كل push"}</p></div></a>
        <a class="qa" href="#/site/${S.sid}/backups"><span class="ico">${icon("archive")}</span><div><b>نسخة احتياطية</b><p>احفظ حالة الموقع الآن</p></div></a>
        <a class="qa" href="#/site/${S.sid}/domains"><span class="ico">${icon("globe")}</span><div><b>الدومينات</b><p>${doms.length ? nf.format(doms.length) + " مرتبط" : "اربط دومينك"}</p></div></a>
      </div></div></div>
    <aside class="card"><div class="card-head"><h3>التفاصيل</h3></div><dl class="kv" style="padding:4px 22px 14px">
      <dt>الحالة</dt><dd>${pill(s.status)}</dd><dt>النوع</dt><dd>${KIND[s.kind] || s.kind}</dd><dt>المساحة</dt><dd class="ltr">${fmtMB(s.used_mb)}</dd>
      <dt>المعرّف</dt><dd class="ltr">${s.slug}</dd><dt>أُنشئ</dt><dd>${fmtDT(s.created_at)}</dd>
      ${S.deploy && S.deploy.message ? html`<dt>آخر عملية</dt><dd>${S.deploy.message}</dd>` : ""}</dl></aside></div>`);
};

/* ───────── Files + editor ───────── */
TAB.files = async (ctx, S, box) => {
  const sid = S.sid;
  const T = { cwd: ".", items: [], tabs: [], active: -1, preview: false, dev: "full", search: null, homepage: null };
  const dirty = () => T.tabs.some(t => t.cur !== t.orig);
  const guard = e => { if (dirty()) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", guard);
  let ed = null;
  ctx.onEnd = () => { window.removeEventListener("beforeunload", guard); if (ed) ed.destroy(); ed = null; $(".view") && $(".view").classList.remove("wide"); };

  setHTML(box, html`<div class="ide">
    <aside class="ide-side"><div class="ide-tools">
      <button class="iconbtn" data-t="file" title="ملف جديد" aria-label="ملف جديد">${icon("filePlus")}</button><button class="iconbtn" data-t="dir" title="مجلد جديد" aria-label="مجلد جديد">${icon("folderPlus")}</button>
      <button class="iconbtn" data-t="up" title="رفع ملفات" aria-label="رفع ملفات">${icon("upload")}</button><button class="iconbtn" data-t="refresh" title="تحديث" aria-label="تحديث">${icon("refresh")}</button>
      <button class="iconbtn" data-t="dl" title="تنزيل الموقع كاملاً" aria-label="تنزيل">${icon("download")}</button><input type="file" id="upin" multiple hidden></div>
      <label class="searchfield sm">${icon("search", "sm")}<input id="fsearch" placeholder="بحث في الملفات والمحتوى" aria-label="بحث"></label>
      <nav class="crumbs ltr" id="crumbs" aria-label="المسار"></nav>
      <div class="ide-list" id="flist" tabindex="-1"></div><div class="ide-drop" hidden>${icon("upload", "lg")}<b>أفلت الملفات للرفع</b></div></aside>
    <section class="ide-main"><div class="ide-tabs" id="otabs" role="tablist"></div><div class="ide-work" id="work"></div></section></div>`);

  const flist = $("#flist"), work = $("#work");
  const cur = () => T.tabs[T.active];

  async function load(path = T.cwd) {
    setHTML(flist, loadingBlock(""));
    const r = await api(`/sites/${sid}/files?path=${encodeURIComponent(path)}`);
    if (!ctx.alive()) return;
    T.cwd = r.path; T.items = r.items; T.homepage = r.homepage; drawList();
  }
  function drawList() {
    const parts = T.cwd === "." ? [] : T.cwd.split("/");
    setHTML($("#crumbs"), html`<a data-p=".">${icon("home", "sm")}</a>${parts.map((p, i) => html`<span>/</span><a data-p="${parts.slice(0, i + 1).join("/")}">${p}</a>`)}`);
    $$("#crumbs a").forEach(a => a.onclick = () => load(a.dataset.p));
    if (T.search) return;
    const rows = T.items;
    setHTML(flist, rows.length ? html`${T.cwd !== "." ? html`<button class="fi" data-up type="button">${icon("folderOpen", "sm")}<span class="ltr">..</span></button>` : ""}${rows.map(it => html`<div class="fi ${cur() && cur().path === it.rel ? "on" : ""}" data-rel="${it.rel}" data-dir="${it.is_dir ? 1 : 0}" tabindex="0" role="button">
        ${icon(it.is_dir ? "folder" : IMG.test(it.name) ? "image" : "file", "sm")}<span class="nm ltr">${it.name}</span>${T.homepage === it.rel ? html`<span class="tag ok sm">رئيسية</span>` : ""}${it.is_dir ? "" : html`<small class="ltr">${fmtSize(it.size)}</small>`}
        <button class="iconbtn sm more" data-more aria-label="خيارات">${icon("more", "sm")}</button></div>`)}`
      : html`<div class="mute-box">${T.cwd === "." ? "الموقع فارغ. أنشئ ملفاً أو ارفع ملفات." : "المجلد فارغ."}</div>`);
  }
  flist.addEventListener("click", e => {
    if (e.target.closest("[data-up]")) { load(parentPath(T.cwd)); return; }
    const row = e.target.closest(".fi[data-rel]"); if (!row) return;
    const it = (T.search ? T.search.map(r => ({ rel: r.path, name: baseName(r.path), is_dir: false })) : T.items).find(x => x.rel === row.dataset.rel) || { rel: row.dataset.rel, name: baseName(row.dataset.rel), is_dir: row.dataset.dir === "1" };
    if (e.target.closest("[data-more]")) { rowMenu(e.target.closest("[data-more]"), it); return; }
    if (it.is_dir) load(it.rel); else openFile(it.rel, T.search && row.dataset.line ? +row.dataset.line : 0);
  });
  flist.addEventListener("keydown", e => { if (e.key === "Enter" && e.target.classList.contains("fi")) e.target.click(); });
  flist.addEventListener("contextmenu", e => { const row = e.target.closest(".fi[data-rel]"); if (!row) return; e.preventDefault(); rowMenu(row, { rel: row.dataset.rel, name: baseName(row.dataset.rel), is_dir: row.dataset.dir === "1" }); });

  function rowMenu(anchor, it) {
    menuAt(anchor, [
      ...(it.is_dir ? [] : [{ label: "فتح", icon: "edit", run: () => openFile(it.rel) }]),
      { label: "إعادة تسمية", icon: "edit", run: () => rename(it) },
      ...(!it.is_dir && /\.html?$/i.test(it.name) ? [{ label: "تعيين كصفحة رئيسية", icon: "home", run: async () => { try { await post(`/sites/${sid}/set-index`, { path: it.rel }); toast("تم تعيين الصفحة الرئيسية", "ok"); load(); } catch (e) { fail(e); } } }] : []),
      { label: "تنزيل", icon: "download", run: () => downloadVia(`/sites/${sid}/download-link`, { path: it.rel }) },
      { hr: true }, { label: "حذف", icon: "trash", bad: true, run: () => del(it) }]);
  }
  async function rename(it) {
    const n = await promptBox({ title: "إعادة تسمية", label: "الاسم الجديد", value: it.name, mono: true }); if (!n || n === it.name) return;
    try {
      await post(`/sites/${sid}/rename-file`, { path: it.rel, new_name: n });
      const np = joinPath(parentPath(it.rel), n);
      T.tabs.forEach(t => { if (t.path === it.rel) { t.path = np; t.name = n; } else if (t.path.startsWith(it.rel + "/")) { t.path = np + t.path.slice(it.rel.length); t.name = baseName(t.path); } });
      await load(); drawTabs(); toast("تمت إعادة التسمية", "ok");
    } catch (e) { fail(e); }
  }
  async function del(it) {
    if (!await confirmBox({ title: it.is_dir ? "حذف المجلد" : "حذف الملف", text: `سيُحذف «${it.name}» نهائياً${it.is_dir ? " مع كل ما بداخله" : ""}.`, label: "حذف", danger: true })) return;
    try {
      const r = await post(`/sites/${sid}/delete-file`, { path: it.rel }); S.set(r.site);
      T.tabs = T.tabs.filter(t => !(t.path === it.rel || t.path.startsWith(it.rel + "/"))); if (T.active >= T.tabs.length) T.active = T.tabs.length - 1;
      await load(); drawTabs(); showActive(); toast("تم الحذف", "ok");
    } catch (e) { fail(e); }
  }

  /* toolbar */
  $$(".ide-tools [data-t]").forEach(b => b.onclick = async () => {
    const t = b.dataset.t;
    if (t === "refresh") load();
    else if (t === "up") $("#upin").click();
    else if (t === "dl") downloadVia(`/sites/${sid}/download-link`, { path: "." });
    else {
      const n = await promptBox({ title: t === "file" ? "ملف جديد" : "مجلد جديد", label: "الاسم", mono: true, placeholder: t === "file" ? "about.html" : "assets", hint: "يمكنك كتابة مسار مثل css/main.css" }); if (!n) return;
      const p = joinPath(T.cwd, n.replace(/^\/+/, ""));
      try {
        if (t === "dir") await post(`/sites/${sid}/mkdir`, { path: p });
        else { const r = await put(`/sites/${sid}/file`, { path: p, content: "" }); S.set(r.site); }
        await load(); if (t === "file") openFile(p);
      } catch (e) { fail(e); }
    }
  });
  async function upload(files, extract = false) {
    if (!files.length) return;
    const fd = new FormData(); fd.append("path", T.cwd); if (extract) fd.append("extract", "1"); [...files].forEach(f => fd.append("file", f, f.name));
    const t = toast(`جارِ رفع ${files.length} ${files.length === 1 ? "ملف" : "ملفات"}…`, "", 60000);
    try {
      const r = await xhrUpload(`/sites/${sid}/upload`, fd); S.set(r.site);
      toast(r.extracted ? `تم فك الضغط (${nf.format(r.extracted)} ملف)` : `تم رفع ${r.saved.length} ${r.saved.length === 1 ? "ملف" : "ملفات"}`, "ok");
      if (r.skipped && r.skipped.length) toast(`تعذّر رفع: ${r.skipped.map(x => x.name).join("، ")}`, "warn");
      await load();
    } catch (e) { fail(e); } finally { if (t && t.remove) t.remove(); }
  }
  $("#upin").onchange = async e => {
    const fl = [...e.target.files]; e.target.value = "";
    const zips = fl.filter(f => /\.zip$/i.test(f.name));
    if (zips.length) {
      const r = await openDialog({ title: "ملف ZIP", body: html`<p>هل تريد فك ضغط الأرشيف داخل المجلد الحالي أم رفعه كملف عادي؟</p>`, buttons: [{ label: "فك الضغط", kind: "primary", run: () => true }, { label: "رفع كملف", kind: "ghost", run: d => { d.returnValue = "plain"; return true; } }] });
      if (r === "ok") { await upload(zips, true); await upload(fl.filter(f => !zips.includes(f))); return; }
      if (r === "plain") { await upload(fl); return; }
      return;
    }
    upload(fl);
  };
  const side = $(".ide-side"), dropEl = $(".ide-drop");
  let dc = 0;
  side.addEventListener("dragenter", e => { if (e.dataTransfer.types.includes("Files")) { dc++; dropEl.hidden = false; } });
  side.addEventListener("dragleave", () => { if (--dc <= 0) { dc = 0; dropEl.hidden = true; } });
  side.addEventListener("dragover", e => e.preventDefault());
  side.addEventListener("drop", e => { e.preventDefault(); dc = 0; dropEl.hidden = true; upload(e.dataTransfer.files); });

  /* search */
  $("#fsearch").addEventListener("input", debounce(async e => {
    const v = e.target.value.trim();
    if (v.length < 2) { T.search = null; drawList(); return; }
    try {
      const r = await api(`/sites/${sid}/search?q=${encodeURIComponent(v)}`); T.search = r.results;
      setHTML(flist, r.results.length ? html`${r.results.map(x => html`<div class="fi res" data-rel="${x.path}" data-line="${x.line || 0}" tabindex="0" role="button">${icon("file", "sm")}<span class="nm ltr">${x.path}${x.line ? html`<em>:${x.line}</em>` : ""}</span>${x.text ? html`<code class="ltr">${x.text}</code>` : ""}</div>`)}${r.truncated ? html`<div class="mute-box">أول النتائج فقط. دقّق البحث.</div>` : ""}` : html`<div class="mute-box">لا نتائج.</div>`);
    } catch (er) { fail(er); }
  }, 300));

  /* tabs & editor */
  function drawTabs() {
    setHTML($("#otabs"), T.tabs.map((t, i) => html`<div class="otab ${i === T.active ? "on" : ""}" role="tab" aria-selected="${i === T.active}" data-i="${i}" tabindex="0"><span class="ltr">${t.name}</span>${t.cur !== t.orig ? html`<i class="dot" title="تغييرات غير محفوظة"></i>` : ""}<button class="x" data-x aria-label="إغلاق ${t.name}" type="button">${icon("x", "sm")}</button></div>`));
  }
  $("#otabs").addEventListener("click", async e => {
    const tab = e.target.closest(".otab"); if (!tab) return; const i = +tab.dataset.i;
    if (e.target.closest("[data-x]")) { await closeTab(i); return; }
    activate(i);
  });
  $("#otabs").addEventListener("auxclick", e => { const tab = e.target.closest(".otab"); if (tab && e.button === 1) closeTab(+tab.dataset.i); });
  async function closeTab(i) {
    const t = T.tabs[i];
    if (t.cur !== t.orig && !await confirmBox({ title: "تغييرات غير محفوظة", text: `«${t.name}» فيه تغييرات لم تُحفظ. هل تريد إغلاقه وتجاهلها؟`, label: "إغلاق وتجاهل", danger: true })) return;
    T.tabs.splice(i, 1); if (T.active >= i) T.active = Math.max(-1, T.active - 1); if (T.active < 0 && T.tabs.length) T.active = 0;
    drawTabs(); showActive();
  }
  function activate(i) { if (ed && cur()) { cur().view = ed.state(); } T.active = i; drawTabs(); showActive(); drawList(); }
  async function openFile(path, line = 0) {
    const ex = T.tabs.findIndex(t => t.path === path);
    if (ex >= 0) { activate(ex); if (line && ed) ed.gotoLine(line); return; }
    const name = baseName(path);
    if (IMG.test(name)) { if (ed && cur()) cur().view = ed.state(); T.tabs.push({ path, name, img: true, orig: "", cur: "" }); T.active = T.tabs.length - 1; drawTabs(); showActive(); drawList(); return; }
    try {
      const r = await api(`/sites/${sid}/file?path=${encodeURIComponent(path)}`);
      if (ed && cur()) cur().view = ed.state();
      T.tabs.push({ path, name, orig: r.content, cur: r.content, size: r.size }); T.active = T.tabs.length - 1; drawTabs(); showActive(line); drawList();
    } catch (e) {
      if (e.status === 415 || e.status === 413) { toast(e.message, "warn"); } else fail(e);
    }
  }
  async function save() {
    const t = cur(); if (!t || t.img || t.cur === t.orig) return;
    const b = $("#savebtn"); if (b) b.classList.add("is-busy");
    try { const r = await put(`/sites/${sid}/file`, { path: t.path, content: t.cur }); t.orig = t.cur; S.set(r.site); drawTabs(); updateBar(); toast("تم الحفظ", "ok", 1400); reloadPreview(); }
    catch (e) { fail(e); } finally { if (b) b.classList.remove("is-busy"); }
  }
  function updateBar() { const t = cur(), b = $("#savebtn"); if (b && t) b.disabled = t.cur === t.orig; }
  function reloadPreview() { const f = $("#pv"); if (f && T.preview) f.src = pvUrl(); }
  function pvUrl() { const t = cur(); const p = t && /\.html?$/i.test(t.name) ? t.path : (T.homepage || "index.html"); return siteUrl(S.site) + "/" + p.split("/").map(encodeURIComponent).join("/") + "?_=" + Date.now(); }

  function showActive(line = 0) {
    const t = cur();
    if (ed) { ed.destroy(); ed = null; }
    if (!t) { setHTML(work, html`<div class="ide-empty">${icon("code", "lg")}<b>اختر ملفاً لتعديله</b><p>افتح ملفاً من القائمة، أو أنشئ ملفاً جديداً.</p><div class="kbds"><span><kbd>Ctrl</kbd>+<kbd>S</kbd> حفظ</span><span><kbd>Ctrl</kbd>+<kbd>F</kbd> بحث</span><span><kbd>Ctrl</kbd>+<kbd>/</kbd> تعليق</span><span><kbd>Ctrl</kbd>+<kbd>D</kbd> تكرار سطر</span></div></div>`); return; }
    if (t.img) { setHTML(work, html`<div class="img-view"><img src="${siteUrl(S.site)}/${t.path.split("/").map(encodeURIComponent).join("/")}" alt="${t.name}"><p class="ltr">${t.name}</p></div>`); return; }
    const isHtml = /\.html?$/i.test(t.name);
    setHTML(work, html`<div class="ide-bar"><span class="ltr path">${t.path}</span><span class="sp"></span>
      ${isHtml || T.preview ? html`<button class="btn ghost sm" id="pvbtn" aria-pressed="${T.preview}" type="button">${icon("eye", "sm")} معاينة</button>` : ""}
      <button class="btn sm" id="savebtn" type="button" ${t.cur === t.orig ? "disabled" : ""}>${icon("save", "sm")} حفظ</button></div>
      <div class="ide-split ${T.preview ? "pv" : ""}"><div class="ide-ed" id="edhost"></div>
        <div class="ide-pv" ${T.preview ? "" : "hidden"}><div class="pv-bar"><div class="seg sm">${[["full", "monitor"], ["tab", "tablet"], ["mob", "phone"]].map(([k, i]) => html`<button type="button" data-d="${k}" aria-pressed="${T.dev === k}" aria-label="${k}">${icon(i, "sm")}</button>`)}</div><button class="iconbtn sm" id="pvr" aria-label="تحديث المعاينة">${icon("refresh", "sm")}</button><a class="iconbtn sm" id="pvx" href="${siteUrl(S.site)}" target="_blank" rel="noopener noreferrer" aria-label="فتح في تبويب">${icon("external", "sm")}</a></div>
          <div class="pv-stage ${T.dev}"><iframe id="pv" title="معاينة" sandbox="allow-scripts allow-forms allow-popups" ${T.preview ? raw(`src="${pvUrl()}"`) : ""}></iframe></div></div></div>`);
    ed = new Editor($("#edhost"), {
      onChange: () => { t.cur = ed.value; const dot = $(`.otab[data-i="${T.active}"]`); if (dot) { const has = $("i.dot", dot); if (t.cur !== t.orig && !has) dot.insertBefore(Object.assign(document.createElement("i"), { className: "dot" }), $(".x", dot)); else if (t.cur === t.orig && has) has.remove(); } updateBar(); },
      onSave: save,
    });
    ed.load(t.cur, t.name);
    if (t.view) ed.restore(t.view);
    if (line) ed.gotoLine(line);
    if (matchMedia("(pointer:fine)").matches) ed.focus();
    $("#savebtn").onclick = save;
    const pvb = $("#pvbtn"); if (pvb) pvb.onclick = () => { T.preview = !T.preview; showActive(); };
    if (T.preview) {
      $("#pvr").onclick = () => { $("#pv").src = pvUrl(); };
      $$("[data-d]").forEach(b => b.onclick = () => { T.dev = b.dataset.d; $(".pv-stage").className = "pv-stage " + T.dev; $$("[data-d]").forEach(x => x.setAttribute("aria-pressed", String(x === b))); });
    }
  }

  await load();
  showActive();
  if (S.site.status === "stopped" && S.site.kind !== "static") { /* nothing */ }
  const first = new URLSearchParams(location.hash.split("?")[1] || "").get("open");
  if (first) openFile(first);
};

/* ───────── GitHub ───────── */
TAB.github = async (ctx, S, box) => {
  const sid = S.sid;
  const { link, history } = await api(`/sites/${sid}/github`);
  if (!ctx.alive()) return;
  const hist = history || [];
  const statusTag = h => h.ok ? html`<span class="tag ok">نجح</span>` : html`<span class="tag bad">فشل</span>`;
  if (!link) {
    let info = null;
    setHTML(box, html`<div class="split"><div class="card"><div class="card-head"><h3>ربط مستودع GitHub</h3></div><div class="card-body">
      <p class="hint" style="margin-bottom:16px">بعد الربط تُسحب ملفات المستودع إلى موقعك، ويُحدَّث الموقع تلقائياً عند كل push.</p>
      <div class="field"><label for="gr">المستودع</label><div class="input-group"><input id="gr" class="input ltr" placeholder="owner/repo أو رابط GitHub" autocapitalize="off" spellcheck="false"><button class="btn ghost" id="insp" type="button">فحص</button></div></div>
      <div id="ginfo"></div>
      <div class="field-row"><div class="field"><label for="gb">الفرع</label><select id="gb" class="select ltr"><option value="">الفرع الافتراضي</option></select></div><div class="field"><label for="gs">مجلد فرعي <small>(اختياري)</small></label><input id="gs" class="input ltr" placeholder="dist"></div></div>
      <details class="more"><summary>مستودع خاص؟</summary><div class="field"><label for="gt">رمز الوصول (Personal Access Token)</label><input id="gt" class="input ltr" type="password" autocomplete="off" placeholder="github_pat_…"><p class="hint">صلاحية Contents: read تكفي، وWebhooks: write إن أردت إنشاء الـ webhook تلقائياً. يُحفظ مشفّراً ولا يظهر بعد الحفظ.</p></div></details>
      <label class="check"><input type="checkbox" id="gc" checked><span>حذف الملفات التي أُزيلت من المستودع عند كل مزامنة</span></label>
      <div class="form-err" id="gerr" hidden></div><button class="btn lg" id="glink" type="button">${icon("link", "sm")} ربط ومزامنة الآن</button></div></div>
      <aside class="card"><div class="card-body"><h3 style="margin-bottom:10px">كيف يعمل؟</h3><ol class="steps v"><li class="active"><span class="mark">1</span><b>تربط المستودع والفرع</b></li><li class="active"><span class="mark">2</span><b>تُسحب الملفات وتُنشر فوراً</b></li><li class="active"><span class="mark">3</span><b>مع كل push يتحدّث الموقع</b><small>عبر webhook موقّع</small></li></ol></div></aside></div>`);
    const err = m => { const e = $("#gerr"); e.hidden = !m; e.textContent = m || ""; };
    $("#insp").onclick = async () => {
      err(""); const b = $("#insp"); b.classList.add("is-busy");
      try {
        info = await post(`/sites/${sid}/github/inspect`, { repo: $("#gr").value, token: $("#gt").value });
        const sel = $("#gb"); setHTML(sel, info.branches.map(x => html`<option value="${x}" ${x === info.default_branch ? "selected" : ""}>${x}</option>`));
        setHTML($("#ginfo"), html`<div class="repo-card">${icon("github")}<div><b class="ltr">${info.full_name}</b><p>${info.description || "بدون وصف"}</p><span class="tag">${info.private ? "خاص" : "عام"}</span> <span class="mute">آخر تحديث ${ago(info.pushed_at)}</span></div></div>`);
      } catch (e) { err(e.message); } finally { b.classList.remove("is-busy"); }
    };
    $("#gr").addEventListener("keydown", e => { if (e.key === "Enter") $("#insp").click(); });
    $("#glink").onclick = async () => {
      err(""); const b = $("#glink"); b.classList.add("is-busy");
      try {
        await post(`/sites/${sid}/github`, { repo: $("#gr").value, branch: $("#gb").value, subdir: $("#gs").value, token: $("#gt").value, clean: $("#gc").checked });
        const r = await post(`/sites/${sid}/github/sync`); S.set(r.site); toast(r.message || "تمت المزامنة", "ok"); go(`#/site/${sid}/github`);
      } catch (e) { err(e.message); } finally { b.classList.remove("is-busy"); }
    };
    return;
  }
  const hook = location.origin + link.webhook_path;
  setHTML(box, html`<div class="split"><div class="stack">
    <div class="card"><div class="card-body gh-head">
      <span class="gh-mk">${icon("github")}</span><div class="grow"><a class="ltr gh-repo" href="${link.repo_url}" target="_blank" rel="noopener noreferrer">${link.repo}</a>
        <div class="gh-meta"><span class="tag ltr">${icon("branch", "sm")} ${link.branch}</span>${link.subdir ? html`<span class="tag ltr">/${link.subdir}</span>` : ""}${link.private ? html`<span class="tag">خاص</span>` : ""}${link.last_sha ? html`<span class="tag ltr">${link.last_sha.slice(0, 7)}</span>` : ""}</div>
        <p class="mute">${link.last_synced_at ? `آخر مزامنة ${ago(link.last_synced_at)}${link.last_message ? " · " + link.last_message : ""}` : "لم تتم مزامنة بعد"}</p></div>
      <button class="btn" id="gsync" type="button">${icon("refresh", "sm")} مزامنة الآن</button></div>
      ${link.last_sync_error ? html`<div class="card-body" style="padding-top:0">${notice("bad", "فشلت آخر مزامنة", link.last_sync_error)}</div>` : ""}</div>
    <div class="card"><div class="card-head"><h3>النشر التلقائي</h3></div><div class="card-body">
      <div class="switch-row"><div><b>تحديث الموقع مع كل push</b><p>عند وصول webhook موقّع من GitHub.</p></div><label class="switch"><input type="checkbox" id="gauto" ${link.auto_sync ? "checked" : ""}><span></span></label></div>
      <hr class="line">
      ${link.webhook_id ? html`<div class="row"><span class="led ok"></span><b>الـ webhook مُنشأ في GitHub</b><span class="sp"></span><button class="btn ghost sm" id="whdel" type="button">إزالته</button></div>`
      : html`<div class="row wrap"><div class="grow"><b>إنشاء الـ webhook تلقائياً</b><p class="hint" style="margin:2px 0 0">${link.has_token ? "يستخدم رمز الوصول المحفوظ." : "يتطلب رمز وصول بصلاحية Webhooks: write."}</p></div><button class="btn ghost" id="whmk" type="button">${icon("zap", "sm")} إنشاؤه</button></div>`}
      <details class="more" style="margin-top:14px"><summary>إعداد الـ webhook يدوياً</summary>
        <div class="field"><label>Payload URL</label><div class="copyline ltr"><code>${hook}</code><button class="iconbtn sm" data-copy="${hook}" aria-label="نسخ">${icon("copy", "sm")}</button></div></div>
        <div class="field"><label>Content type</label><div class="copyline ltr"><code>application/json</code></div></div>
        <div class="field"><label>Secret</label><div class="copyline ltr"><code>${link.webhook_secret}</code><button class="iconbtn sm" data-copy="${link.webhook_secret}" aria-label="نسخ">${icon("copy", "sm")}</button></div></div>
        <p class="hint">من إعدادات المستودع ← Webhooks ← Add webhook، واختر حدث push فقط.</p></details></div></div>
    <div class="card"><div class="card-head"><h3>سجل النشر</h3></div>${hist.length ? html`<div class="hist">${hist.map(h => html`<div class="h-row"><span class="led ${h.ok ? "ok" : "bad"}"></span><div class="grow"><b>${h.message || (h.ok ? "مزامنة ناجحة" : "فشلت المزامنة")}</b><p class="mute"><span class="ltr">${h.sha ? h.sha.slice(0, 7) : "—"}</span> · ${h.trigger === "webhook" ? "webhook" : "يدوي"} · ${fmtDT(h.created_at)}${h.files ? ` · ${nf.format(h.files)} ملف` : ""}</p>${!h.ok && h.error ? html`<p class="err-t">${h.error}</p>` : ""}</div>${statusTag(h)}</div>`)}</div>` : html`<div class="card-body">${emptyBlock("clock", "لا يوجد سجل بعد", "ستظهر هنا كل عملية نشر.")}</div>`}</div></div>
    <aside class="stack"><div class="card"><div class="card-head"><h3>الإعدادات</h3></div><div class="card-body">
      <dl class="kv flat"><dt>المستودع</dt><dd class="ltr">${link.repo}</dd><dt>الفرع</dt><dd class="ltr">${link.branch}</dd><dt>المجلد الفرعي</dt><dd class="ltr">${link.subdir || "الجذر"}</dd><dt>حذف الملفات المزالة</dt><dd>${link.clean ? "نعم" : "لا"}</dd></dl>
      <button class="btn ghost block" id="gedit" type="button" style="margin-top:14px">تعديل الإعدادات</button><button class="btn ghost danger block" id="gun" type="button" style="margin-top:8px">${icon("unlink", "sm")} فك الربط</button></div></div></aside></div>`);
  $("#gsync").onclick = async e => {
    const b = e.currentTarget; b.classList.add("is-busy");
    try { const r = await post(`/sites/${sid}/github/sync`); S.set(r.site); toast(r.message || "تمت المزامنة", "ok"); TAB.github(ctx, S, box); } catch (er) { fail(er); TAB.github(ctx, S, box); }
  };
  $("#gauto").onchange = async e => { try { await post(`/sites/${sid}/github/toggle`); toast(e.target.checked ? "تم تفعيل النشر التلقائي" : "تم إيقاف النشر التلقائي", "ok"); } catch (er) { e.target.checked = !e.target.checked; fail(er); } };
  const mk = $("#whmk"); if (mk) mk.onclick = async () => { mk.classList.add("is-busy"); try { await post(`/sites/${sid}/github/webhook`, { action: "create" }); toast("تم إنشاء الـ webhook", "ok"); TAB.github(ctx, S, box); } catch (er) { fail(er); mk.classList.remove("is-busy"); } };
  const wd = $("#whdel"); if (wd) wd.onclick = async () => { try { await post(`/sites/${sid}/github/webhook`, { action: "delete" }); toast("تمت إزالة الـ webhook", "ok"); TAB.github(ctx, S, box); } catch (er) { fail(er); } };
  $("#gun").onclick = async () => { if (!await confirmBox({ title: "فك الربط", text: "ستبقى ملفات الموقع كما هي، لكن لن يُحدَّث الموقع من GitHub بعد الآن.", label: "فك الربط", danger: true })) return; try { await post(`/sites/${sid}/github/unlink`); toast("تم فك الربط", "ok"); TAB.github(ctx, S, box); } catch (er) { fail(er); } };
  $("#gedit").onclick = () => openDialog({
    title: "إعدادات GitHub", body: html`<div class="field"><label for="eb">الفرع</label><input id="eb" class="input ltr" value="${link.branch}"></div><div class="field"><label for="es">مجلد فرعي</label><input id="es" class="input ltr" value="${link.subdir || ""}" placeholder="الجذر"></div>
      <div class="field"><label for="et">رمز وصول جديد <small>(اتركه فارغاً للإبقاء على الحالي)</small></label><input id="et" class="input ltr" type="password" autocomplete="off"></div><label class="check"><input type="checkbox" id="ec" ${link.clean ? "checked" : ""}><span>حذف الملفات المزالة من المستودع</span></label>`,
    buttons: [{ label: "حفظ", kind: "primary", run: async d => { await post(`/sites/${sid}/github`, { repo: link.repo, branch: $("#eb", d).value, subdir: $("#es", d).value, token: $("#et", d).value, clean: $("#ec", d).checked, keep_token: !$("#et", d).value }); toast("تم الحفظ", "ok"); TAB.github(ctx, S, box); } }, { label: "إلغاء", kind: "ghost" }],
  });
};

/* ───────── Domains ───────── */
TAB.domains = async (ctx, S, box) => {
  const sid = S.sid; const d = await api(`/sites/${sid}/domains`); if (!ctx.alive()) return;
  const full = (n, l, u) => u >= l;
  setHTML(box, html`<div class="split"><div class="card"><div class="card-head"><h3>دومينات الموقع</h3></div>
    ${d.domains.length ? html`<div class="hist">${d.domains.map(x => html`<div class="h-row"><span class="led ${x.verify_status === "verified" ? "ok" : "warn"}"></span><div class="grow"><b class="ltr">${x.hostname}</b><p class="mute">${x.type === "custom" ? "دومين مخصص" : "دومين المنصة"} · ${x.verify_status === "verified" ? "مفعّل" : "بانتظار التحقق"}${x.is_primary ? " · أساسي" : ""}</p>
        ${x.type === "custom" && x.verify_status !== "verified" ? html`<div class="dns"><p>أضف سجل <b>TXT</b> عند مزوّد الدومين:</p><div class="copyline ltr"><code>_sawhost.${x.hostname} → ${x.verify_token}</code><button class="iconbtn sm" data-copy="${x.verify_token}" aria-label="نسخ">${icon("copy", "sm")}</button></div><p class="hint">وسجل <b>CNAME</b> أو <b>A</b> يشير إلى سيرفر المنصة. قد يستغرق انتشار DNS بضع دقائق.</p></div>` : ""}</div>
        <div class="row">${x.type === "custom" && x.verify_status !== "verified" ? html`<button class="btn sm" data-v="${x.id}" type="button">تحقق</button>` : ""}${!x.is_primary && x.verify_status === "verified" ? html`<button class="btn ghost sm" data-p="${x.id}" type="button">تعيين كأساسي</button>` : ""}<button class="iconbtn sm" data-d="${x.id}" aria-label="حذف">${icon("trash", "sm")}</button></div></div>`)}</div>`
      : html`<div class="card-body">${emptyBlock("globe", "لا توجد دومينات", "موقعك يعمل على الرابط الافتراضي. أضف دومينك أو اختر من دومينات المنصة.")}</div>`}</div>
    <aside class="stack">
      <div class="card"><div class="card-head"><h3>دومين مخصص</h3><span class="tag">${d.usage.custom} / ${d.limits.custom < 0 ? "∞" : d.limits.custom}</span></div><div class="card-body">
        ${!d.custom_enabled ? notice("info", "الميزة غير مفعّلة حالياً", "تفعّلها الإدارة عند الحاجة.") : d.limits.custom === 0 ? notice("warn", "خطتك لا تشمل دومينات مخصصة", "", html`<a class="btn sm" href="#/plan">ترقية الخطة</a>`) : html`<div class="field"><label for="cd">اسم الدومين</label><div class="input-group"><input id="cd" class="input ltr" placeholder="example.com" autocapitalize="off"><button class="btn" id="cda" type="button">إضافة</button></div></div>`}</div></div>
      <div class="card"><div class="card-head"><h3>دومين من المنصة</h3><span class="tag">${d.usage.platform} / ${d.limits.platform < 0 ? "∞" : d.limits.platform}</span></div><div class="card-body">
        ${d.limits.platform === 0 ? notice("warn", "خطتك لا تشمل دومينات المنصة", "", html`<a class="btn sm" href="#/plan">ترقية الخطة</a>`) : d.available_platform.length ? html`<div class="field"><select id="pd" class="select ltr">${d.available_platform.map(p => html`<option value="${p.id}">${p.hostname}${p.label ? " — " + p.label : ""}</option>`)}</select></div><button class="btn block" id="pda" type="button">ربط الدومين</button>` : html`<p class="hint">لا توجد دومينات متاحة حالياً.</p>`}</div></div></aside></div>`);
  const refresh = () => TAB.domains(ctx, S, box);
  const wrap = (sel, fn) => $$(sel, box).forEach(b => b.onclick = async () => { b.classList.add("is-busy"); try { await fn(b); } catch (e) { fail(e); b.classList.remove("is-busy"); } });
  wrap("[data-v]", async b => { const r = await post(`/domains/${b.dataset.v}/verify`); toast(r.domain.verify_status === "verified" ? "تم التحقق من الدومين" : "لم يُعثر على سجل TXT بعد", r.domain.verify_status === "verified" ? "ok" : "warn"); refresh(); });
  wrap("[data-p]", async b => { await post(`/domains/${b.dataset.p}/primary`); refresh(); });
  $$("[data-d]", box).forEach(b => b.onclick = async () => { if (!await confirmBox({ title: "حذف الدومين", text: "سيتوقف الوصول للموقع عبر هذا الدومين.", label: "حذف", danger: true })) return; try { await post(`/domains/${b.dataset.d}/delete`); refresh(); } catch (e) { fail(e); } });
  const ca = $("#cda"); if (ca) ca.onclick = async () => { ca.classList.add("is-busy"); try { await post(`/sites/${sid}/domains/custom`, { hostname: $("#cd").value }); toast("أُضيف الدومين. أكمل إعداد DNS ثم اضغط تحقق.", "ok"); refresh(); } catch (e) { fail(e); ca.classList.remove("is-busy"); } };
  const pa = $("#pda"); if (pa) pa.onclick = async () => { pa.classList.add("is-busy"); try { await post(`/sites/${sid}/domains/platform`, { platform_domain_id: $("#pd").value }); toast("تم ربط الدومين", "ok"); refresh(); } catch (e) { fail(e); pa.classList.remove("is-busy"); } };
};

/* ───────── Backups ───────── */
TAB.backups = async (ctx, S, box) => {
  const sid = S.sid; const r = await api(`/sites/${sid}/backups`); if (!ctx.alive()) return;
  const full = r.limit >= 0 && r.backups.length >= r.limit;
  setHTML(box, html`<div class="card"><div class="card-head"><div><h3>النسخ الاحتياطية</h3><p class="sub">${r.backups.length} من ${r.limit < 0 ? "∞" : r.limit}</p></div><button class="btn" id="bk" type="button" ${r.limit === 0 ? "disabled" : ""}>${icon("plus", "sm")} نسخة جديدة</button></div>
    ${r.limit === 0 ? html`<div class="card-body">${notice("warn", "خطتك لا تشمل نسخاً احتياطية", "", html`<a class="btn sm" href="#/plan">ترقية الخطة</a>`)}</div>` : r.backups.length ? html`<div class="hist">${r.backups.map(b => html`<div class="h-row"><span class="ico-sm">${icon("archive", "sm")}</span><div class="grow"><b>${fmtDT(b.created_at)}</b><p class="mute"><span class="ltr">${nf.format(b.size_mb)} MB</span> · ${b.trigger === "auto" ? "تلقائية" : "يدوية"}</p></div>
      <div class="row"><button class="btn ghost sm" data-dl="${b.id}" type="button">${icon("download", "sm")} تنزيل</button><button class="btn ghost sm" data-rs="${b.id}" type="button">استعادة</button><button class="iconbtn sm" data-rm="${b.id}" aria-label="حذف">${icon("trash", "sm")}</button></div></div>`)}</div>`
      : html`<div class="card-body">${emptyBlock("archive", "لا توجد نسخ بعد", "أنشئ نسخة قبل أي تعديل كبير لتستطيع الرجوع إليها.")}</div>`}</div>`);
  const refresh = () => TAB.backups(ctx, S, box);
  $("#bk").onclick = async e => { const b = e.currentTarget; b.classList.add("is-busy"); try { await post(`/sites/${sid}/backups`); toast("تم إنشاء النسخة", "ok"); refresh(); } catch (er) { fail(er); b.classList.remove("is-busy"); } };
  $$("[data-dl]", box).forEach(b => b.onclick = () => downloadVia(`/backups/${b.dataset.dl}/download-link`));
  $$("[data-rs]", box).forEach(b => b.onclick = async () => { if (!await confirmBox({ title: "استعادة النسخة", text: "ستُستبدل ملفات الموقع الحالية بمحتوى هذه النسخة.", label: "استعادة", danger: true })) return; b.classList.add("is-busy"); try { const x = await post(`/backups/${b.dataset.rs}/restore`); S.set(x.site); toast("تمت الاستعادة", "ok"); refresh(); } catch (e) { fail(e); b.classList.remove("is-busy"); } });
  $$("[data-rm]", box).forEach(b => b.onclick = async () => { if (!await confirmBox({ title: "حذف النسخة", text: "لا يمكن التراجع عن الحذف.", label: "حذف", danger: true })) return; try { await post(`/backups/${b.dataset.rm}/delete`); refresh(); } catch (e) { fail(e); } });
};

/* ───────── Logs ───────── */
TAB.logs = async (ctx, S, box) => {
  const sid = S.sid; let follow = true, paused = false;
  setHTML(box, html`<div class="card"><div class="card-head"><div><h3>سجلات التشغيل</h3><p class="sub" id="lsub"></p></div><div class="row"><label class="check sm"><input type="checkbox" id="lfollow" checked><span>تمرير تلقائي</span></label><button class="btn ghost sm" id="lref" type="button">${icon("refresh", "sm")} تحديث</button></div></div><pre class="logbox ltr" id="logbox" tabindex="0"></pre></div>`);
  const draw = async () => {
    try {
      const r = await api(`/sites/${sid}/logs`); if (!ctx.alive()) return;
      const lb = $("#logbox");
      if (!r.supported) { setHTML($("#lsub"), "المواقع الثابتة لا تنتج سجلات تشغيل."); lb.textContent = "هذا موقع ثابت، لا توجد عملية تشغيل لعرض مخرجاتها.\nلرؤية أخطاء المتصفح استخدم أدوات المطوّر (F12)."; return; }
      setHTML($("#lsub"), r.running ? "العملية تعمل" : "العملية متوقفة");
      lb.textContent = r.lines.length ? r.lines.join("\n") : "لا توجد مخرجات بعد.";
      if (follow) lb.scrollTop = lb.scrollHeight;
    } catch (e) { fail(e); }
  };
  await draw();
  $("#lfollow").onchange = e => { follow = e.target.checked; };
  $("#lref").onclick = draw;
  (function tick() { later(ctx, async () => { if (!document.hidden) await draw(); tick(); }, 3000); })();
};

/* ───────── Settings ───────── */
TAB.settings = async (ctx, S, box) => {
  const s = S.site, sid = S.sid;
  setHTML(box, html`<div class="stack narrow">
    <div class="card"><div class="card-head"><h3>اسم الموقع</h3></div><div class="card-body"><div class="input-group"><input id="rn" class="input" maxlength="128" value="${s.name}"><button class="btn" id="rnb" type="button">حفظ</button></div></div></div>
    <div class="card"><div class="card-head"><h3>تنزيل الموقع</h3></div><div class="card-body"><p class="hint" style="margin-bottom:12px">ملف ZIP بكل محتويات الموقع.</p><button class="btn ghost" id="dlall" type="button">${icon("download", "sm")} تنزيل كل الملفات</button></div></div>
    <div class="card danger-zone"><div class="card-head"><h3>منطقة الخطر</h3></div><div class="card-body"><div class="row wrap"><div class="grow"><b>حذف الموقع</b><p class="hint" style="margin:2px 0 0">يُحذف الموقع وملفاته ونسخه والدومينات المرتبطة نهائياً.</p></div><button class="btn solid-danger" id="rm" type="button">حذف الموقع</button></div></div></div></div>`);
  $("#rnb").onclick = async e => { const b = e.currentTarget; b.classList.add("is-busy"); try { const r = await post(`/sites/${sid}/rename`, { name: $("#rn").value }); S.set(r.site); toast("تم الحفظ", "ok"); } catch (er) { fail(er); } finally { b.classList.remove("is-busy"); } };
  $("#dlall").onclick = () => downloadVia(`/sites/${sid}/download-link`, { path: "." });
  $("#rm").onclick = async () => {
    const ok = await openDialog({ title: "حذف الموقع", body: html`<p>اكتب اسم الموقع <b>${s.name}</b> للتأكيد. لا يمكن التراجع.</p><div class="field" style="margin:12px 0 0"><input id="cf" class="input" autocomplete="off"></div>`, buttons: [{ label: "حذف نهائي", kind: "solid-danger", run: d => { if ($("#cf", d).value.trim() !== s.name) throw new Error("الاسم غير مطابق."); return true; } }, { label: "إلغاء", kind: "ghost" }] });
    if (ok !== "ok") return;
    try { await post(`/sites/${sid}/delete`); toast("تم حذف الموقع", "ok"); window.__sawRefreshMe && window.__sawRefreshMe(); go("#/sites"); } catch (e) { fail(e); }
  };
};
