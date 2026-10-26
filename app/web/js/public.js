/* Saw Host — public pages: landing, login, about. */
import {
  $, $$, html, raw, setHTML, icon, state, api, post, go, toast, fail, nf, fmtIQD, fmtMB, toggleTheme, curTheme, initTheme, openExt, tg,
} from "./core.js";

let PUB = null;
async function pub() {
  if (PUB) return PUB;
  try { PUB = await api("/public", { quiet401: true }); } catch { PUB = { bot_username: "", admin_username: "", plans: [], about: "", trial: { days_per_referral: 1, max_days: 30 } }; }
  return PUB;
}
const botUrl = p => p.bot_username ? `https://t.me/${p.bot_username}` : "#/login";

/* ───────── Shared public chrome ───────── */
function pubNav(p, active) {
  const logged = !!state.me;
  return html`<header class="pub-nav"><div class="in">
    <a class="brand" href="#/welcome"><img src="/panel/static/img/mark-96.png" alt="" width="38" height="38"><div><b>Saw Host</b></div></a>
    <nav class="links" aria-label="الصفحات">
      <a href="#/welcome" data-jump="features">المزايا</a><a href="#/welcome" data-jump="plans">الخطط</a><a href="#/about" ${active === "about" ? raw('aria-current="page"') : ""}>عن المنصة</a><a href="#/welcome" data-jump="faq">الأسئلة الشائعة</a>
    </nav>
    <div class="end"><button class="iconbtn" data-theme-btn type="button" aria-label="تبديل المظهر"></button>
      ${logged ? html`<a class="btn sm" href="#/">لوحتي</a>` : html`<a class="btn ghost sm" href="#/login">دخول</a><a class="btn sm" href="${botUrl(p)}" target="_blank" rel="noopener noreferrer">ابدأ من تيليجرام</a>`}</div>
  </div></header>`;
}
function pubFoot(p) {
  return html`<footer class="pub-foot"><div class="in">
    <div><a class="brand" href="#/welcome"><img src="/panel/static/img/mark-96.png" alt="" width="42" height="42"><div><b>Saw Host</b><small>استضافة مواقع بسرعة وثقة</small></div></a>
      <p>منصة عراقية لاستضافة المواقع الثابتة وتطبيقات Python وNode.js وPHP، تُدار بالكامل من لوحة واحدة ومن داخل تيليجرام.</p></div>
    <div><h4>المنصة</h4><a href="#/welcome" data-jump="features">المزايا</a><a href="#/welcome" data-jump="plans">الخطط</a><a href="#/about">عن المنصة</a><a href="#/login">تسجيل الدخول</a></div>
    <div><h4>تواصل معنا</h4>${p.bot_username ? html`<a href="https://t.me/${p.bot_username}" target="_blank" rel="noopener noreferrer" class="ltr">@${p.bot_username}</a>` : ""}${p.admin_username ? html`<a href="https://t.me/${p.admin_username}" target="_blank" rel="noopener noreferrer">الدعم الفني</a>` : ""}</div>
    <div class="copy"><span>© ${new Date().getFullYear()} Saw Host</span><span>الأسعار بالدينار العراقي</span></div>
  </div></footer>`;
}
function bindChrome(root, ctx) {
  $$("[data-theme-btn]", root).forEach(b => { b.onclick = toggleTheme; setHTML(b, icon(curTheme() === "dark" ? "sun" : "moon")); });
  $$("[data-jump]", root).forEach(a => a.addEventListener("click", e => {
    const id = a.dataset.jump;
    if (location.hash === "#/welcome" || location.hash === "" || location.hash === "#/") { e.preventDefault(); const t = document.getElementById(id); if (t) t.scrollIntoView({ behavior: "smooth", block: "start" }); }
    else state.jump = id;
  }));
}

