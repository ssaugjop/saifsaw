/* Saw Host — admin console. */
import {
  $, $$, html, raw, esc, setHTML, icon, state, api, post, go, toast, fail, nf, fmtDT, fmtD, fmtMB, fmtIQD, ago, daysLeft, pill, ledCls, KIND, siteUrl,
  meter, bar, loadingBlock, errorBlock, emptyBlock, notice, copyText, confirmBox, promptBox, openDialog, menuAt, debounce, later, avatar, tgLink, openExt, plural,
} from "./core.js";
import { barChart, donut } from "./charts.js";

const refreshBadges = () => window.__sawRefreshMe && window.__sawRefreshMe();
const SEC = {};
export async function viewAdmin(ctx, section, q) {
  section = SEC[section] ? section : "overview";
  setHTML(ctx.el, loadingBlock());
  try { await SEC[section](ctx, q); }
  catch (e) { if (!ctx.alive()) return; setHTML(ctx.el, errorBlock(e)); const r = $("[data-retry]", ctx.el); if (r) r.onclick = () => go(location.hash); }
}
const head = (title, sub = "", actions = "") => html`<div class="page-head"><div><h1>${title}</h1>${sub ? html`<p class="sub">${sub}</p>` : ""}</div>${actions ? html`<div class="actions">${actions}</div>` : ""}</div>`;

/* Telegram-flavoured HTML → safe HTML (for previews). Only a few tags are honoured. */
export function tgHtml(src) {
  let s = esc(src);
  s = s.replace(/&lt;(\/?)(b|strong|i|em|u|s|code|pre)&gt;/gi, (m, c, t) => `<${c}${t.toLowerCase()}>`);
  s = s.replace(/&lt;a href=&quot;(https?:\/\/[^"&]*|tg:\/\/[^"&]*)&quot;&gt;/gi, (m, u) => `<a href="${u}" target="_blank" rel="noopener noreferrer">`).replace(/&lt;\/a&gt;/gi, "</a>");
  return raw(s.replace(/\n/g, "<br>"));
}

/* ───────── Overview ───────── */
SEC.overview = async ctx => {
  const [d, rq] = await Promise.all([api("/admin/stats"), api("/admin/requests?status=pending")]);
  if (!ctx.alive()) return;
  const st = d.stats, disk = d.disk, ser = d.series || { days: [], users: [], sites: [], requests: [] };
  const stat = (k, v, s = "", href = "") => html`<${href ? "a" : "div"} class="stat" ${href ? raw(`href="${href}"`) : ""}><span class="k">${k}</span><b class="v">${v}</b>${s ? html`<span class="s">${s}</span>` : ""}</${href ? "a" : "div"}>`;
  const diskPct = disk ? Math.round(disk.used_mb / disk.total_mb * 100) : 0;
  setHTML(ctx.el, html`${head("نظرة عامة", "حالة المنصة الآن.", html`<a class="btn ghost" href="#/admin/system">${icon("pulse", "sm")} صحة النظام</a>`)}
    <div class="stats five">
      ${stat("المستخدمون", nf.format(st.total_users), st.banned_users ? `${nf.format(st.banned_users)} محظور` : "", "#/admin/users")}
      ${stat("المواقع", nf.format(st.total_sites), `${nf.format(st.running_sites)} تعمل`, "#/admin/sites")}
      ${stat("طلبات الاشتراك", nf.format(st.pending_requests), st.pending_requests ? "تنتظر المراجعة" : "لا شيء معلّق", "#/admin/requests")}
      ${stat("الدومينات", nf.format(st.total_domains), `${nf.format(st.verified_domains)} مفعّل`)}
      ${stat("المساحة المستخدمة", fmtMB(st.total_mb), disk ? `من ${fmtMB(disk.total_mb)} (${diskPct}%)` : "")}
    </div>
    <div class="grid-2 charts" style="margin-top:18px">
      <div class="card"><div class="card-head"><div><h3>مستخدمون جدد</h3><p class="sub">آخر 14 يوماً · <b>${nf.format(ser.users.reduce((a, b) => a + b, 0))}</b></p></div></div><div class="card-body">${ser.days.length ? barChart({ labels: ser.days, values: ser.users, title: "مستخدمون جدد" }) : html`<p class="mute">لا بيانات.</p>`}</div></div>
      <div class="card"><div class="card-head"><div><h3>مواقع جديدة</h3><p class="sub">آخر 14 يوماً · <b>${nf.format(ser.sites.reduce((a, b) => a + b, 0))}</b></p></div></div><div class="card-body">${ser.days.length ? barChart({ labels: ser.days, values: ser.sites, title: "مواقع جديدة" }) : html`<p class="mute">لا بيانات.</p>`}</div></div>
    </div>
    <div class="split" style="margin-top:18px">
      <div class="card"><div class="card-head"><h3>طلبات تنتظر قرارك</h3><a class="btn ghost sm" href="#/admin/requests">الكل</a></div>
        ${rq.requests.length ? html`<div class="hist">${rq.requests.slice(0, 5).map(r => html`<a class="h-row" href="#/admin/requests">${avatar(r.user.name || r.user.username, r.user.telegram_id)}<div class="grow"><b>${r.user.name || "—"} <span class="mute ltr">${r.user.username ? "@" + r.user.username : ""}</span></b><p class="mute">${r.icon} ${r.plan} · ${r.months} ${r.months === 1 ? "شهر" : "أشهر"} · <span class="ltr">${fmtIQD(r.total)}</span></p></div><span class="mute sm">${ago(r.created_at)}</span></a>`)}</div>` : html`<div class="card-body">${emptyBlock("checkCircle", "لا توجد طلبات معلّقة", "كل الطلبات تمت مراجعتها.")}</div>`}</div>
      <div class="stack"><div class="card"><div class="card-head"><h3>القرص</h3></div><div class="card-body">${disk ? html`<div class="donut-row">${donut([{ label: "مستخدم", value: disk.used_mb, cls: "a" }, { label: "حر", value: disk.free_mb, cls: "bg2" }], diskPct + "%", "مستخدم")}<dl class="kv flat"><dt>الإجمالي</dt><dd class="ltr">${fmtMB(disk.total_mb)}</dd><dt>حر</dt><dd class="ltr">${fmtMB(disk.free_mb)}</dd><dt>تطبيقات تعمل</dt><dd>${nf.format(d.processes.running_count)}</dd></dl></div>` : html`<p class="mute">غير متاح.</p>`}</div></div>
      <div class="card"><div class="card-head"><h3>الدعوات</h3></div><div class="card-body"><dl class="kv flat"><dt>إجمالي الإحالات</dt><dd>${nf.format(d.referral.total)}</dd><dt>تجارب فعّالة</dt><dd>${nf.format(d.referral.active_trials)}</dd></dl>
        ${d.referral.top.length ? html`<hr class="line"><div class="top-list">${d.referral.top.slice(0, 4).map(u => html`<div class="row">${avatar(u.first_name || u.username, u.telegram_id, "sm")}<span class="grow">${u.first_name || u.username || u.telegram_id}</span><b>${nf.format(u.referral_count)}</b></div>`)}</div>` : ""}</div></div></div></div>`);
};

