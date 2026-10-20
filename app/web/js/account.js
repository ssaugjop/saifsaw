/* Saw Host — subscription, invites, account. */
import {
  $, $$, html, setHTML, icon, state, api, post, go, toast, fail, nf, fmtD, fmtDT, fmtMB, fmtIQD, daysLeft, ago, meter, bar, loadingBlock,
  emptyBlock, notice, copyText, curTheme, applyTheme, confirmBox, openDialog, avatar, tg, openExt, plural,
} from "./core.js";

const REQ = { pending: ["warn", "قيد المراجعة"], approved: ["ok", "مفعّل"], rejected: ["bad", "مرفوض"] };

/* ───────── Plan ───────── */
export async function viewPlan(ctx) {
  setHTML(ctx.el, loadingBlock());
  const [{ plans }, hist, me] = await Promise.all([api("/plans"), api("/subscription/history"), api("/me")]);
  state.me = me; if (!ctx.alive()) return;
  const sub = me.subscription, cur = sub.plan.id, dl = daysLeft(sub.expires_at), pending = hist.requests.filter(r => r.status === "pending");
  const paid = plans.filter(p => p.price_monthly > 0);
  const hot = paid.length >= 2 ? paid[1].id : "";
  setHTML(ctx.el, html`<div class="page-head"><div><h1>اشتراكي</h1><p class="sub">اختر الخطة المناسبة وأرسل الطلب، ويُفعَّل اشتراكك بعد تأكيد الدفع.</p></div></div>
    <div class="card cur-plan"><div class="card-body">
      <div class="cp-main"><span class="cp-ico">${sub.plan.icon}</span><div><span class="mute">خطتك الحالية</span><h2>${sub.effective_plan_name}</h2>
        <p class="mute">${sub.boosted_by_trial ? "تجربة مجانية فعّالة" : sub.expires_at ? (dl >= 0 ? `ينتهي في ${fmtD(sub.expires_at)} (بعد ${nf.format(dl)} يوم)` : `انتهى في ${fmtD(sub.expires_at)}`) : "بدون تاريخ انتهاء"}</p></div></div>
      <div class="cp-meters">${meter("المواقع", me.usage.sites, sub.limits.sites)}${meter("المساحة", me.usage.mb, sub.limits.mb, fmtMB)}</div></div></div>
    ${pending.length ? notice("info", "لديك طلب قيد المراجعة", `أُرسل ${ago(pending[0].created_at)}. سيصلك إشعار في تيليجرام فور التفعيل.`) : ""}
    <section class="section"><div class="plans">${paid.length ? paid.map(p => planCard(p, p.id === cur, p.id === hot)) : html`<div class="card">${emptyBlock("card", "لا توجد خطط متاحة حالياً")}</div>`}</div></section>
    <section class="section"><div class="sec-head"><h2>طلباتي</h2></div>
      ${hist.requests.length ? html`<div class="card"><div class="tbl-wrap"><table class="tbl"><thead><tr><th>الخطة</th><th>المدة</th><th>المبلغ</th><th>الحالة</th><th>التاريخ</th></tr></thead><tbody>
        ${hist.requests.map(r => html`<tr><td>${r.icon} ${r.plan}</td><td>${r.months} ${r.months === 1 ? "شهر" : "أشهر"}</td><td class="ltr">${fmtIQD(r.total)}</td><td><span class="pill ${REQ[r.status][0]}"><i></i>${REQ[r.status][1]}</span>${r.admin_note && r.status === "rejected" ? html`<div class="mute sm">${r.admin_note}</div>` : ""}</td><td>${fmtDT(r.created_at)}</td></tr>`)}</tbody></table></div></div>`
      : html`<div class="card">${emptyBlock("receipt", "لا توجد طلبات بعد")}</div>`}</section>`);
  $$("[data-buy]").forEach(b => b.onclick = () => buy(paid.find(p => p.id === b.dataset.buy), me));
}
function planCard(p, current, hot) {
  const feats = p.features || [];
  return html`<article class="plan ${hot ? "hot" : ""} ${current ? "cur" : ""}">${hot ? html`<span class="tag gold ribbon">الأكثر طلباً</span>` : ""}
    <h3>${p.icon} ${p.name}</h3><div class="price"><b class="num">${nf.format(p.price_monthly)}</b><span>د.ع / شهرياً</span></div>
    <ul class="plim"><li><b>${p.max_sites}</b> ${p.max_sites === 1 ? "موقع" : "مواقع"}</li><li><b>${fmtMB(p.max_mb)}</b> مساحة</li><li><b>${p.max_backups}</b> نسخ احتياطية</li>${p.max_custom_domains ? html`<li><b>${p.max_custom_domains}</b> دومين مخصص</li>` : ""}${p.max_platform_domains ? html`<li><b>${p.max_platform_domains}</b> دومين من المنصة</li>` : ""}</ul>
    ${feats.length ? html`<ul class="pfeat">${feats.map(f => html`<li>${icon("check", "sm")} ${f}</li>`)}</ul>` : ""}
    <button class="btn ${hot ? "" : "ghost"} block" data-buy="${p.id}" type="button">${current ? "تجديد هذه الخطة" : "اشترك بهذه الخطة"}</button></article>`;
}
async function buy(p, me) {
  let months = 1;
  const upd = d => { $("#tot", d).textContent = fmtIQD(p.price_monthly * months); $$(".seg button", d).forEach(b => b.setAttribute("aria-pressed", String(+b.dataset.m === months))); };
  await openDialog({
    title: `${p.icon} ${p.name}`,
    body: html`<div class="field"><label>مدة الاشتراك</label><div class="seg block">${[1, 3, 6, 12].map(m => html`<button type="button" data-m="${m}">${m === 1 ? "شهر" : m + " أشهر"}</button>`)}</div></div>
      <div class="total-row"><span>الإجمالي</span><b id="tot" class="ltr"></b></div>
      ${me.payment_info ? html`<div class="pay-info"><b>طريقة الدفع</b><p>${me.payment_info}</p></div>` : ""}
      <div class="field" style="margin-top:14px"><label for="bn">ملاحظة <small>(رقم الحوالة أو أي تفاصيل)</small></label><textarea id="bn" class="textarea" rows="2" maxlength="500"></textarea></div>
      <p class="hint">بعد الإرسال يصل طلبك للمشرف في تيليجرام، وتفعَّل الخطة فور تأكيد الدفع.</p>`,
    buttons: [{ label: "إرسال الطلب", kind: "primary", run: async d => { await post("/subscription/request", { plan_id: p.id, months, note: $("#bn", d).value }); toast("تم إرسال طلبك للمشرف", "ok"); go(location.hash); } }, { label: "إلغاء", kind: "ghost" }],
    onOpen: d => { $$(".seg button", d).forEach(b => b.onclick = () => { months = +b.dataset.m; upd(d); }); upd(d); },
  });
}