/* ───────── Landing ───────── */
const FAQ = [
  ["كيف أبدأ؟", "افتح البوت في تيليجرام ثم اضغط «افتح لوحة التحكم»، أو أرسل الأمر /login واكتب الرمز الذي يصلك هنا. لا حاجة لبريد أو كلمة مرور."],
  ["ما أنواع المواقع المدعومة؟", "المواقع الثابتة (HTML وCSS وJavaScript ومشاريع React وVue وVite بعد بنائها)، وتطبيقات Python (Flask وFastAPI وDjango)، وNode.js (Express وNext)، وPHP (بما فيها Laravel). يُكتشف نوع المشروع تلقائياً بعد الرفع، ويمكنك تحديد أمر التشغيل بملف Procfile."],
  ["كيف أدفع وأفعّل الاشتراك؟", "اختر الخطة من صفحة «اشتراكي» وأرسل الطلب. يصل الطلب للمشرف مباشرة في تيليجرام، وبعد تأكيد الدفع يُفعَّل اشتراكك فوراً ويصلك إشعار."],
  ["هل توجد تجربة مجانية؟", "نعم، عبر الدعوات: تحصل على يوم تجربة عن كل صديق ينضم من رابطك، حتى 30 يوماً، بصلاحيات أعلى خطة مدفوعة."],
  ["ماذا يحدث عند انتهاء الاشتراك؟", "تتوقف مواقعك تلقائياً وتبقى ملفاتك محفوظة. يمكنك تنزيلها أو حذفها، وعند التجديد تشغّل مواقعك من جديد بضغطة واحدة."],
  ["هل يمكنني ربط دومين خاص؟", "نعم، حسب خطتك وحين تكون الميزة مفعّلة من الإدارة. تضيف سجل TXT لإثبات الملكية ثم تربط الدومين بموقعك."],
  ["كيف يعمل الربط مع GitHub؟", "تربط المستودع والفرع من تبويب «النشر»، فيُنشر الموقع مع كل push تلقائياً. يدعم المستودعات الخاصة عبر رمز وصول، ويحتفظ بسجل لكل عملية نشر."],
];