/* ───────── Requests ───────── */
const REJECT_PRESETS = ["لم يصل الدفع", "المبلغ غير مطابق للخطة", "رقم الحوالة غير صحيح", "طلب مكرر"];
SEC.requests = async (ctx, q) => {
  let status = q.get("s") || "pending";
  const draw = async () => {
    const { requests } = await api(`/admin/requests?status=${status}`);
    if (!ctx.alive()) return;
    setHTML($("#rqbody"), requests.length ? html`<div class="req-grid">${requests.map(reqCard)}</div>` : html`<div class="card">${emptyBlock("inbox", status === "pending" ? "لا توجد طلبات معلّقة" : "لا توجد طلبات", status === "pending" ? "عندما يرسل مستخدم طلباً سيظهر هنا وسيصلك إشعار في تيليجرام." : "")}</div>`);
    $$("[data-ok]").forEach(b => b.onclick = () => approve(requests.find(r => r.id === b.dataset.ok)));
    $$("[data-no]").forEach(b => b.onclick = () => reject(requests.find(r => r.id === b.dataset.no)));
    $$("[data-u]").forEach(b => b.onclick = () => userDrawer(b.dataset.u, ctx));
  };
  const reqCard = r => {
    const u = r.user, name = u.name || u.username || "—";
    return html`<article class="card req ${r.status}"><div class="req-top">${avatar(name, u.telegram_id, "lg")}<div class="grow"><b>${name}</b><div class="mute ltr">${u.username ? "@" + u.username + " · " : ""}ID ${u.telegram_id}</div></div><span class="pill ${r.status === "pending" ? "warn" : r.status === "approved" ? "ok" : "bad"}"><i></i>${{ pending: "بانتظار القرار", approved: "مفعّل", rejected: "مرفوض" }[r.status]}</span></div>
      <dl class="kv flat"><dt>الخطة</dt><dd>${r.icon} ${r.plan}</dd><dt>المدة</dt><dd>${r.months} ${r.months === 1 ? "شهر" : "أشهر"}</dd><dt>المبلغ</dt><dd class="ltr"><b>${fmtIQD(r.total)}</b></dd><dt>الوقت</dt><dd>${fmtDT(r.created_at)}</dd>${r.note ? html`<dt>ملاحظة</dt><dd>${r.note}</dd>` : ""}${r.admin_note && r.status === "rejected" ? html`<dt>سبب الرفض</dt><dd>${r.admin_note}</dd>` : ""}</dl>
      <div class="req-act">${r.status === "pending" ? html`<button class="btn" data-ok="${r.id}" type="button">${icon("check", "sm")} قبول</button><button class="btn ghost danger" data-no="${r.id}" type="button">${icon("x", "sm")} رفض</button>` : ""}
        <a class="btn ghost" href="${tgLink(u)}" target="_blank" rel="noopener noreferrer">${icon("telegram", "sm", "fill")} الملف الشخصي</a><button class="btn quiet" data-u="${u.id}" type="button">${icon("user", "sm")} في اللوحة</button></div></article>`;
  };
  async function approve(r) {
    if (!await confirmBox({ title: "قبول الطلب", text: `سيُفعَّل ${r.icon} ${r.plan} لمدة ${r.months} ${r.months === 1 ? "شهر" : "أشهر"} للمستخدم ${r.user.name || r.user.username}، وسيصله إشعار.`, label: "تأكيد التفعيل" })) return;
    try { await post(`/admin/requests/${r.id}/approve`); toast("تم تفعيل الاشتراك", "ok"); refreshBadges(); draw(); } catch (e) { fail(e); }
  }
  async function reject(r) {
    let reason = REJECT_PRESETS[0];
    const res = await openDialog({ title: "رفض الطلب", body: html`<p style="margin-bottom:12px">سيصل المستخدم إشعار بالسبب.</p><div class="chips" id="pr">${REJECT_PRESETS.map((p, i) => html`<button type="button" class="chip" aria-pressed="${i === 0}">${p}</button>`)}</div><div class="field" style="margin-top:14px"><label for="rr">السبب</label><input id="rr" class="input" maxlength="300" value="${REJECT_PRESETS[0]}"></div>`,
      buttons: [{ label: "رفض الطلب", kind: "solid-danger", run: async d => { reason = $("#rr", d).value.trim(); if (!reason) throw new Error("اكتب السبب."); await post(`/admin/requests/${r.id}/reject`, { reason }); } }, { label: "إلغاء", kind: "ghost" }],
      onOpen: d => $$("#pr .chip", d).forEach(c => c.onclick = () => { $("#rr", d).value = c.textContent; $$("#pr .chip", d).forEach(x => x.setAttribute("aria-pressed", String(x === c))); }) });
    if (res === "ok") { toast("تم رفض الطلب", "ok"); refreshBadges(); draw(); }
  }
  setHTML(ctx.el, html`${head("طلبات الاشتراك", "راجع الطلبات وفعّل الاشتراكات. تصلك أيضاً في تيليجرام بأزرار قبول ورفض.")}
    <div class="toolbar"><div class="chips" id="rqf">${[["pending", "قيد المراجعة"], ["approved", "مقبولة"], ["rejected", "مرفوضة"], ["all", "الكل"]].map(([k, t]) => html`<button class="chip" data-s="${k}" aria-pressed="${k === status}">${t}</button>`)}</div></div><div id="rqbody">${loadingBlock()}</div>`);
  $$("#rqf .chip").forEach(b => b.onclick = () => { status = b.dataset.s; $$("#rqf .chip").forEach(x => x.setAttribute("aria-pressed", String(x === b))); setHTML($("#rqbody"), loadingBlock()); draw(); });
  await draw();
};

