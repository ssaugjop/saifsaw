/* Saw Host — boot: يعمل قبل بقية الواجهة (core.js يستورده أولاً).
   1) الخطوط: تُحمَّل بعد الرسم الأول وبلا حجب. إن كان موقع الخطوط محجوباً أو بطيئاً (شائع في بعض الشبكات)
      تبقى الصفحة تعمل بخطوط النظام ولا تنتظر شيئاً.
   2) تيليجرام: نحمّل السكربت الرسمي بمهلة قصيرة. إن لم يصل (telegram.org محجوب/بطيء) نبني بديلاً خفيفاً
      من معاملات الإطلاق (#tgWebAppData=…) ومن قناة الأحداث الأصلية، فيبقى الدخول التلقائي والأزرار تعمل. */

const FONT_CSS = "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap";

function lazyFonts() {
  const add = () => {
    const l = document.createElement("link");
    l.rel = "stylesheet"; l.href = FONT_CSS; l.media = "print";   // media=print: لا يحجب الرسم أبداً
    l.onload = () => { l.media = "all"; };
    l.onerror = () => l.remove();
    document.head.appendChild(l);
  };
  if (document.readyState === "complete") setTimeout(add, 300);
  else addEventListener("load", () => setTimeout(add, 300), { once: true });
}

function loadScript(src, ms) {
  return new Promise(res => {
    const s = document.createElement("script");
    let done = false;
    const fin = v => { if (done) return; done = true; clearTimeout(t); res(v); };
    const t = setTimeout(() => fin(false), ms);
    s.src = src; s.async = true; s.onload = () => fin(true); s.onerror = () => fin(false);
    document.head.appendChild(s);
  });
}