export async function viewLanding(ctx) {
  const p = await pub();
  const plans = (p.plans || []).filter(x => x.price_monthly > 0);
  const hot = plans.length >= 3 ? plans[1].id : plans.length === 2 ? plans[1].id : "";
  setHTML(ctx.el, html`${pubNav(p, "home")}
  <main class="land">
    <section class="hero"><div class="in">
      <div class="hero-copy">
        <h1>ارفع موقعك.<br>يعمل خلال دقيقة.</h1>
        <p class="lead">استضافة عراقية للمواقع الثابتة وتطبيقات Python وNode.js وPHP. ملفات ومحرر وGitHub ودومينات ونسخ احتياطية، كلها في لوحة واحدة تفتحها من تيليجرام.</p>
        <div class="row wrap" style="gap:12px;margin-top:28px">
          <a class="btn lg" href="${botUrl(p)}" target="_blank" rel="noopener noreferrer">${icon("telegram", "fill")} ابدأ من تيليجرام</a>
          <a class="btn ghost lg" href="#/login">لدي حساب</a>
        </div>
        <ul class="hero-facts"><li>${icon("gift", "sm")} تجربة مجانية بدعوة الأصدقاء</li><li>${icon("card", "sm")} الدفع بالدينار العراقي</li><li>${icon("headset", "sm")} دعم مباشر عبر تيليجرام</li></ul>
      </div>
      <div class="hero-demo hero-brand">
        <img src="/panel/static/img/logo-512.png" alt="Saw Host — استضافة مواقع بسرعة وثقة" width="360" height="360" fetchpriority="high">
      </div>
    </div></section>

    <section class="band" id="features"><div class="in">
      <div class="showcase">
        <div class="sc-copy"><h2>محرر أكواد كامل داخل المتصفح</h2>
          <p>افتح أي ملف من موقعك وعدّله مباشرة: تلوين للشيفرة، علامات تبويب لعدة ملفات، بحث واستبدال، واختصارات مألوفة. احفظ بـ Ctrl+S وشاهد النتيجة في معاينة حيّة بجانب الكود.</p>
          <ul class="ticks"><li>${icon("check", "sm")} HTML وCSS وJavaScript وPython وNode.js وPHP</li><li>${icon("check", "sm")} معاينة حيّة بعرض الجوال واللوحي والحاسوب</li><li>${icon("check", "sm")} شجرة ملفات ورفع بالسحب والإفلات</li></ul></div>
        <div class="sc-vis"><dl class="kv flat shortcuts">
          <dt><kbd>Ctrl</kbd> + <kbd>S</kbd></dt><dd>حفظ الملف</dd>
          <dt><kbd>Ctrl</kbd> + <kbd>F</kbd></dt><dd>بحث واستبدال</dd>
          <dt><kbd>Ctrl</kbd> + <kbd>/</kbd></dt><dd>تعليق/إلغاء تعليق الأسطر</dd>
          <dt><kbd>Tab</kbd></dt><dd>إزاحة الكتلة المحددة</dd>
        </dl></div>
      </div>

      <div class="showcase flip">
        <div class="sc-copy"><h2>اربط GitHub وانشر مع كل push</h2>
          <p>اختر المستودع والفرع مرة واحدة، وبعدها يُحدَّث موقعك تلقائياً عند كل push. تدعم المستودعات الخاصة، ويُسجَّل لكل نشر رقم الـ commit ورسالته ونتيجته حتى تعرف ما الذي يعمل الآن.</p>
          <ul class="ticks"><li>${icon("check", "sm")} إنشاء الـ webhook تلقائياً بضغطة واحدة</li><li>${icon("check", "sm")} سجل نشر كامل مع أسباب الفشل</li><li>${icon("check", "sm")} تحقق من توقيع كل طلب قادم من GitHub</li></ul></div>
        <div class="sc-vis"><ol class="flow">
          <li><span class="mk">${icon("github")}</span><div><b>git push</b><span>إلى الفرع المربوط</span></div></li>
          <li><span class="mk">${icon("link")}</span><div><b>Webhook موقّع</b><span>يُتحقق من التوقيع</span></div></li>
          <li><span class="mk">${icon("download")}</span><div><b>سحب الملفات ونشرها</b><span>مع سجل لكل عملية</span></div></li>
        </ol></div>
      </div>

      <div class="minis">
        ${[["globe", "دومينات خاصة", "اربط دومينك بموقعك أو اختر من دومينات المنصة."],
      ["archive", "نسخ احتياطية", "نسخة بضغطة واحدة، وتنزيل واستعادة متى شئت."],
      ["terminal", "سجلات التشغيل", "تابع مخرجات تطبيقك وأخطاءه مباشرة من اللوحة."],
      ["telegram", "لوحة داخل تيليجرام", "تُفتح كتطبيق مصغّر بدخول تلقائي بدون رمز."],
      ["shieldCheck", "عزل وحدود موارد", "كل تطبيق بعملية مستقلة وحدود للذاكرة والمعالج."],
      ["gift", "تجربة بالدعوات", "يوم مجاني عن كل صديق ينضم برابطك."]]
      .map(([i, t, d]) => html`<div class="mini"><span class="ico">${icon(i)}</span><div><b>${t}</b><p>${d}</p></div></div>`)}
      </div>
    </div></section>

    <section class="plans-sec" id="plans"><div class="in">
      <div class="sec-title"><h2>خطط واضحة بالدينار العراقي</h2><p>اشتراك شهري بدون عقود. تقدر ترقّي أو تجدّد في أي وقت.</p></div>
      <div class="plans">${plans.length ? plans.map(pl => planCard(pl, pl.id === hot, p)) : html`<p class="mute">ستظهر الخطط هنا قريباً.</p>`}</div>
    </div></section>

    <section class="trust"><div class="in">
      <h2>مبنية بحيث لا يؤثر موقع على آخر</h2>
      <div class="trust-grid">
        ${[["layers", "عملية مستقلة لكل تطبيق", "حدود للذاكرة والمعالج وعدد العمليات، وإيقاف تلقائي للتطبيقات الخاملة."],
      ["shield", "لوحة معزولة عن المواقع", "كود أي موقع مستضاف لا يستطيع تنفيذ إجراءات باسمك داخل لوحة التحكم."],
      ["lock", "حماية الملفات", "منع الخروج من مجلد الموقع، وفحص ملفات ZIP قبل فكّها، وتجاهل الروابط الرمزية."],
      ["key", "جلسات موقّعة", "رموز دخول لمرة واحدة، وتحديد محاولات الدخول، وإمكانية إنهاء كل الجلسات."]]
      .map(([i, t, d]) => html`<div><span class="ico">${icon(i)}</span><b>${t}</b><p>${d}</p></div>`)}
      </div>
    </div></section>

    <section class="faq" id="faq"><div class="in narrow">
      <div class="sec-title"><h2>أسئلة شائعة</h2></div>
      ${FAQ.map(([q, a]) => html`<details><summary>${q}${icon("chevD", "sm")}</summary><p>${a}</p></details>`)}
    </div></section>

    <section class="cta-band"><div class="in">
      <img src="/panel/static/img/mark-192.png" alt="" width="84" height="84">
      <div><h2>جاهز لنشر موقعك؟</h2><p>افتح البوت وابدأ في أقل من دقيقة.</p></div>
      <a class="btn lg" href="${botUrl(p)}" target="_blank" rel="noopener noreferrer">${icon("telegram", "fill")} افتح البوت</a>
    </div></section>
  </main>
  ${pubFoot(p)}`);
  bindChrome(ctx.el, ctx);
  if (state.jump) { const t = document.getElementById(state.jump); state.jump = null; if (t) setTimeout(() => t.scrollIntoView({ block: "start" }), 50); }
}