/* ───────── Users ───────── */
SEC.users = async (ctx, q) => {
  let term = q.get("q") || "", page = 0;
  setHTML(ctx.el, html`${head("المستخدمون")}<div class="toolbar"><label class="searchfield">${icon("search", "sm")}<input id="uq" value="${term}" placeholder="ابحث بالاسم أو المعرّف أو ID" aria-label="بحث"></label></div><div id="ubody">${loadingBlock()}</div>`);
  const draw = async () => {
    const r = await api(`/admin/users?q=${encodeURIComponent(term)}&page=${page}`); if (!ctx.alive()) return;
    setHTML($("#ubody"), r.users.length ? html`<div class="card"><div class="tbl-wrap"><table class="tbl hov"><thead><tr><th>المستخدم</th><th>الخطة</th><th>المواقع</th><th>انضم</th><th></th></tr></thead><tbody>
      ${r.users.map(u => html`<tr data-u="${u.id}" tabindex="0"><td><div class="row">${avatar(u.first_name || u.username, u.telegram_id, "sm")}<div><b>${u.first_name || "—"}</b> ${u.is_admin ? html`<span class="tag gold sm">أدمن</span>` : ""}${u.is_banned ? html`<span class="tag bad sm">محظور</span>` : ""}<div class="mute sm ltr">${u.username ? "@" + u.username + " · " : ""}${u.telegram_id}</div></div></div></td><td>${u.plan || "—"}${u.trial_active ? html` <span class="tag sm">تجربة</span>` : ""}</td><td>${nf.format(u.sites)}</td><td class="mute">${fmtD(u.created_at)}</td><td><a class="iconbtn sm" href="${tgLink(u)}" target="_blank" rel="noopener noreferrer" aria-label="الملف الشخصي" onclick="event.stopPropagation()">${icon("telegram", "sm", "fill")}</a></td></tr>`)}</tbody></table></div>
      <div class="pager"><button class="btn ghost sm" id="pp" ${page === 0 ? "disabled" : ""}>السابق</button><span>صفحة ${page + 1}</span><button class="btn ghost sm" id="pn" ${r.has_more ? "" : "disabled"}>التالي</button></div></div>` : html`<div class="card">${emptyBlock("users", "لا نتائج")}</div>`);
    $$("#ubody tr[data-u]").forEach(tr => { tr.onclick = () => userDrawer(tr.dataset.u, ctx, draw); tr.onkeydown = e => { if (e.key === "Enter") tr.click(); }; });
    const pp = $("#pp"), pn = $("#pn"); if (pp) pp.onclick = () => { page--; draw(); }; if (pn) pn.onclick = () => { page++; draw(); };
  };
  $("#uq").addEventListener("input", debounce(e => { term = e.target.value.trim(); page = 0; draw(); }, 300));
  await draw();
};

