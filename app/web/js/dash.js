/* Saw Host — dashboard, site list, create-site flow. */
import {
  $, $$, html, raw, setHTML, icon, state, api, post, go, toast, fail, nf, fmtMB, fmtD, ago, daysLeft, pill, ledCls, KIND, siteUrl, fmtLim,
  meter, bar, loadingBlock, errorBlock, emptyBlock, notice, copyText, confirmBox, menuAt, xhrUpload, debounce, later, plural, openExt,
} from "./core.js";

export function siteRow(s, { owner = false } = {}) {
  const url = siteUrl(s).replace(/^https?:\/\//, "");
  return html`<a class="unit" href="#/site/${s.id}" data-sid="${s.id}">
    <span class="led ${ledCls(s.status)}" aria-hidden="true"></span>
    <div class="u-main"><b>${s.name}</b><span class="ltr u-url">${url}</span></div>
    <span class="tag u-kind">${KIND[s.kind] || s.kind}</span>
    ${owner && s.owner ? html`<span class="u-owner ltr">@${s.owner}</span>` : ""}
    <span class="u-size ltr">${fmtMB(s.used_mb)}</span>
    ${pill(s.status)}
    <span class="u-go">${icon("chevL", "sm")}</span>
  </a>`;
}

/* ───────── Home ───────── */
export async function viewHome(ctx) {
  setHTML(ctx.el, loadingBlock());
  const [me, list] = await Promise.all([api("/me"), api("/sites")]);
  state.me = me;
  if (!ctx.alive()) return;
  const u = me.user, sub = me.subscription, lim = sub.limits, use = me.usage;
  const sites = list.sites;
  const run = sites.filter(s => s.status === "running").length;
  const dl = daysLeft(sub.expires_at);
  const trial = u.trial_active && u.trial_expires_at ? daysLeft(u.trial_expires_at) : null;
  let banner = "";
  if (me.maintenance && u.is_admin) banner = notice("warn", "وضع الصيانة مفعّل", "المستخدمون العاديون لا يستطيعون استخدام المنصة الآن.", html`<a class="btn sm ghost" href="#/admin/system">إدارة الإعدادات</a>`);
  else if (!me.has_access && !u.is_admin) banner = notice("bad", "لا يوجد اشتراك فعّال", "مواقعك متوقفة. اشترك أو جدّد لتشغيلها من جديد.", html`<a class="btn sm" href="#/plan">اختيار خطة</a><a class="btn sm ghost" href="#/invite">احصل على أيام مجانية</a>`);
  else if (dl !== null && dl <= 5 && dl >= 0 && !sub.boosted_by_trial) banner = notice("warn", `ينتهي اشتراكك ${dl === 0 ? "اليوم" : dl === 1 ? "غداً" : "بعد " + dl + " أيام"}`, "جدّد الآن حتى لا تتوقف مواقعك.", html`<a class="btn sm" href="#/plan">تجديد الاشتراك</a>`);
  const pend = u.is_admin ? me.pending_requests : 0;
  setHTML(ctx.el, html`
    <div class="page-head"><div><h1>أهلاً ${u.first_name || "بك"}</h1><p class="sub">${sites.length ? `لديك ${plural(sites.length, "موقع واحد", "موقعان", "مواقع", "موقعاً")}، منها ${nf.format(run)} يعمل الآن.` : "ابدأ بنشر موقعك الأول، يستغرق أقل من دقيقة."}</p></div>
      <div class="actions"><a class="btn" href="#/new">${icon("plus", "sm")} موقع جديد</a></div></div>
    ${banner}
    ${pend ? notice("info", `${nf.format(pend)} ${pend === 1 ? "طلب اشتراك ينتظر" : "طلبات اشتراك تنتظر"} مراجعتك`, "", html`<a class="btn sm" href="#/admin/requests">مراجعة الطلبات</a>`) : ""}
    <div class="stats">
      <div class="stat"><span class="k">المواقع</span><b class="v">${nf.format(use.sites)}<small>${lim.sites < 0 ? "· غير محدود" : "من " + nf.format(lim.sites)}</small></b>${bar(use.sites, lim.sites, true)}</div>
      <div class="stat"><span class="k">المساحة</span><b class="v ltr">${fmtMB(use.mb)}<small>${lim.mb < 0 ? "· غير محدود" : "من " + fmtMB(lim.mb)}</small></b>${bar(use.mb, lim.mb, true)}</div>
      <div class="stat"><span class="k">الدومينات</span><b class="v">${nf.format(use.custom_domains + use.platform_domains)}<small>${lim.custom_domains < 0 ? "· غير محدود" : "من " + nf.format(lim.custom_domains + lim.platform_domains)}</small></b>${bar(use.custom_domains + use.platform_domains, lim.custom_domains < 0 ? -1 : lim.custom_domains + lim.platform_domains, true)}</div>
      <div class="stat"><span class="k">الاشتراك</span><b class="v">${sub.effective_plan_name}</b><span class="s">${sub.boosted_by_trial ? `تجربة مجانية${trial !== null ? " · " + trial + " يوم" : ""}` : sub.expires_at ? (dl >= 0 ? `ينتهي ${fmtD(sub.expires_at)} · ${dl} يوم` : "منتهي") : "بدون انتهاء"}</span></div>
    </div>
    <section class="section"><div class="sec-head"><div><h2>مواقعي</h2></div>${sites.length > 6 ? html`<a href="#/sites" class="btn ghost sm">عرض الكل</a>` : ""}</div>
      ${sites.length ? html`<div class="rack">${sites.slice(0, 6).map(s => siteRow(s))}</div>` : html`<div class="card">${emptyBlock("rocket", "لا توجد مواقع بعد", "ارفع ملفات موقعك أو استورده من GitHub وسيعمل فوراً.", html`<a class="btn" href="#/new">إنشاء أول موقع</a>`)}</div>`}
    </section>
    <section class="section"><div class="grid-3 quick">
      <a class="card qa" href="#/new"><span class="ico">${icon("upload")}</span><div><b>انشر موقعاً</b><p>ZIP أو صفحة فارغة أو GitHub</p></div></a>
      <a class="card qa" href="#/invite"><span class="ico">${icon("gift")}</span><div><b>ادعُ أصدقاءك</b><p>يوم مجاني عن كل صديق</p></div></a>
      <a class="card qa" href="#/about"><span class="ico">${icon("info")}</span><div><b>عن المنصة</b><p>المزايا والحدود والأمان</p></div></a>
    </div></section>`);
}

/* ───────── Sites list ───────── */
export async function viewSites(ctx, q) {
  setHTML(ctx.el, loadingBlock());
  const { sites } = await api("/sites");
  if (!ctx.alive()) return;
  let filter = "all", term = "";
  const draw = () => {
    const items = sites.filter(s => (filter === "all" || s.status === filter) && (!term || (s.name + s.slug).toLowerCase().includes(term)));
    setHTML($("#rack"), items.length ? html`<div class="rack">${items.map(s => siteRow(s))}</div>` : html`<div class="card">${emptyBlock("search", "لا توجد نتائج", "جرّب تغيير البحث أو التصفية.")}</div>`);
  };
  setHTML(ctx.el, html`<div class="page-head"><div><h1>مواقعي</h1><p class="sub">${nf.format(sites.length)} ${sites.length === 1 ? "موقع" : "مواقع"}</p></div><div class="actions"><a class="btn" href="#/new">${icon("plus", "sm")} موقع جديد</a></div></div>
    ${sites.length ? html`<div class="toolbar"><label class="searchfield">${icon("search", "sm")}<input id="sq" placeholder="ابحث باسم الموقع" aria-label="بحث"></label>
      <div class="chips" id="flt">${[["all", "الكل"], ["running", "يعمل"], ["stopped", "متوقف"], ["error", "خطأ"]].map(([v, t]) => html`<button class="chip" data-v="${v}" aria-pressed="${v === "all"}">${t}</button>`)}</div></div>` : ""}
    <div id="rack"></div>`);
  if (!sites.length) { setHTML($("#rack"), html`<div class="card">${emptyBlock("rocket", "لا توجد مواقع بعد", "ارفع ملفات موقعك أو استورده من GitHub.", html`<a class="btn" href="#/new">إنشاء موقع</a>`)}</div>`); return; }
  draw();
  $("#sq").addEventListener("input", debounce(e => { term = e.target.value.trim().toLowerCase(); draw(); }, 120));
  $$("#flt .chip").forEach(b => b.onclick = () => { filter = b.dataset.v; $$("#flt .chip").forEach(x => x.setAttribute("aria-pressed", String(x === b))); draw(); });
}

/* ───────── New site ───────── */
export async function viewNew(ctx) {
  const me = state.me, lim = me.subscription.limits, use = me.usage;
  const full = use.sites >= lim.sites && !me.user.is_admin;
  let mode = "zip", file = null;
  setHTML(ctx.el, html`<a class="back" href="#/sites">${icon("arrowR", "sm")} مواقعي</a>
    <div class="page-head"><div><h1>موقع جديد</h1><p class="sub">اختر طريقة النشر. يمكنك تغيير الملفات وربط GitHub لاحقاً.</p></div></div>
    ${full ? notice("warn", "وصلت للحد الأقصى من المواقع", `خطتك الحالية تسمح بـ ${lim.sites} ${lim.sites === 1 ? "موقع" : "مواقع"}.`, html`<a class="btn sm" href="#/plan">ترقية الخطة</a>`) : ""}
    <div class="split">
      <div class="card"><div class="card-body">
        <div class="modes" role="tablist">
          ${[["zip", "upload", "رفع ZIP", "ملفات موقعك مضغوطة"], ["github", "github", "من GitHub", "استيراد من مستودع"], ["blank", "file", "صفحة فارغة", "ابدأ وحرّر في المتصفح"]]
      .map(([k, i, t, d]) => html`<button type="button" class="mode" data-m="${k}" role="tab" aria-selected="${k === mode}"><span class="ico">${icon(i, k === "github" ? "fill" : "")}</span><b>${t}</b><small>${d}</small></button>`)}
        </div>
        <div class="field" style="margin-top:22px"><label for="nm">اسم الموقع</label><input id="nm" class="input" maxlength="128" placeholder="مثال: متجري"><p class="hint">يظهر في لوحتك فقط. الرابط يُنشأ تلقائياً.</p></div>
        <div id="mbody"></div>
        <div class="form-err" id="nerr" hidden></div>
        <button class="btn lg block" id="go" type="button" ${full ? "disabled" : ""}>${icon("rocket", "sm")} انشر الآن</button>
      </div></div>
      <aside class="card"><div class="card-body"><h3 style="margin-bottom:12px">ماذا يحدث بعد الضغط؟</h3>
        <ol class="steps v" id="deploy-steps"><li><span class="mark">1</span><b>استلام الملفات</b></li><li><span class="mark">2</span><b>فحص المشروع</b><small>اكتشاف نوعه: ثابت أو Node.js</small></li><li><span class="mark">3</span><b>التشغيل</b><small>وإنشاء رابط عام للموقع</small></li></ol>
        <hr class="line"><p class="hint">للتطبيقات: ضع ملف <code>package.json</code> في جذر المشروع وسيُشغَّل <code>npm start</code> تلقائياً.</p></div></aside>
    </div>`);
  const body = $("#mbody");
  const drawMode = () => {
    $$(".mode").forEach(b => b.setAttribute("aria-selected", String(b.dataset.m === mode)));
    if (mode === "zip") {
      setHTML(body, html`<div class="field"><label>ملف المشروع</label><label class="dz" id="dz" tabindex="0"><input type="file" id="zf" accept=".zip,application/zip" hidden>${icon("upload", "lg")}<b id="dzt">اسحب ملف ZIP هنا أو اضغط للاختيار</b><span>حتى 150MB</span></label></div>`);
      const dz = $("#dz"), zf = $("#zf");
      const pick = f => { if (!f) return; if (!/\.zip$/i.test(f.name)) { toast("الملف يجب أن يكون ZIP.", "bad"); return; } file = f; setHTML($("#dzt"), html`${f.name} <small class="ltr">(${(f.size / 1048576).toFixed(1)} MB)</small>`); dz.classList.add("has"); if (!$("#nm").value) $("#nm").value = f.name.replace(/\.zip$/i, ""); };
      zf.onchange = () => pick(zf.files[0]);
      dz.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); zf.click(); } });
      ["dragenter", "dragover"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add("over"); }));
      ["dragleave", "drop"].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove("over"); }));
      dz.addEventListener("drop", e => pick(e.dataTransfer.files[0]));
    } else if (mode === "github") {
      setHTML(body, html`<div class="field"><label for="gr">المستودع</label><input id="gr" class="input ltr" placeholder="owner/repo أو رابط GitHub" autocapitalize="off" spellcheck="false"></div>
        <div class="field-row"><div class="field"><label for="gb">الفرع</label><input id="gb" class="input ltr" placeholder="main" autocapitalize="off"></div><div class="field"><label for="gs">مجلد فرعي <small>(اختياري)</small></label><input id="gs" class="input ltr" placeholder="dist"></div></div>
        <details class="more"><summary>مستودع خاص؟</summary><div class="field"><label for="gt">رمز الوصول (Personal Access Token)</label><input id="gt" class="input ltr" type="password" autocomplete="off" placeholder="github_pat_…"><p class="hint">يلزم صلاحية قراءة المحتوى (Contents: read). يُحفظ مشفّراً ولا يظهر مرة أخرى.</p></div></details>`);
    } else setHTML(body, html`<p class="hint" style="margin-bottom:18px">سيُنشأ موقع بصفحة <code>index.html</code> جاهزة للتعديل من المحرر.</p>`);
  };
  $$(".mode").forEach(b => b.onclick = () => { mode = b.dataset.m; drawMode(); });
  drawMode();
  const err = m => { const e = $("#nerr"); e.hidden = !m; e.textContent = m || ""; };
  $("#go").onclick = async () => {
    err("");
    const name = $("#nm").value.trim();
    if (mode === "zip" && !file) return err("اختر ملف ZIP أولاً.");
    if (mode === "github" && !$("#gr").value.trim()) return err("اكتب اسم المستودع.");
    if (!name && mode !== "zip") return err("اكتب اسماً للموقع.");
    const btn = $("#go"); btn.classList.add("is-busy"); btn.disabled = true;
    const lis = $$("#deploy-steps li");
    const step = n => lis.forEach((li, i) => { li.classList.toggle("done", i < n); li.classList.toggle("active", i === n); });
    step(0);
    try {
      let res;
      if (mode === "github") {
        step(0);
        res = await post("/sites/import-github", { name, repo: $("#gr").value.trim(), branch: $("#gb").value.trim() || "", subdir: $("#gs").value.trim(), token: ($("#gt") || {}).value || "" });
      } else {
        const fd = new FormData(); fd.append("name", name); if (mode === "zip") fd.append("file", file, file.name);
        res = await xhrUpload("/sites", fd, p => { if (p >= 1) step(1); });
      }
      step(3);
      toast("تم نشر الموقع", "ok");
      window.__sawRefreshMe && window.__sawRefreshMe();
      go("#/site/" + res.site.id);
    } catch (e) { step(-1); err(e.message || "تعذّر إنشاء الموقع."); fail(e); }
    finally { btn.classList.remove("is-busy"); btn.disabled = false; }
  };
}