/* ───────── Invite ───────── */
export async function viewInvite(ctx) {
  const me = state.me, r = me.referral, u = me.user;
  const earned = Math.min(r.max_days, r.count * r.days_per_referral);
  const text = "جرّب Saw Host لاستضافة المواقع. سجّل من رابطي واحصل على تجربة مجانية.";
  const share = `https://t.me/share/url?url=${encodeURIComponent(r.link)}&text=${encodeURIComponent(text)}`;
  setHTML(ctx.el, html`<div class="page-head"><div><h1>الدعوات والتجربة المجانية</h1><p class="sub">احصل على ${plural(r.days_per_referral, "يوم", "يومين", "أيام", "يوماً")} مجاناً عن كل صديق ينضم من رابطك، حتى ${r.max_days} يوماً.</p></div></div>
    <div class="split"><div class="stack">
      <div class="card"><div class="card-head"><h3>رابطك الخاص</h3></div><div class="card-body">
        <div class="copyline ltr"><code>${r.link}</code><button class="iconbtn sm" data-copy="${r.link}" aria-label="نسخ">${icon("copy", "sm")}</button></div>
        <div class="row wrap" style="margin-top:14px"><button class="btn" id="cp" type="button">${icon("copy", "sm")} نسخ الرابط</button><a class="btn ghost" href="${share}" target="_blank" rel="noopener noreferrer">${icon("telegram", "sm", "fill")} مشاركة على تيليجرام</a></div></div></div>
      <div class="card"><div class="card-head"><h3>كيف تعمل؟</h3></div><div class="card-body"><ol class="steps v"><li class="active"><span class="mark">1</span><b>شارك رابطك</b></li><li class="active"><span class="mark">2</span><b>صديقك يفتح البوت من الرابط</b></li><li class="active"><span class="mark">3</span><b>تُضاف لك أيام تجربة بصلاحيات أعلى خطة</b></li></ol></div></div></div>
    <aside class="card"><div class="card-body"><div class="stats one"><div class="stat"><span class="k">أصدقاء انضموا</span><b class="v">${nf.format(r.count)}</b></div><div class="stat"><span class="k">أيام التجربة المكتسبة</span><b class="v">${nf.format(earned)}<small>من ${r.max_days}</small></b>${bar(earned, r.max_days, true)}</div></div>
      ${u.trial_active && u.trial_expires_at ? notice("ok", "تجربتك فعّالة", `تنتهي في ${fmtD(u.trial_expires_at)}.`) : ""}</div></aside></div>`);
  $("#cp").onclick = () => copyText(r.link, "تم نسخ الرابط");
}