async function userDrawer(uid, ctx, after) {
  const d = await api(`/admin/users/${uid}`).catch(e => { fail(e); return null; }); if (!d) return;
  const { plans } = await api("/admin/plans");
  const u = d.user, sub = d.subscription, name = u.first_name || u.username || String(u.telegram_id);
  const body = html`<div class="u-head">${avatar(name, u.telegram_id, "lg")}<div class="grow"><h3>${name}</h3><div class="mute ltr">${u.username ? "@" + u.username + " · " : ""}ID ${u.telegram_id}</div><div class="row" style="margin-top:6px">${u.is_admin ? html`<span class="tag gold">أدمن</span>` : ""}${u.is_banned ? html`<span class="tag bad">محظور</span>` : ""}${u.trial_active ? html`<span class="tag">تجربة</span>` : ""}</div></div></div>
    ${u.is_banned && u.ban_reason ? notice("bad", "سبب الحظر", u.ban_reason) : ""}
    <div class="row wrap" style="margin:16px 0"><a class="btn" href="${tgLink(u)}" target="_blank" rel="noopener noreferrer">${icon("telegram", "sm", "fill")} الملف الشخصي</a><button class="btn ghost" id="umsg" type="button">${icon("send", "sm")} رسالة</button>${u.is_admin ? "" : u.is_banned ? html`<button class="btn ghost" id="uunban" type="button">رفع الحظر</button>` : html`<button class="btn ghost danger" id="uban" type="button">${icon("ban", "sm")} حظر</button>`}</div>
    <h4 class="dh">الاشتراك</h4><dl class="kv flat"><dt>الخطة</dt><dd>${sub.effective_plan_name}${sub.boosted_by_trial ? " (تجربة)" : ""}</dd><dt>ينتهي</dt><dd>${sub.expires_at ? fmtD(sub.expires_at) + (sub.expired ? " · منتهي" : "") : "بدون انتهاء"}</dd><dt>المواقع</dt><dd>${d.usage.sites} / ${sub.limits.sites < 0 ? "∞" : sub.limits.sites}</dd><dt>المساحة</dt><dd class="ltr">${fmtMB(d.usage.mb)} / ${sub.limits.mb < 0 ? "∞" : fmtMB(sub.limits.mb)}</dd><dt>انضم</dt><dd>${fmtDT(u.created_at)}</dd><dt>إحالات</dt><dd>${u.referral_count}</dd></dl>
    <details class="more" open><summary>تغيير الخطة والحدود</summary><div class="field"><label for="upl">الخطة</label><select id="upl" class="select">${plans.map(p => html`<option value="${p.id}" ${p.id === sub.plan.id ? "selected" : ""}>${p.icon} ${p.name}</option>`)}</select></div>
      <div class="field"><label for="udy">المدة بالأيام من الآن <small>(0 = بلا انتهاء، فارغ = بدون تغيير)</small></label><input id="udy" class="input ltr" inputmode="numeric" placeholder="30"></div>
      <div class="field-row"><div class="field"><label for="cs">حد المواقع</label><input id="cs" class="input ltr" inputmode="numeric" value="${sub.custom.sites ?? ""}" placeholder="افتراضي"></div><div class="field"><label for="cm">حد المساحة MB</label><input id="cm" class="input ltr" inputmode="numeric" value="${sub.custom.mb ?? ""}" placeholder="افتراضي"></div></div>
      <div class="field-row"><div class="field"><label for="cd">دومينات مخصصة</label><input id="cd" class="input ltr" inputmode="numeric" value="${sub.custom.domains ?? ""}" placeholder="افتراضي"></div><div class="field"><label for="cb">نسخ احتياطية</label><input id="cb" class="input ltr" inputmode="numeric" value="${sub.custom.backups ?? ""}" placeholder="افتراضي"></div></div>
      <button class="btn" id="usave" type="button">حفظ التغييرات</button></details>
    <h4 class="dh">المواقع (${d.sites.length})</h4>${d.sites.length ? html`<div class="rack mini">${d.sites.map(s => html`<a class="unit" href="#/site/${s.id}" data-close><span class="led ${ledCls(s.status)}"></span><div class="u-main"><b>${s.name}</b><span class="u-url ltr">${s.slug}</span></div>${pill(s.status)}</a>`)}</div>` : html`<p class="mute">لا مواقع.</p>`}`;
  openDialog({ title: "ملف المستخدم", drawer: true, body, buttons: [], onOpen: dlg => {
    const done = () => { dlg.close("ok"); after && after(); refreshBadges(); };
    $("#usave", dlg).onclick = async e => {
      const b = e.currentTarget; b.classList.add("is-busy");
      try { await post(`/admin/users/${uid}/plan`, { plan_id: $("#upl", dlg).value, days: $("#udy", dlg).value, custom_sites: $("#cs", dlg).value, custom_mb: $("#cm", dlg).value, custom_domains: $("#cd", dlg).value, custom_backups: $("#cb", dlg).value }); toast("تم الحفظ", "ok"); done(); }
      catch (er) { fail(er); b.classList.remove("is-busy"); }
    };
    const ban = $("#uban", dlg); if (ban) ban.onclick = async () => { const r = await promptBox({ title: "حظر المستخدم", label: "السبب", confirm: "حظر", placeholder: "مخالفة الشروط" }); if (r === null) return; try { await post(`/admin/users/${uid}/ban`, { reason: r }); toast("تم الحظر", "ok"); done(); } catch (e) { fail(e); } };
    const unb = $("#uunban", dlg); if (unb) unb.onclick = async () => { try { await post(`/admin/users/${uid}/unban`); toast("تم رفع الحظر", "ok"); done(); } catch (e) { fail(e); } };
    $("#umsg", dlg).onclick = async () => { const t = await promptBox({ title: "رسالة للمستخدم", label: "النص", multiline: true, confirm: "إرسال" }); if (!t) return; try { await post(`/admin/users/${uid}/message`, { text: t }); toast("أُرسلت الرسالة", "ok"); } catch (e) { fail(e); } };
  } });
}