const KEY = "__telegram__initParams";
function parseHash(raw) {
  const out = {};
  for (const part of String(raw || "").replace(/^#/, "").split("&")) {
    if (!part) continue;
    const i = part.indexOf("=");
    const k = i < 0 ? part : part.slice(0, i), v = i < 0 ? "" : part.slice(i + 1);
    try { out[decodeURIComponent(k)] = decodeURIComponent(v); } catch { /* skip */ }
  }
  return out;
}
function launchParams() {
  let p = {};
  if (/tgWebApp(Data|Version|Platform)=/.test(location.hash)) {
    p = parseHash(location.hash);
    try { sessionStorage.setItem(KEY, JSON.stringify(p)); } catch { /* private mode */ }
  } else {
    try { p = JSON.parse(sessionStorage.getItem(KEY) || "{}") || {}; } catch { p = {}; }
  }
  return p;
}

const cmpVer = (a, b) => {
  const x = String(a).split(".").map(Number), y = String(b).split(".").map(Number);
  for (let i = 0; i < Math.max(x.length, y.length); i++) { const d = (x[i] || 0) - (y[i] || 0); if (d) return d > 0 ? 1 : -1; }
  return 0;
};

function installShim(params) {
  const initData = params.tgWebAppData || "";
  if (!initData) return false;
  let themeParams = {};
  try { themeParams = JSON.parse(params.tgWebAppThemeParams || "{}"); } catch { /* ignore */ }
  const version = params.tgWebAppVersion || "6.0";
  const handlers = {};
  const emit = (t, d) => (handlers[t] || []).slice().forEach(f => { try { f(d); } catch { /* ignore */ } });

  const post = (type, data) => {
    const body = data === undefined ? "" : JSON.stringify(data);
    try {
      if (window.TelegramWebviewProxy && window.TelegramWebviewProxy.postEvent) window.TelegramWebviewProxy.postEvent(type, body);
      else if (window.parent && window.parent !== window) window.parent.postMessage(JSON.stringify({ eventType: type, eventData: data }), "*");
      else if (window.external && window.external.notify) window.external.notify(JSON.stringify({ eventType: type, eventData: data }));
    } catch { /* ignore */ }
  };
  const need = v => cmpVer(version, v) >= 0;
  let backVisible = false, backCb = null;

  const app = {
    initData,
    initDataUnsafe: (() => { const o = {}; try { for (const [k, v] of new URLSearchParams(initData)) { try { o[k] = JSON.parse(v); } catch { o[k] = v; } } } catch { /* ignore */ } return o; })(),
    version, platform: params.tgWebAppPlatform || "unknown", themeParams,
    colorScheme: (themeParams.bg_color && parseInt(themeParams.bg_color.slice(1, 3), 16) < 128) ? "dark" : "light",
    isExpanded: true, viewportHeight: innerHeight, viewportStableHeight: innerHeight, isShim: true,
    isVersionAtLeast: need,
    ready() { post("web_app_ready"); },
    expand() { post("web_app_expand"); },
    close() { post("web_app_close"); },
    openLink(url, opts) { post("web_app_open_link", { url, ...(opts || {}) }); },
    setHeaderColor(c) { if (need("6.1")) post("web_app_set_header_color", { color: c }); },
    setBackgroundColor(c) { if (need("6.1")) post("web_app_set_background_color", { color: c }); },
    setBottomBarColor(c) { if (need("7.10")) post("web_app_set_bottom_bar_color", { color: c }); },
    enableClosingConfirmation() { post("web_app_setup_closing_behavior", { need_confirmation: true }); },
    disableClosingConfirmation() { post("web_app_setup_closing_behavior", { need_confirmation: false }); },
    disableVerticalSwipes() { if (need("7.7")) post("web_app_setup_swipe_behavior", { allow_vertical_swipe: false }); },
    enableVerticalSwipes() { if (need("7.7")) post("web_app_setup_swipe_behavior", { allow_vertical_swipe: true }); },
    onEvent(t, f) { (handlers[t] = handlers[t] || []).push(f); },
    offEvent(t, f) { handlers[t] = (handlers[t] || []).filter(x => x !== f); },
    BackButton: {
      get isVisible() { return backVisible; },
      show() { backVisible = true; if (need("6.1")) post("web_app_setup_back_button", { is_visible: true }); return this; },
      hide() { backVisible = false; if (need("6.1")) post("web_app_setup_back_button", { is_visible: false }); return this; },
      onClick(f) { backCb = f; return this; }, offClick() { backCb = null; return this; },
    },
    HapticFeedback: {
      impactOccurred(style) { if (need("6.1")) post("web_app_trigger_haptic_feedback", { type: "impact", impact_style: style || "light" }); },
      notificationOccurred(type) { if (need("6.1")) post("web_app_trigger_haptic_feedback", { type: "notification", notification_type: type || "success" }); },
      selectionChanged() { if (need("6.1")) post("web_app_trigger_haptic_feedback", { type: "selection_change" }); },
    },
  };

  const receive = (type, data) => {
    if (typeof data === "string") { try { data = JSON.parse(data); } catch { /* keep */ } }
    if (type === "viewport_changed" && data) {
      if (data.height) app.viewportHeight = data.height;
      app.viewportStableHeight = data.is_state_stable ? (data.height || app.viewportHeight) : app.viewportStableHeight;
      app.isExpanded = !!data.is_expanded;
    }
    if (type === "theme_changed" && data && data.theme_params) {
      app.themeParams = data.theme_params;
      app.colorScheme = (data.theme_params.bg_color && parseInt(data.theme_params.bg_color.slice(1, 3), 16) < 128) ? "dark" : "light";
    }
    emit(type, data);
  };
  app.onEvent("back_button_pressed", () => { if (backCb) backCb(); });
  window.Telegram = window.Telegram || {};
  window.Telegram.WebApp = app;
  window.Telegram.WebView = { receiveEvent: receive, postEvent: post };
  window.TelegramGameProxy_receiveEvent = receive;
  addEventListener("message", e => {
    if (e.source !== window.parent) return;
    try { const m = JSON.parse(e.data); if (m && m.eventType) receive(m.eventType, m.eventData); } catch { /* not ours */ }
  });
  post("iframe_ready", { reload_supported: true });
  post("web_app_request_theme");
  post("web_app_request_viewport");
  return true;
}

async function bootTelegram() {
  if (window.Telegram && window.Telegram.WebApp) return;
  const params = launchParams();
  const inside = !!params.tgWebAppData || !!window.TelegramWebviewProxy;
  if (!inside) return;                       // متصفح عادي: لا حاجة لأي شيء من تيليجرام
  const ok = await loadScript("https://telegram.org/js/telegram-web-app.js", 3000);
  if (ok && window.Telegram && window.Telegram.WebApp) return;
  installShim(params);
}

lazyFonts();
await bootTelegram();