function planCard(pl, hot, p) {
  const feats = Array.isArray(pl.features) ? pl.features : (pl.features ? String(pl.features).split("\n").map(x => x.trim()).filter(Boolean) : []);
  return html`<article class="plan ${hot ? "hot" : ""}">
    ${hot ? html`<span class="tag gold ribbon">الأكثر طلباً</span>` : ""}
    <h3>${pl.icon} ${pl.name}</h3>
    <div class="price"><b class="num">${nf.format(pl.price_monthly)}</b><span>د.ع / شهرياً</span></div>
    <ul class="plim"><li><b>${pl.max_sites}</b> ${pl.max_sites === 1 ? "موقع" : "مواقع"}</li><li><b>${fmtMB(pl.max_mb)}</b> مساحة</li><li><b>${pl.max_backups}</b> نسخ احتياطية لكل موقع</li>
      ${pl.max_custom_domains ? html`<li><b>${pl.max_custom_domains}</b> دومين مخصص</li>` : ""}${pl.max_platform_domains ? html`<li><b>${pl.max_platform_domains}</b> دومين من المنصة</li>` : ""}</ul>
    ${feats.length ? html`<ul class="pfeat">${feats.map(f => html`<li>${icon("check", "sm")} ${f}</li>`)}</ul>` : ""}
    <a class="btn ${hot ? "" : "ghost"} block" href="${state.me ? "#/plan" : botUrl(p)}" ${state.me ? "" : raw('target="_blank" rel="noopener noreferrer"')}>اشترك بهذه الخطة</a>
  </article>`;
}