/* ───────── Sites ───────── */
SEC.sites = async (ctx, q) => {
  let term = q.get("q") || "", page = 0;
  setHTML(ctx.el, html`${head("كل المواقع")}<div class="toolbar"><label class="searchfield">${icon("search", "sm")}<input id="sq" value="${term}" placeholder="اسم الموقع أو المالك" aria-label="بحث"></label></div><div id="sbody">${loadingBlock()}</div>`);
  const draw = async () => {
    const r = await api(`/admin/sites?q=${encodeURIComponent(term)}&page=${page}`); if (!ctx.alive()) return;
    setHTML($("#sbody"), r.sites.length ? html`<div class="card"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>الموقع</th><th>المالك</th><th>النوع</th><th>المساحة</th><th>الحالة</th><th></th></tr></thead><tbody>
      ${r.sites.map(s => html`<tr><td><div class="row"><span class="led ${ledCls(s.status)}"></span><div><a href="#/site/${s.id}"><b>${s.name}</b></a><div class="mute sm ltr">${s.slug}</div></div></div></td><td class="ltr">@${s.owner}</td><td>${KIND[s.kind] || s.kind}</td><td class="ltr">${fmtMB(s.used_mb)}</td><td>${pill(s.status)}</td>
        <td><div class="row"><a class="iconbtn sm" href="${siteUrl(s)}" target="_blank" rel="noopener noreferrer" aria-label="فتح">${icon("external", "sm")}</a>${s.status === "suspended" ? html`<button class="btn ghost sm" data-un="${s.id}">رفع الإيقاف</button>` : html`<button class="btn ghost sm danger" data-su="${s.id}">إيقاف</button>`}</div></td></tr>`)}</tbody></table></div>
      <div class="pager"><button class="btn ghost sm" id="pp" ${page === 0 ? "disabled" : ""}>السابق</button><span>صفحة ${page + 1}</span><button class="btn ghost sm" id="pn" ${r.has_more ? "" : "disabled"}>التالي</button></div></div>` : html`<div class="card">${emptyBlock("globe", "لا نتائج")}</div>`);
    $$("[data-su]").forEach(b => b.onclick = async () => { if (!await confirmBox({ title: "إيقاف الموقع", text: "سيتوقف الموقع ولن يستطيع المالك تشغيله حتى ترفع الإيقاف.", label: "إيقاف", danger: true })) return; try { await post(`/admin/sites/${b.dataset.su}/suspend`); draw(); } catch (e) { fail(e); } });
    $$("[data-un]").forEach(b => b.onclick = async () => { try { await post(`/admin/sites/${b.dataset.un}/unsuspend`); draw(); } catch (e) { fail(e); } });
    const pp = $("#pp"), pn = $("#pn"); if (pp) pp.onclick = () => { page--; draw(); }; if (pn) pn.onclick = () => { page++; draw(); };
  };
  $("#sq").addEventListener("input", debounce(e => { term = e.target.value.trim(); page = 0; draw(); }, 300));
  await draw();
};

/* ───────── Plans ───────── */
SEC.plans = async ctx => {
  const draw = async () => {
    const { plans } = await api("/admin/plans"); if (!ctx.alive()) return;
    setHTML(ctx.el, html`${head("الخطط والأسعار", "تظهر الخطط المفعّلة للمستخدمين في الصفحة الرئيسية وصفحة الاشتراك.", html`<button class="btn" id="np" type="button">${icon("plus", "sm")} خطة جديدة</button>`)}
      <div class="plans admin-plans">${plans.map(p => html`<article class="plan ${p.is_active ? "" : "off"}"><div class="row"><h3 class="grow">${p.icon} ${p.name}</h3>${p.is_default ? html`<span class="tag">افتراضية</span>` : ""}${p.is_active ? "" : html`<span class="tag bad">معطّلة</span>`}</div>
        <div class="price"><b class="num">${nf.format(p.price_monthly)}</b><span>د.ع / شهرياً</span></div>
        <ul class="plim"><li><b>${p.max_sites}</b> مواقع</li><li><b>${fmtMB(p.max_mb)}</b></li><li><b>${p.max_backups}</b> نسخ</li><li><b>${p.max_custom_domains}</b> دومين مخصص</li><li><b>${p.max_platform_domains}</b> دومين منصة</li></ul>
        <p class="mute sm">${nf.format(p.subscribers)} مشترك</p><div class="row wrap" style="margin-top:12px"><button class="btn ghost sm" data-e="${p.id}">${icon("edit", "sm")} تعديل</button><button class="btn ghost sm" data-t="${p.id}">${p.is_active ? "تعطيل" : "تفعيل"}</button>${p.is_default ? "" : html`<button class="btn ghost sm" data-d="${p.id}">افتراضية</button><button class="iconbtn sm" data-x="${p.id}" aria-label="حذف">${icon("trash", "sm")}</button>`}</div></article>`)}</div>`);
    const form = p => html`<div class="field-row"><div class="field"><label for="pn">اسم الخطة</label><input id="pn" class="input" maxlength="64" value="${p ? p.name : ""}"></div><div class="field narrow-f"><label for="pi">أيقونة</label><input id="pi" class="input" maxlength="8" value="${p ? p.icon : "📦"}"></div></div>
      <div class="field-row"><div class="field"><label for="pp">السعر الشهري (د.ع)</label><input id="pp" class="input ltr" inputmode="numeric" value="${p ? p.price_monthly : 0}"></div><div class="field"><label for="ps">عدد المواقع</label><input id="ps" class="input ltr" inputmode="numeric" value="${p ? p.max_sites : 1}"></div></div>
      <div class="field-row"><div class="field"><label for="pm">المساحة MB</label><input id="pm" class="input ltr" inputmode="numeric" value="${p ? p.max_mb : 100}"></div><div class="field"><label for="pb">النسخ الاحتياطية</label><input id="pb" class="input ltr" inputmode="numeric" value="${p ? p.max_backups : 1}"></div></div>
      <div class="field-row"><div class="field"><label for="pc">دومينات مخصصة</label><input id="pc" class="input ltr" inputmode="numeric" value="${p ? p.max_custom_domains : 0}"></div><div class="field"><label for="pd">دومينات المنصة</label><input id="pd" class="input ltr" inputmode="numeric" value="${p ? p.max_platform_domains : 0}"></div></div>
      <div class="field"><label for="pf">ميزات إضافية <small>(سطر لكل ميزة)</small></label><textarea id="pf" class="textarea" rows="3">${p ? p.features_raw : ""}</textarea></div>`;
    const read = d => ({ name: $("#pn", d).value, icon: $("#pi", d).value, price_monthly: $("#pp", d).value, max_sites: $("#ps", d).value, max_mb: $("#pm", d).value, max_backups: $("#pb", d).value, max_custom_domains: $("#pc", d).value, max_platform_domains: $("#pd", d).value, features: $("#pf", d).value });
    $("#np").onclick = async () => { const r = await openDialog({ title: "خطة جديدة", body: form(null), buttons: [{ label: "إنشاء", kind: "primary", run: d => post("/admin/plans", read(d)) }, { label: "إلغاء", kind: "ghost" }] }); if (r === "ok") { toast("تم إنشاء الخطة", "ok"); draw(); } };
    $$("[data-e]").forEach(b => b.onclick = async () => { const p = plans.find(x => x.id === b.dataset.e); const r = await openDialog({ title: "تعديل الخطة", body: form(p), buttons: [{ label: "حفظ", kind: "primary", run: d => post(`/admin/plans/${p.id}`, read(d)) }, { label: "إلغاء", kind: "ghost" }] }); if (r === "ok") { toast("تم الحفظ", "ok"); draw(); } });
    $$("[data-t]").forEach(b => b.onclick = async () => { try { await post(`/admin/plans/${b.dataset.t}/toggle`); draw(); } catch (e) { fail(e); } });
    $$("[data-d]").forEach(b => b.onclick = async () => { try { await post(`/admin/plans/${b.dataset.d}/default`); toast("تم تعيين الخطة الافتراضية", "ok"); draw(); } catch (e) { fail(e); } });
    $$("[data-x]").forEach(b => b.onclick = async () => { if (!await confirmBox({ title: "حذف الخطة", text: "لا يمكن حذف خطة عليها مشتركون.", label: "حذف", danger: true })) return; try { await post(`/admin/plans/${b.dataset.x}/delete`); draw(); } catch (e) { fail(e); } });
  };
  await draw();
};