/* ───────── Account ───────── */
export async function viewAccount(ctx) {
  const me = state.me, u = me.user, sub = me.subscription;
  const name = u.first_name || u.username || "مستخدم";
  const mode = (() => { try { return localStorage.getItem("saw-theme") || "system"; } catch { return "system"; } })();
  setHTML(ctx.el, html`<div class="page-head"><div><h1>الحساب والأمان</h1></div></div>
    <div class="stack narrow">
      <div class="card"><div class="card-body acct-head">${avatar(name, u.telegram_id, "lg")}<div class="grow"><h2>${name}</h2><p class="mute ltr">${u.username ? "@" + u.username : ""} · ID ${u.telegram_id}</p></div>${u.is_admin ? html`<span class="tag gold">أدمن</span>` : ""}</div>
        <dl class="kv" style="padding:0 22px 8px"><dt>الخطة</dt><dd>${sub.effective_plan_name}</dd><dt>انضممت في</dt><dd>${fmtD(u.created_at)}</dd></dl></div>
      <div class="card"><div class="card-head"><h3>المظهر</h3></div><div class="card-body"><div class="seg" id="thm">${[["light", "فاتح", "sun"], ["dark", "داكن", "moon"]].map(([k, t, i]) => html`<button type="button" data-t="${k}" aria-pressed="${curTheme() === k}">${icon(i, "sm")} ${t}</button>`)}</div></div></div>
      <div class="card"><div class="card-head"><h3>الجلسات</h3></div><div class="card-body">
        <div class="switch-row"><div><b>تسجيل الخروج من هذا الجهاز</b></div><button class="btn ghost" id="lo" type="button">${icon("logout", "sm")} خروج</button></div><hr class="line">
        <div class="switch-row"><div><b>إنهاء كل الجلسات</b><p>يسجّل خروجك من كل الأجهزة والمتصفحات. استخدمه إن شككت بوجود وصول غير مصرّح.</p></div><button class="btn ghost danger" id="loa" type="button">إنهاء الكل</button></div></div></div>
      <div class="card"><div class="card-head"><h3>المساعدة</h3></div><div class="card-body row wrap"><a class="btn ghost" href="#/about">${icon("info", "sm")} عن المنصة</a>${me.admin_username ? html`<a class="btn ghost" href="https://t.me/${me.admin_username}" target="_blank" rel="noopener noreferrer">${icon("headset", "sm")} الدعم الفني</a>` : ""}</div></div></div>`);
  $$("#thm button").forEach(b => b.onclick = () => { applyTheme(b.dataset.t); $$("#thm button").forEach(x => x.setAttribute("aria-pressed", String(x === b))); });
  const out = async (all) => {
    try { const r = await post(all ? "/logout-all" : "/logout"); if (r && r.csrf) state.csrf = r.csrf; state.me = null; state.bearer = ""; go("#/welcome"); location.reload(); } catch (e) { fail(e); }
  };
  $("#lo").onclick = () => out(false);
  $("#loa").onclick = async () => { if (await confirmBox({ title: "إنهاء كل الجلسات", text: "ستحتاج لتسجيل الدخول من جديد على جميع أجهزتك.", label: "إنهاء الكل", danger: true })) out(true); };
}