/* ───────── Login ───────── */
export async function viewLogin(ctx, q) {
  const p = await pub();
  let mode = "tg";
  try { if (!q.get("code") && p.admin_password_login) mode = localStorage.getItem("saw-login-mode") || "tg"; } catch { /* ignore */ }
  setHTML(ctx.el, html`<div class="login">
    <section class="login-side">
      <a class="brand" href="#/welcome"><img src="/panel/static/img/mark-96.png" alt="" width="46" height="46"><div><b>Saw Host</b><small>استضافة مواقع بسرعة وثقة</small></div></a>
      <div class="login-pitch"><h1>أهلاً بعودتك.</h1><p>أدر مواقعك وملفاتك ونشر GitHub ودوميناتك من مكان واحد.</p></div>
      <ul class="login-points"><li>${icon("telegram", "fill")} الدخول عبر حسابك في تيليجرام، بلا كلمة مرور</li><li>${icon("shieldCheck")} رمز لمرة واحدة صالح 10 دقائق</li><li>${icon("panel")} أو افتح اللوحة مباشرة من زر البوت</li></ul>
    </section>
    <section class="login-main"><div class="login-box">
      <div class="row" style="margin-bottom:22px"><h2>تسجيل الدخول</h2><span class="sp"></span><button class="iconbtn" data-theme-btn type="button" aria-label="تبديل المظهر"></button></div>
      ${p.admin_password_login ? html`<div class="seg block" role="group" aria-label="طريقة الدخول"><button type="button" data-m="tg">تيليجرام</button><button type="button" data-m="admin">الأدمن</button></div>` : ""}
      <div id="lfbody" style="margin-top:22px"></div>
      <a class="back" href="#/welcome" style="margin:22px 0 0">${icon("arrowR", "sm")} العودة للصفحة الرئيسية</a>
    </div></section></div>`);
  bindChrome(ctx.el, ctx);
  const done = r => { if (r && r.csrf) state.csrf = r.csrf; state.me = null; go("#/"); };
  const draw = () => {
    try { localStorage.setItem("saw-login-mode", mode); } catch { /* ignore */ }
    $$(".seg button", ctx.el).forEach(b => b.setAttribute("aria-pressed", String(b.dataset.m === mode)));
    const box = $("#lfbody");
    if (mode === "admin") {
      setHTML(box, html`<div class="field"><label for="au">اسم المستخدم</label><input id="au" class="input ltr" autocomplete="username" autocapitalize="off" spellcheck="false"></div>
        <div class="field"><label for="ap">كلمة المرور</label><div class="input-group"><input id="ap" class="input ltr" type="password" autocomplete="current-password"><button type="button" class="iconbtn bordered" id="pw" aria-label="إظهار كلمة المرور" style="height:42px;width:42px">${icon("eye")}</button></div></div>
        <button class="btn lg block" id="adm" type="button">دخول الأدمن</button>`);
      const run = async () => {
        const b = $("#adm"); b.classList.add("is-busy");
        try { done(await post("/login/admin", { username: $("#au").value, password: $("#ap").value })); } catch (e) { fail(e); } finally { b.classList.remove("is-busy"); }
      };
      $("#adm").onclick = run;
      $("#pw").onclick = () => { const i = $("#ap"), s = i.type === "password"; i.type = s ? "text" : "password"; setHTML($("#pw"), icon(s ? "eyeoff" : "eye")); };
      [$("#au"), $("#ap")].forEach(i => i.addEventListener("keydown", e => { if (e.key === "Enter") run(); }));
      $("#au").focus();
      return;
    }
    setHTML(box, html`<ol class="steps" style="margin-bottom:20px">
        <li class="active"><span class="mark">1</span><b>${p.bot_username ? html`افتح البوت <a class="ltr" href="https://t.me/${p.bot_username}" target="_blank" rel="noopener noreferrer">@${p.bot_username}</a>` : "افتح بوت المنصة في تيليجرام"}</b></li>
        <li class="active"><span class="mark">2</span><b>أرسل الأمر <code>/login</code> أو اضغط «افتح لوحة التحكم»</b></li>
        <li class="active"><span class="mark">3</span><b>اكتب الرمز هنا</b></li></ol>
      <div class="field"><label for="code">رمز الدخول</label><input id="code" class="input codebox" maxlength="9" placeholder="XXXX-XXXX" autocomplete="one-time-code" autocapitalize="characters" spellcheck="false"></div>
      <button class="btn lg block" id="enter" type="button">دخول</button>`);
    const code = $("#code");
    code.addEventListener("input", () => { const v = code.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 8); code.value = v.length > 4 ? v.slice(0, 4) + "-" + v.slice(4) : v; });
    const submit = async () => {
      const b = $("#enter"); b.classList.add("is-busy");
      try { done(await post("/login", { code: code.value })); } catch (e) { fail(e); } finally { b.classList.remove("is-busy"); }
    };
    $("#enter").onclick = submit;
    code.addEventListener("keydown", e => { if (e.key === "Enter") submit(); });
    const pre = q.get("code");
    if (pre) { code.value = pre.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 8).replace(/^(.{4})(.+)$/, "$1-$2"); submit(); } else code.focus();
  };
  $$(".seg button", ctx.el).forEach(b => b.onclick = () => { mode = b.dataset.m; draw(); });
  draw();
}