/* ───────── Platform domains ───────── */
SEC.domains = async ctx => {
  const draw = async () => {
    const { domains } = await api("/admin/pdomains"); if (!ctx.alive()) return;
    setHTML(ctx.el, html`${head("دومينات المنصة", "دومينات يستطيع المشتركون ربطها بمواقعهم حسب خطتهم.", html`<button class="btn" id="nd" type="button">${icon("plus", "sm")} إضافة دومين</button>`)}
      ${domains.length ? html`<div class="card"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>الدومين</th><th>الوصف</th><th>الحالة</th><th>مرتبط بـ</th><th></th></tr></thead><tbody>${domains.map(d => html`<tr><td class="ltr"><b>${d.hostname}</b></td><td>${d.label || "—"}</td><td>${d.is_active ? html`<span class="pill ok"><i></i>متاح</span>` : html`<span class="pill"><i></i>معطّل</span>`}</td><td>${d.used ? d.used_by : "—"}</td><td><div class="row"><button class="btn ghost sm" data-t="${d.id}">${d.is_active ? "تعطيل" : "تفعيل"}</button><button class="iconbtn sm" data-x="${d.id}" aria-label="حذف">${icon("trash", "sm")}</button></div></td></tr>`)}</tbody></table></div></div>` : html`<div class="card">${emptyBlock("globe", "لا توجد دومينات", "أضف دومينات ليختار منها المشتركون.")}</div>`}`);
    $("#nd").onclick = async () => { const r = await openDialog({ title: "إضافة دومين", body: html`<div class="field"><label for="dh">الدومين</label><input id="dh" class="input ltr" placeholder="site.example.com" autocapitalize="off"></div><div class="field"><label for="dl">وصف <small>(اختياري)</small></label><input id="dl" class="input" maxlength="128"></div>`, buttons: [{ label: "إضافة", kind: "primary", run: d => post("/admin/pdomains", { hostname: $("#dh", d).value, label: $("#dl", d).value }) }, { label: "إلغاء", kind: "ghost" }] }); if (r === "ok") draw(); };
    $$("[data-t]").forEach(b => b.onclick = async () => { try { await post(`/admin/pdomains/${b.dataset.t}/toggle`); draw(); } catch (e) { fail(e); } });
    $$("[data-x]").forEach(b => b.onclick = async () => { if (!await confirmBox({ title: "حذف الدومين", text: "سيُفك عن الموقع المرتبط به إن وُجد.", label: "حذف", danger: true })) return; try { await post(`/admin/pdomains/${b.dataset.x}/delete`); draw(); } catch (e) { fail(e); } });
  };
  await draw();
};

