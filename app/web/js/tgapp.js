/* Saw Host — تكامل تيليجرام: توسيع النافذة، منع الإغلاق بالسحب، ألوان النظام، زر الرجوع الأصلي،
   تأكيد الإغلاق عند وجود تعديلات غير محفوظة، اهتزاز لمسي، والمناطق الآمنة. كل شيء محمي بـ try/catch للعملاء القديمة. */
import { tg } from "./core.js";

const root = document.documentElement;
const safe = fn => { try { return fn(); } catch { /* عميل تيليجرام قديم */ } };

function setInsets() {
  if (!tg) return;
  const a = tg.safeAreaInset || {}, c = tg.contentSafeAreaInset || {};
  const s = (k, v) => root.style.setProperty(k, (v || 0) + "px");
  s("--tg-safe-top", a.top); s("--tg-safe-bottom", a.bottom); s("--tg-safe-left", a.left); s("--tg-safe-right", a.right);
  s("--tg-content-top", c.top); s("--tg-content-bottom", c.bottom);
}
function setViewport() {
  if (!tg) return;
  const h = tg.viewportStableHeight || tg.viewportHeight;
  if (h) root.style.setProperty("--tg-vh", h + "px");
}

let backBound = false, backVisible = false;
export function syncBack(path) {
  if (!tg || !tg.BackButton) return;
  const want = typeof path === "string" && path.startsWith("/") && path !== "/" && path !== "/welcome" && path !== "/login";
  if (!backBound) {
    backBound = true;
    safe(() => tg.BackButton.onClick(() => {
      if (history.length > 1) history.back(); else location.hash = "#/";
    }));
  }
  if (want !== backVisible) { backVisible = want; safe(() => (want ? tg.BackButton.show() : tg.BackButton.hide())); }
}

export function initTelegram() {
  if (!tg) return;
  root.classList.add("in-tg");
  safe(() => { tg.ready(); tg.expand(); });
  safe(() => { if (tg.disableVerticalSwipes) tg.disableVerticalSwipes(); });   // لا يُغلق التطبيق عند سحب المحرر/القوائم للأسفل
  setViewport(); setInsets();
  safe(() => tg.onEvent("viewportChanged", () => { setViewport(); setInsets(); }));
  safe(() => tg.onEvent("safeAreaChanged", setInsets));
  safe(() => tg.onEvent("contentSafeAreaChanged", setInsets));

  // اهتزاز خفيف عند لمس الأزرار والتبويبات (يخفّف الإحساس بأن الواجهة "ميتة")
  document.addEventListener("pointerdown", e => {
    const t = e.target.closest && e.target.closest("button:not([disabled]), .btn, .tabbar a, .tabs a, .fab");
    if (t && tg.HapticFeedback) safe(() => tg.HapticFeedback.impactOccurred("light"));
  }, { passive: true });

  // تأكيد الإغلاق إذا كان في المحرر ملف غير محفوظ (site.js يعرض window.__sawDirty)
  let confirming = false;
  setInterval(() => {
    const d = !!(window.__sawDirty && window.__sawDirty());
    if (d !== confirming) { confirming = d; safe(() => (d ? tg.enableClosingConfirmation() : tg.disableClosingConfirmation())); }
  }, 1500);
}