/* ───────── About ───────── */
export async function viewAbout(ctx) {
  const p = await pub();
  const logged = !!state.me;
  const adminText = (p.about || "").split(/\n{2,}/).map(s => s.trim()).filter(Boolean);
  const content = html`<div class="about">
    <header class="about-hero"><div class="in">
      <img src="/panel/static/img/mark-192.png" alt="" width="96" height="96">
      <div><h1>عن Saw Host</h1><p class="lead">منصة عراقية تجعل نشر المواقع وإدارتها بسيطة: ترفع الملفات أو تربط GitHub، وتتحكم بكل شيء من لوحة واحدة، وتبقى على تواصل مباشر مع فريق الدعم في تيليجرام.</p></div>
    </div></header>
    <div class="in about-body">
      ${adminText.length ? html`<section class="a-sec"><div class="a-head"><h2>كلمة الإدارة</h2></div><div class="a-note">${adminText.map(t => html`<p>${t}</p>`)}</div></section>` : ""}

      <section class="a-sec"><div class="a-head"><h2>ماذا تقدّم المنصة</h2><p>كل ما يحتاجه موقع صغير أو متوسط ليعمل باستقرار، بدون تعقيد الخوادم.</p></div>
        <div class="a-grid">
          ${[["upload", "نشر فوري", "ارفع ملف ZIP أو ابدأ بصفحة فارغة، ويُنشأ لموقعك رابط مباشر فوراً. يُكتشف نوع المشروع تلقائياً: موقع ثابت، أو تطبيق Python أو Node.js أو PHP، وتُثبَّت حزمه تلقائياً."],
      ["code", "محرر وإدارة ملفات", "محرر أكواد بعلامات تبويب ومعاينة حيّة، وشجرة ملفات، ورفع بالسحب والإفلات، وتنزيل أي ملف أو مجلد."],
      ["github", "تكامل GitHub", "ربط مستودع وفرع، ونشر تلقائي عند كل push عبر webhook موقّع، ودعم المستودعات الخاصة، وسجل كامل لعمليات النشر."],
      ["globe", "دومينات", "دومينات من المنصة أو دومينك الخاص مع تحقق عبر سجل TXT، وتحديد الدومين الأساسي لكل موقع."],
      ["archive", "نسخ احتياطية", "أنشئ نسخة قبل أي تعديل كبير، ونزّلها أو استعدها بضغطة واحدة."],
      ["terminal", "سجلات التشغيل", "للتطبيقات الديناميكية: متابعة مخرجات التطبيق وأخطائه من اللوحة نفسها لمعرفة سبب أي عطل."]]
      .map(([i, t, d]) => html`<article class="a-card"><span class="ico">${icon(i)}</span><h3>${t}</h3><p>${d}</p></article>`)}
        </div></section>

      <section class="a-sec"><div class="a-head"><h2>كيف تعمل</h2></div>
        <ol class="a-steps">
          <li><span class="mk">1</span><div><h3>تسجّل عبر تيليجرام</h3><p>تفتح البوت وتضغط زر لوحة التحكم. يتم التعرف عليك تلقائياً، ولا توجد كلمات مرور تحفظها.</p></div></li>
          <li><span class="mk">2</span><div><h3>تختار خطة أو تبدأ بتجربة</h3><p>ترسل طلب الاشتراك من اللوحة، فيصل للمشرف مباشرة مع أزرار القبول والرفض، ويُفعَّل اشتراكك فور تأكيد الدفع. أو تدعو أصدقاءك للحصول على أيام تجربة.</p></div></li>
          <li><span class="mk">3</span><div><h3>تنشر موقعك</h3><p>ترفع الملفات أو تربط GitHub. يعمل الموقع على رابط خاص بك تحت ${p.platform_host ? html`<code>${p.platform_host}</code>` : "نطاق المنصة"}، ويمكنك ربط دومين لاحقاً.</p></div></li>
          <li><span class="mk">4</span><div><h3>تديره بثقة</h3><p>تعدّل وتنسخ وتستعيد وتراقب السجلات، وتصلك التنبيهات المهمة على تيليجرام.</p></div></li>
        </ol></section>

      <section class="a-sec"><div class="a-head"><h2>الأمان والخصوصية</h2><p>الأمان جزء من التصميم وليس إضافة لاحقة.</p></div>
        <div class="a-list">
          ${[["لوحة معزولة عن المواقع المستضافة", "المواقع المستضافة تُخدَم من نفس نطاق المنصة، لذلك لا تقبل لوحة التحكم أي طلب بدون رمز تحقق لا يمكن لكود موقع الوصول إليه. حتى لو فتحت موقعاً خبيثاً وأنت مسجّل دخولك، لا يستطيع تنفيذ إجراء باسمك."],
      ["تشغيل مقيَّد", "كل تطبيق يعمل كعملية مستقلة بمستخدم نظام خاص وحدود للذاكرة (1GB) والعمليات، وبمتغيرات بيئة نظيفة لا تحمل أي أسرار من المنصة، وتُمنع عنه الشبكات الداخلية. ويُعاد تشغيله تلقائياً إن انهار، ويُوقَف بعد 6 ساعات من الخمول."],
      ["حماية الملفات", "كل مسار يُتحقق منه ويُحصر داخل مجلد موقعك. تُفحص ملفات ZIP وأرشيفات GitHub قبل فكّها وتُتجاهل الروابط الرمزية والمسارات الخطرة، وتُمنع الامتدادات التنفيذية الخطرة."],
      ["دخول آمن", "رموز دخول عشوائية لمرة واحدة، وجلسات موقّعة بـ HMAC داخل كوكي HttpOnly، وتحديد لمحاولات الدخول، وإمكانية إنهاء الجلسات على كل الأجهزة. التحقق من تطبيق تيليجرام المصغّر يتم بتوقيع رسمي."],
      ["webhooks موقّعة", "طلبات GitHub تُقبل فقط بتوقيع HMAC-SHA256 صحيح ومقارنة ثابتة الزمن، وتُحفظ رموز الوصول للمستودعات الخاصة مشفّرة."]]
      .map(([t, d]) => html`<div><span class="ico">${icon("shieldCheck")}</span><div><h3>${t}</h3><p>${d}</p></div></div>`)}
        </div></section>

      <section class="a-sec"><div class="a-head"><h2>حدود الاستخدام</h2><p>لضمان استقرار الخدمة للجميع.</p></div>
        <div class="card"><dl class="kv" style="padding:4px 22px">
          <dt>أنواع المشاريع</dt><dd>مواقع ثابتة، Python، Node.js، PHP</dd>
          <dt>حجم الرفع الواحد</dt><dd>حتى 150MB لكل عملية رفع</dd>
          <dt>عدد الملفات في ZIP</dt><dd>حتى 10,000 ملف</dd>
          <dt>التطبيقات الديناميكية</dt><dd>ذاكرة 1GB · إعادة تشغيل تلقائية · إيقاف بعد 6 ساعات خمول</dd>
          <dt>الاشتراك المنتهي</dt><dd>تتوقف المواقع تلقائياً وتبقى الملفات متاحة للتنزيل والحذف</dd>
        </dl></div></section>

      <section class="a-sec"><div class="a-head"><h2>هل تحتاج مساعدة؟</h2></div>
        <div class="a-contact">
          <div><b>الدعم الفني</b><p>راسلنا على تيليجرام وسنرد بأسرع وقت.</p></div>
          <div class="row wrap">${p.admin_username ? html`<a class="btn" href="https://t.me/${p.admin_username}" target="_blank" rel="noopener noreferrer">${icon("headset", "sm")} تواصل مع الدعم</a>` : ""}${p.bot_username ? html`<a class="btn ghost" href="https://t.me/${p.bot_username}" target="_blank" rel="noopener noreferrer">${icon("telegram", "sm", "fill")} افتح البوت</a>` : ""}</div>
        </div></section>
    </div></div>`;
  if (logged) { setHTML(ctx.el, content); }
  else { setHTML(ctx.el, html`${pubNav(p, "about")}${content}${pubFoot(p)}`); bindChrome(ctx.el, ctx); }
}