/* ───────── Messages: broadcast + bot texts ───────── */
SEC.messages = async ctx => {
  const [{ texts }, bs] = await Promise.all([api("/admin/texts"), api("/admin/broadcast")]); if (!ctx.alive()) return;
  let key = texts[0].key;
  const bubble = t => html`<div class="tg-preview"><div class="tg-top"><img src="/panel/static/img/mark-64.png" alt=""><b>Saw Host</b></div><div class="tg-bubble">${tgHtml(t) || html`<span class="mute">المعاينة تظهر هنا</span>`}</div></div>`;
  setHTML(ctx.el, html`${head("الرسائل والنصوص", "بث رسالة لكل المستخدمين، أو عدّل نصوص البوت.")}
    <div class="split"><div class="card"><div class="card-head"><h3>بث رسالة</h3></div><div class="card-body">
      <div class="field"><label for="bt">نص الرسالة</label><textarea id="bt" class="textarea" rows="6" maxlength="3500" placeholder="يدعم الوسوم: <b> <i> <code> <a href>"></textarea><p class="hint"><span id="bc">0</span> / 3500 · تُرسل لكل المستخدمين غير المحظورين.</p></div>
      <div id="bprog"></div><button class="btn" id="bgo" type="button">${icon("send", "sm")} إرسال للجميع</button></div></div>
      <aside id="bprev">${bubble("")}</aside></div>
    <div class="card" style="margin-top:20px"><div class="card-head"><div><h3>نصوص البوت واللوحة</h3><p class="sub">تظهر في البوت وصفحة «عن المنصة» وتعليمات الدفع.</p></div></div><div class="card-body">
      <div class="chips" id="tk">${texts.map(t => html`<button class="chip" data-k="${t.key}" aria-pressed="${t.key === key}">${t.desc}${t.is_custom ? " •" : ""}</button>`)}</div>
      <div class="split" style="margin-top:16px;align-items:start"><div><textarea id="tt" class="textarea" rows="10" maxlength="4000"></textarea><div class="row" style="margin-top:12px"><button class="btn" id="tsave" type="button">حفظ</button><button class="btn ghost" id="treset" type="button">استعادة الافتراضي</button></div></div><div id="tprev"></div></div></div></div>`);
  const bt = $("#bt");
  bt.addEventListener("input", () => { $("#bc").textContent = bt.value.length; setHTML($("#bprev"), bubble(bt.value)); });
  const showB = s => setHTML($("#bprog"), s.total || s.running ? html`<div class="prog-box">${bar(s.sent + s.failed, s.total || 1)}<p class="mute sm">${nf.format(s.sent)} أُرسلت · ${nf.format(s.failed)} فشلت من ${nf.format(s.total)} ${s.running ? "· جارٍ الإرسال…" : "· اكتمل"}</p></div>` : "");
  showB(bs);
  const poll = () => later(ctx, async () => { try { const s = await api("/admin/broadcast"); showB(s); if (s.running) poll(); else $("#bgo").disabled = false; } catch { /* ignore */ } }, 1500);
  if (bs.running) { $("#bgo").disabled = true; poll(); }
  $("#bgo").onclick = async () => {
    const t = bt.value.trim(); if (!t) return toast("اكتب نص الرسالة.", "warn");
    if (!await confirmBox({ title: "إرسال للجميع", text: "ستصل هذه الرسالة لكل المستخدمين. هل أنت متأكد؟", label: "إرسال" })) return;
    try { await post("/admin/broadcast", { text: t }); $("#bgo").disabled = true; toast("بدأ الإرسال", "ok"); showB({ running: true, sent: 0, failed: 0, total: 0 }); poll(); } catch (e) { fail(e); }
  };
  const drawT = () => { const t = texts.find(x => x.key === key); $("#tt").value = t.value; setHTML($("#tprev"), bubble(t.value)); $$("#tk .chip").forEach(c => c.setAttribute("aria-pressed", String(c.dataset.k === key))); };
  $$("#tk .chip").forEach(c => c.onclick = () => { key = c.dataset.k; drawT(); });
  $("#tt").addEventListener("input", e => setHTML($("#tprev"), bubble(e.target.value)));
  drawT();
  $("#tsave").onclick = async () => { try { await post(`/admin/texts/${key}`, { value: $("#tt").value }); texts.find(x => x.key === key).value = $("#tt").value; toast("تم الحفظ", "ok"); } catch (e) { fail(e); } };
  $("#treset").onclick = async () => { if (!await confirmBox({ title: "استعادة الافتراضي", text: "سيُستبدل النص الحالي بالنص الافتراضي.", label: "استعادة" })) return; try { const r = await post(`/admin/texts/${key}/reset`); texts.find(x => x.key === key).value = r.value; drawT(); toast("تمت الاستعادة", "ok"); } catch (e) { fail(e); } };
};

/* ───────── Notifications ───────── */
SEC.notifs = async ctx => {
  const { notifs } = await api("/admin/notifs"); if (!ctx.alive()) return;
  setHTML(ctx.el, html`${head("الإشعارات", "", notifs.some(n => !n.is_read) ? html`<button class="btn ghost" id="mr" type="button">تحديد الكل كمقروء</button>` : "")}
    ${notifs.length ? html`<div class="card"><div class="hist">${notifs.map(n => html`<div class="h-row ${n.is_read ? "" : "unread"}"><span class="led ${n.kind === "warning" ? "warn" : n.kind === "sub_request" ? "" : "ok"}"></span><div class="grow"><p>${n.text}</p><p class="mute sm">${ago(n.created_at)} · ${fmtDT(n.created_at)}</p></div></div>`)}</div></div>` : html`<div class="card">${emptyBlock("bell", "لا توجد إشعارات")}</div>`}`);
  const mr = $("#mr"); if (mr) mr.onclick = async () => { await post("/admin/notifs/read"); refreshBadges(); go(location.hash); };
};

/* ───────── Audit ───────── */
const ACT = { login: "تسجيل دخول", "login.admin": "دخول الأدمن", "login.fail": "محاولة دخول فاشلة", "request.approve": "قبول طلب", "request.reject": "رفض طلب", "user.ban": "حظر مستخدم", "user.unban": "رفع حظر", "user.plan": "تغيير خطة", "site.suspend": "إيقاف موقع", "site.unsuspend": "رفع إيقاف موقع", "plan.create": "إنشاء خطة", "plan.update": "تعديل خطة", "plan.delete": "حذف خطة", "settings": "تغيير إعدادات", "broadcast": "بث رسالة", "logout.all": "إنهاء كل الجلسات", "text.set": "تعديل نص" };
SEC.audit = async ctx => {
  let page = 0;
  const draw = async () => {
    const r = await api(`/admin/audit?page=${page}`); if (!ctx.alive()) return;
    setHTML(ctx.el, html`${head("سجل التدقيق", "كل الإجراءات الإدارية ومحاولات الدخول، مع الوقت والمصدر.")}
      ${r.items.length ? html`<div class="card"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>الوقت</th><th>المنفّذ</th><th>الإجراء</th><th>التفاصيل</th><th>IP</th></tr></thead><tbody>${r.items.map(a => html`<tr><td class="mute">${fmtDT(a.created_at)}</td><td>${a.actor || "—"}</td><td><span class="tag ${a.action.includes("fail") ? "bad" : ""}">${ACT[a.action] || a.action}</span></td><td>${a.detail || "—"}</td><td class="ltr mute">${a.ip || "—"}</td></tr>`)}</tbody></table></div>
        <div class="pager"><button class="btn ghost sm" id="pp" ${page === 0 ? "disabled" : ""}>السابق</button><span>صفحة ${page + 1}</span><button class="btn ghost sm" id="pn" ${r.has_more ? "" : "disabled"}>التالي</button></div></div>` : html`<div class="card">${emptyBlock("list", "لا توجد سجلات بعد")}</div>`}`);
    const pp = $("#pp"), pn = $("#pn"); if (pp) pp.onclick = () => { page--; draw(); }; if (pn) pn.onclick = () => { page++; draw(); };
  };
  await draw();
};

/* ───────── System ───────── */
SEC.system = async ctx => {
  const [set, h] = await Promise.all([api("/admin/settings"), api("/admin/health")]); if (!ctx.alive()) return;
  const chk = c => html`<div class="h-row"><span class="led ${c.ok ? "ok" : c.level === "warn" ? "warn" : "bad"}"></span><div class="grow"><b>${c.name}</b><p class="mute">${c.detail}</p></div>${c.ok ? html`<span class="tag ok">سليم</span>` : html`<span class="tag ${c.level === "warn" ? "warn" : "bad"}">${c.level === "warn" ? "تنبيه" : "يحتاج إجراء"}</span>`}</div>`;
  const up = s => { const d = Math.floor(s / 86400), hh = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return (d ? d + " يوم " : "") + (hh ? hh + " ساعة " : "") + m + " دقيقة"; };
  setHTML(ctx.el, html`${head("النظام والإعدادات")}
    <div class="split"><div class="stack">
      <div class="card"><div class="card-head"><h3>إعدادات المنصة</h3></div><div class="card-body">
        <div class="switch-row"><div><b>وضع الصيانة</b><p>يمنع المستخدمين العاديين من استخدام المنصة ويعرض لهم رسالة الصيانة. الأدمن غير متأثر.</p></div><label class="switch"><input type="checkbox" id="smt" ${set.maintenance ? "checked" : ""}><span></span></label></div><hr class="line">
        <div class="switch-row"><div><b>الدومينات المخصصة</b><p>السماح للمشتركين بربط دوميناتهم الخاصة.</p></div><label class="switch"><input type="checkbox" id="scd" ${set.custom_domains ? "checked" : ""}><span></span></label></div></div></div>
      <div class="card"><div class="card-head"><h3>فحوصات الأمان والجاهزية</h3></div><div class="hist">${h.checks.map(chk)}</div></div></div>
      <aside class="stack"><div class="card"><div class="card-head"><h3>الخادم</h3></div><div class="card-body"><dl class="kv flat"><dt>الإصدار</dt><dd class="ltr">${h.version}</dd><dt>مدة التشغيل</dt><dd>${up(h.uptime_s)}</dd><dt>Python</dt><dd class="ltr">${h.python}</dd><dt>قاعدة البيانات</dt><dd class="ltr">${h.db}</dd><dt>البوت</dt><dd class="ltr">${h.bot ? "@" + h.bot : "—"}</dd><dt>تطبيقات تعمل</dt><dd>${h.processes}</dd>${h.memory_mb ? html`<dt>ذاكرة السيرفر</dt><dd class="ltr">${fmtMB(h.memory_mb.used)} / ${fmtMB(h.memory_mb.total)}</dd>` : ""}${h.disk ? html`<dt>القرص</dt><dd class="ltr">${fmtMB(h.disk.used_mb)} / ${fmtMB(h.disk.total_mb)}</dd>` : ""}</dl></div></div></aside></div>`);
  const tog = (id, key, ok, off) => $(id).onchange = async e => { try { await post("/admin/settings", { [key]: e.target.checked }); toast(e.target.checked ? ok : off, "ok"); refreshBadges(); } catch (er) { e.target.checked = !e.target.checked; fail(er); } };
  tog("#smt", "maintenance", "تم تفعيل وضع الصيانة", "تم إيقاف وضع الصيانة"); tog("#scd", "custom_domains", "تم تفعيل الدومينات المخصصة", "تم إيقاف الدومينات المخصصة");
};
